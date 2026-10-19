import asyncio
from pathlib import Path

import httpx

from app.config import Settings
from app.model_api import ModelClient, ModelError, chat_text, token_usage


def test_codex_gateway_chat_request(monkeypatch, tmp_path: Path):
    image = tmp_path / "problem.png"
    image.write_bytes(b"image")
    settings = Settings(tmp_path, "https://example.invalid/v1", None, "gpt-5-codex", "reviewer",
                        codex_key="test-key", codex_base_url="https://gateway.test/v1", codex_verify_tls=False)
    calls = {}

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client_options"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, **kwargs):
            calls["url"] = url
            calls["request"] = kwargs
            return httpx.Response(200, request=httpx.Request("POST", url),
                                  json={"choices": [{"message": {"content": "收到"}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 21, "completion_tokens": 8}})

    monkeypatch.setattr("app.model_api.httpx.AsyncClient", FakeClient)
    usage = []
    result = asyncio.run(ModelClient(settings).complete("gpt-5-codex", "读图", [(image, "image/png")],
                                                        max_tokens=4096, usage=usage.append))
    assert result == "收到"
    assert usage[0]["total_tokens"] == 29
    assert calls["url"] == "https://gateway.test/v1/chat/completions"
    assert calls["client_options"]["verify"] is False
    assert calls["request"]["json"]["max_completion_tokens"] == 4096
    assert calls["request"]["json"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_chat_completion_reports_truncated_output():
    import pytest

    with pytest.raises(ModelError, match="输出达到"):
        chat_text({"choices": [{"message": {"content": "<html>"}, "finish_reason": "length"}]})


def test_token_usage_normalizes_three_protocols_and_missing_values():
    assert token_usage({"usage": {"input_tokens": 10, "output_tokens": 4,
                                   "input_tokens_details": {"cached_tokens": 2}}}, "responses") == {
        "input_tokens": 10, "output_tokens": 4, "cached_input_tokens": 2,
        "cache_write_input_tokens": None, "reasoning_output_tokens": None, "total_tokens": 14}
    assert token_usage({"usage": {"prompt_tokens": 8, "completion_tokens": 5,
                                   "completion_tokens_details": {"reasoning_tokens": 3}}}, "chat")["reasoning_output_tokens"] == 3
    anthropic = token_usage({"usage": {"input_tokens": 7, "output_tokens": 2,
                                        "cache_read_input_tokens": 3,
                                        "cache_creation_input_tokens": 4}}, "anthropic")
    assert anthropic["input_tokens"] == 14 and anthropic["total_tokens"] == 16
    assert anthropic["cached_input_tokens"] == 3 and anthropic["cache_write_input_tokens"] == 4
    assert token_usage({}, "chat") is None


def test_explicit_provider_overrides_model_name(monkeypatch, tmp_path: Path):
    settings = Settings(tmp_path, "https://grok.test/v1", "grok-key", "x", "y",
                        openai_key="openai-key", openai_base_url="https://openai.test/v1",
                        codex_key="codex-key", codex_base_url="https://codex.test/v1",
                        edumind_alt_key="alt-key")
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs["headers"]["Authorization"]))
            if url.endswith("/responses"):
                return httpx.Response(200, request=httpx.Request("POST", url), json={"output_text": "ok"})
            return httpx.Response(200, request=httpx.Request("POST", url),
                                  json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("app.model_api.httpx.AsyncClient", FakeClient)
    client = ModelClient(settings)
    asyncio.run(client.complete("gpt-5-codex", "test", [], max_tokens=100, provider="openai"))
    asyncio.run(client.complete("gpt-6-sol", "test", [], max_tokens=100, provider="codex"))
    asyncio.run(client.complete("qwen3.7-plus", "test", [], max_tokens=100, provider="edumind_alt"))
    assert calls == [("https://openai.test/v1/responses", "Bearer openai-key"),
                     ("https://codex.test/v1/chat/completions", "Bearer codex-key"),
                     ("https://codex.test/v1/chat/completions", "Bearer alt-key")]


def test_deepseek_thinking_off_and_gateway_500_retry(monkeypatch, tmp_path: Path):
    settings = Settings(tmp_path, "https://unused.test/v1", None, "x", "y",
                        edumind_alt_key="test-key", codex_base_url="https://gateway.test/v1",
                        deepseek_thinking="disabled", model_timeout_seconds=900)
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["timeout"].read == 900

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, **kwargs):
            calls.append(kwargs["json"])
            if len(calls) == 1:
                return httpx.Response(500, request=httpx.Request("POST", url), text="upstream timeout")
            return httpx.Response(200, request=httpx.Request("POST", url),
                                  json={"choices": [{"message": {"content": "<html></html>"},
                                                     "finish_reason": "stop"}]})

    monkeypatch.setattr("app.model_api.httpx.AsyncClient", FakeClient)
    result = asyncio.run(ModelClient(settings).complete("deepseek-v4-flash-0731", "test", [],
                                                        max_tokens=32000, provider="edumind_alt"))
    assert result == "<html></html>"
    assert len(calls) == 2
    assert all(call["thinking"] == {"type": "disabled"} for call in calls)
    asyncio.run(ModelClient(settings).complete(
        "deepseek-v4-1-flash-261017", "test", [], max_tokens=32000,
        provider="edumind_alt", reasoning_effort="low", thinking="enabled"))
    assert calls[-1]["thinking"] == {"type": "enabled"}
    assert calls[-1]["reasoning_effort"] == "low"
