"""进程内异步任务管理器。"""

from __future__ import annotations

import asyncio
import logging
import os

from app.agents.content_assessment.schemas import ContentAssessmentState
from app.core.rate_limiter import RateLimiter, TaskSlotUnavailable, TaskStateChanged
from app.repositories.content_assessment_task_history import get_task_history_repo
from app.services.content_assessment_service import ContentAssessmentService
from app.services.task_persistence import mark_task_failed, now_beijing_iso, persist_task_state

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self) -> None:
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._rate_limiter = RateLimiter()
        self._resume_lock = asyncio.Lock()
        self._task_scopes: dict[str, str] = {}

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    @property
    def active_count(self) -> int:
        return len(self._running_tasks)

    async def start_pipeline(self, task_id: str, user_scope: str, initial_state: ContentAssessmentState) -> None:
        if self.is_running(task_id):
            return
        task = asyncio.create_task(self._run_pipeline(task_id, user_scope, initial_state))
        self._running_tasks[task_id] = task
        self._task_scopes[task_id] = user_scope
        self._background_tasks.add(task)
        task.add_done_callback(lambda completed: self._cleanup_task(task_id, completed))

    async def continue_after_confirmation(
        self, task_id: str, user_scope: str, state: ContentAssessmentState
    ) -> None:
        await self.resume_pipeline(task_id, user_scope, state)

    async def resume_pipeline(
        self, task_id: str, user_scope: str, state: ContentAssessmentState,
        *, expected_updated_at: str | None = None, expected_status: str | None = None,
    ) -> None:
        """Resume a persisted graph after confirmation or expert review."""
        async with self._resume_lock:
            if self.is_running(task_id):
                raise TaskSlotUnavailable("任务正在运行，请勿重复提交")
            if expected_status is not None:
                latest = await get_task_history_repo().get(task_id, user_scope)
                if not latest or latest.get("status") != expected_status or latest.get("updated_at") != expected_updated_at:
                    raise TaskStateChanged("任务状态已变化，请刷新后重试")
            allowed, message = await self._rate_limiter.check_and_acquire(user_scope, task_id)
            if not allowed:
                raise TaskSlotUnavailable(message)
            queued = False
            try:
                await persist_task_state(task_id=task_id, user_scope=user_scope, state=state, status="queued")
                queued = True
                await self.start_pipeline(task_id, user_scope, state)
            except BaseException:
                try:
                    if queued:
                        await mark_task_failed(task_id, user_scope, state, "任务执行者启动失败，请重新创建任务")
                finally:
                    await self._rate_limiter.release(user_scope, task_id)
                raise

    async def cancel_task(self, task_id: str) -> bool:
        task = self._running_tasks.get(task_id)
        if task is None:
            return False
        scope = self._task_scopes.get(task_id)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if scope:
            await self._rate_limiter.release(scope, task_id)
        return True

    async def release_slot(self, user_scope: str, task_id: str) -> None:
        """Release a slot owned by a persisted task that is currently paused."""
        await self._rate_limiter.release(user_scope, task_id)

    def is_running(self, task_id: str) -> bool:
        return task_id in self._running_tasks

    async def shutdown(self) -> None:
        slots = list(self._task_scopes.items())
        for task in tuple(self._running_tasks.values()):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._running_tasks.clear()
        self._background_tasks.clear()
        for task_id, scope in slots:
            await self._rate_limiter.release(scope, task_id)

    def _cleanup_task(self, task_id: str, task: asyncio.Task[None]) -> None:
        self._running_tasks.pop(task_id, None)
        self._task_scopes.pop(task_id, None)
        self._background_tasks.discard(task)

    async def _run_pipeline(self, task_id: str, user_scope: str, initial_state: ContentAssessmentState) -> None:
        state = initial_state.model_copy(deep=True)
        state.user_scope = user_scope
        state.worker_id = f"async:{os.getpid()}"
        async def progress(current):
            nonlocal state
            state = current
            await self._on_stage_progress(task_id, user_scope, current)
        try:
            await persist_task_state(task_id=task_id, user_scope=user_scope, state=state, status="running")
            service = ContentAssessmentService()
            state = await service.run(
                state,
                on_stage_update=progress,
            )
            if state.current_stage == "awaiting_confirmation":
                await persist_task_state(
                    task_id=task_id,
                    user_scope=user_scope,
                    state=state,
                    status="awaiting_confirmation",
                )
                logger.info("[task_id=%s][user=%s] 创新性评估等待材料确认", task_id, user_scope)
            elif state.current_stage == "failed":
                await mark_task_failed(task_id, user_scope, state,
                    state.output_json.get("generation_recovery", {}).get("message", "生成中断，请重试。"))
            elif state.current_stage == "completed":
                await persist_task_state(
                    task_id=task_id,
                    user_scope=user_scope,
                    state=state,
                    status="completed",
                    completed_at=now_beijing_iso(),
                )
                logger.info("[task_id=%s][user=%s] 创新性评估完成", task_id, user_scope)
            else:
                raise RuntimeError("评估流程尚未完成，不能发布报告")
        except asyncio.CancelledError:
            logger.info("[task_id=%s] 创新性评估已取消", task_id)
            await mark_task_failed(task_id, user_scope, state, "服务中断或任务已停止")
            raise
        except Exception as exc:
            logger.exception("[task_id=%s] 创新性评估失败", task_id)
            await mark_task_failed(task_id, user_scope, state, str(exc))
        finally:
            await self._rate_limiter.release(user_scope, task_id)

    async def _on_stage_progress(
        self, task_id: str, user_scope: str, state: ContentAssessmentState
    ) -> None:
        status = state.current_stage if state.current_stage in {"failed", "awaiting_confirmation"} else "running"
        await persist_task_state(task_id=task_id, user_scope=user_scope, state=state, status=status)
