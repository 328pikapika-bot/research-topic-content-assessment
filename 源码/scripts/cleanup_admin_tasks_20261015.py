"""Scoped, recoverable one-off cleanup requested on 2026-10-15 (Beijing)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from app.core import config

SCOPE = "platform_admin"
TYPES = {"智能选题", "创新性评估", "内容评估"}
CONFIRMATION = {"awaiting_confirmation", "awaiting_clarification"}
CUTOFF = datetime(2026, 10, 15, tzinfo=timezone(timedelta(hours=8)))


def eligible(task):
    if task.get("user_scope") != SCOPE or task.get("task_type", "创新性评估") not in TYPES:
        return False
    value = task.get("created_at")
    created = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created < CUTOFF or task.get("status") in CONFIRMATION


def digest(task):
    return hashlib.sha256(json_util.dumps(task, sort_keys=True).encode()).hexdigest()


def write_backup(collection, directory):
    directory.mkdir(parents=True, exist_ok=False)
    tasks = list(collection.find({}))
    deleting = [task for task in tasks if eligible(task)]
    # Never delete an active worker from a separate process. Stop it through
    # the owning service first, then build a fresh backup of its final state.
    active = [t["task_id"] for t in deleting if t.get("status") in {"queued", "running", "processing"}]
    if active:
        raise RuntimeError("Active tasks must be stopped before cleanup: " + ", ".join(active))
    with (directory / "tasks.bson.jsonl").open("x", encoding="utf-8") as output:
        for task in deleting:
            output.write(json_util.dumps(task) + "\n")
        output.flush()
        os.fsync(output.fileno())
    manifest = {"scope": SCOPE, "cutoff": CUTOFF.isoformat(),
        "delete": [{"task_id": t["task_id"], "title": t.get("title"), "status": t.get("status"),
                    "created_at": t.get("created_at"), "updated_at": t.get("updated_at"), "digest": digest(t)} for t in deleting],
        "preserve": [{"task_id": t["task_id"], "user_scope": t.get("user_scope"), "digest": digest(t)}
                     for t in tasks if not eligible(t)]}
    (directory / "manifest.json").write_text(json_util.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def apply_backup(collection, directory):
    manifest = json_util.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    backed = [json_util.loads(line) for line in (directory / "tasks.bson.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest["scope"] == SCOPE and manifest["cutoff"] == CUTOFF.isoformat()
    assert {t["task_id"] for t in backed} == {t["task_id"] for t in manifest["delete"]}
    # Review the entire scope before making the first mutation.
    for task in backed:
        current = collection.find_one({"task_id": task["task_id"], "user_scope": SCOPE})
        if not current or digest(current) != digest(task) or not eligible(current):
            raise RuntimeError("Task changed after backup: " + task["task_id"])
    deleted = []
    for task in backed:
        result = collection.delete_one({"_id": task["_id"], "task_id": task["task_id"], "user_scope": SCOPE,
            "task_type": task.get("task_type", "创新性评估"), "status": task.get("status"),
            "updated_at": task.get("updated_at"), "created_at": task.get("created_at")})
        if result.deleted_count != 1:
            raise RuntimeError("Concurrent change; cleanup stopped: " + task["task_id"])
        deleted.append(task["task_id"])
        (directory / "deleted.json").write_text(json.dumps(deleted, indent=2), encoding="utf-8")
    for row in manifest["preserve"]:
        current = collection.find_one({"task_id": row["task_id"], "user_scope": row["user_scope"]})
        if not current or digest(current) != row["digest"]:
            raise RuntimeError("Preserved task changed: " + row["task_id"])
    remaining = list(collection.find({"user_scope": SCOPE}, {"task_id": 1, "created_at": 1, "status": 1, "task_type": 1, "user_scope": 1}))
    assert not any(eligible(task) for task in remaining)
    return {"deleted": len(deleted), "remaining": len(remaining), "preserved_verified": len(manifest["preserve"])}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    directory = Path(args.backup_dir).resolve()
    collection = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)[config.MONGO_DB][config.TASK_COLLECTION]
    if args.apply:
        print(json.dumps(apply_backup(collection, directory), ensure_ascii=False))
    else:
        manifest = write_backup(collection, directory)
        print(json.dumps({"backed_up": len(manifest["delete"]), "preserve": len(manifest["preserve"]),
                          "backup_dir": str(directory)}, ensure_ascii=False))
