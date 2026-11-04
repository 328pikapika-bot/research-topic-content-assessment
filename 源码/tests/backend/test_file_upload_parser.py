import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from docx import Document
from openpyxl import Workbook, load_workbook

from app.services import file_upload_parser as parser


def parse(path):
    return asyncio.run(parser.parse_uploaded_file_artifact(path, path.suffix))


def assert_anchors(artifact):
    assert artifact["anchors"]
    for anchor in artifact["anchors"]:
        assert artifact["content"][anchor["char_start"]:anchor["char_end"]][:500] == anchor["text"]


def test_docx_preserves_paragraph_table_order_and_anchors(tmp_path):
    document = Document()
    document.add_paragraph("Before\t table")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Claim C1"
    table.cell(0, 1).text = "First line\nSecond line"
    document.add_paragraph("After table")
    path = tmp_path / "project.docx"
    document.save(path)
    artifact = parse(path)
    assert artifact["content"] == "Before table\nClaim C1 | First line Second line\nAfter table"
    assert artifact["anchors"][1]["locator"] == {"kind": "docx_row", "table": 1, "row": 1}
    assert_anchors(artifact)


def test_xlsx_inline_strings_sheets_sparse_columns_dates_and_formulas(tmp_path):
    workbook = Workbook()
    first = workbook.active
    first.title = "Measurements"
    first.append(["Claim", None, "Rate"])
    first.append(["C1", None, 0])
    first["A5"] = "Drug safety"
    first["C5"] = "=C2+1"
    first["D5"] = datetime(2026, 9, 15)
    second = workbook.create_sheet("Evidence")
    second.append(["Claim", "DOI"])
    second.append(["C1", "10.1000/example"])
    path = tmp_path / "evidence.xlsx"
    workbook.save(path)
    artifact = parse(path)
    text = artifact["content"]
    assert "Claim |  | Rate" in text
    assert "C1 |  | 0" in text
    assert "=C2+1" in text
    assert "结果未缓存，待重算" in text
    assert "2026-09-15" in text
    assert "工作表：Measurements" in text
    assert "工作表：Evidence" in text
    locators = [item["locator"] for item in artifact["anchors"]]
    assert {"kind": "xlsx_row", "sheet": "Measurements", "row": 5, "range": "A5:D5"} in locators
    assert {"kind": "xlsx_row", "sheet": "Evidence", "row": 2, "range": "A2:B2"} in locators
    assert_anchors(artifact)


def test_empty_xlsx_does_not_pass_because_of_sheet_name(tmp_path):
    path = tmp_path / "empty.xlsx"
    Workbook().save(path)
    artifact = parse(path)
    assert artifact["content"] == ""
    with pytest.raises(parser.FileParseError, match="没有可用文字"):
        parser.ensure_parsed_content_usable(artifact["content"], path.name, path.suffix)


@pytest.mark.parametrize("extension", [".docx", ".xlsx"])
def test_corrupt_office_file_reports_format_error(tmp_path, extension):
    path = tmp_path / f"corrupt{extension}"
    path.write_bytes(b"not an Office file")
    with pytest.raises(parser.FileParseError) as exc:
        parse(path)
    assert exc.value.code == f"{extension[1:]}_parse_failed"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_csv_chinese_encoding_and_empty_column(tmp_path, encoding):
    path = tmp_path / "matrix.csv"
    path.write_bytes('主张,基线,指标\nC1,,"肝毒性,心毒性"'.encode(encoding))
    artifact = parse(path)
    assert "主张 | 基线 | 指标" in artifact["content"]
    assert "C1 |  | 肝毒性,心毒性" in artifact["content"]


def mock_checker(monkeypatch, handler):
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(parser, "SENSITIVE_CHECK_API_URL", "https://checker.invalid/text")
    monkeypatch.setattr(parser.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))


def test_sensitive_check_checks_filename_and_all_content_chunks(monkeypatch):
    calls = []
    def handler(request):
        item = json.loads(request.content)["contents"][0]
        calls.append(item)
        return httpx.Response(200, json={"code": "0", "data": [{"id": item["id"], "hasSensitive": False}]})
    mock_checker(monkeypatch, handler)
    monkeypatch.setattr(parser, "SENSITIVE_CHECK_CHUNK_SIZE", 1024)
    text = "a" * 1022 + "boundary-term" + "b" * 1500
    asyncio.run(parser.check_sensitive_content(text, "research.docx"))
    assert calls[0] == {"id": "filename", "text": "research.docx"}
    assert len(calls) > 2
    assert any("boundary-term" in item["text"] for item in calls[1:])
    assert calls[-1]["text"].endswith("b" * 100)


@pytest.mark.parametrize("hits", [None, [], [{"word": "blocked-term"}]])
def test_sensitive_boolean_blocks_even_without_hit_details(monkeypatch, hits):
    def handler(request):
        item = json.loads(request.content)["contents"][0]
        return httpx.Response(200, json={"code": "0", "data": [{"id": item["id"], "hasSensitive": True, "sensitiveHits": hits}]})
    mock_checker(monkeypatch, handler)
    with pytest.raises(parser.SensitiveContentError):
        asyncio.run(parser.check_sensitive_content("body", "file.docx"))


@pytest.mark.parametrize("result", [
    {}, [], {"code": "500", "data": []}, {"code": "0", "data": []},
    {"data": [{"id": "filename", "hasSensitive": "false"}]},
    {"data": [{"id": "filename"}]},
    {"data": [{"id": "other-request", "hasSensitive": False}]},
])
def test_invalid_sensitive_response_is_unavailable_not_safe(monkeypatch, result):
    mock_checker(monkeypatch, lambda request: httpx.Response(200, json=result))
    with pytest.raises(parser.SensitiveCheckUnavailable):
        asyncio.run(parser.check_sensitive_content("body", "file.docx"))


def test_sensitive_service_network_failure(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("service unavailable", request=request)
    mock_checker(monkeypatch, handler)
    with pytest.raises(parser.SensitiveCheckUnavailable):
        asyncio.run(parser.check_sensitive_content("body", "file.docx"))


def test_sensitive_service_explicitly_unconfigured(monkeypatch):
    monkeypatch.setattr(parser, "SENSITIVE_CHECK_API_URL", "")
    with pytest.raises(parser.SensitiveCheckUnavailable, match="未配置"):
        asyncio.run(parser.check_sensitive_content("body", "file.docx"))


@pytest.mark.parametrize("prefix", ["01", "02", "03", "04", "05"])
def test_supplied_multiorgan_materials_preserve_text_and_anchors(prefix):
    sample_dir = Path(__file__).resolve().parents[3] / "资料" / "测试输入" / "创新性评估" / "创新性评估智能体测试资料" / "多器官芯片药物肝毒性早筛"
    matches = list(sample_dir.glob(f"{prefix}_*"))
    if not matches:
        pytest.skip("Optional user-supplied material pack is not installed")
    path = matches[0]
    artifact = parse(path)
    assert_anchors(artifact)
    text = artifact["content"]
    assert len(text) > 1000
    if path.suffix == ".docx":
        document = Document(path)
        source_texts = [p.text for p in document.paragraphs]
        source_texts.extend(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    else:
        workbook = load_workbook(path, read_only=True, data_only=False)
        try:
            sheet_names = {a["locator"].get("sheet") for a in artifact["anchors"]}
            assert set(workbook.sheetnames) <= sheet_names
            source_texts = [str(cell.value) for sheet in workbook for row in sheet for cell in row if cell.value is not None]
        finally:
            workbook.close()
    for source_text in source_texts:
        assert " ".join(source_text.split()) in text
