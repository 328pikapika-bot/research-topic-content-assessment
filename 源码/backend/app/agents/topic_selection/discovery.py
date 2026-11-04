"""Verify method quotations against the task's actual readable evidence.

This establishes bibliographic grounding, never experimental transferability.
All values remain snapshot serializable and optional for historical tasks.
"""
from __future__ import annotations

import re
from typing import Any


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).casefold()


def concise_theme(node) -> str:
    if len(node.label) <= 24:
        return node.label
    seeds = [term.strip() for term in node.seed_terms if 2 <= len(term.strip()) <= 18]
    return "／".join(seeds[:2])[:36] if seeds else node.label[:24] + "…"


def scoped_opportunity(text: str) -> str:
    """Keep absence claims within retrieval coverage, without changing quotes."""
    text = re.sub(r"缺乏([^。；\n]{1,160}研究)", r"本次检索样本尚不足以确认\1", str(text))
    return re.sub(r"(?:现有研究|现有文献|已有研究|相关研究)?(?:均未实现|尚无研究|没有研究|无人研究|从未研究|缺乏研究|尚未实现|未实现|未涉及)",
                  "本次检索样本尚不足以确认", text)


def sample_finding(text: str) -> str:
    """Scope generated synthesis; literal source quotations stay untouched."""
    text = scoped_opportunity(str(text).strip())
    if not text or text.startswith("本次可读检索样本"):
        return text
    return "本次可读检索样本中的观察（不代表领域整体）：" + text


def primary_method_sources(state, method: str, evidence_ids: list[str]) -> list[dict[str, Any]]:
    """Recover a literal primary-method passage when a model quote is invalid.

    Acronyms must all occur in one readable source. This handles publisher
    word wrapping such as 'Propa-gation' while retaining the actual wording.
    Secondary techniques alone cannot certify the named primary technique.
    """
    proposed = []
    acronyms = re.findall(r"\(([A-Z][A-Z0-9-]{1,10})\)", method)
    for record in state.evidence_pool:
        if record.evidence_id not in evidence_ids:
            continue
        passages = [("", record.abstract)]
        passages += [(c.chunk_id, c.text) for c in state.evidence_chunks
                     if c.evidence_id == record.evidence_id and (c.locator or c.section_path)]
        for chunk_id, text in passages:
            if not text:
                continue
            direct = re.search(re.escape(method), text, re.I) if method else None
            matches = [re.search(r"\b" + re.escape(term) + r"\b", text) for term in acronyms]
            if direct:
                start, end = direct.span()
            elif acronyms and all(matches):
                start = min(m.start() for m in matches)
                end = max(m.end() for m in matches)
            else:
                continue
            if end - start > 850:
                continue
            proposed.append({"evidence_id": record.evidence_id, "chunk_id": chunk_id,
                             "source_method": method, "quote": text[max(0, start - 80):end + 100]})
    return method_sources(state, method, proposed)


def discovery_context(state, limit: int = 20) -> dict[str, Any]:
    """Bound model context while covering themes and preserving exact quotes.

    The complete retrieved pool remains in state for subsequent verification.
    Compact evidence avoids serializing raw retrieval and audit structures.
    """
    records = sorted(state.evidence_pool, key=lambda e: (bool(e.abstract.strip()), len(e.abstract)), reverse=True)
    selected = []
    seen = set()
    for node in state.theme_tree.nodes:
        for record in [e for e in records if node.theme_id in e.theme_ids][:2]:
            if record.evidence_id not in seen and len(selected) < limit:
                selected.append(record); seen.add(record.evidence_id)
    for record in records:
        if record.evidence_id not in seen and len(selected) < limit:
            selected.append(record); seen.add(record.evidence_id)
    return {
        "evidence": [{"evidence_id": e.evidence_id, "title": e.title, "year": e.year,
                      "theme_ids": e.theme_ids, "discipline_tags": e.discipline_tags,
                      "abstract": e.abstract[:1800]} for e in selected],
        "fulltext_chunks": [{"evidence_id": c.evidence_id, "chunk_id": c.chunk_id,
                             "locator": c.locator or c.section_path, "text": c.text[:1200]}
                            for c in state.evidence_chunks if c.evidence_id in seen][:20],
        "themes": [{"theme_id": n.theme_id, "label": n.label, "boundary": n.boundary[:350],
                    "discipline_tags": n.discipline_tags} for n in state.theme_tree.nodes[:12]],
    }


def method_sources(state, method: str, proposed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = {item.evidence_id: item for item in state.evidence_pool}
    verified = []
    for proposal in proposed[:8]:
        if not isinstance(proposal, dict):
            continue
        eid = str(proposal.get("evidence_id") or "")
        record = evidence.get(eid)
        quote = str(proposal.get("quote") or "").strip()
        source_method = str(proposal.get("source_method") or method).strip()
        if not record or len(quote) < 12 or not source_method:
            continue
        candidates = [("abstract", "", record.abstract, {"section": "摘要"})] if record.abstract.strip() else []
        for chunk in state.evidence_chunks:
            if chunk.evidence_id != eid or not chunk.text.strip():
                continue
            locator = chunk.locator or chunk.section_path
            if not locator and chunk.char_start is not None and chunk.char_end is not None and chunk.char_end > chunk.char_start:
                locator = f"正文字符 {chunk.char_start}–{chunk.char_end}"
            if locator:
                candidates.append(("fulltext", chunk.chunk_id, chunk.text, {"section": locator}))
        requested_chunk = proposal.get("chunk_id")
        for scope, chunk_id, text, locator in candidates:
            if requested_chunk and chunk_id != requested_chunk:
                continue
            if normalized(quote) not in normalized(text):
                continue
            name_verified = normalized(source_method) in normalized(text)
            if not name_verified and not re.search(r'\b(?:method|technique|analysis|model|control|measurement|learning|culture|microfluidic|perfusion|voltage|spectroscopy|transport|algorithm|ranking|bioreactor)\b|采用|分析|培养|控制|测量|检索|算法|模型|方法|技术|灌流|微流控|电压|阻抗', quote, re.I):
                continue
            # A named method can be introduced earlier in the same abstract or
            # located passage. Retain both exact excerpts rather than rejecting
            # a valid procedure quote merely because it omits the method name.
            name_position = text.casefold().find(source_method.casefold())
            name_quote = text[max(0, name_position - 40):name_position + len(source_method) + 80] if name_position >= 0 else quote
            verified.append({"evidence_id": eid, "chunk_id": chunk_id, "quote": quote[:1000],
                             "source_method": source_method if name_verified else quote[:1000], "evidence_scope": scope,
                             "proposed_method_name": source_method, "method_name_verified": name_verified,
                             "source_basis": "literal_method_name" if name_verified else "quoted_method_description",
                             "method_name_quote": name_quote, "locator": locator, "verified": True})
            break
    unique = {(item["evidence_id"], item["chunk_id"], item["quote"]): item for item in verified}
    return list(unique.values())


def opportunity_theme_ids(state, proposal: dict[str, Any]) -> tuple[str, str]:
    """An opportunity concerns a question, not necessarily a pair of themes."""
    known = {node.theme_id for node in state.theme_tree.nodes}
    related = proposal.get("theme_ids") or []
    left = str(proposal.get("source_theme_id") or (related[0] if related else "") or (next(iter(known)) if len(known) == 1 else ""))
    right = str(proposal.get("target_theme_id") or (related[1] if len(related) > 1 else left))
    return (left, right) if left in known and right in known else ("", "")


def ground_discovery(state, proposals: list[dict[str, Any]]) -> None:
    """Finalize all relations, including deterministic/model fallback relations."""
    evidence = {item.evidence_id: item for item in state.evidence_pool}
    themes = {item.theme_id: item for item in state.theme_tree.nodes}
    from .schemas import DisciplineParticipant
    by_pair: dict[frozenset, list[dict[str, Any]]] = {}
    for proposal in proposals:
        if isinstance(proposal, dict):
            pair = frozenset((str(proposal.get("source_theme_id")), str(proposal.get("target_theme_id"))))
            by_pair.setdefault(pair, []).append(proposal)
    for node in state.theme_tree.nodes:
        node.discipline_sources = []
        for tag in node.discipline_tags:
            papers = [item for item in state.evidence_pool if tag in item.discipline_tags and node.theme_id in item.theme_ids]
            node.discipline_sources.append({"discipline": tag, "source": "retrieval_label" if papers else "model_inference",
                                           "evidence_ids": [item.evidence_id for item in papers],
                                           "reason": "检索服务返回的学科标签" if papers else f"根据主题边界推断：{node.boundary or node.label}"})
    for link in state.crosslinks:
        link.description = scoped_opportunity(link.description)
        link.research_opportunity = scoped_opportunity(link.research_opportunity)
        link.relation_basis = scoped_opportunity(link.relation_basis)
        matching = [p for p in proposals if isinstance(p, dict) and p.get("crosslink_id") == link.crosslink_id]
        if not matching:
            matching = [p for p in by_pair.get(frozenset((link.source_theme_id, link.target_theme_id)), []) if not p.get("crosslink_id")]
        exact = [p for p in matching if p.get("transferable_method") == link.transferable_method and link.transferable_method]
        choices = exact or matching
        item = max(choices, key=lambda p: len(method_sources(state, str(p.get("source_method") or p.get("transferable_method") or ""), p.get("method_evidence") or [])), default={})
        left, right = themes.get(link.source_theme_id), themes.get(link.target_theme_id)
        for key in ("core_discipline", "expansion_discipline", "transferable_method", "source_method", "validation_conditions"):
            value = str(item.get(key) or "").strip()
            if value:
                setattr(link, key, value[:1000])
        if not link.core_discipline and left and left.discipline_tags:
            link.core_discipline = left.discipline_tags[0]
        if not link.expansion_discipline and right and not item.get("participating_disciplines"):
            link.expansion_discipline = next((tag for tag in right.discipline_tags if tag != link.core_discipline), "")
        # A deterministic exact method match can be grounded without a model.
        draft = list(item.get("method_evidence") or [])
        if not draft and link.transferable_method:
            for eid in link.support_evidence_ids:
                record = evidence.get(eid)
                if record and normalized(link.transferable_method) in normalized(record.abstract):
                    draft.append({"evidence_id": eid, "quote": record.abstract[:1000], "source_method": link.transferable_method})
        link.method_evidence = method_sources(state, link.source_method or link.transferable_method, draft)
        primary = link.source_method or link.transferable_method
        is_primary = lambda source: normalized(source.get("proposed_method_name", "")) == normalized(primary)
        if primary and not any(is_primary(source) for source in link.method_evidence):
            recovered = primary_method_sources(state, primary, link.support_evidence_ids)
            link.method_evidence = recovered + link.method_evidence if recovered else []
        # Always show the primary technique's literal source before supporting
        # instrumentation or other related techniques.
        link.method_evidence.sort(key=lambda source: not is_primary(source))
        link.support_evidence_ids = list(dict.fromkeys([*link.support_evidence_ids, *[item["evidence_id"] for item in link.method_evidence]]))
        link.discipline_sources = []
        for role, tag in (("core", link.core_discipline), ("expansion", link.expansion_discipline)):
            ids = [eid for eid in link.support_evidence_ids if eid in evidence and tag and tag in evidence[eid].discipline_tags]
            link.discipline_sources.append({"role": role, "discipline": tag,
                                           "source": "retrieval_label" if ids else "model_inference",
                                           "evidence_ids": ids, "reason": str(item.get(f"{role}_discipline_reason") or link.relation_basis)[:1000]})
        proposed = item.get("participating_disciplines") or [p.model_dump() for p in link.participating_disciplines]
        legacy = [{"discipline": link.core_discipline, "role": "core", "reason": item.get("core_discipline_reason") or link.relation_basis},
                  {"discipline": link.expansion_discipline, "role": "expansion", "reason": item.get("expansion_discipline_reason") or link.relation_basis}]
        participants, seen = [], set()
        for participant in (proposed if isinstance(proposed, list) and proposed else legacy):
            if not isinstance(participant, dict):
                continue
            name = str(participant.get("discipline") or "").strip()[:100]
            if not name or normalized(name) in seen:
                continue
            role = "core" if participant.get("role") == "core" or name == link.core_discipline else "expansion"
            ids = [str(eid) for eid in participant.get("evidence_ids", []) if str(eid) in link.support_evidence_ids and str(eid) in evidence]
            tagged = [eid for eid in link.support_evidence_ids if eid in evidence and name in evidence[eid].discipline_tags]
            reason = str(participant.get("reason") or "").strip()[:1000]
            if not tagged and not reason:
                continue
            seen.add(normalized(name))
            participants.append(DisciplineParticipant(discipline=name, role=role, source="retrieval_label" if tagged else "model_inference",
                reason="检索服务返回的学科标签" if tagged else reason, evidence_ids=list(dict.fromkeys([*tagged, *ids]))))
        link.participating_disciplines = participants
        link.discipline_sources = [p.model_dump() for p in participants]
        different = len({normalized(p.discipline) for p in participants if p.evidence_ids}) > 1
        link.association_kind = "interdisciplinary" if different else "theme_association" if link.core_discipline else "unverified"
        generic = normalized(link.transferable_method) in {"人工智能", "机器学习", "深度学习", "统计分析", "数学方法", "优化方法", "ai"}
        grounded = bool(link.method_evidence and link.transferable_method and not generic and link.relation_basis)
        link.status = "validated" if grounded else "candidate"
        if not link.validation_conditions:
            link.validation_conditions = "以当前研究对象设置独立对照，统一数据、指标和验证条件，检验方法适用性与失效边界。"
        if not grounded:
            restriction = "当前仅为待验证线索，缺少可核验的具体方法依据。"
            if restriction not in link.uncertainty:
                link.uncertainty = (link.uncertainty + " " + restriction).strip()
        else:
            link.uncertainty = (link.uncertainty + " 方法来源有文献依据，迁移效果仍须在当前对象上验证。").strip()

    # Keep the strongest recorded expansion disciplines, never pad a target count.
    strength = {}
    for link in state.crosslinks:
        for p in link.participating_disciplines:
            if p.role == "expansion":
                key = normalized(p.discipline)
                rank = (p.source == "retrieval_label", len(p.evidence_ids))
                strength[key] = max(strength.get(key, (False, 0)), rank)
    allowed = set(sorted(strength, key=lambda key: strength[key], reverse=True)[:6])
    for link in state.crosslinks:
        link.participating_disciplines = [p for p in link.participating_disciplines if p.role == "core" or normalized(p.discipline) in allowed]
        link.expansion_discipline = next((p.discipline for p in link.participating_disciplines if p.role == "expansion"), "")
        link.discipline_sources = [p.model_dump() for p in link.participating_disciplines]
        if len({normalized(p.discipline) for p in link.participating_disciplines if p.evidence_ids}) < 2:
            link.association_kind = "theme_association" if link.core_discipline else "unverified"


def evolution_period(item: dict[str, Any]) -> str:
    """Read legacy chronology without turning recent findings into forecasts."""
    period = str(item.get("period") or "").strip().lower()
    if period in {"past", "present", "future"}:
        return period
    if period:  # Invalid explicit values should be repaired by the producer.
        return ""
    text = str(item.get("label") or item.get("finding") or "").strip()
    if re.match(r"^(?:未来|后续|下一步|建议|拟开展|future|next\b)", text, re.I):
        return "future"
    if re.match(r"^(?:早期|过去|传统|初期|中期|此前|early\b|past\b)", text, re.I):
        return "past"
    return "present"


def method_evolution(state, proposed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {item.evidence_id for item in state.evidence_pool if item.abstract.strip()}
    ids.update(chunk.evidence_id for chunk in state.evidence_chunks if chunk.text.strip() and (chunk.locator or chunk.section_path))
    result = []
    labels = {"past": "过去怎么做", "present": "现在采用什么", "future": "未来待验证方向"}
    for item in proposed:
        if not isinstance(item, dict):
            continue
        refs = [eid for eid in item.get("evidence_ids", []) if eid in ids]
        period = evolution_period(item)
        if period not in labels or not refs or not str(item.get("finding") or "").strip():
            continue
        result.append({"period": period, "label": labels[period], "finding": sample_finding(item["finding"]), "evidence_ids": refs,
                       "is_recommendation": period == "future"})
    return result


def selected_opportunities(state):
    """One shared, bounded selection for charts, cards and new reports."""
    rows = sorted(state.crosslinks, key=lambda link: (link.status == 'validated', len(link.support_evidence_ids)), reverse=True)
    selected, seen = [], set()
    for link in rows:
        # A grounded relation can be persisted before the model has supplied
        # its prose opportunity label (for example, after a recoverable
        # response truncation).  Keep that relation available to the chart so
        # a valid, evidence-backed network is not rendered empty.  The chart
        # presentation supplies a scoped fallback label in that case; an
        # ungrounded relation is still excluded here.
        if not link.transferable_method or not (link.research_opportunity or link.relation_basis) or not any(source.get('verified') for source in link.method_evidence):
            continue
        key = normalized(link.transferable_method + '|' + (link.research_opportunity or link.relation_basis))
        if key in seen:
            continue
        seen.add(key)
        selected.append(link)
        if len(selected) == 3:
            break
    return selected
