"""兼容旧导入路径的文献检索客户端 —— 模板版本。

真正的公共实现已迁移到 `app.common.retrieval` 模块。保留本文件是为了避免
已有业务代码或外部调用方在迁移期因 import 路径变化而失效。

使用方式：
  旧代码: from app.clients.literature_search_client import LiteratureSearchClient
  新代码: from app.common.retrieval import LiteratureSearchClient

迁移完成后可删除本文件。
"""

from app.common.retrieval import (
    LiteratureSearchClient,
    SearchRequest,
    SearchResponse,
    StandardPaper,
    SearchTrace,
)

__all__ = [
    "LiteratureSearchClient",
    "SearchRequest",
    "SearchResponse",
    "StandardPaper",
    "SearchTrace",
]