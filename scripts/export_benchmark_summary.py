"""Export the saved pilot and real human labels; no model calls or inferred ratings."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
KEYS = ("problem_fidelity", "solution_correctness", "page_quality", "interaction_quality", "teaching_effectiveness")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    samples = read(BENCH / "evaluation/samples.json")
    runs = {r["sample_id"]: r for r in read(BENCH / "reports/pilot_metrics.json")["runs"]}
    # Compare only the original joint-judge version, never mix versions in one table.
    version = "c3fcf583bc0c"
    gpts = {r["sample_id"]: r for r in read(BENCH / "reports/gpt_scores.json")["rows"] if r["version"] == version}
    rows = []
    for s in sorted(samples, key=lambda s: ({"math":0,"physics":1,"chemistry":2}[s["question_id"].split('_')[0]], s["question_id"], s["workflow_id"])):
        human = read(BENCH / "evaluation/human/primary" / (s["id"] + ".json"))
        if human["html_sha256"] != s["html_sha256"] or hashlib.sha256((BENCH / s["html"]).read_bytes()).hexdigest() != s["html_sha256"]:
            raise ValueError("人工评分与 HTML 版本不一致：" + s["id"])
        r, g = runs[s["id"]], gpts[s["id"]]
        u = g["usage"]
        fee = (u["input_tokens"] * 5 + u["output_tokens"] * 30) / 1_000_000
        rows.append({"sample_id":s["id"],"question_id":s["question_id"],"workflow_id":s["workflow_id"],
                     "selected_round":s["round_number"],"completed_rounds":s["completed_rounds"],"html":s["html"],"html_sha256":s["html_sha256"],
                     "human_status":human["status"],"human_rubric_version":human.get("rubric_version"),
                     "human_updated_at":human.get("updated_at"),"human_scores":human["scores"],
                     "human_overall":human["overall_score"],"human_comment":human.get("comment",""),"human_flags":human.get("flags",[]),
                     "gpt_version":version,"gpt_scores":g["judgment"]["scores"],"gpt_overall":g["judgment"]["overall_score"],
                     "deepseek_known_tokens":r["known_total_tokens"],"deepseek_missing_usage_calls":r["calls_missing_usage"],
                     "deepseek_api_seconds":r["api_seconds_including_failures"],"deepseek_estimated_cost_cny":r["known_cost_cny"],
                     "gpt_tokens":u["total_tokens"],"gpt_input_tokens":u["input_tokens"],"gpt_output_tokens":u["output_tokens"],
                     "gpt_seconds":g["seconds"],"gpt_estimated_cost_cny":round(fee,8)})
    valid = [r for r in rows if r["human_status"] == "submitted" and '参考答案疑问' not in r["human_flags"]]
    totals = {key:sum(r[key] for r in rows) for key in (
        "deepseek_known_tokens","deepseek_missing_usage_calls","deepseek_api_seconds","deepseek_estimated_cost_cny",
        "gpt_tokens","gpt_input_tokens","gpt_output_tokens","gpt_seconds","gpt_estimated_cost_cny")}
    totals["known_total_tokens"] = totals["deepseek_known_tokens"] + totals["gpt_tokens"]
    totals["known_estimated_cost_cny"] = totals["deepseek_estimated_cost_cny"] + totals["gpt_estimated_cost_cny"]
    comparison = {"n":len(valid), "question_count":len({r['question_id'] for r in valid}),
                  "human_mean_overall":sum(r['human_overall'] for r in valid)/len(valid),
                  "gpt_mean_overall":sum(r['gpt_overall'] for r in valid)/len(valid),
                  "overall_mae":sum(abs(r['human_overall']-r['gpt_overall']) for r in valid)/len(valid),
                  "gpt_higher_count":sum(r['gpt_overall']>r['human_overall'] for r in valid)}
    result = {"exported_at":datetime.now(timezone.utc).isoformat(),"scope":"pilot-v1, 3 questions × 3 workflows; historical samples excluded",
              "pricing":{"source":"用户提供的暂定人民币单价，未核对网关账单", "unit":"CNY per 1,000,000 tokens",
                         "gpt_input":5,"gpt_output":30,"gpt_cache_discount":"未另设折扣，输入总量统一按5元估算",
                         "deepseek_input":1.20,"deepseek_output":4.80,"cache_pricing":"不另计缓存费用",
                         "total_bill_cny":None},"totals":totals,"comparison":comparison,"rows":rows}
    out = BENCH / "reports"
    (out / "分享汇总.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
    flat=[]
    for r in rows:
        item={k:v for k,v in r.items() if k not in ('human_scores','gpt_scores','human_flags')}
        item['human_flags']=';'.join(r['human_flags'])
        for who in ('human','gpt'):
            item.update({who+'_'+key:r[who+'_scores'][key] for key in KEYS})
        flat.append(item)
    with (out/'分享汇总.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    lines=["# 首批人工评分与成本汇总", "", "统计范围：3道开发题 × 3种workflow，21轮HTML、9份选中页面。9份均已提交人工评分；另3份历史标注不混入本表。",
           "原始评分按保存值导出，未修改人工评分。GPT来自旧联合评分版本 `"+version+"`；新六次独立评分尚未运行。本表不是新旧judge的对照结果。", "",
           "| 题目 | 方案 | 选中/总轮 | 人工综合 | GPT综合 | DeepSeek已知token | GPT token | GPT估算费用 | HTML |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for r in rows:
        lines.append(f"| {r['question_id']} | {r['workflow_id']} | {r['selected_round']}/{r['completed_rounds']} | {r['human_overall']} | {r['gpt_overall']} | {r['deepseek_known_tokens']:,} | {r['gpt_tokens']:,} | ¥{r['gpt_estimated_cost_cny']:.4f} | [{r['sample_id']}](../{r['html']}) |")
    lines += ["", "## 评分观察", "",
              f"人工综合均分 {comparison['human_mean_overall']:.2f}，旧GPT均分 {comparison['gpt_mean_overall']:.2f}，综合分MAE {comparison['overall_mae']:.2f}；GPT在{comparison['gpt_higher_count']}/9份页面上高于人工分。",
              "这批结果提示旧judge打分偏宽松，特别需要关注教学理解与页面质量。但仅有3个独立题目、单一人工评分者，不能据此宣布某workflow或judge最好；人工分也不是绝对真值。",
              "", "## Token、耗时与人民币估算", "",
              f"- DeepSeek生成、流程内审核、修订：已知 {totals['deepseek_known_tokens']:,} token；API累计 {totals['deepseek_api_seconds']:.1f} 秒，含失败尝试。",
              f"- GPT旧联合评分：{totals['gpt_tokens']:,} token（输入 {totals['gpt_input_tokens']:,}，输出 {totals['gpt_output_tokens']:,}）；API累计 {totals['gpt_seconds']:.1f} 秒。",
              f"- 合计已知 {totals['known_total_tokens']:,} token。推理token属于输出的子项，不重复累加。API累计时间不等于并行运行的墙钟耗时。",
              f"- GPT暂按用户给出的输入¥5/MTok、输出¥30/MTok：输入×5/百万＋输出×30/百万，约 **¥{totals['gpt_estimated_cost_cny']:.4f}**；含缓存的输入统一按普通输入价估算，未另计缓存折扣，不代表实际账单。",
              f"- DeepSeek按输入¥1.20/MTok、输出¥4.80/MTok，已知用量估算 **¥{totals['deepseek_estimated_cost_cny']:.4f}**。不另加缓存费用或推理子项。",
              f"- 两类模型已知费用合计约 **¥{totals['known_estimated_cost_cny']:.4f}**；另有{totals['deepseek_missing_usage_calls']}次失败调用无usage，实际费用无法补齐，因此不是完整账单。原先按缓存费理解价格的口径已更正，原始token记录未改。",
              "", "五维评分、逐条成本和相对HTML路径见 [CSV](分享汇总.csv) / [JSON](分享汇总.json)。原始调用用量见 [pilot_metrics.json](pilot_metrics.json)。",
              "更新此汇总：`python -m scripts.export_benchmark_summary`，只读取已有记录，不调用模型。"]
    (out/'分享汇总.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({"samples":len(rows),"totals":totals,"comparison":comparison},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
