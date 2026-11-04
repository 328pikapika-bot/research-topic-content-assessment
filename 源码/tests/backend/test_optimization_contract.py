"""Input and publication contracts for the five-step result experience."""
import asyncio
import copy

import pytest
from pydantic import ValidationError

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import ContentAssessmentRunRequest, ResearchNineFields, UploadedFileDigest, UploadedFileContext, UploadedFileRef


@pytest.mark.parametrize('kind,fields', [
    ('paper', dict(research_direction='检索', research_content='排序模型', technical_route='对照实验')),
    ('project', dict(research_direction='检索', research_content='排序模型', key_technologies=['概率排序'])),
    ('project', dict(research_direction='检索', research_content='排序模型', technical_route='对照实验')),
    ('guideline', dict(research_goal='提高召回', research_content='排序模型', evaluation_indicators=['召回率'], application_scenarios=['文献检索'])),
])
def test_only_applicable_information_is_required(kind, fields):
    request = ContentAssessmentRunRequest(user_input='评估对象', artifact_type=kind, nine_fields=fields)
    assert not request.nine_fields.missing_for_type(kind)
    assert request.nine_fields.timeline == ''
    assert ContentAssessmentRunRequest(user_input='评估对象', nine_fields=fields).artifact_type == 'auto'


def test_blank_fields_use_conditional_validation_and_auto_requests_require_type():
    fields = ResearchNineFields(research_goal='  ', evaluation_indicators=[' '])
    assert fields.missing_for_type('guideline') == ['research_goal', 'research_content', 'evaluation_indicators', 'application_scenarios']
    assert fields.missing_for_type('auto') == ['artifact_type']
    assert ContentAssessmentRunRequest(user_input='历史请求').artifact_type == 'auto'


def intake(kind, contents):
    graph = ContentAssessmentGraph()
    files = [UploadedFileDigest(file_id=f'F{i}', name=name, content=body, excerpt=body, status=status)
             for i, (name, body, status) in enumerate(contents)]
    state = graph.create_initial_state('请评估材料', artifact_type=kind,
        uploaded_files=[UploadedFileRef(file_id=f.file_id, name=f.name) for f in files])
    state.uploaded_file_context = UploadedFileContext(files=files)
    graph._document_intake(state)
    return graph, state


def test_same_object_attachments_and_reference_do_not_trigger_split():
    _, state = intake('project', [('项目申请书.txt', '项目名称：固态电池界面稳定化\n研究目标：拟降低阻抗', 'ready'),
        ('项目补充材料.txt', '项目名称：固态电池界面稳定化补充材料\n研究目标：计划增加对照', 'ready'),
        ('参考标准.txt', '研究对象：城市热岛观测\n标准规范监管政策合规', 'ready')])
    assert not any('拆为独立任务' in item for item in state.assessment_profile.pending_confirmation_fields)
    assert len(state.assessment_profile.document_records) == 3


def test_obviously_different_objects_request_separate_tasks():
    _, state = intake('project', [('项目申请书1.txt', '项目名称：固态电池界面稳定化\n研究目标：拟降低阻抗', 'ready'),
        ('项目申请书2.txt', '项目名称：城市热岛遥感观测\n研究目标：拟预测温度', 'ready')])
    assert any('拆为独立任务' in item for item in state.assessment_profile.pending_confirmation_fields)
    assert all(any(issue.code == 'multiple_objects' for issue in record.parse_issues) for record in state.assessment_profile.document_records)


@pytest.mark.parametrize('kind,name,body', [
    ('paper', '论文.txt', '论文题目：基于排序学习的文献检索\n摘要：结果和实验证据\n实验结果：结果\n参考文献：文献'),
    ('guideline', '指南.txt', '指南名称：科技文献检索研究\n指南要求：标准规范与合规指标'),
])
def test_selected_paper_and_guideline_are_primary_material(kind, name, body):
    graph, state = intake(kind, [(name, body, 'ready')])
    assert state.assessment_profile.document_records[0].content_available
    assert graph._project_document_id(state) == 'F0'
    assert not any('未识别出明确' in item for item in state.assessment_profile.pending_confirmation_fields)


@pytest.mark.parametrize('body,status,code', [('', 'ready', 'empty_content'), ('只有错误摘要', 'failed', 'parse_failed')])
def test_empty_or_failed_material_cannot_be_readable(body, status, code):
    _, state = intake('project', [('项目申请书.txt', body, status)])
    record = state.assessment_profile.document_records[0]
    assert not record.content_available and record.status == 'failed'
    assert any(issue.code == code for issue in record.parse_issues)


def test_published_topic_exports_ignore_later_result_mutations(monkeypatch):
    from test_topic_selection_priority import SearchWithFulltext, FulltextFixture
    from app.agents.topic_selection.agent import TopicSelectionAgent
    from app.services import topic_selection_report as report

    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    state = asyncio.run(agent.run(agent.create_initial_state('科技文献检索与数学排序方法', analysis_mode='fast')))
    before = {name: fn(state)[0] for name, fn in [('md', report.build_topic_markdown), ('html', report.build_topic_html)]}
    # Rendering input equality establishes the same DOCX/PDF source, without
    # launching Writer repeatedly in this unit test. Real PDF QA runs separately.
    frozen = report._bounded_export_state(state).model_dump()
    snapshot = copy.deepcopy(state.report_snapshot.model_dump())
    state.candidates[0].title = '未发布的修改'
    state.evidence_pool.clear()
    state.evidence_chunks.clear()
    state.crosslinks.clear()
    state.report_plan.sections.clear()
    state.report_plan.chart_specs.clear()
    state.versions.report_version = 'later'
    assert report._bounded_export_state(state).model_dump() == frozen
    assert report.build_topic_markdown(state)[0] == before['md']
    assert report.build_topic_html(state)[0] == before['html']
    assert state.report_snapshot.model_dump() == snapshot
