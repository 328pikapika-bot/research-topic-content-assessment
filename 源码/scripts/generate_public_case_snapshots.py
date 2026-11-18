from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.content_assessment.schemas import ContentAssessmentState
from app.agents.topic_selection.schemas import TopicSelectionState
from app.services.content_assessment_service import ContentAssessmentService
from app.services.public_cases import CASE_IDS, CASE_ROOT, CASE_SNAPSHOT_VERSION, _complete_case
from app.services.topic_selection_service import TopicSelectionService

CATALOG = ROOT / "frontend" / "web" / "src" / "services" / "homepageCases.json"
STAGING = ROOT / ".tmp" / "public_case_snapshots" / CASE_SNAPSHOT_VERSION
CHECKPOINTS = STAGING / ".checkpoints"
DESTINATION = CASE_ROOT / "snapshots" / CASE_SNAPSHOT_VERSION
MAX_RECOVERIES = 3
GENERATOR_REVISION = 2


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported snapshot value: {type(value).__name__}")


def _as_dict(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def load_catalog() -> dict[str, dict[str, Any]]:
    cases = json.loads(CATALOG.read_text(encoding="utf-8"))
    result = {item["id"]: item for item in cases}
    if set(result) != set(CASE_IDS):
        raise RuntimeError("Homepage case IDs differ from the public case registry.")
    return result


def read_staged(case_id: str) -> dict[str, Any] | None:
    path = STAGING / f"{case_id}.json.gz"
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if payload.get("case_id") == case_id and _complete_case(case_id, payload.get("detail") or {}):
            return payload
    except (OSError, EOFError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def write_staged(case_id: str, payload: dict[str, Any]) -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    target = STAGING / f"{case_id}.json.gz"
    temporary = target.with_suffix(".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=9) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    temporary.replace(target)


def checkpoint_path(case_id: str) -> Path:
    return CHECKPOINTS / f"{case_id}.json.gz"


def write_checkpoint(case_id: str, case: dict[str, Any], state: Any, recovery_attempts: int) -> None:
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    target = checkpoint_path(case_id)
    temporary = target.with_suffix(".tmp")
    payload = {
        "case_id": case_id,
        "version": CASE_SNAPSHOT_VERSION,
        "agent": case["agent"],
        "generator_revision": GENERATOR_REVISION,
        "recovery_attempts": recovery_attempts,
        "state": _as_dict(state),
    }
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    temporary.replace(target)


def read_checkpoint(case_id: str, case: dict[str, Any]) -> tuple[Any, int] | None:
    path = checkpoint_path(case_id)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if (payload.get("case_id") != case_id or payload.get("version") != CASE_SNAPSHOT_VERSION
                or payload.get("agent") != case["agent"]):
            return None
        model = ContentAssessmentState if case["agent"] == "assessment" else TopicSelectionState
        attempts = max(0, int(payload.get("recovery_attempts", 0)))
        if payload.get("generator_revision") != GENERATOR_REVISION:
            attempts = 0
        return model.model_validate(payload["state"]), attempts
    except (OSError, EOFError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def progress(case_id: str, case: dict[str, Any], recovery_state: list[int]):
    last_saved = 0.0
    last_status = None

    async def on_update(state: Any) -> None:
        nonlocal last_saved, last_status
        trace = next((item for item in state.stage_trace if item.stage_key == state.current_stage), None)
        status = trace.status if trace else "running"
        identity = (state.current_stage, status)
        now = time.monotonic()
        if identity != last_status or now - last_saved >= 15 or status != "running":
            name = trace.stage_name if trace else state.current_stage
            print(f"[{case_id}] {name}: {status}", flush=True)
            write_checkpoint(case_id, case, state, recovery_state[0])
            last_status = identity
            last_saved = now
    return on_update


async def generate(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
    checkpoint = read_checkpoint(case_id, case)
    recovery_attempts = checkpoint[1] if checkpoint else 0
    recovery_state = [recovery_attempts]
    if checkpoint:
        state = checkpoint[0]
        print(f"[{case_id}] resuming saved stage {state.resume_from_stage or state.current_stage}", flush=True)
    callback = progress(case_id, case, recovery_state)
    if case["agent"] == "assessment":
        service = ContentAssessmentService()
        if checkpoint:
            state = checkpoint[0]
        else:
            state = service.create_initial_state(case["input"], scenario_id=case.get("scenarioId", ""))
        while True:
            try:
                state = await service.run(state, on_stage_update=callback)
                if state.current_stage != "failed" or not state.resume_from_stage:
                    break
                raise RuntimeError("评估流程返回失败状态，缺少可恢复阶段")
            except Exception as exc:
                if recovery_attempts >= MAX_RECOVERIES:
                    write_checkpoint(case_id, case, state, recovery_attempts)
                    raise
                recovery_attempts += 1
                recovery_state[0] = recovery_attempts
                write_checkpoint(case_id, case, state, recovery_attempts)
                if not state.resume_from_stage:
                    raise
                state.current_stage = state.resume_from_stage
                write_checkpoint(case_id, case, state, recovery_attempts)
                print(f"[{case_id}] resuming {state.resume_from_stage} after {type(exc).__name__}", flush=True)
                state.confirmation.needed = False
                state.output_json.pop("generation_recovery", None)
                state.output_json.pop("pause_reason", None)
                await asyncio.sleep(min(2 * recovery_attempts, 5))
        status = "completed" if state.current_stage == "completed" else state.current_stage
        title = state.assessment_profile.title or case["title"]
        output = state.output_markdown
        created_at = state.report_snapshot.generated_at if state.report_snapshot else datetime.now().astimezone().isoformat()
    else:
        service = TopicSelectionService()
        if not checkpoint:
            state = service.create_initial_state(case["input"], candidate_count=3, output_preference="balanced", analysis_mode="deep")
        from app.agents.topic_selection.recovery import GenerationRecoveryRequired
        while True:
            try:
                state = await service.run(state, on_stage_update=callback)
                break
            except GenerationRecoveryRequired as exc:
                recovery_attempts += 1
                recovery_state[0] = recovery_attempts
                write_checkpoint(case_id, case, state, recovery_attempts)
                if recovery_attempts > MAX_RECOVERIES:
                    raise
                print(f"[{case_id}] resuming {state.resume_from_stage or exc.stage} after {exc.reason}", flush=True)
                await asyncio.sleep(min(2 * recovery_attempts, 5))
        status = "completed" if state.current_stage == "completed" else state.current_stage
        title = state.report_plan.report_title or state.input_profile.title or case["title"]
        output = state.output_markdown
        created_at = state.report_snapshot.created_at or datetime.now().astimezone().isoformat()

    state_data = _as_dict(state)
    task_id = f"public-case:{case_id}"
    detail = {
        "id": task_id,
        "task_id": task_id,
        "title": title,
        "task_type": "创新性评估" if case["agent"] == "assessment" else "智能选题",
        "status": status,
        "current_stage": state.current_stage,
        "completed_steps": sum(item.get("status") == "completed" for item in state_data.get("stage_trace", [])),
        "total_steps": len(state_data.get("stage_trace", [])),
        "needs_confirmation": False,
        "confirmation_status": "not_required",
        "needs_clarification": False,
        "clarification_status": "not_required",
        "summary": state_data.get("report_plan", {}).get("executive_summary", "") if case["agent"] == "topic" else state_data.get("output_json", {}).get("summary", ""),
        "created_at": created_at,
        "updated_at": created_at,
        "completed_at": created_at,
        "duration_seconds": sum(float(item.get("duration_seconds") or 0) for item in state_data.get("stage_trace", [])),
        "user_scope": "",
        "user_name": "公共案例",
        "raw_user_input": case["input"],
        "state": state_data,
        "output_markdown": output,
        "output_json": state_data.get("output_json", {}),
        "stage_trace": state_data.get("stage_trace", []),
        "error_message": None,
    }
    if not _complete_case(case_id, detail):
        write_checkpoint(case_id, case, state, recovery_attempts)
        raise RuntimeError(f"{case_id} did not satisfy complete report and evidence checks (stage={state.current_stage}).")
    checkpoint_file = checkpoint_path(case_id)
    if checkpoint_file.exists():
        checkpoint_file.unlink()
    return {
        "case_id": case_id,
        "version": CASE_SNAPSHOT_VERSION,
        "agent": case["agent"],
        "generated_at": datetime.now().astimezone().isoformat(),
        "detail": detail,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate fixed public homepage case snapshots.")
    parser.add_argument("--case-id", choices=CASE_IDS, help="Generate or resume one case into staging only.")
    args = parser.parse_args()
    catalog = load_catalog()
    STAGING.mkdir(parents=True, exist_ok=True)
    selected = [args.case_id] if args.case_id else list(CASE_IDS)
    for case_id in selected:
        if read_staged(case_id):
            print(f"[{case_id}] staging snapshot already complete; reusing it", flush=True)
            continue
        print(f"[{case_id}] starting real {catalog[case_id]['agent']} workflow", flush=True)
        try:
            payload = await generate(case_id, catalog[case_id])
            write_staged(case_id, payload)
            print(f"[{case_id}] complete; staged snapshot saved", flush=True)
        except Exception as exc:
            print(f"[{case_id}] incomplete; saved checkpoint retained ({type(exc).__name__})", flush=True)
            if args.case_id:
                raise

    if args.case_id:
        return
    snapshots = {case_id: read_staged(case_id) for case_id in CASE_IDS}
    if not all(snapshots.values()):
        missing = [case_id for case_id, payload in snapshots.items() if payload is None]
        raise RuntimeError(f"No cases were published; incomplete snapshots: {', '.join(missing)}")

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for case_id, payload in snapshots.items():
        target = DESTINATION / f"{case_id}.json.gz"
        temporary = target.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=9) as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        temporary.replace(target)
    manifest = {
        "version": CASE_SNAPSHOT_VERSION,
        "published": True,
        "case_ids": list(CASE_IDS),
        "generated_at": datetime.now().astimezone().isoformat(),
    }
    temporary_manifest = CASE_ROOT / "manifest.json.tmp"
    temporary_manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_manifest.replace(CASE_ROOT / "manifest.json")
    print(f"Published {len(CASE_IDS)} verified public case snapshots as {CASE_SNAPSHOT_VERSION}.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
