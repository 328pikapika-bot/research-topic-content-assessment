from pathlib import Path
import json
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[2] / '.tmp/qa/topic-layout'
with pdfplumber.open(root / 'report.pdf') as pdf:
    stats = []
    for index, page in enumerate(pdf.pages, 1):
        words = page.extract_words()
        heads = [w for w in words if '优先级' in w['text']]
        stats.append({'page': index, 'chars': len(page.chars), 'priority_heading': heads})
    print(json.dumps({'pages': len(pdf.pages), 'details': stats}, ensure_ascii=True))
    (root / 'layout-check.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
pdf = pdfium.PdfDocument(root / 'report.pdf')
thumbs = []
for index in range(len(pdf)):
    rendered = pdf[index].render(scale=1.5).to_pil()
    rendered.save(root / f'page-{index + 1}.png')
    rendered.thumbnail((306, 396))
    thumb = Image.new('RGB', (330, 425), 'white')
    thumb.paste(rendered, (12, 20))
    ImageDraw.Draw(thumb).text((12, 4), str(index + 1), fill='black')
    thumbs.append(thumb)
cols = 3
sheet = Image.new('RGB', (cols * 330, ((len(thumbs) + cols - 1) // cols) * 425), '#dce2e8')
for index, thumb in enumerate(thumbs):
    sheet.paste(thumb, ((index % cols) * 330, (index // cols) * 425))
sheet.save(root / 'contact.png')
