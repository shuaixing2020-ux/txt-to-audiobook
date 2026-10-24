"""
整本章节批处理（batch）
----------------------
把 samples/ 里切好的章节依次合成，已存在的输出自动跳过（可断点续跑）。
每次任务失败不会中断整体，最后给出汇总表与失败清单。

用法：
    # 先切章：.venv/bin/python scripts/split.py data/novel.txt --write
    .venv/bin/python scripts/batch.py                 # 合成 samples/ 全部章节
    .venv/bin/python scripts/batch.py --limit 1       # 只跑第一章（试音）
    .venv/bin/python scripts/batch.py --interval 2    # 放慢节奏，规避限流
    .venv/bin/python scripts/batch.py --force         # 已存在也重合成

说明：单章失败多为 edge-tts 限流。稍后重跑即可——逐句缓存（.cache/tts）
会让已完成句子直接复用，不会重复合成。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import audiobook_generator as gen  # noqa: E402
from scripts.analyze_characters import analyze  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    ap = argparse.ArgumentParser(description="整本批量合成")
    ap.add_argument("--samples", default=os.path.join(ROOT, "samples"))
    ap.add_argument("--output", default=os.path.join(ROOT, "output"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 章（0=全部）")
    ap.add_argument("--interval", type=float, default=None, help="句间间隔（秒），默认 1.2")
    ap.add_argument("--force", action="store_true", help="已存在输出也重新合成")
    ap.add_argument("--voice-map", default=os.path.join(ROOT, "config", "voice_map.json"),
                    help="角色声音配置；不存在则用第一章自动分析并保存")
    return ap.parse_args()


def load_or_make_voice_map(sample_path: str, cfg_path: str) -> dict:
    """角色→声音映射尽量全书统一：优先读配置，其次生成并落盘"""
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            vm = json.load(f)
        print(f"使用已有声音配置：{cfg_path}")
        return vm
    print("首次运行：分析角色并保存声音配置（全书统一）…")
    vm = analyze(sample_path)
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(vm, f, ensure_ascii=False, indent=2)
    print(f"已保存：{cfg_path}")
    return vm


async def run() -> int:
    args = parse_args()
    if args.interval is not None:
        gen._INTERVAL = args.interval

    files = sorted(f for f in os.listdir(args.samples) if f.endswith(".txt")) \
        if os.path.isdir(args.samples) else []
    if not files:
        print(f"❌ {args.samples} 下没有 txt。先运行：\n"
              "   .venv/bin/python scripts/split.py data/novel.txt --write")
        return 1
    if args.limit:
        files = files[: args.limit]

    os.makedirs(args.output, exist_ok=True)
    voice_map = load_or_make_voice_map(os.path.join(args.samples, files[0]), args.voice_map)

    results = []
    for idx, fn in enumerate(files, 1):
        name = os.path.splitext(fn)[0]
        out_path = os.path.join(args.output, f"{name}.mp3")
        if os.path.exists(out_path) and not args.force:
            print(f"[{idx}/{len(files)}] 跳过（已存在）{name}.mp3")
            results.append((name, "skip", out_path))
            continue
        print(f"\n[{idx}/{len(files)}] 合成 {name} …")
        try:
            out, duration, _ = await gen.generate(
                novel_path=os.path.join(args.samples, fn),
                start=0,
                end=10 ** 7,
                name=name,
                voice_map=voice_map,
                output_dir=args.output,
                progress_cb=lambda v, d: None,
            )
            print(f"   ✅ {os.path.basename(out)}  {int(duration // 60)}分{int(duration % 60)}秒  "
                  f"{os.path.getsize(out) / 1024:.0f}KB")
            results.append((name, "ok", out))
        except Exception as e:  # noqa: BLE001
            print(f"   ❌ 失败：{e}")
            results.append((name, "fail", str(e)))

    ok = sum(1 for _, s, _ in results if s == "ok")
    skip = sum(1 for _, s, _ in results if s == "skip")
    fail = [(n, d) for n, s, d in results if s == "fail"]
    print(f"\n{'=' * 46}\n完成：成功 {ok} / 跳过 {skip} / 失败 {len(fail)} / 共 {len(results)}")
    for n, d in fail:
        print(f"  ❌ {n}: {d}")
    if fail:
        print("\n直接重跑本命令即可续传（已合成句子有缓存，不会重复消耗接口）。")
    return 0 if not fail else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
