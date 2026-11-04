"""Read UI-created tasks and verify frozen reports through the public APIs."""
import argparse
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / '.tmp' / 'qa' / 'phase2'
parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:17617');parser.add_argument('--export',action='store_true');parser.add_argument('--latest',action='store_true');parser.add_argument('--manifest',type=Path);parser.add_argument('--out',type=Path);args=parser.parse_args()
OUT = args.out or OUT
OUT.mkdir(parents=True, exist_ok=True)
formal = json.loads(args.manifest.read_text(encoding='utf-8')) if args.manifest else []
formal_ids = {item['task_id'] for item in formal}
session=requests.Session();session.headers['X-User-Scope']='platform_admin'
cases=json.loads((ROOT/'资料/测试输入/平台验收/选题案例与预期.json').read_text(encoding='utf-8'))
results=[]
seen=set()
for agent in ['topic-selection','content-assessment']:
    response=session.get(args.base+f'/v1/{agent}/tasks',params={'page_size':100},timeout=30);response.raise_for_status()
    items=response.json().get('items') or response.json().get('data',{}).get('items',[])
    for item in items:
        if not str(item.get('created_at','')).startswith('2026-10-14'):
            continue
        tid=item['task_id']
        if formal_ids and tid not in formal_ids: continue
        response=session.get(args.base+f'/v1/{agent}/tasks/{tid}',timeout=30);response.raise_for_status()
        body=response.json();data=body.get('data',body)
        state=data.get('state') or {}
        name=next((c['id'] for c in cases if c['input']==data.get('raw_user_input')),None) if agent=='topic-selection' else state.get('report_preferences',{}).get('resolved_artifact_type')
        if not name: continue
        key=(agent,name)
        if args.latest and key in seen: continue
        seen.add(key)
        folder=OUT/'任务记录'/tid;folder.mkdir(parents=True,exist_ok=True)
        folder.joinpath('state.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        entry={'task_id':tid,'agent':agent,'case':name,'status':data.get('status'),'stage':data.get('current_stage'),'error':data.get('error_message'),'template':(state.get('report_snapshot') or {}).get('template_version') or state.get('report_plan',{}).get('template_version'),'confirmation':state.get('confirmation')}
        if agent=='topic-selection':
            entry.update(evidence=len(state.get('evidence_pool',[])),methods=sum(bool(x.get('method_evidence')) for x in state.get('crosslinks',[])),candidates=len(state.get('candidates',[])),page_metrics=state.get('report_plan',{}).get('page_metrics'))
            evidence={r['evidence_id']:r for r in state.get('evidence_pool',[])}
            errors=[]
            primary_errors=[]
            for relation in state.get('crosslinks',[]):
                primary=relation.get('source_method') or relation.get('transferable_method') or ''
                clean=lambda value:''.join(str(value).split()).casefold()
                if relation.get('status')=='validated' and not any(
                    clean(source.get('proposed_method_name',''))==clean(primary)
                    for source in relation.get('method_evidence',[])):
                    primary_errors.append(relation['crosslink_id'])
                for source in relation.get('method_evidence',[]):
                    eid=source['evidence_id'];quote=source['quote'];normalize=lambda value:''.join(str(value).split()).casefold()
                    texts=[evidence.get(eid,{}).get('abstract','')]+[c.get('text','') for c in state.get('evidence_chunks',[]) if c.get('evidence_id')==eid]
                    if not any(normalize(quote) in normalize(text) for text in texts): errors.append(eid)
            entry['invalid_method_quotes']=errors
            entry['primary_method_mismatches']=primary_errors
        else:
            entry.update(claims=len(state.get('claims',[])),evidence=len(state.get('evidence_records',[])),attachments=len(state.get('uploaded_files',[])))
        if args.export and data.get('status')=='completed':
            entry['exports']={}
            for fmt in ['md','html','docx','pdf']:
                path=folder/f'report.{fmt}'
                if not path.exists():
                    download=session.get(args.base+f'/v1/{agent}/tasks/{tid}/export',params={'format':fmt},timeout=240)
                    if not download.ok:
                        entry['exports'][fmt]={'http_status':download.status_code,'reason':download.text[:300]};continue
                    path.write_bytes(download.content)
                content=path.read_bytes()
                entry['exports'][fmt]={'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
        results.append(entry)
OUT.joinpath('正式任务核验.json' if formal_ids else '实际任务核验.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{k:v for k,v in x.items() if k not in {'confirmation','exports','page_metrics'}} for x in results],ensure_ascii=False))
