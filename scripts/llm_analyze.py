"""
大模型人物识别（可选，免费模型即可）
==================================
本地的正则/词性方案对「谁在说话」够用，但**人物表**经常翻车：
门派名当人名（太玄门）、形容词当人名（安静）、女性角色判成男声。

这里让大模型只做一件它擅长的事：**读一段原文，输出人物表**
（名字 / 性别 / 定位 / 别名）。逐句归属仍走本地规则 —— 这样既准又便宜，
一次识别通常只要几百 token。

支持任何 OpenAI 兼容接口（都用 /chat/completions）：
    智谱 GLM     https://open.bigmodel.cn/api/paas/v4      glm-4-flash（免费）
    硅基流动      https://api.siliconflow.cn/v1             Qwen2.5-7B（免费额度）
    DeepSeek     https://api.deepseek.com/v1               deepseek-chat
    Ollama 本地   http://127.0.0.1:11434/v1                qwen2.5:7b（完全离线）

配置写在 config/llm.json；没配 key 时自动回退本地规则，不影响使用。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "llm.json")

PRESETS = {
    "tencent": {
        "label": "腾讯混元（TokenHub · hunyuan-role）",
        "base_url": "https://tokenhub.tencentmaas.com/v1",
        "model": "hunyuan-role-latest",
    },
    "zhipu": {
        "label": "智谱 GLM（glm-4-flash 免费）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "siliconflow": {
        "label": "硅基流动（Qwen2.5-7B 免费额度）",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
    },
    "deepseek": {
        "label": "DeepSeek（deepseek-chat）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "ollama": {
        "label": "本地 Ollama（完全离线）",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
    },
    "custom": {
        "label": "自定义（任何 OpenAI 兼容地址）",
        "base_url": "",
        "model": "",
    },
}

DEFAULT_CONFIG = {
    "enabled": False,
    "preset": "tencent",
    "base_url": PRESETS["tencent"]["base_url"],
    "model": PRESETS["tencent"]["model"],
    "api_key": "",
    "timeout": 60,
    "sample_chars": 12000,
}

SYSTEM_PROMPT = "你是中文小说改编有声书的角色分析师，只输出 JSON，不要解释。"

USER_PROMPT = """请通读下面的小说片段，输出其中的「人物表」。

只输出如下 JSON：
{{"characters":[
  {{"name":"角色名","gender":"male|female|unknown",
    "age":"old|middle|young|teen|child",
    "emotion":"calm|gentle|happy|sad|angry|serious|excited",
    "role":"protagonist|supporting|narrator",
    "aliases":["其他称呼"],"evidence":"判断依据，20字内"}}
]}}

规则：
1. 只列**真正会说话的人**。门派、地名、组织、称谓（师兄/掌柜）、形容词（安静、冷笑）都不是人名。
2. **同一个人只能出现一次**。文中既有名字又有绰号时（如「白小云」与「小胖墩」），
   name 用最常用的那个名字，其余全部写进 aliases；文中只有称呼没有名字时（如「说书老者」），
   name 用这个称呼，其他叫法（如「老者」）写进 aliases。
3. **绝对不要编造原文里没出现过的人名**。宁可写「年轻男子」「女子」这种描述性称呼。
4. 旁白必须列一条，name 固定写 "旁白"，role 写 "narrator"，gender 按叙述口吻判断即可。
5. gender 依据文中的「他/她」或明确描写判断；证据不足写 "unknown"，不要猜。
6. age 依据称呼与描写：老者/翁/婆→old，妇人/叔婶→middle，青年/男子→young，
   少年/姑娘→teen，孩童/娃娃→child；拿不准写 "young"。
7. emotion 是这个角色的**常态语气**（不是某一句的情绪）：沉稳寡言→serious，
   爱开玩笑→happy，轻声细语→gentle，火爆→angry，多愁→sad；拿不准写 "calm"。
8. 按重要程度排序，最多 12 个角色。
9. 不要输出 JSON 以外的任何文字。

小说片段：
---
{text}
---"""


class LLMError(RuntimeError):
    """大模型调用失败（网络 / key / 返回格式）"""


# ─────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except (json.JSONDecodeError, OSError):
            pass
    # 环境变量优先，方便不改文件就换 key
    cfg["api_key"] = (os.environ.get("AUDIOBOOK_LLM_KEY")
                      or os.environ.get("OPENAI_API_KEY")
                      or cfg.get("api_key") or "")
    return cfg


def save_config(cfg: dict) -> str:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    data = {k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def ready(cfg: dict | None = None) -> tuple[bool, str]:
    """是否可用：本地 Ollama 不需要 key，其余要有 key"""
    cfg = cfg or load_config()
    if not cfg.get("base_url") or not cfg.get("model"):
        return False, "还没配置大模型（填写服务地址与模型名）"
    if cfg.get("preset") == "ollama" or "127.0.0.1" in cfg["base_url"] \
            or "localhost" in cfg["base_url"]:
        return True, f"本地模型 {cfg['model']}"
    if not cfg.get("api_key"):
        return False, "缺少 API Key（免费注册后填入即可）"
    return True, f"{cfg['model']} @ {cfg['base_url']}"


# ─────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────
_SSL_CTX = None


def _ssl_context():
    """python.org 版 Python 在 macOS 上不读系统钥匙串，urllib 会报
    CERTIFICATE_VERIFY_FAILED（curl 却正常）。这里按 certifi → 系统 CA 包
    的顺序找证书，都找不到才退回默认上下文。**绝不关闭证书校验**。"""
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    candidates = []
    try:
        import certifi
        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates += ["/etc/ssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem",
                   "/usr/local/etc/openssl@3/cert.pem"]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                _SSL_CTX = ssl.create_default_context(cafile=path)
                return _SSL_CTX
            except (ssl.SSLError, OSError):
                continue
    _SSL_CTX = ssl.create_default_context()
    return _SSL_CTX


def _post(cfg: dict, payload: dict, retries: int = 3) -> dict:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    data = json.dumps(payload).encode("utf-8")

    # 代理偶尔会 502 / 超时，长任务里重试比直接失败划算
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 60)),
                                        context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            last = LLMError(f"HTTP {e.code}：{body}")
            if e.code < 500 and e.code != 429:      # 4xx（除限流）重试也没用
                raise last from e
        except urllib.error.URLError as e:
            last = LLMError(f"连不上 {url}（{e.reason}）")
        except (TimeoutError, OSError) as e:
            last = LLMError(f"请求超时或被拦截：{e}")
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise last if last else LLMError("请求失败")


def _content(resp: dict) -> str:
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"返回格式不认识：{str(resp)[:200]}") from e


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise LLMError(f"模型没返回 JSON：{text[:200]}")
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解析失败：{e}｜原文 {m.group()[:200]}") from e


# ─────────────────────────────────────────────────────
# 对外
# ─────────────────────────────────────────────────────
def test_connection(cfg: dict) -> str:
    ok, why = ready(cfg)
    if not ok:
        raise LLMError(why)
    resp = _post(cfg, {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": "回复两个字：可用"}],
        "max_tokens": 16,
        "temperature": 0,
    })
    return _content(resp).strip()[:40] or "（空回复）"


def chat_text(cfg: dict, system: str, user: str, json_mode: bool = False,
              temperature: float = 0.2, max_tokens: int | None = None) -> str:
    """通用对话：返回模型正文（供文本转译等场景复用）

    system 传空字符串时不发 system 消息 —— 像 hunyuan-role 这类角色扮演模型，
    一旦给了 system 人设就会「入戏」改写剧情，反而不听格式指令。
    """
    ok, why = ready(cfg)
    if not ok:
        raise LLMError(why)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return _content(_post(cfg, payload))


def analyze_characters(text: str, cfg: dict | None = None) -> list[dict]:
    """让大模型输出人物表，返回 [{name, gender, role, aliases, evidence}]"""
    cfg = cfg or load_config()
    ok, why = ready(cfg)
    if not ok:
        raise LLMError(why)

    sample = text[: int(cfg.get("sample_chars", 12000))]
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT.format(text=sample)},
        ],
        "temperature": 0.1,
    }
    # 支持 JSON 模式的模型直接约束输出；不支持的（如部分 Ollama 版本）会自动忽略
    if cfg.get("json_mode", True):
        payload["response_format"] = {"type": "json_object"}

    try:
        data = _extract_json(_content(_post(cfg, payload)))
    except LLMError:
        payload.pop("response_format", None)      # 退一步：不要 JSON 模式再试一次
        data = _extract_json(_content(_post(cfg, payload)))

    raw = data.get("characters") or data.get("roles") or []
    if not isinstance(raw, list) or not raw:
        raise LLMError(f"模型没给出角色列表：{str(data)[:200]}")

    out: list[dict] = []
    seen = set()
    for item in raw[:16]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        gender = str(item.get("gender") or "unknown").lower()
        role = str(item.get("role") or "supporting").lower()
        age = str(item.get("age") or "young").lower()
        emotion = str(item.get("emotion") or "calm").lower()
        aliases = [str(a).strip() for a in (item.get("aliases") or [])
                   if str(a).strip() and str(a).strip() != name]
        seen.add(name)
        out.append({
            "name": name,
            "gender": gender if gender in ("male", "female", "unknown") else "unknown",
            "age": age if age in ("old", "middle", "young", "teen", "child") else "young",
            "emotion": emotion if emotion in ("calm", "gentle", "happy", "sad",
                                              "angry", "serious", "excited") else "calm",
            "role": role if role in ("protagonist", "supporting", "narrator") else "supporting",
            "aliases": aliases[:6],
            "evidence": str(item.get("evidence") or "")[:60],
        })

    if not any(c["name"] == "旁白" for c in out):
        out.insert(0, {"name": "旁白", "gender": "female", "age": "young",
                       "emotion": "calm", "role": "narrator",
                       "aliases": [], "evidence": "自动补全"})
    return out


if __name__ == "__main__":      # 手动体检：python scripts/llm_analyze.py
    cfg = load_config()
    print("配置：", {k: (v if k != "api_key" else ("已设置" if v else "未设置"))
                     for k, v in cfg.items()})
    print("状态：", ready(cfg))
    try:
        print("连通性：", test_connection(cfg))
    except LLMError as e:
        print("连通性失败：", e)
