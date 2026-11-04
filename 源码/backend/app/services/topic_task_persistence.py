"""智能选题任务状态持久化。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.topic_selection.schemas import TopicSelectionState
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


def _progress_counts(state: TopicSelectionState) -> tuple[int, int]:
    return sum(item.status == "completed" for item in state.stage_trace), len(state.stage_trace)


async def persist_topic_task_state(
    *, task_id: str, user_scope: str, state: TopicSelectionState, status: str,
    error_message: str | None = None, completed_at: str | None = None,
    expected_updated_at: str | None = None,
) -> str:
    repo = get_task_history_repo()
    completed, total = _progress_counts(state)
    updates: dict[str, object] = {
        "title": state.input_profile.title,
        "status": status,
        "current_stage": state.current_stage,
        "completed_steps": completed,
        "total_steps": total,
        "needs_confirmation": state.confirmation.needed,
        "confirmation_status": "required" if state.confirmation.needed else "not_required",
        "summary": state.report_plan.executive_summary or state.input_profile.title,
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
        updates['duration_seconds'] = sum(item.duration_seconds or 0 for item in state.stage_trace)
    elif status in ('queued', 'running'):
        updates['completed_at'] = None
        updates['duration_seconds'] = sum(item.duration_seconds or 0 for item in state.stage_trace)
    if expected_updated_at is not None:
        if not await repo.update_report(task_id, user_scope, expected_updated_at, updates):
            raise TaskHistoryConflict("任务状态已变化，请刷新后重试")
    else:
        if not await repo.update(task_id, user_scope, updates):
            raise TaskHistoryConflict('任务已停止或删除')
    return str(updates['updated_at'])


class TaskHistoryConflict(RuntimeError):
    """A guarded user action lost a concurrent update."""


async def mark_topic_task_failed(task_id: str, user_scope: str, state: TopicSelectionState | None, error_message: str) -> None:
    repo = get_task_history_repo()
    updates: dict[str, object] = {"status": "failed", "error_message": error_message, "updated_at": now_beijing_iso(), "completed_at": now_beijing_iso()}
    if state is not None:
        state.current_stage = "failed"
        completed, total = _progress_counts(state)
        updates.update({"current_stage": "failed", "completed_steps": completed, "total_steps": total, "state": state.model_dump(), "stage_trace": [item.model_dump() for item in state.stage_trace]})
    try:
        await repo.update(task_id, user_scope, updates)
    except TaskHistoryError:
        return
