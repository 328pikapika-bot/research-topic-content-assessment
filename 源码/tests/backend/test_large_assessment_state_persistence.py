"""Large completed assessment states survive Mongo's document limit."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from bson import BSON

from app.repositories.content_assessment_task_history import (
    ContentAssessmentTaskHistoryRepository, _hydrate_large_state,
    _pack_large_state,
)


class Collection:
    def __init__(self):
        self.record = {"task_id": "run-large", "user_scope": "owner", "updated_at": "old"}

    def find_one(self, selector, projection=None):
        if any(self.record.get(key) != value for key, value in selector.items()):
            return None
        if projection:
            return {key: deepcopy(value) for key, value in self.record.items() if key in projection}
        return deepcopy(self.record)

    def update_one(self, selector, operation):
        if not self.find_one(selector):
            return SimpleNamespace(matched_count=0)
        for path, value in operation["$set"].items():
            target = self.record
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(value)
        return SimpleNamespace(matched_count=1)


def test_large_checkpoint_round_trip_and_report_compare_and_set(monkeypatch):
    async def scenario():
        collection = Collection()
        repo = ContentAssessmentTaskHistoryRepository()

        async def get_collection():
            return collection

        monkeypatch.setattr(repo, "_get_collection", get_collection)
        state = {"current_stage": "report", "output_json": {"generation_recovery": {"stage": "report"}},
                 "output_markdown": "report text", "report_snapshot": {"snapshot_id": "first"},
                 "large_report": "研究材料" * 1_600_000}
        assert await repo.update("run-large", "owner", {"state": state, "updated_at": "new"})
        assert collection.record["state_blob"]
        assert len(BSON.encode(collection.record)) < 16 * 1024 * 1024
        assert collection.record["state"] == {"current_stage": "report"}
        saved = await repo.get("run-large", "owner")
        assert saved["state"] == state
        assert saved["output_json"] == state["output_json"]
        assert saved["output_markdown"] == state["output_markdown"]
        assert not await repo.update_report("run-large", "owner", "old", {"state.report_snapshot": {}})
        assert await repo.update_report("run-large", "owner", "new", {
            "state.report_snapshot": {"snapshot_id": "second"},
            "state.output_json.render_metrics": {"pages": 4},
            "output_json.render_metrics": {"pages": 4},
            "output_markdown": "revised report text", "updated_at": "newer"})
        revised = await repo.get("run-large", "owner")
        assert revised["state"]["report_snapshot"] == {"snapshot_id": "second"}
        assert revised["state"]["large_report"] == state["large_report"]
        assert revised["output_json"]["render_metrics"] == {"pages": 4}
        assert revised["output_markdown"] == "revised report text"
        assert not await repo.get("run-large", "another-owner")

    asyncio.run(scenario())


def test_small_checkpoint_clears_previous_blob():
    large = _pack_large_state({"state": {"current_stage": "report", "body": "长" * 3_000_000}})
    assert large["state_blob"]
    small = _pack_large_state({"state": {"current_stage": "completed", "output_json": {"ready": True}}})
    assert small["state_blob"] is None
    assert _hydrate_large_state(small)["state"]["output_json"] == {"ready": True}
