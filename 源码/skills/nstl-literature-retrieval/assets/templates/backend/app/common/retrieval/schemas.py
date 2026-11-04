"""公共文献检索数据结构模板。

这些结构位于智能体业务层之下，用于统一不同智能体对真实检索服务的调用。
公共层只表达检索请求、标准文献和过程追踪，不承载特定智能体的业务含义。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """统一检索请求。

    query 是智能体生成的检索文本；context 用于保留智能体侧的业务上下文，
    例如 evidence_need_id、section_id 等智能体特有的追踪字段。
    """

    query: str  # 检索查询文本
    paper_num: int = 20  # 期望返回的文献数量
    year_range: tuple[int, int] | None = None  # 年份范围，如 (2016, 2026)
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])  # 检索语种列表
    rerank: bool = True  # 是否启用重排序
    intent_classification: bool = True  # 是否启用意图分类
    solr_type: int = 2  # # TODO: 根据业务需求自定义 Solr 检索类型
    user_scope: str | None = None  # 用户权限范围（如机构权限标识）
    request_id: str | None = None  # 请求追踪ID，为空时自动生成
    source_agent: str = ""  # 调用方智能体标识，如 "good_question"
    context: dict[str, Any] = Field(default_factory=dict)  # # TODO: 根据业务需求自定义 业务上下文字典


class StandardPaper(BaseModel):
    """统一文献结构，所有检索通道输出均转换为此格式。"""

    paper_id: str = ""  # 文献唯一标识（来自检索网关的 id 字段）
    source: str = ""  # 数据来源标识（默认 "nstl"）
    title: str = ""  # 文献标题
    authors: list[str] = Field(default_factory=list)  # 作者列表
    year: int = 0  # 发表年份
    venue: str = ""  # 期刊/会议/来源名称
    doi: str = ""  # DOI 标识
    url: str = ""  # 文献链接（全文或详情页）
    language: str = "zh"  # 文献语种，"zh" 或 "en"
    abstract: str = ""  # 摘要
    keywords: list[str] = Field(default_factory=list)  # 关键词列表
    doc_type: str = "JournalPaper"  # 文献类型，如 JournalPaper、ConferencePaper
    index_level: str = ""  # 索引级别，如 SCI、EI
    has_fulltext: bool = False  # 是否有全文
    retrieval_query: str = ""  # 本次检索使用的查询文本
    retrieval_score: float = 0.0  # 检索原始得分
    rrf_score: float = 0.0  # 倒数排名融合得分
    quality_score: float = 0.0  # 质量评估得分
    final_score: float = 0.0  # 综合最终得分
    rerank_score: Optional[float] = None  # 重排序得分（启用rerank时才有值）
    citation_count: int = 0  # 被引次数
    references: list[str] = Field(default_factory=list)  # 参考文献 ID 列表
    raw: dict[str, Any] = Field(default_factory=dict)  # 检索网关返回的原始数据，供业务层深度访问


class SearchTrace(BaseModel):
    """检索过程追踪信息，供前端展示和排障使用。"""

    request_id: str = ""  # 请求唯一标识
    source_agent: str = ""  # 调用方智能体标识
    query: str = ""  # 原始检索查询
    question: str = ""  # 经 QueryBuilder 构造后的完整查询字符串
    api_url_configured: bool = False  # 是否已配置检索网关地址
    gateway_status_code: int | None = None  # HTTP 状态码
    gateway_code: int | None = None  # 网关业务状态码（1=成功）
    gateway_msg: str = ""  # 网关返回的消息
    paper_num_requested: int = 0  # 请求的文献数量
    paper_num_returned: int = 0  # 实际返回的文献数量
    rerank_enabled: bool = True  # 是否启用了重排序
    intent_classification_enabled: bool = True  # 是否启用了意图分类
    duration_ms: float = 0.0  # 检索耗时（毫秒）
    errors: list[str] = Field(default_factory=list)  # 错误信息列表
    degradation_level: int = 0  # 降级级别：0=正常, 4=完全降级


class SearchResponse(BaseModel):
    """统一检索响应。"""

    success: bool = False  # 检索是否成功
    total: int = 0  # 返回文献总数
    papers: list[StandardPaper] = Field(default_factory=list)  # 标准化文献列表
    query: str = ""  # 原始查询文本
    question: str = ""  # 构造后的查询字符串
    rewritten_query: str | None = None  # # TODO: 根据业务需求自定义 改写后的查询（如需查询改写功能）
    error: str | None = None  # 错误信息
    duration_ms: float = 0.0  # 检索耗时（毫秒）
    search_trace: dict[str, Any] = Field(default_factory=dict)  # 完整追踪信息字典


# ---------------------------------------------------------------------------
# 环境变量默认值（模板版本，从环境变量读取，无需依赖项目配置模块）
# ---------------------------------------------------------------------------


def _read_env_with_fallback(names: list[str], default: str) -> str:
    """按优先级依次尝试多个环境变量名，返回第一个非空值"""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


LITERATURE_SEARCH_API_URL: str = _read_env_with_fallback(
    [
        "LITERATURE_SEARCH_API_URL",
        "NSTL_RETRIEVAL_API_URL",
        "GOOD_QUESTION_MILVUS_API_URL",
        "MILVUS_API_URL",
    ],
    "",
)

LITERATURE_SEARCH_TIMEOUT: int = int(
    _read_env_with_fallback(
        [
            "LITERATURE_SEARCH_TIMEOUT",
            "NSTL_RETRIEVAL_TIMEOUT",
            "GOOD_QUESTION_MILVUS_HTTP_TIMEOUT",
            "MILVUS_HTTP_TIMEOUT",
        ],
        "600",
    )
)
