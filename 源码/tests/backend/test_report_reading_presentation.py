import io
import re
import zipfile
from pathlib import Path
import fitz
from docx import Document
from lxml import etree

from app.agents.content_assessment.report_presentation import display_snapshot, legacy_reference, RENDER_VERSION
from app.agents.content_assessment.schemas import ReportSnapshot, ReportSectionSpec, ReportBlock
from app.agents.topic_selection.schemas import TopicSelectionState
from app.services import export_service, topic_selection_report
from app.services.content_assessment_report import _configure, _add_report_blocks
from app.services.report_pdf_runtime import render_docx
from report_rich_fixture import rich_state


def snapshot():
    records = [
        dict(evidence_id="E1", title="文献题名一", authors=["作者甲"], venue="期刊", year=2024, nstl_url="https://example.org/nstl", source_url="https://example.org/original"),
        dict(evidence_id="E2", title="文献题名二", source_url="https://example.org/original"),
        dict(evidence_id="E3", title="文献题名三", doi="10.1000/test"),
        dict(evidence_id="E4", title="无地址文献", url="javascript:alert(1)"),
    ]
    return ReportSnapshot(snapshot_id="original", generated_at="2026-10-10", audit_data={"evidence_records": records}, citation_numbers={f"E{i}": i for i in range(1, 5)}, sections=[
        ReportSectionSpec(section_key="references", title="参考文献", is_appendix=True, blocks=[ReportBlock(text=f"E{i} 文献，NSTL 详情：未提供；原文地址：未提供。定位：摘要或题录，尚无全文定位。", evidence_ids=[f"E{i}"]) for i in range(1, 5)]),
        ReportSectionSpec(section_key="evidence_crosswalk", title="附录B 创新点—证据对照总表", blocks=[
            ReportBlock(text="报告仅用于已确认评估目的。原始材料、完整证据、逐字段矩阵及检索记录见同版本证据附件。"),
            ReportBlock(text="用户提供的技术描述；文件 ID user_material；内容校验 待补充"),
            ReportBlock(text="真实检索记录及专业限制：无同口径实测数据。")]),
    ])


def test_history_projection_is_immutable_idempotent_and_keeps_actual_appendix():
    original = snapshot()
    before = original.model_dump()
    display = display_snapshot(original)
    assert original.model_dump() == before
    assert display.source_snapshot == before
    assert display.render_version == RENDER_VERSION
    assert display_snapshot(display).model_dump() == display.model_dump()
    all_text = "\n".join(s.content for s in display.sections)
    assert not any(x in all_text for x in ("报告仅用于", "user_material", "未提供", "尚无全文定位"))
    assert "真实检索记录及专业限制：无同口径实测数据。" in all_text
    assert "[文献题名一](<https://example.org/nstl>)" in all_text
    assert "[文献题名二](<https://example.org/original>)" in all_text
    assert "[文献题名三](<https://doi.org/10.1000/test>)" in all_text
    assert "[无地址文献](" not in all_text
    assert display.citation_numbers == original.citation_numbers


def test_persist_frozen_ui_fixture():
    import json
    state = rich_state("standard")
    state.uploaded_files = []
    state.uploaded_file_context.files = []
    state.assessment_profile.document_records = []
    folder = Path(".tmp/qa/report-reading"); folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state.model_dump(), ensure_ascii=False), encoding="utf-8")


def test_prose_only_history_preserves_its_existing_source():
    text = "[1] 作者甲. 历史文献题名. 期刊, 2024. DOI：10.1000/test；NSTL 详情：https://example.org/nstl；原文地址：https://example.org/source。定位：摘要或题录，尚无全文定位。"
    shown = legacy_reference(text)
    assert "历史文献题名" in shown and "(<https://example.org/nstl>)" in shown
    assert not any(value in shown for value in ("NSTL 详情", "原文地址", "定位", "尚无全文定位"))


def topic_state():
    state = TopicSelectionState(raw_user_input="研究问题")
    items = [{**item, "number": index} for index, item in enumerate(snapshot().audit_data["evidence_records"], 1)]
    state.report_plan.sections = [{"key": "references", "title": "参考文献", "blocks": [{"kind": "reference_list", "items": items}]}]
    return state


def test_topic_links_use_frozen_reference_items_in_all_formats():
    state = topic_state()
    # Unrelated mutable evidence must not supply historical bibliography links.
    before = state.model_dump()
    md = "\n".join(topic_selection_report._reference_lines(state.report_plan.sections[0]["blocks"][0]))
    html = topic_selection_report._html_block(state.report_plan.sections[0]["blocks"][0], {}, {})
    for url in ("https://example.org/nstl", "https://example.org/original", "https://doi.org/10.1000/test"):
        assert url in md and url in html
    assert "target='_blank'" in html
    docx, _ = topic_selection_report._build_topic_docx_source(state)
    doc = Document(io.BytesIO(docx))
    links = doc.element.xpath('//w:hyperlink')
    title_links = [n for n in links if "文献题名" in "".join(n.itertext())]
    assert len(title_links) == 3
    assert "javascript:" not in str(doc.part.rels.values())
    assert state.model_dump() == before


def test_both_agents_pdf_title_annotations_and_docx_relations():
    display = display_snapshot(snapshot())
    doc = Document(); _configure(doc)
    _add_report_blocks(doc, display.sections[0], citation_urls={}, citation_numbers=display.citation_numbers)
    buffer = io.BytesIO(); doc.save(buffer)
    topic_docx, _ = topic_selection_report._build_topic_docx_source(topic_state())
    folder = Path(".tmp/qa/report-reading"); folder.mkdir(parents=True, exist_ok=True)
    for agent, source in (("assessment", buffer.getvalue()), ("topic", topic_docx)):
        finalized, pdf = render_docx(source)
        (folder / f"{agent}.docx").write_bytes(finalized)
        (folder / f"{agent}.pdf").write_bytes(pdf)
        with fitz.open(stream=pdf, filetype="pdf") as document:
            annotations = [(page, link) for page in document for link in page.get_links() if link.get("uri")]
            for url in ("https://example.org/nstl", "https://example.org/original", "https://doi.org/10.1000/test"):
                assert any(link["uri"] == url and "文献题名" in page.get_textbox(link["from"]) for page, link in annotations)
            assert not any("javascript:" in link["uri"] for _, link in annotations)


def test_assessment_hyperlink_runs_include_standard_writer_style():
    display = display_snapshot(snapshot())
    doc = Document(); _configure(doc)
    _add_report_blocks(doc, display.sections[0], citation_urls={}, citation_numbers=display.citation_numbers)
    buffer = io.BytesIO(); doc.save(buffer)
    package = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    document_xml = package.read("word/document.xml").decode("utf-8")
    styles_xml = package.read("word/styles.xml").decode("utf-8")
    assert 'w:val="Hyperlink"' in document_xml
    assert 'w:styleId="Hyperlink"' in styles_xml
