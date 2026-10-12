"""Scoped drafting orchestration. External publishing and computer automation are absent."""
import csv
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from foundation import Engine as BaseEngine
from agents import ROOT, SKILL_ROLES, instructions
from claude_adapter import ClaudeAdapter
from business_core import SPECIALISTS, worker_limits, workflow_specs
from access_control import RESTRICTED, capability, role_for, validate_command

TOOLS = {'dead-air':('yt-edit/deadair.py',None), 'chapters':('yt-chapters/chapters.py',None),
         'retention':('yt-retention/retention.py',None),'viral':('yt-viral/swipe.py','--min')}
SECRET = re.compile(r'(?i)(sk-[a-z0-9_-]{12,}|(?:api[_ -]?key|password|access[_ -]?token|secret)\s*[:=]\s*\S+)')
def clean(value):
    return SECRET.sub('[REDACTED]',str(value))
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def safe_path(root, relative):
    base=Path(root).resolve()
    candidate=(base/relative).resolve()
    if candidate==base or base not in candidate.parents: raise ValueError('Path outside assigned workspace')
    return candidate

class SafeEngine(BaseEngine):
    def __init__(self, db_path, root=None, adapter=None):
        super().__init__(db_path)
        self.root=Path(root or ROOT).resolve()
        from budget_guard import load_policy
        load_policy(self.root)
        provider='ollama' if os.environ.get('ZOYA_FREE_MODE')=='1' else os.environ.get('ZOYA_TEXT_PROVIDER','claude')
        if provider not in ('claude','ollama'): raise ValueError('Unsupported text provider; no automatic fallback')
        from ollama_adapter import OllamaAdapter
        self.adapter=adapter or (OllamaAdapter() if provider=='ollama' else ClaudeAdapter())
        self.profiles=json.loads((ROOT/'config/profiles.json').read_text(encoding='utf-8'))
        self.max_inflight=worker_limits()
        self.log_error='';self.monitor_state={"status":"not checked"}
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS plans(task_id TEXT PRIMARY KEY, snapshot TEXT, hash TEXT,
              expires REAL, approved INTEGER DEFAULT 0, consumed INTEGER DEFAULT 0,
              parent_id TEXT, dependencies TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
              time REAL, task_id TEXT, kind TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS conversations(seq INTEGER PRIMARY KEY AUTOINCREMENT,
              time REAL, channel TEXT, role TEXT, task_id TEXT, text TEXT);
            CREATE TABLE IF NOT EXISTS usage(task_id TEXT PRIMARY KEY, day TEXT, status TEXT,
              input_tokens INTEGER, output_tokens INTEGER, provider_id TEXT);
            ''')
        self.event('', 'startup', 'Interrupted running tasks are blocked; no uncertain calls replayed.')
        self.export_sheets()

    def event(self, tid, kind, detail, c=None):
        if c is not None:
            c.execute('INSERT INTO events(time,task_id,kind,detail) VALUES(?,?,?,?)',
                      (time.time(),tid,kind,clean(detail)))
        else:
            with self.db() as conn: self.event(tid,kind,detail,conn)

    def state(self):
        with self.lock:
            s=super().state()
            with self.db() as c:
                s['plans']={r['task_id']:dict(r) for r in c.execute('SELECT * FROM plans')}
                for p in s['plans'].values():
                    p['snapshot']=json.loads(p['snapshot']);p['dependencies']=json.loads(p['dependencies'])
                s['events']=[dict(r) for r in c.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 80')]
                s['conversations']=[dict(r) for r in c.execute('SELECT * FROM conversations ORDER BY seq DESC LIMIT 80')]
                s['usage']=[dict(r) for r in c.execute('SELECT * FROM usage ORDER BY day DESC LIMIT 100')]
            s.update(channels=list(self.profiles), profiles=self.profiles, skills=list(SKILL_ROLES),
                agents=list(SPECIALISTS), specialist_registry=SPECIALISTS, max_inflight=self.max_inflight,
                agent_limits={role:capability(role,'claude_draft') for role in SPECIALISTS},
                mode='Zoya v0.7 — scoped control centre', log_error=self.log_error,
                connections={'Text AI': (getattr(self.adapter,'provider','claude')+' configured; live test pending') if self.adapter.ready() else 'provider model/key/digest missing',
                 'YouTube':'publishing disconnected','Facebook':'publishing disconnected','Instagram':'read/write adapter disconnected','Amazon':'report-based drafts only',
                 'Higgsfield':'offline handoff only; live transport not configured',
                 'Windows':'local workspace only','Mobile':'pairing gateway code ready; private HTTPS deployment pending','Skales':'Official DevKit identified; local API connection and live test pending'},
                monitor=self.monitor_state,
                languages=json.loads((ROOT/'config/languages.json').read_text()),
                local_skills=list(__import__('access_control').LOCAL_ROLES),
                paid_enabled=os.environ.get('ZOYA_FREE_MODE')!='1' and os.environ.get('ZOYA_ALLOW_PAID','0')=='1',
                sheet_path='data/excel/ (UTF-8 CSV sheets; auto-refreshed)')
            return s

    def channel_path(self,channel,relative):
        if channel not in self.profiles: raise ValueError('Unknown target')
        base=self.root/'workspaces'/channel
        resolved=safe_path(self.root,'workspaces/'+channel)
        if resolved != base: raise ValueError('Channel workspace symlink is forbidden')
        candidate=safe_path(base,relative)
        safe_path(self.root,str(candidate.relative_to(self.root)))
        return candidate

    def make_snapshot(self, command, channel, dependencies=None, c=None):
        head=command.split(' ',1)[0][1:]
        body=command.partition(' ')[2]
        if head in SKILL_ROLES:
            operation='claude_draft';skill=head
        elif head in ('hook','title','status','remember','quran-index','tool','higgsfield-plan','health','challenge','pipeline','opportunities','sprint','capabilities','quran','recitation','islamic-catalog','news','loss-check','forget','monetization-monitor','content-check'):
            operation='local';skill=head
        else:
            operation='claude_draft';skill='chat';body=command
        if operation=='claude_draft' and getattr(self.adapter,'provider','claude')=='ollama': operation='local_draft'
        if head in RESTRICTED:
            operation='unavailable';skill=head
        if skill in ('amazon-audit','amazon-listing','amazon-ppc','amazon-viability') and channel!='Amazon':
            raise ValueError('Amazon specialist requires the Amazon target profile')
        if skill.startswith('yt-') and not channel.startswith('YT-'):
            raise ValueError('YouTube specialist requires a YouTube profile')
        memory={}; history=[];deps=[]
        if c is not None:
            memory=dict(c.execute('SELECT key,value FROM memory'))
            history=[dict(r) for r in c.execute('SELECT role,text FROM conversations WHERE channel=? ORDER BY seq DESC LIMIT 12',(channel,))][::-1]
            for tid in dependencies or []:
                r=c.execute('SELECT id,agent,output FROM tasks WHERE id=?',(tid,)).fetchone()
                deps.append(dict(r))
        channel_root=self.channel_path(channel,'imports').parent
        voice_file=self.channel_path(channel,'voice.md')
        voice=voice_file.read_text(encoding='utf-8')[:12000] if voice_file.is_file() else 'Writing voice not yet supplied; ask for creator examples before final scripts.'
        system=instructions(skill) if operation in ('claude_draft','local_draft') else ''
        if operation=='local_draft':
            system+='\nThe JSON payload command field is the CURRENT request. Answer that request directly. The conversation field is background only; do not continue an older task. Follow writing_voice for tone and address. Respect the requested language and length. Do not claim actions ran unless execution evidence is provided.'
        payload={'command':clean(body),'target':self.profiles[channel], 'memory':memory,
                 'conversation':history,'dependency_outputs':deps,'writing_voice':voice,
                 'market_evidence':'Only data explicitly supplied in command or completed dependency outputs. No live research connector.',
                 'action_scope':'Generate a draft; do not perform external account operations.'}
        snap={'command':command,'channel':channel,'operation':operation,'skill':skill,
              'agent':role_for(skill,SKILL_ROLES), 'target':dict(self.profiles[channel]),
              'files':f'workspaces/{channel}/outbox/<task_id>.txt',
              'plan':'Review supplied competitor evidence; report missing current data; generate scoped draft; verify result; record activity.',
              'approval_question':'Do you approve?', 'network': 'api.anthropic.com' if operation=='claude_draft' else '127.0.0.1:11434 (local only)' if operation=='local_draft' else 'none',
              'model':os.environ.get('ZOYA_OLLAMA_MODEL' if operation=='local_draft' else 'ZOYA_CLAUDE_MODEL',''),
              'provider':getattr(self.adapter,'provider','claude') if operation in ('claude_draft','local_draft') else 'none',
              'local_model_digest':os.environ.get('ZOYA_OLLAMA_DIGEST','') if operation=='local_draft' else '',
              'max_tokens':max(128,min(1200,int(os.environ.get('ZOYA_MAX_OUTPUT_TOKENS','1200')))),
              'cost':'One paid Claude request; token cap shown. No USD cost estimated without provider pricing.' if operation=='claude_draft' else 'No paid API call',
              'system':system, 'payload':payload}
        snap['permissions']=capability(snap['agent'],operation)
        snap['security_review_only']=(skill=='security-review')
        if skill in ('pipeline','opportunities','sprint','loss-check','monetization-monitor','content-check'):
            from challenge_ops import pipeline,opportunities,sprint
            from loss_check import check as loss_check
            from monetizer import monitor,content_check
            if not body or Path(body).name!=body or body in ('.','..'): raise ValueError('Use /'+skill+' filename.json from selected channel imports/')
            path=self.channel_path(channel,'imports/'+body)
            if not path.is_file() or path.stat().st_size>100000: raise ValueError('Missing operation input or exceeds 100 KB')
            raw=path.read_bytes()
            if SECRET.search(raw.decode('utf-8')): raise ValueError('Possible credential in input; nothing saved')
            if skill=='monetization-monitor':
                supplied=json.loads(raw)
                if supplied.get('platform')!=self.profiles[channel]['platform']: raise ValueError('Report platform differs from selected target')
                expected=self.profiles[channel].get('account_id')
                if self.profiles[channel].get('verified') and expected and supplied.get('account_ref')!=expected: raise ValueError('Report account differs from verified target')
            {'pipeline':pipeline,'opportunities':opportunities,'sprint':sprint,'loss-check':loss_check,'monetization-monitor':monitor,'content-check':content_check}[skill](json.loads(raw))
            snap.update(input_file=str(path.relative_to(self.root)),input_hash=hashlib.sha256(raw).hexdigest(),
                        approved_input=json.loads(raw),plan='Calculate an offline business planning report from supplied data; no spend, outreach or external verification.',
                        cost='No paid API call')
        if skill=='forget':
            if not body or len(body)>100 or '\n' in body: raise ValueError('Use /forget exact_memory_key')
            snap.update(plan='Remove this active personal preference after approval. Historical records remain.',forget_key=body)
        if skill=='news':
            if body.strip(): raise ValueError('Use /news with no URL; only the reviewed BBC World feed is allowed')
            snap.update(network='feeds.bbci.co.uk',plan='Fetch up to twelve latest BBC World RSS headlines once. Public read only; no login, private data or paid API.',cost='No paid API call; bandwidth and provider availability apply')
        if skill in ('quran','recitation'):
            from quran_reader import read_passage
            passage=read_passage(body)
            snap.update(quran_source_hash=passage['source_sha256'],plan='Read verified verbatim Arabic Quran from bundled Tanzil text. No generated verses, translation or external request.')
        if skill=='challenge':
            from business_core import challenge_scorecard
            if not body or Path(body).name!=body or body in ('.','..'): raise ValueError('Use /challenge filename.json from selected channel imports/')
            path=self.channel_path(channel,'imports/'+body)
            if not path.is_file() or path.stat().st_size>16000: raise ValueError('Missing challenge input or exceeds 16 KB')
            raw=path.read_bytes()
            if SECRET.search(raw.decode('utf-8')): raise ValueError('Possible credential in challenge input')
            score=challenge_scorecard(json.loads(raw))
            snap.update(input_file=str(path.relative_to(self.root)),input_hash=hashlib.sha256(raw).hexdigest(),
                        challenge_data=json.loads(raw),plan='Calculate a 90-day cumulative-revenue scorecard from supplied actuals; no external verification or spending.',
                        cost='No paid API call')
        if skill=='higgsfield-plan':
            from higgsfield_bridge import validate_brief
            if not body or Path(body).name!=body or body in ('.','..'): raise ValueError('Use /higgsfield-plan filename.json from selected channel imports/')
            path=self.channel_path(channel,'imports/'+body)
            if not path.is_file() or path.stat().st_size>32000: raise ValueError('Missing brief or brief exceeds 32 KB')
            raw=path.read_bytes()
            if SECRET.search(raw.decode('utf-8')): raise ValueError('Possible credential in brief; nothing saved')
            config=json.loads((ROOT/'config/higgsfield.json').read_text(encoding='utf-8'))
            brief=validate_brief(json.loads(raw),channel,config)
            snap.update(agent='Creative',input_file=str(path.relative_to(self.root)),input_hash=hashlib.sha256(raw).hexdigest(),
                        higgsfield_account=config,brief=brief,
                        plan='Validate supplied creative brief and create an offline handoff. No upload, rendering, credits or publishing.',
                        cost='No paid API call; future provider generation needs a separate quote and approval')
        if skill=='recitation':
            snap.update(network='browser → everyayah.com',plan='Prepare recorded Alafasy recitation playlist for the selected verse range. Play click contacts EveryAyah; no AI-generated scripture or paid API. Audio is streamed, not bundled.')
        if skill=='tool':
            if not channel.startswith('YT-'): raise ValueError('YouTube helper requires YouTube target')
            parts=body.split()
            if len(parts)!=2 or parts[0] not in TOOLS: raise ValueError('Use /tool dead-air|chapters|retention|viral filename (copy input into selected channel imports/)')
            if Path(parts[1]).name!=parts[1] or parts[1] in ('.','..'): raise ValueError('Input filename only; no directory paths')
            p=self.channel_path(channel,'imports/'+parts[1])
            if not p.is_file() or p.stat().st_size>2_000_000: raise ValueError('Missing input or input exceeds 2 MB')
            snap['input_file']=str(p.relative_to(self.root));snap['input_hash']=hashlib.sha256(p.read_bytes()).hexdigest()
            snap['tool']=parts[0]
            snap['agent']={'dead-air':'Editing','chapters':'Editing','retention':'Monetizer','viral':'Research'}[parts[0]]
        snap['permissions']=capability(snap['agent'],operation)
        return snap

    def submit(self, command, channel, request_id, parent_id=None, dependencies=None):
        if not isinstance(command,str) or not command.strip() or len(command)>6000: raise ValueError('Command must contain 1–6000 characters')
        if channel not in self.profiles: raise ValueError('Unknown target')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100: raise ValueError('request_id required')
        if SECRET.search(command): raise ValueError('Possible credential in command; configure locally instead. Nothing saved.')
        from quran_reader import route_command
        command=route_command(command.strip());validate_command(command,SKILL_ROLES)
        dependencies=dependencies or []
        with self.lock,self.db() as c:
            old=c.execute('SELECT * FROM tasks WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['command']!=command or old['channel']!=channel: raise ValueError('request_id reused with a different payload')
                return dict(old)
            for dep in dependencies:
                if not c.execute('SELECT id FROM tasks WHERE id=? AND channel=?',(dep,channel)).fetchone(): raise ValueError('Unknown or cross-channel dependency')
            ready=not dependencies or all(c.execute('SELECT status FROM tasks WHERE id=?',(d,)).fetchone()[0]=='completed' for d in dependencies)
            snap=self.make_snapshot(command,channel,dependencies if ready else [],c)
            status='awaiting_approval' if ready else 'waiting_dependencies'
            if snap['operation']=='unavailable': status='blocked'
            tid=str(uuid.uuid4())
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(tid,request_id,channel,snap['agent'],command,status,
                       'Adapter disconnected; nothing executed.' if status=='blocked' else '',time.time()))
            c.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?)',(tid,json.dumps(snap,ensure_ascii=False),digest(snap),time.time()+3600,0,0,parent_id,json.dumps(dependencies)))
            c.execute('INSERT INTO conversations(time,channel,role,task_id,text) VALUES(?,?,?,?,?)',(time.time(),channel,'user',tid,command))
            self.event(tid,'plan_created',json.dumps({'target':snap['target'],'operation':snap['operation'],'status':status}),c)
            result=dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
        self.export_sheets();return result

    def workflow(self, command, channel, request_id):
        if not isinstance(command,str) or not command.strip() or len(command)>6000: raise ValueError('Workflow command required, max 6000 characters')
        if channel not in self.profiles: raise ValueError('Unknown target')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=70: raise ValueError('Workflow request_id required, max 70 characters')
        ids={}
        for key,skill,body,deps in workflow_specs(channel,command):
            t=self.submit('/'+skill+' '+body,channel,request_id+':'+key,
                          parent_id=ids.get('survey'),dependencies=[ids[d] for d in deps])
            ids[key]=t['id']
        return {'task_ids':list(ids.values()),'steps':ids,'status':'Scoped DAG created; every ready step requires its own exact approval. No publishing.'}

    def approve(self, tid, expected_hash, decision):
        if decision not in ('approve','reject'): raise ValueError('Invalid decision')
        with self.lock,self.db() as c:
            task=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            plan=c.execute('SELECT * FROM plans WHERE task_id=?',(tid,)).fetchone()
            if not task or not plan or task['status']!='awaiting_approval': raise ValueError('Task is not awaiting approval')
            snap=json.loads(plan['snapshot'])
            if expected_hash!=plan['hash'] or digest(snap)!=plan['hash']: raise ValueError('Proposal changed; refresh before approving')
            if time.time()>plan['expires']: raise ValueError('Approval expired; submit a new task')
            if snap['target']!=self.profiles[task['channel']]: raise ValueError('Target changed; submit a new task')
            if decision=='approve' and snap['operation'] in ('claude_draft','local_draft'):
                if not self.adapter.ready(): raise ValueError('Text provider configuration missing; configure exact local model/digest or optional paid Claude on laptop')
                if snap['model']!=os.environ.get('ZOYA_OLLAMA_MODEL' if snap['operation']=='local_draft' else 'ZOYA_CLAUDE_MODEL'): raise ValueError('Model changed; submit a new task')
            c.execute('UPDATE plans SET approved=? WHERE task_id=?',(int(decision=='approve'),tid))
            c.execute('UPDATE tasks SET status=? WHERE id=?',('queued' if decision=='approve' else 'rejected',tid))
            self.event(tid,decision,plan['hash'],c)
        self.export_sheets();return {'ok':True}

    def cancel(self, tid):
        with self.lock,self.db() as c:
            c.execute("UPDATE tasks SET status='cancelled',output='Cancelled; an in-flight API request may still incur usage; its result will be discarded.' WHERE id=? AND status IN ('queued','running','blocked','awaiting_approval','waiting_dependencies')",(tid,))
            self.event(tid,'cancel','User cancelled task',c)
        self.export_sheets()

    def pause(self,value):
        super().pause(value);self.event('', 'pause' if value else 'resume','Queue control');self.export_sheets()

    def forget(self,key):
        with self.lock,self.db() as c:
            c.execute('DELETE FROM memory WHERE key=?',(key,))
            self.event('','forget','Active preference removed; historical logs and snapshots retain prior instructions.',c)
        self.export_sheets()

    def _release_dependencies(self,c):
        for t in c.execute("SELECT t.*,p.dependencies FROM tasks t JOIN plans p ON p.task_id=t.id WHERE t.status='waiting_dependencies'").fetchall():
            deps=json.loads(t['dependencies'])
            states=[c.execute('SELECT status FROM tasks WHERE id=?',(d,)).fetchone()[0] for d in deps]
            if any(s in ('failed','blocked','rejected','cancelled') for s in states):
                c.execute("UPDATE tasks SET status='blocked',output='Predecessor failed or was not approved; no execution.' WHERE id=?",(t['id'],))
                self.event(t['id'],'dependency_blocked',str(states),c)
            elif all(s=='completed' for s in states):
                snap=self.make_snapshot(t['command'],t['channel'],deps,c)
                c.execute('UPDATE plans SET snapshot=?,hash=?,expires=? WHERE task_id=?',(json.dumps(snap,ensure_ascii=False),digest(snap),time.time()+3600,t['id']))
                c.execute("UPDATE tasks SET status='awaiting_approval' WHERE id=?",(t['id'],))
                self.event(t['id'],'ready_for_approval','Dependency outputs included; fresh approval required.',c)

    def tick(self, agent=None):
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            self._release_dependencies(c)
            if c.execute('SELECT paused FROM control').fetchone()[0]: return False
            if c.execute("SELECT count(*) FROM tasks WHERE status='running'").fetchone()[0]>=self.max_inflight: return False
            query="SELECT t.*,p.snapshot,p.hash,p.expires,p.approved,p.consumed FROM tasks t JOIN plans p ON p.task_id=t.id WHERE t.status='queued' AND NOT EXISTS (SELECT 1 FROM tasks r WHERE r.status='running' AND r.agent=t.agent)"
            params=[]
            if agent is not None:
                query+=' AND t.agent=?';params.append(agent)
            t=c.execute(query+' ORDER BY created LIMIT 1',params).fetchone()
            if not t: return False
            snap=json.loads(t['snapshot'])
            allowed=(t['approved']==1 and not t['consumed'] and t['expires']>=time.time() and digest(snap)==t['hash'] and snap['target']==self.profiles[t['channel']])
            allowed=allowed and snap.get('permissions')==capability(snap['agent'],snap['operation'])
            if snap['operation']=='claude_draft': allowed=allowed and snap['model']==os.environ.get('ZOYA_CLAUDE_MODEL')
            if snap['operation']=='local_draft':
                allowed=allowed and snap['model']==os.environ.get('ZOYA_OLLAMA_MODEL') and snap['local_model_digest']==os.environ.get('ZOYA_OLLAMA_DIGEST') and getattr(self.adapter,'provider','claude')=='ollama'
            if snap['operation']=='claude_draft': allowed=allowed and getattr(self.adapter,'provider','claude')=='claude'
            if not allowed:
                c.execute("UPDATE tasks SET status='blocked',output='Invalid, expired or consumed approval; nothing executed.' WHERE id=?",(t['id'],))
                self.event(t['id'],'approval_blocked','Nothing executed',c);return True
            if snap['operation']=='claude_draft':
                if os.environ.get('ZOYA_FREE_MODE')=='1' or os.environ.get('ZOYA_ALLOW_PAID','0')!='1':
                    c.execute("UPDATE tasks SET status='blocked',output='Free-first mode: paid drafting disabled. Local tasks continue; enable ZOYA_ALLOW_PAID=1 locally and submit a fresh approved plan only when needed.' WHERE id=?",(t['id'],))
                    self.event(t['id'],'paid_disabled','No provider request or usage reservation',c);return True
                day=time.strftime('%Y-%m-%d',time.gmtime())
                count=c.execute("SELECT COUNT(*) FROM usage WHERE day=? AND (provider_id IS NULL OR provider_id NOT LIKE 'local:%')",(day,)).fetchone()[0]
                if count>=max(0,min(500,int(os.environ.get('ZOYA_MAX_CALLS_DAY','2')))):
                    c.execute("UPDATE tasks SET status='blocked',output='Daily Claude request ceiling reached.' WHERE id=?",(t['id'],))
                    self.event(t['id'],'budget_blocked',day,c);return True
                from budget_guard import reserve_paid
                try:
                    reserve_paid(c,t['id'],snap)
                except ValueError as budget_error:
                    c.execute("UPDATE tasks SET status='blocked',output=? WHERE id=?",(str(budget_error),t['id']))
                    self.event(t['id'],'budget_blocked',str(budget_error),c);return True
                c.execute('INSERT INTO usage VALUES(?,?,?,NULL,NULL,NULL)',(t['id'],day,'reserved'))
            c.execute('UPDATE plans SET consumed=1 WHERE task_id=?',(t['id'],))
            c.execute("UPDATE tasks SET status='running' WHERE id=?",(t['id'],))
            self.event(t['id'],'started',snap['agent'],c)
        try:
            status,output=self.execute_snapshot(dict(t),snap)
            output=clean(output)
        except Exception as e:
            status,output='failed',clean(str(e))[:2000]
        with self.lock,self.db() as c:
            current=c.execute('SELECT status FROM tasks WHERE id=?',(t['id'],)).fetchone()[0]
            if current=='running':
                if status=='completed':
                    try:
                        p=self.channel_path(t['channel'],'outbox/'+t['id']+'.txt')
                        p.parent.mkdir(parents=True,exist_ok=True);p.write_text(output,encoding='utf-8')
                    except Exception:
                        status='failed';output='Output workspace unavailable; original API result discarded. Check provider usage.'
                c.execute('UPDATE tasks SET status=?,output=? WHERE id=?',(status,output,t['id']))
                c.execute('INSERT INTO conversations(time,channel,role,task_id,text) VALUES(?,?,?,?,?)',(time.time(),t['channel'],'assistant',t['id'],output))
                self.event(t['id'],status,output,c)
            else: self.event(t['id'],'discarded','Result discarded after cancellation',c)
            c.execute("UPDATE usage SET status=? WHERE task_id=? AND status IN ('reserved','local_reserved')",('uncertain_or_failed',t['id']))
            self._release_dependencies(c)
        self.export_sheets();return True

    def execute_snapshot(self,task,snap):
        if snap['operation'] in ('claude_draft','local_draft'):
            if snap['operation']=='local_draft':
                with self.db() as c:c.execute('INSERT INTO usage VALUES(?,?,?,NULL,NULL,?)',(task['id'],time.strftime('%Y-%m-%d',time.gmtime()),'local_reserved','local:'+snap['local_model_digest']))
            if len(json.dumps(snap['payload']))+len(snap['system'])>120000: raise ValueError('Approved context exceeds 120000-character ceiling; shorten inputs.')
            result=self.adapter.generate(snap['system'],snap['payload'],snap['model'],snap['max_tokens'])
            with self.db() as c:
                c.execute('UPDATE usage SET status=?,input_tokens=?,output_tokens=?,provider_id=? WHERE task_id=?',
                    ('returned',result['usage'].get('input_tokens'),result['usage'].get('output_tokens'),result['provider_id'],task['id']))
            if snap['skill'] in ('yt-outlier','amazon-viability'):
                validator=__import__('research_blueprint' if snap['skill']=='yt-outlier' else 'amazon_viability').validate
                try: validated=validator(json.loads(result['text']))
                except (ValueError,TypeError): raise ValueError('Structured analysis incomplete or invalid; supply missing evidence. No automatic paid retry.')
                return 'completed',json.dumps(validated,ensure_ascii=False)
            return 'completed',result['text']+'\n\nExecution: '+snap['provider']+' draft returned. Nothing published or changed on accounts.'
        if snap['skill'] in ('pipeline','opportunities','sprint','loss-check','monetization-monitor','content-check'):
            from challenge_ops import pipeline,opportunities,sprint
            from loss_check import check as loss_check
            from monetizer import monitor,content_check
            path=self.channel_path(task['channel'],'imports/'+Path(snap['input_file']).name)
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=snap['input_hash']: raise ValueError('Input changed after approval')
            result={'pipeline':pipeline,'opportunities':opportunities,'sprint':sprint,'loss-check':loss_check,'monetization-monitor':monitor,'content-check':content_check}[snap['skill']](json.loads(raw))
            return 'completed',json.dumps(result,indent=2)
        if snap['skill']=='challenge':
            from business_core import challenge_scorecard
            path=self.channel_path(task['channel'],'imports/'+Path(snap['input_file']).name)
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=snap['input_hash']: raise ValueError('Input changed after approval')
            return 'completed',json.dumps(challenge_scorecard(json.loads(raw)),indent=2)
        if snap['skill']=='forget':
            self.forget(snap['forget_key'])
            return 'completed','Active preference removed after approval. Historical records retained.'
        if snap['skill'] in ('quran','recitation'):
            from quran_reader import read_passage
            passage=read_passage(snap['command'].partition(' ')[2])
            if passage['source_sha256']!=snap['quran_source_hash']: raise ValueError('Quran source changed after approval')
            if snap['skill']=='recitation':
                passage.update(kind='recitation_playlist',reciter='Mishary Alafasy',audio_status='Playlist prepared; playback has not been verified',audio_source='https://everyayah.com/data/Alafasy_128kbps/')
                for verse in passage['verses']:
                    surah,ayah=map(int,verse['reference'].split(':'))
                    verse['audio_url']=f'https://everyayah.com/data/Alafasy_128kbps/{surah:03d}{ayah:03d}.mp3'
            return 'completed',json.dumps(passage,ensure_ascii=False,indent=2)
        if snap['skill']=='islamic-catalog': return 'completed',(ROOT/'knowledge/islamic-catalogue.json').read_text(encoding='utf-8')
        if snap['skill']=='news':
            from public_news import fetch_news
            return 'completed',json.dumps(fetch_news(),ensure_ascii=False,indent=2)
        if snap['skill']=='capabilities':
            from free_first import capability_report
            return 'completed',json.dumps(capability_report(self.state()),indent=2)
        if snap['skill']=='health':
            with self.db() as c:
                integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
                counts={r[0]:r[1] for r in c.execute('SELECT status,count(*) FROM tasks GROUP BY status')}
            return 'completed',json.dumps({'sqlite_integrity':integrity,'task_counts':counts,
                'max_inflight':self.max_inflight,'specialists':list(SPECIALISTS),
                'csv_export_error':self.log_error,'external_connections':self.state()['connections'],
                'recovery':'Interrupted tasks remain blocked; inspect evidence before resubmission. No automatic external repair.'},indent=2)
        if snap['skill']=='higgsfield-plan':
            from higgsfield_bridge import handoff
            path=self.channel_path(task['channel'],'imports/'+Path(snap['input_file']).name)
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=snap['input_hash']: raise ValueError('Input changed after approval')
            account=json.loads((ROOT/'config/higgsfield.json').read_text(encoding='utf-8'))
            if account!=snap['higgsfield_account']: raise ValueError('Higgsfield target changed after approval')
            return 'completed',json.dumps(handoff(json.loads(raw),task['channel'],account),ensure_ascii=False,indent=2)
        if snap['skill']=='quran-index': return 'completed',(ROOT/'knowledge/islamic-catalogue.json').read_text(encoding='utf-8')
        if snap['skill']=='status': return 'completed',json.dumps(self.state()['connections'],indent=2)
        if snap['skill']=='tool':
            import subprocess,sys
            from agents import VENDOR
            path=self.channel_path(task['channel'],'imports/'+Path(snap['input_file']).name)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=snap['input_hash']: raise ValueError('Input changed after approval')
            script,extra=TOOLS[snap['tool']]
            args=[sys.executable,str(VENDOR/'skills'/script),str(path)]
            if extra: args.extend([extra,'2.0'])
            p=subprocess.run(args,cwd=VENDOR,capture_output=True,text=True,encoding='utf-8',timeout=15,shell=False)
            if p.returncode: raise ValueError(p.stderr[:1000] or p.stdout[:1000] or 'Local helper failed')
            return 'completed',p.stdout[:100000]
        # Only explicitly enumerated base commands reach the offline foundation.
        if snap['skill'] not in ('hook','title','remember'): raise ValueError('No executable capability')
        return super().execute(task)

    def export_sheets(self):
        """Automatic Excel-compatible sheets. SQLite remains the authoritative full record."""
        tables=('tasks','plans','events','conversations','memory','usage')
        try:
            with self.lock,self.db() as c:
                folder=safe_path(self.root,'data/excel');folder.mkdir(parents=True,exist_ok=True)
                for name in tables:
                    rows=c.execute('SELECT * FROM '+name).fetchall()
                    columns=[r[1] for r in c.execute('PRAGMA table_info('+name+')')]
                    target=safe_path(folder,name+'.csv');temp=safe_path(folder,name+'.tmp')
                    with temp.open('w',encoding='utf-8-sig',newline='') as f:
                        writer=csv.writer(f);writer.writerow(columns)
                        for row in rows:
                            vals=[]
                            for value in row:
                                s='' if value is None else clean(value)
                                if s.lstrip().startswith(('=','+','-','@')): s="'"+s
                                vals.append(s)
                            writer.writerow(vals)
                    temp.replace(target)
                # Date-wise flattened events for Excel and a future approved Google Sheets sink.
                from datetime import datetime,timezone,timedelta
                target=safe_path(folder,'activity.csv');temp=safe_path(folder,'activity.tmp')
                columns=('event_id','timestamp_utc','date_riyadh','time_riyadh','task_id','channel','agent','event_kind','task_current_status','plan_hash','provider_id','input_tokens','output_tokens','quoted_cost','actual_cost_usd','detail')
                rows=c.execute('SELECT e.*,t.channel,t.agent,t.status AS task_status,p.hash,p.snapshot,u.provider_id,u.input_tokens,u.output_tokens FROM events e LEFT JOIN tasks t ON t.id=e.task_id LEFT JOIN plans p ON p.task_id=e.task_id LEFT JOIN usage u ON u.task_id=e.task_id ORDER BY e.seq').fetchall()
                with temp.open('w',encoding='utf-8-sig',newline='') as f:
                    writer=csv.writer(f);writer.writerow(columns)
                    for row in rows:
                        utc=datetime.fromtimestamp(row['time'],timezone.utc)
                        local=utc.astimezone(timezone(timedelta(hours=3)))
                        cost=json.loads(row['snapshot']).get('cost','') if row['snapshot'] else ''
                        values=(f"EVT-{row['seq']}",utc.isoformat(),local.date().isoformat(),local.strftime('%H:%M:%S'),row['task_id'],row['channel'],row['agent'],row['kind'],row['task_status'],row['hash'],row['provider_id'],row['input_tokens'] if row['kind']=='completed' else None,row['output_tokens'] if row['kind']=='completed' else None,cost,None,row['detail'])
                        cleaned=[]
                        for value in values:
                            value='' if value is None else clean(value)
                            if value.lstrip().startswith(('=','+','-','@')):value="'"+value
                            cleaned.append(value)
                        writer.writerow(cleaned)
                temp.replace(target)
                self.log_error=''
        except OSError:
            self.log_error='Excel export unavailable (file may be open). Full records remain in SQLite; close Excel and refresh.'
