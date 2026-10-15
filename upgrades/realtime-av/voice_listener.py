"""Bounded, hands-free PyAudio -> Silero ONNX -> Faster-Whisper pipeline.

256 ms silence endpointing is a detection target, not a transcription SLA.
Use a headset or acoustic echo cancellation for reliable full-duplex barge-in.
Imports of optional hardware/model packages are delayed until startup.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import json
import logging
import math
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler

LOG = logging.getLogger('zoya.listener')
RATE, FRAME = 16000, 512  # Silero: 32ms frames, recurrent state per microphone stream.


@dataclass(frozen=True)
class ListenerConfig:
    model_path: str
    vad_model_path: str
    language: str | None = None  # base.en: en only; use multilingual base/small for Urdu/Hindi.
    device_index: int | None = None
    silence_ms: int = 256
    pre_roll_ms: int = 192
    min_speech_ms: int = 128
    start_threshold: float = .65
    end_threshold: float = .35
    start_frames: int = 2
    max_utterance_seconds: int = 25
    cpu_threads: int = 2
    max_queue_age_seconds: float = 5

    def validate(self):
        if not 64 <= self.silence_ms <= 288 or not 0 <= self.pre_roll_ms <= 512:
            raise ValueError('Use 64-288ms endpoint silence and 0-512ms pre-roll')
        if not 0 < self.end_threshold < self.start_threshold < 1:
            raise ValueError('VAD thresholds must satisfy 0 < end < start < 1')
        if not 1 <= self.start_frames <= 5 or not 64 <= self.min_speech_ms <= 1000:
            raise ValueError('Invalid VAD confirmation/minimum-speech duration')
        if not 2 <= self.max_utterance_seconds <= 60 or not 1 <= self.cpu_threads <= 16:
            raise ValueError('Invalid utterance/CPU bound')
        if not 0 < self.max_queue_age_seconds <= 30:
            raise ValueError('Invalid queue age bound')


class SileroOnnx:
    """CPU Silero standard or Faster-Whisper v6 sequence graph; no PyTorch."""
    def __init__(self, path: str):
        import numpy as np
        import onnxruntime as ort
        ort.disable_telemetry_events()
        if not Path(path).is_file():
            raise ValueError('Silero ONNX file missing; install it before listening')
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(path, sess_options=opts, providers=['CPUExecutionProvider'])
        inputs = {x.name for x in self.session.get_inputs()}
        if inputs not in ({'input', 'state', 'sr'}, {'input', 'h', 'c'}):
            raise ValueError('Unsupported Silero graph input signature')
        self.sequence_graph = inputs == {'input', 'h', 'c'}
        self.np = np
        self.reset()

    def reset(self):
        self.state = self.np.zeros((2, 1, 128), dtype=self.np.float32)
        self.h = self.np.zeros((1, 1, 128), dtype=self.np.float32)
        self.c = self.np.zeros((1, 1, 128), dtype=self.np.float32)
        self.context = self.np.zeros((1, 64), dtype=self.np.float32)

    def probability(self, samples) -> float:
        np = self.np
        x = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        if x.shape != (1, FRAME):
            raise ValueError('Silero needs exactly 512 samples at 16000Hz')
        x = np.concatenate((self.context, x), axis=1)
        if self.sequence_graph:
            out, self.h, self.c = self.session.run(None, {'input': x, 'h': self.h, 'c': self.c})
        else:
            out, self.state = self.session.run(None, {'input': x, 'state': self.state,
                                                      'sr': np.array(RATE, dtype=np.int64)})
        self.context = x[:, -64:].copy()
        return float(out.reshape(-1)[0])


class SpeechSegmenter:
    """Pure frame-state machine; expensive STT never runs in this thread.

    Returns (speech_started, completed_frames, forced_split). Completed frames
    include pre-roll and a short tail. Queue saturation is reported, never hidden.
    """
    def __init__(self, cfg: ListenerConfig):
        cfg.validate()
        self.cfg = cfg
        self.reset()

    def reset(self):
        self.pre = collections.deque(maxlen=max(1, math.ceil(self.cfg.pre_roll_ms/32)))
        self.active, self.frames = False, []
        self.high, self.voiced, self.silence = 0, 0, 0
        self.reject_until_silence = False

    def feed(self, frame, probability: float):
        started, finished, forced = False, None, False
        if self.reject_until_silence:
            self.silence = self.silence+1 if probability < self.cfg.end_threshold else 0
            if self.silence*32 >= self.cfg.silence_ms:
                self.reset()
            return False, None, False
        if not self.active:
            self.pre.append(frame)
            self.high = self.high+1 if probability >= self.cfg.start_threshold else 0
            if self.high >= self.cfg.start_frames:
                self.active = started = True
                self.frames = list(self.pre)
                self.voiced = self.high
                self.silence = 0
                self.pre.clear()
            return started, finished, forced
        self.frames.append(frame)
        if probability >= self.cfg.end_threshold:
            self.voiced += 1
            self.silence = 0
        else:
            self.silence += 1
        forced = len(self.frames)*FRAME >= self.cfg.max_utterance_seconds*RATE
        if self.silence*32 >= self.cfg.silence_ms or forced:
            if not forced and self.voiced*32 >= self.cfg.min_speech_ms:
                # Retain 64ms of tail, remove the rest of the endpointing silence.
                trim = max(0, self.silence-2)
                finished = self.frames[:-trim] if trim else self.frames[:]
            self.reset()
            self.reject_until_silence = forced
        return started, finished, forced


class LocalAgentClient:
    """Authenticated localhost client. Never retries command submission.

    GET / is used only to obtain the existing Zoya CSRF token. POST dispatch is
    deliberately single-attempt: a timeout does not prove execution failed.
    """
    def __init__(self, base_url='http://127.0.0.1:8767', input_path='/api/conversation',
                 interrupt_path: str | None = None, *, voice_replies=False, timeout=10):
        u = urlsplit(base_url)
        if u.scheme != 'http' or u.hostname not in ('localhost', '127.0.0.1') or u.username or u.password or u.path not in ('', '/'):
            raise ValueError('Agent endpoint must be plain HTTP on localhost/127.0.0.1')
        if u.query or u.fragment:
            raise ValueError('Endpoint URL cannot include query/fragment')
        for path in (input_path, interrupt_path):
            if path is not None and (not path.startswith('/api/') or '?' in path or '#' in path):
                raise ValueError('Use an /api/ path')
        self.base, self.input_path = base_url.rstrip('/'), input_path
        self.interrupt_path, self.voice_replies = interrupt_path, voice_replies
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))  # Local credentials never follow proxy settings.
        self.token = None

    def post(self, path: str, body: dict):
        if self.token is None:
            with self.opener.open(self.base+'/', timeout=self.timeout) as response:
                html = response.read(2_000_000).decode('utf-8')
            m = re.search(r"const\s+token\s*=\s*['\"]([^'\"]+)['\"]", html)
            if not m:
                raise RuntimeError('Local Zoya session token unavailable')
            self.token = m[1]
        req = Request(self.base+path, data=json.dumps(body).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'Origin': self.base,
                               'X-Zoya-Token': self.token}, method='POST')
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return json.loads(response.read(2_000_000))
        except Exception as exc:
            if getattr(exc, 'code', None) == 403:
                self.token = None  # Refresh on next distinct request, not replay this command.
            raise

    def dispatch(self, text: str, request_id: str):
        return self.post(self.input_path, {'message': text, 'request_id': request_id,
                                          'voice': self.voice_replies})

    def interrupt(self):
        if not self.interrupt_path:
            return
        self.post(self.interrupt_path, {'request_id': 'barge-'+str(uuid.uuid4())})


def input_devices(pa) -> list[dict]:
    devices = []
    try:
        default = int(pa.get_default_input_device_info()['index'])
    except (OSError, IOError):
        default = -1
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get('maxInputChannels', 0) > 0:
            devices.append({'index': i, 'name': d['name'], 'default': i == default,
                            'native_rate': int(d['defaultSampleRate'])})
    return devices


def choose_device(pa, requested: int | None):
    import pyaudio
    devices = input_devices(pa)
    if requested is not None:
        devices = [d for d in devices if d['index'] == requested]
    else:
        devices = [d for d in devices if not any(w in d['name'].lower() for w in
                   ('loopback', 'stereo mix', 'what u hear', 'monitor of'))]
        devices.sort(key=lambda d: (not d['default'], 'microphone' not in d['name'].lower()))
    for d in devices:
        for rate in dict.fromkeys((16000, d['native_rate'], 48000, 44100)):
            try:
                if pa.is_format_supported(rate, input_device=d['index'], input_channels=1,
                                          input_format=pyaudio.paInt16):
                    return d['index'], rate
            except (ValueError, OSError):
                continue
    raise RuntimeError('No usable mono microphone; list devices and select an input index')


class VoiceListener:
    def __init__(self, config: ListenerConfig, on_text: Callable[[str, str], None],
                 on_speech_start: Callable[[], None], *, vad_factory=SileroOnnx,
                 model_factory=None):
        config.validate()
        self.cfg, self.on_text, self.on_speech_start = config, on_text, on_speech_start
        self.vad_factory, self.model_factory = vad_factory, model_factory
        self.stop_event, self.interrupt_event = threading.Event(), threading.Event()
        self.raw = queue.Queue(maxsize=32)
        self.utterances = queue.Queue(maxsize=2)
        self.threads = []
        self._epoch, self._lock = 0, threading.Lock()
        self._status = {'state': 'stopped', 'audio_drops': 0, 'utterance_drops': 0,
                        'stt_errors': 0, 'dispatch_errors': 0, 'interrupt_errors': 0}
        self.model = self.vad = None

    def status(self):
        with self._lock:
            return dict(self._status)

    def _update(self, **values):
        with self._lock:
            self._status.update(values)

    def _increment(self, name):
        with self._lock:
            self._status[name] = self._status.get(name, 0)+1

    def start(self):
        if self.threads:
            raise RuntimeError('Listener already started; create a new instance after stopping')
        import pyaudio  # Fail on the caller thread if required wheels are missing.
        import soxr
        if not (Path(self.cfg.model_path)/'model.bin').is_file():
            raise ValueError('Local Faster-Whisper model missing; no automatic model download')
        if self.model_factory is None:
            try:
                from whisper_backend import WhisperModel
            except ImportError:
                from faster_whisper import WhisperModel
            self.model_factory = WhisperModel
        self._update(state='loading_models')
        self.model = self.model_factory(self.cfg.model_path, device='cpu', compute_type='int8',
                                        cpu_threads=self.cfg.cpu_threads, local_files_only=True,
                                        num_workers=1)
        self.vad = self.vad_factory(self.cfg.vad_model_path)
        # Warm up before opening the microphone, not after the first command.
        import numpy as np
        self.vad.probability(np.zeros(FRAME, dtype=np.float32)); self.vad.reset()
        segments, _ = self.model.transcribe(np.zeros(RATE//2, dtype=np.float32),
                                           language=self.cfg.language, beam_size=1,
                                           condition_on_previous_text=False, vad_filter=False)
        list(segments)
        for target, name in ((self._capture, 'mic'), (self._vad_loop, 'vad'),
                             (self._transcribe, 'stt'), (self._interrupt, 'interrupt')):
            t = threading.Thread(target=target, name='zoya-'+name, daemon=True)
            self.threads.append(t); t.start()

    def stop(self, timeout=5):
        self.stop_event.set(); self.interrupt_event.set()
        deadline = time.monotonic()+timeout
        for t in self.threads:
            if t is not threading.current_thread():
                t.join(max(0, deadline-time.monotonic()))
        # An in-flight CTranslate2 inference cannot be force-killed safely.
        self._update(state='stopped' if all(not t.is_alive() for t in self.threads) else 'stopping')

    def _capture(self):
        import pyaudio
        retry = .5
        while not self.stop_event.is_set():
            pa = stream = None
            try:
                pa = pyaudio.PyAudio()
                index, rate = choose_device(pa, self.cfg.device_index)
                block = max(128, round(rate*FRAME/RATE))
                def callback(data, frame_count, time_info, status):
                    if self.stop_event.is_set():
                        return None, pyaudio.paComplete
                    # O(1), bounded; never run VAD, STT, HTTP or file I/O here.
                    try:
                        self.raw.put_nowait((data, rate, time.monotonic()))
                    except queue.Full:
                        self._increment('audio_drops')
                    if status:
                        self._increment('audio_status_flags')
                    return None, pyaudio.paContinue
                stream = pa.open(format=pyaudio.paInt16, channels=1, rate=rate, input=True,
                                 input_device_index=index, frames_per_buffer=block,
                                 stream_callback=callback, start=False)
                self._update(state='listening', device_index=index, sample_rate=rate)
                stream.start_stream(); retry = .5
                while not self.stop_event.wait(.1):
                    if not stream.is_active():
                        raise OSError('Microphone stream stopped')
            except Exception as exc:
                LOG.warning('Microphone unavailable (%s); retrying', type(exc).__name__)
                self._update(state='microphone_recovering', microphone_error=type(exc).__name__)
            finally:
                if stream:
                    with contextlib.suppress(Exception): stream.stop_stream()
                    with contextlib.suppress(Exception): stream.close()
                if pa:
                    with contextlib.suppress(Exception): pa.terminate()
            if self.stop_event.wait(retry):
                break
            retry = min(10, retry*2)

    def _vad_loop(self):
        import numpy as np
        segmenter = SpeechSegmenter(self.cfg)
        pending = np.zeros(0, dtype=np.float32)
        source_rate = None; resampler = None; previous_drop = 0; last_capture = None
        while not self.stop_event.is_set():
            try:
                data, rate, captured = self.raw.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                drops = self.status()['audio_drops']
                gap = last_capture is not None and captured-last_capture > .2
                if rate != source_rate or drops != previous_drop or gap or time.monotonic()-captured > .25:
                    segmenter.reset(); self.vad.reset(); pending = np.zeros(0, dtype=np.float32)
                    if rate != RATE:
                        import soxr
                        resampler = soxr.ResampleStream(rate, RATE, 1, dtype='float32', quality='LQ')
                    else:
                        resampler = None
                    source_rate, previous_drop = rate, drops
                    if time.monotonic()-captured > .25:
                        self._increment('stale_audio_blocks'); continue
                last_capture = captured
                x = np.frombuffer(data, dtype='<i2').astype(np.float32)/32768.0
                if resampler:
                    x = resampler.resample_chunk(x)
                pending = np.concatenate((pending, x))
                while pending.size >= FRAME:
                    frame = pending[:FRAME].copy(); pending = pending[FRAME:]
                    started, utterance, forced = segmenter.feed(frame, self.vad.probability(frame))
                    if started:
                        with self._lock:
                            self._epoch += 1
                        self.interrupt_event.set()
                    if utterance:
                        with self._lock: epoch = self._epoch
                        item = (np.concatenate(utterance), epoch, time.monotonic(), str(uuid.uuid4()))
                        try:
                            self.utterances.put_nowait(item)
                        except queue.Full:
                            self._increment('utterance_drops')
                            LOG.warning('Transcription queue full; command dropped, not replayed')
                    if forced:
                        self._increment('overlong_utterances')
                        LOG.warning('Utterance exceeded time limit; discarded until silence, no partial command dispatched')
            except Exception as exc:
                self._increment('vad_errors'); segmenter.reset(); self.vad.reset()
                pending = np.zeros(0, dtype=np.float32)
                LOG.exception('VAD frame rejected (%s)', type(exc).__name__)

    def _interrupt(self):
        while not self.stop_event.is_set():
            if not self.interrupt_event.wait(.1):
                continue
            self.interrupt_event.clear()
            if self.stop_event.is_set(): break
            try:
                self.on_speech_start()
            except Exception as exc:
                self._increment('interrupt_errors')
                LOG.warning('Playback stop failed (%s)', type(exc).__name__)

    def _transcribe(self):
        while not self.stop_event.is_set():
            try:
                samples, epoch, queued, request_id = self.utterances.get(timeout=.1)
            except queue.Empty:
                continue
            with self._lock: current = self._epoch
            if epoch != current or time.monotonic()-queued > self.cfg.max_queue_age_seconds:
                self._increment('stale_utterances'); continue
            try:
                began = time.monotonic()
                segments, _ = self.model.transcribe(samples, language=self.cfg.language,
                     beam_size=1, best_of=1, temperature=0, condition_on_previous_text=False,
                     vad_filter=False, no_speech_threshold=.6, log_prob_threshold=-1.0)
                # The segments iterator performs inference: consume it INSIDE the try.
                text = ' '.join(s.text.strip() for s in segments
                                if getattr(s, 'no_speech_prob', 0) < .8).strip()
                self._update(last_stt_ms=round((time.monotonic()-began)*1000),
                             last_queue_ms=round((began-queued)*1000))
            except Exception as exc:
                self._increment('stt_errors')
                LOG.warning('Transcription failed (%s)', type(exc).__name__); continue
            with self._lock: current = self._epoch
            if not text or epoch != current or self.stop_event.is_set():
                continue
            if len(text) > 4000:
                self._increment('rejected_transcripts'); continue
            try:
                self.on_text(text, request_id)
            except Exception as exc:
                self._increment('dispatch_errors')
                # No replay: an HTTP timeout can follow successful execution.
                LOG.warning('Agent dispatch outcome uncertain (%s); not retried', type(exc).__name__)


class InterruptiblePlayer:
    """Playback owner for same-process barge-in. Replaces blocking playsound/MCI.

    Decode worker cancellation also invalidates late audio that arrives after a
    barge-in. All audio playback must use THIS instance for stop() to control it.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.epoch = 0
        self.stream = self.pa = self.decoder = None

    def stop(self):
        with self.lock:
            self.epoch += 1
            if self.decoder and self.decoder.poll() is None:
                with contextlib.suppress(OSError): self.decoder.terminate()
            self.decoder = None
            if self.stream:
                with contextlib.suppress(Exception): self.stream.stop_stream()
                with contextlib.suppress(Exception): self.stream.close()
                self.stream = None
            if self.pa:
                with contextlib.suppress(Exception): self.pa.terminate()
                self.pa = None

    def begin_response(self) -> int:
        """Call BEFORE starting asynchronous TTS generation."""
        self.stop()
        with self.lock: return self.epoch

    def play(self, path: str | Path, *, expected_epoch: int | None = None):
        if expected_epoch is None:
            self.stop()
        else:
            with self.lock:
                if expected_epoch != self.epoch:
                    return False  # A barge-in invalidated a still-running TTS request.
        source = Path(path).resolve()
        if not source.is_file(): raise ValueError('Playback needs a local audio file')
        with self.lock: epoch = self.epoch
        t = threading.Thread(target=self._play, args=(source, epoch), daemon=True, name='zoya-playback')
        t.start()
        return True

    def _play(self, source, epoch):
        import pyaudio
        import shutil
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            LOG.error('Cannot play audio: FFmpeg missing'); return
        decoder = subprocess.Popen([ffmpeg, '-nostdin', '-v', 'error', '-i', str(source),
                                   '-t', '300', '-f', 's16le', '-ac', '1', '-ar', '24000', '-'],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
        with self.lock:
            if epoch != self.epoch:
                decoder.terminate(); decoder.wait(); return
            self.decoder = decoder
        try:
            pcm, _ = decoder.communicate(timeout=60)
            if decoder.returncode:
                raise RuntimeError('Audio decode failed')
            position = 0
            def callback(in_data, frame_count, time_info, status):
                nonlocal position
                if epoch != self.epoch: return b'', pyaudio.paComplete
                data = pcm[position:position+frame_count*2]; position += len(data)
                return data, pyaudio.paComplete if position >= len(pcm) else pyaudio.paContinue
            with self.lock:
                if epoch != self.epoch: return
                self.decoder = None
                self.pa = pyaudio.PyAudio()
                self.stream = self.pa.open(format=pyaudio.paInt16, channels=1, rate=24000,
                                          output=True, frames_per_buffer=512,
                                          stream_callback=callback, start=False)
                self.stream.start_stream()
            while True:
                with self.lock:
                    if epoch != self.epoch or not self.stream or not self.stream.is_active(): break
                time.sleep(.02)
        except Exception as exc:
            LOG.warning('Playback failed (%s)', type(exc).__name__)
        finally:
            with contextlib.suppress(Exception):
                if decoder.poll() is None: decoder.kill()
                decoder.wait(timeout=2)
            with self.lock:
                if epoch == self.epoch: self.stop()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', type=str)
    p.add_argument('--vad-model', type=str)
    p.add_argument('--language', default=None)
    p.add_argument('--device-index', type=int)
    p.add_argument('--list-devices', action='store_true')
    p.add_argument('--base-url', default='http://127.0.0.1:8767')
    p.add_argument('--input-path', default='/api/conversation')
    p.add_argument('--interrupt-path', default=None)
    p.add_argument('--voice-replies', action='store_true')
    p.add_argument('--silence-ms', type=int, default=256)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.list_devices:
        import pyaudio
        pa = pyaudio.PyAudio()
        try: print(json.dumps(input_devices(pa), indent=2))
        finally: pa.terminate()
        return
    if not args.model or not args.vad_model:
        p.error('--model and --vad-model local paths are required')
    client = LocalAgentClient(args.base_url, args.input_path, args.interrupt_path,
                              voice_replies=args.voice_replies, timeout=15)
    if not args.interrupt_path:
        LOG.warning('Standalone HTTP dispatch enabled; external playback barge-in requires --interrupt-path or same-process stop callback')
    listener = VoiceListener(ListenerConfig(args.model, args.vad_model, language=args.language,
                             device_index=args.device_index, silence_ms=args.silence_ms),
                             client.dispatch, client.interrupt)
    def stop(*_): listener.stop_event.set()
    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, 'SIGTERM'): signal.signal(signal.SIGTERM, stop)
    try:
        listener.start()
        while not listener.stop_event.wait(5): LOG.info('Listener health: %s', listener.status())
    finally:
        listener.stop()


if __name__ == '__main__':
    main()
