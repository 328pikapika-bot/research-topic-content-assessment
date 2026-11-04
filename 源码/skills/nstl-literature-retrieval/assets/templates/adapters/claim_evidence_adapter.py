"""声明-证据适配器模板。

将 StandardPaper 转换为声明验证（claim verification）流程中的 ClaimEvidence。
本适配器面向"事实核查/声明验证"场景，将文献映射为支持/反对/中立某个声明的证据。

核心职责：
  1. to_search_request()：将声明验证需求转换为公共 SearchRequest
  2. to_claim_evidences()：将 StandardPaper 列表映射为 ClaimEvidence 列表
  3. build_model_fallback_signal()：构建降级信号，供智能体模型层判断是否走降级路径

source_type 标记规范：
  - nstl_ 前缀 = 来自真实文献检索（如 nstl_solr, nstl_semantic）
  - model_fallback = 模型降级生成（无真实文献支撑，立场判断可信度低）
  - 其他前缀 = 来自其他数据源

stance（立场）判定逻辑说明：
  - supporting：文献内容支持该声明
  - contradicting：文献内容反对/反驳该声明
  - neutral：文献内容与声明相关但立场不明确
  - 默认为 neutral，需要模型二次判断后更新

relevance_score 计算说明：
  - 综合检索得分、重排序得分和引用量计算
  - 仅为示例公式，实际应根据业务场景调整权重

扩展方式：
  1. 复制此文件并修改 source_agent 为你的智能体标识
  2. 在 ClaimEvidence 中增加你需要的业务字段
  3. 在 to_claim_evidences 中调整 stance 判定和 relevance_score 计算
  4. 在 build_model_fallback_signal 中调整降级阈值
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.common.retrieval.schemas import SearchRequest, SearchResponse, StandardPaper


# ---------------------------------------------------------------------------
# 声明-证据数据模型
# ---------------------------------------------------------------------------

class ClaimEvidence(BaseModel):
    """声明验证证据 —— 将 StandardPaper 映射为支持/反对/中立某个声明的证据结构。

    # TODO: 根据业务需求自定义字段，以下为基础模板字段。
    """
    evidence_id: str = ""  # 证据唯一标识
    claim_id: str = ""  # 所属声明ID
    stance: str = "neutral"  # 立场：supporting / contradicting / neutral
    source_type: str = ""  # 来源标记，如 nstl_solr、model_fallback
    title: str = ""  # 文献标题
    authors: list[str] = Field(default_factory=list)  # 作者列表
    year: int = 0  # 发表年份
    venue: str = ""  # 期刊/来源
    doi: str = ""  # DOI
    url: str = ""  # 文献链接
    abstract: str = ""  # 摘要
    relevance_score: float = 0.0  # 与声明的相关性评分（0-1）
    retrieval_query: str = ""  # 本次检索使用的查询文本
    stance_reason: str = ""  # 立场判定理由（模型二次判断后填入）
    # TODO: 根据业务需求自定义额外字段，如 confidence_level, evidence_excerpt 等


# ---------------------------------------------------------------------------
# 立场枚举常量
# ---------------------------------------------------------------------------

STANCE_SUPPORTING = "supporting"
STANCE_CONTRADICTING = "contradicting"
STANCE_NEUTRAL = "neutral"

VALID_STANCES = {STANCE_SUPPORTING, STANCE_CONTRADICTING, STANCE_NEUTRAL}


# ---------------------------------------------------------------------------
# 降级信号
# ---------------------------------------------------------------------------

class FallbackSignal(BaseModel):
    """降级信号，供智能体模型层判断是否需要走降级路径。

    当检索失败或返回文献不足时，通过此信号告知上游决策逻辑。
    声明验证场景中，降级意味着模型需要自行推断立场，可信度显著降低。
    """
    should_fallback: bool = False  # 是否需要降级
    reason: str = ""  # 降级原因
    paper_count: int = 0  # 实际获取的文献数
    degradation_level: int = 0  # 降级级别（0=正常, 4=完全降级）


def build_model_fallback_signal(response: SearchResponse) -> FallbackSignal:
    """根据检索响应构建降级信号。

    降级判定逻辑：
      - 检索失败（success=False）→ 完全降级（模型需自行生成立场判断）
      - 返回文献数为 0 → 完全降级
      - 返回文献数低于请求量的 30% → 部分降级
      - 其他 → 正常

    # TODO: 根据业务需求自定义降级阈值和判定逻辑。
    声明验证场景中降级影响更大，可适当放宽阈值要求更多文献支撑。

    参数：
        response: LiteratureSearchClient.search() 的返回值

    返回：
        FallbackSignal 降级信号对象

    使用示例：
        response = client.search(request)
        signal = build_model_fallback_signal(response)
        if signal.should_fallback:
            # 走降级路径，模型自行推断立场
            # 此时 ClaimEvidence.source_type 应标记为 model_fallback
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
            reason="检索返回0篇文献，无法验证声明",
            paper_count=0,
            degradation_level=trace.get("degradation_level", 4),
        )

    requested = trace.get("paper_num_requested", 0)
    # 声明验证场景建议至少需要3篇文献才可信
    # TODO: 根据业务需求自定义阈值，声明验证可能需要更多文献
    min_required = max(3, int(requested * 0.3))
    if paper_count < min_required:
        return FallbackSignal(
            should_fallback=True,
            reason=f"检索返回文献不足以验证声明: 请求{requested}篇，实际{paper_count}篇",
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
# 声明-证据适配器
# ---------------------------------------------------------------------------

class ClaimEvidenceAdapter:
    """声明-证据适配器 —— 将 StandardPaper 转换为声明验证流程中的 ClaimEvidence。

    # TODO: 根据业务需求自定义
    #   1. 修改 source_agent 为你的智能体标识
    #   2. 在 to_search_request 中填入声明相关的上下文字段
    #   3. 在 to_claim_evidences 中调整 relevance_score 计算和 stance 初始值
    #   4. 在 _compute_relevance_score 中调整评分权重公式
    """

    source_agent = "claim_verifier"  # TODO: 根据业务需求自定义

    def to_search_request(
        self,
        *,
        claim_text: str,
        paper_num: int = 20,
        year_range: tuple[int, int] | None = None,
        languages: list[str] | None = None,
        user_scope: str | None = None,
        request_id: str | None = None,
        claim_id: str | None = None,
        # TODO: 根据业务需求自定义额外参数，如 domain, expected_stance 等
        **kwargs: Any,
    ) -> SearchRequest:
        """将声明验证需求转换为公共 SearchRequest。

        参数：
            claim_text: 需要验证的声明文本，同时用作检索查询
            paper_num: 期望文献数量
            year_range: 年份范围
            languages: 语种列表
            user_scope: 用户权限范围
            request_id: 请求追踪ID
            claim_id: 声明ID（用于追踪）
            **kwargs: 额外业务参数，将放入 context 字典
        """
        return SearchRequest(
            query=claim_text,
            paper_num=paper_num,
            year_range=year_range,
            languages=languages or ["zh", "en"],
            rerank=True,
            user_scope=user_scope,
            request_id=request_id,
            source_agent=self.source_agent,
            context={
                # TODO: 根据业务需求自定义 context 字段
                "claim_id": claim_id or "",
                "claim_text": claim_text,
                "domain": kwargs.get("domain", ""),
                "expected_stance": kwargs.get("expected_stance", ""),
            },
        )

    def to_claim_evidences(
        self,
        *,
        papers: list[StandardPaper],
        claim_id: str = "",
        evidence_group_id: str = "claim",
    ) -> list[ClaimEvidence]:
        """将 StandardPaper 列表映射为 ClaimEvidence 列表。

        source_type 标记规范：
          - nstl_{source} = 来自真实文献检索
          - 例如 nstl_solr、nstl_semantic、nstl_hybrid
          - 非 nstl_ 前缀表示其他来源（降级生成 model_fallback 等）

        stance 初始值默认为 neutral，需要后续由模型二次判断后更新为
        supporting 或 contradicting。

        参数：
            papers: StandardPaper 列表
            claim_id: 声明ID
            evidence_group_id: 证据分组标识，用于生成 evidence_id
        """
        evidences: list[ClaimEvidence] = []
        for paper_index, paper in enumerate(papers, start=1):
            evidence = ClaimEvidence(
                evidence_id=f"claim_evi_{evidence_group_id}_{paper_index}",
                claim_id=claim_id,
                # 初始立场默认为 neutral，需模型二次判断更新
                stance=STANCE_NEUTRAL,  # TODO: 根据业务需求自定义初始立场判定逻辑
                # 关键：nstl_ 前缀标识这是真实文献数据
                source_type=f"nstl_{paper.source}",
                title=paper.title,
                authors=paper.authors,
                year=self._normalize_year(paper.year),
                venue=paper.venue,
                doi=paper.doi,
                url=paper.url,
                abstract=paper.abstract,
                relevance_score=self._compute_relevance_score(paper),
                retrieval_query=paper.retrieval_query,
                stance_reason="",  # 待模型二次判断后填入
            )
            evidences.append(evidence)
        return evidences

    def _compute_relevance_score(self, paper: StandardPaper) -> float:
        """计算文献与声明的相关性评分。

        示例公式：综合检索得分、重排序得分和引用量计算。
        最终归一化到 [0, 1] 区间。

        # TODO: 根据业务需求自定义评分权重和计算公式。
        当前权重：
          - final_score 权重 0.5（综合检索评分）
          - rerank_score 权重 0.3（重排序评分）
          - citation_count 权重 0.2（被引次数，经对数归一化）
        """
        # final_score 归一化（假设最高约 1.0）
        normalized_final = min(paper.final_score, 1.0) if paper.final_score > 0 else 0.0

        # rerank_score 归一化（假设最高约 1.0）
        normalized_rerank = (
            min(paper.rerank_score or 0.0, 1.0) if paper.rerank_score else 0.0
        )

        # citation_count 经对数归一化（假设最高约 100 次引用）
        import math  # TODO: 根据业务需求自定义引用量归一化方式
        normalized_citation = (
            math.log1p(paper.citation_count) / math.log1p(100)
            if paper.citation_count > 0
            else 0.0
        )

        # 加权求和
        score = (
            0.5 * normalized_final
            + 0.3 * normalized_rerank
            + 0.2 * normalized_citation  # TODO: 根据业务需求自定义权重
        )

        return round(min(score, 1.0), 4)

    def _normalize_year(self, value: int) -> int:
        """将年份标准化到合理范围 [1900, 2100]。"""
        try:
            year = int(value)
        except (TypeError, ValueError):
            year = 2026  # TODO: 根据业务需求自定义默认年份
        return max(1900, min(year, 2100))