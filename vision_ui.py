"""Native Zoya vision dashboard. Real metrics; unavailable sensors stay unavailable."""
import json, math, time, tkinter as tk
from pathlib import Path
from tkinter.scrolledtext import ScrolledText
BG='#07121b'; PANEL='#102532'; INK='#ecf8ff'; ACCENT='#51e0d3'; MUTED='#aac3cf'

def setup(d):
    d.root=tk.Tk();d.root.title('ZOYA · Vision & Workflow Console');d.root.geometry('1360x850');d.root.minsize(1050,700);d.root.configure(bg=BG)
    d.root.protocol('WM_DELETE_WINDOW',d.close)
    d.root.report_callback_exception=lambda kind,value,tb:d.post('status','UI exception: '+type(value).__name__)
    d.vision_started=time.monotonic();d.chat_visible=True;d.vision_metrics={};d.network_previous=None
    head=tk.Frame(d.root,bg=BG);head.pack(fill='x',padx=24,pady=15)
    tk.Label(head,text='Z O Y A   /   VISION CONSOLE',fg=INK,bg=BG,font=('Segoe UI',22,'bold')).pack(side='left')
    tk.Label(head,text='LOCAL FIRST  ·  UPLOAD LOCKED',fg=ACCENT,bg=BG,font=('Segoe UI',11,'bold')).pack(side='right')
    d.metrics_bar=tk.StringVar(value='Initializing measured system metrics…')
    tk.Label(d.root,textvariable=d.metrics_bar,bg=PANEL,fg=MUTED,font=('Consolas',10),anchor='w').pack(fill='x',padx=20)
    upper=tk.Frame(d.root,bg=BG);upper.pack(fill='both',expand=True,padx=20,pady=12)
    left=tk.Frame(upper,bg=PANEL,width=250);left.pack(side='left',fill='y');left.pack_propagate(False)
    tk.Label(left,text='LIVE SYSTEM METRICS',bg=PANEL,fg=ACCENT,font=('Segoe UI',13,'bold')).pack(pady=14)
    d.metric_vars={}
    for key in ('CPU','RAM','GPU temperature','Battery','Camera','Storage','Network','Top processes'):
        tk.Label(left,text=key.upper(),fg=MUTED,bg=PANEL,font=('Segoe UI',9,'bold'),anchor='w').pack(fill='x',padx=14,pady=(12,2))
        v=tk.StringVar(value='Reading…');d.metric_vars[key]=v
        tk.Label(left,textvariable=v,fg=INK,bg=PANEL,font=('Consolas',10),anchor='w',justify='left',wraplength=215).pack(fill='x',padx=14)
    right=tk.Frame(upper,bg=PANEL,width=230);right.pack(side='right',fill='y');right.pack_propagate(False)
    tk.Label(right,text='QUICK ACTIONS',bg=PANEL,fg=ACCENT,font=('Segoe UI',13,'bold')).pack(pady=14)
    def button(label,fn):tk.Button(right,text=label,command=fn,bg='#1c4050',fg=INK,activebackground='#2d5a69',activeforeground=INK,relief='flat',font=('Segoe UI',11),padx=10,pady=10).pack(fill='x',padx=14,pady=5)
    button('Snapshot dashboard',lambda:snapshot(d));button('HUD / Full screen',lambda:d.root.attributes('-fullscreen',not bool(d.root.attributes('-fullscreen'))))
    button('AI chat toggle',lambda:toggle_chat(d));button('Pause / Resume mic',d.toggle);button('Render / Worker status',lambda:show_jobs(d))
    button('Preview offline voice',d.preview);button('Select offline voice',d.choose)
    d.root.bind('<Escape>',lambda e:d.root.attributes('-fullscreen',False))
    center=tk.Frame(upper,bg=BG);center.pack(side='left',fill='both',expand=True,padx=12)
    d.canvas=tk.Canvas(center,bg=BG,highlightthickness=0);d.canvas.pack(fill='both',expand=True)
    d.status=tk.StringVar(value='Initializing Zoya…')
    tk.Label(d.root,textvariable=d.status,bg=BG,fg=ACCENT,font=('Segoe UI',11)).pack(fill='x',padx=24,pady=4)
    d.chat_frame=tk.Frame(d.root,bg=BG);d.chat_frame.pack(fill='x',padx=20,pady=8)
    d.feed=ScrolledText(d.chat_frame,bg=PANEL,fg=INK,insertbackground=INK,font=('Segoe UI',11),wrap='word',height=8);d.feed.pack(fill='both',expand=True)
    row=tk.Frame(d.root,bg=BG);row.pack(fill='x',padx=20,pady=(2,14));d.entry=tk.Entry(row,bg='#153343',fg=INK,insertbackground=INK,font=('Segoe UI',12));d.entry.pack(side='left',fill='x',expand=True,ipady=8)
    d.entry.bind('<Return>',lambda e:d.submit(d.entry.get()))
    tk.Button(row,text='Send typed command',command=lambda:d.submit(d.entry.get()),bg='#245263',fg=INK,relief='flat',padx=12,pady=8).pack(side='right',padx=8)
    d.root.after(500,lambda:update_metrics(d))

def animate(d):
    d.phase+=.07;d.canvas.delete('all');w=max(320,d.canvas.winfo_width());h=max(260,d.canvas.winfo_height());x,y=w/2,h/2-18
    level=min(1,max(0,d.health.get('mic_level',0)));active=d.busy.is_set()
    radius=min(w,h)*.25
    for n in range(4):
        r=radius+10*n+math.sin(d.phase+n)*(3+level*15)
        d.canvas.create_oval(x-r,y-r,x+r,y+r,outline=ACCENT if n%2==0 else '#235d71',width=2)
    d.canvas.create_arc(x-radius-45,y-radius-45,x+radius+45,y+radius+45,start=d.phase*18%360,extent=100,outline='#87c9f8',style='arc',width=3)
    # Stylized vector avatar; no claim this is a camera or a human face stream.
    d.canvas.create_oval(x-48,y-62,x+48,y+62,fill='#153745',outline='#91deeb',width=2)
    for eye in (-20,20):d.canvas.create_oval(x+eye-6,y-12,x+eye+6,y-3,fill=ACCENT,outline='')
    d.canvas.create_arc(x-21,y+13,x+21,y+39,start=190,extent=160,style='arc',outline=INK,width=2)
    base=y+radius+65
    for i in range(29):
        amp=(3+level*42)*abs(math.sin(i*.7+d.phase))
        if active:amp=5+8*abs(math.sin(i*.7+d.phase))
        px=x+(i-14)*8;d.canvas.create_line(px,base-amp,px,base+amp,fill=ACCENT,width=3)
    state='WORKING / SPEAKING' if active else 'MIC PAUSED' if d.muted else 'LISTENING'
    d.canvas.create_text(x,y-radius-68,text=state,fill=ACCENT,font=('Segoe UI',12,'bold'))
    d.canvas.create_text(x,base+50,text='Zoya · local controller\nTool completion requires evidence',fill=MUTED,font=('Segoe UI',10),justify='center')
    if not d.closed.is_set():d.root.after(50,d.animate)

def update_metrics(d):
    values={'GPU temperature':'Not exposed by Windows sensor API','Camera':'Disabled · no camera access'}
    try:
        import psutil
        cpu=psutil.cpu_percent();ram=psutil.virtual_memory();disk=psutil.disk_usage(str(Path.home().anchor));battery=psutil.sensors_battery();net=psutil.net_io_counters();now=time.monotonic()
        values.update(CPU=f'{cpu:.1f}%',RAM=f'{ram.percent:.1f}% · {ram.used/2**30:.1f}/{ram.total/2**30:.1f} GiB',Storage=f'{disk.free/2**30:.1f} GiB free',Battery='Not reported' if battery is None else f'{battery.percent:.0f}% · '+('Charging / AC' if battery.power_plugged else 'Battery'))
        if d.network_previous:
            stamp,recv,sent=d.network_previous;dt=max(.1,now-stamp)
            values['Network']=f'Down {(net.bytes_recv-recv)/dt/1024:.1f} KiB/s\nUp {(net.bytes_sent-sent)/dt/1024:.1f} KiB/s'
        else:values['Network']='Measuring…'
        d.network_previous=(now,net.bytes_recv,net.bytes_sent)
        processes=[]
        for p in psutil.process_iter(['name','memory_info']):
            try:processes.append((p.info['memory_info'].rss,p.info['name']))
            except (psutil.Error,TypeError,AttributeError):pass
        values['Top processes']='\n'.join(f'{name[:18]} {size/2**20:.0f} MiB' for size,name in sorted(processes,reverse=True)[:4])
    except ImportError:
        for key in ('CPU','RAM','Storage','Battery','Network','Top processes'):values[key]='psutil dependency unavailable'
    except Exception as e:values['CPU']='Metrics temporarily unavailable: '+type(e).__name__
    d.vision_metrics=values
    for key,value in values.items():d.metric_vars[key].set(value)
    age=int(time.monotonic()-d.vision_started);d.metrics_bar.set(f'CONSOLE UPTIME {age//3600:02}:{age//60%60:02}:{age%60:02}  |  WEATHER: location/API not configured  |  CAMERA OFF  |  NO AUTO-UPLOADS')
    if not d.closed.is_set():d.root.after(1500,lambda:update_metrics(d))

def toggle_chat(d):
    d.chat_visible=not d.chat_visible
    if d.chat_visible:d.chat_frame.pack(fill='x',padx=20,pady=8,before=d.entry.master)
    else:d.chat_frame.pack_forget()

def show_jobs(d):
    try:
        from promo_workflow import list_jobs
        jobs=list_jobs();d.post('Workers',json.dumps(jobs,ensure_ascii=False))
    except Exception as e:d.post('status','Worker status unavailable: '+type(e).__name__)

def snapshot(d):
    folder=Path(__file__).resolve().parent/'data/dashboard-snapshots';folder.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime('%Y%m%d-%H%M%S');(folder/(stamp+'.json')).write_text(json.dumps(d.vision_metrics,indent=2))
    try:
        from PIL import ImageGrab
        d.root.update_idletasks();box=(d.root.winfo_rootx(),d.root.winfo_rooty(),d.root.winfo_rootx()+d.root.winfo_width(),d.root.winfo_rooty()+d.root.winfo_height())
        ImageGrab.grab(bbox=box).save(folder/(stamp+'.png'));d.post('status','Dashboard snapshot saved locally')
    except Exception:d.post('status','Metrics snapshot saved; image capture unavailable')
