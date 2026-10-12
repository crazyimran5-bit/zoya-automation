"""Read-only quota refresh. No subscription changes, purchases, or credential output."""
import http.client,json,os,time
from pathlib import Path
def remaining_characters(root):
 root=Path(root);path=root/'data/elevenlabs-verification.json';settings=root/'data/provider-settings.dpapi'
 revision=settings.stat().st_mtime_ns if settings.exists() else 0
 try:cached=json.loads(path.read_text())
 except (OSError,ValueError):cached={}
 if path.exists() and time.time()-path.stat().st_mtime<60 and cached.get('settings_revision',revision)==revision:
  return cached.get('remaining_characters')
 key=os.environ.get('ELEVENLABS_API_KEY','').strip()
 if not key:return None
 connection=http.client.HTTPSConnection('api.elevenlabs.io',timeout=10)
 try:
  connection.request('GET','/v1/user/subscription',headers={'xi-api-key':key,'Accept':'application/json'})
  response=connection.getresponse()
  if response.status!=200:return None
  data=json.loads(response.read(100000))
  if not isinstance(data.get('character_limit'),int) or not isinstance(data.get('character_count'),int):return None
  remaining=max(0,data['character_limit']-data['character_count'])
  cached.update(remaining_characters=remaining,settings_revision=revision,quota={'character_count':data['character_count'],'character_limit':data['character_limit']},checked_at=time.time(),authentication_verified=True,subscription_http=200)
  path.write_text(json.dumps(cached,indent=2))
  return remaining
 except Exception:return None
 finally:connection.close()
