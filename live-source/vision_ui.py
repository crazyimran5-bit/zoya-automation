"""Original Zoya particle console; animation states and town use measured runtime events."""
import json,math,sqlite3,time,threading,tkinter as tk
from pathlib import Path
from tkinter.scrolledtext import ScrolledText
BG='#050d17';PANEL='#091827';INK='#e6f3ff';ACCENT='#35dff4';MUTED='#8da5bc'
ROOT=Path(__file__).resolve().parent
ROLES=('Research','Script','Packaging','Editing','Quality','Publisher','Analytics','Supervisor')
COLORS=('#39dff4','#fb9d49','#56df97','#b7bce9')
def setup(d):
 d.root=tk.Tk();d.root.title('ZOYA / Vision Control Centre');d.root.geometry('1400x900');d.root.minsize(1080,760);d.root.configure(bg=BG)
 d.root.protocol('WM_DELETE_WINDOW',d.close);d.root.report_callback_exception=lambda kind,value,tb:d.post('status','UI exception: '+type(value).__name__)
 d.vision_started=time.monotonic();d.chat_visible=True;d.vision_metrics={};d.network_previous=None;d.agent_states={r:'Idle' for r in ROLES};d.particle_drag=0;d.town_photo=None
 head=tk.Frame(d.root,bg=BG);head.pack(fill='x',padx=22,pady=14)
 tk.Label(head,text='Z O Y A   /   VISION CONTROL',fg=INK,bg=BG,font=('Consolas',22,'bold')).pack(side='left')
 tk.Label(head,text='LOCAL WORKERS  /  APPROVAL REQUIRED',fg=ACCENT,bg=BG,font=('Consolas',10)).pack(side='right')
 d.metrics_bar=tk.StringVar(value='Reading live runtime…')
 tk.Label(d.root,textvariable=d.metrics_bar,bg=PANEL,fg=MUTED,font=('Consolas',10),anchor='w',padx=12,pady=8).pack(fill='x',padx=20)
 body=tk.Frame(d.root,bg=BG);body.pack(fill='both',expand=True,padx=20,pady=12)
 right=tk.Frame(body,bg=PANEL,width=380,highlightbackground='#20394d',highlightthickness=1);right.pack(side='right',fill='y',padx=(14,0));right.pack_propagate(False)
 tk.Label(right,text='CONVERSATION / BOSS',bg=PANEL,fg=ACCENT,font=('Consolas',13,'bold'),anchor='w').pack(fill='x',padx=16,pady=14)
 d.chat_frame=tk.Frame(right,bg=PANEL);d.chat_frame.pack(fill='both',expand=True,padx=10)
 d.feed=ScrolledText(d.chat_frame,bg=PANEL,fg=INK,insertbackground=INK,font=('Segoe UI',11),wrap='word',relief='flat',height=16);d.feed.pack(fill='both',expand=True)
 row=tk.Frame(right,bg=PANEL);row.pack(fill='x',padx=10,pady=10)
 d.entry=tk.Entry(row,bg='#11283a',fg=INK,insertbackground=INK,font=('Segoe UI',12),relief='flat');d.entry.pack(fill='x',ipady=9)
 d.entry.bind('<Return>',lambda e:d.submit(d.entry.get()))
 actions=tk.Frame(right,bg=PANEL);actions.pack(fill='x',padx=10,pady=(0,12))
 for label,fn in [('Send',lambda:d.submit(d.entry.get())),('Pause mic',d.toggle),('Snapshot',lambda:snapshot(d))]:
  tk.Button(actions,text=label,command=fn,bg='#16364b',fg=INK,activebackground='#24536c',relief='flat',padx=10,pady=8).pack(side='left',padx=3)
 d.status=tk.StringVar(value='Initializing Zoya…')
 tk.Label(right,textvariable=d.status,bg=PANEL,fg=ACCENT,font=('Segoe UI',10),wraplength=340,justify='left',anchor='w').pack(fill='x',padx=14,pady=10)
 center=tk.Frame(body,bg=BG);center.pack(side='left',fill='both',expand=True)
 upper=tk.Frame(center,bg=BG,height=320);upper.pack(fill='x');upper.pack_propagate(False)
 nodes=tk.Frame(upper,bg=BG,width=200);nodes.pack(side='left',fill='y');nodes.pack_propagate(False)
 tk.Label(nodes,text='NEURAL PIPELINE',bg=BG,fg=MUTED,font=('Consolas',10)).pack(pady=(12,20))
 for label,color in zip(('Memory','Skills','Voices','Settings'),COLORS):
  frame=tk.Frame(nodes,bg=color);frame.pack(fill='x',pady=6,padx=(0,8))
  tk.Button(frame,text=label.upper(),command=lambda n=label:show_node(d,n),bg='#102b3e',fg=color,activebackground='#173f55',activeforeground=INK,relief='flat',font=('Consolas',12,'bold'),anchor='w',padx=16,pady=8).pack(fill='x',padx=1,pady=1)
 d.canvas=tk.Canvas(upper,bg=BG,highlightthickness=0);d.canvas.pack(side='left',fill='both',expand=True)
 d.canvas.bind('<B1-Motion>',lambda e:setattr(d,'particle_drag',e.x*.008))
 d.canvas.bind('<Button-1>',lambda e:d.toggle())
 townhead=tk.Frame(center,bg=PANEL);townhead.pack(fill='x',pady=(12,0))
 tk.Label(townhead,text='AGENT TOWN  /  OPERATIONS FLOOR',bg=PANEL,fg=ACCENT,font=('Consolas',12,'bold')).pack(side='left',padx=12,pady=10)
 d.town_status=tk.StringVar(value='Loading queue evidence…');tk.Label(townhead,textvariable=d.town_status,bg=PANEL,fg=MUTED,font=('Consolas',9)).pack(side='right',padx=10)
 d.town=tk.Canvas(center,bg=PANEL,highlightbackground='#20394d',highlightthickness=1);d.town.pack(fill='both',expand=True)
 d.town.bind('<Configure>',lambda e:draw_town(d))
 d.root.bind('<Escape>',lambda e:d.root.attributes('-fullscreen',False))
 d.root.after(500,lambda:update_metrics(d));d.root.after(700,lambda:update_town(d))
def show_node(d,label):
 if label=='Voices':
  from voice_pipeline_hardening import MODEL_ID,VOICE_SETTINGS
  d.post('Voices',json.dumps({'model':MODEL_ID,'settings':VOICE_SETTINGS,'last_error':d.health.get('last_voice_error'),'stt':d.health.get('stt_backend','initializing'),'wake_latency':'not measured'},indent=2));return
 if label=='Settings':
  d.post('Settings','Local console. Publishing requires exact approval. Camera off. Amazon restricted actions manual. WhatsApp / Telegram command automation remains paused.');return
 def read():
  try:
   from zoya_desktop import http
   state=json.loads(http('/api/state'))
   if label=='Skills':d.post('Skills',json.dumps(state.get('agents',state.get('skills',{})),ensure_ascii=False)[:8000])
   else:
    path=ROOT/'data/zoya.sqlite3'
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
     tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
     counts={t:db.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in ('tasks','events','conversations','preferences','memory') if t in tables}
    d.post('Memory',json.dumps(counts)+' / persistent SQLite and existing Excel exports')
  except Exception as e:d.post(label,'Unavailable: '+type(e).__name__)
 threading.Thread(target=read,daemon=True).start()
def animate(d):
 d.phase+=.035;d.canvas.delete('all');w=max(300,d.canvas.winfo_width());h=max(250,d.canvas.winfo_height());cx,cy=w*.61,h*.49;r=min(w*.27,h*.32)
 state=d.health.get('voice_state','processing' if d.busy.is_set() else 'listening')
 if d.muted:state='paused'
 color={'listening':'#36def4','processing':'#ffb45e','speaking':'#61f5b6','paused':'#778ba5'}.get(state,ACCENT)
 for i,c in enumerate(COLORS):
  start=62+i*60;finish=cy+(i-1.5)*7
  d.canvas.create_line(0,start,w*.14,start,w*.27,finish,cx-r-8,finish,fill='#112b3b',width=7)
  d.canvas.create_line(0,start,w*.14,start,w*.27,finish,cx-r-8,finish,fill=c,width=2)
 for layer in range(9,0,-1):
  rr=r*.19+layer*4;d.canvas.create_oval(cx-rr,cy-rr,cx+rr,cy+rr,fill=('#092e40' if layer>5 else '#0b5361' if layer>2 else color),outline='')
 points=[];rotation=d.phase+d.particle_drag
 for i in range(240):
  yy=1-2*(i+.5)/240;rad=math.sqrt(1-yy*yy);angle=i*2.399963+rotation
  xx=rad*math.cos(angle);zz=rad*math.sin(angle)
  y=yy*math.cos(.28)+zz*math.sin(.28);z=zz*math.cos(.28)-yy*math.sin(.28)
  scale=1+.12*math.sin(d.phase*2) if state=='speaking' else 1
  px=cx+xx*r*scale;py=cy+y*r*scale;size=1.1+(z+1)*.9
  points.append((z,px,py,size))
 for z,px,py,size in sorted(points):
  d.canvas.create_oval(px-size,py-size,px+size,py+size,fill=color if z>0 else '#24566c',outline='')
 for orbit in range(3):
  coords=[]
  for j in range(73):
   a=j*math.tau/72;xx=math.cos(a)*r*1.18;yy=math.sin(a)*r*.32;tilt=orbit*math.pi/3+rotation*.2
   coords.extend((cx+xx*math.cos(tilt)-yy*math.sin(tilt),cy+xx*math.sin(tilt)+yy*math.cos(tilt)))
  d.canvas.create_line(*coords,fill='#286b80',width=1)
 d.canvas.create_text(cx,22,text=state.upper(),fill=color,font=('Consolas',12,'bold'))
 d.canvas.create_text(cx,h-22,text='HEY ZOYA  /  drag to rotate · click to pause mic',fill=MUTED,font=('Consolas',9))
 if not d.closed.is_set():d.root.after(40,d.animate)
def draw_town(d):
 d.town.delete('all');w=max(320,d.town.winfo_width());h=max(200,d.town.winfo_height())
 try:
  from PIL import Image,ImageTk
  image=Image.open(ROOT/'web/assets/agent-town.png');image=image.resize((w,h))
  d.town_photo=ImageTk.PhotoImage(image);d.town.create_image(0,0,image=d.town_photo,anchor='nw')
 except Exception:d.town.create_text(w/2,h/2,text='Office artwork unavailable',fill=MUTED)
 for i,role in enumerate(ROLES):
  col=i%4;row=i//4;x=(col+.5)*w/4;y=(row+.5)*h/2
  state=d.agent_states.get(role,'Idle');color='#60efb2' if state=='Working' else '#ffb45e' if state=='Review' else '#94b7ca'
  d.town.create_rectangle(x-80,y-23,x+80,y+23,fill='#091827',outline=color)
  d.town.create_text(x,y-8,text=role.upper(),fill=INK,font=('Consolas',10,'bold'))
  d.town.create_text(x,y+10,text=state,fill=color,font=('Consolas',9))
def update_town(d):
 states={r:'Idle' for r in ROLES};running=0;review=0
 try:
  with sqlite3.connect('file:'+str(ROOT/'data/desktop-queue.sqlite3')+'?mode=ro',uri=True,timeout=.2) as db:
   counts=dict(db.execute('SELECT status,count(*) FROM queue GROUP BY status'))
  running=counts.get('running',0);review=counts.get('needs_review',0)
  if running:states['Supervisor']='Working'
  elif review:states['Supervisor']='Review'
  path=ROOT/'data/zoya.sqlite3'
  with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.2) as db:
   columns={r[1] for r in db.execute('PRAGMA table_info(tasks)')}
   if {'agent','status'}<=columns:
    for role,status in db.execute("SELECT agent,status FROM tasks WHERE status IN ('running','blocked')"):
     if role in states:states[role]='Working' if status=='running' else 'Review'
  d.town_status.set(str(running)+' handoff(s) running / '+str(review)+' need review')
 except sqlite3.Error:d.town_status.set('Queue evidence temporarily unavailable')
 if states!=d.agent_states:d.agent_states=states;draw_town(d)
 if not d.closed.is_set():d.root.after(1500,lambda:update_town(d))
def update_metrics(d):
 values={}
 try:
  import psutil
  values={'CPU':psutil.cpu_percent(),'RAM':psutil.virtual_memory().percent}
 except Exception:pass
 d.vision_metrics=values
 age=int(time.monotonic()-d.vision_started);stt=d.health.get('stt_backend','initializing')
 d.metrics_bar.set(f"UPTIME {age//60:02}:{age%60:02}  /  CPU {values.get('CPU','?')}%  /  RAM {values.get('RAM','?')}%  /  STT {stt}  /  CAMERA OFF")
 if not d.closed.is_set():d.root.after(1500,lambda:update_metrics(d))
def toggle_chat(d):pass
def show_jobs(d):show_node(d,'Memory')
def snapshot(d):
 folder=ROOT/'data/dashboard-snapshots';folder.mkdir(parents=True,exist_ok=True)
 try:
  from PIL import ImageGrab
  d.root.update_idletasks();box=(d.root.winfo_rootx(),d.root.winfo_rooty(),d.root.winfo_rootx()+d.root.winfo_width(),d.root.winfo_rooty()+d.root.winfo_height())
  ImageGrab.grab(bbox=box).save(folder/('vision-'+time.strftime('%Y%m%d-%H%M%S')+'.png'));d.post('status','Dashboard snapshot saved')
 except Exception as e:d.post('status','Snapshot unavailable: '+type(e).__name__)
