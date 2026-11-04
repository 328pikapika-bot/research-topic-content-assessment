import asyncio

import pytest
from pydantic import ValidationError

from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph, STAGES
from app.agents.content_assessment.schemas import (
    Claim,
    ContentAssessmentRunRequest,
    ContentAssessmentState,
    EvidenceChunk,
    EvidenceRecord,
    NoveltyDecision,
    ResearchNineFields,
    UploadedFileDigest,
    ReportPreferences,
    ReportSectionOverride,
    ReviewTask,
    StageTraceItem,
)
from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekJSONResult
from app.common.retrieval.schemas import SearchResponse, StandardPaper


def run(coroutine):
    return asyncio.run(coroutine)


def test_stage_trace_records_duration_and_preserves_legacy_timestamps() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("评估阶段计时测试")
    graph._set_stage(state, "document_intake", "running", "开始解析")
    trace = state.stage_trace[0]
    assert trace.started_at
    assert trace.duration_seconds is None
    graph._set_stage(state, "document_intake", "completed", "解析完成")
    assert trace.completed_at
    assert trace.duration_seconds is not None
    assert trace.duration_seconds >= 0
    assert trace.model_dump()["duration_seconds"] == trace.duration_seconds
    legacy = StageTraceItem.model_validate({
        "stage_key": "document_intake",
        "stage_name": "材料解析",
        "status": "completed",
        "started_at": trace.started_at,
        "completed_at": trace.completed_at,
    })
    assert legacy.duration_seconds is None


def test_process_trace_records_only_executed_work_without_fabricated_minimum() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("评估阶段计时测试")
    graph._start_process_steps(state, "document_intake")
    assert len(state.process_trace["document_intake"]) == 1
    graph._complete_process_steps(state, "document_intake")
    step = state.process_trace["document_intake"][0]
    assert step.status == "completed"
    assert step.started_at and step.completed_at
    assert step.duration_seconds is not None and step.duration_seconds >= 0
    assert step.summary


def test_quality_model_ignores_unknown_evidence_and_keeps_rule_eligibility(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)

    class QualityClient:
        async def generate_json_with_metrics(self, **kwargs):
            return DeepSeekJSONResult(
                payload={"findings": [
                    {"message": "需核对证据适用范围", "claim_id": "C-001", "evidence_ids": ["E-L-001"]},
                    {"message": "无效引用", "claim_id": "C-001", "evidence_ids": ["E-L-999"]},
                ], "summary": "已复核"},
                metrics=DeepSeekCallMetrics(profile="test", model="test", stream=False, timeout_seconds=1, done_ms=5),
            )

    graph = ContentAssessmentGraph(model_client=QualityClient())
    state = graph.create_initial_state("科研评估材料" * 20)
    state.claims = [Claim(claim_id="C-001", text="测试主张")]
    state.evidence_records = [EvidenceRecord(evidence_id="E-L-001", title="测试文献", source_type="literature")]
    run(graph._quality_gate_stage(state))
    assert state.quality_gate.model_status == "completed"
    assert len(state.quality_gate.model_findings) == 1
    assert state.quality_gate.model_findings[0]["evidence_ids"] == ["E-L-001"]
    assert state.quality_gate.report_eligibility == "exploratory"


def test_quality_model_failure_retains_rule_checks(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)

    class FailingClient:
        async def generate_json_with_metrics(self, **kwargs):
            raise ValueError("unavailable")

    graph = ContentAssessmentGraph(model_client=FailingClient())
    state = graph.create_initial_state("科研评估材料" * 20)
    state.claims = [Claim(claim_id="C-001", text="测试主张")]
    state.evidence_records = [EvidenceRecord(evidence_id="E-L-001", title="测试文献", source_type="literature")]
    run(graph._quality_gate_stage(state))
    assert state.quality_gate.model_status == "fallback"
    assert len(state.quality_gate.checks) == 4
    assert state.process_trace["quality_gate"][0].status == "failed"


async def _run_after_confirmation(graph: ContentAssessmentGraph, state: ContentAssessmentState) -> ContentAssessmentState:
    result = await graph.run(state)
    if result.current_stage == "awaiting_confirmation":
        if result.confirmation.reason == 'missing_fields':
            result.nine_fields = ResearchNineFields(research_direction='微流控芯片', research_content=state.raw_user_input, key_technologies=['微流控灌流'])
            result.nine_fields_confirmed = True
            result.report_preferences.artifact_type = result.report_preferences.resolved_artifact_type = 'project'
            result.output_json['input_fields_supplemented'] = True
        result.confirmation.selected_option = "supplement_fields" if result.confirmation.reason == 'missing_fields' else "continue_with_assumptions"
        result.confirmation.needed = False
        result.assessment_profile.confirmation_required = False
        result.current_stage = result.resume_from_stage
        result = await graph.run(result)

    return result


def test_missing_fields_pause_at_extraction_with_supplement_action() -> None:
    result = run(ContentAssessmentGraph().run(ContentAssessmentGraph().create_initial_state("拟构建多器官芯片培养系统。")))
    assert result.current_stage == "awaiting_confirmation"
    assert result.confirmation.needed
    assert result.confirmation.reason == "missing_fields"
    assert result.confirmation.options == [{"label": "补充缺项后继续", "value": "supplement_fields"}]
    assert result.resume_from_stage == "claim_extraction"
    assert result.stage_trace[0].status == "completed"
    assert result.stage_trace[1].status == "blocked"


def test_confirmed_complete_nine_fields_do_not_request_boundary_confirmation() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("请评估这份完整科研项目方案。")
    state.nine_fields = ResearchNineFields(
        research_direction="多器官芯片",
        research_goal="提升长期培养稳定性",
        research_content="研究多器官互作与屏障功能",
        technical_route="芯片制备、共培养和功能验证",
        key_technologies=["微流控", "共培养"],
        evaluation_indicators=["稳定培养不少于30天"],
        timeline="第1—12个月",
        application_scenarios=["药物评价"],
        expected_outcomes=["原型系统"],
    )
    state.nine_fields_confirmed = True

    graph._document_intake(state)

    assert state.confirmation.needed is False
    assert state.assessment_profile.confirmation_required is False
    assert state.assessment_profile.pending_confirmation_fields == []
    assert "用户已确认的九项评估内容" in state.assessment_profile.assumptions[0]


def test_langgraph_checkpoint_records_pause_and_resume(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建多器官芯片培养系统。")
    state.checkpoint_id = "test-checkpoint-resume"

    paused = run(graph.run(state))
    assert paused.current_stage == "awaiting_confirmation"
    assert paused.checkpoint_history[-1].status == "awaiting_confirmation"
    paused_again = run(graph.run(paused))
    assert paused_again.current_stage == "awaiting_confirmation"
    assert paused_again.checkpoint_revision == paused.checkpoint_revision

    paused.nine_fields = ResearchNineFields(research_direction='微流控芯片', research_content=paused.raw_user_input, key_technologies=['微流控灌流'])
    paused.nine_fields_confirmed = True
    paused.report_preferences.artifact_type = paused.report_preferences.resolved_artifact_type = 'project'
    paused.output_json['input_fields_supplemented'] = True
    paused.confirmation.selected_option = "supplement_fields"
    paused.confirmation.needed = False
    paused.assessment_profile.confirmation_required = False
    paused.current_stage = paused.resume_from_stage
    resumed = run(graph.run(paused))
    assert resumed.current_stage == "failed"
    assert not resumed.confirmation.needed
    assert resumed.output_json["generation_recovery"]["reason"] == "report_sources"
    assert resumed.resume_from_stage == "retrieval"
    assert resumed.checkpoint_history[-1].status == "failed"
    snapshot = graph._compiled_graph.get_state({"configurable": {"thread_id": state.checkpoint_id}})
    assert snapshot.values["payload"]["current_stage"] == "failed"


def test_document_roles_are_constrained_to_assessment_roles() -> None:
    request = ContentAssessmentRunRequest(
        user_input="项目拟建立多器官芯片培养系统。",
        document_roles={"project-1": "PROJECT", "guide-1": "REFERENCE"},
    )
    assert request.document_roles["project-1"] == "PROJECT"
    with pytest.raises(ValidationError):
        ContentAssessmentRunRequest(user_input="测试", document_roles={"x": "UNKNOWN"})


def test_claim_extraction_splits_material_description_into_traceable_claims() -> None:
    state = ContentAssessmentGraph().create_initial_state(
        "项目拟构建多器官芯片互作系统；目标是将培养稳定性提高 20%；计划在药物毒性场景验证。"
    )
    ContentAssessmentGraph()._claim_extraction(state)
    assert len(state.claims) == 3
    assert state.claims[1].assertion_type == "target"
    assert state.claims[1].claim_id == "C-002"
    assert state.indicators[0].source_claim_id == "C-002"


def test_evidence_links_remain_claim_traceable() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建多器官芯片培养系统并提高培养稳定性。")
    graph._claim_extraction(state)
    state.evidence_records = [
        EvidenceRecord(
            evidence_id="E-L-001", title="Organ-on-chip review", source_type="literature",
            excerpt="A comparable organ-on-chip culture approach.", usable_for_novelty=True,
        )
    ]
    graph._comparison(state)
    assert state.claim_evidence_links
    assert {link.claim_id for link in state.claim_evidence_links} <= {claim.claim_id for claim in state.claims}
    assert {link.evidence_id for link in state.claim_evidence_links} == {"E-L-001"}


def test_claim_evidence_view_preserves_saved_fields_when_excerpt_has_no_fields() -> None:
    graph = ContentAssessmentGraph()
    claim = Claim(claim_id="C-001", text="对象 A 采用方法 B", claim_fields={
        "object": "对象 A", "method": "方法 B",
    })
    evidence = EvidenceRecord(
        evidence_id="E-FULL", title="已保存字段文献", source_type="literature",
        excerpt="实验结果仅描述统计结果，没有重复对象和方法字段。",
        fulltext_available=True, anchor_verified=True, access_status="authorized",
        char_start=0, char_end=20, section_ref="Results",
        comparison_fields={"object": "对象 A", "method": "方法 B", "metric": "指标 C"},
    )
    state = ContentAssessmentState(raw_user_input="test", claims=[claim], evidence_records=[evidence])
    view = graph._claim_evidence_view(state, claim, evidence)
    assert view is not None
    assert view.comparison_fields == evidence.comparison_fields


def test_claim_evidence_view_keeps_abstract_fields_in_directional_branch() -> None:
    graph = ContentAssessmentGraph()
    claim = Claim(claim_id="C-001", text="对象 A", claim_fields={"object": "对象 A"})
    evidence = EvidenceRecord(
        evidence_id="E-ABSTRACT", title="摘要文献", source_type="literature",
        abstract="对象 A 的相关研究摘要。", retrieval_intent_ids=["claim:C-001"],
        comparison_fields={"object": "对象 A", "method": "方法 B"},
    )
    state = ContentAssessmentState(raw_user_input="test", claims=[claim], evidence_records=[evidence])
    view = graph._claim_evidence_view(state, claim, evidence)
    assert view is not None
    assert view.evidence_level == "B"
    assert view.comparison_fields["object"] == "对象 A"
    assert view.comparison_fields["method"] == "方法 B"


def test_model_comparison_uses_verified_rule_matrix_when_model_omits_matrix() -> None:
    graph = ContentAssessmentGraph()
    claim = Claim(claim_id="C-001", text="对象 A 采用方法 B", claim_fields={
        "object": "对象 A", "method": "方法 B",
    })
    evidence = EvidenceRecord(
        evidence_id="E-FULL", title="可核对文献", source_type="literature",
        query_group="首页推荐案例核验文献", claim_ids=["C-001"],
        excerpt="对象 A 采用方法 B 的实验结果。", fulltext_available=True,
        anchor_verified=True, access_status="authorized", char_start=0, char_end=20,
        section_ref="Results", comparison_fields={"object": "对象 A", "method": "方法 B"},
    )
    state = ContentAssessmentState(raw_user_input="test", claims=[claim], evidence_records=[evidence])
    accepted = graph._apply_model_comparison(state, {"links": [{
        "claim_id": "C-001", "evidence_id": "E-FULL", "relation": "supports",
    }]})
    assert accepted is True
    link = state.claim_evidence_links[0]
    assert link.field_matrix["object"] == "covered"
    assert link.field_matrix["method"] == "covered"


def test_abstract_only_evidence_cannot_form_formal_originality_decision() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建多器官芯片培养系统。")
    graph._claim_extraction(state)
    state.evidence_records = [EvidenceRecord(
        evidence_id="E-ABSTRACT",
        title="Abstract only",
        source_type="literature",
        abstract="organ chip culture method",
        excerpt="organ chip culture method",
        usable_for_novelty=True,
    )]
    graph._comparison(state)
    graph._assessment_stage(state)
    assert all(item.label in {"方向性支持", "暂未发现可比证据", "当前不可判断", "目标待验证", "证据不足"} for item in state.novelty_decisions)
    assert any(item.evidence_ids for item in state.novelty_decisions)
    assert any(item.relation == "partially_supports" for item in state.claim_evidence_links)


def test_abstract_evidence_produces_directional_label_for_target_claim() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建多器官芯片培养系统并验证长期稳定性。")
    graph._claim_extraction(state)
    state.evidence_records = [EvidenceRecord(
        evidence_id="E-DIRECTIONAL",
        title="Organ chip culture prior",
        source_type="literature",
        abstract="A related organ chip culture method supports long-term perfusion.",
        excerpt="A related organ chip culture method supports long-term perfusion.",
        usable_for_novelty=True,
    )]

    graph._comparison(state)
    graph._assessment_stage(state)

    assert any(item.label == "方向性支持" for item in state.novelty_decisions)
    assert all(item.label not in {"待补证", "证据不足"} for item in state.novelty_decisions)


def test_field_mismatch_blocks_keyword_based_support_and_advancedness() -> None:
    graph = ContentAssessmentGraph()
    state = ContentAssessmentState(raw_user_input="test", claims=[Claim(
        claim_id="C-001",
        text="对象 A 采用方法 B，在数据集 C 上以指标 D 验证。",
        claim_fields={
            "object": "对象 A", "method": "方法 B", "data": "数据集 C",
            "baseline": "基线 X", "metric": "指标 D", "time": "6 个月", "validation": "验证 Y",
        },
    )], evidence_records=[EvidenceRecord(
        evidence_id="E-FULL",
        title="Similar keywords",
        source_type="literature",
        excerpt="full text excerpt",
        fulltext_available=True,
        anchor_verified=True,
        access_status="authorized",
        source_hash="sha256",
        char_start=0,
        char_end=16,
        comparison_fields={
            "object": "对象 A", "method": "方法 B", "data": "数据集 Z",
            "baseline": "基线 Q", "metric": "指标 D", "time": "2 天", "validation": "验证 W",
        },
    )], evidence_chunks=[EvidenceChunk(
        chunk_id="CH-FULL", evidence_id="E-FULL", excerpt="full text excerpt",
        access_status="authorized", source_hash="sha256", char_start=0, char_end=16, reopenable=True,
    )])
    graph._comparison(state)
    graph._assessment_stage(state)
    link = state.claim_evidence_links[0]
    assert link.relation == "not_comparable"
    assert link.incomparability_reasons
    assert state.novelty_decisions[0].label == "暂未发现可比证据"
    assert state.advancedness_decisions[0].label == "证据不足或存疑"


def test_review_resolution_records_log_and_invalidates_downstream_stages() -> None:
    graph = ContentAssessmentGraph()
    state = ContentAssessmentState(raw_user_input="test", review_tasks=[ReviewTask(
        review_task_id="RV-001",
        review_type="source_gap",
        claim_id="C-001",
        title="补证",
        reason="缺少全文证据",
    )])
    resume_stage = graph.resolve_review_task(
        state, "RV-001", decision="return_to_retrieval", reason="补充同口径全文", operator="expert-1"
    )
    assert resume_stage == "retrieval"
    assert state.review_tasks[0].status == "returned"
    assert state.resolution_logs[0].decision == "return_to_retrieval"
    assert state.invalidation_records[0].affected_stages == ["retrieval", "comparison", "assessment", "landscape", "quality_gate", "report"]
    assert state.resume_from_stage == "retrieval"


def test_missing_external_evidence_degrades_to_exploratory_not_originality_claim(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建多器官芯片培养系统。")
    result = run(_run_after_confirmation(graph, state))
    assert result.quality_gate.report_eligibility == "blocked"
    assert result.output_json["retrieval_notice"]
    assert all(item.label in {"目标待验证", "当前不可判断"} for item in result.novelty_decisions)
    assert "绝对原创" not in result.output_markdown
    assert not result.confirmation.needed
    assert result.output_json["generation_recovery"]["reason"] == "report_sources"
    assert result.report_snapshot is None
    assert not result.output_markdown


def test_markdown_appendix_renders_auditable_records_without_raw_json(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    result = run(_run_after_confirmation(ContentAssessmentGraph(), ContentAssessmentGraph().create_initial_state("拟构建多器官芯片培养系统。")))
    assert result.query_plans  # audit stays in state, outside an unpublished report
    assert result.report_snapshot is None
    assert not result.output_markdown
    assert '"filters"' not in result.output_markdown
    assert "章节生成内容" not in result.output_markdown


def test_quality_gate_blocks_formal_report_without_project_material() -> None:
    state = ContentAssessmentState(raw_user_input="测试")
    ContentAssessmentGraph()._quality_gate(state)
    assert not state.quality_gate.passed
    assert state.quality_gate.report_eligibility == "exploratory"
    assert state.quality_gate.blockers


def test_decision_labels_and_report_version_are_constrained(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        NoveltyDecision(claim_id="C-001", label="绝对原创", rationale="不允许")
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    result = run(_run_after_confirmation(ContentAssessmentGraph(), ContentAssessmentGraph().create_initial_state("拟构建多器官芯片系统。")))
    assert result.report_plan.report_version.startswith("report-")
    assert result.report_snapshot is None
    assert not result.confirmation.needed
    assert result.output_json["generation_recovery"]["reason"] == "report_sources"
    assert len(result.stage_trace) == len(STAGES) == 8
    assert result.stage_trace[-1].status != "completed"
    # Technical interruptions use recovery, never a user confirmation. They
    # cannot claim evaluation or later report stages have already completed.
    assert next(item for item in result.stage_trace if item.stage_key == "assessment").status == "failed"


def test_standard_retrieval_builds_query_groups_and_deduplicates_evidence(monkeypatch) -> None:
    from app.agents.content_assessment import public_literature
    async def no_public_supplement(state):
        return None
    monkeypatch.setattr(public_literature, "supplement", no_public_supplement)
    class FakeSearchClient:
        def search(self, request):
            return SearchResponse(
                success=True,
                total=2,
                papers=[
                    StandardPaper(paper_id="same", title="Shared evidence", abstract=f"{request.query} microfluidic organ chip", year=2025, retrieval_query=request.query),
                    StandardPaper(paper_id=request.query[-20:], title=f"{request.query[:20]} evidence", abstract="organ chip method", year=2024, retrieval_query=request.query),
                ],
                search_trace={"query": request.query, "paper_num_returned": 2},
            )

    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "http://retrieval.test")
    monkeypatch.setattr(graph_module, "LiteratureSearchClient", FakeSearchClient)
    state = ContentAssessmentGraph().create_initial_state("拟构建微流控器官芯片并验证长期培养稳定性。")
    result = run(_run_after_confirmation(ContentAssessmentGraph(), state))
    assert len([plan for plan in result.query_plans if plan.query_role != "point_supplement"]) == 6
    assert any(plan.query_role == "point_supplement" for plan in result.query_plans)
    restored = ContentAssessmentState.model_validate_json(result.model_dump_json())
    assert restored.query_plans == result.query_plans
    assert result.output_json["retrieval_stats"]["query_group_count"] == len(result.query_plans)
    # The shared paper remains one record even after supplementary searches.
    assert len(result.evidence_records) == 1 + len({plan.query[-20:] for plan in result.query_plans})
    assert len({item.evidence_id for item in result.evidence_records}) == len(result.evidence_records)
    assert result.report_metrics.long_report_status == "pending"
    assert result.report_snapshot is None


def test_six_query_roles_are_scoped_to_known_claims_or_indicators(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建微流控器官芯片并验证长期培养稳定性。")
    result = run(_run_after_confirmation(graph, state))

    assert len(result.query_plans) == 6
    assert {item.query_role for item in result.query_plans} == {
        "system_prior_art", "mechanism_method", "metric_baseline",
        "application_validation", "industry_market_competitive", "industry_standard_regulatory",
    }
    assert all(item.claim_ids or item.indicator_ids for item in result.query_plans)
    known_claims = {item.claim_id for item in result.claims}
    known_indicators = {item.indicator_id for item in result.indicators}
    assert all(set(item.claim_ids) <= known_claims for item in result.query_plans)
    assert all(set(item.indicator_ids) <= known_indicators for item in result.query_plans)


def test_reader_claim_groups_persist_parent_and_aggregation_reason() -> None:
    from app.agents.content_assessment.report_composition import claim_groups

    state = ContentAssessmentState(raw_user_input="聚合主张")
    state.claims = [
        Claim(claim_id="C-001", text="构建肝脏器官芯片系统。"),
        Claim(claim_id="C-002", text="通过微流控维持长期稳定培养。"),
        Claim(claim_id="C-003", text="实现多器官芯片集成。"),
        Claim(claim_id="C-004", text="提高药敏筛选通量。"),
        Claim(claim_id="C-005", text="验证药物响应预测。"),
        Claim(claim_id="C-006", text="控制批间一致性和质量控制。"),
    ]

    groups = claim_groups(state)

    assert len(groups) < len(state.claims)
    assert all(group[0].is_group_representative for group in groups)
    assert all(group[0].aggregation_group and group[0].aggregation_reason for group in groups)
    children = [claim for claim in state.claims if claim.parent_claim_id]
    assert children
    assert all(child.aggregation_group and child.aggregation_reason for child in children)
    assert all(child.parent_claim_id in {group[0].claim_id for group in groups} for child in children)


def test_model_query_planner_rejects_unrelated_query(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "http://retrieval.test")
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建多器官芯片培养系统。")
    graph._claim_extraction(state)
    accepted = graph._apply_model_query_plans(
        state,
        {"queries": [
            {"query": f"{state.claims[0].text} 技术路线先例", "query_role": "system_prior_art", "claim_ids": ["C-001"], "rationale": "查找系统先例"},
            {"query": "AI药物预测 深度学习", "query_role": "mechanism_method", "claim_ids": ["C-001"], "rationale": "无关主题"},
        ]},
        year_range=[2020, 2026],
        exclusion_terms=[],
    )
    assert accepted
    assert len(state.query_plans) == 1
    assert all("AI药物预测" not in item.query for item in state.query_plans)


def test_evidence_tier_is_derived_from_reopenable_fulltext() -> None:
    abstract = EvidenceRecord(
        evidence_id="E-ABSTRACT", title="Abstract", source_type="literature",
        abstract="abstract text", excerpt="abstract text", usable_for_novelty=True,
    )
    fulltext = EvidenceRecord(
        evidence_id="E-FULL", title="Full text", source_type="literature",
        excerpt="verified full text", fulltext_available=True, anchor_verified=True,
        access_status="authorized", char_start=10, char_end=28, usable_for_novelty=False,
    )
    title_only = EvidenceRecord(
        evidence_id="E-TITLE", title="Title only", source_type="literature",
    )

    assert (abstract.evidence_level, abstract.usable_for_novelty) == ("B", False)
    assert (fulltext.evidence_level, fulltext.usable_for_novelty) == ("A", True)
    assert (title_only.evidence_level, title_only.usable_for_novelty) == ("C", False)


def test_keyword_overlap_without_seven_field_evidence_cannot_support_claim() -> None:
    graph = ContentAssessmentGraph()
    state = ContentAssessmentState(
        raw_user_input="多器官芯片",
        claims=[Claim(claim_id="C-001", text="多器官芯片系统", searchable_terms=["多器官芯片"] )],
        evidence_records=[EvidenceRecord(
            evidence_id="E-KEYWORD", title="多器官芯片系统先例", source_type="literature",
            excerpt="verified full text", fulltext_available=True, anchor_verified=True,
            access_status="authorized", char_start=0, char_end=16,
        )],
        evidence_chunks=[EvidenceChunk(
            chunk_id="CH-KEYWORD", evidence_id="E-KEYWORD", excerpt="verified full text",
            access_status="authorized", source_hash="sha256", char_start=0, char_end=16,
            reopenable=True,
        )],
    )
    graph._comparison(state)
    graph._assessment_stage(state)

    assert state.claim_evidence_links[0].relation == "not_comparable"
    assert state.novelty_decisions[0].label == "暂未发现可比证据"
    assert state.advancedness_decisions[0].label != "可比条件下领先"


class RecordingModelClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.closed = False

    async def generate_json_with_metrics(self, **kwargs) -> DeepSeekJSONResult:
        self.calls.append(kwargs)
        return DeepSeekJSONResult(
            payload={
                "assessment": {"title": "多器官芯片系统评估", "research_domain": "生物医学工程"},
                "nine_fields": {"research_direction": "器官芯片", "key_technologies": ["微流控", "共培养"]},
                "claims": [{
                    "text": "拟构建多器官芯片互作系统。",
                    "claim_type": "technical",
                    "assertion_type": "planned",
                    "searchable_terms": ["多器官芯片", "微流控"],
                    "source_document_id": "user_material",
                    "source_quote": "项目拟构建多器官芯片互作系统，并提高培养稳定性20%。",
                }],
                "indicators": [{"name": "培养稳定性", "value": "20", "unit": "%", "status": "target", "source_claim_index": 1, "source_document_id": "user_material", "source_quote": "提高培养稳定性20%"}],
            },
            metrics=DeepSeekCallMetrics(
                profile="server_v4_flash", model="deepseek-v4-flash", stream=False,
                timeout_seconds=90, done_ms=12,
            ),
        )

    async def aclose(self) -> None:
        self.closed = True


def test_server_model_enriches_claim_extraction_and_records_usage(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    client = RecordingModelClient()
    graph = ContentAssessmentGraph(model_client=client)  # type: ignore[arg-type]

    result = run(_run_after_confirmation(graph, graph.create_initial_state("项目拟构建多器官芯片互作系统，并提高培养稳定性20%。")))

    assert len(client.calls) == 1
    assert client.closed
    assert result.claims[0].assertion_type == "planned"
    assert result.indicators[0].value == "20"
    assert result.output_json["model_usage"]["stages"][0] == {
        "stage": "claim_extraction",
        "status": "completed",
        "profile": "server_v4_flash",
        "model": "deepseek-v4-flash",
        "duration_ms": 12,
    }


def test_report_mode_recommendation_and_artifact_detection() -> None:
    graph = ContentAssessmentGraph()
    sparse = graph.create_initial_state("论文摘要介绍一种新方法。", artifact_type="auto")
    assert sparse.assessment_profile.artifact_type == "paper"
    graph._resolve_report_preferences(sparse)
    assert sparse.report_preferences.recommended_mode == "standard"
    assert sparse.report_preferences.resolved_mode == "standard"
    assert sparse.report_preferences.mode_source == "auto"

    rich = graph.create_initial_state(
        "项目申请书包含技术路线、里程碑和验收指标。",
        artifact_type="auto",
        report_mode="auto",
    )
    rich.uploaded_file_context.total_content_size = 20000
    rich.uploaded_file_context.files = [
        UploadedFileDigest(file_id="D1", name="项目申请书.docx", content_size=20000, content="技术材料" * 5000)
    ]
    rich.claims = [Claim(claim_id=f"C{i}", text=f"技术路线{i}", source_document_id="D1", source_span="技术材料") for i in range(6)]
    graph._resolve_report_preferences(rich)
    assert rich.assessment_profile.artifact_type == "project"
    assert rich.report_preferences.recommended_mode == "deep"


def test_report_section_overrides_hide_optional_appendix_and_preserve_core() -> None:
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("项目技术路线和验收指标。", report_mode="deep")
    state.report_preferences.section_overrides = {
        "evidence_detail": ReportSectionOverride(enabled=False),
        "declarations": ReportSectionOverride(order=1, depth="full"),
        "executive": ReportSectionOverride(enabled=False),
    }
    specs = graph._build_report_section_specs(state)
    keys = [item.section_key for item in specs]
    assert "evidence_detail" not in keys
    assert "executive" in keys
    assert keys[0] == "declarations"
    assert next(item for item in specs if item.section_key == "declarations").depth == "full"
    # Production ignores manual mode overrides; automatic recommendation keeps
    # this sparse sample in the standard/expanded layout.
    assert next(item for item in specs if item.section_key == "executive").depth == "expanded"
    assert {"executive", "process", "technical", "landscape", "claims", "comparison", "assessment", "market", "roadmap", "conclusion", "references"}.issubset(keys)


def test_sparse_report_keeps_exploratory_status_without_padding(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("拟构建一个微流控芯片。", report_mode="deep")
    result = run(_run_after_confirmation(graph, state))
    assert result.quality_gate.report_eligibility == "blocked"
    assert result.report_plan.report_type == "探索性评估报告"
    assert result.report_metrics.page_target_status == "pending"
    assert result.report_metrics.actual_page_count is None
    assert result.report_snapshot is None
    assert not result.output_markdown
    assert not result.confirmation.needed
    assert result.output_json["generation_recovery"]["reason"] == "report_sources"
