"""Reader comparisons are separate from comparability diagnostics and audit data."""
from __future__ import annotations

import re

from .comparison_context import material_context, sentences


DIAGNOSTIC = re.compile(
    r"字段(?:覆盖|缺失)|关键词交集|全文(?:字符)?锚点|证据等级|[ABCD]级摘要|"
    r"(?:摘要|题录|证据)[^。！？]{0,45}(?:无法确认|不能确认|未涉及|未提及|未覆盖|不能支撑|无法支持)|"
    r"(?:无法|不能|尚不能)(?:确认|判断)(?:全文|证据|主张|方法|支持|矛盾|目标|比较)|"
    r"(?:无法|不能|尚不能)(?:直接)?(?:支撑|支持|比较)(?:该|本|当前|主张|目标|全文|性能高低)|"
    r"(?:文献|摘要|证据|来源)[^。！？]{0,24}(?:未涉及|未提及|未提供)|"
    r"(?:缺少|缺失|未提取到).{0,18}字段|"
    r"不具备[^。！？]{0,30}(?:比较|对照)[^。！？]{0,12}(?:基础|条件)|"
    r"(?:无法|不能)(?:开展|进行)(?:直接|实验|定量)?(?:比较|对照)"
)


def _normalized(value):
    return re.sub(r"\s+", "", str(value or ""))


def _material(state, claim):
    # Validate against actual point-related passages, not invented extracted tags
    # or a matching phrase somewhere in a different uploaded document.
    return [_normalized(row["text"]) for row in material_context(state, claim)["material_passages"]]


def _literature(state, claim, evidence):
    return [_normalized(text) for text in [evidence.abstract or "", evidence.excerpt or "", *[
        chunk.excerpt for chunk in state.evidence_chunks
        if chunk.evidence_id == evidence.evidence_id and (not chunk.claim_ids or claim.claim_id in chunk.claim_ids)
    ]] if text]


def _quotes(item, side):
    values = item.get(f"{side}_quotes", [])
    values = values if isinstance(values, list) else []
    single = item.get(f"{side}_quote")
    return list(dict.fromkeys(value.strip() for value in [single, *values]
                             if isinstance(value, str) and value.strip()))


def _quote_error(quotes, corpus):
    if not quotes:
        return "missing_source_anchor"
    if len(quotes) > 8 or any(len(_normalized(quote)) < 4
            or not any(_normalized(quote) in text for text in corpus) for quote in quotes):
        return "source_anchor_mismatch"
    return ""


def _anchor_error(item, side, corpus):
    supplied = item.get(f"{side}_quotes")
    if supplied is not None and (not isinstance(supplied, list)
            or any(not isinstance(value, str) or not value.strip() for value in supplied)):
        return "invalid_source_anchors"
    return _quote_error(_quotes(item, side), corpus)


def _numbers(text, source_ids=()):
    # Bibliographic IDs are labels, never experimental values (e.g. 016).
    # Only IDs actually associated with this paragraph may be excluded.
    for source_id in sorted(source_ids, key=len, reverse=True):
        text = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(source_id) + r"(?![A-Za-z0-9_-])", "", text)
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _source_titles(text, sources):
    for source_id, source in sorted(sources.items(), key=lambda item: -len(item[0])):
        text = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(source_id) + r"(?![A-Za-z0-9_-])",
                      lambda _: f"《{source.title}》", text)
    return text


def _text_error(text):
    if not text:
        return "missing_content"
    if len(text) > 1200:
        return "content_exceeds_budget"
    if DIAGNOSTIC.search(text):
        return "audit_diagnostic_in_prose"
    if re.fullmatch(r"(?:双方|两者|二者|材料与文献)?(?:的)?研究(?:对象|问题|方法|条件)(?:不同|有差异)[。；]?", text):
        return "generic_category_difference"
    return ""


def _grounding_error(item, material, literature, source_ids=()):
    if not isinstance(item, dict):
        return "invalid_structure"
    text = str(item.get("text") or "").strip()
    mq, lq = _quotes(item, "material"), _quotes(item, "literature")
    return (_text_error(text) or _anchor_error(item, "material", material) or _anchor_error(item, "literature", literature)
            or ("unsupported_number" if not _numbers(text, source_ids) <= _numbers(" ".join(mq + lq)) else ""))


def _comparison_semantics_error(text, key):
    # Genre and publication timing are not experimental comparison conditions.
    # Keep concrete scientific differences (including negative results) intact.
    if key == "conditions" and re.search(
            r"(?:性质|文体|目的|计划\s*vs\.?|规范\s*vs\.?)[^。！？]{0,80}(?:不同|差异|前提|条件)|"
            r"(?:标准化程度|适用性)[^。！？]{0,40}构成[^。！？]{0,12}比较|"
            r"(?:本|当前)?(?:材料|指南|项目)(?:是|属于)(?:一项)?(?:计划中|拟制定)?的?(?:规范|指南|项目)[，,；;]"
            r"(?:而)?(?:该)?文献(?:是|属于)(?:已发表的)?(?:工程方法论文|实验研究|方法论文|综述)", text, re.I):
        return "document_genre_is_not_comparison_condition"
    if key == "difference" and re.search(
            r"(?:本|当前)(?:材料|指南|项目)[^。！？]{0,20}可能(?:包含|涵盖|采用)|"
            r"(?:未|没有)(?:提出|验证)(?:或(?:提出|验证))?任何(?:新的)?(?:统一)?(?:标准|规范)", text):
        return "speculative_or_unrecorded_difference"
    return ""


def _generic_contact(text):
    # Detect an entire common-topic statement, not scientific negative words or
    # a substantive short explanation. No minimum word/sentence count applies.
    common = re.compile(r"^(?:两者|双方|二者|两项研究|这两项研究|材料与文献|两篇研究)"
                        r"(?:均|都|共同|也|皆|同样|既|不仅|的研究)?(?:关注|致力于|涉及|研究|聚焦|围绕|强调)")
    developed = re.compile(r"(?:材料|指南|项目|本研究|本要点).{0,35}(?:采用|规定|将|通过|设计|要求)|"
                           r"(?:文献|该研究|该综述).{0,35}(?:采用|讨论|提出|分析|验证|比较)|"
                           r"(?:因此|从而|具体体现|联系在于|联系落在|提供.{0,12}(?:依据|参考|基础))")
    parts = sentences(text)
    return bool(parts and all(common.search(part) and not developed.search(part) for part in parts))


def _connection_value_missing(text):
    common_ending = re.search(
        r"(?:双方|两者|二者)[^。！？]*(?:一致性|高度一致|具有关联性|(?:均为|均将|都将)[^。！？]+)[。；]?\s*$", text)
    concrete_value = re.search(
        r"提供[^。！？]{0,30}(?:依据|参考|基础|背景|思路)|(?:联系|关联)(?:具体)?(?:落在|在于|体现)|"
        r"(?:可借鉴|可供|有助于|衔接|启示|对照依据)", text)
    return bool(common_ending and not concrete_value)


def _check(state, claim_id, path, reason, *, repair=False):
    if not reason:
        return
    record = state.output_json.setdefault("reader_analysis_checks", {}).setdefault(claim_id, {"issues": []})
    issue = {"path": path, "reason": reason, "attempt": "repair" if repair else "initial"}
    if issue not in record["issues"]:
        record["issues"].append(issue)


def _stored_item(item, *, material_only=False):
    result = {"text": str(item["text"]).strip()}
    for side in ("material",) if material_only else ("material", "literature"):
        quotes = _quotes(item, side)
        result[f"{side}_quote"] = quotes[0]
        if len(quotes) > 1 or f"{side}_quotes" in item:
            result[f"{side}_quotes"] = quotes
    return result


def _repeated_contact(state, claim_id, evidence_id, text):
    normalized = _normalized(text).strip("。；")
    return any(_normalized(row.get("text")).strip("。；") == normalized
        for cid, records in state.output_json.get("reader_comparisons", {}).items()
        for eid, record in records.items() if (cid, eid) != (claim_id, evidence_id)
        for row in record.get("similarity", []))


def save_reader_comparison(state, claim, evidence, raw, *, merge=False):
    """Keep only separately generated prose anchored to BOTH actual inputs.

    Anchors establish provenance, not a claim that semantic review is infallible.
    Audit uncertainty is deliberately never used as reader comparison conditions.
    An empty accepted record is authoritative (no fallback to diagnostic prose).
    """
    value = raw.get("reader_comparison")
    if not isinstance(value, dict):
        _check(state, claim.claim_id, f"comparison:{evidence.evidence_id}", "missing_content", repair=merge)
        return
    material = _material(state, claim)
    literature = _literature(state, claim, evidence)
    old = state.output_json.get("reader_comparisons", {}).get(claim.claim_id, {}).get(evidence.evidence_id, {})
    record = dict(old) if merge else {"claim_id": claim.claim_id, "evidence_id": evidence.evidence_id}
    for key in ("similarity", "difference", "conditions"):
        if merge and record.get(key):
            continue
        accepted = []
        items = value.get(key, [])
        for item in items[:4] if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            error = _grounding_error(item, material, literature, (evidence.evidence_id, claim.claim_id))
            text = str(item.get("text") or "").strip()
            if not error:
                error = _comparison_semantics_error(text, key)
            if not error and key == "similarity":
                if _generic_contact(text):
                    error = "generic_common_topic"
                elif _connection_value_missing(text):
                    error = "connection_value_not_explained"
                elif _repeated_contact(state, claim.claim_id, evidence.evidence_id, text):
                    error = "repeated_contact_across_sources_or_points"
            if error:
                _check(state, claim.claim_id, f"{key}:{evidence.evidence_id}", error, repair=merge)
                continue
            if text not in {row["text"] for row in accepted}:
                stored = _stored_item(item)
                stored["text"] = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(evidence.evidence_id)
                    + r"(?![A-Za-z0-9_-])", "该文献", text)
                accepted.append(stored)
        if key == "similarity" and not accepted:
            _check(state, claim.claim_id, f"{key}:{evidence.evidence_id}", "missing_valid_contact", repair=merge)
        record[key] = accepted
    state.output_json.setdefault("reader_comparisons", {}).setdefault(claim.claim_id, {})[evidence.evidence_id] = record


def save_reader_analysis(state, claim, evidence, payload, *, merge=False):
    """Validate the point synthesis returned by the SAME comparison call.

    Each synthesis paragraph identifies its sources and includes both actual
    material and source anchors. It is never built from audit uncertainty.
    """
    raw = payload.get("reader_analysis")
    if not isinstance(raw, dict) or raw.get("claim_id") != claim.claim_id:
        _check(state, claim.claim_id, "reader_analysis", "missing_or_mismatched_point", repair=merge)
        return
    material = _material(state, claim)
    old = state.output_json.get("reader_research_analyses", {}).get(claim.claim_id, {})
    saved = dict(old) if merge and old else {"claim_id": claim.claim_id, "literature_summary": []}
    summary = raw.get("material_summary")
    if isinstance(summary, dict) and not (merge and saved.get("material_summary")):
        text = str(summary.get("text") or "").strip()
        quotes = _quotes(summary, "material")
        error = (_text_error(text) or _anchor_error(summary, "material", material)
                 or ("unsupported_number" if not _numbers(text) <= _numbers(" ".join(quotes)) else ""))
        if not error and claim.display_title and _normalized(text).strip("。") == _normalized(claim.display_title):
            error = "title_only_summary"
        if not error and _generic_contact(text):
            error = "generic_common_topic"
        _check(state, claim.claim_id, "material_summary", error, repair=merge)
        if not error:
            saved["material_summary"] = _stored_item(summary, material_only=True)
    sources = {e.evidence_id: e for e in evidence}
    rows = raw.get("literature_summary", [])
    for row in rows[:3] if isinstance(rows, list) and not (merge and saved.get("literature_summary")) else []:
        if not isinstance(row, dict) or not isinstance(row.get("anchors"), list):
            continue
        ids = row.get("evidence_ids", [])
        if not isinstance(ids, list) or not ids or any(eid not in sources for eid in ids):
            _check(state, claim.claim_id, "literature_summary", "unknown_or_missing_source", repair=merge)
            continue
        anchors = {a.get("evidence_id"): a for a in row["anchors"] if isinstance(a, dict)}
        if set(anchors) != set(ids):
            _check(state, claim.claim_id, "literature_summary", "source_anchor_mismatch", repair=merge)
            continue
        # Each source must have its own genuine anchor. Numeric claims are
        # checked against the combined anchors, rather than every paper.
        text = str(row.get("text") or "").strip()
        quotes = " ".join(quote for a in anchors.values() for side in ("material", "literature") for quote in _quotes(a, side))
        error = (_text_error(text) or ("unsupported_number" if not _numbers(text, ids) <= _numbers(quotes) else ""))
        if not error:
            for eid in ids:
                error = (_anchor_error(anchors[eid], "material", material)
                         or _anchor_error(anchors[eid], "literature", _literature(state, claim, sources[eid])))
                if error:
                    break
        if not error and _generic_contact(text):
            error = "generic_common_topic"
        per_source = state.output_json.get("reader_comparisons", {}).get(claim.claim_id, {})
        if not error and any(_normalized(text) == _normalized(item.get("text"))
                for record in per_source.values() for key in ("similarity", "difference", "conditions")
                for item in record.get(key, [])):
            error = "synthesis_duplicates_source_analysis"
        _check(state, claim.claim_id, "literature_summary", error, repair=merge)
        if error:
            continue
        if text not in {r["text"] for r in saved["literature_summary"]}:
            saved["literature_summary"].append({"text": _source_titles(text, {eid: sources[eid] for eid in ids}),
                "evidence_ids": list(dict.fromkeys(ids)), "anchors": row["anchors"]})
    state.output_json.setdefault("reader_research_analyses", {})[claim.claim_id] = saved


def reader_repair_targets(state, claim, evidence):
    """Only repair supported, missing reading units; never require empty conditions."""
    if not material_context(state, claim)["material_passages"] or not any(_literature(state, claim, e) for e in evidence):
        return []
    analysis = state.output_json.get("reader_research_analyses", {}).get(claim.claim_id, {})
    checks = state.output_json.get("reader_analysis_checks", {}).get(claim.claim_id, {}).get("issues", [])
    targets = []
    for key in ("material_summary", "literature_summary"):
        if not analysis.get(key):
            reasons = list(dict.fromkeys(issue["reason"] for issue in checks
                                         if issue["path"] in {key, "reader_analysis"})) or ["missing_content"]
            targets.append({"path": key, "reasons": reasons})
            _check(state, claim.claim_id, key, reasons[0])
    records = state.output_json.get("reader_comparisons", {}).get(claim.claim_id, {})
    for source in evidence:
        path = f"similarity:{source.evidence_id}"
        reasons = list(dict.fromkeys(issue["reason"] for issue in checks if issue["path"] in {path, f"comparison:{source.evidence_id}"}))
        if not records.get(source.evidence_id, {}).get("similarity") and _literature(state, claim, source):
            reasons = reasons or ["missing_content"]
            targets.append({"path": path, "evidence_id": source.evidence_id, "reasons": reasons})
            _check(state, claim.claim_id, path, reasons[0])
    return targets
