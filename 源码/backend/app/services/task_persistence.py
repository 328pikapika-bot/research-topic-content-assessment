"""任务状态持久化辅助函数。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.content_assessment.schemas import ContentAssessmentState
from app.core.config import BEIJING_TZ
from app.repositories.content_assessment_task_history import TaskHistoryError, get_task_history_repo


def now_beijing_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _progress_counts(state: ContentAssessmentState) -> tuple[int, int]:
    return (
        sum(item.status == "completed" for item in state.stage_trace),
        len(state.stage_trace),
    )


def _confirmation_status(state: ContentAssessmentState) -> str:
    if state.confirmation.needed or state.current_stage == "awaiting_confirmation":
        return "resolved" if state.confirmation.selected_option and not state.confirmation.needed else "required"
    return "not_required"


async def persist_task_state(
    *,
    task_id: str,
    user_scope: str,
    state: ContentAssessmentState,
    status: str,
    error_message: str | None = None,
    completed_at: str | None = None,
) -> None:
    repo = get_task_history_repo()
    completed_steps, total_steps = _progress_counts(state)
    updates: dict[str, object] = {
        "title": state.assessment_profile.title,
        "status": status,
        "current_stage": state.current_stage,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "needs_confirmation": state.confirmation.needed,
        "confirmation_status": _confirmation_status(state),
        "summary": state.report_plan.executive_summary or state.assessment_profile.title,
        "state": state.model_dump(),
        "stage_trace": [item.model_dump() for item in state.stage_trace],
        "output_markdown": state.output_markdown,
        "output_json": state.output_json,
        "updated_at": now_beijing_iso(),
    }
    if error_message:
        updates["error_message"] = error_message
    elif status != "failed":
        updates["error_message"] = None
    if completed_at:
        updates["completed_at"] = completed_at
        existing = await repo.get(task_id, user_scope)
        started = _parse_iso((existing or {}).get("created_at"))
        ended = _parse_iso(completed_at)
        if started and ended:
            updates["duration_seconds"] = max(0.0, round((ended - started).total_seconds(), 3))
    await repo.update(task_id, user_scope, updates)


async def mark_task_failed(
    task_id: str, user_scope: str, state: ContentAssessmentState | None, error_message: str
) -> None:
    repo = get_task_history_repo()
    updates: dict[str, object] = {
        "status": "failed",
        "error_message": error_message,
        "updated_at": now_beijing_iso(),
        "completed_at": now_beijing_iso(),
    }
    if state is not None:
        from app.agents.content_assessment.recovery import interrupt_generation, RECOVERY_STAGES
        stage = state.resume_from_stage or state.current_stage
        if stage in RECOVERY_STAGES:
            if not state.output_json.get("generation_recovery"):
                interrupt_generation(state, stage, "stage_error", "生成中断，已有结果已保存，请重试。")
            else:
                state.confirmation.needed = False
                state.assessment_profile.confirmation_required = False
        state.current_stage = "failed"
        completed_steps, total_steps = _progress_counts(state)
        updates.update(
            {
                "current_stage": state.current_stage,
                "completed_steps": completed_steps,
                "total_steps": total_steps,
                "needs_confirmation": state.confirmation.needed,
                "confirmation_status": _confirmation_status(state),
                "state": state.model_dump(),
                "stage_trace": [item.model_dump() for item in state.stage_trace],
                "output_json": state.output_json,
                "output_markdown": state.output_markdown,
            }
        )
    try:
        await repo.update(task_id, user_scope, updates)
    except TaskHistoryError:
        return
