"""公共文献检索数据结构。

这些结构位于智能体业务层之下，用于统一不同智能体对真实检索服务的调用。
公共层只表达检索请求、标准文献和过程追踪，不承载创新性评估或深度研究的业务含义。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """统一检索请求。

    query 是智能体生成的检索文本；context 用于保留智能体侧的业务上下文，
    例如创新性评估的 evidence_need_id 或深度研究的 section_id。
    """

    query: str
    paper_num: int = 20
    year_range: tuple[int, int] | None = None
    languages: list[str] = Field(default_factory=list)
    rerank: bool = True
    intent_classification: bool = True
    solr_type: int = 2
    user_scope: str | None = None
    request_id: str | None = None
    source_agent: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class StandardPaper(BaseModel):
    """统一文献结构，所有检索通道输出均转换为此格式。"""

    paper_id: str = ""
    source: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    venue: str = ""
    doi: str = ""
    url: str = ""
    nstl_url: str = ""
    source_url: str = ""
    language: str = "zh"
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    discipline_tags: list[str] = Field(default_factory=list)
    doc_type: str = "JournalPaper"
    index_level: str = ""
    has_fulltext: bool = False
    retrieval_query: str = ""
    retrieval_score: float = 0.0
    rrf_score: float = 0.0
    quality_score: float = 0.0
    final_score: float = 0.0
    rerank_score: Optional[float] = None
    citation_count: int = 0
    references: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class SearchTrace(BaseModel):
    """检索过程追踪信息，供前端展示和排障使用。"""

    request_id: str = ""
    source_agent: str = ""
    query: str = ""
    question: str = ""
    api_url_configured: bool = False
    gateway_status_code: int | None = None
    gateway_code: int | None = None
    gateway_msg: str = ""
    paper_num_requested: int = 0
    paper_num_returned: int = 0
    rerank_enabled: bool = True
    intent_classification_enabled: bool = True
    duration_ms: float = 0.0
    errors: list[str] = Field(default_factory=list)
    degradation_level: int = 0


class SearchResponse(BaseModel):
    """统一检索响应。"""

    success: bool = False
    total: int = 0
    papers: list[StandardPaper] = Field(default_factory=list)
    query: str = ""
    question: str = ""
    rewritten_query: str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    search_trace: dict[str, Any] = Field(default_factory=dict)
