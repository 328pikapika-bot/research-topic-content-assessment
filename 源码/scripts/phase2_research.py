"""Reproducible public-source check and three views of one real case."""
from pathlib import Path
import json
import requests
import fitz
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp/qa/phase2'
RESEARCH = OUT / '调研'
RESEARCH.mkdir(parents=True, exist_ok=True)
seeds = json.loads((ROOT.parent / '资料/测试输入/平台验收/公开文献来源.json').read_text(encoding='utf-8'))

def readable(item):
    name, url, term = item
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        with fitz.open(stream=response.content, filetype='pdf') as document:
            pages = [page.get_text() for page in document]
            index = next(i for i, text in enumerate(pages) if term.casefold() in text.casefold())
            text = pages[index]
            pos = text.casefold().find(term.casefold())
            quote = text[max(0, pos - 80):pos+320]
            (RESEARCH / (name+'.pdf')).write_bytes(response.content)
            return dict(name=name, url=url, resolved_url=response.url, http_status=response.status_code,
                        scope='readable_fulltext', pages=len(pages), page=index+1, quote=quote,
                        verification='PDF实际可解析且含可读原文', retrieved='2026-10-14')
    except Exception as exc:
        return dict(name=name,url=url,scope='unavailable',reason=str(exc)[:250])

with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(readable, [
        ('BM25', 'https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf', 'BM25'),
        ('CLIP', 'https://arxiv.org/pdf/2103.00020', 'contrastive'),
    ]))
results += [dict(name=r['title'],url=r['url'],scope=r['scope'],quote=r['abstract'][:240],
                 locator='摘要' if r['abstract'] else '题录',retrieved=r['retrieved'])
            for case in seeds for r in case['records'][:1]]
(OUT / '公开可读证据.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')

paper = seeds[0]['records'][0]
comparison = seeds[0]['records'][2]
data = dict(question='数字图书馆：稀疏反馈下的文献排序', core='图书馆学／信息检索',
            rows=[dict(expansion='概率与统计', method='BM25概率相关性排序',
                reason='将词频、文档长度与概率相关性建模用于文献排序', opportunity='比较稀疏反馈、多语言条件下与语义排序的差异',
                source=paper['title'], url=paper['url'], quote=paper['abstract'], grounded=True,
                limit='来源支持方法原理；当前语料、语言与反馈稀疏度须另行验证'),
            dict(expansion='模型解释方法',method='交叉编码器内部相关性分析',
                reason='公开摘要报告了与BM25相关性信号相似的内部机制',opportunity='检查语义排序的可解释性与失败边界',
                source=comparison['title'],url=comparison['url'],quote=comparison['abstract'],grounded=True,
                limit='摘要范围内的方法先例；向当前数字图书馆迁移尚需验证'),
            dict(expansion='最优传输',method='待验证',reason='用户问题提出的探索方向',opportunity='先检索原文方法与应用条件',
                source='',url='',quote='',grounded=False,limit='本小样未取得方法来源，不能断言研究空白')])
(RESEARCH / '同一案例图形数据.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
payload=json.dumps(data,ensure_ascii=False).replace('</','<\\/')
html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>同一案例三种图形比较</title><style>
body{font:16px/1.65 system-ui;margin:24px auto;max-width:1100px;color:#203449;padding:0 16px}button{font:inherit;padding:10px 20px;margin:6px;border:1px solid #789bb7;background:#edf5fa;border-radius:6px;cursor:pointer}button[aria-pressed=true]{background:#245b82;color:white}.row{display:grid;grid-template-columns:1fr 70px 1fr 70px 1.4fr;align-items:center;margin:16px 0;gap:8px}.node{padding:15px;background:#eef5fa;border-radius:6px;overflow-wrap:anywhere}.edge{text-align:center;border-bottom:2px solid #56809e;font-size:12px}.tentative .edge{border-bottom-style:dashed}.card{padding:18px;border:1px solid #cad9e5;border-radius:6px;margin:12px 0}.muted{color:#586d80}td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd}table{width:100%}ul{border-left:2px solid #b4cadb}#view{overflow:auto}@media(max-width:620px){.row{grid-template-columns:1fr}.edge{width:100px;margin:auto}}
</style><h1>数字图书馆：三种图形小样</h1><p>使用同一组真实文献与明确标记的待验证线索。学科分类为本次方法分析的解释，不把标签视为文献实验结论。</p><nav><button data-view="tree">主题树</button><button data-view="relation">分层关系图</button><button data-view="matrix">交叉矩阵</button></nav><div id="view"></div><div id="cards"></div><script>
const data=PAYLOAD;const el=document.getElementById('view');const cards=document.getElementById('cards');
function escape(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function card(r){return `<article class="card"><b>${escape(r.method)}</b><p>为什么有关：${escape(r.reason)}</p><p>研究机会：${escape(r.opportunity)}</p><p>限制：${escape(r.limit)}</p><p>依据：${r.url?`<a href="${escape(r.url)}">${escape(r.source)}</a>`:'待补方法证据'}</p><details><summary>来源原文</summary><p>${escape(r.quote||'无可核验的方法原文')}</p></details></article>`}
function show(kind){document.querySelectorAll('button[data-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.view===kind));cards.innerHTML=data.rows.map(card).join('');if(kind==='tree')el.innerHTML=`<h2>${escape(data.question)}</h2><ul><li>${escape(data.core)}<ul>${data.rows.map(r=>`<li>${escape(r.expansion)}<ul><li>${escape(r.method)}</li></ul></li>`).join('')}</ul></li></ul>`;if(kind==='relation')el.innerHTML=`<h2>${escape(data.question)}</h2>`+data.rows.map(r=>`<div class="row ${r.grounded?'':'tentative'}"><div class="node">核心学科<br><b>${escape(data.core)}</b></div><span class="edge">方法关联</span><div class="node">拓展学科<br><b>${escape(r.expansion)}</b></div><span class="edge">借鉴</span><div class="node"><b>${escape(r.method)}</b><small>${r.grounded?' · 有方法来源':' · 待验证线索'}</small></div></div>`).join('');if(kind==='matrix')el.innerHTML='<table><caption>空白不表示研究空白</caption><thead><tr><th>核心</th><th>拓展</th><th>方法</th><th>依据状态</th></tr></thead><tbody>'+data.rows.map(r=>`<tr><td>${escape(data.core)}</td><td>${escape(r.expansion)}</td><td>${escape(r.method)}</td><td>${r.grounded?'摘要可核验':'待验证'}</td></tr>`).join('')+'</tbody></table>'}
document.querySelectorAll('button[data-view]').forEach(b=>b.onclick=()=>show(b.dataset.view));show('relation');
</script></html>'''.replace('PAYLOAD',payload)
(RESEARCH / '图形方案同数据比较.html').write_text(html,encoding='utf-8')
print(json.dumps([{'name':r['name'],'scope':r['scope'],'page':r.get('page')} for r in results],ensure_ascii=False))
