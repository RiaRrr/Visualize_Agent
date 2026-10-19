"""Recover and atomically update the guided pilot task index across processes."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

from app.storage import Store


def recover(store: Store, path: Path, experiment_name: str = "guided_math_pilot_v1") -> dict[str, str]:
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    with store._db() as db:
        rows = db.execute("SELECT id, config_name FROM tasks WHERE config_name IS NOT NULL").fetchall()
    for task in rows:
        config_path = store.root / task["id"] / task["config_name"]
        if not config_path.is_file():
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("experiment") != experiment_name:
            continue
        key = f"{config.get('case')}:{config.get('generator_model')}:{config.get('generation_strategy')}"
        manifest.setdefault(key, task["id"])
    return manifest


def record(store: Store, path: Path, key: str, task_id: str,
           experiment_name: str = "guided_math_pilot_v1") -> dict[str, str]:
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = recover(store, path, experiment_name)
        manifest[key] = task_id
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return manifest
