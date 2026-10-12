"""Zoya foundation: local-only command centre, durable tasks and reviewed skill tools.
No third-party dependencies; no arbitrary shell execution or live publishing.
"""
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor/youtube-agent-skill'
SKILLS = sorted(p.parent.name for p in (VENDOR / 'skills').glob('*/SKILL.md'))
AGENTS = ['Research', 'Script', 'Packaging', 'Editing', 'Quality', 'Publisher', 'Analytics', 'Supervisor']


class Engine:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.lock = threading.RLock()
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, request_id TEXT UNIQUE, channel TEXT, agent TEXT,
              command TEXT, status TEXT, output TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY, paused INTEGER);
            INSERT OR IGNORE INTO control VALUES(1,0);
            ''')
            c.execute("UPDATE tasks SET status='blocked',output='Interrupted by restart; inspect before retry.' WHERE status='running'")

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def state(self):
        with self.lock, self.db() as c:
            return {'tasks': [dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY created DESC LIMIT 100')],
                    'memory': dict(c.execute('SELECT key,value FROM memory')),
                    'paused': bool(c.execute('SELECT paused FROM control').fetchone()[0]),
                    'skills': SKILLS, 'agents': AGENTS,
                    'mode': 'Local foundation — publishing disconnected',
                    'channels': ['YT-'+str(i) for i in range(1,7)] + ['Facebook'],
                    'connections': {'Skales': 'source required', 'YouTube': 'not configured',
                        'Facebook': 'not configured', 'Make': 'not configured', 'AI': 'not configured'}}

    def pause(self, value):
        with self.lock, self.db() as c:
            c.execute('UPDATE control SET paused=? WHERE id=1', (int(value),))

    def cancel(self, task_id):
        with self.lock, self.db() as c:
            # Running offline tools finish their bounded local calculation; result is discarded.
            c.execute("UPDATE tasks SET status='cancelled',output='Cancelled by user' WHERE id=? AND status IN ('queued','running','blocked','awaiting_approval')", (task_id,))

    def submit(self, command, channel, request_id):
        if not isinstance(command,str) or not command.strip() or len(command)>6000:
            raise ValueError('Command must contain 1–6000 characters')
        if channel not in self.state()['channels']:
            raise ValueError('Unknown channel')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100:
            raise ValueError('request_id required')
        command = command.strip()
        agent = 'Packaging' if command.startswith('/title ') else 'Script' if command.startswith('/hook ') else 'Supervisor'
        with self.lock, self.db() as c:
            old = c.execute('SELECT * FROM tasks WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['command']!=command or old['channel']!=channel:
                    raise ValueError('request_id reused with a different payload')
                return dict(old)
            tid = str(uuid.uuid4())
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',
                (tid,request_id,channel,agent,command,'queued','',time.time()))
            return dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())

    def execute(self, task):
        text = task['command']
        if text.startswith('/remember '):
            body = text[len('/remember '):]
            if '=' not in body:
                raise ValueError('Use /remember key=value')
            key,value=body.split('=',1)
            if not key.strip() or len(key)>100:
                raise ValueError('Memory key required (up to 100 characters)')
            with self.db() as c:
                c.execute('INSERT OR REPLACE INTO memory VALUES(?,?)',(key.strip(),value.strip()))
            return 'completed','Preference saved. Do not store credentials here.'
        if text == '/status':
            return 'completed',json.dumps(self.state()['connections'],indent=2)
        if text.startswith('/hook '):
            args=[sys.executable,str(VENDOR/'skills/yt-script/hookscore.py'),'--hook',text[6:],'--json']
        elif text.startswith('/title '):
            args=[sys.executable,str(VENDOR/'skills/yt-package/title.py'),'--title',text[7:],'--json']
        elif text.startswith('/yt-'):
            skill = text.split()[0][1:]
            if skill not in SKILLS:
                raise ValueError('Unknown skill')
            instructions=(VENDOR/'skills'/skill/'SKILL.md').read_text(encoding='utf-8')
            return 'blocked', 'Skill loaded; language-model adapter is not configured. No content generated.\n\n'+instructions
        elif text.startswith('/publish '):
            return 'blocked','Publishing adapter and account authorization are not configured. Nothing posted.'
        else:
            return 'blocked','Zoya language-model planner not configured. Available local commands: /hook text, /title text, /remember key=value, /status, /yt-script topic. No external action executed.'
        proc=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=15,cwd=VENDOR,shell=False)
        if proc.returncode:
            raise ValueError(proc.stderr[:1000] or proc.stdout[:1000] or 'Skill tool failed')
        return 'completed',proc.stdout[:20000]

    def tick(self):
        with self.lock, self.db() as c:
            if c.execute('SELECT paused FROM control').fetchone()[0]:
                return False
            task=c.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not task:
                return False
            c.execute("UPDATE tasks SET status='running' WHERE id=? AND status='queued'",(task['id'],))
        try:
            status,output=self.execute(dict(task))
        except Exception as e:
            status,output='failed',str(e)[:2000]
        with self.lock, self.db() as c:
            c.execute("UPDATE tasks SET status=?,output=? WHERE id=? AND status='running'",(status,output,task['id']))
        return True


def main():
    port=int(os.environ.get('ZOYA_PORT','8765'))
    data=ROOT/'data'
    data.mkdir(exist_ok=True)
    from runtime import SafeEngine
    # Hold an OS lock so a second server cannot reset this instance's running tasks.
    instance_lock=(data/'server.lock').open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            instance_lock.seek(0);instance_lock.write(b'0');instance_lock.flush();instance_lock.seek(0)
            msvcrt.locking(instance_lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(instance_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('Zoya already running; open its existing dashboard.')
    from local_settings import load as load_settings, save as save_settings
    load_settings(ROOT)
    engine=SafeEngine(data/'zoya.sqlite3',root=ROOT)
    from production import ProductionCoordinator, readiness as production_readiness
    production=ProductionCoordinator(engine)
    from voice_session import VoiceSession
    voice_session=VoiceSession(engine)
    from conversation import Conversation
    conversation=Conversation(engine,voice_session)
    token=secrets.token_urlsafe(32)
    origin=f'http://127.0.0.1:{port}'
    shutdown=threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def send(self,status,body,kind='application/json'):
            if kind=='application/json':
                body=json.dumps(body).encode()
            elif isinstance(body,str):
                body=body.encode()
            self.send_response(status)
            self.send_header('Content-Type',kind+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            return self.headers.get('Host')==f'127.0.0.1:{port}'

        def do_GET(self):
            if not self.trusted_host():
                return self.send(403,{'error':'Local access only'})
            path=urlsplit(self.path).path
            if path=='/assets/AmiriQuran-Regular.ttf':
                return self.send(200,(ROOT/'web/assets/AmiriQuran-Regular.ttf').read_bytes(),'font/ttf')
            if path=='/':
                html=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('__TOKEN__',token)
                return self.send(200,html,'text/html')
            if self.headers.get('X-Zoya-Token')!=token:
                return self.send(403,{'error':'Invalid session'})
            if path=='/api/voice/audio':
                try:
                    job_id=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                    return self.send(200,voice_session.audio(job_id),'audio/mpeg')
                except ValueError:
                    return self.send(404,{'error':'Voice audio unavailable'})
            if path=='/api/production':
                return self.send(200,{'jobs':production.jobs(),'readiness':production_readiness(ROOT,engine.profiles)})
            if path=='/api/state':
                current=engine.state();current['production_jobs']=production.jobs()
                return self.send(200,current)
            return self.send(404,{'error':'Not found'})

        def do_POST(self):
            if not self.trusted_host() or self.headers.get('Origin')!=origin or self.headers.get('X-Zoya-Token')!=token:
                return self.send(403,{'error':'Invalid local session'})
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=20000:
                    raise ValueError('Invalid request size')
                p=json.loads(self.rfile.read(n))
                path=urlsplit(self.path).path
                if path=='/api/conversation':
                    result=conversation.reply(p.get('message'),p.get('request_id'),p.get('voice',False))
                elif path=='/api/setup/providers':
                    result=save_settings(ROOT,p)
                elif path=='/api/voice/plan':
                    result=voice_session.plan()
                elif path=='/api/voice/approve':
                    result=voice_session.approve(p.get('id'),p.get('hash'))
                elif path=='/api/production/create':
                    result=production.create(p.get('channel'),p.get('brief'),p.get('request_id'))
                elif path=='/api/production/cancel':
                    result=production.cancel(p.get('id'))
                elif path=='/api/production/media-plan':
                    result=production.plan_media(p.get('id'),p.get('clips'),p.get('subtitles'))
                elif path=='/api/production/media-approve':
                    result=production.approve_media(p.get('id'),p.get('hash'))
                elif path=='/api/task':
                    result=engine.submit(p.get('command'),p.get('channel'),p.get('request_id'))
                elif path=='/api/workflow':
                    result=engine.workflow(p.get('command'),p.get('channel'),p.get('request_id'))
                elif path=='/api/approval':
                    result=engine.approve(p.get('id'),p.get('hash'),p.get('decision'))
                elif path=='/api/export':
                    engine.export_sheets(); result={'ok':not bool(engine.log_error),'message':engine.log_error or engine.state()['sheet_path']}
                elif path=='/api/control':
                    if p.get('action') not in ('pause','resume'):
                        raise ValueError('Invalid action')
                    engine.pause(p['action']=='pause')
                    result={'ok':True}
                elif path=='/api/cancel':
                    engine.cancel(p.get('id'))
                    result={'ok':True}
                elif path=='/api/forget':
                    raise ValueError('Personal memory deletion requires a /forget task and exact approval')
                else:
                    return self.send(404,{'error':'Not found'})
                return self.send(200,result)
            except (ValueError,TypeError,sqlite3.Error) as e:
                return self.send(400,{'error':str(e)})

    def worker(agent):
        while not shutdown.is_set():
            try:
                engine.tick(agent=agent)
            except Exception as error:
                engine.pause(True)
                engine.event('', 'worker_fault', 'Worker '+agent+' paused queue; inspect failure: '+type(error).__name__)
                shutdown.wait(2)
            heartbeats[agent]=time.time()
            shutdown.wait(0.3)

    def media_worker():
        while not shutdown.is_set():
            try:
                production.tick()
            except Exception as error:
                engine.pause(True)
                engine.event('', 'production_fault', type(error).__name__)
            shutdown.wait(1)

    threading.Thread(target=media_worker,name='Zoya-Production',daemon=True).start()
    heartbeats={}
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    from business_core import SPECIALISTS
    for agent in [*SPECIALISTS, 'Zoya']:
        threading.Thread(target=worker,args=(agent,),name='Zoya-'+agent,daemon=True).start()
    from local_monitor import monitor_loop
    threading.Thread(target=monitor_loop,args=(engine,heartbeats,shutdown),daemon=True,name='Zoya-Local-Monitor').start()
    print('Zoya v0.7: '+origin+' — every action requires exact approval; publishing disconnected.')
    if '--no-browser' not in sys.argv:
        webbrowser.open(origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown.set()
        server.server_close()


if __name__=='__main__':
    main()
