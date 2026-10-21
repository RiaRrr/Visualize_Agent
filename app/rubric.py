"""Shared five-dimension rubric for model and human review records."""
from __future__ import annotations

SCORE_FIELDS = (
    ("problem_fidelity", "一致性", "题目条件、对象、所求及示例状态与原题一致"),
    ("solution_correctness", "正确性", "推导、计算、定义域、结论与图中数值正确"),
    ("page_quality", "页面效果", "文字图形清晰、层级合理，桌面与手机均可读"),
    ("interaction_quality", "交互效果", "交互量有清楚来历和合法范围，操作后图形、公式、数值同步；无需交互的题目可凭恰当的静态表达得高分"),
    ("teaching_effectiveness", "教学效果", "具备高中基础、刚读完题但不会做的学生，能通过可视化循序渐进理解条件转化和解法；关键步骤不跳跃，理解负担合理，能说明答案为何成立"),
)
SCORE_KEYS = tuple(key for key, _, _ in SCORE_FIELDS)


def empty_scores() -> dict[str, int | None]:
    return {key: None for key in SCORE_KEYS}


def normalize_scores(value: object) -> dict[str, int | None]:
    source = value if isinstance(value, dict) else {}
    scores = empty_scores()
    for key in SCORE_KEYS:
        score = source.get(key)
        if type(score) is int and 1 <= score <= 5:
            scores[key] = score
    return scores


def normalize_overall_score(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 5 else None
