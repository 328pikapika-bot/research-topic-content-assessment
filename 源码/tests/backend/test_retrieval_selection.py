"""Fused sources obey per-topic limits and keep material/evidence associations."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from app.agents.retrieval_selection import limit_assessment_literature, limit_topic_literature
from app.agents.content_assessment.schemas import (
    ContentAssessmentState, Claim, ClaimEvidenceLink, EvidenceRecord, EvidenceChunk, QueryPlan,
)
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import EvidenceRecord as TopicEvidence, ThemeTree, ThemeNode, TopicEvidenceChunk


def test_fused_assessment_sources_are_scoped_and_capped_without_losing_material():
    state = ContentAssessmentState(raw_user_input='organ chip; sodium battery')
    state.claims = [Claim(claim_id='C1', text='organ chip microfluidics'), Claim(claim_id='C2', text='sodium battery')]
    state.query_plans = [QueryPlan(claim_id='C1', claim_ids=['C1'], query='organ chip', query_group='methods')]
    material = EvidenceRecord(evidence_id='material', source_type='project_material', title='Input')
    state.evidence_records = [material] + [EvidenceRecord(
        evidence_id=f'chip-{i}', source_type='literature', title=f'Organ chip microfluidics {i}',
        claim_ids=['C1', 'C2'], query_group='methods', abstract='Organ chip perfusion.') for i in range(36)] + [
        EvidenceRecord(evidence_id='battery', source_type='literature', title='Sodium battery', claim_ids=['C1', 'C2']),
        EvidenceRecord(evidence_id='noise', source_type='literature', title='Bird migration', claim_ids=['C1', 'C2']),
    ]
    state.evidence_chunks = [EvidenceChunk(chunk_id=e.evidence_id, evidence_id=e.evidence_id, claim_ids=['C1', 'C2'], supports_claim_ids=['C1', 'C2']) for e in state.evidence_records]
    state.claim_evidence_links = [ClaimEvidenceLink(claim_id='C2', evidence_id='chip-0', relation='not_comparable', evidence_level='C', confidence=0)]
    limit_assessment_literature(state)
    assert state.output_json['retrieval_stats']['per_point_counts'] == {'C1': 30, 'C2': 1}
    assert state.query_plans[0].selected_count == 30
    assert len(state.evidence_records) == 32
    assert material in state.evidence_records
    assert not state.claim_evidence_links
    assert all(e.claim_ids == ['C1'] for e in state.evidence_records if e.evidence_id.startswith('chip-'))
    assert next(e for e in state.evidence_records if e.evidence_id == 'battery').claim_ids == ['C2']
    kept = {e.evidence_id for e in state.evidence_records}
    assert {c.evidence_id for c in state.evidence_chunks} == kept
    assert all(c.supports_claim_ids == ['C1'] for c in state.evidence_chunks if c.evidence_id.startswith('chip-'))


def test_one_source_can_belong_to_two_related_points_and_remains_one_record():
    state = ContentAssessmentState(raw_user_input='study')
    state.claims = [Claim(claim_id='C1', text='organ chip'), Claim(claim_id='C2', text='chip perfusion')]
    state.evidence_records = [EvidenceRecord(evidence_id='shared', source_type='literature', title='Organ chip perfusion', claim_ids=['C1', 'C2'])]
    limit_assessment_literature(state)
    assert len(state.evidence_records) == 1
    assert state.evidence_records[0].claim_ids == ['C1', 'C2']
    assert state.output_json['retrieval_stats']['unique_evidence_count'] == 1


def test_topic_limit_is_applied_after_fusion_and_retains_input_chunks():
    state = TopicSelectionAgent().create_initial_state('organ chip perfusion and sodium battery')
    state.theme_tree = ThemeTree(root='Research', nodes=[ThemeNode(theme_id='T1', label='organ chip'), ThemeNode(theme_id='T2', label='sodium battery')])
    state.evidence_pool = [TopicEvidence(evidence_id=f'E{i}', title='Organ chip perfusion', theme_ids=['T1']) for i in range(35)] + [
        TopicEvidence(evidence_id='shared', title='Organ chip and sodium battery', theme_ids=['T1', 'T2'])]
    state.evidence_chunks = [TopicEvidenceChunk(chunk_id='input', doc_id='input', text='input material'), TopicEvidenceChunk(chunk_id='overflow', doc_id='paper', evidence_id='E34', text='paper')]
    limit_topic_literature(state)
    assert sum('T1' in e.theme_ids for e in state.evidence_pool) == 25
    assert sum('T2' in e.theme_ids for e in state.evidence_pool) == 1
    assert len({e.evidence_id for e in state.evidence_pool}) == len(state.evidence_pool)
    assert [c.chunk_id for c in state.evidence_chunks] == ['input']


@pytest.mark.parametrize('initial_count, supplemental_related', [(1, True), (22, True), (1, False)])
def test_graph_refills_scarce_points_once_and_never_pads_unrelated_sources(monkeypatch, initial_count, supplemental_related):
    from app.agents.content_assessment import graph as module, public_literature
    from app.common.retrieval.schemas import StandardPaper, SearchResponse
    graph = module.ContentAssessmentGraph()
    state = graph.create_initial_state('Organ chip perfusion study')
    state.claims = [Claim(claim_id='C1', text='organ chip perfusion', searchable_terms=['organ', 'chip', 'perfusion']),
                    Claim(claim_id='C2', text='organ chip monitoring', searchable_terms=['organ', 'chip', 'monitoring'])]
    requests = []
    def search(request):
        requests.append(request)
        supplemental = request.context['query_group'] == '研究要点补充检索'
        papers = [StandardPaper(paper_id=f'paper-{i}', doi=f'10.1/paper-{i}',
            title=f'Organ chip perfusion monitoring {i}', abstract='Organ chip perfusion methods and monitoring results.')
            for i in range(25 if supplemental else initial_count)]
        if supplemental and not supplemental_related:
            papers = [StandardPaper(paper_id='noise', title='Galaxy morphology', abstract='Stellar orbits and dark matter.')]
        return SearchResponse(success=True, total=len(papers), papers=papers)
    monkeypatch.setattr(module, 'LITERATURE_SEARCH_API_URL', 'http://retrieval.invalid')
    monkeypatch.setattr(module.LiteratureSearchClient, 'search', lambda self, request: search(request))
    monkeypatch.setattr(graph.fulltext_client, 'search_intents', AsyncMock(return_value=[]))
    monkeypatch.setattr(graph, '_request_model_json', AsyncMock(return_value=None))
    monkeypatch.setattr(public_literature, 'supplement', AsyncMock())
    asyncio.run(graph._retrieval(state))
    supplemental_requests = [r for r in requests if r.context['query_group'] == '研究要点补充检索']
    assert len(supplemental_requests) == (0 if initial_count >= 20 else 4)
    assert len(requests) <= 16
    counts = state.output_json['retrieval_stats']['per_point_counts']
    expected = 22 if initial_count >= 20 else 25 if supplemental_related else 1
    assert counts == {'C1': expected, 'C2': expected}
    assert len(state.evidence_records) == expected  # shared papers are not duplicated
    assert all(set(e.claim_ids) == {'C1', 'C2'} for e in state.evidence_records)
    assert state.output_json['retrieval_source_order']['C1']
    assert all(r.paper_num == 40 for r in requests)
