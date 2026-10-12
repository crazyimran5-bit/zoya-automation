"""Inspect, stage, compile and checkpoint before scoped Windows source changes."""
import argparse
import ast
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPERS = ('voice_pipeline_hardening.py', 'skales_event_validation.py', 'queue_sync.py')

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Source shape differs; stop for inspection: ' + old[:60])
    return text.replace(old, new, 1)

def method(tree, cls, name):
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(n for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)

def replace_node(text, node, replacement):
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:node.lineno-1]) + replacement.rstrip() + '\n' + ''.join(lines[node.end_lineno:])

def patch_voice(text):
    if 'from voice_pipeline_hardening import' in text:
        raise ValueError('Voice hardening already present; do not apply twice')
    tree=ast.parse(text)
    env=method(tree,'VoiceConfig','from_env')
    returns=[n for n in ast.walk(env) if isinstance(n,ast.Return)]
    if len(returns)!=1 or not isinstance(returns[0].value,ast.Call) or ast.unparse(returns[0].value.func)!='cls':
        raise ValueError('Unsupported VoiceConfig.from_env')
    if len(returns[0].value.args)!=4:
        raise ValueError('VoiceConfig constructor changed; inspect first')
    ret=returns[0]
    args=[ast.unparse(n) for n in ret.value.args]
    args[2]='MODEL_ID'
    text=replace_node(text,ret,' '*ret.col_offset+'return cls('+', '.join(args)+')')
    plan=method(ast.parse(text),'ElevenLabsVoice','plan')
    bodies=[n for n in ast.walk(plan) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='body' for t in n.targets)]
    if len(bodies)!=1 or not isinstance(bodies[0].value,ast.Dict):
        raise ValueError('Unsupported TTS payload builder; no transport replaced')
    body=bodies[0]
    # Approved hash subsequently includes all exact voice settings and formatted text.
    text=replace_node(text,body,' '*body.col_offset+'body = build_tts_payload(text)')
    tree=ast.parse(text)
    plan=method(tree,'ElevenLabsVoice','plan')
    first=plan.body[0]
    lines=text.splitlines(keepends=True)
    lines.insert(first.lineno-1,' '*first.col_offset+'text = format_tts_text(text)\n')
    text=''.join(lines)
    # Keep imports after the module docstring (there are no __future__ imports in this source).
    text=replace_once(text,'from dataclasses import dataclass','from dataclasses import dataclass\nfrom voice_pipeline_hardening import MODEL_ID, build_tts_payload, format_tts_text')
    ast.parse(text)
    return text

WORKER = ''' def task_worker(self):
  from queue_sync import initialize, claim, finish
  from skales_bridge import dispatch
  from conversation_style import task_report
  path=ROOT/'data/desktop-queue.sqlite3'
  initialize(path)
  while not self.closed.wait(1):
   try:
    row=claim(path)
   except sqlite3.Error as error:
    self.post('status','Queue claim delayed: '+type(error).__name__)
    continue
   if not row:continue
   try:
    result=dispatch(row[1])
   except Exception as error:
    result={'status':'needs_review','reason':type(error).__name__,'automatic_retry':False,'text':'Task needs review; it was not automatically repeated.'}
   try:
    finish(path,row[0],result)
   except sqlite3.Error as error:
    # Stop this worker if outcome cannot be persisted. Restart marks running as needs_review.
    self.post('status','Task outcome pending reconciliation: '+type(error).__name__)
    return
   self.post('Skales result',task_report(result))
'''

def patch_desktop(text):
    if 'from voice_pipeline_hardening import' in text:
        raise ValueError('Desktop hardening already installed')
    if 'Local\\\\ZoyaNativeDesktop' not in text:
        raise ValueError('Single-instance desktop mutex missing; queue recovery unsafe')
    text=replace_once(text,'import tkinter as tk','from voice_pipeline_hardening import normalize_stt, voice_cache_key\nimport tkinter as tk')
    text=replace_once(text,"text=json.loads(recognizer.Result()).get('text','').strip()", "text=normalize_stt(json.loads(recognizer.Result()).get('text',''))")
    text=replace_once(text,"key=hashlib.sha256((voice.config.voice_id+text).encode()).hexdigest()[:32]",'key=voice_cache_key(voice.config.voice_id,text)')
    node=method(ast.parse(text),'Desktop','task_worker')
    text=replace_node(text,node,WORKER)
    ast.parse(text)
    return text

def patch_bridge(text):
    text=replace_once(text,'def dispatch(message):','def dispatch(message, required_tools=()):')
    text=replace_once(text,'import json,sqlite3,time,uuid','import json,sqlite3,time,uuid\nfrom skales_event_validation import classify_events')
    text=replace_once(text,"sqlite3.connect(ROOT/'data/desktop-tasks.sqlite3')","sqlite3.connect(ROOT/'data/desktop-tasks.sqlite3',timeout=10)")
    old="  events=[];text=[];done=False"
    new="""  if required_tools:
   with request('/api/cli/tools') as r:registry=json.load(r)
   enabled={t.get('name') for t in registry.get('tools',[]) if t.get('enabled') is True}
   if set(required_tools)-enabled:
    result={'id':task_id,'status':'needs_review','reason':'required_tool_unavailable','text':'Required tool is unavailable or disabled; no chat task sent.'}
    db.execute('UPDATE handoffs SET status=?,result=? WHERE id=?',(result['status'],json.dumps(result),task_id));db.commit();return result
  events=[];text=[];done=False"""
    text=replace_once(text,old,new)
    tree=ast.parse(text)
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='result' for t in n.targets) and isinstance(n.value,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='events' for k in n.value.keys))
    lines=text.splitlines(keepends=True)
    lines.insert(node.end_lineno,"  result.update(classify_events(events,required_tools))\n")
    text=''.join(lines)
    ast.parse(text)
    return text

def transform(root):
    changes={name:(HERE/name).read_text(encoding='utf-8') for name in HELPERS}
    for name,patch in [('natural_voice.py',patch_voice),('zoya_desktop.py',patch_desktop),('skales_bridge.py',patch_bridge)]:
        changes[name]=patch((root/name).read_text(encoding='utf-8'))
    for name,text in changes.items():compile(text,name,'exec')
    return changes

def apply(root, changes):
    # Never change source beneath a live interpreter. Stop only Zoya desktop/backend first.
    if os.name=='nt':
        command="$p=Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^pythonw?\\.exe$' -and ($_.CommandLine -match 'zoya_desktop\\.py|app\\.py')}; if($p){exit 2}"
        check=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True)
        if check.returncode:raise RuntimeError('Stop Zoya desktop/backend before applying; no files changed')
    stamp=str(time.time_ns());checkpoint=root/'data'/('audit-fix-checkpoint-'+stamp)
    checkpoint.mkdir(parents=True)
    before={name:(root/name).read_bytes() if (root/name).exists() else None for name in changes}
    for name,raw in before.items():
        if raw is not None:(checkpoint/name).write_bytes(raw)
    for name in ('zoya.sqlite3','desktop-queue.sqlite3','desktop-tasks.sqlite3'):
        source=root/'data'/name
        if source.exists():
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src:
                with closing(sqlite3.connect(checkpoint/name)) as dst:src.backup(dst)
    try:
        for name,text in changes.items():(root/name).write_text(text,encoding='utf-8')
    except Exception:
        for name,raw in before.items():
            if raw is None:(root/name).unlink(missing_ok=True)
            else:(root/name).write_bytes(raw)
        raise
    return checkpoint

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();root=args.root.resolve()
    changes=transform(root)
    result={'source_compatible':True,'files':list(changes),'installed':False}
    if args.apply:result.update(installed=True,checkpoint=str(apply(root,changes)),restart_required=True)
    print(json.dumps(result,indent=2))
