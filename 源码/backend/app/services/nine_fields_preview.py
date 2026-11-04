"""Extract an editable draft without running the assessment graph."""

import asyncio
import json
import re

from app.agents.content_assessment.prompts import MATERIAL_SYSTEM_PROMPT
from app.agents.content_assessment.schemas import ResearchNineFields, UploadedFileRef
from app.core.config import MAX_UPLOAD_FILE_COUNT
from app.repositories.content_assessment_parsed_file import get_parsed_file_repo
from app.services.deepseek_client import DeepSeekClient
from app.agents.content_assessment.input_rules import (
    ARRAY_FIELDS, FIELD_LABELS, INPUT_RULE_VERSION, explicit_artifact_type,
    explicit_fields, infer_artifact_type, meaningful,
)


def _extract_explicit_fields(text: str) -> ResearchNineFields:
    return ResearchNineFields.model_validate(explicit_fields(text))


def _merge_fields(primary: ResearchNineFields, secondary: ResearchNineFields) -> ResearchNineFields:
    """Fill empty fields without discarding previously recognized content."""
    values = secondary.model_dump()
    for key, value in primary.model_dump().items():
        populated = any(meaningful(v) for v in value) if isinstance(value, list) else meaningful(value)
        if populated:
            values[key] = value
    return ResearchNineFields.model_validate(values)


def _model_fields(payload: dict, material: str, *, only: list[str] | None = None,
                  require_quotes: bool = False) -> ResearchNineFields:
    values = {}
    raw = payload.get("nine_fields")
    if not isinstance(raw, dict):
        raise ValueError("Field recognition returned no usable result")
    sources = payload.get("field_sources")
    for key in only or ResearchNineFields.model_fields:
        value = raw.get(key)
        if key in ARRAY_FIELDS:
            value = [value] if isinstance(value, str) else value
            value = [v.strip() for v in value if isinstance(v, str) and meaningful(v)] if isinstance(value, list) else []
        else:
            value = value.strip() if isinstance(value, str) and meaningful(value) else ""
        if not value:
            continue
        # New responses and the second check identify actual source text.
        # The older extraction-only response contract remains compatible.
        if require_quotes or isinstance(sources, dict):
            quotes = sources.get(key, []) if isinstance(sources, dict) else []
            quotes = [quotes] if isinstance(quotes, str) else quotes
            if not isinstance(quotes, list) or not any(
                isinstance(q, str) and meaningful(q) and q.strip() in material for q in quotes
            ):
                continue
        values[key] = value
    return ResearchNineFields.model_validate(values)


def _material_excerpt(sources: list[str], missing: list[str] | None = None) -> str:
    """Share context across files, preserving relevant middle and end clauses."""
    budget = 22000 // max(len(sources), 1)
    excerpts = []
    for source in sources:
        if len(source) <= budget:
            excerpts.append(source)
            continue
        chunks = [source[:budget // 3], source[-budget // 3:]]
        terms = {
            "research_direction": "研究|领域|对象", "research_goal": "目标|目的|旨在",
            "research_content": "研究|任务|内容", "key_technologies": "方法|技术|采用|使用|路线",
            "evaluation_indicators": "指标|验收|规范|标准|复现|验证|变异|至少|不低于",
            "application_scenarios": "应用|适用|用于|场景",
        }
        pattern = "|".join(terms[k] for k in (missing or list(terms)) if k in terms)
        remaining = budget - sum(map(len, chunks))
        for match in re.finditer(pattern, source):
            if remaining <= 0:
                break
            start, end = max(0, match.start() - 160), min(len(source), match.end() + 480)
            snippet = source[start:end][:remaining]
            if any(snippet in chunk for chunk in chunks):
                continue
            chunks.append(snippet)
            remaining -= len(snippet)
        excerpts.append("\n[…]\n".join(chunks))
    return "\n\n".join(excerpts)


def _recognition_prompt(material: str, kind: str, fields: ResearchNineFields,
                        missing: list[str] | None = None) -> str:
    labels = {value: key for key, value in FIELD_LABELS.items()}
    keys = _review_keys(missing) if missing else list(ResearchNineFields.model_fields)
    contract = {"artifact_type": "paper/project/guideline/unknown",
                "nine_fields": {key: ["实际内容"] if key in ARRAY_FIELDS else "实际内容" for key in keys},
                "field_sources": {key: ["逐字引用材料中对应的原文"] for key in keys}}
    review = "这是补充提示前的最后一次原文复核，只检查：" + "、".join(labels.get(k, k) for k in missing) + "。\n" if missing else ""
    return (
        "识别主评估对象的类型和已提供的信息，不检索、不写报告、不评价信息是否足够详细。不要求固定格式。\n"
        "用户明确指定的论文、项目或指南优先，参考附件不改变主对象。"
        "研究方向可以根据明确的研究对象和内容归纳；关键方法包括实验、算法、设计或技术路线。"
        "项目的研究目标不能替代技术信息。指南的考核指标接受定量指标及可验收的定性要求，"
        "包括形成操作规范、记录模板、测量口径和复现性验证，不要求必须给数值阈值。"
        "信息可以散落在不同段落；不要因为没有标题、数值、单位或完整实验设计而留空。"
        "只有口号、空标题、待补充等占位文字不算实际信息。"
        "只提取实际内容，不补造方法、指标、目标或实验结果。每个填写项附逐字原文；确实未提到才留空。\n"
        f"已确定类型：{kind}；已识别信息（保留，不重写）：{json.dumps(fields.model_dump(), ensure_ascii=False)}\n"
        f"{review}返回契约：{json.dumps(contract, ensure_ascii=False)}\n<assessment_material>\n{material}\n</assessment_material>"
    )


def _review_keys(missing: list[str]) -> list[str]:
    # A route is an equally valid source of technical information for a paper
    # or project, even when the missing-field key names key_technologies.
    return list(dict.fromkeys(missing + (["technical_route"] if "key_technologies" in missing else [])))


async def preview_nine_fields(user_input: str, refs: list[UploadedFileRef], user_scope: str,
                              artifact_type: str = "auto", *, material_texts: list[str] | None = None) -> dict:
    if len(refs) > MAX_UPLOAD_FILE_COUNT:
        raise ValueError(f"最多上传 {MAX_UPLOAD_FILE_COUNT} 个文件")
    sources = [user_input.strip()] if user_input.strip() else []
    sources.extend(text.strip() for text in (material_texts or []) if text.strip())
    warnings: list[str] = []
    for file_id in dict.fromkeys(ref.file_id for ref in refs):
        record = await get_parsed_file_repo().get(file_id, user_scope, include_content=True)
        if not record:
            raise PermissionError("文件不存在或无权访问，请重新上传")
        content = str(record.get("content") or "").strip()
        if content:
            sources.append(f"【{record.get('name', file_id)}】\n{content}")
        else:
            warnings.append(f"{record.get('name', file_id)} 没有可用解析文本")
    fields = ResearchNineFields()
    extraction_failed = bool(warnings)
    client = None
    requested = artifact_type
    if requested == "auto":
        user_type = explicit_artifact_type(user_input)
        if user_type != "unknown":
            requested = user_type
    resolved_type = infer_artifact_type("\n".join(sources), [ref.name for ref in refs], requested)
    try:
        if sources:
            # Read complete sources before limiting model context, including
            # indicators near the end of a long attachment.
            explicit = ResearchNineFields()
            for source in sources:
                # Field boundaries cannot cross files and consume the next
                # source's title or the user's evaluation instructions.
                explicit = _merge_fields(_extract_explicit_fields(source), explicit)
            # Direct input, including a user's latest supplement, takes precedence
            # over identically labelled fields in attached reference materials.
            typed = _extract_explicit_fields(user_input)
            fields = _merge_fields(typed, explicit)
            if resolved_type == "unknown" or fields.missing_for_type(resolved_type):
                client = DeepSeekClient()
                material = _material_excerpt(sources)
                payload = await asyncio.wait_for(client.generate_json(
                    system_prompt=MATERIAL_SYSTEM_PROMPT,
                    user_prompt=_recognition_prompt(material, resolved_type, fields),
                    max_tokens=3000, timeout_seconds=22, max_retries=0,
                ), timeout=25)
                extracted = _model_fields(payload, material)
                if resolved_type == "unknown" and payload.get("artifact_type") in {"paper", "project", "guideline"}:
                    resolved_type = payload["artifact_type"]
                fields = _merge_fields(fields, extracted)
                missing = fields.missing_for_type(resolved_type) if resolved_type != "unknown" else []
                if missing:
                    # Only one additional check, restricted to the remaining
                    # fields and validated against verbatim source quotations.
                    material = _material_excerpt(sources, missing)
                    review = await asyncio.wait_for(client.generate_json(
                        system_prompt=MATERIAL_SYSTEM_PROMPT,
                        user_prompt=_recognition_prompt(material, resolved_type, fields, missing),
                        max_tokens=1800, timeout_seconds=15, max_retries=0,
                    ), timeout=18)
                    fields = _merge_fields(fields, _model_fields(review, material, only=_review_keys(missing), require_quotes=True))
    except Exception:
        extraction_failed = True
        warnings.append("材料识别暂未完成，输入内容已保留，请重试")
    finally:
        if client is not None:
            await client.aclose()
    missing = fields.missing_for_type(resolved_type) if resolved_type != "unknown" else ["artifact_type"]
    return {"nine_fields": fields.model_dump(), "missing_fields": missing, "warnings": warnings,
            "resolved_artifact_type": resolved_type, "validation_version": INPUT_RULE_VERSION,
            "extraction_failed": extraction_failed}
