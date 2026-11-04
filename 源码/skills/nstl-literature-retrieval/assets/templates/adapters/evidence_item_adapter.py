"""证据条目适配器模板。

将 StandardPaper 转换为证据评估流程中使用的 EvidenceItem。
本适配器基于好问题智能体的检索适配器模式，专门面向"证据条目"场景。

核心职责：
  1. to_search_request()：将证据需求参数转换为公共 SearchRequest
  2. to_evidence_items()：将 StandardPaper 列表映射为 EvidenceItem 列表
  3. build_model_fallback_signal()：构建降级信号，供智能体模型层判断是否走降级路径

source_type 标记规范：
  - nstl_ 前缀 = 来自真实文献检索（如 nstl_solr, nstl_semantic, nstl_hybrid）
  - model_fallback = 模型降级生成（无真实文献支撑）
  - 其他前缀 = 来自其他数据源（缓存、知识库等）

扩展方式：
  1. 复制此文件并修改 source_agent 为你的智能体标识
  2. 在 EvidenceItem 中增加你需要的业务字段
  3. 在 to_evidence_items 中调整字段映射逻辑
  4. 在 build_model_fallback_signal 中调整降级阈值
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.common.retrieval.schemas import SearchRequest, SearchResponse, StandardPaper


# ---------------------------------------------------------------------------
# 证据条目数据模型
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    """证据条目 —— 将 StandardPaper 映射为证据评估流程所需的结构。

    # TODO: 根据业务需求自定义字段，以下为基础模板字段。
    """
    evidence_id: str = ""  # 证据唯一标识
    evidence_need_id: str = ""  # 所属证据需求ID
    source_type: str = ""  # 来源标记，如 nstl_solr、model_fallback
    title: str = ""  # 文献标题
    authors: list[str] = Field(default_factory=list)  # 作者列表
    year: int = 0  # 发表年份
    venue: str = ""  # 期刊/来源
    doi: str = ""  # DOI
    url: str = ""  # 文献链接（全文或详情页）
    nstl_url: str = ""  # NSTL 详情页链接
    language: str = "zh"  # 语种
    abstract: str = ""  # 摘要
    score: float = 0.0  # 综合评分
    retrieval_query: str = ""  # 本次检索使用的查询文本
    support_summary: str = ""  # 证据支持摘要（截取摘要前段）
    # TODO: 根据业务需求自定义额外字段，如 section_id, relevance_note 等


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
            # 此时 source_type 应标记为 model_fallback
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
# 证据条目适配器
# ---------------------------------------------------------------------------

class EvidenceItemAdapter:
    """证据条目适配器 —— 将 StandardPaper 转换为证据评估流程中的 EvidenceItem。

    基于 GoodQuestionSearchAdapter 的模式，专门面向"证据条目"场景。

    # TODO: 根据业务需求自定义
    #   1. 修改 source_agent 为你的智能体标识
    #   2. 在 to_search_request 中填入你的上下文字段
    #   3. 在 to_evidence_items 中调整字段映射逻辑
    """

    source_agent = "evidence_evaluator"  # TODO: 根据业务需求自定义

    NSTL_DETAIL_URL_PREFIX = "https://www.nstl.gov.cn/paper_detail.html?id="  # TODO: 根据业务需求自定义

    def to_search_request(
        self,
        *,
        query: str,
        paper_num: int = 20,
        year_range: tuple[int, int] | None = None,
        languages: list[str] | None = None,
        user_scope: str | None = None,
        request_id: str | None = None,
        evidence_need_id: str | None = None,
        # TODO: 根据业务需求自定义额外参数，如 topic, evidence_purpose 等
        **kwargs: Any,
    ) -> SearchRequest:
        """将证据需求参数转换为公共 SearchRequest。

        参数：
            query: 检索查询文本
            paper_num: 期望文献数量
            year_range: 年份范围
            languages: 语种列表
            user_scope: 用户权限范围
            request_id: 请求追踪ID
            evidence_need_id: 证据需求ID（用于追踪）
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
                "evidence_need_id": evidence_need_id or "",
                "topic": kwargs.get("topic", ""),
                "evidence_purpose": kwargs.get("evidence_purpose", ""),
            },
        )

    def to_evidence_items(
        self,
        *,
        papers: list[StandardPaper],
        evidence_need_id: str = "",
        evidence_group_id: str = "standard",
    ) -> list[EvidenceItem]:
        """将 StandardPaper 列表映射为 EvidenceItem 列表。

        source_type 标记规范：
          - nstl_{source} = 来自真实文献检索
          - 例如 nstl_solr、nstl_semantic、nstl_hybrid
          - 非 nstl_ 前缀表示其他来源（降级生成 model_fallback 等）

        nstl_url 构造：
          - 基于 paper.paper_id 构造 NSTL 详情页链接
          - 当 paper_id 为空时 nstl_url 也为空字符串

        参数：
            papers: StandardPaper 列表
            evidence_need_id: 证据需求ID
            evidence_group_id: 证据分组标识，用于生成 evidence_id
        """
        items: list[EvidenceItem] = []
        for paper_index, paper in enumerate(papers, start=1):
            nstl_detail_url = (
                f"{self.NSTL_DETAIL_URL_PREFIX}{paper.paper_id}"
                if paper.paper_id
                else ""
            )
            item = EvidenceItem(
                evidence_id=f"evi_{evidence_group_id}_{paper_index}",
                evidence_need_id=evidence_need_id,
                # 关键：nstl_ 前缀标识这是真实文献数据
                source_type=f"nstl_{paper.source}",
                title=paper.title,
                authors=paper.authors,
                year=self._normalize_year(paper.year),
                venue=paper.venue,
                doi=paper.doi,
                url=paper.url,
                nstl_url=nstl_detail_url,
                language=paper.language or "zh",
                abstract=paper.abstract,
                score=round(paper.final_score, 4),
                retrieval_query=paper.retrieval_query,
                # 截取摘要前200字作为支持摘要
                # TODO: 根据业务需求自定义截取长度或改写逻辑
                support_summary=paper.abstract[:200] if paper.abstract else "",
            )
            items.append(item)
        return items

    def _normalize_year(self, value: int) -> int:
        """将年份标准化到合理范围 [1900, 2100]。"""
        try:
            year = int(value)
        except (TypeError, ValueError):
            year = 2026  # TODO: 根据业务需求自定义默认年份
        return max(1900, min(year, 2100))