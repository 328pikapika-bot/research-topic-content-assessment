"""Versioned content-assessment report exporters."""

from __future__ import annotations

from app.services.report_typography import (TYPOGRAPHY_VERSION, REPORT_TYPOGRAPHY_CSS)

import html
import hashlib
import io
import json
import re
import zipfile
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agents.content_assessment.schemas import ContentAssessmentState
from app.core.config import BEIJING_TZ
from app.services.content_assessment_report import build_content_assessment_docx

# Presentation-only revision: invalidate exports without rewriting frozen reports.
ASSESSMENT_EXPORT_LAYOUT_VERSION = "scholarly-report-v7-caption-and-chart-layout-v1"

def _now_str() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def _safe_filename(value: str) -> str:
    sanitized = "".join("_" if char in r'\\/:*?\"<>|' else char for char in value).strip()
    return sanitized[:100] or "内容评估报告"


def _display_title(state: ContentAssessmentState) -> str:
    from app.services.content_assessment_report import _display_title as reader_title
    return reader_title(state)


def _report_data(state: ContentAssessmentState) -> dict[str, Any]:
    title = _display_title(state)
    evidence_index = {item.evidence_id: item for item in state.evidence_records}
    return {
        "title": title,
        "generated_at": _now_str(),
        "profile": state.assessment_profile,
        "nine_fields": state.nine_fields,
        "claims": state.claims,
        "indicators": state.indicators,
        "evidence": state.evidence_records,
        "links": state.claim_evidence_links,
        "decisions": state.novelty_decisions,
        "scorecard": state.scorecard,
        "trend": state.trend_analysis,
        "quality_gate": state.quality_gate,
        "plan": state.report_plan,
        "evidence_index": evidence_index,
        "stage_trace": state.stage_trace,
        "process_trace": state.process_trace,
    }


def build_markdown(state: ContentAssessmentState) -> tuple[str, str]:
    ensure_snapshot(state)
    prepare_exports(state)
    from app.agents.content_assessment.report_composition import markdown as render_markdown
    markdown = render_markdown(state)
    title = _display_title(state)
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        lines[0] = f"# {title}"
    return "\n".join(lines).rstrip() + "\n", f"{_safe_filename(title)}.md"


def build_html(state: ContentAssessmentState) -> tuple[str, str]:
    markdown, filename = build_markdown(state)
    from app.common.retrieval.links import citation_urls
    links = citation_urls(state.report_snapshot.audit_data.get("evidence_records", []), state.report_snapshot.citation_numbers)
    parts: list[str] = []
    toc: list[str] = []
    in_toc = False
    appendix_section = False
    pending_table: list[str] = []

    def inline(value: str) -> str:
        segments: list[str] = []
        cursor = 0
        pattern = re.compile(r"\[((?:\\.|[^\]\\])+)\]\(<(https?://[^>]+)>\)|https?://[^\s；;，,。)）]+|\[(\d+)\]")
        for match in pattern.finditer(value):
            segments.append(html.escape(value[cursor:match.start()]))
            if match.group(2):
                label = re.sub(r"\\([\\\[\]])", r"\1", match.group(1))
                segments.append(f'<a href="{html.escape(match.group(2), quote=True)}" target="_blank" rel="noopener">{html.escape(label)}</a>')
            elif match.group(3):
                number = match.group(3)
                if links.get(number):
                    segments.append(f'<a class="citation" href="{html.escape(links[number], quote=True)}" target="_blank" rel="noopener">[{number}]</a>')
                else:
                    segments.append(f'<span class="citation">[{number}]</span>')
            else:
                url = match.group(0)
                segments.append(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(url)}</a>')
            cursor = match.end()
        segments.append(html.escape(value[cursor:]))
        return "".join(segments)

    def flush_table() -> None:
        nonlocal pending_table
        if len(pending_table) < 2:
            pending_table = []
            return
        rows = [[item.strip() for item in line.strip().strip("|").split("|")] for line in pending_table]
        if not rows or not re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", pending_table[1]):
            pending_table = []
            return
        headers = rows[0]
        body_rows = rows[2:]
        parts.append("<table><thead><tr>" + "".join(f"<th>{html.escape(value)}</th>" for value in headers) + "</tr></thead><tbody>")
        for row in body_rows:
            values = (row + [""] * len(headers))[:len(headers)]
            parts.append("<tr>" + "".join(f"<td>{inline(value)}</td>" for value in values) + "</tr>")
        parts.append("</tbody></table>")
        pending_table = []

    for line in markdown.splitlines():
        if line.startswith("|") and line.endswith("|"):
            pending_table.append(line)
            continue
        flush_table()
        image_match = re.fullmatch(r"!\[([^\]]+)\]\((data:image/png;base64,[A-Za-z0-9+/=]+)\)", line)
        if image_match:
            parts.append(f'<figure><img src="{image_match[2]}" alt="{html.escape(image_match[1], quote=True)}"></figure>')
        elif line.startswith("> "):
            label = line[2:]
            kind = "execution" if label.startswith("【公开执行摘要】") else "risk" if label.startswith("【风险】") else "result"
            parts.append(f'<div class="report-callout report-callout--{kind}">{inline(label)}</div>')
        elif line.startswith("# "):
            in_toc = False
            parts.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            raw = line[3:]
            raw = re.sub(r"\s*\{[^}]+\}\s*$", "", raw)
            anchor = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw.lower()).strip("-") or "section"
            in_toc = raw == "目录"
            appendix_section = raw == "参考文献" or raw.startswith("附录")
            if raw not in {"目录", "质量门与阅读提示"}:
                toc.append(f'<li><a href="#{html.escape(anchor)}">{html.escape(raw)}</a></li>')
            parts.append(f'<h2 id="{html.escape(anchor)}">{html.escape(raw)}</h2>')
        elif re.match(r"^#{3,5} ", line):
            prefix, raw = line.split(" ", 1)
            level = len(prefix)
            in_toc = False
            appendix_section = False
            anchor = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw.lower()).strip("-")
            toc.append(f'<li class="toc-level-{level}"><a href="#{html.escape(anchor)}">{html.escape(raw)}</a></li>')
            parts.append(f'<h{level} id="{html.escape(anchor)}">{html.escape(raw)}</h{level}>')
        elif line.startswith("- "):
            if in_toc:
                continue
            parts.append(f"<li>{html.escape(line[2:])}</li>")
        elif line:
            if in_toc:
                continue
            if in_toc and re.match(r"^\d+\.\s+\[", line):
                continue
            escaped = inline(line)
            reference_match = re.match(r"^\[(\d+)\]\s+", line) if appendix_section else None
            anchor_attr = f' id="evidence-{reference_match.group(1)}"' if reference_match else ""
            classes = ["report-paragraph"]
            if appendix_section:
                classes.append("report-appendix")
            if reference_match and appendix_section:
                classes.append("report-reference")
            if line.startswith(("报告版本：", "生成时间：", "证据 ", "表 ", "图 ", "数据来源：")):
                classes.append("report-meta")
            parts.append(f'<p class="{" ".join(classes)}"{anchor_attr}>{escaped}</p>')
    flush_table()
    title = html.escape(_display_title(state))
    content = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>{title}</title>
<style>
@page{{size:A4;margin:20mm}}
body{{font-family:'Liberation Serif','Noto Sans CJK SC','Noto Sans SC','Microsoft YaHei',SimSun,serif;max-width:210mm;margin:auto;padding:24px;color:#333;line-height:18pt;font-size:10.5pt;background:#eef2f7}}
main{{background:#fff;padding:20mm;box-shadow:0 2px 12px #17293c14}}
h1{{font-size:25pt;line-height:1.35;color:#1e293b;margin:24px 0}}
h2{{font-size:17pt;line-height:1.4;margin-top:28px;padding-bottom:8px;border-bottom:1.5pt solid #1e3a8a;color:#1e3a8a;break-after:avoid}}
h3{{font-size:17pt;line-height:1.4;margin-top:28px;color:#1e3a8a;break-after:avoid}}
h4{{font-size:11.5pt;margin-top:18px;color:#1e3a8a;break-after:avoid}}
h5{{font-size:10.5pt;margin-top:14px;color:#1e3a8a;break-after:avoid}}
p{{margin:7px 0;orphans:2;widows:2}}.report-paragraph{{text-indent:2em}}
.report-meta,.report-reference,.report-appendix{{text-indent:0;color:#64748b}}
.toc{{background:#f8fafc;padding:18px 24px;margin:24px 0}}.toc a,.citation{{color:#1e3a8a;text-decoration:none}}li{{margin:5px 0}}
.toc-level-3{{margin-left:1em}}.toc-level-4{{margin-left:2em}}.toc-level-5{{margin-left:3em}}
table{{width:100%;border-collapse:collapse;margin:14px 0;font-size:10pt;line-height:1.5}}th,td{{border:0;border-bottom:1px solid #d9d9d9;padding:7px 9px;vertical-align:middle;text-align:left}}th{{background:#eef3f8;color:#1e3a8a;font-weight:700}}thead{{display:table-header-group}}
.report-callout{{padding:12px 16px;margin:12px 0;border-left:3px solid #0d9488;background:#e9f7f5;color:#0d9488;break-inside:avoid}}
.report-callout--result{{background:#eef5ff;border-color:#1e3a8a;color:#1e3a8a}}.report-callout--risk{{background:#fff7e6;border-color:#c26a00;color:#9a5700}}
figure{{margin:16px 0;break-inside:avoid}}figure img{{display:block;width:100%;height:auto}}
@media(max-width:760px){{body{{padding:8px}}main{{padding:20px}}table{{display:block;overflow:auto}}}}
@media print{{body{{padding:0;background:#fff}}main{{padding:0;box-shadow:none}}}}
{REPORT_TYPOGRAPHY_CSS}</style>
</head><body><main><nav class=\"toc\"><strong>目录</strong><ol>{''.join(toc)}</ol></nav>{''.join(parts)}</main></body></html>"""
    return content, filename.removesuffix(".md") + ".html"


def build_docx(state: ContentAssessmentState) -> tuple[bytes, str]:
    ensure_snapshot(state)
    rendered = prepare_exports(state)
    if rendered:
        return rendered[0], f"{_safe_filename(_display_title(state))}.docx"
    return build_content_assessment_docx(state)




def build_pdf(state: ContentAssessmentState) -> tuple[bytes, str]:
    """Export PDF and replace estimated page metadata with the real count."""
    ensure_snapshot(state)
    rendered = prepare_exports(state)
    if rendered is None:
        raise RuntimeError(state.output_json.get("pdf_unavailable", "PDF 转换器不可用，Word 和 Markdown 仍可下载。"))
    return rendered[1], f"{_safe_filename(_display_title(state))}.pdf"


_EXPORT_CACHE: OrderedDict = OrderedDict()
_EXPORT_LOCK = threading.RLock()


def prepare_exports(state: ContentAssessmentState):
    """Finalize one immutable content version before serving any format.

    The bounded cache stores the normalized DOCX and its PDF together. Report
    metadata is persisted by the route with compare-and-set, never whole state.
    """
    import fitz
    from app.services.report_pdf_runtime import render_docx
    from app.agents.content_assessment.report_composition import markdown
    ensure_snapshot(state)
    with _EXPORT_LOCK:
        key = (state.user_scope, state.report_snapshot.snapshot_id,
               state.report_snapshot.template_version, state.report_snapshot.render_version, TYPOGRAPHY_VERSION,
               ASSESSMENT_EXPORT_LAYOUT_VERSION)
        cached = _EXPORT_CACHE.get(key)
        if cached:
            document, pdf, fields = cached
            for attr, value in fields.items():
                if attr == "output_json":
                    state.output_json.update(value)
                else:
                    setattr(state, attr, value.model_copy(deep=True) if hasattr(value, "model_copy") else value)
            return document, pdf
        try:
            result = _render_finalized(state, render_docx, fitz, markdown)
        except RuntimeError as exc:
            state.output_json["pdf_unavailable"] = str(exc)
            return None
        state.output_json.pop("pdf_unavailable", None)
        fields = {name: getattr(state, name).model_copy(deep=True) for name in ("report_plan", "report_snapshot", "report_metrics")}
        fields["output_markdown"] = state.output_markdown
        fields["output_json"] = {k: state.output_json[k] for k in ("report_plan", "report_metrics", "quality_checks") if k in state.output_json}
        _EXPORT_CACHE[key] = (*result, fields)
        final_key = (state.user_scope, state.report_snapshot.snapshot_id,
                     state.report_snapshot.template_version, state.report_snapshot.render_version, TYPOGRAPHY_VERSION,
                     ASSESSMENT_EXPORT_LAYOUT_VERSION)
        _EXPORT_CACHE[final_key] = (*result, fields)
        while len(_EXPORT_CACHE) > 8:
            _EXPORT_CACHE.popitem(last=False)
        return result


def _render_finalized(state, render_docx, fitz, markdown):
    # Pagination may flag a shortage or excess, but must never rewrite an
    # already frozen conclusion, paragraph or citation to meet a page target.
    docx, _ = build_content_assessment_docx(state)
    normalized, payload = render_docx(docx)
    with fitz.open(stream=payload, filetype="pdf") as pdf:
        page_count = len(pdf)
        body_count = page_count
        if state.report_snapshot.template_version == "ContentAssessmentResultReportV5":
            texts = [page.get_text() for page in pdf]
            body_start = next((i for i, text in enumerate(texts) if "一、评估摘要与总体结论" in text and "目录" not in text), 2)
            references_start = next((i for i, text in enumerate(texts) if i > body_start and "六、参考文献及必要说明" in text), page_count)
            body_count = references_start - body_start
        elif state.report_snapshot.template_version in {"ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"}:
            texts = [page.get_text() for page in pdf]
            bookmarks = {title: page - 1 for _, title, page in pdf.get_toc() if page > 0}
            body_start = bookmarks.get("第一章 评估说明", next((i for i, text in enumerate(texts)
                if "第一章 评估说明" in text and "目录" not in text), 2))
            references_start = bookmarks.get("参考文献", next((i for i, text in enumerate(texts)
                if i > body_start and re.search(r"(?m)^参考文献\s*$", text)), page_count))
            body_count = max(0, references_start - body_start)
    from app.agents.content_assessment.graph import ContentAssessmentGraph
    ContentAssessmentGraph._update_report_length_metrics(state)
    state.output_markdown = markdown(state)
    state.report_metrics.actual_page_count = page_count
    state.report_metrics.page_count_verified = True
    state.report_metrics.page_count_source = "pdf:libreoffice:" + state.report_snapshot.snapshot_id
    if state.report_snapshot.template_version in {"ContentAssessmentResultReportV5", "ContentAssessmentResearchReportV6", "ContentAssessmentResearchReportV7"}:
        state.report_metrics.body_pages = body_count
    minimum, maximum = state.report_plan.target_page_min, state.report_plan.target_page_max
    if state.report_snapshot.template_version in {"ContentAssessmentResultReportV3", "ContentAssessmentResultReportV4"} or maximum <= 0:
        state.report_metrics.page_target_status = "not_applicable"
        state.report_metrics.page_target_reason = "按评估对象、要点和有效证据组织篇幅。"
    elif body_count < minimum:
        state.report_metrics.page_target_status = "below_target"
        state.report_metrics.page_target_reason = "当前冻结的材料、比较证据与有效图表不足以达到标准篇幅，保留真实内容，不填充空白页。"
    elif body_count > maximum:
        state.report_metrics.page_target_status = "above_target"
        state.report_metrics.page_target_reason = "为保留完整结论、引用和反证，报告实际篇幅超过目标范围。"
    else:
        state.report_metrics.page_target_status = "within_target"
        state.report_metrics.page_target_reason = ""
    state.report_plan.actual_page_count = page_count
    state.output_json["report_metrics"] = state.report_metrics.model_dump()
    state.output_json["report_plan"] = state.report_plan.model_dump()
    state.output_json.setdefault("quality_checks", {})["page_target_review_required"] = (
        state.report_metrics.page_target_status == "above_target")
    return normalized, payload


def ensure_snapshot(state: ContentAssessmentState) -> None:
    from app.agents.content_assessment.report_composition import state_from_snapshot
    from app.agents.content_assessment.schemas import ReportSectionSpec, ReportSnapshot
    if state.report_snapshot is not None:
        # A frozen snapshot is the historical record. Restore only the renderer
        # projection on the transient state; never normalize or rewrite the
        # snapshot itself during an export/read operation.
        actual_pages = state.report_plan.actual_page_count
        if state.report_snapshot.render_context:
            state.report_plan = state_from_snapshot(state.report_snapshot).report_plan
            state.report_plan.actual_page_count = actual_pages
        state.report_plan.section_specs = [s.model_copy(deep=True) for s in state.report_snapshot.sections]
        state.report_plan.render_version = state.report_snapshot.render_version
        return
    # Pre-snapshot reports use only the saved prose/plan. This temporary rendering
    # envelope is never written back by the route and does not run analysis,
    # normalize sections, renumber references, or manufacture chart data.
    sections = [s.model_copy(deep=True) for s in state.report_plan.section_specs]
    if not sections:
        sections = [ReportSectionSpec(section_key="legacy_body", title="原报告正文",
            content=state.output_markdown or state.report_plan.executive_summary or "该历史版本未记录报告正文。")]
    payload = [s.model_dump() for s in sections]
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    state.report_snapshot = ReportSnapshot(schema_version="legacy-read-only-v1",
        snapshot_id=f"legacy-read-only-{digest}", generated_at="",
        sections=sections, template_version=state.versions.template_version,
        render_version=state.report_plan.render_version, source_snapshot_id="legacy-read-only",
        cover_metadata={"title": state.report_plan.report_title or "内容评估报告",
            "report_version": state.report_plan.report_version, "report_type": state.report_plan.report_type,
            "evidence_cutoff_date": state.report_plan.evidence_cutoff_date},
        render_context={"report_plan": state.report_plan.model_dump(exclude={"sections", "section_specs"}),
                        "versions": state.versions.model_dump()})
    state.report_plan.section_specs = sections


def build_evidence_package(state: ContentAssessmentState) -> tuple[bytes, str]:
    ensure_snapshot(state)
    prepare_exports(state)
    snapshot = state.report_snapshot
    used_ids = set(snapshot.citation_numbers)
    # Legacy snapshots predate usage tracking. Their citation map is the
    # safest fallback because it contains only records referenced by sections.
    if not used_ids:
        used_ids = set(snapshot.citation_numbers)
    audit = dict(snapshot.audit_data)
    audit["evidence_records"] = [
        item for item in audit.get("evidence_records", [])
        if item.get("evidence_id") in used_ids
    ]
    audit["evidence_chunks"] = [
        item for item in audit.get("evidence_chunks", [])
        if item.get("evidence_id") in used_ids
    ]
    audit["claim_evidence_links"] = [
        item for item in audit.get("claim_evidence_links", [])
        if item.get("evidence_id") in used_ids
    ]
    # Search plans are process metadata rather than literature records. Keep
    # completed plans so a researcher can reproduce the search, while the
    # evidence list itself remains limited to cited records.
    audit["query_plans"] = [item for item in audit.get("query_plans", []) if item.get("selected_count", 0)]
    filtered_snapshot = snapshot.model_copy(update={"audit_data": audit})
    lines = ["# 创新性评估证据附件", "", f"快照：{snapshot.snapshot_id}",
             f"报告版本：{state.report_plan.report_version}",
             "本附件仅包含实际参与正文判断、风险或市场/监管观察的证据；未使用的检索候选不导出。", ""]
    # Historical snapshots may predate the v3 audit payload.  Keep the
    # evidence package downloadable with the fields that are available rather
    # than failing the whole export on a missing optional collection.
    for item in audit.get("claim_assessments", []):
        lines.extend([f"## {item['claim_id']} 完整比较记录", "", item.get("normalized_claim", ""), ""])
        for key in ("conclusion", "evidence_gaps", "incomparability_reasons", "field_matrix"):
            from app.agents.content_assessment.report_composition import readable
            lines.extend([f"{key}：{readable(item.get(key))}", ""])
    from app.common.retrieval.links import doi_url, http_url
    for item in audit.get("evidence_records", []):
        # Keep the three destinations explicit in the attachment. A legacy
        # url is treated as the original-source address only when it is a
        # valid absolute HTTP(S) URL; invalid values are never emitted.
        nstl = http_url(item.get("nstl_url")) or "未提供"
        source = http_url(item.get("source_url") or item.get("url")) or "未提供"
        doi = doi_url(item.get("doi")) or "未提供"
        lines.extend([f"## {item['evidence_id']} {item['title']}", "", f"DOI：{doi}；NSTL 详情地址：{nstl}；原文地址：{source}",
                      item.get("abstract") or item.get("excerpt") or "无可用摘要", ""])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("evidence.md", "\n".join(lines))
        archive.writestr("manifest.json", json.dumps({"snapshot_id": snapshot.snapshot_id,
            "report_version": state.report_plan.report_version, "citation_numbers": snapshot.citation_numbers}, ensure_ascii=False, indent=2))
        for key, data in audit.items():
            archive.writestr(f"{key}.json", json.dumps(data, ensure_ascii=False, indent=2))
        archive.writestr("report_snapshot.json", filtered_snapshot.model_dump_json(indent=2))
    return stream.getvalue(), f"{_safe_filename(_display_title(state))}-证据附件.zip"
