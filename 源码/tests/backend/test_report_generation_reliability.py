"""Regressions for the October 16 saved assessment and topic failures."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agents.report_validation import review_response
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.scholarly_report import draft, publication_gaps, review_source_assertions, point_packet
from app.agents.topic_selection.scholarly_report import repair_structure, repair_paragraphs, source_packet, text_issues, normalize_report
from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekClientError, DeepSeekJSONResult
from test_scholarly_reports import sample
from test_topic_report_recovery import packet_and_draft


@pytest.mark.parametrize('value,reason', [
    ({'data': {'review': {'checked': True, 'replacements': []}}}, ''),
    ({'checked': False, 'replacements': []}, 'review_not_approved'),
    ({'checked': 'true', 'replacements': []}, 'missing_boolean_checked'),
    ({'checked': True}, 'missing_replacements_array'),
    ({'checked': True, 'replacements': [{'key': 'other', 'index': 0, 'text': '', 'evidence_ids': []}]}, 'invalid_replacement_location'),
    ({'checked': True, 'replacements': [{'key': ['summary'], 'index': 0, 'text': '', 'evidence_ids': []}]}, 'invalid_replacement_location'),
    ({'checked': True, 'replacements': [{'key': 'summary', 'index': 0, 'text': '内容', 'evidence_ids': [{}]}]}, 'invalid_replacement_content'),
])
def test_review_protocol_recovers_wrappers_without_inferring_approval(value, reason):
    result, actual = review_response(value, {('summary', 0)})
    assert actual == reason
    assert (result is not None) == (not reason)


def test_explicit_source_rejection_deletion_survives_protocol_normalization():
    result, reason = review_response({'checked': True, 'replacements': [
        {'key': 'summary', 'index': 0, 'text': ''}]}, {('summary', 0)})
    assert not reason
    assert result['replacements'][0] == {'key': 'summary', 'index': 0, 'text': '', 'evidence_ids': []}


@pytest.mark.parametrize('name', ['Landsat8', 'Landsat 8', 'Landsat-8'])
def test_satellite_identifiers_are_not_experimental_measurements(name):
    packet, _ = packet_and_draft()
    packet['sources'][0]['content'] = 'Landsat8 thermal observations.'
    assert not text_issues(f'拟采用{name}分析热环境。', ['E1'], packet, proposal=True)
    assert text_issues('拟采用Landsat9分析热环境。', ['E1'], packet, proposal=True)
    assert text_issues(f'拟采用{name}且以8摄氏度为阈值。', ['E1'], packet, proposal=True)


def test_missing_candidates_only_generate_the_two_missing_units_and_keep_owned_order():
    packet, original = packet_and_draft()
    packet['question'] = '长三角高密度城市热暴露研究'
    packet['candidates'] = [{'id': f'C{i}', 'research_object': '长三角城市', 'question': f'比较变量{i}'} for i in range(1, 6)]
    original['candidates'] = [{**original['candidates'][0], 'id': f'C{i}'} for i in range(1, 4)]
    accepted = deepcopy(original)
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            payload = kwargs['payload']
            calls.append(payload)
            assert payload['source_material']['question'] == packet['question']
            return {'candidate': {'id': 'model-cannot-change-this', 'title': '热暴露比较',
                'reason': '研究采用灌流监测。', 'design': '拟比较变量。', 'conditions': '拟固定条件。', 'evidence_ids': ['E1']}}
    state = SimpleNamespace(stage_artifacts={})
    result = asyncio.run(repair_structure(Agent(), state, None, packet, original))
    assert len(calls) == 2
    assert [c['id'] for c in result['candidates']] == [f'C{i}' for i in range(1, 6)]
    assert result['candidates'][:3] == accepted['candidates']
    assert all(result[key] == accepted[key] for key in ('summary', 'progress', 'opportunities', 'recommendations'))


def test_mistyped_evidence_identifier_cannot_be_guessed_or_silently_removed():
    packet, value = packet_and_draft()
    packet['sources'][0]['evidence_id'] = 'E-8ceb8ccfeac3'
    value['summary'][0] = {'text': '依据E-8ceb8ccfe3ac开展比较。', 'evidence_ids': ['E-8ceb8ccfeac3']}
    result = normalize_report(value, packet)
    assert 'E-8ceb8ccfe3ac' in result['summary'][0]['text']
    assert any('内部编号' in reason for reason in text_issues(result['summary'][0]['text'], ['E-8ceb8ccfeac3'], packet))


def test_source_selection_retains_readable_citations_beyond_lexical_top24():
    from app.agents.topic_selection.agent import TopicSelectionAgent
    from app.agents.topic_selection.schemas import TopicCandidate, EvidenceRecord
    agent = TopicSelectionAgent()
    state = agent.create_initial_state('城市热暴露与地表温度的方法研究')
    state.candidates = [TopicCandidate(candidate_id='C1', title='方法迁移', research_question='跨城市预测', evidence_ids=['E-method', 'E-invented'])]
    state.evidence_pool = [EvidenceRecord(evidence_id=f'E{i}', title='城市热暴露与地表温度', abstract='研究采用遥感和地面观测分析城市热暴露与地表温度变化。' * 6) for i in range(30)]
    state.evidence_pool += [EvidenceRecord(evidence_id='E-method', title='Cross-city prediction', abstract='Domain-adversarial training combines spatiotemporal knowledge transfer with attention for traffic prediction.' * 3),
        EvidenceRecord(evidence_id='E-saved', title='Method validation', abstract='Station observations validate the remote sensing estimates across different surface conditions.' * 3)]
    state.stage_artifacts['report_publish'] = {'reader_draft_result': {'summary': [{'text': '保留的比较', 'evidence_ids': ['E-saved']}]}}
    packet = source_packet(state)
    ids = [row['evidence_id'] for row in packet]
    assert len(ids) == len(set(ids)) == 24
    assert {'E-method', 'E-saved'} <= set(ids)
    assert 'E-invented' not in ids


def test_repair_context_excludes_unverified_route_numbers_and_rejected_citations():
    packet, value = packet_and_draft()
    packet['question'] = '长三角夏季热暴露研究'
    packet['candidates'][0].update(question='跨城市预测的方法迁移', title='广州热预测',
        method_route='24小时且阈值30%', sample_boundary='广州', validation_path='5折验证')
    value['candidates'][0]['design'] = '采用30%阈值。'
    value['candidates'][0]['field_evidence_ids'] = {'design': ['E-mistyped'], 'reason': ['E1'], 'conditions': ['E1']}
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            calls.append(kwargs['payload'])
            return {'text': '拟比较灌流方法与静态对照。', 'evidence_ids': ['E1', 'E2']}
    asyncio.run(repair_paragraphs(Agent(), SimpleNamespace(stage_artifacts={}), None, packet, value))
    assert len(calls) == 1
    assert calls[0]['research_context'] == {'id': 'C1', 'question': '跨城市预测的方法迁移'}
    assert 'evidence_ids' not in calls[0]['paragraph_to_repair']
    assert calls[0]['question'] == packet['question']
    assert set(calls[0]['output_schema']) == {'text', 'evidence_ids'}


def test_candidate_titles_are_reviewed_against_user_scope_and_keep_candidate_identity():
    from app.agents.topic_selection.scholarly_report import review_report
    packet, value = packet_and_draft()
    packet['question'] = '长三角夏季热暴露研究'
    value['candidates'][0]['title'] = '广州高温预测研究'
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            rows = kwargs['payload']['paragraphs']
            calls.extend(rows)
            return {'checked': True, 'replacements': [dict(row, text='长三角高温预测研究') for row in rows if row['field'] == 'title']}
    state = SimpleNamespace(stage_artifacts={})
    result = asyncio.run(review_report(Agent(), state, None, packet, value))
    assert any(row['field'] == 'title' for row in calls)
    assert result['candidates'][0]['title'] == '长三角高温预测研究'
    assert result['candidates'][0]['id'] == 'C1'


def test_source_review_cache_expires_when_scope_review_rules_change(monkeypatch):
    from app.agents.topic_selection import scholarly_report as module
    packet, value = packet_and_draft()
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            calls.append(kwargs['payload'])
            return {'checked': True, 'replacements': []}
    state = SimpleNamespace(stage_artifacts={})
    async def run():
        assert await module.review_report(Agent(), state, None, packet, value)
        count = len(calls)
        assert await module.review_report(Agent(), state, None, packet, value)
        assert len(calls) == count
        monkeypatch.setattr(module, 'SOURCE_REVIEW_VERSION', 'updated-scope-review')
        assert await module.review_report(Agent(), state, None, packet, value)
        assert len(calls) == count * 2
    asyncio.run(run())


def prepared_assessment():
    state = sample()
    rows = {'progress': [{'text': '已有研究采用肝脏芯片灌流监测。', 'evidence_ids': ['E1']}],
        'comparison': [{'text': '本材料拟比较不同灌流条件下的读出。', 'evidence_ids': ['E1']}],
        'judgement': [{'text': '灌流监测为材料的条件比较提供了方法参照。', 'evidence_ids': ['E1']}],
        'evaluation': [{'text': '材料将灌流条件设为比较变量，有助于组织对照。', 'evidence_ids': ['E1']}]}
    state.output_json['scholarly_drafts'] = {'C1': deepcopy(rows)}
    state.output_json['scholarly_summary'] = deepcopy(rows['judgement'])
    return state, rows


def test_pending_conclusion_reviews_original_text_and_preserves_accepted_chapters(monkeypatch):
    state, chapters = prepared_assessment()
    conclusion = deepcopy(chapters['evaluation'])
    state.output_json['scholarly_synthesis_drafts'] = {'conclusion': conclusion}
    calls = []
    async def request(*args, **kwargs):
        calls.append(kwargs)
        assert '逐段核对下面评议' in kwargs['user_prompt']
        assert conclusion[0]['text'] in kwargs['user_prompt']
        return {'result': {'checked': True, 'replacements': []}} if len(calls) == 2 else {'checked': True}
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(draft(graph, state))
    assert len(calls) == 2
    assert state.output_json['scholarly_conclusion'] == conclusion
    assert state.output_json['scholarly_drafts']['C1'] == chapters
    assert state.output_json['scholarly_summary'] == chapters['judgement']


def test_point_review_outage_retains_draft_then_resumes_without_rewriting(monkeypatch):
    state, chapters = prepared_assessment()
    state.output_json['scholarly_conclusion'] = deepcopy(chapters['evaluation'])
    state.output_json['scholarly_source_reviews'] = {'C1': {
        'failure_kind': 'review_protocol', 'pending_draft': deepcopy(chapters)}}
    graph = ContentAssessmentGraph()
    calls = []
    approved = False
    async def request(*args, **kwargs):
        calls.append(kwargs)
        assert '逐段核对下面评议' in kwargs['user_prompt']
        return {'checked': True, 'replacements': []} if approved else None
    monkeypatch.setattr(graph, '_request_model_json', request)
    asyncio.run(draft(graph, state))
    assert len(calls) == 2
    assert publication_gaps(state)
    assert state.output_json['scholarly_source_reviews']['C1']['pending_draft'] == chapters
    approved = True
    asyncio.run(draft(graph, state))
    assert len(calls) == 3
    assert not publication_gaps(state)
    assert state.output_json['scholarly_drafts']['C1'] == chapters


def test_identical_assessment_paragraphs_reuse_source_check_but_changed_prose_is_reviewed(monkeypatch):
    state, chapters = prepared_assessment()
    packet = point_packet(state, state.claims[0])
    calls = []
    async def request(*args, **kwargs):
        calls.append(kwargs)
        return {'checked': True, 'replacements': []}
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, '_request_model_json', request)
    async def run():
        assert await review_source_assertions(graph, state, packet, chapters) == chapters
        assert await review_source_assertions(graph, state, packet, chapters) == chapters
        assert len(calls) == 1
        changed = deepcopy(chapters)
        changed['comparison'][0]['text'] = '本材料拟设置灌流对照以比较监测读出。'
        assert await review_source_assertions(graph, state, packet, changed) == changed
        assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize('status_code', [500, 401])
def test_transient_error_does_not_poison_later_model_calls_but_authentication_stops(monkeypatch, status_code):
    from app.agents.content_assessment import graph as module
    monkeypatch.setattr(module, 'CONTENT_ASSESSMENT_LLM_ENABLED', True)
    state, graph = sample(), ContentAssessmentGraph()
    calls = []
    class Model:
        async def generate_json_with_metrics(self, **kwargs):
            calls.append(kwargs)
            metrics = DeepSeekCallMetrics(profile='test', model='test', stream=False,
                timeout_seconds=10, status_code=status_code if len(calls) == 1 else 200)
            if len(calls) == 1:
                raise DeepSeekClientError('test failure', metrics=metrics)
            return DeepSeekJSONResult(payload={'checked': True, 'replacements': []}, metrics=metrics)
    graph._model_client = Model()
    async def run():
        kwargs = dict(stage='report', system_prompt='review', user_prompt='review', max_tokens=100)
        assert await graph._request_model_json(state, **kwargs) is None
        result = await graph._request_model_json(state, **kwargs)
        assert bool(result) == (status_code == 500)
        assert len(calls) == (2 if status_code == 500 else 1)
    asyncio.run(run())


def test_topic_model_transport_unwraps_review_before_schema_check():
    from app.agents.topic_selection.agent import TopicSelectionAgent
    class Model:
        async def generate_json_with_metrics(self, **kwargs):
            return DeepSeekJSONResult(payload={'data': {'checked': True, 'replacements': []}},
                metrics=DeepSeekCallMetrics(profile='test', model='test', stream=False, timeout_seconds=10, done_ms=1))
    agent = TopicSelectionAgent(model_client=Model())
    state = agent.create_initial_state('检查来源核对协议', analysis_mode='deep')
    result = asyncio.run(agent._stage_model_json(state, None, stage_key='report_publish',
        prompt_version='test-review', purpose='来源核对', payload={}, expected_keys={'checked', 'replacements'}))
    assert result == {'checked': True, 'replacements': []}
    assert state.stage_artifacts['report_publish']['model_response_diagnostics'][-1]['response_keys'] == ['data']
