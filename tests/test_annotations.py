import hashlib
import importlib
import json
import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.storage import Store


def _settings(tmp_path):
    return Settings(tmp_path, "https://example.invalid/v1", "fake", "generator", "reviewer")


def test_round_metadata_and_annotation_survive_store_reopen(tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings)
    config = {"generator_provider": "openai", "generator_model": "gpt-6-sol",
              "reviewer_provider": "codex", "reviewer_model": "gpt-5-codex",
              "profile_id": "math-current", "profile_name": "数学", "profile_version": "abc",
              "subject": "math", "run_mode": "single", "max_rounds": 1,
              "generator_prompt": "prompt", "reviewer_prompt": "review"}
    task = store.create("题图.png", "image/png", ".png", b"image", config, "batch-1")
    html = "<!doctype html><html><body>5</body></html>"
    saved = store.save_round(task["id"], 1, html, b"PNG", {"passed": False, "summary": "图示错误", "issues": ["重画"], "source": "model"})
    metadata_path = store.artifact(task["id"], saved["metadata_name"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["files"] == {"image": task["image_name"], "html": saved["html_name"],
                                  "screenshot": saved["screenshot_name"], "model_review": saved["review_name"],
                                  "config": task["config_name"]}
    assert metadata["generator"] == {"provider": "openai", "model": "gpt-6-sol"}
    assert metadata["model_review"]["issues"] == ["重画"]
    assert metadata["model_review"]["source"] == "model"
    assert all(value is None for value in metadata["model_review"]["scores"].values())
    assert metadata["schema_version"] == 2 and metadata["rubric_version"] == "2.0"
    assert metadata["html_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    assert metadata["human_review"]["passed"] is None
    annotation = {"scores": {"problem_fidelity": 5, "solution_correctness": 4,
                             "page_quality": None, "interaction_quality": 2,
                             "teaching_effectiveness": 5},
                  "passed": False, "comment": "点位错误"}
    updated = store.update_annotation(task["id"], 1, annotation)
    assert updated["human_review"]["scores"]["page_quality"] is None
    assert updated["human_review"]["passed"] is False
    assert updated["human_review"]["updated_at"]
    reopened = Store(settings)
    assert reopened.get(task["id"])["rounds"][0]["human_review"] == updated["human_review"]
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["human_review"] == updated["human_review"]
    assert reopened.artifact(task["id"], "../other.json") is None


def test_legacy_round_gets_metadata_without_guessing_model(tmp_path):
    with sqlite3.connect(tmp_path / "history.sqlite3") as db:
        db.execute("""CREATE TABLE tasks (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                   image_name TEXT NOT NULL, image_mime TEXT NOT NULL, original_name TEXT NOT NULL,
                   current_round INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', final_name TEXT)""")
        db.execute("""CREATE TABLE rounds (task_id TEXT NOT NULL, number INTEGER NOT NULL, created_at TEXT NOT NULL,
                   html_name TEXT NOT NULL, screenshot_name TEXT, review_name TEXT NOT NULL, passed INTEGER NOT NULL,
                   summary TEXT NOT NULL, issues_json TEXT NOT NULL, PRIMARY KEY (task_id, number))""")
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                   ("old", "2026-10-14T10:00:00+08:00", "passed", "old.png", "image/png", "old.png", 1, "通过", "final.html"))
        db.execute("INSERT INTO rounds VALUES (?,?,?,?,?,?,?,?,?)",
                   ("old", 1, "2026-10-14T10:02:00+08:00", "round.html", None, "review.json", 1, "通过", "[]"))
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / "round.html").write_text("<html></html>", encoding="utf-8")
    store = Store(_settings(tmp_path))
    task = store.get("old")
    metadata = json.loads(store.artifact("old", task["rounds"][0]["metadata_name"]).read_text(encoding="utf-8"))
    assert metadata["generator"] == {"provider": None, "model": None}
    assert metadata["model_review"]["source"] is None
    assert metadata["files"]["config"] is None
    assert metadata["files"]["html"] == "round.html"


def test_annotation_api_validates_and_can_clear_label(tmp_path, monkeypatch):
    main = importlib.import_module("app.main")
    store = Store(_settings(tmp_path))
    task = store.create("题.png", "image/png", ".png", b"image")
    store.save_round(task["id"], 1, "<!doctype html><html></html>", None,
                     {"passed": True, "summary": "通过", "issues": []})
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app)
    url = f"/api/tasks/{task['id']}/rounds/1/annotation"
    scores = {"problem_fidelity": 5, "solution_correctness": None,
              "page_quality": 3, "interaction_quality": 4, "teaching_effectiveness": 5}
    response = client.put(url, json={"scores": scores, "passed": True, "comment": "可用"})
    assert response.status_code == 200
    assert response.json()["human_review"]["passed"] is True
    assert client.get(f"/api/tasks/{task['id']}").json()["rounds"][0]["human_review"]["comment"] == "可用"
    assert client.put(url, json={"scores": {**scores, "problem_fidelity": 6}, "passed": True}).status_code == 422
    assert client.put(url, json={"scores": {**scores, "page_interaction": 4}, "passed": True}).status_code == 422
    assert client.put(url, json={"scores": scores, "passed": "yes"}).status_code == 422
    assert client.put(url.replace("/1/", "/9/"), json={"scores": scores}).status_code == 404
    cleared = client.put(url, json={"scores": {name: None for name in scores}, "passed": None, "comment": ""})
    assert cleared.status_code == 200
    assert cleared.json()["human_review"]["passed"] is None
