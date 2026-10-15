# Zoya: synchronized UHD video and hands-free voice upgrade

Complete modules for regular Windows x64 CPython 3.13. No Canva, paid render API,
provider login or publishing is needed for local rendering. This is source code
with tests, not a claim that the laptop microphone has passed live acceptance.

## Files

| File | Purpose |
|---|---|
| `video_engine.py` | Audio-master timeline, ElevenLabs alignment / local Whisper, native UHD source checks, H.264/H.265, 30/60fps, subtitles, decode/audio QA |
| `voice_listener.py` | PyAudio capture, Silero ONNX, endpointing, background STT, bounded recovery, localhost dispatch, interruptible playback |
| `service_integration.py` | Durable single-worker render queue and main-thread desktop voice adapter |
| `prepare_models.py` | Copy/validate bundled Silero model; reuse or explicitly download Whisper |
| `requirements.txt` | Pinned runtime dependencies |
| `example_manifest.json` | Asset-to-dialogue mapping example; sample times are illustrative, not real alignment |
| `test_upgrade.py` | Timing, VAD, stale audio, recovery and actual FFmpeg integration tests |

## 1. Install in an isolated environment

Extract this folder under the existing Zoya installation. Run PowerShell in it.
Keep the original installation and its existing environment until acceptance passes.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
ffmpeg -version
ffprobe -version
```

Use the existing FFmpeg installation. Both executables must be on this process's
PATH. In the existing Zoya entrypoint, `local_settings.load(ROOT)` already sets
that PATH; call it before importing/running either module. Standalone shells can
reuse the existing pointer without changing the global Windows PATH:

```powershell
$ffmpegBin = (Get-Content "$env:LOCALAPPDATA\ZoyaAgent\tools\ffmpeg-bin.txt" -Raw).Trim()
$env:PATH = "$ffmpegBin;$env:PATH"
```

The video engine itself uses only the Python standard library plus FFmpeg unless
local Whisper extraction is requested. Do not globally replace working audio
packages. If integrating inside the current desktop process, start that process
with this environment after reviewing its other dependencies. Otherwise install
the exact audio requirements into the desktop's own isolated environment first.

## 2. Prepare models once

Prefer the already installed multilingual model for Hindi/Urdu/English. The
English-only `base.en` model is unsuitable for Urdu/Hindi commands.

```powershell
.\.venv\Scripts\python.exe prepare_models.py `
  --vad-output .\models\silero_vad_v6.onnx `
  --whisper-source ..\data\offline\faster-whisper-base
```

This copies Silero from the pinned Faster-Whisper package, validates its graph,
runs actual silence inference, and reports the model hash. The listener does not
download models during startup. If there is no existing speech model, explicitly
download one (requires internet, no API key):

```powershell
.\.venv\Scripts\python.exe prepare_models.py `
  --download-model base --model-output .\models\whisper
```

For English-only input use `--download-model base.en`; `small` is more demanding
on CPU. CPU `int8`, two inference threads and one render worker are conservative
defaults for the 16GB laptop. Rendering and STT still compete for CPU; measure
latency while rendering before promising a response SLA.

## 3. Render against the actual audio timeline

Use alignment from the SAME ElevenLabs response as the saved audio. Supported
formats:

```python
# Word OR sentence units. No interpolation inside a sentence.
timestamps = [
    {"start": 0.1, "end": 0.4, "text": "Hello"},
    {"start": 0.6, "end": 1.0, "text": "world!"},
]
# Or pass the ElevenLabs response's alignment/normalized_alignment object:
# characters, character_start_times_seconds, character_end_times_seconds.
```

```python
from video_engine import render_video

report = render_video(
    script_text="Hello world!",
    audio_path="voice/narration.wav",
    timestamps=timestamps,
    media_assets=[
        {"path": "assets/hello-4k.mp4", "text": "Hello"},
        {"path": "assets/world-4k.jpg", "text": "world!"},
    ],
    output_path="outbox/demo-4k.mp4",
    workspace=r"C:\ZoyaMedia",  # assigned media directory, not whole laptop
    fps=30,                     # 30 or 60
    codec="h264",               # h264 or h265
    require_native_4k=True,
)
```

Every asset must cover adjacent timestamp units exactly once. You can use
`start_index` and exclusive `end_index` instead of `text`. Leading silence stays
with the first scene; a subsequent scene starts on its first spoken unit's
timestamp; the last scene continues to the audio end. Cuts are quantized to the
nearest output frame, not arbitrary equal-length segments. The last frame can
extend at most one frame beyond audio; it does not stretch narration.

The renderer trims longer clips. It rejects too-short clips unless that asset
explicitly sets `"loop": true`; it never silently freezes a short clip. Static
images are intentionally held. It rejects source dimensions below 3840x2160 by
default; setting `require_native_4k=False` explicitly permits upscaling and is
recorded in the report. Resolution is not a guarantee of source detail or semantic
match. CRF18 is high-quality lossy encoding, not lossless encoding.

For local word timestamps instead of ElevenLabs alignment:

```python
from video_engine import extract_timestamps
timestamps = extract_timestamps(
    script_text, audio_path,
    model_path=r"C:\Zoya\data\offline\faster-whisper-base",
    language=None,  # multilingual auto-detection, or en/hi/ur explicitly
)
```

Whisper output must match the reviewed script; mismatch blocks the render. Do
not substitute guessed timings or silently rewrite a religious recitation.
Sentence-only timestamps can only cut on sentence boundaries. This module selects
and times supplied visuals; it does not generate character animation or repair
lip-sync. Character-performance video must already match the exact master audio.

CLI:

```powershell
.\.venv\Scripts\python.exe video_engine.py `
  --manifest .\my_manifest.json --workspace C:\ZoyaMedia `
  --output outbox\demo-4k.mp4
```

Existing outputs are never overwritten. The MP4 is accompanied by `.srt` and
`.render.json` evidence. A `.mp4.lock` survives an externally killed process:
first inspect that worker/output, then remove the stale lock manually. No automatic
paid retry is involved.

## 4. Add asynchronous render routes to the existing port-8767 service

In the same startup function which creates `production=ProductionCoordinator(...)`,
create ONE queue. Add this upgrade directory to `sys.path` if it is a subfolder.

```python
from service_integration import RenderQueue
render_queue = RenderQueue(ROOT / "workspaces" / "Synced4K")
render_queue.start()
```

Inside the existing `do_POST`, AFTER its trusted-host, Origin and X-Zoya-Token
checks, add these branches beside the existing `/api/studio/...` branches:

```python
elif path == '/api/video-engine/create':
    result = render_queue.create(p.get('manifest'), p.get('request_id'))
elif path == '/api/video-engine/status':
    result = render_queue.get(p.get('id'))
```

Keep the existing JSON response and exception handling. The body is:

```json
{
  "request_id": "one-stable-video-request-id",
  "manifest": {
    "script_text": "Hello world!",
    "audio_path": "voice/narration.wav",
    "timestamps": [
      {"start": 0.1, "end": 0.4, "text": "Hello"},
      {"start": 0.6, "end": 1.0, "text": "world!"}
    ],
    "media_assets": [
      {"path": "assets/hello-4k.mp4", "text": "Hello"},
      {"path": "assets/world-4k.jpg", "text": "world!"}
    ],
    "output_path": "outbox/demo-4k.mp4",
    "fps": 30,
    "codec": "h264"
  }
}
```

The authenticated helper already used by the desktop can submit this body.
Poll status using `{"id":"returned-job-id"}`; never resubmit because a render
has not finished. The request ID rejects changed payloads. One encoder runs at a
time, and at most four jobs can be queued/running. Restarted in-flight jobs become
`interrupted`; they are not automatically replayed. `render_queue.close()` stops
claiming new jobs on service shutdown. Keep the default 20KB request limit for
short scripts, or explicitly raise it to at most 1MB for the new render routes
only. Do not remove authentication or broadly increase other route limits.

## 5. Microphone test, then same-process hands-free integration

First list devices:

```powershell
.\.venv\Scripts\python.exe voice_listener.py --list-devices
```

For an isolated transcription-to-existing-agent test (no new TTS by default):

```powershell
.\.venv\Scripts\python.exe voice_listener.py `
  --model ..\data\offline\faster-whisper-base `
  --vad-model .\models\silero_vad_v6.onnx `
  --base-url http://127.0.0.1:8767 --input-path /api/conversation
```

Add `--device-index N` only if automatic selection chooses the wrong input. The
capture layer tries default physical microphones first, rejects obvious loopback
devices, and resamples supported native sample rates to 16kHz. If the mic drops,
it closes the device and retries with bounded backoff. Audio is not saved or logged.

This standalone test dispatches text to the existing conversation endpoint.
It does NOT control audio owned by a separate desktop process. Do not claim
barge-in just because this CLI is transcribing. `--interrupt-path` is only valid
if you implement that authenticated route and it actually reaches the playback
owner; this package does not invent an existing stop-audio endpoint.

For full barge-in, run listener and player inside `zoya_desktop.py`, which owns
the audio. Disable its old microphone capture loop before starting this one;
otherwise two listeners race for the same device. Keep its typed-command handler
and approval gates. Wire that SAME handler via the adapter:

```python
from voice_listener import ListenerConfig, InterruptiblePlayer
from service_integration import DesktopVoiceAdapter

# Run once during Desktop initialization, on the Tk/main thread.
self.realtime_player = InterruptiblePlayer()
self.realtime_voice = DesktopVoiceAdapter(
    tk_root=self.root,
    dispatch_text=lambda text, request_id: self.enqueue_typed_command(text, request_id),
    stop_playback=self.realtime_player.stop,
    config=ListenerConfig(
        model_path=str(ROOT / 'data/offline/faster-whisper-base'),
        vad_model_path=str(ROOT / 'models/silero_vad_v6.onnx'),
        language=None,
        silence_ms=256,
    ),
)
self.realtime_voice.start()
```

`enqueue_typed_command` above is the ONE application-specific binding: replace
that name with the existing typed-input enqueue function; do not create a second
planner or call a blocking LLM from the Tk main thread. The live desktop source
could not be inspected because its remote connection timed out, so an exact
method name is deliberately not asserted here.

Replace the current blocking `play_mp3(path)` implementation with playback through
`self.realtime_player`. For each asynchronous TTS turn, reserve its token BEFORE
starting generation:

```python
token = self.realtime_player.begin_response()
# Existing ElevenLabs generation runs in its existing background worker.
audio_path = existing_tts_generate(reply_text)
self.realtime_player.play(audio_path, expected_epoch=token)
```

When speech starts, `stop()` terminates decoding/stream playback and invalidates
the token. A late ElevenLabs result cannot start playing after the interruption.
All Zoya playback must use this one player, or a thread-safe stop callback must
also stop every other playback owner. Stopping sound does not undo usage already
spent on a provider request. On desktop shutdown, on the main thread:

```python
self.realtime_voice.close()
self.realtime_player.stop()
```

Use a headset for first acceptance. Silero is VAD, not acoustic echo cancellation:
speaker audio heard by the microphone can look like a user and cause self-barge-in.
True open-speaker full duplex needs an AEC-capable audio path. Do not hide this
problem by disabling the mic during TTS, since that would disable barge-in.

## 6. Latency and recovery contract

- Audio callbacks only enqueue bounded PCM blocks; no HTTP, STT or TTS in callbacks.
- Silero consumes sequential 512-sample frames (32ms) with recurrent state.
- Two high-confidence frames trigger the interruption worker. The 256ms silence
  threshold emits an utterance to STT, with up to one frame of scheduling
  quantization under healthy capture. Actual transcription and response latency
  depend on CPU, utterance length, language and model; zero latency is impossible.
- Health includes STT milliseconds, queue delay, dropped blocks/utterances and
  recovery counters. Capture overflow/gaps discard corrupted utterances.
- STT queue length is two; stale pending commands are discarded instead of
  executing later. Overlong speech is discarded until silence, not dispatched as
  a potentially dangerous partial command. HTTP command dispatch is not retried
  after an uncertain response.
- Ctrl+C closes streams and joins workers with a bound. In-flight native inference
  cannot safely be force-killed inside a thread. Do not load Whisper per utterance.

## 7. Verification and acceptance

```powershell
.\.venv\Scripts\python.exe -m unittest test_upgrade -v
$env:ZOYA_TEST_4K = '1'
.\.venv\Scripts\python.exe -m unittest test_upgrade.FFmpegTests -v
$env:ZOYA_TEST_FPS = '60'
$env:ZOYA_TEST_CODEC = 'h265'
.\.venv\Scripts\python.exe -m unittest test_upgrade.FFmpegTests -v
```

Recorded in the build environment: 21 unit tests pass; actual H.264/30fps and
H.265/60fps UHD encode tests pass. The integration test checks audio, captions,
duration, full decode, dimensions and red-to-blue scene selection at measured
timeline positions. Actual bundled Silero v6 inference/reset passes. Windows
CPython3.13 binary wheels for pinned runtime dependencies resolve/download.

Still required on the laptop: actual package installation, microphone selection,
Hindi/Urdu/English accuracy, end-of-speech latency under render load, speaker/AEC
behavior, physical playback interruption, typed-handler binding and one complete
real-voice 4K acceptance video. Do not call this deployment production-verified
until these checks pass. No existing working installation was overwritten by this
source package.

Official references: https://github.com/SYSTRAN/faster-whisper ;
https://github.com/snakers4/silero-vad ; https://pypi.org/project/PyAudio/ ;
https://ffmpeg.org/ffmpeg-filters.html .
