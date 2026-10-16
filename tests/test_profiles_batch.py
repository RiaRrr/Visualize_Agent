import asyncio
import json

from app.config import Settings
from app.pipeline import Pipeline
from app.profiles import get_profile, list_profiles
from app.storage import Store


HTML = '<!doctype html><html><body><h1>5</h1></body></html>'


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def complete(self, model, prompt, images, *, max_tokens, progress=None, provider=None):
        self.calls.append((provider, model, prompt, len(images)))
        if len(images) == 1:
            return HTML
        return json.dumps({"passed": False, "summary": "需要改进", "issues": ["补充图示"]})


def test_profiles_have_distinct_snapshotted_prompts():
    profiles = list_profiles()
    assert len(profiles) == 6
    assert len({p["version"] for p in profiles}) == 6
    for profile in profiles:
        if profile["id"] == "math-current":
            assert profile["generator_prompt"].startswith("你是一名擅长数学、物理、化学教学设计")
            assert profile["generator_prompt"].endswith("{{QUESTION}}")
            assert "方案指导：" not in profile["generator_prompt"]
        else:
            assert profile["generator_prompt"].startswith("你是一位严谨")
            assert "尽可能让学生通过可交互的图理解解答" in profile["generator_prompt"]
        assert "JSON" in profile["reviewer_prompt"]
        assert "刚读完这道题" in profile["generator_prompt"]
        assert "长段文字" in profile["reviewer_prompt"]
        assert "teaching_effectiveness" in profile["reviewer_prompt"]
        assert get_profile(profile["id"]).version == profile["version"]


def test_batch_task_uses_exact_profile_providers_and_single_round(tmp_path):
    settings = Settings(tmp_path, "https://example.invalid/v1", "fake", "legacy-gen", "legacy-review")
    store = Store(settings)
    profile = get_profile("physics")
    config = {"profile_id": profile.id, "profile_name": profile.name, "profile_version": profile.version,
              "subject": profile.subject, "generator_prompt": profile.generator_prompt,
              "reviewer_prompt": profile.reviewer_prompt, "generator_provider": "codex",
              "generator_model": "same-name", "reviewer_provider": "openai",
              "reviewer_model": "same-name", "run_mode": "single", "max_rounds": 1}
    task = store.create("题目.png", "image/png", ".png", b"test", run_config=config, batch_id="batch")
    client = RecordingClient()
    asyncio.run(Pipeline(settings, store, client, lambda html: asyncio.sleep(0, result=b"PNG")).run(task["id"]))
    result = store.get(task["id"])
    assert result["status"] == "failed" and len(result["rounds"]) == 1
    assert [call[:2] for call in client.calls] == [("codex", "same-name"), ("openai", "same-name")]
    assert "受力" in client.calls[0][2]
    assert "量纲" in client.calls[1][2]
    assert result["profile_version"] == profile.version
    assert store.batch("batch")[0]["id"] == task["id"]
    assert store.config(task["id"]) == config
    assert store.artifact(task["id"], result["config_name"]).is_file()
    assert result["generation_seconds"] >= 0 and result["review_seconds"] >= 0
