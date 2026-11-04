"""Server-side upload parsing and content safety checks."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

import httpx

from app.core.config import (
    MAX_PDF_PARSE_PAGES,
    SENSITIVE_CHECK_API_URL,
    SENSITIVE_CHECK_CHUNK_SIZE,
    SENSITIVE_CHECK_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class FileParseError(RuntimeError):
    """Raised when an uploaded file cannot be parsed into usable text."""

    def __init__(self, message: str, *, code: str = "file_parse_failed") -> None:
        super().__init__(message)
        self.code = code


class SensitiveContentError(RuntimeError):
    """Raised when an uploaded file contains prohibited content."""

    def __init__(self, message: str, *, hits: list[Any] | None = None) -> None:
        super().__init__(message)
        self.hits = hits or []


class SensitiveCheckUnavailable(RuntimeError):
    """Raised when the configured sensitive-content service is unavailable."""


def supported_upload_extensions() -> set[str]:
    """Formats supported by the homepage material uploader.

    Image uploads are intentionally excluded: OCR availability differs between
    environments and made the same task behave differently across deployments.
    """
    return {".pdf", ".txt", ".md", ".docx", ".xlsx", ".csv", ".pptx"}


def _line_anchors(content: str, *, kind: str, page_count: int | None = None) -> list[dict[str, Any]]:
    """Create deterministic source anchors for client-parsed legacy uploads."""
    anchors: list[dict[str, Any]] = []
    offset = 0
    for index, line in enumerate(content.splitlines(keepends=True), start=1):
        text = line.rstrip("\r\n")
        end = offset + len(text)
        if text.strip():
            locator: dict[str, Any] = {"kind": kind, "line": index}
            if page_count:
                locator["page"] = min(page_count, index)
            anchors.append({"char_start": offset, "char_end": end, "text": text[:500], "locator": locator})
        offset += len(line)
    if not anchors and content:
        anchors.append({"char_start": 0, "char_end": len(content), "text": content[:500], "locator": {"kind": kind, "line": 1}})
    return anchors[:20000]


def _anchors_from_values(content: str, values: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    cursor = 0
    for text, locator in values:
        clean = str(text or "").strip()
        if not clean:
            continue
        start = content.find(clean, cursor)
        if start < 0:
            clean = " ".join(clean.split())
            start = content.find(clean, cursor)
        if start < 0:
            continue
        end = start + len(clean)
        anchors.append({"char_start": start, "char_end": end, "text": clean[:500], "locator": locator})
        cursor = end
    return anchors


async def _build_format_anchors(file_path: Path, ext: str, content: str) -> tuple[list[dict[str, Any]], int | None]:
    """Return format-specific locators while keeping one canonical text range."""
    if ext == ".pdf":
        try:
            import fitz
            values: list[tuple[str, dict[str, Any]]] = []
            with fitz.open(str(file_path)) as document:
                for page_index, page in enumerate(document, start=1):
                    text = " ".join((page.get_text("text") or "").split())
                    if text:
                        values.append((text, {"kind": "pdf", "page": page_index}))
                return _anchors_from_values(content, values), len(document)
        except Exception:
            return _line_anchors(content, kind="pdf"), None
    if ext == ".docx":
        return _anchors_from_values(content, _docx_values(file_path)), None
    if ext == ".csv":
        return _anchors_from_values(
            content,
            [(line, {"kind": "csv_row", "row": index}) for index, line in enumerate(content.splitlines(), start=1)],
        ), None
    if ext == ".pptx":
        try:
            with ZipFile(file_path) as archive:
                slide_names = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
                values = []
                for slide_index, slide_name in enumerate(slide_names, start=1):
                    text = " ".join(_xml_texts(archive.read(slide_name)))
                    values.append((text, {"kind": "pptx_slide", "slide": slide_index}))
                return _anchors_from_values(content, values), len(slide_names)
        except Exception:
            return _line_anchors(content, kind="pptx"), None
    if ext == ".xlsx":
        return _anchors_from_values(content, _xlsx_values(file_path)), None
    if ext in {".png", ".jpg", ".jpeg"}:
        return _line_anchors(content, kind="ocr_block"), 1
    return _line_anchors(content, kind=ext.lstrip(".") or "text"), None


async def parse_uploaded_file_artifact(file_path: Path, file_ext: str) -> dict[str, Any]:
    """Parse content and return a stable, format-neutral anchor index."""
    ext = file_ext.lower()
    content = await parse_uploaded_file_content(file_path, ext)
    anchors, page_count = await _build_format_anchors(file_path, ext, content)
    return {"content": content, "anchors": anchors, "page_count": page_count, "anchor_schema_version": "anchor-v1"}


async def build_uploaded_file_anchors(
    file_path: Path, file_ext: str, content: str
) -> tuple[list[dict[str, Any]], int | None]:
    """Build format-aware anchors for content parsed by a caller."""
    return await _build_format_anchors(file_path, file_ext.lower(), content)


async def parse_uploaded_file_content(file_path: Path, file_ext: str) -> str:
    ext = file_ext.lower()
    if ext in {".txt", ".md"}:
        return await _read_text_file(file_path)
    if ext == ".pdf":
        return await _read_pdf_file(file_path)
    if ext == ".docx":
        return await _read_docx_file(file_path)
    if ext == ".csv":
        return await _read_csv_file(file_path)
    if ext == ".xlsx":
        return await _read_xlsx_file(file_path)
    if ext == ".pptx":
        return await _read_pptx_file(file_path)
    raise FileParseError(
        f"不支持的文件类型：{ext or '未知'}，请上传 PDF、DOCX、XLSX、CSV、PPTX、TXT 或 Markdown。",
        code="unsupported_file_type",
    )


async def _read_text_file(file_path: Path) -> str:
    raw = file_path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileParseError(
        "文本文件编码无法识别，请将文件另存为 UTF-8 后重新上传。",
        code="text_decode_failed",
    )


async def _read_pdf_file(file_path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise FileParseError(
            "服务端缺少 PDF 解析组件，请联系管理员处理。",
            code="pdf_parser_missing",
        ) from exc

    doc = None
    try:
        doc = fitz.open(str(file_path))
        if doc.needs_pass:
            raise FileParseError(
                "这个 PDF 设置了密码保护，暂时无法解析。请去除密码后重新上传。",
                code="pdf_password_protected",
            )
        page_count = len(doc)
        if page_count <= 0:
            raise FileParseError(
                "这个 PDF 没有可读取的页面，请检查文件后重新上传。",
                code="pdf_empty",
            )

        text_parts: list[str] = []
        pages_to_parse = min(page_count, MAX_PDF_PARSE_PAGES)
        for page_index in range(pages_to_parse):
            try:
                text = doc[page_index].get_text("text")
            except Exception as exc:  # pragma: no cover - page-level recovery
                logger.warning("PDF page parse failed path=%s page=%s error=%s", file_path, page_index + 1, exc)
                continue
            if text and text.strip():
                text_parts.append(text.strip())

        return "\n\n".join(text_parts)
    except FileParseError:
        raise
    except Exception as exc:
        logger.warning("PDF parse failed path=%s error=%s", file_path, exc, exc_info=True)
        raise FileParseError(
            "PDF 文件无法解析，请确认文件没有损坏、未加密后重新上传。",
            code="pdf_parse_failed",
        ) from exc
    finally:
        if doc is not None:
            doc.close()


async def _read_docx_file(file_path: Path) -> str:
    return "\n".join(text for text, _ in _docx_values(file_path))


def _docx_values(file_path: Path) -> list[tuple[str, dict[str, Any]]]:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise FileParseError("服务端缺少 DOCX 解析组件。", code="docx_parser_missing") from exc
    try:
        document = Document(str(file_path))
        values: list[tuple[str, dict[str, Any]]] = []
        paragraph_index = table_index = 0
        # Keep tables beside their headings instead of moving them to the end.
        for element in document.element.body:
            if element.tag.rsplit("}", 1)[-1] == "p":
                paragraph_index += 1
                text = " ".join(Paragraph(element, document).text.split())
                if text:
                    values.append((text, {"kind": "docx_paragraph", "paragraph": paragraph_index}))
            elif element.tag.rsplit("}", 1)[-1] == "tbl":
                table_index += 1
                for row_index, row in enumerate(Table(element, document).rows, start=1):
                    cells = [" ".join(cell.text.split()) for cell in row.cells]
                    if any(cells):
                        values.append((" | ".join(cells), {"kind": "docx_row", "table": table_index, "row": row_index}))
        return values
    except Exception as exc:
        raise FileParseError("DOCX 文件无法解析，请确认文件没有损坏。", code="docx_parse_failed") from exc


async def _read_csv_file(file_path: Path) -> str:
    raw = file_path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            rows = csv.reader(io.StringIO(text))
            return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
        except UnicodeDecodeError:
            continue
    raise FileParseError("CSV 文件编码无法识别。", code="csv_decode_failed")


def _xml_texts(payload: bytes) -> list[str]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []
    return [" ".join(text.split()) for text in root.itertext() if text and text.strip()]


async def _read_xlsx_file(file_path: Path) -> str:
    return "\n".join(text for text, _ in _xlsx_values(file_path))


def _xlsx_values(file_path: Path) -> list[tuple[str, dict[str, Any]]]:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise FileParseError("服务端缺少 XLSX 解析组件。", code="xlsx_parser_missing") from exc
    workbook = cached_workbook = None
    try:
        workbook = load_workbook(file_path, read_only=True, data_only=False)
        cached_workbook = load_workbook(file_path, read_only=True, data_only=True)
        values: list[tuple[str, dict[str, Any]]] = []
        for sheet in workbook.worksheets:
            has_rows = False
            for row_index, (row, cached_row) in enumerate(
                zip(sheet.iter_rows(), cached_workbook[sheet.title].iter_rows()), start=1
            ):
                cells: list[str] = []
                for cell, cached_cell in zip(row, cached_row):
                    value = cell.value
                    if cell.data_type == "f":
                        cached = cached_cell.value
                        value = f"公式 {value}；缓存值 {cached}" if cached is not None else f"公式 {value}；结果未缓存，待重算"
                    cells.append(" ".join(str(value).split()) if value is not None else "")
                if not any(cells):
                    continue
                if not has_rows:
                    values.append((f"工作表：{sheet.title}", {"kind": "xlsx_sheet", "sheet": sheet.title}))
                    has_rows = True
                # Preserve internal empty cells so columns cannot silently shift.
                while cells and not cells[-1]:
                    cells.pop()
                values.append((" | ".join(cells), {
                    "kind": "xlsx_row", "sheet": sheet.title, "row": row_index,
                    "range": f"A{row_index}:{get_column_letter(len(cells))}{row_index}",
                }))
        return values
    except Exception as exc:
        raise FileParseError("XLSX 文件无法解析，请确认文件没有损坏。", code="xlsx_parse_failed") from exc
    finally:
        if cached_workbook is not None:
            cached_workbook.close()
        if workbook is not None:
            workbook.close()


async def _read_pptx_file(file_path: Path) -> str:
    try:
        with ZipFile(file_path) as archive:
            slide_names = sorted(
                name for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            parts: list[str] = []
            for slide_name in slide_names:
                parts.extend(_xml_texts(archive.read(slide_name)))
            return "\n".join(dict.fromkeys(parts))
    except (BadZipFile, ElementTree.ParseError, OSError) as exc:
        raise FileParseError("PPTX 文件无法解析，请确认文件没有损坏。", code="pptx_parse_failed") from exc


async def _read_image_ocr(file_path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise FileParseError(
            "当前环境未安装 OCR 组件，图片材料暂不能解析；请先进行 OCR 或上传带文字层的 PDF。",
            code="ocr_unavailable",
        ) from exc
    try:
        return pytesseract.image_to_string(Image.open(file_path), lang="chi_sim+eng")
    except Exception as exc:
        raise FileParseError("图片 OCR 解析失败，请上传清晰图片或文字版材料。", code="ocr_failed") from exc


def ensure_parsed_content_usable(content: str, filename: str, file_ext: str) -> None:
    if content.strip():
        return
    if file_ext.lower() == ".pdf":
        raise FileParseError(
            f"未能从 PDF 中提取到可用文字：{filename}。如果这是扫描件图片版 PDF，请先进行 OCR 识别后再上传。",
            code="pdf_no_text_content",
        )
    raise FileParseError(
        f"文件没有可用文字内容：{filename}。请检查文件内容后重新上传。",
        code="empty_parsed_content",
    )


async def check_sensitive_content(text: str, filename: str) -> None:
    if not SENSITIVE_CHECK_API_URL.strip():
        raise SensitiveCheckUnavailable("文件安全校验服务未配置，请联系管理员处理。")

    checks = [("filename", filename)]
    if text.strip():
        checks.extend([
            (f"content-{index + 1}", chunk)
            for index, chunk in enumerate(_chunk_text(text, SENSITIVE_CHECK_CHUNK_SIZE))
        ])

    async with httpx.AsyncClient(timeout=SENSITIVE_CHECK_TIMEOUT_SECONDS) as client:
        for check_id, check_text in checks:
            if not check_text.strip():
                continue
            try:
                response = await client.post(
                    SENSITIVE_CHECK_API_URL,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={"contents": [{"id": check_id, "text": check_text}]},
                )
                response.raise_for_status()
                result = response.json()
                hits = _extract_sensitive_hits(result, check_id=check_id)
            except Exception as exc:
                logger.warning("Sensitive-content check unavailable filename=%s error=%s", filename, exc)
                raise SensitiveCheckUnavailable("文件安全校验服务暂不可用，请稍后重试。") from exc

            if hits:
                raise SensitiveContentError(
                    f"文件包含不适合上传的内容，请修改后重新上传：{filename}",
                    hits=hits,
                )


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    size = max(1024, chunk_size)
    # Overlap keeps terms crossing a chunk boundary visible to the checker.
    step = size - min(256, size // 4)
    chunks: list[str] = []
    for index in range(0, len(text), step):
        chunks.append(text[index : index + size])
        if index + size >= len(text):
            break
    return chunks


def _extract_sensitive_hits(result: Any, *, check_id: str | None = None) -> list[Any]:
    if not isinstance(result, dict) or result.get("code", "0") not in ("0", 0, "200", 200):
        raise SensitiveCheckUnavailable("文件安全校验服务返回异常，请稍后重试。")
    hits: list[Any] = []
    data = result.get("data")
    if not isinstance(data, list) or not data:
        raise SensitiveCheckUnavailable("文件安全校验服务未返回有效检测结果，请稍后重试。")
    if check_id is not None and not any(isinstance(item, dict) and item.get("id") == check_id for item in data):
        raise SensitiveCheckUnavailable("文件安全校验服务未返回对应检测结果，请稍后重试。")
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("hasSensitive"), bool):
            raise SensitiveCheckUnavailable("文件安全校验服务返回无效检测状态，请稍后重试。")
        if item["hasSensitive"] is not True:
            continue
        item_hits = item.get("sensitiveHits") or item.get("hits") or []
        if not item_hits:
            hits.append({"id": item.get("id"), "hasSensitive": True})
        elif isinstance(item_hits, list):
            hits.extend(item_hits)
        else:
            hits.append(item_hits)
    return hits
