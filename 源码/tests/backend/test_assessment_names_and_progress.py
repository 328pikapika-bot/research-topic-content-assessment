"""Focused regressions: formal report names and readable progress during slow search."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.naming import research_name, report_title, formalize_report_name
from app.agents.content_assessment.schemas import ContentAssessmentState, Claim, ReportSnapshot, ReportSectionSpec, ReportBlock
from app.api.routes import _assessment_reader_task
from app.services.export_service import ensure_snapshot

NAME = "科研文献证据链验证方法"
RAW = f"我要评估的是：论文\n材料名称：{NAME}\n研究方向：科学信息学\n研究内容：文献比较与证据定位\n关键方法：混合检索与重排"


@pytest.mark.parametrize("kind,label", [("paper", "论文"), ("project", "项目"), ("guideline", "指南")])
def test_named_material_replaces_type_instruction_at_creation(kind, label):
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state(RAW.replace("论文", label), artifact_type=kind,
                                       assessment_title=f"我要评估的是：{label}")
    assert state.assessment_profile.title == NAME
    assert state.assessment_profile.assessment_object == NAME
    assert graph._report_title(state) == f"{NAME}创新性评估报告"
    assert state.raw_user_input.startswith(f"我要评估的是：{label}")


def test_valid_organized_title_wins_and_reference_names_are_not_objects():
    assert research_name(RAW, candidates=("科研文献证据关联与定位方法",)) == "科研文献证据关联与定位方法"
    assert research_name("我要评估的是：论文", documents=[{"role": "REFERENCE", "name": "参考指南.pdf"}]) == "创新性评估任务"
    assert research_name("我要评估的是：论文", documents=[{"role": "PROJECT", "name": f"{NAME}.pdf"}]) == NAME
    assert research_name("请评估这篇论文，比较其研究方法") == "创新性评估任务"


def broken_report():
    bad = "我要评估的是：论文"
    state = ContentAssessmentState(raw_user_input=RAW)
    state.assessment_profile.title = state.assessment_profile.assessment_object = bad
    state.assessment_profile.artifact_type = "paper"
    state.report_plan.report_title = bad + "内容评估报告"
    sections = [ReportSectionSpec(section_key="executive", title="摘要", blocks=[ReportBlock(text=f"本报告对“{bad}”开展论文内容评估。")]),
                ReportSectionSpec(section_key="scope", title="评估对象", blocks=[ReportBlock(text=bad)]),
                ReportSectionSpec(section_key="comparison", title="研究比较", blocks=[ReportBlock(text="混合检索保留术语匹配并补充语义召回 [7]。", evidence_ids=["E-7"])])]
    state.report_snapshot = ReportSnapshot(snapshot_id="original-frozen", generated_at="2026-10-15", sections=sections,
        citation_numbers={"E-7": 7}, audit_data={"evidence_records": [{"evidence_id": "E-7", "title": "研究文献", "source_type": "literature"}]},
        cover_metadata={"title": state.report_plan.report_title, "assessment_object": bad},
        render_context={"assessment_profile": state.assessment_profile.model_dump(), "report_plan": state.report_plan.model_dump()})
    return state


def test_read_and_export_repair_only_bad_name_without_writing_frozen_judgments():
    state = broken_report()
    original = state.report_snapshot.model_dump()
    task = {"task_id": "recent", "raw_user_input": RAW, "state": state.model_dump()}
    saved = deepcopy(task)
    view = _assessment_reader_task(task)
    assert task == saved
    assert view["title"] == NAME
    assert view["state"]["report_snapshot"]["cover_metadata"]["title"] == f"{NAME}创新性评估报告"
    ensure_snapshot(state)
    assert report_title(state) == f"{NAME}创新性评估报告"
    assert state.report_plan.report_title == report_title(state)
    assert state.report_snapshot.source_snapshot == original
    assert state.report_snapshot.sections[2].model_dump() == original["sections"][2]
    assert state.report_snapshot.citation_numbers == original["citation_numbers"]
    assert state.report_snapshot.audit_data == original["audit_data"]
    text = "\n".join(b.text for s in state.report_snapshot.sections for b in s.blocks)
    assert "我要评估" not in text
    assert f"评估对象为“{NAME}”，材料类型为论文。" in text
    once = state.report_snapshot.model_dump()
    ensure_snapshot(state)
    assert state.report_snapshot.model_dump() == once


def test_valid_historical_report_keeps_frozen_name_despite_changed_input():
    state = broken_report()
    state.report_snapshot.cover_metadata["title"] = "原研究名称内容评估报告"
    before = state.model_dump()
    formalize_report_name(state)
    assert state.model_dump() == before
    assert report_title(state) == "原研究名称内容评估报告"


def test_material_completion_remains_readable_while_eighteen_searches_wait(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
        release = threading.Event()
        all_workers_busy = threading.Event()
        lock = threading.Lock()
        started = 0

        class SlowGateway:
            def search(self, request):
                nonlocal started
                with lock:
                    started += 1
                    if started == 12:
                        all_workers_busy.set()
                release.wait(5)
                raise RuntimeError("controlled slow gateway")

        monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "https://controlled.invalid")
        monkeypatch.setattr(graph_module, "LiteratureSearchClient", SlowGateway)
        graph = ContentAssessmentGraph()
        graph._request_model_json = AsyncMock(return_value=None)
        graph.fulltext_client.search_intents = AsyncMock(return_value=[])
        state = graph.create_initial_state(RAW)
        latest = {}

        async def publish(current):
            # Mongo persistence and reads use the ordinary asyncio executor.
            await asyncio.to_thread(latest.update, current.model_dump())

        graph._on_stage_update = publish

        async def extract(current):
            current.claims = [Claim(claim_id=f"C-{i}", text=f"研究要点{i}的混合检索方法", searchable_terms=[f"method{i}"]) for i in range(3)]

        graph._claim_extraction_stage = extract
        envelope = {"payload": state.model_dump()}
        envelope = await graph._make_stage_node("document_intake")(envelope)
        envelope = await graph._make_stage_node("claim_extraction")(envelope)
        job = asyncio.create_task(graph._make_stage_node("retrieval")(envelope))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(all_workers_busy.wait, 2), 3)
            visible = await asyncio.wait_for(asyncio.to_thread(deepcopy, latest), 1)
            traces = {item["stage_key"]: item for item in visible["stage_trace"]}
            assert traces["document_intake"]["status"] == "completed"
            assert traces["claim_extraction"]["status"] == "completed"
            assert traces["retrieval"]["status"] == "running"
            assert len(visible["claims"]) == 3
            assert not release.is_set()  # No search had to finish to read the material result.
            assert traces["document_intake"]["duration_seconds"] < 1
        finally:
            release.set()
            await asyncio.wait_for(job, 3)
    asyncio.run(run())
