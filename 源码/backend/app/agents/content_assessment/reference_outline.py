"""Deterministic, reader-facing projection of the reference PDF's 11 chapters.

This layer organizes completed evaluation records; it never runs evaluation or
upgrades the strength of a conclusion. Historical projections only move prose.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .schemas import ContentAssessmentState, ReportBlock, ReportSectionSpec


CHAPTER_TITLES = (
    "第1章 任务概述与评估工作流", "第2章 阶段一：文档上传与解析",
    "第3章 阶段二：要点提取", "第4章 阶段三：内容比对与证据溯源",
    "第5章 阶段四：创新性评估", "第6章 阶段五：技术趋势与竞争态势",
    "第7章 项目与申报指南符合性评估", "第8章 前沿性评估",
    "第9章 创新性与先进性评估", "第10章 可行性与风险评估",
    "第11章 综合评估结论与建议",
)
SUBHEADINGS = {
    1: ["1.1 评估任务", "1.2 评估工作流与执行概况", "1.3 评估原则"],
    2: ["2.1 思考过程", "2.2 解析结果"],
    3: ["3.1 思考过程", "3.2 提取结果：项目创新点与量化指标", "3.3 提取结果：待核查事实性陈述"],
    4: ["4.1 思考过程与检索策略", "4.2 学术证据：分方向代表文献",
        "4.3 网络核查：监管、标准与市场证据", "4.4 申报书事实性陈述核查结论"],
    5: ["5.1 思考过程与判定准则", "5.2 逐点判定", "5.3 判定汇总"],
    6: ["6.1 思考过程", "6.2 技术趋势：文献计量证据", "6.3 市场与监管态势", "6.4 竞争格局定位"],
    9: ["9.1 创新性结论", "9.2 先进性条件判定", "9.3 指标口径问题清单", "9.4 综合评估画像"],
    10: ["10.1 可行性支撑", "10.2 风险评估"],
    11: ["11.1 综合评估结论", "11.2 申报文本修改建议", "11.3 后续工作建议"],
}
TABLE_TITLES = {
    "1-1": "评估执行总体情况", "2-1": "输入文档解析结果",
    "3-1": "项目主张与量化指标锚定", "3-2": "待核查事实性陈述",
    "4-1": "学术检索主题与回收情况", "4-2": "网络公开信息核查结果",
    "4-3": "事实性陈述核查判定", "5-1": "主张新颖性逐点判定",
    "5-2": "判定统计", "6-1": "主要竞争方与项目对标",
    "7-1": "指南要求与项目应答映射", "9-1": "先进性候选指标与验证条件",
    "11-1": "申报文本规范化建议", "A-1": "检索执行记录",
    "B-1": "主张—证据—判定对照总表",
}
FIGURES = {1: ["workflow"], 5: ["claim_judgement"],
           6: ["years", "market", "competition"], 9: ["radar"]}


def normalize_reference_outline(state: ContentAssessmentState, *, historical: bool = False) -> None:
    # V6 is composed before freezing. Never route it through the historical
    # eleven-chapter projection, including during export and repeat freezes.
    if state.output_json.get("research_report") or any(s.section_key == "methods" for s in state.report_plan.section_specs):
        return
    from .report_composition import FIELDS, STATUS, material_sources, readable, reader_text, refresh_section, sentences

    source = {s.section_key: s for s in state.report_plan.section_specs}
    # Exporting an already projected plan must not duplicate or rewrite text.
    if all(f"chapter_{i}" in source for i in range(1, 12)) or (
        not historical and {"executive", "object_points", "research_progress", "comparison_novelty", "trends_advice", "references"} <= source.keys()
    ):
        return

    claim_names = {c.claim_id: f"主张 {i}" for i, c in enumerate(state.claims, 1)}
    material_names = {s.file_id: s.name for s in material_sources(state)}
    material_names.update({d.document_id: d.name for d in state.assessment_profile.document_records})
    ids = {**claim_names, **material_names,
           **{v.indicator_id: v.name for v in state.indicators}}

    def text(value, default="未提供"):
        value = reader_text(readable(value)) or default
        for key in sorted(ids, key=len, reverse=True):
            value = re.sub(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])", lambda _: ids[key], value)
        return value

    def p(value, refs=(), *, kind="paragraph", level=3, style="result"):
        return ReportBlock(kind=kind, text=text(value), evidence_ids=list(dict.fromkeys(refs)),
                           heading_level=level, callout_style=style)

    def h(value):
        return p(value, kind="heading", level=4 if re.match(r"4\.2\.\d+\s", value) else 3)

    def figure(key):
        return ReportBlock(kind="figure", text=key)

    def table(number, headers, rows, refs=()):
        def cell(v):
            return text(v).replace("|", "／").replace("\n", " ")
        body = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        if not rows:
            rows = [["本次材料无此类记录"] + ["—"] * (len(headers) - 1)]
        body.extend("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
        return [p(f"表 {number} {TABLE_TITLES[number]}", kind="caption"),
                p("\n".join(body), refs, kind="table")]

    def section(key, title, blocks, charts=(), appendix=False):
        spec = ReportSectionSpec(section_key=key, title=title, blocks=blocks,
                                 chart_keys=list(charts), is_appendix=appendix, status="generated")
        refresh_section(spec)
        return spec

    def source_blocks(*keys):
        blocks = []
        for key in keys:
            original = source.get(key)
            if original:
                if original.blocks:
                    blocks.extend(b.model_copy(deep=True) for b in original.blocks)
                else:
                    blocks.append(ReportBlock(text=original.content))
        return blocks

    if historical:
        # Every old paragraph and reference remains intact; no live task facts
        # are injected into old conclusions, even if the caller has new data.
        mapping = {
            "executive": "executive", "stage_overview": "chapter_1", "process": "chapter_1",
            "stage_material": "chapter_2", "technical": "chapter_2",
            "stage_claims": "chapter_3", "claims": "chapter_3",
            "stage_retrieval": "chapter_4", "stage_comparison": "chapter_4",
            "comparison": "chapter_5", "stage_assessment": "chapter_5",
            "stage_landscape": "chapter_6", "market": "chapter_6", "landscape": "chapter_6",
            "assessment_conformity": "chapter_7", "assessment_frontier": "chapter_8",
            "assessment_innovation": "chapter_9", "assessment": "chapter_9",
            "assessment_feasibility": "chapter_10", "roadmap": "chapter_11",
            "assessment_conclusion": "chapter_11", "conclusion": "chapter_11",
            "stage_quality": "chapter_11", "stage_release": "chapter_11",
            "references": "references", "retrieval_log": "retrieval_log",
            "evidence_crosswalk": "evidence_crosswalk",
        }
        grouped = defaultdict(list)
        for key, spec in source.items():
            if key.startswith("part_"):
                continue
            originals = [b.model_copy(deep=True) for b in spec.blocks] or [ReportBlock(text=spec.content)]
            # Retain legacy heading wording as prose without adding the old
            # numbering/structure to the new TOC or PDF bookmarks.
            for block in originals:
                if block.kind == "heading":
                    block.kind = "paragraph"
            grouped[mapping.get(key, "evidence_crosswalk")].extend(originals)
        chapters = []
        for i, title in enumerate(CHAPTER_TITLES, 1):
            old = grouped[f"chapter_{i}"]
            headings = [h(v) for v in SUBHEADINGS.get(i, [])]
            if i == 4:
                headings[2:2] = [h(f"4.2.{n} 历史证据方向 {n}") for n in range(1, 9)]
            chapters.append(section(f"chapter_{i}", title, headings + (old or [p("该历史版本未记录此项。")])) )
        abstract = section("executive", "摘要", grouped["executive"] or [p("该历史版本未记录此项。")])
        references = section("references", "参考文献", grouped["references"] or [p("该历史版本未记录此项。")], appendix=True)
        appendix_a = section("retrieval_log", "附录A 检索执行记录", grouped["retrieval_log"] or [p("该历史版本未记录此项。")], appendix=True)
        appendix_b = section("evidence_crosswalk", "附录B 创新点—证据对照总表", grouped["evidence_crosswalk"] or [p("该历史版本未记录此项。")], appendix=True)
    else:
        profile = state.assessment_profile
        title = profile.assessment_object or profile.title or state.report_plan.report_title or "本次研究项目"
        materials = profile.document_records
        evidence = {e.evidence_id: e for e in state.evidence_records}
        decisions = {d.claim_id: d for d in state.novelty_decisions}
        details = {d.claim_id: d for d in state.claim_assessments}
        validations = {v.indicator_id: v for v in state.indicator_validations}
        links = defaultdict(list)
        for link in state.claim_evidence_links:
            links[link.claim_id].append(link)

        def evidence_text(refs):
            return "；".join(f"《{evidence[eid].title}》 {eid}" for eid in dict.fromkeys(refs) if eid in evidence) or "本轮证据仅支持材料层面的判断"

        def claim_refs(claim):
            detail, decision = details.get(claim.claim_id), decisions.get(claim.claim_id)
            refs = ([detail.nearest_prior_art_id] + detail.supporting_evidence_ids + detail.contradicting_evidence_ids if detail else [])
            refs += decision.evidence_ids if decision else []
            refs += [link.evidence_id for link in links[claim.claim_id]]
            return list(dict.fromkeys(eid for eid in refs if eid in evidence))

        def claim_reason(claim):
            decision, detail = decisions.get(claim.claim_id), details.get(claim.claim_id)
            parts = [decision.rationale if decision else detail.conclusion if detail else
                     f"已识别项目拟议方案“{claim.text}”；该项以材料中的研究对象、方法与指标作为判定边界。"]
            if detail:
                parts.extend(detail.field_comparisons)
                if detail.advancedness_conclusion:
                    parts.append(detail.advancedness_conclusion)
            # Counterevidence must survive regrouping, even without a decision.
            for link in links[claim.claim_id]:
                record = evidence.get(link.evidence_id)
                if record and (link.difference or link.similarity) and (link.relation == "contradicts"
                        or (detail and link.evidence_id == detail.nearest_prior_art_id)):
                    parts.append(f"{STATUS.get(link.relation, link.relation)}：《{record.title}》 {link.evidence_id}。{link.difference or link.similarity}")
            if decision:
                parts.extend(f"口径风险：{v}" for v in decision.risk_notes)
            return "；".join(dict.fromkeys(text(v) for v in parts if v))

        labels = [decisions[c.claim_id].label if c.claim_id in decisions else "当前不可判断" for c in state.claims]
        counts = Counter(labels)
        summary = "；".join(f"{label} {count} 项" for label, count in counts.items()) or "未识别可独立判定的技术主张"
        score_summary = "；".join(f"{s.dimension}：{s.assessment}" for s in state.scorecard)
        material_count = len(materials) or len(material_sources(state))
        facts = [c for c in state.claims if c.assertion_type == "completed" or c.claim_type == "compliance" or re.search("首次|唯一|最早|最长|填补|市场规模|标准", c.text)]
        abstract = section("executive", "摘要", [
            p(f"本报告对“{title}”开展内容评估，材料范围为{profile.scope_boundary or '本次提交的研究方案与公开证据'}，共登记 {material_count} 份材料。"),
            p(f"评估遵循“文档上传与解析 → 要点提取 → 内容比对与证据溯源 → 创新性评估 → 技术趋势与竞争态势”五步报告工作流。共执行 {len({q.query_group or q.query for q in state.query_plans})} 个检索主题，回收 {sum(q.candidate_count for q in state.query_plans)} 条候选记录，纳入 {len(evidence)} 条去重证据，提取 {len(state.claims)} 项主张、{len(state.indicators)} 项指标及 {len(facts)} 项事实核查对象。"),
            p(f"主要结论：{summary}。{score_summary or '综合判断围绕主张差异、指标口径、实施条件和公开证据展开。'}"),
            *[p(v.assessment_conclusion, v.evidence_refs) for v in state.indicator_validations if v.assessment_conclusion],
            p("说明：结论限于本次材料、检索范围和证据截止日期。公开文献用于说明研究基础与技术比较；拟议目标与项目实测结果分别列示，不将检索边界等同于技术不可行。"),
        ])
        chapters = []
        def chapter(i, blocks):
            spec = section(f"chapter_{i}", CHAPTER_TITLES[i-1], blocks, FIGURES.get(i, []))
            chapters.append(spec)
            return spec

        chapter(1, [h(SUBHEADINGS[1][0]), p(f"评估对象：{title}。评估目的：{profile.assessment_goal}。材料范围：{profile.scope_boundary or '本次提交材料及可核验公开信息'}。"),
            h(SUBHEADINGS[1][1]), figure("workflow"),
            *table("1-1", ["维度", "执行内容", "数量/结果"], [
                ["文档解析", "材料角色、内容和定位核查", f"{material_count} 份"],
                ["要点提取", "主张、指标和事实性陈述", f"{len(state.claims)} 项主张、{len(state.indicators)} 项指标"],
                ["学术检索", "分主题检索、去重和证据溯源", f"{len(state.query_plans)} 组检索、{len(evidence)} 条证据"],
                ["内容比对", "对象、方法、基线及条件对照", f"{len(state.claim_evidence_links)} 条证据关系"],
                ["创新评估", "逐点判定及综合评估", summary]]),
            h(SUBHEADINGS[1][2]), p('遵循“同对象、同口径、同终点”的可比原则。项目计划指标与已实现指标严格区分，摘要级证据用于方向判断，定量领先结论须有可定位、同口径数据。')])
        rows = [[d.name, "；".join(filter(None, [d.excerpt, f"{d.page_count} 页" if d.page_count else ""])),
                 {"PROJECT": "项目材料", "GUIDE": "申报指南", "SUPPLEMENT": "补充材料", "OTHER": "参考材料"}.get(d.role, d.role) + "；" + {"parsed": "已解析", "failed": "解析失败", "pending": "待解析"}.get(d.status, d.status)] for d in materials]
        if not rows:
            rows = [[s.name, sentences(s.content, 220), "用户输入材料"] for s in material_sources(state)]
        chapter(2, [h(SUBHEADINGS[2][0]), p("【思考】按材料角色建立边界：研究方案是评估主体，指南是对照基准，参考材料用于交叉核查。先检查文档可读性和定位，再识别创新主张、量化指标及可核查的事实性陈述。", kind="callout", style="execution"),
            h(SUBHEADINGS[2][1]), *table("2-1", ["文档", "内容摘要", "解析与角色"], rows),
            p(f"材料中的九项字段已识别 {sum(bool(v) for v in state.nine_fields.model_dump().values())}/9 项；原文位置与主张锚定一并保存在本报告快照。")])
        claims_rows = []
        for c in state.claims:
            metrics = [f"{v.name}：{v.value or v.acceptance_condition or '材料未列数值目标'}；单位：{v.unit or '按原定义'}" for v in state.indicators if c.claim_id in (v.source_claim_ids or [v.source_claim_id])]
            locator = "；".join(filter(None, [material_names.get(c.source_document_id), c.source_page_ref, c.source_paragraph_ref]))
            if c.source_char_start is not None:
                locator += f"；原文字符 {c.source_char_start}–{c.source_char_end}"
            claims_rows.append([claim_names[c.claim_id], c.text, "；".join(metrics) or "本主张未单列量化指标", locator or c.source_span or "定位信息未随材料提供"])
        chapter(3, [h(SUBHEADINGS[3][0]), p("【思考】以可独立检索、可独立验证的技术主张为评估单元。分别记录创新点、定量指标和指南要求，不把材料中的设计目标提前视为已实现结论。", kind="callout", style="execution"),
            h(SUBHEADINGS[3][1]), *table("3-1", ["编号", "主张/创新点", "关键量化指标", "原文锚定"], claims_rows),
            h(SUBHEADINGS[3][2]), *table("3-2", ["编号", "待核查表述", "核查对象"], [[claim_names[c.claim_id], c.text, "公开先例、标准要求或已实现结果"] for c in facts])])

        relevant_ids = {eid for c in state.claims for eid in claim_refs(c)}
        relevant_ids.update(eid for cards in [state.market_data_cards, state.regulatory_data_cards, state.competitive_data_cards] for card in cards for eid in card.source_evidence_ids if eid in evidence)
        directions = defaultdict(list)
        for record in state.evidence_records:
            if record.evidence_id not in relevant_ids or record.source_type != "literature":
                continue
            direction = record.query_group or (record.keywords[0] if record.keywords else "")
            associated = [c for c in state.claims if record.evidence_id in claim_refs(c)]
            direction = direction or (associated[0].searchable_terms[0] if associated and associated[0].searchable_terms else profile.research_domain)
            directions[direction].append(record)
        direction_names = list(directions)[:8]
        # More than eight actual themes are regrouped, never discarded.
        for extra in list(directions)[8:]:
            directions[direction_names[-1]].extend(directions[extra])
        facets = ["研究对象与系统路线", "关键方法与技术原理", "材料与结构设计", "测量指标与评价方法", "实验设计与可重复性", "应用场景与工程条件", "标准与规范接口", "跨方向综合证据"]
        b = [h(SUBHEADINGS[4][0]), p("【思考】按对象、方法、指标、先例及应用边界组织检索；对结果去重、区分题录/摘要/全文等级，并以对应主张和真实引用关系解释文献适用范围。", kind="callout", style="execution"),
             *table("4-1", ["检索主题", "检索式", "候选/纳入", "执行情况"], [[q.query_group or "综合检索", q.query, f"{q.candidate_count}/{q.selected_count}", {"completed": "已完成", "pending": "未完成", "unavailable": "检索源不可用"}.get(q.source_status, q.source_status)] for q in state.query_plans]), h(SUBHEADINGS[4][1])]
        for n, direction in enumerate(direction_names, 1):
            b.append(h(f"4.2.{n} {direction}"))
            records = directions.get(direction, [])
            if not records:
                b.append(p(f"本节纳入{direction}的评估框架；本轮证据按实际研究主题归入上述代表文献小节，不据此扩展该方向的独立结论。"))
            # Representative prose is concise; the full relation set remains
            # in Appendix B and all cited sources remain in the bibliography.
            representatives = sorted(records, key=lambda r: (r.evidence_level, -(r.year or 0)))[:2]
            for record in representatives:
                b.append(p(f"代表文献：《{record.title}》（{text(record.authors, '作者未列')}，{record.year or '年份未列'}）。研究进展：{sentences(record.abstract or record.excerpt, 240) or '该记录为题录线索，仅用于识别公开研究方向。'}", [record.evidence_id]))
                related = [(c, l) for c in state.claims for l in links[c.claim_id] if l.evidence_id == record.evidence_id]
                if related:
                    b.append(p("与项目的关系及证据结论：" + "；".join(f"{claim_names[c.claim_id]}（{sentences(c.text, 100)}）：{STATUS.get(l.relation, l.relation)}。{l.difference or l.similarity or '适用范围以文献实际研究对象与方法为准'}" for c, l in related), [record.evidence_id]))
        web_cards = state.regulatory_data_cards + state.market_data_cards
        b.extend([h(SUBHEADINGS[4][2]), *table("4-2", ["核查项目", "公开信息", "来源", "适用范围"], [[card.subject, f"{card.period} {card.value} {card.unit}", evidence_text(card.source_evidence_ids) if card.source_evidence_ids else card.source_url, card.limitation or "按原资料对象和统计口径使用"] for card in web_cards], [eid for card in web_cards for eid in card.source_evidence_ids if eid in evidence]),
                  h(SUBHEADINGS[4][3]), *table("4-3", ["事实性陈述", "核查结论", "判定依据"], [[c.text, decisions[c.claim_id].label if c.claim_id in decisions else "未形成独立事实判定", claim_reason(c)] for c in facts], [eid for c in facts for eid in claim_refs(c)])])
        chapter(4, b)

        decision_rows = [[claim_names[c.claim_id], sentences(c.text, 200), evidence_text(claim_refs(c)[:2]),
                          decisions[c.claim_id].label if c.claim_id in decisions else "当前不可判断", claim_reason(c)] for c in state.claims]
        chapter(5, [h(SUBHEADINGS[5][0]), p("【思考】执行“主张—证据—判定”三段式评价，分别判断概念先例、技术组合差异和同口径指标。结论直接沿用本次主张判定，不因报告排版调整而提高评价等级。", kind="callout", style="execution"),
            h(SUBHEADINGS[5][1]), *table("5-1", ["编号", "主张/创新点", "在先证据（最相近报道）", "判定", "判定理由与口径风险"], decision_rows, [eid for c in state.claims for eid in claim_refs(c)]),
            h(SUBHEADINGS[5][2]), figure("claim_judgement"), *table("5-2", ["判定类别", "数量", "对应主张"], [[label, count, "、".join(claim_names[c.claim_id] for c, own_label in zip(state.claims, labels) if own_label == label)] for label, count in counts.items()])])

        trend = [p(state.trend_analysis.get("summary"))] if state.trend_analysis.get("summary") else []
        trend += [p(f"主题“{theme.get('label')}”：{theme.get('summary') or theme.get('description')}", [eid for eid in theme.get("evidence_ids", []) if eid in evidence]) for theme in state.trend_analysis.get("themes", []) if isinstance(theme, dict) and (theme.get("summary") or theme.get("description"))]
        markets = [p(f"{c.subject}：{c.period} {c.value} {c.unit}。{c.limitation}", [eid for eid in c.source_evidence_ids if eid in evidence]) for c in web_cards]
        chapter(6, [h(SUBHEADINGS[6][0]), p("【思考】以纳入文献的年份和研究主题归纳技术趋势，分别核查市场规模、监管标准与竞争对象。定量图只比较同口径数据；其余图明确展示本次证据覆盖，证据数量不代表市场规模或技术排名。", kind="callout", style="execution"),
            h(SUBHEADINGS[6][1]), *trend, figure("years"), h(SUBHEADINGS[6][2]), *markets, figure("market"),
            h(SUBHEADINGS[6][3]), *table("6-1", ["竞争方/研究对象", "公开水平", "项目对标口径", "证据"], [[c.subject, c.value or f"{c.x_label} {c.x_value} {c.x_unit}；{c.y_label} {c.y_value} {c.y_unit}", c.limitation or f"{c.x_label}/{c.y_label}", evidence_text(c.source_evidence_ids)] for c in state.competitive_data_cards], [eid for c in state.competitive_data_cards for eid in c.source_evidence_ids if eid in evidence]), figure("competition")])
        guideline_rows = []
        for req in state.guideline_requirements:
            matches = [c.text for c in state.claims if c.claim_type == "compliance" and (req.text in c.text or c.text in req.text)]
            guideline_rows.append([req.text, req.response_excerpt or "；".join(matches) or "尚未找到可定位的项目应答", {"matched": "材料文字覆盖", "partial": "部分应答，仍需核验", "unmatched": "不符合", "unknown": "未形成符合性判定"}[req.match_status], req.comparison_note or req.source_span or "以指南原文要求和项目材料应答为判定依据"])
        if not guideline_rows:
            guideline_rows = [["本次未提交独立申报指南", text(state.nine_fields.research_content, title), "不作指定指南符合性结论", "本章评价范围限定为提交的研究内容"]]
        chapter(7, [p("项目与申报指南采用要求—应答—判断映射；指南硬性要求与拟议目标分别列示，不将方案完整性代替指南符合性。"), *table("7-1", ["指南要求", "项目应答", "符合性", "说明"], guideline_rows)])
        chapter(8, [p("前沿性分别从文献计量、代表研究共识和监管标准信号评价。"), *trend,
            p(f"文献计量：本次纳入 {len(evidence)} 条证据，涉及 {len({e.year for e in evidence.values() if e.year})} 个发表年份；统计限定于本次检索记录，并非全领域普查。"),
            p(f"代表研究共识：{summary}。该分布反映项目主张与公开研究的关系，不等同于全部主张均已完成项目实验。"),
            *[p(f"监管与标准：{c.subject}，{c.value}。{c.limitation}", c.source_evidence_ids) for c in state.regulatory_data_cards]])

        advanced_rows = []
        issue_rows = []
        for item in state.indicators:
            v = validations.get(item.indicator_id)
            literature_baseline = v.literature_baseline if v else ""
            project_baseline = v.baseline if v and v.baseline else item.baseline
            baseline = ("公开文献：" + literature_baseline if literature_baseline else
                        "项目材料对照：" + sentences(project_baseline, 100) if project_baseline else
                        "本次证据不用于同口径数值比较")
            conditions = "；".join(filter(None, [v.test_method, v.sample_design, v.statistics_method, v.acceptance_criteria])) if v else "；".join(filter(None, [item.test_method, item.statistics_method, item.acceptance_condition]))
            target = v.target if v and v.target else item.value
            conclusion = v.assessment_conclusion if v and v.assessment_conclusion else f"本次已识别{item.name}的{('数值目标' if target else '评价定义')}；项目达成情况仅以材料中的实测记录判断。"
            # The target cell is a target, not another copy of the entire
            # measurement definition. Preserve project/documentary baselines
            # separately so they cannot be mistaken for public achievements.
            target_text = f"{target} {item.unit}" if target else f"{item.name}（{item.unit or '按指标定义'}）；方案未单列数值目标"
            advanced_rows.append([item.name, baseline, target_text, conditions or "适用条件按研究方案与指标定义", conclusion])
            if v and v.gaps:
                issue_rows.append([item.name, "；".join(v.gaps)])
            elif not item.baseline or not item.test_method:
                issue_rows.append([item.name, "；".join(value for value, present in [("同口径基线", item.baseline), ("测试方法", item.test_method)] if not present)])
        overall = next((s for s in state.scorecard if s.dimension == "创新性"), None)
        chapter(9, [h(SUBHEADINGS[9][0]), p(f"依据第5章逐点判定，本项目创新性分布为：{summary}。"),
            *([p(overall.assessment, [eid for eid in overall.basis if eid in evidence])] if overall else []),
            p("创新性结论以在先研究、技术组合差异和适用条件为边界。逐点依据见表5-1；指标目标的先进性与项目实测达成情况按下节分别判断。"),
            h(SUBHEADINGS[9][1]), *table("9-1", ["候选指标", "公开水平/基线", "项目目标", "验证条件", "本次评估结论"], advanced_rows, [eid for v in state.indicator_validations for eid in v.evidence_refs if eid in evidence]),
            *[p(v.assessment_conclusion, v.evidence_refs) for v in state.indicator_validations if v.assessment_conclusion],
            h(SUBHEADINGS[9][2]), *[p(f"{name}：{issue}。") for name, issue in issue_rows],
            *([] if issue_rows else [p("本次未记录额外影响定量比较的口径问题。")]),
            h(SUBHEADINGS[9][3]), *[p(f"{s.dimension}：{s.assessment}。评分范围：{s.score_range or '未给出数值评分'}。{s.uncertainty}", [eid for eid in s.basis if eid in evidence]) for s in state.scorecard], figure("radar")])
        support = [p(f"技术路线：{text(state.nine_fields.technical_route, '以本次方案中的方法、实施条件和验收方式为准')}。"),
                   p(f"研究安排：{text(state.nine_fields.timeline, '本次材料未单列时间安排')}。预期交付：{text(state.nine_fields.expected_outcomes, '以提交方案为准')}。")]
        support += [p(f"{s.dimension}：{s.assessment}。{s.uncertainty}", [eid for eid in s.basis if eid in evidence]) for s in state.scorecard if s.dimension in {"可行性", "应用与合规"}]
        risks = list(dict.fromkeys([r for d in state.novelty_decisions for r in d.risk_notes] + [s.uncertainty for s in state.scorecard if s.uncertainty]))
        chapter(10, [h(SUBHEADINGS[10][0]), *support, h(SUBHEADINGS[10][1]), *[p(r) for r in risks],
                     *([] if risks else [p("本次未记录独立风险条目；适用条件仍按第5章证据比对和第9章指标口径执行。")])])
        rewrites = [[c.text, d.rewrite_advice] for c in state.claims if (d := decisions.get(c.claim_id)) and d.rewrite_advice]
        next_steps = list(dict.fromkeys([f"{v.definition or next((i.name for i in state.indicators if i.indicator_id == v.indicator_id), '指标')}：{gap}" for v in state.indicator_validations for gap in v.gaps] +
                                      [f"{claim_names[d.claim_id]}：{gap}" for d in state.claim_assessments if d.claim_id in claim_names for gap in d.evidence_gaps]))
        # Preserve the actual overall findings without reintroducing old claim
        # cards, internal identifiers or per-claim rewrite/verification lists.
        conclusion_blocks = source_blocks("conclusion")
        conclusion_blocks = [p(b.text, b.evidence_ids) for b in conclusion_blocks if b.kind in {"paragraph", "callout"}]
        chapter(11, [h(SUBHEADINGS[11][0]), *(conclusion_blocks or [p(f"对“{title}”的综合判断：{summary}。{score_summary}")]),
            h(SUBHEADINGS[11][1]), *table("11-1", ["原表述", "规范化建议"], rewrites or [["现有技术表述", "本次未记录需要单列的改写条目；保持事实、目标和证据边界一致"]]),
            h(SUBHEADINGS[11][2]), *[p(v) for v in next_steps],
            *([] if next_steps else [p("本次未记录单独的后续工作清单，按原研究方案及指标验收条件推进。")])])

        references = section("references", "参考文献", source_blocks("references"), appendix=True)
        if not references.blocks:
            references.blocks = [p(f"{record.evidence_id} {text(record.authors, '作者未列')}. {record.title}. {record.year or '年份未列'}. DOI：{record.doi or '未提供'}。", [record.evidence_id])
                                 for record in state.evidence_records if record.evidence_id in relevant_ids]
            refresh_section(references)
        appendix_a = section("retrieval_log", "附录A 检索执行记录", table("A-1", ["编号", "主题", "检索式", "执行状态", "候选/纳入"], [[f"检索 {i}", q.query_group or "综合主题", q.query, {"completed": "已完成", "pending": "未完成", "unavailable": "检索源不可用"}[q.source_status], f"{q.candidate_count}/{q.selected_count}"] for i, q in enumerate(state.query_plans, 1)]), appendix=True)
        # Keep the already validated, claim-scoped quotations when replacing
        # the old comparison chapter with the reader-facing outline.
        excerpts = [block for block in source_blocks("comparison") if "原文摘录" in block.text]
        appendix_b = section("evidence_crosswalk", "附录B 创新点—证据对照总表", table("B-1", ["主张/创新点", "证据", "关系", "判定依据"], [[claim_names.get(l.claim_id, "材料主张"), evidence_text([l.evidence_id]), STATUS.get(l.relation, l.relation), l.difference or l.similarity or "按实际证据对象与方法限定"] for l in state.claim_evidence_links], [l.evidence_id for l in state.claim_evidence_links if l.evidence_id in evidence]) + excerpts, appendix=True)

    if historical:
        ordered = [abstract, section("part_process", "第一部分 评估过程实录", []), *chapters[:6],
                   section("part_assessment", "第二部分 综合评估报告", []), *chapters[6:], references, appendix_a, appendix_b]
    else:
        # Reader chapters use the already evaluated facts and evidence links.
        # Process headings, execution callouts and logs remain in audit_data.
        def results(*numbers):
            blocks = []
            for number in numbers:
                for block in chapters[number - 1].blocks:
                    if block.kind == "heading" or (block.kind == "callout" and block.callout_style == "execution"):
                        continue
                    if block.kind == "caption" and re.search(r"表 (?:1-1|2-1|4-1)\b", block.text):
                        continue
                    if block.kind == "table" and any(value in block.text for value in ("执行内容", "解析与角色", "候选/纳入")):
                        continue
                    blocks.append(block.model_copy(deep=True))
            return blocks

        artifact = state.report_preferences.resolved_artifact_type
        object_label = {"paper": "论文", "guideline": "指南", "project": "项目"}.get(artifact, "研究材料")
        result_summary = [p(f"评估对象：{title}。登记 {material_count} 份材料，识别 {len(state.claims)} 项要点和 {len(state.indicators)} 项指标。"),
                          *[block.model_copy(deep=True) for block in abstract.blocks
                           if "主要结论：" in block.text or "说明：" in block.text]
                          ]
        # Organize real literature by assessed point rather than padding eight
        # generic directions. The complete comparison relation set is retained.
        progress = []
        for index, claim in enumerate(state.claims, 1):
            progress.append(h(f"要点 {index}：{sentences(claim.text, 100)}"))
            records = [evidence[eid] for eid in claim_refs(claim) if evidence[eid].source_type == "literature"]
            if not records:
                progress.append(p("当前未取得可对应此要点的公开研究证据，暂不作研究进展结论。"))
            for record in records:
                progress.append(p(f"《{record.title}》（{record.year or '年份未提供'}）：{sentences(record.abstract or record.excerpt, 240) or '当前只有题录信息，无法核查方法和结果。'}", [record.evidence_id]))
                related = [link for link in links[claim.claim_id] if link.evidence_id == record.evidence_id]
                for link in related:
                    progress.append(p(f"与本要点的关系：{STATUS.get(link.relation, link.relation)}。{link.difference or link.similarity or '适用范围须按文献对象和方法核查'}", [record.evidence_id]))
        if not progress:
            progress = [p("当前尚未识别可逐项检索的要点，研究进展结论待补充材料后形成。")]
        type_notes = {
            "paper": "论文评价关注研究问题、方法与实验结果、统计口径、可重复性和引用依据；设计目标与实测结果分别核查。",
            "project": "项目评价关注技术路线、目标、交付物、实施条件和验收指标；计划目标与已实现结果分别核查。",
            "guideline": "指南评价关注研究目标、任务范围、可比指标、应用场景和验收要求；不将指南要求视为已取得的研究成果。",
        }
        # Build type-specific results directly from evaluated points. The legacy
        # process/market chapters above remain available only to historical reports.
        object_blocks = [p(f"评估对象：{title}。材料范围：{state.assessment_profile.scope_boundary or '本次提交材料'}。"),
                         p(type_notes.get(artifact, "评价限定于提交材料和可核验依据。"))]
        from .input_rules import FIELD_LABELS
        observations = state.output_json.get('material_observations', [])
        from .material_extraction import source_location_label
        if observations:
            object_blocks.append(h('材料中的目标与观测记录'))
            for observation in observations:
                label = {'mixed':'目标与记录同段，分别核验','target':'计划目标（尚非实测）','reported':'材料报告记录（未独立核验）','unknown':'数值性质待核验'}.get(observation['kind'], '待核验')
                object_blocks.append(p(f"{label}：{observation['text']}。材料定位：{material_names.get(observation['source_document_id'], '用户输入')}；{source_location_label(observation['source_locator'])}。{observation['comparison_note']}"))
        field_labels = {value: key for key, value in FIELD_LABELS.items()}
        applicable = {"paper": ["research_direction", "research_content", "key_technologies", "technical_route"],
                      "project": ["research_direction", "research_goal", "research_content", "key_technologies", "technical_route", "evaluation_indicators", "timeline", "expected_outcomes"],
                      "guideline": ["research_goal", "research_content", "evaluation_indicators", "application_scenarios"]}.get(artifact, ["research_direction", "research_content"])
        for key in applicable:
            value = getattr(state.nine_fields, key)
            if value:
                object_blocks.append(p(f"{field_labels.get(key, key)}：{text(value)}。"))
        compare_blocks = [block for block in results(5, 9) if block.kind in {"table", "caption"}]
        validation_by_id = {v.indicator_id: v for v in state.indicator_validations}
        status_names = {"target": "计划目标（尚非实测）", "planned": "计划内容", "completed": "材料报告结果（需核验）", "unknown": "性质尚未确认"}
        for index, claim in enumerate(state.claims, 1):
            cid = claim.claim_id
            decision, detail = decisions.get(cid), details.get(cid)
            refs = claim_refs(claim)
            indicators = [i for i in state.indicators if cid == i.source_claim_id or cid in i.source_claim_ids]
            label = f"要点 {index}：{sentences(claim.text, 120)}"
            object_blocks.extend([h(label), p(f"材料定位：{material_names.get(claim.source_document_id, '粘贴正文')}；{source_location_label(claim.source_locator or claim.source_paragraph_ref)}。陈述性质：{status_names.get(claim.assertion_type, '待核验')}。")])
            compare_blocks.extend([h(label), p(f"判定：{decision.label if decision else '当前不可判断'}。{decision.rationale if decision else (detail.conclusion if detail else '缺少足够的对应证据。')}", refs)])
            if detail:
                compare_blocks.append(p("关键差异：" + "；".join(detail.field_comparisons or detail.incomparability_reasons or [detail.uncertainty or "需核查对象、基线和验证条件。"]), refs))
            for link in links[cid]:
                if link.difference or link.similarity:
                    compare_blocks.append(p(f"{STATUS.get(link.relation, link.relation)}：{link.difference or link.similarity}", [link.evidence_id]))
            for record in [evidence[eid] for eid in refs if evidence[eid].source_type == "literature"][:2]:
                compare_blocks.append(p(f"主要依据：《{record.title}》（{record.year or '年份未提供'}）。{sentences(record.abstract or record.excerpt, 220) or '仅题录，不能据此核查方法或结果。'}", [record.evidence_id]))
            for item in indicators:
                validation = validation_by_id.get(item.indicator_id)
                observation = f"可比较指标：{item.name}；{status_names.get(item.status, '待核验')}：{item.acceptance_condition or item.value} {item.unit}；基线：{item.baseline or '未提供'}；测试条件：{item.test_method or '未提供'}。"
                object_blocks.append(p(observation))
                compare_blocks.append(p(observation + (f"口径核查：{validation.assessment_conclusion or '；'.join(validation.gaps)}。" if validation else "对象、单位、基线和验证条件不一致时不直接比较。"), validation.evidence_refs if validation else []))
            if decision and decision.risk_notes:
                compare_blocks.append(p("限制：" + "；".join(decision.risk_notes), refs))
        compare_blocks += [block.model_copy(deep=True) for block in excerpts]
        if artifact == "project" and any(r.source_document_id and r.source_span for r in state.guideline_requirements):
            compare_blocks += [h("项目与配套指南符合性"), *results(7)]
        if artifact == "paper":
            advice = [h("方法、实验与可重复性"), p(f"研究方法：{text(state.nine_fields.technical_route or state.nine_fields.key_technologies)}。")]
            advice += [p(f"要点 {index}：{d.uncertainty or '需提供样本设计、对照、统计方案和独立重复记录。'}", d.supporting_evidence_ids) for index, c in enumerate(state.claims, 1) if (d := details.get(c.claim_id))]
        elif artifact == "guideline":
            advice = [h("任务设置、指标口径与应用场景"), p(f"任务目标：{text(state.nine_fields.research_goal)}。应用场景：{text(state.nine_fields.application_scenarios)}。"),
                      p("指南要求作为拟开展任务和验收条件核查，不解释为已经取得的成果。检查指标是否具有明确对象、单位、基线、验证条件及适用范围。")]
            advice += [p(f"{i.name}：验收条件 {i.acceptance_condition or i.value or '需补充'}；测试方法 {i.test_method or '需补充'}。") for i in state.indicators]
        else:
            advice = [h("路线、交付与实施可行性"), p(f"技术路线：{text(state.nine_fields.technical_route or state.nine_fields.key_technologies)}。"),
                      p(f"阶段安排：{text(state.nine_fields.timeline)}。预期交付：{text(state.nine_fields.expected_outcomes)}。计划目标须以独立验收数据核实。")]
            advice += [p(f"{item.dimension}：{item.assessment}。{item.uncertainty}", [eid for eid in item.basis if eid in evidence]) for item in state.scorecard if item.dimension in {"可行性", "应用与合规"}]
        advice += [h("研究趋势与下一步"), *results(8), figure("years")]
        advice += [p(v) for v in next_steps]
        advice += [p(f"要点 {index}的表达与验证建议：{d.rewrite_advice}") for index, c in enumerate(state.claims, 1) if (d := decisions.get(c.claim_id)) and d.rewrite_advice]
        ordered = [section("executive", "评估摘要与总体结论", result_summary),
                   section("object_points", f"评估对象和要点（{object_label}）", object_blocks),
                   section("research_progress", "相关研究进展", progress),
                   section("comparison_novelty", "逐项比对与创新性分析", compare_blocks),
                   section("trends_advice", "趋势和综合建议", advice, ["years"]), references]
        # The old section text can contain internal labels. Public headings
        # consistently call each assessed statement a 要点.
        seen_paragraphs = set()
        table_numbers = {"3-1": "2-1", "3-2": "2-2", "5-1": "4-1", "5-2": "4-2", "9-1": "4-3", "7-1": "4-4", "6-1": "5-1", "11-1": "5-2"}
        for spec in ordered:
            if spec.section_key == "references":
                continue
            unique_blocks = []
            for block in spec.blocks:
                block.text = re.sub(r"技术主张|项目主张|材料主张|创新主张|主张", "要点", block.text)
                block.text = re.sub(r"表\s*(\d+-\d+)", lambda m: "表 " + table_numbers.get(m[1], m[1]), block.text)
                block.text = block.text.replace("依据第5章逐点判定", "依据逐项比对与创新性分析").replace("第5章证据比对和第9章指标口径", "逐项比对中的证据和指标口径").replace("按下节分别判断", "分别核查")
                if artifact in {"paper", "guideline"}:
                    block.text = block.text.replace("本项目", f"本{object_label}").replace("项目", object_label).replace("申报文本", "材料表述").replace("申报书", "材料")
                if block.kind == "paragraph" and block.text in seen_paragraphs:
                    continue
                if block.kind == "paragraph":
                    seen_paragraphs.add(block.text)
                unique_blocks.append(block)
            spec.blocks = unique_blocks
            refresh_section(spec)
    for order, spec in enumerate(ordered, 1):
        spec.order = order
        spec.level = 1 if not historical or spec.section_key.startswith("part_") or spec.is_appendix or spec.section_key == "executive" else 2
    state.report_plan.section_specs = ordered
