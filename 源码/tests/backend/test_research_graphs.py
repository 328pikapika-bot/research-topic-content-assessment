"""Graph relationships and newly frozen report images."""
import io
import zipfile

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.discovery import ground_discovery
from app.agents.topic_selection.schemas import CrossLink, EvidenceRecord
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import Claim
from app.agents.content_assessment.report_composition import markdown
from app.services.content_assessment_report import build_content_assessment_docx, build_report_chart_assets
from app.services.topic_selection_report import _render_chart_parts
from app.services.research_graph_rendering import topic_opportunity_parts, topic_tree_parts
from app.services.topic_selection_report import _render_chart_png

from test_phase2_discovery import fixture_state
from test_research_report_v6 import research_sample


def test_one_opportunity_preserves_multiple_evidence_based_disciplines_and_static_edges():
    state = fixture_state()
    state.evidence_pool.extend([
        EvidenceRecord(evidence_id="E2", title="Information science", abstract="Information science retrieval.",
                       discipline_tags=["信息科学"], theme_ids=["T2"]),
        EvidenceRecord(evidence_id="E3", title="Statistics", abstract="Statistical validation.",
                       discipline_tags=["统计学"], theme_ids=["T2"]),
    ])
    state.crosslinks[0].support_evidence_ids = ["E1", "E2", "E3"]
    proposal = {"crosslink_id": "XL1", "source_theme_id": "T1", "target_theme_id": "T2",
        "participating_disciplines": [
            {"discipline": "图书馆学", "role": "core", "reason": "核心研究方向", "evidence_ids": []},
            {"discipline": "数学", "role": "expansion", "reason": "概率排序", "evidence_ids": ["E1"]},
            {"discipline": "信息科学", "role": "expansion", "reason": "检索组织", "evidence_ids": ["E2"]},
            {"discipline": "统计学", "role": "expansion", "reason": "验证方法", "evidence_ids": ["E3"]},
        ]}
    ground_discovery(state, [proposal])
    link = state.crosslinks[0]
    assert {p.discipline for p in link.participating_disciplines} == {"图书馆学", "数学", "信息科学", "统计学"}
    assert next(p for p in link.participating_disciplines if p.discipline == "统计学").evidence_ids == ["E3"]
    spec = next(s for s in TopicSelectionAgent._chart_specs(state) if s["key"] == "discipline_network")
    # The opportunity network uses the v11 compact layout introduced with the
    # durable recovery/report rendering changes.  v10 remains readable for
    # historical snapshots, but new chart specifications are v11.
    assert spec["chart_version"] == "chart-v11"
    assert len(spec["data"]["rows"][0]["participating_disciplines"]) == 4
    assert _render_chart_parts(spec)[0].startswith(b"\x89PNG")


def test_opportunity_and_theme_charts_keep_long_labels_and_dynamic_counts():
    from PIL import Image

    rows = [{"crosslink_id": f"XL{i}", "number": i, "opportunity": f"研究机会{i}：围绕城市热暴露差异、跨区域模型迁移与验证边界形成完整研究问题",
             "method": "跨城市模型迁移", "participating_disciplines": [
                 {"discipline": "城市环境科学", "role": "core"},
                 {"discipline": "统计学", "role": "expansion"},
             ]} for i in range(1, 4)]
    for count in (1, 2, 3):
        parts = topic_opportunity_parts(rows[:count])
        assert len(parts) == 1
        assert Image.open(io.BytesIO(parts[0])).width == 1900

    short = topic_tree_parts({"root": "研究方向", "nodes": [
        {"theme_id": "T1", "label": "短主题", "parent_id": "root"},
    ]})[0]
    long = topic_tree_parts({"root": "研究方向", "nodes": [
        {"theme_id": "T1", "label": "长三角高密度城市夏季热暴露差异与跨区域模型研究迁移性及其验证条件", "parent_id": "root"},
    ]})[0]
    assert Image.open(io.BytesIO(long)).height > Image.open(io.BytesIO(short)).height


def test_trend_chart_keeps_all_subdirection_series_in_the_export_image():
    from PIL import Image

    spec = {"chart_type": "stacked_bar", "data_status": "ready", "min_data_points": 1,
        "data": {"years": [2020, 2021], "series": [
            {"label": f"研究子方向{i}完整名称", "values": [i + 1, i + 2]} for i in range(10)
        ]}}
    rendered = _render_chart_png(spec)
    assert rendered and rendered.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(rendered)).width == 1900


def test_report_graph_uses_chapter_five_sources_and_frozen_snapshot():
    state = research_sample()
    state.claims.append(Claim(claim_id="C2", text="拟研究另一种材料条件。", assertion_type="target"))
    ContentAssessmentGraph()._report(state)
    entry = next(e for e in state.report_snapshot.chart_manifest if e["chart_key"] == "claim_evidence_network")
    chart = entry["chart_data"]
    assert entry["figure_number"] == "5-1"
    assert len(chart["points"]) == 2
    assert {e["id"] for e in chart["evidence"]} == {"E1", "E3"}
    assert {(link["source"], link["target"]) for link in chart["links"]} == {("C1", "E1"), ("C1", "E3")}
    assert all(link["source"] != "C2" for link in chart["links"])
    before = build_report_chart_assets(state)["claim_evidence_network"]
    state.claims.clear(); state.evidence_records.clear(); state.claim_evidence_links.clear()
    assert build_report_chart_assets(state)["claim_evidence_network"] == before
    docx, _ = build_content_assessment_docx(state)
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert any(name.startswith("word/media/image") for name in archive.namelist())
    assert "![图 5-1 要点与比对证据对应图](data:image/png;base64," in markdown(state)
