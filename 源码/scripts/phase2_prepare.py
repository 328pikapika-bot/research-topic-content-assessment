"""Prepare reproducible client-oriented materials and public-source research records."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import re
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.tmp' / 'qa' / 'phase2'
OUT.mkdir(parents=True, exist_ok=True)
CASES = [
    dict(id='library', title='数字图书馆检索与数学方法', input='围绕数字图书馆科研文献检索，研究概率排序、图方法和最优传输在稀疏反馈、多语言查询中的可借鉴方法。区分词项匹配与语义检索，比较BM25、密集向量和重排序，说明数学方法的文献来源、数据条件和失效边界。不要用样本少直接判定空白。', disciplines=['图书情报', '信息检索', '概率统计', '数学'], methods=['BM25', 'optimal transport'], avoid=['数学方法自动保证召回提升', '主题共现证明创新']),
    dict(id='multimodal', title='多模态文献知识发现', input='研究科研文献正文、图表和公式的多模态知识发现，关注图文对齐、科学图表结构理解和证据定位。比较对比学习与检索增强方法在科研文献中的条件，区分通用图文训练与科学语义，避免将视觉相似当成科学结论相同。', disciplines=['图书情报', '计算机科学', '计算机视觉'], methods=['contrastive learning', 'CLIP'], avoid=['CLIP自动解决科学图表理解', '无法读图仍声称全文核验']),
    dict(id='sodium', title='钠离子电池低温快充', input='研究钠离子电池在低温快充中的界面动力学、析钠风险和诊断控制，探索电化学、信号处理及控制理论方法关联。明确电极体系、温度、倍率、荷电状态和基线；锂电池先例只能作为待迁移方法，不能直接当作钠电实测结论。', disciplines=['电化学', '材料科学', '控制理论'], methods=['electrochemical impedance spectroscopy', 'model predictive control'], avoid=['锂电结果直接证明钠电快充', '不同温度倍率直接比较']),
    dict(id='organoid', title='肿瘤类器官免疫共培养', input='探索患者来源肿瘤类器官与免疫细胞共培养的研究机会，关注自体T细胞扩增、免疫微环境和微流控灌流。明确供体、肿瘤类型、培养基、读出和对照；体外杀伤不能直接推出临床疗效，现有共培养先例必须引用并说明与新建议的差异。', disciplines=['肿瘤学', '免疫学', '生物工程'], methods=['co-culture', 'microfluidics'], avoid=['类器官杀伤等于临床疗效', '共培养无先例']),
    dict(id='lpbf', title='激光粉末床熔融闭环监测', input='围绕激光粉末床熔融的熔池监测与闭环控制，分析高速成像、信号处理和控制理论的方法迁移。区分缺陷分类、在线检测与实际闭环调节，明确材料、设备、帧率、时延、误报及验证条件；离线分类准确率不能直接证明在线闭环成形改善。', disciplines=['增材制造', '计算机视觉', '控制理论'], methods=['in situ monitoring', 'feedback control'], avoid=['离线分类等于闭环控制', '不同设备参数直接比较']),
]
OUT.joinpath('选题案例与预期.json').write_text(json.dumps(CASES, ensure_ascii=False, indent=2), encoding='utf-8')

fields = {
 'paper': ['论文题目：科研文献图文对齐方法研究', '研究方向：科研文献多模态检索', '研究内容：比较正文检索与图文联合检索在图表问答中的证据定位', '关键技术：对比学习；CLIP图文编码；BM25基线'],
 'project': ['项目名称：钠离子电池低温诊断研究', '研究方向：钠离子电池低温界面诊断', '研究目标：拟识别低温充电风险，尚未提供实测结果', '研究内容：在同批电池上比较阻抗谱与温度信号对充电异常的识别', '技术路线：采用电化学阻抗谱和模型预测控制，在负10摄氏度、同SOC下设未优化对照', '考核指标：拟将风险识别召回率提高至85%，尚未实测', '预期成果：诊断方法、对照记录和验证报告'],
 'guideline': ['指南名称：科研文献图表检索任务指南', '研究目标：支持科研图表证据可追溯检索', '研究内容：建立图文联合检索任务与可定位评测集', '考核指标：拟要求证据定位准确率不低于85%，按独立专家标注与基线比较；这是任务要求', '应用场景：科研文献图表问答和数字图书馆知识发现'],
}
materials = OUT / '测试材料'; materials.mkdir(exist_ok=True)
for kind, lines in fields.items():
    body = '\n'.join([*lines, '材料说明：构造业务材料，仅用于系统测试。下列数值为构造值，不能作为真实科研事实。',
        '主张原文 | 核心指标 | 对照要求',
        ('拟采用电化学阻抗谱分析钠离子电池低温界面行为。 | 界面阻抗 | 同批电池、同温度、同SOC' if kind == 'project' else '拟采用图文对比学习建立科研文献图表检索与定位方法。 | 定位准确率 | BM25基线、独立标注集'),
        '统计与验证说明：构造样本60条，仅用于检查系统能否区分计划目标与材料报告结果；未提供可外推的真实实验。'])
    materials.joinpath(kind + '_完整.txt').write_text(body, encoding='utf-8')
    missing = [line for line in lines if not line.startswith('关键技术：' if kind == 'paper' else '技术路线：' if kind == 'project' else '应用场景：')]
    materials.joinpath(kind + '_缺项.txt').write_text('\n'.join([*missing, '材料说明：仅用于缺项提示测试。']), encoding='utf-8')
materials.joinpath('project_补充.txt').write_text('项目名称：钠离子电池低温诊断研究\n实验数据：构造补充材料，与主文件同一对象。目标85%；构造测试记录报告79%，不得说已达标。',encoding='utf-8')
materials.joinpath('project_配套指南.txt').write_text('指南名称：钠离子电池低温诊断研究配套指南\n指南要求：应在负10摄氏度、同SOC、同电芯型号下验证召回率，提供对照和独立重复。',encoding='utf-8')
materials.joinpath('不同对象.txt').write_text('项目名称：患者来源肺癌类器官免疫共培养\n研究方向：肿瘤免疫\n研究内容：比较自体T细胞共培养杀伤\n技术路线：采用流式细胞术与活细胞成像。',encoding='utf-8')
materials.joinpath('空文件.txt').write_bytes(b'')
materials.joinpath('损坏文件.pdf').write_bytes(b'%PDF-1.7\nnot-a-valid-pdf')
materials.joinpath('不可比指标.txt').write_text('项目名称：钠离子电池低温诊断研究\n材料说明：构造行为检查。计划在负10摄氏度钠电召回率85%；补充记录为25摄氏度锂电准确率92%，对象、指标、温度不一致，不可直接比较。', encoding='utf-8')

queries = [('library','The Probabilistic Relevance Framework BM25 and Beyond'),('multimodal','Learning Transferable Visual Models From Natural Language Supervision'),('sodium','sodium ion batteries low temperature fast charging'),('organoid','Generation of Tumor-Reactive T Cells by Co-culture of Peripheral Blood Lymphocytes and Tumor Organoids'),('lpbf','Anomaly detection and classification in a laser powder bed additive manufacturing process using a trained computer vision algorithm')]
def fetch(pair):
    ident, query = pair
    response = requests.get('https://api.openalex.org/works', params={'search':query,'per-page':3},timeout=35)
    response.raise_for_status()
    records = []
    for item in response.json()['results']:
        index = item.get('abstract_inverted_index') or {}
        words = {position: word for word, positions in index.items() for position in positions}
        abstract = ' '.join(words[position] for position in sorted(words))
        records.append(dict(title=item.get('title'),doi=item.get('doi'),url=item.get('primary_location',{}).get('landing_page_url'),year=item.get('publication_year'),abstract=abstract,scope='abstract' if abstract else 'metadata',source='OpenAlex',retrieved='2026-10-14'))
    return {'case':ident,'query':query,'records':records}
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    for pair, result in zip(queries, pool.map(fetch,queries)):
        results.append(result)
OUT.joinpath('公开文献来源.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'case':r['case'],'titles':[x['title'] for x in r['records']]} for r in results],ensure_ascii=False))
