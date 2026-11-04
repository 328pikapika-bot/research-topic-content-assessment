"""Shared, data-only static figures for the topic and assessment reports."""
from __future__ import annotations

from app.services.report_typography import (report_font_path)

import io
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


COLORS = ("#5269c7", "#228579", "#b77928", "#9060ac", "#b75465", "#437f9d")
INK = "#26344b"
MUTED = "#65758b"


def _font(size: int, bold: bool = False):
    configured = os.getenv("REPORT_CJK_BOLD_FONT" if bold else "REPORT_CJK_FONT", "")
    paths = [report_font_path(bold), configured, r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
    for path in paths:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("报告图缺少中文字体，无法安全生成图像")


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True, dpi=(180, 180))
    return output.getvalue()


def _short(value: Any, limit: int = 19) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _fit(draw, value: Any, font, width: int) -> str:
    value = " ".join(str(value or "").split())
    if draw.textlength(value, font=font) <= width:
        return value
    while value and draw.textlength(value + "…", font=font) > width:
        value = value[:-1]
    return value + "…"


def _card(draw, box, label, subtitle="", color=COLORS[0], *, pale=False):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=16, fill="#f5f7fc" if pale else "#ffffff", outline=color, width=3)
    title_font = _font(27, True)
    draw.text((x0 + 17, y0 + 9), _fit(draw, label, title_font, x1-x0-34), fill=INK, font=title_font)
    if subtitle:
        sub_font = _font(20)
        draw.text((x0 + 17, y1 - 31), _fit(draw, subtitle, sub_font, x1-x0-34), fill=MUTED, font=sub_font)


def _wrapped_lines(draw, value, font, width):
    text = " ".join(str(value or "").split())
    lines = []
    current = ""
    for char in text:
        if not current or draw.textlength(current + char, font=font) <= width:
            current += char
        else:
            lines.append(current); current = char
    if current:
        lines.append(current)
    return lines or [""]


def _card_full_title(draw, box, label, subtitle="", color=COLORS[0], *, pale=False):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=12, fill="#f5f7fc" if pale else "#ffffff", outline=color, width=3)
    font = _font(22, True)
    lines = _wrapped_lines(draw, label, font, x1 - x0 - 32)
    line_height = 29
    start_y = y0 + max(8, (y1 - y0 - len(lines) * line_height - (22 if subtitle else 0)) / 2)
    for line in lines:
        draw.text((x0 + 16, start_y), line, fill=INK, font=font)
        start_y += line_height
    if subtitle:
        draw.text((x0 + 16, y1 - 12), subtitle, fill=MUTED, font=_font(16), anchor="ls")


def _edge(draw, start, end, color, dashed=False, width=5):
    x0, y0 = start; x1, y1 = end
    if abs(y1 - y0) < 2:
        points = [(x0 + (x1 - x0) * t / 50, y0) for t in range(51)]
    else:
        mid = (x0 + x1) / 2
        points = []
        for i in range(51):
            t = i / 50; u = 1 - t
            points.append((u**3*x0 + 3*u*u*t*mid + 3*u*t*t*mid + t**3*x1,
                           u**3*y0 + 3*u*u*t*y0 + 3*u*t*t*y1 + t**3*y1))
    if dashed:
        for i in range(0, 50, 7):
            draw.line(points[i:min(i + 4, 50) + 1], fill=color, width=width, joint="curve")
    else:
        draw.line(points, fill=color, width=width, joint="curve")


def _participants(row):
    values = row.get("participating_disciplines") or [
        {"discipline": row.get("core"), "role": "core"},
        {"discipline": row.get("expansion"), "role": "expansion"}]
    seen = set(); result = []
    for item in values:
        name = str(item.get("discipline") or "").strip()
        if name and name != "学科待核验" and name not in seen:
            seen.add(name); result.append(item)
    return result


def topic_opportunity_parts(rows: list[dict[str, Any]], *, concise: bool = False) -> list[bytes]:
    """Render up to three grounded opportunities with readable, complete labels."""
    original_rows = rows
    rows = [row for row in rows if str(row.get("opportunity") or "").strip()]
    if concise:
        rows = rows[:3]
    if not rows and original_rows:
        image = Image.new("RGB", (1900, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.text((950, 150), "学科与机会网络", fill=INK, font=_font(32, True), anchor="mm")
        draw.text((950, 280), "当前检索材料尚未形成可直接表述的研究机会。", fill=MUTED, font=_font(25), anchor="mm")
        return [_png(image)]
    result = []
    for offset in range(0, len(rows), 3):
        group = rows[offset:offset + 3]
        if not group:
            continue
        core = sorted({p["discipline"] for r in group for p in _participants(r) if p.get("role") == "core"})
        expansion = sorted({p["discipline"] for r in group for p in _participants(r) if p.get("role") != "core"})
        title_font = _font(22, True)
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
        row_heights = []
        for row in group:
            lines = _wrapped_lines(measure, row.get("opportunity"), title_font, 678)
            row_heights.append(max(112, 42 + len(lines) * 29))
        row_gap = 28
        total_rows = sum(row_heights) + row_gap * (len(group) - 1)
        height = max(500, 180 + total_rows)
        image = Image.new("RGB", (1900, height), "white"); draw = ImageDraw.Draw(image)
        draw.text((70, 38), "核心学科", fill=INK, font=_font(30, True))
        draw.text((730, 38), "研究机会", fill=INK, font=_font(30, True))
        draw.text((1430, 38), "拓展学科", fill=INK, font=_font(30, True))
        positions = {}
        for names, x, kind, width in ((core, 55, "core", 410), (expansion, 1430, "expansion", 410)):
            total = len(names) * 94
            for i, name in enumerate(names):
                y = 120 + (height - 170 - total) / 2 + i * 94
                positions[(kind, name)] = (x, y, x + width, y + 76)
        opportunities = {}
        current_y = 120 + (height - 170 - total_rows) / 2
        for row, row_height in zip(group, row_heights):
            opportunities[row["crosslink_id"]] = (570, current_y, 1280, current_y + row_height)
            current_y += row_height + row_gap
        for row in group:
            number = int(row.get("number") or offset + group.index(row) + 1)
            color = row.get("color") or COLORS[(number - 1) % len(COLORS)]
            opportunity = opportunities[row["crosslink_id"]]
            for p in _participants(row):
                box = positions.get(("core" if p.get("role") == "core" else "expansion", p["discipline"]))
                if not box:
                    continue
                start = (box[2], (box[1] + box[3]) / 2) if p.get("role") == "core" else (opportunity[2], (opportunity[1] + opportunity[3]) / 2)
                end = (opportunity[0], (opportunity[1] + opportunity[3]) / 2) if p.get("role") == "core" else (box[0], (box[1] + box[3]) / 2)
                _edge(draw, start, end, color, dashed=False, width=5)
        for (kind, name), box in positions.items():
            _card_full_title(draw, box, name, "核心学科" if kind == "core" else "拓展学科", COLORS[0], pale=True)
        for row in group:
            number = int(row.get("number") or offset + group.index(row) + 1)
            color = row.get("color") or COLORS[(number - 1) % len(COLORS)]
            _card_full_title(draw, opportunities[row["crosslink_id"]], row.get("opportunity"), row.get("method") or "研究方法", color)
        result.append(_png(image))
    return result


def topic_tree_parts(data: dict[str, Any]) -> list[bytes]:
    nodes = data.get("nodes") or []
    if not nodes: return []
    by_id = {str(n.get("theme_id")): n for n in nodes}
    children = {key: [] for key in by_id}; roots = []; pending = []
    for node in nodes:
        nid = str(node.get("theme_id")); parent = str(node.get("parent_id") or "")
        seen = {nid}; cursor = parent; invalid = False
        while cursor and cursor != "root":
            if cursor in seen or cursor not in by_id:
                invalid = True; break
            seen.add(cursor); cursor = str(by_id[cursor].get("parent_id") or "")
        if invalid: pending.append(node)
        elif parent in children: children[parent].append(node)
        else: roots.append(node)
    groups = roots + pending
    result = []
    for branch in groups:
        ordered = []
        def visit(node, depth):
            ordered.append((node, depth))
            for child in children.get(str(node.get("theme_id")), []): visit(child, depth + 1)
        visit(branch, 1)
        chunks = [ordered[i:i + 8] for i in range(0, len(ordered), 8)]
        for chunk in chunks:
            included = {str(node.get("theme_id")) for node, _ in chunk}
            context = []
            for node, _ in chunk:
                ancestor = str(node.get("parent_id") or "")
                lineage = []
                while ancestor in by_id and ancestor not in included:
                    parent = by_id[ancestor]
                    lineage.append(parent)
                    ancestor = str(parent.get("parent_id") or "")
                for parent in reversed(lineage):
                    parent_id = str(parent.get("theme_id"))
                    if parent_id not in included:
                        context.append((parent, next(d for n, d in ordered if n is parent)))
                        included.add(parent_id)
            display = [*context, *chunk]
            max_depth = max(depth for _, depth in display)
            width = max(850, 430 + max_depth * 225 + 60)
            step = min(245, (width - 480) // max_depth)
            title_font = _font(22, True)
            measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
            row_gap = 34
            row_heights = []
            for node, _ in display:
                label = node.get("display_label") or node.get("label") or ""
                available = max(160, step - 54)
                lines = _wrapped_lines(measure, label, title_font, available)
                row_heights.append(max(108, 54 + len(lines) * 29))
            content_height = sum(row_heights) + row_gap * max(0, len(display) - 1)
            # Keep the established minimum canvas while adding room whenever
            # wrapped labels make a node taller than its compact baseline.
            height = max(480, 480 + sum(max(0, row_height - 108) for row_height in row_heights))
            image = Image.new("RGB", (width, height), "white"); draw = ImageDraw.Draw(image)
            root_label = data.get("display_root") or data.get("root") or "研究总方向"
            root_box = (55, 55, 350, 140)
            boxes = {}
            row_y = 160 + max(0, height - 190 - content_height) / 2
            for i, (node, depth) in enumerate(display):
                x = 430 + (depth - 1) * step
                boxes[str(node.get("theme_id"))] = (x, row_y, x + max(230, step - 20), row_y + row_heights[i])
                row_y += row_heights[i] + row_gap
            for node, depth in display:
                nid = str(node.get("theme_id")); parent = str(node.get("parent_id") or "")
                child_box = boxes[nid]; parent_box = boxes.get(parent, root_box)
                if node is branch and branch in pending:
                    continue
                if parent in boxes or (node is branch and branch not in pending):
                    _edge(draw, (parent_box[2], (parent_box[1]+parent_box[3])/2), (child_box[0], (child_box[1]+child_box[3])/2), COLORS[min(depth-1, 5)])
            _card(draw, root_box, root_label, "研究总方向", COLORS[0], pale=True)
            for node, depth in display:
                nid = str(node.get("theme_id")); box = boxes[nid]
                subtitle = "待确认层级" if node is branch and branch in pending else "主题 · 第%s层" % depth
                _card_full_title(draw, box, node.get("display_label") or node.get("label") or nid, subtitle, COLORS[min(depth-1, 5)])
                keywords = [str(k) for k in node.get("seed_terms") or node.get("keywords") or [] if str(k).strip()]
                if keywords:
                    leaf = (box[0] + 5, box[3] + 15, box[2], box[3] + 52)
                    draw.line((box[0] + 45, box[3], box[0] + 45, leaf[1]), fill=COLORS[min(depth-1, 5)], width=3)
                    draw.rounded_rectangle(leaf, radius=13, fill="#f4f6fb", outline="#d5ddec", width=2)
                    keyword_font = _font(20)
                    draw.text((leaf[0] + 8, leaf[1] + 5), _fit(draw, "关键词：" + "、".join(keywords), keyword_font, leaf[2]-leaf[0]-16), fill=MUTED, font=keyword_font)
            draw.text((55, height - 45), "层级仅表示已记录的归属；无效父节点或循环关系列为待确认。", fill=MUTED, font=_font(22))
            result.append(_png(image))
    return result


def assessment_evidence_parts(chart: dict[str, Any]) -> list[bytes]:
    points = chart.get("points") or []; evidence = {e["id"]: e for e in chart.get("evidence") or []}
    links = chart.get("links") or []
    result = []
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    for offset in range(0, len(points), 2):
        group = points[offset:offset+2]; point_ids = {p["id"] for p in group}
        group_links = [l for l in links if l.get("source") in point_ids and l.get("target") in evidence]
        eids = list(dict.fromkeys(l["target"] for l in group_links))
        evidence_rows = [(eid, evidence[eid]) for eid in eids]
        title_font = _font(22, True)
        row_heights = []
        for _eid, item in evidence_rows:
            title = " ".join(str(item.get("label") or "").split())
            width = 950
            lines = 1
            current = ""
            for char in title:
                if not current or measure.textlength(current + char, font=title_font) <= width:
                    current += char
                else:
                    lines += 1
                    current = char
            row_heights.append(max(88, lines * 29 + 36))
        list_height = sum(row_heights) + max(0, len(eids) - 1) * 14
        height = max(520, 150 + max(len(group) * 125, list_height) + 90)
        image = Image.new("RGB", (2050, height), "white"); draw = ImageDraw.Draw(image)
        draw.text((60, 35), "评估要点", fill=INK, font=_font(30, True))
        draw.text((910, 35), "纳入比对的文献", fill=INK, font=_font(30, True))
        pboxes = {}; eboxes = {}
        for i, p in enumerate(group):
            y = 100 + (height - 220 - len(group)*125)/2 + i*125
            pboxes[p["id"]] = (65, y, 520, y+100)
        y = 100 + max(0, (height - 220 - list_height) / 2)
        for (eid, _item), row_height in zip(evidence_rows, row_heights):
            eboxes[eid] = (900, y, 1985, y + row_height)
            y += row_height + 14
        for link in group_links:
            pbox = pboxes[link["source"]]; ebox = eboxes[link["target"]]
            point = next(p for p in group if p["id"] == link["source"])
            color = point.get("color") or COLORS[(int(point.get("number") or offset+1)-1)%len(COLORS)]
            _edge(draw, (pbox[2], (pbox[1]+pbox[3])/2), (ebox[0], (ebox[1]+ebox[3])/2), color, width=4)
        for p in group:
            color = p.get("color") or COLORS[(int(p.get("number") or offset+1)-1)%len(COLORS)]
            _card_full_title(draw, pboxes[p["id"]], p.get("label"), f"要点 {p.get('number') or offset+1}", color)
        for eid, e in evidence_rows:
            _card_full_title(draw, eboxes[eid], e.get("label"), f"文献 [{e.get('number')}]" if e.get("number") else "纳入比对", COLORS[0], pale=True)
        draw.text((65, height-55), "连线只表示存在比对依据；具体共同点、差异和反证见逐项分析。", fill=MUTED, font=_font(23))
        result.append(_png(image))
    return result
