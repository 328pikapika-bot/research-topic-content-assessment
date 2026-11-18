"""Download real exports and measure citation typography after restart."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import zipfile

import fitz
import httpx
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '.runtime/phase1-20261117'
OUTPUT.mkdir(parents=True, exist_ok=True)
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
reports = [
    ('topic', '/v1/public-cases/topic-urban-heat-v2/export'),
    ('assessment', '/v1/public-cases/assessment-platform/export'),
    ('marine-original', '/v1/topic-selection/tasks/run_bce943fbdf14/export'),
]
results = []
with httpx.Client(base_url='http://127.0.0.1:17641', timeout=180,
                  headers={'X-User-Scope': 'platform_admin'}) as client:
    print('Health:', client.get('/system/health').status_code, flush=True)
    original = client.get('/v1/topic-selection/tasks/run_bce943fbdf14')
    original.raise_for_status()
    payload = original.json()
    detail = payload.get('task', payload.get('data', payload))
    (OUTPUT / 'marine-after-restart.json').write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Original task:', detail.get('status'), flush=True)
    assert detail.get('status') == 'completed'
    for name, route in reports:
        result = {'report': name}
        for fmt in ('pdf', 'docx', 'md'):
            response = client.get(route, params={'format': fmt})
            response.raise_for_status()
            (OUTPUT / f'{name}.{"zip" if fmt == "md" else fmt}').write_bytes(response.content)
            if fmt == 'pdf':
                document = fitz.open(stream=response.content, filetype='pdf')
                markers = []
                for index, page in enumerate(document):
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines', []):
                            for span in line['spans']:
                                if re.search(r'\[\d+\]', span['text']):
                                    markers.append({'page': index + 1, 'text': span['text'][:60], 'size': round(span['size'], 2)})
                assert markers, f'{name}: no PDF citations'
                assert all(m['size'] >= 10 for m in markers), f'{name}: tiny PDF citations {markers[:8]}'
                page_index = markers[0]['page'] - 1
                document[page_index].get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).save(OUTPUT / f'{name}-citations.png')
                result.update(pdf_pages=len(document), pdf_citation_sizes=sorted({m['size'] for m in markers}), pdf_sample=markers[:6])
            elif fmt == 'docx':
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    xml = etree.fromstring(archive.read('word/document.xml'))
                runs = [r for r in xml.xpath('//w:hyperlink/w:r', namespaces=NS)
                        if re.fullmatch(r'\s*\[\d+\]\s*', ''.join(r.xpath('.//w:t/text()', namespaces=NS)))]
                assert runs, f'{name}: no citation links'
                assert not any(r.xpath('./w:rPr/w:vertAlign[@w:val="superscript"]', namespaces=NS) for r in runs)
                result['word_citation_sizes'] = sorted({x for r in runs for x in r.xpath('./w:rPr/w:sz/@w:val', namespaces=NS)})
            else:
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    text = archive.read('report.md').decode('utf-8')
                    result['markdown_images'] = len([x for x in archive.namelist() if x.startswith('images/')])
                assert '<sup' not in text
                assert re.search(r'<a class="citation" href="#ref-\d+">\[\d+\]</a>', text)
                assert result['markdown_images'] > 0
                result['markdown_citations'] = len(re.findall('class="citation"', text))
            print(name, fmt, 'OK', flush=True)
        results.append(result)
(OUTPUT / 'export-verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False), flush=True)
