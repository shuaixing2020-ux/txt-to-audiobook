"""
离线语音引擎（macOS `say`）
--------------------------
当 edge-tts 因网络/限流不可用时，用系统自带语音离线合成，完全不出网。
质量不如 Edge TTS，但足以先听效果、验证多角色流程。

用法（一般不用单独调用；由 audiobook_generator.py 的 --engine say 触发）：
    .venv/bin/python scripts/tts_local.py --text "你好呀" --out /tmp/a.mp3

⚠️ 本机实测的坑（已修）：
    macOS 里 `Reed` / `Flo` / `Eddy` / `Rocko` / `Sandy` / `Shelley` / `Grandpa`
    都是**多语种同名音色**（德语/英语/日语/中文…各一套）。直接 `say -v Reed`
    会命中排在最前面的那个（不是中文），合成中文时静默产出 **16 毫秒**的空音频。
    所以这里先把 `say -v ?` 的输出解析成「音色名 → 中文（中国大陆）变体」，
    再用带语言后缀的完整名字去调用；并且对产出的时长做校验，
    空音频直接判失败（绝不写进缓存），必要时回退到最稳的 Tingting。
"""
from __future__ import annotations

import argparse
import functools
import os
import re
import subprocess
import sys
import tempfile

# edge-tts 声音 → macOS 音色基础名（按性别/风格粗映射）
VOICE_MAP = {
    # 女声
    "zh-CN-XiaoxiaoNeural": "Tingting",
    "zh-CN-XiaoyiNeural": "Flo",
    "zh-CN-liaoning-XiaobeiNeural": "Shelley",
    "zh-CN-shaanxi-XiaoniNeural": "Sandy",
    "zh-CN-XiaochenNeural": "Shelley",
    "zh-CN-XiaohanNeural": "Sandy",
    "zh-CN-XiaomengNeural": "Flo",
    "zh-CN-XiaomoNeural": "Grandma",
    # 男声
    "zh-CN-YunxiNeural": "Reed",
    "zh-CN-YunjianNeural": "Rocko",
    "zh-CN-YunxiaNeural": "Eddy",
    "zh-CN-YunyangNeural": "Grandpa",
    "zh-CN-YunfengNeural": "Rocko",
    "zh-CN-YunhaoNeural": "Reed",
    "zh-CN-YunyeNeural": "Eddy",
    "zh-CN-YunzeNeural": "Grandpa",
}
DEFAULT_VOICE = "Tingting"
# 最短有效音频（毫秒）：低于此值视为「没念出来」
_MIN_VALID_MS = 80


def available() -> bool:
    """macOS 且有 say 命令"""
    return sys.platform == "darwin" and subprocess.run(
        ["which", "say"], capture_output=True
    ).returncode == 0


@functools.lru_cache(maxsize=1)
def _voice_table() -> tuple[dict[str, str], set[str]]:
    """解析 `say -v ?` → ({基础名: 中文(中国大陆)完整名}, 全部音色名)"""
    zh: dict[str, str] = {}
    all_names: set[str] = set()
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    except OSError:
        return {}, set()

    for line in out.splitlines():
        m = re.match(r"^(?P<name>.+?)\s{2,}(?P<loc>[a-zA-Z]{2}_[a-zA-Z]{2})\s{2,}#", line)
        if not m:
            continue
        name, loc = m.group("name").strip(), m.group("loc")
        all_names.add(name)
        if loc == "zh_CN":
            base = name.split(" (")[0].strip()
            # 同名多语种时，保留带「中文（中国大陆）」后缀的那个
            if base not in zh or "(" in name:
                zh[base] = name
    return zh, all_names


def resolve_voice(name: str) -> str:
    """把音色名（edge-tts 名或 macOS 名）解析成确定是中文（中国大陆）的完整名字"""
    name = VOICE_MAP.get(name, name or "").strip()
    zh, all_names = _voice_table()
    base = name.split(" (")[0].strip()
    if base in zh:
        return zh[base]                    # Reed → "Reed (中文（中国大陆）)"
    if name in all_names:
        return name
    if base in all_names:
        return base
    return zh.get(DEFAULT_VOICE, DEFAULT_VOICE)


def _duration_ms(path: str) -> int:
    """用 ffprobe 读音频时长（毫秒）；读不到返回 0"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return int(float(out) * 1000)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0


def _rate_to_wpm(rate: str) -> int:
    """"+8%" → 175*1.08 wpm"""
    wpm = 175
    try:
        if rate and rate.strip().endswith("%"):
            wpm = int(175 * (1 + float(rate.strip().rstrip("%")) / 100))
    except ValueError:
        pass
    return max(110, min(320, wpm))


def _speak(text: str, voice: str, wpm: int, aiff: str) -> int:
    subprocess.run(
        ["say", "-v", voice, "-r", str(wpm), "-o", aiff, text],
        check=True, capture_output=True,
    )
    return _duration_ms(aiff)


def say_to_mp3(text: str, voice: str, rate: str, pitch: str, out_path: str) -> None:
    """
    用 macOS say 合成到 out_path（mp3）。
    voice 传 edge-tts 名称或 macOS 名称均可；rate 形如 "+8%" / "-10%"，换算成语速。
    pitch 在本引擎下忽略（say 不支持音调微调）。

    产出为空（时长过短）时依次回退，全部失败才抛异常 —— 保证不会把静音片段
    当成「合成成功」写进缓存。
    """
    wpm = _rate_to_wpm(rate)
    primary = resolve_voice(voice)
    candidates = [primary]
    fallback = resolve_voice(DEFAULT_VOICE)
    if fallback not in candidates:
        candidates.append(fallback)

    last_ms = 0
    with tempfile.TemporaryDirectory() as td:
        aiff = os.path.join(td, "seg.aiff")
        for cand in candidates:
            try:
                last_ms = _speak(text, cand, wpm, aiff)
            except subprocess.CalledProcessError:
                continue
            if last_ms >= _MIN_VALID_MS:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", aiff,
                     "-ar", "24000", "-ac", "1", "-b:a", "96k", out_path],
                    check=True, capture_output=True,
                )
                return
    raise RuntimeError(
        f"say 合成不出有效音频（最长 {last_ms}ms）：音色 {primary}。"
        f"请在「系统设置 → 辅助功能 → 朗读内容」里确认中文语音已下载。"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="macOS 离线 TTS")
    ap.add_argument("--text", required=True)
    ap.add_argument("--voice", default="Tingting")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--out", default="say_out.mp3")
    a = ap.parse_args()
    if not available():
        sys.exit("当前系统没有 say 命令（仅 macOS 支持）")
    say_to_mp3(a.text, a.voice, a.rate, "+0Hz", a.out)
    print(f"已生成 {a.out}（{os.path.getsize(a.out)} 字节 / {_duration_ms(a.out)} ms）")
