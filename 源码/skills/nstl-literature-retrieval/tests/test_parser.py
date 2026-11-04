"""检索网关响应解析器测试。

覆盖 SearchResultParser 的核心场景：
  - 正常响应解析
  - 字段映射正确性
  - 作者字段特殊处理
  - venue 兼容处理（dict / string）
  - 空响应处理
  - 异常响应处理（code != 1）
  - 单条文献解析异常不影响整批
"""

from __future__ import annotations

import pytest

from app.common.retrieval.parser import SearchResultParser
from app.common.retrieval.schemas import StandardPaper


@pytest.fixture
def parser() -> SearchResultParser:
    return SearchResultParser()


# ------------------------------------------------------------------
# 1. 正常响应解析（code==1, 有 data 数组）
# ------------------------------------------------------------------


class TestNormalParsing:
    """正常网关响应解析。"""

    def test_parse_standard_response(self, parser: SearchResultParser) -> None:
        """标准 code==1 且 data 非空时应正确返回文献列表。"""
        response = {
            "code": 1,
            "msg": "success",
            "data": [
                {
                    "id": "NSTL-001",
                    "title": "钙钛矿太阳能电池稳定性研究",
                    "hasAuthor": [
                        {"fullName": "张三"},
                        {"fullName": "李四"},
                    ],
                    "hasSource": "太阳能学报",
                    "year": 2023,
                    "doi": "10.1234/test.2023",
                    "hasFile": "https://www.nstl.gov.cn/paper/001",
                    "language": "zh",
                    "abstract": "摘要内容",
                    "keywords": ["钙钛矿", "太阳能电池"],
                    "score": 0.95,
                },
                {
                    "id": "NSTL-002",
                    "title": "Perovskite Solar Cell Stability",
                    "hasAuthor": [{"fullName": "John Doe"}],
                    "hasSource": {"title": "Nature Energy", "volume": "8", "issue": "3"},
                    "year": 2024,
                    "doi": "10.5678/ne.2024",
                    "hasFile": "https://www.nstl.gov.cn/paper/002",
                    "language": "en",
                    "abstract": "Abstract content",
                    "keywords": ["perovskite", "solar cell"],
                    "score": 0.88,
                },
            ],
        }

        papers = parser.parse(response, "钙钛矿太阳能电池")

        assert len(papers) == 2
        assert all(isinstance(p, StandardPaper) for p in papers)

    def test_parse_preserves_query(self, parser: SearchResultParser) -> None:
        """每条文献的 retrieval_query 应等于传入的 query 参数。"""
        response = {
            "code": 1,
            "data": [{"id": "X1", "title": "测试文献"}],
        }
        papers = parser.parse(response, "测试查询")
        assert papers[0].retrieval_query == "测试查询"


# ------------------------------------------------------------------
# 2. 字段映射正确性
# ------------------------------------------------------------------


class TestFieldMapping:
    """NSTL 网关字段 → StandardPaper 字段映射。"""

    def test_id_to_paper_id(self, parser: SearchResultParser) -> None:
        """id 字段应映射为 paper_id。"""
        response = {"code": 1, "data": [{"id": "ABC-123", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].paper_id == "ABC-123"

    def test_has_file_to_url(self, parser: SearchResultParser) -> None:
        """hasFile 字段应映射为 url。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "hasFile": "http://file.url"}]}
        papers = parser.parse(response, "q")
        assert papers[0].url == "http://file.url"

    def test_score_to_retrieval_score(self, parser: SearchResultParser) -> None:
        """score 字段应映射为 retrieval_score。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "score": 0.75}]}
        papers = parser.parse(response, "q")
        assert papers[0].retrieval_score == pytest.approx(0.75)

    def test_score_also_used_as_final_score_fallback(self, parser: SearchResultParser) -> None:
        """final_score 在无显式值时应使用 score 作为后备。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "score": 0.8}]}
        papers = parser.parse(response, "q")
        assert papers[0].final_score == pytest.approx(0.8)

    def test_doc_type_fallback(self, parser: SearchResultParser) -> None:
        """docType 不存在时默认为 JournalPaper。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].doc_type == "JournalPaper"

    def test_doc_type_custom_value(self, parser: SearchResultParser) -> None:
        """docType 存在时应使用自定义值。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "docType": "ConferencePaper"}]}
        papers = parser.parse(response, "q")
        assert papers[0].doc_type == "ConferencePaper"

    def test_source_defaults_to_nstl(self, parser: SearchResultParser) -> None:
        """source 字段默认为 'nstl'。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].source == "nstl"

    def test_language_defaults_to_zh(self, parser: SearchResultParser) -> None:
        """language 为空字符串时默认为 'zh'。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "language": ""}]}
        papers = parser.parse(response, "q")
        assert papers[0].language == "zh"

    def test_raw_field_preserves_original(self, parser: SearchResultParser) -> None:
        """raw 字段应保留网关返回的原始数据。"""
        item = {"id": "1", "title": "T", "custom_field": "custom_value"}
        response = {"code": 1, "data": [item]}
        papers = parser.parse(response, "q")
        assert papers[0].raw == item
        assert papers[0].raw["custom_field"] == "custom_value"

    def test_rerank_score_none_when_absent(self, parser: SearchResultParser) -> None:
        """无 rerank_score 时应为 None。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].rerank_score is None

    def test_rerank_score_present(self, parser: SearchResultParser) -> None:
        """有 rerank_score 时应为浮点数。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "rerank_score": 0.92}]}
        papers = parser.parse(response, "q")
        assert papers[0].rerank_score == pytest.approx(0.92)

    def test_references_field(self, parser: SearchResultParser) -> None:
        """references 字段应正确映射为字符串列表。"""
        response = {
            "code": 1,
            "data": [{"id": "1", "title": "T", "references": ["REF-A", "REF-B"]}],
        }
        papers = parser.parse(response, "q")
        assert papers[0].references == ["REF-A", "REF-B"]

    def test_id_converted_to_string(self, parser: SearchResultParser) -> None:
        """id 为数字时应转换为字符串。"""
        response = {"code": 1, "data": [{"id": 12345, "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].paper_id == "12345"


# ------------------------------------------------------------------
# 3. 作者字段特殊处理
# ------------------------------------------------------------------


class TestAuthorParsing:
    """hasAuthor 数组 → authors 列表的转换。"""

    def test_extract_full_name(self, parser: SearchResultParser) -> None:
        """从 hasAuthor 数组提取 fullName 字段。"""
        response = {
            "code": 1,
            "data": [
                {
                    "id": "1",
                    "title": "T",
                    "hasAuthor": [
                        {"fullName": "张三"},
                        {"fullName": "李四"},
                        {"fullName": "王五"},
                    ],
                }
            ],
        }
        papers = parser.parse(response, "q")
        assert papers[0].authors == ["张三", "李四", "王五"]

    def test_fallback_to_name_field(self, parser: SearchResultParser) -> None:
        """fullName 不存在时回退到 name 字段。"""
        response = {
            "code": 1,
            "data": [
                {
                    "id": "1",
                    "title": "T",
                    "hasAuthor": [{"name": "Alice"}, {"fullName": "Bob"}],
                }
            ],
        }
        papers = parser.parse(response, "q")
        assert papers[0].authors == ["Alice", "Bob"]

    def test_empty_has_author(self, parser: SearchResultParser) -> None:
        """hasAuthor 为空数组时 authors 应为空列表。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "hasAuthor": []}]}
        papers = parser.parse(response, "q")
        assert papers[0].authors == []

    def test_missing_has_author(self, parser: SearchResultParser) -> None:
        """hasAuthor 不存在时 authors 应为空列表。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].authors == []

    def test_author_with_empty_name_skipped(self, parser: SearchResultParser) -> None:
        """fullName 和 name 都为空字符串时，该作者应被跳过。"""
        response = {
            "code": 1,
            "data": [
                {
                    "id": "1",
                    "title": "T",
                    "hasAuthor": [{"fullName": ""}, {"fullName": "有效作者"}],
                }
            ],
        }
        papers = parser.parse(response, "q")
        assert papers[0].authors == ["有效作者"]


# ------------------------------------------------------------------
# 4. venue 兼容处理
# ------------------------------------------------------------------


class TestVenueParsing:
    """hasSource → venue 的兼容解析。"""

    def test_string_venue(self, parser: SearchResultParser) -> None:
        """hasSource 为字符串时直接作为 venue。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "hasSource": "太阳能学报"}]}
        papers = parser.parse(response, "q")
        assert papers[0].venue == "太阳能学报"

    def test_dict_venue_extracts_title(self, parser: SearchResultParser) -> None:
        """hasSource 为字典时提取 title 字段。"""
        response = {
            "code": 1,
            "data": [
                {
                    "id": "1",
                    "title": "T",
                    "hasSource": {"title": "Nature Energy", "volume": "8", "issue": "3"},
                }
            ],
        }
        papers = parser.parse(response, "q")
        assert papers[0].venue == "Nature Energy"

    def test_dict_venue_empty_title(self, parser: SearchResultParser) -> None:
        """hasSource 为字典但 title 为空时返回空字符串。"""
        response = {
            "code": 1,
            "data": [{"id": "1", "title": "T", "hasSource": {"volume": "8"}}],
        }
        papers = parser.parse(response, "q")
        assert papers[0].venue == ""

    def test_missing_has_source(self, parser: SearchResultParser) -> None:
        """hasSource 不存在时 venue 为空字符串。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert papers[0].venue == ""

    def test_none_has_source(self, parser: SearchResultParser) -> None:
        """hasSource 为 None 时 venue 为空字符串。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "hasSource": None}]}
        papers = parser.parse(response, "q")
        assert papers[0].venue == ""

    def test_numeric_has_source(self, parser: SearchResultParser) -> None:
        """hasSource 为数字等非预期类型时转为字符串。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "hasSource": 12345}]}
        papers = parser.parse(response, "q")
        assert papers[0].venue == "12345"


# ------------------------------------------------------------------
# 5. 空响应处理
# ------------------------------------------------------------------


class TestEmptyResponse:
    """各种空数据场景。"""

    def test_empty_data_array(self, parser: SearchResultParser) -> None:
        """data 为空数组时返回空列表。"""
        response = {"code": 1, "data": []}
        papers = parser.parse(response, "q")
        assert papers == []

    def test_missing_data_field(self, parser: SearchResultParser) -> None:
        """data 字段不存在时返回空列表。"""
        response = {"code": 1, "msg": "success"}
        papers = parser.parse(response, "q")
        assert papers == []

    def test_data_is_not_list(self, parser: SearchResultParser) -> None:
        """data 字段不是数组时返回空列表。"""
        response = {"code": 1, "data": "not a list"}
        papers = parser.parse(response, "q")
        assert papers == []

    def test_data_is_dict(self, parser: SearchResultParser) -> None:
        """data 字段为字典（非列表）时返回空列表。"""
        response = {"code": 1, "data": {"key": "value"}}
        papers = parser.parse(response, "q")
        assert papers == []


# ------------------------------------------------------------------
# 6. 异常响应处理（code != 1）
# ------------------------------------------------------------------


class TestErrorResponse:
    """网关返回错误码时的处理。

    注意：parser.parse() 只负责解析 data 数组，
    code!=1 的判断由 client 层处理。parse 只看 data 是否存在。
    """

    def test_code_not_one_still_parses_data(self, parser: SearchResultParser) -> None:
        """parser 层不判断 code，只要 data 存在就解析。"""
        response = {"code": 0, "msg": "error", "data": [{"id": "1", "title": "T"}]}
        # parser 不关心 code，只看 data
        papers = parser.parse(response, "q")
        assert len(papers) == 1

    def test_code_negative_with_data(self, parser: SearchResultParser) -> None:
        """code 为负数但有 data 时，parser 仍解析。"""
        response = {"code": -1, "msg": "fail", "data": [{"id": "1", "title": "T"}]}
        papers = parser.parse(response, "q")
        assert len(papers) == 1


# ------------------------------------------------------------------
# 7. 单条文献解析异常不影响整批
# ------------------------------------------------------------------


class TestItemLevelFaultTolerance:
    """逐项容错：单条解析失败不应影响其他记录。"""

    def test_non_dict_item_skipped(self, parser: SearchResultParser) -> None:
        """data 数组中非字典项被跳过。"""
        response = {
            "code": 1,
            "data": ["not_a_dict", {"id": "1", "title": "有效文献"}, 42, None],
        }
        papers = parser.parse(response, "q")
        assert len(papers) == 1
        assert papers[0].title == "有效文献"

    def test_invalid_year_still_produces_paper(self, parser: SearchResultParser) -> None:
        """year 为非数字字符串时安全转换为 0。"""
        response = {
            "code": 1,
            "data": [{"id": "1", "title": "T", "year": "not_a_number"}],
        }
        papers = parser.parse(response, "q")
        assert len(papers) == 1
        assert papers[0].year == 0

    def test_invalid_score_still_produces_paper(self, parser: SearchResultParser) -> None:
        """score 为非数字时安全转换为 0.0。"""
        response = {
            "code": 1,
            "data": [{"id": "1", "title": "T", "score": "invalid"}],
        }
        papers = parser.parse(response, "q")
        assert len(papers) == 1
        assert papers[0].retrieval_score == 0.0

    def test_mixed_valid_and_invalid_items(self, parser: SearchResultParser) -> None:
        """混合有效和无效项时，有效项正常解析。"""
        response = {
            "code": 1,
            "data": [
                {"id": "1", "title": "文献A", "year": 2023},
                "invalid_item",
                {"id": "2", "title": "文献B", "year": 2024},
            ],
        }
        papers = parser.parse(response, "q")
        assert len(papers) == 2
        assert papers[0].title == "文献A"
        assert papers[1].title == "文献B"

    def test_keywords_not_list_treated_as_empty(self, parser: SearchResultParser) -> None:
        """keywords 非列表时视为空列表。"""
        response = {
            "code": 1,
            "data": [{"id": "1", "title": "T", "keywords": "单一关键词"}],
        }
        papers = parser.parse(response, "q")
        assert papers[0].keywords == []

    def test_references_not_list_treated_as_empty(self, parser: SearchResultParser) -> None:
        """references 非列表时视为空列表。"""
        response = {
            "code": 1,
            "data": [{"id": "1", "title": "T", "references": "not_a_list"}],
        }
        papers = parser.parse(response, "q")
        assert papers[0].references == []

    def test_none_year_converted_to_zero(self, parser: SearchResultParser) -> None:
        """year 为 None 时安全转换为 0。"""
        response = {"code": 1, "data": [{"id": "1", "title": "T", "year": None}]}
        papers = parser.parse(response, "q")
        assert papers[0].year == 0
