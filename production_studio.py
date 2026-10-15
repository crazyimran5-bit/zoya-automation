"""Durable 4K production workflow. External generation is a recorded handoff.
No provider credentials, implicit retries, purchases or publishing.
"""
import hashlib
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from video_render import scoped_path, probe_media, render_video, RenderConfig, qa_video, _run, _tool

STAGES = ('script','performance','captions','render','quality','review')
def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()
def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def validate_script(s):
    if not isinstance(s,dict) or set(s)!= {'title','characters','scenes'}: raise ValueError('Script requires title, characters and scenes')
    if not isinstance(s['title'],str) or not 1<=len(s['title'])<=160: raise ValueError('Title required')
    chars=s['characters']
    if not isinstance(chars,list) or not 1<=len(chars)<=6: raise ValueError('Supply 1-6 characters')
    names=[]
    for c in chars:
        if not isinstance(c,dict) or set(c)!= {'name','voice_description','appearance'}: raise ValueError('Character needs name, voice_description and appearance')
        if any(not isinstance(v,str) or not v.strip() or len(v)>1000 for v in c.values()): raise ValueError('Character fields invalid')
        names.append(c['name'])
    if len(set(names))!=len(names): raise ValueError('Duplicate character')
    scenes=s['scenes']
    if not isinstance(scenes,list) or not 1<=len(scenes)<=60: raise ValueError('Supply 1-60 scenes')
    total=0
    for scene in scenes:
        if not isinstance(scene,dict) or set(scene)!= {'duration','action','dialogue'}: raise ValueError('Scene needs duration, action, dialogue')
        if not finite(scene['duration']) or not 1<=scene['duration']<=30: raise ValueError('Scene duration 1-30 seconds')
        if not isinstance(scene['action'],str) or not 1<=len(scene['action'])<=2000: raise ValueError('Concrete action required')
        if not isinstance(scene['dialogue'],list) or not scene['dialogue']: raise ValueError('Dialogue required')
        for line in scene['dialogue']:
            if not isinstance(line,dict) or set(line)!= {'speaker','text'} or line['speaker'] not in names or not isinstance(line['text'],str) or not 1<=len(line['text'])<=1000: raise ValueError('Dialogue speaker/text invalid')
        total+=scene['duration']
    if total>1800: raise ValueError('Maximum 30 minutes')
    return s

class Studio:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.base=self.root/'workspaces'/'Production4K'
        self.base.mkdir(parents=True,exist_ok=True)
        self.database=self.base/'workflow.sqlite3'
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, script TEXT, hash TEXT, status TEXT, state TEXT, updated REAL)')
        self.recover()
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.database,timeout=20); c.row_factory=sqlite3.Row
        try:
            with c: yield c
        finally: c.close()
    def _get(self,c,jid):
        row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise ValueError('Unknown production job')
        j=dict(row); j['script']=json.loads(j['script']); j['state']=json.loads(j['state'])
        return j
    def get(self,jid):
        with self.db() as c: return self._get(c,jid)
    def save(self,c,j):
        c.execute('UPDATE jobs SET status=?,state=?,updated=? WHERE id=?',(j['status'],json.dumps(j['state'],allow_nan=False),time.time(),j['id']))
    def recover(self):
        with self.db() as c:
            for row in c.execute("SELECT id FROM jobs WHERE status='rendering'").fetchall():
                j=self._get(c,row['id']);j['status']='render_interrupted'
                j['state']['blocker']='Local render interrupted. Resume revalidates all hashes and reuses completed output only after QA.'
                self.save(c,j)
    def create(self,script,request_id):
        validate_script(script)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100: raise ValueError('Request ID required')
        h=digest(script)
        with self.db() as c:
            old=c.execute('SELECT id,hash FROM jobs WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['hash']!=h: raise ValueError('Request ID reused with changed script')
                return self._get(c,old['id'])
            jid=str(uuid.uuid4()); state={'stages':{x:'pending' for x in STAGES},'scenes':{},'upload_allowed':False}
            state['stages']['script']='complete'
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',(jid,request_id,json.dumps(script),h,'awaiting_performance',json.dumps(state),time.time()))
            return self._get(c,jid)
    def handoff(self,jid,index):
        j=self.get(jid)
        if type(index)!=int or not 0<=index<len(j['script']['scenes']): raise ValueError('Invalid scene')
        scene=j['script']['scenes'][index]
        payload={'schema':'zoya.performance-handoff.v1','job_id':jid,'scene':index,'script_hash':j['hash'],
          'characters':j['script']['characters'],'scene_brief':scene,'requirements':{'actual_character_motion':True,'matching_character_voice':True,'lip_sync':True,'natural_speed':True,'no_text_cards':True,'no_stock_overdub':True,'aspect_ratio':'16:9','delivery':'3840x2160','preserve_generated_dialogue':True},
          'provider':'Higgsfield','submission':'external_connector_required','submitted':False}
        payload['request_key']=digest(payload)
        return payload
    def record_submission(self,jid,index,provider_job_id,request_key):
        h=self.handoff(jid,index)
        if request_key!=h['request_key']: raise ValueError('Changed handoff')
        if not isinstance(provider_job_id,str) or not 1<=len(provider_job_id)<=200: raise ValueError('Provider job ID required')
        with self.db() as c:
            j=self._get(c,jid); key=str(index); old=j['state']['scenes'].get(key)
            if old:
                if old['provider_job_id']!=provider_job_id: raise ValueError('Scene already submitted: do not buy duplicate job')
                return j
            j['state']['scenes'][key]={'provider_job_id':provider_job_id,'request_key':request_key,'status':'submitted'}
            j['status']='awaiting_provider';self.save(c,j);return j
    def accept_scene(self,jid,index,receipt):
        if not isinstance(receipt,dict): raise ValueError('Performance receipt required')
        h=self.handoff(jid,index)
        p=scoped_path(self.base,receipt.get('path',''))
        if receipt.get('sha256')!=sha(p): raise ValueError('Performance file changed')
        if receipt.get('method') not in ('generated_dialogue','character_performance','rigged_animation'): raise ValueError('Actual character performance required')
        review=receipt.get('review',{})
        for name in ('identity','action','lip_sync','matching_voice','natural_pacing'):
            if review.get(name) is not True: raise ValueError('Performance review missing: '+name)
        info=probe_media(p)
        if not any(s.get('codec_type')=='audio' for s in info['streams']): raise ValueError('Scene must contain synchronized dialogue audio')
        if not any(s.get('codec_type')=='video' for s in info['streams']): raise ValueError('Scene video missing')
        if abs(info['duration']-h['scene_brief']['duration'])>.5: raise ValueError('Scene duration mismatch')
        with self.db() as c:
            j=self._get(c,jid); old=j['state']['scenes'].get(str(index))
            if not old or receipt.get('provider_job_id')!=old['provider_job_id']: raise ValueError('Record returned provider job ID first')
            if old.get('status')=='accepted':
                if old.get('receipt')!=receipt: raise ValueError('Accepted performance is immutable')
                return j
            old.update(status='accepted',receipt=receipt)
            if all(j['state']['scenes'].get(str(i),{}).get('status')=='accepted' for i in range(len(j['script']['scenes']))):
                j['state']['stages']['performance']='complete';j['status']='awaiting_captions'
            self.save(c,j);return j
    def captions(self,jid,segments):
        j=self.get(jid)
        if j['status']!='awaiting_captions': raise ValueError('Reviewed performance must complete first')
        if not isinstance(segments,list) or not segments or len(segments)>2000: raise ValueError('Timestamped spoken captions required')
        duration=sum(x['duration'] for x in j['script']['scenes']);last=0;lines=[]
        def stamp(v):
            n=round(v*1000);h,n=divmod(n,3600000);m,n=divmod(n,60000);s,ms=divmod(n,1000)
            return f'{h:02}:{m:02}:{s:02},{ms:03}'
        for i,s in enumerate(segments):
            if not isinstance(s,dict) or set(s)!= {'start','end','text'}: raise ValueError('Caption requires start,end,text')
            a,b=s['start'],s['end']
            if not finite(a) or not finite(b) or a<last or b<=a or b>duration: raise ValueError('Caption timing invalid')
            if not isinstance(s['text'],str) or not s['text'].strip() or len(s['text'])>160 or any(x in s['text'] for x in ('\n','\r','-->','{','}')): raise ValueError('Use short plain captions')
            lines.append(f"{i+1}\n{stamp(a)} --> {stamp(b)}\n{s['text']}\n");last=b
        # Captions must match the reviewed spoken script, rather than unrelated titles.
        import re
        normal=lambda x:re.sub(r'[^\w]+',' ',x.lower()).strip()
        expected=' '.join(line['text'] for scene in j['script']['scenes'] for line in scene['dialogue'])
        if normal(' '.join(s['text'] for s in segments))!=normal(expected): raise ValueError('Captions do not match reviewed dialogue')
        folder=self.base/jid;folder.mkdir(exist_ok=True);p=folder/'captions.srt'
        p.write_text('\n'.join(lines),encoding='utf-8')
        with self.db() as c:
            current=self._get(c,jid)
            if current['status']!='awaiting_captions': raise ValueError('Job changed')
            current['state']['captions']={'path':str(p.relative_to(self.base)),'sha256':sha(p),'segments':segments}
            current['state']['stages']['captions']='complete';current['status']='ready_to_render';self.save(c,current)
        return self.get(jid)
    def auto_captions(self,jid):
        j=self.get(jid)
        if j['status']!='awaiting_captions': raise ValueError('Reviewed performance must complete first')
        model_path=self.root/'data/offline/faster-whisper-base'
        if not (model_path/'model.bin').is_file(): raise ValueError('Local Whisper base model unavailable')
        from whisper_backend import WhisperModel
        model=WhisperModel(str(model_path),device='cpu',compute_type='int8',cpu_threads=2)
        import re
        norm=lambda text:re.sub(r'[^\w]+','',text.lower())
        out=[];offset=0
        for i,scene in enumerate(j['script']['scenes']):
            item=j['state']['scenes'][str(i)]['receipt'];p=scoped_path(self.base,item['path'])
            if sha(p)!=item['sha256']: raise ValueError('Approved performance changed')
            segments,_=model.transcribe(str(p),beam_size=1,word_timestamps=True,condition_on_previous_text=False)
            words=[w for seg in segments for w in (seg.words or []) if norm(w.word)]
            authored=' '.join(line['text'] for line in scene['dialogue']).split()
            if len(words)!=len(authored) or any(norm(w.word)!=norm(a) for w,a in zip(words,authored)):
                raise ValueError('Speech transcript differs from approved dialogue; review audio/script before captions')
            for n in range(0,len(words),5):
                group=words[n:n+5];a=max(offset,offset+float(group[0].start));b=min(offset+scene['duration'],offset+float(group[-1].end))
                out.append({'start':a,'end':b,'text':' '.join(authored[n:n+5])})
            offset+=scene['duration']
        return self.captions(jid,out)

    def render(self,jid):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');j=self._get(c,jid)
            if j['status'] not in ('ready_to_render','render_interrupted','render_failed'): raise ValueError('Job not ready for local render')
            j['status']='rendering';self.save(c,j)
        try:
            scenes=[j['state']['scenes'][str(i)]['receipt'] for i in range(len(j['script']['scenes']))]
            cap=j['state']['captions']
            for item in scenes+[cap]:
                if sha(scoped_path(self.base,item['path']))!=item['sha256']: raise ValueError('Approved media changed')
            folder=self.base/jid;source=folder/'joined.mp4';dest=folder/'final-4k.mp4'
            # Normalize each clip while PRESERVING its synchronized audio. No voice replacement or loops.
            import tempfile
            with tempfile.TemporaryDirectory(dir=folder,prefix='.assemble-') as td:
                td=Path(td);total=0
                for i,item in enumerate(scenes):
                    p=scoped_path(self.base,item['path']);duration=j['script']['scenes'][i]['duration'];total+=duration
                    _run([_tool('ffmpeg'),'-nostdin','-v','error','-i',str(p),'-t',str(duration),'-vf','scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,setsar=1,fps=30','-c:v','libx264','-preset','fast','-crf','20','-threads','2','-c:a','aac','-ar','48000','-ac','2',str(td/f'{i}.mp4')],timeout=1800)
                (td/'list.txt').write_text(''.join(f"file '{i}.mp4'\n" for i in range(len(scenes))),encoding='utf-8')
                _run([_tool('ffmpeg'),'-nostdin','-v','error','-f','concat','-safe','1','-i','list.txt','-c','copy','joined.mp4'],cwd=td,timeout=1800)
                # Atomic replacement only of our local intermediate.
                import shutil
                shutil.copyfile(td/'joined.mp4',source)
            if not dest.exists():
                result=render_video(self.base,[str(source.relative_to(self.base))],str(source.relative_to(self.base)),str(dest.relative_to(self.base)),approved=True,subtitles=cap['path'],config=RenderConfig(width=3840,height=2160,fps=30))
            quality=qa_video(dest,width=3840,height=2160,duration=total,fps=30)
            # Decode and inspect black/frozen intervals. These are conservative flags for owner review.
            scan=_run([_tool('ffmpeg'),'-nostdin','-hide_banner','-i',str(dest),'-vf','blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-50dB:d=2','-an','-f','null','-'],timeout=1800)
            quality['black_intervals']=scan.stderr.count('black_start:')
            quality['freeze_intervals']=scan.stderr.count('freeze_start:')
            quality['passed']=quality['passed'] and not quality['black_intervals'] and not quality['freeze_intervals']
            with self.db() as c:
                current=self._get(c,jid);current['state']['stages']['render']='complete'
                current['state']['quality']=quality;current['state']['final']={'path':str(dest.relative_to(self.base)),'sha256':sha(dest),'source_resolution':[{'path':x['path'],'streams':probe_media(scoped_path(self.base,x['path']))['streams']} for x in scenes]}
                current['state']['stages']['quality']='complete' if quality['passed'] else 'failed'
                current['status']='awaiting_owner_review' if quality['passed'] else 'quality_failed';self.save(c,current)
            return self.get(jid)
        except Exception as e:
            with self.db() as c:
                current=self._get(c,jid);current['status']='render_failed';current['state']['blocker']=type(e).__name__+': '+str(e)[:200];self.save(c,current)
            raise
    def review(self,jid,expected_sha,checks):
        with self.db() as c:
            j=self._get(c,jid)
            if j['status']!='awaiting_owner_review': raise ValueError('Technical QA not passed')
            item=j['state']['final']
            if expected_sha!=item['sha256'] or sha(scoped_path(self.base,item['path']))!=expected_sha: raise ValueError('Final artifact changed')
            for k in ('motion','lip_sync','voice','subtitle_sync','visual_continuity'):
                if not isinstance(checks,dict) or checks.get(k) is not True: raise ValueError('Owner review missing: '+k)
            j['state']['stages']['review']='complete';j['state']['owner_checks']=checks;j['status']='ready';self.save(c,j);return j
