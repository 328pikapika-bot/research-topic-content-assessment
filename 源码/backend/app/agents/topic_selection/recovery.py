"""Durable recovery protocol. Waiting never means a report passed review."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import BEIJING_TZ


class GenerationRecoveryRequired(RuntimeError):
    def __init__(self, stage: str, reason: str, message: str):
        super().__init__(message)
        self.stage, self.reason = stage, reason


def now():
    return datetime.now(BEIJING_TZ)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=BEIJING_TZ) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def implementation_signature():
    """Version the loaded process, not edits it has not loaded yet."""
    root = Path(__file__).resolve().parents[1]
    files = [Path(__file__), root / 'report_validation.py',
             root / 'topic_selection' / 'agent.py', root / 'topic_selection' / 'scholarly_report.py',
             root / 'topic_selection' / 'discovery.py', root.parent / 'services' / 'deepseek_client.py',
             root.parent / 'core' / 'topic_task_manager.py']
    return [(str(p.name), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files if p.exists()]


IMPLEMENTATION_SIGNATURE = implementation_signature()


def fingerprint(state):
    """Detect loaded fixes or source changes; never persist secrets."""
    try:
        from app.core.model_profiles import resolve_active_model_profile
        profile = resolve_active_model_profile()
        config = hashlib.sha256(str((profile.name, profile.base_url, profile.model, profile.api_key)).encode()).hexdigest()
    except Exception:
        config = 'not_configured'
    content = [state.raw_user_input, [(e.evidence_id, e.abstract, e.excerpt) for e in state.evidence_pool],
               [c.model_dump() for c in state.candidates]]
    return hashlib.sha256(json.dumps([IMPLEMENTATION_SIGNATURE, config, content], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def failure_kind(state, exc):
    """Model transport errors are recorded by the existing bounded caller."""
    calls = [c for c in getattr(state, 'model_calls', []) if c.stage_key == getattr(exc, 'stage', getattr(state, 'current_stage', ''))]
    last = calls[-1] if calls else None
    status = getattr(last, 'http_status', None)
    error = str(getattr(last, 'error_type', '') or getattr(last, 'error_code', '') or type(exc).__name__)
    if status in (401, 402, 403) or error in ('model_preflight_unavailable', 'ModelPreflightUnavailable', 'HTTPAuthorizationError', 'HTTPPaymentRequired', 'ModelProfileError'):
        return 'dependency'
    if (isinstance(exc, (TimeoutError, ConnectionError, OSError)) or status in (408, 429, 500, 502, 503, 504)
            or any(word in error for word in ('Timeout', 'Connect', 'ReadError', 'RemoteProtocol'))):
        return 'transient'
    return 'content' if isinstance(exc, GenerationRecoveryRequired) else 'system'


def schedule_recovery(state, exc, *, checkpoint=False):
    previous = state.output_json.get('generation_recovery', {})
    stage = getattr(exc, 'stage', None) or state.current_stage
    if stage in ('failed', 'completed', 'created'):
        stage = state.resume_from_stage or 'report_publish'
    kind = 'checkpoint' if checkpoint else failure_kind(state, exc)
    count = int(previous.get('attempts', 0)) + 1
    delay = 1 if checkpoint else (30, 120, 300)[min(count - 1, 2)] if count <= 3 else 900
    if kind == 'dependency':
        delay = 900
    retry_after = state.stage_artifacts.get(stage, {}).get('retry_after_seconds', 0)
    delay = max(delay, float(retry_after or 0))
    pending = list(previous.get('pending_stages') or [])
    if stage not in pending:
        pending.insert(0, stage)
    if stage != 'report_publish' and state.candidates and 'report_publish' not in pending:
        pending.append('report_publish')
    blocked = kind in ('content', 'system')
    record = {'enabled': True, 'phase': 'waiting_system' if blocked else 'waiting_service' if kind == 'dependency' else 'scheduled',
              'stage': stage, 'category': kind, 'attempts': count, 'pending_stages': pending,
              'next_retry_at': None if blocked else (now() + timedelta(seconds=delay)).isoformat(),
              'fingerprint': fingerprint(state), 'last_diagnostic': str(exc)[:500],
              'updated_at': now().isoformat(), 'lease_until': None}
    state.output_json['generation_recovery'] = record
    state.resume_from_stage = stage
    state.current_stage = stage
    state.confirmation.needed = False
    state.error_message = ''
    return record


def raise_for_transport(state, stage):
    error = GenerationRecoveryRequired(stage, 'model_response', '生成服务暂时不可用，已有分析已保存。')
    if failure_kind(state, error) in ('transient', 'dependency'):
        raise error
