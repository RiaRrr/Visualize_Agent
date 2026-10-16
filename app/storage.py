"""SQLite index and timestamped local artifacts."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings

TIMEZONE = ZoneInfo("Asia/Shanghai")
SCORE_KEYS = ("problem_fidelity", "solution_correctness", "page_quality",
              "interaction_quality", "teaching_effectiveness")


def empty_human_review() -> dict:
    return {"scores": {key: None for key in SCORE_KEYS}, "passed": None,
            "comment": "", "updated_at": None}


def write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def timestamp() -> str:
    return datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S_%f")[:-3]


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
        return {
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
            "run_mode": task["run_mode"], "max_rounds": task["max_rounds"],
            "model_review": {"source": round_info.get("review_source"),
                             "passed": bool(round_info["passed"]), "summary": round_info["summary"],
                             "issues": json.loads(round_info["issues_json"])},
            "human_review": empty_human_review(),
        }

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
                       profile_id, profile_name, profile_version, subject, run_mode, max_rounds, config_name)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (task_id, datetime.now(TIMEZONE).isoformat(), "queued", image_name, mime, original_name,
                        batch_id, config.get("generator_provider"), config.get("generator_model"),
                        config.get("reviewer_provider"), config.get("reviewer_model"), config.get("profile_id"),
                        config.get("profile_name"), config.get("profile_version"), config.get("subject"),
                        config.get("run_mode"), config.get("max_rounds"), config_name))
            db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                       (task_id, datetime.now(TIMEZONE).isoformat(), "queued", 0, "题目图片已保存，等待开始生成"))
        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            rounds = db.execute("SELECT * FROM rounds WHERE task_id=? ORDER BY number", (task_id,)).fetchall()
            events = db.execute("SELECT id, created_at, stage, round_number, message FROM events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        task = dict(row)
        task["rounds"] = [{**dict(r), "issues": json.loads(r["issues_json"])} for r in rounds]
        for r in task["rounds"]:
            r.pop("issues_json")
            r["passed"] = bool(r["passed"])
            metadata_name = r.get("metadata_name")
            metadata_path = self.root / task_id / metadata_name if metadata_name else None
            r["human_review"] = json.loads(metadata_path.read_text(encoding="utf-8"))["human_review"] if metadata_path and metadata_path.is_file() else empty_human_review()
        task["events"] = [dict(event) for event in events]
        return task

    def list(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("""SELECT id, created_at, status, stage, original_name, current_round, message,
                               batch_id, generator_provider, generator_model, reviewer_provider, reviewer_model,
                               profile_id, profile_name, profile_version, run_mode, max_rounds,
                               generation_seconds, review_seconds FROM tasks ORDER BY created_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def batch(self, batch_id: str) -> list[dict]:
        return sorted((task for task in self.list() if task["batch_id"] == batch_id), key=lambda task: task["created_at"])

    def config(self, task_id: str) -> dict | None:
        task = self.get(task_id)
        if not task or not task["config_name"]:
            return None
        return json.loads((self.root / task_id / task["config_name"]).read_text(encoding="utf-8"))

    def add_timing(self, task_id: str, column: str, seconds: float) -> None:
        if column not in ("generation_seconds", "review_seconds"):
            raise ValueError("未知计时类型")
        with self._db() as db:
            db.execute(f"UPDATE tasks SET {column}={column}+? WHERE id=?", (seconds, task_id))

    def set_state(self, task_id: str, status: str, round_number: int, message: str,
                  final_name: str | None = None, stage: str | None = None) -> None:
        current_stage = stage or status
        with self._db() as db:
            db.execute("UPDATE tasks SET status=?, stage=?, current_round=?, message=?, final_name=COALESCE(?,final_name) WHERE id=?",
                       (status, current_stage, round_number, message, final_name, task_id))
            db.execute("INSERT INTO events (task_id, created_at, stage, round_number, message) VALUES (?,?,?,?,?)",
                       (task_id, datetime.now(TIMEZONE).isoformat(), current_stage, round_number, message))

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
            metadata["human_review"] = {"scores": annotation["scores"], "passed": annotation["passed"],
                                        "comment": annotation["comment"],
                                        "updated_at": datetime.now(TIMEZONE).isoformat()}
            write_json_atomic(path, metadata)
            return metadata

    def save_final(self, task_id: str, html: str) -> str:
        name = f"{timestamp()}_final.html"
        (self.root / task_id / name).write_text(html, encoding="utf-8")
        return name

    def artifact(self, task_id: str, filename: str) -> Path | None:
        task = self.get(task_id)
        if not task:
            return None
        allowed = {task["image_name"], task["final_name"], task["config_name"]}
        for round_info in task["rounds"]:
            allowed.update((round_info["html_name"], round_info["screenshot_name"],
                            round_info["review_name"], round_info["metadata_name"]))
        if filename not in allowed:
            return None
        path = self.root / task_id / filename
        return path if path.is_file() else None
