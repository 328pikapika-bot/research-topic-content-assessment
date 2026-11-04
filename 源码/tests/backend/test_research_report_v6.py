"""V6 contracts: material-specific evaluation, factual status, frozen history."""
import asyncio
import io
import json
import re

import pytest
from docx import Document

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.research_outline import (
    TEMPLATE, chapter_definitions, compose_research_report, evaluation_dimensions,
    requests_material_revision,
)
from app.agents.content_assessment.report_composition import markdown
from app.agents.content_assessment.schemas import (
    ContentAssessmentState, Claim, EvidenceRecord, ClaimEvidenceLink,
    Indicator, IndicatorValidation, NoveltyDecision, QueryPlan,
)
from app.agents.content_assessment.scholarly_report import draft, publication_gaps
from app.services.content_assessment_report import build_content_assessment_docx


def research_sample(kind="paper"):
    s = ContentAssessmentState(raw_user_input="评估微流控肝脏芯片灌流材料，所有内容为工程验收模拟资料。")
    s.assessment_profile.title = s.assessment_profile.assessment_object = "模拟肝脏芯片灌流研究"
    s.assessment_profile.assessment_goal = "评价材料及证据边界"
    s.assessment_profile.scope_boundary = "模拟芯片灌流条件，不形成真实科研判断"
    s.report_preferences.artifact_type = s.report_preferences.resolved_artifact_type = kind
    s.claims = [Claim(claim_id="C1", text="拟维持肝脏芯片灌流72小时。", assertion_type="target",
        display_title="灌流条件", searchable_terms=["liver", "perfusion"],
        source_page_ref="材料第2页", claim_fields={"object": "liver chip", "method": "perfusion monitoring"})]
    s.evidence_records = [
        EvidenceRecord(evidence_id="E1", title="Synthetic liver chip perfusion", source_type="literature",
            year=2024, abstract="Liver chip perfusion monitoring reports 48 hours under matched flow conditions.",
            doi="10.9999/synthetic.perfusion"),
        EvidenceRecord(evidence_id="E2", title="Unrelated galaxy study", source_type="literature",
            abstract="Stellar orbits and dark matter."),
        EvidenceRecord(evidence_id="E3", title="Synthetic liver perfusion limitations", source_type="literature",
            year=2025, abstract="Liver chip perfusion monitoring may fail under excessive flow; results depend on flow conditions."),
    ]
    s.claim_evidence_links = [ClaimEvidenceLink(claim_id="C1", evidence_id="E1", relation="partially_supports"),
        ClaimEvidenceLink(claim_id="C1", evidence_id="E3", relation="contradicts")]
    s.indicators = [Indicator(indicator_id="I1", name="灌流时长", value="72", unit="小时", status="target",
        source_claim_id="C1", baseline="材料对照，不能当成文献结果", test_method="同流量观测")]
    s.indicator_validations = [IndicatorValidation(indicator_id="I1", target="72小时",
        project_result="材料自述60小时，未独立核验", literature_baseline="文献报告48小时",
        test_method="同流量观测", evidence_refs=["E1"], assessment_conclusion="目标仍需验证，不能推导已实现优势。")]
    s.novelty_decisions = [NoveltyDecision(claim_id="C1", label="方向性支持", rationale="材料限定于灌流条件比较。",
        evidence_ids=["E1"], rewrite_advice="将完成结果与目标分别表述。")]
    s.query_plans = [QueryPlan(claim_id="C1", query="liver chip perfusion", query_group="灌流条件",
        candidate_count=3, selected_count=2, source_scope=["模拟资料库"], year_range=[2024, 2025])]
    s.output_json["scholarly_drafts"] = {"C1": {
        "progress": [{"text": "模拟文献讨论灌流监测的适用条件。", "evidence_ids": ["E1"], "dimension": "representatives"}],
        "comparison": [{"text": "材料将灌流作为比较变量，其计划不能代替文献观察。", "evidence_ids": ["E1"], "dimension": "points"}],
        "judgement": [{"text": title + "：模拟材料的判断限定于灌流监测条件。", "evidence_ids": ["E1"], "dimension": dim}
            for dim, title in dict(chapter_definitions(kind)[6][2]).items()],
        "evaluation": [{"text": title + "：材料记录支持条件比较，验证范围以模拟资料为限。", "evidence_ids": ["E1"], "dimension": dim}
            for dim, title in evaluation_dimensions(kind).items()],
    }}
    s.output_json["scholarly_summary"] = [{"text": "本模拟材料形成可评价的灌流方案，性能目标仍须独立验证。", "evidence_ids": ["E1"]}]
    s.output_json["scholarly_conclusion"] = [{"text": "已有灌流监测方法提供比较依据，模拟材料的目标仍为待验证事项。", "evidence_ids": ["E1"]}]
    return s


@pytest.mark.parametrize("kind", ["paper", "project", "guideline"])
def test_new_outline_dimensions_citations_and_exports(kind):
    s = research_sample(kind)
    ContentAssessmentGraph()._report(s)
    snapshot = s.report_snapshot
    assert snapshot.template_version == TEMPLATE
    assert [x.title for x in snapshot.sections[:9]] == [x[1] for x in chapter_definitions(kind)]
    assert len([x for x in snapshot.sections if x.title.startswith("第")]) == 8
    section_keys = {x.section_key for x in snapshot.sections}
    assert {"references", "evidence_crosswalk"} <= section_keys
    assert "indicator_crosswalk" not in section_keys
    assert "material_details" not in section_keys
    assert set(snapshot.citation_numbers) == {"E1", "E3"}
    text = markdown(s)
    assert "Unrelated galaxy" not in text
    assert "主要问题与改进建议" not in text and "思考过程" not in text
    for title in evaluation_dimensions(kind).values():
        section = next(x for x in snapshot.sections if x.section_key == "evaluation")
        assert title + "：材料记录" in section.content
    judgement = next(x for x in snapshot.sections if x.section_key == "judgement")
    for name in ("创新性", "前沿性", "先进性"):
        assert name + "评价：模拟材料" in judgement.content
    comparison = next(x for x in snapshot.sections if x.section_key == "comparison").content
    assert "72 小时" in comparison and "同流量观测" in comparison
    assert "材料自述60小时" not in comparison and "文献报告48小时" not in comparison
    assert "摘要" in next(x for x in snapshot.sections if x.section_key == "evidence_crosswalk").content
    if kind == "guideline":
        assert "相关标准和指南体系" in text and "国内外规范要求" in text
    before = snapshot.model_dump()
    document, _ = build_content_assessment_docx(s)
    paragraphs = "\n".join(p.text for p in Document(io.BytesIO(document)).paragraphs)
    assert chapter_definitions(kind)[7][1] in paragraphs
    assert s.report_snapshot.model_dump() == before


@pytest.mark.parametrize("instruction,expected", [
    ("请评估材料。", False), ("不需要材料优化。", False),
    ("请不要生成修改建议。", False), ("希望提供表述修改建议。", True),
])
def test_advice_appendix_is_explicit_opt_in(instruction, expected):
    s = research_sample()
    s.raw_user_input = instruction
    assert requests_material_revision(instruction) is expected
    assert any(x.section_key == "revision_advice" for x in compose_research_report(s)) is expected


def test_old_cached_advice_does_not_replace_classification_evaluation():
    s = research_sample()
    point = s.output_json["scholarly_drafts"]["C1"]
    point["recommendations"] = point.pop("evaluation")
    assert publication_gaps(s)


def test_report_generation_routes_new_dimensions_and_summary(monkeypatch):
    s = research_sample("guideline")
    s.output_json.clear()
    graph = ContentAssessmentGraph()
    async def respond(state, *, user_prompt, **kwargs):
        if "checked:true" in user_prompt:
            return {"checked": True, "replacements": []}
        if "综合为评估摘要" in user_prompt:
            return {"paragraphs": [{"text": "材料形成灌流条件比较方案。", "evidence_ids": ["E1"]}],
                "conclusion": [{"text": "计划不构成已实现的灌流优势。", "evidence_ids": ["E1"]}]}
        return research_sample("guideline").output_json["scholarly_drafts"]["C1"]
    monkeypatch.setattr(graph, "_request_model_json", respond)
    asyncio.run(draft(graph, s))
    assert not publication_gaps(s)
    assert s.output_json["scholarly_summary"] and s.output_json["scholarly_conclusion"]
    assert {p["dimension"] for p in s.output_json["scholarly_drafts"]["C1"]["evaluation"]} == set(evaluation_dimensions("guideline"))


def test_unverified_baseline_never_becomes_literature_result():
    s = research_sample()
    s.indicator_validations.clear()
    report = compose_research_report(s)
    comparison = next(x for x in report if x.section_key == "comparison").content
    assert "72 小时" in comparison
    assert "材料对照，不能当成文献结果" not in comparison
    assert "indicator_crosswalk" not in {x.section_key for x in report}


def test_work_packages_are_bound_to_material_or_point_refs():
    from app.agents.content_assessment.schemas import WorkPackage
    from app.agents.content_assessment.scholarly_report import point_packet
    s = research_sample("project")
    s.work_packages = [WorkPackage(work_package_id="WP1", title="相关任务", source_refs=["C1"]),
        WorkPackage(work_package_id="WP2", title="其他任务", source_refs=["C2"])]
    packet = point_packet(s, s.claims[0])
    assert [item["work_package_id"] for item in packet["material_design"]["work_packages"]] == ["WP1"]


def test_report_toc_excludes_repeated_point_labels_and_keeps_sections():
    from zipfile import ZipFile
    s = research_sample("project")
    ContentAssessmentGraph()._report(s)
    document, _ = build_content_assessment_docx(s)
    with ZipFile(io.BytesIO(document)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert 'TOC \\o "1-3"' in xml
    assert 'w:val="Heading4"' not in xml
    toc = markdown(s).split('## 摘要')[0]
    assert '灌流条件' not in toc
    assert '6.1 创新性评价' in toc


def test_numeric_material_results_are_not_discarded_by_source_validator():
    from app.agents.content_assessment.scholarly_report import point_packet, validated_paragraphs
    s = research_sample("project")
    s.indicator_validations[0].source_claim_id = "C1"
    rows = validated_paragraphs([{"text": "材料自述60小时，未独立核验。", "evidence_ids": [], "kind": "material"}],
        point_packet(s, s.claims[0]))
    assert rows


def test_body_page_count_uses_bookmarks_instead_of_prose_mentions():
    from types import SimpleNamespace
    from app.services.export_service import _render_finalized
    s = research_sample()
    ContentAssessmentGraph()._report(s)
    class Pdf(list):
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_toc(self): return [[1, '第一章 评估说明', 3], [1, '参考文献', 10]]
    fake = SimpleNamespace(open=lambda **kwargs: Pdf([
        SimpleNamespace(get_text=lambda: '正文讨论参考文献的使用范围') for _ in range(12)]))
    _render_finalized(s, lambda payload: (payload, b'fake-pdf'), fake, markdown)
    assert s.report_metrics.body_pages == 7
