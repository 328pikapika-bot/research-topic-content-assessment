"""Read deployed image identity, source hashes, health and historical snapshots."""
import hashlib
import json
from pathlib import Path
import re
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp/qa/phase2'
manifest = json.loads((OUT / '源码与构建清单.json').read_text(encoding='utf-8'))
skill = Path('C:/Users/NSTL/.codex/skills/deploy-reference/SKILL.md').read_text(encoding='utf-8')
session = requests.Session()
def refresh_csrf(response, *args, **kwargs):
    if response.headers.get('X-Csrf-Token'):
        session.headers['X-CSRF-Token'] = response.headers['X-Csrf-Token']
session.hooks['response'].append(refresh_csrf)
base = 'http://127.0.0.1:39000/api'
landing = session.get(base.removesuffix('/api')+'/', timeout=30); landing.raise_for_status()
if landing.headers.get('X-Csrf-Token'):
    session.headers['X-CSRF-Token'] = landing.headers['X-Csrf-Token']
auth = session.post(base+'/auth', json={'Username':re.search(r'username:\s*([^\s]+)',skill)[1],
    'Password':re.search(r'password:\s*([^\s]+)',skill)[1]}, timeout=30)
auth.raise_for_status(); session.headers['Authorization'] = 'Bearer ' + auth.json()['jwt']
response = session.get(base+'/endpoints/21/docker/containers/json', params={'all':1}, timeout=30)
response.raise_for_status()
containers = [c for c in response.json() if any('content-assessment-' in n for n in c['Names'])]
summary = {'tag':manifest['tag'], 'source_manifest_sha256':manifest['source_manifest_sha256'], 'containers':[]}
for c in containers:
    response = session.get(base+'/endpoints/21/docker/containers/'+c['Id']+'/json', timeout=30)
    response.raise_for_status(); info = response.json()
    entry = {'name':info['Name'], 'image':info['Config']['Image'], 'image_id':info['Image'],
        'health':info['State'].get('Health',{}).get('Status'), 'running':info['State']['Running'],
        'revision':info['Config'].get('Labels',{}).get('org.opencontainers.image.revision')}
    assert entry['image'].endswith(':'+manifest['tag']) and entry['revision']==manifest['source_manifest_sha256'], entry
    assert entry['running'] and entry['health']=='healthy', entry
    summary['containers'].append(entry)
    if 'backend' in info['Name']:
        expected = {x['path']:x['sha256'] for x in manifest['files'] if x['path'].startswith('backend/app/')}
        program = "import hashlib,json; paths="+repr(list(expected))+"; print(json.dumps({p:hashlib.sha256(open('/app/'+p,'rb').read()).hexdigest() for p in paths}))"
        response=session.post(base+'/endpoints/21/docker/containers/'+c['Id']+'/exec',
            json={'AttachStdout':True,'AttachStderr':True,'Cmd':['python','-c',program]}, timeout=30)
        response.raise_for_status(); eid=response.json()['Id']
        response=session.post(base+'/endpoints/21/docker/exec/'+eid+'/start',json={'Detach':False,'Tty':False},timeout=30)
        response.raise_for_status(); data=response.content; chunks=[]
        while len(data)>=8 and data[0] in (0,1,2):
            size=int.from_bytes(data[4:8],'big');chunks.append(data[8:8+size]);data=data[8+size:]
        actual=json.loads((b''.join(chunks) or response.content).decode())
        assert actual==expected, 'Deployed backend source differs from manifest'
        summary['backend_source_files_verified']=len(actual)
public=requests.Session();public.headers['X-User-Scope']='platform_admin'
response=public.get('http://127.0.0.1:18100/',timeout=30);response.raise_for_status()
assets=re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"',response.text)
summary['frontend_assets']=[]
for path in assets:
    downloaded=public.get('http://127.0.0.1:18100'+path,timeout=30); downloaded.raise_for_status()
    name='frontend/web/dist/'+path.split('/ai/agent/content_assessment/',1)[-1].lstrip('/')
    expected=next(x['sha256'] for x in manifest['files'] if x['path']==name)
    actual=hashlib.sha256(downloaded.content).hexdigest();assert actual==expected
    summary['frontend_assets'].append({'path':path,'sha256':actual})
response=public.get('http://127.0.0.1:17613/system/health',timeout=30);response.raise_for_status()
summary['health_http_status']=response.status_code
summary['historical_reports']=[]
for agent,tid in [('topic-selection','run_dde64f5deee3'),('content-assessment','run_94c469cba75c')]:
    url='http://127.0.0.1:17613/v1/'+agent+'/tasks/'+tid
    def snapshot():
        r=public.get(url,timeout=30);r.raise_for_status();d=r.json();return d.get('data',d)['state']['report_snapshot']
    before=snapshot();r=public.get(url+'/export',params={'format':'md'},timeout=240);r.raise_for_status();after=snapshot()
    assert before==after
    summary['historical_reports'].append({'task_id':tid,'template':before['template_version'],'unchanged':True})
(OUT/'部署复验.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
