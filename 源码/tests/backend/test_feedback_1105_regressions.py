"""Regression evidence for the November 5 feedback."""
import asyncio
import io
import re
from types import SimpleNamespace

from docx import Document

from app.agents.topic_selection.schemas import TopicSelectionState, EvidenceRecord
from app.agents.topic_selection.scholarly_report import compact_citations
from app.agents.content_assessment.material_extraction import locate
from app.agents.content_assessment.schemas import UploadedFileDigest, ReportBlock, ReportSectionSpec
from app.common.retrieval.links import bibliography_text
from app.common.retrieval.parser import SearchResultParser
from app.services.content_assessment_report import _add_report_blocks, _configure, _reference_table_widths
from app.services.topic_selection_report import _chart_md


def test_dense_topic_references_follow_visible_citations_and_keep_source_identity():
    s = TopicSelectionState(raw_user_input='测试引用')
    s.evidence_pool = [EvidenceRecord(evidence_id=f'E-{i}', title=f'文献{i}', doi=f'10.1234/{i}') for i in range(1, 6)]
    s.evidence_pool.append(EvidenceRecord(evidence_id='alias', title='重复来源', doi='10.1234/4'))
    s.report_plan.citation_map = {e.evidence_id: i for i, e in enumerate(s.evidence_pool, 1)}
    s.report_plan.sections = [
        {'key': 'abstract', 'blocks': [{'kind': 'paragraph', 'text': '研究依据[4] [2]，重复[6]。'}]},
        {'key': 'references', 'blocks': [{'kind': 'reference_list', 'items': []}, {'kind': 'table_ref', 'key': 'cross_matrix'}]},
    ]
    s.report_plan.table_specs = [{'key': 'cross_matrix', 'rows': [{'参考文献': '[5]'}]}]
    compact_citations(s)
    assert s.report_plan.sections[0]['blocks'][0]['text'] == '研究依据[1][2]，重复[1]。'
    assert s.report_plan.table_specs[0]['rows'][0]['参考文献'] == '[3]'
    refs = s.report_plan.sections[1]['blocks'][0]['items']
    assert [(r['number'], r['title']) for r in refs] == [(1, '文献4'), (2, '文献2'), (3, '文献5')]
    assert s.report_plan.citation_map == {'E-4': 1, 'E-2': 2, 'alias': 1, 'E-5': 3}
    assert 'E-1' not in s.report_plan.citation_links


def test_citations_follow_inline_table_order_and_ignore_unrendered_tables():
    s = TopicSelectionState(raw_user_input='引用顺序')
    s.evidence_pool = [EvidenceRecord(evidence_id=f'E{i}', title=f'文献{i}') for i in range(1, 5)]
    s.report_plan.citation_map = {f'E{i}': i for i in range(1, 5)}
    s.report_plan.sections = [
        {'key': 'analysis', 'blocks': [{'kind': 'table_ref', 'key': 'visible'}, {'kind': 'paragraph', 'text': '依据[3]。'}]},
        {'key': 'references', 'blocks': [{'kind': 'reference_list', 'items': []}]},
    ]
    s.report_plan.table_specs = [{'key': 'unused', 'rows': [{'文献': '[1]'}]}, {'key': 'visible', 'rows': [{'文献': '[4]'}]}]
    compact_citations(s)
    assert s.report_plan.citation_map == {'E4': 1, 'E3': 2}
    assert s.report_plan.sections[0]['blocks'][1]['text'] == '依据[2]。'
    assert s.report_plan.table_specs[1]['rows'] == [{'文献': '[1]'}]


def test_historical_missing_reference_is_recovered_from_frozen_record_without_mutating_snapshot():
    from app.services.topic_selection_report import _bounded_export_state, build_topic_markdown, build_topic_html
    s = TopicSelectionState(raw_user_input='历史报告')
    s.report_snapshot.frozen = True
    s.report_snapshot.report_plan_snapshot = {'citation_map': {'saved': 3}, 'sections': [
        {'key': 'abstract', 'title': '摘要', 'blocks': [{'kind': 'paragraph', 'text': '已有研究[3]。'}]},
        {'key': 'references', 'title': '参考文献', 'blocks': [{'kind': 'reference_list', 'items': []}]},
    ]}
    s.report_snapshot.result_data_snapshot = {'evidence_pool': [{'evidence_id': 'saved', 'title': '原始文献', 'doi': '10.1234/saved'}]}
    s.evidence_pool = [EvidenceRecord(evidence_id='saved', title='后来改写的题名')]
    before = s.model_dump()
    render = _bounded_export_state(s)
    row = render.report_plan.sections[1]['blocks'][0]['items'][0]
    assert (row['number'], row['title'], row['doi']) == (3, '原始文献', '10.1234/saved')
    assert b"id='ref-3'" in build_topic_html(s)[0]
    assert b'id="ref-3"' in build_topic_markdown(s)[0]
    assert s.model_dump() == before


def test_topic_markdown_reference_anchors_and_entries_are_separate_readable_paragraphs():
    from app.services.topic_selection_report import _reference_lines
    lines = _reference_lines({'items': [
        {'number': 1, 'title': '研究题名&#x20;测试', 'authors': '作者甲', 'venue': '期刊',
         'year': 2025, 'url': 'https://example.org/paper'},
        {'number': 2, 'title': '第二篇研究', 'authors': '作者乙', 'venue': '期刊', 'year': 2024},
    ]})
    rendered = '\n'.join(lines)
    assert '<a id="ref-1"></a>\n\n[1] ' in rendered
    assert '[1] [研究题名 测试](https://example.org/paper)' in rendered
    assert '[2] 第二篇研究' in rendered
    assert '&#x20;' not in rendered
    assert '](<https://' not in rendered


def test_material_type_metadata_is_never_a_claim_anchor():
    for kind in ('项目', '论文', '指南'):
        quote = f'我要评估的是：{kind}'
        source = UploadedFileDigest(file_id='input', name='输入', content=quote + '\n拟比较不同模型的预测误差。')
        assert locate(source, quote) is None
        assert locate(source, '拟比较不同模型的预测误差。')


def test_title_markup_is_removed_without_changing_the_record_doi_or_link():
    raw = '<![CDATA[Surface properties using Li<Subscript>2</Subscript>S-P<Subscript>2</Subscript>S<Subscript>5</Subscript> solid electrolyte]]>'
    item = SearchResultParser().parse({'data': [{'id': 'record-35', 'title': raw, 'doi': '10.1007/s10832-017-0084-z'}]}, 'battery')[0]
    assert item.title == 'Surface properties using Li2S-P2S5 solid electrolyte'
    assert item.doi == '10.1007/s10832-017-0084-z'
    assert item.nstl_url.endswith('?id=record-35')
    assert bibliography_text('[Paper](<https://example.org/paper>)') == '[Paper](<https://example.org/paper>)'
    assert bibliography_text(r'<!\[CDATA\[Surface properties\]\]>') == 'Surface properties'
    assert bibliography_text(0) == '0'


def test_word_citations_are_internal_and_reference_title_is_external():
    doc = Document(); _configure(doc)
    refs = {'1': 'https://example.org/paper'}
    _add_report_blocks(doc, ReportSectionSpec(section_key='progress', title='研究进展', blocks=[
        ReportBlock(kind='paragraph', text='已有研究[1]。')]), citation_urls=refs, citation_numbers={'E-1': 1})
    _add_report_blocks(doc, ReportSectionSpec(section_key='references', title='参考文献', blocks=[
        ReportBlock(kind='paragraph', text='[1] [文献题名](<https://example.org/paper>)', evidence_ids=['E-1'])]),
        citation_urls=refs, citation_numbers={'E-1': 1})
    assert doc.element.xpath('.//w:hyperlink[@w:anchor="evidence_1"]')
    assert doc.element.xpath('.//w:bookmarkStart[@w:name="evidence_1"]')
    assert any(rel.target_ref == 'https://example.org/paper' for rel in doc.part.rels.values())


def test_existing_superscript_citations_keep_body_size_and_links():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from app.services.report_typography import apply_report_typography
    doc = Document(); _configure(doc)
    _add_report_blocks(doc, ReportSectionSpec(section_key='progress', title='研究进展', blocks=[
        ReportBlock(kind='paragraph', text='已有研究[1]。')]),
        citation_urls={'1': 'https://example.org/paper'}, citation_numbers={'E-1': 1})
    link = doc.element.xpath('.//w:hyperlink[@w:anchor="evidence_1"]')[0]
    props = link.xpath('./w:r/w:rPr')[0]
    for tag, value in [('w:vertAlign', 'superscript'), ('w:position', '6')]:
        setting = OxmlElement(tag); setting.set(qn('w:val'), value); props.append(setting)
    apply_report_typography(doc)
    assert not link.xpath('.//w:vertAlign')
    assert not link.xpath('.//w:position')
    paragraph = link.getparent()
    assert link.xpath('.//w:sz/@w:val') == paragraph.xpath('./w:r/w:rPr/w:sz/@w:val')[:1]
    assert link.get(qn('w:anchor')) == 'evidence_1'


def test_source_note_citations_keep_the_note_size_after_writer_reopens_word():
    import fitz
    from app.services.report_typography import apply_report_typography
    from app.services.report_pdf_runtime import render_docx
    doc = Document(); _configure(doc)
    _add_report_blocks(doc, ReportSectionSpec(section_key='progress', title='研究进展', blocks=[
        ReportBlock(kind='paragraph', text='数据来源：样本文献[1]。')]),
        citation_urls={'1': 'https://example.org/paper'}, citation_numbers={'E-1': 1})
    apply_report_typography(doc)
    assert not doc.styles['Hyperlink'].element.xpath('./w:rPr/w:sz|./w:rPr/w:szCs')
    output = io.BytesIO(); doc.save(output)
    _, pdf = render_docx(output.getvalue())
    with fitz.open(stream=pdf, filetype='pdf') as rendered:
        spans = [s for page in rendered for block in page.get_text('dict')['blocks']
                 for line in block.get('lines', []) for s in line['spans']]
        citation = next(span for span in spans if span['text'] == '[1]')
        note = next(span for span in spans if '数据来源：' in span['text'])
        assert abs(citation['size'] - note['size']) < .1
        assert abs(citation['origin'][1] - note['origin'][1]) < .1


def test_every_candidate_word_citation_targets_its_matching_bibliography_entry(monkeypatch):
    from docx.oxml.ns import qn
    from app.services import topic_selection_report as renderer
    from app.services.public_cases import get_case
    _, detail = get_case('topic-urban-heat-v2')
    state = TopicSelectionState.model_validate(detail['state'])
    monkeypatch.setattr(renderer, '_charts', lambda *args: {})
    payload, _ = renderer._build_topic_docx_source(state)
    document = Document(io.BytesIO(payload))
    bookmarks = {node.get(qn('w:name')): node.getparent() for node in document.element.xpath('.//w:bookmarkStart')}
    candidates = []
    active = None
    for paragraph in document.paragraphs:
        text = ''.join(paragraph._p.xpath('.//w:t/text()'))
        if re.match(r'^候选题\s*[一二三四五\d]', text):
            active = []; candidates.append(active)
        elif paragraph.style.name == 'Heading 1':
            active = None
        for link in paragraph._p.xpath('./w:hyperlink[@w:anchor]'):
            if active is None:
                continue
            marker = ''.join(link.xpath('.//w:t/text()'))
            target = link.get(qn('w:anchor'))
            assert target in bookmarks
            assert ''.join(bookmarks[target].xpath('.//w:t/text()')).startswith(marker + ' ')
            assert not link.xpath('.//w:vertAlign')
            assert not link.xpath('.//w:position')
            assert link.xpath('.//w:sz/@w:val') == paragraph._p.xpath('./w:r/w:rPr/w:sz/@w:val')[:1]
            active.append(marker)
    assert len(candidates) == 5
    assert all(candidates)


def test_topic_text_exports_keep_body_size_citations_and_internal_targets():
    from app.services.topic_selection_report import _md_text, _html_text
    for render in (_md_text, _html_text):
        text = render('研究结论[1] [2]。')
        assert '<a class="citation"' in text
        assert '<sup' not in text
        assert '#ref-1' in text and '#ref-2' in text
        assert '[1]</a><a class="citation"' in text


def test_existing_public_cases_present_bracketed_body_size_links():
    from app.services.public_cases import CASE_IDS, get_case
    for case_id in CASE_IDS:
        _, detail = get_case(case_id)
        for markdown in (detail['output_markdown'], detail['state']['output_markdown']):
            assert re.search(r'<a class="citation" href="#ref-(\d+)">\[\1\]</a>', markdown), case_id
            assert '<sup' not in markdown, case_id
            assert not re.search(r'\[\d+\]\(#ref-\d+\)', markdown), case_id


def test_markdown_graphs_are_readable_without_image_encoding():
    text = '\n'.join(_chart_md({'key': 'theme_network', 'chart_type': 'network', 'title': '主题层级', 'data': {
        'root': '研究问题', 'nodes': [{'theme_id': 'T1', 'label': '主题一', 'seed_terms': ['关键词一', '关键词二']}], 'edges': []}}))
    assert 'data:image' not in text
    assert all(word in text for word in ('研究问题', '主题一', '关键词一', '关键词二'))


def test_appendix_layout_gives_long_topic_column_more_room_than_source():
    widths = _reference_table_widths(['检索主题', '来源', '时间范围', '纳入分析'])
    assert widths[0] > widths[1] * 2
    assert _reference_table_widths(['评估要点', '代表文献', '可用定位', '正文位置'])[0] > widths[1]


def test_every_tree_card_and_keyword_leaf_stays_inside_the_canvas(monkeypatch):
    from app.services import research_graph_rendering as render
    boxes = []
    canvases = []
    original = render._card_full_title
    def track(draw, box, *args, **kwargs):
        boxes.append(box)
        return original(draw, box, *args, **kwargs)
    monkeypatch.setattr(render, '_card_full_title', track)
    original_png = render._png
    def capture(image):
        canvases.append((image.size, list(boxes)))
        boxes.clear()
        return original_png(image)
    monkeypatch.setattr(render, '_png', capture)
    data = {'root': '研究问题', 'nodes': [{'theme_id': f'T{i}', 'parent_id': 'root' if i == 0 else 'T0',
        'label': '完整研究主题' * 4, 'seed_terms': ['关键词']} for i in range(8)]}
    from PIL import Image
    parts = render.topic_tree_parts(data)
    assert len(canvases) == 1
    assert sum(len(cards) for _, cards in canvases) >= 8
    assert all(0 <= x0 < x1 <= width and 0 <= y0 < y1 + 52 < height - 30 for (width, height), cards in canvases for x0, y0, x1, y1 in cards)


def test_attention_filter_includes_known_and_legacy_statuses(monkeypatch):
    from app.api import routes
    from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository
    repo = InMemoryTaskHistoryRepository()
    monkeypatch.setattr(routes, 'get_task_history_repo', lambda: repo)
    async def usage(scope):
        return {'occupied': 0, 'limit': 5}
    manager = SimpleNamespace(rate_limiter=SimpleNamespace(usage=usage))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(task_manager=manager, topic_task_manager=manager)))
    async def run():
        for i, status in enumerate(('awaiting_confirmation', 'failed', 'old_unknown_status', 'running', 'completed', 'cancelled')):
            await repo.create({'task_id': str(i), 'user_scope': 'owner', 'task_type': '智能选题', 'status': status})
        result = await routes.list_all_tasks(request, x_user_scope='owner', agent='all', status='attention', search='',
            days=None, created_after=None, created_before=None, page=1, page_size=20)
        assert {item['status'] for item in result['items']} == {'awaiting_confirmation', 'failed', 'old_unknown_status'}
        assert result['total'] == result['statistics']['status_groups']['attention'] == 3
    asyncio.run(run())
