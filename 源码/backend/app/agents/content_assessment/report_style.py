"""Targeted editorial review of newly drafted point analysis."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from difflib import SequenceMatcher

from .scholarly_report import WRITING_RULES, point_packet, review_source_assertions, validated_paragraphs


MECHANICAL_START = re.compile(r"^((?:本|该)(?:指南|项目|论文|材料|研究|方案))(?:草案)?(?:中|的|拟|将|通过|提出|要求|规定|强调|旨在|针对|围绕)?")
GENERIC = re.compile(r"^(?:(?:本|该)(?:指南|项目|论文|材料|研究).{0,18})?(?:具有重要意义|提供重要参考|有助于推动|值得进一步关注|为后续研究提供参考|具有较高的创新性)")
IMMUTABLE_DIMENSIONS = {"innovation", "frontier", "advancedness"}


def style_targets(drafts):
    """Return precise row locations; never edit the stage's reviewed judgments."""
    eligible = []
    for cid, sections in drafts.items():
        for key in ("progress", "comparison", "judgement", "evaluation"):
            for index, row in enumerate(sections.get(key, [])):
                if key == "judgement" and row.get("dimension") in IMMUTABLE_DIMENSIONS:
                    continue
                eligible.append((cid, key, index, row))
    openings = Counter(match.group(1) for _, _, _, row in eligible
        if (match := MECHANICAL_START.match(row.get("text", "").strip())))
    targets = set()
    for pos, (cid, key, index, row) in enumerate(eligible):
        text = row.get("text", "").strip()
        match = MECHANICAL_START.match(text)
        if GENERIC.search(text):
            targets.add((cid, key, index))
        if pos and eligible[pos - 1][0] == cid:
            previous = eligible[pos - 1]
            left = re.sub(r"[\s，。；：、《》]", "", previous[3].get("text", ""))
            right = re.sub(r"[\s，。；：、《》]", "", text)
            if min(len(left), len(right)) >= 35 and SequenceMatcher(None, left, right, autojunk=False).ratio() >= .83:
                targets.add((cid, key, index))
                targets.add((previous[0], previous[1], previous[2]))
        if not match:
            continue
        normalized = match.group(1)
        if openings[normalized] >= 3 and openings[normalized] * 3 >= len(eligible):
            targets.add((cid, key, index))
        neighborhood = eligible[max(0, pos - 2):pos + 3]
        repeated = sum(bool(MECHANICAL_START.match(other.get("text", "").strip()))
                       and MECHANICAL_START.match(other.get("text", "").strip()).group(1) == normalized
                       for _, _, _, other in neighborhood)
        if repeated >= 2:
            targets.add((cid, key, index))
    return targets


def preserves_qualifiers(original, revised):
    numbers = lambda value: re.findall(r"\d+(?:\.\d+)?", value)
    markers = ("拟", "计划", "目标", "实测", "尚未", "不能", "仅")
    return numbers(original) == numbers(revised) and all(
        (marker in original) == (marker in revised) for marker in markers)


async def polish_point_analysis(graph, state):
    if state.report_snapshot:
        return
    drafts = state.output_json.get("scholarly_drafts", {})
    targets = style_targets(drafts)
    checks = state.output_json.setdefault("scholarly_style_checks", {})
    for claim in state.claims:
        cid = claim.claim_id
        packet = point_packet(state, claim)
        pending = []
        for key, index in sorted((key, index) for c, key, index in targets if c == cid):
            original = drafts[cid][key][index]
            fingerprint = hashlib.sha256(json.dumps([packet, original], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            check_key = f"{cid}:{key}:{index}"
            previous = checks.get(check_key, {})
            if previous.get("fingerprint") == fingerprint and (previous.get("status") == "completed" or previous.get("attempts", 0) >= 2):
                continue
            pending.append((key, index, original, fingerprint, previous))
        for offset in range(0, len(pending), 6):
            batch = pending[offset:offset + 6]
            source_rows = {f"{key}:{index}": {"original": row,
                "neighboring_text": [r.get("text", "") for r in drafts[cid][key][max(0, index-1):index+2]]}
                for key, index, row, _, _ in batch}
            prompt = ("仅润色下列已完成来源校核的评估段落。返回{paragraphs:{段落键:{text,evidence_ids,kind,dimension}}}，"
                      "段落键逐一沿用source_rows中的键。保持科研判断、事实、条件和引用ID不变，不新增、删除或更换引用。"
                      "直接从所论研究对象、方法、结果或比较关系切入，避免公式化的材料类型主语；"
                      "减少套话和相邻段落的换词重复，但不得通过缩短丢失信息。"
                      "只调整表达，不能添加source_material未记载的科研事实。\n"
                      + json.dumps({"source_material": packet, "source_rows": source_rows}, ensure_ascii=False))
            response = await graph._request_model_json(state, stage="report", system_prompt=WRITING_RULES,
                                                       user_prompt=prompt, max_tokens=2400)
            proposed_rows = response.get("paragraphs", {}) if isinstance(response, dict) else {}
            if len(batch) == 1 and isinstance(response, dict) and isinstance(response.get("paragraph"), dict):
                proposed_rows = {f"{batch[0][0]}:{batch[0][1]}": response["paragraph"]}
            proposals = {}
            for key, index, original, _, _ in batch:
                ref = f"{key}:{index}"
                proposed = proposed_rows.get(ref) if isinstance(proposed_rows, dict) else None
                if not isinstance(proposed, dict):
                    continue
                clean = validated_paragraphs([proposed], packet, review_draft=True)
                if clean and clean[0]["text"] != original["text"] and \
                        preserves_qualifiers(original["text"], clean[0]["text"]) and \
                        not GENERIC.search(clean[0]["text"]) and not MECHANICAL_START.match(clean[0]["text"]) and \
                        clean[0].get("evidence_ids", []) == original.get("evidence_ids", []) and \
                        clean[0].get("dimension", "") == original.get("dimension", "") and \
                        clean[0].get("kind", "") == original.get("kind", ""):
                    proposals[ref] = clean
            reviewed = await review_source_assertions(graph, state, packet, proposals,
                audit_key=f"scholarly_style_source_reviews_{offset}", repair_budget=0,
                review_instruction="这是文风润色。保留原判断、适用条件、限定词、数字和引用；"
                    "如润色改变事实或结论，请恢复原文。") if proposals else {}
            for key, index, original, fingerprint, previous in batch:
                ref = f"{key}:{index}"
                accepted = reviewed.get(ref, [])
                reason = "润色未通过来源复核，保留原文" if ref in proposals else "润色改变事实限定或未返回有效段落，保留原文"
                if accepted and accepted[0].get("evidence_ids", []) == original.get("evidence_ids", []) and \
                        accepted[0].get("dimension", "") == original.get("dimension", "") and \
                        preserves_qualifiers(original["text"], accepted[0]["text"]):
                    drafts[cid][key][index] = accepted[0]
                    reason = "已通过来源复核"
                check_key = f"{cid}:{key}:{index}"
                checks[check_key] = {"fingerprint": fingerprint,
                    "status": "completed" if reason == "已通过来源复核" else "retained", "reason": reason,
                    "attempts": previous.get("attempts", 0) + 1 if previous.get("fingerprint") == fingerprint else 1}
            await graph._emit_stage_update(state)
