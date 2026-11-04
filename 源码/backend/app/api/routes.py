"""HTTP API for the innovation assessment agent."""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.agents.content_assessment.schemas import (
    ArtifactType,
    ContentAssessmentConfirmationRequest,
    ContentAssessmentConfirmationResponse,
    ContentAssessmentRunRequest,
    ContentAssessmentRunResponse,
    ContentAssessmentState,
    ReviewResolutionRequest,
    ReviewResolutionResponse,
    ContentAssessmentTaskHistoryDeleteResponse,
    ContentAssessmentTaskHistoryDetailResponse,
    ContentAssessmentTaskHistoryItem,
    ContentAssessmentTaskHistoryListResponse,
    HealthResponse,
    MongoHealthResponse,
    QueueHealthResponse,
    ResearchNineFields,
    UploadedFileRef,
)
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import (
    TopicConfirmationRequest,
    CandidateSelectionRequest,
    CandidateSelection,
    TopicReviewRequest,
    TopicSelectionRunRequest,
    TopicSelectionRunResponse,
    TopicSelectionState,
)
from app.core.config import (
    ALLOW_DEFAULT_USER_SCOPE,
    FEEDBACK_UPLOAD_DIR,
    MAX_UPLOAD_FILE_COUNT,
    MAX_UPLOAD_FILE_SIZE_BYTES,
    UPLOAD_DIR,
    is_valid_user_scope,
)
from app.core.task_manager import TaskManager
from app.core.rate_limiter import TaskSlotUnavailable, TaskStateChanged
from app.core.topic_task_manager import TopicTaskManager
from app.repositories.content_assessment_feedback import FeedbackStorageError, get_feedback_repo
from app.repositories.content_assessment_parsed_file import (
    ParsedFileError,
    get_parsed_file_repo,
    normalize_parsed_file_payload,
)
from app.repositories.content_assessment_task_history import (
    ACTIVE_STATUSES, ATTENTION_STATUSES, TaskHistoryError, get_task_history_repo, HISTORY_DELETABLE_STATUSES,
)
from app.services.content_assessment_service import ContentAssessmentService
from app.services.export_service import build_docx, build_html, build_markdown, build_pdf, build_evidence_package
from app.services.file_upload_parser import (
    FileParseError,
    SensitiveCheckUnavailable,
    SensitiveContentError,
    build_uploaded_file_anchors,
    check_sensitive_content,
    ensure_parsed_content_usable,
    parse_uploaded_file_content,
    supported_upload_extensions,
)
from app.services.task_persistence import now_beijing_iso, persist_task_state
from app.services.topic_selection_service import TopicSelectionService
from app.services.topic_selection_report import build_topic_docx, build_topic_html, build_topic_markdown, build_topic_pdf, _render_chart_png, _bounded_export_state, TOPIC_CHART_RENDER_VERSION
from app.services.topic_task_persistence import now_beijing_iso as topic_now_beijing_iso, persist_topic_task_state, mark_topic_task_failed, TaskHistoryConflict
from app.services.uploaded_file_context import UploadedFileContextBuilder
from app.utils.id_generator import generate_run_id
from app.services.nine_fields_preview import preview_nine_fields

logger = logging.getLogger(__name__)
router = APIRouter()


@lru_cache(maxsize=1)
def get_service() -> ContentAssessmentService:
    return ContentAssessmentService()


@lru_cache(maxsize=1)
def get_topic_service() -> TopicSelectionService:
    return TopicSelectionService()


def _get_task_manager(request: Request) -> TaskManager:
    return request.app.state.task_manager


def _get_topic_task_manager(request: Request) -> TopicTaskManager:
    return request.app.state.topic_task_manager


def _resolve_user_scope(x_user_scope: str | None) -> str:
    scope = (x_user_scope or "").strip()
    if not is_valid_user_scope(scope):
        detail = "请先登录平台账号" if not ALLOW_DEFAULT_USER_SCOPE else "无效的用户标识"
        raise HTTPException(status_code=401, detail=detail)
    return scope


def _display_task_title(task: dict[str, Any]) -> str:
    if task.get("task_type", "创新性评估") == "创新性评估":
        from app.agents.content_assessment.naming import research_name
        profile = (task.get("state") or {}).get("assessment_profile") or {}
        return research_name(task.get("raw_user_input", ""),
            candidates=(profile.get("title"), task.get("title"), profile.get("assessment_object")),
            documents=profile.get("document_records", []))
    return str(task.get("title") or task.get("raw_user_input") or "创新性评估任务").strip()


def _assessment_reader_task(task: dict[str, Any]) -> dict[str, Any]:
    """Present corrected names consistently in detail and stream responses."""
    state = task.get("state") or {}
    if state.get("report_snapshot"):
        from app.agents.content_assessment.naming import formalize_report_name
        rendered = ContentAssessmentState.model_validate(state)
        formalize_report_name(rendered)
        return {**task, "title": _display_task_title(task), "state": rendered.model_dump(),
                "output_markdown": rendered.output_markdown, "output_json": rendered.output_json}
    return task


def _progress(task: dict[str, Any]) -> tuple[int, int]:
    trace = task.get("stage_trace") or []
    if trace:
        completed = sum(
            (item.get("status") if isinstance(item, dict) else getattr(item, "status", ""))
            in {"completed", "skipped"}
            for item in trace
        )
        return completed, len(trace)
    return int(task.get("completed_steps") or 0), int(task.get("total_steps") or 8)


def _locate_claim_in_evidence(state: ContentAssessmentState, claim: Any) -> dict[str, Any] | None:
    """Validate a manually confirmed quote against persisted evidence text."""
    evidence_id = (claim.source_evidence_id or claim.source_document_id or "").strip()
    evidence = next((item for item in state.evidence_records if item.evidence_id == evidence_id), None)
    if evidence is None or not claim.source_span.strip():
        return None
    candidates: list[tuple[str, str, str, int]] = []
    for chunk in state.evidence_chunks:
        if chunk.evidence_id == evidence_id and chunk.excerpt:
            candidates.append((chunk.excerpt, chunk.page_ref, chunk.paragraph_ref or chunk.section_ref,
                               chunk.char_start if chunk.char_start is not None else evidence.char_start or 0))
    candidates.extend([
        (evidence.excerpt or "", evidence.page_ref, evidence.paragraph_ref or evidence.section_ref, evidence.char_start or 0),
        (evidence.abstract or "", evidence.page_ref, evidence.paragraph_ref or evidence.section_ref, 0),
    ])
    for text, page_ref, paragraph_ref, base_offset in candidates:
        start = text.find(claim.source_span)
        if start < 0:
            continue
        absolute_start = base_offset + start
        return {
            "source_kind": "evidence",
            "source_evidence_id": evidence_id,
            "source_document_id": evidence.source_document_id,
            "source_char_start": absolute_start,
            "source_char_end": absolute_start + len(claim.source_span),
            "source_page_ref": page_ref,
            "source_paragraph_ref": paragraph_ref,
            "source_locator": {"kind": "evidence", "evidence_id": evidence_id,
                               "page": page_ref, "paragraph": paragraph_ref},
        }
    return None


class ParsedFilesBatchRequest(BaseModel):
    files: list[dict[str, Any]] = Field(default_factory=list)


class ParsedFileSingleRequest(BaseModel):
    file: dict[str, Any] | None = None
    fileId: str = ""
    file_id: str = ""
    name: str = ""
    type: str = ""
    content: str = ""
    size: int = 0
    parsedAt: str = ""
    parsed_at: str = ""
    url: str = ""


class BatchDeleteResponse(BaseModel):
    success: bool = True
    deleted_count: int = 0
    task_ids: list[str] = Field(default_factory=list)


class NineFieldsPreviewRequest(BaseModel):
    user_input: str = Field(default="", max_length=12000)
    uploaded_files: list[UploadedFileRef] = Field(default_factory=list, max_length=MAX_UPLOAD_FILE_COUNT)
    artifact_type: ArtifactType = "auto"


async def _quota_error(scope: str, agent: str, manager: Any, message: str) -> HTTPException:
    try:
        stats = await get_task_history_repo().statistics(scope)
    except TaskHistoryError as exc:
        return HTTPException(status_code=503, detail=str(exc))
    counts = stats["agents"][agent]
    usage = await manager.rate_limiter.usage(scope)
    running = counts.get("running", 0) + counts.get("processing", 0)
    queued = counts.get("queued", 0)
    return HTTPException(status_code=429, detail={"code": "rate_limit_exceeded", "message": message,
        "agent": agent, "running": running, "queued": queued, **usage})


async def _resume_task(manager: Any, task_id: str, scope: str, state: Any, agent: str, expected_task: dict) -> None:
    try:
        await manager.resume_pipeline(task_id, scope, state, expected_updated_at=expected_task.get("updated_at"), expected_status=expected_task.get("status"))
    except TaskStateChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TaskSlotUnavailable as exc:
        if manager.is_running(task_id):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise await _quota_error(scope, agent, manager, str(exc)) from exc


async def _start_task(manager: Any, task_id: str, scope: str, state: Any) -> None:
    try:
        await manager.start_pipeline(task_id, scope, state)
    except Exception:
        state.current_stage = "failed"
        try:
            await get_task_history_repo().update(task_id, scope, {
                "status": "failed", "current_stage": "failed", "state": state.model_dump(),
                "error_message": "任务执行者启动失败，请重新创建任务", "updated_at": now_beijing_iso(),
                "completed_at": now_beijing_iso(),
            })
        finally:
            await manager.rate_limiter.release(scope, task_id)
        raise


@router.post("/v1/content-assessment/preview-nine-fields")
async def preview_content_assessment_fields(
    body: NineFieldsPreviewRequest, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    try:
        return await preview_nine_fields(body.user_input, body.uploaded_files, scope, body.artifact_type)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, ParsedFileError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v1/tasks")
async def list_all_tasks(
    request: Request,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    agent: str = Query(default="all", pattern="^(all|topic|assessment)$"),
    status: str = Query(default="all"), search: str = Query(default="", max_length=300),
    days: int | None = Query(default=None, ge=1, le=36500),
    created_after: datetime | None = Query(default=None), created_before: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    statuses = (list(ACTIVE_STATUSES) if status == "running" else
                list(ATTENTION_STATUSES) if status == "attention" else ["cancelled"] if status == "stopped" else None if status == "all" else [status])
    # Statistics deliberately classifies legacy/unknown statuses as attention.
    # Query the same way so the attention filter cannot show an empty page while
    # its summary card contains those records.
    exclude_statuses = list({*ACTIVE_STATUSES, *ATTENTION_STATUSES, "completed", "cancelled"}) if status == "attention" else None
    beijing = timezone(timedelta(hours=8))
    after = created_after or (datetime.now(beijing) - timedelta(days=days) if days else None)
    try:
        repo = get_task_history_repo()
        try:
            records, total = await repo.list_tasks(scope, task_type={"assessment": "创新性评估", "topic": "智能选题"}.get(agent),
                statuses=None if exclude_statuses else statuses, exclude_statuses=exclude_statuses, search=search,
                created_after=after.astimezone(beijing).isoformat() if after else None,
                created_before=created_before.astimezone(beijing).isoformat() if created_before else None,
                page=page, page_size=page_size)
        except TypeError:
            # Compatibility with lightweight repository doubles predating the
            # unknown-status filter.
            records, total = await repo.list_tasks(scope, task_type={"assessment": "创新性评估", "topic": "智能选题"}.get(agent),
                statuses=statuses, search=search, created_after=after.astimezone(beijing).isoformat() if after else None,
                created_before=created_before.astimezone(beijing).isoformat() if created_before else None,
                page=page, page_size=page_size)
        stats = await repo.statistics(scope)
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    quotas = {}
    for key, manager in (("assessment", request.app.state.task_manager), ("topic", request.app.state.topic_task_manager)):
        counts = stats["agents"][key]
        quotas[key] = {**await manager.rate_limiter.usage(scope), "running": counts.get("running", 0) + counts.get("processing", 0),
                       "queued": counts.get("queued", 0)}
    items = []
    for record in records:
        completed, steps = _progress(record)
        items.append({key: value for key, value in record.items() if key not in {"_id", "state", "output_json", "output_markdown", "stage_trace"}} |
                     {"id": record.get("task_id", ""), "completed_steps": completed, "total_steps": steps, "recovery": record.get("recovery") or record.get("output_json", {}).get("generation_recovery")})
    return {"items": items, "total": total, "page": page, "page_size": page_size, "statistics": stats, "quotas": quotas}


@router.get("/v1/tasks/history")
@router.delete("/v1/tasks/history")
async def clear_task_history(
    request: Request,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    agent: str = Query(default="all", pattern="^(all|topic|assessment)$"),
    task_manager: TaskManager = Depends(_get_task_manager),
    topic_manager: TopicTaskManager = Depends(_get_topic_task_manager),
) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    try:
        return await get_task_history_repo().delete_history(scope,
            task_type={"topic": "智能选题", "assessment": "创新性评估"}.get(agent),
            dry_run=request.method == "GET",
            is_running=lambda task_id: task_manager.is_running(task_id) or topic_manager.is_running(task_id))
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parsed_file_response(record: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    data = {
        "file_id": record.get("file_id", ""),
        "fileId": record.get("file_id", ""),
        "name": record.get("name", ""),
        "type": record.get("type", ""),
        "size": int(record.get("size") or 0),
        "content_size": int(record.get("content_size") or len(record.get("content", "") or "")),
        "original_content_size": int(record.get("original_content_size") or record.get("content_size") or 0),
        "parsed_at": record.get("parsed_at", ""),
        "parsedAt": record.get("parsed_at", ""),
        "url": record.get("url", ""),
        "status": record.get("status", "saved"),
        "truncated": bool(record.get("truncated", False)),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
        "last_used_at": record.get("last_used_at", ""),
        "page_count": record.get("page_count"),
        "anchor_schema_version": record.get("anchor_schema_version", "anchor-v1"),
        "anchor_count": len(record.get("anchors") or []),
        "sensitive_check_status": record.get("sensitive_check_status", "unknown"),
    }
    if include_content:
        data["content"] = record.get("content", "")
    return data


def _validate_parsed_file(raw_file: dict[str, Any], user_scope: str, index: int) -> dict[str, Any]:
    record = normalize_parsed_file_payload(raw_file, user_scope)
    if not record["file_id"]:
        raise HTTPException(status_code=422, detail=f"第 {index} 个文件缺少 fileId")
    if not record["name"]:
        raise HTTPException(status_code=422, detail=f"第 {index} 个文件缺少文件名")
    if record["type"] not in {"pdf", "txt", "md", "docx", "xlsx", "csv", "pptx"}:
        detail = "暂不支持图片和 DOC 文件，请上传 PDF、DOCX、XLSX、CSV、PPTX、TXT 或 Markdown。"
        raise HTTPException(status_code=422, detail=detail)
    if not record["content"].strip():
        raise HTTPException(status_code=422, detail=f"文件无可用解析文本：{record['name']}")
    return record


def _upload_error_detail(code: str, message: str, filename: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "filename": filename, **extra}


def _cleanup_file(path: Path | None) -> None:
    if path and path.exists():
        try:
            path.unlink()
        except OSError:
            logger.warning("Cannot remove failed upload path=%s", path)


@router.get("/system/health", response_model=HealthResponse)
async def system_health() -> HealthResponse:
    return HealthResponse()


@router.get("/system/health/mongo", response_model=MongoHealthResponse)
async def mongo_health() -> MongoHealthResponse:
    try:
        return MongoHealthResponse(**await get_task_history_repo().ping())
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/system/health/model")
async def model_health(request: Request) -> dict[str, Any]:
    """Return the last startup model preflight without exposing credentials."""
    health = dict(getattr(request.app.state, "model_health", {}) or {})
    if not health:
        health = {"status": "unknown", "error_code": "preflight_not_run"}
    health.pop("api_key", None)
    return {"success": health.get("status") == "available", **health}


@router.post("/api/platform/feedback/submit")
@router.post("/api/service/nstl/web/user-feedback/submit")
async def submit_user_feedback(
    request: Request,
    systemName: str = Form(default="content-assessment"),
    content: str = Form(default=""),
    contact: str = Form(default=""),
    pageUrl: str = Form(default=""),
    files: list[UploadFile] = File(default_factory=list),
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> dict[str, Any]:
    user_scope = _resolve_user_scope(x_user_scope)
    description = content.strip()
    if not description:
        raise HTTPException(status_code=422, detail="请填写问题描述或建议")
    if len(description) > 300:
        raise HTTPException(status_code=422, detail="问题描述不能超过 300 字")
    if contact and not re.match(r"^1\d{10}$|^[^\s@]+@[^\s@]+\.[^\s@]+$", contact.strip()):
        raise HTTPException(status_code=422, detail="请输入正确的手机号或邮箱")
    if len(files) > 10:
        raise HTTPException(status_code=422, detail="最多上传 10 张图片")

    feedback_id = f"fb_{uuid.uuid4().hex[:12]}"
    attachment_dir = Path(FEEDBACK_UPLOAD_DIR) / feedback_id
    attachments: list[dict[str, Any]] = []
    for upload in files:
        if not (upload.content_type or "").startswith("image/"):
            raise HTTPException(status_code=422, detail="仅支持上传图片文件")
        payload = await upload.read()
        if len(payload) > 5 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="单个图片不能超过 5MB")
        extension = Path(upload.filename or "").suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            extension = ".png"
        attachment_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4().hex}{extension}"
        target = attachment_dir / stored_name
        await asyncio.to_thread(target.write_bytes, payload)
        attachments.append({"original_name": upload.filename or "", "stored_name": stored_name,
                            "content_type": upload.content_type or "", "size": len(payload), "path": str(target)})

    now = now_beijing_iso()
    record = {
        "feedback_id": feedback_id, "system_name": systemName.strip() or "content-assessment",
        "content": description, "contact": contact.strip(), "page_url": pageUrl.strip(),
        "user_scope": user_scope, "client_host": request.client.host if request.client else "",
        "user_agent": request.headers.get("user-agent", ""), "attachments": attachments,
        "status": "submitted", "created_at": now, "updated_at": now,
    }
    try:
        await get_feedback_repo().create(record)
    except FeedbackStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"code": 200, "message": "提交成功", "data": {"feedback_id": feedback_id}}


@router.post("/v1/files/upload")
async def upload_and_parse_file(
    file: UploadFile = File(...),
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> dict[str, Any]:
    user_scope = _resolve_user_scope(x_user_scope)
    name = (file.filename or "未命名文件").strip()
    extension = Path(name).suffix.lower()
    if extension == ".doc":
        raise HTTPException(status_code=422, detail=_upload_error_detail(
            "unsupported_file_type", "暂不支持 DOC，请先另存为 DOCX 后上传。", name))
    if extension not in supported_upload_extensions():
        raise HTTPException(status_code=422, detail=_upload_error_detail(
            "unsupported_file_type", f"不支持的文件类型：{extension or '未知'}。请上传 PDF、DOCX、XLSX、CSV、PPTX、TXT 或 Markdown。", name))
    file_id = f"file_{uuid.uuid4().hex[:16]}"
    target = Path(UPLOAD_DIR) / f"{file_id}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        body = await file.read()
        if not body:
            raise HTTPException(status_code=422, detail=_upload_error_detail("empty_file", "文件内容为空，请重新选择文件上传。", name))
        if len(body) > MAX_UPLOAD_FILE_SIZE_BYTES:
            raise HTTPException(status_code=422, detail=_upload_error_detail(
                "file_too_large", "单个文件不能超过 50MB，请压缩或拆分后重新上传。", name,
                max_size_bytes=MAX_UPLOAD_FILE_SIZE_BYTES))
        await asyncio.to_thread(target.write_bytes, body)
        content = await parse_uploaded_file_content(target, extension)
        ensure_parsed_content_usable(content, name, extension)
        anchors, page_count = await build_uploaded_file_anchors(target, extension, content)
        await check_sensitive_content(content, name)
        record = _validate_parsed_file({
            "fileId": file_id, "name": name, "type": extension.lstrip("."), "content": content,
            "size": len(content), "parsedAt": now_beijing_iso(), "url": f"/v1/files/{target.name}",
            "source": "server_upload", "anchors": anchors,
            "page_count": page_count,
            "anchor_schema_version": "anchor-v1",
            "sensitive_check_status": "passed",
        }, user_scope, 1)
        _, errors, data = await get_parsed_file_repo().batch_create([record])
        if errors:
            raise ParsedFileError(str(errors[0]))
        response = _parsed_file_response(data[0] if data else record)
        response.update({"id": response["file_id"], "status": "parsed", "content_length": response["content_size"]})
        return {"success": True, "saved": 1, "data": response}
    except HTTPException:
        _cleanup_file(target)
        raise
    except FileParseError as exc:
        _cleanup_file(target)
        raise HTTPException(status_code=422, detail=_upload_error_detail(exc.code, str(exc), name, stage="parse")) from exc
    except SensitiveContentError as exc:
        _cleanup_file(target)
        raise HTTPException(status_code=400, detail=_upload_error_detail(
            "sensitive_content_detected", str(exc), name, stage="sensitive_check", sensitive_hits=exc.hits)) from exc
    except SensitiveCheckUnavailable as exc:
        _cleanup_file(target)
        raise HTTPException(status_code=503, detail=_upload_error_detail("sensitive_check_failed", str(exc), name, stage="sensitive_check")) from exc
    except ParsedFileError as exc:
        _cleanup_file(target)
        raise HTTPException(status_code=503, detail=_upload_error_detail("storage_failed", "文件存储服务暂不可用，请稍后重试。", name, stage="storage")) from exc
    except Exception as exc:
        _cleanup_file(target)
        logger.exception("File upload failed filename=%s", name)
        raise HTTPException(status_code=500, detail=_upload_error_detail("upload_failed", "文件上传失败，请稍后重试。", name)) from exc


@router.get("/v1/files/{stored_name}")
async def get_uploaded_file(stored_name: str) -> FileResponse:
    if not re.fullmatch(r"file_[a-f0-9]{16}\.(pdf|txt|md|docx|xlsx|csv|pptx)", stored_name):
        raise HTTPException(status_code=404, detail="文件不存在")
    target = Path(UPLOAD_DIR) / stored_name
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target)


@router.post("/v1/parsed-files/batch")
async def save_parsed_files_batch(
    request: ParsedFilesBatchRequest, x_user_scope: str | None = Header(default=None, alias="X-User-Scope")
) -> dict[str, Any]:
    user_scope = _resolve_user_scope(x_user_scope)
    if len(request.files) > MAX_UPLOAD_FILE_COUNT:
        raise HTTPException(status_code=422, detail=f"单次最多保存 {MAX_UPLOAD_FILE_COUNT} 个文件")
    records = [_validate_parsed_file(value, user_scope, index) for index, value in enumerate(request.files, 1)]
    # Legacy client-parsed uploads must pass the same check before any write.
    for raw, record in zip(request.files, records):
        try:
            await check_sensitive_content(str(raw.get("content") or ""), record["name"])
            record["sensitive_check_status"] = "passed"
        except SensitiveContentError as exc:
            raise HTTPException(status_code=400, detail=_upload_error_detail(
                "sensitive_content_detected", str(exc), record["name"], stage="sensitive_check", sensitive_hits=exc.hits)) from exc
        except SensitiveCheckUnavailable as exc:
            raise HTTPException(status_code=503, detail=_upload_error_detail(
                "sensitive_check_failed", str(exc), record["name"], stage="sensitive_check")) from exc
    try:
        saved, errors, data = await get_parsed_file_repo().batch_create(records)
    except ParsedFileError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": not errors, "saved": saved, "total": len(records),
            "data": [_parsed_file_response(item) for item in data], "errors": errors}


@router.post("/v1/parsed-files")
async def save_parsed_file(
    request: ParsedFileSingleRequest, x_user_scope: str | None = Header(default=None, alias="X-User-Scope")
) -> dict[str, Any]:
    payload = request.file or request.model_dump()
    result = await save_parsed_files_batch(ParsedFilesBatchRequest(files=[payload]), x_user_scope)
    return {**result, "data": result["data"][0] if result["data"] else {}}


@router.get("/v1/parsed-files")
async def list_parsed_files(
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), include_content: bool = Query(default=False)
) -> dict[str, Any]:
    user_scope = _resolve_user_scope(x_user_scope)
    try:
        files = await get_parsed_file_repo().list_files(user_scope, include_content=include_content)
    except ParsedFileError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True, "total": len(files),
            "data": [_parsed_file_response(item, include_content=include_content) for item in files]}


@router.get("/v1/parsed-files/{file_id}")
async def get_parsed_file(
    file_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), include_content: bool = Query(default=True)
) -> dict[str, Any]:
    user_scope = _resolve_user_scope(x_user_scope)
    try:
        record = await get_parsed_file_repo().get(file_id, user_scope, include_content=include_content)
    except ParsedFileError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="解析文件不存在")
    return {"success": True, "data": _parsed_file_response(record, include_content=include_content)}


@router.post("/v1/content-assessment/run", response_model=ContentAssessmentRunResponse)
async def run_content_assessment(
    request_body: ContentAssessmentRunRequest,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    service: ContentAssessmentService = Depends(get_service),
    task_manager: TaskManager = Depends(_get_task_manager),
) -> ContentAssessmentRunResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    run_id = generate_run_id()
    allowed, message = await task_manager.rate_limiter.check_and_acquire(user_scope, run_id)
    if not allowed:
        raise await _quota_error(user_scope, "assessment", task_manager, message)
    try:
        state = service.create_initial_state(
            request_body.user_input, assessment_title=request_body.assessment_title,
            uploaded_files=request_body.uploaded_files, document_roles=request_body.document_roles,
            year_range=request_body.year_range,
            report_mode=request_body.report_mode,
            artifact_type=request_body.artifact_type,
            scope_focus=request_body.scope_focus,
            scope_summary=request_body.scope_summary,
            section_overrides=request_body.section_overrides,
            scenario_id=request_body.scenario_id or "",
        )
        state.user_scope = user_scope
        if request_body.nine_fields is not None:
            state.nine_fields = request_body.nine_fields.model_copy(deep=True)
            state.nine_fields_confirmed = True
        await UploadedFileContextBuilder().attach_to_state(state, user_scope=user_scope, refs=request_body.uploaded_files)
        # Validate intake before persisting a task. Complete previews take this
        # fast path; legacy/natural-language clients share the same recognition
        # rules instead of entering a mid-pipeline confirmation step.
        kind = state.report_preferences.resolved_artifact_type
        missing = state.nine_fields.missing_for_type(kind) if kind != "unknown" else ["artifact_type"]
        if missing:
            preview = await preview_nine_fields(
                request_body.user_input, request_body.uploaded_files, user_scope,
                request_body.artifact_type,
            )
            fields = ResearchNineFields.model_validate(preview["nine_fields"])
            if request_body.nine_fields is not None:
                from app.services.nine_fields_preview import _merge_fields
                fields = _merge_fields(request_body.nine_fields, fields)
            kind = preview["resolved_artifact_type"]
            missing = fields.missing_for_type(kind) if kind != "unknown" else ["artifact_type"]
            if missing:
                from app.agents.content_assessment.input_rules import FIELD_LABELS
                labels = {value: key for key, value in FIELD_LABELS.items()}
                failed = preview.get("extraction_failed", False)
                raise HTTPException(status_code=503 if failed else 422, detail={
                    "code": "recognition_failed" if failed else "assessment_input_incomplete",
                    "message": ("材料识别暂未完成，请重试，输入内容会保留。" if failed else
                        "请在输入框中补充" + "、".join(labels.get(key, "材料类型") for key in missing) + "，输入内容会保留。"),
                    "resolved_artifact_type": kind, "missing_fields": missing,
                    "nine_fields": fields.model_dump(),
                })
            state.nine_fields = fields
        state.nine_fields_confirmed = True
        state.report_preferences.resolved_artifact_type = kind
        state.assessment_profile.artifact_type = kind
        state.checkpoint_id = run_id
        now = now_beijing_iso()
        await get_task_history_repo().create({
            "task_id": run_id, "user_scope": user_scope, "user_name": user_scope,
            "title": state.assessment_profile.title, "task_type": "创新性评估", "raw_user_input": state.raw_user_input,
            "status": "queued", "current_stage": "created", "completed_steps": 0,
            "total_steps": len(state.stage_trace), "needs_confirmation": False, "confirmation_status": "not_required",
            "summary": "已创建创新性评估任务。", "created_at": now, "updated_at": now, "queued_at": now,
            "state": state.model_dump(), "stage_trace": [item.model_dump() for item in state.stage_trace],
            "output_markdown": "", "output_json": state.output_json,
        })
        await _start_task(task_manager, run_id, user_scope, state)
        return ContentAssessmentRunResponse(run_id=run_id, status="running", state=state)
    except HTTPException:
        await task_manager.rate_limiter.release(user_scope, run_id)
        raise
    except (TaskHistoryError, ParsedFileError) as exc:
        await task_manager.rate_limiter.release(user_scope, run_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        await task_manager.rate_limiter.release(user_scope, run_id)
        logger.exception("[run] 创建创新性评估任务失败 user=%s", user_scope)
        raise HTTPException(status_code=500, detail=f"创建任务失败：{exc}") from exc


@router.post("/v1/content-assessment/tasks/{task_id}/revisions", response_model=ContentAssessmentRunResponse)
async def create_report_revision(
    task_id: str,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    service: ContentAssessmentService = Depends(get_service),
    task_manager: TaskManager = Depends(_get_task_manager),
) -> ContentAssessmentRunResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, user_scope)
    if not task or task.get("task_type", "创新性评估") != "创新性评估" or not task.get("state"):
        raise HTTPException(status_code=404, detail="原任务不存在或无权访问")
    old = ContentAssessmentState.model_validate(task["state"])
    run_id = generate_run_id()
    allowed, message = await task_manager.rate_limiter.check_and_acquire(user_scope, run_id)
    if not allowed:
        raise await _quota_error(user_scope, "assessment", task_manager, message)
    try:
        state = service.create_initial_state(old.raw_user_input, uploaded_files=old.uploaded_files,
            year_range=old.output_json.get("year_range", []), report_mode=old.report_preferences.mode,
            artifact_type=old.report_preferences.artifact_type, scope_focus=old.report_preferences.scope_focus,
            scope_summary=old.report_preferences.scope_summary, section_overrides=old.report_preferences.section_overrides,
            document_roles={d.document_id: d.role for d in old.assessment_profile.document_records},
            scenario_id=old.scenario_id)
        state.parent_task_id = task_id
        state.nine_fields = old.nine_fields.model_copy(deep=True)
        state.nine_fields_confirmed = old.nine_fields_confirmed
        state.user_scope = user_scope
        await UploadedFileContextBuilder().attach_to_state(state, user_scope=user_scope, refs=old.uploaded_files)
        if len(state.uploaded_file_context.files) != len(old.uploaded_files):
            raise HTTPException(status_code=409, detail="原材料已不可用，请补充材料后重新评估")
        state.checkpoint_id = run_id
        now = now_beijing_iso()
        await get_task_history_repo().create({"task_id": run_id, "user_scope": user_scope, "user_name": user_scope,
            "parent_task_id": task_id, "title": old.assessment_profile.title + "（重新评估）", "task_type": "创新性评估",
            "raw_user_input": state.raw_user_input, "status": "queued", "current_stage": "created", "completed_steps": 0,
            "total_steps": len(state.stage_trace), "created_at": now, "updated_at": now, "queued_at": now,
            "state": state.model_dump(), "summary": "基于原材料生成新版，原任务保留。", "output_markdown": "", "output_json": {}})
        await _start_task(task_manager, run_id, user_scope, state)
        return ContentAssessmentRunResponse(run_id=run_id, status="running", state=state)
    except Exception:
        await task_manager.rate_limiter.release(user_scope, run_id)
        raise


@router.get("/v1/content-assessment/tasks/{task_id}/stream")
async def stream_task(
    task_id: str,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    user_scope: str | None = Query(default=None),
) -> StreamingResponse:
    user_scope = _resolve_user_scope(x_user_scope or user_scope)
    repo = get_task_history_repo()

    async def events():
        last_updated = ""
        while True:
            task = await repo.get(task_id, user_scope)
            if not task or task.get("task_type", "创新性评估") != "创新性评估":
                yield f"event: error\ndata: {json.dumps({'message': '任务不存在'}, ensure_ascii=False)}\n\n"
                return
            task = _assessment_reader_task(task)
            updated = str(task.get("updated_at", ""))
            if updated != last_updated:
                event = "state" if not last_updated else "delta"
                yield f"event: {event}\ndata: {json.dumps(task, ensure_ascii=False, default=str)}\n\n"
                last_updated = updated
            if task.get("status") in {"completed", "failed", "cancelled", "blocked", "awaiting_confirmation"}:
                yield f"event: complete\ndata: {json.dumps(task, ensure_ascii=False, default=str)}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/v1/content-assessment/tasks", response_model=ContentAssessmentTaskHistoryListResponse)
async def list_content_assessment_tasks(
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100), status: str | None = Query(default=None), search: str | None = Query(default=None),
) -> ContentAssessmentTaskHistoryListResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    try:
        records, total = await get_task_history_repo().list_tasks(user_scope, status=status, search=search, page=page, page_size=page_size, task_type="创新性评估")
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    items = []
    for task in records:
        completed, all_steps = _progress(task)
        items.append(ContentAssessmentTaskHistoryItem(
            id=task.get("task_id", str(task.get("_id", ""))), task_id=task.get("task_id", ""),
            title=_display_task_title(task), task_type=task.get("task_type", "创新性评估"), status=task.get("status", ""),
            current_stage=task.get("current_stage", ""), completed_steps=completed, total_steps=all_steps,
            needs_confirmation=task.get("needs_confirmation", False), confirmation_status=task.get("confirmation_status", "not_required"),
            summary=task.get("summary", ""), created_at=task.get("created_at", ""), updated_at=task.get("updated_at", ""),
            completed_at=task.get("completed_at"), duration_seconds=task.get("duration_seconds"),
        ))
    return ContentAssessmentTaskHistoryListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/v1/content-assessment/tasks/{task_id}", response_model=ContentAssessmentTaskHistoryDetailResponse)
async def get_content_assessment_task(
    task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope")
) -> ContentAssessmentTaskHistoryDetailResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    try:
        task = await get_task_history_repo().get(task_id, user_scope)
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not task or task.get("task_type", "创新性评估") != "创新性评估":
        raise HTTPException(status_code=404, detail="任务不存在")
    completed, all_steps = _progress(task)
    task = _assessment_reader_task(task)
    return ContentAssessmentTaskHistoryDetailResponse(
        id=task.get("task_id", str(task.get("_id", ""))), task_id=task.get("task_id", ""),
        user_scope=task.get("user_scope", ""), user_name=task.get("user_name", ""), title=_display_task_title(task),
        task_type=task.get("task_type", "创新性评估"), raw_user_input=task.get("raw_user_input", ""), status=task.get("status", ""),
        current_stage=task.get("current_stage", ""), completed_steps=completed, total_steps=all_steps,
        needs_confirmation=task.get("needs_confirmation", False), confirmation_status=task.get("confirmation_status", "not_required"),
        summary=task.get("summary", ""), created_at=task.get("created_at", ""), updated_at=task.get("updated_at", ""),
        completed_at=task.get("completed_at"), duration_seconds=task.get("duration_seconds"), state=task.get("state"),
        output_markdown=task.get("output_markdown", ""), output_json=task.get("output_json", {}),
        stage_trace=task.get("stage_trace", []), error_message=task.get("error_message"),
    )


@router.post("/v1/content-assessment/tasks/{task_id}/confirmation", response_model=ContentAssessmentConfirmationResponse)
async def submit_content_assessment_confirmation(
    task_id: str, request_body: ContentAssessmentConfirmationRequest,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TaskManager = Depends(_get_task_manager),
) -> ContentAssessmentConfirmationResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    repo = get_task_history_repo()
    try:
        task = await repo.get(task_id, user_scope)
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not task or task.get("task_type", "创新性评估") != "创新性评估" or not task.get("state"):
        raise HTTPException(status_code=404, detail="任务不存在或状态已丢失")
    state = ContentAssessmentState.model_validate(task["state"])
    from app.agents.content_assessment.recovery import retry_stage
    technical_stage = retry_stage(state)
    if technical_stage and task.get("status") in {"failed", "awaiting_confirmation"}:
        if task_manager.is_running(task_id):
            raise HTTPException(status_code=409, detail="任务正在运行，请勿重复提交")
        if request_body.selected_option not in {"retry_generation", "continue_with_assumptions", "retry_extraction"}:
            raise HTTPException(status_code=422, detail="请选择重试生成")
        state.confirmation.needed = False
        state.assessment_profile.confirmation_required = False
        state.resume_from_stage = technical_stage
        state.current_stage = technical_stage
        state.output_json.pop("pause_reason", None)
        state.output_json["generation_recovery"] = {"status": "retrying", "stage": technical_stage}
        await _resume_task(task_manager, task_id, user_scope, state, "assessment", task)
        return ContentAssessmentConfirmationResponse(run_id=task_id, status="running", state=state)
    allowed = {value.get("value") for value in state.confirmation.options}
    if task.get("status") != "awaiting_confirmation" or task_manager.is_running(task_id):
        raise HTTPException(status_code=409, detail="任务不在待确认状态")
    if state.confirmation.needed and request_body.selected_option not in allowed:
        raise HTTPException(status_code=422, detail="确认选项无效")
    if state.confirmation.reason == "multiple_objects":
        remove_ids = set(request_body.removed_file_ids)
        known_ids = {f.file_id for f in state.uploaded_files}
        if not remove_ids or not remove_ids.issubset(known_ids):
            raise HTTPException(status_code=422, detail="请选择本任务中需要移除的冲突文件")
        state.uploaded_files = [f for f in state.uploaded_files if f.file_id not in remove_ids]
        state.uploaded_file_context.files = [f for f in state.uploaded_file_context.files if f.file_id not in remove_ids]
        state.assessment_profile.document_records = [f for f in state.assessment_profile.document_records if f.document_id not in remove_ids]
        if not state.uploaded_files:
            raise HTTPException(status_code=422, detail="请至少保留一个评估对象的材料")
        state.assessment_profile.pending_confirmation_fields = []
        for record in state.assessment_profile.document_records:
            record.parse_issues = [i for i in record.parse_issues if i.code != "multiple_objects"]
        # Rebuild every derived input value from the remaining files. A removed
        # object's scope or fields must never leak into the resumed assessment.
        from app.agents.content_assessment.schemas import AssessmentProfile, ResearchNineFields, BoundaryMatrix
        state.assessment_profile = AssessmentProfile()
        state.nine_fields = ResearchNineFields()
        state.boundary_matrix = BoundaryMatrix()
        state.claims = []
        state.indicators = []
        state.output_json.pop("input_fields_supplemented", None)
        state.output_json.pop("claims_manually_confirmed", None)
        state.nine_fields_confirmed = False
        state.resume_from_stage = "document_intake"
    if state.confirmation.reason == "missing_fields":
        kind = request_body.artifact_type or state.report_preferences.resolved_artifact_type
        fields = request_body.nine_fields or state.nine_fields
        supplement = request_body.supplemental_input.strip()
        if supplement:
            # Lock an established object type; a cited guideline is not a new object.
            locked = state.report_preferences.resolved_artifact_type
            if locked in {"paper", "project", "guideline"}:
                kind = locked
            from app.agents.content_assessment.input_rules import FIELD_LABELS
            field_labels = {value: key for key, value in FIELD_LABELS.items()}
            original_fields = "\n".join(
                f"{field_labels[key]}：{'；'.join(value) if isinstance(value, list) else value}"
                for key, value in fields.model_dump().items() if value
            )
            # Re-read the original text and parsed attachments as well: a field
            # omitted during the first extraction is not necessarily absent.
            from app.agents.content_assessment.material_extraction import material_sources
            preview = await preview_nine_fields(
                original_fields + "\n" + supplement,
                [], user_scope, kind if kind in {"paper", "project", "guideline"} else "auto",
                material_texts=[source.content for source in material_sources(state)],
            )
            kind = preview["resolved_artifact_type"]
            from app.agents.content_assessment.schemas import ResearchNineFields
            fields = ResearchNineFields.model_validate({
                key: preview["nine_fields"].get(key) or value for key, value in fields.model_dump().items()
            })
            if preview["extraction_failed"] and (kind not in {"paper", "project", "guideline"} or fields.missing_for_type(kind)):
                raise HTTPException(status_code=503, detail="补充内容识别暂未完成，请重试或粘贴明确的研究信息")
        if kind not in {"paper", "project", "guideline"} or fields.missing_for_type(kind):
            from app.agents.content_assessment.input_rules import FIELD_LABELS
            labels = {value: key for key, value in FIELD_LABELS.items()}
            missing = fields.missing_for_type(kind) if kind in {"paper", "project", "guideline"} else ["artifact_type"]
            detail = "请说明要评估的是论文、项目还是指南" if missing == ["artifact_type"] else "请继续补充：" + "、".join(labels.get(key, key) for key in missing)
            raise HTTPException(status_code=422, detail=detail)
        if supplement:
            materials = state.output_json.setdefault("supplemental_materials", [])
            materials.append({"file_id": f"user_supplement_{len(materials) + 1}", "name": "用户补充评估材料", "content": supplement})
        state.nine_fields = fields
        state.output_json["input_fields_supplemented"] = True
        state.nine_fields_confirmed = True
        state.report_preferences.artifact_type = kind
        state.report_preferences.resolved_artifact_type = kind
        state.resume_from_stage = "claim_extraction"
    state.confirmation.selected_option = request_body.selected_option
    state.confirmation.user_note = request_body.user_note
    if state.confirmation.reason == "claim_extraction":
        if request_body.selected_option == "confirm_claims":
            from app.agents.content_assessment.material_extraction import locate, material_sources
            confirmed = request_body.confirmed_claims or []
            if not confirmed:
                raise HTTPException(status_code=422, detail="请提交带来源和原文引用的技术主张")
            for claim in confirmed:
                if claim.source_kind == "evidence":
                    anchor = _locate_claim_in_evidence(state, claim)
                else:
                    source = next((f for f in material_sources(state) if f.file_id == claim.source_document_id), None)
                    anchor = locate(source, claim.source_span, claim.source_char_start) if source else None
                if not anchor:
                    raise HTTPException(status_code=422, detail="主张原文定位不匹配，请重新核对所选材料或检索文献")
                for key, value in anchor.items():
                    setattr(claim, key, value)
            state.claims = confirmed
            state.output_json["claims_manually_confirmed"] = True
        elif request_body.selected_option != "retry_extraction":
            raise HTTPException(status_code=422, detail="请选择重试提取或确认主张")
    if request_body.report_mode is not None:
        state.report_preferences.mode = request_body.report_mode
    if request_body.artifact_type is not None and not (state.confirmation.reason == "missing_fields" and request_body.supplemental_input.strip()):
        state.report_preferences.artifact_type = request_body.artifact_type
    if request_body.scope_focus is not None:
        state.report_preferences.scope_focus = request_body.scope_focus
    if request_body.scope_summary.strip():
        confirmed_scope = request_body.scope_summary.strip()
        state.report_preferences.scope_summary = confirmed_scope
        state.report_preferences.scope_focus = "focused"
        state.assessment_profile.scope_summary = confirmed_scope
        state.assessment_profile.scope_summary_generated = False
        state.assessment_profile.scope_focus = "focused"
        state.assessment_profile.scope_focus_confidence = 1.0
        state.assessment_profile.scope_focus_reason = "用户在边界确认中补充并确认。"
        state.boundary_matrix.included_scope = list(dict.fromkeys(
            [confirmed_scope, *state.boundary_matrix.included_scope]
        ))
    if request_body.section_overrides is not None:
        state.report_preferences.section_overrides = request_body.section_overrides
    if request_body.selected_option == "supplement_materials":
        state.current_stage = "awaiting_confirmation"
        state.confirmation.needed = True
        await persist_task_state(task_id=task_id, user_scope=user_scope, state=state, status="awaiting_confirmation")
        return ContentAssessmentConfirmationResponse(run_id=task_id, status="awaiting_confirmation", state=state)
    state.confirmation.needed = False
    state.assessment_profile.confirmation_required = False
    if state.confirmation.reason == "scope":
        state.assessment_profile.pending_confirmation_fields = []
        if state.resume_from_stage == "document_intake":
            state.resume_from_stage = "claim_extraction"
    state.resume_from_stage = state.resume_from_stage or "claim_extraction"
    state.current_stage = state.resume_from_stage
    await _resume_task(task_manager, task_id, user_scope, state, "assessment", task)
    return ContentAssessmentConfirmationResponse(run_id=task_id, status="running", state=state)


@router.get("/v1/content-assessment/tasks/{task_id}/reviews")
async def list_content_assessment_reviews(
    task_id: str,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> dict[str, Any]:
    user_scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, user_scope)
    if not task or task.get("task_type", "创新性评估") != "创新性评估" or not task.get("state"):
        raise HTTPException(status_code=404, detail="任务不存在或状态已丢失")
    state = ContentAssessmentState.model_validate(task["state"])
    return {
        "success": True,
        "run_id": task_id,
        "status": task.get("status", ""),
        "items": [item.model_dump() for item in state.review_tasks],
        "resolution_logs": [item.model_dump() for item in state.resolution_logs],
    }


@router.post(
    "/v1/content-assessment/tasks/{task_id}/reviews/{review_task_id}",
    response_model=ReviewResolutionResponse,
)
async def resolve_content_assessment_review(
    task_id: str,
    review_task_id: str,
    request_body: ReviewResolutionRequest,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TaskManager = Depends(_get_task_manager),
) -> ReviewResolutionResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    repo = get_task_history_repo()
    task = await repo.get(task_id, user_scope)
    if not task or task.get("task_type", "创新性评估") != "创新性评估" or not task.get("state"):
        raise HTTPException(status_code=404, detail="任务不存在或状态已丢失")
    if task.get("status") in {"running", "processing", "queued", "cancelled", "failed"} or task_manager.is_running(task_id):
        raise HTTPException(status_code=409, detail="任务正在运行，暂不能处理复核任务")
    state = ContentAssessmentState.model_validate(task["state"])
    try:
        resume_stage = ContentAssessmentGraph.resolve_review_task(
            state,
            review_task_id,
            decision=request_body.decision,
            reason=request_body.reason,
            operator=request_body.operator or user_scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state.current_stage = resume_stage
    await _resume_task(task_manager, task_id, user_scope, state, "assessment", task)
    return ReviewResolutionResponse(run_id=task_id, status="running", state=state)


@router.post("/v1/content-assessment/tasks/{task_id}/stop", response_model=ContentAssessmentTaskHistoryDeleteResponse)
async def stop_content_assessment_task(
    task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TaskManager = Depends(_get_task_manager),
) -> ContentAssessmentTaskHistoryDeleteResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    repo = get_task_history_repo()
    task = await repo.get(task_id, user_scope)
    if not task or task.get("task_type", "创新性评估") != "创新性评估":
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") not in {"queued", "running", "processing", "awaiting_confirmation", "awaiting_review", "awaiting_correction"}:
        raise HTTPException(status_code=409, detail=f"当前任务状态为 {task.get('status')}，无法停止")
    await task_manager.cancel_task(task_id)
    await task_manager.release_slot(user_scope, task_id)
    if task.get("state"):
        state = ContentAssessmentState.model_validate(task["state"])
        state.current_stage = "cancelled"
        await persist_task_state(task_id=task_id, user_scope=user_scope, state=state, status="cancelled", completed_at=now_beijing_iso())
    else:
        await repo.update(task_id, user_scope, {"status": "cancelled", "updated_at": now_beijing_iso()})
    return ContentAssessmentTaskHistoryDeleteResponse(success=True, task_id=task_id)


@router.delete("/v1/content-assessment/tasks/running", response_model=BatchDeleteResponse)
async def delete_running_tasks(
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), task_manager: TaskManager = Depends(_get_task_manager)
) -> BatchDeleteResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    repo = get_task_history_repo()
    try:
        try:
            tasks = await repo.list_running(user_scope, task_type="创新性评估")
        except TypeError:
            # Keep compatibility with lightweight repository doubles used by legacy tests.
            tasks = [item for item in await repo.list_running(user_scope) if item.get("task_type", "创新性评估") == "创新性评估"]
        ids = [str(task["task_id"]) for task in tasks]
        for task_id in ids:
            await task_manager.cancel_task(task_id)
            await task_manager.release_slot(user_scope, task_id)
        deleted = await repo.delete_many(ids, user_scope) if ids else 0
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return BatchDeleteResponse(deleted_count=deleted, task_ids=ids)


@router.delete("/v1/content-assessment/tasks/{task_id}", response_model=ContentAssessmentTaskHistoryDeleteResponse)
async def delete_content_assessment_task(
    task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TaskManager = Depends(_get_task_manager),
) -> ContentAssessmentTaskHistoryDeleteResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    try:
        task = await get_task_history_repo().get(task_id, user_scope)
        if not task or task.get("task_type", "创新性评估") != "创新性评估":
            raise HTTPException(status_code=404, detail="任务不存在")
        if task.get("status") not in HISTORY_DELETABLE_STATUSES or task_manager.is_running(task_id):
            raise HTTPException(status_code=409, detail="任务尚未结束，请先完成或停止任务后再删除")
        result = await get_task_history_repo().delete_history(user_scope, task_id=task_id, task_type="创新性评估", is_running=task_manager.is_running)
        deleted = result["deleted_count"] > 0
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=409 if result["skipped_count"] else 404, detail="任务状态已变化，请刷新后重试" if result["skipped_count"] else "任务不存在")
    return ContentAssessmentTaskHistoryDeleteResponse(success=True, task_id=task_id)


_EXPORT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


@router.get("/v1/content-assessment/tasks/{task_id}/export")
async def export_content_assessment_report(
    task_id: str, format: str = Query(default="docx", pattern="^(md|html|docx|pdf|zip)$"),
    artifact: str = Query(default="report", pattern="^(report|evidence)$"),
    disposition: str = Query(default="attachment", pattern="^(attachment|inline)$"),
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> Response:
    user_scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, user_scope)
    if not task or not task.get("state"):
        raise HTTPException(status_code=404, detail="任务不存在或尚未形成报告")
    if disposition == "inline" and (format != "pdf" or artifact != "report"):
        raise HTTPException(status_code=422, detail="仅支持以内联方式预览 PDF 主报告")
    state = ContentAssessmentState.model_validate(task["state"])
    # Legacy states may not carry their owner; cache isolation uses the
    # authenticated repository scope rather than an optional saved field.
    state.user_scope = user_scope
    if state.confirmation.needed or state.current_stage not in {"completed", "report"}:
        raise HTTPException(status_code=409, detail="评估尚未完成，暂不能下载报告")
    if (artifact == "evidence") != (format == "zip"):
        raise HTTPException(status_code=422, detail="证据附件使用 ZIP；主报告使用 Markdown、Word 或 PDF")
    original_snapshot = task["state"].get("report_snapshot")
    persist_render_metrics = bool(original_snapshot and original_snapshot.get("template_version") in {
        "ContentAssessmentResultReportV3", "ContentAssessmentResultReportV4"})
    try:
        if artifact == "evidence":
            content, filename = await asyncio.to_thread(build_evidence_package, state)
        elif format == "docx":
            content, filename = await asyncio.to_thread(build_docx, state)
        elif format == "pdf":
            content, filename = await asyncio.to_thread(build_pdf, state)
        elif format == "html":
            content, filename = await asyncio.to_thread(build_html, state)
            content = content.encode("utf-8")
        else:
            content, filename = await asyncio.to_thread(build_markdown, state)
            content = content.encode("utf-8")
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    if persist_render_metrics:
        report_fields = {"updated_at": now_beijing_iso(), "state.report_snapshot": original_snapshot,
            "state.report_plan": state.report_plan.model_dump(), "state.report_metrics": state.report_metrics.model_dump(),
            "state.output_markdown": state.output_markdown, "output_markdown": state.output_markdown}
        for key in ("report_metrics", "report_plan", "quality_checks", "pdf_unavailable"):
            report_fields[f"state.output_json.{key}"] = state.output_json.get(key)
            report_fields[f"output_json.{key}"] = state.output_json.get(key)
        if not await get_task_history_repo().update_report(task_id, user_scope, task.get("updated_at", ""), report_fields):
            latest = await get_task_history_repo().get(task_id, user_scope)
            latest_state = (latest or {}).get("state", {})
            if ((latest_state.get("report_snapshot") or {}).get("snapshot_id") != state.report_snapshot.snapshot_id
                    or (latest or {}).get("status") != task.get("status")):
                raise HTTPException(status_code=409, detail="报告版本已更新，请重新下载当前版本")
    # Exporters return a reader-facing, project-specific filename. Include an
    # ASCII fallback for older browsers and RFC 5987 UTF-8 value for modern ones.
    ascii_fallback = f"content-assessment-report.{format}"
    encoded_filename = quote(filename)
    content_disposition = f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_filename}"
    return Response(content=content, media_type="application/zip" if format == "zip" else _EXPORT_TYPES[format], headers={
        "Content-Disposition": content_disposition,
        "Access-Control-Expose-Headers": "Content-Disposition",
    })


@router.post("/v1/topic-selection/run", response_model=TopicSelectionRunResponse)
async def run_topic_selection(
    request: Request,
    request_body: TopicSelectionRunRequest,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    service: TopicSelectionService = Depends(get_topic_service),
    task_manager: TopicTaskManager = Depends(_get_topic_task_manager),
) -> TopicSelectionRunResponse:
    user_scope = _resolve_user_scope(x_user_scope)
    run_id = generate_run_id()
    allowed, message = await task_manager.rate_limiter.check_and_acquire(user_scope, run_id)
    if not allowed:
        raise await _quota_error(user_scope, "topic", task_manager, message)
    try:
        state = service.create_initial_state(
            request_body.user_input,
            topic_title=request_body.topic_title,
            uploaded_files=request_body.uploaded_files,
            constraints=request_body.constraints,
            exclusions=request_body.exclusions,
            language_preference=request_body.language_preference,
            year_range=request_body.year_range,
            enable_web=request_body.enable_web,
            enable_cloud_model=request_body.enable_cloud_model,
            sensitivity_level=request_body.sensitivity_level,
            freshness_days=request_body.freshness_days,
            candidate_count=request_body.candidate_count,
            output_preference=request_body.output_preference,
            evidence_cutoff_at=request_body.evidence_cutoff_at,
            source_policy=request_body.source_policy,
            scoring_objective=request_body.scoring_objective,
            scoring_weight_overrides=request_body.scoring_weight_overrides,
            analysis_mode=request_body.analysis_mode,
        )
        state.user_scope = user_scope
        model_health = dict(getattr(request.app.state, "model_health", {}) or {})
        preflight_status = str(model_health.get("status") or "unknown")
        state.analysis_policy.model_preflight_status = preflight_status if preflight_status in {"unknown", "available", "unavailable", "not_configured"} else "unknown"
        state.analysis_policy.model_preflight_reason = str(model_health.get("error") or model_health.get("error_code") or "")[:240]
        state.analysis_policy.model_profile = str(model_health.get("profile") or "")
        state.analysis_policy.model_name = str(model_health.get("model") or "")
        state.analysis_policy.model_endpoint = str(model_health.get("endpoint") or "")
        # Attach bounded parsed excerpts when the caller references files saved by
        # the existing upload service. Full text is deliberately kept out of the
        # task input; future full-text chunking can extend this field without
        # changing the TopicSelectionState contract.
        for document in state.input_profile.documents:
            try:
                record = await get_parsed_file_repo().get(document.document_id, user_scope, include_content=True)
            except Exception:
                record = None
            if record:
                content = str(record.get("content") or "")
                document.status = "parsed" if content else "unavailable"
                document.content_hash = str(record.get("content_hash") or "")
                document.excerpt = content[:4000]
                if not content:
                    document.parse_warnings.append("文件没有可用的解析文本")
        state.checkpoint_id = run_id
        now = topic_now_beijing_iso()
        await get_task_history_repo().create({
            "task_id": run_id, "user_scope": user_scope, "user_name": user_scope,
            "title": state.input_profile.title, "task_type": "智能选题", "raw_user_input": state.raw_user_input,
            "status": "queued", "current_stage": "created", "completed_steps": 0,
            "total_steps": len(state.stage_trace), "needs_confirmation": False, "confirmation_status": "not_required",
            "summary": "已创建智能选题任务。", "created_at": now, "updated_at": now, "queued_at": now,
            "state": state.model_dump(), "stage_trace": [item.model_dump() for item in state.stage_trace],
            "output_markdown": "", "output_json": {},
        })
        await _start_task(task_manager, run_id, user_scope, state)
        return TopicSelectionRunResponse(run_id=run_id, status="running", state=state)
    except Exception as exc:
        await task_manager.rate_limiter.release(user_scope, run_id)
        logger.exception("[topic-selection] 创建任务失败 user=%s", user_scope)
        raise HTTPException(status_code=500, detail=f"创建智能选题任务失败：{exc}") from exc


@router.get("/v1/topic-selection/tasks/{task_id}/stream")
async def stream_topic_selection_task(
    task_id: str,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    user_scope: str | None = Query(default=None),
) -> StreamingResponse:
    scope = _resolve_user_scope(x_user_scope or user_scope)
    repo = get_task_history_repo()

    async def events():
        last_updated = ""
        while True:
            task = await repo.get(task_id, scope)
            if not task or task.get("task_type") != "智能选题":
                yield f"event: error\ndata: {json.dumps({'message': '任务不存在'}, ensure_ascii=False)}\n\n"
                return
            updated = str(task.get("updated_at", ""))
            if updated != last_updated:
                event = "state" if not last_updated else "delta"
                yield f"event: {event}\ndata: {json.dumps(task, ensure_ascii=False, default=str)}\n\n"
                last_updated = updated
            if task.get("status") in {"completed", "failed", "cancelled", "blocked", "awaiting_confirmation", "awaiting_review", "awaiting_correction", "superseded", "model_unavailable"}:
                yield f"event: complete\ndata: {json.dumps(task, ensure_ascii=False, default=str)}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/v1/topic-selection/tasks")
async def list_topic_selection_tasks(
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100), status: str | None = Query(default=None), search: str | None = Query(default=None),
) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    records, total = await get_task_history_repo().list_tasks(scope, status=status, search=search, page=page, page_size=page_size, task_type="智能选题")
    items = []
    for task in records:
        completed, total_steps = _progress(task)
        items.append({
            "id": task.get("task_id", str(task.get("_id", ""))), "task_id": task.get("task_id", ""),
            "title": _display_task_title(task), "task_type": "智能选题", "status": task.get("status", ""),
            "recovery": task.get("recovery") or task.get("output_json", {}).get("generation_recovery"),
            "current_stage": task.get("current_stage", ""), "completed_steps": completed, "total_steps": total_steps,
            "needs_confirmation": task.get("needs_confirmation", False), "confirmation_status": task.get("confirmation_status", "not_required"),
            "summary": task.get("summary", ""), "created_at": task.get("created_at", ""), "updated_at": task.get("updated_at", ""),
            "completed_at": task.get("completed_at"), "duration_seconds": task.get("duration_seconds"),
        })
    return {"success": True, "items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/v1/topic-selection/tasks/{task_id}")
async def get_topic_selection_task(task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope")) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题":
        raise HTTPException(status_code=404, detail="选题任务不存在")
    completed, total_steps = _progress(task)
    return {"success": True, "data": {
        "id": task.get("task_id", str(task.get("_id", ""))), "task_id": task.get("task_id", ""),
        "user_scope": task.get("user_scope", ""), "user_name": task.get("user_name", ""), "title": _display_task_title(task),
        "task_type": "智能选题", "raw_user_input": task.get("raw_user_input", ""), "status": task.get("status", ""),
        "current_stage": task.get("current_stage", ""), "completed_steps": completed, "total_steps": total_steps,
        "needs_confirmation": task.get("needs_confirmation", False), "confirmation_status": task.get("confirmation_status", "not_required"),
        "summary": task.get("summary", ""), "created_at": task.get("created_at", ""), "updated_at": task.get("updated_at", ""),
        "completed_at": task.get("completed_at"), "duration_seconds": task.get("duration_seconds"), "state": task.get("state"),
        "output_markdown": task.get("output_markdown", ""), "output_json": task.get("output_json", {}),
        "stage_trace": task.get("stage_trace", []), "error_message": task.get("error_message"),
    }}


@router.post("/v1/topic-selection/tasks/{task_id}/confirmation")
async def submit_topic_confirmation(
    task_id: str, request_body: TopicConfirmationRequest, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TopicTaskManager = Depends(_get_topic_task_manager),
) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题" or not task.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或状态已丢失")
    state = TopicSelectionState.model_validate(task["state"])
    if task.get("status") != "awaiting_confirmation" or task_manager.is_running(task_id):
        report_retry = (task.get("status") == "failed" and request_body.selected_option == "retry_report"
                        and not state.report_snapshot.frozen)
        if not report_retry or task_manager.is_running(task_id):
            raise HTTPException(status_code=409, detail="任务当前不能继续，请刷新后重试")
    state.error_message = ''
    if request_body.selected_option == 'retry_report':
        from app.agents.topic_selection.recovery import schedule_recovery, GenerationRecoveryRequired
        pending = ['report_publish']
        if state.stage_artifacts.get('crosslink_gap', {}).get('generation_status') != 'ready' and not any(link.method_evidence for link in state.crosslinks):
            pending.insert(0, 'crosslink_gap')
        schedule_recovery(state, GenerationRecoveryRequired(pending[0], 'legacy_retry', '恢复已保存的分析及报告。'))
        state.output_json['generation_recovery']['pending_stages'] = pending
    state.confirmation.selected_option = request_body.selected_option
    state.confirmation.user_note = request_body.user_note
    state.confirmation.needed = False
    state.resume_from_stage = state.resume_from_stage or "quality_gate"
    state.current_stage = state.resume_from_stage
    await _resume_task(task_manager, task_id, scope, state, "topic", task)
    return {"success": True, "run_id": task_id, "status": "running", "state": state.model_dump()}


@router.post("/v1/topic-selection/tasks/{task_id}/candidate-selection")
async def submit_topic_candidate_selection(
    task_id: str,
    request_body: CandidateSelectionRequest,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
) -> dict[str, Any]:
    """记录报告后用户选择的一个或多个候选题，供后续内容评估/立项分析使用。"""
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题" or not task.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或尚未形成报告")
    state = TopicSelectionState.model_validate(task["state"])
    valid_ids = {candidate.candidate_id for candidate in state.candidates}
    selected = list(dict.fromkeys(request_body.selected_candidate_ids))
    invalid = [item for item in selected if item not in valid_ids]
    if invalid:
        raise HTTPException(status_code=400, detail=f"候选题编号无效：{', '.join(invalid)}")
    state.candidate_selection = CandidateSelection(
        selected_candidate_ids=selected,
        reason=request_body.reason,
        operator=request_body.operator or scope,
        selected_at=topic_now_beijing_iso(),
        report_version=state.versions.report_version,
    )
    state.process_trace.setdefault("candidate_selection", []).append(state.candidate_selection.model_dump())
    await persist_topic_task_state(task_id=task_id, user_scope=scope, state=state, status=task.get("status", "completed"))
    return {"success": True, "run_id": task_id, "status": task.get("status", "completed"), "selection": state.candidate_selection.model_dump(), "state": state.model_dump()}


@router.get("/v1/topic-selection/tasks/{task_id}/reviews")
async def list_topic_reviews(task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope")) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题" or not task.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或状态已丢失")
    state = TopicSelectionState.model_validate(task["state"])
    return {"success": True, "run_id": task_id, "status": task.get("status", ""), "items": state.review_tasks, "resolution_logs": state.resolution_logs}


@router.get("/v1/topic-selection/tasks/{task_id}/report/charts/{chart_key}")
async def topic_report_chart(task_id: str, chart_key: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope")) -> Response:
    task = await get_task_history_repo().get(task_id, _resolve_user_scope(x_user_scope))
    if not task or task.get("task_type") != "智能选题" or not task.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在")
    state = _bounded_export_state(TopicSelectionState.model_validate(task["state"]))
    spec = next((item for item in state.report_plan.chart_specs if item.get("key") == chart_key), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="图表不存在")
    content = await asyncio.to_thread(_render_chart_png, spec)
    return Response(content=content or b"", status_code=200 if content else 204, media_type="image/png", headers={"Cache-Control": "private, no-cache", "X-Report-Version": state.versions.report_version})


@router.post("/v1/topic-selection/tasks/{task_id}/reviews/{review_task_id}")
async def resolve_topic_review(
    task_id: str, review_task_id: str, request_body: TopicReviewRequest,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), task_manager: TopicTaskManager = Depends(_get_topic_task_manager),
) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题" or not task.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或状态已丢失")
    if task_manager.is_running(task_id):
        raise HTTPException(status_code=409, detail="任务正在运行，暂不能处理复核")
    state = TopicSelectionState.model_validate(task["state"])
    review = next((item for item in state.review_tasks if item.get("review_task_id") == review_task_id), None)
    if review is None:
        raise HTTPException(status_code=404, detail="复核任务不存在")
    if task.get("status") != "awaiting_review" or review.get("status") != "open":
        raise HTTPException(status_code=409, detail="复核任务已处理或当前状态不允许重复操作")
    resolved_at = topic_now_beijing_iso()
    state.resolution_logs.append({"review_task_id": review_task_id, "decision": request_body.decision, "reason": request_body.reason, "operator": request_body.operator or scope, "resolved_at": resolved_at})
    if request_body.decision != "approve":
        if not request_body.reason.strip():
            raise HTTPException(status_code=422, detail="退回补正必须填写补正原因")
        review["status"] = "returned"
        review["correction_reason"] = request_body.reason.strip()
        state.resume_from_stage = ""
        state.current_stage = "awaiting_correction"
        state.quality_gate.review_required = True
        state.report_plan.publication_status = "eligible" if state.quality_gate.report_eligibility == "formal" else "blocked"
        state.report_plan.cover_metadata["review_status"] = "待补正"
        state.correction_context.source_task_id = task_id
        state.correction_context.reason = request_body.reason.strip()
        state.correction_context.operator = request_body.operator or scope
        state.correction_context.returned_at = resolved_at
        state.correction_context.status = "awaiting_correction"
        status = "awaiting_correction"
    else:
        review["status"] = "resolved"
    if request_body.decision == "approve" and any(item.get("status") != "resolved" for item in state.review_tasks):
        status = "awaiting_review"
    elif request_body.decision == "approve":
        state.quality_gate.review_required = False
        state.current_stage = "completed"
        state.active_progress = None
        if request_body.decision == "approve" and state.quality_gate.report_eligibility == "formal":
            state.report_plan.publication_status = "published"
            state.report_plan.cover_metadata["review_status"] = "人工复核已通过"
        if state.output_json:
            state.output_json["quality_gate"] = state.quality_gate.model_dump()
            state.output_json["report_plan"] = state.report_plan.model_dump()
        status = "completed"
    try:
        await persist_topic_task_state(task_id=task_id, user_scope=scope, state=state, status=status, completed_at=resolved_at if status == "completed" else None, expected_updated_at=str(task.get("updated_at") or ""))
    except TaskHistoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "run_id": task_id, "status": status, "state": state.model_dump()}


async def _supersede_correction_source(task_id: str, child_id: str, scope: str) -> bool:
    """Finalize a claimed correction without overwriting a concurrent user action."""
    repo = get_task_history_repo()
    for _ in range(3):
        current = await repo.get(task_id, scope)
        if not current or not current.get("state"):
            return False
        state = TopicSelectionState.model_validate(current["state"])
        if state.correction_context.correction_task_id != child_id:
            return False
        if current.get("status") == "superseded":
            return True
        if current.get("status") != "awaiting_correction":
            return False
        now = topic_now_beijing_iso()
        state.correction_context.rerun_at = now
        state.correction_context.status = "superseded"
        state.current_stage = "superseded"
        state.report_plan.cover_metadata["review_status"] = "已进入补正版本"
        try:
            await persist_topic_task_state(
                task_id=task_id, user_scope=scope, state=state, status="superseded",
                completed_at=now, expected_updated_at=str(current.get("updated_at") or ""),
            )
            return True
        except TaskHistoryConflict:
            continue
    return False


def _reset_topic_state_from_retrieval(previous: TopicSelectionState, task_id: str, scope: str) -> TopicSelectionState:
    """Clone a saved task while retaining only the two pre-retrieval stages."""
    state = previous.model_copy(deep=True)
    resume_key = "retrieval_evidence"
    resume_index = next(index for index, (key, _) in enumerate(TopicSelectionAgent.STAGES) if key == resume_key)
    for index, trace in enumerate(state.stage_trace):
        if index < resume_index:
            continue
        trace.status = "pending"; trace.summary = ""; trace.started_at = None; trace.completed_at = None
        trace.duration_seconds = None; trace.progress_percent = 0; trace.active_step = ""; trace.metrics = {}
        trace.model_status = "not_requested"; trace.prompt_version = ""; trace.model_call_count = 0
        trace.model_duration_ms = None; trace.deterministic_duration_ms = None; trace.external_io_duration_ms = None
        trace.model_error_code = ""; trace.execution_mode = "deterministic"; trace.fallback_reason = ""; trace.degradation_reason = ""
    rerun_keys = {key for key, _ in TopicSelectionAgent.STAGES[resume_index:]}
    state.stage_artifacts = {key: value for key, value in state.stage_artifacts.items() if key not in rerun_keys}
    state.model_calls = [item for item in state.model_calls if item.stage_key not in rerun_keys]
    state.process_trace = {key: value for key, value in state.process_trace.items() if key not in rerun_keys}
    state.search_traces = []; state.source_queries = []; state.external_evidence = []; state.evidence_chunks = []
    state.evidence_pool = []; state.query_plan = type(state.query_plan)()
    state.evidence_lock = type(state.evidence_lock)(); state.freshness_snapshot = type(state.freshness_snapshot)()
    state.model_insights = []
    for node in state.theme_tree.nodes:
        node.evidence_ids = []
    state.landscape_snapshot = None; state.future_work_signals = []; state.crosslinks = []; state.gaps = []
    state.candidates = []; state.score_breakdowns = []; state.quality_gate = type(state.quality_gate)(); state.review_tasks = []
    state.report_plan = type(state.report_plan)(); state.report_snapshot = type(state.report_snapshot)()
    state.output_markdown = ""; state.output_json = {}; state.active_progress = None; state.progress_events = []
    state.candidate_selection = None; state.confirmation = type(state.confirmation)(); state.resolution_logs = []
    state.retry_count = 0; state.error_message = ""; state.worker_id = ""; state.checkpoint_revision = 0
    state.parent_task_id = task_id; state.checkpoint_id = ""; state.user_scope = scope
    state.resume_from_stage = resume_key; state.current_stage = resume_key
    state.versions = type(state.versions)(
        input_version=previous.versions.input_version,
        theme_tree_version=previous.versions.theme_tree_version,
        parent_report_version=previous.versions.report_version,
    )
    return state


@router.post("/v1/topic-selection/tasks/{task_id}/retrieval-rerun", response_model=TopicSelectionRunResponse)
async def rerun_topic_retrieval(
    task_id: str,
    request: Request,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TopicTaskManager = Depends(_get_topic_task_manager),
) -> TopicSelectionRunResponse:
    scope = _resolve_user_scope(x_user_scope)
    repo = get_task_history_repo()
    original = await repo.get(task_id, scope)
    if not original or original.get("task_type") != "智能选题" or not original.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或状态已丢失")
    if original.get("status") != "completed":
        raise HTTPException(status_code=409, detail="只有已完成任务可以创建检索修正版")
    existing, _ = await repo.list_tasks(scope, page=1, page_size=100, task_type="智能选题")
    child = next((item for item in existing if item.get("parent_task_id") == task_id and item.get("rerun_kind") == "retrieval_revision"), None)
    if child:
        child = await repo.get(child["task_id"], scope)
    if child and child.get("state"):
        child_state = TopicSelectionState.model_validate(child["state"])
        recovered_stats = dict(child_state.output_json.get("fulltext_retrieval_stats") or {})
        trace = next((item for item in reversed(child_state.process_trace.get("retrieval_evidence", [])) if item.get("code") == "fulltext_fusion" and item.get("stats")), None)
        if trace and not recovered_stats:
            recovered_stats = dict(trace["stats"])
        if recovered_stats and ("chinese_query_variant_count" not in recovered_stats or "english_query_variant_count" not in recovered_stats):
            recovered_stats["chinese_query_variant_count"] = sum(
                bool(re.search(r"[\u3400-\u9fff]", branch.query)) for branch in child_state.query_plan.branches
            )
            recovered_stats["english_query_variant_count"] = sum(
                any(re.search(r"[A-Za-z]", term) for term in branch.synonyms)
                for branch in child_state.query_plan.branches
            )
            filtered = recovered_stats.get("filtered", {})
            recovered_stats["diagnostic_message"] = (
                f"返回 {recovered_stats.get('raw_candidates', 0)} 个候选，其中来源不全 {filtered.get('invalid_source', 0)}、"
                f"非正文 {filtered.get('non_body', 0)}、重复 {filtered.get('duplicate', 0)}、"
                f"相关性不足 {filtered.get('below_relevance', 0)}、覆盖上限 {filtered.get('coverage_cap', 0)}，"
                f"最终采用 {recovered_stats.get('accepted_chunks', 0)} 段。"
            )
            child_state.output_json["fulltext_retrieval_stats"] = recovered_stats
        if recovered_stats != child.get("output_json", {}).get("fulltext_retrieval_stats"):
            await repo.update(child["task_id"], scope, {
                "state": child_state.model_dump(), "output_json": child_state.output_json,
                "updated_at": topic_now_beijing_iso(),
            })
        return TopicSelectionRunResponse(run_id=child["task_id"], status=child.get("status", "running"), state=child_state)
    previous = TopicSelectionState.model_validate(original["state"])
    state = _reset_topic_state_from_retrieval(previous, task_id, scope)
    state.input_profile.time_range = []
    state.input_profile.time_range_source = "unspecified"
    state.input_profile.languages = []
    state.task_profile["language_preference"] = "mixed"
    state.task_profile["rerun_kind"] = "retrieval_revision"
    new_task_id = generate_run_id()
    state.checkpoint_id = new_task_id
    allowed, message = await task_manager.rate_limiter.check_and_acquire(scope, new_task_id)
    if not allowed:
        raise await _quota_error(scope, "topic", task_manager, message)
    now = topic_now_beijing_iso()
    try:
        await repo.create({
            "task_id": new_task_id, "user_scope": scope, "user_name": scope, "title": state.input_profile.title,
            "task_type": "智能选题", "raw_user_input": state.raw_user_input, "status": "queued",
            "current_stage": "retrieval_evidence", "completed_steps": 2, "total_steps": len(state.stage_trace),
            "needs_confirmation": False, "confirmation_status": "not_required",
            "summary": "按不限年份、不限语言重新执行全文检索及后续分析。", "created_at": now,
            "updated_at": now, "queued_at": now, "parent_task_id": task_id, "rerun_kind": "retrieval_revision",
            "state": state.model_dump(), "stage_trace": [item.model_dump() for item in state.stage_trace],
            "output_markdown": "", "output_json": {},
        })
        await _start_task(task_manager, new_task_id, scope, state)
        return TopicSelectionRunResponse(run_id=new_task_id, status="running", state=state)
    except Exception as exc:
        await task_manager.rate_limiter.release(scope, new_task_id)
        logger.exception("[topic-selection] 创建检索修正版失败 task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=f"创建检索修正版失败：{exc}") from exc


@router.post("/v1/topic-selection/tasks/{task_id}/corrections/rerun", response_model=TopicSelectionRunResponse)
async def rerun_topic_correction(
    task_id: str,
    request: Request,
    x_user_scope: str | None = Header(default=None, alias="X-User-Scope"),
    task_manager: TopicTaskManager = Depends(_get_topic_task_manager),
) -> TopicSelectionRunResponse:
    scope = _resolve_user_scope(x_user_scope)
    repo = get_task_history_repo()
    original = await repo.get(task_id, scope)
    if not original or original.get("task_type") != "智能选题" or not original.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或状态已丢失")
    previous = TopicSelectionState.model_validate(original["state"])
    existing_id = previous.correction_context.correction_task_id
    if existing_id and existing_id != task_id:
        existing = await repo.get(existing_id, scope)
        if existing and existing.get("state"):
            if not await _supersede_correction_source(task_id, existing_id, scope):
                raise HTTPException(status_code=409, detail="原任务状态已变化，补正操作无法继续")
            return TopicSelectionRunResponse(
                run_id=existing_id, status=str(existing.get("status") or "running"),
                state=TopicSelectionState.model_validate(existing["state"]),
            )
        raise HTTPException(status_code=409, detail="补正版已创建，请刷新任务列表")
    if original.get("status") != "awaiting_correction":
        raise HTTPException(status_code=409, detail="当前任务不在待补正状态")
    if not previous.correction_context.reason:
        returned = next((item for item in reversed(previous.resolution_logs) if item.get("decision") != "approve" and item.get("reason")), {})
        previous.correction_context.reason = str(returned.get("reason") or "")
        previous.correction_context.operator = str(returned.get("operator") or scope)
        previous.correction_context.returned_at = str(returned.get("resolved_at") or "")
    reason = previous.correction_context.reason.strip()
    if not reason:
        raise HTTPException(status_code=409, detail="补正原因缺失，无法重新执行")
    new_task_id = generate_run_id()
    allowed, message = await task_manager.rate_limiter.check_and_acquire(scope, new_task_id)
    if not allowed:
        raise await _quota_error(scope, "topic", task_manager, message)
    claim_at = topic_now_beijing_iso()
    claimed = False
    child_created = False
    start_attempted = False
    try:
        # Reserve the only child before creating it. Database CAS protects across workers.
        claimed = await repo.update_report(task_id, scope, str(original.get("updated_at") or ""), {
            "state.correction_context.correction_task_id": new_task_id, "updated_at": claim_at,
        })
        if not claimed:
            raise HTTPException(status_code=409, detail="补正操作已被处理，请刷新查看关联版本")
        state = previous.model_copy(deep=True)
        resume_key = "retrieval_evidence"
        resume_index = next(index for index, (key, _) in enumerate(TopicSelectionAgent.STAGES) if key == resume_key)
        for index, trace in enumerate(state.stage_trace):
            if index < resume_index:
                continue
            trace.status = "pending"; trace.summary = ""; trace.started_at = None; trace.completed_at = None
            trace.duration_seconds = None; trace.progress_percent = 0; trace.active_step = ""; trace.metrics = {}
            trace.model_status = "not_requested"; trace.prompt_version = ""; trace.model_call_count = 0
            trace.model_duration_ms = None; trace.deterministic_duration_ms = None; trace.external_io_duration_ms = None
            trace.model_error_code = ""; trace.execution_mode = "deterministic"; trace.fallback_reason = ""; trace.degradation_reason = ""
        rerun_keys = {key for key, _ in TopicSelectionAgent.STAGES[resume_index:]}
        state.stage_artifacts = {key: value for key, value in state.stage_artifacts.items() if key not in rerun_keys}
        state.model_calls = [item for item in state.model_calls if item.stage_key not in rerun_keys]
        state.process_trace = {key: value for key, value in state.process_trace.items() if key not in rerun_keys}
        state.search_traces = []; state.source_queries = []; state.external_evidence = []; state.evidence_chunks = []
        state.evidence_pool = []; state.query_plan = type(state.query_plan)()
        state.evidence_lock = type(state.evidence_lock)(); state.freshness_snapshot = type(state.freshness_snapshot)()
        state.model_insights = []
        for node in state.theme_tree.nodes:
            node.evidence_ids = []
        state.landscape_snapshot = None; state.future_work_signals = []; state.crosslinks = []; state.gaps = []
        state.candidates = []; state.score_breakdowns = []; state.quality_gate = type(state.quality_gate)(); state.review_tasks = []
        state.report_plan = type(state.report_plan)(); state.report_snapshot = type(state.report_snapshot)()
        state.output_markdown = ""; state.output_json = {}; state.active_progress = None; state.progress_events = []
        state.candidate_selection = None; state.confirmation = type(state.confirmation)(); state.resolution_logs = []
        state.retry_count = 0; state.error_message = ""; state.worker_id = ""; state.checkpoint_revision = 0
        state.parent_task_id = task_id; state.checkpoint_id = new_task_id; state.user_scope = scope
        state.resume_from_stage = resume_key; state.current_stage = resume_key
        state.versions = type(state.versions)(
            input_version=previous.versions.input_version,
            theme_tree_version=previous.versions.theme_tree_version,
            parent_report_version=previous.versions.report_version,
        )
        state.task_profile["correction_reason"] = reason
        state.constraint_card.must_include = list(dict.fromkeys([*state.constraint_card.must_include, f"补正要求：{reason}"]))
        state.correction_context.source_task_id = task_id
        state.correction_context.correction_task_id = ""
        state.correction_context.rerun_at = topic_now_beijing_iso()
        state.correction_context.status = "rerun_created"
        model_health = dict(getattr(request.app.state, "model_health", {}) or {})
        preflight_status = str(model_health.get("status") or "unknown")
        state.analysis_policy.model_preflight_status = preflight_status if preflight_status in {"unknown", "available", "unavailable", "not_configured"} else "unknown"
        now = topic_now_beijing_iso()
        await repo.create({
            "task_id": new_task_id, "user_scope": scope, "user_name": scope, "title": state.input_profile.title,
            "task_type": "智能选题", "raw_user_input": state.raw_user_input, "status": "queued", "current_stage": resume_key,
            "completed_steps": resume_index, "total_steps": len(state.stage_trace), "needs_confirmation": False,
            "confirmation_status": "not_required", "summary": f"根据补正意见重新执行：{reason}", "created_at": now,
            "updated_at": now, "queued_at": now, "parent_task_id": task_id, "state": state.model_dump(),
            "stage_trace": [item.model_dump() for item in state.stage_trace], "output_markdown": "", "output_json": {},
        })
        child_created = True
        if not await _supersede_correction_source(task_id, new_task_id, scope):
            raise HTTPException(status_code=409, detail="原任务状态已变化，补正版未启动，请刷新查看")
        start_attempted = True
        await _start_task(task_manager, new_task_id, scope, state)
        return TopicSelectionRunResponse(run_id=new_task_id, status="running", state=state)
    except HTTPException:
        if claimed and not child_created:
            await repo.update_report(task_id, scope, claim_at, {"state": original["state"], "updated_at": topic_now_beijing_iso()})
        elif child_created and not start_attempted:
            await mark_topic_task_failed(new_task_id, scope, state, "原任务状态已变化，补正版未启动")
        await task_manager.rate_limiter.release(scope, new_task_id)
        raise
    except Exception as exc:
        if claimed and not child_created:
            await repo.update_report(task_id, scope, claim_at, {"state": original["state"], "updated_at": topic_now_beijing_iso()})
        elif child_created and not start_attempted:
            await mark_topic_task_failed(new_task_id, scope, state, "补正版关联保存失败，未启动执行")
        await task_manager.rate_limiter.release(scope, new_task_id)
        logger.exception("[topic-selection] 创建补正版失败 task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=f"创建补正版失败：{exc}") from exc


@router.post("/v1/topic-selection/tasks/{task_id}/stop")
async def stop_topic_selection_task(task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), task_manager: TopicTaskManager = Depends(_get_topic_task_manager)) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题":
        raise HTTPException(status_code=404, detail="选题任务不存在")
    if task.get("status") not in {"queued", "running", "processing", "awaiting_confirmation", "awaiting_review", "awaiting_correction"}:
        raise HTTPException(status_code=409, detail="当前任务无法停止")
    await task_manager.cancel_task(task_id)
    await task_manager.release_slot(scope, task_id)
    task = await get_task_history_repo().get(task_id, scope)
    if task and task.get("state"):
        state = TopicSelectionState.model_validate(task["state"])
        state.output_json.pop("generation_recovery", None)
        state.current_stage = "cancelled"
        try:
            await persist_topic_task_state(task_id=task_id, user_scope=scope, state=state, status="cancelled", completed_at=topic_now_beijing_iso(), expected_updated_at=str(task.get("updated_at") or ""))
        except TaskHistoryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "task_id": task_id}


@router.delete("/v1/topic-selection/tasks/{task_id}")
async def delete_topic_selection_task(task_id: str, x_user_scope: str | None = Header(default=None, alias="X-User-Scope"), task_manager: TopicTaskManager = Depends(_get_topic_task_manager)) -> dict[str, Any]:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题":
        raise HTTPException(status_code=404, detail="选题任务不存在")
    if task.get("status") not in HISTORY_DELETABLE_STATUSES or task_manager.is_running(task_id):
        raise HTTPException(status_code=409, detail="任务尚未结束，请先完成或停止任务后再删除")
    try:
        result = await get_task_history_repo().delete_history(scope, task_id=task_id, task_type="智能选题", is_running=task_manager.is_running)
    except TaskHistoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    deleted = result["deleted_count"] > 0
    if not deleted:
        raise HTTPException(status_code=409 if result["skipped_count"] else 404, detail="任务状态已变化，请刷新后重试" if result["skipped_count"] else "选题任务不存在")
    return {"success": True, "task_id": task_id}


@router.get("/v1/topic-selection/tasks/{task_id}/export")
async def export_topic_selection_report(
    task_id: str, format: str = Query(default="docx", pattern="^(md|html|docx|pdf)$"), x_user_scope: str | None = Header(default=None, alias="X-User-Scope")
) -> Response:
    scope = _resolve_user_scope(x_user_scope)
    task = await get_task_history_repo().get(task_id, scope)
    if not task or task.get("task_type") != "智能选题" or not task.get("state"):
        raise HTTPException(status_code=404, detail="选题任务不存在或尚未形成报告")
    state = TopicSelectionState.model_validate(task["state"])
    if state.report_plan.template_version == "TopicReportV7" and (
        not state.report_snapshot.report_plan_snapshot or not state.report_snapshot.frozen
        or state.report_plan.publication_status != "published"
    ):
        raise HTTPException(status_code=409, detail="报告正在整理与校核，完整发布后即可下载。已有分析已保存。")
    if format == "docx":
        content, filename = await asyncio.to_thread(build_topic_docx, state)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format == "pdf":
        content, filename = await asyncio.to_thread(build_topic_pdf, state)
        media_type = "application/pdf"
    elif format == "html":
        content, filename = build_topic_html(state)
        media_type = "text/html; charset=utf-8"
    else:
        content, filename = build_topic_markdown(state)
        media_type = "text/markdown; charset=utf-8"
    disposition = f"attachment; filename=\"topic-selection-report.{format}\"; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": disposition, "Access-Control-Expose-Headers": "Content-Disposition", "Cache-Control": "private, no-cache", "X-Report-Renderer-Version": TOPIC_CHART_RENDER_VERSION})


@router.get("/system/health/queue", response_model=QueueHealthResponse)
async def queue_health(request: Request, task_manager: TaskManager = Depends(_get_task_manager)) -> QueueHealthResponse:
    topic_manager = getattr(request.app.state, "topic_task_manager", None)
    return QueueHealthResponse(active_tasks=task_manager.active_count + (topic_manager.active_count if topic_manager else 0))
