"""Local chat only. No task execution, tools, cloud text fallback or publishing."""
import json, os, threading, time, uuid, http.client
from pathlib import Path
from runtime import clean

class Conversation:
    def __init__(self, engine, voice_session):
        self.engine=engine;self.voice=voice_session;self.lock=threading.Lock()
        with engine.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS chat_turns(id TEXT PRIMARY KEY,message TEXT,voice INTEGER,status TEXT,result TEXT,created REAL)')
            c.execute("UPDATE chat_turns SET status='interrupted' WHERE status='running'")

    def reply(self, message, request_id, voice=False):
        if not isinstance(message,str) or not 1<=len(message.strip())<=2000:raise ValueError('Message must contain 1–2000 characters')
        try:uuid.UUID(request_id)
        except (ValueError,TypeError,AttributeError):raise ValueError('Conversation request ID must be a UUID')
        if type(voice) is not bool:raise ValueError('Invalid voice option')
        message=clean(message.strip())
        if not self.lock.acquire(blocking=False):raise ValueError('Zoya is answering; wait before another message')
        turn_id='conv_'+request_id
        try:
            with self.engine.db() as c:
                old=c.execute('SELECT * FROM chat_turns WHERE id=?',(turn_id,)).fetchone()
                if old:
                    if old['message']!=message or bool(old['voice'])!=voice:raise ValueError('Request ID reused with changed message')
                    if old['status']=='completed':return json.loads(old['result'])
                    raise ValueError('Prior attempt interrupted or unresolved; no automatic repeat')
                c.execute('INSERT INTO chat_turns VALUES(?,?,?,?,?,?)',(turn_id,message,int(voice),'running','',time.time()))
                c.execute('INSERT INTO conversations(time,channel,role,task_id,text) VALUES(?,?,?,?,?)',(time.time(),'Zoya','user',turn_id,message))
                history=[dict(r) for r in c.execute("SELECT role,text FROM conversations WHERE task_id LIKE 'conv_%' AND task_id!=? ORDER BY seq DESC LIMIT 4",(turn_id,))][::-1]
            try:
                if getattr(self.engine.adapter,'provider',None)!='ollama':raise ValueError('Conversation requires configured local Ollama; cloud text fallback disabled')
                from conversation_style import SYSTEM, clean_reply, activity_reply, support_reply, quick_reply
                activity=activity_reply(self.engine.root,message) or support_reply(message) or quick_reply(message)
                if activity:
                    generation={'text':activity,'usage':{}}
                else:
                    generation=self.engine.adapter.generate(SYSTEM,{'command':message,'conversation':history,'writing_voice':'','conversation_mode':True},os.environ['ZOYA_OLLAMA_MODEL'],260)
                text=clean_reply(clean(generation['text']))
                with self.engine.db() as c:
                    c.execute('INSERT INTO conversations(time,channel,role,task_id,text) VALUES(?,?,?,?,?)',(time.time(),'Zoya','assistant',turn_id,text))
                result={'id':turn_id,'reply':text,'provider':'local','audio_id':None,'voice_error':None,'usage':generation.get('usage',{})}
                if voice:
                    try:
                        self.voice_preflight()
                        p=self.voice.plan(text)
                        audio=self.voice.approve(p['id'],p['hash'],conversation_scope=True)
                        result['audio_id']=audio['id']
                    except Exception as e:
                        result['voice_error']=str(e) if isinstance(e,ValueError) else 'Voice unavailable; text reply is saved'
                with self.engine.db() as c:c.execute("UPDATE chat_turns SET status='completed',result=? WHERE id=?",(json.dumps(result),turn_id))
                return result
            except Exception as e:
                with self.engine.db() as c:c.execute("UPDATE chat_turns SET status='failed' WHERE id=?",(turn_id,))
                raise ValueError('Local conversation failed: '+type(e).__name__+'; no paid fallback') from None
        finally:self.lock.release()

    def voice_preflight(self):
        # Read-only permission/tier check BEFORE any billed voice request/reservation.
        from natural_voice import VoiceConfig
        config=VoiceConfig.from_env('zoya');config.validate()
        key=os.environ.get('ELEVENLABS_API_KEY','')
        if not key:raise ValueError('Saved ElevenLabs API key missing')
        category=None
        for endpoint in ('/v1/voices/'+config.voice_id,'/v1/user/subscription'):
            c=http.client.HTTPSConnection('api.elevenlabs.io',timeout=12)
            try:
                c.request('GET',endpoint,headers={'xi-api-key':key})
                r=c.getresponse();raw=r.read(200001)
                if len(raw)>200000:raise ValueError('Voice verification response too large')
                if r.status!=200:raise ValueError('Voice verification HTTP '+str(r.status)+'; check Voices/User Read permissions and account access')
                data=json.loads(raw)
                if not endpoint.endswith('subscription'):category=data.get('category')
                if endpoint.endswith('subscription') and data.get('tier')=='free' and category not in ('premade','default'):
                    raise ValueError('Free ElevenLabs plan: selected library voice needs an eligible plan; voice not generated')
            finally:c.close()
