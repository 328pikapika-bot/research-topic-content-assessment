from fastapi.testclient import TestClient

from app.api import routes
from app.main import app


class FakeFeedbackRepo:
    def __init__(self) -> None:
        self.records = []

    async def create(self, record):
        self.records.append(record)
        return record["feedback_id"]


def test_feedback_submit_persists_record(monkeypatch, tmp_path) -> None:
    repo = FakeFeedbackRepo()
    monkeypatch.setattr(routes, "get_feedback_repo", lambda: repo)
    monkeypatch.setattr(routes, "FEEDBACK_UPLOAD_DIR", str(tmp_path))

    with TestClient(app) as client:
        response = client.post(
            "/api/service/nstl/web/user-feedback/submit",
            data={
                "systemName": "content-assessment",
                "content": "任务详情页结果区域希望增加导出提示。",
                "contact": "user@example.com",
                "pageUrl": "https://www.nstl.gov.cn/content-assessment/tasks/1",
            },
            files={
                "files": ("screenshot.png", b"\x89PNG\r\n\x1a\n", "image/png"),
            },
            headers={"X-User-Scope": "user_001"},
        )

    assert response.status_code == 200
    assert response.json()["code"] == 200
    assert len(repo.records) == 1

    record = repo.records[0]
    assert record["system_name"] == "content-assessment"
    assert record["content"] == "任务详情页结果区域希望增加导出提示。"
    assert record["contact"] == "user@example.com"
    assert record["user_scope"] == "user_001"
    assert record["status"] == "submitted"
    assert len(record["attachments"]) == 1
    assert record["attachments"][0]["original_name"] == "screenshot.png"


def test_feedback_submit_requires_content(monkeypatch) -> None:
    monkeypatch.setattr(routes, "get_feedback_repo", lambda: FakeFeedbackRepo())

    with TestClient(app) as client:
        response = client.post(
            "/api/service/nstl/web/user-feedback/submit",
            data={"systemName": "content-assessment", "content": " "},
            headers={"X-User-Scope": "user_001"},
        )

    assert response.status_code == 422
