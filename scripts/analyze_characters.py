"""
通用小说角色分析器（免费方案）
-----------------------------
1. 用 jieba 词性标注提取人名（nr 标签）
2. 用对话归属正则（"..." + 姓名 + 说/道）补充验证
3. 用 他/她 代词统计自动推断性别
4. 按频次分主角/配角，从声音池自动分配 edge-tts 声音
"""

from __future__ import annotations

import re
import sys
import os
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import jieba.posseg as pseg
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

# ─────────────────────────────────────────────────────
# 声音池（按推荐顺序）
# ─────────────────────────────────────────────────────
FEMALE_VOICES = [
    ("zh-CN-XiaoyiNeural",   "+0%", "+2Hz"),   # 活泼，适合女主
    ("zh-CN-XiaoxiaoNeural", "-3%", "+0Hz"),   # 温柔，通用
]
MALE_VOICES = [
    ("zh-CN-YunxiNeural",   "+8%", "+3Hz"),   # 年轻，适合男主
    ("zh-CN-YunjianNeural", "+0%", "-3Hz"),   # 沉稳
    ("zh-CN-YunxiaNeural",  "+3%", "+0Hz"),   # 清朗
    ("zh-CN-YunyangNeural", "+0%", "+0Hz"),   # 播音腔
]
NARRATOR_VOICE = ("zh-CN-XiaoxiaoNeural", "+0%", "-5Hz")

# ─────────────────────────────────────────────────────
# 过滤词：这些不是角色名
# ─────────────────────────────────────────────────────
NON_NAMES = {
    "他", "她", "我", "你", "您", "大家", "众人", "所有人", "有人", "某人",
    "心里", "脑子", "嘴里", "一个人", "两个人", "这个人", "那个人",
    "爸爸", "妈妈", "父亲", "母亲", "爷爷", "奶奶", "叔叔", "阿姨",
    "老师", "医生", "警察", "店员", "服务员", "同学", "朋友", "陌生人",
    "男人", "女人", "男孩", "女孩", "小孩", "孩子", "老人",
    "作者", "编辑", "读者", "主角", "配角",
    "一声", "一句", "一番", "一笑",
}

# 说话动词正则
SPEECH_VERB = r'说|道|问|答|笑|哭|叫|喊|回答|补充|低声|轻声|大声|嘀咕|叹|吼|嚷|反问|插嘴|继续|开口'


def _extract_by_pattern(text: str) -> Counter:
    """从对话归属模式中提取说话人（精度高）"""
    names: Counter = Counter()

    # 模式1：结束引号 + 姓名 + 说话动词
    # 例："你好吗？"沈佳仪说道
    p1 = re.compile(
        r'[\u201d\u300d\u300f\uff1f\uff01\u3002]'
        r'([^\s，。！？\u201c\u300c\u300e「『]{2,4}?)'
        r'(?:' + SPEECH_VERB + r')'
    )
    for m in p1.finditer(text):
        name = m.group(1).strip()
        if len(name) >= 2 and name not in NON_NAMES:
            names[name] += 1

    # 模式2：姓名 + 说话动词 + 开始引号
    # 例：沈佳仪说："你好吗？"
    p2 = re.compile(
        r'([^\s，。！？\u201c\u201d\u300c\u300d]{2,4}?)'
        r'(?:' + SPEECH_VERB + r')'
        r'[：:\s]*[\u201c\u300c\u300e「『]'
    )
    for m in p2.finditer(text):
        name = m.group(1).strip()
        if len(name) >= 2 and name not in NON_NAMES:
            names[name] += 1

    return names


def _extract_by_jieba(text: str) -> Counter:
    """用 jieba 词性标注提取人名（召回高）"""
    if not JIEBA_AVAILABLE:
        return Counter()
    names: Counter = Counter()
    sample = text[:60000]
    for word, flag in pseg.cut(sample):
        if flag == "nr" and len(word) >= 2 and word not in NON_NAMES:
            names[word] += 1
    return names


def _detect_genders(names: list, text: str) -> dict:
    """用代词统计推断性别，不确定时用名字字符启发式"""
    genders = {}
    sample = text[:80000]
    sentences = re.split(r'[。！？\n]', sample)

    female_name_chars = set("梅兰菊竹莲花玉珠月霞云燕琴雪芳萍娜丽秀珍美香凤英华彩彤婷")

    for name in names:
        he = she = 0
        for sent in sentences:
            if name in sent:
                he  += sent.count("他") + sent.count("他的") + sent.count("他说")
                she += sent.count("她") + sent.count("她的") + sent.count("她说")
        if she > he:
            genders[name] = "female"
        elif he > she:
            genders[name] = "male"
        else:
            # 启发式：名字中含有典型女性用字则判为女
            score = sum(1 for c in name if c in female_name_chars)
            genders[name] = "female" if score > 0 else "male"

    return genders


def _assign_voices(characters: list, genders: dict) -> dict:
    """
    characters: [(name, frequency), ...] 按频次降序
    返回 voice_map: {name: {voice, rate, pitch, gender, role, frequency}}
    """
    voice_map: dict = {
        "旁白": {
            "voice":     NARRATOR_VOICE[0],
            "rate":      NARRATOR_VOICE[1],
            "pitch":     NARRATOR_VOICE[2],
            "gender":    "female",
            "role":      "narrator",
            "frequency": 0,
        }
    }

    female_pool = list(FEMALE_VOICES)
    male_pool   = list(MALE_VOICES)

    for i, (name, freq) in enumerate(characters):
        gender = genders.get(name, "male")
        role   = "protagonist" if i < 2 else "supporting"

        if gender == "female":
            voice_tuple = female_pool.pop(0) if female_pool else FEMALE_VOICES[-1]
        else:
            voice_tuple = male_pool.pop(0) if male_pool else MALE_VOICES[-1]

        voice_map[name] = {
            "voice":     voice_tuple[0],
            "rate":      voice_tuple[1],
            "pitch":     voice_tuple[2],
            "gender":    gender,
            "role":      role,
            "frequency": freq,
        }

    return voice_map


def analyze(novel_path: str, sample_lines: int = 800) -> dict:
    """
    分析小说文件，返回 voice_map 字典。
    只读取前 sample_lines 行作为样本，速度快。
    """
    with open(novel_path, encoding="utf-8") as f:
        lines = f.readlines()

    sample_text = "".join(lines[:sample_lines])

    # 双路提取：正则（精）+ jieba（召）
    pattern_names = _extract_by_pattern(sample_text)
    jieba_names   = _extract_by_jieba(sample_text)

    # 合并，正则结果权重 ×3（更可靠）
    merged: Counter = Counter()
    for name, count in pattern_names.items():
        merged[name] += count * 3
    for name, count in jieba_names.items():
        merged[name] += count

    # 过滤低频（可能是误识别）
    filtered = {
        name: count
        for name, count in merged.items()
        if count >= 3 and len(name) >= 2
    }

    # 按频次降序，只取前 8 个角色
    top_chars = sorted(filtered.items(), key=lambda x: -x[1])[:8]

    if not top_chars:
        # 兜底：至少返回旁白
        return {
            "旁白": {
                "voice": NARRATOR_VOICE[0],
                "rate":  NARRATOR_VOICE[1],
                "pitch": NARRATOR_VOICE[2],
                "gender": "female",
                "role":   "narrator",
                "frequency": 0,
            }
        }

    names_only = [name for name, _ in top_chars]
    genders    = _detect_genders(names_only, sample_text)
    voice_map  = _assign_voices(top_chars, genders)

    return voice_map


if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser(description="小说角色分析器")
    parser.add_argument("--input", required=True, help="小说 txt 文件")
    parser.add_argument("--lines", type=int, default=800, help="分析前 N 行")
    args = parser.parse_args()

    result = analyze(args.input, args.lines)
    print(json.dumps(result, ensure_ascii=False, indent=2))
