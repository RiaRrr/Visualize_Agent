"""Generate, render, review, and revise one problem."""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from .browser import render_html
from .config import ROOT, Settings
from .model_api import ModelClient
from .rubric import REVIEW_SCORE_INSTRUCTIONS, empty_scores, normalize_overall_score, normalize_scores
from .storage import Store

GENERATOR_INSTRUCTIONS = """你是一位理科教师和网页可视化设计师。请阅读题目图片，输出一个完整、可离线打开的 HTML5 文件，用中文展示正确的题意、关键条件、分步解答和结论。
目标是让用户在页面中直观感受到答案是怎么来的，并能在适合的题目中通过操作探索关键关系。请遵循随后提供的五项 skill 汇集指导。
目标读者是刚读完题、还不知道解法的学生。页面开头先建立原题给了什么、要求什么，再解释非显然的条件转化：原题条件如何推到新的表示、这个表示代表什么及合法范围，然后才让学生操作对应控件、看到最终结论。斜率、参数、辅助量或初始数值若非题设直接给出，不要在开头当作已知；示例值要说明只是一组合法状态。关键转化尽量有图示或状态变化支撑，简单显然的步骤可简短处理。
尽可能让学生通过可交互的图理解解答。凡能用图形变化、状态切换或对照解释的关键步骤，优先用视觉证据呈现，可在推导各处加入多个有用图解或让主图分步变化，不要只放一张开头图再写大段说明。文字仅保留必要的题设、操作提示、核心解释、公式与结论；非显然条件转化仍须说明依据。视题目选择滑块、拖拽、步骤切换、播放/暂停或适量动画，操作应改变合法状态并同步更新图形、标注、数值和结论；没有自然可变条件时使用分步高亮或静态图解，不制造装饰控件。数学公式可用 HTML/SVG/CSS/MathML，禁止依赖 CDN、外部字体、网络资源或额外文件。不要使用 emoji 作为图示或图标，它们在离线环境可能显示为方框；请用 SVG 或 CSS 绘制。页面应适合桌面和手机，文字与图形清晰，不遮挡。若图片信息不足，请在页面中说明不确定点，不要编造条件。
只返回 <!doctype html> 开始的 HTML 源码，不要 Markdown 代码围栏。HTML 不超过 120KB。"""

REVIEWER_INSTRUCTIONS = """你是一位严格、独立的理科教师兼页面质检员。请根据第一张原题图片核对 HTML 的题意、条件、推导、计算和结论，再根据第二张渲染截图检查视觉呈现、可读性与图解是否帮助理解。HTML 源码也会附在文字中。
只有题意和解答正确、图示有教学价值、页面可读、无明显错误或遗漏时才通过。核对图中的点位、曲线、标注与公式和题目条件是否一致；抛物线等解析曲线若明显由少量直线段拼成、出现可见折角，不得通过。检查可图解的关键推导是否主要靠可操作的图、分步状态、高亮或前后对照来解释，文字是否只保留必要的依据、公式和提示；若重要转折只有长段文字，应指出具体步骤与可改进的图解方式，并反映在页面效果、交互效果和教学效果评分中。若页面提供滑块或其他交互，检查 HTML/JS 是否同步更新相关图形、点位、标签和结果，不能只有数字变化。截图中出现缺字、方框字形或图标无法显示时不得通过。题图模糊导致无法确认答案时不得通过。没有自然可变条件时，不因缺少滑块或动画而拒绝。
""" + "\n" + REVIEW_SCORE_INSTRUCTIONS

SKILL_GUIDANCE = (ROOT / "app" / "prompts" / "math_current.md").read_text(encoding="utf-8")


def generator_prompt(feedback: str = "", previous_html: str = "") -> str:
    prompt = GENERATOR_INSTRUCTIONS + "\n\n" + SKILL_GUIDANCE
    if feedback:
        prompt += f"\n\n上一轮审核意见：\n{feedback}\n请根据意见改进，保留正确部分。上一轮 HTML：\n{previous_html}"
    return prompt


def clean_html(raw: str) -> str:
    value = raw.strip()
    match = re.search(r"```(?:html)?\s*([\s\S]*?)```", value, re.I)
    if match:
        value = match.group(1).strip()
    start = re.search(r"<!doctype\s+html\s*>|<html\b", value, re.I)
    if start:
        value = value[start.start():]
    return value.strip()


def validate_html(html: str) -> list[str]:
    errors = []
    if not re.search(r"<!doctype\s+html\s*>", html[:200], re.I) or not re.search(r"<html\b", html, re.I) or not re.search(r"</html\s*>", html, re.I):
        errors.append("需要完整的 HTML5 文档")
    if len(html.encode("utf-8")) > 120_000:
        errors.append("HTML 超过 120KB")
    if re.search(r"[\U0001F000-\U0001FAFF]", html):
        errors.append("包含 emoji 图示或图标，离线字体可能显示为方框；请改用 SVG/CSS")
    if re.search(r"<(?:iframe|frame|object|embed|base|form)\b|<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh", html, re.I):
        errors.append("包含不允许的嵌入或跳转元素")
    class ResourceChecker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.external = False
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name in {"src", "href", "srcset", "poster", "action", "xlink:href"} and value:
                    if not value.strip().startswith(("data:", "#")):
                        self.external = True
    checker = ResourceChecker()
    checker.feed(html)
    if checker.external or re.search(r"@import\b|url\s*\(\s*['\"]?(?!data:|#)", html, re.I):
        errors.append("包含外部资源引用")
    if re.search(r"\b(?:fetch\s*\(|XMLHttpRequest|WebSocket|EventSource|navigator\.sendBeacon|import\s*\()", html, re.I):
        errors.append("包含网络请求代码")
    return errors


def parse_review(raw: str) -> dict:
    value = raw.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", value, re.I)
    if fenced:
        value = fenced.group(1)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {"passed": False, "summary": "审核模型没有返回有效 JSON", "issues": ["请重新核对题意、解答和页面呈现"],
                "scores": empty_scores(), "overall_score": None}
    if not isinstance(data, dict) or not isinstance(data.get("passed"), bool):
        return {"passed": False, "summary": "审核结果格式不正确", "issues": ["请重新核对题意、解答和页面呈现"],
                "scores": empty_scores(), "overall_score": None}
    issues = data.get("issues")
    if not isinstance(issues, list):
        issues = []
    issues = [str(x).strip()[:500] for x in issues if str(x).strip()]
    passed = data["passed"] and not issues
    if not passed and not issues:
        issues = ["审核未通过，请检查题意、推导和图示"]
    return {"passed": passed, "summary": str(data.get("summary", ""))[:500], "issues": issues,
            "scores": normalize_scores(data.get("scores")),
            "overall_score": normalize_overall_score(data.get("overall_score"))}


class Pipeline:
    def __init__(self, settings: Settings, store: Store, client: ModelClient | None = None, renderer=None):
        self.settings = settings
        self.store = store
        self.client = client or ModelClient(settings)
        self.renderer = renderer or render_html

    async def run(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task:
            return
        image = self.store.root / task_id / task["image_name"]
        original = [(image, task["image_mime"])]
        feedback = ""
        previous_html = ""
        completed_rounds = task["rounds"]
        config = self.store.config(task_id) or {}
        max_rounds = config.get("max_rounds", self.settings.max_rounds)
        generator_model = config.get("generator_model", self.settings.generator_model)
        reviewer_model = config.get("reviewer_model", self.settings.reviewer_model)
        generator_provider = config.get("generator_provider")
        reviewer_provider = config.get("reviewer_provider")
        round_number = len(completed_rounds)
        if completed_rounds:
            last_round = completed_rounds[-1]
            previous_html = (self.store.root / task_id / last_round["html_name"]).read_text(encoding="utf-8")
            feedback = "\n".join(f"- {issue}" for issue in last_round["issues"])
        try:
            if completed_rounds and completed_rounds[-1]["passed"]:
                final_name = self.store.save_final(task_id, previous_html)
                self.store.set_state(task_id, "passed", round_number, f"审核通过，最终文件：{final_name}", final_name, stage="passed")
                return
            for round_number in range(len(completed_rounds) + 1, max_rounds + 1):
                self.store.set_state(task_id, "running", round_number, f"第 {round_number} 轮：正在调用生成模型", stage="generating")
                prompt = config.get("generator_prompt") or generator_prompt()
                if feedback:
                    prompt += f"\n\n上一轮审核意见：\n{feedback}\n请根据意见改进，保留正确部分。上一轮 HTML：\n{previous_html}"
                started = time.monotonic()
                raw = await self.client.complete(
                    generator_model, prompt, original, max_tokens=self.settings.generator_max_tokens,
                    progress=lambda message: self.store.log_event(task_id, "generating", round_number, message),
                    **({"provider": generator_provider} if generator_provider else {}))
                self.store.add_timing(task_id, "generation_seconds", time.monotonic() - started)
                html = clean_html(raw)
                previous_html = html
                self.store.set_state(task_id, "running", round_number, f"第 {round_number} 轮：检查 HTML 与离线资源", stage="validating")
                errors = validate_html(html)
                screenshot = None
                if not errors:
                    self.store.set_state(task_id, "running", round_number, f"第 {round_number} 轮：浏览器渲染与手机布局检查", stage="rendering")
                    try:
                        screenshot = await self.renderer(html)
                    except Exception as exc:
                        errors.append(f"浏览器渲染失败：{str(exc)[:250] or type(exc).__name__}")
                if errors:
                    review = {"passed": False, "summary": "页面基础检查未通过", "issues": errors,
                              "source": "local_validator", "scores": empty_scores()}
                else:
                    self.store.set_state(task_id, "running", round_number, f"第 {round_number} 轮：审核题意、解答与图示", stage="reviewing")
                    # The screenshot is temporarily saved so the common image API can read it.
                    temp_path = self.store.root / task_id / ".review_screenshot.png"
                    try:
                        temp_path.write_bytes(screenshot)
                        review_prompt = (config.get("reviewer_prompt") or REVIEWER_INSTRUCTIONS) + "\n\nHTML 源码：\n" + html
                        started = time.monotonic()
                        raw_review = await self.client.complete(
                            reviewer_model, review_prompt, original + [(temp_path, "image/png")],
                            max_tokens=2000,
                            progress=lambda message: self.store.log_event(task_id, "reviewing", round_number, message),
                            **({"provider": reviewer_provider} if reviewer_provider else {}))
                        self.store.add_timing(task_id, "review_seconds", time.monotonic() - started)
                        review = parse_review(raw_review)
                        review["source"] = "model"
                    finally:
                        temp_path.unlink(missing_ok=True)
                saved = self.store.save_round(task_id, round_number, html, screenshot, review)
                self.store.log_event(task_id, "reviewing", round_number,
                                     f"第 {round_number} 轮文件已保存：{saved['html_name']}；{review['summary']}")
                if review["passed"]:
                    final_name = self.store.save_final(task_id, html)
                    self.store.set_state(task_id, "passed", round_number, f"审核通过，最终文件：{final_name}", final_name, stage="passed")
                    return
                feedback = "\n".join(f"- {issue}" for issue in review["issues"])
                self.store.set_state(task_id, "running", round_number,
                                     f"第 {round_number} 轮未通过：{'; '.join(review['issues'])[:300]}；准备返修", stage="revising")
            self.store.set_state(task_id, "failed", round_number,
                                 f"达到 {max_rounds} 轮上限；可查看最后草稿和审核意见", stage="failed")
        except Exception as exc:
            self.store.set_state(task_id, "error", round_number, str(exc)[:500], stage="error")
