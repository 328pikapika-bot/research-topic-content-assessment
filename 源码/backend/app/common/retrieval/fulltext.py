"""Read-only client for retrieving relevant full-text chunks through the HTTP gateway."""

from __future__ import annotations

import math
import asyncio
import hashlib
import re
from collections import Counter
from typing import Any

import requests
from pydantic import BaseModel, Field
from .execution import retrieval_io

from app.core.config import LITERATURE_FULLTEXT_API_URL, LITERATURE_FULLTEXT_TIMEOUT
from app.core.config import LITERATURE_FULLTEXT_API_TOKEN


def normalized_identity_keys(chunk: "FulltextChunk") -> set[str]:
    paragraph = hashlib.sha256(re.sub(r"\s+", " ", chunk.text).strip().encode()).hexdigest()
    keys = {f"doc:{chunk.doc_id}:{paragraph}"} if chunk.doc_id else set()
    if chunk.doi:
        keys.add(f"doi:{chunk.doi.strip().casefold()}:{paragraph}")
    if chunk.title and chunk.year and chunk.authors:
        metadata = "|".join((chunk.title.casefold(), str(chunk.year), ",".join(sorted(author.casefold() for author in chunk.authors))))
        keys.add(f"meta:{metadata}:{paragraph}")
    return keys or {f"text:{paragraph}"}


class FulltextChunk(BaseModel):
    doc_id: str
    chunk_id: str
    text: str
    chunk_index: int | None = None
    chunk_type: str = ""
    section_path: str = ""
    char_start: int | None = None
    char_end: int | None = None
    token_count: int | None = None
    score: float = 0.0
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str = ""
    source_url: str = ""
    source: str = ""
    access_status: str = "unknown"
    locator_verified: bool = False
    offsets_consistent: bool = False
    source_hash: str = ""
    intent_id: str = ""
    query_variant: str = ""


class FulltextResponse(BaseModel):
    success: bool = False
    chunks: list[FulltextChunk] = Field(default_factory=list)
    error: str = ""
    retrieval_stats: dict[str, Any] = Field(default_factory=dict)


class FulltextIntent(BaseModel):
    intent_id: str
    query: str
    theme_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    year_range: list[int] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    query_variants: list[str] = Field(default_factory=list)
    fallback_queries: list[str] = Field(default_factory=list)


class LiteratureFulltextClient:
    """Calls a configured read-only gateway; collection details stay behind it."""

    def __init__(self, api_url: str | None = None, timeout: int | None = None, api_token: str | None = None) -> None:
        self.api_url = LITERATURE_FULLTEXT_API_URL if api_url is None else api_url
        self.timeout = timeout or LITERATURE_FULLTEXT_TIMEOUT
        self.api_token = LITERATURE_FULLTEXT_API_TOKEN if api_token is None else api_token

    def query_chunks(self, *, query: str, doc_ids: list[str] | None = None, top_k_per_doc: int = 3,
                     source_agent: str = "topic_selection", intent_id: str = "",
                     year_range: list[int] | None = None, languages: list[str] | None = None,
                     query_variant: str = "") -> FulltextResponse:
        clean_ids = list(dict.fromkeys(str(item).strip() for item in (doc_ids or []) if str(item).strip()))[:30]
        if doc_ids is not None and not clean_ids:
            return FulltextResponse(success=True)
        if not self.api_url:
            return FulltextResponse(error="未配置全文分块接口（LITERATURE_FULLTEXT_API_URL 为空）")
        per_doc_limit = max(1, min(3, top_k_per_doc))
        request_payload = {"query": query[:2000], "doc_ids": clean_ids, "top_k_per_doc": per_doc_limit}
        if doc_ids is None or intent_id or year_range or languages:
            request_payload.update({"top_k": 15, "intent_id": intent_id})
            if year_range:
                request_payload["year_range"] = year_range
            if languages:
                request_payload["languages"] = languages
        try:
            response = requests.post(
                self.api_url,
                json=request_payload,
                headers={"X-Source-Agent": source_agent, "X-Read-Only": "true",
                         **({"Authorization": "Bearer " + self.api_token} if self.api_token else {})},
                timeout=min(self.timeout, 15),
                proxies={"http": None, "https": None},
            )
            response.raise_for_status()
            payload = response.json()
            rows = self._rows(payload)
            chunks = []
            invalid = 0
            seen: set[str] = set()
            counts: Counter[str] = Counter()
            for item in rows[:90]:
                try:
                    parsed = self._parse(item) if isinstance(item, dict) else None
                    if parsed is not None and (not clean_ids or parsed.doc_id in clean_ids):
                        key = parsed.doc_id + ":" + hashlib.sha256(parsed.text.encode()).hexdigest()
                        if key in seen or counts[parsed.doc_id] >= per_doc_limit:
                            continue
                        parsed.intent_id = intent_id
                        parsed.query_variant = query_variant or query
                        chunks.append(parsed)
                        seen.add(key)
                        counts[parsed.doc_id] += 1
                    else:
                        invalid += 1
                except (ValueError, TypeError, OverflowError):
                    invalid += 1
            return FulltextResponse(
                success=True,
                chunks=chunks,
                error=f"忽略 {invalid} 个无效或未请求片段" if invalid else "",
                retrieval_stats={
                    "raw_candidates": len(rows),
                    "parsed_candidates": len(chunks),
                    "invalid_candidates": invalid,
                    "client_deduplicated_or_capped": max(0, len(rows) - invalid - len(chunks)),
                },
            )
        except (requests.RequestException, ValueError, TypeError):
            return FulltextResponse(error="全文查询暂不可用，本轮保留已有文献依据")

    async def search_intents(self, intents: list[FulltextIntent], *, source_agent: str) -> list[tuple[FulltextIntent, FulltextResponse]]:
        """The two agents share the same bounded, failure-isolated query path."""
        if not intents:
            return []
        if not self.api_url:
            return [(intent, FulltextResponse(error="全文查询服务尚未配置，本轮保留摘要依据")) for intent in intents]
        semaphore = asyncio.Semaphore(3)

        async def run(intent: FulltextIntent, query: str, variant_kind: str):
            async with semaphore:
                try:
                    response = await retrieval_io(self.query_chunks, query=query,
                        source_agent=source_agent, intent_id=intent.intent_id,
                        year_range=intent.year_range, languages=intent.languages,
                        query_variant=variant_kind)
                except Exception:
                    response = FulltextResponse(error="全文查询暂不可用，本轮保留摘要依据")
                return intent, query, variant_kind, response

        variants: list[tuple[FulltextIntent, str, str]] = []
        for intent in intents[:12]:
            queries = [(intent.query, "original")]
            queries.extend((item, "synonym") for item in intent.query_variants if item.strip() and item.strip() != intent.query.strip())
            seen_queries: set[str] = set()
            for query, kind in queries:
                normalized_query = re.sub(r"\s+", " ", query).strip().casefold()
                if normalized_query and normalized_query not in seen_queries:
                    variants.append((intent, query.strip(), kind))
                    seen_queries.add(normalized_query)
        tasks = [asyncio.create_task(run(intent, query, kind)) for intent, query, kind in variants]
        done, pending = await asyncio.wait(tasks, timeout=25)
        for task in pending:
            task.cancel()
        completed = [task.result() for task in tasks if task in done]
        completed.extend((intent, query, kind, FulltextResponse(error="全文查询超时，本轮保留已有依据"))
                         for (intent, query, kind), task in zip(variants, tasks) if task in pending)
        grouped: list[tuple[FulltextIntent, FulltextResponse]] = []
        for intent in intents[:12]:
            rows = [item for item in completed if item[0].intent_id == intent.intent_id]
            merged: list[FulltextChunk] = []
            seen: set[str] = set()
            totals: Counter[str] = Counter()
            errors: list[str] = []
            for _, _, _, response in rows:
                totals.update(response.retrieval_stats)
                if response.error and not response.success:
                    errors.append(response.error)
                for chunk in response.chunks:
                    identities = normalized_identity_keys(chunk)
                    if identities & seen:
                        totals["variant_duplicates"] += 1
                        continue
                    seen.update(identities)
                    merged.append(chunk)
            grouped.append((intent, FulltextResponse(
                success=any(item[3].success for item in rows),
                chunks=merged,
                error="；".join(dict.fromkeys(errors)),
                retrieval_stats={
                    **dict(totals),
                    "query_variant_count": len(rows),
                    "original_variant_count": sum(item[2] == "original" for item in rows),
                    "synonym_variant_count": sum(item[2] == "synonym" for item in rows),
                    "chinese_query_variant_count": sum(bool(re.search(r"[\u3400-\u9fff]", item[1])) for item in rows),
                    "english_query_variant_count": sum(bool(re.search(r"[A-Za-z]", item[1])) and not re.search(r"[\u3400-\u9fff]", item[1]) for item in rows),
                    "timeout_count": sum("超时" in item[3].error for item in rows),
                },
            )))
        return grouped

    @staticmethod
    def _rows(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            raise ValueError("全文分块响应不是 JSON 对象或数组")
        if payload.get("success") is False or payload.get("code") not in (None, 0, 1, 200):
            raise ValueError(str(payload.get("message") or payload.get("msg") or "全文分块服务返回失败"))
        data = payload.get("data", payload.get("chunks", payload.get("results", payload.get("items", []))))
        if isinstance(data, dict):
            data = data.get("chunks", data.get("results", data.get("items", [])))
        if not isinstance(data, list):
            raise ValueError("全文分块列表格式错误")
        return data

    @staticmethod
    def _parse(item: dict[str, Any]) -> FulltextChunk | None:
        def value(*keys: str, default: Any = None) -> Any:
            return next((item[key] for key in keys if key in item and item[key] is not None), default)

        doc_id = str(value("doc_id", "docId", "paper_id", "paperId", default="")).strip()
        text = str(value("text", "content", "chunk_text", default=""))
        chunk_id = str(value("chunk_id", "chunkId", "id", default="")).strip()
        if not doc_id or not text.strip() or not chunk_id:
            return None
        start = value("charStart", "char_start")
        end = value("charEnd", "char_end")
        index = value("chunkIndex", "chunk_index")
        token_count = value("tokenCount", "token_count")
        start = int(start) if start is not None else None
        end = int(end) if end is not None else None
        score = float(value("score", default=0.0) or 0.0)
        if not math.isfinite(score) or (start is not None and start < 0) or (end is not None and (end < 0 or (start is not None and end <= start))):
            return None
        authors = value("authors", "hasAuthorname", default=[])
        year_match = re.search(r"\b(?:19|20)\d{2}\b", str(value("year", default="")))
        return FulltextChunk(
            doc_id=doc_id, chunk_id=chunk_id, text=text,
            chunk_index=int(index) if index is not None else None,
            chunk_type=str(value("chunkType", "chunk_type", default="")),
            section_path=str(value("sectionPath", "section_path", default="")),
            char_start=start,
            char_end=end,
            token_count=int(token_count) if token_count is not None else None,
            score=score,
            title=str(value("title", default="")), authors=authors if isinstance(authors, list) else [str(authors)],
            year=int(year_match.group()) if year_match else None, doi=str(value("doi", default="")),
            source_url=str(value("source_url", default="")), source=str(value("source", default="")),
            access_status=str(value("access_status", default="unknown")),
            locator_verified=value("locator_verified", default=False) is True,
            offsets_consistent=value("offsets_consistent", default=False) is True,
            source_hash=str(value("source_hash", default="")),
        )
