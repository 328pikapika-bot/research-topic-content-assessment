"""Readable chart data for portable Markdown reports."""
from __future__ import annotations

import base64
import io
import re
import zipfile


def normalize_saved_citations(value: str) -> str:
    """Apply current typography to legacy Markdown without changing its text."""
    return re.sub(
        r'''<sup\s+class=["']citation["']><a href=["']#ref-(\d+)["']>\[\1\]</a></sup>''',
        r'<a class="citation" href="#ref-\1">[\1]</a>',
        value,
    )


def superscript_citations(value: str) -> str:
    """Keep brackets and links at body size, including in Markdown viewers.

    The historical function name is retained for existing report callers.
    """
    value = re.sub(r"(\[\d+\])\s+(?=\[\d+\])", r"\1", value)
    return re.sub(
        r"\[(\d+)\]",
        r'<a class="citation" href="#ref-\1">[\1]</a>',
        value,
    )


def package_markdown(markdown: str, title: str) -> tuple[bytes, str]:
    """Bundle a Markdown report and its relative-path chart assets."""
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", title).strip(" ._") or "报告"
    assets: dict[str, bytes] = {}

    def replace_image(match: re.Match[str]) -> str:
        asset_path = f"images/figure-{len(assets) + 1:02d}.png"
        assets[asset_path] = base64.b64decode(match.group(2), validate=True)
        return f"![{match.group(1)}]({asset_path})"

    report = re.sub(r"!\[([^\]]*)\]\(data:image/png;base64,([A-Za-z0-9+/=]+)\)", replace_image, markdown)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.md", report)
        for path, content in assets.items():
            archive.writestr(path, content)
    return output.getvalue(), f"{safe_title}-Markdown.zip"

LABELS = {'points': '研究要点', 'evidence': '关联文献', 'links': '对应关系',
          'label': '名称', 'title': '标题', 'year': '年份', 'count': '数量',
          'dimension': '评估维度', 'score': '评分', 'score_range': '评分范围',
          'number': '引用编号', 'source': '研究要点', 'target': '关联文献',
          'value': '数值', 'unit': '单位', 'status': '状态', 'judgement': '判定',
          'claim_count': '研究要点数', 'evidence_count': '文献数', 'material_count': '材料数',
          'query_count': '查询数', 'name': '名称', 'date': '日期', 'event': '事件',
          'undated': '未记录年份的文献数', 'total': '文献总数'}


def chart_data_markdown(data):
    def cell(value):
        from app.common.retrieval.links import bibliography_text
        if isinstance(value, dict):
            return '；'.join(f'{LABELS.get(k, k)}：{cell(v)}' for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return '、'.join(cell(item) for item in value)
        return bibliography_text(value if value is not None else '未提供').replace('|', '｜').replace('\n', ' ')

    if isinstance(data, dict):
        lines = []
        # Resolve graph ids to their displayed labels in relationship rows.
        names = {row.get('id'): row.get('label', row.get('title', row.get('id')))
                 for key in ('points', 'evidence') for row in data.get(key, []) if isinstance(row, dict)}
        for key, value in data.items():
            label = '年度文献数量' if key == 'points' and isinstance(value, list) and value and isinstance(value[0], dict) and 'year' in value[0] else LABELS.get(key, key)
            lines.append(f'**{label}**')
            if key == 'links' and names:
                value = [{LABELS['source']: names.get(row.get('source'), row.get('source')),
                          LABELS['target']: names.get(row.get('target'), row.get('target'))} for row in value]
            lines.extend(chart_data_markdown(value))
        return lines
    if isinstance(data, list) and data and all(isinstance(row, dict) for row in data):
        columns = list(dict.fromkeys(key for row in data for key in row if key not in {'id', 'evidence_ids', 'claim_id', 'color'}))
        return ['| ' + ' | '.join(LABELS.get(k, k) for k in columns) + ' |',
                '| ' + ' | '.join('---' for _ in columns) + ' |',
                *['| ' + ' | '.join(cell(row.get(k, '')) for k in columns) + ' |' for row in data], '']
    if isinstance(data, list) and data and all(isinstance(row, (list, tuple)) and len(row) == 2 for row in data):
        return ['| 年份 | 数量 |', '| --- | --- |', *[f'| {cell(row[0])} | {cell(row[1])} |' for row in data], '']
    return [cell(data), ''] if data is not None and data != [] and data != {} else ['当前没有可用图表数据。', '']
