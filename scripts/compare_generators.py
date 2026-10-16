"""Compare one-round visualizations from two models on the same local questions."""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.browser import render_html  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.model_api import ModelClient  # noqa: E402
from app.pipeline import REVIEWER_INSTRUCTIONS, clean_html, generator_prompt, parse_review, validate_html  # noqa: E402


async def compare(images: list[Path], models: list[str], output: Path) -> list[dict]:
    settings = get_settings()
    client = ModelClient(settings)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for image in images:
        mime = mimetypes.guess_type(image.name)[0] or "image/png"
        for model in models:
            name = f"{image.parent.name}_{model.replace('/', '_')}"
            entry = {"input": str(image), "model": model, "reviewer_model": settings.reviewer_model}
            started = time.monotonic()
            try:
                raw = await client.complete(model, generator_prompt(), [(image, mime)],
                                            max_tokens=settings.generator_max_tokens)
                entry["generation_seconds"] = round(time.monotonic() - started, 2)
                html = clean_html(raw)
                html_path = output / f"{name}.html"
                html_path.write_text(html, encoding="utf-8")
                entry["html"] = str(html_path)
                entry["validation_issues"] = validate_html(html)
                if not entry["validation_issues"]:
                    screenshot = await render_html(html)
                    screenshot_path = output / f"{name}.png"
                    screenshot_path.write_bytes(screenshot)
                    entry["screenshot"] = str(screenshot_path)
                    review_image = output / f"{name}.review_input.png"
                    try:
                        review_image.write_bytes(screenshot)
                        review_started = time.monotonic()
                        response = await client.complete(
                            settings.reviewer_model, REVIEWER_INSTRUCTIONS + "\n\nHTML 源码：\n" + html,
                            [(image, mime), (review_image, "image/png")], max_tokens=2000)
                        entry["review_seconds"] = round(time.monotonic() - review_started, 2)
                        entry["review"] = parse_review(response)
                    finally:
                        review_image.unlink(missing_ok=True)
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            results.append(entry)
            (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{image.parent.name} {model}: {entry.get('generation_seconds', 'error')}s, "
                  f"review={entry.get('review', {}).get('passed', 'unavailable')}", flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", type=Path, help="题图；不指定时使用 data/*/*_input.*")
    parser.add_argument("--models", nargs="+", default=["gpt-5.6-sol", "gpt-6-sol"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    images = args.images or sorted((ROOT / "data").glob("*/*_input.*"))
    if not images:
        parser.error("找不到题图")
    if any(not image.is_file() for image in images):
        parser.error("部分题图不存在")
    output = args.output or ROOT / "data" / "benchmarks" / datetime.now().strftime("%Y%m%d_%H%M%S")
    asyncio.run(compare(images, args.models, output))
    print(f"结果：{output / 'results.json'}")


if __name__ == "__main__":
    main()
