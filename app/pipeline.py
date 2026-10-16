"""Generate, render, review, and revise one problem."""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from .browser import render_html
from .config import Settings
from .model_api import ModelClient
from .profiles import PROMPTS, get_profile
from .rubric import empty_scores, normalize_overall_score, normalize_scores
from .storage import Store, best_round, timestamp

def generator_prompt(feedback: str = "", previous_html: str = "") -> str:
    prompt = get_profile("auto").generator_prompt
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
        fixed_five = config.get("run_mode") == "fixed_five"
        generator_model = config.get("generator_model", self.settings.generator_model)
        reviewer_model = config.get("reviewer_model", self.settings.reviewer_model)
        generator_provider = config.get("generator_provider")
        reviewer_provider = config.get("reviewer_provider")
        generator_mode = config.get("generator_mode", "vision")
        reviewer_mode = config.get("reviewer_mode", "vision")
        ocr_provider = config.get("ocr_provider")
        ocr_model = config.get("ocr_model")
        round_number = len(completed_rounds)
        if completed_rounds:
            last_round = completed_rounds[-1]
            previous_html = (self.store.root / task_id / last_round["html_name"]).read_text(encoding="utf-8")
            feedback = ("上一轮已通过。请继续优化图示、教学顺序与手机可读性。"
                        if fixed_five and last_round["passed"] else "\n".join(f"- {issue}" for issue in last_round["issues"]))
        try:
            if completed_rounds and completed_rounds[-1]["passed"] and not fixed_five:
                final_name = self.store.save_final(task_id, previous_html)
                self.store.set_state(task_id, "passed", round_number, f"审核通过，最终文件：{final_name}", final_name, stage="passed")
                return
            question_ocr = ""
            if "ocr_text" in (generator_mode, reviewer_mode):
                existing_ocr = self.store.question_ocr_name(task_id)
                if existing_ocr:
                    question_ocr = (self.store.root / task_id / existing_ocr).read_text(encoding="utf-8")
                else:
                    if task.get("batch_id"):
                        for sibling in self.store.batch(task["batch_id"]):
                            if sibling["id"] == task_id or (sibling.get("ocr_provider"), sibling.get("ocr_model")) != (ocr_provider, ocr_model):
                                continue
                            sibling_ocr = self.store.question_ocr_name(sibling["id"])
                            if sibling_ocr:
                                question_ocr = (self.store.root / sibling["id"] / sibling_ocr).read_text(encoding="utf-8")
                                self.store.log_event(task_id, "question_ocr", 0, f"复用同批次题目 OCR：{sibling['id']}")
                                break
                    if not question_ocr:
                        self.store.set_state(task_id, "running", 0, "识别原题文字、条件与图形关系", stage="question_ocr")
                        started = time.monotonic()
                        ocr_usage = {}
                        succeeded = False
                        try:
                            question_ocr = await self.client.complete(
                                ocr_model, (PROMPTS / "question_ocr.md").read_text(encoding="utf-8"), original,
                                max_tokens=4000, provider=ocr_provider,
                                progress=lambda message: self.store.log_event(task_id, "question_ocr", 0, message),
                                usage=lambda value: ocr_usage.update(value=value))
                            succeeded = True
                        finally:
                            self.store.record_api_call(task_id, 0, "question_ocr", ocr_provider, ocr_model,
                                                       time.monotonic() - started, "completed" if succeeded else "error",
                                                       ocr_usage.get("value"))
                    if not question_ocr.strip():
                        raise ValueError("识图模型未返回题目内容")
                    (self.store.root / task_id / f"{timestamp()}_question_ocr.md").write_text(question_ocr, encoding="utf-8")
            for round_number in range(len(completed_rounds) + 1, max_rounds + 1):
                pending_path = self.store.root / task_id / f".pending_round_{round_number:02d}.html"
                if pending_path.is_file():
                    html = pending_path.read_text(encoding="utf-8")
                    self.store.log_event(task_id, "generating", round_number,
                                         f"第 {round_number} 轮：复用已生成 HTML，继续审核")
                else:
                    self.store.set_state(task_id, "running", round_number, f"第 {round_number} 轮：正在调用生成模型", stage="generating")
                    question_input = question_ocr if generator_mode == "ocr_text" else "题目图片已随本次 API 消息提供；请直接读取这张图片，以图中题目为准。"
                    prompt = (config.get("generator_prompt") or generator_prompt()).replace("{{QUESTION}}", question_input)
                    if feedback:
                        prompt += f"\n\n上一轮审核意见：\n{feedback}\n请根据意见改进，保留正确部分。上一轮 HTML：\n{previous_html}"
                    started = time.monotonic()
                    generation_usage = {}
                    generated = False
                    try:
                        raw = await self.client.complete(
                            generator_model, prompt, [] if generator_mode == "ocr_text" else original,
                            max_tokens=self.settings.generator_max_tokens,
                            progress=lambda message: self.store.log_event(task_id, "generating", round_number, message),
                            usage=lambda value: generation_usage.update(value=value),
                            **({"provider": generator_provider} if generator_provider else {}))
                        generated = True
                    finally:
                        elapsed = time.monotonic() - started
                        self.store.add_timing(task_id, "generation_seconds", elapsed)
                        self.store.record_api_call(task_id, round_number, "generator", generator_provider,
                                                   generator_model, elapsed, "completed" if generated else "error",
                                                   generation_usage.get("value"))
                    html = clean_html(raw)
                    html, removed_emoji = re.subn(r"[\U0001F000-\U0001FAFF]", "", html)
                    if removed_emoji:
                        self.store.log_event(task_id, "validating", round_number,
                                             f"已清除 {removed_emoji} 个不可靠的 emoji 字符")
                    pending_path.write_text(html, encoding="utf-8")
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
                    page_description = ""
                    if reviewer_mode == "ocr_text":
                        description_name = self.store.page_description_name(task_id, round_number)
                        if description_name:
                            page_description = (self.store.root / task_id / description_name).read_text(encoding="utf-8")
                        else:
                            self.store.set_state(task_id, "running", round_number,
                                                 f"第 {round_number} 轮：描述页面截图", stage="page_description")
                            temp_description_image = self.store.root / task_id / ".description_screenshot.png"
                            try:
                                temp_description_image.write_bytes(screenshot)
                                started = time.monotonic()
                                description_usage = {}
                                succeeded = False
                                try:
                                    page_description = await self.client.complete(
                                        ocr_model, (PROMPTS / "page_description.md").read_text(encoding="utf-8"),
                                        [(temp_description_image, "image/png")], max_tokens=self.settings.description_max_tokens, provider=ocr_provider,
                                        progress=lambda message: self.store.log_event(task_id, "page_description", round_number, message),
                                        usage=lambda value: description_usage.update(value=value))
                                    succeeded = True
                                finally:
                                    self.store.record_api_call(task_id, round_number, "page_description", ocr_provider,
                                                               ocr_model, time.monotonic() - started,
                                                               "completed" if succeeded else "error", description_usage.get("value"))
                                if not page_description.strip():
                                    raise ValueError("识图模型未返回页面描述")
                                name = f"{timestamp()}_round_{round_number:02d}.page_description.md"
                                (self.store.root / task_id / name).write_text(page_description, encoding="utf-8")
                            finally:
                                temp_description_image.unlink(missing_ok=True)
                    self.store.set_state(task_id, "running", round_number, f"第 {round_number} 轮：审核题意、解答与图示", stage="reviewing")
                    # The screenshot is temporarily saved so the common image API can read it.
                    temp_path = self.store.root / task_id / ".review_screenshot.png"
                    try:
                        temp_path.write_bytes(screenshot)
                        review_prompt = config.get("reviewer_prompt") or get_profile("general", reviewer_mode=reviewer_mode).reviewer_prompt
                        if reviewer_mode == "ocr_text":
                            review_prompt += f"\n\n原题 OCR 与图形关系：\n{question_ocr}\n\n页面截图视觉描述：\n{page_description}"
                        review_prompt += "\n\nHTML 源码：\n" + html
                        started = time.monotonic()
                        review_usage = {}
                        reviewed = False
                        try:
                            raw_review = await self.client.complete(
                                reviewer_model, review_prompt,
                                [] if reviewer_mode == "ocr_text" else original + [(temp_path, "image/png")],
                                max_tokens=self.settings.reviewer_max_tokens,
                                progress=lambda message: self.store.log_event(task_id, "reviewing", round_number, message),
                                usage=lambda value: review_usage.update(value=value),
                                **({"provider": reviewer_provider} if reviewer_provider else {}))
                            reviewed = True
                        finally:
                            elapsed = time.monotonic() - started
                            self.store.add_timing(task_id, "review_seconds", elapsed)
                            self.store.record_api_call(task_id, round_number, "reviewer", reviewer_provider,
                                                       reviewer_model, elapsed, "completed" if reviewed else "error",
                                                       review_usage.get("value"))
                        review = parse_review(raw_review)
                        review["source"] = "model"
                    finally:
                        temp_path.unlink(missing_ok=True)
                saved = self.store.save_round(task_id, round_number, html, screenshot, review)
                pending_path.unlink(missing_ok=True)
                self.store.log_event(task_id, "reviewing", round_number,
                                     f"第 {round_number} 轮文件已保存：{saved['html_name']}；{review['summary']}")
                if review["passed"] and not fixed_five:
                    final_name = self.store.save_final(task_id, html)
                    self.store.set_state(task_id, "passed", round_number, f"审核通过，最终文件：{final_name}", final_name, stage="passed")
                    self.store.refresh_round_metrics(task_id, round_number)
                    return
                feedback = ("上一轮已通过。请在保证题意、计算和交互正确的前提下，继续优化图示、教学顺序与手机可读性。"
                            if review["passed"] and fixed_five else "\n".join(f"- {issue}" for issue in review["issues"]))
                self.store.set_state(task_id, "running", round_number,
                                     f"第 {round_number} 轮未通过：{'; '.join(review['issues'])[:300]}；准备返修", stage="revising")
                self.store.refresh_round_metrics(task_id, round_number)
            if fixed_five:
                all_rounds = self.store.get(task_id)["rounds"]
                chosen = best_round(all_rounds)
                if any(item["passed"] for item in all_rounds) and chosen:
                    html = (self.store.root / task_id / chosen["html_name"]).read_text(encoding="utf-8")
                    final_name = self.store.save_final(task_id, html)
                    self.store.set_state(task_id, "passed", round_number,
                                         f"固定 {max_rounds} 轮完成；最终文件取评分最高的第 {chosen['number']} 轮", final_name, stage="passed")
                else:
                    self.store.set_state(task_id, "failed", round_number,
                                         f"固定 {max_rounds} 轮完成；可查看评分最高的草稿和各轮审核意见", stage="failed")
            else:
                self.store.set_state(task_id, "failed", round_number,
                                     f"达到 {max_rounds} 轮上限；可查看评分最高的草稿和各轮审核意见", stage="failed")
        except Exception as exc:
            self.store.set_state(task_id, "error", round_number, str(exc)[:500], stage="error")
