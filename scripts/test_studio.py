"""服务层回归测试：python -m scripts.test_studio

只测纯逻辑与文件操作，不联网、不合成音频（合成链路见 test_pipeline.py）。
所有写操作都指向临时目录，不会碰到真实的 samples/ output/ config/。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

from scripts import studio


def _make_book(n_chapters: int = 3, encoding: str = "gb18030") -> str:
    body = "雨下得很大。\n“你怎么又淋成这样？”陈默说：“外面雨这么大。”\n林晚笑了笑。\n"
    text = "".join(f"第{i}章 测试章节{i}\n{body}" for i in range(1, n_chapters + 1))
    tmp = os.path.join(tempfile.mkdtemp(prefix="studio_test_"), "book.txt")
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
    return tmp


def _isolate() -> dict:
    """把三个目录指到临时目录，返回原值以便还原"""
    original = {k: getattr(studio, k) for k in ("SAMPLES_DIR", "OUTPUT_DIR", "CONFIG_DIR", "VOICE_MAP_PATH")}
    root = tempfile.mkdtemp(prefix="studio_root_")
    studio.SAMPLES_DIR = os.path.join(root, "samples")
    studio.OUTPUT_DIR = os.path.join(root, "output")
    studio.CONFIG_DIR = os.path.join(root, "config")
    studio.VOICE_MAP_PATH = os.path.join(studio.CONFIG_DIR, "voice_map.json")
    studio.ensure_dirs()
    return original


def _restore(original: dict) -> None:
    for k, v in original.items():
        setattr(studio, k, v)


def test_preview_handles_gbk():
    """GBK 编码的书也要能切章（上传的文件常是 GBK）"""
    book = _make_book(3, encoding="gb18030")
    rows = studio.preview_rows(book)
    assert len(rows) == 3, rows
    assert rows[0][1].startswith("第1章"), rows[0]
    assert rows[0][4] == "预览", rows[0]
    assert rows[0][3].startswith("001_"), rows[0]


def test_write_chapters_moves_stale_files():
    original = _isolate()
    try:
        book = _make_book(3)
        res = studio.write_chapters(book)
        assert res["count"] == 3, res
        assert len(studio.list_chapters()) == 3, studio.list_chapters()

        # 换成只有 1 章的书：多出来的两章应移到 .stale，而不是删除
        short = _make_book(1)
        res2 = studio.write_chapters(short)
        assert res2["count"] == 1, res2
        assert len(res2["removed"]) == 2, res2
        stale = os.path.join(studio.SAMPLES_DIR, ".stale")
        assert len(os.listdir(stale)) == 2, os.listdir(stale)
        assert len(studio.list_chapters()) == 1
    finally:
        _restore(original)


def test_voice_table_roundtrip():
    """voice_map → 表格 → voice_map 必须等价（界面上改完再合成靠它）"""
    vm = {
        "旁白": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "-5Hz",
                 "gender": "female", "age": "young", "emotion": "calm",
                 "role": "narrator", "frequency": 0},
        "陈默": {"voice": "zh-CN-YunxiNeural", "rate": "+8%", "pitch": "+3Hz",
                 "gender": "male", "age": "young", "emotion": "calm",
                 "role": "protagonist", "frequency": 9},
    }
    rows = studio.voice_map_rows(vm)
    assert rows[0][0] == "旁白", rows          # 旁白置顶
    assert rows[0][4] == "旁白", rows          # 定位列
    assert studio.rows_to_voice_map(rows) == vm


def test_voice_table_tolerates_dirty_input():
    """表格里出现 NaN / 空行 / 只写后缀 / 频次写成 3.0 时不能炸"""
    rows = [
        [None] * 10,
        ["", "女", "青", "平静", "主角", "Yunxi", "", "", "", ""],
        ["苏晴", float("nan"), "少", "温柔", "配角", "XiaoyiNeural", "", "", "3.0", "小晴、晴姐"],
    ]
    vm = studio.rows_to_voice_map(rows)
    assert list(vm) == ["苏晴"], vm
    assert vm["苏晴"]["voice"] == "zh-CN-XiaoyiNeural", vm
    assert vm["苏晴"]["frequency"] == 3, vm
    assert vm["苏晴"]["gender"] == "unknown", vm
    assert vm["苏晴"]["age"] == "teen", vm
    assert vm["苏晴"]["emotion"] == "gentle", vm
    assert vm["苏晴"]["aliases"] == ["小晴", "晴姐"], vm
    assert vm["苏晴"]["rate"] == "+0%", vm


def test_restyle_by_age_emotion():
    """年龄/情绪要能改变语速音调，且可反复执行不叠加"""
    vm = {"小明": {"voice": "zh-CN-YunxiaNeural", "rate": "+0%", "pitch": "+0Hz",
                   "gender": "male", "age": "child", "emotion": "happy",
                   "role": "supporting", "frequency": 3}}
    once = studio.restyle_voice_map({k: dict(v) for k, v in vm.items()})["小明"]
    twice = studio.restyle_voice_map({k: dict(v) for k, v in vm.items()})["小明"]
    assert once["rate"] == twice["rate"] and once["pitch"] == twice["pitch"], (once, twice)
    # 小孩 + 欢快：语速明显加快、音调抬高
    assert int(once["rate"].rstrip("%")) > 0, once
    assert int(once["pitch"].rstrip("Hz")) > 0, once


def test_move_role_reorders_rows():
    rows = [["A", "", "", "", "配角"], ["B", "", "", "", "配角"], ["C", "", "", "", "配角"]]
    moved = studio.move_role(rows, "C", -1)
    assert [r[0] for r in moved] == ["A", "C", "B"], moved
    assert studio.move_role(rows, "A", -1) is None        # 已在顶部
    assert studio.move_role(rows, "不存在", 1) is None


# ─────────────────────────────────────────────────────
# 文本转译
# ─────────────────────────────────────────────────────
def test_translate_rules_produces_canonical():
    from scripts import translate

    text = ("“你怎么又淋成这样？”陈默说：“外面雨这么大。”\n"
            "林晚笑了笑，把伞收起来。\n"
            "………\n"
            "更新时间2012-12-21 18:33:21  字数：2521\n")
    vm = {"旁白": {}, "陈默": {"gender": "male"}, "林晚": {"gender": "female"}}
    lines = translate.translate_by_rules(text, vm)

    assert lines, "规则转译不应为空"
    assert all("｜" in ln for ln in lines), lines
    assert translate.is_canonical("\n".join(lines))
    # 纯标点行与非正文行要被丢掉
    assert not any(ln.split("｜", 1)[1].startswith("……") for ln in lines), lines
    assert not any("更新时间" in ln for ln in lines), lines

    back = translate.parse_canonical("\n".join(lines))
    assert len(back) == len(lines)
    assert {b["role"] for b in back} >= {"旁白", "陈默"}, back


def test_pronounce_longest_first():
    """发音词典要长词优先：重庆 → 崇庆，不能被「重」抢先替换"""
    import scripts.audiobook_generator as gen

    old = gen._PRONOUNCE
    try:
        gen._PRONOUNCE = {"重庆": "崇庆", "重": "虫"}
        assert gen.apply_pronounce("重庆很重") == "崇庆很虫"
        gen._PRONOUNCE = {}
        assert gen.apply_pronounce("重庆很重") == "重庆很重"
    finally:
        gen._PRONOUNCE = old


def test_script_preview_flags():
    """剧本预览要标出异常：未配音角色 / 过长 / 过短"""
    original = _isolate()
    try:
        with open(os.path.join(studio.SAMPLES_DIR, "001_t.txt"), "w", encoding="utf-8") as f:
            # 转译稿格式要求至少 3 行才认，这里造够行数
            f.write("旁白｜短。\n陌生人｜这句没在配音表里。\n旁白｜这一句是正常长度的叙述文本。\n")
        vm = {"旁白": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz",
                       "gender": "female", "role": "narrator", "frequency": 0}}
        p = studio.script_preview(os.path.join(studio.SAMPLES_DIR, "001_t.txt"), vm)
        flags = {r[2]: r[6] for r in p["rows"]}
        assert "未配音" in flags.get("陌生人", ""), flags
        narr = [r[6] for r in p["rows"] if r[2] == "旁白"]
        assert any("过短" in f for f in narr), narr
        assert p["stats"]["warn"] >= 2, p["stats"]
    finally:
        _restore(original)


def test_translate_chunks_and_similarity():
    from scripts import translate

    text = "\n".join("这是第%d段，内容不长。" % i for i in range(50))
    chunks = translate.split_chunks(text, 100)
    assert len(chunks) > 1
    assert all(len(c) <= 130 for c in chunks), [len(c) for c in chunks]
    # 内容守恒校验：原样保留通过，砍掉一半要判失败
    assert translate._similarity_ok(text, [text])
    assert not translate._similarity_ok(text, [text[: len(text) // 3]])


def test_translate_job_writes_file():
    """转译任务要落盘到 translated/，并能被合成端优先读取"""
    from scripts import translate

    original = _isolate()
    old_dir = translate.TRANSLATED_DIR
    translate.TRANSLATED_DIR = os.path.join(os.path.dirname(studio.SAMPLES_DIR), "translated")
    try:
        _prep_chapters(1)
        vm = {"旁白": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz",
                       "gender": "female", "role": "narrator", "frequency": 0}}

        job = studio.start_translate_job(["001_t.txt"], vm, mode="rules")
        assert _wait_until(lambda: not job.is_running(), timeout=20)
        snap = job.snapshot()
        assert snap["status"] == "done" and snap["ok"] == 1, snap

        path = translate.translated_path("001_t.txt")
        assert os.path.exists(path), path
        assert translate.load_translated("001_t.txt")

        tasks = studio.chapter_tasks(["001_t.txt"], use_translated=True)
        assert tasks[0]["translated"] is True, tasks
        tasks2 = studio.chapter_tasks(["001_t.txt"], use_translated=False)
        assert tasks2[0]["translated"] is False, tasks2
    finally:
        translate.TRANSLATED_DIR = old_dir
        _restore(original)


def test_resolve_voice_aliases():
    assert studio.resolve_voice("YunxiNeural") == "zh-CN-YunxiNeural"
    assert studio.resolve_voice("yunxi") == "zh-CN-YunxiNeural"
    assert studio.resolve_voice("zh-CN-XiaoxiaoNeural") == "zh-CN-XiaoxiaoNeural"
    assert studio.resolve_voice("zh-HK-HiuMaanNeural") == "zh-HK-HiuMaanNeural"
    assert studio.resolve_voice("") == studio.DEFAULT_VOICE
    assert studio.resolve_voice(None) == studio.DEFAULT_VOICE


def test_config_save_load():
    original = _isolate()
    try:
        assert studio.load_voice_map() is None
        vm = {"旁白": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz",
                       "gender": "female", "role": "narrator", "frequency": 0}}
        studio.save_voice_map(vm)
        assert studio.load_voice_map() == vm
    finally:
        _restore(original)


def test_engine_switch():
    original = (studio.gen._ENGINE, studio.gen._INTERVAL)
    try:
        assert studio.set_engine("say", 0.5) == ("say", 0.5)
        assert studio.set_engine("edge", 1.2) == ("edge", 1.2)
        assert studio.set_engine("bogus", None)[0] == "edge"      # 非法值回退
    finally:
        studio.gen._ENGINE, studio.gen._INTERVAL = original


# ─────────────────────────────────────────────────────
# 后台任务：停止 / 暂停 / 跳过（用假 TTS，毫秒级）
# ─────────────────────────────────────────────────────
_VM = {"旁白": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz",
                "gender": "female", "role": "narrator", "frequency": 0}}


def _with_fake_tts(fn):
    """临时把单句合成换成「睡 60ms + 写静音」，避免测试依赖 say / 网络"""
    from pydub import AudioSegment

    orig = studio.gen._synthesize_line
    orig_interval = studio.gen._INTERVAL

    async def fake(text, voice, rate, pitch, out_path):
        import asyncio
        await asyncio.sleep(0.06)
        AudioSegment.silent(duration=200).export(out_path, format="mp3")
        return True

    studio.gen._synthesize_line = fake
    studio.gen._INTERVAL = 0.0
    try:
        return fn()
    finally:
        studio.gen._synthesize_line = orig
        studio.gen._INTERVAL = orig_interval


def _prep_chapters(n: int = 1) -> None:
    book = _make_book(n)
    text = studio.read_text(book)          # 造的书是 GBK，这里走自动识别
    for i in range(1, n + 1):
        with open(os.path.join(studio.SAMPLES_DIR, f"00{i}_t.txt"), "w", encoding="utf-8") as f:
            f.write(text)


def _wait_until(pred, timeout=8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_job_stop_leaves_no_partial_file():
    """中途停止：状态变 stopped、不产出半成品 MP3、日志说清楚"""
    original = _isolate()
    try:
        _prep_chapters(1)

        def run():
            job = studio.start_job(studio.chapter_tasks(["001_t.txt"]), _VM, force=True)
            time.sleep(0.35)
            job.stop()
            assert _wait_until(lambda: not job.is_running()), "停止后 8 秒仍未退出"
            snap = job.snapshot()
            assert snap["status"] == "stopped", snap["status"]
            assert "已停止" in snap["log"], snap["log"]
            assert not os.path.exists(os.path.join(studio.OUTPUT_DIR, "001_t.mp3")), \
                "停止后不应留下半成品 MP3"
            return snap

        _with_fake_tts(run)
    finally:
        _restore(original)


def test_job_pause_freezes_progress():
    """暂停后进度停住，继续后重新推进"""
    original = _isolate()
    try:
        _prep_chapters(1)

        def run():
            job = studio.start_job(studio.chapter_tasks(["001_t.txt"]), _VM, force=True)
            assert _wait_until(lambda: job.snapshot()["progress"] > 0.05)
            job.toggle_pause()
            assert _wait_until(lambda: job.snapshot()["status"] == "paused")
            # 并发下「在飞的几句」会先跑完，等进度稳定下来再断言冻结
            last = -1.0
            while job.snapshot()["progress"] != last:
                last = job.snapshot()["progress"]
                time.sleep(0.35)
            p1 = job.snapshot()["progress"]
            time.sleep(0.6)
            assert job.snapshot()["progress"] == p1, \
                f"暂停期间进度还在动：{p1} → {job.snapshot()['progress']}"
            job.toggle_pause()
            assert _wait_until(lambda: job.snapshot()["progress"] > p1), "继续后进度没有推进"
            job.stop()
            _wait_until(lambda: not job.is_running())

        _with_fake_tts(run)
    finally:
        _restore(original)


def test_job_skip_current_chapter():
    """跳过当前章：记一次 skip，并接着做下一章"""
    original = _isolate()
    try:
        _prep_chapters(2)

        def run():
            job = studio.start_job(studio.chapter_tasks(["001_t.txt", "002_t.txt"]),
                                   _VM, force=True)
            assert _wait_until(lambda: job.snapshot()["progress"] > 0.05)
            job.skip_current()
            assert _wait_until(lambda: job.snapshot()["index"] == 2), "没有跳到第二章"
            snap = job.snapshot()
            assert snap["skip"] == 1, snap
            assert "已跳过" in snap["log"], snap["log"]
            job.stop()
            _wait_until(lambda: not job.is_running())

        _with_fake_tts(run)
    finally:
        _restore(original)


def test_second_job_is_rejected_while_running():
    original = _isolate()
    try:
        _prep_chapters(1)

        def run():
            job = studio.start_job(studio.chapter_tasks(["001_t.txt"]), _VM, force=True)
            try:
                try:
                    studio.start_job(studio.chapter_tasks(["001_t.txt"]), _VM)
                except RuntimeError as e:
                    assert "已有任务在跑" in str(e), e
                else:
                    raise AssertionError("并发启动没有被拒绝")
            finally:
                job.stop()
                _wait_until(lambda: not job.is_running())

        _with_fake_tts(run)
    finally:
        _restore(original)


if __name__ == "__main__":
    tests = [
        test_preview_handles_gbk,
        test_write_chapters_moves_stale_files,
        test_voice_table_roundtrip,
        test_voice_table_tolerates_dirty_input,
        test_resolve_voice_aliases,
        test_config_save_load,
        test_engine_switch,
        test_restyle_by_age_emotion,
        test_move_role_reorders_rows,
        test_translate_rules_produces_canonical,
        test_pronounce_longest_first,
        test_script_preview_flags,
        test_translate_chunks_and_similarity,
        test_translate_job_writes_file,
        test_job_stop_leaves_no_partial_file,
        test_job_pause_freezes_progress,
        test_job_skip_current_chapter,
        test_second_job_is_rejected_while_running,
    ]
    for fn in tests:
        fn()
        print(f"✅ {fn.__name__}")
    print(f"全部通过（{len(tests)} 项）")
