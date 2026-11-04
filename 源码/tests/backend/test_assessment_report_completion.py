"""Report failures observed in the organ-chip guideline, without live models."""
import asyncio
from copy import deepcopy
import json
import pytest

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.scholarly_report import (
    draft, point_packet, review_source_assertions, validated_paragraphs,
)
from test_report_generation_reliability import prepared_assessment


def test_absence_guard_does_not_cross_material_clause_or_quoted_paper():
    state, _ = prepared_assessment()
    state.evidence_records[0].abstract += ' There is no standard methodology for microfluidic TEER measurements.'
    packet = point_packet(state, state.claims[0])
    text = '与现有文献相比，本指南草案回应了《Liver chip perfusion》中指出的“没有标准方法”这一问题。'
    assert validated_paragraphs([{'text': text, 'evidence_ids': ['E1']}], packet)
    assert not validated_paragraphs([{'text': '现有研究尚未开展灌流验证。', 'evidence_ids': ['E1']}], packet)


def test_reviewer_receives_full_material_and_distinguishes_both_attributions(monkeypatch):
    state, chapters = prepared_assessment()
    state.raw_user_input += '拟开展三家实验室的复现性验证。'
    packet = point_packet(state, state.claims[0])
    assert '三家实验室' in packet['material_design']['submitted_text']
    async def request(_state, **kwargs):
        prompt = kwargs['user_prompt']
        assert '不要求材料方案也出现在文献中' in prompt
        assert '指南已写出的条款是草案内容' in prompt
        assert '三家实验室' in prompt
        return {'checked': True, 'replacements': []}
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    assert asyncio.run(review_source_assertions(graph, state, packet, chapters)) == chapters


def test_empty_rejected_unit_is_repaired_without_rewriting_other_units(monkeypatch):
    state, chapters = prepared_assessment()
    calls = []
    async def request(_state, **kwargs):
        prompt = kwargs['user_prompt']
        calls.append(prompt)
        if '局部修正' in prompt:
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            assert payload['missing_units'] == ['comparison']
            assert all('text' not in item for item in payload['validation_feedback'])
            return {'comparison': [{'text': '材料拟将灌流条件作为比较变量，所引研究采用流量监测。', 'evidence_ids': ['E1']}]}
        if len(calls) == 1:
            return {'checked': True, 'replacements': [{'key': 'comparison', 'index': 0, 'text': '', 'reason': '修正归属'}]}
        return {'checked': True, 'replacements': []}
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    result = asyncio.run(review_source_assertions(graph, state, point_packet(state, state.claims[0]), chapters))
    assert len(calls) == 3
    assert result['progress'] == chapters['progress']
    assert result['judgement'] == chapters['judgement']
    assert result['comparison'][0]['text'].startswith('材料拟将')
    assert state.output_json['scholarly_source_reviews']['C1']['status'] == 'completed'


def test_invalid_editorial_synthesis_uses_exact_accepted_analysis(monkeypatch):
    state, chapters = prepared_assessment()
    state.output_json.pop('scholarly_summary')
    original = deepcopy(chapters)
    async def request(_state, **kwargs):
        # Responsive model keeps producing an unbound reference. It must never
        # enter the report; approved point prose can supply the overview.
        return {'summary': [{'text': '虚构结论。', 'evidence_ids': ['invented']}],
                'conclusion': [{'text': '虚构结论。', 'evidence_ids': ['invented']}]}
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(draft(graph, state))
    assert state.output_json['scholarly_summary'] == original['judgement']
    assert state.output_json['scholarly_conclusion'] == original['evaluation']
    assert state.output_json['scholarly_drafts']['C1'] == original
    assert state.output_json['scholarly_synthesis_completion']['summary']['mode'] == 'reviewed_extract'


def test_synthesis_outage_does_not_claim_success(monkeypatch):
    state, _ = prepared_assessment()
    state.output_json.pop('scholarly_summary')
    async def request(*args, **kwargs):
        return None
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(draft(graph, state))
    assert not state.output_json.get('scholarly_summary')
    assert not state.output_json.get('scholarly_conclusion')


@pytest.mark.parametrize('artifact_type', ['paper', 'project', 'guideline'])
def test_three_material_types_publish_from_accepted_analyses_after_editorial_failure(monkeypatch, artifact_type):
    state, chapters = prepared_assessment()
    state.report_preferences.artifact_type = artifact_type
    state.report_preferences.resolved_artifact_type = artifact_type
    state.output_json.pop('scholarly_summary')
    async def request(_state, **kwargs):
        if '逐段核对下面评议' in kwargs['user_prompt']:
            return {'checked': True, 'replacements': []}
        if '撰写科研评估报告第四章' in kwargs['user_prompt']:
            return {key: [{'text': '材料以灌流条件为研究对象，来源《Liver chip perfusion》记录了灌流监测。',
                           'evidence_ids': ['E1']}]
                    for key in ('selection', 'mapping', 'comparability', 'quality', 'limits')}
        return {'summary': [{'text': '无依据的摘要。', 'evidence_ids': ['wrong']}],
                'conclusion': [{'text': '无依据的结论。', 'evidence_ids': ['wrong']}]}
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(graph._report_stage(state))
    assert state.report_snapshot is not None
    assert state.output_json['scholarly_drafts']['C1'] == chapters
    assert state.output_json.get('generation_recovery', {}).get('status') != 'interrupted'
    assert '无依据的摘要' not in state.output_markdown


@pytest.mark.parametrize('response', [
    {},
    {'selection': []},
    {'selection': [{'text': '检索共纳入999篇。', 'evidence_ids': []}]},
    {'mapping': [{'text': '虚构来源。', 'evidence_ids': ['invented']}]},
])
def test_missing_or_invalid_method_sections_use_facts_and_publish(monkeypatch, response):
    from app.agents.content_assessment.report_methods import METHOD_KEYS
    state, chapters = prepared_assessment()
    state.output_json['scholarly_conclusion'] = deepcopy(chapters['evaluation'])
    original = deepcopy(state.model_dump())
    graph = ContentAssessmentGraph()

    async def request(_state, **kwargs):
        if '撰写科研评估报告第四章' in kwargs['user_prompt']:
            return deepcopy(response)
        return {'checked': True, 'replacements': []}

    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(graph._report_stage(state))
    assert state.report_snapshot is not None
    assert all(state.output_json['scholarly_methods_completion'][key]['status'] == 'fallback'
               for key in METHOD_KEYS)
    assert '检索共纳入999篇' not in state.output_markdown
    assert '虚构来源' not in state.output_markdown
    assert state.output_json['scholarly_drafts'] == original['output_json']['scholarly_drafts']
    for field in ('claims', 'evidence_records', 'claim_evidence_links', 'query_plans',
                  'novelty_decisions', 'stage_trace'):
        assert state.model_dump()[field] == original[field]


@pytest.mark.parametrize('review_mode', ['rejected', 'deleted', 'unsupported_absence'])
def test_source_rejection_falls_back_then_freezes_without_rewriting_accepted_text(monkeypatch, review_mode):
    from app.agents.content_assessment.report_methods import METHOD_KEYS, structured_method_rows
    state, chapters = prepared_assessment()
    state.output_json['scholarly_conclusion'] = deepcopy(chapters['evaluation'])
    original = deepcopy(state.output_json['scholarly_drafts'])
    graph = ContentAssessmentGraph()

    async def request(_state, **kwargs):
        if '撰写科研评估报告第四章' in kwargs['user_prompt']:
            rows = structured_method_rows(state)
            rows['quality'] = [{'text': '代表资料提供的摘要用于研究方向与方法比较。', 'evidence_ids': ['E1']}]
            if review_mode == 'unsupported_absence':
                rows = {key: [{'text': '现有研究尚未开展灌流验证。', 'evidence_ids': ['E1']}]
                        for key in METHOD_KEYS}
            return rows
        if review_mode == 'deleted':
            packet = json.loads(kwargs['user_prompt'].rsplit('\n', 1)[1])
            return {'checked': True, 'replacements': [
                {'key': row['key'], 'index': row['index'], 'text': '', 'reason': '来源不支持当前表述'}
                for row in packet['paragraphs']]}
        if review_mode == 'unsupported_absence':
            return {'checked': True, 'replacements': []}
        return {'checked': False, 'replacements': []}

    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(graph._report_stage(state))
    assert state.report_snapshot is not None
    assert state.output_json['scholarly_drafts'] == original
    assert all(state.output_json['scholarly_methods_completion'][key]['status'] == 'fallback'
               for key in METHOD_KEYS)
    assert set(state.output_json['scholarly_methods_source_reviews']) == set(METHOD_KEYS)
    assert all(word not in state.output_markdown for word in
               ('source_review_rejected', '来源校核失败', '方法章节兜底', '现有研究尚未开展灌流验证'))
    snapshot = deepcopy(state.report_snapshot.model_dump())

    async def unavailable(*args, **kwargs):
        raise AssertionError('A frozen report must not call the model again')

    monkeypatch.setattr(graph, '_request_model_json', unavailable)
    asyncio.run(graph._report_stage(state))
    assert state.report_snapshot.model_dump() == snapshot


@pytest.mark.parametrize('failure', ['empty', 'timeout', 'connection'])
def test_methods_model_outage_retains_report_recovery_and_can_resume(monkeypatch, failure):
    from app.agents.content_assessment.report_methods import structured_method_rows
    from app.agents.content_assessment.recovery import retry_stage
    from app.agents.content_assessment import graph as graph_module
    state, chapters = prepared_assessment()
    state.output_json['scholarly_conclusion'] = deepcopy(chapters['evaluation'])
    original = deepcopy(state.model_dump())
    graph = ContentAssessmentGraph()

    class UnavailableClient:
        async def generate_json_with_metrics(self, **kwargs):
            if failure == 'timeout':
                raise TimeoutError('test timeout')
            if failure == 'connection':
                raise ConnectionError('test connection')
            # Match the empty-result signal from the production client.
            from app.services.deepseek_client import DeepSeekClientError
            raise DeepSeekClientError('empty model result')

    graph._model_client = UnavailableClient()
    monkeypatch.setattr(graph_module, 'CONTENT_ASSESSMENT_LLM_ENABLED', True)
    asyncio.run(graph._report_stage(state))
    assert state.report_snapshot is None
    assert state.output_json['generation_recovery']['reason'] == 'report_drafting'
    assert retry_stage(state) == 'report'
    assert not state.output_json['scholarly_methods']
    assert state.output_json['scholarly_drafts'] == original['output_json']['scholarly_drafts']
    for field in ('claims', 'evidence_records', 'claim_evidence_links', 'query_plans', 'novelty_decisions'):
        assert state.model_dump()[field] == original[field]

    async def restored(_state, **kwargs):
        if '撰写科研评估报告第四章' in kwargs['user_prompt']:
            return structured_method_rows(state)
        return {'checked': True, 'replacements': []}

    monkeypatch.setattr(graph, '_request_model_json', restored)
    asyncio.run(graph._report_stage(state))
    assert state.report_snapshot is not None
    assert state.output_json['scholarly_drafts'] == original['output_json']['scholarly_drafts']


def test_selection_fallback_never_adds_query_candidate_or_selected_counts():
    from app.agents.content_assessment.report_methods import structured_method_rows
    from test_research_report_v6 import research_sample
    state = research_sample('guideline')
    state.query_plans[0].candidate_count = 987
    state.query_plans[0].selected_count = 654
    state.query_plans.append(state.query_plans[0].model_copy(deep=True))
    selection = structured_method_rows(state)['selection']
    text = ' '.join(row['text'] for row in selection)
    assert (state.query_plans[0].query_group or state.query_plans[0].query) in text
    assert all(str(count) not in text for count in (987, 654, 1974, 1308))
    assert '可能包含重复资料' in text


def test_method_audits_keep_each_sections_own_review_and_accepted_prose(monkeypatch):
    from app.agents.content_assessment.report_methods import draft_methods, methods_complete, structured_method_rows
    state, _ = prepared_assessment()
    generated = structured_method_rows(state)
    generated['quality'] = [{'text': '代表资料提供的摘要用于研究方向与方法比较。', 'evidence_ids': ['E1']}]
    graph = ContentAssessmentGraph()

    async def request(_state, **kwargs):
        prompt = kwargs['user_prompt']
        if '撰写科研评估报告第四章' in prompt:
            return deepcopy(generated)
        if '"key": "quality"' in prompt:
            return {'checked': False, 'replacements': []}
        return {'checked': True, 'replacements': []}

    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(draft_methods(graph, state))
    assert methods_complete(state)
    audits = state.output_json['scholarly_methods_source_reviews']
    assert audits['quality']['failure_kind'] == 'source_rejected'
    for key in ('selection', 'mapping', 'comparability', 'limits'):
        assert not audits[key]['failure_kind']
        assert state.output_json['scholarly_methods_completion'][key]['status'] == 'completed'
        assert [(row['text'], row['evidence_ids']) for row in state.output_json['scholarly_methods'][key]] == [
            (row['text'], row['evidence_ids']) for row in generated[key]]
