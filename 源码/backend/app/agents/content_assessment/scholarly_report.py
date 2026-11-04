"""Result-oriented composition for new reports; original evidence remains in audit_data.

Reader prose is generated before freezing. Existing snapshots never pass through
this composer. Abstracts support qualitative comparisons, not invented results.
"""
from __future__ import annotations

import json
import re
import hashlib
from difflib import SequenceMatcher
from app.agents.report_validation import review_response, response_diagnostic
from .schemas import ReportBlock, ReportSectionSpec

from .research_outline import TEMPLATE, RENDER, evaluation_dimensions
LEGACY_CHAPTERS = [
    ("executive", "一、评估摘要与总体结论"),
    ("object", "二、评估对象和要点"),
    ("progress", "三、相关研究进展"),
    ("comparison", "四、逐项比对与创新性分析"),
    ("recommendations", "五、趋势和综合建议"),
    ("references", "六、参考文献及必要说明"),
]
WRITING_RULES = """你是一名撰写科研评议报告的研究者。只依据给定材料和文献可读内容分析。
每段围绕具体对象、方法或指标作论述，明确已有研究做了什么、本研究与其有何联系和差异。
直接从本段讨论的研究对象、方法或结果切入，避免连续使用“本指南”“本材料”“本项目”作为段落开头；
采用研究论文评议的客观书面语，避免行政通知腔、口语化表达、对用户说话和模型自述。
创新性、前沿性、先进性须结合当前要点及对应来源论证，不以通用定义或文献年份代替分析。
以准确引用和判断范围体现严谨性。不要输出字段检查、后台编号、
服务配置、质量门、缺少清单、结论强度、部分支持标签、分数、评分雷达、领先性套话及口语化过渡。不要把文献摘要当全文。
没有具体依据的维度不展开。拟开展/目标不能写为已完成或实测，文献结果不能写成本研究结果。
不同对象、单位、基线、条件的数值不能直接比较。分析说明已支持、有条件成立和暂不能判断的范围。
不编造数值、方法、实验、引文或来源定位。不得把未检出当作原创证明。
文献事实只能来自给定content，不能凭已知论文名称补写训练集、架构细节、指标、作者或实验结论。
作者列表或出版信息不构成方法依据。未在content明确陈述的细节不写成文献事实；建议另行提出。
材料未提及某方法不能推断为明确排除该方法。只比较材料实际提出的方案。
材料中的拟研究、拟开展、计划和目标始终使用将来或建议表述，不能概括为已经完成比较或验证。
不要以未考虑、未借鉴、未计划或缺乏某架构逐项批评材料；评价已有方案及其依据。
只描述真正影响判断的证据缺口、适用条件和待验证问题，不编写咨询方案或行动优先级。
建议中的因果解释也必须核对来源；不能仅凭性能变化把原因归于某模型组件。
来源摘要未提及不能写为原研究未开展、尚未应用、现有研究没有或领域缺乏。判断研究机会应说明拟比较的变量与已知方法的联系。
不要重复摘要、逐字复述材料或反复说同一适用范围。不要使用'综上所述'、'值得注意的是'、
'具有重要意义'、'赋能'、'填补空白'等空泛表述。输出结构化 JSON。"""
INTERNAL = re.compile(r"结论强度|部分支持|(?:缺少|缺失|未配置|尚未配置).{0,60}(?:字段|锚点|服务)|字段覆盖\s*\d+\s*/\s*\d+|关键词交集\s*\d+\s*项?|质量门|覆盖率将在|字段矩阵|降级结果|字符锚点|内部评分|综合评分|证据等级")
ABSOLUTE = re.compile(r"全球首创|国际领先|首次实现|完全证明|填补.{0,12}空白")
UNSUPPORTED_ABSENCE = re.compile(
    # Keep the field-wide subject in the same clause, outside titles/quotes.
    r"(?:现有|已有|在先|相关)(?:研究|文献)[^。；，,：:《》“”]{0,70}(?:未见|未曾|尚未|没有|未采用|未开展|未报道|未涉及|未充分|缺乏)"
    r"|(?:尚属|仍属|研究)空白|未见[^。；]{0,35}(?:报道|研究|实施)"
    # Keep the asserted action in the same clause. In particular, the
    # material's execution status must not consume a later validation caveat:
    # “尚未实际执行，因此其实际效果有待验证” is not a field-gap claim.
    r"|尚未[^。；，,]{0,70}(?:研究|验证|开展|建立|应用|报道|探讨)"
    r"|(?:本研究|本项目|论文|项目|指南|材料)[^。；]{0,30}(?:未计划|未采用|未引入|未考虑|未借鉴)"
    r"|(?:但|却|而)未[^。；]{0,90}(?:开展|进行|采用|研究|验证|应用)")


def reader_prose(value: str) -> str:
    """Filter generated narration only, never quotations or uploaded materials."""
    parts = re.split(r"(?<=[。！？；])|\n+", str(value or ""))
    return "".join(part for part in parts if not INTERNAL.search(part) and not ABSOLUTE.search(part)).strip()


def point_title(claim) -> str:
    saved = getattr(claim, "display_title", "")
    if saved:
        return saved
    fields = claim.claim_fields or {}
    named = re.match(r'^(?:本)?(?:论文|项目|指南)?(?:拟研究|拟开展|拟设置)([^，,。；;\n]{4,36})(?:[，,。；;]|$)', claim.text)
    if named:
        return named.group(1).strip()
    value = str(fields.get("method") or claim.text)
    value = re.sub(r"^(?:本研究|本项目|项目|论文|拟|计划|将|采用|提出|建立|开展|研究|通过)+", "", value).strip(" ：:，,。")
    value = re.split(r"[。；;\n]|，(?=拟|并|以|比较|验证)", value)[0]
    if len(value) > 30:
        name = re.match(r"[A-Za-z][A-Za-z0-9-]*", value)
        if name:
            value = name.group() + "方法与应用研究"
        else:
            value = re.split(r"[，,；;。]", claim.text)[0]
            value = re.sub(r"^(?:论文|项目|指南)?(?:拟研究|拟开展|拟设置)", "", value)
    return value or "研究方法与验证设计"


def tokens(text):
    text = str(text).lower()
    # Equivalent English spellings must survive cross-language retrieval:
    # organ-on-a-chip / organ-on-chip, blood-brain / blood–brain.
    text = re.sub(r"[–—]", "-", text)
    text = re.sub(r"-on-(?:a|an|the)-", "-on-", text)
    text = text.replace("organs-on-chips", "organ-on-chip").replace("organs-on-chip", "organ-on-chip")
    latin = set(re.findall(r"[a-z][a-z0-9-]{2,}", text)) - {"the", "and", "with", "for", "from", "study", "research", "method", "using", "information", "document", "documents", "retrieval", "scientific", "based", "learning", "model", "models", "data", "analysis", "performance", "evaluation", "results", "framework", "approach", "system", "systems"}
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    return latin | {word[i:i+2] for word in chinese for i in range(len(word)-1)}


def selected_sources(state, claim, limit=6):
    """Require readable content and concrete topical overlap, not a query tag.

    Contradictory related findings are deliberately retained. A weak semantic
    link cannot override a paper about an unrelated research object.
    """
    query = " ".join([claim.text, *claim.searchable_terms])
    target = tokens(query)
    # Retrieval already translates the material into claim-scoped queries.
    # Use those terms as vocabulary, but still require concrete matches in
    # the source itself. Merely assigning a query/claim tag is insufficient.
    scoped_queries = [plan.query for plan in state.query_plans
        if claim.claim_id in plan.claim_ids or plan.claim_id == claim.claim_id]
    translated = set().union(*(tokens(query) for query in scoped_queries))
    # Leading translated subject terms also have to occur in the paper;
    # shared words such as barrier/permeability alone could refer to soil.
    # Skip generic leading words removed by the tokenizer (e.g. "scientific"
    # in "scientific claim verification"). Requiring a removed word makes
    # every otherwise relevant English source fail for Chinese materials.
    subject_terms = set()
    for scoped_query in scoped_queries:
        for match in re.finditer(r"[A-Za-z][A-Za-z0-9–—-]{2,}", scoped_query):
            meaningful = tokens(match.group())
            if meaningful:
                subject_terms.update(meaningful)
                break
    translated -= {"quality", "control", "standardization", "reproducibility",
                   "validation", "monitoring", "clinical", "application"}
    links = {link.evidence_id: link for link in state.claim_evidence_links if link.claim_id == claim.claim_id}
    scored = []
    seen = set()
    seen_titles = set()
    records = sorted(state.evidence_records, key=lambda e: bool(links.get(e.evidence_id) and links[e.evidence_id].relation == "contradicts"), reverse=True)
    for record in records:
        kinds = {"literature", "standard", "regulatory", "guideline"} if state.report_preferences.resolved_artifact_type == "guideline" else {"literature"}
        if record.source_type not in kinds or not (record.abstract.strip() or record.excerpt.strip()):
            continue
        if not readable_research_content(record):
            continue
        from .report_composition import source_identity
        identity = source_identity(record)
        title_identity = re.sub(r"\W+", "", record.title.lower())
        if identity in seen or title_identity in seen_titles:
            continue
        # Model-generated comparison fields and query tags are not source text.
        # In particular they must not turn unrelated background into evidence.
        corpus = tokens(" ".join([record.title, record.abstract, record.excerpt]))
        overlap = target & corpus
        concrete = {term for term in overlap if term not in {"研究", "方法", "技术", "系统", "结果", "分析", "性能", "模型", "数据", "应用", "验证", "提高", "比较"}}
        title_overlap = target & tokens(record.title)
        if not concrete or (len(concrete) < 2 and not title_overlap):
            translated_overlap = translated & corpus
            translated_title = translated & tokens(record.title)
            if len(translated_overlap) < 2 or not translated_title or not subject_terms & corpus:
                continue
            concrete, title_overlap = translated_overlap, translated_title
        link = links.get(record.evidence_id)
        method_match = re.search(r"[A-Za-z][A-Za-z0-9 -]*", str(claim.claim_fields.get("method", "")))
        method_latin = tokens(method_match.group()) if method_match else set()
        first = method_match.group().split()[0] if method_match else ""
        primary = {first.lower()} if first.isupper() or re.search(r"[A-Z].*[A-Z]|\d", first) else method_latin
        reader = state.output_json.get("reader_comparisons", {}).get(claim.claim_id, {}).get(record.evidence_id, {})
        # A source-specific, two-sided comparison may legitimately use a
        # different named method. It still passed the topical text checks
        # above and the literal source-anchor validator during comparison.
        grounded_comparator = any(reader.get(key) for key in ("similarity", "difference", "conditions"))
        if not grounded_comparator and primary and len(primary & corpus) < min(2, len(primary)):
            continue
        secondary = method_latin - primary
        if not grounded_comparator and first.isupper() and secondary and not secondary & corpus:
            continue
        score = len(concrete) + len(title_overlap) * 2 + (1 if link else 0)
        if primary and record.title.lower().startswith(first.lower() + ":"):
            score += 8
        seen.add(identity)
        seen_titles.add(title_identity)
        scored.append((score, record, link))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = scored[:limit]
    counter = next((item for item in scored if item[2] and item[2].relation == "contradicts"), None)
    if counter and counter not in chosen:
        chosen = [*chosen[:max(0, limit-1)], counter]
    return [item[1] for item in chosen]


def readable_research_content(record):
    """Do not mistake publisher author/citation metadata for an abstract."""
    text = (record.abstract or record.excerpt or "").strip()
    # Short genuine source excerpts remain usable; explicit bibliographic-only
    # responses are excluded even if an index labels them as an abstract.
    if not re.search(r"Proceedings of|Journal of|Conference on", text, re.I):
        return bool(text)
    remainder = text.replace(record.title, "")
    for author in record.authors:
        remainder = remainder.replace(author, "")
    remainder = re.sub(r"Proceedings of[^.]*\.|\b(?:19|20)\d{2}\b|\b[A-Z][a-z]+ [A-Z][a-z]+\b", "", remainder)
    return len(re.findall(r"[A-Za-z]{3,}", remainder)) >= 18 or len(re.findall(r"[\u4e00-\u9fff]", remainder)) >= 60


def point_packet(state, claim):
    selected = selected_sources(state, claim)
    return {
        "material_type": state.report_preferences.resolved_artifact_type,
        "guideline_requirements": [{"requirement": r.text, "material_response": r.response_excerpt,
            "comparison": r.comparison_note} for r in state.guideline_requirements if r.response_excerpt],
        "point": {"id": claim.claim_id, "name": point_title(claim), "text": claim.text,
                  "assertion_type": claim.assertion_type, "fields": claim.claim_fields,
                  "location": claim.source_page_ref or claim.source_paragraph_ref},
        "material_design": {"research_content": state.nine_fields.research_content,
            "technical_route": state.nine_fields.technical_route,
            "extracted_fields": state.nine_fields.model_dump(),
            "submitted_text": state.raw_user_input[:20000],
            "work_packages": [item.model_dump() for item in state.work_packages
                if claim.claim_id in item.source_refs or
                (claim.source_document_id and claim.source_document_id in item.source_refs)]},
        "indicators": [item.model_dump() for item in state.indicators if item.source_claim_id == claim.claim_id or claim.claim_id in item.source_claim_ids],
        "indicator_evaluations": [item.model_dump() for item in state.indicator_validations if item.source_claim_id == claim.claim_id or claim.claim_id in item.source_claim_ids],
        "literature": [{"id": e.evidence_id, "title": e.title, "year": e.year,
                        "content": e.excerpt if e.fulltext_available and e.anchor_verified else e.abstract or e.excerpt,
                        "scope": "正文片段" if e.fulltext_available and e.anchor_verified else "摘要",
                        } for e in selected],
    }


def validated_paragraphs(items, packet, feedback=None, *, review_draft=False):
    """Reject unbound citations and numbers absent from cited source/material."""
    if feedback is None:
        feedback = []
    if not isinstance(items, list):
        feedback.append({"reason": "未返回段落数组"})
        return []
    sources = {item["id"]: item for item in packet["literature"]}
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        # Render an explicit paper name instead of exposing execution IDs in
        # generated narration. Quotations in the source records stay intact.
        for eid, source in sorted(sources.items(), key=lambda row: len(row[0]), reverse=True):
            text = re.sub(r'(?<![A-Za-z0-9_-])' + re.escape(eid) + r'(?![A-Za-z0-9_-])', lambda _: '《' + source['title'] + '》', text)
        ids = list(dict.fromkeys(str(eid) for eid in item.get("evidence_ids", []) if isinstance(eid, str)))
        absence = UNSUPPORTED_ABSENCE.search(text)
        invalid_ids = [eid for eid in ids if eid not in sources]
        # Editorial defects in an assessment draft must reach the source
        # reviewer, which can correct the offending assertion in context.
        # This mode is never used for the publication check below.
        if not text or invalid_ids or (not review_draft and (reader_prose(text) != text or absence)):
            reason = ("未返回正文，请写完整自然段" if not text else
                "引用不存在于当前来源，请从literature选择实际id：" + ", ".join(invalid_ids) if invalid_ids else
                "从局部来源推断了原研究或领域不存在某项工作：" + absence.group() +
                "。请比较双方明确记载的做法及本材料的设计意义；不宣称首次或未见系统整合，也不要改成摘要未提及的缺项叙述" if absence else
                "正文含内部诊断或未经验证的领先性表述，请改为有来源的具体研究分析")
            feedback.append({"dimension": item.get("dimension"), "text": text, "reason": reason})
            continue
        if re.search(r"(?<![A-Za-z0-9_-])(?:E\d+|E-[A-Za-z0-9_-]+|C\d+|claim[_-][A-Za-z0-9_-]+)(?![A-Za-z0-9_-])", text):
            feedback.append({"dimension": item.get("dimension"), "text": text, "reason": "正文含内部编号"})
            continue
        # Citations are rendered from metadata; prose may not invent numeric results.
        corpus = json.dumps([packet["point"], packet["indicators"], packet.get("material_design", {}),
                             packet.get("indicator_evaluations", []), *[sources[eid] for eid in ids]], ensure_ascii=False)
        if any(number not in corpus for number in re.findall(r"\d+(?:\.\d+)?", text)):
            feedback.append({"dimension": item.get("dimension"), "text": text, "reason": "数字或含数字的方法名称未在材料或所引来源出现"})
            continue
        if item.get("kind", "analysis") == "literature" and not ids:
            feedback.append({"dimension": item.get("dimension"), "text": text, "reason": "文献事实未关联有效来源"})
            continue
        if text not in {p["text"] for p in result}:
            result.append({"text": text, "evidence_ids": ids,
                           **({"dimension": str(item["dimension"])} if item.get("dimension") else {}),
                           **({"kind": str(item["kind"])} if item.get("kind") else {})})
    return result


async def draft(graph, state):
    from .point_evaluation import saved_evaluations, DIMENSIONS
    if state.report_snapshot:
        return
    drafts = state.output_json.setdefault("scholarly_drafts", {})
    for claim in state.claims:
        packet = point_packet(state, claim)
        stage_rows = saved_evaluations(state, claim) if not state.report_snapshot else []
        stage_dimensions = {row.get("dimension") for row in stage_rows if row.get("dimension") in DIMENSIONS}
        saved = drafts.get(claim.claim_id, {})
        saved_judgement_dimensions = {row.get("dimension") for row in saved.get("judgement", []) if row.get("dimension")}
        missing_stage_dimensions = sorted(
            set(DIMENSIONS) - stage_dimensions - saved_judgement_dimensions if stage_rows else set())
        if not packet["literature"]:
            continue
        review = state.output_json.get("scholarly_source_reviews", {}).get(claim.claim_id, {})
        pending = review.get("pending_draft") if review.get("failure_kind") == "review_protocol" else None
        if pending:
            # Restore the same prose after a transport/protocol interruption.
            # Verified paragraphs are cached by content and source packet.
            checked = deduplicate_analysis(await review_source_assertions(graph, state, packet, pending))
            review = state.output_json["scholarly_source_reviews"][claim.claim_id]
            saved = drafts.setdefault(claim.claim_id, {})
            saved.update(checked)
            if stage_rows:
                saved["judgement"] = [*stage_rows, *[row for row in saved.get("judgement", [])
                    if row.get("dimension") not in stage_dimensions]]
            state.output_json.setdefault("scholarly_unit_completion", {})[claim.claim_id] = {
                key: {"status": "pending" if any(r.get("key") == key and r.get("failure_kind") == "review_protocol"
                    for r in review.get("feedback", [])) or not saved.get(key) else "completed"}
                for key in ("progress", "comparison", "judgement", "evaluation")}
            await graph._emit_stage_update(state)
            if review.get("failure_kind") == "review_protocol":
                continue
        if all(saved.get(key) for key in ("progress", "comparison", "judgement", "evaluation")) and not missing_stage_dimensions:
            if stage_rows:
                saved["judgement"] = [*stage_rows, *[
                    row for row in saved["judgement"] if row.get("dimension") not in stage_dimensions]]
            continue
        count = len(packet["literature"])
        instruction = """逐项研究分析，返回 name(简短研究名称)、progress(研究进展)、comparison(方法与指标比较)、
judgement(创新性、前沿性、先进性和价值判断)、evaluation(分类评价)，均为段落数组，段落结构为
{text,evidence_ids,kind,dimension}，kind为literature/material/analysis。引用使用evidence_ids，正文不写编号。
研究进展介绍代表方法的对象、机制与适用问题，dimension使用representatives/advances/trends。
研究格局和趋势只能说明来源中实际出现的内容，不能推测国家或机构排名。
比较要解释材料方案与在先研究的具体联系、差异，dimension使用points/prior/metrics/differences，
指标定义、验证条件；judgement的dimension使用innovation/frontier/advancedness/value/limits，分别说明
创新贡献、前沿关联、同口径性能、学术应用价值和判断边界，有依据时展开、无依据时说明不能判断。
evaluation评价现有材料，不提出修改建议，不安排实施步骤。其dimension使用下方分类维度。
论文侧重设计、结果支持与复现；项目侧重路线、条件与风险；指南侧重条款、规范衔接与适用性。
有充分材料且至少四篇相关可读文献时，合计约2200至3000中文字，按实质内容自然展开。
资料简短则简明写作，不为篇幅添加泛论。不逐字复制英文摘要。
正文不要出现证据ID或要点ID，引用仅放evidence_ids。不要用数字枚举段落。
数值及带数字的方法名称只允许使用本材料或所引文献中实际出现的写法。段落先写具体研究内容或文献方法，
不要每段以“本指南”“本材料”“本项目”作公式化开头；少用“旨在”“具有重要意义”等套语，
不得将材料类型说明、执行过程或证据状态扩写成研究结论。"""
        if stage_rows:
            instruction += ("\n评估阶段已完成并校核的维度必须原样保留，不得改写或覆盖。"
                "仅补充以下尚未形成的三维评价：" + json.dumps(missing_stage_dimensions, ensure_ascii=False)
                + "；judgement可另写value/limits。其他章节与已保存判断衔接，不改变已有研究结论。\n"
                + json.dumps({"accepted_stage_evaluations": stage_rows}, ensure_ascii=False))
        requested = [key for key in ("progress", "comparison", "judgement", "evaluation")
                     if not saved.get(key) or (key == "judgement" and missing_stage_dimensions)]
        instruction += "\n仅生成尚未完成的章节：" + json.dumps(requested) + "\n已保存章节：" + json.dumps(saved, ensure_ascii=False)
        feedback = []
        payload = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
            user_prompt=instruction + "\n分类评价维度：" + json.dumps(evaluation_dimensions(state.report_preferences.resolved_artifact_type), ensure_ascii=False)
                + "\n" + json.dumps(packet, ensure_ascii=False), max_tokens=6500 if count >= 4 else 3200)
        payload = payload if isinstance(payload, dict) else {}
        clean = {key: validated_paragraphs(payload.get(key), packet, feedback) if key in requested else []
                 for key in ("progress", "comparison", "judgement", "evaluation")}
        if stage_rows:
            clean["judgement"] = [*stage_rows, *[row for row in clean["judgement"] if row.get("dimension") not in stage_dimensions]]
        # Develop distinct scientific questions, not longer versions of the
        # same summary. Short dossiers deliberately retain natural length.
        substantive = (len(state.claims) >= 4 and len(state.raw_user_input) + sum(
            len(d.excerpt) for d in state.assessment_profile.document_records) >= 2000)
        length = sum(len(p["text"]) for rows in clean.values() for p in rows)
        if count >= 2 and substantive and length < 3800:
            extension = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
                user_prompt="在以下已有评议基础上补充尚未分析的具体问题，只返回comparison/evaluation段落数组。"
                    "研究进展与创新判断已形成，不再追加文献简介或重复判断。方法比较分析数据划分、"
                    "对照公平性、指标含义、误差类型和可重复条件；判断区分方法沿用与可检验的方案改变；"
                    "评价材料已有对照、观察结果、失败条件和交付核验能支持什么判断，不编写建议。"
                    "论文侧重实验与复现，项目侧重依赖和里程碑，指南侧重任务边界及验收口径。"
                    "只展开与本要点及所引文献有关的方面，不添加空泛维度。合计约1800至2400中文字，"
                    "每部分4至6段，各段讨论不同的具体问题；结构{text,evidence_ids,kind}。数字只用材料或引用原文已出现的内容。\n"
                    + json.dumps({"sources_and_material": packet, "existing_analysis": clean}, ensure_ascii=False),
                max_tokens=6500)
            if isinstance(extension, dict):
                for key in clean:
                    seen = {p["text"] for p in clean[key]}
                    clean[key].extend(p for p in validated_paragraphs(extension.get(key), packet) if p["text"] not in seen)
        if substantive and count >= 2 and state.report_preferences.resolved_artifact_type in {'project', 'guideline'}:
            implementation_focus = (
                '从指南规范性、完整性与应用适用性补充已有评议尚未展开的分析，返回evaluation段落数组。'
                '分别分析本要点的适用场景与申报任务边界、输入和评价样本的定义、'
                '验收指标怎样对应实际观察对象、怎样记录困难样本和失败情况、'
                '独立复核需要怎样的材料与可重复配置。说明验收记录如何影响任务设置，'
                '区分指南提出的目标和承研单位以后报告的结果。'
                if state.report_preferences.resolved_artifact_type == 'guideline' else
                '从项目可行性角度补充已有评议尚未展开的依赖与交付评价，返回evaluation段落数组。'
                '围绕本要点的实际材料和文献方法，分别分析输入数据与前置条件、路线之间的依赖、'
                '阶段性交付如何核验、误差与失败怎样影响后续集成、可重复配置如何移交。')
            implementation = await graph._request_model_json(state, stage='report', system_prompt=WRITING_RULES,
                user_prompt=implementation_focus +
                    '给出具体观察对象和验收记录，不重复已有方法简介、指标定义或一般性建议。'
                    '只写适用于当前研究单元的独立问题，约800至1100中文字；已有分析覆盖的方面不再扩写。'
                    '评价已提供的材料和实施条件，不编写实施建议；不得自行指定时间、阈值或实验结果。'
                    '段落结构{text,evidence_ids,kind,dimension}，kind为analysis，文献事实必须引用实际来源。\n'
                    + '分类维度：' + json.dumps(evaluation_dimensions(state.report_preferences.resolved_artifact_type), ensure_ascii=False) + '\n'
                    + json.dumps({'source_material':packet,'existing_analysis':clean}, ensure_ascii=False), max_tokens=3500)
            if isinstance(implementation, dict):
                seen = {p['text'] for rows in clean.values() for p in rows}
                clean['evaluation'].extend(p for p in validated_paragraphs(implementation.get('evaluation'), packet)
                    if p['text'] not in seen)
        if stage_rows:
            # Already reviewed appraisals are immutable during report editing.
            # Review/consolidate only the new prose, then restore their exact text/citations.
            clean["judgement"] = [row for row in clean["judgement"] if row.get("dimension") not in stage_dimensions]
        clean = await consolidate_analysis(graph, state, packet, clean)
        clean = deduplicate_analysis(await review_source_assertions(graph, state, packet, clean))
        review = state.output_json.get("scholarly_source_reviews", {}).get(claim.claim_id, {})
        feedback.extend(review.get("feedback", []))
        protocol_units = {row["key"] for row in review.get("feedback", [])
                          if row.get("failure_kind") == "review_protocol" and row.get("key")}
        for key, rows in saved.items():
            if rows:
                if key == "judgement" and stage_rows:
                    known = {(row.get("dimension"), row.get("text")) for row in rows}
                    clean[key] = [*rows, *[row for row in clean.get(key, [])
                        if (row.get("dimension"), row.get("text")) not in known]]
                else:
                    clean[key] = rows
        if stage_rows:
            clean["judgement"] = [*stage_rows, *[row for row in clean.get("judgement", []) if row.get("dimension") not in stage_dimensions]]
        missing = [key for key in ("progress", "comparison", "judgement", "evaluation") if not clean.get(key)]
        repair_units = [] if protocol_units else missing
        if repair_units:
            repair = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
                user_prompt="这是本次唯一的章节定向修复。仅补写missing_units中的章节，保留已有章节和三维评价。"
                    "根据validation_feedback中的原因，围绕材料和来源实际记载的做法写完整研究分析。"
                    "返回对应章节的段落数组{text,evidence_ids,kind,dimension}，引用只放evidence_ids。\n"
                    + json.dumps({"missing_units": repair_units, "validation_feedback": feedback,
                                  "source_material": packet, "accepted_chapters": clean}, ensure_ascii=False), max_tokens=4500)
            candidates = {key: validated_paragraphs(repair.get(key), packet, feedback) if isinstance(repair, dict) else [] for key in repair_units}
            repaired = await review_source_assertions(graph, state, packet, candidates)
            repair_review = state.output_json.get("scholarly_source_reviews", {}).get(claim.claim_id, {})
            protocol_units.update(row["key"] for row in repair_review.get("feedback", [])
                if row.get("failure_kind") == "review_protocol" and row.get("key"))
            for key in repair_units:
                clean[key] = repaired.get(key, [])
        name = str(payload.get("name") or "").strip()
        if name and len(name) <= 40 and '方法与应用研究' not in name and not INTERNAL.search(name) and tokens(name) & tokens(claim.text + " ".join(claim.claim_fields.values())):
            claim.display_title = name
        drafts[claim.claim_id] = clean
        state.output_json.setdefault("scholarly_unit_completion", {})[claim.claim_id] = {
            key: {"status": "completed" if clean.get(key) and key not in protocol_units else "pending", "repair_attempted": key in repair_units}
            for key in ("progress", "comparison", "judgement", "evaluation")}
        await graph._emit_stage_update(state)
    state.output_json["scholarly_drafts"] = drafts
    state.output_json["report_source_selection"] = {
        c.claim_id: [e.evidence_id for e in selected_sources(state, c)] for c in state.claims}
    # Save synthesis units independently. A failed conclusion must not clear
    # a source-reviewed summary (or force all point chapters to regenerate).
    packet = {"material_type": state.report_preferences.resolved_artifact_type,
              "points": [{"name": point_title(c), "judgement": drafts.get(c.claim_id, {}).get("judgement", []),
                          "evaluation": drafts.get(c.claim_id, {}).get("evaluation", [])}
                         for c in state.claims]}
    literature = {e['id']: e for c in state.claims for e in point_packet(state, c)["literature"]}
    merged = {"point": {"id": "summary", **packet, "materials": [point_packet(state, c)["point"] for c in state.claims]},
              "material_design": {"submitted_text": state.raw_user_input[:20000],
                                  "extracted_fields": state.nine_fields.model_dump()},
              "indicators": [item.model_dump() for item in state.indicators],
              "literature": list(literature.values())}
    units = state.output_json.setdefault("scholarly_synthesis_completion", {})
    pending_drafts = state.output_json.setdefault("scholarly_synthesis_drafts", {})
    keys = {"summary": "scholarly_summary", "conclusion": "scholarly_conclusion"}
    synthesis_available = False
    # A protocol outage must retain the original prose for source review on
    # resume. Never replace it with a newly invented summary of the failure.
    protocol_pending = set()
    for key, storage in keys.items():
        saved = pending_drafts.get(key)
        if state.output_json.get(storage) or not saved:
            continue
        unit_packet = {**merged, "point": {**merged["point"], "id": key}}
        checked = await review_source_assertions(graph, state, unit_packet, {key: saved})
        audit = state.output_json["scholarly_source_reviews"][key]
        synthesis_available |= not audit.get('failure_kind')
        if checked[key]:
            state.output_json[storage] = checked[key]
            units[key] = {"status": "completed", "feedback": []}
        elif audit.get('failure_kind') == 'review_protocol':
            protocol_pending.add(key)
            units[key] = {"status": "pending", "feedback": audit.get("feedback", []),
                          "failure_kind": "review_protocol"}
        await graph._emit_stage_update(state)
    for attempt in range(2):
        missing = [key for key, storage in keys.items() if not state.output_json.get(storage) and key not in protocol_pending]
        if not missing:
            break
        prompt = ("将以下已校核逐项评议综合为评估摘要和综合结论，分别形成完整自然段。"
            "summary交代总体定位、主要差异和核心判断；conclusion综合研究贡献、适用条件与研究意义。"
            "不要添加新事实，不重复原句，不编写修改建议；计划保持计划属性。"
            "返回JSON对象，仅返回下方missing_units指定的键，各键为{text,evidence_ids,kind}段落数组。"
            "引用必须来自source_material中的literature。")
        if attempt:
            prompt += "根据validation_feedback改正失败内容，保留已通过部分。只比较实际做法，不推断文献未开展的工作。"
        feedback = {key: [{field: row[field] for field in ('dimension', 'reason') if field in row}
                         for row in units.get(key, {}).get("feedback", [])] for key in missing}
        payload = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
            user_prompt=prompt + "\n" + json.dumps({"missing_units": missing, "validation_feedback": feedback,
                "source_material": merged, "accepted_summary": state.output_json.get("scholarly_summary", []),
                "accepted_conclusion": state.output_json.get("scholarly_conclusion", [])}, ensure_ascii=False), max_tokens=3000)
        synthesis_available |= isinstance(payload, dict) and bool(payload)
        for key in missing:
            issues = []
            items = payload.get(key, payload.get("paragraphs") if key == "summary" else None) if isinstance(payload, dict) else None
            rows = validated_paragraphs(items, merged, issues, review_draft=True)
            if rows:
                pending_drafts[key] = rows
                await graph._emit_stage_update(state)
                unit_packet = {**merged, "point": {**merged["point"], "id": key}}
                rows = (await review_source_assertions(graph, state, unit_packet, {key: rows}))[key]
                audit = state.output_json.get("scholarly_source_reviews", {}).get(key, {})
                issues.extend(audit.get("feedback", []))
                if not rows and audit.get('failure_kind') == 'review_protocol':
                    protocol_pending.add(key)
            if rows:
                state.output_json[keys[key]] = rows
            units[key] = {"status": "completed" if rows else "pending", "repair_attempted": bool(attempt),
                          "feedback": issues or ([] if rows else [{"reason": "未形成有效综合段落，请综合已有逐项评价并绑定来源"}])}
            await graph._emit_stage_update(state)



    # Summaries may reuse exact accepted analysis instead of failing the report
    # over an editorial defect. Never use rejected text or mask model outages.
    for key, storage in keys.items():
        if state.output_json.get(storage) or key in protocol_pending or not synthesis_available:
            continue
        collected, seen = [], set()
        for claim in state.claims:
            chapter = drafts.get(claim.claim_id, {})
            order = ('judgement', 'comparison') if key == 'summary' else ('evaluation', 'judgement')
            rows = [row for section in order for row in chapter.get(section, [])]
            accepted = validated_paragraphs(rows, merged)
            chosen = next((row for row in accepted if row['text'] not in seen), None)
            if chosen:
                seen.add(chosen['text'])
                collected.append(chosen)
        if collected:
            state.output_json[storage] = collected
            units[key] = {"status": "completed", "mode": "reviewed_extract", "paragraph_count": len(collected)}
            await graph._emit_stage_update(state)


async def consolidate_analysis(graph, state, packet, paragraphs):
    """Revise the complete point, so duplicate meanings across sections are visible."""
    result = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
        user_prompt="整理以下同一研究要点的完整评议，返回progress/comparison/judgement/evaluation四个段落数组。"
            "合并跨段落和跨章节的同义论述。progress只介绍在先方法；comparison逐项解释材料方案、对照变量、"
            "指标含义及解释条件；judgement分别给出创新性、前沿性、先进性及价值判断；evaluation给出分类评价。"
            "保留dimension。评价已提交的材料、科学设计、实施条件或规范条款，不编写修改建议或行动方案。"
            "每个实质分析保留一次，不能仅换词重复。不要缩写为提纲，不要删去独立的分析问题。"
            "原研究或领域没有做某项工作的否定断言改为拟比较的方案及其与已知方法的联系；"
            "检索样本不能证明尚属空白、未见报道或尚未验证。不得追加来源之外的事实。"
            "段落结构{text,evidence_ids,kind,dimension}；引用只放evidence_ids。\n"
            + json.dumps({"source_material":packet,"analysis":paragraphs}, ensure_ascii=False), max_tokens=7000)
    if isinstance(result, dict):
        clean = {key:validated_paragraphs(result.get(key), packet) for key in paragraphs}
        if all(clean.values()):
            return clean
    # A malformed editorial response cannot discard already verified analysis.
    return paragraphs


def deduplicate_analysis(paragraphs):
    """Keep each nearly identical sentence once within a point, across chapters.

    This runs before freezing, after source review. Distinct conditions,
    negations, numbers and citations are retained even when wording is close.
    """
    seen = []
    result = {}
    for key, rows in paragraphs.items():
        result[key] = []
        for row in rows:
            kept = []
            for sentence in re.findall(r"[^。！？；\n]+[。！？；]?", row["text"]):
                normalized = re.sub(r"[\s，,：:《》]", "", sentence)
                qualifiers = re.findall(r"\d+(?:\.\d+)?|不|未|无|仅|除|若|假如", normalized)
                citations = set(row.get("evidence_ids", []))
                duplicate = len(normalized) >= 30 and any(
                    qualifiers == prior_qualifiers and citations == prior_citations
                    and SequenceMatcher(None, normalized, prior, autojunk=False).ratio() >= .9
                    for prior, prior_qualifiers, prior_citations in seen)
                if not duplicate:
                    kept.append(sentence)
                    if len(normalized) >= 30:
                        seen.append((normalized, qualifiers, citations))
            if kept:
                result[key].append({**row, "text": "".join(kept)})
    return result


async def review_source_assertions(graph, state, packet, paragraphs, *, stage="report", audit_key="scholarly_source_reviews", review_instruction="", repair_budget=1):
    """Independently review attribution before freezing, with exact source text."""
    flattened = [{"key": key, "index": index, **row}
                 for key, rows in paragraphs.items() for index, row in enumerate(rows)]
    cid = packet["point"]["id"]
    replacements = []
    feedback = []
    failed = set()
    cache = state.output_json.setdefault('source_review_verified', {})
    def fingerprint(row):
        return hashlib.sha256(json.dumps(['material-attribution-v2', packet, row], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    verified = set(cache.get(audit_key + ':' + cid, []))
    remaining = [row for row in flattened if fingerprint(row) not in verified]
    attempts = []
    failure_kind = ''
    for offset in range(0, len(remaining), 6):
        batch = remaining[offset:offset + 6]
        prompt = ("逐段核对下面评议。材料事实来自source_material的point、material_design、indicators及guideline_requirements；"
                "文献事实来自对应literature.content。同一段可分别介绍双方，不要求材料方案也出现在文献中。"
                "先辨认每句话的主语再校核；不能因论文没有记载本材料的方案而否定比较。"
                "material_design.submitted_text是用户原始材料。指南已写出的条款是草案内容，"
                "可以说草案规定或设计了某流程，不能说实验已完成或效果已实现。"
                "来源明确写出的否定事实可以限定来源、对象和条件转述，不可扩展为整个领域不存在。"
                "特别检查训练集、架构、作者、年份、实验指标和性能是否实际出现；不能凭论文标题或常识补全。"
                "摘要没有提及不能推断原研究未开展；材料的计划不能写成完成结果。"
                "区分原研究或领域的缺口断言与本材料的计划状态；材料明确拟开展的验证可以保持待验证表述，"
                "不得将这一状态误判为宣称整个领域尚未验证，也不得改写为已经取得的结果。"
                "出现尚属空白、未见报道、尚未验证、尚未应用等研究缺口断言时，必须改写为拟比较变量和已知方法联系。"
                "不要把未提及的方法推断为材料明确排除。说明具体方法联系、差异及结论条件。"
                "只保留影响评价的证据缺口与待验证问题，不将缺口改写为咨询建议。"
                "建议中解释模型组件或指标变化的因果关系也须核对来源；无直接依据改为待检验的解释。"
                "返回{checked:true,replacements:[{key,index,text,evidence_ids,kind,dimension,reason}]}。reason用一句话指出具体错误，不输出推敲过程或相互矛盾的改写建议。保留原dimension，只列需要修正的段落，"
                "key/index保留所给值，用原来源实际记载的内容改写并保留具体比较，无法成立时text为空。"
                "将无依据的否定断言改为来源中实际采用的方法，以及本材料明确提出的设计和研究意义，"
                "不要仅改成‘摘要未提及’、‘材料未说明’或‘无法判断’；这些缺项描述不能代替实质评价。"
                "全部准确则replacements为空。不得引入新的文献ID或未在来源出现的新事实。\n" +
                review_instruction + "\n" + json.dumps({"source_material":packet, "paragraphs":batch}, ensure_ascii=False))
        locations = {(r['key'], r['index']) for r in batch}
        result = None
        for protocol_attempt in range(2):
            raw = await graph._request_model_json(state, stage=stage, system_prompt=WRITING_RULES,
                user_prompt=prompt, max_tokens=4800 if protocol_attempt else 3200)
            result, reason = review_response(raw, locations)
            attempts.append(response_diagnostic(raw, reason, batch=offset // 6, attempt=protocol_attempt + 1))
            if result is not None or reason == 'review_not_approved':
                break
            prompt = ('上一轮来源校核响应不符合协议，原因：' + reason +
                '。重新核对同一批原文，不重写报告。必须返回一个完整JSON对象，'
                'checked为布尔值，replacements为数组；只用给定key/index。\n' + prompt)
        if result is None:
            batch_failure = 'source_rejected' if reason == 'review_not_approved' else 'review_protocol'
            if failure_kind != 'review_protocol':
                failure_kind = batch_failure
            failed.update((r["key"], r["index"]) for r in batch)
            feedback.extend({"key": r["key"], "dimension": r.get("dimension"), "text": r["text"],
                             "failure_kind": batch_failure,
                             "reason": "来源校核响应异常，保留原文等待校核恢复" if batch_failure == 'review_protocol'
                                else "来源校核未批准当前段落，请按实际来源修正"} for r in batch)
            continue
        valid_keys = {(r["key"], r["index"]) for r in batch}
        if any(not isinstance(r, dict) or type(r.get("index")) is not int or (r.get("key"), r.get("index")) not in valid_keys for r in result["replacements"]):
            failed.update(valid_keys)
            feedback.append({"reason": "来源校核返回的段落索引不属于当前批次"})
            continue
        replacements.extend(result["replacements"])
    result = {"replacements": replacements}
    changes = {}
    for item in result["replacements"]:
        if not isinstance(item, dict):
            continue
        key, index = item.get("key"), item.get("index")
        if key not in paragraphs or not isinstance(index, int) or not 0 <= index < len(paragraphs[key]):
            continue
        replacement = {**item, "dimension": item.get("dimension") or paragraphs[key][index].get("dimension", "")}
        rejected = []
        changes[(key, index)] = validated_paragraphs([replacement], packet, rejected)
        if not changes[(key, index)] or item.get("reason"):
            feedback.append({"key": key, "dimension": replacement.get("dimension"),
                             "text": paragraphs[key][index]["text"],
                             "reason": "；".join(dict.fromkeys([
                                 *[r["reason"] for r in rejected],
                                 *([item["reason"]] if item.get("reason") else []),
                             ])) or "该段未通过来源校核；请仅使用所给材料及所引文献明确记载的事实重写"})
    # A checked:true response is not a waiver for deterministic validation.
    # In particular, an unchanged draft containing an absence assertion must
    # never be published when the reviewer omitted its replacement.
    for key, rows in paragraphs.items():
        for index, original in enumerate(rows):
            if (key, index) in failed or (key, index) in changes:
                continue
            rejected = []
            changes[(key, index)] = validated_paragraphs([original], packet, rejected)
            feedback.extend({**r, "key": key} for r in rejected)
    for row in flattened:
        location = (row['key'], row['index'])
        if location not in failed:
            for checked in changes.get(location, []):
                verified.add(fingerprint({'key': row['key'], 'index': row['index'], **checked}))
    cache[audit_key + ':' + cid] = sorted(verified)
    audit = {
        "status":"pending" if failed or any(not rows for rows in changes.values()) else "completed",
        "paragraph_count":len(flattened), "corrected_count":len(replacements),
        "feedback": feedback, "failure_kind": failure_kind,
        "protocol_attempts": attempts, "pending_draft": paragraphs if failed else {}}
    state.output_json.setdefault(audit_key, {})[cid] = audit
    accepted = {key: [row for index, original in enumerate(rows)
                  if (key, index) not in failed
                  for row in changes.get((key,index), [original])]
            for key, rows in paragraphs.items()}
    # A rejected sentence must not discard otherwise usable chapters. Repair
    # only wholly empty units, from original sources, with a separate review.
    # Transport failures retain the checkpoint rather than fabricating prose.
    missing = [key for key, rows in paragraphs.items() if rows and not accepted[key]]
    if missing and repair_budget and stage == 'report' and not failure_kind:
        repair = await graph._request_model_json(state, stage=stage, system_prompt=WRITING_RULES,
            user_prompt="对以下未通过的报告单元进行局部修正，不重写已通过章节。返回missing_units中每个键的段落数组"
                "{text,evidence_ids,kind,dimension}。每个单元写一至两个完整自然段，"
                "只讲材料实际提出的方案、文献实际采用的做法及二者的具体联系。"
                "材料中的条款和目标用‘拟’‘草案规定’等表达；不推断论文没有开展什么，不宣称领先或填补空白。"
                "不用缺项清单替代评议，不补造指标或实测结果。不得返回已通过的单元。\n"
                + json.dumps({"missing_units": missing, "source_material": packet,
                    "validation_feedback": [{field: row[field] for field in ('key', 'dimension', 'reason') if field in row}
                                            for row in feedback if row.get('key') in missing]}, ensure_ascii=False),
            max_tokens=3600)
        candidates = {key: validated_paragraphs(repair.get(key), packet, review_draft=True)
                      if isinstance(repair, dict) else [] for key in missing}
        if any(candidates.values()):
            repaired = await review_source_assertions(graph, state, packet, candidates, stage=stage,
                audit_key=audit_key + '_repair', review_instruction=review_instruction, repair_budget=0)
            for key in missing:
                accepted[key] = repaired.get(key, [])
            audit['repair'] = state.output_json[audit_key + '_repair'][cid]
            if audit['repair'].get('failure_kind'):
                failure_kind = audit['failure_kind'] = audit['repair']['failure_kind']
                audit['pending_draft'] = paragraphs
        elif repair is None:
            failure_kind = audit['failure_kind'] = 'review_protocol'
            audit['pending_draft'] = paragraphs
    if all(accepted[key] for key, rows in paragraphs.items() if rows) and not failure_kind:
        audit['status'] = 'completed'
    return accepted


def publication_gaps(state):
    """Keep unfinished main analyses on the task page, outside the report."""
    drafts = state.output_json.get("scholarly_drafts", {})
    completion = state.output_json.get("scholarly_unit_completion", {})
    return [point_title(c) for c in state.claims if not selected_sources(state, c) or
            any(unit.get("status") == "pending" for unit in completion.get(c.claim_id, {}).values()) or
            any(not drafts.get(c.claim_id, {}).get(key) for key in
                ("progress", "comparison", "judgement", "evaluation"))]


def build(state):
    from .research_outline import compose_research_report
    return compose_research_report(state)


def _build_legacy_specs(state):
    """Six frozen chapters with point-specific prose and only cited literature."""
    from .report_composition import refresh_section
    specs = [ReportSectionSpec(section_key=key, title=title, order=i, required=True,
                              is_appendix=key == "references") for i, (key, title) in enumerate(LEGACY_CHAPTERS, 1)]
    sections = {item.section_key: item for item in specs}
    # Only request the actual sample trend, never an audit coverage/score chart.
    years = {e.year for c in state.claims for e in selected_sources(state, c) if e.year}
    if len(years) >= 2:
        sections["progress"].chart_keys = ["years"]
    def add(key, text, ids=(), claim_id="", kind="paragraph"):
        if text:
            sections[key].blocks.append(ReportBlock(kind=kind, text=text, evidence_ids=list(ids), claim_id=claim_id))
    kind = state.report_preferences.resolved_artifact_type
    focus = {"paper": "方法设计、实验对照与可重复性", "project": "技术路线、阶段目标与验证交付", "guideline": "任务设置、指标定义与应用验收"}.get(kind, "研究方案与验证设计")
    names = [point_title(c) for c in state.claims]
    add("executive", f"本报告评估“{state.assessment_profile.title}”，围绕{'、'.join(names[:6])}考察{focus}。研究判断以材料陈述和所引文献的实际内容为范围，方案目标、材料报告结果与文献结果分别表述。")
    profile = state.assessment_profile
    add("object", profile.assessment_object or profile.title)
    if profile.scope_boundary:
        add("object", reader_prose(profile.scope_boundary))
    if kind == "project" and state.guideline_requirements:
        responses = [r for r in state.guideline_requirements if r.response_excerpt]
        if responses:
            add("object", "项目与配套指南的对应关系", kind="heading")
            for requirement in responses:
                add("object", "指南要求：" + requirement.text + "\n项目材料：" + requirement.response_excerpt)
                add("object", reader_prose(requirement.comparison_note))
    drafts = state.output_json.get("scholarly_drafts", {})
    used = set()
    first_quote = set()
    summaries = []
    for claim in state.claims:
        name = point_title(claim)
        cid = claim.claim_id
        chosen = selected_sources(state, claim)
        allowed = {e.evidence_id for e in chosen}
        point = drafts.get(cid, {})
        add("object", name, claim_id=cid, kind="heading")
        prefix = {"target": "材料提出的研究目标：", "planned": "材料拟开展的研究：", "completed": "材料报告的研究内容："}.get(claim.assertion_type, "材料陈述：")
        add("object", prefix + claim.text, claim_id=cid)
        # Preserve the submitted research design, rather than reducing a rich
        # dossier to its one-sentence claim. Repeated clauses in validation
        # fields are shown once; no empty field matrix is generated.
        shown = set()
        labels = {"object": "研究对象", "method": "拟采用方法" if claim.assertion_type != "completed" else "研究方法",
                  "baseline": "比较路线", "metric": "观察指标", "validation": "对照与验证安排"}
        for field, label in labels.items():
            value = str(claim.claim_fields.get(field) or "").strip()
            clauses = re.split(r"(?<=[。；])\s*", value)
            unique = []
            for clause in clauses:
                identity = re.sub(r"\s+", "", clause).strip("。； ")
                if identity and identity not in shown:
                    unique.append(clause)
                    shown.add(identity)
            if unique:
                add("object", label + "：" + "".join(unique), claim_id=cid)
        location = claim.source_page_ref or claim.source_paragraph_ref
        if location:
            add("object", "材料位置：" + location, claim_id=cid)
        for key in ("progress", "comparison", "recommendations"):
            add(key, name, claim_id=cid, kind="heading")
        for output_key, draft_key in (("progress", "progress"), ("comparison", "comparison"), ("comparison", "judgement"), ("recommendations", "recommendations")):
            for paragraph in point.get(draft_key, []):
                ids = [eid for eid in paragraph["evidence_ids"] if eid in allowed]
                add(output_key, paragraph["text"], ids, cid)
                used.update(ids)
            if draft_key == "judgement" and point.get(draft_key):
                summaries.append((name, point[draft_key][0]))
        if not point.get("progress"):
            for e in chosen[:3]:
                if e.evidence_id in first_quote:
                    continue
                first_quote.add(e.evidence_id)
                # A source excerpt is explicitly quoted, never relabelled as synthesis.
                source_text = e.abstract or e.excerpt
                add("progress", f"《{e.title}》{f'（{e.year}）' if e.year else ''}的{'正文' if e.anchor_verified else '摘要'}记载：\n“{source_text}”", [e.evidence_id], cid)
                used.add(e.evidence_id)
        if not point.get("comparison"):
            links = [link for link in state.claim_evidence_links if link.claim_id == cid and link.evidence_id in allowed]
            for link in links:
                detail = reader_prose(link.difference)
                if detail:
                    add("comparison", detail, [link.evidence_id], cid)
                    used.add(link.evidence_id)
            # State only actual paired values. No empty comparison rows.
            rows = []
            labels = {"object": "研究对象", "method": "方法", "data": "数据", "baseline": "对照", "metric": "指标", "validation": "验证条件"}
            for e in chosen[:3]:
                for field, label in labels.items():
                    a, b = claim.claim_fields.get(field), e.comparison_fields.get(field)
                    if a and b:
                        rows.append(f"| {label} | {a.replace('|', '／')} | {b.replace('|', '／')} | {e.evidence_id} |")
                        used.add(e.evidence_id)
            if rows:
                add("comparison", "| 比较内容 | 本研究材料 | 代表文献 | 来源 |\n| --- | --- | --- | --- |\n" + "\n".join(rows), [e.evidence_id for e in chosen if e.evidence_id in used], cid, "table")
        for indicator in state.indicators:
            if indicator.source_claim_id != cid and cid not in indicator.source_claim_ids:
                continue
            validation = next((v for v in state.indicator_validations if v.indicator_id == indicator.indicator_id), None)
            if validation and validation.assessment_conclusion:
                ids = [eid for eid in validation.evidence_refs if eid in allowed]
                text = reader_prose(validation.assessment_conclusion)
                if text and ids:
                    add("comparison", text, ids, cid)
                    used.update(ids)
        if not point.get("judgement"):
            decision = next((d for d in state.novelty_decisions if d.claim_id == cid), None)
            if decision:
                text = reader_prose(decision.rationale)
                ids = [eid for eid in decision.evidence_ids if eid in allowed]
                if text and ids:
                    add("comparison", text, ids, cid)
                    summaries.append((name, {"text": text, "evidence_ids": ids}))
                    used.update(ids)
        if not point.get("recommendations"):
            indicators = [item for item in state.indicators if item.source_claim_id == cid or cid in item.source_claim_ids]
            for item in indicators:
                content = "；".join(value for value in [item.test_method, item.acceptance_condition, item.statistics_method] if value)
                if content:
                    add("recommendations", f"{item.name}的验证设计：{content}。", claim_id=cid)
    for item in state.output_json.get("scholarly_summary", []):
        add("executive", item["text"], item["evidence_ids"])
        used.update(item["evidence_ids"])
    # References come exclusively from final prose, never the retrieval count.
    for e in state.evidence_records:
        if e.evidence_id in used:
            add("references", e.title, [e.evidence_id])
    add("references", "评估范围限于所列材料与引用文献；提出的验证方案属于后续研究建议。")
    for section in specs:
        refresh_section(section)
    return specs
