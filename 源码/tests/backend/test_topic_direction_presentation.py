import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import TopicCandidate, ScoreBreakdown


@pytest.mark.parametrize('preference,expected', [('balanced', 3), ('recommended', 5), ('exploratory', 0)])
def test_direction_quota_does_not_promote_scientific_evidence(preference, expected):
    agent = TopicSelectionAgent()
    state = agent.create_initial_state('城市热暴露与绿地结构研究', output_preference=preference)
    state.candidates = [TopicCandidate(candidate_id=f'C{i}', title=f'具体研究问题{i}', research_question=f'问题{i}',
        convergence_valid=i < 4, validation_status='evidence_insufficient', recommendation_level='pending_evidence') for i in range(1, 6)]
    state.score_breakdowns = [ScoreBreakdown(candidate_id=f'C{i}', total=100 - i) for i in range(1, 6)]
    agent._apply_output_preference(state)
    assert sum(item.direction_type == 'recommended' for item in state.candidates) == expected
    assert all(item.validation_status == 'evidence_insufficient' and item.recommendation_level == 'pending_evidence' for item in state.candidates)
    assert [item.candidate_id for item in state.candidates] == ['C1', 'C2', 'C3', 'C4', 'C5']
    if preference == 'balanced':
        assert all(item.direction_type == 'recommended' for item in state.candidates[:3])


def test_legacy_missing_direction_stays_optional_and_unknown_is_not_promoted():
    agent = TopicSelectionAgent()
    state = agent.create_initial_state('城市热环境研究')
    state.candidates = [
        TopicCandidate(candidate_id='C1', title='城市热暴露机制研究', research_question='机制', convergence_valid=True),
        TopicCandidate(candidate_id='C2', title='探索方向：跨城迁移', research_question='迁移'),
        TopicCandidate(candidate_id='C3', title='历史研究', research_question='历史'),
    ]
    assert state.candidates[0].direction_type is None
    agent._assign_direction_types(state)
    assert [item.direction_type for item in state.candidates] == ['recommended', 'exploratory', None]
