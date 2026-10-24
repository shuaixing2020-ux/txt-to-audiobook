"""
角色分析（增强版入口）
--------------------
对外保留与上游一致的接口：
    analyze(path) -> voice_map
    FEMALE_VOICES / MALE_VOICES / NARRATOR_VOICE

相对上游的改进：
  1. 说话人统计改用「引号切分 + 说话动词」的直接统计，不再用会吃掉
     前后修饰语的贪心正则（原版会产出「苏晴低声」「”陈默」这类假角色）。
  2. jieba 只在直接统计命中不足时作为补充召回。
  3. 保留旁白 + 最多 8 个角色，性别用「他/她」就近统计，判定不出时留 unknown，
     绝不强行盖章（原版会把「苏晴」判成男声）。

注：上游同名文件已备份为 analyze_characters.upstream.py。
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts._parse_impl import SPEECH_VERB_RE, _find_speaker, _segments
else:
    from ._parse_impl import SPEECH_VERB_RE, _find_speaker, _segments

try:
    import jieba.posseg as pseg

    JIEBA_AVAILABLE = True
except ImportError:  # pragma: no cover
    JIEBA_AVAILABLE = False

# ─────────────────────────────────────────────────────
# 声音池（与上游一致）
# ─────────────────────────────────────────────────────
FEMALE_VOICES = [
    ("zh-CN-XiaoyiNeural", "+0%", "+2Hz"),          # 活泼，适合女主
    ("zh-CN-XiaoxiaoNeural", "-3%", "+0Hz"),        # 温柔，通用
    ("zh-CN-liaoning-XiaobeiNeural", "+0%", "+0Hz"),  # 辽宁方言，幽默
    ("zh-CN-shaanxi-XiaoniNeural", "+0%", "+0Hz"),  # 陕西方言，明快
]
MALE_VOICES = [
    ("zh-CN-YunxiNeural", "+8%", "+3Hz"),    # 年轻，适合男主
    ("zh-CN-YunjianNeural", "+0%", "-3Hz"),  # 沉稳
    ("zh-CN-YunxiaNeural", "+3%", "+0Hz"),   # 清朗
    ("zh-CN-YunyangNeural", "+0%", "+0Hz"),  # 播音腔
]
NARRATOR_VOICE = ("zh-CN-XiaoxiaoNeural", "+0%", "-5Hz")

# 额外可选中文声音（角色超过 8 个时可手动填表使用）
EXTRA_VOICES = {
    "zh-CN-XiaochenNeural": "female", "zh-CN-XiaohanNeural": "female",
    "zh-CN-XiaomengNeural": "female", "zh-CN-XiaomoNeural": "female",
    "zh-CN-XiaoruiNeural": "female", "zh-CN-XiaoshuangNeural": "female",
    "zh-CN-XiaoxuanNeural": "female", "zh-CN-XiaoyanNeural": "female",
    "zh-CN-XiaoyouNeural": "female", "zh-CN-XiaozhenNeural": "female",
    "zh-CN-YunfengNeural": "male", "zh-CN-YunhaoNeural": "male",
    "zh-CN-YunyeNeural": "male", "zh-CN-YunzeNeural": "male",
    "zh-HK-HiuMaanNeural": "female", "zh-HK-WanLungNeural": "male",
    "zh-TW-HsiaoChenNeural": "female", "zh-TW-YunJheNeural": "male",
}

NON_NAMES = {
    "他", "她", "我", "你", "您", "大家", "众人", "所有人", "有人", "某人",
    "心里", "脑子", "嘴里", "一个人", "两个人", "这个人", "那个人",
    "爸爸", "妈妈", "父亲", "母亲", "爷爷", "奶奶", "叔叔", "阿姨",
    "老师", "医生", "警察", "店员", "服务员", "同学", "朋友", "陌生人",
    "男人", "女人", "男孩", "女孩", "小孩", "孩子", "老人",
    "作者", "编辑", "读者", "主角", "配角", "少女", "少年", "声音",
}
# 以这些字符开头的候选，多半是「笑了笑」「忽然说」一类的短语
_NON_NAME_PREFIX = ("一", "忽然", "突然", "连忙", "轻轻", "笑着", "苦笑", "低声", "轻声")

_FEMALE_CHARS = set(
    "梅兰菊竹莲花玉珠月霞燕琴雪芳萍娜丽秀珍美香凤英彩彤婷琳瑶欣怡"
    # 常见女性名用字补充：只影响「他/她」证据不足时的保守判断
    "晴曦薇柔涵蕊萱颖洁静慧敏娟莉倩楠岚霜露媛妮姝嫣姗茜蔓莹珊瑜瑾曼茹菲芸"
    "娇婉妍妤蕊蕾琦璐妮娅嘉宁"
)


# ─────────────────────────────────────────────────────
# 角色候选提取
# ─────────────────────────────────────────────────────
def _direct_speaker_counts(text: str) -> Counter:
    """
    直接统计「谁在说话」：只看「XX说道」「XX说：」这种紧贴引号的归属结构。
    名字取说话动词之前、被句读隔开的那一段的末尾 2~3 字。
    """
    counts: Counter = Counter()
    verb_re = re.compile(r"(?:" + SPEECH_VERB_RE + ")")
    for line in text.splitlines():
        for seg, is_dialog in _segments(line.strip()):
            if is_dialog:
                continue
            for m in verb_re.finditer(seg):
                chunk = seg[: m.start()]
                tail = re.split(r"[。！？；，,、：:\s]", chunk)[-1]
                for cand in (tail[-2:], tail[-3:]):
                    if len(cand) >= 2 and _valid_name(cand):
                        counts[cand] += 1
                        break
    return counts


# 这些词出现在任何片段里，说明该片段不是单纯的人名（多半是人名+动作）
_NOT_NAME_PARTS = (
    "没有", "也没", "忽然", "突然", "苦笑", "笑了笑", "摇头", "点头", "低声", "轻声",
    "大声", "缓缓", "终于", "这才", "不由", "继续", "开口", "插嘴", "反问", "皱了",
    "皱了皱", "叹了", "说是", "一个", "这个", "那个", "什么", "怎么", "不是", "已经",
    "仍旧", "依然", "正想", "想要", "准备", "只好", "只得", "连忙", "急忙",
)


def _valid_name(name: str) -> bool:
    name = name.strip()
    if len(name) < 2 or len(name) > 4:
        return False
    if name in NON_NAMES:
        return False
    if re.search(SPEECH_VERB_RE, name):
        return False
    if any(name.startswith(p) for p in _NON_NAME_PREFIX):
        return False
    if any(part in name for part in _NOT_NAME_PARTS):
        return False
    return True


def _extract_by_jieba(text: str) -> Counter:
    if not JIEBA_AVAILABLE:
        return Counter()
    names: Counter = Counter()
    for word, flag in pseg.cut(text[:60000]):
        if flag.startswith("nr") and _valid_name(word):
            names[word] += 1
    return names


def _detect_genders(names: list[str], text: str) -> dict:
    """用「名字所在句子里离它最近的一次他/她」推断性别；证据不足返回 unknown"""
    genders: dict = {}
    sentences = re.split(r"[。！？；\n]", text[:120000])
    for name in names:
        he = she = 0
        for sent in sentences:
            pos = sent.find(name)
            if pos < 0:
                continue
            tail = sent[pos + len(name):]
            i_he, i_she = tail.find("他"), tail.find("她")
            if i_he >= 0 and (i_she < 0 or i_he < i_she):
                he += 1
            elif i_she >= 0:
                she += 1
        if she > he:
            genders[name] = "female"
        elif he > she:
            genders[name] = "male"
        else:
            # 证据不足时用「名字末字是否为典型女性用字」做保守启发
            genders[name] = "female" if len(name) == 2 and name[-1] in _FEMALE_CHARS else "unknown"
    return genders


_DIALECT_MARK = ("liaoning", "shaanxi")

# ── 年龄 / 情绪 → 音色与语速音调 ─────────────────────
AGE_DISPLAY = {"child": "幼", "teen": "少", "young": "青", "middle": "中", "old": "老"}
AGE_REVERSE = {v: k for k, v in AGE_DISPLAY.items()}
EMOTION_DISPLAY = {"calm": "平静", "gentle": "温柔", "happy": "欢快", "sad": "低沉",
                   "angry": "愤怒", "serious": "严肃", "excited": "激动"}
EMOTION_REVERSE = {v: k for k, v in EMOTION_DISPLAY.items()}

# 年龄对语速/音调的整体偏移（百分比 / Hz）
AGE_STYLE = {"child": (18, 8), "teen": (8, 4), "young": (0, 0),
             "middle": (-4, -2), "old": (-12, -6)}
# 情绪对语速/音调的偏移，叠加在年龄之上
EMOTION_STYLE = {"calm": (0, 0), "gentle": (-6, -2), "happy": (8, 5), "sad": (-6, -4),
                 "angry": (10, 4), "serious": (-4, -3), "excited": (12, 6)}

# 性别 × 年龄 的首选音色（前面的更贴，取不到就往后找，最后才回退性别池）
_AGE_VOICE_PREF = {
    ("male", "old"): ["zh-CN-YunyangNeural", "zh-CN-YunjianNeural", "zh-CN-YunzeNeural"],
    ("male", "middle"): ["zh-CN-YunjianNeural", "zh-CN-YunyangNeural", "zh-CN-YunfengNeural"],
    ("male", "young"): ["zh-CN-YunxiNeural", "zh-CN-YunxiaNeural", "zh-CN-YunhaoNeural"],
    ("male", "teen"): ["zh-CN-YunxiaNeural", "zh-CN-YunxiNeural", "zh-CN-YunyeNeural"],
    ("male", "child"): ["zh-CN-YunyeNeural", "zh-CN-YunxiaNeural", "zh-CN-YunxiNeural"],
    ("female", "old"): ["zh-CN-XiaomoNeural", "zh-CN-XiaoshuangNeural", "zh-CN-XiaoxiaoNeural"],
    ("female", "middle"): ["zh-CN-XiaochenNeural", "zh-CN-XiaohanNeural", "zh-CN-XiaoxiaoNeural"],
    ("female", "young"): ["zh-CN-XiaoyiNeural", "zh-CN-XiaomengNeural", "zh-CN-XiaoxuanNeural"],
    ("female", "teen"): ["zh-CN-XiaoyanNeural", "zh-CN-XiaoyiNeural", "zh-CN-XiaoyouNeural"],
    ("female", "child"): ["zh-CN-XiaoyouNeural", "zh-CN-XiaozhenNeural", "zh-CN-XiaoyiNeural"],
}

# 备用中文声音按性别整理（不含港澳台口音，避免给普通话小说配出粤语腔）
_EXTRA_FEMALE = [(k, "+0%", "+0Hz") for k, g in EXTRA_VOICES.items()
                 if g == "female" and k.startswith("zh-CN")]
_EXTRA_MALE = [(k, "+0%", "+0Hz") for k, g in EXTRA_VOICES.items()
               if g == "male" and k.startswith("zh-CN")]


def _voice_pool(base: list, extra: list) -> list:
    """排声音池：先标准音，再备用音，方言音垫底；旁白音色不进角色池"""
    base = [v for v in base if v[0] != NARRATOR_VOICE[0]]
    neutral = [v for v in base if not any(m in v[0] for m in _DIALECT_MARK)]
    dialect = [v for v in base if any(m in v[0] for m in _DIALECT_MARK)]
    return neutral + extra + dialect


def _apply_style(rate: str, pitch: str, age: str, emotion: str) -> tuple[str, str]:
    """把年龄/情绪叠加到语速与音调上"""
    ar, ap = AGE_STYLE.get(age, (0, 0))
    er, ep = EMOTION_STYLE.get(emotion, (0, 0))

    def _num(s: str, suffix: str) -> float:
        try:
            return float(str(s).strip().rstrip(suffix).strip())
        except ValueError:
            return 0.0

    base_r = _num(rate, "%")
    base_p = _num(pitch, "Hz")
    r = int(round(base_r + ar + er))
    p = int(round(base_p + ap + ep))
    return (f"{r:+d}%", f"{p:+d}Hz")


def _pick_voice(gender: str, age: str, used: set, pool: list) -> str:
    """性别×年龄 的首选音色 → 该性别池 → 池首（实在没有就复用）"""
    for v in _AGE_VOICE_PREF.get((gender, age), []):
        if v not in used:
            return v
    for v in pool:
        if v[0] not in used:
            return v[0]
    return pool[0][0]


def _assign_voices(characters: list, genders: dict,
                   ages: dict | None = None, emotions: dict | None = None) -> dict:
    """characters: [(名字, 频次)]，按顺序分配互不重复的音色"""
    ages = ages or {}
    emotions = emotions or {}
    voice_map: dict = {
        "旁白": {
            "voice": NARRATOR_VOICE[0], "rate": NARRATOR_VOICE[1], "pitch": NARRATOR_VOICE[2],
            "gender": "female", "age": "young", "emotion": "calm",
            "role": "narrator", "frequency": 0,
        }
    }
    female_pool = _voice_pool(FEMALE_VOICES, _EXTRA_FEMALE)
    male_pool = _voice_pool(MALE_VOICES, _EXTRA_MALE)
    used = {NARRATOR_VOICE[0]}          # 旁白占掉的音色，角色不再用

    for i, (name, freq) in enumerate(characters):
        gender = genders.get(name, "unknown")
        age = ages.get(name, "young")
        emotion = emotions.get(name, "calm")
        pool = female_pool if gender == "female" else male_pool
        # 未知性别优先分男声（男声池更长，避免全部挤到女声）
        voice = _pick_voice(gender if gender == "female" else "male", age, used, pool)
        used.add(voice)

        preset = next((v for v in (FEMALE_VOICES + MALE_VOICES + _EXTRA_FEMALE + _EXTRA_MALE)
                       if v[0] == voice), (voice, "+0%", "+0Hz"))
        rate, pitch = _apply_style(preset[1], preset[2], age, emotion)
        voice_map[name] = {
            "voice": voice, "rate": rate, "pitch": pitch,
            # 界面/表格只认 男/女/未知 三档，unknown 原样传给 UI 显示「未知」；
            # 合成时按 voice 声线播放，与标签无关，不会影响音色
            "gender": gender,
            "age": age,
            "emotion": emotion,
            "role": "protagonist" if i < 2 else "supporting",
            "frequency": freq,
        }
    return voice_map


def voice_map_from_roster(roster: list[dict], text: str = "") -> dict:
    """按「人物表」分配声音（大模型识别用）

    roster: [{"name","gender","role","aliases"}]，来自 llm_analyze
    性别/定位以 roster 为准，音色仍按池子顺序分配（保证不撞音色、不占旁白音色）。
    """
    roster = [r for r in roster if isinstance(r, dict) and r.get("name")]
    counts: Counter = Counter()
    for item in roster:
        name = item["name"]
        if name == "旁白":
            continue
        counts[name] = text.count(name) + sum(
            text.count(a) for a in (item.get("aliases") or []) if a)

    chars = [(r["name"], counts.get(r["name"], 0)) for r in roster if r["name"] != "旁白"]
    chars.sort(key=lambda x: -x[1])
    genders = {r["name"]: r.get("gender", "unknown") for r in roster}
    ages = {r["name"]: _norm_age(r.get("age")) for r in roster}
    emotions = {r["name"]: _norm_emotion(r.get("emotion")) for r in roster}

    vm = _assign_voices(chars, genders, ages, emotions)
    for item in roster:
        name = item["name"]
        if name in vm:
            vm[name]["role"] = item.get("role") or vm[name]["role"]
            if item.get("aliases"):
                vm[name]["aliases"] = list(item["aliases"])[:6]
    return vm


def _norm_age(age) -> str:
    """接受 old/middle/young/teen/child，也接受中文「老/中/青/少/幼」"""
    raw = str(age or "").strip().lower()
    if raw in AGE_STYLE:
        return raw
    return AGE_REVERSE.get(raw, "young")


def _norm_emotion(emotion) -> str:
    raw = str(emotion or "").strip().lower()
    if raw in EMOTION_STYLE:
        return raw
    return EMOTION_REVERSE.get(raw, "calm")


# 规则引擎的年龄/情绪启发（认得多少算多少，认不出就按青壮年+平静）
_AGE_HINT = [
    (("老", "翁", "叟", "爷", "婆", "妪", "翁"), "old"),
    (("童", "娃", "孩", "儿"), "child"),
    (("少", "姑娘", "丫头", "妹", "少年"), "teen"),
    (("妇人", "娘", "叔", "婶", "中年"), "middle"),
]
_EMOTION_HINT = [
    (("怒", "喝", "吼", "骂", "厉"), "angry"),
    (("笑", "嘻", "乐", "欢"), "happy"),
    (("叹", "悲", "泣", "哭", "苦"), "sad"),
    (("低声", "轻声", "柔", "温"), "gentle"),
    (("沉", "冷", "淡淡"), "serious"),
    (("急", "惊", "慌", "喊"), "excited"),
]


def _guess_age(name: str) -> str:
    for keys, age in _AGE_HINT:
        if any(k in name for k in keys):
            return age
    return "young"


def analyze(novel_path: str, sample_lines: int = 800) -> dict:
    """分析小说前 sample_lines 行，返回 voice_map"""
    with open(novel_path, encoding="utf-8") as f:
        sample = "".join(f.readlines()[:sample_lines])

    direct = _direct_speaker_counts(sample)
    jieba_names = _extract_by_jieba(sample)

    merged: Counter = Counter()
    for name, c in direct.items():
        merged[name] += c * 3        # 直接归属证据权重高
    for name, c in jieba_names.items():
        merged[name] += c
    filtered = {n: c for n, c in merged.items() if c >= 3 or (n in direct and c >= 2)}
    top = sorted(filtered.items(), key=lambda x: -x[1])[:8]
    if not top:
        return _assign_voices([], {})

    names = [n for n, _ in top]
    ages = {n: _guess_age(n) for n in names}
    return _assign_voices(top, _detect_genders(names, sample), ages)


# ─────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────
def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="小说角色分析器")
    ap.add_argument("--input", required=True)
    ap.add_argument("--lines", type=int, default=800)
    a = ap.parse_args()
    print(json.dumps(analyze(a.input, a.lines), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
