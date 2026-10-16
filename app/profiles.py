"""Versioned, read-only prompt profiles for standalone teaching pages."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT
from .rubric import REVIEW_SCORE_INSTRUCTIONS


COMMON_GENERATOR = """你是一位严谨的理科教师和网页可视化设计师。阅读题目图片，准确识别题意、条件、单位、限制与所求，先核对解答，再制作让学生看出答案如何得到的中文教学网页。
目标读者是刚读完这道题、尚不知道解法的学生。页面开头先建立题设的已知、未知和所求，再引入帮助解题的表示方法；最终结论与推导出的控制量应在来历讲清后呈现，不要在首屏抢先作为已知条件。对于非显然的条件转化，说明“原题怎样说 → 为什么能这样转化 → 新量代表什么及其合法范围 → 它怎样帮助下一步”，并尽量用图示或状态变化让学生亲眼看到这座桥梁。交互控件出现前，先让学生知道它控制的是什么、从何而来；若采用左右并排布局，控件旁边也必须先给出这段桥梁，不能依赖右侧靠后的文字才解释。初始取值若只是一个合法例子，要标明示例而非唯一答案。简单、显然的转换可以一句话带过，不强制固定步骤数。
通常可用一个主要可视化说明核心关系。先找出题目中真正需要解释的关键推导或认知转折，再按题目难度决定是否在对应步骤附近加入局部图解、主图状态切换与高亮、动画、数值对照等视觉证据。让读者能把该步的条件、公式、图中对象和下一步结论对起来；避免只有开头一张图、后续可图解的关键推导却全靠长段文字。简单步骤可以用简短文字和公式，不规定图的数量、固定版式或每步都做动画。
图形、公式、标注和结论必须对应同一组合法状态。交互要同步更新相关图形与数值，不得只是装饰；分步切换应揭示关系，而非只隐藏或显示文字。信息不足时明确说明不确定点，不编造题目条件。
只输出一个完整的、以 <!doctype html> 开始的 HTML5 文件，不要 Markdown 围栏或附加说明；大小不超过 120KB。CSS、JavaScript、SVG、Canvas 和 MathML 可内联；不得依赖 CDN、外部字体、网络请求、外部图片、额外文件或发布服务。不要用 emoji 充当图标。保证桌面和手机可读、无裁切、控件可触达，动画提供静态可读状态。"""

COMMON_REVIEW = """你是独立的理科教师和页面质检员。第一张图是原题，第二张图是生成页面截图；HTML 源码也会附在文字中。核对题意、条件、推导、计算、单位、图示、结论、交互和离线可用性。图形的点位、形状、标注与公式必须一致；交互应同步更新相关视图。检查读者能否把关键推导的条件、图中对象和公式联系起来；若一个可图解的重要转折只剩长段文字，且主图也没有标注或状态变化支撑它，应指出具体哪一步需要怎样的视觉证据。局部图、主图高亮或切换、动画、对照均可；不要硬性要求每一步有图，也不要因简单题没有交互而拒绝。出现数学或科学错误、明显误导的图、裁切、缺字、不可读内容时不得通过。"""

SUBJECT_REVIEW = {
    "math": "重点核对定义域、严格不等号、边界与退化条件；解析曲线不得有明显折角，控制点和标记点应满足同一方程。",
    "physics": "重点核对受力对象、矢量方向、坐标正方向、单位量纲、守恒条件与理想化假设；动画不得违反题设约束或把示意运动当作精确计算。",
    "chemistry": "重点核对化学式、方程配平、原子与电荷守恒、状态和计量关系；分子结构与粒子示意不得凭空编造，动画需保留粒子数与身份。",
    "general": "重点核对题目事实、步骤顺序和图文对应；不要强加不适用的数学、物理或化学规则。",
}

SUBJECT_GENERATOR = {
    "math": "数学核对：先逐字辨认题图中的坐标、点名、括号、绝对值、等号与不等号，再写出用于作图的精确关系。题设命名的点不可擅自当成坐标原点；若同一记号可能有两种读法，应回到题图核验。每个图上点和曲线分支都须满足原始条件，而不只是代数变形后的必要条件。若用斜率、角度参数或坐标来操控图形，先说明它与题设几何约束的等价或必要关系，并只开放满足全部原始条件的取值；不要让学生误以为题目直接给出了这个参数。",
    "physics": "物理核对：区分题设的精确模型与仅用于解释的示意图，所有单位和方向以原题为准。",
    "chemistry": "化学核对：先检查题图中的元素下标、离子电荷、反应条件和状态符号，再确定粒子图。",
    "general": "事实核对：先从题图提取原始条件、来源与限制，不补造材料或隐含前提。",
}

PROFILE_FILES = {
    "math-current": ("数学 · 当前综合指导", "math", ROOT / "app" / "visualization_guidance.md"),
    "math-interactive": ("数学 · 交互白板方案", "math", ROOT / "app" / "prompts" / "math_interactive.md"),
    "math-workflow": ("数学 · 推导工作流方案", "math", ROOT / "app" / "prompts" / "math_workflow.md"),
    "physics": ("物理 · 过程与受力", "physics", ROOT / "app" / "prompts" / "physics.md"),
    "chemistry": ("化学 · 粒子与反应", "chemistry", ROOT / "app" / "prompts" / "chemistry.md"),
    "general": ("通用 · 关系与步骤", "general", ROOT / "app" / "prompts" / "general.md"),
}


@dataclass(frozen=True)
class PromptProfile:
    id: str
    name: str
    subject: str
    version: str
    generator_prompt: str
    reviewer_prompt: str


def get_profile(profile_id: str) -> PromptProfile:
    if profile_id not in PROFILE_FILES:
        raise ValueError("未知 Prompt 方案")
    name, subject, path = PROFILE_FILES[profile_id]
    guidance = path.read_text(encoding="utf-8").strip()
    generator = COMMON_GENERATOR + "\n\n" + SUBJECT_GENERATOR[subject] + "\n\n方案指导：\n" + guidance
    reviewer = COMMON_REVIEW + "\n" + SUBJECT_REVIEW[subject] + "\n\n" + REVIEW_SCORE_INSTRUCTIONS
    version = hashlib.sha256((generator + "\n" + reviewer).encode("utf-8")).hexdigest()[:12]
    return PromptProfile(profile_id, name, subject, version, generator, reviewer)


def list_profiles() -> list[dict]:
    return [{"id": p.id, "name": p.name, "subject": p.subject, "version": p.version,
             "generator_prompt": p.generator_prompt, "reviewer_prompt": p.reviewer_prompt}
            for p in (get_profile(profile_id) for profile_id in PROFILE_FILES)]
