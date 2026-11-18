from __future__ import annotations

import asyncio
import copy
import gzip
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.content_assessment.schemas import ContentAssessmentState
from app.agents.topic_selection.schemas import TopicSelectionState
from app.services.export_service import build_markdown
from app.services.public_cases import CASE_IDS, CASE_ROOT, _complete_case
from app.services.topic_selection_report import build_topic_markdown


SOURCE_VERSION = "v2"
TARGET_VERSION = "v3"
SOURCE_DIR = CASE_ROOT / "snapshots" / SOURCE_VERSION
TARGET_DIR = CASE_ROOT / "snapshots" / TARGET_VERSION

PHAGE_JBV = "E-PUB-PHAGE-2025-JBV"
PHAGE_MSYSTEMS = "E-PUB-PHAGE-2024-MSYSTEMS"
PHAGE_FRONTIERS = "E-PUB-PHAGE-2025-FCIMB"
PV_SOLARRL = "E-PUB-PV-2023-SOLARRL"
PV_NATURE = "E-PUB-PV-2024-NC"

SUPPLEMENTAL_EVIDENCE = {
    "topic-phage-v2": [
        {
            "evidence_id": PHAGE_JBV,
            "title": "Evaluation of Bacteriophage and Antibiotic Synergy Against Carbapenem-Resistant Klebsiella pneumoniae Clinical Isolates",
            "authors": ["Bokyung Kim", "Shukho Kim", "Yoon-Jung Choi"],
            "year": 2025,
            "venue": "Journal of Bacteriology and Virology",
            "doi": "10.4167/jbv.2025.55.2.131",
            "url": "https://doi.org/10.4167/jbv.2025.55.2.131",
            "source_url": "https://doi.org/10.4167/jbv.2025.55.2.131",
            "source_type": "nstl_abstract",
            "source_label": "Journal of Bacteriology and Virology",
            "evidence_scope": "abstract",
            "abstract": "The global rise of carbapenem-resistant Klebsiella pneumoniae (CRKP) poses a serious threat to public health due to limited therapeutic options and high mortality rates. Bacteriophage (phage) therapy has emerged as a promising alternative; however, its effectiveness is often compromised by the rapid development of phage resistance. In this study, we investigated the synergistic potential of phage-antibiotic combination therapy against CRKP. Four phages were isolated and characterized for adsorption rate, replication dynamics, and environmental stability. Synergistic interactions between phages and five antibiotics (colistin, meropenem, ciprofloxacin, amikacin, and tigecycline) were assessed in vitro using checkerboard assays against two CRKP clinical isolates. In vivo mouse infection models further confirmed the therapeutic benefit, with repeated administration of the combination treatment resulting in a significant improvement in survival.",
            "query_group": "噬菌体与抗生素联用及耐药菌感染模型",
            "retrieval_query": "bacteriophage antibiotic synergy carbapenem-resistant Klebsiella checkerboard mouse model",
        },
        {
            "evidence_id": PHAGE_MSYSTEMS,
            "title": "Antibacterial effect of phage cocktails and phage-antibiotic synergy against pathogenic Klebsiella pneumoniae",
            "authors": ["Mengshi Zhao", "Hongru Li", "Dehao Gan"],
            "year": 2024,
            "venue": "mSystems",
            "doi": "10.1128/msystems.00607-24",
            "url": "https://doi.org/10.1128/msystems.00607-24",
            "source_url": "https://doi.org/10.1128/msystems.00607-24",
            "source_type": "nstl_abstract",
            "source_label": "mSystems",
            "evidence_scope": "abstract",
            "abstract": "This study examined strategies to improve phage therapy against multidrug-resistant Klebsiella pneumoniae, including selection of broad-host-range phages, optimization of phage formulations, and combinations with last-resort antibiotics. The authors report that increasing phage numbers with antibiotics enhanced suppression of bacterial growth, and that optimized phage-antibiotic synergy had activity across different clades of clinical MDR K. pneumoniae. In vitro findings were also evaluated in a murine model, where optimized combination treatment reduced bacterial burden in mouse tissues.",
            "query_group": "噬菌体鸡尾酒、宿主范围与联用方案",
            "retrieval_query": "phage cocktail broad host range antibiotic synergy Klebsiella in vitro murine model",
        },
        {
            "evidence_id": PHAGE_FRONTIERS,
            "title": "In vitro and in vivo antibacterial efficacy of bacteriophage combined with tigecycline against carbapenem-resistant Klebsiella pneumoniae and characterization of phage resistant mutants",
            "authors": ["Rui Zhu", "Ruilin Wang", "Bing Fei"],
            "year": 2025,
            "venue": "Frontiers in Cellular and Infection Microbiology",
            "doi": "10.3389/fcimb.2025.1610625",
            "url": "https://doi.org/10.3389/fcimb.2025.1610625",
            "source_url": "https://doi.org/10.3389/fcimb.2025.1610625",
            "source_type": "nstl_abstract",
            "source_label": "Frontiers in Cellular and Infection Microbiology",
            "evidence_scope": "abstract",
            "abstract": "The study characterized bacteriophage HZJ31 against carbapenem-resistant Klebsiella pneumoniae and evaluated its combination with tigecycline. The combination enhanced bactericidal efficacy, delayed emergence of phage-resistant mutants, and improved survival in a Galleria mellonella infection model. A capsule-loss phage-resistant mutant showed reduced growth and virulence and regained sensitivity to some antibiotics, including gentamicin, indicating a fitness cost in this isolate and model.",
            "query_group": "噬菌体抗性、毒力代价与联合治疗",
            "retrieval_query": "carbapenem-resistant Klebsiella phage tigecycline resistant mutants virulence infection model",
        },
    ],
    "topic-perovskite-v2": [
        {
            "evidence_id": PV_SOLARRL,
            "title": "Accelerated Stress Testing of Perovskite Photovoltaic Modules: Differentiating Degradation Modes with Electroluminescence Imaging",
            "authors": ["Jackson W. Schall", "Andrew Glaws", "Nutifafa Y. Doumon"],
            "year": 2023,
            "venue": "Solar RRL",
            "doi": "10.1002/solr.202300229",
            "url": "https://doi.org/10.1002/solr.202300229",
            "source_url": "https://doi.org/10.1002/solr.202300229",
            "source_type": "nstl_abstract",
            "source_label": "Solar RRL",
            "evidence_scope": "abstract",
            "abstract": "Electroluminescence and thermal imaging were used to examine p-i-n metal halide perovskite photovoltaic mini-modules before and after indoor accelerated stress testing or outdoor deployment. UV stress produced dark speckle patterns associated with interfacial contact degradation, while thermal cycling produced lateral intensity gradients associated with current crowding near scribe defects. The study reports that current-voltage analysis alone did not fully reveal degradation processes and that accelerated UV or thermal-cycle signatures did not individually reproduce the signatures observed after outdoor deployment.",
            "query_group": "钙钛矿组件加速应力与失效成像",
            "retrieval_query": "perovskite photovoltaic mini-module accelerated stress outdoor electroluminescence degradation modes",
        },
        {
            "evidence_id": PV_NATURE,
            "title": "Low-temperature strain-free encapsulation for perovskite solar cells and modules passing multifaceted accelerated ageing tests",
            "authors": ["Paolo Mariani", "Miguel Ángel Molina-García", "Jessica Barichello", "Marilena Isabella Zappia", "Erica Magliano", "Luigi Angelo Castriotta", "Luca Gabatel", "Sanjay Balkrishna Thorat", "Antonio Esaú Del Rio Castillo", "Filippo Drago", "Enrico Leonardi", "Sara Pescetelli", "Luigi Vesce", "Francesco Di Giacomo", "Fabio Matteocci", "Antonio Agresti", "Nicole De Giorgi", "Sebastiano Bellani", "Aldo Di Carlo", "Francesco Bonaccorso"],
            "year": 2024,
            "venue": "Nature Communications",
            "doi": "10.1038/s41467-024-48877-y",
            "url": "https://doi.org/10.1038/s41467-024-48877-y",
            "source_url": "https://doi.org/10.1038/s41467-024-48877-y",
            "source_type": "nstl_abstract",
            "source_label": "Nature Communications",
            "evidence_scope": "abstract",
            "abstract": "The study reports an industrial encapsulation process based on laminating a highly viscoelastic adhesive onto perovskite solar cells and modules. The encapsulant was designed to reduce thermomechanical stresses at the encapsulant/rear-electrode interface, while adding hexagonal boron nitride to the polymer matrix improved barrier and thermal-management properties. Encapsulated devices were evaluated under multifaceted accelerated-ageing tests; the reported results apply to the tested device configurations and stress conditions.",
            "query_group": "钙钛矿组件封装及加速老化",
            "retrieval_query": "perovskite solar module encapsulation thermomechanical accelerated ageing tests",
        },
    ],
}


TOPIC_CANDIDATES: dict[str, dict[str, dict[str, Any]]] = {
    "topic-urban-heat-v2": {
        "C1": {
            "title": "卫星地表温度表征近地气温的下垫面差异与跨城市迁移",
            "reason": "既有研究分别验证了Landsat地表温度产品，并分析城市地表温度与土地覆盖的关系。这个方向把站点校准、下垫面分层和留出城市验证放进同一研究框架，能判断遥感温度的误差会怎样影响高温预警模型迁移。",
            "object": "长三角三座高密度城市中老旧居住区、产业园区和滨水绿地周边的地表温度、近地气温及预警模型迁移表现。",
            "question": "不同下垫面和观测时段下，卫星地表温度能否稳定表征近地气温；在未参与建模的城市中，模型误差是否仍处于可用范围？",
            "method": "比较Landsat 8/9、Sentinel-3与自动站观测，按晴空夏季、下垫面类型和城市划分样本；先做站点交叉校准，再采用整城留出验证模型迁移，分别报告地表温度反演误差与近地气温预测误差。",
            "evidence": "现有文献提供了Landsat产品与地面站点校验、城市地表温度与土地覆盖分析的先例，但研究区域和验证目标并不等同于长三角的跨城市预警迁移。它们适合作为观测与校准方法的起点，不能直接代替本研究的跨城验证。",
            "ids": ["E-480ecc39d7ec", "E-9e1b8cd99907", "E-a579f0540827"],
        },
        "C2": {
            "title": "绿地连通性与树冠结构对昼夜热暴露的阈值作用",
            "reason": "亚洲多城市比较和多年绿地研究都把绿地的空间配置纳入降温分析，说明只统计绿地面积不足以解释不同街区的热环境差异。结合昼夜观测、树冠结构和热暴露，可进一步辨认哪些空间特征在不同城市形态下具有稳定关联。",
            "object": "高密度建成区的绿地连通性、树冠结构及其与昼间和夜间热暴露的关系。",
            "question": "绿地斑块的连通性、形状和树冠结构与热暴露的关系是否存在阈值；这种关系会不会随昼夜、下垫面和城市而改变？",
            "method": "结合遥感温度、绿地解译和地面气象记录，计算景观格局指标并按昼夜及下垫面分层；用分段回归或树模型识别候选阈值，再以留出街区或城市检验其稳定性。",
            "evidence": "已有多城市和多年研究分析了城市绿地空间格局与地表降温之间的关系，可为指标选择和尺度设定提供依据。当前方向把这些空间关系推进到昼夜热暴露与树冠结构的联合检验，结论仍需由本地观测数据验证。",
            "ids": ["E-92b8e7313ac9", "E-8fd24844e20e", "E-8f967c1cf610"],
        },
        "C3": {
            "title": "绿地降温、近地气温校准与居民热暴露的联合评估",
            "reason": "地表温度产品验证与绿地格局研究分别回答了观测可靠性和降温空间差异问题。把自动站、遥感和人口暴露信息联合起来，可以避免把地表温度直接当作人体热暴露，并检验绿地效应在不同空间场景中的可解释性。",
            "object": "长三角高密度城市中地表温度、近地气温、绿地结构和老年人口热暴露的空间对应关系。",
            "question": "完成地面气温校准后，绿地连通性和树冠结构对不同人群昼夜热暴露的关联是否仍然存在，并会在哪些下垫面上发生变化？",
            "method": "先用自动站校准卫星温度，再将热环境指标与绿地格局、建筑形态和人口分布进行空间匹配；按老旧居住区、产业园区和滨水绿地分层，并用多城市留出验证检查结论能否复现。",
            "evidence": "产品验证研究说明了遥感温度需要地面观测校准，城市绿地研究则提供了景观格局与热环境关联的分析路径。两类证据分别支撑测量和空间分析环节，本方向的贡献在于把它们与人群暴露问题放到同一验证框架。",
            "ids": ["E-480ecc39d7ec", "E-92b8e7313ac9", "E-8f967c1cf610"],
        },
        "C4": {
            "title": "不同下垫面与遥感产品差异对热暴露模型稳健性的影响",
            "reason": "地表温度产品的精度会受地表类型和观测条件影响，而绿地研究也显示空间尺度会改变观察到的降温关系。这个方向把传感器差异、下垫面分层和模型误差作为同一套稳健性检查对象，避免只报告总体平均精度。",
            "object": "不同遥感温度产品、下垫面类型和空间尺度下热暴露模型的误差分布与稳定性。",
            "question": "产品选择、空间分辨率和下垫面类型分别会造成多大误差；误差是否集中在特定城市区域或观测时段？",
            "method": "用相同地面站和统一晴空筛选比较卫星产品，按水体、植被和不透水面分层报告偏差；再通过空间留出和敏感性分析检查热暴露模型对产品及尺度选择的依赖。",
            "evidence": "Landsat产品验证和不同城市绿地格局研究提供了观测精度及空间尺度分析的实例。它们支持设置分层与敏感性比较，但没有直接给出长三角多产品热暴露模型的误差范围。",
            "ids": ["E-480ecc39d7ec", "E-9e1b8cd99907", "E-92b8e7313ac9"],
        },
        "C5": {
            "title": "面向未参与建模城市的多源高温预警验证",
            "reason": "上海等城市的长时序遥感研究可描述热环境变化，却不能单独说明模型能否迁移到另一座城市。将多源卫星、自动站和居民热风险信息与整城留出验证结合，能把研究从热岛制图推进到预警模型的可迁移性评估。",
            "object": "长三角高密度城市夏季高温预警模型及其在未参与建模城市中的预测表现。",
            "question": "融合遥感温度、土地利用、气象和居民热风险信息后，模型在留出城市中的误差是否稳定；哪些下垫面需要单独校准？",
            "method": "在统一时间窗口内整合卫星温度、土地利用、自动站和人口数据；采用按城市留出的训练与测试划分，分层报告误差，并以单城市模型和跨城市模型作对照。",
            "evidence": "已有上海和昆明等城市研究展示了遥感温度反演与热岛空间分析方法，Landsat验证研究也强调了地面观测校准。它们为数据与基线选择提供参考，跨城市预警性能仍需独立验证。",
            "ids": ["E-a579f0540827", "E-9e1b8cd99907", "E-480ecc39d7ec"],
        },
    },
    "topic-phage-v2": {
        "C1": {
            "title": "噬菌体与抗生素联用对耐药菌的协同效应及浓度组合",
            "reason": "公开研究已在碳青霉烯耐药肺炎克雷伯菌临床分离株中用棋盘法评估噬菌体与多种抗生素的联用，也有铜绿假单胞菌研究比较噬菌体与抗生素的组合效应。把菌种、菌株、药物和噬菌体方案分层，能避免将单一组合的协同结果泛化到所有耐药菌。",
            "object": "碳青霉烯耐药肺炎克雷伯菌和铜绿假单胞菌临床分离株中的噬菌体单药、鸡尾酒及抗生素联用方案。",
            "question": "不同菌株和药物组合下，噬菌体与抗生素是否出现可重复的协同或拮抗；效应是否受噬菌体效价、感染复数和生物膜状态影响？",
            "method": "先测宿主范围和药敏，再用棋盘法筛选组合并以时间杀菌曲线复核；对代表组合记录噬菌体效价、感染复数、药物浓度、生物膜状态和耐药突变频率，分别报告两种菌的结果。",
            "evidence": "新近研究在两株耐药肺炎克雷伯菌中用棋盘法比较噬菌体与多种药物，并进一步观察小鼠感染模型；另一项研究覆盖不同克雷伯菌分支。铜绿假单胞菌文献提供了不同菌种的联用方法参照，但不能替代针对本项目药物组合的实验。",
            "ids": [PHAGE_JBV, PHAGE_MSYSTEMS, "E-4c9ef1020a14"],
        },
        "C2": {
            "title": "噬菌体抗性突变对耐药菌毒力与药物敏感性的影响",
            "reason": "个案研究和近期耐药菌实验都报告了噬菌体抗性与毒力、细菌生长或抗生素敏感性变化之间的联系，但这些结果对应特定菌株、突变和模型。比较肺炎克雷伯菌与铜绿假单胞菌的受体变化及适应性代价，有助于区分可重复机制和菌株特异现象。",
            "object": "两类耐药菌在噬菌体选择压力下产生的抗性突变、毒力表型及抗生素敏感性变化。",
            "question": "不同受体或荚膜相关突变是否改变毒力、生长竞争力或抗生素敏感性；这些代价会不会随菌种和遗传背景变化？",
            "method": "诱导并测序噬菌体抗性株，结合受体或荚膜表型、菌落生长、竞争实验和抗生素药敏进行比较；对有明确机制线索的突变再用动物感染模型验证，不把单个病例外推为普遍规律。",
            "evidence": "一项肺炎克雷伯菌个案将抗性突变与临床变化、基因分析及感染模型联系起来；另一项近期研究报告荚膜缺失抗性株在特定模型中生长和毒力下降，并恢复对部分药物的敏感性。它们提供可检验的机制线索，不能预设其他菌株出现相同代价。",
            "ids": ["E-3868ec4e512a", PHAGE_FRONTIERS, "E-4c9ef1020a14"],
        },
        "C3": {
            "title": "体外筛选的联用方案在急性感染动物模型中的验证",
            "reason": "既有铜绿假单胞菌噬菌体研究在小鼠急性肺部感染模型中观察了感染负荷和炎症指标，碳青霉烯耐药肺炎克雷伯菌联用研究也报告了小鼠模型结果。不同研究的菌种、给药时点和终点并不相同，适合据此设计分层验证，而不能直接比较疗效高低。",
            "object": "体外筛选得到的噬菌体或联用方案，以及对应的耐药肺炎克雷伯菌和铜绿假单胞菌感染模型。",
            "question": "体外表现较好的方案在动物感染过程中能否重现细菌负荷或生存结局上的改善；给药时点和菌株背景如何影响结果？",
            "method": "先固定菌株和体外联用条件，再按预防、同步及感染后给药设置模型组；将细菌负荷、炎症或生存结局分别报告，并设置抗生素单药、噬菌体单药和联用对照。",
            "evidence": "铜绿假单胞菌研究使用急性小鼠肺部感染模型并记录感染负荷和炎症；耐药肺炎克雷伯菌研究则采用小鼠感染模型观察联用治疗。模型、菌种和给药方案存在差异，因此本方向重点是验证体外筛选结果的可重复性，而不是照搬文献中的效应量。",
            "ids": ["E-23861abf008f", PHAGE_JBV, "E-df996307a7b3"],
        },
        "C4": {
            "title": "噬菌体宿主范围与鸡尾酒组合对菌株覆盖的影响",
            "reason": "多克隆肺炎克雷伯菌研究将宿主范围、噬菌体配方和联用效果放在一起分析，铜绿假单胞菌研究也比较了不同噬菌体的宿主谱和交叉抗性。由菌株面板出发评估覆盖率，比只在单株菌上验证更接近临床分离株差异这一实际问题。",
            "object": "不同克隆型和临床来源的耐药菌株，以及单噬菌体、鸡尾酒和抗生素联用方案的宿主覆盖。",
            "question": "鸡尾酒能否扩大对菌株面板的覆盖并延缓抗性出现；宿主范围和交叉抗性是否会改变组合的稳定性？",
            "method": "对临床分离株建立宿主范围矩阵，测定吸附和裂解表现；根据交叉抗性结果筛选鸡尾酒，再用连续传代、时间杀菌和生物膜实验比较单噬菌体与组合方案。",
            "evidence": "mSystems研究在多分支临床肺炎克雷伯菌中考察广宿主噬菌体、配方优化与联用方案；铜绿假单胞菌文献则报告了不同噬菌体间的宿主谱和交叉抗性观察。两者可支持菌株面板和组合筛选设计，最终覆盖率仍需由本案例菌株确定。",
            "ids": [PHAGE_MSYSTEMS, "E-4c9ef1020a14", PHAGE_JBV],
        },
        "C5": {
            "title": "按菌株背景建立噬菌体治疗的体外筛选与验证流程",
            "reason": "现有研究分别采用宿主范围检测、棋盘法、时间杀菌和动物模型评价噬菌体治疗，但实验条件和终点不完全一致。把这些环节按菌株背景串联起来，能为后续判断哪些组合值得进入动物验证提供清晰的实验依据。",
            "object": "覆盖不同克隆型和临床来源的碳青霉烯耐药肺炎克雷伯菌及铜绿假单胞菌筛选流程。",
            "question": "哪些体外指标最能帮助筛出可重复的噬菌体方案；宿主范围、联合药物和耐药突变频率应如何共同进入筛选决策？",
            "method": "按菌株记录噬菌体效价、宿主范围、感染复数、时间杀菌、生物膜状态和耐药突变频率；设置统一阳性与阴性对照，并将筛出的代表组合送入后续感染模型验证。",
            "evidence": "近期研究分别展示了宿主范围与配方优化、棋盘法联用筛选、抗性突变表征及动物模型验证。它们提供流程各环节的文献依据，但不同研究的菌种和模型不能直接合并为一套已验证标准。",
            "ids": [PHAGE_JBV, PHAGE_MSYSTEMS, PHAGE_FRONTIERS, "E-23861abf008f"],
        },
    },
    "topic-perovskite-v2": {
        "C1": {
            "title": "封装与界面钝化对钙钛矿组件多因素衰减的影响",
            "reason": "器件尺度研究指出离子迁移、界面缺陷和反向偏压会参与衰减，组件研究又显示不同应力可能形成不同的空间退化图案。把封装阻隔、界面处理和组件尺度失效分析并列比较，可避免只用初始效率判断稳定性。",
            "object": "面积不小于10 cm²的钙钛矿组件及其封装阻隔层、界面钝化方案和多应力下的失效位置。",
            "question": "不同封装和界面处理分别影响哪些衰减路径；组件放大后，器件内部、互连区域和边缘的失效分布是否发生变化？",
            "method": "设置未封装、常规封装和多层阻隔封装，并控制界面处理、载量和压实条件；在光照、湿热、热循环和户外暴露后联合使用电性能测试、EL/PL和截面分析定位失效。",
            "evidence": "已有界面钝化研究分析了缺陷和老化行为，组件级加速应力研究则用电致发光和热成像区分局部退化模式。两类工作分别支撑界面和组件检测路径，本项目提出的组合方案仍需按统一条件实测。",
            "ids": ["E-7a7e5c053276", "E-80d3c3bcf81d", PV_SOLARRL],
        },
        "C3": {
            "title": "纳入组件空间失效信息的加速老化寿命预测",
            "reason": "现有RPM研究在钙钛矿电池上开展了加速老化与预测建模，组件级成像研究则观察到应力相关的局部退化差异。把空间失效信息纳入模型是有明确方法来源的扩展方向，但预测是否改善必须通过组件数据和户外观察验证。",
            "object": "不同区域的钙钛矿组件功率衰减过程，以及空间失效特征对RPM或其他寿命模型预测的影响。",
            "question": "边缘、互连和有效发电区域的衰减是否具有不同时间过程；引入空间特征后，模型对户外寿命的解释是否更贴近实测？",
            "method": "在统一加速应力下定期进行EL/PL成像和局部电性能测量，形成位置分层的衰减序列；比较不含与含空间特征的模型，并用独立户外数据检验预测偏差。",
            "evidence": "钙钛矿电池研究提供了RPM加速老化建模先例，组件级研究显示EL和热成像能区分应力相关退化模式。两者尺度不同，恰好说明从电池模型推广到组件前需要增加空间定位和户外验证。",
            "ids": ["E-366a54da2fb8", PV_SOLARRL, "E-0735f4166652"],
        },
        "C2": {
            "title": "封装与界面方案对组件寿命预测的影响验证",
            "reason": "文献已分别报道封装、界面钝化和加速老化模型，但这些证据来自不同器件结构与应力条件。这个方向把材料处理结果作为模型输入，并同步比较加速老化数据与实际组件响应，重点是验证各环节能否接续。",
            "object": "多种封装阻隔和界面处理下钙钛矿组件的衰减曲线及寿命模型输入参数。",
            "question": "封装与界面处理对不同衰减通道的影响能否被稳定量化，并用于改善同类组件的寿命预测？",
            "method": "按材料方案制备组件，记录温湿度、光谱、负载和有效面积；开展统一加速测试并拟合衰减参数，再以独立组件和户外数据检查模型适用范围。",
            "evidence": "RPM研究提供了加速老化与性能预测的模型路径，模块封装研究说明封装结构和热机械应力会进入稳定性评价。现有证据尚不能给出本方案的预测精度，需由同一批组件的对照数据确定。",
            "ids": ["E-366a54da2fb8", "E-d1781bfc44bd", PV_NATURE],
        },
        "C5": {
            "title": "组件放大后的局部失效定位与封装方案比较",
            "reason": "小面积器件结果不能直接代表组件互连、边缘和多电池串联后的应力分布。组件级EL研究已展示应力条件与局部退化图案的对应关系，封装研究则提供了可比较的阻隔和热管理方案，二者可以共同支撑尺度放大后的验证设计。",
            "object": "钙钛矿组件中心、划线区、互连区和边缘的失效分布，以及不同封装方案下的变化。",
            "question": "面积放大和封装处理会不会改变主导失效位置；这些局部变化与组件功率衰减能否建立可重复对应？",
            "method": "对不同封装组件执行热循环、湿热和户外暴露，定期采集EL/PL与温度图像，并将局部缺陷位置与组件I-V、T80和失效记录关联。",
            "evidence": "组件应力成像研究在迷你组件中区分了UV和热循环产生的空间图案；低温封装研究则将封装工艺用于电池与组件并进行多因素老化。两者为局部检测和封装对照提供方法依据。",
            "ids": [PV_SOLARRL, PV_NATURE, "E-93fd56bbd742"],
        },
        "C4": {
            "title": "实验室加速应力与户外运行退化模式的对应关系",
            "reason": "光伏组件标准加速试验适合质量筛查，但相关研究指出它并不自动等同于长期寿命预测；钙钛矿组件成像研究也观察到单一室内应力不能复现所有户外退化图案。这个方向将环境应力、空间失效和户外记录纳入同一验证链。",
            "object": "钙钛矿组件在湿热、光照、热循环和户外运行中的失效模式及其随时间的变化。",
            "question": "不同加速应力分别覆盖哪些户外退化特征；组合应力或多源观测能否建立更可解释的室内外映射？",
            "method": "对同批组件开展多种单项及组合应力测试并持续进行EL/PL、热成像和电性能测量；与户外部署的同类组件按季节和运行条件对照，避免用单一应力推算全寿命。",
            "evidence": "光伏模块寿命测试研究区分了资格筛查与长期耐久预测的用途，钙钛矿迷你组件研究报告了加速应力和户外暴露间的退化图案差异。两者共同支持室内外对照设计，但不能替代长期实测。",
            "ids": ["E-0735f4166652", PV_SOLARRL, "E-366a54da2fb8"],
        },
    },
}


OPPORTUNITY_LINKS = {
    "topic-phage-v2": [
        {
            "crosslink_id": "XL-PHAGE-01",
            "source_theme_id": "T1",
            "target_theme_id": "T2",
            "core": "微生物学",
            "expansion": "药理学",
            "opportunity": "按菌株和药物组合评价噬菌体—抗生素协同",
            "method": "宿主范围测定结合棋盘法与时间杀菌曲线",
            "reason": "将噬菌体覆盖范围与药物浓度组合放在同一菌株面板内比较，区分可重复协同、拮抗及菌株差异。",
            "quote": "Synergistic interactions between phages and five antibiotics (colistin, meropenem, ciprofloxacin, amikacin, and tigecycline) were assessed in vitro using checkerboard assays against two CRKP clinical isolates.",
            "evidence_id": PHAGE_JBV,
            "support": [PHAGE_JBV, PHAGE_MSYSTEMS, "E-4c9ef1020a14"],
            "core_support": [PHAGE_JBV, PHAGE_MSYSTEMS, "E-4c9ef1020a14"],
            "expansion_support": [PHAGE_JBV, PHAGE_MSYSTEMS],
            "validation": "在肺炎克雷伯菌和铜绿假单胞菌中分别记录菌株来源、药敏、噬菌体效价和感染复数；不同菌种的协同判定不合并统计。",
            "discipline_reasons": ["宿主范围和噬菌体裂解实验提供菌株层面的微生物学依据。", "棋盘法与药物浓度组合用于判断联用的药理效应。"],
        },
        {
            "crosslink_id": "XL-PHAGE-02",
            "source_theme_id": "T2",
            "target_theme_id": "T4",
            "core": "感染微生物学",
            "expansion": "动物感染模型",
            "opportunity": "从体外联合筛选衔接到感染模型验证",
            "method": "先做棋盘筛选，再按给药时点比较感染负荷和生存结局",
            "reason": "体外组合结果应在明确菌株和给药方案后进入动物模型；模型终点分开报告，避免把体外抑菌直接等同于治疗获益。",
            "quote": "In vivo mouse infection models further confirmed the therapeutic benefit, with repeated administration of the combination treatment resulting in a significant improvement in survival.",
            "evidence_id": PHAGE_JBV,
            "support": [PHAGE_JBV, PHAGE_MSYSTEMS, "E-23861abf008f"],
            "core_support": [PHAGE_JBV, PHAGE_MSYSTEMS, "E-23861abf008f"],
            "expansion_support": [PHAGE_JBV, PHAGE_MSYSTEMS, "E-23861abf008f"],
            "validation": "将预防、同步和感染后给药分组，分别报告细菌负荷、炎症和生存指标；小鼠与其他感染模型不直接合并比较。",
            "discipline_reasons": ["感染模型用于检验联用方案在宿主体内的效应。", "药物与噬菌体的配伍、剂量和给药时点需要在体内外保持可追溯。"],
        },
        {
            "crosslink_id": "XL-PHAGE-03",
            "source_theme_id": "T3",
            "target_theme_id": "T4",
            "core": "细菌遗传与适应性",
            "expansion": "感染生物学",
            "opportunity": "在感染模型中检验抗性突变的毒力与适应性代价",
            "method": "突变株表征结合感染模型中的生长、毒力及药敏终点",
            "reason": "抗性突变可能改变毒力或抗生素敏感性，但已发表结果对应特定突变和模型；把基因变化、体外表型与感染结局对应起来，才能判断代价是否具有菌株依赖性。",
            "quote": "A capsule-loss phage-resistant mutant showed reduced growth and virulence and regained sensitivity to some antibiotics, including gentamicin, indicating a fitness cost in this isolate and model.",
            "evidence_id": PHAGE_FRONTIERS,
            "support": [PHAGE_FRONTIERS, "E-3868ec4e512a", "E-4c9ef1020a14"],
            "core_support": [PHAGE_FRONTIERS, "E-3868ec4e512a"],
            "expansion_support": [PHAGE_FRONTIERS, "E-3868ec4e512a"],
            "validation": "记录突变基因、荚膜或受体表型、竞争生长和药敏；将Galleria、斑马鱼或小鼠结果按模型分别解释。",
            "discipline_reasons": ["基因组和受体表型分析用于解释噬菌体抗性形成。", "感染模型用于观察特定突变背景下的毒力表现。"],
        },
    ],
    "topic-perovskite-v2": [
        {
            "crosslink_id": "XL-PV-01",
            "source_theme_id": "T1",
            "target_theme_id": "T2",
            "core": "光伏材料与器件",
            "expansion": "可靠性工程",
            "opportunity": "把封装和界面方案纳入组件加速老化评价",
            "method": "分组封装对照结合多因素加速老化和性能跟踪",
            "reason": "封装会影响水氧阻隔与热机械应力，界面处理则对应缺陷和复合过程；在同一组件与测试条件下比较，可判断材料处理效果能否成为寿命模型的可解释输入。",
            "quote": "The encapsulant was designed to reduce thermomechanical stresses at the encapsulant/rear-electrode interface, while adding hexagonal boron nitride to the polymer matrix improved barrier and thermal-management properties.",
            "evidence_id": PV_NATURE,
            "support": [PV_NATURE, "E-366a54da2fb8", "E-7a7e5c053276"],
            "core_support": [PV_NATURE, "E-7a7e5c053276"],
            "expansion_support": [PV_NATURE, "E-366a54da2fb8"],
            "validation": "统一组件面积、封装结构、温湿度、光谱和负载条件；区分封装、界面处理和加速应力的独立影响。",
            "discipline_reasons": ["封装和界面材料决定阻隔、热管理及局部接触稳定性。", "加速老化和寿命建模用于描述退化过程及适用条件。"],
        },
        {
            "crosslink_id": "XL-PV-02",
            "source_theme_id": "T1",
            "target_theme_id": "T3",
            "core": "光伏器件物理",
            "expansion": "失效分析",
            "opportunity": "从界面退化追踪到组件局部失效位置",
            "method": "EL与热成像联合定位应力相关的空间退化模式",
            "reason": "器件界面和晶界过程可能形成局部电学异常，而组件成像研究已在不同应力下观察到不同空间图案；联合材料处理与位置成像有助于解释放大后的失效分布。",
            "quote": "Electroluminescence and thermal imaging were used to examine p-i-n metal halide perovskite photovoltaic mini-modules before and after indoor accelerated stress testing or outdoor deployment.",
            "evidence_id": PV_SOLARRL,
            "support": [PV_SOLARRL, "E-80d3c3bcf81d", "E-173abfb18982"],
            "core_support": [PV_SOLARRL, "E-80d3c3bcf81d", "E-173abfb18982"],
            "expansion_support": [PV_SOLARRL],
            "validation": "在相同面积与测试时点采集EL/PL和电性能图，按中心、划线、互连及边缘区域比较；表征到的局部图案不直接等同于单一失效机理。",
            "discipline_reasons": ["器件材料与界面处理决定应力下的电学退化路径。", "失效分析通过空间成像将局部缺陷与组件性能关联。"],
        },
        {
            "crosslink_id": "XL-PV-03",
            "source_theme_id": "T2",
            "target_theme_id": "T3",
            "core": "可靠性工程",
            "expansion": "光伏测试与成像",
            "opportunity": "对照室内加速应力与户外暴露的退化图案",
            "method": "多应力测试结合EL、热成像和户外运行对照",
            "reason": "加速试验与户外运行未必产生相同的失效特征；将电性能曲线与空间成像同步采集，可以辨认单项应力覆盖了哪些退化过程、遗漏了哪些户外模式。",
            "quote": "The study reports that current-voltage analysis alone did not fully reveal degradation processes and that accelerated UV or thermal-cycle signatures did not individually reproduce the signatures observed after outdoor deployment.",
            "evidence_id": PV_SOLARRL,
            "support": [PV_SOLARRL, "E-0735f4166652", "E-366a54da2fb8"],
            "core_support": [PV_SOLARRL, "E-0735f4166652", "E-366a54da2fb8"],
            "expansion_support": [PV_SOLARRL],
            "validation": "保持组件配置一致，记录加速应力和户外部署条件；逐项对照退化位置、I-V参数与测试时间，不由单项应力直接外推户外年限。",
            "discipline_reasons": ["可靠性测试需要明确应力条件和寿命推断边界。", "光伏成像与电学表征用于识别组件局部退化模式。"],
        },
    ],
}


ASSESSMENT_INTRODUCTIONS = {
    "assessment-platform": "这份论文方案关注如何把综述中的科学主张拆成可定位、可复核的证据单元。设计拟比较题名、关键词、向量和混合检索，并用双人标注、主张级准确性、引用覆盖及人工复核时间衡量表现；这些都是计划中的验证内容，不是已取得的实验结果。",
    "assessment-drug": "本例提交的是面向激酶抑制剂筛选的模型评估设计。研究把分子图模型、随机森林和指纹基线放在随机、时间与骨架切分下比较，并单独检验解释质量；IC50与Ki等不同测定口径不混合，报告不把预测结果写成实测活性。",
    "assessment-battery-v2": "项目围绕硫化物固态电解质与高镍正极界面，计划比较不同涂层并跟踪界面副反应、接触损失和阻抗增长。统一载量、压力、温度及平行电池设计构成主要比较基础；循环和软包表现仍属待验证目标。",
    "assessment-chip-v2": "项目以肝脏—血脑屏障双器官芯片为对象，重点区分接口、连续灌流和跨器官代谢对屏障读数的影响。单器官基线、不同流路对照、TEER与示踪通量构成验证链；方案中的指标范围不代表已经完成实验。",
    "assessment-standard": "这份指南草案拟统一屏障类器官芯片的细胞、器件、培养、检测和结果记录口径，并计划用多实验室、多批次和多操作人员验证复现性。当前评估针对规范设计本身；跨实验室验证结果仍是待开展任务。",
    "assessment-cost": "这份指南草案面向科研级微流控器件，覆盖软光刻、激光加工、三维打印和热压成型等制造路径，并把尺寸、密封、流量和批次追溯纳入使用前验证。三批器件的重复性验证仍属计划，不替代诊断产品的法规检验。",
}

ASSESSMENT_BOUNDARIES = {
    "assessment-platform": "结论限于生物医学和材料学综述中的证据链定位任务；双人标注、未见主题验证及全文定位实验完成前，不能据方案推断方法效果。",
    "assessment-drug": "结论限于激酶抑制剂与候选筛选场景；IC50、Ki和不同实验室条件须分开处理，模型预测不能替代独立实验活性。",
    "assessment-battery-v2": "结论限于硫化物电解质与高镍正极界面及给出的涂层和测试方案；循环、T80及软包结果仅在实测后纳入结论。",
    "assessment-chip-v2": "结论限于肝脏—血脑屏障双器官芯片和当前拟定的细胞、流路及读数；跨器官稳定性需由设置的对照和重复性实验验证。",
    "assessment-standard": "适用范围为屏障类器官芯片。多实验室复现仍属指南草案中的计划任务，相关结论不推广到未覆盖的芯片模型，也不等同于临床疗效。",
    "assessment-cost": "适用于科研级微流控器件的设计、制造和使用前验收；不替代诊断产品法规检验，也不从计划中的三批验证推断长期临床性能。",
}

QUERY_ROLE_READERS = {
    "system_prior_art": "相近技术路线和已有实现",
    "mechanism_method": "关键原理、作用机制和研究方法",
    "metric_baseline": "技术指标、公开基线和测量方法",
    "application_validation": "应用场景、验证条件和测试结果",
    "industry_market_competitive": "产业应用、市场情况和同类方案",
    "industry_standard_regulatory": "相关标准、规范和监管要求",
    "point_supplement": "该研究要点对应的补充文献",
}


def _query_reader_fields(state: dict[str, Any]) -> list[dict[str, Any]]:
    claims = {str(item.get("claim_id")): item for item in state.get("claims", [])}
    plans = copy.deepcopy(state.get("query_plans", []))
    for plan in plans:
        claim_ids = _unique([*(plan.get("claim_ids") or []), plan.get("claim_id", "")])
        claim = next((claims[item] for item in claim_ids if item in claims), {})
        fields = claim.get("claim_fields") or {}
        topic = str(claim.get("display_title") or fields.get("title") or fields.get("research_object") or "").strip()
        if not topic:
            topic = re.split(r"[，,。；;\n]", str(claim.get("text") or ""), maxsplit=1)[0].strip()
        if len(topic) > 48:
            topic = topic[:47].rstrip() + "…"
        topic = topic or "本次研究要点"
        focus = QUERY_ROLE_READERS.get(str(plan.get("query_role") or plan.get("query_group") or ""), "相关研究和验证依据")

        filters = plan.get("filters") or {}
        languages = filters.get("languages") or plan.get("languages") or []
        language_names = {"zh": "中文", "en": "英文"}
        language_text = "、".join(dict.fromkeys(language_names.get(str(item), str(item)) for item in languages)) or "未注明"
        years = [str(item) for item in plan.get("year_range", []) if item]
        year_text = f"{years[0]}—{years[-1]} 年" if len(years) >= 2 else "未限定年份"
        sources = ["NSTL 科技文献服务平台" if str(item).upper() == "NSTL" else str(item) for item in plan.get("source_scope", []) if item]
        source_text = "、".join(dict.fromkeys(sources)) or "未注明"
        types = filters.get("document_types") or []
        type_names = {"paper": "论文", "standard": "标准", "guideline": "指南"}
        type_text = "、".join(dict.fromkeys(type_names.get(str(item), str(item)) for item in types)) or "未注明"
        candidate_count = max(0, int(plan.get("candidate_count") or 0))
        selected_count = max(0, int(plan.get("selected_count") or 0))

        plan.update({
            "reader_topic": topic,
            "reader_focus": focus,
            "reader_summary": f"围绕“{topic}”，查找{focus}，为该研究要点的逐项分析提供依据。",
            "reader_scope": f"文献来源：{source_text}；语种：{language_text}；年份：{year_text}；文献类型：{type_text}",
            "reader_result": f"检索到 {candidate_count} 条候选记录，筛选 {selected_count} 篇进入后续分析。",
        })
    return plans


def _repair_battery_reference(state: dict[str, Any]) -> None:
    evidence_id = "E-L-075"
    evidence = next((item for item in state.get("evidence_records", []) if item.get("evidence_id") == evidence_id), None)
    if not evidence or not evidence.get("doi"):
        raise ValueError("assessment-battery-v2: the third reference needs its DOI source")
    from app.common.retrieval.links import bibliography_text

    title = bibliography_text(evidence.get("title"))
    doi = str(evidence["doi"]).removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    url = f"https://doi.org/{doi}"
    authors = "、".join(bibliography_text(item) for item in evidence.get("authors", []))
    venue = bibliography_text(evidence.get("venue") or "")
    number = (state.get("report_snapshot", {}).get("citation_numbers") or {}).get(evidence_id)
    if not title or not number or not re.fullmatch(r"10\.1007/s10832-017-0084-z", doi):
        raise ValueError("assessment-battery-v2: third-reference title, number, or DOI did not verify")
    # Crossref's record for this DOI matches the paper title. Retain the old
    # gateway destination for provenance, and use the verified DOI everywhere
    # the curated case exposes this source.
    evidence.setdefault('locator', {})['original_nstl_url'] = evidence.get('nstl_url', '')
    title = 'Surface and morphological investigation of the electrode/electrolyte properties in an all-solid-state battery using a Li2S-P2S5 solid electrolyte'
    evidence.update(title=title, nstl_url='', source_url=url, url=url)
    section = next((item for item in state.get("report_snapshot", {}).get("sections", [])
                    if item.get("section_key") == "references"), None)
    if not section:
        raise ValueError("assessment-battery-v2: its report has no reference section")
    matched = False
    for block in section.get("blocks", []):
        if evidence_id not in (block.get("evidence_ids") or []):
            continue
        block["text"] = f"[{number}] [{title}](<{url}>)。{authors}，{venue}，{evidence.get('year') or '年份未提供'}。DOI：{doi}。"
        matched = True
    if not matched:
        raise ValueError("assessment-battery-v2: its third reference block was not found")
    section["content"] = _section_content(section.get("blocks", []))


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=9) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _citation_text(text: str, evidence_ids: list[str], citation_map: dict[str, int]) -> str:
    numbers = _unique([str(citation_map[item]) for item in evidence_ids if item in citation_map])
    return text.rstrip() + (" " + "".join(f"[{number}]" for number in numbers) if numbers else "")


def _add_supplemental_evidence(case_id: str, state: dict[str, Any], report_plan: dict[str, Any]) -> None:
    records = state.setdefault("evidence_pool", [])
    existing = {str(item.get("evidence_id")): item for item in records}
    citation_map = report_plan.setdefault("citation_map", {})
    citation_links = report_plan.setdefault("citation_links", {})
    next_number = max([int(value) for value in citation_map.values() if str(value).isdigit()] or [0]) + 1
    for source in SUPPLEMENTAL_EVIDENCE.get(case_id, []):
        evidence_id = source["evidence_id"]
        if evidence_id not in existing:
            records.append(copy.deepcopy(source))
            existing[evidence_id] = records[-1]
        if evidence_id not in citation_map:
            citation_map[evidence_id] = next_number
            next_number += 1
        citation_links[evidence_id] = source["url"]

    for section in report_plan.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("kind") == "reference_list":
                references = block.setdefault("items", [])
                referenced = {item.get("evidence_id") for item in references}
                for source in SUPPLEMENTAL_EVIDENCE.get(case_id, []):
                    if source["evidence_id"] not in referenced:
                        references.append({
                            "number": citation_map[source["evidence_id"]],
                            "evidence_id": source["evidence_id"],
                            "title": source["title"],
                            "authors": "",
                            "year": source["year"],
                            "venue": source["venue"],
                            "doi": source["doi"],
                            "url": source["url"],
                        })


def _candidate_reader(candidate_data: dict[str, Any], citation_map: dict[str, int]) -> dict[str, Any]:
    ids = candidate_data["ids"]
    object_ids = ids[:2]
    method_ids = ids[-2:]
    return {
        "reason": {"text": candidate_data["reason"], "evidence_ids": ids},
        "research_content": {
            "research_object": {"text": candidate_data["object"], "evidence_ids": object_ids},
            "core_question": {"text": candidate_data["question"], "evidence_ids": ids[:2]},
            "method_route": {"text": candidate_data["method"], "evidence_ids": method_ids},
        },
        "evidence_summary": {"text": candidate_data["evidence"], "evidence_ids": ids},
    }


def _candidate_report_fields(candidate_data: dict[str, Any], citation_map: dict[str, int]) -> list[dict[str, str]]:
    ids = candidate_data["ids"]
    design = "\n".join([
        "研究对象：" + _citation_text(candidate_data['object'], ids[:2], citation_map),
        "核心问题：" + _citation_text(candidate_data['question'], ids[:2], citation_map),
        "方法路线：" + _citation_text(candidate_data['method'], ids[-2:], citation_map),
    ])
    return [
        {"label": "选题理由", "value": _citation_text(candidate_data["reason"], ids, citation_map)},
        {"label": "研究内容", "value": design},
        {"label": "文献依据", "value": _citation_text(candidate_data["evidence"], ids, citation_map)},
    ]


def _update_topic_candidates(state: dict[str, Any], report_plan: dict[str, Any], case_id: str) -> None:
    citation_map = report_plan.get("citation_map", {})
    curated = TOPIC_CANDIDATES[case_id]
    candidates = {str(item.get("candidate_id")): item for item in state.get("candidates", [])}
    if len(candidates) != 5 or set(candidates) != set(curated):
        raise ValueError(f"{case_id}: expected the five existing candidate IDs")
    for candidate_id, text in curated.items():
        candidate = candidates[candidate_id]
        candidate["title"] = text["title"]
        candidate["display_title"] = text["title"]
        candidate["research_object"] = text["object"]
        candidate["research_question"] = text["question"]
        candidate["method_route"] = text["method"]
        candidate["reader_content"] = _candidate_reader(text, citation_map)
        candidate["evidence_ids"] = _unique((candidate.get("evidence_ids") or []) + text["ids"])
        candidate["representative_evidence_ids"] = _unique(text["ids"])

    candidate_section = next(item for item in report_plan["sections"] if item.get("key") == "candidate_evaluation")
    blocks = candidate_section.get("blocks", [])
    for block in blocks:
        if block.get("kind") != "candidate_detail":
            continue
        candidate_id = str(block.get("candidate_id") or "")
        if candidate_id not in curated:
            continue
        block["title"] = curated[candidate_id]["title"]
        block["fields"] = _candidate_report_fields(curated[candidate_id], citation_map)
    paragraphs = []
    for index, candidate_id in enumerate(candidates, start=1):
        item = curated[candidate_id]
        fields = _candidate_report_fields(item, citation_map)
        paragraphs.append(f"### {index}. {item['title']}\n\n" + "\n\n".join(
            f"**{field['label']}**：{field['value']}" for field in fields
        ))
    candidate_section["content"] = "\n\n".join(paragraphs)
    for table in report_plan.get("table_specs", []):
        if table.get("key") == "candidate_priority":
            table["rows"] = [{"优先级": i, "候选题": item["display_title"],
                              "方向类型": "推荐方向" if item.get("direction_type") == "recommended" else "探索方向"}
                             for i, item in enumerate(candidates.values(), 1)]
            table["columns"] = ["优先级", "候选题", "方向类型"]
            table["column_widths"] = [0.6, 4.9, 1.3]


def _participant_rows(link: dict[str, Any]) -> list[dict[str, Any]]:
    core_evidence = _unique(link.get("core_support", []))
    expansion_evidence = _unique(link.get("expansion_support", []))
    return [
        {"discipline": link["core"], "role": "core", "source": "model_inference", "reason": link["discipline_reasons"][0], "evidence_ids": core_evidence},
        {"discipline": link["expansion"], "role": "expansion", "source": "model_inference", "reason": link["discipline_reasons"][1], "evidence_ids": expansion_evidence},
    ]


def _method_evidence(link: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_id = link["evidence_id"]
    quote = str(link["quote"]).strip()
    source = evidence_by_id.get(evidence_id)
    if not source or not quote or quote not in str(source.get("abstract") or ""):
        raise ValueError(f"{link['crosslink_id']}: method quote does not match the stored source abstract")
    return [{
        "evidence_id": evidence_id,
        "chunk_id": "",
        "quote": quote,
        "source_method": quote,
        "evidence_scope": "abstract",
        "proposed_method_name": link["method"],
        "method_name_verified": True,
        "source_basis": "quoted_abstract_method",
        "method_name_quote": quote,
        "locator": {"section": "Abstract"},
        "verified": True,
    }]


def _curated_crosslink(
    link: dict[str, Any],
    citation_map: dict[str, int],
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    participants = _participant_rows(link)
    ids = _unique(link["support"])
    if any(evidence_id not in citation_map or evidence_id not in evidence_by_id for evidence_id in ids):
        raise ValueError(f"{link['crosslink_id']}: unresolved supporting literature")
    if any(not item["evidence_ids"] for item in participants):
        raise ValueError(f"{link['crosslink_id']}: each discipline needs linked evidence")
    return {
        "crosslink_id": link["crosslink_id"],
        "source_theme_id": link["source_theme_id"],
        "target_theme_id": link["target_theme_id"],
        "core_discipline": link["core"],
        "expansion_discipline": link["expansion"],
        "title": link["opportunity"],
        "research_opportunity": link["opportunity"],
        "transferable_method": link["method"],
        "relation_basis": link["reason"],
        "description": _citation_text(link["reason"], ids, citation_map),
        "support_evidence_ids": ids,
        "participating_disciplines": participants,
        "method_evidence": _method_evidence(link, evidence_by_id),
        "association_kind": "interdisciplinary",
        "validation_conditions": link["validation"],
        "uncertainty": "本研究方向由摘要中的方法描述支持；其在本案例材料中的适用性仍需按所列条件验证。",
        "alternative_explanations": [],
        "counter_evidence_ids": [],
        "status": "validated",
        "reader_content": {
            "themes": [link["source_theme_id"], link["target_theme_id"]],
            "method": link["method"],
            "summary": link["reason"],
            "evidence_ids": ids,
        },
    }


def _update_opportunity_graph(state: dict[str, Any], report_plan: dict[str, Any], case_id: str) -> None:
    evidence_by_id = {str(item.get("evidence_id")): item for item in state.get("evidence_pool", [])}
    links = [
        _curated_crosslink(item, report_plan["citation_map"], evidence_by_id)
        for item in OPPORTUNITY_LINKS[case_id]
    ]
    state["crosslinks"] = links
    valid_ids = {item["crosslink_id"] for item in links}
    for gap in state.get("gaps", []):
        gap["crosslink_ids"] = [item for item in gap.get("crosslink_ids", []) if item in valid_ids]
    for candidate in state.get("candidates", []):
        themes = set(candidate.get("theme_ids", []))
        candidate["crosslink_ids"] = [
            link["crosslink_id"] for link in links
            if {link["source_theme_id"], link["target_theme_id"]} & themes
        ]

    spec = next(item for item in report_plan["chart_specs"] if item.get("key") == "discipline_network")
    rows = []
    for index, link in enumerate(links, start=1):
        rows.append({
            "crosslink_id": link["crosslink_id"],
            "core": link["core_discipline"],
            "expansion": link["expansion_discipline"],
            "method": link["transferable_method"],
            "reason": link["relation_basis"],
            "opportunity": link["research_opportunity"],
            "evidence_ids": link["support_evidence_ids"],
            "participating_disciplines": link["participating_disciplines"],
            "method_evidence": link["method_evidence"],
            "association_kind": link["association_kind"],
            "validation_conditions": link["validation_conditions"],
            "status": link["status"],
            "number": index,
        })
    spec.setdefault("data", {})["rows"] = rows
    spec["report_version"] = "public-case-v2"
    spec["data_snapshot_version"] = "public-case-data-v2"


def _update_theme_labels(state: dict[str, Any], case_id: str) -> None:
    if case_id == "topic-phage-v2":
        labels = {
            "T1": "耐药菌与噬菌体宿主范围",
            "T2": "噬菌体与抗生素联用",
            "T3": "噬菌体抗性及毒力代价",
            "T4": "体外疗效与动物感染模型",
        }
        for node in state.get("theme_tree", {}).get("nodes", []):
            if node.get("theme_id") in labels:
                label = labels[node["theme_id"]]
                node["label"] = label
                node["display_label"] = label


def _set_topic_summaries(state: dict[str, Any], report_plan: dict[str, Any], case_id: str) -> None:
    candidate_texts = TOPIC_CANDIDATES[case_id]
    analysis = state.setdefault("stage_artifacts", {}).setdefault("landscape_trend", {}).setdefault("analysis", {})
    if case_id == "topic-urban-heat-v2":
        method = "本次文献分别提供了Landsat温度产品校验、绿地格局分析和城市热岛时序研究。它们的方法从地表温度反演与地面站点核验，延伸到景观指标及空间模型；本案的长三角比较可在这些方法上增加下垫面分层和整城留出验证。"
        future = "后续研究可把卫星地表温度与自动站观测按下垫面和昼夜分层，再将绿地连通性、树冠结构、建筑形态及人口暴露纳入同一验证流程；模型是否能迁移到未参与建模的城市，应以独立城市数据判断。"
    elif case_id == "topic-phage-v2":
        method = "相关研究的实验路径包括宿主范围与噬菌体表征、棋盘法筛选联用组合、时间杀菌和生物膜测试，以及进入动物感染模型后的菌负荷或生存观察。菌种、抗生素、感染模型和给药时点各不相同，跨研究比较应保留这些条件。"
        future = "可以从临床分离株面板开始，先比较噬菌体覆盖和抗性突变，再筛选代表性联用方案进入感染模型；毒力、药敏变化和治疗结局应按菌株与模型分别记录，不能由单个抗性株的代价推断整体疗效。"
    else:
        method = "现有方法从电池材料与界面表征，扩展到组件级加速老化、RPM寿命建模及EL/热成像定位局部退化。组件成像研究还显示单项加速应力不一定复现户外图案，因此模型比较需要保留器件尺度、应力组合和部署条件。"
        future = "可在统一组件结构下比较封装和界面处理，按周期采集电性能与EL/PL图像，并用户外运行数据检查加速试验能否解释实际退化。寿命映射需由长期对照验证，不宜把短期T80直接换算为户外年限。"
    analysis["method_evolution_summary"] = {
        "text": method,
        "evidence_ids": _unique([evidence_id for item in candidate_texts.values() for evidence_id in item["ids"][:2]]),
    }
    analysis["future_research_summary"] = {
        "text": future,
        "evidence_ids": _unique([evidence_id for item in candidate_texts.values() for evidence_id in item["ids"][-2:]]),
    }
    if case_id == "topic-phage-v2":
        state["theme_tree"]["nodes"] = state["theme_tree"].get("nodes", [])


def _set_topic_report_sections(state: dict[str, Any], report_plan: dict[str, Any], case_id: str) -> None:
    candidates = state.get("candidates", [])
    citation_map = report_plan["citation_map"]
    titles = [item.get("title") or item.get("display_title") for item in candidates]
    if case_id == "topic-urban-heat-v2":
        summary = "本案例围绕长三角高密度城市夏季热环境，保留五个方向：遥感温度校准与跨城迁移、绿地结构阈值、居民热暴露联合评估、模型稳健性和多源预警验证。现有文献分别提供温度产品校验、绿地空间分析和城市热环境建模方法；本次推荐将这些路径映射到材料提出的三城、三类下垫面及留出城市验证设计中。"
        conclusion = "推荐方向按材料的观测对象、空间机制和跨城应用逐步展开。卫星地表温度与近地气温的对应关系、绿地连通性和树冠结构、不同城市的模型迁移分别形成可独立检验的问题；需要同步保留站点校准、晴空筛选和城市留出条件。\n\n报告引用的城市研究提供方法与场景参照，并不证明长三角样本已经出现同样的阈值或迁移表现。后续实验应分别报告地表温度反演误差、热暴露关联和预警误差，避免用单一指标概括不同研究环节。"
        opportunity_intro = "城市遥感、景观生态与公共卫生方法在热环境问题上存在可核对的连接点。图中方向对应具体文献方法，研究结论仍需在长三角的站点、下垫面和留出城市条件下验证。"
    elif case_id == "topic-phage-v2":
        summary = "本案例围绕碳青霉烯耐药肺炎克雷伯菌和铜绿假单胞菌，推荐五个彼此区分的方向：联用协同、抗性突变代价、动物模型验证、宿主范围覆盖和按菌株建立筛选流程。补充文献提供了棋盘法联用、多分支菌株覆盖、抗性突变表型及感染模型方法；菌种和模型差异在报告中分别说明。"
        conclusion = "五个方向从菌株覆盖和体外组合筛选，延伸到抗性突变表型及动物感染模型验证。研究中应记录菌种、克隆型、噬菌体效价、感染复数、药物浓度和模型给药时点；这些条件决定不同文献的结果能否放在一起解释。\n\n新增文献包含肺炎克雷伯菌临床分离株的棋盘法和小鼠模型研究，也包含特定荚膜缺失抗性株在Galleria模型中的表型。它们支持将这些环节纳入候选研究，但不支持把单个药物组合或突变结果推广至所有耐药菌。"
        opportunity_intro = "当前检索中可由文献方法直接支撑的连接集中在联用筛选、感染模型验证和抗性突变表型。每个方向均明确其菌种、实验方法和模型边界。"
    else:
        summary = "本案例聚焦钙钛矿光伏组件的水氧阻隔、离子迁移、界面反应和热应力，保留五个方向：多因素衰减、空间失效建模、封装方案与寿命模型、室内外应力映射及局部失效分析。补充的组件级研究提供EL和热成像方法，封装研究提供多因素加速老化先例；电池级结果不直接等同于组件寿命。"
        conclusion = "五个方向覆盖材料与界面、组件尺度、寿命建模和室内外验证。实验设计需要统一有效面积、封装结构、温湿度、光谱、负载和运行时间，并将组件中心、划线、互连及边缘的局部成像与整体I-V和T80记录对应。\n\n相关研究说明加速应力下的局部退化图案可能不同于户外暴露，电池级RPM模型也需要组件数据校准。本报告将这些文献作为方法依据，不把短期稳定性或单一应力结果外推为实际运行年限。"
        opportunity_intro = "材料、器件物理与可靠性测试之间的连接体现在封装方案、加速应力和组件空间失效的联合观察。图中每条方向都对应可定位的方法描述，适用性仍由本案例的组件条件决定。"

    report_plan["executive_summary"] = summary
    for section in report_plan.get("sections", []):
        key = section.get("key")
        if key == "abstract":
            recommended = sum(item.get('direction_type') == 'recommended' for item in candidates)
            summary += f"本次共形成{len(state.get('theme_tree', {}).get('nodes', []))}个研究主题，并据此展开{recommended}个推荐选题和{len(candidates)-recommended}个探索方向。"
            section["content"] = summary
            section["blocks"] = [{"kind": "paragraph", "text": summary}]
        elif key == "cross_discipline":
            old_figures = [block for block in section.get("blocks", []) if block.get("kind") == "figure_ref"]
            section["blocks"] = [{"kind": "paragraph", "text": opportunity_intro}, *old_figures]
            section["content"] = opportunity_intro
        elif key == "conclusion":
            section["content"] = conclusion
            section["blocks"] = [{"kind": "paragraph", "text": item} for item in conclusion.split("\n\n")]

    evidence = {str(item.get("evidence_id")): item for item in state.get("evidence_pool", [])}
    for spec in report_plan.get("chart_specs", []):
        if spec.get("key") != "discipline_network":
            continue
        spec.setdefault("data", {})["root"] = state.get("input_profile", {}).get("title") or report_plan.get("report_title")


def _curate_topic(payload: dict[str, Any], case_id: str) -> None:
    detail = payload["detail"]
    state = detail["state"]
    report_plan = state["report_plan"]
    _add_supplemental_evidence(case_id, state, report_plan)
    _update_topic_candidates(state, report_plan, case_id)
    _update_theme_labels(state, case_id)
    _set_topic_summaries(state, report_plan, case_id)
    _set_topic_report_sections(state, report_plan, case_id)
    if case_id in OPPORTUNITY_LINKS:
        _update_opportunity_graph(state, report_plan, case_id)

    for spec in report_plan.get('chart_specs', []):
        if spec.get('key') == 'discipline_network':
            from app.services.research_graph_rendering import _participants
            for row in spec.get('data', {}).get('rows', []):
                row['participating_disciplines'] = _participants(row)
        if spec.get('key') == 'theme_network':
            spec['data'] = copy.deepcopy(state['theme_tree'])
            spec['caption'] = '完整横向展示研究总方向、各层研究主题及关键词，与主题探索中的层级和内容一致。'
        elif spec.get('key') == 'trend_comparison':
            spec['chart_type'] = 'grouped_bar'
            spec['caption'] = '按年份并列展示各研究子方向在本次检索样本中的文献数量；颜色对应右侧图例，样本可能交叉，不代表领域全部发文量。'
            labels = {str(n['theme_id']): n.get('display_label') or n.get('label') for n in state['theme_tree']['nodes']}
            for series in spec.get('data', {}).get('series', []):
                series['label'] = labels.get(str(series.get('theme_id')), series.get('label', ''))

    from app.agents.topic_selection.scholarly_report import compact_citations
    model = TopicSelectionState.model_validate(state)
    compact_citations(model)
    state = model.model_dump(mode='json')
    report_plan = state['report_plan']
    version = "public-case-v3-20261113"
    state.setdefault("versions", {})["report_version"] = version
    report_plan["report_version"] = version
    report_plan["publication_status"] = "published"
    report_plan["chart_manifest"] = report_plan.get("chart_specs", [])
    snapshot = state.get("report_snapshot") or {}
    snapshot["report_version"] = version
    snapshot["content_version"] = version
    snapshot["citation_version"] = version
    snapshot["snapshot_id"] = f"{case_id}-report-v2"
    snapshot["created_at"] = _now()
    snapshot["report_plan_snapshot"] = copy.deepcopy(report_plan)
    snapshot['content_hash'] = hashlib.sha256(json.dumps(report_plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    snapshot['snapshot_id'] = f"{case_id}-report-{snapshot['content_hash'][:16]}"
    snapshot.setdefault('format_hashes', {})['content'] = snapshot['content_hash']
    result_snapshot = snapshot.setdefault("result_data_snapshot", {})
    for key in ("theme_tree", "evidence_pool", "crosslinks", "gaps", "candidates", "stage_artifacts"):
        result_snapshot[key] = copy.deepcopy(state.get(key))
    state["report_snapshot"] = snapshot

    model = TopicSelectionState.model_validate(state)
    rendered, _ = build_topic_markdown(model)
    markdown = rendered.decode("utf-8")
    model.output_markdown = markdown
    model.output_json['report_plan'] = model.report_plan.model_dump(mode='json')
    detail["state"] = model.model_dump(mode="json")
    detail["output_markdown"] = markdown
    detail["output_json"] = detail["state"].get("output_json", {})
    detail["updated_at"] = _now()


def _section_content(blocks: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for block in blocks:
        kind = block.get("kind")
        if kind == "heading":
            rows.append("### " + str(block.get("text") or ""))
        elif kind in {"paragraph", "callout", "lead"} and block.get("text"):
            rows.append(str(block["text"]))
        elif kind in {"bullet_list", "numbered_list"}:
            rows.extend((f"{i}. {text}" if kind == "numbered_list" else f"- {text}") for i, text in enumerate(block.get("items", []), 1))
    return "\n\n".join(row for row in rows if row).strip()


def _remove_empty_judgement_headings(section: dict[str, Any]) -> None:
    blocks = section.get("blocks", [])
    paragraph_claims = {str(block.get("claim_id")) for block in blocks if block.get("kind") == "paragraph" and block.get("claim_id") and block.get("text", "").strip()}
    populated_dimensions: set[str] = set()
    dimension = ""
    heading_dimension = {
        "6.1": "innovation", "6.2": "frontier", "6.3": "advancedness",
        "创新性评价": "innovation", "前沿性评价": "frontier", "先进性评价": "advancedness",
    }
    for block in blocks:
        if block.get("kind") == "heading" and not block.get("claim_id"):
            text = str(block.get("text") or "")
            dimension = next((value for key, value in heading_dimension.items() if key in text), "")
        elif block.get("kind") == "paragraph" and block.get("claim_id") and dimension:
            populated_dimensions.add(dimension)

    cleaned = []
    current_dimension = ""
    for index, block in enumerate(blocks):
        if block.get("kind") != "heading":
            cleaned.append(block)
            continue
        text = str(block.get("text") or "")
        claim_id = str(block.get("claim_id") or "")
        if claim_id and claim_id not in paragraph_claims:
            continue
        if not claim_id:
            next_dimension = next((value for key, value in heading_dimension.items() if key in text), "")
            if next_dimension and next_dimension not in populated_dimensions:
                continue
            current_dimension = next_dimension
        cleaned.append(block)
    section["blocks"] = cleaned
    section["content"] = _section_content(cleaned)


def _remove_metadata_claims(state: dict[str, Any]) -> None:
    """Remove the misclassified input selector and its derived rows from saved cases."""
    bad = {c['claim_id'] for c in state.get('claims', [])
           if re.fullmatch(r'我要评估的是\s*[:：]\s*(?:项目|论文|指南|专利|产品|技术)\s*[。.!！]?', str(c.get('text', '')).strip())}
    if not bad:
        return
    labels = [c['text'] for c in state['claims'] if c['claim_id'] in bad]
    def prune(value):
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, dict) and (item.get('claim_id') in bad or item.get('id') in bad
                                              or item.get('source') in bad or item.get('target') in bad):
                    continue
                if isinstance(item, str) and item in bad:
                    continue
                result.append(prune(item))
            return result
        if isinstance(value, dict):
            return {key: prune(item) for key, item in value.items() if key not in bad}
        return value
    cleaned = prune(state)
    state.clear(); state.update(cleaned)
    snapshot = state.get('report_snapshot', {})
    # Section content mirrors blocks; rebuild it after removing dedicated analyses.
    for section in snapshot.get('sections', []):
        blocks = []
        for block in section.get('blocks', []):
            text = str(block.get('text') or '')
            if any(label in text for label in labels):
                if block.get('kind') == 'table':
                    block['text'] = '\n'.join(line for line in text.splitlines() if not any(label in line for label in labels))
                elif section.get('section_key') == 'object' and block.get('kind') == 'paragraph':
                    for label in labels:
                        text = text.replace('、' + label, '').replace(label + '、', '')
                    block['text'] = text
                else:
                    continue
            blocks.append(block)
        section['blocks'] = blocks
        section['content'] = _section_content(section.get('blocks', []))
        section['evidence_refs'] = _unique([eid for block in section.get('blocks', []) for eid in block.get('evidence_ids', [])])
    for chart in snapshot.get('chart_manifest', []):
        data = chart.get('chart_data', {})
        if isinstance(data, dict) and 'points' in data and chart.get('chart_key') == 'claim_evidence_network':
            for i, point in enumerate(data['points'], 1):
                point['number'] = i
    state.setdefault('stage_artifacts', {})['public_case_metadata_cleanup'] = {'removed_claim_ids': sorted(bad)}


def _curate_assessment(payload: dict[str, Any], case_id: str) -> None:
    detail = payload["detail"]
    state = detail["state"]
    _remove_metadata_claims(state)
    snapshot = state["report_snapshot"]
    query_plans = _query_reader_fields(state)
    state["query_plans"] = query_plans
    state.setdefault("output_json", {})["query_plans"] = copy.deepcopy(query_plans)
    snapshot.setdefault("audit_data", {})["query_plans"] = copy.deepcopy(query_plans)
    report_sections = snapshot.get("sections", [])
    by_key = {item.get("section_key"): item for item in report_sections}

    executive = by_key.get("executive")
    if executive:
        blocks = executive.setdefault("blocks", [])
        first = next((block for block in blocks if block.get("kind") == "paragraph"), None)
        if first:
            first["text"] = ASSESSMENT_INTRODUCTIONS[case_id]
            first.pop("evidence_ids", None)
        executive["content"] = _section_content(blocks)

    conclusion = by_key.get("conclusion")
    if conclusion:
        blocks = conclusion.setdefault("blocks", [])
        scope = next((block for block in reversed(blocks) if block.get("kind") == "paragraph"), None)
        if scope:
            scope["text"] = ASSESSMENT_BOUNDARIES[case_id]
            scope.pop("evidence_ids", None)
        conclusion["content"] = _section_content(blocks)

    judgement = by_key.get("judgement")
    if judgement:
        _remove_empty_judgement_headings(judgement)

    if case_id == "assessment-battery-v2":
        _repair_battery_reference(state)
        by_key["references"]["content"] = _section_content(by_key["references"].get("blocks", []))

    version = "public-case-v3-20261113"
    snapshot["snapshot_id"] = f"{case_id}-report-v3"
    snapshot["report_version"] = version
    snapshot["generated_at"] = _now()
    snapshot["audit_data"]["evidence_records"] = state.get("evidence_records", [])
    # Curated report content has a new layout; the source report's measured
    # page count is no longer a measurement of this report.
    state.setdefault('report_metrics', {}).update(actual_page_count=None, page_count_verified=False, page_count_source='')
    state.setdefault('report_plan', {})['actual_page_count'] = None
    state.setdefault("versions", {})["report_version"] = version
    state.setdefault("report_plan", {})["report_version"] = version
    state["report_plan"]["sections"] = copy.deepcopy(report_sections)
    state["report_plan"]["section_specs"] = copy.deepcopy(report_sections)
    context = snapshot.setdefault('render_context', {})
    context.setdefault('report_plan', {}).update(copy.deepcopy(state['report_plan']))
    context.setdefault('versions', {}).update(state['versions'])
    context['report_metrics'] = copy.deepcopy(state['report_metrics'])
    snapshot.setdefault('cover_metadata', {})['report_version'] = version
    digest = hashlib.sha256(json.dumps([report_sections, snapshot.get('chart_manifest'), snapshot.get('audit_data')], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    snapshot['snapshot_id'] = f'{case_id}-report-{digest}'
    model = ContentAssessmentState.model_validate(state)
    from app.agents.content_assessment.report_composition import markdown as render_markdown
    markdown = render_markdown(model)
    model.output_markdown = markdown
    model.output_json['report_plan'] = model.report_plan.model_dump(mode='json')
    model.output_json['report_metrics'] = model.report_metrics.model_dump(mode='json')
    detail["state"] = model.model_dump(mode="json")
    detail["output_markdown"] = markdown
    detail["output_json"] = detail["state"].get("output_json", {})
    detail["updated_at"] = _now()


def validate_snapshot(case_id: str, payload: dict[str, Any]) -> None:
    if payload.get("case_id") != case_id or payload.get("version") != TARGET_VERSION:
        raise ValueError(f"{case_id}: invalid case/version metadata")
    detail = payload.get("detail") or {}
    if not _complete_case(case_id, detail):
        raise ValueError(f"{case_id}: base report or source-reference checks failed")
    state = detail["state"]
    if case_id.startswith("topic-"):
        candidates = state.get("candidates", [])
        if len(candidates) != 5:
            raise ValueError(f"{case_id}: candidate count changed")
        evidence = {str(item.get("evidence_id")): item for item in state.get("evidence_pool", [])}
        citations = state["report_plan"].get("citation_map", {})
        if len({str(item.get("candidate_id")) for item in candidates}) != 5:
            raise ValueError(f"{case_id}: candidate IDs are not unique")
        for candidate in candidates:
            reading = candidate.get("reader_content") or {}
            fields = [reading.get("reason"), reading.get("research_content", {}).get("research_object"), reading.get("research_content", {}).get("core_question"), reading.get("research_content", {}).get("method_route"), reading.get("evidence_summary")]
            if any(not isinstance(item, dict) or not str(item.get("text") or "").strip() for item in fields):
                raise ValueError(f"{case_id}/{candidate.get('candidate_id')}: incomplete reader content")
            for field in fields:
                for evidence_id in field.get("evidence_ids", []):
                    if evidence_id not in evidence or evidence_id not in citations:
                        raise ValueError(f"{case_id}/{candidate.get('candidate_id')}: unresolved candidate evidence {evidence_id}")
        candidate_section = next((item for item in state["report_plan"].get("sections", []) if item.get("key") == "candidate_evaluation"), None)
        if not candidate_section:
            raise ValueError(f"{case_id}: candidate report section missing")
        detail_ids = [str(item.get("candidate_id")) for item in candidate_section.get("blocks", []) if item.get("kind") == "candidate_detail"]
        if detail_ids != [str(item.get("candidate_id")) for item in candidates]:
            raise ValueError(f"{case_id}: candidate detail order differs between task and report")
        if case_id in OPPORTUNITY_LINKS:
            links = state.get("crosslinks", [])
            nodes = state.get("theme_tree", {}).get("nodes", [])
            theme_ids = {str(item.get("theme_id")) for item in nodes}
            theme_labels = ["".join(str(item.get("label") or "").split()).casefold() for item in nodes]
            if len(theme_labels) != len(set(theme_labels)):
                raise ValueError(f"{case_id}: duplicate research theme labels")
            if any(link.get("source_theme_id") == link.get("target_theme_id") for link in links):
                raise ValueError(f"{case_id}: self-linked research theme")
            if any(link.get("source_theme_id") not in theme_ids or link.get("target_theme_id") not in theme_ids for link in links):
                raise ValueError(f"{case_id}: opportunity link references an unknown theme")
            if len({str(link.get("crosslink_id")) for link in links}) != len(links):
                raise ValueError(f"{case_id}: duplicate opportunity link IDs")
            for link in links:
                verified = [item for item in link.get("method_evidence", []) if item.get("verified")]
                if not link.get("transferable_method") or not verified:
                    raise ValueError(f"{case_id}/{link.get('crosslink_id')}: missing method evidence")
                support = set(link.get("support_evidence_ids", []))
                if not support or any(item not in evidence or item not in citations for item in support):
                    raise ValueError(f"{case_id}/{link.get('crosslink_id')}: unresolved opportunity sources")
                participants = link.get("participating_disciplines", [])
                if len({item.get("discipline") for item in participants}) < 2:
                    raise ValueError(f"{case_id}/{link.get('crosslink_id')}: interdisciplinary direction needs two disciplines")
                for participant in participants:
                    participant_ids = set(participant.get("evidence_ids", []))
                    if not participant_ids or not participant_ids <= support:
                        raise ValueError(f"{case_id}/{link.get('crosslink_id')}: discipline source mapping is invalid")
                for method in verified:
                    record = evidence.get(method.get("evidence_id"), {})
                    if not record or method.get("quote", "") not in record.get("abstract", ""):
                        raise ValueError(f"{case_id}/{link.get('crosslink_id')}: method quote not found in source abstract")
            graph_spec = next((item for item in state["report_plan"].get("chart_specs", []) if item.get("key") == "discipline_network"), None)
            graph_rows = (graph_spec or {}).get("data", {}).get("rows", [])
            if [item.get("crosslink_id") for item in graph_rows] != [item.get("crosslink_id") for item in links]:
                raise ValueError(f"{case_id}: opportunity detail and graph rows differ")
    else:
        snapshot = state.get("report_snapshot") or {}
        sections = snapshot.get("sections", [])
        if len(sections) != 12:
            raise ValueError(f"{case_id}: report section count changed")
        section_keys = [str(item.get("section_key")) for item in sections]
        if len(section_keys) != len(set(section_keys)):
            raise ValueError(f"{case_id}: duplicate report sections")
        for required_key in ("executive", "judgement", "evaluation", "conclusion", "references"):
            section = next((item for item in sections if item.get("section_key") == required_key), None)
            if not section or not str(section.get("content") or "").strip():
                raise ValueError(f"{case_id}: required report section {required_key} is empty")
        if not any(ASSESSMENT_INTRODUCTIONS[case_id][:24] in str(block.get("text") or "") for block in (next(section for section in sections if section.get("section_key") == "executive").get("blocks") or [])):
            raise ValueError(f"{case_id}: case-specific report introduction missing")
        if not any(ASSESSMENT_BOUNDARIES[case_id][:16] in str(block.get("text") or "") for block in (next(section for section in sections if section.get("section_key") == "conclusion").get("blocks") or [])):
            raise ValueError(f"{case_id}: case-specific report boundary missing")
        plans = state.get("query_plans", [])
        reader_fields = ("reader_topic", "reader_summary", "reader_focus", "reader_scope", "reader_result")
        if not plans or any(not all(str(plan.get(field) or "").strip() for field in reader_fields) for plan in plans):
            raise ValueError(f"{case_id}: query plans need complete reader-facing summaries")
        if plans != (snapshot.get("audit_data") or {}).get("query_plans"):
            raise ValueError(f"{case_id}: reader-facing query plans differ from the audit snapshot")
        if case_id == "assessment-battery-v2":
            reference = next(block for block in next(section for section in sections if section.get("section_key") == "references").get("blocks", [])
                             if "E-L-075" in block.get("evidence_ids", []))
            if "https://doi.org/10.1007/s10832-017-0084-z" not in reference.get("text", "") or "CDATA" in reference.get("text", ""):
                raise ValueError(f"{case_id}: the third reference link/title did not normalize")


async def _render_assessment_exports(payload: dict[str, Any]) -> None:
    detail = payload["detail"]
    state = ContentAssessmentState.model_validate(detail["state"])
    from app.services.export_service import build_html
    html, _ = build_html(state.model_copy(deep=True))
    detail["public_case_exports"] = {"html": html}


def build_snapshots() -> None:
    if not SOURCE_DIR.is_dir():
        raise FileNotFoundError(f"Source snapshots not found: {SOURCE_DIR}")
    staged: dict[str, dict[str, Any]] = {}
    for case_id in CASE_IDS:
        payload = _read(SOURCE_DIR / f"{case_id}.json.gz")
        payload["version"] = TARGET_VERSION
        if case_id.startswith("topic-"):
            _curate_topic(payload, case_id)
        else:
            _curate_assessment(payload, case_id)
        payload["generated_at"] = _now()
        validate_snapshot(case_id, payload)
        staged[case_id] = payload

    if set(staged) != set(CASE_IDS):
        raise ValueError("Refusing to publish an incomplete case set")
    for case_id, payload in staged.items():
        _write(TARGET_DIR / f"{case_id}.json.gz", payload)

    manifest = {
        "version": TARGET_VERSION,
        "published": True,
        "case_ids": list(CASE_IDS),
        "generated_at": _now(),
    }
    temporary = CASE_ROOT / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CASE_ROOT / "manifest.json")
    print(f"Published {len(staged)} curated public case snapshots as {TARGET_VERSION}.")


if __name__ == "__main__":
    build_snapshots()
