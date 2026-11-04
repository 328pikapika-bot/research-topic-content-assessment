"""Verify existing frozen reports without rewriting historical task data."""
import hashlib
import json
from pathlib import Path
import requests
import argparse

parser=argparse.ArgumentParser(); parser.add_argument('--base',default='http://127.0.0.1:17636'); args=parser.parse_args()

OUT = Path(__file__).resolve().parents[1] / '.tmp/qa/reader'
session = requests.Session()
session.headers['X-User-Scope'] = 'platform_admin'
results = []
for agent, tid in [('topic-selection', 'run_dde64f5deee3'), ('content-assessment', 'run_94c469cba75c')]:
    url = f'{args.base}/v1/{agent}/tasks/{tid}'
    def get():
        response = session.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('data', data)
    before = get()
    snapshot = before['state'].get('report_snapshot')
    if not snapshot:
        results.append({'agent': agent, 'task_id': tid, 'result': 'no_snapshot_read_only', 'created_at': before['created_at']})
        continue
    digest = lambda obj: hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    exports = {}
    folder = OUT / '历史兼容' / tid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'snapshot-before.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    for fmt in ['md', 'html', 'docx', 'pdf']:
        response = session.get(url + '/export', params={'format': fmt}, timeout=240)
        response.raise_for_status()
        (folder / f'report.{fmt}').write_bytes(response.content)
        exports[fmt] = hashlib.sha256(response.content).hexdigest()
    after = get()
    (folder / 'snapshot-after.json').write_text(json.dumps(after['state'].get('report_snapshot'), ensure_ascii=False, indent=2), encoding='utf-8')
    stable = digest(snapshot) == digest(after['state'].get('report_snapshot'))
    assert stable, f'Historical frozen snapshot changed: {tid}'
    results.append({'agent': agent, 'task_id': tid, 'created_at': before['created_at'],
                    'template': snapshot.get('template_version'), 'snapshot_sha256': digest(snapshot),
                    'snapshot_unchanged_after_all_exports': stable, 'exports_sha256': exports})
(OUT / '历史任务核验.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False))
