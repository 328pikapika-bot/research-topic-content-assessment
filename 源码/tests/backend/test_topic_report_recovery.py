"""Publication resumes checked paragraphs and keeps citations field-specific."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest

from app.agents.topic_selection.scholarly_report import (
    field_refs, replace_paragraph, review_report, saved_review_draft,
    invalid_paragraphs, repair_paragraphs, valid_report, valid_source_basis,
)


def packet_and_draft():
    packet = {'question': '比较灌流方法', 'sources': [{'evidence_id': 'E1', 'title':'灌流监测', 'content': '采用灌流监测。'}, {'evidence_id': 'E2', 'title':'静态对照', 'content': '采用静态对照。'}], 'candidates': [{'id': 'C1'}]}
    draft = {key: [{'text': '研究采用灌流监测。', 'evidence_ids': ['E1']}] for key in ('summary', 'progress', 'opportunities', 'recommendations')}
    draft['candidates'] = [{'id':'C1','reason':'已有研究采用灌流监测。','design':'拟比较灌流条件。','conditions':'建议固定监测条件。','evidence_ids':['E1']}]
    return packet, draft


def test_repair_replaces_just_one_fields_references_without_accumulating_sources():
    _, draft = packet_and_draft()
    candidate = draft['candidates'][0]
    replace_paragraph(candidate, 'design', '拟设置静态对照。', ['E2'])
    assert field_refs(candidate, 'design') == ['E2']
    assert field_refs(candidate, 'reason') == ['E1']
    replace_paragraph(candidate, 'design', '拟比较灌流条件。', ['E1'])
    assert candidate['evidence_ids'] == ['E1']


def test_source_review_cache_only_skips_identical_previously_checked_paragraphs():
    packet, draft = packet_and_draft()
    state = SimpleNamespace(stage_artifacts={})
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            calls.append(kwargs['payload']['paragraphs'])
            return {'checked': True, 'replacements': []}
    async def run():
        assert await review_report(Agent(), state, None, packet, draft)
        assert len(calls) == 2
        assert await review_report(Agent(), state, None, packet, draft)
        assert len(calls) == 2
        changed = deepcopy(draft)
        changed['candidates'][0]['design'] = '拟比较灌流速度。'
        assert await review_report(Agent(), state, None, packet, changed)
        assert len(calls) == 3
        assert [(row['key'],row['field']) for row in calls[-1]] == [('candidates','design')]
    asyncio.run(run())


def test_legacy_review_restore_does_not_mark_unchecked_batches_as_verified():
    packet, draft = packet_and_draft()
    artifacts = {'reader_draft_result': draft, 'source_review_batches': [{'checked':False,'replacements':[]}]}
    assert saved_review_draft(artifacts, packet) == draft
    assert not artifacts.get('verified_paragraph_hashes')


def test_partial_failed_draft_resumes_without_regenerating_good_sections():
    packet, draft = packet_and_draft()
    draft['candidates'][0]['design'] = '拟以FIC≤0.5为协同阈值。'
    restored = saved_review_draft({'reader_draft_result': draft}, packet)
    assert restored == draft
    assert not valid_report(restored, packet)
    targets = invalid_paragraphs(restored, packet)
    assert [(r['key'], r['field']) for r in targets] == [('candidates', 'design')]
    assert '0.5' in ' '.join(targets[0]['validation_reasons'])


def test_numeric_substrings_and_evidence_identifiers_do_not_support_thresholds():
    packet, draft = packet_and_draft()
    packet['sources'][0].update(year=2024, content='灌流编号123，观察20组样本。')
    draft['candidates'][0]['design'] = '拟以细菌载量降低2 log10作为疗效阈值。'
    assert not valid_report(draft, packet)


def test_targeted_repair_receives_precise_failure_and_runs_once_per_paragraph():
    packet, draft = packet_and_draft()
    draft['candidates'][0]['design'] = '拟以FIC≤0.5为协同阈值。'
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            calls.append(kwargs['payload'])
            return {'text': draft['candidates'][0]['design'], 'evidence_ids': ['E1']}
    async def run():
        state = SimpleNamespace(stage_artifacts={})
        attempted = set()
        result = await repair_paragraphs(Agent(), state, None, packet, draft, attempted)
        result = await repair_paragraphs(Agent(), state, None, packet, result, attempted)
        assert not valid_report(result, packet)
        assert len(calls) == 1
        assert calls[0]['paragraph_to_repair']['validation_reasons']
        assert 'text' not in calls[0]['paragraph_to_repair']
        assert result['summary'] == draft['summary']
    asyncio.run(run())


def test_review_repair_does_not_cache_a_rejected_replacement():
    packet, draft = packet_and_draft()
    state = SimpleNamespace(stage_artifacts={})
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            if 'paragraph_to_repair' in kwargs['payload']:
                return None
            rows = kwargs['payload']['paragraphs']
            target = next((r for r in rows if r['key'] == 'summary'), None)
            return {'checked': True, 'replacements': [dict(target, text='准确率99.9%。')] if target else []}
    assert asyncio.run(review_report(Agent(), state, None, packet, draft)) is None
    from app.agents.topic_selection.scholarly_report import paragraph_hash, paragraph_rows
    bad = next(r for r in paragraph_rows(state.stage_artifacts['report_publish']['reviewed_draft']) if r['key'] == 'summary')
    assert paragraph_hash(bad, packet) not in state.stage_artifacts['report_publish']['verified_paragraph_hashes']


def test_verbatim_basis_rejects_invented_source_threshold():
    packet, _ = packet_and_draft()
    result = {'evidence_ids': ['E1'], 'source_basis': [{'evidence_id': 'E1', 'quote': '采用灌流监测'}]}
    assert valid_source_basis(result, packet)
    result['source_basis'][0]['quote'] = '采用灌流监测，FIC≤0.5。'
    assert not valid_source_basis(result, packet)


def test_auxiliary_quote_format_does_not_discard_reviewable_fixed_prose():
    packet, draft = packet_and_draft()
    draft['candidates'][0]['design'] = '拟以FIC≤0.5为协同阈值。'
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            if 'paragraph_to_repair' in kwargs['payload']:
                return {'text': '拟比较灌流条件。', 'evidence_ids': ['E1'],
                    'source_basis': [{'evidence_id': 'E1', 'quote': '采用灌流...监测。'}]}
            return {'checked': True, 'replacements': []}
    async def run():
        state = SimpleNamespace(stage_artifacts={})
        result = await repair_paragraphs(Agent(), state, None, packet, draft)
        assert state.stage_artifacts['report_publish']['paragraph_repairs'][0]['basis_status'] == 'rejected'
        assert not state.stage_artifacts['report_publish'].get('verified_paragraph_hashes')
        assert result['candidates'][0]['design'] == '拟比较灌流条件。'
        assert await review_report(Agent(), state, None, packet, result)
        assert state.stage_artifacts['report_publish']['verified_paragraph_hashes']
    asyncio.run(run())


def test_legacy_duplicate_titles_recover_only_the_actual_title_blocker():
    from app.agents.topic_selection.agent import TopicSelectionAgent
    from app.agents.topic_selection.schemas import TopicCandidate
    agent = TopicSelectionAgent()
    state = agent.create_initial_state('噬菌体联用研究')
    state.candidates = [TopicCandidate(candidate_id=f'C{i}', title='探索方向：噬菌体联用的待研究问题',
        display_title=f'探索方向：噬菌体联用的{direction}', research_object='噬菌体联用', research_question=direction)
        for i, direction in enumerate(('可靠性治理', '工程转化'), 1)]
    state.quality_gate.blockers = ['候选选题语义标题重复', '证据池尚未锁定']
    agent._restore_distinct_candidate_titles(state)
    assert len({c.title for c in state.candidates}) == 2
    assert state.quality_gate.blockers == ['证据池尚未锁定']
    assert all(not c.convergence_valid for c in state.candidates)


@pytest.mark.parametrize('mode', ['completed', 'exception', 'preflight'])
def test_model_calls_keep_report_checkpoint_dictionary_identity(mode):
    from app.agents.topic_selection.agent import TopicSelectionAgent
    from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekJSONResult

    class Model:
        async def generate_json_with_metrics(self, **kwargs):
            if mode == 'exception':
                raise RuntimeError('model unavailable')
            return DeepSeekJSONResult(payload={'checked': True}, metrics=DeepSeekCallMetrics(
                profile='test', model='test', stream=False, timeout_seconds=150, done_ms=1))

    agent = TopicSelectionAgent(model_client=Model())
    state = agent.create_initial_state('检查报告恢复记录', analysis_mode='deep')
    state.analysis_policy.max_retries = 0
    if mode == 'preflight':
        state.analysis_policy.model_preflight_status = 'unavailable'
    checkpoint = state.stage_artifacts.setdefault('report_publish', {})
    checkpoint['verified_paragraph_hashes'] = ['previous']

    async def run():
        for index in range(2):
            await agent._stage_model_json(state, None, stage_key='report_publish',
                prompt_version='topic-source-review-test', purpose='来源核对',
                payload={}, expected_keys={'checked'})
            assert state.stage_artifacts['report_publish'] is checkpoint
            checkpoint['reviewed_draft'] = {'batch': index}
        assert state.stage_artifacts['report_publish']['reviewed_draft'] == {'batch': 1}
        assert checkpoint['verified_paragraph_hashes'] == ['previous']
        assert checkpoint['model_status'] == ('completed' if mode == 'completed' else 'degraded')
    asyncio.run(run())


def test_scientific_numbers_and_named_products_use_entire_tokens():
    from app.agents.report_validation import numeric_values, scientific_identifiers
    assert numeric_values('0.5ha p0.01 HI0.01 1.2e-3mg 2024 20142021年') == ['0.5', '0.01', '0.01', '1.2e-3', '2024', '20142021']
    assert numeric_values('Landsat 8/9 Sentinel-2A BM25 316L caspase3') == []
    assert scientific_identifiers('Landsat 8/9 Sentinel-2A') == ['landsat8', 'landsat9', 'sentinel2a']


def test_raw_user_conditions_support_proposals_but_not_literature_facts():
    from app.agents.topic_selection.scholarly_report import text_issues
    packet, _ = packet_and_draft()
    packet['user_conditions'] = '用户要求分析3座城市，采用Landsat 8/9。'
    text = '拟分析3座城市，采用Landsat 8/9进行方法对照。'
    assert not text_issues(text, ['E1'], packet, proposal=True)
    assert not text_issues(text, ['E1'], packet, proposal=False)
    assert text_issues('已有研究分析3座城市，采用Landsat 8/9。', ['E1'], packet, proposal=True)
    packet['sources'][0]['content'] = '20142021年分析p0.01，面积0.5ha。'
    assert text_issues('采用2014—2021年数据，p<0.01。', ['E1'], packet)
    assert not text_issues('分析面积0.5ha。', ['E1'], packet)
    assert text_issues('以0.3ha为阈值。', ['E1'], packet)


def test_summary_checks_proposed_conditions_per_clause_without_supporting_paper_claims():
    from app.agents.topic_selection.scholarly_report import text_issues
    packet, _ = packet_and_draft()
    packet['question'] = '2015—2025年城市热环境研究'
    packet['user_conditions'] = '采用Landsat 8/9、Sentinel-3，研究2015—2025年。'
    packet['sources'][0]['content'] = '使用Landsat8进行地表温度分析，采用20142021年数据。'
    valid = '已有研究使用Landsat8分析地表温度；本研究拟研究2015—2025年，采用Landsat 8/9、Sentinel-3比较城市热环境。'
    assert not text_issues(valid, ['E1'], packet)
    assert text_issues('已有研究使用Sentinel-3分析地表温度。本研究拟比较城市热环境。', ['E1'], packet)
    assert text_issues('本研究拟采用Landsat 8/9，已有研究使用Sentinel-3分析地表温度。', ['E1'], packet, proposal=True)
    assert text_issues('已有研究采用2015—2025年数据。', ['E1'], packet)
    assert text_issues('已有研究采用2014—2021年数据。', ['E1'], packet)
    assert text_issues('拟使用Sentinel-2分析植被指数。', ['E1'], packet, proposal=True)


def test_strict_structure_rejects_missing_chapters_and_candidate_identity():
    from app.agents.topic_selection.scholarly_report import report_structure, structure_issues
    packet, draft = packet_and_draft()
    assert report_structure(draft, packet)
    for key in ('summary', 'opportunities', 'recommendations', 'candidates'):
        broken = deepcopy(draft)
        broken[key] = []
        assert not report_structure(broken, packet)
        assert structure_issues(broken, packet)
    broken = deepcopy(draft)
    broken['candidates'][0]['id'] = 'another-candidate'
    assert not report_structure(broken, packet)
    packet['required_candidate_fields'] = ['title', 'reason', 'design', 'conditions']
    assert not report_structure(draft, packet)


def test_measurement_or_other_metric_cannot_support_acceptance_threshold():
    from app.agents.topic_selection.scholarly_report import text_issues
    packet, _ = packet_and_draft()
    packet['sources'][0]['content'] = '模型RMSE=2.5°C，面积2.5ha。'
    cutoff = '拟比较迁移模型，若RMSE增量小于2.5°C，则认为具有可接受的迁移性能。'
    assert text_issues(cutoff, ['E1'], packet, proposal=True)
    packet['sources'][0]['content'] = '采用RMSE增量小于2.5ha作为阈值。'
    assert text_issues(cutoff, ['E1'], packet, proposal=True)
    packet['user_conditions'] = '用户指定以RMSE增量<2.5℃作为待验证的判定阈值。'
    assert not text_issues(cutoff, ['E1'], packet, proposal=True)
    assert text_issues('已有研究采用RMSE增量小于2.5°C作为判定阈值。', ['E1'], packet)
    packet['sources'][0]['content'] = '采用RMSE增量<2.5℃作为迁移判定阈值。'
    assert not text_issues(cutoff, ['E1'], packet, proposal=True)


def test_repair_missing_candidate_title_preserves_checked_fields_and_refs():
    from app.agents.topic_selection.scholarly_report import repair_structure, report_structure
    packet, draft = packet_and_draft()
    packet['required_candidate_fields'] = ['title', 'reason', 'design', 'conditions']
    draft['candidates'][0]['field_evidence_ids'] = None
    current = SimpleNamespace(stage_artifacts={})
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            calls.append(kwargs)
            return {'candidate': {'title':'灌流条件对照研究','reason':'不应覆盖旧段落','evidence_ids':['E2']}}
    async def run():
        repaired = await repair_structure(Agent(), current, None, packet, draft)
        assert report_structure(repaired, packet)
        row = repaired['candidates'][0]
        assert row['reason'] == draft['candidates'][0]['reason']
        assert field_refs(row, 'reason') == ['E1']
        assert field_refs(row, 'title') == ['E2']
        assert row['id'] == 'C1'
        await repair_structure(Agent(), current, None, packet, repaired)
        assert len(calls) == 1
    asyncio.run(run())


def test_source_change_invalidates_only_paragraphs_using_that_source():
    from app.agents.topic_selection.scholarly_report import paragraph_hash, paragraph_rows
    packet, draft = packet_and_draft()
    replace_paragraph(draft['candidates'][0], 'design', '拟比较静态条件。', ['E2'])
    before = {tuple((r['key'],r['index'],r['field'])):paragraph_hash(r, packet) for r in paragraph_rows(draft)}
    packet['sources'][1]['content'] = '采用静态和灌流条件对照。'
    changed = [r for r in paragraph_rows(draft) if before[(r['key'],r['index'],r['field'])] != paragraph_hash(r,packet)]
    assert [(r['key'],r['field']) for r in changed] == [('candidates','design')]


def test_content_repair_has_two_durable_modes_and_transport_keeps_budget():
    packet, draft = packet_and_draft()
    draft['candidates'][0]['design'] = '拟以0.5为阈值。'
    current = SimpleNamespace(stage_artifacts={})
    calls = []
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            calls.append(kwargs)
            return {'text':'拟以0.5为阈值。','evidence_ids':['E1']}
    async def run():
        result = draft
        for _ in range(3):
            result = await repair_paragraphs(Agent(), current, None, packet, result)
            result = await repair_paragraphs(Agent(), current, None, packet, result, fact_rewrite=True)
        assert len(calls) == 2
        assert result['summary'] == draft['summary']
    asyncio.run(run())
