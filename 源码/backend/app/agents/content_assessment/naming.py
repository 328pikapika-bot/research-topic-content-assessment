"""Resolve research names and repair instructional labels in report presentation only."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy

FALLBACK = "创新性评估任务"
PRESENTATION_VERSION = "formal-assessment-name-v1"
_SUFFIX = re.compile(r"(?:创新性评估|内容评估)?(?:综合)?(?:正式|探索性)?(?:评估)?报告$")
_LABEL = re.compile(r"^(?:(?:论文|项目|指南|材料|课题)(?:名称|题目|标题)|评估对象|研究对象|题目|标题)\s*[:：|]\s*")
_NAMED = re.compile(r"(?:^|\n)\s*(?:(?:论文|项目|指南|材料|课题)(?:名称|题目|标题)|评估对象|研究对象|题目|标题)\s*[:：|]\s*([^\r\n]+)")
_GENERIC = re.compile(r"^(?:(?:我(?:要|想)?|本次|本次我要)?评估(?:的(?:是|对象))?\s*[:：]?\s*)?(?:论文|项目|指南)[。.!！\s]*$")


def clean_name(value: str) -> str:
    value = _LABEL.sub("", str(value or "").strip())
    value = _SUFFIX.sub("", value).strip(" ：:，,。；;_-—《》\"“”")
    if not value or "\n" in value or "\r" in value or _GENERIC.fullmatch(value):
        return ""
    if value in {FALLBACK, "评估任务", "新建评估", "待识别", "科研项目", "评估"}:
        return ""
    # Input field content and a long assessment request are not research titles.
    if re.match(r"(?:研究方向|研究内容|研究目标|关键方法|关键技术)\s*[:：]", value) or len(value) > 120:
        return ""
    if re.match(r"(?:请|帮我|我要|我想|本次).*?评估|我要评估", value):
        return ""
    return value


def research_name(raw: str = "", *, candidates=(), documents=(), material_texts=()) -> str:
    for candidate in candidates:
        if name := clean_name(candidate):
            return name
    for text in (raw, *material_texts):
        for match in _NAMED.finditer(text or ""):
            if name := clean_name(match[1]):
                return name
    # A bare title entered before the research brief is a valid saved name.
    first = next((line.strip() for line in (raw or "").splitlines() if line.strip()), "")
    if name := clean_name(first):
        return name
    for doc in documents:
        get = doc.get if isinstance(doc, dict) else lambda key, default="": getattr(doc, key, default)
        role = str(get("role", "") or "").lower()
        if role not in {"project", "primary", "main", "main_material", "主材料", "正文"}:
            continue
        filename = re.sub(r"\.(?:pdf|docx?|txt|md|rtf)$", "", str(get("name", "")), flags=re.I)
        if name := clean_name(filename):
            return name
    return FALLBACK


def state_research_name(state) -> str:
    profile = state.assessment_profile
    primary_ids = {doc.document_id for doc in profile.document_records if doc.role == "PROJECT"}
    texts = [item.content or item.excerpt for item in state.uploaded_file_context.files if item.file_id in primary_ids]
    return research_name(state.raw_user_input, candidates=(profile.title, profile.assessment_object),
                         documents=profile.document_records, material_texts=texts)


def report_title(state) -> str:
    snapshot = state.report_snapshot
    # Valid historical names remain exactly as frozen.
    if snapshot and clean_name(snapshot.cover_metadata.get("title", "")):
        return snapshot.cover_metadata["title"]
    name = state_research_name(state)
    return "创新性评估报告" if name == FALLBACK else f"{name}创新性评估报告"


def formalize_report_name(state) -> None:
    """Correct only invalid object labels on a rendering copy, preserving frozen facts."""
    snapshot = state.report_snapshot
    if not snapshot or clean_name(snapshot.cover_metadata.get("title", "")):
        return
    name = state_research_name(state)
    if name == FALLBACK:
        return
    old_names = {str(v).strip() for v in (snapshot.cover_metadata.get("assessment_object"),
        snapshot.render_context.get("assessment_profile", {}).get("title"),
        snapshot.render_context.get("assessment_profile", {}).get("assessment_object")) if v and not clean_name(v)}
    projected = snapshot.model_copy(deep=True)
    original_title = str(snapshot.cover_metadata.get("title", ""))
    title = f"{name}创新性评估报告"

    def prose(text):
        if not isinstance(text, str):
            return text
        if original_title:
            text = text.replace(original_title, title)
        for old in sorted(old_names, key=len, reverse=True):
            text = text.replace(old, name)
        label = {"paper": "论文", "project": "项目", "guideline": "指南"}.get(state.assessment_profile.artifact_type)
        if label:
            text = text.replace(f"本报告对“{name}”开展{label}内容评估。", f"评估对象为“{name}”，材料类型为{label}。")
        return text

    # Do not alter source excerpts, scientific comparisons, citation maps or charts.
    for section in projected.sections:
        if section.section_key not in {"executive", "scope"}:
            continue
        section.content = prose(section.content)
        for block in section.blocks:
            block.text = prose(block.text)
    projected.cover_metadata.update(title=title, assessment_object=name)
    context = projected.render_context
    for key in ("title", "assessment_object"):
        context.setdefault("assessment_profile", {})[key] = name
    context.setdefault("report_plan", {})["report_title"] = title
    context["report_plan"]["executive_summary"] = prose(context["report_plan"].get("executive_summary", ""))
    projected.source_snapshot_id = snapshot.source_snapshot_id or snapshot.snapshot_id
    projected.source_snapshot = snapshot.source_snapshot or snapshot.model_dump()
    projected.source_sections = snapshot.source_sections or deepcopy(snapshot.sections)
    projected.snapshot_id = "snapshot-" + hashlib.sha256(f"{snapshot.snapshot_id}:{PRESENTATION_VERSION}:{name}".encode()).hexdigest()[:20]
    projected.render_version = f"{snapshot.render_version}:{PRESENTATION_VERSION}"
    state.report_snapshot = projected
    state.assessment_profile.title = state.assessment_profile.assessment_object = name
    state.report_plan.report_title = title
    state.report_plan.executive_summary = prose(state.report_plan.executive_summary)
    state.output_markdown = prose(state.output_markdown)
    if isinstance(state.output_json.get("report_plan"), dict):
        state.output_json["report_plan"]["report_title"] = title
