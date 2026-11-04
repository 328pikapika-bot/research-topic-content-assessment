"""Parsed upload-file storage for the innovation assessment agent.

The server parses uploads and checks content safety before storing text. The agent run
request only carries file metadata; runtime resolves content through this
repository using user_scope + file_id.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from copy import deepcopy
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from app.core.config import (
    MAX_PARSED_FILE_CONTENT_CHARS,
    MONGO_DB,
    MONGO_URI,
    MONGO_URI_SAFE,
    PARSED_FILE_COLLECTION,
)

logger = logging.getLogger(__name__)

USE_MEMORY_STORE = os.getenv("USE_MEMORY_STORE", "").strip().lower() in ("true", "1", "yes", "on")


class ParsedFileError(RuntimeError):
    """Parsed file storage error."""


def _now_iso() -> str:
    from app.services.task_persistence import now_beijing_iso

    return now_beijing_iso()


def _normalize_file_id(value: Any) -> str:
    return str(value or "").strip()


def _normalize_file_type(value: Any) -> str:
    file_type = str(value or "").strip().lower().lstrip(".")
    if file_type == "markdown":
        return "md"
    return file_type


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


def _normalize_anchors(value: Any, content: str, file_type: str) -> list[dict[str, Any]]:
    anchors = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for item in anchors:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0, int(item.get("char_start", 0)))
            end = min(len(content), int(item.get("char_end", 0)))
        except (TypeError, ValueError):
            continue
        if end <= start or content[start:end].strip() == "":
            continue
        locator = item.get("locator") if isinstance(item.get("locator"), dict) else {}
        normalized.append({
            "char_start": start,
            "char_end": end,
            "text": content[start:end][:500],
            "locator": {"kind": file_type, **locator},
        })
    if normalized:
        return normalized[:20000]
    offset = 0
    for line_number, line in enumerate(content.splitlines(keepends=True), start=1):
        text = line.rstrip("\r\n")
        end = offset + len(text)
        if text.strip():
            normalized.append({
                "char_start": offset,
                "char_end": end,
                "text": text[:500],
                "locator": {"kind": file_type, "line": line_number},
            })
        offset += len(line)
    return normalized[:20000]


def normalize_parsed_file_payload(file_data: dict[str, Any], user_scope: str) -> dict[str, Any]:
    """Normalize frontend camelCase payload into repository record fields."""
    file_id = _normalize_file_id(file_data.get("file_id") or file_data.get("fileId"))
    name = str(file_data.get("name") or "").strip()
    file_type = _normalize_file_type(file_data.get("type"))
    content = str(file_data.get("content") or "")
    original_content_size = len(content)
    truncated = False
    if len(content) > MAX_PARSED_FILE_CONTENT_CHARS:
        content = content[:MAX_PARSED_FILE_CONTENT_CHARS]
        truncated = True
    now = _now_iso()
    return {
        "file_id": file_id,
        "user_scope": user_scope,
        "name": name,
        "type": file_type,
        "content": content,
        "size": int(file_data.get("size") or 0),
        "content_size": len(content),
        "original_content_size": original_content_size,
        "content_hash": _content_hash(content),
        "parsed_at": str(file_data.get("parsed_at") or file_data.get("parsedAt") or now),
        "url": str(file_data.get("url") or ""),
        "source": str(file_data.get("source") or "client_parser"),
        "sensitive_check_status": str(file_data.get("sensitive_check_status") or "unknown"),
        "page_count": int(file_data.get("page_count") or 0) or None,
        "anchor_schema_version": str(file_data.get("anchor_schema_version") or "anchor-v1"),
        "anchors": _normalize_anchors(file_data.get("anchors"), content, file_type),
        "truncated": truncated,
        "created_at": now,
        "updated_at": now,
        "last_used_at": "",
    }


class InMemoryParsedFileRepository:
    """In-memory parsed file repository for local tests."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def ping(self) -> dict[str, Any]:
        return {"status": "ok", "database": "in-memory", "collection": PARSED_FILE_COLLECTION}

    async def batch_create(self, records: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        saved = 0
        errors: list[dict[str, Any]] = []
        data: list[dict[str, Any]] = []
        async with self._lock:
            for record in records:
                file_id = record.get("file_id", "")
                user_scope = record.get("user_scope", "")
                try:
                    self._records[(user_scope, file_id)] = deepcopy(record)
                    saved += 1
                    data.append(self._public_record(record, include_content=False))
                except Exception as exc:
                    errors.append({"file_id": file_id, "error": str(exc)})
        return saved, errors, data

    async def get(self, file_id: str, user_scope: str, *, include_content: bool = True) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get((user_scope, file_id))
            if not record:
                return None
            return self._public_record(record, include_content=include_content)

    async def list_files(self, user_scope: str, *, include_content: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            records = [
                self._public_record(record, include_content=include_content)
                for (scope, _), record in self._records.items()
                if scope == user_scope
            ]
        return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)

    async def delete(self, file_id: str, user_scope: str) -> bool:
        async with self._lock:
            return self._records.pop((user_scope, file_id), None) is not None

    async def clear(self, user_scope: str) -> int:
        async with self._lock:
            keys = [key for key in self._records if key[0] == user_scope]
            for key in keys:
                self._records.pop(key, None)
            return len(keys)

    async def touch_used(self, file_ids: list[str], user_scope: str) -> None:
        now = _now_iso()
        async with self._lock:
            for file_id in file_ids:
                record = self._records.get((user_scope, file_id))
                if record:
                    record["last_used_at"] = now

    @staticmethod
    def _public_record(record: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
        data = deepcopy(record)
        if not include_content:
            data.pop("content", None)
        return data


class ContentAssessmentParsedFileRepository:
    """MongoDB parsed file repository."""

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
            self._collection = db[PARSED_FILE_COLLECTION]
            self._collection.create_index(
                [("user_scope", ASCENDING), ("created_at", DESCENDING)],
                name="idx_user_scope_created",
            )
            self._collection.create_index(
                [("user_scope", ASCENDING), ("file_id", ASCENDING)],
                name="idx_user_file",
                unique=True,
            )
            logger.info("Parsed file MongoDB connected db=%s collection=%s", MONGO_DB, PARSED_FILE_COLLECTION)
        except PyMongoError as exc:
            logger.error("Parsed file MongoDB connect failed uri=%s: %s", MONGO_URI_SAFE, exc)
            raise ParsedFileError(f"无法连接 MongoDB ({MONGO_URI_SAFE})：{exc}") from exc

    async def ping(self) -> dict[str, Any]:
        try:
            if self._client is None:
                await asyncio.to_thread(self._connect)
            assert self._client is not None
            await asyncio.to_thread(self._client.admin.command, "ping")
            return {
                "status": "ok",
                "database": MONGO_DB,
                "collection": PARSED_FILE_COLLECTION,
                "uri": MONGO_URI_SAFE,
            }
        except ParsedFileError:
            raise
        except PyMongoError as exc:
            raise ParsedFileError(f"MongoDB ping 失败 ({MONGO_URI_SAFE})：{exc}") from exc

    async def batch_create(self, records: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        if not records:
            return 0, [], []
        try:
            collection = await self._get_collection()

            def write() -> tuple[int, list[dict[str, Any]]]:
                operations = []
                for record in records:
                    doc = deepcopy(record)
                    doc["_id"] = f"{doc['user_scope']}:{doc['file_id']}"
                    operations.append(
                        UpdateOne(
                            {"_id": doc["_id"], "user_scope": doc["user_scope"]},
                            {
                                "$set": {k: v for k, v in doc.items() if k not in {"_id", "created_at"}},
                                "$setOnInsert": {"created_at": doc.get("created_at") or _now_iso()},
                            },
                            upsert=True,
                        )
                    )
                collection.bulk_write(operations, ordered=False)
                return len(records), []

            saved, errors = await asyncio.to_thread(write)
            data = [self._public_record(record, include_content=False) for record in records]
            return saved, errors, data
        except PyMongoError as exc:
            logger.error("Parsed file batch create failed: %s", exc)
            raise ParsedFileError(f"保存解析文件失败：{exc}") from exc

    async def get(self, file_id: str, user_scope: str, *, include_content: bool = True) -> dict[str, Any] | None:
        try:
            collection = await self._get_collection()
            projection = None if include_content else {"content": 0}
            doc = await asyncio.to_thread(
                collection.find_one,
                {"_id": f"{user_scope}:{file_id}", "user_scope": user_scope},
                projection,
            )
            if not doc:
                return None
            return self._public_record(doc, include_content=include_content)
        except PyMongoError as exc:
            raise ParsedFileError(f"查询解析文件失败：{exc}") from exc

    async def list_files(self, user_scope: str, *, include_content: bool = False) -> list[dict[str, Any]]:
        try:
            collection = await self._get_collection()
            projection = None if include_content else {"content": 0}
            items = await asyncio.to_thread(
                lambda: list(
                    collection.find({"user_scope": user_scope}, projection)
                    .sort("created_at", -1)
                )
            )
            return [self._public_record(item, include_content=include_content) for item in items]
        except PyMongoError as exc:
            raise ParsedFileError(f"查询解析文件列表失败：{exc}") from exc

    async def delete(self, file_id: str, user_scope: str) -> bool:
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(
                collection.delete_one,
                {"_id": f"{user_scope}:{file_id}", "user_scope": user_scope},
            )
            return result.deleted_count > 0
        except PyMongoError as exc:
            raise ParsedFileError(f"删除解析文件失败：{exc}") from exc

    async def clear(self, user_scope: str) -> int:
        try:
            collection = await self._get_collection()
            result = await asyncio.to_thread(collection.delete_many, {"user_scope": user_scope})
            return int(result.deleted_count)
        except PyMongoError as exc:
            raise ParsedFileError(f"清空解析文件失败：{exc}") from exc

    async def touch_used(self, file_ids: list[str], user_scope: str) -> None:
        if not file_ids:
            return
        try:
            collection = await self._get_collection()
            ids = [f"{user_scope}:{file_id}" for file_id in file_ids]
            await asyncio.to_thread(
                collection.update_many,
                {"_id": {"$in": ids}, "user_scope": user_scope},
                {"$set": {"last_used_at": _now_iso()}},
            )
        except PyMongoError as exc:
            raise ParsedFileError(f"更新解析文件使用时间失败：{exc}") from exc

    @staticmethod
    def _public_record(record: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
        data = deepcopy(record)
        data.pop("_id", None)
        if not include_content:
            data.pop("content", None)
        return data


_parsed_file_repo: ContentAssessmentParsedFileRepository | None = None
_memory_repo: InMemoryParsedFileRepository | None = None


def get_parsed_file_repo() -> ContentAssessmentParsedFileRepository | InMemoryParsedFileRepository:
    if USE_MEMORY_STORE:
        global _memory_repo
        if _memory_repo is None:
            _memory_repo = InMemoryParsedFileRepository()
        return _memory_repo
    global _parsed_file_repo
    if _parsed_file_repo is None:
        _parsed_file_repo = ContentAssessmentParsedFileRepository()
    return _parsed_file_repo
