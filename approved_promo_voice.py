"""One exact owner-approved business TTS request in an isolated process.

Stored free-text policy and daily business ceilings remain unchanged.
"""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
import local_settings,budget_guard
local_settings.load(ROOT);budget_guard.load_policy(ROOT)
from promo_workflow import get
from natural_voice import VoiceConfig,ElevenLabsVoice

if __name__=='__main__':
    job_id,expected_hash=sys.argv[1:]
    job=get(job_id)
    if job['status']!='voice_running':raise ValueError('No consumed voice approval')
    approved=json.loads(job['voice_plan'])
    if approved['hash']!=expected_hash:raise ValueError('Approval changed')
    voice=ElevenLabsVoice(ROOT/'workspaces/Zoya/promo'/job_id,VoiceConfig.from_env('zoya'))
    if voice.plan(job['script'],'narration.mp3')!=approved:raise ValueError('Voice payload changed')
    # Read-only provider quota check before reserving/billing a new request.
    import http.client
    c=http.client.HTTPSConnection('api.elevenlabs.io',timeout=15)
    try:
        c.request('GET','/v1/user/subscription',headers={'xi-api-key':os.environ['ELEVENLABS_API_KEY']})
        r=c.getresponse();raw=r.read(200001)
        if r.status!=200 or len(raw)>200000:raise ValueError('Provider quota check unavailable; no TTS sent')
        quota=json.loads(raw)
        if quota.get('character_limit',0)-quota.get('character_count',0)<len(approved['payload']['body']['text']):raise ValueError('Provider quota insufficient; no TTS sent')
    finally:c.close()
    # The foreground owner explicitly approved this payload. This child has no text tools.
    os.environ['ZOYA_FREE_MODE']='0';os.environ['ZOYA_ALLOW_PAID']='1'
    print(json.dumps(voice.generate(job['script'],'narration.mp3',expected_hash,conversation_scope=False)))
