"""解析器回归测试：python -m scripts.test_parse"""
from __future__ import annotations

import os

from scripts.analyze_characters import analyze
from scripts.parse_script import parse_novel_to_script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "data", "sample.txt")


def test_complex_line():
    """自带说话人 + 一句多段对话，应全部归到正确角色"""
    vm = {"旁白": {"gender": "female"}, "陈默": {"gender": "male"}, "林晚": {"gender": "female"}}
    text = "“你怎么又淋成这样？”陈默说：“外面雨这么大。”\n“我怕你等急了。”林晚说。"
    script = parse_novel_to_script(text, vm)
    roles = [s["role"] for s in script]
    assert roles == ["陈默", "陈默", "林晚"], roles
    assert script[1]["text"] == "外面雨这么大。"


def test_plain_narration_and_pronoun():
    vm = {"旁白": {"gender": "female"}, "苏晴": {"gender": "female"}}
    script = parse_novel_to_script("雨下得很大。\n苏晴说：“先喝点热的吧。”\n她说：“太晚了。”", vm)
    assert [s["role"] for s in script] == ["旁白", "苏晴", "苏晴"], script


def test_analyze_drops_fake_names():
    if not os.path.exists(SAMPLE):
        print("跳过：data/sample.txt 不存在")
        return
    names = [n for n in analyze(SAMPLE) if n != "旁白"]
    for bad in ("苏晴低声", "陈默忽然", "他苦笑", "”陈默", "轻轻"):
        assert bad not in names, f"假角色未被过滤：{bad} in {names}"
    assert {"陈默", "林晚", "苏晴"} <= set(names), names


def test_female_lead_gets_female_voice():
    """女角色不能被分到男声池（历史上苏晴被判成男声）"""
    if not os.path.exists(SAMPLE):
        print("跳过：data/sample.txt 不存在")
        return
    from scripts.analyze_characters import EXTRA_VOICES, FEMALE_VOICES

    vm = analyze(SAMPLE)
    female_pool = {v[0] for v in FEMALE_VOICES} | {
        k for k, g in EXTRA_VOICES.items() if g == "female" and k.startswith("zh-CN")
    }
    assert vm["苏晴"]["gender"] == "female", vm["苏晴"]
    assert vm["苏晴"]["voice"] in female_pool, vm["苏晴"]
    # 旁白始终独立，不占用角色声音池
    assert vm["旁白"]["role"] == "narrator"


def test_no_voice_collision():
    """角色不能和旁白共用音色，角色之间也不能撞音色"""
    if not os.path.exists(SAMPLE):
        print("跳过：data/sample.txt 不存在")
        return
    vm = analyze(SAMPLE)
    voices = [cfg["voice"] for cfg in vm.values()]
    assert len(voices) == len(set(voices)), f"音色重复：{voices}"
    assert vm["旁白"]["voice"] not in {cfg["voice"] for k, cfg in vm.items() if k != "旁白"}


if __name__ == "__main__":
    for fn in (test_complex_line, test_plain_narration_and_pronoun,
               test_analyze_drops_fake_names, test_female_lead_gets_female_voice,
               test_no_voice_collision):
        fn()
        print(f"✅ {fn.__name__}")
    print("全部通过")
