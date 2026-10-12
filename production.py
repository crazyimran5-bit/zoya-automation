"""Two-channel production coordinator. Upload stays locked until live acceptance.
Text tasks use existing exact approvals. Media jobs have immutable approvals and
never replay uncertain paid calls. This module grants no model tool authority.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

CHANNELS = {'YT-1': {'name':'Mindset Forge','kids':False},
            'YT-3': {'name':'Little Wonder Trails','kids':True,'characters':['Armaan','Ismat']}}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def scoped(root, name):
    if not isinstance(name,str) or Path(name).is_absolute(): raise ValueError('Relative workspace path required')
    root=Path(root).resolve(); path=(root/name).resolve()
    if root not in path.parents: raise ValueError('Outside production workspace')
    return path

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def readiness(root, profiles):
    root=Path(root)
    provider=os.environ.get('ZOYA_TEXT_PROVIDER','claude')
    from claude_adapter import ClaudeAdapter
    from ollama_adapter import OllamaAdapter
    text=(OllamaAdapter() if provider=='ollama' else ClaudeAdapter()).ready()
    checks=[{'name':'Text provider configuration','ready':bool(text),'live_verified':False},
            {'name':'FFmpeg + ffprobe installed','ready':bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),'live_verified':False},
            {'name':'Natural narration configuration','ready':all(os.environ.get(k) for k in ('ELEVENLABS_API_KEY','NARRATION_VOICE_ID','NARRATION_VOICE_MODEL_ID')),'live_verified':False},
            {'name':'Zoya conversational voice configuration','ready':all(os.environ.get(k) for k in ('ELEVENLABS_API_KEY','ZOYA_VOICE_ID','ZOYA_VOICE_MODEL_ID')),'live_verified':False}]
    for key, spec in CHANNELS.items():
        p=profiles.get(key,{})
        checks.append({'name':spec['name']+' actual YouTube channel','ready':bool(p.get('verified') and p.get('account_id')),'live_verified':False})
    checks.append({'name':'YouTube startup acceptance','ready':False,'live_verified':False})
    return {'channels':CHANNELS,'checks':checks,'upload_locked':True,
            'reason':'Owner requires full hands-free startup test before any upload. No automatic unlock.',
            'configured_is_not_tested':True}

class ProductionCoordinator:
    def __init__(self, engine):
        self.engine=engine; self.root=engine.root; self.lock=threading.RLock()
        with engine.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS production_jobs(
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE, channel TEXT,
                brief TEXT, steps TEXT, media_plan TEXT, media_hash TEXT,
                status TEXT, output TEXT, created REAL)''')
            c.execute('CREATE TABLE IF NOT EXISTS production_artifacts(job_id TEXT PRIMARY KEY,voice_hash TEXT,path TEXT,sha256 TEXT)')
            c.execute("UPDATE production_jobs SET status='blocked',output='Interrupted media job; inspect paid provider ledger before a new approval.' WHERE status IN ('queued','running')")

    def jobs(self):
        with self.engine.db() as c:
            records=[dict(row) for row in c.execute('SELECT * FROM production_jobs ORDER BY created DESC LIMIT 30')]
        state={t['id']:t for t in self.engine.state()['tasks']}
        for row in records:
            row['steps']=json.loads(row['steps']); row['media_plan']=json.loads(row['media_plan']) if row['media_plan'] else None
            row['stage_states']={name:state.get(tid,{}).get('status','unknown') for name,tid in row['steps'].items()}
            row['upload_locked']=True
        return records

    def create(self, channel, brief, request_id):
        if channel not in CHANNELS: raise ValueError('Only Mindset Forge and Little Wonder Trails are enabled for production')
        if not isinstance(brief,str) or not 1<=len(brief)<=3000: raise ValueError('Brief required, maximum 3000 characters')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=50: raise ValueError('Production request ID required')
        with self.lock:
            with self.engine.db() as c:
                old=c.execute('SELECT * FROM production_jobs WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['brief']!=brief or old['channel']!=channel: raise ValueError('Request ID reused with changed brief')
                    return dict(old)
            spec=CHANNELS[channel]
            instruction=(brief+'\nOriginal content only. Use supplied dated evidence; do not invent market data. '
                +'Audience: '+spec['name']+'. Video narration in en-US. '
                +'Script agent: return only a JSON object with narration (string), scenes (array of visual briefs), '
                +'title (string), description (string), tags (array of strings). Packaging/creative/quality agents review that script. '
                +'No upload, no generated claim of finished video.')
            from research_blueprint import TEMPLATE
            instruction+=' Research role only: return JSON matching '+json.dumps(TEMPLATE)+'. NUMBER and STRING are type placeholders, never output values. Outlier score and subscriber count must come from supplied evidence. If missing, report the exact blocker instead of fabricating JSON numbers.'
            if spec['kids']:
                instruction+=' Preschool learning: original recurring characters Armaan and Ismat. Gentle pacing, child-safe original stories; made_for_kids=true. No copying competitor characters.'
            result=self.engine.workflow(instruction,channel,'prod:'+request_id)
            steps=result['steps']
            for name,skill,deps in [('audience','metrics-review',['quality']),('cost','finance-review',['creative']),('review','problem-resolver',['quality','creative','audience','cost'])]:
                task=self.engine.submit('/'+skill+' '+instruction,channel,'prod:'+request_id+':'+name,
                    dependencies=[steps[d] for d in deps])
                steps[name]=task['id']
            job_id=str(uuid.uuid4())
            with self.engine.db() as c:
                c.execute('INSERT INTO production_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (job_id,request_id,channel,brief,json.dumps(steps),'','','drafting','',time.time()))
            self.engine.event(job_id,'production_created','Two-channel production DAG created; no provider execution before exact approval.')
            return {'id':job_id,'channel':channel,'steps':steps,'upload_locked':True}

    def _job(self, job_id):
        with self.engine.db() as c: row=c.execute('SELECT * FROM production_jobs WHERE id=?',(job_id,)).fetchone()
        if not row: raise ValueError('Unknown production job')
        return dict(row)

    def plan_media(self, job_id, clips, subtitles=None):
        with self.lock:
            job=self._job(job_id)
            if job['status'] in ('running','queued','media_ready'): raise ValueError('Media job already queued or completed')
            steps=json.loads(job['steps'])
            with self.engine.db() as c:
                tasks={name:c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone() for name,tid in steps.items()}
            if any(not t or t['status']!='completed' for t in tasks.values()): raise ValueError('All script, packaging, creative and review stages must complete before media approval')
            from research_blueprint import validate as validate_research
            try: validate_research(json.loads(tasks['survey']['output']))
            except (ValueError,TypeError): raise ValueError('Outlier research incomplete; supply actual evidence before production')
            try:
                raw=tasks['script']['output']
                for provider in ('claude','ollama'):
                    suffix='\n\nExecution: '+provider+' draft returned. Nothing published or changed on accounts.'
                    if raw.endswith(suffix): raw=raw[:-len(suffix)]; break
                script=json.loads(raw)
            except (ValueError,TypeError): raise ValueError('Script must be reviewed structured JSON; no automatic repair or paid retry')
            narration=script.get('narration') if isinstance(script,dict) else None
            if not isinstance(narration,str) or not 1<=len(narration)<=4000: raise ValueError('Script narration missing or too long')
            if not isinstance(clips,list) or not 1<=len(clips)<=40: raise ValueError('Supply 1–40 reviewed visual clips')
            channel_root=self.root/'workspaces'/job['channel']
            inputs=[]
            for clip in clips:
                p=scoped(channel_root,clip)
                if not p.is_file(): raise ValueError('Visual clip missing')
                inputs.append({'path':clip,'sha256':file_hash(p)})
            subtitle=None
            if subtitles:
                p=scoped(channel_root,subtitles)
                if not p.is_file(): raise ValueError('Subtitle file missing')
                subtitle={'path':subtitles,'sha256':file_hash(p)}
            from natural_voice import VoiceConfig, ElevenLabsVoice
            voice=ElevenLabsVoice(channel_root,VoiceConfig.from_env('narration'))
            voice_plan=voice.plan(narration,job_id+'.mp3')
            plan={'job_id':job_id,'channel':job['channel'],'target':dict(self.engine.profiles[job['channel']]),
                  'clips':inputs,'subtitles':subtitle,'narration':narration,'voice_plan':voice_plan,
                  'output':'outbox/'+job_id+'.mp4','made_for_kids':CHANNELS[job['channel']]['kids'],
                  'model_paid':True,'upload_allowed':False,'expires':time.time()+3600,
                  'scope':'One approved TTS call + local FFmpeg edit/render/QA. No uploads.'}
            key=fingerprint(plan)
            with self.engine.db() as c:
                c.execute("UPDATE production_jobs SET media_plan=?,media_hash=?,status='awaiting_media_approval' WHERE id=?",(json.dumps(plan),key,job_id))
            return {'id':job_id,'hash':key,'plan':plan}

    def approve_media(self, job_id, expected_hash):
        with self.lock, self.engine.db() as c:
            row=c.execute('SELECT * FROM production_jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['status']!='awaiting_media_approval': raise ValueError('Media proposal not awaiting approval')
            plan=json.loads(row['media_plan'])
            if expected_hash!=row['media_hash'] or fingerprint(plan)!=expected_hash or time.time()>plan['expires']: raise ValueError('Media approval changed or expired')
            if plan['target']!=self.engine.profiles[row['channel']]: raise ValueError('Channel target changed')
            if os.environ.get('ZOYA_ALLOW_PAID','0')!='1': raise ValueError('Paid TTS disabled; configure locally before approval')
            c.execute("UPDATE production_jobs SET status='queued' WHERE id=?",(job_id,))
        self.engine.event(job_id,'media_approved','Voice/render approved; upload remains locked')
        return {'id':job_id,'status':'queued','upload_locked':True}

    def cancel(self, job_id):
        with self.lock, self.engine.db() as c:
            c.execute("UPDATE production_jobs SET status='cancelled',output='Cancelled; an in-flight provider request may still incur usage. No upload.' WHERE id=? AND status NOT IN ('media_ready','cancelled')",(job_id,))
        return {'id':job_id,'status':'cancelled'}

    def tick(self):
        if self.engine.state()['paused']: return False
        with self.lock, self.engine.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT * FROM production_jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:return False
            job=dict(row)
            c.execute("UPDATE production_jobs SET status='running' WHERE id=?",(job['id'],))
        try:
            plan=json.loads(job['media_plan'])
            if fingerprint(plan)!=job['media_hash'] or time.time()>plan['expires']: raise ValueError('Expired or modified media approval')
            if plan['target']!=self.engine.profiles[job['channel']]: raise ValueError('Target changed')
            root=self.root/'workspaces'/job['channel']
            for item in plan['clips']+([plan['subtitles']] if plan['subtitles'] else []):
                if file_hash(scoped(root,item['path']))!=item['sha256']: raise ValueError('Approved visual or subtitle changed')
            from natural_voice import VoiceConfig, ElevenLabsVoice
            voice=ElevenLabsVoice(root,VoiceConfig.from_env('narration'))
            if voice.plan(plan['narration'],job['id']+'.mp3')!=plan['voice_plan']: raise ValueError('Voice settings changed')
            with self.engine.db() as c:c.execute("UPDATE production_jobs SET output='Natural narration running' WHERE id=?",(job['id'],))
            self.engine.event(job['id'],'voice_running','Approved natural narration generation started')
            with self.engine.db() as c:
                cached=c.execute('SELECT * FROM production_artifacts WHERE job_id=?',(job['id'],)).fetchone()
            if cached:
                if cached['voice_hash']!=plan['voice_plan']['hash'] or file_hash(scoped(root,cached['path']))!=cached['sha256']:
                    raise ValueError('Cached paid narration changed; review required')
                audio={'relative_path':cached['path']}
            else:
                audio=voice.generate(plan['narration'],job['id']+'.mp3',plan['voice_plan']['hash'])
                audio_path=scoped(root,audio['relative_path'])
                with self.engine.db() as c:
                    c.execute('INSERT INTO production_artifacts VALUES(?,?,?,?)',(job['id'],plan['voice_plan']['hash'],audio['relative_path'],file_hash(audio_path)))
            if self._job(job['id'])['status']=='cancelled': return True
            from video_render import render_video
            with self.engine.db() as c:c.execute("UPDATE production_jobs SET output='Video editing and rendering running' WHERE id=?",(job['id'],))
            self.engine.event(job['id'],'render_running','Local editing/rendering and technical QA started')
            result=render_video(root,[item['path'] for item in plan['clips']],audio['relative_path'],plan['output'],
                approved=True,subtitles=plan['subtitles']['path'] if plan['subtitles'] else None)
            if not result['qa']['passed']: raise ValueError('Video failed technical QA')
            status='media_ready'; output=json.dumps(result)
        except Exception as error:
            status='blocked';output='Stage stopped: '+type(error).__name__+'. Inspect local stage/provider logs; no automatic paid retry or upload.'
        with self.engine.db() as c:
            c.execute("UPDATE production_jobs SET status=?,output=? WHERE id=? AND status='running'",(status,output,job['id']))
        self.engine.event(job['id'],'production_'+status,output)
        return True
