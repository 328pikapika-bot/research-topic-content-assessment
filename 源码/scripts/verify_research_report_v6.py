"""Generate isolated paper/project/guideline samples through the product exporter.

No model, retrieval service or application database is called. All scientific
content is synthetic; these files only validate structure and rendering.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests" / "backend")]
os.environ["CONTENT_ASSESSMENT_LLM_ENABLED"] = "false"
os.environ["USE_MEMORY_STORE"] = "true"

import fitz
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import StageTraceItem
from app.agents.content_assessment.research_outline import chapter_definitions, evaluation_dimensions
from app.services.export_service import build_docx, build_pdf, build_markdown
from report_rich_fixture import rich_state
from test_research_report_v6 import research_sample


def main():
    output = ROOT / ".tmp" / "qa" / "research-report-v6"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for kind in ("paper", "project", "guideline"):
        state = rich_state("standard")
        state.report_snapshot = None
        state.report_preferences.artifact_type = state.report_preferences.resolved_artifact_type = kind
        state.assessment_profile.title = state.assessment_profile.assessment_object = "工程验收模拟资料—" + kind
        state.output_json["scholarly_drafts"] = {}
        state.report_plan.target_page_min = state.report_plan.target_page_max = 0
        dimensions = dict(chapter_definitions(kind)[6][2])
        for claim in state.claims:
            ids = [e.evidence_id for e in state.evidence_records if e.evidence_id.startswith("E" + str(int(claim.claim_id[-3:])))]
            draft = {"progress": [], "comparison": [], "judgement": [], "evaluation": []}
            for dim, field in (("representatives", "method"), ("advances", "data"), ("trends", "validation")):
                draft["progress"].append({"text": "模拟来源《" + state.evidence_records[next(i for i,e in enumerate(state.evidence_records) if e.evidence_id == ids[0])].title + "》记载：" + claim.claim_fields[field],
                    "evidence_ids": [ids[0]], "dimension": dim})
            for index, (field, value) in enumerate(claim.claim_fields.items()):
                draft["comparison"].append({"text": "材料中的" + field + "条件：" + value + "比较判断限定于上述模拟材料和文献条件，不能据此认定目标已实现。",
                    "evidence_ids": [ids[index % len(ids)]], "dimension": "points"})
            for index, (dim, title) in enumerate(dimensions.items()):
                value = list(claim.claim_fields.values())[index % len(claim.claim_fields)]
                draft["judgement"].append({"text": title + "的判断依据：" + value + "本项为工程验收模拟评价，研究目标与文献观测分别解释。",
                    "evidence_ids": [ids[index % len(ids)]], "dimension": dim})
            for index, (dim, title) in enumerate(evaluation_dimensions(kind).items()):
                draft["evaluation"].append({"text": title + "的评价边界：" + claim.claim_fields["validation"] + "本模拟材料尚不构成独立实施或符合性认证。",
                    "evidence_ids": [ids[index % len(ids)]], "dimension": dim})
            state.output_json["scholarly_drafts"][claim.claim_id] = draft
        state.output_json["scholarly_summary"] = [{"text": "本报告仅为工程验收的模拟报告，检查研究内容、证据对应和结论边界，不代表真实科研结论。", "evidence_ids": ["E11"]}]
        state.output_json["scholarly_conclusion"] = [{"text": "模拟材料提供可比较的测量链路，尚待验证的目标和独立观测分别列示；工程验收结果不能作为真实创新性判断。", "evidence_ids": ["E11"]}]
        ContentAssessmentGraph()._report(state)
        frozen = [(s.title, s.content) for s in state.report_snapshot.sections]
        docx, _ = build_docx(state)
        pdf, _ = build_pdf(state)
        md, _ = build_markdown(state)
        (output / f"{kind}.docx").write_bytes(docx)
        (output / f"{kind}.pdf").write_bytes(pdf)
        (output / f"{kind}.md").write_text(md, encoding="utf-8")
        assert frozen == [(s.title, s.content) for s in state.report_snapshot.sections]
        with fitz.open(stream=pdf, filetype="pdf") as document:
            text = "\n".join(p.get_text() for p in document)
            toc = document.get_toc()
            for _, title, _ in chapter_definitions(kind):
                assert title in text, (kind, title)
                assert any(title == row[1] for row in toc), (kind, title, "bookmark")
            assert all("附录" + letter in text for letter in "ABCD")
            assert "主要问题与改进建议" not in text
            assert "�" not in text
            images = output / kind
            images.mkdir(exist_ok=True)
            for index, page in enumerate(document):
                assert page.get_text().strip(), (kind, index, "blank page")
                page.get_pixmap(matrix=fitz.Matrix(1, 1)).save(str(images / f"page-{index+1:02d}.png"))
            results.append({"type": kind, "pages": len(document), "bookmarks": len(toc),
                "citations": len(state.report_snapshot.citation_numbers), "content_preserved": True})
        if kind == "project":
            ui = research_sample(kind)
            ContentAssessmentGraph()._report(ui)
            ui.stage_trace = [StageTraceItem(stage_key=key, stage_name=key, status="completed", progress_percent=100)
                for key in ("document_intake", "claim_extraction", "retrieval", "comparison", "assessment", "landscape", "quality_gate", "report")]
            (output / "state.json").write_text(ui.model_dump_json(indent=2), encoding="utf-8")
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
