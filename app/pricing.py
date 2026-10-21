"""User-provided CNY / million-token estimates, not provider invoices."""
RATES = {
    ("codex", "deepseek-v4-1-flash-261017"): (1.20, 4.80),
    ("edumind_alt", "deepseek-v4-1-flash-261017"): (1.20, 4.80),
    ("openai", "gpt-5.6-sol"): (5.00, 30.00),
}


def estimate_cost(calls):
    amount = 0.0
    known = missing = unpriced = 0
    for call in calls:
        rates = RATES.get((call.get("provider"), call.get("model")))
        if rates is None:
            unpriced += 1
            continue
        for key, rate in zip(("input_tokens", "output_tokens"), rates):
            value = call.get(key)
            if type(value) is not int or value < 0:
                missing += 1
            else:
                known += 1
                amount += value * rate / 1_000_000
    return {"known_cost_cny": amount if known else None, "missing_token_fields": missing,
            "unpriced_calls": unpriced, "currency": "CNY", "basis": "input/output; no separate cache or reasoning charge"}
