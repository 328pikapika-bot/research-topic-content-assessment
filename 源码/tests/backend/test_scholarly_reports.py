"""Reader reports must retain evidence and reject unfinished analyses."""
import asyncio
import io
from docx import Document
from app.agents.content_assessment.graph import ContentAssessmentGraph
from app.agents.content_assessment.schemas import Claim, EvidenceRecord, ClaimEvidenceLink, ContentAssessmentState, QueryPlan
from app.agents.content_assessment.scholarly_report import selected_sources, validated_paragraphs, point_packet, publication_gaps
from app.agents.content_assessment.report_composition import markdown, freeze_snapshot
from app.services.content_assessment_report import build_content_assessment_docx


def sample():
    s = ContentAssessmentState(raw_user_input="拟比较微流控肝脏芯片灌流条件。")
    s.claims = [Claim(claim_id="C1", text=s.raw_user_input, searchable_terms=["liver", "perfusion"], assertion_type="planned")]
    s.evidence_records = [
        EvidenceRecord(evidence_id="E1", title="Liver chip perfusion", abstract="Liver chip perfusion assessed with flow monitoring.", year=2024, source_type="literature", doi="10.1/liver"),
        EvidenceRecord(evidence_id="E2", title="Galaxy morphology", abstract="Stellar orbits and dark matter distributions.", year=2023, source_type="literature"),
        EvidenceRecord(evidence_id="E3", title="Liver chip perfusion failure", abstract="Liver chip perfusion can fail under excessive flow.", source_type="literature"),
    ]
    s.claim_evidence_links = [ClaimEvidenceLink(claim_id="C1", evidence_id="E2", relation="supports"),
        ClaimEvidenceLink(claim_id="C1", evidence_id="E3", relation="contradicts")]
    s.report_preferences.artifact_type = s.report_preferences.resolved_artifact_type = "paper"
    return s


def test_unrelated_source_rejected_and_counterexample_retained():
    s = sample()
    assert {e.evidence_id for e in selected_sources(s, s.claims[0])} == {"E1", "E3"}
    assert selected_sources(s, s.claims[0], limit=1)[0].evidence_id == "E3"


def test_chinese_point_uses_translated_query_but_requires_source_content():
    s = sample()
    s.claims[0].text = "建立屏障类器官芯片制备、培养、检测和结果报告规范。"
    s.claims[0].searchable_terms = ["器官芯片", "屏障", "质量控制"]
    s.query_plans = [QueryPlan(claim_id="C1", query=
        "organ-on-chip barrier permeability quality control standardization reproducibility")]
    s.evidence_records = [
        EvidenceRecord(evidence_id="related", source_type="literature",
            title="Organ-on-a-chip barrier monitoring",
            abstract="We measure barrier permeability in an organ-on-a-chip under flow."),
        EvidenceRecord(evidence_id="unrelated", source_type="literature",
            title="Quality control standardization in manufacturing",
            abstract="Manufacturing quality control improves reproducibility.",
            claim_ids=["C1"], retrieval_query=s.query_plans[0].query),
        EvidenceRecord(evidence_id="metadata", source_type="literature",
            title="Organ-on-chip barrier permeability"),
        EvidenceRecord(evidence_id="soil", source_type="literature",
            title="Soil barrier permeability",
            abstract="We measure barrier permeability in clay soil under water flow."),
    ]
    assert [e.evidence_id for e in selected_sources(s, s.claims[0])] == ["related"]
    s.query_plans[0].claim_id = "OTHER"
    s.query_plans[0].claim_ids = ["OTHER"]
    assert not selected_sources(s, s.claims[0])


def test_generated_comparison_fields_cannot_manufacture_source_relevance():
    s = sample()
    s.evidence_records[1].comparison_fields = {"object":"liver chip perfusion"}
    assert "E2" not in {e.evidence_id for e in selected_sources(s, s.claims[0])}
    s.claims[0].claim_fields = {"method":"采用CLIP contrastive image-text learning比较图文编码。"}
    s.claims[0].searchable_terms = ["scientific", "document", "retrieval", "CLIP"]
    s.evidence_records.append(EvidenceRecord(evidence_id="E4", source_type="literature",
        title="Scientific information retrieval", abstract="Document retrieval with probability ranking.",
        comparison_fields={"method":"CLIP contrastive image-text learning"}))
    assert not selected_sources(s, s.claims[0])


def test_grounded_method_comparator_is_eligible_without_requiring_the_same_method_name():
    from app.agents.content_assessment.reader_comparison import save_reader_comparison
    state = sample()
    claim = state.claims[0]
    claim.text = "Biomedical evidence retrieval with CLIP contrastive image-text learning."
    claim.searchable_terms = ["biomedical", "evidence", "retrieval", "CLIP"]
    claim.claim_fields = {"method": "CLIP contrastive image-text learning"}
    state.raw_user_input = claim.text
    state.evidence_records = [EvidenceRecord(evidence_id="different", source_type="literature",
        title="Biomedical evidence retrieval with BM25", abstract="Biomedical evidence retrieval uses BM25 lexical ranking."),
        EvidenceRecord(evidence_id="noise", source_type="literature", title="Galaxy morphology", abstract="Stellar orbit measurement.")]
    assert not selected_sources(state, claim)
    for evidence in state.evidence_records:
        save_reader_comparison(state, claim, evidence, {"reader_comparison": {"difference": [{
            "text": "材料采用CLIP图文对齐，文献采用BM25词项排序。", "material_quote": claim.text,
            "literature_quote": evidence.abstract}]}})
    # A valid comparison can explain a different method, but cannot turn an
    # unrelated research object into a relevant source.
    assert [e.evidence_id for e in selected_sources(state, claim)] == ["different"]


def test_public_abstract_reconstruction_preserves_source_scope():
    from app.agents.content_assessment.public_literature import abstract_text
    assert abstract_text({"Dense":[0], "retrieval":[2], "passage":[1]}) == "Dense passage retrieval"
    assert abstract_text(None) == ""


def test_same_acronym_in_unrelated_domain_and_duplicate_title_are_excluded():
    s = sample(); s.claims[0].text = "SPECTER citation informed scientific document embeddings"
    s.claims[0].searchable_terms = ["SPECTER", "citation", "embeddings"]
    s.claims[0].claim_fields = {"method":s.claims[0].text}
    s.evidence_records = [
        EvidenceRecord(evidence_id="legal", title="The Omnipresent Specter of Omnicare",
            abstract="The specter of legal proceedings affects scientific information disclosure.", source_type="literature"),
        EvidenceRecord(evidence_id="method", title="SPECTER: Citation-informed embeddings",
            abstract="Scientific document embeddings use citation relationships.", source_type="literature"),
        EvidenceRecord(evidence_id="duplicate", title="SPECTER: Citation-informed embeddings",
            abstract="Scientific document embeddings use citation relationships.", source_type="literature", doi="10.1/duplicate"),
    ]
    assert [e.evidence_id for e in selected_sources(s,s.claims[0])] == ["method"]


def test_bibliography_metadata_is_not_method_evidence():
    from app.agents.content_assessment.scholarly_report import readable_research_content
    e = EvidenceRecord(evidence_id="DPR", source_type="literature",
        title="Dense Passage Retrieval for Open-Domain Question Answering",
        abstract="Vladimir Karpukhin, Barlas Oguz. Proceedings of the 2020 Conference on Empirical Methods in Natural Language Processing (EMNLP). 2020.",
        authors=["Vladimir Karpukhin", "Barlas Oguz"])
    assert not readable_research_content(e)
    e.abstract = "We propose dense passage retrieval based on a dual encoder and evaluate retrieval performance across different question answering datasets. The experiments compare sparse and dense representations."
    assert readable_research_content(e)


def test_source_review_corrects_attribution_and_fails_closed(monkeypatch):
    from app.agents.content_assessment.scholarly_report import review_source_assertions
    s=sample(); g=ContentAssessmentGraph(); packet=point_packet(s,s.claims[0])
    rows={"comparison":[{"text":"原研究使用不存在的数据集。","evidence_ids":["E1"]}]}
    async def corrected(*args,**kwargs):
        return {"checked":True,"replacements":[{"key":"comparison","index":0,"text":"原研究讨论灌流监测。","evidence_ids":["E1"]}]}
    monkeypatch.setattr(g,"_request_model_json",corrected)
    assert asyncio.run(review_source_assertions(g,s,packet,rows))["comparison"][0]["text"] == "原研究讨论灌流监测。"
    async def unavailable(*args,**kwargs): return None
    monkeypatch.setattr(g,"_request_model_json",unavailable)
    assert asyncio.run(review_source_assertions(g,s,packet,rows)) == {"comparison":[]}


def test_repeated_analysis_sentences_are_merged_without_losing_conditions():
    from app.agents.content_assessment.scholarly_report import deduplicate_analysis
    sentence = '项目拟通过人工核对每句科研判断与引用片段的对应关系，并保留逐句判断与源文本位置。'
    rows = {'comparison': [{'text': sentence, 'evidence_ids':['E1']}],
            'recommendations': [{'text': sentence + '建议记录不同查询类型的核对耗时。', 'evidence_ids':['E1']},
                                {'text': sentence.replace('拟通过', '不通过'), 'evidence_ids':['E1']},
                                {'text': sentence, 'evidence_ids':['E2']}]}
    cleaned = deduplicate_analysis(rows)
    assert cleaned['recommendations'][0]['text'] == '建议记录不同查询类型的核对耗时。'
    assert len(cleaned['recommendations']) == 3
    assert '不通过' in cleaned['recommendations'][1]['text']


def test_generated_paragraphs_reject_unknown_sources_and_invented_numbers():
    s = sample(); packet = point_packet(s, s.claims[0])
    assert not validated_paragraphs([{"text":"准确率达到99.9%。", "evidence_ids":["E1"]}], packet)
    assert not validated_paragraphs([{"text":"已有研究采用灌流。", "evidence_ids":["UNKNOWN"]}], packet)
    assert not validated_paragraphs([{"text":"结论强度较高。", "evidence_ids":["E1"]}], packet)
    assert validated_paragraphs([{"text":"文献讨论灌流条件下的监测方法。", "evidence_ids":["E1"]}], packet)


def test_report_prose_uses_paper_names_and_rejects_unknown_execution_ids():
    s = sample(); packet = point_packet(s, s.claims[0])
    result = validated_paragraphs([{"text":"E1讨论灌流监测。", "evidence_ids":["E1"]}], packet)
    assert result[0]["text"] == "《Liver chip perfusion》讨论灌流监测。"
    assert not validated_paragraphs([{"text":"E-OA-unknown讨论灌流。", "evidence_ids":["E1"]}], packet)


def test_topic_source_review_requires_complete_sections_and_bound_citations():
    from app.agents.topic_selection.scholarly_report import valid_report
    packet = {"question":"灌流监测研究", "sources":[{"evidence_id":"E1","content":"灌流监测"}], "candidates":[{"id":"C1"}]}
    paragraph = {"text":"拟研究灌流监测的适用条件。", "evidence_ids":["E1"]}
    report = {key:[paragraph] for key in ("summary","progress","opportunities","recommendations")}
    report["candidates"] = [{"id":"C1", "reason":"比较灌流监测条件。", "design":"拟设置流量对照。", "conditions":"以相同读出进行比较。", "evidence_ids":["E1"]}]
    assert valid_report(report,packet)
    assert not valid_report({**report,"progress":[]},packet)
    assert not valid_report({**report,"summary":[{"text":"结果达到99.9%。","evidence_ids":["E1"]}]},packet)
    assert not valid_report({**report,"summary":[{"text":"已有研究讨论灌流。","evidence_ids":["E-OA-unknown"]}]},packet)


def test_proposed_topic_design_can_name_metrics_without_claiming_results():
    from app.agents.topic_selection.scholarly_report import numeric_values, valid_report
    assert numeric_values('316L、BM25和caspase-3/7用于比较，重复10次，准确率99.9%。') == ['10', '99.9']
    packet = {'question':'灌流研究', 'sources':[{'evidence_id':'E1','content':'灌流比较'}], 'candidates':[{'id':'C1'}]}
    report = {k:[{'text':'拟比较灌流条件。','evidence_ids':['E1']}] for k in ('summary','progress','opportunities','recommendations')}
    report['candidates'] = [{'id':'C1','reason':'拟比较灌流条件。','design':'拟采用相同基线，比较准确率和召回率。','conditions':'建议以相同读出作为比较条件。','evidence_ids':[]}]
    # The candidate reason remains a literature argument and needs a source.
    report['candidates'][0]['evidence_ids'] = ['E1']
    report['recommendations'] = [{'text':'建议将相同读出作为基线，记录准确率与召回率。','evidence_ids':[]}]
    assert valid_report(report,packet)
    report['recommendations'][0]['text'] = '建议重复10次，准确率达到99.9%。'
    assert not valid_report(report,packet)


def test_topic_normalization_preserves_exact_candidate_set_and_source_facts():
    from app.agents.topic_selection.scholarly_report import normalize_report, valid_report
    packet = {"question":"灌流监测研究", "sources":[{"evidence_id":"E-known", "title":"灌流条件研究", "content":"灌流监测"}],
              "candidates":[{"id":"C2", "title":"灌流监测条件"}, {"id":"C1", "title":"灌流对照"}]}
    paragraph = {"text":"E-known讨论灌流监测。", "evidence_ids":["E-known"]}
    report = {key:[paragraph.copy()] for key in ("summary","progress","opportunities","recommendations")}
    report['candidates'] = [{'id':cid, 'reason':'拟比较灌流条件。', 'design':'拟统一读出。', 'conditions':'适用于灌流监测。',
                             'evidence_ids':['E-known']} for cid in ('C1','C2')]
    result = normalize_report({'draft':report}, packet)
    assert [c['id'] for c in result['candidates']] == ['C2','C1']
    assert result['summary'][0]['text'] == '《灌流条件研究》讨论灌流监测。'
    assert valid_report(result,packet)
    report['candidates'].append(report['candidates'][0])
    assert not valid_report(normalize_report(report,packet),packet)
    result['summary'][0]['text'] = '准确率为99.9%。'
    assert not valid_report(result,packet)


def test_topic_review_cannot_publish_invalid_source_corrections():
    from types import SimpleNamespace
    from app.agents.topic_selection.scholarly_report import review_report
    packet = {"question":"灌流研究", "sources":[{"evidence_id":"E-known","title":"灌流研究","content":"灌流监测"}],
              "candidates":[{"id":"C1","title":"灌流对照"}]}
    report = {key:[{'text':'拟比较灌流条件。','evidence_ids':['E-known']}] for key in ('summary','progress','opportunities','recommendations')}
    report['candidates'] = [{'id':'C1','reason':'拟比较灌流条件。','design':'拟统一读出。','conditions':'适用于灌流监测。','evidence_ids':['E-known']}]
    state = SimpleNamespace(stage_artifacts={})
    class Agent:
        async def _stage_model_json(self, *args, **kwargs):
            return {'checked':True,'replacements':[{'key':'summary','index':0,'field':'text','text':'准确率为99.9%。','evidence_ids':['E-known']}]}
    assert asyncio.run(review_report(Agent(),state,None,packet,report)) is None
    assert report['summary'][0]['text'] == '拟比较灌流条件。'


def test_unfinished_main_analysis_stays_on_task_page(monkeypatch):
    s = sample(); graph = ContentAssessmentGraph()
    async def failed(**kwargs): return None
    monkeypatch.setattr(graph, "_request_model_json", lambda *args, **kwargs: failed(**kwargs))
    asyncio.run(graph._report_stage(s))
    assert not s.confirmation.needed and s.current_stage == "failed"
    assert s.output_json["generation_recovery"]["reason"] == "report_drafting"
    assert s.resume_from_stage == "report"
    assert s.report_snapshot is None
    s.evidence_records = s.evidence_records[1:2]
    asyncio.run(graph._report_stage(s))
    assert not s.confirmation.needed
    assert s.output_json["generation_recovery"]["reason"] == "report_sources"
    assert s.resume_from_stage == "retrieval"
    assert s.report_snapshot is None


def test_failed_conclusion_does_not_fabricate_synthesis_or_clear_saved_chapters(monkeypatch):
    from app.agents.content_assessment.scholarly_report import draft
    s = sample()
    judgement = {"text": "研究着眼于肝脏芯片灌流条件的适用性。", "evidence_ids": ["E1"], "dimension": "innovation"}
    s.output_json["scholarly_drafts"] = {"C1": {key: [judgement.copy()]
        for key in ("progress", "comparison", "judgement", "evaluation")}}
    s.output_json["scholarly_source_reviews"] = {"C1": {"status": "completed"}}
    graph = ContentAssessmentGraph()
    async def failed(*args, **kwargs):
        return None
    monkeypatch.setattr(graph, "_request_model_json", failed)
    asyncio.run(draft(graph, s))
    assert not s.output_json.get("scholarly_conclusion")
    assert s.output_json["scholarly_drafts"]["C1"]["judgement"] == [judgement]
    assert s.output_json["scholarly_synthesis_completion"]["conclusion"]["repair_attempted"]


def test_v7_freezes_research_chapters_and_all_formats_use_selected_sources():
    s = sample()
    s.claims[0].display_title = "肝脏芯片灌流条件设计"
    s.output_json["scholarly_drafts"] = {"C1": {key:[{"text":text,"evidence_ids":["E1"]}]
        for key,text in {"progress":"代表文献考察了灌流监测。", "comparison":"本方案将灌流条件作为比较变量。",
                         "judgement":"研究着眼于监测条件的适用性。", "evaluation":"材料中的流量读出与对照可以用于比较灌流条件。"}.items()}}
    s.output_json["scholarly_drafts"]["C1"]["progress"].append({
        "text":"字段覆盖0/7；缺少方法字段。", "evidence_ids":["E1"]})
    assert not publication_gaps(s)
    s.output_json["report_source_selection"] = {"C1":["E1","E3"]}
    ContentAssessmentGraph()._report(s)
    snap = s.report_snapshot.model_copy(deep=True)
    assert snap.template_version == "ContentAssessmentResearchReportV7"
    assert snap.schema_version == "report-snapshot-v9"
    assert [x.section_key for x in snap.sections] == ["executive", "scope", "object", "progress", "methods",
        "comparison", "judgement", "evaluation", "conclusion", "references", "evidence_crosswalk",
        ]
    assert "material_details" not in {x.section_key for x in snap.sections}
    text = markdown(s)
    assert "Galaxy" not in text
    assert not any(word in text for word in ["结论强度","内部评分","探索性评估报告"])
    assert "字段覆盖0/7" not in text and "缺少方法字段" not in text
    assert "肝脏芯片灌流条件设计" in text
    raw,_ = build_content_assessment_docx(s)
    assert "Galaxy" not in "".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
    s.claims.clear(); s.evidence_records.clear()
    assert markdown(s) == text
    assert s.report_snapshot.sections == snap.sections


def test_legacy_snapshot_export_does_not_upgrade_template():
    from report_rich_fixture import rich_state
    s = rich_state("standard")
    before = s.report_snapshot.model_copy(deep=True)
    markdown(s)
    assert s.report_snapshot == before
    assert s.report_snapshot.template_version == "ContentAssessmentResultReportV4"


def test_reader_year_chart_uses_cited_sources_instead_of_old_retrieval_themes():
    from app.services.content_assessment_report import _year_theme_series
    data = {"template_version": "ContentAssessmentResultReportV5", "evidence": sample().evidence_records[:2],
            "trend": {"themes": [{"label":"旧主题", "evidence_ids":["irrelevant"]}]}}
    assert _year_theme_series(data) == {"报告引用文献": [(2023,1),(2024,1)]}


def test_v7_report_charts_use_year_counts_and_source_backed_point_judgements():
    from app.agents.content_assessment.schemas import NoveltyDecision
    from app.services.content_assessment_report import _annual_literature_counts, _claim_judgement_rows

    claim = Claim(claim_id="C-001", text="构建高稳定性器官芯片灌流系统。", display_title="器官芯片灌流系统")
    evidence = [
        EvidenceRecord(evidence_id="E-1", title="灌流芯片研究甲", source_type="literature", year=2024),
        EvidenceRecord(evidence_id="E-2", title="灌流芯片研究乙", source_type="literature", year=2024),
        EvidenceRecord(evidence_id="E-3", title="灌流芯片研究丙", source_type="literature"),
    ]
    assert _annual_literature_counts({"evidence": evidence}) == {
        "points": [{"year": 2024, "count": 2}], "undated": 1, "total": 3,
    }
    decision = NoveltyDecision(claim_id="C-001", label="存在相近先例", rationale="已有相关路线。", evidence_ids=["E-1"])
    rows = _claim_judgement_rows({"claims": [claim], "decisions": [decision], "citation_numbers": {"E-1": 7}})
    assert rows == [{"point": "器官芯片灌流系统", "judgement": "存在相近研究", "references": [7]}]


def test_v7_chart_selection_does_not_create_coverage_placeholders():
    from types import SimpleNamespace
    from app.services.content_assessment_report import _select_chart_keys

    data = {"template_version": "ContentAssessmentResearchReportV7", "profile": SimpleNamespace(document_records=[]),
        "claims": [], "queries": [], "stage_trace": [], "evidence": [], "evidence_by_id": {}, "links": [],
        "decisions": [], "scorecard": [], "market_cards": [], "competitive_cards": [], "regulatory_cards": [],
        "trend": {}, "indicator_validations": [], "metrics": SimpleNamespace(), "quality_gate": SimpleNamespace(warnings=[])}
    selected, manifest = _select_chart_keys(data, ["years", "market", "competition", "radar"], include_coverage=False)
    assert selected == []
    assert all(not entry["included"] for entry in manifest)


def test_abstract_noncoverage_does_not_establish_a_field_gap():
    from app.agents.content_assessment.scholarly_report import validated_paragraphs, point_packet
    s = sample()
    packet = point_packet(s, s.claims[0])
    eid = packet['literature'][0]['id']
    for text in ['该方向研究尚属空白。', '现有文献中未见系统实施的报道。', '此方法尚未在该体系中验证。',
                 '指南未计划引入微流控方法。', '现有研究采用静态培养并观察细胞增殖，但针对肝脏芯片的灌流条件变化规律尚未系统研究。',
                 '该文献介绍培养条件，但未在肝脏芯片场景开展系统验证。']:
        assert not validated_paragraphs([{'text':text, 'evidence_ids':[eid]}], packet)
    assert validated_paragraphs([{'text':'拟比较灌流条件与静态培养下的监测读出。', 'evidence_ids':[eid]}], packet)


def test_guideline_execution_status_does_not_consume_later_validation_clause():
    from app.agents.content_assessment.scholarly_report import validated_paragraphs, point_packet
    state = sample()
    packet = point_packet(state, state.claims[0])
    eid = packet['literature'][0]['id']
    text = '本材料为指南草案，其复现性验证为拟开展任务，尚未实际执行，因此其实际效果和可操作性有待验证结果支持。'
    assert validated_paragraphs([{'text': text, 'evidence_ids': [eid]}], packet)
    # A legitimate material-status clause does not excuse a separate claim
    # about the entire field, including one after a comma.
    for assertion in ['此方法尚未在该体系中验证。', '现有研究采用静态培养，但尚未开展灌流验证。']:
        assert not validated_paragraphs([{'text': text + assertion, 'evidence_ids': [eid]}], packet)


def test_report_preserves_material_design_without_empty_or_duplicate_fields():
    from app.agents.content_assessment.scholarly_report import build
    s = sample()
    s.claims[0].claim_fields = {'object':'观察肝脏芯片。', 'method':'比较静态与灌流培养。',
        'baseline':'静态培养', 'metric':'流量读出',
        'validation':'观察肝脏芯片。比较静态与灌流培养。固定培养条件并保留重复记录。', 'data':''}
    body = next(x for x in build(s) if x.section_key == 'object')
    text = '\n'.join(b.text for b in body.blocks)
    assert text.count('观察肝脏芯片。') == 1
    assert text.count('比较静态与灌流培养。') == 1
    assert '固定培养条件并保留重复记录。' in text
    assert '观察指标：流量读出' in text
    assert '缺少' not in text


def test_topic_repair_targets_include_unresolved_identifiers_and_unbound_proposals():
    from app.agents.topic_selection.scholarly_report import invalid_paragraphs
    packet = {'question':'比较灌流条件', 'sources':[{'evidence_id':'E-known','content':'灌流监测'}],
        'candidates':[]}
    draft = {'summary':[{'text':'E-unknown讨论灌流监测。','evidence_ids':['E-known']}],
        'opportunities':[{'text':'建议重复10次进行验证。','evidence_ids':[]}],
        'progress':[{'text':'研究采用灌流监测。','evidence_ids':[]}]}
    targets = invalid_paragraphs(draft, packet)
    assert {row['key'] for row in targets} == {'summary','opportunities','progress'}


def test_public_repository_fallback_keeps_preprint_and_abstract_scope():
    import httpx
    from app.agents.content_assessment.public_literature import arxiv_records
    feed = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/abs/2005.11401v4</id><title>Retrieval-Augmented Generation</title>
    <summary>We compare retrieved context and generation methods.</summary>
    <published>2020-05-22T00:00:00Z</published><author><name>Example Author</name></author>
    </entry><entry><id>https://untrusted.invalid/abs/1</id><title>Invalid</title>
    <summary>Invalid repository entry.</summary><published>2020-01-01</published></entry></feed>'''
    async def run(years):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=feed))) as client:
            return await arxiv_records(client, sample().claims[0], 'Retrieval-Augmented Generation', years)
    records = asyncio.run(run([]))
    assert len(records) == 1
    assert records[0].source_url == 'https://arxiv.org/abs/2005.11401v4'
    assert records[0].venue == 'arXiv（预印本）'
    assert records[0].abstract == 'We compare retrieved context and generation methods.'
    assert records[0].access_status == 'abstract_only'
    assert not records[0].fulltext_available
    assert not asyncio.run(run([2021,2026]))
