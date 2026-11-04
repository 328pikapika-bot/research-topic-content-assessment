"""统一检索 query 构造器。"""

from __future__ import annotations

from app.common.retrieval.schemas import SearchRequest


class QueryBuilder:
    """将公共检索请求转换为统一网关接受的 question 字符串。"""

    def build_question(self, request: SearchRequest) -> str:
        """构造兼容现有 NSTL 混合检索网关的 question 参数。"""
        query_text = self._build_query_text(request)
        filters: list[str] = []
        if request.languages:
            filters.append(f"语种：{self._language_label(request.languages)}")
        if request.year_range:
            filters.append(f"年份：{request.year_range[0]}-{request.year_range[1]}")
        return " | ".join([query_text, *filters])

    def _build_query_text(self, request: SearchRequest) -> str:
        parts = [
            request.context.get("topic", ""),
            request.context.get("section_title", ""),
            request.context.get("claim_text", ""),
            request.context.get("evidence_purpose", ""),
            request.query,
        ]
        # 保留顺序去重，避免不同智能体传入的上下文让检索词重复堆叠。
        cleaned = [str(part).strip() for part in parts if str(part).strip()]
        return " ".join(dict.fromkeys(cleaned)) or request.query

    def _language_label(self, languages: list[str]) -> str:
        normalized = {item.lower() for item in languages}
        if {"zh", "en"}.issubset(normalized) or len(normalized) >= 2:
            return "中英混合"
        if "zh" in normalized:
            return "中文"
        if "en" in normalized:
            return "英文"
        return "中英混合"
