"""Source-bound, reader-facing appraisal available before report composition."""
from __future__ import annotations

import hashlib
import html
import json
import re

from .scholarly_report import (
    deduplicate_analysis, point_packet,
    review_source_assertions, validated_paragraphs,
)


DIMENSIONS = {"innovation": "创新性", "frontier": "前沿性", "advancedness": "先进性"}
EVALUATION_WRITING_RULES = """你是科研评议人员，依据给定材料和文献原文评价研究设计。输出结构化JSON。
评价对象是材料实际提出的方案：说明它如何组织研究问题、方法与验证，再以文献明确记载的做法说明联系。
研究贡献可以体现为研究问题、方法组织或验证设计的具体价值；选取的文献不能证明领域首创或文献空白。
引用仅使用literature中的id。文献事实来自content，材料事实来自point和material_design。
保留拟开展、计划和目标的性质。不同实验口径不直接比较性能高低；可评价方案中具体机制、验证设计与应用衔接。
采用完整、专业的自然段，每段包含具体的材料设计、来源联系及研究意义。
只论述资料实际记载的研究做法，避免以资料未提及什么来代替评价；不得编造数值、方法或结果。
"""
INSTRUCTION = """根据实际材料及相关文献，为当前研究要点撰写三项研究评价。
返回 {evaluations:[{dimension,text,evidence_ids,kind}]}；dimension只能使用
innovation/frontier/advancedness；kind为material/literature/analysis。
每项形成一至两段完整分析：先给针对该研究内容的判断，再解释具体比较及其研究意义。
采用科研评议的书面语，围绕本材料的技术问题、具体研究设计和适用场景组织论证。
按论点选择真正相关的文献，必要时综合不同来源的方法、验证设计及结果，不固定每项引用数量，
不要求三个维度引用相同文献，也不为增加引用而附加没有用于论证的来源。
innovation：分析问题设置、方法组合、机制或验证设计的具体贡献，区别已有方法的继承与变化。
frontier：联系所引研究正在处理的科学问题、方法演进与应用需求，不能仅按年份判断。
advancedness：比较技术机制、实验设计、适用对象及验证表现；有同口径结果才讨论性能优势，
否则就实际可分析的方法与设计作评价，不将先进性等同于必须取得更高数值。
论文侧重方法与实验，项目侧重技术路线与实现条件，指南侧重任务设置、指标口径与应用衔接。
材料写拟、计划、目标时沿用这一性质；不得改成已完成的实验结果。
文献事实必须引用下方literature实际存在的id，引用只放evidence_ids，正文不写后台编号。
只分析来源能够说明的研究内容，不罗列字段缺失，不用不能判断、不能仅凭年份等固定句代替评价。
仍需明确真正影响比较的对象、实验条件或适用范围，不宣称未经验证的领先性。
不逐字重复材料或摘要；三项各有分析重点，按实际内容自然展开，通常每项约二百中文字。
先评价本材料自己的研究设计，再用文献说明联系，不围绕文献没有做什么展开。
不得把文献中的具体方法写成本材料明确采用的方法；例如来源使用SHAP，不代表材料也使用SHAP。
摘要未提及某项验证不能写成原研究未开展该验证，不把未提及当作研究差异。
方法设计的意义不等于更严谨、更先进或性能领先；仅在同口径事实支持时作优劣比较。
不要为完整性额外罗列材料未说明的细节，将适用条件与当前判断自然衔接。
只围绕双方明确记载的做法开展比较，不写“文献未在摘要中提及”之类的缺项叙述。
没有同口径性能数据时，先进性应评价已有技术组合、验证设计和应用衔接的具体意义，
不要转为“材料未提供……因此无法比较”的收尾。不得据资料的局部内容声称整份材料未提供某信息。
选取的几篇文献不代表整个领域，不能宣称某设计在领域内较少、首次、领先或属于领域共识。
"""

SOURCE_BASIS_RULES = """先组织事实依据，再写评价，二者在同一次响应中返回。
每项评价增加source_basis:{material_quotes:[材料中的连续原文],sources:[{id,quote,role}]}。
quote逐字摘录literature.content中的连续原文；role为direct/method/background。
direct是对象与比较问题相符的直接研究，method是可借鉴方法，background是背景研究。
优先采用direct；不同电极、反应体系或应用对象的来源应按method使用，只联系明确记载的方法，
不以负极、钠体系或背景综述推断高镍正极性能。不凭标题认定电池形式、验证流程或实验条件。
背景资料不能单独构成先进性评价；引用数量由实际论点决定，不将所有来源都作直接比较。
source_basis只记录实际用于该段的依据，其sources与evidence_ids一一对应；不增加新的事实。
正文按“材料拟开展的具体设计—来源明确采用的方法—该设计可检验的具体问题”展开。
不同验证设计的意义可以直接解释，不用先声称文献未开展某种验证来证明其价值。
"""

REVIEW_INSTRUCTION = """本次只核对任务页逐项评价。严格区分材料明确采用的方法与参考文献的方法；
例如材料只写注意力或子结构归因，不能说本研究采用SHAP。材料事实只能来自point.text、point.fields和material_design。
point.name只是显示名称，不能据该名称认定材料明确采用了某方法。
来源摘要没有记载某操作不能推断原研究未进行；删改仅、未涉及、单一数据集等无来源的排他性断言。
没有同口径比较时，不允许更为严谨、领先、优于等优劣判断；改为具体设计的研究意义。
保留每项对材料的实质评价与文献联系，不罗列尚未说明的字段或技术细节。
不得编造本研究不追求某性能、明确排除某方法等材料未声明的意图。
只用双方已记载的研究做法说明联系，不以文献摘要未提及的内容推断研究差异。
不添加材料缺项的收尾；没有可比较性能数值时，围绕技术机制、验证设计及应用衔接给出具体评价，
不把这一点写成拒绝评价。不要重复题名；只说来源使用某验证方法，除非来源明确排除其他方法，不加“仅”。"""


def checked_source_basis(items, packet, feedback, *, strict=True):
    """Check optional verbatim fact plans before the semantic source review.

    Older model responses without a fact plan still undergo the full reviewer;
    a supplied plan must match the original material and each cited source.
    Keep it out of the input fingerprint and reader-facing evaluation rows.
    """
    def normalize(value):
        value = html.unescape(str(value or ""))
        value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value, flags=re.S)
        return re.sub(r"\s+|<[^>]+>", "", value)

    sources = {row["id"]: row for row in packet["literature"]}
    material = normalize(json.dumps([packet["point"], packet["material_design"], packet["indicators"]], ensure_ascii=False))
    accepted, bases = [], {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        basis = item.get("source_basis")
        if basis is None:
            accepted.append(item)
            continue
        quotes = basis.get("material_quotes", []) if isinstance(basis, dict) else []
        uses = basis.get("sources", []) if isinstance(basis, dict) else []
        valid = (isinstance(quotes, list) and bool(quotes) and isinstance(uses, list) and bool(uses)
            and all(isinstance(q, str) and normalize(q) and normalize(q) in material for q in quotes)
            and all(isinstance(u, dict) and u.get("id") in sources
                and u.get("role") in {"direct", "method", "background"}
                and isinstance(u.get("quote"), str) and normalize(u["quote"])
                and normalize(u["quote"]) in normalize(sources[u["id"]]["content"]) for u in uses)
            and {u["id"] for u in uses} == set(item.get("evidence_ids", [])))
        if not valid:
            feedback.append({"dimension": item.get("dimension"),
                "reason": "附加事实依据清单未完全匹配保存的原文；已降级为正文引用校核，保留可核验内容" if not strict else
                    "事实依据必须逐字来自材料和对应文献content，并覆盖本段全部引用；请重新选取实际研究做法，不能凭题名补全",
                "kind": "source_basis_format"})
            if not strict:
                # source_basis is an optional audit aid, not a second citation
                # system. Keep the evaluation and let validated_paragraphs plus
                # the semantic reviewer check the actual text and evidence_ids.
                accepted.append({key: value for key, value in item.items() if key != "source_basis"})
            continue
        bases[item.get("dimension")] = basis
        accepted.append(item)
    return accepted, bases


def local_comparison_prose(text):
    # A handful of selected papers cannot establish field-wide prevalence or
    # consensus. Keep concrete analysis instead of unsupported survey claims.
    sentences = re.split(r"(?<=[。！？])", text)
    return "".join(sentence for sentence in sentences if not re.search(
        r"(?:现有|当前|整个|全|该)(?:的)?(?:[^。！？]{0,28})(?:领域|研究中|研究界)(?:[^。！？]{0,36})(?:较少|鲜有|首次|首个|共识|普遍认可)",
        sentence)).strip()


def evaluation_packet(state, claim):
    packet = point_packet(state, claim)
    packet["material_design"]["extracted_fields"] = state.nine_fields.model_dump()
    packet["material_design"]["submitted_text"] = state.raw_user_input[:20000]
    return packet


def input_fingerprint(packet):
    # A report may refine the display name; that is not a change of research input.
    packet = {**packet, "point": {k: v for k, v in packet["point"].items() if k != "name"}}
    return hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def substantive_rows(rows):
    """Reject empty/fragmentary or diagnostic-only dimensions, without padding."""
    result, seen = [], set()
    for row in rows:
        text = local_comparison_prose(row.get("text", ""))
        # Internal comparison counters are not reader-facing evaluation prose.
        text = re.sub(r"[^。！？.!?]*(?:字段覆盖|关键词交集)[^。！？.!?]*(?:[。！？.!?]|$)", "", text).strip()
        if row.get("dimension") not in DIMENSIONS or not row.get("evidence_ids"):
            continue
        if (len(text) < 28 or not re.search(r"[。！？.!?]$", text)
                or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text)
                or text in seen):
            continue
        seen.add(text)
        result.append({**row, "text": text})
    return result


def completed_dimensions(rows):
    return {row["dimension"] for row in substantive_rows(rows)}


def saved_evaluations(state, claim):
    """Return current, individually accepted appraisals, including partial sets."""
    cid = claim.claim_id
    status = state.output_json.get("reader_evaluation_completion", {}).get(cid, {})
    rows = state.output_json.get("reader_point_evaluations", {}).get(cid, [])
    if (status.get("status") in {"completed", "partial", "pending"}
            and state.output_json.get("reader_evaluation_inputs", {}).get(cid) == input_fingerprint(evaluation_packet(state, claim))):
        return substantive_rows(rows)
    return []


def reviewed_evaluations(state, claim):
    """Only current, fully reviewed stage prose can be reused by report drafting."""
    rows = saved_evaluations(state, claim)
    cid = claim.claim_id
    status = state.output_json.get("reader_evaluation_completion", {}).get(cid, {})
    if (status.get("status") == "completed"
            and completed_dimensions(rows) == set(DIMENSIONS)):
        return rows
    return []


async def evaluate_points(graph, state):
    # Runtime appraisals do not rewrite any published snapshot or report.
    if state.report_snapshot:
        return
    evaluations = state.output_json.setdefault("reader_point_evaluations", {})
    fingerprints = state.output_json.setdefault("reader_evaluation_inputs", {})
    completion = state.output_json.setdefault("reader_evaluation_completion", {})
    for claim in state.claims:
        cid = claim.claim_id
        packet = evaluation_packet(state, claim)
        fingerprint = input_fingerprint(packet)
        if reviewed_evaluations(state, claim):
            continue
        # Reuse accepted dimensions on retry; invalidate only changed inputs.
        rows = substantive_rows(evaluations.get(cid, [])) if fingerprints.get(cid) == fingerprint else []
        if rows:
            evaluations[cid] = rows
        else:
            evaluations.pop(cid, None)
        dims = completed_dimensions(rows)
        previous_feedback = completion.get(cid, {}).get("feedback", [])
        completion[cid] = {"status": "pending", "completed_dimensions": sorted(dims),
                           "missing_dimensions": [dim for dim in DIMENSIONS if dim not in dims]}
        fingerprints[cid] = fingerprint
        if not packet["literature"]:
            continue
        feedback = previous_feedback
        # Initial generation plus at most one targeted repair in this attempt.
        for attempt in range(2):
            missing = [dim for dim in DIMENSIONS if dim not in completed_dimensions(rows)]
            if not missing:
                break
            graph._model_unavailable = False
            prompt = INSTRUCTION + "\n只返回以下尚未通过的维度，不改写已有评价：" + json.dumps(missing, ensure_ascii=False)
            if attempt:
                prompt += ("\n这是本次唯一的定向补写。请按validation_feedback中的具体校核原因修改，补齐完整、有具体材料与文献联系的自然段。"
                    "逐句修正失败表述，保留能够成立的分析。可以按‘材料计划采用的具体做法；所引文献明确使用的具体做法；"
                    "本材料这一设计适合检验什么问题’组织。不要评价文献或材料没有做什么，"
                    "不声称现有文献尚未系统整合，不用无法评估、缺少数据的长段落替代方法或验证设计的评价。")
            # Do not feed rejected paragraphs back as writing examples. They
            # anchor repairs to the same unsupported survey/absence assertions.
            # Keep their full text in the audit, but send the actual source
            # packet and dimension-specific correction reasons to the writer.
            repair_feedback = [{key: item[key] for key in ("dimension", "reason") if key in item}
                               for item in feedback]
            if attempt or repair_feedback:
                prompt = ("返回 {evaluations:[{dimension,text,evidence_ids,kind}]}，kind为analysis。\n"
                    "只返回以下尚未通过的维度，不改写已有评价：" + json.dumps(missing, ensure_ascii=False) +
                    "\n这是定向修复，请根据原始材料和所引文献重新组织完整自然段。"
                    "每项约二百字，先说明材料明确提出的具体设计，再引用来源实际采用的方法，最后解释这一设计适合研究什么问题。"
                    "innovation评价具体问题设置或方法组织的贡献；frontier评价与相关研究问题、应用需求的联系；"
                    "advancedness评价技术机制及验证设计的特点。计划始终作为计划叙述。"
                    "以双方明确记载的做法和研究意义构成评价，不以缺项清单或对整个领域的排他性断言构成评价。"
                    "只写请求的维度，引用id只放evidence_ids；保持材料方法与文献方法的归属。")
            payload = await graph._request_model_json(state, stage="assessment", system_prompt=EVALUATION_WRITING_RULES,
                user_prompt=prompt + "\n" + SOURCE_BASIS_RULES + "\n" + json.dumps({**packet, "validation_feedback": repair_feedback}, ensure_ascii=False), max_tokens=4500)
            feedback = []
            raw, bases = checked_source_basis(payload.get("evaluations") if isinstance(payload, dict) else [], packet, feedback, strict=False)
            candidates = [row for row in validated_paragraphs(raw, packet, feedback, review_draft=True)
                          if row.get("dimension") in missing]
            if candidates:
                graph._model_unavailable = False
                review_packet = {**packet, "source_basis": bases}
                reviewed = await review_source_assertions(graph, state, review_packet, {"evaluations": candidates},
                    stage="assessment", audit_key="reader_evaluation_reviews", review_instruction=REVIEW_INSTRUCTION +
                    "\n核对source_basis与正文的一致性及来源用途：直接研究、方法借鉴、背景。逐字摘录不代表可跨对象推断性能。"
                    "保留成立的材料设计与方法联系，仅修正具体错误断言，不能将整段改为摘要缺项。"
                    "不同体系的文献只能说明方法联系；不能凭题名补出扣式电池、软包或双人标注等操作。")
                state.output_json.setdefault("reader_evaluation_basis", {}).setdefault(cid, {}).update({
                    row["dimension"]: bases[row["dimension"]] for row in reviewed["evaluations"] if row["dimension"] in bases})
                feedback.extend(state.output_json.get("reader_evaluation_reviews", {}).get(cid, {}).get("feedback", []))
                for row in reviewed["evaluations"]:
                    if not substantive_rows([row]):
                        feedback.append({"dimension": row.get("dimension"), "text": row.get("text"),
                            "reason": "段落不完整、缺少有效引用或只有套话，请写针对材料与来源的完整研究评价"})
                candidates = [row for row in substantive_rows(deduplicate_analysis(reviewed)["evaluations"])
                              if row["dimension"] in missing]
                rows = substantive_rows([*rows, *candidates])
            dims = completed_dimensions(rows)
            if rows:
                evaluations[cid] = rows
            completion[cid] = {"status": "completed" if dims == set(DIMENSIONS) else ("partial" if rows else "pending"),
                               "completed_dimensions": sorted(dims),
                               "missing_dimensions": [dim for dim in DIMENSIONS if dim not in dims],
                               "feedback": feedback or ([] if dims == set(DIMENSIONS) else [{"reason": "尚未返回全部必要评价维度的有效段落"}]),
                               "repair_attempted": bool(attempt)}
            graph._refresh_stage_progress(state, "assessment", completed=False)
            await graph._emit_stage_update(state)
