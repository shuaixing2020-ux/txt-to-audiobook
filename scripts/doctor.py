#!/usr/bin/env python3
"""
环境自检（doctor）
------------------
一条命令体检：Python / 依赖 / ffmpeg / edge-tts 联网 / UTF-8 编码 / 端口占用。
用法：.venv/bin/python scripts/doctor.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, BAD, WARN = "✅", "❌", "⚠️ "
_failed = 0


def check(name: str, fn) -> None:
    """跑一项检查；返回 (状态, 详情) 元组，状态为 ok/warn/fail"""
    global _failed
    try:
        status, detail = fn()
    except Exception as e:  # noqa: BLE001
        status, detail = "fail", f"{type(e).__name__}: {e}"
    icon = {"ok": OK, "warn": WARN, "fail": BAD}[status]
    if status == "fail":
        _failed += 1
    print(f"{icon} {name:<16} {detail}")


# ── 各项检查 ─────────────────────────────────────────
def c_python():
    v = sys.version_info
    if v < (3, 9):
        return "fail", f"Python {v.major}.{v.minor} 过低，需要 ≥3.9"
    if v >= (3, 13):
        return "warn", f"Python {v.major}.{v.minor}（gradio 6 可运行，但 3.10/3.11 最稳）"
    return "ok", f"Python {v.major}.{v.minor}.{v.micro}"


def c_venv():
    if sys.prefix != sys.base_prefix:
        return "ok", sys.prefix
    return "warn", "当前未在 .venv 内运行（建议 .venv/bin/python scripts/doctor.py）"


def c_deps():
    import edge_tts, gradio, jieba, pandas, pydub  # noqa: F401
    return "ok", f"gradio {gradio.__version__} / edge-tts {edge_tts.__version__} / jieba {jieba.__version__} / pandas {pandas.__version__}"


def c_ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        return "fail", "未找到 ffmpeg（brew install ffmpeg）"
    out = subprocess.run([path, "-version"], capture_output=True, text=True).stdout
    return "ok", f"{out.splitlines()[0][:60]}  ({path})"


def c_pydub():
    from pydub import AudioSegment
    seg = AudioSegment.silent(duration=200)
    p = os.path.join(tempfile.mkdtemp(), "t.mp3")
    seg.export(p, format="mp3")
    back = len(AudioSegment.from_mp3(p))
    return "ok", f"pydub 读写 mp3 正常（200ms 静音回读 {back}ms）"


def _offline_engine() -> bool:
    return os.environ.get("AUDIOBOOK_ENGINE", "edge").lower() == "say"


def c_engine():
    """当前合成引擎 + 离线引擎是否可用"""
    eng = os.environ.get("AUDIOBOOK_ENGINE", "edge").lower()
    if eng == "say":
        try:
            sys.path.insert(0, ROOT)
            from scripts.tts_local import available
            if not available():
                return "fail", "离线引擎要求 macOS 且有 say 命令"
        except Exception as e:  # noqa: BLE001
            return "fail", f"离线引擎不可用：{e}"
        return "ok", "say（macOS 系统语音，离线）"
    return "ok", "edge（微软在线）"


def c_dns():
    """edge-tts 的域名必须先能解析，否则合成必然超时"""
    host = "speech.platform.bing.com"
    t0 = time.time()
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        msg = (f"{host} 解析失败（DNS 异常）。可尝试把 DNS 改成 223.5.5.5 / "
               f"114.114.114.114，或接入代理后重试")
        if _offline_engine():
            return "warn", msg + "｜当前用离线引擎，不影响出片（只是音质差些）"
        return "fail", msg
    return "ok", f"{host} → {ip}（{time.time() - t0:.2f}s）"


def c_tts():
    import edge_tts
    text, voice = "自检通过。", "zh-CN-XiaoxiaoNeural"
    p = os.path.join(tempfile.mkdtemp(), "t.mp3")
    t0 = time.time()
    try:
        asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, voice).save(p), timeout=40))
    except asyncio.TimeoutError:
        msg = ("连接微软 TTS 超时。常见原因：①DNS 解析不到（先看上一项）"
               "②网络受限/需代理 ③短时间请求过多被限流，等 5~10 分钟再试")
        if _offline_engine():
            return "warn", msg + "｜当前用离线引擎，可正常出片"
        return "fail", msg + "｜或改用离线引擎：AUDIOBOOK_ENGINE=say"
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e)[:70]}"
        if _offline_engine():
            return "warn", msg + "｜当前用离线引擎，可正常出片"
        return "fail", msg
    size = os.path.getsize(p) if os.path.exists(p) else 0
    if size == 0:
        return "fail", "合成结果为空（多半被限流或鉴权失败）"
    return "ok", f"合成成功 {size} B / {time.time() - t0:.1f}s（微软在线服务可用）"


def c_port():
    port = int(os.environ.get("AUDIOBOOK_PORT", "7860"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    return ("warn", f"{port} 已被占用（服务可能已在运行）") if in_use else ("ok", f"{port} 空闲")


def c_texts():
    """检查 data/ 下小说编码"""
    d = os.path.join(ROOT, "data")
    files = [f for f in os.listdir(d) if f.lower().endswith(".txt")] if os.path.isdir(d) else []
    if not files:
        return "warn", "data/ 下还没有 txt"
    good, bad = [], []
    for f in files:
        try:
            with open(os.path.join(d, f), "rb") as fh:
                fh.read().decode("utf-8")
            good.append(f)
        except UnicodeDecodeError:
            bad.append(f)
    if bad:
        return "warn", f"非 UTF-8（需转码）：{bad}"
    return "ok", f"{len(good)} 个 txt 均为 UTF-8"


FAST_CHECKS = [
    ("Python 版本", c_python), ("虚拟环境", c_venv), ("Python 依赖", c_deps),
    ("ffmpeg", c_ffmpeg), ("pydub 音频", c_pydub), ("合成引擎", c_engine),
    ("TTS 域名", c_dns), ("端口", c_port),
]


def run_checks(include_network: bool = False) -> list[dict]:
    """给网页「诊断」用：返回 [{name, status, detail}]，不打印"""
    items = []
    for name, fn in FAST_CHECKS + ([("TTS 联网", c_tts)] if include_network else []):
        try:
            status, detail = fn()
        except Exception as e:  # noqa: BLE001
            status, detail = "fail", f"{type(e).__name__}: {e}"
        items.append({"name": name, "status": status, "detail": detail})
    return items


def main() -> int:
    print(f"工作目录：{ROOT}\n")
    for name, fn in [
        ("Python 版本", c_python), ("虚拟环境", c_venv), ("Python 依赖", c_deps),
        ("ffmpeg", c_ffmpeg), ("pydub 音频", c_pydub), ("合成引擎", c_engine),
        ("TTS 域名", c_dns),
        ("TTS 联网", c_tts), ("端口", c_port), ("小说编码", c_texts),
    ]:
        check(name, fn)
    print()
    if _failed:
        print(f"❌ {_failed} 项检查失败（多半是 TTS 网络）。")
        print("   应急方案：AUDIOBOOK_ENGINE=say .venv/bin/python app.py  # 离线出片")
        return 1
    if _offline_engine():
        print("✅ 离线引擎就绪：AUDIOBOOK_ENGINE=say .venv/bin/python app.py")
    else:
        print("✅ 全部关键检查通过，可以启动：.venv/bin/python app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
