"""Compose reader analysis from readable sources before freezing a new report."""
from __future__ import annotations

import json
import re
import hashlib
from copy import deepcopy
from app.agents.report_validation import numeric_values, scientific_identifiers, review_response, response_diagnostic

from app.agents.content_assessment.scholarly_report import WRITING_RULES, reader_prose, tokens, readable_research_content, UNSUPPORTED_ABSENCE
from app.agents.topic_selection.presentation import candidate_reader_report_fields, opportunity_reader_type


SOURCE_REVIEW_VERSION = 'topic-source-review-v6'


def threshold_relations(text):
    """A measured value alone cannot establish a decision threshold.

    Keep the metric, its qualifier, comparison and unit together. This also
    prevents a source RMSE value from supporting an RMSE-increase cutoff.
    Semantic review remains responsible for experimental applicability.
    """
    operators = {'小于': '<', '低于': '<', '少于': '<', '不超过': '≤', '不高于': '≤', '<=': '≤',
                 '大于': '>', '高于': '>', '超过': '>', '至少': '≥', '不低于': '≥', '>=': '≥',
                 '等于': '=', '为': '='}
    pattern = (r'(?P<metric>RMSE|MAE|MSE|R[²2]|FIC|温度|误差|准确率|召回率|样本量)'
               r'\s*(?P<qualifier>增量|增幅|差值|变化量)?\s*(?:均|值)?\s*'
               r'(?P<op>不超过|不高于|不低于|小于|低于|少于|大于|高于|超过|至少|等于|为|<=|>=|[<≤>≥=])\s*'
               r'(?P<value>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\d.])\s*'
               r'(?P<unit>°\s*C|℃|%|K|mm|cm|km|ha)?')
    return {(m['metric'].lower().replace('²', '2'), m['qualifier'] or '', operators.get(m['op'], m['op']),
             m['value'], re.sub(r'\s+', '', m['unit'] or '').replace('℃', '°C'))
            for m in re.finditer(pattern, str(text), re.I)}


def claim_contexts(text, *, proposal=False):
    """Keep user-specified designs separate from assertions about the literature.

    A summary can contain both. Scope follows an explicit proposed-study clause
    within one sentence, and resets when a literature/result clause begins.
    Splitting on Chinese punctuation preserves decimal and model identifiers.
    Semantic review still checks that cited facts actually support each claim.
    """
    prospective = re.compile(r'本(?:研究|项目|课题|方案|选题).{0,8}(?:拟|计划|将)|(?:^|[：:])\s*(?:拟|建议|计划)|拟(?:开展|分析|比较|采用|构建|设置|评估)|建议(?:开展|采用|设置)|可(?:采用|通过|设置|在)|应当')
    factual = re.compile(r'已有研究|既有研究|该文献|该论文|文献(?:报道|显示|表明|采用|使用|在)|(?:结果|实验|实测|研究)(?:表明|显示|发现|证实)|显著(?:提升|下降)|已(?:经|取得|验证)')
    for sentence in re.split(r'[。！？;；\n]|(?<=[.!?])\s+(?=[A-Z])', text):
        proposed = proposal
        for clause in re.split(r'[，,]', sentence):
            if not clause.strip():
                continue
            if factual.search(clause):
                proposed = False
            elif prospective.search(clause):
                proposed = True
            yield clause, proposed


def field_refs(row, field):
    mapping = row.get('field_evidence_ids')
    return mapping.get(field, row.get('evidence_ids', [])) if isinstance(mapping, dict) else row.get('evidence_ids', [])


def replace_paragraph(row, field, text, ids):
    if field == 'text':
        row[field], row['evidence_ids'] = text, ids
        return
    mapping = row.get('field_evidence_ids')
    if not isinstance(mapping, dict):
        mapping = {key: list(row.get('evidence_ids') or []) for key in candidate_fields(row)}
        row['field_evidence_ids'] = mapping
    row[field], mapping[field] = text, ids
    row['evidence_ids'] = list(dict.fromkeys(eid for values in mapping.values() for eid in values))


def candidate_fields(row):
    return ('title', 'reason', 'design', 'conditions') if 'title' in row else ('reason', 'design', 'conditions')


def paragraph_rows(draft):
    rows = []
    if not isinstance(draft, dict):
        return rows
    for key in ('summary', 'progress', 'opportunities', 'recommendations', 'candidates'):
        section = draft.get(key, [])
        for index, row in enumerate(section if isinstance(section, list) else []):
            if not isinstance(row, dict):
                continue
            for field in candidate_fields(row) if key == 'candidates' else ('text',):
                rows.append({'key': key, 'index': index, 'field': field, 'text': row.get(field, ''), 'evidence_ids': field_refs(row, field)})
    return rows


def paragraph_hash(row, packet):
    referenced = [e for e in packet['sources'] if e['evidence_id'] in row.get('evidence_ids', [])]
    context = [packet['question'], packet.get('user_conditions', ''), referenced]
    return hashlib.sha256(json.dumps([SOURCE_REVIEW_VERSION, row, context], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def report_structure(value, packet):
    """A recoverable draft may have factual defects without losing its structure."""
    if not isinstance(value, dict):
        return False
    for key in ('summary', 'progress', 'opportunities', 'recommendations', 'candidates'):
        rows = value.get(key)
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            return False
        fields = packet.get('required_candidate_fields', ('reason', 'design', 'conditions')) if key == 'candidates' else ('text',)
        if any(any(not isinstance(row.get(f), str) or not row[f].strip() for f in fields)
               or not isinstance(row.get('evidence_ids'), list) for row in rows):
            return False
    return [row.get('id') for row in value['candidates']] == [row['id'] for row in packet['candidates']]


def structure_issues(value, packet):
    value = value if isinstance(value, dict) else {}
    issues = []
    for key in ('summary', 'progress', 'opportunities', 'recommendations'):
        rows = value.get(key)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not isinstance(row.get('text'), str) or not row['text'].strip() or not isinstance(row.get('evidence_ids'), list) for row in rows):
            issues.append({'key': key, 'reason': '章节缺失或段落结构无效'})
    rows = value.get('candidates', [])
    rows = rows if isinstance(rows, list) else []
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    expected = [row['id'] for row in packet['candidates']]
    for cid in expected:
        if ids.count(cid) != 1:
            issues.append({'key': 'candidates', 'id': cid, 'reason': '候选缺失或重复'})
        else:
            row = next(row for row in rows if isinstance(row, dict) and row.get('id') == cid)
            fields = packet.get('required_candidate_fields', ('reason', 'design', 'conditions'))
            missing = [f for f in fields if not isinstance(row.get(f), str) or not row[f].strip()]
            if missing or not isinstance(row.get('evidence_ids'), list):
                issues.append({'key': 'candidates', 'id': cid, 'fields': missing, 'reason': '候选必需字段缺失'})
    if any(cid not in expected for cid in ids) or any(not isinstance(row, dict) for row in rows):
        issues.append({'key': 'candidates', 'reason': '包含未知候选或无效结构'})
    return issues


async def repair_structure(agent, state, callback, packet, draft):
    """The application owns candidate identity/order; repair only missing units."""
    result = deepcopy(draft) if isinstance(draft, dict) else {}
    artifacts = state.stage_artifacts.setdefault('report_publish', {})
    artifacts['structure_diagnostics'] = structure_issues(result, packet)
    missing_sections = [row['key'] for row in artifacts['structure_diagnostics'] if row['key'] != 'candidates']
    rows = result.get('candidates', [])
    by_id = {row['id']: row for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str)} if isinstance(rows, list) else {}
    targets = [(key, None) for key in missing_sections]
    invalid_ids = {issue.get('id') for issue in artifacts['structure_diagnostics'] if issue['key'] == 'candidates'}
    targets.extend(('candidates', candidate) for candidate in packet['candidates'] if candidate['id'] not in by_id or candidate['id'] in invalid_ids)
    for key, candidate in targets:
        unit_id = key + ':' + (candidate['id'] if candidate else '')
        budget = artifacts.setdefault('structure_repair_budget', {})
        if budget.get(unit_id):
            continue
        budget[unit_id] = True
        response = await agent._stage_model_json(state, callback, stage_key='report_publish',
            prompt_version='topic-scholarly-unit-repair-v6', purpose='补齐缺失选题或报告章节',
            payload={'writing_rules': WRITING_RULES, 'source_material': packet,
                'missing_unit': {'key': key, **({'candidate': candidate_context(candidate)} if candidate else {})},
                'instructions': '只补写指定的一个单元，保留研究问题中的地域、对象和研究范围。外地区文献只作方法参考，不改变研究地点。'
                    + ('返回{candidate:{title,reason,design,conditions,evidence_ids}}。候选身份由程序固定，不返回其他候选。'
                       if candidate else '返回{paragraphs:[{text,evidence_ids}]}。')
                    + '文献事实只取sources.content，方案保持拟研究属性，不指定无来源数字；引用只放evidence_ids。'},
            expected_keys={'candidate'} if candidate else {'paragraphs'},
            evidence_refs=[source['evidence_id'] for source in packet['sources']], max_tokens=2600)
        artifacts.setdefault('structure_repairs', []).append({'key': key, 'id': candidate['id'] if candidate else '', 'response': response})
        if response is None:
            # Transport failures do not consume the content repair budget.
            from .recovery import raise_for_transport
            budget.pop(unit_id, None)
            raise_for_transport(state, 'report_publish')
        if isinstance(response, dict):
            if candidate and isinstance(response.get('candidate'), dict):
                old = by_id.get(candidate['id'], {})
                new = response['candidate']
                merged = {**new, **old, 'id': candidate['id']}
                for field in packet.get('required_candidate_fields', ('reason', 'design', 'conditions')):
                    if (not isinstance(old.get(field), str) or not old[field].strip()) and isinstance(new.get(field), str):
                        replace_paragraph(merged, field, new[field], field_refs(new, field))
                if not isinstance(merged.get('evidence_ids'), list):
                    merged['evidence_ids'] = new.get('evidence_ids', [])
                by_id[candidate['id']] = merged
            elif not candidate and isinstance(response.get('paragraphs'), list):
                result[key] = response['paragraphs']
        result['candidates'] = [by_id[c['id']] for c in packet['candidates'] if c['id'] in by_id]
        artifacts['reader_draft_result'] = deepcopy(result)
        artifacts.pop('reviewed_draft', None)
        if hasattr(agent, '_notify'):
            await agent._notify(state, callback)
    # Remove model-added candidates and restore the application-owned order.
    result['candidates'] = [by_id[c['id']] for c in packet['candidates'] if c['id'] in by_id]
    artifacts['structure_diagnostics'] = structure_issues(result, packet)
    artifacts['reader_draft_result'] = deepcopy(result)
    return result


def text_issues(text, ids, packet, *, proposal=False):
    reasons = []
    allowed = {e['evidence_id'] for e in packet['sources']}
    if not isinstance(text, str) or not text.strip():
        return ['段落内容为空']
    if re.search(r'[“‘]+\s*[”’]+|"\s*"', text):
        reasons.append('正文含空引号，须使用具体研究名称或直接说明研究建议')
    if reader_prose(text) != text:
        reasons.append('包含内部诊断或未经核验的领先性表述')
    absence = UNSUPPORTED_ABSENCE.search(text)
    if absence:
        reasons.append('不能由摘要没有记载推断原研究未开展：' + absence.group())
    if not isinstance(ids, list) or any(not isinstance(eid, str) or eid not in allowed for eid in ids):
        return [*reasons, '引用必须来自当前锁定来源']
    if not ids and (not proposal or not re.search(r'拟|建议|可通过|可采用|应当|可在|进一步研究|验证设计', text)
                    or numeric_values(text) or re.search(r'已经|结果表明|实验表明|显著提升|显著下降', text)):
        reasons.append('事实需要对应来源；无引用方案须明确为建议且不能含实测结果或数值')
    if re.search(r'(?<![A-Za-z0-9_-])(?:E-[A-Za-z0-9_-]+|M-INPUT|T\d+|G\d+|CAND-\d+|C\d+)(?![A-Za-z0-9_-])', text):
        reasons.append('正文包含内部编号，引用仅放在evidence_ids')
    # Explicit user conditions describe a proposed study, never measured
    # literature results. Semantic review receives that distinction below.
    sources = ' '.join([
        ' '.join(str(e.get(key) or '') for key in ('content', 'title', 'year'))
        for e in packet['sources'] if e['evidence_id'] in ids])
    conditions = ' '.join([packet['question'], packet.get('user_conditions', '')])
    unknown_names, unsupported = set(), []
    for clause, proposed in claim_contexts(text, proposal=proposal):
        corpus = sources + (' ' + conditions if proposed else '')
        unknown_names.update(set(scientific_identifiers(clause)) - set(scientific_identifiers(corpus)))
        available = set(numeric_values(corpus))
        unsupported.extend(n for n in numeric_values(clause) if n not in available)
    if unknown_names:
        reasons.append('所引来源没有对应的方法或设备标识：' + '、'.join(sorted(unknown_names)))
    if unsupported:
        reasons.append('来源未给出这些数值：' + '、'.join(dict.fromkeys(unsupported)) + '；删除相应阈值，改述比较变量，不另编数字')
    # Numerical coincidence (e.g. 2.5 occurs elsewhere in an abstract) is
    # insufficient for a new acceptance rule. Preserve explicitly supplied
    # user criteria only as proposed conditions, never literature results.
    for sentence in re.split(r'[。！？;；\n]', text):
        if not re.search(r'阈值|(?:若|当).{0,100}(?:则|认为)|可接受|判定|达标', sentence):
            continue
        proposed = any(p for _, p in claim_contexts(sentence, proposal=proposal))
        basis = sources + (' ' + conditions if proposed else '')
        if threshold_relations(sentence) - threshold_relations(basis):
            reasons.append('判定阈值的指标、比较方向和单位没有对应依据；不得将文献实测值转作可接受阈值，应说明拟比较的变量与验证方式')
            break
    for match in re.finditer(r'\bp\s*([<≤>≥=])\s*(\d+(?:\.\d+)?)', text, re.I):
        if not re.search(r'\bp\s*' + re.escape(match[1]) + r'\s*' + re.escape(match[2]) + r'(?![\d.])', sources, re.I):
            reasons.append('来源缺失统计比较符号，不能自行恢复显著性含义；改用可核实的方法或观察描述')
    return reasons


def source_packet(state):
    target = tokens(state.input_profile.research_question)
    readable = [e for e in state.evidence_pool if readable_research_content(e)]
    # A lexical top-24 can discard the real method paper a candidate cites.
    # Keep readable citations from candidates and persisted paragraphs first;
    # unknown IDs still cannot enter the packet through the draft.
    required = {eid for c in state.candidates for eid in c.evidence_ids}
    artifacts = state.stage_artifacts.get('report_publish', {})
    for key in ('reader_draft_result', 'reviewed_draft'):
        value = artifacts.get(key)
        if isinstance(value, dict):
            for row in paragraph_rows(value):
                if isinstance(row['evidence_ids'], list):
                    required.update(eid for eid in row['evidence_ids'] if isinstance(eid, str))
    readable.sort(key=lambda e: (e.evidence_id in required,
        len(target & tokens(e.title + ' ' + e.abstract))), reverse=True)
    limit = max(24, sum(e.evidence_id in required for e in readable))
    return [{"evidence_id": e.evidence_id, "title": e.title, "year": e.year,
             "content": (e.abstract or e.excerpt)[:2600], "scope": "abstract" if e.abstract else "excerpt"}
            for e in readable[:limit]]


def candidate_context(candidate):
    """A generated route is a proposal, never a factual source for its repair."""
    return {key: candidate[key] for key in ('id', 'question', 'evidence_ids') if key in candidate}


def valid_report(value, packet):
    """A partial answer or unknown citation cannot pass source review."""
    return report_structure(value, packet) and not invalid_paragraphs(value, packet)


def invalid_paragraphs(value, packet):
    """Return exact repair targets with actionable validation feedback."""
    if not isinstance(value, dict):
        return []
    targets = []
    for key in ('summary', 'progress', 'opportunities', 'recommendations', 'candidates'):
        rows = value.get(key, [])
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for field in candidate_fields(row) if key == 'candidates' else ('text',):
                ids, text = field_refs(row, field), row.get(field, '')
                proposal = key in ('opportunities', 'recommendations') or (key == 'candidates' and field in ('title', 'design', 'conditions'))
                reasons = text_issues(text, ids, packet, proposal=proposal)
                if reasons:
                    targets.append({'key': key, 'index': index, 'field': field, 'text': text,
                                    'validation_reasons': reasons, 'evidence_ids': ids})
    return targets


async def repair_paragraphs(agent, state, callback, packet, draft, attempted=None, *, fact_rewrite=False):
    attempted = attempted if attempted is not None else set()
    draft = normalize_report(draft, packet)
    targets = invalid_paragraphs(draft, packet)
    if not targets:
        return draft
    result = deepcopy(draft)
    for target in targets:
        # A single paragraph has a caller-owned location. The model cannot
        # silently change an index or return a whole report in its place.
        key, index, field = target['key'], target['index'], target['field']
        location = (key, index, field)
        if location in attempted:
            continue
        attempted.add(location)
        artifacts = state.stage_artifacts.setdefault('report_publish', {})
        basis_hash = paragraph_hash({**target, 'text': '', 'validation_reasons': []}, packet)
        budgets = artifacts.setdefault('paragraph_repair_budget', {})
        budget = budgets.setdefault(basis_hash, [])
        mode = 'verified_facts' if fact_rewrite else 'targeted'
        if mode in budget:
            continue
        context = candidate_context(next((c for c in packet['candidates'] if c['id'] == result[key][index].get('id')), {})) if key == 'candidates' else {'section': key}
        # Rejected prose anchors the model to the same invented thresholds.
        # Keep it in the audit; regenerate from the real sources and reasons.
        feedback = {k: target[k] for k in ('key', 'index', 'field', 'validation_reasons')}
        # Full sentences copied from exact source texts are the fact inventory
        # for the second rewrite. They are not a new generated report.
        relevant_ids = set(target.get('evidence_ids') or context.get('evidence_ids') or [])
        relevant = [s for s in packet['sources'] if s['evidence_id'] in relevant_ids] or packet['sources'][:4]
        facts = [{'evidence_id': s['evidence_id'],
                  'literal_model_identifiers': scientific_identifiers(s['content']),
                  'sentences': [x.strip() for x in re.split(r'(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z])', s['content']) if x.strip()][:12]} for s in relevant]
        response = await agent._stage_model_json(state, callback, stage_key='report_publish',
            prompt_version='topic-scholarly-fact-rewrite-v2' if fact_rewrite else 'topic-scholarly-paragraph-repair-v8', purpose='按核实事实重写问题段落' if fact_rewrite else '核对并修正报告中的具体段落',
            payload={'writing_rules': WRITING_RULES, 'sources': relevant if fact_rewrite else packet['sources'], 'question': packet['question'],
                     'user_conditions': packet.get('user_conditions', ''),
                     **({'verified_fact_inventory': facts, 'fact_rewrite': '只用逐字来源事实重新组织一个完整段落。缺失符号、日期分隔不得推测，改述方法、变量与对照；用户条件保持拟研究属性。'} if fact_rewrite else {}),
                     'research_context': context, 'paragraph_role': field, 'paragraph_to_repair': feedback,
                     'output_schema': {'text':'一个完整自然段，约200至300汉字','evidence_ids':['实际引用的来源ID']},
                     'instructions': '重新撰写一个具体研究段落，返回{text,evidence_ids}，不要返回整个报告或replacements。'
                        'paragraph_role为title时只返回简短科研题名，保持用户指定范围，不写成说明段落。'
                        '只输出这两个字段，不附加source_basis或逐篇原文摘抄；后续由独立来源校核确认事实。'
                        '围绕本段问题选择最相关的来源，避免把全部文献的事实塞入一个段落。'
                        'question是用户的研究范围，research_context只是待研究方向；必须服从question的地域、研究对象和边界。'
                        '不把其他地区的参考论文地点变成拟开展研究的地点。跨领域来源只提供方法参照，不证明当前研究的性能。'
                        '不得写原研究尚未、现有研究缺乏、未见报道等否定断言；摘要没有记录不能据此推断不存在。'
                        '以来源实际采用的方法开篇，再说明拟比较的变量和对照设计。'
                        '按当前研究领域说明比较变量与对照条件，不指定来源之外的数值。'
                        '本次修复不写实验数值或时间数值，以变量名称、对照组和观察指标说明设计；方法名称中的标识可以保留。'
                        '设备和产品型号必须逐字存在于本段evidence_ids对应的来源，或明确作为user_conditions中的拟研究条件。'
                        'Sentinel-2、Sentinel-2A、Sentinel-3是不同产品，不能相互替换；无法对应时改用遥感植被指数等具体方法表述，不编造型号。'
                        '20142021之类连续数字不是已确认的年份范围，不能改成2014—2021。不要重写成任何年份，直接说明方法和变量。'
                        '逐条解决validation_reasons。阈值没有来源时删掉阈值，说明拟比较的变量、对照组与结果解释，不能换成另一个数字。'
                        '已有研究事实须由所引来源的content直接记载；'
                        '来源中的年份、菌株编号或其他指标数字不能用作疗效阈值。拟定设计与来源实际结论分开叙述。'
                        '引用只放evidence_ids。保留实质内容，用具体研究名称；不评论资料完整程度。'},
            expected_keys={'text','evidence_ids'}, evidence_refs=[e['evidence_id'] for e in packet['sources']], max_tokens=2200)
        if response is None:
            from .recovery import raise_for_transport
            raise_for_transport(state, 'report_publish')
        budget.append(mode)
        basis_status = ('not_provided' if isinstance(response, dict) and response.get('source_basis') is None
            else 'verified' if isinstance(response, dict) and valid_source_basis(response, packet) else 'rejected')
        artifacts = state.stage_artifacts.setdefault('report_publish', {})
        artifacts.setdefault('paragraph_repairs', []).append(
            {'key':key, 'index':index, 'field':field, 'result':response,
             'basis_status': basis_status})
        # Fact plans are auxiliary audit material. The repaired prose remains
        # unpublished until full semantic source review and strict validation;
        # a malformed quote must not discard an otherwise reviewable draft.
        if isinstance(response, dict) and isinstance(response.get('text'), str) and isinstance(response.get('evidence_ids'), list):
            replace_paragraph(result[key][index], field, response['text'], response['evidence_ids'])
        artifacts['reader_draft_result'] = normalize_report(result, packet)
        if isinstance(artifacts.get('reviewed_draft'), dict):
            artifacts['reviewed_draft'] = deepcopy(artifacts['reader_draft_result'])
        if hasattr(agent, '_notify'):
            await agent._notify(state, callback)
    result = normalize_report(result, packet)
    state.stage_artifacts['report_publish']['repair_diagnostics'] = invalid_paragraphs(result, packet)
    return result


def valid_source_basis(response, packet):
    """Check optional verbatim provenance; semantic review remains mandatory."""
    basis = response.get('source_basis')
    if basis is None:
        return True
    if not isinstance(basis, list):
        return False
    import html
    normalize = lambda text: re.sub(r'\s+', '', html.unescape(re.sub(r'<[^>]+>', '', str(text))))
    contents = {e['evidence_id']: normalize(e['content']) for e in packet['sources']}
    refs = response.get('evidence_ids')
    if not isinstance(refs, list) or not all(isinstance(eid, str) for eid in refs):
        return False
    if any(not isinstance(row, dict) or not isinstance(row.get('evidence_id'), str) for row in basis):
        return False
    return {row['evidence_id'] for row in basis} == set(refs) and all(
        isinstance(row, dict) and isinstance(row.get('quote'), str) and bool(normalize(row['quote']))
        and row.get('evidence_id') in contents and normalize(row['quote']) in contents[row['evidence_id']]
        for row in basis)


def resolve_report_names(text, sources, candidates):
    """Resolve IDs once, including legacy quote wrappers, without empty labels."""
    if not isinstance(text, str):
        return text
    names = {e['evidence_id']: '《' + e['title'].strip() + '》'
             for e in sources if isinstance(e.get('title'), str) and e['title'].strip()}
    names.update({c['id']: '“' + c['title'].strip() + '”'
                  for c in candidates if isinstance(c.get('title'), str) and c['title'].strip()
                  and c['title'].strip() != c['id']})
    if not names:
        return text
    identifiers = '|'.join(re.escape(key) for key in sorted(names, key=len, reverse=True))
    # Consume wrappers only around a known ID. Repeated normalization must
    # neither wrap an unresolved ID again nor alter a scientific quotation.
    pattern = r'(?:[“‘\"\x27]+\s*)?(?<![A-Za-z0-9])(' + identifiers + r')(?![A-Za-z0-9])(?:\s*[”’\"\x27]+)?'
    return re.sub(pattern, lambda match: names[match.group(1)], text)


def normalize_report(value, packet):
    """Resolve known display identifiers without changing scientific claims."""
    if not isinstance(value, dict):
        return value
    if set(value) == {'draft'} and isinstance(value['draft'], dict):
        value = value['draft']
    value = deepcopy(value)
    # The freshly written short title is preferred; packets from old tasks
    # may omit titles. Never use the ID itself as a quoted display name.
    draft_rows = value.get('candidates', [])
    titles = {row.get('id'): row['title'] for row in draft_rows if isinstance(row, dict)
              and isinstance(row.get('title'), str) and row['title'].strip()} if isinstance(draft_rows, list) else {}
    candidates = [{**c, 'title': titles.get(c['id']) or c.get('title', '')} for c in packet['candidates']]
    for key in ('summary', 'progress', 'opportunities', 'recommendations', 'candidates'):
        rows = value.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                for field in ('text', 'reason', 'design', 'conditions'):
                    if field in row:
                        row[field] = resolve_report_names(row[field], packet['sources'], candidates)
    rows = value.get('candidates', [])
    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
        by_id = {r.get('id'): r for r in rows}
        expected = [c['id'] for c in packet['candidates']]
        if len(rows) == len(by_id) == len(expected) and set(by_id) == set(expected):
            value['candidates'] = [by_id[cid] for cid in expected]
    return value


async def review_report(agent, state, callback, packet, draft, repair_review=True, attempted=None):
    """Review exact paragraphs; avoid regenerating a report during verification."""
    artifacts = state.stage_artifacts.setdefault('report_publish', {})
    attempted = attempted if attempted is not None else set()
    verified = set(artifacts.get('verified_paragraph_hashes', []))
    rows = [row for row in paragraph_rows(draft) if paragraph_hash(row, packet) not in verified]
    result = deepcopy(draft)
    for offset in range(0, len(rows), 6):
        batch = rows[offset:offset + 6]
        payload = {'writing_rules': WRITING_RULES, 'source_material': packet, 'paragraphs': batch,
                 'output_schema': {'checked': True, 'replacements': [{'key': 'string', 'index': 0, 'field': 'string', 'text': 'string', 'evidence_ids': []}]},
                 'instructions': '逐段核对文献事实和研究建议。先核对用户限定的研究范围，再核对来源事实。'
                    'source_material.question是拟研究对象、地域和时间范围的唯一约束；candidate_validation是待核验的既有检查意见，不是事实来源。'
                    'user_conditions为用户明确提供的研究条件，可以用于拟研究设计；不能将这些条件表述为文献实测结果。'
                    '来源丢失比较符号或日期分隔时，不得推测恢复其含义，应改述能够核实的方法、对象或观察。'
                    '逐项检查candidate_validation指出的问题，不能因为来源研究了其他地区，就允许方案把其他地区加入目标城市。'
                    '题名、reason中拟开展研究的部分、design与conditions都必须遵守用户范围。'
                    '修正时保留方法与对照变量，将越界的拟研究地点改为用户范围内的研究对象；不强行指定用户未指定的城市。'
                    'reason中准确介绍外地文献的原研究地点可以保留；将外地地点用于本研究源域、目标域、样本或验证地点则必须修正。'
                    '文献事实只依据sources.content，不凭标题或常识补写。'
                    '摘要没有提及某任务或验证，不能推断原研究没有开展，更不能推断整个领域无人研究。'
                    '不得将一个指标的差异解释为已经证明原因；建议写为待比较的设计。'
                    '删除来源未给出的实验参数、性能数值、领先性和研究空白断言。'
                    '尚属空白、未见报道、尚未验证等断言必须改为明确的拟比较变量及已有方法联系。'
                    '返回{checked:true,replacements:[{key,index,field,text,evidence_ids}]}，只列需要修正的段落，'
                    '全部准确则replacements为空。无法成立的事实段落text为空；明确标注为拟研究或建议的段落可以保留无引用设计，但不得写成已完成结果。引用只放evidence_ids，不写内部编号。'
                    'key/index/field必须逐字沿用paragraphs的定位：候选key始终为candidates，field为title、reason、design或conditions；其他key的field为text。'
                    'field为title时只校核并修正题名。所有拟研究的题名、对象与地域必须服从source_material.question；外地区文献可以作为方法参照，但不能改变用户的研究地点。'
                    '不重写整个report、不更改候选顺序、不引入新来源。'}
        allowed = {(r['key'], r['index'], r['field']) for r in batch}
        reviewed = None
        for protocol_attempt in range(2):
            raw = await agent._stage_model_json(state, callback, stage_key='report_publish',
                prompt_version=SOURCE_REVIEW_VERSION, purpose='核对选题论述与实际来源', payload=payload,
                expected_keys={'checked', 'replacements'}, evidence_refs=[e['evidence_id'] for e in packet['sources']], max_tokens=5500)
            reviewed, reason = review_response(raw, allowed, fields=True)
            if raw is None:
                from .recovery import raise_for_transport
                raise_for_transport(state, 'report_publish')
            artifacts.setdefault('review_protocol_attempts', []).append(response_diagnostic(
                raw, reason, batch=offset // 6, attempt=protocol_attempt + 1))
            if reviewed is not None or reason == 'review_not_approved':
                break
            payload = {**payload, 'protocol_feedback': reason,
                'recovery_instruction': '只恢复本批来源校核，返回完整checked布尔值与replacements数组，key/index/field沿用输入。'}
        state.stage_artifacts.setdefault('report_publish', {}).setdefault('source_review_batches', []).append(reviewed)
        state.stage_artifacts['report_publish']['source_review_result'] = reviewed
        if not isinstance(reviewed, dict) or reviewed.get('checked') is not True or not isinstance(reviewed.get('replacements'), list):
            return None
        allowed = {(r['key'], r['index'], r['field']) for r in batch}
        for change in reviewed['replacements']:
            if not isinstance(change, dict):
                return None
            key, index, field = change.get('key'), change.get('index'), change.get('field')
            if type(index) is not int or (key, index, field) not in allowed or not isinstance(change.get('evidence_ids'), list):
                return None
            row = result[key][index]
            replace_paragraph(row, field, change.get('text'), change['evidence_ids'])
        result = normalize_report(result, packet)
        locations = {(row['key'], row['index'], row['field']) for row in batch}
        invalid = {(r['key'], r['index'], r['field']) for r in invalid_paragraphs(result, packet)}
        verified.update(paragraph_hash(row, packet) for row in paragraph_rows(result)
                        if (row['key'], row['index'], row['field']) in locations - invalid)
        artifacts['verified_paragraph_hashes'] = sorted(verified)
        artifacts['reviewed_draft'] = deepcopy(result)
        if hasattr(agent, '_notify'):
            await agent._notify(state, callback)
    result = normalize_report(result, packet)
    if valid_report(result, packet):
        return result
    if repair_review:
        corrected = await repair_paragraphs(agent, state, callback, packet, result, attempted)
        if valid_report(corrected, packet):
            return await review_report(agent, state, callback, packet, corrected, repair_review=False, attempted=attempted)
    return None


def saved_review_draft(artifacts, packet):
    """Resume exact reviewed paragraphs, including legacy failed publications."""
    reviewed = normalize_report(artifacts.get('reviewed_draft'), packet)
    if report_structure(reviewed, packet):
        return reviewed
    initial = normalize_report(artifacts.get('reader_draft_result'), packet)
    if not report_structure(initial, packet):
        return reviewed if isinstance(reviewed, dict) else initial if isinstance(initial, dict) else None
    rows = paragraph_rows(initial)
    batches = artifacts.get('source_review_batches', [])
    count = (len(rows) + 5) // 6
    if len(batches) != count or any(not isinstance(batch, dict) or batch.get('checked') is not True for batch in batches):
        return initial
    result = deepcopy(initial)
    for offset, batch in enumerate(batches):
        allowed = {(r['key'], r['index'], r['field']) for r in rows[offset * 6:offset * 6 + 6]}
        for change in batch.get('replacements', []):
            if (change.get('key'), change.get('index'), change.get('field')) not in allowed or not isinstance(change.get('evidence_ids'), list):
                return initial
            replace_paragraph(result[change['key']][change['index']], change['field'], change.get('text'), change['evidence_ids'])
    result = normalize_report(result, packet)
    invalid = {(r['key'], r['index'], r['field']) for r in invalid_paragraphs(result, packet)}
    artifacts['verified_paragraph_hashes'] = [paragraph_hash(row, packet) for row in paragraph_rows(result)
        if (row['key'], row['index'], row['field']) not in invalid]
    # Previously repaired paragraphs still require review. Reuse their text,
    # rather than issuing another whole-report generation request.
    for target in invalid_paragraphs(result, packet):
        repair = next((r.get('result') for r in reversed(artifacts.get('paragraph_repairs', [])) if all(r.get(k) == target[k] for k in ('key', 'index', 'field'))), None)
        if isinstance(repair, dict) and isinstance(repair.get('text'), str) and isinstance(repair.get('evidence_ids'), list):
            replace_paragraph(result[target['key']][target['index']], target['field'], repair['text'], repair['evidence_ids'])
    return normalize_report(result, packet)


def prose(value, state):
    text = str(value or '')
    text = resolve_report_names(text, [], [
        {'id': candidate.candidate_id, 'title': candidate.display_title or candidate.title}
        for candidate in state.candidates])
    for eid, number in state.report_plan.citation_map.items():
        text = text.replace(eid, f'[{number}]')
    # Remove internal identifiers even when a model writes a range such as
    # C1-C3; the report already presents candidate names and citation numbers.
    text = re.sub(r'(?<![A-Za-z0-9])(?:E-[A-Za-z0-9_-]+|M-INPUT|T\d+|G\d+|CAND-\d+)(?![A-Za-z0-9])', '', text)
    # Audit assessments do not explain a research proposal to its reader.
    text = ''.join(p for p in re.split(r'(?<=[。！？；])|\n+', text)
                   if not re.search(r'未直接支持|尚未证明|仅提供主题背景|缺少|缺失|未提供额外资源|适配性基于主题|当前仅有|关联主题|结论强度|部分支持', p))
    return reader_prose(text).strip('； ，')


async def compose(agent, state, callback):
    """Do not republish historical snapshots. Called only on the new-task path."""
    sources = source_packet(state)
    packet = {"question": state.input_profile.research_question,
              "user_conditions": state.raw_user_input,
              "required_candidate_fields": ['title', 'reason', 'design', 'conditions'],
              "sources": sources,
              "candidate_validation": [{"id": c.candidate_id, "scope_feedback": c.fit_rationale}
                                       for c in state.candidates[:5] if c.fit_rationale],
              "candidates": [{"id": c.candidate_id,
                               "title": c.display_title or c.title,
                               "question": c.research_question,
                               "evidence_ids": [eid for eid in c.evidence_ids if eid in {e['evidence_id'] for e in sources}]}
                              for c in state.candidates[:5]]}
    original = state.stage_artifacts.get('report_publish', {}).get('analysis', {})
    artifacts = state.stage_artifacts.setdefault('report_publish', {})
    draft = saved_review_draft(artifacts, packet)
    resumed = draft is not None
    if not resumed:
        draft = await agent._stage_model_json(state, callback, stage_key='report_publish',
            prompt_version='topic-scholarly-v1', purpose='以可读文献编写学术选题报告',
            payload={"writing_rules": WRITING_RULES,
                     "output_schema": {"summary": [{"text": "string", "evidence_ids": ["string"]}], "progress": [], "opportunities": [], "recommendations": [], "candidates": [{"id": "string", "title": "string", "reason": "string", "design": "string", "conditions": "string", "evidence_ids": ["string"]}]},
                     "instructions": "候选题是研究建议，不能将前期模型假设写成文献事实。只用sources.content说明在先研究。"
                        "question规定用户的地域、对象与研究边界，候选的题名和问题都必须服从它；外地区文献只作方法参照，不改变拟研究地域。"
                        "不要自行指定样本数、温度、规模、参数值或性能阈值；没有给定数值时用变量名称和对照条件说明设计。"
                        "撰写可直接阅读的科研选题分析，合计2000至3000中文字；每段具体讨论方法、差异或验证设计。"
                        "不要重述后台取证过程，也不要反复强调支持或不支持。数据集和数值未在来源出现时不写成事实；"
                        "提出新设计时用建议或拟研究表述。候选顺序与输入一致；title给出具体研究对象和方法的简短研究题名，"
                        "不用方法改进、可靠性治理、工程转化等泛化尾词，不改变研究方向。"
                        "返回summary/progress/opportunities/recommendations段落数组和candidates数组。"
                        "每段{text,evidence_ids}；每个candidate{id,title,reason,design,conditions,evidence_ids}。"
                        "reason具体解释该问题为何值得研究，design给基线与变量控制及结果解释，conditions说明适用场景。"
                        "recommendations直接说明拟研究的对象、方法、推进次序与验证安排，不用候选编号或‘候选所描述’代替具体内容，不输出空引号。"
                        "只有题录的文献不用来解释方法；引用ID只写在evidence_ids中，不堆在正文。"
                        "opportunities和recommendations可提出无引用的拟研究设计，但其中已有研究事实、方法来源和数值必须引用；不要为了补引用编造来源。",
                     "source_material": packet}, expected_keys={'summary', 'progress', 'candidates'},
            evidence_refs=[e['evidence_id'] for e in sources], max_tokens=6500)
        if draft is None:
            from .recovery import raise_for_transport
            raise_for_transport(state, 'report_publish')
    draft = normalize_report(draft, packet)
    if not report_structure(draft, packet):
        draft = normalize_report(await repair_structure(agent, state, callback, packet, draft), packet)
    attempted = set()
    if not valid_report(draft, packet):
        draft = await repair_paragraphs(agent, state, callback, packet, draft, attempted)
    if not valid_report(draft, packet):
        draft = await repair_paragraphs(agent, state, callback, packet, draft, set(), fact_rewrite=True)
    state.stage_artifacts.setdefault('report_publish', {})['reader_draft_result'] = draft
    draft = await review_report(agent, state, callback, packet, draft, attempted=attempted) if valid_report(draft, packet) else None
    if draft is None:
        last = artifacts.get('reviewed_draft') or artifacts.get('reader_draft_result')
        if report_structure(last, packet) and invalid_paragraphs(last, packet):
            repaired = await repair_paragraphs(agent, state, callback, packet, last, set(), fact_rewrite=True)
            artifacts['reader_draft_result'] = repaired
            if valid_report(repaired, packet):
                draft = await review_report(agent, state, callback, packet, repaired, repair_review=False)
    last_draft = artifacts.get('reviewed_draft') or artifacts.get('reader_draft_result')
    artifacts['repair_diagnostics'] = [*structure_issues(last_draft, packet), *invalid_paragraphs(last_draft, packet)]
    if draft is not None:
        artifacts['reviewed_draft'] = deepcopy(draft)
        artifacts['repair_diagnostics'] = []
    if draft is None and state.analysis_policy.mode == 'deep' and state.analysis_policy.model_enabled:
        state.quality_gate.passed = False
        if '选题分析尚未完成来源核对' not in state.quality_gate.blockers:
            state.quality_gate.blockers.append('选题分析尚未完成来源核对')
        state.report_plan.quality_status = 'blocked'
        state.report_plan.publication_status = 'blocked'
        state.confirmation.needed = False
        state.report_snapshot.frozen = False
        state.resume_from_stage = 'report_publish'
        from .recovery import GenerationRecoveryRequired, raise_for_transport
        raise_for_transport(state, 'report_publish')
        raise GenerationRecoveryRequired('report_publish', 'source_validation', '报告段落尚未完成来源及完整性校核，已保存通过的分析与段落。')
    state.stage_artifacts.setdefault('report_publish', {})['analysis'] = original
    allowed = {e['evidence_id'] for e in sources}
    used = set()

    def refs(ids):
        ids = [eid for eid in ids or [] if eid in allowed]
        used.update(ids)
        return ' '.join(f'[{state.report_plan.citation_map[eid]}]' for eid in dict.fromkeys(ids))

    def paragraphs(key):
        rows = draft.get(key, []) if isinstance(draft, dict) else []
        return [{"kind": "paragraph", "text": prose(row.get('text'), state) + ' ' + refs(row.get('evidence_ids'))}
                for row in rows if isinstance(row, dict) and prose(row.get('text'), state)]

    from .discovery import selected_opportunities
    theme_names = {node.theme_id: (node.display_label or node.label).strip()
                   for node in state.theme_tree.nodes if (node.display_label or node.label).strip()}

    def opportunity_blocks():
        """Use the same grounded opportunity rows as the chart, in the same order."""
        blocks = []
        for link in selected_opportunities(state):
            opportunity = prose(link.research_opportunity or link.relation_basis, state)
            if not opportunity:
                continue
            participants = []
            for participant in link.participating_disciplines:
                name = str(participant.discipline or '').strip()
                if name and name not in participants:
                    participants.append(name)
            themes = []
            for theme_id in (link.source_theme_id, link.target_theme_id):
                name = theme_names.get(theme_id)
                if name and name not in themes:
                    themes.append(name)
            method = prose(link.transferable_method, state)
            basis = prose(link.relation_basis or link.description, state)
            sentences = []
            if themes:
                sentences.append('关联主题：' + '、'.join(themes) + '。')
            if method:
                sentences.append('可借鉴的方法：' + method.rstrip('。；;') + '。')
            if basis and basis != opportunity:
                sentences.append('关键线索：' + basis.rstrip('。；;') + '。')
            evidence_ids = list(link.support_evidence_ids)
            evidence_ids.extend(item.get('evidence_id') for item in link.method_evidence if item.get('evidence_id'))
            blocks.extend([
                {'kind': 'heading', 'level': 3, 'text': f'{opportunity_reader_type(link)}：{opportunity}'},
                {'kind': 'paragraph', 'text': ' '.join(sentences) + ' ' + refs(evidence_ids)},
            ])
        return blocks

    by_id = {c.get('id'): c for c in (draft.get('candidates', []) if isinstance(draft, dict) else []) if isinstance(c, dict)}
    candidate_blocks = []
    priority_rows = []
    for index, candidate in enumerate(state.candidates[:5], 1):
        row = by_id.get(candidate.candidate_id, {})
        reader_fields = candidate_reader_report_fields(candidate, state.report_plan.citation_map)
        if reader_fields:
            links = candidate.evidence_links or []
            associated_ids = (
                {link.evidence_id for link in links if link.relation in {"supports", "contradicts"}}
                if links else set(candidate.evidence_ids)
            )
            for section in (candidate.reader_content or {}).values():
                if isinstance(section, dict):
                    used.update(eid for eid in section.get('evidence_ids', []) if eid in allowed and eid in associated_ids)
                    for entry in section.values():
                        if isinstance(entry, dict):
                            used.update(eid for eid in entry.get('evidence_ids', []) if eid in allowed and eid in associated_ids)
            candidate_blocks.append({'kind': 'candidate_detail', 'candidate_id': candidate.candidate_id,
                'index': index, 'title': candidate.display_title or candidate.title,
                'show_status': False, 'fields': reader_fields})
            priority_rows.append({'优先级': index, '候选题': candidate.display_title or candidate.title})
            continue
        title = str(row.get('title') or '').strip()
        if title and len(title) <= 65 and reader_prose(title) == title and tokens(title) & tokens(candidate.title + candidate.research_question):
            candidate.display_title = title
        reason = prose(row.get('reason'), state) or f'拟研究的问题是：{prose(candidate.research_question, state)}'
        design = prose(row.get('design'), state) or '建议在相同研究对象与数据划分下设置基线和方法对照，分别记录主要指标与困难样本。'
        conditions = prose(row.get('conditions'), state) or '先开展小规模对照研究，再按实际结果确定进一步研究的范围。'
        reason += ' ' + refs(field_refs(row, 'reason'))
        design += ' ' + refs(field_refs(row, 'design'))
        conditions += ' ' + refs(field_refs(row, 'conditions'))
        references = ''
        candidate_blocks.append({'kind': 'candidate_detail', 'index': index, 'title': candidate.display_title or candidate.title,
            'show_status': False, 'fields': [{'label': '核心理由', 'value': reason}, {'label': '研究设计', 'value': design},
                                           *([{'label': '代表文献', 'value': references}] if references else []),
                                           {'label': '适用条件与验证建议', 'value': conditions}]})
        # The overview helps select a topic; its reasoning is presented once
        # in the details immediately below, rather than in oversized cells.
        priority_rows.append({'优先级': index, '候选题': candidate.display_title or candidate.title})
    sections = state.report_plan.sections
    for section in sections:
        key = section['key']
        figures = [b for b in section.get('blocks', []) if b.get('kind') == 'figure_ref']
        if key == 'abstract':
            section['blocks'] = paragraphs('summary') or [{'kind': 'lead', 'text': state.report_plan.executive_summary}]
            state.report_plan.executive_summary = section['blocks'][0]['text']
        elif key == 'candidate_evaluation':
            section['blocks'] = [{'kind': 'table_ref', 'key': 'candidate_priority'}, *candidate_blocks]
        elif key == 'literature_trends':
            section['blocks'] = (paragraphs('progress') or [{'kind': 'paragraph', 'text': '相关研究按研究主题及其方法组织；下图统计本次检索样本，不代表领域全部成果。'}]) + figures
        elif key == 'cross_discipline':
            opportunity_rows = opportunity_blocks()
            analysis = state.stage_artifacts.get('landscape_trend', {}).get('analysis', {})

            def saved_summary(key, period):
                summary = analysis.get(key)
                if isinstance(summary, dict) and str(summary.get('text') or '').strip():
                    return summary
                rows = [item for item in analysis.get('method_evolution', []) if item.get('period') == period]
                return {'text': ' '.join(str(item.get('finding') or '').strip() for item in rows if item.get('finding')),
                        'evidence_ids': list(dict.fromkeys(eid for item in rows for eid in item.get('evidence_ids', [])))}

            method_summary = saved_summary('method_evolution_summary', 'present')
            future_summary = saved_summary('future_research_summary', 'future')
            summary_blocks = []
            if method_summary.get('text'):
                summary_blocks.append({'kind': 'paragraph', 'text': '方法演进：' + prose(method_summary['text'], state) + ' ' + refs(method_summary.get('evidence_ids'))})
            if future_summary.get('text'):
                summary_blocks.append({'kind': 'paragraph', 'text': '后续研究方向：' + prose(future_summary['text'], state) + ' ' + refs(future_summary.get('evidence_ids'))})
            section['blocks'] = figures + summary_blocks + opportunity_rows
        elif key == 'conclusion':
            section['blocks'] = paragraphs('recommendations') or [{'kind': 'paragraph', 'text': '优先确定研究对象、比较基线与评价条件，开展小规模预研，根据误差分析调整研究设计。'}]
        if key != 'references':
            section['content'] = '\n'.join(b.get('text', '') for b in section['blocks'])
    for table in state.report_plan.table_specs:
        if table['key'] == 'candidate_priority':
            table['rows'] = priority_rows
            table['columns'] = ['优先级', '候选题']
            table['column_widths'] = [0.8, 6.0]
        elif table['key'] == 'cross_matrix':
            used.update(eid for link in state.crosslinks for eid in link.support_evidence_ids[:2])
    # Include every actually cited source, including the graph's opportunity data.
    for link in selected_opportunities(state):
        used.update(link.support_evidence_ids)
    reference_section = next(s for s in sections if s['key'] == 'references')
    items = [{'number': state.report_plan.citation_map[e.evidence_id], 'evidence_id': e.evidence_id,
              'title': e.title, 'authors': ', '.join(e.authors), 'year': e.year, 'venue': e.venue, 'doi': e.doi,
              'paper_id': e.paper_id, 'url': state.report_plan.citation_links.get(e.evidence_id, '')}
             for e in state.evidence_pool if e.evidence_id in used]
    reference_section['blocks'] = [{'kind': 'reference_list', 'items': items}, {'kind': 'table_ref', 'key': 'cross_matrix'}]
    reference_section['content'] = '\n'.join(f"[{r['number']}] {r['title']} ({r['year'] or ''}) {r['url']}" for r in items)
    state.stage_artifacts['report_publish']['reader_source_review'] = {'checked': bool(draft), 'source_ids': sorted(used)}
    state.report_plan.limitations = ['研究建议依据本次检索文献提出，方法迁移效果需在相应研究对象与评价条件下检验。']
