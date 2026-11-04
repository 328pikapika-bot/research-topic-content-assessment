"""Deterministic reader projection; never change frozen assessment facts."""
import hashlib
import re

from app.common.retrieval.links import evidence_url, citation_urls

RENDER_VERSION = "result-layout-v3.1-links"
_DECLARATION = "报告仅用于已确认评估目的。原始材料、完整证据、逐字段矩阵及检索记录见同版本证据附件。"


def legacy_reference(text):
    """Resolve prose-only historical sources without querying mutable records."""
    addresses = {}
    for label, key in (("NSTL 详情", "nstl_url"), ("原文地址", "source_url"), ("DOI", "doi")):
        match = re.search(re.escape(label) + r"[：:]\s*([^；。\s]+)", text)
        if match:
            addresses[key] = match[1]
    url_match = re.search(r"https?://[^\s；。<>]+", text)
    if url_match:
        addresses["url"] = url_match[0]
    cleaned = re.sub(r"；?NSTL 详情[：:][^。；]*|；?原文地址[：:][^。；]*|定位[：:][^。]*。?", "", text)
    cleaned = re.sub(r"DOI[：:]未提供[；。]?", "", cleaned).strip()
    url = evidence_url(addresses)
    if url and not re.search(r"\]\(<?https?://", cleaned):
        prefix = re.match(r"\s*(\[\d+\]|[\w-]+)\s+", cleaned)
        label = cleaned[prefix.end():] if prefix else cleaned
        label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        cleaned = (prefix[0] if prefix else "") + f"[{label}](<{url}>)"
    return cleaned


def clean_text(text):
    lines = []
    for line in text.splitlines():
        if line.strip() in {_DECLARATION, "附录：检索记录与证据对照"}:
            continue
        if re.fullmatch(r"用户提供的技术描述；文件 ID user_material；内容校验\s*待补充[。.]?", line.strip()):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def display_snapshot(snapshot):
    from .research_outline import TEMPLATE, RENDER
    if snapshot.template_version in {TEMPLATE, "ContentAssessmentResearchReportV6"}:
        target_render = RENDER if snapshot.template_version == TEMPLATE else "research-layout-v6"
        if snapshot.source_snapshot_id:
            return snapshot
    else:
        target_render = "scholarly-layout-v1" if snapshot.template_version == "ContentAssessmentResultReportV5" else RENDER_VERSION
    if snapshot.render_version == target_render and (snapshot.template_version not in {TEMPLATE, "ContentAssessmentResearchReportV6", "ContentAssessmentResultReportV5"} or snapshot.source_snapshot_id):
        return snapshot
    from .report_composition import refresh_section
    from .schemas import ReportBlock
    projected = snapshot.model_copy(deep=True)
    records = {item["evidence_id"]: item for item in snapshot.audit_data.get("evidence_records", [])}
    urls = citation_urls(list(records.values()), snapshot.citation_numbers)
    for section in projected.sections:
        if not section.blocks and section.content:
            section.blocks = [ReportBlock(text=part) for part in re.split(r"\n\s*\n", section.content)]
        kept = []
        for block in section.blocks:
            block.text = clean_text(block.text)
            if not block.text and block.kind != "figure":
                continue
            if section.section_key == "references":
                eid = next((eid for eid in block.evidence_ids if eid in records), "")
                if not eid:
                    eid = next((eid for eid in records if re.search(r"(?<![\w-])" + re.escape(eid) + r"(?![\w-])", block.text)), "")
                if not eid:
                    number = re.match(r"\s*\[(\d+)\]", block.text)
                    eid = next((eid for eid in records if number and snapshot.citation_numbers.get(eid) == int(number[1])), "")
                record = records.get(eid)
                if record:
                    title = str(record.get("title") or "")
                    url = urls.get(str(snapshot.citation_numbers.get(eid)), "") or evidence_url(record)
                    label = title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                    title_text = f"[{label}](<{url}>)" if url and title else title
                    authors = record.get("authors") or []
                    authors = "、".join(authors) if isinstance(authors, list) else str(authors)
                    metadata = [str(value) for value in (authors, record.get("venue"), record.get("year")) if value]
                    if record.get("doi"):
                        metadata.append("DOI：" + record["doi"])
                    block.text = f"{eid} {title_text}。" + "，".join(metadata) + ("。" if metadata else "")
                else:
                    block.text = legacy_reference(block.text)
            kept.append(block)
        section.blocks = kept
        refresh_section(section)
    projected.render_version = target_render
    projected.snapshot_id = "snapshot-" + hashlib.sha256(f"{snapshot.snapshot_id}:{target_render}".encode()).hexdigest()[:20]
    projected.source_snapshot_id = snapshot.source_snapshot_id or snapshot.snapshot_id
    projected.source_snapshot = snapshot.source_snapshot or snapshot.model_dump()
    projected.source_sections = snapshot.source_sections or [s.model_copy(deep=True) for s in snapshot.sections]
    return projected
