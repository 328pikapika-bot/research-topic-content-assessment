"""Candidate references must survive repeated review and final rendering."""
from types import SimpleNamespace

from app.agents.topic_selection.scholarly_report import (
    normalize_report, prose, resolve_report_names, text_issues,
)


def test_review_normalization_uses_actual_title_and_is_idempotent():
    packet = {'sources': [], 'candidates': [{'id': 'C1'}, {'id': 'C2', 'title': '协同效应比较'}]}
    draft = {'recommendations': [{'text': '建议在““C1””的基础上开展候选C2所描述的研究。'}],
             'candidates': [{'id': 'C1', 'title': '噬菌体抗性代价评估'}, {'id': 'C2'}]}
    normalized = normalize_report(draft, packet)
    assert normalized['recommendations'][0]['text'] == '建议在“噬菌体抗性代价评估”的基础上开展候选“协同效应比较”所描述的研究。'
    for _ in range(10):
        assert normalize_report(normalized, packet) == normalized
    assert draft['recommendations'][0]['text'].startswith('建议在““C1')


def test_missing_title_does_not_accumulate_quotes_or_pass_validation():
    packet = {'question': '噬菌体研究', 'sources': [], 'candidates': [{'id': 'C1', 'title': ''}]}
    draft = {'recommendations': [{'text': '建议研究C1。'}]}
    assert normalize_report(normalize_report(draft, packet), packet) == draft
    assert any('内部编号' in reason for reason in text_issues('建议研究C1。', [], packet, proposal=True))
    assert any('空引号' in reason for reason in text_issues('建议研究““””所描述的方法。', [], packet, proposal=True))


def test_rendering_resolves_legacy_nested_quotes_without_changing_research_data():
    state = SimpleNamespace(candidates=[SimpleNamespace(candidate_id='C1', title='抗性代价评估', display_title='')],
                            report_plan=SimpleNamespace(citation_map={'E-known': 6}))
    text = '建议开展候选' + '“' * 8 + 'C1' + '”' * 8 + '所描述的研究，比较存活率与时间-杀菌曲线。E-known'
    result = prose(text, state)
    assert result == '建议开展候选“抗性代价评估”所描述的研究，比较存活率与时间-杀菌曲线。[6]'
    assert prose(result, state) == result


def test_identifier_boundaries_and_real_quotations_are_preserved():
    text = 'C1与C10；AC1、C1q保持原名；“存活率”按定义报告，观察值为99.9%。'
    result = resolve_report_names(text, [], [{'id': 'C1', 'title': '抗性研究'}, {'id': 'C10', 'title': '协同研究'}])
    assert result == '“抗性研究”与“协同研究”；AC1、C1q保持原名；“存活率”按定义报告，观察值为99.9%。'
