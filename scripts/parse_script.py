"""
通用小说剧本解析器
-----------------
将任意中文小说 txt 解析为带角色标注的剧本列表。
角色列表由外部传入（来自 analyze_characters.py），不再硬编码。

使用方法（CLI 预览）：
    python scripts/parse_script.py --input data/novel.txt --start 0 --end 300
    python scripts/parse_script.py --input data/novel.txt --start 0 --end 300 --output preview.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 支持的引号格式：(正则, 开引号描述, 闭引号描述)
QUOTE_PATTERNS = [
    re.compile(r'\u201c(.*?)\u201d(.*)'),   # "..."  中文弯引号
    re.compile(r'\u300c(.*?)\u300d(.*)'),   # 「...」 方角引号
    re.compile(r'\u300e(.*?)\u300f(.*)'),   # 『...』 双方角引号
]

# 说话动词
SPEECH_VERB_RE = re.compile(r'说|道|问|答|笑|哭|叫|喊|回答|补充|低声|轻声|大声|嘀咕|叹|吼|嚷|反问|插嘴|开口')


def _build_speaker_strip_re(characters: list) -> re.Pattern | None:
    """构建用于清理引号后角色名前缀的正则"""
    if not characters:
        return None
    escaped = "|".join(re.escape(n) for n in characters)
    return re.compile(
        r'^(?:' + escaped + r'|我)[^\s，。！？\u201c\u201d\u300c\u300d]{0,6}[，。]?\s*'
    )


def detect_speaker(
    after_text: str,
    characters: list,
    last_speaker: str,
    protagonist: str,
) -> str:
    """
    从引号后的归属文字中识别说话人。
    characters: 角色名列表（不含"旁白"）
    """
    # 精确匹配：after_text 中直接出现角色名
    for name in characters:
        if name in after_text:
            return name

    # "我" → 映射到主角
    stripped = after_text.lstrip()
    if stripped.startswith("我") and not stripped.startswith("我们"):
        return protagonist

    # 交替推断：在主角和上一个非主角之间轮换
    non_protagonist = [c for c in characters if c != protagonist]
    if last_speaker == protagonist and non_protagonist:
        # 优先上一个配角（对话场景）
        return non_protagonist[0]
    return protagonist


def parse_novel_to_script(text: str, voice_map: dict) -> list[dict]:
    """
    将小说原文解析为剧本列表。
    每个元素：{"role": str, "text": str}

    voice_map: {name: {voice, rate, pitch, ...}}，"旁白" key 必须存在。
    """
    characters = [name for name in voice_map if name != "旁白"]
    protagonist = characters[0] if characters else "主角"
    strip_re    = _build_speaker_strip_re(characters)

    lines       = [l.strip() for l in text.splitlines()]
    script: list[dict] = []
    last_speaker = protagonist

    for line in lines:
        if not line:
            continue

        matched = False
        for pattern in QUOTE_PATTERNS:
            m = pattern.match(line)
            if m:
                dialog_text = m.group(1).strip()
                after_text  = m.group(2).strip()

                speaker = detect_speaker(after_text, characters, last_speaker, protagonist)
                last_speaker = speaker
                script.append({"role": speaker, "text": dialog_text})

                # 引号后的动作描述 → 旁白（去掉开头的"沈佳仪说道"等前缀）
                narration = strip_re.sub("", after_text).strip() if strip_re else after_text.strip()
                if narration:
                    script.append({"role": "旁白", "text": narration})

                matched = True
                break

        if not matched:
            script.append({"role": "旁白", "text": line})

    return script


# ─────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="小说剧本解析器（通用版）")
    parser.add_argument("--input",  required=True, help="小说 txt 文件")
    parser.add_argument("--start",  type=int, default=0)
    parser.add_argument("--end",    type=int, default=500)
    parser.add_argument("--output", default=None, help="输出 JSON 路径（不指定则打印预览）")
    args = parser.parse_args()

    # 自动分析角色
    from analyze_characters import analyze
    print("正在分析角色...")
    voice_map = analyze(args.input)
    print(f"识别到角色：{[k for k in voice_map if k != '旁白']}")

    with open(args.input, encoding="utf-8") as f:
        lines = f.readlines()

    text   = "".join(lines[args.start:args.end])
    script = parse_novel_to_script(text, voice_map)

    roles: dict = {}
    for s in script:
        roles[s["role"]] = roles.get(s["role"], 0) + 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"已保存：{args.output}（共 {len(script)} 句）")
    else:
        marks = {"旁白": "📖"}
        for name, cfg in voice_map.items():
            if name == "旁白":
                continue
            marks[name] = "👧" if cfg.get("gender") == "female" else "👦"

        for item in script:
            m = marks.get(item["role"], "❓")
            print(f"{m}[{item['role']}] {item['text'][:60]}")

    print(f"\n共 {len(script)} 句 | 角色分布：{roles}")


if __name__ == "__main__":
    main()
