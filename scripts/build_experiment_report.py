"""Build a self-contained experiment overview with links to saved local artifacts."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.storage import Store
from scripts.pilot_manifest import recover


EXPERIMENTS = (
    ("受控对照 V1", "_guided_pilot", "guided_math_pilot_v1"),
    ("执行提示跟进 V2", "_guided_followup_v2", "guided_math_followup_v2"),
    ("DeepSeek V4.1 跟进", "_guided_deepseek_v41", "guided_math_deepseek_v41"),
    ("DeepSeek 10.17 跟进", "_guided_deepseek_1017", "guided_math_deepseek_1017"),
)
CASE_NAMES = {"triangle": "直角三角形", "parabola": "抛物线与面积", "conic": "双曲线与椭圆"}
EFFECTS = {
    ("triangle", "qwen3.6-flash", "direct"): "答案 5 cm 正确；有图文联动，但三轮均未获审核通过。",
    ("triangle", "qwen3.6-flash", "guided_math"): "答案正确；初轮缺少文档声明，版面留白明显，规划未稳定改善页面。",
    ("triangle", "deepseek-v4-flash", "direct"): "答案正确且自动通过；装饰图占位过大，固定 3/4 的滑块易混淆原题条件。",
    ("triangle", "deepseek-v4-flash", "guided_math"): "几何比例和正方形绘制出错，最高自动分 2。",
    ("parabola", "qwen3.6-flash", "direct"): "首轮语法错误；后续漏最终范围或交互报错。",
    ("parabola", "qwen3.6-flash", "guided_math"): "规划把上界修正为 13/60；HTML 多轮仍有语法、审核格式或运行错误。",
    ("parabola", "deepseek-v4-flash", "direct"): "多轮把曲线方程或区间写错，未通过。",
    ("parabola", "deepseek-v4-flash", "guided_math"): "规划后生成三轮，最高审核分 4；仍需人工核对动态约束。",
    ("conic", "qwen3.6-flash", "direct"): "页面给出错误下界 3/2，审核误称正确。",
    ("conic", "qwen3.6-flash", "guided_math"): "三次规划核对未通过，未生成 HTML。",
    ("conic", "deepseek-v4-flash", "direct"): "末轮把向量积范围写为负到正的区间，审核误给 5 分。",
    ("conic", "deepseek-v4-flash", "guided_math"): "三次规划核对未通过，未生成 HTML。",
    ("triangle", "deepseek-v4-1-flash-260910", "direct"): "第 2 轮自动通过；图形和面积关系清楚，但部分标签重叠，滑块改变原题固定边长。",
    ("triangle", "deepseek-v4-1-flash-260910", "guided_math"): "第 2 轮自动通过；首屏主图更集中、3:4 比例准确；仍为英文且有改变固定边长的滑块。",
    ("parabola", "deepseek-v4-1-flash-260910", "direct"): "第 1 轮区间错误；第 2、3 轮写出正确区间 (1/6, 13/60)，自动审核仍未判通过。",
    ("parabola", "deepseek-v4-1-flash-260910", "guided_math"): "三轮均未判通过；第 2 轮审核 JSON 原始文本已修复归档，第 3 轮自动分 5。规划增加了 token 成本。",
    ("conic", "deepseek-v4-1-flash-260910", "guided_math"): "独立规划核对三次未通过，未生成 HTML；最后一次指出定点求解错误。",
    ("triangle", "deepseek-v4-1-flash-261017", "direct"): "前两轮按钮交互触发脚本错误；第 3 轮可用但双控制入口同步仍有疑点，最高 4 分。",
    ("triangle", "deepseek-v4-1-flash-261017", "guided_math"): "第 2、3 轮自动通过；中文分步主图清晰，原题边长默认锁定，拓展滑块需主动开启。",
    ("parabola", "deepseek-v4-1-flash-261017", "direct"): "三轮最高 2 分；末轮静态区间与交互计算互相矛盾。",
    ("conic", "deepseek-v4-1-flash-261017", "direct"): "三轮最高 4 分；仍有定点推导或最终范围缺失等问题。",
    ("conic", "deepseek-v4-1-flash-261017", "guided_math"): "规划审核通过，但漏掉区间内部不可取值 2 与 30/19；生成页面沿用错误区间，自动审核首轮未发现。",
    ("parabola", "deepseek-v4-1-flash-261017", "guided_math"): "三轮最高 5 分但均未获通过；第 2 轮的主图、局部示意和范围图联动，人工查看有明显教学价值。",
}


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def collect(store: Store) -> list[dict]:
    items = []
    for group, directory, experiment in EXPERIMENTS:
        manifest = recover(store, store.root / directory / "manifest.json", experiment)
        for key, task_id in manifest.items():
            task = store.get(task_id)
            if not task:
                continue
            case, model, strategy = key.split(":")
            rounds = task["rounds"]
            calls = task.get("api_calls", [])
            stage_seconds = {}
            for call in calls:
                role = call.get("role") or "other"
                stage_seconds[role] = round(stage_seconds.get(role, 0) + (call.get("duration_seconds") or 0), 1)
            scored = [(r["model_review"].get("overall_score"), r) for r in rounds
                      if isinstance(r.get("model_review"), dict)
                      and isinstance(r["model_review"].get("overall_score"), (int, float))]
            best = max(scored, key=lambda pair: (pair[0], pair[1]["number"]))[1] if scored else None
            items.append({
                "group": group, "case": case, "model": model, "strategy": strategy,
                "id": task_id, "status": task["status"], "rounds": rounds,
                "reviewer": task.get("reviewer_model") or "—",
                "plan_reviewer": (store.config(task_id) or {}).get("plan_reviewer_model") or "—",
                "best": best, "best_score": best["model_review"].get("overall_score") if best else None,
                "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
                "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
                "api_seconds": round(sum(c.get("duration_seconds") or 0 for c in calls), 1),
                "stage_seconds": stage_seconds,
                "effect": EFFECTS.get((case, model, strategy), "需人工复核数学结论与交互效果。"),
            })
    return sorted(items, key=lambda x: (x["group"], x["case"], x["model"], x["strategy"]))


def task_card(item: dict) -> str:
    task_id = item["id"]
    rounds = item["rounds"]
    best = item["best"]
    image = (f'<a class="preview" href="data_v2/{_e(task_id)}/{_e(best["html_name"])}" target="_blank">'
             f'<img loading="lazy" src="data_v2/{_e(task_id)}/{_e(best["screenshot_name"])}" '
             f'alt="最佳轮次截图"></a>') if best and best.get("screenshot_name") else '<div class="empty">尚无 HTML 截图</div>'
    round_links = " ".join(
        f'<a href="data_v2/{_e(task_id)}/{_e(r["html_name"])}" target="_blank">'
        f'第 {r["number"]} 轮'
        f'{" · " + str(r["model_review"].get("overall_score")) + "/5" if isinstance(r.get("model_review"), dict) and r["model_review"].get("overall_score") is not None else " · 未评分"}'
        f'{" ✓" if r.get("passed") else ""}</a>'
        for r in rounds if r.get("html_name")
    ) or "规划阶段未生成 HTML"
    score = "—" if item["best_score"] is None else f'{item["best_score"]}/5'
    strategy_name = "规划后生成" if item["strategy"] == "guided_math" else "直接生成"
    role_names = {"math_plan": "解题规划", "math_plan_review": "规划核对", "generator": "HTML 生成",
                  "page_description": "截图描述", "reviewer": "页面审核"}
    stage_text = " · ".join(f"{_e(role_names.get(role, role))} {_e(seconds)} 秒"
                            for role, seconds in item["stage_seconds"].items())
    return f"""
    <article class="card" data-case="{_e(item['case'])}" data-model="{_e(item['model'])}" data-strategy="{_e(item['strategy'])}">
      <div class="card-head"><div><span class="eyebrow">{_e(item['group'])} · {_e(CASE_NAMES[item['case']])}</span>
        <h3>{_e(item['model'])}</h3><p>{strategy_name} · {_e(item['status'])} · {len(rounds)} 轮<br>
        HTML 审核：{_e(item['reviewer'])}{' · 规划核对：' + _e(item['plan_reviewer']) if item['strategy'] == 'guided_math' else ''}</p></div>
        <strong class="score">{score}</strong></div>
      {image}
      <p class="effect">{_e(item['effect'])}</p>
      <div class="metrics"><span>输入 {_e(item['input_tokens']):} token</span><span>输出 {_e(item['output_tokens']):} token</span>
      <span>API {_e(item['api_seconds'])} 秒</span></div>
      <details class="stages"><summary>各环节 API 用时</summary><p>{stage_text or '暂无模型调用记录'}</p></details>
      <div class="links">{round_links}</div>
      <a class="history" href="http://localhost:8001/?task={_e(task_id)}" target="_blank">在 8001 历史中查看任务 →</a>
      <small class="id">{_e(task_id)}</small>
    </article>"""


def main() -> None:
    store = Store(get_settings())
    items = collect(store)
    cards = "\n".join(task_card(item) for item in items)
    total_rounds = sum(len(item["rounds"]) for item in items)
    models = sorted({item["model"] for item in items})
    model_options = "".join(f'<option value="{_e(model)}">{_e(model)}</option>' for model in models)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>题目可视化实验汇报</title>
<style>
:root{color-scheme:light;--ink:#142c39;--muted:#55707e;--line:#d8e5e8;--teal:#087d78;--bg:#f2f7f8}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1440px;margin:auto;padding:28px}header{background:linear-gradient(135deg,#073d4d,#0b7872);color:white;border-radius:24px;padding:32px 38px}
header h1{font-size:clamp(28px,4vw,48px);line-height:1.15;margin:8px 0}header p{max-width:850px;color:#ddf3ef}
.kicker{letter-spacing:.12em;text-transform:uppercase;font-size:12px;font-weight:800;color:#a9eee1}
.stats{display:flex;flex-wrap:wrap;gap:12px;margin-top:24px}.stats span{background:#ffffff20;border:1px solid #ffffff35;border-radius:12px;padding:8px 14px}
h2{font-size:25px;margin:34px 0 14px}h3{margin:2px 0;font-size:20px}.flow{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.flow section,.note,.card{background:white;border:1px solid var(--line);border-radius:18px;padding:20px}
.stack{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:14px 0}.stack span{background:#dcefee;border:1px solid #bddbd9;color:#075f5a;border-radius:9px;padding:7px 10px;font-size:14px;font-weight:700}.stack b{color:#6b8b95}
.flow ol{padding-left:23px;margin:9px 0}.flow li{padding:4px 0}.note{margin-top:16px;background:#fffaf0;border-color:#e8dcb6}
.filters{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 20px}select{background:white;border:1px solid #bdd3d8;border-radius:9px;padding:10px 12px;color:var(--ink)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}.card{min-width:0;display:flex;flex-direction:column}
.card-head{display:flex;justify-content:space-between;gap:12px}.card-head p{margin:0 0 10px;color:var(--muted)}.eyebrow{font-size:12px;color:var(--teal);font-weight:800}
.score{background:#ddf3ef;color:#086c65;border-radius:10px;padding:5px 8px;align-self:start;white-space:nowrap}
.preview{display:block;height:240px;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#eef4f5}
.preview img{width:100%;height:100%;object-fit:contain;object-position:top}.empty{height:240px;display:grid;place-items:center;color:var(--muted);background:#eef4f5;border-radius:12px}
.effect{min-height:72px}.metrics{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.metrics span{font-size:12px;background:#eaf1f3;border-radius:7px;padding:4px 6px}
.links{display:flex;flex-wrap:wrap;gap:8px}.links a,.history{color:var(--teal);font-weight:700}.history{margin-top:14px}.id{display:block;color:#7b939d;margin-top:8px;overflow-wrap:anywhere}
.stages{font-size:13px;color:var(--muted);margin-bottom:11px}.stages summary{cursor:pointer;color:#087d78;font-weight:700}.stages p{margin:5px 0}
footer{color:var(--muted);padding:30px 0}a:hover{text-decoration-thickness:2px}
@media(max-width:740px){.wrap{padding:12px}header{padding:24px}.flow{grid-template-columns:1fr}.cards{grid-template-columns:1fr}}
</style></head><body><div class="wrap"><header><div class="kicker">Visualize Agent · 实验汇报</div>
<h1>从题图到可交互解题页面</h1><p>同题比较直接生成与先规划后生成。每个案例保存逐轮 HTML、截图、模型审核、token 与用时，方便人工复核。</p>
<div class="stats"><span>__TASKS__ 个任务</span><span>__ROUNDS__ 轮 HTML</span><span>3 道题</span><span>更新：__NOW__</span></div></header>
<h2>架构与实验路径</h2><div class="stack"><span>浏览器上传 / 历史 / 人工评分</span><b>→</b><span>FastAPI 任务接口</span><b>→</b><span>Pipeline 编排</span><b>→</b><span>统一 ModelClient</span><b>→</b><span>Chrome 渲染与交互检查</span><b>→</b><span>SQLite 索引 + data_v2 文件</span></div><div class="flow">
<section><h3>共用前处理</h3><ol><li>上传题图并保存带时间戳的原图。</li><li>多模态模型分流学科；本次数学实验共用同一份题目 OCR 与图形描述。</li><li>加载固定的通用可视化要求、数学经验和输出格式要求。</li></ol></section>
<section><h3>直接生成</h3><ol><li>OCR 题意送给文本生成模型，生成完整 HTML。</li><li>本地检查源码，浏览器渲染并试用可见交互。</li><li>题意、截图描述和源码送审核模型评分并提修改意见；固定三轮。</li></ol></section>
<section><h3>规划后生成</h3><ol><li>同一生成模型先输出公开的解题与可视化规划 JSON。</li><li>独立文本模型核对规划，最多三次尝试；未通过则停止。</li><li>原版生成 Prompt 加核对过的规划，再进入相同三轮 HTML 流程。</li></ol></section>
<section><h3>存档与查看</h3><ol><li>每轮 HTML、截图、模型审核、人工评分和 API 阶段记录存入 data_v2。</li><li>历史页显示模型、配置、轮次及最佳轮；本页链接到所有轮次。</li><li>OCR 共用，不重复计入各任务的 token 与 API 用时。</li></ol></section></div>
<div class="note"><strong>评价口径：</strong>自动审核分可能误判数学结论或忽略页面问题；10.17 双曲线规划版虽被自动判为通过，最终值域仍漏掉内部不可取的 2 与 30/19。下面“效果观察”结合了人工核对。V2、V4.1 与 10.17 的规划提示比 V1 更具体，不能把跨批次差异只归因于模型。10.17 型号使用项目中的另一把网关密钥；两把密钥的可见模型目录不同。</div>
<h2>案例与逐轮文件</h2><div class="filters">
<select id="case"><option value="">全部题目</option><option value="triangle">直角三角形</option><option value="parabola">抛物线与面积</option><option value="conic">双曲线与椭圆</option></select>
<select id="model"><option value="">全部模型</option>__MODELS__</select>
<select id="strategy"><option value="">两种路径</option><option value="direct">直接生成</option><option value="guided_math">规划后生成</option></select></div>
<div class="cards" id="cards">__CARDS__</div>
<footer>用 <code>PYTHONPATH=. .venv/bin/python scripts/build_experiment_report.py</code> 重新生成本页。相对链接指向同仓库 data_v2；任务历史链接使用本机 8001 服务。</footer>
</div><script>
const filters=[...document.querySelectorAll(".filters select")];
function apply(){for(const card of document.querySelectorAll(".card")) card.hidden=filters.some(f=>f.value&&card.dataset[f.id]!==f.value)}
filters.forEach(f=>f.addEventListener("change",apply));apply();
</script></body></html>"""
    page = (page.replace("__TASKS__", str(len(items))).replace("__ROUNDS__", str(total_rounds))
            .replace("__NOW__", _e(now)).replace("__MODELS__", model_options).replace("__CARDS__", cards))
    output = Path(__file__).resolve().parents[1] / "实验汇报.html"
    output.write_text(page, encoding="utf-8")
    print(f"{output}: {len(items)} tasks, {total_rounds} rounds")


if __name__ == "__main__":
    main()
