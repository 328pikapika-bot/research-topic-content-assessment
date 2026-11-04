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
    assert content["reason"]["evidence_ids"] == ["E1"]
    assert content["research_content"]["research_object"]["evidence_ids"] == []
    assert content["research_content"]["core_question"]["evidence_ids"] == ["E1"]
    assert content["research_content"]["method_route"]["evidence_ids"] == []
    assert content["evidence_summary"] == {"text": "", "evidence_ids": []}


def test_candidate_copy_removes_internal_codes_and_process_diagnostics():
    result = candidate_reader_prose("比较热暴露的遥感方法[4]；关联主题 T1、T2；空白 G3；尚未证明候选假设。")
    assert "比较热暴露的遥感方法[4]" in result
    assert not any(token in result for token in ("T1", "T2", "G3", "关联主题", "空白", "尚未证明"))


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
