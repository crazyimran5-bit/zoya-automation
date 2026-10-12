"""Pinned local GGUF model only. No cloud endpoint, tools, downloads or paid fallback."""
import json
import os
import re
import urllib.request

class OllamaAdapter:
    provider='ollama'
    def ready(self):
        return bool(os.environ.get('ZOYA_OLLAMA_MODEL') and re.fullmatch(r'[a-f0-9]{64}',os.environ.get('ZOYA_OLLAMA_DIGEST','')))
    def request(self,path,body=None,timeout=5):
        if path not in ('/api/tags','/api/chat'):raise ValueError('Unsupported local endpoint')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        req=urllib.request.Request('http://127.0.0.1:11434'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(req,timeout=timeout) as r:
            raw=r.read(2000001)
            if len(raw)>2000000:raise ValueError('Local model response too large')
        return json.loads(raw)
    def validate_model(self,models,model,digest):
        found=next((m for m in models if m.get('name')==model),None)
        if not found or found.get('digest')!=digest:raise ValueError('Pinned local model missing or changed')
        if 'cloud' in model.lower() or found.get('remote_host') or found.get('remote_model') or found.get('details',{}).get('format')!='gguf':
            raise ValueError('Only reviewed local GGUF model allowed; cloud model refused')
    def generate(self,system,payload,model,max_tokens):
        if not self.ready() or model!=os.environ.get('ZOYA_OLLAMA_MODEL'):raise ValueError('Exact installed local model and digest must be configured')
        if len(system)+len(json.dumps(payload))>16000:raise ValueError('Local context exceeds 16000-character cap; shorten inputs. No paid fallback.')
        self.validate_model(self.request('/api/tags').get('models',[]),model,os.environ['ZOYA_OLLAMA_DIGEST'])
        messages=[{'role':'system','content':system}]
        if payload.get('conversation_mode') is True:
            messages[0]['content']+='\n'+payload.get('writing_voice','')
            messages.extend({'role':m['role'],'content':m['text']} for m in payload.get('conversation',[]) if m.get('role') in ('user','assistant'))
            messages.append({'role':'user','content':payload['command']})
        else:
            messages.append({'role':'user','content':json.dumps(payload,ensure_ascii=False)})
        body={'model':model,'messages':messages,
            'stream':False,'options':{'num_predict':min(3000,max_tokens),'num_ctx':8192}}
        result=self.request('/api/chat',body,90);message=result.get('message',{})
        if result.get('done') is not True or result.get('done_reason')!='stop' or message.get('tool_calls') or not message.get('content','').strip():
            raise ValueError('Incomplete or unsupported local output; no tools executed and no paid fallback')
        return {'text':message['content'],'usage':{'input_tokens':result.get('prompt_eval_count'),'output_tokens':result.get('eval_count')},'provider_id':'local:'+os.environ['ZOYA_OLLAMA_DIGEST']}
