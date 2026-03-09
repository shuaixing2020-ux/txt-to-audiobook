"""
通用有声书生成器
---------------
将任意中文小说 txt 合成为多角色 MP3 有声书。

使用方法：
    # 自动分析角色后合成
    python scripts/audiobook_generator.py --input data/novel.txt --start 0 --end 500 --name 第一章

    # 使用已有声音配置
    python scripts/audiobook_generator.py --input data/novel.txt --start 0 --end 500 --name 第一章 --voice-map config/voice_map.json
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import re
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pydub import AudioSegment
import edge_tts

from analyze_characters import analyze
from parse_script import parse_novel_to_script

# 停顿时长（毫秒）
PAUSE_AFTER_NARRATION = 420
PAUSE_ROLE_CHANGE     = 260
PAUSE_SAME_ROLE       = 160


# ─────────────────────────────────────────────────────
# TTS 合成
# ─────────────────────────────────────────────────────
_PUNCT_ONLY = re.compile(r'^[\s\u3000\u00a0，。！？、；：""''「」『』【】…—·\.,!?;:\-\(\)\[\]]+$')
_MAX_TTS_CHARS = 500  # edge-tts 单次建议上限


def _clean_text(text: str) -> list[str]:
    """
    清洗文本，返回可安全合成的片段列表。
    - 过滤纯标点/空白
    - 超长文本按句子分割
    """
    text = text.strip()
    if not text or _PUNCT_ONLY.match(text):
        return []
    if len(text) <= _MAX_TTS_CHARS:
        return [text]
    # 按句子边界分割
    chunks, current = [], ""
    for sent in re.split(r'(?<=[。！？\n])', text):
        if len(current) + len(sent) > _MAX_TTS_CHARS:
            if current:
                chunks.append(current.strip())
            current = sent
        else:
            current += sent
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c and not _PUNCT_ONLY.match(c)]


async def _synthesize_line(text: str, voice: str, rate: str, pitch: str, out_path: str):
    """合成单句，失败时自动重试（最多 3 次，指数退避）"""
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
            await communicate.save(out_path)
            return
        except Exception as e:
            if attempt == 2:
                raise
            wait = 2 ** attempt  # 1s, 2s
            await asyncio.sleep(wait)


async def synthesize_script(
    script: list[dict],
    voice_map: dict,
    tmpdir: str,
    progress_cb: Callable | None = None,
) -> tuple[list[str], list[dict]]:
    """合成所有台词，返回 (mp3文件路径列表, 时间轴列表)
    时间轴格式：[{start: ms, end: ms, role: str, text: str}, ...]
    """
    paths: list[str] = []
    timings: list[dict] = []
    cumulative_ms: float = 0.0
    total = len(script)

    # 旁白兜底配置
    narrator_cfg = voice_map.get("旁白", {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate":  "+0%",
        "pitch": "-5Hz",
    })

    for i, line in enumerate(script):
        role = line["role"]
        text = line["text"]
        cfg  = voice_map.get(role, narrator_cfg)

        chunks = _clean_text(text)
        if not chunks:
            continue  # 纯标点或空行，跳过

        line_start_ms = cumulative_ms
        line_duration_ms = 0.0

        for ci, chunk in enumerate(chunks):
            out = os.path.join(tmpdir, f"{i:05d}_{ci}_{role}.mp3")
            try:
                await _synthesize_line(chunk, cfg["voice"], cfg["rate"], cfg["pitch"], out)
                seg = AudioSegment.from_mp3(out)
                line_duration_ms += len(seg)
                paths.append(out)
            except Exception:
                pass  # 单句失败不中断整体
            await asyncio.sleep(0.5)  # 避免触发 Microsoft TTS 限流

        if line_duration_ms > 0:
            timings.append({
                "start": line_start_ms,
                "end":   line_start_ms + line_duration_ms,
                "role":  role,
                "text":  text,
            })
            cumulative_ms += line_duration_ms

        if progress_cb:
            progress_cb(i / total, f"合成第 {i+1}/{total} 句 [{role}]")

        # 插入停顿
        if i < total - 1:
            next_role = script[i + 1]["role"]
            if role == "旁白":
                ms = PAUSE_AFTER_NARRATION
            elif next_role != role:
                ms = PAUSE_ROLE_CHANGE
            else:
                ms = PAUSE_SAME_ROLE

            pause      = AudioSegment.silent(duration=ms)
            pause_path = os.path.join(tmpdir, f"{i:05d}_pause.mp3")
            pause.export(pause_path, format="mp3")
            paths.append(pause_path)
            cumulative_ms += ms

    return paths, timings


# ─────────────────────────────────────────────────────
# 音频拼接
# ─────────────────────────────────────────────────────
def merge_audio(paths: list[str], output_file: str) -> float:
    """拼接所有片段，导出 192kbps MP3，返回时长（秒）"""
    combined = AudioSegment.empty()
    for p in paths:
        combined += AudioSegment.from_mp3(p)
    combined.export(output_file, format="mp3", bitrate="192k")
    return len(combined) / 1000.0


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
) -> tuple[str, float, list[dict]]:
    """
    合成有声书片段。
    返回 (output_file_path, duration_seconds, timings)
    """
    with open(novel_path, encoding="utf-8") as f:
        all_lines = f.readlines()

    excerpt = "".join(all_lines[start:end])
    script  = parse_novel_to_script(excerpt, voice_map)

    if progress_cb:
        progress_cb(0.0, f"剧本解析完成，共 {len(script)} 句")

    tmpdir = tempfile.mkdtemp()
    try:
        paths, timings = await synthesize_script(script, voice_map, tmpdir, progress_cb)

        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{name}.mp3")

        if progress_cb:
            progress_cb(0.95, "拼接音频中...")

        duration = merge_audio(paths, output_file)
        return output_file, duration, timings
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="通用有声书生成器")
    parser.add_argument("--input",     default="data/novel.txt", help="小说 txt 文件路径")
    parser.add_argument("--output",    default="output",         help="输出目录")
    parser.add_argument("--start",     type=int, default=0,      help="起始行（0-indexed）")
    parser.add_argument("--end",       type=int, default=500,    help="结束行")
    parser.add_argument("--name",      default="片段",           help="输出文件名前缀")
    parser.add_argument("--voice-map", default=None,             help="声音配置 JSON 路径（不指定则自动分析）")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"找不到文件：{args.input}")
        return

    if args.voice_map and Path(args.voice_map).exists():
        with open(args.voice_map, encoding="utf-8") as f:
            voice_map = json.load(f)
        print(f"使用配置：{args.voice_map}")
    else:
        print("自动分析角色...")
        voice_map = analyze(args.input)
        chars = [k for k in voice_map if k != "旁白"]
        print(f"识别到 {len(chars)} 个角色：{chars}")

    def cli_progress(v, desc):
        print(f"  {desc}")

    output_file, duration, _ = await generate(
        novel_path=args.input,
        start=args.start,
        end=args.end,
        name=args.name,
        voice_map=voice_map,
        output_dir=args.output,
        progress_cb=cli_progress,
    )

    size_kb = os.path.getsize(output_file) / 1024
    print(f"\n完成！")
    print(f"  输出：{output_file}")
    print(f"  时长：{int(duration // 60)}分{int(duration % 60)}秒")
    print(f"  大小：{size_kb:.0f} KB")


if __name__ == "__main__":
    asyncio.run(main())
