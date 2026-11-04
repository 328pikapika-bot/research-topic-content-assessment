"""Compare embedded chart bytes in the downloaded acceptance report formats."""
import base64
import hashlib
import html
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / '资料/测试反馈/第二期优化/第二期验收'


def hashes(contents):
    return sorted(hashlib.sha256(content).hexdigest() for content in contents)


records = []
for output in (ROOT, ROOT / '部署复验'):
    for formal in json.loads((output / '正式任务清单.json').read_text(encoding='utf-8')):
        folder = output / '任务记录' / formal['task_id']
        embedded = {}
        for fmt in ('md', 'html'):
            source = html.unescape((folder / f'report.{fmt}').read_text(encoding='utf-8'))
            embedded[fmt] = hashes(base64.b64decode(data) for data in
                re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', source))
        with zipfile.ZipFile(folder / 'report.docx') as archive:
            embedded['docx'] = hashes(archive.read(name) for name in archive.namelist()
                                      if name.startswith('word/media/'))
        passed = bool(embedded['md']) and embedded['md'] == embedded['html'] == embedded['docx']
        records.append({**formal, 'location': str(output.relative_to(ROOT)) or '.',
                        'sha256': embedded, 'passed': passed})

destination = ROOT / '图形字节一致性核验.json'
destination.write_text(json.dumps({
    'scope': 'Markdown、HTML、DOCX 嵌入 PNG 逐字节一致；PDF 使用同源转换与分页目视核验，未以重复 XObject 数量判定图形数量。',
    'records': records,
    'passed': all(record['passed'] for record in records),
}, ensure_ascii=False, indent=2), encoding='utf-8')
assert all(record['passed'] for record in records), 'Embedded chart bytes differ'
print(f'{len(records)} 份报告的 Markdown／HTML／DOCX 图形哈希一致。')
