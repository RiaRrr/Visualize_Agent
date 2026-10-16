"""Local gateway smoke-test results; these are not guarantees of long HTML quality."""

TESTED_AT = "2026-10-16"
IMAGE_OK = {
    "qwen3.6-flash", "qwen3.6-35b-a3b", "Qwen3.6-35B-A3B", "Qwen/Qwen3.6-35B-A3B",
    "qwen3.7-plus", "qwen3.8-27b", "Qwen3.8-27B", "Qwen/Qwen3.8-27B",
    "deepseek-v4-pro", "deepseek-v4-flash-0731", "deepseek-v4-1-flash-260910",
    "glm-5-3-flash-260828",
}
TEXT_ONLY = {
    "qwen3.7-max", "deepseek-ai/DeepSeek-V4-Pro", "deepseek-v4-flash",
    "deepseek-ai/DeepSeek-V4-Flash",
}


def capability(provider: str, model: str, probe: dict | None = None) -> dict:
    """A successful live image probe upgrades a model; failure downgrades it."""
    if provider == "edumind_alt" and model in IMAGE_OK:
        result = {"text_input": True, "image_input": True, "tested_at": TESTED_AT}
    elif provider == "edumind_alt" and model in TEXT_ONLY:
        result = {"text_input": True, "image_input": False, "tested_at": TESTED_AT}
    elif provider == "openai" and model in {"gpt-6-sol", "gpt-5.6-sol"}:
        result = {"text_input": True, "image_input": True, "tested_at": TESTED_AT}
    else:
        result = {"text_input": None, "image_input": None, "tested_at": None}
    if probe and probe.get("state") in {"passed", "failed"}:
        result["image_input"] = probe["state"] == "passed"
        result["tested_at"] = probe.get("checked_at")
    return result
