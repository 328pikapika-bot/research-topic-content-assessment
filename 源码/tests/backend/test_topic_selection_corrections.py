"""Correction workflow regressions with independent repository snapshots and CAS."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import routes
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import TopicReviewRequest, TopicSelectionState


class Repository:
    def __init__(self):
        self.records = {}
        self.fail_create = False
        self.finalize_conflicts = 0
        self.cancel_after_create = False

    async def get(self, key, scope):
        return deepcopy(self.records.get(key))

    async def create(self, record):
        if self.fail_create:
            raise RuntimeError("create failed")
        self.records[record["task_id"]] = deepcopy(record)
        if self.cancel_after_create:
            self.records["original"]["status"] = "cancelled"
            self.records["original"]["updated_at"] = "cancelled-at"

    async def update(self, key, scope, fields):
        self.records[key].update(deepcopy(fields))
        return True

    async def update_report(self, key, scope, expected, fields):
        record = self.records[key]
        if fields.get("status") == "superseded" and self.finalize_conflicts:
            self.finalize_conflicts -= 1
            record["updated_at"] += "-concurrent"
            return False
        if record.get("updated_at", "") != expected:
            return False
        for path, value in fields.items():
            target = record
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(value)
        return True

    async def list_tasks(self, scope, **_kwargs):
        items = [deepcopy(value) for value in self.records.values() if value.get("user_scope") == scope]
        return items, len(items)


class Manager:
    def __init__(self, fail_start=False):
        self.rate_limiter = self
        self.started = []
        self.released = []
        self.fail_start = fail_start

    def is_running(self, task_id):
        return False

    async def check_and_acquire(self, scope, key):
        return True, ""

    async def release(self, scope, key):
        self.released.append(key)

    async def start_pipeline(self, key, scope, state):
        if self.fail_start:
            raise RuntimeError("worker failed")
        self.started.append((key, state.model_copy(deep=True)))


@pytest.fixture
def correction_repo(monkeypatch):
    repo = Repository()
    state = TopicSelectionAgent().create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")
    for trace in state.stage_trace:
        trace.status = "completed"
        trace.progress_percent = 100
    state.current_stage = "completed"
    state.quality_gate.review_required = True
    state.quality_gate.report_eligibility = "formal"
    state.review_tasks = [{"review_task_id": "review-1", "status": "open", "title": "边界复核"}]
    state.report_plan.sections = [{"key": "old", "title": "旧报告", "content": "旧产物"}]
    state.output_markdown = "旧报告正文"
    state.versions.evidence_pool_version = "old-evidence"
    state.versions.report_version = "old-report"
    state.stage_artifacts = {"input_modeling": {"analysis": "保留"}, "retrieval_evidence": {"analysis": "清空"}}
    repo.records["original"] = {"task_id": "original", "task_type": "智能选题", "user_scope": "reviewer", "status": "awaiting_review", "updated_at": "initial", "created_at": "2026-10-08T10:00:00+08:00", "state": state.model_dump()}
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repo)
    monkeypatch.setattr("app.services.topic_task_persistence.get_task_history_repo", lambda: repo)
    return repo


async def return_task(manager):
    return await routes.resolve_topic_review("original", "review-1", TopicReviewRequest(decision="return_to_retrieval", reason="限定老年人群，并补充热暴露全文证据", operator="测试复核人"), "reviewer", manager)


async def rerun(manager):
    request = Request({"type": "http", "app": type("App", (), {"state": type("State", (), {"model_health": {}})()})()})
    return await routes.rerun_topic_correction("original", request, "reviewer", manager)


def test_complete_correction_flow_retains_original_clears_stale_outputs_and_is_idempotent(correction_repo):
    async def scenario():
        manager = Manager()
        returned = await return_task(manager)
        assert returned["status"] == "awaiting_correction"
        assert not manager.started
        with pytest.raises(HTTPException) as duplicate:
            await routes.resolve_topic_review("original", "review-1", TopicReviewRequest(decision="approve"), "reviewer", manager)
        assert duplicate.value.status_code == 409
        run = await rerun(manager)
        original = correction_repo.records["original"]
        assert original["status"] == "superseded"
        assert original["state"]["output_markdown"] == "旧报告正文"
        assert original["state"]["correction_context"]["correction_task_id"] == run.run_id
        state = run.state
        assert state.parent_task_id == "original"
        assert state.correction_context.source_task_id == "original"
        assert "老年人群" in state.task_profile["correction_reason"]
        assert any("老年人群" in item for item in state.constraint_card.must_include)
        assert state.resume_from_stage == "retrieval_evidence"
        assert all(item.status == "completed" for item in state.stage_trace[:2])
        assert all(item.status == "pending" for item in state.stage_trace[2:])
        assert not state.evidence_pool and not state.evidence_chunks and not state.candidates
        assert not state.report_plan.sections and not state.output_markdown
        assert not state.query_plan.branches and not state.evidence_lock.locked
        assert state.versions.evidence_pool_version == "evidence-v0"
        assert state.versions.parent_report_version == "old-report"
        assert set(state.stage_artifacts) == {"input_modeling"}
        repeated = await rerun(manager)
        assert repeated.run_id == run.run_id
        assert len(manager.started) == 1 and len(correction_repo.records) == 2
        # A correction is independently reviewable; it may itself be returned again.
        state.review_tasks = [{"review_task_id": "review-2", "status": "open"}]
        correction_repo.records[run.run_id].update(status="awaiting_review", state=state.model_dump())
        result = await routes.resolve_topic_review(run.run_id, "review-2", TopicReviewRequest(decision="return_to_retrieval", reason="再补充样本边界"), "reviewer", manager)
        assert result["status"] == "awaiting_correction"
    asyncio.run(scenario())


def test_retrieval_revision_keeps_completed_parent_and_clears_scope_filters(correction_repo):
    async def scenario():
        correction_repo.records["original"]["status"] = "completed"
        original_snapshot = deepcopy(correction_repo.records["original"])
        manager = Manager()
        request = Request({"type": "http", "app": type("App", (), {"state": type("State", (), {"model_health": {}})()})()})
        run = await routes.rerun_topic_retrieval("original", request, "reviewer", manager)
        assert correction_repo.records["original"] == original_snapshot
        assert run.state.parent_task_id == "original"
        assert run.state.resume_from_stage == "retrieval_evidence"
        assert run.state.input_profile.time_range == []
        assert run.state.input_profile.time_range_source == "unspecified"
        assert run.state.input_profile.languages == []
        assert all(item.status == "completed" for item in run.state.stage_trace[:2])
        assert all(item.status == "pending" for item in run.state.stage_trace[2:])
        repeated = await routes.rerun_topic_retrieval("original", request, "reviewer", manager)
        assert repeated.run_id == run.run_id
        assert len(manager.started) == 1
    asyncio.run(scenario())


def test_review_requires_reason_and_approve_prevents_return(correction_repo):
    async def scenario():
        manager = Manager()
        with pytest.raises(HTTPException) as missing:
            await routes.resolve_topic_review("original", "review-1", TopicReviewRequest(decision="return_to_retrieval", reason="  "), "reviewer", manager)
        assert missing.value.status_code == 422
        assert correction_repo.records["original"]["status"] == "awaiting_review"
        await routes.resolve_topic_review("original", "review-1", TopicReviewRequest(decision="approve"), "reviewer", manager)
        with pytest.raises(HTTPException) as duplicate:
            await return_task(manager)
        assert duplicate.value.status_code == 409
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["create", "start", "cancel", "cas_retry", "cas_exhausted"])
def test_correction_failures_never_leave_orphaned_queued_children(correction_repo, failure):
    async def scenario():
        manager = Manager(fail_start=failure == "start")
        await return_task(manager)
        correction_repo.fail_create = failure == "create"
        correction_repo.cancel_after_create = failure == "cancel"
        correction_repo.finalize_conflicts = {"cas_retry": 1, "cas_exhausted": 4}.get(failure, 0)
        if failure == "cas_retry":
            await rerun(manager)
            assert correction_repo.records["original"]["status"] == "superseded"
            assert len(manager.started) == 1
            return
        with pytest.raises(HTTPException) as error:
            await rerun(manager)
        assert error.value.status_code == (409 if failure in {"cancel", "cas_exhausted"} else 500)
        children = [value for key, value in correction_repo.records.items() if key != "original"]
        assert not children or all(item["status"] == "failed" for item in children)
        assert not manager.started and manager.released
        if failure == "create":
            assert not correction_repo.records["original"]["state"]["correction_context"]["correction_task_id"]
    asyncio.run(scenario())


def test_concurrent_correction_requests_create_one_child(correction_repo):
    async def scenario():
        manager = Manager()
        await return_task(manager)
        # Make both requests read the same original snapshot before either CAS.
        initial_read = correction_repo.get
        both_read = asyncio.Event()
        read_count = 0
        async def synchronized_get(key, scope):
            nonlocal read_count
            snapshot = await initial_read(key, scope)
            if key == "original" and read_count < 2:
                read_count += 1
                if read_count == 2:
                    both_read.set()
                await both_read.wait()
            return snapshot
        correction_repo.get = synchronized_get
        results = await asyncio.gather(rerun(manager), rerun(manager), return_exceptions=True)
        successful = [item for item in results if not isinstance(item, Exception)]
        assert successful
        assert len({item.run_id for item in successful}) == 1
        assert len(manager.started) == 1
        assert len(correction_repo.records) == 2
    asyncio.run(scenario())
