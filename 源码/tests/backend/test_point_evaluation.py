"""Reader appraisals are generated during assessment and stay bound to sources."""
import asyncio
import copy

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import Claim, ContentAssessmentState, EvidenceRecord
from app.agents.content_assessment.point_evaluation import evaluate_points


def material():
    state = ContentAssessmentGraph().create_initial_state("拟比较微流控肝脏芯片灌流条件。")
    state.report_preferences.resolved_artifact_type = "paper"
    state.claims = [Claim(claim_id="C1", text=state.raw_user_input,
                          searchable_terms=["liver", "perfusion"], assertion_type="planned")]
    state.evidence_records = [EvidenceRecord(evidence_id="E1", source_type="literature",
        title="Liver chip perfusion", abstract="Liver chip perfusion assessed with flow monitoring.")]
    return state


def paragraphs():
    return [{"dimension": dim, "text": text, "evidence_ids": ["E1"], "kind": "analysis"} for dim, text in [
        ("innovation", "材料拟比较肝脏芯片的灌流条件；相关研究采用流量监测，可围绕灌流控制与读出设计开展方法比较。"),
        ("frontier", "该要点将肝脏芯片的灌流过程作为研究对象，与来源中流量监测的研究问题相联系。"),
        ("advancedness", "其方法设计可以从灌流条件与监测读出的对应关系进行评价，材料拟开展的条件比较属于实验设计层面的探索。"),
    ]]


def test_appraisal_is_reviewed_emitted_before_report_and_cached_by_source_input(monkeypatch):
    state = material()
    graph = ContentAssessmentGraph()
    calls, updates = [], []

    async def request(_state, **kwargs):
        calls.append(kwargs)
        assert kwargs["stage"] == "assessment"
        assert "拟比较微流控肝脏芯片灌流条件" in kwargs["user_prompt"]
        if len(calls) % 2:
            return {"evaluations": paragraphs()}
        return {"checked": True, "replacements": []}

    async def emit(s):
        updates.append(copy.deepcopy(s.output_json["reader_point_evaluations"]))

    monkeypatch.setattr(graph, "_request_model_json", request)
    monkeypatch.setattr(graph, "_emit_stage_update", emit)
    asyncio.run(evaluate_points(graph, state))
    rows = state.output_json["reader_point_evaluations"]["C1"]
    assert {p["dimension"] for p in rows} == {"innovation", "frontier", "advancedness"}
    assert updates[0]["C1"] == rows
    assert "scholarly_drafts" not in state.output_json
    asyncio.run(evaluate_points(graph, state))
    assert len(calls) == 2
    state.evidence_records[0].abstract += " The flow is controlled."
    asyncio.run(evaluate_points(graph, state))
    assert len(calls) == 4


def test_bad_sources_and_invented_results_cannot_appear_in_appraisal(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()

    async def request(_state, **kwargs):
        return {"evaluations": [
            {"dimension": "innovation", "text": "准确率达到99.9%。", "evidence_ids": ["E1"]},
            {"dimension": "frontier", "text": "来源提出流量监测。", "evidence_ids": ["unknown"]},
        ]}
    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(evaluate_points(graph, state))
    assert not state.output_json["reader_point_evaluations"]


def test_failed_grounding_check_does_not_publish_unreviewed_analysis(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    async def request(_state, **kwargs):
        return {"evaluations": paragraphs()} if "返回 {evaluations" in kwargs["user_prompt"] else None
    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(evaluate_points(graph, state))
    assert not state.output_json["reader_point_evaluations"]
    assert state.output_json["reader_evaluation_reviews"]["C1"]["status"] == "pending"


def test_no_readable_source_clears_stale_appraisal_without_model_request(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    state.evidence_records = []
    state.output_json["reader_point_evaluations"] = {"C1": paragraphs()}
    async def request(*args, **kwargs):
        raise AssertionError("No source means no invented evaluation")
    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(evaluate_points(graph, state))
    assert state.output_json["reader_point_evaluations"] == {}


def test_scholarly_evaluation_runs_in_assessment_stage_without_changing_sync_rule_contract(monkeypatch):
    graph, state = ContentAssessmentGraph(), material()
    import app.agents.content_assessment.point_evaluation as module
    order = []
    monkeypatch.setattr(graph, "_assessment_stage", lambda _state: order.append("rules"))
    async def evaluate(_graph, _state):
        order.append("analysis")
    monkeypatch.setattr(module, "evaluate_points", evaluate)
    asyncio.run(graph._assessment_stage_with_analysis(state))
    assert order == ["rules", "analysis"]


def test_new_snapshot_keeps_appraisal_already_shown_but_historical_freeze_does_not_borrow_it():
    from app.agents.content_assessment.report_composition import freeze_snapshot
    state = material()
    state.output_json["reader_point_evaluations"] = {"C1": paragraphs()}
    freeze_snapshot(state)
    assert state.report_snapshot.audit_data["reader_point_evaluations"]["C1"] == paragraphs()
    historical = material()
    historical.output_json["reader_point_evaluations"] = {"C1": paragraphs()}
    freeze_snapshot(historical, historical=True)
    assert "reader_point_evaluations" not in historical.report_snapshot.audit_data


def test_selected_papers_do_not_establish_field_wide_novelty_or_consensus():
    from app.agents.content_assessment.point_evaluation import local_comparison_prose
    text = "材料计划以专家盲评考察解释质量。该设计在现有可解释性研究中较少被系统实施。时间切分对应后续化合物的验证。"
    assert local_comparison_prose(text) == "材料计划以专家盲评考察解释质量。时间切分对应后续化合物的验证。"
    assert local_comparison_prose("材料未采用随机切分，因此这里保留时间切分的适用条件。") == "材料未采用随机切分，因此这里保留时间切分的适用条件。"


def test_missing_dimension_is_repaired_once_without_rewriting_accepted_rows(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    calls, updates = [], []
    async def request(_state, **kwargs):
        calls.append(kwargs["user_prompt"])
        if "逐段核对下面评议" in kwargs["user_prompt"]:
            return {"checked": True, "replacements": []}
        return {"evaluations": paragraphs()[:2] if len(calls) == 1 else paragraphs()[2:]}
    async def emit(s):
        updates.append(copy.deepcopy(s.output_json))
    monkeypatch.setattr(graph, "_request_model_json", request)
    monkeypatch.setattr(graph, "_emit_stage_update", emit)
    asyncio.run(evaluate_points(graph, state))
    assert len(calls) == 4
    assert '只返回以下尚未通过的维度，不改写已有评价：["advancedness"]' in calls[2]
    assert updates[0]["reader_evaluation_completion"]["C1"]["status"] == "partial"
    assert updates[-1]["reader_evaluation_completion"]["C1"]["status"] == "completed"
    assert state.output_json["reader_point_evaluations"]["C1"] == paragraphs()
    assert state.stage_progress["assessment"].completed == 1


def test_repair_receives_sources_and_specific_reason_without_reusing_failed_prose(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    bad = {**paragraphs()[0], "text": "材料计划比较灌流条件，但现有文献中尚未有研究系统整合此方法，因此填补研究空白。"}
    calls = []
    async def request(_state, **kwargs):
        prompt = kwargs["user_prompt"]
        if "逐段核对下面评议" in prompt:
            return {"checked": True, "replacements": []}
        calls.append(prompt)
        if len(calls) == 1:
            return {"evaluations": [bad, *paragraphs()[1:]]}
        import json
        packet = json.loads(prompt.rsplit("\n", 1)[1])
        assert bad["text"] not in prompt
        assert "text" not in packet["validation_feedback"][0]
        assert "从局部来源推断" in packet["validation_feedback"][0]["reason"]
        assert packet["literature"][0]["id"] == "E1"
        assert "accepted_evaluations" not in packet
        return {"evaluations": paragraphs()[:1]}
    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(evaluate_points(graph, state))
    assert len(calls) == 2
    assert state.output_json["reader_evaluation_completion"]["C1"]["status"] == "completed"


def test_review_deleted_dimension_keeps_stage_result_and_retry_preserves_accepted_analysis(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    calls = []
    async def request(_state, **kwargs):
        calls.append(kwargs["user_prompt"])
        if "逐段核对下面评议" in kwargs["user_prompt"]:
            if len(calls) == 2:
                return {"checked": True, "replacements": [{"key": "evaluations", "index": 2, "text": ""}]}
            return {"checked": True, "replacements": []}
        return {"evaluations": paragraphs()} if len(calls) == 1 else None
    monkeypatch.setattr(graph, "_request_model_json", request)
    monkeypatch.setattr(graph, "_assessment_stage", lambda s: None)
    result = asyncio.run(graph._make_stage_node("assessment")({"payload": state.model_dump()}))
    state = ContentAssessmentState.model_validate(result["payload"])
    assert state.current_stage == "assessment"
    assert not state.resume_from_stage
    assert not state.confirmation.needed
    assert "generation_recovery" not in state.output_json
    assert next(s for s in state.stage_trace if s.stage_key == "assessment").status == "completed"
    assert state.output_json["reader_evaluation_completion"]["C1"]["status"] == "partial"
    assert state.stage_progress["assessment"].completed == 1
    assert len(calls) == 3  # initial, review, single failed repair
    accepted = copy.deepcopy(state.output_json["reader_point_evaluations"]["C1"])
    async def retry(_state, **kwargs):
        if "逐段核对下面评议" in kwargs["user_prompt"]:
            return {"checked": True, "replacements": []}
        assert '只返回以下尚未通过的维度，不改写已有评价：["advancedness"]' in kwargs["user_prompt"]
        assert _state.output_json["reader_point_evaluations"]["C1"] == accepted
        return {"evaluations": paragraphs()[2:]}
    monkeypatch.setattr(graph, "_request_model_json", retry)
    state.confirmation.needed = False
    result = asyncio.run(graph._make_stage_node("assessment")({"payload": state.model_dump()}))
    state = ContentAssessmentState.model_validate(result["payload"])
    assert state.stage_progress["assessment"].completed == 1
    assert next(s for s in state.stage_trace if s.stage_key == "assessment").status == "completed"
    assert state.output_json["reader_point_evaluations"]["C1"][:2] == accepted


def test_saved_complete_point_is_emitted_before_next_point_finishes(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    state.claims.append(state.claims[0].model_copy(update={"claim_id": "C2"}))
    seen = []
    async def request(_state, **kwargs):
        if "逐段核对下面评议" in kwargs["user_prompt"]:
            return {"checked": True, "replacements": []}
        return {"evaluations": paragraphs()}
    async def emit(s):
        seen.append((copy.deepcopy(s.output_json["reader_evaluation_completion"]), s.stage_progress["assessment"].completed))
    monkeypatch.setattr(graph, "_request_model_json", request)
    monkeypatch.setattr(graph, "_emit_stage_update", emit)
    asyncio.run(evaluate_points(graph, state))
    assert seen[0][0]["C1"]["status"] == "completed"
    assert "C2" not in seen[0][0]
    assert [row[1] for row in seen] == [1, 2]


def test_report_reuses_stage_evaluations_even_if_editor_tries_to_replace_them(monkeypatch):
    import app.agents.content_assessment.scholarly_report as module
    from app.agents.content_assessment.point_evaluation import evaluation_packet, input_fingerprint
    state, graph = material(), ContentAssessmentGraph()
    state.output_json.update(reader_point_evaluations={"C1": paragraphs()},
        reader_evaluation_inputs={"C1": input_fingerprint(evaluation_packet(state, state.claims[0]))},
        reader_evaluation_completion={"C1": {"status": "completed"}})
    base = {"text": "材料拟比较肝脏芯片的灌流条件，相关来源采用流量监测。", "evidence_ids": ["E1"], "kind": "analysis"}
    async def request(_state, **kwargs):
        if "逐项研究分析" in kwargs["user_prompt"]:
            assert "仅补充以下尚未形成的三维评价：[]" in kwargs["user_prompt"]
        return {**{key: [{**base, "dimension": dim}] for key, dim in [
            ("progress", "representatives"), ("comparison", "points"), ("evaluation", "design")]},
            "judgement": [{**base, "dimension": "innovation"}, {**base, "dimension": "value"}, {**base, "dimension": "limits"}]}
    async def edit(*args, **kwargs):
        return {**args[3], "judgement": [{**base, "dimension": "innovation"}]}
    async def review(*args, **kwargs):
        return args[3]
    monkeypatch.setattr(graph, "_request_model_json", request)
    monkeypatch.setattr(module, "consolidate_analysis", edit)
    monkeypatch.setattr(module, "review_source_assertions", review)
    asyncio.run(module.draft(graph, state))
    rows = state.output_json["scholarly_drafts"]["C1"]["judgement"]
    assert rows[:3] == paragraphs()
    assert len([row for row in rows if row["dimension"] == "innovation"]) == 1


def test_report_fills_only_missing_stage_dimensions_and_preserves_accepted_rows(monkeypatch):
    import app.agents.content_assessment.scholarly_report as module
    from app.agents.content_assessment.point_evaluation import evaluation_packet, input_fingerprint
    state, graph = material(), ContentAssessmentGraph()
    accepted = copy.deepcopy(paragraphs()[:2])
    state.output_json.update(reader_point_evaluations={"C1": accepted},
        reader_evaluation_inputs={"C1": input_fingerprint(evaluation_packet(state, state.claims[0]))},
        reader_evaluation_completion={"C1": {"status": "partial", "missing_dimensions": ["advancedness"]}})
    state.output_json["scholarly_drafts"] = {"C1": {
        "progress": [{"text": "已保存的研究进展段落，报告恢复时应直接沿用。", "evidence_ids": ["E1"]}],
        "comparison": [{"text": "已保存的方法比较段落，报告恢复时应直接沿用。", "evidence_ids": ["E1"]}],
        "evaluation": [{"text": "已保存的分类评价段落，报告恢复时应直接沿用。", "evidence_ids": ["E1"]}],
        "judgement": [{"dimension": "value", "text": "已保存的研究价值评价。", "evidence_ids": ["E1"]}],
    }}
    generated = {"dimension": "advancedness",
        "text": "灌流監測的配置與屏障功能读出相结合，可用于评价研究方案对动态实验条件的适配方式。",
        "evidence_ids": ["E1"], "kind": "analysis"}
    replacement = {"dimension": "innovation",
        "text": "报告编辑器试图覆盖已经校核通过的创新性评价内容。",
        "evidence_ids": ["E1"], "kind": "analysis"}

    async def request(_state, **kwargs):
        if "逐项研究分析" in kwargs["user_prompt"]:
            assert '"advancedness"' in kwargs["user_prompt"]
            assert '"innovation"' in kwargs["user_prompt"]
            return {"progress": [{"text": "研究进展说明与当前要点相关的既有技术路线。", "evidence_ids": ["E1"]}],
                "comparison": [{"text": "相关研究采用流量监测，可与材料中的灌流设计进行方法层面对照。", "evidence_ids": ["E1"]}],
                "evaluation": [{"text": "当前证据可用于分析研究方案的验证安排及适用条件。", "evidence_ids": ["E1"]}],
                "judgement": [generated, {"dimension": "value", "text": "该设计为动态条件下的屏障研究提供了可分析的实验路径。", "evidence_ids": ["E1"]}]}
        return {"summary": [{"text": "结合材料与来源形成的总体评价。", "evidence_ids": ["E1"]}],
            "conclusion": [{"text": "本次结论以已核对的研究内容和相关来源为依据。", "evidence_ids": ["E1"]}]}

    async def edit(*args, **kwargs):
        return {**args[3], "judgement": [replacement, generated]}

    async def review(*args, **kwargs):
        return args[3]

    monkeypatch.setattr(graph, "_request_model_json", request)
    monkeypatch.setattr(module, "consolidate_analysis", edit)
    monkeypatch.setattr(module, "review_source_assertions", review)
    asyncio.run(module.draft(graph, state))

    rows = state.output_json["scholarly_drafts"]["C1"]["judgement"]
    assert rows[:2] == accepted
    assert generated in rows
    assert len([row for row in rows if row["dimension"] == "innovation"]) == 1
    assert len([row for row in rows if row["dimension"] == "advancedness"]) == 1
