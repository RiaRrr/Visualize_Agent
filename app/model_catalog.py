"""Provider-specific model directory and short image-input probes."""
from __future__ import annotations

import asyncio
import time

from .config import Settings
from .model_api import ModelClient, ModelError

PROVIDERS = ("grok", "openai", "codex", "edumind_alt", "anthropic")
NON_TEXT = ("embedding", "audio", "speech", "transcribe", "whisper", "tts", "dall-e", "image", "realtime", "moderation")


class ModelCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: tuple[float, list[dict]] | None = None
        self.status: dict[tuple[str, str], dict] = {}

    async def list(self, refresh: bool = False) -> dict:
        if self._cache and not refresh and time.monotonic() - self._cache[0] < 300:
            models = self._cache[1]
            return {"models": [self._with_status(x) for x in models], "errors": {}}
        async def fetch(provider):
            try:
                items = await ModelClient(self.settings).list_models(provider)
                models = []
                for item in items:
                    model_id = item.get("id") if isinstance(item, dict) else None
                    if isinstance(model_id, str):
                        models.append({"provider": provider, "id": model_id,
                                       "candidate": not any(token in model_id.lower() for token in NON_TEXT)})
                return models, None
            except Exception as exc:
                if isinstance(exc, ModelError) and str(exc) == "未配置密钥":
                    return [], "未配置密钥"
                return [], f"模型目录获取失败：{type(exc).__name__}"
        results = await asyncio.gather(*(fetch(p) for p in PROVIDERS))
        models = [m for result, _ in results for m in result]
        errors = {p: err for p, (_, err) in zip(PROVIDERS, results) if err}
        if self._cache:
            models.extend(m for m in self._cache[1] if m["provider"] in errors)
        models = sorted(models, key=lambda m: (m["provider"], m["id"]))
        if models:
            self._cache = (time.monotonic(), models)
        return {"models": [self._with_status(x) for x in models], "errors": errors}

    def _with_status(self, model: dict) -> dict:
        return {**model, "probe": self.status.get((model["provider"], model["id"]),
                                               {"state": "untested", "message": "尚未短识图测试"})}

    def record(self, provider: str, model: str, ok: bool, message: str, seconds: float) -> dict:
        value = {"state": "passed" if ok else "failed", "message": message[:200],
                 "seconds": round(seconds, 2), "checked_at": time.time()}
        self.status[(provider, model)] = value
        return value
