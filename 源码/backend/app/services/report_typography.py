"""Shared report typography, independent of frozen research content.

The reference uses Noto Serif CJK SC: 12 pt body, 21.6 pt leading and
two-character indentation. Static local faces are prepared by
scripts/setup-report-fonts.py to avoid Writer selecting a variable Thin face.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

TYPOGRAPHY_VERSION = "serif-reference-20261015-v1"
FONT_DIRECTORY = Path(__file__).resolve().parents[3] / ".runtime/report-tools/fonts"


def report_font_path(bold: bool = False) -> str:
    face = "Bold" if bold else "Regular"
    candidates = [os.getenv("REPORT_CJK_BOLD_FONT" if bold else "REPORT_CJK_FONT", ""),
                  str(FONT_DIRECTORY / f"ResearchReportSerif-{face}.ttf"),
                  f"/usr/share/fonts/opentype/noto/NotoSerifCJK-{face}.ttc",
                  f"/usr/share/fonts/opentype/noto/NotoSerifCJKsc-{face}.otf",
                  r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"]
    return next((p for p in candidates if p and Path(p).is_file()), "")


def report_font_family() -> str:
    if (FONT_DIRECTORY / "ResearchReportSerif-Regular.ttf").is_file():
        return "Research Report Serif"
    return "Noto Serif SC" if os.name == "nt" else "Noto Serif CJK SC"


# Appended after each renderer's existing layout CSS. Content, links, table
# widths, TOC anchors and chapter structure are deliberately left intact.
REPORT_TYPOGRAPHY_CSS = """
body {font-family:'Research Report Serif','Noto Serif CJK SC','Noto Serif SC','Source Han Serif SC',SimSun,serif;
      font-size:12pt;line-height:1.8;color:#1a1a1a;font-weight:400;}
h1,h2,h3,h4,h5,h6 {font-family:inherit;color:#1a1a1a;font-weight:700;line-height:1.45;}
h1 {font-size:26pt;} h2 {font-size:19pt;border-bottom-color:#d9d9d9;}
h3 {font-size:16pt;} h4 {font-size:14pt;} h5,h6 {font-size:12pt;}
.report-head h1 {font-size:26pt;line-height:1.45;color:#1a1a1a;}
.candidate h3,p.lead {color:#1a1a1a;}
p {line-height:1.8;} a,.citation,.toc a {color:#2c6fbb;}
table {font-size:10.5pt;line-height:1.45;} th,td {line-height:1.45;}
th {color:#1a1a1a;background:#f3f4f5;}
.meta,.metadata,.metadata p,.report-meta,figcaption,.caption,.source-note {font-size:10.5pt;line-height:1.6;color:#666;}
.report-head {border-color:#d9d9d9;}
.report-reference,.references {font-size:11pt;line-height:1.6;color:#1a1a1a;}
.toc {font-size:12pt;line-height:1.6;background:transparent;}
.report-callout,.report-callout--result,.report-callout--risk {color:#1a1a1a;}
@media print {
 body {font-size:12pt;line-height:1.8;color:#1a1a1a;}
 h1 {font-size:26pt;} h2 {font-size:19pt;} h3 {font-size:16pt;} h4 {font-size:14pt;}
 table {font-size:10.5pt;line-height:1.45;}
}
"""


def apply_report_typography(document) -> None:
    """Format existing OOXML without rebuilding text, fields or relationships.

Includes runs inside hyperlinks, which python-docx Paragraph.runs omits.
Existing numbering, bookmarks, citations, geometry and emphasis are retained.
"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    from docx.text.paragraph import Paragraph

    family = report_font_family()

    def fonts(properties, size, *, color="1A1A1A", bold=None):
        node = properties.get_or_add_rFonts()
        for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
            node.set(qn(f"w:{attr}"), family)
        for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "csTheme"):
            node.attrib.pop(qn(f"w:{attr}"), None)
        for tag in ("sz", "szCs"):
            value = properties.find(qn(f"w:{tag}"))
            if value is None:
                value = OxmlElement(f"w:{tag}"); properties.append(value)
            value.set(qn("w:val"), str(round(size * 2)))
        value = properties.get_or_add_color()
        value.set(qn("w:val"), color)
        for attr in ("themeColor", "themeTint", "themeShade"):
            value.attrib.pop(qn(f"w:{attr}"), None)
        if bold is not None:
            properties.get_or_add_b().val = bold

    for style in document.styles:
        # Set fonts on all styles, including TOC styles used by Writer refresh.
        fonts(style.element.get_or_add_rPr(), 12)
        if style.type != 1:
            continue
        style.paragraph_format.line_spacing = Pt(21.6)
        name = style.name
        size = 12
        if name == "Title": size = 26
        elif name == "Subtitle": size = 16
        elif name == "TOC Heading": size = 21
        elif name.startswith("TOC "):
            size = 12 if name == "TOC 1" else 11
            style.paragraph_format.line_spacing = Pt(size * 1.6)
        elif name.startswith("Heading "):
            level = int(name.split()[-1])
            size = {1: 19, 2: 16, 3: 14}.get(level, 12)
            style.font.bold = True
        fonts(style.element.get_or_add_rPr(), size)
        for border in style.element.xpath("./w:pPr/w:pBdr/*"):
            border.set(qn("w:color"), "D9D9D9")
            border.set(qn("w:sz"), "6")
            border.attrib.pop(qn("w:themeColor"), None)
        if name in {"Title", "Subtitle", "TOC Heading"} or name.startswith("Heading "):
            style.paragraph_format.line_spacing = Pt(size * 1.45)
    if "Hyperlink" in document.styles:
        document.styles["Hyperlink"].font.color.rgb = RGBColor.from_string("2C6FBB")

    references = False
    for node in document.element.body.xpath(".//w:p"):
        paragraph = Paragraph(node, document._body)
        text = "".join(node.xpath(".//w:t/text()"))
        if not text.strip():
            continue  # Preserve picture dimensions, blank cover space and breaks.
        fmt = paragraph.paragraph_format
        style_name = paragraph.style.name if paragraph.style else "Normal"
        table = bool(node.xpath("ancestor::w:tc"))
        outline = node.xpath("./w:pPr/w:outlineLvl/@w:val")
        match = re.fullmatch(r"Heading (\d+)", style_name)
        level = int(outline[0]) + 1 if outline else int(match[1]) if match else 1 if style_name == "Report Chapter" else 0
        if level and not table:
            references = "参考文献" in text if level == 1 else references
        is_toc = style_name.startswith("TOC ") and style_name != "TOC Heading"
        max_size = max([int(x) / 2 for x in node.xpath(".//w:rPr/w:sz/@w:val")] or [0])
        title = style_name == "Title" or (not table and max_size >= 24 and paragraph.alignment == 1)
        metadata = bool(re.match(r"^(生成(?:日期|时间|方式)|数据源|样本量|报告版本|模板版本|材料类型|研究类型|文献截止日期)[：:]", text))
        caption = style_name == "Caption" or bool(re.match(r"^(?:图|表)\s*\d+[.、\s]", text))
        source = text.startswith(("来源：", "资料来源：", "数据来源："))
        size, leading, color, bold = 12, 1.8, "1A1A1A", None
        if table: size, leading = 10.5, 1.45
        elif title: size, leading, bold = 26, 1.4, True
        elif style_name == "TOC Heading": size, leading, bold = 21, 1.4, True
        elif is_toc: size, leading = (12 if style_name == "TOC 1" else 11), 1.6
        elif level: size, leading, bold = {1: 19, 2: 16, 3: 14}.get(level, 12), 1.45, True
        elif style_name == "Subtitle" or (max_size >= 16 and paragraph.alignment == 1): size, leading = 16, 1.45
        elif metadata or source: size, leading, color = 10.5, 1.6, "666666"
        elif caption: size, leading = 10.5, 1.5
        elif references: size, leading = 11, 1.6
        # Writer's multiple spacing uses the font's full ascent/descent, which
        # makes CJK serif leading substantially larger than CSS line-height.
        # Use points so the body baseline matches the reference's 21.6 pt.
        fmt.line_spacing = Pt(round(size * leading, 2))
        fmt.widow_control = True
        if table:
            fmt.first_line_indent = Pt(0)
            fmt.space_before = fmt.space_after = Pt(3)
        elif level:
            fmt.space_before, fmt.space_after = Pt(16), Pt(8)
            fmt.keep_with_next = True
        elif fmt.first_line_indent and fmt.first_line_indent > 0:
            fmt.first_line_indent = Pt(24)
        for run in node.xpath(".//w:r"):
            props = run.get_or_add_rPr()
            linked = bool(run.xpath("ancestor::w:hyperlink"))
            fonts(props, size, color="2C6FBB" if linked else color, bold=bold)
        for border in node.xpath("./w:pPr/w:pBdr/*"):
            border.set(qn("w:color"), "D9D9D9")
            border.set(qn("w:sz"), "6")
            border.attrib.pop(qn("w:themeColor"), None)

    for section in document.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            for node in part._element.xpath(".//w:r"):
                fonts(node.get_or_add_rPr(), 9, color="666666")
