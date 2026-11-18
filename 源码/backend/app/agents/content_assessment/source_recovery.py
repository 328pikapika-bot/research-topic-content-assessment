"""Recover individual evidence gaps without discarding completed appraisals."""
from __future__ import annotations

import hashlib
import json
import re

from app.common.retrieval import LiteratureSearchClient, SearchRequest
from app.common.retrieval.execution import retrieval_io
from app.core.config import LITERATURE_SEARCH_API_URL
from .schemas import EvidenceRecord, QueryPlan


def source_input_key(state, claim):
    return hashlib.sha256(json.dumps({"claim": claim.model_dump(exclude={"display_title"}),
        "domain": state.nine_fields.research_direction,
        "years": state.output_json.get("year_range", [])}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_limited(state, claim):
    from .scholarly_report import selected_sources
    record = state.output_json.get("point_source_recovery", {}).get(claim.claim_id, {})
    return (record.get("status") == "limited" and record.get("input_key") == source_input_key(state, claim)
            and not selected_sources(state, claim))


async def recover_point_sources(graph, state):
    from .scholarly_report import selected_sources
    missing = [c for c in state.claims if not selected_sources(state, c) and not source_limited(state, c)]
    if not missing:
        return []
    if not LITERATURE_SEARCH_API_URL:
        return missing
    client = LiteratureSearchClient()
    audit = state.output_json.setdefault("point_source_recovery", {})
    for claim in missing:
        cid = claim.claim_id
        entry = {"input_key": source_input_key(state, claim), "status": "searching", "attempts": []}
        audit[cid] = entry
        domain = state.nine_fields.research_direction or state.assessment_profile.research_domain
        terms = " ".join(claim.searchable_terms[:5]) or claim.claim_fields.get("method") or claim.text[:100]
        terms = re.sub(r"\bSOP\b", "标准操作流程", terms, flags=re.I)
        queries = list(dict.fromkeys([f"{domain} {terms}".strip(),
            f"{domain} {claim.claim_fields.get('object', '')} {claim.claim_fields.get('method', '')}".strip()]))
        successful = False
        for query in queries[:2]:
            try:
                response = await retrieval_io(client.search, SearchRequest(query=query, paper_num=20,
                    year_range=tuple(state.output_json["year_range"]) if len(state.output_json.get("year_range", [])) == 2 else None,
                    user_scope=state.user_scope or None, source_agent="content_assessment",
                    context={"claim_id": cid, "purpose": "missing_source_recovery"}))
            except Exception as exc:
                entry["attempts"].append({"query": query, "success": False, "error_type": type(exc).__name__})
                continue
            successful |= response.success
            entry["attempts"].append({"query": query, "success": response.success,
                "returned": len(response.papers), "error": response.error})
            state.query_plans.append(QueryPlan(claim_id=cid, claim_ids=[cid], query=query,
                query_group="point_source_recovery", query_role="point_supplement",
                source_status="completed" if response.success else "unavailable", candidate_count=len(response.papers)))
            known = {(e.doi or e.paper_id or e.title.lower()): e for e in state.evidence_records}
            for paper in response.papers if response.success else []:
                identity = paper.doi or paper.paper_id or paper.title.lower()
                if not identity:
                    continue
                if identity in known:
                    existing = known[identity]
                    existing.claim_ids = list(dict.fromkeys([*existing.claim_ids, cid]))
                    continue
                evidence = EvidenceRecord(evidence_id="E-R-" + hashlib.sha256(identity.encode()).hexdigest()[:12],
                    source_type="literature", title=paper.title, abstract=paper.abstract, excerpt=paper.abstract[:1600],
                    authors=paper.authors, venue=paper.venue, year=paper.year or None, doi=paper.doi,
                    paper_id=paper.paper_id, url=paper.url, nstl_url=paper.nstl_url, source_url=paper.source_url,
                    evidence_level="B" if paper.abstract else "C", access_status="abstract_only" if paper.abstract else "unavailable",
                    retrieval_query=query, query_group="point_source_recovery", claim_ids=[cid])
                state.evidence_records.append(evidence)
                known[identity] = evidence
            await graph._emit_stage_update(state)
            if selected_sources(state, claim):
                entry["status"] = "recovered"
                break
        else:
            # An isolated lack of comparable literature is a scope limitation,
            # not proof of novelty and not a failed retrieval service. Require
            # a successful supplementary search and usable evidence elsewhere.
            entry["status"] = "limited" if successful and any(
                selected_sources(state, other) for other in state.claims if other.claim_id != cid
            ) else "unavailable"
        state.output_json["query_groups"] = [plan.model_dump() for plan in state.query_plans]
        stats = state.output_json.setdefault("retrieval_stats", {})
        stats["query_group_count"] = len(state.query_plans)
        stats["unique_evidence_count"] = sum(e.source_type == "literature" for e in state.evidence_records)
        await graph._emit_stage_update(state)
    return [c for c in missing if not selected_sources(state, c) and not source_limited(state, c)]


def limited_point_draft(state, claim):
    """State a material fact and its evidence boundary; make no novelty claim."""
    def row(text, dimension, kind="material"):
        return {"text": text, "dimension": dimension, "kind": kind, "evidence_ids": []}
    return {
        "progress": [row("本次检索与补充检索尚未取得与该要点直接可比的可读文献，以下仅说明提交材料及判断范围。",
                         "representatives")],
        "comparison": [row("材料提出：" + claim.text, "points")],
        "judgement": [row("该要点的外部比较依据不足，暂不能据此判断其创新性、前沿性或先进性。检索未命中不构成原创证明。",
                          "limits")],
        "evaluation": [row("本项内容保留为材料陈述；其实际效果及相对已有方法的差异需要可比较的文献或独立验证支持。",
                           "limits")],
    }
