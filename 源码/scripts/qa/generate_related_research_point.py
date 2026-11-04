"""Read-only one-point generation check; never writes tasks or report snapshots."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import ContentAssessmentState


async def main():
    fixtures = ROOT / "tests/fixtures/related-research-depth-qa"
    output = ROOT / ".tmp/qa/related-research-depth"
    output.mkdir(parents=True, exist_ok=True)
    detail = json.loads((fixtures / "original-task.json").read_text(encoding="utf-8"))
    state = ContentAssessmentState.model_validate(detail["state"])
    claim = next(c for c in state.claims if c.claim_id == "C-002")
    state.claims = [claim]
    state.report_snapshot = None
    graph_module.CONTENT_ASSESSMENT_LLM_ENABLED = True
    graph = ContentAssessmentGraph()
    request = graph._request_model_json
    responses = []
    replay = json.loads((fixtures / "model-responses.json").read_text(encoding="utf-8")) if "--replay" in sys.argv else None

    async def capture(*args, **kwargs):
        payload = replay[len(responses)] if replay is not None else await request(*args, **kwargs)
        responses.append(payload)
        if replay is None:
            (output / "model-responses.json").write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    graph._request_model_json = capture
    try:
        await graph._comparison_stage(state)
    finally:
        await graph._close_model_client()
    # Save an isolated replay of the actual generated point. Published data is
    # neither replaced nor used to pretend this draft was an historical result.
    detail["state"] = state.model_dump(mode="json")
    detail["task_id"] = detail["id"] = "related-research-preview"
    detail["title"] = "器官芯片研究标准化指南 · 单要点优化预览"
    detail["state"]["assessment_profile"]["title"] = detail["title"]
    detail["state"]["output_markdown"] = detail["output_markdown"] = ""
    (output / "preview-task.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"analysis": state.output_json.get("reader_research_analyses", {}).get(claim.claim_id),
              "comparisons": state.output_json.get("reader_comparisons", {}).get(claim.claim_id),
              "checks": state.output_json.get("reader_analysis_checks", {}).get(claim.claim_id)}
    (output / "generated-point.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"material_summary": bool(result["analysis"].get("material_summary")),
        "synthesis_paragraphs": len(result["analysis"].get("literature_summary", [])),
        "contacts": {eid: len(row.get("similarity", [])) for eid, row in (result["comparisons"] or {}).items()},
        "checks": result["checks"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
