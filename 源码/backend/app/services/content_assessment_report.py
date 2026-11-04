"""Word delivery report built directly from a content-assessment task state."""

from __future__ import annotations

from app.services.report_typography import (apply_report_typography, report_font_family, report_font_path)

import io
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.agents.content_assessment.schemas import (
    Claim, ClaimAssessmentDetail, ClaimEvidenceLink, ContentAssessmentState,
    EvidenceRecord, Indicator, IndicatorValidation, LandscapeDataCard,
    NoveltyDecision, QueryPlan, ScoreCardItem,
)
from app.core.config import BEIJING_TZ


REPORT_WIDTH_DXA = 9638
NAVY = "1E3A8A"
BLUE = "2563EB"
INK = "333333"
MUTED = "64748B"


def _render_citations(text: str, numbers: dict[str, int]) -> str:
    if not numbers:
        return text
    pattern = "|".join(re.escape(key) for key in sorted(numbers, key=len, reverse=True))
    return re.sub(r"(?<![\w-])(?:" + pattern + r")(?![\w-])", lambda match: f"[{numbers[match[0]]}]", text)


def _safe_filename(value: str) -> str:
    return ("".join("_" if char in r'\\/:*?\"<>|' else char for char in value).strip()[:100] or "内容评估报告")


def _display_title(state: ContentAssessmentState) -> str:
    from app.agents.content_assessment.naming import report_title
    return report_title(state)


def _data(state: ContentAssessmentState) -> dict[str, Any]:
    snapshot = state.report_snapshot
    if snapshot is not None:
        from app.agents.content_assessment.report_composition import state_from_snapshot
        data = _data(state_from_snapshot(snapshot))
        data["title"] = _display_title(state)
        data["generated_at"] = snapshot.cover_metadata.get("generated_at") or snapshot.generated_at
        data["stage_trace"] = snapshot.stage_trace
        data["process_trace"] = snapshot.process_trace
        data["template_version"] = snapshot.template_version
        data["report_source_selection"] = snapshot.audit_data.get("report_source_selection", {})
        data["citation_numbers"] = dict(snapshot.citation_numbers)
        if snapshot.template_version in {"ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"}:
            cited = set(snapshot.citation_numbers)
            data["evidence"] = [e for e in data["evidence"] if e.evidence_id in cited]
            data["evidence_by_id"] = {e.evidence_id: e for e in data["evidence"]}
            data["links"] = [link for link in data["links"] if link.evidence_id in cited]
        return data
    audit = snapshot.audit_data if snapshot else {}
    def frozen(key: str, model: Any, fallback: Any) -> list[Any]:
        values = audit.get(key)
        return [model.model_validate(item) for item in values] if values is not None else list(fallback)
    evidence = frozen("evidence_records", EvidenceRecord, state.evidence_records)
    chart_snapshot = snapshot.chart_data_snapshot if snapshot else {}
    market_cards = [LandscapeDataCard.model_validate(item) for item in chart_snapshot.get("market_data_cards", [])] if "market_data_cards" in chart_snapshot else list(state.market_data_cards)
    regulatory_cards = [LandscapeDataCard.model_validate(item) for item in chart_snapshot.get("regulatory_data_cards", [])] if "regulatory_data_cards" in chart_snapshot else list(state.regulatory_data_cards)
    competitive_cards = [LandscapeDataCard.model_validate(item) for item in chart_snapshot.get("competitive_data_cards", [])] if "competitive_data_cards" in chart_snapshot else list(state.competitive_data_cards)
    scorecard = [ScoreCardItem.model_validate(item) for item in chart_snapshot.get("scorecard", [])] if "scorecard" in chart_snapshot else list(state.scorecard)
    return {
        "title": _display_title(state),
        "generated_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M"),
        "profile": state.assessment_profile,
        "boundary": state.boundary_matrix,
        "nine_fields": state.nine_fields,
        "work_packages": list(state.work_packages),
        "claims": frozen("claims", Claim, state.claims),
        "report_source_selection": state.output_json.get("report_source_selection", {}),
        "citation_numbers": {},
        "indicators": frozen("indicators", Indicator, state.indicators),
        "indicator_validations": frozen("indicator_validations", IndicatorValidation, state.indicator_validations),
        "guidelines": list(state.guideline_requirements),
        "queries": frozen("query_plans", QueryPlan, state.query_plans),
        "evidence": evidence,
        "evidence_by_id": {item.evidence_id: item for item in evidence},
        "links": frozen("claim_evidence_links", ClaimEvidenceLink, state.claim_evidence_links),
        "claim_assessments": frozen("claim_assessments", ClaimAssessmentDetail, state.claim_assessments),
        "evidence_chunks": list(state.evidence_chunks),
        "decisions": frozen("novelty_decisions", NoveltyDecision, state.novelty_decisions),
        "scorecard": scorecard,
        "trend": chart_snapshot.get("trend_analysis", state.trend_analysis),
        "market_cards": market_cards,
        "regulatory_cards": regulatory_cards,
        "competitive_cards": competitive_cards,
        "quality_gate": state.quality_gate,
        "plan": state.report_plan,
        "versions": state.versions,
        "stage_trace": list(snapshot.stage_trace) if snapshot and snapshot.stage_trace else list(state.stage_trace),
        "process_trace": snapshot.process_trace if snapshot and snapshot.process_trace else state.process_trace,
        "search_trace": state.output_json.get("search_trace") or [],
        "metrics": state.report_metrics,
    }


def _citation_map(evidence: Iterable[Any]) -> dict[str, int]:
    return {item.evidence_id: index for index, item in enumerate(evidence, start=1)}


def _refs(ids: Iterable[str], citation_map: dict[str, int]) -> str:
    values = list(dict.fromkeys(str(citation_map[item]) for item in ids if item in citation_map))
    return " ".join(f"[{value}]" for value in values) if values else "[无可定位证据]"


def _value(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if str(item).strip()) or "待确认"
    return str(value or "待确认")


def _score(item: ScoreCardItem, data: dict[str, Any]) -> float | None:
    """Read only an explicit score or an explicitly declared range midpoint."""
    range_match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[-~至–—]\s*(\d+(?:\.\d+)?)\s*(?:/\s*5|分)?\s*", item.score_range or "")
    if range_match:
        lower, upper = map(float, range_match.groups())
        return round((lower + upper) / 2, 2) if 0 <= lower <= upper <= 5 else None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:/\s*5|分)?\s*", item.score_range or "")
    if match:
        value = float(match.group(1))
        return value if math.isfinite(value) and 0 <= value <= 5 else None
    return None


def _pillow() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow 未安装，无法生成 Word 报告图表。") from exc
    return Image, ImageDraw, ImageFont


def _font(image_font: Any, size: int, bold: bool = False) -> Any:
    configured = os.getenv("REPORT_CJK_BOLD_FONT" if bold else "REPORT_CJK_FONT", "").strip()
    candidates = ([Path(report_font_path(bold))] if report_font_path(bold) else []) + ([Path(configured)] if configured else []) + [
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                font = image_font.truetype(str(candidate), size=round(size * 1.7), index=0)
                try:
                    axes = font.get_variation_axes()
                    coordinates = [min(axis["maximum"], max(axis["minimum"], 700 if bold else 400))
                                   if axis["name"] in (b"Weight", "Weight") else axis["default"] for axis in axes]
                    font.set_variation_by_axes(coordinates)
                except (AttributeError, OSError, ValueError):
                    pass  # Static CJK fonts have no variation axes.
                return font
            except OSError:
                pass
    raise RuntimeError("未找到可用的 Noto CJK 或 Windows 中文字体，已停止生成图表，避免输出乱码。")


def _wrap(draw: Any, text: str, font: Any, width: int, max_lines: int = 4) -> list[str]:
    text = " ".join(str(text or "").split()) or "未提供"
    lines: list[str] = []
    current = ""
    for char in text:
        if not current or draw.textlength(current + char, font=font) <= width:
            current += char
        else:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _draw_lines(draw: Any, x: float, y: float, lines: list[str], font: Any, color: str, anchor: str = "la", gap: int = 5) -> None:
    for line in lines:
        draw.text((x, y), line, font=font, fill=color, anchor=anchor)
        box = draw.textbbox((0, 0), line, font=font)
        y += box[3] - box[1] + gap


def _canvas(title: str = "", subtitle: str = "") -> tuple[Any, Any, Any]:
    """Create a clean plotting surface.

    Figure names and provenance belong to the document caption, not inside
    the raster. Keeping the parameters preserves compatibility with existing
    chart builders while making the image itself reusable in other views.
    """
    Image, ImageDraw, ImageFont = _pillow()
    image = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(image)
    return image, draw, ImageFont


def _footer(draw: Any, image_font: Any, note: str) -> None:
    # Source notes are rendered as DOCX paragraphs below the image.
    return None


def _workflow_chart(data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas("图 1  创新性评估八阶段工作流与本次执行概况", "本图记录任务过程和规模，不替代主张级证据结论")
    profile = data["profile"]
    steps = [
        (("document_intake",), "文档上传与解析", f"{len(profile.document_records)} 份材料"),
        (("claim_extraction",), "要点提取", f"{len(data['claims'])} 项主张"),
        (("retrieval", "comparison"), "内容比对与溯源", f"{len(data['evidence'])} 条证据 / {len(data['links'])} 条关系"),
        (("assessment",), "创新性评估", f"{len(data['decisions'])} 项判定"),
        (("landscape",), "技术趋势与竞争", f"{len(data['trend'].get('themes', []))} 个主题"),
    ]
    traces = {item["stage_key"] if isinstance(item, dict) else item.stage_key: item for item in data["stage_trace"]}
    labels = {"completed": "已完成", "running": "执行中", "pending": "待执行", "failed": "失败", "skipped": "已跳过"}
    for index, (keys, title, detail) in enumerate(steps):
        states = [trace.get("status", "") if isinstance(trace, dict) else getattr(trace, "status", "") for trace in (traces.get(key) for key in keys)]
        status = "completed" if all(value == "completed" for value in states) else "failed" if "failed" in states else "running" if "running" in states else "pending" if "pending" in states else ""
        fill = "#E9F7F5" if status == "completed" else "#EEF5FF" if status == "running" else "#F1F5F9"
        column = index
        x, y, width, height = 65 + column * 300, 265, 270, 300
        draw.rounded_rectangle((x, y, x + width, y + height), radius=12, fill=fill, outline=f"#{BLUE}", width=3)
        draw.ellipse((x + 18, y + 18, x + 68, y + 68), fill=f"#{NAVY}")
        draw.text((x + 43, y + 43), str(index + 1), font=_font(image_font, 21, True), fill="white", anchor="mm")
        _draw_lines(draw, x + 25, y + 90, _wrap(draw, title, _font(image_font, 18, True), width - 50, 2), _font(image_font, 18, True), f"#{INK}")
        detail_font = _font(image_font, 16)
        _draw_lines(draw, x + 28, y + 185, _wrap(draw, detail, detail_font, width - 56, 2), detail_font, f"#{MUTED}", gap=7)
        draw.text((x + 28, y + 270), labels.get(status, "用时与状态见执行记录"), font=_font(image_font, 12), fill="#0D9488" if status == "completed" else f"#{MUTED}", anchor="lm")
        if index < len(steps) - 1:
            draw.line((x + width + 6, y + height / 2, x + width + 28, y + height / 2), fill=f"#{BLUE}", width=4)
            draw.polygon([(x + width + 28, y + height / 2), (x + width + 16, y + height / 2 - 8), (x + width + 16, y + height / 2 + 8)], fill=f"#{BLUE}")
    image.save(output, optimize=True, dpi=(180, 180))


def _year_theme_series(data: dict[str, Any]) -> dict[str, list[tuple[int, int]]]:
    if data.get("template_version") in {"ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"}:
        # V5 _data limits this pool to sources actually cited in the report.
        # Old retrieval themes do not describe the reviewed report's scope.
        years = Counter(item.year for item in data["evidence"]
                        if isinstance(item.year, int) and item.year > 0)
        return {"报告引用文献": sorted(years.items())} if len(years) >= 2 else {}
    grouped: dict[str, Counter] = defaultdict(Counter)
    evidence = data["evidence_by_id"]
    for theme in data["trend"].get("themes", []):
        if not isinstance(theme, dict) or not theme.get("label"):
            continue
        for eid in dict.fromkeys(theme.get("evidence_ids") or []):
            item = evidence.get(eid)
            if item and isinstance(item.year, int) and item.year > 0:
                grouped[str(theme["label"])][item.year] += 1
    if grouped:
        return {theme: sorted(counts.items()) for theme, counts in grouped.items() if len(counts) >= 2}
    # Query groups can describe search roles rather than technical themes.
    themes = {query.query_group for query in data["queries"] if query.query_group and query.query_group != getattr(query, "query_role", "")}
    for item in data["evidence"]:
        theme = str(getattr(item, "query_group", "") or "").strip()
        year = getattr(item, "year", None)
        if theme in themes and isinstance(year, int) and year > 0:
            grouped[theme][year] += 1
    return {theme: sorted(counts.items()) for theme, counts in grouped.items() if len(counts) >= 2}


def _years_chart(data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas()
    series = _year_theme_series(data)
    if not series:
        return
    years = sorted({year for records in series.values() for year, _ in records})
    maximum = max(count for records in series.values() for _, count in records) or 1
    left, top, right, bottom = 165, 150, 1450, 700
    for value in sorted(set(round(maximum * tick / 5) for tick in range(6))):
        y = bottom - value / maximum * (bottom - top)
        draw.line((left, y, right, y), fill="#DCE5EE", width=1)
        draw.text((left - 15, y), str(value), font=_font(image_font, 14), fill=f"#{MUTED}", anchor="rm")
    colors = [f"#{NAVY}", "#0D9488", "#64748B", "#7C93B3", "#334155"]
    for series_index, (theme, records) in enumerate(series.items()):
        values = dict(records)
        points = []
        for index, year in enumerate(years):
            x = left + (right - left) * index / max(1, len(years) - 1)
            y = bottom - values.get(year, 0) / maximum * (bottom - top)
            points.append((x, y))
        color = colors[series_index % len(colors)]
        draw.line(points, fill=color, width=4, joint="curve")
        for x, y in points:
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color, outline="white", width=2)
        legend_x, legend_y = left + (series_index % 3) * 440, 760 + (series_index // 3) * 38
        draw.line((legend_x, legend_y, legend_x + 32, legend_y), fill=color, width=4)
        draw.text((legend_x + 44, legend_y), theme[:16], font=_font(image_font, 11), fill=f"#{INK}", anchor="lm")
    for index, year in enumerate(years):
        if index % max(1, math.ceil(len(years) / 10)) and index != len(years) - 1:
            continue
        x = left + (right - left) * index / max(1, len(years) - 1)
        draw.text((x, bottom + 30), str(year), font=_font(image_font, 13), fill=f"#{MUTED}", anchor="ma")
    image.save(output, optimize=True, dpi=(180, 180))


def _annual_literature_counts(data: dict[str, Any]) -> dict[str, Any]:
    counts = Counter()
    undated = 0
    for item in data.get("evidence", []):
        if item.source_type != "literature":
            continue
        if isinstance(item.year, int) and item.year > 0:
            counts[item.year] += 1
        else:
            undated += 1
    return {"points": [{"year": year, "count": counts[year]} for year in sorted(counts)],
            "undated": undated, "total": sum(counts.values()) + undated}


def _annual_literature_chart(chart_data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas()
    points = list(chart_data.get("points") or [])
    if chart_data.get("undated"):
        points.append({"year": "未注明年份", "count": chart_data["undated"]})
    if not points:
        return
    row_height = min(78, 610 / len(points))
    maximum = max(int(item.get("count", 0)) for item in points) or 1
    for index, item in enumerate(points):
        y = 130 + index * row_height
        label = str(item["year"])
        count = int(item.get("count", 0))
        draw.text((335, y + row_height / 2), label, font=_font(image_font, 20), fill=f"#{INK}", anchor="rm")
        draw.rounded_rectangle((365, y + 8, 1370, y + row_height - 8), radius=8, fill="#E8EEF5")
        if count:
            draw.rounded_rectangle((365, y + 8, 365 + 1005 * count / maximum, y + row_height - 8),
                                   radius=8, fill="#228579")
        draw.text((1400, y + row_height / 2), str(count), font=_font(image_font, 20, True),
                  fill=f"#{INK}", anchor="lm")
    draw.text((365, 805), f"统计范围：报告引用的 {chart_data.get('total', 0)} 篇文献",
              font=_font(image_font, 15), fill=f"#{MUTED}")
    image.save(output, optimize=True, dpi=(180, 180))


_PUBLIC_JUDGEMENT_LABELS = {
    "有直接支持": "存在直接先例",
    "存在相近先例": "存在相近研究",
    "方向性支持": "相关方法或方向",
    "暂未发现可比证据": "本次纳入来源未形成直接对应",
    "当前不可判断": "依据范围内暂不作判断",
}


def _claim_judgement_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    from app.agents.content_assessment.scholarly_report import point_title
    claims = {item.claim_id: item for item in data.get("claims", [])}
    citations = data.get("citation_numbers", {})
    rows = []
    for decision in data.get("decisions", []):
        claim = claims.get(decision.claim_id)
        if not claim:
            continue
        references = list(dict.fromkeys(citations[eid] for eid in decision.evidence_ids if eid in citations))
        if not references:
            continue
        rows.append({"point": point_title(claim),
                     "judgement": _PUBLIC_JUDGEMENT_LABELS.get(decision.label, "研究比较"),
                     "references": references})
    return rows


def _claim_judgement_chart(chart_data: list[dict[str, Any]], output: Path) -> None:
    image, draw, image_font = _canvas()
    if not chart_data:
        return
    row_height = min(125, 660 / len(chart_data))
    for index, item in enumerate(chart_data):
        top = 100 + index * row_height
        point_lines = _wrap(draw, item["point"], _font(image_font, 18, True), 680, 3)
        _draw_lines(draw, 70, top + 10, point_lines, _font(image_font, 18, True), f"#{INK}", gap=2)
        label_lines = _wrap(draw, item["judgement"], _font(image_font, 17), 470, 2)
        _draw_lines(draw, 790, top + 10, label_lines, _font(image_font, 17), "#228579", gap=2)
        refs = "依据文献 " + "、".join(f"[{value}]" for value in item["references"])
        draw.text((1320, top + 28), refs, font=_font(image_font, 14), fill=f"#{MUTED}", anchor="rm")
        if index < len(chart_data) - 1:
            draw.line((70, top + row_height - 3, 1510, top + row_height - 3), fill="#DCE5EE", width=2)
    image.save(output, optimize=True, dpi=(180, 180))


def _regulatory_timeline_data(data: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_ids = {item.evidence_id for item in data.get("evidence", [])}
    rows = []
    for card in data.get("regulatory_cards", []):
        if card.status != "available" or not card.source_evidence_ids:
            continue
        match = re.search(r"(?:19|20)\d{2}(?:[-年/.]\d{1,2}(?:[-月/.]\d{1,2}日?)?)?", card.period)
        sources = [eid for eid in card.source_evidence_ids if eid in evidence_ids]
        if not match or not sources:
            continue
        rows.append({"period": match.group(), "subject": card.subject,
                     "description": card.value, "evidence_ids": sources})
    return sorted(rows, key=lambda item: re.sub(r"\D", "", item["period"]))


def _regulatory_timeline_chart(chart_data: list[dict[str, Any]], output: Path) -> None:
    image, draw, image_font = _canvas()
    if not chart_data:
        return
    left, right = 150, 1450
    y = 300
    draw.line((left, y, right, y), fill="#5269c7", width=5)
    width = (right - left) / max(1, len(chart_data) - 1)
    for index, item in enumerate(chart_data):
        x = left + width * index if len(chart_data) > 1 else (left + right) / 2
        draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill="#228579", outline="white", width=3)
        draw.text((x, y - 48), item["period"], font=_font(image_font, 17, True), fill=f"#{INK}", anchor="mm")
        lines = _wrap(draw, item["subject"], _font(image_font, 15), 260, 4)
        _draw_lines(draw, x, y + 34, lines, _font(image_font, 15), f"#{MUTED}", anchor="ma", gap=3)
    image.save(output, optimize=True, dpi=(180, 180))


def _competition_points(data: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for card in data.get("competitive_cards") or []:
        if (card.status != "available" or card.observation_level != "quantitative" or not card.subject
                or not card.source_evidence_ids or any(eid not in data["evidence_by_id"] for eid in card.source_evidence_ids)):
            continue
        if card.x_value is None or card.y_value is None or not card.x_label or not card.y_label:
            continue
        if not math.isfinite(card.x_value) or not math.isfinite(card.y_value):
            continue
        axes = (card.x_label, card.y_label, card.x_unit, card.y_unit)
        groups[axes].append({"label": card.subject, "x": card.x_value, "y": card.y_value, "axes": axes,
                             "evidence_ids": card.source_evidence_ids})
    return max(groups.values(), key=len, default=[])


def _competition_chart(data: dict[str, Any], output: Path) -> None:
    points = _competition_points(data)
    if len(points) < 3:
        return
    image, draw, image_font = _canvas()
    left, top, right, bottom = 180, 130, 1450, 720
    min_x, max_x = min(0, min(point["x"] for point in points)), max(point["x"] for point in points)
    min_y, max_y = min(0, min(point["y"] for point in points)), max(point["y"] for point in points)
    max_x = min_x + max(1, max_x - min_x) * 1.2
    max_y = min_y + max(1, max_y - min_y) * 1.2
    for tick in range(6):
        x = left + (right - left) * tick / 5
        y = bottom - (bottom - top) * tick / 5
        draw.line((x, top, x, bottom), fill="#E2E8F0", width=1)
        draw.line((left, y, right, y), fill="#E2E8F0", width=1)
        draw.text((x, bottom + 20), f"{min_x + (max_x - min_x) * tick / 5:.1f}", font=_font(image_font, 14), fill=f"#{MUTED}", anchor="ma")
        draw.text((left - 16, y), f"{min_y + (max_y - min_y) * tick / 5:.1f}", font=_font(image_font, 14), fill=f"#{MUTED}", anchor="rm")
    draw.line((left, bottom, right, bottom), fill=f"#{NAVY}", width=3)
    draw.line((left, top, left, bottom), fill=f"#{NAVY}", width=3)
    colors = [f"#{NAVY}", "#0D9488", "#64748B", "#7C93B3", "#334155"]
    for index, point in enumerate(points[:12]):
        x = left + (point["x"] - min_x) / (max_x - min_x) * (right - left)
        y = bottom - (point["y"] - min_y) / (max_y - min_y) * (bottom - top)
        draw.ellipse((x - 13, y - 13, x + 13, y + 13), fill=colors[index % len(colors)], outline="white", width=3)
        draw.text((x + 18, y - 4), str(point["label"])[:24], font=_font(image_font, 14), fill=f"#{INK}", anchor="lm")
    x_label, y_label, x_unit, y_unit = points[0]["axes"]
    draw.text(((left + right) / 2, 785), f"{x_label}（{x_unit or '无量纲'}）", font=_font(image_font, 18), fill=f"#{MUTED}", anchor="mm")
    draw.text((left, 70), f"{y_label}（{y_unit or '无量纲'}）", font=_font(image_font, 18), fill=f"#{MUTED}", anchor="lm")
    image.save(output, optimize=True, dpi=(180, 180))


def _radar_chart(data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas()
    labels = [item.dimension for item in data["scorecard"]] or ["尚无评分卡"]
    values = [_score(item, data) for item in data["scorecard"]] or [0.0]
    count = max(3, len(labels))
    center, radius = (800, 465), 250
    for level in range(1, 6):
        points = [(center[0] + radius * level / 5 * math.cos(-math.pi / 2 + 2 * math.pi * index / count), center[1] + radius * level / 5 * math.sin(-math.pi / 2 + 2 * math.pi * index / count)) for index in range(count)]
        draw.line(points + [points[0]], fill="#DCE5EE", width=1)
    points = []
    for index in range(count):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        x, y = center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)
        draw.line((center[0], center[1], x, y), fill="#DCE5EE", width=1)
        label = labels[index] if index < len(labels) else ""
        label_font = _font(image_font, 15, True)
        _draw_lines(draw, center[0] + (radius + 55) * math.cos(angle), center[1] + (radius + 55) * math.sin(angle), _wrap(draw, label, label_font, 175, 2), label_font, f"#{INK}", anchor="ma", gap=3)
        value = values[index] if index < len(values) else 0.0
        points.append((center[0] + radius * min(5, max(0, value)) / 5 * math.cos(angle), center[1] + radius * min(5, max(0, value)) / 5 * math.sin(angle)))
    draw.polygon(points, fill="#B9DCD7", outline=f"#{NAVY}")
    for x, y in points:
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#0D9488")
    image.save(output, optimize=True, dpi=(180, 180))


def _market_points(data: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    evidence_ids = {item.evidence_id for item in data.get("evidence", []) if hasattr(item, "evidence_id")}
    for card in data.get("market_cards", []):
        if (getattr(card, "status", "") != "available" or getattr(card, "observation_level", "") != "quantitative"
                or not getattr(card, "unit", "") or not getattr(card, "period", "")
                or not getattr(card, "source_evidence_ids", [])
                or any(eid not in evidence_ids for eid in card.source_evidence_ids)):
            continue
        try:
            value = float(str(card.value).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if value < 0 or not math.isfinite(value):
            continue
        groups[(card.subject, card.unit)].append({"period": card.period, "value": value,
            "subject": card.subject, "unit": card.unit, "evidence_ids": card.source_evidence_ids})
    return sorted(max(groups.values(), key=len, default=[]), key=lambda item: item["period"])


def _horizontal_bar_chart(title: str, subtitle: str, labels: list[str], values: list[int], output: Path, note: str) -> None:
    image, draw, image_font = _canvas(title, subtitle)
    if not labels or not values or max(values, default=0) <= 0:
        draw.rounded_rectangle((250, 270, 1350, 620), radius=10, fill="#F4F8FC", outline="#B7D0E6", width=2)
        draw.text((800, 430), "当前数据不足，未绘制定量图表。", font=_font(image_font, 25, True), fill=f"#{MUTED}", anchor="mm")
    else:
        maximum = max(values) or 1
        left, top, right = 310, 220, 1450
        row_height = min(74, 500 / max(len(labels), 1))
        for index, (label, value) in enumerate(zip(labels, values)):
            y = top + index * row_height
            draw.text((left - 18, y + 18), _wrap(draw, label, _font(image_font, 15), 210, 2)[0], font=_font(image_font, 15), fill=f"#{INK}", anchor="ra")
            draw.rounded_rectangle((left, y, right, y + 32), radius=5, fill="#E8EEF5")
            draw.rounded_rectangle((left, y, left + (right - left) * value / maximum, y + 32), radius=5, fill=f"#{BLUE}")
            draw.text((right + 18, y + 16), str(value), font=_font(image_font, 16, True), fill=f"#{INK}", anchor="lm")
    _footer(draw, image_font, note)
    image.save(output, optimize=True, dpi=(180, 180))


def _source_mix_chart(data: dict[str, Any], output: Path) -> None:
    counts = Counter(str(item.source_type or "其他") for item in data["evidence"])
    labels, values = zip(*counts.most_common()) if counts else ([], [])
    _horizontal_bar_chart("图 2  证据来源构成", "按本次去重纳入记录的来源类型统计", list(labels), list(values), output, "来源：本次任务证据池。上传材料与外部检索证据分开统计。")


def _query_coverage_chart(data: dict[str, Any], output: Path) -> None:
    plans = data["queries"]
    labels = [str(item.query_group or item.claim_id) for item in plans]
    values = [int(item.selected_count or 0) for item in plans]
    _horizontal_bar_chart("图 4  查询组纳入证据量", "每组检索经去重后进入证据池的记录数", labels, values, output, "来源：本次检索计划与去重统计；空值表示该组没有返回可纳入记录。")


def _numeric_score(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    match = re.search(r"(?<!\d)([0-5](?:\.\d+)?)\s*(?:/\s*5|分)?", text)
    if match:
        return float(match.group(1))
    return {"高": 4.0, "较高": 4.0, "中": 3.0, "中等": 3.0, "低": 2.0, "较低": 2.0}.get(text)


def _theme_points(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in data["trend"].get("themes", []):
        if not isinstance(item, dict):
            continue
        x = _numeric_score(item.get("maturity"))
        y = _numeric_score(item.get("opportunity"))
        if x is None or y is None:
            continue
        rows.append({"label": str(item.get("label") or "未命名主题"), "x": max(0, min(5, x)),
                     "y": max(0, min(5, y)), "count": int(item.get("evidence_count") or 0)})
    return rows


def _theme_chart(data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas()
    themes = _theme_points(data)
    if len(themes) < 2:
        return
    left, top, right, bottom = 170, 150, 1460, 740
    for value in range(6):
        x = left + (right - left) * value / 5
        y = bottom - (bottom - top) * value / 5
        draw.line((x, top, x, bottom), fill="#DCE5EE", width=1)
        draw.line((left, y, right, y), fill="#DCE5EE", width=1)
        draw.text((x, bottom + 28), str(value), font=_font(image_font, 15), fill=f"#{MUTED}", anchor="ma")
        draw.text((left - 20, y), str(value), font=_font(image_font, 15), fill=f"#{MUTED}", anchor="rm")
    draw.line((left, bottom, right, bottom), fill=f"#{NAVY}", width=2)
    draw.line((left, top, left, bottom), fill=f"#{NAVY}", width=2)
    draw.text(((left + right) / 2, 805), "技术成熟度（0-5）", font=_font(image_font, 17), fill=f"#{MUTED}", anchor="mm")
    draw.text((55, (top + bottom) / 2), "机会度（0-5）", font=_font(image_font, 17), fill=f"#{MUTED}", anchor="mm")
    colors = ["#2E74B5", "#008F83", "#E07A12", "#7B61B8", "#2C9460"]
    maximum = max((item["count"] for item in themes), default=1)
    for index, item in enumerate(themes[:10]):
        x = left + (right - left) * item["x"] / 5
        y = bottom - (bottom - top) * item["y"] / 5
        radius = 14 + round(25 * math.sqrt(max(0, item["count"]) / max(1, maximum)))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colors[index % len(colors)], outline="white", width=3)
        _draw_lines(draw, x + radius + 10, y - 13, _wrap(draw, item["label"], _font(image_font, 15), 270, 2), _font(image_font, 15), f"#{INK}", gap=2)
    image.save(output, optimize=True, dpi=(180, 180))


def _claim_coverage_chart(data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas()
    evidence = data["evidence_by_id"]
    claims = list(data["claims"])
    if not claims or not data["links"]:
        return
    columns = ["A 直接", "B 方向", "C 背景", "反证"]
    rows = claims[:10]
    left, top, right, bottom = 280, 150, 1450, 760
    row_h = min(58, (bottom - top) / max(1, len(rows)))
    col_w = (right - left) / len(columns)
    draw.text((left - 18, top - 34), "主张", font=_font(image_font, 16, True), fill=f"#{INK}", anchor="rs")
    for col, label in enumerate(columns):
        draw.text((left + col_w * (col + .5), top - 34), label, font=_font(image_font, 16, True), fill=f"#{INK}", anchor="mm")
    for row, claim in enumerate(rows):
        y = top + row * row_h
        draw.text((left - 18, y + row_h / 2), str(claim.claim_id), font=_font(image_font, 15, True), fill=f"#{INK}", anchor="rm")
        counts = [0, 0, 0, 0]
        for link in data["links"]:
            if link.claim_id != claim.claim_id:
                continue
            e = evidence.get(link.evidence_id)
            level = getattr(e, "evidence_level", getattr(link, "evidence_level", "C")) if e else "C"
            if link.relation == "contradicts":
                counts[3] += 1
            else:
                counts[{"A": 0, "B": 1, "C": 2}.get(level, 2)] += 1
        for col, count in enumerate(counts):
            x = left + col * col_w
            draw.rectangle((x, y, x + col_w, y + row_h), outline="#D6DEE8", width=2, fill="#F7F9FB" if count else "white")
            if count:
                draw.ellipse((x + col_w / 2 - 19, y + row_h / 2 - 19, x + col_w / 2 + 19, y + row_h / 2 + 19), fill=["#2E74B5", "#008F83", "#9AA7B5", "#C7434B"][col])
                draw.text((x + col_w / 2, y + row_h / 2), str(count), font=_font(image_font, 15, True), fill="white", anchor="mm")
            else:
                draw.text((x + col_w / 2, y + row_h / 2), "-", font=_font(image_font, 16), fill=f"#{MUTED}", anchor="mm")
    draw.line((left, top, left, bottom), fill=f"#{NAVY}", width=2)
    image.save(output, optimize=True, dpi=(180, 180))


def _indicator_status_chart(data: dict[str, Any], output: Path) -> None:
    image, draw, image_font = _canvas()
    counts = Counter(str(item.status or "missing") for item in data["indicator_validations"])
    labels = ["已支持", "部分支持", "缺少证据", "不适用"]
    values = [counts.get(key, 0) for key in ("supported", "partial", "missing", "not_applicable")]
    total = sum(values)
    if not total:
        return
    center, outer, inner = (560, 440), 190, 105
    colors = ["#2C9460", "#E07A12", "#C7434B", "#9AA7B5"]
    start = -math.pi / 2
    for value, color in zip(values, colors):
        end = start + 2 * math.pi * value / total
        draw.pieslice((center[0] - outer, center[1] - outer, center[0] + outer, center[1] + outer), start=start * 180 / math.pi, end=end * 180 / math.pi, fill=color)
        start = end
    draw.ellipse((center[0] - inner, center[1] - inner, center[0] + inner, center[1] + inner), fill="white")
    draw.text(center, str(total), font=_font(image_font, 34, True), fill=f"#{NAVY}", anchor="mm")
    draw.text((center[0], center[1] + 42), "项指标", font=_font(image_font, 16), fill=f"#{MUTED}", anchor="mm")
    for index, (label, value) in enumerate(zip(labels, values)):
        y = 260 + index * 92
        draw.rounded_rectangle((960, y - 12, 990, y + 18), radius=7, fill=colors[index])
        draw.text((1010, y + 3), f"{label}  {value}", font=_font(image_font, 19), fill=f"#{INK}", anchor="lm")
    image.save(output, optimize=True, dpi=(180, 180))


def _market_data_card_chart(data: dict[str, Any], output: Path) -> None:
    points = _market_points(data)
    image, draw, image_font = _canvas()
    if len(points) < 2:
        return
    left, top, right, bottom = 170, 130, 1450, 725
    maximum = max(point["value"] for point in points) * 1.2 or 1
    for tick in range(6):
        y = bottom - (bottom - top) * tick / 5
        draw.line((left, y, right, y), fill="#E2E8F0", width=1)
        draw.text((left - 16, y), f"{maximum * tick / 5:g}", font=_font(image_font, 16), fill=f"#{MUTED}", anchor="rm")
    step = (right - left) / len(points)
    for index, point in enumerate(points):
        x = left + step * (index + .5)
        y = bottom - point["value"] / maximum * (bottom - top)
        draw.rectangle((x - step * .3, y, x + step * .3, bottom), fill=f"#{BLUE}" if index % 2 == 0 else "#0D9488")
        draw.text((x, y - 20), f"{point['value']:g}", font=_font(image_font, 18, True), fill=f"#{INK}", anchor="ms")
        draw.text((x, bottom + 28), point["period"], font=_font(image_font, 16), fill=f"#{MUTED}", anchor="ma")
    draw.text((left, 70), f"{points[0]['subject']}（{points[0]['unit']}）", font=_font(image_font, 20), fill=f"#{INK}", anchor="lm")
    image.save(output, optimize=True, dpi=(180, 180))


def _competition_data_card_chart(data: dict[str, Any], output: Path) -> None:
    cards = data.get("competitive_cards") or []
    labels = [str(item.subject) for item in cards[:12]]
    values = []
    for item in cards[:12]:
        try:
            values.append(int(item.value))
        except (TypeError, ValueError):
            values.append(0)
    _horizontal_bar_chart(
        "图 9  竞争态势证据覆盖",
        "按查询组展示当前证据覆盖面，不代表机构或产品市场份额排名",
        labels,
        values,
        output,
        "来源：竞争数据卡、查询组和证据编号。数量只描述本次检索覆盖，不外推竞争地位。",
    )


def _set_run(run: Any, *, size: float, bold: bool = False, color: str = INK, italic: bool = False) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    run.font.name = "Liberation Serif"
    run._element.rPr.rFonts.set(qn("w:ascii"), "Liberation Serif")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Liberation Serif")
    run._element.rPr.rFonts.set(qn("w:eastAsia"), _docx_cjk_font())
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor.from_string(color.replace("#", ""))


def _docx_cjk_font() -> str:
    return report_font_family()


def _shade(cell: Any, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    node = tc_pr.find(qn("w:shd"))
    if node is None:
        node = OxmlElement("w:shd")
        tc_pr.append(node)
    node.set(qn("w:fill"), fill.replace("#", ""))


def _table_geometry(table: Any, widths: list[int]) -> None:
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    table.autofit = False
    table_pr = table._tbl.tblPr
    table_width = table_pr.first_child_found_in("w:tblW")
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        table_pr.append(table_width)
    table_width.set(qn("w:w"), str(sum(widths)))
    table_width.set(qn("w:type"), "dxa")
    indent = table_pr.first_child_found_in("w:tblInd")
    if indent is None:
        indent = OxmlElement("w:tblInd")
        table_pr.append(indent)
    indent.set(qn("w:w"), "0")
    indent.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for column in list(grid):
        grid.remove(column)
    for width in widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(width))
        grid.append(column)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_width = tc_pr.find(qn("w:tcW"))
            if tc_width is None:
                tc_width = OxmlElement("w:tcW")
                tc_pr.append(tc_width)
            tc_width.set(qn("w:w"), str(widths[index]))
            tc_width.set(qn("w:type"), "dxa")
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _set_table_borders(table: Any) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    table_pr = table._tbl.tblPr
    borders = table_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "nil" if edge in {"left", "right", "insideV"} else "single")
        node.set(qn("w:sz"), "6" if edge in {"top", "bottom", "insideH"} else "0")
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), "D9D9D9")


def _add_table(document: Any, headers: list[str], rows: list[list[Any]], widths: list[int], size: float = 8.8, citation_urls: dict[str, str] | None = None) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement

    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for index, value in enumerate(headers):
        cell = table.rows[0].cells[index]
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = 0
        _set_run(paragraph.add_run(value), size=size, bold=True, color=NAVY)
        _shade(cell, "EEF3F8")
    header_properties = table.rows[0]._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "true")
    header_properties.append(header)
    for values in rows or [["暂无"] + [""] * (len(headers) - 1)]:
        row = table.add_row()
        # Long rows must be allowed to continue onto the following page.
        for index, value in enumerate(values):
            cell = row.cells[index]
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if index == 0 and len(str(value or "")) < 18 else WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.space_after = 0
            paragraph.paragraph_format.line_spacing = 1.08
            _paragraph(document, str(value or "-"), size=size, indent=False, after=0,
                       citation_urls=citation_urls, target=paragraph)
            paragraph.paragraph_format.line_spacing = 1.15
        # Keep tables black-and-white for screen review and printing. Visual
        # hierarchy comes from borders, spacing and typography.
    _table_geometry(table, widths)
    _set_table_borders(table)
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_after = 5


def _paragraph(document: Any, text: str, *, size: float = 10.5, bold: bool = False, color: str = INK, italic: bool = False, after: float = 7, indent: bool = True, citation_urls: dict[str, str] | None = None, target: Any = None) -> Any:
    """Add a reader-facing paragraph with Chinese first-line indentation.

    Indentation is deliberately opt-out for metadata, captions, references and
    other compact helper text.  Using Word paragraph properties keeps the
    source text clean and avoids inserting visual spaces into exports.
    """
    from docx.shared import Pt

    paragraph = target if target is not None else document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = Pt(18)
    paragraph.paragraph_format.widow_control = True
    paragraph.paragraph_format.first_line_indent = Pt(21 if indent else 0)
    from app.agents.content_assessment.report_composition import reader_text
    value = reader_text(str(text or ""))
    # Keep source addresses clickable in DOCX while preserving the visible
    # reader text.  Citation-number links are added by the caller when an
    # evidence URL is available.
    url_pattern = re.compile(r"\[((?:\\.|[^\]\\])+)\]\(<(https?://[^>]+)>\)|https?://[^\s；;，,。)）]+|\[(\d+)\]")
    cursor = 0
    for match in url_pattern.finditer(value):
        _set_run(paragraph.add_run(value[cursor:match.start()]), size=size, bold=bold, color=color, italic=italic)
        target = match.group(2) or (match.group(0) if match.group(0).startswith("http") else (citation_urls or {}).get(match.group(3) or "", ""))
        label = re.sub(r"\\([\\\[\]])", r"\1", match.group(1)) if match.group(2) else match.group(0)
        if target:
            _add_external_hyperlink(paragraph, label, target, size=size)
        else:
            _set_run(paragraph.add_run(match.group(0)), size=size, bold=bold, color=color, italic=italic)
        cursor = match.end()
    _set_run(paragraph.add_run(value[cursor:]), size=size, bold=bold, color=color, italic=italic)
    return paragraph


def _add_external_hyperlink(paragraph: Any, text: str, url: str, *, size: float = 10.5) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    part = paragraph.part
    relationship = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    node = OxmlElement("w:hyperlink")
    node.set(qn("r:id"), relationship)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    # LibreOffice 7.x only carries external OOXML hyperlinks into PDF when
    # the run has the standard Hyperlink character style. Newer Writer
    # versions accept the explicit color/underline alone, but the style keeps
    # exports consistent across local and test environments.
    hyperlink_style = OxmlElement("w:rStyle"); hyperlink_style.set(qn("w:val"), "Hyperlink"); props.append(hyperlink_style)
    color = OxmlElement("w:color"); color.set(qn("w:val"), BLUE)
    underline = OxmlElement("w:u"); underline.set(qn("w:val"), "single")
    props.extend([color, underline])
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), "Liberation Serif"); fonts.set(qn("w:hAnsi"), "Liberation Serif"); fonts.set(qn("w:eastAsia"), _docx_cjk_font())
    font_size = OxmlElement("w:sz"); font_size.set(qn("w:val"), str(round(size * 2)))
    props.extend([fonts, font_size])
    if re.fullmatch(r"\[\d+\]", text):
        superscript = OxmlElement("w:vertAlign"); superscript.set(qn("w:val"), "superscript"); props.append(superscript)
    run.append(props)
    value = OxmlElement("w:t"); value.text = text
    run.append(value); node.append(run); paragraph._p.append(node)


def _heading(document: Any, text: str, level: int) -> None:
    from docx.shared import Pt

    paragraph = document.add_paragraph(style="Report Chapter" if level <= 2 else f"Heading {level}")
    if level <= 2:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        outline = OxmlElement("w:outlineLvl"); outline.set(qn("w:val"), str(level - 1))
        paragraph._p.get_or_add_pPr().append(outline)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(16 if level == 1 else 10 if level == 2 else 7)
    paragraph.paragraph_format.space_after = Pt(7 if level == 1 else 5 if level == 2 else 4)
    _set_run(paragraph.add_run(text), size=17 if level <= 2 else 11.5, bold=True, color=NAVY if level <= 2 else BLUE)
    if level <= 2:
        border = paragraph._p.get_or_add_pPr()
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        p_border = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "12"); bottom.set(qn("w:space"), "5"); bottom.set(qn("w:color"), NAVY)
        p_border.append(bottom); border.append(p_border)


def _add_toc(document: Any, specs=None, *, max_level=4) -> None:
    """Insert a visible static TOC plus a real Word TOC field for page updates."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    from docx.shared import Pt

    title = document.add_paragraph(style="TOC Heading")
    title.paragraph_format.space_before = Pt(10)
    title.paragraph_format.space_after = Pt(8)
    _set_run(title.add_run("目录"), size=16, bold=True, color=NAVY)
    entries = [title for s in (specs or []) for title in
               [s.title, *(b.text for b in s.blocks if b.kind == "heading" and b.heading_level <= max_level)]]
    # Do not use Word's built-in list styles here.  LibreOffice can keep the
    # list numbering context alive after the TOC and render numbers in every
    # following paragraph and table cell.  Static numbering is sufficient for
    # this visible overview; the real TOC field below remains available for
    # users who update fields in Word.
    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f'TOC \\o "1-{max_level}" \\h \\z \\u'
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "\n".join(entries)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instr, separate, text, end])
    paragraph.paragraph_format.space_after = 8


def _bullet(document: Any, text: str) -> None:
    """Render a compact labeled paragraph; avoid Markdown bullet styling in Word."""
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = None
    paragraph.paragraph_format.first_line_indent = 0
    paragraph.paragraph_format.space_after = 4
    _set_run(paragraph.add_run(str(text)), size=10.2)


def _callout(document: Any, title: str, text: str, fill: str = "FFF6DE", color: str = "7A5A00", citation_urls: dict[str, str] | None = None) -> None:
    table = document.add_table(rows=1, cols=1)
    _table_geometry(table, [REPORT_WIDTH_DXA])
    cell = table.cell(0, 0)
    _shade(cell, fill)
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    left = OxmlElement("w:left"); left.set(qn("w:val"), "single"); left.set(qn("w:sz"), "18"); left.set(qn("w:color"), color)
    borders.append(left); tc_pr.append(borders)
    paragraph = cell.paragraphs[0]
    _paragraph(document, f"【{title}】{text}", size=10.5, color=color, indent=False,
               citation_urls=citation_urls, target=paragraph, after=6)
    margins = OxmlElement("w:tcMar")
    for name, width in (("top", 100), ("bottom", 100), ("left", 180), ("right", 180)):
        node = OxmlElement(f"w:{name}"); node.set(qn("w:w"), str(width)); node.set(qn("w:type"), "dxa"); margins.append(node)
    tc_pr.append(margins)
    from docx.shared import Pt
    spacer = document.add_paragraph()
    spacer.paragraph_format.line_spacing = Pt(4)
    spacer.paragraph_format.space_after = Pt(3)
    _set_run(spacer.add_run(""), size=4)


def _figure(document: Any, path: Path | bytes, caption: str, note: str, citation_urls: dict[str, str] | None = None) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches

    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # Exact body leading clips inline drawings in Word and prevents Writer
    # from reserving their height. Figures require automatic line height.
    paragraph.paragraph_format.line_spacing = 1
    paragraph.paragraph_format.first_line_indent = 0
    paragraph.paragraph_format.keep_with_next = True
    paragraph.add_run().add_picture(io.BytesIO(path) if isinstance(path, bytes) else str(path), width=Inches(6.69))
    caption_paragraph = _paragraph(document, caption, size=9.5, bold=False, color=INK, after=2, indent=False)
    caption_paragraph.paragraph_format.keep_with_next = True
    caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    source_paragraph = _paragraph(document, f"数据来源：{note}", size=8.8, color=MUTED, after=8, indent=False, citation_urls=citation_urls)
    source_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT


def _configure(document: Any) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Mm, Pt, RGBColor

    section = document.sections[0]
    section.top_margin = Inches(0.82)
    section.bottom_margin = Inches(0.70)
    section.left_margin = section.right_margin = Mm(20)
    section.header_distance = section.footer_distance = Inches(0.35)
    normal = document.styles["Normal"]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.different_first_page_header_footer = True
    normal.font.name = "Liberation Serif"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), _docx_cjk_font())
    normal.paragraph_format.line_spacing = Pt(18)
    normal.paragraph_format.widow_control = True
    from docx.enum.style import WD_STYLE_TYPE
    hyperlink_style = document.styles["Hyperlink"] if "Hyperlink" in document.styles else document.styles.add_style("Hyperlink", WD_STYLE_TYPE.CHARACTER)
    hyperlink_style.font.color.rgb = RGBColor(37, 99, 235)
    hyperlink_style.font.underline = True
    chapter_style = document.styles.add_style("Report Chapter", WD_STYLE_TYPE.PARAGRAPH)
    chapter_style.base_style = document.styles["Heading 1"]
    for style_name in ("Title", "Subtitle", "Heading 1", "Heading 2", "Heading 3", "Heading 4", "TOC Heading"):
        style = document.styles[style_name]
        style.font.color.rgb = RGBColor(0, 0, 0)
        if style.element.pPr is not None:
            for border in list(style.element.pPr.findall(qn("w:pBdr"))):
                style.element.pPr.remove(border)
    # Compact all real TOC levels rather than inheriting body leading.
    for level in range(1, 5):
        name = f"TOC {level}"
        style = document.styles[name] if name in document.styles else document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.font.size = Pt(9.5)
        style.font.name = "Liberation Serif"
        style.paragraph_format.line_spacing = Pt(12.5)
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.first_line_indent = Pt(0)
        style.paragraph_format.left_indent = Pt((level - 1) * 10)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), _docx_cjk_font())
    update_fields = document.settings.element.find(qn("w:updateFields"))
    if update_fields is None:
        update_fields = OxmlElement("w:updateFields")
        document.settings.element.append(update_fields)
    update_fields.set(qn("w:val"), "true")
    section.header.paragraphs[0].clear()
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _set_run(footer.add_run(""), size=9, color="666666")
    section.first_page_header.paragraphs[0].clear()
    section.first_page_footer.paragraphs[0].clear()
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    _set_run(footer.add_run(""), size=9, color="666666")


def _running_header(document: Any, title: str) -> None:
    """Use the official report title; cover and frozen body remain untouched."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    for section in document.sections:
        section.different_first_page_header_footer = True
        section.first_page_header.paragraphs[0].clear()
        header = section.header.paragraphs[0]
        header.clear()
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        header.paragraph_format.first_line_indent = Pt(0)
        header.paragraph_format.space_before = Pt(0)
        header.paragraph_format.space_after = Pt(0)
        header.paragraph_format.line_spacing = Pt(11)
        _set_run(header.add_run(title), size=8, color="999999")


def _cover(document: Any, data: dict[str, Any]) -> None:
    if data.get("template_version") in {"ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"}:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
        for _ in range(6):
            document.add_paragraph()
        title = document.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run(title.add_run(data["title"]), size=25, bold=True, color=NAVY)
        subtitle = document.add_paragraph()
        subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subtitle.paragraph_format.space_after = Pt(28)
        _set_run(subtitle.add_run("创新性评估报告"), size=16, color=BLUE)
        kind = {"paper": "论文", "project": "项目", "guideline": "指南"}.get(data["plan"].artifact_type, "研究材料")
        _add_table(document, ["项目", "内容"], [
            ["评估对象", data["profile"].assessment_object or data["profile"].title],
            ["材料类型", kind], ["文献截止日期", data["plan"].evidence_cutoff_date],
            ["生成日期", data["generated_at"]],
        ], [2250, 7110], 10.5)
        document.add_page_break()
        return
    # New result reports lead with the conclusion and assessed object.  Keep
    # the prose cover only for frozen historical/reference snapshots so an old
    # export remains visually and semantically stable.
    plan = data.get("plan")
    section_keys = {getattr(item, "section_key", "") for item in getattr(plan, "section_specs", [])}
    result_outline = {"executive", "object_points", "research_progress", "comparison_novelty", "trends_advice", "references"}
    if result_outline <= section_keys:
        return _cover_v2(document, data)
    return _prose_cover(document, data)


def _decision_card(document: Any, data: dict[str, Any]) -> None:
    """Add a one-page reader card before the detailed table of contents."""
    _heading(document, "评估结论卡", 1)
    profile, gate, metrics = data["profile"], data["quality_gate"], data["metrics"]
    _paragraph(
        document,
        f"评估对象：{profile.assessment_object or profile.title or '待确认'}",
        bold=True,
        indent=False,
        after=5,
    )
    status = "正式评估" if gate.report_eligibility == "formal" else "探索性评估"
    _paragraph(document, f"当前状态：{status}。本页只给出快速判断，详细证据和限制见正文。", indent=False, after=10)
    decisions = {item.claim_id: item for item in data["decisions"]}
    for claim in data["claims"][:6]:
        decision = decisions.get(claim.claim_id)
        label = decision.label if decision else "当前不可判断"
        rationale = (decision.rationale if decision else "尚未形成主张级判断").strip()
        _paragraph(document, f"{claim.claim_id} · {label}：{rationale[:260]}", indent=False, after=5)
    if len(data["claims"]) > 6:
        _paragraph(document, f"其余 {len(data['claims']) - 6} 项主张详见逐项证据分析。", size=9.6, color=MUTED, indent=False, after=7)
    risks = []
    for decision in data["decisions"]:
        risks.extend(decision.risk_notes)
    for item in list(dict.fromkeys(risks))[:3]:
        _paragraph(document, f"主要限制：{item}", size=9.8, indent=False, after=4)
    if gate.warnings:
        _paragraph(document, "下一步：" + "；".join(gate.warnings[:2]), size=9.8, indent=False, after=4)
    _paragraph(
        document,
        f"证据覆盖概况：主张 {metrics.claim_coverage:.0%}；指标 {metrics.indicator_coverage:.0%}；引用完整率 {metrics.citation_integrity:.0%}。",
        size=9.6,
        color=MUTED,
        indent=False,
        after=0,
    )
    document.add_page_break()


def _prose_cover(document: Any, data: dict[str, Any]) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    _cover_background(document)
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.space_before = Pt(185)
    _set_run(paragraph.add_run("内容评估 · 查新类智能体工作实录"), size=11, bold=True, color="0D9488")
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title.paragraph_format.space_before = Pt(28)
    title.style = document.styles["Title"]
    title.paragraph_format.line_spacing = Pt(34)
    title.paragraph_format.space_after = Pt(0)
    _set_run(title.add_run(data["title"].removesuffix("内容评估报告") + "\n内容评估报告"), size=25, bold=True, color="1E293B")
    subtitle = document.add_paragraph()
    subtitle.paragraph_format.space_before = Pt(22)
    subtitle.paragraph_format.line_spacing = Pt(21)
    _set_run(subtitle.add_run("——基于文档解析、要点提取、内容比对与证据溯源、创新性评估、技术趋势与竞争态势五步工作流的评估过程实录与综合评估结论"), size=13, color="475569")
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    rule = document.add_paragraph()
    rule.paragraph_format.space_before = Pt(28)
    rule.paragraph_format.space_after = Pt(14)
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in {"val": "single", "sz": "4", "space": "1", "color": "CBD5E1"}.items():
        bottom.set(qn(f"w:{key}"), value)
    borders.append(bottom); rule._p.get_or_add_pPr().append(borders)
    plan = data["plan"]
    for label, value in [("评估对象", data["profile"].assessment_object or data["title"].removesuffix("内容评估报告")),
                         ("委托目的", data["profile"].assessment_goal), ("报告类型", plan.report_type),
                         ("报告版本", plan.report_version), ("报告日期", data["generated_at"][:10])]:
        item = document.add_paragraph()
        item.paragraph_format.space_after = Pt(7)
        item.paragraph_format.line_spacing = Pt(18)
        _set_run(item.add_run(f"{label}："), size=10.5, bold=True, color=NAVY)
        _set_run(item.add_run(value or "未提供"), size=10.5, color="475569")
    document.add_page_break()


def _cover_background(document: Any) -> None:
    """Page-anchored reference grid and corner marks, confined to the cover."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm
    Image, ImageDraw, _ = _pillow()
    image = Image.new("RGB", (1600, 2263), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 1600, 81):
        draw.line((x, 0, x, 2263), fill="#EEF2FF", width=1)
    for y in range(0, 2263, 81):
        draw.line((0, y, 1600, y), fill="#EEF2FF", width=1)
    draw.line((140, 400, 140, 140, 400, 140), fill=f"#{NAVY}", width=6)
    draw.line((1200, 2120, 1460, 2120, 1460, 1860), fill=f"#{NAVY}", width=6)
    draw.rectangle((1310, 140, 1460, 185), fill="#0D9488")
    draw.rectangle((140, 2080, 290, 2125), fill=f"#{NAVY}")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    header = document.sections[0].first_page_header.paragraphs[0]
    picture = header.add_run().add_picture(io.BytesIO(buffer.getvalue()), width=Mm(210), height=Mm(297))
    inline = picture._inline
    anchor = OxmlElement("wp:anchor")
    for key, value in {"distT": "0", "distB": "0", "distL": "0", "distR": "0", "simplePos": "0",
                       "relativeHeight": "0", "behindDoc": "1", "locked": "0", "layoutInCell": "1", "allowOverlap": "1"}.items():
        anchor.set(key, value)
    simple = OxmlElement("wp:simplePos"); simple.set("x", "0"); simple.set("y", "0"); anchor.append(simple)
    for direction in ("H", "V"):
        position = OxmlElement(f"wp:position{direction}"); position.set("relativeFrom", "page")
        offset = OxmlElement("wp:posOffset"); offset.text = "0"; position.append(offset); anchor.append(position)
    for tag in ("extent", "effectExtent"):
        node = inline.find(qn(f"wp:{tag}"))
        if node is not None: anchor.append(node)
    anchor.append(OxmlElement("wp:wrapNone"))
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        node = inline.find(qn(tag))
        if node is not None: anchor.append(node)
    inline.getparent().replace(inline, anchor)


def _cover_v2(document: Any, data: dict[str, Any]) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    for _ in range(5):
        document.add_paragraph().paragraph_format.space_after = Pt(8)
    kicker = document.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run(kicker.add_run("科技文献智能服务平台"), size=12, bold=True, color=BLUE)
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(14)
    title.paragraph_format.space_after = Pt(8)
    _set_run(title.add_run(data["title"]), size=25, bold=True, color=NAVY)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(24)
    _set_run(subtitle.add_run("基于材料边界、文献检索证据与主张级比对的结构化评估报告"), size=12, color=MUTED)
    profile, plan = data["profile"], data["plan"]
    _add_table(document, ["项目", "内容"], [
        ["评估对象", profile.assessment_object or profile.title or "待确认"],
        ["委托目的", profile.assessment_goal or "科研项目创新性评估"],
        ["报告类型", plan.report_type],
        ["报告版本", plan.report_version],
        ["证据截止日期", plan.evidence_cutoff_date or "待记录"],
        ["生成时间", data["generated_at"]],
        ["评估范围", profile.scope_boundary or "以任务输入材料及检索计划为边界"],
        ["版本链", f"输入 {data['versions'].input_version}；证据池 {data['versions'].evidence_pool_version}；规则 {data['versions'].rule_version}；模型 {data['versions'].model_version}；图表 {data['versions'].chart_data_snapshot_version}；模板 {data['versions'].template_version}"],
    ], [2250, 7110], 9.5)
    gate = data["quality_gate"]
    strength = "正式评估" if gate.report_eligibility == "formal" else "探索性评估"
    limitation = "；".join((gate.blockers + gate.warnings)[:2]) or "结论依据当前任务材料、检索轨迹和可定位证据形成。"
    _paragraph(document, f"结论强度：本报告为{strength}。{limitation}", size=10.2, color=INK, after=12, indent=False)
    document.add_page_break()


def _markdown_table(block: str) -> tuple[list[str], list[list[str]]] | None:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) < 2 or not all(line.startswith("|") and line.endswith("|") for line in lines[:2]):
        return None
    headers = [item.strip() for item in lines[0].strip("|").split("|")]
    if not headers:
        return None
    if not re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", lines[1]):
        return None
    rows: list[list[str]] = []
    for line in lines[2:]:
        if not line.startswith("|"):
            continue
        values = [item.strip() for item in line.strip("|").split("|")]
        rows.append((values + [""] * len(headers))[:len(headers)])
    return headers, rows


def _content_widths(column_count: int) -> list[int]:
    widths = _content_widths_v2(column_count)
    normalized = [int(w * REPORT_WIDTH_DXA / sum(widths)) for w in widths]
    normalized[-1] += REPORT_WIDTH_DXA - sum(normalized)
    return normalized


def _reference_table_widths(headers: list[str]) -> list[int]:
    """Give long narrative columns room instead of sizing every table by count."""
    layouts = {
        ("主题", "检索式", "来源与年份范围", "候选／纳入", "筛选依据"): [1800, 3000, 2000, 950, 1700],
        ("评估要点", "判断", "来源", "证据位置", "正文位置"): [2300, 1100, 2700, 1000, 2350],
        ("指标", "材料值", "状态", "材料报告结果", "文献基线", "验证条件", "判断"): [1150, 850, 1000, 1600, 1600, 1600, 1750],
        ("内容", "材料位置", "补充说明"): [2300, 1100, 5950],
        ("编号", "主张/创新点", "在先证据（最相近报道）", "判定", "判定理由与口径风险"): [650, 1800, 2000, 900, 4000],
        ("主张/创新点", "证据", "关系", "判定依据"): [700, 2700, 1000, 4950],
        ("指南要求", "项目应答", "符合性", "说明"): [2700, 2800, 900, 2950],
        ("候选指标", "公开水平/基线", "项目目标", "验证条件", "本次评估结论"): [1400, 2300, 1300, 2000, 2350],
        ("检索主题", "检索式", "候选/纳入", "执行情况"): [2200, 4500, 1100, 1550],
        ("核查项目", "公开信息", "来源", "适用范围"): [1800, 2300, 3300, 1950],
        ("竞争方/研究对象", "公开水平", "项目对标口径", "证据"): [2100, 2400, 2100, 2750],
    }
    widths = layouts.get(tuple(headers))
    if widths is None:
        return _content_widths(len(headers))
    scaled = [int(w * REPORT_WIDTH_DXA / sum(widths)) for w in widths]
    scaled[-1] += REPORT_WIDTH_DXA - sum(scaled)
    return scaled


def _content_widths_v2(column_count: int) -> list[int]:
    if column_count == 2:
        return [2250, 7110]
    if column_count == 4:
        return [700, 3250, 1500, 3910]
    if column_count == 5:
        return [900, 2450, 2100, 1300, 2610]
    if column_count == 7:
        return [650, 850, 1500, 700, 900, 2700, 2060]
    if column_count == 8:
        return [650, 1200, 1550, 550, 850, 1800, 1350, 1410]
    if column_count == 9:
        return [600, 1250, 1250, 550, 850, 1250, 1450, 1100, 1060]
    width = REPORT_WIDTH_DXA // max(1, column_count)
    return [width] * column_count


def _add_content_block(
    document: Any,
    block: str,
    *,
    appendix: bool = False,
    table_size: float | None = None,
    citation_urls: dict[str, str] | None = None,
) -> None:
    parsed = _markdown_table(block)
    if parsed:
        headers, rows = parsed
        _add_table(
            document,
            headers,
            rows,
            _reference_table_widths(headers),
            size=table_size if table_size is not None else 7.0 if appendix else 8.6,
            citation_urls=citation_urls,
        )
        return
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        return
    for line in lines:
        if line.startswith("### "):
            _heading(document, line[4:], 3)
        elif line.startswith("#### "):
            _heading(document, line[5:], 3)
        elif line.startswith("- "):
            _bullet(document, line[2:])
        else:
            _paragraph(document, line, size=10 if appendix else 10.5, indent=not appendix, citation_urls=citation_urls)


def _add_report_blocks(document: Any, spec: Any, *, citation_urls: dict[str, str], citation_numbers: dict[str, int], figure_renderer=None, max_nav_level=4) -> None:
    """Render immutable structured blocks while keeping legacy snapshots readable."""
    from app.agents.content_assessment.report_composition import block_text_with_sources
    blocks = getattr(spec, "blocks", None) or []
    if not blocks:
        for part in re.split(r"\n\s*\n", spec.content or "本章节暂无可用分析内容，已记录为待补证。"):
            _add_content_block(document, _render_citations(part, citation_numbers), appendix=spec.is_appendix, table_size=10, citation_urls=citation_urls)
        return
    styles = {
        "execution": ("E9F7F5", "0D9488", "公开执行摘要"),
        "result": ("EEF5FF", "1E3A8A", "结果"),
        "risk": ("FFF7E6", "C26A00", "风险"),
    }
    for item in blocks:
        if item.kind == "figure":
            if figure_renderer:
                figure_renderer(item.text)
            continue
        text = block_text_with_sources(item, references=spec.section_key == "references")
        text = _render_citations(text.strip(), citation_numbers)
        if item.kind == "heading":
            if item.heading_level > max_nav_level:
                paragraph = _paragraph(document, text, size=11.5, bold=True, color=BLUE, indent=False)
                paragraph.paragraph_format.keep_with_next = True
            else:
                _heading(document, text, item.heading_level)
        elif item.kind == "caption":
            paragraph = _paragraph(document, text, size=10, bold=True, color=BLUE, indent=False)
            paragraph.paragraph_format.keep_with_next = True
        elif item.kind == "table":
            for part in re.split(r"\n\s*\n", text):
                _add_content_block(document, part, appendix=spec.is_appendix, table_size=10, citation_urls=citation_urls)
        elif item.kind == "callout":
            fill, color, label = styles.get(item.callout_style, styles["result"])
            _callout(document, label, text, fill=fill, color=color, citation_urls=citation_urls)
        else:
            for part in re.split(r"\n\s*\n", text):
                _add_content_block(document, part, appendix=spec.is_appendix,
                                   table_size=10, citation_urls=citation_urls)


def _chart_is_ready(data: dict[str, Any], key: str) -> bool:
    """Only create figures with a real data basis; never emit placeholders."""
    if key == "workflow":
        return bool(data.get("stage_trace") and (data["profile"].document_records or data["claims"] or data["queries"]))
    if key == "claim_evidence_network":
        return bool(data.get("claims"))
    if key == "claim_judgement":
        if data.get("template_version") == "ContentAssessmentResearchReportV7":
            return bool(_claim_judgement_rows(data))
        return bool(data.get("decisions"))
    if key == "years":
        if data.get("template_version") == "ContentAssessmentResearchReportV7":
            return bool(_annual_literature_counts(data)["total"])
        return len(_year_theme_series(data)) >= 2
    if key == "regulatory_timeline":
        return bool(_regulatory_timeline_data(data))
    if key == "themes":
        return len(_theme_points(data)) >= 2 and all(item["count"] > 0 for item in _theme_points(data))
    if key == "radar":
        scores = [_score(item, data) for item in data["scorecard"]]
        return (len(data["scorecard"]) == 6 and len({item.dimension for item in data["scorecard"]}) == 6
                and all(score is not None and 0 <= score <= 5 for score in scores))
    if key == "market":
        return len(_market_points(data)) >= 1
    if key == "competition":
        return len({point["label"] for point in _competition_points(data)}) >= 3
    if key == "claim_coverage":
        return bool(data["claims"] and data["links"])
    if key == "indicator_status":
        validations = data.get("indicator_validations") or []
        return bool(validations and any(getattr(item, "status", "") for item in validations))
    return False


_CHART_PRIORITY = [
    "workflow",
    "claim_judgement",
    "years",
    "market",
    "competition",
    "radar",
    "claim_coverage",
    "indicator_status",
    "themes",
]

_CHART_TYPES = {
    "claim_evidence_network": "bipartite_network",
    "workflow": "workflow",
    "claim_judgement": "point_judgement_summary",
    "years": "multi_series_line",
    "themes": "bubble_positioning",
    "market": "market_size_bar",
    "competition": "competition_scatter",
    "radar": "radar",
    "claim_coverage": "evidence_matrix",
    "indicator_status": "donut",
    "regulatory_timeline": "dated_event_timeline",
}


def _coverage_points(data: dict[str, Any], key: str) -> list[tuple[str, int]]:
    """Fallback counts describe available records, never proxy quantitative results."""
    if key == "years":
        literature = [e for e in data.get("evidence", []) if e.source_type == "literature"]
        return [("具有发表年份的文献", sum(bool(e.year) for e in literature)),
                ("未注明年份的文献", sum(not e.year for e in literature))] if literature else []
    if key in {"market", "competition"}:
        cards = data.get("market_cards" if key == "market" else "competitive_cards", [])
        known = data.get("evidence_by_id", {})
        if not cards:
            return []
        sourced = sum(any(eid in known for eid in getattr(c, "source_evidence_ids", [])) for c in cards)
        usable = len(_market_points(data) if key == "market" else _competition_points(data))
        return [("整理的公开记录", len(cards)), ("具有证据来源的记录", sourced), ("满足同口径数值条件的记录", usable)]
    if key == "radar":
        cards = data.get("scorecard", [])
        if not cards:
            return []
        known = data.get("evidence_by_id", {})
        return [("已形成评估说明的维度", len(cards)),
                ("具有引用依据的维度", sum(any(eid in known for eid in c.basis) for c in cards)),
                ("具有明确数值的维度", sum(_score(c, data) is not None for c in cards))]
    return []


def _judgement_chart(data: dict[str, Any], output: Path) -> None:
    counts = Counter(d.label for d in data.get("decisions", []))
    _horizontal_bar_chart("主张新颖性判定结果", "按本次逐点判定统计，不代表实验达成情况",
                          list(counts), list(counts.values()), output, "来源：本次报告冻结的逐点判定。")


def _coverage_chart(entry: dict[str, Any], output: Path) -> None:
    points = entry["chart_data"]
    # A zero count is an actual observation; do not replace it with a placeholder.
    image, draw, image_font = _canvas(entry["chart_caption"], "统计单位：条／项；此图不表示规模、排名或评分")
    maximum = max((p[1] for p in points), default=0) or 1
    for index, (label, value) in enumerate(points):
        y = 250 + index * 130
        draw.text((410, y + 25), label, font=_font(image_font, 20), fill=f"#{INK}", anchor="rm")
        draw.rectangle((440, y, 1380, y + 50), fill="#E8EEF5")
        if value:
            draw.rectangle((440, y, 440 + 940 * value / maximum, y + 50), fill="#0D9488")
        draw.text((1410, y + 25), str(value), font=_font(image_font, 22, True), fill=f"#{NAVY}", anchor="lm")
    _footer(draw, image_font, "口径：冻结快照中的记录覆盖情况；不同统计项可能重叠。")
    image.save(output, optimize=True, dpi=(180, 180))


def _select_chart_keys(data: dict[str, Any], requested: list[str], *, include_coverage: bool = True) -> tuple[list[str], list[dict[str, Any]]]:
    """Keep fixed figure positions with explicit, data-backed fallback semantics."""
    unique = list(dict.fromkeys(requested))
    selected = []
    manifest = []
    for key in unique:
        ready = _chart_is_ready(data, key)
        fallback = _coverage_points(data, key) if include_coverage and not ready else []
        included = ready or bool(fallback)
        if included:
            selected.append(key)
        manifest.append({"chart_key": key, "chart_type": _CHART_TYPES.get(key, "unknown") if ready else "coverage_bar",
                         "render_mode": "primary" if ready else "coverage",
                         "included": included, "selection_reason": "真实数据满足原图条件" if ready else
                         "按真实记录绘制覆盖统计，未推算规模或评分" if fallback else "本次没有此类数据记录"})
    return selected, manifest


REPORT_CHARTS = {
    "claim_evidence_network": ("要点与比对证据对应图", "来源：冻结报告中实际纳入逐项比对的文献；连线只表示存在比对依据。", None),
    "workflow": ("内容评估智能体五步工作流及本次执行概况", "后台八阶段按报告五步聚合；来自冻结的执行记录。", _workflow_chart),
    "claim_judgement": ("各研究要点的创新性判定汇总", "依据正文逐项判断及其引用来源整理；不代表数值评分。", _judgement_chart),
    "years": ("多主题年度文献趋势", "本次去重证据池按检索主题和发表年份统计；不代表全库发文量。", _years_chart),
    "market": ("领域市场规模或公开规模数据对比", "同一对象、同一单位的公开规模数据；预测与实测性质以原始资料为准。", _market_data_card_chart),
    "competition": ("竞争态势定位", "具有同口径轴定义、数值和来源的对象；不外推市场份额或产品排名。", _competition_chart),
    "regulatory_timeline": ("监管与标准事件时间轴", "仅列出来源材料中注明时间且可追溯的标准或监管事件。", None),
    "radar": ("项目内容综合评估雷达图", "六项显式评分（0–5）；声明区间采用中点并非独立测量值。", _radar_chart),
}


def _claim_evidence_chart_data(data: dict[str, Any], snapshot: Any) -> dict[str, Any]:
    """Use only frozen sources cited in the chapter-five point comparison."""
    from app.agents.content_assessment.scholarly_report import point_title
    from app.services.research_graph_rendering import COLORS
    selection = snapshot.audit_data.get("report_source_selection", {})
    cited = snapshot.citation_numbers
    allowed = {claim.claim_id: set(selection[claim.claim_id]) if claim.claim_id in selection else set(cited)
               for claim in data["claims"]}
    records = {e.evidence_id: e for e in data["evidence"]}
    pairs = set()
    for section in snapshot.sections:
        if section.section_key != "comparison":
            continue
        for block in section.blocks:
            if block.claim_id in allowed:
                for eid in block.evidence_ids:
                    if eid in allowed[block.claim_id] and eid in cited and eid in records:
                        pairs.add((block.claim_id, eid))
    points = [{"id": claim.claim_id, "label": point_title(claim), "number": i + 1,
               "color": COLORS[i % len(COLORS)]} for i, claim in enumerate(data["claims"])]
    ids = {eid for _, eid in pairs}
    evidence = [{"id": e.evidence_id, "label": e.title, "number": cited[e.evidence_id]}
                for e in data["evidence"] if e.evidence_id in ids]
    links = [{"source": p["id"], "target": e["id"]} for p in points for e in evidence if (p["id"], e["id"]) in pairs]
    return {"points": points, "evidence": evidence, "links": links}

FIGURE_NUMBERS = {"workflow": "1-1", "claim_evidence_network": "5-1", "claim_judgement": "5-1", "years": "6-1",
                  "market": "6-2", "competition": "6-3", "radar": "9-1"}
RESULT_FIGURE_NUMBERS = {"claim_evidence_network": "5-1", "claim_judgement": "4-1", "radar": "4-2", "years": "5-1",
                         "market": "5-2", "competition": "5-3"}
_COVERAGE_CAPTIONS = {"years": "年度文献记录覆盖情况", "market": "市场公开记录覆盖情况",
                      "competition": "竞争方公开记录覆盖情况", "radar": "综合评估维度覆盖情况"}


def prepare_chart_manifest(state: ContentAssessmentState) -> None:
    snapshot = state.report_snapshot
    if snapshot is None:
        return
    if snapshot.chart_manifest:
        return
    if snapshot.schema_version not in {"report-snapshot-v7", "report-snapshot-v8", "report-snapshot-v9"}:
        # Older versions did not freeze the inputs needed to regenerate charts.
        return
    data = _data(state)
    requested = list(dict.fromkeys(key for spec in snapshot.sections for key in spec.chart_keys if key in REPORT_CHARTS))
    is_v7 = snapshot.template_version == "ContentAssessmentResearchReportV7"
    selected, manifest = _select_chart_keys(data, requested, include_coverage=not is_v7)
    annual_counts = _annual_literature_counts(data)
    judgement_rows = _claim_judgement_rows(data)
    regulatory_rows = _regulatory_timeline_data(data)
    chart_data = {
        "claim_evidence_network": _claim_evidence_chart_data(data, snapshot),
        "workflow": {"stages": snapshot.stage_trace, "material_count": len(data["profile"].document_records),
                     "claim_count": len(data["claims"]), "evidence_count": len(data["evidence"]), "query_count": len(data["queries"])},
        "years": annual_counts if is_v7 else _year_theme_series(data), "market": _market_points(data),
        "claim_judgement": judgement_rows if is_v7 else dict(Counter(d.label for d in data["decisions"])),
        "competition": _competition_points(data),
        "regulatory_timeline": regulatory_rows,
        "radar": [{"dimension": item.dimension, "score_range": item.score_range, "score": _score(item, data)} for item in data["scorecard"]],
    }
    for entry in manifest:
        key = entry["chart_key"]
        spec = next(item for item in snapshot.sections if key in item.chart_keys)
        caption, note, _ = REPORT_CHARTS[key]
        result_layout = snapshot.template_version in {"ContentAssessmentResultReportV3", "ContentAssessmentResultReportV4", "ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"}
        figure_number = (RESULT_FIGURE_NUMBERS if result_layout else FIGURE_NUMBERS).get(key, FIGURE_NUMBERS[key])
        if snapshot.template_version in {"ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6"} and key == "years":
            figure_number = "3-1"
        if is_v7:
            figure_number = {"years": "3-1", "claim_evidence_network": "5-1", "claim_judgement": "6-1",
                             "market": "7-1", "competition": "7-2", "regulatory_timeline": "7-3"}.get(key, figure_number)
        if result_layout:
            caption = caption.replace("主张", "要点").replace("项目内容", "评估对象")
        if is_v7:
            caption = {"years": "年度文献发表年份分布", "claim_judgement": "研究要点判定汇总"}.get(key, caption)
        if entry["render_mode"] == "coverage":
            caption = _COVERAGE_CAPTIONS.get(key, caption)
            chart_data[key] = _coverage_points(data, key)
            note = "冻结快照中的实际记录覆盖统计，单位为条／项；不代表市场规模、竞争排名或评估评分。"
        source_ids = []
        if key in {"market", "competition"} and entry["render_mode"] == "primary":
            source_ids = list(dict.fromkeys(eid for point in chart_data[key] for eid in point["evidence_ids"]))
        elif key == "regulatory_timeline":
            source_ids = list(dict.fromkeys(eid for point in chart_data[key] for eid in point["evidence_ids"]))
        elif key == "claim_judgement":
            source_ids = list(dict.fromkeys(eid for decision in data["decisions"] for eid in decision.evidence_ids
                                             if eid in snapshot.citation_numbers))
        elif key == "years":
            source_ids = [item.evidence_id for item in data["evidence"] if item.source_type == "literature"]
        elif key == "radar":
            source_ids = list(dict.fromkeys(eid for item in data["scorecard"] for eid in item.basis if eid in snapshot.citation_numbers))
        elif key == "claim_evidence_network":
            source_ids = [item["id"] for item in chart_data[key]["evidence"]]
        if source_ids:
            note += " 引用：" + _refs(source_ids, snapshot.citation_numbers) + "。"
        entry.update({"section_key": spec.section_key, "figure_number": figure_number,
                      "chart_caption": f"图 {figure_number} {caption}",
                      "chart_source_note": note, "chart_data": chart_data[key],
                      "data_basis": "冻结的报告数据", "chart_data_basis": "冻结的报告数据"})
    snapshot.chart_manifest = manifest


def build_report_chart_assets(state: ContentAssessmentState) -> dict[str, bytes]:
    if not state.report_snapshot or not state.report_snapshot.chart_manifest:
        prepare_chart_manifest(state)
    data = _data(state)
    assets = {}
    with tempfile.TemporaryDirectory(prefix="assessment-figures-") as temporary:
        for entry in state.report_snapshot.chart_manifest:
            if not entry["included"] or entry["chart_key"] not in REPORT_CHARTS:
                continue
            key = entry["chart_key"]
            path = Path(temporary) / f"{key}.png"
            if key == "claim_evidence_network":
                from app.services.research_graph_rendering import assessment_evidence_parts
                parts = assessment_evidence_parts(entry["chart_data"])
                if parts:
                    assets[key] = parts[0]
                    for i, content in enumerate(parts[1:], 2):
                        assets[f"{key}:{i}"] = content
                continue
            if state.report_snapshot.template_version == "ContentAssessmentResearchReportV7":
                if key == "years":
                    _annual_literature_chart(entry["chart_data"], path)
                elif key == "claim_judgement":
                    _claim_judgement_chart(entry["chart_data"], path)
                elif key == "regulatory_timeline":
                    _regulatory_timeline_chart(entry["chart_data"], path)
                else:
                    REPORT_CHARTS[key][2](data, path)
                assets[key] = path.read_bytes()
                continue
            if entry.get("render_mode") == "coverage":
                _coverage_chart(entry, path)
            else:
                REPORT_CHARTS[key][2](data, path)
            assets[key] = path.read_bytes()
    return assets


def _build_canonical_docx(state: ContentAssessmentState) -> tuple[bytes, str]:
    """Render the frozen eleven-chapter reference report."""
    from docx import Document
    data = _data(state)
    document = Document()
    _configure(document)
    _cover(document, data)
    research = bool(state.report_snapshot and state.report_snapshot.template_version in {"ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"})
    _add_toc(document, state.report_plan.section_specs, max_level=3 if research else 4)
    document.add_page_break()
    plan = data["plan"]
    citations = state.report_snapshot.citation_numbers if state.report_snapshot else _citation_map(data["evidence"])
    from app.common.retrieval.links import citation_urls as resolve_citations
    citation_urls = resolve_citations(data["evidence"], citations)
    if not state.report_snapshot.chart_manifest:
        prepare_chart_manifest(state)
    assets = build_report_chart_assets(state)
    manifest = state.report_snapshot.chart_manifest
    def render_figure(key):
        entry = next((e for e in manifest if e["chart_key"] == key), None)
        if entry and entry["included"] and key in assets:
            contents = [assets[key]]
            contents.extend(assets[f"{key}:{i}"] for i in range(2, len(assets) + 2) if f"{key}:{i}" in assets)
            for index, content in enumerate(contents, 1):
                caption = entry["chart_caption"] + (f"（{index}）" if len(contents) > 1 else "")
                _figure(document, content, caption, entry["chart_source_note"], citation_urls)
    for spec in plan.section_specs:
        _heading(document, spec.title, 1 if spec.level <= 1 else 2)
        if spec.section_key.startswith("part_"):
            continue
        _add_report_blocks(document, spec, citation_urls=citation_urls, citation_numbers=citations, figure_renderer=render_figure,
                           max_nav_level=3 if research else 4)
        placed = {b.text for b in spec.blocks if b.kind == "figure"}
        for entry in manifest:
            if entry["section_key"] == spec.section_key and entry["chart_key"] not in placed:
                render_figure(entry["chart_key"])
    apply_report_typography(document)
    # Apply after shared typography so the small, light running title is retained.
    _running_header(document, data['title'])
    result = io.BytesIO()
    document.save(result)
    return result.getvalue(), f"{_safe_filename(data['title'])}.docx"


def _search_rows(data: dict[str, Any]) -> list[list[str]]:
    trace = data["search_trace"]
    entries = trace if isinstance(trace, list) else [trace] if isinstance(trace, dict) else []
    rows = []
    for index, item in enumerate(entries, start=1):
        if isinstance(item, dict):
            rows.append([str(item.get("query_id") or item.get("id") or f"Q{index}"), str(item.get("query") or item.get("keyword") or "检索式未返回"), str(item.get("gateway_status_code") or item.get("status_code") or "-"), str(item.get("gateway_code") or item.get("code") or "-"), str(item.get("paper_num_returned") or item.get("count") or item.get("total") or "-")])
    return rows or [[f"Q{index}", item.query, item.source_status, "-", "-"] for index, item in enumerate(data["queries"], start=1)]


def build_content_assessment_docx(state: ContentAssessmentState) -> tuple[bytes, str]:
    """Create the reader-facing report from one state snapshot.

    The main body contains decisions and representative evidence. Machine-level
    retrieval traces and the complete evidence pool are deliberately confined to
    appendices so the first pages remain useful to a human reader.
    """
    from app.services.export_service import ensure_snapshot
    ensure_snapshot(state)
    return _build_canonical_docx(state)
