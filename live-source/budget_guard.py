"""Local paid-drafting reservations. Bytes are input-size units, NOT token counts or USD."""
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3

def load_policy(root):
    path=Path(root)/'config/economy-policy.json'
    if not path.exists():return
    p=json.loads(path.read_text(encoding='utf-8'))
    if type(p.get('free_mode')) is not bool:raise ValueError('Invalid economy policy')
    os.environ['ZOYA_FREE_MODE']='1' if p['free_mode'] else '0'
    os.environ['ZOYA_BUDGET_DB']=str(Path(root)/'data/voice-budget.sqlite3')
    for key in ('ZOYA_MAX_CALLS_DAY','ZOYA_MAX_OUTPUT_TOKENS','ZOYA_MAX_INPUT_BYTES_CALL','ZOYA_MAX_INPUT_BYTES_DAY','ZOYA_MAX_OUTPUT_TOKENS_DAY','ZOYA_MAX_VOICE_CALLS_DAY','ZOYA_MAX_VOICE_CHARS_DAY'):
        value=p.get(key)
        if type(value) is not int or value<0:raise ValueError('Invalid economy limit '+key)
        os.environ[key]=str(value)
    if p['free_mode']:
        os.environ['ZOYA_ALLOW_PAID']='0'
        os.environ['ZOYA_TEXT_PROVIDER']='ollama'
        os.environ['ZOYA_OLLAMA_MODEL']=p['local_model']
        os.environ['ZOYA_OLLAMA_DIGEST']=p['local_digest']

def conversation_voice_allowed():
    db=os.environ.get('ZOYA_BUDGET_DB','')
    if not db:return False
    policy=Path(db).parent.parent/'config/economy-policy.json'
    return json.loads(policy.read_text(encoding='utf-8')).get('allow_conversation_voice') is True

def reserve_voice(text,voice_id,model_id,conversation_scope=False):
    # Conversation has no application daily cap. Provider quotas still apply.
    if conversation_scope is True and conversation_voice_allowed():
        path=os.environ.get('ZOYA_BUDGET_DB')
        policy=json.loads((Path(path).parent.parent/'config/economy-policy.json').read_text(encoding='utf-8'))
        if policy.get('conversation_voice_unlimited') is True:
            day=datetime.now(timezone(timedelta(hours=3))).date().isoformat()
            key=hashlib.sha256(json.dumps([text,voice_id,model_id],ensure_ascii=False).encode()).hexdigest()
            from contextlib import closing
            with closing(sqlite3.connect(path,timeout=10)) as c:
                c.execute('CREATE TABLE IF NOT EXISTS conversation_voice_usage(day TEXT,hash TEXT,characters INTEGER)')
                c.execute('INSERT INTO conversation_voice_usage VALUES(?,?,?)',(day,key,len(text)))
                c.commit()
            return
    if (os.environ.get('ZOYA_FREE_MODE')=='1' or os.environ.get('ZOYA_ALLOW_PAID','0')!='1') and not (conversation_scope is True and conversation_voice_allowed()):
        raise ValueError('Economy mode: paid voice blocked. Saved voice credentials are preserved.')
    path=os.environ.get('ZOYA_BUDGET_DB')
    if not path:raise ValueError('Shared voice budget ledger not configured')
    day=datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    key=hashlib.sha256(json.dumps([text,voice_id,model_id],ensure_ascii=False).encode()).hexdigest()
    c=sqlite3.connect(path,timeout=10)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('CREATE TABLE IF NOT EXISTS reservations(day TEXT,hash TEXT,characters INTEGER,UNIQUE(day,hash))')
            if c.execute('SELECT 1 FROM reservations WHERE day=? AND hash=?',(day,key)).fetchone():
                raise ValueError('Duplicate paid voice blocked; reuse audio or reconcile the prior attempt.')
            calls,chars=c.execute('SELECT COUNT(*),COALESCE(SUM(characters),0) FROM reservations WHERE day=?',(day,)).fetchone()
            if calls>=limit('ZOYA_MAX_VOICE_CALLS_DAY',2) or chars+len(text)>limit('ZOYA_MAX_VOICE_CHARS_DAY',1000):
                raise ValueError('Daily paid voice allowance reached (characters, not provider credits).')
            c.execute('INSERT INTO reservations VALUES(?,?,?)',(day,key,len(text)))
    finally:c.close()

def limit(name, default):
    value = int(os.environ.get(name, str(default)))
    if value < 0:
        raise ValueError('Budget limits must be nonnegative')
    return value

def reserve_paid(connection, task_id, snapshot):
    # Caller holds BEGIN IMMEDIATE: multiple workers share a single durable cap.
    connection.execute('''CREATE TABLE IF NOT EXISTS spend_reservations(
        task_id TEXT PRIMARY KEY, day TEXT, request_hash TEXT,
        input_bytes INTEGER, output_reserved INTEGER,
        UNIQUE(day, request_hash))''')
    day = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    payload = json.dumps({'system': snapshot['system'], 'payload': snapshot['payload'],
                         'model': snapshot['model']}, ensure_ascii=False, sort_keys=True).encode('utf-8')
    size, output = len(payload), snapshot['max_tokens']
    if size > limit('ZOYA_MAX_INPUT_BYTES_CALL', 16000):
        raise ValueError('Input-size budget reached; shorten evidence/history or use local mode.')
    if output > limit('ZOYA_MAX_OUTPUT_TOKENS', 1200):
        raise ValueError('Output limit changed; prepare a new approval.')
    key = hashlib.sha256(payload).hexdigest()
    if connection.execute('SELECT 1 FROM spend_reservations WHERE day=? AND request_hash=?', (day,key)).fetchone():
        raise ValueError('Duplicate paid request blocked; reuse the prior result or review its uncertain outcome.')
    calls, used_input, used_output = connection.execute(
        'SELECT COUNT(*),COALESCE(SUM(input_bytes),0),COALESCE(SUM(output_reserved),0) FROM spend_reservations WHERE day=?', (day,)).fetchone()
    if calls >= limit('ZOYA_MAX_CALLS_DAY', 2):
        raise ValueError('Daily paid-call budget reached. Free/local tasks remain available.')
    if used_input + size > limit('ZOYA_MAX_INPUT_BYTES_DAY', 32000):
        raise ValueError('Daily input-size budget reached.')
    if used_output + output > limit('ZOYA_MAX_OUTPUT_TOKENS_DAY', 2400):
        raise ValueError('Daily reserved-output budget reached.')
    connection.execute('INSERT INTO spend_reservations VALUES(?,?,?,?,?)', (task_id,day,key,size,output))
    # Reservations remain consumed on network failure/cancellation: no automatic rebilling.
