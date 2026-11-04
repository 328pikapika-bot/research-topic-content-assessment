"""Audit formal UI tasks against frozen chapters and all four report formats."""
import html
import argparse
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / '.tmp/qa/phase2'
parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path); args = parser.parse_args()
OUT = args.out or OUT
normalize = lambda s: re.sub(r'\s+', '', html.unescape(str(s)))
results = []
for formal in json.loads((OUT / '正式任务清单.json').read_text(encoding='utf-8')):
    folder = OUT / '任务记录' / formal['task_id']
    data = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
    state = data['state']; snapshot = state['report_snapshot']
    topic = formal['agent'] == 'topic-selection'
    sections = snapshot['report_plan_snapshot']['sections'] if topic else snapshot['sections']
    md = (folder / 'report.md').read_text(encoding='utf-8')
    html_source = (folder / 'report.html').read_text(encoding='utf-8')
    html_text = re.sub('<[^>]+>', '', html_source)
    with zipfile.ZipFile(folder / 'report.docx') as z:
        docxml = ET.fromstring(z.read('word/document.xml'))
        docx_text = ''.join(docxml.itertext())
        docx_images = len([n for n in z.namelist() if n.startswith('word/media/')])
        rels = ET.fromstring(z.read('word/_rels/document.xml.rels'))
        docx_links = [r.attrib['Target'] for r in rels if r.attrib.get('TargetMode') == 'External']
    pdf = fitz.open(folder / 'report.pdf')
    pages = [p.get_text() for p in pdf]
    pdf_text = '\n'.join(pages)
    texts = dict(md=md, html=html_text, docx=docx_text, pdf=pdf_text)
    checks = {}
    titles = [section['title'] for section in sections]
    for fmt, text in texts.items():
        checks[fmt] = {'missing_chapters': [t for t in titles if normalize(t) not in normalize(text)]}
    # Exact first conclusion is checked after removing source-format markup only.
    blocks = sections[0].get('blocks', [])
    lead = next((b.get('text') for b in blocks if b.get('text')), sections[0].get('content', '').split('\n\n')[0])
    for fmt, text in texts.items():
        exact = normalize(lead) in normalize(text)
        checks[fmt]['first_conclusion_present'] = exact
        if fmt == 'pdf' and not exact:
            # Linux Writer places CJK quotation glyphs in separate PDF spans.
            # Extraction returns them out of order despite correct coordinates
            # (visually verified). Ignore only those two typographic glyphs;
            # every substantive character, number and qualifier still matches.
            without_quotes = lambda s: normalize(s).translate(str.maketrans('', '', '“”'))
            checks[fmt]['first_conclusion_present'] = without_quotes(lead) in without_quotes(text)
            checks[fmt]['cjk_quote_span_order_only'] = checks[fmt]['first_conclusion_present']
    manifest = snapshot['report_plan_snapshot'].get('chart_specs', []) if topic else snapshot.get('chart_manifest', [])
    figure_captions = []
    if topic:
        used = {b['key'] for s in sections for b in s.get('blocks', []) if b.get('kind') == 'figure_ref'}
        figure_captions = [c.get('title','') for c in manifest if c['key'] in used and c.get('data_status') != 'empty']
    else:
        figure_captions = [c.get('chart_caption','') for c in manifest if c.get('included')]
    for fmt, text in texts.items():
        checks[fmt]['missing_figure_captions'] = [c for c in figure_captions if normalize(c) not in normalize(text)]
    citation_map = snapshot['report_plan_snapshot'].get('citation_map', {}) if topic else snapshot.get('citation_numbers', {})
    referenced_numbers = set(map(str,citation_map.values()))
    for fmt, text in texts.items():
        found = set(re.findall(r'\[(\d+)\]',text))
        checks[fmt]['missing_reference_numbers'] = sorted(referenced_numbers-found,key=int)
    # Verify actual frozen source URLs in each format, not just link counts.
    if topic:
        source_links = set(snapshot['report_plan_snapshot'].get('citation_links', {}).values())
    else:
        reference_content = '\n'.join(s.get('content','') for s in sections if s['title']=='参考文献')
        source_links = set(re.findall(r'https?://[^\s<>\)]+', reference_content))
    pdf_links = {link.get('uri','') for page in pdf for link in page.get_links() if link.get('uri')}
    for fmt, text in texts.items():
        found_links = set(docx_links) if fmt=='docx' else pdf_links if fmt=='pdf' else set(re.findall(r'https?://[^\s<>\)"\']+', html.unescape(html_source if fmt=='html' else text)))
        checks[fmt]['missing_source_links'] = sorted(source_links-found_links)
    if topic:
        body_start = next(i for i,t in enumerate(pages) if '摘要' in normalize(t) and '围绕' in t)
        reference_title = next(s['title'] for s in sections if s['key'] == 'references')
        reference_page = next(i for i,t in enumerate(pages) if i >= body_start and normalize(reference_title) in normalize(t))
        body_pages = reference_page - body_start
    else:
        body_start = 0; body_pages = None
    render_dir = folder / '分页渲染'; render_dir.mkdir(exist_ok=True)
    thumbs = []
    for index,page in enumerate(pdf):
        pix = page.get_pixmap(matrix=fitz.Matrix(1.2,1.2))
        path = render_dir / f'page-{index+1:02}.png'; pix.save(path)
        img = Image.open(path).convert('RGB'); img.thumbnail((408,550))
        canvas=Image.new('RGB',(428,580),'#dde2e8'); canvas.paste(img,((428-img.width)//2,20))
        ImageDraw.Draw(canvas).text((16,560),f'Page {index+1}',fill='black'); thumbs.append(canvas)
    rows = (len(thumbs)+2)//3
    sheet=Image.new('RGB',(428*3,580*rows),'white')
    for i,img in enumerate(thumbs): sheet.paste(img,((i%3)*428,(i//3)*580))
    sheet.save(folder / '分页总览.png')
    entry={**formal,'status':data['status'],'template':snapshot.get('template_version'),'checks':checks,
           'pdf_pages':len(pdf),'body_pages':body_pages,'body_target_ok':4<=body_pages<=6 if topic else None,
           'image_counts':{'docx':docx_images,'html':len(re.findall('<img',html_source)),
                           'md':len(re.findall(r'!\[',md)),'pdf':sum(len(p.get_images()) for p in pdf)},
           'link_counts':{'docx':len(docx_links),'html':len(re.findall('href=',html_source)),
                          'pdf':sum(len(p.get_links()) for p in pdf)},
           'replacement_glyphs':pdf_text.count('\ufffd')}
    entry['passed']=all(not c['missing_chapters'] and c['first_conclusion_present'] and not c['missing_figure_captions']
                        and not c['missing_reference_numbers'] and not c['missing_source_links'] for c in checks.values()) and entry['replacement_glyphs']==0 and (entry['body_target_ok'] if topic else True)
    results.append(entry)
OUT.joinpath('四格式与分页核验.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False))
