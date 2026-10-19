"""Print paired pilot metrics from saved tasks; no network calls."""
from __future__ import annotations

import json
import argparse

from app.config import get_settings
from app.storage import Store
from scripts.pilot_manifest import recover


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("v1", "v2", "v41", "v1017"), default="v1")
    args = parser.parse_args()
    settings = get_settings()
    directory, experiment = {"v1": ("_guided_pilot", "guided_math_pilot_v1"),
                             "v2": ("_guided_followup_v2", "guided_math_followup_v2"),
                             "v41": ("_guided_deepseek_v41", "guided_math_deepseek_v41"),
                             "v1017": ("_guided_deepseek_1017", "guided_math_deepseek_1017")}[args.variant]
    manifest_path = settings.data_dir / directory / "manifest.json"
    if not manifest_path.is_file():
        print("尚无实验任务：网关 OCR 未完成。")
        return
    store = Store(settings)
    manifest = recover(store, manifest_path, experiment)
    print("| 题目 | 生成模型 | 策略 | 状态 | 轮次 | 最佳审核分 | 输入 token | 输出 token | API 用时/s | 历史任务 ID |")
    print("|---|---|---|---|---:|---:|---:|---:|---:|---|")
    for key, task_id in manifest.items():
        task = store.get(task_id)
        if not task:
            continue
        case, model, strategy = key.split(":")
        calls = task["api_calls"]
        input_tokens = sum(call["input_tokens"] or 0 for call in calls)
        output_tokens = sum(call["output_tokens"] or 0 for call in calls)
        seconds = sum(call["duration_seconds"] or 0 for call in calls)
        best = task.get("best_round_score")
        print(f"| {case} | {model} | {strategy} | {task['status']} | {len(task['rounds'])} | "
              f"{best if best is not None else '—'} | {input_tokens} | {output_tokens} | "
              f"{seconds:.1f} | {task_id} |")


if __name__ == "__main__":
    main()
