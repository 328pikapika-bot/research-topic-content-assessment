import asyncio
import copy

import pytest

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment import source_recovery as recovery
from app.agents.content_assessment.schemas import Claim, ContentAssessmentState, EvidenceRecord
from app.agents.content_assessment.scholarly_report import selected_sources, publication_gaps
from app.common.retrieval.client import LiteratureSearchClient
from app.common.retrieval.schemas import SearchRequest, SearchResponse, StandardPaper


def state_with_gap():
    state = ContentAssessmentState(raw_user_input="比较肝脏芯片灌流，并梳理车险服务流程。")
    state.nine_fields.research_direction = "车辆保险服务"
    state.claims = [
        Claim(claim_id="C1", text="liver chip perfusion with flow monitoring", searchable_terms=["liver", "perfusion"]),
        Claim(claim_id="C2", text="围绕车险投保、承保、理赔和续保服务建立统一操作流程。",
              searchable_terms=["车险", "理赔", "服务流程"], claim_fields={"method": "SOP"}),
    ]
    state.evidence_records = [EvidenceRecord(evidence_id="E1", source_type="literature", title="Liver chip perfusion",
        abstract="Liver chip perfusion uses flow monitoring.")]
    state.output_json["reader_point_evaluations"] = {"C1": [{"text": "原有已校核评价", "evidence_ids": ["E1"]}]}
    return state


def fake_search(monkeypatch, response):
    calls = []
    class Client:
        def search(self, request):
            calls.append(request)
            return response
    monkeypatch.setattr(recovery, "LITERATURE_SEARCH_API_URL", "https://gateway.test")
    monkeypatch.setattr(recovery, "LiteratureSearchClient", Client)
    return calls


def test_sop_is_not_an_exclusive_named_method_and_irrelevant_studies_stay_excluded():
    state = state_with_gap()
    state.evidence_records += [
        EvidenceRecord(evidence_id="insurance", source_type="literature", title="车险理赔服务流程优化研究",
                       abstract="分析车险理赔服务流程，明确岗位动作和承保理赔办理时限。"),
        EvidenceRecord(evidence_id="noise", source_type="literature", title="SOP在电力设备检测中的应用",
                       abstract="采用SOP检测电力设备电压，记录电力系统运行参数。"),
    ]
    assert [e.evidence_id for e in selected_sources(state, state.claims[1])] == ["insurance"]


def test_targeted_search_recovers_source_without_erasing_saved_analysis(monkeypatch):
    state = state_with_gap()
    old = copy.deepcopy(state.output_json["reader_point_evaluations"])
    calls = fake_search(monkeypatch, SearchResponse(success=True, papers=[StandardPaper(
        paper_id="insurance", title="车险理赔服务流程优化研究", abstract="梳理车险理赔服务流程与岗位时限，开展服务质量评价。")]))
    assert not asyncio.run(recovery.recover_point_sources(ContentAssessmentGraph(), state))
    assert len(calls) == 1
    assert state.output_json["point_source_recovery"]["C2"]["status"] == "recovered"
    assert selected_sources(state, state.claims[1])[0].abstract
    assert state.output_json["reader_point_evaluations"] == old
    assert all(request.context["claim_id"] == "C2" for request in calls)


def test_empty_results_are_an_explicit_local_limitation_and_are_reused(monkeypatch):
    state = state_with_gap()
    calls = fake_search(monkeypatch, SearchResponse(success=True, papers=[]))
    assert not asyncio.run(recovery.recover_point_sources(ContentAssessmentGraph(), state))
    assert len(calls) == 2 and recovery.source_limited(state, state.claims[1])
    assert not asyncio.run(recovery.recover_point_sources(ContentAssessmentGraph(), state))
    assert len(calls) == 2
    draft = recovery.limited_point_draft(state, state.claims[1])
    assert all(not row["evidence_ids"] for rows in draft.values() for row in rows)
    assert "暂不能" in draft["judgement"][0]["text"]
    state.output_json["scholarly_drafts"] = {
        "C1": {key: [{"text": "已完成评议", "evidence_ids": ["E1"]}] for key in draft}, "C2": draft}
    assert not publication_gaps(state)
    state.claims[1].text += "新增客户分层研究。"
    assert not recovery.source_limited(state, state.claims[1])
    assert publication_gaps(state)


def test_service_failure_or_globally_missing_evidence_is_not_marked_as_local_limitation(monkeypatch):
    state = state_with_gap()
    calls = fake_search(monkeypatch, SearchResponse(success=False, error="network timeout"))
    assert [c.claim_id for c in asyncio.run(recovery.recover_point_sources(ContentAssessmentGraph(), state))] == ["C2"]
    assert len(calls) == 2 and not recovery.source_limited(state, state.claims[1])
    state = state_with_gap()
    state.claims = state.claims[1:]
    state.evidence_records = []
    fake_search(monkeypatch, SearchResponse(success=True, papers=[]))
    assert asyncio.run(recovery.recover_point_sources(ContentAssessmentGraph(), state))
    assert not recovery.source_limited(state, state.claims[0])


def test_limited_scope_remains_explicit_in_published_report(monkeypatch):
    from app.agents.content_assessment.research_outline import compose_research_report
    state = state_with_gap()
    fake_search(monkeypatch, SearchResponse(success=True, papers=[]))
    asyncio.run(recovery.recover_point_sources(ContentAssessmentGraph(), state))
    state.output_json["scholarly_drafts"] = {"C2": recovery.limited_point_draft(state, state.claims[1])}
    state.output_json["report_evidence_limitations"] = {"claim_ids": ["C2"],
        "message": "该项缺少直接可比文献，仅保留材料说明。"}
    sections = compose_research_report(state)
    scope = next(section for section in sections if section.section_key == "scope")
    assert any("仅保留材料说明" in block.text for block in scope.blocks)
    judgement = next(section for section in sections if section.section_key == "judgement")
    assert any("暂不能" in block.text for block in judgement.blocks)
    assert all(not block.evidence_ids for block in judgement.blocks if block.claim_id == "C2")


def test_report_repairs_only_unfinished_chapters_before_failing_the_task(monkeypatch):
    from app.agents.content_assessment import scholarly_report, report_style, report_methods
    state = state_with_gap()
    state.claims = state.claims[:1]
    calls = []
    kept = {"text": "已完成的肝脏芯片灌流比较。", "evidence_ids": ["E1"]}
    async def draft(graph, current):
        calls.append(1)
        if len(calls) == 1:
            current.output_json.update(scholarly_drafts={"C1": {
                "progress": [kept], "comparison": [kept], "judgement": [kept], "evaluation": []}},
                scholarly_summary=[kept], scholarly_conclusion=[kept])
        else:
            assert current.output_json["scholarly_drafts"]["C1"]["comparison"] == [kept]
            current.output_json["scholarly_drafts"]["C1"]["evaluation"] = [kept]
    async def done(*args):
        return None
    monkeypatch.setattr(scholarly_report, "draft", draft)
    monkeypatch.setattr(report_style, "polish_point_analysis", done)
    monkeypatch.setattr(report_methods, "draft_methods", done)
    monkeypatch.setattr(report_methods, "methods_complete", lambda state: True)
    graph = ContentAssessmentGraph()
    published = []
    monkeypatch.setattr(graph, "_report", lambda state: published.append(True))
    asyncio.run(graph._report_stage(state))
    assert len(calls) == 2 and published == [True]
    assert state.current_stage != "failed"
    assert state.output_json["report_drafting_recoveries"][0]["point_gaps"]


def test_report_chapter_repair_stops_after_a_bounded_number_of_attempts(monkeypatch):
    from app.agents.content_assessment import scholarly_report
    state = state_with_gap()
    state.claims = state.claims[:1]
    calls = []
    async def unavailable(*args):
        calls.append(1)
    monkeypatch.setattr(scholarly_report, "draft", unavailable)
    asyncio.run(ContentAssessmentGraph()._report_stage(state))
    assert len(calls) == 3
    assert state.current_stage == "failed" and state.report_snapshot is None
    assert state.output_json["generation_recovery"]["stage"] == "report"


@pytest.mark.parametrize("message,success", [("查询结果为空", True), ("权限不足", False)])
def test_gateway_empty_result_is_distinct_from_a_service_error(monkeypatch, message, success):
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"code": 0, "msg": message}
    monkeypatch.setattr("app.common.retrieval.client.requests.post", lambda *args, **kwargs: Response())
    result = LiteratureSearchClient(api_url="https://gateway.test").search(SearchRequest(query="保险服务流程"))
    assert result.success is success
    assert not result.papers
    assert bool(result.error) is not success
