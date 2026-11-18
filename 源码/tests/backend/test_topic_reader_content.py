from app.agents.topic_selection.presentation import (
    candidate_reader_prose,
    candidate_reader_report_fields,
    normalize_candidate_reader_content,
    opportunity_reader_type,
)
from app.agents.topic_selection.schemas import (
    CrossLink,
    DisciplineParticipant,
    TopicCandidate,
)


def test_candidate_reader_content_keeps_only_substantive_candidate_citations():
    content = normalize_candidate_reader_content(
        {
            "reason": {"text": "该方向把两类研究方法用于同一问题。", "evidence_ids": ["E1", "E2"]},
            "research_content": {
                "research_object": {"text": "比较城市热暴露", "evidence_ids": ["E2"]},
                "core_question": {"text": "分析绿地结构差异", "evidence_ids": ["E1"]},
                "method_route": {"text": "结合遥感和街区数据", "evidence_ids": ["E2"]},
            },
            "evidence_summary": {"text": "综合文献指出两类指标可用于城市热环境分析。", "evidence_ids": ["E2"]},
        },
        allowed_evidence_ids={"E1", "E2"},
        substantive_evidence_ids={"E1"},
    )

    assert content["reason"]["text"]
    assert content["reason"]["evidence_ids"] == ["E1", "E2"]
    assert content["research_content"]["research_object"]["evidence_ids"] == ["E2"]
    assert content["research_content"]["core_question"]["evidence_ids"] == ["E1"]
    assert content["research_content"]["method_route"]["evidence_ids"] == ["E2"]
    assert content["evidence_summary"] == {"text": "", "evidence_ids": []}


def test_candidate_copy_removes_internal_codes_and_process_diagnostics():
    result = candidate_reader_prose("比较热暴露的遥感方法[4]；关联主题 T1、T2；空白 G3；尚未证明候选假设。")
    assert "比较热暴露的遥感方法[4]" in result
    assert not any(token in result for token in ("T1", "T2", "G3", "关联主题", "空白", "尚未证明"))


def test_fallback_preserves_valid_inline_sources_and_removes_empty_parentheses():
    content = normalize_candidate_reader_content(
        {"reason": {"text": "已有研究（E-source）支持方法；待验证的方向（）", "evidence_ids": []}},
        allowed_evidence_ids={"E-source"}, substantive_evidence_ids=set(),
    )
    assert content["reason"] == {"text": "已有研究支持方法；待验证的方向", "evidence_ids": ["E-source"]}


def test_candidate_reader_removes_empty_punctuation_only_parentheses():
    assert candidate_reader_prose("现有方法（, ）可用；研究设计（，、）待验证。") == "现有方法可用；研究设计待验证。"


def test_context_only_candidate_shows_cited_background_without_claiming_support():
    from app.agents.topic_selection.agent import TopicSelectionAgent
    from app.agents.topic_selection.schemas import CandidateEvidenceLink, EvidenceRecord, TopicSelectionState

    state = TopicSelectionState(raw_user_input="比较研究方法")
    state.evidence_pool = [EvidenceRecord(evidence_id="E1", title="相关研究", abstract="讨论相近方法的应用。")]
    candidate = TopicCandidate(candidate_id="C1", title="方法比较", research_question="如何比较两种方法？", evidence_ids=["E1"],
                               evidence_links=[CandidateEvidenceLink(link_id="C1:E1", candidate_id="C1",
                                   evidence_id="E1", relation="context")])
    candidate.reader_content = TopicSelectionAgent._normalize_candidate_reader_content(candidate, {}, state)
    assert candidate.reader_content["background_summary"]["evidence_ids"] == ["E1"]
    fields = candidate_reader_report_fields(candidate, {"E1": 1})
    assert next(field["value"] for field in fields if field["label"] == "相关文献").endswith("[1]")
    assert not any(field["label"] == "文献依据" for field in fields)


def test_scientific_lifetime_measure_is_preserved_in_reader_and_export():
    from app.services.topic_selection_report import _reader_visible
    text = '比较组件I-V、T80和失效记录。[1][2]'
    assert candidate_reader_prose(text) == text
    assert _reader_visible(text) == text


def test_report_candidate_citations_cannot_escape_candidate_evidence_set():
    candidate = TopicCandidate(
        candidate_id="C1",
        title="城市热暴露研究",
        research_question="比较绿地结构差异",
        method_route="结合街区绿地与温度观测进行分层分析",
        innovation="将绿地结构差异放入跨街区比较。",
        evidence_ids=["E1"],
        reader_content={
            "reason": {"text": "选题理由。", "evidence_ids": ["E2"]},
            "research_content": {
                "research_object": {"text": "研究城市街区。", "evidence_ids": ["E1"]},
            },
            "evidence_summary": {"text": "文献支持该比较。", "evidence_ids": ["E2"]},
        },
    )

    fields = candidate_reader_report_fields(candidate, {"E1": 3, "E2": 8})

    assert next(item["value"] for item in fields if item["label"] == "选题理由") == "选题理由。"
    assert next(item["value"] for item in fields if item["label"] == "研究内容") == "研究对象：研究城市街区。 [3]\n核心问题：比较绿地结构差异\n方法路线：结合街区绿地与温度观测进行分层分析"
    assert all("[8]" not in item["value"] for item in fields)
    assert not any(item["label"] == "文献依据" for item in fields)


def test_opportunity_type_uses_only_disciplines_with_supporting_literature():
    link = CrossLink(
        crosslink_id="X1",
        source_theme_id="T1",
        target_theme_id="T2",
        title="城市热环境方法衔接",
        description="关联卫星温度和街区观测。",
        support_evidence_ids=["E1", "E2"],
        participating_disciplines=[
            DisciplineParticipant(discipline="地理学", role="core", evidence_ids=["E1"]),
            DisciplineParticipant(discipline="公共卫生", role="expansion", evidence_ids=["E2"]),
            DisciplineParticipant(discipline="经济学", role="expansion", evidence_ids=["E3"]),
        ],
    )

    assert opportunity_reader_type(link) == "交叉研究方向"

    link.participating_disciplines = [
        DisciplineParticipant(discipline="地理学", role="core", evidence_ids=["E1"]),
        DisciplineParticipant(discipline="公共卫生", role="expansion", evidence_ids=["E3"]),
    ]
    assert opportunity_reader_type(link) == "研究切入点"
