"""Bounded, verbatim reading context for one research point (no model calls)."""
from __future__ import annotations

import re

from .material_extraction import material_sources


_STOP = {"research", "study", "studies", "method", "methods", "analysis", "the", "and",
         "with", "from", "that", "this", "using", "for", "are", "was", "into", "of", "in",
         "研究", "方法", "技术", "材料", "项目", "论文", "指南", "采用", "进行", "分析"}
_TECHNICAL = re.compile(
    r"测量|检测|验证|对照|工艺|温度|流量|条件|结果|重复性|标准化|"
    r"measur|validat|compar|protocol|experiment|fabricat|impedance|reproduc|temperature|result",
    re.I,
)


def sentences(text: str) -> list[str]:
    # English decimals and abbreviations are not arbitrary character boundaries.
    return list(dict.fromkeys(part.strip() for part in re.split(
        r"(?<=[。！？；])|(?<=[.!?;])\s+(?=[A-Z])|\r?\n+", str(text or "")) if part.strip()))


def focus_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[a-z][a-z0-9-]{1,}", text.lower())) - _STOP
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(phrase[i:i + 2] for i in range(len(phrase) - 1)
                     if phrase[i:i + 2] not in _STOP)
    return terms


def point_focus(state, claim) -> str:
    queries = [q.query for q in state.query_plans if claim.claim_id in q.claim_ids]
    return " ".join([claim.text, claim.source_span or "", *claim.searchable_terms,
                     *claim.claim_fields.values(), *queries])


def selected_passages(text: str, focus: str, budget: int, *, introduction: bool = True) -> list[str]:
    """Select whole sentences across the source, then retain their source order."""
    if budget <= 0:
        return []
    units = sentences(text)
    terms = focus_terms(focus)
    scores = {i: sum(min(3, len(term)) for term in terms if term in unit.lower())
              + (2 if _TECHNICAL.search(unit) else 0) for i, unit in enumerate(units)}
    selected, used = [], 0
    if introduction and units and len(units[0]) < budget // 3:
        selected, used = [0], len(units[0])
    for index in sorted(range(len(units)), key=lambda i: (-scores[i], i)):
        if index in selected or scores[index] <= 0:
            continue
        if used + len(units[index]) + bool(selected) <= budget:
            selected.append(index)
            used += len(units[index]) + 1
    if not selected:
        # A source with no lexical match may still provide a short object definition.
        selected = [i for i, unit in enumerate(units) if len(unit) <= budget][:1]
    return [units[i] for i in sorted(selected)]


def material_context(state, claim) -> dict:
    """Keep the point's own passage first; select supporting original paragraphs."""
    focus = point_focus(state, claim)
    point_terms = focus_terms(claim.text + " " + " ".join(claim.searchable_terms))
    passages = []
    seen = set()
    used = 0
    sources = material_sources(state)
    sources.sort(key=lambda source: source.file_id != claim.source_document_id)
    # Extracted claim labels are useful for focus, but are not original sources.
    for text in (claim.source_span, claim.text):
        matched = next((source for source in sources if text and text in source.content), None)
        if matched and text not in seen:
            remaining = max(0, 2400 - used)
            original = [text] if len(text) <= remaining else selected_passages(text, focus, remaining, introduction=False)
            for passage in original:
                if passage in seen or any(passage in old for old in seen):
                    continue
                passages.append({"source_document_id": matched.file_id, "text": passage})
                used += len(passage)
                seen.add(passage)
    for source in sources:
        for text in selected_passages(source.content, focus, max(0, 2400 - used), introduction=False):
            if text in seen or any(text in old for old in seen):
                continue
            # A generic global goal or another point is not supporting context.
            if len({term for term in point_terms if term in text.lower()}) < 2:
                continue
            passages.append({"source_document_id": source.file_id, "text": text})
            seen.add(text)
            used += len(text)
    return {"artifact_type": state.report_preferences.resolved_artifact_type,
            "assertion_type": claim.assertion_type,
            "research_direction": state.nine_fields.research_direction,
            "material_passages": passages}


def evidence_context(state, claim, evidence) -> dict:
    """Fit specific methods later in an abstract into the existing excerpt budget."""
    focus = point_focus(state, claim)
    text = evidence.excerpt if evidence.fulltext_available else evidence.abstract or evidence.excerpt
    excerpt = "\n".join(selected_passages(text, focus, 1600 if evidence.fulltext_available else 1100))
    chunks = [chunk for chunk in state.evidence_chunks if chunk.evidence_id == evidence.evidence_id
              and claim.claim_id in chunk.claim_ids]
    # Select relevant chunks before applying the old maximum of three.
    terms = focus_terms(focus)
    chunks.sort(key=lambda chunk: -sum(term in chunk.excerpt.lower() for term in terms))
    return {"evidence_id": evidence.evidence_id, "title": evidence.title, "year": evidence.year,
            "excerpt": excerpt, "evidence_level": evidence.evidence_level,
            "paragraphs": [{"chunk_id": chunk.chunk_id,
                            "excerpt": "\n".join(selected_passages(chunk.excerpt, focus, 1600)),
                            "locator": chunk.locator} for chunk in chunks[:3]],
            "comparison_fields": evidence.comparison_fields,
            "excerpt_scope": "abstract" if evidence.section_ref.lower() == "abstract"
                else "fulltext" if evidence.anchor_verified else "abstract"}
