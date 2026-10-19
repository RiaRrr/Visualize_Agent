"""Local-only API and web UI."""
from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image, ImageDraw, UnidentifiedImageError

from .config import ROOT, get_settings
from .model_api import ModelClient
from .model_catalog import ModelCatalog
from .pipeline import Pipeline
from .profiles import get_profile, get_routed_profile, list_profiles, router_prompt
from .profiles import PROMPTS
from .router import classify_image
from .storage import Store, TIMEZONE

settings = get_settings()
store = Store(settings)
pipeline = Pipeline(settings, store)
catalog = ModelCatalog(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.mark_interrupted()
    yield


app = FastAPI(title="题目可视化", lifespan=lifespan)
running_tasks: set[asyncio.Task] = set()
model_check_cache: tuple[float, dict] | None = None


def _start_task(task_id: str) -> None:
    task = asyncio.create_task(_run_single(task_id))
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)


def _track(coro) -> None:
    task = asyncio.create_task(coro)
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)


async def _run_batch(batch_id: str) -> None:
    await _prepare_routes(store.batch(batch_id))
    for item in store.batch(batch_id):
        item = store.get(item["id"])
        if item["status"] in ("queued", "interrupted"):
            if item["status"] == "interrupted":
                store.set_state(item["id"], "queued", item["current_round"], "批量任务恢复排队", stage="queued")
            await pipeline.run(item["id"])


async def _run_single(task_id: str) -> None:
    await _prepare_routes([store.get(task_id)])
    task = store.get(task_id)
    if task and task["status"] in ("queued", "interrupted"):
        await pipeline.run(task_id)


async def _prepare_routes(tasks: list[dict]) -> None:
    pending = [task for task in tasks if task and task["profile_id"] == "auto"
               and not task.get("route_subject") and task["status"] in ("queued", "interrupted")]
    if not pending:
        return
    known = next((task["route_subject"] for task in tasks if task and task["profile_id"] == "auto"
                  and task.get("route_subject")), None)
    for task in pending:
        store.set_state(task["id"], "running", task["current_round"], "正在识别题目学科", stage="routing")
    try:
        first = pending[0]
        image_task = store.get(first["id"])
        if known:
            subject = known
        else:
            started = time.monotonic()
            route_usage = {}
            classified = False
            try:
                subject = await classify_image(
                    ModelClient(settings), first["router_provider"], first["router_model"],
                    store.root / first["id"] / image_task["image_name"], image_task["image_mime"],
                    prompt=(store.config(first["id"]) or {}).get("router_prompt"),
                    usage=lambda value: route_usage.update(value=value))
                classified = True
            finally:
                store.record_api_call(first["id"], 0, "router", first["router_provider"],
                                      first["router_model"], time.monotonic() - started,
                                      "completed" if classified else "error", route_usage.get("value"))
        for task in pending:
            config = store.config(task["id"]) or {}
            profile = get_routed_profile(subject, config.get("generator_mode", "vision"),
                                         config.get("reviewer_mode", "vision"))
            store.set_route(task["id"], subject, profile)
            store.set_state(task["id"], "queued", task["current_round"],
                            f"已识别为{profile.name}，等待生成", stage="queued")
    except Exception as exc:
        for task in pending:
            store.set_state(task["id"], "error", task["current_round"],
                            f"学科分流失败：{str(exc)[:300]}", stage="error")


class ProbeRequest(BaseModel):
    provider: str
    model: str


class HumanScores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    problem_fidelity: int | None = Field(default=None, ge=1, le=5, strict=True)
    solution_correctness: int | None = Field(default=None, ge=1, le=5, strict=True)
    page_quality: int | None = Field(default=None, ge=1, le=5, strict=True)
    interaction_quality: int | None = Field(default=None, ge=1, le=5, strict=True)
    teaching_effectiveness: int | None = Field(default=None, ge=1, le=5, strict=True)


class HumanAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scores: HumanScores
    overall_score: int | None = Field(default=None, ge=0, le=5, strict=True)
    comment: str = Field(default="", max_length=2000)


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/web/app.js")
def javascript():
    return Response((ROOT / "web" / "app.js").read_text(encoding="utf-8"), media_type="text/javascript")


@app.get("/web/style.css")
def stylesheet():
    return Response((ROOT / "web" / "style.css").read_text(encoding="utf-8"), media_type="text/css")


@app.get("/web/activity.css")
def activity_stylesheet():
    return Response((ROOT / "web" / "activity.css").read_text(encoding="utf-8"), media_type="text/css")


@app.get("/api/config")
def config():
    return {"generator_model": settings.generator_model, "reviewer_model": settings.reviewer_model,
            "data_directory": settings.data_dir.name,
            "generator_max_tokens": settings.generator_max_tokens,
            "max_rounds": settings.max_rounds,
            "key_configured": all(bool(settings.anthropic_key if model.startswith("claude-") else
                                       settings.codex_key if "-codex" in model else
                                       settings.openai_key if model.startswith(("gpt-", "o1", "o3", "o4")) else settings.api_key)
                                  for model in (settings.generator_model, settings.reviewer_model))}


@app.get("/api/models/check")
async def check_models(refresh: bool = False):
    global model_check_cache
    if model_check_cache and not refresh and time.monotonic() - model_check_cache[0] < 300:
        return model_check_cache[1]
    sample = settings.data_dir / f".model_check_{uuid.uuid4().hex}.png"
    picture = Image.new("RGB", (240, 80), "white")
    ImageDraw.Draw(picture).text((12, 30), "2 + 3 = ?", fill="black")
    picture.save(sample, format="PNG")
    client = ModelClient(settings)
    async def probe(model: str):
        try:
            answer = await client.complete(model, "读出图片里的算式。", [(sample, "image/png")], max_tokens=80)
            return {"ok": bool(answer.strip()), "message": "图片输入正常"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:150]}
    try:
        generator, reviewer = await asyncio.gather(probe(settings.generator_model), probe(settings.reviewer_model))
    finally:
        sample.unlink(missing_ok=True)
    result = {"generator": generator, "reviewer": reviewer}
    model_check_cache = (time.monotonic(), result)
    return result


@app.get("/api/options")
async def options(refresh: bool = False):
    return {"profiles": list_profiles(), "router_prompt": router_prompt(),
            "guided_math_prompts": {key: (PROMPTS / filename).read_text(encoding="utf-8") for key, filename in {
                "plan": "math_guided_plan.md", "plan_review": "math_guided_plan_review.md",
                "html_addendum": "math_guided_html.md"}.items()},
            **(await catalog.list(refresh=refresh))}


@app.post("/api/models/probe")
async def probe_model(request: ProbeRequest):
    directory = await catalog.list()
    if not any(x["provider"] == request.provider and x["id"] == request.model and x["candidate"] for x in directory["models"]):
        raise HTTPException(400, "模型未列入当前可选文本模型目录")
    return await _probe_choice(request.provider, request.model)


async def _probe_choice(provider: str, model: str) -> dict:
    sample = settings.data_dir / f".model_probe_{uuid.uuid4().hex}.png"
    picture = Image.new("RGB", (240, 80), "white")
    ImageDraw.Draw(picture).text((12, 30), "2 + 3 = ?", fill="black")
    picture.save(sample, format="PNG")
    started = time.monotonic()
    try:
        answer = await ModelClient(settings).complete(model, "读出图片中的算式，并给出结果。只返回结果。",
                                                     [(sample, "image/png")], max_tokens=100, provider=provider)
        ok, message = bool(answer.strip()), "短识图通过" if answer.strip() else "没有返回文字"
    except Exception as exc:
        ok, message = False, str(exc)[:200]
    finally:
        sample.unlink(missing_ok=True)
    return catalog.record(provider, model, ok, message, time.monotonic() - started)


def _parse_image(raw: bytes) -> tuple[str, str]:
    if len(raw) > 10_000_000:
        raise HTTPException(413, "图片不能超过 10MB")
    if not raw:
        raise HTTPException(400, "请选择图片")
    try:
        with Image.open(io.BytesIO(raw)) as parsed:
            parsed.verify()
        with Image.open(io.BytesIO(raw)) as parsed:
            if parsed.width * parsed.height > 40_000_000:
                raise HTTPException(400, "图片像素过大")
            image_format = parsed.format
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "只支持 PNG、JPEG、WebP 图片")
    format_map = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
    if image_format not in format_map:
        raise HTTPException(400, "只支持 PNG、JPEG、WebP 图片")
    return format_map[image_format]


@app.post("/api/batches", status_code=201)
async def create_batch(image: UploadFile = File(...), rows: str = Form(...), run_mode: str = Form("single"),
                       router_provider: str = Form(""), router_model: str = Form("")):
    upload_time = datetime.now(TIMEZONE)
    upload_started = time.monotonic()
    raw = await image.read(10_000_001)
    suffix, mime = _parse_image(raw)
    upload_seconds = time.monotonic() - upload_started
    if run_mode not in ("single", "full", "fixed_five"):
        raise HTTPException(400, "无效运行模式")
    try:
        selected = json.loads(rows)
    except json.JSONDecodeError:
        raise HTTPException(400, "配置行不是有效 JSON")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 12:
        raise HTTPException(400, "每批请选择 1 至 12 个配置")
    directory = await catalog.list()
    allowed = {(m["provider"], m["id"]) for m in directory["models"] if m["candidate"]}
    image_allowed = {(m["provider"], m["id"]) for m in directory["models"]
                     if m["candidate"] and m.get("capability", {}).get("image_input") is not False}
    tested_image = {(m["provider"], m["id"]) for m in directory["models"]
                    if m["candidate"] and m.get("capability", {}).get("image_input") is True}
    configs = []
    for index, row in enumerate(selected, 1):
        if not isinstance(row, dict):
            raise HTTPException(400, f"第 {index} 行配置无效")
        try:
            generator_mode = row.get("generator_mode", "vision")
            reviewer_mode = row.get("reviewer_mode", "vision")
            strategy = row.get("generation_strategy", "direct")
            if strategy not in ("direct", "guided_math"):
                raise ValueError("未知生成策略")
            profile = get_profile(row["profile_id"], generator_mode, reviewer_mode)
            gen = (row["generator_provider"], row["generator_model"])
            rev = (row["reviewer_provider"], row["reviewer_model"])
        except (KeyError, ValueError):
            raise HTTPException(400, f"第 {index} 行配置无效")
        if strategy == "guided_math" and (generator_mode != "ocr_text" or profile.subject != "math"):
            raise HTTPException(400, f"第 {index} 行数学规划模式需选择数学方案和 OCR＋文字生成")
        if strategy == "guided_math" and ("edumind_alt", "qwen3.6-35b-a3b") not in allowed:
            raise HTTPException(400, "数学规划核对模型暂不可用")
        if gen not in allowed or rev not in allowed:
            raise HTTPException(400, f"第 {index} 行模型未列入当前可选目录")
        if generator_mode == "vision" and gen not in image_allowed:
            raise HTTPException(400, f"第 {index} 行生成模型尚未通过识图测试")
        if reviewer_mode == "vision" and rev not in image_allowed:
            raise HTTPException(400, f"第 {index} 行审核模型尚未通过识图测试")
        ocr = (row.get("ocr_provider", ""), row.get("ocr_model", ""))
        if "ocr_text" in (generator_mode, reviewer_mode) and ocr not in tested_image:
            raise HTTPException(400, f"第 {index} 行识图模型尚未通过识图测试")
        configs.append({"generator_provider": gen[0], "generator_model": gen[1],
                        "generation_strategy": strategy,
                        "plan_reviewer_provider": "edumind_alt", "plan_reviewer_model": "qwen3.6-35b-a3b",
                        "reviewer_provider": rev[0], "reviewer_model": rev[1],
                        "generator_mode": generator_mode, "reviewer_mode": reviewer_mode,
                        "ocr_provider": ocr[0] if "ocr_text" in (generator_mode, reviewer_mode) else None,
                        "ocr_model": ocr[1] if "ocr_text" in (generator_mode, reviewer_mode) else None,
                        "profile_id": profile.id, "profile_name": profile.name, "profile_version": profile.version,
                        "subject": profile.subject, "generator_prompt": profile.generator_prompt,
                        "reviewer_prompt": profile.reviewer_prompt, "run_mode": run_mode,
                        "max_rounds": 1 if run_mode == "single" else 5 if run_mode == "fixed_five" else settings.max_rounds})
        if strategy == "guided_math":
            configs[-1].update({key: (PROMPTS / filename).read_text(encoding="utf-8") for key, filename in {
                "math_plan_prompt": "math_guided_plan.md", "math_plan_review_prompt": "math_guided_plan_review.md",
                "math_guided_html_prompt": "math_guided_html.md"}.items()})
    if any(config["profile_id"] == "auto" for config in configs):
        route = (router_provider, router_model) if router_provider and router_model else (
            configs[0]["reviewer_provider"], configs[0]["reviewer_model"]
        )
        if route not in image_allowed:
            raise HTTPException(400, "分流模型尚未通过识图测试")
        for config in configs:
            if config["profile_id"] == "auto":
                config["router_provider"], config["router_model"] = route
                config["router_prompt"] = router_prompt()
    batch_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tasks = [store.create(Path(image.filename or "image").name[:200], mime, suffix, raw,
                          run_config=config, batch_id=batch_id) for config in configs]
    for task in tasks:
        store.record_upload(task["id"], upload_time, upload_seconds)
    _track(_run_batch(batch_id))
    return {"id": batch_id, "tasks": tasks}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    tasks = store.batch(batch_id)
    if not tasks:
        raise HTTPException(404, "批次不存在")
    return {"id": batch_id, "tasks": tasks}


@app.post("/api/batches/{batch_id}/resume", status_code=202)
async def resume_batch(batch_id: str):
    tasks = store.batch(batch_id)
    if not tasks:
        raise HTTPException(404, "批次不存在")
    if not any(x["status"] == "interrupted" for x in tasks):
        raise HTTPException(409, "没有可恢复的任务")
    _track(_run_batch(batch_id))
    return {"id": batch_id, "tasks": store.batch(batch_id)}


@app.post("/api/tasks", status_code=201)
async def create_task(image: UploadFile = File(...)):
    upload_time = datetime.now(TIMEZONE)
    upload_started = time.monotonic()
    raw = await image.read(10_000_001)
    suffix, mime = _parse_image(raw)
    upload_seconds = time.monotonic() - upload_started
    profile = get_profile("auto")
    config = {"profile_id": profile.id, "profile_name": profile.name,
              "profile_version": profile.version, "subject": profile.subject,
              "generator_prompt": profile.generator_prompt, "reviewer_prompt": profile.reviewer_prompt,
              "generator_model": settings.generator_model, "reviewer_model": settings.reviewer_model,
              "router_model": settings.reviewer_model, "router_prompt": router_prompt(),
              "run_mode": "full", "max_rounds": settings.max_rounds}
    task = store.create(Path(image.filename or "image").name[:200], mime, suffix, raw, run_config=config)
    store.record_upload(task["id"], upload_time, upload_seconds)
    _start_task(task["id"])
    return task


@app.get("/api/tasks")
def list_tasks():
    return store.list()


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.put("/api/tasks/{task_id}/rounds/{number}/annotation")
def annotate_round(task_id: str, number: int, annotation: HumanAnnotation):
    try:
        return store.update_annotation(task_id, number, annotation.model_dump())
    except LookupError:
        raise HTTPException(404, "轮次不存在")


@app.post("/api/tasks/{task_id}/resume", status_code=202)
async def resume_task(task_id: str):
    task = store.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] not in ("interrupted", "error"):
        raise HTTPException(409, "只有已中断或出错的任务可以继续")
    if len(task["rounds"]) >= (task["max_rounds"] or settings.max_rounds) and not task["rounds"][-1]["passed"]:
        raise HTTPException(409, "已达到最大迭代轮次")
    store.set_state(task_id, "queued", len(task["rounds"]), "任务已重新排队，将从上次完成的轮次继续", stage="queued")
    _start_task(task_id)
    return store.get(task_id)


@app.get("/api/tasks/{task_id}/files/{filename}")
def get_file(task_id: str, filename: str, download: bool = False):
    path = store.artifact(task_id, filename)
    if not path:
        raise HTTPException(404, "文件不存在")
    suffix = path.suffix.lower()
    mime = {".html": "text/html", ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp", ".json": "application/json"}.get(suffix, "application/octet-stream")
    headers = {"Content-Security-Policy": "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'"} if suffix == ".html" else {}
    return FileResponse(path, media_type=mime, filename=filename if download else None, headers=headers)
