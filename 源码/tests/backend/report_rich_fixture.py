"""Fixed, explicitly synthetic engineering dossier for pagination regression.

No generated observation in this fixture is used in real assessments.
"""
from app.agents.content_assessment.schemas import (
    ContentAssessmentState, Claim, ClaimAssessmentDetail, EvidenceRecord,
    ClaimEvidenceLink, NoveltyDecision, Indicator, UploadedFileDigest,
    DocumentRecord, StageTraceItem, QueryPlan, LandscapeDataCard, ScoreCardItem,
)
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.report_composition import freeze_snapshot, build_specs, compose
from unittest.mock import patch


def legacy_report(s, historical=False):
    """Explicitly exercise the retained V4/reference composer, not the new V5."""
    s.report_snapshot = None
    s.output_json.pop("scholarly_report", None)
    s.output_json.pop("research_report", None)
    s.report_plan.render_version = "reference-layout-v2" if historical else "phase2-layout-v1"
    s.report_plan.section_specs = build_specs(s)
    compose(s, s.report_plan.section_specs)
    s.report_plan.target_page_min = 20 if historical else 0
    s.report_plan.target_page_max = 26 if historical else 0
    s.report_metrics.page_target_status = "unverified" if historical else "not_applicable"
    freeze_snapshot(s, historical=historical)
    # This fixture represents an already published historical dossier. The
    # new composer may pause its preliminary run; legacy publication is explicit.
    s.current_stage = "completed"
    s.confirmation.needed = False


def rich_state(mode, historical=False, scholarly=False):
    s = ContentAssessmentState(raw_user_input="材料充分的报告分页回归样本")
    s.assessment_profile.title = s.assessment_profile.assessment_object = "微流控平台方法学验证模拟评估"
    s.report_preferences.mode = mode
    s.report_preferences.mode_source = "user"
    s.report_preferences.artifact_type = "project"
    systems = [
        ("灌流压力控制", "压差传感器", "kPa", "管路顺应性", "阀门滞后", "阶跃响应", "稳态误差"),
        ("氧浓度监测", "光学氧探头", "%", "探头光漂白", "温度漂移", "两点校准", "缺测比例"),
        ("内皮屏障测定", "阻抗电极", "Ω·cm²", "电极极化", "培养基电导率", "空白膜扣除", "屏障恢复率"),
        ("肝脏损伤读出", "荧光读板器", "U/L", "培养液背景", "检测试剂批次", "加标回收", "检出限"),
        ("心肌收缩记录", "高速相机", "Hz", "光照波动", "视频帧丢失", "固定时间窗分析", "搏动变异"),
        ("跨操作者复现", "装配质控流程", "%", "操作者熟练度", "装配顺序", "盲法样品编号", "批间变异"),
    ]
    labels = {"object": "对象", "method": "方法", "data": "数据", "baseline": "基线", "metric": "指标", "time": "时间", "validation": "验证条件"}
    for i, (topic, instrument, unit, factor, other, method, metric) in enumerate(systems, 1):
        cid = f"C-{i:03d}"
        fields = {
            "object": f"{topic}模块以{instrument}为主要观测设备，观测单位为独立装配的芯片。记录材料批号、几何尺寸、细胞或介质来源，区分校准样与处理样。每次实验均核对样品编号与设备日志，排除将同一芯片多次读取当作独立重复的情况。",
            "method": f"以{method}建立测量链路，先执行设备空白、阴性条件和阳性条件检查，再采集连续信号。重点控制{factor}和{other}，完整记录算法版本、原始读数和排除规则。装配和读数阶段采用不同人员复核，分析前固定窗口长度及异常值定义。",
            "data": f"模拟研究{i}提供三批独立实验，每批十二个芯片，每个芯片保留基线与末端两次记录。记录包含原始测量、样品温度、处理标识和质控判定。无效芯片保留失败原因；汇总同时给出有效样本数、分布范围和效应量。此处所有结果均为软件回归用模拟数据。",
            "baseline": f"{topic}以同批未处理样品及仪器空白作为双重参照。背景扣除先于归一化，样品处理前读数用于检查初始不平衡。参考条件和实验条件共享温度、介质与采样时刻，避免把测量增益变化当作生物学改善。基线来源随原始文件同时保存。",
            "metric": f"{metric}采用{unit}记录，明确定义分子、分母与统计窗口。相对变化必须同时给出未经归一化的原始水平。评价同时检查平均表现与极端样本，防止总体均值掩盖个体失效。方案中的验收门槛为预设目标，不因观察到的数据而反向调整。",
            "time": f"模拟研究{i}设置起始、六小时、二十四小时、四十八小时四个预定时间点。记录从处理开始计时的实际采样偏差。出现中断时保留中断长度，不能把缺测区间补为零。跨日测量核对设备重启、校准和培养条件变化。",
            "validation": f"{topic}验证要求三名操作者独立完成装配，至少一个批次由未参与开发的人员复核。以芯片作为重采样单位估计置信区间，报告排除前后结果。预先规定{factor}超限与{other}异常的处理方式，无法满足条件时仅形成探索性观察。",
        }
        quote = f"拟验证{topic}在规定测量条件下的稳定性及可重复性。"
        s.claims.append(Claim(claim_id=cid, text=quote, source_document_id=f"F{i}", source_span=quote,
            source_char_start=0, source_char_end=len(quote), assertion_type="target", searchable_terms=[topic], claim_fields=fields))
        s.uploaded_file_context.files.append(UploadedFileDigest(file_id=f"F{i}", name=f"{topic}方案", content=quote + "\n" + "\n".join(fields.values())))
        s.indicators.append(Indicator(indicator_id=f"I{i}", name=metric, definition=fields["metric"], unit=unit,
            baseline=fields["baseline"], test_method=method, acceptance_condition="需依据预注册门槛复核", source_claim_id=cid))
        ids = []
        for j, route in enumerate(["基准路线", "改进设计", "独立验证", "失效研究", "长期观测", "替代方案"], 1):
            eid = f"E{i}{j}"
            ids.append(eid)
            alternate = {key: f"{route}研究{i}-{j}的{label}记录：{'。'.join(value.split('。')[:2])}。保存第{j}批{instrument}校准记录，结果限定于{topic}边界。" for key, (label, value) in zip(fields, zip(labels.values(), fields.values()))}
            s.evidence_records.append(EvidenceRecord(evidence_id=eid, title=f"{topic}的{route}及条件验证", source_type="literature",
                year=2020+j, doi=f"10.9999/synthetic.{i}.{j}", url=f"https://example.org/synthetic/{i}/{j}",
                authors=["模拟研究组"], venue="回归测试资料",
                abstract=f"{route}关注{factor}对{topic}的影响。{alternate['method']} {alternate['data']}", comparison_fields=alternate))
            s.claim_evidence_links.append(ClaimEvidenceLink(claim_id=cid, evidence_id=eid,
                relation="contradicts" if j == 4 else "not_comparable",
                difference=f"{route}研究采用不同的{method}参数，{metric}的统计窗口与项目预定条件并不完全相同。需逐项核对样品、{instrument}校准及{factor}。研究编号{i}-{j}的数据不能直接替代项目验收。",
                uncertainty=f"第{j}条路线的{other}控制条件不同，当前未核验全文。", confidence=.3))
        s.claim_assessments.append(ClaimAssessmentDetail(claim_id=cid, normalized_claim=quote,
            nearest_prior_art_id=ids[0], field_matrix={key:"partial" for key in fields},
            evidence_gaps=[f"补充{instrument}校准原件", f"复核{factor}控制范围", f"锁定{metric}验收门槛"],
            conclusion=f"{topic}存在候选路线，需按共同基线复核。"))
        s.novelty_decisions.append(NoveltyDecision(claim_id=cid, label="目标待验证",
            rationale=f"{topic}的材料包含可检验方案，但{factor}与{other}的控制尚需独立重复；所列{metric}不能由设计目标直接推导。",
            evidence_ids=ids[:2], rewrite_advice=f"将{topic}表述限定在{method}、独立芯片样本和预注册统计窗口内。"))
    s.assessment_profile.assessment_goal = "检查同口径证据、实验可复现性及报告交付质量"
    s.assessment_profile.scope_boundary = "模拟工程方案与可核验测量链路；不评估任何真实商业产品。"
    s.assessment_profile.document_records = [DocumentRecord(document_id=f.file_id, name=f.name, role="PROJECT", status="parsed", content_available=True) for f in s.uploaded_file_context.files]
    names = [("document_intake", "材料解析"), ("claim_extraction", "主张提取"), ("retrieval", "证据检索"), ("comparison", "主张比对"), ("assessment", "创新评估"), ("landscape", "态势分析"), ("quality_gate", "质量检查"), ("report", "报告发布")]
    s.stage_trace = [StageTraceItem(stage_key=key, stage_name=name, status="completed", summary=f"{name}完成。所有观测均来自隔离回归资料，保留来源和验证边界。") for key, name in names]
    s.query_plans = [QueryPlan(claim_id=c.claim_id, query=" ".join(c.searchable_terms), query_group=c.searchable_terms[0], candidate_count=6, selected_count=6, source_status="completed") for c in s.claims]
    s.trend_analysis = {"summary": "各方法学主题均包含跨年度记录，跨主题统计限定于本次模拟证据池。", "themes": [{"label": topic[0], "evidence_ids": [e.evidence_id for e in s.evidence_records if e.evidence_id.startswith(f"E{i}")]} for i, topic in enumerate(systems, 1)]}
    s.market_data_cards = [LandscapeDataCard(card_id=f"M{year}", card_type="market", subject="回归模拟设备市场", value=str(value), unit="亿元", period=str(year), source_evidence_ids=["E11"], status="available", observation_level="quantitative") for year, value in [(2024, 12), (2025, 15)]]
    s.competitive_data_cards = [LandscapeDataCard(card_id=f"P{i}", card_type="competitive", subject=f"模拟对象{i}", x_value=float(i * 10), y_value=float(70 + i * 5), x_label="连续采集时长", x_unit="小时", y_label="有效样本率", y_unit="%", source_evidence_ids=[f"E1{i}"], status="available", observation_level="quantitative") for i in range(1, 4)]
    s.scorecard = [ScoreCardItem(dimension=name, assessment="回归用显式区间", score_range="2–4", basis=["E11"]) for name in ("创新性", "先进性", "可行性", "风险", "证据充分性", "应用与合规")]
    with patch("app.agents.content_assessment.graph.CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES", True):
        ContentAssessmentGraph()._report(s)
    if not scholarly:
        legacy_report(s, historical)
    return s
