#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ARCHIVE_ROOT = Path("E:/all-context")
DEFAULT_TOOL_NAME = "claudecode"
DEFAULT_NAMING_RULE = "<问题摘要>-<时间>-<工具名>.md"


def safe_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    if not text:
        return fallback
    return text.encode("utf-8", errors="replace").decode("utf-8")


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [safe_text(item).strip() for item in value if safe_text(item).strip()]
    text = safe_text(value).strip()
    return [text] if text else []


def normalize_file_items(value: Any, empty_text: str) -> list[dict[str, str]]:
    if not value:
        return [{"path": empty_text, "location": "", "note": ""}]
    items: list[dict[str, str]] = []
    for entry in value:
        if isinstance(entry, dict):
            items.append(
                {
                    "path": safe_text(entry.get("path", "")).strip(),
                    "location": safe_text(entry.get("location", "")).strip(),
                    "note": safe_text(entry.get("note", "")).strip(),
                }
            )
        else:
            items.append({"path": safe_text(entry).strip(), "location": "", "note": ""})
    return items


def render_bullets(items: list[str], empty_text: str = "无") -> str:
    if not items:
        return f"- {empty_text}"
    return "\n".join(f"- {item}" for item in items)


def render_paragraphs(items: list[str], empty_text: str = "无") -> str:
    if not items:
        return empty_text
    return "\n\n".join(items)


def render_file_section(items: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in items:
        path = item.get("path", "")
        location = item.get("location", "")
        note = item.get("note", "")
        if path.startswith("本次无"):
            lines.append(f"- {path}")
            continue
        fragments = [path]
        if location:
            fragments.append(f"行号/区段：{location}")
        if note:
            fragments.append(f"说明：{note}")
        lines.append("- " + " ｜ ".join(fragments))
    return "\n".join(lines) if lines else "- 无"


def render_reference_section(
    code_references: list[str],
    page_references: list[str],
    design_references: list[str],
) -> str:
    lines: list[str] = []
    if code_references:
        lines.append("代码：")
        lines.extend(f"- {item}" for item in code_references)
    if page_references:
        lines.append("页面：")
        lines.extend(f"- {item}" for item in page_references)
    if design_references:
        lines.append("设计：")
        lines.extend(f"- {item}" for item in design_references)
    if not lines:
        return "- 本次未涉及可明确标注的代码 / 页面 / 设计引用"
    return "\n".join(lines)


def sanitize_filename_part(value: str, fallback: str) -> str:
    text = safe_text(value).strip()
    if not text:
        return fallback
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(".")
    return text or fallback


def normalize_tool_name(value: str | None) -> str:
    text = safe_text(value).strip().lower()
    if not text:
        return DEFAULT_TOOL_NAME
    if text in {"claude", "claude-code", "claude_code", "claudecode"}:
        return "claudecode"
    if text == "codex":
        return "codex"
    return sanitize_filename_part(text, DEFAULT_TOOL_NAME)


def resolve_filename_time(payload: dict[str, Any], fallback_time: str) -> str:
    raw_time = safe_text(
        payload.get("document_time")
        or payload.get("document_date")
        or payload.get("record_time")
        or payload.get("timestamp")
        or fallback_time
    )
    raw_time = raw_time.strip()
    if not raw_time:
        raw_time = fallback_time
    return sanitize_filename_part(raw_time.replace(":", "-"), datetime.now().strftime("%Y-%m-%d %H-%M"))


def resolve_history_file_name(summary: str, time_text: str, tool_name: str) -> str:
    safe_summary = sanitize_filename_part(summary, "context")
    safe_time = sanitize_filename_part(time_text, datetime.now().strftime("%Y-%m-%d %H-%M"))
    safe_tool = normalize_tool_name(tool_name)
    return f"{safe_summary}-{safe_time}-{safe_tool}.md"


def resolve_context_path(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def resolve_context_file(
    project_root: Path,
    context_file: str | None,
    context_dir: str | None,
    title: str | None,
    tool_name: str | None,
    time_text: str | None,
) -> Path:
    del project_root, context_file, context_dir
    file_name = resolve_history_file_name(
        title or "context",
        time_text or datetime.now().strftime("%Y-%m-%d %H-%M"),
        tool_name or DEFAULT_TOOL_NAME,
    )
    return ARCHIVE_ROOT / file_name


def normalize_entry(
    raw_entry: dict[str, Any],
    fallback_time: str,
    index: int,
    default_tool_name: str,
) -> dict[str, Any]:
    record_time = safe_text(raw_entry.get("record_time") or raw_entry.get("timestamp") or fallback_time)
    question_time = safe_text(raw_entry.get("question_time") or raw_entry.get("original_question_time") or "")
    question_index = safe_text(raw_entry.get("question_index") or raw_entry.get("sequence") or index)
    related_question = safe_text(raw_entry.get("related_question") or raw_entry.get("related_to") or "")
    question_topic = safe_text(raw_entry.get("question_topic") or raw_entry.get("topic") or "")
    question_id = safe_text(raw_entry.get("question_id") or "")
    tool_name = normalize_tool_name(raw_entry.get("tool_name") or default_tool_name)
    user_raw = safe_text(raw_entry.get("user_raw", "")).strip() or "（未提供用户原话）"
    entry_heading = safe_text(
        raw_entry.get("entry_heading")
        or raw_entry.get("heading")
        or (f"{question_index}-{question_topic}" if question_topic else "")
    ).strip()
    entry_summary = normalize_list(raw_entry.get("entry_summary") or raw_entry.get("summary"))

    answer_detail = normalize_list(
        raw_entry.get("answer_detail")
        or raw_entry.get("assistant_response")
        or raw_entry.get("answer")
    )
    processing_logic = normalize_list(
        raw_entry.get("processing_logic")
        or raw_entry.get("logic")
        or raw_entry.get("assistant_summary")
    )
    actions = normalize_list(raw_entry.get("actions") or raw_entry.get("solution"))
    result = normalize_list(raw_entry.get("result") or raw_entry.get("result_summary"))
    notes = normalize_list(raw_entry.get("notes"))
    code_references = normalize_list(raw_entry.get("code_references") or raw_entry.get("code_refs"))
    page_references = normalize_list(raw_entry.get("page_references") or raw_entry.get("page_refs"))
    design_references = normalize_list(raw_entry.get("design_references") or raw_entry.get("design_refs"))
    modified_files = normalize_file_items(raw_entry.get("modified_files"), "本次无文件变更")
    added_files = normalize_file_items(raw_entry.get("added_files"), "本次无新增文件")
    deleted_files = normalize_file_items(raw_entry.get("deleted_files"), "本次无删除文件")

    return {
        "question_index": question_index,
        "record_time": record_time,
        "question_time": question_time,
        "related_question": related_question,
        "question_topic": question_topic,
        "question_id": question_id,
        "tool_name": tool_name,
        "user_raw": user_raw,
        "entry_heading": entry_heading,
        "entry_summary": entry_summary,
        "answer_detail": answer_detail,
        "processing_logic": processing_logic,
        "actions": actions,
        "result": result,
        "notes": notes,
        "code_references": code_references,
        "page_references": page_references,
        "design_references": design_references,
        "modified_files": modified_files,
        "added_files": added_files,
        "deleted_files": deleted_files,
    }


def build_entry(entry: dict[str, Any]) -> str:
    sections: list[str] = []
    if entry["entry_heading"]:
        sections.append(f"### {entry['entry_heading']}")
    if entry["entry_summary"]:
        sections.append("摘要：\n" + render_paragraphs(entry["entry_summary"], "无"))

    header_lines = [
        "---",
        f"问题序号：{entry['question_index']}",
        f"记录时间：{entry['record_time']}",
        f"当前工具：{entry['tool_name']}",
    ]
    if entry["question_time"]:
        header_lines.append(f"原始提问时间：{entry['question_time']}")
    if entry["question_id"]:
        header_lines.append(f"问题ID：{entry['question_id']}")
    if entry["question_topic"]:
        header_lines.append(f"问题主题：{entry['question_topic']}")
    if entry["related_question"]:
        header_lines.append(f"关联问题：{entry['related_question']}")

    sections.extend([
        "\n".join(header_lines),
        "问（用户原话）：\n" + entry["user_raw"],
        "答（模型回答 / 本轮结果）：\n" + render_paragraphs(entry["answer_detail"], "本次未单独提供详细回答文本"),
        "模型处理逻辑：\n" + render_bullets(entry["processing_logic"], "本次未单独记录处理逻辑"),
        "执行动作：\n" + render_bullets(entry["actions"], "本次未单独记录执行动作"),
        "结果 / 结论：\n" + render_bullets(entry["result"], "本次未单独记录结果 / 结论"),
        "相关代码 / 页面 / 设计：\n"
        + render_reference_section(
            entry["code_references"],
            entry["page_references"],
            entry["design_references"],
        ),
        "修改文件：\n" + render_file_section(entry["modified_files"]),
        "新增文件：\n" + render_file_section(entry["added_files"]),
        "删除文件：\n" + render_file_section(entry["deleted_files"]),
        "备注 / 风险 / 待办：\n" + render_bullets(entry["notes"], "无"),
    ])
    return "\n\n".join(sections).rstrip() + "\n"


def build_entries(payload: dict[str, Any], fallback_time: str, default_tool_name: str) -> str:
    raw_entries = payload.get("entries")
    if isinstance(raw_entries, list) and raw_entries:
        entries = [
            normalize_entry(item, fallback_time, index, default_tool_name)
            for index, item in enumerate(raw_entries, start=1)
        ]
    else:
        entries = [normalize_entry(payload, fallback_time, 1, default_tool_name)]
    return "\n".join(build_entry(entry) for entry in entries)


def build_document_header(payload: dict[str, Any], context_path: Path, default_tool_name: str) -> str:
    document_title = safe_text(payload.get("document_title") or context_path.stem).strip()
    archive_root = safe_text(payload.get("archive_root") or context_path.parent)
    workspace = safe_text(payload.get("workspace") or "")
    model_name = safe_text(payload.get("model_name") or "")
    conversation_scope = safe_text(payload.get("conversation_scope") or "")
    update_policy = safe_text(payload.get("update_policy") or "同一窗口后续继续增量追加到本文件，不覆盖历史")
    naming_rule = safe_text(payload.get("naming_rule") or DEFAULT_NAMING_RULE)
    default_tool = normalize_tool_name(payload.get("tool_name") or default_tool_name)

    lines = [
        f"# {document_title}",
        "",
        "## 文档元信息",
        f"- 文件名：`{context_path.name}`",
        f"- 归档路径：`{context_path}`",
        f"- 归档目录：`{archive_root}`",
        f"- 当前工具：`{default_tool}`",
        f"- 命名规则：`{naming_rule}`",
        f"- 更新策略：{update_policy}",
    ]
    if model_name:
        lines.append(f"- 当前模型：`{model_name}`")
    if workspace:
        lines.append(f"- 关联工作区：`{workspace}`")
    if conversation_scope:
        lines.append(f"- 会话范围：{conversation_scope}")
    lines.append("")
    return "\n".join(lines)


def load_payload(input_json: str | None) -> dict[str, Any]:
    if input_json:
        return json.loads(Path(input_json).read_text(encoding="utf-8-sig"))

    raw_bytes = sys.stdin.buffer.read()
    return json.loads(raw_bytes.decode("utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append detailed, question-by-question context entries to a Markdown context file without overwriting history."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--context-file")
    parser.add_argument("--context-dir")
    parser.add_argument("--title")
    parser.add_argument("--tool-name")
    parser.add_argument("--date")
    parser.add_argument("--input-json", help="Read payload from a UTF-8 JSON file instead of stdin.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    raw = load_payload(args.input_json)

    fallback_time = safe_text(raw.get("record_time") or raw.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M"))
    default_tool_name = normalize_tool_name(raw.get("tool_name") or args.tool_name or DEFAULT_TOOL_NAME)
    file_time = resolve_filename_time(raw, fallback_time)
    file_title = safe_text(raw.get("document_title") or args.title or raw.get("question_topic") or "context")
    context_path = resolve_context_file(
        project_root=project_root,
        context_file=args.context_file or raw.get("context_file"),
        context_dir=args.context_dir or raw.get("context_dir"),
        title=file_title,
        tool_name=default_tool_name,
        time_text=file_time,
    )
    context_path.parent.mkdir(parents=True, exist_ok=True)

    new_content = build_entries(raw, fallback_time, default_tool_name)
    existing = context_path.read_text(encoding="utf-8") if context_path.exists() else ""
    document_header = ""
    if not existing.strip():
        document_header = build_document_header(raw, context_path, default_tool_name).rstrip() + "\n\n"

    separator = ""
    if existing and not existing.endswith("\n"):
        separator = "\n\n"
    elif existing:
        separator = "\n"

    context_path.write_text(existing + separator + document_header + new_content, encoding="utf-8")
    print(context_path)


if __name__ == "__main__":
    main()
