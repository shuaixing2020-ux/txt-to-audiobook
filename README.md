# 那些年，我们一起追的女孩 · 有声书生成器

> 九把刀著。本项目将小说文本自动解析为多角色有声书，使用 Microsoft Edge TTS 免费合成语音。

---

## 效果预览

| 角色 | 声音 | 风格 |
|------|------|------|
| 旁白 | XiaoxiaoNeural | 温柔女声，故事书感 |
| 柯景腾 | YunxiNeural | 年轻男声，稍快带痞气 |
| 沈佳仪 | XiaohanNeural | 清甜女声，语速略慢 |
| 阿和 | YunjianNeural | 沉稳男声 |
| 廖英宏 | YunfengNeural | 热情男声 |
| 许博淳 | YunyangNeural | 标准男声 |

---

## 环境要求

- Python 3.10+
- ffmpeg（用于音频拼接）

**安装 ffmpeg：**

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows
# 前往 https://ffmpeg.org/download.html 下载，并将 bin 目录加入 PATH
```

---

## 安装依赖

```bash
pip install -r requirements.txt
```

---

## 使用方法

### 1. 准备小说文本

将小说 txt 文件（UTF-8 编码）放入 `data/` 目录，命名为 `novel.txt`。

```
nanian-audiobook/
└── data/
    └── novel.txt   ← 放这里
```

### 2. 预览剧本解析结果

在合成音频前，先检查角色识别是否正确：

```bash
python scripts/parse_script.py --input data/novel.txt --start 2278 --end 2362
```

输出示例：
```
📖[旁白] 饼干快吃完了，我突然有个怪点子。
👦[柯景腾] 沈佳仪，这么说起来，你对安安静静念书这一类的事很有把握？
👧[沈佳仪] 什么意思？
👦[柯景腾] 没，我只是想跟你打赌。
...
```

如需保存为 JSON 以便手动修正：

```bash
python scripts/parse_script.py --input data/novel.txt --start 2278 --end 2362 --output scripts/script_preview.json
```

### 3. 合成有声书

```bash
# 合成赌牛奶片段（行 2278～2362）
python scripts/audiobook_generator.py \
    --input data/novel.txt \
    --start 2278 \
    --end   2362 \
    --name  高中时代_赌牛奶

# 合成第一章（行 153～809）
python scripts/audiobook_generator.py \
    --input data/novel.txt \
    --start 153 \
    --end   809 \
    --name  第一章
```

输出文件保存在 `output/` 目录，格式为 MP3（192kbps）。

---

## 章节行号参考

| 章节 | 起始行 | 结束行 | 内容 |
|------|--------|--------|------|
| 序言 | 0 | 119 | 推荐序 |
| 第一章 | 153 | 809 | 国中·初遇沈佳仪 |
| 第二章 | 810 | 1444 | 李小华与第一段暗恋 |
| 第三章 | 1445 | 2968 | 高中三年 |
| 第四章 | 2969 | 3411 | 高三与联考 |
| 第五章 | 3412 | 4130 | 大学时代 |
| 尾声 | 4131 | 4719 | 那些年 |

---

## 自定义声音

在 `scripts/audiobook_generator.py` 中修改 `VOICE_MAP`：

```python
VOICE_MAP = {
    "旁白":   ("zh-CN-XiaoxiaoNeural",  "+0%",  "-5%"),
    "柯景腾": ("zh-CN-YunxiNeural",     "+8%",  "+3%"),
    # 参数：(声音名称, 语速偏移, 音调偏移)
}
```

**常用中文声音列表：**

| 声音名 | 性别 | 风格 |
|--------|------|------|
| zh-CN-XiaoxiaoNeural | 女 | 温柔，通用 |
| zh-CN-XiaohanNeural | 女 | 清甜 |
| zh-CN-XiaomoNeural | 女 | 活泼 |
| zh-CN-YunxiNeural | 男 | 年轻 |
| zh-CN-YunjianNeural | 男 | 沉稳 |
| zh-CN-YunfengNeural | 男 | 热情 |
| zh-CN-YunyangNeural | 男 | 播音腔 |

查看完整列表：
```bash
python -c "import asyncio, edge_tts; asyncio.run(edge_tts.list_voices())" | grep zh-CN
```

---

## 项目结构

```
nanian-audiobook/
├── scripts/
│   ├── audiobook_generator.py   # 主程序：解析 + 合成 + 拼接
│   └── parse_script.py          # 工具：预览剧本解析结果
├── data/
│   └── novel.txt                # 小说原文（需自行提供，不入库）
├── output/                      # 生成的 MP3 文件
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 注意事项

- 小说版权归原作者九把刀所有，本项目仅供个人学习使用，请勿二次传播。
- edge-tts 调用微软在线 TTS 服务，需要网络连接。
- 合成整本书（约10万字）预计需要 30～60 分钟，建议分章节合成。

---

## License

MIT
