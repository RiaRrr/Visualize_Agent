"""One short image classification request for automatic prompt routing."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model_api import ModelClient
from .profiles import SUBJECTS, router_prompt


def parse_subject(raw: str) -> str:
    value = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", value, re.I)
    if fenced:
        value = fenced.group(1)
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("分流模型没有返回有效 JSON") from exc
    subject = data.get("subject") if isinstance(data, dict) else None
    if subject not in SUBJECTS:
        raise ValueError("分流模型没有返回 math、physics、chemistry 或 general")
    return subject


async def classify_image(client: ModelClient, provider: str | None, model: str, image: Path, mime: str,
                         prompt: str | None = None) -> str:
    raw = await client.complete(model, prompt or router_prompt(), [(image, mime)], max_tokens=512, provider=provider)
    return parse_subject(raw)
