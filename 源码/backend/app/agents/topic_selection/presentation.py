"""Reader-facing report adaptation; never change stored evidence or decisions."""
from __future__ import annotations

import re
from typing import Any


def reader_text(value: str) -> str:
    text = re.sub(r"(?:[，；、·]\s*)?全文覆盖\s*[：:]?\s*\d+\s*篇(?:\s*(?:[（(]\s*\d+\s*段[）)]|[、，·]?\s*\d+\s*段))?", "", value)
    text = re.sub(r"摘要\s*\d+\s*篇?\s*[、，·]\s*全文\s*\d+\s*篇?", "检索样本", text)
    text = re.sub(r"(?:[，；、·]\s*)?全文\s*\d+\s*(?:篇|段)(?:\s*[（(]\s*\d+\s*段[）)])?", "", text)
    text = re.sub(r"，其中\s*\d+\s*篇取得可定位全文片段", "", text)
    text = text.replace("；全文不可用的记录明确保留为摘要或元数据证据", "；相关判断以可核验内容为限")
    text = re.sub(r"[，；、]\s*([。；])", r"\1", text)
    text = re.sub(r"^(?:[：:，；、。]\s*)+$", "", text)
    text = text.replace("全文不可用", "当前依据有限")
    text = text.replace("待补证据的探索方向", "探索方向").replace("待补证据方向", "探索方向").replace("待补证据", "探索方向")
    return text.strip()


def reader_report(value: Any) -> Any:
    """Normalize prose in a copy, leaving citations and source records untouched."""
    if isinstance(value, str):
        return reader_text(value)
    if isinstance(value, list):
        return [normalized for item in value if (normalized := reader_report(item)) != ""]
    if isinstance(value, dict):
        if value.get("kind") == "reference_list" or value.get("key") == "references":
            return value
        preserved = {"citation_links", "citation_map", "title", "url", "nstl_url", "source_url", "doi", "paper_id", "evidence_id"}
        return {key: item if key in preserved else reader_report(item) for key, item in value.items() if key not in {"全文覆盖", "fulltext_count", "fulltext_chunk_count"}}
    return value


def reader_prose(value: Any) -> str:
    text = reader_text(str(value or ""))
    text = re.sub(r"\b(?:E-[A-Za-z0-9_-]+|M-INPUT|[TG]\d+)\b", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    text = re.sub(r"\s+([，。；、])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ，；、")


def candidate_reader_prose(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?:关联)?(?:研究)?主题\s*[TG]\d+(?:\s*[、,，]\s*[TG]\d+)*", "", text)
    text = re.sub(r"空白\s*G\d+(?:\s*[、,，]\s*G\d+)*", "", text)
    text = reader_prose(text)
    diagnostics = re.compile(r"尚未证明|尚不证明候选假设|摘要未提及|摘要或元数据|仅有摘要|全文不可用|证据范围|全文摘录|当前仅有")
    parts = re.split(r"(?<=[。；;！？!?])|(?=尚未证明|尚不证明候选假设|摘要未提及|摘要或元数据|仅有摘要|全文不可用|证据范围|全文摘录|当前仅有)", text)
    text = "".join(part for part in parts if part.strip() and not diagnostics.search(part))
    text = re.sub(r"\s+([，。；、])", r"\1", text)
    text = re.sub(r"[，；、]{2,}", "；", text)
    return text.strip(" ，；、")


def normalize_candidate_reader_content(
    value: Any,
    *,
    allowed_evidence_ids: set[str],
    substantive_evidence_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    cited_evidence_ids = allowed_evidence_ids & substantive_evidence_ids

    def entry(raw: Any, allowed: set[str], *, require_source: bool = False) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {"text": raw}
        text = candidate_reader_prose(raw.get("text"))
        refs = list(dict.fromkeys(
            str(item) for item in raw.get("evidence_ids", [])
            if str(item) in allowed
        ))
        if require_source and not refs:
            text = ""
        return {"text": text, "evidence_ids": refs} if text else {"text": "", "evidence_ids": []}

    raw_content = value.get("research_content") if isinstance(value.get("research_content"), dict) else {}
    research_content = {
        key: entry(raw_content.get(key), cited_evidence_ids)
        for key in ("research_object", "core_question", "method_route")
    }
    normalized = {
        "reason": entry(value.get("reason"), cited_evidence_ids),
        "research_content": research_content,
        "evidence_summary": entry(value.get("evidence_summary"), cited_evidence_ids, require_source=True),
    }
    return normalized


def candidate_reader_report_fields(candidate: Any, citation_map: dict[str, int]) -> list[dict[str, str]]:
    content = getattr(candidate, "reader_content", {}) or {}
    if not isinstance(content, dict):
        return []

    candidate_ids = set(getattr(candidate, "evidence_ids", []) or [])
    evidence_links = getattr(candidate, "evidence_links", []) or []
    linked_ids = {
        str(getattr(link, "evidence_id", ""))
        for link in evidence_links
        if getattr(link, "evidence_id", None)
    }
    substantive_link_ids = {
        str(getattr(link, "evidence_id", ""))
        for link in evidence_links
        if getattr(link, "relation", "") in {"supports", "contradicts"}
        and getattr(link, "evidence_id", None)
    }
    associated_ids = substantive_link_ids if evidence_links else candidate_ids | linked_ids

    def cited(entry: Any, *, require_source: bool = False) -> str:
        if not isinstance(entry, dict):
            return ""
        text = candidate_reader_prose(entry.get("text"))
        refs = list(dict.fromkeys(
            f"[{citation_map[evidence_id]}]"
            for evidence_id in entry.get("evidence_ids", [])
            if evidence_id in associated_ids and evidence_id in citation_map
        ))
        if require_source and not refs:
            return ""
        return (text + (" " + " ".join(refs) if refs else "")).strip()

    reason = cited(content.get("reason")) or candidate_reader_prose(
        getattr(candidate, "innovation", "") or getattr(candidate, "distinctiveness_rationale", "")
    )
    evidence_summary = cited(content.get("evidence_summary"), require_source=True)
    research = content.get("research_content") if isinstance(content.get("research_content"), dict) else {}
    labels = {"research_object": "研究对象", "core_question": "核心问题", "method_route": "方法路线"}
    fallbacks = {
        "research_object": getattr(candidate, "research_object", ""),
        "core_question": getattr(candidate, "research_question", "") or getattr(candidate, "hypothesis", ""),
        "method_route": getattr(candidate, "method_route", ""),
    }
    research_lines = [
        f"{label}：{value}"
        for key, label in labels.items()
        if (value := cited(research.get(key)) or candidate_reader_prose(fallbacks[key]))
    ]
    fields = []
    if reason:
        fields.append({"label": "选题理由", "value": reason})
    if research_lines:
        fields.append({"label": "研究内容", "value": "\n".join(research_lines)})
    if evidence_summary:
        fields.append({"label": "文献依据", "value": evidence_summary})
    return fields


def opportunity_reader_type(link: Any) -> str:
    support_ids = set(getattr(link, "support_evidence_ids", []) or [])
    disciplines = {
        re.sub(r"\s+", "", str(getattr(item, "discipline", ""))).casefold()
        for item in (getattr(link, "participating_disciplines", []) or [])
        if getattr(item, "evidence_ids", None)
        and set(item.evidence_ids) & support_ids
        and str(getattr(item, "discipline", "")).strip()
    }
    return "交叉研究方向" if len(disciplines) > 1 else "研究切入点"
