import asyncio
import json

from app.config import Settings
from app.pipeline import Pipeline
from app.profiles import get_profile
from app.storage import Store


HTML = '<!doctype html><html><body><svg><circle cx="8" cy="8" r="4"/></svg></body></html>'


class ModeClient:
    def __init__(self):
        self.calls = []

    async def complete(self, model, prompt, images, *, max_tokens, provider=None, progress=None, usage=None):
        self.calls.append((model, prompt, len(images)))
        if model == "ocr" and "题目识图员" in prompt:
            return "题目：求圆的面积。图中半径为 2。"
        if model == "ocr":
            return "页面可见圆、半径 2 和面积公式；没有裁切。"
        if model == "gen":
            return HTML
        return json.dumps({"passed": False, "summary": "需修改", "issues": ["补充面积变化图"],
                           "scores": {}, "overall_score": 2})


def test_text_modes_reuse_question_ocr_and_describe_each_round(tmp_path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "gen", "review")
    store = Store(settings)
    profile = get_profile("math-current", "ocr_text", "ocr_text")
    config = {"profile_id": profile.id, "profile_name": profile.name, "profile_version": profile.version,
              "subject": profile.subject, "generator_prompt": profile.generator_prompt,
              "reviewer_prompt": profile.reviewer_prompt, "generator_provider": "edumind_alt",
              "generator_model": "gen", "reviewer_provider": "edumind_alt", "reviewer_model": "review",
              "ocr_provider": "edumind_alt", "ocr_model": "ocr", "generator_mode": "ocr_text",
              "reviewer_mode": "ocr_text", "max_rounds": 2}
    task = store.create("题目.png", "image/png", ".png", b"image", config)
    client = ModeClient()
    asyncio.run(Pipeline(settings, store, client, lambda html: asyncio.sleep(0, result=b"PNG")).run(task["id"]))
    saved = store.get(task["id"])
    assert saved["status"] == "failed"
    assert [call[0] for call in client.calls] == ["ocr", "gen", "ocr", "review", "gen", "ocr", "review"]
    assert [call[2] for call in client.calls] == [1, 0, 1, 0, 0, 1, 0]
    assert all("题目：求圆的面积" in call[1] for call in client.calls if call[0] in {"gen", "review"})
    assert all("页面可见圆" in call[1] for call in client.calls if call[0] == "review")
    assert saved["question_ocr_name"] and store.artifact(task["id"], saved["question_ocr_name"])
    assert len(saved["rounds"]) == 2
    assert all(store.artifact(task["id"], row["page_description_name"]) for row in saved["rounds"])
    metadata = json.loads(store.artifact(task["id"], saved["rounds"][0]["metadata_name"]).read_text())
    assert metadata["input_pipeline"]["generator_mode"] == "ocr_text"
    assert metadata["files"]["question_ocr"] == saved["question_ocr_name"]


def test_mixed_mode_uses_image_only_for_selected_role(tmp_path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "gen", "review")
    store = Store(settings)
    profile = get_profile("general", "vision", "ocr_text")
    config = {"profile_id": profile.id, "profile_name": profile.name, "profile_version": profile.version,
              "subject": profile.subject, "generator_prompt": profile.generator_prompt,
              "reviewer_prompt": profile.reviewer_prompt, "generator_model": "gen", "reviewer_model": "review",
              "ocr_provider": "edumind_alt", "ocr_model": "ocr", "generator_mode": "vision",
              "reviewer_mode": "ocr_text", "max_rounds": 1}
    task = store.create("题目.png", "image/png", ".png", b"image", config)
    client = ModeClient()
    asyncio.run(Pipeline(settings, store, client, lambda html: asyncio.sleep(0, result=b"PNG")).run(task["id"]))
    assert [call[2] for call in client.calls] == [1, 1, 1, 0]
    assert "题目图片已随本次 API 消息提供" in client.calls[1][1]


def test_same_batch_reuses_matching_question_ocr(tmp_path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "gen", "review")
    store = Store(settings)
    profile = get_profile("general", "ocr_text", "vision")
    config = {"profile_id": profile.id, "profile_name": profile.name, "profile_version": profile.version,
              "subject": profile.subject, "generator_prompt": profile.generator_prompt,
              "reviewer_prompt": profile.reviewer_prompt, "generator_model": "gen", "reviewer_model": "review",
              "ocr_provider": "edumind_alt", "ocr_model": "ocr", "generator_mode": "ocr_text",
              "reviewer_mode": "vision", "max_rounds": 1}
    tasks = [store.create("题目.png", "image/png", ".png", b"same", config, "batch") for _ in range(2)]
    client = ModeClient()
    pipeline = Pipeline(settings, store, client, lambda html: asyncio.sleep(0, result=b"PNG"))
    for task in tasks:
        asyncio.run(pipeline.run(task["id"]))
    assert sum(model == "ocr" for model, _, _ in client.calls) == 1
    assert all(store.get(task["id"])["question_ocr_name"] for task in tasks)
