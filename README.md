# 有声书工坊

> 上传任意中文小说 txt，自动识别角色，智能配音，一键生成多角色有声书。

---

## 功能特点

- **自动角色识别** — 无需手动标注，基于对话归属正则 + jieba 词性分析，自动提取小说中的角色名
- **智能性别判断** — 通过「他/她」代词统计推断角色性别，自动分配合适声音
- **可视化配音编辑** — 网页界面中直接修改角色声音、语速、音调
- **免费合成** — 调用 Microsoft Edge TTS，无需 API Key，完全免费
- **容错合成** — 自动过滤空行/纯标点，超长段落自动切分，失败自动重试

---

## 效果预览

| 角色类型 | 默认声音 | 风格 |
|----------|----------|------|
| 旁白 | XiaoxiaoNeural | 温柔女声，语调偏低 |
| 女主角 | XiaoyiNeural | 活泼女声 |
| 女配角 | XiaoxiaoNeural | 温柔，语速稍慢 |
| 男主角 | YunxiNeural | 年轻男声，语速稍快 |
| 男配角 | YunjianNeural / YunxiaNeural / YunyangNeural | 沉稳、清朗、播音腔 |

---

## 环境要求

- Python 3.9+
- ffmpeg（用于音频拼接）

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
```

---

## 安装

```bash
# 建议使用虚拟环境
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

## 启动

```bash
python app.py
```

浏览器打开 **http://127.0.0.1:7860**

---

## 使用流程

### 1. 上传小说

将 UTF-8 编码的 `.txt` 文件上传到界面。

### 2. 自动分析角色

点击「自动分析角色与声音」，系统会：
- 提取出现在对话中的角色名
- 推断各角色性别
- 自动从声音池分配合适的声音

### 3. 按需调整（可选）

表格中可直接修改声音名称、语速、音调。

**当前可用中文声音：**

| 声音名 | 性别 | 风格 |
|--------|------|------|
| zh-CN-XiaoxiaoNeural | 女 | 温柔，通用 |
| zh-CN-XiaoyiNeural | 女 | 活泼 |
| zh-CN-YunxiNeural | 男 | 年轻 |
| zh-CN-YunjianNeural | 男 | 沉稳 |
| zh-CN-YunxiaNeural | 男 | 清朗 |
| zh-CN-YunyangNeural | 男 | 播音腔 |

### 4. 设置范围并合成

填写起始行、结束行（建议每次不超过 500 行），点击「开始合成有声书」。

### 5. 试听与下载

合成完成后可直接在页面试听，或下载 MP3 文件。输出保存在 `output/` 目录。

---

## 命令行使用（可选）

```bash
# 预览角色识别结果
python scripts/parse_script.py --input data/novel.txt --start 0 --end 200

# 直接合成（自动分析角色）
python scripts/audiobook_generator.py \
    --input data/novel.txt \
    --start 0 \
    --end   500 \
    --name  第一章
```

---

## 项目结构

```
txt-to-audiobook/
├── app.py                          # 网页界面（Gradio）
├── scripts/
│   ├── analyze_characters.py       # 角色自动分析与声音分配
│   ├── parse_script.py             # 小说文本 → 剧本解析
│   └── audiobook_generator.py      # TTS 合成 + 音频拼接
├── data/                           # 放置小说 txt（不入库）
├── output/                         # 生成的 MP3 文件
└── requirements.txt
```

---

## 注意事项

- edge-tts 调用微软在线 TTS 服务，需要网络连接
- 连续合成大量内容时，建议每次处理 200～500 行，避免触发限流
- 小说版权归原作者所有，本项目仅供个人学习使用

---

## License

MIT
