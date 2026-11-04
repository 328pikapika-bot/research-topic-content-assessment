import asyncio
import logging
from pathlib import Path

import httpx

from app.core.model_profiles import (
    DEFAULT_MODEL_PROFILE_NAME,
    MODEL_PROFILE_SELECTOR_PATH,
    OFFICIAL_FAST_PROFILE_NAME,
    OFFICIAL_R1_PROFILE_NAME,
    resolve_active_model_profile,
    resolve_model_profile,
    write_model_profile_selector,
)
from app.services.deepseek_client import DeepSeekClient


class RecordingAsyncClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def post(self, url, *, json, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": self.content}}]},
            request=httpx.Request("POST", url),
        )

    async def aclose(self) -> None:
        return None


def _clear_legacy_env(monkeypatch) -> None:
    for key in (
        "CONTENT_ASSESSMENT_DEEPSEEK_API_KEY",
        "CONTENT_ASSESSMENT_DEEPSEEK_BASE_URL",
        "CONTENT_ASSESSMENT_DEEPSEEK_MODEL",
        "CONTENT_ASSESSMENT_DEEPSEEK_TIMEOUT",
        "CONTENT_ASSESSMENT_OFFICIAL_R1_API_KEY",
        "CONTENT_ASSESSMENT_OFFICIAL_R1_BASE_URL",
        "CONTENT_ASSESSMENT_OFFICIAL_R1_MODEL",
        "CONTENT_ASSESSMENT_OFFICIAL_R1_TIMEOUT",
        "CONTENT_ASSESSMENT_OFFICIAL_FAST_API_KEY",
        "CONTENT_ASSESSMENT_OFFICIAL_FAST_BASE_URL",
        "CONTENT_ASSESSMENT_OFFICIAL_FAST_MODEL",
        "CONTENT_ASSESSMENT_OFFICIAL_FAST_TIMEOUT",
        "CONTENT_ASSESSMENT_LLM_API_KEY",
        "CONTENT_ASSESSMENT_LLM_API_URL",
        "CONTENT_ASSESSMENT_LLM_MODEL_NAME",
        "CONTENT_ASSESSMENT_PERSONAL_API_KEY",
        "CONTENT_ASSESSMENT_PERSONAL_BASE_URL",
        "CONTENT_ASSESSMENT_PERSONAL_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_missing_selector_defaults_to_server_v4_flash(monkeypatch, tmp_path: Path) -> None:
    _clear_legacy_env(monkeypatch)
    selector_path = tmp_path / "missing-model-profile.txt"

    profile = resolve_active_model_profile(selector_path)

    assert profile.name == DEFAULT_MODEL_PROFILE_NAME
    assert profile.base_url == "http://127.0.0.1:16703/v1"
    assert profile.model == "deepseek-v4-flash"


def test_write_selector_can_switch_to_personal_profile(monkeypatch, tmp_path: Path) -> None:
    _clear_legacy_env(monkeypatch)
    selector_path = tmp_path / "model_profile.txt"

    written_path = write_model_profile_selector("personal_v4_flash", selector_path)
    profile = resolve_active_model_profile(selector_path)

    assert written_path == selector_path
    assert selector_path.read_text(encoding="utf-8").strip() == "personal_v4_flash"
    assert profile.name == "personal_v4_flash"
    assert profile.base_url == "https://api.deepseek.com"
    assert profile.model == "deepseek-v4-flash"


def test_invalid_selector_falls_back_to_default(monkeypatch, tmp_path: Path) -> None:
    _clear_legacy_env(monkeypatch)
    selector_path = tmp_path / "model_profile.txt"
    selector_path.write_text("unknown_profile\n", encoding="utf-8")

    profile = resolve_active_model_profile(selector_path)

    assert profile.name == DEFAULT_MODEL_PROFILE_NAME


def test_selector_accepts_utf8_bom(monkeypatch, tmp_path: Path) -> None:
    _clear_legacy_env(monkeypatch)
    selector_path = tmp_path / "model_profile.txt"
    selector_path.write_text("\ufeffserver_v4_flash\n", encoding="utf-8")

    profile = resolve_active_model_profile(selector_path)

    assert profile.name == DEFAULT_MODEL_PROFILE_NAME


def test_official_profiles_are_resolved_from_env(monkeypatch) -> None:
    _clear_legacy_env(monkeypatch)
    monkeypatch.setenv("CONTENT_ASSESSMENT_OFFICIAL_R1_API_KEY", "r1-key")
    monkeypatch.setenv("CONTENT_ASSESSMENT_OFFICIAL_R1_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CONTENT_ASSESSMENT_OFFICIAL_R1_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("CONTENT_ASSESSMENT_OFFICIAL_FAST_API_KEY", "fast-key")
    monkeypatch.setenv("CONTENT_ASSESSMENT_OFFICIAL_FAST_MODEL", "deepseek-chat")

    r1_profile = resolve_model_profile(OFFICIAL_R1_PROFILE_NAME)
    fast_profile = resolve_model_profile(OFFICIAL_FAST_PROFILE_NAME)

    assert r1_profile.base_url == "https://example.test/v1"
    assert r1_profile.model == "deepseek-reasoner"
    assert r1_profile.api_key == "r1-key"
    assert fast_profile.model == "deepseek-chat"
    assert fast_profile.api_key == "fast-key"


def test_model_profile_selector_path_points_to_runtime_file() -> None:
    assert MODEL_PROFILE_SELECTOR_PATH.as_posix().endswith("/.runtime/model_profile.txt")


def test_deepseek_client_uses_selected_profile_for_requests(monkeypatch, caplog) -> None:
    _clear_legacy_env(monkeypatch)
    monkeypatch.setenv("CONTENT_ASSESSMENT_LLM_API_KEY", "server-test-key")
    monkeypatch.setenv("CONTENT_ASSESSMENT_PERSONAL_API_KEY", "personal-test-key")

    for profile_name, expected_url, expected_model in (
        ("server_v4_flash", "http://127.0.0.1:16703/v1/chat/completions", "deepseek-v4-flash"),
        (
            "personal_v4_flash",
            "https://api.deepseek.com/chat/completions",
            "deepseek-v4-flash",
        ),
    ):
        profile = resolve_model_profile(profile_name)

        client = DeepSeekClient(model_profile=profile)
        recorder = RecordingAsyncClient('{"ok": true}')
        client._client = recorder  # type: ignore[assignment]

        caplog.clear()
        caplog.set_level(logging.INFO, logger="app.services.deepseek_client")

        result = asyncio.run(
            client.generate_json(system_prompt="system", user_prompt="user prompt")
        )

        assert result == {"ok": True}
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["url"] == expected_url
        assert call["json"]["model"] == expected_model
        assert call["headers"]["Authorization"].startswith("Bearer ")
        assert f"profile={profile_name}" in caplog.text
