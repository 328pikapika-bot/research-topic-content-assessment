"""兼容旧导入路径的文献检索客户端。

真正的公共实现已迁移到 `app.common.retrieval`。保留本文件是为了避免
已有业务代码或外部调用方在迁移期因 import 路径变化而失效。
"""

from app.common.retrieval import (
    LiteratureSearchClient,
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

