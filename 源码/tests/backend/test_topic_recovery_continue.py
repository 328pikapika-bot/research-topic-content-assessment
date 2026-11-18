"""Manual recovery-continue regressions for content-blocked topic tasks."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.api import routes
from app.api.routes import TopicRecoveryContinueRequest
from app.agents.topic_selection.agent import TopicSelectionAgent


class Repository:
    def __init__(self):
        self.records = {}

    async def get(self, key, scope):
        return deepcopy(self.records.get(key))

    async def update(self, key, scope, fields):
        self.records[key].update(deepcopy(fields))
        return True

    async def update_report(self, key, scope, expected, fields):
        record = self.records[key]
        if record.get("updated_at", "") != expected:
            return False
        for path, value in fields.items():
            target = record
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(value)
        return True


class Manager:
    def __init__(self, running=False):
        self.rate_limiter = self
        self.running = running
        self.resumed = []

    def is_running(self, task_id):
        return self.running

    async def check_and_acquire(self, scope, key):
        return True, ""

    async def release(self, scope, key):
        pass

    async def resume_pipeline(self, task_id, scope, state, *, expected_updated_at=None, expected_status=None):
        self.resumed.append((task_id, expected_status, state.model_copy(deep=True)))


@pytest.fixture
def blocked_repo(monkeypatch):
    repo = Repository()
    state = TopicSelectionAgent().create_initial_state("海洋抗肿瘤药物诱导凋亡机制研究", analysis_mode="fast")
    blocked = False
    for trace in state.stage_trace:
        if trace.stage_key == "crosslink_gap":
            trace.status = "blocked"
            blocked = True
        elif not blocked:
            trace.status = "completed"
            trace.progress_percent = 100
    state.current_stage = "crosslink_gap"
    state.resume_from_stage = "crosslink_gap"
    state.output_json["generation_recovery"] = {
        "enabled": True, "phase": "waiting_system", "stage": "crosslink_gap",
        "category": "content", "attempts": 3, "pending_stages": ["crosslink_gap", "report_publish"],
        "next_retry_at": None, "fingerprint": "stale",
    }
    repo.records["blocked"] = {
        "task_id": "blocked", "task_type": "智能选题", "user_scope": "tester",
        "status": "queued", "updated_at": "t1",
        "created_at": "2026-11-16T11:19:00+08:00", "state": state.model_dump(),
    }
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repo)
    monkeypatch.setattr("app.services.topic_task_persistence.get_task_history_repo", lambda: repo)
    return repo


def test_continue_with_supplement_resumes_from_blocked_stage(blocked_repo):
    async def scenario():
        manager = Manager()
        response = await routes.continue_topic_recovery(
            "blocked", TopicRecoveryContinueRequest(supplemental_input="补充凋亡机制相关全文文献"), "tester", manager)
        assert response.status == "running"
        assert len(manager.resumed) == 1
        task_id, expected_status, state = manager.resumed[0]
        assert task_id == "blocked" and expected_status == "queued"
        assert state.resume_from_stage == "crosslink_gap"
        assert state.current_stage == "crosslink_gap"
        assert "补充材料：补充凋亡机制相关全文文献" in state.raw_user_input
        assert any("补充要求：补充凋亡机制相关全文文献" in item for item in state.constraint_card.must_include)
        blocked = next(item for item in state.stage_trace if item.stage_key == "crosslink_gap")
        assert blocked.status == "pending"
        assert "generation_recovery" not in state.output_json
        materials = state.output_json["supplemental_materials"]
        assert len(materials) == 1 and materials[0]["content"] == "补充凋亡机制相关全文文献"
    asyncio.run(scenario())


def test_continue_requires_supplement(blocked_repo):
    async def scenario():
        manager = Manager()
        with pytest.raises(HTTPException) as error:
            await routes.continue_topic_recovery("blocked", TopicRecoveryContinueRequest(supplemental_input="  "), "tester", manager)
        assert error.value.status_code == 422
        assert not manager.resumed
        state = blocked_repo.records["blocked"]["state"]
        assert state["output_json"]["generation_recovery"]["phase"] == "waiting_system"
        assert "补充材料" not in state["raw_user_input"]
    asyncio.run(scenario())


def test_explicit_retry_preserves_input_and_resets_only_repair_budget(blocked_repo):
    async def scenario():
        original = blocked_repo.records['blocked']['state']
        original['stage_artifacts']['crosslink_gap'] = {'generation_attempts': ['initial', 'repair'], 'kept': True}
        original['stage_artifacts']['report_publish'] = {'paragraph_repair_budget': {'a': ['targeted']}}
        manager = Manager()
        await routes.continue_topic_recovery('blocked', TopicRecoveryContinueRequest(action='retry'), 'tester', manager)
        resumed = manager.resumed[0][2]
        assert resumed.raw_user_input == original['raw_user_input']
        assert 'supplemental_materials' not in resumed.output_json
        assert resumed.resume_from_stage == 'crosslink_gap'
        assert resumed.stage_artifacts['crosslink_gap'] == {'kept': True}
        assert 'paragraph_repair_budget' not in resumed.stage_artifacts['report_publish']
        assert next(t for t in resumed.stage_trace if t.stage_key == 'retrieval_evidence').status == 'completed'
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['completed', 'cancelled', 'superseded'])
def test_retry_cannot_reopen_terminal_task_with_stale_recovery(blocked_repo, status):
    async def scenario():
        blocked_repo.records['blocked']['status'] = status
        manager = Manager()
        with pytest.raises(HTTPException) as error:
            await routes.continue_topic_recovery('blocked', TopicRecoveryContinueRequest(action='retry'), 'tester', manager)
        assert error.value.status_code == 409
        assert not manager.resumed
    asyncio.run(scenario())


def test_continue_rejected_without_waiting_recovery(blocked_repo):
    async def scenario():
        blocked_repo.records["blocked"]["state"]["output_json"].pop("generation_recovery")
        manager = Manager()
        with pytest.raises(HTTPException) as error:
            await routes.continue_topic_recovery(
                "blocked", TopicRecoveryContinueRequest(supplemental_input="补充"), "tester", manager)
        assert error.value.status_code == 409
        assert not manager.resumed
    asyncio.run(scenario())


def test_continue_rejected_while_running(blocked_repo):
    async def scenario():
        manager = Manager(running=True)
        with pytest.raises(HTTPException) as error:
            await routes.continue_topic_recovery(
                "blocked", TopicRecoveryContinueRequest(supplemental_input="补充"), "tester", manager)
        assert error.value.status_code == 409
        assert not manager.resumed
    asyncio.run(scenario())
