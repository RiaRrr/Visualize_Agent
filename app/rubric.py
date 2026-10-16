"""Shared five-dimension rubric for model and human review records."""
from __future__ import annotations

SCORE_FIELDS = (
    ("problem_fidelity", "一致性", "题目条件、对象、所求及示例状态与原题一致"),
    ("solution_correctness", "正确性", "推导、计算、定义域、结论与图中数值正确"),
    ("page_quality", "页面效果", "文字图形清晰、层级合理，桌面与手机均可读"),
    ("interaction_quality", "交互效果", "交互量有清楚来历和合法范围，操作后图形、公式、数值同步；无需交互的题目可凭恰当的静态表达得高分"),
    ("teaching_effectiveness", "教学效果", "刚读完题、尚不知道解法的学生，能从题设出发理解关键条件转化、每步依据和答案由来"),
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


REVIEW_SCORE_INSTRUCTIONS = (
    "按以下五项分别给 1–5 整数分（1=严重问题，3=部分达标，5=优秀）：\n"
    + "\n".join(f"- {key}（{label}）：{description}" for key, label, description in SCORE_FIELDS)
    + "\n以刚读完题、尚不知道解法的学生视角评价教学效果。按截图实际视觉阅读顺序检查：首屏是否先建立原题关系，而非抢先给出结论或不明来历的控件；左右并排时，控件旁是否先说明从题设如何得到参数与范围，不能用右侧靠后的说明代替。检查首次引入的参数、斜率、控制量、示例值是否从原题条件交代来源、变换依据和合法范围；"
    "如果关键非显然转化被跳过，学生必须先会解题才能理解页面，教学效果不能给高分，并在 issues 中写明缺口。"
    "合格判定独立于分数；题意或解答有实质错误、关键转化缺失、图形明显误导、严重不可读时不得通过。"
    "仅因简单题不需要交互，不应降低交互效果分或判不合格。"
    "只返回 JSON 对象：{\"passed\":布尔值,\"scores\":{\"problem_fidelity\":1到5整数,\"solution_correctness\":1到5整数,"
    "\"page_quality\":1到5整数,\"interaction_quality\":1到5整数,\"teaching_effectiveness\":1到5整数},"
    "\"summary\":\"简短结论\",\"issues\":[\"可执行的具体修改意见\"]}。不通过时 issues 至少一项。"
)
