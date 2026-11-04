"""Build bounded uploaded-file context for innovation assessment runs."""

from __future__ import annotations

import re
from typing import Iterable

from app.agents.content_assessment.schemas import (
    ContentAssessmentState,
    UploadedFileContext,
    UploadedFileDigest,
    UploadedFileRef,
)
from app.core.config import MAX_FILE_CONTEXT_PER_FILE_CHARS, MAX_FILE_CONTEXT_TOTAL_CHARS, MAX_UPLOAD_FILE_COUNT
from app.repositories.content_assessment_parsed_file import get_parsed_file_repo


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")


def _clean_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\u200b", "")
        .replace("-\n", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _keywords(text: str, *, limit: int = 10) -> list[str]:
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "are", "was",
        "were", "研究", "方法", "结果", "分析", "问题", "基于", "相关",
    }
    counts: dict[str, int] = {}
    for match in _WORD_RE.finditer(text[:50000]):
        word = match.group(0).strip().lower()
        if word in stop or len(word) < 2:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _dedupe_refs(refs: Iterable[UploadedFileRef]) -> list[UploadedFileRef]:
    seen: set[str] = set()
    result: list[UploadedFileRef] = []
    for ref in refs:
        file_id = (ref.file_id or "").strip()
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        result.append(ref)
    return result


def _brief_from_digests(digests: list[UploadedFileDigest], *, max_chars: int) -> str:
    if not digests:
        return ""
    parts = [
        f"用户上传了 {len(digests)} 个参考文件。以下内容仅作为用户补充材料，不等同于 NSTL 检索文献。"
    ]
    for index, item in enumerate(digests, start=1):
        keyword_text = "、".join(item.keywords[:10]) or "无"
        parts.append(
            "\n".join([
                f"【上传文件{index}】{item.name}",
                f"类型：{item.type}；解析字符数：{item.content_size}",
                f"关键词候选：{keyword_text}",
                "内容片段：",
                item.excerpt,
            ])
        )
    brief = "\n\n".join(parts)
    return brief[:max_chars]


def build_stage_file_context(state: ContentAssessmentState, *, max_chars: int = 8000) -> dict[str, object]:
    context = state.uploaded_file_context
    if not context.enabled:
        return {"enabled": False, "files": []}
    files = []
    budget_per_file = max(500, max_chars // max(1, len(context.files)))
    for item in context.files:
        files.append({
            "file_id": item.file_id,
            "name": item.name,
            "type": item.type,
            "content_size": item.content_size,
            "keywords": item.keywords[:10],
            "excerpt": item.excerpt[:budget_per_file],
        })
    return {
        "enabled": True,
        "total_files": context.total_files,
        "total_content_size": context.total_content_size,
        "files": files,
        "warnings": context.warnings,
        "note": "上传材料仅作为用户补充上下文，正式 evidence_refs 仍必须来自输入 evidence_id。",
    }


def build_retrieval_file_hints(state: ContentAssessmentState) -> dict[str, object]:
    context = state.uploaded_file_context
    if not context.enabled:
        return {"count": 0, "names": [], "keywords": [], "short_summaries": []}
    keywords: list[str] = []
    for item in context.files:
        keywords.extend(item.keywords[:6])
    deduped_keywords = list(dict.fromkeys(keywords))[:20]
    return {
        "count": context.total_files,
        "names": [item.name for item in context.files],
        "keywords": deduped_keywords,
        "short_summaries": [
            f"{item.name}: {item.excerpt[:300]}"
            for item in context.files[:5]
        ],
        "warnings": context.warnings,
    }


class UploadedFileContextBuilder:
    def __init__(self) -> None:
        self.repo = get_parsed_file_repo()

    async def attach_to_state(
        self,
        state: ContentAssessmentState,
        *,
        user_scope: str,
        refs: list[UploadedFileRef],
    ) -> ContentAssessmentState:
        selected_refs = _dedupe_refs(refs)[:MAX_UPLOAD_FILE_COUNT]
        digests: list[UploadedFileDigest] = []
        warnings: list[str] = []
        errors: list[str] = []
        if not selected_refs:
            state.uploaded_files = []
            state.uploaded_file_context = UploadedFileContext()
            return state

        per_file_budget = max(
            1000,
            min(MAX_FILE_CONTEXT_PER_FILE_CHARS, MAX_FILE_CONTEXT_TOTAL_CHARS // max(1, len(selected_refs))),
        )
        used_ids: list[str] = []
        for ref in selected_refs:
            record = await self.repo.get(ref.file_id, user_scope, include_content=True)
            if not record:
                warnings.append(f"文件不存在或无权访问：{ref.name or ref.file_id}")
                continue
            content = str(record.get("content") or "")
            if not content:
                warnings.append(f"文件无可用解析文本：{record.get('name') or ref.file_id}")
                continue
            used_ids.append(ref.file_id)
            excerpt = _compact_whitespace(_clean_text(content))[:per_file_budget]
            digests.append(UploadedFileDigest(
                file_id=record.get("file_id", ref.file_id),
                name=record.get("name", ref.name),
                type=record.get("type", ref.type),
                size=int(record.get("size") or ref.size or 0),
                content_size=int(record.get("content_size") or len(content)),
                content_hash=str(record.get("content_hash") or ""),
                excerpt=excerpt,
                content=content,
                keywords=_keywords(content),
                status="ready",
                warning="内容已截断用于控制上下文长度" if len(content) > per_file_budget else "",
                page_count=int(record.get("page_count") or 0) or None,
                anchor_schema_version=str(record.get("anchor_schema_version") or "anchor-v1"),
                anchors=list(record.get("anchors") or []),
            ))
        await self.repo.touch_used(used_ids, user_scope)
        prompt_brief = _brief_from_digests(digests, max_chars=MAX_FILE_CONTEXT_TOTAL_CHARS)
        state.uploaded_files = selected_refs
        state.uploaded_file_context = UploadedFileContext(
            enabled=bool(digests),
            total_files=len(digests),
            total_content_size=sum(item.content_size for item in digests),
            used_context_chars=len(prompt_brief),
            files=digests,
            warnings=warnings,
            errors=errors,
            prompt_brief=prompt_brief,
        )
        state.output_json["uploaded_files"] = [
            item.model_dump(exclude={"excerpt"}) for item in digests
        ]
        if digests:
            state.output_json["uploaded_file_context_summary"] = (
                f"已接收 {len(digests)} 个上传材料，作为评估边界、主张提取与检索规划的补充上下文。"
            )
        if warnings:
            state.output_json["uploaded_file_warnings"] = warnings
        return state
