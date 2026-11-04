"""Runtime configuration for the innovation assessment agent."""

from __future__ import annotations

import logging
import os
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

BEIJING_TZ = timezone(timedelta(hours=8))


def setup_logging() -> None:
    log_dir = Path(os.getenv("LOG_DIR", "./data/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(log_dir / "app.log", encoding="utf-8")):
        handler.setFormatter(formatter)
        root.addHandler(handler)


MONGO_ENV = os.getenv("MONGO_ENV", "").strip().lower()
MONGO_LOCAL_HOST = os.getenv("MONGO_LOCAL_HOST", "127.0.0.1")
MONGO_TEST_HOST = os.getenv("MONGO_TEST_HOST", "127.0.0.1")
MONGO_LOCAL_PORT = os.getenv("MONGO_LOCAL_PORT", os.getenv("MONGO_PORT", "27017"))
MONGO_TEST_PORT = os.getenv("MONGO_TEST_PORT", os.getenv("MONGO_PORT", "27017"))
if MONGO_ENV in {"local", "本地"}:
    MONGO_HOST, MONGO_PORT = MONGO_LOCAL_HOST, MONGO_LOCAL_PORT
elif MONGO_ENV in {"test", "testing", "测试"}:
    MONGO_HOST, MONGO_PORT = MONGO_TEST_HOST, MONGO_TEST_PORT
else:
    MONGO_HOST, MONGO_PORT = os.getenv("MONGO_HOST", "127.0.0.1"), os.getenv("MONGO_PORT", "27017")
MONGO_DB = os.getenv("MONGO_DATABASE", "content_assessment_agent")
MONGO_USERNAME = os.getenv("MONGO_USERNAME", os.getenv("MONGO_USER", ""))
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_AUTH_SOURCE = os.getenv("MONGO_AUTH_SOURCE", "admin")


def _mongo_uri(redacted: bool = False) -> str:
    if not (MONGO_USERNAME and MONGO_PASSWORD):
        return f"mongodb://{MONGO_HOST}:{MONGO_PORT}"
    password = "***" if redacted else quote_plus(MONGO_PASSWORD)
    return f"mongodb://{quote_plus(MONGO_USERNAME)}:{password}@{MONGO_HOST}:{MONGO_PORT}/?authSource={quote_plus(MONGO_AUTH_SOURCE)}"


MONGO_URI = _mongo_uri()
MONGO_URI_SAFE = _mongo_uri(redacted=True)

BACKEND_PORT = int(os.getenv("BACKEND_PORT", "17607"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")
LOG_DIR = os.getenv("LOG_DIR", "./data/logs")
MAX_CONCURRENT_TASKS_PER_USER = int(os.getenv("MAX_CONCURRENT_TASKS_PER_USER", "5"))
MAX_CONCURRENT_TASKS_GLOBAL = int(os.getenv("MAX_CONCURRENT_TASKS_GLOBAL", "16"))
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:18093,http://localhost:18093,https://www.nstl.gov.cn")


def get_allowed_origins() -> list[str]:
    return [value.strip() for value in ALLOWED_ORIGINS.split(",") if value.strip()]


TASK_COLLECTION = os.getenv("CONTENT_ASSESSMENT_TASK_COLLECTION", "content_assessment_tasks")
EVENT_COLLECTION = os.getenv("CONTENT_ASSESSMENT_EVENT_COLLECTION", "content_assessment_task_events")
PARSED_FILE_COLLECTION = os.getenv("CONTENT_ASSESSMENT_PARSED_FILE_COLLECTION", "content_assessment_parsed_files")
FEEDBACK_COLLECTION = os.getenv("CONTENT_ASSESSMENT_FEEDBACK_COLLECTION", "content_assessment_feedback")
FEEDBACK_UPLOAD_DIR = os.getenv("CONTENT_ASSESSMENT_FEEDBACK_UPLOAD_DIR", str(Path(UPLOAD_DIR) / "feedback"))
MAX_UPLOAD_FILE_COUNT = int(os.getenv("CONTENT_ASSESSMENT_MAX_UPLOAD_FILE_COUNT", "10"))
MAX_UPLOAD_FILE_SIZE_BYTES = int(
    os.getenv("CONTENT_ASSESSMENT_MAX_UPLOAD_FILE_SIZE_BYTES", str(50 * 1024 * 1024))
)
MAX_PDF_PARSE_PAGES = int(os.getenv("CONTENT_ASSESSMENT_MAX_PDF_PARSE_PAGES", "200"))
MAX_PARSED_FILE_CONTENT_CHARS = int(os.getenv("CONTENT_ASSESSMENT_MAX_PARSED_FILE_CONTENT_CHARS", "300000"))
MAX_FILE_CONTEXT_TOTAL_CHARS = int(os.getenv("CONTENT_ASSESSMENT_MAX_FILE_CONTEXT_TOTAL_CHARS", "30000"))
MAX_FILE_CONTEXT_PER_FILE_CHARS = int(os.getenv("CONTENT_ASSESSMENT_MAX_FILE_CONTEXT_PER_FILE_CHARS", "6000"))
SENSITIVE_CHECK_API_URL = os.getenv(
    "SENSITIVE_CHECK_API_URL",
    "http://127.0.0.1:16673/api/v1/ai-search/sensitive/text",
)
SENSITIVE_CHECK_CHUNK_SIZE = int(os.getenv("CONTENT_ASSESSMENT_SENSITIVE_CHECK_CHUNK_SIZE", "16384"))
SENSITIVE_CHECK_TIMEOUT_SECONDS = float(os.getenv("CONTENT_ASSESSMENT_SENSITIVE_CHECK_TIMEOUT_SECONDS", "10"))
LITERATURE_SEARCH_API_URL = os.getenv("LITERATURE_SEARCH_API_URL", "").strip()
LITERATURE_SEARCH_TIMEOUT = int(os.getenv("LITERATURE_SEARCH_TIMEOUT", "600"))
LITERATURE_FULLTEXT_API_URL = os.getenv("LITERATURE_FULLTEXT_API_URL", "").strip()
LITERATURE_FULLTEXT_API_TOKEN = os.getenv("LITERATURE_FULLTEXT_API_TOKEN", "").strip()
LITERATURE_FULLTEXT_TIMEOUT = int(os.getenv("LITERATURE_FULLTEXT_TIMEOUT", "60"))
CONTENT_ASSESSMENT_LLM_ENABLED = os.getenv("CONTENT_ASSESSMENT_LLM_ENABLED", "true").strip().lower() == "true"
CONTENT_ASSESSMENT_LLM_TIMEOUT_SECONDS = float(os.getenv("CONTENT_ASSESSMENT_LLM_TIMEOUT_SECONDS", "90"))
CONTENT_ASSESSMENT_LLM_MAX_RETRIES = int(os.getenv("CONTENT_ASSESSMENT_LLM_MAX_RETRIES", "0"))
CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES = os.getenv("CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES", "false").strip().lower() == "true"
ALLOW_DEFAULT_USER_SCOPE = os.getenv("ALLOW_DEFAULT_USER_SCOPE", "false").strip().lower() == "true"


def is_valid_user_scope(scope: str | None) -> bool:
    value = (scope or "").strip()
    if not value:
        return False
    if ALLOW_DEFAULT_USER_SCOPE:
        return True
    lowered = value.lower()
    return lowered not in {"default", "unknown", "mock_user_id", "test_user", "test_account"} and not lowered.startswith(("mock_", "fake_", "test_", "nstl_user_"))
