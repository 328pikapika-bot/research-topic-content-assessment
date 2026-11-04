"""Automatic publication, bounded repair and account-scoped recovery contracts."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import ContentAssessmentState, EvidenceRecord, QueryPlan
from app.agents.content_assessment.recovery import interrupt_generation, retry_stage
from app.agents.content_assessment.scholarly_report import selected_sources, validated_paragraphs
from app.api import routes
from app.common.retrieval.schemas import SearchResponse, StandardPaper
from app.services.nine_fields_preview import preview_nine_fields
from test_assessment_input_recognition import current_cases
from test_homepage_priority import api, repository, complete_fields
from test_point_evaluation import material, paragraphs


def test_all_six_homepage_cases_pass_http_preview_and_start_without_extra_fields(api, monkeypatch):
    client, app = api
    from app.services import nine_fields_preview as preview_module
    monkeypatch.setattr(preview_module, 'DeepSeekClient', lambda: pytest.fail('Complete cases must not invoke extraction models'))
    for case in current_cases():
        # Separate accounts avoid confusing quota limits with input validation.
        headers = {'X-User-Scope': 'case-' + case['id']}
        preview = client.post('/v1/content-assessment/preview-nine-fields', headers=headers,
            json={'user_input': case['input'], 'uploaded_files': [], 'artifact_type': 'auto'})
        assert preview.status_code == 200, (case['id'], preview.text)
        result = preview.json()
        assert result['validation_version'] == 'assessment-input-semantic-v2'
        assert result['missing_fields'] == [], case['id']
        created = client.post('/v1/content-assessment/run', headers=headers,
            json={'user_input': case['input'], 'scenario_id': case['id'], 'uploaded_files': [],
                  'artifact_type': result['resolved_artifact_type'], 'nine_fields': result['nine_fields']})
        assert created.status_code == 200, (case['id'], created.text)
        state = created.json()['state']
        assert not state['confirmation']['needed'], case['id']
        assert state['nine_fields']['timeline'] == '', case['id']


@pytest.mark.parametrize("failed", [False, True])
def test_incomplete_start_is_rejected_before_creating_task_and_releases_slot(api, repository, monkeypatch, failed):
    client, app = api
    async def preview(*args):
        return {"nine_fields": {}, "resolved_artifact_type": "paper", "extraction_failed": failed}
    monkeypatch.setattr(routes, "preview_nine_fields", preview)
    response = client.post("/v1/content-assessment/run", headers={"X-User-Scope": "alice"},
                           json={"user_input": "请评估这篇论文"})
    assert response.status_code == (503 if failed else 422)
    assert response.json()["detail"]["code"] == ("recognition_failed" if failed else "assessment_input_incomplete")
    assert asyncio.run(repository.statistics("alice"))["total"] == 0
    assert asyncio.run(app.state.task_manager.rate_limiter.usage("alice"))["occupied"] == 0
    app.state.task_manager.start_pipeline.assert_not_awaited()


def test_complete_start_does_not_repeat_recognition(api, monkeypatch):
    client, _ = api
    async def forbidden(*args):
        pytest.fail("Confirmed complete input must not call recognition again")
    monkeypatch.setattr(routes, "preview_nine_fields", forbidden)
    response = client.post("/v1/content-assessment/run", headers={"X-User-Scope": "alice"},
        json={"user_input": "完整论文", "artifact_type": "paper", "nine_fields": complete_fields().model_dump()})
    assert response.status_code == 200
    assert not response.json()["state"]["confirmation"]["needed"]


@pytest.mark.parametrize("legacy", [False, True])
def test_retry_keeps_same_task_saved_analysis_and_account_scope(api, repository, legacy):
    client, app = api
    state = material()
    state.output_json["reader_point_evaluations"] = {"C1": paragraphs()[:2]}
    if legacy:
        state.current_stage = "awaiting_confirmation"
        state.resume_from_stage = "assessment"
        state.confirmation.needed = True
        state.confirmation.reason = "assessment_analysis"
    else:
        interrupt_generation(state, "assessment", "assessment_analysis", "生成中断")
    asyncio.run(repository.create({"task_id": "saved", "user_scope": "alice", "task_type": "创新性评估",
        "status": state.current_stage, "state": state.model_dump()}))
    url = "/v1/content-assessment/tasks/saved/confirmation"
    body = {"selected_option": "retry_generation"}
    assert client.post(url, headers={"X-User-Scope": "bob"}, json=body).status_code == 404
    result = client.post(url, headers={"X-User-Scope": "alice"}, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["run_id"] == "saved"
    assert result.json()["state"]["output_json"]["reader_point_evaluations"]["C1"] == paragraphs()[:2]
    assert not result.json()["state"]["confirmation"]["needed"]
    assert asyncio.run(repository.statistics("alice"))["total"] == 1
    assert client.post(url, headers={"X-User-Scope": "alice"}, json=body).status_code == 409
    app.state.task_manager.start_pipeline.assert_awaited_once()


def test_translated_query_skips_generic_prefix_but_retains_subject_filter():
    state = material()
    claim = state.claims[0]
    claim.text = "采用结构化主张拆分及原文句定位研究综述证据链。"
    claim.searchable_terms = ["科学主张验证"]
    state.query_plans = [QueryPlan(claim_id="C1", query="scientific claim verification PICO")]
    state.evidence_records = [EvidenceRecord(evidence_id="claim", source_type="literature",
        title="Retrieval augmented scientific claim verification", abstract="Scientific claim verification uses PICO and abstracts."),
        EvidenceRecord(evidence_id="soil", source_type="literature", title="Soil verification procedures",
            abstract="Soil monitoring evaluates scientific methods.")]
    assert [row.evidence_id for row in selected_sources(state, claim)] == ["claim"]
    interrupt_generation(state, "retrieval", "report_sources", "中断")
    assert retry_stage(state) == "assessment"


def test_validation_feedback_identifies_failed_clause_instead_of_generic_rejection():
    from app.agents.content_assessment.scholarly_report import point_packet
    state = material()
    feedback = []
    row = {**paragraphs()[0], "text": "该方法在现有文献中尚未见系统整合，属于首次验证。"}
    assert not validated_paragraphs([row], point_packet(state, state.claims[0]), feedback)
    assert "现有文献中尚未见" in feedback[0]["reason"]
    assert "摘要未提及" in feedback[0]["reason"]


def test_export_interruption_is_not_complete_and_retry_keeps_frozen_content(monkeypatch):
    from test_scholarly_reports import sample
    import app.services.export_service as exports
    state, graph = sample(), ContentAssessmentGraph()
    state.stage_trace = graph.create_initial_state(state.raw_user_input).stage_trace
    row = {"text": "材料计划比较肝脏芯片灌流条件，研究记录中的流量监测提供可追溯的比较来源。", "evidence_ids": ["E1"], "kind": "analysis"}
    state.output_json["scholarly_drafts"] = {"C1": {key: [row.copy()] for key in ("progress", "comparison", "judgement", "evaluation")}}
    state.output_json["scholarly_summary"] = [row.copy()]
    state.output_json["scholarly_conclusion"] = [row.copy()]
    graph._report(state)
    snapshot = state.report_snapshot.model_dump()
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("REPORT_PREFLIGHT_ENABLED", "true")
    monkeypatch.setattr(exports, "prepare_exports", lambda s: None)
    failed = asyncio.run(graph._make_stage_node("report")({"payload": state.model_dump()}))
    failed = ContentAssessmentState.model_validate(failed["payload"])
    assert failed.current_stage == "failed"
    assert not failed.confirmation.needed
    assert failed.output_json.get("report_publication", {}).get("status") != "published"
    assert retry_stage(failed) == "report"
    assert failed.report_snapshot.model_dump() == snapshot
    async def forbidden(*args, **kwargs):
        pytest.fail("Export retry must not regenerate accepted prose")
    monkeypatch.setattr(graph, "_request_model_json", forbidden)
    monkeypatch.setattr(exports, "prepare_exports", lambda s: (b"docx", b"pdf"))
    retried = asyncio.run(graph._make_stage_node("report")({"payload": failed.model_dump()}))
    retried = ContentAssessmentState.model_validate(retried["payload"])
    assert retried.output_json["report_publication"]["status"] == "published"
    assert retried.report_snapshot.model_dump() == snapshot
    assert next(s for s in retried.stage_trace if s.stage_key == "report").status == "completed"


def test_six_homepage_cases_run_through_real_graph_checks_with_fixed_external_responses(monkeypatch):
    """Only search/model boundaries are fixed; no stage, validator or publication bypass."""
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "http://fixture.invalid")
    for case in current_cases():
        preview = asyncio.run(preview_nine_fields(case["input"], [], "alice"))
        graph = ContentAssessmentGraph()
        state = graph.create_initial_state(case["input"], artifact_type=preview["resolved_artifact_type"], scenario_id=case["id"])
        state.nine_fields = state.nine_fields.model_validate(preview["nine_fields"])
        state.nine_fields_confirmed = True
        source_text = case["input"]
        class Search:
            def search(self, request):
                papers = [StandardPaper(paper_id=f"fixture-{i}", title=f"{state.nine_fields.research_direction}：研究设计{i}",
                    year=2024, abstract=source_text, source_url=f"https://example.org/papers/{i}") for i in range(24)]
                return SearchResponse(success=True, total=len(papers), papers=papers)
        async def fulltext(*args, **kwargs):
            return []
        async def request(_state, **kwargs):
            prompt = kwargs["user_prompt"]
            if kwargs.get("stage") == "claim_extraction":
                claim = _state.claims[0]
                return {"claims": [{
                    "source_document_id": claim.source_document_id or "user_material",
                    "source_quote": claim.source_span,
                    "text": claim.text,
                    "claim_type": claim.claim_type,
                    "assertion_type": claim.assertion_type,
                    "searchable_terms": claim.searchable_terms,
                }]}
            if "逐段核对下面评议" in prompt:
                return {"checked": True, "replacements": []}
            if "整理以下同一研究要点的完整评议" in prompt:
                return json.loads(prompt.rsplit("\n", 1)[1])["analysis"]
            if "将以下已校核逐项评议综合" in prompt:
                packet = json.loads(prompt.rsplit("\n", 1)[1])
                ids = [packet["source_material"]["literature"][0]["id"]]
                return {key: [{"text": text, "evidence_ids": ids, "kind": "analysis"}] for key, text in [
                    ("summary", "本材料的研究定位以明确的问题设置为基础，相关文献提供了方法设计及验证条件的比较来源，评价围绕材料提出的研究方案展开。"),
                    ("conclusion", "综合各研究要点，材料将技术路线与应用问题相衔接，其研究贡献可以通过具体研究对象、方法选择及验证设计说明，计划目标保持待开展的性质。")]
                    if key in packet["missing_units"]}
            if "撰写科研评估报告第四章" in prompt:
                packet = json.loads(prompt.rsplit("\n", 1)[1])
                source = packet["source_material"]
                requested = packet["missing_units"]
                mappings = []
                literature = {item["id"]: item for item in source["literature"]}
                for point in source["point"]["research_points"]:
                    ids = point["representative_ids"]
                    if ids:
                        mappings.append({"text": point["name"] + "以材料所述研究内容为对象，代表来源《"
                            + literature[ids[0]]["title"] + "》提供了相关研究方法的可读记录。",
                            "evidence_ids": ids[:1]})
                body = {
                    "selection": [{"text": "检索按材料研究要点组织，来源和年份范围沿用已保存的查询记录；"
                                   "查询候选与正文引用分别统计。", "evidence_ids": []}],
                    "mapping": mappings,
                    "comparability": [{"text": "材料中的研究对象与验证安排用于界定比较范围，"
                                       "计划目标不写成已完成结果。", "evidence_ids": []}],
                    "quality": [{"text": "当前代表资料按实际可读范围使用；摘要信息只用于其记载的研究内容。",
                                 "evidence_ids": []}],
                    "limits": [{"text": "本次方法的判断范围限于材料中列明的研究对象及对应可读来源，"
                                "性能差异需要一致的验证条件。", "evidence_ids": []}],
                }
                return {key: body[key] for key in requested}
            if "返回 {evaluations" in prompt or "逐项研究分析" in prompt:
                packet = json.loads(prompt.rsplit("\n", 1)[1])
                ids = [row["id"] for row in packet["literature"][:2]]
                name = packet["point"]["name"]
                if "返回 {evaluations" in prompt:
                    return {"evaluations": [{"text": name + text, "dimension": dim, "kind": "analysis", "evidence_ids": ids}
                        for dim, text in [
                        ("innovation", "的研究贡献体现在问题设置与方法设计的衔接。材料中的具体方案与来源中记载的方法构成可追溯的比较对象，评价保持拟开展研究的性质。"),
                        ("frontier", "所关注的问题与相关文献的应用对象相联系。研究设计将方法选择置于明确的使用场景中，可以从研究问题及验证需求解释其前沿关联。"),
                        ("advancedness", "的技术特点可以从机制和验证设计加以评价。材料安排的研究内容为考察方法适用范围提供具体对象，所引来源用于说明对应的设计条件。") ]]}
                return {key: [{"text": name + text, "evidence_ids": ids, "kind": "analysis", "dimension": dim}]
                    for key, dim, text in [
                        ("progress", "representatives", "的在先研究围绕对应研究对象组织方法与观察记录，来源中的研究设计为理解其技术背景提供参照。"),
                        ("comparison", "points", "在材料方案中对应明确的研究内容，比较围绕方法、观察对象及实验设计展开，所引来源的记载界定了论述的适用范围。"),
                        ("judgement", "value", "的研究意义在于将具体问题与可检查的验证过程联系起来，其预期价值属于方案设计层面的研究判断。"),
                        ("evaluation", "design", "的分类评价以材料提供的技术内容为对象，研究方法及应用衔接可以分别通过实验记录与交付内容进行核验。") ]}
            return {}
        monkeypatch.setattr(graph_module, "LiteratureSearchClient", Search)
        monkeypatch.setattr(graph.fulltext_client, "search_intents", fulltext)
        monkeypatch.setattr(graph, "_request_model_json", request)
        updates = []
        async def emit(s):
            updates.append((s.current_stage, s.confirmation.needed))
        result = asyncio.run(graph.run(state, on_stage_update=emit))
        assert result.current_stage == "completed", (case["id"], result.output_json.get("generation_recovery"))
        assert not any(needed for _, needed in updates)
        assert result.output_json["report_publication"]["status"] == "published"
        assert result.report_snapshot and result.output_markdown
        assert all(item.status == "completed" for item in result.stage_trace)


def test_cleanup_beijing_boundary_and_account_isolation():
    path = Path(__file__).resolve().parents[2] / "scripts/cleanup_admin_tasks_20261015.py"
    spec = importlib.util.spec_from_file_location("cleanup_scope", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = {"user_scope": "platform_admin", "task_type": "创新性评估", "status": "completed"}
    assert module.eligible({**base, "created_at": "2026-10-14T15:59:59Z"})
    assert not module.eligible({**base, "created_at": "2026-10-14T16:00:00Z"})
    assert module.eligible({**base, "created_at": "2026-10-15T08:00:00+08:00", "status": "awaiting_confirmation"})
    assert not module.eligible({**base, "created_at": "2026-10-01T08:00:00+08:00", "user_scope": "other"})
    assert not module.eligible({**base, "created_at": "2026-10-01T08:00:00+08:00", "task_type": "other"})
