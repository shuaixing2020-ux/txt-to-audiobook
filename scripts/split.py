#!/usr/bin/env python3
"""
章节切割（split）
-----------------
按章节标题把整本小说切成小样文件，避免手工数行号。
产物写到 samples/ 目录：第001章.txt 这样，可直接喂给 audiobook_generator.py。

用法：
    .venv/bin/python scripts/split.py data/novel.txt              # 预览目录，不写文件
    .venv/bin/python scripts/split.py data/novel.txt --write      # 实际切分
    .venv/bin/python scripts/split.py data/novel.txt --write --max-chars 12000
    .venv/bin/python scripts/split.py data/novel.txt --write --pattern '^第.+[章节回]'
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_PATTERN = r"^\s*(?:第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章节回卷]|序章|楔子|尾声|番外)"


def read_text(path: str) -> str:
    """读文件，UTF-8 失败时自动按 GB18030 解码并提示"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
            if enc not in ("utf-8-sig", "utf-8"):
                print(f"⚠️  检测到 {enc} 编码，已自动解码（建议另存为 UTF-8）")
            return text
        except UnicodeDecodeError:
            continue
    raise SystemExit("❌ 无法识别文件编码，请先转成 UTF-8")


def split_chapters(text: str, pattern: str, max_chars: int) -> list[tuple[str, str]]:
    """返回 [(章节标题, 正文)]；超长章节按 max_chars 再切分"""
    head_re = re.compile(pattern)
    lines = text.splitlines()

    chapters: list[tuple[str, list[str]]] = []
    current_title, current_lines = "前言", []
    for line in lines:
        if head_re.match(line) and len(line.strip()) <= 40:
            chapters.append((current_title, current_lines))
            current_title, current_lines = line.strip(), [line]
        else:
            current_lines.append(line)
    chapters.append((current_title, current_lines))

    out: list[tuple[str, str]] = []
    for title, ls in chapters:
        body = "\n".join(ls).strip()
        if not body:
            continue
        if len(body) <= max_chars:
            out.append((title, body))
            continue
        # 超长章节按段落切成若干段
        part, buf = 1, []
        for para in body.splitlines():
            buf.append(para)
            if sum(len(x) for x in buf) >= max_chars:
                out.append((f"{title}_part{part}", "\n".join(buf)))
                part, buf = part + 1, []
        if buf:
            out.append((f"{title}_part{part}", "\n".join(buf)))
    return out


def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")[:40] or "未命名"


def main() -> int:
    ap = argparse.ArgumentParser(description="按章节标题切割小说")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "samples"))
    ap.add_argument("--pattern", default=DEFAULT_PATTERN)
    ap.add_argument("--max-chars", type=int, default=8000,
                    help="单段最大字数，超过则再切（默认 8000，约 12~15 分钟音频）")
    ap.add_argument("--write", action="store_true", help="真正写文件")
    args = ap.parse_args()

    text = read_text(args.input)
    chapters = split_chapters(text, args.pattern, args.max_chars)
    total = sum(len(b) for _, b in chapters)
    print(f"共 {len(chapters)} 段，合计 {total} 字\n")

    for i, (title, body) in enumerate(chapters, 1):
        # 统一三位序号命名，保证文件排序正确
        print(f"{i:03d}  {title:<30} {len(body):>6} 字")
        if args.write:
            os.makedirs(args.out_dir, exist_ok=True)
            fn = f"{i:03d}_{safe_name(title)}.txt"
            with open(os.path.join(args.out_dir, fn), "w", encoding="utf-8") as f:
                f.write(body)

    if args.write:
        print(f"\n✅ 已写入 {args.out_dir}")
        print("下一步（先跑第一段试听）：")
        print(f"  .venv/bin/python scripts/audiobook_generator.py --input {args.out_dir}/001_*.txt --start 0 --end 200 --name 第001章")
    else:
        print("\n（预览模式，未写文件；加 --write 实际切分）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
