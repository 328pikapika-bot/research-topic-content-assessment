"""New report method chapter and targeted editorial checks."""
import asyncio
from copy import deepcopy
import io

import fitz
from docx import Document

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.report_methods import METHOD_KEYS, draft_methods, method_packet, methods_complete
from app.agents.content_assessment.report_style import polish_point_analysis, style_targets
from app.agents.content_assessment.scholarly_report import validated_paragraphs
from app.agents.content_assessment.schemas import Claim, ClaimEvidenceLink
from test_research_report_v6 import research_sample


def method_response():
    return {
        "selection": [{"text": "检索围绕灌流条件展开，记录来源为模拟资料库；单次候选记录与正文实际引用分别统计。",
                       "evidence_ids": []}],
        "mapping": [{"text": "灌流条件对应的模拟研究记录了流量监测；另一项研究指出过量流量可能影响灌流。",
                     "evidence_ids": ["E1", "E3"]}],
        "comparability": [{"text": "材料将灌流时长列为计划目标，同流量观测是材料记载的验证方法；"
                           "文献观察与材料目标需要分别解释。", "evidence_ids": ["E1"]}],
        "quality": [{"text": "代表文献目前提供摘要信息；其研究内容用于方法联系与方向性比较，"
                     "不能作为当前材料的实测结果。", "evidence_ids": ["E1"]}],
        "limits": [{"text": "评价适用于肝脏芯片灌流条件这一研究对象，"
                    "比较以材料所述同流量观测和文献记录的流量条件为范围。", "evidence_ids": ["E1"]}],
    }


def test_method_packet_uses_actual_query_and_appendix_evidence():
    state = research_sample("guideline")
    packet = method_packet(state)
    assert packet["material_design"]["search_records"][0]["year_range"] == [2024, 2025]
    assert packet["material_design"]["search_records"][0]["source_scope"] == ["模拟资料库"]
    assert packet["point"]["research_points"][0]["representative_ids"] == ["E1", "E3"]
    assert {source["id"] for source in packet["literature"]} == {"E1", "E3"}
    assert all(source["scope"] == "摘要" for source in packet["literature"])
    assert not validated_paragraphs([{"text": "检索共纳入999篇。", "evidence_ids": []}], packet)
    assert not validated_paragraphs([{"text": "虚构研究得出结论。", "evidence_ids": ["E2"]}], packet)


def test_methods_resume_unreviewed_unit_without_regenerating_accepted_sections(monkeypatch):
    state = research_sample("paper")
    graph = ContentAssessmentGraph()
    calls = []
    unavailable = True

    async def request(_state, **kwargs):
        nonlocal unavailable
        prompt = kwargs["user_prompt"]
        calls.append(prompt)
        if "撰写科研评估报告第四章" in prompt:
            return method_response()
        if "逐段核对下面评议" in prompt:
            if '"key": "quality"' in prompt and unavailable:
                return None
            return {"checked": True, "replacements": []}
        raise AssertionError(prompt[:100])

    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(draft_methods(graph, state))
    assert not methods_complete(state)
    assert state.output_json["scholarly_methods"]["selection"]
    assert state.output_json["scholarly_methods_pending"]["quality"]
    first_generation = sum("撰写科研评估报告第四章" in prompt for prompt in calls)
    unavailable = False
    asyncio.run(draft_methods(graph, state))
    assert methods_complete(state)
    assert sum("撰写科研评估报告第四章" in prompt for prompt in calls) == first_generation
    frozen = deepcopy(state.output_json["scholarly_methods"])
    asyncio.run(draft_methods(graph, state))
    assert state.output_json["scholarly_methods"] == frozen


def test_methods_use_recorded_facts_when_source_review_rejects(monkeypatch):
    state = research_sample("paper")
    graph = ContentAssessmentGraph()

    async def request(_state, **kwargs):
        if "撰写科研评估报告第四章" in kwargs["user_prompt"]:
            return method_response()
        return {"checked": False, "replacements": []}

    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(draft_methods(graph, state))
    assert methods_complete(state)
    assert all(state.output_json["scholarly_methods_completion"][key]["status"] == "fallback"
               for key in METHOD_KEYS)
    assert set(state.output_json["scholarly_methods_source_reviews"]) == set(METHOD_KEYS)
    assert all(state.output_json["scholarly_methods_source_reviews"][key]["failure_kind"] == "source_rejected"
               for key in METHOD_KEYS)
    assert state.report_snapshot is None


def test_multi_point_mapping_requires_each_point_and_its_own_source(monkeypatch):
    state = research_sample("project")
    state.claims.append(Claim(claim_id="C2", display_title="第二项灌流验证",
                              text="拟比较肝脏芯片的灌流流量。", searchable_terms=["liver", "perfusion"]))
    state.claim_evidence_links.append(ClaimEvidenceLink(claim_id="C2", evidence_id="E1", relation="supports"))
    state.output_json["scholarly_drafts"]["C2"] = deepcopy(state.output_json["scholarly_drafts"]["C1"])
    graph = ContentAssessmentGraph()

    async def request(_state, **kwargs):
        if "撰写科研评估报告第四章" in kwargs["user_prompt"]:
            return method_response()
        return {"checked": True, "replacements": []}

    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(draft_methods(graph, state))
    assert methods_complete(state)
    mapping = state.output_json["scholarly_methods"]["mapping"]
    for point in method_packet(state)["point"]["research_points"]:
        if point["representative_ids"]:
            assert any(point["name"] in row["text"] and row["evidence_ids"] == point["representative_ids"]
                       for row in mapping)
    assert state.output_json["scholarly_methods_completion"]["mapping"]["reason"] == "incomplete_mapping"
    assert any("逐项对应" in item["reason"]
               for item in state.output_json["scholarly_methods_completion"]["feedback"]["mapping"])


def test_targeted_style_revision_preserves_citation_and_frozen_history(monkeypatch):
    state = research_sample("guideline")
    rows = state.output_json["scholarly_drafts"]["C1"]["comparison"]
    rows[:] = [
        {"text": "本指南围绕灌流条件组织比较。", "evidence_ids": ["E1"], "dimension": "points"},
        {"text": "本指南拟采用同流量观测方法。", "evidence_ids": ["E1"], "dimension": "points"},
    ]
    assert len(style_targets(state.output_json["scholarly_drafts"])) == 2
    graph = ContentAssessmentGraph()

    async def request(_state, **kwargs):
        if "仅润色下列" in kwargs["user_prompt"]:
            return {"paragraphs": {
                "comparison:0": {"text": "灌流条件比较围绕流量监测展开。", "evidence_ids": ["E1"], "dimension": "points"},
                "comparison:1": {"text": "同流量观测方法拟用于材料中的灌流比较。", "evidence_ids": ["E1"], "dimension": "points"}}}
        return {"checked": True, "replacements": []}

    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(polish_point_analysis(graph, state))
    assert rows[0]["text"].startswith("灌流条件")
    assert rows[0]["evidence_ids"] == ["E1"]
    assert state.output_json["scholarly_style_checks"]
    from app.agents.content_assessment.report_composition import freeze_snapshot
    graph._report(state)
    before = deepcopy(state.report_snapshot.model_dump())
    asyncio.run(polish_point_analysis(graph, state))
    assert state.report_snapshot.model_dump() == before


def test_methods_use_the_same_frozen_text_in_preview_and_exports():
    from app.services.export_service import build_docx, build_html, build_markdown, build_pdf

    state = research_sample("guideline")
    state.output_json["scholarly_methods"] = method_response()
    ContentAssessmentGraph()._report(state)
    methods = next(section for section in state.report_snapshot.sections if section.section_key == "methods")
    assert all(f"4.{index}" in methods.content for index in range(1, 6))
    crosswalk = next(section for section in state.report_snapshot.sections if section.section_key == "evidence_crosswalk")
    assert "第四章" in crosswalk.content
    original = deepcopy(state.report_snapshot.model_dump())
    markdown, _ = build_markdown(state)
    html, _ = build_html(state)
    docx, _ = build_docx(state)
    pdf, _ = build_pdf(state)
    docx_text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(docx)).paragraphs)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        pdf_text = "\n".join(page.get_text() for page in document)
    for text in (markdown, html, docx_text, pdf_text):
        assert "灌流条件对应" in text
        assert "检索围绕灌流条件" in text
    assert state.report_snapshot.model_dump() == original
