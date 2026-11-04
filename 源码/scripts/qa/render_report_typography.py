import json
from pathlib import Path
from app.agents.topic_selection.schemas import TopicSelectionState
from app.agents.content_assessment.schemas import ContentAssessmentState
from app.services.topic_selection_report import _build_topic_docx_source, build_topic_html
from app.services.content_assessment_report import _build_canonical_docx
from app.services.export_service import build_html
from app.services.report_pdf_runtime import render_docx
import fitz
from PIL import Image,ImageDraw
ROOT = Path(__file__).resolve().parents[2]
folder = ROOT / '.tmp/qa/report-typography'
topic=json.loads((ROOT / '.tmp/qa/topic-layout/task.json').read_text(encoding='utf-8'))['state']
assessment=json.loads((ROOT / '.tmp/qa/report-typography/assessment-task.json').read_text(encoding='utf-8-sig'))['state']
results=[]
for name,state,builder,html in [('topic',TopicSelectionState.model_validate(topic),_build_topic_docx_source,build_topic_html),('assessment',ContentAssessmentState.model_validate(assessment),_build_canonical_docx,build_html)]:
 print('Rendering',name,flush=True)
 source,_=builder(state);(folder/f'{name}-source.docx').write_bytes(source)
 normalized,pdf=render_docx(source)
 (folder/f'{name}.docx').write_bytes(normalized);(folder/f'{name}.pdf').write_bytes(pdf)
 page_html,_=html(state);(folder/f'{name}.html').write_bytes(page_html.encode('utf-8') if isinstance(page_html,str) else page_html)
 doc=fitz.open(stream=pdf,filetype='pdf');fonts=sorted({f[3] for p in doc for f in p.get_fonts()})
 sizes=sorted({round(s['size'],1) for p in doc for b in p.get_text('dict')['blocks'] if 'lines' in b for line in b['lines'] for s in line['spans']})
 previews=[]
 for i,page in enumerate(doc):
  page.get_pixmap(matrix=fitz.Matrix(1.3,1.3)).save(folder/f'{name}-{i+1}.png')
  pix=page.get_pixmap(matrix=fitz.Matrix(.36,.36));im=Image.frombytes('RGB',[pix.width,pix.height],pix.samples);previews.append(im)
 cols=4;w,h=230,320;sheet=Image.new('RGB',(cols*w,((len(previews)+cols-1)//cols)*h),'#ddd');draw=ImageDraw.Draw(sheet)
 for i,im in enumerate(previews):
  sheet.paste(im,((i%cols)*w,(i//cols)*h+18));draw.text(((i%cols)*w+5,(i//cols)*h+2),f'{name} {i+1}',fill='black')
 sheet.save(folder/f'{name}-contact.png')
 results.append(dict(agent=name,pages=len(doc),template=state.report_plan.template_version,fonts=fonts,sizes=sizes,links=sum(len(p.get_links()) for p in doc)))
 print(results[-1],flush=True)
(folder/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
