import asyncio
import json
import subprocess
from pathlib import Path

from app.agents.content_assessment import graph as graph_module
from app.agents.content_assessment.graph import ContentAssessmentGraph, STANDARD_QUERY_GROUPS
from app.agents.content_assessment.scenario_catalog import SCENARIO_CONFIGS
from app.agents.content_assessment.schemas import (
    ClaimEvidenceLink, ContentAssessmentState, EvidenceChunk, EvidenceRecord,
    Indicator, ResearchNineFields,
)
from app.api.routes import _locate_claim_in_evidence
from app.agents.content_assessment.schemas import Claim
from app.common.retrieval.links import citation_urls, evidence_url
from app.common.retrieval.parser import SearchResultParser
from app.common.retrieval.schemas import StandardPaper
from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import QueryBranch
from app.repositories.content_assessment_task_history import task_statistics


def test_current_homepage_assessment_materials_complete_real_eight_stage_flow(monkeypatch):
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    root = Path(__file__).resolve().parents[2]
    script = """
const fs = require('fs')
const vm = require('vm')
const ts = require('./frontend/web/node_modules/typescript')
const source = fs.readFileSync('./frontend/web/src/services/homepageDraft.ts', 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const exports = {}
vm.runInNewContext(compiled, { exports })
process.stdout.write(JSON.stringify(exports.ASSESSMENT_CASES.map(({ id, input }) => ({ id, input }))))
"""
    cases = json.loads(subprocess.run(
        ["node", "-e", script], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout)
    assert len(cases) == 6
    for case in cases:
        graph = ContentAssessmentGraph()
        state = graph.create_initial_state(case["input"], scenario_id=case["id"])
        result = asyncio.run(graph.run(state))
        if result.current_stage == "awaiting_confirmation":
            result.confirmation.needed = False
            result.assessment_profile.confirmation_required = False
            result.current_stage = result.resume_from_stage
            result = asyncio.run(graph.run(result))
        assert result.current_stage == "awaiting_confirmation", case["id"]
        assert result.confirmation.reason in {"report_sources", "report_drafting"}, case["id"]
        artifact_type = {"assessment-platform": "paper", "assessment-drug": "paper", "assessment-battery-v2": "project", "assessment-chip-v2": "project", "assessment-standard": "guideline", "assessment-cost": "guideline"}[case["id"]]
        assert result.report_preferences.resolved_artifact_type == artifact_type, case["id"]
        assert not result.nine_fields.missing_for_type(artifact_type), case["id"]
        assert len(result.query_plans) == len(STANDARD_QUERY_GROUPS), case["id"]
        assert any(item.source_url and item.doi for item in result.evidence_records), case["id"]
        assert result.report_snapshot is None, case["id"]
        assert not result.output_markdown, case["id"]
        assert all(item.status == "completed" for item in result.stage_trace[:-1]), case["id"]


def test_evidence_claim_locator_uses_chunk_offset_and_rejects_mismatch():
    state = ContentAssessmentState(
        raw_user_input="测试主张定位",
        evidence_records=[EvidenceRecord(evidence_id="E-1", title="证据", source_type="literature", abstract="摘要原文", char_start=100)],
        evidence_chunks=[EvidenceChunk(chunk_id="K-1", evidence_id="E-1", excerpt="前缀主张原文后缀", char_start=320, page_ref="12")],
    )
    claim = Claim(claim_id="C-1", text="主张原文", source_kind="evidence", source_evidence_id="E-1", source_span="主张原文")
    located = _locate_claim_in_evidence(state, claim)
    assert located and located["source_char_start"] == 322 and located["source_page_ref"] == "12"
    claim.source_span = "并不存在"
    assert _locate_claim_in_evidence(state, claim) is None


def test_evidence_url_priority_and_empty_state():
    base = {"nstl_url": "https://nstl.example/detail", "source_url": "https://publisher.example/full", "url": "https://legacy.example", "doi": "10.1000/test"}
    assert evidence_url(base) == base["nstl_url"]
    base["nstl_url"] = ""
    assert evidence_url(base) == base["source_url"]
    base["source_url"] = ""; base["url"] = ""
    assert evidence_url(base) == "https://doi.org/10.1000/test"
    assert evidence_url({"nstl_url": "javascript:alert(1)", "doi": "bad"}) == ""
    assert evidence_url({"paper_id": "NSTL-001"}) == "https://www.nstl.gov.cn/paper_detail.html?id=NSTL-001"
    assert evidence_url({"id": "NSTL-002"}) == "https://www.nstl.gov.cn/paper_detail.html?id=NSTL-002"


def test_nstl_parser_normalizes_aliases_and_detail_fallback():
    papers = SearchResultParser().parse({"data": [
        {"id": "NSTL-001", "title": "只有编号", "hasAuthor": [], "year": 2024},
        {"paperId": "NSTL-002", "title": "字段别名", "detail_url": "https://www.nstl.gov.cn/paper_detail.html?id=NSTL-002",
         "fullTextUrl": "https://publisher.example/full", "doi": "10.1000/example"},
        {"id": "NSTL-003", "title": "文件字段", "hasFile": "https://www.nstl.gov.cn/paper_detail.html?id=NSTL-003"},
    ]}, "测试")
    assert papers[0].nstl_url.endswith("id=NSTL-001")
    assert papers[1].nstl_url.endswith("id=NSTL-002")
    assert papers[1].source_url == "https://publisher.example/full"
    assert papers[2].nstl_url.endswith("id=NSTL-003")
    assert not papers[2].url


def test_nstl_parser_reads_nested_aliases_and_rejects_landing_page():
    papers = SearchResultParser().parse({"data": [
        {"articleId": "NSTL-NESTED", "title": "嵌套记录", "links": {
            "nstl": {"detailURL": "https://www.nstl.gov.cn/paper_detail.html?id=NSTL-NESTED"},
            "source": {"originalURL": "https://publisher.example/nested"},
        }},
        {"id": "NSTL-LANDING", "title": "首页回退", "detailUrl": "https://www.nstl.gov.cn/",
         "sourceUrl": "https://publisher.example/fallback"},
    ]}, "测试")
    assert papers[0].paper_id == "NSTL-NESTED"
    assert papers[0].nstl_url.endswith("id=NSTL-NESTED")
    assert papers[0].source_url == "https://publisher.example/nested"
    assert papers[1].nstl_url.endswith("id=NSTL-LANDING")
    assert evidence_url({"nstl_url": "https://www.nstl.gov.cn/", "sourceUrl": "https://publisher.example/fallback"}) == "https://publisher.example/fallback"


def test_citation_urls_uses_aliases_and_falls_back_across_deduplicated_records():
    records = [
        {"evidence_id": "E1", "nstlUrl": "https://www.nstl.gov.cn/", "sourceUrl": "https://publisher.example/1"},
        {"evidence_id": "E2", "paperId": "NSTL-2"},
        {"evidence_id": "E3", "doiUrl": "https://doi.org/10.1000/three"},
    ]
    assert citation_urls(records, {"E1": 1, "E2": 2, "E3": 3}) == {
        "1": "https://publisher.example/1",
        "2": "https://www.nstl.gov.cn/paper_detail.html?id=NSTL-2",
        "3": "https://doi.org/10.1000/three",
    }


def test_topic_evidence_keeps_independent_source_addresses():
    paper = StandardPaper(
        paper_id="NSTL-004", title="证据题名", nstl_url="https://www.nstl.gov.cn/paper_detail.html?id=NSTL-004",
        source_url="https://publisher.example/full", url="https://publisher.example/file", doi="10.1000/topic",
    )
    evidence = TopicSelectionAgent._evidence_from_paper(
        paper, QueryBranch(branch_id="q-1", theme_id="theme-1", purpose="核心检索", query="检索", query_role="核心")
    )
    assert evidence.nstl_url.endswith("id=NSTL-004")
    assert evidence.source_url == "https://publisher.example/full"
    assert evidence.url == "https://publisher.example/file"


def test_normalized_history_groups_equal_total():
    statuses = ["queued", "running", "processing", "completed", "awaiting_review", "failed", "time_out", "cancelled", "legacy_unknown"]
    stats = task_statistics([{"status": status, "task_type": "创新性评估"} for status in statuses])
    groups = stats["status_groups"]
    assert groups == {"running": 3, "completed": 1, "attention": 4, "stopped": 1}
    assert sum(groups.values()) == stats["total"]


def test_homepage_scenario_uses_verified_sources_and_balanced_conclusions(monkeypatch):
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    fields = ResearchNineFields(
        research_direction="高离子电导固态电解质及界面稳定化",
        research_goal="开发室温高电导、低界面阻抗的复合固态电解质",
        research_content="研究复合电解质配方、界面改性机制和全电池性能验证",
        technical_route="制备复合电解质，通过阻抗谱筛选后开展循环测试",
        key_technologies=["复合固态电解质", "界面改性"],
        evaluation_indicators=["离子电导率不低于1 mS/cm", "界面阻抗低于80 Ω·cm²"],
        timeline="24个月",
        application_scenarios=["新能源汽车动力电池"],
        expected_outcomes=["复合固态电解质配方"],
    )
    raw = "\n".join([
        "项目名称：固态电池材料研发项目评估",
        f"研究方向：{fields.research_direction}", f"研究目标：{fields.research_goal}",
        f"研究内容：{fields.research_content}", f"技术路线：{fields.technical_route}",
        f"关键技术：{'；'.join(fields.key_technologies)}", f"考核指标：{'；'.join(fields.evaluation_indicators)}",
        f"时间节点：{fields.timeline}", f"应用场景：{'；'.join(fields.application_scenarios)}",
        f"预期成果：{'；'.join(fields.expected_outcomes)}",
    ])
    state = graph.create_initial_state(raw, scenario_id="assessment-battery")
    state.nine_fields = fields
    state.nine_fields_confirmed = True
    graph._claim_extraction(state)
    asyncio.run(graph._retrieval(state))
    graph._comparison(state)
    graph._assessment(state)
    graph._rebuild_novelty_decisions(state)
    graph._quality_gate(state)

    assert 3 <= len(state.claims) <= 6
    assert len(state.query_plans) == len(STANDARD_QUERY_GROUPS)
    assert all(item.source_url.startswith("https://europepmc.org/articles/") and item.doi for item in state.evidence_records if item.source_type == "literature")
    assert {item.claim_id for item in state.claim_evidence_links} == {item.claim_id for item in state.claims}
    assert all(item.label in {"有直接支持", "存在相近先例", "方向性支持"} for item in state.novelty_decisions)
    # Curated summaries are still abstracts; they cannot satisfy a fulltext gate.
    assert state.quality_gate.report_eligibility == "exploratory"
    assert all(item.evidence_level == "B" and not item.fulltext_available for item in state.evidence_records if item.source_type == "literature")
    assert [item["key"] for item in state.quality_gate.checks] == ["input_completeness", "retrieval_completeness", "claim_coverage", "report_readiness"]


def test_homepage_verified_relationships_survive_pessimistic_model_output(monkeypatch):
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    fields = ResearchNineFields(
        research_direction="多器官芯片规模化制造与全过程质量控制",
        research_goal="建立覆盖设计、制造、组装和生物学验证的质量标准",
        research_content="研究微流道尺寸、密封可靠性和细胞功能等关键质量属性",
        technical_route="梳理制造失效模式并开展三批次对照试制",
        key_technologies=["微纳制造", "等离子键合"],
        evaluation_indicators=["关键尺寸偏差不超过5%"],
        timeline="18个月",
        application_scenarios=["多器官芯片研发与生产"],
        expected_outcomes=["质量控制规范和标准草案"],
    )
    raw = "\n".join([
        "项目名称：多器官芯片制造标准与质量控制体系",
        f"研究方向：{fields.research_direction}", f"研究目标：{fields.research_goal}",
        f"研究内容：{fields.research_content}", f"技术路线：{fields.technical_route}",
        f"关键技术：{'；'.join(fields.key_technologies)}", f"考核指标：{'；'.join(fields.evaluation_indicators)}",
        f"时间节点：{fields.timeline}", f"应用场景：{'；'.join(fields.application_scenarios)}",
        f"预期成果：{'；'.join(fields.expected_outcomes)}",
    ])
    state = graph.create_initial_state(raw, scenario_id="assessment-standard")
    state.nine_fields = fields
    state.nine_fields_confirmed = True
    graph._claim_extraction(state)
    asyncio.run(graph._retrieval(state))
    graph._comparison(state)
    scenario_evidence = [
        item for item in state.evidence_records
        if item.retrieval_batch_id == "homepage-scenario-v1:assessment-standard"
    ]
    state.output_json["_model_comparison"] = {
        "links": [
            {
                "claim_id": claim.claim_id,
                "evidence_id": evidence.evidence_id,
                "relation": "insufficient_evidence",
                "confidence": 0,
                "difference": "模型认为字段不足",
            }
            for claim in state.claims for evidence in scenario_evidence
        ],
        "decisions": [],
    }

    graph._assessment_stage(state)
    graph._quality_gate(state)

    links = [
        item for item in state.claim_evidence_links
        if item.evidence_id in {evidence.evidence_id for evidence in scenario_evidence}
    ]
    assert {item.claim_id for item in links} == {item.claim_id for item in state.claims}
    assert all(item.relation == "partially_supports" for item in links)
    assert all(item.incomparability_reasons for item in links)
    assert all(item.label in {"方向性支持", "存在相近先例"} for item in state.novelty_decisions)
    assert state.report_metrics.directional_claim_coverage == 1
    assert state.quality_gate.report_eligibility == "exploratory"


def test_all_homepage_scenario_snapshots_are_source_traceable():
    current_ids = {key for key in SCENARIO_CONFIGS if key.endswith("-v2")}
    assert current_ids == {
        "assessment-battery-v2", "assessment-chip-v2", "assessment-crop-v2",
        "assessment-microplastics-v2", "assessment-co2-v2", "assessment-quantum-v2",
    }
    assert "assessment-battery" in SCENARIO_CONFIGS  # Historical tasks keep their original source set.
    for scenario_id, config in SCENARIO_CONFIGS.items():
        assert scenario_id.startswith("assessment-")
        assert len(config["terms"]) >= 4
        assert len(config["papers"]) >= 2
        for paper in config["papers"]:
            assert paper["doi"].startswith("10.")
            assert paper["source_url"].startswith("https://europepmc.org/articles/PMC")
            assert paper["title"] and paper["abstract"] and paper["verified_at"]


def test_case_claims_do_not_promote_technology_or_application_names():
    graph = ContentAssessmentGraph()
    fields = ResearchNineFields(
        research_goal="在不降低实验质量的前提下降低单批实验成本20%",
        research_content="评估芯片制造与细胞培养环节的成本并识别主要瓶颈",
        technical_route="采集流程数据建立成本模型并通过对照批次验证优化效果",
        key_technologies=["流程挖掘", "成本核算"],
        application_scenarios=["器官芯片研发项目管理"],
    )
    raw = "\n".join([fields.research_goal, fields.research_content, fields.technical_route,
                     "；".join(fields.key_technologies), "；".join(fields.application_scenarios)])
    state = graph.create_initial_state(raw, scenario_id="assessment-cost")
    state.nine_fields = fields
    state.nine_fields_confirmed = True
    graph._claim_extraction(state)
    assert [claim.text for claim in state.claims] == [fields.research_goal, fields.research_content, fields.technical_route]
    assert state.nine_fields.key_technologies == ["流程挖掘", "成本核算"]
    assert all(claim.source_span in raw for claim in state.claims)


def test_comparison_model_reads_the_same_case_priors_as_rule_comparison(monkeypatch):
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("降低器官芯片制造成本并验证批次优化效果", scenario_id="assessment-cost")
    claim = Claim(claim_id="C-001", text=state.raw_user_input, assertion_type="target")
    state.claims = [claim]
    state.evidence_records = [EvidenceRecord(
        evidence_id=f"E-L-{index:03d}", title="器官芯片概述", source_type="literature",
        abstract="器官芯片是微流控研究平台。", evidence_level="B", claim_ids=[claim.claim_id],
    ) for index in range(1, 6)]
    graph._append_scenario_evidence(state)
    graph._comparison(state)
    selected = graph._comparison_evidence_for_claim(state, claim)
    assert 3 <= len(selected) <= 5
    assert all(item.retrieval_batch_id == "homepage-scenario-v1:assessment-cost" for item in selected[:2])
    assert any("fabrication" in item.title.lower() for item in selected[:2])


def test_target_comparison_prompt_separates_method_basis_and_project_results():
    from app.agents.content_assessment.prompts import comparison_prompt
    prompt = comparison_prompt(claims=[{"claim_id": "C-001", "assertion_type": "target"}], evidence=[])
    assert "待实测验证" in prompt
    assert "不是外部支持证据" in prompt
    assert "不得称为全文实测证明" in prompt


def test_indicator_assessment_separates_comparable_directional_missing_and_project_result():
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("评估成本、周期、返工率和重复性")
    state.indicators = [
        Indicator(indicator_id="I-001", name="成本降低", value="20", unit="%", status="target",
                  source_claim_id="C-001", source_document_id="user_material", source_span="目标成本降低20%"),
        Indicator(indicator_id="I-002", name="实验周期缩短", value="25", unit="%", status="target",
                  source_claim_id="C-002", source_document_id="user_material", source_span="计划实验周期缩短25%"),
        Indicator(indicator_id="I-003", name="返工率", value="8", unit="%", status="target",
                  source_claim_id="C-003", source_document_id="user_material", source_span="返工率低于8%"),
        Indicator(indicator_id="I-004", name="重复性", value="90", unit="%", status="completed",
                  source_claim_id="C-004", source_document_id="user_material", source_span="实测重复性达到90%"),
        Indicator(indicator_id="I-005", name="样本一致性", value="95", unit="%", status="target",
                  source_claim_id="C-005", source_document_id="user_material", source_span="目标样本一致性95%"),
    ]
    state.claims = [Claim(claim_id=f"C-{index:03d}", text=item.source_span)
                    for index, item in enumerate(state.indicators, 1)]
    state.evidence_records = [
        EvidenceRecord(evidence_id="E-L-001", title="成本对照", source_type="literature",
                       evidence_level="A", access_status="authorized", fulltext_available=True,
                       anchor_verified=True, char_start=0, char_end=30, section_ref="Results",
                       excerpt="成本降低的同口径对照数据", comparison_fields={"metric": "成本降低10%", "baseline": "10%"}),
        EvidenceRecord(evidence_id="E-L-002", title="周期方法综述", source_type="literature",
                       evidence_level="B", abstract="实验周期优化方法的方向性综述。"),
    ]
    matrix = {field: "covered" for field in ("object", "method", "metric", "baseline", "validation")}
    state.claim_evidence_links = [
        ClaimEvidenceLink(claim_id="C-001", evidence_id="E-L-001", relation="supports",
                          field_matrix=matrix, evidence_level="A"),
        ClaimEvidenceLink(claim_id="C-002", evidence_id="E-L-002", relation="partially_supports",
                          evidence_level="B"),
    ]
    graph._evaluate_indicators(state)
    comparable, directional, missing, measured, planned = state.indicator_validations
    assert comparable.literature_baseline_status == "comparable"
    assert comparable.literature_baseline == "10%" and comparable.evidence_refs == ["E-L-001"]
    assert "相差10个百分点" in comparable.assessment_conclusion
    assert comparable.project_achievement_status == "not_provided"
    assert directional.literature_baseline_status == "directional" and directional.evidence_refs == ["E-L-002"]
    assert not directional.literature_baseline
    assert missing.literature_baseline_status == "none" and not missing.evidence_refs
    assert measured.project_achievement_status == "documented" and measured.project_result == "实测重复性达到90%"
    assert planned.project_achievement_status == "not_provided" and "未提供" in planned.assessment_conclusion
    graph._quality_gate(state)
    assert state.report_metrics.indicator_assessed_count == 5
    assert state.report_metrics.indicator_evidence_count == 1
    assert state.report_metrics.indicator_coverage == 0.2


def test_numeric_indicator_rejects_mismatched_baseline_unit():
    graph = ContentAssessmentGraph()
    state = graph.create_initial_state("目标成本降低20%")
    state.indicators = [Indicator(indicator_id="I-001", name="成本降低", value="20", unit="%",
                                  status="target", source_claim_id="C-001")]
    state.evidence_records = [EvidenceRecord(
        evidence_id="E-L-001", title="成本对照", source_type="literature", evidence_level="A",
        access_status="authorized", fulltext_available=True, anchor_verified=True,
        char_start=0, char_end=30, section_ref="Results", excerpt="成本降低10%",
        comparison_fields={"metric": "成本降低10%", "baseline": "100元"},
    )]
    state.claim_evidence_links = [ClaimEvidenceLink(
        claim_id="C-001", evidence_id="E-L-001", relation="supports", evidence_level="A",
        field_matrix={key: "covered" for key in ("object", "method", "metric", "baseline", "validation")},
    )]
    graph._evaluate_indicators(state)
    result = state.indicator_validations[0]
    assert result.literature_baseline_status == "directional"
    assert result.literature_baseline == ""
    assert "同口径数值" not in result.assessment_basis


def test_homepage_cost_case_assesses_all_four_indicators_without_claiming_results(monkeypatch):
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    graph = ContentAssessmentGraph()
    fields = ResearchNineFields(
        research_direction="器官芯片研发流程的成本与周期协同优化",
        research_goal="将单批实验直接成本降低20%，周期缩短25%",
        research_content="评估芯片设计、耗材、细胞培养和返工环节",
        technical_route="采集六个月数据，建立价值流模型并验证优化效果",
        key_technologies=["流程挖掘", "成本核算"],
        evaluation_indicators=["单批直接成本降低不低于20%", "平均实验周期缩短不低于25%",
                               "返工率低于8%", "关键实验结果重复性不低于90%"],
        timeline="第1—9个月", application_scenarios=["器官芯片研发项目管理"],
        expected_outcomes=["成本与周期优化报告"],
    )
    raw = "\n".join(["项目名称：器官芯片研发成本与实验周期优化"] +
                    [f"{key}：{value}" for key, value in (
                        ("研究方向", fields.research_direction), ("研究目标", fields.research_goal),
                        ("研究内容", fields.research_content), ("技术路线", fields.technical_route),
                        ("考核指标", "；".join(fields.evaluation_indicators)), ("时间节点", fields.timeline),
                    )])
    state = graph.create_initial_state(raw, scenario_id="assessment-cost")
    state.nine_fields = fields
    state.nine_fields_confirmed = True
    graph._claim_extraction(state)
    graph._append_scenario_evidence(state)
    graph._comparison(state)
    graph._assessment_stage(state)
    graph._quality_gate(state)
    assert len(state.indicators) == len(state.indicator_validations) == 4
    assert state.report_metrics.indicator_assessed_count == 4
    assert all(item.assessment_conclusion for item in state.indicator_validations)
    assert all(item.project_achievement_status == "not_provided" for item in state.indicator_validations)
    assert state.report_metrics.indicator_evidence_count == 0


def test_all_homepage_scenarios_complete_the_calibrated_rule_flow(monkeypatch):
    monkeypatch.setattr(graph_module, "LITERATURE_SEARCH_API_URL", "")
    fields = ResearchNineFields(
        research_direction="推荐案例的完整研究方向",
        research_goal="建立可验证的技术体系并完成代表性场景验证",
        research_content="研究核心方法、关键参数、质量控制和应用验证",
        technical_route="完成材料设计、对照试验、指标验证和成果评审",
        key_technologies=["核心技术", "质量控制"],
        evaluation_indicators=["关键指标不低于90%"],
        timeline="18个月",
        application_scenarios=["代表性应用场景"],
        expected_outcomes=["技术规范和验证报告"],
    )
    raw = "\n".join([
        "项目名称：首页推荐内容评估案例",
        f"研究方向：{fields.research_direction}", f"研究目标：{fields.research_goal}",
        f"研究内容：{fields.research_content}", f"技术路线：{fields.technical_route}",
        f"关键技术：{'；'.join(fields.key_technologies)}", f"考核指标：{'；'.join(fields.evaluation_indicators)}",
        f"时间节点：{fields.timeline}", f"应用场景：{'；'.join(fields.application_scenarios)}",
        f"预期成果：{'；'.join(fields.expected_outcomes)}",
    ])

    for scenario_id in SCENARIO_CONFIGS:
        graph = ContentAssessmentGraph()
        state = graph.create_initial_state(raw, scenario_id=scenario_id)
        state.nine_fields = fields.model_copy(deep=True)
        state.nine_fields_confirmed = True
        graph._claim_extraction(state)
        asyncio.run(graph._retrieval(state))
        graph._comparison(state)
        graph._assessment_stage(state)
        graph._quality_gate(state)

        assert not state.nine_fields.missing_required(), scenario_id
        assert len(state.query_plans) == len(STANDARD_QUERY_GROUPS), scenario_id
        assert {item.claim_id for item in state.claim_evidence_links} == {item.claim_id for item in state.claims}, scenario_id
        assert state.report_metrics.directional_claim_coverage == 1, scenario_id
        assert not state.quality_gate.blockers, scenario_id
        assert state.quality_gate.report_eligibility == "exploratory", scenario_id
