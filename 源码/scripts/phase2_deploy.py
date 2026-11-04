"""Inspect/update only IMAGE_TAG on the existing test Stack; never print secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp' / 'qa' / 'phase2-deployment'
OUT.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument('--tag')
args = parser.parse_args()
skill = Path('C:/Users/NSTL/.codex/skills/deploy-reference/SKILL.md').read_text(encoding='utf-8')
username = re.search(r'username:\s*([^\s]+)', skill)[1]
password = re.search(r'password:\s*([^\s]+)', skill)[1]
base = 'http://127.0.0.1:39000/api'
session = requests.Session()
def refresh_csrf(response, *args, **kwargs):
    if response.headers.get('X-Csrf-Token'):
        session.headers['X-CSRF-Token'] = response.headers['X-Csrf-Token']
session.hooks['response'].append(refresh_csrf)
landing = session.get(base.removesuffix('/api')+'/', timeout=30)
landing.raise_for_status()
if landing.headers.get('X-Csrf-Token'):
    session.headers['X-CSRF-Token'] = landing.headers['X-Csrf-Token']
auth = session.post(base+'/auth',json={'Username':username,'Password':password},timeout=30);auth.raise_for_status()
session.headers['Authorization']='Bearer '+auth.json()['jwt']
stack = session.get(base+'/stacks/334',timeout=30);stack.raise_for_status(); stack=stack.json()
content = session.get(base+'/stacks/334/file',timeout=30);content.raise_for_status(); content=content.json()['StackFileContent']
env = stack['Env']
tag = next((item['value'] for item in env if item['name']=='IMAGE_TAG'), '')
services = session.get(base+'/endpoints/21/docker/containers/json',params={'all':1},timeout=30);services.raise_for_status()
current = [{'name':s['Names'],'image':s['Image'],'state':s['State'],'status':s['Status']} for s in services.json() if any('content-assessment' in name for name in s['Names'])]
summary={'stack_id':334,'endpoint_id':stack['EndpointId'],'name':stack['Name'],'tag':tag,'env_count':len(env),'stack_sha256':hashlib.sha256(content.encode()).hexdigest(),'services':current}
if not args.tag:
    OUT.joinpath('baseline.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    # Original confidential config remains only in an ignored local backup.
    tmp=ROOT/'.tmp';tmp.mkdir(exist_ok=True)
    tmp.joinpath('phase2-stack-before.json').write_text(json.dumps({'stack':stack,'content':content},ensure_ascii=False),encoding='utf-8')
else:
    original=json.loads((ROOT/'.tmp/phase2-stack-before.json').read_text(encoding='utf-8'))
    if original['content']!=content or [i for i in original['stack']['Env'] if i['name']!='IMAGE_TAG'] != [i for i in env if i['name']!='IMAGE_TAG']:
        raise RuntimeError('Existing stack/config changed during implementation; inspect before redeploy')
    updated=[{**item,'value':args.tag} if item['name']=='IMAGE_TAG' else item for item in env]
    if tag != args.tag:
        response=session.put(base+'/stacks/334',params={'endpointId':21},json={'StackFileContent':content,'Env':updated,'Prune':False,'PullImage':True},timeout=180)
        response.raise_for_status()
    summary.update({'tag':args.tag,'rollback_tag':original['stack']['Env'][next(i for i,x in enumerate(original['stack']['Env']) if x['name']=='IMAGE_TAG')]['value'],'configuration_preserved':True})
    OUT.joinpath('release.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
