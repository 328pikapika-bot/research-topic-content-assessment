from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ElementTree.register_namespace("x", NS)


def normalize(path: Path) -> None:
    with ZipFile(path, "r") as source:
        parts = {name: source.read(name) for name in source.namelist()}
    for name, payload in list(parts.items()):
        if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
            continue
        root = ElementTree.fromstring(payload)
        if root.find(f"{{{NS}}}dimension") is not None:
            continue
        max_row = 1
        max_col = 1
        for cell in root.findall(f".//{{{NS}}}c"):
            match = re.fullmatch(r"([A-Z]+)(\d+)", cell.get("r", ""))
            if not match:
                continue
            letters, row = match.groups()
            col = 0
            for char in letters:
                col = col * 26 + ord(char) - 64
            max_col = max(max_col, col)
            max_row = max(max_row, int(row))
        def col_name(number: int) -> str:
            value = ""
            while number:
                number, remainder = divmod(number - 1, 26)
                value = chr(65 + remainder) + value
            return value
        dimension = ElementTree.Element(f"{{{NS}}}dimension", {"ref": f"A1:{col_name(max_col)}{max_row}"})
        root.insert(0, dimension)
        parts[name] = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    fd, tmp_name = tempfile.mkstemp(prefix="xlsx-normalize-", suffix=".xlsx", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with ZipFile(tmp_path, "w", ZIP_DEFLATED) as target:
            for name, payload in parts.items():
                target.writestr(name, payload)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2] / "资料" / "测试输入" / "创新性评估" / "创新性评估智能体测试资料" / "五领域实测报告集_20260917"
    paths = sorted(root.rglob("*.xlsx"))
    for item in paths:
        normalize(item)
    print(f"normalized={len(paths)}")
