"""Local hybrid wake gate and Faster-Whisper command recognition; no cloud audio."""
import json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def is_wake(text):
 import re
 from conversation_style import roman_display
 spoken=roman_display(text).lower().strip(' .!?')
 return bool(re.match(r'^(?:hey|he|hello|helo|hi|hai)\s+zoya\b|^zoya\b',spoken))
class Recognizer:
 def __init__(self,d):
  from vosk import Model,KaldiRecognizer,SetLogLevel
  SetLogLevel(-1);self.d=d;self.buffer=bytearray();self.result='';self.partial=''
  self.vosk=KaldiRecognizer(Model(str(ROOT/'data/offline/vosk-model-small-hi-0.22')),16000);self.whisper=None
  model=ROOT/'data/offline/faster-whisper-base'
  if model.joinpath('model.bin').is_file():
   try:
    from whisper_backend import WhisperModel
    self.whisper=WhisperModel(str(model),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1)
   except Exception as error:
    d.health['last_stt_error']=type(error).__name__+': '+str(error)
    d.post('status','Whisper unavailable; existing Vosk wake/command recognition retained: '+type(error).__name__)
  d.health['stt_backend']='faster-whisper/base + local Vosk wake gate' if self.whisper else 'Vosk / Whisper unavailable; see last_stt_error'
  d.health['wake_latency_verified']=False
 def Reset(self):
  self.vosk.Reset();self.buffer.clear();self.result='';self.partial=''
 def AcceptWaveform(self,data):
  self.buffer.extend(data)
  if len(self.buffer)>16000*2*15:self.buffer=self.buffer[-16000*2*15:]
  accepted=self.vosk.AcceptWaveform(data)
  if not accepted:
   self.partial=json.loads(self.vosk.PartialResult()).get('partial','')
   if is_wake(self.partial):
    self.d.listening_until=time.monotonic()+45;self.d.health['voice_state']='listening'
   return False
  text=json.loads(self.vosk.Result()).get('text','')
  # Wake detection never grants approval; only opens a short local command window.
  if is_wake(text):self.d.listening_until=time.monotonic()+45
  if self.whisper and len(self.buffer)>=8000 and (is_wake(text) or time.monotonic()<getattr(self.d,'listening_until',0)):
   import numpy as np
   audio=np.frombuffer(bytes(self.buffer),dtype=np.int16).astype(np.float32)/32768
   started=time.monotonic();self.d.health['voice_state']='processing'
   try:
    segments,info=self.whisper.transcribe(audio,beam_size=1,vad_filter=True,condition_on_previous_text=False,language='hi')
    words=' '.join(s.text.strip() for s in segments).strip()
    if words:text=words
    self.d.health['last_stt_seconds']=round(time.monotonic()-started,3)
   except Exception as e:
    self.d.health['last_stt_error']=type(e).__name__
    text='' # Failed advanced transcription must not execute an approximate fallback command.
   finally:self.d.health['voice_state']='listening'
  self.buffer.clear();self.result=json.dumps({'text':text});return True
 def Result(self):return self.result or '{"text":""}'
