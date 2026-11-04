import copy
import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import CrossLink, EvidenceRecord, ThemeNode, ThemeTree, TopicEvidenceChunk
from app.agents.topic_selection.discovery import ground_discovery, method_sources, method_evolution
from app.agents.content_assessment.schemas import ResearchNineFields, ContentAssessmentRunRequest
from app.agents.content_assessment.input_rules import infer_artifact_type


def fixture_state():
    state = TopicSelectionAgent().create_initial_state('研究数字图书馆文献检索与概率排序')
    state.theme_tree = ThemeTree(root='数字图书馆检索', nodes=[
        ThemeNode(theme_id='T1', label='文献检索', discipline_tags=['图书馆学']),
        ThemeNode(theme_id='T2', label='概率排序', discipline_tags=['数学'])])
    state.evidence_pool = [EvidenceRecord(evidence_id='E1', title='BM25 ranking', abstract=
        'The probabilistic BM25 ranking function estimates relevance from term frequency and document length.',
        discipline_tags=['数学'], theme_ids=['T2'])]
    state.crosslinks = [CrossLink(crosslink_id='XL1', source_theme_id='T1', target_theme_id='T2', title='排序关联', description='方法关联',
                                 relation_basis='概率相关性用于排序', transferable_method='BM25', support_evidence_ids=['E1'])]
    return state


def test_method_name_and_procedure_in_same_abstract_are_both_retained():
    state = fixture_state()
    state.evidence_pool[0].abstract = 'EVIDENT is a microfluidic system. Flow control uses a single pump and low-sorption materials.'
    records = method_sources(state, 'EVIDENT', [{'evidence_id': 'E1', 'source_method': 'EVIDENT',
        'quote': 'Flow control uses a single pump and low-sorption materials.'}])
    assert records and 'EVIDENT' in records[0]['method_name_quote']
    assert records[0]['quote'] in state.evidence_pool[0].abstract
    description = method_sources(state, 'Translated label', [{'evidence_id': 'E1', 'source_method': 'Translated label',
        'quote': 'Flow control uses a single pump and low-sorption materials.'}])
    assert description[0]['source_basis'] == 'quoted_method_description'
    assert description[0]['method_name_verified'] is False
    assert description[0]['source_method'] == description[0]['quote']


def test_compact_discovery_context_covers_themes_without_raw_audit_payload():
    from app.agents.topic_selection.discovery import discovery_context
    state = fixture_state()
    for i in range(40):
        state.evidence_pool.append(EvidenceRecord(evidence_id=f'E-{i}', title='Test', abstract='actual sentence. '*1000,
                                                  theme_ids=['T1' if i % 2 else 'T2']))
    context = discovery_context(state, limit=12)
    assert len(context['evidence']) == 12
    assert {t for e in context['evidence'] for t in e['theme_ids']} == {'T1', 'T2'}
    assert all(len(e['abstract']) <= 1800 and 'raw' not in e for e in context['evidence'])


def test_multiple_method_proposals_for_one_pair_keep_the_verified_source():
    state = fixture_state()
    valid = {'source_theme_id': 'T1', 'target_theme_id': 'T2', 'transferable_method': 'BM25',
             'source_method': 'BM25', 'method_evidence': [{'evidence_id': 'E1', 'source_method': 'BM25',
                 'quote': state.evidence_pool[0].abstract}]}
    invalid = {**valid, 'transferable_method': 'Unknown', 'method_evidence': []}
    ground_discovery(state, [valid, invalid])
    assert state.crosslinks[0].method_evidence
    assert state.crosslinks[0].transferable_method == 'BM25'


def test_secondary_method_cannot_certify_an_unsupported_primary_method():
    state = fixture_state()
    proposal = {'source_theme_id': 'T1', 'target_theme_id': 'T2',
                'source_method': 'model predictive control', 'transferable_method': '模型预测控制',
                'method_evidence': [{'evidence_id': 'E1', 'source_method': 'BM25',
                                     'quote': state.evidence_pool[0].abstract}]}
    ground_discovery(state, [proposal])
    assert state.crosslinks[0].status == 'candidate'
    assert not state.crosslinks[0].method_evidence


def test_primary_method_can_be_recovered_from_literal_acronyms_despite_word_wrapping():
    state = fixture_state()
    state.evidence_pool[0].abstract = ('The Back Propa-gation Neural Network (BPNN), '
        'Stacked Sparse AutoEncoder (SSAE), and Long Short-Term Memory (LSTM) '
        'are used to build the correlation model.')
    primary = 'Back Propagation Neural Network (BPNN), Stacked Sparse AutoEncoder (SSAE), and Long Short-Term Memory (LSTM)'
    proposal = {'source_theme_id': 'T1', 'target_theme_id': 'T2', 'source_method': primary,
                'transferable_method': '深度学习多信号融合模型', 'method_evidence': []}
    ground_discovery(state, [proposal])
    source = state.crosslinks[0].method_evidence[0]
    assert source['proposed_method_name'] == primary
    assert source['quote'] in state.evidence_pool[0].abstract
    assert 'Propa-gation' in source['quote']
    assert state.crosslinks[0].status == 'validated'


def test_confirmed_supplement_is_a_separate_locatable_material_source():
    from app.agents.content_assessment.schemas import ContentAssessmentState
    from app.agents.content_assessment.material_extraction import material_sources, locate
    state = ContentAssessmentState(raw_user_input='论文内容：比較图文检索的定位准确率。')
    state.nine_fields.technical_route = '采用CLIP图文对比学习并与BM25基线比较。'
    assert len(material_sources(state)) == 1
    state.nine_fields_confirmed = True
    source = material_sources(state)[1]
    assert source.file_id == 'user_confirmed_fields'
    assert '技术路线：' in source.content
    assert locate(source, state.nine_fields.technical_route)['source_document_id'] == 'user_confirmed_fields'


def test_absence_claim_with_intervening_method_is_limited_to_sample():
    from app.agents.topic_selection.discovery import scoped_opportunity
    assert scoped_opportunity('缺乏将离线分类模型应用于闭环控制系统的研究。') == '本次检索样本尚不足以确认将离线分类模型应用于闭环控制系统的研究。'
    assert scoped_opportunity('缺乏独立对照，需要补充实验。') == '缺乏独立对照，需要补充实验。'


def test_exploratory_candidates_keep_distinct_decision_directions():
    from app.agents.topic_selection.schemas import TopicCandidate
    for direction in ['方法改进', '数据与验证', '跨场景应用', '可靠性治理', '工程转化']:
        candidate = TopicCandidate(candidate_id='C1', title='探索方向：同一研究对象与证据范围', research_question=f'如何解决{direction}中的关键瓶颈？')
        assert TopicSelectionAgent._direction_from_candidate(candidate) == direction


def test_unrelated_abstract_is_not_a_prior_art_conclusion():
    from app.agents.content_assessment.graph import ContentAssessmentGraph
    from app.agents.content_assessment.schemas import ContentAssessmentState, Claim, ClaimEvidenceLink, EvidenceRecord
    state = ContentAssessmentState(raw_user_input='指南指标：图文检索证据定位准确率85%')
    state.claims = [Claim(claim_id='C1', text='拟实现图文检索', assertion_type='target')]
    state.evidence_records = [EvidenceRecord(evidence_id='E1', source_type='literature', title='Human face memory', abstract='Experiments on human face memory.', evidence_level='B')]
    state.claim_evidence_links = [ClaimEvidenceLink(claim_id='C1', evidence_id='E1', relation='not_comparable', evidence_level='B', confidence=0,
                                                    field_matrix={'object':'missing','method':'missing'})]
    ContentAssessmentGraph()._rebuild_novelty_decisions(state)
    assert state.novelty_decisions[0].label == '暂未发现可比证据'
    assert not state.novelty_decisions[0].evidence_ids


def test_grounded_method_keeps_transfer_validation_and_label_provenance():
    state = fixture_state()
    ground_discovery(state, [])
    link = state.crosslinks[0]
    assert link.status == 'validated'
    assert link.association_kind == 'interdisciplinary'
    assert link.method_evidence[0]['evidence_scope'] == 'abstract'
    assert link.discipline_sources[1]['source'] == 'retrieval_label'
    assert '验证' in link.validation_conditions
    assert '仍须' in link.uncertainty


def test_title_and_cooccurrence_cannot_certify_method():
    state = fixture_state()
    state.evidence_pool[0].abstract = ''
    ground_discovery(state, [])
    assert state.crosslinks[0].status == 'candidate'
    assert not state.crosslinks[0].method_evidence


def test_fabricated_quote_unknown_reference_and_unlocated_fulltext_are_rejected():
    state = fixture_state()
    for eid, quote in [('E1', 'BM25 improves every task without any validation.'), ('unknown', state.evidence_pool[0].abstract)]:
        assert not method_sources(state, 'BM25', [{'evidence_id': eid, 'quote': quote}])
    state.evidence_pool[0].abstract = ''
    state.evidence_chunks = [TopicEvidenceChunk(chunk_id='CH1', doc_id='D1', evidence_id='E1', text='BM25 uses probabilistic relevance for ranking documents.')]
    proposal = [{'evidence_id': 'E1', 'quote': state.evidence_chunks[0].text}]
    assert not method_sources(state, 'BM25', proposal)
    state.evidence_chunks[0].section_path = 'Methods / Ranking'
    result = method_sources(state, 'BM25', proposal)
    assert result[0]['locator']['section'] == 'Methods / Ranking'


def test_same_discipline_is_theme_association_and_generic_method_stays_tentative():
    state = fixture_state()
    state.theme_tree.nodes[1].discipline_tags = ['图书馆学']
    state.crosslinks[0].expansion_discipline = '图书馆学'
    ground_discovery(state, [])
    assert state.crosslinks[0].association_kind == 'theme_association'
    state.crosslinks[0].transferable_method = '机器学习'
    ground_discovery(state, [])
    assert state.crosslinks[0].status == 'candidate'


def test_method_evolution_requires_readable_evidence_and_marks_future_as_advice():
    state = fixture_state()
    rows = method_evolution(state, [{'period': 'future', 'finding': '验证新场景', 'evidence_ids': ['E1']},
                                     {'period': 'past', 'finding': '虚构历史', 'evidence_ids': ['missing']}])
    assert len(rows) == 1 and rows[0]['is_recommendation']


@pytest.mark.parametrize('disciplines', [['图书馆学'], ['图书馆学', '数学', '计算机科学']])
def test_explicit_participants_allow_single_or_multiple_disciplines(disciplines):
    state = fixture_state()
    proposal = {'crosslink_id':'XL1', 'transferable_method':'BM25', 'source_method':'BM25',
        'participating_disciplines':[{'discipline':name,'role':'core' if i == 0 else 'expansion','reason':'提供排序方法与验证设计'} for i,name in enumerate(disciplines)],
        'method_evidence':[{'evidence_id':'E1','source_method':'BM25','quote':state.evidence_pool[0].abstract}]}
    ground_discovery(state, [proposal])
    assert {p.discipline for p in state.crosslinks[0].participating_disciplines} == set(disciplines)


def test_main_opportunities_are_bounded_distinct_and_require_actual_method_sources():
    from app.agents.topic_selection.discovery import selected_opportunities
    state = fixture_state()
    prototype = state.crosslinks[0]
    state.crosslinks = []
    for i in range(5):
        row = prototype.model_copy(deep=True)
        row.crosslink_id = f'XL{i}'
        row.research_opportunity = f'拟比较不同检索场景{i}'
        row.method_evidence = [{'evidence_id':'E1','verified':True}]
        state.crosslinks.append(row)
    state.crosslinks.insert(0, prototype)  # no verified method source
    state.crosslinks.insert(2, state.crosslinks[1].model_copy(deep=True))  # duplicate
    chosen = selected_opportunities(state)
    assert len(chosen) == 3
    assert len({row.research_opportunity for row in chosen}) == 3
    assert all(row.method_evidence for row in chosen)


@pytest.mark.parametrize('kind', ['paper', 'project'])
def test_technical_information_alternatives_and_project_goal_not_substitute(kind):
    fields = ResearchNineFields(research_direction='检索', research_content='概率排序', research_goal='提高精度')
    assert fields.missing_for_type(kind) == ['key_technologies']
    fields.key_technologies = ['BM25']
    assert not fields.missing_for_type(kind)
    fields.key_technologies = []
    fields.technical_route = '概率排序与对照实验'
    assert not fields.missing_for_type(kind)


def test_auto_request_remains_accepted_and_classifies_primary_object():
    assert ContentAssessmentRunRequest(user_input='论文题目：检索研究', nine_fields={'research_direction': '检索'}).artifact_type == 'auto'
    assert infer_artifact_type('项目名称：检索研究\n参考文献：论文\n配套指南：目标', []) == 'project'
    assert infer_artifact_type('论文题目：检索研究\n项目资助信息：基金', []) == 'paper'


def test_absence_is_scoped_to_sample_and_short_theme_preserves_full_label():
    from app.agents.topic_selection.discovery import concise_theme, scoped_opportunity
    state = fixture_state()
    node = state.theme_tree.nodes[0]
    node.label = '围绕数字图书馆科研文献检索研究概率排序和图方法的可借鉴方法与验证边界'
    node.seed_terms = ['数字图书馆', '概率排序']
    assert concise_theme(node) == '数字图书馆／概率排序'
    assert len(node.label) > 24
    assert scoped_opportunity('现有研究均未实现实时闭环') == '本次检索样本尚不足以确认实时闭环'


def test_supplementary_numbers_and_guideline_are_retained_with_table_claims():
    from app.agents.content_assessment.material_extraction import retain_material_observations
    from app.agents.content_assessment.schemas import ContentAssessmentState, UploadedFileDigest, UploadedFileContext
    state = ContentAssessmentState(raw_user_input='项目材料')
    state.report_preferences.resolved_artifact_type = 'project'
    state.uploaded_file_context = UploadedFileContext(files=[
        UploadedFileDigest(file_id='main', name='主文件', content='项目名称：钠电诊断\n目标召回率85%；同SOC对照。'),
        UploadedFileDigest(file_id='supp', name='补充', content='构造记录报告79%；未达标。'),
        UploadedFileDigest(file_id='guide', name='配套指南', content='指南要求：应在负10摄氏度、同SOC下验证召回率，提供独立重复。')])
    retain_material_observations(state)
    assert len(state.output_json['material_observations']) == 3
    assert state.output_json['material_observations'][1]['source_document_id'] == 'supp'
    assert state.guideline_requirements[0].match_status == 'partial'
    assert '独立重复' in state.guideline_requirements[0].comparison_note
    assert state.guideline_requirements[0].source_locator
