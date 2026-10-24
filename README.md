# 有声书工坊 · txt-to-audiobook

> 把一个中文小说 txt 变成**多角色有声书**：自动切章 → 识别人物 → 转译成脚本 → 校对 → 配音合成。
> 全程网页操作，可暂停 / 停止 / 续跑；在线（Edge TTS）与离线（macOS say）双引擎。

---

## 它解决什么问题

把小说直接丢给 TTS，只会得到"一个声音从头念到尾"。真正难的是：

1. **谁在说话** —— 引号、破折号、「他说」到底指谁，排版千奇百怪
2. **人物是谁** —— 门派名、形容词经常被误认成人名（"太玄门"、"安静"）
3. **念得对不对** —— 网站水印、更新时间、`……` 分隔线不该念出来
4. **整本要跑很久** —— 中途想停、想改、想续跑

这个项目把上面每一步都做成可见、可干预的环节。

---

## 功能特性

| 环节 | 能力 |
| --- | --- |
| 切章 | 按「第 X 章 / 序章 / 楔子」正则切分，UTF-8 / GBK 自动识别，超长章再切 |
| 识别人物 | 本地规则（jieba + 说话动词 + 代词统计）或**大模型**（任何 OpenAI 兼容接口）；输出名字/性别/**年龄**/**情绪**/别名 |
| 文本转译 | 把整章改写成 `角色｜台词` 标准脚本：一行一句、角色明示、自动去水印；规则版秒级、大模型版逐章 |
| 剧本校对 | 逐句面板：谁在说、旁白还是对白、字数、**哪些不朗读**（非正文/纯标点），角色占比条 |
| 配音 | 音色按「性别 × 年龄」选池，语速音调按「年龄 + 情绪」叠加；角色条可拖拽排序；角色库跨书复用 |
| 合成 | **多路并发**、逐句缓存续跑、暂停/停止/跳过当前章、不留半成品 MP3 |
| 产物 | 网页试听 / 下载，或按章节批量输出 |

---

## 快速开始

```bash
git clone https://github.com/shuaixing2020-ux/txt-to-audiobook.git
cd txt-to-audiobook

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# macOS 需要 ffmpeg
brew install ffmpeg

python app.py            # → http://127.0.0.1:7860
```

没有网络时用离线引擎：

```bash
AUDIOBOOK_ENGINE=say python app.py
```

---

## 使用流程（网页六步）

```
① 导入与切章 → ② 人物与配音 → ③ 文本转译 → ④ 剧本校对 → ⑤ 合成 → ⑥ 产物库
```

1. 拖入 txt，点「预览章节」看目录，再「写入 samples/」
2. 「识别人物」自动填表（勾选「用大模型识别」更准），可直接改声音/年龄/情绪/别名
3. 「开始转译」把整章改写成 `角色｜台词`，右侧实时滚动
4. 「生成剧本预览」逐句检查归属，异常一眼可见
5. 勾章节 → 「开始批量合成」，随时可暂停/停止
6. 产物库试听、下载

### 合成中途可以停

| 按钮 | 语义 |
| --- | --- |
| ⏸ 暂停 | 在飞的几句跑完就停住，点「继续」原地接着跑 |
| ⏹ 停止 | 退出整个任务，**不产出半成品**；已合成句子进缓存，再点开始自动续跑 |
| ⏭ 跳过本章 | 放弃当前章，直接做下一章 |

---

## 大模型配置（可选）

「二 · 人物与配音 → 🤖 大模型识别设置」里填服务商、地址、模型、API Key，点「测试连接」→「保存」。

内置预设（任何 OpenAI 兼容接口都能用）：

| 服务商 | 接口地址 | 模型 |
| --- | --- | --- |
| 腾讯混元（TokenHub） | `https://tokenhub.tencentmaas.com/v1` | `hunyuan-role-latest` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash`（免费） |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5:7b`（完全离线） |

配置写到 `config/llm.json`（**已在 .gitignore 里，不会提交**），也可以用环境变量 `AUDIOBOOK_LLM_KEY` 临时覆盖。
没配 Key 或调用失败会**自动回退本地规则**，不会卡住流程。

> 角色扮演类模型（如 `hunyuan-role`）给了 system 提示词容易"入戏"改写剧情，
> 本项目对这类请求改用单条用户消息 + 示例，并对 JSON 围栏、多余解释都做了容错。

---

## 目录结构

```
.
├── app.py                     # 网页界面（Gradio），只做绑定
├── scripts/
│   ├── studio.py              # 服务层：切章/配音表/角色库/剧本预览/后台任务/产物
│   ├── translate.py           # 文本转译：规则版 + 大模型逐章版
│   ├── llm_analyze.py         # 大模型客户端（OpenAI 兼容，urllib 实现）
│   ├── _parse_impl.py         # 剧本解析：引号切段、归属、别名、非正文过滤
│   ├── analyze_characters.py  # 本地人物识别 + 音色分配（性别×年龄×情绪）
│   ├── audiobook_generator.py # 并发合成、缓存、拼接、中断钩子
│   ├── tts_local.py           # macOS say 离线引擎
│   ├── split.py / batch.py / doctor.py
│   └── test_*.py              # 回归测试（不联网、不花钱）
├── data/                      # 放小说 txt（不入库）
├── samples/                   # 切好的章节（不入库）
├── translated/                # 转译稿（不入库）
├── output/                    # 生成的 MP3（不入库）
└── config/                    # 本机配置，含 Key（不入库）
```

---

## 技术要点

- **并发合成**：把「句 × 段」展开成任务池，`asyncio.Semaphore` 控并发，按索引回填保证顺序；同一章冷缓存 **100s → 44s**
- **免子进程读时长**：纯 Python 解析 MP3 帧头（跳过 ID3v2 / Xing 元数据帧），替代每句一次 ffmpeg
- **单次拼接**：ffmpeg concat 一次完成，40 段 **4.8s → 1.0s**
- **可中断**：`synthesize_script(control=...)` 每句前回调，可等待（暂停）或抛 `SynthesisAborted`（停止），异常跳过导出步骤 → 不留半截 MP3
- **转译格式**：`角色｜台词`，解析器识别到该格式直接走快路径，不再猜

---

## 测试

```bash
python -m scripts.test_parse      # 解析 / 假角色 / 性别 / 音色撞车
python -m scripts.test_studio     # 切章 / 表格 / 角色库 / 转译 / 任务控制
python -m scripts.test_pipeline   # 清洗 / 全链路 / say 引擎
python -m scripts.test_llm        # 大模型识别（本地假服务器）
```

全部离线运行，不消耗 API 额度。

---

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 在线合成超时 / 报 DNS | 网络到微软 TTS 不通；换网络或改用 `AUDIOBOOK_ENGINE=say` |
| 某个角色全程没声音 | `say` 同名音色（Reed/Flo）会命中非中文变体，本项目已用「中文（中国大陆）」全名调用 |
| 角色表音色重复 | 点「识别人物」重分配，或手动改；旁白音色不会被角色占用 |
| 转译很慢 | 大模型每章 3~8 秒属正常；只想要格式规整可切「本地规则」 |
| 端口占用 | `GRADIO_SERVER_PORT=7861 python app.py` |

---

## 来源与致谢

本项目基于 [yunieyuna/txt-to-audiobook](https://github.com/yunieyuna/txt-to-audiobook) 的初始版本做了大幅重构：
重写解析器、重写人物识别、新增服务层与六步网页、并发合成、文本转译、角色库、任务控制等。
上游仓库未声明开源许可证，如有异议请联系删除。

## 免责声明

仅供个人学习与自用。请只处理你**有权使用**的文本，生成音频的版权与使用风险由使用者自行承担。
仓库内不包含任何小说原文、转译稿或生成的音频。
