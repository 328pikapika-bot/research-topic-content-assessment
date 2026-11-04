"""Focused checks for intake recognition; no report generation or live models."""
import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.agents.content_assessment.input_rules import explicit_fields, meaningful
from app.agents.content_assessment.schemas import ResearchNineFields, UploadedFileRef
from app.services import nine_fields_preview as preview


def current_cases():
    root = Path(__file__).resolve().parents[2]
    script = """
const fs = require('fs'), vm = require('vm')
const ts = require('./frontend/web/node_modules/typescript')
const source = fs.readFileSync('./frontend/web/src/services/homepageDraft.ts', 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const exports = {}
vm.runInNewContext(compiled, { exports })
process.stdout.write(JSON.stringify(exports.ASSESSMENT_CASES.map(({ id, input }) => ({ id, input }))))
"""
    return json.loads(subprocess.run(["node", "-e", script], cwd=root, capture_output=True,
                                     text=True, encoding="utf-8", check=True).stdout)


@pytest.mark.parametrize("kind", ["auto", "unknown"])
def test_unknown_material_requests_type_instead_of_eight_fields(kind):
    assert ResearchNineFields().missing_for_type(kind) == ["artifact_type"]


def test_six_current_cases_pass_without_model_and_allow_task_start(monkeypatch):
    from app.agents.content_assessment.schemas import ContentAssessmentRunRequest
    monkeypatch.setattr(preview, "DeepSeekClient", lambda: pytest.fail("Complete example must not call a model"))
    cases = current_cases()
    assert len(cases) == 6
    for case in cases:
        result = asyncio.run(preview.preview_nine_fields(case["input"], [], "alice"))
        assert result["missing_fields"] == [], case["id"]
        assert not result["extraction_failed"], case["id"]
        request = ContentAssessmentRunRequest(user_input=case["input"],
            artifact_type=result["resolved_artifact_type"], nine_fields=result["nine_fields"])
        assert not request.nine_fields.missing_for_type(request.artifact_type)


def test_six_current_cases_extract_real_anchored_points_without_supplement_popup(monkeypatch):
    from app.agents.content_assessment.graph import ContentAssessmentGraph
    graph = ContentAssessmentGraph()
    monkeypatch.setattr(graph, "_request_model_json", lambda *args, **kwargs: pytest.fail("Structured examples need no extraction call"))
    for case in current_cases():
        result = asyncio.run(preview.preview_nine_fields(case["input"], [], "alice"))
        state = graph.create_initial_state(case["input"], artifact_type=result["resolved_artifact_type"], scenario_id=case["id"])
        state.nine_fields = ResearchNineFields.model_validate(result["nine_fields"])
        state.nine_fields_confirmed = True
        graph._document_intake(state)
        asyncio.run(graph._claim_extraction_stage(state))
        assert not state.confirmation.needed, (case["id"], state.confirmation.prompt)
        assert state.output_json["missing_fields"] == [], case["id"]
        assert state.claims, case["id"]
        assert all(claim.source_span and claim.source_span in case["input"] for claim in state.claims), case["id"]


def test_equivalent_headings_and_qualitative_acceptance_are_valid(monkeypatch):
    monkeypatch.setattr(preview, "DeepSeekClient", lambda: pytest.fail("Equivalent headings should suffice"))
    text = """我要评估的是：指南
## 研究目的：统一器官芯片试验条件
**主要研究内容**：制定灌流培养和屏障功能测试流程
（一）验收要求：形成标准操作流程和记录模板；完成多实验室复现验证，并报告偏差来源
适用范围：药物筛选实验室和芯片生产质量控制
"""
    result = asyncio.run(preview.preview_nine_fields(text, [], "alice"))
    assert result["missing_fields"] == []
    assert result["nine_fields"]["evaluation_indicators"] == [
        "形成标准操作流程和记录模板", "完成多实验室复现验证，并报告偏差来源"]


def test_empty_heading_cannot_swallow_next_prompt_or_count_as_content():
    text = "研究目标：\n研究内容：比较屏障功能\n考核指标：……\n应用场景：药物筛选"
    fields = ResearchNineFields.model_validate(explicit_fields(text))
    assert fields.missing_for_type("guideline") == ["research_goal", "evaluation_indicators"]
    assert fields.research_content == "比较屏障功能"


@pytest.mark.parametrize("value", ["待补充", "……", "——", "达到国际领先水平", "创新性强"])
def test_placeholder_or_slogan_is_not_information(value):
    assert not meaningful(value)


@pytest.mark.parametrize("valid_quote", [True, False])
def test_review_recovers_missed_qualitative_indicator_only_from_actual_source(monkeypatch, valid_quote):
    quote = "需形成操作规范和记录模板，并开展跨实验室复现验证"
    text = "请评估这份指南。\n研究目标：规范芯片测量\n研究内容：统一测量条件\n应用场景：芯片实验室\n" + quote
    calls = []
    class Model:
        async def generate_json(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"nine_fields": {}}
            return {"nine_fields": {"evaluation_indicators": [quote], "research_goal": "错误覆盖"},
                    "field_sources": {"evaluation_indicators": [quote if valid_quote else "原文不存在的考核阈值"]}}
        async def aclose(self): pass
    monkeypatch.setattr(preview, "DeepSeekClient", Model)
    result = asyncio.run(preview.preview_nine_fields(text, [], "alice"))
    assert len(calls) == 2
    assert "最后一次原文复核" in calls[1]["user_prompt"]
    assert result["missing_fields"] == ([] if valid_quote else ["evaluation_indicators"])
    assert result["nine_fields"]["research_goal"] == "规范芯片测量"
    assert not result["extraction_failed"]


def test_natural_language_can_supply_route_instead_of_key_technology(monkeypatch):
    quote = "先制备电极，再测量阻抗与低温循环性能"
    class Model:
        calls = 0
        async def generate_json(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"nine_fields": {}}
            assert "technical_route" in kwargs["user_prompt"]
            return {"nine_fields": {"technical_route": quote}, "field_sources": {"technical_route": [quote]}}
        async def aclose(self): pass
    monkeypatch.setattr(preview, "DeepSeekClient", Model)
    text = "请评估这个项目。\n研究方向：低温电池\n研究内容：改善界面稳定性\n" + quote
    result = asyncio.run(preview.preview_nine_fields(text, [], "alice"))
    assert result["missing_fields"] == []
    assert result["nine_fields"]["key_technologies"] == []
    assert result["nine_fields"]["technical_route"] == quote


def test_long_attachment_tail_is_checked_before_context_limit(monkeypatch):
    class Repo:
        async def get(self, *args, **kwargs):
            return {"name": "配套材料.txt", "content": "研究内容：统一屏障功能测量\n" + "实验说明。" * 6000
                    + "\n考核指标：形成操作规程并完成跨实验室验证\n应用场景：药物筛选实验室"}
    monkeypatch.setattr(preview, "get_parsed_file_repo", Repo)
    monkeypatch.setattr(preview, "DeepSeekClient", lambda: pytest.fail("Complete sources must not call a model"))
    result = asyncio.run(preview.preview_nine_fields("请评估这份指南。\n研究目标：统一芯片试验条件",
        [UploadedFileRef(file_id="long", name="配套材料.txt")], "alice"))
    assert result["missing_fields"] == []
    assert result["nine_fields"]["evaluation_indicators"] == ["形成操作规程并完成跨实验室验证"]


def test_natural_prose_is_extracted_with_verifiable_source(monkeypatch):
    text = "请评估这篇论文。本文面向数字图书馆的文献检索，采用双编码器学习查询与摘要的相关性，并比较排序效果。"
    class Model:
        async def generate_json(self, **kwargs):
            return {"nine_fields": {"research_direction": "数字图书馆文献检索",
                "research_content": "学习查询与摘要的相关性，并比较排序效果", "key_technologies": ["双编码器"]},
                "field_sources": {"research_direction": ["数字图书馆的文献检索"],
                    "research_content": ["学习查询与摘要的相关性，并比较排序效果"], "key_technologies": ["采用双编码器"]}}
        async def aclose(self): pass
    monkeypatch.setattr(preview, "DeepSeekClient", Model)
    result = asyncio.run(preview.preview_nine_fields(text, [], "alice"))
    assert not result["extraction_failed"]
    assert result["missing_fields"] == []


def test_recognition_failure_does_not_claim_user_omitted_existing_information(monkeypatch):
    class Model:
        async def generate_json(self, **kwargs): raise TimeoutError("unavailable")
        async def aclose(self): pass
    monkeypatch.setattr(preview, "DeepSeekClient", Model)
    result = asyncio.run(preview.preview_nine_fields("请评估这篇论文。研究方向：芯片。采用灌流培养开展测量。", [], "alice"))
    assert result["extraction_failed"]
    assert all("请补充" not in warning for warning in result["warnings"])
    assert result["nine_fields"]["research_direction"].startswith("芯片")


@pytest.mark.parametrize("failed", [False, True])
def test_direct_task_entry_reuses_preview_review_and_distinguishes_service_failure(monkeypatch, failed):
    from unittest.mock import AsyncMock
    from app.agents.content_assessment import graph as graph_module
    graph = graph_module.ContentAssessmentGraph()
    text = "我要评估的是：指南\n研究目标：统一芯片测量\n研究内容：规范屏障测量\n应用场景：药物筛选\n需形成记录模板并完成多实验室验证。"
    state = graph.create_initial_state(text)
    state.report_preferences.resolved_artifact_type = "guideline"
    fields = ResearchNineFields(research_goal="统一芯片测量", research_content="规范屏障测量",
        evaluation_indicators=[] if failed else ["形成记录模板并完成多实验室验证"], application_scenarios=["药物筛选"])
    review = AsyncMock(return_value={"nine_fields": fields.model_dump(), "resolved_artifact_type": "guideline",
        "missing_fields": ["evaluation_indicators"] if failed else [], "extraction_failed": failed})
    monkeypatch.setattr(graph_module, "CONTENT_ASSESSMENT_LLM_ENABLED", True)
    monkeypatch.setattr(graph, "_request_model_json", AsyncMock(return_value=None))
    monkeypatch.setattr(preview, "preview_nine_fields", review)
    asyncio.run(graph._claim_extraction_stage(state))
    review.assert_awaited_once()
    assert text in review.call_args.kwargs["material_texts"]
    if failed:
        assert state.current_stage == "failed"
        assert not state.confirmation.needed
        assert state.output_json["generation_recovery"]["reason"] == "claim_extraction"
        assert "识别中断" in state.output_json["generation_recovery"]["message"]
        assert "原文字和材料已保留" in state.output_json["generation_recovery"]["message"]
    else:
        assert state.output_json["missing_fields"] == []
        assert state.nine_fields.evaluation_indicators == fields.evaluation_indicators
