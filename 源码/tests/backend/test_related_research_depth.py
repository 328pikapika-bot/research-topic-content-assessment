import asyncio
import copy

import pytest

from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.comparison_context import evidence_context, material_context, selected_passages
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.prompts import comparison_prompt
from app.agents.content_assessment.reader_comparison import (
    reader_repair_targets, save_reader_analysis, save_reader_comparison,
)
from app.agents.content_assessment.schemas import (
    Claim, ContentAssessmentState, EvidenceRecord, QueryPlan, UploadedFileDigest,
)
from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekJSONResult


def example():
    material = "指南规定将TEER与示踪通量纳入屏障检测流程。\n要求记录实验温度和流量，用于批次质量控制。"
    state = ContentAssessmentState(raw_user_input=material)
    state.report_preferences.resolved_artifact_type = "guideline"
    claim = Claim(claim_id="C1", text="TEER检测与批次质量控制", source_span=material.splitlines()[0],
                  source_document_id="user_material", searchable_terms=["TEER", "实验温度", "流量"])
    source = EvidenceRecord(evidence_id="E1", title="TEER测量方法", source_type="literature",
                            abstract="文献将TEER测量集成到器官芯片。通过阻抗谱分析屏障功能和温度对测量的影响。")
    state.claims, state.evidence_records = [claim], [source]
    return state, claim, source


def contact(state, source, text="该文献将TEER测量与器官芯片集成，用阻抗谱分析屏障功能；指南则将TEER和示踪通量纳入流程，并记录实验温度。两者在屏障检测和测量条件控制上衔接，文献提供测量方法基础，指南侧重流程与质量控制。"):
    return {"text": text, "material_quotes": state.raw_user_input.splitlines(), "literature_quotes": [source.abstract]}


def synthesis(state, source):
    return {"claim_id": "C1", "material_summary": {
        "text": "指南将TEER与示踪通量组合用于屏障检测。\n\n通过记录实验温度和流量，将测量条件与批次质量控制衔接。",
        "material_quotes": state.raw_user_input.splitlines()}, "literature_summary": [{
        "text": "相关研究通过集成TEER测量和阻抗谱分析建立屏障表征路径，为指南的检测流程提供方法基础。",
        "evidence_ids": [source.evidence_id], "anchors": [{"evidence_id": source.evidence_id,
            "material_quotes": state.raw_user_input.splitlines(), "literature_quotes": [source.abstract]}]}]}


def test_late_abstract_method_is_selected_as_whole_source_sentences():
    state, claim, source = example()
    state.query_plans = [QueryPlan(claim_id="C1", query="TEER impedance spectroscopy temperature")]
    introduction = "Biological barriers are important for microphysiological systems."
    background = " ".join(f"Background item {i} describes a general historical overview of the field." for i in range(30))
    late = "TEER measurements use impedance spectroscopy and compare electrode geometry under controlled temperature."
    source.abstract = f"{introduction} {background} {late}"
    assert source.abstract.index(late) > 1100
    excerpt = evidence_context(state, claim, source)["excerpt"]
    assert late in excerpt and len(excerpt) <= 1100
    assert all(unit in source.abstract for unit in excerpt.splitlines())
    assert selected_passages("很长的句子不能在中间截断。", "句子", 4) == []


def test_context_uses_point_related_original_passages_not_generated_fields_or_other_files():
    state, claim, _ = example()
    state.uploaded_file_context.files = [UploadedFileDigest(file_id="other", name="别的材料", content="电池循环寿命测试采用高镍正极。")]
    claim.claim_fields["validation"] = "虚构的验证条件达到99%"
    context = material_context(state, claim)
    text = "\n".join(p["text"] for p in context["material_passages"])
    assert "记录实验温度和流量" in text
    assert "电池" not in text and "99%" not in text
    assert context["artifact_type"] == "guideline"


def test_long_material_passage_keeps_budget_and_actual_document_identity():
    state, claim, _ = example()
    point = "TEER检测需要记录实验温度和流量。"
    material = "\n".join(f"TEER检测的批次质量控制要求第{i}次记录实验条件。" for i in range(150)) + "\n" + point
    state.raw_user_input = "用户要求评估上传的指南。"
    state.uploaded_file_context.files = [UploadedFileDigest(file_id="guideline-file", name="指南.pdf", content=material)]
    claim.source_span, claim.text, claim.source_document_id = material, point, ""
    passages = material_context(state, claim)["material_passages"]
    assert passages and sum(len(row["text"]) for row in passages) <= 2400
    assert all(row["source_document_id"] == "guideline-file" and row["text"] in material for row in passages)


def test_multiple_anchors_ground_complete_paragraphs_and_preserve_source_ids():
    state, claim, source = example()
    save_reader_comparison(state, claim, source, {"reader_comparison": {"similarity": [contact(state, source)]}})
    save_reader_analysis(state, claim, [source], {"reader_analysis": synthesis(state, source)})
    saved = state.output_json["reader_research_analyses"]["C1"]
    assert saved["material_summary"]["text"].count("\n\n") == 1
    assert len(saved["material_summary"]["material_quotes"]) == 2
    assert saved["literature_summary"][0]["evidence_ids"] == ["E1"]
    assert reader_repair_targets(state, claim, [source]) == []


@pytest.mark.parametrize("changes", [
    {"material_quotes": ["不存在的材料原文"]},
    {"literature_quotes": ["另一篇文献的描述"]},
    {"material_quotes": [42]},
    {"text": "指南重复实验7次，文献重复实验27次。", "material_quotes": ["指南规定将TEER与示踪通量纳入屏障检测流程。"]},
])
def test_invalid_anchors_and_numbers_do_not_pass(changes):
    state, claim, source = example()
    source.abstract += "文献重复实验27次。"
    save_reader_comparison(state, claim, source, {"reader_comparison": {"similarity": [{**contact(state, source), **changes}]}})
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["similarity"] == []
    assert state.output_json["reader_analysis_checks"]["C1"]["issues"]


def test_generic_contacts_fail_but_concrete_short_links_and_true_limitations_survive():
    state, claim, source = example()
    save_reader_comparison(state, claim, source, {"reader_comparison": {
        "similarity": [contact(state, source, "两者都关注屏障类器官芯片中的TEER测量。")],
    }})
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["similarity"] == []
    reasons = [i["reason"] for i in state.output_json["reader_analysis_checks"]["C1"]["issues"]]
    assert "generic_common_topic" in reasons
    save_reader_comparison(state, claim, source, {"reader_comparison": {"similarity": [contact(state, source,
        "指南规定TEER测量流程，文献采用阻抗谱分析屏障功能。双方在屏障测量这一方向上具有一致性。")]} })
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["similarity"] == []
    assert any(issue["reason"] == "connection_value_not_explained"
               for issue in state.output_json["reader_analysis_checks"]["C1"]["issues"])
    text = "文献通过阻抗谱分析温度影响，为指南记录实验温度提供测量方法参考。"
    save_reader_comparison(state, claim, source, {"reader_comparison": {"similarity": [contact(state, source, text)]}})
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["similarity"][0]["text"] == text
    assert not any(t["path"].startswith(("difference", "conditions")) for t in reader_repair_targets(state, claim, [source]))


def test_synthesis_cannot_repeat_per_source_paragraph_or_use_wrong_source():
    state, claim, source = example()
    save_reader_comparison(state, claim, source, {"reader_comparison": {"similarity": [contact(state, source)]}})
    raw = synthesis(state, source)
    raw["literature_summary"][0]["text"] = contact(state, source)["text"]
    save_reader_analysis(state, claim, [source], {"reader_analysis": raw})
    assert state.output_json["reader_research_analyses"]["C1"]["literature_summary"] == []
    raw["literature_summary"][0]["evidence_ids"] = ["other"]
    save_reader_analysis(state, claim, [source], {"reader_analysis": raw})
    assert state.output_json["reader_research_analyses"]["C1"]["literature_summary"] == []


def test_bibliographic_id_is_not_a_measurement_but_unanchored_numbers_still_fail():
    state, claim, source = example()
    source.evidence_id = "E-L-016"
    save_reader_comparison(state, claim, source, {"reader_comparison": {"similarity": [contact(state, source,
        "E-L-016采用阻抗谱表征屏障功能，为指南的检测流程提供测量方法参考。")],
        "conditions": [contact(state, source, "E-L-016是综述而指南是规范，两者性质不同，构成比较的前提条件。")],
        "difference": [contact(state, source, "本指南可能包含其他测量方法，与该文献存在技术路径差异。")],
    }})
    saved = state.output_json["reader_comparisons"]["C1"][source.evidence_id]
    assert saved["similarity"] and "E-L-016" not in saved["similarity"][0]["text"]
    assert saved["conditions"] == saved["difference"] == []
    raw = synthesis(state, source)
    raw["literature_summary"][0]["text"] = "E-L-016采用阻抗谱表征屏障功能，为指南的检测流程提供方法参考。"
    save_reader_analysis(state, claim, [source], {"reader_analysis": raw})
    row = state.output_json["reader_research_analyses"]["C1"]["literature_summary"][0]
    assert "E-L-016" not in row["text"] and f"《{source.title}》" in row["text"]
    raw["literature_summary"][0]["text"] += "精度为99%。"
    save_reader_analysis(state, claim, [source], {"reader_analysis": raw})
    assert state.output_json["reader_research_analyses"]["C1"]["literature_summary"] == []


def test_document_type_refusal_is_not_a_research_comparison_condition():
    state, claim, source = example()
    save_reader_comparison(state, claim, source, {"reader_comparison": {"conditions": [
        contact(state, source, "指南是规范制定，文献是知识综述，因此不具备直接比较实验条件的基础。"),
        contact(state, source, "本指南是计划中的规范，而该文献是已发表的工程方法论文。"),
        contact(state, source, "文献分析测量温度对阻抗的影响，指南要求记录实验温度以便控制测量条件。"),
    ]}})
    rows = state.output_json["reader_comparisons"]["C1"]["E1"]["conditions"]
    assert len(rows) == 1 and "记录实验温度" in rows[0]["text"]


def test_numeric_result_elsewhere_in_source_still_requires_its_own_anchor():
    state, claim, source = example()
    source.abstract += "实测电阻为22单位。"
    raw = synthesis(state, source)
    raw["literature_summary"][0]["text"] += "该测量的电阻为22单位。"
    raw["literature_summary"][0]["anchors"][0]["literature_quotes"] = [
        "文献将TEER测量集成到器官芯片。"]
    save_reader_analysis(state, claim, [source], {"reader_analysis": raw})
    assert state.output_json["reader_research_analyses"]["C1"]["literature_summary"] == []
    assert any(issue["reason"] == "unsupported_number"
               for issue in state.output_json["reader_analysis_checks"]["C1"]["issues"])


def test_truncated_primary_reply_repairs_missing_contacts_with_summaries_once(monkeypatch):
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    state, claim, source = example()
    second = source.model_copy(update={"evidence_id": "E2", "title": "温度条件下的屏障检测",
        "abstract": "该研究比较温度条件下示踪通量的变化，用于屏障测量质量控制。"})
    state.evidence_records.append(second)
    graph, requests = ContentAssessmentGraph(), []
    graph._comparison = lambda _: None
    graph._comparison_evidence_for_claim = lambda *_: [source, second]

    async def request(*args, **kwargs):
        requests.append(kwargs["user_prompt"])
        if len(requests) == 1:
            return None
        return {"reader_analysis": synthesis(state, source), "links": [
            {"claim_id": "C1", "evidence_id": source.evidence_id,
             "reader_comparison": {"similarity": [contact(state, source)]}},
            {"claim_id": "C1", "evidence_id": second.evidence_id,
             "reader_comparison": {"similarity": [contact(state, second,
                 "文献比较实验温度与示踪通量变化的关系，为指南将温度记录纳入屏障检测质量控制提供验证设计参考。")]}},
        ]}

    graph._request_model_json = request
    asyncio.run(graph._comparison_stage(state))
    assert len(requests) == 2
    checks = state.output_json["reader_analysis_checks"]["C1"]
    assert {target["path"] for target in checks["repair_targets"]} == {
        "material_summary", "literature_summary", "similarity:E1", "similarity:E2"}
    assert checks["remaining_targets"] == []
    assert all(state.output_json["reader_comparisons"]["C1"][eid]["similarity"] for eid in ("E1", "E2"))
    asyncio.run(graph._repair_reader_analysis(state, claim, [source, second], {}, []))
    assert len(requests) == 2


def test_single_targeted_repair_keeps_passed_content_and_audit_decisions(monkeypatch):
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    state, claim, source = example()
    initial = synthesis(state, source)
    initial["literature_summary"] = []
    save_reader_analysis(state, claim, [source], {"reader_analysis": initial})
    save_reader_comparison(state, claim, source, {"reader_comparison": {
        "similarity": [contact(state, source, "两者都关注TEER测量。")],
        "difference": [contact(state, source, "文献侧重阻抗谱测量，指南侧重检测流程与质量控制。")],
    }})
    old = copy.deepcopy(state.output_json)
    requests, updates = [], []
    graph = ContentAssessmentGraph()

    async def request(*args, **kwargs):
        requests.append(kwargs["user_prompt"])
        response = synthesis(state, source)
        response["material_summary"]["text"] = "不得覆盖的已通过总结。"
        return {"reader_analysis": response, "links": [{"claim_id": "C1", "evidence_id": "E1",
            "reader_comparison": {"similarity": [contact(state, source)], "difference": [contact(state, source, "不得覆盖")]} }],
            "decisions": [{"label": "不得改动审计结论"}]}

    async def update(value):
        updates.append(copy.deepcopy(value.output_json))

    graph._request_model_json, graph._emit_stage_update = request, update
    args = (state, claim, [source], {**claim.model_dump(), "assessment_context": material_context(state, claim)}, [evidence_context(state, claim, source)])
    asyncio.run(graph._repair_reader_analysis(*args))
    asyncio.run(graph._repair_reader_analysis(*args))
    assert len(requests) == 1 and "generic_common_topic" in requests[0]
    assert "已通过内容" in requests[0]
    assert state.output_json["reader_research_analyses"]["C1"]["material_summary"] == old["reader_research_analyses"]["C1"]["material_summary"]
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["difference"] == old["reader_comparisons"]["C1"]["E1"]["difference"]
    assert state.output_json["reader_analysis_checks"]["C1"]["remaining_targets"] == []
    assert len(updates) == 1 and not state.novelty_decisions


def test_unavailable_repair_is_bounded_and_keeps_existing_analysis(monkeypatch):
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    state, claim, source = example()
    graph, calls = ContentAssessmentGraph(), []

    async def unavailable(*args, **kwargs):
        calls.append(1)
        return None

    graph._request_model_json = unavailable
    args = (state, claim, [source], claim.model_dump(), [])
    asyncio.run(graph._repair_reader_analysis(*args))
    asyncio.run(graph._repair_reader_analysis(*args))
    assert calls == [1]
    assert state.output_json["reader_analysis_checks"]["C1"]["repair_status"] == "unavailable"
    assert state.current_stage != "failed" and not state.confirmation.needed


def test_comparison_stage_uses_selected_context_and_pushes_before_repair(monkeypatch):
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    state, claim, source = example()
    graph, events = ContentAssessmentGraph(), []
    graph._comparison = lambda _: None
    graph._comparison_evidence_for_claim = lambda *_: [source]

    async def request(*args, **kwargs):
        events.append(("call", kwargs["user_prompt"]))
        return {"links": [{"claim_id": "C1", "evidence_id": "E1",
            "reader_comparison": {"similarity": [contact(state, source, "两者都关注TEER测量。")]} }],
            "reader_analysis": synthesis(state, source)} if len([e for e in events if e[0] == "call"]) == 1 else {
                "links": [{"claim_id": "C1", "evidence_id": "E1", "reader_comparison": {"similarity": [contact(state, source)]}}]}

    async def update(value):
        events.append(("update", copy.deepcopy(value.output_json)))

    graph._request_model_json, graph._emit_stage_update = request, update
    asyncio.run(graph._comparison_stage(state))
    assert [e[0] for e in events] == ["call", "update", "call", "update"]
    assert "material_passages" in events[0][1] and "记录实验温度和流量" in events[0][1]
    assert events[1][1]["reader_research_analyses"]["C1"]["material_summary"]
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["similarity"]


def test_summary_only_model_reply_is_accepted(monkeypatch):
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    state, claim, source = example()

    class Client:
        async def generate_json_with_metrics(self, **kwargs):
            return DeepSeekJSONResult(payload={"reader_analysis": synthesis(state, source)},
                metrics=DeepSeekCallMetrics(profile="test", model="test", stream=False, timeout_seconds=1, done_ms=1))

    result = asyncio.run(ContentAssessmentGraph(model_client=Client())._request_model_json(state,
        stage="comparison", system_prompt="test", user_prompt="test", max_tokens=100))
    assert result["reader_analysis"]["claim_id"] == "C1"


@pytest.mark.parametrize("kind", ["paper", "project", "guideline"])
def test_artifact_context_and_generation_rules_distinguish_targets_and_observations(kind):
    state, claim, source = example()
    state.report_preferences.resolved_artifact_type = kind
    context = material_context(state, claim)
    prompt = comparison_prompt(claims=[{**claim.model_dump(), "assessment_context": context}], evidence=[evidence_context(state, claim, source)])
    assert context["artifact_type"] == kind
    assert "项目区分计划与已完成工作" in prompt
    assert "论文区分方法与实验结果" in prompt
    assert "指南说明规范对象、流程和指标用途" in prompt
    assert "material_quotes" in prompt
