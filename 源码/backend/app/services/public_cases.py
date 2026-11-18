from __future__ import annotations

import gzip
from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from functools import lru_cache

from fastapi import HTTPException


CASE_IDS = (
    "topic-urban-heat-v2",
    "topic-phage-v2",
    "topic-perovskite-v2",
    "assessment-platform",
    "assessment-drug",
    "assessment-battery-v2",
    "assessment-chip-v2",
    "assessment-standard",
    "assessment-cost",
)
CASE_SNAPSHOT_VERSION = "v3"
CASE_ROOT = Path(__file__).resolve().parents[1] / "public_cases"


@lru_cache(maxsize=9)
def _read_snapshot(case_id: str, modified_ns: int, size: int) -> dict[str, Any] | None:
    path = CASE_ROOT / "snapshots" / CASE_SNAPSHOT_VERSION / f"{case_id}.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if payload.get("case_id") != case_id or payload.get("version") != CASE_SNAPSHOT_VERSION:
            return None
        detail = payload.get("detail")
        if not isinstance(detail, dict) or not _complete_case(case_id, detail):
            return None
        return payload
    except (OSError, EOFError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _complete_case(case_id: str, detail: dict[str, Any]) -> bool:
    state = detail.get("state") or {}
    if detail.get("status") != "completed" or state.get("current_stage") != "completed":
        return False
    traces = detail.get("stage_trace") or state.get("stage_trace") or []
    expected_count = 8 if case_id.startswith("assessment-") else 9
    if len(traces) != expected_count or any(item.get("status") != "completed" for item in traces):
        return False
    if case_id.startswith("assessment-"):
        snapshot = state.get("report_snapshot") or {}
        if not detail.get("output_markdown") or not snapshot.get("snapshot_id") or not snapshot.get("sections"):
            return False
        evidence = state.get("evidence_records") or []
        citations = snapshot.get("citation_numbers") or {}
        return _citations_have_sources(evidence, citations)

    snapshot = state.get("report_snapshot") or {}
    report_plan = state.get("report_plan") or {}
    if (not detail.get("output_markdown") or not snapshot.get("frozen")
            or report_plan.get("publication_status") != "published" or not report_plan.get("sections")):
        return False
    evidence = state.get("evidence_pool") or []
    citation_map = report_plan.get("citation_map") or {}
    return _citations_have_sources(evidence, citation_map)


def _citations_have_sources(evidence: list[dict[str, Any]], citations: dict[str, Any]) -> bool:
    records = {
        str(item.get("evidence_id") or item.get("id") or ""): item
        for item in evidence
        if isinstance(item, dict)
    }
    cited_ids = {str(evidence_id) for evidence_id in citations}
    return bool(cited_ids) and cited_ids <= records.keys() and all(
        _has_source(records[evidence_id]) for evidence_id in cited_ids
    )


def _has_source(record: dict[str, Any]) -> bool:
    return any(str(record.get(key) or "").strip() for key in ("doi", "url", "source_url", "nstl_url", "paper_url"))


def case_catalog() -> dict[str, Any]:
    snapshots = {}
    for case_id in CASE_IDS:
        path = CASE_ROOT / "snapshots" / CASE_SNAPSHOT_VERSION / f"{case_id}.json.gz"
        try:
            stat = path.stat()
            snapshots[case_id] = _read_snapshot(case_id, stat.st_mtime_ns, stat.st_size)
        except OSError:
            snapshots[case_id] = None
    try:
        manifest = json.loads((CASE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    manifest_valid = (
        manifest.get("published") is True
        and manifest.get("version") == CASE_SNAPSHOT_VERSION
        and manifest.get("case_ids") == list(CASE_IDS)
    )
    ready = manifest_valid and all(snapshots.values())
    return {
        "success": True,
        "ready": ready,
        "version": CASE_SNAPSHOT_VERSION,
        "expected_count": len(CASE_IDS),
        "published_case_ids": list(CASE_IDS) if ready else [],
    }


def get_case(case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if case_id not in CASE_IDS:
        raise HTTPException(status_code=404, detail="案例不存在")
    catalog = case_catalog()
    if not catalog["ready"]:
        raise HTTPException(status_code=503, detail="案例报告正在准备，完整发布后即可查看")
    path = CASE_ROOT / "snapshots" / CASE_SNAPSHOT_VERSION / f"{case_id}.json.gz"
    try:
        stat = path.stat()
    except OSError:
        stat = None
    payload = _read_snapshot(case_id, stat.st_mtime_ns, stat.st_size) if stat else None
    if payload is None:
        raise HTTPException(status_code=503, detail="案例报告暂不可用")
    # Frozen research content remains intact. Apply current citation typography
    # to the presentation copy so existing cases match newly exported reports.
    from app.services.report_markdown import normalize_saved_citations
    presented = deepcopy(payload)
    detail = presented["detail"]
    for owner in (detail, detail.get("state") or {}):
        if isinstance(owner.get("output_markdown"), str):
            owner["output_markdown"] = normalize_saved_citations(owner["output_markdown"])
    return presented, detail
