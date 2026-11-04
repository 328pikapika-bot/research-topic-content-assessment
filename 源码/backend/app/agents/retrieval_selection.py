"""Select representative records after source fusion, before downstream analysis."""
from __future__ import annotations

from collections import Counter
import math

from app.agents.content_assessment.scholarly_report import tokens


def grouped_selection(records, targets, scopes, limit, *, require_relevance=False):
    """Return per-target IDs. Query association is a candidate scope, not evidence.

    Metadata-only records remain eligible: their titles are scored without
    pretending that they contain method or result evidence.
    """
    corpus = {item.evidence_id: tokens(item.title + ' ' + (item.abstract or '') + ' ' + (item.excerpt or '')) for item in records}
    frequency = Counter(term for terms in corpus.values() for term in terms)
    count = max(1, len(records))
    result = {}
    for target_id, query in targets.items():
        vocabulary = tokens(query)
        ranked = []
        for index, item in enumerate(records):
            if target_id not in scopes.get(item.evidence_id, []):
                continue
            overlap = vocabulary & corpus[item.evidence_id]
            # A common label or query tag alone cannot associate a paper.
            if require_relevance and not overlap:
                continue
            title_terms = tokens(item.title)
            rank = sum((4 if term in title_terms else 1) * (1 + math.log((count + 1) / (frequency[term] + 1))) for term in overlap)
            ranked.append((rank, float(getattr(item, 'retrieval_score', 0) or 0), -index, item.evidence_id))
        ranked.sort(reverse=True)
        result[target_id] = [row[3] for row in ranked[:limit]]
    return result


ASSESSMENT_POINT_TARGET = 20
ASSESSMENT_POINT_LIMIT = 30


def assessment_literature_selection(state, limit=ASSESSMENT_POINT_LIMIT):
    records = [item for item in state.evidence_records if item.source_type == 'literature']
    targets = {}
    for claim in state.claims:
        # Only genuinely point-scoped translated queries extend its vocabulary.
        queries = [p.query for p in state.query_plans if set(p.claim_ids or [p.claim_id]) == {claim.claim_id}]
        targets[claim.claim_id] = ' '.join([claim.text, *claim.searchable_terms, *queries])
    return grouped_selection(records, targets, {item.evidence_id: item.claim_ids for item in records}, limit, require_relevance=True)


def limit_assessment_literature(state):
    chosen = assessment_literature_selection(state)
    membership = {eid: [cid for cid, ids in chosen.items() if eid in ids] for eid in {eid for ids in chosen.values() for eid in ids}}
    retained = []
    for item in state.evidence_records:
        if item.source_type != 'literature':
            retained.append(item)
        elif item.evidence_id in membership:
            item.claim_ids = membership[item.evidence_id]
            item.indicator_ids = [iid for iid in item.indicator_ids if any(i.indicator_id == iid and (i.source_claim_id in item.claim_ids or set(i.source_claim_ids) & set(item.claim_ids)) for i in state.indicators)]
            retained.append(item)
    kept = {item.evidence_id for item in retained}
    state.evidence_records = retained
    state.claim_evidence_links = [link for link in state.claim_evidence_links
        if link.evidence_id in kept and (link.evidence_id not in membership or link.claim_id in membership[link.evidence_id])]
    state.evidence_chunks = [chunk for chunk in state.evidence_chunks if chunk.evidence_id in kept]
    for chunk in state.evidence_chunks:
        if chunk.evidence_id in membership:
            chunk.claim_ids = [cid for cid in chunk.claim_ids if cid in membership[chunk.evidence_id]]
            chunk.supports_claim_ids = [cid for cid in chunk.supports_claim_ids if cid in membership[chunk.evidence_id]]
    for plan in state.query_plans:
        plan.selected_count = sum(item.source_type == 'literature' and bool(set(item.claim_ids) & set(plan.claim_ids or [plan.claim_id])) and (item.retrieval_query == plan.query or item.query_group == plan.query_group) for item in retained)
    stats = state.output_json.setdefault('retrieval_stats', {})
    stats.update(unique_evidence_count=len(membership), per_point_counts={cid: len(ids) for cid, ids in chosen.items()},
        per_point_target=ASSESSMENT_POINT_TARGET, per_point_limit=ASSESSMENT_POINT_LIMIT)
    # Retrieval pool and report citations serve different views. Never replace
    # the pool with the few sources finally selected for report arguments.
    state.output_json['retrieval_source_order'] = chosen
    state.output_json['query_groups'] = [plan.model_dump() for plan in state.query_plans]


def limit_topic_literature(state):
    targets = {theme.theme_id: ' '.join([theme.label, theme.boundary, *theme.seed_terms]) for theme in state.theme_tree.nodes}
    chosen = grouped_selection(state.evidence_pool, targets, {item.evidence_id: item.theme_ids for item in state.evidence_pool}, 25)
    membership = {eid: [tid for tid, ids in chosen.items() if eid in ids] for eid in {eid for ids in chosen.values() for eid in ids}}
    state.evidence_pool = [item for item in state.evidence_pool if item.evidence_id in membership]
    for item in state.evidence_pool:
        item.theme_ids = membership[item.evidence_id]
    # Unassociated document chunks are input material, not literature overflow.
    state.evidence_chunks = [chunk for chunk in state.evidence_chunks if not chunk.evidence_id or chunk.evidence_id in membership]
