"""Verify delivery files without changing any source or task snapshot."""
from datetime import datetime
import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

import requests


PROJECT = Path(__file__).resolve().parents[1]
DOCS = PROJECT.parent / '资料/测试反馈/第二期优化'
SOURCE_OUT = DOCS / '第一轮测试优化建议' / '第二期验收'
OUT = PROJECT / '.tmp/qa/phase2'
read = lambda name: json.loads((OUT / name).read_text(encoding='utf-8'))
manifest = read('源码与构建清单.json')
online = read('部署复验.json')
assert manifest['tag'] == online['tag'] == 'v1.2.0-20261014-0354'
source_mismatches = [item['path'] for item in manifest['files']
    if not (PROJECT / item['path']).exists()
    or hashlib.sha256((PROJECT / item['path']).read_bytes()).hexdigest() != item['sha256']]
assert not source_mismatches, source_mismatches

documents = [DOCS / '科技文献智能服务平台_第二期最终交付报告.md',
             DOCS / '会议需求与第二期落实对应表.md',
             SOURCE_OUT / '测试说明.md', SOURCE_OUT / '调研/图形调研与选型记录.md']
checked_links = 0
missing_links = []
for document in documents:
    assert document.is_file(), document
    for target in re.findall(r'\]\(([^\n]+?)\)', document.read_text(encoding='utf-8')):
        target = target.strip('<>')
        if re.match(r'(?:https?://|#|data:)', target):
            continue
        checked_links += 1
        path = document.parent / unquote(target.split('#')[0])
        if not path.exists():
            missing_links.append(str(path))
assert not missing_links, missing_links

checks = {}
for location in ('', '部署复验/'):
    records = read(location + '四格式与分页核验.json')
    assert len(records) == 8 and all(record['passed'] for record in records)
    checks[location or '本地'] = {
        'reports_passed': len(records),
        'topic_body_pages': {record['case']: record['body_pages']
                             for record in records if record['agent'] == 'topic-selection'},
    }
formal = read('正式任务核验.json')
assert len(formal) == 8 and all(record['status'] == 'completed'
    and not record.get('invalid_method_quotes') and not record.get('primary_method_mismatches')
    for record in formal)
charts = read('图形字节一致性核验.json')
assert charts['passed'] and len(charts['records']) == 16
health = requests.get('http://127.0.0.1:17613/system/health', timeout=20)
assert health.status_code == 200
for name in ('phase2-local-final-audit.log', 'phase2-final-audit.log', 'phase2-final-exports.log'):
    path = OUT / name
    if path.exists():
        shutil.copy2(path, OUT / '自动检查' / name)
record = {
    'recorded_at': datetime.now().astimezone().isoformat(),
    'tag': manifest['tag'],
    'source_manifest_sha256': manifest['source_manifest_sha256'],
    'source_files_checked': len(manifest['files']),
    'source_mismatches': source_mismatches,
    'local_document_links_checked': checked_links,
    'missing_local_document_links': missing_links,
    'report_checks': checks,
    'formal_tasks_completed': len(formal),
    'method_quote_and_primary_method_checks': 'passed',
    'embedded_chart_comparisons_passed': len(charts['records']),
    'health_http_status': health.status_code,
    'limits': '模型验收；暂无客户现场反馈。DOCX 未在客户 Word 环境验收，窄屏检查采用 390px 浏览器容器。',
    'passed': True,
}
(OUT / '交付复核.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(record, ensure_ascii=False))
