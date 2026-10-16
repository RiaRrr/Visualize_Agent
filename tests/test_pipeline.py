import asyncio
import json
import sqlite3
from pathlib import Path

from app.config import Settings
from app.model_api import ModelError, message_text, response_text
from app.pipeline import Pipeline, generator_prompt, validate_html
from app.storage import Store


GOOD_HTML = '<!doctype html><html><head><meta charset="utf-8"></head><body><h1>解答</h1><svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/></svg></body></html>'


class FakeClient:
    def __init__(self):
        self.calls = []

    async def complete(self, model, prompt, images, *, max_tokens, progress=None):
        self.calls.append((model, prompt, len(images)))
        if progress:
            progress("测试模型请求")
        if model == "generator":
            return GOOD_HTML
        if sum(call[0] == "reviewer" for call in self.calls) == 1:
            return json.dumps({"passed": False, "summary": "结论缺失", "issues": ["补充最终结论"]})
        return json.dumps({"passed": True, "summary": "通过", "issues": []})


def test_revision_and_artifact_history(tmp_path: Path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "generator", "reviewer")
    store = Store(settings)
    task = store.create("题目.png", "image/png", ".png", b"fake")
    client = FakeClient()

    async def fake_render(html):
        return b"PNG"

    asyncio.run(Pipeline(settings, store, client, fake_render).run(task["id"]))
    saved = store.get(task["id"])
    assert saved["status"] == "passed", saved["message"]
    assert len(saved["rounds"]) == 2
    assert saved["rounds"][0]["html_name"].endswith("_round_01.html")
    assert saved["rounds"][1]["html_name"].endswith("_round_02.html")
    assert "补充最终结论" in client.calls[2][1]
    for call in (client.calls[0], client.calls[2]):
        assert all(skill in call[1] for skill in
                   ("interactive-whiteboard", "math-teacher", "html-report", "visualize-data", "math-skills"))
        assert "不要用少量 `L` 直线" in call[1]
    assert saved["final_name"] and store.artifact(task["id"], saved["final_name"])
    assert store.artifact(task["id"], "../../apikeys.md") is None
    assert saved["stage"] == "passed"
    stages = [event["stage"] for event in saved["events"]]
    assert stages[0] == "queued" and stages[-1] == "passed"
    assert stages.count("generating") >= 2 and stages.count("reviewing") >= 2
    assert "revising" in stages
    assert any("补充最终结论" in event["message"] for event in saved["events"])


def test_offline_validator_allows_inline_svg_and_blocks_external_resources():
    assert validate_html(GOOD_HTML) == []
    assert validate_html(GOOD_HTML.replace("</body>", '<script src="https://example.com/x.js"></script></body>'))
    assert validate_html(GOOD_HTML.replace("解答", "🍎"))


def test_generator_prompt_keeps_project_rules_ahead_of_skill_guidance():
    prompt = generator_prompt("曲线有折角", GOOD_HTML)
    assert prompt.index("可离线打开") < prompt.index("interactive-whiteboard")
    assert "不得引用 CDN" in prompt
    assert "x0*x1/8" in prompt
    assert "曲线有折角" in prompt and GOOD_HTML in prompt


def test_existing_history_gets_log_summary(tmp_path: Path):
    db_path = tmp_path / "history.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE tasks (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                   image_name TEXT NOT NULL, image_mime TEXT NOT NULL, original_name TEXT NOT NULL,
                   current_round INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', final_name TEXT)""")
        db.execute("""CREATE TABLE rounds (task_id TEXT NOT NULL, number INTEGER NOT NULL, created_at TEXT NOT NULL,
                   html_name TEXT NOT NULL, screenshot_name TEXT, review_name TEXT NOT NULL, passed INTEGER NOT NULL,
                   summary TEXT NOT NULL, issues_json TEXT NOT NULL, PRIMARY KEY (task_id, number))""")
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                   ("old", "2026-10-14T10:00:00+08:00", "passed", "old.png", "image/png", "旧题.png", 1, "审核通过", "final.html"))
        db.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?,?)",
                   ("old", 1, "2026-10-14T10:02:00+08:00", "round.html", None, "review.json", 1, "答案正确", "[]"))
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "generator", "reviewer")
    task = Store(settings).get("old")
    assert task["stage"] == "passed"
    assert len(task["events"]) == 3
    assert "答案正确" in task["events"][1]["message"]


def test_interrupted_task_continues_from_next_round(tmp_path: Path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "generator", "reviewer")
    store = Store(settings)
    task = store.create("题目.png", "image/png", ".png", b"fake")
    store.save_round(task["id"], 1, GOOD_HTML, None,
                     {"passed": False, "summary": "图示不清楚", "issues": ["重画三角形"]})
    store.set_state(task["id"], "interrupted", 1, "测试中断", stage="interrupted")
    client = FakeClient()

    async def fake_render(html):
        return b"PNG"

    asyncio.run(Pipeline(settings, store, client, fake_render).run(task["id"]))
    saved = store.get(task["id"])
    assert saved["status"] == "passed"
    assert [round_info["number"] for round_info in saved["rounds"]] == [1, 2, 3]
    assert "重画三角形" in client.calls[0][1]


def test_store_read_does_not_interrupt_running_task(tmp_path: Path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "generator", "reviewer")
    store = Store(settings)
    task = store.create("题目.png", "image/png", ".png", b"fake")
    store.set_state(task["id"], "running", 1, "正在生成", stage="generating")
    assert Store(settings).get(task["id"])["status"] == "running"
    store.mark_interrupted()
    result = store.get(task["id"])
    assert result["status"] == "interrupted"
    assert result["events"][-1]["stage"] == "interrupted"


def test_output_limit_is_reported_as_truncation():
    import pytest

    with pytest.raises(ModelError, match="输出达到 max_tokens"):
        message_text({"stop_reason": "max_tokens", "content": [{"type": "text", "text": "<html>"}]})
    with pytest.raises(ModelError, match="输出达到 max_output_tokens"):
        response_text({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})
