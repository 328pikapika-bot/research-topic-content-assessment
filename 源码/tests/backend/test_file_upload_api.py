from fastapi.testclient import TestClient
import pytest

from app.api import routes
from app.main import app


class FakeParsedFileRepo:
    def __init__(self) -> None:
        self.records = []

    async def batch_create(self, records):
        self.records.extend(records)
        return len(records), [], records


async def _skip_sensitive_check(text: str, filename: str) -> None:
    return None


def test_upload_text_file_parses_and_persists(monkeypatch, tmp_path) -> None:
    repo = FakeParsedFileRepo()
    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "get_parsed_file_repo", lambda: repo)
    monkeypatch.setattr(routes, "check_sensitive_content", _skip_sensitive_check)

    with TestClient(app) as client:
        response = client.post(
            "/v1/files/upload",
            files={"file": ("paper.txt", b"quantum sensing research question", "text/plain")},
            headers={"X-User-Scope": "user_001"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["fileId"].startswith("file_")
    assert body["data"]["status"] == "parsed"
    assert repo.records[0]["content"] == "quantum sensing research question"
    assert repo.records[0]["source"] == "server_upload"
    assert body["data"]["sensitive_check_status"] == "passed"


def test_upload_ignores_files_from_previous_tasks(monkeypatch, tmp_path) -> None:
    repo = FakeParsedFileRepo()
    repo.records = [{"file_id": f"old_{index}", "user_scope": "user_001"} for index in range(12)]
    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "get_parsed_file_repo", lambda: repo)
    monkeypatch.setattr(routes, "check_sensitive_content", _skip_sensitive_check)

    with TestClient(app) as client:
        response = client.post(
            "/v1/files/upload",
            files={"file": ("new.txt", b"new research material", "text/plain")},
            headers={"X-User-Scope": "user_001"},
        )

    assert response.status_code == 200
    assert len(repo.records) == 13


def test_legacy_batch_ignores_files_from_previous_tasks_but_limits_request(monkeypatch) -> None:
    repo = FakeParsedFileRepo()
    repo.records = [{"file_id": f"old_{index}", "user_scope": "user_001"} for index in range(12)]
    monkeypatch.setattr(routes, "get_parsed_file_repo", lambda: repo)
    monkeypatch.setattr(routes, "check_sensitive_content", _skip_sensitive_check)
    record = {"fileId": "new_1", "name": "new.txt", "type": "txt", "content": "research"}

    with TestClient(app) as client:
        accepted = client.post("/v1/parsed-files/batch", json={"files": [record]}, headers={"X-User-Scope": "user_001"})
        rejected = client.post("/v1/parsed-files/batch", json={"files": [record] * 11}, headers={"X-User-Scope": "user_001"})

    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert len(repo.records) == 13


def test_upload_pdf_with_no_extracted_text_returns_friendly_error(monkeypatch, tmp_path) -> None:
    async def empty_pdf_content(file_path, file_ext):
        return ""

    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "parse_uploaded_file_content", empty_pdf_content)
    monkeypatch.setattr(routes, "check_sensitive_content", _skip_sensitive_check)

    with TestClient(app) as client:
        response = client.post(
            "/v1/files/upload",
            files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")},
            headers={"X-User-Scope": "user_001"},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "pdf_no_text_content"
    assert "OCR" in detail["message"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure, expected_status, expected_code", [
    (routes.SensitiveContentError("blocked", hits=[{"word": "blocked"}]), 400, "sensitive_content_detected"),
    (routes.SensitiveCheckUnavailable("unavailable"), 503, "sensitive_check_failed"),
])
def test_upload_safety_failures_do_not_persist(monkeypatch, tmp_path, failure, expected_status, expected_code):
    repo = FakeParsedFileRepo()
    async def reject(text, filename):
        raise failure
    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "get_parsed_file_repo", lambda: repo)
    monkeypatch.setattr(routes, "check_sensitive_content", reject)
    with TestClient(app) as client:
        response = client.post("/v1/files/upload", files={"file": ("sample.txt", b"research")}, headers={"X-User-Scope": "user_001"})
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code
    assert response.json()["detail"]["stage"] == "sensitive_check"
    assert not repo.records
    assert not list(tmp_path.iterdir())


def test_parse_failure_does_not_call_sensitive_service(monkeypatch, tmp_path):
    async def unexpected_check(text, filename):
        pytest.fail("A corrupt file should fail before checking content safety")
    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "check_sensitive_content", unexpected_check)
    with TestClient(app) as client:
        response = client.post("/v1/files/upload", files={"file": ("broken.docx", b"bad docx")}, headers={"X-User-Scope": "user_001"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "docx_parse_failed"
    assert response.json()["detail"]["stage"] == "parse"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("endpoint", ["/v1/parsed-files", "/v1/parsed-files/batch"])
def test_legacy_client_upload_cannot_bypass_sensitive_check(monkeypatch, endpoint):
    repo = FakeParsedFileRepo()
    async def reject(text, filename):
        raise routes.SensitiveContentError("blocked")
    monkeypatch.setattr(routes, "get_parsed_file_repo", lambda: repo)
    monkeypatch.setattr(routes, "check_sensitive_content", reject)
    record = {"fileId": "legacy_1", "name": "sample.txt", "type": "txt", "content": "blocked"}
    payload = {"files": [record]} if endpoint.endswith("batch") else {"file": record}
    with TestClient(app) as client:
        response = client.post(endpoint, json=payload, headers={"X-User-Scope": "user_001"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "sensitive_content_detected"
    assert not repo.records
