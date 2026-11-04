from __future__ import annotations

import asyncio

import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import ExternalEvidence, SourceQuery
from app.agents.topic_selection.sources import SourceExecutionResult, ensure_prelock_external_access
from app.common.retrieval.schemas import SearchResponse, StandardPaper
from app.common.retrieval.query_builder import QueryBuilder
from app.common.retrieval.schemas import SearchRequest


class FakeNSTL:
    def search_multi_sync(self, requests, *, max_concurrency=3):
        return [SearchResponse(success=True, papers=[StandardPaper(paper_id="p1", title="公开证据", year=2025, abstract="future work")], search_trace={"query": request.query}) for request in requests]


class CapturingWeb:
    kind = "web"

    def __init__(self):
        self.queries: list[str] = []

    async def query(self, query: SourceQuery, *, user_scope: str, policy):
        self.queries.append(query.sanitized_query)
        return SourceExecutionResult(True, "web", external_evidence=[ExternalEvidence(evidence_id="WEB-1", source_kind="web", title="近期动态", url="https://example.test/a", fetched_at="2026-01-01", query_id=query.query_id)])


class DisabledCloud:
    kind = "cloud_model"

    async def query(self, *args, **kwargs):
        raise AssertionError("cloud adapter should not be called in this test")


def test_three_source_policy_audits_and_locks_evidence():
    web = CapturingWeb()
    agent = TopicSelectionAgent(retrieval_client=FakeNSTL(), web_adapter=web, cloud_adapter=DisabledCloud())
    state = agent.create_initial_state(
        "公开蛋白质结构研究，内部项目编号 P-SECRET-123456",
        uploaded_files=[{"file_id": "f1", "filename": "内部材料", "text": "绝密实验方案"}],
        enable_web=True,
        sensitivity_level="internal",
        candidate_count=3,
        analysis_mode="fast",
    )
    result = asyncio.run(agent.run(state))

    assert result.evidence_lock.locked is True
    assert result.evidence_lock.external_calls_blocked is True
    assert len(result.candidates) == 3
    assert result.freshness_snapshot.web_count == 1
    assert result.source_queries
    assert all("绝密实验方案" not in query for query in web.queries)
    assert all("P-SECRET-123456" not in query for query in web.queries)
    assert all(item.source_kind == "nstl" for item in result.evidence_pool)
    assert any(item.source_kind == "web" for item in result.external_evidence)


def test_external_access_is_rejected_after_lock():
    with pytest.raises(RuntimeError):
        ensure_prelock_external_access(True, "web")
    with pytest.raises(RuntimeError):
        ensure_prelock_external_access(True, "cloud_model")
    ensure_prelock_external_access(True, "nstl")


def test_unspecified_ranges_and_languages_remain_unfiltered():
    agent = TopicSelectionAgent()
    state = agent.create_initial_state("生成式人工智能与科研评价", year_range=[], language_preference="mixed")
    assert state.input_profile.time_range == []
    assert state.input_profile.time_range_source == "unspecified"
    assert state.input_profile.languages == []
    question = QueryBuilder().build_question(SearchRequest(query="generative AI"))
    assert "语种" not in question and "年份" not in question
    explicit = agent.create_initial_state("生成式人工智能", year_range=[2022, 2026], language_preference="zh")
    assert explicit.input_profile.time_range_source == "user"
    assert explicit.input_profile.languages == ["zh"]
