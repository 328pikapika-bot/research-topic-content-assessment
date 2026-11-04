from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
MODEL_PROFILE_SELECTOR_PATH = RUNTIME_DIR / "model_profile.txt"

DEFAULT_MODEL_PROFILE_NAME = "server_v4_flash"
PERSONAL_MODEL_PROFILE_NAME = "personal_v4_flash"
SERVER_V4_FLASH_PROFILE_NAME = "server_v4_flash"
OFFICIAL_R1_PROFILE_NAME = "official_r1_reasoner"
OFFICIAL_FAST_PROFILE_NAME = "official_fast_chat"
LEGACY_ENV_MODEL_PROFILE_NAME = "legacy_env"

_LEGACY_API_KEY_ENV = "CONTENT_ASSESSMENT_DEEPSEEK_API_KEY"
_LEGACY_BASE_URL_ENV = "CONTENT_ASSESSMENT_DEEPSEEK_BASE_URL"
_LEGACY_MODEL_ENV = "CONTENT_ASSESSMENT_DEEPSEEK_MODEL"
_LEGACY_TIMEOUT_ENV = "CONTENT_ASSESSMENT_DEEPSEEK_TIMEOUT"

_OFFICIAL_R1_API_KEY_ENV = "CONTENT_ASSESSMENT_OFFICIAL_R1_API_KEY"
_OFFICIAL_R1_BASE_URL_ENV = "CONTENT_ASSESSMENT_OFFICIAL_R1_BASE_URL"
_OFFICIAL_R1_MODEL_ENV = "CONTENT_ASSESSMENT_OFFICIAL_R1_MODEL"
_OFFICIAL_R1_TIMEOUT_ENV = "CONTENT_ASSESSMENT_OFFICIAL_R1_TIMEOUT"

_OFFICIAL_FAST_API_KEY_ENV = "CONTENT_ASSESSMENT_OFFICIAL_FAST_API_KEY"
_OFFICIAL_FAST_BASE_URL_ENV = "CONTENT_ASSESSMENT_OFFICIAL_FAST_BASE_URL"
_OFFICIAL_FAST_MODEL_ENV = "CONTENT_ASSESSMENT_OFFICIAL_FAST_MODEL"
_OFFICIAL_FAST_TIMEOUT_ENV = "CONTENT_ASSESSMENT_OFFICIAL_FAST_TIMEOUT"

_SERVER_LLM_API_KEY_ENV = "CONTENT_ASSESSMENT_LLM_API_KEY"
_SERVER_LLM_BASE_URL_ENV = "CONTENT_ASSESSMENT_LLM_API_URL"
_SERVER_LLM_MODEL_ENV = "CONTENT_ASSESSMENT_LLM_MODEL_NAME"
_SERVER_LLM_TIMEOUT_ENV = "CONTENT_ASSESSMENT_LLM_TIMEOUT_SECONDS"

_GENERIC_LLM_API_KEY_ENV = "LLM_API_KEY"
_GENERIC_LLM_BASE_URL_ENV = "LLM_API_URL"
_GENERIC_LLM_MODEL_ENV = "LLM_MODEL_NAME"

_PERSONAL_API_KEY_ENV = "CONTENT_ASSESSMENT_PERSONAL_API_KEY"
_PERSONAL_BASE_URL_ENV = "CONTENT_ASSESSMENT_PERSONAL_BASE_URL"
_PERSONAL_MODEL_ENV = "CONTENT_ASSESSMENT_PERSONAL_MODEL"


@dataclass(frozen=True)
class ModelProfile:
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float
    source: str = "preset"
    json_response_format: bool = True


def _get_first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


MODEL_PROFILES: dict[str, ModelProfile] = {
    PERSONAL_MODEL_PROFILE_NAME: ModelProfile(
        name=PERSONAL_MODEL_PROFILE_NAME,
        base_url=os.getenv(_PERSONAL_BASE_URL_ENV, "https://api.deepseek.com").rstrip("/"),
        api_key=os.getenv(_PERSONAL_API_KEY_ENV, "").strip(),
        model=os.getenv(_PERSONAL_MODEL_ENV, "deepseek-v4-flash").strip() or "deepseek-v4-flash",
        timeout=90.0,
        source="env",
    ),
    OFFICIAL_R1_PROFILE_NAME: ModelProfile(
        name=OFFICIAL_R1_PROFILE_NAME,
        base_url=os.getenv(_OFFICIAL_R1_BASE_URL_ENV, "https://api.deepseek.com/v1").rstrip("/"),
        api_key=_get_first_env(_OFFICIAL_R1_API_KEY_ENV, _LEGACY_API_KEY_ENV),
        model=os.getenv(_OFFICIAL_R1_MODEL_ENV, "deepseek-reasoner").strip() or "deepseek-reasoner",
        timeout=float(os.getenv(_OFFICIAL_R1_TIMEOUT_ENV, "180")),
        source="env",
    ),
    OFFICIAL_FAST_PROFILE_NAME: ModelProfile(
        name=OFFICIAL_FAST_PROFILE_NAME,
        base_url=os.getenv(_OFFICIAL_FAST_BASE_URL_ENV, "https://api.deepseek.com/v1").rstrip("/"),
        api_key=_get_first_env(
            _OFFICIAL_FAST_API_KEY_ENV,
            _OFFICIAL_R1_API_KEY_ENV,
            _LEGACY_API_KEY_ENV,
        ),
        model=os.getenv(_OFFICIAL_FAST_MODEL_ENV, "deepseek-chat").strip() or "deepseek-chat",
        timeout=float(os.getenv(_OFFICIAL_FAST_TIMEOUT_ENV, "60")),
        source="env",
    ),
}


def list_model_profile_names() -> tuple[str, ...]:
    return (SERVER_V4_FLASH_PROFILE_NAME, *MODEL_PROFILES.keys())


def write_model_profile_selector(
    profile_name: str,
    selector_path: Path | None = None,
) -> Path:
    if profile_name not in list_model_profile_names():
        raise ValueError(f"Unsupported model profile: {profile_name}")

    target_path = selector_path or MODEL_PROFILE_SELECTOR_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(f"{profile_name}\n", encoding="utf-8")
    return target_path


def read_selected_model_profile_name(selector_path: Path | None = None) -> str:
    target_path = selector_path or MODEL_PROFILE_SELECTOR_PATH
    if not target_path.exists():
        return DEFAULT_MODEL_PROFILE_NAME

    selected = target_path.read_text(encoding="utf-8").strip().lstrip("﻿")
    if not selected:
        return DEFAULT_MODEL_PROFILE_NAME
    if selected in list_model_profile_names() or selected == LEGACY_ENV_MODEL_PROFILE_NAME:
        return selected

    logger.warning(
        "Unknown model profile `%s` in `%s`; falling back to `%s`.",
        selected,
        target_path,
        DEFAULT_MODEL_PROFILE_NAME,
    )
    return DEFAULT_MODEL_PROFILE_NAME


def resolve_model_profile(profile_name: str) -> ModelProfile:
    if profile_name == LEGACY_ENV_MODEL_PROFILE_NAME:
        return _resolve_legacy_env_profile()
    if profile_name == SERVER_V4_FLASH_PROFILE_NAME:
        return _resolve_server_v4_flash_profile()
    if profile_name == PERSONAL_MODEL_PROFILE_NAME:
        return _resolve_personal_v4_flash_profile()
    if profile_name == OFFICIAL_R1_PROFILE_NAME:
        return _resolve_official_r1_profile()
    if profile_name == OFFICIAL_FAST_PROFILE_NAME:
        return _resolve_official_fast_profile()

    try:
        profile = MODEL_PROFILES[profile_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported model profile: {profile_name}") from exc

    _warn_if_legacy_env_config_is_present(profile_name)
    return profile


def _resolve_server_v4_flash_profile() -> ModelProfile:
    """Resolve the NSTL server model without embedding credentials in source code."""
    return ModelProfile(
        name=SERVER_V4_FLASH_PROFILE_NAME,
        base_url=_get_first_env(
            _SERVER_LLM_BASE_URL_ENV,
            _GENERIC_LLM_BASE_URL_ENV,
            _OFFICIAL_FAST_BASE_URL_ENV,
        ).rstrip("/") or "http://127.0.0.1:16703/v1",
        api_key=_get_first_env(
            _SERVER_LLM_API_KEY_ENV,
            _GENERIC_LLM_API_KEY_ENV,
            _OFFICIAL_FAST_API_KEY_ENV,
        ),
        model=_get_first_env(
            _SERVER_LLM_MODEL_ENV,
            _GENERIC_LLM_MODEL_ENV,
            _OFFICIAL_FAST_MODEL_ENV,
        ) or "deepseek-v4-flash",
        timeout=float(os.getenv(_SERVER_LLM_TIMEOUT_ENV, "90")),
        json_response_format=False,
        source="env",
    )


def _resolve_personal_v4_flash_profile() -> ModelProfile:
    return ModelProfile(
        name=PERSONAL_MODEL_PROFILE_NAME,
        base_url=os.getenv(_PERSONAL_BASE_URL_ENV, "https://api.deepseek.com").rstrip("/"),
        api_key=os.getenv(_PERSONAL_API_KEY_ENV, "").strip(),
        model=os.getenv(_PERSONAL_MODEL_ENV, "deepseek-v4-flash").strip() or "deepseek-v4-flash",
        timeout=90.0,
        source="env",
    )


def _resolve_official_r1_profile() -> ModelProfile:
    return ModelProfile(
        name=OFFICIAL_R1_PROFILE_NAME,
        base_url=os.getenv(_OFFICIAL_R1_BASE_URL_ENV, "https://api.deepseek.com/v1").rstrip("/"),
        api_key=_get_first_env(_OFFICIAL_R1_API_KEY_ENV, _LEGACY_API_KEY_ENV),
        model=os.getenv(_OFFICIAL_R1_MODEL_ENV, "deepseek-reasoner").strip() or "deepseek-reasoner",
        timeout=float(os.getenv(_OFFICIAL_R1_TIMEOUT_ENV, "180")),
        source="env",
    )


def _resolve_official_fast_profile() -> ModelProfile:
    return ModelProfile(
        name=OFFICIAL_FAST_PROFILE_NAME,
        base_url=os.getenv(_OFFICIAL_FAST_BASE_URL_ENV, "https://api.deepseek.com/v1").rstrip("/"),
        api_key=_get_first_env(
            _OFFICIAL_FAST_API_KEY_ENV,
            _OFFICIAL_R1_API_KEY_ENV,
            _LEGACY_API_KEY_ENV,
        ),
        model=os.getenv(_OFFICIAL_FAST_MODEL_ENV, "deepseek-chat").strip() or "deepseek-chat",
        timeout=float(os.getenv(_OFFICIAL_FAST_TIMEOUT_ENV, "60")),
        source="env",
    )


def resolve_active_model_profile(selector_path: Path | None = None) -> ModelProfile:
    return resolve_model_profile(read_selected_model_profile_name(selector_path))


def _warn_if_legacy_env_config_is_present(profile_name: str) -> None:
    if not _has_legacy_env_config():
        return
    logger.warning(
        "Detected deprecated DeepSeek environment variables, but model profile `%s` "
        "is active. The built-in profile selector takes precedence.",
        profile_name,
    )


def _resolve_legacy_env_profile() -> ModelProfile:
    api_key = os.getenv(_LEGACY_API_KEY_ENV, "").strip()
    if not api_key:
        raise ValueError(
            "Model profile `legacy_env` requires CONTENT_ASSESSMENT_DEEPSEEK_API_KEY."
        )

    logger.warning(
        "Model profile `legacy_env` is deprecated. Please migrate to the built-in "
        "profile selector file at `%s`.",
        MODEL_PROFILE_SELECTOR_PATH,
    )
    return ModelProfile(
        name=LEGACY_ENV_MODEL_PROFILE_NAME,
        base_url=os.getenv(_LEGACY_BASE_URL_ENV, "https://api.deepseek.com").rstrip("/"),
        api_key=api_key,
        model=os.getenv(_LEGACY_MODEL_ENV, "deepseek-chat").strip() or "deepseek-chat",
        timeout=float(os.getenv(_LEGACY_TIMEOUT_ENV, "90")),
        source="legacy_env",
    )


def _has_legacy_env_config() -> bool:
    return any(
        os.getenv(key, "").strip()
        for key in (
            _LEGACY_API_KEY_ENV,
            _LEGACY_BASE_URL_ENV,
            _LEGACY_MODEL_ENV,
            _LEGACY_TIMEOUT_ENV,
        )
    )
