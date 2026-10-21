import asyncio
import json
from pathlib import Path

import pytest

from app.independent_judge import (
    CALL_KEYS, build_prompts, evaluate_dimensions, parse_dimension, prompt_version, usage_summary,
)


def response(key, score=3):
    return json.dumps({"dimension": key, "score": score, "issues": [],
                       "evidence": "本次独立核对依据", "summary": "评价", "reference_concern": ""})


def test_prompts_focus_one_dimension_and_version_all_instructions():
    prompts = build_prompts(Path(__file__).resolve().parents[1] / "benchmark")
    for key, prompt in prompts.items():
        assert prompt.count("当前评分维度：") == 1
        assert "当前评分维度：" + key in prompt
        assert "{{" not in prompt
    assert "但还不会做这道题" in prompts["teaching_effectiveness"]
    assert "教学效果评分锚点" not in prompts["page_quality"]
    changed = {**prompts, "teaching_effectiveness": prompts["teaching_effectiveness"] + "新规则"}
    assert prompt_version(prompts) != prompt_version(changed)


def test_dimension_parser_rejects_wrong_dimension_boolean_and_missing_evidence():
    with pytest.raises(ValueError):
        parse_dimension(response("page_quality"), "solution_correctness")
    with pytest.raises(ValueError):
        parse_dimension(response("page_quality", True), "page_quality")
    with pytest.raises(ValueError):
        parse_dimension(response("page_quality", 0), "page_quality")
    assert parse_dimension(response("overall_score", 0), "overall_score")["score"] == 0
    assert parse_dimension(response("page_quality", None), "page_quality")["score"] is None
    data = json.loads(response("page_quality")); data["evidence"] = ""
    with pytest.raises(ValueError):
        parse_dimension(json.dumps(data), "page_quality")


def test_failed_dimension_resumes_without_repeating_paid_successes(tmp_path):
    calls = []
    class Client:
        async def complete(self, model, prompt, images, **kwargs):
            key = prompt.splitlines()[0]
            calls.append(key)
            assert "本次独立核对依据" not in prompt
            assert images == ["original-image"]
            if key == "solution_correctness" and calls.count(key) == 1:
                raise RuntimeError("mock timeout, usage unknown")
            kwargs["usage"]({"total_tokens": 10})
            return response(key)
    prompts = {key: key for key in CALL_KEYS}
    args = (Client(), {"model": "mock", "provider": "mock"}, prompts, "\nHTML及相同证据",
            ["original-image"], tmp_path, "fixed-evidence", {})
    first = asyncio.run(evaluate_dimensions(*args))
    assert first["status"] == "error" and "judgment" not in first
    assert len(calls) == 6
    second = asyncio.run(evaluate_dimensions(*args))
    assert second["status"] == "completed" and len(calls) == 7
    assert second["api_call_count"] == 7
    assert second["known_total_tokens"] == 60
    assert second["usage"]["total_tokens"] is None
    assert second["missing_usage_count"] == 1
    assert len(list(tmp_path.glob("*.attempt-*.json"))) == 7
    assert len(list(tmp_path.glob("*.prompt.txt"))) == 6
    with pytest.raises(ValueError, match="材料已改变"):
        asyncio.run(evaluate_dimensions(*args[:-2], "changed-evidence", {}))
    assert len(calls) == 7


def test_missing_token_counters_are_not_reported_as_zero():
    result = usage_summary([{"usage": {"total_tokens": 10}}, {"usage": None}])
    assert result["usage"]["total_tokens"] is None
    assert result["known_total_tokens"] == 10
    assert result["missing_usage_count"] == 1
