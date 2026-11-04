"""平台级统一文献检索客户端模板。

封装对统一混合检索 HTTP 网关的调用。该客户端只负责真实检索、
响应标准化和过程追踪，不负责生成业务降级线索或智能体最终答案。

使用方式：
  1. 配置环境变量 LITERATURE_SEARCH_API_URL（必填）和 LITERATURE_SEARCH_TIMEOUT（可选，默认30秒）
  2. 创建客户端实例：client = LiteratureSearchClient()
  3. 调用检索：response = client.search_sync(query="检索词", paper_num=20)

环境变量：
  - LITERATURE_SEARCH_API_URL: 检索网关地址（必填，如 http://internal-api/search）
  - LITERATURE_SEARCH_TIMEOUT: 请求超时秒数（可选，默认30）

注意：
  - 代理绕过：proxies={"http": None, "https": None} 确保内网请求不经过系统代理
  - 请求头：自动携带 X-Request-Id、X-Source-Agent、X-User-Scope
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from uuid import uuid4

import requests

from app.common.retrieval.parser import SearchResultParser
from app.common.retrieval.query_builder import QueryBuilder
from app.common.retrieval.schemas import (
    LITERATURE_SEARCH_API_URL,
    LITERATURE_SEARCH_TIMEOUT,
    SearchRequest,
    SearchResponse,
    SearchTrace,
)

logger = logging.getLogger(__name__)


class LiteratureSearchClient:
    """统一文献检索客户端。

    对外输出 StandardPaper，供各智能体 Adapter 映射为业务结构。
    所有异常在内部兜底，绝不向外抛出 —— 调用方只需检查 response.success。
    """

    DEFAULT_API_URL: str = LITERATURE_SEARCH_API_URL
    DEFAULT_TIMEOUT: int = LITERATURE_SEARCH_TIMEOUT

    def __init__(
        self,
        api_url: str | None = None,
        timeout: int | None = None,
        *,
        query_builder: QueryBuilder | None = None,
        parser: SearchResultParser | None = None,
    ) -> None:
        self.api_url = api_url or self.DEFAULT_API_URL
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.query_builder = query_builder or QueryBuilder()
        self.parser = parser or SearchResultParser()

    # ------------------------------------------------------------------
    # 核心检索方法
    # ------------------------------------------------------------------

    def search(self, request: SearchRequest) -> SearchResponse:
        """执行一次真实文献检索。

        流程：
          1. 构建追踪信息 SearchTrace
          2. 检查 api_url 是否配置
          3. QueryBuilder 构造 question 字符串
          4. 发送 HTTP POST 到检索网关
          5. 解析响应，转换为 StandardPaper 列表
          6. 所有异常兜底，返回失败 SearchResponse
        """
        request_id = request.request_id or str(uuid4())
        trace = SearchTrace(
            request_id=request_id,
            source_agent=request.source_agent,
            query=request.query,
            api_url_configured=bool(self.api_url),
            paper_num_requested=min(max(request.paper_num, 0), 100),
            rerank_enabled=request.rerank,
            intent_classification_enabled=request.intent_classification,
        )
        t0 = time.perf_counter()

        if not self.api_url:
            trace.errors.append("未配置统一文献检索网关地址")
            trace.degradation_level = 4
            return self._response_from_trace(
                request=request,
                trace=trace,
                success=False,
                error="未配置统一文献检索网关地址（LITERATURE_SEARCH_API_URL 为空）",
                started_at=t0,
            )

        question = self.query_builder.build_question(request)
        trace.question = question
        payload: dict[str, Any] = {
            "question": question,
            "paperNum": trace.paper_num_requested,
            "rerank": request.rerank,
            "intent_classification": request.intent_classification,
            "solr_type": request.solr_type,
            "log_params": {
                "id": request.source_agent or "unknown_agent",
                "request_id": request_id,
            },
        }
        headers = self._build_headers(request, request_id)

        try:
            logger.info(
                "统一文献检索请求: agent=%s query=%.80s paperNum=%d",
                request.source_agent or "unknown_agent",
                request.query,
                trace.paper_num_requested,
            )
            # 注意：proxies={"http": None, "https": None} 绕过系统代理，
            # 避免内网请求被 Windows 系统代理拦截导致连接失败
            response = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
                proxies={"http": None, "https": None},
            )
            trace.gateway_status_code = response.status_code
            response.raise_for_status()

            data = response.json()
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                raise ValueError("检索服务响应不是 JSON 对象")

            trace.gateway_code = data.get("code")
            trace.gateway_msg = str(data.get("msg", ""))
            if data.get("code") != 1:
                error_msg = trace.gateway_msg or "未知错误"
                trace.errors.append(error_msg)
                trace.degradation_level = 4
                return self._response_from_trace(
                    request=request,
                    trace=trace,
                    success=False,
                    error=f"检索服务返回错误: {error_msg}",
                    started_at=t0,
                )

            papers = self.parser.parse(data, request.query)
            trace.paper_num_returned = len(papers)
            logger.info("统一文献检索成功: %d 篇文献", len(papers))
            return self._response_from_trace(
                request=request,
                trace=trace,
                success=True,
                papers=papers,
                started_at=t0,
            )
        except requests.Timeout:
            trace.errors.append("检索服务响应超时")
            trace.degradation_level = 4
            return self._response_from_trace(
                request=request,
                trace=trace,
                success=False,
                error="检索服务响应超时",
                started_at=t0,
            )
        except requests.ConnectionError as exc:
            trace.errors.append("检索服务网络不可达")
            trace.degradation_level = 4
            logger.warning("统一文献检索网络不可达: %s", exc)
            return self._response_from_trace(
                request=request,
                trace=trace,
                success=False,
                error="检索服务网络不可达，请检查 VPN 或内网连接",
                started_at=t0,
            )
        except requests.RequestException as exc:
            trace.errors.append(str(exc))
            trace.degradation_level = 4
            return self._response_from_trace(
                request=request,
                trace=trace,
                success=False,
                error=f"检索服务请求异常: {exc}",
                started_at=t0,
            )
        except Exception as exc:
            trace.errors.append(str(exc))
            trace.degradation_level = 4
            logger.error("统一文献检索未知错误: %s", exc, exc_info=True)
            return self._response_from_trace(
                request=request,
                trace=trace,
                success=False,
                error=f"检索过程异常: {exc}",
                started_at=t0,
            )

    # ------------------------------------------------------------------
    # 便捷入口
    # ------------------------------------------------------------------

    def search_sync(
        self,
        query: str,
        *,
        paper_num: int = 20,
        year_range: tuple[int, int] | None = None,
        languages: list[str] | None = None,
        rerank: bool = True,
        user_scope: str | None = None,
        source_agent: str = "",
        request_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> SearchResponse:
        """兼容旧调用方式的同步检索入口。

        示例：
            response = client.search_sync(
                query="大语言模型幻觉",
                paper_num=20,
                year_range=(2020, 2026),
                source_agent="my_agent",
            )
        """
        return self.search(
            SearchRequest(
                query=query,
                paper_num=paper_num,
                year_range=year_range,
                languages=languages or ["zh", "en"],
                rerank=rerank,
                user_scope=user_scope,
                source_agent=source_agent,
                request_id=request_id,
                context=context or {},
            )
        )

    def search_multi_sync(
        self,
        requests_: list[SearchRequest],
        *,
        max_concurrency: int = 5,
    ) -> list[SearchResponse]:
        """批量执行检索，保持返回顺序与请求顺序一致。

        使用 ThreadPoolExecutor 并行执行多个检索请求，
        单个请求异常不影响其他请求。

        参数：
            requests_: 检索请求列表
            max_concurrency: 最大并发数（默认5）
        """
        if not requests_:
            return []
        workers = max(1, min(max_concurrency, len(requests_)))
        responses: list[SearchResponse | None] = [None] * len(requests_)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self.search, request): index
                for index, request in enumerate(requests_)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    responses[index] = future.result()
                except Exception as exc:
                    # 理论上 search 已兜底，这里防止线程级异常打断整批检索。
                    request = requests_[index]
                    trace = SearchTrace(
                        request_id=request.request_id or str(uuid4()),
                        source_agent=request.source_agent,
                        query=request.query,
                        api_url_configured=bool(self.api_url),
                        paper_num_requested=request.paper_num,
                        errors=[str(exc)],
                        degradation_level=4,
                    )
                    responses[index] = SearchResponse(
                        success=False,
                        query=request.query,
                        error=f"批量检索子任务异常: {exc}",
                        search_trace=trace.model_dump(),
                    )
        return [response for response in responses if response is not None]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_headers(self, request: SearchRequest, request_id: str) -> dict[str, str]:
        """构造请求头。

        标准请求头：
          - X-Request-Id: 请求追踪ID
          - X-Source-Agent: 调用方智能体标识
          - X-User-Scope: 用户权限范围（可选）
        """
        headers = {
            "X-Request-Id": request_id,
            "X-Source-Agent": request.source_agent or "unknown_agent",
        }
        if request.user_scope:
            headers["X-User-Scope"] = request.user_scope
        # TODO: 根据业务需求自定义额外的请求头，如认证 Token 等
        return headers

    def _response_from_trace(
        self,
        *,
        request: SearchRequest,
        trace: SearchTrace,
        success: bool,
        started_at: float,
        papers: list | None = None,
        error: str | None = None,
    ) -> SearchResponse:
        """从追踪信息构建统一响应。"""
        duration_ms = (time.perf_counter() - started_at) * 1000
        trace.duration_ms = duration_ms
        papers = papers or []
        if papers and not trace.paper_num_returned:
            trace.paper_num_returned = len(papers)
        return SearchResponse(
            success=success,
            total=len(papers),
            papers=papers,
            query=request.query,
            question=trace.question,
            error=error,
            duration_ms=duration_ms,
            search_trace=trace.model_dump(),
        )
