"""Read-only live retrieval -> agent fusion -> report export acceptance check.

Run from the project root. Uses configured credentials without printing them.
Creates local evidence artifacts, never persists tasks or changes the library.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import zipfile
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

CASES = [
    ("battery-zh", "固态电池中界面接触和堆叠压力如何影响锂枝晶形成与循环稳定性？"),
    ("battery-en", "How do interfaces and stack pressure affect lithium dendrite formation and cycling stability in solid-state batteries?"),
    ("organ-chip-zh", "器官芯片中的肿瘤细胞与免疫细胞共培养如何用于抗肿瘤药物响应评估？"),
    ("organ-chip-en", "How does co-culture of tumour cells and immune cells in organ-on-chip models evaluate anticancer drug responses?"),
    ("generative-ai-zh", "生成式人工智能如何影响科研知识发现、协作方式与评价范式？"),
    ("generative-ai-en", "How does generative AI affect scientific knowledge discovery, research collaboration, and evaluation practices?"),
]


async def verify(output: Path) -> list[dict]:
    from app.agents.content_assessment.graph import ContentAssessmentGraph
    from app.agents.content_assessment.report_composition import build_specs, compose, freeze_snapshot
    from app.agents.content_assessment.schemas import Claim, ClaimEvidenceLink
    from app.agents.topic_selection.agent import TopicSelectionAgent
    from app.agents.topic_selection.schemas import ThemeNode, ThemeTree
    from app.common.retrieval.fulltext import FulltextIntent, LiteratureFulltextClient
    from app.common.retrieval.fusion import selected_chunks
    from app.services.export_service import build_docx, build_html, build_markdown

    client = LiteratureFulltextClient()
    if not client.api_url:
        raise SystemExit("请先配置 LITERATURE_FULLTEXT_API_URL 与服务凭证。")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for case_id, query in CASES:
        intent = FulltextIntent(intent_id=case_id, query=query, theme_ids=["T1"], claim_ids=["C1"])
        started = time.monotonic()
        response = await asyncio.to_thread(client.query_chunks, query=query, source_agent="acceptance",
            intent_id=case_id, year_range=intent.year_range, languages=intent.languages)
        retrieval_seconds = round(time.monotonic() - started, 2)
        selected = selected_chunks(intent, response)
        topic = TopicSelectionAgent(fulltext_client=client)
        topic_state = topic.create_initial_state(query, analysis_mode="fast")
        topic_state.theme_tree = ThemeTree(nodes=[ThemeNode(theme_id="T1", label=query)])
        await topic._fuse_problem_fulltext(topic_state, [(intent, response)], None)
        graph = ContentAssessmentGraph(fulltext_client=client)
        assessment = graph.create_initial_state(query)
        assessment.claims = [Claim(claim_id="C1", text=query), Claim(claim_id="C2", text="无关主张：交通污染与呼吸健康")]
        graph._fuse_problem_fulltext(assessment, [(intent, response)])
        linked = []
        for record in assessment.evidence_records[:5]:
            chunks = [c for c in assessment.evidence_chunks if c.evidence_id == record.evidence_id]
            if not chunks:
                continue
            # Relevance is not a support verdict. Live acceptance quotes are
            # background evidence, without fabricating a model comparison.
            assessment.claim_evidence_links.append(ClaimEvidenceLink(claim_id="C1", evidence_id=record.evidence_id,
                relation="not_comparable", evidence_level="A", evidence_chunk_ids=[c.chunk_id for c in chunks],
                uncertainty="检索验收仅验证相关正文与报告引用，未进行支持关系判定。"))
            linked.append(record)
        assert all(graph._claim_evidence_view(assessment, assessment.claims[1], e) is None for e in linked)
        assessment.report_plan.section_specs = build_specs(assessment)
        compose(assessment, assessment.report_plan.section_specs)
        freeze_snapshot(assessment)
        markdown, _ = build_markdown(assessment)
        html, _ = build_html(assessment)
        docx, _ = build_docx(assessment)
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            word = archive.read("word/document.xml").decode()
        first = assessment.evidence_chunks[0].excerpt[:100] if assessment.evidence_chunks else ""
        # Export escaping differs; compare decoded visible content.
        visible = [markdown, re.sub(r"<[^>]*>", "", html), "".join(ET.fromstring(word).itertext())]
        def compact(value: str) -> str:
            return "".join(unescape(value).split())
        export_matches = [bool(first and compact(first) in compact(v)) for v in visible]
        export_ok = all(export_matches)
        (output / f"{case_id}.md").write_bytes(markdown if isinstance(markdown, bytes) else markdown.encode())
        (output / f"{case_id}.html").write_bytes(html if isinstance(html, bytes) else html.encode())
        (output / f"{case_id}.docx").write_bytes(docx)
        (output / f"{case_id}-evidence.json").write_text(json.dumps([c.model_dump() for c in selected], ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"case": case_id, "query": query, "success": response.success,
            "retrieval_seconds": retrieval_seconds,
            "duration_seconds": round(time.monotonic() - started, 2), "returned_chunks": len(response.chunks),
            "selected_chunks": len(selected), "topic_papers": len(topic_state.evidence_pool),
            "assessment_papers": len(assessment.evidence_records), "export_contains_quote": export_ok,
            "export_checks": dict(zip(("markdown", "html", "docx"), export_matches)),
            "claim_isolation_verified": True, "limitation": response.error,
            "sources": [{"title": e.title, "year": e.year, "doi": e.doi, "locator": e.locator,
                "excerpt": e.excerpt[:500]} for e in assessment.evidence_records[:5]]}
        if not (result["success"] and result["selected_chunks"] and result["topic_papers"]
                and result["assessment_papers"] and result["export_contains_quote"]):
            raise RuntimeError(f"实库验收未通过：{case_id}")
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in {"sources", "query"}}, ensure_ascii=False), flush=True)
    (output / "results.json").write_text(json.dumps({"verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "真实全文查询、两个智能体融合、主张隔离、三种报告导出；未调用结论生成模型", "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".tmp" / "qa" / "fulltext-fusion-live")
    args = parser.parse_args()
    verified = asyncio.run(verify(args.output))
    raise SystemExit(0)
