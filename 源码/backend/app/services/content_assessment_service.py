from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agents.content_assessment.agent import ContentAssessmentAgent
from app.agents.content_assessment.schemas import (
    ArtifactType,
    ContentAssessmentState,
    ReportMode,
    ReportSectionOverride,
    UploadedFileRef,
)


class ContentAssessmentService:
    """为路由和后台任务提供一致的八阶段创新性评估服务。"""

    def __init__(self) -> None:
        self.agent = ContentAssessmentAgent()

    def create_initial_state(
        self,
        user_input: str,
        *,
        assessment_title: str = "",
        uploaded_files: list[UploadedFileRef] | None = None,
        document_roles: dict[str, str] | None = None,
        year_range: list[int] | None = None,
        report_mode: ReportMode = "auto",
        artifact_type: ArtifactType = "auto",
        scope_focus: str = "auto",
        scope_summary: str = "",
        section_overrides: dict[str, ReportSectionOverride] | None = None,
        scenario_id: str = "",
    ) -> ContentAssessmentState:
        return self.agent.create_initial_state(
            user_input,
            assessment_title=assessment_title,
            uploaded_files=uploaded_files,
            document_roles=document_roles,
            year_range=year_range,
            report_mode=report_mode,
            artifact_type=artifact_type,
            scope_focus=scope_focus,
            scope_summary=scope_summary,
            section_overrides=section_overrides,
            scenario_id=scenario_id,
        )

    async def run(
        self,
        state: ContentAssessmentState,
        *,
        on_stage_update: Callable[[ContentAssessmentState], Awaitable[None]] | None = None,
    ) -> ContentAssessmentState:
        return await self.agent.graph.run(state, on_stage_update=on_stage_update)
