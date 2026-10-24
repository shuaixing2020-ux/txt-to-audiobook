"""大模型识别测试（本地假服务器，不联网、不花钱）

    .venv/bin/python -m scripts.test_llm

用 http.server 起一个 OpenAI 兼容的假接口，验证：
  · 没配 key 时 ready() 为 False（自动回退本地规则）
  · 返回带 ```json 围栏 / 带解释文字时也能解析出人物表
  · 人物表 → voice_map：性别/定位/别名正确，且不与旁白撞音色
  · 接口挂掉时 analyze_source_ex 回退本地规则而不是抛错
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from scripts import llm_analyze as llm
from scripts import studio
from scripts.analyze_characters import voice_map_from_roster

ROSTER = {
    "characters": [
        {"name": "旁白", "gender": "female", "role": "narrator", "aliases": [],
         "evidence": "叙述"},
        {"name": "白小云", "gender": "male", "role": "protagonist",
         "aliases": ["小胖墩", "小白"], "evidence": "文中称他小胖墩"},
        {"name": "王静", "gender": "female", "role": "supporting",
         "aliases": ["静姐"], "evidence": "她"},
    ]
}


class _Handler(BaseHTTPRequestHandler):
    reply = "```json\n" + json.dumps(ROSTER, ensure_ascii=False) + "\n```"

    def do_POST(self):                                   # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({
            "choices": [{"message": {"content": self.reply}}]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                        # 静音
        pass


def _serve() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1"


def test_ready_without_key():
    cfg = {"preset": "zhipu", "base_url": "https://example.com/v1", "model": "m", "api_key": ""}
    ok, why = llm.ready(cfg)
    assert not ok and "API Key" in why, (ok, why)
    ok, _ = llm.ready({"preset": "ollama", "base_url": "http://127.0.0.1:11434/v1",
                       "model": "qwen", "api_key": ""})
    assert ok, "本地模型不该要求 key"


def test_extract_json_tolerates_fences_and_prose():
    text = "好的，结果如下：\n```json\n{\"characters\": []}\n```\n希望有帮助"
    assert llm._extract_json(text) == {"characters": []}
    assert llm._extract_json('{"a": 1}') == {"a": 1}


def test_analyze_via_mock_server():
    srv, base = _serve()
    try:
        cfg = {"preset": "custom", "base_url": base, "model": "mock",
               "api_key": "test", "timeout": 10}
        roster = llm.analyze_characters("随便一段原文", cfg)
        names = [c["name"] for c in roster]
        assert "白小云" in names and "旁白" in names, names
        assert roster[1]["aliases"] == ["小胖墩", "小白"], roster[1]
        assert llm.test_connection(cfg)
    finally:
        srv.shutdown()


def test_voice_map_from_roster():
    vm = voice_map_from_roster(ROSTER["characters"], "白小云说 王静说 旁白")
    assert set(vm) == {"旁白", "白小云", "王静"}, vm
    assert vm["白小云"]["gender"] == "male"
    assert vm["王静"]["gender"] == "female"
    assert vm["白小云"]["role"] == "protagonist"
    assert vm["白小云"]["aliases"] == ["小胖墩", "小白"]
    voices = [v["voice"] for v in vm.values()]
    assert len(voices) == len(set(voices)), f"音色重复：{voices}"
    assert vm["旁白"]["voice"] not in {vm["白小云"]["voice"], vm["王静"]["voice"]}


def test_fallback_when_llm_unreachable():
    """接口连不上：应该回退本地规则并给出提示，而不是抛异常"""
    book = os.path.join(tempfile.mkdtemp(prefix="llm_test_"), "a.txt")
    with open(book, "w", encoding="utf-8") as f:
        f.write("“你怎么又淋成这样？”陈默说：“外面雨这么大。”\n林晚笑了笑。\n")

    old = llm.load_config
    llm.load_config = lambda: {"preset": "custom", "base_url": "http://127.0.0.1:9/v1",
                               "model": "m", "api_key": "k", "timeout": 3,
                               "sample_chars": 4000, "enabled": True}
    try:
        vm, note = studio.analyze_source_ex(book, use_llm=True)
        assert vm and "旁白" in vm, vm
        assert "回退" in note or "失败" in note, note
    finally:
        llm.load_config = old


if __name__ == "__main__":
    tests = [test_ready_without_key, test_extract_json_tolerates_fences_and_prose,
             test_analyze_via_mock_server, test_voice_map_from_roster,
             test_fallback_when_llm_unreachable]
    for fn in tests:
        fn()
        print(f"✅ {fn.__name__}")
    print(f"全部通过（{len(tests)} 项）")
