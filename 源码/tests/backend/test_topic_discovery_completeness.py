"""Regressions for single-theme discovery and legacy method chronology."""
import asyncio
import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.discovery import method_evolution, selected_opportunities
from app.agents.topic_selection.schemas import EvidenceRecord, ThemeNode, ThemeTree


def single_theme_state():
    state = TopicSelectionAgent().create_initial_state('研究概率排序对数字图书馆检索的作用', analysis_mode='fast')
    state.theme_tree = ThemeTree(root='文献检索', nodes=[ThemeNode(theme_id='T1', label='概率排序检索', discipline_tags=['图书馆学', '数学'])])
    state.evidence_pool = [EvidenceRecord(evidence_id='E1', title='Probabilistic ranking', abstract=
        'The BM25 ranking function estimates relevance using term frequency and document length.', theme_ids=['T1'])]
    return state


def opportunity(state, method='BM25'):
    return {'source_theme_id': 'T1', 'transferable_method': method, 'source_method': 'BM25',
            'core_discipline': '图书馆学', 'expansion_discipline': '数学',
            'description': '概率相关性用于文献排序', 'relation_basis': 'BM25以词频和文档长度估计相关性',
            'research_opportunity': f'比较{method}在长短文献混合检索中的适用范围',
            'method_evidence': [{'evidence_id': 'E1', 'source_method': 'BM25', 'quote': state.evidence_pool[0].abstract}]}


def test_single_theme_can_produce_multiple_grounded_method_opportunities():
    state = single_theme_state()
    agent = TopicSelectionAgent()
    async def model(*args, **kwargs):
        return {'gaps': [], 'cross_opportunities': [opportunity(state), opportunity(state, 'BM25长度归一化对照')]}
    agent._stage_model_json = model
    asyncio.run(agent._crosslink_gap(state))
    chosen = selected_opportunities(state)
    assert len(chosen) == 2
    assert all(link.source_theme_id == link.target_theme_id == 'T1' for link in chosen)
    assert all(link.support_evidence_ids == ['E1'] and link.method_evidence[0]['verified'] for link in chosen)
    related = [gap for gap in state.gaps if gap.crosslink_ids]
    assert len(related) == 2 and all(gap.theme_ids == ['T1'] for gap in related)
    assert all(len(gap.crosslink_ids) == 1 for gap in related)
    graph = next(spec for spec in agent._chart_specs(state) if spec['key'] == 'discipline_network')
    assert graph['data_status'] == 'ready'


def test_explicit_invalid_theme_is_not_replaced_with_a_fake_relation():
    state = single_theme_state()
    proposal = {**opportunity(state), 'target_theme_id': 'unknown'}
    agent = TopicSelectionAgent()
    async def model(*args, **kwargs):
        return {'gaps': [], 'cross_opportunities': [proposal]}
    agent._stage_model_json = model
    asyncio.run(agent._crosslink_gap(state))
    assert not state.crosslinks


def test_legacy_chronology_keeps_recent_results_in_present():
    state = single_theme_state()
    rows = method_evolution(state, [
        {'finding': '早期采用词频排序', 'evidence_ids': ['E1']},
        {'finding': '中期引入长度归一化', 'evidence_ids': ['E1']},
        {'finding': '近期采用概率排序', 'evidence_ids': ['E1']},
        {'finding': '未来比较跨库检索效果', 'evidence_ids': ['E1']},
        {'finding': '虚构方法', 'evidence_ids': ['unknown']},
    ])
    assert [row['period'] for row in rows] == ['past', 'past', 'present', 'future']
    assert [row['is_recommendation'] for row in rows] == [False, False, False, True]


def test_empty_opportunities_are_reviewed_once_without_fabricated_fallback():
    state = single_theme_state()
    state.analysis_policy.mode = 'deep'
    state.analysis_policy.model_enabled = True
    agent = TopicSelectionAgent()
    calls = []
    async def model(*args, **kwargs):
        calls.append(kwargs['prompt_version'])
        return {'gaps': [], 'cross_opportunities': [] if len(calls) == 1 else [opportunity(state)]}
    agent._stage_model_json = model
    asyncio.run(agent._crosslink_gap(state))
    assert len(calls) == 2 and len(selected_opportunities(state)) == 1


@pytest.mark.parametrize('first_response', ['length', 'inner_object', 'missing_array'])
def test_incomplete_opportunity_response_gets_one_bounded_repair(first_response):
    from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekJSONResult
    state = single_theme_state()
    state.analysis_policy.mode = 'deep'
    state.analysis_policy.model_enabled = True
    calls = []
    class Model:
        async def generate_json_with_metrics(self, **kwargs):
            calls.append(kwargs)
            valid = {'gaps': [], 'cross_opportunities': [opportunity(state)]}
            if len(calls) == 1:
                payload = opportunity(state) if first_response == 'inner_object' else valid.copy()
                if first_response == 'missing_array':
                    payload.pop('gaps')
            else:
                payload = {'result': valid}  # Existing legitimate wrapper remains supported.
            return DeepSeekJSONResult(payload=payload, metrics=DeepSeekCallMetrics(
                profile='test', model='test', stream=False, timeout_seconds=150, done_ms=1,
                finish_reason='length' if len(calls) == 1 and first_response == 'length' else 'stop'))
    agent = TopicSelectionAgent(model_client=Model())
    asyncio.run(agent._crosslink_gap(state))
    assert len(calls) == 2 and all(call['max_tokens'] == 6000 for call in calls)
    assert len(selected_opportunities(state)) == 1
    assert state.stage_artifacts['crosslink_gap']['generation_status'] == 'ready'
    asyncio.run(agent._crosslink_gap(state))
    assert len(calls) == 2  # Refresh/resume cannot repeat the content repair loop.


def test_opportunity_transport_failure_preserves_content_attempt_budget():
    from app.agents.topic_selection.recovery import GenerationRecoveryRequired
    from app.services.deepseek_client import DeepSeekClientError, DeepSeekCallMetrics, DeepSeekJSONResult
    state = single_theme_state()
    state.analysis_policy.mode = 'deep'
    state.analysis_policy.model_enabled = True
    calls = []
    class Model:
        async def generate_json_with_metrics(self, **kwargs):
            calls.append(kwargs)
            metrics = DeepSeekCallMetrics(profile='test', model='test', stream=False,
                timeout_seconds=150, done_ms=1)
            if len(calls) == 1:
                metrics.error_type = 'ConnectError'
                raise DeepSeekClientError('service offline', metrics=metrics)
            return DeepSeekJSONResult(payload={'gaps': [], 'cross_opportunities': [opportunity(state)]}, metrics=metrics)
    agent = TopicSelectionAgent(model_client=Model())
    with pytest.raises(GenerationRecoveryRequired):
        asyncio.run(agent._crosslink_gap(state))
    assert not state.stage_artifacts['crosslink_gap']['generation_attempts']
    asyncio.run(agent._crosslink_gap(state))
    assert state.stage_artifacts['crosslink_gap']['generation_attempts'] == ['initial']
    assert len(selected_opportunities(state)) == 1


def test_rich_collapsed_input_is_reviewed_but_genuinely_single_theme_is_kept():
    state = single_theme_state()
    state.raw_user_input = '研究范围包含不同临床来源的耐药菌株，比较联用方案与宿主范围的变化。' * 6 + '进一步研究受体变异的毒力代价。比较体外和动物模型中的差异。'
    assert len(state.raw_user_input) >= 180
    state.analysis_policy.mode = 'deep'
    state.analysis_policy.model_enabled = True
    agent = TopicSelectionAgent()
    calls = []
    async def model(*args, **kwargs):
        calls.append(kwargs['prompt_version'])
        return {'themes': [{'label': '联用与宿主范围'}, {'label': '受体变异与毒力代价'}] if len(calls) == 2 else [{'label': '宽泛总题'}]}
    agent._stage_model_json = model
    asyncio.run(agent._theme_decomposition(state))
    assert len(calls) == 2 and len(state.theme_tree.nodes) == 2
    state.raw_user_input = '单一问题'
    calls.clear()
    asyncio.run(agent._theme_decomposition(state))
    assert len(calls) == 1 and len(state.theme_tree.nodes) == 1


def test_unpublished_report_does_not_mark_task_completed(monkeypatch):
    from app.core import topic_task_manager as manager_module
    state = single_theme_state()
    state.report_plan.publication_status = 'blocked'
    recorded = []
    async def persist(**kwargs):
        recorded.append(kwargs['status'])
    class Repo:
        async def get(self, *args):
            return {'status': 'running', 'updated_at': 'test-revision'}
    async def run(self, current, **kwargs):
        return current
    async def release(*args):
        pass
    monkeypatch.setattr(manager_module, 'persist_topic_task_state', persist)
    monkeypatch.setattr(manager_module, 'get_task_history_repo', lambda: Repo())
    monkeypatch.setattr(manager_module.TopicSelectionService, 'run', run)
    manager = manager_module.TopicTaskManager()
    monkeypatch.setattr(manager._rate_limiter, 'release', release)
    asyncio.run(manager._run_pipeline('local-regression', 'test', state))
    assert recorded == ['running', 'queued']
