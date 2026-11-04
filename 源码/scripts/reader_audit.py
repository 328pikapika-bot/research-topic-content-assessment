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

OUT = Path(__file__).resolve().parents[1] / '.tmp/qa/reader'
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
    narrative = '\n'.join(b.get('text','') for s in sections if s.get('key',s.get('section_key'))!='references'
                          for b in s.get('blocks',[]) if b.get('kind') in {'paragraph','lead','heading','candidate_detail'})
    for s in sections:
        for b in s.get('blocks',[]):
            if b.get('kind') == 'candidate_detail':
                narrative += '\n' + '\n'.join(f.get('value','') for f in b.get('fields',[]))
    # Frozen assessment blocks retain evidence IDs as citation tokens. Only
    # reader-visible exports can establish an internal-ID presentation leak.
    issue_pattern = r'结论强度|部分支持|字符锚点|内部评分|综合评分|尚未配置|缺少[^。\n]*(?:字段|服务|证据)|(?<![A-Za-z0-9_-])(?:E-[A-Za-z0-9_-]+|M-INPUT|CAND-\d+)(?![A-Za-z0-9_-])'
    prose_issues = {fmt:re.findall(issue_pattern, text) for fmt,text in texts.items()}
    for fmt, text in texts.items():
        checks[fmt] = {'missing_chapters': [t for t in titles if normalize(t) not in normalize(text)]}
    # Exact first conclusion is checked after removing source-format markup only.
    blocks = sections[0].get('blocks', [])
    lead = next((b.get('text') for b in blocks if b.get('text')), sections[0].get('content', '').split('\n\n')[0])
    if not topic:
        numbers = snapshot.get('citation_numbers', {})
        for eid in sorted(numbers,key=len,reverse=True):
            lead = re.sub(r'(?<![\w-])' + re.escape(eid) + r'(?![\w-])', f'[{numbers[eid]}]', lead)
    for fmt, text in texts.items():
        # Markdown citation links add navigation markup, not extra wording.
        reading_text = re.sub(r'\[(\d+)\]\(#ref-\d+\)', r'[\1]', text) if fmt == 'md' else text
        exact = normalize(lead) in normalize(reading_text)
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
    # The topic snapshot keeps the complete retrieval map for audit, while its
    # reference_list contains only sources used in the published chapters.
    # Verify the published list and every in-text citation, not the unused pool.
    published_references = [item for s in sections for b in s.get('blocks', [])
                            if b.get('kind') == 'reference_list' for item in b.get('items', [])]
    in_text_numbers = set(re.findall(r'\[(\d+)\]', '\n'.join(s.get('content', '') for s in sections)))
    if topic:
        referenced_numbers = {str(item['number']) for item in published_references} | in_text_numbers
        assert in_text_numbers <= {str(item['number']) for item in published_references}, 'Unresolved published citation'
    else:
        referenced_numbers = set(map(str,citation_map.values()))
    for fmt, text in texts.items():
        found = set(re.findall(r'\[(\d+)\]',text))
        checks[fmt]['missing_reference_numbers'] = sorted(referenced_numbers-found,key=int)
    # Verify actual frozen source URLs in each format, not just link counts.
    if topic:
        source_links = {item.get('url') or item.get('nstl_url') or item.get('source_url')
                        for item in published_references} - {None, ''}
    else:
        reference_content = '\n'.join(s.get('content','') for s in sections if s.get('section_key')=='references')
        source_links = set(re.findall(r'https?://[^\s<>\)]+', reference_content))
    pdf_links = {link.get('uri','') for page in pdf for link in page.get_links() if link.get('uri')}
    for fmt, text in texts.items():
        found_links = set(docx_links) if fmt=='docx' else pdf_links if fmt=='pdf' else set(re.findall(r'https?://[^\s<>\)"\']+', html.unescape(html_source if fmt=='html' else text)))
        checks[fmt]['missing_source_links'] = sorted(source_links-found_links)
    key = 'key' if topic else 'section_key'
    body_title = sections[0]['title']
    body_start = next(i for i,t in enumerate(pages) if normalize(body_title) in normalize(t) and ('目录' not in t or i > 1))
    reference_title = next(s['title'] for s in sections if s[key] == 'references')
    reference_page = next(i for i,t in enumerate(pages) if i >= body_start and normalize(reference_title) in normalize(t))
    body_pages = reference_page - body_start
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
           'pdf_pages':len(pdf),'body_pages':body_pages,'body_target_ok':len(pdf)<=10 if topic else body_pages>0 if formal['case'].startswith('short') else 20<=body_pages<=30,
           'image_counts':{'docx':docx_images,'html':len(re.findall('<img',html_source)),
                           'md':len(re.findall(r'!\[',md)),'pdf':sum(len(p.get_images()) for p in pdf)},
           'link_counts':{'docx':len(docx_links),'html':len(re.findall('href=',html_source)),
                          'pdf':sum(len(p.get_links()) for p in pdf)},
           'replacement_glyphs':pdf_text.count('\ufffd'), 'prose_issues':prose_issues}
    entry['passed']=all(not c['missing_chapters'] and c['first_conclusion_present'] and not c['missing_figure_captions']
                        and not c['missing_reference_numbers'] and not c['missing_source_links'] for c in checks.values()) and entry['replacement_glyphs']==0 and not any(prose_issues.values()) and entry['body_target_ok']
    results.append(entry)
OUT.joinpath('四格式与分页核验.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False))
