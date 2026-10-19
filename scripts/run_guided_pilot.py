"""Run paired, three-round math OCR experiments without changing the live service.

Each task is saved in data_v2 and appears in the existing history UI.
Usage: .venv/bin/python scripts/run_guided_pilot.py [--case triangle|parabola|conic]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.model_api import ModelClient
from app.pipeline import Pipeline
from app.profiles import PROMPTS, get_profile
from app.storage import Store
from scripts.pilot_manifest import recover, record

CASES = {
    "triangle": "20261016_115605_757_f42cea66",
    "parabola": "20261016_115908_828_8655714d",
    "conic": "20261016_131130_962_6e0df07e",
}
MODELS = ("qwen3.6-flash", "deepseek-v4-flash", "deepseek-v4-1-flash-260910", "deepseek-v4-1-flash-261017")
V41_MODEL = "deepseek-v4-1-flash-260910"
V1017_MODEL = "deepseek-v4-1-flash-261017"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--model", choices=MODELS)
    parser.add_argument("--strategy", choices=("direct", "guided_math"))
    parser.add_argument("--variant", choices=("v1", "v2", "v41", "v1017"), default="v1")
    args = parser.parse_args()
    settings = get_settings()
    store = Store(settings)
    client = ModelClient(settings)
    experiment_name = {"v1": "guided_math_pilot_v1", "v2": "guided_math_followup_v2",
                       "v41": "guided_math_deepseek_v41", "v1017": "guided_math_deepseek_1017"}[args.variant]
    experiment = settings.data_dir / {"v1": "_guided_pilot", "v2": "_guided_followup_v2",
                                      "v41": "_guided_deepseek_v41", "v1017": "_guided_deepseek_1017"}[args.variant]
    experiment.mkdir(exist_ok=True)
    manifest_path = experiment / "manifest.json"
    manifest = recover(store, manifest_path, experiment_name)
    profile = get_profile("math-current", "ocr_text", "ocr_text")
    for name, source_id in CASES.items():
        if args.case and name != args.case:
            continue
        source = next((settings.data_dir / source_id).glob("*_input.*"))
        ocr_path = experiment / f"{name}.question_ocr.md"
        if args.variant in ("v2", "v41", "v1017") and not ocr_path.is_file():
            original_ocr = settings.data_dir / "_guided_pilot" / f"{name}.question_ocr.md"
            if original_ocr.is_file():
                ocr_path.write_text(original_ocr.read_text(encoding="utf-8"), encoding="utf-8")
        if not ocr_path.is_file():
            started = time.monotonic()
            ocr_usage: dict = {}
            try:
                ocr = await client.complete("qwen3.6-flash", (PROMPTS / "question_ocr.md").read_text(encoding="utf-8"),
                                            [(source, "image/png")], max_tokens=12000, provider="edumind_alt",
                                            usage=lambda value: ocr_usage.update(value=value or {}))
            except Exception as exc:
                (experiment / f"{name}.ocr_error.json").write_text(
                    json.dumps({"case": name, "model": "qwen3.6-flash", "stage": "question_ocr",
                                "error_type": type(exc).__name__, "message": str(exc),
                                "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                raise
            ocr_path.write_text(ocr, encoding="utf-8")
            (experiment / f"{name}.ocr_metrics.json").write_text(json.dumps(
                {"provider": "edumind_alt", "model": "qwen3.6-flash", "seconds": round(time.monotonic() - started, 2),
                 "usage": ocr_usage}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"OCR {name}: {time.monotonic()-started:.1f}s", flush=True)
        variant_models = ((V41_MODEL,) if args.variant == "v41" else
                          (V1017_MODEL,) if args.variant == "v1017" else MODELS[:2])
        for model in variant_models:
            if args.model and model != args.model:
                continue
            for strategy in ("direct", "guided_math"):
                if args.strategy and strategy != args.strategy:
                    continue
                if args.variant == "v2" and strategy != "guided_math":
                    continue
                key = f"{name}:{model}:{strategy}"
                previous_id = manifest.get(key)
                previous = store.get(previous_id) if previous_id else None
                if previous and previous["status"] in ("passed", "failed") and len(previous["rounds"]) == 3:
                    print(f"SKIP {key}: {previous_id}", flush=True)
                    continue
                if previous and previous["status"] not in ("error", "interrupted", "queued"):
                    print(f"SKIP active {key}: {previous_id}", flush=True)
                    continue
                if previous:
                    task = previous
                else:
                    config = {
                        "profile_id": "math-current", "profile_name": profile.name, "profile_version": profile.version,
                        "subject": "math", "generator_prompt": profile.generator_prompt,
                        "reviewer_prompt": profile.reviewer_prompt, "run_mode": "fixed_five", "max_rounds": 3,
                        "generation_strategy": strategy, "generator_mode": "ocr_text", "reviewer_mode": "ocr_text",
                        "generator_provider": "codex" if args.variant == "v1017" else "edumind_alt", "generator_model": model,
                        "reviewer_provider": "edumind_alt", "reviewer_model": "qwen3.6-flash",
                        "plan_reviewer_provider": "edumind_alt", "plan_reviewer_model": "qwen3.6-35b-a3b",
                        "ocr_provider": "edumind_alt", "ocr_model": "qwen3.6-flash",
                        "experiment": experiment_name, "case": name,
                    }
                    if strategy == "guided_math":
                        config.update({key: (PROMPTS / filename).read_text(encoding="utf-8") for key, filename in {
                            "math_plan_prompt": "math_guided_plan.md", "math_plan_review_prompt": "math_guided_plan_review.md",
                            "math_guided_html_prompt": "math_guided_html.md"}.items()})
                    task = store.create(f"pilot_{args.variant}_{name}.png", "image/png", ".png", source.read_bytes(),
                                        run_config=config)
                    (settings.data_dir / task["id"] / "pilot_question_ocr.md").write_text(
                        ocr_path.read_text(encoding="utf-8"), encoding="utf-8")
                    manifest = record(store, manifest_path, key, task["id"], experiment_name)
                print(f"RUN {key}: {task['id']}", flush=True)
                started = time.monotonic()
                await Pipeline(settings, store, client).run(task["id"])
                result = store.get(task["id"])
                print(f"DONE {key}: {result['status']} rounds={len(result['rounds'])} "
                      f"seconds={time.monotonic()-started:.1f} message={result['message'][:160]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
