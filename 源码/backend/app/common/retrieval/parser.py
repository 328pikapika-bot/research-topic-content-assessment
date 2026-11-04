"""统一检索网关响应解析。"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from app.common.retrieval.schemas import StandardPaper
from app.common.retrieval.links import http_url, nstl_detail_url

logger = logging.getLogger(__name__)


class SearchResultParser:
    """将检索网关原始响应转换为 StandardPaper。"""

    def parse(self, response_data: dict[str, Any], query: str) -> list[StandardPaper]:
        """解析 NSTL 网关返回的 JSON 数据，单条异常不影响整批结果。"""
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
        authors = self._parse_authors(
            item.get("hasAuthor") or item.get("authors") or item.get("author") or []
        )

        keywords = item.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        raw_disciplines = next((item[key] for key in ("disciplineTags", "discipline_tags", "disciplines", "subjects", "subjectCategories", "hasSubject") if item.get(key)), [])
        if isinstance(raw_disciplines, (str, dict)):
            raw_disciplines = [raw_disciplines]
        discipline_tags = []
        if isinstance(raw_disciplines, list):
            for value in raw_disciplines:
                label = value if isinstance(value, str) else next((str(value[key]) for key in ("name", "label", "subject", "discipline") if isinstance(value, dict) and value.get(key)), "")
                if label and str(label).strip() not in discipline_tags:
                    discipline_tags.append(str(label).strip()[:80])

        links = item.get("links") if isinstance(item.get("links"), dict) else {}
        urls = item.get("urls") if isinstance(item.get("urls"), dict) else {}
        paper_id = self._text(self._first(
            item, links,
            "id", "paperId", "paperID", "paper_id", "documentId", "document_id",
            "articleId", "article_id", "recordId", "record_id",
        ))
        detail_value = self._first(
            item, links, urls,
            "detailUrl", "detail_url", "detailURL", "nstlUrl", "nstl_url", "nstlDetailUrl",
            "nstl_detail_url", "paperDetailUrl", "paper_detail_url", "landingPage", "landing_page",
            "recordUrl", "record_url", "detail", "paperDetail",
        )
        source_value = self._first(
            item, links, urls,
            "sourceUrl", "source_url", "sourceURL", "fulltextUrl", "fullTextUrl", "fulltextURL",
            "full_text_url", "pdfUrl", "pdf_url", "originalUrl", "original_url", "originalURL",
            "documentUrl", "document_url", "link", "href", "source",
        )
        file_value = self._first(item, links, urls, "hasFile", "fileUrl", "file_url", "url")
        nstl_url = http_url(detail_value) or nstl_detail_url(paper_id)
        source_url = http_url(source_value)
        file_url = http_url(file_value)
        # A few gateway versions put the detail page in hasFile. Preserve it
        # as the canonical NSTL URL instead of treating it as a full-text URL.
        if file_url and "nstl.gov.cn" in (urlsplit(file_url).hostname or "") and "paper_detail" in file_url:
            nstl_url, file_url = file_url, ""

        return StandardPaper(
            paper_id=paper_id,
            source=str(item.get("source", "nstl")),
            title=str(item.get("title", "")),
            authors=authors,
            year=self._safe_int(item.get("year", 0)),
            venue=self._parse_venue(item.get("hasSource", "")),
            doi=self._text(item.get("doi") or item.get("DOI")),
            url=file_url,
            nstl_url=nstl_url,
            source_url=source_url,
            language=str(item.get("language", "")) or "zh",
            abstract=str(item.get("abstract", "")),
            keywords=[str(keyword) for keyword in keywords],
            discipline_tags=discipline_tags[:12],
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

    @staticmethod
    def _first(item: dict[str, Any], *sources: Any) -> Any:
        """Return the first non-empty value across top-level and link maps."""
        keys = [source for source in sources if isinstance(source, str)]
        # Search the top-level record as well as nested link maps.  Previously
        # only nested maps were searched, so aliases such as ``fullTextUrl``
        # at the top level were silently discarded.
        for source in (item, *sources):
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if value and not isinstance(value, (dict, list, tuple)):
                    return value
            # Gateway responses sometimes group link aliases below an
            # ``nstl``/``source`` object. Keep the traversal shallow and
            # deterministic so arbitrary response payloads are not scanned.
            for value in source.values():
                if isinstance(value, dict):
                    nested = SearchResultParser._first(value, *keys)
                    if nested:
                        return nested
        return ""

    def _parse_authors(self, value: Any) -> list[str]:
        """Normalize NSTL author fields to a list of display names."""
        if value is None:
            return []
        if isinstance(value, str):
            return [
                name.strip()
                for name in value.replace("；", ";").replace("\n", ";").split(";")
                if name.strip()
            ]
        if isinstance(value, dict):
            full_name = value.get("fullName") or value.get("name") or value.get("authorName")
            return [str(full_name).strip()] if full_name and str(full_name).strip() else []
        if isinstance(value, list):
            authors: list[str] = []
            for author in value:
                authors.extend(self._parse_authors(author))
            return authors
        return []

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _text(self, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    def _safe_float(self, value: Any) -> float:
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
