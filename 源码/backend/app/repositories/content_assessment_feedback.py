"""Feedback storage for the innovation assessment agent."""

from __future__ import annotations

import asyncio
import logging
import os
from copy import deepcopy
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from app.core.config import FEEDBACK_COLLECTION, MONGO_DB, MONGO_URI, MONGO_URI_SAFE

logger = logging.getLogger(__name__)

USE_MEMORY_STORE = os.getenv("USE_MEMORY_STORE", "").strip().lower() in ("true", "1", "yes", "on")


class FeedbackStorageError(RuntimeError):
    """Feedback persistence error."""


class InMemoryFeedbackRepository:
    """In-memory feedback repository for local tests."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def create(self, record: dict[str, Any]) -> str:
        async with self._lock:
            self._records.append(deepcopy(record))
            return record.get("feedback_id", "")


class ContentAssessmentFeedbackRepository:
    """MongoDB feedback repository."""

    def __init__(self) -> None:
        self._client: MongoClient | None = None
        self._collection: Collection | None = None

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
            self._collection = db[FEEDBACK_COLLECTION]
            self._collection.create_index(
                [("user_scope", ASCENDING), ("created_at", DESCENDING)],
                name="idx_user_scope_created",
            )
            self._collection.create_index(
                [("feedback_id", ASCENDING)],
                name="idx_feedback_id",
                unique=True,
            )
            logger.info("Feedback MongoDB connected db=%s collection=%s", MONGO_DB, FEEDBACK_COLLECTION)
        except PyMongoError as exc:
            logger.error("Feedback MongoDB connect failed uri=%s: %s", MONGO_URI_SAFE, exc)
            raise FeedbackStorageError(f"无法连接 MongoDB ({MONGO_URI_SAFE})：{exc}") from exc

    async def create(self, record: dict[str, Any]) -> str:
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(collection.insert_one, deepcopy(record))
            return str(result.inserted_id)
        except PyMongoError as exc:
            logger.error("保存意见反馈失败 feedback_id=%s: %s", record.get("feedback_id"), exc)
            raise FeedbackStorageError(f"保存意见反馈失败：{exc}") from exc


_feedback_repo: ContentAssessmentFeedbackRepository | None = None
_memory_repo: InMemoryFeedbackRepository | None = None


def get_feedback_repo() -> ContentAssessmentFeedbackRepository | InMemoryFeedbackRepository:
    if USE_MEMORY_STORE:
        global _memory_repo
        if _memory_repo is None:
            _memory_repo = InMemoryFeedbackRepository()
        return _memory_repo
    global _feedback_repo
    if _feedback_repo is None:
        _feedback_repo = ContentAssessmentFeedbackRepository()
    return _feedback_repo
