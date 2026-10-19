"""Controlled original-image experiments, using only DeepSeek 261017 API calls.

python -m scripts.run_1019 --case ellipse --variant rules --rounds 1
Artifacts, configuration snapshots, API usage and rounds are stored in data_v2.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import re
from pathlib import Path
import time

from app.config import ROOT, get_settings
from app.model_api import ModelClient
from app.pipeline import Pipeline
from app.revision import apply_html_patch
from app.profiles import get_profile
from app.storage import Store, timestamp

MODEL = "deepseek-v4-1-flash-261017"
CASES = {"ellipse": "20261016_152052_373_1d9e2410", "parabola": "20261016_120248_689_ec5c112c",
         "conic": "20261016_131130_962_6e0df07e"}


class RecordedClient:
    def __init__(self, settings, folder: Path, generator_prompt: str, thinking: str, effort: str | None = None, module: bool = False, seed: dict | None = None, patch: bool = False):
        self.settings, self.folder = settings, folder
        self.generator_prompt, self.thinking = generator_prompt, thinking
        self.effort = effort
        self.module = module
        self.seed = seed
        self.patch = patch
        self.patch_prompt_path = folder / 'revision_patch.snapshot.md'
        if patch and not self.patch_prompt_path.exists():
            self.patch_prompt_path.write_text((ROOT/'app/prompts/html_revision_patch.md').read_text())
        self.shell_path = folder / "lesson_shell.snapshot.html"
        if module and not self.shell_path.exists():
            self.shell_path.write_text((ROOT / 'app/lesson_shell.html').read_text(encoding='utf-8'),encoding='utf-8')
        self.counts = {"generator": 0, "reviewer": 0}

    async def complete(self, model, prompt, images, **kwargs):
        # Pipeline calls are sequential: both roles receive the original image.
        role = "reviewer" if prompt.startswith("你是独立的学科教师") else "generator"
        if role == "generator" and self.seed and not list(self.folder.glob('*_round_*.html')):
            prompt += '\n\n上一配置的具体检查意见：\n'+self.seed['feedback']+'\n上一轮 HTML：\n'+self.seed['html']
        if role == "generator" and self.module and "上一轮 HTML：" in prompt:
            before, previous = prompt.split("上一轮 HTML：", 1)
            match = re.search(r"/\*__LESSON_START__\*/([\s\S]*?)/\*__LESSON_END__\*/", previous)
            if match:
                prompt = before + "上一轮教学模块（只修改模块，框架保持固定）：\n" + match.group(1)
        previous_html = None
        if role == "generator" and self.patch:
            if "上一轮 HTML：" not in prompt:
                raise ValueError("局部修订必须提供已有 HTML")
            local_feedback = self.folder / 'local_feedback.md'
            if local_feedback.exists():
                prompt = prompt.replace('上一轮 HTML：', '本地实际交互检查补充：\n'+local_feedback.read_text()+'\n上一轮 HTML：', 1)
            previous_html = prompt.split("上一轮 HTML：", 1)[1].strip()
            prompt += '\n\n' + self.patch_prompt_path.read_text()
            if self.seed and self.seed.get('feedback_image') and not list(self.folder.glob('*_round_*.html')):
                images = images + [(Path(self.seed['feedback_image']), 'image/png')]
        self.counts[role] += 1
        stem = f"request_{role}_{self.counts[role]:02d}_{timestamp()}"
        (self.folder / f"{stem}.prompt.txt").write_text(prompt, encoding="utf-8")
        (self.folder / f"{stem}.input.json").write_text(json.dumps({
            "model": model, "thinking": "enabled" if role == "reviewer" else self.thinking,
            "reasoning_effort": (self.effort or kwargs.get('reasoning_effort')) if role == "generator" else kwargs.get('reasoning_effort'),
            "images": [p.name for p, _ in images], "max_tokens": kwargs["max_tokens"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        client = ModelClient(replace(self.settings, deepseek_thinking="enabled" if role == "reviewer" else self.thinking))
        if role == "generator" and self.effort:
            kwargs["reasoning_effort"] = self.effort
        raw = await client.complete(model, prompt, images, **kwargs)
        (self.folder / f"{stem}.response.txt").write_text(raw, encoding="utf-8")
        if role == "generator" and self.patch:
            return apply_html_patch(previous_html, raw)
        if role == "generator" and self.module:
            code=raw.strip()
            fenced=re.search(r"```(?:javascript|js)?\s*([\s\S]*?)```",code,re.I)
            if fenced:code=fenced.group(1).strip()
            if "window.LESSON" not in code or "</script" in code.lower():
                raise ValueError("模型没有返回有效教学模块")
            shell=self.shell_path.read_text(encoding='utf-8')
            return shell.replace('/*__LESSON_MODULE__*/','/*__LESSON_START__*/\n'+code+'\n/*__LESSON_END__*/')
        return raw


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES, default="ellipse")
    parser.add_argument("--variant", choices=("original", "rules", "reasoned", "scaffold", "scaffold-low", "template", "template-v2", "template-v3", "patch"))
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--resume", help="Resume the saved task without regenerating pending HTML")
    parser.add_argument("--seed", help="Revise a saved candidate under a new configuration; provenance is recorded")
    parser.add_argument("--feedback-image", type=Path, help="Actual browser state screenshot for a seeded patch")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5:
        parser.error("rounds must be 1–5")
    if args.variant == 'patch' and not args.seed:
        parser.error('patch requires --seed')
    if args.feedback_image and (not args.seed or not args.feedback_image.is_file()):
        parser.error('feedback-image requires a seed and an existing image')
    settings = replace(get_settings(), generator_max_tokens=64000, reviewer_max_tokens=64000,
                       model_timeout_seconds=900)
    store = Store(settings)
    profile = get_profile("math-current")
    if args.resume:
        task = store.get(args.resume)
        if not task:
            parser.error("Task not found")
        if task["status"] in ("running", "queued"):
            parser.error("Task is already active")
        config = store.config(task["id"])
        if config.get("experiment") != "1019_direct_multimodal":
            parser.error("Only 1019 tasks may be resumed here")
        folder = settings.data_dir / task["id"]
        (folder / f"config_before_resume_{timestamp()}.json").write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
        config["max_rounds"] = max(args.rounds, len(task["rounds"]))
        config["generator_max_tokens"] = settings.generator_max_tokens
        config["reviewer_max_tokens"] = settings.reviewer_max_tokens
        (folder / task["config_name"]).write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
        with store._db() as db:
            db.execute("UPDATE tasks SET max_rounds=? WHERE id=?",(config["max_rounds"],task["id"]))
        print(f"RESUME {task['id']} total_rounds={config['max_rounds']}",flush=True)
        seed=json.loads((folder/'seed.json').read_text()) if (folder/'seed.json').exists() else None
        await Pipeline(settings,store,RecordedClient(settings,folder,config["generator_prompt"],config["generator_thinking"],config.get("generator_reasoning_effort"),config.get("output_mode")=="lesson_module",seed,config.get('variant')=='patch')).run(task["id"])
        result=store.get(task["id"])
        print(json.dumps({"id":task['id'],"status":result['status'],"message":result['message'],
              "rounds":[{"number":r['number'],"review":r['model_review']} for r in result['rounds']]},ensure_ascii=False,indent=2),flush=True)
        return
    if not args.variant:
        parser.error("--variant or --resume is required")
    prompt = profile.generator_prompt
    if args.variant == "original":
        prompt = (ROOT / "experiments/1019/original_generator.txt").read_text().strip()
    elif args.variant.startswith("scaffold") or args.variant == 'patch':
        extra = (ROOT / "app/prompts/math_deepseek.md").read_text().strip()
        prompt = prompt.replace("## 技术与输出要求", extra + "\n\n## 技术与输出要求")
    elif args.variant.startswith("template"):
        prompt="\n\n".join((ROOT/'app/prompts'/name).read_text().strip() for name in ('common_visual.md','math_current.md','math_lesson_module.md'))+'\n\n{{QUESTION}}'
    thinking = "disabled" if args.variant in ("original", "rules") else "enabled"
    effort = "low" if args.variant in ("scaffold-low", "patch") or args.variant.startswith("template") else None
    patch_prompt=(ROOT/'app/prompts/html_revision_patch.md').read_text() if args.variant=='patch' else ''
    config = {"profile_id": "math-current", "profile_name": f"1019 · {args.variant} · 原图直传",
              "profile_version": __import__('hashlib').sha256((prompt+profile.reviewer_prompt+patch_prompt).encode()).hexdigest()[:12],
              "subject": "math", "generator_prompt": prompt, "reviewer_prompt": profile.reviewer_prompt,
              "generator_provider": "codex", "generator_model": MODEL, "reviewer_provider": "codex",
              "reviewer_model": MODEL, "generator_mode": "vision", "reviewer_mode": "vision",
              "generation_strategy": "direct", "run_mode": "fixed_five", "max_rounds": args.rounds,
              "generator_max_tokens": settings.generator_max_tokens, "reviewer_max_tokens": settings.reviewer_max_tokens,
              "experiment": "1019_direct_multimodal", "case": args.case, "variant": args.variant,
              "generator_thinking": thinking, "generator_reasoning_effort": effort, "reviewer_thinking": "enabled",
              "output_mode": "lesson_module" if args.variant.startswith("template") else "html", "seed_task_id":args.seed}
    source = next((settings.data_dir / CASES[args.case]).glob("*_input.*"))
    task = store.create(f"1019_{args.case}_{args.variant}.png", "image/png", ".png", source.read_bytes(), run_config=config)
    folder = settings.data_dir / task["id"]
    seed=None
    if args.seed:
        parent=store.get(args.seed)
        if not parent or not parent['rounds']:
            raise ValueError('Seed task has no completed rounds')
        if (settings.data_dir/parent['id']/parent['image_name']).read_bytes()!=source.read_bytes():
            raise ValueError('Seed must use exactly the same input image')
        last=parent['rounds'][-1]
        seed={'task_id':parent['id'],'round':last['number'],
              'html':(settings.data_dir/parent['id']/last['html_name']).read_text(),
              'feedback':'\n'.join(last['issues'])+'\n当前框架已支持 inset.kind=plot 的横纵轴独立缩放；几何图用 kind=geometry。按实际桌面预览修复，保持正确数学部分。'}
        if args.variant == 'patch':
            seed['feedback']='\n'.join(last['issues'])+'\n只按本次实际桌面画面修复。'
            if args.case == 'ellipse':
                seed['feedback']+='取等状态中函数小图的当前值和最小值标签相互重叠，需合并或错开。'
            if args.case == 'parabola':
                seed['feedback']+='不要采纳放宽 r>2 的建议，这会违反原题；在条件转化讲清前暂用固定合法示例，把滑块延后到范围推导之后显示。'
        if args.feedback_image:
            feedback_path=folder/'feedback_state.png'
            feedback_path.write_bytes(args.feedback_image.read_bytes())
            seed['feedback_image']=str(feedback_path)
        (folder/'seed.json').write_text(json.dumps(seed,ensure_ascii=False,indent=2),encoding='utf-8')
    record_dir = settings.data_dir / "_1019"
    record_dir.mkdir(exist_ok=True)
    (record_dir / f"{task['id']}.json").write_text(json.dumps({"id":task['id'], "case":args.case,
        "variant":args.variant, "rounds":args.rounds}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"START {args.case} {args.variant} {task['id']}", flush=True)
    started = time.monotonic()
    await Pipeline(settings, store, RecordedClient(settings, folder, prompt, thinking, effort, args.variant.startswith("template"),seed,args.variant=='patch')).run(task["id"])
    result = store.get(task["id"])
    print(json.dumps({"id":task['id'], "status":result['status'], "seconds":round(time.monotonic()-started,2),
         "message":result['message'], "rounds":[{"number":r['number'],"review":r['model_review'],
                    "html":r['html_name'],"screenshot":r['screenshot_name']} for r in result['rounds']]},ensure_ascii=False,indent=2),flush=True)


if __name__ == "__main__":
    asyncio.run(main())
