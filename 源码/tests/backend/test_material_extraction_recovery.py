"""Regressions for long uploads whose structured model output was truncated."""
import asyncio

import pytest

from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.material_extraction import source_chunks, split_source_chunk
from app.agents.content_assessment.schemas import ContentAssessmentState, ResearchNineFields, UploadedFileDigest
from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekClientError, DeepSeekJSONResult


def material(length=7200):
    text = "".join(f"第{i}组通过问卷和访谈研究车辆保险续保服务，比较不同渠道客户满意度。\n" for i in range(250))[:length]
    state = ContentAssessmentState(raw_user_input="", current_stage="claim_extraction", nine_fields_confirmed=True)
    state.uploaded_file_context.files = [UploadedFileDigest(file_id="file1", name="研究材料.docx", content=text)]
    state.nine_fields = ResearchNineFields(research_direction="车辆保险", research_goal="比较不同渠道客户满意度",
        research_content="研究车辆保险续保服务", technical_route="问卷和访谈", key_technologies=["问卷"])
    state.report_preferences.artifact_type = state.report_preferences.resolved_artifact_type = "paper"
    return state


class Model:
    def __init__(self, failure=None):
        self.failure = failure or (lambda *_: None)
        self.calls = []

    async def generate_json_with_metrics(self, **kwargs):
        chunk = kwargs["user_prompt"].split("<assessment_material>\n", 1)[1].rsplit("\n</assessment_material>", 1)[0]
        header, name, offset, text = chunk.split("\n", 3)
        self.calls.append((chunk, kwargs["max_tokens"]))
        metrics = DeepSeekCallMetrics(profile="test", model="test", stream=False,
            timeout_seconds=1, status_code=200, done_ms=1, max_tokens=kwargs["max_tokens"], finish_reason="stop")
        error = self.failure(text, kwargs["max_tokens"], len(self.calls))
        if error:
            metrics.error_type = error
            metrics.error = "模型输出达到长度上限" if error == "ModelOutputTruncated" else error
            metrics.finish_reason = "length" if error == "ModelOutputTruncated" else "stop"
            if error == "HTTPAuthorizationError":
                metrics.status_code = 401
            raise DeepSeekClientError(metrics.error, metrics=metrics)
        quote = next(line for line in text.splitlines() if line.strip())
        return DeepSeekJSONResult(payload={"claims": [{"source_document_id": header.removeprefix("文件 ID: "),
            "source_quote": quote, "text": quote, "claim_type": "technical"}]}, metrics=metrics)


@pytest.fixture(autouse=True)
def enable_model(monkeypatch):
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)


def assert_grounded(state):
    source = state.uploaded_file_context.files[0].content
    assert state.claims
    assert all(source[c.source_char_start:c.source_char_end] == c.source_span for c in state.claims)


def test_smaller_chunks_and_recursive_splits_preserve_every_original_character():
    state = material()
    chunks = list(source_chunks(state))
    assert "".join(c.split("\n", 3)[3] for c in chunks) == state.uploaded_file_context.files[0].content
    for chunk in chunks:
        parts = split_source_chunk(chunk)
        if parts is None:
            continue
        left, right = [p.split("\n", 3) for p in parts]
        original = chunk.split("\n", 3)
        assert left[:2] == right[:2] == original[:2]
        assert left[3] + right[3] == original[3]
        assert int(right[2].split(": ")[1]) == int(left[2].split(": ")[1]) + len(left[3])


@pytest.mark.parametrize("error_type", ["ModelOutputTruncated", "JSONDecodeError"])
def test_truncated_or_malformed_response_is_recovered_without_skipping_remaining_material(error_type):
    state = material()
    model = Model(lambda text, *_: error_type if len(text) > 1400 else None)
    graph = ContentAssessmentGraph(model_client=model)
    asyncio.run(graph._claim_extraction_stage(state))
    assert state.current_stage == "claim_extraction"
    assert not state.confirmation.needed and not graph._model_unavailable
    assert_grounded(state)
    covered = [c.split("\n", 3)[3] for c, _ in model.calls if len(c.split("\n", 3)[3]) <= 1400]
    assert "".join(covered) == state.uploaded_file_context.files[0].content
    diagnostic = next(d for d in state.output_json["model_response_diagnostics"] if d["error_type"])
    assert diagnostic["error_type"] == error_type and diagnostic["http_status"] == 200
    assert diagnostic["max_tokens"] == 6000
    assert state.output_json["material_extraction_recoveries"]


def test_small_segment_can_increase_output_budget_once():
    state = material(280)
    model = Model(lambda text, budget, _: "ModelOutputTruncated" if budget < 9000 else None)
    asyncio.run(ContentAssessmentGraph(model_client=model)._claim_extraction_stage(state))
    assert [budget for _, budget in model.calls] == [6000, 9000]
    assert state.current_stage != "failed"
    assert_grounded(state)


def test_persistent_truncation_has_bounded_recovery_and_explicit_error():
    state = material()
    model = Model(lambda *_: "ModelOutputTruncated")
    asyncio.run(ContentAssessmentGraph(model_client=model)._claim_extraction_stage(state))
    assert len(model.calls) <= 5
    assert state.current_stage == "failed" and not state.confirmation.needed
    assert "输出上限" in state.output_json["generation_recovery"]["message"]
    assert state.output_json["material_extraction_failure"]["automatic_recovery_exhausted"]
    assert not state.claims  # no truncated JSON promoted to a completed claim
    assert len(state.uploaded_file_context.files[0].content) == 7200


def test_authorization_error_stops_without_splitting_or_increasing_budget():
    state = material()
    model = Model(lambda *_: "HTTPAuthorizationError")
    asyncio.run(ContentAssessmentGraph(model_client=model)._claim_extraction_stage(state))
    assert len(model.calls) == 1 and state.current_stage == "failed"
    assert not state.output_json.get("material_extraction_recoveries")


def test_retry_reuses_grounded_completed_chunks_and_invalidates_cache_after_material_change():
    state = material()
    first = Model(lambda text, budget, index: "ReadError" if index == 2 else None)
    asyncio.run(ContentAssessmentGraph(model_client=first)._claim_extraction_stage(state))
    assert state.current_stage == "failed"
    assert_grounded(state)
    first_completed_chunk = first.calls[0][0]
    second = Model()
    state.current_stage = "claim_extraction"
    asyncio.run(ContentAssessmentGraph(model_client=second)._claim_extraction_stage(state))
    assert state.current_stage == "claim_extraction"
    assert first_completed_chunk not in [chunk for chunk, _ in second.calls]
    assert len(second.calls) == len(list(source_chunks(state))) - 1
    assert_grounded(state)
    state.uploaded_file_context.files[0].content = "补充研究对象与方法。\n" + state.uploaded_file_context.files[0].content
    third = Model()
    asyncio.run(ContentAssessmentGraph(model_client=third)._claim_extraction_stage(state))
    assert len(third.calls) == len(list(source_chunks(state)))
    assert_grounded(state)


def test_retry_reuses_split_children_without_repeating_truncated_parent():
    state = material(2600)
    first = Model(lambda text, budget, index: "ModelOutputTruncated" if len(text) > 1400
                  else "ReadError" if index == 3 else None)
    asyncio.run(ContentAssessmentGraph(model_client=first)._claim_extraction_stage(state))
    assert state.current_stage == "failed" and len(first.calls) == 3
    assert_grounded(state)
    second = Model()
    state.current_stage = "claim_extraction"
    asyncio.run(ContentAssessmentGraph(model_client=second)._claim_extraction_stage(state))
    assert len(second.calls) == 1
    assert state.current_stage != "failed"
    assert_grounded(state)
