"""Checkpointed topic execution and durable recovery scheduling."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from uuid import uuid4

from app.agents.topic_selection.schemas import TopicSelectionState
from app.agents.topic_selection.recovery import (GenerationRecoveryRequired, fingerprint, now, parse_time, schedule_recovery)
from app.core.rate_limiter import RateLimiter, TaskSlotUnavailable, TaskStateChanged
from app.repositories.content_assessment_task_history import get_task_history_repo
from app.services.topic_selection_service import TopicSelectionService
from app.services.topic_task_persistence import TaskHistoryConflict, now_beijing_iso, persist_topic_task_state

logger = logging.getLogger(__name__)


class TopicTaskManager:
    execution_window_seconds = 600

    def __init__(self) -> None:
        self._running_tasks = {}
        self._background_tasks = set()
        self._rate_limiter = RateLimiter()
        self._resume_lock = asyncio.Lock()
        self._task_scopes = {}
        self._revisions = {}
        self._cancelled = set()
        self._scheduler = None
        self._closing = False
        self._owner = f'topic:{os.getpid()}:{uuid4().hex[:10]}'

    @property
    def rate_limiter(self):
        return self._rate_limiter

    @property
    def active_count(self):
        return len(self._running_tasks)

    def is_running(self, task_id):
        return task_id in self._running_tasks

    async def start(self):
        if self._scheduler is None:
            self._scheduler = asyncio.create_task(self._schedule_loop())

    async def start_pipeline(self, task_id, user_scope, initial_state):
        if self.is_running(task_id) or self._closing:
            return
        self._cancelled.discard(task_id)
        task = asyncio.create_task(self._run_pipeline(task_id, user_scope, initial_state))
        self._running_tasks[task_id] = task
        self._task_scopes[task_id] = user_scope
        self._background_tasks.add(task)
        task.add_done_callback(lambda done: self._cleanup_task(task_id, done))

    async def resume_pipeline(self, task_id, user_scope, state, *, expected_updated_at=None, expected_status=None):
        async with self._resume_lock:
            if self.is_running(task_id) or self._closing:
                raise TaskSlotUnavailable('任务正在运行')
            latest = await get_task_history_repo().get(task_id, user_scope)
            if (not latest or latest.get('status') in ('completed', 'cancelled', 'superseded')
                    or expected_status is not None and (latest.get('status') != expected_status or latest.get('updated_at') != expected_updated_at)):
                raise TaskStateChanged('任务状态已变化')
            recovery = (latest.get('state') or {}).get('output_json', {}).get('generation_recovery', {})
            lease = parse_time(recovery.get('lease_until'))
            if recovery.get('lease_owner') not in (None, self._owner) and lease and lease > now():
                raise TaskSlotUnavailable('任务正在由其他执行进程恢复')
            allowed, message = await self._rate_limiter.check_and_acquire(user_scope, task_id)
            if not allowed:
                raise TaskSlotUnavailable(message)
            record = state.output_json.setdefault('generation_recovery', {})
            record.update(enabled=True, phase='running', lease_owner=self._owner,
                          lease_until=(now() + timedelta(seconds=self.execution_window_seconds + 60)).isoformat(), next_retry_at=None)
            try:
                await persist_topic_task_state(task_id=task_id, user_scope=user_scope, state=state, status='queued', expected_updated_at=latest.get('updated_at', ''))
                await self.start_pipeline(task_id, user_scope, state)
            except BaseException:
                await self._rate_limiter.release(user_scope, task_id)
                raise

    async def cancel_task(self, task_id):
        self._cancelled.add(task_id)
        task = self._running_tasks.get(task_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def release_slot(self, user_scope, task_id):
        await self._rate_limiter.release(user_scope, task_id)

    async def shutdown(self):
        self._closing = True
        if self._scheduler:
            self._scheduler.cancel()
            await asyncio.gather(self._scheduler, return_exceptions=True)
        for task in tuple(self._running_tasks.values()):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    def _cleanup_task(self, task_id, task):
        self._running_tasks.pop(task_id, None)
        self._task_scopes.pop(task_id, None)
        self._revisions.pop(task_id, None)
        self._background_tasks.discard(task)

    async def _persist(self, task_id, scope, state, status, **kwargs):
        self._revisions[task_id] = await persist_topic_task_state(task_id=task_id, user_scope=scope,
            state=state, status=status, expected_updated_at=self._revisions.get(task_id), **kwargs)

    async def _run_pipeline(self, task_id, user_scope, initial_state):
        state = initial_state.model_copy(deep=True)
        state.user_scope, state.worker_id = user_scope, self._owner
        try:
            latest = await get_task_history_repo().get(task_id, user_scope)
            if not latest or latest.get('status') in ('cancelled', 'completed', 'superseded'):
                return
            current_record = (latest.get('state') or {}).get('output_json', {}).get('generation_recovery', {})
            lease = parse_time(current_record.get('lease_until'))
            if current_record.get('lease_owner') not in (None, self._owner) and lease and lease > now():
                return
            record = state.output_json.setdefault('generation_recovery', {})
            self._revisions[task_id] = latest.get('updated_at', '')
            record.update(enabled=True, phase='running', lease_owner=self._owner, next_retry_at=None,
                          lease_until=(now() + timedelta(seconds=self.execution_window_seconds + 60)).isoformat())
            # A new task also gets a restart checkpoint before its first call.
            record.setdefault('pending_stages', [t.stage_key for t in state.stage_trace if t.status != 'completed'])
            await self._persist(task_id, user_scope, state, 'running')
            service = TopicSelectionService()
            state = await asyncio.wait_for(service.run(state, on_stage_update=lambda current: self._on_stage_progress(task_id, user_scope, current)), timeout=self.execution_window_seconds)
            if state.confirmation.needed:
                await self._persist(task_id, user_scope, state, 'awaiting_confirmation')
            elif state.report_plan.publication_status != 'published':
                raise GenerationRecoveryRequired('report_publish', 'publication_check', '报告发布检查尚未通过，已有结果已保存。')
            else:
                state.output_json.pop('generation_recovery', None)
                await self._persist(task_id, user_scope, state, 'completed', completed_at=now_beijing_iso())
        except TaskHistoryConflict:
            logger.info('Topic checkpoint superseded task=%s', task_id)
        except asyncio.CancelledError:
            if task_id in self._cancelled:
                state.output_json.pop('generation_recovery', None)
                state.current_stage = 'cancelled'
                try:
                    await self._persist(task_id, user_scope, state, 'cancelled')
                except TaskHistoryConflict:
                    pass
            else:
                await self._wait_for_recovery(task_id, user_scope, state, TimeoutError('执行服务重启，等待续跑已保存的分析。'), checkpoint=True)
            raise
        except TimeoutError as exc:
            await self._wait_for_recovery(task_id, user_scope, state, exc, checkpoint=True)
        except Exception as exc:
            logger.exception('Topic checkpoint waiting task=%s', task_id)
            await self._wait_for_recovery(task_id, user_scope, state, exc)
        finally:
            await self._rate_limiter.release(user_scope, task_id)

    async def _wait_for_recovery(self, task_id, scope, state, exc, *, checkpoint=False):
        latest = await get_task_history_repo().get(task_id, scope)
        if (not latest or latest.get('status') in ('cancelled', 'completed', 'superseded')
                or task_id in self._cancelled):
            return
        if task_id not in self._revisions:
            self._revisions[task_id] = latest.get('updated_at', '')
        schedule_recovery(state, exc, checkpoint=checkpoint)
        for trace in state.stage_trace:
            if trace.status == 'running':
                trace.status = 'blocked'
        try:
            await self._persist(task_id, scope, state, 'queued')
        except TaskHistoryConflict:
            pass  # A stop/delete or another owner won; never recreate it.

    async def _on_stage_progress(self, task_id, scope, state):
        if task_id in self._cancelled:
            raise asyncio.CancelledError
        await self._persist(task_id, scope, state, 'awaiting_confirmation' if state.confirmation.needed else 'running')

    async def _schedule_loop(self):
        while not self._closing:
            try:
                await self.recover_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Topic recovery scheduler deferred')
            await asyncio.sleep(5)

    async def recover_due(self):
        records = await get_task_history_repo().list_topic_recoveries()
        for task in records:
            try:
                await self._recover_record(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Topic recovery record deferred task=%s', task.get('task_id'))

    async def _recover_record(self, task):
        task_id, scope = task['task_id'], task['user_scope']
        if self.is_running(task_id) or self._closing:
            return
        state = TopicSelectionState.model_validate(task['state'])
        record = state.output_json.get('generation_recovery', {})
        lease = parse_time(record.get('lease_until'))
        if lease and lease > now():
            return
        due = parse_time(record.get('next_retry_at'))
        if due and due > now():
            return
        if record.get('phase') == 'waiting_system' and record.get('fingerprint') == fingerprint(state):
            return
        if record.get('category') == 'dependency':
            # Probe availability without resubmitting the whole generation.
            from app.services.deepseek_client import DeepSeekClient
            client = None
            try:
                client = DeepSeekClient()
                health = await client.preflight()
            except Exception:
                health = {'status': 'unavailable'}
            finally:
                if client:
                    await client.aclose()
            if health.get('status') != 'available':
                record['next_retry_at'] = (now() + timedelta(minutes=15)).isoformat()
                try:
                    await persist_topic_task_state(task_id=task_id, user_scope=scope, state=state, status='queued', expected_updated_at=task.get('updated_at', ''))
                except TaskHistoryConflict:
                    pass
                return
            state.analysis_policy.model_preflight_status = 'available'
            state.analysis_policy.model_preflight_reason = ''
        if record.get('phase') == 'waiting_system':
            # A source/configuration/implementation change permits a fresh
            # bounded repair budget; identical rejected content cannot loop.
            state.stage_artifacts.get('report_publish', {}).pop('paragraph_repair_budget', None)
            state.stage_artifacts.get('report_publish', {}).pop('structure_repair_budget', None)
            state.stage_artifacts.get('crosslink_gap', {}).pop('generation_attempts', None)
            state.stage_artifacts.get('crosslink_gap', {}).pop('generation_payload', None)
        state.resume_from_stage = record.get('stage') or next((t.stage_key for t in state.stage_trace if t.status != 'completed'), 'report_publish')
        state.current_stage = state.resume_from_stage
        try:
            await self.resume_pipeline(task_id, scope, state, expected_updated_at=task.get('updated_at', ''), expected_status=task.get('status'))
        except (TaskSlotUnavailable, TaskStateChanged, TaskHistoryConflict):
            return
