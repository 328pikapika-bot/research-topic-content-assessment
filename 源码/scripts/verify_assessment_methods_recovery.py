"""Replay one saved report checkpoint offline; never update task history.

The model stub deliberately rejects method prose to exercise factual fallback.
This verifies publication/recovery, not the availability of a live model.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["USE_MEMORY_STORE"] = "true"
os.environ["CONTENT_ASSESSMENT_LLM_ENABLED"] = "false"
os.environ["REPORT_PREFLIGHT_ENABLED"] = "false"

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.report_methods import METHOD_KEYS, method_packet, structured_method_rows
from app.agents.content_assessment.schemas import ContentAssessmentState


async def verify(source: Path, target: Path):
    source_bytes = source.read_bytes()
    task = json.loads(source_bytes.decode("utf-8-sig"))
    state = ContentAssessmentState.model_validate(task["state"])
    assert state.report_snapshot is None, "Use a failed, unpublished checkpoint"
    before = deepcopy(state.model_dump())
    methods_before = deepcopy(state.output_json.get("scholarly_methods", {}))
    graph = ContentAssessmentGraph()
    calls = []

    async def rejecting_model(_state, **kwargs):
        prompt = kwargs["user_prompt"]
        calls.append(kwargs["stage"])
        assert kwargs["stage"] == "report", "Earlier stages must not run"
        if "撰写科研评估报告第四章" in prompt:
            return structured_method_rows(_state)
        # Editorial polish is optional: preserve the exact accepted prose.
        if "仅润色下列" in prompt:
            return None
        assert "逐段核对下面评议" in prompt, "Unexpected content regeneration"
        return {"checked": False, "replacements": []}

    graph._request_model_json = rejecting_model
    state.current_stage = state.resume_from_stage = "report"
    state = await graph.run(state)
    assert state.current_stage == "completed", state.output_json.get("generation_recovery")
    assert state.report_snapshot and state.output_markdown
    assert state.output_json["report_publication"]["status"] == "published"
    assert "generation_recovery" not in state.output_json
    assert state.output_json["scholarly_drafts"] == before["output_json"]["scholarly_drafts"]
    for key, rows in methods_before.items():
        assert state.output_json["scholarly_methods"][key] == rows
    for key in ("claims", "evidence_records", "claim_evidence_links", "query_plans", "novelty_decisions"):
        assert state.model_dump()[key] == before[key], key
    assert state.model_dump()["stage_trace"][:7] == before["stage_trace"][:7]
    packet = method_packet(state)
    mapping = state.output_json["scholarly_methods"]["mapping"]
    for point in packet["point"]["research_points"]:
        ids = point["representative_ids"]
        if ids:
            assert any(point["name"] in row["text"] and row["evidence_ids"] == ids for row in mapping)
    assert source.read_bytes() == source_bytes

    target.mkdir(parents=True, exist_ok=True)
    (target / "recovered-state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
    (target / "report.md").write_text(state.output_markdown, encoding="utf-8")

    # Exercise the real detail and export routes against an isolated repository.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import routes
    from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository
    repository = InMemoryTaskHistoryRepository()
    scope = task["user_scope"]
    record = deepcopy(task)
    record.update(status="completed", current_stage="completed", error_message=None,
                  state=state.model_dump(), output_markdown=state.output_markdown,
                  output_json=state.output_json, stage_trace=state.model_dump()["stage_trace"])
    await repository.create(record)
    original_repo = routes.get_task_history_repo
    routes.get_task_history_repo = lambda: repository
    app = FastAPI()
    app.include_router(routes.router)
    try:
        with TestClient(app) as client:
            headers = {"X-User-Scope": scope}
            url = f"/v1/content-assessment/tasks/{task['task_id']}"
            detail = client.get(url, headers=headers)
            assert detail.status_code == 200, detail.text[:300]
            assert detail.json()["status"] == "completed"
            for format in ("md", "html"):
                response = client.get(url + "/export", params={"format": format}, headers=headers)
                assert response.status_code == 200, response.text[:300]
                assert "source_review_rejected" not in response.text
                (target / f"report.{format}").write_bytes(response.content)
    finally:
        routes.get_task_history_repo = original_repo
    result = {
        "task_id": task["task_id"], "mode": "offline_source_rejection_reproduction",
        "status": state.current_stage, "snapshot_id": state.report_snapshot.snapshot_id,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_unchanged": True, "earlier_stages_unchanged": True,
        "accepted_chapters_unchanged": True, "previous_methods_unchanged": True,
        "sections": len(state.report_snapshot.sections), "report_only_model_stubs": len(calls),
        "methods": {key: state.output_json["scholarly_methods_completion"].get(key) for key in METHOD_KEYS},
        "local_api_checks": {"detail": 200, "markdown": 200, "html": 200},
    }
    (target / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.source.resolve(), args.output_dir.resolve()))
