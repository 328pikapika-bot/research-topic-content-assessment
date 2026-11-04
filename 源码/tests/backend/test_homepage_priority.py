import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import routes
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.material_extraction import material_context
from app.agents.content_assessment.schemas import ContentAssessmentRunRequest, ResearchNineFields, UploadedFileRef
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.core import task_manager, topic_task_manager
from app.core.rate_limiter import RateLimiter, TaskSlotUnavailable, TaskStateChanged
from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository
from app.services import nine_fields_preview, task_persistence, topic_task_persistence


def complete_fields():
    return ResearchNineFields(research_direction="方向", research_goal="目标", research_content="内容",
        technical_route="路线", key_technologies=["技术"], evaluation_indicators=["指标"],
        timeline="周期", application_scenarios=["场景"], expected_outcomes=[])


@pytest.fixture
def repository(monkeypatch):
    repo = InMemoryTaskHistoryRepository()
    for module in (routes, task_persistence, topic_task_persistence, task_manager, topic_task_manager):
        monkeypatch.setattr(module, "get_task_history_repo", lambda: repo)
    return repo


@pytest.fixture
def api(repository, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.state.task_manager = task_manager.TaskManager()
    app.state.topic_task_manager = topic_task_manager.TopicTaskManager()
    for manager in (app.state.task_manager, app.state.topic_task_manager):
        manager._rate_limiter = RateLimiter(max_per_user=5, max_global=50)
        monkeypatch.setattr(manager, "start_pipeline", AsyncMock())
    with TestClient(app) as client:
        yield client, app


def test_nine_fields_first_eight_required_ninth_optional_legacy_compatible():
    fields = complete_fields()
    assert fields.missing_required() == []
    assert ContentAssessmentRunRequest(user_input="手填", nine_fields=fields).nine_fields.expected_outcomes == []
    assert ContentAssessmentRunRequest(user_input="旧客户端").nine_fields is None
    # Explicit type uses conditional fields. Legacy auto is accepted until
    # the material type has been recognized by the execution pipeline.
    for name in ('research_direction', 'research_content'):
        incomplete = fields.model_dump()
        incomplete[name] = ' '
        with pytest.raises(ValidationError):
            ContentAssessmentRunRequest(user_input="手填", artifact_type="project", nine_fields=incomplete)
    no_tech = fields.model_dump()
    no_tech.update(technical_route='', key_technologies=[])
    with pytest.raises(ValidationError):
        ContentAssessmentRunRequest(user_input="手填", artifact_type="project", nine_fields=no_tech)


def test_scenario_id_is_allowlisted():
    request = ContentAssessmentRunRequest(user_input="案例", scenario_id="assessment-battery")
    assert request.scenario_id == "assessment-battery"
    with pytest.raises(ValidationError):
        ContentAssessmentRunRequest(user_input="案例", scenario_id="unknown-case")


@pytest.mark.parametrize("active_status", ["queued", "running"])
def test_run_preserves_confirmed_fields_and_rejects_sixth_per_agent(api, repository, active_status):
    client, app = api
    headers = {"X-User-Scope": "alice"}
    body = {"artifact_type": "project", "user_input": "手填方案", "nine_fields": complete_fields().model_dump()}
    assert client.post("/v1/content-assessment/run", headers=headers, json={**body, "nine_fields": {}}).status_code == 422
    for _ in range(5):
        result = client.post("/v1/content-assessment/run", headers=headers, json=body)
        assert result.status_code == 200
        state = result.json()["state"]
        assert state["nine_fields_confirmed"] is True
        assert state["nine_fields"] == body["nine_fields"]
        assert state["raw_user_input"] == body["user_input"]
        assert "技术路线：路线" not in state["raw_user_input"]
        if active_status == "running":
            asyncio.run(repository.update(result.json()["run_id"], "alice", {"status": "running"}))
    denied = client.post("/v1/content-assessment/run", headers=headers, json=body)
    assert denied.status_code == 429
    detail = denied.json()["detail"]
    assert {key: detail[key] for key in ("running", "queued", "occupied", "limit")} == {
        "running": 5 if active_status == "running" else 0,
        "queued": 5 if active_status == "queued" else 0, "occupied": 5, "limit": 5}
    assert app.state.task_manager.start_pipeline.await_count == 5
    topic = client.post("/v1/topic-selection/run", headers=headers,
        json={"user_input": "材料研究机会", "output_preference": "balanced", "year_range": []})
    assert topic.status_code == 200
    topic_state = topic.json()["state"]
    assert topic_state["input_profile"]["desired_candidate_count"] == 5
    assert topic_state["input_profile"]["time_range"] == []
    assert client.post("/v1/content-assessment/run", headers={"X-User-Scope": "bob"}, json=body).status_code == 200


def test_limiter_idempotence_ownership_and_global_limit():
    async def check():
        limiter = RateLimiter(max_per_user=5, max_global=6)
        for i in range(5):
            assert (await limiter.check_and_acquire("alice", str(i)))[0]
        assert (await limiter.check_and_acquire("alice", "0"))[0]
        assert not (await limiter.check_and_acquire("bob", "0"))[0]
        assert not (await limiter.check_and_acquire("alice", "six"))[0]
        await limiter.release("bob", "0")
        assert (await limiter.usage("alice"))["occupied"] == 5
        await limiter.release("alice", "0")
        await limiter.release("alice", "0")
        assert (await limiter.usage("alice"))["occupied"] == 4
        assert (await limiter.check_and_acquire("bob", "b1"))[0]
        assert (await limiter.check_and_acquire("bob", "b2"))[0]
        assert not (await limiter.check_and_acquire("carol", "c"))[0]
    asyncio.run(check())


@pytest.mark.parametrize("kind", ["assessment", "topic"])
def test_pause_release_resume_rechecks_quota_and_stale_state(kind, repository, monkeypatch):
    async def check():
        if kind == "assessment":
            manager = task_manager.TaskManager()
            state = ContentAssessmentGraph().create_initial_state("暂停恢复测试")
            service_class = task_manager.ContentAssessmentService
            task_type = "创新性评估"
        else:
            manager = topic_task_manager.TopicTaskManager()
            state = TopicSelectionAgent().create_initial_state("暂停恢复测试", analysis_mode="fast")
            service_class = topic_task_manager.TopicSelectionService
            task_type = "智能选题"
        manager._rate_limiter = RateLimiter(max_per_user=5, max_global=50)
        async def pause(_self, current, **_kwargs):
            current.current_stage = "awaiting_confirmation"
            current.confirmation.needed = True
            return current
        monkeypatch.setattr(service_class, "run", pause)
        await repository.create({"task_id": "paused", "user_scope": "alice", "task_type": task_type,
            "status": "queued", "created_at": "2026-10-01T00:00:00+08:00", "updated_at": "original"})
        await manager.rate_limiter.check_and_acquire("alice", "paused")
        await manager.start_pipeline("paused", "alice", state)
        await asyncio.gather(manager._running_tasks["paused"])
        assert (await manager.rate_limiter.usage("alice"))["occupied"] == 0
        paused = await repository.get("paused", "alice")
        assert paused["status"] == "awaiting_confirmation"
        for i in range(5):
            await manager.rate_limiter.check_and_acquire("alice", f"busy-{i}")
        with pytest.raises(TaskSlotUnavailable):
            await manager.resume_pipeline("paused", "alice", state,
                expected_status=paused["status"], expected_updated_at=paused["updated_at"])
        assert (await repository.get("paused", "alice")) == paused
        await manager.rate_limiter.release("alice", "busy-0")
        monkeypatch.setattr(manager, "start_pipeline", AsyncMock())
        await manager.resume_pipeline("paused", "alice", state,
            expected_status=paused["status"], expected_updated_at=paused["updated_at"])
        assert (await repository.get("paused", "alice"))["status"] == "queued"
        assert (await manager.rate_limiter.usage("alice"))["occupied"] == 5
        with pytest.raises(TaskStateChanged):
            await manager.resume_pipeline("paused", "alice", state,
                expected_status=paused["status"], expected_updated_at=paused["updated_at"])
        await manager.release_slot("alice", "paused")
        assert (await manager.rate_limiter.usage("alice"))["occupied"] == 4
    asyncio.run(check())


@pytest.mark.parametrize("manager_class,initial_state", [
    (task_manager.TaskManager, lambda: ContentAssessmentGraph().create_initial_state("取消测试")),
    (topic_task_manager.TopicTaskManager, lambda: TopicSelectionAgent().create_initial_state("取消测试")),
])
def test_cancel_before_worker_entry_releases_slot(manager_class, initial_state):
    async def check():
        manager = manager_class()
        await manager.rate_limiter.check_and_acquire("alice", "new")
        await manager.start_pipeline("new", "alice", initial_state())
        assert await manager.cancel_task("new")
        assert not manager.is_running("new")
        assert (await manager.rate_limiter.usage("alice"))["occupied"] == 0
        assert not await manager.cancel_task("new")
    asyncio.run(check())


def test_unified_history_over_100_real_states_pagination_and_user_isolation(api, repository):
    client, _app = api
    statuses = ["running", "processing", "queued", "awaiting_confirmation", "awaiting_review", "awaiting_correction"]
    async def seed():
        for i in range(126):
            await repository.create({"task_id": str(i), "user_scope": "alice", "status": statuses[i % 6],
                "task_type": "智能选题" if i % 2 else "创新性评估", "title": f"历史任务{i}",
                "summary": "needle" if i == 1 else "普通任务", "created_at": "2026-10-01T12:00:00+08:00"})
        await repository.create({"task_id": "private", "user_scope": "bob", "status": "queued"})
    asyncio.run(seed())
    headers = {"X-User-Scope": "alice"}
    for status, expected in [("all", 126), ("running", 63), ("queued", 21), ("awaiting_confirmation", 21), ("awaiting_review", 21), ("awaiting_correction", 21)]:
        data = client.get("/v1/tasks", headers=headers, params={"status": status, "page_size": 10}).json()
        assert data["total"] == expected
        assert len(data["items"]) == 10
        assert data["statistics"]["total"] == 126
        assert data["quotas"]["assessment"]["running"] == 21
        assert data["quotas"]["topic"]["queued"] == 0
        if status != "all":
            assert all(t["status"] in (["queued", "running", "processing"] if status == "running" else [status]) for t in data["items"])
    assert client.get("/v1/tasks", headers=headers, params={"page": 7, "page_size": 20}).json()["total"] == 126
    assert len(client.get("/v1/tasks", headers=headers, params={"page": 7, "page_size": 20}).json()["items"]) == 6
    assert client.get("/v1/tasks", headers=headers, params={"agent": "topic"}).json()["total"] == 63
    assert client.get("/v1/tasks", headers=headers, params={"search": "needle"}).json()["total"] == 1
    assert client.get("/v1/tasks", headers=headers, params={"created_after": "2026-10-02T00:00:00+08:00"}).json()["total"] == 0
    assert client.get("/v1/tasks", headers={"X-User-Scope": "bob"}).json()["total"] == 1


def test_stop_delete_and_cross_agent_user_protection(api, repository):
    client, app = api
    state = ContentAssessmentGraph().create_initial_state("停止删除测试")
    async def seed():
        for task_id, kind in [("own", "创新性评估"), ("foreign-agent", "智能选题")]:
            await repository.create({"task_id": task_id, "user_scope": "alice", "task_type": kind,
                "status": "queued", "state": state.model_dump()})
            await app.state.task_manager.rate_limiter.check_and_acquire("alice", task_id)
    asyncio.run(seed())
    assert client.post("/v1/content-assessment/tasks/own/stop", headers={"X-User-Scope": "bob"}).status_code == 404
    headers = {"X-User-Scope": "alice"}
    assert client.post("/v1/content-assessment/tasks/foreign-agent/stop", headers=headers).status_code == 404
    assert client.delete("/v1/content-assessment/tasks/foreign-agent", headers=headers).status_code == 404
    assert client.post("/v1/content-assessment/tasks/own/stop", headers=headers).status_code == 200
    assert asyncio.run(repository.get("own", "alice"))["status"] == "cancelled"
    assert asyncio.run(app.state.task_manager.rate_limiter.usage("alice"))["occupied"] == 1
    assert client.post("/v1/content-assessment/tasks/own/confirmation", headers=headers, json={"selected_option": "continue"}).status_code == 409
    assert client.delete("/v1/content-assessment/tasks/own", headers=headers).status_code == 200
    assert asyncio.run(repository.get("own", "alice")) is None


def test_restart_fails_only_orphaned_active_records_preserves_pauses(repository):
    async def check():
        for status in ["queued", "running", "processing", "awaiting_confirmation", "completed"]:
            await repository.create({"task_id": status, "user_scope": "alice", "status": status, "state": None})
        assert await repository.reconcile_interrupted("2026-10-08T00:00:00+08:00") == 3
        assert (await repository.statistics("alice"))["total"] == 5
        assert (await repository.get("queued", "alice"))["status"] == "failed"
        assert (await repository.get("awaiting_confirmation", "alice"))["status"] == "awaiting_confirmation"
        await repository.create({"task_id": "other-worker", "user_scope": "alice", "status": "running",
                                 "updated_at": "2026-10-07T23:55:00+08:00"})
        assert await repository.reconcile_interrupted("2026-10-08T00:00:00+08:00") == 0
        assert (await repository.get("other-worker", "alice"))["status"] == "running"
    asyncio.run(check())


@pytest.mark.parametrize("kind", ["assessment", "topic"])
@pytest.mark.parametrize("resume", [False, True])
def test_worker_start_failure_records_failed_and_releases_slot(kind, resume, repository, monkeypatch):
    async def check():
        manager = task_manager.TaskManager() if kind == "assessment" else topic_task_manager.TopicTaskManager()
        state = (ContentAssessmentGraph().create_initial_state("启动失败测试") if kind == "assessment"
                 else TopicSelectionAgent().create_initial_state("启动失败测试"))
        await repository.create({"task_id": "broken", "user_scope": "alice",
                                 "task_type": "创新性评估" if kind == "assessment" else "智能选题"})
        persist = task_persistence.persist_task_state if kind == "assessment" else topic_task_persistence.persist_topic_task_state
        await persist(task_id="broken", user_scope="alice", state=state,
                      status="awaiting_confirmation" if resume else "queued")
        monkeypatch.setattr(manager, "start_pipeline", AsyncMock(side_effect=RuntimeError("worker failed")))
        with pytest.raises(RuntimeError, match="worker failed"):
            if resume:
                await manager.resume_pipeline("broken", "alice", state)
            else:
                await manager.rate_limiter.check_and_acquire("alice", "broken")
                await routes._start_task(manager, "broken", "alice", state)
        assert (await repository.get("broken", "alice"))["status"] == "failed"
        assert (await manager.rate_limiter.usage("alice"))["occupied"] == 0
        assert not manager.is_running("broken")
    asyncio.run(check())


def test_preview_all_files_owned_no_task_creation_missing_left_empty(api, repository, monkeypatch):
    client, _app = api
    calls = []
    class ParsedRepo:
        async def get(self, file_id, scope, **kwargs):
            calls.append((file_id, scope))
            return {"name": file_id, "content": f"材料-{file_id}"} if scope == "alice" else None
    class Model:
        async def generate_json(self, **kwargs):
            assert all(value in kwargs["user_prompt"] for value in ("材料-one", "材料-two", "补充文字"))
            return {"nine_fields": {"research_direction": "材料方向"}}
        async def aclose(self):
            pass
    monkeypatch.setattr(nine_fields_preview, "get_parsed_file_repo", ParsedRepo)
    monkeypatch.setattr(nine_fields_preview, "DeepSeekClient", Model)
    body = {"user_input": "补充文字", "uploaded_files": [{"file_id": "one", "name": "one.txt"}, {"file_id": "two", "name": "two.txt"}]}
    result = client.post("/v1/content-assessment/preview-nine-fields", headers={"X-User-Scope": "alice"}, json=body)
    assert result.status_code == 200
    assert result.json()["nine_fields"]["research_goal"] == ""
    assert result.json()["missing_fields"] == ["artifact_type"]
    assert calls == [("one", "alice"), ("two", "alice")]
    assert asyncio.run(repository.statistics("alice"))["total"] == 0
    assert client.post("/v1/content-assessment/preview-nine-fields", headers={"X-User-Scope": "bob"}, json=body).status_code == 404


def test_preview_failure_is_editable_and_empty_preview_does_not_call_model(monkeypatch):
    class FailedModel:
        async def generate_json(self, **_kwargs):
            raise RuntimeError("unavailable")
        async def aclose(self):
            pass
    monkeypatch.setattr(nine_fields_preview, "DeepSeekClient", FailedModel)
    result = asyncio.run(nine_fields_preview.preview_nine_fields("材料", [], "alice"))
    assert result["extraction_failed"] is True
    assert result["nine_fields"] == ResearchNineFields().model_dump()
    assert result["warnings"]
    monkeypatch.setattr(nine_fields_preview, "DeepSeekClient", lambda: pytest.fail("Empty preview must not use model"))
    assert asyncio.run(nine_fields_preview.preview_nine_fields("", [], "alice"))["extraction_failed"] is False


def test_explicit_nine_field_brief_is_complete_without_model(monkeypatch):
    monkeypatch.setattr(nine_fields_preview, "DeepSeekClient", lambda: pytest.fail("Labelled brief must not use model"))
    material = """项目名称：可直接评估的推荐案例
研究方向：固态电池材料
研究目标：提升界面稳定性
研究内容：研究电解质与电极界面
技术路线：材料制备、结构表征和全电池验证
关键技术：电解质制备；界面表征；电化学测试
考核指标：离子电导率不低于1 mS/cm；循环300次容量保持率不低于80%
时间节点：第1—6个月筛选材料，第7—12个月验证
应用场景：新能源汽车；储能电池
预期成果：材料样品；性能测试报告"""
    result = asyncio.run(nine_fields_preview.preview_nine_fields(material, [], "alice"))
    assert result["missing_fields"] == []
    assert result["extraction_failed"] is False
    assert result["nine_fields"]["key_technologies"] == ["电解质制备", "界面表征", "电化学测试"]
    assert result["nine_fields"]["expected_outcomes"] == ["材料样品", "性能测试报告"]


def test_confirmed_fields_not_overwritten_by_graph_or_material():
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("研究新技术。应用对象 | 其他场景\n执行周期 | 其他周期")
    state.nine_fields = complete_fields()
    state.nine_fields_confirmed = True
    graph._initialize_nine_fields(state, ["替代技术"], ["替代成果"], state.raw_user_input)
    material_context(state)
    assert state.nine_fields == complete_fields()


def test_topic_old_defaults_and_preferences_never_promote_missing_evidence():
    agent = TopicSelectionAgent()
    assert agent.create_initial_state("旧选题").input_profile.desired_candidate_count == 3
    for preference in ("balanced", "recommended", "exploratory"):
        state = agent.create_initial_state("研究选题", year_range=[], candidate_count=2, output_preference=preference)
        assert state.input_profile.desired_candidate_count == 5
        assert state.input_profile.time_range == []
        from app.agents.topic_selection.schemas import TopicCandidate
        state.candidates = [TopicCandidate(candidate_id="c1", title="候选题", research_question="研究问题", validation_status="evidence_insufficient")]
        agent._apply_output_preference(state)
        assert state.candidates[0].recommendation_level == "pending_evidence"
        if preference != "exploratory":
            assert state.quality_gate.warnings


@pytest.mark.parametrize("preference,recommended_count", [("balanced", 3), ("recommended", 5), ("exploratory", 0)])
def test_topic_preferences_respect_counts_and_distinct_fulltext_evidence(preference, recommended_count):
    from app.agents.topic_selection.schemas import EvidenceRecord, GapScore, TopicCandidate

    agent = TopicSelectionAgent()
    state = agent.create_initial_state("研究选题", output_preference=preference)
    state.quality_gate.passed = True
    state.quality_gate.review_required = False
    state.evidence_pool = [EvidenceRecord(evidence_id=key, fulltext_available=True) for key in ("e1", "e2")]
    state.gaps = [GapScore(gap_id="g1", label="研究空白", validation_status="validated")]
    state.candidates = [TopicCandidate(candidate_id=f"c{i}", title=f"候选{i}", research_question="研究问题",
        validation_status="supported", convergence_valid=True, gap_ids=["g1"], evidence_ids=["e1", "e2"]) for i in range(5)]
    state.candidates.insert(0, TopicCandidate(candidate_id="duplicate", title="重复引用", research_question="研究问题",
        validation_status="supported", convergence_valid=True, gap_ids=["g1"], evidence_ids=["e1", "e1"]))

    agent._apply_output_preference(state)

    assert state.candidates[0].recommendation_level == "exploratory"
    assert sum(item.recommendation_level == "recommended" for item in state.candidates) == recommended_count


@pytest.mark.parametrize('kind,brief', [
    ('paper', '请评估这篇论文，研究方向：文献检索，研究内容：相关性排序，关键方法：图神经网络'),
    ('project', '请评估这个项目，研究方向：电池材料，研究内容：界面稳定性，技术路线：制备和循环测试'),
    ('guideline', '我要研究的是指南，研究目标：建设检索系统，研究内容：语义排序，考核指标：召回率90%，应用场景：数字图书馆'),
])
def test_text_input_conditional_complete_no_extra_fields(kind, brief, monkeypatch):
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', lambda: pytest.fail('Complete brief does not need model'))
    result = asyncio.run(nine_fields_preview.preview_nine_fields(brief, [], 'alice'))
    assert result['resolved_artifact_type'] == kind
    assert result['missing_fields'] == []
    assert not result['extraction_failed']
    if kind in {'paper', 'project'}:
        assert result['nine_fields']['research_goal'] == ''
        assert result['nine_fields']['timeline'] == ''


@pytest.mark.parametrize('brief,kind,missing', [
    ('请评估这篇论文，研究方向：检索，研究内容：排序', 'paper', ['key_technologies']),
    ('请评估这个项目，研究方向：检索，研究内容：排序，研究目标：提高召回率', 'project', ['key_technologies']),
    ('请评估这份指南，研究内容：排序，考核指标：召回率90%，应用场景：数字图书馆', 'guideline', ['research_goal']),
    ('这是材料，研究方向：检索，研究内容：排序，关键方法：图模型', 'unknown', ['artifact_type']),
])
def test_text_input_only_actual_missing_fields(brief, kind, missing, monkeypatch):
    class Model:
        async def generate_json(self, **kwargs):
            return {'nine_fields': {}, 'artifact_type': 'unknown'}
        async def aclose(self): pass
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', Model)
    result = asyncio.run(nine_fields_preview.preview_nine_fields(brief, [], 'alice'))
    assert result['resolved_artifact_type'] == kind
    assert result['missing_fields'] == missing
    assert not result['extraction_failed']


def test_user_object_type_overrides_attached_reference_and_latest_intent(monkeypatch):
    class ParsedRepo:
        async def get(self, *_args, **_kwargs):
            return {'name': '配套指南和参考论文.txt', 'content': '指南名称：配套指南\n研究方向：电池\n研究内容：界面稳定性\n关键技术：循环测试'}
    monkeypatch.setattr(nine_fields_preview, 'get_parsed_file_repo', ParsedRepo)
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', lambda: pytest.fail('Complete attached brief'))
    result = asyncio.run(nine_fields_preview.preview_nine_fields('请评估这篇论文。更正：请评估这个项目，配套指南和参考论文仅供参考。', [UploadedFileRef(file_id='one', name='配套指南.txt')], 'alice'))
    assert result['resolved_artifact_type'] == 'project'
    assert result['missing_fields'] == []


def test_natural_language_model_preview_keeps_only_required_information(monkeypatch):
    class Model:
        async def generate_json(self, **kwargs):
            assert '不要求固定格式' in kwargs['user_prompt']
            return {'artifact_type': 'paper', 'nine_fields': {'research_direction': '语义检索', 'research_content': '评测检索性能', 'key_technologies': ['双编码器']}}
        async def aclose(self): pass
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', Model)
    result = asyncio.run(nine_fields_preview.preview_nine_fields('我们使用双编码器进行文献语义检索，并评测检索性能。', [], 'alice'))
    assert result['resolved_artifact_type'] == 'paper'
    assert result['missing_fields'] == []
    assert result['nine_fields']['technical_route'] == ''


@pytest.mark.parametrize('legacy', [False, True])
def test_supplement_resumes_same_task_and_keeps_raw_material_source(api, repository, monkeypatch, legacy):
    from app.agents.content_assessment.material_extraction import material_sources, locate
    from app.agents.content_assessment.schemas import ContentAssessmentState
    client, app = api
    state = ContentAssessmentGraph().create_initial_state('请评估这个项目')
    state.report_preferences.artifact_type = 'project'
    state.report_preferences.resolved_artifact_type = 'project'
    state.nine_fields = ResearchNineFields(research_direction='检索', research_content='排序')
    state.current_stage = 'awaiting_confirmation'
    state.confirmation.needed = True
    state.confirmation.reason = 'missing_fields'
    state.confirmation.options = [{'value': 'supplement_fields', 'label': '补充并继续'}]
    asyncio.run(repository.create({'task_id': 'supplement-test', 'user_scope': 'alice', 'task_type': '创新性评估', 'status': 'awaiting_confirmation', 'state': state.model_dump(mode='json'), 'updated_at': 'original'}))
    class Model:
        async def generate_json(self, **kwargs): return {'nine_fields': {}, 'artifact_type': 'guideline'}
        async def aclose(self): pass
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', Model)
    supplement = '关键技术：图神经网络检索。配套指南仅用作参考。'
    body = {'selected_option': 'supplement_fields'}
    body.update({'nine_fields': ResearchNineFields(research_direction='检索', research_content='排序', technical_route='图模型').model_dump()} if legacy else {'supplemental_input': supplement})
    response = client.post('/v1/content-assessment/tasks/supplement-test/confirmation', headers={'X-User-Scope': 'alice'}, json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['run_id'] == 'supplement-test'
    assert data['status'] == 'running'
    resumed = ContentAssessmentState.model_validate(data['state'])
    assert resumed.report_preferences.resolved_artifact_type == 'project'
    assert resumed.nine_fields.missing_for_type('project') == []
    assert app.state.task_manager.start_pipeline.await_count == 1
    assert asyncio.run(repository.statistics('alice'))['total'] == 1
    if not legacy:
        source = next(s for s in material_sources(resumed) if s.file_id == 'user_supplement_1')
        assert source.content == supplement
        assert locate(source, '图神经网络检索')['source_char_start'] == supplement.index('图神经网络检索')



def test_text_supplement_overrides_attachment_fields_and_retains_raw_input(monkeypatch):
    from app.agents.content_assessment.material_extraction import material_sources, locate
    from app.agents.content_assessment.schemas import UploadedFileDigest
    class ParsedRepo:
        async def get(self, *_args, **_kwargs):
            return {'name': '材料.txt', 'content': '论文题目：文献检索\n研究方向：原方向\n研究内容：检索排序\n关键方法：向量检索'}
    monkeypatch.setattr(nine_fields_preview, 'get_parsed_file_repo', ParsedRepo)
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', lambda: pytest.fail('Complete mixed material'))
    raw = '请评估这篇论文。\n研究方向：数字图书馆'
    ref = UploadedFileRef(file_id='one', name='材料.txt')
    preview = asyncio.run(nine_fields_preview.preview_nine_fields(raw, [ref], 'alice'))
    assert preview['nine_fields']['research_direction'] == '数字图书馆'
    assert preview['missing_fields'] == []
    state = ContentAssessmentGraph().create_initial_state(raw)
    state.uploaded_files = [ref]
    state.uploaded_file_context.files = [UploadedFileDigest(file_id='one', name='材料.txt', content='研究内容：检索排序')]
    source = next(s for s in material_sources(state) if s.file_id == 'user_material')
    assert locate(source, '数字图书馆')['source_char_start'] == raw.index('数字图书馆')


def test_primary_heading_and_ambiguous_technical_route_do_not_default_project():
    from app.agents.content_assessment.input_rules import infer_artifact_type
    assert infer_artifact_type('论文题目：文献检索\n项目名称：引用的项目', []) == 'paper'
    assert infer_artifact_type('研究方向：检索\n研究内容：排序\n技术路线：向量编码', []) == 'unknown'


def test_incomplete_task_supplement_keeps_same_paused_task(api, repository, monkeypatch):
    client, app = api
    state = ContentAssessmentGraph().create_initial_state('请评估这份指南')
    state.report_preferences.resolved_artifact_type = 'guideline'
    state.nine_fields = ResearchNineFields(research_content='语义检索')
    state.confirmation.needed = True
    state.confirmation.reason = 'missing_fields'
    state.confirmation.options = [{'value': 'supplement_fields', 'label': '补充并继续'}]
    record = {'task_id': 'still-paused', 'user_scope': 'alice', 'task_type': '创新性评估', 'status': 'awaiting_confirmation', 'state': state.model_dump(mode='json'), 'updated_at': 'original'}
    asyncio.run(repository.create(record))
    class Model:
        async def generate_json(self, **kwargs): return {'nine_fields': {}, 'artifact_type': 'guideline'}
        async def aclose(self): pass
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', Model)
    response = client.post('/v1/content-assessment/tasks/still-paused/confirmation', headers={'X-User-Scope': 'alice'}, json={'selected_option': 'supplement_fields', 'supplemental_input': '研究目标：建设检索系统'})
    assert response.status_code == 422
    assert '考核指标' in response.json()['detail'] and '应用场景' in response.json()['detail']
    assert '研究目标' not in response.json()['detail']
    assert (asyncio.run(repository.get('still-paused', 'alice')))['state'] == record['state']
    assert app.state.task_manager.start_pipeline.await_count == 0



def test_unreadable_attachment_is_parse_failure_not_user_missing_fields(monkeypatch):
    class ParsedRepo:
        async def get(self, *_args, **_kwargs): return {'name': '损坏材料.txt', 'content': ''}
    monkeypatch.setattr(nine_fields_preview, 'get_parsed_file_repo', ParsedRepo)
    result = asyncio.run(nine_fields_preview.preview_nine_fields('', [UploadedFileRef(file_id='empty', name='损坏材料.txt')], 'alice'))
    assert result['extraction_failed']
    assert any('损坏材料.txt' in warning for warning in result['warnings'])


def test_supplement_rechecks_original_attachment_before_asking_again(api, repository, monkeypatch):
    from app.agents.content_assessment.schemas import ContentAssessmentState, UploadedFileDigest
    client, app = api
    state = ContentAssessmentGraph().create_initial_state('请评估这份指南')
    state.report_preferences.resolved_artifact_type = 'guideline'
    state.nine_fields = ResearchNineFields(research_content='统一芯片测量', application_scenarios=['实验室'])
    state.uploaded_file_context.files = [UploadedFileDigest(file_id='original', name='原指南.txt',
        content='考核指标：形成操作规范并完成复现验证')]
    state.confirmation.needed = True
    state.confirmation.reason = 'missing_fields'
    state.confirmation.options = [{'value': 'supplement_fields', 'label': '补充并继续'}]
    asyncio.run(repository.create({'task_id': 'original-review', 'user_scope': 'alice',
        'task_type': '创新性评估', 'status': 'awaiting_confirmation',
        'state': state.model_dump(mode='json'), 'updated_at': 'original'}))
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', lambda: pytest.fail('Original material and supplement suffice'))
    response = client.post('/v1/content-assessment/tasks/original-review/confirmation', headers={'X-User-Scope': 'alice'},
        json={'selected_option': 'supplement_fields', 'supplemental_input': '研究目标：统一试验条件；参考论文仅作背景'})
    assert response.status_code == 200, response.text
    resumed = ContentAssessmentState.model_validate(response.json()['state'])
    assert resumed.nine_fields.evaluation_indicators == ['形成操作规范并完成复现验证']
    assert resumed.report_preferences.resolved_artifact_type == 'guideline'
    assert response.json()['run_id'] == 'original-review'
    assert app.state.task_manager.start_pipeline.await_count == 1



def test_guided_object_sentence_and_project_alternative_label(monkeypatch):
    monkeypatch.setattr(nine_fields_preview, 'DeepSeekClient', lambda: pytest.fail('Complete guided brief'))
    result = asyncio.run(nine_fields_preview.preview_nine_fields('我要评估的是：项目\n研究方向：文献检索\n研究内容：语义排序\n关键技术或技术路线：图神经网络', [], 'alice'))
    assert result['resolved_artifact_type'] == 'project'
    assert result['missing_fields'] == []
    assert result['nine_fields']['key_technologies'] == ['图神经网络']
