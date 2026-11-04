from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "资料" / "测试输入" / "创新性评估" / "创新性评估智能体测试资料" / "五领域实测报告集_20260917"


def shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_text(cell, text: str, bold: bool = False, color: str = "000000") -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.05
    run = p.add_run(str(text))
    run.bold = bold
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def add_table(doc: Document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = True
    for index, header in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], header, bold=True, color="FFFFFF")
        shade(table.rows[0].cells[index], "1F4E78")
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for index, value in enumerate(row):
            set_cell_text(cells[index], value)
            if row_index % 2:
                shade(cells[index], "F3F6F8")
    if widths:
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Cm(width)
    doc.add_paragraph()


def configure_doc(doc: Document) -> None:
    section = doc.sections[0]
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.25
    normal.paragraph_format.space_after = Pt(6)
    for style_name, size in (("Title", 19), ("Heading 1", 14), ("Heading 2", 11.5)):
        style = styles[style_name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.bold = True


def add_title(doc: Document, title: str, code: str) -> None:
    p = doc.add_paragraph(style="Title")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run(title)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"项目编号 {code}  | 版本 1.0  | 2026 年 9 月")
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(89, 89, 89)


def add_body(doc: Document, text: str) -> None:
    for paragraph in text.split("\n"):
        if paragraph.strip():
            doc.add_paragraph(paragraph.strip())


DOMAINS = [
    {
        "key": "新能源",
        "folder": "01_新能源_钠离子电池低温快充预警",
        "code": "IA-SIB-2026-001",
        "title": "面向电网储能的钠离子电池低温快充与热失控早期预警系统",
        "direction": "电网侧储能、钠离子电池、低温充电和热安全监测",
        "goal": "建立面向 100 kWh 级储能单元的低温快充与热失控早期预警原型，验证电芯级多传感融合、充电策略和故障预警的可行性。",
        "target_baseline": "公开资料显示钠离子电池适合低成本和宽温区探索，但不同电芯体系、SOC 窗口、环境温度和热安全试验条件差异很大，不能把跨体系结果直接视为同口径基线。",
        "claims": [
            ("C1", "系统在 -20 °C 环境下可将 20% 至 80% SOC 的充电时间控制在 45 min 内，并保持单体温差不超过 4 °C。", "target"),
            ("C2", "基于电压、温度、压力和声发射的多传感融合能够在热失控前 10 min 以上识别异常趋势。", "planned"),
            ("C3", "采用脉冲电流与预热协同策略，相比恒流恒压策略可降低低温充电极化。", "planned"),
            ("C4", "在 0 °C 至 25 °C 的循环条件下，系统可使 1000 次循环后的容量保持率达到 90% 以上。", "target"),
            ("C5", "储能簇级预警模型可在不依赖单一电芯阈值的情况下迁移到两种钠离子正极体系。", "planned"),
            ("C6", "该系统可直接作为电网储能项目的消防验收和并网安全认证依据。", "planned"),
        ],
        "indicators": [
            ("I1", "低温充电时间", "min", "45", "拟达到"),
            ("I2", "充电末端最大单体温差", "°C", "4", "拟达到"),
            ("I3", "热失控预警提前量", "min", "10", "拟达到"),
            ("I4", "1000 次循环容量保持率", "%", "90", "拟达到"),
            ("I5", "预警准确率", "%", "95", "拟达到"),
            ("I6", "误报率", "%", "<3", "统计口径待补"),
            ("I7", "跨体系迁移性能下降", "%", "<8", "拟达到"),
            ("I8", "系统采样延迟", "ms", "100", "构造结果"),
        ],
        "experiments": [
            ["低温快充", "-20 °C 恒温箱；20%-80% SOC", "恒流恒压", "n=12 电芯/组，3 批", "时间、温差、极化电压", "Welch t 检验；95% CI", "构造结果，部分未达目标"],
            ["预警验证", "热滥用触发前采集 1 Hz + 声发射", "单传感温度阈值", "n=30 事件，含 8 个异常事件", "提前量、召回率、误报率", "事件级混淆矩阵", "构造结果"],
            ["循环寿命", "0/10/25 °C；1C 充放电", "商业 BMS 策略", "n=9 模组", "容量保持率、内阻", "重复测量 ANOVA", "构造结果，目标与结果不一致"],
            ["迁移测试", "两种正极体系；同一模型不重训", "分别训练模型", "每体系 n=10", "F1、校准误差", "Bootstrap 95% CI", "构造结果"],
        ],
        "sources": [
            ["SIB-A1", "Energy Storage Grand Challenge Roadmap", "U.S. Department of Energy", "2020", "官方路线图全文", "A", "https://www.energy.gov/sites/default/files/2020/12/f81/Energy-Storage-Grand-Challenge-Roadmap_0.pdf", "PDF 第 1-4 页，储能技术和安全目标"],
            ["SIB-A2", "Battery Safety", "National Renewable Energy Laboratory", "2024", "官方主题页", "A", "https://www.nrel.gov/transportation/battery-safety.html", "网页标题和 Battery Safety 主题段落"],
            ["SIB-B1", "Sodium-ion batteries: present and future", "Nature Energy", "2021", "论文题录与摘要", "B", "https://doi.org/10.1038/s41560-021-00842-5", "摘要级；未用于证明本项目指标"],
            ["SIB-B2", "Battery Archive", "Argonne National Laboratory", "2024", "公开数据平台说明", "B", "https://www.anl.gov/cse/battery-archive", "平台说明页；数据体系与实验条件需逐项核对"],
            ["SIB-C1", "Sodium ion battery thermal runaway early warning", "题录级补充记录", "2025", "只有标题和关键词", "C", "https://www.sciencedirect.com/topics/engineering/sodium-ion-battery", "题录级，禁止支撑强创新结论"],
        ],
        "boundary": "可讨论技术路线、实验可行性和证据充分性；不得将构造数据、论文摘要或实验室预警结果直接推导为消防验收、并网许可或监管认证。",
        "risks": "关键风险包括低温内阻变化导致模型漂移、热失控事件样本稀缺、不同正极体系的传感信号不可比，以及误报率定义未固定。",
    },
    {
        "key": "新材料",
        "folder": "02_新材料_可回收动态共价环氧复合材料",
        "code": "IA-DCE-2026-002",
        "title": "可回收动态共价环氧复合材料的高强度制备与低能耗再制造",
        "direction": "动态共价网络、环氧复合材料、玻纤增强和循环再制造",
        "goal": "构造一套可拆解、可重塑的动态共价环氧复合材料路线，比较其强度保持、再加工能耗和纤维回收质量。",
        "target_baseline": "热固性环氧材料的高强度、耐热和可回收通常存在权衡；公开研究可证明动态键网络提供再加工思路，但不自动证明本配方在相同纤维体积分数和固化条件下领先。",
        "claims": [
            ("C1", "动态共价环氧基体在保持 120 MPa 以上拉伸强度的同时可完成 5 次热重塑。", "target"),
            ("C2", "引入可交换键后，复合材料再制造能耗相比传统热解回收下降 35% 以上。", "planned"),
            ("C3", "该材料的玻纤界面结合强度与常规双酚 A 环氧体系相当。", "planned"),
            ("C4", "五次重塑后的弯曲强度保持率不低于 85%，且未明确是否按初始试样或首轮重塑值计算。", "target"),
            ("C5", "回收玻纤表面处理和动态键交换可在同一温和工艺窗口内完成。", "planned"),
            ("C6", "该复合材料已经满足风电叶片结构件的长期服役和产品认证要求。", "planned"),
        ],
        "indicators": [
            ("I1", "拉伸强度", "MPa", "120", "拟达到"),
            ("I2", "弯曲强度保持率", "%", "85", "基线口径待补"),
            ("I3", "重塑次数", "次", "5", "拟达到"),
            ("I4", "再制造单位能耗", "MJ/kg", "<18", "构造结果"),
            ("I5", "玻纤回收率", "%", "90", "拟达到"),
            ("I6", "界面剪切强度", "MPa", "42", "拟达到"),
            ("I7", "热变形温度", "°C", "110", "拟达到"),
            ("I8", "挥发物释放量", "mg/m²", "<5", "构造结果"),
        ],
        "experiments": [
            ["基体重塑", "160 °C；20 min；压片重塑", "普通环氧不可重塑", "n=5 配方，每配方 6 片", "强度、凝胶分数", "单因素 ANOVA", "构造结果"],
            ["复合材料力学", "玻纤体积分数 45%；23 °C", "双酚 A 环氧", "每组 n=10", "拉伸、弯曲、层间剪切", "均值±SD；效应量", "构造结果，C4 基线未锁定"],
            ["能耗核算", "粉碎、热处理、再浸润全流程", "热解回收", "3 批工艺重复", "kWh/kg、MJ/kg", "过程清单法", "构造结果"],
            ["回收玻纤", "再制造后表面观察和拉伸", "原始玻纤", "n=30 单丝/批", "回收率、强度保持", "Bootstrap CI", "构造结果"],
        ],
        "sources": [
            ["DCE-A1", "A highly recyclable thermoset polymer", "Science", "2014", "开放题录与摘要", "B", "https://doi.org/10.1126/science.1252240", "摘要级；比较对象与本配方不同"],
            ["DCE-A2", "Plastics Innovation Challenge", "U.S. Department of Energy", "2024", "官方项目页", "A", "https://www.energy.gov/eere/bioenergy/plastics-innovation-challenge", "官方页的循环材料目标和项目范围"],
            ["DCE-A3", "Sustainable Materials Management", "U.S. Environmental Protection Agency", "2024", "官方主题页", "A", "https://www.epa.gov/smm", "网页的生命周期和资源效率边界"],
            ["DCE-B1", "Polymer Characterization", "National Institute of Standards and Technology", "2024", "官方主题页", "B", "https://www.nist.gov/programs-projects/polymer-characterization", "方法平台说明，未提供本项目强度基线"],
            ["DCE-C1", "Dynamic covalent epoxy composite recycling", "题录级补充记录", "2025", "只有标题", "C", "https://pubs.acs.org/journal/mamobx", "题录级，禁止支撑领先结论"],
        ],
        "boundary": "可评估材料组成、力学性能、能耗核算和回收工艺；不得据此宣称已满足风电叶片、航空或压力容器结构件的长期认证要求。",
        "risks": "关键风险包括动态键交换温度与纤维表面处理冲突、能耗边界遗漏、循环次数定义不统一，以及实验室小试结果不能替代疲劳和环境老化验证。",
    },
    {
        "key": "生物医药",
        "folder": "03_生物医药_肿瘤类器官免疫共培养药敏",
        "code": "IA-PDO-2026-003",
        "title": "患者来源肿瘤类器官与免疫细胞共培养的个体化药敏预测",
        "direction": "患者来源类器官、免疫细胞共培养、药物反应和个体化预测",
        "goal": "建立患者来源肿瘤类器官与自体免疫细胞共培养实验流程，探索其对药物反应分层和临床结局预测的可行性。",
        "target_baseline": "类器官和组织芯片研究可用于机制和药敏探索，但样本来源、培养基、免疫细胞比例、药物暴露时长和临床终点差异会显著影响比较。",
        "claims": [
            ("C1", "共培养模型可在 14 天内保持肿瘤类器官活性并形成可重复的免疫浸润读数。", "target"),
            ("C2", "基于类器官药敏和免疫浸润特征的模型可预测患者对联合治疗的 8 周影像学反应。", "planned"),
            ("C3", "自体免疫细胞共培养相比单独类器官培养能够提高药物反应分层的区分度。", "planned"),
            ("C4", "模型的预测 AUC 可达到 0.85 以上，但当前材料未规定阳性判定阈值和独立验证集。", "target"),
            ("C5", "采用统一基质和自动成像后，不同患者样本间的批次效应可控制在 10% 以内。", "planned"),
            ("C6", "该检测可直接用于临床处方选择并替代现行肿瘤药敏和伴随诊断流程。", "planned"),
        ],
        "indicators": [
            ("I1", "类器官活性保持时间", "day", "14", "拟达到"),
            ("I2", "免疫浸润评分 ICC", "%", "0.80", "拟达到"),
            ("I3", "预测 AUC", "无量纲", "0.85", "验证集未定义"),
            ("I4", "灵敏度", "%", "80", "拟达到"),
            ("I5", "特异度", "%", "80", "拟达到"),
            ("I6", "批次效应 CV", "%", "<10", "拟达到"),
            ("I7", "样本成功率", "%", "75", "构造结果"),
            ("I8", "结果周转时间", "day", "10", "构造结果"),
        ],
        "experiments": [
            ["模型稳定性", "患者样本；14 天；统一基质", "单独类器官", "12 位患者，每人 3 个技术重复", "活性、浸润、形态", "混合效应模型", "构造结果"],
            ["药敏分层", "标准药物与联合治疗；72 h", "单药/无药对照", "n=12 患者；训练/测试 8/4", "AUC、敏感性、特异度", "预注册阈值；Bootstrap CI", "构造结果，独立验证不足"],
            ["批次效应", "3 批基质和 2 名操作者", "同批内重复", "每条件 n=6", "CV、ICC", "方差分量分析", "构造结果"],
            ["临床一致性", "影像学反应作为探索终点", "历史记录回顾", "n=8 可配对样本", "一致率", "仅探索性，不作临床结论", "构造结果"],
        ],
        "sources": [
            ["PDO-A1", "Organs-on-Chips", "U.S. Food and Drug Administration", "2024", "官方主题页", "A", "https://www.fda.gov/science-research/advancing-regulatory-science/organs-chips", "官方页的监管科学和模型边界"],
            ["PDO-A2", "Tissue Chips", "NIH NCATS", "2024", "官方主题页", "A", "https://ncats.nih.gov/research/research-activities/tissue-chips", "官方页的组织芯片研究范围"],
            ["PDO-B1", "Patient-derived organoids for cancer research", "PubMed", "2020", "题录与摘要", "B", "https://pubmed.ncbi.nlm.nih.gov/32733100/", "摘要级；不证明本项目临床预测性能"],
            ["PDO-B2", "Precision Medicine", "National Cancer Institute", "2024", "官方主题页", "B", "https://www.cancer.gov/research/areas/precision-medicine", "临床转化背景，非本项目验证"],
            ["PDO-C1", "Organoid immune co-culture drug response", "题录级补充记录", "2025", "只有题录", "C", "https://pubmed.ncbi.nlm.nih.gov/?term=organoid+immune+co-culture+drug+response", "题录级，禁止支撑强临床结论"],
        ],
        "boundary": "可评估体外模型稳定性、药敏区分和预测模型的探索性性能；不得把构造数据、回顾性小样本或 FDA/NCATS 研究主题页解读为临床认可、处方替代或伴随诊断批准。",
        "risks": "关键风险包括样本异质性、免疫细胞活性衰减、训练测试泄漏、临床终点不一致和伦理/隐私合规要求。",
    },
    {
        "key": "人工智能",
        "folder": "04_人工智能_科研文献多模态知识发现",
        "code": "IA-MKD-2026-004",
        "title": "面向科研文献的可验证多模态知识发现与假设生成",
        "direction": "科研文献检索、图表理解、知识图谱和可验证假设生成",
        "goal": "构造一套以文献全文、图表和结构化数据库为输入的知识发现原型，要求每条假设绑定证据片段、比较条件和可执行验证任务。",
        "target_baseline": "文献语言模型、知识图谱和大模型检索增强系统已有大量研究；生成新颖假设不等于科学有效，也不等于在相同数据集和评价协议下优于已有系统。",
        "claims": [
            ("C1", "系统可以从文本和图表联合输入中抽取对象、方法、数据、基线、指标、时间和验证条件七字段。", "planned"),
            ("C2", "每条生成假设能够回链到至少两段可重新打开的原文证据并给出反证搜索式。", "planned"),
            ("C3", "多模态输入相比纯文本检索可将科学实体关系抽取 F1 提高 8 个百分点。", "target"),
            ("C4", "系统生成的候选假设在专家盲评中具有较高的新颖性和可行性，但专家评分量表尚未固定。", "planned"),
            ("C5", "基于证据图的约束解码可以降低不可验证假设比例至 15% 以下。", "target"),
            ("C6", "系统输出可以直接作为基金评审结论或科研项目立项依据。", "planned"),
        ],
        "indicators": [
            ("I1", "七字段抽取 F1", "%", "85", "拟达到"),
            ("I2", "证据回链完整率", "%", "90", "拟达到"),
            ("I3", "关系抽取 F1 提升", "百分点", "8", "对照协议待补"),
            ("I4", "不可验证假设比例", "%", "<15", "拟达到"),
            ("I5", "专家新颖性评分", "1-5 分", "4.0", "量表待补"),
            ("I6", "专家可行性评分", "1-5 分", "3.5", "量表待补"),
            ("I7", "引用准确率", "%", "95", "构造结果"),
            ("I8", "单条假设响应时间", "s", "30", "构造结果"),
        ],
        "experiments": [
            ["字段抽取", "200 篇含图表论文；固定切分", "纯文本抽取", "训练/验证/测试 120/40/40", "字段级 P/R/F1", "宏平均；95% CI", "构造结果"],
            ["假设生成", "材料科学和生物医学各 50 篇", "无证据约束生成", "每篇生成 3 条", "可验证率、新颖性", "双评审一致性", "构造结果"],
            ["回链测试", "全文段落、图表说明和数据库记录", "仅标题引用", "300 条引用", "链接可打开率、定位准确率", "人工抽样审计", "构造结果"],
            ["鲁棒性", "同义词、扫描图表和缺失字段", "标准输入", "4 类扰动各 50 条", "性能下降幅度", "分层 Bootstrap", "构造结果，部分数据缺失"],
        ],
        "sources": [
            ["MKD-A1", "AI Risk Management Framework 1.0", "NIST", "2023", "官方全文 PDF", "A", "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf", "PDF 第 1-5 页及 GOVERN 主题"],
            ["MKD-A2", "Artificial Intelligence Risk Management Framework Generative AI Profile", "NIST", "2024", "官方全文 PDF", "A", "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf", "PDF 的生成式 AI 风险和评估边界"],
            ["MKD-B1", "Scientific discovery with large language models", "Nature / PubMed", "2024", "题录与摘要", "B", "https://pubmed.ncbi.nlm.nih.gov/?term=large+language+models+scientific+discovery", "摘要级；不证明本系统性能"],
            ["MKD-B2", "AlphaFold", "Nature", "2021", "论文题录与摘要", "B", "https://doi.org/10.1038/s41586-021-03819-2", "方法先例，任务和数据不同"],
            ["MKD-C1", "Multimodal literature hypothesis generation", "题录级补充记录", "2025", "只有标题和关键词", "C", "https://arxiv.org/search/?query=multimodal+scientific+hypothesis+generation&searchtype=all", "题录级，禁止支撑领先结论"],
        ],
        "boundary": "可评估抽取、回链、生成和人评协议；不得将模型输出当作基金评审结论、科研事实或自动立项依据，尤其不能把引用存在性替代证据内容核验。",
        "risks": "关键风险包括引用幻觉、图表解析误读、评审者偏差、训练测试污染、知识库时效性和敏感科研数据泄露。",
    },
    {
        "key": "先进制造",
        "folder": "05_先进制造_激光粉末床熔融闭环监测",
        "code": "IA-LPBF-2026-005",
        "title": "激光粉末床熔融钛合金原位多传感缺陷监测与闭环调控",
        "direction": "激光粉末床熔融、钛合金、原位监测、缺陷识别和闭环调控",
        "goal": "构造多传感原位监测和参数闭环调控方案，探索对孔隙、未熔合和熔道不稳定的识别及抑制能力。",
        "target_baseline": "增材制造监测研究常见视觉、光电、声学和熔池信号融合，但设备型号、粉末批次、几何特征、缺陷标注和后验 CT 标准不同，跨设备结果不能直接比较。",
        "claims": [
            ("C1", "系统能够在打印过程中融合熔池辐射、声发射和层面图像，识别三类典型缺陷。", "planned"),
            ("C2", "闭环调控可将试样孔隙率从 1.2% 降至 0.5% 以下。", "target"),
            ("C3", "在相同扫描速度和激光功率下，多传感模型的缺陷召回率高于单一视觉模型。", "planned"),
            ("C4", "系统对不同几何件和不同粉末批次保持 90% 以上的识别准确率，但材料未定义跨域测试集。", "target"),
            ("C5", "基于缺陷置信度的参数调整能够在不牺牲表面粗糙度的条件下提升致密度。", "planned"),
            ("C6", "该系统可直接替代航空钛合金零件的无损检测和质量放行。", "planned"),
        ],
        "indicators": [
            ("I1", "孔隙率", "%", "<0.5", "拟达到"),
            ("I2", "缺陷召回率", "%", "90", "拟达到"),
            ("I3", "缺陷识别准确率", "%", "90", "跨域集待补"),
            ("I4", "调控响应延迟", "ms", "200", "拟达到"),
            ("I5", "相对密度", "%", "99.5", "拟达到"),
            ("I6", "表面粗糙度 Ra", "µm", "<12", "拟达到"),
            ("I7", "层间误报率", "%", "<5", "统计口径待补"),
            ("I8", "粉末批次迁移性能", "%", "构造结果", "未定义"),
        ],
        "experiments": [
            ["缺陷识别", "Ti-6Al-4V；三类人工缺陷和自然缺陷", "单视觉模型", "120 层；缺陷事件 n=180", "P/R/F1、定位误差", "按构件分组交叉验证", "构造结果"],
            ["闭环调控", "在线调节功率和扫描速度", "开环打印", "20 个试样，10 对照/10 调控", "孔隙率、密度、粗糙度", "ANCOVA；95% CI", "构造结果，孔隙率目标部分未达"],
            ["跨批次", "3 粉末批次；2 种几何件", "同批训练", "每域 n=30", "准确率、校准误差", "分层 Bootstrap", "构造结果"],
            ["后验验证", "工业 CT + 金相切片", "仅 CT 抽样", "40 个试样", "缺陷一致率", "双方法一致性", "构造结果"],
        ],
        "sources": [
            ["LPBF-A1", "Additive Manufacturing", "NIST", "2024", "官方主题页", "A", "https://www.nist.gov/additive-manufacturing", "官方主题页的测量、标准和过程控制范围"],
            ["LPBF-A2", "AM-Bench", "NIST", "2024", "官方项目页", "A", "https://www.nist.gov/ambench", "公开基准试验说明和数据可比性边界"],
            ["LPBF-A3", "Additive Manufacturing Standards", "ASTM International", "2024", "官方主题页", "A", "https://www.astm.org/industry/additive-manufacturing.html", "标准和认证边界，不是本项目性能证据"],
            ["LPBF-B1", "In-situ monitoring of laser powder bed fusion", "PubMed", "2022", "题录与摘要", "B", "https://pubmed.ncbi.nlm.nih.gov/?term=in-situ+monitoring+laser+powder+bed+fusion", "摘要级；设备和缺陷定义需核对"],
            ["LPBF-C1", "Multi-sensor closed-loop control titanium LPBF", "题录级补充记录", "2025", "只有关键词", "C", "https://www.nist.gov/ambench", "题录级，禁止支撑领先结论"],
        ],
        "boundary": "可评估原位监测、控制响应、后验验证和实验设计；不得将实验室样件的 CT/金相结果直接替代航空件适航、无损检测或质量放行。",
        "risks": "关键风险包括缺陷标注偏差、传感器同步误差、控制策略引入新的热历史、跨设备域偏移和工业检测标准不一致。",
    },
]


def project_doc(domain: dict) -> Document:
    doc = Document()
    configure_doc(doc)
    add_title(doc, f"{domain['title']} 项目申报书", domain["code"])
    add_body(doc, f"本申报书用于创新性评估智能体的真实模拟测试。研究方向：{domain['direction']}。\n\n项目名称、内部实验记录和性能数据均为构造测试数据，不代表真实项目成果；外部来源仅用于验证材料解析、证据分级、可比性和引用边界。")
    doc.add_heading("一 项目概况", level=1)
    add_body(doc, domain["goal"] + "\n\n" + domain["target_baseline"])
    doc.add_heading("二 研究内容与技术路线", level=1)
    add_body(doc, "项目包含材料制备或数据准备、实验/模型验证、基线对照、统计分析和应用边界复核五个工作包。项目目标值是拟达到的验收目标，不应被写作已经完成的实测结果。所有内部数据均在实验数据工作簿中以“构造测试数据”标记。")
    doc.add_heading("三 六条原子主张", level=1)
    add_table(doc, ["主张编号", "原子主张", "材料状态", "预期证据"], [[cid, text, status, "项目材料 + 外部证据 + 构造实验数据"] for cid, text, status in domain["claims"]], [1.6, 9.0, 2.0, 4.0])
    doc.add_heading("四 量化指标与验收口径", level=1)
    add_table(doc, ["指标", "名称", "单位", "申报目标", "状态", "需补口径"], [[i, name, unit, target, status, "基线/统计/验证条件按技术路线确认"] for i, name, unit, target, status in domain["indicators"]], [1.3, 4.2, 2.0, 2.3, 2.0, 5.3])
    doc.add_heading("五 目标和结果的区分", level=1)
    add_body(doc, "申报书只表达研究目标、计划和验收条件，不将任何目标值视为已实现结果。已完成或部分未达标的构造实验数据只出现在实验数据工作簿，并由技术路线文件说明样本、对照、统计和验证条件。")
    doc.add_heading("六 项目边界", level=1)
    add_body(doc, domain["boundary"])
    doc.add_heading("七 可观测测试差异", level=1)
    add_body(doc, "本案例故意包含一条存在相近先例的主张、一条只有方向性证据的主张、一条高于公开基线的目标、一项缺少完整基线或统计定义的指标，以及至少一处申报目标与构造实验结果不一致。评估系统应在报告中保留这些差异，不应用综合分数抹平。")
    return doc


def route_doc(domain: dict) -> Document:
    doc = Document()
    configure_doc(doc)
    add_title(doc, f"{domain['title']} 技术路线与实施方案", domain["code"])
    add_body(doc, "本文件描述实验或模型实施、对照、样本量、统计方法和质量门。内部实施记录是构造测试数据，仅用于检测评估链路是否能把计划、结果、外部证据和合规边界分开。")
    doc.add_heading("一 工作包与实施步骤", level=1)
    add_table(doc, ["工作包", "实施内容", "输出", "验证节点"], [[f"WP-{i+1}", row[0], row[4], row[6]] for i, row in enumerate(domain["experiments"])], [2.0, 7.0, 4.0, 4.0])
    doc.add_heading("二 实验或模型设计", level=1)
    add_table(doc, ["试验模块", "条件", "对照", "样本设计", "主要指标", "统计规则", "数据性质"], domain["experiments"], [2.2, 4.0, 3.0, 3.1, 3.3, 4.0, 3.2])
    doc.add_heading("三 七字段比较规则", level=1)
    add_body(doc, "每条外部证据均需按以下七字段逐项比较：对象、方法、数据、基线、指标、时间、验证条件。任一关键字段不一致时，关系应标记为不可比或部分支持，并说明需要补充的实验或检索任务。论文数量、关键词重叠和项目目标值不能替代七字段比较。")
    add_table(doc, ["字段", "本项目记录方式", "缺失时处理"], [["对象", "材料/电芯/患者样本/模型/构件及规格", "禁止跨对象直接比较"], ["方法", "流程、仪器、模型、参数和版本", "说明方法差异"], ["数据", "样本量、来源、训练测试划分或重复", "降为方向性支持"], ["基线", "对照组、公开基线或历史值", "标记基线缺失"], ["指标", "定义、单位、计算分母和阈值", "禁止使用模糊指标"], ["时间", "暴露、循环、随访或版本时间窗", "说明时间不可比"], ["验证条件", "环境、设备、后验检测、独立验证", "列出补证任务"]], [2.5, 8.5, 6.5])
    doc.add_heading("四 统计与质量门", level=1)
    add_body(doc, "连续变量报告 n、均值、标准差或置信区间；分类结果报告混淆矩阵、召回率和误报率；模型结果固定训练/验证/测试边界，避免数据泄漏。质量门在证据全文定位、主张—证据回链、指标定义和导出完整性任一项缺失时应提示探索性或待补证。")
    doc.add_heading("五 风险和补证任务", level=1)
    add_body(doc, domain["risks"] + "\n\n建议补证：补充同对象同方法基线、提供原始数据和数据字典、固定统计口径、补充独立验证集、保留证据全文定位，并对应用或监管结论单独检索正式标准和监管文件。")
    return doc


def boundary_doc(domain: dict) -> Document:
    doc = Document()
    configure_doc(doc)
    add_title(doc, f"{domain['title']} 应用场景与标准监管边界", domain["code"])
    add_body(doc, "本文件用于测试系统对应用、产业、标准和监管语句的边界治理。材料中所有项目数据均为构造测试数据；官方网页和标准主题页只证明其公开范围，不代表对本项目的认可。")
    doc.add_heading("一 允许形成的结论", level=1)
    add_body(doc, "可以形成：技术路线是否清楚、内部数据是否达到材料自定义的探索性目标、外部证据是否存在方向性支持、比较字段是否完整、下一轮验证需要做什么。")
    doc.add_heading("二 禁止直接推断的结论", level=1)
    add_body(doc, domain["boundary"])
    doc.add_heading("三 应用、标准和监管证据", level=1)
    add_table(doc, ["结论类型", "可使用来源", "本案例处理", "禁止表达"], [["应用场景", "官方项目页、公开数据平台、标准范围", "只作为背景和需求证据", "已经商业化或可直接部署"], ["性能", "同对象同条件全文研究和内部数据", "按七字段逐项比较", "跨对象直接领先"], ["安全/质量", "正式标准、监管指南、认证文件", "无正式文件则数据不足", "已通过认证或验收"], ["临床/工程放行", "监管批准、适航/质量放行记录", "本案例不生成此类结论", "替代现有检测或处方"]], [3.0, 5.3, 5.6, 5.2])
    doc.add_heading("四 风险分层", level=1)
    add_body(doc, "高风险：把目标值写成结果、把摘要或题录写成全文证据、把论文主题页写成监管认可、把不具备可比条件的对象直接比较。\n中风险：遗漏样本量、统计方法、基线或验证条件。\n低风险：格式、文件命名或引用展示问题，但仍需在导出检查中记录。")
    doc.add_heading("五 评估建议措辞", level=1)
    add_body(doc, "推荐使用“材料显示”“构造数据提示”“公开来源提供方向性背景”“在当前条件下尚不能证明”“需要补充同口径基线”等限定语。避免使用“首次”“国际领先”“已获认可”“可直接替代”等强断言，除非有可核验的正式证据。")
    return doc


def write_text_files(domain: dict, folder: Path) -> None:
    prompt = f"""# {domain['title']} 测试说明

项目编号：`{domain['code']}`。本套材料用于创新性评估智能体真实模拟测试。

- 项目申报书中的目标值、技术路线和实验数据中的结果均为构造测试数据。
- 外部来源优先使用官方页面、官方全文 PDF、公开论文题录或摘要；无法核验全文的记录只能为 B/C 级。
- 评估时必须区分创新性和先进性，逐条检查对象、方法、数据、基线、指标、时间、验证条件七字段。
- 请识别 C1-C6 六条主张，核对目标值与实测结果，不要把目标值写成已完成结果。
- 请特别检查一条相近先例、一条方向性支持、一条高于公开基线的目标、一个缺少完整统计口径的指标，以及目标与构造结果不一致的位置。

统一提示词：

请对已上传的 {domain['code']} 项目材料执行一次深度、综合范围的创新性评估。先列出材料版本、角色、哈希、可重新打开的定位和证据边界；再抽取六条原子主张和指标。对每条主张选择最接近的外部证据，按七字段比较。严格区分项目目标、构造实验结果、外部证据、创新性、先进性、可行性和应用/监管边界。A/B/C 证据必须说明可定位范围和结论强度。报告应包含主张逐项判定、指标治理、风险、补证任务、参考文献、检索记录和主张-证据-判定对照表。摘要和题录不能支撑强创新或领先结论。
"""
    (folder / "00_测试说明与提示词.md").write_text(prompt, encoding="utf-8")
    supplemental = [
        f"# {domain['title']} 补充证据\n",
        "以下记录用于测试证据等级降级，不是项目实测数据。\n",
        *[f"- {row[0]} | {row[1]} | {row[2]} | {row[4]}级 | 来源：{row[6]} | 定位：{row[7]}\n" for row in domain["sources"]],
        "\n处理规则：A 级需有全文或官方页面的可复核定位；B 级为摘要或官方主题页背景；C 级为只有题录/关键词的记录。没有全文定位时，不得将记录升级为强创新、国际领先或监管认可证据。\n",
    ]
    (folder / "06_摘要级和题录级补充证据.txt").write_text("".join(supplemental), encoding="utf-8")


def write_manifest(domain: dict, folder: Path) -> None:
    (folder / "材料清单.csv").write_text("", encoding="utf-8")
    rows = [
        ["文件名", "格式", "材料角色", "关联主张", "数据性质", "说明"],
        [f"01_项目申报书_{domain['key']}.docx", "DOCX", "PROJECT", "C1-C6", "构造测试材料", "研究背景、六条原子主张和目标指标"],
        [f"02_技术路线与实施方案_{domain['key']}.docx", "DOCX", "BASELINE", "C1-C6", "构造测试材料", "实验、对照、统计、七字段和质量门"],
        [f"03_实验数据与统计表_{domain['key']}.xlsx", "XLSX", "EVIDENCE", "C1-C6", "构造测试数据", "原始观测、汇总、数据字典和质控规则"],
        [f"04_文献证据与主张对照矩阵_{domain['key']}.xlsx", "XLSX", "REFERENCE", "C1-C6", "公开来源 + 测试矩阵", "来源、证据等级、查询任务和七字段比较"],
        [f"05_应用场景与合规边界说明_{domain['key']}.docx", "DOCX", "REFERENCE", "C1-C6", "边界说明", "允许结论、禁止推断、标准监管和补证任务"],
        ["06_摘要级和题录级补充证据.txt", "TXT", "EVIDENCE", "C1、C4", "公开来源摘要/题录", "第二轮证据降级测试"],
        ["00_测试说明与提示词.md", "MD", "REFERENCE", "C1-C6", "测试配置", "统一提示词和预期观察点"],
    ]
    with (folder / "材料清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for domain in DOMAINS:
        folder = OUT / domain["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        project_doc(domain).save(folder / f"01_项目申报书_{domain['key']}.docx")
        route_doc(domain).save(folder / f"02_技术路线与实施方案_{domain['key']}.docx")
        boundary_doc(domain).save(folder / f"05_应用场景与合规边界说明_{domain['key']}.docx")
        write_text_files(domain, folder)
        write_manifest(domain, folder)
    spec_path = OUT / "domain_specs.json"
    spec_path.write_text(json.dumps(DOMAINS, ensure_ascii=False, indent=2), encoding="utf-8")
    for folder in [OUT / d["folder"] for d in DOMAINS]:
        rows = []
        for path in sorted(folder.iterdir()):
            if path.name in {"材料哈希清单.csv"} or path.is_dir():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append([path.name, path.suffix.lstrip(".").upper(), str(path.stat().st_size), digest])
        with (folder / "材料哈希清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows([["文件名", "格式", "字节数", "SHA256"], *rows])
    readme = OUT / "00_五领域测试总说明.md"
    readme.write_text("""# 五领域创新性评估真实模拟测试\n\n本目录包含新能源、新材料、生物医药、人工智能和先进制造五套真实模拟项目材料。项目名称、内部实验/模型数据和部分实施记录均为构造测试数据；公开来源保留原始 URL、访问边界和证据等级。\n\n每套材料固定包含项目申报书、技术路线与实施方案、实验数据与统计表、文献证据与主张对照矩阵、应用场景与合规边界说明、摘要级和题录级补充证据、测试说明以及哈希清单。\n\n上传与任务创建应使用后端 `/v1/files/upload` 和 `/v1/content-assessment/run`；不要直接写入 Mongo 任务记录。\n""", encoding="utf-8")
    print(json.dumps({"output": str(OUT), "domains": len(DOMAINS)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
