"""统一检索 query 构造器模板。

将 SearchRequest 转换为 NSTL 混合检索网关接受的 question 字符串格式：
    "{查询文本} | 语种：{语言标签} | 年份：{开始年}-{结束年}"
"""

from __future__ import annotations

from app.common.retrieval.schemas import SearchRequest


class QueryBuilder:
    """将公共检索请求转换为统一网关接受的 question 字符串。"""

    def build_question(self, request: SearchRequest) -> str:
        """构造兼容 NSTL 混合检索网关的 question 参数。"""
        lang_pref = self._language_label(request.languages)
        start_year, end_year = request.year_range or (2016, 2026)  # TODO: 根据业务需求自定义默认年份范围
        query_text = self._build_query_text(request)
        return f"{query_text} | 语种：{lang_pref} | 年份：{start_year}-{end_year}"

    def _build_query_text(self, request: SearchRequest) -> str:
        """拼接检索文本，从 context 中提取辅助信息丰富查询。

        context 中可包含以下键（均为可选）：
          - topic: 研究主题
          - section_title: 章节/板块标题
          - candidate_question: 候选问题文本
          - evidence_purpose: 证据用途说明

        # TODO: 根据业务需求自定义 context 中提取的字段，
        #  例如添加 "research_field"、"sub_question" 等。
        """
        parts = [
            request.context.get("topic", ""),
            request.context.get("section_title", ""),
            request.context.get("candidate_question", ""),
            request.context.get("evidence_purpose", ""),
            request.query,
        ]
        # 保留顺序去重，避免不同智能体传入的上下文让检索词重复堆叠。
        cleaned = [str(part).strip() for part in parts if str(part).strip()]
        return " ".join(dict.fromkeys(cleaned)) or request.query

    def _language_label(self, languages: list[str]) -> str:
        """将语种代码列表转换为中文标签。

        映射规则：
          - ["zh", "en"] 或多种语言 → "中英混合"
          - 仅 "zh" → "中文"
          - 仅 "en" → "英文"
          - 其他 → "中英混合"
        """
        normalized = {item.lower() for item in languages}
        if {"zh", "en"}.issubset(normalized) or len(normalized) >= 2:
            return "中英混合"
        if "zh" in normalized:
            return "中文"
        if "en" in normalized:
            return "英文"
        return "中英混合"
