"""创新性评估智能体的公开状态与接口模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


StageStatus = Literal["pending", "running", "completed", "skipped", "blocked", "failed"]
DocumentRole = Literal["PROJECT", "BASELINE", "REFERENCE", "EVIDENCE"]
ReportMode = Literal["auto", "standard", "deep"]
ResolvedReportMode = Literal["standard", "deep"]
ArtifactType = Literal["auto", "paper", "project", "guideline", "technology_result", "mixed", "unknown"]
SectionDepth = Literal["compact", "expanded", "full"]
DocumentStatus = Literal["pending", "parsed", "needs_confirmation", "failed"]
EvidenceRelation = Literal[
    "supports", "contradicts", "partially_supports", "not_comparable", "insufficient_evidence"
]
DecisionLabel = Literal[
    "有直接支持", "存在相近先例", "方向性支持", "暂未发现可比证据", "当前不可判断",
]
AdvancednessLabel = Literal["可比条件下领先", "存在技术差异但无法证明领先", "不可比", "证据不足或存疑"]
QueryRole = Literal[
    "system_prior_art",
    "mechanism_method",
    "metric_baseline",
    "application_validation",
    "industry_market_competitive",
    "industry_standard_regulatory",
    "point_supplement",
]
ReviewTaskStatus = Literal["open", "approved", "limited", "returned", "dismissed"]
ReviewDecision = Literal["approve", "limit", "return_to_retrieval", "dismiss"]
FieldCoverage = Literal["covered", "partial", "missing", "conflict", "not_comparable"]


class StageTraceItem(BaseModel):
    stage_key: str
    stage_name: str
    status: StageStatus = "pending"
    summary: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None


class StageProgress(BaseModel):
    stage_key: str
    completed: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    display_name: str = "处理项"
    updated_at: str = ""


class ProcessTraceStep(BaseModel):
    step_key: str
    title: str
    status: StageStatus = "pending"
    summary: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class UploadedFileRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_id: str = Field(default="", validation_alias=AliasChoices("file_id", "fileId"))
    name: str = ""
    type: str = ""
    size: int = 0
    url: str = ""
    parsed_at: str = Field(default="", validation_alias=AliasChoices("parsed_at", "parsedAt"))


class UploadedFileDigest(UploadedFileRef):
    content_size: int = 0
    content_hash: str = ""
    excerpt: str = ""
    content: str = ""
    keywords: list[str] = Field(default_factory=list)
    status: str = "ready"
    warning: str = ""
    page_count: int | None = None
    anchor_schema_version: str = "anchor-v1"
    anchors: list[dict[str, Any]] = Field(default_factory=list)


class UploadedFileContext(BaseModel):
    enabled: bool = False
    total_files: int = 0
    total_content_size: int = 0
    used_context_chars: int = 0
    files: list[UploadedFileDigest] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    prompt_brief: str = ""


class ParseIssue(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "blocking"] = "warning"
    page_ref: str = ""


class DocumentRecord(BaseModel):
    document_id: str
    name: str
    role: DocumentRole
    status: DocumentStatus = "pending"
    source: str = "user_upload"
    page_count: int | None = None
    parse_issues: list[ParseIssue] = Field(default_factory=list)
    excerpt: str = ""
    content_available: bool = False
    content_hash: str = ""
    section_count: int = 0
    source_page_refs: list[str] = Field(default_factory=list)
    content_length: int = 0
    anchor_ready: bool = False
    anchor_schema_version: str = "anchor-v1"
    anchors: list[dict[str, Any]] = Field(default_factory=list)


class BoundaryMatrix(BaseModel):
    """可审计的评估范围，不把缺失信息默认为项目事实。"""

    assessment_object: str = ""
    included_scope: list[str] = Field(default_factory=list)
    excluded_scope: list[str] = Field(default_factory=list)
    time_boundary: str = ""
    geography_boundary: str = ""
    technology_boundary: str = ""
    evidence_boundary: str = ""
    assumptions: list[str] = Field(default_factory=list)
    confirmation_items: list[str] = Field(default_factory=list)


class WorkPackage(BaseModel):
    work_package_id: str
    title: str
    objective: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    verification_nodes: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class IndicatorValidation(BaseModel):
    indicator_id: str
    definition: str = ""
    unit: str = ""
    baseline: str = ""
    target: str = ""
    test_method: str = ""
    sample_design: str = ""
    statistics_method: str = ""
    acceptance_criteria: str = ""
    status: Literal["supported", "partial", "missing", "not_applicable"] = "missing"
    definition_status: Literal["complete", "partial", "missing"] = "missing"
    literature_baseline_status: Literal["comparable", "directional", "none"] = "none"
    project_achievement_status: Literal["documented", "not_provided"] = "not_provided"
    literature_baseline: str = ""
    project_result: str = ""
    assessment_conclusion: str = ""
    assessment_basis: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    source_claim_id: str = ""
    source_claim_ids: list[str] = Field(default_factory=list)


class EvidenceChunk(BaseModel):
    chunk_id: str
    evidence_id: str
    document_id: str = ""
    page_ref: str = ""
    section_ref: str = ""
    excerpt: str = ""
    purpose: str = ""
    evidence_level: Literal["A", "B", "C"] = "C"
    supports_claim_ids: list[str] = Field(default_factory=list)
    paper_id: str = ""
    paragraph_ref: str = ""
    char_start: int | None = None
    char_end: int | None = None
    access_status: Literal["authorized", "abstract_only", "unavailable", "unknown"] = "unknown"
    source_hash: str = ""
    reopenable: bool = False
    locator: dict[str, Any] = Field(default_factory=dict)
    intent_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)


class ClaimAssessmentDetail(BaseModel):
    claim_id: str
    parent_claim_id: str = ""
    aggregation_group: str = ""
    aggregation_reason: str = ""
    normalized_claim: str = ""
    comparable_fields: dict[str, str] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    field_comparisons: list[str] = Field(default_factory=list)
    conclusion: str = ""
    uncertainty: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)
    field_matrix: dict[str, FieldCoverage] = Field(default_factory=dict)
    nearest_prior_art_id: str = ""
    advancedness_conclusion: str = ""
    incomparability_reasons: list[str] = Field(default_factory=list)


class ReportSectionSpec(BaseModel):
    section_key: str
    title: str
    # Presentation metadata is optional so old persisted tasks remain readable.
    order: int = 0
    level: int = 1
    parent_key: str = ""
    display_label: str = ""
    is_appendix: bool = False
    chart_keys: list[str] = Field(default_factory=list)
    chart_metadata: dict[str, dict[str, str]] = Field(default_factory=dict)
    required: bool = True
    depth: SectionDepth = "expanded"
    target_words: int = 0
    target_tables: int = 0
    target_evidence_refs: int = 0
    actual_words: int = 0
    actual_tables: int = 0
    actual_evidence_refs: int = 0
    status: Literal["pending", "generated", "insufficient", "skipped"] = "pending"
    evidence_refs: list[str] = Field(default_factory=list)
    content: str = ""
    blocks: list["ReportBlock"] = Field(default_factory=list)


class ReportBlock(BaseModel):
    kind: Literal["paragraph", "heading", "table", "figure", "callout", "caption"] = "paragraph"
    heading_level: int = Field(default=3, ge=3, le=4)
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    claim_id: str = ""
    optional_detail: bool = False
    # Public, reader-facing callouts replace vague model narration while
    # keeping the underlying block compatible with older snapshots.
    callout_style: Literal["execution", "result", "risk"] = "result"


class ReportSnapshot(BaseModel):
    schema_version: str = "report-snapshot-v7"
    snapshot_id: str
    generated_at: str
    citation_numbers: dict[str, int] = Field(default_factory=dict)
    audit_data: dict[str, Any] = Field(default_factory=dict)
    sections: list[ReportSectionSpec] = Field(default_factory=list)
    # Final reader-facing figures selected during export.  This is optional so
    # snapshots written before the chart redesign remain loadable.
    chart_manifest: list[dict[str, Any]] = Field(default_factory=list)
    condensation_passes: int = 0
    template_version: str = "ContentAssessmentReportReferenceV2"
    render_version: str = "reference-layout-v2"
    cover_metadata: dict[str, Any] = Field(default_factory=dict)
    chart_data_snapshot: dict[str, Any] = Field(default_factory=dict)
    stage_trace: list[dict[str, Any]] = Field(default_factory=list)
    process_trace: list[dict[str, Any]] = Field(default_factory=list)
    render_context: dict[str, Any] = Field(default_factory=dict)
    source_snapshot_id: str = ""
    source_sections: list[ReportSectionSpec] = Field(default_factory=list)
    # Complete prior snapshot, including its template/chart metadata, remains
    # available for comparison when a historical export adopts a new layout.
    source_snapshot: dict[str, Any] = Field(default_factory=dict)
    layout_config: dict[str, Any] = Field(default_factory=dict)


class ReportSectionOverride(BaseModel):
    """Reader-controlled section presentation preferences.

    Core sections remain enabled by the report builder. Overrides only change
    presentation depth/order or hide optional appendices.
    """

    enabled: bool = True
    order: int | None = None
    depth: SectionDepth | None = None


class ReportPreferences(BaseModel):
    mode: ReportMode = "auto"
    resolved_mode: ResolvedReportMode = "standard"
    mode_source: Literal["auto", "user"] = "auto"
    recommended_mode: ResolvedReportMode = "standard"
    artifact_type: ArtifactType = "auto"
    resolved_artifact_type: Literal["paper", "project", "guideline", "technology_result", "mixed", "unknown"] = "unknown"
    scope_focus: Literal["focused", "comprehensive"] = "comprehensive"
    scope_summary: str = ""
    section_overrides: dict[str, ReportSectionOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_mode(self) -> "ReportPreferences":
        if self.mode in {"standard", "deep"}:
            self.resolved_mode = self.mode
            self.mode_source = "user"
        if self.artifact_type != "auto":
            self.resolved_artifact_type = self.artifact_type
        return self


class ReportMetrics(BaseModel):
    evidence_count: int = 0
    unique_evidence_count: int = 0
    claim_count: int = 0
    indicator_count: int = 0
    indicator_assessed_count: int = 0
    indicator_evidence_count: int = 0
    query_group_count: int = 0
    claim_coverage: float = Field(default=0.0, ge=0, le=1)
    formal_claim_coverage: float = Field(default=0.0, ge=0, le=1)
    directional_claim_coverage: float = Field(default=0.0, ge=0, le=1)
    indicator_coverage: float = Field(default=0.0, ge=0, le=1)
    citation_integrity: float = Field(default=0.0, ge=0, le=1)
    section_quota_completion: float = Field(default=0.0, ge=0, le=1)
    estimated_pages: float = 0.0
    body_words: int = 0
    appendix_words: int = 0
    body_pages: float = 0.0
    appendix_pages: float = 0.0
    body_ratio: float = Field(default=0.0, ge=0, le=1)
    appendix_ratio: float = Field(default=0.0, ge=0, le=1)
    section_content_completion: float = 0.0
    section_citation_completion: float = 0.0
    appendix_compression_status: str = "pending"
    actual_page_count: int | None = None
    page_count_verified: bool = False
    page_count_source: str = "estimate"
    page_target_status: Literal["pending", "within_target", "below_target", "above_target", "unverified", "not_applicable"] = "pending"
    page_target_reason: str = ""
    long_report_status: Literal["pending", "eligible", "insufficient_evidence", "incomplete", "failed"] = "pending"
    deficiencies: list[str] = Field(default_factory=list)
    evidence_strength: Literal["high", "medium", "low"] = "low"
    evidence_strength_reason: str = ""


class AssessmentProfile(BaseModel):
    title: str = ""
    assessment_object: str = ""
    research_domain: str = "通用科研"
    scope_boundary: str = ""
    assessment_goal: str = "科研项目创新性评估"
    input_version: str = "input-v1"
    document_records: list[DocumentRecord] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confirmation_required: bool = False
    artifact_type: Literal["paper", "project", "guideline", "technology_result", "mixed", "unknown"] = "unknown"
    scope_focus: Literal["focused", "comprehensive"] = "comprehensive"
    scope_summary: str = ""
    artifact_type_confidence: float = Field(default=0.0, ge=0, le=1)
    artifact_type_reason: str = ""
    scope_focus_confidence: float = Field(default=0.0, ge=0, le=1)
    scope_focus_reason: str = ""
    scope_summary_generated: bool = False
    document_role_predictions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pending_confirmation_fields: list[str] = Field(default_factory=list)


class ResearchNineFields(BaseModel):
    research_direction: str = ""
    research_goal: str = ""
    research_content: str = ""
    technical_route: str = ""
    key_technologies: list[str] = Field(default_factory=list)
    evaluation_indicators: list[str] = Field(default_factory=list)
    timeline: str = ""
    application_scenarios: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)

    def missing_required(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if name != "expected_outcomes"
                and not (any(item.strip() for item in value) if isinstance(value, list) else value.strip())]

    def missing_for_type(self, artifact_type: ArtifactType) -> list[str]:
        if artifact_type in {"auto", "unknown"}:
            # Resolve the type instead of falling back to the old eight-field form.
            return ["artifact_type"]
        from .input_rules import REQUIRED_FIELDS
        required = REQUIRED_FIELDS.get(artifact_type, ("research_direction", "research_content"))
        def populated(key: str) -> bool:
            from .input_rules import meaningful
            value = getattr(self, key)
            return any(meaningful(item) for item in value) if isinstance(value, list) else meaningful(value)

        missing = [key for key in required if not populated(key)]
        if artifact_type in {"paper", "project"} and not (populated("technical_route") or populated("key_technologies")):
            missing.append("key_technologies")
        return missing


class Claim(BaseModel):
    claim_id: str
    text: str
    display_title: str = ""
    claim_type: Literal["innovation", "technical", "indicator", "application", "compliance"] = "technical"
    assertion_type: Literal["completed", "target", "planned", "unknown"] = "unknown"
    source_document_id: str = ""
    # ``material`` is the legacy/default source. ``evidence`` identifies a
    # retrieved paper whose abstract or full text was used for confirmation.
    source_kind: Literal["material", "evidence"] = "material"
    source_evidence_id: str = ""
    source_span: str = ""
    parent_claim_id: str = ""
    aggregation_group: str = ""
    aggregation_reason: str = ""
    is_group_representative: bool = False
    ambiguity: str = ""
    searchable_terms: list[str] = Field(default_factory=list)
    claim_fields: dict[str, str] = Field(default_factory=dict)
    source_page_ref: str = ""
    source_paragraph_ref: str = ""
    source_char_start: int | None = None
    source_char_end: int | None = None
    source_locator: dict[str, Any] = Field(default_factory=dict)


class Indicator(BaseModel):
    indicator_id: str
    name: str
    value: str = ""
    unit: str = ""
    status: Literal["completed", "target", "planned", "unknown"] = "unknown"
    source_claim_id: str = ""
    source_claim_ids: list[str] = Field(default_factory=list)
    source_span: str = ""
    baseline: str = ""
    test_method: str = ""
    statistics_method: str = ""
    definition: str = ""
    acceptance_condition: str = ""
    source_document_id: str = ""
    source_char_start: int | None = None
    source_char_end: int | None = None
    source_locator: dict[str, Any] = Field(default_factory=dict)


class GuidelineRequirement(BaseModel):
    requirement_id: str
    text: str
    source_document_id: str = ""
    source_span: str = ""
    match_status: Literal["matched", "partial", "unmatched", "unknown"] = "unknown"
    source_locator: dict[str, Any] = Field(default_factory=dict)
    response_excerpt: str = ""
    comparison_note: str = ""


class QueryPlan(BaseModel):
    claim_id: str
    query: str
    query_group: str = ""
    query_role: QueryRole = "system_prior_art"
    claim_ids: list[str] = Field(default_factory=list)
    indicator_ids: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"])
    year_range: list[int] = Field(default_factory=list)
    rationale: str = ""
    filters: dict[str, Any] = Field(default_factory=dict)
    candidate_count: int = 0
    selected_count: int = 0
    source_status: Literal["pending", "completed", "unavailable"] = "pending"
    indicator_id: str = ""
    query_version: str = "query-v1"
    exclusion_terms: list[str] = Field(default_factory=list)
    source_scope: list[str] = Field(default_factory=lambda: ["NSTL"])
    pagination: dict[str, Any] = Field(default_factory=lambda: {"page": 1, "page_size": 20})
    stop_condition: str = "完成候选召回并保留可定位证据"

    @model_validator(mode="after")
    def normalize_scope_ids(self) -> "QueryPlan":
        if self.claim_id and self.claim_id not in self.claim_ids:
            self.claim_ids.insert(0, self.claim_id)
        if self.indicator_id and self.indicator_id not in self.indicator_ids:
            self.indicator_ids.insert(0, self.indicator_id)
        if not self.claim_id and self.claim_ids:
            self.claim_id = self.claim_ids[0]
        if not self.indicator_id and self.indicator_ids:
            self.indicator_id = self.indicator_ids[0]
        return self


class EvidenceRecord(BaseModel):
    evidence_id: str
    title: str
    source_type: Literal["project_material", "guideline", "literature", "standard", "market", "regulatory"]
    source_document_id: str = ""
    # Keep NSTL detail, publisher/full-text, and DOI destinations distinct.
    # Existing records only have ``url`` and remain valid.
    nstl_url: str = ""
    source_url: str = ""
    year: int | None = None
    url: str = ""
    doi: str = ""
    authors: list[str] = Field(default_factory=list)
    venue: str = ""
    keywords: list[str] = Field(default_factory=list)
    abstract: str = ""
    retrieval_score: float | None = None
    query_group: str = ""
    excerpt: str = ""
    page_ref: str = ""
    evidence_level: Literal["A", "B", "C"] = "C"
    retrieval_query: str = ""
    usable_for_novelty: bool = False
    usable_for_directional: bool = False
    paper_id: str = ""
    section_ref: str = ""
    paragraph_ref: str = ""
    char_start: int | None = None
    char_end: int | None = None
    fulltext_available: bool = False
    anchor_verified: bool = False
    access_status: Literal["authorized", "abstract_only", "unavailable", "unknown"] = "unknown"
    source_hash: str = ""
    retrieval_batch_id: str = ""
    comparison_fields: dict[str, str] = Field(default_factory=dict)
    claim_ids: list[str] = Field(default_factory=list)
    indicator_ids: list[str] = Field(default_factory=list)
    usage: list[Literal["used_for_claim", "used_for_risk", "used_for_market", "used_for_regulation", "unused"]] = Field(default_factory=list)
    relevance_score: float = Field(default=0.0, ge=0, le=1)
    locator: dict[str, Any] = Field(default_factory=dict)
    retrieval_source: str = ""
    match_status: str = ""
    retrieval_intent_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_evidence_tier(self) -> "EvidenceRecord":
        """Derive literature evidence strength from verifiable source data."""
        if self.source_type != "literature":
            return self
        formal = bool(
            self.fulltext_available
            and self.anchor_verified
            and self.access_status == "authorized"
            and ((self.char_start is not None and self.char_end is not None and self.char_end > self.char_start)
                 or (self.locator.get("chunk_id") and self.source_hash))
            and self.section_ref.lower() != "abstract"
            and self.excerpt.strip()
        )
        if formal:
            if self.evidence_level == "C":
                self.evidence_level = "A"
            self.usable_for_novelty = self.evidence_level == "A"
            self.usable_for_directional = True
        else:
            self.usable_for_novelty = False
            self.usable_for_directional = bool(self.abstract.strip() or self.excerpt.strip()) and self.evidence_level in {"B", "C"}
            if self.evidence_level == "A":
                self.evidence_level = "B" if (self.abstract.strip() or self.excerpt.strip()) else "C"
            elif self.evidence_level == "C" and (self.abstract.strip() or self.excerpt.strip()):
                self.evidence_level = "B"
        return self


class ClaimEvidenceLink(BaseModel):
    claim_id: str
    evidence_id: str
    relation: EvidenceRelation
    comparable_fields: list[str] = Field(default_factory=list)
    similarity: str = ""
    difference: str = ""
    uncertainty: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)
    field_matrix: dict[str, FieldCoverage] = Field(default_factory=dict)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    incomparability_reasons: list[str] = Field(default_factory=list)
    evidence_level: Literal["A", "B", "C"] = "C"


class NoveltyDecision(BaseModel):
    claim_id: str
    label: DecisionLabel
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    rewrite_advice: str = ""

    @field_validator("label", mode="before")
    @classmethod
    def map_legacy_label(cls, value: object) -> object:
        return {
            "基本支持": "有直接支持",
            "直接支持": "有直接支持",
            "基本成立": "方向性支持",
            "增量成立": "存在相近先例",
            "目标高于公开水平": "当前不可判断",
            "概念已有先例": "存在相近先例",
            "证据不足或存疑": "当前不可判断",
            "证据支持有限": "方向性支持",
            "目标待验证": "当前不可判断",
            "证据不足": "当前不可判断",
        }.get(value, value)


class AdvancednessDecision(BaseModel):
    claim_id: str
    label: AdvancednessLabel = "证据不足或存疑"
    rationale: str = ""
    comparison_evidence_ids: list[str] = Field(default_factory=list)
    comparable_indicator_ids: list[str] = Field(default_factory=list)
    uncertainty: str = ""


class ReviewTask(BaseModel):
    review_task_id: str
    review_type: Literal["strong_assertion", "evidence_conflict", "incomparable_metric", "source_gap", "quality_gate"]
    claim_id: str = ""
    title: str
    reason: str
    status: ReviewTaskStatus = "open"
    evidence_ids: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    created_version: str = ""
    resolved_by: str = ""
    resolved_at: str = ""
    resolution_reason: str = ""


class ResolutionLog(BaseModel):
    resolution_id: str
    review_task_id: str
    decision: ReviewDecision
    operator: str = ""
    occurred_at: str = ""
    reason: str = ""
    evidence_version: str = ""
    affected_stages: list[str] = Field(default_factory=list)


class VersionChain(BaseModel):
    input_version: str = "input-v1"
    evidence_pool_version: str = "evidence-v0"
    rule_version: str = "rule-v3-evidence-tier-and-seven-fields"
    model_version: str = "model-v1"
    chart_data_snapshot_version: str = "chart-v0"
    template_version: str = "ContentAssessmentReportReferenceV2"
    report_version: str = "report-v0"
    parent_report_version: str = ""
    invalidated_stages: list[str] = Field(default_factory=list)
    stage_versions: dict[str, str] = Field(default_factory=dict)


class InvalidationRecord(BaseModel):
    invalidation_id: str
    reason: str
    source_version: str
    affected_stages: list[str] = Field(default_factory=list)
    created_at: str = ""


class CheckpointRecord(BaseModel):
    checkpoint_id: str
    revision: int = 0
    stage: str = ""
    status: Literal["created", "running", "completed", "awaiting_confirmation", "failed"] = "created"
    state_hash: str = ""
    saved_at: str = ""


class LandscapeDataCard(BaseModel):
    card_id: str
    card_type: Literal["trend", "market", "regulatory", "competitive"]
    subject: str
    value: str = ""
    unit: str = ""
    period: str = ""
    source_evidence_ids: list[str] = Field(default_factory=list)
    source_url: str = ""
    source_hash: str = ""
    status: Literal["available", "qualitative", "insufficient_evidence"] = "insufficient_evidence"
    confidence: float = Field(default=0.0, ge=0, le=1)
    limitation: str = ""
    observation_level: Literal["quantitative", "qualitative", "unavailable"] = "unavailable"
    # Explicit, comparable axes; a free-form value string is never parsed as
    # competition coordinates.
    x_value: float | None = None
    y_value: float | None = None
    x_label: str = ""
    y_label: str = ""
    x_unit: str = ""
    y_unit: str = ""


class ScoreCardItem(BaseModel):
    dimension: Literal[
        "创新性", "先进性", "可行性", "风险", "证据充分性", "应用与合规"
    ]
    assessment: str
    score_range: str = ""
    basis: list[str] = Field(default_factory=list)
    uncertainty: str = ""

    @field_validator("dimension", mode="before")
    @classmethod
    def map_legacy_dimension(cls, value: object) -> object:
        return {
            "指南符合性": "应用与合规",
            "前沿性": "创新性",
            "技术前沿性": "创新性",
            "创新性与先进性": "创新性",
            "申报文本质量": "应用与合规",
        }.get(value, value)


class QualityGate(BaseModel):
    passed: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    review_required: bool = False
    report_eligibility: Literal["formal", "exploratory", "blocked"] = "exploratory"
    open_review_count: int = 0
    checks: list[dict[str, Any]] = Field(default_factory=list)
    model_status: Literal["not_needed", "completed", "fallback"] = "not_needed"
    model_findings: list[dict[str, Any]] = Field(default_factory=list)
    post_report_checks: list[dict[str, Any]] = Field(default_factory=list)


class AssessmentReportPlan(BaseModel):
    report_version: str = "report-v1"
    report_title: str = ""
    report_type: Literal["正式评估报告", "探索性评估报告"] = "探索性评估报告"
    executive_summary: str = ""
    evidence_cutoff_date: str = ""
    sections: list[dict[str, Any]] = Field(default_factory=list)
    section_specs: list[ReportSectionSpec] = Field(default_factory=list)
    citation_map: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    report_mode: ResolvedReportMode = "standard"
    mode_source: Literal["auto", "user"] = "auto"
    recommended_mode: ResolvedReportMode = "standard"
    artifact_type: Literal["paper", "project", "guideline", "technology_result", "mixed", "unknown"] = "unknown"
    scope_summary: str = ""
    target_page_min: int = 20
    target_page_max: int = 26
    quality_status: Literal["pending", "eligible", "insufficient_evidence", "incomplete", "failed"] = "pending"
    actual_page_count: int | None = None
    content_unit_counts: dict[str, int] = Field(default_factory=dict)
    render_version: str = "report-render-v2"
    evidence_strength: Literal["high", "medium", "low"] = "low"
    evidence_strength_reason: str = ""
    auto_detection: dict[str, Any] = Field(default_factory=dict)


class ConfirmationState(BaseModel):
    reason: str = "scope"
    needed: bool = False
    prompt: str = ""
    options: list[dict[str, str]] = Field(default_factory=list)
    selected_option: str = ""
    user_note: str = ""


class ContentAssessmentState(BaseModel):
    parent_task_id: str = ""
    scenario_id: str = ""
    report_snapshot: ReportSnapshot | None = None
    raw_user_input: str
    user_scope: str = ""
    current_stage: str = "created"
    stage_trace: list[StageTraceItem] = Field(default_factory=list)
    stage_progress: dict[str, StageProgress] = Field(default_factory=dict)
    process_trace: dict[str, list[ProcessTraceStep]] = Field(default_factory=dict)
    assessment_profile: AssessmentProfile = Field(default_factory=AssessmentProfile)
    report_preferences: ReportPreferences = Field(default_factory=ReportPreferences)
    boundary_matrix: BoundaryMatrix = Field(default_factory=BoundaryMatrix)
    nine_fields: ResearchNineFields = Field(default_factory=ResearchNineFields)
    nine_fields_confirmed: bool = False
    work_packages: list[WorkPackage] = Field(default_factory=list)
    confirmation: ConfirmationState = Field(default_factory=ConfirmationState)
    claims: list[Claim] = Field(default_factory=list)
    indicators: list[Indicator] = Field(default_factory=list)
    indicator_validations: list[IndicatorValidation] = Field(default_factory=list)
    guideline_requirements: list[GuidelineRequirement] = Field(default_factory=list)
    query_plans: list[QueryPlan] = Field(default_factory=list)
    evidence_records: list[EvidenceRecord] = Field(default_factory=list)
    evidence_chunks: list[EvidenceChunk] = Field(default_factory=list)
    claim_evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    claim_assessments: list[ClaimAssessmentDetail] = Field(default_factory=list)
    novelty_decisions: list[NoveltyDecision] = Field(default_factory=list)
    advancedness_decisions: list[AdvancednessDecision] = Field(default_factory=list)
    review_tasks: list[ReviewTask] = Field(default_factory=list)
    resolution_logs: list[ResolutionLog] = Field(default_factory=list)
    scorecard: list[ScoreCardItem] = Field(default_factory=list)
    trend_analysis: dict[str, Any] = Field(default_factory=dict)
    market_data_cards: list[LandscapeDataCard] = Field(default_factory=list)
    regulatory_data_cards: list[LandscapeDataCard] = Field(default_factory=list)
    competitive_data_cards: list[LandscapeDataCard] = Field(default_factory=list)
    quality_gate: QualityGate = Field(default_factory=QualityGate)
    report_plan: AssessmentReportPlan = Field(default_factory=AssessmentReportPlan)
    report_metrics: ReportMetrics = Field(default_factory=ReportMetrics)
    output_markdown: str = ""
    output_json: dict[str, Any] = Field(default_factory=dict)
    uploaded_files: list[UploadedFileRef] = Field(default_factory=list)
    uploaded_file_context: UploadedFileContext = Field(default_factory=UploadedFileContext)
    worker_id: str = ""
    retry_count: int = 0
    versions: VersionChain = Field(default_factory=VersionChain)
    invalidation_records: list[InvalidationRecord] = Field(default_factory=list)
    checkpoint_id: str = ""
    checkpoint_revision: int = 0
    checkpoint_history: list[CheckpointRecord] = Field(default_factory=list)
    resume_from_stage: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "content-assessment-backend"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class MongoHealthResponse(BaseModel):
    status: str = "ok"
    database: str = ""
    collection: str = ""
    uri: str = ""


class ContentAssessmentRunRequest(BaseModel):
    user_input: str = Field(min_length=1, max_length=12000)
    assessment_title: str = Field(default="", max_length=300)
    language_preference: str = "zh"
    year_range: list[int] = Field(default_factory=list)
    uploaded_files: list[UploadedFileRef] = Field(default_factory=list)
    nine_fields: ResearchNineFields | None = None
    scenario_id: Literal[
        "assessment-platform", "assessment-battery", "assessment-drug",
        "assessment-standard", "assessment-cost", "assessment-data",
        "assessment-battery-v2", "assessment-chip-v2", "assessment-crop-v2",
        "assessment-microplastics-v2", "assessment-co2-v2", "assessment-quantum-v2",
    ] | None = None

    document_roles: dict[str, DocumentRole] = Field(default_factory=dict)
    report_mode: ReportMode = "auto"
    artifact_type: ArtifactType = "auto"
    scope_focus: Literal["auto", "focused", "comprehensive"] = "auto"
    scope_summary: str = Field(default="", max_length=4000)
    section_overrides: dict[str, ReportSectionOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_nine_fields(self) -> "ContentAssessmentRunRequest":
        if self.nine_fields is not None and self.artifact_type != "auto":
            missing = self.nine_fields.missing_for_type(self.artifact_type)
            if missing:
                labels = {"research_direction": "研究方向", "research_goal": "研究目标", "research_content": "研究内容",
                          "technical_route": "技术路线", "key_technologies": "关键技术", "evaluation_indicators": "评价指标",
                          "timeline": "时间安排", "application_scenarios": "应用场景", "technical_route 或 research_goal": "技术路线或研究目标"}
                raise ValueError("请补充当前材料缺少的要点：" + "、".join(labels.get(key, key) for key in missing))
        return self

    @field_validator("year_range")
    @classmethod
    def validate_year_range(cls, value: list[int]) -> list[int]:
        if value and (len(value) != 2 or value[0] > value[1]):
            raise ValueError("year_range 必须是升序的起止年份")
        return value


class ContentAssessmentRunResponse(BaseModel):
    success: bool = True
    run_id: str
    status: str = "running"
    state: ContentAssessmentState


class ContentAssessmentConfirmationRequest(BaseModel):
    selected_option: str = Field(min_length=1, max_length=100)
    user_note: str = Field(default="", max_length=1000)
    report_mode: ReportMode | None = None
    artifact_type: ArtifactType | None = None
    scope_focus: Literal["focused", "comprehensive"] | None = None
    scope_summary: str = Field(default="", max_length=4000)
    section_overrides: dict[str, ReportSectionOverride] | None = None
    confirmed_claims: list[Claim] | None = None
    nine_fields: ResearchNineFields | None = None
    supplemental_input: str = Field(default="", max_length=12000)
    removed_file_ids: list[str] = Field(default_factory=list, max_length=10)


class ReviewResolutionRequest(BaseModel):
    decision: ReviewDecision
    reason: str = Field(default="", max_length=2000)
    operator: str = Field(default="", max_length=120)


class ReviewResolutionResponse(BaseModel):
    success: bool = True
    run_id: str
    status: str
    state: ContentAssessmentState


class ContentAssessmentConfirmationResponse(ContentAssessmentRunResponse):
    pass


class ContentAssessmentTaskHistoryItem(BaseModel):
    id: str = ""
    task_id: str = ""
    title: str = ""
    task_type: str = "创新性评估"
    status: str = ""
    current_stage: str = ""
    completed_steps: int = 0
    total_steps: int = 8
    needs_confirmation: bool = False
    confirmation_status: str = "not_required"
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    duration_seconds: float | None = None


class ContentAssessmentTaskHistoryListResponse(BaseModel):
    items: list[ContentAssessmentTaskHistoryItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class ContentAssessmentTaskHistoryDetailResponse(ContentAssessmentTaskHistoryItem):
    user_scope: str = ""
    user_name: str = ""
    raw_user_input: str = ""
    state: ContentAssessmentState | None = None
    output_markdown: str = ""
    output_json: dict[str, Any] = Field(default_factory=dict)
    stage_trace: list[StageTraceItem] = Field(default_factory=list)
    error_message: str | None = None


class ContentAssessmentTaskHistoryDeleteResponse(BaseModel):
    success: bool = True
    task_id: str = ""


class QueueHealthResponse(BaseModel):
    status: str = "healthy"
    active_tasks: int = 0
