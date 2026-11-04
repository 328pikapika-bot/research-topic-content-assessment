from __future__ import annotations

import re
import shutil
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document

from app.services.report_pdf_runtime import render_docx


SOURCE = Path(__file__).resolve().parents[2] / "资料" / "测试反馈" / "首轮优化" / "微流控器件制造与验证指南_V7新版"
TARGET = SOURCE.parent / "微流控器件制造与验证指南_V7优化稿"
STEM = "微流控器件制造与验证指南_创新性评估报告_V7"
INTERNAL = re.compile(r"字段覆盖\s*\d+\s*/\s*\d+|关键词交集\s*\d+\s*项?|缺少.{0,60}(?:字段|锚点|服务)|结论强度|部分支持|字符锚点|内部评分|综合评分|质量门")
REMOVED_APPENDICES = {"A", "C", "D", "E"}


def compact_markdown(source: str) -> str:
    lines = source.splitlines()
    result: list[str] = []
    appendix = ""
    table_claims: dict[str, int] = {}
    in_crosswalk = False
    for line in lines:
        heading = re.match(r"^## 附录([A-E])\b", line)
        if heading:
            appendix = heading.group(1)
            in_crosswalk = appendix == "B"
            if appendix in REMOVED_APPENDICES:
                continue
            result.append(line)
            continue
        if appendix in REMOVED_APPENDICES:
            continue
        if INTERNAL.search(line):
            continue
        if in_crosswalk and line.startswith("|") and not re.match(r"\|\s*:?-{3,}", line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and cells[0] not in {"评估要点", "---"}:
                table_claims[cells[0]] = table_claims.get(cells[0], 0) + 1
                if table_claims[cells[0]] > 3:
                    continue
        result.append(line)
    return "\n".join(result).rstrip() + "\n"


def compact_html(source: str) -> str:
    soup = BeautifulSoup(source, "html.parser")
    main = soup.find("main") or soup.body
    if not main:
        return source
    appendix = ""
    for node in list(main.children):
        if getattr(node, "name", None) in {"h1", "h2", "h3", "h4"}:
            match = re.match(r"附录\s*([A-E])", node.get_text(" ", strip=True))
            if match:
                appendix = match.group(1)
        if appendix in REMOVED_APPENDICES or (getattr(node, "name", None) == "p" and INTERNAL.search(node.get_text(" ", strip=True))):
            node.decompose()
    return str(soup)


def compact_docx(source: Path, target: Path) -> bytes:
    doc = Document(source)
    body = doc._element.body
    appendix = ""
    for child in list(body):
        text = ""
        if child.tag.endswith("}p"):
            text = "".join(child.itertext())
        heading = re.match(r"附录\s*([A-E])", text.strip())
        if heading:
            appendix = heading.group(1)
        if appendix in REMOVED_APPENDICES or (text and INTERNAL.search(text)):
            body.remove(child)
    doc.save(target)
    return target.read_bytes()


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    md_path = SOURCE / f"{STEM}.md"
    html_path = SOURCE / f"{STEM}.html"
    docx_path = SOURCE / f"{STEM}.docx"
    markdown = compact_markdown(md_path.read_text(encoding="utf-8"))
    (TARGET / f"{STEM}_优化稿.md").write_text(markdown, encoding="utf-8")
    (TARGET / f"{STEM}_优化稿.html").write_text(compact_html(html_path.read_text(encoding="utf-8")), encoding="utf-8")
    compacted_docx = TARGET / f"{STEM}_优化稿.docx"
    payload = compact_docx(docx_path, compacted_docx)
    normalized, pdf = render_docx(payload)
    compacted_docx.write_bytes(normalized)
    (TARGET / f"{STEM}_优化稿.pdf").write_bytes(pdf)
    print(f"Wrote optimized report set to {TARGET}")


if __name__ == "__main__":
    main()
