"""Public, checkable mathematical planning for optional guided HTML generation."""
from __future__ import annotations

import json
import re

from .profiles import PROMPTS


def parse_json_object(raw: str) -> dict:
    value = raw.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", value, re.I)
    if fenced:
        value = fenced.group(1).strip()
    if not value.startswith("{"):
        start, end = value.find("{"), value.rfind("}")
        value = value[start:end + 1] if start >= 0 and end > start else value
    # Models often put raw TeX commands (\frac, \sqrt, ...) in JSON strings.
    # Some prefixes such as \f are valid JSON escapes, so repair before parsing.
    repaired = re.sub(r'(?<!\\)\\(?!u[0-9a-fA-F]{4})(?!["\\/])', r"\\\\", value)
    try:
        result = json.loads(repaired)
    except json.JSONDecodeError:
        result = json.loads(_escape_inner_quotes(repaired))
    if not isinstance(result, dict):
        raise ValueError("规划必须是 JSON 对象")
    return result


def _escape_inner_quotes(value: str) -> str:
    """Repair model prose that uses quotation marks inside JSON string values."""
    result = []
    in_string = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            result.append(char)
            escaped = True
            continue
        if char == '"':
            if not in_string:
                in_string = True
            else:
                next_index = index + 1
                while next_index < len(value) and value[next_index].isspace():
                    next_index += 1
                if next_index < len(value) and value[next_index] not in ",:}]":
                    result.append("\\")
                else:
                    in_string = False
        result.append(char)
    return "".join(result)


def validate_plan(plan: dict) -> list[str]:
    issues = []
    for key in ("given", "goal", "derivation", "answer", "storyboard", "state", "checks"):
        value = plan.get(key)
        if value is None or value == "" or value == [] or value == {}:
            issues.append(f"缺少 {key}")
    for key in ("given", "goal", "derivation", "storyboard", "checks"):
        if key in plan and not isinstance(plan[key], list):
            issues.append(f"{key} 必须是数组")
    if isinstance(plan.get("derivation"), list):
        for index, step in enumerate(plan["derivation"], 1):
            if not isinstance(step, dict) or not all(step.get(field) for field in ("from", "reason", "to")):
                issues.append(f"推导第 {index} 步缺少 from/reason/to")
    if isinstance(plan.get("storyboard"), list):
        for index, step in enumerate(plan["storyboard"], 1):
            if not isinstance(step, dict) or not all(step.get(field) for field in ("show", "action", "insight")):
                issues.append(f"画面第 {index} 步缺少 show/action/insight")
    if isinstance(plan.get("state"), dict):
        if not isinstance(plan["state"].get("independent"), list) or not isinstance(plan["state"].get("derived"), list):
            issues.append("state 必须包含 independent 与 derived 数组")
    return issues


def plan_prompt(question: str, feedback: str = "", previous: dict | None = None,
                template: str | None = None) -> str:
    prompt = (template or (PROMPTS / "math_guided_plan.md").read_text(encoding="utf-8")).replace("{{QUESTION}}", question)
    if feedback:
        prompt += "\n\n规划核对意见：\n" + feedback
    if previous:
        prompt += "\n\n待修订规划：\n" + json.dumps(previous, ensure_ascii=False)
    return prompt


def plan_review_prompt(question: str, plan: dict, template: str | None = None) -> str:
    template = template or (PROMPTS / "math_guided_plan_review.md").read_text(encoding="utf-8")
    return template.replace("{{QUESTION}}", question).replace("{{PLAN}}", json.dumps(plan, ensure_ascii=False))


def guided_html_addendum(plan: dict, template: str | None = None) -> str:
    template = template or (PROMPTS / "math_guided_html.md").read_text(encoding="utf-8")
    return template.replace("{{PLAN}}", json.dumps(plan, ensure_ascii=False))
