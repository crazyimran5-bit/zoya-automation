"""Live Windows adapter for the existing Zoya Desktop.submit/reply contract."""
import queue
import threading
import time
from pathlib import Path

from voice_listener import InterruptiblePlayer, ListenerConfig, VoiceListener


def initialize(d):
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    d.realtime_player = InterruptiblePlayer()
    d.realtime_context = threading.local()
    d.realtime_commands = queue.Queue(maxsize=1)
    d.realtime_listener = None
    d.health['realtime_integration'] = 'PyAudio + Silero + local Whisper; existing submit/approval path'

    def pump():
        if d.closed.is_set(): return
        try:
            if not d.busy.is_set():
                text, received = d.realtime_commands.get_nowait()
                if time.monotonic()-received <= 30 and not d.muted and not d.locked:
                    d.submit(text)
                else:
                    d.post('status','Queued voice command expired or microphone paused; please repeat.')
        except queue.Empty:
            pass
        finally:
            if not d.closed.is_set(): d.root.after(50,pump)
    d.root.after(50,pump)


def begin_turn(d):
    d.realtime_context.epoch = d.realtime_player.begin_response()


def valid_turn(d):
    with d.realtime_player.lock:
        return getattr(d.realtime_context,'epoch',d.realtime_player.epoch) == d.realtime_player.epoch


def play(d,path):
    with d.realtime_player.lock:
        token=getattr(d.realtime_context,'epoch',d.realtime_player.epoch)
    if not d.realtime_player.play(path,expected_epoch=token): return
    d.health['voice_state']='speaking'
    while not d.closed.wait(.02):
        with d.realtime_player.lock:
            if d.realtime_player.epoch != token: break
    d.health['voice_state']='listening'


def run_listener(d):
    root=Path(__file__).resolve().parent
    from voice_pipeline_hardening import normalize_stt
    from advanced_stt import is_wake

    def heard(text,request_id):
        if d.closed.is_set() or d.muted or d.locked: return
        text=normalize_stt(text)
        wake=is_wake(text)
        if wake: d.listening_until=time.monotonic()+45
        if not wake and time.monotonic()>=getattr(d,'listening_until',0): return
        try: d.realtime_commands.get_nowait()
        except queue.Empty: pass
        try: d.realtime_commands.put_nowait((text,time.monotonic()))
        except queue.Full: return
        d.health['last_voice_request_id']=request_id
        if d.busy.is_set():
            d.post('status','Voice command captured; waiting for the current agent step to stop safely.')

    def interrupt():
        if d.closed.is_set() or d.muted or d.locked: return
        d.realtime_player.stop()
        d.health['barge_in_count']=d.health.get('barge_in_count',0)+1

    cfg=ListenerConfig(str(root/'data/offline/faster-whisper-base'),
                       str(root/'models/silero_vad_v6.onnx'),language=None,
                       silence_ms=256,cpu_threads=2)
    listener=VoiceListener(cfg,heard,interrupt)
    d.realtime_listener=listener
    listener.set_enabled(not d.muted and not d.locked)
    try:
        listener.start()
        d.post('status','Realtime microphone ready. Hey Zoya boliye; wake window and approvals remain active.')
        while not d.closed.wait(.1):
            listener.set_enabled(not d.muted and not d.locked)
            status=listener.status()
            d.health['realtime_listener']=status
            d.health['microphone_open']=status.get('state')=='listening'
            d.health['microphone_device']=status.get('device_index')
            d.health['mic_level']=min(1,status.get('last_rms',0)/.2)
            d.health['stt_backend']='Silero VAD + local Faster-Whisper/base (multilingual)'
    except Exception as error:
        d.health['microphone_open']=False
        d.health['last_stt_error']=type(error).__name__+': '+str(error)
        d.post('status','Realtime microphone stopped: '+d.health['last_stt_error']+'. Typed commands remain available.')
    finally:
        listener.stop()


def close(d):
    d.realtime_player.stop()
    if d.realtime_listener: d.realtime_listener.stop()
