"""One-call DeepSeek image-to-HTML baseline; no OCR, planning, or model review."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.browser import render_html
from app.config import get_settings
from app.model_api import ModelClient
from app.pipeline import clean_html, validate_html
from app.storage import timestamp

PROMPT = "我需要你帮我生成一个html可视化这个题目，希望用户能够从这个html中直观感受到答案是怎么来的。"
MODEL = "deepseek-v4-1-flash-261017"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    image = args.image.resolve()
    if not image.is_file():
        parser.error(f"Image does not exist: {image}")
    settings = get_settings()
    folder = settings.data_dir / "_deepseek_direct_image" / f"{timestamp()}_{image.stem}"
    folder.mkdir(parents=True)
    (folder / "prompt.txt").write_text(PROMPT + "\n", encoding="utf-8")
    (folder / image.name).write_bytes(image.read_bytes())
    metadata = {"model": args.model, "provider": "codex", "endpoint": settings.codex_base_url + "/chat/completions",
                "input_mode": "original_image", "prompt": PROMPT, "image": image.name,
                "ocr": False, "planning": False, "model_review": False, "max_completion_tokens": 64000}
    started = time.monotonic()
    usage: dict = {}
    try:
        raw = await ModelClient(settings).complete(args.model, PROMPT, [(image, "image/png")],
                                                   max_tokens=64000, provider="codex",
                                                   usage=lambda value: usage.update(value or {}),
                                                   progress=lambda message: print(message, flush=True))
        metadata["generation_seconds"] = round(time.monotonic() - started, 2)
        metadata["usage"] = usage
        (folder / "response_raw.txt").write_text(raw, encoding="utf-8")
        html = clean_html(raw)
        metadata["html_validation_errors"] = validate_html(html)
        if "<html" in html.lower() and "</html" in html.lower():
            (folder / "result.html").write_text(html, encoding="utf-8")
            try:
                render_started = time.monotonic()
                (folder / "screenshot.png").write_bytes(await render_html(html))
                metadata["render_seconds"] = round(time.monotonic() - render_started, 2)
                metadata["render_status"] = "ok"
            except Exception as exc:
                metadata["render_status"] = "error"
                metadata["render_error"] = f"{type(exc).__name__}: {exc}"
        else:
            metadata["render_status"] = "no_html"
    except Exception as exc:
        metadata["generation_seconds"] = round(time.monotonic() - started, 2)
        metadata["error"] = f"{type(exc).__name__}: {exc}"
    (folder / "metrics.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"folder": str(folder), **metadata}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
