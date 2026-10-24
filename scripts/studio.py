"""网站服务层（studio）
====================
把「切章 / 配音表 / 批量合成 / 产物管理」从界面里拆出来，供网页与测试共用。

设计约束：
  · **不 import gradio** —— 可以脱离界面单独运行、单独测试
  · 脏输入容错：NaN、空串、GBK 编码、只写 "Yunxi" 的声音名、缺列的表格
  · 不静默丢数据：跳过 / 清理的动作都返回明细，由界面原样展示给用户

网页（app.py）只负责把这里的返回值渲染出来，不再自己拼业务逻辑。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Iterator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:                     # 允许 `python scripts/studio.py` 直接跑
    sys.path.insert(0, ROOT)

from scripts import audiobook_generator as gen                      # noqa: E402
from scripts import llm_analyze as llm                              # noqa: E402
from scripts import translate                                       # noqa: E402
from scripts.analyze_characters import (                            # noqa: E402
    AGE_DISPLAY, AGE_REVERSE, AGE_STYLE, EMOTION_DISPLAY, EMOTION_REVERSE,
    EMOTION_STYLE, EXTRA_VOICES, FEMALE_VOICES, MALE_VOICES, NARRATOR_VOICE,
    _EXTRA_FEMALE, _EXTRA_MALE, analyze, voice_map_from_roster,
)
from scripts.parse_script import parse_novel_to_script              # noqa: E402
from scripts.split import (                                         # noqa: E402
    DEFAULT_PATTERN, read_text, safe_name, split_chapters,
)

SAMPLES_DIR = os.path.join(ROOT, "samples")
OUTPUT_DIR = os.path.join(ROOT, "output")
CONFIG_DIR = os.path.join(ROOT, "config")
VOICE_MAP_PATH = os.path.join(CONFIG_DIR, "voice_map.json")
LIBRARY_PATH = os.path.join(CONFIG_DIR, "character_library.json")
PRONOUNCE_PATH = os.path.join(CONFIG_DIR, "pronounce.json")

# 只认 split.py 生成的 3 位序号文件，避免误删用户自己放进 samples/ 的东西
CHAPTER_FILE_RE = re.compile(r"^\d{3}_.*\.txt$")

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

GENDER_DISPLAY = {"female": "女", "male": "男", "unknown": "未知"}
ROLE_DISPLAY = {"narrator": "旁白", "protagonist": "主角", "supporting": "配角"}
GENDER_REVERSE = {v: k for k, v in GENDER_DISPLAY.items()}
ROLE_REVERSE = {v: k for k, v in ROLE_DISPLAY.items()}

VOICE_TABLE_HEADERS = ["角色", "性别", "年龄", "情绪", "定位", "声音", "语速", "音调", "频次", "别名"]
CHAPTER_TABLE_HEADERS = ["序号", "章节", "字数", "文件名", "状态"]


# ─────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────
def ensure_dirs() -> None:
    for d in (SAMPLES_DIR, OUTPUT_DIR, CONFIG_DIR):
        os.makedirs(d, exist_ok=True)


def is_blank(v) -> bool:
    """NaN / None / 空串 都算空（pandas 表格里全是这三种）"""
    if v is None:
        return True
    if isinstance(v, float) and v != v:          # NaN
        return True
    return str(v).strip() in ("", "nan", "NaN", "None", "<NA>")


def as_int(v, default: int = 0) -> int:
    if is_blank(v):
        return default
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v))
    return int(m.group()) if m else default


def as_str(v, default: str = "") -> str:
    return default if is_blank(v) else str(v).strip()


def _voice_index() -> dict:
    """声音别名 → 标准名。支持 YunxiNeural / Yunxi / zh-CN-Yunxi 三种写法"""
    full_names = (
        [v[0] for v in FEMALE_VOICES]
        + [v[0] for v in MALE_VOICES]
        + [NARRATOR_VOICE[0]]
        + list(EXTRA_VOICES)
    )
    idx: dict[str, str] = {}
    for full in full_names:
        low = full.lower()
        idx[low] = full
        tail = low.split("-")[-1]                 # yunxineural
        idx.setdefault(tail, full)
        idx.setdefault(tail.replace("neural", ""), full)   # yunxi
    return idx


def resolve_voice(name) -> str:
    """把用户写的任意声音别名规范成 edge-tts 全名；认不出就回退默认音色"""
    raw = as_str(name)
    if not raw:
        return DEFAULT_VOICE
    idx = _voice_index()
    key = raw.lower().replace(" ", "")
    if key in idx:
        return idx[key]
    if key.startswith("zh-"):                     # 已经是全名，只是不在内置池里
        return raw
    return "zh-CN-" + raw


def normalize_text_file(path: str) -> str:
    """返回一个 UTF-8 文本路径：原文件就是 UTF-8 时原样返回，否则转码到临时文件

    上传的 txt 常是 GBK，而生成器按 UTF-8 读，不转就会直接抛 UnicodeDecodeError。
    """
    text = read_text(path)                        # 内部已做编码探测
    try:
        with open(path, "rb") as f:
            f.read().decode("utf-8")
        return path
    except UnicodeDecodeError:
        tmp = os.path.join(tempfile.mkdtemp(prefix="novel_"), "utf8.txt")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        return tmp


# ─────────────────────────────────────────────────────
# 切章
# ─────────────────────────────────────────────────────
def preview_chapters(path: str, pattern: str | None = None, max_chars: int = 8000) -> list[dict]:
    """按章节标题切分（不落盘），返回 [{序号, 章节, 字数, 文件名}]"""
    text = read_text(path)
    chapters = split_chapters(text, pattern or DEFAULT_PATTERN, int(max_chars))
    return [
        {"序号": i, "章节": title, "字数": len(body), "文件名": f"{i:03d}_{safe_name(title)}.txt"}
        for i, (title, body) in enumerate(chapters, 1)
    ]


def preview_rows(path: str, pattern: str | None = None, max_chars: int = 8000) -> list[list]:
    """表格行，列与 CHAPTER_TABLE_HEADERS 对齐（状态列：预览）"""
    return [[r["序号"], r["章节"], r["字数"], r["文件名"], "预览"]
            for r in preview_chapters(path, pattern, max_chars)]


def write_chapters(path: str, pattern: str | None = None, max_chars: int = 8000) -> dict:
    """切章并写入 samples/。旧章节文件移到 samples/.stale/（不删除）"""
    rows = preview_chapters(path, pattern, max_chars)
    if not rows:
        return {"count": 0, "removed": [], "total_chars": 0}

    ensure_dirs()
    keep = set()
    total_chars = 0
    text = read_text(path)
    bodies = split_chapters(text, pattern or DEFAULT_PATTERN, int(max_chars))

    for row, (_, body) in zip(rows, bodies):
        fn = row["文件名"]
        with open(os.path.join(SAMPLES_DIR, fn), "w", encoding="utf-8") as f:
            f.write(body)
        keep.add(fn)
        total_chars += len(body)

    # 新目录里没有的旧章节：挪进 .stale，随时可捞回来
    stale_dir = os.path.join(SAMPLES_DIR, ".stale")
    removed: list[str] = []
    for fn in sorted(os.listdir(SAMPLES_DIR)):
        if CHAPTER_FILE_RE.match(fn) and fn not in keep:
            os.makedirs(stale_dir, exist_ok=True)
            shutil.move(os.path.join(SAMPLES_DIR, fn), os.path.join(stale_dir, fn))
            removed.append(fn)

    return {"count": len(rows), "removed": removed, "total_chars": total_chars}


def list_chapters() -> list[str]:
    if not os.path.isdir(SAMPLES_DIR):
        return []
    return sorted(f for f in os.listdir(SAMPLES_DIR) if CHAPTER_FILE_RE.match(f))


def chapter_stats() -> list[list]:
    """samples/ 现状，列与 CHAPTER_TABLE_HEADERS 对齐"""
    rows = []
    for i, fn in enumerate(list_chapters(), 1):
        try:
            chars = len(read_text(os.path.join(SAMPLES_DIR, fn)))
        except Exception:                          # noqa: BLE001
            chars = 0
        done = os.path.exists(os.path.join(OUTPUT_DIR, os.path.splitext(fn)[0] + ".mp3"))
        rows.append([i, os.path.splitext(fn)[0], chars, fn, "✅ 已生成" if done else "—"])
    return rows


# ─────────────────────────────────────────────────────
# 角色配音表
# ─────────────────────────────────────────────────────
def analyze_source(path: str) -> dict:
    """分析角色 → voice_map（先做编码归一化，GBK 也不会炸）"""
    return analyze(normalize_text_file(path))


def analyze_source_ex(path: str, use_llm: bool = False) -> tuple[dict, str]:
    """带大模型的人物识别：返回 (voice_map, 给人看的一句话说明)

    大模型只负责「人物表」（名字/性别/定位/别名），逐句归属仍走本地规则；
    没配 key、调用失败都自动回退本地规则，不会卡住流程。
    """
    src = normalize_text_file(path)
    if not use_llm:
        return analyze(src), "✅ 本地规则识别完成（未启用大模型）"

    ok, why = llm.ready()
    if not ok:
        return analyze(src), f"⚠️ {why}，已回退本地规则"

    try:
        text = read_text(src)
        roster = llm.analyze_characters(text)
        vm = voice_map_from_roster(roster, text)
        n = len([k for k in vm if k != "旁白"])
        return vm, f"✅ 大模型识别到 {n} 个角色 · {why}"
    except Exception as e:                          # noqa: BLE001
        return analyze(src), f"⚠️ 大模型调用失败（{str(e)[:80]}），已回退本地规则"


def voice_map_rows(vm: dict) -> list[list]:
    """voice_map → 表格行；旁白置顶，其余按出现频次降序"""
    rows = []
    for name, cfg in vm.items():
        rows.append([
            name,
            GENDER_DISPLAY.get(cfg.get("gender", "unknown"), "未知"),
            AGE_DISPLAY.get(cfg.get("age", "young"), "青"),
            EMOTION_DISPLAY.get(cfg.get("emotion", "calm"), "平静"),
            ROLE_DISPLAY.get(cfg.get("role", "supporting"), "配角"),
            cfg.get("voice", DEFAULT_VOICE),
            cfg.get("rate", "+0%"),
            cfg.get("pitch", "+0Hz"),
            as_int(cfg.get("frequency", 0)),
            "、".join(cfg.get("aliases") or []),
        ])
    rows.sort(key=lambda r: (0 if r[4] == "旁白" else 1, -r[8]))
    return rows


def _split_aliases(cell) -> list[str]:
    raw = as_str(cell)
    if not raw:
        return []
    parts = re.split(r"[、,，;；/|\s]+", raw)
    return [p.strip() for p in parts if p.strip()][:6]


def rows_to_voice_map(rows) -> dict:
    """表格行 → voice_map。容忍：整行空白、缺列、频次写成 "3.0"、性别写英文"""
    if rows is None:
        return {}
    if hasattr(rows, "values"):                    # pandas.DataFrame
        rows = rows.values.tolist()

    vm: dict = {}
    for row in rows:
        if not row:
            continue
        cells = list(row) + [None] * (len(VOICE_TABLE_HEADERS) - len(row))
        name = as_str(cells[0])
        if not name:
            continue
        gender = as_str(cells[1])
        age = as_str(cells[2])
        emotion = as_str(cells[3])
        role = as_str(cells[4])
        entry = {
            "voice": resolve_voice(cells[5]),
            "rate": as_str(cells[6], "+0%") or "+0%",
            "pitch": as_str(cells[7], "+0Hz") or "+0Hz",
            "gender": GENDER_REVERSE.get(gender, gender if gender in GENDER_DISPLAY else "unknown"),
            "age": AGE_REVERSE.get(age, "young"),
            "emotion": EMOTION_REVERSE.get(emotion, "calm"),
            "role": ROLE_REVERSE.get(role, "narrator" if name == "旁白" else "supporting"),
            "frequency": as_int(cells[8]),
        }
        aliases = _split_aliases(cells[9])
        if aliases:
            entry["aliases"] = aliases
        vm[name] = entry
    return vm


def add_missing_roles(vm: dict, names: list[str]) -> dict:
    """把剧本里出现、但配音表里没有的角色补进来（各分一个没被占用的音色）

    转译稿的角色名可能与当初识别的人物表不一致（比如「王静」vs「王静一」），
    不补的话合成时会静默回退成旁白音色。
    """
    from scripts.analyze_characters import (FEMALE_VOICES, MALE_VOICES, _EXTRA_FEMALE,
                                            _EXTRA_MALE, _apply_style, _voice_pool)

    used = {cfg.get("voice") for cfg in vm.values() if isinstance(cfg, dict)}
    pool = _voice_pool(FEMALE_VOICES, _EXTRA_FEMALE) + _voice_pool(MALE_VOICES, _EXTRA_MALE)
    for name in names:
        name = (name or "").strip()
        if not name or name in vm:
            continue
        pick = next((v for v in pool if v[0] not in used), pool[0])
        used.add(pick[0])
        rate, pitch = _apply_style(pick[1], pick[2], "young", "calm")
        vm[name] = {"voice": pick[0], "rate": rate, "pitch": pitch,
                    "gender": "unknown", "age": "young", "emotion": "calm",
                    "role": "supporting", "frequency": 0}
    return vm


def missing_roles(vm: dict, roles: list[str]) -> list[str]:
    return [r for r in roles if r and r != "旁白" and r not in vm]


def restyle_voice_map(vm: dict) -> dict:
    """按「年龄 + 情绪」重算语速/音调（以音色自带的预设为基准，可反复执行）"""
    presets = {}
    for v in (FEMALE_VOICES + MALE_VOICES + _EXTRA_FEMALE + _EXTRA_MALE):
        presets[v[0]] = (v[1], v[2])

    for name, cfg in vm.items():
        voice = cfg.get("voice", DEFAULT_VOICE)
        if name == "旁白":
            base_r, base_p = NARRATOR_VOICE[1], NARRATOR_VOICE[2]
        else:
            base_r, base_p = presets.get(voice, ("+0%", "+0Hz"))
        ar, ap = AGE_STYLE.get(cfg.get("age", "young"), (0, 0))
        er, ep = EMOTION_STYLE.get(cfg.get("emotion", "calm"), (0, 0))

        def _n(s, suf):
            try:
                return float(str(s).strip().rstrip(suf).strip())
            except ValueError:
                return 0.0

        cfg["rate"] = f"{int(round(_n(base_r, '%') + ar + er)):+d}%"
        cfg["pitch"] = f"{int(round(_n(base_p, 'Hz') + ap + ep)):+d}Hz"
    return vm


def move_role(rows, name: str, delta: int):
    """把某个角色在表里上移/下移（拖拽之外的键盘友好方案）"""
    if rows is None:
        return None
    data = rows.values.tolist() if hasattr(rows, "values") else [list(r) for r in rows]
    idx = next((i for i, r in enumerate(data) if str(r[0]).strip() == name), None)
    if idx is None:
        return None
    new_idx = max(0, min(len(data) - 1, idx + delta))
    if new_idx == idx:
        return None
    data.insert(new_idx, data.pop(idx))
    return data


# ─────────────────────────────────────────────────────
# 角色库（跨书复用：存下来、下次一键载入、可删）
# ─────────────────────────────────────────────────────
def library_load() -> dict:
    if not os.path.exists(LIBRARY_PATH):
        return {}
    try:
        with open(LIBRARY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def library_save(lib: dict) -> str:
    ensure_dirs()
    with open(LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    return LIBRARY_PATH


def library_upsert(vm: dict) -> int:
    """把当前配音表里的角色写进库（同名覆盖），返回写入条数"""
    lib = library_load()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    n = 0
    for name, cfg in vm.items():
        if not name:
            continue
        lib[name] = {
            "voice": cfg.get("voice", DEFAULT_VOICE),
            "rate": cfg.get("rate", "+0%"),
            "pitch": cfg.get("pitch", "+0Hz"),
            "gender": cfg.get("gender", "unknown"),
            "role": cfg.get("role", "supporting"),
            "aliases": cfg.get("aliases", []),
            "updated": stamp,
        }
        n += 1
    library_save(lib)
    return n


def library_delete(names: list[str]) -> int:
    lib = library_load()
    removed = 0
    for name in names or []:
        if name in lib:
            lib.pop(name)
            removed += 1
    library_save(lib)
    return removed


def library_rows() -> list[list]:
    lib = library_load()
    rows = []
    for name, cfg in sorted(lib.items(), key=lambda kv: (kv[1].get("role") != "narrator", kv[0])):
        rows.append([
            name,
            GENDER_DISPLAY.get(cfg.get("gender", "unknown"), "未知"),
            ROLE_DISPLAY.get(cfg.get("role", "supporting"), "配角"),
            cfg.get("voice", DEFAULT_VOICE),
            cfg.get("rate", "+0%"),
            cfg.get("pitch", "+0Hz"),
            cfg.get("updated", ""),
        ])
    return rows


def library_apply(names: list[str]) -> dict:
    """从库里取出这些角色，拼成可直接用的 voice_map 片段"""
    lib = library_load()
    vm: dict = {}
    for name in names or []:
        cfg = lib.get(name)
        if not cfg:
            continue
        vm[name] = {
            "voice": resolve_voice(cfg.get("voice")),
            "rate": cfg.get("rate", "+0%"),
            "pitch": cfg.get("pitch", "+0Hz"),
            "gender": cfg.get("gender", "unknown"),
            "role": cfg.get("role", "supporting"),
            "frequency": 0,
            "aliases": cfg.get("aliases", []),
        }
    return vm


# ─────────────────────────────────────────────────────
# 剧本预览（哪句谁念 / 哪些不念）
# ─────────────────────────────────────────────────────
def script_preview(novel_path: str, voice_map: dict, start: int = 0,
                   end: int = 10 ** 7, max_rows: int = 600) -> dict:
    """解析成「逐句清单」，标出角色、类型、是否朗读、字数，供可视化面板用"""
    if not voice_map:
        return {"rows": [], "stats": {}, "roles": [], "total": 0}

    src = normalize_text_file(novel_path)
    lines = read_text(src).splitlines()
    excerpt = "\n".join(lines[int(start):int(end)])

    trace: list = []
    parse_novel_to_script(excerpt, voice_map, trace=trace)

    rows = []
    stats = {"total": 0, "read": 0, "skipped": 0, "chars": 0}
    per_role: dict[str, dict] = {}

    for idx, item in enumerate(trace, 1):
        text = item.get("text") or ""
        role = item.get("role") or "-"
        kind = item.get("kind") or "-"
        skip = item.get("skip")
        chars = len(re.sub(r"\s", "", text))

        if not skip:
            # 纯标点 / 空片段：生成器会直接丢掉，面板上也要标出来
            if not gen._clean_text(text):
                skip = "纯标点"
        status = f"跳过（{skip}）" if skip else "朗读"

        stats["total"] += 1
        if skip:
            stats["skipped"] += 1
        else:
            stats["read"] += 1
            stats["chars"] += chars
            r = per_role.setdefault(role, {"lines": 0, "chars": 0})
            r["lines"] += 1
            r["chars"] += chars

        flags = []
        if not skip:
            if role != "旁白" and role not in voice_map:
                flags.append("未配音")
            if chars > 120:
                flags.append("过长")
            if chars and chars < 3:
                flags.append("过短")
        if len(rows) < max_rows:
            rows.append([idx, kind, role, text[:80], chars, status, "、".join(flags)])

    roles = [
        {"role": k, "lines": v["lines"], "chars": v["chars"],
         "percent": round(v["lines"] * 100 / max(stats["read"], 1))}
        for k, v in sorted(per_role.items(), key=lambda kv: -kv[1]["lines"])
    ]
    stats["warn"] = sum(1 for r in rows if r[6])
    return {"rows": rows, "stats": stats, "roles": roles, "total": len(trace)}


def save_voice_map(vm: dict) -> str:
    ensure_dirs()
    with open(VOICE_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(vm, f, ensure_ascii=False, indent=2)
    return VOICE_MAP_PATH


def load_voice_map() -> dict | None:
    if not os.path.exists(VOICE_MAP_PATH):
        return None
    try:
        with open(VOICE_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ─────────────────────────────────────────────────────
# 发音词典（多音字 / 专名，用同音字替换）
# ─────────────────────────────────────────────────────
def load_pronounce() -> dict:
    """{原文: 替换为}；文件不存在返回空表"""
    if not os.path.exists(PRONOUNCE_PATH):
        return {}
    try:
        with open(PRONOUNCE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_pronounce(items: dict) -> str:
    ensure_dirs()
    clean = {str(k).strip(): str(v).strip()
             for k, v in (items or {}).items()
             if str(k).strip() and str(v).strip()}
    with open(PRONOUNCE_PATH, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    apply_pronounce(clean)
    return PRONOUNCE_PATH


def apply_pronounce(items: dict | None = None) -> dict:
    """把词典推给生成器（合成时逐句替换）"""
    table = load_pronounce() if items is None else items
    gen._PRONOUNCE = dict(table)
    return gen._PRONOUNCE


def pronounce_rows() -> list[list]:
    return [[k, v] for k, v in sorted(load_pronounce().items(), key=lambda kv: -len(kv[0]))]


# ─────────────────────────────────────────────────────
# 引擎与合成
# ─────────────────────────────────────────────────────
def set_engine(engine: str, interval: float | None = None,
               workers: int | None = None) -> tuple[str, float]:
    """运行期切换引擎（写 audiobook_generator 的模块级变量，无需重启）"""
    engine = (engine or "edge").lower()
    if engine not in ("edge", "say"):
        engine = "edge"
    gen._ENGINE = engine
    if interval is not None:
        gen._INTERVAL = max(0.0, float(interval))
    if workers is not None:
        gen._WORKERS = max(1, min(16, int(workers)))
    return gen._ENGINE, gen._INTERVAL


def set_workers(n: int) -> int:
    gen._WORKERS = max(1, min(16, int(n or 1)))
    return gen._WORKERS


def engine_state() -> tuple[str, float]:
    return gen._ENGINE, gen._INTERVAL


# ─────────────────────────────────────────────────────
# 大模型识别（可选）
# ─────────────────────────────────────────────────────
def llm_config() -> dict:
    return llm.load_config()


def llm_save(cfg: dict) -> str:
    return llm.save_config(cfg)


def llm_status(cfg: dict | None = None) -> tuple[bool, str]:
    return llm.ready(cfg or llm.load_config())


def llm_test(cfg: dict) -> str:
    return llm.test_connection(cfg)


def synthesize_file(
    novel_path: str,
    voice_map: dict,
    name: str,
    start: int = 0,
    end: int = 10 ** 7,
    progress_cb: Callable | None = None,
) -> dict:
    """合成一段文本 → {file, duration, size_kb, timings}"""
    if not voice_map:
        raise RuntimeError("配音表是空的：先点「分析角色与声音」，或从配置读入")
    src = normalize_text_file(novel_path)
    out, duration, timings = asyncio.run(gen.generate(
        novel_path=src, start=int(start), end=int(end), name=name,
        voice_map=voice_map, output_dir=OUTPUT_DIR, progress_cb=progress_cb,
    ))
    return {
        "file": out,
        "name": name,
        "duration": duration,
        "size_kb": os.path.getsize(out) / 1024,
        "timings": timings,
    }


def synthesize_chapter(chapter_file: str, voice_map: dict,
                       progress_cb: Callable | None = None) -> dict:
    name = os.path.splitext(os.path.basename(chapter_file))[0]
    return synthesize_file(os.path.join(SAMPLES_DIR, chapter_file), voice_map, name,
                           progress_cb=progress_cb)


def batch_generate(
    chapters: list[str],
    voice_map: dict,
    force: bool = False,
    progress_cb: Callable | None = None,
) -> Iterator[dict]:
    """逐章合成，每章产出一条事件（同步生成器，给脚本 / 定时任务用）

    网页不用它 —— 网页走 SynthesisJob（可暂停/停止/跳过）。这里保持简单：
    调用方拿到事件流自己处理，中途想停就 break（本章不会产出半成品）。

    事件：{index, total, name, status: ok|skip|fail, duration, size_kb, message, file}
    """
    total = len(chapters)
    for i, fn in enumerate(chapters, 1):
        name = os.path.splitext(fn)[0]
        out = os.path.join(OUTPUT_DIR, f"{name}.mp3")
        event = {"index": i, "total": total, "name": name, "file": out,
                 "duration": 0.0, "size_kb": 0.0, "message": ""}

        if os.path.exists(out) and not force:
            event.update(status="skip", size_kb=os.path.getsize(out) / 1024,
                         message="已存在，跳过（勾选「强制重做」可覆盖）")
            yield event
            continue
        try:
            res = synthesize_chapter(fn, voice_map, progress_cb)
            event.update(status="ok", duration=res["duration"],
                         size_kb=res["size_kb"], file=res["file"])
        except Exception as e:                     # noqa: BLE001
            event.update(status="fail", message=str(e).replace("\n", " ")[:200])
        yield event


# ─────────────────────────────────────────────────────
# 可干预的后台任务（暂停 / 继续 / 停止 / 跳过当前章）
# ─────────────────────────────────────────────────────
class SynthesisJob:
    """把合成跑在后台线程里，界面只做轮询 —— 这样任何时刻都能人工干预。

    与「用生成器把日志流式推给页面」相比，关键差别是：
      · 点「停止」立刻生效（每句合成前检查一次，1~2 秒内响应）
      · 可以「暂停 / 继续」，也可以「跳过当前章」继续下一章
      · 关掉或刷新网页，任务仍在后台跑（日志和产物不会丢）
      · 停止不会留下半成品 MP3，已合成的句子都进了 .cache/tts，重跑自动续上
    """

    def __init__(self, tasks: list[dict], voice_map: dict, force: bool = False,
                 kind: str = "batch"):
        self.id = f"{int(time.time() * 1000)}-{os.getpid()}"
        self.kind = kind                    # batch | single
        self.tasks = tasks
        self.voice_map = voice_map
        self.force = force

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._skip = threading.Event()

        self.status = "ready"               # ready|running|paused|stopping|done|stopped
        self.log: list[str] = []
        self.version = 0
        self.index = 0
        self.total = len(tasks)
        self.progress = 0.0
        self.detail = ""
        self.ok = 0
        self.skip = 0
        self.fail = 0
        self.latest: str | None = None
        self.failures: list[str] = []
        self.timings: list[dict] = []
        self.started_at = 0.0
        self.finished_at = 0.0
        self._thread: threading.Thread | None = None

    # ── 对外控制 ──────────────────────────────────
    def start(self) -> "SynthesisJob":
        self._thread = threading.Thread(target=self._run, name="synth-job", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> str:
        if not self.is_running():
            return "任务已经结束了"
        self._stop.set()
        self._pause.clear()
        with self._lock:
            if self.status in ("running", "paused"):
                self.status = "stopping"
        self._log("⏹ 收到停止指令，正在收尾（当前句结束后退出）…")
        return "正在停止…"

    def toggle_pause(self) -> str:
        if not self.is_running():
            return "任务已经结束了"
        if self._pause.is_set():
            self._pause.clear()
            with self._lock:
                if self.status == "paused":
                    self.status = "running"
            self._log("▶ 已继续")
            return "继续合成"
        self._pause.set()
        with self._lock:
            if self.status == "running":
                self.status = "paused"
        self._log("⏸ 已暂停（当前句合成完后停在这里）")
        return "已暂停"

    def skip_current(self) -> str:
        if not self.is_running():
            return "任务已经结束了"
        if self.index <= 0:
            return "还没有在跑的任务"
        self._skip.set()
        self._log(f"⏭ 跳过当前任务：{self.tasks[self.index - 1]['name']}")
        return "正在跳过当前章…"

    def is_running(self) -> bool:
        return self.status in ("running", "paused", "stopping")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "log": "\n".join(self.log),
                "version": self.version,
                "index": self.index,
                "total": self.total,
                "progress": round(self.progress, 4),
                "detail": self.detail,
                "ok": self.ok,
                "skip": self.skip,
                "fail": self.fail,
                "latest": self.latest,
                "failures": list(self.failures),
                "timings": self.timings,
                "elapsed": round((self.finished_at or time.time()) - self.started_at, 1)
                if self.started_at else 0.0,
            }

    # ── 内部 ──────────────────────────────────────
    def _log(self, msg: str) -> None:
        with self._lock:
            self.log.append(msg)
            self.version += 1

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _progress_cb(self, v: float, desc: str) -> None:
        with self._lock:
            self.progress = min(1.0, (self.index - 1 + max(0.0, min(1.0, v))) / max(self.total, 1))
            self.detail = desc

    async def _control(self) -> None:
        """每句合成前调用：暂停时挂起，停止/跳过时抛异常"""
        while self._pause.is_set() and not self._stop.is_set() and not self._skip.is_set():
            await asyncio.sleep(0.2)
        if self._stop.is_set():
            raise gen.SynthesisAborted("用户停止")
        if self._skip.is_set():
            raise gen.SynthesisAborted("跳过当前章")

    def _run(self) -> None:
        self._set(status="running", started_at=time.time())
        self._log(f"引擎 {gen._ENGINE} · 句间间隔 {gen._INTERVAL:.1f}s · "
                  f"{self.total} 个任务 · {'强制重做' if self.force else '已有成品自动跳过'}")

        for i, task in enumerate(self.tasks, 1):
            if self._stop.is_set():
                break
            self._set(index=i)
            name = task["name"]
            out = os.path.join(OUTPUT_DIR, f"{name}.mp3")

            if task["kind"] == "chapter" and os.path.exists(out) and not self.force:
                with self._lock:
                    self.skip += 1
                    self.progress = i / self.total
                self._log(f"[{i}/{self.total}] ⏭ {name} 已存在，跳过")
                continue

            self._skip.clear()
            self._log(f"[{i}/{self.total}] ▶ {name} 开始合成")
            try:
                file, duration, timings = asyncio.run(gen.generate(
                    novel_path=task["path"],
                    start=int(task.get("start", 0)),
                    end=int(task.get("end", 10 ** 7)),
                    name=name,
                    voice_map=self.voice_map,
                    output_dir=OUTPUT_DIR,
                    progress_cb=self._progress_cb,
                    control=self._control,
                ))
                with self._lock:
                    self.ok += 1
                    self.latest = file
                    self.timings = timings
                    self.progress = i / self.total
                    self.detail = ""
                self._log(f"[{i}/{self.total}] ✅ {name} · {duration:.0f}s · "
                          f"{os.path.getsize(file) / 1024:.0f} KB")
            except gen.SynthesisAborted:
                if self._skip.is_set():
                    with self._lock:
                        self.skip += 1
                        self.progress = i / self.total
                    self._log(f"[{i}/{self.total}] ⏭ {name} 已跳过（已合成句子留在缓存）")
                    continue
                self._log(f"[{i}/{self.total}] ⏹ 已停止：{name} 未完成，"
                          f"不产出半成品；已合成句子在缓存里，重跑会续上")
                self._set(status="stopped", finished_at=time.time())
                return
            except Exception as e:                 # noqa: BLE001
                with self._lock:
                    self.fail += 1
                    self.failures.append(name)
                    self.progress = i / self.total
                self._log(f"[{i}/{self.total}] ❌ {name}：{str(e).replace(chr(10), ' ')[:160]}")

        status = "stopped" if self._stop.is_set() else "done"
        self._set(status=status, finished_at=time.time())
        tail = f"完成：成功 {self.ok} / 跳过 {self.skip} / 失败 {self.fail} / 共 {self.total}"
        if status == "stopped":
            tail = (f"已停止：成功 {self.ok} / 跳过 {self.skip} / 失败 {self.fail} / "
                    f"未处理 {self.total - self.ok - self.skip - self.fail}")
        self._log(tail)
        if status == "stopped" or self.fail:
            self._log("再点一次「开始」即可续跑：已完成章节自动跳过，已合成句子命中缓存")


_JOB_LOCK = threading.Lock()
_CURRENT_JOB: SynthesisJob | None = None


def start_job(tasks: list[dict], voice_map: dict, force: bool = False,
              kind: str = "batch") -> SynthesisJob:
    """启动一个后台任务；已有任务在跑时抛 RuntimeError"""
    global _CURRENT_JOB
    if not tasks:
        raise RuntimeError("没有要处理的任务")
    if not voice_map:
        raise RuntimeError("配音表是空的：先「分析角色与声音」或「读入默认配置」")
    with _JOB_LOCK:
        if _CURRENT_JOB is not None and _CURRENT_JOB.is_running():
            raise RuntimeError("已有任务在跑，请先停止它")
        _CURRENT_JOB = SynthesisJob(tasks, voice_map, force, kind).start()
        return _CURRENT_JOB


def current_job() -> SynthesisJob | None:
    return _CURRENT_JOB


def chapter_tasks(chapters: list[str], use_translated: bool = True) -> list[dict]:
    """章节 → 任务。转译稿存在且允许时优先用它（格式更规整，归属更准）"""
    tasks = []
    for fn in chapters:
        raw = os.path.join(SAMPLES_DIR, fn)
        path = raw
        if use_translated:
            tp = translate.translated_path(fn)
            if os.path.exists(tp):
                path = tp
        tasks.append({
            "kind": "chapter",
            "name": os.path.splitext(fn)[0],
            "path": path,
            "translated": path != raw,
        })
    return tasks


def single_task(novel_path: str, name: str, start: int = 0, end: int = 10 ** 7) -> list[dict]:
    return [{
        "kind": "range",
        "name": (name or "片段").strip(),
        "path": normalize_text_file(novel_path),
        "start": int(start),
        "end": int(end),
    }]


# ─────────────────────────────────────────────────────
# 文本转译任务（边转边出，界面可实时滚动预览）
# ─────────────────────────────────────────────────────
class TranslateJob(SynthesisJob):
    """逐章把原文改写成「角色｜台词」脚本

    复用 SynthesisJob 的暂停/停止/跳过/快照机制，只换掉 _run 的执行体。
    每转译完一块就把新行推进 self.lines，界面据此做「文字翻滚」的实时预览。
    """

    def __init__(self, chapters: list[str], voice_map: dict, mode: str = "llm", cfg: dict | None = None):
        tasks = [{"kind": "translate",
                  "name": os.path.splitext(fn)[0],
                  "path": os.path.join(SAMPLES_DIR, fn)} for fn in chapters]
        super().__init__(tasks, voice_map, force=True, kind="translate")
        self.mode = mode if mode in ("llm", "rules") else "rules"
        self.cfg = cfg or llm.load_config()
        self.lines: list[str] = []
        self.current = ""

    # 覆盖：把已转译的行一起吐给界面
    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["lines"] = self.lines[-300:]
        snap["current"] = self.current
        return snap

    def _control_sync(self) -> None:
        """translate 的 control 是同步回调（不像 TTS 那样 await）"""
        if self._stop.is_set():
            raise gen.SynthesisAborted("用户停止")
        while self._pause.is_set() and not self._stop.is_set() and not self._skip.is_set():
            time.sleep(0.2)
        if self._stop.is_set():
            raise gen.SynthesisAborted("用户停止")
        if self._skip.is_set():
            raise gen.SynthesisAborted("跳过当前章")

    def _emit(self, lines: list[str]) -> None:
        with self._lock:
            self.lines.extend(lines)
            self.version += 1

    def _run(self) -> None:
        self._set(status="running", started_at=time.time())
        mode_label = "大模型逐章转译" if self.mode == "llm" else "本地规则转译（不联网）"
        self._log(f"{mode_label} · {self.total} 章 · 输出到 translated/")
        if self.mode == "llm":
            ok, why = llm.ready(self.cfg)
            self._log(f"模型：{why}" if ok else f"⚠️ {why} —— 将全部回退本地规则")

        for i, task in enumerate(self.tasks, 1):
            if self._stop.is_set():
                break
            self._set(index=i)
            self._skip.clear()
            name = task["name"]
            self._set(current=name)
            self._log(f"[{i}/{self.total}] ▶ {name} 开始转译")
            try:
                text = read_text(task["path"])
                if self.mode == "llm" and llm.ready(self.cfg)[0]:
                    lines, stats = translate.translate_chapter(
                        text, self.voice_map, self.cfg,
                        progress_cb=self._progress_cb,
                        control=self._control_sync,
                        emit=self._emit,
                    )
                else:
                    lines = translate.translate_by_rules(text, self.voice_map)
                    self._emit(lines)
                    stats = {"chunks": 1, "llm": 0, "fallback": 0, "lines": len(lines)}

                translate.save_translated(name + ".txt", lines)
                with self._lock:
                    self.ok += 1
                    self.progress = i / self.total
                    self.detail = f"{name} 完成 · {len(lines)} 行"
                extra = f"（{stats['fallback']} 块回退规则）" if stats.get("fallback") else ""
                self._log(f"[{i}/{self.total}] ✅ {name} · {len(lines)} 行{extra}")
            except gen.SynthesisAborted:
                if self._skip.is_set():
                    with self._lock:
                        self.skip += 1
                        self.progress = i / self.total
                    self._log(f"[{i}/{self.total}] ⏭ {name} 已跳过")
                    continue
                self._log(f"[{i}/{self.total}] ⏹ 已停止：{name} 未完成，已完成部分未写入文件")
                self._set(status="stopped", finished_at=time.time())
                return
            except Exception as e:                 # noqa: BLE001
                with self._lock:
                    self.fail += 1
                    self.progress = i / self.total
                self._log(f"[{i}/{self.total}] ❌ {name}：{str(e).replace(chr(10), ' ')[:160]}")

        status = "stopped" if self._stop.is_set() else "done"
        self._set(status=status, finished_at=time.time(), current="")
        tail = (f"完成：成功 {self.ok} / 跳过 {self.skip} / 失败 {self.fail} / 共 {self.total}"
                if status == "done" else
                f"已停止：成功 {self.ok} / 跳过 {self.skip} / 失败 {self.fail}")
        self._log(tail)
        self._log("转译稿在 translated/ 下，合成时会自动优先使用")


def start_translate_job(chapters: list[str], voice_map: dict,
                        mode: str = "llm") -> TranslateJob:
    global _CURRENT_JOB
    if not chapters:
        raise RuntimeError("没有选中要转译的章节")
    with _JOB_LOCK:
        if _CURRENT_JOB is not None and _CURRENT_JOB.is_running():
            raise RuntimeError("已有任务在跑，请先停止它")
        _CURRENT_JOB = TranslateJob(chapters, voice_map, mode).start()
        return _CURRENT_JOB


# ─────────────────────────────────────────────────────
# 导出 m4b（带章节标记，手机播放器可按章跳转）
# ─────────────────────────────────────────────────────
def export_plan(chapters: list[str]) -> list[tuple[str, str, int]]:
    """[(章节名, mp3 路径, 时长ms)]，只保留已经出片的"""
    plan = []
    for fn in chapters:
        name = os.path.splitext(fn)[0]
        mp3 = os.path.join(OUTPUT_DIR, f"{name}.mp3")
        if os.path.exists(mp3):
            plan.append((name, mp3, gen.mp3_duration_ms(mp3)))
    return plan


class ExportJob(SynthesisJob):
    """把若干章 MP3 合成一个带章节标记的 m4b

    复用 Job 的暂停/停止/快照机制；ffmpeg 的 -progress 输出用来算真实进度。
    """

    def __init__(self, chapters: list[str], title: str = "有声书", author: str = ""):
        plan = export_plan(chapters)
        if not plan:
            raise RuntimeError("这些章节还没有成品 MP3，先合成")
        tasks = [{"kind": "export", "name": n, "path": p, "ms": ms} for n, p, ms in plan]
        super().__init__(tasks, {}, force=True, kind="export")
        self.title = (title or "有声书").strip()
        self.author = (author or "").strip()
        self.total_ms = sum(t["ms"] for t in tasks)
        self.out_path = ""

    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["out_path"] = self.out_path
        return snap

    def _run(self) -> None:
        self._set(status="running", started_at=time.time())
        total_min = self.total_ms / 60000
        self._log(f"导出《{self.title}》· {self.total} 章 · 约 {total_min:.1f} 分钟音频")
        self._log("正在转 AAC 并写入章节标记，时长较长时请耐心等待…")

        td = tempfile.mkdtemp(prefix="m4b_")
        listfile = os.path.join(td, "list.txt")
        metafile = os.path.join(td, "meta.txt")
        out = os.path.join(OUTPUT_DIR, f"{safe_name(self.title)}.m4b")

        with open(listfile, "w", encoding="utf-8") as f:
            for t in self.tasks:
                f.write("file '" + t["path"].replace("'", "'\\''") + "'\n")

        with open(metafile, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            f.write(f"title={self.title}\n")
            if self.author:
                f.write(f"artist={self.author}\n")
            start = 0
            for t in self.tasks:
                f.write("[CHAPTER]\nTIMEBASE=1/1000\n")
                f.write(f"START={start}\nEND={start + t['ms']}\n")
                f.write(f"title={t['name']}\n")
                start += t["ms"]

        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
               "-i", listfile, "-i", metafile, "-map_metadata", "1",
               "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", out]

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            self._log(f"❌ 起不了 ffmpeg：{e}")
            self._set(status="done", finished_at=time.time())
            return

        total_s = max(self.total_ms / 1000, 1)
        for line in proc.stdout:                   # 实时读 ffmpeg 进度
            if self._stop.is_set():
                proc.terminate()
                break
            line = line.strip()
            if line.startswith("out_time="):
                sec = _parse_hhmmss(line.split("=", 1)[1])
                with self._lock:
                    self.progress = min(0.99, sec / total_s)
                    self.detail = f"已转换 {sec/60:.1f}/{total_s/60:.1f} 分钟"
        code = proc.wait()
        err = (proc.stderr.read() or "").strip()[:300] if proc.stderr else ""

        if self._stop.is_set():
            self._log("⏹ 已停止导出，未生成文件")
            self._set(status="stopped", finished_at=time.time())
            shutil.rmtree(td, ignore_errors=True)
            return
        if code != 0 or not os.path.exists(out):
            self._log(f"❌ ffmpeg 失败（{code}）：{err}")
            self._set(status="done", finished_at=time.time())
            shutil.rmtree(td, ignore_errors=True)
            return

        self.out_path = out
        size_mb = os.path.getsize(out) / 1024 / 1024
        with self._lock:
            self.ok = 1
            self.progress = 1.0
            self.detail = f"{os.path.basename(out)} · {size_mb:.1f} MB"
        self._log(f"✅ 已导出 {os.path.basename(out)} · {size_mb:.1f} MB · "
                  f"{self.total} 章 / {total_min:.1f} 分钟")
        self._set(status="done", finished_at=time.time())
        shutil.rmtree(td, ignore_errors=True)


def _parse_hhmmss(text: str) -> float:
    try:
        h, m, s = text.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return 0.0


def start_export_job(chapters: list[str], title: str, author: str = "") -> ExportJob:
    global _CURRENT_JOB
    with _JOB_LOCK:
        if _CURRENT_JOB is not None and _CURRENT_JOB.is_running():
            raise RuntimeError("已有任务在跑，请先停止它")
        job = ExportJob(chapters, title, author)
        _CURRENT_JOB = job.start()
        return _CURRENT_JOB


def translated_summary() -> dict[str, int]:
    return translate.list_translated()


def preview_voice(text: str, voice: str, rate: str = "+0%", pitch: str = "+0Hz") -> str:
    """单句样音试听（走当前引擎，命中缓存时几乎瞬时）"""
    text = as_str(text) or "这是一段试音。"
    tmpdir = tempfile.mkdtemp(prefix="preview_")
    out = os.path.join(tmpdir, "preview.mp3")
    ok = asyncio.run(gen._synthesize_line(
        gen.apply_pronounce(text), resolve_voice(voice), as_str(rate, "+0%") or "+0%",
        as_str(pitch, "+0Hz") or "+0Hz", out,
    ))
    if not ok:
        engine = gen._ENGINE
        raise RuntimeError(
            "样音合成失败。"
            + ("离线 say 引擎：检查系统是否装了中文声音。" if engine == "say"
               else "在线 edge 引擎：多半被限流或 DNS 不通，可切到离线引擎。")
        )
    return out


# ─────────────────────────────────────────────────────
# 产物库
# ─────────────────────────────────────────────────────
def list_outputs() -> list[list]:
    """output/ 下的成品：[文件名, 大小MB, 修改时间]"""
    if not os.path.isdir(OUTPUT_DIR):
        return []
    rows = []
    for fn in sorted(f for f in os.listdir(OUTPUT_DIR)
                     if f.lower().endswith((".mp3", ".m4b"))):
        p = os.path.join(OUTPUT_DIR, fn)
        st = os.stat(p)
        rows.append([fn, round(st.st_size / 1024 / 1024, 2),
                     time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))])
    return rows


def output_path(name: str) -> str | None:
    p = os.path.join(OUTPUT_DIR, os.path.basename(as_str(name)))
    return p if os.path.exists(p) else None


def cache_size_mb() -> float:
    cache = os.path.join(ROOT, ".cache", "tts")
    if not os.path.isdir(cache):
        return 0.0
    total = sum(os.path.getsize(os.path.join(cache, f)) for f in os.listdir(cache))
    return round(total / 1024 / 1024, 1)


def summary() -> dict:
    engine, interval = engine_state()
    outs = list_outputs()
    lib = library_load()
    vm = load_voice_map()
    return {
        "engine": engine,
        "interval": interval,
        "workers": gen._WORKERS,
        "chapters": len(list_chapters()),
        "translated": len(translated_summary()),
        "roles": len([k for k in (vm or {}) if k != "旁白"]),
        "library": len(lib),
        "outputs": len(outs),
        "output_mb": round(sum(r[1] for r in outs), 1),
        "cache_mb": cache_size_mb(),
        "llm": llm_status()[0],
    }


if __name__ == "__main__":                        # 手动体检：python scripts/studio.py
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
    for row in chapter_stats()[:20]:
        print(row)
