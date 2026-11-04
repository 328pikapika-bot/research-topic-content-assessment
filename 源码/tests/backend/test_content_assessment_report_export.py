from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.agents.content_assessment.schemas import (
    AssessmentProfile,
    AssessmentReportPlan,
    Claim,
    ClaimEvidenceLink,
    ContentAssessmentState,
    DocumentRecord,
    EvidenceRecord,
    NoveltyDecision,
    QualityGate,
    QueryPlan,
    ReportSectionSpec,
    ReportSnapshot,
    ScoreCardItem,
    StageTraceItem,
)
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.services.export_service import build_docx
from app.services.export_service import build_html, build_markdown, build_pdf
from app.services.content_assessment_report import _select_chart_keys


def test_word_export_contains_report_sections_and_charts(tmp_path: Path) -> None:
    state = ContentAssessmentState(
        raw_user_input="构建肿瘤免疫微环境器官芯片，并验证高通量药敏评价能力。",
        stage_trace=[StageTraceItem(stage_key="document_intake", stage_name="材料解析", status="completed", summary="已解析项目申请书。")],
        assessment_profile=AssessmentProfile(
            title="肿瘤免疫微环境器官芯片与高通量药敏评价系统",
            assessment_object="肿瘤免疫微环境器官芯片与高通量药敏评价系统",
            scope_boundary="以项目材料中的芯片设计、免疫共培养和药敏指标为边界。",
            document_records=[
                DocumentRecord(
                    document_id="D-001",
                    name="项目申请书.docx",
                    role="PROJECT",
                    status="parsed",
                    excerpt="项目拟建立肿瘤免疫微环境器官芯片并开展药敏评价。",
                    content_available=True,
                )
            ],
        ),
        claims=[
            Claim(
                claim_id="C01",
                text="系统可在芯片中稳定维持肿瘤细胞和免疫细胞共培养 72 小时。",
                claim_type="technical",
                assertion_type="target",
            )
        ],
        evidence_records=[
            EvidenceRecord(
                evidence_id="E01",
                title="Organ-on-chip models for tumour immune microenvironment research",
                source_type="literature",
                year=2025,
                doi="10.1000/example",
                excerpt="The study reports immune co-culture in an organ-on-chip model.",
                evidence_level="B",
                usable_for_novelty=True,
            )
        ],
        claim_evidence_links=[
            ClaimEvidenceLink(
                claim_id="C01",
                evidence_id="E01",
                relation="partially_supports",
                confidence=0.78,
                uncertainty="证据未直接覆盖 72 小时指标。",
            )
        ],
        novelty_decisions=[
            NoveltyDecision(
                claim_id="C01",
                label="证据不足或存疑",
                rationale="已有共培养先例，但尚未形成同口径的 72 小时稳定性证据。",
                evidence_ids=["E01"],
                risk_notes=["关键时长指标需要项目实验数据支撑。"],
                rewrite_advice="将绝对断言改写为拟达到目标，并补充稳定性验证方案。",
            )
        ],
        scorecard=[
            ScoreCardItem(
                dimension="证据充分性",
                assessment="已获得方向相关证据，但关键性能指标仍缺少直接比对。",
                score_range="2.5/5",
                basis=["E01 提供共培养先例。"],
                uncertainty="当前仅纳入摘要级证据。",
            )
        ],
        trend_analysis={
            "summary": "肿瘤免疫微环境器官芯片是持续发展的研究方向。",
            "trend_highlights": ["免疫共培养与高通量分析的集成需求提升。"],
            "competition_observations": ["现有系统在标准化和长期稳定性方面仍有差异。"],
        },
        quality_gate=QualityGate(
            passed=True,
            report_eligibility="formal",
        ),
        report_plan=AssessmentReportPlan(
            report_version="report-test-001",
            report_title="肿瘤免疫微环境器官芯片_正式评估报告",
            report_type="正式评估报告",
            executive_summary="已完成材料解析、证据检索和主张级比对。",
        ),
        output_json={
            "search_trace": [
                {"query_id": "Q1", "query": "tumour immune organ-on-chip co-culture", "gateway_status_code": 200, "gateway_code": 1, "paper_num_returned": 12}
            ]
        },
    )

    from app.agents.content_assessment.report_composition import freeze_snapshot
    freeze_snapshot(state)
    content, filename = build_docx(state)

    assert filename.endswith(".docx")
    assert content[:2] == b"PK"
    report_path = tmp_path / filename
    report_path.write_bytes(content)
    with zipfile.ZipFile(io.BytesIO(report_path.read_bytes())) as archive:
        names = archive.namelist()
        body = archive.read("word/document.xml").decode("utf-8")
    assert "word/media/" in "\n".join(names)
    for text in ("目录", "评估摘要与总体结论", "评估对象和要点", "相关研究进展", "逐项比对与创新性分析", "趋势和综合建议", "参考文献"):
        assert text in body
    assert 'TOC' in body and '\\o' in body
    assert "评估过程实录" not in body
    assert "文档上传与解析" not in body
    assert body.count("章节生成内容") == 0
    assert "ListBullet" not in body
    root = ET.fromstring(body)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    first_line_values = [
        node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}firstLine")
        for node in root.findall(".//w:body/w:p/w:pPr/w:ind", ns)
    ]
    assert "420" in first_line_values
    assert body.count("<w:drawing") >= 1


def test_all_exports_use_reader_title_and_hide_machine_trace_from_body() -> None:
    state = ContentAssessmentState(
        raw_user_input="评估器官芯片药敏研究方案。",
        assessment_profile=AssessmentProfile(title="器官芯片药敏研究方案"),
        report_plan=AssessmentReportPlan(
            report_version="report-test-002",
            report_title="器官芯片药敏研究方案_探索性评估报告",
            section_specs=[ReportSectionSpec(section_key="executive", title="执行摘要与核心结论", order=1, content="这是面向项目负责人的简要结论。", status="generated")],
        ),
        output_json={"search_trace": [{"query": "raw query", "filters": {"language": "zh"}, "gateway_status_code": 200}]},
    )

    markdown, markdown_name = build_markdown(state)
    html, html_name = build_html(state)

    assert markdown_name == "器官芯片药敏研究方案内容评估报告.md"
    assert html_name == "器官芯片药敏研究方案内容评估报告.html"
    assert markdown.startswith("# 器官芯片药敏研究方案内容评估报告")
    assert "report-test-002" in markdown
    assert "report-test-002" in html
    assert "章节生成内容" not in markdown
    assert "filters" not in markdown
    assert "<nav class=\"toc\">" in html
    assert "id=\"执行摘要与核心结论\"" in html
    assert ".report-paragraph{text-indent:2em}" in html


def test_retrieval_section_merges_duplicate_query_groups_and_limits_representatives() -> None:
    state = ContentAssessmentState(
        raw_user_input="器官芯片药敏评估",
        query_plans=[
            QueryPlan(
                claim_id="C01",
                query="organ chip drug screening",
                query_group="直接先例",
                rationale="寻找同类芯片验证依据",
                candidate_count=12,
                selected_count=4,
                source_status="completed",
            ),
            QueryPlan(
                claim_id="C01",
                query="organ-on-chip drug response",
                query_group="直接先例",
                rationale="寻找同类芯片验证依据",
                candidate_count=8,
                selected_count=3,
                source_status="completed",
            ),
            QueryPlan(
                claim_id="C01",
                query="microfluidic assay reproducibility",
                query_group="方法路线",
                rationale="比较方法可重复性",
                candidate_count=10,
                selected_count=2,
                source_status="completed",
            ),
        ],
        evidence_records=[
            EvidenceRecord(evidence_id="E01", title="A", source_type="literature", year=2024),
            EvidenceRecord(evidence_id="E02", title="B", source_type="literature", year=2023),
            EvidenceRecord(evidence_id="E03", title="C", source_type="literature", year=2022),
            EvidenceRecord(evidence_id="E04", title="D", source_type="literature", year=2021),
        ],
    )
    specs = ContentAssessmentGraph._build_report_section_specs(state)
    ContentAssessmentGraph._compose_report_sections(state, specs)
    retrieval = next(item for item in specs if item.section_key == "process")

    assert "organ chip drug screening" not in retrieval.content
    from app.services.export_service import build_evidence_package
    state.report_plan.section_specs = specs
    from app.agents.content_assessment.report_composition import freeze_snapshot
    freeze_snapshot(state)
    archive, _ = build_evidence_package(state)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        plans = bundle.read("query_plans.json").decode("utf-8")
    assert "寻找同类芯片验证依据" in plans
    assert "比较方法可重复性" in plans


def test_evidence_package_tolerates_legacy_snapshot_without_audit_collections(monkeypatch) -> None:
    from app.services import export_service

    state = ContentAssessmentState(
        raw_user_input="历史任务证据附件兼容性",
        report_plan=AssessmentReportPlan(report_version="report-legacy", report_title="历史任务"),
        report_snapshot=ReportSnapshot(snapshot_id="snapshot-legacy", generated_at="2026-01-01T00:00:00+08:00"),
    )
    monkeypatch.setattr(export_service, "prepare_exports", lambda current: (b"", b""))

    payload, filename = export_service.build_evidence_package(state)

    assert filename.endswith("-证据附件.zip")
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        assert "evidence.md" in bundle.namelist()
        assert "manifest.json" in bundle.namelist()


def test_pdf_export_is_readable_and_records_real_page_count() -> None:
    import fitz

    state = ContentAssessmentState(
        raw_user_input="项目拟构建器官芯片系统。",
        assessment_profile=AssessmentProfile(title="器官芯片系统", assessment_object="器官芯片系统"),
        report_plan=AssessmentReportPlan(
            report_version="report-pdf-001",
            report_title="器官芯片系统创新性评估综合报告",
            report_type="探索性评估报告",
            target_page_min=18,
            target_page_max=26,
            section_specs=[
                ReportSectionSpec(
                    section_key="executive",
                    title="执行摘要与核心结论",
                    order=1,
                    content="评估对象：器官芯片系统。\n\n| 主张 | 判定 |\n| --- | --- |\n| C-001 | 证据不足 |",
                    status="generated",
                )
            ],
        ),
    )
    payload, filename = build_pdf(state)
    assert filename.endswith(".pdf")
    assert payload.startswith(b"%PDF")
    document = fitz.open(stream=payload, filetype="pdf")
    assert len(document) >= 1
    text = "\n".join(page.get_text() for page in document)
    assert "# 执行摘要" not in text
    assert "| --- |" not in text
    assert "器官芯片系统" in text
    assert state.report_metrics.page_count_verified is True
    assert state.report_metrics.page_count_source.startswith("pdf:libreoffice:")
    assert state.report_metrics.actual_page_count == len(document)
    assert state.report_plan.actual_page_count == len(document)
    assert state.report_metrics.page_target_status == "below_target"


def test_chart_selection_is_data_backed_and_bounded() -> None:
    from types import SimpleNamespace

    data = {
        "profile": SimpleNamespace(document_records=[1]),
        "claims": [SimpleNamespace(claim_id="C-001")],
        "queries": [SimpleNamespace(query_group="路线")],
        "stage_trace": [SimpleNamespace(stage_key="retrieval", status="completed")],
        "evidence": [SimpleNamespace(year=2023, source_type="literature", query_group="路线")],
        "links": [SimpleNamespace(claim_id="C-001", evidence_id="E-001", relation="supports")],
        "evidence_by_id": {"E-001": SimpleNamespace(evidence_level="B")},
        "indicator_validations": [SimpleNamespace(status="partial")],
        "scorecard": [SimpleNamespace(dimension="创新性", score_range="3/5", basis=["E-001"])],
        "decisions": [SimpleNamespace(label="方向性支持")],
        "market_cards": [SimpleNamespace(source_evidence_ids=["E-001"])],
        "competitive_cards": [],
        "regulatory_cards": [],
        "trend": {"themes": [{"label": "路线 A", "maturity": "3/5", "opportunity": "4/5", "evidence_count": 1},
                               {"label": "路线 B", "maturity": "4/5", "opportunity": "3/5", "evidence_count": 1}]},
        "metrics": SimpleNamespace(directional_claim_coverage=0.5, formal_claim_coverage=0.1, indicator_coverage=0.5),
        "quality_gate": SimpleNamespace(warnings=[]),
    }
    selected, manifest = _select_chart_keys(
        data, ["workflow", "claim_judgement", "years", "market", "competition", "radar"]
    )
    assert len(selected) <= 6
    assert "claim_judgement" in selected
    assert "years" in selected
    assert all(item["chart_type"] for item in manifest)
    assert any(item["included"] is False for item in manifest)
