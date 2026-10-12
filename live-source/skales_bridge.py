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
def dispatch_chat(message, required_tools=()):
 task_id=str(uuid.uuid4());db=sqlite3.connect(ROOT/'data/desktop-tasks.sqlite3',timeout=10)
 try:
  db.execute('CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY,command TEXT,status TEXT,result TEXT,created REAL)')
  db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)',(task_id,message,'submitted_uncertain','',time.time()));db.commit()
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
# Direct tool handoffs keep model prose separate from actual execution evidence.
def subagent_plan(message):
 import hashlib
 if not isinstance(message,str) or not message.strip() or len(message)>12000:raise ValueError('Task must contain 1-12000 characters')
 args={'prompt':message+'\nWork only inside your scratch context. Public research and text drafting only. Never log in, upload, publish, send messages, access office folders, purchase, or execute shell commands. Identify missing evidence explicitly.',
       'tools':['get_system_info','search_web','fetch_web_page'],'model':'same','cwd':'scratch'}
 payload={'name':'run_subagent','arguments':args}
 digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 return {'payload':payload,'hash':digest,'target':'Skales local Ollama / scratch specialist','publishing_allowed':False}
def _agent_db():
 db=sqlite3.connect(ROOT/'data/desktop-tasks.sqlite3',timeout=10)
 db.execute('PRAGMA busy_timeout=10000')
 db.execute('CREATE TABLE IF NOT EXISTS agent_handoffs(id TEXT PRIMARY KEY,command TEXT,hash TEXT,status TEXT,approval TEXT,model TEXT,result TEXT,created REAL)')
 db.commit();return db
def _tool_response(response,task_id,digest):
 tool=response.get('result') or {}
 pending=response.get('status')=='pending'
 verified=response.get('status')=='done' and tool.get('toolName')=='run_subagent' and tool.get('success') is True
 text=response.get('confirmationMessage') if pending else tool.get('displayMessage','')
 return {'id':task_id,'proposal_hash':digest,'status':'awaiting_skales_approval' if pending else 'subagent_result_received' if verified else 'needs_review',
         'approval_id':response.get('approvalId'),'subagent_execution_verified':verified,'business_outcome_verified':False,
         'tool_execution_verified':False,'transport':'authenticated_direct_tool','text':text or 'Skales returned no verified sub-agent result.',
         'raw_result':tool,'automatic_retry':False}
def dispatch(message,required_tools=()):
 plan=subagent_plan(message);task_id=str(uuid.uuid4());db=_agent_db()
 db.execute('INSERT INTO agent_handoffs VALUES(?,?,?,?,?,?,?,?)',(task_id,message,plan['hash'],'submitted_uncertain','','','',time.time()));db.commit()
 try:
  with request('/api/cli/status') as response:status=json.load(response)
  if status.get('provider')!='ollama':
   result={'id':task_id,'status':'awaiting_local_provider','text':'Local Skales provider required; no task was sent.','automatic_retry':False}
  else:
   with request('/api/cli/tools') as response:registry=json.load(response)
   enabled={t.get('name') for t in registry.get('tools',[]) if t.get('enabled') is True}
   if 'run_subagent' not in enabled or set(required_tools)-enabled:
    result={'id':task_id,'status':'needs_review','reason':'required_tool_unavailable','text':'Required Skales tool unavailable; no task sent.','automatic_retry':False}
   else:
    db.execute('UPDATE agent_handoffs SET model=? WHERE id=?',(str(status.get('model','')),task_id));db.commit()
    with request('/api/cli/tools',plan['payload']) as response:result=_tool_response(json.load(response),task_id,plan['hash'])
  db.execute('UPDATE agent_handoffs SET status=?,approval=?,result=? WHERE id=?',(result['status'],result.get('approval_id') or '',json.dumps(result,ensure_ascii=False),task_id));db.commit();return result
 except Exception as error:
  result={'id':task_id,'status':'needs_review','reason':type(error).__name__,'text':'Handoff outcome uncertain; retained for reconciliation. No automatic retry.','automatic_retry':False}
  db.execute('UPDATE agent_handoffs SET status=?,result=? WHERE id=?',(result['status'],json.dumps(result),task_id));db.commit();return result
 finally:db.close()
def approve_handoff(task_id,approved_hash):
 db=_agent_db()
 try:
  db.execute('BEGIN IMMEDIATE')
  row=db.execute('SELECT hash,status,approval,model,created FROM agent_handoffs WHERE id=?',(task_id,)).fetchone()
  if not row or row[0]!=approved_hash or row[1]!='awaiting_skales_approval' or time.time()-row[4]>3600:raise ValueError('Exact current approval required')
  with request('/api/cli/status') as response:status=json.load(response)
  if status.get('provider')!='ollama' or str(status.get('model',''))!=row[3]:raise ValueError('Provider/model changed; prepare a new proposal')
  db.execute("UPDATE agent_handoffs SET status='approval_submitted_uncertain' WHERE id=?",(task_id,));db.commit()
  # Only called after the boss explicitly confirms the displayed immutable handoff.
  with request('/api/cli/tools/approval',{'id':row[2],'decision':'approve'}) as response:result=_tool_response(json.load(response),task_id,approved_hash)
  db.execute('UPDATE agent_handoffs SET status=?,result=? WHERE id=?',(result['status'],json.dumps(result,ensure_ascii=False),task_id));db.commit();return result
 except Exception as error:
  db.rollback()
  # Preserve pending approvals on validation errors; never retry an uncertain submitted approval.
  raise
 finally:db.close()
