"""
创新性评估智能体 任务历史仓储层

封装所有数据库访问，业务接口不应直接写 Mongo 查询。
所有方法强制带 user_scope，确保用户数据隔离。

异步实现：sync PyMongo + asyncio.to_thread，兼容测试服旧版 MongoDB。
支持内存存储模式（USE_MEMORY_STORE=true），无需 MongoDB。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from app.core.config import MONGO_DB, MONGO_URI, MONGO_URI_SAFE, TASK_COLLECTION

logger = logging.getLogger(__name__)

USE_MEMORY_STORE = os.getenv("USE_MEMORY_STORE", "").strip().lower() in ("true", "1", "yes", "on")


class TaskHistoryError(RuntimeError):
    """数据库操作异常。"""


ACTIVE_STATUSES = ("queued", "running", "processing")
HISTORY_DELETABLE_STATUSES = ("completed", "failed", "cancelled", "superseded", "model_unavailable", "time_out", "blocked", "awaiting_confirmation", "awaiting_clarification")
HISTORY_TASK_TYPES = ("智能选题", "创新性评估", "内容评估")
ATTENTION_STATUSES = (
    "awaiting_clarification", "awaiting_confirmation", "awaiting_quality_continue",
    "awaiting_review", "awaiting_correction", "superseded", "model_unavailable", "failed", "time_out",
)


def interrupted_cutoff(timestamp: str) -> str:
    # Other development/production processes may use the same collection.
    # Stage progress persists more frequently than this conservative timeout;
    # startup alone is never proof that a recently updated worker has exited.
    return (datetime.fromisoformat(timestamp) - timedelta(hours=6)).isoformat()


def _status_groups(statuses: dict[str, int]) -> dict[str, int]:
    """Expose the product's four mutually-exclusive status buckets."""
    running = sum(statuses.get(key, 0) for key in ACTIVE_STATUSES)
    completed = statuses.get("completed", 0)
    stopped = statuses.get("cancelled", 0)
    recognized = {*ACTIVE_STATUSES, *ATTENTION_STATUSES, "completed", "cancelled"}
    attention = sum(statuses.get(key, 0) for key in ATTENTION_STATUSES)
    # Historical records can contain legacy status names. Keep the four-card
    # invariant by treating any non-terminal unknown state as requiring action.
    attention += sum(count for key, count in statuses.items() if key not in recognized)
    return {"running": running, "completed": completed, "attention": attention, "stopped": stopped}


def task_statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = dict(Counter(t.get("status", "unknown") for t in records))
    return {"total": len(records), "statuses": statuses, "status_groups": _status_groups(statuses),
            "agents": {agent: {"total": sum(t.get("task_type", "创新性评估") == kind for t in records),
                               **dict(Counter(t.get("status", "unknown") for t in records if t.get("task_type", "创新性评估") == kind))}
                       for agent, kind in (("assessment", "创新性评估"), ("topic", "智能选题"))}}


class InMemoryTaskHistoryRepository:
    """内存版任务历史仓储，本地测试用，无需 MongoDB。"""

    def __init__(self) -> None:
        self._tasks: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def ping(self) -> dict[str, Any]:
        return {"status": "ok", "database": "in-memory", "collection": TASK_COLLECTION}

    def disconnect(self) -> None:
        self._tasks.clear()

    async def create(self, record: dict[str, Any]) -> str:
        async with self._lock:
            self._tasks.append(deepcopy(record))
            return record.get("task_id", "")

    async def update(self, task_id: str, user_scope: str, updates: dict[str, Any]) -> bool:
        async with self._lock:
            for t in self._tasks:
                if t.get("task_id") == task_id and t.get("user_scope") == user_scope:
                    t.update(deepcopy(updates))
                    return True
            return False

    async def get(self, task_id: str, user_scope: str) -> dict[str, Any] | None:
        async with self._lock:
            for t in self._tasks:
                if t.get("task_id") == task_id and t.get("user_scope") == user_scope:
                    return deepcopy(t)
            return None

    async def update_report(self, task_id: str, user_scope: str, expected_updated_at: str, fields: dict) -> bool:
        async with self._lock:
            for t in self._tasks:
                if t.get("task_id") == task_id and t.get("user_scope") == user_scope and t.get("updated_at", "") == expected_updated_at:
                    for path, value in fields.items():
                        target = t
                        parts = path.split(".")
                        for part in parts[:-1]:
                            target = target.setdefault(part, {})
                        target[parts[-1]] = deepcopy(value)
                    return True
            return False

    async def list_tasks(
        self, user_scope: str, *, status: str | None = None,
        search: str | None = None, page: int = 1, page_size: int = 20, task_type: str | None = None,
        statuses: list[str] | None = None, exclude_statuses: list[str] | None = None,
        created_after: str | None = None, created_before: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        async with self._lock:
            filtered = [t for t in self._tasks if t.get("user_scope") == user_scope]
            if status and status != "all":
                filtered = [t for t in filtered if t.get("status") == status]
            if statuses is not None:
                filtered = [t for t in filtered if t.get("status") in statuses]
            if exclude_statuses is not None:
                filtered = [t for t in filtered if t.get("status") not in exclude_statuses]
            if created_after:
                filtered = [t for t in filtered if t.get("created_at", "") >= created_after]
            if created_before:
                filtered = [t for t in filtered if t.get("created_at", "") <= created_before]
            if task_type:
                filtered = [t for t in filtered if t.get("task_type") == task_type]
            if search and search.strip():
                s = search.strip().lower()
                filtered = [t for t in filtered if (
                    s in (t.get("title") or "").lower()
                    or s in (t.get("task_type") or "").lower()
                    or s in (t.get("raw_user_input") or "").lower()
                    or s in (t.get("summary") or "").lower()
                )]
            total = len(filtered)
            filtered.sort(key=lambda t: t.get("created_at", ""), reverse=True)
            skip = max(0, page - 1) * page_size
            return [deepcopy(t) for t in filtered[skip:skip + page_size]], total

    async def statistics(self, user_scope: str) -> dict[str, Any]:
        async with self._lock:
            return task_statistics([t for t in self._tasks if t.get("user_scope") == user_scope])

    async def list_topic_recoveries(self) -> list[dict[str, Any]]:
        """Internal scheduler scan; every subsequent mutation remains scoped."""
        async with self._lock:
            return [deepcopy(t) for t in self._tasks if t.get('task_type') == '智能选题'
                    and t.get('status') in ACTIVE_STATUSES
                    and t.get('output_json', {}).get('generation_recovery', {}).get('enabled')]

    async def reconcile_interrupted(self, timestamp: str) -> int:
        cutoff = interrupted_cutoff(timestamp)
        async with self._lock:
            count = 0
            for task in self._tasks:
                if (task.get("status") in ACTIVE_STATUSES and task.get("updated_at", task.get("created_at", "")) < cutoff
                        and not task.get('output_json', {}).get('generation_recovery', {}).get('enabled')):
                    task.update(status="failed", current_stage="failed", updated_at=timestamp,
                                completed_at=timestamp, error_message="服务中断，任务执行者已退出，请重新创建任务")
                    if task.get("state"):
                        task["state"]["current_stage"] = "failed"
                    count += 1
            return count

    async def delete(self, task_id: str, user_scope: str) -> bool:
        async with self._lock:
            for i, t in enumerate(self._tasks):
                if t.get("task_id") == task_id and t.get("user_scope") == user_scope:
                    self._tasks.pop(i)
                    return True
            return False

    async def delete_history(self, user_scope: str, *, task_type: str | None = None,
                             task_id: str | None = None, dry_run: bool = False,
                             is_running: Callable[[str], bool] | None = None) -> dict[str, Any]:
        async with self._lock:
            records = [task for task in self._tasks if task.get("user_scope") == user_scope
                       and task.get("task_type", "创新性评估") in HISTORY_TASK_TYPES
                       and (not task_type or task.get("task_type", "创新性评估") == task_type
                            or task_type == "创新性评估" and task.get("task_type") == "内容评估")
                       and (not task_id or task.get("task_id") == task_id)]
            eligible = [task for task in records if task.get("status") in HISTORY_DELETABLE_STATUSES
                        and not (is_running and is_running(task["task_id"]))]
            ids = [task["task_id"] for task in eligible]
            if not dry_run:
                self._tasks = [task for task in self._tasks if not (task.get("user_scope") == user_scope and task.get("task_id") in ids)]
                logger.info("history_delete scope=%s task_type=%s task_ids=%s deleted=%s", user_scope, task_type, ids, len(ids))
            return {"success": True, "eligible_count": len(ids), "deleted_count": 0 if dry_run else len(ids),
                    "task_ids": [] if dry_run else ids, "skipped_count": len(records) - len(ids),
                    "skipped_reasons": dict(Counter(task.get("status", "unknown") for task in records if task not in eligible))}

    async def list_running(self, user_scope: str, *, task_type: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                deepcopy(t) for t in self._tasks
                if t.get("user_scope") == user_scope
                and t.get("status") in ACTIVE_STATUSES
                and (not task_type or t.get("task_type") == task_type)
            ]

    async def exists_running(self, user_scope: str) -> tuple[bool, str | None]:
        async with self._lock:
            for t in self._tasks:
                if (t.get("user_scope") == user_scope
                        and t.get("status") in ACTIVE_STATUSES
                        and not (t.get("output_json", {}).get("generation_recovery", {}).get("enabled") and t.get("output_json", {}).get("generation_recovery", {}).get("phase") != "running")):
                    return True, t.get("task_id", "")
            return False, None

    async def delete_many(self, task_ids: list[str], user_scope: str) -> int:
        async with self._lock:
            before = len(self._tasks)
            ids_set = set(task_ids)
            self._tasks = [
                t for t in self._tasks
                if not (t.get("task_id") in ids_set and t.get("user_scope") == user_scope)
            ]
            return before - len(self._tasks)


class ContentAssessmentTaskHistoryRepository:
    """创新性评估任务历史仓储。

    所有增删改查都必须同时带 task_id 和 user_scope。
    """

    def __init__(self) -> None:
        self._client: MongoClient | None = None
        self._collection: Collection | None = None

    # ---- 内部方法 ----

    async def _get_collection(self) -> Collection:
        if self._collection is None:
            await asyncio.to_thread(self._connect)
        assert self._collection is not None
        return self._collection

    def _connect(self) -> None:
        try:
            self._client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            self._client.admin.command("ping")
            db = self._client[MONGO_DB]
            self._collection = db[TASK_COLLECTION]
            logger.info("MongoDB 连接成功 db=%s collection=%s", MONGO_DB, TASK_COLLECTION)
        except PyMongoError as exc:
            logger.error("MongoDB 连接失败 uri=%s: %s", MONGO_URI_SAFE, exc)
            raise TaskHistoryError(f"无法连接 MongoDB ({MONGO_URI_SAFE})：{exc}") from exc

    async def ping(self) -> dict[str, Any]:
        """返回 MongoDB 连接诊断信息，供本地联调和健康检查使用。"""
        try:
            if self._client is None:
                await asyncio.to_thread(self._connect)
            assert self._client is not None
            await asyncio.to_thread(self._client.admin.command, "ping")
            return {
                "status": "ok",
                "database": MONGO_DB,
                "collection": TASK_COLLECTION,
                "uri": MONGO_URI_SAFE,
            }
        except TaskHistoryError:
            raise
        except PyMongoError as exc:
            logger.error("MongoDB ping 失败 uri=%s: %s", MONGO_URI_SAFE, exc)
            raise TaskHistoryError(f"MongoDB ping 失败 ({MONGO_URI_SAFE})：{exc}") from exc

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection = None

    # ---- 公开方法 ----

    async def update_report(self, task_id: str, user_scope: str, expected_updated_at: str, fields: dict) -> bool:
        collection = await self._get_collection()
        result = await asyncio.to_thread(collection.update_one,
            {"task_id": task_id, "user_scope": user_scope, "updated_at": expected_updated_at}, {"$set": fields})
        return result.matched_count > 0

    async def create(self, record: dict[str, Any]) -> str:
        """创建一条任务记录，返回 task_id。"""
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(collection.insert_one, record)
            return str(result.inserted_id)
        except PyMongoError as exc:
            logger.error("创建任务记录失败 task_id=%s: %s", record.get("task_id"), exc)
            raise TaskHistoryError(f"创建任务记录失败：{exc}") from exc

    async def update(
        self,
        task_id: str,
        user_scope: str,
        updates: dict[str, Any],
    ) -> bool:
        """更新任务记录。只允许更新自己的任务。"""
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(
                collection.update_one,
                {"task_id": task_id, "user_scope": user_scope},
                {"$set": updates},
            )
            return result.matched_count > 0
        except PyMongoError as exc:
            logger.error("更新任务记录失败 task_id=%s: %s", task_id, exc)
            raise TaskHistoryError(f"更新任务记录失败：{exc}") from exc

    async def get(self, task_id: str, user_scope: str) -> dict[str, Any] | None:
        """按 task_id + user_scope 查询单条任务详情。"""
        try:
            collection = await self._get_collection()
            return await asyncio.to_thread(
                collection.find_one,
                {"task_id": task_id, "user_scope": user_scope},
            )
        except PyMongoError as exc:
            logger.error("查询任务详情失败 task_id=%s: %s", task_id, exc)
            raise TaskHistoryError(f"查询任务记录失败：{exc}") from exc

    async def list_tasks(
        self,
        user_scope: str,
        *,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        task_type: str | None = None,
        statuses: list[str] | None = None,
        exclude_statuses: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页查询当前用户的任务历史列表。"""
        try:
            collection = await self._get_collection()
            filter_dict: dict[str, Any] = {"user_scope": user_scope}

            if status and status != "all":
                filter_dict["status"] = status
            if task_type:
                filter_dict["task_type"] = task_type
            if statuses is not None:
                filter_dict["status"] = {"$in": statuses}
            if exclude_statuses is not None:
                filter_dict["status"] = {"$nin": exclude_statuses}
            if created_after or created_before:
                filter_dict["created_at"] = {}
                if created_after:
                    filter_dict["created_at"]["$gte"] = created_after
                if created_before:
                    filter_dict["created_at"]["$lte"] = created_before

            if search and search.strip():
                filter_dict["$or"] = [
                    {key: {"$regex": re.escape(search.strip()), "$options": "i"}}
                    for key in ("title", "task_type", "raw_user_input", "summary")
                ]

            total = await asyncio.to_thread(collection.count_documents, filter_dict)

            skip = max(0, (page - 1)) * page_size

            items = await asyncio.to_thread(
                lambda: list(
                    collection.find(
                        filter_dict,
                        {"state": 0, "output_json": 0},
                    )
                    .sort("created_at", -1)
                    .skip(skip)
                    .limit(page_size)
                )
            )
            for item in items:
                if "_id" in item:
                    item["_id"] = str(item["_id"])
            # Recovery lives only in existing JSON. Fetch its small envelope
            # without loading report bodies into the task list.
            ids = [item.get('task_id') for item in items]
            recoveries = await asyncio.to_thread(lambda: list(collection.find(
                {'user_scope': user_scope, 'task_id': {'$in': ids}},
                {'task_id': 1, 'output_json.generation_recovery': 1})))
            by_id = {row['task_id']: row.get('output_json', {}).get('generation_recovery') for row in recoveries}
            for item in items:
                item['recovery'] = by_id.get(item.get('task_id'))

            return items, total
        except PyMongoError as exc:
            logger.error("查询任务列表失败 user=%s: %s", user_scope, exc)
            raise TaskHistoryError(f"查询任务列表失败：{exc}") from exc

    async def statistics(self, user_scope: str) -> dict[str, Any]:
        try:
            collection = await self._get_collection()
            grouped = await asyncio.to_thread(lambda: list(collection.aggregate([
                {"$match": {"user_scope": user_scope}},
                {"$group": {"_id": {"task_type": "$task_type", "status": "$status"}, "count": {"$sum": 1}}},
            ])))
            result: dict[str, Any] = {"total": 0, "statuses": {}, "status_groups": {}, "agents": {"assessment": {"total": 0}, "topic": {"total": 0}}}
            for row in grouped:
                status = row["_id"].get("status") or "unknown"
                count = row["count"]
                result["total"] += count
                result["statuses"][status] = result["statuses"].get(status, 0) + count
                agent = "topic" if row["_id"].get("task_type") == "智能选题" else "assessment"
                result["agents"][agent][status] = result["agents"][agent].get(status, 0) + count
                result["agents"][agent]["total"] += count
            result["status_groups"] = _status_groups(result["statuses"])
            return result
        except PyMongoError as exc:
            raise TaskHistoryError(f"查询状态统计失败：{exc}") from exc

    async def list_topic_recoveries(self) -> list[dict[str, Any]]:
        try:
            collection = await self._get_collection()
            return await asyncio.to_thread(lambda: list(collection.find({
                'task_type': '智能选题', 'status': {'$in': list(ACTIVE_STATUSES)},
                'output_json.generation_recovery.enabled': True},
                {'_id': 0, 'task_id': 1, 'user_scope': 1, 'status': 1, 'updated_at': 1, 'state': 1})))
        except PyMongoError as exc:
            raise TaskHistoryError('自动恢复记录读取暂不可用') from exc

    async def reconcile_interrupted(self, timestamp: str) -> int:
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(collection.update_many,
                {"status": {"$in": list(ACTIVE_STATUSES)}, 'output_json.generation_recovery.enabled': {'$ne': True}, "$or": [
                    {"updated_at": {"$lt": interrupted_cutoff(timestamp)}},
                    {"updated_at": {"$exists": False}, "created_at": {"$lt": interrupted_cutoff(timestamp)}}]}, {"$set": {
                    "status": "failed", "current_stage": "failed",
                    "updated_at": timestamp, "completed_at": timestamp,
                    "error_message": "服务中断，任务执行者已退出，请重新创建任务"}})
            await asyncio.to_thread(collection.update_many,
                {"status": "failed", "updated_at": timestamp, "state": {"$type": "object"}},
                {"$set": {"state.current_stage": "failed"}})
            return result.modified_count
        except PyMongoError as exc:
            raise TaskHistoryError(f"恢复遗留任务状态失败：{exc}") from exc

    async def delete(self, task_id: str, user_scope: str) -> bool:
        """删除任务记录。只允许删除自己的任务。"""
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(
                collection.delete_one,
                {"task_id": task_id, "user_scope": user_scope},
            )
            return result.deleted_count > 0
        except PyMongoError as exc:
            logger.error("删除任务记录失败 task_id=%s: %s", task_id, exc)
            raise TaskHistoryError(f"删除任务记录失败：{exc}") from exc

    async def delete_history(self, user_scope: str, *, task_type: str | None = None,
                             task_id: str | None = None, dry_run: bool = False,
                             is_running: Callable[[str], bool] | None = None) -> dict[str, Any]:
        try:
            collection = await self._get_collection()
            query: dict[str, Any] = {"user_scope": user_scope}
            if task_type == "智能选题":
                query["task_type"] = task_type
            else:
                types = ["创新性评估", "内容评估"] if task_type == "创新性评估" else list(HISTORY_TASK_TYPES)
                query["$or"] = [{"task_type": {"$in": types}},
                                {"task_type": {"$exists": False}}]
            if task_id:
                query["task_id"] = task_id
            records = await asyncio.to_thread(lambda: list(collection.find(query, {"task_id": 1, "status": 1, "updated_at": 1})))
            eligible = [task for task in records if task.get("status") in HISTORY_DELETABLE_STATUSES
                        and not (is_running and is_running(task["task_id"]))]
            deleted_ids = []
            if not dry_run:
                # Re-check each snapshot atomically: a resumed/changed task must
                # never be deleted based on a stale history-list observation.
                for task in eligible:
                    if is_running and is_running(task["task_id"]):
                        continue
                    deleted = await asyncio.to_thread(collection.find_one_and_delete, {
                        **query, "_id": task["_id"], "status": {"$in": list(HISTORY_DELETABLE_STATUSES)},
                        "updated_at": task.get("updated_at"),
                    })
                    if deleted:
                        deleted_ids.append(task["task_id"])
                logger.info("history_delete scope=%s task_type=%s task_ids=%s deleted=%s", user_scope, task_type, deleted_ids, len(deleted_ids))
            reasons = dict(Counter("executor_running" if task.get("status") in HISTORY_DELETABLE_STATUSES else task.get("status", "unknown") for task in records if task not in eligible))
            changed = 0 if dry_run else len(eligible) - len(deleted_ids)
            if changed:
                reasons["state_changed"] = changed
            return {"success": True, "eligible_count": len(eligible), "deleted_count": len(deleted_ids),
                    "task_ids": deleted_ids, "skipped_count": len(records) - len(eligible) + changed,
                    "skipped_reasons": reasons}
        except PyMongoError as exc:
            raise TaskHistoryError(f"清理任务历史失败：{exc}") from exc

    async def list_running(self, user_scope: str, *, task_type: str | None = None) -> list[dict[str, Any]]:
        """查询当前用户所有进行中的任务。"""
        try:
            collection = await self._get_collection()
            items = await asyncio.to_thread(
                lambda: list(
                    collection.find(
                        {
                            "user_scope": user_scope,
                            "status": {"$in": list(ACTIVE_STATUSES)},
                            **({"task_type": task_type} if task_type else {}),
                        },
                        {"task_id": 1, "title": 1, "status": 1, "created_at": 1},
                    ).sort("created_at", -1)
                )
            )
            for item in items:
                if "_id" in item:
                    item["_id"] = str(item["_id"])
            return items
        except PyMongoError as exc:
            logger.error("查询进行中任务失败 user=%s: %s", user_scope, exc)
            raise TaskHistoryError(f"查询进行中任务失败：{exc}") from exc

    async def exists_running(self, user_scope: str) -> tuple[bool, str | None]:
        """检查当前用户是否有进行中的任务。"""
        try:
            collection = await self._get_collection()
            doc = await asyncio.to_thread(
                collection.find_one,
                {
                    "user_scope": user_scope,
                    "status": {"$in": list(ACTIVE_STATUSES)},
                    "$or": [{"output_json.generation_recovery.enabled": {"$ne": True}}, {"output_json.generation_recovery.phase": "running"}],
                },
                {"task_id": 1},
                sort=[("created_at", -1)],
            )
            if doc:
                return True, doc.get("task_id", "")
            return False, None
        except PyMongoError as exc:
            logger.error("检查进行中任务失败 user=%s: %s", user_scope, exc)
            raise TaskHistoryError(f"检查进行中任务失败：{exc}") from exc

    async def delete_many(self, task_ids: list[str], user_scope: str) -> int:
        """批量删除任务记录。只允许删除自己的任务。"""
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(
                collection.delete_many,
                {"task_id": {"$in": task_ids}, "user_scope": user_scope},
            )
            return result.deleted_count
        except PyMongoError as exc:
            logger.error("批量删除任务记录失败 task_ids=%s: %s", task_ids, exc)
            raise TaskHistoryError(f"批量删除任务记录失败：{exc}") from exc


# 模块级单例，供路由直接使用
_task_history_repo: ContentAssessmentTaskHistoryRepository | None = None


def get_task_history_repo() -> ContentAssessmentTaskHistoryRepository | InMemoryTaskHistoryRepository:
    if USE_MEMORY_STORE:
        global _memory_repo
        if _memory_repo is None:
            _memory_repo = InMemoryTaskHistoryRepository()
        return _memory_repo
    global _task_history_repo
    if _task_history_repo is None:
        _task_history_repo = ContentAssessmentTaskHistoryRepository()
    return _task_history_repo


_memory_repo: InMemoryTaskHistoryRepository | None = None
