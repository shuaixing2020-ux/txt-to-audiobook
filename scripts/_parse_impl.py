"""
增强版剧本解析（本地修复版）
--------------------------
与上游 scripts/parse_script.py 的差异：
  原版对每一行用 ``^“...”`` 整行匹配，遇到这行很常见的排版：

      “你怎么又淋成这样？”陈默说：“外面雨这么大。”

  会把整行当成旁白，一个角色都分不到。
  本版先把每一行按「引号外文本 / 引号内台词」切成句子片段，再逐段判定：
      · 引号内             → 台词，归属「最近一次明确的说话人」
      · 引号外且带人名+说话动词 → 台词前后可以直接指定说话人
      · 引号外且没有说话人     → 旁白（顺带校正“他说/她说”对应的角色）
"""

from __future__ import annotations

import re

# 中文引号：开引号 -> 闭引号
QUOTE_PAIRS = {"\u201c": "\u201d", "\u300c": "\u300d", "\u300e": "\u300f"}

# 说话动词（长词优先，避免「说」先吃掉「说道」）
SPEECH_VERBS = sorted(
    "说道 说 道 问 答 回答 补充 低声 轻声 大声 喊 叫 嚷 吼 叹 嘀咕 反问 插嘴 开口 继续".split(),
    key=len,
    reverse=True,
)
SPEECH_VERB_RE = r"(?:" + "|".join(SPEECH_VERBS) + ")"

# 句读：在这些符号之后断句（保留符号本身）
# 注意：不能按「。」断句，否则行尾的「陈默说：」会被切到上一句
_BREAK_RE = re.compile(r"(?<=[！？!?；;\n])")

# 「他说」「她问」这类代词归属，用上一句里的人名替换
# 只认独立代词：排除「其他/他们/她们/他人/他日/他乡…」这些不是代词的情况
_STANDALONE_PRONOUN = re.compile(
    r"(?<![其吉他])(?<![他她])([他她])"
    r"(?![们人日处乡国方年物事杀校处边])"
)

# 引号后紧跟的动作：去掉开头的「名字+说话动词」再当旁白
_LEAD_ATTRIB_RE = re.compile(
    r"^\s*[，,]?\s*[^\s，。！？“”「」『』]{1,6}?"
    + SPEECH_VERB_RE
    + r"[^\s，。！？“”「」『』]{0,4}[，,。：:]?"
)


_ATTR_TAIL_RE = re.compile(r"(?:" + SPEECH_VERB_RE + r")(?:[了着过]?[，,]?)?[。！？…：:\s]*$")

# 非正文行：网站元数据、章节更新信息、求票求收藏、网址……念出来很出戏
_JUNK_LINE_RE = re.compile(
    r"^\s*(?:"
    r"更新时间|字数[:：]|本章未完|本章完|请收藏|求收藏|求推荐|求月票|求订阅|"
    r"手机用户|最新网址|最新章节|全文阅读|百度搜索|一秒记住|记住本站|"
    r"PS[:：]|ps[:：]|作者的话|作者有话说|声明[:：]|"
    r"www\.|https?://|m\.|wap\.|<[a-zA-Z/!]"
    r")", re.IGNORECASE
)
# 行内出现的网址/HTML 标签（起点、纵横等站点的尾巴）
_INLINE_JUNK_RE = re.compile(
    r"<[^>]{0,200}>|https?://\S+|www\.\S+|[a-zA-Z0-9-]+\.(?:com|net|cn|org)\S*",
    re.IGNORECASE,
)
# 纯符号 / 纯数字行（分隔线、页码等）
_JUNK_ONLY_RE = re.compile(r"^[\s\d\-—_=*~·.。,，、…\u3000]*$")


def strip_inline_junk(text: str) -> str:
    """去掉行内的 HTML 标签 / 网址"""
    return _INLINE_JUNK_RE.sub("", text).strip()


def is_junk_line(line: str) -> bool:
    """判断一行是否为「不该朗读的非正文」"""
    s = line.strip()
    if not s:
        return True
    if _JUNK_LINE_RE.match(s):
        return True
    # 行内含网址/标签：基本都是站点尾巴（"起点中文网www.qidian.com…"）
    if _INLINE_JUNK_RE.search(s):
        return True
    if _JUNK_ONLY_RE.match(s):
        return True
    # 「第一章 xxx」这种标题行本身可以读，但「更新时间2012-12-21 字数：2521」不行
    if len(s) < 6 and not re.search(r"[\u4e00-\u9fa5]", s):
        return True
    return False


def _split_close(line: str, start: int) -> int:
    """从 start 起找与开引号配对的闭引号；找不到返回 -1"""
    depth = 1
    j = start
    while j < len(line):
        c = line[j]
        if c in QUOTE_PAIRS:
            depth += 1
        elif c in QUOTE_PAIRS.values():
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _segments(line: str) -> list[tuple[str, str]]:
    """
    把一行切成 [(文本, 类型)]，类型取值：
        "quote"  —— 引号内台词（已去掉首尾引号）
        "before" —— 本段台词「前面」的文字，可能是前置归属：「陈默说：『…』」
        "after"  —— 本段台词「后面」紧随的文字，可能是后置归属：「…』陈默说」
        "text"   —— 独立的引号外旁白
    例：`雨很大。“甲。”陈默说：『乙。』`
        → [text"雨很大。", quote"甲。", after"陈默说：", quote"乙。"]
    """
    out: list[tuple[str, str]] = []
    buf = ""
    pos = 0
    n = len(line)
    while pos < n:
        ch = line[pos]
        if ch in QUOTE_PAIRS:
            seg_before = buf
            buf = ""
            j = _split_close(line, pos + 1)
            body = line[pos + 1:] if j == -1 else line[pos + 1: j]
            if not seg_before.strip():
                out.append((body, "quote"))
            elif _ATTR_TAIL_RE.search(seg_before):
                # 前置归属：「陈默说：『…』」
                out.append((seg_before, "before"))
                out.append((body, "quote"))
            else:
                # 普通旁白 + 可能的后置归属：「…。』陈默说」
                kind = "after" if re.search(r"(?:" + SPEECH_VERB_RE + r")", seg_before) else "text"
                out.append((seg_before, kind))
                out.append((body, "quote"))
            pos = (pos + 1) if j == -1 else (j + 1)
        else:
            buf += ch
            pos += 1

    # 行尾剩余文字：若紧跟在台词后面且含说话动词，就是后置归属
    if buf.strip():
        tail_kind = "after" if (out and out[-1][1] == "quote"
                                and re.search(r"(?:" + SPEECH_VERB_RE + r")", buf)) else "text"
        out.append((buf, tail_kind))
    return out


def _other_character(characters: list[str], current: str, fallback: str) -> str:
    """选一个和 current 不同的角色（排除旁白），用于对手戏换人"""
    for name in characters:
        if name != current:
            return name
    return fallback


def _find_speaker(text: str, characters: list[str]) -> str | None:
    """从一段引号外文本里找说话人（长名字优先，避免「林晚」抢「林晚星」）"""
    for name in sorted(characters, key=len, reverse=True):
        if name in text:
            return name
    return None


def _strip_leading_attribution(text: str) -> str:
    """去掉旁白开头的「陈默说道，」「他苦笑道，」"""
    text = _LEAD_ATTRIB_RE.sub("", text, count=1).strip()
    return text.lstrip("，,。").strip()


# 转译稿格式：`角色｜台词`。全角竖线在正文里几乎不出现，切分安全
CANON_DELIM = "｜"
_CANON_ALTS = ("｜", "|", "丨")


def looks_canonical(text: str) -> bool:
    """前 30 行里 80% 以上是「角色｜台词」就认为是转译稿"""
    lines = [ln for ln in text.splitlines() if ln.strip()][:30]
    if len(lines) < 3:
        return False
    hit = sum(1 for ln in lines if any(d in ln for d in _CANON_ALTS))
    return hit >= max(3, int(len(lines) * 0.8))


def parse_canonical(text: str) -> list[dict]:
    """转译稿 → [{"role","text"}]，不再做任何猜测"""
    out: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        for d in _CANON_ALTS:
            if d in line:
                role, _, body = line.partition(d)
                role = role.strip("【】[]「」『』 ").strip()
                body = body.strip()
                if role and body:
                    out.append({"role": role, "text": body})
                break
    return out


def parse_novel_to_script(text: str, voice_map: dict, trace: list | None = None) -> list[dict]:
    """把小说原文解析为 [{"role": 角色, "text": 台词/旁白}, ...]

    trace: 传入列表时，逐条记录「这一句归谁、是朗读还是跳过」，
           供网页的剧本预览面板展示（含被跳过的非正文行）。
    """
    # 已经是转译稿（角色｜台词）就直接读，不再走启发式
    if looks_canonical(text):
        items = parse_canonical(text)
        if trace is not None:
            trace.extend({"role": it["role"], "text": it["text"], "skip": None,
                          "kind": "旁白" if it["role"] == "旁白" else "对白"}
                         for it in items)
        return items

    characters = [n for n in voice_map if n != "旁白"]
    protagonist = characters[0] if characters else "旁白"

    # 别名（小名/绰号）也参与「谁在说话」的匹配，但最终归到本名
    aliases: dict[str, str] = {}
    for name, cfg in voice_map.items():
        for a in (cfg.get("aliases") or []) if isinstance(cfg, dict) else []:
            if a and a != name:
                aliases[a] = name
    match_names = characters + list(aliases)

    def _speaker(seg: str) -> str | None:
        found = _find_speaker(seg, match_names)
        return aliases.get(found, found) if found else None

    script: list[dict] = []
    last_speaker = protagonist
    last_named = protagonist      # 最近一次被点名的角色（供「他说」使用）

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if trace is not None:
                trace.append({"role": "-", "text": "", "skip": "空行"})
            continue
        if is_junk_line(line):
            if trace is not None:
                trace.append({"role": "-", "text": line, "skip": "非正文"})
            continue

        segs = _segments(line)
        prev_kind = None          # 上一条被处理片段（含 before/after）的类型
        prev_text = ""
        i = 0
        while i < len(segs):
            seg, kind = segs[i][0].strip(), segs[i][1]

            if kind in ("before", "after"):
                # 归属文字：只更新「最近点名」，不朗读、不改 last_speaker
                at_named = _speaker(seg)
                if at_named:
                    last_named = at_named
                prev_kind, prev_text = kind, seg
                i += 1
                continue

            if kind == "quote":
                if not seg:
                    i += 1
                    continue

                speaker = None
                if prev_kind == "before":
                    # 前置归属：「陈默说：『…』」
                    speaker = _speaker(prev_text)
                if speaker is None and i + 1 < len(segs) and segs[i + 1][1] == "after":
                    # 后置归属：「…。』陈默说」
                    speaker = _speaker(segs[i + 1][0])
                if speaker is None and prev_kind == "quote":
                    # 同一句话里紧挨着的下一段台词：对手戏，换人
                    speaker = _other_character(characters, last_named or last_speaker, protagonist)
                if speaker is None and prev_kind in ("before", "after"):
                    # 「陈默说：“…”」里没认出新名字：说明说话人还是刚才那位
                    speaker = last_named

                if speaker:
                    last_speaker = last_named = speaker
                else:
                    # 既没有归属也不是连续台词：沿用上一位说话人
                    speaker = last_speaker
                    if prev_kind not in ("before", "after"):
                        last_named = last_named or speaker

                script.append({"role": speaker, "text": seg})
                if trace is not None:
                    trace.append({"role": speaker, "text": seg, "skip": None, "kind": "对白"})
                prev_kind, prev_text = "quote", seg
                i += 1
                continue

            # ── 引号外文字 = 旁白 ──
            named = _speaker(seg)
            if named:
                last_named = named
                last_speaker = named

            narration = _strip_leading_attribution(seg).strip("，,。：: ")
            if narration:
                if named:
                    # 「他苦笑道」→「陈默苦笑道」，让旁白也带上角色声线。
                    # 只替换独立代词：「其他/他们/他人/他日」里的「他」不是代词，
                    # 替换会把「其他方面」变成「其白小云方面」。
                    gender = voice_map.get(named, {}).get("gender")
                    for m in _STANDALONE_PRONOUN.finditer(narration):
                        if (m.group() == "他" and gender in ("male", "unknown")) or (
                            m.group() == "她" and gender == "female"
                        ):
                            narration = narration[:m.start()] + named + narration[m.end():]
                            break
                script.append({"role": "旁白", "text": narration})
                if trace is not None:
                    trace.append({"role": "旁白", "text": narration, "skip": None, "kind": "旁白"})
            prev_kind, prev_text = "text", seg
            i += 1

    return script


# ─────────────────────────────────────────────────────
# CLI（与上游保持兼容）
# ─────────────────────────────────────────────────────
def main() -> None:
    import argparse
    import json

    from .analyze_characters import analyze

    ap = argparse.ArgumentParser(description="小说剧本解析器（增强版）")
    ap.add_argument("--input", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=500)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    print("正在分析角色...")
    voice_map = analyze(args.input)
    chars = [k for k in voice_map if k != "旁白"]
    print(f"识别到角色：{chars}")

    with open(args.input, encoding="utf-8") as f:
        lines = f.readlines()
    script = parse_novel_to_script("".join(lines[args.start: args.end]), voice_map)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        print(f"已保存：{args.output}（共 {len(script)} 句）")
    else:
        marks = {"旁白": "📖"}
        for name, cfg in voice_map.items():
            if name != "旁白":
                marks[name] = "👧" if cfg.get("gender") == "female" else "👦"
        for item in script:
            print(f"{marks.get(item['role'], '❓')}[{item['role']}] {item['text'][:60]}")

    roles: dict = {}
    for s in script:
        roles[s["role"]] = roles.get(s["role"], 0) + 1
    print(f"\n共 {len(script)} 句 | 角色分布：{roles}")


if __name__ == "__main__":
    main()
