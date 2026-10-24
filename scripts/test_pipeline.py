"""
离线管道测试（不联网）
---------------------
用 pydub 生成静音片段冒充 TTS 结果，验证：
  解析 → 逐句合成循环 → 停顿插入 → 拼接导出 整条链路。
edge-tts 被限流/断网时也能确认程序本身没坏。

运行：.venv/bin/python -m scripts.test_pipeline
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from pydub import AudioSegment

from scripts import audiobook_generator as gen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample.txt")


async def fake_synth(text, voice, rate, pitch, out_path):
    """假 TTS：按字数生成对应时长的静音 mp3"""
    ms = max(300, len(text) * 120)
    AudioSegment.silent(duration=ms).export(out_path, format="mp3")
    return True


def test_clean_text():
    assert gen._clean_text("   ") == []
    assert gen._clean_text("……——") == []
    got = gen._clean_text("你好。")
    assert got == ["你好。"], got
    long_text = "这是一句话。" * 200
    chunks = gen._clean_text(long_text)
    assert len(chunks) > 1 and all(len(c) <= gen._MAX_TTS_CHARS for c in chunks)


def test_pipeline_offline():
    if not os.path.exists(SAMPLE):
        print("跳过：data/sample.txt 不存在")
        return
    orig = gen._synthesize_line
    gen._synthesize_line = fake_synth
    gen._INTERVAL = 0
    try:
        with tempfile.TemporaryDirectory() as td:
            out, duration, timings = asyncio.run(gen.generate(
                novel_path=SAMPLE, start=0, end=200, name="__pipeline_test__",
                voice_map={"旁白": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "-5Hz", "gender": "female"},
                           "陈默": {"voice": "zh-CN-YunxiNeural", "rate": "+8%", "pitch": "+3Hz", "gender": "male"},
                           "林晚": {"voice": "zh-CN-XiaoyiNeural", "rate": "+0%", "pitch": "+2Hz", "gender": "female"},
                           "苏晴": {"voice": "zh-CN-YunjianNeural", "rate": "+0%", "pitch": "-3Hz", "gender": "female"}},
                output_dir=td,
            ))
            assert os.path.exists(out), out
            size = os.path.getsize(out)
            assert size > 5000, f"输出太小：{size}B"
            assert duration > 3, duration
            assert timings and "start" in timings[0]
            roles = {t["role"] for t in timings}
            assert "旁白" in roles, roles
            print(f"   → {os.path.basename(out)}  {duration:.1f}s  {size/1024:.0f}KB  角色={roles}")
    finally:
        gen._synthesize_line = orig


def test_say_engine_speaks_chinese():
    """回归：`say -v Reed` 曾命中同名非中文音色，中文合成了 16ms 静音"""
    from scripts import tts_local

    if not tts_local.available():
        print("跳过：非 macOS 或没有 say")
        return
    sample = "你怎么又淋成这样？外面雨这么大。"
    for edge_name in ("zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural", "zh-CN-XiaochenNeural"):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "t.mp3")
            tts_local.say_to_mp3(sample, edge_name, "+0%", "+0Hz", out)
            ms = tts_local._duration_ms(out)
            assert ms > 500, f"{edge_name} 只产出 {ms}ms，多半命中了非中文同名音色"
    # 解析出来的音色名必须是「中文（中国大陆）」变体，不能是裸名字
    assert "中文（中国大陆）" in tts_local.resolve_voice("zh-CN-YunxiNeural")


if __name__ == "__main__":
    for fn in (test_clean_text, test_pipeline_offline, test_say_engine_speaks_chinese):
        fn()
        print(f"✅ {fn.__name__}")
    print("全部通过")
