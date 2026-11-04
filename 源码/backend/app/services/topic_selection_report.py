"""Decision-oriented topic-selection exporters sharing one report snapshot."""
from __future__ import annotations

from app.services.report_typography import (TYPOGRAPHY_VERSION, REPORT_TYPOGRAPHY_CSS, apply_report_typography, report_font_path)

import html
import base64
import io
import math
import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.agents.topic_selection.schemas import TopicSelectionState, TopicReportPlan
from app.agents.topic_selection.presentation import reader_report
from app.common.retrieval.links import evidence_url


TOPIC_CHART_RENDER_VERSION = "topic-chart-render-v16-readable-opportunities-" + TYPOGRAPHY_VERSION
_WRITER_EXPORT_LOCK = threading.RLock()
_WRITER_EXPORT_CACHE: OrderedDict[tuple[str, str, str, str], tuple[bytes, bytes]] = OrderedDict()


def _safe_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]', "_", value).strip()
    return value[:100] or "智能选题正式报告"


def _title(state: TopicSelectionState) -> str:
    return state.report_plan.report_title or f"{state.input_profile.title or state.raw_user_input[:40]}智能选题报告"


def _truncate_at_sentence(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    for delimiter in ("。", "！", "？", "；", "\n"):
        if delimiter in clipped:
            clipped = clipped.rsplit(delimiter, 1)[0] + delimiter
            break
    return clipped.strip()


def _bounded_export_state(state: TopicSelectionState) -> TopicSelectionState:
    """Freeze one canonical report snapshot for every output format."""
    frozen = state.model_copy(deep=True)
    if frozen.report_snapshot.result_data_snapshot:
        values = {key: value for key, value in frozen.report_snapshot.result_data_snapshot.items()
                  if key in TopicSelectionState.model_fields and key not in {"report_snapshot", "report_plan"}}
        frozen = TopicSelectionState.model_validate({**frozen.model_dump(), **values})
    if frozen.report_snapshot.report_plan_snapshot:
        frozen.report_plan = TopicReportPlan.model_validate(frozen.report_snapshot.report_plan_snapshot)
        frozen.versions.report_version = frozen.report_plan.report_version
        frozen.versions.template_version = frozen.report_plan.template_version
        return frozen
    gaps = {item.gap_id: item for item in frozen.gaps}
    restored_titles: dict[str, str] = {}
    for candidate in frozen.candidates:
        gap = gaps.get(candidate.gap_ids[0]) if candidate.gap_ids else None
        if not gap or len(gap.label) <= 60:
            continue
        # Old rules-based directions clipped the preserved gap at 60 characters.
        # Only restore an exact match, using existing task data rather than
        # rewriting a model-authored title or inventing a missing ending.
        prefix = f"探索方向：{candidate.research_object.strip(' ：:')}中的"
        old_title = prefix + gap.label[:60]
        if candidate.title == old_title:
            candidate.title = prefix + gap.label
            restored_titles[old_title] = candidate.title
    if restored_titles:
        # Match complete titles first so an already restored field does not
        # receive the missing suffix a second time.
        title_pattern = re.compile("|".join(
            re.escape(title) for title in sorted(
                set(restored_titles) | set(restored_titles.values()), key=len, reverse=True,
            )
        ))

        def restore(value: Any) -> Any:
            if isinstance(value, str):
                return title_pattern.sub(lambda match: restored_titles.get(match.group(0), match.group(0)), value)
            if isinstance(value, list):
                return [restore(item) for item in value]
            if isinstance(value, dict):
                return {key: restore(item) for key, item in value.items()}
            return value

        for field in ("sections", "section_specs", "table_specs", "chart_specs", "chart_manifest", "executive_summary"):
            setattr(frozen.report_plan, field, restore(getattr(frozen.report_plan, field)))
    for field in ("sections", "section_specs", "table_specs", "chart_specs", "chart_manifest", "executive_summary", "limitations", "cover_metadata"):
        setattr(frozen.report_plan, field, reader_report(getattr(frozen.report_plan, field)))
    frozen.report_plan.cover_metadata.setdefault("data_source", f"文献 {len(frozen.evidence_pool)} 篇")
    return frozen


def _blocks(section: dict[str, Any], spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    blocks = section.get("blocks") or (spec or {}).get("blocks") or []
    if blocks:
        return [item for item in blocks if isinstance(item, dict)]
    content = str(section.get("content") or section.get("summary") or section.get("abstract") or "本章节数据不足，保留章节并说明证据限制。")
    return [{"kind": "paragraph", "text": line.strip()} for line in content.splitlines() if line.strip()]


def _section_spec(state: TopicSelectionState, key: str) -> dict[str, Any]:
    return next((item for item in state.report_plan.section_specs if item.get("key") == key), {})


def _reference_lines(block: dict[str, Any]) -> list[str]:
    lines = []
    for item in block.get("items") or []:
        title = str(item.get('title', '')).replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
        url = evidence_url(item)
        title = f"[{title}](<{url}>)" if url else title
        detail = f"{title}。{item.get('authors', '')} {item.get('venue') or item.get('source') or 'NSTL'}，{item.get('year') or '年份未提供'}。"
        detail += _reference_identifier(item)
        lines.append(f"<a id=\"ref-{item.get('number', '')}\"></a> [{item.get('number', '')}] {detail}")
    return lines


def _reference_identifier(item: dict[str, Any]) -> str:
    if item.get("doi"):
        return f" DOI: {item['doi']}。"
    # NSTL identifiers are retained in the frozen evidence record and are
    # used to construct a detail-page link when necessary.  They are an
    # internal trace field, so keep them out of the reader-facing bibliography
    # when no DOI is available.
    return ""


_INTERNAL_EVIDENCE_RE = re.compile(r"(?<![A-Za-z0-9])(?:证据\s*)?E-[A-Za-z0-9_-]{6,}(?![A-Za-z0-9])")
_INTERNAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(?:CAND-[A-Za-z0-9_-]+|M-INPUT|T\d+|G\d+)(?![A-Za-z0-9])")


def _reader_visible(value: Any) -> str:
    """Remove pipeline identifiers from reader-facing text while preserving citations."""
    text = str(value or "")
    text = _INTERNAL_EVIDENCE_RE.sub("相关研究", text)
    text = _INTERNAL_TOKEN_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"[：:，；、]\s*([。；])", r"\1", text)
    return text.strip()


def _md_text(value: Any) -> str:
    return re.sub(r"\[(\d+)\]", r"[\1](#ref-\1)", _reader_visible(value))


def _html_text(value: Any) -> str:
    escaped = html.escape(_reader_visible(value))
    escaped = re.sub(r"\[(\d+)\]", r"<a href='#ref-\1'>[\1]</a>", escaped)
    return re.sub(r"(https://www\.nstl\.gov\.cn/paper_detail\.html\?id=[^\s；。，）)<>]+)", r"<a href='\1'>\1</a>", escaped).replace("\n", "<br>")


def _table_md(spec: dict[str, Any]) -> list[str]:
    columns = [_reader_visible(column) for column in (spec.get("columns") or [])]
    if not columns:
        return []
    lines = [f"### {spec.get('title', '')}", "| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in (spec.get("rows") or []):
        lines.append("| " + " | ".join(_reader_visible(row.get(column, "")) if isinstance(row, dict) else "" for column in columns) + " |")
    return lines + [""]


def _chart_rows(spec: dict[str, Any]) -> list[tuple[str, float]]:
    data = spec.get("data") or []
    if isinstance(data, dict):
        data = data.get("nstl") or data.get("nodes") or data.get("items") or []
    if not isinstance(data, list):
        return []
    rows: list[tuple[str, float]] = []
    for item in data[:12]:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("source") or item.get("title") or item.get("candidate_id") or item.get("year") or "item")
            value = item.get("count", item.get("value", item.get("total", 1)))
        else:
            label, value = str(item), 1
        try:
            rows.append((label[:80], max(0.0, float(value or 0))))
        except (TypeError, ValueError):
            rows.append((label[:80], 0.0))
    return rows


def _candidate_status_label(value: Any) -> str:
    return {
        "recommended": "正式推荐",
        "exploratory": "探索方向",
        "pending_evidence": "探索方向",
    }.get(str(value or ""), str(value or "待评估"))


def _chart_md(spec: dict[str, Any]) -> list[str]:
    lines = []
    data = spec.get("data") or {}
    kind = spec.get("chart_type")
    parts = _render_chart_parts(spec)
    for index, content in enumerate(parts, 1):
        suffix = f"（{index}）" if len(parts) > 1 else ""
        lines += ["", f"![{_reader_visible(spec.get('title', '图表'))}{suffix}](data:image/png;base64,{base64.b64encode(content).decode('ascii')})", ""]
        if suffix:
            lines += [f"图 {spec.get('figure_number', '')}{suffix} {_reader_visible(spec.get('title', ''))}", ""]
    lines.append(f"图 {spec.get('figure_number', '')} {_reader_visible(spec.get('title', ''))}")
    lines.append(f"> {_reader_visible(spec.get('source_note') or ('来源：本次锁定证据池；数据快照：' + str(spec.get('data_snapshot_version', ''))))}")
    if spec.get("caption"):
        lines.append(f"> {_reader_visible(spec['caption'])}")
    columns, rows = [], []
    if kind == "discipline" and isinstance(data, dict):
        columns = ["研究机会", "核心学科", "拓展学科", "可借鉴方法", "关联原因"]
        rows = [dict(zip(columns, (_reader_visible(row.get("opportunity")), _reader_visible(row.get("core")), _reader_visible(row.get("expansion")), _reader_visible(row.get("method")), _reader_visible(row.get("reason"))))) for row in data.get("rows", []) if row.get("opportunity")]
    elif kind == "radar" and isinstance(data, dict):
        dimensions = list(data.get("dimensions") or [])
        columns = ["候选题", *dimensions]
        rows = [{"候选题": _reader_visible(row.get("label") or "候选题"), **dict(zip(dimensions, row.get("values") or []))} for row in data.get("rows") or []]
    elif kind in {"scatter", "bubble"} and isinstance(data, list):
        columns = ["空白编号", "竞争热度", "空白空间", "样本量", "篇均被引", "状态"]
        rows = [{"空白编号": f"研究方向{index + 1}", "竞争热度": row.get("x", ""), "空白空间": row.get("y", ""), "样本量": row.get("size", ""), "篇均被引": row.get("color", ""), "状态": _reader_visible(row.get("status", ""))} for index, row in enumerate(data)]
    elif kind == "stacked_bar" and isinstance(data, dict):
        columns = ["子方向", *[str(year) for year in data.get("years") or []]]
        rows = [{"子方向": _reader_visible(row.get("label", "")), **{str(year): value for year, value in zip(data.get("years") or [], row.get("values") or [])}} for row in data.get("series") or []]
    elif kind == "network" and isinstance(data, dict):
        columns = ["主题", "研究边界"]
        nodes = data.get("nodes") or []
        by_id = {str(row.get("theme_id")): _reader_visible(row.get("label") or "主题") for row in nodes}
        rows = [{"主题": _reader_visible(row.get("label", "")), "研究边界": _reader_visible(row.get("boundary", ""))} for row in nodes]
        lines.append(f"> 研究根节点：{_reader_visible(data.get('display_root') or data.get('root') or '未提供')}")
        lines.extend(f"> 关系：{by_id.get(str(edge.get('source')), '相关主题')} → {by_id.get(str(edge.get('target')), '相关主题')}；{_reader_visible(edge.get('relation', ''))}" for edge in data.get("edges") or [])
    else:
        columns = ["项目", "文献数" if kind == "line" else "排序得分"]
        rows = [dict(zip(columns, (_reader_visible(label), value))) for label, value in _chart_rows(spec)]
    if rows:
        lines.extend(["", *_table_md({"title": "图表数据摘要", "columns": columns, "rows": rows})])
    if not _chart_has_data(spec):
        lines.extend(["", _chart_limitation(spec)])
    return lines + [""]


def _structured_markdown(state: TopicSelectionState) -> str:
    lines = [f"# {_title(state)}", "", f"生成方式：{state.report_plan.cover_metadata.get('generation_method', '智能选题九阶段流程')}", f"数据源：{state.report_plan.cover_metadata.get('data_source', '')}", f"样本量：{state.report_plan.cover_metadata.get('sample_size', len(state.evidence_pool))}", f"生成日期：{state.report_plan.cover_metadata.get('generated_at', '')}", f"报告版本：{state.versions.report_version}", f"模板版本：{state.report_plan.template_version or state.versions.template_version}", f"证据截止时间：{state.report_plan.cover_metadata.get('evidence_cutoff_at', '未指定')}", ""]
    if state.report_plan.template_version == "TopicReportV7":
        lines = [f"# {_title(state)}", "", f"数据源：{state.report_plan.cover_metadata.get('data_source', '')}",
                 f"生成日期：{state.report_plan.cover_metadata.get('generated_at', '')}", ""]
    charts = {str(item.get("key")): item for item in state.report_plan.chart_specs}
    tables = {str(item.get("key")): item for item in state.report_plan.table_specs}
    for section in state.report_plan.sections:
        key = str(section.get("key")); spec = _section_spec(state, key); lines += [f'<a id="{key.lower().replace("_", "-")}"></a>', f"## {section.get('title', '')}", ""]
        rendered_refs: set[str] = set()
        for block in _blocks(section, spec):
            kind = block.get("kind", "paragraph")
            if kind in {"paragraph", "lead"}:
                lines.extend([_md_text(block.get("text", "")), ""])
            elif kind == "heading":
                level = max(3, min(6, int(block.get("level") or 2) + 1))
                lines.extend([f"{'#' * level} {_md_text(block.get('text', ''))}", ""])
            elif kind in {"bullet_list", "numbered_list"}:
                for index, item in enumerate(block.get("items") or [], start=1):
                    lines.append(f"{index}. {_md_text(item)}" if kind == "numbered_list" else f"- {_md_text(item)}")
                lines.append("")
            elif kind == "table_ref":
                rendered_refs.add(str(block.get("key")))
                lines.extend(_table_md(tables.get(str(block.get("key")), {})))
            elif kind == "figure_ref":
                rendered_refs.add(str(block.get("key")))
                chart = charts.get(str(block.get("key")))
                if chart: lines.extend(_chart_md(chart))
            elif kind == "candidate_detail":
                lines.extend([f"### 候选题 {block.get('index', '')}：{block.get('title', '')}", "", *([f"状态：{_candidate_status_label(block.get('status'))}", ""] if block.get("show_status", True) else [])])
                for field in block.get("fields") or []: lines.extend([f"**{field.get('label', '')}**：{_md_text(field.get('value', ''))}", ""])
            elif kind == "reference_list":
                lines.extend(_reference_lines(block)); lines.append("")
        for table_key in spec.get("table_keys", []):
            if table_key not in rendered_refs:
                lines.extend(_table_md(tables.get(str(table_key), {})))
        for chart_key in spec.get("chart_keys", []):
            if chart_key not in rendered_refs and charts.get(str(chart_key)):
                lines.extend(_chart_md(charts[str(chart_key)]))
    return "\n".join(lines)


def build_topic_markdown(state: TopicSelectionState) -> tuple[bytes, str]:
    render_state = _bounded_export_state(state)
    return _structured_markdown(render_state).encode("utf-8"), f"{_safe_filename(_title(render_state))}.md"


def _table_widths(spec: dict[str, Any], total: float = 6.8) -> list[float]:
    """Share column geometry between HTML and Writer, including old snapshots.

    Narrow stored widths are hints: reserve enough room for short headings
    before assigning the remaining space to narrative columns.
    """
    columns = list(spec.get("columns") or [])
    if not columns:
        return []
    font_size = float(spec.get("font_size") or 9.5)
    minimums = [max(.72, (min(8, sum(1 if ord(c) > 255 else .55 for c in str(label))) * font_size + 18) / 72)
                for label in columns]
    if sum(minimums) >= total:
        return [value * total / sum(minimums) for value in minimums]
    try:
        weights = [float(value) for value in (spec.get("column_widths") or [])]
    except (TypeError, ValueError):
        weights = []
    if len(weights) != len(columns) or any(not math.isfinite(value) or value <= 0 for value in weights):
        weights = [max(1., min(36., max((len(str(row.get(label, ""))) for row in spec.get("rows", []) if isinstance(row, dict)), default=len(str(label)))))
                   for label in columns]
    scaled = [value * total / sum(weights) for value in weights]
    extras = [max(0., value - minimum) for value, minimum in zip(scaled, minimums)]
    remaining = total - sum(minimums)
    return [minimum + remaining * extra / sum(extras) for minimum, extra in zip(minimums, extras)]


def _html_table(spec: dict[str, Any]) -> str:
    source_columns = list(spec.get("columns") or [])
    columns = [_reader_visible(column) for column in source_columns]
    if not columns:
        return ""
    widths = _table_widths(spec)
    grid = "<colgroup>" + "".join(f"<col style='width:{width / sum(widths) * 100:.3f}%'>" for width in widths) + "</colgroup>"
    head = "".join("<th scope='col'" + (' class="short-heading"' if len(column) <= 6 else '') + f">{html.escape(column)}</th>" for column in columns)
    rows = "".join(
        "<tr>" + "".join(
            f"<td>{_html_text(row.get(source_column, '') if isinstance(row, dict) else '')}</td>"
            for source_column in source_columns
        ) + "</tr>"
        for row in (spec.get("rows") or [])
    )
    empty = "<tr><td class='empty-cell' colspan='%d'>当前快照没有可展示记录。</td></tr>" % len(columns) if not rows else rows
    return f"<div class='table-wrap'><h3>{html.escape(_reader_visible(spec.get('title', '')))}</h3><table>{grid}<thead><tr>{head}</tr></thead><tbody>{empty}</tbody></table></div>"


def _html_chart(spec: dict[str, Any]) -> str:
    parts = _render_chart_parts(spec)
    caption = f"图 {spec.get('figure_number', '')} {_reader_visible(spec.get('title', ''))}"
    source = _reader_visible(spec.get("source_note") or f"来源：本次锁定证据池；数据快照：{spec.get('data_snapshot_version', '')}")
    if not parts:
        reason = html.escape(_chart_limitation(spec))
        return f"<p class='chart-empty'><b>{html.escape(caption)}：</b>{reason}<br>{html.escape(source)}<br>{html.escape(str(spec.get('caption') or ''))}</p>"
    alt = str(spec.get("alt_text") or caption)
    note = f"<small>{html.escape(_reader_visible(spec.get('caption')))}</small>" if spec.get("caption") else ""
    return "".join(
        f"<figure><img src='data:image/png;base64,{base64.b64encode(content).decode('ascii')}' alt='{html.escape(alt)}'>"
        f"<figcaption><span class='figure-title'>{html.escape(caption + (f'（{index}）' if len(parts) > 1 else ''))}</span>"
        f"<span>{html.escape(source)}</span>{note}</figcaption></figure>"
        for index, content in enumerate(parts, 1)
    )


def _html_paragraph(text: Any, kind: str = "paragraph") -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    linked = _html_text(value)
    return f"<p class='{html.escape(kind)}'>{linked}</p>"


def _html_block(block: dict[str, Any], tables: dict[str, dict[str, Any]], charts: dict[str, dict[str, Any]]) -> str:
    kind = str(block.get("kind") or "paragraph")
    if kind in {"paragraph", "lead"}:
        return _html_paragraph(block.get("text"), "lead" if kind == "lead" else "paragraph")
    if kind == "heading":
        level = max(3, min(6, int(block.get("level") or 2) + 1))
        return f"<h{level}>{html.escape(_reader_visible(block.get('text') or ''))}</h{level}>"
    if kind in {"bullet_list", "numbered_list"}:
        tag = "ol" if kind == "numbered_list" else "ul"
        items = "".join(f"<li>{_html_text(item)}</li>" for item in (block.get("items") or []))
        return f"<{tag}>{items}</{tag}>" if items else ""
    if kind == "table_ref":
        return _html_table(tables.get(str(block.get("key")), {}))
    if kind == "figure_ref":
        chart = charts.get(str(block.get("key")))
        return _html_chart(chart) if chart else ""
    if kind == "candidate_detail":
        fields = "".join(
            f"<p><b>{html.escape(str(field.get('label', '')))}：</b>{_html_text(field.get('value', ''))}</p>"
            for field in (block.get("fields") or [])
        )
        status = html.escape(_candidate_status_label(block.get("status"))) if block.get("show_status", True) else ""
        title = html.escape(_reader_visible(block.get("title") or "候选题"))
        status_html = f"<p class='candidate-status'>状态：{status}</p>" if status else ""
        return f"<div class='candidate'><h3>候选题 {html.escape(str(block.get('index', '')))}：{title}</h3>{status_html}{fields}</div>"
    if kind == "reference_list":
        items = []
        for item in block.get("items") or []:
            label = html.escape(str(item.get("number", "")))
            title = html.escape(str(item.get('title', '')))
            url = evidence_url(item)
            if url:
                title = f"<a href='{html.escape(url, quote=True)}' target='_blank' rel='noopener'>{title}</a>"
            detail = title + html.escape(f"。{item.get('authors', '')} {item.get('venue') or 'NSTL'}，{item.get('year') or '年份未提供'}。" + _reference_identifier(item))
            items.append(f"<li id='ref-{label}'><span class='ref-number'>[{label}]</span> {detail}</li>")
        return f"<ol class='references'>{''.join(items)}</ol>" if items else _html_paragraph("当前没有可引用文献。", "limitation")
    return _html_paragraph(block.get("text") or block.get("value"))


_REPORT_LAYOUT_CSS = """
body {font-size:15px; line-height:1.8}
h2,h3,h4 {break-after:avoid; page-break-after:avoid}
p,li {orphans:2; widows:2}
.table-wrap {margin:20px 0 24px}
.table-wrap h3 {margin:0 0 10px}
table {table-layout:fixed; font-size:13px; line-height:1.65}
th,td {padding:9px 10px; vertical-align:middle; overflow-wrap:anywhere; word-break:normal}
th.short-heading {white-space:nowrap; overflow-wrap:normal}
thead {display:table-header-group}
tr {break-inside:avoid; page-break-inside:avoid}
tbody tr:nth-child(even) {background:#f6f8fb}
.candidate p {text-indent:0}
.references {list-style:none; padding-left:2.5em}
.references li {padding-left:0; text-indent:-2.5em; overflow-wrap:anywhere}
@media print {
  @page {size:Letter; margin:.68in .85in}
  body {font-size:10.5pt; line-height:1.35}
  .report-head,section {padding:12pt 0; max-width:none}
  table {min-width:0; font-size:9.5pt}
  .table-wrap {overflow:visible}
  .report-head {border-top-width:3pt}
  h2 {font-size:16pt; margin:0 0 10pt}
  h3 {font-size:12pt}
}
"""


def build_topic_html(state: TopicSelectionState) -> tuple[bytes, str]:
    state = _bounded_export_state(state)
    charts = {str(item.get("key")): item for item in state.report_plan.chart_specs}
    tables = {str(item.get("key")): item for item in state.report_plan.table_specs}
    metadata = state.report_plan.cover_metadata
    cover_meta = "".join(
        f"<p><b>{html.escape(label)}：</b>{html.escape(_reader_visible(value or '未提供'))}</p>"
        for label, value in [
            ("生成方式", metadata.get("generation_method", "智能选题九阶段流程")),
            ("数据源", metadata.get("data_source", f"文献 {len(state.evidence_pool)} 篇")),
            ("样本量", metadata.get("sample_size", len(state.evidence_pool))),
            ("生成日期", metadata.get("generated_at", "")),
            ("报告版本", state.versions.report_version),
            ("模板版本", state.report_plan.template_version or state.versions.template_version),
        ] if state.report_plan.template_version != "TopicReportV7" or label in {"数据源", "生成日期"}
    )
    sections = [f"<header class='report-head'><h1>{html.escape(_title(state))}</h1><div class='metadata'>{cover_meta}</div></header>"]
    for section in state.report_plan.sections:
        key = str(section.get("key")); spec = _section_spec(state, key)
        fragments = [f"<section id='{html.escape(key)}'><h2>{html.escape(str(section.get('title', '')))}</h2>"]
        rendered_refs: set[str] = set()
        for block in _blocks(section, spec):
            block_key = str(block.get("key") or "")
            if block.get("kind") in {"table_ref", "figure_ref"}:
                rendered_refs.add(block_key)
            fragments.append(_html_block(block, tables, charts))
        for chart_key in spec.get("chart_keys", []):
            if chart_key not in rendered_refs and charts.get(chart_key): fragments.append(_html_chart(charts[chart_key]))
        for table_key in spec.get("table_keys", []):
            if table_key not in rendered_refs: fragments.append(_html_table(tables.get(table_key, {})))
        fragments.append("</section>"); sections.append("".join(fragments))
    document = f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{html.escape(_title(state))}</title><style>@page{{size:Letter;margin:1in 1.25in}}:root{{--blue:#4F81BD;--navy:#17365D;--line:#D9E2F3;--muted:#687585;--ink:#243447}}*{{box-sizing:border-box}}body{{font-family:'Microsoft YaHei','Noto Sans CJK SC',Arial,sans-serif;max-width:816px;margin:32px auto;color:var(--ink);line-height:1.75;background:#f7f9fc}}.report-head,section{{background:white;padding:28px 54px;margin:0 auto;max-width:816px}}.report-head{{padding-bottom:12px;border-top:6px solid var(--blue)}}.report-head h1{{color:var(--navy);font-size:28px;line-height:1.4;text-align:center;margin:10px 0 22px}}.metadata{{display:grid;grid-template-columns:1fr 1fr;gap:4px 20px;border-bottom:1px solid var(--line);padding-bottom:16px}}.metadata p{{font-size:12px;text-indent:0;margin:2px 0}}h2{{color:var(--blue);font-size:22px;border-bottom:2px solid var(--line);padding-bottom:8px;margin:0 0 22px}}h3{{color:var(--navy);font-size:16px;margin:22px 0 10px}}h4{{color:var(--blue);font-size:14px;margin:18px 0 8px}}p{{margin:0 0 12px;text-indent:2em}}p.lead{{text-indent:0;font-weight:600;color:#324b66;border-left:4px solid var(--blue);padding:6px 0 6px 14px}}p.limitation,.chart-empty{{text-indent:0;color:#687585;border-left:4px solid #E0A458;padding:8px 0 8px 14px}}ul,ol{{padding-left:2em}}li{{margin:5px 0}}a{{color:#2F75B5}}.table-wrap{{overflow-x:auto;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:12px;line-height:1.5;min-width:560px}}td,th{{border:1px solid #C9D4E2;padding:8px 10px;text-align:left;vertical-align:top;word-break:break-word}}th{{background:#DCEAF7;color:#17365D;font-weight:700}}.empty-cell{{text-align:center;color:var(--muted)}}figure{{margin:26px 0;break-inside:avoid;text-align:center}}figure img{{display:block;max-width:100%;height:auto;margin:0 auto}}figcaption{{margin-top:8px;text-align:center;font-size:12px;color:#687585}}figcaption b,figcaption span,figcaption small{{display:block}}.candidate{{border-top:1px solid var(--line);padding-top:10px;margin-top:24px}}.candidate h3{{color:var(--blue)}}.candidate-status{{text-indent:0;color:#687585;font-size:13px}}.references{{padding-left:2.5em}}.references li{{padding-left:.3em;text-indent:-.3em}}@media(max-width:700px){{body{{margin:0;background:white;font-size:15px}}.report-head,section{{padding:22px 18px}}.metadata{{grid-template-columns:1fr}}.report-head h1{{font-size:24px}}table{{min-width:620px}}.table-wrap,figure{{overflow-x:auto}}figure img{{min-width:620px;max-width:none}}}}@media print{{body{{background:white;margin:0}}.report-head,section{{margin:0;max-width:none}}}}{_REPORT_LAYOUT_CSS}{REPORT_TYPOGRAPHY_CSS}</style></head><body>{''.join(sections)}</body></html>"
    return document.encode("utf-8"), f"{_safe_filename(_title(state))}.html"


def _font(size: int, bold: bool = False) -> Any:
    from PIL import ImageFont
    configured = os.getenv("REPORT_CJK_BOLD_FONT" if bold else "REPORT_CJK_FONT", "").strip()
    paths = ([report_font_path(bold)] if report_font_path(bold) else []) + ([configured] if configured else []) + [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size, index=0)
        except OSError:
            continue
    return None


def _chart_has_data(spec: dict[str, Any]) -> bool:
    if str(spec.get("data_status") or "") in {"insufficient", "unavailable"}:
        return False
    data = spec.get("data")
    chart_type = str(spec.get("chart_type") or "bar")
    minimum = max(1, int(spec.get("min_data_points") or 1))
    if chart_type == "discipline":
        return isinstance(data, dict) and bool(data.get("rows"))
    if chart_type == "network":
        return bool(isinstance(data, dict) and len(data.get("nodes") or []) >= minimum)
    if chart_type == "line":
        rows = data.get("nstl") or [] if isinstance(data, dict) else []
        return len({item.get("year") for item in rows if item.get("year")}) >= max(2, minimum) and any(_finite(item.get("count")) > 0 for item in rows)
    if chart_type == "stacked_bar":
        if not isinstance(data, dict) or len(data.get("years") or []) < max(2, minimum):
            return False
        return any(any(_finite(value) > 0 for value in row.get("values") or []) for row in data.get("series") or [])
    if chart_type == "radar":
        if not isinstance(data, dict):
            return False
        count = len(data.get("dimensions") or [])
        maximum = _finite(data.get("max_value")) or 100
        return count >= max(3, minimum) and any(len(row.get("values") or []) == count and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= maximum for value in row["values"]) for row in data.get("rows") or [])
    if chart_type in {"scatter", "bubble"}:
        if not isinstance(data, list) or len(data) < max(2, minimum):
            return False
        coordinates = {(_finite(row.get("x")), _finite(row.get("y"))) for row in data}
        return len(coordinates) >= max(2, minimum)
    if chart_type == "bar":
        return isinstance(data, list) and len(data) >= minimum and any(value > 0 for _, value in _chart_rows(spec))
    # Historical unsupported matrices remain readable as data, never black placeholders.
    return False


def _finite(value: Any) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _chart_limitation(spec: dict[str, Any]) -> str:
    if not _chart_has_data(spec):
        return str(spec.get("empty_reason") or spec.get("limitation") or "数据不足或图表类型不受支持，保留文字及数据摘要。")
    if not all(_font(size, bold) for size, bold in ((28, True), (24, False), (22, False))):
        return "中文字体不可用，图表已降级为文字；请配置 REPORT_CJK_FONT / REPORT_CJK_BOLD_FONT。"
    return str(spec.get("limitation") or "图表暂时无法渲染，保留原始数据与正文结论。")


def _render_chart_png(spec: dict[str, Any]) -> bytes | None:
    if not _chart_has_data(spec):
        return None
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(image)
    font = _font(28, True)
    small = _font(24)
    tiny = _font(22)
    if not all((font, small, tiny)):
        return None
    navy, blue, teal, orange, purple, grid, muted = "#17365D", "#4F81BD", "#2A9D8F", "#E79D43", "#8064A2", "#D9E2F3", "#687585"
    chart_type = str(spec.get("chart_type") or "bar")
    data = spec.get("data") or {}
    left, top, right, bottom = 185, 120, 1490, 735

    def wrap(value: Any, limit: int = 12) -> list[str]:
        text = _reader_visible(value or "未提供")
        if len(text) > limit * 2:
            text = text[:limit * 2 - 1] + "…"
        return [text[i:i + limit] for i in range(0, len(text), limit)] or ["未提供"]

    def axis(x_label: str = "", y_label: str = "") -> None:
        draw.line((left, top, left, bottom), fill=navy, width=3)
        draw.line((left, bottom, right, bottom), fill=navy, width=3)
        if x_label:
            draw.text(((left + right) / 2, bottom + 68), x_label, fill=muted, font=small, anchor="mm")
        if y_label:
            draw.text((48, (top + bottom) / 2), y_label, fill=muted, font=small, anchor="mm")

    if chart_type == "discipline":
        draw.text((800, 55), "核心主题：" + str(data.get("root", ""))[:46], fill=navy, font=small, anchor="mm")
        for column, label in enumerate(("核心学科", "拓展学科", "可借鉴方法")):
            draw.text((300 + column * 500, 130), label, fill=navy, font=small, anchor="mm")
        for index, row in enumerate(data.get("rows", [])[:3]):
            y = 245 + index * 195
            grounded = any(item.get("verified") for item in row.get("method_evidence", []))
            for column, key in enumerate(("core", "expansion", "method")):
                x = 300 + column * 500
                draw.rounded_rectangle((x - 195, y - 48, x + 195, y + 48), radius=15, fill="#E8F3F8", outline=blue, width=2)
                draw.multiline_text((x, y), "\n".join(wrap(row.get(key), 15)), fill=navy, font=small, anchor="mm", align="center")
                if column < 2:
                    start, end = x + 197, x + 303
                    if grounded:
                        draw.line((start, y, end, y), fill=blue, width=3)
                    else:
                        for dx in range(start, end, 18):
                            draw.line((dx, y, min(dx + 10, end), y), fill=muted, width=2)
                    relation = "主题关联" if row.get("association_kind") == "theme_association" else "方法关联" if column else "学科关联"
                    draw.text((x + 250, y - 28), relation, fill=muted, font=_font(17), anchor="mm")
            draw.text((800, y + 70), str(row.get("status") or "待验证线索"), fill=muted, font=tiny, anchor="mm")
        draw.text((800, 852), "实线：方法原文可核验；虚线：待验证线索。所有迁移效果均须进一步验证。", fill=muted, font=tiny, anchor="mm")
    elif chart_type == "network":
        nodes = list(data.get("nodes") or [])
        by_id = {str(n.get("theme_id")): n for n in nodes}
        ordered, seen = [], set()
        def visit(node, depth):
            nid = str(node.get("theme_id"))
            if nid in seen: return
            seen.add(nid); ordered.append((node, depth))
            for child in nodes:
                if str(child.get("parent_id")) == nid: visit(child, depth + 1)
        for node in nodes:
            if str(node.get("parent_id")) not in by_id or node.get("parent_id") == node.get("theme_id"): visit(node, 0)
        for node in nodes: visit(node, 0)
        height = max(360, 170 + len(ordered) * 108)
        if height != 900:
            image = Image.new("RGB", (1600, height), "white"); draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((90, 30, 1510, 105), radius=12, fill="#E8F3F8", outline=blue, width=2)
        draw.text((120, 55), str(data.get("display_root") or data.get("root") or "研究问题")[:48], fill=navy, font=small)
        for index, (node, depth) in enumerate(ordered):
            y = 140 + index * 108; x = 130 + min(depth, 6) * 75
            draw.line((x - 30, y + 33, x, y + 33), fill=blue, width=2)
            draw.rounded_rectangle((x, y, 1490, y + 94), radius=10, fill="#F0F5FA", outline=grid, width=2)
            label = str(node.get("display_label") or node.get("label") or "主题")
            draw.multiline_text((x + 18, y + 12), "\n".join(label[i:i+48] for i in range(0,len(label),48)), fill=navy, font=tiny, spacing=3)
            draw.text((x + 18, y + 66), "、".join(node.get("discipline_tags") or []) or "学科待核验", fill=muted, font=_font(18))
    elif chart_type == "line":
        rows = list((data or {}).get("nstl") or [])
        if len(rows) < 2:
            return None
        values = [max(0.0, float(item.get("count") or 0)) for item in rows]
        maximum = max(values) or 1
        axis("年份", "文献数")
        for tick in range(5):
            value = maximum * tick / 4
            y = bottom - (bottom - top) * tick / 4
            draw.line((left, y, right, y), fill=grid, width=1)
            draw.text((left - 16, y), f"{value:g}", fill=muted, font=tiny, anchor="rm")
        points = []
        for index, (item, value) in enumerate(zip(rows, values)):
            x = left + (right - left) * index / max(1, len(rows) - 1)
            y = bottom - (bottom - top) * value / maximum
            points.append((x, y))
            draw.text((x, bottom + 20), str(item.get("year") or "")[-4:], fill=muted, font=tiny, anchor="ma")
            draw.text((x, y - 22), f"{value:g}", fill=navy, font=tiny, anchor="ms")
        draw.line(points, fill=blue, width=6, joint="curve")
        for x, y in points:
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=blue, outline="white", width=3)
    elif chart_type == "stacked_bar":
        chart_years = list(data.get("years") or [])
        series = list(data.get("series") or [])
        if len(chart_years) < 2 or not series:
            return None
        def full_wrap(value: Any, limit: int = 14) -> list[str]:
            text = _reader_visible(value or "子方向")
            return [text[index:index + limit] for index in range(0, len(text), limit)] or ["子方向"]
        legend_rows = [full_wrap(row.get("label") or "子方向") for row in series]
        legend_height = max(900, 180 + sum(max(1, len(lines)) * 32 + 18 for lines in legend_rows))
        image = Image.new("RGB", (1900, legend_height), "white")
        draw = ImageDraw.Draw(image)
        right = 1190
        bottom = min(735, legend_height - 165)
        totals = [sum(_finite(row.get("values", [])[index]) if index < len(row.get("values") or []) else 0 for row in series) for index in range(len(chart_years))]
        maximum = max(totals) or 1
        axis("年份", "文献数")
        for tick in range(5):
            value = maximum * tick / 4
            y = bottom - (bottom - top) * tick / 4
            draw.line((left, y, right, y), fill=grid, width=1)
            draw.text((left - 16, y), f"{value:g}", fill=muted, font=tiny, anchor="rm")
        colors = (blue, teal, orange, purple, "#C0504D", "#7F8C8D", "#5B9BD5", "#70AD47")
        band = (right - left) / max(1, len(chart_years))
        width = min(86, band * .62)
        for year_index, year in enumerate(chart_years):
            x = left + (year_index + .5) * band
            cursor = bottom
            for series_index, row in enumerate(series):
                values = row.get("values") or []
                value = _finite(values[year_index]) if year_index < len(values) else 0
                height = (bottom - top) * value / maximum
                if height > 0:
                    draw.rectangle((x - width / 2, cursor - height, x + width / 2, cursor), fill=colors[series_index % len(colors)])
                    cursor -= height
            draw.text((x, bottom + 20), str(year)[-4:], fill=muted, font=tiny, anchor="ma")
        legend_x, legend_y = 1300, 145
        draw.text((legend_x, legend_y - 38), "研究子方向", fill=navy, font=small)
        for series_index, (row, lines) in enumerate(zip(series, legend_rows)):
            draw.rectangle((legend_x, legend_y + 4, legend_x + 24, legend_y + 24), fill=colors[series_index % len(colors)])
            for line_index, line in enumerate(lines):
                draw.text((legend_x + 36, legend_y + line_index * 32), line, fill=navy, font=tiny)
            legend_y += max(1, len(lines)) * 32 + 18
    elif chart_type == "bar":
        rows = _chart_rows(spec)[:10]
        if not rows:
            return None
        maximum = max(value for _, value in rows) or 1
        logarithmic = str(spec.get("data_basis") or "") == "citation_count"
        left = 440
        axis("被引次数（对数刻度）" if logarithmic else "排序得分")
        for tick in range(5):
            x = left + (right - left - 100) * tick / 4
            draw.line((x, top, x, bottom), fill=grid, width=1)
            tick_value = math.expm1(math.log1p(maximum) * tick / 4) if logarithmic else maximum * tick / 4
            draw.text((x, bottom + 24), f"{tick_value:.0f}" if logarithmic else f"{tick_value:.2f}", fill=muted, font=tiny, anchor="ma")
        band = (bottom - top) / max(1, len(rows))
        for index, (label, value) in enumerate(rows):
            y = top + (index + .5) * band
            ratio = math.log1p(value) / math.log1p(maximum) if logarithmic and maximum > 0 else value / maximum
            width = (right - left - 100) * ratio
            number = data[index].get("citation_number") or index + 1
            draw.multiline_text((left - 20, y), "\n".join(wrap(f"[{number}] {_reader_visible(label)}", 17)), fill=navy, font=tiny, anchor="rm", align="right")
            if width > 0:
                draw.rounded_rectangle((left, y - 14, left + width, y + 14), radius=min(5, width / 2), fill=(blue if index % 2 == 0 else teal))
            draw.text((left + width + 12, y), f"{value:g}", fill=navy, font=tiny, anchor="lm")
    elif chart_type in {"scatter", "bubble"}:
        rows = list(data or [])[:20]
        if len(rows) < 2:
            return None
        max_x = max(1, math.ceil(max(_finite(item.get("x")) for item in rows) * 1.2))
        max_y = max(0.1, max(_finite(item.get("y")) for item in rows) * 1.2)
        axis("竞争热度", "空白空间")
        for tick in range(5):
            x = left + (right - left) * tick / 4
            y = bottom - (bottom - top) * tick / 4
            draw.line((x, top, x, bottom), fill=grid, width=1)
            draw.line((left, y, right, y), fill=grid, width=1)
            draw.text((x, bottom + 24), f"{max_x * tick / 4:g}", fill=muted, font=tiny, anchor="ma")
            draw.text((left - 16, y), f"{max_y * tick / 4:.2f}", fill=muted, font=tiny, anchor="rm")
        for index, item in enumerate(rows):
            x_value, y_value = float(item.get("x") or 0), float(item.get("y") or 0)
            x = left + (right - left) * x_value / max_x
            y = bottom - (bottom - top) * y_value / max_y
            radius = 12 + min(32, math.sqrt(max(1, _finite(item.get("size")))) * 7)
            cited = min(1.0, _finite(item.get("color")) / max(1.0, max(_finite(row.get("color")) for row in rows)))
            fill_color = (int(225 - 90 * cited), int(237 - 70 * cited), int(247 - 25 * cited)) if chart_type == "bubble" else (231, 157, 67)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill_color, outline=blue, width=3)
            draw.text((x, y), _reader_visible(item.get("display_label") or item.get("label") or f"研究方向{index + 1}"), fill=navy, font=tiny, anchor="mm")
    elif chart_type == "radar":
        dimensions = [str(item) for item in (data.get("dimensions") or [])]
        maximum = _finite(data.get("max_value")) or 100
        count = len(dimensions)
        rows = [row for row in list(data.get("rows") or [])[:5]
                if len(row.get("values") or []) == count
                and all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= maximum
                        for value in row["values"])]
        if len(dimensions) < 3 or not rows:
            return None
        legend_font = _font(32)
        score_font = _font(28)
        if not legend_font or not score_font:
            return None

        def full_lines(value: Any, text_font: Any, width: int) -> list[str]:
            # Measure pixels rather than counting characters: Chinese and Latin
            # titles need different widths. Never discard any part of a title.
            lines: list[str] = []
            for paragraph in str(value or "未提供").split("\n"):
                line = ""
                for character in paragraph:
                    if line and draw.textlength(line + character, font=text_font) > width:
                        lines.append(line)
                        line = ""
                    line += character
                lines.append(line)
            return lines

        legend_rows = [full_lines(_reader_visible(row.get('label') or '候选题'), legend_font, 1380)
                       for row in rows]
        legend_start, line_height, row_gap = 810, 43, 22
        height = legend_start + sum(len(lines) * line_height + row_gap for lines in legend_rows) + 40
        image = Image.new("RGB", (1600, height), "white")
        draw = ImageDraw.Draw(image)
        groups: dict[tuple[float, ...], list[str]] = {}
        for row in rows:
            groups.setdefault(tuple(row["values"]), []).append(str(row.get("candidate_id") or "候选题"))
        colors = (blue, teal, orange, purple, "#C0504D")
        group_colors = {values: colors[index % len(colors)] for index, values in enumerate(groups)}
        center = (535, 365); radius = 235
        angles = [-math.pi / 2 + 2 * math.pi * i / count for i in range(count)]
        for ring in (0.25, 0.5, 0.75, 1.0):
            points = [(center[0] + radius * ring * math.cos(angle), center[1] + radius * ring * math.sin(angle)) for angle in angles]
            draw.polygon(points, outline=grid)
            draw.text((center[0] + 10, center[1] - radius * ring), f"{ring * maximum:g}", fill=muted, font=score_font)
        for index, (angle, label) in enumerate(zip(angles, dimensions)):
            x2, y2 = center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)
            draw.line((*center, x2, y2), fill=grid, width=2)
            draw.multiline_text((center[0] + (radius + 58) * math.cos(angle), center[1] + (radius + 58) * math.sin(angle)), "\n".join(full_lines(label, legend_font, 250)), fill=navy, font=legend_font, anchor="mm", align="center")
        for values, candidate_ids in groups.items():
            points = [(center[0] + radius * min(maximum, max(0, float(value or 0))) / maximum * math.cos(angle), center[1] + radius * min(maximum, max(0, float(value or 0))) / maximum * math.sin(angle)) for angle, value in zip(angles, values)]
            color = group_colors[values]
            draw.line(points + [points[0]], fill=color, width=4, joint="curve")
            for x, y in points:
                draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
        score_x, score_width, score_top = 930, 620, 110
        draw.text((score_x, score_top), f"评分对照（满分 {maximum:g}）", fill=navy, font=font)
        score_headers = full_lines(" / ".join(dimensions), score_font, score_width)
        header_text = "\n".join(score_headers)
        draw.multiline_text((score_x, score_top + 50), header_text, fill=muted, font=score_font, spacing=7)
        score_y = score_top + 60 + len(score_headers) * 36
        for row in rows:
            color = group_colors[tuple(row["values"])]
            draw.line((score_x, score_y + 20, score_x + 36, score_y + 20), fill=color, width=4)
            values_text = " / ".join(f"{value:g}" for value in row["values"])
            draw.text((score_x + 52, score_y), f"{row.get('candidate_id') or '候选题'}   {values_text}", fill=color, font=legend_font)
            score_y += 57
        overlap_text = "；".join("、".join(ids) + "评分相同，曲线重合" for ids in groups.values() if len(ids) > 1)
        if overlap_text:
            draw.multiline_text((score_x, score_y + 20), "\n".join(full_lines(overlap_text + "。", score_font, score_width)), fill=muted, font=score_font, spacing=8)
        draw.line((80, 715, 1520, 715), fill=grid, width=2)
        draw.text((80, 745), "候选题完整标题", fill=navy, font=font)
        legend_y = legend_start
        for row, lines in zip(rows, legend_rows):
            color = group_colors[tuple(row["values"])]
            draw.line((80, legend_y + 20, 116, legend_y + 20), fill=color, width=5)
            # One drawing call per line keeps the measured row spacing exact.
            for line in lines:
                draw.text((140, legend_y), line, fill=color, font=legend_font)
                legend_y += line_height
            legend_y += row_gap
    else:
        return None
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True, dpi=(180, 180))
    return output.getvalue()


def _render_chart_parts(spec: dict[str, Any]) -> list[bytes]:
    if not _chart_has_data(spec):
        return []
    if spec.get("chart_version") in {"chart-v10", "chart-v11"}:
        from app.services.research_graph_rendering import topic_opportunity_parts, topic_tree_parts
        if spec.get("key") == "discipline_network":
            return topic_opportunity_parts((spec.get("data") or {}).get("rows") or [], concise=spec.get("chart_version") == "chart-v11")
        if spec.get("key") == "theme_network":
            return topic_tree_parts(spec.get("data") or {})
    content = _render_chart_png(spec)
    return [content] if content else []


def _charts(state: TopicSelectionState, directory: Path) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {}
    for index, spec in enumerate(state.report_plan.chart_specs or [], start=1):
        for part_index, content in enumerate(_render_chart_parts(spec), start=1):
            path = directory / f"topic-chart-{index}-{part_index}.png"
            path.write_bytes(content)
            paths.setdefault(str(spec.get("key")), []).append(path)
    return paths


def _set_black(run: Any) -> None:
    from docx.shared import RGBColor
    run.font.color.rgb = RGBColor(36, 52, 71)


def _set_brand(run: Any) -> None:
    from docx.shared import RGBColor
    run.font.color.rgb = RGBColor(79, 129, 189)


def _build_topic_docx_source(state: TopicSelectionState) -> tuple[bytes, str]:
    state = _bounded_export_state(state)
    try:
        from docx import Document
        from docx.enum.style import WD_STYLE_TYPE
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise RuntimeError("python-docx 未安装，无法导出 DOCX。") from exc

    def add_hyperlink(paragraph: Any, text: str, url: str) -> None:
        hyperlink = OxmlElement("w:hyperlink")
        if url.startswith("#"):
            hyperlink.set(qn("w:anchor"), url[1:])
        else:
            relationship = paragraph.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
            hyperlink.set(qn("r:id"), relationship)
        run = OxmlElement("w:r"); props = OxmlElement("w:rPr")
        hyperlink_style = OxmlElement("w:rStyle"); hyperlink_style.set(qn("w:val"), "Hyperlink"); props.append(hyperlink_style)
        color = OxmlElement("w:color"); color.set(qn("w:val"), "2F75B5"); props.append(color)
        underline = OxmlElement("w:u"); underline.set(qn("w:val"), "single"); props.append(underline)
        run.append(props); node = OxmlElement("w:t"); node.text = text; run.append(node); hyperlink.append(run); paragraph._p.append(hyperlink)

    bookmark_id = 0
    def bookmark(paragraph: Any, name: str) -> None:
        nonlocal bookmark_id
        bookmark_id += 1
        start = OxmlElement("w:bookmarkStart"); start.set(qn("w:id"), str(bookmark_id)); start.set(qn("w:name"), name)
        end = OxmlElement("w:bookmarkEnd"); end.set(qn("w:id"), str(bookmark_id))
        paragraph._p.insert(0, start); paragraph._p.append(end)

    document = Document()
    hyperlink_style = document.styles["Hyperlink"] if "Hyperlink" in document.styles else document.styles.add_style("Hyperlink", WD_STYLE_TYPE.CHARACTER)
    hyperlink_style.font.color.rgb = RGBColor(47, 117, 181)
    hyperlink_style.font.underline = True
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    # Keep the complete frozen report readable without oversized spacing.
    section.left_margin = section.right_margin = Inches(0.85)
    section.top_margin = section.bottom_margin = Inches(0.68)
    section.header_distance = section.footer_distance = Inches(0.45)
    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"; normal.font.size = Pt(10); normal.font.color.rgb = RGBColor(36, 52, 71)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.paragraph_format.line_spacing = 1.12
    normal.paragraph_format.widow_control = True
    for style_name, size, color in (("Title", 22, RGBColor(23, 54, 93)), ("Heading 1", 15, RGBColor(79, 129, 189)), ("Heading 2", 10.5, RGBColor(23, 54, 93)), ("Heading 3", 10, RGBColor(79, 129, 189))):
        style = document.styles[style_name]
        style.font.name = "Microsoft YaHei"; style.font.size = Pt(size); style.font.color.rgb = color
        style.font.bold = True
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Microsoft YaHei")
    document.styles["Heading 1"].paragraph_format.space_before = Pt(7)
    document.styles["Heading 1"].paragraph_format.space_after = Pt(3)
    document.styles["Heading 2"].paragraph_format.line_spacing = 1.08
    document.styles["Heading 2"].paragraph_format.space_before = Pt(5)
    document.styles["Heading 2"].paragraph_format.space_after = Pt(2)
    document.styles["Heading 2"].paragraph_format.keep_with_next = True
    header = section.header.paragraphs[0]; header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header_run = header.add_run("科技文献智能服务平台｜智能选题正式报告"); header_run.font.size = Pt(8); _set_brand(header_run)
    footer = section.footer.paragraphs[0]; footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer.add_run("智能选题正式报告 · "); footer_run.font.size = Pt(8); _set_black(footer_run)
    field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), "PAGE"); footer._p.append(field)

    def add_text(paragraph: Any, text: str, *, bold: bool = False, color: RGBColor | None = None) -> None:
        text = _reader_visible(text)
        cursor = 0
        pattern = re.compile(r"https://www\.nstl\.gov\.cn/paper_detail\.html\?id=[^\s；。，）)<>]+|\[(\d+)\]")
        for match in pattern.finditer(text):
            if match.start() > cursor:
                run = paragraph.add_run(text[cursor:match.start()]); run.bold = bold; run.font.color.rgb = color or RGBColor(36, 52, 71)
            url = match.group(0).rstrip("；。")
            add_hyperlink(paragraph, url, f"#ref_{match.group(1)}" if match.group(1) else url)
            if len(url) < len(match.group(0)):
                run = paragraph.add_run(match.group(0)[len(url):]); run.bold = bold; _set_black(run)
            cursor = match.end()
        if cursor < len(text):
            run = paragraph.add_run(text[cursor:]); run.bold = bold; run.font.color.rgb = color or RGBColor(36, 52, 71)

    def add_body(text: Any, *, lead: bool = False, indent: bool = True) -> None:
        value = str(text or "").strip()
        if not value: return
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.first_line_indent = Inches(0) if lead or not indent else Inches(0.28)
        paragraph.paragraph_format.space_after = Pt(3 if not lead else 5)
        if lead:
            paragraph.paragraph_format.left_indent = Inches(0.12)
            paragraph.paragraph_format.right_indent = Inches(0.12)
        add_text(paragraph, value, bold=False, color=RGBColor(50, 75, 102) if lead else None)

    def add_list(items: list[Any], numbered: bool = False) -> None:
        for index, item in enumerate(items, start=1):
            paragraph = document.add_paragraph(style="Normal" if numbered else "List Bullet")
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.left_indent = Inches(0.28)
            paragraph.paragraph_format.first_line_indent = Inches(-0.28) if numbered else Inches(0)
            add_text(paragraph, f"{index}. {item}" if numbered else str(item))

    def shade(cell: Any, fill: str) -> None:
        properties = cell._tc.get_or_add_tcPr(); element = properties.find(qn("w:shd"))
        if element is None: element = OxmlElement("w:shd"); properties.append(element)
        element.set(qn("w:fill"), fill)

    def add_table(spec: dict[str, Any]) -> None:
        columns = list(spec.get("columns") or [])
        if not columns: return
        document.add_heading(_reader_visible(spec.get("title", "")), level=2)
        table = document.add_table(rows=1, cols=len(columns)); table.style = "Table Grid"; table.autofit = False
        table_properties = table._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = OxmlElement(f"w:{edge}")
            for name, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
                border.set(qn(f"w:{name}"), value)
            borders.append(border)
        table_properties.append(borders)
        margins = OxmlElement("w:tblCellMar")
        for edge in ("top", "left", "bottom", "right"):
            margin = OxmlElement(f"w:{edge}")
            margin.set(qn("w:w"), "90")
            margin.set(qn("w:type"), "dxa")
            margins.append(margin)
        table_properties.append(margins)
        layout = table_properties.find(qn("w:tblLayout"))
        if layout is None:
            layout = OxmlElement("w:tblLayout"); table_properties.append(layout)
        layout.set(qn("w:type"), "fixed")
        widths = _table_widths(spec, total=(section.page_width - section.left_margin - section.right_margin) / 914400)
        table_width = table_properties.find(qn("w:tblW"))
        table_width.set(qn("w:w"), str(round(sum(widths) * 1440)))
        table_width.set(qn("w:type"), "dxa")
        for child in list(table._tbl.tblGrid):
            table._tbl.tblGrid.remove(child)
        for width in widths:
            grid_column = OxmlElement("w:gridCol")
            grid_column.set(qn("w:w"), str(round(width * 1440)))
            table._tbl.tblGrid.append(grid_column)
        table_font_size = Pt(float(spec.get("font_size") or 9.5))

        def set_cell_width(cell: Any, width: float) -> None:
            cell.width = Inches(width)
            properties = cell._tc.get_or_add_tcPr()
            cell_width = properties.find(qn("w:tcW"))
            if cell_width is None:
                cell_width = OxmlElement("w:tcW"); properties.append(cell_width)
            cell_width.set(qn("w:w"), str(round(width * 1440)))
            cell_width.set(qn("w:type"), "dxa")

        header_row = table.rows[0]; header_row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for index, (cell, column) in enumerate(zip(header_row.cells, columns)):
            set_cell_width(cell, widths[index])
            cell.text = _reader_visible(column); shade(cell, "DCEAF7")
            cell.vertical_alignment = 1
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.15
            paragraph.paragraph_format.keep_with_next = True
            if len(str(column)) <= 6:
                cell._tc.get_or_add_tcPr().append(OxmlElement("w:noWrap"))
            if str(column) in {"优先级", "序号", "年份", "排名"}:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in cell.paragraphs[0].runs:
                run.bold = True; run.font.size = table_font_size; _set_brand(run)
        for row_index, row in enumerate(spec.get("rows") or []):
            body_row = table.add_row()
            body_row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            cells = body_row.cells
            for index, column in enumerate(columns):
                set_cell_width(cells[index], widths[index])
                cells[index].vertical_alignment = 1
                if row_index % 2:
                    shade(cells[index], "F6F8FB")
                paragraph = cells[index].paragraphs[0]
                add_text(paragraph, str(row.get(column, "") if isinstance(row, dict) else ""))
                if str(column) in {"优先级", "序号", "年份", "排名"}:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for paragraph in cells[index].paragraphs:
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
                    paragraph.paragraph_format.line_spacing = 1.15
                    paragraph.paragraph_format.widow_control = True
                    # Keep short summary tables together, including their last row.
                    # Long tables retain normal pagination and repeated headings.
                    paragraph.paragraph_format.keep_with_next = (
                        len(spec.get("rows") or []) <= 8
                        and row_index < len(spec.get("rows") or []) - 1
                    )
                    for run in paragraph.runs:
                        run.font.size = table_font_size; _set_black(run)
                    for properties in paragraph._p.xpath('./w:hyperlink/w:r/w:rPr'):
                        size = OxmlElement("w:sz"); size.set(qn("w:val"), str(round(table_font_size.pt * 2)))
                        properties.append(size)

    def add_chart(chart: dict[str, Any], paths: dict[str, list[Path]]) -> None:
        if chart.get("key") not in paths:
            add_body(f"图 {chart.get('figure_number', '')} {chart.get('title') or '图表'}：{_chart_limitation(chart)}", lead=True, indent=False)
            add_body(chart.get("source_note"), indent=False)
            add_body(chart.get("caption"), indent=False)
            return
        for part_index, path in enumerate(paths[chart["key"]], 1):
            # Writer can place an inline picture above the top page boundary
            # when the picture, caption and source are separate keep-with-next
            # paragraphs at a page break. Keep one figure in a non-splitting
            # table row so all four parts move to the next page together.
            figure = document.add_table(rows=1, cols=1)
            figure.autofit = False
            figure.columns[0].width = Inches(6.8)
            figure.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
            cell = figure.cell(0, 0)
            cell.width = Inches(6.8)
            picture = cell.paragraphs[0]; picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
            # The shared typography sets an exact 21.6pt body line. An inline
            # image in that line is then painted above its baseline rather than
            # reserving its actual height, so Writer clips it at the page top.
            picture.paragraph_format.line_spacing = 1.0
            graph_width = 6.0 if chart.get("chart_version") in {"chart-v10", "chart-v11"} and chart.get("key") == "discipline_network" else 4.8
            picture.add_run().add_picture(str(path), width=Inches(graph_width))
            suffix = f"（{part_index}）" if len(paths[chart["key"]]) > 1 else ""
            caption = cell.add_paragraph(f"图 {chart.get('figure_number', '')}{suffix} {_reader_visible(chart.get('title', ''))}"); caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in caption.runs: run.bold = False; _set_black(run)
            source = cell.add_paragraph(_reader_visible(chart.get("source_note") or f"来源：本次锁定证据池；数据快照：{chart.get('data_snapshot_version', '')}")); source.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in source.runs: run.font.size = Pt(8.5); _set_black(run)
            if chart.get("caption"):
                note = cell.add_paragraph(_reader_visible(chart["caption"])); note.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in note.runs: run.font.size = Pt(8.5); _set_black(run)

    def render_block(block: dict[str, Any], tables: dict[str, dict[str, Any]], charts: dict[str, dict[str, Any]], paths: dict[str, Path]) -> None:
        kind = str(block.get("kind") or "paragraph")
        if kind in {"paragraph", "lead"}: add_body(block.get("text"), lead=kind == "lead", indent=kind != "lead")
        elif kind == "heading":
            document.add_heading(_reader_visible(block.get("text") or ""), level=max(2, min(3, int(block.get("level") or 2))))
        elif kind in {"bullet_list", "numbered_list"}: add_list(list(block.get("items") or []), numbered=kind == "numbered_list")
        elif kind == "table_ref": add_table(tables.get(str(block.get("key")), {}))
        elif kind == "figure_ref":
            chart = charts.get(str(block.get("key")))
            if chart: add_chart(chart, paths)
        elif kind == "candidate_detail":
            candidate_title = _reader_visible(block.get("title") or "")
            heading_text = f"候选题 {block.get('index', '')}：{candidate_title}"
            if len(heading_text) > 32:
                heading_text = f"候选题 {block.get('index', '')}\n{candidate_title}"
            title = document.add_heading(heading_text, level=2)
            title.paragraph_format.line_spacing = 1.08
            title.paragraph_format.space_before = Pt(5)
            title.paragraph_format.space_after = Pt(2)
            title.paragraph_format.keep_with_next = True
            if title.runs: _set_brand(title.runs[0])
            if block.get("show_status", True):
                status = document.add_paragraph(f"状态：{_candidate_status_label(block.get('status'))}"); status.paragraph_format.first_line_indent = Inches(0); status.paragraph_format.space_after = Pt(2)
                status.paragraph_format.keep_with_next = True
                for run in status.runs: run.font.size = Pt(9); _set_black(run)
            for field in block.get("fields") or []:
                paragraph = document.add_paragraph(); paragraph.paragraph_format.first_line_indent = Inches(0.2); paragraph.paragraph_format.space_after = Pt(3)
                label = paragraph.add_run(f"{_reader_visible(field.get('label', ''))}："); label.bold = True; _set_brand(label)
                add_text(paragraph, str(field.get("value") or ""))
        elif kind == "reference_list":
            for item in block.get("items") or []:
                paragraph = document.add_paragraph(); paragraph.paragraph_format.left_indent = Inches(0.2); paragraph.paragraph_format.first_line_indent = Inches(-0.2); paragraph.paragraph_format.space_after = Pt(3)
                paragraph.paragraph_format.line_spacing = 1.08
                bookmark(paragraph, f"ref_{item.get('number', '')}")
                url = evidence_url(item)
                if url:
                    add_hyperlink(paragraph, f"[{item.get('number', '')}] ", url)
                else:
                    paragraph.add_run(f"[{item.get('number', '')}] ")
                if url:
                    add_hyperlink(paragraph, str(item.get('title', '')), url)
                else:
                    add_text(paragraph, str(item.get('title', '')))
                detail = f"。{item.get('authors', '')} {item.get('venue') or 'NSTL'}，{item.get('year') or '年份未提供'}。" + _reference_identifier(item)
                add_text(paragraph, detail)
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                for properties in paragraph._p.xpath('./w:hyperlink/w:r/w:rPr'):
                    size = OxmlElement("w:sz"); size.set(qn("w:val"), "18"); properties.append(size)

    metadata = state.report_plan.cover_metadata
    cover = document.add_paragraph(style="Title"); cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cover.paragraph_format.space_before = Pt(0); cover.paragraph_format.space_after = Pt(8)
    title_run = cover.add_run(_title(state)); title_run.bold = True; title_run.font.size = Pt(17); _set_brand(title_run)
    for label, value in [("生成方式", metadata.get("generation_method", "智能选题九阶段流程")), ("数据源", metadata.get("data_source", f"文献 {len(state.evidence_pool)} 篇")), ("样本量", metadata.get("sample_size", len(state.evidence_pool))), ("生成日期", metadata.get("generated_at", "")), ("报告版本", state.versions.report_version), ("模板版本", state.report_plan.template_version or state.versions.template_version)]:
        if state.report_plan.template_version == "TopicReportV7" and label not in {"数据源", "生成日期"}:
            continue
        paragraph = document.add_paragraph(f"{label}：{value or '未提供'}")
        paragraph.paragraph_format.space_after = Pt(2); paragraph.paragraph_format.line_spacing = 1.05
        for run in paragraph.runs: run.font.size = Pt(8); _set_black(run)
    with tempfile.TemporaryDirectory(prefix="topic-report-") as temporary:
        chart_paths = _charts(state, Path(temporary)); tables = {str(item.get("key")): item for item in state.report_plan.table_specs}; charts = {str(item.get("key")): item for item in state.report_plan.chart_specs}
        for section_index, section_data in enumerate(state.report_plan.sections):
            key = str(section_data.get("key", "")); spec = _section_spec(state, key)
            if key == "references" and state.report_plan.template_version in {"TopicReportV5", "TopicReportV6", "TopicReportV7"}:
                document.add_page_break()
            heading = document.add_heading(str(section_data.get("title", "")), level=1)
            bookmark(heading, f"section_{key}")
            if heading.runs: _set_brand(heading.runs[0])
            rendered_refs: set[str] = set()
            for block in _blocks(section_data, spec):
                if block.get("kind") in {"table_ref", "figure_ref"}: rendered_refs.add(str(block.get("key")))
                render_block(block, tables, charts, chart_paths)
            for chart_key in spec.get("chart_keys", []):
                if chart_key not in rendered_refs and charts.get(chart_key): add_chart(charts[chart_key], chart_paths)
            for table_key in spec.get("table_keys", []):
                if table_key not in rendered_refs: add_table(tables.get(table_key, {}))
        apply_report_typography(document)
        output = io.BytesIO(); document.save(output)
    return output.getvalue(), f"{_safe_filename(_title(state))}.docx"


def _writer_outputs(state: TopicSelectionState) -> tuple[bytes, bytes]:
    """Return the one Writer-normalized DOCX/PDF pair for a frozen snapshot."""
    from app.services.report_pdf_runtime import render_docx

    render_state = _bounded_export_state(state)
    key = (
        render_state.report_snapshot.snapshot_id or render_state.versions.report_version,
        render_state.report_plan.template_version or render_state.versions.template_version,
        render_state.report_snapshot.format_hashes.get("content", ""),
        TOPIC_CHART_RENDER_VERSION,
    )
    with _WRITER_EXPORT_LOCK:
        cached = _WRITER_EXPORT_CACHE.get(key)
        if cached is not None:
            _WRITER_EXPORT_CACHE.move_to_end(key)
            return cached
        source, _ = _build_topic_docx_source(render_state)
        normalized, pdf = render_docx(source)
        _WRITER_EXPORT_CACHE[key] = (normalized, pdf)
        while len(_WRITER_EXPORT_CACHE) > 8:
            _WRITER_EXPORT_CACHE.popitem(last=False)
        return normalized, pdf


def build_topic_docx(state: TopicSelectionState) -> tuple[bytes, str]:
    normalized, _ = _writer_outputs(state)
    return normalized, f"{_safe_filename(_title(state))}.docx"


def _pdf_page_count(content: bytes) -> int:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    return len(PdfReader(io.BytesIO(content)).pages)


def build_topic_pdf(state: TopicSelectionState) -> tuple[bytes, str]:
    render_state = _bounded_export_state(state)
    _, content = _writer_outputs(render_state)
    try:
        page_count = _pdf_page_count(content)
    except Exception:
        page_count = 0
    try:
        page_count = page_count or _pdf_page_count(content)
        render_state.report_snapshot.rendered_page_count = page_count
        render_state.report_plan.page_metrics.rendered_total_pages = page_count
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        pages = PdfReader(io.BytesIO(content)).pages
        # Writer/PDF text extraction can concatenate the running header, page
        # number, and the first heading on a page (for example ``7参考文献``).
        # Match the stable heading text itself instead of requiring a line
        # boundary so the frozen report's body/appendix metrics remain usable.
        references_start = next((index for index, page in enumerate(pages)
            if re.search(r"参考文献", page.extract_text() or "")), None)
        if references_start is None:
            raise ValueError("无法定位参考文献起始页")
        body_pages = references_start
        appendix_pages = page_count - body_pages
        render_state.report_plan.page_metrics.rendered_body_pages = body_pages
        render_state.report_plan.page_metrics.rendered_appendix_pages = appendix_pages
        render_state.report_snapshot.body_page_count = body_pages
        render_state.report_snapshot.appendix_page_count = appendix_pages
        lower, upper = render_state.report_plan.page_metrics.target_body_pages
        render_state.report_plan.page_metrics.status = "within_target" if lower <= body_pages <= upper else "over_target" if body_pages > upper else "under_target"
        if render_state.report_plan.page_metrics.status != "within_target":
            render_state.report_plan.page_metrics.warning = f"正文实际 {body_pages} 页，目标约5页（{lower}-{upper}页）；参考文献 {appendix_pages} 页另计。"
    except Exception:
        render_state.report_plan.page_metrics.status = "unavailable"
        render_state.report_plan.page_metrics.warning = "无法核验正文与参考文献分页，未以总页数代替正文页数。"
    state.report_plan.page_metrics = render_state.report_plan.page_metrics.model_copy(deep=True)
    for key in ("rendered_page_count", "body_page_count", "appendix_page_count"):
        setattr(state.report_snapshot, key, getattr(render_state.report_snapshot, key))
    return content, f"{_safe_filename(_title(state))}.pdf"
