"""Citation labels must retain their brackets after Markdown renders links."""
from html.parser import HTMLParser

import pytest

from app.agents.content_assessment.report_composition import markdown
from app.agents.content_assessment.schemas import ContentAssessmentState, ReportBlock, ReportSectionSpec, ReportSnapshot
from app.services import export_service


class CitationLabels(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href', '').startswith('#ref-'):
            self.active = [attrs['href'], '']

    def handle_data(self, data):
        if self.active is not None:
            self.active[1] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.active is not None:
            self.links.append(tuple(self.active))
            self.active = None


@pytest.mark.parametrize('with_blocks', [True, False])
def test_assessment_citations_keep_brackets_body_size_and_matching_targets(with_blocks, monkeypatch):
    def section(key, title, text):
        return ReportSectionSpec(section_key=key, title=title, content=text,
                                 blocks=[ReportBlock(text=text)] if with_blocks else [])

    state = ContentAssessmentState(raw_user_input='引用格式回归', report_snapshot=ReportSnapshot(
        snapshot_id='citation-test', generated_at='2026-11-13', template_version='historical',
        citation_numbers={'E1': 1, 'E2': 2}, sections=[
            section('progress', '相关研究', '已有研究 E1 E2。'),
            section('references', '参考文献', '[1] 第一篇文献\n\n[2] 第二篇文献'),
        ]))
    text = markdown(state)
    expected = '<a class="citation" href="#ref-1">[1]</a><a class="citation" href="#ref-2">[2]</a>'
    assert expected in text
    assert '<sup' not in text
    assert '[1](#ref-1)' not in text
    references = text.split('## 参考文献', 1)[1]
    assert '<sup' not in references
    assert '<a id="ref-1"></a>\n\n[1] ' in references
    assert '<a id="ref-2"></a>\n\n[2] ' in references

    monkeypatch.setattr(export_service, 'ensure_snapshot', lambda _: None)
    monkeypatch.setattr(export_service, 'prepare_exports', lambda _: None)
    rendered, _ = export_service.build_html(state)
    labels = CitationLabels()
    labels.feed(rendered)
    assert labels.links[:2] == [('#ref-1', '[1]'), ('#ref-2', '[2]')]
    assert '&lt;sup' not in rendered


def test_html_export_accepts_old_citation_links_and_escapes_unrelated_html(monkeypatch):
    from app.agents.content_assessment import report_composition
    state = ContentAssessmentState(raw_user_input='历史引用', report_snapshot=ReportSnapshot(
        snapshot_id='old-citation-test', generated_at='2026-11-13', citation_numbers={'E1': 1}))
    monkeypatch.setattr(export_service, 'ensure_snapshot', lambda _: None)
    monkeypatch.setattr(export_service, 'prepare_exports', lambda _: None)
    monkeypatch.setattr(report_composition, 'markdown', lambda *args, **kwargs:
                        '# 历史报告\n已有研究[1](#ref-1)。<img src="example">\n')
    rendered, _ = export_service.build_html(state)
    labels = CitationLabels()
    labels.feed(rendered)
    assert labels.links == [('#ref-1', '[1]')]
    assert '&lt;img' in rendered
