"""
文本转译：把任意排版的小说改写成「这个系统最吃得动的格式」
=========================================================
系统内部最理想的形式只有一条规则：

    一行一句，角色明示 —— 角色名｜台词

    旁白｜雨下得很大，林晚把伞收起来。
    林晚｜你怎么又淋成这样？
    陈默｜外面雨这么大。

有了这个格式，解析器不需要再猜「谁在说话」：引号、破折号、混排、
「他说」到底指谁，全部在转译阶段一次解决。合成端只做朗读。

两种转译方式：
  · rules —— 纯本地：复用现有解析器 + 排版清洗，不联网，秒级完成
  · llm   —— 大模型逐章改写：按 token 预算自动切块，带「上文 + 人物表」
              作为上下文，保证跨块的人物一致；失败或跑偏的块自动回退规则版

产物写到 translated/<同名>.txt，合成时可优先使用（见 studio.chapter_tasks）。
"""
from __future__ import annotations

import os
import re

from scripts import llm_analyze as llm
from scripts._parse_impl import CANON_DELIM as DELIM
from scripts._parse_impl import is_junk_line
from scripts._parse_impl import looks_canonical as is_canonical
from scripts._parse_impl import parse_canonical
from scripts.parse_script import parse_novel_to_script

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSLATED_DIR = os.path.join(ROOT, "translated")

NARRATOR = "旁白"
_ALT_DELIMS = ("｜", "|", "丨", "：", ":")

# 转译稿最少要保留原文多少内容（防止模型偷懒/胡编）
_MIN_KEEP_RATIO = 0.72
_MAX_KEEP_RATIO = 1.35


# ─────────────────────────────────────────────────────
# 标准格式
# ─────────────────────────────────────────────────────
def canonical_line(role: str, text: str) -> str:
    return f"{role.strip()}{DELIM}{text.strip()}"


def _clean_line(text: str) -> str:
    """去掉模型偶尔带出来的序号、项目符号、markdown"""
    text = re.sub(r"^\s*(?:[-*•]|\d+[.、)]|\(\d+\))\s*", "", text)
    text = text.replace("**", "").replace("`", "")
    return text.strip()


def _norm_body(text: str) -> str:
    """正文归一：全角空格缩进、多余空白、引号统一"""
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text).strip()
    text = text.strip("“”\"'「」『』 ")
    return text


def _has_words(text: str) -> bool:
    """至少有一个汉字/字母/数字，纯标点（……、——）不值得占一行"""
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", text))


def _similarity_ok(src: str, out_lines: list[str]) -> bool:
    """粗校验：转译稿保留的汉字量要跟原文接近，否则判定跑偏"""
    src_chars = len(re.sub(r"\s", "", src))
    out_chars = len(re.sub(r"\s", "", "".join(out_lines)))
    if src_chars == 0:
        return True
    ratio = out_chars / src_chars
    return _MIN_KEEP_RATIO <= ratio <= _MAX_KEEP_RATIO


# ─────────────────────────────────────────────────────
# 规则版转译（离线、秒级）
# ─────────────────────────────────────────────────────
def translate_by_rules(text: str, voice_map: dict | None = None) -> list[str]:
    """用本地解析器把原文拆成「角色｜台词」；不需要联网"""
    vm = voice_map or {NARRATOR: {"voice": "zh-CN-XiaoxiaoNeural",
                                  "rate": "+0%", "pitch": "-5Hz",
                                  "gender": "female", "role": "narrator"}}
    script = parse_novel_to_script(text, vm)
    lines: list[str] = []
    for item in script:
        body = _norm_body(item["text"])
        if not body or not _has_words(body):
            continue
        role = item["role"] or NARRATOR
        lines.append(canonical_line(role, body))
    return lines


# ─────────────────────────────────────────────────────
# 大模型转译（逐章、分块、带上下文）
# ─────────────────────────────────────────────────────
SYSTEM_PROMPT = ""          # 见下：这类模型给 system 人设就会「入戏」，一律用单条用户消息

USER_PROMPT = """你是文本格式转换工具，不是聊天助手。把下面的小说原文逐句改写成有声书脚本。

规则：
1. 一行一句，格式 `角色｜台词`
2. 说话人用原文出现的人名或稳定称呼（如「说书老者」）；引号外的叙述写「旁白」
3. 只做**断句、归属、去噪**：不许改写措辞、不许增删情节、不许总结
4. **不要把人称代词改成人名**：原文写「他/她/他们」就保留，不要替换成「白小云」
5. 删除网址、更新时间、字数统计、求票求收藏、HTML 标签这类非正文
6. **必须覆盖原文每一句**，不许省略，也不许只输出一行

示例：
原文：雨下得很大。林晚说：“你怎么又淋成这样？”
输出：
旁白｜雨下得很大。
林晚｜你怎么又淋成这样？

{roster_block}{context_block}原文：
---
{text}
---

只输出脚本正文，每行一句。"""


def _roster_block(voice_map: dict | None) -> str:
    if not voice_map:
        return ""
    names = [n for n in voice_map if n != NARRATOR]
    if not names:
        return ""
    parts = []
    for n in names[:14]:
        aliases = voice_map[n].get("aliases") or []
        parts.append(f"{n}" + (f"（又称：{'、'.join(aliases)}）" if aliases else ""))
    return "已知人物：" + "；".join(parts) + "\n\n"


def split_chunks(text: str, max_chars: int = 2600) -> list[str]:
    """按段落切成不超长的块（尽量在段落边界切，别把对话切断）"""
    paragraphs = [p for p in text.splitlines() if p.strip()]
    chunks, buf, size = [], [], 0
    for p in paragraphs:
        if size + len(p) > max_chars and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(p)
        size += len(p) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def translate_chapter(
    text: str,
    voice_map: dict | None = None,
    cfg: dict | None = None,
    progress_cb=None,
    control=None,
    emit=None,
    max_chars: int = 2600,
) -> tuple[list[str], dict]:
    """逐块让大模型改写；某块跑偏/失败就回退规则版

    emit: 每块出结果时回调一次（界面用它做「边转译边滚动」的实时预览）
    返回 (脚本行列表, 统计)
    """
    cfg = cfg or llm.load_config()
    chunks = split_chunks(text, max_chars)
    stats = {"chunks": len(chunks), "llm": 0, "fallback": 0, "lines": 0}

    out: list[str] = []
    context = ""                     # 上一块的结尾，给模型接续人物
    for i, chunk in enumerate(chunks):
        if control is not None:
            control()                # 可被暂停/停止（同步回调）
        prompt = USER_PROMPT.format(
            roster_block=_roster_block(voice_map),
            context_block=(f"上文结尾（仅供接续人物，不要重复输出）：\n{context}\n\n"
                           if context else ""),
            text=chunk,
        )
        try:
            raw = llm.chat_text(cfg, SYSTEM_PROMPT, prompt)
            lines = [canonical_line(*_split_pair(_clean_line(ln)))
                     for ln in raw.splitlines() if _clean_line(ln)]
            lines = [ln for ln in lines if ln and DELIM in ln]
            body = [ln.split(DELIM, 1)[1] for ln in lines]
            # 模型偶尔把「更新时间…」也转出来，这里再滤一遍
            keep = [(ln, bd) for ln, bd in zip(lines, body)
                    if _has_words(bd) and not is_junk_line(bd)]
            lines = [ln for ln, _ in keep]
            body = [bd for _, bd in keep]
            # 块与块之间偶尔会把「上文结尾」重复输出一遍，去掉衔接处的重复行
            while lines and out and lines[0] == out[-1]:
                lines.pop(0)
                body.pop(0)
            if not lines or not _similarity_ok(chunk, body):
                raise ValueError("内容偏差过大")
            out.extend(lines)
            stats["llm"] += 1
            context = "\n".join(lines[-2:])
        except Exception:                          # noqa: BLE001
            fallback = translate_by_rules(chunk, voice_map)
            out.extend(fallback)
            stats["fallback"] += 1
            context = "\n".join(fallback[-2:]) if fallback else context
            lines = fallback

        if emit is not None:
            emit(lines)
        if progress_cb:
            progress_cb((i + 1) / len(chunks),
                        f"转译第 {i + 1}/{len(chunks)} 块 · 已出 {len(out)} 行")

    stats["lines"] = len(out)
    return out, stats


def _split_pair(line: str) -> tuple[str, str]:
    """`角色｜台词` → (角色, 台词)，容忍模型写成冒号/竖线/带括号"""
    for d in _ALT_DELIMS:
        if d in line:
            role, _, body = line.partition(d)
            role = role.strip("【】[]「」『』 ").strip()
            body = _norm_body(body)
            if role and body:
                return role, body
    return NARRATOR, _norm_body(line)


# ─────────────────────────────────────────────────────
# 落盘 / 读取
# ─────────────────────────────────────────────────────
def translated_path(chapter_file: str) -> str:
    os.makedirs(TRANSLATED_DIR, exist_ok=True)
    return os.path.join(TRANSLATED_DIR, os.path.splitext(os.path.basename(chapter_file))[0] + ".txt")


def save_translated(chapter_file: str, lines: list[str]) -> str:
    path = translated_path(chapter_file)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def load_translated(chapter_file: str) -> list[str] | None:
    path = translated_path(chapter_file)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if ln.strip()]


def list_translated() -> dict[str, int]:
    """{章节名: 行数}"""
    if not os.path.isdir(TRANSLATED_DIR):
        return {}
    out = {}
    for fn in sorted(os.listdir(TRANSLATED_DIR)):
        if fn.endswith(".txt"):
            with open(os.path.join(TRANSLATED_DIR, fn), encoding="utf-8") as f:
                out[fn] = sum(1 for _ in f)
    return out
