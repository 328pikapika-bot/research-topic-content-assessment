"""示例检索适配器模板。

公共检索客户端只返回 StandardPaper；本适配器负责把它映射成具体智能体
使用的业务对象。你可以复制此文件并修改为新适配器。

核心职责：
  1. to_search_request()：将业务参数转换为公共 SearchRequest
  2. to_business_items()：将 StandardPaper 列表转换为业务对象列表
  3. build_model_fallback_signal()：构建降级信号，供智能体模型层判断是否走降级路径

source_type 标记规范：
  - nstl_ 前缀 = 来自真实文献检索（如 nstl_solr, nstl_semantic）
  - 其他前缀 = 来自其他数据源或降级生成

扩展方式：
  1. 复制此文件为 your_agent.py
  2. 修改 source_agent 为你的智能体标识
  3. 替换 BusinessItem 为你的业务数据模型
  4. 在 to_search_request 中填入你的上下文字段
  5. 在 to_business_items 中完成字段映射
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.common.retrieval.schemas import SearchRequest, SearchResponse, StandardPaper


# ---------------------------------------------------------------------------
# 示例业务数据模型 —— 替换为你自己的业务模型
# ---------------------------------------------------------------------------

class BusinessItem(BaseModel):
    """示例业务对象 —— 将 StandardPaper 映射为你的智能体所需的结构。

    # TODO: 根据业务需求自定义字段，以下为示例字段。
    """
    item_id: str = ""  # 业务对象唯一标识
    source_type: str = ""  # 来源标记，如 nstl_solr、nstl_semantic
    title: str = ""  # 文献标题
    authors: list[str] = Field(default_factory=list)  # 作者列表
    year: int = 0  # 发表年份
    venue: str = ""  # 期刊/来源
    doi: str = ""  # DOI
    url: str = ""  # 链接
    language: str = "zh"  # 语种
    abstract: str = ""  # 摘要
    score: float = 0.0  # 综合评分
    extra: dict[str, Any] = Field(default_factory=dict)  # 扩展字段


# ---------------------------------------------------------------------------
# 降级信号
# ---------------------------------------------------------------------------

class FallbackSignal(BaseModel):
    """降级信号，供智能体模型层判断是否需要走降级路径。

    当检索失败或返回文献不足时，通过此信号告知上游决策逻辑。
    """
    should_fallback: bool = False  # 是否需要降级
    reason: str = ""  # 降级原因
    paper_count: int = 0  # 实际获取的文献数
    degradation_level: int = 0  # 降级级别（0=正常, 4=完全降级）


def build_model_fallback_signal(response: SearchResponse) -> FallbackSignal:
    """根据检索响应构建降级信号。

    降级判定逻辑：
      - 检索失败（success=False）→ 完全降级
      - 返回文献数为 0 → 完全降级
      - 返回文献数低于请求量的 30% → 部分降级
      - 其他 → 正常

    # TODO: 根据业务需求自定义降级阈值和判定逻辑。

    参数：
        response: LiteratureSearchClient.search() 的返回值

    返回：
        FallbackSignal 降级信号对象

    使用示例：
        response = client.search(request)
        signal = build_model_fallback_signal(response)
        if signal.should_fallback:
            # 走降级路径，如让模型自行生成内容
            ...
    """
    if not response.success:
        return FallbackSignal(
            should_fallback=True,
            reason=f"检索失败: {response.error or '未知错误'}",
            paper_count=0,
            degradation_level=4,
        )

    paper_count = len(response.papers)
    trace = response.search_trace

    if paper_count == 0:
        return FallbackSignal(
            should_fallback=True,
            reason="检索返回0篇文献",
            paper_count=0,
            degradation_level=trace.get("degradation_level", 4),
        )

    requested = trace.get("paper_num_requested", 0)
    # 返回文献不足请求量30%时部分降级
    threshold = max(1, int(requested * 0.3))  # TODO: 根据业务需求自定义阈值
    if paper_count < threshold:
        return FallbackSignal(
            should_fallback=True,
            reason=f"检索返回文献不足: 请求{requested}篇，实际{paper_count}篇",
            paper_count=paper_count,
            degradation_level=2,
        )

    return FallbackSignal(
        should_fallback=False,
        reason="",
        paper_count=paper_count,
        degradation_level=0,
    )


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------

class ExampleSearchAdapter:
    """示例检索适配器 —— 展示如何将 StandardPaper 转换为业务对象。

    # TODO: 根据业务需求自定义
    #   1. 修改 source_agent 为你的智能体标识
    #   2. 修改 to_search_request 中的 context 字段
    #   3. 修改 to_business_items 中的字段映射逻辑
    """

    source_agent = "example_agent"  # TODO: 根据业务需求自定义

    def to_search_request(
        self,
        *,
        query: str,
        paper_num: int = 20,
        year_range: tuple[int, int] | None = None,
        languages: list[str] | None = None,
        user_scope: str | None = None,
        request_id: str | None = None,
        # TODO: 根据业务需求自定义额外参数，如 topic, section_id 等
        **kwargs: Any,
    ) -> SearchRequest:
        """将业务参数转换为公共 SearchRequest。

        参数：
            query: 检索查询文本
            paper_num: 期望文献数量
            year_range: 年份范围
            languages: 语种列表
            user_scope: 用户权限范围
            request_id: 请求追踪ID
            **kwargs: 额外业务参数，将放入 context 字典
        """
        return SearchRequest(
            query=query,
            paper_num=paper_num,
            year_range=year_range,
            languages=languages or ["zh", "en"],
            rerank=True,
            user_scope=user_scope,
            request_id=request_id,
            source_agent=self.source_agent,
            context={
                # TODO: 根据业务需求自定义 context 字段
                # 这些字段会被 QueryBuilder 用于丰富查询
                "topic": kwargs.get("topic", ""),
                "evidence_purpose": kwargs.get("evidence_purpose", ""),
            },
        )

    def to_business_items(
        self,
        *,
        papers: list[StandardPaper],
        group_id: str = "",
    ) -> list[BusinessItem]:
        """将 StandardPaper 列表映射为业务对象列表。

        source_type 标记规范：
          - nstl_{source} = 来自真实文献检索
          - 例如 nstl_solr、nstl_semantic、nstl_hybrid
          - 非 nstl_ 前缀表示其他来源（降级生成、缓存等）

        参数：
            papers: StandardPaper 列表
            group_id: 分组标识，用于生成 item_id
        """
        items: list[BusinessItem] = []
        for paper_index, paper in enumerate(papers, start=1):
            item = BusinessItem(
                item_id=f"item_{group_id}_{paper_index}",
                # 关键：nstl_ 前缀标识这是真实文献数据
                source_type=f"nstl_{paper.source}",
                title=paper.title,
                authors=paper.authors,
                year=self._normalize_year(paper.year),
                venue=paper.venue,
                doi=paper.doi,
                url=paper.url,
                language=paper.language or "zh",
                abstract=paper.abstract,
                score=round(paper.final_score, 4),
                extra={
                    "paper_id": paper.paper_id,
                    "doc_type": paper.doc_type,
                    "index_level": paper.index_level,
                    "has_fulltext": paper.has_fulltext,
                    "retrieval_query": paper.retrieval_query,
                    "rerank_score": paper.rerank_score,
                    "citation_count": paper.citation_count,
                },
            )
            items.append(item)
        return items

    def _normalize_year(self, value: int) -> int:
        """将年份标准化到合理范围 [1900, 2100]。"""
        try:
            year = int(value)
        except (TypeError, ValueError):
            year = 0
        return max(1900, min(year, 2100))
