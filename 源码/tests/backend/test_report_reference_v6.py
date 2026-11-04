"""Reference-layout regressions using isolated, explicitly synthetic inputs."""
import io
import json
import re
import html as html_tools
import xml.etree.ElementTree as ET
import zipfile

import fitz
import pytest
from docx import Document

from app.agents.content_assessment.schemas import ContentAssessmentState, IndicatorValidation, ReportSectionSpec, ReportSnapshot, ScoreCardItem
from app.agents.content_assessment.report_composition import freeze_snapshot, markdown, state_from_snapshot, condense
from app.agents.content_assessment.report_composition import reader_text, render_citations
from app.services import export_service
from app.services.content_assessment_report import (
    _data, _score, _chart_is_ready, _competition_points, _year_theme_series,
    build_content_assessment_docx, build_report_chart_assets,
)
from report_rich_fixture import rich_state


def test_exports_read_frozen_facts_and_share_figures(monkeypatch):
    state = rich_state("standard")
    snapshot = state.report_snapshot.model_copy(deep=True)
    expected = markdown(state)
    state.claims.clear()
    state.evidence_records.clear()
    state.scorecard.clear()
    state.market_data_cards.clear()
    state.assessment_profile.assessment_object = "不应进入冻结报告的变更"
    state.trend_analysis.clear()
    state.report_plan.section_specs = []
    assert markdown(state) == expected
    assert _data(state)["claims"]
    assert len(state_from_snapshot(snapshot).scorecard) == 6
    monkeypatch.setattr(export_service, "prepare_exports", lambda _: None)
    html, _ = export_service.build_html(state)
    raw, _ = build_content_assessment_docx(state)
    doc = Document(io.BytesIO(raw))
    assert "不应进入冻结报告的变更" not in html
    assert all(section.title in html for section in snapshot.sections)
    assert expected.count("data:image/png;base64,") == 1
    assert html.count("<figure>") == 1
    # The result layout has five fixed, data-backed report figures.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        images = [archive.read(n) for n in archive.namelist() if n.startswith("word/media/")]
    assert len(images) == 1
    assert all(asset in images for asset in build_report_chart_assets(state).values())
    assert [p.text for p in doc.paragraphs if p.style.name == "Report Chapter"] == [s.title for s in snapshot.sections]


def test_indicator_conclusion_and_citation_stay_frozen_in_report_formats():
    state = rich_state("standard")
    graph = __import__("app.agents.content_assessment.graph", fromlist=["ContentAssessmentGraph"]).ContentAssessmentGraph()
    finding = "指标评估回归：文献基线为10%，项目目标为20%，相差10个百分点；项目实测未提供。"
    state.indicator_validations = [IndicatorValidation(
        indicator_id=state.indicators[0].indicator_id, definition="成本降低", unit="%",
        target="20%", literature_baseline="10%", literature_baseline_status="comparable",
        assessment_conclusion=finding, evidence_refs=["E11"],
    )]
    state.report_metrics.indicator_assessed_count = 1
    state.report_metrics.indicator_evidence_count = 1
    graph._report(state)
    snapshot = state.report_snapshot.model_copy(deep=True)
    assert snapshot.audit_data["indicator_validations"][0]["assessment_conclusion"] == finding
    assert snapshot.citation_numbers["E11"] > 0
    state.indicator_validations.clear()
    state.indicators.clear()
    state.evidence_records.clear()
    assert finding in markdown(state)
    html, _ = export_service.build_html(state)
    assert finding in html
    raw, _ = build_content_assessment_docx(state)
    doc = Document(io.BytesIO(raw))
    assert any(finding in paragraph.text for paragraph in doc.paragraphs)
    assert state_from_snapshot(snapshot).indicator_validations[0].assessment_conclusion == finding


def test_chart_gates_reject_proxies_missing_sources_and_mixed_axes():
    state = rich_state("standard")
    data = _data(state)
    assert all(_chart_is_ready(data, key) for key in ("workflow", "years", "market", "competition", "radar"))
    data["scorecard"][0].score_range = "0"
    assert _score(data["scorecard"][0], data) == 0
    data["scorecard"][0].score_range = "2–4"
    assert _score(data["scorecard"][0], data) == 3
    for invalid in ("较高", "6/5", "4–2", "缺少数据 4", ""):
        data["scorecard"][0].score_range = invalid
        assert not _chart_is_ready(data, "radar")
    data["competitive_cards"][0].source_evidence_ids = ["missing"]
    assert not _chart_is_ready(data, "competition")
    data["competitive_cards"][0].source_evidence_ids = ["E11"]
    data["competitive_cards"][0].x_unit = "分钟"
    assert len(_competition_points(data)) == 2
    assert not _chart_is_ready(data, "competition")
    data["market_cards"] = data["market_cards"][:1]
    assert _chart_is_ready(data, "market")
    data["market_cards"][0].source_evidence_ids = ["missing"]
    assert not _chart_is_ready(data, "market")
    data["market_cards"][0].source_evidence_ids = ["E11"]
    data["market_cards"][0].value = "覆盖 12 条来源"
    assert not _chart_is_ready(data, "market")
    data["trend"]["themes"] = [{"label": "只有一个主题", "evidence_ids": ["E11", "E12"]}]
    assert len(_year_theme_series(data)) == 1
    assert not _chart_is_ready(data, "years")
    data["trend"]["themes"] = [{"label": "主题一", "evidence_count": 4}, {"label": "主题二", "evidence_count": 10}]
    data["queries"] = []
    assert not _chart_is_ready(data, "years")


def test_history_adapter_preserves_original_text_citations_and_audit():
    source = ReportSectionSpec(section_key="conclusion", title="原结论", content="历史判断与反证须保留 [7]。")
    original = ReportSnapshot(schema_version="report-snapshot-v4", snapshot_id="historical-original", generated_at="2026-09-01T10:00:00+08:00",
                              sections=[source], citation_numbers={"E-old": 7}, audit_data={"claims": []})
    state = ContentAssessmentState(raw_user_input="历史任务", report_snapshot=original)
    state.assessment_profile.title = "现在修改的对象"
    state.stage_trace = rich_state("standard", historical=True).stage_trace
    export_service.ensure_snapshot(state)
    assert state.report_snapshot.model_dump() == original.model_dump()
    assert state.report_plan.section_specs == original.sections
    assert not condense(state)
    assert state.report_snapshot.model_dump() == original.model_dump()


def test_sparse_report_omits_all_chart_slots_and_records_actual_shortage():
    state = ContentAssessmentState(raw_user_input="材料不足任务")
    state.report_plan.section_specs = [ReportSectionSpec(section_key="executive", title="摘要", content="没有足够材料作出判断。")]
    freeze_snapshot(state, historical=True)
    assert not any(e["included"] for e in state.report_snapshot.chart_manifest)
    assert "data:image/png" not in markdown(state)
    pdf, _ = export_service.build_pdf(state)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert state.report_metrics.actual_page_count == len(document)
        assert len(document) < 20
    assert state.report_metrics.page_target_status == "below_target"
    assert state.report_metrics.page_target_reason


def test_export_pagination_never_condenses_frozen_content(monkeypatch):
    state = rich_state("standard")
    original = state.report_snapshot.model_dump()
    with fitz.open() as document:
        for _ in range(28):
            document.new_page()
        payload = document.tobytes()
    monkeypatch.setattr(export_service, "build_content_assessment_docx", lambda _: (b"docx", "report.docx"))
    export_service._render_finalized(state, lambda _: (b"normalized", payload), fitz, markdown)
    assert state.report_snapshot.model_dump() == original
    assert state.report_metrics.actual_page_count == 28
    assert state.report_metrics.page_target_status == "not_applicable"


def test_missing_cjk_font_fails_instead_of_drawing_garbled_labels(monkeypatch):
    from PIL import ImageFont
    from app.services.content_assessment_report import _font
    monkeypatch.setattr(ImageFont, "truetype", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")))
    with pytest.raises(RuntimeError, match="未找到可用.*中文字体"):
        _font(ImageFont, 14)


def test_legacy_plan_freezes_once_without_backfilling_live_stages():
    state = rich_state("standard")
    state.report_snapshot = None
    state.report_plan.section_specs = [ReportSectionSpec(section_key="conclusion", title="原报告", content="原判断保持不变。")]
    export_service.ensure_snapshot(state)
    snapshot = state.report_snapshot.model_copy(deep=True)
    assert not snapshot.stage_trace
    assert not snapshot.chart_data_snapshot
    assert any("原判断保持不变。" in s.content for s in snapshot.sections)
    assert len(snapshot.sections) == 1
    assert snapshot.sections[0].title == "原报告"
    assert snapshot.source_snapshot_id == "legacy-read-only"
    state.report_plan.section_specs = []
    state.novelty_decisions.clear()
    export_service.ensure_snapshot(state)
    assert state.report_snapshot.model_dump() == snapshot.model_dump()


def test_deduplicated_citation_resolves_valid_alias_by_source_priority():
    from app.common.retrieval.links import citation_urls
    records = [
        {"evidence_id": "A", "source_url": "https://example.org/original"},
        {"evidence_id": "B", "nstl_url": "https://example.org/nstl"},
        {"evidence_id": "C"},
        {"evidence_id": "D", "doi": "10.1000/example"},
    ]
    assert citation_urls(records, {"A": 1, "B": 1, "C": 1, "D": 2}) == {"1": "https://example.org/nstl", "2": "https://doi.org/10.1000/example"}


def test_long_reference_table_repeats_header_across_real_pdf_pages():
    from app.services.content_assessment_report import _configure, _add_table, _heading
    from app.services.report_pdf_runtime import render_docx
    document = Document()
    _configure(document)
    _heading(document, "跨页表格隔离验收", 1)
    _add_table(document, ["验证编号", "验证内容"],
               [[f"ROW-{number:03}", f"模拟记录 {number}：确认长表格延续时重复表头且保留全部记录。"] for number in range(100)],
               [2000, 7600], size=10.5)
    buffer = io.BytesIO()
    document.save(buffer)
    _, payload = render_docx(buffer.getvalue())
    with fitz.open(stream=payload, filetype="pdf") as pdf:
        assert len(pdf) >= 3
        for page in pdf:
            if "ROW-" in page.get_text():
                assert "验证编号" in page.get_text()
        text = "".join(page.get_text() for page in pdf)
        assert all(f"ROW-{number:03}" in text for number in range(100))


def test_reference_report_real_pagination_toc_and_links(tmp_path):
    state = rich_state("standard", historical=True)
    docx, _ = export_service.build_docx(state)
    pdf, _ = export_service.build_pdf(state)
    text, _ = export_service.build_markdown(state)
    html, _ = export_service.build_html(state)
    for name, payload in (("report.docx", docx), ("report.pdf", pdf)):
        (tmp_path / name).write_bytes(payload)
    (tmp_path / "report.md").write_text(text, encoding="utf-8")
    (tmp_path / "report.html").write_text(html, encoding="utf-8")
    (tmp_path / "snapshot.json").write_text(json.dumps(state.report_snapshot.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert 1 <= len(document) <= 40, f"实际 {len(document)} 页；样稿 {tmp_path}"
        assert document[0].rect.width == 595.3037109375 or abs(document[0].rect.width - 595.28) < 1
        all_text = "\n".join(page.get_text() for page in document)
        body_text = "".join(page.get_text(clip=fitz.Rect(45, 50, page.rect.width - 40, page.rect.height - 55)) for page in document)
        assert "run_" not in all_text
        assert "\ufffd" not in all_text
        assert "目录" in document[1].get_text()
        toc = document.get_toc()
        assert len(toc) >= 20
        assert any(link.get("uri", "").startswith("https://") for page in document for link in page.get_links())
        assert sum(len(page.get_image_info()) for page in document) >= 1
        for page in document:
            for figure in page.get_image_info():
                if figure["height"] == 900:
                    assert figure["bbox"][1] >= 50
                    assert figure["bbox"][3] <= page.rect.height - 40
        for section in state.report_snapshot.sections:
            assert section.title.replace(" ", "") in all_text.replace(" ", "").replace("\n", "")
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        xml = archive.read("word/document.xml").decode()
        assert "TOC" in xml
        assert 'w:firstLine="420"' in xml
        if any(b.kind == "table" for section in state.report_snapshot.sections for b in section.blocks):
            assert "tblHeader" in xml
        else:
            assert "<w:tbl>" not in xml
    def normalize(value):
        # Citation positions can differ visually in superscript runs; their
        # numbers and URLs are separately checked above. Compare all prose.
        # Markdown keeps link syntax; DOCX/PDF/HTML render the link label.
        value = re.sub(r"\[([^\]]+)\]\(<?https?://[^\s)]+>?\)", r"\1", value)
        return re.sub(r"\s+", "", re.sub(r"\[\d+\]", "", value))
    doc_text = "".join(node.text or "" for node in ET.fromstring(xml).iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
    contents = {"DOCX": normalize(doc_text), "PDF": normalize(body_text),
                "Markdown": normalize(text), "HTML": normalize(html_tools.unescape(re.sub(r"<[^>]+>", "", html)))}
    for section in state.report_snapshot.sections:
        for block in section.blocks:
            if block.kind not in {"paragraph", "callout", "heading"}:
                continue
            expected = normalize(reader_text(render_citations(block.text, state.report_snapshot.citation_numbers)))
            for format_name, content in contents.items():
                assert expected in content, f"{format_name} 未保留 {section.title} 正文：{block.text[:80]}"
    assert state.report_snapshot.template_version == "ContentAssessmentReportReferenceV2"
    # Historical templates keep their page accounting; rich frozen content
    # may exceed the legacy target and must be retained with an explicit reason.
    pages = state.report_metrics.actual_page_count
    expected_status = ("below_target" if pages < state.report_plan.target_page_min else
                       "above_target" if pages > state.report_plan.target_page_max else "within_target")
    assert state.report_metrics.page_target_status == expected_status
    if expected_status != "within_target":
        assert state.report_metrics.page_target_reason


def test_inline_export_keeps_user_scope_and_rejects_other_formats(monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    from app.api import routes
    from app.main import app
    from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository
    repository = InMemoryTaskHistoryRepository()
    state = ContentAssessmentState(raw_user_input="隔离接口报告", current_stage="completed")
    freeze_snapshot(state)
    asyncio.run(repository.create({"task_id": "reference-api-qa", "user_scope": "owner", "state": state.model_dump(), "status": "completed", "updated_at": "fixed"}))
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repository)
    export_scopes = []
    def render_pdf(state):
        export_scopes.append(state.user_scope)
        return b"%PDF-isolated", "隔离报告.pdf"
    monkeypatch.setattr(routes, "build_pdf", render_pdf)
    monkeypatch.setattr(routes, "ALLOW_DEFAULT_USER_SCOPE", False)
    with TestClient(app) as client:
        url = "/v1/content-assessment/tasks/reference-api-qa/export"
        assert client.get(url + "?format=pdf&disposition=inline").status_code == 401
        assert client.get(url + "?format=pdf&disposition=inline", headers={"X-User-Scope": "other"}).status_code == 404
        response = client.get(url + "?format=pdf&disposition=inline", headers={"X-User-Scope": "owner"})
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith("inline;")
        assert response.content == b"%PDF-isolated"
        assert export_scopes == ["owner"]
        assert client.get(url + "?format=md&disposition=inline", headers={"X-User-Scope": "owner"}).status_code == 422
