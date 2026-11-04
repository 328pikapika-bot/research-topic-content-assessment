"""通用文献适配器模板。

将 StandardPaper 转换为前端展示用的 PaperCard 数据。
本适配器面向"文献卡片展示"场景，映射为前端友好的文献展示结构。

核心职责：
  1. to_search_request()：将展示需求转换为公共 SearchRequest
  2. to_paper_cards()：将 StandardPaper 列表映射为 PaperCard 列表
  3. build_model_fallback_signal()：构建降级信号，供智能体模型层判断是否走降级路径

source_type 标记规范：
  - nstl_ 前缀 = 来自真实文献检索（如 nstl_solr, nstl_semantic）
  - model_fallback = 模型降级生成（无真实文献支撑，展示时需标注）
  - 其他前缀 = 来自其他数据源

venue 清洗说明：
  - 检索返回的 venue 字段可能包含多余空格、标点不一致等问题
  - 本适配器提供基础清洗逻辑，可根据业务需求扩展

作者格式化说明：
  - StandardPaper.authors 为列表格式 ["张三", "李四"]
  - 前端展示通常需要格式化为 "张三, 李四" 或 "张三 et al."（超过阈值时）
  - 本适配器提供两种格式化方式

扩展方式：
  1. 复制此文件并修改 source_agent 为你的智能体标识
  2. 在 PaperCard 中增加你需要的展示字段
  3. 在 to_paper_cards 中调整 venue 清洗和作者格式化逻辑
  4. 在 build_model_fallback_signal 中调整降级阈值
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.common.retrieval.schemas import SearchRequest, SearchResponse, StandardPaper


# ---------------------------------------------------------------------------
# 文献卡片数据模型
# ---------------------------------------------------------------------------

class PaperCard(BaseModel):
    """文献卡片 —— 将 StandardPaper 映射为前端友好的文献展示结构。

    # TODO: 根据业务需求自定义字段，以下为基础模板字段。
    """
    card_id: str = ""  # 卡片唯一标识
    source_type: str = ""  # 来源标记，如 nstl_solr、model_fallback
    title: str = ""  # 文献标题
    authors_display: str = ""  # 格式化后的作者展示文本（如 "张三, 李四, 王五 et al."）
    year: int = 0  # 发表年份
    venue_display: str = ""  # 清洗后的期刊/来源展示文本
    doi: str = ""  # DOI
    url: str = ""  # 文献链接
    language: str = "zh"  # 语种
    abstract_display: str = ""  # 截断后的摘要展示文本
    doc_type: str = "JournalPaper"  # 文献类型展示标签
    index_level: str = ""  # 索引级别标签（如 SCI、EI）
    citation_count: int = 0  # 被引次数
    score: float = 0.0  # 综合评分
    has_fulltext: bool = False  # 是否有全文（前端可据此显示全文标识）
    # TODO: 根据业务需求自定义额外字段，如 tags, badge, research_field 等


# ---------------------------------------------------------------------------
# 降级信号
# ---------------------------------------------------------------------------

class FallbackSignal(BaseModel):
    """降级信号，供智能体模型层判断是否需要走降级路径。

    当检索失败或返回文献不足时，通过此信号告知上游决策逻辑。
    文献展示场景中，降级意味着前端可能需要展示空状态或提示信息。
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
            # 走降级路径，前端展示空状态或提示信息
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
# 通用文献适配器
# ---------------------------------------------------------------------------

class PaperAdapter:
    """通用文献适配器 —— 将 StandardPaper 转换为前端展示用的 PaperCard。

    # TODO: 根据业务需求自定义
    #   1. 修改 source_agent 为你的智能体标识
    #   2. 在 to_search_request 中填入你的上下文字段
    #   3. 在 to_paper_cards 中调整 venue 清洗和作者格式化逻辑
    #   4. 在 _clean_venue 中增加更多清洗规则
    #   5. 在 _format_authors 中调整作者展示阈值和格式
    """

    source_agent = "paper_display"  # TODO: 根据业务需求自定义

    # 作者展示阈值：超过此数量的作者使用 "et al." 缩写
    MAX_AUTHORS_DISPLAY = 3  # TODO: 根据业务需求自定义阈值

    # 摘要展示截断长度
    ABSTRACT_DISPLAY_LENGTH = 300  # TODO: 根据业务需求自定义截断长度

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
        """将展示需求转换为公共 SearchRequest。

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
                "topic": kwargs.get("topic", ""),
                "display_section": kwargs.get("display_section", ""),
            },
        )

    def to_paper_cards(
        self,
        *,
        papers: list[StandardPaper],
        group_id: str = "paper",
    ) -> list[PaperCard]:
        """将 StandardPaper 列表映射为 PaperCard 列表。

        source_type 标记规范：
          - nstl_{source} = 来自真实文献检索
          - 例如 nstl_solr、nstl_semantic、nstl_hybrid
          - 非 nstl_ 前缀表示其他来源（降级生成 model_fallback 等）

        venue 清洗：
          - 去除多余空格和标点
          - 统一期刊名称格式

        作者格式化：
          - 3人以内：全部列出，逗号分隔
          - 超过3人：列出前3人 + "et al."

        参数：
            papers: StandardPaper 列表
            group_id: 分组标识，用于生成 card_id
        """
        cards: list[PaperCard] = []
        for paper_index, paper in enumerate(papers, start=1):
            card = PaperCard(
                card_id=f"card_{group_id}_{paper_index}",
                # 关键：nstl_ 前缀标识这是真实文献数据
                source_type=f"nstl_{paper.source}",
                title=paper.title,
                authors_display=self._format_authors(paper.authors),
                year=self._normalize_year(paper.year),
                venue_display=self._clean_venue(paper.venue),
                doi=paper.doi,
                url=paper.url,
                language=paper.language or "zh",
                abstract_display=self._truncate_abstract(paper.abstract),
                doc_type=self._format_doc_type(paper.doc_type),
                index_level=paper.index_level,
                citation_count=paper.citation_count,
                score=round(paper.final_score, 4),
                has_fulltext=paper.has_fulltext,
            )
            cards.append(card)
        return cards

    # ------------------------------------------------------------------
    # venue 清洗逻辑
    # ------------------------------------------------------------------

    def _clean_venue(self, venue: str) -> str:
        """清洗期刊/来源名称，去除多余空格和标点不一致。

        # TODO: 根据业务需求自定义清洗规则，当前为基础清洗。

        清洗规则：
          1. 去除首尾空格
          2. 合并连续空格为单个空格
          3. 去除尾部多余的逗号和分号
          4. 去除常见多余括号标记（如 "(全文)"）
        """
        if not venue:
            return ""

        # 去除首尾空格
        cleaned = venue.strip()

        # 合并连续空格
        cleaned = re.sub(r"\s+", " ", cleaned)

        # 去除尾部多余逗号和分号
        cleaned = re.sub(r"[,;]+$", "", cleaned)

        # 去除常见多余括号标记
        # TODO: 根据业务需求自定义需要去除的标记
        cleaned = re.sub(r"\s*\(全文\)\s*$", "", cleaned)
        cleaned = re.sub(r"\s*\(中文\)\s*$", "", cleaned)
        cleaned = re.sub(r"\s*\(外文\)\s*$", "", cleaned)

        return cleaned.strip()

    # ------------------------------------------------------------------
    # 作者格式化逻辑
    # ------------------------------------------------------------------

    def _format_authors(self, authors: list[str]) -> str:
        """将作者列表格式化为前端展示文本。

        # TODO: 根据业务需求自定义格式化方式。

        规则：
          - 空列表 → 返回空字符串
          - 1~3人 → 全部列出，逗号分隔："张三, 李四, 王五"
          - 超过阈值 → 列出前N人 + "et al."："张三, 李四, 王五 et al."
        """
        if not authors:
            return ""

        # 去除空字符串作者
        valid_authors = [a.strip() for a in authors if a.strip()]

        if not valid_authors:
            return ""

        if len(valid_authors) <= self.MAX_AUTHORS_DISPLAY:
            return ", ".join(valid_authors)

        # 超过阈值，截取前N人 + et al.
        shown = ", ".join(valid_authors[:self.MAX_AUTHORS_DISPLAY])
        return f"{shown} et al."

    # ------------------------------------------------------------------
    # 摘要截断逻辑
    # ------------------------------------------------------------------

    def _truncate_abstract(self, abstract: str) -> str:
        """截断摘要为前端展示长度。

        # TODO: 根据业务需求自定义截断长度和后缀。
        """
        if not abstract:
            return ""

        if len(abstract) <= self.ABSTRACT_DISPLAY_LENGTH:
            return abstract

        return abstract[:self.ABSTRACT_DISPLAY_LENGTH] + "..."

    # ------------------------------------------------------------------
    # 文献类型格式化
    # ------------------------------------------------------------------

    def _format_doc_type(self, doc_type: str) -> str:
        """将文献类型映射为前端展示标签。

        # TODO: 根据业务需求自定义映射规则和中文标签。
        """
        # 基础映射表
        type_labels = {
            "JournalPaper": "期刊论文",
            "ConferencePaper": "会议论文",
            "Book": "专著",
            "BookChapter": "专著章节",
            "Dissertation": "学位论文",
            "Patent": "专利",
            "Report": "报告",
            "Preprint": "预印本",
        }
        return type_labels.get(doc_type, doc_type)

    # ------------------------------------------------------------------
    # 年份标准化
    # ------------------------------------------------------------------

    def _normalize_year(self, value: int) -> int:
        """将年份标准化到合理范围 [1900, 2100]。"""
        try:
            year = int(value)
        except (TypeError, ValueError):
            year = 2026  # TODO: 根据业务需求自定义默认年份
        return max(1900, min(year, 2100))