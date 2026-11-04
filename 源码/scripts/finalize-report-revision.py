"""Complete missing model comparisons on an authorized associated revision."""
import argparse
import asyncio
from pymongo import MongoClient
from app.core.config import MONGO_URI, MONGO_DB, TASK_COLLECTION
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import ContentAssessmentState
from app.agents.content_assessment.material_extraction import material_context, enrich_indicators
from app.repositories.content_assessment_task_history import get_task_history_repo
from app.services.task_persistence import persist_task_state


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--render-only", action="store_true", help="只用已保存的事实重排报告，不重新检索或调用模型")
    args = parser.parse_args()
    with MongoClient(MONGO_URI) as client:
        record = client[MONGO_DB][TASK_COLLECTION].find_one({"task_id": args.task_id})
    state = ContentAssessmentState.model_validate(record["state"])
    if not state.parent_task_id or record["status"] != "completed":
        raise RuntimeError("仅允许完成已关联原任务的新版报告")
    graph = ContentAssessmentGraph()
    old_payload = state.output_json.get("_model_comparison") or {}
    done = {item["claim_id"] for item in old_payload.get("decisions", [])}
    claims = state.claims[:]
    material_context(state)
    state.indicators = enrich_indicators(state)
    for claim in ([] if args.render_only else claims):
        if claim.claim_id in done:
            continue
        state.claims = [claim]
        state.output_json.pop("_model_comparison", None)
        graph._model_unavailable = False
        await graph._comparison_stage(state)
        payload = state.output_json.get("_model_comparison") or {}
        for key in ("links", "decisions"):
            old_payload.setdefault(key, []).extend(payload.get(key, []))
        print(claim.claim_id, "模型比较完成" if payload.get("decisions") else "保留规则比较", flush=True)
    state.claims = claims
    if not args.render_only:
        state.output_json["_model_comparison"] = old_payload
        graph._comparison(state)
        graph._assessment_stage(state)
        graph._quality_gate(state)
    await graph._report_stage(state)
    state.current_stage = "completed"
    await persist_task_state(task_id=args.task_id, user_scope=state.user_scope, state=state, status="completed")
    print("完成", args.task_id, "PDF 页数", state.report_metrics.actual_page_count,
          "质量检查", state.output_json.get("quality_checks"), flush=True)
    await graph._close_model_client()
    get_task_history_repo().disconnect()


if __name__ == "__main__":
    asyncio.run(main())
