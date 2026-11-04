from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.agents.content_assessment import node as content_assessment_node  # noqa: E402
from app.agents.content_assessment.node import ContentAssessmentNodeExecutor  # noqa: E402
from app.agents.content_assessment.schemas import (  # noqa: E402
    EvidenceItem,
    FinalTaskProfile,
    ContentAssessmentState,
    InputUnderstanding,
)
from app.services.deepseek_client import DeepSeekClientError  # noqa: E402


class OfflineAnalysisExecutor(ContentAssessmentNodeExecutor):
    def __init__(self) -> None:
        pass


class DummyClient:
    model = "offline-benchmark"
    timeout = 90
    max_retries = 2


def build_state(evidence_count: int) -> ContentAssessmentState:
    state = ContentAssessmentState(raw_user_input="钙钛矿太阳能电池稳定性优化")
    state.standardized_question = "围绕钙钛矿太阳能电池稳定性优化可以提出哪些科研内容评估"
    state.input_understanding = InputUnderstanding(
        topic="钙钛矿太阳能电池稳定性优化",
        user_intent="生成内容评估报告",
        research_domain="材料科学与工程",
        core_entities=["钙钛矿太阳能电池", "稳定性"],
        summary="围绕钙钛矿太阳能电池稳定性优化生成内容评估报告。",
    )
    state.final_task_profile = FinalTaskProfile(
        final_task_title="钙钛矿太阳能电池：稳定性优化",
        research_domain="材料科学与工程",
        research_object="钙钛矿太阳能电池",
        research_goal="稳定性优化",
        confirmed_focus="稳定性优化",
        summary="聚焦钙钛矿太阳能电池稳定性优化。",
    )
    state.retrieval_results = {
        "standard_question": [
            EvidenceItem(
                evidence_id=f"evi_{index}",
                evidence_need_id="need_1",
                source_type="nstl_paper",
                title=f"钙钛矿太阳能电池稳定性研究 {index}",
                authors=["A"],
                year=2026 - (index % 10),
                venue="Journal",
                abstract="该研究讨论钙钛矿太阳能电池稳定性、评价指标、真实场景验证与机制解释。",
                score=1.0 - index * 0.01,
                support_summary="稳定性、场景边界与验证空白。",
            )
            for index in range(1, evidence_count + 1)
        ]
    }
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark content-assessment research analysis pacing.")
    parser.add_argument("--evidence-count", type=int, default=20)
    parser.add_argument("--step-seconds", type=float, default=7.0)
    parser.add_argument("--disable-pacing", action="store_true")
    args = parser.parse_args()

    content_assessment_node.ANALYSIS_STEP_TARGET_SECONDS = args.step_seconds
    content_assessment_node.ANALYSIS_STEP_PACING_ENABLED = not args.disable_pacing
    content_assessment_node.ANALYSIS_MODEL_ENHANCEMENT_ENABLED = True

    executor = OfflineAnalysisExecutor()
    executor.client = DummyClient()

    def offline_generate_json(**_kwargs):
        raise DeepSeekClientError("offline benchmark")

    executor._generate_json = offline_generate_json  # type: ignore[method-assign]

    updates: list[float] = []
    started_at = time.perf_counter()
    state = executor.run_analysis_with_updates(
        build_state(args.evidence_count),
        on_process_step_update=lambda _state: updates.append(time.perf_counter()),
    )
    elapsed = time.perf_counter() - started_at
    print(json.dumps(
        {
            "elapsed_seconds": round(elapsed, 3),
            "updates_count": len(updates),
            "analysis_runtime": state.topic_analysis.get("analysis_runtime", {}),
            "step_durations": [
                {
                    "step": step.step_key,
                    "status": step.status,
                    "duration_seconds": step.duration_seconds,
                    "evidence_refs": step.evidence_refs,
                }
                for step in state.process_trace.get("analysis", [])
            ],
            "coverage": {
                "evidence_total": state.evidence_coverage.get("evidence_total"),
                "used_ref_count": len(state.evidence_coverage.get("used_refs", [])),
                "used_refs": state.evidence_coverage.get("used_refs", []),
            },
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
