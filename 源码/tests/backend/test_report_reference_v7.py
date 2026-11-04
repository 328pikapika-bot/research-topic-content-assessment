"""The reference outline is a projection of evidence, not a new evaluation."""
import re

from docx import Document

from app.agents.content_assessment.reference_outline import CHAPTER_TITLES, SUBHEADINGS, TABLE_TITLES
from app.agents.content_assessment.report_composition import block_text_with_sources, normalize_outline
from app.agents.content_assessment.schemas import ContentAssessmentState, ReportBlock, ReportSectionSpec, ReportSnapshot, GuidelineRequirement, IndicatorValidation
from app.services.content_assessment_report import _add_report_blocks, _configure, prepare_chart_manifest, _data
from report_rich_fixture import rich_state


def test_result_outline_groups_real_points_and_numbers_five_figures():
    state = rich_state("standard")
    sections = state.report_snapshot.sections
    assert [s.section_key for s in sections] == ["executive", "object_points", "research_progress", "comparison_novelty", "trends_advice", "references"]
    assert state.report_snapshot.template_version == "ContentAssessmentResultReportV4"
    all_blocks = [b for s in sections for b in s.blocks]
    progress = next(s for s in sections if s.section_key == "research_progress")
    points = [b for b in progress.blocks if b.kind == "heading"]
    assert len(points) == len(state.claims)
    assert all(b.text.startswith(f"要点 {i}：") for i, b in enumerate(points, 1))
    assert any("反证" in b.text for b in progress.blocks)
    assert not any("解析结果" in b.text or "执行总体情况" in b.text for b in all_blocks if b.kind == "caption")
    manifest = state.report_snapshot.chart_manifest
    assert [e["figure_number"] for e in manifest] == ["5-1"]
    assert all(e["included"] for e in manifest)
    assert not any("第12章" in s.title or "质量检查" in s.title for s in sections)


def test_three_claims_keep_distinct_findings_and_rewrites_in_one_place():
    state = rich_state("standard")
    state.report_snapshot = None
    state.report_plan.section_specs = []
    state.claims = state.claims[:3]
    state.novelty_decisions = state.novelty_decisions[:3]
    state.claim_assessments = state.claim_assessments[:3]
    state.claim_evidence_links = [l for l in state.claim_evidence_links if l.claim_id in {c.claim_id for c in state.claims}]
    normalize_outline(state)
    chapters = {s.section_key: s for s in state.report_plan.section_specs}
    decision_table = next(b for b in chapters["comparison_novelty"].blocks if b.kind == "table")
    assert len(decision_table.text.splitlines()) == 5
    for decision in state.novelty_decisions:
        assert decision.rationale in decision_table.text
        assert decision.rewrite_advice in chapters["trends_advice"].content
        assert all(decision.rewrite_advice not in s.content for s in state.report_plan.section_specs if s.section_key != "trends_advice")
    assert all("候选证据" not in b.text for s in chapters.values() for b in s.blocks if b.kind == "heading")
    assert not any(re.search(r"\bC-\d+\b", s.content) for s in state.report_plan.section_specs)


def test_table_sources_do_not_break_last_row_or_disappear_in_docx():
    block = ReportBlock(kind="table", text="| 指标 | 结论 |\n| --- | --- |\n| 成本 | 同口径比较 |", evidence_ids=["E-001"])
    rendered = block_text_with_sources(block)
    assert rendered.splitlines()[2].endswith("|")
    assert "\n\n依据：E-001" in rendered
    doc = Document()
    _configure(doc)
    _add_report_blocks(doc, ReportSectionSpec(section_key="chapter_9", title="指标", blocks=[block]), citation_urls={"1": "https://example.org/source"}, citation_numbers={"E-001": 1})
    assert len(doc.tables[0].columns) == 2
    assert doc.tables[0].cell(1, 1).text == "同口径比较"
    assert "依据：[1]" in "".join(p.text for p in doc.paragraphs)


def test_coverage_figures_are_explicit_counts_not_invented_market_or_scores(monkeypatch):
    state = rich_state("standard")
    data = _data(state)
    data["market_cards"][0].value = "无同口径数值"
    data["market_cards"][1].value = "只有背景材料"
    data["scorecard"][0].score_range = "较高"
    # Manifest is frozen once; inspect a fresh manifest for the changed inputs.
    state.report_snapshot.chart_manifest = []
    state.report_snapshot.sections[-2].chart_keys += ['market', 'radar']
    monkeypatch.setattr("app.services.content_assessment_report._data", lambda _: data)
    prepare_chart_manifest(state)
    manifest = state.report_snapshot.chart_manifest
    market = next(e for e in manifest if e["chart_key"] == "market")
    radar = next(e for e in manifest if e["chart_key"] == "radar")
    assert market["render_mode"] == radar["render_mode"] == "coverage"
    assert "覆盖" in market["chart_caption"] and "覆盖" in radar["chart_caption"]
    assert "不代表" in market["chart_source_note"]


def test_guideline_table_matches_reference_four_columns():
    state = rich_state("standard")
    state.report_snapshot = None
    state.report_plan.section_specs = []
    state.guideline_requirements = [GuidelineRequirement(requirement_id="G1", source_document_id="guide-file", text="独立验证", source_span="指南原文要求独立验证", match_status="partial")]
    normalize_outline(state)
    table = next(b for s in state.report_plan.section_specs if s.section_key == "comparison_novelty" for b in s.blocks if b.kind == "table" and b.text.startswith("| 指南要求"))
    assert table.text.splitlines()[0] == "| 指南要求 | 项目应答 | 符合性 | 说明 |"
    assert "指南原文要求独立验证" in table.text


def test_indicator_table_separates_public_and_project_baselines():
    state = rich_state("standard")
    state.report_snapshot = None
    state.report_plan.section_specs = []
    state.indicator_validations = [
        IndicatorValidation(indicator_id=state.indicators[0].indicator_id,
                            literature_baseline="文献条件下误差 2%", baseline="项目对照尚未实测", target="1"),
        IndicatorValidation(indicator_id=state.indicators[1].indicator_id,
                            baseline="项目同批空白对照"),
    ]
    normalize_outline(state)
    table = next(b for s in state.report_plan.section_specs if s.section_key == "comparison_novelty" for b in s.blocks if b.kind == "table" and b.text.startswith("| 候选指标"))
    first_row, second_row = table.text.splitlines()[2:4]
    assert "公开文献：文献条件下误差 2%" in first_row
    assert "项目对照尚未实测" not in first_row
    assert "项目材料对照：项目同批空白对照" in second_row
    assert "方案未单列数值目标" in second_row


def test_markdown_html_preserve_result_and_point_hierarchy(monkeypatch):
    from app.services import export_service
    state = rich_state("standard")
    monkeypatch.setattr(export_service, "prepare_exports", lambda _: None)
    markdown, _ = export_service.build_markdown(state)
    html, _ = export_service.build_html(state)
    assert "\n## 评估摘要与总体结论\n" in markdown
    assert "\n## 相关研究进展\n" in markdown
    point = next(b.text for s in state.report_snapshot.sections for b in s.blocks if b.text.startswith("要点 1："))
    assert f"\n#### {point}\n" in markdown
    assert re.search(r'<h2[^>]*>相关研究进展</h2>', html)
    assert re.search(r'<h4[^>]*>' + re.escape(point) + r'</h4>', html)
    assert "文档上传与解析" not in markdown


def test_historical_headings_stay_as_prose_without_polluting_new_directory():
    from app.services.export_service import ensure_snapshot
    original = ReportSnapshot(schema_version="report-snapshot-v6", snapshot_id="old-heading-snapshot",
                              generated_at="2026-09-01T10:00:00+08:00",
                              template_version="ContentAssessmentReportReferenceV1", render_version="reference-layout-v1",
                              sections=[ReportSectionSpec(section_key="assessment_innovation", title="第12章 原分析",
                                  blocks=[ReportBlock(kind="heading", text="原主张分析：候选证据", heading_level=3),
                                          ReportBlock(text="原判断及引用 E-old", evidence_ids=["E-old"])])],
                              citation_numbers={"E-old": 2})
    state = ContentAssessmentState(raw_user_input="历史材料", report_snapshot=original)
    ensure_snapshot(state)
    assert state.report_snapshot.model_dump() == original.model_dump()
    assert state.report_plan.section_specs[0].section_key == "assessment_innovation"
    assert state.report_plan.section_specs[0].blocks[0].kind == "heading"
    assert state.report_snapshot.citation_numbers == {"E-old": 2}


def test_new_bibliography_uses_body_citation_order():
    state = rich_state("standard")
    snapshot = state.report_snapshot
    references = next(s for s in snapshot.sections if s.section_key == "references")
    numbers = [min(snapshot.citation_numbers[eid] for eid in b.evidence_ids)
               for b in references.blocks if b.evidence_ids]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)
