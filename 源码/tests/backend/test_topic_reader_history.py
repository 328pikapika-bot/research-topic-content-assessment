"""Reader-facing changes must not alter evidence or user isolation."""
import asyncio
import copy
import io
import json
import re

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.presentation import reader_report, reader_text
from app.agents.topic_selection.schemas import ThemeNode, ThemeTree, EvidenceRecord, CrossLink, TopicSelectionState
from app.repositories.content_assessment_task_history import InMemoryTaskHistoryRepository, ContentAssessmentTaskHistoryRepository
from app.services import topic_selection_report
from app.services.topic_selection_report import build_topic_docx, build_topic_pdf, build_topic_html, build_topic_markdown, _bounded_export_state


def test_reader_normalizes_legacy_statistics_without_touching_citations():
    assert reader_text("文献 153 篇，全文覆盖 0 篇（0 段）。") == "文献 153 篇。"
    assert reader_text("全文覆盖：0 篇（0 段）。") == ""
    assert reader_text("全文覆盖 0 篇 0 段") == ""
    assert reader_text("摘要 153、全文 0") == "检索样本"
    assert reader_text("待补证据方向") == "探索方向"
    reference = {"kind": "reference_list", "items": [{"title": "全文覆盖 0 篇的测试", "url": "https://example.org"}]}
    assert reader_report(reference) == reference
    assert reader_report({"key": "references", "content": "全文覆盖 0 篇的论文"})["content"] == "全文覆盖 0 篇的论文"


def test_topic_exports_hide_pipeline_ids_and_conclusion_strength():
    state = TopicSelectionState(raw_user_input="研究问题")
    state.report_plan.template_version = "TopicReportV7"
    state.report_plan.sections = [{"key": "analysis", "title": "研究主题", "blocks": [
        {"kind": "paragraph", "text": "证据E-b9589cb75af4表明T1与T2存在方法关联。"},
        {"kind": "figure_ref", "key": "theme_network"},
    ]}]
    state.report_plan.chart_specs = [{"key": "theme_network", "figure_number": 1, "title": "主题关系", "chart_type": "network",
        "data": {"root": "T1", "display_root": "研究问题", "nodes": [
            {"theme_id": "T1", "label": "主题甲", "boundary": "E-b9589cb75af4支持"},
            {"theme_id": "T2", "label": "主题乙", "boundary": "研究边界", "parent_id": "T1"}],
            "edges": [{"source": "T1", "target": "T2", "relation": "证据E-b9589cb75af4支持"}]}}]
    state.report_plan.cover_metadata = {"conclusion_strength": "high"}
    original = state.model_dump()

    markdown = build_topic_markdown(state)[0].decode("utf-8")
    html = build_topic_html(state)[0].decode("utf-8")
    docx, _ = topic_selection_report._build_topic_docx_source(state)
    document = Document(io.BytesIO(docx))
    docx_text = "\n".join([paragraph.text for paragraph in document.paragraphs] +
                         [cell.text for table in document.tables for row in table.rows for cell in row.cells])

    reader_markdown = re.sub(r"data:image/[^)]+", "", markdown)
    html_reader_text = re.sub(r"<img[^>]*>", "", html, flags=re.S)
    for rendered in (reader_markdown, html_reader_text, docx_text):
        assert "E-b9589cb75af4" not in rendered
        assert "T1" not in rendered and "T2" not in rendered
        assert "结论强度" not in rendered
    assert "主题甲" in reader_markdown and "主题乙" in reader_markdown
    assert state.model_dump() == original


def test_all_exports_use_reader_snapshot_and_keep_original_decisions_and_chunks():
    agent = TopicSelectionAgent()
    state = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    state.report_plan.sections[0]["blocks"].append({"kind": "paragraph", "text": "全文覆盖：0 篇（0 段）。待补证据方向"})
    original = state.model_dump()
    adapted = _bounded_export_state(state)
    assert adapted.evidence_chunks == state.evidence_chunks
    assert adapted.quality_gate == state.quality_gate
    assert adapted.candidates == state.candidates
    assert "全文覆盖" not in json.dumps(adapted.report_plan.model_dump(), ensure_ascii=False)
    for builder in (build_topic_markdown, build_topic_html, build_topic_docx, build_topic_pdf):
        data, _ = builder(state)
        if builder == build_topic_docx:
            doc = Document(io.BytesIO(data))
            text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
        elif builder == build_topic_pdf:
            import fitz
            with fitz.open(stream=data, filetype="pdf") as pdf:
                text = "\n".join(page.get_text() for page in pdf)
                assert len(pdf) > 0
        else:
            text = data.decode("utf-8")
        assert "全文覆盖" not in text
        assert "待补证据" not in text
    actual = state.model_dump()
    # Export records real pagination; the published content and decisions stay frozen.
    actual["report_plan"]["page_metrics"] = original["report_plan"]["page_metrics"]
    for key in ("rendered_page_count", "body_page_count", "appendix_page_count"):
        actual["report_snapshot"][key] = original["report_snapshot"][key]
    assert actual == original


def test_theme_output_is_not_padded_and_relations_keep_original_node_mapping():
    agent = TopicSelectionAgent()
    state = agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")
    async def model(*args, **kwargs):
        return {"themes": [{"label": "交通污染与儿童呼吸症状"}, {"label": "交通污染与儿童呼吸症状"}, {"label": "绿地暴露与老年人健康"}],
                "relations": [{"source": "T1", "target": "T3", "relation": "比较两类环境暴露与年龄边界"}, {"source": "T2", "target": "T3", "relation": "不应错误映射"}]}
    agent._stage_model_json = model
    asyncio.run(agent._theme_decomposition(state))
    assert len(state.theme_tree.nodes) == 2
    relations = [edge for edge in state.theme_tree.edges if edge["source"] != "root"]
    assert relations == [{"source": "T1", "target": "T2", "relation": "比较两类环境暴露与年龄边界"}]


def test_shared_keywords_do_not_create_confirmed_crosslink():
    agent = TopicSelectionAgent()
    state = agent.create_initial_state("居民健康风险研究", analysis_mode="fast")
    state.theme_tree = ThemeTree(root="居民健康", nodes=[ThemeNode(theme_id="T1", label="交通污染", seed_terms=["健康"]), ThemeNode(theme_id="T2", label="绿地暴露", seed_terms=["健康"])])
    state.evidence_pool = [EvidenceRecord(evidence_id="E1", paper_id="p1", title="居民健康", abstract="共同术语健康")]
    async def empty(*args, **kwargs):
        return None
    agent._stage_model_json = empty
    asyncio.run(agent._crosslink_gap(state))
    assert not state.crosslinks
    assert len(state.gaps) == 2
    assert all(gap.validation_status == "coverage_limited" for gap in state.gaps)


def test_model_crosslinks_reject_unknown_evidence_and_do_not_certify_mechanisms():
    agent = TopicSelectionAgent()
    state = agent.create_initial_state("环境与健康", analysis_mode="fast")
    state.theme_tree = ThemeTree(root="环境与健康", nodes=[ThemeNode(theme_id="T1", label="交通污染"), ThemeNode(theme_id="T2", label="绿地暴露")])
    state.evidence_pool = [EvidenceRecord(evidence_id="E1", paper_id="p1", title="环境研究", abstract="已有研究")]
    async def model(*args, **kwargs):
        return {"gaps": [], "cross_opportunities": [{"source_theme_id": "T1", "target_theme_id": "T2", "description": "比较环境暴露的共同指标", "evidence_ids": ["E1", "invented"]}]}
    agent._stage_model_json = model
    asyncio.run(agent._crosslink_gap(state))
    assert state.crosslinks[0].support_evidence_ids == ["E1"]
    assert state.crosslinks[0].status == "candidate"
    assert any(state.crosslinks[0].crosslink_id in gap.crosslink_ids for gap in state.gaps)
    assert all(gap.validation_status != "validated" for gap in state.gaps)


def test_scoped_history_cleanup_is_idempotent_and_preserves_unended_tasks():
    repo = InMemoryTaskHistoryRepository()
    async def run():
        for scope in ("owner", "other"):
            for kind in ("智能选题", "创新性评估"):
                for status in ("completed", "failed", "cancelled", "superseded", "running", "queued", "awaiting_confirmation", "awaiting_review", "awaiting_correction", "unknown"):
                    await repo.create({"task_id": f"{scope}-{kind}-{status}", "user_scope": scope, "task_type": kind, "status": status})
        before = await repo.delete_history("owner", dry_run=True)
        assert before["eligible_count"] == 8 and before["skipped_count"] == 12
        assert before["deleted_count"] == 0
        result = await repo.delete_history("owner")
        assert result["deleted_count"] == 8
        assert await repo.get("owner-智能选题-completed", "owner") is None
        assert await repo.get("other-智能选题-completed", "other") is not None
        assert (await repo.delete_history("owner"))["deleted_count"] == 0
        assert (await repo.statistics("other"))["total"] == 20
    asyncio.run(run())


def test_cleanup_retains_terminal_record_if_executor_is_still_running():
    repo = InMemoryTaskHistoryRepository()
    async def run():
        await repo.create({"task_id": "finishing", "user_scope": "owner", "task_type": "智能选题", "status": "completed"})
        result = await repo.delete_history("owner", is_running=lambda key: True)
        assert result["deleted_count"] == 0 and result["skipped_count"] == 1
    asyncio.run(run())


def test_history_cleanup_includes_legacy_assessment_name_only_in_assessment_scope():
    repo = InMemoryTaskHistoryRepository()
    async def run():
        await repo.create({"task_id": "legacy", "user_scope": "owner", "task_type": "内容评估", "status": "completed"})
        await repo.create({"task_id": "unrelated", "user_scope": "owner", "task_type": "other", "status": "completed"})
        assert (await repo.delete_history("owner", task_type="智能选题"))["deleted_count"] == 0
        assert (await repo.delete_history("owner", task_type="创新性评估"))["deleted_count"] == 1
        assert await repo.get("unrelated", "owner") is not None
    asyncio.run(run())


def test_mongo_delete_rechecks_scope_status_and_version_atomically():
    class Collection:
        def find(self, query, fields):
            assert query["user_scope"] == "owner"
            return [{"_id": 1, "task_id": "done", "status": "completed", "updated_at": "version1"}]
        def find_one_and_delete(self, query):
            assert query["user_scope"] == "owner" and query["updated_at"] == "version1"
            assert "running" not in query["status"]["$in"]
            return None  # Simulate a concurrent resume or update.
    repo = ContentAssessmentTaskHistoryRepository()
    async def collection():
        return Collection()
    repo._get_collection = collection
    result = asyncio.run(repo.delete_history("owner"))
    assert result["deleted_count"] == 0 and result["skipped_reasons"] == {"state_changed": 1}


def test_cleanup_api_and_deleted_links_are_scope_safe(monkeypatch):
    repo = InMemoryTaskHistoryRepository()
    async def seed():
        await repo.create({"task_id": "done", "user_scope": "reader", "task_type": "智能选题", "status": "completed"})
        await repo.create({"task_id": "active", "user_scope": "reader", "task_type": "创新性评估", "status": "running"})
        await repo.create({"task_id": "other", "user_scope": "other", "task_type": "智能选题", "status": "completed"})
    asyncio.run(seed())
    monkeypatch.setattr(routes, "get_task_history_repo", lambda: repo)
    monkeypatch.setattr(routes, "is_valid_user_scope", lambda value: bool(value))
    app = FastAPI()
    app.include_router(routes.router)
    class Manager:
        def is_running(self, key):
            return key == "active"
    app.state.task_manager = app.state.topic_task_manager = Manager()
    with TestClient(app) as client:
        assert client.delete("/v1/tasks/history").status_code == 401
        headers = {"X-User-Scope": "reader"}
        assert client.get("/v1/tasks/history", headers=headers).json()["eligible_count"] == 1
        assert client.delete("/v1/content-assessment/tasks/active", headers=headers).status_code == 409
        assert client.delete("/v1/tasks/history", headers=headers).json()["deleted_count"] == 1
        assert client.delete("/v1/tasks/history", headers=headers).json()["deleted_count"] == 0
        for path in ("/v1/topic-selection/tasks/done", "/v1/topic-selection/tasks/done/export?format=md", "/v1/topic-selection/tasks/done/report/charts/theme_network"):
            assert client.get(path, headers=headers).status_code == 404
    assert asyncio.run(repo.get("other", "other")) is not None
