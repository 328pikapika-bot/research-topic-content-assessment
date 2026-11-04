"""Register only tasks already submitted through the actual login form."""
import json
import argparse
from pathlib import Path
import requests

OUT = Path(__file__).resolve().parents[1] / '.tmp/qa/reader'
parser = argparse.ArgumentParser()
parser.add_argument('--base', default='http://127.0.0.1:17627')
parser.add_argument('--since', default='2026-10-14T12:28')
parser.add_argument('--revision', default='scholarly')
args = parser.parse_args()
base = args.base
session = requests.Session(); session.headers['X-User-Scope'] = 'platform_admin'
cases = json.loads((OUT/'选题案例与预期.json').read_text(encoding='utf-8'))
records = json.loads((OUT/'任务清单.json').read_text(encoding='utf-8'))
for agent in ('topic-selection', 'content-assessment'):
    response = session.get(base+'/v1/'+agent+'/tasks', params={'page_size':30}, timeout=30)
    response.raise_for_status()
    for item in response.json()['items']:
        if item['created_at'] < args.since or any(r['task_id']==item['task_id'] for r in records):
            continue
        response = session.get(base+'/v1/'+agent+'/tasks/'+item['task_id'],timeout=30)
        response.raise_for_status(); data=response.json();data=data.get('data',data)
        state=data['state']; raw=data.get('raw_user_input') or state['raw_user_input']
        if agent=='topic-selection':
            case = next((c['id'] for c in cases if c['input']==raw),None)
        else:
            case=state['report_preferences']['artifact_type']
        if not case: continue
        record={'case':case+'_'+args.revision,'task_id':item['task_id'],'agent':agent,'base':base,
                'entry':'actual admin login form, ordinary user submission','created_at':item['created_at']}
        records.append(record)
        folder=OUT/'任务记录'/item['task_id'];folder.mkdir(exist_ok=True,parents=True)
        (folder/'submitted-material.txt').write_text(raw,encoding='utf-8')
        print(json.dumps(record,ensure_ascii=False))
(OUT/'任务清单.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
