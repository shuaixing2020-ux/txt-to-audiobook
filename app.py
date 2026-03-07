"""
有声书工坊 · 网页界面
---------------------
运行方式：
    python app.py
然后在浏览器打开 http://localhost:7860
"""

import asyncio
import json
import os
import sys

# ── Monkey-patch: gradio-client 4.x 在处理 Dataframe 的 JSON schema 时，
#    遇到 additionalProperties:true (bool) 会抛 TypeError。
#    在导入 gradio 之前打补丁，让它安全降级为 "any"。
import gradio_client.utils as _gcu
_orig_jschema = _gcu.json_schema_to_python_type
def _safe_jschema(schema, defs=None):
    try:
        return _orig_jschema(schema, defs)
    except TypeError:
        return "any"
_gcu.json_schema_to_python_type = _safe_jschema

import gradio as gr
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from analyze_characters import analyze, FEMALE_VOICES, MALE_VOICES, NARRATOR_VOICE
from audiobook_generator import generate as _generate_audio

# ─────────────────────────────────────────────────────
# 声音参考（用于界面显示）
# ─────────────────────────────────────────────────────
_ALL_VOICE_NAMES = (
    [v[0].replace("zh-CN-", "") for v in FEMALE_VOICES]
    + [v[0].replace("zh-CN-", "") for v in MALE_VOICES]
    + [NARRATOR_VOICE[0].replace("zh-CN-", "")]
)

VOICE_REF_HTML = (
    '<div class="voice-ref">'
    "<strong>可用声音一览</strong><br>"
    "🎀 <b>女声</b>：" + " &nbsp;·&nbsp; ".join(v[0].replace("zh-CN-", "") for v in FEMALE_VOICES) + "<br>"
    "🎙 <b>男声</b>：" + " &nbsp;·&nbsp; ".join(v[0].replace("zh-CN-", "") for v in MALE_VOICES) + "<br>"
    '<small style="color:#9e8b7a">填写格式：zh-CN-YunxiNeural 或直接写 YunxiNeural 均可</small>'
    "</div>"
)

# ─────────────────────────────────────────────────────
# 样式
# ─────────────────────────────────────────────────────
CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600&display=swap');

/* 整体背景与字体 */
body, .gradio-container, .main {
    background-color: #f5f0e8 !important;
    font-family: 'Noto Serif SC', 'Georgia', 'SimSun', serif !important;
}

/* 顶部标题区 */
.app-header {
    text-align: center;
    padding: 2.5rem 1rem 1.5rem;
    border-bottom: 1px solid #d4bc96;
    margin-bottom: 1.5rem;
}
.app-header h1 {
    font-size: 2.2em;
    color: #2c1810;
    font-weight: 600;
    letter-spacing: 0.2em;
    margin: 0 0 0.4rem;
}
.app-header .subtitle {
    color: #7a6051;
    font-size: 0.95em;
    font-style: italic;
    letter-spacing: 0.12em;
}
.app-header .ornament {
    color: #c4a882;
    font-size: 1.1em;
    letter-spacing: 0.4em;
    margin: 0.6rem 0;
}

/* 章节标题 */
.section-label {
    font-size: 0.88em;
    font-weight: 600;
    color: #5c4033;
    letter-spacing: 0.15em;
    border-left: 3px solid #c4a882;
    padding: 0.2em 0 0.2em 0.8em;
    margin: 1.4rem 0 0.6rem;
}

/* 声音参考卡片 */
.voice-ref {
    background: #ede8de;
    border: 1px solid #d4bc96;
    border-radius: 6px;
    padding: 0.75rem 1rem;
    font-size: 0.82em;
    color: #5c4033;
    line-height: 2;
    margin-top: 0.5rem;
}

/* 底部装饰 */
.app-footer {
    text-align: center;
    color: #b09880;
    font-style: italic;
    font-size: 0.82em;
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid #d4bc96;
    letter-spacing: 0.1em;
}

/* 按钮 */
button.lg { letter-spacing: 0.08em !important; }
"""

# ─────────────────────────────────────────────────────
# 业务逻辑
# ─────────────────────────────────────────────────────
_GENDER_DISPLAY = {"female": "女", "male": "男", "unknown": "未知"}
_ROLE_DISPLAY   = {"narrator": "旁白", "protagonist": "主角", "supporting": "配角"}
_GENDER_REVERSE = {"女": "female", "男": "male", "未知": "unknown"}
_ROLE_REVERSE   = {"旁白": "narrator", "主角": "protagonist", "配角": "supporting"}


def analyze_novel(file):
    """上传文件后分析角色，返回供表格显示的 DataFrame 和状态信息"""
    if file is None:
        return None, "⚠️ 请先上传小说 txt 文件"
    try:
        voice_map = analyze(file.name)
        rows = []
        for name, cfg in voice_map.items():
            rows.append({
                "角色":   name,
                "性别":   _GENDER_DISPLAY.get(cfg.get("gender", "unknown"), "未知"),
                "定位":   _ROLE_DISPLAY.get(cfg.get("role", "supporting"), "配角"),
                "声音":   cfg["voice"],
                "语速":   cfg["rate"],
                "音调":   cfg["pitch"],
                "出现频次": cfg.get("frequency", 0),
            })
        # 旁白排首位，其余按频次降序
        rows.sort(key=lambda r: (0 if r["定位"] == "旁白" else 1, -r["出现频次"]))
        df = pd.DataFrame(rows)
        char_count = len(voice_map) - 1  # 不计旁白
        return df, f"✅ 识别到 {char_count} 个角色，已自动分配声音。可直接在表格中修改后再合成。"
    except Exception as e:
        return None, f"❌ 分析失败：{e}"


def _df_to_voice_map(df: pd.DataFrame) -> dict:
    """将表格还原为 voice_map 字典"""
    voice_map = {}
    for _, row in df.iterrows():
        name  = str(row["角色"]).strip()
        voice = str(row["声音"]).strip()
        # 补全 zh-CN- 前缀
        if voice and not voice.startswith("zh-CN-"):
            voice = "zh-CN-" + voice
        voice_map[name] = {
            "voice":     voice or "zh-CN-XiaoxiaoNeural",
            "rate":      str(row["语速"]).strip() or "+0%",
            "pitch":     str(row["音调"]).strip() or "+0%",
            "gender":    _GENDER_REVERSE.get(str(row["性别"]), "unknown"),
            "role":      _ROLE_REVERSE.get(str(row["定位"]), "supporting"),
            "frequency": int(row.get("出现频次", 0)),
        }
    return voice_map


async def generate_audiobook(
    file,
    char_df,
    start_line,
    end_line,
    section_name,
    progress=gr.Progress(),
):
    """主合成函数，供 Gradio 调用"""
    if file is None:
        return None, None, "⚠️ 请先上传小说文件"
    if char_df is None or len(char_df) == 0:
        return None, None, "⚠️ 请先点击「分析角色与声音」"

    section_name = (section_name or "片段").strip()
    output_dir   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        voice_map = _df_to_voice_map(char_df)

        progress(0.02, desc="解析剧本中…")

        def _progress_cb(v: float, desc: str):
            # v 在 0~1 之间，映射到 5%~90%
            progress(0.05 + 0.85 * v, desc=desc)

        output_file, duration = await _generate_audio(
            novel_path=file.name,
            start=int(start_line),
            end=int(end_line),
            name=section_name,
            voice_map=voice_map,
            output_dir=output_dir,
            progress_cb=_progress_cb,
        )

        progress(1.0, desc="合成完成！")
        mins = int(duration // 60)
        secs = int(duration % 60)
        size_kb = os.path.getsize(output_file) / 1024

        status = (
            f"✅ 合成完成！"
            f"  时长：{mins} 分 {secs} 秒  ·  大小：{size_kb:.0f} KB"
        )
        return output_file, output_file, status

    except Exception as e:
        return None, None, f"❌ 合成失败：{e}"


# ─────────────────────────────────────────────────────
# Gradio 界面
# ─────────────────────────────────────────────────────
_theme = gr.themes.Soft(
    primary_hue=gr.themes.colors.orange,
    neutral_hue=gr.themes.colors.stone,
    font=[gr.themes.GoogleFont("Noto Serif SC"), "Georgia", "serif"],
)

with gr.Blocks(title="有声书工坊", theme=_theme, css=CUSTOM_CSS) as demo:

    # ── 标题 ──────────────────────────────────────────
    gr.HTML("""
    <div class="app-header">
        <h1>📖 有声书工坊</h1>
        <div class="ornament">✦ ✦ ✦</div>
        <p class="subtitle">上传小说，自动识别角色，一键生成有声书</p>
    </div>
    """)

    # ── 第一步：上传 + 分析 ───────────────────────────
    gr.HTML('<div class="section-label">一 &nbsp;·&nbsp; 上传小说</div>')
    with gr.Row(equal_height=True):
        with gr.Column(scale=1):
            file_input = gr.File(
                label="选择 txt 文件（UTF-8 编码）",
                file_types=[".txt"],
            )
            analyze_btn = gr.Button(
                "🔍  自动分析角色与声音",
                variant="primary",
                size="lg",
            )
            analyze_status = gr.Textbox(
                label="",
                show_label=False,
                interactive=False,
                placeholder="分析结果将显示在此处…",
                lines=2,
            )

        with gr.Column(scale=2):
            gr.HTML('<div class="section-label">二 &nbsp;·&nbsp; 角色配音（可直接编辑表格）</div>')
            char_table = gr.Dataframe(
                headers=["角色", "性别", "定位", "声音", "语速", "音调", "出现频次"],
                datatype=["str", "str", "str", "str", "str", "str", "str"],
                interactive=True,
                label="",
                wrap=True,
            )
            gr.HTML(VOICE_REF_HTML)

    # ── 第二步：合成设置 ──────────────────────────────
    gr.HTML('<div class="section-label">三 &nbsp;·&nbsp; 合成设置</div>')
    with gr.Row():
        start_line   = gr.Number(label="起始行（从 0 计）", value=0,   precision=0, minimum=0, scale=1)
        end_line     = gr.Number(label="结束行",            value=500, precision=0, minimum=1, scale=1)
        section_name = gr.Textbox(
            label="章节名称",
            placeholder="例：第一章_初遇  （用于文件命名）",
            scale=2,
        )
    generate_btn = gr.Button("🎙  开始合成有声书", variant="primary", size="lg")
    gen_status   = gr.Textbox(label="", show_label=False, interactive=False, lines=2)

    # ── 第三步：结果 ──────────────────────────────────
    gr.HTML('<div class="section-label">四 &nbsp;·&nbsp; 试听与下载</div>')
    with gr.Row():
        audio_out = gr.Audio(label="试听", type="filepath", scale=3)
        file_out  = gr.File(label="下载 MP3", scale=1)

    # ── 页脚 ──────────────────────────────────────────
    gr.HTML("""
    <div class="app-footer">
        ✦ &nbsp; 由 Microsoft Edge TTS 免费驱动 &nbsp;·&nbsp; 仅供个人学习使用 &nbsp; ✦
    </div>
    """)

    # ── 事件绑定 ──────────────────────────────────────
    analyze_btn.click(
        fn=analyze_novel,
        inputs=[file_input],
        outputs=[char_table, analyze_status],
    )

    generate_btn.click(
        fn=generate_audiobook,
        inputs=[file_input, char_table, start_line, end_line, section_name],
        outputs=[audio_out, file_out, gen_status],
    )


if __name__ == "__main__":
    import time
    try:
        demo.launch()
    except ValueError as e:
        if "localhost is not accessible" in str(e):
            # gradio-client schema bug causes health check to fail,
            # but uvicorn is already running — just keep the main thread alive.
            print("\n App is running at: http://127.0.0.1:7860")
            print("Press Ctrl+C to stop\n")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            raise
