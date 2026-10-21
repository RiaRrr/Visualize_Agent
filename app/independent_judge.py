"""Six isolated judgments, with resumable per-call evidence and usage records."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .benchmark import read, write
from .guided_math import parse_json_object
from .rubric import SCORE_KEYS

CALL_KEYS = (*SCORE_KEYS, "overall_score")
USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens",
              "cache_write_input_tokens", "reasoning_output_tokens", "total_tokens")


def build_prompts(bench: Path) -> dict[str, str]:
    folder = bench / "evaluation"
    common = (folder / "judge_prompt.md").read_text(encoding="utf-8")
    dimension = (folder / "dimension_prompt.md").read_text(encoding="utf-8")
    overall = (folder / "overall_prompt.md").read_text(encoding="utf-8")
    rubric = (bench / "rubric.md").read_text(encoding="utf-8")
    teaching = (Path(__file__).parent / "prompts/reviewer_teaching.md").read_text(encoding="utf-8")
    criteria = read(folder / "dimensions.json")
    if set(criteria) != set(SCORE_KEYS):
        raise ValueError("独立评分标准的维度不完整")
    prompts = {}
    for key in SCORE_KEYS:
        item = criteria[key]
        text = item["criteria"] + ("\n" + teaching if key == "teaching_effectiveness" else "")
        instruction = dimension.replace("{{DIMENSION}}", key).replace("{{LABEL}}", item["label"]).replace("{{CRITERIA}}", text)
        prompts[key] = common + "\n\n" + instruction
    prompts["overall_score"] = common + "\n\n" + overall.replace("{{RUBRIC}}", rubric).replace("{{TEACHING}}", teaching)
    return prompts


def prompt_version(prompts: dict[str, str]) -> str:
    # Include protocol version as well as every instruction in the cache identity.
    return hashlib.sha256(json.dumps({"protocol": "isolated-dimensions-v2", "prompts": prompts},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def parse_dimension(raw: str, key: str) -> dict:
    data = parse_json_object(raw)
    if data.get("dimension") != key or "score" not in data:
        raise ValueError("judge返回的评分维度不匹配或缺少分数")
    score = data["score"]
    minimum = 0 if key == "overall_score" else 1
    if score is not None and (type(score) is not int or not minimum <= score <= 5):
        raise ValueError("judge维度评分超出范围")
    if not isinstance(data.get("issues"), list) or not all(isinstance(x, str) for x in data["issues"]):
        raise ValueError("judge缺少有效的问题列表")
    for field in ("evidence", "summary", "reference_concern"):
        if not isinstance(data.get(field), str):
            raise ValueError("judge缺少证据或说明")
    if not data["evidence"].strip():
        raise ValueError("judge证据不能为空")
    return data


def merge_judgments(calls: dict[str, dict]) -> dict:
    if set(calls) != set(CALL_KEYS) or any(c["status"] != "completed" for c in calls.values()):
        raise ValueError("存在未完成的独立评分，不能生成完整评分")
    judgments = {key: value["judgment"] for key, value in calls.items()}
    issues = list(dict.fromkeys(issue for j in judgments.values() for issue in j["issues"]))
    concerns = list(dict.fromkeys(j["reference_concern"] for j in judgments.values() if j["reference_concern"]))
    return {"scores": {key: judgments[key]["score"] for key in SCORE_KEYS},
            "overall_score": judgments["overall_score"]["score"], "issues": issues,
            "evidence": {key: judgments[key]["evidence"] for key in CALL_KEYS},
            "reference_concern": "\n".join(concerns), "summary": judgments["overall_score"]["summary"]}


def usage_summary(attempts: list[dict]) -> dict:
    usage = {}
    for key in USAGE_KEYS:
        values = [(item.get("usage") or {}).get(key) for item in attempts]
        # Missing usage is unknown, not a free call; expose a separate known subtotal.
        usage[key] = sum(values) if values and all(type(v) is int for v in values) else None
    return {"usage": usage, "api_call_count": len(attempts),
            "missing_usage_count": sum((a.get("usage") or {}).get("total_tokens") is None for a in attempts),
            "known_total_tokens": sum((a.get("usage") or {}).get("total_tokens") or 0 for a in attempts),
            "duration_seconds": sum(a.get("duration_seconds", 0) for a in attempts)}


async def evaluate_dimensions(client, judge: dict, prompts: dict[str, str], materials: str,
                              images: list, folder: Path, evidence_hash: str, options: dict) -> dict:
    """Fresh API request per dimension; successful calls are reused on retry.

    No dimension gets another dimension's answer (including the overall call).
    Calls within a sample are sequential to respect the existing gateway concurrency cap.
    """
    calls = {}
    for key in CALL_KEYS:
        output = folder / (key + ".json")
        previous = read(output, {})
        if previous and previous.get("evidence_sha256") != evidence_hash:
            raise ValueError("独立评分材料已改变，不能复用旧维度评分")
        if previous.get("status") == "completed":
            calls[key] = previous
            continue
        prompt = prompts[key] + materials
        folder.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".prompt.txt").write_text(prompt, encoding="utf-8")
        attempt = len(list(folder.glob(key + ".attempt-*.json"))) + 1
        attempt_path = folder / f"{key}.attempt-{attempt:03d}.json"
        record = {"dimension": key, "model": judge["model"], "provider": judge["provider"],
                  "evidence_sha256": evidence_hash, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                  "created_at": datetime.now(timezone.utc).isoformat(), "status": "running", "attempt": attempt}
        write(attempt_path, record)
        write(output, record)
        used = {}
        start = time.monotonic()
        cancelled = False
        try:
            raw = await client.complete(judge["model"], prompt, images,
                                        max_tokens=64000 if options else 16000, provider=judge["provider"],
                                        usage=lambda value: used.update(value=value), **options)
            attempt_path.with_suffix(".raw.txt").write_text(raw, encoding="utf-8")
            record.update(status="completed", judgment=parse_dimension(raw, key))
        except asyncio.CancelledError:
            record.update(status="cancelled", error="评价已停止；未返回用量的费用未知")
            cancelled = True
        except Exception as exc:
            record.update(status="error", error=str(exc)[:500])
        finally:
            record.update(duration_seconds=time.monotonic() - start, usage=used.get("value"))
            write(attempt_path, record)
            write(output, record)
        if cancelled:
            raise asyncio.CancelledError
        calls[key] = record
    attempts = [read(path) for path in sorted(folder.glob("*.attempt-*.json"))]
    metrics = usage_summary(attempts)
    result = {**metrics, "evaluation_mode": "isolated_dimensions", "calls": calls}
    if all(c["status"] == "completed" for c in calls.values()):
        result.update(status="completed", judgment=merge_judgments(calls))
    else:
        failed = [key for key, value in calls.items() if value["status"] != "completed"]
        result.update(status="error", error="维度评分未全部完成：" + ", ".join(failed))
    return result
