import copy
import io

import pytest
from docx import Document

from app.agents.topic_selection.schemas import TopicSelectionState
from app.services import topic_selection_report as report


def test_frozen_narrow_heading_gets_readable_width_without_changing_content():
    spec = {
        "key": "candidate_priority", "title": "候选题优先级排序",
        "columns": ["优先级", "候选题"], "column_widths": [0.5, 5.8],
        "font_size": 9.5, "rows": [{"优先级": "1", "候选题": "城市热环境研究 [1]"}],
    }
    original = copy.deepcopy(spec)
    widths = report._table_widths(spec)
    assert widths[0] >= .72
    assert sum(widths) == pytest.approx(6.8)
    assert widths[1] > widths[0]
    markup = report._html_table(spec)
    assert '<colgroup>' in markup
    assert 'class="short-heading">优先级</th>' in markup
    assert "href='#ref-1'" in markup

    state = TopicSelectionState(raw_user_input="城市热环境研究")
    state.report_plan.table_specs = [spec]
    state.report_plan.sections = [{"key": "summary", "title": "推荐结论", "blocks": [{"kind": "table_ref", "key": "candidate_priority"}]}]
    before = state.model_dump()
    payload, _ = report._build_topic_docx_source(state)
    table = Document(io.BytesIO(payload)).tables[0]
    assert table.cell(0, 0).text == "优先级"
    assert table.cell(0, 0)._tc.xpath('./w:tcPr/w:noWrap')
    assert table.cell(0, 0).width.inches >= .72
    assert table.cell(1, 1)._tc.xpath('.//w:hyperlink')
    assert state.model_dump() == before
    assert spec == original


def test_report_figure_reserves_image_height_and_keeps_its_caption():
    state = TopicSelectionState(raw_user_input="城市热环境研究")
    state.report_plan.chart_specs = [{
        "key": "trend", "chart_type": "bar", "chart_version": "chart-v11",
        "title": "文献趋势", "figure_number": 1,
        "data": [{"label": "2024", "count": 3}, {"label": "2025", "count": 5}],
        "source_note": "来源：检索样本", "caption": "样本年份分布",
    }]
    state.report_plan.sections = [{
        "key": "trends", "title": "研究趋势",
        "blocks": [{"kind": "figure_ref", "key": "trend"}],
    }]
    payload, _ = report._build_topic_docx_source(state)
    document = Document(io.BytesIO(payload))
    figure = next(table for table in document.tables if table.cell(0, 0)._tc.xpath('.//w:drawing'))
    cell = figure.cell(0, 0)
    assert figure.rows[0]._tr.xpath('./w:trPr/w:cantSplit')
    assert cell.paragraphs[0].paragraph_format.line_spacing == 1.0
    assert [paragraph.text for paragraph in cell.paragraphs[1:]] == [
        "图 1 文献趋势", "来源：检索样本", "样本年份分布",
    ]
