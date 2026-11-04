"""Source-bound Chapter 4 drafting for newly generated assessment reports."""
from __future__ import annotations

import hashlib
import json

from .research_outline import chapter_definitions
from .scholarly_report import (
    WRITING_RULES, point_title, review_source_assertions, selected_sources,
    validated_paragraphs,
)


METHOD_KEYS = ("selection", "mapping", "comparability", "quality", "limits")


def representative_evidence_ids(state, claim, used):
    """Use the same evidence ordering as the report's appendix crosswalk."""
    linked = {link.evidence_id for link in state.claim_evidence_links if link.claim_id == claim.claim_id}
    cited = linked & used
    cited |= {eid for section in state.output_json.get("scholarly_drafts", {}).get(claim.claim_id, {}).values()
              if isinstance(section, list) for row in section for eid in row.get("evidence_ids", []) if eid in used}
    preferred = {link.evidence_id for link in state.claim_evidence_links
                 if link.claim_id == claim.claim_id and link.relation in {"supports", "partially_supports", "contradicts"}}
    return sorted(cited, key=lambda eid: (eid not in preferred, eid))[:3]


def method_packet(state):
    records = {e.evidence_id: e for e in state.evidence_records}
    drafts = state.output_json.get("scholarly_drafts", {})
    cited = {eid for point in drafts.values() for rows in point.values() if isinstance(rows, list)
             for row in rows for eid in row.get("evidence_ids", []) if eid in records}
    cited.update(eid for key in ("scholarly_summary", "scholarly_conclusion")
                 for row in state.output_json.get(key, []) for eid in row.get("evidence_ids", []) if eid in records)
    cited.update(link.evidence_id for link in state.claim_evidence_links
                 if link.relation == "contradicts" and link.evidence_id in records)
    point_rows = []
    permitted = set()
    for claim in state.claims:
        selected = {e.evidence_id for e in selected_sources(state, claim)}
        ids = [eid for eid in representative_evidence_ids(state, claim, cited) if eid in selected]
        permitted.update(ids)
        point_rows.append({"id": claim.claim_id, "name": point_title(claim), "content": claim.text,
                           "assertion_type": claim.assertion_type, "fields": claim.claim_fields,
                           "selected_for_analysis_ids": sorted(selected),
                           "representative_ids": ids,
                           "relations": [{"evidence_id": link.evidence_id, "relation": link.relation,
                                          "comparable_fields": link.comparable_fields}
                                         for link in state.claim_evidence_links
                                         if link.claim_id == claim.claim_id and link.evidence_id in ids]})
    queries = [{"topic": q.query_group or q.query, "query": q.query, "source_scope": q.source_scope,
                "year_range": q.year_range, "filters": q.filters, "exclusion_terms": q.exclusion_terms,
                "source_status": q.source_status, "candidate_count": q.candidate_count,
                "selected_count": q.selected_count}
               for q in state.query_plans]
    literature = []
    for eid in sorted(permitted):
        record = records[eid]
        verified_body = (record.fulltext_available and record.anchor_verified
                         and record.access_status == "authorized" and record.evidence_level == "A")
        literature.append({"id": eid, "title": record.title, "year": record.year,
                           "content": record.excerpt if verified_body else record.abstract or record.excerpt,
                           "scope": "可定位正文" if verified_body else "摘要",
                           "location": " ".join(filter(None, [record.page_ref, record.section_ref, record.paragraph_ref]))
                                       if verified_body else ""})
    return {"point": {"id": "methods", "name": state.assessment_profile.assessment_object or state.assessment_profile.title,
                      "research_points": point_rows},
            "material_type": state.report_preferences.resolved_artifact_type,
            "material_design": {"submitted_text": state.raw_user_input[:20000],
                                "extracted_fields": state.nine_fields.model_dump(),
                                "search_records": queries,
                                "selection_rule": "逐要点按题名与可读摘要或正文中的研究对象、方法相关性筛选；去除重复来源和仅有书目信息的记录，保留相关相反证据；每要点从可读结果中选取代表资料进行逐项分析。",
                                "saved_literature_count": len([e for e in state.evidence_records if e.source_type == "literature"]),
                                "body_cited_source_count": len(cited),
                                "evidence_scope": state.boundary_matrix.evidence_boundary,
                                "time_scope": state.boundary_matrix.time_boundary},
            "indicators": [i.model_dump() for i in state.indicators],
            "indicator_evaluations": [i.model_dump() for i in state.indicator_validations],
            "literature": literature}


def structured_method_rows(state):
    """Build publication-safe method sections from recorded task facts only."""
    packet = method_packet(state)
    queries = packet["material_design"]["search_records"]
    points = packet["point"]["research_points"]
    literature = {row["id"]: row for row in packet["literature"]}
    topics = "、".join(dict.fromkeys(q["topic"] for q in queries if q["topic"]))
    scopes = "、".join(dict.fromkeys(source for q in queries for source in q["source_scope"]))
    selection = [f"本次检索围绕{topics}开展，记录的来源为{scopes}。" if topics and scopes else
                 "本次按材料中的研究要点检索相关来源。"]
    if queries:
        selection.append("各查询的候选数与纳入数是查询记录，可能包含重复资料；正文引用按实际逐项分析选择。")
    mapping = []
    for point in points:
        ids = point["representative_ids"]
        if ids:
            titles = "、".join("《" + literature[eid]["title"] + "》" for eid in ids if eid in literature)
            mapping.append({"text": f"{point['name']}对应的代表资料包括{titles}；具体研究联系及判断见逐项分析。",
                            "evidence_ids": ids})
    indicators = packet["indicators"]
    comparable = []
    for item in indicators:
        name = item.get("name", "")
        status = item.get("status", "unknown")
        definition = item.get("definition") or item.get("test_method") or item.get("acceptance_condition")
        if name:
            label = "计划目标" if status in {"target", "planned"} else "材料报告结果" if status == "completed" else "材料指标"
            comparable.append(f"{name}属于{label}" + (f"，材料记载的检验条件为{definition}" if definition else ""))
    scope_counts = {scope: sum(row["scope"] == scope for row in literature.values())
                    for scope in ("可定位正文", "摘要")}
    return {
        "selection": [{"text": text, "evidence_ids": []} for text in selection],
        "mapping": mapping or [{"text": "逐项分析依据材料中的研究要点组织；当前没有可列入附录的代表资料。", "evidence_ids": []}],
        "comparability": [{"text": "；".join(comparable) + "。同口径定量比较还需核对对象、单位、基线与验证条件。",
                           "evidence_ids": []}] if comparable else
                         [{"text": "本次以材料中的研究对象和验证条件为比较范围，计划目标与文献结果分别表述。", "evidence_ids": []}],
        "quality": [{"text": f"当前代表资料中有{scope_counts['可定位正文']}项可定位正文、{scope_counts['摘要']}项摘要；"
                     "摘要资料仅用于其明确记载内容的方向性分析。", "evidence_ids": []}],
        "limits": [{"text": "判断适用于本次材料提出的研究对象、方法与检索所得资料；"
                    "定量结论须以一致的指标定义、单位和实验条件为前提。", "evidence_ids": []}],
    }


async def draft_methods(graph, state):
    if state.report_snapshot:
        return
    packet = method_packet(state)
    fingerprint = hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    saved = state.output_json.setdefault("scholarly_methods", {})
    completion = state.output_json.setdefault("scholarly_methods_completion", {})
    if completion.get("source_fingerprint") != fingerprint:
        saved.clear()
        completion.clear()
        completion["source_fingerprint"] = fingerprint
    pending = state.output_json.setdefault("scholarly_methods_pending", {})
    if not saved and completion.get("source_fingerprint") == fingerprint and pending.get("source_fingerprint") != fingerprint:
        pending.clear()
    pending["source_fingerprint"] = fingerprint
    fallback = structured_method_rows(state)

    def use_fallback(key, reason):
        rows = fallback.get(key) or []
        if not rows:
            return
        saved[key] = rows
        pending.pop(key, None)
        completion[key] = {
            "status": "fallback", "paragraph_count": len(rows), "reason": reason,
            "feedback_count": len(completion.get("feedback", {}).get(key, [])),
        }

    titles = dict(next(dims for key, _, dims in chapter_definitions(state.report_preferences.resolved_artifact_type)
                       if key == "methods"))
    for attempt in range(2):
        missing = [key for key in METHOD_KEYS if not saved.get(key)]
        if not missing:
            return
        to_generate = [key for key in missing if not pending.get(key)]
        if to_generate:
            prompt = (
                "撰写科研评估报告第四章中missing_units指定的小节。每节通常写2至3个信息不同的完整自然段，"
                "资料有限时据实缩短，不凑字数。返回JSON对象，键为小节key，值为{text,evidence_ids,kind}段落数组。"
                "selection：逐项说明真实检索主题、来源、记录的年份范围及从候选到实质分析的筛选；"
                "query的candidate_count和selected_count是单次查询记录，不能求和充当去重文献数；"
                "区分保存的检索记录、逐项分析的代表资料与正文实际引用。未记录筛选阈值不得补造。"
                "mapping：按照research_points及其representative_ids解释每个要点的代表研究、支持或相反信息及评价用途；"
                "每条文献事实绑定对应evidence_ids，不得使用其他来源。与附录的代表文献范围保持一致。"
                "comparability：结合材料中真实对象、指标定义、单位、基线、验证条件判断可比性；"
                "计划目标、材料自述结果与文献结果分别表达，不将一方数字当双方同口径比较。"
                "quality：根据literature.scope和location区别摘要与可定位正文，说明确实影响判断的覆盖和不确定性；"
                "不得声称读过没有提供的全文。"
                "limits：具体说明本次方法支持哪些判断以及适用条件，不用通用免责句替代。"
                "研究结论和数值只依赖source_material。文献事实必须附实际ID；材料事实可以无文献ID。"
                "不得编造检索日期、筛选比例、质量评分、实验条件或来源。"
            )
            if attempt:
                prompt += "只修复仍缺失小节；参考validation_feedback中的具体原因，已完成小节不得重写。"
            payload = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
                user_prompt=prompt + "\n" + json.dumps({"missing_units": {key: titles[key] for key in to_generate},
                    "validation_feedback": completion.get("feedback", {}), "source_material": packet}, ensure_ascii=False),
                max_tokens=6500)
            if not isinstance(payload, dict):
                # None is the model client's interruption signal (including
                # empty/unparseable responses). Keep the checkpoint for retry;
                # it is not a semantic rejection of a generated section.
                for key in to_generate:
                    completion[key] = {"status": "pending", "reason": "generation_unavailable"}
                await graph._emit_stage_update(state)
                return
            for key in to_generate:
                issues = []
                rows = validated_paragraphs(payload.get(key), packet, issues, review_draft=True)
                if rows:
                    pending[key] = rows
                completion.setdefault("feedback", {})[key] = issues
                if not rows:
                    use_fallback(key, "invalid_or_missing_section")
            await graph._emit_stage_update(state)
        for key in missing:
            rows = pending.get(key)
            if not rows:
                continue
            audit_key = f"scholarly_methods_source_reviews:{key}"
            accepted = await review_source_assertions(graph, state, packet, {key: rows},
                audit_key=audit_key, repair_budget=0,
                review_instruction="第四章仅可叙述source_material中的检索记录、材料事实和所引文献内容；"
                    "核对查询计数是否被误当去重文献数，核对摘要是否被误当全文，代表文献是否属于对应要点。")
            audit = state.output_json.get(audit_key, {}).get("methods", {})
            # Keep a compact, per-unit audit index for inspection without
            # allowing later units to overwrite the previous audit record.
            state.output_json.setdefault("scholarly_methods_source_reviews", {})[key] = audit
            mapping_complete = True
            if key == "mapping" and len(packet["point"]["research_points"]) > 1:
                mapping_complete = all(any(point["name"] in row["text"] and
                    set(row.get("evidence_ids", [])) & set(point["representative_ids"])
                    for row in accepted.get(key, [])) for point in packet["point"]["research_points"]
                    if point["representative_ids"])
            if accepted.get(key) and not audit.get("failure_kind") and len(accepted[key]) == len(rows) and mapping_complete:
                saved[key] = accepted[key]
                pending.pop(key, None)
                completion[key] = {"status": "completed", "paragraph_count": len(accepted[key])}
            else:
                feedback = [*audit.get("feedback", []),
                    *([] if mapping_complete else [{"reason": "评估要点与代表文献未逐项对应，请逐一写明要点名称和对应文献"}])]
                completion.setdefault("feedback", {})[key] = feedback
                if audit.get("failure_kind") == "review_protocol":
                    completion[key] = {"status": "pending", "reason": "review_protocol"}
                    await graph._emit_stage_update(state)
                    return
                # A semantic/source rejection is recoverable locally. The
                # deterministic rows use only facts already present in the
                # frozen task state and therefore cannot invent literature,
                # counts, results, or unsupported claims.
                use_fallback(key, "source_review_rejected" if mapping_complete else "incomplete_mapping")
                if not saved.get(key):
                    # Preserve the candidate for a later report retry when a
                    # local fallback cannot form a factual section.
                    pending[key] = rows
            await graph._emit_stage_update(state)


def methods_complete(state):
    saved = state.output_json.get("scholarly_methods", {})
    return all(saved.get(key) for key in METHOD_KEYS)
