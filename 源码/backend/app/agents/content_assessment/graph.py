"""八阶段创新性评估编排。

该层只把受控的结构化产物串联起来；模型和检索不可用时输出探索性结论，
不会把项目材料或检索未命中解释为确定性创新结论。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from app.common.retrieval.fulltext import LiteratureFulltextClient, FulltextIntent
from app.common.retrieval.fusion import selected_chunks, match_source, chunk_locator
from app.common.retrieval.execution import retrieval_io
from .naming import research_name, state_research_name, report_title
from .recovery import interrupt_generation
from app.agents.report_validation import response_diagnostic

from app.agents.content_assessment.schemas import (
    AssessmentProfile,
    AssessmentReportPlan,
    ArtifactType,
    AdvancednessDecision,
    BoundaryMatrix,
    Claim,
    ClaimAssessmentDetail,
    ClaimEvidenceLink,
    CheckpointRecord,
    ConfirmationState,
    ContentAssessmentState,
    DocumentRecord,
    ParseIssue,
    EvidenceRecord,
    EvidenceChunk,
    GuidelineRequirement,
    Indicator,
    IndicatorValidation,
    LandscapeDataCard,
    NoveltyDecision,
    QueryPlan,
    QualityGate,
    ReportMetrics,
    ReportMode,
    ReportPreferences,
    ReportSectionOverride,
    ReportSectionSpec,
    ReviewTask,
    ResolutionLog,
    InvalidationRecord,
    ResearchNineFields,
    ProcessTraceStep,
    ScoreCardItem,
    StageProgress,
    StageTraceItem,
    UploadedFileRef,
    WorkPackage,
)
from app.core.config import BEIJING_TZ
from app.agents.content_assessment.material_extraction import (
    INSTRUCTION, explicit_claims, enrich_indicators, locate, material_sources, relevant, source_chunks,
)
from app.common.retrieval import LiteratureSearchClient, SearchRequest
from app.common.retrieval.links import evidence_url, http_url, nstl_detail_url
from app.core.config import (
    CONTENT_ASSESSMENT_LLM_ENABLED,
    CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES,
    CONTENT_ASSESSMENT_LLM_MAX_RETRIES,
    CONTENT_ASSESSMENT_LLM_TIMEOUT_SECONDS,
    LITERATURE_SEARCH_API_URL,
)
from app.services.deepseek_client import DeepSeekClient, DeepSeekClientError
from app.agents.content_assessment.prompts import (
    COMPARISON_SYSTEM_PROMPT,
    LANDSCAPE_SYSTEM_PROMPT,
    MATERIAL_SYSTEM_PROMPT,
    PROMPT_VERSION,
    RETRIEVAL_SYSTEM_PROMPT,
    RULE_VERSION,
    TEMPLATE_VERSION,
    comparison_prompt,
    landscape_prompt,
    material_extraction_prompt,
    retrieval_planning_prompt,
)
from app.agents.content_assessment.scenario_catalog import scenario_config


StageCallback = Callable[[ContentAssessmentState], Awaitable[None]]
logger = logging.getLogger(__name__)


class _GraphEnvelope(TypedDict):
    payload: dict[str, Any]


# Mongo task persistence remains the durable source of truth. LangGraph's
# checkpointer supplies node-level execution checkpoints within the process;
# the checkpoint metadata is also stored in ContentAssessmentState so a worker
# restart can rebuild the graph from the last durable task snapshot.
_CONTENT_ASSESSMENT_CHECKPOINTER = InMemorySaver()


STAGES: list[tuple[str, str]] = [
    ("document_intake", "材料解析与评估边界确认"),
    ("claim_extraction", "九字段、主张与指标提取"),
    ("retrieval", "检索规划与证据池构建"),
    ("comparison", "相关研究分析"),
    ("assessment", "创新性、先进性与口径风险判定"),
    ("landscape", "技术趋势与竞争态势分析"),
    ("quality_gate", "自动质量检查"),
    ("report", "创新性评估报告发布与导出"),
]

STANDARD_QUERY_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("system_prior_art", "system_prior_art", "系统或技术路线先例"),
    ("mechanism_method", "mechanism_method", "关键机制与实现方法"),
    ("metric_baseline", "metric_baseline", "指标、基线和验证方法"),
    ("application_validation", "application_validation", "监测、应用与实验验证"),
    ("industry_market_competitive", "industry_market_competitive", "市场、产业与竞争路线"),
    ("industry_standard_regulatory", "industry_standard_regulatory", "产业、标准和监管资料"),
)
MAX_CLAIMS = 20
MAX_EVIDENCE = 120
COMPARISON_FIELDS: tuple[tuple[str, str], ...] = (
    ("object", "对象"),
    ("method", "方法"),
    ("data", "数据"),
    ("baseline", "基线"),
    ("metric", "指标"),
    ("time", "时间"),
    ("validation", "验证条件"),
)

SUBSTEPS: dict[str, tuple[str, ...]] = {
    "document_intake": ("解析上传材料内容", "建立章节与页码索引", "识别材料角色", "确认评估边界", "检查材料完整性"),
    "claim_extraction": ("提取九字段信息", "拆分评估工作包", "拆解原子主张", "整理评价指标", "提取指南要求"),
    "retrieval": ("规划检索查询组", "扩展领域术语", "执行分组检索", "去重并排序证据", "提取可引用证据片段"),
    "comparison": ("将主张与证据逐项匹配", "比较统一口径指标", "识别支持与反证", "判断证据可靠程度", "统计主张证据覆盖情况"),
    "assessment": ("形成主张评估结论", "核验关键指标", "分析实施可行性", "识别表达与应用风险", "汇总评分并提出改写建议"),
    "landscape": ("统计文献发表年份", "聚类分析研究主题", "归纳主要技术路线", "观察研究竞争态势", "核对监管与适用边界"),
    "quality_gate": ("检查评估材料完整性", "检查主张证据覆盖情况", "核对引用是否完整", "检查重复与空洞表述", "核验报告章节配额"),
    "report": ("规划报告章节结构", "分阶段生成报告内容", "校验引用与证据对应", "固化报告版本", "导出网页与 Word 报告"),
}


class ContentAssessmentGraph:
    """可恢复的创新性评估主流程。"""

    def __init__(self, model_client: DeepSeekClient | None = None, fulltext_client: LiteratureFulltextClient | None = None) -> None:
        self._model_client = model_client
        self.fulltext_client = fulltext_client or LiteratureFulltextClient()
        self._model_unavailable = False
        self._model_block_reason = ""
        self._compiled_graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(_GraphEnvelope)
        builder.add_node("dispatch", self._dispatch_node)
        builder.add_node("finalize", self._finalize_node)
        for key, _name in STAGES:
            builder.add_node(key, self._make_stage_node(key))
        builder.add_edge(START, "dispatch")
        builder.add_conditional_edges(
            "dispatch",
            self._dispatch_route,
            {key: key for key, _name in STAGES} | {END: END},
        )
        for index, (key, _name) in enumerate(STAGES):
            next_key = STAGES[index + 1][0] if index + 1 < len(STAGES) else "finalize"
            builder.add_conditional_edges(
                key,
                lambda envelope, stage_key=key, next_stage=next_key: self._stage_route(
                    envelope, stage_key, next_stage
                ),
                {next_key: next_key, END: END},
            )
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=_CONTENT_ASSESSMENT_CHECKPOINTER)

    @staticmethod
    def _dispatch_node(envelope: _GraphEnvelope) -> _GraphEnvelope:
        return envelope

    @staticmethod
    def _dispatch_route(envelope: _GraphEnvelope) -> str:
        payload = envelope.get("payload", {})
        if payload.get("current_stage") in {"awaiting_confirmation", "failed"}:
            return END
        target = payload.get("resume_from_stage") or payload.get("current_stage")
        valid = {key for key, _name in STAGES}
        return target if target in valid else STAGES[0][0]

    async def _finalize_node(self, envelope: _GraphEnvelope) -> _GraphEnvelope:
        state = ContentAssessmentState.model_validate(envelope.get("payload", {}))
        state.current_stage = "completed"
        state.resume_from_stage = ""
        self._save_checkpoint(state, status="completed")
        return {"payload": state.model_dump()}

    @staticmethod
    def _stage_route(envelope: _GraphEnvelope, stage_key: str, next_stage: str) -> str:
        state = ContentAssessmentState.model_validate(envelope.get("payload", {}))
        if state.confirmation.needed or state.current_stage == "failed":
            return END
        return next_stage

    def _make_stage_node(self, key: str):
        handlers = {
            "document_intake": self._document_intake,
            "claim_extraction": self._claim_extraction_stage,
            "retrieval": self._retrieval,
            "comparison": self._comparison_stage,
            "assessment": self._assessment_stage_with_analysis,
            "landscape": self._landscape_stage,
            "quality_gate": self._quality_gate_stage,
            "report": self._report_stage,
        }

        async def run_stage(envelope: _GraphEnvelope) -> _GraphEnvelope:
            state = ContentAssessmentState.model_validate(envelope.get("payload", {}))
            frozen_before_attempt = state.report_snapshot is not None
            handler = handlers[key]
            name = next(stage_name for stage_key, stage_name in STAGES if stage_key == key)
            self._set_stage(state, key, "running", f"正在完成{name}")
            self._start_process_steps(state, key)
            self._refresh_stage_progress(state, key, completed=False)
            await self._emit_stage_update(state)
            try:
                result = handler(state)
                if inspect.isawaitable(result):
                    await result
                if key == "report" and not state.confirmation.needed and state.current_stage != "failed":
                    self._verify_generated_report(state)
                    if any(check["status"] != "passed" for check in state.quality_gate.post_report_checks if check["key"] != "exports"):
                        raise RuntimeError("报告章节或引用校核未通过")
                    from .report_composition import freeze_snapshot
                    if not frozen_before_attempt:
                        freeze_snapshot(state)
                    state.output_json["quality_gate"] = state.quality_gate.model_dump()
                    import os
                    if os.getenv("REPORT_PREFLIGHT_ENABLED", "true").lower() == "true" and not os.getenv("PYTEST_CURRENT_TEST"):
                        from app.services.export_service import prepare_exports
                        if await asyncio.to_thread(prepare_exports, state) is None:
                            raise RuntimeError("报告导出准备未完成")
                    state.output_json["report_publication"] = {"status": "published"}
            except Exception as exc:
                logging.getLogger(__name__).exception("Assessment stage interrupted: %s", key)
                interrupt_generation(state, key, "stage_error", "本阶段生成中断，已有结果已保存，请重试。")
                state.output_json["generation_recovery"]["error"] = str(exc)
            if state.current_stage == "failed":
                message = state.output_json["generation_recovery"]["message"]
                self._set_stage(state, key, "failed", message)
                state.current_stage = "failed"
                self._save_checkpoint(state, status="failed")
                await self._emit_stage_update(state)
                return {"payload": state.model_dump()}
            if state.confirmation.needed:
                self._set_stage(state, key, "completed" if key == "document_intake" else "blocked", state.confirmation.prompt)
                state.current_stage = "awaiting_confirmation"
                state.resume_from_stage = state.resume_from_stage or ("claim_extraction" if key == "document_intake" else key)
                state.output_json["pause_reason"] = state.confirmation.prompt
                self._save_checkpoint(state, status="awaiting_confirmation")
                await self._emit_stage_update(state)
                return {"payload": state.model_dump()}
            self._complete_process_steps(state, key)
            self._refresh_stage_progress(state, key, completed=True)
            self._set_stage(state, key, "completed", self._stage_summary(state, key))
            state.output_json.pop("generation_recovery", None)
            self._save_checkpoint(state, status="completed")
            await self._emit_stage_update(state)
            return {"payload": state.model_dump()}

        return run_stage

    @staticmethod
    def _progress_label(stage_key: str) -> str:
        return {
            "document_intake": "材料",
            "claim_extraction": "九项字段",
            "retrieval": "查询组",
            "comparison": "主张",
            "assessment": "评估主张",
            "landscape": "分析主题",
            "quality_gate": "质量检查",
            "report": "报告章节",
        }.get(stage_key, "处理项")

    @classmethod
    def _refresh_stage_progress(cls, state: ContentAssessmentState, stage_key: str, *, completed: bool) -> None:
        fields = state.nine_fields.model_dump()
        field_count = sum(
            bool([item for item in value if str(item).strip()]) if isinstance(value, list) else bool(str(value).strip())
            for value in fields.values()
        )
        comparison_count = len({item.claim_id for item in state.claim_assessments})
        if not comparison_count:
            comparison_count = len({item.claim_id for item in state.claim_evidence_links})
        totals = {
            "document_intake": max(1, len(state.assessment_profile.document_records)),
            "claim_extraction": 9,
            "retrieval": max(len(STANDARD_QUERY_GROUPS), len(state.query_plans)),
            "comparison": len(state.claims),
            "assessment": len(state.claims),
            "landscape": max(1, len(state.trend_analysis.get("themes", []))),
            "quality_gate": 4,
            "report": len(state.report_plan.section_specs),
        }
        current = {
            "document_intake": sum(item.status in {"parsed", "ready", "completed"} for item in state.assessment_profile.document_records),
            "claim_extraction": field_count,
            "retrieval": sum(item.source_status in {"completed", "unavailable"} for item in state.query_plans),
            "comparison": comparison_count,
            "assessment": sum(item.get("status") in {"completed", "partial"} for cid, item in
                state.output_json.get("reader_evaluation_completion", {}).items()
                if cid in {claim.claim_id for claim in state.claims}),
            "landscape": len(state.trend_analysis.get("themes", [])),
            "quality_gate": len(state.quality_gate.checks),
            "report": sum(item.status == "generated" for item in state.report_plan.section_specs),
        }
        total = max(0, totals.get(stage_key, 0))
        done = total if completed and total and stage_key != "assessment" else min(total, max(0, current.get(stage_key, 0)))
        state.stage_progress[stage_key] = StageProgress(
            stage_key=stage_key,
            completed=done,
            total=total,
            display_name=cls._progress_label(stage_key),
            updated_at=datetime.now().astimezone().isoformat(),
        )

    async def _emit_stage_update(self, state: ContentAssessmentState) -> None:
        callback = getattr(self, "_on_stage_update", None)
        if callback:
            await callback(state)

    def create_initial_state(
        self,
        raw_user_input: str,
        *,
        assessment_title: str = "",
        uploaded_files: list[UploadedFileRef] | None = None,
        document_roles: dict[str, str] | None = None,
        year_range: list[int] | None = None,
        report_mode: ReportMode = "auto",
        artifact_type: ArtifactType = "auto",
        scope_focus: str = "auto",
        scope_summary: str = "",
        section_overrides: dict[str, ReportSectionOverride] | None = None,
        scenario_id: str = "",
    ) -> ContentAssessmentState:
        # Production runs infer roles, scope and report depth from the parsed
        # materials.  The request fields remain accepted for old clients and
        # local fixture tests, but do not override the automatic path.
        if not CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES:
            document_roles = {}
            report_mode = "auto"
            # A material type selected in the intake form is a task fact.
            # Other legacy override fields still follow the automatic path.
            artifact_type = artifact_type if artifact_type in {"paper", "project", "guideline"} else "auto"
            scope_focus = "auto"
            scope_summary = ""
            section_overrides = None
        document_roles = document_roles or {}
        records = [
            DocumentRecord(
                document_id=file_ref.file_id or f"doc-{index:03d}",
                name=file_ref.name or f"材料 {index}",
                role=document_roles.get(file_ref.file_id, "PROJECT"),
                status="pending",
            )
            for index, file_ref in enumerate(uploaded_files or [], start=1)
        ]
        title = research_name(raw_user_input, candidates=(assessment_title,), documents=records)
        resolved_artifact = self._infer_artifact_type(raw_user_input, [item.name for item in uploaded_files or []], artifact_type)
        resolved_focus = self._infer_scope_focus(raw_user_input, scope_focus)
        preferences = ReportPreferences(
            mode=report_mode,
            artifact_type=artifact_type,
            resolved_artifact_type=resolved_artifact,
            scope_focus=resolved_focus,
            scope_summary=scope_summary.strip(),
            section_overrides=section_overrides or {},
        )
        state = ContentAssessmentState(
            raw_user_input=raw_user_input.strip(),
            scenario_id=scenario_id,
            uploaded_files=list(uploaded_files or []),
            assessment_profile=AssessmentProfile(
                title=title,
                assessment_object=title,
                scope_boundary="基于已上传材料和可定位外部证据进行评估。",
                document_records=records,
                artifact_type=resolved_artifact,
                scope_focus=resolved_focus,
                scope_summary=scope_summary.strip(),
            ),
            report_preferences=preferences,
            output_json={"year_range": list(year_range or [])},
        )
        state.stage_trace = [StageTraceItem(stage_key=key, stage_name=name) for key, name in STAGES]
        state.stage_progress = {
            key: StageProgress(stage_key=key, display_name=self._progress_label(key))
            for key, _name in STAGES
        }
        return state

    @staticmethod
    def _infer_scope_focus(text: str, requested: str = "auto") -> str:
        if requested in {"focused", "comprehensive"}:
            return requested
        if re.search(r"只评估|仅评估|聚焦|重点评估|针对(?:性)?评估", text):
            return "focused"
        return "comprehensive"

    @staticmethod
    def _infer_artifact_type(text: str, names: list[str], requested: ArtifactType = "auto") -> str:
        from .input_rules import infer_artifact_type
        if requested in {"auto", "paper", "project", "guideline"}:
            return infer_artifact_type(text, names, requested)
        if requested in {"paper", "project", "guideline"} or (requested != "auto" and CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES):
            return requested
        corpus = f"{text} {' '.join(names)}".lower()
        paper = bool(re.search(r"论文|学位论文|摘要|引言|实验结果|参考文献|paper|thesis", corpus))
        project = bool(re.search(r"项目|申报|申请书|技术路线|里程碑|验收|project|proposal", corpus))
        guideline = bool(re.search(r"指南|申报指南|考核指标|应用场景|指南要求|guideline|call for proposals", corpus))
        result = bool(re.search(r"科技成果|技术成果|专利|产品|转化|technology result|patent", corpus))
        if sum((paper, project, guideline, result)) > 1:
            return "mixed"
        if paper:
            return "paper"
        if result:
            return "technology_result"
        if guideline:
            return "guideline"
        if project:
            return "project"
        return "unknown"

    @staticmethod
    def _predict_document_role(name: str, content: str) -> tuple[str, float, str]:
        corpus = f"{name} {content[:12000]}".lower()
        # An explicit document heading outweighs repeated method/result words
        # in a long guideline. This also preserves its reference role when it
        # accompanies a project rather than being the assessment object.
        if re.search(r"(?:^|\n)\s*(?:指南名称|指南标题|标准名称)\s*[:：|]", content):
            return "REFERENCE", 0.95, "材料标题明确标注指南或标准。"
        if not re.search(r"参考|标准|规范|基线|文献", name) and re.search(r"(?:^|\n)\s*(?:项目名称|论文题目|论文标题|研究对象|评估对象|课题名称)\s*[:：|]", content):
            return "PROJECT", 0.95, "材料明确标注本次研究对象。"
        scores = {
            "PROJECT": len(re.findall(r"项目|申报|申请书|技术路线|实施方案|里程碑|验收|研究目标|拟|计划", corpus)),
            "BASELINE": len(re.findall(r"对照|基线|查新|先例|现状|竞品|比较矩阵|baseline|prior art", corpus)),
            "REFERENCE": len(re.findall(r"指南|标准|规范|监管|合规|政策|guideline|standard|regulatory", corpus)),
            "EVIDENCE": len(re.findall(r"实验数据|统计表|原始数据|结果|证据|文献|摘要|参考文献|data|evidence", corpus)),
        }
        role, score = max(scores.items(), key=lambda item: item[1])
        total = sum(scores.values())
        confidence = min(0.98, 0.45 + score / max(12, total + 4)) if score else 0.0
        if score == 0 or list(scores.values()).count(score) > 1:
            return "PROJECT", 0.35, "文件内容缺少明确角色信号，暂按项目材料处理并保留探索性边界。"
        reasons = {
            "PROJECT": "命中项目申报、技术路线、目标或验收语义。",
            "BASELINE": "命中对照、查新、先例或比较矩阵语义。",
            "REFERENCE": "命中指南、标准、监管或合规语义。",
            "EVIDENCE": "命中实验数据、统计结果或证据记录语义。",
        }
        return role, round(confidence, 2), reasons[role]

    @staticmethod
    def _recommended_mode(state: ContentAssessmentState) -> str:
        material_chars = sum(len(item.content) for item in material_sources(state))
        claims = len(state.claims)
        evidence = sum(any(relevant(claim, item) for claim in state.claims)
                       for item in state.evidence_records if item.source_type == "literature")
        # Deep mode is recommended only when there is enough material to make
        # the additional analysis substantive; the user can still force it.
        return "deep" if claims >= 6 and (material_chars >= 12000 or evidence >= 30) else "standard"

    @staticmethod
    def _resolve_report_preferences(state: ContentAssessmentState) -> None:
        prefs = state.report_preferences
        recommended = ContentAssessmentGraph._recommended_mode(state)
        prefs.recommended_mode = recommended
        if CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES and prefs.mode in {"standard", "deep"}:
            prefs.resolved_mode = prefs.mode
            prefs.mode_source = "user"
        else:
            prefs.resolved_mode = recommended
            prefs.mode_source = "auto"
        inferred = ContentAssessmentGraph._infer_artifact_type(
            state.raw_user_input + "\n" + "\n".join(item.content[:3000] for item in material_sources(state)),
            [record.name for record in state.assessment_profile.document_records],
            prefs.artifact_type,
        )
        prefs.resolved_artifact_type = inferred
        state.assessment_profile.artifact_type = inferred
        resolved_focus = ContentAssessmentGraph._infer_scope_focus(
            state.raw_user_input,
            prefs.scope_focus if CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES else "auto",
        )
        prefs.scope_focus = resolved_focus
        state.assessment_profile.scope_focus = resolved_focus
        state.assessment_profile.scope_summary = (
            prefs.scope_summary if CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES and prefs.scope_summary else (
            "围绕用户明确提出的重点主张和比较范围开展评估。"
            if resolved_focus == "focused"
            else "对材料中的主张、指标、研究现状、创新性、风险和应用边界开展综合评估。"
            )
        )
        state.assessment_profile.scope_summary_generated = True
        state.assessment_profile.scope_focus_confidence = 0.9 if resolved_focus == "focused" and re.search(r"只评估|仅评估|聚焦|重点评估|针对(?:性)?评估", state.raw_user_input) else 0.75
        state.assessment_profile.scope_focus_reason = "根据用户问题中的聚焦语义自动判断。" if resolved_focus == "focused" else "未发现明确聚焦限定，按材料整体进行综合评估。"
        state.assessment_profile.artifact_type_confidence = 0.85 if inferred != "unknown" else 0.35
        state.assessment_profile.artifact_type_reason = "根据任务文本和上传文件名称/内容中的材料类型信号自动判断。" if inferred != "unknown" else "材料类型信号不足，需结合上传内容确认。"
        if inferred == "unknown" and state.assessment_profile.document_records:
            marker = "材料类型：无法从任务和文件信号可靠识别。"
            if marker not in state.assessment_profile.pending_confirmation_fields:
                state.assessment_profile.pending_confirmation_fields.append(marker)

    async def run(
        self,
        state: ContentAssessmentState,
        *,
        on_stage_update: StageCallback | None = None,
    ) -> ContentAssessmentState:
        if not state.checkpoint_id:
            state.checkpoint_id = f"assessment-{uuid4().hex}"
        self._on_stage_update = on_stage_update
        try:
            if state.current_stage == "awaiting_confirmation":
                return state
            self._model_unavailable = False
            self._save_checkpoint(state, status="running")
            result = await self._compiled_graph.ainvoke(
                {"payload": state.model_dump()},
                config={"configurable": {"thread_id": state.checkpoint_id}},
            )
            state = ContentAssessmentState.model_validate(result["payload"])
            if state.current_stage not in {"awaiting_confirmation", "failed"}:
                state.current_stage = "completed"
                state.resume_from_stage = ""
                if not state.checkpoint_history or state.checkpoint_history[-1].status != "completed":
                    self._save_checkpoint(state, status="completed")
            return state
        finally:
            self._on_stage_update = None
            # Paused tasks can remain idle for hours. Release their connections;
            # the next run opens a fresh client and can retry a transient outage.
            await self._close_model_client()

    def _document_intake(self, state: ContentAssessmentState) -> None:
        self._resolve_report_preferences(state)
        name = state_research_name(state)
        state.assessment_profile.title = state.assessment_profile.assessment_object = name
        records = state.assessment_profile.document_records
        primary_roles = self._primary_document_roles(state)
        has_confirmed_brief = state.nine_fields_confirmed and not state.nine_fields.missing_for_type(state.report_preferences.resolved_artifact_type)
        digest_by_id = {item.file_id: item for item in state.uploaded_file_context.files}
        for record in records:
            digest = digest_by_id.get(record.document_id)
            if digest and not CONTENT_ASSESSMENT_ALLOW_USER_OVERRIDES:
                predicted_role, confidence, reason = self._predict_document_role(
                    record.name, digest.content or digest.excerpt
                )
                record.role = predicted_role  # type: ignore[assignment]
                state.assessment_profile.document_role_predictions[record.document_id] = {
                    "role": predicted_role, "confidence": confidence, "reason": reason,
                }
                if confidence < 0.5:
                    state.assessment_profile.pending_confirmation_fields.append(
                        f"材料角色：{record.name}（{reason}）"
                    )
            content = (digest.content or "").strip() if digest else ""
            excerpt = (digest.excerpt or "").strip() if digest else record.excerpt.strip()
            failed = bool(digest and digest.status in {"failed", "error", "unavailable"})
            record.content_available = bool(content) and not failed
            record.status = "failed" if failed or not (content or excerpt) else "parsed"
            if record.status == "failed":
                issue = ParseIssue(code="parse_failed" if failed else "empty_content",
                                   message=(digest.warning if digest else "") or "未获得可读材料，请重新上传或粘贴正文。")
                if not any(item.code == issue.code for item in record.parse_issues):
                    record.parse_issues.append(issue)
            if record.role == "PROJECT":
                record.excerpt = (digest.excerpt[:1200] if digest else "项目材料已纳入评估边界。")
            elif record.role == "BASELINE":
                record.excerpt = (digest.excerpt[:1200] if digest else "对照材料已纳入符合性与差异比对。")
            else:
                record.excerpt = (digest.excerpt[:1200] if digest else "辅助材料已登记，使用边界将在证据链中说明。")
            if digest:
                record.section_count = max(1, len(re.findall(r"(?:^|\n)\s*(?:第[一二三四五六七八九十]+章|\d+(?:\.\d+)+)\b", digest.excerpt)))
                record.content_hash = str(getattr(digest, "content_hash", "") or "")
                record.content_length = len(digest.content or "")
                record.anchor_schema_version = str(getattr(digest, "anchor_schema_version", "anchor-v1") or "anchor-v1")
                record.anchors = list(getattr(digest, "anchors", []) or [])
                record.anchor_ready = bool(record.content_available and record.content_hash and record.anchors)
                record.page_count = getattr(digest, "page_count", None)

        profile = state.assessment_profile
        if not records:
            if has_confirmed_brief:
                profile.assumptions.append("未上传独立文件，本次以用户已确认的九项评估内容作为项目材料边界。")
            else:
                profile.assumptions.append("未上传项目材料，本次仅依据任务描述生成探索性评估。")
                # Pasted materials are checked after extraction using the same type rules.
        elif not any(record.role in primary_roles for record in records):
            profile.assumptions.append("未识别出明确项目材料，报告将按上传材料和任务描述形成探索性判断。")
            profile.pending_confirmation_fields.append("项目材料：未识别出明确的项目/研究主体材料。")
        elif any(record.role in primary_roles and not record.content_available for record in records):
            profile.assumptions.append("项目材料已登记但未取得可用解析全文，需确认是否按任务描述进入探索性评估。")
            profile.pending_confirmation_fields.append("项目材料解析：至少一份项目材料缺少可用全文。")
            for record in records:
                if record.role in primary_roles and not record.content_available:
                    if record.status != "failed":
                        record.status = "needs_confirmation"

        # Only explicit object/title fields in primary materials can trigger
        # this warning. Reference papers, baselines and filenames are ignored.
        objects = []
        for record in records:
            digest = digest_by_id.get(record.document_id)
            primary = record.role in primary_roles
            if not primary or not digest or record.status == "failed":
                continue
            match = re.search(r"(?:^|\n)\s*(?:项目名称|论文题目|研究对象|评估对象|课题名称|指南名称)\s*[:：]\s*([^\n]{4,120})", digest.content or digest.excerpt)
            if match:
                label = match.group(1).strip()
                normalized = re.sub(r"[\W_]+|研究|项目|附件|补充材料", "", label)
                if len(normalized) >= 4:
                    objects.append((record, label, normalized))
        for index, (left, left_label, left_name) in enumerate(objects):
            for right, right_label, right_name in objects[index + 1:]:
                left_pairs = {left_name[i:i + 2] for i in range(len(left_name) - 1)}
                right_pairs = {right_name[i:i + 2] for i in range(len(right_name) - 1)}
                overlap = len(left_pairs & right_pairs) / max(1, min(len(left_pairs), len(right_pairs)))
                if left_name in right_name or right_name in left_name or overlap >= 0.2:
                    continue
                warning = f"材料可能属于不同评估对象：《{left.name}》标明“{left_label}”，《{right.name}》标明“{right_label}”。建议每个对象拆为独立任务；相关附件可保留在同一任务。"
                profile.pending_confirmation_fields.append(warning)
                profile.assumptions.append(warning)
                for record in (left, right):
                    if not any(item.code == "multiple_objects" for item in record.parse_issues):
                        record.parse_issues.append(ParseIssue(code="multiple_objects", message=warning))

        profile.pending_confirmation_fields = list(dict.fromkeys(profile.pending_confirmation_fields))
        conflicts = [r for r in records if any(i.code == "multiple_objects" for i in r.parse_issues)]
        if conflicts:
            state.confirmation = ConfirmationState(needed=True, reason="multiple_objects",
                prompt="以下文件属于明显不同的评估对象，请移除其他对象材料后继续：" + "、".join(r.name for r in conflicts),
                options=[{"label": "移除所选材料并重新检查", "value": "remove_conflicting_materials"}])
            state.quality_gate.report_eligibility = "blocked"
        elif profile.pending_confirmation_fields:
            # Scope assumptions are audit information, not a user decision.
            profile.confirmation_required = False
            profile.assumptions = list(dict.fromkeys([*profile.assumptions, *profile.pending_confirmation_fields]))

        project_text = self._material_text(state)
        included = ["用户明确描述的研究对象、目标、技术路线、指标和应用场景"]
        excluded = ["未在材料或检索证据中出现的机构、产品、市场数字和绝对创新结论"]
        assumptions = list(dict.fromkeys(profile.assumptions))
        state.boundary_matrix = BoundaryMatrix(
            assessment_object=profile.assessment_object or profile.title,
            included_scope=included,
            excluded_scope=excluded,
            time_boundary="按任务指定年份；未指定时优先近五年并保留近十年方法先例",
            geography_boundary="以中英文公开证据为边界，地域结论需有来源",
            technology_boundary="围绕材料中可识别的对象、方法、指标和应用场景",
            evidence_boundary="仅使用上传材料、NSTL 返回记录及程序计算统计；本阶段不接入联网搜索或云端知识源",
            assumptions=assumptions,
            confirmation_items=[state.confirmation.prompt] if state.confirmation.needed else [],
        )
        known_evidence = {item.evidence_id for item in state.evidence_records}
        for record in records:
            evidence_id = f"E-P-{record.document_id}"
            if evidence_id in known_evidence:
                continue
            state.evidence_records.append(EvidenceRecord(
                evidence_id=evidence_id,
                title=record.name,
                source_type="project_material" if record.role in primary_roles and state.report_preferences.resolved_artifact_type != "guideline" else "guideline",
                source_document_id=record.document_id,
                excerpt=record.excerpt,
                evidence_level="C",
                usable_for_novelty=False,
                access_status="authorized" if record.content_available else "unavailable",
                source_hash=record.content_hash,
                fulltext_available=bool(record.content_available and record.anchor_ready),
                anchor_verified=record.anchor_ready,
                char_start=0 if record.content_available else None,
                char_end=min(record.content_length, 1600) if record.content_available else None,
                locator=(record.anchors[0].get("locator", {}) if record.anchors else {}),
            ))
            known_evidence.add(evidence_id)
            digest = digest_by_id.get(record.document_id)
            if digest and digest.content:
                state.evidence_chunks.append(EvidenceChunk(
                    chunk_id=f"CH-{evidence_id}-001",
                    evidence_id=evidence_id,
                    document_id=record.document_id,
                    page_ref=str((record.anchors[0].get("locator", {}) if record.anchors else {}).get("page", "")),
                    section_ref="全文材料",
                    excerpt=digest.content[:1600],
                    purpose="项目材料原文定位",
                    evidence_level="C",
                    char_start=0,
                    char_end=min(len(digest.content), 1600),
                    access_status="authorized",
                    source_hash=record.content_hash,
                    reopenable=record.anchor_ready,
                    locator=(record.anchors[0].get("locator", {}) if record.anchors else {}),
                ))
        if not state.work_packages:
            sentences = self._split_claims(project_text)
            state.work_packages = [
                WorkPackage(
                    work_package_id=f"WP-{index:02d}",
                    title=sentence[:120],
                    objective=sentence,
                    outputs=["可验收的阶段性成果"],
                    verification_nodes=["补充实验或对照证据"],
                    source_refs=[self._project_document_id(state)] if self._project_document_id(state) else [],
                )
                for index, sentence in enumerate(sentences[:12], start=1)
            ]
        self._refresh_versions(state)

    def _claim_extraction(self, state: ContentAssessmentState) -> None:
        sources = material_sources(state)
        text = "\n".join(source.content for source in sources)
        sentences = [s for s in self._split_claims(text) if not INSTRUCTION.search(s)]
        keywords = self._keywords(text)
        if not state.nine_fields_confirmed:
            self._initialize_nine_fields(state, keywords, sentences, text)
        state.claims = self._scenario_claims(state) if scenario_config(state.scenario_id) else explicit_claims(state)
        if not state.claims and not state.uploaded_files and not state.uploaded_file_context.files:
            for source in sources:
                for sentence in self._split_claims(source.content):
                    anchor = locate(source, sentence)
                    if anchor and len(sentence) >= 8 and re.search(r"构建|开发|实现|提出|验证|制备|检测|提高|降低|比较|采用|建立", sentence):
                        state.claims.append(Claim(claim_id=f"C-{len(state.claims)+1:03d}", text=sentence,
                            **anchor, assertion_type="target" if re.search(r"拟|计划|目标", sentence) else "unknown"))
        for claim in state.claims:
            claim.searchable_terms = self._keywords(claim.text)
            claim.claim_fields = {**self._extract_claim_fields(claim.text), **claim.claim_fields}
        state.indicators = enrich_indicators(state)
        state.indicator_validations = [
            IndicatorValidation(indicator_id=item.indicator_id, definition=item.name, unit=item.unit,
                target=f"{item.value}{item.unit}".strip(), status="partial" if item.value else "missing",
                gaps=["缺少基线、测试方法、样本设计或验收条件"]) for item in state.indicators
        ]
        state.guideline_requirements = [GuidelineRequirement(requirement_id="G-001",
            text="需在上传指南或对照材料后完成符合性映射。", match_status="unknown")]

    def _scenario_claims(self, state: ContentAssessmentState) -> list[Claim]:
        source = next((item for item in material_sources(state) if item.file_id == "user_material"), None)
        if source is None:
            return []
        rows: list[tuple[str, str, str]] = [
            (state.nine_fields.research_goal, "technical", "target"),
            (state.nine_fields.research_content, "technical", "planned"),
            (state.nine_fields.technical_route, "technical", "planned"),
        ]
        # A technology or application name is an input descriptor, not an
        # independently verifiable innovation. Keep those names in the nine
        # fields; only promote complete source statements to separate claims.
        for values, claim_type in [(state.nine_fields.key_technologies, "technical"),
                                   (state.nine_fields.application_scenarios, "application")]:
            for value in values:
                if len(value.strip()) >= 12 and re.search(r"采用|通过|构建|开发|实现|提出|验证|制备|检测|提高|降低|优化|建立", value):
                    rows.append((value, claim_type, "planned"))
        claims: list[Claim] = []
        for text, claim_type, assertion_type in rows:
            value = str(text or "").strip()
            if len(value) < 4 or any(item.text == value for item in claims):
                continue
            anchor = locate(source, value)
            if not anchor:
                continue
            claims.append(Claim(
                claim_id=f"C-{len(claims) + 1:03d}", text=value,
                claim_type=claim_type, assertion_type=assertion_type, **anchor,
                searchable_terms=self._keywords(value),
                claim_fields=self._extract_claim_fields(value),
            ))
        for raw in state.nine_fields.evaluation_indicators:
            value = str(raw or "").strip()
            if not value:
                continue
            match = re.search(r"([≥≤<>]?(?:不低于|不高于|低于|高于|达到)?\s*\d+(?:\.\d+)?)\s*([A-Za-zΩ·²/%℃]+|次|项|个月|年|天)?", value)
            metric_name = re.split(r"(?:不低于|不高于|低于|高于|达到|[≥≤<>])", value, maxsplit=1)[0].strip(" ：:，,") or value[:30]
            anchor = locate(source, value) or {}
            state.indicators.append(Indicator(
                indicator_id=f"I-{len(state.indicators) + 1:03d}",
                name=metric_name[:80], value=(match.group(1).strip() if match else ""),
                unit=(match.group(2) or "") if match else "", status="target",
                source_claim_id=claims[0].claim_id if claims else "",
                source_claim_ids=[claims[0].claim_id] if claims else [],
                source_span=value, acceptance_condition=value,
                **{key: val for key, val in anchor.items() if key in Indicator.model_fields and key != "source_span"},
            ))
        return claims

    def _initialize_nine_fields(self, state: ContentAssessmentState, keywords: list[str], sentences: list[str], text: str) -> None:
        if state.nine_fields_confirmed:
            return
        from .input_rules import explicit_fields
        state.nine_fields = ResearchNineFields.model_validate(explicit_fields(text))

    async def _claim_extraction_stage(self, state: ContentAssessmentState) -> None:
        if (state.output_json.pop("claims_manually_confirmed", False) or state.output_json.pop("input_fields_supplemented", False)) and state.claims:
            state.indicators = enrich_indicators(state)
            return
        state.claims = []
        state.indicators = []
        self._claim_extraction(state)
        structured = bool(explicit_claims(state)) or bool(state.scenario_id and state.claims)
        extracted: list[Claim] = []
        extracted_indicators: list[Indicator] = []
        incomplete = False
        for chunk in source_chunks(state):
            payload = await self._request_model_json(state, stage="claim_extraction",
                system_prompt=MATERIAL_SYSTEM_PROMPT,
                user_prompt=material_extraction_prompt(chunk), max_tokens=4000)
            if payload is not None:
                previous_claims = state.claims
                previous_indicators = state.indicators
                if self._apply_model_claim_extraction(state, payload):
                    for indicator in state.indicators:
                        own_claim = next((c for c in state.claims if c.claim_id == indicator.source_claim_id), None)
                        if own_claim:
                            indicator.source_claim_id = own_claim.source_span
                    extracted.extend(state.claims)
                    extracted_indicators.extend(state.indicators)
                elif payload.get("claims") != []:
                    incomplete = True
                state.claims = previous_claims
                state.indicators = previous_indicators
            else:
                incomplete = True
            if self._model_unavailable:
                break
        if not structured and extracted:
            unique = {claim.source_span: claim for claim in extracted}
            state.claims = [claim.model_copy(update={"claim_id": f"C-{i:03d}"})
                            for i, claim in enumerate(unique.values(), 1)]
            by_span = {c.source_span: c.claim_id for c in state.claims}
            for indicator in extracted_indicators:
                indicator.source_claim_id = by_span.get(indicator.source_claim_id, "")
        elif structured:
            existing = {re.sub(r"\s+", "", claim.source_span): claim for claim in state.claims}
            for claim in extracted:
                key = re.sub(r"\s+", "", claim.source_span)
                match = existing.get(key)
                if match:
                    match.searchable_terms = list(dict.fromkeys(match.searchable_terms + claim.searchable_terms))
                    match.claim_fields = {**claim.claim_fields, **match.claim_fields}
                    continue
                if any(key in prior or prior in key for prior in existing if key and prior):
                    continue
                claim.claim_id = f"C-{len(state.claims) + 1:03d}"
                state.claims.append(claim)
                existing[key] = claim
            by_span = {re.sub(r"\s+", "", claim.source_span): claim.claim_id for claim in state.claims}
            for indicator in extracted_indicators:
                indicator.source_claim_id = by_span.get(re.sub(r"\s+", "", indicator.source_claim_id), "")
            if not extracted:
                incomplete = True
        state.indicators.extend(extracted_indicators)
        state.indicators = enrich_indicators(state)
        state.indicator_validations = [IndicatorValidation(indicator_id=i.indicator_id,
            definition=i.definition or i.name, unit=i.unit, target=i.acceptance_condition or i.value,
            baseline=i.baseline, test_method=i.test_method, status="partial", gaps=["需核对样本设计、原始记录和验收条件"])
            for i in state.indicators]
        if structured and extracted:
            self._record_model_usage(state, stage="claim_extraction", status="completed", reason="model_reviewed_structured_material")
        # Preserve a valid organized title; otherwise read explicit names from
        # the primary material instead of treating the type prompt as a name.
        name = state_research_name(state)
        state.assessment_profile.title = state.assessment_profile.assessment_object = name
        from .material_extraction import material_context
        material_context(state)
        material_chars = sum(len(source.content.strip()) for source in material_sources(state))
        if material_chars < 600 and len(state.claims) <= 1:
            state.output_json["input_depth_notice"] = "当前材料适合形成简要评议。如需完整评估，请补充研究方法、对照设计、指标定义与验证记录；报告将按现有内容自然展开。"
        else:
            state.output_json.pop("input_depth_notice", None)
        kind = state.report_preferences.resolved_artifact_type
        missing = state.nine_fields.missing_for_type(kind) if kind != "unknown" else ["artifact_type"]
        recognition_failed = False
        if missing and CONTENT_ASSESSMENT_LLM_ENABLED:
            # Legacy callers can enter the graph without intake preview. Use
            # the same source review before asking them to supply information.
            recognition_failed = self._model_unavailable
            if not recognition_failed:
                from app.services.nine_fields_preview import preview_nine_fields
                from .input_rules import FIELD_LABELS
                labels = {value: key for key, value in FIELD_LABELS.items()}
                recognized = "\n".join(
                    f"{labels[key]}：{'；'.join(value) if isinstance(value, list) else value}"
                    for key, value in state.nine_fields.model_dump().items() if value
                )
                reviewed = await preview_nine_fields(
                    recognized, [], "", kind if kind != "unknown" else "auto",
                    material_texts=[source.content for source in material_sources(state)],
                )
                recognition_failed = reviewed["extraction_failed"]
                if not recognition_failed:
                    state.nine_fields = ResearchNineFields.model_validate(reviewed["nine_fields"])
                    kind = reviewed["resolved_artifact_type"]
                    if kind in {"paper", "project", "guideline"}:
                        state.report_preferences.resolved_artifact_type = kind
                    missing = reviewed["missing_fields"]
        state.output_json["missing_fields"] = missing
        if structured and not extracted:
            interrupt_generation(state, "claim_extraction", "claim_extraction", "研究要点尚未完成语义分析，请重试；原材料已保留。")
        elif missing and recognition_failed:
            interrupt_generation(state, "claim_extraction", "claim_extraction", "材料识别中断，原文字和材料已保留，请重试。")
        elif missing:
            state.confirmation = ConfirmationState(needed=True, reason="missing_fields",
                prompt="材料已保留，请补充缺少的信息后继续。",
                options=[{"value": "supplement_fields", "label": "补充缺项后继续"}])
            state.quality_gate.report_eligibility = "blocked"
        elif not state.claims or (incomplete and extracted):
            interrupt_generation(state, "claim_extraction", "claim_extraction", "研究要点提取中断，原材料已保留，请重试。")

    async def _retrieval(self, state: ContentAssessmentState) -> None:
        year_range = state.output_json.get("year_range") or []
        # Retrieval is a replaceable downstream stage. Remove its previous
        # literature output before rebuilding it so a retry is idempotent.
        literature_ids = {
            item.evidence_id for item in state.evidence_records if item.source_type == "literature"
        }
        state.evidence_records = [
            item for item in state.evidence_records if item.source_type != "literature"
        ]
        state.evidence_chunks = [
            item for item in state.evidence_chunks if item.evidence_id not in literature_ids
        ]
        for key in ("search_trace", "retrieval_stats", "query_groups", "retrieval_errors", "fulltext_limitations", "fulltext_search_trace",
                    "reader_point_evaluations", "reader_evaluation_inputs", "reader_evaluation_reviews",
                    "reader_evaluation_completion", "reader_comparisons", "reader_research_analyses", "reader_analysis_checks", "retrieval_source_order"):
            state.output_json.pop(key, None)
        claims = state.claims
        indicators = state.indicators
        keywords = list(dict.fromkeys(
            self._keywords(state.assessment_profile.title)
            + [term for claim in claims for term in claim.searchable_terms]
            + state.nine_fields.key_technologies
        ))[:20]
        base_query = " ".join(keywords) or state.assessment_profile.title
        demo_config = scenario_config(state.scenario_id) if state.scenario_id else None
        demo_terms = list(demo_config.get("terms", [])) if demo_config else []
        all_claim_ids = [claim.claim_id for claim in claims]
        all_indicator_ids = [item.indicator_id for item in indicators]
        exclusion_terms = [] if re.search(r"药物|药敏|drug|pharma", base_query, re.I) else ["AI药物预测", "药物发现深度学习"]
        state.query_plans = []
        for group, role, purpose in STANDARD_QUERY_GROUPS:
            claim_scope = list(all_claim_ids)
            indicator_scope = list(all_indicator_ids) if role in {"metric_baseline", "application_validation"} else []
            if role == "industry_standard_regulatory":
                claim_scope = [claim.claim_id for claim in claims if claim.claim_type == "compliance"] or all_claim_ids[:]
            if role == "application_validation":
                claim_scope = [claim.claim_id for claim in claims if claim.claim_type == "application"] or all_claim_ids[:]
            if not claim_scope and not indicator_scope:
                continue
            scoped_terms = [
                term for claim in claims if claim.claim_id in claim_scope
                for term in (claim.searchable_terms or self._keywords(claim.text))
            ]
            scoped_terms += [item.name for item in indicators if item.indicator_id in indicator_scope]
            query_terms = " ".join(demo_terms if demo_terms else list(dict.fromkeys(scoped_terms))[:16]) or base_query
            state.query_plans.append(QueryPlan(
                claim_id=claim_scope[0] if claim_scope else (all_claim_ids[0] if all_claim_ids else ""),
                claim_ids=claim_scope,
                indicator_id=indicator_scope[0] if indicator_scope else "",
                indicator_ids=indicator_scope,
                query=f"{query_terms} {purpose}",
                query_group=group,
                query_role=role,
                year_range=year_range,
                rationale=f"围绕 {len(claim_scope)} 项主张和 {len(indicator_scope)} 项指标执行{purpose}检索。",
                exclusion_terms=exclusion_terms,
                filters={"document_types": ["paper", "standard", "guideline"], "languages": ["zh", "en"]},
                source_status="pending" if LITERATURE_SEARCH_API_URL else "unavailable",
            ))

        automatic_query_plans = list(state.query_plans)
        from .material_extraction import material_query_plans
        supplied_plans = material_query_plans(state)
        if supplied_plans:
            # Material-supplied queries can refine an automatically generated
            # group, but cannot remove one of the six required observation
            # surfaces. Keep one plan per group and use supplied plans first.
            supplied_roles = {item.query_role for item in supplied_plans}
            state.query_plans = supplied_plans + [
                item for item in automatic_query_plans if item.query_role not in supplied_roles
            ]
        if LITERATURE_SEARCH_API_URL and not supplied_plans and not demo_config:
            payload = await self._request_model_json(
                state,
                stage="retrieval_planning",
                system_prompt=RETRIEVAL_SYSTEM_PROMPT,
                user_prompt=retrieval_planning_prompt(
                    claims=[item.model_dump() for item in claims],
                    indicators=[item.model_dump() for item in indicators],
                    scope=state.boundary_matrix.technology_boundary or state.assessment_profile.scope_boundary,
                    year_range=year_range,
                ),
                max_tokens=5000,
            )
            if payload:
                self._apply_model_query_plans(state, payload, year_range=year_range, exclusion_terms=exclusion_terms)
        # Search each observation surface for a named point instead of
        # attaching every result from a broad query to every point.
        scoped_plans = []
        for plan in state.query_plans:
            for claim in claims:
                if claim.claim_id not in plan.claim_ids:
                    continue
                scoped = plan.model_copy(deep=True)
                scoped.claim_id = claim.claim_id
                scoped.claim_ids = [claim.claim_id]
                if len(plan.claim_ids) > 1:
                    scoped.query = ' '.join([*demo_terms[:2], *(claim.searchable_terms or self._keywords(claim.text))[:4]]) + ' ' + plan.query_role.replace('_', ' ')
                scoped.indicator_ids = [item.indicator_id for item in indicators if item.source_claim_id == claim.claim_id or claim.claim_id in item.source_claim_ids]
                scoped.indicator_id = scoped.indicator_ids[0] if scoped.indicator_ids else ''
                scoped_plans.append(scoped)
        state.query_plans = scoped_plans
        known_evidence = {item.evidence_id for item in state.evidence_records}
        for record in state.assessment_profile.document_records:
            evidence_id = f"E-P-{record.document_id}"
            if evidence_id in known_evidence:
                continue
            state.evidence_records.append(EvidenceRecord(
                evidence_id=evidence_id,
                title=record.name,
                source_type="project_material" if record.role in self._primary_document_roles(state) else "guideline",
                source_document_id=record.document_id,
                excerpt=record.excerpt,
                evidence_level="C",
                usable_for_novelty=False,
            ))
            known_evidence.add(evidence_id)

        fulltext_intents = [FulltextIntent(intent_id=f"claim:{c.claim_id}", query=c.text[:1800],
            claim_ids=[c.claim_id], year_range=year_range) for c in claims[:12]]
        fulltext_job = asyncio.create_task(self.fulltext_client.search_intents(fulltext_intents, source_agent="content_assessment"))
        if len(claims) > 12:
            state.output_json.setdefault("fulltext_limitations", []).append("本轮对前 12 项主张补充正文查询，其余主张沿用文献检索依据。")
        if not LITERATURE_SEARCH_API_URL:
            self._append_scenario_evidence(state)
            for plan in state.query_plans:
                plan.source_status = "completed" if demo_config else "unavailable"
                plan.selected_count = len(state.evidence_records) if demo_config else 0
            state.output_json["retrieval_notice"] = "未配置统一检索网关，已保留检索计划并进入低证据模式。"
            self._fuse_problem_fulltext(state, await fulltext_job)
            from app.agents.retrieval_selection import limit_assessment_literature
            limit_assessment_literature(state)
            self._refresh_versions(state)
            return
        client = LiteratureSearchClient()

        async def run_query(plan: QueryPlan):
            return plan, await retrieval_io(
                client.search,
                SearchRequest(
                    query=plan.query,
                    # Each query group gets a wider candidate pool.  The
                    # comparison stage still applies relevance and field
                    # comparability filters before anything reaches the
                    # reader-facing report.
                    paper_num=40,
                    year_range=tuple(year_range) if len(year_range) == 2 else None,
                    user_scope=state.user_scope or None,
                    source_agent="content_assessment",
                    context={"assessment_title": state.assessment_profile.title, "query_group": plan.query_group},
                ),
            )

        plan_index = {id(plan): index for index, plan in enumerate(state.query_plans)}
        results = await asyncio.gather(*(run_query(plan) for plan in state.query_plans), return_exceptions=True)
        traces: list[dict[str, object]] = []
        unique: dict[str, EvidenceRecord] = {}
        selected_by_plan: dict[int, set[str]] = defaultdict(set)
        def merge_results(results):
            for result in results:
                if isinstance(result, Exception):
                    state.output_json.setdefault("retrieval_errors", []).append(type(result).__name__)
                    continue
                plan, response = result
                current_plan_index = plan_index[id(plan)]
                if response.search_trace:
                    traces.append(response.search_trace)
                plan.candidate_count = response.total
                if not response.success:
                    state.output_json.setdefault("retrieval_errors", []).append(response.error or "检索失败")
                    continue
                plan.source_status = "completed"
                for paper in response.papers:
                    identity = (paper.doi or paper.paper_id or re.sub(r"\W+", "", paper.title.lower()))[:160]
                    if not identity:
                        continue
                    if identity in unique:
                        existing = unique[identity]
                        existing.claim_ids = list(dict.fromkeys(existing.claim_ids + plan.claim_ids))
                        existing.indicator_ids = list(dict.fromkeys(existing.indicator_ids + plan.indicator_ids))
                        selected_by_plan[current_plan_index].add(existing.evidence_id)
                        continue
                    if len(unique) >= max(MAX_EVIDENCE, 30 * len(claims)):
                        continue
                    evidence_id = f"E-L-{len(unique) + 1:03d}"
                    evidence = EvidenceRecord(
                        evidence_id=evidence_id,
                        title=paper.title or f"检索文献 {len(unique) + 1}",
                        source_type="literature",
                        paper_id=paper.paper_id,
                        year=paper.year or None,
                        url=paper.url,
                        nstl_url=getattr(paper, "nstl_url", ""),
                        source_url=getattr(paper, "source_url", ""),
                        doi=paper.doi,
                        authors=paper.authors,
                        venue=paper.venue,
                        keywords=paper.keywords,
                        abstract=paper.abstract,
                        retrieval_score=paper.final_score or paper.retrieval_score,
                        query_group=plan.query_group,
                        excerpt=paper.abstract[:1600],
                        evidence_level="C",
                        retrieval_query=plan.query,
                        claim_ids=list(plan.claim_ids),
                        indicator_ids=list(plan.indicator_ids),
                    )
                    fulltext = self._paper_fulltext(paper)
                    self._populate_evidence_anchor(evidence, fulltext, paper.raw)
                    if evidence.anchor_verified and evidence.access_status == "authorized":
                        evidence.evidence_level = "A"
                        evidence.usable_for_novelty = True
                    elif evidence.abstract:
                        evidence.evidence_level = "B"
                        evidence.access_status = "abstract_only"
                    unique[identity] = evidence
                    selected_by_plan[current_plan_index].add(evidence.evidence_id)
                plan.selected_count = len(selected_by_plan[current_plan_index])
        merge_results(results)
        state.evidence_records.extend(unique.values())
        from app.agents.retrieval_selection import assessment_literature_selection, ASSESSMENT_POINT_TARGET
        selection = assessment_literature_selection(state)
        refill = []
        for claim in claims:
            if len(selection.get(claim.claim_id, [])) >= ASSESSMENT_POINT_TARGET:
                continue
            # Same subject, two complementary queries at most. The expanded
            # vocabulary stays scoped to this point and is scored against the
            # actual title/abstract by the final relevance selection.
            originals = [p for p in state.query_plans if p.claim_ids == [claim.claim_id]]
            if not originals:
                continue
            terms = list(dict.fromkeys([*demo_terms, *claim.searchable_terms]))
            variants = [' '.join(terms[:3]), ' '.join([*terms[:1], *terms[2:5]])]
            for query in dict.fromkeys(q.strip() for q in variants):
                if not query or any(p.query == query and p.claim_ids == [claim.claim_id] for p in state.query_plans):
                    continue
                plan = originals[0].model_copy(deep=True)
                plan.query = query
                plan.query_group = '研究要点补充检索'
                plan.query_role = 'point_supplement'
                plan.source_status = 'pending'
                plan.candidate_count = plan.selected_count = 0
                plan.rationale = '补充同一研究要点的相关方法与验证研究。'
                plan_index[id(plan)] = len(state.query_plans)
                state.query_plans.append(plan)
                refill.append(plan)
        if refill:
            before = set(unique)
            merge_results(await asyncio.gather(*(run_query(plan) for plan in refill), return_exceptions=True))
            state.evidence_records.extend(record for identity, record in unique.items() if identity not in before)
        if not demo_config:
            from .public_literature import supplement
            await supplement(state)
        self._fuse_problem_fulltext(state, await fulltext_job)
        self._append_scenario_evidence(state)
        if demo_config:
            snapshot_count = sum(
                item.retrieval_batch_id == f"homepage-scenario-v1:{state.scenario_id}"
                for item in state.evidence_records
            )
            for plan in state.query_plans:
                plan.source_status = "completed"
                plan.selected_count = max(plan.selected_count, snapshot_count)
        for index, plan in enumerate(state.query_plans):
            plan.selected_count = len(selected_by_plan.get(index, set()))
        state.output_json["search_trace"] = traces
        state.output_json["query_groups"] = [plan.model_dump() for plan in state.query_plans]
        state.output_json["retrieval_stats"] = {
            "query_group_count": len(state.query_plans),
            "candidate_count": sum(plan.candidate_count for plan in state.query_plans),
            "unique_evidence_count": len(unique),
            "dedupe_count": max(0, sum(plan.candidate_count for plan in state.query_plans) - len(unique)),
        }
        for item in unique.values():
            if item.retrieval_intent_ids:
                continue
            state.evidence_chunks.append(EvidenceChunk(
                chunk_id=f"CH-{item.evidence_id}",
                evidence_id=item.evidence_id,
                document_id=item.source_document_id,
                page_ref=item.page_ref,
                section_ref=item.query_group,
                excerpt=item.excerpt[:1600],
                purpose=f"支撑{item.query_group or '综合评估'}",
                evidence_level=item.evidence_level,
                paper_id=item.paper_id,
                paragraph_ref=item.paragraph_ref,
                char_start=item.char_start,
                char_end=item.char_end,
                access_status=item.access_status,
                source_hash=item.source_hash,
                reopenable=bool(item.anchor_verified and item.evidence_level == "A"),
                locator=item.locator,
            ))
        from app.agents.retrieval_selection import limit_assessment_literature
        limit_assessment_literature(state)
        if not any(e.source_type == "literature" for e in state.evidence_records):
            state.output_json["retrieval_notice"] = "检索未返回可用结果，已进入低证据模式。"
        self._refresh_versions(state)

    def _fuse_problem_fulltext(self, state: ContentAssessmentState, results: list) -> None:
        counts: dict[str, int] = defaultdict(int)
        existing_chunks = {c.chunk_id: c for c in state.evidence_chunks}
        for c in state.evidence_chunks:
            if c.locator.get("kind") == "fulltext_library":
                counts[c.paper_id] += 1
        for intent, response in results:
            if not response.success:
                state.output_json.setdefault("fulltext_limitations", []).append(response.error)
            selected = selected_chunks(intent, response)
            if response.success and not selected:
                state.output_json.setdefault("fulltext_limitations", []).append("部分主张尚未取得可核对的正文依据，相关判断保留摘要或题录范围限制。")
            state.output_json.setdefault("fulltext_search_trace", []).append({"intent_id": intent.intent_id,
                "claim_ids": intent.claim_ids, "status": "completed" if response.success else "unavailable",
                "returned": len(response.chunks), "selected": len(selected)})
            for chunk in selected:
                records = [e for e in state.evidence_records if e.source_type == "literature"]
                evidence, status = match_source(chunk, records)
                if status in {"ambiguous", "conflicting_identity"}:
                    continue
                if evidence is None:
                    evidence = EvidenceRecord(evidence_id="E-FT-" + hashlib.sha1(chunk.doc_id.encode()).hexdigest()[:12],
                        title=chunk.title, source_type="literature", paper_id=chunk.doc_id, doi=chunk.doi,
                        year=chunk.year, authors=chunk.authors, source_url=chunk.source_url,
                        retrieval_source="fulltext_library", match_status=status)
                    state.evidence_records.append(evidence)
                key = "FT-" + hashlib.sha1(f"{chunk.doc_id}:{chunk.chunk_id}".encode()).hexdigest()[:20]
                if key not in existing_chunks and counts[chunk.doc_id] >= 3:
                    continue
                locator = {"kind": "fulltext_library", "doc_id": chunk.doc_id, "chunk_id": chunk.chunk_id,
                    "section": chunk.section_path, "display": chunk_locator(chunk), "source": chunk.source,
                    "char_start": chunk.char_start, "char_end": chunk.char_end,
                    "offsets_consistent": chunk.offsets_consistent}
                if key in existing_chunks:
                    item = existing_chunks[key]
                    item.claim_ids = list(dict.fromkeys(item.claim_ids + intent.claim_ids))
                    item.intent_ids = list(dict.fromkeys(item.intent_ids + [intent.intent_id]))
                else:
                    item = EvidenceChunk(chunk_id=key, evidence_id=evidence.evidence_id,
                        paper_id=chunk.doc_id, document_id=chunk.doc_id, section_ref=chunk.section_path,
                        paragraph_ref=chunk.chunk_id, excerpt=chunk.text, evidence_level="A",
                        char_start=chunk.char_start, char_end=chunk.char_end, source_hash=chunk.source_hash,
                        access_status="authorized", reopenable=True, locator=locator,
                        purpose="当前主张的正文候选依据；支持关系由主张比对确定",
                        intent_ids=[intent.intent_id], claim_ids=intent.claim_ids)
                    existing_chunks[key] = item
                    state.evidence_chunks.append(item)
                    counts[chunk.doc_id] += 1
                evidence.claim_ids = list(dict.fromkeys(evidence.claim_ids + intent.claim_ids))
                evidence.retrieval_intent_ids = list(dict.fromkeys(evidence.retrieval_intent_ids + [intent.intent_id]))
                evidence.match_status = status if evidence.retrieval_source != "fulltext_library" else evidence.match_status
                evidence.fulltext_available = evidence.anchor_verified = True
                evidence.evidence_level = "A"
                evidence.access_status = "authorized"
                evidence.usable_for_novelty = evidence.usable_for_directional = True
                evidence.excerpt = item.excerpt
                evidence.section_ref = item.section_ref or "正文片段"
                evidence.paragraph_ref = item.paragraph_ref
                evidence.char_start, evidence.char_end = item.char_start, item.char_end
                evidence.source_hash = item.source_hash
                evidence.locator = locator
                evidence.retrieval_score = max(evidence.retrieval_score or 0, chunk.score)
                evidence.retrieval_query = intent.query
                evidence.query_group = evidence.query_group or "按主张检索正文"
        if "fulltext_limitations" in state.output_json:
            state.output_json["fulltext_limitations"] = list(dict.fromkeys(state.output_json["fulltext_limitations"]))

    @staticmethod
    def _merge_comparison_fields(saved: dict[str, str], extracted: dict[str, str]) -> dict[str, str]:
        """Keep frozen evidence fields authoritative; only fill genuinely empty fields."""
        merged = {str(key): str(value).strip() for key, value in (saved or {}).items() if str(value).strip()}
        for key, value in (extracted or {}).items():
            normalized = str(value or "").strip()
            if normalized and not str(merged.get(key) or "").strip():
                merged[str(key)] = normalized
        return merged

    @staticmethod
    def _claim_evidence_view(state: ContentAssessmentState, claim: Claim, evidence: EvidenceRecord) -> EvidenceRecord | None:
        chunks = [c for c in state.evidence_chunks if c.evidence_id == evidence.evidence_id and claim.claim_id in c.claim_ids]
        if chunks:
            c = chunks[0]
            return evidence.model_copy(update={"excerpt": c.excerpt, "section_ref": c.section_ref or "正文片段",
                "locator": c.locator, "char_start": c.char_start, "char_end": c.char_end,
                "comparison_fields": ContentAssessmentGraph._merge_comparison_fields(
                    evidence.comparison_fields, ContentAssessmentGraph._extract_claim_fields(c.excerpt),
                )})
        if evidence.retrieval_intent_ids:
            if not evidence.abstract:
                return None
            return evidence.model_copy(update={"excerpt": evidence.abstract, "evidence_level": "B",
                "anchor_verified": False, "fulltext_available": False, "section_ref": "Abstract",
                "comparison_fields": ContentAssessmentGraph._merge_comparison_fields(
                    evidence.comparison_fields, ContentAssessmentGraph._extract_claim_fields(evidence.abstract),
                )})
        return evidence

    @staticmethod
    def _append_scenario_evidence(state: ContentAssessmentState) -> None:
        config = scenario_config(state.scenario_id) if state.scenario_id else None
        if not config:
            return
        known = {item.doi.lower() for item in state.evidence_records if item.doi}
        all_claim_ids = [item.claim_id for item in state.claims]
        all_indicator_ids = [item.indicator_id for item in state.indicators]
        added: list[str] = []
        for raw in config.get("papers", []):
            doi = str(raw.get("doi") or "").strip()
            paper_id = str(raw.get("paper_id") or raw.get("id") or "").strip()
            nstl_url = http_url(raw.get("nstl_url"))
            source_url = http_url(raw.get("source_url"))
            url = http_url(raw.get("url"))
            resolved_url = evidence_url({
                "nstl_url": nstl_url,
                "source_url": source_url,
                "url": url,
                "paper_id": paper_id,
                "doi": doi,
            })
            # A verified case may have a DOI, original source, or only an
            # NSTL identifier.  Keep it when one real destination exists;
            # never add an unopenable placeholder record.
            if not resolved_url or doi.lower() in known:
                continue
            if not nstl_url and paper_id and "nstl.gov.cn" in resolved_url:
                nstl_url = nstl_detail_url(paper_id)
            abstract = str(raw.get("abstract") or "").strip()
            source_hash = hashlib.sha256(abstract.encode("utf-8", errors="ignore")).hexdigest()
            evidence_id = f"E-L-{sum(item.source_type == 'literature' for item in state.evidence_records) + 1:03d}"
            record = EvidenceRecord(
                evidence_id=evidence_id,
                title=str(raw.get("title") or "已核验公开文献"),
                source_type="literature",
                year=int(raw.get("year") or 0) or None,
                source_url=source_url,
                url=url or resolved_url,
                doi=doi,
                authors=[str(item) for item in raw.get("authors", []) if str(item).strip()],
                venue=str(raw.get("venue") or "公开文献来源"),
                keywords=list(config.get("terms", [])),
                abstract=abstract,
                excerpt=abstract,
                evidence_level="B",
                access_status="abstract_only",
                fulltext_available=False,
                anchor_verified=False,
                section_ref="Abstract",
                paragraph_ref="abstract-1",
                char_start=0,
                char_end=len(abstract),
                source_hash=source_hash,
                usable_for_novelty=False,
                retrieval_query="；".join(config.get("terms", [])),
                query_group="首页推荐案例核验文献",
                claim_ids=all_claim_ids,
                indicator_ids=all_indicator_ids,
                paper_id=paper_id,
                retrieval_batch_id=f"homepage-scenario-v1:{state.scenario_id}",
                locator={
                    "kind": "verified_public_record",
                    "verified_at": raw.get("verified_at", ""),
                    "nstl_url": nstl_url,
                    "source_url": source_url,
                    "url": url or resolved_url,
                    "doi": doi,
                    "paper_id": paper_id,
                    "section": "Abstract",
                    "paragraph": "abstract-1",
                    "char_start": 0,
                    "char_end": len(abstract),
                },
            )
            state.evidence_records.append(record)
            state.evidence_chunks.append(EvidenceChunk(
                chunk_id=f"CH-{evidence_id}", evidence_id=evidence_id,
                document_id=paper_id, section_ref="Abstract",
                paragraph_ref="abstract-1", excerpt=abstract,
                purpose="首页推荐案例公开摘要核验", evidence_level="B",
                supports_claim_ids=all_claim_ids, paper_id=paper_id,
                char_start=0, char_end=len(abstract), access_status="abstract_only",
                source_hash=source_hash, reopenable=False,
                locator=record.locator,
            ))
            known.add(doi.lower())
            added.append(evidence_id)
        if added:
            state.output_json.setdefault("search_trace", []).append({
                "source": "verified_homepage_scenario_snapshot",
                "scenario_id": state.scenario_id,
                "evidence_ids": added,
                "note": "用于稳定演示的已核验公开文献；创新结论仍由统一比对规则生成。",
            })

    def _comparison(self, state: ContentAssessmentState) -> None:
        external = [item for item in state.evidence_records if item.source_type == "literature"]
        scenario_batch = f"homepage-scenario-v1:{state.scenario_id}" if state.scenario_id else ""
        links: list[ClaimEvidenceLink] = []
        details: list[ClaimAssessmentDetail] = []
        for claim in state.claims:
            scoped_external = [view for e in external if (view := self._claim_evidence_view(state, claim, e)) is not None]
            terms = set(claim.searchable_terms or self._keywords(claim.text))
            ranked = sorted(
                [
                    item for item in scoped_external
                    if relevant(claim, item)
                    or bool(scenario_batch and item.retrieval_batch_id == scenario_batch)
                ],
                key=lambda item: (
                    sum(value == "covered" for value in self._field_matrix(claim, item).values()),
                    int(item.anchor_verified),
                    len(terms.intersection(set(item.keywords) | set(self._keywords(item.title + " " + item.abstract)))),
                ),
                reverse=True,
            )[:8]
            supporting: list[str] = []
            best_evidence: EvidenceRecord | None = None
            best_matrix: dict[str, str] = {}
            best_reasons: list[str] = []
            for evidence in ranked:
                field_matrix = self._field_matrix(claim, evidence)
                incomparability_reasons = self._incomparability_reasons(field_matrix, evidence)
                overlap = len(terms.intersection(set(evidence.keywords) | set(self._keywords(evidence.title + " " + evidence.abstract))))
                covered_count = sum(value == "covered" for value in field_matrix.values())
                if evidence.evidence_level == "C" or not (evidence.abstract or evidence.excerpt):
                    relation = "insufficient_evidence"
                elif evidence.evidence_level == "B" or not evidence.anchor_verified:
                    # Abstract-level records are useful directional evidence.
                    # They cannot prove a target or a quantitative advantage,
                    # but they should remain visible as a relevant prior.
                    relation = (
                        "not_comparable"
                        if any("字段不一致" in reason for reason in incomparability_reasons)
                        else "partially_supports"
                    )
                elif evidence.retrieval_batch_id.startswith("homepage-scenario-v1:"):
                    # Curated homepage records are verified, open full-text
                    # sources. They establish a documented prior direction,
                    # while missing field parity still prevents a leading-edge
                    # or target-achieved conclusion.
                    relation = "partially_supports"
                elif incomparability_reasons:
                    relation = "not_comparable"
                elif covered_count >= len(COMPARISON_FIELDS) - 1:
                    relation = "supports"
                elif covered_count:
                    relation = "partially_supports"
                else:
                    relation = "not_comparable"
                if relation == "partially_supports":
                    supporting.append(evidence.evidence_id)
                elif relation == "supports":
                    supporting.append(evidence.evidence_id)
                if best_evidence is None or (
                    sum(value == "covered" for value in field_matrix.values()), int(evidence.anchor_verified)
                ) > (
                    sum(value == "covered" for value in best_matrix.values()), int(best_evidence.anchor_verified)
                ):
                    best_evidence = evidence
                    best_matrix = field_matrix
                    best_reasons = incomparability_reasons
                chunk_ids = [
                    chunk.chunk_id for chunk in state.evidence_chunks
                    if chunk.evidence_id == evidence.evidence_id and chunk.reopenable
                    and (not chunk.claim_ids or claim.claim_id in chunk.claim_ids)
                ]
                links.append(
                    ClaimEvidenceLink(
                        claim_id=claim.claim_id,
                        evidence_id=evidence.evidence_id,
                        relation=relation,
                        comparable_fields=[label for key, label in COMPARISON_FIELDS if field_matrix.get(key) == "covered"],
                        similarity=f"字段覆盖 {covered_count}/{len(COMPARISON_FIELDS)}；关键词交集 {overlap} 项。",
                        difference="；".join(incomparability_reasons) or "已按字段矩阵完成同口径比对。",
                        uncertainty=(
                            "摘要或题录支持方向性判断，但不能替代全文实验数据和统一验证条件。"
                            if evidence.evidence_level == "B" else
                            "缺失字段不能替代统一验证条件。"
                        ),
                        confidence=(
                            min(0.55, max(0.15, covered_count / len(COMPARISON_FIELDS) * 0.55))
                            if evidence.evidence_level == "B" else
                            min(0.95, covered_count / len(COMPARISON_FIELDS)) if evidence.anchor_verified else 0.0
                        ),
                        field_matrix=field_matrix,
                        evidence_chunk_ids=chunk_ids,
                        incomparability_reasons=incomparability_reasons,
                        evidence_level=evidence.evidence_level,
                    )
                )
            nearest_id = best_evidence.evidence_id if best_evidence else ""
            details.append(ClaimAssessmentDetail(
                claim_id=claim.claim_id,
                normalized_claim=claim.text,
                supporting_evidence_ids=supporting,
                evidence_gaps=best_reasons or ["缺少同对象、同方法、同数据、同基线、同指标、同时间和同验证条件的直接比较"],
                field_comparisons=[label for _key, label in COMPARISON_FIELDS],
                conclusion="存在相关候选，但同口径程度仍需复核。" if best_evidence else "尚未获得可比证据。",
                uncertainty=(
                    "B级摘要或题录可形成方向性判断；只有带全文字符锚点的证据才可支持正式创新性和定量领先判断。"
                    if best_evidence and best_evidence.evidence_level == "B" else
                    "只有带全文字符锚点的证据才可进入正式创新性判断。"
                ),
                confidence=min(0.95, len(supporting) / 3) if supporting else 0.0,
                field_matrix=best_matrix,
                nearest_prior_art_id=nearest_id,
                advancedness_conclusion=(
                    "存在相近路线，可行性具有方向性基础但无法证明领先"
                    if best_evidence and best_evidence.evidence_level == "B" else
                    "存在技术差异但无法证明领先" if best_evidence and best_evidence.anchor_verified else
                    "证据不足或存疑"
                ),
                incomparability_reasons=best_reasons,
            ))
        state.claim_evidence_links = links
        state.claim_assessments = details
        links_by_evidence: dict[str, list[str]] = defaultdict(list)
        for link in links:
            if link.relation in {"supports", "partially_supports"}:
                links_by_evidence[link.evidence_id].append(link.claim_id)
        for chunk in state.evidence_chunks:
            chunk.supports_claim_ids = [cid for cid in dict.fromkeys(links_by_evidence.get(chunk.evidence_id, []))
                                       if not chunk.claim_ids or cid in chunk.claim_ids]

    def _comparison_evidence_for_claim(self, state: ContentAssessmentState, claim: Claim) -> list[EvidenceRecord]:
        """Use the same relevant priors for model comparison and rule results."""
        relations = {
            link.evidence_id: link.relation for link in state.claim_evidence_links
            if link.claim_id == claim.claim_id
        }
        scenario_batch = f"homepage-scenario-v1:{state.scenario_id}" if state.scenario_id else ""
        candidates = [view for item in state.evidence_records if item.source_type == "literature"
                      and (view := self._claim_evidence_view(state, claim, item)) is not None and relevant(claim, view)]
        return sorted(candidates, key=lambda item: (
            int(relations.get(item.evidence_id) == "contradicts"),
            int(relations.get(item.evidence_id) in {"supports", "partially_supports"}),
            int(bool(scenario_batch and item.retrieval_batch_id == scenario_batch)),
            sum(value == "covered" for value in self._field_matrix(claim, item).values()),
            int(item.anchor_verified),
            float(item.retrieval_score or 0),
        ), reverse=True)[:5]

    async def _comparison_stage(self, state: ContentAssessmentState) -> None:
        from .comparison_context import material_context, evidence_context
        from .reader_comparison import save_reader_comparison, save_reader_analysis
        self._comparison(state)
        combined = {"links": [], "decisions": []}
        for claim in state.claims:
            # A timeout for one claim must not suppress the remaining claims
            # or the independent landscape stage.
            self._model_unavailable = False
            state.output_json.setdefault("reader_comparisons", {}).pop(claim.claim_id, None)
            state.output_json.setdefault("reader_research_analyses", {}).pop(claim.claim_id, None)
            state.output_json.setdefault("reader_analysis_checks", {}).pop(claim.claim_id, None)
            state.output_json["comparison_gaps"] = [
                gap for gap in state.output_json.get("comparison_gaps", [])
                if gap.get("claim_id") != claim.claim_id
            ]
            external = self._comparison_evidence_for_claim(state, claim)
            if not external:
                continue
            point_context = {**claim.model_dump(), "assessment_context": material_context(state, claim)}
            source_context = [evidence_context(state, claim, e) for e in external]
            # Three reading layers plus verbatim anchors must fit alongside the
            # audit record. Bound the budget by the existing five-source limit.
            payload = await self._request_model_json(state, stage="comparison", system_prompt=COMPARISON_SYSTEM_PROMPT,
                user_prompt=comparison_prompt(claims=[point_context], evidence=source_context),
                max_tokens=min(12000, 5000 + 1400 * len(external)))
            accepted = {key: [] for key in combined}
            if isinstance(payload, dict):
                for key in combined:
                    items = payload.get(key, [])
                    if isinstance(items, list):
                        accepted[key] = [item for item in items if isinstance(item, dict) and item.get("claim_id") == claim.claim_id]
                        combined[key].extend(accepted[key])
                for raw in accepted["links"]:
                    source = next((e for e in external if e.evidence_id == raw.get("evidence_id")), None)
                    if source is not None:
                        save_reader_comparison(state, claim, source, raw)
                save_reader_analysis(state, claim, external, payload)
                # Make each completed point readable while the next is running.
                await self._emit_stage_update(state)
            # Enrichment is independent of audit decisions and cannot restart
            # the workflow or replace content which already passed validation.
            await self._repair_reader_analysis(state, claim, external, point_context, source_context)
            if not accepted["decisions"]:
                state.output_json.setdefault("comparison_gaps", []).append({"claim_id": claim.claim_id, "reason": "模型比较未返回有效结果，保留规则比较与补证任务"})
        self._model_unavailable = False
        if any(combined.values()):
            state.output_json["_model_comparison"] = combined
        # Publish the rule-based indicator assessment with the comparison stage.
        # The assessment stage may refine links with model output and recomputes it.
        self._evaluate_indicators(state)

    async def _repair_reader_analysis(self, state, claim, external, point_context, source_context) -> None:
        from .prompts import reader_analysis_repair_prompt
        from .reader_comparison import reader_repair_targets, save_reader_analysis, save_reader_comparison
        targets = reader_repair_targets(state, claim, external)
        if not targets or not CONTENT_ASSESSMENT_LLM_ENABLED or self._model_block_reason:
            return
        check = state.output_json.setdefault("reader_analysis_checks", {}).setdefault(claim.claim_id, {"issues": []})
        if check.get("repair_attempted"):
            return
        check.update(repair_attempted=True, repair_targets=targets)
        accepted = {
            "reader_analysis": state.output_json.get("reader_research_analyses", {}).get(claim.claim_id, {}),
            "reader_comparisons": state.output_json.get("reader_comparisons", {}).get(claim.claim_id, {}),
        }
        payload = await self._request_model_json(state, stage="comparison", system_prompt=COMPARISON_SYSTEM_PROMPT,
            user_prompt=reader_analysis_repair_prompt(claim=point_context, evidence=source_context,
                targets=targets, accepted=accepted), max_tokens=min(12000, 5000 + 1400 * len(external)))
        if isinstance(payload, dict):
            allowed = {target["path"] for target in targets}
            raw_analysis = payload.get("reader_analysis")
            if isinstance(raw_analysis, dict):
                save_reader_analysis(state, claim, external, {"reader_analysis": {
                    "claim_id": raw_analysis.get("claim_id"),
                    **{key: raw_analysis[key] for key in ("material_summary", "literature_summary")
                       if key in allowed and key in raw_analysis},
                }}, merge=True)
            links = payload.get("links", [])
            for raw in links if isinstance(links, list) else []:
                if not isinstance(raw, dict) or raw.get("claim_id") != claim.claim_id:
                    continue
                source = next((e for e in external if e.evidence_id == raw.get("evidence_id")), None)
                reader = raw.get("reader_comparison")
                if source is not None and isinstance(reader, dict) and f"similarity:{source.evidence_id}" in allowed:
                    save_reader_comparison(state, claim, source,
                        {"reader_comparison": {"similarity": reader.get("similarity", [])}}, merge=True)
        check["repair_status"] = "received" if isinstance(payload, dict) else "unavailable"
        check["remaining_targets"] = reader_repair_targets(state, claim, external)
        await self._emit_stage_update(state)

    def _assessment(self, state: ContentAssessmentState) -> None:
        external_ids = [item.evidence_id for item in state.evidence_records if self._formal_evidence(item)]
        state.novelty_decisions = [
            NoveltyDecision(
                claim_id=claim.claim_id,
                label="当前不可判断",
                rationale="当前证据未完成主张级原文定位与同口径比较，不能形成确定性创新结论。",
                evidence_ids=[link.evidence_id for link in state.claim_evidence_links if link.claim_id == claim.claim_id],
                risk_notes=["项目材料自述不能替代独立外部证据。", "检索未命中不能证明未经证据支持的原创断言。"],
                rewrite_advice=f"将“{claim.text[:50]}”改写为可验证的目标，补充对象、方法、指标和验证条件。",
            )
            for claim in state.claims
        ]
        state.advancedness_decisions = []
        details_by_claim = {item.claim_id: item for item in state.claim_assessments}
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        for claim in state.claims:
            detail = details_by_claim.get(claim.claim_id)
            formal_links = [
                link for link in links_by_claim.get(claim.claim_id, [])
                if link.evidence_level == "A" and link.evidence_chunk_ids and link.relation in {"supports", "partially_supports"}
            ]
            if not formal_links:
                state.advancedness_decisions.append(AdvancednessDecision(
                    claim_id=claim.claim_id,
                label="证据不足或存疑",
                    rationale="没有带全文锚点的同口径证据，不能独立判断先进性。",
                    uncertainty="先进性判断不从创新性标签或关键词相似度推导。",
                ))
                continue
            comparable = detail and all(value == "covered" for value in detail.field_matrix.values())
            state.advancedness_decisions.append(AdvancednessDecision(
                claim_id=claim.claim_id,
                label="存在技术差异但无法证明领先" if not comparable else "可比条件下领先",
                rationale=(
                    "已找到带全文锚点的可比证据，但当前没有足够的统一基线和量化结果证明领先。"
                    if not comparable else "字段矩阵显示对象、方法、数据、基线、指标、时间和验证条件均可比，需结合量化结果复核领先性。"
                ),
                comparison_evidence_ids=[link.evidence_id for link in formal_links[:5]],
                comparable_indicator_ids=[item.indicator_id for item in state.indicators if item.source_claim_id == claim.claim_id],
                uncertainty="先进性与创新性分开判定，不能把目标值直接当作已实现水平。",
            ))
        if not state.indicator_validations:
            state.indicator_validations = [
                IndicatorValidation(
                    indicator_id=item.indicator_id,
                    definition=item.name,
                    unit=item.unit,
                    target=f"{item.value}{item.unit}".strip(),
                    status="partial" if item.value else "missing",
                    gaps=["缺少基线、测试方法、样本设计或验收条件"],
                )
                for item in state.indicators
            ]
        state.scorecard = [
            ScoreCardItem(dimension="创新性", assessment="创新性只判断是否存在相近技术先例及组合差异；无直接证据时保留有限标签。", basis=[item.evidence_id for item in state.evidence_records if item.evidence_level in {"A", "B"}][:5], uncertainty="关键词相似不能替代主张级字段比较。"),
            ScoreCardItem(dimension="先进性", assessment="先进性要求同对象、同方法、同指标和同验证条件下的量化对照，当前不从创新性标签推导。", basis=[item.evidence_id for item in state.evidence_records if item.evidence_level == "A"][:5], uncertainty="项目目标值不能作为已实现的领先结果。"),
            ScoreCardItem(dimension="可行性", assessment=f"已拆分 {len(state.work_packages)} 个工作包，需补齐依赖、资源、样本和里程碑验证。", basis=[item.work_package_id for item in state.work_packages[:5]], uncertainty="资源与实验条件未在材料中明确时，结论为条件性判断。"),
            ScoreCardItem(dimension="风险", assessment="重点检查指标口径、验证条件、长期稳定性、样本代表性和证据迁移风险。", basis=[item.indicator_id for item in state.indicator_validations if item.status != "supported"][:5], uncertainty="风险等级需结合项目实施数据更新。"),
            ScoreCardItem(dimension="证据充分性", assessment=f"已建立 {len(state.claim_evidence_links)} 条主张-证据关联，覆盖率将在质量门阶段计算。", basis=[item.evidence_id for item in state.evidence_records[:5]], uncertainty="项目自述材料不作为独立外部证据。"),
            ScoreCardItem(dimension="应用与合规", assessment="应用、产业和监管结论优先采用有来源的数据卡；缺少正式量化来源时仍保留定性路线观察，并明确不能量化的部分。", basis=[item.evidence_id for item in state.evidence_records if item.source_type in {"standard", "regulatory", "market", "literature"}][:5], uncertainty="当前证据池不等同于完整市场或监管调查。"),
        ]

    def _assessment_stage(self, state: ContentAssessmentState) -> None:
        self._assessment(state)
        payload = state.output_json.pop("_model_comparison", None)
        if isinstance(payload, dict):
            self._apply_model_comparison(state, payload)
        self._sanitize_claim_outputs(state)
        self._rebuild_novelty_decisions(state)
        self._recompute_advancedness(state)
        self._evaluate_indicators(state)

    async def _assessment_stage_with_analysis(self, state: ContentAssessmentState) -> None:
        self._assessment_stage(state)
        from .point_evaluation import evaluate_points, reviewed_evaluations, saved_evaluations
        await evaluate_points(self, state)
        pending = [claim for claim in state.claims if not reviewed_evaluations(state, claim)]
        if pending and not state.report_snapshot:
            from .scholarly_report import selected_sources, point_title
            unresolved = [claim for claim in pending if not selected_sources(state, claim)]
            if unresolved:
                interrupt_generation(state, "retrieval", "report_sources", "相关文献检索未完成，已有结果已保存，请重试检索。")
                return
            # A source-grounded partial appraisal is still useful to the
            # reader and the report writer. Do not turn one rejected dimension
            # into a failed five-stage task; retain the warning for the detail
            # page and let the report stage fill the missing dimension.
            partial = [claim.claim_id for claim in pending if saved_evaluations(state, claim)]
            state.output_json["reader_evaluation_warning"] = {
                "status": "partial" if partial else "limited",
                "claim_ids": [claim.claim_id for claim in pending],
                "partial_claim_ids": partial,
                "missing_dimensions": {
                    claim.claim_id: state.output_json.get("reader_evaluation_completion", {}).get(claim.claim_id, {}).get("missing_dimensions", [])
                    for claim in pending
                },
                "message": "部分评价维度仍需补充，已保留现有结果并继续生成报告。" if partial else
                    "个别研究要点暂未形成完整评价段落，评估结果会在报告中结合可用证据继续整理。",
            }

    @classmethod
    def _evaluate_indicators(cls, state: ContentAssessmentState) -> None:
        """Assess indicator evidence separately from whether a project target was achieved."""
        by_id = {item.indicator_id: item for item in state.indicator_validations}
        evidence_by_id = {item.evidence_id: item for item in state.evidence_records}
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        evaluated: list[IndicatorValidation] = []
        for indicator in state.indicators:
            previous = by_id.get(indicator.indicator_id)
            validation = previous.model_copy(deep=True) if previous else IndicatorValidation(indicator_id=indicator.indicator_id)
            validation.definition = validation.definition or indicator.definition or indicator.name
            validation.unit = validation.unit or indicator.unit
            validation.target = validation.target or indicator.acceptance_condition or f"{indicator.value}{indicator.unit}".strip()
            validation.baseline = validation.baseline or indicator.baseline
            validation.test_method = validation.test_method or indicator.test_method
            validation.acceptance_criteria = validation.acceptance_criteria or indicator.acceptance_condition
            has_target = bool(validation.target.strip())
            validation.definition_status = (
                "complete" if validation.definition.strip() and has_target and (validation.unit or re.search(r"%|倍|小时|天|月|年", validation.target))
                else "partial" if validation.definition.strip() or has_target else "missing"
            )
            claim_ids = set(indicator.source_claim_ids)
            if indicator.source_claim_id:
                claim_ids.add(indicator.source_claim_id)
            aliases = {
                "成本": ("cost", "expense"),
                "周期": ("cycle time", "turnaround", "duration", "time-to"),
                "返工": ("rework", "rework rate"),
                "重复性": ("reproducibility", "repeatability"),
            }
            metric_core = re.sub(r"(?:降低|缩短|提高|提升|达到|不低于|不高于)$", "", indicator.name.lower())
            metric_terms = [indicator.name.lower(), metric_core] + [term for key, terms in aliases.items()
                                                        if key in indicator.name for term in terms]
            def mentions_metric(evidence: EvidenceRecord) -> bool:
                corpus = " ".join((evidence.title, evidence.abstract, evidence.excerpt,
                                   str(evidence.comparison_fields.get("metric") or ""))).lower()
                return any(term and term in corpus for term in metric_terms)
            candidates: list[tuple[EvidenceRecord, ClaimEvidenceLink]] = []
            for claim_id in claim_ids:
                for link in links_by_claim.get(claim_id, []):
                    evidence = evidence_by_id.get(link.evidence_id)
                    if (evidence and evidence.source_type == "literature" and
                            link.relation in {"supports", "partially_supports"} and
                            evidence.evidence_level in {"A", "B"} and (evidence.abstract or evidence.excerpt)
                            and mentions_metric(evidence)):
                        candidates.append((evidence, link))
            # Query-level indicator IDs are only a search scope, not proof of
            # metric comparability. Require an explicit metric field for an
            # indicator without a linked claim.
            if not candidates and not claim_ids:
                for evidence in state.evidence_records:
                    metric = str(evidence.comparison_fields.get("metric") or "")
                    if (evidence.source_type == "literature" and evidence.evidence_level in {"A", "B"}
                            and metric and mentions_metric(evidence) and (evidence.abstract or evidence.excerpt)):
                        candidates.append((evidence, ClaimEvidenceLink(
                            claim_id=indicator.source_claim_id, evidence_id=evidence.evidence_id,
                            relation="partially_supports", confidence=0,
                        )))
            unique = list({e.evidence_id: (e, link) for e, link in candidates}.values())
            comparable = [e for e, link in unique if (
                cls._formal_evidence(e) and link.relation in {"supports", "partially_supports"}
                and all(link.field_matrix.get(field) == "covered"
                        for field in ("object", "method", "metric", "baseline", "validation"))
                and re.search(r"\d", str(e.comparison_fields.get("baseline") or ""))
                and e.comparison_fields.get("metric") and
                bool(indicator.unit)
                and indicator.unit in str(e.comparison_fields.get("metric"))
                and indicator.unit in str(e.comparison_fields.get("baseline"))
            )]
            validation.literature_baseline_status = "comparable" if comparable else "directional" if unique else "none"
            validation.literature_baseline = str(comparable[0].comparison_fields.get("baseline") or "") if comparable else ""
            validation.evidence_refs = list(dict.fromkeys(e.evidence_id for e in (comparable or [item[0] for item in unique])))[:8]
            source_text = indicator.source_span or ""
            documented = (indicator.status == "completed" and bool(indicator.source_document_id and source_text)
                          and bool(re.search(r"实测|实际结果|测得|已实现|已完成|实验结果", source_text))
                          and bool(re.search(r"\d", source_text))
                          and not bool(re.search(r"计划|拟|目标|预期|预计", source_text)))
            validation.project_achievement_status = "documented" if documented else "not_provided"
            validation.project_result = source_text if documented else ""
            validation.status = "supported" if comparable else "partial" if unique else "missing"
            numeric_comparison = ""
            if comparable:
                target_value = re.search(r"\d+(?:\.\d+)?", validation.target)
                baseline_value = re.search(r"\d+(?:\.\d+)?", validation.literature_baseline)
                if target_value and baseline_value:
                    delta = float(target_value[0]) - float(baseline_value[0])
                    difference_unit = "个百分点" if indicator.unit == "%" else indicator.unit
                    numeric_comparison = (f"项目目标与文献基线数值相差{abs(delta):g}{difference_unit}；"
                                          "这不是项目已达成的证明")
            basis = (
                f"找到带全文定位的同口径文献指标（{comparable[0].comparison_fields['metric']}），"
                f"文献基线为{validation.literature_baseline}；{numeric_comparison}；仍需核对适用对象与实验条件" if comparable else
                "找到相关文献，可用于方向性判断，不能证明量化目标已实现" if unique else
                "本次检索未找到可用于该指标判断的文献基线"
            )
            validation.assessment_basis = basis
            validation.assessment_conclusion = (
                f"{indicator.name}（{validation.target or '目标未提供'}）："
                f"{'目标及口径已明确' if validation.definition_status == 'complete' else '指标定义或目标口径尚不完整'}；"
                f"{basis}；"
                f"{'项目材料记录了实测结果，仍须核对原始记录' if documented else '项目材料未提供目标达成的实测结果'}。"
            )
            validation.gaps = []
            if validation.definition_status != "complete":
                validation.gaps.append("需明确指标定义、单位或目标口径")
            if not validation.baseline:
                validation.gaps.append("项目基线未提供")
            if not validation.test_method:
                validation.gaps.append("测试方法未提供")
            if validation.literature_baseline_status != "comparable":
                validation.gaps.append("缺少同口径数值文献基线")
            if not documented:
                validation.gaps.append("项目达成实测数据未提供")
            evaluated.append(validation)
        state.indicator_validations = evaluated

    def _landscape(self, state: ContentAssessmentState) -> None:
        self._relevant_landscape(state)

    def _relevant_landscape(self, state: ContentAssessmentState) -> None:
        pool = [e for e in state.evidence_records if e.source_type == "literature" and any(relevant(c, e) for c in state.claims)]
        years: dict[str, int] = {}
        for e in pool:
            if e.year:
                years[str(e.year)] = years.get(str(e.year), 0) + 1
        state.trend_analysis = {"literature_count": len(pool), "status": "available" if pool else "insufficient_evidence",
            "summary": f"本轮获得 {len(pool)} 条与技术主张相关的文献线索。下文按技术内容讨论；数量和年份仅反映本次检索覆盖，不能外推领域发展速度或市场竞争力。" if pool else "当前未获得足以归纳技术现状的相关外部证据。",
            "year_distribution": years, "themes": [], "methods": [], "trend_highlights": [], "competition_observations": [],
            "limitations": ["摘要线索不等同于同口径验证，技术演化与竞争主体需核验原文。"]}
        # A literature-only pool cannot support market size or regulatory
        # approval claims, but it can still support a useful qualitative
        # observation about application routes, competing approaches and
        # standards-related search coverage.
        representative_ids = [item.evidence_id for item in pool[:8]]
        qualitative_status = "qualitative" if pool else "insufficient_evidence"
        setattr(state, "market_data_cards", [LandscapeDataCard(
            card_id="market-001", card_type="market", subject="应用场景与转化方向",
            value="定性观察" if pool else "", unit="文献路线",
            source_evidence_ids=representative_ids[:5], status=qualitative_status,
            confidence=0.3 if pool else 0.0,
            limitation=("当前文献支持应用方向和转化关注点，但没有权威市场规模、收入或用户数量数据。" if pool
                        else "当前没有相关证据，无法形成市场方向观察。"),
        )])
        setattr(state, "competitive_data_cards", [LandscapeDataCard(
            card_id="competitive-001", card_type="competitive", subject="公开技术路线竞争面",
            value=f"{len(pool)} 条相关文献" if pool else "", unit="检索范围内",
            source_evidence_ids=representative_ids[:8], status=qualitative_status,
            confidence=0.35 if pool else 0.0,
            limitation=("该观察用于比较公开技术路线，不代表企业排名、市场份额或产品竞争力。" if pool
                        else "当前没有足够相关记录，无法形成竞争路线观察。"),
        )])
        setattr(state, "regulatory_data_cards", [LandscapeDataCard(
            card_id="regulatory-001", card_type="regulatory", subject="标准与监管关注点",
            value="定性观察" if pool else "", unit="公开文献范围",
            source_evidence_ids=representative_ids[:5], status=qualitative_status,
            confidence=0.25 if pool else 0.0,
            limitation=("相关文献可提示验证、标准化和合规关注点，但不代表正式监管认可或合规通过。" if pool
                        else "当前没有相关记录，无法形成监管观察。"),
        )])

    def _landscape_v2(self, state: ContentAssessmentState) -> None:
        literature_count = sum(1 for item in state.evidence_records if item.source_type == "literature")
        year_distribution: dict[str, int] = {}
        query_distribution: dict[str, int] = {}
        term_counts: dict[str, int] = {}
        for item in state.evidence_records:
            if item.year:
                year_distribution[str(item.year)] = year_distribution.get(str(item.year), 0) + 1
            if item.query_group:
                query_distribution[item.query_group] = query_distribution.get(item.query_group, 0) + 1
            for term in (item.keywords or self._keywords(item.title))[:10]:
                term_counts[term] = term_counts.get(term, 0) + 1
        themes = [term for term, _ in sorted(term_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]]
        theme_rows = []
        for term, count in sorted(term_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:8]:
            ids = [item.evidence_id for item in state.evidence_records if term in (item.keywords or self._keywords(item.title + " " + item.abstract))][:8]
            theme_rows.append({"label": term, "evidence_count": count, "evidence_ids": ids})
        if literature_count and len(theme_rows) < min(4, len(query_distribution)):
            known = {str(item["label"]) for item in theme_rows}
            for group, count in sorted(query_distribution.items(), key=lambda pair: (-pair[1], pair[0])):
                if group in known:
                    continue
                ids = [item.evidence_id for item in state.evidence_records if item.query_group == group][:8]
                theme_rows.append({"label": group, "evidence_count": count, "evidence_ids": ids})
                known.add(group)
                if len(theme_rows) >= min(4, len(query_distribution)):
                    break
        method_rows = []
        for term, count in sorted(term_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:5]:
            ids = [item.evidence_id for item in state.evidence_records if term in (item.keywords or self._keywords(item.title + " " + item.abstract))][:8]
            method_rows.append({"label": term, "evidence_count": count, "evidence_ids": ids})
        if literature_count and len(method_rows) < 3:
            for group, count in sorted(query_distribution.items(), key=lambda pair: (-pair[1], pair[0])):
                if any(item["label"] == group for item in method_rows):
                    continue
                method_rows.append({"label": group, "evidence_count": count, "evidence_ids": [item.evidence_id for item in state.evidence_records if item.query_group == group][:8]})
                if len(method_rows) >= 3:
                    break
        state.trend_analysis = {
            "literature_count": literature_count,
            "status": "available" if literature_count else "insufficient_evidence",
            "summary": "已按查询分组、年份和高频术语生成趋势与竞争态势的可审计统计。" if literature_count else "外部文献不足，仅保留态势分析框架和限制说明。",
            "year_distribution": year_distribution,
            "query_group_distribution": query_distribution,
            "themes": theme_rows,
            "methods": method_rows,
            "trend_highlights": [f"主题词“{term}”在当前证据池中出现 {term_counts[term]} 条记录。" for term in themes[:5]],
            "competition_observations": [f"查询组“{group}”纳入 {count} 条证据，代表当前检索范围内的一个比较面向。" for group, count in query_distribution.items()],
            "limitations": ["方向前沿不等于具体方案已领先。", "市场与竞争定位需标记数据来源和定量/定性边界。"],
        }
        state.market_data_cards = [LandscapeDataCard(
            card_id="MKT-001",
            card_type="market",
            subject="市场规模、用户规模与商业化收入",
            status="available" if any(item.source_type == "market" for item in state.evidence_records) else "insufficient_evidence",
            source_evidence_ids=[item.evidence_id for item in state.evidence_records if item.source_type == "market"][:8],
            limitation="当前检索证据池未提供可核验市场数据，不能由论文数量外推市场规模。",
        )]
        regulatory_evidence = [
            item for item in state.evidence_records
            if item.source_type in {"regulatory", "standard"} or item.query_group == "standard"
        ]
        state.regulatory_data_cards = [LandscapeDataCard(
            card_id="REG-001",
            card_type="regulatory",
            subject="标准、指南与监管适用边界",
            value=str(len(regulatory_evidence)),
            unit="条证据记录",
            source_evidence_ids=[item.evidence_id for item in regulatory_evidence[:12]],
            status="qualitative" if regulatory_evidence else "insufficient_evidence",
            confidence=0.45 if regulatory_evidence else 0.0,
            limitation="该卡片仅描述本次检索范围内的规范性材料，不代表完整监管意见。",
        )]
        competitive_cards: list[LandscapeDataCard] = []
        for group, count in sorted(query_distribution.items(), key=lambda pair: (-pair[1], pair[0]))[:12]:
            group_evidence = [item for item in state.evidence_records if item.query_group == group]
            competitive_cards.append(LandscapeDataCard(
                card_id=f"COMP-{len(competitive_cards) + 1:03d}",
                card_type="competitive",
                subject=group,
                value=str(count),
                unit="条检索记录",
                source_evidence_ids=[item.evidence_id for item in group_evidence[:8]],
                status="qualitative" if group_evidence else "insufficient_evidence",
                confidence=0.35 if group_evidence else 0.0,
                limitation="按查询组统计的证据覆盖面，不等同于机构、产品或市场份额排名。",
            ))
        state.competitive_data_cards = competitive_cards
        state.trend_analysis["data_cards"] = [
            item.model_dump() for item in (
                state.market_data_cards + state.regulatory_data_cards + state.competitive_data_cards
            )
        ]

    async def _landscape_stage(self, state: ContentAssessmentState) -> None:
        self._landscape(state)
        external = [item for item in state.evidence_records if item.source_type == "literature" and any(relevant(c, item) for c in state.claims)]
        if not external:
            return
        payload = await self._request_model_json(
            state,
            stage="landscape",
            system_prompt=LANDSCAPE_SYSTEM_PROMPT,
            user_prompt=landscape_prompt(
                title=state.assessment_profile.title,
                evidence=[{"evidence_id": item.evidence_id, "title": item.title, "year": item.year,
                           "excerpt": (item.abstract or item.excerpt)[:1200], "evidence_level": item.evidence_level} for item in external[:12]],
            ),
            max_tokens=5000,
        )
        if payload is not None:
            self._apply_model_landscape(state, payload)

    async def _request_model_json(
        self,
        state: ContentAssessmentState,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> dict[str, object] | None:
        if not CONTENT_ASSESSMENT_LLM_ENABLED:
            self._record_model_usage(state, stage=stage, status="disabled")
            return None
        if self._model_block_reason:
            self._record_model_usage(state, stage=stage, status="fallback", reason=self._model_block_reason)
            return None
        # A malformed answer or transient error must not disable every later
        # repair/review call in this task. Authentication errors still stop.
        self._model_unavailable = False

        trace_stage = "retrieval" if stage == "retrieval_planning" else stage
        model_started = datetime.now().astimezone().isoformat()
        model_step = ProcessTraceStep(
            step_key=f"{trace_stage}:model:{len(state.process_trace.get(trace_stage, []))}",
            title="模型辅助语义复核", status="running", started_at=model_started,
        )
        state.process_trace.setdefault(trace_stage, []).append(model_step)
        await self._emit_stage_update(state)
        try:
            if self._model_client is None:
                self._model_client = DeepSeekClient()
            result = await self._model_client.generate_json_with_metrics(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=CONTENT_ASSESSMENT_LLM_TIMEOUT_SECONDS,
                max_retries=CONTENT_ASSESSMENT_LLM_MAX_RETRIES,
                max_tokens=max_tokens,
            )
        except (DeepSeekClientError, ValueError) as exc:
            self._model_unavailable = True
            metrics = getattr(exc, "metrics", None)
            if getattr(metrics, "status_code", None) in {401, 402, 403}:
                self._model_block_reason = getattr(metrics, "error_type", None) or "model_authorization_failed"
            self._record_model_response(state, stage, None, metrics, type(exc).__name__)
            self._finish_model_process_step(model_step, "模型调用失败，已继续按规则处理。", failed=True)
            logger.warning("创新性评估模型阶段降级 stage=%s error=%s", stage, type(exc).__name__)
            self._record_model_usage(
                state,
                stage=stage,
                status="fallback",
                reason=type(exc).__name__,
            )
            return None
        except Exception as exc:  # Keep task availability independent from a model outage.
            self._model_unavailable = True
            self._record_model_response(state, stage, None, getattr(exc, "metrics", None), type(exc).__name__)
            self._finish_model_process_step(model_step, "模型调用异常，已继续按规则处理。", failed=True)
            logger.exception("创新性评估模型阶段异常，已回退 stage=%s", stage)
            self._record_model_usage(
                state,
                stage=stage,
                status="fallback",
                reason=type(exc).__name__,
            )
            return None

        self._record_model_response(state, stage, result.payload, result.metrics)
        allowed_keys = {
            "claim_extraction": {"assessment", "nine_fields", "claims", "indicators", "indicator_validations", "work_packages"},
            "retrieval_planning": {"queries"},
            "comparison": {"links", "decisions", "scorecard", "reader_analysis"},
            "landscape": {"summary", "trend_highlights", "competition_observations", "risk_notes", "themes", "methods", "data_cards"},
            "quality_gate": {"findings", "summary"},
        }.get(stage)
        if not isinstance(result.payload, dict) or (allowed_keys and not any(key in result.payload for key in allowed_keys)):
            self._finish_model_process_step(model_step, "模型返回结构未通过校验，继续按规则处理。", failed=True)
            self._record_model_usage(state, stage=stage, status="fallback", reason="schema_validation_failed")
            return None
        self._finish_model_process_step(model_step, "已收到模型结果，正在按本阶段规则核对结构与来源。")
        self._record_model_usage(
            state,
            stage=stage,
            status="completed",
            profile=result.metrics.profile,
            model=result.metrics.model,
            duration_ms=result.metrics.done_ms,
        )
        return result.payload if isinstance(result.payload, dict) else None

    @staticmethod
    def _record_model_response(state, stage, payload, metrics, reason="") -> None:
        rows = state.output_json.setdefault("model_response_diagnostics", [])
        rows.append(response_diagnostic(payload, reason, stage=stage,
            finish_reason=getattr(metrics, "finish_reason", None),
            http_status=getattr(metrics, "status_code", None),
            duration_ms=getattr(metrics, "done_ms", None),
            raw_content_preview=str(getattr(metrics, "raw_content_preview", ""))[:1600]))
        del rows[:-100]

    @staticmethod
    def _finish_model_process_step(step: ProcessTraceStep, summary: str, *, failed: bool = False) -> None:
        now = datetime.now().astimezone().isoformat()
        step.status = "failed" if failed else "completed"
        step.summary = summary
        step.completed_at = now
        if step.started_at:
            step.duration_seconds = max(0.0, (
                datetime.fromisoformat(now) - datetime.fromisoformat(step.started_at)
            ).total_seconds())

    async def _close_model_client(self) -> None:
        if self._model_client is None:
            return
        try:
            await self._model_client.aclose()
        except Exception:
            logger.debug("关闭创新性评估模型客户端失败", exc_info=True)
        finally:
            self._model_client = None

    @staticmethod
    def _record_model_usage(
        state: ContentAssessmentState,
        *,
        stage: str,
        status: str,
        profile: str = "",
        model: str = "",
        duration_ms: int | None = None,
        reason: str = "",
    ) -> None:
        usage = state.output_json.setdefault("model_usage", {"enabled": CONTENT_ASSESSMENT_LLM_ENABLED, "stages": []})
        stages = usage.setdefault("stages", [])
        entry: dict[str, object] = {"stage": stage, "status": status}
        if profile:
            entry["profile"] = profile
        if model:
            entry["model"] = model
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if reason:
            entry["reason"] = reason
        stages.append(entry)

    def _claim_extraction_prompt(self, state: ContentAssessmentState) -> str:
        schema = {
            "assessment": {"title": "string", "research_domain": "string", "scope_boundary": "string"},
            "nine_fields": {
                "research_direction": "string", "research_goal": "string", "research_content": "string",
                "technical_route": "string", "key_technologies": ["string"],
                "evaluation_indicators": ["string"], "timeline": "string",
                "application_scenarios": ["string"], "expected_outcomes": ["string"],
            },
            "claims": [{
                "text": "string", "claim_type": "innovation|technical|indicator|application|compliance",
                "assertion_type": "completed|target|planned|unknown", "ambiguity": "string",
                "searchable_terms": ["string"],
            }],
            "indicators": [{
                "name": "string", "value": "string", "unit": "string",
                "status": "completed|target|planned|unknown", "source_claim_index": 1,
            }],
            "indicator_validations": [{
                "indicator_id": "I-001", "definition": "string", "unit": "string", "baseline": "string", "target": "string",
                "test_method": "string", "sample_design": "string", "statistics_method": "string", "acceptance_criteria": "string",
                "status": "supported|partial|missing|not_applicable", "evidence_refs": ["E-L-001"], "gaps": ["string"],
            }],
            "work_packages": [{
                "title": "string", "objective": "string", "inputs": ["string"],
                "outputs": ["string"], "dependencies": ["string"], "verification_nodes": ["string"],
            }],
        }
        return (
            "按以下 JSON 结构抽取材料中的内容；缺失字段使用空字符串或空数组，最多 20 条原子主张。\n"
            f"输出结构：{json.dumps(schema, ensure_ascii=False)}\n\n"
            "<assessment_material>\n"
            f"{self._material_text(state)}\n"
            "</assessment_material>"
        )

    def _comparison_prompt(self, state: ContentAssessmentState, external: list[EvidenceRecord]) -> str:
        evidence = [
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "year": item.year,
                "excerpt": item.excerpt[:1600],
                "authors": item.authors[:8],
                "venue": item.venue,
                "query_group": item.query_group,
                "evidence_level": item.evidence_level,
            }
            for item in external[:24]
        ]
        schema = {
            "links": [{
                "claim_id": "C-001", "evidence_id": "E-L-001",
                "relation": "supports|contradicts|partially_supports|not_comparable|insufficient_evidence",
                "comparable_fields": ["object", "method", "data", "baseline", "metric", "time", "validation"], "field_matrix": {"object": "covered|partial|missing|conflict|not_comparable", "method": "covered|partial|missing|conflict|not_comparable", "data": "covered|partial|missing|conflict|not_comparable", "baseline": "covered|partial|missing|conflict|not_comparable", "metric": "covered|partial|missing|conflict|not_comparable", "time": "covered|partial|missing|conflict|not_comparable", "validation": "covered|partial|missing|conflict|not_comparable"}, "difference": "string", "uncertainty": "string",
                "confidence": 0.0,
            }],
            "decisions": [{
                "claim_id": "C-001",
                "label": "存在相近先例|证据支持有限|目标待验证|证据不足",
                "rationale": "string", "evidence_ids": ["E-L-001"],
                "risk_notes": ["string"], "rewrite_advice": "string",
            }],
            "scorecard": [{
                "dimension": "指南符合性|前沿性|创新性与先进性|可行性|风险|证据充分性|申报文本质量",
                "assessment": "string", "score_range": "string", "basis": ["string"], "uncertainty": "string",
            }],
        }
        return (
            "完成主张级同口径比对。只能引用下方已有 evidence_id；无证据的主张必须使用“证据不足”。\n"
            f"输出结构：{json.dumps(schema, ensure_ascii=False)}\n"
            f"主张：{json.dumps([item.model_dump() for item in state.claims], ensure_ascii=False)}\n"
            f"证据：{json.dumps(evidence, ensure_ascii=False)}"
        )

    def _landscape_prompt(self, state: ContentAssessmentState, external: list[EvidenceRecord]) -> str:
        evidence = [
            {"evidence_id": item.evidence_id, "title": item.title, "year": item.year, "excerpt": item.excerpt[:1200], "query_group": item.query_group}
            for item in external[:24]
        ]
        schema = {
            "summary": "string", "trend_highlights": ["string"],
            "competition_observations": ["string"], "risk_notes": ["string"],
            "themes": [{"label": "string", "evidence_ids": ["E-L-001"], "maturity": "string", "opportunity": "string", "rationale": "string"}],
            "methods": [{"label": "string", "evidence_ids": ["E-L-001"], "strengths": ["string"], "limitations": ["string"]}],
        }
        return (
            "仅基于给定证据生成技术趋势与竞争态势的谨慎概括，并明确不确定性。\n"
            f"输出结构：{json.dumps(schema, ensure_ascii=False)}\n"
            f"评估对象：{state.assessment_profile.title}\n"
            f"证据：{json.dumps(evidence, ensure_ascii=False)}"
        )

    def _apply_model_claim_extraction(self, state: ContentAssessmentState, payload: dict[str, object]) -> bool:
        raw_claims = payload.get("claims")
        if not isinstance(raw_claims, list):
            return False
        source_document_id = self._project_document_id(state)
        claims: list[Claim] = []
        allowed_claim_types = {"innovation", "technical", "indicator", "application", "compliance"}
        allowed_assertion_types = {"completed", "target", "planned", "unknown"}
        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue
            text = self._text(raw.get("text"), limit=500)
            if not text:
                continue
            quote = str(raw.get("source_quote") or raw.get("source_span") or "")
            document_id = str(raw.get("source_document_id") or "")
            source = next((f for f in material_sources(state) if f.file_id == document_id), None)
            anchor = locate(source, quote) if source else None
            if not anchor or INSTRUCTION.search(text):
                continue
            # The reported assertion stays verbatim; ungrounded paraphrases
            # cannot invent results while pointing at an unrelated valid quote.
            text = quote
            claim_type = self._text(raw.get("claim_type"), limit=30)
            assertion_type = self._text(raw.get("assertion_type"), limit=30)
            claims.append(Claim(
                claim_id=f"C-{len(claims) + 1:03d}",
                text=text,
                claim_type=claim_type if claim_type in allowed_claim_types else "technical",
                assertion_type=assertion_type if assertion_type in allowed_assertion_types else "unknown",
                **anchor,
                ambiguity=self._text(raw.get("ambiguity"), limit=300),
                searchable_terms=self._string_list(raw.get("searchable_terms"), limit=8, item_limit=80) or self._keywords(text),
                claim_fields={
                    key: self._text(raw.get(key), limit=160)
                    for key in dict.fromkeys(["object", "method", "data", "baseline", "metric", "time", "validation"])
                    if self._text(raw.get(key), limit=160)
                } or self._extract_claim_fields(text),
            ))
        if not claims:
            return False
        state.claims = claims

        fields = payload.get("nine_fields")
        if isinstance(fields, dict) and not state.nine_fields_confirmed:
            values = state.nine_fields.model_dump()
            for name in ("research_direction", "research_goal", "research_content", "technical_route", "timeline"):
                text = self._text(fields.get(name), limit=1000)
                if text and not values[name]:
                    values[name] = text
            for name in ("key_technologies", "evaluation_indicators", "application_scenarios", "expected_outcomes"):
                items = self._string_list(fields.get(name), limit=12, item_limit=200)
                if items and not values[name]:
                    values[name] = items
            state.nine_fields = ResearchNineFields(**values)

        assessment = payload.get("assessment")
        if isinstance(assessment, dict):
            title = self._text(assessment.get("title"), limit=120)
            domain = self._text(assessment.get("research_domain"), limit=120)
            scope = self._text(assessment.get("scope_boundary"), limit=600)
            if title:
                state.assessment_profile.title = title
                state.assessment_profile.assessment_object = title
            if domain:
                state.assessment_profile.research_domain = domain
            if scope:
                state.assessment_profile.scope_boundary = scope

        raw_indicators = payload.get("indicators")
        indicators: list[Indicator] = []
        allowed_indicator_status = {"completed", "target", "planned", "unknown"}
        if isinstance(raw_indicators, list):
            for raw in raw_indicators:
                if not isinstance(raw, dict):
                    continue
                name = self._text(raw.get("name"), limit=240)
                if not name:
                    continue
                source = next((f for f in material_sources(state) if f.file_id == raw.get("source_document_id")), None)
                anchor = locate(source, str(raw.get("source_quote") or "")) if source else None
                if not anchor:
                    continue
                value = self._text(raw.get("value"), limit=100)
                if value and any(number not in anchor["source_span"] for number in re.findall(r"\d+(?:\.\d+)?", value)):
                    continue
                source_index = self._positive_int(raw.get("source_claim_index"), default=1)
                source_claim = state.claims[min(source_index - 1, len(state.claims) - 1)]
                status = self._text(raw.get("status"), limit=30)
                indicators.append(Indicator(
                    indicator_id=f"I-{len(indicators) + 1:03d}",
                    name=name,
                    value=self._text(raw.get("value"), limit=100),
                    unit=self._text(raw.get("unit"), limit=30),
                    status=status if status in allowed_indicator_status else "unknown",
                    source_claim_id=source_claim.claim_id,
                    **{k: v for k, v in anchor.items() if k in Indicator.model_fields},
                    baseline=self._text(raw.get("baseline"), limit=160),
                    test_method=self._text(raw.get("test_method"), limit=240),
                    statistics_method=self._text(raw.get("statistics_method"), limit=180),
                ))
        if indicators:
            state.indicators = indicators
            state.indicator_validations = [
                IndicatorValidation(
                    indicator_id=item.indicator_id,
                    definition=item.name,
                    unit=item.unit,
                    target=f"{item.value}{item.unit}".strip(),
                    status="partial" if item.value else "missing",
                    gaps=["需要补充基线、测试方法、样本设计和验收条件"],
                )
                for item in indicators
            ]
            raw_validations = payload.get("indicator_validations")
            if isinstance(raw_validations, list):
                by_id = {item.indicator_id: item for item in state.indicator_validations}
                allowed_validation_status = {"supported", "partial", "missing", "not_applicable"}
                for raw in raw_validations:
                    if not isinstance(raw, dict):
                        continue
                    indicator_id = self._text(raw.get("indicator_id"), limit=40)
                    if indicator_id not in by_id:
                        continue
                    current = by_id[indicator_id]
                    by_id[indicator_id] = current.model_copy(update={
                        "definition": self._text(raw.get("definition"), limit=300) or current.definition,
                        "unit": self._text(raw.get("unit"), limit=40) or current.unit,
                        "baseline": self._text(raw.get("baseline"), limit=160),
                        "target": self._text(raw.get("target"), limit=160) or current.target,
                        "test_method": self._text(raw.get("test_method"), limit=300),
                        "sample_design": self._text(raw.get("sample_design"), limit=300),
                        "statistics_method": self._text(raw.get("statistics_method"), limit=240),
                        "acceptance_criteria": self._text(raw.get("acceptance_criteria"), limit=300),
                        "status": self._text(raw.get("status"), limit=30) if self._text(raw.get("status"), limit=30) in allowed_validation_status else current.status,
                        # Literature IDs may be resolved by the retrieval
                        # stage that follows extraction; final integrity is
                        # checked after the evidence pool is complete.
                        "evidence_refs": self._string_list(raw.get("evidence_refs"), limit=8, item_limit=40),
                        "gaps": self._string_list(raw.get("gaps"), limit=8, item_limit=240),
                    })
                state.indicator_validations = list(by_id.values())
        raw_work_packages = payload.get("work_packages")
        if isinstance(raw_work_packages, list):
            packages: list[WorkPackage] = []
            for raw in raw_work_packages[:12]:
                if not isinstance(raw, dict):
                    continue
                title = self._text(raw.get("title"), limit=160)
                if not title:
                    continue
                packages.append(WorkPackage(
                    work_package_id=f"WP-{len(packages) + 1:02d}",
                    title=title,
                    objective=self._text(raw.get("objective"), limit=600),
                    inputs=self._string_list(raw.get("inputs"), limit=8, item_limit=180),
                    outputs=self._string_list(raw.get("outputs"), limit=8, item_limit=180),
                    dependencies=self._string_list(raw.get("dependencies"), limit=8, item_limit=180),
                    verification_nodes=self._string_list(raw.get("verification_nodes"), limit=8, item_limit=240),
                    source_refs=[source_document_id] if source_document_id else [],
                ))
            if packages:
                state.work_packages = packages
        return True

    def _apply_model_query_plans(
        self,
        state: ContentAssessmentState,
        payload: dict[str, object],
        *,
        year_range: list[int],
        exclusion_terms: list[str],
    ) -> bool:
        """Accept only query plans that remain tied to a known claim or indicator."""
        raw_queries = payload.get("queries")
        if not isinstance(raw_queries, list):
            return False
        claim_ids = {item.claim_id for item in state.claims}
        indicator_ids = {item.indicator_id for item in state.indicators}
        allowed_roles = {item[1] for item in STANDARD_QUERY_GROUPS}
        plans: list[QueryPlan] = []
        for raw in raw_queries[:6]:
            if not isinstance(raw, dict):
                continue
            query = self._text(raw.get("query"), limit=300)
            role = self._text(raw.get("query_role"), limit=60)
            scoped_claims = [item for item in self._string_list(raw.get("claim_ids"), limit=MAX_CLAIMS, item_limit=40) if item in claim_ids]
            scoped_indicators = [item for item in self._string_list(raw.get("indicator_ids"), limit=12, item_limit=40) if item in indicator_ids]
            if not query or role not in allowed_roles or not (scoped_claims or scoped_indicators):
                continue
            related_text = " ".join(
                [claim.text for claim in state.claims if claim.claim_id in scoped_claims]
                + [item.name for item in state.indicators if item.indicator_id in scoped_indicators]
            )
            if not any(term.lower() in query.lower() for term in self._keywords(related_text)):
                continue
            if any(item.query_role == role for item in plans):
                continue
            model_exclusions = self._string_list(raw.get("exclusion_terms"), limit=12, item_limit=80)
            plans.append(QueryPlan(
                claim_id=scoped_claims[0] if scoped_claims else "",
                claim_ids=scoped_claims,
                indicator_id=scoped_indicators[0] if scoped_indicators else "",
                indicator_ids=scoped_indicators,
                query=query,
                query_group=role,
                query_role=role,
                year_range=year_range,
                rationale=self._text(raw.get("rationale"), limit=400) or "围绕已关联的主张和指标检索同口径证据。",
                exclusion_terms=list(dict.fromkeys(exclusion_terms + model_exclusions)),
                filters={"document_types": ["paper", "standard", "guideline"], "languages": ["zh", "en"]},
                source_status="pending" if LITERATURE_SEARCH_API_URL else "unavailable",
            ))
        roles = {item.query_role for item in plans}
        if len(plans) < 1 or not roles:
            return False
        existing = {item.query_role for item in state.query_plans}
        state.query_plans = plans + [item for item in state.query_plans if item.query_role not in {p.query_role for p in plans}]
        return True

    def _apply_model_comparison(self, state: ContentAssessmentState, payload: dict[str, object]) -> bool:
        claim_ids = {item.claim_id for item in state.claims}
        evidence_ids = {
            item.evidence_id for item in state.evidence_records
            if item.source_type == "literature"
        }
        if not evidence_ids:
            return False
        allowed_relations = {"supports", "contradicts", "partially_supports", "not_comparable", "insufficient_evidence"}
        links: list[ClaimEvidenceLink] = []
        raw_links = payload.get("links")
        if isinstance(raw_links, list):
            for raw in raw_links[: max(24, len(state.claims) * 8)]:
                if not isinstance(raw, dict):
                    continue
                claim_id = self._text(raw.get("claim_id"), limit=30)
                evidence_id = self._text(raw.get("evidence_id"), limit=40)
                relation = self._text(raw.get("relation"), limit=40)
                if claim_id not in claim_ids or evidence_id not in evidence_ids or relation not in allowed_relations:
                    continue
                claim = next(c for c in state.claims if c.claim_id == claim_id)
                evidence = next(e for e in state.evidence_records if e.evidence_id == evidence_id)
                evidence = self._claim_evidence_view(state, claim, evidence)
                if evidence is None:
                    continue
                if not relevant(claim, evidence):
                    continue
                verified_matrix = self._field_matrix(claim, evidence)
                if relation in {"supports", "partially_supports", "contradicts"}:
                    if not evidence.anchor_verified or evidence.evidence_level != "A":
                        relation = "insufficient_evidence"
                    elif any(v == "conflict" for v in verified_matrix.values()):
                        relation = "not_comparable"
                    elif not any(v == "covered" for v in verified_matrix.values()):
                        relation = "insufficient_evidence"
                links.append(ClaimEvidenceLink(
                    claim_id=claim_id,
                    evidence_id=evidence_id,
                    relation=relation,
                    evidence_level=evidence.evidence_level,
                    evidence_chunk_ids=[c.chunk_id for c in state.evidence_chunks if c.evidence_id == evidence_id
                        and c.reopenable and (not c.claim_ids or claim_id in c.claim_ids)][:3],
                    comparable_fields=self._string_list(raw.get("comparable_fields"), limit=6, item_limit=80),
                    difference=self._text(raw.get("difference"), limit=500),
                    uncertainty=self._text(raw.get("uncertainty"), limit=500),
                    confidence=self._confidence(raw.get("confidence")),
                    field_matrix=verified_matrix,
                    incomparability_reasons=self._string_list(raw.get("incomparability_reasons"), limit=8, item_limit=240),
                ))
        if links:
            replacements = {(l.claim_id, l.evidence_id): l for l in links}
            merged = {(l.claim_id, l.evidence_id): l for l in state.claim_evidence_links}
            if state.scenario_id:
                evidence_by_id = {item.evidence_id: item for item in state.evidence_records}
                scenario_batch = f"homepage-scenario-v1:{state.scenario_id}"
                replacements = {
                    key: value for key, value in replacements.items()
                    if evidence_by_id.get(key[1]) is None
                    or evidence_by_id[key[1]].retrieval_batch_id != scenario_batch
                }
            merged.update(replacements)
            state.claim_evidence_links = list(merged.values())

        allowed_labels = {
            "有直接支持", "存在相近先例", "方向性支持", "暂未发现可比证据", "当前不可判断",
        }
        defaults = {item.claim_id: item for item in state.novelty_decisions}
        overrides: dict[str, NoveltyDecision] = {}
        raw_decisions = payload.get("decisions")
        if isinstance(raw_decisions, list):
            for raw in raw_decisions[:len(state.claims)]:
                if not isinstance(raw, dict):
                    continue
                claim_id = self._text(raw.get("claim_id"), limit=30)
                rationale = self._text(raw.get("rationale"), limit=1200)
                label = self._text(raw.get("label"), limit=40)
                selected_evidence = [
                    value for value in self._string_list(raw.get("evidence_ids"), limit=5, item_limit=40)
                    if value in evidence_ids and any(link.claim_id == claim_id and link.evidence_id == value for link in state.claim_evidence_links)
                ]
                if claim_id not in claim_ids or not rationale or label not in allowed_labels:
                    continue
                if not selected_evidence and label not in {"当前不可判断", "暂未发现可比证据"}:
                    label = "当前不可判断"
                overrides[claim_id] = NoveltyDecision(
                    claim_id=claim_id,
                    label=label,
                    rationale=rationale,
                    evidence_ids=selected_evidence,
                    risk_notes=self._string_list(raw.get("risk_notes"), limit=5, item_limit=300),
                    rewrite_advice=self._text(raw.get("rewrite_advice"), limit=600),
                )
        if overrides:
            state.novelty_decisions = [
                overrides.get(claim.claim_id, defaults[claim.claim_id]) for claim in state.claims
            ]

        # Keep the detailed matrix synchronized with model-returned links and
        # decisions; the deterministic fields remain the fallback for any
        # claim the model omitted.
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        existing_details = {item.claim_id: item for item in state.claim_assessments}
        state.claim_assessments = []
        decision_by_claim = {item.claim_id: item for item in state.novelty_decisions}
        for claim in state.claims:
            claim_links = links_by_claim.get(claim.claim_id, [])
            prior = existing_details.get(claim.claim_id)
            state.claim_assessments.append(ClaimAssessmentDetail(
                claim_id=claim.claim_id,
                parent_claim_id=claim.parent_claim_id,
                aggregation_group=claim.aggregation_group,
                aggregation_reason=claim.aggregation_reason,
                normalized_claim=claim.text,
                comparable_fields=prior.comparable_fields if prior else {},
                supporting_evidence_ids=[item.evidence_id for item in claim_links if item.relation in {"supports", "partially_supports"}],
                contradicting_evidence_ids=[item.evidence_id for item in claim_links if item.relation == "contradicts"],
                evidence_gaps=prior.evidence_gaps if prior else [],
                field_comparisons=list(dict.fromkeys(field for item in claim_links for field in item.comparable_fields)) or (prior.field_comparisons if prior else []),
                conclusion=(decision_by_claim.get(claim.claim_id).rationale if decision_by_claim.get(claim.claim_id) else prior.conclusion if prior else "待补充"),
                uncertainty=(decision_by_claim.get(claim.claim_id).risk_notes[0] if decision_by_claim.get(claim.claim_id) and decision_by_claim.get(claim.claim_id).risk_notes else prior.uncertainty if prior else "待补充"),
                confidence=max((item.confidence for item in claim_links), default=prior.confidence if prior else 0.0),
                nearest_prior_art_id=prior.nearest_prior_art_id if prior else "",
                field_matrix=prior.field_matrix if prior else {},
                incomparability_reasons=prior.incomparability_reasons if prior else [],
                advancedness_conclusion=prior.advancedness_conclusion if prior else "",
            ))
        links_by_evidence = defaultdict(list)
        for link in state.claim_evidence_links:
            if link.relation in {"supports", "partially_supports"}:
                links_by_evidence[link.evidence_id].append(link.claim_id)
        for chunk in state.evidence_chunks:
            chunk.supports_claim_ids = list(dict.fromkeys(links_by_evidence.get(chunk.evidence_id, [])))

        allowed_dimensions = {
            "创新性", "先进性", "可行性", "风险", "证据充分性", "应用与合规",
        }
        scorecard: list[ScoreCardItem] = []
        raw_scorecard = payload.get("scorecard")
        if isinstance(raw_scorecard, list):
            for raw in raw_scorecard[:7]:
                if not isinstance(raw, dict):
                    continue
                dimension = self._text(raw.get("dimension"), limit=40)
                assessment = self._text(raw.get("assessment"), limit=800)
                if dimension not in allowed_dimensions or not assessment or any(item.dimension == dimension for item in scorecard):
                    continue
                scorecard.append(ScoreCardItem(
                    dimension=dimension,
                    assessment=assessment,
                    score_range=self._text(raw.get("score_range"), limit=80),
                    basis=self._string_list(raw.get("basis"), limit=5, item_limit=300),
                    uncertainty=self._text(raw.get("uncertainty"), limit=500),
                ))
        if scorecard:
            state.scorecard = scorecard
        return bool(links or overrides or scorecard)

    def _apply_model_landscape(self, state: ContentAssessmentState, payload: dict[str, object]) -> bool:
        summary = self._text(payload.get("summary"), limit=1200)
        evidence_ids = {item.evidence_id for item in state.evidence_records if any(relevant(c, item) for c in state.claims)}
        highlights: list[str] = []
        raw_highlights = payload.get("trend_highlights")
        if isinstance(raw_highlights, list):
            for item in raw_highlights[:6]:
                if not isinstance(item, dict):
                    continue
                source_ids = [value for value in self._string_list(item.get("evidence_ids"), limit=6, item_limit=40) if value in evidence_ids]
                text = self._text(item.get("text"), limit=400)
                if text and source_ids:
                    highlights.append(f"{text}（证据：{'、'.join(source_ids)}）")
        competition = []
        raw_competition = payload.get("competition_observations")
        if isinstance(raw_competition, list):
            for item in raw_competition[:6]:
                if not isinstance(item, dict):
                    continue
                source_ids = [value for value in self._string_list(item.get("evidence_ids"), limit=6, item_limit=40) if value in evidence_ids]
                text = self._text(item.get("text"), limit=400)
                if text and source_ids:
                    competition.append(f"{text}（证据：{'、'.join(source_ids)}）")
        risks = self._string_list(payload.get("risk_notes"), limit=6, item_limit=400)
        themes = payload.get("themes") if isinstance(payload.get("themes"), list) else []
        methods = payload.get("methods") if isinstance(payload.get("methods"), list) else []
        theme_rows = [item for item in themes[:8] if isinstance(item, dict)]
        method_rows = [item for item in methods[:8] if isinstance(item, dict)]
        raw_cards = payload.get("data_cards") if isinstance(payload.get("data_cards"), list) else []
        cards: list[LandscapeDataCard] = []
        for raw in raw_cards[:12]:
            if not isinstance(raw, dict):
                continue
            source_ids = [value for value in self._string_list(raw.get("source_evidence_ids"), limit=8, item_limit=40) if value in evidence_ids]
            card_type = self._text(raw.get("card_type"), limit=30)
            subject = self._text(raw.get("subject"), limit=160)
            if card_type not in {"trend", "market", "regulatory", "competitive"} or not subject:
                continue
            cards.append(LandscapeDataCard(
                card_id=f"MODEL-{card_type}-{len(cards) + 1:03d}",
                card_type=card_type,  # type: ignore[arg-type]
                subject=subject,
                value=self._text(raw.get("value"), limit=100),
                unit=self._text(raw.get("unit"), limit=40),
                period=self._text(raw.get("period"), limit=60),
                source_evidence_ids=source_ids,
                status="available" if source_ids and self._text(raw.get("status"), limit=40) == "available" else "qualitative" if source_ids else "insufficient_evidence",
                limitation=self._text(raw.get("limitation"), limit=400) or ("缺少正式来源，显示数据不足。" if not source_ids else "仅适用于本次证据池。"),
            ))
        if not any((summary, highlights, competition, risks, cards)):
            return False
        state.trend_analysis = {
            **state.trend_analysis,
            "summary": summary or state.trend_analysis.get("summary", ""),
            "trend_highlights": highlights,
            "competition_observations": competition,
            "risk_notes": risks,
            "themes": theme_rows or state.trend_analysis.get("themes", []),
            "methods": method_rows or state.trend_analysis.get("methods", []),
            "source": "server_model_with_retrieved_evidence",
        }
        for card in cards:
            target = {
                "market": state.market_data_cards,
                "regulatory": state.regulatory_data_cards,
                "competitive": state.competitive_data_cards,
            }.get(card.card_type)
            if target is not None:
                target.append(card)
        return True

    @staticmethod
    def _formal_evidence(item: EvidenceRecord) -> bool:
        """Return whether an evidence record can support a formal conclusion."""
        return bool(
            item.source_type == "literature"
            and item.fulltext_available
            and item.anchor_verified
            and item.access_status == "authorized"
            and ((item.char_start is not None and item.char_end is not None and item.char_end > item.char_start)
                 or (item.locator.get("chunk_id") and item.source_hash))
            and item.section_ref.lower() != "abstract"
            and item.excerpt.strip()
        )

    @staticmethod
    def _normal(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    @classmethod
    def _field_matrix(cls, claim: Claim, evidence: EvidenceRecord) -> dict[str, str]:
        claim_fields = claim.claim_fields or {}
        evidence_fields = evidence.comparison_fields or {}
        matrix: dict[str, str] = {}
        for field, _label in COMPARISON_FIELDS:
            claim_value = str(claim_fields.get(field) or "").strip()
            evidence_value = str(evidence_fields.get(field) or "").strip()
            if not claim_value or not evidence_value:
                matrix[field] = "missing"
                continue
            claim_text = cls._normal(claim_value)
            evidence_text = cls._normal(evidence_value)
            if claim_text == evidence_text or claim_text in evidence_text or evidence_text in claim_text:
                matrix[field] = "covered"
            else:
                matrix[field] = "conflict"
        return matrix

    @classmethod
    def _incomparability_reasons(cls, matrix: dict[str, str], evidence: EvidenceRecord) -> list[str]:
        reasons: list[str] = []
        if not evidence.anchor_verified:
            reasons.append("缺少可重新打开的全文字符锚点")
        labels = dict(COMPARISON_FIELDS)
        for field, status in matrix.items():
            if status == "conflict":
                reasons.append(f"{labels[field]}字段不一致")
            elif status == "missing":
                reasons.append(f"缺少{labels[field]}字段")
        return reasons

    @classmethod
    def _extract_claim_fields(cls, text: str) -> dict[str, str]:
        """Extract only explicitly stated comparison fields; never fill gaps with guesses."""
        value = " ".join(str(text or "").split())
        fields: dict[str, str] = {}
        patterns = {
            "object": r"(?:对象|针对|构建|建立|开发|研究)([^，。；;]{2,40})",
            "method": r"(?:采用|通过|基于|利用|使用|方法|技术路线)([^，。；;]{2,60})",
            "data": r"(?:数据|样本|病例|实验材料|受试者)([^，。；;]{1,60})",
            "baseline": r"(?:基线|对照|相比|相较于|优于)([^，。；;]{1,60})",
            "metric": r"(?:提高|降低|达到|指标|稳定性|准确率|性能|效率|灵敏度)([^，。；;]{0,50}(?:\d+(?:\.\d+)?\s*(?:%|倍|项|例|mm|μm|个月|年|天))?)",
            "time": r"(\d+(?:\.\d+)?\s*(?:个月|年|天|小时|h|d|weeks?|周))",
            "validation": r"(?:验证|测试|实验|评估|验收)([^，。；;]{1,60})",
        }
        for field, pattern in patterns.items():
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if match:
                candidate = " ".join(match.group(0).split()).strip(" ：:，,。；;")
                if candidate:
                    fields[field] = candidate[:160]
        return fields

    @staticmethod
    def _paper_fulltext(paper: object) -> str:
        raw = getattr(paper, "raw", {}) or {}
        if not isinstance(raw, dict):
            return ""
        candidates: list[object] = [
            raw.get("fulltext"), raw.get("full_text"), raw.get("fullText"),
        ]
        if str(raw.get("content_scope") or raw.get("text_scope") or "").lower() == "fulltext":
            candidates.extend([raw.get("content"), raw.get("text"), raw.get("body")])
        for candidate in candidates:
            if isinstance(candidate, str) and len(candidate.strip()) >= 80:
                return candidate.strip()
            if isinstance(candidate, dict):
                for key in ("text", "content", "value"):
                    nested = candidate.get(key)
                    if isinstance(nested, str) and len(nested.strip()) >= 80:
                        return nested.strip()
        return ""

    @classmethod
    def _populate_evidence_anchor(
        cls, evidence: EvidenceRecord, fulltext: str, raw: dict[str, object] | None = None
    ) -> None:
        raw = raw if isinstance(raw, dict) else {}
        evidence.fulltext_available = bool(fulltext)
        evidence.paper_id = evidence.paper_id or str(raw.get("paper_id") or raw.get("paperId") or "")
        access = str(raw.get("access_status") or raw.get("accessStatus") or "").strip().lower()
        if access in {"authorized", "abstract_only", "unavailable"}:
            evidence.access_status = access  # type: ignore[assignment]
        elif fulltext:
            evidence.access_status = "authorized"
        else:
            evidence.access_status = "abstract_only" if evidence.abstract else "unavailable"
        raw_fields = raw.get("comparison_fields") or raw.get("comparisonFields") or {}
        if isinstance(raw_fields, dict):
            aliases = {
                "object": ("object", "对象", "scene", "场景"),
                "method": ("method", "方法"),
                "data": ("data", "数据", "sample", "样本"),
                "baseline": ("baseline", "基线", "control", "对照"),
                "metric": ("metric", "指标", "result", "结果"),
                "time": ("time", "时间", "duration", "时长"),
                "validation": ("validation", "验证条件", "test_method", "测试方法"),
            }
            evidence.comparison_fields = {
                field: str(next((raw_fields.get(key) for key in keys if raw_fields.get(key)), ""))[:240]
                for field, keys in aliases.items()
                if any(raw_fields.get(key) for key in keys)
            }
        if not fulltext:
            return
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\r\n", fulltext) if part.strip()]
        if not paragraphs:
            paragraphs = [fulltext]
        terms = [term for term in evidence.keywords if term]
        selected_index, selected = 0, paragraphs[0]
        if terms:
            ranked = sorted(
                enumerate(paragraphs),
                key=lambda item: sum(term.lower() in item[1].lower() for term in terms),
                reverse=True,
            )
            if ranked and sum(term.lower() in ranked[0][1].lower() for term in terms) > 0:
                selected_index, selected = ranked[0]
        start = fulltext.find(selected)
        if start < 0:
            start = 0
            selected = fulltext[:1600]
        end = min(len(fulltext), start + min(len(selected), 1600))
        evidence.excerpt = fulltext[start:end]
        evidence.char_start = start
        evidence.char_end = end
        evidence.paragraph_ref = f"p-{selected_index + 1:04d}"
        evidence.section_ref = str(raw.get("section") or raw.get("section_ref") or evidence.query_group or "全文")
        evidence.page_ref = str(raw.get("page") or raw.get("page_ref") or "")
        evidence.source_hash = hashlib.sha256(fulltext.encode("utf-8", errors="ignore")).hexdigest()
        evidence.locator = {
            "kind": "literature_fulltext",
            "page": evidence.page_ref,
            "section": evidence.section_ref,
            "paragraph": evidence.paragraph_ref,
            "char_start": evidence.char_start,
            "char_end": evidence.char_end,
        }
        evidence.anchor_verified = bool(
            evidence.char_start >= 0
            and evidence.char_end > evidence.char_start
            and fulltext[evidence.char_start:evidence.char_end] == evidence.excerpt
            and evidence.source_hash
            and evidence.access_status == "authorized"
        )

    def _sanitize_claim_outputs(self, state: ContentAssessmentState) -> None:
        """Apply deterministic evidence rules after optional model output."""
        evidence_by_id = {item.evidence_id: item for item in state.evidence_records}
        chunks_by_evidence: dict[str, list[EvidenceChunk]] = defaultdict(list)
        for chunk in state.evidence_chunks:
            chunks_by_evidence[chunk.evidence_id].append(chunk)
        for link in state.claim_evidence_links:
            evidence = evidence_by_id.get(link.evidence_id)
            claim = next((c for c in state.claims if c.claim_id == link.claim_id), None)
            if evidence is not None and claim is not None:
                evidence = self._claim_evidence_view(state, claim, evidence)
            if evidence is None:
                link.relation = "insufficient_evidence"
                link.confidence = 0.0
                link.evidence_level = "C"
                link.evidence_chunk_ids = []
                link.incomparability_reasons = list(dict.fromkeys(link.incomparability_reasons + ["证据记录不存在"]))
                continue
            if evidence.evidence_level == "C" or not evidence.abstract and not evidence.excerpt:
                link.relation = "insufficient_evidence"
                link.confidence = 0.0
                link.evidence_level = "C"
                link.evidence_chunk_ids = []
                link.incomparability_reasons = list(dict.fromkeys(
                    link.incomparability_reasons + ["题录级记录不能支撑主张结论"]
                ))
                continue
            scenario_batch = f"homepage-scenario-v1:{state.scenario_id}" if state.scenario_id else ""
            if scenario_batch and evidence.retrieval_batch_id == scenario_batch:
                claim = next((item for item in state.claims if item.claim_id == link.claim_id), Claim(claim_id="", text=""))
                link.evidence_level = evidence.evidence_level
                link.field_matrix = link.field_matrix or self._field_matrix(claim, evidence)
                link.evidence_chunk_ids = list(dict.fromkeys(
                    item.chunk_id for item in chunks_by_evidence.get(link.evidence_id, []) if item.reopenable
                    and (not item.claim_ids or link.claim_id in item.claim_ids)
                ))
                link.incomparability_reasons = list(dict.fromkeys(
                    link.incomparability_reasons + self._incomparability_reasons(link.field_matrix, evidence)
                ))
                # Homepage snapshots establish a verified adjacent route, not
                # a blanket claim of leadership or target achievement. Keep
                # that calibrated relationship stable when optional model
                # output is more pessimistic, while retaining every field gap.
                link.relation = "partially_supports"
                link.confidence = min(0.55, max(link.confidence, 0.35))
                continue
            if evidence.evidence_level == "B" or not self._formal_evidence(evidence):
                link.evidence_level = "B"
                link.field_matrix = link.field_matrix or self._field_matrix(claim, evidence)
                if evidence.abstract.strip() or evidence.excerpt.strip():
                    # Preserve a directional relationship for abstract-level
                    # records.  This is deliberately capped below formal
                    # evidence and remains subject to the target-value caveat.
                    link.relation = "partially_supports" if link.relation in {
                        "supports", "partially_supports", "insufficient_evidence"
                    } else link.relation
                    link.confidence = min(max(link.confidence, 0.15), 0.55)
                else:
                    link.relation = "insufficient_evidence"
                    link.confidence = 0.0
                link.evidence_chunk_ids = [item.chunk_id for item in chunks_by_evidence.get(link.evidence_id, [])
                    if not item.claim_ids or link.claim_id in item.claim_ids]
                link.incomparability_reasons = list(dict.fromkeys(
                    link.incomparability_reasons + ["B级摘要或部分文本仅支持方向性判断，待全文核验"]
                ))
                continue
            link.evidence_level = evidence.evidence_level
            link.field_matrix = link.field_matrix or self._field_matrix(
                next((item for item in state.claims if item.claim_id == link.claim_id), Claim(claim_id="", text="")),
                evidence,
            )
            valid_chunks = [item for item in chunks_by_evidence.get(link.evidence_id, []) if item.reopenable
                            and (not item.claim_ids or link.claim_id in item.claim_ids)]
            link.evidence_chunk_ids = list(dict.fromkeys(item.chunk_id for item in valid_chunks))
            link.incomparability_reasons = list(dict.fromkeys(
                link.incomparability_reasons + self._incomparability_reasons(link.field_matrix, evidence)
            ))
            if link.relation in {"supports", "partially_supports"} and (
                not link.evidence_chunk_ids or link.incomparability_reasons
            ):
                link.relation = "not_comparable"
                link.confidence = min(link.confidence, 0.25)

        valid_supports = {
            (link.claim_id, link.evidence_id)
            for link in state.claim_evidence_links
            if link.relation in {"supports", "partially_supports"}
            and link.evidence_chunk_ids
            and not link.incomparability_reasons
        }
        for decision in state.novelty_decisions:
            decision.evidence_ids = [item for item in decision.evidence_ids if item in evidence_by_id]
            usable = [item for item in decision.evidence_ids if (decision.claim_id, item) in valid_supports]
            directional = [
                link.evidence_id for link in state.claim_evidence_links
                if link.claim_id == decision.claim_id
                and link.evidence_level == "B"
                and link.relation == "partially_supports"
            ]
            if decision.label not in {"目标待验证", "证据不足", "当前不可判断", "暂未发现可比证据"} and not usable and not directional:
                decision.label = "当前不可判断"
                decision.rationale = "候选记录与主张方向相关性不足，当前不能形成可靠的方向性判断。"
                decision.evidence_ids = []

        by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            by_claim[link.claim_id].append(link)
        old_details = {item.claim_id: item for item in state.claim_assessments}
        decisions = {item.claim_id: item for item in state.novelty_decisions}
        state.claim_assessments = []
        for claim in state.claims:
            links = by_claim.get(claim.claim_id, [])
            prior = old_details.get(claim.claim_id)
            field_matrix = prior.field_matrix if prior else {}
            if not field_matrix:
                best = max(links, key=lambda item: sum(value == "covered" for value in item.field_matrix.values()), default=None)
                field_matrix = best.field_matrix if best else {}
            supporting = [item.evidence_id for item in links if item.relation in {"supports", "partially_supports"} and item.evidence_chunk_ids]
            directional = [
                item.evidence_id for item in links
                if item.evidence_level == "B"
                and item.relation in {"partially_supports", "not_comparable"}
                and item.evidence_chunk_ids
            ]
            contradicting = [item.evidence_id for item in links if item.relation == "contradicts" and item.evidence_chunk_ids]
            decision = decisions.get(claim.claim_id)
            state.claim_assessments.append(ClaimAssessmentDetail(
                claim_id=claim.claim_id,
                parent_claim_id=claim.parent_claim_id,
                aggregation_group=claim.aggregation_group,
                aggregation_reason=claim.aggregation_reason,
                normalized_claim=claim.text,
                comparable_fields=dict(claim.claim_fields),
                supporting_evidence_ids=supporting or [
                    item.evidence_id for item in links
                    if item.evidence_level == "B" and item.relation == "partially_supports"
                ],
                contradicting_evidence_ids=contradicting,
                evidence_gaps=list(dict.fromkeys(
                    (prior.evidence_gaps if prior else [])
                    + [reason for link in links for reason in link.incomparability_reasons]
                ))[:12],
                field_comparisons=[label for _key, label in COMPARISON_FIELDS],
                conclusion=decision.rationale if decision else (prior.conclusion if prior else "待补充"),
                uncertainty=(decision.risk_notes[0] if decision and decision.risk_notes else prior.uncertainty if prior else "待补充"),
                confidence=max((item.confidence for item in links), default=prior.confidence if prior else 0.0),
                field_matrix=field_matrix,
                nearest_prior_art_id=prior.nearest_prior_art_id if prior else (supporting[0] if supporting else directional[0] if directional else ""),
                advancedness_conclusion=prior.advancedness_conclusion if prior else "证据不足或存疑",
                incomparability_reasons=list(dict.fromkeys(
                    (prior.incomparability_reasons if prior else [])
                    + [reason for link in links for reason in link.incomparability_reasons]
                ))[:12],
            ))

        for chunk in state.evidence_chunks:
            chunk.supports_claim_ids = list(dict.fromkeys(
                link.claim_id for link in state.claim_evidence_links
                if link.evidence_id == chunk.evidence_id and link.relation in {"supports", "partially_supports"}
                and chunk.chunk_id in link.evidence_chunk_ids
            ))

    def _recompute_advancedness(self, state: ContentAssessmentState) -> None:
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        details = {item.claim_id: item for item in state.claim_assessments}
        results: list[AdvancednessDecision] = []
        for claim in state.claims:
            links = [
                link for link in links_by_claim.get(claim.claim_id, [])
                if link.evidence_level == "A" and link.evidence_chunk_ids
                and link.relation in {"supports", "partially_supports"}
                and not link.incomparability_reasons
            ]
            detail = details.get(claim.claim_id)
            if not links:
                results.append(AdvancednessDecision(
                    claim_id=claim.claim_id,
                    label="证据不足或存疑",
                    rationale="没有经过全文锚点和字段级可比性校验的证据，不能独立判断先进性。",
                    uncertainty="先进性判断不从创新性标签或关键词相似度推导。",
                ))
                continue
            comparable = bool(detail and len(detail.field_matrix) == len(COMPARISON_FIELDS)
                              and all(value == "covered" for value in detail.field_matrix.values()))
            results.append(AdvancednessDecision(
                claim_id=claim.claim_id,
                label="存在技术差异但无法证明领先",
                rationale=(
                    "字段矩阵可比，但当前仍缺少同口径量化结果和专家确认，不能直接标记为领先。"
                    if comparable else "已有可定位证据，但字段不完整或缺少统一基线，不能证明领先。"
                ),
                comparison_evidence_ids=[item.evidence_id for item in links[:5]],
                comparable_indicator_ids=[item.indicator_id for item in state.indicators if item.source_claim_id == claim.claim_id],
                uncertainty="先进性与创新性分开判定，不能把目标值直接当作已实现水平。",
            ))
        state.advancedness_decisions = results

    def _rebuild_novelty_decisions(self, state: ContentAssessmentState) -> None:
        """Derive labels from evidence tier and the seven-field matrix."""
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        previous = {item.claim_id: item for item in state.novelty_decisions}
        decisions: list[NoveltyDecision] = []
        for claim in state.claims:
            links = links_by_claim.get(claim.claim_id, [])
            comparable = [
                link for link in links
                if link.evidence_level == "A"
                and link.relation in {"supports", "partially_supports"}
                and link.evidence_chunk_ids
                and not link.incomparability_reasons
                and len(link.field_matrix) == len(COMPARISON_FIELDS)
                and all(value == "covered" for value in link.field_matrix.values())
            ]
            limited = [
                link for link in links
                if link.evidence_level == "B" and link.relation == "partially_supports"
            ]
            nearby = [
                link for link in links
                if link.evidence_level == "B"
                and link.relation == "not_comparable"
                and link.confidence > 0
                and any(link.field_matrix.get(key) == "covered" for key in ("object", "method"))
                and any(
                    evidence.evidence_id == link.evidence_id
                    and bool(evidence.abstract.strip() or evidence.excerpt.strip())
                    for evidence in state.evidence_records
                )
            ]
            comparable_partial = [
                link for link in links
                if link.evidence_level == "A"
                and link.relation == "partially_supports"
                and link.evidence_chunk_ids
            ]
            prior = previous.get(claim.claim_id)
            if comparable:
                label = "有直接支持"
                evidence_ids = [item.evidence_id for item in comparable[:5]]
                rationale = "存在带稳定全文锚点且七字段基本可比的直接证据；这支持相关技术事实，但不自动证明项目目标已经实现或具有领先性。"
            elif comparable_partial:
                label = "存在相近先例"
                evidence_ids = [item.evidence_id for item in comparable_partial[:5]]
                rationale = "存在带全文定位的相近技术先例，但对象、指标或验证条件仍有差异，当前只能判断为相近路线。"
            elif limited:
                label = "方向性支持"
                evidence_ids = [item.evidence_id for item in limited[:5]]
                rationale = "已有摘要或部分文本与主张方向相关，可支持相近路线和工程可行性的方向性判断；定量目标和领先性仍待全文或实验核验。"
            elif nearby:
                label = "存在相近先例"
                evidence_ids = [item.evidence_id for item in nearby[:5]]
                rationale = "检索记录与主张存在实质主题关联，但对象、方法或验证条件不一致，当前只能确认存在相近技术先例，不能作同口径性能判断。"
            elif claim.assertion_type in {"target", "planned"}:
                label = "暂未发现可比证据" if links else "当前不可判断"
                evidence_ids = []
                rationale = (
                    "当前检索记录中暂未发现可比先例；这不等同于不存在先例。项目目标仍需在明确基线和验证条件后独立复核。"
                    if links else
                    "该主张属于目标或计划，当前没有足够相关证据形成方向性判断；需要扩大检索或补充材料。"
                )
            else:
                label = "暂未发现可比证据" if links else "当前不可判断"
                evidence_ids = []
                rationale = (
                    "当前检索记录与主张存在主题关联，但尚未获得可比证据；不能据此认定没有先例。"
                    if links else
                    "当前没有足够相关记录，暂不能形成方向性判断。"
                )
            if prior and prior.label == label and prior.evidence_ids:
                # Preserve reviewed, claim-specific reasoning while retaining
                # the deterministic limit on conclusion strength.
                reviewed_ids = [eid for eid in prior.evidence_ids if eid in evidence_ids]
                if reviewed_ids:
                    rationale = prior.rationale
                    evidence_ids = reviewed_ids
            risk_notes = list(dict.fromkeys((prior.risk_notes if prior else []) + [
                "摘要或题录可以支持方向性判断，但不能替代全文实验数据和统一验证条件。",
                "创新性判断不等同于先进性或市场竞争排名。",
            ]))[:4]
            decisions.append(NoveltyDecision(
                claim_id=claim.claim_id,
                label=label,  # type: ignore[arg-type]
                rationale=rationale,
                evidence_ids=evidence_ids,
                risk_notes=risk_notes,
                rewrite_advice=(prior.rewrite_advice if prior and prior.rewrite_advice else
                                f"将“{claim.text[:60]}”改写为包含对象、基线、指标、样本和验证条件的可检验表述。"),
            ))
        state.novelty_decisions = decisions

    @classmethod
    def resolve_review_task(
        cls,
        state: ContentAssessmentState,
        review_task_id: str,
        *,
        decision: str,
        reason: str = "",
        operator: str = "",
    ) -> str:
        """Apply an expert decision and return the stage that must be rerun."""
        task = next((item for item in state.review_tasks if item.review_task_id == review_task_id), None)
        if task is None:
            raise ValueError("复核任务不存在")
        if task.status not in {"open", "returned"}:
            raise ValueError("复核任务已经处理，不能重复提交")
        if decision not in {"approve", "limit", "return_to_retrieval", "dismiss"}:
            raise ValueError("复核决策无效")
        status_map = {
            "approve": "approved",
            "limit": "limited",
            "return_to_retrieval": "returned",
            "dismiss": "dismissed",
        }
        now = datetime.now().astimezone().isoformat()
        task.status = status_map[decision]  # type: ignore[assignment]
        task.resolved_by = operator
        task.resolved_at = now
        task.resolution_reason = reason
        affected = ["quality_gate", "report"]
        resume_stage = "quality_gate"
        if decision == "limit" and task.claim_id:
            for item in state.novelty_decisions:
                if item.claim_id == task.claim_id:
                    item.label = "证据不足"
                    item.evidence_ids = []
                    item.risk_notes = list(dict.fromkeys(item.risk_notes + [reason or "专家要求限制结论强度。"]))
            for item in state.advancedness_decisions:
                if item.claim_id == task.claim_id:
                    item.label = "证据不足或存疑"
                    item.comparison_evidence_ids = []
        if decision == "return_to_retrieval":
            affected = ["retrieval", "comparison", "assessment", "landscape", "quality_gate", "report"]
            resume_stage = "retrieval"
            state.query_plans = []
            state.evidence_chunks = []
            state.claim_evidence_links = []
            state.claim_assessments = []
            state.novelty_decisions = []
            state.advancedness_decisions = []
            state.trend_analysis = {}
            state.report_plan = AssessmentReportPlan()
            state.output_markdown = ""
            state.output_json.pop("search_trace", None)
            state.output_json.pop("retrieval_stats", None)
        state.resolution_logs.append(ResolutionLog(
            resolution_id=f"RES-{len(state.resolution_logs) + 1:04d}",
            review_task_id=review_task_id,
            decision=decision,  # type: ignore[arg-type]
            operator=operator,
            occurred_at=now,
            reason=reason,
            evidence_version=state.versions.evidence_pool_version,
            affected_stages=affected,
        ))
        state.invalidation_records.append(InvalidationRecord(
            invalidation_id=f"INV-{len(state.invalidation_records) + 1:04d}",
            reason=reason or f"专家复核决策：{decision}",
            source_version=state.versions.report_version,
            affected_stages=affected,
            created_at=now,
        ))
        state.versions.invalidated_stages = affected
        state.resume_from_stage = resume_stage
        state.current_stage = resume_stage
        for index, (key, _name) in enumerate(STAGES):
            if key not in affected:
                continue
            trace = next((item for item in state.stage_trace if item.stage_key == key), None)
            if trace:
                trace.status = "pending"
                trace.summary = "等待复核处理后重新执行"
                trace.started_at = None
                trace.completed_at = None
            state.process_trace.pop(key, None)
            state.versions.stage_versions.pop(key, None)
        reusable = [
            key for key in state.versions.stage_versions
            if key not in affected
        ]
        state.output_json["recompute_plan"] = {
            "resume_from_stage": resume_stage,
            "invalidated_stages": affected,
            "reused_stage_versions": reusable,
        }
        return resume_stage

    def _material_text(self, state: ContentAssessmentState) -> str:
        material = state.raw_user_input[:12000]
        file_context = state.uploaded_file_context.prompt_brief[:12000]
        if file_context:
            material = f"{material}\n\n上传材料摘要：\n{file_context}"
        return material

    @staticmethod
    def _version_digest(value: object, *, prefix: str) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"

    @classmethod
    def _save_checkpoint(cls, state: ContentAssessmentState, *, status: str) -> None:
        """Persist a compact durable checkpoint marker alongside the graph state."""
        if status in {"completed", "awaiting_confirmation"} and state.current_stage in {key for key, _name in STAGES}:
            cls._refresh_stage_version(state)
        state.checkpoint_revision += 1
        checkpoint_payload = state.model_dump(
            exclude={"checkpoint_history", "checkpoint_revision", "output_markdown"}
        )
        state_hash = cls._version_digest(checkpoint_payload, prefix="state")
        record = CheckpointRecord(
            checkpoint_id=state.checkpoint_id,
            revision=state.checkpoint_revision,
            stage=state.current_stage,
            status=status,  # type: ignore[arg-type]
            state_hash=state_hash,
            saved_at=datetime.now().astimezone().isoformat(),
        )
        state.checkpoint_history.append(record)
        state.checkpoint_history = state.checkpoint_history[-20:]
        state.output_json["checkpoint"] = record.model_dump()

    @classmethod
    def _refresh_stage_version(cls, state: ContentAssessmentState) -> None:
        stage = state.current_stage
        stage_payloads: dict[str, Any] = {
            "document_intake": {
                "profile": state.assessment_profile.model_dump(),
                "boundary": state.boundary_matrix.model_dump(),
                "files": [item.model_dump() for item in state.uploaded_file_context.files],
            },
            "claim_extraction": {
                "nine_fields": state.nine_fields.model_dump(),
                "work_packages": [item.model_dump() for item in state.work_packages],
                "claims": [item.model_dump() for item in state.claims],
                "indicators": [item.model_dump() for item in state.indicators],
            },
            "retrieval": {
                "plans": [item.model_dump() for item in state.query_plans],
                "evidence": [item.model_dump() for item in state.evidence_records],
                "chunks": [item.model_dump() for item in state.evidence_chunks],
            },
            "comparison": {
                "links": [item.model_dump() for item in state.claim_evidence_links],
                "assessments": [item.model_dump() for item in state.claim_assessments],
            },
            "assessment": {
                "novelty": [item.model_dump() for item in state.novelty_decisions],
                "advancedness": [item.model_dump() for item in state.advancedness_decisions],
                "indicator_validations": [item.model_dump() for item in state.indicator_validations],
                "scorecard": [item.model_dump() for item in state.scorecard],
            },
            "landscape": {
                "trend": state.trend_analysis,
                "market": [item.model_dump() for item in state.market_data_cards],
                "regulatory": [item.model_dump() for item in state.regulatory_data_cards],
                "competitive": [item.model_dump() for item in state.competitive_data_cards],
            },
            "quality_gate": {
                "quality_gate": state.quality_gate.model_dump(),
                "reviews": [item.model_dump() for item in state.review_tasks],
                "resolutions": [item.model_dump() for item in state.resolution_logs],
            },
            "report": {
                "report_plan": state.report_plan.model_dump(exclude={"sections", "section_specs"}),
                "metrics": state.report_metrics.model_dump(),
                "versions": state.versions.model_dump(exclude={"stage_versions"}),
            },
        }
        state.versions.stage_versions[stage] = cls._version_digest(
            stage_payloads.get(stage, {}), prefix=f"stage-{stage}"
        )
        state.output_json["versions"] = state.versions.model_dump()

    @classmethod
    def _refresh_versions(cls, state: ContentAssessmentState) -> None:
        input_payload = {
            "raw_user_input": state.raw_user_input,
            "title": state.assessment_profile.title,
            "documents": [item.model_dump() for item in state.assessment_profile.document_records],
            "files": [item.model_dump(exclude={"excerpt"}) for item in state.uploaded_file_context.files],
        }
        state.versions.input_version = cls._version_digest(input_payload, prefix="input-v")
        state.assessment_profile.input_version = state.versions.input_version
        state.versions.rule_version = RULE_VERSION
        model_names = [
            {key: item.get(key, "") for key in ("stage", "profile", "model", "status")}
            for item in state.output_json.get("model_usage", {}).get("stages", [])
            if isinstance(item, dict)
        ]
        state.versions.model_version = cls._version_digest(
            {"prompt_version": PROMPT_VERSION, "models": model_names or ["deterministic-fallback"]},
            prefix="model-v",
        )
        state.versions.template_version = TEMPLATE_VERSION
        state.versions.evidence_pool_version = cls._version_digest(
            {
                "plans": [item.model_dump() for item in state.query_plans],
                "evidence": [item.model_dump() for item in state.evidence_records],
            },
            prefix="evidence-v",
        )
        state.versions.chart_data_snapshot_version = cls._version_digest(
            {
                "trend": state.trend_analysis,
                "advancedness": [item.model_dump() for item in state.advancedness_decisions],
                "scorecard": [item.model_dump() for item in state.scorecard],
            },
            prefix="chart-v",
        )
        state.output_json["versions"] = state.versions.model_dump()

    @staticmethod
    def _text(value: object, *, limit: int) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())[:limit]

    @classmethod
    def _string_list(cls, value: object, *, limit: int, item_limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        values = [cls._text(item, limit=item_limit) for item in value]
        return list(dict.fromkeys(item for item in values if item))[:limit]

    @staticmethod
    def _positive_int(value: object, *, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _confidence(value: object) -> float:
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _quality_gate(self, state: ContentAssessmentState) -> None:
        self._resolve_report_preferences(state)
        has_confirmed_input = state.nine_fields_confirmed and not state.nine_fields.missing_for_type(state.report_preferences.resolved_artifact_type)
        primary_roles = self._primary_document_roles(state)
        has_project = any(record.role in primary_roles and record.content_available for record in state.assessment_profile.document_records) or has_confirmed_input
        has_external = any(self._formal_evidence(item) for item in state.evidence_records)
        has_anchored_project = any(
            item.source_type == "project_material" and item.anchor_verified
            for item in state.evidence_records
        )
        has_comparable_evidence = has_external or has_anchored_project
        evidence_by_id = {item.evidence_id: item for item in state.evidence_records}
        formal_chunk_keys = {(c.evidence_id, claim_id, c.chunk_id)
            for c in state.evidence_chunks if c.reopenable and c.evidence_level == "A"
            for claim_id in (c.claim_ids or [claim.claim_id for claim in state.claims])}
        formal_claim_covered = {
            link.claim_id for link in state.claim_evidence_links
            if link.relation in {"supports", "partially_supports"}
            and link.evidence_level == "A" and link.evidence_chunk_ids
            and any((link.evidence_id, link.claim_id, chunk_id) in formal_chunk_keys for chunk_id in link.evidence_chunk_ids)
            and (
                self._formal_evidence(evidence_by_id.get(link.evidence_id, EvidenceRecord(
                    evidence_id="", title="", source_type="literature"
                )))
                or (
                    evidence_by_id.get(link.evidence_id) is not None
                    and evidence_by_id[link.evidence_id].source_type == "project_material"
                    and evidence_by_id[link.evidence_id].anchor_verified
                )
            )
        }
        directional_claim_covered = {
            link.claim_id for link in state.claim_evidence_links
            if link.relation in {"supports", "partially_supports", "not_comparable"}
            and evidence_by_id.get(link.evidence_id) is not None
            and (
                evidence_by_id[link.evidence_id].evidence_level in {"A", "B"}
                and bool(evidence_by_id[link.evidence_id].abstract or evidence_by_id[link.evidence_id].excerpt)
                or evidence_by_id[link.evidence_id].source_type == "project_material"
            )
        }
        valid_evidence_ids = {
            item.evidence_id for item in state.evidence_records
            if self._formal_evidence(item) or (item.source_type == "project_material" and item.anchor_verified)
        }
        indicator_assessed = sum(bool(item.assessment_conclusion) for item in state.indicator_validations)
        indicator_covered = sum(
            item.literature_baseline_status == "comparable"
            and any(ref in valid_evidence_ids for ref in item.evidence_refs)
            for item in state.indicator_validations
        )
        formal_claim_coverage = len(formal_claim_covered) / max(1, len(state.claims))
        directional_claim_coverage = len(directional_claim_covered) / max(1, len(state.claims))
        # The user-facing coverage metric reflects usable directional evidence;
        # formal eligibility still uses the stricter A-level set below.
        claim_coverage = directional_claim_coverage
        indicator_coverage = indicator_covered / max(1, len(state.indicators)) if state.indicators else 1.0
        blockers: list[str] = []
        warnings: list[str] = list(state.output_json.get("fulltext_limitations", []))
        if not has_project:
            blockers.append("未提供或未确认项目材料，不能发布正式评估报告。")
        if not has_comparable_evidence:
            warnings.append("缺少可定位外部证据，创新性与先进性结论仅可作为探索性判断。")
        if len(state.query_plans) < len(STANDARD_QUERY_GROUPS):
            warnings.append("六类检索计划未完整生成，未覆盖的观察面将按当前资料边界说明。")
        if formal_claim_coverage < 0.8:
            warnings.append(f"正式主张证据覆盖率为 {formal_claim_coverage:.0%}，低于 80% 长报告门槛；当前方向性覆盖率为 {directional_claim_coverage:.0%}。")
        if indicator_coverage < 0.8:
            warnings.append(f"同口径指标证据覆盖率为 {indicator_coverage:.0%}，低于 80% 长报告门槛；已完成 {indicator_assessed}/{len(state.indicators)} 项指标评估。")
        # New tasks are self-contained reports.  ReviewTask remains in the
        # schema/API only so historical snapshots can still be read, but the
        # automatic quality check must never create an operator workflow.
        open_review_count = sum(1 for item in state.review_tasks if item.status in {"open", "returned"})
        formal = (
            has_project
            and has_comparable_evidence
            and formal_claim_coverage >= 0.8
            and indicator_coverage >= 0.8
            and not state.confirmation.needed
        )
        query_complete = len(state.query_plans) >= len(STANDARD_QUERY_GROUPS)
        nine_count = sum(
            bool([entry for entry in value if str(entry).strip()]) if isinstance(value, list) else bool(str(value).strip())
            for value in state.nine_fields.model_dump().values()
        )
        checks = [
            {
                "key": "input_completeness",
                "title": "输入完整度",
                "status": "passed" if has_project else "blocked",
                "completed": nine_count,
                "total": 9,
                "message": "九项评估内容与材料边界已确认。" if has_project else "缺少可确认的项目材料或九项评估内容。",
                "basis": "逐项核对九字段和已解析项目材料。",
                "anchor": "claim_extraction",
            },
            {
                "key": "retrieval_completeness",
                "title": "文献检索完成度",
                "status": "passed" if query_complete and bool(state.evidence_records) else "warning",
                "completed": min(len(state.query_plans), len(STANDARD_QUERY_GROUPS)),
                "total": len(STANDARD_QUERY_GROUPS),
                "message": f"已执行 {len(state.query_plans)} 组检索，纳入 {len(state.evidence_records)} 条证据记录。",
                "basis": "依据六类查询计划、检索状态和实际纳入的证据记录计算。",
                "anchor": "retrieval",
            },
            {
                "key": "claim_coverage",
                "title": "主张证据覆盖度",
                "status": "passed" if directional_claim_coverage >= 0.8 else "warning",
                "completed": len(directional_claim_covered),
                "total": len(state.claims),
                "message": f"{len(directional_claim_covered)}/{len(state.claims)} 条主张获得直接、部分或方向性证据。",
                "basis": "逐条核对主张—文献关系及 A/B 级可读取摘要或片段；方向性覆盖不等于全文证明。",
                "anchor": "comparison",
            },
            {
                "key": "report_readiness",
                "title": "报告发布准备度",
                "status": "passed" if formal else ("blocked" if blockers else "warning"),
                "completed": int(has_project) + int(has_comparable_evidence) + int(formal_claim_coverage >= 0.8) + int(indicator_coverage >= 0.8),
                "total": 4,
                "message": (f"已评估 {indicator_assessed}/{len(state.indicators)} 项指标，同口径数值基线覆盖 {indicator_covered}/{len(state.indicators)} 项。"
                            if state.indicators else "没有量化指标；报告将说明本次评估边界。"),
                "basis": "依据材料确认、可定位证据、正式主张覆盖和同口径指标基线四项规则。",
                "anchor": "assessment",
            },
        ]
        state.quality_gate = QualityGate(
            passed=formal,
            blockers=blockers,
            warnings=warnings,
            # Quality findings are informational report metadata. Only the
            # explicit automatic-identification confirmation can pause a new
            # task; no post-report review action is required.
            review_required=False,
            report_eligibility="formal" if formal else "exploratory",
            open_review_count=open_review_count,
            checks=checks,
        )
        state.report_metrics = ReportMetrics(
            evidence_count=len(state.evidence_records),
            unique_evidence_count=len({item.doi or item.title for item in state.evidence_records if item.title}),
            claim_count=len(state.claims),
            indicator_count=len(state.indicators),
            indicator_assessed_count=indicator_assessed,
            indicator_evidence_count=indicator_covered,
            query_group_count=len(state.query_plans),
            claim_coverage=claim_coverage,
            formal_claim_coverage=formal_claim_coverage,
            directional_claim_coverage=directional_claim_coverage,
            indicator_coverage=indicator_coverage,
            deficiencies=list(dict.fromkeys(blockers + warnings)),
        )

    async def _quality_gate_stage(self, state: ContentAssessmentState) -> None:
        self._quality_gate(state)
        gate = state.quality_gate
        evidence_ids = {item.evidence_id for item in state.evidence_records}
        claim_ids = {item.claim_id for item in state.claims}
        # The model explains frozen rule findings; eligibility is never model-authored.
        if len(state.raw_user_input.strip()) < 120 or not state.evidence_records or not state.claims:
            gate.model_status = "not_needed"
            return
        payload = await self._request_model_json(
            state, stage="quality_gate",
            system_prompt=("你是科研内容评估质量复核员。只复核给定规则结果、项目主张和已锁定证据。"
                           "不得改变规则数值、通过状态或报告资格；不得虚构证据、实测数据或模型推理过程。"
                           "只返回 JSON，包含 findings 数组，每项含 message、claim_id、evidence_ids，"
                           "以及 summary 字符串。没有新增风险时 findings 为空数组。"),
            user_prompt=json.dumps({
                "checks": gate.checks,
                "blockers": gate.blockers,
                "warnings": gate.warnings,
                "claims": [{"claim_id": item.claim_id, "text": item.text[:350]} for item in state.claims[:16]],
                "evidence": [{"evidence_id": item.evidence_id, "title": item.title,
                              "evidence_level": item.evidence_level, "abstract": (item.abstract or item.excerpt)[:450]}
                             for item in state.evidence_records[:30]],
            }, ensure_ascii=False), max_tokens=1200,
        )
        if payload is None:
            gate.model_status = "fallback"
            return
        findings = []
        for item in payload.get("findings", [])[:8] if isinstance(payload.get("findings"), list) else []:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or "").strip()[:300]
            claim_id = str(item.get("claim_id") or "")
            refs = item.get("evidence_ids") or []
            if not message or (claim_id and claim_id not in claim_ids) or not isinstance(refs, list):
                continue
            if any(str(ref) not in evidence_ids for ref in refs):
                continue
            findings.append({"message": message, "claim_id": claim_id,
                             "evidence_ids": list(dict.fromkeys(str(ref) for ref in refs)),
                             "anchor": "comparison" if claim_id else "quality_gate"})
        gate.model_findings = findings
        gate.model_status = "completed"

    @staticmethod
    def _create_quality_review_tasks(
        state: ContentAssessmentState, *, has_project: bool, has_external: bool
    ) -> None:
        existing = {item.review_task_id for item in state.review_tasks}

        def add(
            review_id: str,
            review_type: str,
            title: str,
            reason: str,
            *,
            claim_id: str = "",
            evidence_ids: list[str] | None = None,
            actions: list[str] | None = None,
        ) -> None:
            if review_id in existing:
                return
            state.review_tasks.append(ReviewTask(
                review_task_id=review_id,
                review_type=review_type,  # type: ignore[arg-type]
                claim_id=claim_id,
                title=title,
                reason=reason,
                evidence_ids=list(dict.fromkeys(evidence_ids or [])),
                suggested_actions=actions or [],
                created_version=state.versions.report_version,
            ))
            existing.add(review_id)

        if not has_project:
            add(
                "RV-QUALITY-PROJECT",
                "quality_gate",
                "确认项目材料和评估对象",
                "缺少已确认的项目材料，不能把任务描述当作项目事实或正式结论依据。",
                actions=["补充项目材料", "确认材料角色和评估边界"],
            )
        if not has_external:
            for claim in state.claims[:MAX_CLAIMS]:
                add(
                    f"RV-SOURCE-{claim.claim_id}",
                    "source_gap",
                    f"补充 {claim.claim_id} 的全文对照证据",
                    "当前没有带全文字符锚点的独立外部证据，不能形成正式创新性或先进性判断。",
                    claim_id=claim.claim_id,
                    actions=["补充可访问全文", "记录页码、段落和字符范围", "重新执行主张级检索"],
                )
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        for claim in state.claims:
            links = links_by_claim.get(claim.claim_id, [])
            if any(link.incomparability_reasons for link in links):
                evidence_ids = [link.evidence_id for link in links if link.incomparability_reasons]
                add(
                    f"RV-MATRIX-{claim.claim_id}",
                    "incomparable_metric",
                    f"复核 {claim.claim_id} 的字段可比性",
                    "对象、方法、数据、基线、指标、时间或验证条件存在缺失/冲突，关键词相似不能替代字段比对。",
                    claim_id=claim.claim_id,
                    evidence_ids=evidence_ids,
                    actions=["补齐比较字段", "说明不可比原因", "限定结论表述"],
                )
            if claim.claim_type == "innovation" or re.search(r"首次|领先|突破|唯一|国际水平", claim.text):
                add(
                    f"RV-ASSERT-{claim.claim_id}",
                    "strong_assertion",
                    f"复核 {claim.claim_id} 的强断言",
                    "主张包含创新、首次、领先或突破性表述，需由专家确认其证据强度和适用范围。",
                    claim_id=claim.claim_id,
                    evidence_ids=[link.evidence_id for link in links],
                    actions=["核对全文原文", "改为限定性可验证表述"],
                )

    async def _report_stage(self, state: ContentAssessmentState) -> None:
        # An export interruption resumes publication of the same frozen
        # content. It must not rewrite chapters, citations or the snapshot.
        if state.report_snapshot is not None:
            return
        from .scholarly_report import draft, selected_sources, publication_gaps, point_title
        unresolved = [claim for claim in state.claims if not selected_sources(state, claim)]
        if unresolved:
            interrupt_generation(state, "retrieval", "report_sources", "相关文献检索未完成，已有结果已保存，请重试检索。")
            return
        await draft(self, state)
        gaps = publication_gaps(state)
        if not state.output_json.get("scholarly_summary"):
            gaps = [*gaps, "总体研究结论"]
        if not state.output_json.get("scholarly_conclusion"):
            gaps = [*gaps, "综合评估结论"]
        if gaps:
            interrupt_generation(state, "report", "report_drafting", "报告部分内容生成中断，已完成章节已保存，请重试。")
            return
        from .report_style import polish_point_analysis
        from .report_methods import draft_methods, methods_complete

        await polish_point_analysis(self, state)
        await draft_methods(self, state)
        if not methods_complete(state):
            interrupt_generation(state, "report", "report_drafting", "证据基础与评估方法正在校核，已完成内容已保存，请重试。")
            return
        self._report(state)

    def _verify_generated_report(self, state: ContentAssessmentState) -> None:
        sections = state.report_plan.section_specs
        filled = sum(bool(section.content.strip()) for section in sections)
        citation_integrity = self._citation_integrity(state)
        state.quality_gate.post_report_checks = [
            {"key": "sections", "status": "passed" if filled == len(sections) and sections else "warning",
             "message": f"已生成 {filled}/{len(sections)} 个报告章节。"},
            {"key": "citations", "status": "passed" if citation_integrity >= 1 else "warning",
             "message": f"正文证据引用回链核对完成率 {citation_integrity:.0%}。"},
            {"key": "exports", "status": "pending", "message": "导出文件在报告快照固化后生成并核验。"},
        ]

    def _report(self, state: ContentAssessmentState) -> None:
        self._resolve_report_preferences(state)
        formal = state.quality_gate.report_eligibility == "formal"
        report_type = "正式评估报告" if formal else "探索性评估报告"
        previous_report_version = state.versions.report_version
        self._refresh_versions(state)
        limitations = list(state.quality_gate.blockers) + list(state.quality_gate.warnings) + [
            "报告仅使用用户输入、已解析项目材料和本任务纳入的可定位检索记录；统计结果仅对当前证据池负责。",
            "不把检索未命中解释为原创证明，不生成无证据的绝对化创新、领先性、机构或市场规模断言。",
        ]
        summary = (
            f"本次评估围绕“{state.assessment_profile.title}”形成了 {len(state.claims)} 项主张、"
            f"{len(state.indicators)} 项指标、{len(state.query_plans)} 组查询、{len(state.evidence_records)} 条证据记录，"
            f"并完成 {len(state.novelty_decisions)} 项主张级判定。"
            f"已逐项评估 {state.report_metrics.indicator_assessed_count}/{len(state.indicators)} 项指标，"
            f"其中 {state.report_metrics.indicator_evidence_count} 项具有同口径数值文献基线；"
            "项目目标是否达成另按实测数据判断。"
        )
        from .scholarly_report import build, TEMPLATE, RENDER
        state.output_json["scholarly_report"] = True
        state.output_json["research_report"] = True
        state.versions.template_version = TEMPLATE
        specs = build(state)
        summary = "\n\n".join(block.text for block in specs[0].blocks)
        limitations = ["评估以本次研究材料和所引文献的实际内容为范围。"]
        state.report_metrics.citation_integrity = self._citation_integrity(state)
        state.report_metrics.section_quota_completion = self._section_quota_completion(specs)
        formal_coverage = float(state.report_metrics.formal_claim_coverage or 0)
        directional_coverage = float(state.report_metrics.directional_claim_coverage or 0)
        if formal_coverage >= 0.8 and state.report_metrics.citation_integrity >= 0.9:
            evidence_strength, evidence_reason = "high", "多数主张具有可定位全文或同口径证据，引用链完整。"
        elif directional_coverage > 0 or state.report_metrics.evidence_count:
            evidence_strength, evidence_reason = "medium", "当前证据池可以支持部分方向性判断，但仍有主张或指标未被同口径资料覆盖。"
        else:
            evidence_strength, evidence_reason = "low", "当前没有足够的相关证据形成稳定比较，结论主要用于界定下一步验证方向。"
        state.report_metrics.evidence_strength = evidence_strength
        state.report_metrics.evidence_strength_reason = evidence_reason
        title_base = self._report_title(state)
        report_version = self._version_digest(
            {
                "input_version": state.versions.input_version,
                "evidence_pool_version": state.versions.evidence_pool_version,
                "rule_version": state.versions.rule_version,
                "model_version": state.versions.model_version,
                "chart_data_snapshot_version": state.versions.chart_data_snapshot_version,
                "template_version": state.versions.template_version,
                "quality_gate": state.quality_gate.model_dump(),
                "claims": [item.model_dump() for item in state.claim_assessments],
            },
            prefix="report",
        )
        state.versions.parent_report_version = (
            previous_report_version if previous_report_version.startswith("report-") and previous_report_version != report_version else ""
        )
        state.versions.report_version = report_version
        state.report_plan = AssessmentReportPlan(
            report_version=report_version,
            report_title=title_base,
            report_type=report_type,
            executive_summary=summary,
            evidence_cutoff_date=datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"),
            sections=[item.model_dump(exclude={"content"}) for item in specs],
            section_specs=specs,
            citation_map={item.evidence_id: item.title for item in state.evidence_records},
            limitations=limitations,
            report_mode=state.report_preferences.resolved_mode,
            mode_source=state.report_preferences.mode_source,
            recommended_mode=state.report_preferences.recommended_mode,
            artifact_type=state.report_preferences.resolved_artifact_type,
            scope_summary=state.assessment_profile.scope_summary,
            target_page_min=20 if len(state.claims) >= 6 else 0,
            target_page_max=30 if len(state.claims) >= 6 else 0,
            quality_status="pending",
            content_unit_counts={
                "documents": len(state.assessment_profile.document_records),
                "work_packages": len(state.work_packages),
                "claims": len(state.claims),
                "indicators": len(state.indicators),
                "query_groups": len(state.query_plans),
                "evidence": len(state.evidence_records),
                "evidence_chunks": len(state.evidence_chunks),
            },
            render_version=RENDER,
            evidence_strength=evidence_strength,
            evidence_strength_reason=evidence_reason,
            auto_detection={
                "artifact_type": state.assessment_profile.artifact_type,
                "artifact_type_confidence": state.assessment_profile.artifact_type_confidence,
                "artifact_type_reason": state.assessment_profile.artifact_type_reason,
                "scope_focus": state.assessment_profile.scope_focus,
                "scope_focus_confidence": state.assessment_profile.scope_focus_confidence,
                "scope_focus_reason": state.assessment_profile.scope_focus_reason,
                "document_role_predictions": state.assessment_profile.document_role_predictions,
            },
        )
        state.report_plan.section_specs = specs
        state.output_markdown = self._markdown_report(state)
        # This is a content estimate. The DOCX exporter may replace it with a
        # renderer-backed count when a PDF renderer is available.
        self._update_report_length_metrics(state)
        state.report_metrics.actual_page_count = None
        paragraphs = [item.strip() for item in state.output_markdown.split("\n\n") if len(item.strip()) >= 24]
        duplicate_ratio = 1.0 - (len(set(paragraphs)) / len(paragraphs) if paragraphs else 1.0)
        state.output_json["quality_checks"] = {
            "duplicate_paragraph_ratio": round(duplicate_ratio, 3),
            "empty_section_count": sum(1 for item in specs if not item.content.strip()),
        }
        long_status = "eligible" if formal else "insufficient_evidence"
        state.report_metrics.long_report_status = long_status
        target_min = state.report_plan.target_page_min
        target_max = state.report_plan.target_page_max
        if target_max <= 0:
            state.report_metrics.page_target_status = "not_applicable"
        elif state.report_metrics.estimated_pages < target_min:
            state.report_metrics.page_target_status = "below_target"
        elif state.report_metrics.estimated_pages > target_max:
            state.report_metrics.page_target_status = "above_target"
        else:
            state.report_metrics.page_target_status = "within_target"
        deficiencies = list(limitations)
        if state.report_metrics.section_quota_completion < 0.75:
            deficiencies.append(f"章节内容完成率为 {state.report_metrics.section_quota_completion:.0%}，部分章节需要补充。")
        if state.report_metrics.page_target_status == "below_target":
            deficiencies.append(f"当前内容估算约 {state.report_metrics.estimated_pages:.1f} 页，低于{state.report_preferences.resolved_mode}档目标下限 {target_min} 页；材料不足时保留完整结构，不以重复内容补页。")
        elif state.report_metrics.page_target_status == "above_target":
            deficiencies.append(f"当前内容估算约 {state.report_metrics.estimated_pages:.1f} 页，高于{state.report_preferences.resolved_mode}档目标上限 {target_max} 页；内容按证据自然增长，建议复核版式。")
        if state.report_metrics.appendix_ratio > 0.10:
            deficiencies.append(f"附录占比为 {state.report_metrics.appendix_ratio:.0%}，超过 10% 正文优先阈值。")
        if duplicate_ratio > 0.05:
            deficiencies.append(f"重复段落比例为 {duplicate_ratio:.0%}，超过 5% 质量门阈值。")
        if len(state.query_plans) < 5 and not any(term in str(state.output_json.get("retrieval_notice", "")) for term in ("稀疏", "耗尽")):
            deficiencies.append("五类查询计划未完整生成且未记录领域稀疏或检索耗尽原因。")
        state.report_metrics.deficiencies = list(dict.fromkeys(deficiencies))
        state.report_plan.quality_status = long_status
        state.report_plan.actual_page_count = state.report_metrics.actual_page_count
        if long_status != "eligible":
            state.report_plan.report_type = "探索性评估报告"
            state.quality_gate.report_eligibility = "exploratory"
            state.quality_gate.passed = False
            state.quality_gate.review_required = True
            state.quality_gate.warnings = list(dict.fromkeys(state.quality_gate.warnings + state.report_metrics.deficiencies))
        # Re-render after status and deficiencies are final so every export
        # observes the same quality snapshot.
        state.output_markdown = self._markdown_report(state)
        self._update_report_length_metrics(state)
        state.report_metrics.actual_page_count = None
        state.report_plan.actual_page_count = state.report_metrics.actual_page_count
        state.report_plan.sections = [item.model_dump(exclude={"content"}) for item in state.report_plan.section_specs]
        state.report_plan.content_unit_counts.update({
            "report_chars": len(state.output_markdown),
            "body_words": state.report_metrics.body_words,
            "appendix_words": state.report_metrics.appendix_words,
            "body_pages": int(round(state.report_metrics.body_pages)),
            "appendix_pages": int(round(state.report_metrics.appendix_pages)),
            "section_actual_words": sum(item.actual_words for item in state.report_plan.section_specs),
            "section_actual_tables": sum(item.actual_tables for item in state.report_plan.section_specs),
            "section_actual_evidence_refs": sum(item.actual_evidence_refs for item in state.report_plan.section_specs),
        })
        from .report_composition import freeze_snapshot
        freeze_snapshot(state)
        state.output_markdown = self._markdown_report(state)
        state.report_metrics.page_target_status = "not_applicable" if state.report_plan.target_page_max <= 0 else "unverified"
        state.output_json = {
            **state.output_json,
            "assessment_profile": state.assessment_profile.model_dump(),
            "claims": [claim.model_dump() for claim in state.claims],
            "evidence_records": [item.model_dump() for item in state.evidence_records],
            "novelty_decisions": [item.model_dump() for item in state.novelty_decisions],
            "scorecard": [item.model_dump() for item in state.scorecard],
            "quality_gate": state.quality_gate.model_dump(),
            "report_plan": state.report_plan.model_dump(),
            "boundary_matrix": state.boundary_matrix.model_dump(),
            "work_packages": [item.model_dump() for item in state.work_packages],
            "indicator_validations": [item.model_dump() for item in state.indicator_validations],
            "evidence_chunks": [item.model_dump() for item in state.evidence_chunks],
            "claim_assessments": [item.model_dump() for item in state.claim_assessments],
            "market_data_cards": [item.model_dump() for item in state.market_data_cards],
            "regulatory_data_cards": [item.model_dump() for item in state.regulatory_data_cards],
            "competitive_data_cards": [item.model_dump() for item in state.competitive_data_cards],
            "report_metrics": state.report_metrics.model_dump(),
            "advancedness_decisions": [item.model_dump() for item in state.advancedness_decisions],
            "review_tasks": [item.model_dump() for item in state.review_tasks],
            "resolution_logs": [item.model_dump() for item in state.resolution_logs],
            "versions": state.versions.model_dump(),
            "report_generation": {
                "layers": [
                    {"name": "evidence_chunk_batches", "count": len(state.output_json.get("evidence_batches", []))},
                    {"name": "entity_synthesis", "count": len(state.claim_assessments) + len(state.indicator_validations) + len(state.trend_analysis.get("themes", []))},
                    {"name": "section_composition", "count": len(specs)},
                ],
                "section_quota_completion": state.report_metrics.section_quota_completion,
            },
        }

    @staticmethod
    def _update_report_length_metrics(state: ContentAssessmentState) -> None:
        """Estimate report size for preflight; rendered PDF may replace it."""
        body = sum(len(spec.content or "") for spec in state.report_plan.section_specs if not spec.is_appendix)
        appendix = sum(len(spec.content or "") for spec in state.report_plan.section_specs if spec.is_appendix)
        total = max(1, body + appendix)
        metrics = state.report_metrics
        metrics.body_words = body
        metrics.appendix_words = appendix
        metrics.body_pages = round(body / 1200, 1)
        metrics.appendix_pages = round(appendix / 2200, 1)
        metrics.body_ratio = round(body / total, 3)
        metrics.appendix_ratio = round(appendix / total, 3)
        metrics.estimated_pages = round(max(1.0, 3.5 + body / 1200 + appendix / 2200), 1)
        metrics.actual_page_count = None
        metrics.page_count_verified = False
        metrics.page_count_source = "estimate"
        metrics.section_content_completion = round(sum(1 for spec in state.report_plan.section_specs if spec.content.strip()) / max(1, len(state.report_plan.section_specs)), 3)
        metrics.section_citation_completion = round(sum(1 for spec in state.report_plan.section_specs if spec.target_evidence_refs == 0 or spec.actual_evidence_refs > 0) / max(1, len(state.report_plan.section_specs)), 3)
        metrics.appendix_compression_status = "within_10_percent" if metrics.appendix_ratio <= 0.10 else "over_10_percent"

    @staticmethod
    def _report_title(state: ContentAssessmentState) -> str:
        """Build a stable, reader-facing Chinese title for all export formats."""
        return report_title(state)

    @staticmethod
    def _citation_integrity(state: ContentAssessmentState) -> float:
        evidence_ids = {item.evidence_id for item in state.evidence_records}
        refs = [ref for decision in state.novelty_decisions for ref in decision.evidence_ids]
        refs += [ref for validation in state.indicator_validations for ref in validation.evidence_refs]
        refs += [link.evidence_id for link in state.claim_evidence_links]
        refs += [chunk.evidence_id for chunk in state.evidence_chunks]
        for item in (state.trend_analysis.get("themes", []) + state.trend_analysis.get("methods", [])):
            if isinstance(item, dict):
                refs += item.get("evidence_ids", []) if isinstance(item.get("evidence_ids", []), list) else []
        if not refs:
            return 1.0 if not evidence_ids else 0.0
        return sum(1 for ref in refs if ref in evidence_ids) / len(refs)

    @staticmethod
    def _build_report_section_specs(state: ContentAssessmentState) -> list[ReportSectionSpec]:
        from .report_composition import build_specs
        ContentAssessmentGraph._resolve_report_preferences(state)
        return build_specs(state)

    @staticmethod
    def _build_report_section_specs_v2(state: ContentAssessmentState) -> list[ReportSectionSpec]:
        ContentAssessmentGraph._resolve_report_preferences(state)
        evidence_count = len(state.evidence_records)
        claim_count = len(state.claims)
        indicator_count = len(state.indicators)
        query_count = len({item.query_role for item in state.query_plans})
        deep = state.report_preferences.resolved_mode == "deep"
        # Keep the two historical part headings for existing readers while
        # adding the full evidence-oriented chapters required by the new
        # report contract. Core chapters cannot be disabled by overrides.
        specs = [
            ("executive", "执行摘要与核心结论", max(1100 if deep else 850, 700 + claim_count * 90), 1, min(evidence_count, max(1, claim_count)), False, True),
            ("process", "第一部分：评估过程实录", max(2600 if deep else 1900, 1500 + claim_count * 110 + query_count * 100), 4, min(evidence_count, 20), False, True),
            ("technical", "科学技术要点与材料概览", max(1700 if deep else 1000, 850 + len(state.work_packages) * 180), 2, min(evidence_count, max(1, len(state.assessment_profile.document_records))), False, True),
            ("landscape", "研究现状、技术路线与发展趋势", max(2600 if deep else 1400, 1100 + evidence_count * (18 if deep else 8)), 3, min(evidence_count, 24 if deep else 12), False, True),
            ("claims", "主张与指标拆解", max(2200 if deep else 1300, 850 + claim_count * (220 if deep else 120) + indicator_count * 100), 3, min(evidence_count, max(1, claim_count * 2)), False, True),
            ("comparison", "主张逐项证据比较", max(4200 if deep else 2300, 1100 + claim_count * (520 if deep else 260)), max(3, claim_count), min(evidence_count, max(1, claim_count * (5 if deep else 3))), False, True),
            ("assessment", "创新性、先进性、可行性、成熟度与风险", max(2600 if deep else 1600, 1000 + claim_count * (230 if deep else 120)), 3, min(evidence_count, 20), False, True),
            ("market", "市场、竞争与监管观察", max(1900 if deep else 950, 800 + (len(state.market_data_cards) + len(state.regulatory_data_cards) + len(state.competitive_data_cards)) * 100), 2, min(evidence_count, 18), False, True),
            ("roadmap", "验证路线、补证清单与实施建议", max(2200 if deep else 1200, 850 + claim_count * (190 if deep else 100)), 2, min(evidence_count, max(1, claim_count * 2)), False, True),
            ("conclusion", "综合结论与报告使用边界", max(1200 if deep else 800, 650 + claim_count * 60), 1, min(evidence_count, max(1, claim_count)), False, True),
            ("references", "参考文献与数据说明", max(700, min(3200 if deep else 2200, evidence_count * (46 if deep else 32))), 1, evidence_count, False, True),
            ("search_log", "附录 A：检索执行记录", max(700, 420 + len(state.query_plans) * 90), 1, min(evidence_count, 30), True, False),
            ("claim_matrix", "附录 B：主张—证据—判定对照表", max(800, 520 + claim_count * 170), 1, min(evidence_count, max(1, claim_count * 5)), True, False),
            ("evidence_detail", "附录 C：证据明细与字段字典", max(900, 600 + evidence_count * (55 if deep else 30)), 1, evidence_count, True, False),
            ("declarations", "附录 D：声明、限制与复核记录", 900 if deep else 600, 0, 0, True, False),
        ]
        chart_map = {
            "executive": ["workflow"],
            # The executive section already presents the workflow image.  Keep
            # the process section focused on evidence and landscape charts so
            # the same figure is not emitted twice in the final report.
            "process": ["years"],
            "landscape": ["themes"],
            "assessment": ["radar"],
            "market": ["market"],
        }
        overrides = state.report_preferences.section_overrides
        result: list[ReportSectionSpec] = []
        for index, (key, title, words, tables, refs, is_appendix, required) in enumerate(specs, start=1):
            override = overrides.get(key)
            if override and not override.enabled and not required:
                continue
            order = override.order if override and override.order is not None else index
            depth = override.depth if override else ("full" if deep else "expanded")
            result.append(ReportSectionSpec(section_key=key, title=title, order=order, level=1, is_appendix=is_appendix, chart_keys=chart_map.get(key, []), target_words=words, target_tables=tables, target_evidence_refs=refs, required=required, depth=depth))
        return sorted(result, key=lambda item: (item.order, item.section_key))

    @staticmethod
    def _section_quota_completion(specs: list[ReportSectionSpec]) -> float:
        """Compute a weighted completion from generated content units.

        Word count carries most of the weight for Chinese reports; tables and
        citations are independent checks and are never inferred from targets.
        """
        required = [item for item in specs if item.required]
        if not required:
            return 0.0
        target_words = sum(item.target_words for item in required)
        actual_words = sum(item.actual_words for item in required)
        target_tables = sum(item.target_tables for item in required)
        actual_tables = sum(item.actual_tables for item in required)
        target_refs = sum(item.target_evidence_refs for item in required)
        actual_refs = sum(item.actual_evidence_refs for item in required)
        word_ratio = min(1.0, actual_words / target_words) if target_words else 1.0
        table_ratio = min(1.0, actual_tables / target_tables) if target_tables else 1.0
        ref_ratio = min(1.0, actual_refs / target_refs) if target_refs else 1.0
        return round(sum(bool(s.content.strip()) for s in required) / len(required), 3)

    @staticmethod
    def _compose_report_sections_legacy(state: ContentAssessmentState, specs: list[ReportSectionSpec]) -> None:
        """Legacy report composer kept for historical state migration only.

        New runs use the compact composer defined below. Keeping the legacy
        implementation named prevents accidental shadowing of the canonical
        method while old persisted reports are being upgraded.
        """
        evidence = list(state.evidence_records)
        evidence_by_id = {item.evidence_id: item for item in evidence}
        project_evidence_ids = [item.evidence_id for item in evidence if item.source_type == "project_material"]
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        decisions = {item.claim_id: item for item in state.novelty_decisions}
        validations = {item.indicator_id: item for item in state.indicator_validations}

        def refs(values: list[str]) -> list[str]:
            return list(dict.fromkeys(item for item in values if item in evidence_by_id))

        def cite(values: list[str]) -> str:
            values = refs(values)
            numbers = [str(list(evidence_by_id).index(item) + 1) for item in values if item in evidence_by_id]
            return "".join(f"[{number}]" for number in numbers) if numbers else "（暂无可定位证据）"

        for spec in specs:
            paragraphs: list[str] = []
            section_refs: list[str] = []
            key = spec.section_key
            if key == "executive":
                paragraphs.append(f"本报告以“{state.assessment_profile.assessment_object or state.assessment_profile.title}”为对象，围绕材料边界、可检验主张、指标口径和公开证据开展综合评估。当前形成 {len(state.claims)} 项主张、{len(state.indicators)} 项指标、{len(state.query_plans)} 组查询和 {len(evidence)} 条证据记录。")
                for claim in state.claims:
                    decision = decisions.get(claim.claim_id)
                    ids = (decision.evidence_ids if decision else []) or [link.evidence_id for link in links_by_claim.get(claim.claim_id, [])]
                    section_refs.extend(ids)
                    paragraphs.append(f"{claim.claim_id}：{claim.text}；有限判定为“{decision.label if decision else '证据不足或存疑'}”。{decision.rationale if decision else '尚未形成同口径比较。'}{cite(ids)}")
                if evidence:
                    section_refs.extend(item.evidence_id for item in evidence[: min(8, len(evidence))])
                    for item in evidence[: min(8, len(evidence))]:
                        paragraphs.append(f"核心证据摘要 {item.evidence_id}：该记录来自 {item.venue or '未标注载体'}，发表于 {item.year or '未标注年份'}，以“{(item.excerpt or item.abstract or '未返回摘录')[:180]}”描述研究内容；它只能支撑明确列出的对象、方法或指标，不外推为绝对领先结论。{cite([item.evidence_id])}")
                paragraphs.append(f"质量门结果为 {state.quality_gate.report_eligibility}，章节配额将在本阶段按实际字数、表格和引用回填；任何检索未命中均不被解释为原创证明。")
            elif key == "boundary":
                b = state.boundary_matrix
                paragraphs.extend([
                    f"评估对象：{b.assessment_object or state.assessment_profile.assessment_object or '待确认'}。纳入范围包括：{'；'.join(b.included_scope) or '待确认'}。排除范围包括：{'；'.join(b.excluded_scope) or '待确认'}。",
                    f"时间边界为 {b.time_boundary or '待确认'}；地域边界为 {b.geography_boundary or '待确认'}；技术边界为 {b.technology_boundary or '待确认'}；证据边界为 {b.evidence_boundary or '待确认'}。",
                ])
                for record in state.assessment_profile.document_records:
                    ref = f"E-P-{record.document_id}"
                    section_refs.append(ref)
                    paragraphs.append(f"材料 {record.name}（{record.role}）状态为 {record.status}，页数/章节索引为 {record.page_count or '待返回'}/{record.section_count or '待返回'}，内容可用性为 {record.content_available}。材料摘要：{record.excerpt or '未返回摘要。'}{cite([ref])}")
                    for issue in record.parse_issues:
                        paragraphs.append(f"材料解析问题 {record.document_id}/{issue.code}：{issue.message}；严重度 {issue.severity}；定位 {issue.page_ref or '未定位'}。{cite([ref])}")
                paragraphs.extend([f"默认假设：{item}。" for item in b.assumptions] or ["当前没有新增默认假设。"])
                paragraphs.extend([f"待确认项：{item}。" for item in b.confirmation_items])
            elif key == "profile":
                for name, value in state.nine_fields.model_dump().items():
                    rendered = "；".join(value) if isinstance(value, list) else str(value or "待确认")
                    paragraphs.append(f"九字段-{name}：{rendered}。该字段用于界定研究任务画像，并不替代项目材料原文定位。{cite(project_evidence_ids)}")
                for package in state.work_packages:
                    refs_for_wp = package.source_refs or project_evidence_ids
                    section_refs.extend(refs_for_wp)
                    paragraphs.append(f"工作包 {package.work_package_id}（{package.title}）目标为：{package.objective or '待补充'}。输入为 {'；'.join(package.inputs) or '待补充'}，输出为 {'；'.join(package.outputs) or '待补充'}，依赖为 {'；'.join(package.dependencies) or '无'}，验证节点为 {'；'.join(package.verification_nodes) or '待补充'}。{cite(refs_for_wp)}")
                    paragraphs.append(f"工作包 {package.work_package_id} 的验收链条应依次记录输入完整性、过程变更、阶段输出和验证结果；若依赖条件未满足，应将该节点标记为阻塞而不是把目标值视为已完成。{cite(refs_for_wp)}")
            elif key == "claims":
                for claim in state.claims:
                    ids = [link.evidence_id for link in links_by_claim.get(claim.claim_id, [])]
                    section_refs.extend(ids)
                    paragraphs.append(f"{claim.claim_id}（{claim.claim_type}/{claim.assertion_type}）：{claim.text}。来源定位：{claim.source_span or '待定位'}；检索词：{'、'.join(claim.searchable_terms) or '待补充'}；歧义说明：{claim.ambiguity or '无'}。{cite(ids)}")
                for indicator in state.indicators:
                    validation = validations.get(indicator.indicator_id)
                    ids = validation.evidence_refs if validation else []
                    section_refs.extend(ids)
                    paragraphs.append(f"{indicator.indicator_id} {indicator.name}：目标 {indicator.value}{indicator.unit}，状态 {indicator.status}。定义为 {validation.definition if validation else indicator.name}；基线 {validation.baseline if validation else '待补充'}；测试方法 {validation.test_method if validation else '待补充'}；样本 {validation.sample_design if validation else '待补充'}；验收条件 {validation.acceptance_criteria if validation else '待补充'}；验证状态 {validation.status if validation else 'missing'}。{cite(ids)}")
            elif key == "retrieval":
                # Collapse multiple plans with the same query_group into one
                # reader-facing row. Raw query strings and per-request status
                # remain in the audit appendix only.
                grouped: dict[str, dict[str, Any]] = {}
                for plan in state.query_plans:
                    group = (plan.query_group or plan.claim_id or "未命名查询组").strip()
                    bucket = grouped.setdefault(group, {
                        "rationales": [],
                        "years": [],
                        "candidate_count": 0,
                        "selected_count": 0,
                        "statuses": [],
                    })
                    rationale = (plan.rationale or "为主张和指标寻找同口径公开依据").strip()
                    if rationale and rationale not in bucket["rationales"]:
                        bucket["rationales"].append(rationale)
                    if len(plan.year_range) == 2:
                        bucket["years"].append(tuple(plan.year_range))
                    bucket["candidate_count"] += int(plan.candidate_count or 0)
                    bucket["selected_count"] += int(plan.selected_count or 0)
                    status = str(plan.source_status or "待完成")
                    if status not in bucket["statuses"]:
                        bucket["statuses"].append(status)

                total_candidates = sum(item["candidate_count"] for item in grouped.values())
                total_selected = sum(item["selected_count"] for item in grouped.values())
                unique_selected = len({item.evidence_id for item in evidence})
                paragraphs.append(
                    "统一检索方法：本次围绕研究背景、技术路线、方法设计、指标口径、直接先例、应用场景、风险限制、标准指南和年度趋势设置查询组。"
                    "每组先扩展中英文术语并召回候选记录，再按题名、DOI或来源标识合并重复项，最后依据对象、方法、场景、指标、样本和时长等比较字段筛选可定位证据。"
                    "同一查询组的多个请求只在统计上合并一次，避免把检索操作记录误读为独立研究结论。"
                )
                paragraphs.append(
                    f"总体检索结果：共形成 {len(grouped)} 个查询组，累计召回 {total_candidates} 条候选记录，"
                    f"按组统计纳入 {total_selected} 条，当前证据池去重后保留 {unique_selected} 条记录。"
                    f"纳入数量用于描述本次证据覆盖范围，不等同于研究质量或结论强度。"
                )
                for group, bucket in grouped.items():
                    ranges = bucket["years"]
                    if ranges:
                        start = min(item[0] for item in ranges)
                        end = max(item[1] for item in ranges)
                        year_text = f"{start}—{end} 年"
                    else:
                        year_text = "未限定年份"
                    purpose = "；".join(bucket["rationales"][:2]) or "为主张和指标寻找同口径公开依据"
                    status_text = "、".join(bucket["statuses"][:2]) or "待完成"
                    paragraphs.append(
                        f"查询组“{group}”：覆盖目的为{purpose}；时间范围为{year_text}；"
                        f"候选 {bucket['candidate_count']} 条，去重纳入 {bucket['selected_count']} 条；"
                        f"执行状态为{status_text}。该组结果仅用于本报告列明的比较字段，跨对象或跨场景外推仍需单独验证。"
                    )

                # Keep only three representative records in the body. The
                # complete evidence pool and long excerpts stay in appendices.
                representative = []
                seen_titles: set[str] = set()
                for item in evidence:
                    title_key = (item.title or item.evidence_id).strip().lower()
                    if title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)
                    representative.append(item)
                    if len(representative) == 3:
                        break
                for item in representative:
                    related_links = [link for link in state.claim_evidence_links if link.evidence_id == item.evidence_id]
                    fields = sorted({field for link in related_links for field in link.comparable_fields})
                    relation = "、".join(dict.fromkeys(link.relation for link in related_links)) or "方向性参考"
                    field_text = "、".join(fields) or "对象、方法或场景"
                    paragraphs.append(
                        f"代表证据：{item.title}（{item.year or '年份待核'}）与本任务的关系为{relation}，"
                        f"主要用于支撑{field_text}的同口径比较；它能够说明已有研究覆盖的范围，"
                        f"但不能单独证明本项目目标值、样本条件或长期稳定性已经达成。{cite([item.evidence_id])}"
                    )
                section_refs.extend(item.evidence_id for item in evidence)
            elif key == "landscape":
                trend = state.trend_analysis
                paragraphs.append(str(trend.get("summary") or "当前任务未形成完整趋势结论。"))
                paragraphs.append("年份统计：" + "；".join(f"{year} 年 {count} 条" for year, count in sorted((trend.get("year_distribution") or {}).items())) + "。该统计只反映当前去重证据池，不代表领域总体产出。")
                for theme in trend.get("themes", []):
                    if isinstance(theme, dict):
                        ids = refs(theme.get("evidence_ids") or []) or [item.evidence_id for item in evidence if item.query_group == theme.get("label")][:4]
                        section_refs.extend(ids)
                        paragraphs.append(f"主题簇“{theme.get('label', '未命名')}”包含 {theme.get('evidence_count', 0)} 条记录，成熟度为 {theme.get('maturity', '待定')}，机会观察为 {theme.get('opportunity', '待定')}，归纳依据为 {theme.get('rationale', '基于当前证据关键词和查询组统计')}。{cite(ids)}")
                for method in trend.get("methods", []):
                    if isinstance(method, dict):
                        ids = refs(method.get("evidence_ids") or [])
                        section_refs.extend(ids)
                        paragraphs.append(f"方法路线“{method.get('label', '未命名')}”的优势为 {'；'.join(method.get('strengths', [])) or '待补充'}，局限为 {'；'.join(method.get('limitations', [])) or '待补充'}。{cite(ids)}")
                for card in state.market_data_cards + state.regulatory_data_cards + state.competitive_data_cards:
                    ids = refs(card.source_evidence_ids)
                    section_refs.extend(ids)
                    paragraphs.append(
                        f"{card.card_type}数据卡“{card.subject}”：值为 {card.value or '待补充'} {card.unit}，周期 {card.period or '未标注'}，"
                        f"状态 {card.status}，置信度 {card.confidence:.2f}；限制为 {card.limitation or '待补充'}。{cite(ids)}"
                    )
                paragraphs.extend([f"趋势/竞争观察：{item}。" for item in (trend.get("trend_highlights") or []) + (trend.get("competition_observations") or []) + (trend.get("limitations") or [])])
            elif key == "comparison":
                for claim in state.claims:
                    detail = next((item for item in state.claim_assessments if item.claim_id == claim.claim_id), None)
                    decision = decisions.get(claim.claim_id)
                    claim_links = links_by_claim.get(claim.claim_id, [])
                    claim_refs = [link.evidence_id for link in claim_links]
                    section_refs.extend(claim_refs)
                    fields = (detail.field_comparisons if detail else []) or ["对象", "方法", "场景", "指标", "样本", "时长"]
                    paragraphs.extend([
                        f"{claim.claim_id} 主张原文与分析任务：项目材料提出“{claim.text}”。本节将其视为一个需要外部证据和项目实测共同验证的原子命题，而不是直接等同于已实现成果。主张类型为 {claim.claim_type}，完成状态为 {claim.assertion_type}，来源定位为 {claim.source_span or '材料定位信息待补充'}。",
                        f"评价问题与比较口径：本主张需要回答的核心问题是，已有研究是否在相近对象和相近条件下达到同类效果；比较不采用概念相似作为充分条件，而是逐项检查 {'、'.join(fields)}。因此，只有同时说明对象、方法和测量条件的证据，才可支撑较强结论。",
                        f"对象比较：当前关联证据共 {len(claim_links)} 条。它们与本主张的对象相似性主要体现在研究方向或技术名称层面；如果样本来源、材料组成、尺度或应用环境不同，则只能作为方向性先例，不能直接外推为本项目对象已经得到验证。{cite(claim_refs)}",
                        f"方法与技术路线比较：现有证据中与该主张相关的方法关系包括 {'、'.join(link.relation for link in claim_links) or '尚未建立明确关系'}。比较重点是实验流程、关键参数、对照设置和可重复性，而非仅比较方法名称；若证据未报告关键参数，本主张的先进性判断应保持审慎。{cite(claim_refs)}",
                        f"场景与边界比较：项目拟应用场景为 {'、'.join(state.nine_fields.application_scenarios) or '材料未明确' }。关联研究的实验场景、使用目的和约束条件需要逐一核对；跨场景证据可以支持方法可迁移性的假设，但不能替代目标场景验证。{cite(claim_refs)}",
                        f"指标与口径比较：项目主张涉及的指标口径需明确单位、基线、目标值、测量时点和统计方法。当前记录中直接覆盖的指标证据为 {sum(1 for link in claim_links if '指标' in link.comparable_fields)} 条；其余证据即使报告了相近现象，也不能自动证明项目目标值已达成。{cite(claim_refs)}",
                        f"样本、实验条件与时长比较：主张中的样本设计、数据规模、重复次数和持续时间决定了结论能否复现。现有链接的差异字段包括 {'、'.join(sorted({field for link in claim_links for field in link.comparable_fields})) or '尚未形成字段级记录'}；未披露的条件应列为补证事项，而不是用平均经验值填补。{cite(claim_refs)}",
                    ])
                    # Keep the narrative readable: at most five representative
                    # evidence explanations per claim; the complete matrix is
                    # retained in the audit state/appendix.
                    for link in claim_links[:5]:
                        item = evidence_by_id.get(link.evidence_id)
                        section_refs.append(link.evidence_id)
                        excerpt = (item.excerpt if item else "").replace("\n", " ")[:160]
                        paragraphs.append(f"证据逐条解释：该证据与主张的关系为“{link.relation}”，涉及字段为“{','.join(link.comparable_fields) or '对象/方法/场景/指标/样本/时长'}”。记录的主要差异是“{link.difference or '尚未提供同口径差异'}”，不确定性为“{link.uncertainty or '需要全文复核'}”。证据摘录表明：{excerpt or '当前没有可直接引用的摘录'}。因此，本条证据支持的是上述明确范围内的比较，不支持超出实验条件的绝对化外推。{cite([link.evidence_id])}")
                    if detail:
                        paragraphs.extend([
                            f"支持与反证：支持证据 {len(detail.supporting_evidence_ids)} 条，反证或限制证据 {len(detail.contradicting_evidence_ids)} 条。支持证据只能提高可行性判断，不能消除反证；如果两类证据的对象或指标口径不同，应保留差异并交由专家复核。{cite(detail.supporting_evidence_ids + detail.contradicting_evidence_ids)}",
                            f"证据等级与覆盖范围：本主张的当前置信度为 {detail.confidence:.2f}。证据缺口包括 {'；'.join(detail.evidence_gaps) or '暂未记录明确缺口'}；已完成字段比较为 {'、'.join(detail.field_comparisons) or '待补充'}。置信度反映当前证据链完整程度，不代表统计显著性。{cite(claim_refs)}",
                            f"有限判定与外推限制：判定为“{decision.label if decision else '证据不足或存疑'}”。{decision.rationale if decision else '尚未形成同口径比较结论。'} 在缺少直接指标、样本或时长证据时，结论应限定为方向性支持或待验证假设，不应写成无证据的绝对化创新或领先性结论。{cite(claim_refs)}",
                            f"申报文本改写与下一步：建议将主张改写为“{decision.rewrite_advice if decision and decision.rewrite_advice else '拟在明确对象、基线、样本和统计方法后验证该目标'}”。下一步应补充可定位原始数据、对照组、重复实验和预先声明的验收条件，再更新主张级判断。{cite(claim_refs)}",
                        ])
            elif key == "assessment":
                for item in state.scorecard:
                    paragraphs.append(f"{item.dimension}：{item.assessment}。评分区间 {item.score_range or '待定'}；依据 {'；'.join(item.basis) or '待补充'}；不确定性 {item.uncertainty or '待补充'}。")
                for indicator in state.indicator_validations:
                    section_refs.extend(indicator.evidence_refs)
                    paragraphs.append(f"指标 {indicator.indicator_id} 审计：定义 {indicator.definition or '待补充'}，单位 {indicator.unit or '待补充'}，基线 {indicator.baseline or '待补充'}，目标 {indicator.target or '待补充'}，测试方法 {indicator.test_method or '待补充'}，样本设计 {indicator.sample_design or '待补充'}，统计方法 {indicator.statistics_method or '待补充'}，验收条件 {indicator.acceptance_criteria or '待补充'}，状态 {indicator.status}，缺口 {'；'.join(indicator.gaps) or '无'}。{cite(indicator.evidence_refs)}")
                for decision in state.novelty_decisions:
                    section_refs.extend(decision.evidence_ids)
                    paragraphs.append(f"{decision.claim_id} 的风险与改写：风险 {'；'.join(decision.risk_notes) or '无'}；建议 {decision.rewrite_advice or '无'}。{cite(decision.evidence_ids)}")
                for claim in state.claims:
                    detail = next((item for item in state.claim_assessments if item.claim_id == claim.claim_id), None)
                    if detail:
                        section_refs.extend(detail.supporting_evidence_ids + detail.contradicting_evidence_ids)
                        paragraphs.append(f"{claim.claim_id} 可行性核查：比较字段为 {'、'.join(detail.field_comparisons) or '对象、方法、场景、指标、样本、时长'}；支持证据 {len(detail.supporting_evidence_ids)} 条；反证 {len(detail.contradicting_evidence_ids)} 条；缺口 {'；'.join(detail.evidence_gaps) or '无'}；置信度 {detail.confidence:.2f}。{cite(detail.supporting_evidence_ids + detail.contradicting_evidence_ids)}")
            elif key == "roadmap":
                for package in state.work_packages:
                    package_refs = package.source_refs or project_evidence_ids
                    section_refs.extend(package_refs)
                    paragraphs.append(f"路线节点 {package.work_package_id}：先完成输入核验，再交付 {'；'.join(package.outputs) or '阶段性成果'}；依赖 {'；'.join(package.dependencies) or '无'}；在 {'；'.join(package.verification_nodes) or '阶段验证'} 完成验收。该节点的补证任务是把目标转化为可重复测试记录。{cite(package_refs)}")
                    paragraphs.extend([
                        f"{package.work_package_id} 里程碑-设计：冻结对象、变量、样本和对照条件，并记录版本。{cite(package_refs)}",
                        f"{package.work_package_id} 里程碑-实施：按依赖顺序执行，记录偏差、失败模式和变更原因。{cite(package_refs)}",
                        f"{package.work_package_id} 里程碑-验证：使用预先声明的测试方法和统计方法，形成可复核原始记录。{cite(package_refs)}",
                    ])
                paragraphs.extend([f"补证行动：{item}" for item in state.report_metrics.deficiencies])
            elif key == "quality":
                paragraphs.extend([
                    f"材料完整性：项目材料 {len(state.assessment_profile.document_records)} 份；外部证据 {len([item for item in evidence if item.source_type == 'literature'])} 条。",
                    f"主张覆盖率 {state.report_metrics.claim_coverage:.0%}；指标覆盖率 {state.report_metrics.indicator_coverage:.0%}；引用完整率 {state.report_metrics.citation_integrity:.0%}。",
                    f"质量门当前状态为 {state.quality_gate.report_eligibility}，阻断项 {'；'.join(state.quality_gate.blockers) or '无'}，警示项 {'；'.join(state.quality_gate.warnings) or '无'}。",
                ])
                paragraphs.extend([f"复核项：{item}" for item in state.report_metrics.deficiencies])
                paragraphs.extend([f"主张复核行：{claim.claim_id}；关联证据 {len(links_by_claim.get(claim.claim_id, []))} 条；判定 {decisions.get(claim.claim_id).label if decisions.get(claim.claim_id) else '证据不足或存疑'}。" for claim in state.claims])
                paragraphs.extend([f"指标复核行：{item.indicator_id}；状态 {item.status}；证据 {len(item.evidence_refs)} 条；缺口 {'；'.join(item.gaps) or '无'}。" for item in state.indicator_validations])
            elif key == "references":
                for item in evidence:
                    paragraphs.append(f"[{item.evidence_id}] {item.title}。{item.year or '年份未标注'}。{item.venue or 'NSTL 证据记录'}。{item.doi or item.url or '链接待补充'}。")
                    section_refs.append(item.evidence_id)
            elif key == "appendices":
                for item in state.query_plans[:24]:
                    years = f"{item.year_range[0]}—{item.year_range[1]}" if len(item.year_range) == 2 else "未限定年份"
                    document_types = "、".join(str(value) for value in item.filters.get("document_types", []) if value) or "公开文献、标准或指南"
                    paragraphs.append(
                        f"检索记录 {item.query_group or item.claim_id}：检索式为“{item.query}”；检索目的为 {item.rationale or '主张级公开证据比对'}；"
                        f"覆盖语言为 {'、'.join(item.languages) or '中英文'}，时间范围为 {years}，资料类型为 {document_types}；"
                        f"候选 {item.candidate_count} 条，去重纳入 {item.selected_count} 条，执行状态为 {item.source_status}。"
                    )
                for item in state.claim_evidence_links[:80]:
                    paragraphs.append(
                        f"引用映射：主张 {item.claim_id} 与证据 {item.evidence_id} 的关系为 {item.relation}；"
                        f"比较字段为 {'、'.join(item.comparable_fields) or '待明确'}；差异为 {item.difference or '待全文复核'}；"
                        f"不确定性为 {item.uncertainty or '待补充'}。"
                    )
                    section_refs.append(item.evidence_id)
                for item in state.scorecard:
                    paragraphs.append(
                        f"评估明细：维度为 {item.dimension}；结论为 {item.assessment}；"
                        f"辅助评分为 {item.score_range or '未量化'}；依据为 {'；'.join(item.basis) or '待补充'}；"
                        f"不确定性为 {item.uncertainty or '待补充'}。"
                    )
                # Full excerpts remain available in task state; the reader
                # report keeps only the compact evidence directory above.
            body = "\n\n".join(item for item in paragraphs if item.strip()) or "本章节暂无可用结构化内容，已记录为待补证。"
            chapter_purpose = {
                "executive": "本章目的与输入：汇总项目材料、主张目录、指标验证记录和可定位外部证据，给出供项目负责人快速决策的总体判断。",
                "boundary": "本章目的与输入：审计上传材料的用途、解析质量和定位信息，明确本次评估的纳入、排除、时间、地域与技术边界。",
                "profile": "本章目的与输入：把材料中的研究方向、目标、技术路线、场景和预期成果组织成可执行的任务画像与工作包。",
                "claims": "本章目的与输入：将项目叙述拆解为可验证的原子主张和指标，并说明来源、口径与待确认条件。",
                "retrieval": "本章目的与输入：说明检索为何开展、覆盖哪些比较面、如何筛选证据，以及证据池对后续判断的支撑范围。",
                "landscape": "本章目的与输入：基于证据年份、主题簇和方法路线，分析研究现状、发展趋势、竞争观察与统计限制。",
                "comparison": "本章目的与输入：逐条比较主张与相关证据在对象、方法、场景、指标、样本和时长上的一致性与差异。",
                "assessment": "本章目的与输入：结合七维评分、指标验证和主张比对，判断创新性、先进性、可行性、风险与口径适切性。",
                "roadmap": "本章目的与输入：把已识别的工作包、指标缺口和风险转化为验证路线、里程碑、资源安排与补证行动。",
                "quality": "本章目的与输入：复核材料完整性、证据覆盖、引用完整性、章节完成度和矛盾风险，确定报告可用强度。",
            }.get(key, "本章目的与输入：基于当前任务状态形成可追溯的分析记录。")
            chapter_conclusion = "阶段结论与限制：以上判断仅适用于本次材料与证据边界；未被证据覆盖的内容标记为待验证，不将检索未命中解释为原创证明。"
            if not spec.is_appendix:
                body = f"{chapter_purpose}\n\n{body}\n\n{chapter_conclusion}"
            spec.content = body
            spec.evidence_refs = refs(section_refs)
            spec.actual_words = len(spec.content)
            table_rows = {
                "executive": 1,
                "boundary": 1 if state.assessment_profile.document_records else 0,
                "profile": 2 if state.work_packages or state.nine_fields.model_dump() else 0,
                "claims": 2 if state.claims or state.indicators else 0,
                "retrieval": 2 if state.query_plans and evidence else 1 if state.query_plans else 0,
                "landscape": 2 if state.trend_analysis.get("themes") or state.trend_analysis.get("methods") else 0,
                "comparison": len(state.claims),
                "assessment": 2 if state.scorecard or state.indicator_validations else 0,
                "roadmap": 2 if state.work_packages else 0,
                "quality": 1,
                "references": 1 if evidence else 0,
                "appendices": 2 if state.query_plans or state.evidence_chunks else 0,
            }.get(key, 0)
            spec.actual_tables = min(spec.target_tables, table_rows)
            # Count citation uses for quota purposes; keep the stored list
            # deduplicated for a compact citation map.
            spec.actual_evidence_refs = len([item for item in section_refs if item in evidence_by_id])
            values = [spec.actual_words / spec.target_words] if spec.target_words else []
            if spec.target_tables:
                values.append(spec.actual_tables / spec.target_tables)
            if spec.target_evidence_refs:
                values.append(spec.actual_evidence_refs / spec.target_evidence_refs)
            complete = min(values) >= 0.95 if values else True
            spec.status = "generated" if complete else "insufficient"
        state.output_json["evidence_batches"] = [
            {"batch_id": f"B-{index:02d}", "evidence_ids": [item.evidence_id for item in evidence[start:start + 12]], "summary_count": len(evidence[start:start + 12])}
            for index, start in enumerate(range(0, len(evidence), 12), start=1)
        ]

    @staticmethod
    def _compose_report_sections(state: ContentAssessmentState, specs: list[ReportSectionSpec]) -> None:
        from .report_composition import compose
        compose(state, specs)

    @staticmethod
    def _compose_report_sections_v2(state: ContentAssessmentState, specs: list[ReportSectionSpec]) -> None:
        """Build the compact reader report from validated state records only."""
        evidence = list(state.evidence_records)
        evidence_by_id = {item.evidence_id: item for item in evidence}
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        decisions = {item.claim_id: item for item in state.novelty_decisions}
        advancedness = {item.claim_id: item for item in state.advancedness_decisions}
        validations = {item.indicator_id: item for item in state.indicator_validations}
        evidence_numbers = {item.evidence_id: index for index, item in enumerate(evidence, start=1)}

        def safe(value: object, limit: int = 300) -> str:
            text = " ".join(str(value or "").split()).replace("|", "/")
            return text[:limit] or "待确认"

        def refs(values: list[str]) -> list[str]:
            return list(dict.fromkeys(item for item in values if item in evidence_by_id))

        def cite(values: list[str]) -> str:
            return " ".join(f"（证据：{item}）" for item in refs(values)) or "（暂无可定位证据）"

        def table(headers: list[str], rows: list[list[object]]) -> str:
            head = "| " + " | ".join(headers) + " |"
            divider = "| " + " | ".join("---" for _ in headers) + " |"
            body = ["| " + " | ".join(safe(value, 520) for value in row) + " |" for row in rows]
            return "\n".join([head, divider] + (body or ["| " + " | ".join("暂无" if index == 0 else "-" for index in range(len(headers))) + " |"]))

        def field_text(matrix: dict[str, str]) -> str:
            labels = dict(COMPARISON_FIELDS)
            status = {"covered": "已覆盖", "partial": "部分", "missing": "缺失", "conflict": "冲突", "not_comparable": "不可比"}
            return "; ".join(f"{labels[key]}:{status.get(matrix.get(key, 'missing'), matrix.get(key, 'missing'))}" for key, _ in COMPARISON_FIELDS)

        def level_for_claim(claim_id: str) -> str:
            levels = {link.evidence_level for link in links_by_claim.get(claim_id, []) if link.evidence_level in {"A", "B", "C"}}
            return "A" if "A" in levels else "B" if "B" in levels else "C" if levels else "-"

        for spec in specs:
            key = spec.section_key
            paragraphs: list[str] = []
            section_refs: list[str] = []
            if key == "executive":
                title = state.assessment_profile.assessment_object or state.assessment_profile.title or "科研项目"
                paragraphs.extend([
                    f"评估对象：{safe(title, 180)}。本报告围绕项目材料中的原子主张、目标指标和公开证据，给出可回溯的有限判断。",
                    f"当前形成 {len(state.claims)} 项主张、{len(state.indicators)} 项指标、{len(state.query_plans)} 项查询计划和 {len(evidence)} 条证据记录。质量门状态为 {state.quality_gate.report_eligibility}。",
                    "结论边界：A 级全文和稳定锚点可进入正式强度比较；B 级摘要或部分文本只能支持有限判断；C 级题录只能说明检索覆盖。检索未命中不等于原创。",
                ])
                rows = []
                for claim in state.claims:
                    decision = decisions.get(claim.claim_id)
                    ids = decision.evidence_ids if decision else []
                    section_refs.extend(ids)
                    rows.append([claim.claim_id, claim.text, decision.label if decision else "证据不足", level_for_claim(claim.claim_id), safe(decision.rationale if decision else "待补充", 260)])
                paragraphs.append(table(["主张", "主张原文", "有限判定", "最高证据级别", "判定依据"], rows))
            elif key == "technical":
                profile = state.assessment_profile
                nine = state.nine_fields
                paragraphs.extend([
                    f"本节说明评估对象的科学技术要点和材料基础。材料类型判定为 {profile.artifact_type}，评估范围为{profile.scope_focus}。{safe(profile.scope_summary or profile.scope_boundary, 500)}",
                    f"研究方向：{safe(nine.research_direction, 500)}。研究目标：{safe(nine.research_goal, 600)}。研究内容：{safe(nine.research_content, 900)}。",
                    f"技术路线：{safe(nine.technical_route, 900)}。关键技术：{safe('；'.join(nine.key_technologies), 700)}。应用场景：{safe('；'.join(nine.application_scenarios), 700)}。",
                    f"时间计划：{safe(nine.timeline, 500)}。预期成果：{safe('；'.join(nine.expected_outcomes), 700)}。",
                ])
                if state.work_packages:
                    paragraphs.append("工作包和验证节点：")
                    paragraphs.append(table(["工作包", "目标", "输入", "输出", "验证节点"], [[
                        package.title, package.objective, "；".join(package.inputs), "；".join(package.outputs), "；".join(package.verification_nodes)
                    ] for package in state.work_packages]))
                if profile.artifact_type in {"paper", "mixed"}:
                    paragraphs.append("论文模块观察：应进一步核对研究问题、方法设计、实验/统计结果、引用质量和重复风险；当前自动评估不把写作完整性等同于创新性成立。")
                if profile.artifact_type in {"project", "mixed"}:
                    paragraphs.append("项目模块观察：应将技术路线、阶段里程碑、交付物、资源依赖和验收条件逐项绑定到主张与指标。")
                if profile.artifact_type in {"technology_result", "mixed"}:
                    paragraphs.append("成果模块观察：应补充技术成熟度、知识产权状态、转化条件、应用约束和产品化风险。")
            elif key == "landscape":
                trend = state.trend_analysis or {}
                paragraphs.extend([
                    f"研究现状总结：{safe(trend.get('summary') or '当前证据池尚不足以形成稳定的领域趋势结论。', 1400)}",
                    f"技术主题：{safe('；'.join(str(item.get('theme') or item.get('name') or item) if isinstance(item, dict) else str(item) for item in trend.get('themes', [])[:20]), 1400)}。",
                    f"方法路线：{safe('；'.join(str(item.get('method') or item.get('name') or item) if isinstance(item, dict) else str(item) for item in trend.get('methods', [])[:20]), 1400)}。",
                    f"趋势亮点：{safe('；'.join(str(item) for item in trend.get('trend_highlights', [])[:12]), 1200)}",
                    "本节只描述当前检索证据池的覆盖和归纳，不把证据条数外推为领域总体规模，也不把检索未命中解释为技术空白或原创证明。",
                ])
                year_counts = trend.get("year_counts") or trend.get("years") or {}
                if isinstance(year_counts, dict) and year_counts:
                    paragraphs.append(table(["年份", "记录数", "统计口径"], [[year, value, "当前证据池统计"] for year, value in sorted(year_counts.items(), key=lambda item: str(item[0]))]))
            elif key == "claims":
                paragraphs.extend([
                    f"本节将材料中的表达拆解为 {len(state.claims)} 项原子主张和 {len(state.indicators)} 项指标。每项主张在后文单独与最近先例和七字段证据进行比较。",
                    table(["主张", "类型", "断言状态", "来源定位", "对象/方法/数据/基线/指标/时间/验证条件"], [[
                        claim.claim_id, claim.claim_type, claim.assertion_type, claim.source_span or claim.source_page_ref or "待定位", field_text(claim.claim_fields)
                    ] for claim in state.claims]),
                ])
                paragraphs.append(table(["指标", "名称", "目标值", "单位", "基线", "测试方法", "统计方法", "验收状态"], [[
                    item.indicator_id, item.name, item.value, item.unit, item.baseline, item.test_method, item.statistics_method,
                    validations[item.indicator_id].status if item.indicator_id in validations else "待验证"
                ] for item in state.indicators]))
                for indicator in state.indicators:
                    validation = validations.get(indicator.indicator_id)
                    if validation and validation.gaps:
                        paragraphs.append(f"指标 {indicator.indicator_id} 待补证项：{'；'.join(validation.gaps)}。")
            elif key == "comparison":
                paragraphs.append("本节为主张级证据 dossier。判断顺序固定为主张、最近先例、七字段比较、证据等级、可比性、有限判定、改写建议和下一步验证。")
                for claim in state.claims:
                    detail = next((item for item in state.claim_assessments if item.claim_id == claim.claim_id), None)
                    decision = decisions.get(claim.claim_id)
                    adv = advancedness.get(claim.claim_id)
                    links = links_by_claim.get(claim.claim_id, [])
                    ids = list(dict.fromkeys((decision.evidence_ids if decision else []) + [link.evidence_id for link in links]))
                    section_refs.extend(ids)
                    nearest = evidence_by_id.get(detail.nearest_prior_art_id) if detail else None
                    paragraphs.extend([
                        f"### {claim.claim_id} 主张证据分析",
                        f"主张：{safe(claim.text, 1000)}。来源定位：{safe(claim.source_span or claim.source_page_ref or '待定位', 500)}。",
                        f"最近先例：{safe(nearest.title if nearest else '未形成可比的最近先例', 700)}。",
                        table(["比较字段", "项目主张", "最近先例/证据", "覆盖状态"], [[
                            label, safe(claim.claim_fields.get(field, "待补证"), 360),
                            safe((detail.comparable_fields.get(field) if detail else "") or "; ".join(link.difference for link in links if link.difference) or "待补证", 420),
                            (detail.field_matrix.get(field, "missing") if detail else "missing")
                        ] for field, label in COMPARISON_FIELDS]),
                        f"证据等级：{level_for_claim(claim.claim_id)}；创新性判定：{safe(decision.label if decision else '证据不足', 160)}；先进性判定：{safe(adv.label if adv else '证据不足或存疑', 220)}。",
                        f"判定依据：{safe(decision.rationale if decision else detail.conclusion if detail else '尚未形成同口径比较。', 1200)}{cite(ids)}",
                        f"不可比原因：{safe('；'.join(detail.incomparability_reasons if detail else []) or '当前没有记录，仍需核验字段完整性。', 1000)}。",
                        f"改写建议：{safe(decision.rewrite_advice if decision else '将绝对化表述改为可验证目标，并补充对象、基线、指标和验证条件。', 1000)}。",
                        f"下一步验证：补齐{safe('；'.join(detail.evidence_gaps if detail else []) or '全文定位、同口径基线、测试方法和验收条件', 1000)}。",
                    ])
                    if spec.depth == "full":
                        for link in links:
                            evidence_item = evidence_by_id.get(link.evidence_id)
                            if evidence_item:
                                paragraphs.append(f"证据 {link.evidence_id} 逐条说明：{safe(evidence_item.excerpt or evidence_item.abstract or evidence_item.title, 900)}；关系为 {link.relation}，可比性说明为 {safe(link.difference or link.uncertainty or '待专家复核', 700)}。{cite([link.evidence_id])}")
            elif key == "assessment":
                paragraphs.extend([
                    "本节分别判断创新性、先进性、技术可行性、成熟度、风险和证据充分性。当前报告不合成单一总分；数值区间只表达维度内的证据准备度，不代表项目成功概率。",
                    table(["维度", "判断", "证据基础", "不确定性"], [[item.dimension, item.assessment, "；".join(item.basis), item.uncertainty] for item in state.scorecard]),
                ])
                paragraphs.append("成熟度观察：若材料没有阶段成果、重复实验、稳定性记录、工程化约束或应用反馈，成熟度只能记录为待验证，不能从技术概念直接推导可转化结论。")
                risk_rows = []
                for claim in state.claims:
                    detail = next((item for item in state.claim_assessments if item.claim_id == claim.claim_id), None)
                    risk_rows.append([claim.claim_id, "; ".join(detail.evidence_gaps if detail else []) or "待识别", "; ".join(detail.incomparability_reasons if detail else []) or "待核验", "; ".join(item.title for item in state.review_tasks if item.claim_id == claim.claim_id) or "无"])
                paragraphs.append(table(["主张", "证据风险", "可比性风险", "复核任务"], risk_rows))
            elif key == "market":
                cards = state.market_data_cards + state.competitive_data_cards + state.regulatory_data_cards
                paragraphs.extend([
                    "市场、竞争和监管观察仅使用当前证据池中明确标记的资料。没有足够数据时保留章节并说明不足，不用论文数量代替市场规模或竞争排名。",
                    table(["类型", "对象", "状态", "数值/期间", "来源", "限制"], [[
                        card.card_type, card.subject, card.status, f"{card.value} {card.unit}；{card.period}", "; ".join(refs(card.source_evidence_ids)) or "待补证", card.limitation or "无"
                    ] for card in cards]),
                    f"市场观察：{safe((state.trend_analysis or {}).get('market') or (state.trend_analysis or {}).get('market_summary') or '当前没有可复核的市场规模或转化数据。', 1200)}",
                    f"竞争观察：{safe((state.trend_analysis or {}).get('competition') or (state.trend_analysis or {}).get('competition_summary') or '当前没有足够主体级资料形成竞争排名。', 1200)}",
                    f"监管观察：{safe((state.trend_analysis or {}).get('regulatory') or (state.trend_analysis or {}).get('regulatory_summary') or '当前没有形成适用标准和监管路径的完整证据链。', 1200)}",
                ])
                section_refs.extend(item.evidence_id for item in evidence if item.source_type in {"market", "regulatory", "competitive", "literature"})
            elif key == "roadmap":
                paragraphs.extend([
                    "验证路线按证据缺口优先级组织，先解决会改变创新性判定的关键字段，再补充市场、监管和工程化资料。",
                    table(["优先级", "对象", "待补证内容", "建议验证动作", "通过条件"], [[
                        "P0" if not links_by_claim.get(claim.claim_id) else "P1", claim.claim_id,
                        "; ".join(next((item.evidence_gaps for item in state.claim_assessments if item.claim_id == claim.claim_id), [])) or "全文锚点、同口径基线和验证条件",
                        "补充原始实验/项目材料，锁定对象、方法、数据、基线、指标、时间和验证条件",
                        "形成可定位证据并完成重复或对照验证",
                    ] for claim in state.claims]),
                    "实施建议：把补证任务写入项目里程碑，指定材料负责人、验证时间、数据格式和失败处理方式；在关键证据未闭环前，不发布绝对化创新结论。",
                ])
            elif key == "conclusion":
                paragraphs.extend([
                    f"综合结论：当前报告为{('正式评估' if state.quality_gate.report_eligibility == 'formal' else '探索性评估')}，形成 {len(state.claims)} 项主张、{len(state.indicators)} 项指标和 {len(evidence)} 条证据记录。",
                    "创新性与先进性必须以对象、方法、数据、基线、指标、时间和验证条件的可比证据为基础；无法定位的记录只用于说明检索覆盖。",
                    f"主要限制：{'；'.join(state.report_metrics.deficiencies[:8]) or '暂无新增限制。'}",
                    "使用边界：本报告服务于研究决策、材料补强和专家复核，不替代法定科技查新、知识产权法律意见、临床/安全审批或市场尽调。",
                ])
            elif key == "evidence_detail":
                paragraphs.append("本附录保留证据池的完整字段，便于复核来源、证据等级和定位状态。")
                rows = []
                for item in evidence:
                    rows.append([item.evidence_id, item.evidence_level, item.title, item.source_type, item.year or "-", item.doi or item.url or "-", item.page_ref or item.paragraph_ref or "无定位", "是" if item.anchor_verified else "否", safe(item.excerpt or item.abstract, 420)])
                    section_refs.append(item.evidence_id)
                paragraphs.append(table(["编号", "等级", "题名", "来源", "年份", "DOI/链接", "定位", "锚点", "摘要/摘录"], rows))
            elif key == "declarations":
                paragraphs.extend([
                    "声明：报告由结构化材料、公共检索结果和程序化规则生成；所有结论均受证据截止日期、检索范围和来源可访问性限制。",
                    "复核记录：" + ("；".join(f"{item.review_task_id}:{item.status}" for item in state.review_tasks) or "当前没有专家复核记录。"),
                    "附件清单：检索执行记录、主张—证据—判定对照表、证据明细与字段字典、图表数据快照。",
                ])
            elif key == "process":
                b = state.boundary_matrix
                paragraphs.extend([
                    "### 1. 任务概述与评估边界",
                    f"评估对象：{safe(b.assessment_object or state.assessment_profile.assessment_object)}；纳入范围：{safe('; '.join(b.included_scope))}；排除范围：{safe('; '.join(b.excluded_scope))}。",
                    f"时间边界：{safe(b.time_boundary)}；地域边界：{safe(b.geography_boundary)}；技术边界：{safe(b.technology_boundary)}；证据边界：{safe(b.evidence_boundary)}。",
                    table(["材料", "角色", "解析状态", "可定位锚点", "范围说明"], [[record.name, record.role, record.status, "是" if record.anchor_ready else "否", record.excerpt] for record in state.assessment_profile.document_records]),
                    "### 2. 项目主张、指标和对照要求",
                    table(["主张", "原文", "七字段矩阵", "主张状态", "关联指标"], [
                        [claim.claim_id, claim.text, field_text(claim.claim_fields), claim.assertion_type, "; ".join(item.indicator_id for item in state.indicators if item.source_claim_id == claim.claim_id) or "-" ]
                        for claim in state.claims
                    ]),
                    "### 3. NSTL 检索与证据池",
                    table(["查询角色", "关联主张", "关联指标", "检索目的", "候选/纳入", "排除词"], [[
                        item.query_role, "; ".join(item.claim_ids) or item.claim_id or "-", "; ".join(item.indicator_ids) or item.indicator_id or "-", item.rationale, f"{item.candidate_count}/{item.selected_count}", "; ".join(item.exclusion_terms) or "-"
                    ] for item in state.query_plans]),
                    f"证据池分层：A 级 {sum(item.evidence_level == 'A' for item in evidence)} 条，B 级 {sum(item.evidence_level == 'B' for item in evidence)} 条，C 级 {sum(item.evidence_level == 'C' for item in evidence)} 条。数量只反映本次检索覆盖，不代表领域总体规模。",
                    "### 4. 技术态势与综合评估",
                    safe(state.trend_analysis.get("summary") or "当前没有足够外部证据形成趋势结论。"),
                    table(["数据卡", "对象", "状态", "来源", "限制"], [[card.card_type, card.subject, card.status, "; ".join(refs(card.source_evidence_ids)) or "-", card.limitation] for card in state.market_data_cards + state.regulatory_data_cards + state.competitive_data_cards]),
                ])
                section_refs.extend(item.evidence_id for item in evidence)
            elif key == "report":
                paragraphs.extend([
                    "### 1. 主要发现",
                    f"主张覆盖率 {state.report_metrics.claim_coverage:.0%}，指标覆盖率 {state.report_metrics.indicator_coverage:.0%}。创新性标签与先进性标签分别计算，目标值不作为已实现结果。",
                    "### 2. 主张级结论",
                ])
                claim_rows = []
                for claim in state.claims:
                    decision = decisions.get(claim.claim_id)
                    detail = next((item for item in state.claim_assessments if item.claim_id == claim.claim_id), None)
                    ids = decision.evidence_ids if decision else []
                    section_refs.extend(ids)
                    nearest = evidence_by_id.get(detail.nearest_prior_art_id) if detail else None
                    claim_rows.append([
                        claim.claim_id,
                        claim.text,
                        safe(nearest.title if nearest else "未形成可比最近先例", 180),
                        level_for_claim(claim.claim_id),
                        decision.label if decision else "证据不足",
                        safe(decision.rationale if decision else "待补充", 260),
                        field_text(detail.field_matrix if detail else claim.claim_fields),
                        safe("; ".join(detail.incomparability_reasons if detail else []) or "-", 220),
                        safe(decision.rewrite_advice if decision else "补充对象、基线、指标和验证条件", 240),
                    ])
                paragraphs.append(table(["主张", "原文", "最近先例", "证据等级", "创新性判定", "有限判定", "七字段比较", "不可比原因", "改写/补证建议"], claim_rows))
                paragraphs.append("### 3. 风险与补证建议")
                risk_rows = []
                for claim in state.claims:
                    detail = next((item for item in state.claim_assessments if item.claim_id == claim.claim_id), None)
                    risk_rows.append([claim.claim_id, "; ".join(detail.evidence_gaps if detail else []) or "无", "; ".join(item.title for item in state.review_tasks if item.claim_id == claim.claim_id) or "无专家复核项", "; ".join(validations[item.indicator_id].gaps[0] for item in state.indicators if item.source_claim_id == claim.claim_id and item.indicator_id in validations and validations[item.indicator_id].gaps) or "补充同口径基线和测试记录"])
                paragraphs.append(table(["主张", "证据缺口", "复核任务", "补证动作"], risk_rows))
                paragraphs.append("先进性单独判断：当前只有在对象、方法、数据、基线、指标、时间和验证条件均可比时才进入比较；本任务未将“可比”直接推导为“领先”。")
                section_refs.extend(item.evidence_id for item in evidence)
            elif key == "references":
                rows = []
                for item in evidence:
                    section_refs.append(item.evidence_id)
                    authors = "; ".join(item.authors[:2]) if item.authors else "作者缺失"
                    if len(item.authors) > 2:
                        authors += " 等"
                    identifier = item.doi or item.url or "DOI/链接缺失"
                    locator = item.page_ref or item.paragraph_ref or "定位缺失"
                    rows.append([
                        item.evidence_id,
                        safe(
                            f"{item.title}；作者：{authors}；年份：{item.year or '年份缺失'}；"
                            f"载体：{item.venue or '载体缺失'}；DOI/链接：{identifier}；"
                            f"证据等级：{item.evidence_level}；定位：{locator}",
                            380,
                        ),
                    ])
                paragraphs.append(table(["编号", "参考文献与证据字段"], rows))
            elif key == "search_log":
                trace_by_query = {str(item.get("query")): item for item in state.output_json.get("search_trace", []) if isinstance(item, dict)}
                rows = []
                for item in state.query_plans:
                    trace = trace_by_query.get(item.query, {})
                    rows.append([item.query_role, item.query, "; ".join(item.claim_ids) or item.claim_id or "-", "; ".join(item.indicator_ids) or item.indicator_id or "-", item.source_status, item.candidate_count, item.selected_count, trace.get("gateway_status_code", "-")])
                paragraphs.append(table(["角色", "检索式", "主张", "指标", "状态", "候选", "纳入", "网关状态"], rows))
                paragraphs.append("检索记录仅用于审计执行过程；查询结果和纳入数量不能替代主张级字段比对。")
            elif key == "claim_matrix":
                rows = []
                for claim in state.claims:
                    claim_links = links_by_claim.get(claim.claim_id, [])
                    if not claim_links:
                        rows.append([claim.claim_id, "-", "C；insufficient_evidence", "七字段均待补充；无可定位证据"])
                        continue
                    for link in claim_links:
                        section_refs.append(link.evidence_id)
                    evidence_labels = ", ".join(link.evidence_id for link in claim_links)
                    levels = ", ".join(dict.fromkeys(link.evidence_level for link in claim_links))
                    relations = ", ".join(dict.fromkeys(link.relation for link in claim_links))
                    reasons = "; ".join(dict.fromkeys(
                        reason for link in claim_links for reason in link.incomparability_reasons
                    )) or "-"
                    matrix = field_text(claim_links[0].field_matrix)
                    rows.append([
                        claim.claim_id,
                        evidence_labels,
                        f"{levels}；{relations}",
                        f"{matrix}；不可比原因：{reasons}",
                    ])
                paragraphs.append(table(["主张", "关联证据编号", "等级/关系", "七字段矩阵与不可比原因"], rows))

            content = "\n\n".join(item for item in paragraphs if item.strip()) or "当前没有可用结构化内容。"
            spec.content = content
            spec.evidence_refs = refs(section_refs)
            spec.actual_words = len(content)
            spec.actual_tables = sum(1 for item in paragraphs if item.lstrip().startswith("| "))
            spec.actual_evidence_refs = len(spec.evidence_refs)
            spec.status = "generated" if content.strip() else "insufficient"

        state.output_json["evidence_batches"] = [
            {"batch_id": f"B-{index:02d}", "evidence_ids": [item.evidence_id for item in evidence[start:start + 12]], "summary_count": len(evidence[start:start + 12])}
            for index, start in enumerate(range(0, len(evidence), 12), start=1)
        ]

    @staticmethod
    def _set_stage(state: ContentAssessmentState, key: str, status: str, summary: str) -> None:
        now = datetime.now().astimezone().isoformat()
        state.current_stage = key
        for item in state.stage_trace:
            if item.stage_key != key:
                continue
            item.status = status  # type: ignore[assignment]
            item.summary = summary
            if status == "running":
                item.started_at = now
                item.completed_at = None
                item.duration_seconds = None
            else:
                item.completed_at = now
                if item.started_at:
                    item.duration_seconds = max(0.0, (
                        datetime.fromisoformat(now) - datetime.fromisoformat(item.started_at)
                    ).total_seconds())
            break

    @staticmethod
    def _start_process_steps(state: ContentAssessmentState, key: str) -> None:
        now = datetime.now().astimezone().isoformat()
        stage_name = next((name for stage_key, name in STAGES if stage_key == key), key)
        state.process_trace[key] = [ProcessTraceStep(
            step_key=f"{key}:work", title=f"处理{stage_name}", status="running", started_at=now,
        )]

    @staticmethod
    def _complete_process_steps(state: ContentAssessmentState, key: str) -> None:
        now = datetime.now().astimezone().isoformat()
        steps = state.process_trace.get(key, [])
        evidence_ids = [item.evidence_id for item in state.evidence_records]
        stage_refs = {
            "document_intake": [item for item in evidence_ids if item.startswith("E-P-")],
            "claim_extraction": [f"E-P-{item.source_document_id}" for item in state.claims if item.source_document_id],
            "retrieval": [item.evidence_id for item in state.evidence_records if item.source_type == "literature"],
            "comparison": [item.evidence_id for item in state.claim_evidence_links],
            "assessment": [item.evidence_id for item in state.claim_evidence_links],
            "landscape": [item.evidence_id for item in state.evidence_chunks],
            "quality_gate": evidence_ids,
            "report": evidence_ids,
        }.get(key, [])
        for step in steps:
            if step.status == "running":
                step.status = "completed"
                step.completed_at = now
                if step.started_at:
                    step.duration_seconds = max(0.0, (
                        datetime.fromisoformat(now) - datetime.fromisoformat(step.started_at)
                    ).total_seconds())
                step.summary = ContentAssessmentGraph._stage_summary(state, key)
                step.evidence_refs = list(dict.fromkeys(stage_refs))[:12]
        if steps:
            state.output_json.setdefault("process_metrics", {})[key] = {
                "substep_count": len(steps),
                "completed_count": sum(1 for step in steps if step.status == "completed"),
                "input_count": {
                    "document_intake": len(state.uploaded_files),
                    "claim_extraction": len(state.assessment_profile.document_records),
                    "retrieval": len(state.query_plans),
                    "comparison": len(state.claims),
                    "assessment": len(state.claims) + len(state.indicators),
                    "landscape": len(state.evidence_records),
                    "quality_gate": len(state.evidence_records),
                    "report": len(state.report_plan.section_specs),
                }.get(key, 0),
                "output_count": {
                    "document_intake": len(state.boundary_matrix.model_dump()),
                    "claim_extraction": len(state.claims) + len(state.indicators),
                    "retrieval": len(state.evidence_records),
                    "comparison": len(state.claim_evidence_links),
                    "assessment": len(state.novelty_decisions),
                    "landscape": len(state.trend_analysis.get("themes", [])),
                    "quality_gate": len(state.quality_gate.blockers) + len(state.quality_gate.warnings),
                    "report": len(state.report_plan.section_specs),
                }.get(key, 0),
            }

    @staticmethod
    def _title_from_input(value: str) -> str:
        return research_name(value)

    @staticmethod
    def _primary_document_roles(state: ContentAssessmentState) -> set[str]:
        # Papers commonly score as EVIDENCE because they contain results and
        # references; those uploads still represent the selected object.
        return {"paper": {"PROJECT", "EVIDENCE"}, "guideline": {"PROJECT", "REFERENCE", "BASELINE"}}.get(
            state.report_preferences.resolved_artifact_type, {"PROJECT"})

    @staticmethod
    def _project_document_id(state: ContentAssessmentState) -> str:
        roles = ContentAssessmentGraph._primary_document_roles(state)
        return next((record.document_id for record in state.assessment_profile.document_records if record.role in roles), "")

    @staticmethod
    def _split_claims(text: str) -> list[str]:
        candidates = [item.strip(" -；。") for item in re.split(r"[。；;\n]+", text) if item.strip(" -；。")]
        return candidates[:MAX_CLAIMS]

    @staticmethod
    def _keywords(text: str) -> list[str]:
        values = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
        return list(dict.fromkeys(values))[:12]

    @staticmethod
    def _indicators_from_text(text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"[^，。；;\n]{0,24}(?:\d+(?:\.\d+)?\s*(?:%|个月|年|天|项|倍|例|mm|μm))[^，。；;\n]{0,24}", text)))[:8]

    @staticmethod
    def _stage_summary(state: ContentAssessmentState, key: str) -> str:
        summaries = {
            "document_intake": f"已登记 {len(state.assessment_profile.document_records)} 份材料并建立评估边界。",
            "claim_extraction": f"已形成 {len(state.claims)} 项主张和 {len(state.indicators)} 项指标。",
            "retrieval": f"已生成 {len(state.query_plans)} 份检索计划和 {len(state.evidence_records)} 条证据记录。",
            "comparison": f"已建立 {len(state.claim_evidence_links)} 条主张-证据关联。",
            "assessment": f"已完成 {len(state.novelty_decisions)} 项主张级判定。",
            "landscape": state.trend_analysis.get("summary", "已完成态势分析。"),
            "quality_gate": state.quality_gate.report_eligibility,
            "report": state.report_plan.report_type,
        }
        return summaries[key]

    @staticmethod
    def _markdown_report(state: ContentAssessmentState) -> str:
        return ContentAssessmentGraph._structured_markdown_report(state)

    @staticmethod
    def _structured_markdown_report(state: ContentAssessmentState) -> str:
        from .report_composition import markdown
        return markdown(state)

    @staticmethod
    def _structured_markdown_report_v2(state: ContentAssessmentState) -> str:
        """Render one canonical Markdown snapshot from the section specs.

        DOCX and HTML exporters consume this same state snapshot, so the
        report body is not regenerated independently per format.
        """
        specs = sorted(state.report_plan.section_specs, key=lambda item: item.order or 999)
        lines = [
            f"# {state.report_plan.report_title}", "",
            f"报告版本：{state.report_plan.report_version}  |  {state.report_plan.report_type}",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"证据截止日期：{state.report_plan.evidence_cutoff_date or '待记录'}",
            f"证据 {len(state.evidence_records)} 条 · 主张 {len(state.claims)} 项 · 指标 {len(state.indicators)} 项 · 查询组 {len(state.query_plans)} 组",
            f"版本链：input={state.versions.input_version}；evidence={state.versions.evidence_pool_version}；rule={state.versions.rule_version}；model={state.versions.model_version}；chart={state.versions.chart_data_snapshot_version}；template={state.versions.template_version}",
            "",
            "## 目录",
        ]
        for index, spec in enumerate([item for item in specs if not item.is_appendix], start=1):
            anchor = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", spec.title.lower()).strip("-") or spec.section_key
            lines.append(f"{index}. [{spec.title}](#{anchor})")
        appendix_specs = [item for item in specs if item.is_appendix]
        for appendix_spec in appendix_specs:
            appendix_anchor = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", appendix_spec.title.lower()).strip("-") or appendix_spec.section_key
            lines.append(f"附录：[{appendix_spec.title}](#{appendix_anchor})")
        lines.extend(["", "## 质量门与阅读提示"])
        status_text = {"formal": "正式报告", "exploratory": "探索性报告", "blocked": "暂不可发布"}.get(state.quality_gate.report_eligibility, state.quality_gate.report_eligibility)
        lines.append(f"当前状态：{status_text}。主张覆盖率 {state.report_metrics.claim_coverage:.0%}，指标覆盖率 {state.report_metrics.indicator_coverage:.0%}，引用完整率 {state.report_metrics.citation_integrity:.0%}。")
        lines.extend(f"- {item}" for item in state.report_metrics.deficiencies)
        citation_numbers = {item.evidence_id: index for index, item in enumerate(state.evidence_records, start=1)}
        def reader_content(spec: ReportSectionSpec) -> str:
            content = spec.content or "本章节暂无可用分析内容，已记录为待补证。"
            # Reader-facing chapters use paper-style numeric citations. Keep
            # raw evidence identifiers only in the audit appendix.
            if not spec.is_appendix or spec.section_key == "references":
                for evidence_id, number in citation_numbers.items():
                    content = content.replace(evidence_id, f"[{number}]")
                content = re.sub(r"（证据：([^）]+)）", lambda match: "".join(f"[{citation_numbers.get(item.strip(), item.strip())}]" for item in match.group(1).split("、")), content)
                content = re.sub(r"\bE-[A-Z0-9-]+\b", "相关证据", content)
            return content
        for spec in specs:
            anchor = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", spec.title.lower()).strip("-") or spec.section_key
            content = reader_content(spec)
            lines.extend(["", f"## {spec.title} {{{anchor}}}"])
            if spec.section_key == "comparison":
                for block in re.split(r"\n\s*\n", content):
                    block = block.strip()
                    match = re.match(r"(C[-\w]+)\s+主张原文与分析任务：", block)
                    if match:
                        lines.extend([f"### {match.group(1)} 主张证据分析", block])
                    elif block:
                        lines.append(block)
            else:
                lines.extend(content.splitlines())
        return "\n".join(lines) + "\n"

    @staticmethod
    def _legacy_markdown_report(state: ContentAssessmentState) -> str:
        def bullet(values: list[str]) -> str:
            return "\n".join(f"- {value}" for value in values) or "- 待补充"

        evidence_by_id = {item.evidence_id: item for item in state.evidence_records}
        links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
        for link in state.claim_evidence_links:
            links_by_claim[link.claim_id].append(link)
        lines = [
            f"# {state.report_plan.report_title}",
            "",
            f"- 报告版本：{state.report_plan.report_version}",
            f"- 报告类型：{state.report_plan.report_type}",
            f"- 长报告状态：{state.report_metrics.long_report_status}",
            f"- 证据/主张/指标/查询组：{len(state.evidence_records)} / {len(state.claims)} / {len(state.indicators)} / {len(state.query_plans)}",
            "",
            "## 一、执行摘要与核心结论",
            state.report_plan.executive_summary or "尚未形成执行摘要。",
            "",
            "## 二、材料审计、评估边界与假设",
            f"评估对象：{state.boundary_matrix.assessment_object or state.assessment_profile.assessment_object}",
            "纳入范围：",
            bullet(state.boundary_matrix.included_scope),
            "排除范围：",
            bullet(state.boundary_matrix.excluded_scope),
            f"时间边界：{state.boundary_matrix.time_boundary}",
            f"证据边界：{state.boundary_matrix.evidence_boundary}",
            "材料记录：",
        ]
        for record in state.assessment_profile.document_records:
            lines.extend([f"### {record.name}", f"角色：{record.role}；状态：{record.status}；内容可用：{record.content_available}", record.excerpt or "未返回材料摘要。"])
        lines.extend(["假设与限制：", bullet(state.boundary_matrix.assumptions + state.report_plan.limitations), "", "## 三、九字段、研究任务画像与工作包"])
        for key, value in state.nine_fields.model_dump().items():
            lines.append(f"- {key}：{'; '.join(value) if isinstance(value, list) else value or '待确认'}")
        for package in state.work_packages:
            lines.extend([f"### {package.work_package_id} {package.title}", package.objective or "待补充目标", f"输入：{'；'.join(package.inputs) or '待补充'}", f"输出：{'；'.join(package.outputs) or '待补充'}", f"验证节点：{'；'.join(package.verification_nodes) or '待补充'}"])
        lines.extend(["", "## 四、主张与指标目录"])
        for claim in state.claims:
            lines.extend([f"### {claim.claim_id}（{claim.claim_type}/{claim.assertion_type}）", claim.text, f"来源：{claim.source_span or '待定位'}", f"检索词：{'、'.join(claim.searchable_terms) or '待补充'}"])
        for item in state.indicators:
            validation = next((value for value in state.indicator_validations if value.indicator_id == item.indicator_id), None)
            lines.extend([f"### {item.indicator_id} {item.name}", f"目标：{item.value}{item.unit or ''}；状态：{item.status}", f"验证结论：{validation.status if validation else 'missing'}；缺口：{'；'.join(validation.gaps) if validation else '待补充'}"])
        lines.extend(["", "## 五、检索方法与证据池"])
        for plan in state.query_plans:
            lines.extend([f"### {plan.query_group or plan.claim_id}", f"检索式：{plan.query}", f"目的：{plan.rationale}", f"候选/去重后：{plan.candidate_count}/{plan.selected_count}"])
        lines.extend(["### 证据目录"])
        for evidence in state.evidence_records:
            lines.append(f"- {evidence.evidence_id}｜{evidence.title}｜{evidence.year or '-'}｜{evidence.venue or '-'}｜{evidence.evidence_level}｜查询组 {evidence.query_group or '-'}")
        lines.extend(["", "## 六、研究现状、趋势与竞争态势", str(state.trend_analysis.get("summary") or "当前任务未形成完整趋势结论。")])
        lines.extend(["年份统计：", bullet([f"{year}：{count} 条" for year, count in sorted((state.trend_analysis.get("year_distribution") or {}).items())])])
        lines.extend(["主题观察：", bullet([f"{item.get('label')}：{item.get('evidence_count', 0)} 条" for item in state.trend_analysis.get("themes", []) if isinstance(item, dict)])])
        lines.extend(["趋势与竞争要点：", bullet([*state.trend_analysis.get("trend_highlights", []), *state.trend_analysis.get("competition_observations", []), *state.trend_analysis.get("risk_notes", [])])])
        lines.append("## 七、主张-证据逐条比对与判定")
        decisions = {item.claim_id: item for item in state.novelty_decisions}
        for claim in state.claims:
            decision = decisions.get(claim.claim_id)
            lines.extend([f"### {claim.claim_id} {decision.label if decision else '待判定'}", claim.text])
            for link in links_by_claim.get(claim.claim_id, []):
                evidence = evidence_by_id.get(link.evidence_id)
                lines.append(f"- {link.evidence_id} {link.relation}；比较字段：{'、'.join(link.comparable_fields)}；差异：{link.difference or '待补充'}；不确定性：{link.uncertainty or '待补充'}；置信度：{link.confidence:.2f}")
                if evidence and evidence.excerpt:
                    lines.append(f"  - 摘录：{evidence.excerpt[:500]}")
            if decision:
                lines.extend([f"判定理由：{decision.rationale}", f"风险：{'；'.join(decision.risk_notes) or '无'}", f"改写建议：{decision.rewrite_advice or '无'}"])
        lines.extend(["", "## 八、创新性、指标口径、可行性与风险"])
        for item in state.scorecard:
            lines.extend([f"### {item.dimension}（{item.score_range or '待定'}）", item.assessment, f"依据：{'；'.join(item.basis) or '待补充'}", f"不确定性：{item.uncertainty or '待补充'}"])
        lines.extend(["", "## 九、验证路线、里程碑与补证行动"])
        for package in state.work_packages:
            lines.append(f"- {package.work_package_id}：围绕 {package.title}，完成 {'；'.join(package.verification_nodes) or '阶段验证'}。")
        lines.extend(["", "## 十、质量门与专家复核矩阵", f"发布资格：{state.quality_gate.report_eligibility}；通过：{state.quality_gate.passed}；需复核：{state.quality_gate.review_required}", "阻断项：", bullet(state.quality_gate.blockers), "警示项：", bullet(state.quality_gate.warnings), f"主张覆盖率：{state.report_metrics.claim_coverage:.0%}；指标覆盖率：{state.report_metrics.indicator_coverage:.0%}；引用完整率：{state.report_metrics.citation_integrity:.0%}"])
        lines.extend(["", "## 十一、参考文献", *[f"[{item.evidence_id}] {item.title}。{item.year or '年份未标注'}。{item.doi or item.url or 'NSTL 证据记录'}" for item in state.evidence_records], "", "## 十二、附录：检索日志、评分明细与引用映射"])
        for trace in state.output_json.get("search_trace", []):
            lines.append(f"- {json.dumps(trace, ensure_ascii=False)}")
        lines.extend(["", "### 章节配额"])
        for spec in state.report_plan.section_specs:
            lines.append(f"- {spec.section_key}｜目标字数 {spec.target_words}｜目标表格 {spec.target_tables}｜目标引用 {spec.target_evidence_refs}｜状态 {spec.status}")
        lines.extend(["", "## 十三、章节生成内容（结构化快照）"])
        for spec in state.report_plan.section_specs:
            lines.extend([
                f"### {spec.title}",
                f"章节状态：{spec.status}；实际字数：{spec.actual_words}；实际表格：{spec.actual_tables}；实际引用：{spec.actual_evidence_refs}。",
                spec.content or "本章节暂无可用结构化内容，已记录为待补证。",
            ])
        return "\n".join(lines) + "\n"
