"""Native desktop voice controller. Local microphone, scoped commands, no automatic task retries."""
import json, math, os, queue, re, sqlite3, subprocess, sys, threading, time, uuid, wave
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.request import Request,urlopen
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'config/desktop.json'
def greeting(hour):
 return 'Assalamu alaikum. '+('Good morning' if 5<=hour<12 else 'Good afternoon' if 12<=hour<17 else 'Good evening' if 17<=hour<21 else 'Good night')+', boss.'
def greeting_key(hour):
 return 'morning' if 5<=hour<12 else 'afternoon' if 12<=hour<17 else 'evening' if 17<=hour<21 else 'night'
def load_config():return json.loads(CONFIG.read_text(encoding='utf-8'))
def http(path,body=None):
 page=urlopen('http://127.0.0.1:8767',timeout=8).read().decode()
 match=re.search(r"const token='([^']+)'",page)
 if not match:raise ValueError('Zoya authentication token unavailable')
 request=Request('http://127.0.0.1:8767'+path,data=None if body is None else json.dumps(body).encode(),headers={'X-Zoya-Token':match[1],'Content-Type':'application/json','Origin':'http://127.0.0.1:8767'})
 with urlopen(request,timeout=120) as response:return response.read()
class Desktop:
 def __init__(self):
  import ctypes
  self.mutex=ctypes.windll.kernel32.CreateMutexW(None,False,'Local\\ZoyaNativeDesktop')
  if ctypes.windll.kernel32.GetLastError()==183:raise SystemExit('Zoya desktop already active')
  self.health={'started':time.time(),'microphone_open':False,'voice_source':'saved selection','daily_cap':None};self.last_health=0
  self.events=queue.Queue();self.audio=queue.Queue(maxsize=100);self.busy=threading.Event();self.closed=threading.Event();self.pending=None;self.muted=False
  self.root=tk.Tk();self.root.title('ZOYA · Desktop Command Centre');self.root.geometry('1100x730');self.root.configure(bg='#070d1c')
  self.root.protocol('WM_DELETE_WINDOW',self.close)
  self.root.report_callback_exception=lambda kind,value,tb:self.post('status','Desktop callback: '+str(value))
  tk.Label(self.root,text='Z O Y A   /   DESKTOP COMMAND CENTRE',font=('Segoe UI',21,'bold'),fg='#74f5da',bg='#070d1c').pack(pady=16)
  body=tk.Frame(self.root,bg='#070d1c');body.pack(fill='both',expand=True,padx=12)
  self.canvas=tk.Canvas(body,width=330,height=420,bg='#070d1c',highlightthickness=0);self.canvas.pack(side='left')
  self.status=tk.StringVar(value='Starting local voice controller…');tk.Label(self.root,textvariable=self.status,fg='#a2b8d4',bg='#070d1c',font=('Segoe UI',11)).pack()
  self.feed=ScrolledText(body,bg='#101c30',fg='#eef6ff',insertbackground='white',font=('Segoe UI',12),wrap='word',height=17);self.feed.pack(side='right',fill='both',expand=True,padx=12,pady=10)
  row=tk.Frame(self.root,bg='#070d1c');row.pack(fill='x',padx=22,pady=8)
  self.entry=tk.Entry(row,bg='#172945',fg='white',insertbackground='white',font=('Segoe UI',12));self.entry.pack(side='left',fill='x',expand=True);self.entry.bind('<Return>',lambda e:self.submit(self.entry.get()))
  tk.Button(row,text='Pause / Resume mic',command=self.toggle).pack(side='left',padx=8)
  tk.Button(row,text='Preview offline Hindi voice',command=self.preview).pack(side='left',padx=8)
  tk.Button(row,text='Use previewed offline voice',command=self.choose).pack(side='left')
  self.locked=False;self.last_tick=time.time();self.root.after(5000,self.resume_check)
  self.root.after(500,self.register_unlock)
  self.phase=0;self.root.after(50,self.animate);self.root.after(100,self.pump)
  threading.Thread(target=self.start,daemon=True).start()
  threading.Thread(target=self.task_worker,daemon=True).start()
 def post(self,kind,text):
  if kind=='status':self.health['status']=text
  self.events.put((kind,text))
 def animate(self):
  self.phase+=.09;self.canvas.delete('all');x,y=max(320,self.canvas.winfo_width())/2,140
  for n in range(5):
   r=32+n*12+math.sin(self.phase+n)*4
   self.canvas.create_oval(x-r,y-r,x+r,y+r,outline='#164c62' if n%2 else '#39d8b4',width=2)
  self.canvas.create_text(x,y,text='Z',fill='#e5fff9',font=('Segoe UI',26,'bold'))
  self.canvas.create_text(x,260,text='LOCAL FIRST\nNo daily conversation cap',fill='#819eba',font=('Segoe UI',13),justify='left')
  self.canvas.create_text(x,330,text='VOICE → ZOYA → SKALES\nResults stay visible here',fill='#819eba',font=('Segoe UI',13),justify='left')
  if not self.closed.is_set():self.root.after(50,self.animate)
 def register_unlock(self):
  import ctypes
  from ctypes import wintypes
  user=ctypes.windll.user32
  user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
  hwnd=user.GetAncestor(self.root.winfo_id(),2)
  callback=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
  user.SetWindowLongPtrW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_void_p];user.SetWindowLongPtrW.restype=ctypes.c_void_p
  user.CallWindowProcW.argtypes=[ctypes.c_void_p,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM];user.CallWindowProcW.restype=ctypes.c_ssize_t
  def handler(window,message,wp,lp):
   if message==0x2B1:
    if wp==8:self.locked=True
    elif wp==7:
     self.locked=False
     if not self.busy.is_set():threading.Thread(target=self.resume_greeting,daemon=True).start()
   return user.CallWindowProcW(self.original_proc,window,message,wp,lp)
  self.window_callback=callback(handler)
  self.original_proc=user.SetWindowLongPtrW(hwnd,-4,ctypes.cast(self.window_callback,ctypes.c_void_p))
  if not self.original_proc:raise ValueError('Unlock greeting hook unavailable')
  wts=ctypes.windll.wtsapi32;wts.WTSRegisterSessionNotification.argtypes=[wintypes.HWND,wintypes.DWORD]
  if not wts.WTSRegisterSessionNotification(hwnd,0):self.post('status','Unlock event subscription failed; startup/wake greeting still available')
 def resume_check(self):
  now=time.time()
  if now-self.last_tick>45 and not self.busy.is_set():threading.Thread(target=self.resume_greeting,daemon=True).start()
  self.last_tick=now
  if not self.closed.is_set():self.root.after(5000,self.resume_check)
 def resume_greeting(self):
  self.busy.set()
  try:
   hour=datetime.now(timezone(timedelta(hours=3))).hour;self.post('Zoya',greeting(hour));cache=ROOT/'data/greetings'/(greeting_key(hour)+'.mp3')
   if cache.exists():self.play_mp3(cache)
  finally:self.busy.clear()
 def pump(self):
  if time.time()-self.last_health>5:
   self.health.update(heartbeat=time.time(),busy=self.busy.is_set(),paused=self.muted,locked=self.locked,offline_voice_approved=load_config().get('offline_voice_approved',False))
   (ROOT/'data/desktop-health.json').write_text(json.dumps(self.health),encoding='utf-8');self.last_health=time.time()
  while not self.events.empty():
   kind,text=self.events.get()
   if kind=='status':self.status.set(text)
   else:
    from conversation_style import roman_display
    self.feed.insert('end',kind+': '+roman_display(text)+'\n\n');self.feed.see('end')
  if not self.closed.is_set():self.root.after(100,self.pump)
 def close(self):self.closed.set();self.root.destroy()
 def toggle(self):self.muted=not self.muted;self.status.set('Mic paused' if self.muted else 'Listening · Send click ki zaroorat nahi')
 def start(self):
  try:
   media_pointer=Path(os.environ['LOCALAPPDATA'])/'ZoyaAgent/tools/ffmpeg-bin.txt'
   if media_pointer.exists():
    media_dir=Path(media_pointer.read_text(encoding='utf-8-sig').strip())
    if (media_dir/'ffmpeg.exe').is_file() and (media_dir/'ffprobe.exe').is_file():
     os.environ['PATH']=str(media_dir)+os.pathsep+os.environ.get('PATH','')
   # autosave.py holds an OS lock, preventing duplicate backup workers.
   autosave=ROOT/'autosave.py'
   if autosave.exists():
    with (ROOT/'data/autosave-runtime.log').open('ab') as log:
     subprocess.Popen([sys.executable,str(autosave)],cwd=ROOT,stdout=log,stderr=log,creationflags=0x08000000)
   try:urlopen('http://127.0.0.1:11434/api/tags',timeout=3)
   except Exception:
    ollama=Path(os.environ['LOCALAPPDATA'])/'Programs/Ollama/ollama.exe'
    if ollama.exists():subprocess.Popen([str(ollama),'serve'],creationflags=0x08000000)
   try:http('/api/state')
   except Exception:
    env=os.environ.copy();env['ZOYA_PORT']='8767'
    subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--no-browser'],cwd=ROOT,env=env,creationflags=0x08000000)
    for _ in range(15):
     if self.closed.wait(1):return
     try:http('/api/state');break
     except Exception:pass
   self.post('Zoya',greeting(datetime.now(timezone(timedelta(hours=3))).hour));self.busy.set()
   cache=ROOT/'data/greetings'/ (greeting_key(datetime.now(timezone(timedelta(hours=3))).hour)+'.mp3')
   if cache.exists():self.play_mp3(cache)
   else:self.post('status','Greeting text ready; selected-voice greeting audio cache not available yet')
   self.busy.clear();self.listen()
  except Exception as error:self.post('status','Voice start failed: '+str(error))
 def listen(self):
  import sounddevice as sd
  from vosk import Model,KaldiRecognizer,SetLogLevel
  SetLogLevel(-1);model=ROOT/'data/offline/vosk-model-small-hi-0.22'
  if not model.exists():raise ValueError('Offline Hindi recognition model not installed')
  recognizer=KaldiRecognizer(Model(str(model)),16000);was_busy=False
  def callback(data,frames,timing,status):
   if not self.busy.is_set() and not self.muted and not self.locked:
    try:self.audio.put_nowait(bytes(data))
    except queue.Full:pass
  devices=sd.query_devices();device=next((i for i,d in enumerate(devices) if d['max_input_channels']>0 and 'microphone array' in d['name'].lower()),None)
  if device is None:raise ValueError('Physical microphone not found; Stereo Mix is not used for voice commands')
  with sd.RawInputStream(device=device,samplerate=16000,blocksize=4000,dtype='int16',channels=1,callback=callback):
   self.health.update(microphone_open=True,microphone_device=device)
   self.post('status','Listening · Hindi/Urdu command boliye; mic aur Send button zaroori nahi')
   while not self.closed.is_set():
    try:data=self.audio.get(timeout=.3)
    except queue.Empty:
     if self.busy.is_set():recognizer.Reset();was_busy=True
     continue
    if self.busy.is_set() or self.muted or self.locked:recognizer.Reset();was_busy=True;continue
    if was_busy:recognizer.Reset();was_busy=False
    if recognizer.AcceptWaveform(data):
     text=json.loads(recognizer.Result()).get('text','').strip()
     if text:self.submit(text)
 def submit(self,text):
  text=text.strip()
  if not text or self.busy.is_set():return
  self.busy.set();self.post('Aap',text);self.post('status','Zoya samajh rahi hai…')
  threading.Thread(target=self.reply,args=(text,),daemon=True).start()
 def reply(self,text):
  self.health.pop('last_voice_error',None)
  try:
   from quran_voice import handle
   if handle(self,text):return
   low=text.lower()
   if self.pending:
    if low in ('haan','haa','ha','yes','हाँ','हां','जी हाँ','जी हां','हां यही करो','हाँ यही करो','ji haan'):
     request=self.pending;self.pending=None;self.run_action(*request);return
    if low in ('no','nahi','नहीं','cancel'):
     self.pending=None;self.post('Zoya','Ji boss, action cancel kar diya.');self.say('Ji boss, action cancel kar diya.');return
    self.pending=None
   apps={'notepad':'notepad.exe','नोटपैड':'notepad.exe','calculator':'calc.exe','कैलकुलेटर':'calc.exe','chrome':'chrome','क्रोम':'chrome','edge':'msedge','एज':'msedge','skales':str(Path(os.environ['LOCALAPPDATA'])/'Programs/skales/Skales.exe')}
   for name,target in apps.items():
    if name in low and any(w in low for w in ('open','kholo','खोल','चालू')):
     self.pending=('app',target);answer='Ji boss, '+name+' open karna hai? Haan boliye.';self.post('Zoya',answer);self.say(answer);return
   if any(w in low for w in ('open','kholo','खोल','चालू')):
    from app_catalog import shortcuts,match
    from unidecode import unidecode
    aliases={'एक्सेल':'excel','वर्ड':'word','पावरपॉइंट':'powerpoint','टेलीग्राम':'telegram','व्हाट्सएप':'whatsapp','वीएलसी':'vlc'}
    normalized=low
    for source,target in aliases.items():normalized=normalized.replace(source,target)
    candidate=match(unidecode(normalized),shortcuts())
    if candidate:
     name,target=candidate;self.pending=('shortcut',target);answer='Ji boss, '+name+' open karna hai? Haan boliye.';self.post('Zoya',answer);self.say(answer);return
   if low.startswith('/task ') or any(w in low for w in ('स्केल्स','skales','task banao','टास्क बनाओ')):
    self.pending=('task',text.removeprefix('/task '));answer='Ji boss, yeh task Skales ke local agents ko dena hai: '+self.pending[1]+'? Haan boliye.';self.post('Zoya',answer);self.say(answer);return
   result=json.loads(http('/api/conversation',{'message':text,'request_id':str(uuid.uuid4()),'voice':False}))
   self.post('Zoya',result['reply']);self.post('status','Jawab ready · voice taiyar ho rahi hai')
   self.say(result['reply'])
  except Exception as e:self.post('status','Sirf yeh step fail hua: '+str(e)+' · automatic retry nahi hui')
  finally:
   while not self.audio.empty():
    try:self.audio.get_nowait()
    except queue.Empty:break
   self.busy.clear();self.post('status',self.health.get('last_voice_error','Listening · aap bol sakte hain'))
 def run_action(self,kind,value):
  if kind=='shortcut':
   path=Path(value)
   if path.suffix.lower()!='.lnk' or not path.exists():raise ValueError('Registered application shortcut unavailable')
   os.startfile(str(path));self.post('Zoya','Ji boss, application open kar di.');self.say('Ji boss, application open kar di.')
  elif kind=='app':
   if value in ('chrome','msedge'):
    vendor='Google/Chrome/Application/chrome.exe' if value=='chrome' else 'Microsoft/Edge/Application/msedge.exe'
    paths=[Path(os.environ.get(k,''))/vendor for k in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')]
    executable=next((p for p in paths if p.exists()),None)
    if executable is None:raise ValueError('Requested browser executable not found')
    subprocess.Popen([str(executable)])
   else:subprocess.Popen([value])
   self.post('Zoya','Ji boss, application open kar di.');self.say('Ji boss, application open kar di.')
  else:
   task_id=str(uuid.uuid4())
   from contextlib import closing
   with closing(sqlite3.connect(ROOT/'data/desktop-queue.sqlite3')) as db:
    db.execute('CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,message TEXT,status TEXT,created REAL)')
    db.execute('INSERT INTO queue VALUES(?,?,?,?)',(task_id,value,'queued',time.time()));db.commit()
   self.post('Zoya','Ji boss, task queue mein save ho gaya. Result yahin aayega.');self.say('Ji boss, task save ho gaya. Aap agla command de sakte hain.')
 def task_worker(self):
  from contextlib import closing
  from skales_bridge import dispatch
  path=ROOT/'data/desktop-queue.sqlite3'
  with closing(sqlite3.connect(path)) as db:
   db.execute('CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,message TEXT,status TEXT,created REAL)')
   db.execute("UPDATE queue SET status='needs_review' WHERE status='running'");db.commit()
  while not self.closed.wait(1):
   with closing(sqlite3.connect(path)) as db:
    db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT id,message FROM queue WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
    if not row:db.commit();continue
    db.execute("UPDATE queue SET status='running' WHERE id=?",(row[0],));db.commit()
   try:
    from conversation_style import task_report
    result=dispatch(row[1]);self.post('Skales result',task_report(result));status=result['status']
   except Exception as error:self.post('status','Skales task retained: '+type(error).__name__);status='needs_review'
   with closing(sqlite3.connect(path)) as db:db.execute('UPDATE queue SET status=? WHERE id=?',(status,row[0]));db.commit()
 def say(self,text):
  try:
   if load_config().get('offline_voice_approved'):self.local_say(text);return
   import hashlib
   import local_settings,budget_guard
   from natural_voice import ElevenLabsVoice,VoiceConfig
   local_settings.load(ROOT);budget_guard.load_policy(ROOT)
   voice=ElevenLabsVoice(ROOT/'workspaces/Zoya',VoiceConfig.from_env('zoya'))
   key=hashlib.sha256((voice.config.voice_id+text).encode()).hexdigest()[:32]
   cache=ROOT/'data/voice-control-cache';cache.mkdir(exist_ok=True);path=cache/(key+'.mp3')
   if not path.exists():
    plan=voice.plan(text,'control-'+key+'.mp3');result=voice.generate(text,'control-'+key+'.mp3',plan['hash'],conversation_scope=True);path.write_bytes(Path(result['path']).read_bytes())
   self.play_mp3(path)
  except Exception as error:
   self.health['last_voice_error']='Voice step: '+str(error)+' · no automatic retry'
   self.post('status',self.health['last_voice_error'])
 def play_mp3(self,path):
  import ctypes
  mci=ctypes.windll.winmm.mciSendStringW
  mci('close zoyaaudio',None,0,None)
  if mci('open "'+str(path)+'" type mpegvideo alias zoyaaudio',None,0,None):raise ValueError('Windows audio player could not open MP3')
  try:
   if mci('play zoyaaudio wait',None,0,None):raise ValueError('Windows audio playback failed')
  finally:mci('close zoyaaudio',None,0,None)
 def local_say(self,text):
  from piper import PiperVoice
  import winsound
  model=ROOT/'data/offline/hi_IN-priyamvada-medium.onnx'
  voice=PiperVoice.load(str(model));path=ROOT/'data/desktop-offline.wav'
  with wave.open(str(path),'wb') as wav:voice.synthesize_wav(text,wav)
  winsound.PlaySound(str(path),winsound.SND_FILENAME)
 def preview(self):
  if self.busy.is_set():return
  self.busy.set()
  def work():
   try:self.local_say('अस्सलामु अलैकुम, पाशा भाई। मैं ज़ोया हूँ। यह ऑफलाइन हिंदी आवाज़ का नमूना है।')
   except Exception as e:self.post('status','Offline preview: '+str(e))
   finally:self.busy.clear()
  threading.Thread(target=work,daemon=True).start()
 def choose(self):
  config=load_config();config['offline_voice_approved']=True;CONFIG.write_text(json.dumps(config,indent=2));self.post('Zoya','Offline Hindi voice selected by you. Local replies use no provider credits.')
if __name__=='__main__':Desktop().root.mainloop()