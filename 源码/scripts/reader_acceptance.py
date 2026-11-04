"""Reproducible first-reader-release fixtures, public-API runs and exports.

Business material is constructed; literature is retrieved by the live agent.
Never inject report prose or literature into the task state/database.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp/qa/reader'
POINTS = [
    ('词项检索与概率排序', 'BM25 probabilistic relevance retrieval', 'The Probabilistic Relevance Framework BM25 and Beyond',
     '以科研论文题名、摘要和关键词为对象，考察术语匹配、词频饱和及篇长归一化。中文术语分词与英文缩写分别处理，保留公式名称、材料名称与方法缩写。',
     '采用BM25建立可解释词项排序，固定分词、停用词与字段权重，区分精确术语查询与语义改写查询。设置字段消融，检查长摘要与短摘要的排序差异。', 'BM25；布尔检索；无篇长归一化的词项排序', 'nDCG@10、Recall@100、查询延时分布'),
    ('稠密检索与领域迁移', 'Dense Passage Retrieval scientific document retrieval', 'Dense Passage Retrieval for Open-Domain Question Answering',
     '面向科研文献查询与摘要的语义匹配，区分自然语言问题、研究主题和术语组合。独立划分训练与测试的论文集合，防止同篇论文的改写版本跨集合泄露。',
     '采用双编码器Dense Passage Retrieval生成查询和摘要向量，比较通用初始化与科研领域适配。训练负例分别取随机文献、BM25相邻结果与同主题难负例，分析负例来源对错误检索的影响。', 'BM25；冻结通用向量模型；同计算预算的双编码器', 'Recall@100、MRR@10、索引存储及批处理延时'),
    ('词级交互与精细排序', 'ColBERT late interaction document retrieval', 'ColBERT Efficient and Effective Passage Search via Contextualized Late Interaction over BERT',
     '面向同主题论文中方法术语与实验限定语的精细区分，重点观察词级匹配能否区分研究对象相同但验证条件不同的文献。长摘要采用固定切块策略。',
     '采用ColBERT late interaction保存词级向量并执行MaxSim聚合。对比只保留单向量的编码方式，记录查询词到文献词的匹配，检查术语解释与实际排序的一致性。', 'BM25；双编码器；相同候选池的交叉编码器', 'nDCG@10、候选重排耗时、索引大小与困难查询误差'),
    ('引文表征与学科泛化', 'SPECTER citation informed scientific document embeddings', 'SPECTER Document-level Representation Learning using Citation-informed Transformers',
     '面向跨学科科研论文推荐，区分引文相关、主题相似与方法可迁移三种关系。按发表年份划分评价集，防止未来引文被用于过去时间点的推荐。',
     '采用SPECTER citation-informed document embeddings表征科研文献，比较仅题名摘要训练与引文关系训练。保留引文方向、年份与学科标签，抽样分析跨学科推荐的关联理由。', 'BM25；通用摘要向量；仅主题标签匹配', '推荐相关率、跨学科案例解释一致性、年份划分下的检索性能'),
    ('科学图表的图文对齐', 'CLIP contrastive image text scientific figure retrieval', 'Learning Transferable Visual Models From Natural Language Supervision',
     '面向科研论文图表与图注检索，分别评价曲线图、显微图像与示意图。图表和图注按论文整体划分，禁止同论文图片进入不同集合。',
     '采用CLIP contrastive image-text learning建立图文嵌入，比较通用图文编码与科学图表适配。图注文字、图内文字识别与视觉表征分开消融，避免将视觉相似解释为科学含义相同。', '图注BM25；只使用图内文字的检索；冻结CLIP', '图文Recall@10、图表类别分层表现、证据页定位准确率'),
    ('检索增强与引用核对', 'Retrieval Augmented Generation knowledge intensive NLP citation', 'Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks',
     '面向科研问题回答与引用定位，比较检索文献与回答论述之间的对应关系。将方案目标、作者报告结果和系统建议分别标注，评测人员对任务方案保持盲态。',
     '采用Retrieval-Augmented Generation连接候选检索、片段选择与引用生成，比较固定候选数及不同片段组合。保留原始回答和所引片段，人工核对每句科研判断能否由实际片段表达。', '纯检索摘要展示；无检索生成；相同模型的固定上下文生成', '引用对应率、事实错误类别、回答覆盖与人工复核时间'),
]

def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def prepare():
    mat = OUT / '测试材料'; mat.mkdir(parents=True, exist_ok=True)
    for kind, label in [('paper', '论文'), ('project', '项目'), ('guideline', '指南')]:
        lines = [f'{label}名称：科研文献检索与知识发现方法体系',
            '材料性质：客户业务场景构造材料，用于软件验收，不是已发表论文或实际立项指南。没有提供真实实验结果。',
            '研究方向：数字图书馆、科研文献检索与可定位知识发现',
            '研究目标：比较词项匹配、语义表征、词级交互、引文关系、图文对齐及检索增强六类方法的适用问题，形成可复核的评测与应用方案。',
            '研究内容：建立科研文献和图表的检索测试集，组织六个相互区分的方法研究单元；同时考察相关性、运行成本、适用学科和引用定位。',
            '关键技术：BM25；Dense Passage Retrieval；ColBERT；SPECTER；CLIP；Retrieval-Augmented Generation。',
            '技术路线：先确定评价任务和独立样本，再建立基线和方法对照，逐项验证并分析跨学科迁移；最后整合检索与引用核对。',
            '应用场景：数字图书馆主题检索、科研论文推荐、科学图表检索及带引用的研究问答。',
            '考核指标：每项报告检索相关性、误差类别与运行成本；引用必须能定位原文。具体阈值应在预实验后登记，本材料不声称已达成。',
            '时间安排：需求定义、基线复现、独立验证、集成评价四个阶段，阶段报告包括失败案例和原始记录。',
            '预期成果：版本化语料、基线配置、评测代码、独立复核记录及研究报告。',
            '共同设计：评价以查询为统计单位，对查询类别分层抽样。重复查询和同源论文在划分前合并。所有模型固定版本、训练数据范围和随机种子；训练文献与测试文献保持隔离。标注采用独立评阅与分歧讨论，保留分歧分布而不只给平均分。',
            '结果解释：下列均为拟开展的研究设计，不能表述为已经改善检索性能。方法在原论文中的结果不得转写为本材料的实验结果。不同语料、学科、指标定义、候选池规模和硬件条件下的数字不直接比较。',
            '主张 | 主张原文 | 研究对象 | 研究方法 | 比较基线 | 核心指标 | 对照要求 | 项目目标值']
        for i, (name, terms, query, obj, method, baseline, metric) in enumerate(POINTS, 1):
            verb = '指南拟设置' if kind == 'guideline' else '论文拟研究' if kind == 'paper' else '项目拟开展'
            quote = f'{verb}{name}，以{terms}考察科研文献检索与知识发现的适用条件。'
            check = f'{obj} {method} 采用{baseline}作为比较路线。固定数据划分、语料版本与预处理参数；按学科与查询类型分别汇总，保留原始排序和误差样例。验证时同时检查均值表现与困难样本，不以单个成功例子替代总体评价。'
            lines.append(' | '.join([f'C{i}', quote, obj, method, baseline, metric, check, '形成可重复的方法比较，不预设性能提升']))
        lines += ['', '主张 | QueryText | ClaimIDs | QueryRole | Rationale']
        for i, (_, terms, query, *_rest) in enumerate(POINTS, 1):
            for role, text in [('系统或技术路线先例', query), ('关键机制与实现方法', terms)]:
                lines.append(' | '.join([f'C{i}', text, f'C{i}', role, '检索公开在先方法，核对实际摘要及方法适用范围']))
        (mat / f'{kind}_多要点完整材料.txt').write_text('\n'.join(lines), encoding='utf-8')
    (mat / 'paper_简短单要点.txt').write_text('论文名称：科研文献词项检索研究\n研究方向：数字图书馆信息检索\n研究内容：拟比较BM25与布尔检索的主题相关性。\n关键技术：BM25概率排序。\n说明：软件构造材料，尚无实验结果。', encoding='utf-8')
    original = ROOT.parent / '资料/测试输入/平台验收/选题案例与预期.json'
    dump(OUT / '选题案例与预期.json', json.loads(original.read_text(encoding='utf-8')))

def session():
    s = requests.Session(); s.headers['X-User-Scope'] = 'platform_admin'; return s

def start(base, kinds, revision=""):
    suffix = f"_{revision}" if revision else ""
    records = json.loads((OUT / '任务清单.json').read_text(encoding='utf-8')) if (OUT / '任务清单.json').exists() else []
    s = session()
    for kind in kinds:
        if any(x['case'] == kind + suffix for x in records): continue
        if kind in ['paper', 'project', 'guideline', 'short']:
            typ = 'paper' if kind == 'short' else kind
            path = OUT / '测试材料' / ('paper_简短单要点.txt' if kind == 'short' else f'{kind}_多要点完整材料.txt')
            with path.open('rb') as f:
                response = s.post(base + '/v1/files/upload', files={'file': (path.name, f, 'text/plain')}, timeout=90)
            response.raise_for_status(); uploaded = response.json()['data']
            request = {'user_input': '请评估所附科研文献检索与知识发现材料。以六个研究单元分别检索、比较方法与条件；区分研究计划与实验结果，以学术书面语言形成完整创新性评估。',
                'assessment_title': f'科研文献检索方法{dict(paper="论文", project="项目", guideline="指南")[typ]}评估', 'artifact_type': typ,
                'uploaded_files': [uploaded], 'document_roles': {uploaded['file_id']: 'PROJECT' if typ == 'project' else 'BASELINE' if typ == 'guideline' else 'EVIDENCE'}}
            agent_name = 'content-assessment'
        else:
            case = next(c for c in json.loads((OUT / '选题案例与预期.json').read_text(encoding='utf-8')) if c['id'] == kind)
            request = {'user_input': case['input'], 'topic_title': case['title'], 'candidate_count': 3, 'analysis_mode': 'deep'}
            agent_name = 'topic-selection'
        response = s.post(base + f'/v1/{agent_name}/run', json=request, timeout=90)
        response.raise_for_status(); data = response.json(); tid = data['run_id']
        records.append({'case': kind + suffix, 'task_id': tid, 'agent': agent_name, 'base': base})
        dump(OUT / '任务清单.json', records)
        dump(OUT / '任务记录' / tid / 'request.json', request)
        print(json.dumps(records[-1], ensure_ascii=False), flush=True)

def inspect(base, export=False, revision="", since=""):
    s = session(); results = []
    for item in json.loads((OUT / '任务清单.json').read_text(encoding='utf-8')):
        if revision and not item['case'].endswith('_' + revision):
            continue
        if since and item.get('created_at', '') < since:
            continue
        tid = item['task_id']; agent_name = item['agent']
        response = s.get(base + f'/v1/{agent_name}/tasks/{tid}', timeout=60); response.raise_for_status()
        data = response.json(); data = data.get('data', data); state = data.get('state') or {}
        folder = OUT / '任务记录' / tid; dump(folder / 'state.json', data)
        result = {**item, 'status': data.get('status'), 'stage': data.get('current_stage'),
            'claims': len(state.get('claims', [])), 'sources': len(state.get('evidence_records', state.get('evidence_pool', []))),
            'template': (state.get('report_snapshot') or {}).get('template_version') or state.get('report_plan', {}).get('template_version'),
            'confirmation': state.get('confirmation'), 'error': data.get('error_message')}
        if export and result['status'] == 'completed':
            result['exports'] = {}
            for fmt in ['md', 'html', 'docx', 'pdf']:
                path = folder / f'report.{fmt}'
                download = s.get(base + f'/v1/{agent_name}/tasks/{tid}/export', params={'format': fmt}, timeout=300)
                download.raise_for_status(); path.write_bytes(download.content)
                result['exports'][fmt] = {'bytes': len(download.content), 'sha256': hashlib.sha256(download.content).hexdigest()}
        results.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in ['confirmation', 'exports']}, ensure_ascii=False), flush=True)
    dump(OUT / '任务核验.json', results)

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['prepare', 'start', 'status', 'export'])
    p.add_argument('--base', default='http://127.0.0.1:17617'); p.add_argument('--cases', default='paper,project,guideline')
    p.add_argument('--revision', default='')
    p.add_argument('--since', default='')
    a = p.parse_args()
    if a.action == 'prepare': prepare()
    elif a.action == 'start': start(a.base, a.cases.split(','), a.revision)
    else: inspect(a.base, a.action == 'export', a.revision, a.since)
