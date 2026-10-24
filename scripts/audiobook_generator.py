"""
通用有声书生成器（本地增强版）
------------------------------
在原版基础上增加：
  1. 逐句缓存（.cache/tts）：同一句同一声音只合成一次，中断后重跑自动续上
  2. 更稳的重试：5 次指数退避 + 单句 45s 超时；失败句计入统计而非静默丢弃
  3. 可调节流：AUDIOBOOK_INTERVAL（秒）或 --interval，默认 1.2s
  4. 失败兜底提示：全部失败时给出明确原因（多半是 edge-tts 被限流）

用法：
    .venv/bin/python scripts/audiobook_generator.py --input data/novel.txt --start 0 --end 200 --name 第001章
    .venv/bin/python scripts/audiobook_generator.py --input ... --voice-map config/voice_map.json
    AUDIOBOOK_INTERVAL=2 .venv/bin/python scripts/audiobook_generator.py ...
    .venv/bin/python scripts/audiobook_generator.py ... --workers 6   # 并发提速
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydub import AudioSegment  # noqa: E402
import edge_tts  # noqa: E402

from scripts.analyze_characters import analyze  # noqa: E402
from scripts.parse_script import parse_novel_to_script  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, ".cache", "tts")

# 停顿时长（毫秒）
PAUSE_AFTER_NARRATION = 420
PAUSE_ROLE_CHANGE = 260
PAUSE_SAME_ROLE = 160

_PUNCT_ONLY = re.compile(
    r'^[\s\u3000\u00a0，。！？、；：""' "''" r"「」『』【】…—·\.,!?;:\-\(\)\[\]]+$"
)
_MAX_TTS_CHARS = 500          # edge-tts 单次建议上限
_TTS_TIMEOUT = 45             # 单句超时（秒）
_MAX_ATTEMPTS = 5             # 单句最多尝试次数
_INTERVAL = float(os.environ.get("AUDIOBOOK_INTERVAL", "1.2"))
# 并发路数：合成慢在「每句一次网络往返」，串行等于把延迟相加。
# 4 路通常能快 3~4 倍；被限流时调小，网络好可调大。
_WORKERS = int(os.environ.get("AUDIOBOOK_WORKERS", "4") or 4)
# 引擎：edge = 微软在线（音质好，需联网）；say = macOS 系统语音（离线可用）
_ENGINE = os.environ.get("AUDIOBOOK_ENGINE", "edge").lower()


# ─────────────────────────────────────────────────────
# 文本清洗
# ─────────────────────────────────────────────────────
def _clean_text(text: str) -> list[str]:
    """清洗文本，返回可安全合成的片段列表（过滤纯标点、超长切句）"""
    text = text.strip()
    if not text or _PUNCT_ONLY.match(text):
        return []
    if len(text) <= _MAX_TTS_CHARS:
        return [text]
    chunks, current = [], ""
    for sent in re.split(r"(?<=[。！？\n])", text):
        if len(current) + len(sent) > _MAX_TTS_CHARS:
            if current:
                chunks.append(current.strip())
            current = sent
        else:
            current += sent
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c and not _PUNCT_ONLY.match(c)]


def _cache_key(text: str, voice: str, rate: str, pitch: str) -> str:
    raw = f"{_ENGINE}|{text}|{voice}|{rate}|{pitch}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


# ─────────────────────────────────────────────────────
# TTS 合成（带缓存 + 退避）
# ─────────────────────────────────────────────────────
async def _synthesize_line(text: str, voice: str, rate: str, pitch: str, out_path: str) -> bool:
    """合成单句。命中缓存直接复制；失败重试 5 次（4/8/16/32s 退避）。返回是否成功"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached = os.path.join(CACHE_DIR, _cache_key(text, voice, rate, pitch) + ".mp3")
    if os.path.exists(cached) and os.path.getsize(cached) > 0:
        shutil.copyfile(cached, out_path)
        return True

    if _ENGINE == "say":
        from scripts.tts_local import say_to_mp3
        try:
            await asyncio.to_thread(say_to_mp3, text, voice, rate, pitch, out_path)
        except Exception:
            return False
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            shutil.copyfile(out_path, cached)
            return True
        return False

    for attempt in range(_MAX_ATTEMPTS):
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
            await asyncio.wait_for(communicate.save(out_path), timeout=_TTS_TIMEOUT)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                shutil.copyfile(out_path, cached)   # 成功即入缓存
                return True
        except Exception:
            if attempt == _MAX_ATTEMPTS - 1:
                return False
            await asyncio.sleep(4 * (2 ** attempt))   # 4, 8, 16, 32 秒
    return False


# ─────────────────────────────────────────────────────
# 音频时长 / 拼接（避免「每句一次 ffmpeg 子进程」）
# ─────────────────────────────────────────────────────
_MP3_BITRATES = {
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],   # MPEG1 L3
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],       # MPEG2/2.5 L3
}
_MP3_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
_SILENCE_CACHE: dict[int, str] = {}


def mp3_duration_ms(path: str) -> int:
    """纯 Python 解析 MP3 帧头算时长。

    比 pydub/ffprobe 快两个数量级（无子进程）：一次合成几百句时，
    光「读时长」就能省下几十秒。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return 0

    i = 0
    if data[:3] == b"ID3" and len(data) > 10:          # 跳过 ID3v2 标签
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) \
               | ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        i = 10 + size
        if data[5] & 0x10:                             # v2.4 footer
            i += 10

    total = 0.0
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
            i += 1
            continue
        b1, b2 = data[i + 1], data[i + 2]
        version = (b1 >> 3) & 0x03          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
        layer = (b1 >> 1) & 0x03            # 1=Layer III
        br_idx = (b2 >> 4) & 0x0F
        sr_idx = (b2 >> 2) & 0x03
        padding = (b2 >> 1) & 0x01
        if version == 1 or layer != 1 or br_idx in (0, 15) or sr_idx == 3:
            i += 1
            continue
        table = 1 if version == 3 else 2
        bitrate = _MP3_BITRATES[table][br_idx] * 1000
        sample_rate = _MP3_RATES[version][sr_idx]
        spf = 1152 if version == 3 else 576           # 每帧采样数
        frame_len = int(spf / 8 * bitrate / sample_rate) + padding
        if frame_len <= 4:
            i += 1
            continue
        # 校验：下一帧也必须是同步字（否则这是标签里的假同步）
        nxt = i + frame_len
        if nxt + 1 < n and (data[nxt] != 0xFF or (data[nxt + 1] & 0xE0) != 0xE0):
            i += 1
            continue
        # Xing/Info 是 LAME 写的元数据帧，不含音频，播放器会跳过 —— 不能计入时长
        mono = ((data[i + 3] >> 6) & 0x03) == 3
        side = (17 if mono else 32) if version == 3 else (9 if mono else 17)
        tag_at = i + 4 + side
        if data[tag_at:tag_at + 4] in (b"Xing", b"Info"):
            i = nxt
            continue
        total += spf / sample_rate
        i = nxt
    return int(total * 1000)


def _silence_path(ms: int) -> str:
    """缓存静音片段：同一种时长只生成一次，避免每句都起一次 ffmpeg"""
    cached = _SILENCE_CACHE.get(ms)
    if cached and os.path.exists(cached):
        return cached
    d = os.path.join(tempfile.gettempdir(), "audiobook_silence")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"silence_{ms}.mp3")
    if not os.path.exists(p):
        AudioSegment.silent(duration=ms, frame_rate=24000).set_channels(1).export(
            p, format="mp3", bitrate="96k")
    _SILENCE_CACHE[ms] = p
    return p


class SynthesisAborted(RuntimeError):
    """用户主动中断（停止 / 跳过当前章）。

    已合成的句子都在 .cache/tts 里，重跑会自动续上；本章不会产出半成品 MP3。
    """


async def synthesize_script(
    script: list[dict],
    voice_map: dict,
    tmpdir: str,
    progress_cb: Callable | None = None,
    control: Callable | None = None,
) -> tuple[list[str], list[dict], dict]:
    """
    合成所有台词，返回 (mp3 路径列表, 时间轴, 统计)。
    统计格式：{"total": N, "ok": N, "failed": N, "failed_samples": [...]}

    control: 可选的异步回调，每句合成前调用一次。它可以在里面等待（暂停），
             也可以抛 SynthesisAborted（停止/跳过），用于让人工干预即时生效。

    并发：_WORKERS 路同时请求。慢的是「每句一次网络往返」，串行等于把延迟相加；
          并发后仍按原顺序拼接，时间轴不受影响。
    """
    narrator_cfg = voice_map.get("旁白", {
        "voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "-5Hz",
    })

    # ① 展开成「句 × 段」任务清单（长句会被 _clean_text 再切）
    tasks: list[dict] = []
    for i, line in enumerate(script):
        cfg = voice_map.get(line["role"], narrator_cfg)
        for ci, chunk in enumerate(_clean_text(line["text"])):
            tasks.append({
                "line": i, "chunk": ci, "role": line["role"], "text": chunk,
                "voice": cfg["voice"], "rate": cfg["rate"], "pitch": cfg["pitch"],
            })

    stats = {"total": len(tasks), "ok": 0, "failed": 0, "failed_samples": []}
    if not tasks:
        return [], [], stats

    # ② 并发合成（信号量控并发，control 决定暂停/中断）
    results: list[str | None] = [None] * len(tasks)
    sem = asyncio.Semaphore(max(1, _WORKERS))
    counter = {"done": 0}

    async def worker(idx: int, t: dict) -> None:
        async with sem:
            if control is not None:
                await control()          # 暂停时在此等待；停止/跳过时抛异常
            out = os.path.join(tmpdir, f"{t['line']:05d}_{t['chunk']}_{t['role']}.mp3")
            ok = await _synthesize_line(t["text"], t["voice"], t["rate"], t["pitch"], out)
            results[idx] = out if ok else None
            counter["done"] += 1
            if progress_cb:
                progress_cb(counter["done"] / len(tasks),
                            f"合成 {counter['done']}/{len(tasks)} 句 [{t['role']}]")
            if _INTERVAL:
                await asyncio.sleep(_INTERVAL)   # 限速：并发下仍控制总请求频率

    running = [asyncio.create_task(worker(i, t)) for i, t in enumerate(tasks)]
    try:
        await asyncio.gather(*running)
    except SynthesisAborted:
        for job in running:                  # 中断：把还在飞的请求全部取消
            job.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        raise

    # ③ 按原顺序读时长、拼时间轴（纯 Python 解析帧头，不起子进程）
    paths: list[str] = []
    timings: list[dict] = []
    cumulative_ms = 0.0
    line_audio: list[tuple[int, float]] = []      # (script 行号, 时长ms)

    for idx, t in enumerate(tasks):
        out = results[idx]
        if not out:
            stats["failed"] += 1
            if len(stats["failed_samples"]) < 5:
                stats["failed_samples"].append(t["text"][:30])
            continue
        ms = mp3_duration_ms(out)
        if ms <= 0:
            stats["failed"] += 1
            continue
        stats["ok"] += 1
        if line_audio and line_audio[-1][0] == t["line"]:
            line_audio[-1] = (t["line"], line_audio[-1][1] + ms)
        else:
            line_audio.append((t["line"], ms))
        paths.append(out)

    for n, (line_i, duration_ms) in enumerate(line_audio):
        line = script[line_i]
        timings.append({
            "start": cumulative_ms,
            "end": cumulative_ms + duration_ms,
            "role": line["role"],
            "text": line["text"],
        })
        cumulative_ms += duration_ms

        # 行间停顿：旁白后最长，换角色次之，同角色最短
        if n < len(line_audio) - 1:
            next_role = script[line_audio[n + 1][0]]["role"]
            if line["role"] == "旁白":
                ms = PAUSE_AFTER_NARRATION
            elif next_role != line["role"]:
                ms = PAUSE_ROLE_CHANGE
            else:
                ms = PAUSE_SAME_ROLE
            paths.append(_silence_path(ms))
            cumulative_ms += ms

    return paths, timings, stats


# ─────────────────────────────────────────────────────
# 音频拼接
# ─────────────────────────────────────────────────────
def merge_audio(paths: list[str], output_file: str) -> float:
    """拼接所有片段，导出 192kbps MP3，返回时长（秒）

    用 ffmpeg 的 concat 分离器一次搞定：旧写法对每个片段 `AudioSegment.from_mp3`
    再相加，等于每句起一个 ffmpeg 进程（~150ms/句），整章能白等几十秒。
    """
    listfile = output_file + ".concat.txt"
    try:
        with open(listfile, "w", encoding="utf-8") as f:
            for p in paths:
                f.write("file '" + p.replace("'", "'\\''") + "'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", listfile, "-ar", "24000", "-ac", "1", "-b:a", "192k", output_file],
            check=True, capture_output=True,
        )
        return mp3_duration_ms(output_file) / 1000.0
    except (OSError, subprocess.CalledProcessError):
        # 兜底：ffmpeg 不可用时退回 pydub 逐段解码
        combined = AudioSegment.empty()
        for p in paths:
            combined += AudioSegment.from_mp3(p)
        combined.export(output_file, format="mp3", bitrate="192k")
        return len(combined) / 1000.0
    finally:
        if os.path.exists(listfile):
            os.remove(listfile)


# ─────────────────────────────────────────────────────
# 核心生成函数（供 CLI 和 UI 共用）
# ─────────────────────────────────────────────────────
async def generate(
    novel_path: str,
    start: int,
    end: int,
    name: str,
    voice_map: dict,
    output_dir: str = "output",
    progress_cb: Callable | None = None,
    control: Callable | None = None,
) -> tuple[str, float, list[dict]]:
    """合成有声书片段，返回 (output_file, duration_seconds, timings)

    control: 见 synthesize_script；抛 SynthesisAborted 时本章不会落盘。
    """
    with open(novel_path, encoding="utf-8") as f:
        all_lines = f.readlines()

    excerpt = "".join(all_lines[start:end])
    script = parse_novel_to_script(excerpt, voice_map)

    if progress_cb:
        progress_cb(0.0, f"剧本解析完成，共 {len(script)} 句")

    tmpdir = tempfile.mkdtemp()
    try:
        paths, timings, stats = await synthesize_script(script, voice_map, tmpdir,
                                                        progress_cb, control)
        if stats["total"] == 0:
            raise RuntimeError("没有可合成的文本（检查起止行号是否落在正文上）")
        if stats["ok"] == 0:
            hint = ("当前用的是 edge-tts（在线）：多半被限流、DNS 解析不到或断网。\n"
                    "  · 稍后重跑即可（已成功句子有缓存，不会重复消耗接口）\n"
                    "  · 想立刻离线出片：加 --engine say（macOS 系统语音，音质差些）")
            if _ENGINE == "say":
                hint = "当前用的是离线 say 引擎：检查系统是否装有中文声音（系统设置→辅助功能→朗读内容）。"
            raise RuntimeError(f"全部 {stats['total']} 句合成失败。\n  {hint}")

        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{name}.mp3")
        if progress_cb:
            progress_cb(0.95, "拼接音频中...")
        duration = merge_audio(paths, output_file)

        # 时间轴锚定：纯 Python 算时长会比实际播放略长（LAME 编码延迟被播放器裁掉），
        # 每句几十毫秒，几百句就会累积成好几秒。用成品真实时长做一次等比校正，
        # 保证最后一句正好落在音频结尾，同步字幕不会越播越偏。
        if timings and duration > 0:
            est_end = timings[-1]["end"] / 1000.0
            if est_end > 0 and abs(est_end - duration) / duration > 0.005:
                k = duration / est_end
                for t in timings:
                    t["start"] *= k
                    t["end"] *= k

        if progress_cb and stats["failed"]:
            progress_cb(0.98, f"注意：{stats['failed']}/{stats['total']} 句失败，已跳过")
        return output_file, duration, timings
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="通用有声书生成器（增强版）")
    parser.add_argument("--input", default="data/novel.txt")
    parser.add_argument("--output", default="output")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=500)
    parser.add_argument("--name", default="片段")
    parser.add_argument("--voice-map", default=None)
    parser.add_argument("--interval", type=float, default=None, help="句间间隔秒数（默认 1.2）")
    parser.add_argument("--workers", type=int, default=None,
                        help="并发路数（默认 4）；被限流时调小，网络好可调大")
    parser.add_argument("--engine", choices=["edge", "say"], default=None,
                        help="edge=微软在线（默认）；say=macOS 离线语音")
    args = parser.parse_args()

    if args.interval is not None:
        global _INTERVAL
        _INTERVAL = args.interval
    if args.workers is not None:
        global _WORKERS
        _WORKERS = max(1, args.workers)
    if args.engine is not None:
        global _ENGINE
        _ENGINE = args.engine

    if not Path(args.input).exists():
        print(f"找不到文件：{args.input}")
        return 1

    if args.voice_map and Path(args.voice_map).exists():
        with open(args.voice_map, encoding="utf-8") as f:
            voice_map = json.load(f)
        print(f"使用配置：{args.voice_map}")
    else:
        print("自动分析角色...")
        voice_map = analyze(args.input)
        print(f"识别到 {len(voice_map) - 1} 个角色：{[k for k in voice_map if k != '旁白']}")

    def cli_progress(v, desc):
        print(f"  {desc}", flush=True)

    try:
        output_file, duration, _ = await generate(
            novel_path=args.input, start=args.start, end=args.end, name=args.name,
            voice_map=voice_map, output_dir=args.output, progress_cb=cli_progress,
        )
    except RuntimeError as e:
        print(f"\n❌ {e}")
        return 1

    size_kb = os.path.getsize(output_file) / 1024
    print("\n完成！")
    print(f"  输出：{output_file}")
    print(f"  时长：{int(duration // 60)}分{int(duration % 60)}秒")
    print(f"  大小：{size_kb:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
