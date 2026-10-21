"""Small Responses-compatible client with image input support."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from typing import Callable

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class ModelError(Exception):
    pass


def token_usage(data: dict, protocol: str) -> dict | None:
    """Normalize counters reported by the gateway; never estimate missing counts."""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    def count(value):
        return value if type(value) is int and value >= 0 else None
    def detail(name):
        value = usage.get(name)
        return value if isinstance(value, dict) else {}
    if protocol == "chat":
        inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
        cached = detail("prompt_tokens_details").get("cached_tokens")
        cache_write = None
        reasoning = detail("completion_tokens_details").get("reasoning_tokens")
    else:
        inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
        cached = (detail("input_tokens_details").get("cached_tokens") if protocol == "responses"
                  else usage.get("cache_read_input_tokens"))
        cache_write = usage.get("cache_creation_input_tokens") if protocol == "anthropic" else None
        reasoning = (detail("output_tokens_details").get("reasoning_tokens")
                     if protocol == "responses" else None)
    if protocol == "anthropic" and count(inputs) is not None:
        inputs = inputs + (count(cached) or 0) + (count(cache_write) or 0)
    result = {"input_tokens": count(inputs), "output_tokens": count(outputs),
              "cached_input_tokens": count(cached), "cache_write_input_tokens": count(cache_write),
              "reasoning_output_tokens": count(reasoning),
              "total_tokens": count(usage.get("total_tokens"))}
    if result["total_tokens"] is None and result["input_tokens"] is not None and result["output_tokens"] is not None:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result if any(value is not None for value in result.values()) else None


def http_error_detail(response: httpx.Response, model: str, secrets: tuple[str | None, ...] = ()) -> str:
    """Expose useful gateway diagnostics without echoing request data or credentials."""
    parts = [f"模型接口返回 HTTP {response.status_code}（{model}）"]
    try:
        data = response.json()
        error = data.get("error", {}) if isinstance(data, dict) else {}
        if isinstance(error, str):
            error = {"message": error}
        if isinstance(error, dict):
            for label, value in (("类型", error.get("type")), ("代码", error.get("code")),
                                 ("原因", error.get("message"))):
                if isinstance(value, str) and value and len(value) <= 300 and "\n" not in value:
                    for secret in secrets:
                        if secret:
                            value = value.replace(secret, "[已隐藏]")
                    value = re.sub(r"sk-[A-Za-z0-9_-]+", "[已隐藏]", value)
                    parts.append(f"{label}：{value}")
    except (json.JSONDecodeError, ValueError):
        pass
    retry_after = response.headers.get("retry-after")
    if retry_after and len(retry_after) <= 80:
        parts.append(f"Retry-After：{retry_after}")
    request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
    if request_id and len(request_id) <= 100:
        parts.append(f"请求 ID：{request_id}")
    return "；".join(parts)


def image_content(path: Path, mime: str) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "high"}


def chat_image_content(path: Path, mime: str) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"}}


def response_text(data: dict) -> str:
    if data.get("status") == "incomplete" and (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
        raise ModelError("模型输出达到 max_output_tokens 上限，响应可能不完整；请调整本环节输出预算或要求更精简的输出")
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    parts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    if not parts:
        raise ModelError("模型没有返回文字内容")
    return "\n".join(parts)


def message_text(data: dict) -> str:
    if data.get("stop_reason") == "max_tokens":
        raise ModelError("模型输出达到 max_tokens 上限，响应可能不完整；请调整本环节输出预算或要求更精简的输出")
    parts = [item.get("text", "") for item in data.get("content", []) if item.get("type") == "text"]
    if not parts:
        raise ModelError("模型没有返回文字内容")
    return "\n".join(parts)


def chat_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ModelError("Chat Completions 接口没有返回 choices")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise ModelError("模型输出达到 max_completion_tokens 上限，响应可能不完整（预算也可能被推理消耗）")
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        if any(parts):
            return "\n".join(parts)
    raise ModelError("Chat Completions 接口没有返回文字内容")


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def list_models(self, provider: str) -> list[dict]:
        """Fetch one provider's model directory; keep credentials and HTTP here."""
        s = self.settings
        if provider == "grok":
            url, key, headers, verify = f"{s.base_url}/models", s.api_key, {}, True
        elif provider == "openai":
            url, key, headers, verify = f"{s.openai_base_url or s.base_url}/models", s.openai_key, {}, True
        elif provider in ("codex", "edumind_alt"):
            url, key, headers, verify = f"{s.codex_base_url}/models", (s.codex_key if provider == "codex" else s.edumind_alt_key), {}, s.codex_verify_tls
        elif provider == "anthropic":
            url, key, headers, verify = f"{s.anthropic_base_url}/v1/models", s.anthropic_key, {"anthropic-version": "2023-06-01"}, True
        else:
            raise ModelError("未知模型接口")
        if not key:
            raise ModelError("未配置密钥")
        headers.update({"x-api-key": key} if provider == "anthropic" else {"Authorization": f"Bearer {key}"})
        async with httpx.AsyncClient(timeout=20, trust_env=s.use_proxy, verify=verify) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        return data.get("data", []) if isinstance(data, dict) else []

    async def complete(self, model: str, prompt: str, images: list[tuple[Path, str]], *, max_tokens: int,
                       progress: Callable[[str], None] | None = None, provider: str | None = None,
                       usage: Callable[[dict | None], None] | None = None,
                       reasoning_effort: str | None = None, thinking: str | None = None) -> str:
        if reasoning_effort not in (None, "low", "high", "max"):
            raise ModelError("未知推理强度")
        if thinking not in (None, "enabled", "disabled"):
            raise ModelError("未知推理开关")
        if provider not in (None, "anthropic", "codex", "edumind_alt", "openai", "grok"):
            raise ModelError("未知模型接口")
        is_claude = provider == "anthropic" if provider else model.startswith("claude-")
        is_codex = provider in ("codex", "edumind_alt") if provider else "-codex" in model
        if is_claude:
            if not self.settings.anthropic_key:
                raise ModelError("没有找到 Claude API 密钥；请设置 VISUALIZE_ANTHROPIC_KEY")
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image", "source": {"type": "base64", "media_type": mime,
                                                     "data": base64.b64encode(path.read_bytes()).decode("ascii")}} for path, mime in images]
            payload = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens}
            headers = {"x-api-key": self.settings.anthropic_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            url = f"{self.settings.anthropic_base_url}/v1/messages"
        elif is_codex:
            codex_key = self.settings.edumind_alt_key if provider == "edumind_alt" else self.settings.codex_key
            if not codex_key:
                raise ModelError("没有找到网关密钥；请设置 VISUALIZE_EDUMIND_ALT_KEY 或 VISUALIZE_CODEX_KEY")
            content = [{"type": "text", "text": prompt}]
            content += [chat_image_content(path, mime) for path, mime in images]
            payload = {"model": model, "messages": [{"role": "user", "content": content}],
                       "max_completion_tokens": max_tokens}
            deepseek_thinking = thinking or self.settings.deepseek_thinking
            if model.lower().startswith(("deepseek-", "deepseek-ai/")) and deepseek_thinking in ("enabled", "disabled"):
                payload["thinking"] = {"type": deepseek_thinking}
            if model.lower().startswith(("deepseek-", "deepseek-ai/")) and reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            headers = {"Authorization": f"Bearer {codex_key}", "Content-Type": "application/json"}
            url = f"{self.settings.codex_base_url}/chat/completions"
        else:
            is_openai = provider == "openai" if provider else model.startswith(("gpt-", "codex-", "o1", "o3", "o4"))
            key = self.settings.openai_key if is_openai else self.settings.api_key
            if not key:
                name = "VISUALIZE_OPENAI_KEY" if is_openai else "VISUALIZE_API_KEY"
                raise ModelError(f"没有找到 Responses API 密钥；请设置 {name}")
            content = [{"type": "input_text", "text": prompt}]
            content += [image_content(path, mime) for path, mime in images]
            payload = {"model": model, "input": [{"role": "user", "content": content}], "max_output_tokens": max_tokens}
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            base_url = (self.settings.openai_base_url or self.settings.base_url) if is_openai else self.settings.base_url
            url = f"{base_url}/responses"
        verify_tls = self.settings.codex_verify_tls if is_codex else True
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.settings.model_timeout_seconds, connect=20.0),
                                     trust_env=self.settings.use_proxy, verify=verify_tls) as client:
            for attempt in range(3):
                try:
                    if progress:
                        progress(f"正在请求 {model}（第 {attempt + 1}/3 次）")
                    result = await asyncio.wait_for(
                        client.post(url, headers=headers, json=payload),
                        timeout=self.settings.model_timeout_seconds,
                    )
                    if result.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        if progress:
                            progress(f"{model} 返回 HTTP {result.status_code}，准备重试")
                        retry_after = result.headers.get("retry-after", "")
                        delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** attempt
                        await asyncio.sleep(min(30.0, max(1.0, delay)))
                        continue
                    result.raise_for_status()
                    data = result.json()
                    if usage:
                        usage(token_usage(data, "anthropic" if is_claude else "chat" if is_codex else "responses"))
                    return message_text(data) if is_claude else chat_text(data) if is_codex else response_text(data)
                except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                    if progress:
                        progress(f"{model} 请求超时，本轮已停止")
                    raise ModelError(f"模型请求超时（{model}）；可在任务页继续处理") from exc
                except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as exc:
                    if attempt < 2:
                        if progress:
                            progress(f"{model} 连接失败，准备重试")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise ModelError(f"模型连接失败：{type(exc).__name__}") from exc
                except httpx.HTTPStatusError as exc:
                    raise ModelError(http_error_detail(exc.response, model,
                                                       (self.settings.api_key, self.settings.anthropic_key,
                                                        self.settings.openai_key, self.settings.codex_key,
                                                        self.settings.edumind_alt_key))) from exc
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ModelError("模型接口返回了无效 JSON") from exc
        raise ModelError("模型请求失败")
