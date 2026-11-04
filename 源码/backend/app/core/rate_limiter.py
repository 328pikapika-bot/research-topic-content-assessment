"""进程内异步速率限制器。

使用 asyncio.Lock 保护共享计数器，确保多个协程不会同时修改
用户/全局运行任务计数。
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import MAX_CONCURRENT_TASKS_GLOBAL, MAX_CONCURRENT_TASKS_PER_USER

logger = logging.getLogger(__name__)


class RateLimiter:
    """异步速率限制器 — 单进程内使用 asyncio.Lock 保护计数器。"""

    def __init__(
        self,
        max_per_user: int = MAX_CONCURRENT_TASKS_PER_USER,
        max_global: int = MAX_CONCURRENT_TASKS_GLOBAL,
    ) -> None:
        self._max_per_user = max_per_user
        self._max_global = max_global
        self._lock = asyncio.Lock()
        self._slots: dict[str, str] = {}

    async def check_and_acquire(self, user_scope: str, task_id: str) -> tuple[bool, str]:
        """尝试为用户获取一个执行槽位。

        Returns:
            (allowed, message) — allowed 表示是否允许执行，message 为拒绝原因。
        """
        async with self._lock:
            if task_id in self._slots:
                return (True, "") if self._slots[task_id] == user_scope else (False, "任务无权访问")
            if len(self._slots) >= self._max_global:
                return False, "系统繁忙，请稍后重试"
            user_count = sum(owner == user_scope for owner in self._slots.values())
            if user_count >= self._max_per_user:
                return (
                    False,
                    f"当前已有 {user_count} 个进行中的任务（最多 {self._max_per_user} 个），请等待其中至少一个任务完成后再创建新任务。",
                )
            self._slots[task_id] = user_scope
            return True, ""

    async def release(self, user_scope: str, task_id: str) -> None:
        """释放一个执行槽位。"""
        async with self._lock:
            if self._slots.get(task_id) == user_scope:
                self._slots.pop(task_id)

    async def usage(self, user_scope: str) -> dict[str, int]:
        async with self._lock:
            return {"occupied": sum(owner == user_scope for owner in self._slots.values()),
                    "limit": self._max_per_user, "global_occupied": len(self._slots), "global_limit": self._max_global}


class TaskSlotUnavailable(RuntimeError):
    pass


class TaskStateChanged(RuntimeError):
    pass
