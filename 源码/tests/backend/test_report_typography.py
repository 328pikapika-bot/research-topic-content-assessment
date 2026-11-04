"""Typography must not rewrite frozen text, navigation or evidence links."""
import io

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from app.services.report_typography import apply_report_typography, report_font_family, REPORT_TYPOGRAPHY_CSS
from app.services.content_assessment_report import _configure, _paragraph, _heading, _running_header
from app.services import topic_selection_report as topic
from app.agents.topic_selection.schemas import TopicSelectionState


def test_shared_typography_preserves_research_text_links_and_outline():
    document = Document()
    _configure(document)
    _heading(document, "一、研究内容", 1)
    _paragraph(document, "既有研究比较 [1]，保留适用条件。", citation_urls={"1": "https://example.org/paper"})
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "优先级"
    table.cell(1, 1).text = "比较结果"
    texts = document.element.xpath('//w:t/text()')
    relations = [(r.rId, r.target_ref) for r in document.part.rels.values()]
    outline = document.element.xpath('//w:outlineLvl/@w:val')
    apply_report_typography(document)
    assert document.element.xpath('//w:t/text()') == texts
    assert [(r.rId, r.target_ref) for r in document.part.rels.values()] == relations
    assert document.element.xpath('//w:outlineLvl/@w:val') == outline
    body = document.paragraphs[1]
    assert body.paragraph_format.first_line_indent.pt == 24
    assert body.paragraph_format.line_spacing.pt == 21.6
    assert body.runs[0].font.size.pt == 12
    assert document.paragraphs[0].runs[0].font.size.pt == 19
    assert table.cell(1, 1).paragraphs[0].runs[0].font.size.pt == 10.5
    for run in document.element.xpath('//w:hyperlink/w:r'):
        assert run.rPr.rFonts.get(qn('w:eastAsia')) == report_font_family()
        assert run.rPr.color.get(qn('w:val')) == '2C6FBB'
    # Applying twice must not accumulate style nodes or alter content.
    first = document.element.xml
    apply_report_typography(document)
    assert document.element.xml == first


def test_topic_renderer_styles_snapshot_without_mutating_it():
    state = TopicSelectionState(raw_user_input="城市热环境研究")
    state.report_plan.sections = [dict(key="summary", title="推荐结论", blocks=[
        dict(kind="paragraph", text="热环境研究采用实测与遥感相结合的方法。")])]
    frozen = state.model_dump()
    payload, _ = topic._build_topic_docx_source(state)
    document = Document(io.BytesIO(payload))
    body = next(p for p in document.paragraphs if p.text.startswith("热环境研究"))
    assert body.runs[0].font.size == Pt(12)
    assert body.runs[0].font.name == report_font_family()
    assert state.model_dump() == frozen
    markup, _ = topic.build_topic_html(state)
    assert REPORT_TYPOGRAPHY_CSS in markup.decode('utf-8')


def test_assessment_running_header_uses_official_title_without_cover_or_chapter_fields():
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    title = "固态电池电极界面稳定化研究创新性评估报告"
    document = Document()
    _configure(document)
    _paragraph(document, "保留原有判断及引用[1]。")
    body_before = document.element.xml
    apply_report_typography(document)
    _running_header(document, title)
    section = document.sections[0]
    header = section.header.paragraphs[0]
    assert header.text == title
    assert header.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert header.runs[0].font.size == Pt(8)
    assert str(header.runs[0].font.color.rgb) == '999999'
    assert header.runs[0]._element.rPr.rFonts.get(qn('w:eastAsia')) == report_font_family()
    assert not section.header._element.xpath('.//w:fldSimple')
    assert section.different_first_page_header_footer
    assert section.first_page_header.paragraphs[0].text == ''
    # The running title updates only its own part, and can be applied repeatedly.
    styled_body = document.element.xml
    _running_header(document, title)
    assert document.element.xml == styled_body
    assert document.paragraphs[0].text == '保留原有判断及引用[1]。'
    assert body_before != styled_body  # shared font styling still applies
