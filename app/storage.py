"""SQLite index and timestamped local artifacts."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .rubric import empty_scores, normalize_overall_score, normalize_scores

TIMEZONE = ZoneInfo("Asia/Shanghai")
def empty_human_review() -> dict:
    return {"scores": empty_scores(), "overall_score": None,
            "comment": "", "updated_at": None}


def normalize_human_review(value: object) -> dict:
    review = value if isinstance(value, dict) else {}
    score = review.get("overall_score")
    if type(score) is not int or not 0 <= score <= 5:
        legacy = review.get("passed")
        score = 3 if legacy is True else 0 if legacy is False else None
    return {**empty_human_review(), **review, "overall_score": score}


def write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def timestamp() -> str:
    return datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S_%f")[:-3]


def best_round(rounds: list[dict]) -> dict | None:
    """Prefer scored rounds; resolve ties in favor of the later revision."""
    if not rounds:
        return None
    return max(rounds, key=lambda item: (
        item.get("model_review", {}).get("overall_score") is not None,
        item.get("model_review", {}).get("overall_score") or 0,
        item["number"],
    ))


class Store:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "history.sqlite3"
        self._annotation_lock = threading.RLock()
        self._init_db()

    def _db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                image_name TEXT NOT NULL, image_mime TEXT NOT NULL, original_name TEXT NOT NULL,
                current_round INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                final_name TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS rounds (
                task_id TEXT NOT NULL, number INTEGER NOT NULL, created_at TEXT NOT NULL,
                html_name TEXT NOT NULL, screenshot_name TEXT,
                review_name TEXT NOT NULL, passed INTEGER NOT NULL,
                summary TEXT NOT NULL, issues_json TEXT NOT NULL,
                PRIMARY KEY (task_id, number))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if "stage" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'")
                db.execute("UPDATE tasks SET stage=status")
            extra = {
                "batch_id": "TEXT", "generator_provider": "TEXT", "generator_model": "TEXT",
                "reviewer_provider": "TEXT", "reviewer_model": "TEXT", "profile_id": "TEXT",
                "profile_name": "TEXT", "profile_version": "TEXT", "subject": "TEXT",
                "run_mode": "TEXT", "max_rounds": "INTEGER", "config_name": "TEXT",
                "generator_mode": "TEXT NOT NULL DEFAULT 'vision'", "reviewer_mode": "TEXT NOT NULL DEFAULT 'vision'",
                "ocr_provider": "TEXT", "ocr_model": "TEXT",
                "generation_strategy": "TEXT NOT NULL DEFAULT 'direct'",
                "route_subject": "TEXT", "router_provider": "TEXT", "router_model": "TEXT",
                "generation_seconds": "REAL NOT NULL DEFAULT 0", "review_seconds": "REAL NOT NULL DEFAULT 0",
            }
            for name, definition in extra.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_batch_id ON tasks(batch_id)")
            round_columns = {row[1] for row in db.execute("PRAGMA table_info(rounds)")}
            if "metadata_name" not in round_columns:
                db.execute("ALTER TABLE rounds ADD COLUMN metadata_name TEXT")
            if "review_source" not in round_columns:
                db.execute("ALTER TABLE rounds ADD COLUMN review_source TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                created_at TEXT NOT NULL, stage TEXT NOT NULL,
                round_number INTEGER NOT NULL, message TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS events_task_id ON events(task_id, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS stage_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                round_number INTEGER NOT NULL, stage TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, duration_seconds REAL,
                status TEXT NOT NULL DEFAULT 'running')""")
            db.execute("CREATE INDEX IF NOT EXISTS stage_runs_task_id ON stage_runs(task_id, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                batch_id TEXT, round_number INTEGER NOT NULL, role TEXT NOT NULL,
                provider TEXT, model TEXT NOT NULL, created_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL, status TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, cached_input_tokens INTEGER,
                cache_write_input_tokens INTEGER,
                reasoning_output_tokens INTEGER, total_tokens INTEGER)""")
            db.execute("CREATE INDEX IF NOT EXISTS api_calls_task_id ON api_calls(task_id, id)")
            api_columns = {row[1] for row in db.execute("PRAGMA table_info(api_calls)")}
            if "cache_write_input_tokens" not in api_columns:
                db.execute("ALTER TABLE api_calls ADD COLUMN cache_write_input_tokens INTEGER")
            # Existing history predates the event log. Preserve a useful summary.
            old_tasks = db.execute("""SELECT id, created_at, status, current_round, message FROM tasks
                                      WHERE NOT EXISTS (SELECT 1 FROM events WHERE events.task_id = tasks.id)""").fetchall()
            for task in old_tasks:
                db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                           (task["id"], task["created_at"], task["status"], 0, "历史任务：上传与处理中间日志未记录"))
                for round_info in db.execute("SELECT number, created_at, passed, summary FROM rounds WHERE task_id=? ORDER BY number", (task["id"],)):
                    db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                               (task["id"], round_info["created_at"], "passed" if round_info["passed"] else "revising",
                                round_info["number"], f"第 {round_info['number']} 轮：{round_info['summary']}"))
                db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                           (task["id"], datetime.now(TIMEZONE).isoformat(), task["status"], task["current_round"], task["message"]))
            self._backfill_metadata(db)

    def _round_metadata(self, task: dict, round_info: dict) -> dict:
        html_path = self.root / task["id"] / round_info["html_name"]
        html_hash = hashlib.sha256(html_path.read_bytes()).hexdigest() if html_path.is_file() else None
        review_path = self.root / task["id"] / round_info["review_name"]
        try:
            review_file = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else {}
        except (OSError, ValueError):
            review_file = {}
        if not isinstance(review_file, dict):
            review_file = {}
        metadata = {
            "schema_version": 2, "rubric_version": "2.0",
            "record_id": f"{task['id']}:{round_info['number']}",
            "task_id": task["id"], "batch_id": task["batch_id"],
            "round": round_info["number"], "created_at": round_info["created_at"],
            "files": {"image": task["image_name"], "html": round_info["html_name"],
                      "screenshot": round_info["screenshot_name"], "model_review": round_info["review_name"],
                      "config": task["config_name"]},
            "html_sha256": html_hash,
            "generator": {"provider": task["generator_provider"], "model": task["generator_model"]},
            "reviewer": {"provider": task["reviewer_provider"], "model": task["reviewer_model"]},
            "profile": {"id": task["profile_id"], "name": task["profile_name"],
                        "version": task["profile_version"], "subject": task["subject"]},
            "routing": {"subject": task.get("route_subject"), "provider": task.get("router_provider"),
                        "model": task.get("router_model")},
            "run_mode": task["run_mode"], "max_rounds": task["max_rounds"],
            "generation_strategy": task.get("generation_strategy") or "direct",
            "model_review": {"source": round_info.get("review_source"),
                             "passed": bool(round_info["passed"]), "summary": round_info["summary"],
                             "issues": json.loads(round_info["issues_json"]),
                             "scores": normalize_scores(review_file.get("scores")),
                             "overall_score": normalize_overall_score(review_file.get("overall_score"))},
            "human_review": empty_human_review(),
            "metrics": {"stages": [item for item in task.get("stage_runs", []) if item["round_number"] == round_info["number"]],
                        "api_calls": [item for item in task.get("api_calls", []) if item["round_number"] == round_info["number"]]},
        }
        raw_review_name = f"round_{round_info['number']:02d}.model_review_raw.txt"
        if (self.root / task["id"] / raw_review_name).is_file():
            metadata["files"]["model_review_raw"] = raw_review_name
        if task.get("ocr_model"):
            metadata["input_pipeline"] = {"generator_mode": task.get("generator_mode") or "vision",
                                          "reviewer_mode": task.get("reviewer_mode") or "vision",
                                          "ocr_provider": task.get("ocr_provider"), "ocr_model": task.get("ocr_model")}
            metadata["files"].update({"question_ocr": self.question_ocr_name(task["id"]),
                                      "page_description": self.page_description_name(task["id"], round_info["number"])})
        if task.get("generation_strategy") == "guided_math":
            plan_path = self.root / task["id"] / "math_plan.accepted.json"
            metadata["files"]["math_plan"] = plan_path.name if plan_path.is_file() else None
            if round_info["number"] == 1:
                metadata["metrics"]["planning_api_calls"] = [item for item in task.get("api_calls", [])
                                                              if item["role"] in ("math_plan", "math_plan_review")]
                metadata["metrics"]["planning_stages"] = [item for item in task.get("stage_runs", [])
                                                           if item["stage"] in ("math_planning", "math_plan_review")]
        return metadata

    def _backfill_metadata(self, db: sqlite3.Connection) -> None:
        rows = db.execute("""SELECT rounds.*, tasks.* FROM rounds JOIN tasks ON tasks.id=rounds.task_id
                             ORDER BY rounds.task_id, rounds.number""").fetchall()
        for row in rows:
            record = dict(row)
            task_id, html_name = record["task_id"], record["html_name"]
            metadata_name = record["metadata_name"] or f"{Path(html_name).stem}.meta.json"
            path = self.root / task_id / metadata_name
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(path, self._round_metadata(record, record))
            else:
                metadata = json.loads(path.read_text(encoding="utf-8"))
                review = metadata.get("human_review")
                normalized = normalize_human_review(review)
                if review != normalized:
                    metadata["human_review"] = normalized
                    write_json_atomic(path, metadata)
            if record["metadata_name"] != metadata_name:
                db.execute("UPDATE rounds SET metadata_name=? WHERE task_id=? AND number=?",
                           (metadata_name, task_id, record["number"]))

    def mark_interrupted(self) -> None:
        """Call once when the server starts, never on ordinary history reads."""
        with self._db() as db:
            interrupted = db.execute("SELECT id, current_round FROM tasks WHERE status IN ('queued','running')").fetchall()
            for task in interrupted:
                db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                           (task["id"], datetime.now(TIMEZONE).isoformat(), "interrupted", task["current_round"], "服务重启中断了任务"))
            db.execute("UPDATE tasks SET status='interrupted', stage='interrupted', message='服务重启中断了任务' WHERE status IN ('queued','running')")
            db.execute("UPDATE stage_runs SET status='interrupted' WHERE ended_at IS NULL")

    def create(self, original_name: str, mime: str, suffix: str, content: bytes,
               run_config: dict | None = None, batch_id: str | None = None) -> dict:
        stamp = timestamp()
        task_id = f"{stamp}_{uuid.uuid4().hex[:8]}"
        folder = self.root / task_id
        folder.mkdir()
        image_name = f"{stamp}_input{suffix}"
        (folder / image_name).write_bytes(content)
        config_name = f"{stamp}_config.json" if run_config else None
        if run_config:
            (folder / config_name).write_text(json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._db() as db:
            config = run_config or {}
            db.execute("""INSERT INTO tasks (id, created_at, status, image_name, image_mime, original_name,
                       batch_id, generator_provider, generator_model, reviewer_provider, reviewer_model,
                       profile_id, profile_name, profile_version, subject, run_mode, max_rounds, config_name,
                       router_provider, router_model, generator_mode, reviewer_mode, ocr_provider, ocr_model,
                       generation_strategy)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (task_id, datetime.now(TIMEZONE).isoformat(), "queued", image_name, mime, original_name,
                        batch_id, config.get("generator_provider"), config.get("generator_model"),
                        config.get("reviewer_provider"), config.get("reviewer_model"), config.get("profile_id"),
                        config.get("profile_name"), config.get("profile_version"), config.get("subject"),
                        config.get("run_mode"), config.get("max_rounds"), config_name,
                        config.get("router_provider"), config.get("router_model"),
                        config.get("generator_mode", "vision"), config.get("reviewer_mode", "vision"),
                        config.get("ocr_provider"), config.get("ocr_model"), config.get("generation_strategy", "direct")))
            db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                       (task_id, datetime.now(TIMEZONE).isoformat(), "queued", 0, "题目图片已保存，等待开始生成"))
            db.execute("INSERT INTO stage_runs (task_id, round_number, stage, started_at) VALUES (?,?,?,?)",
                       (task_id, 0, "queued", datetime.now(TIMEZONE).isoformat()))
        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            rounds = db.execute("SELECT * FROM rounds WHERE task_id=? ORDER BY number", (task_id,)).fetchall()
            events = db.execute("SELECT id, created_at, stage, round_number, message FROM events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
            stages = db.execute("SELECT * FROM stage_runs WHERE task_id=? ORDER BY started_at, id", (task_id,)).fetchall()
            calls = db.execute("SELECT * FROM api_calls WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        task = dict(row)
        task["question_ocr_name"] = self.question_ocr_name(task_id)
        task["math_plan_name"] = "math_plan.accepted.json" if (self.root / task_id / "math_plan.accepted.json").is_file() else None
        task["rounds"] = [{**dict(r), "issues": json.loads(r["issues_json"])} for r in rounds]
        for r in task["rounds"]:
            r["page_description_name"] = self.page_description_name(task_id, r["number"])
            r.pop("issues_json")
            r["passed"] = bool(r["passed"])
            metadata_name = r.get("metadata_name")
            metadata_path = self.root / task_id / metadata_name if metadata_name else None
            metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path and metadata_path.is_file() else {}
            r["human_review"] = normalize_human_review(metadata.get("human_review"))
            r["model_review"] = metadata.get("model_review", {})
            r["model_review"]["scores"] = normalize_scores(r["model_review"].get("scores"))
            r["model_review"]["overall_score"] = normalize_overall_score(r["model_review"].get("overall_score"))
        task["events"] = [dict(event) for event in events]
        task["stage_runs"] = [dict(stage) for stage in stages]
        task["api_calls"] = [dict(call) for call in calls]
        for item in task["rounds"]:
            item["metrics"] = {"stages": [stage for stage in task["stage_runs"] if stage["round_number"] == item["number"]],
                               "api_calls": [call for call in task["api_calls"] if call["round_number"] == item["number"]]}
            if task.get("generation_strategy") == "guided_math" and item["number"] == 1:
                item["metrics"]["planning_stages"] = [stage for stage in task["stage_runs"]
                                                      if stage["stage"] in ("math_planning", "math_plan_review")]
                item["metrics"]["planning_api_calls"] = [call for call in task["api_calls"]
                                                         if call["role"] in ("math_plan", "math_plan_review")]
        chosen = best_round(task["rounds"])
        task["best_round_number"] = chosen["number"] if chosen else None
        task["best_round_score"] = chosen["model_review"]["overall_score"] if chosen else None
        return task

    def list(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("""SELECT id, created_at, status, stage, original_name, current_round, message,
                               batch_id, generator_provider, generator_model, reviewer_provider, reviewer_model,
                               profile_id, profile_name, profile_version, run_mode, max_rounds,
                               generator_mode, reviewer_mode, ocr_provider, ocr_model, generation_strategy,
                               route_subject, router_provider, router_model,
                               generation_seconds, review_seconds FROM tasks ORDER BY created_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def batch(self, batch_id: str) -> list[dict]:
        return sorted((task for task in self.list() if task["batch_id"] == batch_id), key=lambda task: task["created_at"])

    def config(self, task_id: str) -> dict | None:
        task = self.get(task_id)
        if not task or not task["config_name"]:
            return None
        return json.loads((self.root / task_id / task["config_name"]).read_text(encoding="utf-8"))

    def set_route(self, task_id: str, subject: str, profile) -> None:
        task = self.get(task_id)
        if not task or task["profile_id"] != "auto" or not task["config_name"]:
            raise ValueError("任务不支持自动分流")
        config_path = self.root / task_id / task["config_name"]
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update({"route_subject": subject, "subject": subject, "profile_name": profile.name,
                       "profile_version": profile.version, "generator_prompt": profile.generator_prompt,
                       "reviewer_prompt": profile.reviewer_prompt})
        write_json_atomic(config_path, config)
        with self._db() as db:
            db.execute("""UPDATE tasks SET route_subject=?, subject=?, profile_name=?, profile_version=?
                          WHERE id=?""", (subject, subject, profile.name, profile.version, task_id))
            db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                       (task_id, datetime.now(TIMEZONE).isoformat(), "routing", 0,
                        f"题目已识别为 {profile.name}；将使用对应学科经验与审核规则"))

    def add_timing(self, task_id: str, column: str, seconds: float) -> None:
        if column not in ("generation_seconds", "review_seconds"):
            raise ValueError("未知计时类型")
        with self._db() as db:
            db.execute(f"UPDATE tasks SET {column}={column}+? WHERE id=?", (seconds, task_id))

    def set_state(self, task_id: str, status: str, round_number: int, message: str,
                  final_name: str | None = None, stage: str | None = None) -> None:
        current_stage = stage or status
        now = datetime.now(TIMEZONE)
        with self._db() as db:
            active = db.execute("SELECT * FROM stage_runs WHERE task_id=? AND ended_at IS NULL AND status='running' ORDER BY id DESC LIMIT 1",
                                (task_id,)).fetchone()
            if active and (active["stage"] != current_stage or active["round_number"] != round_number):
                elapsed = max(0.0, (now - datetime.fromisoformat(active["started_at"])).total_seconds())
                close_status = "error" if current_stage == "error" else "interrupted" if current_stage == "interrupted" else "completed"
                db.execute("UPDATE stage_runs SET ended_at=?, duration_seconds=?, status=? WHERE id=?",
                           (now.isoformat(), elapsed, close_status, active["id"]))
            if current_stage in {"queued", "routing", "question_ocr", "generating", "validating", "rendering", "page_description", "reviewing"} and (
                not active or active["stage"] != current_stage or active["round_number"] != round_number
            ):
                db.execute("INSERT INTO stage_runs (task_id, round_number, stage, started_at) VALUES (?,?,?,?)",
                           (task_id, round_number, current_stage, now.isoformat()))
            db.execute("UPDATE tasks SET status=?, stage=?, current_round=?, message=?, final_name=COALESCE(?,final_name) WHERE id=?",
                       (status, current_stage, round_number, message, final_name, task_id))
            db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                       (task_id, now.isoformat(), current_stage, round_number, message))

    def record_api_call(self, task_id: str, round_number: int, role: str, provider: str | None,
                        model: str, duration_seconds: float, status: str, usage: dict | None) -> None:
        usage = usage or {}
        with self._db() as db:
            batch = db.execute("SELECT batch_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            db.execute("""INSERT INTO api_calls (task_id, batch_id, round_number, role, provider, model,
                       created_at, duration_seconds, status, input_tokens, output_tokens,
                       cached_input_tokens, cache_write_input_tokens, reasoning_output_tokens, total_tokens)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (task_id, batch["batch_id"] if batch else None, round_number, role, provider, model,
                        datetime.now(TIMEZONE).isoformat(), duration_seconds, status,
                        usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cached_input_tokens"),
                        usage.get("cache_write_input_tokens"), usage.get("reasoning_output_tokens"), usage.get("total_tokens")))

    def record_upload(self, task_id: str, started: datetime, duration_seconds: float) -> None:
        ended = started + timedelta(seconds=duration_seconds)
        with self._db() as db:
            db.execute("""INSERT INTO stage_runs (task_id, round_number, stage, started_at,
                       ended_at, duration_seconds, status) VALUES (?,?,?,?,?,?,?)""",
                       (task_id, 0, "upload", started.isoformat(), ended.isoformat(), duration_seconds, "completed"))

    def refresh_round_metrics(self, task_id: str, number: int) -> None:
        task = self.get(task_id)
        item = next((row for row in task["rounds"] if row["number"] == number), None) if task else None
        if not item or not item.get("metadata_name"):
            return
        path = self.root / task_id / item["metadata_name"]
        with self._annotation_lock:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            metadata["metrics"] = {**metadata.get("metrics", {}), **item["metrics"]}
            write_json_atomic(path, metadata)

    def log_event(self, task_id: str, stage: str, round_number: int, message: str) -> None:
        with self._db() as db:
            db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                       (task_id, datetime.now(TIMEZONE).isoformat(), stage, round_number, message))

    def save_round(self, task_id: str, number: int, html: str, screenshot: bytes | None, review: dict) -> dict:
        stamp = timestamp()
        prefix = f"{stamp}_round_{number:02d}"
        folder = self.root / task_id
        html_name = f"{prefix}.html"
        screenshot_name = f"{prefix}.png" if screenshot else None
        review_name = f"{prefix}.review.json"
        metadata_name = f"{prefix}.meta.json"
        (folder / html_name).write_text(html, encoding="utf-8")
        if screenshot:
            (folder / screenshot_name).write_bytes(screenshot)
        (folder / review_name).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        created_at = datetime.now(TIMEZONE).isoformat()
        with self._db() as db:
            db.execute("""INSERT INTO rounds (task_id, number, created_at, html_name, screenshot_name,
                       review_name, passed, summary, issues_json, metadata_name, review_source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (task_id, number, created_at, html_name, screenshot_name, review_name,
                        int(review["passed"]), review["summary"], json.dumps(review["issues"], ensure_ascii=False),
                        metadata_name, review.get("source")))
        task = self.get(task_id)
        with self._db() as db:
            row = dict(db.execute("SELECT * FROM rounds WHERE task_id=? AND number=?", (task_id, number)).fetchone())
        write_json_atomic(folder / metadata_name, self._round_metadata(task, row))
        return {"html_name": html_name, "screenshot_name": screenshot_name,
                "review_name": review_name, "metadata_name": metadata_name}

    def update_annotation(self, task_id: str, number: int, annotation: dict) -> dict:
        with self._annotation_lock:
            task = self.get(task_id)
            round_info = next((item for item in task["rounds"] if item["number"] == number), None) if task else None
            if not round_info or not round_info["metadata_name"]:
                raise LookupError("轮次不存在")
            path = self.root / task_id / round_info["metadata_name"]
            metadata = json.loads(path.read_text(encoding="utf-8"))
            metadata["human_review"] = {"scores": annotation["scores"], "overall_score": annotation["overall_score"],
                                        "comment": annotation["comment"],
                                        "updated_at": datetime.now(TIMEZONE).isoformat()}
            write_json_atomic(path, metadata)
            return metadata

    def save_final(self, task_id: str, html: str) -> str:
        name = f"{timestamp()}_final.html"
        (self.root / task_id / name).write_text(html, encoding="utf-8")
        return name

    def question_ocr_name(self, task_id: str) -> str | None:
        matches = sorted((self.root / task_id).glob("*_question_ocr.md"))
        return matches[0].name if matches else None

    def page_description_name(self, task_id: str, number: int) -> str | None:
        matches = sorted((self.root / task_id).glob(f"*_round_{number:02d}.page_description.md"))
        return matches[0].name if matches else None

    def artifact(self, task_id: str, filename: str) -> Path | None:
        task = self.get(task_id)
        if not task:
            return None
        allowed = {task["image_name"], task["final_name"], task["config_name"]}
        allowed.update((self.question_ocr_name(task_id),))
        if task.get("generation_strategy") == "guided_math":
            allowed.update(path.name for path in (self.root / task_id).glob("math_plan*.json"))
            allowed.update(path.name for path in (self.root / task_id).glob("math_plan*.raw.txt"))
        for round_info in task["rounds"]:
            allowed.update((round_info["html_name"], round_info["screenshot_name"],
                            round_info["review_name"], round_info["metadata_name"],
                            f"round_{round_info['number']:02d}.model_review_raw.txt",
                            self.page_description_name(task_id, round_info["number"])))
        if filename not in allowed:
            return None
        path = self.root / task_id / filename
        return path if path.is_file() else None
