from __future__ import annotations

import asyncio
import io
import json
import subprocess
from pathlib import Path
import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.common.retrieval.schemas import SearchResponse, StandardPaper
from app.services.deepseek_client import DeepSeekCallMetrics, DeepSeekClientError, DeepSeekJSONResult
from app.services.topic_selection_report import (
    _render_chart_png,
    build_topic_docx,
    build_topic_html,
    build_topic_markdown,
)


class FakeTopicSearch:
    def search_multi_sync(self, requests, *, max_concurrency=3):
        return [
            SearchResponse(
                success=True,
                total=1,
                papers=[
                    StandardPaper(
                        paper_id=f"paper-{index}",
                        title=f"Evidence {index}",
                        year=2024,
                        abstract="Future work should validate this method.",
                        doi=f"10.1234/{index}",
                        has_fulltext=False,
                    )
                ],
                search_trace={"query": request.query, "paper_num_returned": 1},
            )
            for index, request in enumerate(requests, start=1)
        ]


def test_current_homepage_topic_cases_run_full_flow() -> None:
    root = Path(__file__).resolve().parents[2]
    script = """
const fs = require('fs')
const vm = require('vm')
const ts = require('./frontend/web/node_modules/typescript')
const source = fs.readFileSync('./frontend/web/src/services/homepageDraft.ts', 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const exports = {}
vm.runInNewContext(compiled, { exports })
process.stdout.write(JSON.stringify(exports.TOPIC_CASES.map(({ id, input }) => ({ id, input }))))
"""
    cases = json.loads(subprocess.run(
        ["node", "-e", script], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout)
    assert len(cases) == 3
    for case in cases:
        agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
        result = asyncio.run(agent.run(agent.create_initial_state(case["input"], analysis_mode="fast")))
        assert result.current_stage == "completed", case["id"]
        assert result.candidates, case["id"]
        assert result.report_plan.citation_map, case["id"]


def test_topic_selection_runs_nine_stages_with_traceable_report() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    state = agent.create_initial_state("大模型预测蛋白质结构与实验验证", analysis_mode="fast")
    result = asyncio.run(agent.run(state))

    assert result.current_stage == "completed"
    assert [item.status for item in result.stage_trace] == ["completed"] * 9
    assert result.evidence_pool
    assert result.candidates
    assert result.report_plan.report_version.startswith("TopicReportV7-")
    assert len(result.report_plan.sections) == 6
    assert result.report_plan.citation_map
    assert all(item.model_status == "disabled" for item in result.stage_trace)
    assert all(item.model_call_count == 0 for item in result.stage_trace)


def test_topic_report_docx_is_generated_from_same_state() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    state = agent.create_initial_state("单细胞多组学解析肿瘤微环境", analysis_mode="fast")
    result = asyncio.run(agent.run(state))
    content, filename = build_topic_docx(result)

    assert filename.endswith(".docx")
    assert content[:2] == b"PK"
    assert len(content) > 10_000
    from docx import Document
    document = Document(io.BytesIO(content))
    section = document.sections[0]
    assert 215 <= section.page_width.mm <= 217
    assert 279 <= section.page_height.mm <= 281


def test_real_report_topics_remain_regression_inputs() -> None:
    topics = [
        "大模型预测蛋白质结构",
        "单细胞多组学解析肿瘤微环境",
        "生成式人工智能驱动抗菌肽设计",
    ]
    for topic in topics:
        agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
        result = asyncio.run(agent.run(agent.create_initial_state(topic, analysis_mode="fast")))
        assert result.report_plan.report_type == "正式选题报告"
        assert [section["key"] for section in result.report_plan.sections] == [
            "abstract", "candidate_evaluation", "literature_trends",
            "cross_discipline", "conclusion", "references",
        ]


def test_report_is_published_after_automatic_quality_check() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    asyncio.run(agent._report_publish(result))

    assert result.quality_gate.review_required is False
    assert result.review_tasks == []
    assert result.report_plan.publication_status == "published"
    assert result.progress_events[-1].status == "completed"
    assert result.progress_events[-1].title == "正式报告已生成"


def test_topic_progress_events_cover_all_stages_and_are_monotonic() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    snapshots: list[tuple[str, int, int]] = []

    async def capture(state) -> None:
        if state.active_progress:
            snapshots.append((
                state.active_progress.stage_key,
                state.active_progress.sequence,
                state.active_progress.progress_percent,
            ))

    result = asyncio.run(agent.run(agent.create_initial_state("大模型预测蛋白质结构", analysis_mode="fast"), on_stage_update=capture))

    assert {item.stage_key for item in result.progress_events} == {key for key, _ in agent.STAGES}
    assert [item.sequence for item in result.progress_events] == list(range(1, len(result.progress_events) + 1))
    for stage_key, _ in agent.STAGES:
        values = [item.progress_percent for item in result.progress_events if item.stage_key == stage_key]
        assert values == sorted(values)
        assert values[-1] == 100
    assert any(item.step_key.startswith("query_") for item in result.progress_events)
    assert any(item.step_key.startswith("candidate_") for item in result.progress_events)
    assert snapshots


def test_topic_progress_event_history_is_bounded_and_old_state_is_compatible() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    state = agent.create_initial_state("单细胞多组学解析肿瘤微环境", analysis_mode="fast")

    async def emit_many() -> None:
        for index in range(130):
            await agent._emit_progress(
                state, None, stage_key="input_modeling", step_key=f"probe_{index}",
                title="进度测试", progress_percent=min(index, 99),
            )

    asyncio.run(emit_many())
    assert len(state.progress_events) == 120
    assert state.progress_events[0].sequence == 11
    legacy_payload = state.model_dump(exclude={"active_progress", "progress_events"})
    restored = type(state).model_validate(legacy_payload)
    assert restored.active_progress is None
    assert restored.progress_events == []


class FakeStageModel:
    def __init__(self, fail_stage: str = "") -> None:
        self.fail_stage = fail_stage
        self.calls: list[dict[str, object]] = []

    async def generate_json_with_metrics(self, **kwargs) -> DeepSeekJSONResult:
        payload = json.loads(str(kwargs["user_prompt"]))
        schema = payload.get("output_schema", {})
        keys = set(schema)
        if 'summary' in keys or 'checked' in keys:
            stage = 'report_publish'
            self.calls.append({'stage': stage, **kwargs})
            metrics = DeepSeekCallMetrics(profile='server_v4_flash', model='deepseek-v4-flash', stream=False,
                timeout_seconds=float(kwargs['timeout_seconds']), done_ms=20 + len(self.calls))
            if 'checked' in keys:
                result = {'checked': True, 'replacements': []}
            else:
                packet = payload['source_material']
                source = packet['sources'][0]['evidence_id']
                result = {key: [{'text': '研究建议采用相同对象开展方法对照。', 'evidence_ids': [source]}]
                    for key in ('summary', 'progress', 'opportunities', 'recommendations')}
                result['candidates'] = [{'id': c['id'], 'title': c['title'], 'reason': '拟比较不同方法的适用条件。',
                    'design': '建议固定研究对象开展对照。', 'conditions': '以独立样本检验迁移效果。',
                    'evidence_ids': [source]} for c in packet['candidates']]
            return DeepSeekJSONResult(payload=result, metrics=metrics)
        stage = {
            "boundary": "input_modeling",
            "themes": "theme_decomposition",
            "queries": "retrieval_evidence",
            "overview": "landscape_trend",
            "gaps": "crosslink_gap",
            "candidates": "candidate_generation",
            "deliberations": "scoring_ranking",
            "findings": "quality_gate",
            "executive_summary": "report_publish",
        }[next(key for key in (
            "boundary", "themes", "queries", "overview", "gaps", "candidates",
            "deliberations", "findings", "executive_summary",
        ) if key in keys)]
        self.calls.append({"stage": stage, **kwargs})
        metrics = DeepSeekCallMetrics(
            profile="server_v4_flash",
            model="deepseek-v4-flash",
            stream=False,
            timeout_seconds=float(kwargs["timeout_seconds"]),
            done_ms=20 + len(self.calls),
        )
        if stage == self.fail_stage:
            raise DeepSeekClientError("simulated model timeout", metrics=metrics)
        if stage == "input_modeling":
            result = {"concise_title": "蛋白质结构可验证研究", "boundary": "公开研究与实验验证边界", "research_question": "如何建立可验证的研究路线？", "missing_fields": [], "terms": []}
        elif stage == "theme_decomposition":
            result = {"themes": [], "relations": []}
        elif stage == "retrieval_evidence":
            result = {"queries": [], "coverage_warnings": []}
        elif stage == "landscape_trend":
            valid_ref = payload["evidence"][0]["evidence_id"]
            result = {
                "concise_title": "蛋白质结构可验证研究",
                "overview": "当前证据显示研究正在向可验证实验演进。",
                "trend_findings": [{"finding": "形成持续研究活动", "evidence_ids": [valid_ref, "E-UNKNOWN"]}],
                "method_evolution": [],
                "conflicts": [],
                "coverage_limitations": ["当前样本以摘要为主"],
            }
        elif stage == "crosslink_gap":
            result = {"gaps": [], "cross_opportunities": []}
        elif stage == "candidate_generation":
            result = {"candidates": []}
        elif stage == "scoring_ranking":
            result = {"deliberations": [], "portfolio_advice": "并行验证关键假设。"}
        elif stage == "quality_gate":
            result = {"findings": [], "review_required": False, "strong_claims": []}
        else:
            result = {
                "executive_summary": "围绕证据边界形成三个可验证方向。",
                "landscape_narrative": "研究主题及时间趋势均来自锁定证据。",
                "gap_narrative": "空白判断保留覆盖限制。",
                "candidate_comparison": "三个方向分别侧重方法、数据与场景验证。",
                "next_actions": ["核验全文", "开展小规模预研"],
                "limitations": ["当前证据以摘要为主"],
            }
        return DeepSeekJSONResult(payload=result, metrics=metrics)


def test_deep_analysis_calls_model_for_all_nine_stages_and_filters_unknown_evidence() -> None:
    model = FakeStageModel()
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch(), model_client=model)
    state = agent.create_initial_state(
        "大模型预测蛋白质结构，联系人 test@example.com，电话 13800138000",
        uploaded_files=[{"file_id": "f1", "filename": "研究材料", "text": "项目编号 ABCDEF123456，关键片段可用于验证。"}],
        analysis_mode="deep",
    )

    result = asyncio.run(agent.run(state))

    stages = [item["stage"] for item in model.calls]
    assert list(dict.fromkeys(stages)) == [key for key, _ in agent.STAGES]
    assert stages.count('crosslink_gap') == 2  # Empty opportunities get one bounded repair.
    assert len(result.model_calls) == len(model.calls)
    assert all(item.status == "completed" for item in result.model_calls)
    assert all(item.profile == "server_v4_flash" and item.model == "deepseek-v4-flash" for item in result.model_calls)
    assert all(item.duration_ms and item.duration_ms > 0 for item in result.model_calls)
    assert all(item.prompt_version for item in result.stage_trace)
    assert all(item.model_call_count >= 1 for item in result.stage_trace)
    assert all(item.model_duration_ms and item.model_duration_ms > 0 for item in result.stage_trace)
    sent_payloads = "\n".join(str(item["user_prompt"]) for item in model.calls)
    assert "test@example.com" not in sent_payloads
    assert "13800138000" not in sent_payloads
    assert "ABCDEF123456" not in sent_payloads
    landscape = result.stage_artifacts["landscape_trend"]
    assert landscape["rejected_evidence_refs"] == ["E-UNKNOWN"]
    assert landscape["analysis"]["trend_findings"][0]["evidence_ids"] == [result.evidence_pool[0].evidence_id]
    assert result.quality_gate.review_required is False
    assert any("未锁定证据编号" in item for item in result.quality_gate.warnings)


def test_deep_analysis_degradation_is_audited_without_creating_review_work() -> None:
    model = FakeStageModel(fail_stage="landscape_trend")
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch(), model_client=model)

    result = asyncio.run(agent.run(agent.create_initial_state("深度分析降级测试", analysis_mode="deep")))

    call = next(item for item in result.model_calls if item.stage_key == "landscape_trend")
    trace = next(item for item in result.stage_trace if item.stage_key == "landscape_trend")
    assert call.status == "degraded"
    assert call.error_type == "DeepSeekClientError"
    assert trace.model_status == "degraded"
    assert trace.model_duration_ms and trace.model_duration_ms > 0
    assert result.quality_gate.review_required is False
    assert result.review_tasks == []
    assert any("核心分析阶段" in item for item in result.quality_gate.warnings)


class AuthorizationFailingModel:
    def __init__(self) -> None:
        self.calls = 0
        self.profile_name = "server_v4_flash"
        self.model = "deepseek-v4-flash"
        self.base_url = "http://127.0.0.1:16703/v1"

    async def generate_json_with_metrics(self, **kwargs):
        self.calls += 1
        metrics = DeepSeekCallMetrics(
            profile=self.profile_name,
            model=self.model,
            stream=False,
            timeout_seconds=float(kwargs["timeout_seconds"]),
            status_code=403,
            done_ms=12,
            error_type="HTTPAuthorizationError",
            error="HTTP 403",
        )
        raise DeepSeekClientError("模型鉴权失败", metrics=metrics)


def test_model_403_stops_retries_and_skips_later_stage_requests() -> None:
    model = AuthorizationFailingModel()
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch(), model_client=model)
    result = agent.create_initial_state("模型鉴权失败回归测试", analysis_mode="deep")
    from app.agents.topic_selection.recovery import GenerationRecoveryRequired, failure_kind
    with pytest.raises(GenerationRecoveryRequired) as caught:
        asyncio.run(agent.run(result))

    assert model.calls == 1
    assert result.report_plan.publication_status != "published"
    assert result.report_snapshot.frozen is False
    first = result.model_calls[0]
    assert first.http_status == 403
    assert first.error_code == "HTTPAuthorizationError"
    assert first.retry_skipped is True
    assert first.attempt_count == 1
    assert all(item.status == "degraded" for item in result.model_calls)
    assert failure_kind(result, caught.value) == 'dependency'
    assert any(item.status == 'blocked' for item in result.stage_trace)
    assert result.stage_trace[-1].status == 'pending'
    assert result.analysis_policy.model_preflight_status == "unavailable"


def test_concise_title_replaces_instruction_text_but_explicit_title_is_preserved() -> None:
    model = FakeStageModel()
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch(), model_client=model)
    state = agent.create_initial_state(
        "请帮我生成报告，我想研究大模型预测蛋白质结构并开展实验验证，最终给我完整报告。",
        analysis_mode="deep",
    )
    result = asyncio.run(agent.run(state))
    assert result.input_profile.title == "蛋白质结构可验证研究"
    assert len(result.input_profile.title) <= 24
    assert result.report_plan.report_title == "蛋白质结构可验证研究智能选题报告"

    explicit = agent.create_initial_state("任意长输入", topic_title="用户明确标题", analysis_mode="deep")
    explicit_result = asyncio.run(agent.run(explicit))
    assert explicit_result.input_profile.title == "用户明确标题"

    legacy_fallback = agent._title_from_text("研究方向：面向科研绘图场景的智能体多轮意图理解与可控生成研究。重点研究局部修正。")
    assert len(legacy_fallback) <= 24
    assert not legacy_fallback.startswith("研究方向")


def test_report_v5_uses_evidence_charts_and_numbered_captions() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    result = asyncio.run(agent.run(agent.create_initial_state("多图表报告回归测试", analysis_mode="fast")))
    chart_types = {item["chart_type"] for item in result.report_plan.chart_specs}
    assert "network" in chart_types
    assert not chart_types & {"radar", "bubble"}

    from PIL import Image
    rendered = 0
    for spec in result.report_plan.chart_specs:
        content = _render_chart_png(spec)
        if not content:
            continue
        rendered += 1
        image = Image.open(io.BytesIO(content)).convert("RGB")
        assert any(not (red == green == blue) for red, green, blue in image.getdata())
    assert rendered >= 1

    html_content, _ = build_topic_html(result)
    html_text = html_content.decode("utf-8")
    included = {key for section in result.report_plan.section_specs for key in section.get("chart_keys", [])}
    for index, spec in enumerate(result.report_plan.chart_specs, start=1):
        if spec['key'] in included and _render_chart_png(spec):
            assert f"图 {spec['figure_number']} {spec['title']}" in html_text
    assert "color:#173f70" not in html_text
    assert "#004098" not in html_text


def test_user_reports_exclude_internal_scoring_audit_and_expose_no_evidence_package() -> None:
    agent = TopicSelectionAgent(retrieval_client=FakeTopicSearch())
    state = agent.create_initial_state(
        "生成式人工智能驱动抗菌肽设计",
        uploaded_files=[{"file_id": "f1", "filename": "内部参考", "text": "联系人 13800138000，项目编号 TOPSECRET123456。"}],
        analysis_mode="fast",
    )
    result = asyncio.run(agent.run(state))
    markdown, _ = build_topic_markdown(result)
    docx, _ = build_topic_docx(result)
    forbidden_text = ("总分", "权重", "排名", "评分分解", "附录 A", "附录 B")
    report_text = markdown.decode("utf-8")
    assert not any(term in report_text for term in forbidden_text)
    from docx import Document
    document = Document(io.BytesIO(docx))
    docx_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert not any(term in docx_text for term in forbidden_text)
    assert result.report_plan.evidence_package_manifest == []
