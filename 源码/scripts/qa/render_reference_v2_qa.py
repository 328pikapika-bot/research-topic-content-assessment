"""Generate isolated QA artifacts; synthetic fixture never enters production."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests/backend")]
import fitz
from report_rich_fixture import rich_state
from app.services import export_service

destination = ROOT / ".tmp/qa/report-reference-v2"
destination.mkdir(parents=True, exist_ok=True)
state = rich_state("standard")
for fmt, renderer in (("docx", export_service.build_docx), ("pdf", export_service.build_pdf), ("md", export_service.build_markdown), ("html", export_service.build_html)):
    data, _ = renderer(state)
    (destination / f"report.{fmt}").write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
(destination / "snapshot.json").write_text(json.dumps(state.report_snapshot.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
with fitz.open(destination / "report.pdf") as pdf:
    print(f"Pages: {len(pdf)}; bookmarks: {len(pdf.get_toc())}; status: {state.report_metrics.page_target_status}")
    for i in range(len(pdf)):
        pdf[i].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(destination / f"page-{i+1:02d}.png")
    for i, page in enumerate(pdf):
        headings = [t for t in page.get_text().splitlines() if t.startswith(("第", "9.", "表 5-", "图 ", "参考文献", "附录"))]
        print(f"{i+1}: {' / '.join(headings)[:180]}")
