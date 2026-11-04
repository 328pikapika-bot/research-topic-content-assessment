import asyncio
import json

from app.core.model_profiles import ModelProfile
from app.services.deepseek_client import DeepSeekClient


def _sse(delta: dict) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False)


class FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStreamClient:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.calls: list[dict] = []

    def stream(self, method, url, *, json, headers, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeStreamResponse(self.lines)

    async def aclose(self) -> None:
        return None


def _client_with_stream(lines: list[str]) -> tuple[DeepSeekClient, FakeStreamClient]:
    profile = ModelProfile(
        name="test_stream",
        base_url="https://example.test/v1",
        api_key="test-key",
        model="deepseek-reasoner",
        timeout=10,
    )
    client = DeepSeekClient(model_profile=profile)
    fake = FakeStreamClient(lines)
    client._client = fake  # type: ignore[assignment]
    return client, fake


def test_stream_json_collects_content_and_skips_reasoning() -> None:
    lines = [
        _sse({"reasoning_content": "先想一下"}),
        _sse({"content": '{"ok": '}),
        _sse({"content": "true}"}),
        "data: [DONE]",
    ]
    client, fake = _client_with_stream(lines)

    result = asyncio.run(
        client.generate_json_with_metrics(
            system_prompt="system",
            user_prompt="user",
            stream=True,
            max_tokens=512,
            first_chunk_timeout_seconds=1,
            max_retries=0,
        )
    )

    assert result.payload == {"ok": True}
    assert result.metrics.first_reasoning_ms is not None
    assert result.metrics.first_content_ms is not None
    assert result.metrics.reasoning_chars == len("先想一下")
    assert result.metrics.content_chars == len('{"ok": true}')
    assert fake.calls[0]["json"]["stream"] is True
    assert fake.calls[0]["json"]["max_tokens"] == 512


def test_stream_json_filters_legacy_think_tags() -> None:
    lines = [
        _sse({"content": "<think>hidden reasoning</think>"}),
        _sse({"content": '{"ok": true}'}),
        "data: [DONE]",
    ]
    client, _fake = _client_with_stream(lines)

    result = asyncio.run(
        client.generate_json_with_metrics(
            system_prompt="system",
            user_prompt="user",
            stream=True,
            max_tokens=512,
            first_chunk_timeout_seconds=1,
            max_retries=0,
        )
    )

    assert result.payload == {"ok": True}


def test_parse_json_extracts_object_from_wrapped_text() -> None:
    client, _fake = _client_with_stream([])

    payload = client._parse_json_content(
        '说明文字\n```json\n{"bad": false}\n```\n最终结果：{"ok": true, "items": [1, 2]}'
    )

    assert payload == {"bad": False}


def test_parse_json_prefers_largest_candidate_when_multiple_objects() -> None:
    client, _fake = _client_with_stream([])

    payload = client._parse_json_content('prefix {"small": true} suffix {"ok": true, "nested": {"x": 1}}')

    assert payload == {"ok": True, "nested": {"x": 1}}


def test_parse_json_rejects_truncated_outer_object_and_complete_inner_item() -> None:
    client, _fake = _client_with_stream([])

    import pytest
    for text in ('{"ok": true, "items": ["a", "b"', '{"gaps": [{"id": "G1"}], "cross_opportunities": ['):
        with pytest.raises(Exception):
            client._parse_json_content(text)
