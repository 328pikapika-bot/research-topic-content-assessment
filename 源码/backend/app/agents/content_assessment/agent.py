from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import (
    ArtifactType,
    ContentAssessmentState,
    ReportMode,
    ReportSectionOverride,
    UploadedFileRef,
)


class ContentAssessmentAgent:
    """创新性评估流程的轻量入口。"""

    def __init__(self) -> None:
        self.graph = ContentAssessmentGraph()

    def create_initial_state(
        self,
        raw_user_input: str,
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
        return self.graph.create_initial_state(
            raw_user_input,
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
