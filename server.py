"""
有声书工坊 · Web 服务（FastAPI）
==============================
把 `scripts/studio.py` 的服务层原样暴露成 HTTP 接口，前端用原生 HTML/CSS/JS
实现 iOS 风格交互（Sheet / ActionSheet / 分段控件 / 气泡）。

启动：
    .venv/bin/python server.py                # → http://127.0.0.1:7860
    PORT=8000 .venv/bin/python server.py

设计取舍：
  · 默认只绑 127.0.0.1（无登录，别裸奔到公网）
  · 任务控制沿用 studio 的全局单例 Job：前端轮询 /api/job，比 SSE 简单且够用
  · 所有写操作都走 studio 里已有的函数，这里只做参数校验与序列化
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import uuid

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import studio, translate          # noqa: E402

WEB_DIR = os.path.join(ROOT, "web")
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "audiobook_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="有声书工坊", docs_url="/api/docs")

# 预览音频：token → 文件路径（进程内，重启即失效）
_PREVIEW: dict[str, str] = {}


# ─────────────────────────────────────────────────────
# 基础状态
# ─────────────────────────────────────────────────────
@app.get("/api/bootstrap")
def bootstrap():
    chapters = studio.list_chapters()
    vm = studio.load_voice_map() or {}
    lib = studio.library_load()
    outputs = studio.list_outputs()
    s = studio.summary()
    return {
        "summary": s,
        "chapters": [
            {"file": fn, "title": os.path.splitext(fn)[0],
             "chars": _chapter_chars(fn), "translated": os.path.exists(translate.translated_path(fn))}
            for fn in chapters
        ],
        "cast": studio.voice_map_rows(vm) if vm else [],
        "outputs": outputs,
        "library": sorted(lib),
        "settings": {
            "engine": studio.engine_state()[0],
            "interval": studio.engine_state()[1],
            "workers": studio.gen._WORKERS,
        },
        "llm": {
            "ready": studio.llm_status()[0],
            "note": studio.llm_status()[1],
            "config": {k: ("" if k == "api_key" else v) for k, v in studio.llm_config().items()},
            "has_key": bool(studio.llm_config().get("api_key")),
            "presets": {k: v for k, v in studio.llm.PRESETS.items()},
        },
    }


def _chapter_chars(fn: str) -> int:
    try:
        return len(studio.read_text(os.path.join(studio.SAMPLES_DIR, fn)))
    except Exception:                              # noqa: BLE001
        return 0


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".txt"):
        raise HTTPException(400, "只支持 .txt")
    dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}.txt")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        text = studio.read_text(dest)
    except Exception as e:                         # noqa: BLE001
        raise HTTPException(400, f"读不出来：{e}") from e
    return {
        "path": dest,
        "name": file.filename,
        "chars": len(text),
        "size": os.path.getsize(dest),
        "preview": text[:200],
    }


class SplitReq(BaseModel):
    path: str
    pattern: str | None = None
    max_chars: int = 8000
    write: bool = False


@app.post("/api/split")
def split(req: SplitReq):
    if not os.path.exists(req.path):
        raise HTTPException(400, "文件不存在，请重新上传")
    try:
        rows = studio.preview_chapters(req.path, req.pattern, req.max_chars)
    except Exception as e:                         # noqa: BLE001
        raise HTTPException(400, f"切章失败：{e}") from e
    out = {"chapters": rows, "written": False, "removed": []}
    if req.write:
        res = studio.write_chapters(req.path, req.pattern, req.max_chars)
        out.update({"written": True, "count": res["count"], "removed": res["removed"]})
        out["chapters"] = studio.preview_rows(req.path, req.pattern, req.max_chars)
    return out


# ─────────────────────────────────────────────────────
# 角色
# ─────────────────────────────────────────────────────
class AnalyzeReq(BaseModel):
    path: str
    mode: str = "rule"          # rule | llm


@app.post("/api/cast/analyze")
def analyze(req: AnalyzeReq):
    path = req.path
    if not path:                       # 没上传过就用书架里的第一章
        chapters = studio.list_chapters()
        if not chapters:
            raise HTTPException(400, "先导入小说或切好章节")
        path = os.path.join(studio.SAMPLES_DIR, chapters[0])
    if not os.path.exists(path):
        raise HTTPException(400, "文件不存在，请重新上传")
    vm, note = studio.analyze_source_ex(path, req.mode == "llm")
    return {"cast": studio.voice_map_rows(vm), "note": note}


class CastReq(BaseModel):
    rows: list


@app.put("/api/cast")
def save_cast(req: CastReq):
    vm = studio.rows_to_voice_map(req.rows)
    if not vm:
        raise HTTPException(400, "角色表是空的")
    studio.save_voice_map(vm)
    return {"ok": True, "count": len(vm)}


class MergeReq(BaseModel):
    roles: list[str]


@app.post("/api/cast/merge")
def merge_roles(req: MergeReq):
    """把剧本里有、配音表里没有的角色补进来"""
    vm = studio.load_voice_map() or {}
    before = len(vm)
    studio.add_missing_roles(vm, req.roles)
    if len(vm) > before:
        studio.save_voice_map(vm)
    return {"added": len(vm) - before, "cast": studio.voice_map_rows(vm)}


@app.post("/api/cast/restyle")
def restyle(req: CastReq):
    vm = studio.rows_to_voice_map(req.rows)
    studio.restyle_voice_map(vm)
    return {"cast": studio.voice_map_rows(vm)}


@app.post("/api/cast/order")
def order(payload: dict):
    names = payload.get("names") or []
    rows = payload.get("rows") or []
    index = {str(r[0]).strip(): r for r in rows if r and str(r[0]).strip()}
    ordered = [index.pop(n) for n in names if n in index]
    ordered += [r for r in rows if str(r[0]).strip() in index]
    return {"cast": ordered}


@app.post("/api/cast/library/save")
def library_save(req: CastReq):
    n = studio.library_upsert(studio.rows_to_voice_map(req.rows))
    return {"saved": n, "library": sorted(studio.library_load())}


@app.post("/api/cast/library/apply")
def library_apply(payload: dict):
    names = payload.get("names") or []
    rows = payload.get("rows") or []
    vm = studio.rows_to_voice_map(rows)
    vm.update(studio.library_apply(names))
    return {"cast": studio.voice_map_rows(vm)}


@app.post("/api/cast/library/delete")
def library_delete(payload: dict):
    n = studio.library_delete(payload.get("names") or [])
    return {"removed": n, "library": sorted(studio.library_load())}


# ─────────────────────────────────────────────────────
# 剧本 / 转译
# ─────────────────────────────────────────────────────
@app.get("/api/script")
def script(chapter: str, limit: int = Query(800, le=3000)):
    vm = studio.load_voice_map() or {}
    if not vm:
        raise HTTPException(400, "还没有角色表")
    path = _chapter_path(chapter)
    preview = studio.script_preview(path, vm, max_rows=limit)
    preview["chapter"] = chapter
    preview["source"] = "translated" if os.path.exists(translate.translated_path(chapter)) else "raw"
    return preview


def _chapter_path(chapter: str) -> str:
    tp = translate.translated_path(chapter)
    if os.path.exists(tp):
        return tp
    p = os.path.join(studio.SAMPLES_DIR, chapter)
    if not os.path.exists(p):
        raise HTTPException(404, "章节不存在")
    return p


@app.get("/api/audit")
def audit(chapter: str):
    try:
        return studio.audit_chapter(chapter)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


class SkipReq(BaseModel):
    chapter: str
    indexes: list[int]


@app.post("/api/script/skip")
def skip_lines(req: SkipReq):
    try:
        return studio.skip_lines(req.chapter, req.indexes)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e)) from e


class TranslateReq(BaseModel):
    chapters: list[str]
    mode: str = "llm"


@app.post("/api/translate")
def start_translate(req: TranslateReq):
    vm = studio.load_voice_map() or {}
    if not vm:
        raise HTTPException(400, "先识别人物")
    try:
        studio.start_translate_job(req.chapters, vm, req.mode)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


class LineEditReq(BaseModel):
    chapter: str
    index: int              # 1 起（与剧本面板一致）
    action: str             # role | skip | restore
    value: str | None = None


@app.post("/api/script/edit")
def edit_line(req: LineEditReq):
    """改归属 / 设为不朗读 —— 直接改写 translated/ 下的脚本行"""
    path = translate.translated_path(req.chapter)
    if not os.path.exists(path):
        raise HTTPException(400, "这一章还没有转译稿，先转译再改")
    lines = [ln for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
    if not (1 <= req.index <= len(lines)):
        raise HTTPException(400, "行号越界")
    idx = req.index - 1
    old = lines[idx]

    if req.action == "skip":
        removed = lines.pop(idx)
        translate.save_translated(req.chapter, lines)
        return {"ok": True, "removed": removed}
    if req.action == "restore":
        if not req.value:
            raise HTTPException(400, "缺少要恢复的内容")
        lines.insert(idx, req.value)
        translate.save_translated(req.chapter, lines)
        return {"ok": True}
    if req.action == "role":
        if not req.value:
            raise HTTPException(400, "缺少角色名")
        _, _, body = old.partition(translate.DELIM)
        lines[idx] = f"{req.value}{translate.DELIM}{body}"
        translate.save_translated(req.chapter, lines)
        return {"ok": True, "old": old}
    raise HTTPException(400, "不认识的操作")


# ─────────────────────────────────────────────────────
# 任务（合成 / 转译共用）
# ─────────────────────────────────────────────────────
class BatchReq(BaseModel):
    chapters: list[str]
    force: bool = False
    use_translated: bool = True


@app.post("/api/job/batch")
def start_batch(req: BatchReq):
    vm = studio.load_voice_map() or {}
    if not vm:
        raise HTTPException(400, "先识别人物")
    try:
        studio.start_job(studio.chapter_tasks(req.chapters, req.use_translated),
                         vm, req.force, kind="batch")
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.post("/api/job/{action}")
def job_action(action: str):
    job = studio.current_job()
    if job is None:
        raise HTTPException(404, "没有任务")
    if action == "pause":
        return {"msg": job.toggle_pause()}
    if action == "stop":
        return {"msg": job.stop()}
    if action == "skip":
        return {"msg": job.skip_current()}
    raise HTTPException(400, "不认识的操作")


@app.get("/api/job")
def job_state():
    job = studio.current_job()
    if job is None:
        return {"job": None}
    snap = job.snapshot()
    snap.pop("timings", None)          # 时间轴体积大，剧本页不需要
    return {"job": snap}


# ─────────────────────────────────────────────────────
# 试听 / 音频
# ─────────────────────────────────────────────────────
class PreviewReq(BaseModel):
    text: str = "你怎么又淋成这样？外面雨这么大。"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"


class LineAudioReq(BaseModel):
    chapter: str
    index: int                 # 1 起
    voice: str | None = None


@app.post("/api/line/audio")
def line_audio(req: LineAudioReq):
    """单句重合成：只做这一句，用来校验改完的归属对不对"""
    path = translate.translated_path(req.chapter)
    if not os.path.exists(path):
        raise HTTPException(400, "这一章还没有转译稿")
    lines = [ln for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
    if not (1 <= req.index <= len(lines)):
        raise HTTPException(400, "行号越界")
    role, _, text = lines[req.index - 1].partition(translate.DELIM)
    role, text = role.strip(), text.strip()
    if not text:
        raise HTTPException(400, "这一行没有可朗读的正文")

    vm = studio.load_voice_map() or {}
    cfg = vm.get(role) or vm.get("旁白") or {}
    voice = req.voice or cfg.get("voice") or "zh-CN-XiaoxiaoNeural"
    rate, pitch = cfg.get("rate", "+0%"), cfg.get("pitch", "+0Hz")
    try:
        audio = studio.preview_voice(text, voice, rate, pitch)
    except Exception as e:                         # noqa: BLE001
        raise HTTPException(500, str(e)) from e
    token = uuid.uuid4().hex
    _PREVIEW[token] = audio
    from scripts.audiobook_generator import mp3_duration_ms
    return {"url": f"/api/audio/preview/{token}", "role": role, "text": text,
            "voice": voice, "ms": mp3_duration_ms(audio)}


class ExportReq(BaseModel):
    chapters: list[str]
    title: str = "有声书"
    author: str = ""


@app.post("/api/export")
def export_m4b(req: ExportReq):
    try:
        studio.start_export_job(req.chapters, req.title, req.author)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.get("/api/export/plan")
def export_plan(chapters: str = ""):
    """预览能导出几章（只统计已出片的）"""
    names = [c for c in chapters.split("|") if c] or studio.list_chapters()
    plan = studio.export_plan(names)
    return {"count": len(plan), "minutes": round(sum(p[2] for p in plan) / 60000, 1),
            "names": [p[0] for p in plan]}


@app.get("/api/pronounce")
def get_pronounce():
    return {"items": studio.pronounce_rows()}


@app.post("/api/pronounce")
def set_pronounce(payload: dict):
    items = payload.get("items") or {}
    studio.save_pronounce(items)
    return {"items": studio.pronounce_rows()}


@app.get("/api/diagnose")
def diagnose(deep: bool = False):
    from scripts import doctor
    return {"items": doctor.run_checks(include_network=deep)}


@app.post("/api/preview")
def preview(req: PreviewReq):
    try:
        path = studio.preview_voice(req.text, req.voice, req.rate, req.pitch)
    except Exception as e:                         # noqa: BLE001
        raise HTTPException(500, str(e)) from e
    token = uuid.uuid4().hex
    _PREVIEW[token] = path
    return {"url": f"/api/audio/preview/{token}"}


@app.get("/api/audio/preview/{token}")
def audio_preview(token: str):
    path = _PREVIEW.get(token)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "试听文件已失效")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/audio/output/{name}")
def audio_output(name: str):
    if "/" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    path = os.path.join(studio.OUTPUT_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
    media = "audio/mp4" if name.lower().endswith(".m4b") else "audio/mpeg"
    return FileResponse(path, media_type=media)


# ─────────────────────────────────────────────────────
# 设置 / 输出
# ─────────────────────────────────────────────────────
class SettingsReq(BaseModel):
    engine: str | None = None
    interval: float | None = None
    workers: int | None = None


@app.post("/api/settings")
def save_settings(req: SettingsReq):
    e, i = studio.set_engine(req.engine or studio.engine_state()[0],
                             req.interval, req.workers)
    return {"engine": e, "interval": i, "workers": studio.gen._WORKERS}


class LLMReq(BaseModel):
    preset: str = "tencent"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    enabled: bool = True


@app.post("/api/llm")
def save_llm(req: LLMReq):
    cfg = studio.llm_config()
    cfg.update({"preset": req.preset, "base_url": req.base_url.strip(),
                "model": req.model.strip(), "enabled": req.enabled})
    if req.api_key:
        cfg["api_key"] = req.api_key.strip()
    studio.llm_save(cfg)
    ok, why = studio.llm_status(cfg)
    return {"ready": ok, "note": why}


@app.post("/api/llm/test")
def test_llm(req: LLMReq):
    cfg = studio.llm_config()
    cfg.update({"preset": req.preset, "base_url": req.base_url.strip(),
                "model": req.model.strip()})
    if req.api_key:
        cfg["api_key"] = req.api_key.strip()
    try:
        return {"ok": True, "reply": studio.llm_test(cfg)[:80]}
    except Exception as e:                         # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=200)


@app.get("/api/voices")
def voices():
    """音色清单：前端不写死，新增音色只改后端"""
    from scripts.analyze_characters import (EXTRA_VOICES, FEMALE_VOICES, MALE_VOICES,
                                            NARRATOR_VOICE)
    items = []
    for v, gender in ([(v[0], "female") for v in FEMALE_VOICES]
                      + [(v[0], "male") for v in MALE_VOICES]
                      + [(k, g) for k, g in EXTRA_VOICES.items()]):
        items.append({"id": v, "gender": gender,
                      "label": v.replace("zh-CN-", "").replace("zh-HK-", "HK·")
                                 .replace("zh-TW-", "TW·").replace("Neural", "")})
    items.insert(0, {"id": NARRATOR_VOICE[0], "gender": "female", "label": "Xiaoxiao（旁白默认）"})
    return {"voices": items}


@app.get("/api/outputs")
def outputs():
    return {"outputs": studio.list_outputs()}


@app.delete("/api/outputs/{name}")
def delete_output(name: str):
    if "/" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    path = os.path.join(studio.OUTPUT_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
    os.remove(path)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "time": time.time()}


# ─────────────────────────────────────────────────────
# 静态前端
# ─────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    studio.ensure_dirs()
    studio.apply_pronounce()
    print(f"\n  有声书工坊 · iOS 界面")
    print(f"  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
