import asyncio
import importlib
import io
import json
import pytest
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.pipeline import Pipeline
from app.profiles import get_profile, get_routed_profile, list_profiles, runtime_defaults
from app.router import parse_subject
from app.storage import Store


HTML = '<!doctype html><html><body><h1>5</h1></body></html>'


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def complete(self, model, prompt, images, *, max_tokens, progress=None, provider=None, usage=None):
        self.calls.append((provider, model, prompt, len(images)))
        if len(images) == 1:
            return HTML
        return json.dumps({"passed": False, "summary": "需要改进", "issues": ["补充图示"]})


def test_profiles_have_distinct_snapshotted_prompts():
    profiles = list_profiles()
    assert len(profiles) == 10
    assert len({p["version"] for p in profiles}) == 9  # 自动模板和通用方案的内容相同
    for profile in profiles:
        assert profile["generator_prompt"].startswith("你是一名擅长多学科教学设计")
        assert profile["generator_prompt"].endswith("{{QUESTION}}")
        assert "可视化优先原则（最高优先级）" in profile["generator_prompt"]
        assert "JSON" in profile["reviewer_prompt"]
        assert "刚读完这道题" in profile["generator_prompt"]
        assert "长段文字" in profile["reviewer_prompt"]
        assert "teaching_effectiveness" in profile["reviewer_prompt"]
        assert get_profile(profile["id"]).version == profile["version"]
    assert "数学学科经验" in get_routed_profile("math").generator_prompt
    math_prompt = get_routed_profile("math").generator_prompt
    assert "组合箭头" in math_prompt and "<sub>" in math_prompt
    assert "缺字方框" in get_routed_profile("math").reviewer_prompt
    assert math_prompt.index("可视化优先原则") < math_prompt.index("数学学科经验") < math_prompt.index("技术与输出要求")
    assert "受力图" in get_routed_profile("physics").generator_prompt
    assert "粒子模型" in get_routed_profile("chemistry").generator_prompt
    assert get_routed_profile("general").generator_prompt == get_profile("general").generator_prompt
    assert "数学学科经验" not in get_routed_profile("general").generator_prompt
    assert parse_subject('{"subject":"general"}') == "general"
    defaults = runtime_defaults("math-deepseek", "deepseek-v4-1-flash-261017", "other-model")
    assert defaults["generator_thinking"] == "enabled" and defaults["generator_max_tokens"] == 64000
    assert "reviewer_thinking" not in defaults
    assert runtime_defaults("math-current", "deepseek-v4-1-flash-261017", "other-model") == {}


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


def test_deepseek_preset_passes_image_and_snapshotted_runtime_settings(tmp_path):
    settings=Settings(tmp_path,"https://example.invalid/v1",None,"legacy","legacy",deepseek_thinking="disabled")
    profile=get_profile("math-deepseek")
    model="deepseek-v4-1-flash-261017"
    config={"profile_id":profile.id,"generator_model":model,"reviewer_model":model,
            "generator_provider":"codex","reviewer_provider":"codex",
            "generator_prompt":profile.generator_prompt,"reviewer_prompt":profile.reviewer_prompt,
            "generator_mode":"vision","reviewer_mode":"vision","max_rounds":1,
            **runtime_defaults(profile.id,model,model)}
    calls=[]
    class Client:
        async def complete(self, model, prompt, images, **kwargs):
            calls.append((len(images),kwargs))
            return HTML if len(images)==1 else json.dumps({"passed":True,"issues":[],"summary":"完成"})
    store=Store(settings);task=store.create("test.png","image/png",".png",b"image",run_config=config)
    asyncio.run(Pipeline(settings,store,Client(),lambda html:asyncio.sleep(0,result=b"PNG")).run(task['id']))
    assert [c[0] for c in calls]==[1,2]
    assert all(c[1]['thinking']=='enabled' and c[1]['reasoning_effort']=='high' and c[1]['max_tokens']==64000 for c in calls)
    assert settings.deepseek_thinking=='disabled'
    assert store.get(task['id'])['question_ocr_name'] is None


def test_batch_runs_without_automatic_model_probe(monkeypatch):
    main = importlib.import_module("app.main")
    calls = []

    class BatchStore:
        def batch(self, batch_id):
            return [{"id": "first", "profile_id": "physics", "status": "queued", "current_round": 0},
                    {"id": "second", "profile_id": "chemistry", "status": "queued", "current_round": 0}]

        def get(self, task_id):
            return next(item for item in self.batch("batch") if item["id"] == task_id)

    async def run(task_id):
        calls.append(task_id)

    async def reject_probe(*args):
        raise AssertionError("批量生成不应自动调用短识图测试")

    monkeypatch.setattr(main, "store", BatchStore())
    monkeypatch.setattr(main.pipeline, "run", run)
    monkeypatch.setattr(main, "_probe_choice", reject_probe)
    asyncio.run(main._run_batch("batch"))
    assert calls == ["first", "second"]


def test_auto_batch_classifies_once_and_snapshots_subject_prompts(tmp_path, monkeypatch):
    main = importlib.import_module("app.main")
    store = Store(Settings(tmp_path, "https://example.invalid/v1", "fake", "gen", "review"))
    profile = get_profile("auto")
    config = {"profile_id": "auto", "profile_name": profile.name, "profile_version": profile.version,
              "subject": "auto", "generator_prompt": profile.generator_prompt,
              "reviewer_prompt": profile.reviewer_prompt, "generator_provider": "openai",
              "generator_model": "gen", "reviewer_provider": "openai", "reviewer_model": "review",
              "router_provider": "openai", "router_model": "route", "run_mode": "single", "max_rounds": 1}
    tasks = [store.create(f"题{i}.png", "image/png", ".png", b"image", config, "batch") for i in range(2)]
    classified, generated = [], []

    async def classify(client, provider, model, image, mime, prompt=None, usage=None):
        classified.append((provider, model, image.name, mime))
        if usage:
            usage({"input_tokens": 12, "output_tokens": 2, "total_tokens": 14})
        return "physics"

    async def run(task_id):
        generated.append(task_id)

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "classify_image", classify)
    monkeypatch.setattr(main.pipeline, "run", run)
    asyncio.run(main._run_batch("batch"))
    assert len(classified) == 1
    assert len(store.get(tasks[0]["id"])["api_calls"]) == 1
    assert len(store.get(tasks[1]["id"])["api_calls"]) == 0
    assert generated == [task["id"] for task in tasks]
    for task in tasks:
        saved = store.get(task["id"])
        assert saved["route_subject"] == "physics"
        assert saved["router_model"] == "route"
        assert "受力图" in store.config(task["id"])["generator_prompt"]
        assert "矢量方向" in store.config(task["id"])["reviewer_prompt"]
    round_file = store.save_round(tasks[0]["id"], 1, HTML, None,
                                  {"passed": False, "summary": "测试", "issues": ["测试"]})
    metadata = json.loads(Path(store.artifact(tasks[0]["id"], round_file["metadata_name"])).read_text())
    assert metadata["routing"] == {"subject": "physics", "provider": "openai", "model": "route"}


@pytest.mark.parametrize('profile_id', ['auto', 'math-deepseek-patch'])
def test_upload_accepts_router_model_and_saves_auto_template(tmp_path, monkeypatch, profile_id):
    main = importlib.import_module("app.main")
    store = Store(Settings(tmp_path, "https://example.invalid/v1", "fake", "gen", "review"))

    async def models(refresh=False):
        return {"models": [{"provider": "openai", "id": name, "candidate": True}
                           for name in ("gen", "review", "route")], "errors": {}}

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main.catalog, "list", models)
    monkeypatch.setattr(main, "_track", lambda coro: coro.close())
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(image, format="PNG")
    row = {"profile_id": profile_id, "generator_provider": "openai", "generator_model": "gen",
           "reviewer_provider": "openai", "reviewer_model": "review"}
    response = TestClient(main.app).post(
        "/api/batches", files={"image": ("question.png", image.getvalue(), "image/png")},
        data={"rows": json.dumps([row]), "run_mode": "single",
              "router_provider": "openai", "router_model": "route"})
    assert response.status_code == 201
    task = response.json()["tasks"][0]
    config = store.config(task["id"])
    if profile_id == 'auto':
        assert task["router_model"] == "route" and task["route_subject"] is None
        assert config["router_model"] == "route"
        assert "只返回一行 JSON" in config["router_prompt"]
    else:
        assert config['revision_strategy'] == 'patch'
        assert '局部代码修订' in config['revision_prompt']
        assert config['generator_mode'] == config['reviewer_mode'] == 'vision'
        assert config['profile_version'] == get_profile(profile_id).version
        assert 'router_model' not in config
    assert "可视化优先原则（最高优先级）" in config["generator_prompt"]


def test_upload_rejects_text_only_model_for_ocr(tmp_path, monkeypatch):
    main = importlib.import_module("app.main")
    store = Store(Settings(tmp_path, "https://example.invalid/v1", "fake", "gen", "review"))

    async def models(refresh=False):
        return {"models": [{"provider": "edumind_alt", "id": name, "candidate": True,
                            "capability": {"image_input": name != "text-only"}}
                           for name in ("image", "text-only")], "errors": {}}

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main.catalog, "list", models)
    monkeypatch.setattr(main, "_track", lambda coro: coro.close())
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(image, format="PNG")
    row = {"profile_id": "general", "generator_provider": "edumind_alt", "generator_model": "text-only",
           "reviewer_provider": "edumind_alt", "reviewer_model": "text-only",
           "generator_mode": "ocr_text", "reviewer_mode": "ocr_text",
           "ocr_provider": "edumind_alt", "ocr_model": "text-only"}
    response = TestClient(main.app).post(
        "/api/batches", files={"image": ("question.png", image.getvalue(), "image/png")},
        data={"rows": json.dumps([row]), "run_mode": "single"})
    assert response.status_code == 400
    assert "识图模型" in response.json()["detail"]
    row["ocr_model"] = "image"
    response = TestClient(main.app).post(
        "/api/batches", files={"image": ("question.png", image.getvalue(), "image/png")},
        data={"rows": json.dumps([row]), "run_mode": "single"})
    assert response.status_code == 201
    assert store.config(response.json()["tasks"][0]["id"])["generator_mode"] == "ocr_text"
