"""Shared source identity and paragraph relevance rules, independent of agents."""
from __future__ import annotations

import hashlib
import html
import re
from difflib import SequenceMatcher
from typing import Any

from app.common.retrieval.fulltext import FulltextChunk, FulltextIntent, FulltextResponse


def normalized(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]", "", html.unescape(value).casefold())


def normalized_doi(value: str) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip().casefold())


def match_source(chunk: FulltextChunk, records: list[Any]) -> tuple[Any | None, str]:
    """Only a unique, consistent identity may merge two source records."""
    candidates = [r for r in records if
        (chunk.doc_id and chunk.doc_id == r.paper_id)
        or (chunk.doi and normalized_doi(chunk.doi) == normalized_doi(r.doi))]
    if not candidates and chunk.title and chunk.year and chunk.authors:
        candidates = [r for r in records if normalized(chunk.title) == normalized(r.title)
            and chunk.year == r.year and {normalized(a) for a in chunk.authors} & {normalized(a) for a in r.authors}]
    # Reject contradictory DOI/title/year even when an ID happened to match.
    if len(candidates) != 1:
        return None, "ambiguous" if candidates else "independent"
    record = candidates[0]
    if ((chunk.doi and record.doi and normalized_doi(chunk.doi) != normalized_doi(record.doi))
        or (chunk.year and record.year and chunk.year != record.year)
        or (chunk.title and record.title and normalized(chunk.title) != normalized(record.title))):
        return None, "conflicting_identity"
    return record, "matched"


def content_tokens(text: str) -> set[str]:
    result = set(re.findall(r"[a-z][a-z0-9-]{2,}", text.casefold()))
    for word in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(word[i:i + 2] for i in range(len(word) - 1))
    return result - {"研究", "方法", "结果", "分析", "技术", "验证", "应用", "the", "and", "for", "with", "from", "that", "this", "study", "results", "method"}


def selected_chunks_with_stats(
    intent: FulltextIntent, response: FulltextResponse
) -> tuple[list[FulltextChunk], dict[str, int]]:
    stats = {
        "raw_candidates": int(response.retrieval_stats.get("raw_candidates", len(response.chunks))),
        "parsed_candidates": len(response.chunks),
        "invalid_source": int(response.retrieval_stats.get("invalid_candidates", 0)),
        "non_body": 0,
        "duplicate": int(response.retrieval_stats.get("client_deduplicated_or_capped", 0))
            + int(response.retrieval_stats.get("variant_duplicates", 0)),
        "below_relevance": 0,
        "out_of_range": 0,
        "coverage_cap": 0,
        "lexical_mismatch": 0,
        "accepted_chunks": 0,
        "covered_papers": 0,
        "timeout_count": int(response.retrieval_stats.get("timeout_count", 0)),
        "query_variant_count": int(response.retrieval_stats.get("query_variant_count", 1)),
        "original_variant_count": int(response.retrieval_stats.get("original_variant_count", 1)),
        "synonym_variant_count": int(response.retrieval_stats.get("synonym_variant_count", 0)),
        "chinese_query_variant_count": int(response.retrieval_stats.get("chinese_query_variant_count", 0)),
        "english_query_variant_count": int(response.retrieval_stats.get("english_query_variant_count", 0)),
    }
    if not response.success:
        return [], stats
    kept: list[FulltextChunk] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for chunk in sorted(response.chunks, key=lambda c: c.score, reverse=True):
        overlap = content_tokens(intent.query) & content_tokens(chunk.text)
        if len(overlap) < 2:
            stats["lexical_mismatch"] += 1
        provenance = (chunk.access_status == "authorized" and chunk.locator_verified
            and chunk.source_hash == hashlib.sha256(chunk.text.encode()).hexdigest())
        if not provenance or not chunk.title.strip() or not (chunk.doi or (chunk.year and chunk.authors)):
            stats["invalid_source"] += 1
            continue
        if chunk.score < 0.45:
            stats["below_relevance"] += 1
            continue
        if len(chunk.text.strip()) < 40 or chunk.chunk_type.casefold() in {"references", "bibliography", "reference", "toc", "abstract", "title_page"}:
            stats["non_body"] += 1
            continue
        if re.search(r"\b(?:references?|bibliography|contents|abstract|citation)\b|参考文献|目录|摘要", chunk.section_path, re.I):
            stats["non_body"] += 1
            continue
        if re.match(r"^\s*\d{1,3}\.\s+\S.+\b(?:19|20)\d{2}\b", chunk.section_path):
            stats["non_body"] += 1
            continue
        if len(re.findall(r"(?:^|\n)\s*\d{1,3}\.\s+\S", chunk.text)) >= 2:
            stats["non_body"] += 1
            continue
        if chunk.chunk_index is not None and chunk.chunk_index <= 2 and re.match(
            r"^\s*(?:article\s+)?" + re.escape(chunk.section_path.strip()), chunk.text, re.I
        ):
            stats["non_body"] += 1
            continue
        if len(chunk.text) < 600 and not re.search(r"[.!?。！？](?:\s|$)", chunk.text):
            stats["non_body"] += 1
            continue
        if any(c.doc_id == chunk.doc_id and SequenceMatcher(None, normalized(c.text), normalized(chunk.text), autojunk=False).ratio() > .88 for c in kept):
            stats["duplicate"] += 1
            continue
        if intent.year_range and chunk.year and not intent.year_range[0] <= chunk.year <= intent.year_range[-1]:
            stats["out_of_range"] += 1
            continue
        key = chunk.doc_id + ":" + normalized(chunk.text)
        if key in seen:
            stats["duplicate"] += 1
            continue
        if counts.get(chunk.doc_id, 0) >= 3 or (chunk.doc_id not in counts and len(counts) >= 5):
            stats["coverage_cap"] += 1
            continue
        counts[chunk.doc_id] = counts.get(chunk.doc_id, 0) + 1
        seen.add(key)
        kept.append(chunk)
    stats["accepted_chunks"] = len(kept)
    stats["covered_papers"] = len(counts)
    return kept, stats


def selected_chunks(intent: FulltextIntent, response: FulltextResponse) -> list[FulltextChunk]:
    """Compatibility wrapper for callers that do not need diagnostics."""
    return selected_chunks_with_stats(intent, response)[0]


def chunk_locator(chunk: FulltextChunk) -> str:
    section = chunk.section_path or "正文片段"
    # The stored offsets can be inconsistent with the indexed text. Preserve
    # a stable chunk locator instead of claiming an exact character match.
    if chunk.offsets_consistent and chunk.char_start is not None and chunk.char_end is not None:
        return f"{section} · 字符 {chunk.char_start}-{chunk.char_end}"
    return section + (f" · 第 {chunk.chunk_index + 1} 段" if chunk.chunk_index is not None else "")
