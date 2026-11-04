"""统一检索网关响应解析模板。

将 NSTL 混合检索网关返回的 JSON 数据转换为 StandardPaper 列表。
核心特性：
  - 网关 code==1 判断成功
  - data[] 数组提取
  - 逐项容错：单条解析失败不影响其他记录
  - 字段映射：id→paper_id, hasAuthor→authors, hasSource→venue 等
  - 作者字段特殊处理：从 hasAuthor 数组提取 fullName 或 name
  - 来源字段兼容处理：dict 和 string 两种格式
"""

from __future__ import annotations

import logging
from typing import Any

from app.common.retrieval.schemas import StandardPaper

logger = logging.getLogger(__name__)


class SearchResultParser:
    """将检索网关原始响应转换为 StandardPaper。"""

    def parse(self, response_data: dict[str, Any], query: str) -> list[StandardPaper]:
        """解析 NSTL 网关返回的 JSON 数据，单条异常不影响整批结果。

        预期网关响应格式：
        {
            "code": 1,           # 1=成功，其他=失败
            "msg": "success",
            "data": [            # 文献列表
                { "id": "...", "title": "...", ... },
                ...
            ]
        }
        """
        papers: list[StandardPaper] = []
        data_list = response_data.get("data", [])
        if not isinstance(data_list, list):
            logger.warning("NSTL 响应 data 字段不是数组，返回空结果")
            return papers

        for item in data_list:
            if not isinstance(item, dict):
                continue
            try:
                papers.append(self._parse_item(item, query))
            except Exception as exc:
                logger.warning("跳过无法解析的文献记录: %s", exc)
        return papers

    def _parse_item(self, item: dict[str, Any], query: str) -> StandardPaper:
        """解析单条文献记录。

        字段映射规则（NSTL 网关 → StandardPaper）：
          - id → paper_id
          - hasAuthor (数组) → authors (提取 fullName/name)
          - hasSource (dict/string) → venue
          - hasFile → url
          - 其余字段基本一对一映射
        """
        # ---- 作者字段特殊处理 ----
        # hasAuthor 通常是数组，每项为 dict，含 fullName 或 name 字段
        authors: list[str] = []
        has_author = item.get("hasAuthor", [])
        if isinstance(has_author, list):
            for author in has_author:
                if isinstance(author, dict):
                    full_name = author.get("fullName") or author.get("name", "")
                    if full_name:
                        authors.append(str(full_name))

        # ---- 关键词字段兼容 ----
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []

        return StandardPaper(
            paper_id=str(item.get("id", "")),
            source=str(item.get("source", "nstl")),
            title=str(item.get("title", "")),
            authors=authors,
            year=self._safe_int(item.get("year", 0)),
            venue=self._parse_venue(item.get("hasSource", "")),  # 兼容 dict 和 string
            doi=str(item.get("doi", "")),
            url=str(item.get("hasFile", "")),  # TODO: 根据业务需求自定义 URL 字段映射
            language=str(item.get("language", "")) or "zh",
            abstract=str(item.get("abstract", "")),
            keywords=[str(keyword) for keyword in keywords],
            doc_type=str(item.get("docType", item.get("doc_type", "JournalPaper"))),
            index_level=str(item.get("indexLevel", item.get("index_level", ""))),
            has_fulltext=bool(item.get("hasFulltext", item.get("has_fulltext", False))),
            retrieval_query=query,
            retrieval_score=self._safe_float(item.get("score", 0.0)),
            rrf_score=self._safe_float(item.get("rrf_score", 0.0)),
            quality_score=self._safe_float(item.get("quality_score", 0.0)),
            final_score=self._safe_float(item.get("final_score", item.get("score", 0.0))),
            rerank_score=(
                self._safe_float(item["rerank_score"])
                if item.get("rerank_score") is not None
                else None
            ),
            citation_count=self._safe_int(item.get("citation_count", 0)),
            references=item.get("references", []) if isinstance(item.get("references"), list) else [],
            raw=item,
        )

    def _safe_int(self, value: Any) -> int:
        """安全转换为整数，转换失败返回 0。"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _safe_float(self, value: Any) -> float:
        """安全转换为浮点数，转换失败返回 0.0。"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _parse_venue(self, has_source: Any) -> str:
        """解析期刊/来源信息，兼容 dict 和 string 两种格式。

        NSTL 网关返回的 hasSource 通常是期刊名字符串，但部分记录
        会返回完整 dict（含 volume/issue/title 等字段）。
        """
        if isinstance(has_source, str):
            return has_source
        if isinstance(has_source, dict):
            return str(has_source.get("title", "") or "")
        return str(has_source) if has_source else ""
