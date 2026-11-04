"""QueryBuilder 单元测试。

覆盖语种标签、年份范围、最终格式和上下文去重逻辑。
"""

from __future__ import annotations

import pytest

from app.common.retrieval.query_builder import QueryBuilder
from app.common.retrieval.schemas import SearchRequest


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

builder = QueryBuilder()


def _request(**overrides) -> SearchRequest:
    """构造 SearchRequest，query 必填，其余提供合理默认值。"""
    defaults = dict(
        query="钙钛矿太阳能电池稳定性",
        paper_num=20,
        languages=["zh", "en"],
        year_range=(2016, 2026),
        context={},
    )
    defaults.update(overrides)
    return SearchRequest(**defaults)


# ---------------------------------------------------------------------------
# 语种标签
# ---------------------------------------------------------------------------


class TestLanguageLabel:
    def test_mixed_zh_en(self):
        """中英混合语种 → '中英混合'"""
        req = _request(languages=["zh", "en"])
        assert builder._language_label(req.languages) == "中英混合"

    def test_pure_zh(self):
        """纯中文语种 → '中文'"""
        req = _request(languages=["zh"])
        assert builder._language_label(req.languages) == "中文"

    def test_pure_en(self):
        """纯英文语种 → '英文'"""
        req = _request(languages=["en"])
        assert builder._language_label(req.languages) == "英文"

    def test_case_insensitive(self):
        """语种代码大小写不敏感"""
        assert builder._language_label(["ZH", "EN"]) == "中英混合"
        assert builder._language_label(["ZH"]) == "中文"
        assert builder._language_label(["EN"]) == "英文"

    def test_unknown_language_fallback(self):
        """未知语种列表默认 → '中英混合'"""
        assert builder._language_label(["fr"]) == "中英混合"

    def test_multi_language(self):
        """三种以上语种 → '中英混合'"""
        assert builder._language_label(["zh", "en", "fr"]) == "中英混合"


# ---------------------------------------------------------------------------
# 年份范围
# ---------------------------------------------------------------------------


class TestYearRange:
    def test_custom_year_range(self):
        """指定年份范围应正确体现在 question 中"""
        req = _request(year_range=(2021, 2026))
        question = builder.build_question(req)
        assert "年份：2021-2026" in question

    def test_default_year_range(self):
        """未指定 year_range 时使用默认 (2016, 2026)"""
        req = _request(year_range=None)
        question = builder.build_question(req)
        assert "年份：2016-2026" in question


# ---------------------------------------------------------------------------
# 最终格式
# ---------------------------------------------------------------------------


class TestFinalFormat:
    def test_format_structure(self):
        """最终 question 格式为 '{查询文本} | 语种：{语言标签} | 年份：{开始年}-{结束年}'"""
        req = _request(
            query="深度学习",
            languages=["zh", "en"],
            year_range=(2021, 2026),
            context={},
        )
        question = builder.build_question(req)
        # 格式：查询文本 | 语种：中英混合 | 年份：2021-2026
        assert question == "深度学习 | 语种：中英混合 | 年份：2021-2026"

    def test_pure_zh_format(self):
        """纯中文语种的格式"""
        req = _request(query="量子计算", languages=["zh"], year_range=(2020, 2025))
        question = builder.build_question(req)
        assert question == "量子计算 | 语种：中文 | 年份：2020-2025"

    def test_pure_en_format(self):
        """纯英文语种的格式"""
        req = _request(query="transformer", languages=["en"], year_range=(2019, 2024))
        question = builder.build_question(req)
        assert question == "transformer | 语种：英文 | 年份：2019-2024"


# ---------------------------------------------------------------------------
# 上下文字段合并去重
# ---------------------------------------------------------------------------


class TestContextDedup:
    def test_dedup_duplicate_context(self):
        """context 与 query 重复时去重，不堆叠"""
        req = _request(
            query="钙钛矿太阳能电池稳定性",
            context={
                "topic": "钙钛矿太阳能电池稳定性",
            },
        )
        question = builder.build_question(req)
        # topic 和 query 相同，去重后只出现一次
        query_text = question.split(" | ")[0]
        # 不应出现重复拼接
        assert "钙钛矿太阳能电池稳定性 钙钛矿太阳能电池稳定性" not in query_text
        assert "钙钛矿太阳能电池稳定性" in query_text

    def test_context_fields_merged(self):
        """context 中各字段按顺序合并"""
        req = _request(
            query="优化方法",
            context={
                "topic": "钙钛矿太阳能电池",
                "section_title": "稳定性研究",
                "candidate_question": "如何提高稳定性",
                "evidence_purpose": "综述研究进展",
            },
        )
        question = builder.build_question(req)
        query_text = question.split(" | ")[0]
        # 各上下文字段和 query 应都出现在查询文本中
        assert "钙钛矿太阳能电池" in query_text
        assert "稳定性研究" in query_text
        assert "如何提高稳定性" in query_text
        assert "综述研究进展" in query_text
        assert "优化方法" in query_text

    def test_empty_context_uses_query_only(self):
        """context 为空时直接使用 query"""
        req = _request(query="大模型", context={})
        question = builder.build_question(req)
        query_text = question.split(" | ")[0]
        assert query_text == "大模型"

    def test_dedup_preserves_order(self):
        """去重保留首次出现的顺序"""
        req = _request(
            query="深度学习",
            context={
                "topic": "深度学习",
                "section_title": "图像识别",
                "candidate_question": "图像识别",
                "evidence_purpose": "",
            },
        )
        question = builder.build_question(req)
        query_text = question.split(" | ")[0]
        # 深度学习 和 图像识别 各只出现一次
        assert query_text == "深度学习 图像识别"
