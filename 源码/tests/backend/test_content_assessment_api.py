import asyncio
import copy
import pytest

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import routes
from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import ContentAssessmentState, ReviewTask
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.core import config as core_config
from app.main import app


class FakeTaskRepository:
    def __init__(self):
        self.records: dict[str, dict] = {}

    async def create(self, record):
        self.records[record["task_id"]] = record
        return record["task_id"]

    async def get(self, task_id, _user_scope):
        return self.records.get(task_id)

    async def list_tasks(self, _user_scope, **_kwargs):
        return list(self.records.values()), len(self.records)

    async def update(self, task_id, _user_scope, changes):
        self.records[task_id].update(changes)
        return True

    async def update_report(self, task_id, _user_scope, expected_updated_at, fields):
        record = self.records[task_id]
        if record.get("updated_at", "") != expected_updated_at:
            return False
        for path, value in fields.items():
            target = record
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        return True

    async def delete(self, task_id, _user_scope):
        return self.records.pop(task_id, None) is not None

    async def list_running(self, _user_scope):
        return []

    async def delete_many(self, _task_ids, _user_scope):
        return 0


@pytest.mark.parametrize("template", [None, "ContentAssessmentReportReferenceV1", "ContentAssessmentResultReportV4"])
def test_export_keeps_raw_frozen_snapshot_and_reads_legacy_without_writes(monkeypatch, template):
    from app.agents.content_assessment.schemas import ReportSnapshot, ReportSectionSpec
    from app.services.export_service import ensure_snapshot
    repository = FakeTaskRepository()
    state = ContentAssessmentState(raw_user_input="旧材料", current_stage="completed")
    state.report_plan.section_specs = [ReportSectionSpec(section_key="old", title="原结论", content="原结论 [7]")]
    if template:
        state.report_snapshot = ReportSnapshot(snapshot_id="original", generated_at="2026-09-01",
            template_version=template, sections=state.report_plan.section_specs, citation_numbers={"E-old": 7})
    record = {"task_id": "old-task", "status": "completed", "updated_at": "original-date", "state": state.model_dump()}
    if template:
        record["state"]["report_snapshot"]["legacy_extra"] = {"preserve": True}
    repository.records["old-task"] = record
    before = copy.deepcopy(record)
    def render(working):
        ensure_snapshot(working)
        working.report_metrics.page_count_verified = True
        return "原结论 [7]", "原报告.md"
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    monkeypatch.setattr(routes, "build_markdown", render)
    with TestClient(app) as client:
        response = client.get("/v1/content-assessment/tasks/old-task/export?format=md", headers={"X-User-Scope": "user_001"})
    assert response.status_code == 200
    assert response.text == "原结论 [7]"
    assert record["state"]["report_snapshot"] == before["state"]["report_snapshot"]
    if template != "ContentAssessmentResultReportV4":
        assert record == before


async def _do_not_start_worker(*_args, **_kwargs):
    return None


class AllowingRateLimiter:
    def __init__(self) -> None:
        self.released: list[str] = []

    async def check_and_acquire(self, _scope, _task_id):
        return True, ""

    async def release(self, scope, _task_id):
        self.released.append(scope)


class CapturingTopicManager:
    def __init__(self) -> None:
        self.rate_limiter = AllowingRateLimiter()
        self.started: list[tuple[str, str, object]] = []

    async def start_pipeline(self, task_id, scope, state):
        self.started.append((task_id, scope, state))


class CapturingTopicService:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def create_initial_state(self, user_input, **kwargs):
        self.kwargs = kwargs
        return TopicSelectionAgent().create_initial_state(user_input, **kwargs)


def test_topic_review_publishes_only_after_all_reviews_resolve(monkeypatch) -> None:
    repository = FakeTaskRepository()
    state = TopicSelectionAgent().create_initial_state("复核发布闭环测试")
    state.current_stage = "completed"
    state.quality_gate.report_eligibility = "formal"
    state.quality_gate.review_required = True
    state.report_plan.publication_status = "eligible"
    state.review_tasks = [
        {"review_task_id": "review-1", "status": "open", "title": "复核一"},
        {"review_task_id": "review-2", "status": "open", "title": "复核二"},
    ]
    repository.records["topic-review"] = {
        "task_id": "topic-review", "task_type": "智能选题", "user_scope": "reviewer",
        "status": "awaiting_review", "created_at": "2026-09-21T10:00:00+08:00",
        "state": state.model_dump(),
    }
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.services.topic_task_persistence.get_task_history_repo", lambda: repository)

    with TestClient(app) as client:
        first = client.post(
            "/v1/topic-selection/tasks/topic-review/reviews/review-1",
            headers={"X-User-Scope": "reviewer"}, json={"decision": "approve"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "awaiting_review"
        assert repository.records["topic-review"]["state"]["report_plan"]["publication_status"] == "eligible"

        second = client.post(
            "/v1/topic-selection/tasks/topic-review/reviews/review-2",
            headers={"X-User-Scope": "reviewer"}, json={"decision": "approve"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "completed"
        persisted = repository.records["topic-review"]["state"]
        assert persisted["quality_gate"]["review_required"] is False
        assert persisted["report_plan"]["publication_status"] == "published"


def test_topic_review_return_marks_awaiting_correction_without_rerun(monkeypatch) -> None:
    repository = FakeTaskRepository()
    state = TopicSelectionAgent().create_initial_state("退回补正行为测试")
    state.current_stage = "completed"
    state.quality_gate.report_eligibility = "formal"
    state.quality_gate.review_required = True
    state.report_plan.publication_status = "eligible"
    state.review_tasks = [{"review_task_id": "review-1", "status": "open", "title": "边界复核"}]
    repository.records["topic-correction"] = {
        "task_id": "topic-correction", "task_type": "智能选题", "user_scope": "reviewer",
        "status": "awaiting_review", "created_at": "2026-09-21T10:00:00+08:00",
        "state": state.model_dump(),
    }
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.services.topic_task_persistence.get_task_history_repo", lambda: repository)

    with TestClient(app) as client:
        missing = client.post(
            "/v1/topic-selection/tasks/topic-correction/reviews/review-1",
            headers={"X-User-Scope": "reviewer"}, json={"decision": "return_to_retrieval"},
        )
        assert missing.status_code == 422
        returned = client.post(
            "/v1/topic-selection/tasks/topic-correction/reviews/review-1",
            headers={"X-User-Scope": "reviewer"},
            json={"decision": "return_to_retrieval", "reason": "补充实验边界与验证样本"},
        )
    assert returned.status_code == 200
    assert returned.json()["status"] == "awaiting_correction"
    persisted = repository.records["topic-correction"]
    assert persisted["status"] == "awaiting_correction"
    assert persisted["state"]["current_stage"] == "awaiting_correction"
    assert persisted["state"]["resume_from_stage"] == ""
    assert persisted["state"]["review_tasks"][0]["status"] == "returned"


def test_local_default_user_scope_can_load_task_history(monkeypatch) -> None:
    repository = FakeTaskRepository()
    monkeypatch.setattr(core_config, "ALLOW_DEFAULT_USER_SCOPE", True)
    monkeypatch.setattr(routes, "ALLOW_DEFAULT_USER_SCOPE", True)
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)

    with TestClient(app) as client:
        response = client.get("/v1/content-assessment/tasks", headers={"X-User-Scope": "default"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_create_history_sse_export_and_old_route_absence(monkeypatch) -> None:
    repository = FakeTaskRepository()
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.services.task_persistence.get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.core.task_manager.TaskManager.start_pipeline", _do_not_start_worker)
    with TestClient(app) as client:
        created = client.post("/v1/content-assessment/run", headers={"X-User-Scope": "user_001"}, json={
            "user_input": "拟构建多器官芯片培养系统并提高稳定性。", "assessment_title": "芯片系统评估",
        })
        assert created.status_code == 200
        task_id = created.json()["run_id"]
        record = repository.records[task_id]
        record["status"] = "completed"
        completed = asyncio.run(ContentAssessmentGraph().run(ContentAssessmentGraph().create_initial_state("拟构建多器官芯片培养系统。")))
        from app.agents.content_assessment.schemas import ResearchNineFields
        completed.nine_fields = ResearchNineFields(research_direction='微流控', research_content='多器官芯片培养', key_technologies=['灌流控制'])
        completed.nine_fields_confirmed = True
        completed.report_preferences.artifact_type = completed.report_preferences.resolved_artifact_type = 'project'
        completed.output_json['input_fields_supplemented'] = True
        completed.confirmation.needed = False
        completed.confirmation.selected_option = "continue_with_assumptions"
        completed.current_stage = completed.resume_from_stage
        completed = asyncio.run(ContentAssessmentGraph().run(completed))
        # Export/history contract uses a completed frozen historical dossier.
        # Source/drafting failure is exercised separately by scholarly report tests.
        from report_rich_fixture import rich_state
        completed = rich_state("standard")
        record.update({"state": completed.model_dump(), "stage_trace": [item.model_dump() for item in completed.stage_trace], "output_markdown": completed.output_markdown, "updated_at": "2026-08-28T10:00:00+08:00"})
        history = client.get("/v1/content-assessment/tasks", headers={"X-User-Scope": "user_001"})
        assert history.status_code == 200
        assert history.json()["items"][0]["total_steps"] == 8
        stream = client.get(f"/v1/content-assessment/tasks/{task_id}/stream", headers={"X-User-Scope": "user_001"})
        assert stream.status_code == 200
        assert "event: complete" in stream.text
        exported = client.get(f"/v1/content-assessment/tasks/{task_id}/export?format=md", headers={"X-User-Scope": "user_001"})
        assert exported.status_code == 200
        assert "逐项比对与创新性分析" in exported.text
        assert "filename*=UTF-8''" in exported.headers["content-disposition"]
        assert "content-assessment-report.md" in exported.headers["content-disposition"]
        assert exported.headers["access-control-expose-headers"] == "Content-Disposition"
        exported_pdf = client.get(f"/v1/content-assessment/tasks/{task_id}/export?format=pdf", headers={"X-User-Scope": "user_001"})
        assert exported_pdf.status_code == 200
        assert exported_pdf.headers["content-type"].startswith("application/pdf")
        assert exported_pdf.content.startswith(b"%PDF")
        assert exported_pdf.headers["content-disposition"].startswith("attachment;")
        inline_pdf = client.get(f"/v1/content-assessment/tasks/{task_id}/export?format=pdf&disposition=inline", headers={"X-User-Scope": "user_001"})
        assert inline_pdf.status_code == 200
        assert inline_pdf.headers["content-disposition"].startswith("inline;")
        assert inline_pdf.content == exported_pdf.content
        assert client.get(f"/v1/content-assessment/tasks/{task_id}/export?format=docx&disposition=inline", headers={"X-User-Scope": "user_001"}).status_code == 422
        assert repository.records[task_id]["state"]["report_metrics"]["page_count_verified"] is True
        assert repository.records[task_id]["state"]["report_metrics"]["page_count_source"].startswith("pdf:libreoffice:")
        old_path = "/v1/" + "good" + "-" + "question/run"
        assert client.post(old_path, headers={"X-User-Scope": "user_001"}, json={"user_input": "x"}).status_code == 404


def test_confirmation_endpoint_resumes_from_persisted_pause(monkeypatch) -> None:
    repository = FakeTaskRepository()
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.services.task_persistence.get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.core.task_manager.TaskManager.start_pipeline", _do_not_start_worker)
    monkeypatch.setattr("app.core.task_manager.TaskManager.start_pipeline", _do_not_start_worker)
    monkeypatch.setattr("app.core.task_manager.get_task_history_repo", lambda: repository)
    with TestClient(app) as client:
        created = client.post(
            "/v1/content-assessment/run",
            headers={"X-User-Scope": "user_confirmation"},
            json={"user_input": "拟构建多器官芯片培养系统。"},
        )
        assert created.status_code == 200
        task_id = created.json()["run_id"]
        paused = asyncio.run(ContentAssessmentGraph().run(
            ContentAssessmentGraph().create_initial_state("拟构建多器官芯片培养系统。")
        ))
        repository.records[task_id].update({
            "status": "awaiting_confirmation",
            "state": paused.model_dump(),
            "stage_trace": [item.model_dump() for item in paused.stage_trace],
        })
        response = client.post(
            f"/v1/content-assessment/tasks/{task_id}/confirmation",
            headers={"X-User-Scope": "user_confirmation"},
            json={
                "selected_option": "supplement_fields",
                "artifact_type": "project",
                "nine_fields": {"research_direction": "微流控", "research_content": "多器官芯片培养", "key_technologies": ["灌流控制"]},
                "user_note": "确认按当前范围继续",
                "scope_summary": "仅评估长期培养稳定性与微血管屏障功能。",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "running"
        assert repository.records[task_id]["status"] == "queued"
        assert repository.records[task_id]["state"]["confirmation"]["needed"] is False
        assert repository.records[task_id]["state"]["resume_from_stage"] == "claim_extraction"
        resumed_state = repository.records[task_id]["state"]
        assert resumed_state["assessment_profile"]["scope_summary"] == "仅评估长期培养稳定性与微血管屏障功能。"
        assert resumed_state["assessment_profile"]["scope_focus"] == "focused"
        assert resumed_state["boundary_matrix"]["included_scope"][0] == "仅评估长期培养稳定性与微血管屏障功能。"


def test_review_endpoint_persists_resolution_and_exposes_review_history(monkeypatch) -> None:
    repository = FakeTaskRepository()
    state = ContentAssessmentState(
        raw_user_input="test",
        review_tasks=[ReviewTask(
            review_task_id="RV-001",
            review_type="strong_assertion",
            claim_id="C-001",
            title="复核强断言",
            reason="需要专家限定表述",
        )],
    )
    repository.records["task-review"] = {
        "task_id": "task-review", "user_scope": "user_review", "status": "completed",
        "state": state.model_dump(), "stage_trace": [],
    }
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.services.task_persistence.get_task_history_repo", lambda: repository)
    monkeypatch.setattr("app.core.task_manager.TaskManager.start_pipeline", _do_not_start_worker)
    monkeypatch.setattr("app.core.task_manager.get_task_history_repo", lambda: repository)
    with TestClient(app) as client:
        response = client.post(
            "/v1/content-assessment/tasks/task-review/reviews/RV-001",
            headers={"X-User-Scope": "user_review"},
            json={"decision": "limit", "reason": "改为目标性表述", "operator": "expert-1"},
        )
        assert response.status_code == 200
        assert repository.records["task-review"]["state"]["review_tasks"][0]["status"] == "limited"
        assert repository.records["task-review"]["state"]["resolution_logs"][0]["operator"] == "expert-1"
        history = client.get(
            "/v1/content-assessment/tasks/task-review/reviews",
            headers={"X-User-Scope": "user_review"},
        )
        assert history.status_code == 200
        assert history.json()["items"][0]["review_task_id"] == "RV-001"


def test_topic_selection_has_no_generic_regenerate_or_evidence_zip_api() -> None:
    contracts = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    assert ("/api/v1/topic-selection/tasks/{task_id}/regenerate", "POST") not in contracts
    topic_export = next(route for route in app.routes if route.path.endswith("/v1/topic-selection/tasks/{task_id}/export"))
    format_parameter = next(param for param in topic_export.dependant.query_params if param.name == "format")
    assert "zip" not in str(format_parameter.field_info)
