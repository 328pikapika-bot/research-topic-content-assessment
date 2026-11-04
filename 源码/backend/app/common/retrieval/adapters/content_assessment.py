"""Map common literature-search results into content-assessment evidence records."""

from __future__ import annotations

import hashlib
import re

from app.agents.content_assessment.schemas import Claim, EvidenceRecord, QueryPlan
from app.common.retrieval.schemas import SearchRequest, StandardPaper


class ContentAssessmentSearchAdapter:
    source_agent = "content_assessment"

    def to_search_request(
        self, claim: Claim, plan: QueryPlan, *, user_scope: str | None, assessment_title: str
    ) -> SearchRequest:
        return SearchRequest(
            query=plan.query,
            # Recall enough candidates for the downstream relevance pass;
            # this does not mean all returned records are cited in the report.
            paper_num=40,
            year_range=tuple(plan.year_range) if len(plan.year_range) == 2 else None,
            languages=plan.languages,
            user_scope=user_scope,
            source_agent=self.source_agent,
            context={"assessment_title": assessment_title, "claim_id": claim.claim_id, "claim_text": claim.text},
        )

    def to_evidence_records(self, papers: list[StandardPaper], *, query: str) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        for index, paper in enumerate(papers, start=1):
            raw = paper.raw if isinstance(paper.raw, dict) else {}
            fulltext_keys = ("fulltext", "full_text", "fullText")
            if str(raw.get("content_scope") or raw.get("text_scope") or "").lower() == "fulltext":
                fulltext_keys += ("content", "text", "body")
            fulltext = next(
                (
                    value.strip() for key in fulltext_keys
                    for value in [raw.get(key)]
                    if isinstance(value, str) and len(value.strip()) >= 80
                ),
                "",
            )
            excerpt = paper.abstract[:1200]
            start = None
            end = None
            if fulltext:
                paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\r\n", fulltext) if part.strip()]
                excerpt = (paragraphs[0] if paragraphs else fulltext)[:1200]
                start = fulltext.find(excerpt)
                end = start + len(excerpt) if start >= 0 else None
            access_status = str(raw.get("access_status") or raw.get("accessStatus") or "").lower()
            authorized = access_status in {"", "authorized"} and bool(fulltext)
            records.append(EvidenceRecord(
                evidence_id=f"E-L-{index:03d}",
                title=paper.title or f"检索文献 {index}",
                source_type="literature",
                year=paper.year or None,
                url=paper.url,
                nstl_url=paper.nstl_url,
                source_url=paper.source_url,
                doi=paper.doi,
                excerpt=excerpt,
                evidence_level="A" if authorized and start is not None and end else "B" if paper.abstract else "C",
                retrieval_query=query,
                usable_for_novelty=bool(authorized and start is not None and end),
                paper_id=paper.paper_id,
                paragraph_ref="p-0001" if fulltext else "",
                char_start=start,
                char_end=end,
                fulltext_available=bool(fulltext),
                anchor_verified=bool(authorized and start is not None and end),
                access_status="authorized" if authorized else "abstract_only" if paper.abstract else "unavailable",
                source_hash=hashlib.sha256(fulltext.encode("utf-8", errors="ignore")).hexdigest() if fulltext else "",
            ))
        return records
