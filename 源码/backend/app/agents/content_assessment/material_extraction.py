"""Source-grounded extraction from canonical upload text and parser anchors."""
from __future__ import annotations

import re
from collections.abc import Iterator

from .schemas import Claim, ContentAssessmentState, Indicator, QueryPlan, UploadedFileDigest


INSTRUCTION = re.compile(r"^(?:请|要求[：:]?$|语言偏好|文献年份|报告模式|先列出|不要把|不得|禁止|每条主张|每一组查询)")


def source_location_label(locator: dict | str | None) -> str:
    if not isinstance(locator, dict):
        return str(locator or "原文位置未提供")
    labels = {"page": "页", "line": "行", "paragraph": "段", "row": "表格行", "sheet": "工作表", "section": "章节"}
    parts = [f"{labels[key]} {value}" for key, value in locator.items() if key in labels and str(value).strip()]
    return "，".join(parts) or "原文位置未提供"


def material_sources(state: ContentAssessmentState) -> list[UploadedFileDigest]:
    if state.uploaded_files or state.uploaded_file_context.files:
        sources = [f for f in state.uploaded_file_context.files if f.content.strip()]
        if state.raw_user_input.strip():
            sources.append(UploadedFileDigest(file_id="user_material", name="用户提供的评估说明", content=state.raw_user_input))
    # A directly supplied technical description is also a source, but workflow
    # instructions never become claims just because no file was supplied.
    else:
        sources = [UploadedFileDigest(file_id="user_material", name="用户提供的技术描述", content=state.raw_user_input)]
    for material in state.output_json.get("supplemental_materials", []):
        if material.get("content", "").strip():
            sources.append(UploadedFileDigest.model_validate(material))
    if state.nine_fields_confirmed:
        from .input_rules import FIELD_LABELS, meaningful
        labels = {value: key for key, value in FIELD_LABELS.items()}
        lines = []
        for key, value in state.nine_fields.model_dump().items():
            text = "；".join(value) if isinstance(value, list) else str(value or "")
            if meaningful(text) and not any(text in source.content for source in sources):
                lines.append(f"{labels.get(key, key)}：{text}")
        if lines:
            sources.append(UploadedFileDigest(file_id="user_confirmed_fields", name="用户确认补充信息", content="\n".join(lines)))
    return sources


def locate(source: UploadedFileDigest, quote: str, start: int | None = None) -> dict | None:
    if not quote or INSTRUCTION.search(quote.strip()):
        return None
    start = source.content.find(quote) if start is None else start
    if start < 0 or source.content[start:start + len(quote)] != quote:
        return None
    anchors = [a for a in source.anchors if isinstance(a.get("char_start"), int)
               and a["char_start"] <= start and a.get("char_end", 0) >= start + len(quote)]
    anchor = min(anchors, key=lambda a: a["char_end"] - a["char_start"]) if anchors else {}
    locator = dict(anchor.get("locator") or {})
    if not locator:
        locator = {"kind": "text", "line": source.content[:start].count("\n") + 1}
    return dict(source_document_id=source.file_id, source_span=quote,
                source_char_start=start, source_char_end=start + len(quote), source_locator=locator,
                source_page_ref=str(locator.get("page", "")),
                source_paragraph_ref=str(locator.get("paragraph", locator.get("row", locator.get("line", "")))))


def source_chunks(state: ContentAssessmentState, limit: int = 6000) -> Iterator[str]:
    for source in material_sources(state):
        start = 0
        while start < len(source.content):
            end = min(start + limit, len(source.content))
            if end < len(source.content):
                boundary = source.content.rfind("\n", start + limit // 2, end)
                if boundary > start:
                    end = boundary + 1
            yield f"文件 ID: {source.file_id}\n文件名: {source.name}\n原文字符起点: {start}\n{source.content[start:end]}"
            start = end


def rows(source: UploadedFileDigest) -> Iterator[tuple[dict[str, str], int]]:
    """Read the parser's pipe-delimited table rows using their header schema."""
    headers: list[str] = []
    offset = 0
    header_names = {"主张原文", "ClaimText", "指标", "指标名称", "定义", "字段", "含义", "QueryText", "模块", "组别", "内容", "质量门"}
    for line in source.content.splitlines(keepends=True):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) > 1 and header_names.intersection(cells):
            headers = cells
        elif len(cells) == len(headers) and headers:
            yield dict(zip(headers, cells)), offset
        elif len(cells) == 1:
            headers = []
        offset += len(line)


def explicit_claims(state: ContentAssessmentState) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[str] = set()
    for source in material_sources(state):
        for row, offset in rows(source):
            quote = row.get("主张原文") or row.get("ClaimText") or ""
            if not quote or quote in seen:
                continue
            anchor = locate(source, quote, source.content.find(quote, offset))
            if not anchor:
                continue
            seen.add(quote)
            target = row.get("项目目标值", "")
            claims.append(Claim(claim_id=f"C-{len(claims)+1:03d}", text=quote, **anchor,
                assertion_type="target" if target or re.search(r"拟|目标|计划", quote) else "unknown",
                claim_fields={"object": row.get("研究对象", ""), "method": row.get("研究方法", ""),
                              "baseline": row.get("比较基线", ""), "time": row.get("观察周期", ""),
                              "metric": row.get("核心指标", ""), "validation": row.get("对照要求", ""),
                              "data": f"项目目标，尚非已实现数据：{target}" if target else row.get("材料结果", "")}))
    return claims


def enrich_indicators(state: ContentAssessmentState) -> list[Indicator]:
    result: list[Indicator] = []
    index: dict[str, Indicator] = {}
    claims_by_quote = {c.source_span: c for c in state.claims}

    def add(name: str, source: UploadedFileDigest, quote: str, **fields) -> Indicator:
        name = name.strip()
        item = index.get(name)
        if item is None:
            anchor = locate(source, quote) or {}
            item = Indicator(indicator_id=f"I-{len(result)+1:03d}", name=name,
                             **{k: v for k, v in anchor.items() if k in Indicator.model_fields})
            result.append(item)
            index[name] = item
        for key, value in fields.items():
            if key == "source_claim_id" and value and value not in item.source_claim_ids:
                item.source_claim_ids.append(value)
            if value and not getattr(item, key, ""):
                setattr(item, key, value)
        return item

    for source in material_sources(state):
        for row, offset in rows(source):
            quote = source.content[offset:].splitlines()[0]
            if row.get("主张原文"):
                claim = claims_by_quote.get(row["主张原文"])
                for name in re.split(r"[、；;]", row.get("核心指标", "")):
                    if name:
                        item = add(name, source, quote, source_claim_id=claim.claim_id if claim else "",
                                   acceptance_condition=row.get("项目目标值", ""), baseline=row.get("对照要求", ""))
                        item.status = "target" if row.get("项目目标值") else "unknown"
                # Some acceptance endpoints are explicitly named only in the
                # target cell (e.g. TEER), and must not disappear.
                for name in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", row.get("项目目标值", "")):
                    if name not in {"CV"} and name not in index:
                        add(name, source, quote, source_claim_id=claim.claim_id if claim else "",
                            acceptance_condition=row["项目目标值"], status="target")
            elif (row.get("定义") and row.get("指标")) or (row.get("含义") and row.get("单位")):
                names = row.get("指标") or row.get("含义", "")
                # Reuse explicit field abbreviations from the source schema.
                abbreviation = re.sub(r"_(?:pct|ng_mL|U_L|pg_mL)$", "", row.get("字段", ""))
                if abbreviation in index:
                    names = abbreviation
                elif names.endswith("浓度") and names[:-2] in index:
                    names = names[:-2]
                elif names.endswith("系数") and names[:-2] in index:
                    names = names[:-2]
                aliases = {"相对目标流量漂移": "流量漂移", "氧记录完整率": "氧张力记录完整率"}
                names = aliases.get(names, names)
                if row.get("单位") in {"文本", ""}:
                    continue
                for name in re.split(r"[、；;]", names):
                    add(name, source, quote, definition=row.get("定义") or row.get("含义", ""),
                        unit=row.get("单位", ""), statistics_method=row.get("统计方法", ""),
                        test_method=row.get("来源定位", ""), acceptance_condition=row.get("质量门", ""))
    # Keep a target only on the endpoint actually named in that clause. ALT's
    # target must not silently become an AST/LDH acceptance threshold.
    target_aliases = {"细胞活率": ["活率"], "氧张力记录完整率": ["氧记录完整率", "氧张力记录完整率"],
        "有效运行时间": ["有效运行"], "流量漂移": ["流量漂移"],
        "芯片批间 CV": ["CV"], "失效率": ["无效芯片率"],
        "与参考分类的一致性": ["参考分类一致"]}
    for item in result:
        if item.source_claim_ids:
            conditions = []
            for claim in state.claims:
                if claim.claim_id not in item.source_claim_ids:
                    continue
                target = re.sub(r"^项目目标，尚非已实现数据：", "", claim.claim_fields.get("data", ""))
                clauses = re.split(r"[；;，,]", target)
                for clause in clauses:
                    if any(term.lower() in clause.lower() for term in target_aliases.get(item.name, [item.name])):
                        conditions.append(f"{claim.claim_id} 目标：{clause.strip()}")
            item.acceptance_condition = "；".join(dict.fromkeys(conditions))
            if not item.unit and conditions:
                units = re.findall(r"\d\s*(%|h|小时|天|倍)", "；".join(conditions))
                if units:
                    item.unit = units[-1]
        if not item.unit and item.name == "有效运行时间":
            match = re.search(r"有效运行[≥≤]?\s*\d+\s*(h|小时)", item.source_span)
            if match:
                item.unit = match[1]
    for old in state.indicators:
        if old.name not in index and old.source_document_id and old.source_span:
            result.append(old.model_copy(update={"indicator_id": f"I-{len(result)+1:03d}"}))
    # A numeric assertion supplied directly is retained with its exact source.
    for claim in state.claims:
        if claim.source_document_id != "user_material":
            continue
        source = next((f for f in material_sources(state) if f.file_id == claim.source_document_id), None)
        for match in re.finditer(r"([^，。；,;]{2,30}?)([≥≤<>]?\s*\d+(?:\.\d+)?)\s*(%|小时|天|mM|nm|mm|mg|倍)", claim.source_span):
            if state.scenario_id and any(
                item.unit == match[3]
                and re.search(r"\d+(?:\.\d+)?", item.value or "")
                and re.search(r"\d+(?:\.\d+)?", item.value)[0] == re.search(r"\d+(?:\.\d+)?", match[2])[0]
                and any(item.name[i:i + 2] in match[1] for i in range(max(0, len(item.name) - 1)))
                for item in result
            ):
                continue
            if source:
                add(match[1], source, match[0], source_claim_id=claim.claim_id, value=match[2], unit=match[3],
                    acceptance_condition=match[0], status=claim.assertion_type)
    return result


def relevant(claim: Claim, evidence) -> bool:
    """Candidate relevance is separate from proof and field comparability."""
    if (f"claim:{claim.claim_id}" in getattr(evidence, "retrieval_intent_ids", [])
            and evidence.fulltext_available and evidence.anchor_verified):
        return True
    if evidence.query_group == "首页推荐案例核验文献" and claim.claim_id in evidence.claim_ids:
        return True
    text = re.sub(r"[‐‑‒–—]", "-", f"{evidence.title} {evidence.abstract} {evidence.excerpt}".lower())
    common = [k for k, value in claim.claim_fields.items() if value and
              evidence.comparison_fields.get(k) == value]
    if "object" in common and "method" in common:
        return True
    terms = [t.lower().strip() for t in claim.searchable_terms if len(t.strip()) >= 3]
    generic = {"研究", "技术", "方法", "评估", "分析", "项目", "创新", "系统", "study", "method", "analysis"}
    terms = [t for t in terms if t not in generic and not INSTRUCTION.search(t)]
    # These are search aliases, never a field-comparability or support rule.
    if re.search(r"器官芯片|肝脏|心肌|APAP|DOX|芯片|化合物", claim.text):
        terms += ["organ-on-chip", "organs-on-chips", "organ-on-chips", "organ chip", "organ-on-a-chip", "microphysiological"]
    aliases = {r"对乙酰氨基酚|肝损伤": ["acetaminophen", "hepatotoxic", "liver injury"],
               r"多柔比星|心脏损伤": ["doxorubicin", "cardiotoxic"],
               r"操作者|重复性|CV": ["inter-operator", "reproducibility", "standardization"],
               r"风险排序|参考分类": ["compound prioritization", "risk assessment", "reference compounds"]}
    for pattern, values in aliases.items():
        if re.search(pattern, claim.text):
            terms += values
    if any(t in text for t in terms):
        return True
    # Chinese parser keywords can be entire phrases; compare distinctive
    # substrings only when at least two occur, excluding generic task language.
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", claim.text)
    grams = {word[i:i+3] for word in zh for i in range(len(word)-2)}
    return sum(g in text for g in grams) >= 2


def material_query_plans(state: ContentAssessmentState) -> list[QueryPlan]:
    """Use explicit material queries only when their claim mapping is valid."""
    roles = {"系统或技术路线先例": "system_prior_art", "关键机制与实现方法": "mechanism_method",
             "指标基线与验证方法": "metric_baseline", "监测应用与实验方案": "application_validation",
             "市场、产业与竞争路线": "industry_market_competitive",
             "市场产业与竞争路线": "industry_market_competitive",
             "产业标准与监管资料": "industry_standard_regulatory"}
    claim_map = {}
    for source in material_sources(state):
        for row, _ in rows(source):
            quote = row.get("主张原文") or row.get("ClaimText")
            claim = next((c for c in state.claims if c.source_span == quote), None)
            if claim:
                for label in ("主张", "主张编号", "ClaimID", "编号"):
                    if row.get(label):
                        claim_map[row[label]] = claim.claim_id
    plans = []
    for source in material_sources(state):
        for row, _ in rows(source):
            query = row.get("QueryText", "")
            cids = [claim_map[c.strip()] for c in re.split(r"[;,；]", row.get("ClaimIDs", "")) if c.strip() in claim_map]
            role = roles.get(row.get("QueryRole", ""))
            if query and role and cids and not INSTRUCTION.search(query):
                plans.append(QueryPlan(claim_id=cids[0], claim_ids=cids, query=query, query_group=role,
                    query_role=role, year_range=state.output_json.get("year_range", []),
                    rationale=row.get("Rationale", ""), source_status="pending",
                    exclusion_terms=[s.strip() for s in row.get("ExclusionTerms", "").split(";") if s.strip()]))
    return plans


def material_context(state: ContentAssessmentState) -> None:
    """Complete shared scope and methods from explicit source sections."""
    route = []
    modules = []
    shared_fields = {}
    for source in material_sources(state):
        in_route = False
        for line in source.content.splitlines():
            if re.match(r"^[一二三四五六七八九十]+\s", line):
                in_route = bool(re.search(r"技术路线|系统设计", line))
                continue
            if in_route and "|" not in line and len(line) >= 25:
                route.append(line)
            if line.startswith("应用对象 |") and not state.nine_fields_confirmed:
                state.nine_fields.application_scenarios = [line.split("|", 1)[1].strip()]
            if line.startswith("执行周期 |") and not state.nine_fields_confirmed:
                state.nine_fields.timeline = line.split("|", 1)[1].strip()
        for row, _ in rows(source):
            if row.get("模块"):
                modules.append(row["模块"])
            if row.get("本项目填写要求"):
                names = {"对象": "object", "方法": "method", "基线": "baseline", "时间": "time", "验证条件": "validation"}
                if row.get("字段") in names:
                    shared_fields[names[row["字段"]]] = row["本项目填写要求"]
    if route and not state.nine_fields_confirmed:
        state.nine_fields.technical_route = "\n".join(dict.fromkeys(route))
    if modules and not state.nine_fields_confirmed:
        state.nine_fields.key_technologies = list(dict.fromkeys(modules))
    if state.claims and not state.nine_fields_confirmed:
        state.nine_fields.research_direction = state.assessment_profile.assessment_object or state.claims[0].text
    for claim in state.claims:
        for key, value in shared_fields.items():
            if not claim.claim_fields.get(key):
                claim.claim_fields[key] = "项目共用条件（具体主张适用性需核对）：" + value
    retain_material_observations(state)


def retain_material_observations(state: ContentAssessmentState) -> None:
    """Keep supplementary numbers and requirements even with a table source.

    Literal source coverage is not proof of performance or full compliance.
    """
    from .schemas import GuidelineRequirement
    sources = material_sources(state)
    observations = []
    guidelines = [s for s in sources if '指南' in s.name or re.search(r'(?m)^指南名称[：:]', s.content)]
    primary = [s for s in sources if s not in guidelines]
    primary_text = '\n'.join(s.content for s in primary)
    for source in sources:
        for line in source.content.splitlines():
            if '|' in line or not re.search(r'\d+(?:\.\d+)?\s*[%℃]|\d+\s*摄氏度', line):
                continue
            anchor = locate(source, line)
            if not anchor:
                continue
            target = bool(re.search(r'目标|计划|拟|要求', line))
            recorded = bool(re.search(r'记录|结果|报告|实测', line))
            observations.append({'text': line, **anchor,
                'kind': 'mixed' if target and recorded else 'target' if target else 'reported' if recorded else 'unknown',
                'comparison_note': '材料中的目标与记录分别核验；对象、指标、单位、基线或条件不同不可直接比较。'})
    state.output_json['material_observations'] = observations[:40]
    if state.report_preferences.resolved_artifact_type != 'project':
        return
    requirements = []
    for source in guidelines:
        for line in source.content.splitlines():
            if not re.search(r'要求|应当|应在|须|指标|验收', line) or re.match(r'指南名称[：:]', line):
                continue
            anchor = locate(source, line)
            if not anchor:
                continue
            terms = re.findall(r'负?\d+摄氏度|[+-]?\d+\s*℃|SOC|召回率|准确率|独立重复|对照|电芯型号', line)
            hits = [term for term in terms if term in primary_text]
            status = 'matched' if line in primary_text else 'partial' if hits else 'unknown'
            response = next((part for s in primary for part in s.content.splitlines() if any(t in part for t in hits)), '')
            requirements.append(GuidelineRequirement(requirement_id=f'G-{len(requirements)+1:03d}', text=line,
                source_document_id=source.file_id, source_span=line, source_locator=anchor['source_locator'],
                match_status=status, response_excerpt=response,
                comparison_note='文字覆盖仅说明材料应答，不证明目标已实现。' if status=='matched' else
                    f"已覆盖：{'、'.join(hits) or '未找到逐字应答'}；仍需核验：{'、'.join(t for t in terms if t not in hits) or '完整要求及独立验收记录'}。"))
    state.guideline_requirements = requirements
