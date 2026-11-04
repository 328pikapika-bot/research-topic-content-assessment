"""九阶段智能选题智能体。

检索统计、评分和引用映射在本地完成。新报告须完成文献来源复核后
再冻结发布；主要分析未完成时保留任务结果并提供重试入口。
"""

from __future__ import annotations

import asyncio
from app.common.retrieval.execution import retrieval_io
import hashlib
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.agents.topic_selection.schemas import (
    CandidateEvidenceLink,
    ConstraintCard,
    CrossLink,
    EvidenceRecord,
    EvidenceLock,
    ExternalEvidence,
    FreshnessSnapshot,
    FutureWorkSignal,
    GapScore,
    InputProfile,
    LandscapeSnapshot,
    QueryBranch,
    QueryPlan,
    QualityGate,
    QualityFinding,
    ReportSnapshot,
    ReportTocEntry,
    ScoreBreakdown,
    ScoreDimensionResult,
    ScoringPolicy,
    SourcePolicy,
    SourceQuery,
    TermMapEntry,
    ThemeNode,
    ThemeTree,
    TopicCandidate,
    TopicEvidenceChunk,
    TopicAnalysisPolicy,
    TopicModelCall,
    TopicProgressEvent,
    TopicReportPlan,
    TopicSelectionState,
    TopicStageTrace,
)
from app.common.retrieval.client import LiteratureSearchClient
from app.agents.topic_selection.presentation import (
    candidate_reader_report_fields,
    normalize_candidate_reader_content,
    reader_report,
    reader_prose,
    reader_text,
)
from app.common.retrieval.fulltext import LiteratureFulltextClient, FulltextIntent
from app.common.retrieval.fusion import selected_chunks_with_stats, match_source, chunk_locator
from app.common.retrieval.schemas import SearchRequest, StandardPaper
from app.common.retrieval.links import evidence_url
from app.services.deepseek_client import DeepSeekClient, DeepSeekClientError
from app.agents.topic_selection.sources import (
    CloudModelAdapter,
    NSTLAdapter,
    SourceAdapter,
    SourceExecutionResult,
    WebAdapter,
    ensure_prelock_external_access,
)


StageCallback = Callable[[TopicSelectionState], Awaitable[None]]


class TopicSelectionAgent:
    """智能选题九阶段流程，支持无模型/无检索服务的确定性降级。"""

    STAGES: tuple[tuple[str, str], ...] = (
        ("input_modeling", "输入建模与边界确认"),
        ("theme_decomposition", "主题拆解与术语映射"),
        ("retrieval_evidence", "三路查询与证据池构建"),
        ("landscape_trend", "研究全景与时效趋势分析"),
        ("crosslink_gap", "交叉机会与研究空白"),
        ("candidate_generation", "候选选题生成"),
        ("scoring_ranking", "候选研判与验证编排"),
        ("quality_gate", "自动质量检查"),
        ("report_publish", "正式报告生成与发布"),
    )

    STAGE_ACTIVITY: dict[str, str] = {
        "input_modeling": "正在解析研究问题、材料与任务边界",
        "theme_decomposition": "正在拆分子主题并建立术语映射",
        "retrieval_evidence": "正在执行检索分支并构建证据池",
        "landscape_trend": "正在统计研究全景与时效趋势",
        "crosslink_gap": "正在核验交叉关系与候选研究空白",
        "candidate_generation": "正在生成可验证的候选选题",
        "scoring_ranking": "正在研判候选方向的证据覆盖、可行性与验证优先级",
        "quality_gate": "正在检查引用回链与发布条件",
        "report_publish": "正在固化快照并装配正式报告",
    }

    def __init__(
        self,
        *,
        retrieval_client: LiteratureSearchClient | None = None,
        fulltext_client: LiteratureFulltextClient | None = None,
        model_client: DeepSeekClient | None = None,
        nstl_adapter: SourceAdapter | None = None,
        web_adapter: SourceAdapter | None = None,
        cloud_adapter: SourceAdapter | None = None,
    ) -> None:
        self.retrieval_client = retrieval_client or LiteratureSearchClient()
        self.fulltext_client = fulltext_client or LiteratureFulltextClient()
        self.model_client = model_client
        self.nstl_adapter = nstl_adapter or NSTLAdapter(self.retrieval_client)
        self.web_adapter = web_adapter or WebAdapter()
        self.cloud_adapter = cloud_adapter or CloudModelAdapter(self.model_client)

    def create_initial_state(
        self,
        raw_user_input: str,
        *,
        topic_title: str = "",
        uploaded_files: list[dict[str, Any]] | None = None,
        constraints: list[str] | None = None,
        exclusions: list[str] | None = None,
        language_preference: str = "mixed",
        year_range: list[int] | None = None,
        enable_web: bool = False,
        enable_cloud_model: bool = False,
        sensitivity_level: str = "internal",
        freshness_days: int = 365,
        candidate_count: int = 3,
        output_preference: str | None = None,
        evidence_cutoff_at: str = "",
        source_policy: SourcePolicy | None = None,
        scoring_objective: str = "balanced",
        scoring_weight_overrides: dict[str, float] | None = None,
        analysis_mode: str = "deep",
    ) -> TopicSelectionState:
        text = str(raw_user_input or "").strip()
        years = list(year_range or [])
        profile = InputProfile(
            title=(topic_title or self._title_from_text(text)),
            research_question=text,
            research_object=text,
            time_range=years,
            time_range_source="user" if years else "unspecified",
            languages=self._languages(language_preference),
            resource_constraints=list(constraints or []),
            exclusions=list(exclusions or []),
            documents=[self._document_from_upload(item) for item in (uploaded_files or [])],
            sensitivity_level=(source_policy.sensitivity_level if source_policy else (sensitivity_level if sensitivity_level in {"public", "internal", "confidential", "secret"} else "internal")),
            freshness_requirement_days=(source_policy.freshness_days if source_policy else freshness_days),
            desired_candidate_count=5 if output_preference else candidate_count,
            output_preference=output_preference,
            evidence_cutoff_at=(source_policy.evidence_cutoff_at if source_policy else evidence_cutoff_at),
        )
        configured_policy = source_policy or SourcePolicy(
            allow_web=bool(enable_web),
            allow_cloud_model=bool(enable_cloud_model),
            sensitivity_level=profile.sensitivity_level,
            freshness_days=freshness_days,
            evidence_cutoff_at=evidence_cutoff_at,
        )
        trace = [TopicStageTrace(stage_key=key, stage_name=name) for key, name in self.STAGES]
        state = TopicSelectionState(
            raw_user_input=text,
            input_profile=profile,
            stage_trace=trace,
            uploaded_files=[dict(item) for item in (uploaded_files or [])],
            source_policy=configured_policy,
            scoring_policy=self._build_scoring_policy(scoring_objective, scoring_weight_overrides or {}),
            task_profile={
                "language_preference": language_preference,
                "created_at": datetime.now().isoformat(),
                "explicit_topic_title": bool(str(topic_title or "").strip()),
                "concise_title_generated": False,
            },
            versions={"model_version": ""},
            analysis_policy=TopicAnalysisPolicy(
                mode="fast" if analysis_mode == "fast" else "deep",
                model_enabled=analysis_mode != "fast" and profile.sensitivity_level not in {"confidential", "secret"},
            ),
        )
        state.checkpoint_id = f"topic-{uuid4().hex[:12]}"
        return state

    async def run(self, state: TopicSelectionState, *, on_stage_update: StageCallback | None = None) -> TopicSelectionState:
        start_index = self._resume_index(state)
        handlers = {
            "input_modeling": self._input_modeling,
            "theme_decomposition": self._theme_decomposition,
            "retrieval_evidence": self._retrieval_evidence,
            "landscape_trend": self._landscape_trend,
            "crosslink_gap": self._crosslink_gap,
            "candidate_generation": self._candidate_generation,
            "scoring_ranking": self._scoring_ranking,
            "quality_gate": self._quality_gate,
            "report_publish": self._report_publish,
        }
        for index, (key, _) in enumerate(self.STAGES):
            if index < start_index:
                continue
            recovery = state.output_json.get("generation_recovery", {})
            if recovery and key not in recovery.get("pending_stages", [key]) and state.stage_trace[index].status == "completed":
                continue
            trace = state.stage_trace[index]
            previous_duration = float(trace.duration_seconds or 0) if recovery else 0.0
            trace.prior_duration_seconds = previous_duration
            trace.status = "running"
            trace.started_at = datetime.now().isoformat()
            trace.completed_at = None
            trace.duration_seconds = previous_duration or None
            state.current_stage = key
            await self._emit_progress(
                state,
                on_stage_update,
                stage_key=key,
                step_key="stage_start",
                title=self.STAGE_ACTIVITY[key],
                message="阶段已开始，后续只展示可审计的执行动作与产物。",
                progress_percent=0,
            )
            try:
                await handlers[key](state, on_stage_update)
                if state.confirmation.needed:
                    trace.status = "blocked"
                    trace.completed_at = datetime.now().isoformat()
                    trace.duration_seconds = previous_duration + max(0.0, (datetime.fromisoformat(trace.completed_at) - datetime.fromisoformat(trace.started_at)).total_seconds())
                    await self._notify(state, on_stage_update)
                    return state
                trace.status = "completed"
                trace.summary = self._stage_summary(state, key)
                trace.evidence_refs = self._stage_evidence_refs(state, key)
            except asyncio.CancelledError:
                trace.completed_at = datetime.now().isoformat()
                trace.duration_seconds = previous_duration + max(0.0, (datetime.fromisoformat(trace.completed_at) - datetime.fromisoformat(trace.started_at)).total_seconds())
                trace.status = 'blocked'
                raise
            except Exception as exc:  # pragma: no cover - defensive boundary for external services
                from .recovery import GenerationRecoveryRequired
                if isinstance(exc, GenerationRecoveryRequired):
                    trace.status = "blocked"
                    trace.completed_at = datetime.now().isoformat()
                    trace.duration_seconds = previous_duration + max(0.0, (datetime.fromisoformat(trace.completed_at) - datetime.fromisoformat(trace.started_at)).total_seconds())
                    await self._notify(state, on_stage_update)
                    raise
                trace.status = "blocked"
                trace.summary = "当前环节已保存，等待自动恢复。"
                state.process_trace.setdefault(key, []).append({"level": "error", "message": str(exc)})
                await self._emit_progress(
                    state,
                    on_stage_update,
                    stage_key=key,
                    step_key="stage_failed",
                    kind="warning",
                    status="failed",
                    title="阶段执行出现异常",
                    message=str(exc),
                    progress_percent=trace.progress_percent,
                )
                trace.completed_at = datetime.now().isoformat()
                trace.duration_seconds = previous_duration + max(0.0, (datetime.fromisoformat(trace.completed_at) - datetime.fromisoformat(trace.started_at)).total_seconds())
                raise
            trace.completed_at = datetime.now().isoformat()
            if trace.started_at:
                trace.duration_seconds = previous_duration + max(0.0, (datetime.fromisoformat(trace.completed_at) - datetime.fromisoformat(trace.started_at)).total_seconds())
            total_ms = round((trace.duration_seconds or 0.0) * 1000)
            model_ms = trace.model_duration_ms or 0
            external_ms = trace.external_io_duration_ms or 0
            trace.deterministic_duration_ms = max(0, total_ms - model_ms - external_ms)
            if trace.model_status == "completed":
                trace.execution_mode = "mixed" if trace.deterministic_duration_ms else "model"
            elif trace.model_status == "degraded":
                trace.execution_mode = "deterministic_fallback"
                trace.fallback_reason = trace.degradation_reason or "model_unavailable"
            elif trace.model_status == "disabled":
                trace.execution_mode = "disabled"
                trace.fallback_reason = trace.degradation_reason
            else:
                trace.execution_mode = "deterministic"
            state.checkpoint_revision += 1
            state.versions.stage_versions[key] = f"{key}-v1-r{state.checkpoint_revision}"
            if recovery and key in recovery.get("pending_stages", []):
                recovery["pending_stages"].remove(key)
            await self._emit_progress(
                state,
                on_stage_update,
                stage_key=key,
                step_key="stage_complete",
                kind="artifact",
                status="completed" if trace.status == "completed" else "failed",
                title=f"{trace.stage_name}{'已完成' if trace.status == 'completed' else '已降级完成'}",
                message=trace.summary,
                progress_percent=100 if trace.status == "completed" else trace.progress_percent,
                metrics=trace.metrics,
                artifact_refs=trace.evidence_refs,
            )
        state.current_stage = "completed"
        state.active_progress = None
        await self._notify(state, on_stage_update)
        return state

    async def _input_modeling(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        profile = state.input_profile
        profile.missing_fields = []
        if len(profile.research_question.strip()) < 8:
            profile.missing_fields.append("研究对象或研究问题过短")
        if not profile.title:
            profile.title = self._title_from_text(state.raw_user_input)
        state.constraint_card = ConstraintCard(
            boundary_text=profile.research_question,
            must_include=[profile.research_object] if profile.research_object else [],
            must_exclude=profile.exclusions,
            resource_limits=profile.resource_constraints,
            assumptions=profile.assumptions,
            unresolved=list(profile.missing_fields),
            confirmed=not profile.missing_fields,
        )
        state.term_map = self._build_term_map(state.raw_user_input)
        await self._emit_progress(
            state, callback, stage_key="input_modeling", step_key="boundary_parsed",
            kind="metric", title="研究边界与材料解析完成",
            message="已识别输入边界、材料状态和基础术语。", progress_percent=55,
            metrics={"材料": len(profile.documents), "术语": len(state.term_map), "待确认": len(profile.missing_fields)},
        )
        policy = state.source_policy
        policy.sensitivity_level = profile.sensitivity_level
        policy.freshness_days = profile.freshness_requirement_days
        policy.evidence_cutoff_at = profile.evidence_cutoff_at or policy.evidence_cutoff_at or datetime.now().isoformat()
        profile.evidence_cutoff_at = policy.evidence_cutoff_at
        if profile.sensitivity_level in {"confidential", "secret"}:
            if policy.allow_web or policy.allow_cloud_model:
                state.process_trace.setdefault("input_modeling", []).append({
                    "level": "warning", "message": "保密等级禁止外发，已关闭联网和云端模型查询。",
                })
            policy.allow_web = False
            policy.allow_cloud_model = False
            state.analysis_policy.model_enabled = False
        model_payload = await self._stage_model_json(
            state, callback,
            stage_key="input_modeling", prompt_version="topic-input-v3",
            purpose="研究问题、边界与约束分析",
            payload={
                "task": self._redact_model_text(state.raw_user_input, 3000),
                "title": self._redact_model_text(profile.title, 300),
                "time_range": profile.time_range,
                "languages": profile.languages,
                "constraints": profile.resource_constraints,
                "exclusions": profile.exclusions,
                "materials": self._model_materials(state),
                "output_schema": {"concise_title": "10-24个中文字符的概括性标题", "boundary": "string", "research_question": "string", "missing_fields": ["string"], "terms": [{"preferred": "string", "alternatives": ["string"], "english_terms": ["string"]}]},
            },
            expected_keys={"boundary", "research_question", "terms"},
        )
        if model_payload:
            if not state.task_profile.get("explicit_topic_title"):
                concise_title = self._normalize_concise_title(str(model_payload.get("concise_title") or ""))
                if concise_title:
                    profile.title = concise_title
                    state.task_profile["concise_title_generated"] = True
            boundary = str(model_payload.get("boundary") or "").strip()
            question = str(model_payload.get("research_question") or "").strip()
            if boundary:
                state.constraint_card.boundary_text = boundary[:1200]
            if question:
                profile.research_question = question[:1200]
            model_missing = [str(item)[:160] for item in model_payload.get("missing_fields", []) if str(item).strip()]
            profile.missing_fields = list(dict.fromkeys([*profile.missing_fields, *model_missing]))[:12]
            state.constraint_card.unresolved = list(profile.missing_fields)
            for item in model_payload.get("terms", [])[:12]:
                if not isinstance(item, dict) or not str(item.get("preferred") or "").strip():
                    continue
                preferred = str(item["preferred"]).strip()[:120]
                if any(term.preferred == preferred for term in state.term_map):
                    continue
                state.term_map.append(TermMapEntry(
                    term_id=f"TM-{len(state.term_map) + 1:03d}", preferred=preferred,
                    alternatives=[str(value)[:120] for value in item.get("alternatives", []) if str(value).strip()][:8],
                    english_terms=[str(value)[:120] for value in item.get("english_terms", []) if str(value).strip()][:8],
                ))
        state.versions.input_version = self._version("input", state.raw_user_input)
        state.process_trace.setdefault("input_modeling", []).append({
            "missing_fields": profile.missing_fields,
            "sensitivity_level": profile.sensitivity_level,
            "external_query_enabled": {"web": policy.allow_web, "cloud_model": policy.allow_cloud_model},
            "uploaded_materials_excluded_from_external_requests": True,
        })
        await self._emit_progress(
            state, callback, stage_key="input_modeling", step_key="source_policy_ready",
            kind="artifact", title="数据与来源策略已确定",
            message="已根据材料密级确定可用信息来源，上传材料正文不会进入外部查询。", progress_percent=90,
            metrics={"时间范围": "-".join(map(str, profile.time_range)), "语言": "/".join(profile.languages)},
        )

    async def _theme_decomposition(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        terms = [item.preferred for item in state.term_map]
        focus_terms = terms[:8] or [state.input_profile.title or state.raw_user_input[:40]]
        profile = state.input_profile
        # Build a small task-specific draft when the model is unavailable.  These
        # are derived from the user's actual object/question/method/scenario, not
        # a fixed eight-axis checklist.
        draft_parts = [
            (profile.research_object or profile.title or state.raw_user_input[:50], [profile.research_object, *focus_terms[:3]], "研究对象"),
            (profile.research_question, [profile.research_question, *focus_terms[:3]], "科学问题"),
            (profile.method_paradigm, [profile.method_paradigm, *focus_terms[1:5]], "方法路线"),
            (profile.application_scenario, [profile.application_scenario, *focus_terms[2:6]], "应用边界"),
        ]
        nodes = []
        for label, seeds, kind in draft_parts:
            label = str(label or "").strip()
            if not label or any(node.label == label for node in nodes):
                continue
            nodes.append(ThemeNode(
                theme_id=f"T{len(nodes) + 1}", label=label[:120],
                boundary=f"聚焦{label[:80]}，遵循任务边界：{state.constraint_card.boundary_text or '以用户给定研究范围为准'}。",
                seed_terms=list(dict.fromkeys(str(value).strip() for value in seeds if str(value or '').strip()))[:8] or [label],
                uncertainty=f"{kind}方向由任务输入提取，需结合检索证据确认。",
                research_object=profile.research_object or focus_terms[0],
                research_question=profile.research_question or state.raw_user_input,
                method_boundary=profile.method_paradigm or "比较现有方法与对照结果，不预设性能增益",
                application_scenario=profile.application_scenario or state.constraint_card.boundary_text,
            ))
        if not nodes:
            nodes = [ThemeNode(theme_id="T1", label=state.raw_user_input[:120], boundary=state.constraint_card.boundary_text or "以用户给定研究范围为准", seed_terms=focus_terms[:8])]
        state.theme_tree = ThemeTree(
            root=state.input_profile.title or state.raw_user_input[:60],
            nodes=nodes,
            edges=[{"source": "root", "target": node.theme_id} for node in nodes],
            coverage_score=min(1.0, len(nodes) / 3),
            status="exploratory",
        )
        await self._emit_progress(
            state, callback, stage_key="theme_decomposition", step_key="theme_tree_built",
            kind="artifact", title="主题树已形成",
            message="已将研究方向拆分为可独立检索的子主题。", progress_percent=70,
            metrics={"主题": len(nodes), "术语": len(state.term_map), "覆盖度": f"{state.theme_tree.coverage_score * 100:.0f}%"},
            artifact_refs=[node.theme_id for node in nodes],
        )
        model_payload = await self._stage_model_json(
            state, callback,
            stage_key="theme_decomposition", prompt_version="topic-themes-v3",
            purpose="主题树、领域关系与术语映射分析",
            payload={
                "research_question": state.input_profile.research_question,
                "boundary": state.constraint_card.boundary_text,
                "terms": [item.model_dump() for item in state.term_map[:20]],
                "draft_themes": [item.model_dump() for item in nodes],
                "input_profile": profile.model_dump(),
                "materials": [{"name": item.name, "excerpt": item.excerpt[:1500]} for item in profile.documents[:8]],
                "instruction": "按输入中的独立科学问题拆解可分别检索的主题，通常2至5个，最多8个；窄问题可以只有1个，不补齐数量。不同问题不能合并为一个复述整个任务的宽泛标题，也不要把同一问题的对象、方法、场景机械拆开。每个主题写明对应的原始问题与检索种子。学科数与主题数无固定对应，一个主题也可以涉及多个学科。检索发生在下一阶段，不得虚构文献支持。关系索引用1起始序号或T1格式。",
                "output_schema": {"themes": [{"label": "任务相关的具体子方向", "boundary": "string", "research_object": "string", "research_question": "string", "method_boundary": "string", "application_scenario": "string", "seed_terms": ["string"], "discipline_tags": ["string"], "uncertainty": "string"}], "relations": [{"source": "T1", "target": "T2", "relation": "string"}]},
            },
            expected_keys={"themes"},
        )
        # A rich multi-question input can be collapsed into a single restatement
        # by the first response. Review once, without enforcing a theme quota.
        proposed = model_payload.get("themes", []) if isinstance(model_payload, dict) else []
        question_text = state.raw_user_input or profile.research_question
        if (len(proposed) == 1 and len(question_text) >= 180
                and len(re.split(r"[。；;？?]", question_text)) >= 3
                and state.analysis_policy.mode == "deep" and state.analysis_policy.model_enabled):
            reviewed = await self._stage_model_json(
                state, callback, stage_key="theme_decomposition", prompt_version="topic-themes-coverage-v1",
                purpose="复核独立研究问题与主题覆盖",
                payload={"original_input": question_text, "input_profile": profile.model_dump(), "previous_themes": proposed,
                         "instruction": "检查当前单一主题是否合并了多个可独立检索的科学问题。若是，按原文问题拆分，写具体名称、边界、seed_terms与discipline_tags；若确为一个窄问题则保留1个，不能凑数。只使用输入中的对象与问题，不增加通用主题。返回themes和relations数组。",
                         "output_schema": {"themes": [{"label": "具体研究问题", "research_question": "原文问题", "boundary": "范围", "seed_terms": ["检索词"], "discipline_tags": ["学科"]}], "relations": []}},
                expected_keys={"themes"},
            )
            if isinstance(reviewed, dict) and any(isinstance(item, dict) and item.get("label") for item in reviewed.get("themes", [])):
                model_payload = reviewed
            elif model_payload:
                state.stage_artifacts.setdefault("theme_decomposition", {})["analysis"] = model_payload
        if isinstance(model_payload, dict) and isinstance(model_payload.get("themes"), list):
            model_nodes = []
            original_ids: dict[str, str] = {}
            for index, item in enumerate(model_payload["themes"][:8], start=1):
                if not isinstance(item, dict) or not str(item.get("label", "")).strip():
                    continue
                label = str(item["label"]).strip()[:120]
                if any(node.label == label for node in model_nodes):
                    continue
                original_ids[f"T{index}"] = f"T{len(model_nodes) + 1}"
                model_nodes.append(ThemeNode(
                    theme_id=original_ids[f"T{index}"], label=label,
                    seed_terms=[str(value)[:120] for value in item.get("seed_terms", []) if str(value).strip()][:8] or [label],
                    boundary=str(item.get("boundary", ""))[:500],
                    discipline_tags=[str(value)[:80] for value in item.get("discipline_tags", []) if str(value).strip()][:8],
                    uncertainty=str(item.get("uncertainty", ""))[:300],
                    research_object=str(item.get("research_object") or profile.research_object)[:300],
                    research_question=str(item.get("research_question") or item.get("boundary") or profile.research_question)[:500],
                    method_boundary=str(item.get("method_boundary") or profile.method_paradigm)[:500],
                    application_scenario=str(item.get("application_scenario") or profile.application_scenario)[:300],
                ))
            nodes = model_nodes or state.theme_tree.nodes
            for index, node in enumerate(nodes[:8], start=1):
                node.theme_id = f"T{index}"
            nodes = nodes[:8]
            state.theme_tree.nodes = nodes
            # Keep model-supplied semantic relations where both endpoints are
            # valid; otherwise retain only the root-to-node hierarchy.
            relations = []
            raw_relations = model_payload.get("relations") if isinstance(model_payload.get("relations"), list) else []
            for relation in raw_relations:
                if not isinstance(relation, dict):
                    continue
                try:
                    source = f"T{int(relation.get('source'))}" if str(relation.get('source')).isdigit() else str(relation.get('source'))
                    target = f"T{int(relation.get('target'))}" if str(relation.get('target')).isdigit() else str(relation.get('target'))
                except (TypeError, ValueError):
                    continue
                source = original_ids.get(source, "")
                target = original_ids.get(target, "")
                if source in {node.theme_id for node in nodes} and target in {node.theme_id for node in nodes} and source != target:
                    relations.append({"source": source, "target": target, "relation": str(relation.get("relation") or "主题关系需结合证据确认")[:240]})
            state.theme_tree.edges = [{"source": "root", "target": node.theme_id} for node in nodes] + relations
        from .discovery import concise_theme
        for node in nodes:
            node.display_label = concise_theme(node)
        state.theme_tree.display_root = self._normalize_concise_title(state.input_profile.title)
        state.versions.theme_tree_version = self._version("theme", "|".join(node.label for node in nodes))
        await self._emit_progress(
            state, callback, stage_key="theme_decomposition", step_key="term_mapping_ready",
            kind="metric", title="术语映射与检索种子已就绪",
            message="主题边界、种子词和术语版本已经固化。", progress_percent=92,
            metrics={"主题版本": state.versions.theme_tree_version, "检索种子": sum(len(node.seed_terms) for node in nodes)},
        )

    async def _retrieval_evidence(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        if state.evidence_lock.locked:
            if state.resume_from_stage != "retrieval_evidence":
                raise RuntimeError("证据池已锁定；只有显式回退到检索阶段才能创建新版本")
            # A retry is a new evidence-pool version. It must explicitly reopen
            # the pre-lock phase before any source adapter can be called.
            state.process_trace.setdefault("retrieval_evidence", []).append({
                "level": "warning", "message": "重新执行检索，旧证据锁定版本将被替换。",
            })
            state.evidence_lock = EvidenceLock()
            state.external_evidence = []
            state.evidence_chunks = []
            state.model_insights = []
            state.source_queries = []
        branches: list[QueryBranch] = []
        for index, node in enumerate(state.theme_tree.nodes[:8], start=1):
            correction_terms = re.findall(r"[\u4e00-\u9fffA-Za-z0-9-]{2,30}", state.correction_context.reason)[:8]
            query = " ".join(dict.fromkeys([node.label, *node.seed_terms, *correction_terms]))
            branches.append(QueryBranch(
                branch_id=f"Q{index}", theme_id=node.theme_id, purpose="主题召回与证据覆盖", query=query,
                languages=state.input_profile.languages, year_range=state.input_profile.time_range,
                synonyms=node.seed_terms, exclusions=state.input_profile.exclusions,
                paper_num=20,
            ))
        model_payload = await self._stage_model_json(
            state, callback,
            stage_key="retrieval_evidence", prompt_version="topic-retrieval-v2",
            purpose="检索分支与查新词策略优化",
            payload={
                "research_question": state.input_profile.research_question,
                "time_range": state.input_profile.time_range,
                "languages": state.input_profile.languages,
                "themes": [item.model_dump() for item in state.theme_tree.nodes],
                "draft_queries": [item.model_dump() for item in branches],
                "output_schema": {"queries": [{"branch_id": "Q1", "query": "string", "synonyms": ["string"], "purpose": "string", "stop_condition": "string"}], "coverage_warnings": ["string"]},
            },
            expected_keys={"queries"},
        )
        if model_payload:
            by_id = {item.branch_id: item for item in branches}
            for item in model_payload.get("queries", [])[:8]:
                if not isinstance(item, dict):
                    continue
                branch = by_id.get(str(item.get("branch_id") or ""))
                if branch is None:
                    continue
                query = self._redact_model_text(str(item.get("query") or ""), 500)
                if query:
                    branch.query = query
                branch.synonyms = [self._redact_model_text(str(value), 100) for value in item.get("synonyms", []) if str(value).strip()][:12] or branch.synonyms
                branch.purpose = str(item.get("purpose") or branch.purpose)[:240]
                branch.stop_condition = str(item.get("stop_condition") or branch.stop_condition)[:240]
            warnings = [str(item)[:240] for item in model_payload.get("coverage_warnings", []) if str(item).strip()]
        else:
            warnings = []
        state.query_plan = QueryPlan(branches=branches, coverage_warnings=warnings)
        intents = []
        for branch in branches:
            english_terms = [term for term in branch.synonyms if re.search(r"[A-Za-z]", term)]
            variants = []
            if english_terms:
                variants.append(" ".join(dict.fromkeys(english_terms)))
            compact = " ".join(dict.fromkeys([branch.query.split()[0] if branch.query.split() else "", *english_terms])).strip()
            intents.append(FulltextIntent(
                intent_id=branch.branch_id, query=branch.query, theme_ids=[branch.theme_id],
                year_range=branch.year_range, languages=branch.languages,
                query_variants=variants, fallback_queries=[compact] if compact else [],
            ))
        fulltext_job = asyncio.create_task(self.fulltext_client.search_intents(intents, source_agent="topic_selection")) if state.source_policy.allow_nstl and hasattr(self.fulltext_client, "search_intents") else None
        await self._emit_progress(
            state, callback, stage_key="retrieval_evidence", step_key="query_plan_ready",
            kind="artifact", title="检索计划已生成",
            message="已按子主题形成独立查询分支，准备执行来源查询。", progress_percent=12,
            current=0, total=len(branches), unit="分支",
            metrics={"主题分支": len(branches), "每支上限": 20},
            artifact_refs=[branch.branch_id for branch in branches],
        )
        now = datetime.now().isoformat()
        source_queries: list[SourceQuery] = []
        adapter_jobs: list[tuple[SourceQuery, SourceAdapter]] = []
        for branch in branches:
            sanitized = self._sanitize_external_query(branch.query, state)
            source_query = SourceQuery(
                query_id=branch.branch_id,
                source_kind="nstl",
                purpose=branch.purpose,
                query=branch.query,
                sanitized_query=sanitized,
                time_range=branch.year_range,
                languages=branch.languages,
                stop_condition=branch.stop_condition,
                requested_at=now,
                redaction_applied=sanitized != branch.query,
                external_request_allowed=state.source_policy.allow_nstl,
            )
            source_queries.append(source_query)
            if state.source_policy.allow_nstl:
                adapter_jobs.append((source_query, self.nstl_adapter))

        # Dynamic sources are queried once per run with an aggregate, sanitized
        # intent. They never receive uploaded excerpts or the raw user input.
        aggregate_query = self._sanitize_external_query(
            " ".join(node.label for node in state.theme_tree.nodes[:5]), state,
        )
        for source_kind, adapter, enabled in (
            ("web", self.web_adapter, state.source_policy.allow_web),
            ("cloud_model", self.cloud_adapter, state.source_policy.allow_cloud_model),
        ):
            source_query = SourceQuery(
                query_id=f"{source_kind[:3].upper()}-1",
                source_kind=source_kind,
                purpose="近期动态、问题变体与交叉方向查询",
                query=aggregate_query,
                sanitized_query=aggregate_query,
                time_range=state.input_profile.time_range,
                stop_condition="形成可审计的动态补充后停止",
                requested_at=now,
                redaction_applied=True,
                external_request_allowed=enabled and bool(aggregate_query),
                status="pending" if enabled and aggregate_query else "disabled",
            )
            source_queries.append(source_query)
            if source_query.external_request_allowed:
                adapter_jobs.append((source_query, adapter))

        state.source_queries = source_queries
        enabled_jobs = len(adapter_jobs)
        await self._emit_progress(
            state, callback, stage_key="retrieval_evidence", step_key="source_queries_started",
            title="正在等待检索来源返回",
            message="各查询独立执行，页面将按真实完成顺序更新召回数量。", progress_percent=20,
            current=0, total=enabled_jobs, unit="查询",
            metrics={"已启用查询": enabled_jobs, "已关闭来源": sum(item.status == "disabled" for item in source_queries)},
        )
        results: list[tuple[SourceQuery, SourceExecutionResult]] = []
        external_io_started = time.perf_counter()
        if adapter_jobs:
            async def execute(job: tuple[SourceQuery, SourceAdapter]) -> tuple[SourceQuery, SourceExecutionResult]:
                source_query, adapter = job
                try:
                    ensure_prelock_external_access(state.evidence_lock.locked, source_query.source_kind)
                    source_query.status = "running"
                    result = await adapter.query(source_query, user_scope=state.user_scope, policy=state.source_policy)
                except Exception as exc:
                    result = SourceExecutionResult(False, source_query.source_kind, error=str(exc))
                source_query.status = "completed" if result.success else "failed"
                source_query.completed_at = datetime.now().isoformat()
                source_query.returned_count = len(result.papers) + len(result.external_evidence) + len(result.model_insights)
                source_query.response_hash = self._version("response", str(result.trace) + result.error)
                source_query.failure_reason = result.error
                return source_query, result
            tasks = [asyncio.create_task(execute(job)) for job in adapter_jobs]
            for completed_index, task in enumerate(asyncio.as_completed(tasks), start=1):
                source_query, result = await task
                results.append((source_query, result))
                returned = sum(item.returned_count for item in state.source_queries)
                await self._emit_progress(
                    state, callback, stage_key="retrieval_evidence",
                    step_key=f"query_{source_query.query_id}",
                    kind="metric" if result.success else "warning",
                    status="completed" if result.success else "failed",
                    title=f"{self._source_label(source_query.source_kind)}查询{'完成' if result.success else '失败'}",
                    message=(f"{source_query.query_id} 返回 {source_query.returned_count} 条记录。" if result.success else (result.error or "来源暂不可用。")),
                    progress_percent=20 + round(55 * completed_index / max(1, enabled_jobs)),
                    current=completed_index, total=enabled_jobs, unit="查询",
                    metrics={"累计召回": returned, "完成查询": completed_index, "失败查询": sum(item.status == "failed" for item in state.source_queries)},
                    artifact_refs=[source_query.query_id],
                )
        retrieval_trace = next((item for item in state.stage_trace if item.stage_key == "retrieval_evidence"), None)
        if retrieval_trace is not None:
            retrieval_trace.external_io_duration_ms = round((time.perf_counter() - external_io_started) * 1000)
            retrieval_trace.metrics["外部IO耗时"] = f"{retrieval_trace.external_io_duration_ms / 1000:.1f}s"

        records: dict[str, EvidenceRecord] = {}
        branch_by_id = {branch.branch_id: branch for branch in branches}
        for source_query, result in results:
            state.search_traces.append({
                "query_id": source_query.query_id,
                "source_kind": source_query.source_kind,
                "query": source_query.sanitized_query,
                "purpose": source_query.purpose,
                "status": source_query.status,
                "returned_count": source_query.returned_count,
                "response_hash": source_query.response_hash,
                "redaction_applied": source_query.redaction_applied,
                "external_request_allowed": source_query.external_request_allowed,
                "error": source_query.failure_reason,
                **result.trace,
            })
            branch = branch_by_id.get(source_query.query_id)
            if branch is not None:
                branch.status = "completed" if result.success else "unavailable"
                branch.returned_count = len(result.papers)
                branch.source_status["nstl"] = source_query.status
                if not result.success:
                    state.query_plan.coverage_warnings.append(result.error or "NSTL 检索不可用")
            for paper in result.papers:
                if branch is None:
                    continue
                evidence = self._evidence_from_paper(paper, branch)
                key = evidence.doi.lower() or self._normal_key(evidence.title, evidence.year)
                if key and key not in records:
                    records[key] = evidence
                elif key in records and branch.theme_id not in records[key].theme_ids:
                    records[key].theme_ids.append(branch.theme_id)
        state.evidence_pool = list(records.values())
        await self._retrieve_fulltext_chunks(state, callback)
        if fulltext_job is not None:
            await self._fuse_problem_fulltext(state, await fulltext_job, callback)
        for source_query, result in results:
            state.external_evidence.extend(result.external_evidence)
            state.model_insights.extend(result.model_insights)
        state.external_evidence = self._deduplicate_external_evidence(state.external_evidence)
        from app.agents.retrieval_selection import limit_topic_literature
        limit_topic_literature(state)
        for node in state.theme_tree.nodes:
            node.evidence_ids = [item.evidence_id for item in state.evidence_pool if node.theme_id in item.theme_ids]
        state.versions.query_plan_version = self._version("query", "|".join(item.query for item in branches))
        state.versions.evidence_pool_version = self._version("evidence", "|".join(item.evidence_id for item in state.evidence_pool))
        state.evidence_lock = EvidenceLock(
            locked=True,
            lock_id=f"lock-{uuid4().hex[:12]}",
            locked_at=datetime.now().isoformat(),
            reason="三路查询、去重、冲突识别和来源分层已完成",
            evidence_pool_version=state.versions.evidence_pool_version,
            external_calls_blocked=True,
            locked_source_counts={
                "nstl": sum(item.source_kind == "nstl" for item in state.evidence_pool),
                "fulltext_library": sum(item.source_kind == "fulltext_library" for item in state.evidence_pool),
                "web": sum(item.source_kind == "web" for item in state.external_evidence),
                "cloud_model": sum(item.source_kind == "cloud_model" for item in state.external_evidence),
            },
        )
        cutoff = state.source_policy.evidence_cutoff_at or datetime.now().isoformat()
        newest_year = max((item.year for item in state.evidence_pool if item.year and item.source_kind == "nstl"), default=None)
        newest_external = max((item.fetched_at or item.published_at for item in state.external_evidence), default="")
        state.freshness_snapshot = FreshnessSnapshot(
            cutoff_at=cutoff,
            requirement_days=state.source_policy.freshness_days,
            newest_nstl_year=newest_year,
            newest_external_at=newest_external,
            nstl_count=sum(item.source_kind == "nstl" for item in state.evidence_pool),
            web_count=sum(item.source_kind == "web" for item in state.external_evidence),
            cloud_count=sum(item.source_kind == "cloud_model" for item in state.external_evidence),
            fresh_external_count=sum(bool(item.published_at or item.fetched_at) for item in state.external_evidence),
            limitations=["联网和云端模型结果是动态补充证据，未与文献证据混称。"] if state.external_evidence else [],
        )
        await self._emit_progress(
            state, callback, stage_key="retrieval_evidence", step_key="evidence_pool_locked",
            kind="artifact", title="证据池去重并锁定",
            message="来源记录已分层保存，后续分析只使用当前锁定版本。", progress_percent=94,
            metrics={
                "NSTL证据": sum(item.source_kind == "nstl" for item in state.evidence_pool),
                "动态证据": len(state.external_evidence),
                "去重后证据": len(state.evidence_pool),
                "失败查询": sum(item.status == "failed" for item in state.source_queries),
                "全文片段": len(state.evidence_chunks),
            },
            artifact_refs=[state.evidence_lock.lock_id],
        )
        state.resume_from_stage = ""

    async def _fuse_problem_fulltext(self, state: TopicSelectionState, results: list, callback: StageCallback | None) -> None:
        initial_selected = sum(len(selected_chunks_with_stats(intent, response)[0]) for intent, response in results)
        fallback_results: list = []
        if initial_selected == 0:
            fallback_intents = [FulltextIntent(
                intent_id=intent.intent_id,
                query=intent.fallback_queries[0],
                theme_ids=intent.theme_ids,
                claim_ids=intent.claim_ids,
                year_range=intent.year_range,
                languages=intent.languages,
            ) for intent, _ in results if intent.fallback_queries]
            if fallback_intents:
                fallback_results = await self.fulltext_client.search_intents(fallback_intents, source_agent="topic_selection")
                results = [*results, *fallback_results]
        chunks_by_key = {(c.doc_id, c.chunk_id): c for c in state.evidence_chunks}
        per_doc = Counter(c.doc_id for c in state.evidence_chunks)
        rejected = 0
        diagnostics: Counter[str] = Counter()
        raw_candidates = 0
        for intent, response in results:
            if not response.success:
                state.query_plan.coverage_warnings.append(response.error)
            raw_candidates += int(response.retrieval_stats.get("raw_candidates", len(response.chunks)))
            selected, stats = selected_chunks_with_stats(intent, response)
            diagnostics.update(stats)
            rejected += max(0, len(response.chunks) - len(selected))
            for chunk in selected:
                evidence, status = match_source(chunk, state.evidence_pool)
                if status in {"ambiguous", "conflicting_identity"}:
                    rejected += 1
                    continue
                if evidence is None:
                    evidence_id = "E-FT-" + hashlib.sha1(chunk.doc_id.encode()).hexdigest()[:12]
                    evidence = EvidenceRecord(evidence_id=evidence_id, paper_id=chunk.doc_id,
                        title=chunk.title, authors=chunk.authors, year=chunk.year, doi=chunk.doi,
                        url=chunk.source_url if chunk.source_url.startswith(("https://", "http://")) else "",
                        source_type="fulltext_library", source_kind="fulltext_library", match_status=status)
                    state.evidence_pool.append(evidence)
                key = (chunk.doc_id, chunk.chunk_id)
                if key not in chunks_by_key and per_doc[chunk.doc_id] >= 3:
                    continue
                if key not in chunks_by_key:
                    item = TopicEvidenceChunk(**{k: v for k, v in chunk.model_dump().items() if k in TopicEvidenceChunk.model_fields},
                        evidence_id=evidence.evidence_id, locator=chunk_locator(chunk),
                        intent_ids=[intent.intent_id], theme_ids=intent.theme_ids)
                    chunks_by_key[key] = item
                    state.evidence_chunks.append(item)
                    per_doc[chunk.doc_id] += 1
                else:
                    item = chunks_by_key[key]
                    item.intent_ids = list(dict.fromkeys(item.intent_ids + [intent.intent_id]))
                    item.theme_ids = list(dict.fromkeys(item.theme_ids + intent.theme_ids))
                evidence.theme_ids = list(dict.fromkeys(evidence.theme_ids + intent.theme_ids))
                evidence.retrieval_intent_ids = list(dict.fromkeys(evidence.retrieval_intent_ids + [intent.intent_id]))
                evidence.match_status = status if evidence.source_kind == "nstl" else evidence.match_status
                evidence.fulltext_available = True
                evidence.anchor_verified = True
                evidence.evidence_level = "A"
                evidence.access_status = "authorized"
                if evidence.source_kind == "nstl":
                    evidence.source_type = "nstl_fulltext"
                if not evidence.excerpt or chunk.score > evidence.retrieval_score:
                    evidence.excerpt = chunk.text
                    evidence.locator = chunk_locator(chunk)
                    evidence.paragraph_ref = chunk.section_path
                    evidence.char_start, evidence.char_end = chunk.char_start, chunk.char_end
                evidence.source_hash = chunk.source_hash
                evidence.retrieval_score = max(evidence.retrieval_score, chunk.score)
        if not any(c.intent_ids for c in state.evidence_chunks):
            state.query_plan.coverage_warnings.append("按问题查询尚未取得可核对的正文依据，相关判断仍受摘要或题录范围限制。")
        state.query_plan.coverage_warnings = list(dict.fromkeys(state.query_plan.coverage_warnings))
        if state.evidence_chunks:
            state.query_plan.coverage_warnings = [w for w in state.query_plan.coverage_warnings
                if not w.startswith("检索结果未提供可查询的全文文献")]
        fulltext_stats = {
            "raw_candidates": raw_candidates,
            "accepted_chunks": len(state.evidence_chunks),
            "covered_papers": len({item.evidence_id for item in state.evidence_chunks}),
            "query_variant_count": diagnostics.get("query_variant_count", 0),
            "original_variant_count": diagnostics.get("original_variant_count", 0),
            "synonym_variant_count": diagnostics.get("synonym_variant_count", 0),
            "chinese_query_variant_count": diagnostics.get("chinese_query_variant_count", 0),
            "english_query_variant_count": diagnostics.get("english_query_variant_count", 0),
            "fallback_query_count": len(fallback_results),
            "filtered": {key: value for key, value in diagnostics.items() if key in {
                "invalid_source", "non_body", "duplicate", "below_relevance", "out_of_range", "coverage_cap", "timeout_count"
            }},
        }
        fulltext_stats["diagnostic_message"] = (
            f"返回 {raw_candidates} 个候选，其中来源不全 {fulltext_stats['filtered'].get('invalid_source', 0)}、"
            f"非正文 {fulltext_stats['filtered'].get('non_body', 0)}、重复 {fulltext_stats['filtered'].get('duplicate', 0)}、"
            f"相关性不足 {fulltext_stats['filtered'].get('below_relevance', 0)}、"
            f"覆盖上限 {fulltext_stats['filtered'].get('coverage_cap', 0)}，最终采用 {len(state.evidence_chunks)} 段。"
        )
        state.output_json["fulltext_retrieval_stats"] = fulltext_stats
        state.process_trace.setdefault("retrieval_evidence", []).append({
            "code": "fulltext_fusion", "message": fulltext_stats["diagnostic_message"],
            "intent_count": len(results), "chunk_count": len(state.evidence_chunks),
            "stats": fulltext_stats,
        })
        await self._emit_progress(state, callback, stage_key="retrieval_evidence", step_key="problem_fulltext_ready",
            kind="artifact", title="按问题整理正文依据", message="正文片段已关联对应主题，并保留出处与适用范围。",
            progress_percent=88, metrics={"全文片段": len(state.evidence_chunks)}, artifact_refs=[])

    async def _retrieve_fulltext_chunks(self, state: TopicSelectionState, callback: StageCallback | None) -> None:
        eligible = sorted(
            (item for item in state.evidence_pool if item.paper_id and (item.fulltext_indexed or item.fulltext_available)),
            key=lambda item: item.final_score or item.retrieval_score,
            reverse=True,
        )[:30]
        # Index metadata describes availability, never proof of retrieved full text.
        for evidence in state.evidence_pool:
            evidence.fulltext_available = False
            evidence.anchor_verified = False
            evidence.access_status = "abstract_only" if evidence.abstract else "unavailable"
            evidence.source_type = "nstl_abstract" if evidence.abstract else "nstl_metadata"
            evidence.evidence_level = "B" if evidence.abstract else "C"
        state.evidence_chunks = []
        if not eligible:
            state.evidence_chunks = []
            state.query_plan.coverage_warnings.append("检索结果未提供可查询的全文文献，当前仅保留摘要或元数据证据")
            return
        query = " ".join(node.label for node in state.theme_tree.nodes[:5]) or state.input_profile.research_question
        response = await retrieval_io(
            self.fulltext_client.query_chunks,
            query=query,
            doc_ids=[item.paper_id for item in eligible],
            top_k_per_doc=3,
        )
        if not response.success:
            reason = response.error or "全文分块接口不可用"
            state.query_plan.coverage_warnings.append(f"{reason}；已降级为摘要证据，未按全文证据提升推荐等级")
            state.process_trace.setdefault("retrieval_evidence", []).append({
                "level": "warning", "code": "fulltext_unavailable", "message": reason,
                "anchor": "retrieval-fulltext-degradation",
            })
            await self._emit_progress(
                state, callback, stage_key="retrieval_evidence", step_key="fulltext_degraded",
                kind="warning", status="completed", title="全文查询已降级",
                message=f"{reason}；摘要证据不会冒充全文证据。", progress_percent=82,
                metrics={"全文片段": 0, "摘要证据": sum(bool(item.abstract) for item in state.evidence_pool)},
                artifact_refs=["retrieval-fulltext-degradation"],
            )
            return
        evidence_by_doc = {item.paper_id: item for item in eligible}
        kept: list[TopicEvidenceChunk] = []
        per_doc: Counter[str] = Counter()
        seen: set[str] = set()
        for chunk in sorted(response.chunks, key=lambda item: item.score, reverse=True):
            evidence = evidence_by_doc.get(chunk.doc_id)
            if evidence is None or per_doc[chunk.doc_id] >= 3:
                continue
            valid_range = chunk.char_start is not None and chunk.char_end is not None and 0 <= chunk.char_start < chunk.char_end
            if not chunk.text.strip() or not chunk.chunk_id or not (chunk.section_path.strip() or valid_range):
                continue
            stable_key = f"{chunk.doc_id}:{chunk.chunk_id}"
            content_key = f"{chunk.doc_id}:text:{hashlib.sha256(chunk.text.strip().encode()).hexdigest()}"
            range_key = f"{chunk.doc_id}:range:{chunk.char_start}:{chunk.char_end}" if valid_range else content_key
            if stable_key in seen or content_key in seen or range_key in seen:
                continue
            seen.update((stable_key, content_key, range_key))
            per_doc[chunk.doc_id] += 1
            locator = chunk.section_path or (
                f"字符 {chunk.char_start}-{chunk.char_end}" if chunk.char_start is not None and chunk.char_end is not None else chunk.chunk_id
            )
            kept.append(TopicEvidenceChunk(
                chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, evidence_id=evidence.evidence_id,
                text=chunk.text, chunk_index=chunk.chunk_index, chunk_type=chunk.chunk_type,
                section_path=chunk.section_path, char_start=chunk.char_start, char_end=chunk.char_end,
                token_count=chunk.token_count, score=chunk.score, locator=locator,
            ))
        state.evidence_chunks = kept
        missing = len(eligible) - len({item.doc_id for item in kept})
        if missing or response.error:
            reason = "；".join(item for item in [f"{missing} 篇全文标记文献未返回可定位片段，保留摘要或元数据证据" if missing else "", response.error] if item)
            state.query_plan.coverage_warnings.append(reason)
            state.process_trace.setdefault("retrieval_evidence", []).append({"level": "warning", "code": "fulltext_partial", "message": reason, "anchor": "retrieval-fulltext-degradation"})
        chunks_by_evidence: dict[str, list[TopicEvidenceChunk]] = {}
        for chunk in kept:
            chunks_by_evidence.setdefault(chunk.evidence_id, []).append(chunk)
        for evidence in state.evidence_pool:
            chunks = chunks_by_evidence.get(evidence.evidence_id, [])
            if not chunks:
                evidence.fulltext_available = False
                evidence.access_status = "abstract_only" if evidence.abstract else "unavailable"
                continue
            best = chunks[0]
            evidence.source_type = "nstl_fulltext"
            evidence.evidence_level = "A"
            evidence.access_status = "authorized"
            evidence.fulltext_available = True
            evidence.anchor_verified = True
            evidence.excerpt = best.text
            evidence.paragraph_ref = best.section_path
            evidence.char_start = best.char_start
            evidence.char_end = best.char_end
            evidence.locator = best.locator
        await self._emit_progress(
            state, callback, stage_key="retrieval_evidence", step_key="fulltext_chunks_ready",
            kind="artifact", title="全文证据片段已定位",
            message="全文片段已按文献去重并保留章节与字符位置。", progress_percent=82,
            metrics={"全文文献": len(chunks_by_evidence), "全文片段": len(kept)},
            artifact_refs=[item.chunk_id for item in kept[:20]],
        )

    async def _landscape_trend(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        statistical_records = [item for item in state.evidence_pool if item.source_kind == "nstl"]
        years = Counter(item.year for item in statistical_records if item.year)
        themes = Counter(theme_id for item in statistical_records for theme_id in item.theme_ids)
        state.landscape_snapshot = LandscapeSnapshot(
            snapshot_id=f"landscape-{uuid4().hex[:8]}",
            year_counts=[{"year": year, "count": count} for year, count in sorted(years.items())],
            theme_counts=[{"theme_id": key, "count": value} for key, value in themes.most_common()],
            source_counts=[
                {"source": "文献", "count": len(statistical_records)},
                {"source": "全文覆盖", "count": len({item.evidence_id for item in state.evidence_chunks})},
                *([{"source": "Web", "count": sum(item.source_kind == "web" for item in state.external_evidence)}] if any(item.source_kind == "web" for item in state.external_evidence) else []),
                *([{"source": "CloudModel", "count": sum(item.source_kind == "cloud_model" for item in state.external_evidence)}] if any(item.source_kind == "cloud_model" for item in state.external_evidence) else []),
            ],
            sample_count=len(statistical_records),
            abstract_count=sum(bool(item.abstract) for item in state.evidence_pool),
            fulltext_count=sum(item.fulltext_available for item in state.evidence_pool),
            limitations=["当前证据池以 NSTL 元数据与摘要为主，尚未取得可定位全文片段。"] if not any(item.fulltext_available for item in state.evidence_pool) else [],
        )
        await self._emit_progress(
            state, callback, stage_key="landscape_trend", step_key="landscape_counted",
            kind="metric", title="研究全景统计完成",
            message="已按年份、主题和来源汇总锁定证据池。", progress_percent=58,
            metrics={"样本": len(state.evidence_pool), "年份": len(years), "主题": len(themes), "全文": state.landscape_snapshot.fulltext_count},
            artifact_refs=[state.landscape_snapshot.snapshot_id],
        )
        signals: list[FutureWorkSignal] = []
        patterns = re.compile(r"(?:future work|future research|limitation|open problem|展望|局限|未来工作|有待|仍需|尚未解决)[^。；.!?]{0,160}", re.I)
        for item in state.evidence_pool:
            for match in patterns.finditer(item.abstract or ""):
                signals.append(FutureWorkSignal(
                    signal_id=f"FW-{len(signals)+1:03d}", paper_id=item.paper_id, evidence_id=item.evidence_id,
                    text=match.group(0).strip(), signal_type="future_work", source_scope="abstract",
                    theme_ids=item.theme_ids, confidence=0.55, usable_for=["context", "candidate_seed"],
                ))
        state.future_work_signals = signals[:100]
        evidence_refs = [item.evidence_id for item in state.evidence_pool[:40]]
        from .discovery import discovery_context
        landscape_payload = await self._stage_model_json(
            state, callback,
            stage_key="landscape_trend", prompt_version="topic-landscape-v4",
            purpose="研究全景、趋势、方法演化与证据冲突分析",
            payload={
                "research_question": state.input_profile.research_question,
                "landscape_statistics": state.landscape_snapshot.model_dump(),
                **discovery_context(state, limit=16),
                "future_work_signals": [item.model_dump() for item in state.future_work_signals[:30]],
                "instruction": "方法演进用一段自然语言综合说明本次文献中方法、测量或验证设计如何变化；依据本次文献真实年份和内容灵活组织，不固定分期、不按年份罗列文献。若年份跨度很短或资料稀疏，只概括证据实际显示的方法特点，不虚构演进阶段。另用一段概括可从这些研究继续发展的方向，可以作有依据的综合推演，但不要写成文献已经验证的结论。两段分别提供实际支撑它们的evidence_ids。保留method_evolution列表以兼容既有图表和报告。近期发表的既有成果不属于future。检索年份分布不等于领域总体增长。",
                "output_schema": {"concise_title": "约15个中文字符的概括性标题", "overview": "string", "trend_findings": [{"finding": "string", "evidence_ids": ["E-..."]}], "method_evolution_summary": {"text": "依据本次文献形成的一段方法演进综合说明", "evidence_ids": ["E-..."]}, "future_research_summary": {"text": "依据本次文献形成的一段后续研究思路，不作为已验证结论", "evidence_ids": ["E-..."]}, "method_evolution": [{"period": "past|present|future", "finding": "具体方法及变化；future必须是待验证建议", "evidence_ids": ["E-..."]}], "conflicts": [{"finding": "string", "support_evidence_ids": ["E-..."], "counter_evidence_ids": ["E-..."]}], "coverage_limitations": ["string"]},
            },
            expected_keys={"overview", "trend_findings", "method_evolution", "coverage_limitations"},
            evidence_refs=evidence_refs,
            max_tokens=4200,
        )
        if (
            landscape_payload
            and not state.task_profile.get("explicit_topic_title")
            and not state.task_profile.get("concise_title_generated")
        ):
            concise_title = self._normalize_concise_title(str(landscape_payload.get("concise_title") or ""))
            if concise_title:
                state.input_profile.title = concise_title
                state.task_profile["concise_title_generated"] = True
        from .discovery import method_evolution, sample_finding
        if landscape_payload:
            landscape_payload["overview"] = sample_finding(landscape_payload.get("overview", ""))
            for finding in landscape_payload.get("trend_findings", []):
                if isinstance(finding, dict):
                    finding["finding"] = sample_finding(finding.get("finding", ""))
            evolution = method_evolution(state, landscape_payload.get("method_evolution", []))
            readable_ids = {
                item.evidence_id for item in state.evidence_pool if item.abstract.strip()
            } | {
                chunk.evidence_id for chunk in state.evidence_chunks
                if chunk.text.strip() and (chunk.locator or chunk.section_path)
            }

            def grounded_summary(key: str, fallback_period: str | None = None) -> dict[str, Any]:
                proposed = landscape_payload.get(key)
                proposed = proposed if isinstance(proposed, dict) else {}
                text = reader_prose(proposed.get("text"))
                refs = list(dict.fromkeys(str(eid) for eid in proposed.get("evidence_ids", []) if str(eid) in readable_ids))
                if not text and fallback_period:
                    matching = [row for row in evolution if row["period"] == fallback_period]
                    text = " ".join(str(row.get("finding") or "").strip() for row in matching if row.get("finding"))
                    refs = list(dict.fromkeys(eid for row in matching for eid in row.get("evidence_ids", []) if eid in readable_ids))
                text = reader_prose(text)
                return {"text": text, "evidence_ids": refs} if text and refs else {"text": "", "evidence_ids": []}

            analysis = state.stage_artifacts.setdefault("landscape_trend", {}).setdefault("analysis", {})
            analysis["method_evolution"] = evolution
            analysis["method_evolution_summary"] = grounded_summary("method_evolution_summary", "present")
            analysis["future_research_summary"] = grounded_summary("future_research_summary", "future")
        state.versions.landscape_snapshot_version = self._version("landscape", str(state.landscape_snapshot.model_dump()))
        await self._emit_progress(
            state, callback, stage_key="landscape_trend", step_key="future_signals_ready",
            kind="artifact", title="未来工作与局限信号已提取",
            message="仅保留可回链到证据摘要或全文的研究信号。", progress_percent=92,
            metrics={"未来工作信号": len(state.future_work_signals), "摘要": state.landscape_snapshot.abstract_count, "来源类型": len(state.landscape_snapshot.source_counts)},
            artifact_refs=[item.signal_id for item in state.future_work_signals[:12]],
        )

    async def _crosslink_gap(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        from .recovery import raise_for_transport
        # Preserve verified opportunities across interrupted execution windows.
        saved_proposals = state.stage_artifacts.get('crosslink_gap', {}).get('verified_opportunities', [])
        links: list[CrossLink] = []
        for left, right in combinations(state.theme_tree.nodes, 2):
            left_terms = {self._term_key(item) for item in [left.label, *left.seed_terms] if item}
            right_terms = {self._term_key(item) for item in [right.label, *right.seed_terms] if item}
            shared_terms = left_terms & right_terms
            support: list[str] = []
            counter: list[str] = []
            for item in state.evidence_pool:
                excerpts = [chunk.text for chunk in state.evidence_chunks if chunk.evidence_id == item.evidence_id]
                text = " ".join([item.title, item.abstract, *item.keywords, *excerpts]).lower()
                specific_left = left_terms - shared_terms
                specific_right = right_terms - shared_terms
                mentions_both = (any(term in text for term in specific_left) and any(term in text for term in specific_right))
                if mentions_both:
                    support.append(item.evidence_id)
            confidence = min(0.95, 0.2 + len(support) / 8 + (0.15 if shared_terms else 0))
            # A pair with no shared evidence is not a user-facing crosslink.
            # Keep only evidence-backed or explicitly model-derived relations;
            # mere keyword co-occurrence is retained as a gap input, not asserted.
            explicit_relation = next((edge.get("relation", "") for edge in state.theme_tree.edges if {edge.get("source"), edge.get("target")} == {left.theme_id, right.theme_id}), "")
            if not support and not counter and not explicit_relation:
                continue
            relation_type = "shared_bottleneck"
            relation_text = (
                f"{left.label}与{right.label}在{'、'.join(sorted(shared_terms)[:3])}方面出现共同研究线索，"
                "需要通过对照数据确认该联系是否具有可迁移性。"
                if shared_terms else
                f"同一批文献同时讨论{left.label}与{right.label}，提示两者可能存在研究衔接。"
            )
            left_disciplines = list(dict.fromkeys(left.discipline_tags))
            right_disciplines = list(dict.fromkeys([*right.discipline_tags, *(tag for paper in state.evidence_pool if paper.evidence_id in support for tag in paper.discipline_tags)]))
            left_method = (left.method_boundary or "").strip()
            right_method = (right.method_boundary or "").strip()
            method = next((value for value in (left_method, right_method) if value and len(value) >= 4
                           and any(value.lower() in " ".join([paper.title, paper.abstract, *paper.keywords]).lower()
                                   for paper in state.evidence_pool if paper.evidence_id in support)), "")
            if method:
                relation_type = "shared_method"
            links.append(CrossLink(
                crosslink_id=f"XL-{len(links)+1:03d}", source_theme_id=left.theme_id, target_theme_id=right.theme_id,
                relation_type=relation_type,
                title=f"{left.label} × {right.label}", description=explicit_relation or relation_text,
                support_evidence_ids=support[:20], counter_evidence_ids=counter[:20],
                future_work_signal_ids=[signal.signal_id for signal in state.future_work_signals if set(signal.theme_ids) & {left.theme_id, right.theme_id}][:10],
                sample_count=len(support), confidence=confidence,
                status="candidate",
                uncertainty="文献同时涉及两个方向，但尚不能据此确认具体机制或方法可迁移性。" if support else "当前只有主题分析线索，不能据此断言存在交叉研究。",
                relation_basis=(f"文献《{next((item.title for item in state.evidence_pool if item.evidence_id == support[0]), '')}》同时涉及两个方向" if support else "任务主题分析提出的关系线索，当前尚无共同文献支持"),
                research_opportunity=f"检验{left.label}中的方法或发现是否适用于{right.label}的边界，并比较失效条件。",
                core_discipline=(left_disciplines[0] if left_disciplines else ""),
                expansion_discipline=(next((tag for tag in right_disciplines if tag not in left_disciplines), "")),
                transferable_method=method,
                alternative_explanations=["检索词重叠造成的表面共现", "两个主题分别成熟但尚未形成可迁移方法"],
            ))
        state.crosslinks = links
        await self._emit_progress(
            state, callback, stage_key="crosslink_gap", step_key="crosslinks_checked",
            kind="metric", title="主题交叉关系核验完成",
            message="已区分有共同证据的关系与仅有主题邻近性的组合。", progress_percent=58,
            metrics={"主题组合": len(links), "有效交叉": sum(item.status == "validated" for item in links), "候选交叉": sum(item.status == "candidate" for item in links)},
            artifact_refs=[item.crosslink_id for item in links[:12]],
        )
        gaps: list[GapScore] = []
        for index, link in enumerate(links[:12], start=1):
            density = min(1.0, link.sample_count / 20)
            support = min(1.0, len(link.support_evidence_ids) / 8)
            coverage_limited = not link.support_evidence_ids
            opportunity = round(max(0.0, 0.35 * link.confidence + 0.4 * support +
                                    (0.25 if link.future_work_signal_ids else 0.0) -
                                    0.15 * min(1.0, len(link.counter_evidence_ids) / 5)), 4)
            gaps.append(GapScore(
                gap_id=f"G{index}", label=f"{link.title} 的交叉验证问题" if not coverage_limited else f"{link.title} 的覆盖不足待验证方向", gap_type="method" if not coverage_limited else "coverage", theme_ids=[link.source_theme_id, link.target_theme_id],
                crosslink_ids=[link.crosslink_id], evidence_ids=link.support_evidence_ids, counter_evidence_ids=link.counter_evidence_ids, future_work_signal_ids=link.future_work_signal_ids,
                density=density, problem_coverage=support, method_coverage=support, scenario_coverage=0.5,
                recent_change=0.5 if link.future_work_signal_ids else 0.25, evidence_completeness=support, counter_evidence_strength=min(1.0, len(link.counter_evidence_ids) / 5), competition_heat=density,
                opportunity_space=opportunity, score=opportunity, rationale=f"{link.research_opportunity} 当前检索发现{link.sample_count}篇共同文献；共现本身不证明因果机制或方法效果。",
                uncertainty=link.uncertainty, validation_status="candidate" if link.support_evidence_ids else "coverage_limited",
            ))
        if not gaps:
            for node in state.theme_tree.nodes:
                gaps.append(GapScore(
                    gap_id=f"G{len(gaps) + 1}", label=f"{node.label}的验证边界", gap_type="coverage", theme_ids=[node.theme_id],
                    evidence_ids=list(node.evidence_ids), opportunity_space=0.4, score=0.4,
                    rationale=f"现有资料围绕{node.label}展开；尚不能确认{node.research_question or node.boundary}中的机制与适用范围，是研究机会线索而非已证实空白。",
                    uncertainty="资料覆盖不足，暂不能确认；可通过边界对照和独立样本检验。", validation_status="coverage_limited",
                ))
        state.gaps = sorted(gaps, key=lambda item: item.score, reverse=True)
        from .discovery import discovery_context
        compact = discovery_context(state)
        analysis_payload = {
                "research_question": state.input_profile.research_question,
                "crosslinks": [{"crosslink_id": item.crosslink_id, "source_theme_id": item.source_theme_id,
                                "target_theme_id": item.target_theme_id, "title": item.title} for item in state.crosslinks[:8]],
                "draft_gaps": [{"gap_id": item.gap_id, "label": item.label[:300]} for item in state.gaps[:8]],
                **compact,
                "response_limits": "只返回最多3条主要cross_opportunities和3条gaps。每条机会可关联多个有依据的参与学科，核心学科另计，所有机会合计最多6个拓展学科；证据不足时少于2个也如实返回，不能补齐数量，不能把所有主题标签连到每条机会。每条引文60–220字符；source_method使用quote中逐字出现的具体方法词或操作描述，禁止翻译或拼接为原文名称。即使没有结果也必须返回gaps和cross_opportunities两个数组。",
                "instruction": "主题数量不限制机会生成。只有一个主题时也应从其研究问题、学科及文献中的具体方法分析机会，source_theme_id与target_theme_id可以相同；没有可借鉴方法才返回空数组。不要将未解决问题等同于完全没有先例。从研究问题确定核心学科，从文献发现拓展学科和具体方法。逐条解释借鉴什么、为何适用、形成何种机会及验证条件。方法原文名称和quote必须逐字来自提供的摘要或全文，注明evidence_id及全文chunk_id。不得引用题名或关键词作为方法效果证据；主题共现、检索较少不证明交叉机制或研究空白。学科模型推断需给出reason。",
                "output_schema": {"gaps": [{"gap_id": "G1", "gap_statement": "string", "gap_type": "problem|method|scenario|evidence|coverage", "support_evidence_ids": ["E-..."], "counter_evidence_ids": ["E-..."], "coverage_status": "supported|coverage_limited|contested", "validation_advice": "string", "uncertainty": "string"}], "cross_opportunities": [{"core_discipline": "核心学科", "expansion_discipline": "首个拓展学科；单学科机会留空", "participating_disciplines": [{"discipline": "学科名称", "role": "core|expansion", "reason": "此学科为何参与当前机会", "evidence_ids": ["E-..."]}], "core_discipline_reason": "标签来源或推断原因", "expansion_discipline_reason": "标签来源或推断原因", "transferable_method": "具体方法中文名", "source_method": "文献中的方法原文名称", "method_evidence": [{"evidence_id": "E-...", "chunk_id": "全文片段时填写", "source_method": "方法原文名称", "quote": "逐字原文引文"}], "validation_conditions": "适用性验证条件", "source_theme_id": "当前问题所属主题ID", "target_theme_id": "仅涉及一个主题时与source_theme_id相同", "description": "从研究问题与方法形成机会，再说明各参与学科贡献；可单主题、单学科或多个学科，不强制两两组合", "relation_basis": "为何有关以及文献具体支持哪一判断", "research_opportunity": "研究机会", "evidence_ids": ["E-..."], "counter_evidence_ids": [], "uncertainty": "限制"}]},
            }
        # Provenance remains explicit without repeating descriptions at every
        # level of the output. This leaves room for complete literal quotes.
        for schema in analysis_payload['output_schema']['cross_opportunities']:
            for field in ('expansion_discipline', 'expansion_discipline_reason', 'core_discipline_reason', 'relation_basis', 'counter_evidence_ids', 'uncertainty'):
                schema.pop(field, None)
        analysis_payload['response_limits'] += '同一判断不在description和research_opportunity中重复；每条机会只保留必要的方法引文。'
        # Persist bounded content attempts. A service interruption may retry
        # the request, but rejected content cannot restart the same loop.
        artifact = state.stage_artifacts.setdefault('crosslink_gap', {})
        attempts = artifact.setdefault('generation_attempts', [])
        model_payload = artifact.get('generation_payload')
        if 'initial' not in attempts:
            model_payload = await self._stage_model_json(
                state, callback,
                stage_key="crosslink_gap", prompt_version="topic-opportunities-v4",
                purpose="交叉机会、研究空白、反证与覆盖不足分析",
                payload=analysis_payload,
                expected_keys={"gaps", "cross_opportunities"},
                evidence_refs=[item.evidence_id for item in state.evidence_pool[:50]],
                max_tokens=6000,
            )
            if model_payload is None:
                raise_for_transport(state, 'crosslink_gap')
            attempts.append('initial')
            artifact['generation_payload'] = model_payload
            await self._notify(state, callback)
        from .discovery import method_sources, primary_method_sources, opportunity_theme_ids
        proposals = (model_payload or {}).get("cross_opportunities", [])
        if model_payload is None:
            raise_for_transport(state, 'crosslink_gap')
        verified_proposals = [item for item in [*saved_proposals, *proposals] if isinstance(item, dict)
            and method_sources(state, str(item.get('source_method') or item.get('transferable_method') or ''), item.get('method_evidence') or [])]
        state.stage_artifacts.setdefault('crosslink_gap', {})['verified_opportunities'] = verified_proposals[:3]
        grounded = any(
            isinstance(item, dict) and (method_sources(state, str(item.get("transferable_method") or ""), item.get("method_evidence") or [])
            or primary_method_sources(state, str(item.get("transferable_method") or ""), item.get("evidence_ids") or []))
            for item in proposals
        )
        if ((not grounded or len(verified_proposals) < len(proposals)) and 'repair' not in attempts
                and state.analysis_policy.mode == "deep" and state.analysis_policy.model_enabled
                and any(row.get("abstract") for row in compact["evidence"])):
            reviewed = await self._stage_model_json(
                state, callback, stage_key="crosslink_gap", prompt_version="topic-opportunities-review-v1",
                purpose="复核文献中的具体方法与研究机会",
                payload={**analysis_payload, "previous_opportunities": proposals, "preserve_verified": verified_proposals,
                         "review_instruction": "上次没有得到可定位的方法来源。重新阅读所给摘要及全文，围绕用户科学问题提炼最多3条不同的可验证研究机会，不能只做主题配对。已有方法及先例并不妨碍提出新的比较问题。每条必须直接复制原文quote和其中的方法词source_method，引用真实evidence_id；不编造引文、学科或效果。确无可借鉴的具体方法才留空。"},
                expected_keys={"gaps", "cross_opportunities"},
                evidence_refs=[item["evidence_id"] for item in compact["evidence"]], max_tokens=6000,
            )
            if reviewed is None:
                raise_for_transport(state, 'crosslink_gap')
            attempts.append('repair')
            if isinstance(reviewed, dict) and reviewed.get("cross_opportunities"):
                model_payload = reviewed
            elif model_payload:
                state.stage_artifacts.setdefault("crosslink_gap", {})["analysis"] = model_payload
            artifact['generation_payload'] = model_payload
            await self._notify(state, callback)
        if not isinstance(model_payload, dict) and state.analysis_policy.mode == "deep" and state.analysis_policy.model_enabled:
            from .recovery import GenerationRecoveryRequired
            raise GenerationRecoveryRequired("crosslink_gap", "model_response", "研究机会正在恢复生成，已保留文献与分析。")
        if model_payload and verified_proposals:
            proposed = [*verified_proposals, *model_payload.get('cross_opportunities', [])]
            unique = {}
            for row in proposed:
                if isinstance(row, dict):
                    unique.setdefault((row.get('transferable_method'), row.get('research_opportunity')), row)
            model_payload['cross_opportunities'] = list(unique.values())[:3]
        if model_payload:
            known_ids = {e.evidence_id for e in state.evidence_pool}
            nodes_by_id = {node.theme_id: node for node in state.theme_tree.nodes}
            for item in model_payload.get("cross_opportunities", [])[:3]:
                if not isinstance(item, dict):
                    continue
                left_id, right_id = opportunity_theme_ids(state, item)
                description = str(item.get("description") or item.get("relation_basis") or item.get("research_opportunity") or "").strip()
                if left_id not in nodes_by_id or right_id not in nodes_by_id or not description:
                    continue
                support_ids = list(dict.fromkeys(str(value) for value in [*item.get("evidence_ids", []), *[source.get("evidence_id") for source in item.get("method_evidence", []) if isinstance(source, dict)]] if str(value) in known_ids))
                counter_ids = [str(value) for value in item.get("counter_evidence_ids", []) if str(value) in known_ids and str(value) not in support_ids]
                proposed_method = str(item.get("transferable_method") or "").strip()
                link = next((link for link in state.crosslinks if {link.source_theme_id, link.target_theme_id} == {left_id, right_id}
                             and (not link.transferable_method or link.transferable_method == proposed_method)
                             and (not link.research_opportunity or link.research_opportunity == str(item.get("research_opportunity") or ""))), None)
                if link is None:
                    link = CrossLink(crosslink_id=f"XL-{len(state.crosslinks)+1:03d}", source_theme_id=left_id, target_theme_id=right_id,
                                     title=str(item.get("research_opportunity") or nodes_by_id[left_id].label)[:200], description=description[:1000], status="candidate")
                    state.crosslinks.append(link)
                item["crosslink_id"] = link.crosslink_id
                link.description = description[:1000]
                link.transferable_method = proposed_method[:1000]
                link.relation_basis = str(item.get("relation_basis") or link.relation_basis or "当前主题分析提出的关系线索")[:1000]
                link.research_opportunity = str(item.get("research_opportunity") or link.research_opportunity)[:1000]
                link.support_evidence_ids = list(dict.fromkeys([*link.support_evidence_ids, *support_ids]))[:20]
                link.counter_evidence_ids = counter_ids[:20]
                link.uncertainty = str(item.get("uncertainty") or link.uncertainty or "主题共现不等于已证实机制；当前仅作为研究机会线索")[:500]
                link.sample_count = len(link.support_evidence_ids)
                # Explanatory model prose cannot independently certify a mechanism.
                link.status = "candidate"
                # Different methods can address the same theme or theme pair.
                # Do not collapse their distinct questions into one generic gap.
                related = [gap for gap in state.gaps if link.crosslink_id in gap.crosslink_ids]
                if not related:
                    related = [GapScore(
                        gap_id=f"G{len(state.gaps) + 1}", label=link.research_opportunity or f"{link.title}的验证边界",
                        theme_ids=list(dict.fromkeys([left_id, right_id])), crosslink_ids=[link.crosslink_id],
                        evidence_ids=list(link.support_evidence_ids), counter_evidence_ids=list(link.counter_evidence_ids),
                        rationale=link.research_opportunity or link.description, uncertainty=link.uncertainty,
                        validation_status="candidate" if link.support_evidence_ids else "coverage_limited",
                    )]
                    state.gaps.extend(related)
                for gap in related:
                    gap.crosslink_ids = list(dict.fromkeys([*gap.crosslink_ids, link.crosslink_id]))
                    gap.evidence_ids = list(dict.fromkeys([*gap.evidence_ids, *link.support_evidence_ids]))[:30]
            gap_by_id = {item.gap_id: item for item in state.gaps}
            for item in model_payload.get("gaps", [])[:12]:
                if not isinstance(item, dict):
                    continue
                gap = gap_by_id.get(str(item.get("gap_id") or ""))
                if gap is None:
                    continue
                statement = str(item.get("gap_statement") or "").strip()
                if statement:
                    gap.label = statement[:400]
                known_ids = {e.evidence_id for e in state.evidence_pool}
                gap.evidence_ids = list(dict.fromkeys([*gap.evidence_ids, *[str(value) for value in item.get("support_evidence_ids", []) if str(value) in known_ids]]))[:30]
                gap.counter_evidence_ids = list(dict.fromkeys([*gap.counter_evidence_ids, *[str(value) for value in item.get("counter_evidence_ids", []) if str(value) in known_ids]]))[:20]
                gap.uncertainty = str(item.get("uncertainty") or gap.uncertainty)[:500]
                advice = str(item.get("validation_advice") or "").strip()
                if advice:
                    gap.rationale = f"{gap.rationale} 验证建议：{advice}"[:1000]
        from .discovery import ground_discovery, scoped_opportunity, sample_finding
        ground_discovery(state, (model_payload or {}).get("cross_opportunities", []))
        from .discovery import selected_opportunities
        artifact = state.stage_artifacts.setdefault('crosslink_gap', {})
        visible = selected_opportunities(state)
        artifact['verified_opportunities'] = [row for row in (model_payload or {}).get('cross_opportunities', [])
            if isinstance(row, dict) and any(link.crosslink_id == row.get('crosslink_id') for link in visible)]
        artifact['generation_status'] = 'ready' if visible else 'no_supported_opportunities' if model_payload and not proposals and not model_payload.get('cross_opportunities') else 'incomplete'
        if artifact['generation_status'] == 'incomplete' and state.analysis_policy.mode == 'deep' and state.analysis_policy.model_enabled:
            from .recovery import GenerationRecoveryRequired
            raise GenerationRecoveryRequired('crosslink_gap', 'source_validation', '研究机会的方法来源校核尚未通过，文献与已有成果已保存。')
        for gap in state.gaps:
            gap.label = sample_finding(gap.label)
            gap.rationale = scoped_opportunity(gap.rationale)
        state.versions.gap_rule_version = "gap-v3-method-opportunities"
        await self._emit_progress(
            state, callback, stage_key="crosslink_gap", step_key="gaps_ranked",
            kind="artifact", title="候选研究空白已分层",
            message="覆盖不足不会被表述为已经证实的研究空白。", progress_percent=92,
            metrics={"候选空白": len(state.gaps), "已验证": sum(item.validation_status == "validated" for item in state.gaps), "覆盖不足": sum(item.validation_status == "coverage_limited" for item in state.gaps)},
            artifact_refs=[item.gap_id for item in state.gaps],
        )

    async def _candidate_generation(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        candidates: list[TopicCandidate] = []
        target = max(3, min(5, state.input_profile.desired_candidate_count))
        documents = state.input_profile.documents
        material_tokens = self._material_tokens(state)
        gaps = sorted(state.gaps, key=lambda item: item.opportunity_space, reverse=True) or [
            GapScore(gap_id="G1", label="覆盖不足待验证方向", theme_ids=[node.theme_id for node in state.theme_tree.nodes], validation_status="coverage_limited")
        ]
        # Candidate archetypes are only a scaffold. The actual wording, evidence and
        # route are derived from the selected gap, user material and paper overlap.
        archetypes = [
            ("方法改进", "围绕现有基线的误差来源和方法瓶颈设计可消融的改进方案"),
            ("数据与验证", "围绕样本覆盖、标签质量或基准缺口建立可复现的验证集和评测 protocol"),
            ("跨场景应用", "围绕用户指定场景检验方法迁移、鲁棒性与边界条件"),
            ("可靠性治理", "围绕不确定性、失败案例、偏差或合规约束建立可审计机制"),
            ("工程转化", "围绕资源、周期和部署条件验证原型的实际可用性"),
        ]
        used_signatures: set[str] = set()
        for index in range(target):
            gap = gaps[index % len(gaps)]
            theme_nodes = [node for node in state.theme_tree.nodes if node.theme_id in gap.theme_ids]
            theme_labels = [node.label for node in theme_nodes]
            subject = "、".join(theme_labels[:2]) or state.input_profile.title or state.raw_user_input[:32]
            direction, route = archetypes[index % len(archetypes)]
            candidate_evidence = self._candidate_evidence(state, gap, subject, material_tokens, limit=5)
            evidence_ids = [item.evidence_id for item in candidate_evidence]
            representative_ids = evidence_ids[:5]
            external_ids = self._candidate_external_evidence(state, subject, limit=3)
            material_refs = self._candidate_material_refs(documents, material_tokens, subject)
            if not material_refs:
                material_refs = ["M-INPUT"]
            signature = self._term_key(f"{subject} {direction} {' '.join(evidence_ids)}")
            if signature in used_signatures:
                direction = f"{direction}（边界验证）"
            used_signatures.add(signature)
            evidence_insufficient = not evidence_ids or not documents or material_refs == ["M-INPUT"]
            title = self._candidate_direction_title(subject, direction, state)
            question = self._candidate_question(subject, direction, state)
            fit = f"用户材料{', '.join(material_refs)}直接涉及{subject}，当前约束为{'; '.join(state.input_profile.resource_constraints) or '尚未给出额外资源限制'}。"
            distinct = f"相较代表文献，拟把{gap.label or '待验证问题'}转化为{direction}问题，并以可复现实验区分增益来源。"
            candidate = TopicCandidate(
                candidate_id=f"C{index + 1}", title=title, research_question=question,
                research_object=subject, hypothesis=f"在用户材料给定的{subject}边界内，{direction}可通过对照实验检验。",
                innovation=distinct, method_route=route,
                validation_path="先复现代表文献基线，再执行消融/对照、跨样本验证和失败判定；若关键数据缺失则降级为小样本可行性试验。",
                data_requirements=self._candidate_data_requirements(state, subject),
                evaluation_metrics=["基线差异", "稳健性", "跨场景/样本泛化", "资源消耗"],
                milestones=["第1阶段：材料与数据核验", "第2阶段：基线复现和方案实现", "第3阶段：验证、失败分析和立项判断"],
                expected_outputs=["可复现实验记录", "候选方法/数据集", "阶段性选题验证报告"],
                resource_requirements=state.input_profile.resource_constraints or ["数据访问权限、可复现实验环境"],
                risks=["代表文献只有摘要或样本较少", "用户材料中的关键假设尚未被独立证据验证"],
                risk_mitigation=["优先补充全文和关键样本核验", "将未确认假设设为预注册验证条件"],
                theme_ids=gap.theme_ids, gap_ids=[gap.gap_id], crosslink_ids=gap.crosslink_ids,
                evidence_ids=evidence_ids, representative_evidence_ids=representative_ids,
                external_evidence_ids=external_ids, user_material_refs=material_refs,
                future_work_signal_ids=gap.future_work_signal_ids[:8], fit_rationale=fit,
                distinctiveness_rationale=distinct, uncertainties=["关键数据可得性需人工确认"] if evidence_insufficient else [],
                validation_status="evidence_insufficient" if evidence_insufficient else ("supported" if len(evidence_ids) >= 2 else "provisional"),
                recommendation_level="pending_evidence" if evidence_insufficient else "exploratory",
            )
            candidate.evidence_links = self._candidate_evidence_links(candidate, state)
            self._concretize_candidate(candidate, state, index)
            candidates.append(candidate)
            await self._emit_progress(
                state, callback, stage_key="candidate_generation", step_key=f"candidate_{index + 1}",
                kind="artifact", title=f"候选选题 {index + 1} 已生成",
                message=candidate.title, progress_percent=round(88 * (index + 1) / target),
                current=index + 1, total=target, unit="候选",
                metrics={"已生成": index + 1, "证据回链": sum(bool(item.evidence_ids) for item in candidates)},
                artifact_refs=[f"C{index + 1}", *evidence_ids[:4]],
            )
        state.candidates = candidates
        model_payload = await self._stage_model_json(
            state, callback,
            stage_key="candidate_generation", prompt_version="topic-candidates-v2",
            purpose="候选研究问题、创新点、方法路线与验证路径深化",
            payload={
                "research_question": state.input_profile.research_question,
                "constraints": state.input_profile.resource_constraints,
                "gaps": [item.model_dump() for item in state.gaps[:10]],
                "draft_candidates": [item.model_dump(exclude={"score_dimension_refs", "evidence_links", "reader_content"}) for item in state.candidates],
                "evidence": [item.model_dump(exclude={"raw"}) for item in state.evidence_pool[:40]],
                "fulltext_chunks": [item.model_dump() for item in state.evidence_chunks[:60]],
                "convergence_policy": "禁止原主题加通用后缀。每题明确研究对象、待检验假设、方法/数据路线、样本或应用边界、量化验证指标；题目之间在对象、机制或场景上有实质区别。证据只能说明实际支持的内容，研究方案是待验证假设。reader_content面向用户自然说明该选题为何值得考虑、研究对象/核心问题/方法路线和相关文献的共同依据；不同候选按各自实际内容组织，不重复套句。不要输出内部主题、空白或证据编号作为正文，不写摘要/全文范围诊断、尚未证明等兜底句。文献依据只可引用candidate_evidence中supports或contradicts关系的来源；每个文本项的evidence_ids必须对应实际支撑该项的文献。没有实质相关文献时，文献依据留空。",
                "candidate_evidence": [{"candidate_id": candidate.candidate_id, "sources": [{"evidence_id": link.evidence_id, "relation": link.relation, "title": next((source.title for source in state.evidence_pool if source.evidence_id == link.evidence_id), ""), "abstract": next((source.abstract for source in state.evidence_pool if source.evidence_id == link.evidence_id), ""), "excerpt": next((source.excerpt for source in state.evidence_pool if source.evidence_id == link.evidence_id), "")} for link in candidate.evidence_links if link.relation in {"supports", "contradicts"}]} for candidate in state.candidates],
                "output_preference": state.input_profile.output_preference,
                "preference_policy": "balanced 形成3个面向当前研究条件的推荐选题及2个拓展性探索方向；recommended 全部聚焦当前条件下可开展的研究设计；exploratory 全部聚焦拓展性假设。每题必须有各自的研究问题、方法、对象与验证设计，不得用重复通用理由区分。推荐用途不表示研究假设已获验证，不改变内部证据等级。",
                "materials": self._model_materials(state),
                "output_schema": {"candidates": [{"candidate_id": "C1", "title": "string", "research_object": "string", "sample_boundary": "string", "hypothesis": "string", "research_question": "string", "innovation": "string", "method_route": "string", "validation_path": "string", "data_requirements": ["string"], "evaluation_metrics": ["string"], "risks": ["string"], "risk_mitigation": ["string"], "evidence_ids": ["E-..."], "reader_content": {"reason": {"text": "自然、具体且与其他候选有区别的选题理由", "evidence_ids": ["E-..."]}, "research_content": {"research_object": {"text": "string", "evidence_ids": ["E-..."]}, "core_question": {"text": "string", "evidence_ids": ["E-..."]}, "method_route": {"text": "string", "evidence_ids": ["E-..."]}}, "evidence_summary": {"text": "只综合实质相关文献；无依据时留空", "evidence_ids": ["E-..."]}}, "uncertainties": ["string"]}]},
            },
            expected_keys={"candidates"},
            evidence_refs=[item.evidence_id for item in state.evidence_pool[:60]],
            max_tokens=6500,
        )
        if model_payload:
            by_id = {item.candidate_id: item for item in state.candidates}
            allowed = {item.evidence_id for item in state.evidence_pool} | {item.evidence_id for item in state.external_evidence}
            for item in model_payload.get("candidates", [])[:5]:
                if not isinstance(item, dict):
                    continue
                candidate = by_id.get(str(item.get("candidate_id") or ""))
                if candidate is None:
                    continue
                for field_name in ("title", "research_object", "sample_boundary", "hypothesis", "research_question", "innovation", "method_route", "validation_path"):
                    value = str(item.get(field_name) or "").strip()
                    if value:
                        setattr(candidate, field_name, value[:1600])
                for field_name in ("data_requirements", "evaluation_metrics", "risks", "risk_mitigation", "uncertainties"):
                    values = [str(value)[:300] for value in item.get(field_name, []) if str(value).strip()]
                    if values:
                        setattr(candidate, field_name, values[:12])
                returned_refs = [str(value) for value in item.get("evidence_ids", []) if str(value) in allowed]
                if returned_refs:
                    candidate.evidence_ids = list(dict.fromkeys(returned_refs))[:12]
                    candidate.representative_evidence_ids = candidate.evidence_ids[:5]
                candidate.evidence_links = self._candidate_evidence_links(candidate, state)
                candidate.reader_content = self._normalize_candidate_reader_content(
                    candidate, item.get("reader_content"), state,
                )
        for candidate in state.candidates:
            candidate.title = self._candidate_direction_title(
                candidate.research_object or state.input_profile.title,
                self._direction_from_candidate(candidate), state,
            ) if self._is_generic_candidate_title(candidate.title) else candidate.title
            candidate.evidence_links = self._candidate_evidence_links(candidate, state)
            if not candidate.reader_content:
                candidate.reader_content = self._normalize_candidate_reader_content(candidate, {}, state)
        self._validate_convergence(state)
        for candidate in state.candidates:
            if len(candidate.title) <= 48:
                candidate.display_title = candidate.title
            elif self._is_generic_candidate_title(candidate.title):
                labels = [n.display_label or n.label[:18] for n in state.theme_tree.nodes if n.theme_id in candidate.theme_ids]
                candidate.display_title = f"探索方向：{'／'.join(labels[:2])[:30]}的{self._direction_from_candidate(candidate)}"
            else:
                candidate.display_title = re.split(r'[：:；;。]', candidate.title)[0][:48]
        self._assign_direction_types(state)

    @staticmethod
    def _normalize_candidate_reader_content(candidate: TopicCandidate, proposed: Any, state: TopicSelectionState) -> dict[str, Any]:
        evidence_ids = {item.evidence_id for item in state.evidence_pool}
        allowed = evidence_ids & set(candidate.evidence_ids)
        substantive = {
            link.evidence_id for link in candidate.evidence_links
            if link.relation in {"supports", "contradicts"} and link.evidence_id in evidence_ids
        }
        normalized = normalize_candidate_reader_content(
            proposed,
            allowed_evidence_ids=allowed,
            substantive_evidence_ids=substantive,
        )
        fallback = normalize_candidate_reader_content(
            {
                "reason": {"text": candidate.innovation or candidate.distinctiveness_rationale or "", "evidence_ids": []},
                "research_content": {
                    "research_object": {"text": candidate.research_object, "evidence_ids": []},
                    "core_question": {"text": candidate.research_question or candidate.hypothesis, "evidence_ids": []},
                    "method_route": {"text": candidate.method_route, "evidence_ids": []},
                },
                "evidence_summary": {"text": "", "evidence_ids": []},
            },
            allowed_evidence_ids=allowed,
            substantive_evidence_ids=substantive,
        )
        if not normalized["reason"]["text"]:
            normalized["reason"] = fallback["reason"]
        for key, fallback_item in fallback["research_content"].items():
            if not normalized["research_content"][key]["text"]:
                normalized["research_content"][key] = fallback_item
        return normalized

    async def _scoring_ranking(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        policy = state.scoring_policy or self._build_scoring_policy("balanced", {})
        weights = policy.active_weights
        scores: list[ScoreBreakdown] = []
        gap_by_id = {item.gap_id: item for item in state.gaps}
        for candidate in state.candidates:
            gap = gap_by_id.get(candidate.gap_ids[0]) if candidate.gap_ids else None
            evidence = len(candidate.representative_evidence_ids or candidate.evidence_ids)
            material = len(candidate.user_material_refs)
            external = len(candidate.external_evidence_ids)
            recent = sum(1 for item in state.evidence_pool if item.evidence_id in candidate.evidence_ids and item.year and item.year >= datetime.now().year - 3)
            direction_factor = {"方法改进": 0.08, "数据与验证": 0.12, "跨场景应用": 0.06, "可靠性治理": 0.10, "工程转化": 0.04}
            route_bonus = next((value for key, value in direction_factor.items() if key in candidate.title), 0.0)
            dimensions = [
                self._score_dimension("problem_fit", "用户问题匹配度", 0.55 + 0.08 * min(3, len(candidate.theme_ids)) + 0.05 * min(2, material), 20, weights["problem_fit"], candidate, reason=f"覆盖用户问题“{state.input_profile.title or state.raw_user_input[:28]}”，材料回链 {', '.join(candidate.user_material_refs) or '无'}。"),
                self._score_dimension("evidence_support", "证据支持度", min(1.0, evidence / 5 * 0.75 + external / 3 * 0.1 + (0.15 if gap and gap.validation_status == "validated" else 0)), 20, weights["evidence_support"], candidate, reason=f"关联 {evidence} 篇去重科学文献和 {external} 条动态证据，定位完整性决定得分。"),
                self._score_dimension("feasibility_data", "可行性与数据可得性", 0.48 + route_bonus + (0.18 if state.input_profile.resource_constraints else 0) + (0.18 if candidate.data_requirements else 0) + (0.08 if any(item.fulltext_available for item in state.evidence_pool if item.evidence_id in candidate.evidence_ids) else 0), 15, weights["feasibility_data"], candidate, reason=f"资源约束为{'; '.join(state.input_profile.resource_constraints) or '未明确'}，数据要求 {len(candidate.data_requirements)} 项。"),
                self._score_dimension("novelty_opportunity", "新颖性与机会度", min(1.0, 0.42 + (gap.opportunity_space if gap else 0.15) * 0.55), 15, weights["novelty_opportunity"], candidate, reason=f"机会判断来自{gap.label if gap else '覆盖不足'}及其关系级证据；低密度未直接等同于无人研究。"),
                self._score_dimension("timeliness", "时效性", min(1.0, 0.35 + recent * 0.1 + (0.15 if state.freshness_snapshot.newest_external_at else 0)), 10, weights["timeliness"], candidate, reason=f"近三年相关文献 {recent} 篇，动态补充 {external} 条，动态证据不与正式论文混称。"),
                self._score_dimension("validation_clarity", "验证清晰度", min(1.0, (0.72 if candidate.validation_path and candidate.evaluation_metrics else 0.35) + route_bonus / 2), 10, weights["validation_clarity"], candidate, reason=f"已定义基线、对照/消融、失败判定和指标：{', '.join(candidate.evaluation_metrics)}。"),
                self._score_dimension("potential_value", "预期价值", min(1.0, 0.45 + 0.08 * len(candidate.expected_outputs) + (0.08 if candidate.crosslink_ids else 0)), 10, weights["potential_value"], candidate, reason=f"预期产出 {', '.join(candidate.expected_outputs[:2])}，价值仍受验证结果约束。"),
            ]
            for dimension in dimensions:
                dimension.score = round(min(dimension.max_score, dimension.max_score * max(0.0, min(1.0, dimension.score))), 2)
            total = sum(item.score / item.max_score * item.weight * 100 for item in dimensions if item.max_score)
            missing = ["独立文献证据"] if not candidate.evidence_ids else []
            missing += ["用户材料具体数据编号"] if candidate.user_material_refs == ["M-INPUT"] else []
            status = "confirmed" if candidate.validation_status == "supported" else "pending_confirmation" if candidate.validation_status == "evidence_insufficient" else "provisional"
            scores.append(ScoreBreakdown(
                candidate_id=candidate.candidate_id, value=round(total, 2), total=round(total, 2), score_range=[round(max(0, total - (8 if missing else 3)), 2), round(min(100, total + (8 if missing else 3)), 2)], ranking_status=status, weights=weights, missing_fields=missing, explanation="分数由固定权重和结构化证据计算；模型只提供可读理由，不覆盖数值。", dimensions=dimensions, default_total=round(total, 2), weight_adjustment=policy.adjustment_reason,
            ))
            await self._emit_progress(
                state, callback, stage_key="scoring_ranking", step_key=f"score_{candidate.candidate_id}",
                kind="metric", title=f"{candidate.candidate_id} 结构化校验完成",
                message="已完成内部一致性校验；用户报告仅展示证据覆盖、适配性、可行性和风险。", progress_percent=round(85 * len(scores) / max(1, len(state.candidates))),
                current=len(scores), total=len(state.candidates), unit="候选",
                metrics={"证据数": evidence, "校验状态": "需补证据" if missing else "可研判"},
                artifact_refs=[candidate.candidate_id],
            )
        state.score_breakdowns = sorted(scores, key=lambda item: (item.ranking_status == "pending_confirmation", -item.total))
        model_payload = await self._stage_model_json(
            state, callback,
            stage_key="scoring_ranking", prompt_version="topic-deliberation-v2",
            purpose="候选适配性、证据覆盖、可行性、验证清晰度与风险研判",
            payload={
                "research_question": state.input_profile.research_question,
                "constraints": state.input_profile.resource_constraints,
                "candidates": [item.model_dump(exclude={"score_dimension_refs"}) for item in state.candidates],
                "output_schema": {"deliberations": [{"candidate_id": "C1", "evidence_coverage": "充分|有限|不足", "problem_fit": "string", "feasibility": "string", "validation_clarity": "string", "risks": ["string"], "validation_priority": "string", "evidence_ids": ["E-..."]}], "portfolio_advice": "string"},
            },
            expected_keys={"deliberations", "portfolio_advice"},
            evidence_refs=[item.evidence_id for item in state.evidence_pool[:60]],
            max_tokens=4200,
        )
        if model_payload:
            by_id = {item.candidate_id: item for item in state.candidates}
            for item in model_payload.get("deliberations", [])[:5]:
                if not isinstance(item, dict):
                    continue
                candidate = by_id.get(str(item.get("candidate_id") or ""))
                if candidate is None:
                    continue
                fit = str(item.get("problem_fit") or "").strip()
                if fit:
                    candidate.fit_rationale = fit[:1000]
                extra_risks = [str(value)[:300] for value in item.get("risks", []) if str(value).strip()]
                candidate.risks = list(dict.fromkeys([*candidate.risks, *extra_risks]))[:12]
        state.versions.scoring_version = "score-v1-deterministic"
        await self._emit_progress(
            state, callback, stage_key="scoring_ranking", step_key="ranking_ready",
            kind="artifact", title="候选研判与验证编排已完成",
            message="已形成证据覆盖、问题适配、可行性、验证清晰度和风险的定性比较。", progress_percent=94,
            metrics={"候选": len(state.score_breakdowns), "证据充分": sum(item.ranking_status == "confirmed" for item in state.score_breakdowns), "需补证据": sum(item.ranking_status == "pending_confirmation" for item in state.score_breakdowns)},
            artifact_refs=[item.candidate_id for item in state.score_breakdowns],
        )

    async def _quality_gate(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        blockers: list[str] = []
        warnings: list[str] = []
        checked = ["输入非空", "候选题数量为 3-5", "候选题包含研究对象、研究问题和验证路径", "候选题与证据/空白 ID 回链", "证据锁定后禁止外部调用", "来源类型和动态证据边界", "报告包含限制说明", "图表数据快照可追溯"]
        if not state.raw_user_input.strip():
            blockers.append("研究方向为空")
        if not 3 <= len(state.candidates) <= 5:
            blockers.append("候选选题数量必须为 3-5 个")
        evidence_ids = {item.evidence_id for item in state.evidence_pool} | {item.evidence_id for item in state.external_evidence}
        chunk_refs = {(item.evidence_id, item.chunk_id) for item in state.evidence_chunks}
        theme_ids = {item.theme_id for item in state.theme_tree.nodes}
        gap_ids = {item.gap_id for item in state.gaps}
        for candidate in state.candidates:
            if not candidate.research_object or not candidate.research_question or not candidate.validation_path:
                blockers.append(f"{candidate.candidate_id} 字段不完整")
            if any(item not in evidence_ids for item in candidate.evidence_ids):
                blockers.append(f"{candidate.candidate_id} 存在无法回链的证据编号")
            if not candidate.theme_ids or not candidate.gap_ids or any(ref not in theme_ids for ref in candidate.theme_ids) or any(ref not in gap_ids for ref in candidate.gap_ids):
                blockers.append(f"{candidate.candidate_id} 缺少拆分主题或研究空白关联")
            if any(link.evidence_id not in evidence_ids for link in candidate.evidence_links):
                blockers.append(f"{candidate.candidate_id} 存在无法回链的证据关联")
            if any((link.evidence_id, chunk_id) not in chunk_refs for link in candidate.evidence_links for chunk_id in link.chunk_ids):
                blockers.append(f"{candidate.candidate_id} 存在无法定位的全文片段")
        normalized_titles = [re.sub(r"\W+", "", item.title.lower()) for item in state.candidates]
        if len(normalized_titles) != len(set(normalized_titles)):
            blockers.append("候选选题语义标题重复")
        if not state.evidence_pool:
            warnings.append("NSTL 当前未返回样本，研究空白仅能标记为覆盖不足，不作为已证实结论")
        if state.query_plan.coverage_warnings:
            warnings.append("部分检索分支不可用，详见附录执行记录")
        if any(item.status == "failed" for item in state.source_queries):
            warnings.append("部分三路来源查询失败，动态结论按可用来源降级")
        if state.constraint_card.unresolved:
            warnings.append("输入边界仍有待确认字段")
        if state.external_evidence:
            warnings.append("联网/云端材料已独立标注为动态外部证据，不能与 NSTL 论文证据混称")
        if any(link.counter_evidence_ids for link in state.crosslinks):
            warnings.append("存在冲突或反证关系，系统已降低相关结论强度并保留对照证据")
        if any(re.search(r"无人研究|首次提出|完全解决", candidate.title + candidate.innovation + candidate.research_question) for candidate in state.candidates):
            warnings.append("候选题包含强断言，系统已按证据范围降级并禁止作为确定性结论")
        if any(item.external_request_allowed and not item.redaction_applied for item in state.source_queries if item.source_kind in {"web", "cloud_model"}):
            blockers.append("外部查询缺少脱敏记录")
        rejected_evidence_refs = sorted({
            str(ref)
            for artifact in state.stage_artifacts.values()
            for ref in artifact.get("rejected_evidence_refs", [])
            if str(ref).strip()
        })
        if rejected_evidence_refs:
            warnings.append(f"模型返回 {len(rejected_evidence_refs)} 个未锁定证据编号，系统已自动丢弃")
        citation_total = sum(bool(item.evidence_ids) for item in state.candidates)
        citation_completeness = citation_total / max(1, len(state.candidates))
        if not state.evidence_lock.locked:
            blockers.append("证据池尚未锁定")
        model_payload = await self._stage_model_json(
            state, callback,
            stage_key="quality_gate", prompt_version="topic-quality-v2",
            purpose="强断言、证据回链、字段完整性与发布风险复核",
            payload={
                "boundary": state.constraint_card.model_dump(),
                "candidates": [item.model_dump() for item in state.candidates],
                "gap_analysis": state.stage_artifacts.get("crosslink_gap", {}).get("analysis", {}),
                "deterministic_checks": {"blockers": blockers, "warnings": warnings, "citation_completeness": citation_completeness},
                "output_schema": {"findings": [{"severity": "info|warning|blocker", "message": "string", "candidate_id": "string", "evidence_ids": ["E-..."]}], "strong_claims": ["string"], "release_note": "string"},
            },
            expected_keys={"findings"},
            evidence_refs=[item.evidence_id for item in state.evidence_pool[:60]],
            max_tokens=3000,
        )
        if model_payload:
            for finding in model_payload.get("findings", [])[:20]:
                if not isinstance(finding, dict):
                    continue
                message = str(finding.get("message") or "").strip()
                if message:
                    warnings.append(message[:400])
        quality_rejected_refs = sorted(state.stage_artifacts.get("quality_gate", {}).get("rejected_evidence_refs", []))
        if quality_rejected_refs:
            warnings.append(f"质量门模型返回 {len(quality_rejected_refs)} 个未锁定证据编号，系统已自动丢弃")
        critical_degraded = any(
            call.status == "degraded" and call.stage_key in {"landscape_trend", "crosslink_gap", "candidate_generation", "scoring_ranking", "quality_gate"}
            for call in state.model_calls
        )
        if critical_degraded:
            warnings.append("一个或多个核心分析阶段使用了确定性降级结果，报告已降低相应结论强度。")
        warnings = list(dict.fromkeys(warnings))
        findings: list[QualityFinding] = []
        for index, message in enumerate(blockers, start=1):
            findings.append(QualityFinding(code=f"BLOCK-{index:03d}", severity="blocker", message=message, stage_key="quality_gate", anchor="quality-blockers"))
        for index, message in enumerate(warnings, start=1):
            stage_key = "retrieval_evidence" if "检索" in message or "摘要" in message or "全文" in message else "quality_gate"
            findings.append(QualityFinding(code=f"WARN-{index:03d}", severity="warning", message=message, stage_key=stage_key, anchor="quality-warnings"))
        for query in state.source_queries:
            if query.status == "failed":
                findings.append(QualityFinding(code=f"QUERY-{query.query_id}", message=f"查询 {query.query_id} 失败：{query.failure_reason or '来源不可用'}", stage_key="retrieval_evidence", query_id=query.query_id, anchor=f"query-{query.query_id}"))
        for record in state.process_trace.get("retrieval_evidence", []):
            if record.get("code") in {"fulltext_unavailable", "fulltext_partial"}:
                findings.append(QualityFinding(code=str(record["code"]), message=str(record["message"]), stage_key="retrieval_evidence", anchor="retrieval-fulltext-degradation"))
        for call in state.model_calls:
            if call.status in {"degraded", "failed"}:
                findings.append(QualityFinding(code=f"CALL-{call.call_id}", message=f"{call.call_id} 模型降级：{call.error_message}", stage_key=call.stage_key, model_call_id=call.call_id, anchor=f"model-{call.call_id}"))
        for finding in findings:
            if finding.anchor == "quality-warnings" and finding.stage_key == "retrieval_evidence":
                finding.anchor = "retrieval-fulltext-degradation" if state.query_plan.coverage_warnings else "execution-records"
        for finding in (model_payload or {}).get("findings", []) if isinstance(model_payload, dict) else []:
            if not isinstance(finding, dict) or not str(finding.get("message") or "").strip():
                continue
            severity = str(finding.get("severity") or "warning")
            if severity == "review":
                severity = "warning"
            findings.append(QualityFinding(
                code=str(finding.get("code") or f"MODEL-{len(findings)+1:03d}"),
                severity=severity if severity in {"info", "warning", "blocker"} else "warning",
                message=str(finding.get("message"))[:500], stage_key=str(finding.get("stage_key") or "quality_gate"),
                query_id=str(finding.get("query_id") or ""), model_call_id=str(finding.get("model_call_id") or ""),
                anchor=str(finding.get("anchor") or "quality-model-findings"), candidate_id=str(finding.get("candidate_id") or ""),
                evidence_ids=[item for item in finding.get("evidence_ids", []) if str(item) in evidence_ids],
            ))
        state.quality_gate = QualityGate(
            passed=not blockers,
            report_eligibility="formal" if not blockers else "blocked",
            blockers=blockers, warnings=warnings, review_required=False, checked_rules=checked,
            evidence_lock_valid=state.evidence_lock.locked and state.evidence_lock.external_calls_blocked,
            source_integrity_valid=all(item.source_kind in {"nstl", "fulltext_library"} and item.title for item in state.evidence_pool),
            citation_completeness=round(citation_completeness, 3), findings=findings,
        )
        self._apply_output_preference(state)
        await self._emit_progress(
            state, callback, stage_key="quality_gate", step_key="rules_checked",
            kind="warning" if warnings or blockers else "metric",
            status="blocked" if blockers else "completed",
            title="质量规则检查完成",
            message="发现阻断项时报告不会被标记为可发布。", progress_percent=88,
            current=len(checked), total=len(checked), unit="规则",
            metrics={"已检查规则": len(checked), "证据限制": len(warnings), "阻断项": len(blockers), "自动质检": "已完成"},
            artifact_refs=["quality_gate"],
        )
        state.review_tasks = []

    @staticmethod
    def _assign_direction_types(state: TopicSelectionState) -> None:
        """Group research designs by user intent without promoting evidence grades."""
        preference = state.input_profile.output_preference
        if preference is None:
            for candidate in state.candidates:
                if candidate.direction_type is not None:
                    continue
                if candidate.title.startswith("探索方向：") or candidate.display_title.startswith("探索方向："):
                    candidate.direction_type = "exploratory"
                elif candidate.convergence_valid or candidate.recommendation_level == "recommended":
                    candidate.direction_type = "recommended"
            return
        quota = {"balanced": min(3, len(state.candidates)), "recommended": len(state.candidates), "exploratory": 0}[preference]
        scores = {item.candidate_id: item.total for item in state.score_breakdowns}
        ordered = sorted(state.candidates, key=lambda item: (item.convergence_valid, scores.get(item.candidate_id, -1)), reverse=True)
        for index, candidate in enumerate(ordered):
            candidate.direction_type = "recommended" if index < quota else "exploratory"

    @staticmethod
    def _apply_output_preference(state: TopicSelectionState) -> None:
        TopicSelectionAgent._assign_direction_types(state)
        preference = state.input_profile.output_preference
        if preference is None:
            return
        quota = {"balanced": 3, "recommended": 5, "exploratory": 0}[preference]
        evidence = {item.evidence_id: item for item in state.evidence_pool}
        gaps = {item.gap_id: item for item in state.gaps}
        order = {item.candidate_id: index for index, item in enumerate(state.score_breakdowns)}
        for candidate in sorted(state.candidates, key=lambda item: order.get(item.candidate_id, 999)):
            supported = (candidate.validation_status == "supported" and candidate.convergence_valid and state.quality_gate.passed
                         and any(gaps[key].validation_status == "validated" for key in candidate.gap_ids if key in gaps)
                         and sum(evidence[key].fulltext_available for key in set(candidate.evidence_ids) if key in evidence) >= 2)
            if supported and quota > 0:
                candidate.recommendation_level = "recommended"
                quota -= 1
            else:
                candidate.recommendation_level = "pending_evidence" if candidate.validation_status == "evidence_insufficient" else "exploratory"
        if quota:
            state.quality_gate.warnings.append(f"输出偏好未达到推荐数量：仍有 {quota} 项缺少足够证据，已保留为探索或待补证据方向")

    async def _report_publish(self, state: TopicSelectionState, callback: StageCallback | None = None) -> None:
        if not state.evidence_lock.locked:
            raise RuntimeError("报告发布前必须完成证据锁定")
        self._restore_distinct_candidate_titles(state)
        # A retry fixes publication failures; retain every scientific gate.
        state.quality_gate.blockers = [message for message in state.quality_gate.blockers if message not in ('选题分析尚未完成来源核对，请重试报告生成。', '选题分析尚未完成来源核对')]
        state.quality_gate.passed = not state.quality_gate.blockers
        state.quality_gate.report_eligibility = "formal" if state.quality_gate.passed else "blocked"
        report_version = f"TopicReportV7-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:6]}"
        citations = {item.evidence_id: index for index, item in enumerate(state.evidence_pool, start=1)}
        state.versions.report_version = report_version
        state.versions.chart_data_snapshot_version = f"chart-v1-{state.checkpoint_revision + 1}"
        state.versions.template_version = "TopicReportV7"
        state.report_snapshot = ReportSnapshot(
            snapshot_id=f"snapshot-{uuid4().hex[:12]}", report_version=report_version,
            evidence_pool_version=state.versions.evidence_pool_version,
            chart_data_snapshot_version=state.versions.chart_data_snapshot_version,
            template_version=state.versions.template_version, chart_version="chart-v11", content_version="content-v9", citation_version="citation-v7", created_at=datetime.now().isoformat(), frozen=False,
            font_policy="REPORT_CJK_FONT/REPORT_CJK_BOLD_FONT 优先；缺少中文字体时图表降级为文字限制说明",
            render_checks={"status": "pending_visual_review", "black_block_guard": True, "empty_chart_guard": True, "letter_page": True, "shared_chart_assets": True},
        )
        await self._emit_progress(
            state, callback, stage_key="report_publish", step_key="snapshot_frozen",
            kind="artifact", title="报告数据快照已准备",
            message="报告、图表和引用将使用同一版本的数据快照。", progress_percent=42,
            metrics={"报告版本": report_version, "引用": len(citations), "证据版本": state.versions.evidence_pool_version},
            artifact_refs=[state.report_snapshot.snapshot_id],
        )
        publication_status = "blocked" if state.quality_gate.report_eligibility != "formal" else "published"
        scores = {row.candidate_id: row.total for row in state.score_breakdowns}
        state.candidates.sort(key=lambda item: (item.direction_type == "recommended" if item.direction_type else item.recommendation_level == "recommended", scores.get(item.candidate_id, -1)), reverse=True)
        state.input_profile.title = self._normalize_concise_title(state.input_profile.title)
        state.report_plan = TopicReportPlan(
            report_version=report_version,
            report_title=f"{state.input_profile.title}智能选题报告",
            executive_summary=self._executive_summary(state),
            limitations=self._public_limitations(state.quality_gate.warnings),
            citation_map=citations,
            citation_links={item.evidence_id: evidence_url(item.model_dump()) for item in state.evidence_pool},
            template_version="TopicReportV7",
            quality_status="eligible" if state.quality_gate.report_eligibility == "formal" else "blocked",
            report_mode="full_report",
            sections=[],
            chart_specs=[],
            source_cutoff_at=state.source_policy.evidence_cutoff_at,
            source_status_summary={"queries": len(state.source_queries), "completed": sum(item.status == "completed" for item in state.source_queries), "failed": sum(item.status == "failed" for item in state.source_queries)},
            evidence_lock_status="locked",
            external_evidence_counts={"web": state.freshness_snapshot.web_count, "cloud_model": state.freshness_snapshot.cloud_count},
            provisional_page_baseline="报告总篇幅以10页以内为目标",
            publication_status="draft",
            snapshot_id=state.report_snapshot.snapshot_id,
            cover_metadata={
                "title": f"{state.input_profile.title}智能选题报告",
                "generated_at": datetime.now().strftime("%Y-%m-%d"),
                "generation_method": "主题、文献、交叉机会与候选研判",
                "data_source": f"文献 {len(state.evidence_pool)} 篇",
                "sample_size": len(state.evidence_pool),
                "conclusion_strength": "有证据支持的推荐方向" if any(item.recommendation_level == "recommended" for item in state.candidates) and state.quality_gate.passed else "探索性结论，候选方向受证据边界限制",
                "evidence_cutoff_at": state.source_policy.evidence_cutoff_at or "未指定",
                "source_counts": {
                    "文献": len(state.evidence_pool),
                    **({"Web": state.freshness_snapshot.web_count} if state.freshness_snapshot.web_count else {}),
                    **({"CloudModel": state.freshness_snapshot.cloud_count} if state.freshness_snapshot.cloud_count else {}),
                },
                "status": state.quality_gate.report_eligibility,
                "review_status": "自动质检已完成",
            },
            body_page_target="报告总篇幅以10页以内为目标",
            page_metrics={"target_body_pages": [4, 6]},
            appendix_policy="evidence-inline",
        )
        state.report_plan.chart_specs = [spec for spec in self._chart_specs(state) if spec["key"] in {"theme_network", "trend_comparison", "discipline_network"} and spec.get("data_status") == "ready"]
        state.report_plan.sections = self._report_sections(state)
        state.report_plan.section_specs = self._section_specs(state)
        state.report_plan.table_specs = self._table_specs(state)
        from .scholarly_report import compose
        await compose(self, state, callback)
        if state.confirmation.needed:
            state.report_snapshot = ReportSnapshot(frozen=False)
            state.report_plan.sections = []
            state.output_markdown = ""
            return
        for field in ("sections", "section_specs", "table_specs", "chart_specs", "executive_summary", "limitations", "cover_metadata"):
            setattr(state.report_plan, field, reader_report(getattr(state.report_plan, field)))
        for section in state.report_plan.sections:
            section["content_hash"] = hashlib.sha256(str(section.get("content") or "").encode("utf-8")).hexdigest()
        state.report_plan.chart_manifest = state.report_plan.chart_specs
        state.report_snapshot.chart_manifest = [{"key": item["key"], "chart_version": item["chart_version"], "data_status": item["data_status"]} for item in state.report_plan.chart_specs]
        snapshot_hash = hashlib.sha256(json.dumps({"sections": state.report_plan.sections, "tables": state.report_plan.table_specs, "charts": state.report_plan.chart_specs, "citations": citations}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        state.report_snapshot.format_hashes = {key: snapshot_hash for key in ("content", "md", "html", "docx", "pdf")}
        state.report_plan.toc_entries = [ReportTocEntry(key=item["key"], title=item["title"], level=1, is_appendix=item["key"].startswith("appendix")) for item in state.report_plan.sections]
        state.report_plan.format_consistency = {"report_version": report_version, "evidence_pool_version": state.versions.evidence_pool_version, "chart_data_snapshot_version": state.versions.chart_data_snapshot_version, "template_version": state.versions.template_version, "content_version": state.report_snapshot.content_version, "citation_version": state.report_snapshot.citation_version, "chart_version": state.report_snapshot.chart_version, "formats": ["md", "html", "docx", "pdf"]}
        state.report_plan.evidence_package_manifest = []
        await self._emit_progress(
            state, callback, stage_key="report_publish", step_key="report_assembled",
            kind="artifact", title="章节、表格与图表已装配",
            message="正在执行最终版本映射和发布资格检查。", progress_percent=82,
            metrics={"章节": len(state.report_plan.sections), "表格": len(state.report_plan.table_specs), "图表": len(state.report_plan.chart_specs)},
            artifact_refs=[item.get("key", "") for item in state.report_plan.chart_specs if item.get("key")],
        )
        if any(not item.get("data_snapshot_version") or item.get("report_version") != report_version for item in state.report_plan.chart_specs):
            state.quality_gate.blockers.append("图表缺少数据快照或报告版本映射")
            state.quality_gate.passed = False
            state.report_plan.quality_status = "blocked"
            state.report_plan.publication_status = "blocked"
        from .recovery import GenerationRecoveryRequired
        if state.report_plan.quality_status == 'blocked':
            raise GenerationRecoveryRequired('report_publish', 'publication_check', '报告版本与发布检查尚未通过。')
        state.report_plan.publication_status = publication_status
        state.report_snapshot.frozen = publication_status == 'published'
        state.report_snapshot.report_plan_snapshot = state.report_plan.model_dump()
        # Publish the stage results and report together. Execution traces stay
        # live; subsequent review/correction work must not rewrite this version.
        result_keys = ("input_profile", "constraint_card", "term_map", "theme_tree",
                       "evidence_pool", "evidence_chunks", "landscape_snapshot",
                       "future_work_signals", "crosslinks", "gaps", "candidates",
                       "score_breakdowns", "stage_artifacts")
        state.report_snapshot.result_data_snapshot = state.model_dump(include=set(result_keys))
        state.output_markdown = self._markdown_report(state)
        fulltext_retrieval_stats = state.output_json.get("fulltext_retrieval_stats")
        state.output_json = {**state.output_json,
            "report_version": report_version,
            "quality_gate": state.quality_gate.model_dump(),
            "versions": state.versions.model_dump(),
            "evidence_count": len(state.evidence_pool),
            "search_trace": state.search_traces,
            "report_plan": state.report_plan.model_dump(),
            "fulltext_retrieval_stats": fulltext_retrieval_stats,
        }
        report_published = state.report_plan.publication_status == "published"
        await self._emit_progress(
            state, callback, stage_key="report_publish", step_key="report_ready",
            kind="artifact", status="completed" if report_published else "blocked",
            title="正式报告已生成" if report_published else "报告尚未发布",
            message="报告及来源引用已保存。" if report_published else "当前结果已保留，请处理任务提示后重试。",
            progress_percent=96,
            metrics={"发布状态": state.report_plan.publication_status, "报告版本": report_version, "章节": len(state.report_plan.sections)},
            artifact_refs=[report_version],
        )

    async def _stage_model_json(
        self,
        state: TopicSelectionState,
        callback: StageCallback | None,
        *,
        stage_key: str,
        prompt_version: str,
        purpose: str,
        payload: dict[str, Any],
        expected_keys: set[str],
        evidence_refs: list[str] | None = None,
        max_tokens: int = 3200,
    ) -> dict[str, Any] | None:
        """Run one auditable analysis call without reopening the evidence pool."""
        trace = next((item for item in state.stage_trace if item.stage_key == stage_key), None)
        started_at = datetime.now().isoformat()
        if state.correction_context.reason:
            payload = {**payload, "correction_context": state.correction_context.model_dump(), "correction_policy": "必须逐项响应补正意见；无法补足的证据明确列为限制，不能通过改写提高推荐等级。"}
        safe_payload = self._sanitize_model_payload(payload)
        input_text = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, default=str)
        call = TopicModelCall(
            call_id=f"TMC-{len(state.model_calls) + 1:04d}",
            stage_key=stage_key,
            prompt_version=prompt_version,
            started_at=started_at,
            input_hash=hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            evidence_refs=list(dict.fromkeys(evidence_refs or []))[:100],
        )
        if trace is not None:
            trace.prompt_version = prompt_version
        disabled_reason = ""
        if state.analysis_policy.mode != "deep":
            disabled_reason = "fast_mode"
        elif not state.analysis_policy.model_enabled:
            disabled_reason = "sensitivity_policy" if state.input_profile.sensitivity_level in {"confidential", "secret"} else "model_disabled"
        if disabled_reason:
            call.status = "disabled"
            call.error_type = disabled_reason
            call.error_message = "当前分析策略未启用阶段模型分析。"
            call.completed_at = datetime.now().isoformat()
            call.duration_ms = 0
            state.model_calls.append(call)
            if trace is not None:
                trace.model_status = "disabled"
                trace.degradation_reason = disabled_reason
                trace.execution_mode = "disabled"
                trace.fallback_reason = disabled_reason
            return None

        if state.analysis_policy.model_preflight_status in {"unavailable", "not_configured"}:
            reason = state.analysis_policy.model_preflight_reason or "model_preflight_failed"
            call.status = "degraded"
            call.profile = state.analysis_policy.model_profile
            call.model = state.analysis_policy.model_name
            call.attempt_count = 0
            call.duration_ms = 0
            call.error_type = "ModelPreflightUnavailable"
            call.error_code = "model_preflight_unavailable"
            call.error_message = self._safe_progress_text(reason, 240)
            call.retry_skipped = True
            call.retry_skip_reason = "startup_preflight_failed"
            call.completed_at = datetime.now().isoformat()
            state.model_calls.append(call)
            if trace is not None:
                trace.model_status = "degraded"
                trace.model_duration_ms = 0
                trace.model_error_code = call.error_code
                trace.degradation_reason = call.error_code
                trace.execution_mode = "deterministic_fallback"
                trace.fallback_reason = call.error_message
            state.stage_artifacts.setdefault(stage_key, {}).update({
                "prompt_version": prompt_version,
                "model_status": "degraded",
                "degradation_reason": call.error_code,
            })
            await self._emit_progress(
                state, callback, stage_key=stage_key, step_key="model_analysis_skipped",
                kind="warning", status="completed", title=f"{purpose}使用规则兜底",
                message="启动预检显示模型不可用，本阶段未重复发送无效请求，系统已使用确定性分析并降低结论强度。",
                progress_percent=max(82, trace.progress_percent if trace else 82),
                metrics={"错误码": call.error_code, "重试": "已跳过"},
            )
            return None

        if trace is not None:
            trace.model_status = "running"
        await self._emit_progress(
            state, callback, stage_key=stage_key, step_key="model_analysis_started",
            title=f"正在进行{purpose}", message="模型正在分析当前阶段的结构化输入与可回链证据。",
            progress_percent=max(15, trace.progress_percent if trace else 15),
            metrics={"提示词版本": prompt_version},
        )
        system_prompt = (
            "你是科技情报与科研选题分析专家。只输出一个 JSON 对象，不输出思维链、过程解释或 Markdown。"
            "所有判断必须受输入数据和证据编号约束；检索覆盖不足不等于无人研究；不得编造证据编号、数值、文献或结论。"
            f"本阶段任务：{purpose}。"
            "以科研评议的书面语言论述具体对象、方法、差异和验证设计；引用对应具体论述。"
            "区分材料目标、已有实测与研究建议，不写空泛意义、口语过渡、机械重复和未经核验的领先性。"
            "读者叙述不使用结论强度、部分支持、字段缺口、服务配置和内部编号堆砌；"
            "只展开有资料依据的维度，重要检索失败保留在执行状态中。"
        )
        started_clock = time.perf_counter()
        accumulated_model_ms = 0
        client: DeepSeekClient | Any | None = None
        owns_client = self.model_client is None
        try:
            client = self.model_client or DeepSeekClient()
            state.analysis_policy.model_profile = str(getattr(client, "profile_name", ""))
            state.analysis_policy.model_name = str(getattr(client, "model", ""))
            state.analysis_policy.model_endpoint = str(getattr(client, "base_url", ""))
            retry_count = state.analysis_policy.max_retries
            stage_budget = state.analysis_policy.max_stage_timeout_seconds
            if stage_key == "crosslink_gap" or prompt_version.startswith(("topic-scholarly-", "topic-source-review-")):
                # Quotes and method provenance require a longer single answer.
                # Give the first attempt a useful budget rather than dividing
                # it into short requests which repeatedly time out.
                stage_budget = max(stage_budget, 150)
                retry_count = 0
            attempt_timeout = max(15, int((stage_budget - (2 * retry_count)) / (retry_count + 1)))
            async def invoke_with_retries() -> Any:
                nonlocal accumulated_model_ms
                last_error: Exception | None = None
                for attempt in range(retry_count + 1):
                    call.attempt_count = attempt + 1
                    if trace is not None:
                        trace.model_call_count = attempt + 1
                    attempt_started = time.perf_counter()
                    duration_recorded = False
                    try:
                        result = await client.generate_json_with_metrics(
                            system_prompt=system_prompt,
                            user_prompt=input_text,
                            timeout_seconds=attempt_timeout,
                            max_retries=0,
                            max_tokens=max_tokens,
                        )
                        accumulated_model_ms += result.metrics.done_ms or round((time.perf_counter() - attempt_started) * 1000)
                        duration_recorded = True
                        candidate_output = result.payload if isinstance(result.payload, dict) else {}
                        from app.agents.report_validation import response_diagnostic, review_envelope
                        diagnostic = response_diagnostic(candidate_output, attempt=attempt + 1,
                            prompt_version=prompt_version, finish_reason=getattr(result.metrics, "finish_reason", None),
                            http_status=getattr(result.metrics, "status_code", None))
                        diagnostics = state.stage_artifacts.setdefault(stage_key, {}).setdefault('model_response_diagnostics', [])
                        diagnostics.append(diagnostic)
                        del diagnostics[:-100]
                        if 'checked' in expected_keys:
                            candidate_output = review_envelope(candidate_output)
                            result.payload = candidate_output
                        if getattr(result.metrics, 'finish_reason', None) == 'length':
                            diagnostic['reason'] = 'output_truncated'
                            result.metrics.error_type = 'ModelOutputTruncated'
                            raise DeepSeekClientError('模型输出达到长度上限', metrics=result.metrics)
                        if stage_key == 'crosslink_gap':
                            for _ in range(3):
                                if isinstance(candidate_output, dict) and expected_keys <= candidate_output.keys():
                                    break
                                wrappers = [candidate_output[k] for k in ('result', 'data', 'analysis')
                                            if isinstance(candidate_output, dict) and isinstance(candidate_output.get(k), dict)]
                                if len(wrappers) != 1:
                                    break
                                candidate_output = wrappers[0]
                            if not isinstance(candidate_output, dict) or not all(isinstance(candidate_output.get(k), list) for k in expected_keys):
                                diagnostic['reason'] = 'schema_validation_failed'
                                raise ValueError('crosslink_schema_validation_failed')
                            result.payload = candidate_output
                        if not isinstance(candidate_output, dict) or (expected_keys and not any(key in candidate_output for key in expected_keys)):
                            diagnostic['reason'] = 'schema_validation_failed'
                            raise ValueError("schema_validation_failed")
                        return result
                    except Exception as exc:
                        last_error = exc
                        metrics = exc.metrics if isinstance(exc, DeepSeekClientError) else None
                        if not duration_recorded:
                            accumulated_model_ms += (metrics.done_ms if metrics else None) or round((time.perf_counter() - attempt_started) * 1000)
                        non_retryable = bool(metrics and metrics.error_type in {
                            "HTTPAuthorizationError", "HTTPPaymentRequired"
                        }) or getattr(metrics, "status_code", None) in {401, 403, 402}
                        if non_retryable:
                            call.retry_skipped = True
                            call.retry_skip_reason = metrics.error_type if metrics else "non_retryable_model_error"
                            raise
                        if attempt >= retry_count:
                            raise
                        await asyncio.sleep(2)
                raise last_error or RuntimeError("stage_model_call_failed")

            result = await asyncio.wait_for(invoke_with_retries(), timeout=stage_budget)
            output = result.payload if isinstance(result.payload, dict) else {}
            allowed_evidence = {item.evidence_id for item in state.evidence_pool} | {item.evidence_id for item in state.external_evidence}
            output, rejected_refs = self._filter_model_evidence_refs(output, allowed_evidence, allowed_chunks={item.chunk_id for item in state.evidence_chunks})
            call.status = "completed"
            call.profile = result.metrics.profile
            call.model = result.metrics.model
            call.duration_ms = accumulated_model_ms or result.metrics.done_ms or round((time.perf_counter() - started_clock) * 1000)
            call.output_hash = hashlib.sha256(json.dumps(output, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            call.output_keys = sorted(str(key) for key in output)[:40]
            call.completed_at = datetime.now().isoformat()
            state.model_calls.append(call)
            state.versions.model_version = result.metrics.model
            if trace is not None:
                trace.model_status = "completed"
                trace.degradation_reason = ""
                trace.model_error_code = ""
                trace.model_duration_ms = call.duration_ms
                trace.metrics.update({"模型": result.metrics.model, "模型耗时": f"{call.duration_ms / 1000:.1f}s"})
            if rejected_refs:
                warning = f"模型返回 {len(rejected_refs)} 个未锁定证据编号，已丢弃并要求复核。"
                state.process_trace.setdefault(stage_key, []).append({"level": "warning", "message": warning})
                state.stage_artifacts.setdefault(stage_key, {})["rejected_evidence_refs"] = sorted(rejected_refs)
            # Report repair/review holds this dictionary across awaited model
            # calls. Keep its identity so saved paragraphs and review hashes
            # remain part of the persisted state.
            state.stage_artifacts.setdefault(stage_key, {}).update({
                "prompt_version": prompt_version,
                "model_status": "completed",
                "degradation_reason": "",
                "retry_after_seconds": 0,
                "analysis": output,
            })
            await self._emit_progress(
                state, callback, stage_key=stage_key, step_key="model_analysis_completed",
                kind="artifact", status="completed", title=f"{purpose}完成",
                message="结构化分析已保存，并完成证据编号校验。",
                progress_percent=max(82, trace.progress_percent if trace else 82),
                metrics={"模型": result.metrics.model, "耗时": f"{call.duration_ms / 1000:.1f}s", "无效证据引用": len(rejected_refs)},
                artifact_refs=call.evidence_refs[:12],
            )
            return output
        except Exception as exc:
            metrics = exc.metrics if isinstance(exc, DeepSeekClientError) else None
            call.status = "degraded"
            call.error_type = metrics.error_type if metrics and metrics.error_type else type(exc).__name__
            call.error_message = self._safe_progress_text(str(exc), 240)
            call.duration_ms = accumulated_model_ms or (metrics.done_ms if metrics else None) or round((time.perf_counter() - started_clock) * 1000)
            call.profile = metrics.profile if metrics else str(getattr(client, "profile_name", ""))
            call.model = metrics.model if metrics else str(getattr(client, "model", ""))
            call.http_status = metrics.status_code if metrics else None
            call.error_code = metrics.error_type if metrics else type(exc).__name__
            if call.error_code in {"HTTPAuthorizationError", "HTTPPaymentRequired"} or call.http_status in {401, 402, 403}:
                state.analysis_policy.model_preflight_status = "unavailable"
                state.analysis_policy.model_preflight_reason = call.error_message or call.error_code
            call.completed_at = datetime.now().isoformat()
            state.model_calls.append(call)
            state.stage_artifacts.setdefault(stage_key, {}).update({
                "prompt_version": prompt_version,
                "model_status": "degraded",
                "degradation_reason": call.error_type,
                "retry_after_seconds": getattr(metrics, 'retry_after_seconds', None) or 0,
            })
            if trace is not None:
                trace.model_status = "degraded"
                trace.model_duration_ms = call.duration_ms
                trace.model_error_code = call.error_code
                trace.degradation_reason = call.error_code
                trace.fallback_reason = call.error_message or call.error_code
                trace.execution_mode = "deterministic_fallback"
                trace.metrics.update({"模型状态": "已降级", "模型耗时": f"{call.duration_ms / 1000:.1f}s", "错误码": call.error_code, "HTTP": call.http_status or "--"})
            state.process_trace.setdefault(stage_key, []).append({"level": "warning", "message": f"阶段模型分析已降级：{call.error_type}"})
            await self._emit_progress(
                state, callback, stage_key=stage_key, step_key="model_analysis_degraded",
                kind="warning", status="completed", title=f"{purpose}已降级",
                message="模型分析不可用，当前阶段继续使用确定性分析产物，并自动降低结论强度。",
                progress_percent=max(82, trace.progress_percent if trace else 82),
                metrics={"降级原因": call.error_type, "耗时": f"{call.duration_ms / 1000:.1f}s"},
            )
            return None
        finally:
            if owns_client and client is not None:
                await client.aclose()

    @classmethod
    def _filter_model_evidence_refs(cls, value: Any, allowed: set[str], key: str = "", *, allowed_chunks: set[str] | None = None) -> tuple[Any, set[str]]:
        rejected: set[str] = set()
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for child_key, child_value in value.items():
                if child_key == "evidence_id":
                    ref = str(child_value or "")
                    if ref and ref not in allowed:
                        rejected.add(ref)
                    result[child_key] = ref if ref in allowed else ""
                elif child_key in {"chunk_id", "chunk_ids"}:
                    refs = child_value if isinstance(child_value, list) else [child_value]
                    valid_chunks = allowed_chunks or set()
                    rejected.update(str(ref) for ref in refs if str(ref) and str(ref) not in valid_chunks)
                    clean = [str(ref) for ref in refs if str(ref) in valid_chunks]
                    result[child_key] = clean if isinstance(child_value, list) else (clean[0] if clean else "")
                elif child_key in {"evidence_ids", "support_evidence_ids", "counter_evidence_ids", "representative_evidence_ids", "evidence_refs"} and isinstance(child_value, list):
                    refs = [str(item) for item in child_value if str(item).strip()]
                    rejected.update(item for item in refs if item not in allowed)
                    result[child_key] = [item for item in refs if item in allowed]
                else:
                    result[child_key], child_rejected = cls._filter_model_evidence_refs(child_value, allowed, child_key, allowed_chunks=allowed_chunks)
                    rejected.update(child_rejected)
            return result, rejected
        if isinstance(value, list):
            result_list = []
            for item in value:
                cleaned, child_rejected = cls._filter_model_evidence_refs(item, allowed, key, allowed_chunks=allowed_chunks)
                result_list.append(cleaned)
                rejected.update(child_rejected)
            return result_list, rejected
        return value, rejected

    @staticmethod
    def _redact_model_text(value: str, limit: int = 1600) -> str:
        text = str(value or "")
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[已脱敏邮箱]", text)
        text = re.sub(r"(?<!\d)1\d{10}(?!\d)|(?<!\d)\d{12,}(?!\d)", "[已脱敏标识]", text)
        text = re.sub(r"(?:项目|合同|编号|工号)\s*[:：]?\s*[A-Za-z0-9_-]{6,}", "[已脱敏编号]", text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip()[:limit]

    @classmethod
    def _sanitize_model_payload(cls, value: Any, key: str = "") -> Any:
        """Redact user-originated text while preserving auditable identifiers."""
        identifier_keys = {
            "evidence_id", "evidence_ids", "support_evidence_ids", "counter_evidence_ids",
            "representative_evidence_ids", "external_evidence_ids", "branch_id", "theme_id",
            "theme_ids", "candidate_id", "gap_id", "crosslink_id", "query_id", "material_ref",
            "document_id", "source", "target", "doi", "url",
        }
        if isinstance(value, dict):
            return {
                str(child_key): cls._sanitize_model_payload(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [cls._sanitize_model_payload(item, key) for item in value]
        if isinstance(value, str) and key not in identifier_keys:
            return cls._redact_model_text(value, 6000)
        return value

    def _model_materials(self, state: TopicSelectionState) -> list[dict[str, str]]:
        if state.analysis_policy.material_mode == "none":
            return []
        result = []
        total = 0
        for document in state.input_profile.documents[:8]:
            excerpt = self._redact_model_text(document.excerpt, 1200)
            if not excerpt:
                continue
            remaining = max(0, 6000 - total)
            if not remaining:
                break
            excerpt = excerpt[:remaining]
            result.append({"material_ref": document.document_id, "name": self._redact_model_text(document.name, 120), "excerpt": excerpt})
            total += len(excerpt)
        return result

    @staticmethod
    def _build_scoring_policy(objective: str, overrides: dict[str, float]) -> ScoringPolicy:
        default = {
            "problem_fit": 0.20, "evidence_support": 0.20, "feasibility_data": 0.15,
            "novelty_opportunity": 0.15, "timeliness": 0.10, "validation_clarity": 0.10,
            "potential_value": 0.10,
        }
        presets = {
            "feasibility": {"feasibility_data": 0.25, "validation_clarity": 0.15, "potential_value": 0.05, "novelty_opportunity": 0.10},
            "timeliness": {"timeliness": 0.20, "evidence_support": 0.22, "potential_value": 0.08, "novelty_opportunity": 0.10},
            "novelty": {"novelty_opportunity": 0.25, "timeliness": 0.12, "potential_value": 0.13, "feasibility_data": 0.10},
            "impact": {"potential_value": 0.20, "problem_fit": 0.22, "novelty_opportunity": 0.18, "feasibility_data": 0.10},
        }
        active = dict(default)
        if objective in presets:
            active.update(presets[objective])
        valid = set(default)
        clean_overrides = {key: max(0.0, float(value)) for key, value in (overrides or {}).items() if key in valid}
        active.update(clean_overrides)
        total = sum(active.values()) or 1.0
        active = {key: round(value / total, 6) for key, value in active.items()}
        reason = "默认均衡权重"
        if objective != "balanced":
            reason = f"用户目标为“{objective}”，采用预设权重"
        if clean_overrides:
            reason += f"；用户覆盖维度：{', '.join(clean_overrides)}"
        return ScoringPolicy(objective=objective if objective in {"balanced", "feasibility", "timeliness", "novelty", "impact"} else "balanced", default_weights=default, active_weights=active, adjustment_reason=reason, version="score-v2-deterministic")

    @staticmethod
    def _score_dimension(key: str, label: str, normalized: float, max_score: float, weight: float, candidate: TopicCandidate, *, reason: str) -> ScoreDimensionResult:
        evidence_ids = list(candidate.representative_evidence_ids or candidate.evidence_ids)[:5]
        return ScoreDimensionResult(key=key, label=label, score=max(0.0, min(1.0, normalized)), max_score=max_score, weight=weight, rationale=reason, evidence_ids=evidence_ids, material_refs=list(candidate.user_material_refs), missing_information=list(candidate.uncertainties), uncertainty="high" if candidate.validation_status == "evidence_insufficient" else "medium", requires_review=candidate.validation_status != "supported")

    @staticmethod
    def _material_tokens(state: TopicSelectionState) -> set[str]:
        text = " ".join([state.raw_user_input, *[doc.excerpt for doc in state.input_profile.documents], *state.input_profile.resource_constraints, *state.input_profile.exclusions])
        return {token for token in re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", text.lower()) if token not in {"研究", "分析", "数据", "方法"}}

    @staticmethod
    def _candidate_material_refs(documents: list[Any], material_tokens: set[str], subject: str) -> list[str]:
        refs = []
        subject_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", subject.lower()))
        for index, document in enumerate(documents, start=1):
            excerpt_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", (document.excerpt or "").lower()))
            if subject_tokens & excerpt_tokens or material_tokens & excerpt_tokens:
                refs.append(f"M-{index:02d}")
        return refs[:4]

    @staticmethod
    def _candidate_evidence(state: TopicSelectionState, gap: GapScore, subject: str, material_tokens: set[str], limit: int = 5) -> list[EvidenceRecord]:
        subject_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", f"{subject} {gap.label}".lower()))
        ranked = []
        for item in state.evidence_pool:
            text = " ".join([item.title, item.abstract, " ".join(item.keywords)]).lower()
            tokens = set(re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", text))
            overlap = len(subject_tokens & tokens) + 0.5 * len(material_tokens & tokens)
            theme_bonus = 2 if gap.theme_ids and set(item.theme_ids) & set(gap.theme_ids) else 0
            if overlap or theme_bonus:
                ranked.append((overlap + theme_bonus, item))
        return [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]]

    @staticmethod
    def _candidate_external_evidence(state: TopicSelectionState, subject: str, limit: int = 3) -> list[str]:
        tokens = set(re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", subject.lower()))
        ranked = []
        for item in state.external_evidence:
            text = f"{item.title} {item.summary} {item.excerpt}".lower()
            score = len(tokens & set(re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z][A-Za-z0-9-]{2,30}", text)))
            if score:
                ranked.append((score, item.evidence_id))
        return [evidence_id for _, evidence_id in sorted(ranked, reverse=True)[:limit]]

    @staticmethod
    def _candidate_title(subject: str, gap: GapScore | None, direction: str, state: TopicSelectionState) -> str:
        gap_text = gap.label if gap else "关键机制与边界条件"
        object_text = (subject or state.input_profile.title or state.raw_user_input[:24]).strip(" ：:")
        # A rules-only fallback cannot invent a concrete scientific design.
        # Keep it visibly exploratory until the model supplies verifiable boundaries.
        return f"探索方向：{object_text}中的{gap_text}"

    @staticmethod
    def _candidate_direction_title(subject: str, direction: str, state: TopicSelectionState) -> str:
        # New fallback names distinguish existing routes; the legacy helper
        # above retains the exact naming used by old, unfrozen exports.
        object_text = (subject or state.input_profile.title or state.raw_user_input[:24]).strip(" ：:")
        return f"探索方向：{object_text}的{direction}"

    @staticmethod
    def _restore_distinct_candidate_titles(state: TopicSelectionState) -> None:
        """Recover legacy fallback names from already saved, distinct routes."""
        key = lambda title: re.sub(r'\W+', '', title.lower())
        counts = {}
        for candidate in state.candidates:
            counts[key(candidate.title)] = counts.get(key(candidate.title), 0) + 1
        for candidate in state.candidates:
            if counts[key(candidate.title)] > 1 and TopicSelectionAgent._is_generic_candidate_title(candidate.title):
                direction = TopicSelectionAgent._direction_from_candidate(candidate)
                display = candidate.display_title
                candidate.title = (display if display and direction in display
                    else f"探索方向：{candidate.research_object}的{direction}")
        names = [key(candidate.title) for candidate in state.candidates]
        if len(names) == len(set(names)):
            state.quality_gate.blockers = [message for message in state.quality_gate.blockers
                if message != '候选选题语义标题重复']

    @staticmethod
    def _direction_from_candidate(candidate: TopicCandidate) -> str:
        for text in (candidate.research_question, candidate.hypothesis, candidate.title, candidate.method_route):
            for key in ("方法改进", "数据与验证", "跨场景应用", "可靠性治理", "工程转化"):
                if key in text:
                    return key
        return "方法改进"

    @staticmethod
    def _is_generic_candidate_title(title: str) -> bool:
        return bool(re.search(
            r"^探索方向[：:]|(?:方法改进|数据与验证|数据验证|跨场景应用|可靠性治理|工程转化)(?:与可验证边界)?(?:研究)?$"
            r"|(?:方法改进|数据与验证|跨场景应用|可靠性治理|工程转化).*可验证边界"
            r"|的(?:机制解释与对照实验|分层样本与外部验证|跨区域样本与迁移验证|不确定性量化与失败案例审计|资源约束下的原型验证|可复现实验与边界验证)研究$",
            title or "",
        ))

    @staticmethod
    def _candidate_specific_overlap(candidate: TopicCandidate, evidence: EvidenceRecord) -> bool:
        def tokens(value: str) -> set[str]:
            result = set(re.findall(r"[a-z][a-z0-9-]{2,}", value.lower()))
            for word in re.findall(r"[\u4e00-\u9fff]+", value):
                result.update(word[index:index + 2] for index in range(len(word) - 1))
            return result - {"城市", "居民", "健康", "研究", "关联", "风险", "环境", "分析", "相关", "方法", "数据", "验证"}
        return len(tokens(candidate.research_object) & tokens(" ".join([evidence.title, evidence.abstract, evidence.excerpt, *evidence.keywords]))) >= 2

    @classmethod
    def _candidate_evidence_links(cls, candidate: TopicCandidate, state: TopicSelectionState) -> list[CandidateEvidenceLink]:
        evidence_by_id = {item.evidence_id: item for item in state.evidence_pool}
        chunks_by_evidence: dict[str, list[TopicEvidenceChunk]] = {}
        for chunk in state.evidence_chunks:
            chunks_by_evidence.setdefault(chunk.evidence_id, []).append(chunk)
        links: list[CandidateEvidenceLink] = []
        counter_ids = {ref for cross in state.crosslinks if cross.crosslink_id in candidate.crosslink_ids for ref in cross.counter_evidence_ids}
        counter_ids.update(ref for gap in state.gaps if gap.gap_id in candidate.gap_ids for ref in gap.counter_evidence_ids)
        gap_refs = {ref for gap in state.gaps if gap.gap_id in candidate.gap_ids for ref in gap.evidence_ids}
        for evidence_id in list(dict.fromkeys(candidate.evidence_ids + candidate.external_evidence_ids + sorted(counter_ids)))[:15]:
            evidence = evidence_by_id.get(evidence_id)
            scope = "fulltext" if evidence and evidence.fulltext_available else "abstract" if evidence and evidence.abstract else "metadata"
            if evidence_id in candidate.external_evidence_ids:
                scope = "external"
            chunks = [c for c in chunks_by_evidence.get(evidence_id, []) if not c.theme_ids or set(c.theme_ids) & set(candidate.theme_ids)]
            if scope == "fulltext" and not chunks:
                scope = "abstract" if evidence and evidence.abstract else "metadata"
            specific = evidence is not None and cls._candidate_specific_overlap(candidate, evidence)
            relation = "contradicts" if evidence_id in counter_ids else "supports" if evidence_id in gap_refs and specific else "context"
            links.append(CandidateEvidenceLink(
                link_id=f"{candidate.candidate_id}:{evidence_id}", candidate_id=candidate.candidate_id,
                theme_ids=list(candidate.theme_ids), gap_ids=list(candidate.gap_ids), evidence_id=evidence_id,
                chunk_ids=[item.chunk_id for item in chunks[:3]], relation=relation,
                rationale=f"关联主题 {', '.join(candidate.theme_ids)}、空白 {', '.join(candidate.gap_ids)}；{'交叉分析识别的反证，需对照核验' if relation == 'contradicts' else '空白分析引用的依据，尚不证明候选假设成立' if relation == 'supports' else '相关主题背景材料，尚未证明支持候选假设'}；{('已定位全文片段' if chunks else '当前仅有摘要或元数据')}。",
                evidence_scope=scope, locator=(chunks[0].locator if chunks else (evidence.locator if evidence else evidence_id)),
            ))
        return links

    @staticmethod
    def _concretize_candidate(candidate: TopicCandidate, state: TopicSelectionState, index: int) -> None:
        """Explicit proposed designs for a recognized domain; never claimed findings."""
        if "健康" in state.raw_user_input and any(term in state.raw_user_input for term in ("城市", "人居", "居民")):
            designs = [
                ("高密度街区绿地可达性与老年居民热相关健康风险的纵向分析", "老年居民的绿地暴露与热相关就诊风险", "城市高密度街区的老年常住居民；比较夏季热浪与非热浪时段", "绿地可达性是否通过降低热暴露改变热相关就诊风险？", "绿地可达性与热相关就诊风险的关联受地表温度中介，需控制收入与基础疾病", "遥感绿地/地表温度、步行路网可达性与匿名就诊记录联结；采用时空分层回归和中介分析", ["热相关就诊相对风险及95%置信区间", "温度中介效应", "不同可达性阈值的敏感性"], ["街区路网与绿地遥感数据", "气温及热浪记录", "经授权的年龄分层匿名就诊数据"]),
                ("交通污染暴露对学龄儿童呼吸健康的影响：学校周边多源数据队列", "学校周边交通污染与学龄儿童呼吸症状", "城市道路沿线与背景地区学校的学龄儿童；跟踪一个完整学年", "校区交通污染暴露与呼吸症状的关联是否存在距离梯度？", "道路距离和通勤暴露影响呼吸症状发生率；该关联可能受室内污染与季节混杂", "联结校区PM2.5/NO2监测、通勤路径与症状随访；构建混合效应队列模型并留校外部验证", ["呼吸症状发生率比及95%置信区间", "暴露模型MAE", "留校验证校准误差"], ["PM2.5/NO2时序监测", "匿名儿童症状随访", "学校位置、通勤及家庭环境协变量"]),
                ("社区步行环境与成年人心理健康的自然实验：街道改造前后比较", "街道步行环境改善与成年人心理健康", "实施街道改造社区与匹配未改造社区的成年居民；比较改造前后随访", "步行环境改善能否经由活动水平与社会交往影响心理健康？", "改造后的心理健康量表变化与步行活动和社会交往有关，需要检验平行趋势", "采用匹配社区双重差分，结合步行审计、活动记录与心理量表；进行安慰剂与失访敏感性检验", ["量表变化的处理效应及95%置信区间", "平行趋势检验", "失访加权后的效应稳定性"], ["街道改造时间与步行审计", "经授权的心理健康随访量表", "活动记录和社会人口协变量"]),
                ("老旧住宅通风与潮湿暴露对居民呼吸风险的季节性随访", "室内通风、潮湿与居民呼吸风险", "老旧住宅居民；比较供暖季与非供暖季，按通风条件分层", "潮湿暴露与呼吸症状的关联是否受通风条件调节？", "通风不足可能放大潮湿暴露相关风险，需排除吸烟和基础疾病混杂", "室内湿度/CO2监测与症状随访联结；采用个体固定效应及交互项分析", ["暴露交互效应及95%置信区间", "监测缺失率", "跨季节效应一致性"], ["住宅室内监测", "匿名居民症状记录", "建筑及通风属性"]),
                ("城市健康风险预测的跨社区公平性评估：多源环境暴露外部验证", "社区环境健康风险模型的分群误差", "不同收入和居住密度社区；按社区划分训练集与外部验证集", "环境健康风险预测在弱势群体中是否出现系统性校准偏差？", "样本覆盖差异可能导致分群校准偏差，需与简单风险基线比较", "以公开社区暴露和经授权健康记录建立基线；按社区外部验证并做分群校准评估", ["分群Brier评分", "校准斜率", "社区外部验证AUC及置信区间"], ["社区多源环境暴露", "人口结构协变量", "授权的健康结局数据"]),
            ]
            title, obj, boundary, question, hypothesis, route, metrics, data = designs[index % len(designs)]
            candidate.title = title; candidate.research_object = obj; candidate.sample_boundary = boundary
            candidate.research_question = question; candidate.hypothesis = hypothesis; candidate.method_route = route
            candidate.validation_path = f"拟定边界：{boundary}；先核验数据权限与结局定义，再建立对照基线、控制混杂并进行外部/敏感性验证；置信区间跨越无效值或假设检验失败时不判定假设成立。"
            candidate.evaluation_metrics = metrics; candidate.data_requirements = data
            candidate.convergence_notes = ["从原始方向收敛到独立暴露、目标人群、结局和研究设计", "以上是待验证方案；采样地点、随访时间与数据权限尚需确认"]
            candidate.distinctiveness_rationale = f"研究对象：{obj}；样本边界：{boundary}；与其他候选比较暴露、结局和验证设计。"
            candidate.fit_rationale = "与城市人居环境和居民健康相关；具体场景为候选研究设计，用户尚未确认数据可得性。"
        else:
            candidate.sample_boundary = "；".join(state.input_profile.resource_constraints + state.input_profile.data_constraints)
            candidate.convergence_notes = ["规则生成的初步方向；需进一步核验具体对象、样本和指标"]

    @classmethod
    def _validate_convergence(cls, state: TopicSelectionState) -> None:
        from difflib import SequenceMatcher
        previous: list[TopicCandidate] = []
        normalize = lambda value: re.sub(r"[\W_]+|关联性研究|研究$", "", value.lower())
        for candidate in state.candidates:
            issues = []
            if SequenceMatcher(None, normalize(candidate.title), normalize(state.raw_user_input)).ratio() >= 0.84 or cls._is_generic_candidate_title(candidate.title):
                issues.append("题目与原始方向高度同义，尚未收敛")
            if not all((candidate.research_object, candidate.hypothesis, candidate.method_route, candidate.sample_boundary, candidate.validation_path, candidate.evaluation_metrics, candidate.data_requirements)):
                issues.append("缺少具体对象、假设、样本边界或验证指标")
            for other in previous:
                if SequenceMatcher(None, normalize(candidate.title), normalize(other.title)).ratio() >= 0.84 or (candidate.research_object == other.research_object and candidate.sample_boundary == other.sample_boundary):
                    issues.append(f"与{other.candidate_id}缺少实质对象或场景差异")
            candidate.convergence_valid = not issues
            specific_ids = {link.evidence_id for link in candidate.evidence_links if link.relation == "supports"}
            if not specific_ids:
                candidate.convergence_notes.append("现有文献仅提供主题背景，尚未直接覆盖收敛后的研究对象；需补充针对性证据")
            candidate.convergence_notes = list(dict.fromkeys(candidate.convergence_notes + issues))
            if issues or not specific_ids or not candidate.evidence_ids or not candidate.theme_ids or not candidate.gap_ids:
                candidate.validation_status = "evidence_insufficient"
                candidate.recommendation_level = "pending_evidence"
            previous.append(candidate)

    @staticmethod
    def _candidate_question(subject: str, direction: str, state: TopicSelectionState) -> str:
        constraint = "；".join(state.input_profile.resource_constraints) or "现有数据和资源条件"
        return f"在{constraint}下，如何针对{subject}解决{direction}中的关键瓶颈，并用可重复实验判断改进是否真实有效？"

    @staticmethod
    def _candidate_data_requirements(state: TopicSelectionState, subject: str) -> list[str]:
        return [f"与{subject}对应的用户材料样本或公开替代数据", "代表文献所用基线/指标的可复现实现", *state.input_profile.data_constraints[:2]]

    async def _notify(self, state: TopicSelectionState, callback: StageCallback | None) -> None:
        if callback is not None:
            await callback(state)

    async def _emit_progress(
        self,
        state: TopicSelectionState,
        callback: StageCallback | None,
        *,
        stage_key: str,
        step_key: str,
        title: str,
        message: str = "",
        kind: str = "status",
        status: str = "running",
        progress_percent: int = 0,
        current: int | float | None = None,
        total: int | float | None = None,
        unit: str = "",
        metrics: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
    ) -> None:
        trace = next((item for item in state.stage_trace if item.stage_key == stage_key), None)
        bounded_percent = max(0, min(100, int(progress_percent)))
        if trace is not None:
            bounded_percent = max(trace.progress_percent, bounded_percent)
            trace.progress_percent = bounded_percent
            trace.active_step = self._safe_progress_text(title, 160)
            if metrics:
                trace.metrics.update(metrics)
        sequence = (state.progress_events[-1].sequence if state.progress_events else 0) + 1
        event = TopicProgressEvent(
            event_id=f"TPE-{sequence:04d}",
            sequence=sequence,
            stage_key=stage_key,
            step_key=step_key,
            kind=kind,
            status=status,
            title=self._safe_progress_text(title, 160),
            message=self._safe_progress_text(message, 500),
            progress_percent=bounded_percent,
            current=current,
            total=total,
            unit=self._safe_progress_text(unit, 24),
            metrics=dict(metrics or {}),
            artifact_refs=[str(item)[:120] for item in (artifact_refs or []) if str(item).strip()][:24],
            occurred_at=datetime.now().isoformat(),
        )
        state.active_progress = event
        state.progress_events = [*state.progress_events, event][-120:]
        await self._notify(state, callback)

    @staticmethod
    def _safe_progress_text(value: str, limit: int) -> str:
        text = str(value or "")
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[已脱敏邮箱]", text)
        text = re.sub(r"(?<!\d)1\d{10}(?!\d)|(?<!\d)\d{15,}(?!\d)", "[已脱敏标识]", text)
        return re.sub(r"\s+", " ", text).strip()[:limit]

    @staticmethod
    def _source_label(source_kind: str) -> str:
        return {"nstl": "NSTL", "fulltext_library": "全文库补充", "web": "联网", "cloud_model": "云模型"}.get(source_kind, source_kind)

    def _resume_index(self, state: TopicSelectionState) -> int:
        if state.resume_from_stage:
            for index, (key, _) in enumerate(self.STAGES):
                if key == state.resume_from_stage:
                    return index
        return next((index for index, trace in enumerate(state.stage_trace) if trace.status != "completed"), 0)

    @staticmethod
    def _languages(preference: str) -> list[str]:
        return ["zh"] if preference == "zh" else ["en"] if preference == "en" else []

    @classmethod
    def _title_from_text(cls, text: str) -> str:
        cleaned = re.sub(r"\s+", "", str(text or ""))
        cleaned = re.sub(
            r"^(?:请帮我|请为我|帮我|我想研究|我希望研究|我要研究|围绕|针对|基于)+",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?:请生成(?:一份)?报告|生成(?:一份)?报告|最终给我|给我进行分析|帮我分析|智能选题)+",
            "",
            cleaned,
        )
        clauses = [item.strip("，,:：、的") for item in re.split(r"[\r\n。！？!?；;]", cleaned) if item.strip()]
        candidate = next((item for item in clauses if len(item) >= 6), clauses[0] if clauses else "")
        return cls._normalize_concise_title(candidate) or "未命名研究方向"

    @staticmethod
    def _normalize_concise_title(value: str) -> str:
        title = re.sub(r"[\r\n\t]+", "", str(value or "")).strip(" ，。；：:、-_")
        title = re.sub(r"^(?:研究方向|研究主题|任务|主题)\s*[:：]\s*", "", title)
        title = re.sub(r"^(?:请帮我|请为我|帮我|我想研究|我希望研究|我要研究|关于|围绕|研究|探索)", "", title)
        title = re.split(r'[，。；：:]', title)[0]
        title = re.sub(r"(?:智能选题)?(?:分析)?报告$", "", title).strip(" ，。；：:、-_")
        if len(title) > 24:
            title = title[:24].rstrip("，。；：:、-_")
        return title if len(title) >= 4 else ""

    @staticmethod
    def _document_from_upload(item: dict[str, Any]) -> Any:
        from app.agents.topic_selection.schemas import DocumentRecord
        return DocumentRecord(document_id=str(item.get("file_id") or item.get("id") or uuid4().hex[:10]), name=str(item.get("filename") or item.get("name") or "上传材料"), mime_type=str(item.get("mime_type") or item.get("content_type") or ""), status="parsed" if item.get("text") or item.get("content") else "pending", excerpt=str(item.get("text") or item.get("content") or "")[:1000])

    @staticmethod
    def _build_term_map(text: str) -> list[TermMapEntry]:
        tokens: list[str] = []
        for phrase in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9-]{2,30}", text):
            if phrase.isascii():
                tokens.append(phrase)
                continue
            for part in re.split(r"以及|及其|与|和|及", phrase):
                part = re.sub(r"(?:关联性?研究|相关性研究|研究|分析|探索)$", "", part)
                if len(part) >= 2:
                    tokens.append(part[:120])
        seen: set[str] = set()
        entries = []
        for token in tokens:
            normalized = token.lower()
            if normalized in seen or normalized in {"研究", "方法", "问题", "分析", "based", "using"}:
                continue
            seen.add(normalized)
            entries.append(TermMapEntry(term_id=f"TERM-{len(entries)+1:03d}", preferred=token, english_terms=[token] if token.isascii() else [], status="candidate"))
            if len(entries) >= 12:
                break
        return entries

    @staticmethod
    def _normal_key(title: str, year: int | None) -> str:
        normalized = re.sub(r"\W+", "", title.lower())
        return hashlib.sha1(f"{normalized}:{year or 0}".encode()).hexdigest()[:20] if title else ""

    @staticmethod
    def _term_key(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())[:80]

    @staticmethod
    def _sanitize_external_query(value: str, state: TopicSelectionState) -> str:
        """只保留主题术语；上传文件和原始任务描述永远不进入外发文本。"""
        text = str(value or "")
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", text)
        text = re.sub(r"(?<!\d)1\d{10}(?!\d)|(?<!\d)\d{15,}(?!\d)", " ", text)
        text = re.sub(r"\b[A-Za-z]{1,12}[-_][A-Za-z]+[-_]?[A-Za-z0-9]*\b", " ", text)
        text = re.sub(r"\b(?:secret|confidential|internal|classified)\b", " ", text, flags=re.I)
        text = re.sub(r"(?:密级|内部|涉密|项目编号|合同号|身份证|手机号|邮箱)\s*[:：]?\s*\S+", " ", text, flags=re.I)
        text = re.sub(r"[^\w\u4e00-\u9fff\-+#./ ]+", " ", text, flags=re.UNICODE)
        terms = [part.strip() for part in text.split() if part.strip()]
        if not terms:
            terms = [item.preferred for item in state.term_map[:8] if item.preferred]
        return " ".join(dict.fromkeys(terms))[:500]

    @staticmethod
    def _deduplicate_external_evidence(items: list[ExternalEvidence]) -> list[ExternalEvidence]:
        result: list[ExternalEvidence] = []
        seen: dict[str, ExternalEvidence] = {}
        for item in items:
            key = (item.url or item.hash or re.sub(r"\W+", "", item.title.lower())).lower()
            if not key:
                continue
            if key in seen:
                existing = seen[key]
                if existing.excerpt != item.excerpt:
                    existing.conflict_group = existing.conflict_group or f"conflict-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
                    item.conflict_group = existing.conflict_group
                    result.append(item)
                continue
            seen[key] = item
            result.append(item)
        return result

    @staticmethod
    def _evidence_from_paper(paper: StandardPaper, branch: QueryBranch) -> EvidenceRecord:
        evidence_id = f"E-{hashlib.sha1((paper.paper_id or paper.title or uuid4().hex).encode()).hexdigest()[:12]}"
        return EvidenceRecord(
            evidence_id=evidence_id, paper_id=paper.paper_id, title=paper.title, authors=paper.authors, year=paper.year or None,
            venue=paper.venue, doi=paper.doi,
            url=paper.url, nstl_url=paper.nstl_url, source_url=paper.source_url,
            language=paper.language, abstract=paper.abstract,
            keywords=paper.keywords, discipline_tags=paper.discipline_tags, retrieval_score=paper.retrieval_score, final_score=paper.final_score or paper.rrf_score,
            citation_count=max(0, int(paper.citation_count or 0)),
            query_id=branch.branch_id, theme_ids=[branch.theme_id], evidence_level="B" if paper.abstract else "C",
            fulltext_indexed=paper.has_fulltext, fulltext_available=False, access_status="abstract_only", raw=paper.raw,
            source_hash=hashlib.sha256(f"{paper.doi}|{paper.title}|{paper.year}".encode()).hexdigest()[:24],
            source_published_at=str(paper.year or ""), retrieved_at=datetime.now().isoformat(),
            locator=paper.nstl_url or paper.source_url or paper.url or paper.doi or paper.paper_id,
        )

    @staticmethod
    def _version(prefix: str, value: str) -> str:
        return f"{prefix}-v1-{hashlib.sha1(value.encode()).hexdigest()[:10]}"

    @staticmethod
    def _stage_summary(state: TopicSelectionState, key: str) -> str:
        values = {"input_modeling": f"形成 {len(state.term_map)} 个术语和约束卡", "theme_decomposition": f"形成 {len(state.theme_tree.nodes)} 个主题节点", "retrieval_evidence": f"纳入 {len(state.evidence_pool)} 条证据", "landscape_trend": f"统计 {len(state.future_work_signals)} 条未来工作信号", "crosslink_gap": f"识别 {len(state.gaps)} 个候选空白", "candidate_generation": f"生成 {len(state.candidates)} 个候选题", "scoring_ranking": f"完成 {len(state.candidates)} 个候选方向研判", "quality_gate": f"质量门：{state.quality_gate.report_eligibility}", "report_publish": f"报告版本 {state.versions.report_version}"}
        return values.get(key, "阶段完成")

    @staticmethod
    def _stage_evidence_refs(state: TopicSelectionState, key: str) -> list[str]:
        if key in {"retrieval_evidence", "landscape_trend", "crosslink_gap"}:
            return [item.evidence_id for item in state.evidence_pool[:30]]
        return []

    @staticmethod
    def _executive_summary(state: TopicSelectionState) -> str:
        title = state.input_profile.title
        first = state.candidates[0] if state.candidates else None
        lead = f"优先比较“{first.display_title or first.title}”。" if first else "当前尚无充分依据推荐具体题目。"
        return (f"围绕“{title}”，基于本次检索的 {len(state.evidence_pool)} 篇文献形成 {len(state.candidates)} 个候选方向。"
                f"{lead}各方向从研究问题、方法借鉴和验证设计进行比较，按研究推进顺序列示。")

    @staticmethod
    def _public_report_text(value: Any) -> str:
        """Reader narration; original source quotations are rendered separately."""
        from app.agents.content_assessment.scholarly_report import reader_prose
        return reader_prose(reader_text(str(value or "")))

    @staticmethod
    def _public_limitations(items: list[str]) -> list[str]:
        """Condense internal checks into a short reader-facing evidence boundary."""
        values = [re.sub(r"\s+", " ", str(item)).strip() for item in items if str(item).strip()]
        limitations: list[str] = []
        if any(any(term in item for term in ("全文", "摘要", "元数据")) for item in values):
            limitations.append("部分文献仅提供摘要或题录，相关判断以可核验内容为限，未据此提升推荐等级。")
        if any(any(term in item for term in ("检索", "查询", "来源", "NSTL 当前未返回")) for item in values):
            limitations.append("部分检索分支或来源覆盖有限，趋势与研究空白结论仅适用于当前锁定样本。")
        if any(any(term in item for term in ("模型", "降级", "未锁定证据编号", "强断言")) for item in values):
            limitations.append("部分智能分析采用确定性规则或自动降级结果，报告已降低相应结论强度。")
        if any(any(term in item for term in ("边界", "待确认", "缺少足够证据", "候选")) for item in values):
            limitations.append("部分研究边界或候选方向的依据有限，作为探索方向提供初步研究设计。")
        if not limitations:
            limitations.append("结论以当前任务输入、检索范围和可定位证据为边界。")
        return limitations

    @staticmethod
    def _report_cutoff(value: Any) -> str:
        """Keep figure metadata readable without exposing timestamp noise."""
        text = str(value or "").strip()
        if not text:
            return "未指定"
        match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        return match.group(1) if match else text[:10]

    @staticmethod
    def _report_sections(state: TopicSelectionState) -> list[dict[str, Any]]:
        report_analysis = state.stage_artifacts.get("report_publish", {}).get("analysis", {})
        landscape_analysis = state.stage_artifacts.get("landscape_trend", {}).get("analysis", {})
        candidates = list(state.candidates[:5])
        limitations = TopicSelectionAgent._public_limitations([
            *state.report_plan.limitations,
            *[str(item) for item in report_analysis.get("limitations", []) if str(item).strip()],
        ])
        landscape_text = str(report_analysis.get("landscape_narrative") or landscape_analysis.get("overview") or "").strip() or f"当前锁定证据池包含 {len(state.evidence_pool)} 篇去重科学文献，覆盖 {len(state.theme_tree.nodes)} 个研究主题。年份与主题趋势仅统计文献检索样本，全文库独立发现按补充来源展示。"
        gap_text = "以下研究方向依据所引文献的方法及其适用对象提出，方法迁移通过后续对照研究检验。"
        first = candidates[0] if candidates else None
        next_actions = [f"优先核验 {first.candidate_id}《{first.display_title or first.title}》的代表文献、适用条件与反证。" if first else "核验候选题代表文献的全文与实验边界", "确认数据授权、基线实现和资源约束", "按候选研究设计开展小规模预研，预先定义评价指标和失败条件", "根据预研结果选择进入立项或创新性评估的方向"]

        def paragraph(text: str, kind: str = "paragraph") -> dict[str, Any]:
            from app.agents.content_assessment.scholarly_report import reader_prose
            return {"kind": kind, "text": reader_prose(TopicSelectionAgent._public_report_text(text))}

        def bullet(items: list[str], kind: str = "bullet_list") -> dict[str, Any]:
            from app.agents.content_assessment.scholarly_report import reader_prose
            return {"kind": kind, "items": [value for item in items if (value := reader_prose(TopicSelectionAgent._public_report_text(item)))]}

        cited_ids = {eid for candidate in candidates for eid in [*candidate.representative_evidence_ids, *[link.evidence_id for link in candidate.evidence_links]]}
        ranked_crosslinks = sorted(state.crosslinks, key=lambda item: (
            item.status == "validated", len(item.support_evidence_ids), -len(item.counter_evidence_ids), item.confidence), reverse=True)
        cited_ids.update(eid for link in ranked_crosslinks[:3] for eid in link.support_evidence_ids[:3])
        cited_ids.update(eid for item in landscape_analysis.get("method_evolution", [])[:3] for eid in item.get("evidence_ids", []))
        report_evidence = [e for e in state.evidence_pool if e.evidence_id in cited_ids]
        evidence_text = "\n".join(
            f"[{state.report_plan.citation_map.get(item.evidence_id, index)}] {item.title}（{item.year or '年份未提供'}；{item.venue or '来源未提供'}） 标识：{item.doi or item.paper_id or '未提供'}；链接：{item.url or '未提供'}"
            for index, item in enumerate(report_evidence, start=1)
        ) or "当前无可引用 NSTL 记录；本节保留并明确证据限制。"
        def brief(value: Any, limit: int) -> str:
            text = re.sub(r"\s+", " ", TopicSelectionAgent._public_report_text(value)).strip()
            if len(text) <= limit: return text
            cut = text[:limit]
            for delimiter in ("。", "；", ";", ". "):
                if delimiter in cut: return cut.rsplit(delimiter, 1)[0] + delimiter
            return cut.rstrip("，、; ") + "…"
        from .discovery import sample_finding
        landscape_text = brief(sample_finding(landscape_text), 450)
        theme_lines = [f"{node.display_label or node.label}" + (f"（{'、'.join(node.discipline_tags)}）" if node.discipline_tags else "") + f"；关注：{brief(node.research_question or node.boundary, 70)}" for node in state.theme_tree.nodes]
        ranked_crosslinks = sorted(
            state.crosslinks,
            key=lambda item: (
                item.status == "validated",
                len(item.support_evidence_ids),
                -len(item.counter_evidence_ids),
                item.confidence,
            ),
            reverse=True,
        )
        report_crosslinks = ranked_crosslinks[:3]
        def cited_evidence(ids: list[str]) -> str:
            return " ".join(f"[{state.report_plan.citation_map[eid]}]" for eid in dict.fromkeys(ids) if eid in state.report_plan.citation_map)

        def landscape_summary(key: str, fallback_period: str) -> dict[str, Any]:
            saved = landscape_analysis.get(key)
            if isinstance(saved, dict) and str(saved.get("text") or "").strip():
                return saved
            rows = [row for row in landscape_analysis.get("method_evolution", []) if row.get("period") == fallback_period]
            return {
                "text": " ".join(str(row.get("finding") or "").strip() for row in rows if row.get("finding")),
                "evidence_ids": list(dict.fromkeys(eid for row in rows for eid in row.get("evidence_ids", []))),
            }

        method_summary = landscape_summary("method_evolution_summary", "present")
        future_summary = landscape_summary("future_research_summary", "future")
        method_blocks = []
        if method_summary.get("text"):
            method_blocks.append(paragraph(f"方法演进：{method_summary['text']} {cited_evidence(method_summary.get('evidence_ids', []))}"))
        if future_summary.get("text"):
            method_blocks.append(paragraph(f"后续研究方向：{future_summary['text']} {cited_evidence(future_summary.get('evidence_ids', []))}"))

        gap_status_labels = {"candidate": "候选空白", "validated": "已有证据支持", "coverage_limited": "证据覆盖有限", "rejected": "不成立"}
        cross_lines = []
        for item in report_crosslinks:
            relationship = " → ".join(value for value in (item.core_discipline, item.expansion_discipline) if value)
            details = [relationship] if relationship else []
            for label, value, limit in [("借鉴方法", item.transferable_method, 90), ("关联原因", item.relation_basis, 85),
                                        ("研究方向", item.research_opportunity, 90), ("验证设计", item.validation_conditions, 100)]:
                if value:
                    details.append(f"{label}：{brief(value, limit)}")
            if item.support_evidence_ids:
                details.append("参考文献：" + cited_evidence(item.support_evidence_ids[:3]))
            if item.status != "validated":
                details.append("上述关系作为后续研究可检验的线索")
            cross_lines.append("；".join(details) + "。")
        candidate_blocks = []
        for index, item in enumerate(candidates, 1):
            reader_fields = candidate_reader_report_fields(item, state.report_plan.citation_map)
            if reader_fields:
                candidate_blocks.append({"kind": "candidate_detail", "candidate_id": item.candidate_id,
                    "index": index, "title": item.display_title or item.title, "status": "", "show_status": False,
                    "fields": reader_fields})
                continue
            refs = cited_evidence(list(dict.fromkeys([*item.representative_evidence_ids, *[link.evidence_id for link in item.evidence_links]])))
            selected_links = item.evidence_links[:1]
            counter = next((link for link in item.evidence_links if link.relation == "contradicts"), None)
            if counter and counter not in selected_links:
                selected_links = [*selected_links[:2], counter]
            evidence_details = []
            for link in selected_links:
                scope = {"fulltext": "可定位全文片段", "abstract": "摘要", "metadata": "题录", "external": "外部来源"}.get(link.evidence_scope, "以来源实际可读内容为准")
                detail = f"{cited_evidence([link.evidence_id])} {'反证' if link.relation == 'contradicts' else '依据'}：{brief(link.rationale, 65)}；证据范围：{scope}"
                chunk = next((chunk for chunk in state.evidence_chunks if chunk.evidence_id == link.evidence_id and chunk.chunk_id in link.chunk_ids and chunk.text.strip()), None)
                if chunk:
                    location = chunk.section_path or ""
                    if location:
                        detail += f"；全文摘录：{chunk.text[:120]}（{location}）"
                evidence_details.append(detail)
            status = "优先推荐" if item.recommendation_level == "recommended" else "探索方向"
            candidate_blocks.append({"kind": "candidate_detail", "candidate_id": item.candidate_id, "title": f"{index}. {item.display_title or item.title}", "status": "", "show_status": False,
                "fields": [
                    {"label": "核心理由", "value": brief(re.sub(r"^(?:高|中|低)[。．：:]\s*", "", item.fit_rationale or item.distinctiveness_rationale or item.research_question), 100)},
                    {"label": "研究设计", "value": f"对象：{brief(item.research_object or '待明确', 65)}；方法：{brief(item.method_route or '待细化', 140)}；指标：{brief('、'.join(item.evaluation_metrics) or '待定义', 65)}；验证：{brief(item.validation_path or '待验证', 85)}。"},
                    {"label": "证据依据", "value": refs + "；" + "；".join(evidence_details)},
                    {"label": "适用条件与验证建议", "value": brief("；".join(item.risks[:2]) or "结论限定于本次证据，需核验适用条件。", 100)},
                ]})
        references = {"key": "references", "title": "参考文献", "content": evidence_text,
            "blocks": [{"kind": "reference_list", "items": [{"number": state.report_plan.citation_map.get(item.evidence_id, index),
                "evidence_id": item.evidence_id, "title": item.title, "authors": ", ".join(item.authors), "venue": item.venue,
                "year": item.year, "doi": item.doi, "paper_id": item.paper_id, "nstl_url": item.nstl_url, "source_url": item.source_url,
                "source": "全文库补充来源" if item.source_kind == "fulltext_library" else "NSTL",
                "url": item.url or state.report_plan.citation_links.get(item.evidence_id, "")} for index, item in enumerate(report_evidence, 1)]}, {"kind": "table_ref", "key": "cross_matrix"}]}
        sections = [
            {"key": "abstract", "title": "摘要", "content": state.report_plan.executive_summary,
             "blocks": [paragraph(state.report_plan.executive_summary, "lead")]},
            {"key": "candidate_evaluation", "title": "一、推荐结论与候选题优先级", "content": "\n".join(item.title for item in candidates),
             "blocks": [{"kind": "table_ref", "key": "candidate_priority"}, *candidate_blocks]},
            {"key": "literature_trends", "title": "二、研究主题与相关进展", "content": landscape_text,
             "blocks": [paragraph(landscape_text, "lead"), bullet(theme_lines[:5]), {"kind": "figure_ref", "key": "theme_network"}, {"kind": "figure_ref", "key": "trend_comparison"}]},
            {"key": "cross_discipline", "title": "三、趋势、方法演进与研究方向", "content": "\n".join(cross_lines[:3]) or gap_text,
             "blocks": [*method_blocks, {"kind": "figure_ref", "key": "discipline_network"},
                        bullet(cross_lines[:3] or ["研究建议围绕上述主题及其具体方法展开。"]),
                        bullet([f"方法原文：{source['quote'][:120]}；定位：{source['locator']}；依据：{cited_evidence([source['evidence_id']])}" for item in report_crosslinks[:3] for source in item.method_evidence[:1]]), paragraph(gap_text)]},
            {"key": "conclusion", "title": "四、推荐依据和研究建议", "content": "\n".join(next_actions),
             "blocks": [bullet(next_actions[:4], "numbered_list"), paragraph("研究建议依据本次检索文献及其适用条件提出；方法迁移效果应通过相应对象上的对照研究验证。")]},
            references,
        ]
        for section in sections:
            section["content_hash"] = hashlib.sha256(str(section.get("content") or "").encode("utf-8")).hexdigest()
        return sections

    @staticmethod
    def _section_specs(state: TopicSelectionState) -> list[dict[str, Any]]:
        specs = []
        for index, section in enumerate(state.report_plan.sections, start=1):
            appendix = section["key"] == "references"
            candidate_ids = [item.candidate_id for item in state.candidates[:5]] if section["key"] == "candidate_evaluation" else []
            chart_keys = []
            if section["key"] == "literature_trends": chart_keys = ["theme_network", "trend_comparison"]
            elif section["key"] == "theme_decomposition": chart_keys = ["theme_network"]
            elif section["key"] == "research_gaps": chart_keys = ["gap_evidence_coverage"]
            elif section["key"] == "core_literature": chart_keys = ["core_literature_top10"]
            elif section["key"] == "candidate_evaluation": chart_keys = []
            elif section["key"] == "cross_discipline": chart_keys = ["discipline_network"]
            elif candidate_ids: chart_keys = []
            table_keys: list[str] = []
            if section["key"] == "task_method": table_keys = ["method_flow"]
            if section["key"] == "literature_trends": table_keys = []
            if section["key"] == "candidate_evaluation": table_keys = ["candidate_priority"]
            specs.append({"key": section["key"], "title": section["title"], "order": index, "required": True, "body_kind": "narrative", "chart_keys": chart_keys, "table_keys": table_keys, "candidate_ids": candidate_ids, "is_appendix": appendix, "page_budget": 2 if candidate_ids else 1, "toc_label": section["title"], "blocks": section.get("blocks", [])})
        return specs

    @staticmethod
    def _table_specs(state: TopicSelectionState) -> list[dict[str, Any]]:
        def compact(value: Any, limit: int) -> str:
            text = re.sub(r"\s+", " ", TopicSelectionAgent._public_report_text(value)).strip()
            if len(text) <= limit:
                return text
            return f"{text[: max(1, limit - 3)].rstrip()}..."

        score_by_id = {item.candidate_id: item for item in state.score_breakdowns}
        gap_by_id = {item.gap_id: item for item in state.gaps}
        candidate_rows = []
        ordered = list(state.candidates[:5])
        for rank, item in enumerate(ordered, start=1):
            score = score_by_id.get(item.candidate_id)
            dimensions = {dimension.key: dimension for dimension in (score.dimensions if score else [])}
            novelty = dimensions.get("novelty_opportunity")
            feasibility = dimensions.get("feasibility_data")
            gap = gap_by_id.get(item.gap_ids[0]) if item.gap_ids else None
            candidate_rows.append({
                "优先级": rank,
                "候选题": compact(item.display_title or item.title, 44),
                "研究问题": compact(item.research_question, 90),
                "可行性": round(feasibility.score / feasibility.max_score * 50, 1) if feasibility and feasibility.max_score else "—",
                "竞争压力": round((gap.competition_heat if gap else 0) * 50, 1),
                "推荐依据": compact(re.sub(r"^(?:高|中|低)[。．：:]\s*", "", item.fit_rationale or item.distinctiveness_rationale), 110),
                "建议": "优先推荐" if item.recommendation_level == "recommended" else "建议验证" if item.recommendation_level == "exploratory" else "探索方向",
            })
        query_by_id = {item.query_id: item for item in state.source_queries}
        retrieval_rows = []
        for branch in state.query_plan.branches:
            query = query_by_id.get(branch.branch_id)
            retrieval_rows.append({
                "子方向": next((node.label for node in state.theme_tree.nodes if node.theme_id == branch.theme_id), branch.theme_id),
                "检索式": compact(query.sanitized_query if query else branch.query, 72),
                "用途": compact(branch.purpose, 36),
                "样本数": query.returned_count if query else branch.returned_count,
            })
        return [
            {"key": "cross_matrix", "title": "附录：学科与方法对照", "columns": ["核心学科", "拓展学科", "方法", "参考文献"], "column_widths": [1, 1, 2, 2], "rows": [{"核心学科": link.core_discipline or "", "拓展学科": link.expansion_discipline or "", "方法": link.transferable_method, "参考文献": '、'.join(f'[{state.report_plan.citation_map[eid]}]' for eid in link.support_evidence_ids[:2] if eid in state.report_plan.citation_map)} for link in state.crosslinks if link.transferable_method and link.support_evidence_ids], "source_ids": [e.evidence_id for e in state.evidence_pool], "snapshot_version": state.versions.evidence_pool_version},
            {"key": "method_flow", "title": "方法流程与证据规则", "columns": ["阶段", "处理内容", "输出"], "column_widths": [1.05, 3.0, 1.95], "rows": [{"阶段": "主题拆解", "处理内容": "研究对象、方法路线、应用边界", "输出": "主题节点与检索种子"}, {"阶段": "检索取证", "处理内容": "NSTL 元数据、摘要和全文片段", "输出": "可定位证据池"}, {"阶段": "趋势与空白", "处理内容": "年份、主题、交叉关系和机会空间", "输出": "趋势结论与研究空白"}, {"阶段": "候选与质量门", "处理内容": "候选题收敛、评分、证据复核", "输出": "候选题和发布状态"}], "source_ids": [item.evidence_id for item in state.evidence_pool], "snapshot_version": state.versions.evidence_pool_version},
            {"key": "retrieval_queries", "title": "子方向检索式与样本数", "columns": ["子方向", "检索式", "用途", "样本数"], "column_widths": [1.2, 2.8, 1.4, 0.6], "font_size": 9, "rows": retrieval_rows, "source_ids": [item.evidence_id for item in state.evidence_pool], "snapshot_version": state.versions.evidence_pool_version},
            {"key": "candidate_priority", "title": "候选题优先级排序", "columns": ["优先级", "候选题", "研究问题", "推荐依据"], "column_widths": [0.4, 1.8, 2.0, 2.1], "font_size": 9.5, "rows": candidate_rows, "source_ids": [item.evidence_id for item in state.evidence_pool], "snapshot_version": state.versions.scoring_version},
        ]

    @staticmethod
    def _chart_specs(state: TopicSelectionState) -> list[dict[str, Any]]:
        snapshot = state.versions.chart_data_snapshot_version
        report_version = state.versions.report_version
        years = sorted({item.year for item in state.evidence_pool if item.year})
        top_papers = sorted(state.evidence_pool, key=lambda item: (item.citation_count, item.final_score, item.retrieval_score), reverse=True)[:10]
        theme_series = []
        for node in state.theme_tree.nodes:
            values = [sum(item.year == year and node.theme_id in item.theme_ids for item in state.evidence_pool) for year in years]
            if any(values):
                theme_series.append({"theme_id": node.theme_id, "label": node.display_label or node.label, "values": values})
        score_by_candidate = {item.candidate_id: item for item in state.score_breakdowns}
        gap_by_id = {item.gap_id: item for item in state.gaps}
        radar_rows = []
        for candidate in state.candidates[:5]:
            breakdown = score_by_candidate.get(candidate.candidate_id)
            dimensions = {item.key: item for item in breakdown.dimensions} if breakdown else {}
            novelty = dimensions.get("novelty_opportunity")
            feasibility = dimensions.get("feasibility_data")
            gap = gap_by_id.get(candidate.gap_ids[0]) if candidate.gap_ids else None
            if not novelty or not feasibility or not novelty.max_score or not feasibility.max_score:
                continue
            radar_rows.append({
                "candidate_id": candidate.candidate_id,
                "label": candidate.title,
                "values": [
                    round(novelty.score / novelty.max_score * 50, 2),
                    round(feasibility.score / feasibility.max_score * 50, 2),
                    round((1 - min(1.0, max(0.0, gap.competition_heat if gap else 0.0))) * 50, 2),
                ],
            })
        missing_scores = len(state.candidates[:5]) - len(radar_rows)
        from .discovery import selected_opportunities
        cross_rows = [
            {"crosslink_id": link.crosslink_id, "core": link.core_discipline,
             "expansion": link.expansion_discipline, "method": link.transferable_method,
             "reason": link.relation_basis, "opportunity": link.research_opportunity or link.relation_basis,
            "evidence_ids": list(link.support_evidence_ids), "participating_disciplines": [item.model_dump() for item in link.participating_disciplines],
             "method_evidence": list(link.method_evidence), "association_kind": link.association_kind, "validation_conditions": link.validation_conditions, "status": "方法迁移关系" if link.method_evidence else "研究线索"}
            for link in selected_opportunities(state)
        ]
        for index, row in enumerate(cross_rows, start=1):
            row["number"] = index
            row["color"] = ("#5269c7", "#228579", "#b77928", "#9060ac", "#b75465", "#437f9d")[(index - 1) % 6]
        specs = [
            {"key": "discipline_network", "title": "学科与研究方向网络", "chart_type": "discipline", "visual_role": "opportunity_network", "section_key": "cross_discipline",
             "data": {"root": state.theme_tree.root, "rows": cross_rows, "display_limit": 3, "method_evolution": state.stage_artifacts.get("landscape_trend", {}).get("analysis", {}).get("method_evolution", [])},
             "source_ids": list(dict.fromkeys(eid for row in cross_rows for eid in row["evidence_ids"])),
             "data_snapshot_version": snapshot, "report_version": report_version,
             "caption": "同色连线关联同一研究方向；参与学科提供相应方法与研究视角。"},
            {"key": "theme_network", "title": "研究主题层级", "chart_type": "network", "visual_role": "theme_hierarchy", "data": {"root": state.theme_tree.root, "display_root": state.theme_tree.display_root, "nodes": [node.model_dump() for node in state.theme_tree.nodes], "edges": state.theme_tree.edges}, "source_ids": [item.evidence_id for item in state.evidence_pool], "data_snapshot_version": snapshot, "report_version": report_version, "caption": "层级只表示已记录的主题归属；关键词为叶节点标签，父节点缺失或循环关系单列待确认。"},
            {"key": "trend_comparison", "title": "年度文献与子方向趋势", "chart_type": "stacked_bar", "section_key": "literature_trends", "visual_role": "trend", "data": {"years": years, "series": theme_series}, "source_ids": [item.evidence_id for item in state.evidence_pool], "data_snapshot_version": snapshot, "report_version": report_version, "caption": "按年份堆叠展示各子方向在当前锁定样本中的文献数量；样本覆盖不代表领域全部产出。"},
            {"key": "core_literature_top10", "title": "核心文献被引 Top10", "chart_type": "bar", "section_key": "core_literature", "visual_role": "core_literature", "data": [{"label": item.title, "citation_number": state.report_plan.citation_map.get(item.evidence_id), "value": item.citation_count, "evidence_id": item.evidence_id} for item in top_papers], "source_ids": [item.evidence_id for item in top_papers], "data_snapshot_version": snapshot, "report_version": report_version, "data_basis": "citation_count", "caption": "横轴为检索网关返回的实际被引次数，并使用对数刻度；未返回被引数据时不生成图表。"},
            {"key": "gap_evidence_coverage", "title": "竞争热度与空白空间", "chart_type": "bubble", "visual_role": "gap_opportunity", "data": [{"label": gap.gap_id, "x": round(gap.competition_heat * 50, 2), "y": round(gap.opportunity_space * 50, 2), "size": max(1, len(gap.evidence_ids)), "color": round(sum(item.citation_count for item in state.evidence_pool if item.evidence_id in gap.evidence_ids) / max(1, len(gap.evidence_ids)), 2), "status": gap.validation_status} for gap in state.gaps], "source_ids": [item.evidence_id for item in state.evidence_pool], "data_snapshot_version": snapshot, "report_version": report_version, "caption": "横轴为竞争热度，纵轴为空白空间；气泡大小表示支持样本量，颜色深浅表示篇均被引。零记录只表示当前检索覆盖不足。"},
            {"key": "candidate_radar", "title": "候选题三维评估", "chart_type": "radar", "section_key": "candidate_evaluation", "visual_role": "candidate_evaluation", "data": {"dimensions": ["新颖性", "可行性", "逆竞争压力"], "max_value": 50, "rows": radar_rows}, "source_ids": [item.evidence_id for item in state.evidence_pool], "data_snapshot_version": snapshot, "report_version": report_version, "caption": f"三项满分均为 50；逆竞争压力越高表示竞争压力越低。{missing_scores} 个候选题缺少可比较评分，未绘入图中。"},
        ]
        for index, spec in enumerate(specs, start=1):
            spec.setdefault("figure_number", index)
            spec.setdefault("placement", "inline")
            spec.setdefault("source_note", f"来源：本次锁定证据池；数据截止时间：{TopicSelectionAgent._report_cutoff(state.source_policy.evidence_cutoff_at)}")
            spec.setdefault("chart_version", "chart-v11")
            spec.setdefault("min_data_points", {"trend_comparison": 2, "gap_evidence_coverage": 2, "candidate_radar": 3}.get(spec["key"], 1))
            if spec["key"] == "theme_network": ready = len(spec["data"].get("nodes", [])) >= 1
            elif spec["key"] == "discipline_network": ready = bool(cross_rows)
            elif spec["key"] == "trend_comparison": ready = len(spec["data"].get("years", [])) >= 2 and bool(spec["data"].get("series"))
            elif spec["key"] == "gap_evidence_coverage":
                points = {(float(item.get("x") or 0), float(item.get("y") or 0)) for item in spec["data"]}
                ready = len(spec["data"]) >= 2 and len(points) >= 2
            elif spec["key"] == "candidate_radar": ready = len(spec["data"].get("dimensions", [])) >= 3 and bool(spec["data"].get("rows"))
            elif spec["key"] == "core_literature_top10": ready = bool(spec["data"]) and any(item.get("value", 0) > 0 for item in spec["data"])
            else: ready = len(spec["data"]) >= 1
            spec["data_status"] = "ready" if ready else "insufficient"
            spec["empty_reason"] = "数据不足：证据数量或结构化维度未达到成图阈值，保留文字限制说明。" if not ready else ""
            spec["limitation"] = spec["empty_reason"]
            spec["alt_text"] = f"{spec['title']}；{spec['empty_reason'] or '展示当前报告快照中的结构化证据。'}"
        return specs

    @staticmethod
    def _markdown_report(state: TopicSelectionState) -> str:
        from app.services.topic_selection_report import _structured_markdown
        return _structured_markdown(state)
