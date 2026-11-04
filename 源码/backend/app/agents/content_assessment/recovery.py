"""Technical interruptions retain graph output and never ask users to confirm it."""
from datetime import datetime
from .schemas import ConfirmationState

RECOVERY_STAGES = {"document_intake", "claim_extraction", "retrieval", "comparison", "assessment", "landscape", "quality_gate", "report"}
LEGACY_TECHNICAL_REASONS = {"report_sources", "report_drafting", "assessment_analysis", "claim_extraction"}


def interrupt_generation(state, stage, reason, message):
    if stage not in RECOVERY_STAGES:
        raise ValueError("Invalid recovery stage")
    state.confirmation = ConfirmationState()
    state.assessment_profile.confirmation_required = False
    state.current_stage = "failed"
    state.resume_from_stage = stage
    state.output_json.pop("pause_reason", None)
    state.output_json["generation_recovery"] = {
        "status": "interrupted", "stage": stage, "reason": reason, "message": message,
    }
    now = datetime.now().astimezone()
    for trace in state.stage_trace:
        if trace.stage_key == stage and trace.status == "running":
            trace.status = "failed"
            trace.completed_at = now.isoformat()
            if trace.started_at:
                trace.duration_seconds = max(0, (now - datetime.fromisoformat(trace.started_at)).total_seconds())
    for step in state.process_trace.get(stage, []):
        if step.status == "running":
            step.status = "failed"
            step.completed_at = now.isoformat()
            if step.started_at:
                step.duration_seconds = max(0, (now - datetime.fromisoformat(step.started_at)).total_seconds())


def retry_stage(state):
    recovery = state.output_json.get("generation_recovery", {})
    stage = recovery.get("stage") if recovery.get("status") == "interrupted" else None
    if not stage and state.confirmation.reason in LEGACY_TECHNICAL_REASONS:
        stage = state.resume_from_stage
    reason = recovery.get("reason") or state.confirmation.reason
    if stage == "retrieval" and reason == "report_sources" and state.claims:
        from .scholarly_report import selected_sources
        from .point_evaluation import reviewed_evaluations
        # A retry after a source-selection correction can reuse the saved
        # search, comparisons and accepted evaluations. Only search again
        # when a point still has no usable source.
        if all(selected_sources(state, claim) for claim in state.claims):
            stage = "assessment" if any(not reviewed_evaluations(state, claim) for claim in state.claims) else "report"
    return stage if stage in RECOVERY_STAGES else None
