"""Regression cases from the two interrupted recommended examples."""
import asyncio
import copy
import json

import pytest

from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.point_evaluation import (
    checked_source_basis, evaluate_points, evaluation_packet, input_fingerprint,
)
from app.agents.content_assessment.scholarly_report import review_source_assertions
from test_point_evaluation import material, paragraphs


@pytest.mark.parametrize("bad_clause", [
    "但未提及同批次制备或跨尺度验证。",
    "但未涉及双人标注或未见主题验证。",
])
def test_source_reviewer_can_repair_offending_clause_without_losing_valid_design(monkeypatch, bad_clause):
    state, graph = material(), ContentAssessmentGraph()
    accepted = paragraphs()[:2]
    state.output_json.update(
        reader_point_evaluations={"C1": copy.deepcopy(accepted)},
        reader_evaluation_inputs={"C1": input_fingerprint(evaluation_packet(state, state.claims[0]))},
        reader_evaluation_completion={"C1": {"status": "pending"}},
    )
    calls = []
    async def request(_state, **kwargs):
        prompt = kwargs["user_prompt"]
        calls.append(prompt)
        if "逐段核对下面评议" in prompt:
            assert bad_clause in prompt  # bad sentence reaches contextual review
            return {"checked": True, "replacements": [{"key": "evaluations", "index": 0,
                **paragraphs()[2], "reason": "局部来源不能证明原研究未开展某项验证；保留实际设计分析"}]}
        assert json.loads(prompt.rsplit("\n", 1)[1])["point"]["id"] == "C1"
        return {"evaluations": [{**paragraphs()[2], "text": paragraphs()[2]["text"] + bad_clause}]}
    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(evaluate_points(graph, state))
    assert len(calls) == 2  # no extra generation cycle needed
    assert state.output_json["reader_point_evaluations"]["C1"][:2] == accepted
    assert state.output_json["reader_evaluation_completion"]["C1"]["status"] == "completed"
    assert state.output_json["reader_point_evaluations"]["C1"][2] == paragraphs()[2]


def test_checked_true_cannot_publish_unchanged_bad_sentence_or_repeat_forever(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    calls = []
    async def request(_state, **kwargs):
        calls.append(kwargs["user_prompt"])
        if "逐段核对下面评议" in kwargs["user_prompt"]:
            return {"checked": True, "replacements": []}
        return {"evaluations": [{**p, "text": p["text"] + "但未涉及跨尺度验证。"} for p in paragraphs()]}
    monkeypatch.setattr(graph, "_request_model_json", request)
    asyncio.run(evaluate_points(graph, state))
    assert len(calls) == 4  # first draft/review plus a single repair/review
    assert not state.output_json["reader_point_evaluations"]
    assert state.output_json["reader_evaluation_completion"]["C1"]["status"] == "pending"


def test_rejected_replacement_keeps_specific_validation_reason(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    async def request(*args, **kwargs):
        return {"checked": True, "replacements": [{"key": "evaluations", "index": 0,
            **paragraphs()[2], "text": "材料拟比较灌流条件，但未涉及跨尺度验证。", "reason": "改写比较表述"}]}
    monkeypatch.setattr(graph, "_request_model_json", request)
    result = asyncio.run(review_source_assertions(graph, state,
        evaluation_packet(state, state.claims[0]), {"evaluations": paragraphs()[2:]}, audit_key="reviews"))
    assert not result["evaluations"]
    assert "但未涉及跨尺度验证" in state.output_json["reviews"]["C1"]["feedback"][0]["reason"]
    assert state.output_json["reviews"]["C1"]["status"] == "pending"


def test_verbatim_basis_checks_source_and_material_attribution_without_changing_fingerprint():
    state = material()
    packet = evaluation_packet(state, state.claims[0])
    original = input_fingerprint(packet)
    row = {**paragraphs()[2], "source_basis": {
        "material_quotes": [state.raw_user_input],
        "sources": [{"id": "E1", "quote": "flow monitoring", "role": "method"}],
    }}
    feedback = []
    rows, basis = checked_source_basis([row], packet, feedback)
    assert rows == [row] and basis["advancedness"] == row["source_basis"] and not feedback
    assert input_fingerprint(packet) == original
    row = copy.deepcopy(row)
    row["source_basis"]["sources"][0]["quote"] = "coin cells and pouch cells"
    assert checked_source_basis([row], packet, feedback) == ([], {})
    assert "逐字" in feedback[-1]["reason"]


def test_assessment_keeps_a_readable_evaluation_when_optional_basis_is_misaligned(monkeypatch):
    """A malformed audit quote must not erase an otherwise grounded dimension."""
    state, graph = material(), ContentAssessmentGraph()
    rows = paragraphs()
    rows[0] = {**rows[0], "source_basis": {
        "material_quotes": ["材料中不存在的摘录"],
        "sources": [{"id": "E1", "quote": "not in the saved abstract", "role": "method"}],
    }}

    async def request(_state, **kwargs):
        if "逐段核对下面评议" in kwargs["user_prompt"]:
            return {"checked": True, "replacements": []}
        return {"evaluations": rows}

    monkeypatch.setattr(graph, "_request_model_json", request)
    import asyncio
    asyncio.run(evaluate_points(graph, state))

    assert state.output_json["reader_evaluation_completion"]["C1"]["status"] == "completed"
    assert {row["dimension"] for row in state.output_json["reader_point_evaluations"]["C1"]} == {
        "innovation", "frontier", "advancedness"
    }
    assert any(item.get("kind") == "source_basis_format"
               for item in state.output_json["reader_evaluation_completion"]["C1"]["feedback"])


def test_actual_negative_result_remains_readable_after_source_review(monkeypatch):
    state, graph = material(), ContentAssessmentGraph()
    state.evidence_records[0].abstract += " High flow decreased barrier stability."
    row = {**paragraphs()[2], "text": "材料拟比较灌流条件；来源观察到高流量下屏障稳定性下降，因此灌流调控与屏障读出的对应关系具有具体的实验研究价值。"}
    async def request(*args, **kwargs):
        return {"checked": True, "replacements": []}
    monkeypatch.setattr(graph, "_request_model_json", request)
    result = asyncio.run(review_source_assertions(graph, state,
        evaluation_packet(state, state.claims[0]), {"evaluations": [row]}))
    assert result["evaluations"] == [row]
