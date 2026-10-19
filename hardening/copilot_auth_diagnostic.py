"""Read-only Copilot eligibility diagnostic using a separately configured user token.

Never prints token/response bodies, changes credentials, assigns tasks or purchases a plan.
"""
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def request(path, token, payload=None):
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('Invalid GitHub path')
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
             'X-GitHub-Api-Version':'2022-11-28','User-Agent':'Zoya-Copilot-Diagnostic'}
    if path=='/graphql':headers['GraphQL-Features']='issues_copilot_assignment_api_support,coding_agent_model_selection'
    body=None if payload is None else json.dumps(payload).encode()
    req=Request('https://api.github.com'+path,data=body,headers=headers)
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(req,timeout=15) as response:
            raw=response.read(1_000_001)
            if len(raw)>1_000_000:raise ValueError('Response too large')
            return response.status,json.loads(raw)
    except HTTPError as error:
        return error.code,{}  # Never log API error body or credentials.

def diagnose(token):
    if not token:
        return {'ready':False,'reason':'GH_USER_TOKEN_missing','credentials_changed':False}
    status,_=request('/user',token)
    if status!=200:return {'ready':False,'user_auth_http':status,'reason':'user_token_rejected_or_wrong_token_type','credentials_changed':False}
    status,repo=request('/repos/crazyimran5-bit/zoya-automation',token)
    if status!=200:return {'ready':False,'repository_http':status,'credentials_changed':False}
    query='query { repository(owner:"crazyimran5-bit",name:"zoya-automation") { suggestedActors(capabilities:[CAN_BE_ASSIGNED],first:100) { nodes { login } } } }'
    status,data=request('/graphql',token,{'query':query})
    nodes=(((data.get('data') or {}).get('repository') or {}).get('suggestedActors') or {}).get('nodes',[])
    available=any(n.get('login') in ('copilot-swe-agent','copilot-swe-agent[bot]') for n in nodes)
    return {'ready_for_assignment_check':status==200 and available and not data.get('errors'),
            'user_auth_http':200,'repository_http':200,'graphql_http':status,
            'repository_push':bool(repo.get('permissions',{}).get('push')),
            'copilot_suggested_actor':available,'graphql_error':bool(data.get('errors')),
            'assignment_not_attempted':True,'token_scopes_and_paid_entitlement_not_verified':True,
            'credentials_changed':False}

if __name__=='__main__':print(json.dumps(diagnose(os.environ.get('GH_USER_TOKEN','')),indent=2))
