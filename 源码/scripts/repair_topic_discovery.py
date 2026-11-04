"""Create a discovery revision using a task's existing, traceable literature.

This local repair preserves the source task and its frozen report. It reviews
themes, classifies the existing sources, then runs the regular analysis and
publication stages. It does not manufacture another retrieval run.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.discovery import selected_opportunities
from app.agents.topic_selection.schemas import TopicSelectionState, TopicStageTrace
from app.core.topic_task_manager import TopicTaskManager
from app.repositories.content_assessment_task_history import get_task_history_repo
from app.services.deepseek_client import DeepSeekClient
from app.services.topic_task_persistence import (
    mark_topic_task_failed, now_beijing_iso, persist_topic_task_state,
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


async def main(args):
    repo = get_task_history_repo()
    source = await repo.get(args.source, args.scope)
    if not source or source.get('task_type') != '智能选题':
        raise ValueError('找不到指定范围内的智能选题任务')
    source_hash = digest(source['state'])
    child_id = args.child or f'run_{uuid4().hex[:12]}'
    if await repo.get(child_id, args.scope):
        raise ValueError('补正版编号已存在，请查看现有结果，避免重复执行')
    state = TopicSelectionState.model_validate(source['state'])
    if not state.evidence_pool:
        raise ValueError('原任务没有可复用的检索文献')
    now = now_beijing_iso()
    state.parent_task_id = args.source
    state.checkpoint_id = child_id
    state.user_scope = args.scope
    state.current_stage = 'theme_decomposition'
    state.checkpoint_revision = 0
    state.error_message = ''
    state.confirmation = type(state.confirmation)()
    state.report_plan = type(state.report_plan)()
    state.report_snapshot = type(state.report_snapshot)()
    state.candidate_selection = None
    state.output_markdown = ''
    state.output_json = {}
    state.landscape_snapshot = None
    state.future_work_signals = []
    state.crosslinks = []
    state.gaps = []
    state.candidates = []
    state.score_breakdowns = []
    state.quality_gate = type(state.quality_gate)()
    state.review_tasks = []
    state.resolution_logs = []
    state.model_insights = []
    state.active_progress = None
    state.progress_events = []
    state.task_profile.update({
        'rerun_kind': 'discovery_revision',
        'discovery_revision': {'source_task_id': args.source, 'source_state_sha256': source_hash,
            'reused_evidence_ids': [e.evidence_id for e in state.evidence_pool],
            'retrieval_policy': 'reuse_original_sources_no_new_search', 'created_at': now},
    })
    state.versions = type(state.versions)(
        input_version=state.versions.input_version,
        query_plan_version=state.versions.query_plan_version,
        evidence_pool_version=state.versions.evidence_pool_version,
        parent_report_version=state.versions.report_version,
    )
    reused = {'input_modeling', 'retrieval_evidence'}
    state.stage_trace = [trace if trace.stage_key in reused else TopicStageTrace(stage_key=trace.stage_key, stage_name=trace.stage_name)
                         for trace in state.stage_trace]
    state.stage_artifacts = {key: value for key, value in state.stage_artifacts.items() if key in reused}
    state.process_trace = {key: value for key, value in state.process_trace.items() if key in reused}
    state.model_calls = []
    state.correction_context = type(state.correction_context)(
        source_task_id=args.source, reason='修复主题覆盖、方法演进及研究机会展示，复用原任务真实文献',
        operator=args.scope, rerun_at=now, status='rerun_created')
    await repo.create({'task_id': child_id, 'user_scope': args.scope, 'user_name': args.scope,
        'title': state.input_profile.title, 'task_type': '智能选题', 'raw_user_input': state.raw_user_input,
        'status': 'running', 'current_stage': state.current_stage, 'created_at': now, 'updated_at': now,
        'parent_task_id': args.source, 'rerun_kind': 'discovery_revision', 'state': state.model_dump(),
        'total_steps': len(state.stage_trace), 'completed_steps': len(reused), 'needs_confirmation': False})
    print(json.dumps({'child_task_id': child_id, 'source_task_id': args.source}, ensure_ascii=False), flush=True)

    async def progress(current):
        await persist_topic_task_state(task_id=child_id, user_scope=args.scope, state=current, status='running')

    try:
        agent = TopicSelectionAgent()
        health_client = DeepSeekClient()
        try:
            health = await health_client.preflight()
        finally:
            await health_client.aclose()
        if health.get('status') != 'available':
            raise RuntimeError('模型暂不可用，未执行补正分析')
        state.analysis_policy.model_preflight_status = 'available'
        state.analysis_policy.model_preflight_reason = ''
        state.analysis_policy.mode = 'deep'
        state.analysis_policy.model_enabled = True
        trace = next(t for t in state.stage_trace if t.stage_key == 'theme_decomposition')
        trace.status = 'running'
        trace.started_at = datetime.now().isoformat()
        began = time.monotonic()
        await agent._theme_decomposition(state, progress)
        theme_analysis = state.stage_artifacts['theme_decomposition'].get('analysis')
        assignments = await agent._stage_model_json(state, progress, stage_key='theme_decomposition',
            prompt_version='topic-existing-source-classification-v1', purpose='按新主题整理原检索文献',
            payload={'themes': [n.model_dump() for n in state.theme_tree.nodes],
                'sources': [{'evidence_id': e.evidence_id, 'title': e.title, 'abstract': e.abstract[:2200]} for e in state.evidence_pool],
                'instruction': '只按所给题名和摘要将原有文献归入实际相关的主题。允许一篇关联多个主题；不相关时theme_ids为空。不能臆测原文方法，不新增文献或主题编号。逐篇返回evidence_id、theme_ids和简短reason。此步骤是重分类，不是重新检索。',
                'output_schema': {'assignments': [{'evidence_id': '实际ID', 'theme_ids': ['实际主题ID'], 'reason': '关联原因'}]}},
            expected_keys={'assignments'}, max_tokens=3600)
        if not assignments or not isinstance(assignments.get('assignments'), list):
            raise RuntimeError('原文献主题重分类未完成，保留补正任务供检查')
        known = {node.theme_id for node in state.theme_tree.nodes}
        by_id = {row.get('evidence_id'): row for row in assignments['assignments'] if isinstance(row, dict)}
        if set(by_id) != {record.evidence_id for record in state.evidence_pool}:
            raise RuntimeError('文献重分类未覆盖原文献清单，未沿用旧主题编号')
        for record in state.evidence_pool:
            record.theme_ids = list(dict.fromkeys(t for t in by_id[record.evidence_id].get('theme_ids', []) if t in known))
        by_evidence = {e.evidence_id: e.theme_ids for e in state.evidence_pool}
        for chunk in state.evidence_chunks:
            chunk.theme_ids = by_evidence.get(chunk.evidence_id, [])
        for node in state.theme_tree.nodes:
            node.evidence_ids = [e.evidence_id for e in state.evidence_pool if node.theme_id in e.theme_ids]
        artifact = state.stage_artifacts['theme_decomposition']
        artifact['source_reclassification'] = assignments
        artifact['analysis'] = theme_analysis
        state.task_profile['discovery_revision']['source_reclassification'] = assignments
        version = digest([(e.evidence_id, e.theme_ids) for e in state.evidence_pool])[:12]
        state.versions.evidence_pool_version += '-classification-' + version
        state.evidence_lock.evidence_pool_version = state.versions.evidence_pool_version
        trace.status = 'completed'
        trace.completed_at = datetime.now().isoformat()
        trace.duration_seconds = time.monotonic() - began
        trace.summary = f'从原研究问题整理{len(state.theme_tree.nodes)}个主题，并重新关联已有文献'
        state.resume_from_stage = 'landscape_trend'
        await progress(state)
        await TopicTaskManager()._run_pipeline(child_id, args.scope, state)
    except Exception as exc:
        await mark_topic_task_failed(child_id, args.scope, state, str(exc))
        raise
    finally:
        current = await repo.get(child_id, args.scope)
        final = TopicSelectionState.model_validate(current['state'])
        old = await repo.get(args.source, args.scope)
        result = {'task_id': child_id, 'parent_task_id': args.source, 'status': current['status'],
            'error': current.get('error_message'), 'source_unchanged': digest(old['state']) == source_hash,
            'themes': [{'id': n.theme_id, 'name': n.label, 'evidence_count': len(n.evidence_ids)} for n in final.theme_tree.nodes],
            'evidence_count': len(final.evidence_pool), 'opportunity_count': len(selected_opportunities(final)),
            'opportunities': [{'name': l.research_opportunity, 'method': l.transferable_method, 'sources': l.method_evidence} for l in selected_opportunities(final)],
            'method_evolution': final.stage_artifacts.get('landscape_trend', {}).get('analysis', {}).get('method_evolution', []),
            'charts': [{'key': c.get('key'), 'status': c.get('data_status')} for c in final.report_plan.chart_specs],
            'template': final.report_plan.template_version, 'publication_status': final.report_plan.publication_status,
            'snapshot_frozen': final.report_snapshot.frozen}
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in result.items() if k not in {'opportunities', 'method_evolution'}}, ensure_ascii=False), flush=True)
    if current['status'] != 'completed':
        raise RuntimeError('补正版尚未完成，请检查保存的执行记录')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--scope', required=True)
    parser.add_argument('--child')
    parser.add_argument('--output', required=True)
    asyncio.run(main(parser.parse_args()))
