from app.agents.content_assessment.schemas import Claim, EvidenceRecord, ContentAssessmentState
from app.agents.content_assessment.reader_comparison import save_reader_comparison, save_reader_analysis


def save(**content):
    state = ContentAssessmentState(raw_user_input="材料采用时间切分，在高温条件下性能下降。")
    claim = Claim(claim_id="C1", text=state.raw_user_input)
    evidence = EvidenceRecord(evidence_id="E1", title="比较研究", source_type="literature",
        abstract="文献采用随机切分，在室温条件下进行测试。")
    save_reader_comparison(state, claim, evidence, {"reader_comparison": content})
    return state.output_json["reader_comparisons"]["C1"]["E1"]


def anchored(text):
    return {"text": text, "material_quote": "材料采用时间切分，在高温条件下性能下降。",
        "literature_quote": "文献采用随机切分，在室温条件下进行测试。"}


def test_reader_comparison_keeps_real_methods_conditions_and_negative_findings():
    result = save(difference=[anchored("材料采用时间切分，文献采用随机切分。")],
        conditions=[anchored("材料在高温条件下性能下降，文献在室温条件下进行测试。")])
    assert len(result["difference"]) == len(result["conditions"]) == 1
    assert result["similarity"] == []


def test_diagnostics_one_sided_and_fabricated_data_cannot_be_reader_comparisons():
    result = save(difference=[anchored("摘要证据无法确认全文。"), anchored("文献未涉及目标方法。"),
        anchored("指标为99%。"), {**anchored("双方比较。"), "literature_quote": ""}],
        conditions=[anchored("不能支撑目标。"), {**anchored("实验条件一致。"), "material_quote": "虚构的实验条件"}])
    assert result["difference"] == result["conditions"] == []


def test_no_reader_payload_never_promotes_audit_uncertainty():
    state = ContentAssessmentState(raw_user_input="")
    save_reader_comparison(state, Claim(claim_id="C1", text="材料"),
        EvidenceRecord(evidence_id="E1", title="文献", source_type="literature"), {"uncertainty": "摘要无法确认全文"})
    assert "reader_comparisons" not in state.output_json


def test_real_method_limitation_is_not_treated_as_audit_refusal():
    state = ContentAssessmentState(raw_user_input="该方法不能支持在线更新，使用离线批量训练。")
    claim = Claim(claim_id="C1", text=state.raw_user_input)
    evidence = EvidenceRecord(evidence_id="E1", title="在线研究", source_type="literature",
        abstract="文献采用增量更新方法。")
    text = "材料方法不能支持在线更新，文献采用增量更新方法。"
    save_reader_comparison(state, claim, evidence, {"reader_comparison": {"difference": [{
        "text": text, "material_quote": claim.text, "literature_quote": evidence.abstract}]}})
    assert state.output_json["reader_comparisons"]["C1"]["E1"]["difference"][0]["text"] == text


def test_synthesis_requires_actual_material_and_each_cited_source_and_rejects_invented_numbers():
    material = "材料采用时间切分，在高温条件下性能下降。"
    state = ContentAssessmentState(raw_user_input=material)
    claim = Claim(claim_id="C1", text=material)
    sources = [EvidenceRecord(evidence_id="E1", source_type="literature", title="随机切分",
        abstract="文献采用随机切分，在室温条件下进行测试。"),
        EvidenceRecord(evidence_id="E2", source_type="literature", title="时间外推",
        abstract="文献采用时间外推，在低温条件下进行测试。")]
    anchors = [{"evidence_id": e.evidence_id, "material_quote": material, "literature_quote": e.abstract} for e in sources]
    valid = {"text": "相关研究分别采用随机切分和时间外推，材料则在高温条件下检验时间切分。",
        "evidence_ids": ["E1", "E2"], "anchors": anchors}
    save_reader_analysis(state, claim, sources, {"reader_analysis": {
        "claim_id": "C1", "material_summary": {"text": "材料在高温条件下检验时间切分。", "material_quote": material},
        "literature_summary": [valid, {**valid, "text": "精度达到99%。"},
            {**valid, "evidence_ids": ["E1", "unknown"]}]}})
    result = state.output_json["reader_research_analyses"]["C1"]
    assert result["material_summary"]["text"] == "材料在高温条件下检验时间切分。"
    assert result["literature_summary"] == [valid]
    for invalid in [
        {**valid, "anchors": [anchors[0]]},
        {**valid, "anchors": [anchors[0], {**anchors[1], "literature_quote": sources[0].abstract}]},
        {**valid, "text": "摘要证据无法确认全文。"},
    ]:
        save_reader_analysis(state, claim, sources, {"reader_analysis": {
            "claim_id": "C1", "literature_summary": [invalid]}})
        assert state.output_json["reader_research_analyses"]["C1"]["literature_summary"] == []
    save_reader_analysis(state, claim, sources, {"reader_analysis": {"claim_id": "C2", "literature_summary": [valid]}})
    assert "C2" not in state.output_json["reader_research_analyses"]


def test_generic_category_difference_is_not_accepted_as_research_analysis():
    assert save(difference=[anchored("研究对象不同。"), anchored("研究问题不同。")])["difference"] == []
