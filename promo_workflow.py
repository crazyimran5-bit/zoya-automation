"""Bounded local promo tools with separate exact TTS and rendering approvals.

No account operations, publishing endpoints, model tools or automatic paid retries.
"""
import hashlib,json,os,shutil,sqlite3,subprocess,time,uuid,sys
from pathlib import Path
from queue_sync import connection
ROOT=Path(__file__).resolve().parent
DB=ROOT/'data/promo-jobs.sqlite3'
SCRIPT=('Every pet deserves a little more care, every day. Start with the basics: fresh water, '
        'comfortable rest, gentle grooming, and time together. Before choosing any pet-care item, '
        'check its size, materials, and suitability for your companion. Prime Falcon invites you '
        'to make thoughtful choices for everyday pet care. Explore the available product details, '
        'follow the instructions, and choose what fits your pet. Small routines. More quality time.')

def initialize():
    DB.parent.mkdir(exist_ok=True)
    with connection(DB) as db:db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,command TEXT,script TEXT,status TEXT,voice_plan TEXT,audio TEXT,render_plan TEXT,result TEXT,created REAL)')

def get(job_id):
    initialize()
    with connection(DB) as db:
        db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise ValueError('Unknown promo job')
        return dict(row)

def list_jobs():
    initialize()
    with connection(DB) as db:return [dict(zip(('id','status','created'),row)) for row in db.execute('SELECT id,status,created FROM jobs ORDER BY created DESC LIMIT 10')]

def create(command,request_id):
    initialize()
    # Reviewed generic template: no claims about an unspecified physical product.
    job_id=str(uuid.uuid4());folder=ROOT/'workspaces/Zoya/promo'/job_id;folder.mkdir(parents=True)
    from natural_voice import ElevenLabsVoice,VoiceConfig
    voice=ElevenLabsVoice(folder,VoiceConfig.from_env('zoya'))
    plan=voice.plan(SCRIPT,'narration.mp3')
    existing_id=None
    with connection(DB) as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT id,command FROM jobs WHERE request_id=?',(request_id,)).fetchone()
        if old:
            if old[1]!=command:raise ValueError('Request ID reused with changed command')
            existing_id=old[0]
        else:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',(job_id,request_id,command,SCRIPT,'awaiting_voice_approval',json.dumps(plan),'','','',time.time()))
    if existing_id:return get(existing_id)
    (folder/'script.txt').write_text(SCRIPT,encoding='utf-8')
    (folder/'niche-check.json').write_text(json.dumps({'source':'supplied Prime Falcon pet-care brief','market_validated':False,'claims':'No specific product specifications supplied; generic routine advice only','worker':'Niche/Claim Check','requires_evidence_for_viral_analysis':True},indent=2))
    return get(job_id)

def approve_voice(job_id,expected_hash):
    job=get(job_id);plan=json.loads(job['voice_plan'])
    if expected_hash!=plan['hash']:raise ValueError('Voice approval hash mismatch')
    with connection(DB) as db:
        db.execute('BEGIN IMMEDIATE')
        changed=db.execute("UPDATE jobs SET status='voice_running' WHERE id=? AND status='awaiting_voice_approval'",(job_id,)).rowcount
        if changed!=1:raise ValueError('Voice already attempted or not awaiting approval')
    folder=ROOT/'workspaces/Zoya/promo'/job_id
    try:
        from natural_voice import ElevenLabsVoice,VoiceConfig
        voice=ElevenLabsVoice(folder,VoiceConfig.from_env('zoya'))
        actual=voice.plan(job['script'],'narration.mp3')
        if actual!=plan:raise ValueError('Voice settings changed; fresh approval needed')
        # Isolate this exact approved business request; retain daily business limits.
        child=subprocess.run([sys.executable,str(ROOT/'approved_promo_voice.py'),job_id,expected_hash],capture_output=True,text=True,timeout=70)
        if child.returncode:
            import re
            match=re.search(r'HTTP (\d{3})',child.stderr)
            reason='provider_http_'+match[1] if match else 'provider_quota_insufficient' if 'quota insufficient' in child.stderr.lower() else 'approved_tts_failed'
            with connection(DB) as db:db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps({'reason':reason,'automatic_retry':False}),job_id))
            raise ValueError('Approved business TTS stopped: '+reason+'; no automatic retry')
        audio=json.loads(child.stdout)
        render_plan={'audio':audio['relative_path'],'audio_sha256':hashlib.sha256(Path(audio['path']).read_bytes()).hexdigest(),'width':3840,'height':2160,'visuals':'generated brand text cards, not product footage','upload_allowed':False}
        render_plan['hash']=hashlib.sha256(json.dumps(render_plan,sort_keys=True).encode()).hexdigest()
        with connection(DB) as db:db.execute("UPDATE jobs SET status='awaiting_render_approval',audio=?,render_plan=? WHERE id=?",(json.dumps(audio),json.dumps(render_plan),job_id))
    except Exception as e:
        with connection(DB) as db:db.execute("UPDATE jobs SET status='needs_review',result=CASE WHEN result='' THEN ? ELSE result END WHERE id=?",(json.dumps({'error':type(e).__name__,'automatic_retry':False}),job_id))
        raise
    return get(job_id)

def approve_render(job_id,expected_hash):
    job=get(job_id);plan=json.loads(job['render_plan'])
    if plan['hash']!=expected_hash:raise ValueError('Render approval hash mismatch')
    with connection(DB) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("UPDATE jobs SET status='render_running' WHERE id=? AND status='awaiting_render_approval'",(job_id,)).rowcount!=1:raise ValueError('Render not awaiting approval')
    folder=ROOT/'workspaces/Zoya/promo'/job_id
    try:
        audio=json.loads(job['audio']);path=folder/audio['relative_path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=plan['audio_sha256']:raise ValueError('Approved audio changed')
        from video_render import render_video,RenderConfig,probe_media
        duration=probe_media(path)['duration'];ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:raise ValueError('FFmpeg unavailable')
        subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','color=c=0x123844:s=1280x720:r=30','-t','3','-c:v','libx264','-threads','2',str(folder/'visual.mp4')],check=True,timeout=30,capture_output=True)
        def timestamp(s):
            ms=int(s*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
        sentences=[s.strip()+'.' for s in job['script'].split('.') if s.strip()]
        captions='\n\n'.join(f'{i+1}\n{timestamp(i*duration/len(sentences))} --> {timestamp((i+1)*duration/len(sentences))}\n{sentence}' for i,sentence in enumerate(sentences))
        (folder/'captions.srt').write_text(captions,encoding='utf-8')
        result=render_video(folder,['visual.mp4'],audio['relative_path'],'promo-preview.mp4',approved=True,subtitles='captions.srt',config=RenderConfig(width=plan['width'],height=plan['height']))
        result.update(visuals='brand text-card preview; product footage not supplied',market_validated=False,uploaded=False)
        with connection(DB) as db:db.execute("UPDATE jobs SET status='render_complete',result=? WHERE id=?",(json.dumps(result),job_id))
    except Exception as e:
        with connection(DB) as db:db.execute("UPDATE jobs SET status='needs_review',result=? WHERE id=?",(json.dumps({'error':type(e).__name__,'automatic_retry':False}),job_id))
        raise
    return get(job_id)
