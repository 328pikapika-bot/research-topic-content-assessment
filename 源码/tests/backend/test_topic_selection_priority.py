from __future__ import annotations

import asyncio
import copy
import html as html_tools
import io
import re
import zipfile

import pytest

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.common.retrieval.fulltext import FulltextResponse, FulltextChunk, LiteratureFulltextClient
from app.common.retrieval.schemas import SearchResponse, StandardPaper
from app.agents.topic_selection.schemas import CandidateEvidenceLink, TopicEvidenceChunk, EvidenceRecord
from app.services.topic_selection_report import build_topic_docx, build_topic_html, build_topic_markdown, build_topic_pdf, _chart_has_data, _chart_limitation, _render_chart_png


class SearchWithFulltext:
    def search_multi_sync(self, requests, *, max_concurrency=3):
        return [SearchResponse(success=True, papers=[StandardPaper(
            paper_id=f"paper-{index}", title=f"城市健康证据 {index}", year=2024,
            abstract="摘要证据", has_fulltext=True, retrieval_score=0.9,
        )], search_trace={"query": request.query}) for index, request in enumerate(requests, start=1)]


class FulltextFixture:
    def query_chunks(self, *, query: str, doc_ids: list[str], top_k_per_doc: int = 3):
        return FulltextResponse(success=True, chunks=[
            FulltextChunk(doc_id=doc_id, chunk_id=f"{doc_id}-1", text=f"全文片段 {doc_id}", chunk_index=1, section_path="结果/健康风险", char_start=10, char_end=22, score=0.9)
            for doc_id in doc_ids for _ in range(2)
        ])


def test_fulltext_chunks_are_capped_deduplicated_and_promote_only_anchored_evidence():
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    assert result.evidence_chunks
    assert all(sum(chunk.doc_id == doc_id for chunk in result.evidence_chunks) <= 3 for doc_id in {chunk.doc_id for chunk in result.evidence_chunks})
    assert all(item.source_type == "nstl_fulltext" and item.anchor_verified for item in result.evidence_pool)
    assert all(link.evidence_scope == "fulltext" for candidate in result.candidates for link in candidate.evidence_links if link.evidence_id in {item.evidence_id for item in result.evidence_pool})


def test_fulltext_unavailable_is_visible_degradation_and_does_not_raise_recommendation():
    class Unavailable:
        def query_chunks(self, **kwargs):
            return FulltextResponse(success=False, error="全文网关不可达")

    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=Unavailable())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    assert not result.evidence_chunks
    assert any("全文网关不可达" in item for item in result.query_plan.coverage_warnings)
    assert all(item.recommendation_level != "recommended" for item in result.candidates)
    assert any(finding.stage_key == "retrieval_evidence" for finding in result.quality_gate.findings)


def test_fixed_report_headings_and_nstl_links_are_canonical():
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    assert [item["title"] for item in result.report_plan.sections] == [
        "摘要", "一、推荐结论与候选题优先级", "二、研究主题与相关进展", "三、趋势、方法演进与交叉机会", "四、推荐依据和研究建议", "参考文献",
    ]
    assert 1 <= len(result.theme_tree.nodes) <= 8
    assert all(node.research_object and node.research_question for node in result.theme_tree.nodes)
    assert [item["key"] for item in result.report_plan.table_specs] == ["cross_matrix", "method_flow", "retrieval_queries", "candidate_priority"]
    assert {item["key"] for item in result.report_plan.chart_specs} <= {"discipline_network", "theme_network", "trend_comparison"}
    assert "theme_network" in {item["key"] for item in result.report_plan.chart_specs}
    assert all(not item.url or item.url.startswith(("http://", "https://")) for item in result.evidence_pool)
    assert all("paper_detail.html?id=" not in item.url for item in result.evidence_pool)
    assert all(not ("方法改进与可验证边界" in item.title or "原主题" in item.title) for item in result.candidates)


def test_chinese_fallback_terms_keep_semantic_boundaries_and_generic_titles_are_exploratory():
    terms = TopicSelectionAgent._build_term_map("城市人居环境与居民健康风险关联性研究")
    assert [item.preferred for item in terms] == ["城市人居环境", "居民健康风险"]
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("蛋白质结构预测与罕见病变异解释研究", analysis_mode="fast")))
    assert all(item.title.startswith("探索方向：") for item in result.candidates)
    assert all(not item.convergence_valid and item.recommendation_level == "pending_evidence" for item in result.candidates)
    assert TopicSelectionAgent._is_generic_candidate_title("蛋白质结构预测方法改进")
    assert not TopicSelectionAgent._is_generic_candidate_title("学校周边交通污染对儿童呼吸症状的队列分析")


def test_fulltext_gateway_request_is_batched_read_only_and_rejects_invalid_rows(monkeypatch):
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": {"chunks": [
                {"doc_id": "p1", "chunk_id": "c1", "text": "正文", "chunkIndex": 2, "chunkType": "paragraph", "sectionPath": "结果", "charStart": "10", "charEnd": "20", "tokenCount": 6, "score": .8},
                {"doc_id": "unknown", "chunk_id": "c2", "text": "未请求文档"},
                {"doc_id": "p1", "chunk_id": "bad", "text": "坏范围", "charStart": 20, "charEnd": 10},
                {"doc_id": "p1", "chunk_id": "nan", "text": "无效得分", "score": float("nan")},
                {"doc_id": "p1", "text": "缺少片段编号"},
            ]}}
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr("app.common.retrieval.fulltext.requests.post", post)
    client = LiteratureFulltextClient(api_url="https://gateway.test/chunks")
    result = client.query_chunks(query="研究对象", doc_ids=["p1", "p1", " p2 "], top_k_per_doc=99)
    assert result.success and len(result.chunks) == 1
    chunk = result.chunks[0]
    assert (chunk.chunk_index, chunk.section_path, chunk.char_start, chunk.char_end, chunk.token_count) == (2, "结果", 10, 20, 6)
    assert calls[0][1]["json"] == {"query": "研究对象", "doc_ids": ["p1", "p2"], "top_k_per_doc": 3}
    assert calls[0][1]["headers"]["X-Read-Only"] == "true"
    assert "4 个" in result.error
    assert client.query_chunks(query="", doc_ids=[]).success
    assert not LiteratureFulltextClient(api_url="").query_chunks(query="", doc_ids=["p1"]).success
    assert len(calls) == 1


def test_fulltext_dedup_is_document_scoped_and_keeps_three_highest_unique_chunks():
    class Gateway:
        def query_chunks(self, **kwargs):
            rows = [FulltextChunk(doc_id=doc, chunk_id=f"c{i}", text=f"文本{i}", section_path="结果", char_start=i * 10, char_end=i * 10 + 5, score=i / 10) for doc in ["p1", "p2"] for i in range(6)]
            rows += [rows[-1].model_copy(update={"chunk_id": "duplicate-text", "score": .99})]
            rows += [rows[-2].model_copy(update={"chunk_id": "duplicate-range", "text": "同一范围不同正文", "score": .98})]
            return FulltextResponse(success=True, chunks=rows)
    agent = TopicSelectionAgent(fulltext_client=Gateway())
    state = agent.create_initial_state("主题", analysis_mode="fast")
    state.evidence_pool = [EvidenceRecord(evidence_id=f"E{i}", paper_id=f"p{i}", title="论文", abstract="摘要", fulltext_indexed=True) for i in [1, 2]]
    asyncio.run(agent._retrieve_fulltext_chunks(state, None))
    assert len(state.evidence_chunks) == 6
    assert all(sum(item.doc_id == doc for item in state.evidence_chunks) == 3 for doc in ["p1", "p2"])
    assert len({(item.doc_id, item.char_start, item.char_end) for item in state.evidence_chunks}) == 6
    assert all(item.score >= .3 for item in state.evidence_chunks)


def test_unknown_evidence_and_chunk_references_are_filtered_and_cross_document_links_blocked():
    cleaned, rejected = TopicSelectionAgent._filter_model_evidence_refs({"evidence_id": "unknown", "evidence_ids": ["E1", "unknown"], "nested": {"chunk_ids": ["c1", "invented"]}}, {"E1"}, allowed_chunks={"c1"})
    assert cleaned["evidence_id"] == ""
    assert cleaned["evidence_ids"] == ["E1"] and cleaned["nested"]["chunk_ids"] == ["c1"]
    assert rejected == {"unknown", "invented"}
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    evidence_id = result.evidence_pool[0].evidence_id
    result.evidence_chunks = [TopicEvidenceChunk(doc_id="other-doc", evidence_id="other-evidence", chunk_id="c1", text="正文", section_path="结果")]
    result.candidates[0].evidence_links = [CandidateEvidenceLink(link_id="bad", candidate_id=result.candidates[0].candidate_id, evidence_id=evidence_id, chunk_ids=["c1"])]
    asyncio.run(agent._quality_gate(result))
    assert any("无法定位的全文片段" in item for item in result.quality_gate.blockers)


def test_canonical_exports_retain_body_all_rows_citations_and_clean_links():
    from docx import Document
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    link = "https://www.nstl.gov.cn/paper_detail.html?id=p1"
    result.report_plan.sections[0]["content"] = f"共同正文[1]，来源（{link}）。\n保留第二段。"
    result.report_plan.sections[0]["blocks"] = []
    result.report_plan.section_specs[0]["blocks"] = []
    result.report_plan.table_specs = [{"key": "large", "title": "完整表", "columns": ["行"], "rows": [{"行": f"保留行{i}"} for i in range(105)]}]
    result.report_plan.section_specs[0]["table_keys"] = ["large"]
    result.report_snapshot.report_plan_snapshot = result.report_plan.model_dump()
    before = result.model_dump()
    md, _ = build_topic_markdown(result)
    html, _ = build_topic_html(result)
    docx, _ = build_topic_docx(result)
    document = Document(io.BytesIO(docx))
    body = "\n".join(paragraph.text for paragraph in document.paragraphs)
    xml_body = "\n".join("".join(paragraph.xpath('.//w:t/text()')) for paragraph in document.element.xpath('.//w:p'))
    html_body = html_tools.unescape(re.sub(r"<[^>]*>", "", html.decode().replace("<br>", "\n")))
    compact = lambda value: re.sub(r"\s+", "", value)
    for section in result.report_plan.sections:
        assert section["title"] in md.decode() and section["title"] in html.decode() and section["title"] in body
        if section["key"] == "abstract":
            assert "共同正文" in md.decode()
            assert "共同正文" in compact(html_body)
            assert "共同正文" in compact(xml_body)
    assert all(result.versions.report_version not in content for content in [md.decode(), html_body, xml_body])
    assert "共同正文[1]" in body and "保留第二段。" in body
    assert "保留行104" in md.decode() and "保留行104" in html.decode()
    assert any(cell.text == "保留行104" for table in document.tables for row in table.rows for cell in row.cells)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        relations = archive.read("word/_rels/document.xml.rels").decode()
        assert f'Target="{link}"' in relations
        assert f'Target="{link}）。"' not in relations
    assert result.model_dump() == before


def test_pdf_export_renders_canonical_docx_without_mutating_the_report_snapshot(monkeypatch):
    from docx import Document
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    result.report_plan.sections[0]["content"] = "同一快照的完整正文[1]。"
    result.report_plan.sections[0]["blocks"] = []
    result.report_plan.section_specs[0]["blocks"] = []
    result.report_snapshot.report_plan_snapshot = result.report_plan.model_dump()
    before = result.model_dump()
    rendered = []
    def render(docx):
        rendered.append(Document(io.BytesIO(docx)))
        return None, b"test-pdf"
    monkeypatch.setattr("app.services.report_pdf_runtime.render_docx", render)
    monkeypatch.setattr("app.services.topic_selection_report._pdf_page_count", lambda content: 10)
    content, filename = build_topic_pdf(result)
    assert content == b"test-pdf" and filename.endswith(".pdf")
    body = "\n".join(paragraph.text for paragraph in rendered[0].paragraphs)
    xml_body = "\n".join("".join(paragraph.xpath('.//w:t/text()')) for paragraph in rendered[0].element.xpath('.//w:p'))
    assert "同一快照的完整正文" in body
    assert result.versions.report_version not in body
    assert all(section["title"] in body for section in result.report_plan.sections)
    assert re.sub(r"\s+", "", result.report_plan.sections[0]["content"]) not in re.sub(r"\s+", "", xml_body) or "同一快照的完整正文" in xml_body
    assert result.report_snapshot.report_plan_snapshot == before["report_snapshot"]["report_plan_snapshot"]
    assert result.report_plan.sections == before["report_plan"]["sections"]
    assert result.report_plan.page_metrics.status == "unavailable"


def test_new_report_omits_scores_and_legacy_radar_keeps_empty_state():
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    assert all(item["chart_type"] != "radar" for item in result.report_plan.chart_specs)
    markdown, _ = build_topic_markdown(result)
    assert "候选题三维评估" not in markdown.decode("utf-8")
    assert "可行性" not in next(t for t in result.report_plan.table_specs if t["key"] == "candidate_priority")["columns"]
    from app.services.topic_selection_report import _chart_md
    empty_radar = {"key":"candidate_radar", "title":"历史评分图", "chart_type":"radar", "data_status":"insufficient", "data":{"dimensions":["新颖性","可行性","逆竞争压力"], "max_value":50, "rows":[]}}
    rendered = "\n".join(_chart_md(empty_radar))
    assert "数据不足" in rendered or "共同有效维度" in rendered


def test_chart_font_failure_returns_limitation_instead_of_black_or_blank_image(monkeypatch):
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    chart = {"key":"core_literature_top10", "chart_type":"bar", "title":"历史被引文献图", "min_data_points":1}
    chart["data"] = [{"label": "真实被引文献", "count": 12, "citation_number": 1}]
    chart["data_status"] = "ready"
    monkeypatch.setattr("app.services.topic_selection_report._font", lambda *args, **kwargs: None)
    assert _render_chart_png(chart) is None
    assert "字体" in _chart_limitation(chart)


def test_report_limits_crosslinks_and_candidate_evidence_without_truncating_stage_state():
    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    from app.agents.topic_selection.schemas import CrossLink
    # Export bounding must be tested with explicit relationships, not with
    # synthetic all-pairs links produced from unrelated retrieval results.
    result.crosslinks = [CrossLink(crosslink_id=f"XL-{index:03d}", source_theme_id="T1", target_theme_id="T2",
                                  title=f"有依据的关系 {index}", description="已保存的具体关系", relation_basis="具体方法与研究对象的关联", support_evidence_ids=[result.evidence_pool[0].evidence_id]) for index in range(10)]
    original_link_counts = [len(item.evidence_links) for item in result.candidates]

    sections = agent._report_sections(result)
    cross_section = next(item for item in sections if item["key"] == "cross_discipline")
    cross_result = next(item for item in cross_section["blocks"] if item["kind"] == "bullet_list" and any("关联原因" in value for value in item["items"]))
    assert len(cross_result["items"]) == 3

    candidate_section = next(item for item in sections if item["key"] == "candidate_evaluation")
    candidate_blocks = [item for item in candidate_section["blocks"] if item["kind"] == "candidate_detail"]
    for block in candidate_blocks:
        evidence_text = next(item["value"] for item in block["fields"] if item["label"] == "证据依据")
        assert evidence_text.count("证据范围：") <= 3
        assert evidence_text.count("全文摘录：") <= 3
    assert [len(item.evidence_links) for item in result.candidates] == original_link_counts


def test_gap_chart_requires_distinct_coordinates_and_uses_date_only_source_note():
    spec = {
        "chart_type": "bubble",
        "data_status": "ready",
        "min_data_points": 2,
        "data": [
            {"x": 12.5, "y": 20.0, "size": 1},
            {"x": 12.5, "y": 20.0, "size": 3},
        ],
    }
    assert not _chart_has_data(spec)
    spec["data"][1]["y"] = 21.0
    assert _chart_has_data(spec)

    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    assert all(re.search(r"数据截止时间：(未指定|\d{4}-\d{2}-\d{2})$", item["source_note"]) for item in result.report_plan.chart_specs)


def test_candidate_status_is_localized_in_all_report_formats():
    from docx import Document

    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    result = asyncio.run(agent.run(agent.create_initial_state("城市人居环境与居民健康风险关联性研究", analysis_mode="fast")))
    markdown, _ = build_topic_markdown(result)
    html_report, _ = build_topic_html(result)
    docx, _ = build_topic_docx(result)
    docx_text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(docx)).paragraphs)
    for content in (markdown.decode("utf-8"), html_report.decode("utf-8"), docx_text):
        assert "状态：探索方向" not in content and "状态：优先推荐" not in content
        assert result.candidates[0].display_title or result.candidates[0].title
        assert "待补证据" not in content
        assert "状态：pending_evidence" not in content


def test_radar_displays_complete_long_titles_actual_scores_and_overlap_without_clipping(monkeypatch):
    from PIL import Image, ImageDraw

    titles = [
        "LLZO晶界类型与锂枝晶成核能垒的定量关联：基于高分辨TEM与AIMD的系统研究",
        "探索方向：研究对象与科学问题、应用场景与转化边界中的界面失效机制在真实电池中动态演变的原位验证。" * 3,
        "跨实验室标准化数据对比：" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 6,
        "硫化物电解质在高温高压工况下的性能退化机制：原位XRD与长循环测试",
        "探索方向：不同实验室间电解质性能数据缺乏标准化对比，建立可追溯的实验边界与误差记录。",
    ]
    scores = [[35.3, 33, 50], [35.3, 33, 50], [35.3, 33, 50], [34.63, 33, 47.5], [34.47, 33, 50]]
    spec = {"chart_type": "radar", "data_status": "ready", "data": {
        "dimensions": ["新颖性", "可行性", "逆竞争压力"], "max_value": 50,
        "rows": [{"candidate_id": f"C{index + 1}", "label": title, "values": values}
                 for index, (title, values) in enumerate(zip(titles, scores))],
    }}
    drawn = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, position, text, *args, **kwargs):
        # Check the actual glyph bounds as well as retained text; a renderer can
        # retain a title in memory but still draw its last lines off the canvas.
        box = draw.textbbox(position, text, font=kwargs.get("font"), anchor=kwargs.get("anchor"))
        drawn.append((text, box, draw._image.size))
        return original_text(draw, position, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    before = copy.deepcopy(spec)
    content = _render_chart_png(spec)
    assert content and spec == before
    Image.open(io.BytesIO(content)).verify()
    retained_text = "".join(str(text) for text, _, _ in drawn)
    for title in titles:
        assert title in retained_text
    assert "C1、C2、C3评分相同，曲线重合。" in retained_text
    assert "35.3 / 33 / 50" in retained_text
    assert "34.63 / 33 / 47.5" in retained_text
    assert "…" not in retained_text
    for _, (left, top, right, bottom), (width, height) in drawn:
        assert 0 <= left <= right <= width
        assert 0 <= top <= bottom <= height


def test_legacy_clipped_exploratory_title_restores_only_preserved_gap_text_in_all_formats():
    from app.agents.topic_selection.schemas import GapScore, TopicCandidate
    from app.services.topic_selection_report import _bounded_export_state

    agent = TopicSelectionAgent(retrieval_client=SearchWithFulltext(), fulltext_client=FulltextFixture())
    state = asyncio.run(agent.run(agent.create_initial_state("固态电池界面失效机制研究", analysis_mode="fast")))
    # Historical tasks did not freeze the complete report plan.
    state.report_snapshot.report_plan_snapshot = {}
    state.report_snapshot.result_data_snapshot = {}
    label = "现有摘要缺乏对界面失效机制在真实电池中动态演变的验证，需考虑温度、压力、循环次数和实验室间测量误差，建立标准化原位表征及独立复现实验。"
    gap = GapScore(gap_id="legacy-gap", label=label)
    full_title = agent._candidate_title("界面稳定性", gap, "验证", state)
    assert full_title.endswith(label)
    old_title = f"探索方向：界面稳定性中的{label[:60]}"
    state.gaps = [gap]
    state.candidates = [TopicCandidate(candidate_id="C4", title=old_title, research_question="界面失效机制如何随循环变化？", research_object="界面稳定性", gap_ids=[gap.gap_id])]
    state.report_plan.sections = [{"key": "candidate_evaluation", "title": "七、高价值选题生成与多维评估",
                                  "content": f"{old_title}\n{full_title}", "blocks": [{"kind": "candidate_detail", "title": old_title, "fields": []}]}]
    state.report_plan.chart_specs = [{"key": "candidate_radar", "chart_type": "radar", "data": {"rows": [{"candidate_id": "C4", "label": old_title}]}}]
    state.report_plan.table_specs = [{"key": "candidate_priority", "columns": ["候选题"], "rows": [{"候选题": old_title}]}]
    before = state.model_dump_json()
    adapted = _bounded_export_state(state)
    assert adapted.candidates[0].title == full_title
    assert adapted.report_plan.chart_specs[0]["data"]["rows"][0]["label"] == full_title
    assert adapted.report_plan.table_specs[0]["rows"][0]["候选题"] == full_title
    assert adapted.report_plan.sections[0]["content"] == f"{full_title}\n{full_title}"
    assert _bounded_export_state(adapted).model_dump_json() == adapted.model_dump_json()
    assert full_title in build_topic_html(state)[0].decode("utf-8")
    assert full_title in build_topic_markdown(state)[0].decode("utf-8")
    assert state.model_dump_json() == before
    state.candidates[0].title = "模型生成的具体科学问题"
    assert _bounded_export_state(state).candidates[0].title == state.candidates[0].title


def test_export_cache_refreshes_for_new_chart_renderer_without_changing_snapshot(monkeypatch):
    from app.agents.topic_selection.schemas import TopicSelectionState
    from app.services import topic_selection_report as report

    state = TopicSelectionState(raw_user_input="缓存测试")
    state.versions.report_version = "radar-cache-regression"
    before = state.model_dump_json()
    renders = []
    monkeypatch.setattr(report, "_build_topic_docx_source", lambda state: (report.TOPIC_CHART_RENDER_VERSION.encode(), "report.docx"))

    def render(source):
        renders.append(source)
        return b"word-" + source, b"pdf-" + source

    monkeypatch.setattr("app.services.report_pdf_runtime.render_docx", render)
    first = report._writer_outputs(state)
    assert report._writer_outputs(state) == first
    assert len(renders) == 1
    monkeypatch.setattr(report, "TOPIC_CHART_RENDER_VERSION", "next-renderer")
    next_pair = report._writer_outputs(state)
    assert next_pair != first
    assert len(renders) == 2
    assert state.model_dump_json() == before
