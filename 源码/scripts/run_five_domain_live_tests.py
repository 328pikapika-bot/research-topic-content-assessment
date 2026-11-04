from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


BASE_URL = "http://127.0.0.1:17607"
USER_SCOPE = "platform_admin"
ROOT = Path(__file__).resolve().parents[2]
MATERIAL_ROOT = ROOT / "资料" / "测试输入" / "创新性评估" / "创新性评估智能体测试资料" / "五领域实测报告集_20260917"
LOG_PATH = MATERIAL_ROOT / "五领域实测任务运行日志.json"
TASK_CSV = MATERIAL_ROOT / "五领域任务清单.csv"
REQUEST_TIMEOUT = 180
POLL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 720

DOMAIN_FOLDERS = [
    "01_新能源_钠离子电池低温快充预警",
    "02_新材料_可回收动态共价环氧复合材料",
    "03_生物医药_肿瘤类器官免疫共培养药敏",
    "04_人工智能_科研文献多模态知识发现",
    "05_先进制造_激光粉末床熔融闭环监测",
]


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def headers() -> dict[str, str]:
    return {"X-User-Scope": USER_SCOPE}


def json_headers() -> dict[str, str]:
    return {**headers(), "Content-Type": "application/json"}


def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    if not response.ok:
        raise RuntimeError(f"{method} {url} failed {response.status_code}: {response.text[:1000]}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {url} returned non-JSON: {response.text[:500]}") from exc


def save_bytes(response: requests.Response, path: Path) -> dict[str, Any]:
    if not response.ok:
        raise RuntimeError(f"export failed {response.status_code}: {response.text[:1000]}")
    path.write_bytes(response.content)
    return {"path": str(path), "bytes": len(response.content), "sha256": hashlib.sha256(response.content).hexdigest()}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(folder: Path) -> list[Path]:
    ordered = [
        "00_测试说明与提示词.md",
        next((p.name for p in folder.glob("01_项目申报书_*.docx")), ""),
        next((p.name for p in folder.glob("02_技术路线与实施方案_*.docx")), ""),
        next((p.name for p in folder.glob("03_实验数据与统计表_*.xlsx")), ""),
        next((p.name for p in folder.glob("04_文献证据与主张对照矩阵_*.xlsx")), ""),
        next((p.name for p in folder.glob("05_应用场景与合规边界说明_*.docx")), ""),
        "06_摘要级和题录级补充证据.txt",
    ]
    result = []
    for name in ordered:
        path = folder / name
        if not name or not path.exists():
            raise FileNotFoundError(f"missing material in {folder}: {name}")
        result.append(path)
    return result


def confirmed_claims_from_uploaded_state(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Build exact, source-grounded claims for the real confirmation path.

    The fixture table intentionally uses the human-readable header "原子主张",
    while the current deterministic parser recognizes only "主张原文" and
    "ClaimText". When model extraction pauses for confirmation, use the exact
    uploaded parser text and submit the claims through the public confirmation
    endpoint rather than altering the task state or database.
    """
    files = ((detail.get("state") or {}).get("uploaded_file_context") or {}).get("files") or []
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(r"(?m)^\s*(C\d+)\s*\|\s*([^|\r\n]+?)\s*\|\s*(target|planned|completed|unknown)\s*\|")
    for source in files:
        content = str(source.get("content") or "")
        for match in pattern.finditer(content):
            claim_number, quote, status = match.groups()
            quote = quote.strip()
            if not quote or quote in seen:
                continue
            seen.add(quote)
            start = content.find(quote, match.start())
            end = start + len(quote)
            anchor = next((item for item in source.get("anchors") or []
                           if isinstance(item.get("char_start"), int)
                           and item.get("char_start", -1) <= start
                           and item.get("char_end", 0) >= end), None) or {}
            locator = dict(anchor.get("locator") or {})
            if not locator:
                locator = {"kind": "text", "line": content[:start].count("\n") + 1}
            claims.append({
                "claim_id": f"C-{len(claims) + 1:03d}",
                "text": quote,
                "claim_type": "technical",
                "assertion_type": status,
                "source_document_id": source.get("file_id", ""),
                "source_span": quote,
                "source_char_start": start,
                "source_char_end": end,
                "source_locator": locator,
                "source_page_ref": str(locator.get("page", "")),
                "source_paragraph_ref": str(locator.get("paragraph", locator.get("row", locator.get("line", "")))),
                "searchable_terms": [word for word in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9-]{2,}", quote)[:8]],
                "claim_fields": {},
            })
    return claims[:6]


def role_for(path: Path) -> str:
    if path.name.startswith("01_项目申报书"):
        return "PROJECT"
    if path.name.startswith("02_技术路线"):
        return "BASELINE"
    if path.name.startswith("03_实验数据"):
        return "EVIDENCE"
    if path.name.startswith("04_文献证据"):
        return "REFERENCE"
    if path.name.startswith("05_应用场景"):
        return "REFERENCE"
    if path.name.startswith("06_"):
        return "EVIDENCE"
    return "REFERENCE"


def get_task(task_id: str) -> dict[str, Any]:
    return request_json("GET", f"{BASE_URL}/v1/content-assessment/tasks/{task_id}", headers=headers())


def export_task(task_id: str, output_dir: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exports = []
    for fmt, filename, params in (
        ("md", "报告.md", {"format": "md", "artifact": "report"}),
        ("docx", "报告.docx", {"format": "docx", "artifact": "report"}),
        ("pdf", "报告.pdf", {"format": "pdf", "artifact": "report"}),
        ("zip", "证据包.zip", {"format": "zip", "artifact": "evidence"}),
    ):
        response = requests.get(
            f"{BASE_URL}/v1/content-assessment/tasks/{task_id}/export",
            params=params,
            headers=headers(),
            timeout=REQUEST_TIMEOUT,
        )
        record = save_bytes(response, output_dir / filename)
        record.update({"format": fmt, "artifact": params["artifact"]})
        exports.append(record)
    return exports


def snapshot_historical(task_ids: list[str], root_log: dict[str, Any]) -> None:
    baseline_root = MATERIAL_ROOT / "历史任务基线"
    baseline_root.mkdir(parents=True, exist_ok=True)
    root_log["historical_baselines"] = []
    for task_id in task_ids:
        detail = get_task(task_id)
        target = baseline_root / task_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "task_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        exports = export_task(task_id, target / "exports")
        state = detail.get("state") or {}
        root_log["historical_baselines"].append({
            "task_id": task_id,
            "status": detail.get("status"),
            "title": detail.get("title"),
            "created_at": detail.get("created_at"),
            "report_version": (state.get("report_plan") or {}).get("report_version", ""),
            "claim_count": len(state.get("claims") or []),
            "indicator_count": len(state.get("indicators") or []),
            "evidence_count": len(state.get("evidence_records") or []),
            "quality_gate": state.get("quality_gate") or {},
            "report_metrics": state.get("report_metrics") or {},
            "exports": exports,
        })


def upload_material(path: Path, log: dict[str, Any]) -> dict[str, Any]:
    payload = path.read_bytes()
    started = time.perf_counter()
    with path.open("rb") as stream:
        response = requests.post(
            f"{BASE_URL}/v1/files/upload",
            headers=headers(),
            files={"file": (path.name, stream, "application/octet-stream")},
            timeout=REQUEST_TIMEOUT,
        )
    if not response.ok:
        raise RuntimeError(f"upload failed {path.name} {response.status_code}: {response.text[:1000]}")
    body = response.json()
    data = body.get("data") or {}
    record = {
        "local_path": str(path),
        "name": path.name,
        "role": role_for(path),
        "local_bytes": len(payload),
        "local_sha256": file_hash(path),
        "upload_seconds": round(time.perf_counter() - started, 3),
        "response": data,
    }
    log.append(record)
    return data


def confirm_if_needed(task_id: str, detail: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    state = detail.get("state") or {}
    confirmation = state.get("confirmation") or {}
    if detail.get("status") != "awaiting_confirmation" and not confirmation.get("needed"):
        return detail
    options = confirmation.get("options") or []
    values = {item.get("value") for item in options if isinstance(item, dict)}
    claims = state.get("claims") or []
    if "confirm_claims" in values:
        confirmed = claims if len(claims) >= 6 else confirmed_claims_from_uploaded_state(detail)
        if len(confirmed) >= 6:
            body = {"selected_option": "confirm_claims", "user_note": "测试运行按已上传材料逐字定位的六条原子主张继续。", "confirmed_claims": confirmed}
        else:
            events.append({"time": now(), "event": "confirmation_blocked", "options": options, "claim_count": len(claims), "grounded_claim_count": len(confirmed)})
            return detail
    elif "retry_extraction" in values:
        body = {"selected_option": "retry_extraction", "user_note": "测试运行要求按同一批材料重试主张抽取。"}
    else:
        events.append({"time": now(), "event": "confirmation_blocked", "options": options, "claim_count": len(claims)})
        return detail
    started = time.perf_counter()
    response = request_json("POST", f"{BASE_URL}/v1/content-assessment/tasks/{task_id}/confirmation", headers=json_headers(), json=body)
    events.append({"time": now(), "event": "confirmation_submitted", "body": {"selected_option": body["selected_option"]}, "seconds": round(time.perf_counter() - started, 3), "response_status": response.get("status")})
    return get_task(task_id)


def wait_for_completion(task_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    last_stage = ""
    while time.perf_counter() - started < POLL_TIMEOUT_SECONDS:
        detail = get_task(task_id)
        stage = detail.get("current_stage", "")
        if stage != last_stage:
            events.append({"time": now(), "event": "stage", "stage": stage, "status": detail.get("status"), "completed_steps": detail.get("completed_steps"), "total_steps": detail.get("total_steps")})
            last_stage = stage
        if detail.get("status") == "awaiting_confirmation" or ((detail.get("state") or {}).get("confirmation") or {}).get("needed"):
            detail = confirm_if_needed(task_id, detail, events)
        if detail.get("status") in {"completed", "failed", "cancelled", "blocked"}:
            events.append({"time": now(), "event": "terminal", "status": detail.get("status"), "seconds": round(time.perf_counter() - started, 3)})
            return detail
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"task {task_id} did not finish in {POLL_TIMEOUT_SECONDS}s")


def run_domain(folder_name: str, root_log: dict[str, Any]) -> dict[str, Any]:
    folder = MATERIAL_ROOT / folder_name
    upload_log: list[dict[str, Any]] = []
    refs = []
    roles: dict[str, str] = {}
    for path in source_files(folder):
        data = upload_material(path, upload_log)
        file_id = data.get("file_id") or data.get("fileId") or data.get("id")
        if not file_id:
            raise RuntimeError(f"upload response missing file id for {path.name}: {data}")
        refs.append({
            "file_id": file_id,
            "name": data.get("name", path.name),
            "type": data.get("type", path.suffix.lstrip(".")),
            "size": data.get("size", path.stat().st_size),
            "url": data.get("url", ""),
            "parsed_at": data.get("parsed_at", data.get("parsedAt", "")),
        })
        roles[file_id] = role_for(path)
    code = next((part for part in folder_name.split("_") if part.startswith("IA-")), "")
    title = folder_name.split("_", 1)[1]
    prompt = (folder / "00_测试说明与提示词.md").read_text(encoding="utf-8")
    user_input = f"请对 {code} 项目执行一次完整的创新性评估。{prompt}"
    payload = {
        "user_input": user_input[:12000],
        "assessment_title": f"{title} 五领域真实模拟创新性评估",
        "language_preference": "zh",
        "year_range": [2020, 2026],
        "uploaded_files": refs,
        "document_roles": roles,
        "report_mode": "deep",
        "artifact_type": "project",
        "scope_focus": "comprehensive",
        "scope_summary": "科研项目创新性、先进性、指标、证据可追溯性、可行性、应用和标准监管边界综合评估。",
    }
    started = time.perf_counter()
    created = request_json("POST", f"{BASE_URL}/v1/content-assessment/run", headers=json_headers(), json=payload)
    task_id = created.get("run_id")
    if not task_id:
        raise RuntimeError(f"run response missing run_id: {created}")
    events = [{"time": now(), "event": "created", "run_id": task_id, "initial_status": created.get("status")}]
    detail = wait_for_completion(task_id, events)
    task_dir = folder / "报告输出" / task_id
    (task_dir).mkdir(parents=True, exist_ok=True)
    (task_dir / "task_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    exports = []
    if detail.get("status") == "completed":
        exports = export_task(task_id, task_dir / "exports")
    state = detail.get("state") or {}
    result = {
        "domain_folder": folder_name,
        "task_id": task_id,
        "status": detail.get("status"),
        "title": detail.get("title"),
        "created_at": detail.get("created_at"),
        "completed_at": detail.get("completed_at"),
        "duration_seconds": detail.get("duration_seconds"),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "report_version": (state.get("report_plan") or {}).get("report_version", ""),
        "report_type": (state.get("report_plan") or {}).get("report_type", ""),
        "claim_count": len(state.get("claims") or []),
        "indicator_count": len(state.get("indicators") or []),
        "evidence_count": len(state.get("evidence_records") or []),
        "quality_gate": state.get("quality_gate") or {},
        "report_metrics": state.get("report_metrics") or {},
        "stage_trace": detail.get("stage_trace") or [],
        "uploads": upload_log,
        "exports": exports,
        "events": events,
        "error_message": detail.get("error_message"),
    }
    (task_dir / "run_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    MATERIAL_ROOT.mkdir(parents=True, exist_ok=True)
    health = request_json("GET", f"{BASE_URL}/system/health")
    mongo = request_json("GET", f"{BASE_URL}/system/health/mongo")
    root_log: dict[str, Any] = {"started_at": now(), "base_url": BASE_URL, "user_scope": USER_SCOPE, "health": health, "mongo_health": mongo, "domains": []}
    LOG_PATH.write_text(json.dumps(root_log, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot_historical(["run_b5ba30a5061a", "run_28b82738ecdb"], root_log)
    for folder_name in DOMAIN_FOLDERS:
        print(f"START {folder_name}", flush=True)
        result = run_domain(folder_name, root_log)
        root_log["domains"].append(result)
        root_log["updated_at"] = now()
        LOG_PATH.write_text(json.dumps(root_log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"folder": folder_name, "task_id": result["task_id"], "status": result["status"], "wall_seconds": result["wall_seconds"]}, ensure_ascii=False), flush=True)
        if result["status"] != "completed":
            raise RuntimeError(f"domain task did not complete: {folder_name} {result['status']}")
    root_log["completed_at"] = now()
    LOG_PATH.write_text(json.dumps(root_log, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [["领域目录", "任务 ID", "状态", "报告版本", "主张数", "指标数", "证据数", "墙钟秒数", "DOCX", "PDF", "MD", "证据 ZIP"]]
    for item in root_log["domains"]:
        formats = {entry["format"]: entry["path"] for entry in item.get("exports", [])}
        rows.append([item["domain_folder"], item["task_id"], item["status"], item["report_version"], item["claim_count"], item["indicator_count"], item["evidence_count"], item["wall_seconds"], formats.get("docx", ""), formats.get("pdf", ""), formats.get("md", ""), formats.get("zip", "")])
    with TASK_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(rows)
    print(json.dumps({"log": str(LOG_PATH), "task_csv": str(TASK_CSV), "domains": len(root_log["domains"])}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
