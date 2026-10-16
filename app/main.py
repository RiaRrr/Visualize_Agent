"""Local-only API and web UI."""
from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image, ImageDraw, UnidentifiedImageError

from .config import ROOT, get_settings
from .model_api import ModelClient
from .model_catalog import ModelCatalog
from .pipeline import Pipeline
from .profiles import get_profile, list_profiles
from .storage import Store

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
    task = asyncio.create_task(pipeline.run(task_id))
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)


def _track(coro) -> None:
    task = asyncio.create_task(coro)
    running_tasks.add(task)
    task.add_done_callback(running_tasks.discard)


async def _run_batch(batch_id: str) -> None:
    for item in store.batch(batch_id):
        if item["status"] in ("queued", "interrupted"):
            if item["status"] == "interrupted":
                store.set_state(item["id"], "queued", item["current_round"], "批量任务恢复排队", stage="queued")
            if not item["current_round"]:
                failed = None
                for provider, model in ((item["generator_provider"], item["generator_model"]),
                                        (item["reviewer_provider"], item["reviewer_model"])):
                    status = catalog.status.get((provider, model))
                    if not status or time.time() - status.get("checked_at", 0) > 300:
                        status = await _probe_choice(provider, model)
                    store.log_event(item["id"], "queued", 0, f"短识图测试 {provider}/{model}：{status['message']}")
                    if status["state"] != "passed":
                        failed = f"模型短识图测试失败（{provider}/{model}）：{status['message']}"
                        break
                if failed:
                    store.set_state(item["id"], "error", 0, failed, stage="error")
                    continue
            await pipeline.run(item["id"])


class ProbeRequest(BaseModel):
    provider: str
    model: str


class HumanScores(BaseModel):
    problem_fidelity: int | None = Field(default=None, ge=1, le=5, strict=True)
    solution_correctness: int | None = Field(default=None, ge=1, le=5, strict=True)
    visualization_pedagogy: int | None = Field(default=None, ge=1, le=5, strict=True)
    page_interaction: int | None = Field(default=None, ge=1, le=5, strict=True)


class HumanAnnotation(BaseModel):
    scores: HumanScores
    passed: bool | None = Field(default=None, strict=True)
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
    return {"profiles": list_profiles(), **(await catalog.list(refresh=refresh))}


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
async def create_batch(image: UploadFile = File(...), rows: str = Form(...), run_mode: str = Form("single")):
    raw = await image.read(10_000_001)
    suffix, mime = _parse_image(raw)
    if run_mode not in ("single", "full"):
        raise HTTPException(400, "无效运行模式")
    try:
        selected = json.loads(rows)
    except json.JSONDecodeError:
        raise HTTPException(400, "配置行不是有效 JSON")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 12:
        raise HTTPException(400, "每批请选择 1 至 12 个配置")
    directory = await catalog.list()
    allowed = {(m["provider"], m["id"]) for m in directory["models"] if m["candidate"]}
    configs = []
    for index, row in enumerate(selected, 1):
        if not isinstance(row, dict):
            raise HTTPException(400, f"第 {index} 行配置无效")
        try:
            profile = get_profile(row["profile_id"])
            gen = (row["generator_provider"], row["generator_model"])
            rev = (row["reviewer_provider"], row["reviewer_model"])
        except (KeyError, ValueError):
            raise HTTPException(400, f"第 {index} 行配置无效")
        if gen not in allowed or rev not in allowed:
            raise HTTPException(400, f"第 {index} 行模型未列入当前可选目录")
        configs.append({"generator_provider": gen[0], "generator_model": gen[1],
                        "reviewer_provider": rev[0], "reviewer_model": rev[1],
                        "profile_id": profile.id, "profile_name": profile.name, "profile_version": profile.version,
                        "subject": profile.subject, "generator_prompt": profile.generator_prompt,
                        "reviewer_prompt": profile.reviewer_prompt, "run_mode": run_mode,
                        "max_rounds": 1 if run_mode == "single" else settings.max_rounds})
    batch_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    tasks = [store.create(Path(image.filename or "image").name[:200], mime, suffix, raw,
                          run_config=config, batch_id=batch_id) for config in configs]
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
    raw = await image.read(10_000_001)
    suffix, mime = _parse_image(raw)
    task = store.create(Path(image.filename or "image").name[:200], mime, suffix, raw)
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
