"""Strict, recoverable protocols shared by both research report composers."""
from __future__ import annotations

import json
import re


SCIENTIFIC_IDENTIFIER = r'(?<![A-Za-z0-9])(?:Landsat|Sentinel)\s*[-–]?\s*\d+[A-Za-z]?(?:\s*/\s*\d+[A-Za-z]?)*(?![A-Za-z0-9])'


def review_envelope(value):
    """Accept known transport wrappers without changing the review decision."""
    for _ in range(3):
        if not isinstance(value, dict) or 'checked' in value:
            break
        children = [value[key] for key in ('review', 'result', 'data')
                    if isinstance(value.get(key), dict)]
        if len(children) != 1:
            break
        value = children[0]
    return value


def review_response(value, locations, *, fields=False):
    """Unwrap known envelopes, but never infer approval or missing corrections."""
    value = review_envelope(value)
    if not isinstance(value, dict):
        return None, 'response_not_object'
    if value.get('checked') is not True:
        return None, 'review_not_approved' if value.get('checked') is False else 'missing_boolean_checked'
    if not isinstance(value.get('replacements'), list):
        return None, 'missing_replacements_array'
    seen = set()
    for row in value['replacements']:
        if (not isinstance(row, dict) or type(row.get('index')) is not int
                or not isinstance(row.get('key'), str)
                or (fields and not isinstance(row.get('field'), str))):
            return None, 'invalid_replacement_location'
        location = (row.get('key'), row['index'], row.get('field')) if fields else (row.get('key'), row['index'])
        if location not in locations or location in seen:
            return None, 'invalid_replacement_location'
        if not isinstance(row.get('text'), str):
            return None, 'invalid_replacement_content'
        # Removing a rejected assertion needs no citation. An explicit empty
        # replacement must survive protocol recovery so it cannot accidentally
        # turn a source rejection into approval of the original paragraph.
        if row['text'] == '' and 'evidence_ids' not in row:
            row = {**row, 'evidence_ids': []}
        if (not isinstance(row.get('evidence_ids'), list)
                or any(not isinstance(eid, str) for eid in row['evidence_ids'])):
            return None, 'invalid_replacement_content'
        seen.add(location)
    replacements = [dict(row, evidence_ids=row.get('evidence_ids', [])) for row in value['replacements']]
    return {**value, 'replacements': replacements}, ''


def response_diagnostic(value, reason='', **metadata):
    """Bounded model output only; no request configuration or credentials."""
    return {**metadata, 'reason': reason,
            'response_keys': sorted(str(key) for key in value) if isinstance(value, dict) else [],
            'response_preview': json.dumps(value, ensure_ascii=False, default=str)[:1600]}


def scientific_identifiers(text):
    # Named product/version identifiers are not experimental measurements.
    # Keep this explicit: arbitrary words followed by numbers may be thresholds.
    values = []
    for match in re.finditer(SCIENTIFIC_IDENTIFIER, str(text), re.I):
        name = re.match(r'(Landsat|Sentinel)', match.group(), re.I).group().lower()
        values.extend(name + part.lower() for part in re.findall(r'\d+[A-Za-z]?', match.group()))
    return values


def numeric_values(text):
    text = re.sub(SCIENTIFIC_IDENTIFIER, '', str(text), flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-/]\d+)+(?![A-Za-z0-9])', '', text)
    # Consume the whole token before checking boundaries. A unit or p-value
    # marker must never make the regex backtrack from 0.5ha to a false 0.
    text = re.sub(r'(?<![A-Za-z0-9])316L(?![A-Za-z0-9])', '', text, flags=re.I)
    values = []
    for match in re.finditer(r'\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', text):
        start, end = match.span()
        prefix = text[:start]
        if start and (text[start - 1].isalnum() and text[start - 1].isascii()):
            if not re.search(r'(?:\bp|\bHI)$', prefix, re.I):
                continue
        suffix = re.match(r'[A-Za-z]+', text[end:])
        if suffix and not re.fullmatch(r'ha|mm|cm|km|nm|mg|kg|ml|ms|Hz|Pa|K|V|W|s|h|g|m|L', suffix.group(), re.I):
            continue
        values.append(match.group())
    return values
