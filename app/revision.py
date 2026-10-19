"""Apply exact, bounded HTML edits without writing partial results."""
import json
import re


def apply_html_patch(html: str, raw: str) -> str:
    value = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError('局部修订必须返回有效 JSON') from exc
    edits = data.get('edits') if isinstance(data, dict) else None
    if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
        raise ValueError('局部修订应为 1–12 个 edits')
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('每个 edit 必须包含 old 和 new')
        old, new = edit.get('old'), edit.get('new')
        if not isinstance(old, str) or not old or not isinstance(new, str) or html.count(old) != 1:
            raise ValueError('局部修订的原文必须精确且仅匹配一次')
        if len(old) > len(html) * .75:
            raise ValueError('局部修订不得整体重写页面')
        html = html.replace(old, new, 1)
    return html
