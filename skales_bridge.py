"""Authenticated, local-only Skales handoff. Persist uncertainty; never resubmit on timeout."""
import json,sqlite3,time,uuid
from skales_event_validation import classify_events
from pathlib import Path
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parent
BASE='http://127.0.0.1:3000'
def request(path,body=None,method=None):
 config=json.loads((Path.home()/'.skales-data/devkit/devkit.json').read_text())
 token=config['api']['token']
 return urlopen(Request(BASE+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=method),timeout=120)
def dispatch(message, required_tools=()):
 task_id=str(uuid.uuid4());db=sqlite3.connect(ROOT/'data/desktop-tasks.sqlite3',timeout=10)
 try:
  db.execute('CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY,command TEXT,status TEXT,result TEXT,created REAL)')
  db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)',(task_id,message,'submitted_uncertain','',time.time()));db.commit()
  if tuple(required_tools)!=('get_system_info',):
   result={'id':task_id,'status':'needs_review','reason':'external_tool_scope_unverified','text':'External Skales tasks are blocked until a verified tool permission scope is configured. Local approved promo drafting/rendering remains available.','automatic_retry':False}
   db.execute('UPDATE handoffs SET status=?,result=? WHERE id=?',(result['status'],json.dumps(result),task_id));db.commit();return result
  with request('/api/cli/status') as r:status=json.load(r)
  if status.get('provider')!='ollama':
   db.execute('UPDATE handoffs SET status=? WHERE id=?',('awaiting_local_provider',task_id));db.commit()
   return {'id':task_id,'status':'awaiting_local_provider','text':'Task saved; local Skales model must be configured. No paid request sent.'}
  if required_tools:
   with request('/api/cli/tools') as r:registry=json.load(r)
   enabled={t.get('name') for t in registry.get('tools',[]) if t.get('enabled') is True}
   if set(required_tools)-enabled:
    result={'id':task_id,'status':'needs_review','reason':'required_tool_unavailable','text':'Required tool is unavailable or disabled; no chat task sent.'}
    db.execute('UPDATE handoffs SET status=?,result=? WHERE id=?',(result['status'],json.dumps(result),task_id));db.commit();return result
  events=[];text=[];done=False
  with request('/api/cli/chat',{'message':'Zoya user-approved task: '+message+'\nUse local models. Explain target and plan before actions. Keep existing folder and publishing permissions. Do not repeat failed operations. Return actual results and unresolved dependencies.'}) as r:
   for raw in r:
    if not raw.startswith(b'data:'):continue
    event=json.loads(raw[5:].strip());events.append(event)
    if event.get('type')=='text':text.append(event.get('content',''))
    if event.get('type') in ('error','tool_error'):break
    if event.get('type')=='done':done=True;break
  no_tools=any('produced without tools' in chunk.lower() for chunk in text)
  result={'id':task_id,'text':'\n'.join(text),'events':events,'status':'reply_received' if done and not no_tools else 'needs_review','tool_execution_verified':False}
  result.update(classify_events(events,required_tools))
  if no_tools:result['reason']='local_model_tool_timeout'
  db.execute('UPDATE handoffs SET status=?,result=? WHERE id=?',(result['status'],json.dumps(result),task_id));db.commit();return result
 except Exception as e:
  db.execute('UPDATE handoffs SET status=?,result=? WHERE id=?',('needs_review',json.dumps({'error':type(e).__name__,'automatic_retry':False}),task_id));db.commit()
  return {'id':task_id,'status':'needs_review','text':'Task retained; failed handoff needs reconciliation. No automatic retry.'}
 finally:db.close()