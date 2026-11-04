"""文献检索公共能力模板。

该包只封装真实检索服务的调用、响应解析和过程追踪。
各智能体的业务降级、证据分析和最终输出映射放在对应 Adapter 中。

使用方式：
  1. 将此目录复制到项目的 app/common/retrieval/ 下
  2. 配置环境变量 LITERATURE_SEARCH_API_URL 和 LITERATURE_SEARCH_TIMEOUT
  3. 在 adapters/ 下创建业务适配器（参考 example.py）
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
