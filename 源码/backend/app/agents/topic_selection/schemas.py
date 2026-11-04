"""智能选题智能体的版本化业务契约。

模型只写入候选字段，正式结论由确定性校验和证据回链后发布。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


StageStatus = Literal["pending", "running", "completed", "blocked", "failed", "skipped"]
SourceKind = Literal["nstl", "web", "cloud_model"]
SourceStatus = Literal["pending", "running", "completed", "failed", "disabled", "locked"]
TopicTaskStatus = Literal[
    "queued", "running", "awaiting_confirmation", "awaiting_review", "awaiting_correction", "superseded", "model_unavailable", "completed", "failed", "cancelled"
]


class TopicStageTrace(BaseModel):
    stage_key: str
    stage_name: str
    status: StageStatus = "pending"
    summary: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    prior_duration_seconds: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    progress_percent: int = Field(default=0, ge=0, le=100)
    active_step: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    model_status: Literal["not_requested", "running", "completed", "degraded", "disabled"] = "not_requested"
    prompt_version: str = ""
    model_call_count: int = 0
    model_duration_ms: int | None = None
    deterministic_duration_ms: int | None = None
    external_io_duration_ms: int | None = None
    model_error_code: str = ""
    execution_mode: Literal["model", "deterministic", "deterministic_fallback", "disabled", "mixed"] = "deterministic"
    fallback_reason: str = ""
    degradation_reason: str = ""


class TopicModelCall(BaseModel):
    call_id: str
    stage_key: str
    prompt_version: str
    status: Literal["completed", "degraded", "disabled", "failed"] = "completed"
    profile: str = ""
    model: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int | None = None
    attempt_count: int = 0
    input_hash: str = ""
    output_hash: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    output_keys: list[str] = Field(default_factory=list)
    error_type: str = ""
    error_message: str = ""
    http_status: int | None = None
    error_code: str = ""
    retry_skipped: bool = False
    retry_skip_reason: str = ""


class TopicAnalysisPolicy(BaseModel):
    mode: Literal["deep", "fast"] = "deep"
    model_enabled: bool = True
    max_stage_timeout_seconds: int = Field(default=90, ge=15, le=180)
    max_retries: int = Field(default=1, ge=0, le=2)
    material_mode: Literal["redacted_excerpt", "none"] = "redacted_excerpt"
    external_evidence_locked: bool = True
    model_preflight_status: Literal["unknown", "available", "unavailable", "not_configured"] = "unknown"
    model_preflight_reason: str = ""
    model_profile: str = ""
    model_name: str = ""
    model_endpoint: str = ""


class TopicProgressEvent(BaseModel):
    event_id: str
    sequence: int = Field(ge=1)
    stage_key: str
    step_key: str
    kind: Literal["status", "metric", "artifact", "warning"] = "status"
    status: Literal["running", "completed", "failed", "blocked"] = "running"
    title: str
    message: str = ""
    progress_percent: int = Field(default=0, ge=0, le=100)
    current: int | float | None = None
    total: int | float | None = None
    unit: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    occurred_at: str


class TopicConfirmation(BaseModel):
    needed: bool = False
    prompt: str = ""
    options: list[dict[str, str]] = Field(default_factory=list)
    selected_option: str = ""
    user_note: str = ""


class TopicVersions(BaseModel):
    input_version: str = "input-v1"
    theme_tree_version: str = "theme-v0"
    query_plan_version: str = "query-v0"
    evidence_pool_version: str = "evidence-v0"
    landscape_snapshot_version: str = "landscape-v0"
    gap_rule_version: str = "gap-v1"
    scoring_version: str = "score-v1"
    chart_data_snapshot_version: str = "chart-v0"
    template_version: str = "TopicReportV4"
    report_version: str = "report-v0"
    model_version: str = ""
    parent_report_version: str = ""
    invalidated_stages: list[str] = Field(default_factory=list)
    stage_versions: dict[str, str] = Field(default_factory=dict)


class DocumentRecord(BaseModel):
    document_id: str
    name: str
    role: str = "REFERENCE"
    mime_type: str = ""
    content_hash: str = ""
    status: Literal["parsed", "failed", "unavailable", "pending"] = "pending"
    excerpt: str = ""
    parse_warnings: list[str] = Field(default_factory=list)


class InputProfile(BaseModel):
    title: str = ""
    research_question: str = ""
    research_object: str = ""
    method_paradigm: str = ""
    application_scenario: str = ""

    desired_output: str = "形成可验证的研究选题与实施建议"
    time_range: list[int] = Field(default_factory=list)
    time_range_source: Literal["unspecified", "user"] = "unspecified"
    languages: list[str] = Field(default_factory=list)
    data_constraints: list[str] = Field(default_factory=list)
    resource_constraints: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    documents: list[DocumentRecord] = Field(default_factory=list)
    sensitivity_level: Literal["public", "internal", "confidential", "secret"] = "internal"
    freshness_requirement_days: int = Field(default=365, ge=0, le=3650)
    desired_candidate_count: int = Field(default=3, ge=3, le=5)
    output_preference: Literal["balanced", "recommended", "exploratory"] | None = None
    evidence_cutoff_at: str = ""


class ConstraintCard(BaseModel):
    confirmed: bool = False
    boundary_text: str = ""
    must_include: list[str] = Field(default_factory=list)
    must_exclude: list[str] = Field(default_factory=list)
    resource_limits: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class TermMapEntry(BaseModel):
    term_id: str
    preferred: str
    alternatives: list[str] = Field(default_factory=list)
    english_terms: list[str] = Field(default_factory=list)
    broader_terms: list[str] = Field(default_factory=list)
    ambiguity_note: str = ""
    status: Literal["confirmed", "candidate", "rejected"] = "candidate"


class ThemeNode(BaseModel):
    theme_id: str
    label: str
    display_label: str = ""
    boundary: str = ""
    exclusions: list[str] = Field(default_factory=list)
    seed_terms: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    parent_id: str = "root"
    discipline_tags: list[str] = Field(default_factory=list)
    discipline_sources: list[dict[str, Any]] = Field(default_factory=list)
    uncertainty: str = ""
    research_object: str = ""
    research_question: str = ""
    method_boundary: str = ""
    application_scenario: str = ""


class ThemeTree(BaseModel):
    root: str = ""
    display_root: str = ""
    nodes: list[ThemeNode] = Field(default_factory=list)
    edges: list[dict[str, str]] = Field(default_factory=list)
    coverage_score: float = 0.0
    overlap_warnings: list[str] = Field(default_factory=list)
    status: Literal["candidate", "confirmed", "exploratory"] = "candidate"


class QueryBranch(BaseModel):
    branch_id: str
    theme_id: str
    purpose: str
    query: str
    languages: list[str] = Field(default_factory=list)
    year_range: list[int] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    source_scope: list[str] = Field(default_factory=lambda: ["NSTL"])
    paper_num: int = 20
    stop_condition: str = "完成主题召回并保留证据"
    status: Literal["pending", "completed", "failed", "unavailable"] = "pending"
    request_id: str = ""
    returned_count: int = 0
    source_status: dict[str, SourceStatus] = Field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    failure_reason: str = ""


class QueryPlan(BaseModel):
    branches: list[QueryBranch] = Field(default_factory=list)
    coverage_warnings: list[str] = Field(default_factory=list)
    query_version: str = "query-v1"


class SourcePolicy(BaseModel):
    """外部查询策略。默认关闭联网和云端模型，避免无意外发数据。"""

    allow_nstl: bool = True
    allow_web: bool = False
    allow_cloud_model: bool = False
    sensitivity_level: Literal["public", "internal", "confidential", "secret"] = "internal"
    redact_uploaded_materials: bool = True
    freshness_days: int = Field(default=365, ge=0, le=3650)
    max_web_results: int = Field(default=8, ge=0, le=50)
    max_cloud_insights: int = Field(default=8, ge=0, le=50)
    evidence_cutoff_at: str = ""


class SourceQuery(BaseModel):
    query_id: str
    source_kind: SourceKind
    purpose: str
    query: str
    sanitized_query: str = ""
    time_range: list[int] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    stop_condition: str = ""
    status: SourceStatus = "pending"
    requested_at: str = ""
    completed_at: str = ""
    response_hash: str = ""
    returned_count: int = 0
    failure_reason: str = ""
    redaction_applied: bool = False
    external_request_allowed: bool = False


class ExternalEvidence(BaseModel):
    evidence_id: str
    source_kind: Literal["web", "cloud_model"]
    title: str = ""
    url: str = ""
    source_name: str = ""
    published_at: str = ""
    fetched_at: str = ""
    locator: str = ""
    excerpt: str = ""
    summary: str = ""
    query_id: str = ""
    hash: str = ""
    reliability: Literal["high", "medium", "low", "unverified"] = "unverified"
    evidence_level: Literal["dynamic_external", "model_context"] = "dynamic_external"
    intended_use: list[str] = Field(default_factory=lambda: ["freshness"])
    theme_ids: list[str] = Field(default_factory=list)
    conflict_group: str = ""
    is_model_generated: bool = False
    citation_verified: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class ModelInsight(BaseModel):
    insight_id: str
    query_id: str
    model_name: str = ""
    insight_type: Literal["term_expansion", "trend", "problem_variant", "cross_direction", "query_expansion"] = "query_expansion"
    text: str
    confidence: float = Field(default=0.4, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    generated_at: str = ""
    response_hash: str = ""
    externally_verified: bool = False


class EvidenceLock(BaseModel):
    locked: bool = False
    lock_id: str = ""
    locked_at: str = ""
    reason: str = ""
    evidence_pool_version: str = ""
    external_calls_blocked: bool = False
    locked_source_counts: dict[str, int] = Field(default_factory=dict)


class FreshnessSnapshot(BaseModel):
    cutoff_at: str = ""
    requirement_days: int = 365
    newest_nstl_year: int | None = None
    newest_external_at: str = ""
    nstl_count: int = 0
    web_count: int = 0
    cloud_count: int = 0
    fresh_external_count: int = 0
    limitations: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    evidence_id: str
    paper_id: str = ""
    source_type: Literal["nstl_metadata", "nstl_abstract", "nstl_fulltext", "fulltext_library", "future_work_signal"] = "nstl_metadata"
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    nstl_url: str = ""
    source_url: str = ""
    language: str = ""
    abstract: str = ""
    excerpt: str = ""
    keywords: list[str] = Field(default_factory=list)
    discipline_tags: list[str] = Field(default_factory=list)
    retrieval_score: float = 0.0
    final_score: float = 0.0
    citation_count: int = 0
    query_id: str = ""
    theme_ids: list[str] = Field(default_factory=list)
    evidence_level: Literal["A", "B", "C", "D"] = "B"
    page_ref: str = ""
    paragraph_ref: str = ""
    char_start: int | None = None
    char_end: int | None = None
    fulltext_available: bool = False
    fulltext_indexed: bool = False
    anchor_verified: bool = False
    access_status: Literal["authorized", "abstract_only", "unavailable", "unknown"] = "abstract_only"
    source_hash: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    source_kind: Literal["nstl", "fulltext_library"] = "nstl"
    match_status: str = ""
    retrieval_intent_ids: list[str] = Field(default_factory=list)
    source_published_at: str = ""
    retrieved_at: str = ""
    locator: str = ""
    intended_use: list[str] = Field(default_factory=lambda: ["landscape", "support"])


class TopicEvidenceChunk(BaseModel):
    chunk_id: str
    doc_id: str
    evidence_id: str = ""
    text: str
    chunk_index: int | None = None
    chunk_type: str = ""
    section_path: str = ""
    char_start: int | None = None
    char_end: int | None = None
    token_count: int | None = None
    score: float = 0.0
    locator: str = ""
    intent_ids: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    source: str = ""


class CandidateEvidenceLink(BaseModel):
    link_id: str
    candidate_id: str
    theme_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    evidence_id: str
    chunk_ids: list[str] = Field(default_factory=list)
    relation: Literal["supports", "contradicts", "context", "coverage_limit"] = "supports"
    rationale: str = ""
    evidence_scope: Literal["fulltext", "abstract", "metadata", "external"] = "metadata"
    locator: str = ""


class FutureWorkSignal(BaseModel):
    signal_id: str
    paper_id: str
    evidence_id: str
    text: str
    signal_type: Literal["future_work", "limitation", "open_problem", "recommended_direction"]
    source_scope: Literal["abstract", "fulltext", "metadata"] = "abstract"
    locator: str = "abstract"
    theme_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    independent_support_count: int = 1
    validation_status: Literal["candidate", "validated", "rejected"] = "candidate"
    usable_for: list[str] = Field(default_factory=lambda: ["context"])


class LandscapeSnapshot(BaseModel):
    snapshot_id: str
    year_counts: list[dict[str, Any]] = Field(default_factory=list)
    theme_counts: list[dict[str, Any]] = Field(default_factory=list)
    source_counts: list[dict[str, Any]] = Field(default_factory=list)
    method_counts: list[dict[str, Any]] = Field(default_factory=list)
    sample_count: int = 0
    abstract_count: int = 0
    fulltext_count: int = 0
    dedup_rule: str = "doi优先，题名+年份归一化兜底"
    limitations: list[str] = Field(default_factory=list)


class DisciplineParticipant(BaseModel):
    discipline: str
    role: Literal["core", "expansion"] = "expansion"
    source: Literal["retrieval_label", "model_inference"] = "model_inference"
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class CrossLink(BaseModel):
    crosslink_id: str
    source_theme_id: str
    target_theme_id: str
    relation_type: Literal["shared_method", "shared_object", "shared_data", "shared_bottleneck", "citation_path"] = "shared_method"
    title: str
    description: str
    support_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    future_work_signal_ids: list[str] = Field(default_factory=list)
    sample_count: int = 0
    confidence: float = Field(default=0.0, ge=0, le=1)
    status: Literal["candidate", "validated", "needs_review", "rejected"] = "candidate"
    uncertainty: str = ""
    relation_basis: str = ""
    alternative_explanations: list[str] = Field(default_factory=list)
    research_opportunity: str = ""
    core_discipline: str = ""
    expansion_discipline: str = ""
    participating_disciplines: list[DisciplineParticipant] = Field(default_factory=list)
    transferable_method: str = ""
    source_method: str = ""
    method_evidence: list[dict[str, Any]] = Field(default_factory=list)
    discipline_sources: list[dict[str, Any]] = Field(default_factory=list)
    validation_conditions: str = ""
    association_kind: Literal["interdisciplinary", "theme_association", "unverified"] = "unverified"


class GapScore(BaseModel):
    gap_id: str
    counter_evidence_ids: list[str] = Field(default_factory=list)
    label: str
    gap_type: Literal["problem", "method", "scenario", "evidence", "coverage"] = "evidence"
    theme_ids: list[str] = Field(default_factory=list)
    crosslink_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    future_work_signal_ids: list[str] = Field(default_factory=list)
    density: float = 0.0
    problem_coverage: float = 0.0
    method_coverage: float = 0.0
    scenario_coverage: float = 0.0
    recent_change: float = 0.0
    evidence_completeness: float = 0.0
    counter_evidence_strength: float = 0.0
    competition_heat: float = 0.0
    opportunity_space: float = 0.0
    score: float = 0.0
    rationale: str = ""
    uncertainty: str = ""
    validation_status: Literal["candidate", "validated", "coverage_limited", "rejected"] = "candidate"


class TopicCandidate(BaseModel):
    candidate_id: str
    display_title: str = ""
    title: str
    research_question: str
    research_object: str = ""
    sample_boundary: str = ""
    convergence_notes: list[str] = Field(default_factory=list)
    convergence_valid: bool = False
    hypothesis: str = ""
    innovation: str = ""
    method_route: str = ""
    validation_path: str = ""
    data_requirements: list[str] = Field(default_factory=list)
    evaluation_metrics: list[str] = Field(default_factory=list)
    milestones: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    resource_requirements: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    risk_mitigation: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    crosslink_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    future_work_signal_ids: list[str] = Field(default_factory=list)
    user_material_refs: list[str] = Field(default_factory=list)
    representative_evidence_ids: list[str] = Field(default_factory=list)
    external_evidence_ids: list[str] = Field(default_factory=list)
    fit_rationale: str = ""
    distinctiveness_rationale: str = ""
    score_dimension_refs: dict[str, list[str]] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)
    validation_status: Literal["supported", "provisional", "evidence_insufficient"] = "provisional"
    recommendation_level: Literal["recommended", "exploratory", "pending_evidence"] = "exploratory"
    # Output purpose is independent of scientific evidence/validation status.
    direction_type: Literal["recommended", "exploratory"] | None = None
    evidence_links: list[CandidateEvidenceLink] = Field(default_factory=list)
    reader_content: dict[str, Any] = Field(default_factory=dict)


class ScoreDimensionResult(BaseModel):
    key: str
    label: str
    score: float = 0.0
    max_score: float = 0.0
    weight: float = 0.0
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    material_refs: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high", "pending"] = "medium"
    requires_review: bool = False


class ScoringPolicy(BaseModel):
    objective: Literal["balanced", "feasibility", "timeliness", "novelty", "impact"] = "balanced"
    default_weights: dict[str, float] = Field(default_factory=lambda: {
        "problem_fit": 0.20,
        "evidence_support": 0.20,
        "feasibility_data": 0.15,
        "novelty_opportunity": 0.15,
        "timeliness": 0.10,
        "validation_clarity": 0.10,
        "potential_value": 0.10,
    })
    active_weights: dict[str, float] = Field(default_factory=dict)
    adjustment_reason: str = ""
    version: str = "score-v2"


class ReportTocEntry(BaseModel):
    key: str
    title: str
    level: int = 1
    page: int | None = None
    is_appendix: bool = False


class ReportPageMetrics(BaseModel):
    target_body_pages: list[int] = Field(default_factory=lambda: [8, 12])
    rendered_total_pages: int | None = None
    rendered_body_pages: int | None = None
    rendered_appendix_pages: int | None = None
    status: Literal["pending", "within_target", "over_target", "under_target", "unavailable"] = "pending"
    warning: str = ""


class ScoreBreakdown(BaseModel):
    candidate_id: str
    value: float = 0.0
    relevance: float = 0.0
    freshness: float = 0.0
    novelty: float = 0.0
    evidence_support: float = 0.0
    feasibility: float = 0.0
    data_availability: float = 0.0
    resource_fit: float = 0.0
    competition_pressure: float = 0.0
    expected_impact: float = 0.0
    total: float = 0.0
    score_range: list[float] = Field(default_factory=list)
    ranking_status: Literal["confirmed", "provisional", "pending_confirmation"] = "provisional"
    weights: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    explanation: str = ""
    dimensions: list[ScoreDimensionResult] = Field(default_factory=list)
    default_total: float | None = None
    weight_adjustment: str = ""


class QualityFinding(BaseModel):
    code: str
    severity: Literal["info", "warning", "review", "blocker"] = "warning"
    message: str
    stage_key: str = "quality_gate"
    query_id: str = ""
    model_call_id: str = ""
    anchor: str = ""
    candidate_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class QualityGate(BaseModel):
    passed: bool = False
    report_eligibility: Literal["formal", "blocked"] = "blocked"
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    review_required: bool = False
    checked_rules: list[str] = Field(default_factory=list)
    evidence_lock_valid: bool = False
    source_integrity_valid: bool = False
    citation_completeness: float = 0.0
    findings: list[QualityFinding] = Field(default_factory=list)


class CorrectionContext(BaseModel):
    source_task_id: str = ""
    correction_task_id: str = ""
    reason: str = ""
    operator: str = ""
    returned_at: str = ""
    rerun_at: str = ""
    status: Literal["none", "awaiting_correction", "rerun_created", "superseded"] = "none"


class TopicReportPlan(BaseModel):
    report_version: str = "report-v0"
    report_title: str = ""
    executive_summary: str = ""
    limitations: list[str] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    chart_specs: list[dict[str, Any]] = Field(default_factory=list)
    citation_map: dict[str, int] = Field(default_factory=dict)
    citation_links: dict[str, str] = Field(default_factory=dict)
    template_version: str = "TopicReportV4"
    quality_status: Literal["pending", "eligible", "blocked"] = "pending"
    # Keep the legacy report_type value for API compatibility; report_mode
    # carries the new decision-oriented brief semantics.
    report_type: Literal["正式选题报告", "深度选题简报"] = "正式选题报告"
    report_mode: Literal["deep_brief", "full_report"] = "full_report"
    source_cutoff_at: str = ""
    source_status_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_lock_status: str = "unlocked"
    external_evidence_counts: dict[str, int] = Field(default_factory=dict)
    section_specs: list[dict[str, Any]] = Field(default_factory=list)
    table_specs: list[dict[str, Any]] = Field(default_factory=list)
    chart_manifest: list[dict[str, Any]] = Field(default_factory=list)
    provisional_page_baseline: str = "8-12（待真实案例确认）"
    publication_status: Literal["draft", "eligible", "published", "blocked"] = "draft"
    snapshot_id: str = ""
    cover_metadata: dict[str, Any] = Field(default_factory=dict)
    toc_entries: list[ReportTocEntry] = Field(default_factory=list)
    page_metrics: ReportPageMetrics = Field(default_factory=ReportPageMetrics)
    body_page_target: str = "8-12"
    appendix_policy: str = "unlimited-separated"
    format_consistency: dict[str, Any] = Field(default_factory=dict)
    evidence_package_manifest: list[dict[str, Any]] = Field(default_factory=list)


class ReportSectionSpec(BaseModel):
    key: str
    title: str
    order: int
    required: bool = True
    body_kind: Literal["narrative", "table", "chart", "appendix"] = "narrative"
    evidence_ids: list[str] = Field(default_factory=list)
    max_lines: int | None = None
    chart_keys: list[str] = Field(default_factory=list)
    table_keys: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    is_appendix: bool = False
    page_budget: int | None = None
    toc_label: str = ""
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class TableSpec(BaseModel):
    key: str
    title: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    snapshot_version: str = ""


class ChartSpec(BaseModel):
    key: str
    title: str
    chart_type: Literal["network", "line", "stacked_bar", "heatmap", "matrix", "scatter", "bubble", "bar", "lollipop", "donut", "radar", "stat_card"] = "bar"
    data: Any = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    data_snapshot_version: str = ""
    report_version: str = ""
    limitation: str = ""
    section_key: str = ""
    candidate_id: str = ""
    figure_number: int | None = None
    caption: str = ""
    source_note: str = ""
    data_basis: str = ""
    placement: str = "inline"
    data_status: Literal["ready", "insufficient", "unavailable", "degraded"] = "ready"
    empty_reason: str = ""
    min_data_points: int = 1
    alt_text: str = ""
    visual_role: str = ""
    chart_version: str = "chart-v1"


class ReportSnapshot(BaseModel):
    snapshot_id: str = ""
    report_version: str = ""
    evidence_pool_version: str = ""
    chart_data_snapshot_version: str = ""
    template_version: str = "TopicReportV4"
    chart_version: str = "chart-v1"
    content_version: str = "content-v1"
    citation_version: str = "citation-v1"
    created_at: str = ""
    frozen: bool = False
    rendered_page_count: int | None = None
    body_page_count: int | None = None
    appendix_page_count: int | None = None
    font_policy: str = "CJK正文深灰与蓝色标题；缺少字体时图表降级为文字说明"
    render_checks: dict[str, Any] = Field(default_factory=dict)
    chart_manifest: list[dict[str, Any]] = Field(default_factory=list)
    format_hashes: dict[str, str] = Field(default_factory=dict)
    report_plan_snapshot: dict[str, Any] = Field(default_factory=dict)
    result_data_snapshot: dict[str, Any] = Field(default_factory=dict)


class CandidateSelection(BaseModel):
    selected_candidate_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    operator: str = ""
    selected_at: str = ""
    report_version: str = ""


class TopicSelectionState(BaseModel):
    raw_user_input: str
    user_scope: str = ""
    task_type: str = "智能选题"
    current_stage: str = "created"
    stage_trace: list[TopicStageTrace] = Field(default_factory=list)
    process_trace: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    active_progress: TopicProgressEvent | None = None
    progress_events: list[TopicProgressEvent] = Field(default_factory=list, max_length=120)
    input_profile: InputProfile = Field(default_factory=InputProfile)
    constraint_card: ConstraintCard = Field(default_factory=ConstraintCard)
    term_map: list[TermMapEntry] = Field(default_factory=list)
    task_profile: dict[str, Any] = Field(default_factory=dict)
    theme_tree: ThemeTree = Field(default_factory=ThemeTree)
    query_plan: QueryPlan = Field(default_factory=QueryPlan)
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    source_queries: list[SourceQuery] = Field(default_factory=list)
    external_evidence: list[ExternalEvidence] = Field(default_factory=list)
    model_insights: list[ModelInsight] = Field(default_factory=list)
    evidence_lock: EvidenceLock = Field(default_factory=EvidenceLock)
    freshness_snapshot: FreshnessSnapshot = Field(default_factory=FreshnessSnapshot)
    evidence_pool: list[EvidenceRecord] = Field(default_factory=list)
    evidence_chunks: list[TopicEvidenceChunk] = Field(default_factory=list)
    search_traces: list[dict[str, Any]] = Field(default_factory=list)
    landscape_snapshot: LandscapeSnapshot | None = None
    future_work_signals: list[FutureWorkSignal] = Field(default_factory=list)
    crosslinks: list[CrossLink] = Field(default_factory=list)
    gaps: list[GapScore] = Field(default_factory=list)
    candidates: list[TopicCandidate] = Field(default_factory=list)
    score_breakdowns: list[ScoreBreakdown] = Field(default_factory=list)
    scoring_policy: ScoringPolicy = Field(default_factory=ScoringPolicy)
    quality_gate: QualityGate = Field(default_factory=QualityGate)
    review_tasks: list[dict[str, Any]] = Field(default_factory=list)
    resolution_logs: list[dict[str, Any]] = Field(default_factory=list)
    confirmation: TopicConfirmation = Field(default_factory=TopicConfirmation)
    report_plan: TopicReportPlan = Field(default_factory=TopicReportPlan)
    report_snapshot: ReportSnapshot = Field(default_factory=ReportSnapshot)
    candidate_selection: CandidateSelection | None = None
    output_markdown: str = ""
    output_json: dict[str, Any] = Field(default_factory=dict)
    uploaded_files: list[dict[str, Any]] = Field(default_factory=list)
    versions: TopicVersions = Field(default_factory=TopicVersions)
    checkpoint_id: str = ""
    checkpoint_revision: int = 0
    resume_from_stage: str = ""
    retry_count: int = 0
    worker_id: str = ""
    error_message: str = ""
    stage_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    model_calls: list[TopicModelCall] = Field(default_factory=list)
    analysis_policy: TopicAnalysisPolicy = Field(default_factory=TopicAnalysisPolicy)
    parent_task_id: str = ""
    correction_context: CorrectionContext = Field(default_factory=CorrectionContext)

    @field_validator("raw_user_input")
    @classmethod
    def validate_input(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("研究方向不能为空")
        return value


class TopicSelectionRunRequest(BaseModel):
    user_input: str = Field(min_length=1, max_length=12000)
    topic_title: str = Field(default="", max_length=300)
    language_preference: str = "mixed"
    year_range: list[int] = Field(default_factory=list)
    uploaded_files: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    enable_web: bool = False
    enable_cloud_model: bool = False
    sensitivity_level: Literal["public", "internal", "confidential", "secret"] = "internal"
    freshness_days: int = Field(default=365, ge=0, le=3650)
    candidate_count: int = Field(default=3, ge=3, le=5)
    output_preference: Literal["balanced", "recommended", "exploratory"] | None = None
    scoring_objective: Literal["balanced", "feasibility", "timeliness", "novelty", "impact"] = "balanced"
    scoring_weight_overrides: dict[str, float] = Field(default_factory=dict)
    evidence_cutoff_at: str = ""
    source_policy: SourcePolicy | None = None
    analysis_mode: Literal["deep", "fast"] = "deep"

    @field_validator("year_range")
    @classmethod
    def validate_year_range(cls, value: list[int]) -> list[int]:
        if value and (len(value) != 2 or value[0] > value[1]):
            raise ValueError("year_range 必须是升序的起止年份")
        return value


class TopicSelectionRunResponse(BaseModel):
    success: bool = True
    run_id: str
    status: str = "running"
    state: TopicSelectionState


class TopicConfirmationRequest(BaseModel):
    selected_option: str = Field(min_length=1, max_length=100)
    user_note: str = Field(default="", max_length=1000)


class TopicReviewRequest(BaseModel):
    decision: Literal["approve", "return_to_retrieval", "limit", "dismiss"]
    reason: str = Field(default="", max_length=2000)
    operator: str = Field(default="", max_length=120)


class CandidateSelectionRequest(BaseModel):
    selected_candidate_ids: list[str] = Field(min_length=1, max_length=5)
    reason: str = Field(default="", max_length=2000)
    operator: str = Field(default="", max_length=120)
