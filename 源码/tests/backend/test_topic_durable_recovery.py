"""Durable recovery boundaries: no duplicate execution or false publication."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection import recovery
from app.core import topic_task_manager as managers
from app.core.rate_limiter import TaskSlotUnavailable
from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository
from app.services import topic_task_persistence as persistence


def state():
    result = TopicSelectionAgent().create_initial_state('研究城市热暴露差异与模型迁移性', analysis_mode='deep')
    result.current_stage = 'report_publish'
    result.resume_from_stage = 'report_publish'
    return result


def setup_repo(monkeypatch):
    repo = InMemoryTaskHistoryRepository()
    monkeypatch.setattr(managers, 'get_task_history_repo', lambda: repo)
    monkeypatch.setattr(persistence, 'get_task_history_repo', lambda: repo)
    return repo


async def save(repo, current, status='queued'):
    await repo.create(dict(task_id='task', user_scope='owner', task_type='智能选题', status=status,
        updated_at='revision', created_at='2026-10-01', state=current.model_dump(), output_json=deepcopy(current.output_json)))


def test_backoff_honors_retry_after_and_never_consumes_wait_as_execution():
    current = state()
    before = recovery.now()
    for expected in (30, 120, 300, 900):
        record = recovery.schedule_recovery(current, TimeoutError('timeout'))
        assert expected - 1 <= (recovery.parse_time(record['next_retry_at']) - recovery.now()).total_seconds() <= expected
        assert record['phase'] == 'scheduled'
    current.stage_artifacts['report_publish'] = {'retry_after_seconds': 1800}
    record = recovery.schedule_recovery(current, ConnectionError('offline'))
    assert recovery.parse_time(record['next_retry_at']) >= before + timedelta(seconds=1800)
    assert all(trace.duration_seconds is None for trace in current.stage_trace)


def test_identical_content_waits_for_a_fix_then_resumes_same_task(monkeypatch):
    repo = setup_repo(monkeypatch)
    current = state()
    recovery.schedule_recovery(current, recovery.GenerationRecoveryRequired('report_publish', 'source_validation', 'bad paragraph'))
    current.stage_artifacts['report_publish'] = {'paragraph_repair_budget': {'old': ['targeted', 'verified_facts']}, 'reader_draft_result': {'saved': True}}
    manager = managers.TopicTaskManager()
    resumed = []
    async def resume(task_id, scope, restored, **kwargs):
        resumed.append((task_id, scope, restored))
    monkeypatch.setattr(manager, 'resume_pipeline', resume)
    async def run():
        await save(repo, current)
        await manager.recover_due()
        assert not resumed
        monkeypatch.setattr(managers, 'fingerprint', lambda _: 'changed-implementation')
        await manager.recover_due()
        assert len(resumed) == 1
        assert resumed[0][:2] == ('task', 'owner')
        artifacts = resumed[0][2].stage_artifacts['report_publish']
        assert 'paragraph_repair_budget' not in artifacts
        assert artifacts['reader_draft_result'] == {'saved': True}
    asyncio.run(run())


def test_live_foreign_lease_blocks_manual_and_scheduled_resume(monkeypatch):
    repo = setup_repo(monkeypatch)
    current = state()
    current.output_json['generation_recovery'] = {'enabled': True, 'phase': 'running', 'lease_owner': 'another-process',
        'lease_until': (recovery.now() + timedelta(minutes=5)).isoformat()}
    manager = managers.TopicTaskManager()
    async def run():
        await save(repo, current)
        with pytest.raises(TaskSlotUnavailable):
            await manager.resume_pipeline('task', 'owner', current)
        await manager.recover_due()
        assert manager.active_count == 0
        assert (await manager.rate_limiter.usage('owner'))['occupied'] == 0
        assert (await repo.get('task', 'owner'))['updated_at'] == 'revision'
    asyncio.run(run())


def test_restart_scans_checkpoints_but_stop_and_delete_cannot_resurrect(monkeypatch):
    repo = setup_repo(monkeypatch)
    current = state()
    record = recovery.schedule_recovery(current, TimeoutError(), checkpoint=True)
    record['next_retry_at'] = (recovery.now() - timedelta(seconds=1)).isoformat()
    manager = managers.TopicTaskManager()
    resumed = []
    async def resume(*args, **kwargs):
        resumed.append(args)
    monkeypatch.setattr(manager, 'resume_pipeline', resume)
    async def run():
        await save(repo, current)
        await repo.reconcile_interrupted(recovery.now().isoformat())
        assert (await repo.get('task', 'owner'))['status'] == 'queued'
        await manager.recover_due()
        assert len(resumed) == 1
        await repo.update('task', 'owner', {'status': 'cancelled'})
        await manager._wait_for_recovery('task', 'owner', current, TimeoutError())
        assert (await repo.get('task', 'owner'))['status'] == 'cancelled'
        await repo.delete('task', 'owner')
        await manager._wait_for_recovery('task', 'owner', current, TimeoutError())
        assert await repo.get('task', 'owner') is None
        await manager.recover_due()
        assert len(resumed) == 1
    asyncio.run(run())


def test_execution_window_saves_checkpoint_and_releases_slot(monkeypatch):
    repo = setup_repo(monkeypatch)
    current = state()
    class SlowService:
        async def run(self, restored, on_stage_update):
            restored.stage_artifacts['report_publish'] = {'reader_draft_result': {'saved': True}}
            await on_stage_update(restored)
            await asyncio.Event().wait()
    monkeypatch.setattr(managers, 'TopicSelectionService', SlowService)
    manager = managers.TopicTaskManager()
    manager.execution_window_seconds = .01
    async def run():
        await save(repo, current)
        await manager.rate_limiter.check_and_acquire('owner', 'task')
        await manager._run_pipeline('task', 'owner', current)
        saved = await repo.get('task', 'owner')
        assert saved['status'] == 'queued'
        assert saved['output_json']['generation_recovery']['category'] == 'checkpoint'
        assert saved['state']['stage_artifacts']['report_publish']['reader_draft_result'] == {'saved': True}
        assert (await manager.rate_limiter.usage('owner'))['occupied'] == 0
    asyncio.run(run())


def test_dependency_probe_does_not_submit_generation_requests(monkeypatch):
    from app.services import deepseek_client
    repo = setup_repo(monkeypatch)
    current = state()
    current.output_json['generation_recovery'] = {'enabled': True, 'phase': 'waiting_service', 'category': 'dependency'}
    counts = []
    class Client:
        async def preflight(self):
            counts.append('probe')
            return {'status': 'unavailable'}
        async def aclose(self):
            pass
        async def generate_json_with_metrics(self, **kwargs):
            pytest.fail('dependency wait must not regenerate')
    monkeypatch.setattr(deepseek_client, 'DeepSeekClient', Client)
    manager = managers.TopicTaskManager()
    async def run():
        await save(repo, current)
        await manager.recover_due()
        await manager.recover_due()
        assert counts == ['probe']
        saved = await repo.get('task', 'owner')
        assert saved['status'] == 'queued'
        assert recovery.parse_time(saved['output_json']['generation_recovery']['next_retry_at']) > recovery.now()
    asyncio.run(run())


def test_persistence_rejects_stale_checkpoint_and_different_account(monkeypatch):
    repo = setup_repo(monkeypatch)
    current = state()
    async def run():
        await save(repo, current)
        for scope, revision in [('owner', 'stale'), ('other', 'revision')]:
            with pytest.raises(persistence.TaskHistoryConflict):
                await persistence.persist_topic_task_state(task_id='task', user_scope=scope, state=current,
                    status='running', expected_updated_at=revision)
        assert (await repo.get('task', 'owner'))['updated_at'] == 'revision'
    asyncio.run(run())
