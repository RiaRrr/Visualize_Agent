"""Server-only configuration. Never send these values to the browser."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env_file_value(name: str) -> str | None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return None
    match = re.search(rf'^\s*{re.escape(name)}\s*=\s*(.*?)\s*$', env_file.read_text(encoding="utf-8"), re.M)
    return match.group(1).strip('"\'') if match else None


def _setting(name: str, default: str | None = None) -> str | None:
    return os.getenv(name) or _env_file_value(name) or default


def _data_dir() -> Path:
    configured = Path(_setting("VISUALIZE_DATA_DIR", "data") or "data").expanduser()
    return (configured if configured.is_absolute() else ROOT / configured).resolve()


def _key_from_notes(name: str) -> str | None:
    notes = ROOT / "apikeys.md"
    if not notes.is_file():
        return None
    # Only recognize shell export lines; do not execute the notes file.
    match = re.search(rf'^export\s+{re.escape(name)}\s*=\s*["\']?([^\s"\']+)', notes.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else None


def _openai_key_from_notes() -> str | None:
    notes = ROOT / "apikeys.md"
    if not notes.is_file():
        return None
    # The Codex configuration in the notes contains an auth.json example.
    match = re.search(r'"OPENAI_API_KEY"\s*:\s*"([^"\s]+)"', notes.read_text(encoding="utf-8"))
    return match.group(1) if match else None


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    base_url: str
    api_key: str | None
    generator_model: str
    reviewer_model: str
    max_rounds: int = 5
    use_proxy: bool = False
    anthropic_key: str | None = None
    anthropic_base_url: str = "https://sub2api.minutex.cn"
    generator_max_tokens: int = 16000
    openai_key: str | None = None
    openai_base_url: str | None = None
    codex_key: str | None = None
    codex_base_url: str = "https://llm-gateway.edumind.ai/v1"
    codex_verify_tls: bool = True
    edumind_alt_key: str | None = None
    model_timeout_seconds: float = 300.0
    reviewer_max_tokens: int = 6000
    deepseek_thinking: str = "default"
    description_max_tokens: int = 3000


def get_settings() -> Settings:
    return Settings(
        data_dir=_data_dir(),
        base_url=(_setting("VISUALIZE_BASE_URL", "https://sub2api.minutex.cn/v1") or "").rstrip("/"),
        api_key=_setting("VISUALIZE_API_KEY") or _setting("XAI_API_KEY") or _key_from_notes("XAI_API_KEY"),
        generator_model=_setting("VISUALIZE_GENERATOR_MODEL", "claude-sonnet-4-6") or "claude-sonnet-4-6",
        reviewer_model=_setting("VISUALIZE_REVIEWER_MODEL", "claude-opus-4-6") or "claude-opus-4-6",
        use_proxy=_setting("VISUALIZE_USE_PROXY", "0") == "1",
        anthropic_key=_setting("VISUALIZE_ANTHROPIC_KEY") or _setting("ANTHROPIC_AUTH_TOKEN") or _key_from_notes("ANTHROPIC_AUTH_TOKEN"),
        anthropic_base_url=(_setting("VISUALIZE_ANTHROPIC_BASE_URL") or _setting("ANTHROPIC_BASE_URL", "https://sub2api.minutex.cn") or "").rstrip("/"),
        generator_max_tokens=max(512, min(128000, int(_setting("VISUALIZE_GENERATOR_MAX_TOKENS", "16000") or "16000"))),
        openai_key=_setting("VISUALIZE_OPENAI_KEY") or _setting("OPENAI_API_KEY") or _openai_key_from_notes(),
        openai_base_url=(_setting("VISUALIZE_OPENAI_BASE_URL") or _setting("VISUALIZE_BASE_URL", "https://sub2api.minutex.cn/v1") or "").rstrip("/"),
        codex_key=_setting("VISUALIZE_CODEX_KEY"),
        codex_base_url=(_setting("VISUALIZE_CODEX_BASE_URL", "https://llm-gateway.edumind.ai/v1") or "").rstrip("/"),
        codex_verify_tls=_setting("VISUALIZE_CODEX_VERIFY_TLS", "1") != "0",
        edumind_alt_key=_setting("VISUALIZE_EDUMIND_ALT_KEY"),
        model_timeout_seconds=max(30.0, min(1800.0, float(_setting("VISUALIZE_MODEL_TIMEOUT_SECONDS", "300") or "300"))),
        reviewer_max_tokens=max(1000, min(32000, int(_setting("VISUALIZE_REVIEWER_MAX_TOKENS", "6000") or "6000"))),
        deepseek_thinking=_setting("VISUALIZE_DEEPSEEK_THINKING", "default") or "default",
        description_max_tokens=max(1000, min(16000, int(_setting("VISUALIZE_DESCRIPTION_MAX_TOKENS", "3000") or "3000"))),
    )
