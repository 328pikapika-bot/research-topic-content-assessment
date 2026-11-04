"""Versioned research report outline. Execution traces stay in audit data.

Only newly generated reports use this composer. Frozen historical sections
and their citation maps are never migrated by a read/export operation.
"""
from __future__ import annotations

import re

from .schemas import ReportBlock, ReportSectionSpec

TEMPLATE = "ContentAssessmentResearchReportV7"
RENDER = "research-layout-v7"
SCHOLARLY_TEMPLATES = {"ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6", TEMPLATE}

CHAPTERS = [
    ("executive", "摘要"),
    ("scope", "第一章 评估说明"),
    ("object", "第二章 评估对象的内容与核心主张"),
    ("progress", "第三章 相关研究、技术或规范基础"),
    ("methods", "第四章 证据基础与评估方法"),
    ("comparison", "第五章 核心内容与关键指标对照分析"),
    ("judgement", "第六章 创新性、前沿性与先进性评价"),
    ("evaluation", "第七章 分类评价"),
    ("conclusion", "第八章 综合评估结论"),
]
EVALUATION = {
    "paper": ("科学性、可靠性与可重复性评价", [
        ("design", "研究设计合理性"), ("results", "实验或数据对结论的支持"),
        ("reproducibility", "方法可重复性"), ("limits", "结论局限")]),
    "project": ("技术可行性、实施条件与风险边界", [
        ("route", "技术路线完整性"), ("validation", "指标可验证性"),
        ("conditions", "实施条件"), ("risks", "系统集成与工程化风险")]),
    "guideline": ("规范性、完整性与适用性评价", [
        ("completeness", "条款完整性"), ("clarity", "要求清晰度"),
        ("execution", "指标可执行性和可验收性"), ("alignment", "与现有标准的衔接"),
        ("applicability", "实际应用适用性")]),
}


def evaluation_dimensions(kind):
    return dict(EVALUATION.get(kind, ("研究设计与适用边界评价", [
        ("design", "研究设计与证据依据"), ("limits", "适用条件与判断边界")]))[1])


def chapter_definitions(kind):
    """Single source of titles, subheadings and prompt dimensions for V6."""
    dimensions = {
        "scope": [("object", "评估对象与材料版本"), ("purpose", "评估目的与核心问题"),
                  ("sources", "评估范围和证据来源"), ("principles", "评价原则与术语说明"),
                  ("limits", "结论适用范围与限制")],
        "object": [("background", "材料背景"), ("content", "研究内容、技术路线或条款结构"),
                   ("points", "核心研究问题、主张与技术指标"), ("status", "已完成结果与计划目标"),
                   ("scope", "纳入评估的要点")],
        "progress": [("landscape", "领域研究格局"), ("representatives", "代表性研究和主要技术路线"),
                     ("advances", "关键技术进展与已有工作"), ("trends", "发展趋势与待验证问题")],
        "methods": [("selection", "证据筛选范围"), ("mapping", "评估要点与证据对应关系"),
                    ("comparability", "指标与条件的可比性"), ("quality", "证据质量、缺口和不确定性"),
                    ("limits", "评估方法的适用边界")],
        "comparison": [("points", "评估要点逐项分析"), ("prior", "相关研究与比较"),
                       ("metrics", "关键指标和验证条件比较"), ("differences", "研究对象与方法差异")],
        "judgement": [("innovation", "创新性评价"), ("frontier", "前沿性评价"),
                      ("advancedness", "先进性评价"), ("value", "学术价值或应用价值"),
                      ("limits", "评价结论的证据依据和适用条件")],
        "evaluation": list(evaluation_dimensions(kind).items()),
        "conclusion": [("overall", "总体评估结论"), ("scope", "结论适用范围"),
                       ("validation", "后续验证重点")],
    }
    if kind == "guideline":
        dimensions["progress"] = [("landscape", "相关标准和指南体系"), ("representatives", "国内外规范要求"),
                                  ("advances", "现有条款的适用范围和不足"), ("trends", "技术或管理发展趋势")]
    return [(key, "第七章 " + EVALUATION.get(kind, ("研究设计与适用边界评价", []))[0]
             if key == "evaluation" else title, dimensions.get(key, [])) for key, title in CHAPTERS]


def compose_research_report(state):
    from .report_composition import refresh_section, material_sources, STATUS
    from .scholarly_report import point_title, reader_prose, selected_sources

    kind = state.report_preferences.resolved_artifact_type
    definitions = chapter_definitions(kind)
    sections = {key: ReportSectionSpec(section_key=key, title=title, order=i)
                for i, (key, title, _) in enumerate(definitions, 1)}
    content = {}

    def add(key, dimension, text, ids=(), cid="", block_kind="paragraph"):
        if text:
            content.setdefault((key, dimension), []).append(ReportBlock(
                kind=block_kind, text=str(text), evidence_ids=list(dict.fromkeys(ids)), claim_id=cid,
                heading_level=4 if block_kind == "heading" and cid else 3))

    def table(key, dimension, headers, rows, ids=()):
        if rows:
            def cell(value):
                return str(value or "未提供").replace("|", "／").replace("\n", " ")
            text = "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n"
            text += "\n".join("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
            add(key, dimension, text, ids, block_kind="table")

    profile = state.assessment_profile
    records = {e.evidence_id: e for e in state.evidence_records}
    materials = material_sources(state)
    drafts = state.output_json.get("scholarly_drafts", {})
    label = {"paper": "论文", "project": "项目", "guideline": "指南"}.get(kind, "研究材料")
    boundary = profile.scope_boundary or state.boundary_matrix.evidence_boundary or "本次提交材料及实际引用的外部证据"
    date = state.boundary_matrix.time_boundary or "以封面所列资料截止日期为准"
    add("executive", "", f"评估对象为“{profile.assessment_object or profile.title}”，材料类型为{label}。"
        f"评估目的为{profile.assessment_goal}，范围为{boundary}。材料目标、材料自述结果与外部文献结果分别评价。")
    for row in state.output_json.get("scholarly_summary", []):
        add("executive", "", row["text"], [eid for eid in row.get("evidence_ids", []) if eid in records])
    add("scope", "object", profile.assessment_object or profile.title or "用户提交的研究材料")
    table("scope", "object", ["材料", "版本或内容依据"],
          [[d.name, d.content_hash or "材料未标注版本"] for d in profile.document_records]
          or [[m.name, m.content_hash or "材料未标注版本"] for m in materials])
    add("scope", "purpose", profile.assessment_goal)
    add("scope", "sources", f"范围：{boundary}。时间范围：{date}。检索来源和策略详见附录A。")
    add("scope", "principles", "创新性考察方法、结构、机制或应用方式的新增贡献；前沿性考察与当前研究问题的关联；"
        "先进性须基于可比较的对象、单位、基线和实验条件。检索未命中不构成原创证明。")
    add("scope", "limits", "本报告形成材料与证据范围内的评价，不替代独立实验、实施验证或规范符合性认证。")
    add("object", "background", state.nine_fields.research_direction or profile.research_domain)
    add("object", "content", state.nine_fields.research_content or state.nine_fields.technical_route
        or profile.scope_summary or "以下按材料中的独立要点呈现其研究内容。")
    table("object", "status", ["内容状态", "材料中的要点"],
          [[STATUS.get(c.assertion_type, "状态待确认"), point_title(c)] for c in state.claims])
    add("object", "scope", "、".join(point_title(c) for c in state.claims) or "当前材料未形成可独立评估的要点。")
    from .report_methods import representative_evidence_ids, structured_method_rows
    methods = state.output_json.get("scholarly_methods") or structured_method_rows(state)
    for dimension in ("selection", "mapping", "comparability", "quality", "limits"):
        for row in methods.get(dimension, []):
            ids = [eid for eid in row.get("evidence_ids", []) if eid in records]
            if len(ids) == len(row.get("evidence_ids", [])):
                prose = reader_prose(row.get("text", ""))
                matching = [claim.claim_id for claim in state.claims
                            if dimension == "mapping" and point_title(claim) in prose and
                            ids and any(link.claim_id == claim.claim_id and link.evidence_id in ids
                                        for link in state.claim_evidence_links)]
                add("methods", dimension, prose, ids, matching[0] if len(matching) == 1 else "")
    used = {eid for (key, _), blocks in content.items() if key in {"executive", "methods"}
            for block in blocks for eid in block.evidence_ids}
    for claim in state.claims:
        cid, name = claim.claim_id, point_title(claim)
        allowed = {e.evidence_id for e in selected_sources(state, claim)}
        point = drafts.get(cid, {})
        add("object", "points", name, cid=cid, block_kind="heading")
        add("object", "points", STATUS.get(claim.assertion_type, "材料陈述") + "：" + claim.text, cid=cid)
        shown = set()
        for field, title in (("object", "研究对象"), ("method", "研究方法或拟采用方法"), ("baseline", "对照基础"),
                             ("metric", "观察指标"), ("validation", "材料中的验证安排")):
            if claim.claim_fields.get(field):
                clauses = []
                for clause in re.split(r"(?<=[。；])\s*", claim.claim_fields[field]):
                    identity = re.sub(r"\s+", "", clause).strip("。； ")
                    if identity and identity not in shown:
                        clauses.append(clause)
                        shown.add(identity)
                if clauses:
                    add("object", "points", title + "：" + "".join(clauses), cid=cid)
        for key, default in (("progress", "representatives"), ("comparison", "points"),
                             ("judgement", "innovation"), ("evaluation", next(iter(evaluation_dimensions(kind))))):
            rows = point.get(key, [])
            dims = dict(next(dims for k, _, dims in definitions if k == key))
            for row in rows:
                if row.get("kind") == "proposal":
                    continue
                ids = [eid for eid in row.get("evidence_ids", []) if eid in allowed]
                # Never silently strip an invalid citation and publish its claim.
                if len(ids) != len(row.get("evidence_ids", [])):
                    continue
                dimension = row.get("dimension") if row.get("dimension") in dims else default
                if not any(b.claim_id == cid and b.kind == "heading" for b in content.get((key, dimension), [])):
                    add(key, dimension, name, cid=cid, block_kind="heading")
                prose = reader_prose(row["text"])
                if prose:
                    add(key, dimension, prose, ids, cid)
                used.update(ids)
        for source in selected_sources(state, claim):
            link = next((link for link in state.claim_evidence_links
                         if link.claim_id == cid and link.evidence_id == source.evidence_id and link.relation == "contradicts"), None)
            if link and not any(source.evidence_id in block.evidence_ids and block.claim_id == cid
                                for (key, _), blocks in content.items() if key == "comparison" for block in blocks):
                # Keep contrary research visible even when the draft omits it;
                # quote readable source text rather than inventing a rebuttal.
                add("comparison", "differences", f"与{name}有关的研究《{source.title}》记载："
                    f"“{source.excerpt if source.anchor_verified else source.abstract or source.excerpt}”",
                    [source.evidence_id], cid)
                used.add(source.evidence_id)
        detail = next((d for d in state.claim_assessments if d.claim_id == cid), None)
        # Field matrices and generic comparison labels are audit diagnostics,
        # not scientific prose. A point without a substantive comparison keeps
        # its sourced material description without a generated gap list.
        for decision in state.novelty_decisions:
            if decision.claim_id == cid:
                ids = [eid for eid in decision.evidence_ids if eid in allowed]
                if not point.get("judgement"):
                    add("judgement", "innovation", reader_prose(decision.rationale), ids, cid)
                used.update(ids)
        for decision in state.advancedness_decisions:
            if decision.claim_id == cid and not point.get("judgement"):
                ids = [eid for eid in decision.comparison_evidence_ids if eid in allowed]
                add("judgement", "advancedness", reader_prose(decision.rationale + "。" + decision.uncertainty), ids, cid)
                used.update(ids)
        for indicator in state.indicators:
            if indicator.source_claim_id != cid and cid not in indicator.source_claim_ids:
                continue
            validation = next((v for v in state.indicator_validations if v.indicator_id == indicator.indicator_id), None)
            target = f"目标值{indicator.value} {indicator.unit}".strip() if indicator.value else "材料提出的指标"
            method = indicator.test_method or indicator.acceptance_condition
            metric_text = f"{name}——{indicator.name}：{target}。"
            if method:
                metric_text += f"材料拟采用的验证方法为{method}。"
            add("comparison", "metrics", metric_text, cid=cid)
            if validation:
                used.update(eid for eid in validation.evidence_refs if eid in allowed)
    if kind == "project" and state.guideline_requirements:
        table("comparison", "prior", ["指南要求", "项目应答", "比较判断"],
              [[r.text, r.response_excerpt, r.comparison_note] for r in state.guideline_requirements])
    # Summaries of the evidence actually cited; no ranking or publication-year
    # count is interpreted as a proof of frontier status.
    for row in state.output_json.get("scholarly_conclusion", []):
        ids = [eid for eid in row.get("evidence_ids", []) if eid in records]
        prose = reader_prose(row["text"])
        if prose:
            add("conclusion", "overall", prose, ids)
        used.update(ids)
    cited = [e for e in state.evidence_records if e.evidence_id in used]
    add("progress", "landscape", f"本报告实际引用{len(cited)}项相关来源。研究格局按以下代表内容解释，"
        "不据本次样本推算整个领域的机构、国家或市场占比。")
    years = {e.year for e in cited if e.source_type == "literature" and e.year}
    if years or any(not e.year for e in cited if e.source_type == "literature"):
        sections["progress"].chart_keys = ["years"]
    if state.novelty_decisions:
        sections["judgement"].chart_keys = ["claim_judgement"]
    landscape_charts = []
    if any(card.status == "available" and card.observation_level == "quantitative"
           and card.source_evidence_ids for card in state.market_data_cards):
        landscape_charts.append("market")
    if any(card.status == "available" and card.observation_level == "quantitative"
           and card.x_value is not None and card.y_value is not None and card.source_evidence_ids
           for card in state.competitive_data_cards):
        landscape_charts.append("competition")
    if any(card.status == "available" and card.period and card.source_evidence_ids
           for card in state.regulatory_data_cards):
        landscape_charts.append("regulatory_timeline")
    sections["evaluation"].chart_keys = landscape_charts
    if not content.get(("conclusion", "overall")):
        add("conclusion", "overall", "综合判断以第六章的分项评价及第七章的分类评价为依据；"
            "可比较的差异与尚待验证的目标分别列示，不以目标值替代已实现结果。")
    add("conclusion", "scope", f"结论适用于{boundary}，证据截止范围{date}。")
    for key, _, dims in definitions:
        if not dims:
            sections[key].blocks = content.get((key, ""), [])
            continue
        chapter = next(i for i, (k, _, _) in enumerate(definitions) if k == key)
        for index, (dim, title) in enumerate(dims, 1):
            blocks = content.get((key, dim), [])
            if not blocks:
                continue
            sections[key].blocks.append(ReportBlock(kind="heading", text=f"{chapter}.{index} {title}"))
            sections[key].blocks.extend(blocks)
    # New research reports freeze this figure before the item-by-item comparison.
    sections["comparison"].chart_keys.insert(0, "claim_evidence_network")
    sections["comparison"].blocks.insert(0, ReportBlock(kind="figure", text="claim_evidence_network"))
    specs = list(sections.values())

    def annex(key, title, headers, rows, ids=()):
        if not rows and not content.get((key, "")):
            return
        table(key, "", headers, rows, ids)
        spec = ReportSectionSpec(section_key=key, title=title, order=len(specs) + 1, is_appendix=True)
        spec.blocks = content.get((key, ""), [])
        specs.append(spec)

    references = ReportSectionSpec(section_key="references", title="参考文献", order=len(specs) + 1, is_appendix=True)
    references.blocks = [ReportBlock(text=e.title, evidence_ids=[e.evidence_id]) for e in cited]
    if not references.blocks:
        references.blocks = [ReportBlock(text="本次未形成外部引用。")]
    specs.append(references)
    retrieval_rows = []
    grouped_queries = {}
    for query in state.query_plans:
        group = query.query_group or "综合检索"
        current = grouped_queries.setdefault(group, {"sources": [], "years": [], "candidates": 0, "selected": 0})
        current["sources"].extend(query.source_scope)
        current["years"].extend(query.year_range)
        current["candidates"] += query.candidate_count
        current["selected"] += query.selected_count
    for group, summary in grouped_queries.items():
        years = sorted({year for year in summary["years"] if year})
        year_label = f"{years[0]}—{years[-1]}" if years else "按任务范围"
        retrieval_rows.append([group, "、".join(dict.fromkeys(summary["sources"])) or "本次可用来源",
                               year_label, f"{summary['selected']} 篇纳入相关分析"])
    annex("retrieval_log", "附录A 检索范围", ["检索主题", "来源", "时间范围", "纳入分析"], retrieval_rows)
    crosswalk = []
    for claim in state.claims:
        ranked_ids = representative_evidence_ids(state, claim, used)
        for eid in ranked_ids:
            e = records[eid]
            chunks = [c for c in state.evidence_chunks if c.evidence_id == eid and claim.claim_id in c.supports_claim_ids]
            location = "；".join(" ".join(filter(None, [c.page_ref, c.section_ref, c.paragraph_ref])) for c in chunks)
            location = location or (" ".join(filter(None, [e.page_ref, e.section_ref])) if e.anchor_verified else "摘要")
            positions = [re.match(r"第.+?章", s.title).group() for s in sections.values()
                if re.match(r"第.+?章", s.title) and any(
                b.claim_id == claim.claim_id and eid in b.evidence_ids for b in s.blocks)]
            crosswalk.append([point_title(claim), e.title,
                              location or ("摘要" if e.abstract else "正文分析引用"),
                              "；".join(positions) or "相关要点评价"])
    annex("evidence_crosswalk", "附录B 评估要点与代表文献", ["评估要点", "代表文献", "可用定位", "正文位置"], crosswalk)
    # Indicator definitions and planned validation stay with their relevant
    # research point in the body; a second full-width audit table adds no value.
    if requests_material_revision(state.raw_user_input):
        annex("revision_advice", "附录E 表述修改建议", ["原表述", "建议表述", "依据"],
              [[c.text, d.rewrite_advice, d.rationale] for c in state.claims for d in state.novelty_decisions
               if d.claim_id == c.claim_id and d.rewrite_advice])
    for section in specs:
        refresh_section(section)
    return specs


def requests_material_revision(text):
    """Only a direct instruction can enable advice; quoted material cannot."""
    return any(not re.search(r"不需要|不要求|无需|不要|不希望|不包含|不提供|不生成", sentence)
               and re.search(r"(?:请|需要|希望|要求|帮我).{0,20}(?:修改建议|表述修改|材料优化|修改材料|优化材料)", sentence)
               for sentence in re.split(r"[。；;\n]", text))
