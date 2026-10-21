import pytest
from app.pricing import estimate_cost


def test_pilot_cost_uses_total_input_output_without_recounting_subsets():
    calls = [{"provider":"codex", "model":"deepseek-v4-1-flash-261017", "input_tokens":470153,
              "output_tokens":1150308, "cached_input_tokens":10000, "reasoning_output_tokens":900000},
             {"provider":"openai", "model":"gpt-5.6-sol", "input_tokens":244280, "output_tokens":14880}]
    assert estimate_cost(calls)["known_cost_cny"] == pytest.approx(7.753462)


def test_missing_usage_is_unknown_and_unpriced_model_not_free():
    result = estimate_cost([{"provider":"codex", "model":"deepseek-v4-1-flash-261017"},
                            {"provider":"unknown", "model":"unknown", "input_tokens":10}])
    assert result['known_cost_cny'] is None
    assert result['missing_token_fields'] == 2
    assert result['unpriced_calls'] == 1
