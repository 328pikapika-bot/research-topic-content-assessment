"""文献检索公共能力。

该包只封装真实检索服务的调用、响应解析和过程追踪。
各智能体的业务降级、证据分析和最终输出映射放在对应 Adapter 中。
"""

from app.common.retrieval.client import LiteratureSearchClient
from app.common.retrieval.schemas import (
    SearchRequest,
    SearchResponse,
    SearchTrace,
    StandardPaper,
)

__all__ = [
    "LiteratureSearchClient",
    "SearchRequest",
    "SearchResponse",
    "SearchTrace",
    "StandardPaper",
]

