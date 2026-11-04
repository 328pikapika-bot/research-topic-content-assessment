"""Prose-first report composition and versioned evidence audit snapshots."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from app.core.config import BEIJING_TZ
from .material_extraction import material_sources, relevant, rows, locate
from .schemas import ContentAssessmentState, ReportBlock, ReportSectionSpec, ReportSnapshot
from app.common.retrieval.links import http_url

FIELDS = {"object": "对象", "method": "方法", "data": "数据", "baseline": "基线",
          "metric": "指标", "time": "时间", "validation": "验证条件"}
STATUS = {"missing": "未报告该项", "partial": "部分可比", "covered": "一致", "conflict": "存在差异",
          "not_comparable": "不可直接比较", "insufficient_evidence": "证据不足",
          "supports": "有限支持", "partially_supports": "部分支持", "contradicts": "存在反证",
          "有直接支持": "有直接支持", "存在相近先例": "存在相近先例", "方向性支持": "方向性支持",
          "暂未发现可比证据": "暂未发现可比证据", "当前不可判断": "当前不可判断",
          "completed": "材料记录为已完成", "target": "待验证目标", "planned": "计划开展", "unknown": "状态待确认"}
CHAPTERS = [
    ("executive", "摘要与综合结论", 1600), ("process", "任务概述与评估边界", 1600),
    ("technical", "材料解析与结构化字段", 2000), ("landscape", "技术态势分析", 3400),
    ("claims", "主张提取与指标", 2000), ("comparison", "主张比对与证据关系", 11000),
    ("assessment", "创新性与先进性评估", 3200),
    ("market", "证据检索与来源分析", 1800), ("roadmap", "质量检查与验证建议", 1800),
    ("conclusion", "报告发布与综合建议", 900), ("references", "参考文献", 1800),
    ("declarations", "附录：检索记录与证据对照", 900),
]


def claim_groups(state: ContentAssessmentState) -> list[tuple[object, list[object]]]:
    """Group dense material claims for reader-facing sections.

    State keeps every source claim for auditability. The report groups only
    when there are more than five claims and uses conservative domain buckets,
    so a single-sentence task still renders as one claim.
    """
    claims = list(state.claims)
    if len(claims) <= 5:
        for claim in claims:
            claim.aggregation_group = claim.claim_id
            claim.aggregation_reason = "单句或低复杂度输入，保留为独立评估主张。"
            claim.is_group_representative = True
        return [(claim, [claim]) for claim in claims]
    patterns = [
        # Avoid generic words such as “稳定性” and “重复性”: they occur in
        # nearly every evaluation goal and would collapse unrelated subjects
        # into one reader-facing comparison.
        ("重复性与质量控制", r"批间|变异|标准化|一致性|CV|质控"),
        ("应用与预测验证", r"预测|临床|药敏|吻合|应用|筛选|分类|响应"),
        ("性能、通量与兼容性", r"通量|并行|梯度|兼容|读板|信噪|效率|速度|周期|时间"),
        ("技术路线与系统组合", r"芯片|器官|微流控|共培养|浸润|结构|平台|集成|机制"),
    ]
    grouped: dict[str, list[object]] = {}
    order: list[str] = []
    for claim in claims:
        label = next((name for name, pattern in patterns if re.search(pattern, claim.text, re.I)), "其他评估主张")
        # An unmatched claim has no evidence that it belongs to another
        # claim's domain. Keep it independent; putting every fallback item in
        # one bucket is exactly what makes unrelated analyses look identical.
        if label == "其他评估主张":
            label = f"其他评估主张·{claim.claim_id}"
        if label not in grouped:
            grouped[label] = []
            order.append(label)
        grouped[label].append(claim)
    result: list[tuple[object, list[object]]] = []
    for label in order:
        members = grouped[label]
        representative = members[0]
        reason = f"按技术价值将同一{label}路线下的原子要点合并，指标作为该主张组的验证项。"
        representative.aggregation_group = label
        representative.aggregation_reason = reason
        representative.is_group_representative = True
        for member in members[1:]:
            member.parent_claim_id = representative.claim_id
            member.aggregation_group = label
            member.aggregation_reason = reason
            member.is_group_representative = False
        result.append((representative, members))
    return result


def readable(value) -> str:
    if isinstance(value, list):
        return "；".join(readable(v) for v in value if v)
    if isinstance(value, dict):
        return "；".join(f"{FIELDS.get(k, k)}：{readable(v)}" for k, v in value.items() if v)
    text = str(value or "").strip()
    return STATUS.get(text, text)


def sentences(text: str, budget: int) -> str:
    """Select complete sentences, never substring-truncate a qualification."""
    units = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Z])|\n+", readable(text))
    result: list[str] = []
    for unit in units:
        if not unit or unit in result:
            continue
        if result and sum(map(len, result)) + len(unit) > budget:
            break
        result.append(unit)
    return "".join(result)


def build_specs(state: ContentAssessmentState) -> list[ReportSectionSpec]:
    deep = state.report_preferences.resolved_mode == "deep"
    specs = []
    chart_map = {
        "executive": ["workflow"],
        "landscape": ["years", "themes"],
        "claims": ["claim_coverage", "indicator_status"],
        "assessment": ["radar"],
        "market": ["market"],
    }
    chart_metadata = {
        "workflow": {"chart_type": "workflow", "chart_caption": "创新性评估工作流与执行概况", "chart_source_note": "本次任务阶段记录与检索轨迹。", "chart_data_basis": "结构化阶段计数"},
        "years": {"chart_type": "point_line", "chart_caption": "证据发表年份趋势", "chart_source_note": "本次去重纳入的证据池。", "chart_data_basis": "证据年份字段"},
        "themes": {"chart_type": "bubble_positioning", "chart_caption": "技术路线主题定位", "chart_source_note": "本次证据归纳的主题与查询组统计。", "chart_data_basis": "主题成熟度、机会度与证据量"},
        "claim_coverage": {"chart_type": "evidence_matrix", "chart_caption": "主张与证据等级覆盖", "chart_source_note": "主张-证据关联及 A/B/C 证据分层。", "chart_data_basis": "主张关联证据的等级与反证关系"},
        "indicator_status": {"chart_type": "donut", "chart_caption": "指标验证状态", "chart_source_note": "指标验证记录。", "chart_data_basis": "指标状态计数"},
        "radar": {"chart_type": "radar", "chart_caption": "六维评估结果雷达图", "chart_source_note": "仅使用评分卡中明确提供的六项数值或声明区间。", "chart_data_basis": "评分卡显式数值或区间中点"},
        "market": {"chart_type": "market_size", "chart_caption": "市场规模与公开数据", "chart_source_note": "仅使用带年份、单位、数值和来源的市场数据卡。", "chart_data_basis": "市场数据卡中的真实规模值"},
    }
    for i, (key, title, words) in enumerate(CHAPTERS, 1):
        override = state.report_preferences.section_overrides.get(key)
        required = key != "declarations"
        if override and not override.enabled and not required:
            continue
        specs.append(ReportSectionSpec(section_key=key, title=title,
            order=override.order if override and override.order is not None else i,
            required=required, is_appendix=not required, target_words=words if deep else int(words * .6),
            depth=override.depth if override and override.depth else "full" if deep else "expanded",
            chart_keys=chart_map.get(key, []),
            chart_metadata={chart_key: chart_metadata[chart_key] for chart_key in chart_map.get(key, []) if chart_key in chart_metadata},
            target_tables=0))
    return sorted(specs, key=lambda s: (s.order, s.section_key))


def compose(state: ContentAssessmentState, specs: list[ReportSectionSpec]) -> None:
    chapters: dict[str, list[ReportBlock]] = defaultdict(list)
    evidence = {e.evidence_id: e for e in state.evidence_records}
    decisions = {d.claim_id: d for d in state.novelty_decisions}
    details = {d.claim_id: d for d in state.claim_assessments}
    links = defaultdict(list)
    for link in state.claim_evidence_links:
        if link.evidence_id in evidence:
            links[link.claim_id].append(link)
    groups = claim_groups(state)
    claim_by_id = {claim.claim_id: claim for claim in state.claims}

    def scoped_evidence(record, claim_id: str):
        """Return the portion of a record that belongs to this claim.

        A single paper is often linked to several retrieval intents.  Using
        the merged record here makes unrelated numeric fields appear to support
        every claim.  The graph already has the authoritative chunk/abstract
        scoping helper; keep this import lazy because graph imports the report
        composer while building a run.
        """
        claim = claim_by_id.get(claim_id)
        if not claim or not record:
            return record
        try:
            from .graph import ContentAssessmentGraph
            return ContentAssessmentGraph._claim_evidence_view(state, claim, record) or record
        except (ImportError, AttributeError, TypeError):
            return record
    group_member_ids = {representative.claim_id: [member.claim_id for member in members]
                        for representative, members in groups}

    def group_links(claim_id: str):
        return [link for member_id in group_member_ids.get(claim_id, [claim_id]) for link in links.get(member_id, [])]

    def group_decision(claim_id: str):
        candidates = [decisions.get(member_id) for member_id in group_member_ids.get(claim_id, [claim_id])]
        candidates = [item for item in candidates if item]
        order = {"有直接支持": 0, "存在相近先例": 1, "方向性支持": 2, "证据支持有限": 2,
                 "暂未发现可比证据": 3, "目标待验证": 3, "当前不可判断": 4, "证据不足": 5}
        return min(candidates, key=lambda item: order.get(item.label, 9), default=None)

    def validation_action(claim, detail, decision) -> str:
        """Turn each claim's actual gaps into a distinct next action."""
        labels = dict(FIELDS)
        missing = [labels[key] for key, status in (detail.field_matrix if detail else {}).items()
                   if status in {"missing", "partial", "conflict", "not_comparable"}]
        indicator_names = [item.name for item in state.indicators
                           if claim.claim_id in (item.source_claim_ids or [item.source_claim_id])]
        target = f"指标“{indicator_names[0]}”" if indicator_names else f"主张“{claim.text[:70]}”"
        if decision and decision.label in {"方向性支持", "存在相近先例"}:
            action = f"为{target}取得最接近文献的全文定位，并复核"
        elif decision and decision.label == "有直接支持":
            action = f"对{target}按先例的对象、方法和验证条件做重复性核验，并保留反证搜索记录"
        else:
            action = f"围绕{target}补齐"
        if missing:
            action += "、".join(dict.fromkeys(missing))
        else:
            action += "基线、测试方法、样本量和验收条件"
        return action + "，形成可重新打开的原始记录。"
    deep = state.report_preferences.resolved_mode == "deep"
    seen_text: set[str] = set()
    quoted_material: set[tuple[str, str]] = set()
    depths = {s.section_key: s.depth for s in specs}

    def expanded(key):
        return depths.get(key) == "full"

    def add(key, text, refs=(), claim="", kind="paragraph", optional=False):
        text = readable(text)
        if not text or (kind == "paragraph" and text in seen_text):
            return
        if kind == "paragraph":
            seen_text.add(text)
        refs = list(dict.fromkeys(r for r in refs if r in evidence))
        chapters[key].append(ReportBlock(kind=kind, text=text, evidence_ids=refs, claim_id=claim, optional_detail=optional))

    title = state.assessment_profile.assessment_object or state.assessment_profile.title
    add("executive", f"本报告评估“{title}”。共审查 {len(state.claims)} 项技术主张和 {len(state.indicators)} 项指标。"
        f"当前为{'正式' if state.quality_gate.report_eligibility == 'formal' else '探索性'}评估："
        f"方向性主张证据覆盖率 {state.report_metrics.directional_claim_coverage:.0%}，"
        f"正式全文覆盖率 {state.report_metrics.formal_claim_coverage:.0%}，"
        f"指标覆盖率 {state.report_metrics.indicator_coverage:.0%}。")
    if state.indicators:
        add("executive", f"已对 {state.report_metrics.indicator_assessed_count}/{len(state.indicators)} 项量化指标形成逐项评估结论；"
            f"其中 {state.report_metrics.indicator_evidence_count} 项具有可定位的同口径数值文献基线。"
            "项目计划目标与已实现结果分别核验，不将文献方向性依据写成目标达成证明。")
    if len(groups) < len(state.claims):
        add("executive", f"材料中的 {len(state.claims)} 个原子要点已按技术价值聚合为 {len(groups)} 个阅读主张组；原子主张仍保留在证据审计中。")
    for claim, members in groups:
        decision = group_decision(claim.claim_id)
        member_text = "；".join(item.text[:90] for item in members[1:])
        add("executive", f"{claim.claim_id}：{decision.label if decision else '当前不可判断'}。"
            f"{sentences(decision.rationale, 220) if decision else '尚未获得可用于判断的比较证据。'}"
            f"{(' 组内要点：' + member_text) if member_text else ''}", claim=claim.claim_id)
    add("executive", "以下先说明评估边界与技术现状，再逐项比较主张和文献证据；综合判断及验证建议据此形成。")
    add("process", f"委托目的：{state.assessment_profile.assessment_goal}。评估对象：{title}。")
    add("process", state.assessment_profile.scope_summary)
    add("process", state.assessment_profile.scope_boundary)
    add("process", f"检索范围：{readable(state.output_json.get('year_range')) or '以已确认检索范围为准'}；"
        f"证据截止日期以封面为准。材料类型：{ {'paper':'论文','project':'项目','guideline':'指南','technology_result':'科技成果','mixed':'混合材料','unknown':'待确认'}.get(state.report_preferences.resolved_artifact_type, '待确认')}。")
    for source in material_sources(state):
        add("process", f"材料《{source.name}》：可用原文 {len(source.content)} 字符，已建立 {len(source.anchors)} 个定位条目。")
    add("process", "方法：从材料原文拆解技术主张，按对象、方法、数据、基线、指标、时间和验证条件逐项比较。"
        "A 级为具有核验全文定位的证据，B 级为摘要或部分文本，C 级为题录线索。证据层级不等同于论文质量评级。")
    add("process", "项目目标、内部观测与外部文献分别记录。没有全文不补写实验数值；检索未命中不等于不存在先例，"
        "本报告不替代正式科技查新、注册审评或独立实验验证。")
    fields = state.nine_fields
    for label, value in [("研究方向", fields.research_direction), ("研究目标", fields.research_goal),
                         ("研究内容", fields.research_content), ("技术路线", fields.technical_route),
                         ("关键技术", fields.key_technologies), ("应用场景", fields.application_scenarios)]:
        if value:
            add("technical", f"{label}：{readable(value)}")
    # Distinct material paragraphs, not whole spreadsheet dumps, explain the
    # actual platform and methods. Upload instructions remain untrusted data.
    for source in material_sources(state):
        selected = 0
        for line in source.content.splitlines():
            if "|" in line or not 60 <= len(line) <= 1000 or re.search(r"报告应|评估时|不得|本次创新性评估", line):
                continue
            if re.search(r"系统|路线|细胞|实验|统计|方法|技术", line) and not re.search(r"比较规则|评估|证据等级|A级|B级|C级", line):
                add("technical", f"材料《{source.name}》记载：{line}", optional=True)
                quoted_material.add((source.file_id, line))
                selected += 1
            if selected >= (4 if expanded("technical") else 2):
                break
    add("landscape", state.trend_analysis.get("summary"))
    for text in state.trend_analysis.get("trend_highlights", []):
        refs = [eid for eid in evidence if re.search(r"(?<![\w-])" + re.escape(eid) + r"(?![\w-])", str(text))]
        if refs:
            add("landscape", text, refs)
    for key in ("themes", "methods"):
        for item in state.trend_analysis.get(key, []):
            if isinstance(item, dict):
                refs = [eid for eid in item.get("evidence_ids", []) if eid in evidence and any(relevant(c, evidence[eid]) for c in state.claims)]
                if refs:
                    add("landscape", readable(item.get("name") or item.get("label")) + "：" +
                        readable(item.get("summary") or item.get("description") or item.get("finding")), refs)
    relevant_evidence = [e for e in evidence.values() if e.source_type == "literature" and any(relevant(c, e) for c in state.claims)]
    for e in relevant_evidence[:12 if expanded("landscape") else 4]:
        add("landscape", f"《{e.title}》（{e.year or '年份待核验'}）的相关信息："
            f"{sentences(e.abstract or e.excerpt, 600 if expanded('landscape') else 220) or '尚无可用于技术归纳的正文信息。'}", [e.evidence_id], optional=True)
    if not relevant_evidence:
        add("landscape", "当前未获得足以归纳本领域技术演化的相关文献，研究现状不可判定。需补充对应技术对象和方法的文献，不能用无关领域统计代替。")
    add("claims", "以下将技术主张与可观测指标分开。目标值不代表已实现性能；具体比较条件和判定见逐项分析。")
    add("comparison", "各项均按主张出处、最近先例、关键比较、判定限制和验证建议展开。摘要或题录可以支持方向性判断，但不能替代全文实验数据。文中所说的比较维度，是指对象、方法、数据、基线、指标、时间和验证条件。")
    for claim, members in groups:
        add("claims", f"{claim.claim_id}：{claim.text} 状态：{STATUS[claim.assertion_type]}。"
            + (f" 聚合组：{claim.aggregation_group}。{claim.aggregation_reason}"
               f" 组内相关要点：{'；'.join(item.text for item in members[1:])}" if len(members) > 1
               else f" 聚合说明：{claim.aggregation_reason}"), claim=claim.claim_id)
        if claim.claim_fields.get("data"):
            add("claims", f"{claim.claim_id} 的目标与数据口径：{claim.claim_fields['data']}。", claim=claim.claim_id)
    for item in state.indicators:
        add("claims", f"{item.indicator_id} {item.name}：定义为{item.definition or '材料未给出'}；单位{item.unit or '材料未给出'}；"
            f"目标或记录值{item.value or item.acceptance_condition or '材料未给出'}；"
            f"基线{item.baseline or '材料未给出'}；测试方法{item.test_method or '材料未给出'}；"
            f"统计方法{item.statistics_method or '材料未给出'}。")
        validation = next((entry for entry in state.indicator_validations if entry.indicator_id == item.indicator_id), None)
        if validation and validation.assessment_conclusion:
            add("claims", f"{item.indicator_id} 本次评估：{validation.assessment_conclusion}"
                f"项目基线：{validation.baseline or '未提供'}；"
                f"可比文献基线：{validation.literature_baseline or '未找到同口径数值'}；"
                f"测试方法：{validation.test_method or '未提供'}。", validation.evidence_refs)
    cached_results_notice = False
    for source in material_sources(state):
        for row, offset in rows(source):
            if not row.get("批次 A 结果"):
                continue
            if not cached_results_notice:
                add("claims", "下列内部记录为构造测试批次的表内缓存计算结果，尚未独立复算，不能作为正式实验结论。原始精度保留于证据附件。")
                cached_results_notice = True
            endpoint = row.get("质量门", "")
            value = re.sub(r"^.*?；缓存值\s*", "", row["批次 A 结果"])
            try:
                value = f"{float(value):.4g}"
            except ValueError:
                pass
            status = re.sub(r"^.*?；缓存值\s*", "", row.get("状态", ""))
            anchor = locate(source, source.content[offset:].splitlines()[0]) or {}
            at = anchor.get("source_locator", {})
            add("claims", f"内部记录核对：{endpoint}，表内阈值 {row.get('阈值', '材料未给出')}，缓存结果 {value}，"
                f"表内状态“{status or '待确认'}”。来源《{source.name}》{at.get('sheet', '')} 第 {at.get('row', '?')} 行。")
    for claim, members in groups:
        cid = claim.claim_id
        detail = details.get(cid)
        decision = group_decision(cid)
        add("comparison", f"{cid} 主张证据分析", claim=cid, kind="heading")
        if len(members) > 1:
            add("comparison", "本组聚合的材料要点：" + "；".join(item.text for item in members), claim=cid)
            for member in members[1:]:
                add("comparison", f"{member.claim_id} 原子主张已归入 {cid} 组：{member.text}。其原文定位和证据关联保留在同组分析及审计快照中。", claim=member.claim_id, optional=True)
        source = next((s for s in material_sources(state) if s.file_id == claim.source_document_id), None)
        locator_labels = {"table": "表", "row": "行", "paragraph": "段落", "page": "页", "sheet": "工作表", "cell": "单元格", "line": "行"}
        locator = "，".join(f"{locator_labels.get(k, k)} {v}" for k, v in claim.source_locator.items() if k != "kind")
        add("comparison", f"主张及出处：{claim.text} 来源《{source.name if source else claim.source_document_id or '待确认'}》，"
            f"{locator or claim.source_paragraph_ref or '定位信息未提供'}。"
            f"状态：{STATUS[claim.assertion_type]}。", claim=cid)
        nearest_raw = evidence.get(detail.nearest_prior_art_id) if detail else None
        nearest = scoped_evidence(nearest_raw, cid)
        add("comparison", f"最近先例候选：{nearest.title}；证据等级 {nearest.evidence_level}，是否同口径可比需以下字段核验。"
            if nearest else "最近先例：尚未获得可比证据，不能据此认定不存在先例。",
            [nearest.evidence_id] if nearest else [], claim=cid)

        # Keep the comparison tied to the current claim.  Older versions
        # cleared comparison_fields on papers that were linked to several
        # retrieval intents, which made every claim fall back to the same
        # generic sentence.  A missing field is now described in the context
        # of the named paper and its available abstract/full-text evidence.
        nearest_name = f"《{nearest.title}》" if nearest else "当前纳入文献"
        def display_value(value: str, budget: int | None = None) -> str:
            budget = budget or (520 if expanded("comparison") else 150)
            value = str(value or "").strip()
            if len(value) <= budget:
                return value
            return sentences(value, budget) or value[:budget].rstrip("，；。 ") + "……"
        # Show only fields that contain an actual project value or a value in
        # the claim-scoped evidence.  Missing dimensions are summarized once
        # below, so the body reads like a professional comparison rather than
        # seven repetitions of “not covered”.
        comparison = []
        missing = []
        matrix = detail.field_matrix if detail else {}
        for key, label in FIELDS.items():
            own = str(claim.claim_fields.get(key) or "").strip()
            other = str(nearest.comparison_fields.get(key) or "").strip() if nearest else ""
            status = matrix.get(key, "missing")
            if own and other:
                comparison.append(f"{label}：项目为“{display_value(own)}”；{nearest_name}记录为“{display_value(other)}”；{STATUS.get(status, status)}。")
            elif own and not other and status in {"conflict", "not_comparable", "partial"}:
                comparison.append(f"{label}：项目为“{display_value(own)}”；{nearest_name}未形成同口径记录（{STATUS.get(status, status)}）。")
            elif status in {"missing", "not_comparable"}:
                missing.append(label)
        if comparison:
            # The deep/reference layout keeps every populated dimension. The
            # standard layout keeps the most decision-relevant dimensions in
            # one compact paragraph; the evidence bundle retains the matrix.
            visible = comparison if expanded("comparison") else [comparison[index] for index in (0, 1, 4) if index < len(comparison)]
            add("comparison", "关键比较：" + "".join(visible), claim=cid)
            if expanded("comparison") and len(comparison) > len(visible):
                add("comparison", f"其余比较维度已记录在证据对照附录（另有 {len(comparison) - len(visible)} 项具备可核对记录）。", claim=cid, optional=True)
        if missing:
            add("comparison", f"口径边界：{nearest_name}未报告或无法同口径比较的维度为“{'、'.join(missing)}”；"
                "该边界只限制定量比较的强度，不代表主张本身不成立。", claim=cid)

        focus = []
        if nearest:
            evidence_text = sentences(nearest.excerpt or nearest.abstract, 520 if expanded("comparison") else 240)
            if evidence_text:
                focus.append(f"{nearest_name}的公开摘要/正文线索：{evidence_text}")
            if nearest.keywords:
                focus.append(f"检索主题：{'、'.join(nearest.keywords[:6])}")
            covered = [label for key, label in FIELDS.items() if nearest.comparison_fields.get(key)]
            if covered:
                focus.append(f"该文献可直接对照：{'、'.join(covered)}")
        if focus:
            add("comparison", "证据侧重点：" + "；".join(focus), [nearest.evidence_id] if nearest else [], claim=cid)
        else:
            add("comparison", "证据侧重点：本组记录暂未形成可引用的公开摘要或正文片段，因此仅作主题层面的谨慎判断。", claim=cid)
        candidate_links = sorted(group_links(cid), key=lambda l: (l.relation != "contradicts", l.evidence_id != (nearest.evidence_id if nearest else ""), -l.confidence))
        important = [l for l in candidate_links if l.relation == "contradicts"]
        # Keep one representative relation in the standard report; the
        # complete relation set remains in the evidence appendix. A concrete
        # contradiction is preferred because it changes the judgement.
        if expanded("comparison"):
            important += [l for l in candidate_links if l.relation != "contradicts"][:6]
        else:
            important = (important or [l for l in candidate_links if l.relation != "contradicts"])[:1]
        shown_sources = set()
        for link in important:
            if link.evidence_id in shown_sources:
                continue
            shown_sources.add(link.evidence_id)
            record = scoped_evidence(evidence[link.evidence_id], link.claim_id)
            matching_chunks = [c for c in state.evidence_chunks if c.evidence_id == link.evidence_id
                and c.reopenable and c.evidence_level == "A" and link.evidence_level == "A"
                and (not link.evidence_chunk_ids or c.chunk_id in link.evidence_chunk_ids)
                and (not c.claim_ids or link.claim_id in c.claim_ids)]
            if matching_chunks:
                chunk = matching_chunks[0]
                position = chunk.locator.get("display") or chunk.section_ref or chunk.paragraph_ref or "正文片段"
                add("comparison", f"{STATUS.get(link.relation, '背景依据')}：《{record.title}》"
                    f"（{'、'.join(record.authors[:2]) or '作者未提供'}，{record.year or '年份未提供'}）；"
                    f"来源：{chunk.locator.get('source') or '科学文献'}；{position}。"
                    f"原文摘录：“{chunk.excerpt[:500]}”。该片段仅用于当前主张及所报告的适用条件。",
                    [record.evidence_id], claim=link.claim_id)
        grouped = defaultdict(list)
        for link in important:
            grouped[(link.relation, link.difference, link.uncertainty)].append(link)
        for group in grouped.values():
            link = group[0]
            e = scoped_evidence(evidence[link.evidence_id], link.claim_id)
            add("comparison", f"{cid} 补充候选证据：{STATUS.get(link.relation, link.relation)}。"
                f"比较理由：{display_value(link.difference or readable(link.incomparability_reasons), 300)}。"
                f"限制：{display_value(link.uncertainty or '证据仅适用于所报告的对象与验证条件', 220)}。", [l.evidence_id for l in group], claim=cid)
            if expanded("comparison") and e != nearest:
                available = [(label, claim.claim_fields.get(key), e.comparison_fields.get(key)) for key, label in FIELDS.items() if e.comparison_fields.get(key)]
                if available:
                    # The nearest candidate already has all seven fields.
                    # Other candidates contribute their first two distinct
                    # comparison fields; their full matrix is in the bundle.
                    add("comparison", "补充比较：" + "".join(f"{label}方面，文献记录为“{other}”。" for label, own, other in available[:2]), [e.evidence_id], claim=cid)
        decision_refs = list(dict.fromkeys((decision.evidence_ids if decision else []) + [link.evidence_id for link in group_links(cid)]))
        decision_label = decision.label if decision else "当前不可判断"
        user_label = {
            "有直接支持": "已有直接公开依据",
            "存在相近先例": "存在相近技术先例",
            "方向性支持": "形成方向性支持",
            "暂未发现可比证据": "本次检索范围内暂未形成同口径结论",
            "当前不可判断": "当前证据不足以作出稳定判断",
        }.get(decision_label, decision_label)
        rationale = decision.rationale if decision else (detail.conclusion if detail else "")
        limit = readable(decision.risk_notes) if decision else ""
        if not limit and detail:
            limit = detail.uncertainty or readable(detail.incomparability_reasons)
        add("comparison", f"当前判定：{user_label}。{rationale or '该判断仅适用于本轮纳入的对象、方法和证据范围。'}"
            f"{(' 证据边界：' + limit) if limit else ''}", decision_refs, claim=cid)
        advice = decision.rewrite_advice if decision and decision.rewrite_advice else "保留技术目标，并把比较对象、指标口径和适用条件写清楚。"
        add("comparison", f"申报文本建议：{advice}", claim=cid)
        gaps = (detail.evidence_gaps if detail else []) or [f"明确{label}并补充可定位依据" for key, label in FIELDS.items() if not claim.claim_fields.get(key)]
        if gaps:
            add("comparison", f"指标口径与证据边界：{readable(gaps)}。这些是影响定量比较的具体条件，不代表该技术路线不存在。", claim=cid)
    add("assessment", "以下综合判断建立在逐主张比较基础上。各维度分别报告，不合成单一总分，成熟度不能由文献数量推定。")
    for validation in state.indicator_validations:
        if validation.assessment_conclusion:
            add("assessment", validation.assessment_conclusion, validation.evidence_refs)
    for item in state.scorecard:
        add("assessment", f"{item.dimension}：{item.assessment} {item.score_range}。依据：{readable(item.basis)}。限制：{item.uncertainty}")
    for claim, members in groups:
        details_for_group = [details.get(member.claim_id) for member in members]
        details_for_group = [item for item in details_for_group if item]
        if not details_for_group:
            continue
        for item in details_for_group:
            claim_item = claim_by_id.get(item.claim_id)
            decision_item = decisions.get(item.claim_id)
            related_raw = evidence.get(item.nearest_prior_art_id) if item.nearest_prior_art_id else None
            related = scoped_evidence(related_raw, item.claim_id)
            claim_text = claim_item.text if claim_item else item.normalized_claim or item.claim_id
            related_text = f"公开依据为《{related.title}》（{related.year or '年份未提供'}）" if related else "本轮纳入记录未形成直接对照文献"
            advanced = item.advancedness_conclusion or (decision_item.rationale if decision_item else "")
            if not advanced:
                advanced = "基于当前纳入证据，先给出该主张的适用范围判断"
            uncertainty = item.uncertainty or readable(item.incomparability_reasons)
            differences = readable(item.comparable_fields)
            if not differences and related and related.comparison_fields:
                differences = "、".join(FIELDS.get(k, k) for k in related.comparison_fields)
            differences = differences or "对象、方法、指标与验证条件"
            add("assessment", f"主张“{claim_text}”的创新性与先进性：{advanced}。{related_text}。"
                f"本项重点比较{differences}。"
                f"{('证据边界：' + uncertainty) if uncertainty else ''}",
                [related.evidence_id] if related else [], claim=item.claim_id)
    add("assessment", "成熟度观察：按概念、实验室验证、独立重复与应用验证分别核对。缺少独立批次、跨操作者或应用场景数据时，不能仅由设计完整推断成熟。")
    artifact = state.report_preferences.resolved_artifact_type
    modules = {"paper": "论文模块：核验研究方法、实验结果、统计严谨性、引用质量与重复风险。",
               "guideline": "指南模块：核验研究目标、任务范围、指标口径、应用场景与验收要求。",
               "project": "项目模块：核验技术路线、阶段目标、里程碑、交付物与验收条件。",
               "technology_result": "成果模块：核验技术成熟度、转化条件、知识产权与应用风险。"}
    for key, text in modules.items():
        if artifact in {key, "mixed"}:
            add("assessment", text + "仅对材料明确提供的记录作判断；未覆盖的验证结果会在数据限制与验证建议中单独说明。")
    # Type-specific observations use actual material paragraphs, preserving
    # synthetic-data and intended-use qualifications in their original context.
    routes = {"paper": r"统计|样本|重复|研究方法|实验结果|引用",
              "guideline": r"目标|范围|指标|场景|验收|要求",
              "project": r"里程碑|交付|验收|进度|阶段|实施",
              "technology_result": r"成熟|转化|专利|知识产权|应用|合规"}
    for source in material_sources(state):
        count = 0
        for line in source.content.splitlines():
            if "|" in line or not 25 <= len(line) <= 1200:
                continue
            if not any(re.search(pattern, line) for key, pattern in routes.items() if artifact in {key, "mixed"}):
                continue
            if (source.file_id, line) in quoted_material:
                continue
            add("assessment", f"《{source.name}》中的类型核验依据：{line}", optional=True)
            quoted_material.add((source.file_id, line))
            count += 1
            if count >= (4 if expanded("assessment") else 2):
                break
        for line in source.content.splitlines():
            if "|" not in line and 25 <= len(line) <= 1200 and re.search(r"模拟数据|合成数据|临床|注册|监管|商业化|市场规模|合规边界", line):
                if (source.file_id, line) not in quoted_material:
                    add("market", f"《{source.name}》说明：{line}")
                    quoted_material.add((source.file_id, line))
    for label, cards in [("市场", state.market_data_cards), ("竞争", state.competitive_data_cards), ("监管", state.regulatory_data_cards)]:
        add("market", label + "观察", kind="heading")
        for card in cards:
            refs = card.source_evidence_ids
            if card.status == "insufficient_evidence" or not refs:
                add("market", f"{label}观察：本轮检索未获得可用于量化该项的公开资料。{card.limitation}")
            else:
                value = f"{card.value or '暂无量化值'} {card.unit}".strip()
                if (label == "竞争" and card.observation_level == "quantitative"
                        and card.x_value is not None and card.y_value is not None
                        and math.isfinite(card.x_value) and math.isfinite(card.y_value)
                        and card.x_label and card.y_label):
                    value = f"{card.x_label} {card.x_value:g} {card.x_unit}；{card.y_label} {card.y_value:g} {card.y_unit}"
                if card.period:
                    value += f"（{card.period}）"
                add("market", f"{card.subject}：{value}。{card.limitation}", refs)
        if not cards:
            add("market", f"{label}观察：本轮没有形成可回溯的专门资料，因此不推断{'市场规模' if label == '市场' else '机构或产品排名' if label == '竞争' else '监管认可或合规通过'}。")
    for text in state.trend_analysis.get("competition_observations", []):
        refs = [eid for eid in evidence if re.search(r"(?<![\w-])" + re.escape(eid) + r"(?![\w-])", str(text))]
        if refs:
            add("market", text, refs)
    for claim, members in groups:
        detail = details.get(claim.claim_id)
        gaps = detail.evidence_gaps if detail else []
        decision = group_decision(claim.claim_id)
        action_text = validation_action(claim, detail, decision)
        if gaps:
            action_text += f" 当前记录的具体缺口为：{readable(gaps)}。"
        add("roadmap", f"{claim.claim_id} 验证任务：{action_text}"
            "实施时核对指标定义、单位、基线和测试方法。", claim=claim.claim_id)
    add("roadmap", "实施顺序：先验证会改变判定的关键证据，再开展同口径复测，最后检查应用与合规边界。每次更新保留原报告及证据版本，避免把计划中的验证写成已经完成的结果。")
    add("conclusion", f"对“{title}”的综合判断：{state.quality_gate.report_eligibility == 'formal' and '在已确认范围内形成正式评估' or '当前仅形成探索性评估'}。"
        "可用结论及其适用条件以逐主张分析为准；不能将局部结果扩展为整体领先或监管认可。")
    if state.indicators:
        add("conclusion", f"本次已完成 {state.report_metrics.indicator_assessed_count}/{len(state.indicators)} 项指标评估，"
            f"其中 {state.report_metrics.indicator_evidence_count} 项取得同口径数值文献基线。"
            "未提供项目实测结果的目标不能判定为已达成；各指标的可用发现与具体限制见指标章节。")
    for text in list(dict.fromkeys(state.quality_gate.blockers + state.quality_gate.warnings
                                  + state.output_json.get("fulltext_limitations", [])))[:8]:
        add("conclusion", text)
    add("declarations", "报告仅用于已确认评估目的。原始材料、完整证据、逐字段矩阵及检索记录见同版本证据附件。")
    for source in material_sources(state):
        add("declarations", f"{source.name}；文件 ID {source.file_id}；内容校验 {source.content_hash or '待补充'}。")

    # Mark only records that actually appear in a reader-facing reasoning
    # block. Retrieval candidates that never affect a conclusion stay out of
    # references and the downloadable evidence package.
    evidence_usage: dict[str, set[str]] = defaultdict(set)
    for spec_key, blocks in chapters.items():
        if spec_key == "references":
            continue
        for block in blocks:
            if not block.evidence_ids:
                continue
            if spec_key == "market":
                usage = "used_for_market"
            elif spec_key in {"assessment", "roadmap", "conclusion"}:
                usage = "used_for_risk"
            else:
                usage = "used_for_claim"
            for evidence_id in block.evidence_ids:
                evidence_usage[evidence_id].add(usage)
    for item in state.evidence_records:
        item.usage = sorted(evidence_usage.get(item.evidence_id, {"unused"}))

    cited = list(dict.fromkeys(r for blocks in chapters.values() for b in blocks for r in b.evidence_ids))
    identities = set()
    for eid in cited:
        e = evidence[eid]
        identity = source_identity(e)
        if identity in identities:
            continue
        identities.add(identity)
        add("references", f"{eid} {readable(e.authors) or '作者待核验'}. {e.title}. {e.venue or '来源刊物待核验'}, "
            f"{e.year or '未提供'}. DOI：{e.doi or '未提供'}；NSTL 详情：{http_url(e.nstl_url) or '未提供'}；原文地址：{http_url(e.source_url or e.url) or '未提供'}。"
            f"定位：{e.page_ref or e.section_ref or e.paragraph_ref or ('全文字符 '+str(e.char_start) if e.anchor_verified else '摘要或题录，尚无全文定位')}。", [eid])
    if not cited:
        add("references", "当前无可支撑正文比较的外部引用。完整候选记录保留在证据附件，不将无关文献列为结论依据。")
    for spec in specs:
        spec.blocks = chapters[spec.section_key]
        refresh_section(spec)


def block_text_with_sources(block: ReportBlock, *, references: bool = False) -> str:
    """Keep source markers outside table syntax and out of heading labels."""
    value = block.text
    if references or block.kind in {"heading", "caption", "figure"}:
        return value
    missing = [eid for eid in block.evidence_ids if not re.search(
        r"(?<![\w-])" + re.escape(eid) + r"(?![\w-])", value)]
    if missing:
        value += ("\n\n依据：" if block.kind == "table" else " ") + " ".join(missing)
    return value


def refresh_section(spec: ReportSectionSpec) -> None:
    rendered = []
    for block in spec.blocks:
        if block.kind == "figure":
            continue
        text = block_text_with_sources(block, references=spec.section_key == "references")
        if block.kind == "callout":
            label = {"execution": "公开执行摘要", "result": "结果", "risk": "风险"}[block.callout_style]
            text = f"> 【{label}】" + text.replace("\n", "\n> ")
        rendered.append(("#" * block.heading_level + " " if block.kind == "heading" else "") + text)
    spec.content = "\n\n".join(rendered)
    spec.actual_words = len(spec.content)
    spec.actual_tables = sum(b.kind == "table" for b in spec.blocks)
    spec.evidence_refs = list(dict.fromkeys(r for b in spec.blocks for r in b.evidence_ids))
    spec.actual_evidence_refs = len(spec.evidence_refs)
    spec.status = "generated" if spec.content else "insufficient"


def freeze_snapshot(state: ContentAssessmentState, condensation_passes: int = 0, *, historical: bool = False) -> None:
    from .research_outline import TEMPLATE, RENDER, SCHOLARLY_TEMPLATES
    scholarly = not historical and (state.output_json.get("scholarly_report") or
        (state.report_snapshot and state.report_snapshot.template_version in SCHOLARLY_TEMPLATES))
    research = not historical and (state.output_json.get("research_report") is True or
        (state.report_snapshot and state.report_snapshot.template_version == TEMPLATE))
    if not scholarly:
        normalize_outline(state, historical=historical)
    # Keep the API-facing section list synchronized with the exact blocks used
    # by the frozen snapshot. This prevents the page reader from showing the
    # pre-projection process outline while exports use the result outline.
    # Section switches and semantic condensation can remove the only mention
    # of a source. Keep references aligned with the final body, retaining DOI
    # aliases used by other claims and all evidence in the audit attachment.
    evidence = {e.evidence_id: e for e in state.evidence_records}
    cited_identities = {source_identity(evidence[eid])
        for section in state.report_plan.section_specs if section.section_key != "references"
        for eid in section.evidence_refs if eid in evidence}
    for section in state.report_plan.section_specs:
        if section.section_key != "references" or not section.blocks:
            continue
        section.blocks = [block for block in section.blocks if not block.evidence_ids or any(
            eid in evidence and source_identity(evidence[eid]) in cited_identities for eid in block.evidence_ids)]
        if not section.blocks:
            section.blocks = [ReportBlock(kind="paragraph", text="当前正文没有外部引用，完整候选记录见证据附件。")]
        refresh_section(section)
    keys = ("claims", "indicators", "indicator_validations", "evidence_records", "evidence_chunks", "claim_evidence_links",
            "claim_assessments", "novelty_decisions", "advancedness_decisions", "query_plans",
            "guideline_requirements", "work_packages")
    audit = {key: [item.model_dump() for item in getattr(state, key)] for key in keys}
    if scholarly:
        audit["reader_point_summaries"] = {
            cid: rows.get("judgement", [])[:2]
            for cid, rows in state.output_json.get("scholarly_drafts", {}).items()}
        audit["reader_point_evaluations"] = {
            cid: rows.get("judgement", [])
            for cid, rows in state.output_json.get("scholarly_drafts", {}).items()}
    if not historical:
        audit["reader_comparisons"] = state.output_json.get("reader_comparisons", {})
        audit["reader_research_analyses"] = state.output_json.get("reader_research_analyses", {})
        audit["retrieval_source_order"] = state.output_json.get("retrieval_source_order", {})
        # Preserve the appraisals already read in the assessment step when the
        # new task freezes. Report body and historical snapshots are unchanged.
        audit.setdefault("reader_point_evaluations", {}).update(
            state.output_json.get("reader_point_evaluations", {}))
    if scholarly:
        audit["report_source_selection"] = state.output_json.get("report_source_selection", {})
        audit["scholarly_methods_completion"] = state.output_json.get("scholarly_methods_completion", {})
        audit["scholarly_style_checks"] = state.output_json.get("scholarly_style_checks", {})
    audit['material_observations'] = state.output_json.get('material_observations', [])
    audit["materials"] = [{"file_id": f.file_id, "name": f.name, "content": f.content,
                           "content_hash": f.content_hash, "anchors": f.anchors} for f in material_sources(state)]
    citations = {}
    evidence = {e.evidence_id: e for e in state.evidence_records}
    source_numbers = {}
    for s in state.report_plan.section_specs:
        for eid in s.evidence_refs:
            if eid not in evidence:
                continue
            e = evidence[eid]
            identity = source_identity(e)
            if identity not in source_numbers:
                source_numbers[identity] = len(source_numbers) + 1
            citations[eid] = source_numbers[identity]
    if not historical:
        # The body determines numbering. List sources in that same order,
        # rather than the original retrieval order; historical prose is kept.
        for section in state.report_plan.section_specs:
            if section.section_key == "references":
                section.blocks.sort(key=lambda block: min(
                    (citations[eid] for eid in block.evidence_ids if eid in citations), default=0))
                refresh_section(section)
    payload = {"audit": audit, "sections": [s.model_dump() for s in state.report_plan.section_specs],
               "mode": state.report_preferences.resolved_mode, "report_version": state.report_plan.report_version,
               "title": state.report_plan.report_title, "report_type": state.report_plan.report_type,
               "cutoff": state.report_plan.evidence_cutoff_date, "render_version": state.report_plan.render_version}
    chart_snapshot = {
        "trend_analysis": state.trend_analysis,
        "market_data_cards": [item.model_dump() for item in state.market_data_cards],
        "competitive_data_cards": [item.model_dump() for item in state.competitive_data_cards],
        "regulatory_data_cards": [item.model_dump() for item in state.regulatory_data_cards],
        "scorecard": [item.model_dump() for item in state.scorecard],
    }
    cover_metadata = {
        "title": state.report_plan.report_title,
        "assessment_object": state.assessment_profile.assessment_object or state.assessment_profile.title,
        "assessment_goal": state.assessment_profile.assessment_goal,
        "report_type": state.report_plan.report_type,
        "report_version": state.report_plan.report_version,
        "evidence_cutoff_date": state.report_plan.evidence_cutoff_date,
        "generated_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M"),
    }
    # Freeze the renderer template in the version chain as well as on the
    # snapshot.  Exporters use the chain for the cover/version table; leaving
    # the legacy graph template here made a V3 report appear as an old V5 one.
    template_version = TEMPLATE if research else "ContentAssessmentResultReportV5" if scholarly else "ContentAssessmentResultReportV4" if not historical else "ContentAssessmentReportReferenceV2"
    state.versions.template_version = template_version
    if research:
        state.versions.chart_data_snapshot_version = "assessment-charts-v2"
    context_keys = ("assessment_profile", "boundary_matrix", "nine_fields", "quality_gate", "report_preferences", "report_metrics", "versions")
    render_context = {key: getattr(state, key).model_dump() for key in context_keys}
    render_context["report_plan"] = state.report_plan.model_dump(exclude={"section_specs", "sections"})
    render_context["search_trace"] = state.output_json.get("search_trace", [])
    stage_trace = [] if historical else [item.model_dump() for item in state.stage_trace]
    process_trace = [{"stage_key": key, "steps": [item.model_dump() for item in steps]}
                     for key, steps in state.process_trace.items()]
    if historical:
        chart_snapshot = {}
        process_trace = []
    render_version = RENDER if research else "scholarly-layout-v2" if scholarly else "phase2-layout-v1" if not historical else "reference-layout-v2"
    payload["template_version"] = template_version
    payload["chart_data_snapshot"] = chart_snapshot
    payload["render_context"] = render_context
    payload["stage_trace"] = stage_trace
    payload["process_trace"] = process_trace
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    state.report_snapshot = ReportSnapshot(schema_version="report-snapshot-v7" if historical else "report-snapshot-v9", snapshot_id=f"snapshot-{digest}", generated_at=datetime.now(BEIJING_TZ).isoformat(),
        sections=[s.model_copy(deep=True) for s in state.report_plan.section_specs],
        citation_numbers=citations, audit_data=audit, condensation_passes=condensation_passes,
        template_version=template_version, render_version=render_version,
        cover_metadata=cover_metadata, chart_data_snapshot=chart_snapshot,
        stage_trace=stage_trace, process_trace=process_trace, render_context=render_context,
        layout_config={"page_size": "A4", "margin_mm": 20, "body_pt": 10.5, "line_pt": 18,
                       "cjk_font": "Noto Sans CJK SC", "latin_font": "Liberation Serif", "navy": "1E3A8A"}).model_copy(deep=True)
    from .report_presentation import display_snapshot
    state.report_snapshot = display_snapshot(state.report_snapshot)
    state.report_plan.section_specs = [s.model_copy(deep=True) for s in state.report_snapshot.sections]
    state.report_plan.sections = [s.model_dump(exclude_none=True) for s in state.report_snapshot.sections]
    state.report_plan.render_version = state.report_snapshot.render_version
    from app.services.content_assessment_report import prepare_chart_manifest
    prepare_chart_manifest(state)
    paragraphs = [b.text for s in state.report_plan.section_specs for b in s.blocks if b.kind == "paragraph" and len(b.text) >= 80]
    exact = 1 - len(set(paragraphs)) / max(1, len(paragraphs))
    near = sum(any(SequenceMatcher(None, p, q).ratio() >= .9 for q in paragraphs[:i]) for i, p in enumerate(paragraphs))
    state.output_json["quality_checks"] = {"duplicate_paragraph_ratio": round(exact, 3),
        "near_duplicate_paragraph_ratio": round(near / max(1, len(paragraphs)), 3),
        "near_duplicate_review_required": near / max(1, len(paragraphs)) >= .05,
        "empty_section_count": sum(not s.content.strip() for s in state.report_plan.section_specs)}
    if near / max(1, len(paragraphs)) >= .05:
        warning = "正文存在近似重复段落，建议复核相似说明是否可合并；不得删除不同主张的关键比较。"
        if warning not in state.quality_gate.warnings:
            state.quality_gate.warnings.append(warning)


def source_identity(e) -> str:
    doi = re.sub(r"^https?://(?:dx\.)?doi.org/|^doi:\s*", "", (e.doi or "").strip().lower())
    return doi or (e.source_url or e.nstl_url or e.url or "").rstrip("/").lower() or re.sub(r"\W+", "", e.title.lower())


def reader_text(text: str) -> str:
    return re.sub(r"\brun_[A-Za-z0-9_-]+\b", "本次评估", text or "")


def state_from_snapshot(snapshot: ReportSnapshot) -> ContentAssessmentState:
    """Reconstitute rendering inputs without consulting mutable task state."""
    context = snapshot.render_context
    values = {key: context[key] for key in ("assessment_profile", "boundary_matrix", "nine_fields", "quality_gate", "report_preferences", "report_metrics", "versions") if key in context}
    values["raw_user_input"] = str(snapshot.cover_metadata.get("assessment_object") or "")
    for key, value in snapshot.audit_data.items():
        if key in ContentAssessmentState.model_fields and key not in values:
            values[key] = value
    values["stage_trace"] = snapshot.stage_trace
    values["process_trace"] = {item["stage_key"]: item.get("steps", []) for item in snapshot.process_trace}
    values.update({key: value for key, value in snapshot.chart_data_snapshot.items()
                   if key in ContentAssessmentState.model_fields})
    plan = dict(context.get("report_plan", {}))
    plan.update({key: snapshot.cover_metadata[key] for key in ("report_type", "report_version", "evidence_cutoff_date") if key in snapshot.cover_metadata})
    plan["report_title"] = snapshot.cover_metadata.get("title", plan.get("report_title", "内容评估报告"))
    plan["section_specs"] = [item.model_dump() for item in snapshot.sections]
    plan["sections"] = [item.model_dump() for item in snapshot.sections]
    values["report_plan"] = plan
    values["output_json"] = {"search_trace": context.get("search_trace", [])}
    return ContentAssessmentState.model_validate(values).model_copy(deep=True)


def normalize_outline(state: ContentAssessmentState, *, historical: bool = False) -> None:
    from .reference_outline import normalize_reference_outline
    normalize_reference_outline(state, historical=historical)


def render_citations(text: str, numbers: dict[str, int]) -> str:
    if not numbers:
        return text
    pattern = "|".join(re.escape(k) for k in sorted(numbers, key=len, reverse=True))
    return re.sub(r"(?<![\w-])(?:" + pattern + r")(?![\w-])", lambda m: f"[{numbers[m[0]]}]", text)


def markdown(state: ContentAssessmentState) -> str:
    from .research_outline import SCHOLARLY_TEMPLATES, TEMPLATE
    snapshot = state.report_snapshot
    numbers = snapshot.citation_numbers if snapshot else {}
    from app.services.content_assessment_report import _display_title, build_report_chart_assets, prepare_chart_manifest
    import base64
    prepare_chart_manifest(state)
    frozen = state_from_snapshot(snapshot) if snapshot else state
    plan = frozen.report_plan
    if snapshot and snapshot.template_version in SCHOLARLY_TEMPLATES:
        lines = [f"# {_display_title(state)}", "", f"资料截止日期：{plan.evidence_cutoff_date}", "", "## 目录", ""]
    else:
        lines = [f"# {_display_title(state)}", "", plan.report_type,
                 f"报告版本：{plan.report_version}", f"证据截止日期：{plan.evidence_cutoff_date}", "", "## 目录", ""]
    specs = snapshot.sections if snapshot else state.report_plan.section_specs
    research = bool(snapshot and snapshot.template_version in {TEMPLATE, "ContentAssessmentResearchReportV6"})
    for spec in specs:
        lines.append(f"{'  ' * (spec.level - 1)}- {spec.title}")
        lines.extend(f"{'  ' if research else '  ' * (b.heading_level - 1)}- {b.text}"
                     for b in spec.blocks if b.kind == "heading" and (not research or b.heading_level <= 3))
    assets = build_report_chart_assets(state) if snapshot else {}
    def figure(key):
        entry = next((e for e in snapshot.chart_manifest if e["chart_key"] == key), None) if snapshot else None
        if entry and entry["included"] and key in assets:
            contents = [assets[key]]
            contents.extend(assets[f"{key}:{i}"] for i in range(2, len(assets) + 2) if f"{key}:{i}" in assets)
            for index, content in enumerate(contents, 1):
                caption = entry["chart_caption"] + (f"（{index}）" if len(contents) > 1 else "")
                encoded = base64.b64encode(content).decode("ascii")
                lines.extend(["", f"![{caption}](data:image/png;base64,{encoded})", "", caption,
                              f"数据来源：{entry['chart_source_note']}"])

    for spec in specs:
        lines.extend(["", f"{'#' * (spec.level + 1)} {spec.title}", ""])
        if not spec.blocks:
            lines.append(reader_text(render_citations(spec.content, numbers)))
        for block in spec.blocks:
            if block.kind == "figure":
                figure(block.text)
                continue
            value = block_text_with_sources(block, references=spec.section_key == "references")
            value = reader_text(render_citations(value.strip(), numbers))
            if block.kind == "heading":
                value = "#" * (block.heading_level + 1) + " " + value
            elif block.kind == "callout":
                label = {"execution": "公开执行摘要", "result": "结果", "risk": "风险"}.get(block.callout_style, "结果")
                value = f"> 【{label}】{value}"
            lines.extend([value, ""])
        placed = {b.text for b in spec.blocks if b.kind == "figure"}
        for key in spec.chart_keys:
            if key not in placed:
                figure(key)
    return "\n".join(lines) + "\n"


def condense(state: ContentAssessmentState) -> bool:
    if state.report_snapshot and (state.report_snapshot.source_snapshot_id or
            state.report_snapshot.template_version not in {"ContentAssessmentResultReportV3", "ContentAssessmentResultReportV4"}):
        state.report_plan.section_specs = [s.model_copy(deep=True) for s in state.report_snapshot.sections]
        return False
    count = state.report_snapshot.condensation_passes if state.report_snapshot else 0
    if count >= 2:
        return False
    changed = False
    for spec in state.report_plan.section_specs:
        if count == 0:
            seen = set()
            blocks = []
            for b in spec.blocks:
                if b.text not in seen:
                    blocks.append(b)
                    seen.add(b.text)
        else:
            blocks = [b for b in spec.blocks if not b.optional_detail]
            if spec.required and not any(b.kind == "paragraph" for b in blocks):
                blocks = spec.blocks[:]
        if blocks != spec.blocks:
            spec.blocks = blocks
            refresh_section(spec)
            changed = True
    if state.report_snapshot is not None:
        snapshot = state.report_snapshot
        if snapshot.source_snapshot_id:
            # A historical content version is immutable even when its new
            # layout exceeds the current page target.
            state.report_plan.section_specs = [item.model_copy(deep=True) for item in snapshot.sections]
            return False
        from .schemas import EvidenceRecord
        evidence = {item["evidence_id"]: EvidenceRecord.model_validate(item) for item in snapshot.audit_data.get("evidence_records", [])}
        cited = {source_identity(evidence[eid]) for spec in state.report_plan.section_specs if spec.section_key != "references"
                 for eid in spec.evidence_refs if eid in evidence}
        for spec in state.report_plan.section_specs:
            if spec.section_key == "references" and spec.blocks:
                spec.blocks = [block for block in spec.blocks if not block.evidence_ids or any(
                    eid in evidence and source_identity(evidence[eid]) in cited for eid in block.evidence_ids)]
                refresh_section(spec)
        used_ids = {eid for spec in state.report_plan.section_specs for eid in spec.evidence_refs}
        numbers = {eid: number for eid, number in snapshot.citation_numbers.items() if eid in used_ids}
        sections = [item.model_copy(deep=True) for item in state.report_plan.section_specs]
        digest = hashlib.sha256(json.dumps([snapshot.snapshot_id, [item.model_dump() for item in sections]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        state.report_snapshot = snapshot.model_copy(deep=True, update={"sections": sections, "citation_numbers": numbers,
            "condensation_passes": count + 1, "snapshot_id": f"snapshot-{digest}"})
    else:
        freeze_snapshot(state, count + 1)
    return changed
