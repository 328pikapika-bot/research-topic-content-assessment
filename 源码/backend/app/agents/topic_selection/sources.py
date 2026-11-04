"""智能选题的可审计来源适配器。

外部来源只接收已经脱敏的检索词和查询意图。适配器不读取上传文件、原始
研究描述或任务状态，从接口边界上防止它们被意外发送到联网服务或云端模型。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

import httpx

from app.agents.topic_selection.schemas import ExternalEvidence, ModelInsight, SourcePolicy, SourceQuery
from app.common.retrieval.client import LiteratureSearchClient
from app.common.retrieval.execution import retrieval_io
from app.common.retrieval.schemas import SearchRequest, StandardPaper
from app.services.deepseek_client import DeepSeekClient, DeepSeekClientError


def _now() -> str:
    return datetime.now().isoformat()


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


@dataclass
class SourceExecutionResult:
    success: bool
    source_kind: str
    papers: list[StandardPaper] = field(default_factory=list)
    external_evidence: list[ExternalEvidence] = field(default_factory=list)
    model_insights: list[ModelInsight] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class SourceAdapter(Protocol):
    kind: str

    async def query(
        self,
        query: SourceQuery,
        *,
        user_scope: str,
        policy: SourcePolicy,
    ) -> SourceExecutionResult: ...


class NSTLAdapter:
    kind = "nstl"

    def __init__(self, client: LiteratureSearchClient | None = None) -> None:
        self.client = client or LiteratureSearchClient()

    async def query(self, query: SourceQuery, *, user_scope: str, policy: SourcePolicy) -> SourceExecutionResult:
        if not policy.allow_nstl:
            return SourceExecutionResult(False, self.kind, error="NSTL 查询已按来源策略关闭")
        request = SearchRequest(
            query=query.sanitized_query or query.query,
            paper_num=20,
            year_range=tuple(query.time_range) if len(query.time_range) == 2 else None,
            languages=list(getattr(query, "languages", []) or []),
            user_scope=user_scope or None,
            source_agent="topic_selection",
            request_id=query.query_id,
            context={"source_query_id": query.query_id},
        )
        try:
            responses = await retrieval_io(self.client.search_multi_sync, [request], max_concurrency=1)
            response = responses[0] if responses else None
        except Exception as exc:  # pragma: no cover - defensive client boundary
            return SourceExecutionResult(False, self.kind, error=str(exc))
        if response is None:
            return SourceExecutionResult(False, self.kind, error="NSTL 未返回检索响应")
        return SourceExecutionResult(
            bool(response.success),
            self.kind,
            papers=list(response.papers),
            trace=dict(response.search_trace or {}),
            error=str(response.error or ""),
        )


WebSearchHandler = Callable[[str, SourceQuery], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]


class WebAdapter:
    """联网动态信息接口。

    生产环境可注入站内网关 handler；配置 ``TOPIC_SELECTION_WEB_SEARCH_URL`` 时
    使用一个简单的 JSON POST 协议。未配置时明确降级，不会尝试未知站点。
    """

    kind = "web"

    def __init__(self, handler: WebSearchHandler | None = None, *, endpoint: str | None = None) -> None:
        self.handler = handler
        self.endpoint = endpoint if endpoint is not None else os.getenv("TOPIC_SELECTION_WEB_SEARCH_URL", "").strip()

    async def query(self, query: SourceQuery, *, user_scope: str, policy: SourcePolicy) -> SourceExecutionResult:
        if query.status == "locked":
            raise RuntimeError("证据池已锁定，禁止联网查询")
        if not policy.allow_web:
            return SourceExecutionResult(False, self.kind, error="联网查询已按来源策略关闭")
        try:
            if self.handler is not None:
                data = self.handler(query.sanitized_query or query.query, query)
                items = await data if inspect.isawaitable(data) else data
            elif self.endpoint:
                timeout = httpx.Timeout(float(os.getenv("TOPIC_SELECTION_WEB_TIMEOUT_SECONDS", "12")))
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(self.endpoint, json={
                        "query": query.sanitized_query or query.query,
                        "purpose": query.purpose,
                        "time_range": query.time_range,
                        "limit": policy.max_web_results,
                    })
                    response.raise_for_status()
                    body = response.json()
                items = body.get("items", body) if isinstance(body, dict) else body
            else:
                return SourceExecutionResult(False, self.kind, error="未配置联网检索适配器")
        except Exception as exc:
            return SourceExecutionResult(False, self.kind, error=str(exc))
        if not isinstance(items, list):
            return SourceExecutionResult(False, self.kind, error="联网检索响应不是列表")
        now = _now()
        evidence: list[ExternalEvidence] = []
        for index, item in enumerate(items[:policy.max_web_results], start=1):
            if not isinstance(item, dict):
                continue
            normalized = {
                "title": str(item.get("title") or item.get("name") or "动态外部资料"),
                "url": str(item.get("url") or item.get("link") or ""),
                "source_name": str(item.get("source_name") or item.get("site") or "web"),
                "published_at": str(item.get("published_at") or item.get("date") or ""),
                "excerpt": str(item.get("excerpt") or item.get("summary") or item.get("content") or "")[:2000],
            }
            evidence.append(ExternalEvidence(
                evidence_id=f"WEB-{_hash([query.query_id, normalized, index])}",
                source_kind="web",
                query_id=query.query_id,
                fetched_at=now,
                locator=normalized["url"] or normalized["source_name"],
                hash=_hash(normalized),
                reliability=str(item.get("reliability") or "unverified") if str(item.get("reliability") or "unverified") in {"high", "medium", "low", "unverified"} else "unverified",
                citation_verified=bool(normalized["url"]),
                **normalized,
                raw={"source_item": item},
            ))
        return SourceExecutionResult(True, self.kind, external_evidence=evidence, trace={"endpoint_configured": bool(self.endpoint), "returned_count": len(evidence)})


class CloudModelAdapter:
    """云端模型只产生查询扩展和待验证洞见，不作为论文事实证据。"""

    kind = "cloud_model"

    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self.client = client

    async def query(self, query: SourceQuery, *, user_scope: str, policy: SourcePolicy) -> SourceExecutionResult:
        if query.status == "locked":
            raise RuntimeError("证据池已锁定，禁止调用云端模型")
        if not policy.allow_cloud_model:
            return SourceExecutionResult(False, self.kind, error="云端模型查询已按来源策略关闭")
        try:
            client = self.client or DeepSeekClient()
            payload = {
                "query": query.sanitized_query or query.query,
                "purpose": query.purpose,
                "time_range": query.time_range,
                "requirement": "仅提出术语扩展、问题变体、交叉方向或待验证近期线索；不得断言事实，不得输出未给出的内部信息。",
            }
            result = await client.generate_json(
                system_prompt="你是科研检索辅助器。只输出 JSON：insights 数组，每项有 type、text、confidence。",
                user_prompt=json.dumps(payload, ensure_ascii=False),
                max_tokens=1000,
                timeout_seconds=15,
                max_retries=0,
            )
        except (DeepSeekClientError, Exception) as exc:
            return SourceExecutionResult(False, self.kind, error=str(exc))
        insights: list[ModelInsight] = []
        items = result.get("insights", []) if isinstance(result, dict) else []
        now = _now()
        for index, item in enumerate(items[:policy.max_cloud_insights], start=1):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            insight_type = str(item.get("type") or "query_expansion")
            if insight_type not in {"term_expansion", "trend", "problem_variant", "cross_direction", "query_expansion"}:
                insight_type = "query_expansion"
            text = str(item["text"]).strip()[:1200]
            insights.append(ModelInsight(
                insight_id=f"MI-{_hash([query.query_id, text, index])}",
                query_id=query.query_id,
                model_name=str(getattr(client, "model", "cloud-model")),
                insight_type=insight_type,
                text=text,
                confidence=max(0.0, min(1.0, float(item.get("confidence", 0.4) or 0.4))),
                generated_at=now,
                response_hash=_hash(item),
            ))
        external = [ExternalEvidence(
            evidence_id=f"CLOUD-{insight.insight_id}", source_kind="cloud_model", title="云端模型检索洞见",
            source_name=insight.model_name, fetched_at=now, locator=insight.insight_id, excerpt=insight.text,
            summary=insight.text, query_id=query.query_id, hash=insight.response_hash, reliability="unverified",
            evidence_level="model_context", intended_use=["query_expansion", "hypothesis"], is_model_generated=True,
            raw={"insight_id": insight.insight_id, "insight_type": insight.insight_type},
        ) for insight in insights]
        return SourceExecutionResult(True, self.kind, external_evidence=external, model_insights=insights, trace={"returned_count": len(insights), "model": getattr(client, "model", "")})


def ensure_prelock_external_access(locked: bool, source_kind: str) -> None:
    if locked and source_kind in {"web", "cloud_model"}:
        raise RuntimeError("证据池已锁定，禁止联网或调用云端模型补充外部材料")
