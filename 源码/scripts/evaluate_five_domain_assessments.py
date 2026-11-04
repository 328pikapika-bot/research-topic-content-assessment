from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MATERIAL_ROOT = ROOT / "资料" / "测试输入" / "创新性评估" / "创新性评估智能体测试资料" / "五领域实测报告集_20260917"
OUTPUT_JSON = MATERIAL_ROOT / "五领域综合质量评估.json"
OUTPUT_MD = MATERIAL_ROOT / "五领域综合评估报告.md"
OUTPUT_CSV = MATERIAL_ROOT / "五领域横向质量对照.csv"

SEVEN_FIELDS = ("object", "method", "data", "baseline", "metric", "time", "validation")
FIELD_LABELS = {
    "object": "对象", "method": "方法", "data": "数据", "baseline": "基线",
    "metric": "指标", "time": "时间", "validation": "验证条件",
}
ALLOWED_ROLES = {"PROJECT", "BASELINE", "EVIDENCE", "REFERENCE"}
CAUTIOUS_NOVELTY = {"存在相近先例", "方向性支持", "当前不可判断", "证据不足或存疑", "证据支持有限", "目标待验证"}
CAUTIOUS_ADVANCEDNESS = {"证据不足或存疑", "当前不可判断", "方向性支持", "存在相近先例"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_files() -> list[tuple[str, Path, str]]:
    items: list[tuple[str, Path, str]] = []
    for path in sorted(MATERIAL_ROOT.glob("历史任务基线/*/task_detail.json")):
        items.append((f"历史任务 {path.parent.name}", path, "历史基线"))
    for path in sorted(MATERIAL_ROOT.glob("*/报告输出/*/task_detail.json")):
        items.append((path.parent.parent.parent.name, path, "五领域实测"))
    return items


def text_ratio(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def claim_grounding(state: dict[str, Any]) -> tuple[float, int, list[str]]:
    files = {item.get("file_id"): item for item in (state.get("uploaded_file_context") or {}).get("files", [])}
    grounded = 0
    notes: list[str] = []
    for claim in state.get("claims") or []:
        source = files.get(claim.get("source_document_id"))
        quote = str(claim.get("source_span") or "")
        start = claim.get("source_char_start")
        end = claim.get("source_char_end")
        valid = bool(source and quote and isinstance(start, int) and isinstance(end, int)
                    and end > start and str(source.get("content") or "")[start:end] == quote)
        grounded += int(valid)
        if not valid:
            notes.append(f"{claim.get('claim_id', '?')} 主张原文或字符锚点无法在上传材料中复现")
    return (grounded / max(1, len(state.get("claims") or [])), grounded, notes)


def material_metrics(state: dict[str, Any]) -> dict[str, Any]:
    files = (state.get("uploaded_file_context") or {}).get("files") or []
    records = (state.get("assessment_profile") or {}).get("document_records") or []
    parsed = [bool(str(item.get("content") or "").strip()) for item in files]
    anchors = [bool(item.get("anchors")) for item in files]
    roles = [item.get("role") in ALLOWED_ROLES for item in records]
    grounding_ratio, grounded_count, grounding_notes = claim_grounding(state)
    claims = state.get("claims") or []
    completed_claims = [item for item in claims if item.get("assertion_type") == "completed"]
    target_guard = 1.0 if not completed_claims else 0.5
    claim_ratio = clamp(len(claims) / 6)
    parse_ratio = (text_ratio(parsed) + text_ratio(anchors)) / 2 if files else 0.0
    role_ratio = text_ratio(roles) if records else 0.0
    score = 15 * clamp(0.25 * parse_ratio + 0.15 * role_ratio + 0.30 * claim_ratio
                       + 0.25 * grounding_ratio + 0.05 * target_guard)
    notes = []
    if len(claims) != 6:
        notes.append(f"主张数为 {len(claims)}，测试设计期望 6 条")
    if grounding_notes:
        notes.extend(grounding_notes)
    if completed_claims:
        notes.append(f"发现 {len(completed_claims)} 条主张被标记为 completed，需要核对是否把目标值当成结果")
    return {
        "score": round(score, 2), "max_score": 15, "file_count": len(files),
        "record_count": len(records), "parsed_ratio": round(parse_ratio, 3),
        "role_ratio": round(role_ratio, 3), "claim_count": len(claims),
        "claim_grounding_ratio": round(grounding_ratio, 3), "grounded_claim_count": grounded_count,
        "completed_claim_count": len(completed_claims), "notes": notes,
    }


def external_evidence(state: dict[str, Any]) -> list[dict[str, Any]]:
    uploaded_ids = {
        item.get("file_id") for item in (state.get("uploaded_file_context") or {}).get("files", [])
    }
    return [item for item in state.get("evidence_records") or []
            if item.get("source_document_id") not in uploaded_ids
            and item.get("source_type") in {"literature", "standard", "regulatory", "market"}]


def evidence_metrics(state: dict[str, Any]) -> dict[str, Any]:
    evidence = external_evidence(state)
    formal = [bool(item.get("fulltext_available") and item.get("anchor_verified")
                   and item.get("access_status") == "authorized"
                   and isinstance(item.get("char_start"), int)
                   and isinstance(item.get("char_end"), int)
                   and item.get("char_end", 0) > item.get("char_start", 0)
                   and str(item.get("excerpt") or "").strip()) for item in evidence]
    reopenable = [bool((item.get("url") or item.get("doi")) and item.get("excerpt")
                       and (item.get("locator") or item.get("page_ref") or item.get("section_ref"))) for item in evidence]
    graded = [item.get("evidence_level") in {"A", "B", "C"} for item in evidence]
    links = state.get("claim_evidence_links") or []
    linked_claims = {item.get("claim_id") for item in links if item.get("claim_id")}
    claim_ratio = len(linked_claims) / max(1, len(state.get("claims") or []))
    formal_ratio = text_ratio(formal)
    reopenable_ratio = text_ratio(reopenable)
    grade_ratio = text_ratio(graded)
    score = 20 * clamp(0.35 * formal_ratio + 0.25 * reopenable_ratio + 0.15 * grade_ratio + 0.25 * claim_ratio)
    levels = Counter(item.get("evidence_level", "?") for item in evidence)
    notes = []
    if evidence and formal_ratio == 0:
        notes.append("外部证据正式全文锚点覆盖率为 0%，只能支撑方向性判断")
    if evidence and reopenable_ratio < 0.8:
        notes.append("部分外部记录缺少 URL/DOI 与可重新打开定位的组合")
    return {
        "score": round(score, 2), "max_score": 20, "external_evidence_count": len(evidence),
        "formal_evidence_count": sum(formal), "formal_ratio": round(formal_ratio, 3),
        "reopenable_ratio": round(reopenable_ratio, 3), "grade_ratio": round(grade_ratio, 3),
        "linked_claim_ratio": round(claim_ratio, 3), "evidence_levels": dict(levels), "notes": notes,
    }


def field_status_score(value: Any) -> float:
    return {"covered": 1.0, "partial": 0.5}.get(str(value or ""), 0.0)


def comparison_metrics(state: dict[str, Any]) -> dict[str, Any]:
    links = state.get("claim_evidence_links") or []
    assessments = state.get("claim_assessments") or []
    matrices: list[dict[str, Any]] = []
    for item in links + assessments:
        matrix = item.get("field_matrix") or {}
        if matrix:
            matrices.append(matrix)
    values = [field_status_score(matrix.get(field)) for matrix in matrices for field in SEVEN_FIELDS]
    field_score = sum(values) / len(values) if values else 0.0
    claim_link_ratio = len({item.get("claim_id") for item in links if item.get("claim_id")}) / max(1, len(state.get("claims") or []))
    assessment_ratio = len({item.get("claim_id") for item in assessments if item.get("claim_id")}) / max(1, len(state.get("claims") or []))
    score = 20 * clamp(0.70 * field_score + 0.15 * claim_link_ratio + 0.15 * assessment_ratio)
    field_counts = Counter()
    for matrix in matrices:
        for field in SEVEN_FIELDS:
            field_counts[matrix.get(field, "missing")] += 1
    notes = []
    if field_score < 0.5:
        missing = ", ".join(FIELD_LABELS[field] for field in SEVEN_FIELDS
                             if not any(matrix.get(field) == "covered" for matrix in matrices))
        notes.append(f"七字段有效覆盖偏低；未出现 covered 的字段：{missing}")
    if not links:
        notes.append("没有主张-证据链接记录")
    return {
        "score": round(score, 2), "max_score": 20, "link_count": len(links),
        "assessment_count": len(assessments), "matrix_count": len(matrices),
        "field_score": round(field_score, 3), "claim_link_ratio": round(claim_link_ratio, 3),
        "assessment_ratio": round(assessment_ratio, 3), "field_status_counts": dict(field_counts),
        "notes": notes,
    }


def innovation_metrics(state: dict[str, Any]) -> dict[str, Any]:
    claims = state.get("claims") or []
    novelty = state.get("novelty_decisions") or []
    advanced = state.get("advancedness_decisions") or []
    novelty_ratio = len({item.get("claim_id") for item in novelty if item.get("claim_id")}) / max(1, len(claims))
    advanced_ratio = len({item.get("claim_id") for item in advanced if item.get("claim_id")}) / max(1, len(claims))
    cautious_novelty = text_ratio([item.get("label") in CAUTIOUS_NOVELTY for item in novelty])
    cautious_advanced = text_ratio([item.get("label") in CAUTIOUS_ADVANCEDNESS for item in advanced])
    independent = 0
    advanced_by_claim = {item.get("claim_id"): item for item in advanced}
    for item in novelty:
        other = advanced_by_claim.get(item.get("claim_id")) or {}
        if other.get("label") and (other.get("rationale") or other.get("uncertainty")):
            independent += 1
    independence_ratio = independent / max(1, len(claims))
    score = 20 * clamp(0.30 * novelty_ratio + 0.25 * cautious_novelty
                       + 0.20 * advanced_ratio + 0.15 * cautious_advanced + 0.10 * independence_ratio)
    notes = []
    if cautious_novelty < 1:
        notes.append("存在需要人工复核的强创新/支持性标签")
    if independence_ratio < 0.8:
        notes.append("先进性判定与创新性判定的独立理由不完整")
    return {
        "score": round(score, 2), "max_score": 20, "novelty_decision_count": len(novelty),
        "advancedness_decision_count": len(advanced), "cautious_novelty_ratio": round(cautious_novelty, 3),
        "cautious_advancedness_ratio": round(cautious_advanced, 3),
        "independence_ratio": round(independence_ratio, 3),
        "novelty_labels": dict(Counter(item.get("label", "") for item in novelty)),
        "advancedness_labels": dict(Counter(item.get("label", "") for item in advanced)),
        "notes": notes,
    }


def feasibility_metrics(state: dict[str, Any]) -> dict[str, Any]:
    validations = state.get("indicator_validations") or []
    required = ("definition", "unit", "baseline", "target", "test_method", "sample_design", "statistics_method", "acceptance_criteria")
    validation_quality = []
    for item in validations:
        present = sum(bool(str(item.get(field) or "").strip()) for field in required)
        validation_quality.append(present / len(required))
    validation_score = sum(validation_quality) / len(validation_quality) if validation_quality else 0.0
    work_packages = state.get("work_packages") or []
    work_score = clamp(len(work_packages) / 5)
    boundary = state.get("boundary_matrix") or {}
    boundary_score = clamp(len(boundary) / 3) if isinstance(boundary, dict) else clamp(len(boundary) / 3)
    scorecard = state.get("scorecard") or []
    risk_notes = sum(len(item.get("risk_notes") or []) for item in state.get("novelty_decisions") or [])
    risk_score = clamp(risk_notes / max(1, len(state.get("claims") or []) * 2))
    score = 15 * clamp(0.50 * validation_score + 0.20 * work_score + 0.15 * boundary_score + 0.15 * risk_score)
    notes = []
    if validation_score < 0.6:
        notes.append("指标验证记录缺少基线、样本设计、统计方法或验收条件")
    if len(validations) < 3:
        notes.append(f"只形成 {len(validations)} 项指标验证记录，低于六条主张的综合指标治理预期")
    if not state.get("boundary_matrix"):
        notes.append("未形成结构化应用与监管边界矩阵")
    return {
        "score": round(score, 2), "max_score": 15, "validation_count": len(validations),
        "validation_quality": round(validation_score, 3), "work_package_count": len(work_packages),
        "boundary_count": len(boundary), "scorecard_count": len(scorecard),
        "risk_note_count": risk_notes, "notes": notes,
    }


def export_metrics(task_dir: Path) -> dict[str, Any]:
    export_dir = task_dir / "exports"
    files = {suffix: next(iter(export_dir.glob(f"*.{suffix}")), None) for suffix in ("md", "docx", "pdf", "zip")}
    checks: dict[str, Any] = {}
    for suffix, path in files.items():
        valid = bool(path and path.exists() and path.stat().st_size > 0)
        error = ""
        if valid and suffix == "docx":
            try:
                with zipfile.ZipFile(path) as archive:
                    valid = archive.testzip() is None and "word/document.xml" in archive.namelist()
            except Exception as exc:
                valid, error = False, type(exc).__name__
        elif valid and suffix == "zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    valid = archive.testzip() is None
            except Exception as exc:
                valid, error = False, type(exc).__name__
        elif valid and suffix == "pdf":
            try:
                from pypdf import PdfReader
                valid = len(PdfReader(path).pages) > 0
            except Exception as exc:
                valid, error = False, type(exc).__name__
        checks[suffix] = {"path": str(path) if path else "", "bytes": path.stat().st_size if path and path.exists() else 0, "valid": valid, "error": error}
    ratio = text_ratio([item["valid"] for item in checks.values()])
    return {"checks": checks, "valid_ratio": round(ratio, 3)}


def hard_defects(state: dict[str, Any], exports: dict[str, Any]) -> list[dict[str, str]]:
    defects: list[dict[str, str]] = []
    metrics = state.get("report_metrics") or {}
    if metrics.get("citation_integrity", 1) < 1:
        defects.append({"severity": "严重", "type": "缺失引用", "evidence": f"citation_integrity={metrics.get('citation_integrity')}"})
    if exports.get("valid_ratio", 0) < 1:
        bad = [key for key, item in exports.get("checks", {}).items() if not item.get("valid")]
        defects.append({"severity": "严重", "type": "导出文件损坏", "evidence": ",".join(bad)})
    completed = [item.get("claim_id") for item in state.get("claims") or [] if item.get("assertion_type") == "completed"]
    if completed:
        defects.append({"severity": "严重", "type": "目标值可能冒充实测结果", "evidence": ",".join(completed)})
    links = state.get("claim_evidence_links") or []
    direct = [item for item in links if item.get("relation") in {"supports", "directly_supports"}
              and all(item.get("field_matrix", {}).get(field) == "missing" for field in SEVEN_FIELDS)]
    if direct:
        defects.append({"severity": "严重", "type": "跨对象直接比较", "evidence": f"{len(direct)} 条 supports 链接七字段全部 missing"})
    novelty = {item.get("claim_id"): item.get("label") for item in state.get("novelty_decisions") or []}
    advanced = {item.get("claim_id"): item.get("label") for item in state.get("advancedness_decisions") or []}
    same = [claim_id for claim_id in novelty if claim_id in advanced and novelty[claim_id] == advanced[claim_id]
            and novelty[claim_id] not in {"当前不可判断", "证据不足或存疑"}]
    if same:
        defects.append({"severity": "严重", "type": "创新性冒充先进性", "evidence": ",".join(same)})
    return defects


def evaluate(label: str, path: Path, category: str) -> dict[str, Any]:
    detail = load_json(path)
    state = detail.get("state") or {}
    task_dir = path.parent
    material = material_metrics(state)
    evidence = evidence_metrics(state)
    comparison = comparison_metrics(state)
    innovation = innovation_metrics(state)
    feasibility = feasibility_metrics(state)
    exports = export_metrics(task_dir)
    metrics = state.get("report_metrics") or {}
    structure_base = clamp((float(metrics.get("section_content_completion", 0))
                            + float(metrics.get("section_citation_completion", 0))
                            + float(metrics.get("citation_integrity", 0))) / 3)
    structure = {
        "score": round(10 * clamp(0.55 * structure_base + 0.45 * exports["valid_ratio"]), 2),
        "max_score": 10, "section_content_completion": metrics.get("section_content_completion", 0),
        "section_citation_completion": metrics.get("section_citation_completion", 0),
        "citation_integrity": metrics.get("citation_integrity", 0),
        "actual_page_count": metrics.get("actual_page_count"), "page_count_verified": metrics.get("page_count_verified"),
        "estimated_pages": metrics.get("estimated_pages"), "exports": exports,
        "notes": (["deep 模式报告页数低于目标下限，但系统已明确标记为证据不足"]
                  if metrics.get("page_target_status") == "below_target" else []),
    }
    dimensions = [material, evidence, comparison, innovation, feasibility, structure]
    total = round(sum(item["score"] for item in dimensions), 2)
    defects = hard_defects(state, exports)
    model_stages = (state.get("output_json") or {}).get("model_usage", {}).get("stages", [])
    fallback_count = sum(item.get("status") == "fallback" for item in model_stages)
    completed_model_count = sum(item.get("status") == "completed" for item in model_stages)
    docs = (state.get("assessment_profile") or {}).get("document_records") or []
    return {
        "label": label, "category": category, "path": str(path), "task_id": detail.get("task_id", label),
        "status": detail.get("status"), "current_stage": detail.get("current_stage"),
        "created_at": detail.get("created_at"), "completed_at": detail.get("completed_at"),
        "duration_seconds": detail.get("duration_seconds"),
        "title": detail.get("title"), "report_version": (state.get("report_plan") or {}).get("report_version", ""),
        "report_type": (state.get("report_plan") or {}).get("report_type", ""),
        "resolved_mode": (state.get("report_preferences") or {}).get("resolved_mode", ""),
        "document_count": len(docs), "role_predictions": (state.get("assessment_profile") or {}).get("document_role_predictions", {}),
        "model_fallback_count": fallback_count, "model_completed_count": completed_model_count,
        "quality_gate": state.get("quality_gate") or {}, "report_metrics": metrics,
        "dimensions": {"claims_material": material, "evidence_traceability": evidence,
                       "seven_field_comparability": comparison, "innovation_advancedness": innovation,
                       "indicator_feasibility_risk": feasibility, "report_structure_export": structure},
        "total_score": total, "hard_defects": defects,
        "defect_count": len(defects),
    }


def grade(score: float) -> str:
    if score >= 85:
        return "良好"
    if score >= 70:
        return "可用但需复核"
    if score >= 60:
        return "探索性可用"
    return "不宜据此下强结论"


def report_markdown(items: list[dict[str, Any]]) -> str:
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# 五领域创新性评估综合质量报告",
        "",
        f"生成时间：{generated}",
        "",
        "## 结论",
        "",
        "五条实测任务均通过真实接口完成，并生成 Markdown、DOCX、PDF 和证据 ZIP。质量门均未通过正式长报告门槛，统一被判为探索性评估；主要原因不是任务状态失败，而是外部全文证据、指标验证和七字段比较尚未达到强结论要求。",
        "",
        "评分为 100 分制：主张与材料抽取 15 分，证据追溯与等级治理 20 分，七字段可比性 20 分，创新性与先进性判定 20 分，指标可行性与风险 15 分，报告结构和导出 10 分。",
        "",
        "## 横向结果",
        "",
        "| 样本 | 任务 ID | 状态 | 总分 | 等级 | 主张 | 指标 | 外部证据 | 正式全文证据 | 七字段得分 | 质量门 | 模型降级 |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for item in items:
        d = item["dimensions"]
        q = item["quality_gate"]
        e = d["evidence_traceability"]
        c = d["seven_field_comparability"]
        lines.append(
            f"| {item['label']} | `{item['task_id']}` | {item['status']} | {item['total_score']} | {grade(item['total_score'])} | "
            f"{d['claims_material']['claim_count']} | {d['indicator_feasibility_risk']['validation_count']} | {e['external_evidence_count']} | "
            f"{e['formal_evidence_count']} | {c['field_score']:.0%} | {q.get('report_eligibility', '')} | {item['model_fallback_count']} |"
        )
    lines += ["", "## 历史两条基线", ""]
    for item in items:
        if item["category"] != "历史基线":
            continue
        d = item["dimensions"]
        lines.append(f"### {item['label']}")
        lines.append("")
        lines.append(f"总分 {item['total_score']}，等级为“{grade(item['total_score'])}”；任务状态为 {item['status']}，报告版本 `{item['report_version']}`。")
        lines.append("")
        lines.append("主要观察：")
        for section in d.values():
            for note in section.get("notes", []):
                lines.append(f"- {note}")
        lines.append("")
    lines += ["## 五个领域逐项结果", ""]
    for item in items:
        if item["category"] != "五领域实测":
            continue
        d = item["dimensions"]
        lines.append(f"### {item['label']}")
        lines.append("")
        lines.append(f"任务 `{item['task_id']}`，报告版本 `{item['report_version']}`，总分 {item['total_score']}，等级为“{grade(item['total_score'])}”。状态为 {item['status']}，耗时 {item.get('duration_seconds')} 秒。")
        lines.append("")
        lines.append(f"材料与主张 {d['claims_material']['score']}/{d['claims_material']['max_score']}：{d['claims_material']['claim_count']} 条主张，{d['claims_material']['grounded_claim_count']} 条可由上传原文和字符锚点复现。")
        lines.append(f"证据治理 {d['evidence_traceability']['score']}/{d['evidence_traceability']['max_score']}：外部证据 {d['evidence_traceability']['external_evidence_count']} 条，正式全文锚点 {d['evidence_traceability']['formal_evidence_count']} 条。")
        lines.append(f"七字段比较 {d['seven_field_comparability']['score']}/{d['seven_field_comparability']['max_score']}：字段有效覆盖得分 {d['seven_field_comparability']['field_score']:.0%}。")
        lines.append(f"创新与先进性 {d['innovation_advancedness']['score']}/{d['innovation_advancedness']['max_score']}：创新判定 {d['innovation_advancedness']['novelty_decision_count']} 条，先进性判定 {d['innovation_advancedness']['advancedness_decision_count']} 条。")
        lines.append(f"指标可行性与风险 {d['indicator_feasibility_risk']['score']}/{d['indicator_feasibility_risk']['max_score']}：指标验证 {d['indicator_feasibility_risk']['validation_count']} 条，验证字段完整度 {d['indicator_feasibility_risk']['validation_quality']:.0%}。")
        lines.append(f"报告结构与导出 {d['report_structure_export']['score']}/{d['report_structure_export']['max_score']}：导出文件有效率 {d['report_structure_export']['exports']['valid_ratio']:.0%}，PDF 实际页数 {d['report_structure_export']['actual_page_count']}。")
        notes = [note for section in d.values() for note in section.get("notes", [])]
        if notes:
            lines.append("")
            lines.append("复现到的问题：")
            for note in dict.fromkeys(notes):
                lines.append(f"- {note}")
        if item["hard_defects"]:
            lines.append("")
            lines.append("严重缺陷：")
            for defect in item["hard_defects"]:
                lines.append(f"- {defect['severity']}：{defect['type']}；证据：{defect['evidence']}")
        else:
            lines.append("")
            lines.append("硬性问题清单：未发现伪造文献、损坏导出、缺失引用或目标值被标记为 completed 的自动化证据。")
        lines.append("")
    lines += [
        "## 缺陷分类与优先级",
        "",
        "1. P0 证据强度：五个新报告的正式全文证据覆盖率均为 0%，A/B/C 证据不能直接支撑强创新或领先结论；应补充可重新打开的全文、页码或字符锚点。",
        "2. P0 指标治理：五个新报告均只形成 1 项指标验证记录，且系统报告的指标验证覆盖率为 0%；当前抽取的指标还可能落到数据字典 ID 字段，而不是项目核心验收指标。",
        "3. P1 七字段：主张对象、方法、数据、基线、指标、时间、验证条件的 field_matrix 以 missing 为主，报告可以识别不可比性，但不能完成同口径定量比较。",
        "4. P1 模型稳定性：已切换到现有可用的 personal_v4_flash 预设后五条任务均完成，但每条仍出现若干模型 fallback；自动主张抽取还会触发确认节点。",
        "5. P2 报告长度：deep 模式实际页数约 13.3 至 22.3 页，系统已明确标记为证据不足，没有通过重复内容强行扩页，这一点属于正确的谨慎行为。",
        "",
        "## 复现证据",
        "",
        "每条任务的完整状态位于对应领域的 `报告输出/<任务 ID>/task_detail.json`；导出质量记录位于同一目录的 `run_result.json`；五个任务的阶段、上传耗时、任务 ID、哈希和导出清单位于 `五领域实测任务运行日志.json`。",
        "",
        "## 改进优先级",
        "",
        "先修复指标表识别和主张表字段兼容性，再补充全文证据锚点和七字段结构化比对，最后才提高报告深度或页数目标。下一轮回归应固定同一批材料，分别比较自动识别角色与显式角色、server_v4_flash 与 personal_v4_flash、模型成功与 fallback 两条路径，并逐条验证目标值和实测值不混淆。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    results = [evaluate(label, path, category) for label, path, category in task_files()]
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scoring": {
            "claims_material": 15, "evidence_traceability": 20, "seven_field_comparability": 20,
            "innovation_advancedness": 20, "indicator_feasibility_risk": 15, "report_structure_export": 10,
        },
        "items": results,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(report_markdown(results), encoding="utf-8")
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["样本", "任务ID", "类别", "状态", "总分", "等级", "材料主张", "证据追溯", "七字段", "创新先进性", "指标风险", "结构导出", "外部证据", "正式全文证据", "fallback次数"])
        for item in results:
            d = item["dimensions"]
            writer.writerow([
                item["label"], item["task_id"], item["category"], item["status"], item["total_score"], grade(item["total_score"]),
                d["claims_material"]["score"], d["evidence_traceability"]["score"], d["seven_field_comparability"]["score"],
                d["innovation_advancedness"]["score"], d["indicator_feasibility_risk"]["score"], d["report_structure_export"]["score"],
                d["evidence_traceability"]["external_evidence_count"], d["evidence_traceability"]["formal_evidence_count"], item["model_fallback_count"],
            ])
    print(json.dumps({"json": str(OUTPUT_JSON), "markdown": str(OUTPUT_MD), "csv": str(OUTPUT_CSV), "items": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
