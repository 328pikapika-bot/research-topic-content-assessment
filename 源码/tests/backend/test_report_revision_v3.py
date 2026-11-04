import asyncio
import io
import json
import zipfile

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.material_extraction import explicit_claims, material_query_plans, relevant, enrich_indicators
from app.agents.content_assessment.report_composition import compose, build_specs, freeze_snapshot, render_citations, condense, refresh_section
from app.agents.content_assessment.schemas import Claim, ContentAssessmentState, EvidenceRecord, UploadedFileDigest, ClaimEvidenceLink, ResearchNineFields
from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository


def material_state():
    s = ContentAssessmentState(raw_user_input="要求：输出中文；语言偏好：中文；每条主张必须画图。")
    lines = ["主张 | 主张原文 | 核心指标 | 项目目标值 | 对照要求"]
    lines += [f"C{i} | 构建第{i}种微流控肝脏芯片并验证连续灌流稳定性。 | 流量漂移 | 漂移≤{i}% | 同器官流路" for i in range(1, 7)]
    lines += ["", "QueryID | QueryRole | ClaimIDs | QueryText", "Q1 | 系统或技术路线先例 | C1;C2 | liver chip perfusion"]
    text = "\n".join(lines)
    s.uploaded_file_context.files = [UploadedFileDigest(file_id="file1", name="材料.docx", content=text)]
    s.nine_fields = ResearchNineFields(research_direction='微流控', research_content='肝脏芯片灌流', key_technologies=['灌流控制'])
    s.nine_fields_confirmed = True
    s.report_preferences.artifact_type = s.report_preferences.resolved_artifact_type = 'project'
    return s


def test_material_claims_ignore_instructions_and_query_ids_survive():
    s = material_state()
    asyncio.run(ContentAssessmentGraph()._claim_extraction_stage(s))
    assert len(s.claims) == 6
    assert not s.confirmation.needed
    for c in s.claims:
        assert c.source_document_id == "file1"
        assert s.uploaded_file_context.files[0].content[c.source_char_start:c.source_char_end] == c.source_span
        assert "要求" not in c.text
    assert material_query_plans(s)[0].claim_ids == ["C-001", "C-002"]


def test_wrong_anchor_and_unanchored_upload_pause(monkeypatch):
    s = ContentAssessmentState(raw_user_input="要求输出六条主张")
    s.uploaded_file_context.files = [UploadedFileDigest(file_id="file1", name="材料.docx", content="本研究构建肝脏芯片。")]
    g = ContentAssessmentGraph()
    assert not g._apply_model_claim_extraction(s, {"claims": [{"text": "首次实现", "source_document_id": "file1", "source_quote": "不存在的原文"}]})
    async def fail(*args, **kwargs):
        return None
    monkeypatch.setattr(g, "_request_model_json", fail)
    asyncio.run(g._claim_extraction_stage(s))
    assert not s.claims
    assert s.confirmation.reason in {"claim_extraction", "missing_fields"}
    assert s.confirmation.needed
    assert s.quality_gate.report_eligibility == "blocked"


def test_partial_chunk_success_does_not_silently_skip_rest(monkeypatch):
    s = ContentAssessmentState(raw_user_input="评估")
    quote = "构建肝脏芯片并验证灌流。"
    s.uploaded_file_context.files = [UploadedFileDigest(file_id="file1", name="材料", content=quote + "\n" + "材料正文。" * 1500)]
    g = ContentAssessmentGraph()
    calls = []
    async def partial(*args, **kwargs):
        calls.append(1)
        return {"claims": [{"text": quote, "source_document_id": "file1", "source_quote": quote}]} if len(calls) == 1 else None
    monkeypatch.setattr(g, "_request_model_json", partial)
    asyncio.run(g._claim_extraction_stage(s))
    assert s.claims and s.confirmation.needed
    assert s.confirmation.reason in {"claim_extraction", "missing_fields"}


def test_irrelevant_evidence_is_not_nearest_prior_or_deep_recommendation():
    s = material_state()
    g = ContentAssessmentGraph()
    g._claim_extraction(s)
    s.evidence_records = [EvidenceRecord(evidence_id=f"E{i}", title="银河星系测量", abstract="恒星轨道和引力波观测", source_type="literature") for i in range(60)]
    g._comparison(s)
    assert not s.claim_evidence_links
    assert all(not d.nearest_prior_art_id for d in s.claim_assessments)
    assert g._recommended_mode(s) == "standard"


def test_citation_identity_dedup_and_contrary_evidence_kept():
    s = material_state(); s.claims = explicit_claims(s)
    s.evidence_records = [EvidenceRecord(evidence_id=eid, title="微流控肝脏芯片", abstract="肝脏芯片结果", doi=doi, source_type="literature")
        for eid, doi in [("E1", "10.1/a"), ("E10", "https://doi.org/10.1/a")]]
    s.claim_evidence_links = [ClaimEvidenceLink(claim_id=s.claims[0].claim_id, evidence_id="E10", relation="contradicts", difference="实际出现失效，不能删去", uncertainty="需核验全文")]
    s.report_plan.section_specs = build_specs(s); compose(s, s.report_plan.section_specs); freeze_snapshot(s)
    refs = next(x for x in s.report_plan.section_specs if x.section_key == "references")
    assert len(refs.blocks) == 1
    assert "实际出现失效" in next(x.content for x in s.report_plan.section_specs if x.section_key == "comparison_novelty")
    assert render_citations("E1 E10 E100", {"E1": 1, "E10": 2}) == "[1] [2] E100"
    assert s.report_snapshot.sections
    assert s.report_snapshot.audit_data["materials"][0]["content"]


def test_report_metadata_compare_and_set_preserves_unrelated_state():
    async def scenario():
        repo = InMemoryTaskHistoryRepository()
        await repo.create({"task_id": "t", "user_scope": "u", "updated_at": "v1", "state": {"claims": ["original"]}})
        assert await repo.update_report("t", "u", "v1", {"updated_at": "v2", "state.report_metrics": {"actual_page_count": 9}})
        assert not await repo.update_report("t", "u", "v1", {"state.report_metrics": {"actual_page_count": 999}})
        assert not await repo.update_report("t", "other", "v2", {"state.report_metrics": {}})
        final = await repo.get("t", "u")
        assert final["state"] == {"claims": ["original"], "report_metrics": {"actual_page_count": 9}}
    asyncio.run(scenario())


def test_comparison_timeout_is_isolated_and_retry_clears_old_gap(monkeypatch):
    s = material_state()
    s.claims = explicit_claims(s)[:2]
    s.evidence_records = [EvidenceRecord(evidence_id="E1", title="微流控肝脏芯片灌流稳定性", source_type="literature")]
    s.output_json["comparison_gaps"] = [{"claim_id": s.claims[1].claim_id, "reason": "旧超时"}]
    graph = ContentAssessmentGraph()
    calls = []
    async def compare(*args, **kwargs):
        assert graph._model_unavailable is False
        calls.append(1)
        if len(calls) == 1:
            graph._model_unavailable = True
            return None
        return {"decisions": [{"claim_id": s.claims[1].claim_id, "label": "证据不足"}]}
    monkeypatch.setattr(graph, "_request_model_json", compare)
    asyncio.run(graph._comparison_stage(s))
    assert len(calls) == 2
    assert [x["claim_id"] for x in s.output_json["comparison_gaps"]] == [s.claims[0].claim_id]
    assert s.output_json["_model_comparison"]["decisions"][0]["claim_id"] == s.claims[1].claim_id
    assert graph._model_unavailable is False


def test_malformed_comparison_records_gap_without_crashing(monkeypatch):
    s = material_state(); s.claims = explicit_claims(s)[:1]
    s.evidence_records = [EvidenceRecord(evidence_id="E1", title="微流控肝脏芯片", source_type="literature")]
    graph = ContentAssessmentGraph()
    async def invalid(*args, **kwargs):
        return {"decisions": {"claim_id": "wrong"}, "links": None}
    monkeypatch.setattr(graph, "_request_model_json", invalid)
    asyncio.run(graph._comparison_stage(s))
    assert s.output_json["comparison_gaps"]
    assert not s.output_json.get("_model_comparison")


def test_modes_preserve_audit_facts_and_reordering_keeps_core():
    from app.agents.content_assessment.schemas import ReportSectionOverride
    s = material_state(); graph = ContentAssessmentGraph(); graph._claim_extraction(s)
    s.report_preferences.mode = "standard"
    graph._report(s)
    audit = s.report_snapshot.audit_data
    s.report_preferences.mode = "deep"
    s.report_preferences.section_overrides = {
        "comparison": ReportSectionOverride(enabled=False, order=1),
        "declarations": ReportSectionOverride(enabled=False),
    }
    graph._report(s)
    assert s.report_snapshot.audit_data == audit
    comparison = next(x for x in s.report_plan.section_specs if x.section_key == "comparison")
    keys = [x.section_key for x in s.report_plan.section_specs]
    assert keys.index("object") < keys.index("comparison")
    assert "declarations" not in keys
    assert all(c.text in next(x.content for x in s.report_plan.section_specs if x.section_key == "object") for c in s.claims)
    assert s.report_snapshot.template_version == "ContentAssessmentResearchReportV6"


def test_condensation_removes_unused_references_but_retains_counterevidence():
    s = material_state(); s.claims = explicit_claims(s)
    s.report_preferences.resolved_mode = "deep"
    s.evidence_records = [EvidenceRecord(evidence_id=f"E{i}", title=f"微流控肝脏芯片路线{i}",
        abstract=f"芯片路线{i}提供灌流条件观察。", source_type="literature") for i in (1, 2)]
    s.claim_evidence_links = [ClaimEvidenceLink(claim_id=s.claims[0].claim_id, evidence_id="E2",
        relation="contradicts", difference="长期失效反证必须保留")]
    s.report_plan.section_specs = build_specs(s); compose(s, s.report_plan.section_specs); freeze_snapshot(s)
    # A remaining essential landscape observation keeps the section useful.
    from app.agents.content_assessment.schemas import ReportBlock
    landscape = next(x for x in s.report_plan.section_specs if x.section_key == "trends_advice")
    landscape.blocks.append(ReportBlock(text="检索所见路线仍需独立验证。")); refresh_section(landscape)
    condense(s); condense(s)
    assert "E1" not in s.report_snapshot.citation_numbers
    assert "E2" in s.report_snapshot.citation_numbers
    assert "长期失效反证必须保留" in next(x.content for x in s.report_plan.section_specs if x.section_key == "comparison_novelty")
    assert len(s.report_snapshot.audit_data["evidence_records"]) == 2


def test_indicator_acceptance_is_specific_to_named_endpoint():
    s = ContentAssessmentState(raw_user_input="评估肝损伤指标")
    text = "主张 | 主张原文 | 核心指标 | 项目目标值 | 对照要求\nC1 | 测定药物作用下肝损伤。 | ALT、AST、LDH | ALT 较对照升高≥2 倍 | 同药物浓度"
    s.uploaded_file_context.files = [UploadedFileDigest(file_id="F1", name="材料", content=text)]
    s.claims = explicit_claims(s)
    metrics = {x.name:x for x in enrich_indicators(s)}
    assert "2 倍" in metrics["ALT"].acceptance_condition
    assert not metrics["AST"].acceptance_condition
    assert not metrics["LDH"].acceptance_condition
