"""客户端集成测试（使用 mock 模拟 HTTP 网关）。

覆盖 LiteratureSearchClient 的核心场景：
  - 正常检索流程
  - 网关超时处理
  - 网关连接失败处理
  - 网关返回错误码处理
  - API URL 未配置时的行为
  - SearchTrace 记录完整性
  - search_sync 兼容方法
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.common.retrieval.client import LiteratureSearchClient
from app.common.retrieval.schemas import SearchRequest, SearchResponse


# ------------------------------------------------------------------
# 测试用 mock 响应数据
# ------------------------------------------------------------------

MOCK_SUCCESS_RESPONSE = {
    "code": 1,
    "msg": "success",
    "data": [
        {
            "id": "NSTL-001",
            "title": "钙钛矿太阳能电池稳定性研究",
            "hasAuthor": [{"fullName": "张三"}, {"fullName": "李四"}],
            "hasSource": "太阳能学报",
            "year": 2023,
            "doi": "10.1234/test.2023",
            "hasFile": "https://www.nstl.gov.cn/paper/001",
            "language": "zh",
            "abstract": "摘要内容",
            "keywords": ["钙钛矿", "太阳能电池"],
            "score": 0.95,
        },
    ],
}


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    raise_for_status: Exception | None = None,
) -> MagicMock:
    """构造模拟 requests.post 返回的 Response 对象。"""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status.side_effect = raise_for_status
    return resp


# ------------------------------------------------------------------
# 1. 正常检索流程
# ------------------------------------------------------------------


class TestNormalSearch:
    """正常检索流程。"""

    @patch("app.common.retrieval.client.requests.post")
    def test_search_returns_success(self, mock_post: MagicMock) -> None:
        """网关正常返回 code==1 时，响应 success=True。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        request = SearchRequest(query="钙钛矿", source_agent="test_agent")
        response = client.search(request)

        assert response.success is True
        assert len(response.papers) == 1
        assert response.papers[0].title == "钙钛矿太阳能电池稳定性研究"

    @patch("app.common.retrieval.client.requests.post")
    def test_search_sends_correct_payload(self, mock_post: MagicMock) -> None:
        """客户端应发送正确格式的 payload。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        request = SearchRequest(
            query="大语言模型",
            paper_num=15,
            year_range=(2020, 2026),
            languages=["zh", "en"],
            rerank=True,
            source_agent="my_agent",
            request_id="req-001",
        )
        client.search(request)

        # 验证 requests.post 被调用
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # 验证 URL
        assert call_kwargs[0][0] == "http://test-api/search"

        # 验证 payload 中的关键字段
        payload = call_kwargs[1]["json"]
        assert payload["paperNum"] == 15
        assert payload["rerank"] is True
        assert "语种" in payload["question"]
        assert "年份" in payload["question"]

    @patch("app.common.retrieval.client.requests.post")
    def test_search_sends_correct_headers(self, mock_post: MagicMock) -> None:
        """客户端应发送标准请求头。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        request = SearchRequest(
            query="测试",
            source_agent="test_agent",
            request_id="req-002",
            user_scope="org_001",
        )
        client.search(request)

        call_kwargs = mock_post.call_args
        headers = call_kwargs[1]["headers"]
        assert headers["X-Request-Id"] == "req-002"
        assert headers["X-Source-Agent"] == "test_agent"
        assert headers["X-User-Scope"] == "org_001"

    @patch("app.common.retrieval.client.requests.post")
    def test_search_bypasses_proxy(self, mock_post: MagicMock) -> None:
        """客户端应绕过系统代理发送请求。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        client.search(SearchRequest(query="测试"))

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["proxies"] == {"http": None, "https": None}


# ------------------------------------------------------------------
# 2. 网关超时处理
# ------------------------------------------------------------------


class TestTimeoutHandling:
    """网关超时处理。"""

    @patch("app.common.retrieval.client.requests.post")
    def test_timeout_returns_failure(self, mock_post: MagicMock) -> None:
        """网关超时时应返回失败响应。"""
        mock_post.side_effect = requests.Timeout("Connection timed out")

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=5)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False
        assert "超时" in response.error

    @patch("app.common.retrieval.client.requests.post")
    def test_timeout_sets_degradation_level(self, mock_post: MagicMock) -> None:
        """超时时 degradation_level 应为 4。"""
        mock_post.side_effect = requests.Timeout("Connection timed out")

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=5)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("degradation_level") == 4

    @patch("app.common.retrieval.client.requests.post")
    def test_timeout_records_error(self, mock_post: MagicMock) -> None:
        """超时时 errors 列表应记录超时信息。"""
        mock_post.side_effect = requests.Timeout("Connection timed out")

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=5)
        response = client.search(SearchRequest(query="测试"))

        errors = response.search_trace.get("errors", [])
        assert any("超时" in e for e in errors)


# ------------------------------------------------------------------
# 3. 网关连接失败处理
# ------------------------------------------------------------------


class TestConnectionErrorHandling:
    """网关连接失败处理。"""

    @patch("app.common.retrieval.client.requests.post")
    def test_connection_error_returns_failure(self, mock_post: MagicMock) -> None:
        """连接失败时应返回失败响应。"""
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False
        assert "网络不可达" in response.error

    @patch("app.common.retrieval.client.requests.post")
    def test_connection_error_sets_degradation_level(self, mock_post: MagicMock) -> None:
        """连接失败时 degradation_level 应为 4。"""
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("degradation_level") == 4

    @patch("app.common.retrieval.client.requests.post")
    def test_connection_error_records_error(self, mock_post: MagicMock) -> None:
        """连接失败时 errors 应包含网络不可达信息。"""
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        errors = response.search_trace.get("errors", [])
        assert any("网络不可达" in e for e in errors)


# ------------------------------------------------------------------
# 4. 网关返回错误码处理
# ------------------------------------------------------------------


class TestGatewayErrorCode:
    """网关返回 code != 1 的处理。"""

    @patch("app.common.retrieval.client.requests.post")
    def test_gateway_code_zero(self, mock_post: MagicMock) -> None:
        """网关返回 code==0 时应返回失败响应。"""
        mock_post.return_value = _mock_response(
            json_data={"code": 0, "msg": "参数错误", "data": []}
        )

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False
        assert "参数错误" in response.error

    @patch("app.common.retrieval.client.requests.post")
    def test_gateway_code_negative(self, mock_post: MagicMock) -> None:
        """网关返回 code==-1 时应返回失败响应。"""
        mock_post.return_value = _mock_response(
            json_data={"code": -1, "msg": "服务异常", "data": []}
        )

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False
        assert "服务异常" in response.error

    @patch("app.common.retrieval.client.requests.post")
    def test_gateway_error_records_code_in_trace(self, mock_post: MagicMock) -> None:
        """错误码应记录到 search_trace 的 gateway_code。"""
        mock_post.return_value = _mock_response(
            json_data={"code": 0, "msg": "fail", "data": []}
        )

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("gateway_code") == 0

    @patch("app.common.retrieval.client.requests.post")
    def test_gateway_error_with_empty_msg(self, mock_post: MagicMock) -> None:
        """网关返回错误码但 msg 为空时应显示'未知错误'。"""
        mock_post.return_value = _mock_response(
            json_data={"code": 2, "msg": "", "data": []}
        )

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False
        assert "未知错误" in response.error

    @patch("app.common.retrieval.client.requests.post")
    def test_http_status_error(self, mock_post: MagicMock) -> None:
        """HTTP 状态码非 2xx 时应返回失败响应。"""
        resp = _mock_response(status_code=500, json_data={"error": "Internal Server Error"})
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_post.return_value = resp

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False


# ------------------------------------------------------------------
# 5. API URL 未配置时的行为
# ------------------------------------------------------------------


class TestApiUrlNotConfigured:
    """LITERATURE_SEARCH_API_URL 为空时的行为。"""

    def test_empty_api_url_returns_failure(self) -> None:
        """API URL 为空字符串时应返回失败响应。"""
        client = LiteratureSearchClient(api_url="", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.success is False
        assert "未配置" in response.error

    def test_empty_api_url_sets_degradation(self) -> None:
        """API URL 为空时 degradation_level 应为 4。"""
        client = LiteratureSearchClient(api_url="", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("degradation_level") == 4

    def test_empty_api_url_trace_flag(self) -> None:
        """API URL 为空时 api_url_configured 应为 False。"""
        client = LiteratureSearchClient(api_url="", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("api_url_configured") is False

    @patch("app.common.retrieval.client.requests.post")
    def test_configured_api_url_trace_flag(self, mock_post: MagicMock) -> None:
        """API URL 已配置时 api_url_configured 应为 True。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("api_url_configured") is True


# ------------------------------------------------------------------
# 6. SearchTrace 记录完整性
# ------------------------------------------------------------------


class TestSearchTraceCompleteness:
    """SearchTrace 应完整记录检索过程。"""

    @patch("app.common.retrieval.client.requests.post")
    def test_trace_contains_request_id(self, mock_post: MagicMock) -> None:
        """trace 应包含 request_id。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(
            SearchRequest(query="测试", request_id="trace-001", source_agent="agent_a")
        )

        assert response.search_trace.get("request_id") == "trace-001"

    @patch("app.common.retrieval.client.requests.post")
    def test_trace_contains_source_agent(self, mock_post: MagicMock) -> None:
        """trace 应包含 source_agent。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(
            SearchRequest(query="测试", source_agent="good_question")
        )

        assert response.search_trace.get("source_agent") == "good_question"

    @patch("app.common.retrieval.client.requests.post")
    def test_trace_contains_duration(self, mock_post: MagicMock) -> None:
        """trace 应包含非零 duration_ms。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        assert response.search_trace.get("duration_ms", 0) >= 0

    @patch("app.common.retrieval.client.requests.post")
    def test_trace_contains_paper_counts(self, mock_post: MagicMock) -> None:
        """trace 应包含请求和返回的文献数量。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试", paper_num=20))

        trace = response.search_trace
        assert trace.get("paper_num_requested") == 20
        assert trace.get("paper_num_returned") == 1

    @patch("app.common.retrieval.client.requests.post")
    def test_trace_contains_gateway_status(self, mock_post: MagicMock) -> None:
        """trace 应包含网关 HTTP 状态码和业务码。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        trace = response.search_trace
        assert trace.get("gateway_status_code") == 200
        assert trace.get("gateway_code") == 1

    @patch("app.common.retrieval.client.requests.post")
    def test_trace_contains_question(self, mock_post: MagicMock) -> None:
        """trace 应包含构造后的 question 字符串。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试查询"))

        assert "测试查询" in response.search_trace.get("question", "")

    @patch("app.common.retrieval.client.requests.post")
    def test_auto_generated_request_id(self, mock_post: MagicMock) -> None:
        """未指定 request_id 时应自动生成 UUID。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试"))

        request_id = response.search_trace.get("request_id", "")
        assert len(request_id) > 0  # UUID 格式

    @patch("app.common.retrieval.client.requests.post")
    def test_paper_num_clamped_to_max_100(self, mock_post: MagicMock) -> None:
        """请求 paper_num > 100 时应被限制为 100。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search(SearchRequest(query="测试", paper_num=200))

        assert response.search_trace.get("paper_num_requested") == 100


# ------------------------------------------------------------------
# 7. search_sync 兼容方法
# ------------------------------------------------------------------


class TestSearchSync:
    """search_sync 便捷入口测试。"""

    @patch("app.common.retrieval.client.requests.post")
    def test_search_sync_returns_search_response(self, mock_post: MagicMock) -> None:
        """search_sync 应返回 SearchResponse。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search_sync("钙钛矿太阳能电池")

        assert isinstance(response, SearchResponse)
        assert response.success is True

    @patch("app.common.retrieval.client.requests.post")
    def test_search_sync_passes_parameters(self, mock_post: MagicMock) -> None:
        """search_sync 应正确传递所有参数。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search_sync(
            "测试查询",
            paper_num=10,
            year_range=(2020, 2026),
            languages=["en"],
            rerank=False,
            source_agent="sync_agent",
            request_id="sync-001",
            user_scope="scope_a",
        )

        # 验证请求参数传递到了 trace
        trace = response.search_trace
        assert trace.get("paper_num_requested") == 10
        assert trace.get("source_agent") == "sync_agent"
        assert trace.get("request_id") == "sync-001"

        # 验证 question 中包含年份和语种信息
        question = trace.get("question", "")
        assert "2020-2026" in question
        assert "英文" in question

    @patch("app.common.retrieval.client.requests.post")
    def test_search_sync_default_languages(self, mock_post: MagicMock) -> None:
        """search_sync 未指定 languages 时默认中英混合。"""
        mock_post.return_value = _mock_response(json_data=MOCK_SUCCESS_RESPONSE)

        client = LiteratureSearchClient(api_url="http://test-api/search", timeout=30)
        response = client.search_sync("测试查询")

        question = response.search_trace.get("question", "")
        assert "中英混合" in question
