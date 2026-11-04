from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile

from app.common.retrieval.fulltext import FulltextChunk, FulltextIntent, FulltextResponse, LiteratureFulltextClient
from app.common.retrieval.fusion import match_source, selected_chunks, selected_chunks_with_stats
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import EvidenceRecord as TopicEvidence, ThemeNode, ThemeTree
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import Claim, EvidenceRecord


def paragraph(**updates):
    text = "Solid state battery interfaces resist lithium dendrite growth. Validation requires cycling experiments and pressure controls."
    chunk = FulltextChunk(doc_id="library-1", chunk_id="library-1_3", text=text,
        title="Solid state battery interfaces", authors=["A. Li"], year=2025,
        section_path="Results", chunk_index=3, source="NSTL 全文库", access_status="authorized",
        locator_verified=True, source_hash=hashlib.sha256(text.encode()).hexdigest(), score=.8)
    return chunk.model_copy(update=updates)


def test_problem_search_accepts_new_documents_and_passes_filters(monkeypatch):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"success": True, "chunks": [paragraph().model_dump()]}
    def post(url, **kwargs):
        captured.update(kwargs)
        return Response()
    monkeypatch.setattr("app.common.retrieval.fulltext.requests.post", post)
    result = LiteratureFulltextClient(api_url="https://test/search", api_token="test").query_chunks(
        query="solid state battery", source_agent="content_assessment", intent_id="C1",
        year_range=[2020, 2026])
    assert result.success and result.chunks[0].doc_id == "library-1"
    assert captured["json"]["doc_ids"] == []
    assert captured["json"]["year_range"] == [2020, 2026]
    assert captured["headers"]["X-Source-Agent"] == "content_assessment"
    # Restricted mode must never admit unrequested documents.
    result = LiteratureFulltextClient(api_url="https://test/search").query_chunks(query="battery", doc_ids=["other"])
    assert result.chunks == []


def test_identity_conflict_and_ambiguity_do_not_merge():
    record = TopicEvidence(evidence_id="E1", paper_id="library-1", title="Unrelated paper", year=2025)
    assert match_source(paragraph(), [record])[1] == "conflicting_identity"
    matching = record.model_copy(update={"title": "Solid state battery interfaces"})
    assert match_source(paragraph(), [matching, matching.model_copy(update={"evidence_id": "E2"})])[1] == "ambiguous"


def test_title_match_requires_author_year_and_unique_identity():
    record = TopicEvidence(evidence_id="E1", paper_id="nstl-7", title="Solid state battery interfaces", year=2025, authors=["A. Li"])
    assert match_source(paragraph(), [record])[0] is record
    assert match_source(paragraph(authors=[]), [record])[0] is None


def test_unverified_sources_noise_and_duplicate_paragraphs_are_filtered():
    intent = FulltextIntent(intent_id="Q1", query="solid state battery interfaces")
    valid = paragraph()
    unknown = valid.model_copy(update={"title": "", "doc_id": "unknown"})
    noise = valid.model_copy(update={"score": .2})
    forged = valid.model_copy(update={"source_hash": "invented"})
    repeated = valid.model_copy(update={"chunk_id": "duplicate"})
    result = selected_chunks(intent, FulltextResponse(success=True, chunks=[valid, unknown, noise, forged, repeated]))
    assert len(result) == 1 and result[0].chunk_id == valid.chunk_id


def test_topic_fusion_retains_independent_source_and_reuses_matching_source():
    agent = TopicSelectionAgent()
    state = agent.create_initial_state("solid state battery", analysis_mode="fast")
    state.theme_tree = ThemeTree(nodes=[ThemeNode(theme_id="T1", label="battery")])
    intent = FulltextIntent(intent_id="Q1", query="solid state battery interfaces", theme_ids=["T1"])
    response = FulltextResponse(success=True, chunks=[paragraph()])
    asyncio.run(agent._fuse_problem_fulltext(state, [(intent, response)], None))
    asyncio.run(agent._fuse_problem_fulltext(state, [(intent, response)], None))
    assert len(state.evidence_pool) == len(state.evidence_chunks) == 1
    assert state.evidence_pool[0].source_kind == "fulltext_library"
    assert state.evidence_pool[0].url == ""  # Do not manufacture an NSTL link.
    assert state.evidence_chunks[0].theme_ids == ["T1"]


def test_assessment_views_never_borrow_other_claims_paragraphs():
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("solid state battery interfaces")
    one = Claim(claim_id="C1", text="solid state battery interfaces")
    two = Claim(claim_id="C2", text="clinical diagnosis")
    state.claims = [one, two]
    intent = FulltextIntent(intent_id="claim:C1", query=one.text, claim_ids=["C1"])
    graph._fuse_problem_fulltext(state, [(intent, FulltextResponse(success=True, chunks=[paragraph()]))])
    evidence = state.evidence_records[0]
    assert graph._claim_evidence_view(state, one, evidence).excerpt == paragraph().text
    assert graph._claim_evidence_view(state, two, evidence) is None
    assert evidence.char_start is None and graph._formal_evidence(evidence)
    assert EvidenceRecord.model_validate(evidence.model_dump()).evidence_level == "A"


def test_snapshot_abstracts_remain_abstract_evidence():
    state = ContentAssessmentGraph().create_initial_state("器官芯片")
    state.scenario_id = "organ_chip"
    # Tier rule applies equally to old stored snapshots.
    evidence = EvidenceRecord(evidence_id="E1", title="Paper", source_type="literature",
        abstract="An abstract", excerpt="An abstract", evidence_level="A",
        fulltext_available=True, anchor_verified=True, access_status="authorized",
        char_start=0, char_end=11, section_ref="Abstract")
    assert evidence.evidence_level == "B" and not evidence.usable_for_novelty


def test_service_outage_keeps_existing_evidence():
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("battery")
    state.evidence_records = [EvidenceRecord(evidence_id="E1", title="Paper", source_type="literature", abstract="battery", evidence_level="B")]
    graph._fuse_problem_fulltext(state, [(FulltextIntent(intent_id="C1", query="battery"), FulltextResponse(error="超时"))])
    assert len(state.evidence_records) == 1
    assert state.output_json["fulltext_limitations"] == ["超时"]


def test_empty_intents_and_returned_chunk_limit(monkeypatch):
    client = LiteratureFulltextClient(api_url="https://test/search")
    assert asyncio.run(client.search_intents([], source_agent="topic_selection")) == []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"chunks": [paragraph(chunk_id=str(i), text=paragraph().text + str(i)).model_dump() for i in range(8)]}
    monkeypatch.setattr("app.common.retrieval.fulltext.requests.post", lambda *a, **kw: Response())
    assert len(client.query_chunks(query="battery", top_k_per_doc=99).chunks) == 3


def test_generic_gateway_text_is_not_promoted_to_fulltext():
    from app.common.retrieval.adapters.content_assessment import ContentAssessmentSearchAdapter
    from app.common.retrieval.schemas import StandardPaper
    paper = StandardPaper(title="Paper", abstract="An abstract", raw={"text": "Abstract-like gateway text. " * 10})
    result = ContentAssessmentSearchAdapter().to_evidence_records([paper], query="test")[0]
    assert result.evidence_level == "B" and not result.fulltext_available
    assert ContentAssessmentGraph._paper_fulltext(paper) == ""


def test_abstract_sections_are_not_body_evidence():
    intent = FulltextIntent(intent_id="Q1", query="solid state battery interfaces")
    assert selected_chunks(intent, FulltextResponse(success=True, chunks=[paragraph(section_path="Abstract")])) == []


def test_cross_language_candidates_require_semantic_relevance_not_shared_characters():
    intent = FulltextIntent(intent_id="Q1", query="固态电池界面与锂枝晶生长")
    assert selected_chunks(intent, FulltextResponse(success=True, chunks=[paragraph(score=.53)]))
    assert not selected_chunks(intent, FulltextResponse(success=True, chunks=[paragraph(score=.4)]))


def test_selection_stats_explain_rejections_without_using_lexical_overlap_as_gate():
    intent = FulltextIntent(intent_id="Q1", query="器官芯片毒理评价")
    accepted, stats = selected_chunks_with_stats(intent, FulltextResponse(success=True, chunks=[
        paragraph(score=.53),
        paragraph(doc_id="low", chunk_id="low", score=.44),
        paragraph(doc_id="abstract", chunk_id="abstract", section_path="Abstract"),
    ]))
    assert len(accepted) == 1
    assert stats["lexical_mismatch"] == 3
    assert stats["below_relevance"] == 1
    assert stats["non_body"] == 1
    assert stats["accepted_chunks"] == 1


def test_fulltext_query_variants_execute_and_deduplicate(monkeypatch):
    calls = []
    client = LiteratureFulltextClient(api_url="https://test/search")
    def query_chunks(**kwargs):
        calls.append(kwargs)
        return FulltextResponse(success=True, chunks=[paragraph(query_variant=kwargs["query_variant"])],
            retrieval_stats={"raw_candidates": 1, "parsed_candidates": 1})
    monkeypatch.setattr(client, "query_chunks", query_chunks)
    intent = FulltextIntent(intent_id="Q1", query="生成式人工智能 科研评价",
        query_variants=["generative AI research evaluation"], languages=[])
    _, response = asyncio.run(client.search_intents([intent], source_agent="topic_selection"))[0]
    assert [item["query"] for item in calls] == ["生成式人工智能 科研评价", "generative AI research evaluation"]
    assert all(item["languages"] == [] for item in calls)
    assert len(response.chunks) == 1
    assert response.retrieval_stats["query_variant_count"] == 2
    assert response.retrieval_stats["variant_duplicates"] == 1


def test_reference_style_sections_are_rejected_even_without_reference_heading():
    intent = FulltextIntent(intent_id="Q1", query="solid state battery interfaces")
    refs = paragraph(section_path="3. Author A. Paper. 2022;1:2.",
        text="3. Author A. Paper. 2022;1:2.\n4. Author B. Other. 2021;2:3.")
    assert selected_chunks(intent, FulltextResponse(success=True, chunks=[refs])) == []


def test_claim_scope_and_counterevidence_survive_sanitizing_and_export():
    from html import unescape
    from app.agents.content_assessment.schemas import ClaimEvidenceLink
    from app.agents.content_assessment.report_composition import build_specs, compose, freeze_snapshot
    from app.services.export_service import build_docx, build_html, build_markdown
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("battery")
    state.claims = [Claim(claim_id="C1", text="solid state battery interfaces"), Claim(claim_id="C2", text="clinical diagnosis")]
    graph._fuse_problem_fulltext(state, [(FulltextIntent(intent_id="claim:C1", query=state.claims[0].text, claim_ids=["C1"]),
        FulltextResponse(success=True, chunks=[paragraph()]))])
    evidence_id = state.evidence_records[0].evidence_id
    state.claim_evidence_links = [ClaimEvidenceLink(claim_id="C1", evidence_id=evidence_id, relation="contradicts"),
        ClaimEvidenceLink(claim_id="C2", evidence_id=evidence_id, relation="supports")]
    graph._sanitize_claim_outputs(state)
    assert state.claim_evidence_links[0].relation == "contradicts"
    assert state.claim_evidence_links[0].evidence_chunk_ids
    assert state.claim_evidence_links[1].relation == "insufficient_evidence"
    assert not state.claim_evidence_links[1].evidence_chunk_ids
    state.report_plan.section_specs = build_specs(state)
    compose(state, state.report_plan.section_specs)
    freeze_snapshot(state)
    markdown, _ = build_markdown(state)
    html, _ = build_html(state)
    docx, _ = build_docx(state)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        word = archive.read("word/document.xml").decode()
    for content in (markdown, html, word):
        content = unescape(content.decode() if isinstance(content, bytes) else content)
        assert paragraph().text in content
        assert "存在反证" in content
    blocks = [b for section in state.report_plan.section_specs for b in section.blocks if "原文摘录" in b.text]
    assert blocks and all(b.claim_id == "C1" for b in blocks)
