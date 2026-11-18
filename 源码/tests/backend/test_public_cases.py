from __future__ import annotations

import gzip
import asyncio
import io
import json
import re
import zipfile

from app.services import public_cases


def _detail(case_id: str) -> dict:
    is_assessment = case_id.startswith("assessment-")
    count = 8 if is_assessment else 9
    traces = [{"stage_key": f"stage-{index}", "status": "completed"} for index in range(count)]
    if is_assessment:
        state = {
            "current_stage": "completed",
            "stage_trace": traces,
            "report_snapshot": {"snapshot_id": "report-v1", "sections": [{"section_key": "summary"}], "citation_numbers": {"E1": 1}},
            "evidence_records": [{"evidence_id": "E1", "source_url": "https://example.org/paper"}],
        }
    else:
        state = {
            "current_stage": "completed",
            "stage_trace": traces,
            "report_snapshot": {"frozen": True},
            "report_plan": {"publication_status": "published", "sections": [{"key": "summary"}], "citation_map": {"E1": 1}},
            "evidence_pool": [{"evidence_id": "E1", "url": "https://example.org/paper"}],
        }
    return {"status": "completed", "current_stage": "completed", "output_markdown": "# 完整报告", "stage_trace": traces, "state": state}


def _write_snapshot(root, case_id: str) -> None:
    directory = root / "snapshots" / public_cases.CASE_SNAPSHOT_VERSION
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"case_id": case_id, "version": public_cases.CASE_SNAPSHOT_VERSION,
               "agent": "assessment" if case_id.startswith("assessment-") else "topic", "detail": _detail(case_id)}
    with gzip.open(directory / f"{case_id}.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)


def test_public_case_catalog_stays_closed_without_all_nine_snapshots_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(public_cases, "CASE_ROOT", tmp_path)
    public_cases._read_snapshot.cache_clear()
    for case_id in public_cases.CASE_IDS[:-1]:
        _write_snapshot(tmp_path, case_id)
    assert public_cases.case_catalog()["ready"] is False

    _write_snapshot(tmp_path, public_cases.CASE_IDS[-1])
    assert public_cases.case_catalog()["ready"] is False

    (tmp_path / "manifest.json").write_text(json.dumps({
        "published": True,
        "version": public_cases.CASE_SNAPSHOT_VERSION,
        "case_ids": list(public_cases.CASE_IDS),
    }), encoding="utf-8")
    catalog = public_cases.case_catalog()
    assert catalog["ready"] is True
    assert catalog["published_case_ids"] == list(public_cases.CASE_IDS)


def test_public_case_requires_complete_report_stages_and_linked_source(tmp_path, monkeypatch):
    monkeypatch.setattr(public_cases, "CASE_ROOT", tmp_path)
    public_cases._read_snapshot.cache_clear()
    _write_snapshot(tmp_path, "topic-urban-heat-v2")
    path = tmp_path / "snapshots" / public_cases.CASE_SNAPSHOT_VERSION / "topic-urban-heat-v2.json.gz"
    stat = path.stat()
    payload, detail = public_cases._read_snapshot("topic-urban-heat-v2", stat.st_mtime_ns, stat.st_size), _detail("topic-urban-heat-v2")
    assert payload is not None
    detail["state"]["report_snapshot"]["frozen"] = False
    assert public_cases._complete_case("topic-urban-heat-v2", detail) is False
    detail = _detail("topic-urban-heat-v2")
    detail["state"]["evidence_pool"][0]["url"] = ""
    assert public_cases._complete_case("topic-urban-heat-v2", detail) is False


def test_every_report_citation_must_resolve_to_a_linked_evidence_record():
    assessment = _detail("assessment-platform")
    assessment["state"]["report_snapshot"]["citation_numbers"]["UNKNOWN"] = 2
    assert public_cases._complete_case("assessment-platform", assessment) is False

    topic = _detail("topic-urban-heat-v2")
    topic["state"]["report_plan"]["citation_map"]["E2"] = 2
    assert public_cases._complete_case("topic-urban-heat-v2", topic) is False

    topic = _detail("topic-urban-heat-v2")
    topic["state"]["evidence_pool"][0]["url"] = ""
    assert public_cases._complete_case("topic-urban-heat-v2", topic) is False


def test_case_cannot_be_read_before_the_global_publication_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(public_cases, "CASE_ROOT", tmp_path)
    public_cases._read_snapshot.cache_clear()
    _write_snapshot(tmp_path, public_cases.CASE_IDS[0])
    try:
        public_cases.get_case(public_cases.CASE_IDS[0])
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("A partial public case must not be readable.")


def test_all_published_v3_case_snapshots_are_complete_and_evidence_consistent():
    from scripts.curate_public_case_snapshots import TARGET_DIR, validate_snapshot

    manifest = json.loads((public_cases.CASE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert public_cases.CASE_SNAPSHOT_VERSION == "v3"
    assert manifest["published"] is True
    assert manifest["case_ids"] == list(public_cases.CASE_IDS)

    for case_id in public_cases.CASE_IDS:
        path = TARGET_DIR / f"{case_id}.json.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        validate_snapshot(case_id, payload)
        detail = payload["detail"]
        assert public_cases._complete_case(case_id, detail)

        if case_id.startswith("topic-"):
            state = detail["state"]
            assert len(state["candidates"]) == 5
            links = state.get("crosslinks", [])
            assert all(item["source_theme_id"] != item["target_theme_id"] for item in links)
            if case_id in {"topic-phage-v2", "topic-perovskite-v2"}:
                assert len(links) == 3
                evidence = {item["evidence_id"]: item for item in state["evidence_pool"]}
                assert all(item["quote"] in evidence[item["evidence_id"]]["abstract"]
                           for link in links for item in link["method_evidence"] if item["verified"])
        else:
            assert len(detail["state"]["report_snapshot"]["sections"]) == 12
            plans = detail["state"].get("query_plans", [])
            assert plans
            assert plans == detail["state"]["report_snapshot"]["audit_data"]["query_plans"]
            assert all(all(plan.get(key) for key in ("reader_topic", "reader_summary", "reader_focus", "reader_scope", "reader_result")) for plan in plans)


def test_battery_case_third_reference_links_to_its_verified_doi_title():
    import re
    from scripts.curate_public_case_snapshots import TARGET_DIR

    with gzip.open(TARGET_DIR / "assessment-battery-v2.json.gz", "rt", encoding="utf-8") as stream:
        state = json.load(stream)["detail"]["state"]
    references = next(section for section in state["report_snapshot"]["sections"] if section["section_key"] == "references")
    third = next(block for block in references["blocks"] if "E-L-075" in block.get("evidence_ids", []))
    assert third["text"].startswith("[3] [Surface and morphological investigation")
    assert "https://doi.org/10.1007/s10832-017-0084-z" in third["text"]
    assert not re.search(r"CDATA|Subscript|paper_detail\.html", third["text"])
    from app.common.retrieval.links import evidence_url
    source = next(item for item in state['evidence_records'] if item['evidence_id'] == 'E-L-075')
    assert evidence_url(source) == 'https://doi.org/10.1007/s10832-017-0084-z'
    assert source['title'] in third['text']
    assert source['locator']['original_nstl_url'].endswith('61dc6258e75e4542945fce5f42957693')


def test_public_markdown_exports_bundle_rendered_images_and_relative_paths():
    from app.api.routes import export_public_case

    for case_id, expected_images in (("assessment-battery-v2", 1), ("topic-urban-heat-v2", 1)):
        response = asyncio.run(export_public_case(case_id, "md"))
        assert response.media_type == "application/zip"
        assert response.headers["Content-Disposition"].endswith(".zip")

        with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
            names = set(archive.namelist())
            report = archive.read("report.md").decode("utf-8")
            image_paths = re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", report)
            assert len(image_paths) >= expected_images
            assert set(image_paths) <= names
            assert all(archive.read(path).startswith(b"\x89PNG\r\n\x1a\n") for path in image_paths)
            assert "data:image/png;base64" not in report

            if case_id.startswith("assessment-"):
                assert '<col width="40%"><col width="60%">' in report
                assert "https://doi.org/10.1007/s10832-017-0084-z" in report


def test_public_cases_keep_candidate_fields_titles_and_figures_in_sync():
    import re
    from scripts.curate_public_case_snapshots import TARGET_DIR, TOPIC_CANDIDATES, _candidate_report_fields
    for case_id in public_cases.CASE_IDS:
        with gzip.open(TARGET_DIR / f'{case_id}.json.gz', 'rt', encoding='utf-8') as stream:
            state = json.load(stream)['detail']['state']
        if not case_id.startswith('topic-'):
            assert all(not re.fullmatch(r'我要评估的是\s*[:：]\s*项目', c['text']) for c in state['claims'])
            assert all('我要评估的是：项目' not in str(b) for s in state['report_snapshot']['sections'] for b in s.get('blocks', []))
            continue
        plan = state['report_plan']
        assert plan == state['report_snapshot']['report_plan_snapshot']
        blocks = [b for s in plan['sections'] for b in s.get('blocks', []) if b.get('kind') == 'candidate_detail']
        candidates = state['candidates']
        table = next(t for t in plan['table_specs'] if t['key'] == 'candidate_priority')
        assert [r['候选题'] for r in table['rows']] == [b['title'] for b in blocks] == [c['display_title'] for c in candidates]
        for candidate, block in zip(candidates, blocks):
            assert block['fields'] == _candidate_report_fields(TOPIC_CANDIDATES[case_id][candidate['candidate_id']], plan['citation_map'])
            research = next(f['value'] for f in block['fields'] if f['label'] == '研究内容')
            assert len(research.splitlines()) == 3
            assert all(re.search(r'\[\d+\]', line) for line in research.splitlines())
            assert not re.search(r'\[\d+\]\s+\[\d+\]', research)
        tree = next(s for s in plan['chart_specs'] if s['key'] == 'theme_network')
        assert tree['data'] == state['theme_tree']
        trend = next(s for s in plan['chart_specs'] if s['key'] == 'trend_comparison')
        assert trend['chart_type'] == 'grouped_bar' and '并列' in trend['caption']
        numbers = sorted(set(plan['citation_map'].values()))
        assert numbers == list(range(1, len(numbers) + 1))
