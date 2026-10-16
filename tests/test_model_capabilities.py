from app.model_capabilities import capability


def test_image_models_and_text_only_aliases_are_distinguished():
    assert capability("edumind_alt", "qwen3.6-flash")["image_input"] is True
    assert capability("edumind_alt", "deepseek-v4-pro")["image_input"] is True
    assert capability("edumind_alt", "deepseek-ai/DeepSeek-V4-Pro")["image_input"] is False
    assert capability("edumind_alt", "deepseek-ai/DeepSeek-V4-Pro")["text_input"] is True
    assert capability("edumind_alt", "unknown")["image_input"] is None


def test_current_probe_overrides_old_smoke_test():
    assert capability("edumind_alt", "qwen3.6-flash", {"state": "failed"})["image_input"] is False
    assert capability("edumind_alt", "unknown", {"state": "passed"})["image_input"] is True
