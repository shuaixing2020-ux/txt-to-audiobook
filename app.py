"""
有声书工坊 · 网页界面（完整工作流版）
------------------------------------
运行：
    .venv/bin/python app.py                      # → http://127.0.0.1:7860
    AUDIOBOOK_ENGINE=say .venv/bin/python app.py # 网络不通时用离线引擎启动

页面覆盖了日常全部流程，命令行不再是必需品：
    上传 txt → 切章（写入 samples/）→ 角色配音（可改、可存、可复用）
    → 选章节批量合成（实时日志 + 断点续跑）→ 产物库试听/下载
    另附「单章精修」：按行号区间合成，并生成随音频高亮的同步字幕。

业务逻辑都在 scripts/studio.py（不依赖 gradio），这里只做界面绑定。
"""

from __future__ import annotations

import html as _html
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Monkey-patch: gradio-client 处理 Dataframe 的 JSON schema 时，
#    遇到 additionalProperties:true (bool) 会抛 TypeError。
#    在导入 gradio 之前打补丁，让它安全降级为 "any"。
try:
    import gradio_client.utils as _gcu

    _orig_jschema = _gcu.json_schema_to_python_type

    def _safe_jschema(schema, defs=None):
        try:
            return _orig_jschema(schema, defs)
        except TypeError:
            return "any"

    _gcu.json_schema_to_python_type = _safe_jschema
except Exception:                                  # noqa: BLE001
    pass

import gradio as gr                                # noqa: E402

from scripts import studio                         # noqa: E402
from scripts.split import DEFAULT_PATTERN          # noqa: E402

APP_VERSION = "2.0"

# ─────────────────────────────────────────────────────
# 声音参考（界面展示用）
# ─────────────────────────────────────────────────────
from scripts.analyze_characters import (           # noqa: E402
    EXTRA_VOICES, FEMALE_VOICES, MALE_VOICES, NARRATOR_VOICE,
)


def _short(v: str) -> str:
    return v.replace("zh-CN-", "").replace("zh-HK-", "HK·").replace("zh-TW-", "TW·")


VOICE_REF_HTML = (
    '<div class="voice-ref">'
    "<strong>可用声音一览</strong>（表格里写全名或只写后缀都行，如 <code>YunxiNeural</code>）<br>"
    "🎀 <b>女声</b>：" + " &nbsp;·&nbsp; ".join(_short(v[0]) for v in FEMALE_VOICES) + "<br>"
    "🎙 <b>男声</b>：" + " &nbsp;·&nbsp; ".join(_short(v[0]) for v in MALE_VOICES) + "<br>"
    "🎧 <b>旁白默认</b>：" + _short(NARRATOR_VOICE[0]) + "<br>"
    '<small style="color:#9e8b7a">还有 ' + str(len(EXTRA_VOICES)) +
    " 个备用声音（Xiaochen / Xiaohan / Yunye / Yunze / HK·HiuMaan …），直接填进表格即可</small>"
    "</div>"
)

# ─────────────────────────────────────────────────────
# 样式
# ─────────────────────────────────────────────────────
CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600&display=swap');

body, .gradio-container, .main {
    background-color: #f5f0e8 !important;
    font-family: 'Noto Serif SC', 'Georgia', 'SimSun', serif !important;
}

.app-header { text-align: center; padding: 2.2rem 1rem 1.2rem;
    border-bottom: 1px solid #d4bc96; margin-bottom: 1rem; }
.app-header h1 { font-size: 2.2em; color: #2c1810; font-weight: 600;
    letter-spacing: 0.2em; margin: 0 0 0.4rem; }
.app-header .subtitle { color: #7a6051; font-size: 0.95em; font-style: italic;
    letter-spacing: 0.12em; }
.app-header .ornament { color: #c4a882; font-size: 1.1em; letter-spacing: 0.4em; margin: 0.5rem 0; }

.section-label { font-size: 0.88em; font-weight: 600; color: #5c4033;
    letter-spacing: 0.15em; border-left: 3px solid #c4a882;
    padding: 0.2em 0 0.2em 0.8em; margin: 1.6rem 0 0.6rem; }

.voice-ref { background: #ede8de; border: 1px solid #d4bc96; border-radius: 6px;
    padding: 0.75rem 1rem; font-size: 0.82em; color: #5c4033; line-height: 2;
    margin-top: 0.5rem; }
.voice-ref code { background: #f5f0e8; padding: 0 0.3em; border-radius: 3px; }

.stat-bar { display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 0.2rem 0 0.4rem; }
.stat-bar .chip { background: #ede8de; border: 1px solid #d4bc96; border-radius: 999px;
    padding: 0.2rem 0.85rem; font-size: 0.8em; color: #5c4033; letter-spacing: 0.05em; }
.stat-bar .chip b { color: #2c1810; font-weight: 600; }

.app-footer { text-align: center; color: #b09880; font-style: italic; font-size: 0.82em;
    margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #d4bc96; letter-spacing: 0.1em; }

button.lg { letter-spacing: 0.08em !important; }

/* 日志区 */
.log-box textarea { font-family: 'SF Mono', Menlo, monospace !important;
    font-size: 0.78em !important; line-height: 1.7 !important; background: #faf7f2 !important; }

/* 进度条 */
.prog-wrap { background: #faf7f2; border: 1px solid #d4bc96; border-radius: 8px;
    padding: 0.7rem 0.9rem 0.6rem; margin: 0.4rem 0 0.2rem; }
.prog-meta { display: flex; justify-content: space-between; font-size: 0.8em;
    color: #5c4033; letter-spacing: 0.05em; margin-bottom: 0.45rem; }
.prog-meta b { color: #2c1810; }
.prog-track { height: 8px; background: #e8ddc8; border-radius: 999px; overflow: hidden; }
.prog-bar { height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, #c4a882, #8c6239); transition: width 0.4s ease; }
.prog-detail { font-size: 0.76em; color: #9e8b7a; font-style: italic; margin-top: 0.4rem;
    min-height: 1.2em; }
.prog-wrap.paused .prog-bar { background: linear-gradient(90deg, #d8c9a8, #b09880); }
.prog-wrap.stopped .prog-bar { background: linear-gradient(90deg, #c9b8a0, #9e8b7a); }
.prog-wrap.done .prog-bar { background: linear-gradient(90deg, #a8bf8f, #6f8b52); }

/* 控制按钮行 */
.ctl-row { gap: 0.4rem !important; }

/* 章节多选：35 章以上会撑爆页面，限高滚动 */
#chapter-picker { max-height: 260px; overflow-y: auto; }
#tr-picker { max-height: 220px; overflow-y: auto; }

/* 转译实时面板 */
.hint-line { font-size: 0.78em; color: #9e8b7a; font-style: italic; margin: 0.2rem 0 0.4rem; }
.hint-line code { background: #ede8de; padding: 0 0.3em; border-radius: 3px; }
.tr-panel { height: 360px; overflow-y: auto; border: 1px solid #d4bc96; border-radius: 8px;
    background: #faf7f2; padding: 0.6rem 0.8rem; scroll-behavior: smooth; }
.tr-empty { color: #b09880; text-align: center; padding: 3rem 1rem;
    font-size: 0.86em; font-style: italic; }
.tr-line { display: flex; gap: 0.6rem; padding: 0.22rem 0.3rem; font-size: 0.84em;
    line-height: 1.7; border-bottom: 1px dashed #ece2d2; animation: trIn 0.25s ease; }
@keyframes trIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; } }
.tr-line .tr-role { flex: none; width: 5.5em; font-weight: 600; color: #8c6239;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tr-line .tr-text { flex: 1; color: #3a2010; }
.tr-line.tr-narrator .tr-role { color: #9e8b7a; font-weight: 500; }
.tr-line.tr-narrator .tr-text { color: #6b5a49; }

/* 拖拽排序 */
.drag-hint { font-size: 0.76em; color: #9e8b7a; font-style: italic; margin: 0.5rem 0 0.3rem; }
.role-drag { display: flex; flex-wrap: wrap; gap: 0.35rem; padding: 0.45rem;
    background: #faf7f2; border: 1px dashed #d4bc96; border-radius: 8px; min-height: 42px; }
.drag-item { display: flex; align-items: center; gap: 0.3rem; padding: 0.25rem 0.6rem;
    background: #f1e9db; border: 1px solid #ddceb4; border-radius: 999px;
    font-size: 0.8em; cursor: grab; user-select: none; transition: transform .12s; }
.drag-item:hover { background: #ece1cd; }
.drag-item.dragging { opacity: 0.45; transform: scale(0.96); }
.drag-item .drag-handle { color: #c4a882; letter-spacing: -1px; }
.drag-item .drag-name { font-weight: 600; }
.drag-item .drag-meta { font-size: 0.85em; color: #a08a72; }

/* 五步流程条 */
.stepper { display: flex; gap: 0.4rem; margin: 0.3rem 0 0.5rem; flex-wrap: wrap; }
.stepper .step { display: flex; align-items: center; gap: 0.45rem; flex: 1 1 0;
    min-width: 150px; padding: 0.5rem 0.7rem; border: 1px solid #ddd0b8;
    border-radius: 8px; background: #f7f2e9; font-size: 0.8em; color: #8d7965; }
.stepper .step .num { width: 20px; height: 20px; border-radius: 50%; flex: none;
    display: flex; align-items: center; justify-content: center;
    background: #e2d5bd; color: #7a6051; font-size: 0.85em; font-weight: 600; }
.stepper .step .t { font-weight: 600; letter-spacing: 0.04em; }
.stepper .step .v { margin-left: auto; font-size: 0.9em; opacity: 0.85; }
.stepper .step.done { background: #eef1e4; border-color: #b9c79b; color: #5c6b3d; }
.stepper .step.done .num { background: #9fb072; color: #fff; }
.stepper .step.current { background: #f6ead6; border-color: #c4a882; color: #7a5326;
    box-shadow: 0 1px 0 #d9c3a0; }
.stepper .step.current .num { background: #b98a4e; color: #fff; }

/* 剧本校对面板 */
.script-panel { max-height: 520px; overflow-y: auto; border: 1px solid #d4bc96;
    border-radius: 8px; background: #faf7f2; padding: 0.5rem; }
.script-stats { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 0.2rem 0 0.5rem; }
.script-stats .s { background: #ede8de; border: 1px solid #d4bc96; border-radius: 6px;
    padding: 0.3rem 0.7rem; font-size: 0.78em; color: #5c4033; }
.script-stats .s b { color: #2c1810; font-size: 1.15em; }
.sline { display: flex; gap: 0.55rem; padding: 0.28rem 0.5rem; border-radius: 5px;
    font-size: 0.82em; line-height: 1.65; border-left: 3px solid transparent; }
.sline:nth-child(odd) { background: #f3ede2; }
.sline .idx { color: #b09880; width: 2.4em; flex: none; text-align: right;
    font-family: 'SF Mono', Menlo, monospace; font-size: 0.9em; }
.sline .who { flex: none; width: 5.2em; font-weight: 600; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
.sline .tag { flex: none; width: 3.2em; font-size: 0.85em; color: #9e8b7a; }
.sline .txt { flex: 1; color: #3a2010; }
.sline .len { flex: none; width: 2.6em; text-align: right; color: #b09880;
    font-family: 'SF Mono', Menlo, monospace; font-size: 0.85em; }
.sline.skip { opacity: 0.45; }
.sline.skip .txt { text-decoration: line-through; }
.sline.narrator .txt { color: #6b5a49; }
.role-bar { display: flex; align-items: center; gap: 0.5rem; font-size: 0.78em;
    margin: 0.15rem 0; color: #5c4033; }
.role-bar .name { width: 6em; flex: none; font-weight: 600; }
.role-bar .track { flex: 1; height: 8px; background: #e8ddc8; border-radius: 999px; }
.role-bar .fill { height: 100%; border-radius: 999px; }
.role-bar .pct { width: 4.5em; text-align: right; color: #9e8b7a; }

/* 同步文本面板 */
#sync-lines { height: 380px; overflow-y: auto; padding: 0.8rem 1rem;
    background: #faf7f2; border: 1px solid #d4bc96; border-radius: 8px;
    font-family: 'Noto Serif SC', Georgia, serif; font-size: 0.88em; line-height: 2; }
.sync-line { padding: 0.3rem 0.7rem; margin: 0.1rem 0; border-left: 3px solid transparent;
    border-radius: 4px; transition: background 0.25s, border-color 0.25s; }
.sync-line.active { background: #e8d5b0; border-left-color: #c4a882; }
.sync-role { font-size: 0.75em; font-weight: 600; margin-right: 0.5em; opacity: 0.7; }
"""

_SYNC_JS = """
() => {
    // 页面上有多个 <audio>，只绑定「单章精修」那个；换 src 后自动重绑
    if (window._syncTimer) return;
    function bind(audio) {
        if (window._audioSyncListener) {
            audio.removeEventListener('timeupdate', window._audioSyncListener);
        }
        window._lastActiveSyncLine = null;
        window._audioSyncListener = function() {
            var ms = audio.currentTime * 1000;
            var container = document.getElementById('sync-lines');
            if (!container) return;
            var lines = container.querySelectorAll('.sync-line');
            var found = null;
            for (var i = 0; i < lines.length; i++) {
                var s = parseFloat(lines[i].dataset.start);
                var e = parseFloat(lines[i].dataset.end);
                if (ms >= s && ms < e) { found = lines[i]; break; }
            }
            if (found !== window._lastActiveSyncLine) {
                if (window._lastActiveSyncLine) {
                    window._lastActiveSyncLine.classList.remove('active');
                }
                if (found) {
                    found.classList.add('active');
                    found.scrollIntoView({behavior: 'smooth', block: 'center'});
                }
                window._lastActiveSyncLine = found;
            }
        };
        audio.addEventListener('timeupdate', window._audioSyncListener);
        window._boundSyncSrc = audio.src;
    }
    window._syncTimer = setInterval(function() {
        var audio = document.querySelector('#single-audio audio');
        if (!audio || !audio.src || audio.src === window.location.href) return;
        if (audio.src === window._boundSyncSrc) return;
        bind(audio);
    }, 800);
}
"""

_SYNC_PLACEHOLDER = (
    '<div style="color:#b09880; font-size:0.88em; text-align:center; '
    'padding:3rem 1rem; font-style:italic;">合成完成后，此处将随音频播放同步显示对应文本</div>'
)


_TR_JS = """
() => {
    // 转译面板：行数一变就滚到底，形成「文字跟着进度翻滚」的效果
    if (window._trTimer) return;
    let lastCount = -1;
    window._trTimer = setInterval(function() {
        const p = document.getElementById('tr-panel');
        if (!p) return;
        const n = p.querySelectorAll('.tr-line').length;
        if (n !== lastCount) { lastCount = n; p.scrollTop = p.scrollHeight; }
    }, 350);
}
"""


_DRAG_JS = """
() => {
    // 原生 HTML5 拖拽排序：不引第三方库，离线也能用
    if (window._dragTimer) return;
    let dragEl = null;
    function bind(box) {
        const items = box.querySelectorAll('[data-role]');
        items.forEach(function(el) {
            el.draggable = true;
            el.ondragstart = function(e) {
                dragEl = el; el.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
                try { e.dataTransfer.setData('text/plain', el.dataset.role); } catch (err) {}
            };
            el.ondragend = function() {
                el.classList.remove('dragging');
                const order = [...box.querySelectorAll('[data-role]')]
                    .map(function(x) { return x.dataset.role; }).join('||');
                const ta = document.querySelector('#drag-order textarea');
                if (!ta) return;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value').set;
                setter.call(ta, order);
                ta.dispatchEvent(new Event('input', { bubbles: true }));
            };
            el.ondragover = function(e) {
                e.preventDefault();
                if (!dragEl || dragEl === el) return;
                const r = el.getBoundingClientRect();
                const after = (e.clientY - r.top) > r.height / 2;
                el.parentNode.insertBefore(dragEl, after ? el.nextSibling : el);
            };
        });
    }
    let lastSig = '';
    window._dragTimer = setInterval(function() {
        const box = document.getElementById('role-drag');
        if (!box) return;
        const sig = [...box.querySelectorAll('[data-role]')]
            .map(function(x) { return x.dataset.role; }).join(',');
        if (sig === lastSig) return;
        lastSig = sig;
        bind(box);
    }, 700);
}
"""


def _role_drag_html(df) -> str:
    """可拖拽的角色顺序条（拖完自动写回表格）"""
    rows = _df_rows(df)
    if not rows:
        return ('<div class="drag-hint">识别出人物后，这里会出现可拖拽的角色条，'
                '拖到想要的顺序即可（也可用上面的上移/下移）</div>')
    chips = []
    for r in rows:
        if not r or not str(r[0]).strip():
            continue
        name = str(r[0]).strip()
        role = str(r[4]) if len(r) > 4 else ""
        age = str(r[2]) if len(r) > 2 else ""
        emo = str(r[3]) if len(r) > 3 else ""
        color = "#9e8b7a" if role == "旁白" else "#8c6239"
        chips.append(
            f'<div class="drag-item" data-role="{_html.escape(name)}">'
            f'<span class="drag-handle">⠿</span>'
            f'<span class="drag-name" style="color:{color}">{_html.escape(name)}</span>'
            f'<span class="drag-meta">{_html.escape(age)}{_html.escape(emo)}</span></div>'
        )
    return ('<div class="drag-hint">拖动排序（松手即保存）：</div>'
            f'<div id="role-drag" class="role-drag">{"".join(chips)}</div>')


def _build_sync_html(timings: list) -> str:
    """把时间轴渲染成带 data-start/end 的 HTML，供前端 JS 高亮"""
    if not timings:
        return _SYNC_PLACEHOLDER
    role_colors = {"旁白": "#9e8b7a"}
    parts = []
    for t in timings:
        role = t["role"]
        parts.append(
            f'<div class="sync-line" data-start="{int(t["start"])}" data-end="{int(t["end"])}">'
            f'<span class="sync-role" style="color:{role_colors.get(role, "#3a2010")};">'
            f'[{_html.escape(role)}]</span>{_html.escape(t["text"])}</div>'
        )
    return '<div id="sync-lines">' + "".join(parts) + "</div>"


def _stepper_html(s: dict) -> str:
    """六步流程状态条：让「现在该做什么」一眼可见"""
    steps = [
        ("导入与切章", f'{s["chapters"]} 章', s["chapters"] > 0),
        ("人物与配音", f'{s["roles"]} 角色', s["roles"] > 0),
        ("文本转译", f'{s["translated"]} 章', s["translated"] > 0),
        ("剧本校对", "可预览", s["roles"] > 0),
        ("合成", f'{s["outputs"]} 个成品', s["outputs"] > 0),
        ("产物库", f'{s["output_mb"]} MB', s["outputs"] > 0),
    ]
    first_open = next((i for i, (_, _, done) in enumerate(steps) if not done), len(steps))
    parts = []
    for i, (name, value, done) in enumerate(steps):
        cls = "done" if done else ("current" if i == first_open else "")
        mark = "✓" if done else str(i + 1)
        parts.append(
            f'<div class="step {cls}"><span class="num">{mark}</span>'
            f'<span class="t">{name}</span><span class="v">{value}</span></div>'
        )
    return '<div class="stepper">' + "".join(parts) + "</div>"


def _stat_bar_html() -> str:
    s = studio.summary()
    engine_label = "edge 在线" if s["engine"] == "edge" else "say 离线"
    chips = [
        ("引擎", engine_label),
        ("并发", f'{s["workers"]} 路'),
        ("间隔", f'{s["interval"]:.1f}s'),
        ("已转译", f'{s["translated"]} 章'),
        ("角色库", f'{s["library"]} 人'),
        ("AI 识别", "已配置" if s["llm"] else "未配置"),
        ("缓存", f'{s["cache_mb"]} MB'),
    ]
    bar = '<div class="stat-bar">' + "".join(
        f'<span class="chip">{k} <b>{v}</b></span>' for k, v in chips) + "</div>"
    return _stepper_html(s) + bar


# ─────────────────────────────────────────────────────
# 回调：切章
# ─────────────────────────────────────────────────────
def cb_split_preview(file, pattern, max_chars):
    if file is None:
        return [], "⚠️ 请先上传小说 txt", gr.update()
    try:
        rows = studio.preview_rows(file.name, pattern, int(max_chars))
    except Exception as e:                          # noqa: BLE001
        return [], f"❌ 切章失败：{e}", gr.update()
    if not rows:
        return [], "⚠️ 没匹配到任何章节标题，可放宽正则或检查编码", gr.update()
    total = sum(r[2] for r in rows)
    return (rows,
            f"✅ 共 {len(rows)} 章，合计 {total} 字（预览，未写文件）",
            gr.update(), gr.update())


def cb_split_write(file, pattern, max_chars):
    if file is None:
        return [], "⚠️ 请先上传小说 txt", gr.update()
    job = studio.current_job()
    if job is not None and job.is_running():
        return [], "⚠️ 正在合成中，重新切章会让正在读的文件被挪走。请先「停止」再切。", gr.update()
    try:
        res = studio.write_chapters(file.name, pattern, int(max_chars))
        rows = studio.preview_rows(file.name, pattern, int(max_chars))
    except Exception as e:                          # noqa: BLE001
        return [], f"❌ 写入失败：{e}", gr.update()

    msg = [f"✅ 已写入 {res['count']} 章 / {res['total_chars']} 字 → samples/"]
    if res["removed"]:
        msg.append(f"♻️ {len(res['removed'])} 个旧章节已移到 samples/.stale/（未删除）")
    chapters = studio.list_chapters()
    return (rows, "\n".join(msg),
            gr.update(choices=chapters, value=chapters),
            gr.update(choices=chapters, value=chapters))


def cb_refresh():
    chapters = studio.list_chapters()
    outputs = studio.list_outputs()
    return (gr.update(choices=chapters, value=chapters),
            studio.chapter_stats(), outputs,
            gr.update(choices=[r[0] for r in outputs]), _stat_bar_html(),
            gr.update(choices=chapters, value=chapters))


def cb_sync_role_choices(df):
    """表格变化时同步「排序对象」下拉"""
    names = [str(r[0]).strip() for r in _df_rows(df) if r and str(r[0]).strip()]
    return gr.update(choices=names)


def cb_sync_output_choices(df):
    """产物表格变化时同步下拉框选项"""
    if df is None:
        rows = []
    elif hasattr(df, "values"):
        rows = df.values.tolist()
    else:
        rows = df
    return gr.update(choices=[str(r[0]) for r in rows if r])


# ─────────────────────────────────────────────────────
# 回调：设置 / 角色 / 配置
# ─────────────────────────────────────────────────────
def cb_apply_settings(engine, interval, workers):
    e, i = studio.set_engine(engine, interval, workers)
    label = "edge（微软在线）" if e == "edge" else "say（macOS 离线）"
    return (f"✅ {label} · 并发 {studio.gen._WORKERS} 路 · 间隔 {i:.1f}s", _stat_bar_html())


def cb_analyze(file, use_llm):
    if file is None:
        return None, "⚠️ 请先上传小说 txt"
    try:
        vm, note = studio.analyze_source_ex(file.name, bool(use_llm))
    except Exception as e:                          # noqa: BLE001
        return None, f"❌ 分析失败：{e}"
    rows = studio.voice_map_rows(vm)
    n = len([r for r in rows if r[2] != "旁白"])
    return rows, f"{note}｜共 {n} 个角色。可直接改表格（含别名），改完「保存为默认配置」"


# ── 大模型设置 ───────────────────────────────────────
def cb_llm_preset(preset):
    info = studio.llm.PRESETS.get(preset, {})
    return (gr.update(value=info.get("base_url", "")),
            gr.update(value=info.get("model", "")))


def _llm_cfg_from_ui(preset, base_url, model, key):
    cfg = studio.llm_config()
    cfg.update({"preset": preset, "base_url": (base_url or "").strip(),
                "model": (model or "").strip()})
    if key:
        cfg["api_key"] = key.strip()
    return cfg


def cb_llm_save(preset, base_url, model, key, enabled):
    cfg = _llm_cfg_from_ui(preset, base_url, model, key)
    cfg["enabled"] = bool(enabled)
    path = studio.llm_save(cfg)
    ok, why = studio.llm_status(cfg)
    flag = "✅ 可用" if ok else f"⚠️ {why}"
    return f"已保存到 {os.path.relpath(path, ROOT)}｜{flag}", _stat_bar_html()


def cb_llm_test(preset, base_url, model, key):
    cfg = _llm_cfg_from_ui(preset, base_url, model, key)
    try:
        reply = studio.llm_test(cfg)
    except Exception as e:                          # noqa: BLE001
        return f"❌ 连接失败：{str(e)[:200]}"
    return f"✅ 连接成功，模型回复：{reply}"


# ── 角色库 ───────────────────────────────────────────
def cb_library_save(df):
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return "⚠️ 表格是空的", gr.update(), studio.library_rows()
    n = studio.library_upsert(vm)
    lib = studio.library_load()
    return (f"✅ 已把 {n} 个角色存入角色库（共 {len(lib)} 人，同名覆盖）",
            gr.update(choices=sorted(lib), value=[]), studio.library_rows())


def cb_library_load(names, df):
    if not names:
        return gr.update(), "⚠️ 先在库里选要载入的角色"
    lib_vm = studio.library_apply(list(names))
    if not lib_vm:
        return gr.update(), "⚠️ 库里的这些角色读不出来"
    cur = studio.rows_to_voice_map(df)
    cur.update(lib_vm)
    return studio.voice_map_rows(cur), f"✅ 已载入 {len(lib_vm)} 个角色（同名覆盖当前表格）"


def cb_library_delete(names):
    if not names:
        return "⚠️ 先选要删除的角色", gr.update(), studio.library_rows()
    n = studio.library_delete(list(names))
    lib = studio.library_load()
    return (f"🗑 已从角色库删除 {n} 个",
            gr.update(choices=sorted(lib), value=[]), studio.library_rows())


def cb_library_refresh():
    lib = studio.library_load()
    return gr.update(choices=sorted(lib)), studio.library_rows()


# ── 剧本校对 ─────────────────────────────────────────
_ROLE_COLORS = ["#8c6239", "#4f6d7a", "#7a4f6d", "#5d7a4f", "#7a6a4f",
                "#4f5d7a", "#7a4f4f", "#4f7a6d", "#6d4f7a", "#7a7a4f"]


def _role_color(role: str, roles: list[str]) -> str:
    if role == "旁白":
        return "#9e8b7a"
    try:
        return _ROLE_COLORS[roles.index(role) % len(_ROLE_COLORS)]
    except ValueError:
        return "#8c6239"


def _script_panel_html(preview: dict, only_role: str = "全部", only_skip: bool = False) -> str:
    if not preview or not preview.get("rows"):
        return ('<div class="script-panel"><div style="color:#b09880;text-align:center;'
                'padding:2.5rem 1rem;font-style:italic;">点「生成剧本预览」后，'
                '这里逐句显示谁在说、哪些不朗读</div></div>')

    st = preview["stats"]
    roles = [r["role"] for r in preview["roles"]]
    head = (
        '<div class="script-stats">'
        f'<div class="s">总句数 <b>{st["total"]}</b></div>'
        f'<div class="s">朗读 <b>{st["read"]}</b></div>'
        f'<div class="s">跳过 <b>{st["skipped"]}</b></div>'
        f'<div class="s">有效字数 <b>{st["chars"]}</b></div>'
        '</div>'
    )
    bars = ""
    for r in preview["roles"][:10]:
        color = _role_color(r["role"], roles)
        bars += (f'<div class="role-bar"><span class="name" style="color:{color}">'
                 f'{_html.escape(r["role"])}</span>'
                 f'<span class="track"><span class="fill" style="width:{r["percent"]}%;'
                 f'background:{color}"></span></span>'
                 f'<span class="pct">{r["lines"]} 句 / {r["percent"]}%</span></div>')

    lines = ""
    shown = 0
    for idx, kind, role, text, chars, status in preview["rows"]:
        if only_role != "全部" and role != only_role:
            continue
        if only_skip and not str(status).startswith("跳过"):
            continue
        shown += 1
        cls = "sline"
        if str(status).startswith("跳过"):
            cls += " skip"
        elif role == "旁白":
            cls += " narrator"
        color = _role_color(role, roles)
        lines += (
            f'<div class="{cls}"><span class="idx">{idx}</span>'
            f'<span class="who" style="color:{color}">{_html.escape(role)}</span>'
            f'<span class="tag">{_html.escape(str(kind))}</span>'
            f'<span class="txt">{_html.escape(str(text))}</span>'
            f'<span class="len">{chars}</span></div>'
        )
    note = ""
    if shown == 0:
        note = '<div style="color:#b09880;text-align:center;padding:1.5rem;">当前筛选下没有内容</div>'
    if len(preview["rows"]) >= 600:
        note += ('<div style="color:#b09880;text-align:center;padding:0.6rem;'
                 'font-size:0.78em;font-style:italic;">仅显示前 600 条，统计仍按全量计算</div>')

    return (f'{head}{bars}<div class="script-panel">{lines}{note}</div>')


def cb_script_preview(file, df, start, end):
    if file is None:
        return {}, _script_panel_html({}), "⚠️ 请先上传小说 txt", gr.update()
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return {}, _script_panel_html({}), "⚠️ 先识别角色（第二节），再生成剧本预览", gr.update()
    try:
        preview = studio.script_preview(file.name, vm, int(start or 0), int(end or 10 ** 7))
    except Exception as e:                          # noqa: BLE001
        return {}, _script_panel_html({}), f"❌ 预览失败：{e}", gr.update()
    st = preview["stats"]
    msg = (f"✅ 共 {st['total']} 句：朗读 {st['read']}、跳过 {st['skipped']}，"
           f"有效 {st['chars']} 字")
    roles = ["全部"] + [r["role"] for r in preview["roles"]]
    return preview, _script_panel_html(preview), msg, gr.update(choices=roles, value="全部")


def cb_script_filter(preview, role, only_skip):
    return _script_panel_html(preview or {}, role or "全部", bool(only_skip))


def cb_save_config(df):
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return "⚠️ 表格是空的，没什么可保存"
    path = studio.save_voice_map(vm)
    return f"✅ 已保存 {len(vm)} 个角色 → {os.path.relpath(path, ROOT)}（下次启动自动复用）"


# ── 角色排序 / 年龄情绪 ──────────────────────────────
def _df_rows(df):
    if df is None:
        return []
    return df.values.tolist() if hasattr(df, "values") else [list(r) for r in df]


def cb_move_role(df, name, delta):
    if not name:
        return gr.update(), "⚠️ 先在下面选一个角色"
    rows = studio.move_role(df, name, delta)
    if not rows:
        return gr.update(), "已经在头/尾了"
    return rows, f"{'⬆️ 上移' if delta < 0 else '⬇️ 下移'}：{name}"


def cb_restyle(df):
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return gr.update(), "⚠️ 表格是空的"
    studio.restyle_voice_map(vm)
    return studio.voice_map_rows(vm), "✅ 已按「年龄 + 情绪」重算语速与音调"


def cb_apply_order(order_text, df):
    """拖拽排序回写：'A||B||C' → 按这个顺序重排表格"""
    names = [n.strip() for n in (order_text or "").split("||") if n.strip()]
    rows = _df_rows(df)
    if not names or not rows:
        return gr.update(), ""
    index = {str(r[0]).strip(): r for r in rows if r and str(r[0]).strip()}
    ordered = [index.pop(n) for n in names if n in index]
    ordered += [r for r in rows if str(r[0]).strip() in index]     # 剩下的接在后面
    return ordered, f"↕️ 已按拖拽顺序保存（{len(ordered)} 个角色）"


# ── 文本转译 ─────────────────────────────────────────
def cb_start_translate(chapters, df, mode):
    if not chapters:
        return "⚠️ 先在下面勾选要转译的章节", {}, gr.update(active=False)
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return "⚠️ 配音表是空的：先「识别人物」", {}, gr.update(active=False)
    try:
        studio.start_translate_job(list(chapters), vm, mode)
    except RuntimeError as e:                       # noqa: BLE001
        return f"⚠️ {e}", {}, gr.update(active=False)
    return "已开始转译（可随时暂停 / 停止）", {}, gr.update(active=True)


def _translate_panel_html(snap: dict | None) -> str:
    if not snap:
        return ('<div class="tr-panel" id="tr-panel"><div class="tr-empty">'
                '点「开始转译」后，这里会逐块显示「角色｜台词」的转译结果，'
                '并随进度自动翻滚到底部</div></div>')
    lines = snap.get("lines") or []
    if not lines:
        return ('<div class="tr-panel" id="tr-panel"><div class="tr-empty">'
                '正在准备第一块…</div></div>')
    parts = []
    for ln in lines:
        role, _, body = ln.partition("｜")
        cls = "tr-narrator" if role == "旁白" else "tr-dialog"
        parts.append(f'<div class="tr-line {cls}"><span class="tr-role">'
                     f'{_html.escape(role)}</span>'
                     f'<span class="tr-text">{_html.escape(body)}</span></div>')
    return '<div class="tr-panel" id="tr-panel">' + "".join(parts) + "</div>"


def cb_load_config():
    vm = studio.load_voice_map()
    if vm is None:
        return None, "⚠️ 还没有保存过配置（点「保存为默认配置」生成 config/voice_map.json）"
    rows = studio.voice_map_rows(vm)
    return rows, f"✅ 已读入 {len(rows)} 个角色的配音配置"


def cb_load_saved_on_startup():
    """页面加载时：有配置就自动填表，角色库也一并读出来"""
    chapters = studio.list_chapters()
    picker = gr.update(choices=chapters, value=chapters)
    outputs = studio.list_outputs()
    picker_out = gr.update(choices=[r[0] for r in outputs])
    vm = studio.load_voice_map()
    table = studio.voice_map_rows(vm) if vm else None
    lib = studio.library_load()
    return (table, picker, studio.chapter_stats(), outputs, picker_out,
            gr.update(choices=sorted(lib)), studio.library_rows(), _stat_bar_html(),
            gr.update(choices=chapters, value=chapters), _role_drag_html(table),
            gr.update(choices=[str(r[0]).strip() for r in (table or [])]))


# ─────────────────────────────────────────────────────
# 回调：样音试听
# ─────────────────────────────────────────────────────
def cb_preview_voice(text, voice, rate, pitch, engine, interval):
    studio.set_engine(engine, interval)
    try:
        path = studio.preview_voice(text, voice, rate, pitch)
    except Exception as e:                          # noqa: BLE001
        return None, f"❌ {e}"
    return path, f"✅ 样音已生成（{os.path.getsize(path) / 1024:.0f} KB）"


# ─────────────────────────────────────────────────────
# 回调：批量合成（流式日志）
# ─────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────
# 回调：后台任务（开始 / 暂停 / 停止 / 跳过 / 轮询）
# ─────────────────────────────────────────────────────
_STATUS_LABEL = {
    "ready": "准备中", "running": "合成中", "paused": "已暂停",
    "stopping": "正在停止", "done": "已完成", "stopped": "已停止",
}


def _progress_html(snap: dict | None) -> str:
    if not snap:
        return ('<div class="prog-wrap"><div class="prog-meta"><span>空闲</span>'
                '<span>—</span></div><div class="prog-track">'
                '<div class="prog-bar" style="width:0%"></div></div>'
                '<div class="prog-detail">点「开始批量合成」后这里显示实时进度</div></div>')
    pct = int(round(snap["progress"] * 100))
    label = _STATUS_LABEL.get(snap["status"], snap["status"])
    elapsed = int(snap["elapsed"])
    mmss = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
    counts = f'✅{snap["ok"]} · ⏭{snap["skip"]} · ❌{snap["fail"]}'
    detail = snap.get("detail") or (
        "已停止，可再点「开始」续跑" if snap["status"] == "stopped" else "")
    return (
        f'<div class="prog-wrap {snap["status"]}">'
        f'<div class="prog-meta"><span><b>{label}</b> · '
        f'{snap["index"]}/{snap["total"]} 个任务 · {pct}%</span>'
        f'<span>{counts} &nbsp;·&nbsp; {mmss}</span></div>'
        f'<div class="prog-track"><div class="prog-bar" style="width:{pct}%"></div></div>'
        f'<div class="prog-detail">{_html.escape(detail)}</div></div>'
    )


def _status_line(snap: dict) -> str:
    label = _STATUS_LABEL.get(snap["status"], snap["status"])
    verb = "转译" if snap.get("kind") == "translate" else "合成"
    if snap["status"] == "running":
        return f"{label}：第 {snap['index']}/{snap['total']} 个任务（可随时暂停或停止）"
    if snap["status"] == "paused":
        return f"已暂停：点「继续」接着{verb}，已完成的部分不会重做"
    if snap["status"] == "stopping":
        return f"正在停止：当前这一步做完就退出"
    if snap["status"] == "done":
        return f"完成：成功 {snap['ok']} / 跳过 {snap['skip']} / 失败 {snap['fail']}"
    if snap["status"] == "stopped":
        return (f"已停止：成功 {snap['ok']} / 跳过 {snap['skip']} / 失败 {snap['fail']}"
                f"，再点「开始」可续跑")
    return label


def cb_start_batch(chapters, df, force, use_translated, engine, interval, workers):
    if not chapters:
        return "⚠️ 请先在上方勾选要合成的章节", {}, gr.update(active=False)
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return "⚠️ 配音表是空的：先「识别人物」或「读入默认配置」", {}, gr.update(active=False)
    studio.set_engine(engine, interval, workers)
    try:
        studio.start_job(studio.chapter_tasks(chapters, bool(use_translated)),
                         vm, bool(force), kind="batch")
    except RuntimeError as e:                       # noqa: BLE001
        return f"⚠️ {e}", {}, gr.update(active=False)
    return ("已开始（随时可以暂停 / 停止 / 跳过当前章）", {}, gr.update(active=True))


def cb_start_single(file, df, start, end, name, engine, interval, workers):
    if file is None:
        return "⚠️ 请先上传小说文件", {}, gr.update(active=False)
    vm = studio.rows_to_voice_map(df)
    if not vm:
        return "⚠️ 请先「分析角色与声音」或「读入默认配置」", {}, gr.update(active=False)
    studio.set_engine(engine, interval)
    try:
        studio.start_job(
            studio.single_task(file.name, name, int(start or 0), int(end or 10 ** 7)),
            vm, force=True, kind="single")
    except RuntimeError as e:                       # noqa: BLE001
        return f"⚠️ {e}", {}, gr.update(active=False)
    return "已开始合成（可点「停止」中断）", {}, gr.update(active=True)


def cb_pause():
    job = studio.current_job()
    return job.toggle_pause() if job else "当前没有在跑的任务"


def cb_stop():
    job = studio.current_job()
    return job.stop() if job else "当前没有在跑的任务"


def cb_skip():
    job = studio.current_job()
    return job.skip_current() if job else "当前没有在跑的任务"


def cb_poll(state):
    """定时轮询后台任务：刷新日志 / 进度 / 按钮可用态；无变化时返回空更新

    没有任务在跑时会把定时器自己关掉（active=False），避免页面空转。
    """
    state = dict(state or {})
    upd = [gr.update() for _ in range(23)]

    job = studio.current_job()
    if job is None:
        upd[7] = gr.update(interactive=True)
        upd[8] = gr.update(interactive=False, value="⏸  暂停")
        upd[9] = gr.update(interactive=False)
        upd[10] = gr.update(interactive=False)
        upd[15] = gr.update(interactive=True)
        upd[16] = gr.update(interactive=False)
        upd[21] = gr.update(interactive=True)
        upd[22] = gr.update(interactive=False)
        upd[18] = gr.update(active=False)
        return upd

    snap = job.snapshot()
    if snap["id"] != state.get("job"):               # 换了一个新任务 → 重置记忆
        state = {"job": snap["id"], "v": -1, "audio": None, "done": False}

    running = snap["status"] in ("running", "paused", "stopping")
    paused = snap["status"] == "paused"

    if snap["version"] != state.get("v"):
        if snap["kind"] != "translate":
            upd[0] = gr.update(value=snap["log"])
        state["v"] = snap["version"]

    if snap["kind"] == "translate":
        upd[19] = gr.update(value=_translate_panel_html(snap))
        upd[20] = gr.update(value=_status_line(snap) + "\n" + "\n".join(snap["log"].splitlines()[-3:]))
        upd[21] = gr.update(interactive=not running)
        upd[22] = gr.update(interactive=running)
        if snap["status"] in ("done", "stopped") and not state.get("done"):
            state["done"] = True
            upd[4] = studio.list_outputs()
            upd[5] = studio.chapter_stats()
            upd[6] = _stat_bar_html()
        upd[17] = state
        upd[18] = gr.update(active=running)
        return upd

    upd[1] = gr.update(value=_progress_html(snap))
    if snap["kind"] == "batch":
        upd[2] = gr.update(value=_status_line(snap))
    else:
        upd[11] = gr.update(value=snap["log"] or _status_line(snap))

    if snap["latest"] and snap["latest"] != state.get("audio"):
        state["audio"] = snap["latest"]
        if snap["kind"] == "single":
            upd[12] = gr.update(value=snap["latest"])
            upd[13] = gr.update(value=snap["latest"])
            upd[14] = gr.update(value=_build_sync_html(snap["timings"]))
        else:
            upd[3] = gr.update(value=snap["latest"])

    if snap["status"] in ("done", "stopped") and not state.get("done"):
        state["done"] = True
        upd[4] = studio.list_outputs()
        upd[5] = studio.chapter_stats()
        upd[6] = _stat_bar_html()

    upd[7] = gr.update(interactive=not running)
    upd[8] = gr.update(interactive=running and snap["status"] != "stopping",
                       value="▶️  继续" if paused else "⏸  暂停")
    upd[9] = gr.update(interactive=running)
    upd[10] = gr.update(interactive=running and snap["kind"] == "batch")
    upd[15] = gr.update(interactive=not running)
    upd[16] = gr.update(interactive=running and snap["kind"] == "single")

    upd[17] = state
    upd[18] = gr.update(active=running)              # 任务结束即停掉轮询
    return upd


def cb_pick_output(name):
    path = studio.output_path(name)
    if not path:
        return None, None, "⚠️ 文件不存在"
    size_mb = os.path.getsize(path) / 1024 / 1024
    return path, path, f"✅ {name} · {size_mb:.2f} MB"


# ─────────────────────────────────────────────────────
# 界面
# ─────────────────────────────────────────────────────
def build_ui() -> gr.Blocks:
    with gr.Blocks(title="有声书工坊") as demo:

        gr.HTML("""
        <div class="app-header">
            <h1>📖 有声书工坊</h1>
            <div class="ornament">✦ ✦ ✦</div>
            <p class="subtitle">导入 → 人物 → 转译 → 剧本 → 合成 → 产物</p>
        </div>
        """)
        stat_bar = gr.HTML(_stat_bar_html())

        # ══ 一 · 导入与切章 ══════════════════════════════
        gr.HTML('<div class="section-label">一 &nbsp;·&nbsp; 导入与切章</div>')
        with gr.Row(equal_height=False):
            with gr.Column(scale=1):
                file_input = gr.File(label="选择小说 txt（UTF-8 / GBK 自动识别）",
                                     file_types=[".txt"])
                with gr.Accordion("切章参数（一般不用改）", open=False):
                    pattern_box = gr.Textbox(
                        value=DEFAULT_PATTERN, label="章节标题正则",
                        info="匹配「第X章 / 序章 / 楔子 / 番外」这类标题行")
                    max_chars = gr.Slider(2000, 20000, value=8000, step=500,
                                          label="单章最大字数",
                                          info="超过则再切分；8000 字约 12~15 分钟音频")
                with gr.Row():
                    split_btn = gr.Button("✂️  预览章节")
                    split_write_btn = gr.Button("📥  写入 samples/", variant="secondary")
                split_status = gr.Textbox(show_label=False, interactive=False, lines=2,
                                          placeholder="切章结果会显示在这里…")

            with gr.Column(scale=2):
                chapter_df = gr.Dataframe(
                    headers=studio.CHAPTER_TABLE_HEADERS,
                    datatype=["number", "str", "number", "str", "str"],
                    interactive=False, label="章节列表", wrap=True, max_height=420,
                )

        # ══ 二 · 人物与配音 ══════════════════════════════
        gr.HTML('<div class="section-label">二 &nbsp;·&nbsp; 人物与配音</div>')
        with gr.Row(equal_height=False):
            with gr.Column(scale=2):
                with gr.Row():
                    analyze_btn = gr.Button("🔍  识别人物", variant="primary", size="lg", scale=2)
                    use_llm_chk = gr.Checkbox(
                        value=studio.llm_status()[0], label="用大模型识别（更准）", scale=1,
                        info="未配置时自动回退本地规则")
                analyze_status = gr.Textbox(show_label=False, interactive=False, lines=2,
                                            placeholder="识别结果会显示在这里…")
                char_table = gr.Dataframe(
                    headers=studio.VOICE_TABLE_HEADERS,
                    datatype=["str"] * len(studio.VOICE_TABLE_HEADERS),
                    interactive=True, wrap=True, max_height=380,
                    label="角色 → 声音（可直接改；别名用于「谁在说话」匹配）",
                )
                with gr.Row():
                    save_cfg_btn = gr.Button("💾  保存为默认配置")
                    load_cfg_btn = gr.Button("📂  读入默认配置")
                role_drag = gr.HTML(_role_drag_html(None))
                drag_order = gr.Textbox(value="", visible=False, elem_id="drag-order")
                with gr.Row(elem_classes=["ctl-row"]):
                    role_picker = gr.Dropdown(choices=[], label="排序对象", scale=2)
                    up_btn = gr.Button("⬆️ 上移", scale=1)
                    down_btn = gr.Button("⬇️ 下移", scale=1)
                    restyle_btn = gr.Button("🎚 应用年龄/情绪", scale=2)
                cfg_status = gr.Textbox(show_label=False, interactive=False, lines=1)
            with gr.Column(scale=1):
                with gr.Accordion("🤖 大模型识别设置（免费模型即可）", open=False):
                    _cfg = studio.llm_config()
                    llm_preset = gr.Dropdown(
                        choices=[(v["label"], k) for k, v in studio.llm.PRESETS.items()],
                        value=_cfg.get("preset", "zhipu"), label="服务商")
                    llm_base = gr.Textbox(value=_cfg.get("base_url", ""), label="接口地址")
                    llm_model = gr.Textbox(value=_cfg.get("model", ""), label="模型名")
                    llm_key = gr.Textbox(value="", type="password", label="API Key",
                                         placeholder="留空则沿用已保存的 key")
                    with gr.Row():
                        llm_test_btn = gr.Button("🔌  测试连接", scale=1)
                        llm_save_btn = gr.Button("💾  保存", scale=1)
                    llm_status = gr.Textbox(show_label=False, interactive=False, lines=2,
                                            value="；".join(studio.llm_status()[1:]))
                with gr.Accordion("👥 角色库（跨书复用：保存 / 载入 / 删除）", open=False):
                    library_picker = gr.Dropdown(choices=[], multiselect=True,
                                                 label="库里的角色")
                    with gr.Row():
                        lib_load_btn = gr.Button("载入所选", scale=1)
                        lib_del_btn = gr.Button("🗑 删除所选", scale=1)
                        lib_save_btn = gr.Button("把当前表格存入库", scale=1)
                    library_status = gr.Textbox(show_label=False, interactive=False, lines=1)
                    library_df = gr.Dataframe(headers=["角色", "性别", "定位", "声音",
                                                       "语速", "音调", "更新时间"],
                                              interactive=False, max_height=220,
                                              label="角色库")
                gr.HTML(VOICE_REF_HTML)

        # ══ 三 · 文本转译 ════════════════════════════════
        gr.HTML('<div class="section-label">三 &nbsp;·&nbsp; 文本转译（改写成「角色｜台词」）</div>')
        with gr.Row(equal_height=False):
            with gr.Column(scale=1):
                tr_picker = gr.CheckboxGroup(choices=[], label="要转译的章节",
                                             show_select_all=True, elem_id="tr-picker")
                tr_mode = gr.Radio(
                    choices=[("大模型逐章（更准，走 API）", "llm"),
                             ("本地规则（不联网，秒级）", "rules")],
                    value="llm", label="转译方式")
                with gr.Row(elem_classes=["ctl-row"]):
                    tr_btn = gr.Button("🪄  开始转译", variant="primary", size="lg", scale=2)
                    tr_stop = gr.Button("⏹  停止", interactive=False, scale=1)
                tr_status = gr.Textbox(show_label=False, interactive=False, lines=3,
                                       placeholder="转译进度会显示在这里…")
            with gr.Column(scale=2):
                gr.HTML('<div class="hint-line">转译结果实时滚动显示；成品写到 '
                        '<code>translated/</code>，合成时自动优先使用</div>')
                tr_panel = gr.HTML(_translate_panel_html(None))

        # ══ 四 · 剧本校对 ════════════════════════════════
        gr.HTML('<div class="section-label">四 &nbsp;·&nbsp; 剧本校对（谁在说 / 哪些不朗读）</div>')
        with gr.Row():
            sp_start = gr.Number(label="起始行", value=0, precision=0, minimum=0, scale=1)
            sp_end = gr.Number(label="结束行", value=100000, precision=0, minimum=1, scale=1)
            sp_btn = gr.Button("📋  生成剧本预览", variant="primary", scale=2)
            sp_role = gr.Dropdown(choices=["全部"], value="全部", label="只看某角色", scale=1)
            sp_skip = gr.Checkbox(value=False, label="只看跳过的", scale=1)
        script_status = gr.Textbox(show_label=False, interactive=False, lines=1)
        script_panel = gr.HTML(_script_panel_html({}))
        preview_state = gr.State({})

        # ══ 五 · 合成 ════════════════════════════════════
        gr.HTML('<div class="section-label">五 &nbsp;·&nbsp; 合成</div>')
        with gr.Row(equal_height=False):
            engine_radio = gr.Radio(
                choices=[("edge 在线（音质好，需联网）", "edge"),
                         ("say 离线（macOS 系统语音，不联网）", "say")],
                value=studio.engine_state()[0], label="引擎", scale=3,
            )
            interval_slider = gr.Slider(0.0, 5.0, value=studio.engine_state()[1], step=0.1,
                                        label="句间间隔(s)", scale=1, info="限流时调大")
            workers_slider = gr.Slider(1, 12, value=studio.gen._WORKERS, step=1,
                                       label="并发路数", scale=1,
                                       info="越多越快，被限流就调小")
            apply_btn = gr.Button("应用", scale=1)
        settings_status = gr.Textbox(show_label=False, interactive=False, lines=1)

        with gr.Row(equal_height=False):
            with gr.Column(scale=1):
                chapter_picker = gr.CheckboxGroup(choices=[], label="要合成的章节",
                                                  show_select_all=True,
                                                  elem_id="chapter-picker")
                with gr.Row():
                    refresh_btn = gr.Button("🔄  刷新")
                    force_chk = gr.Checkbox(value=False, label="强制重做")
                use_tr_chk = gr.Checkbox(value=True, label="优先使用转译稿",
                                         info="translated/ 里有同名文件就读它")
                batch_btn = gr.Button("🎙  开始批量合成", variant="primary", size="lg")
                batch_status = gr.Textbox(show_label=False, interactive=False, lines=2)
            with gr.Column(scale=2):
                batch_progress = gr.HTML(_progress_html(None))
                with gr.Row(elem_classes=["ctl-row"]):
                    pause_btn = gr.Button("⏸  暂停", interactive=False, scale=1)
                    stop_btn = gr.Button("⏹  停止", interactive=False, scale=1)
                    skip_btn = gr.Button("⏭  跳过本章", interactive=False, scale=1)
                batch_log = gr.Textbox(value="", label="运行日志", lines=12, max_lines=20,
                                       interactive=False, autoscroll=True,
                                       elem_classes=["log-box"])
                batch_audio = gr.Audio(label="最新成品", type="filepath")

        with gr.Accordion("🎧 样音试听（改声音前先听一句）", open=False):
            with gr.Row():
                preview_text = gr.Textbox(value="你怎么又淋成这样？外面雨这么大。",
                                          label="试听文本", scale=3)
                preview_voice_name = gr.Textbox(value="YunxiNeural", label="声音", scale=1)
            with gr.Row():
                preview_rate = gr.Textbox(value="+8%", label="语速", scale=1)
                preview_pitch = gr.Textbox(value="+3Hz", label="音调", scale=1)
                preview_btn = gr.Button("▶️  生成样音", variant="secondary", scale=1)
            preview_audio = gr.Audio(label="样音", type="filepath")
            preview_status = gr.Textbox(show_label=False, interactive=False, lines=1)

        # ══ 六 · 产物库 ══════════════════════════════════
        gr.HTML('<div class="section-label">六 &nbsp;·&nbsp; 产物库</div>')
        with gr.Row(equal_height=False):
            with gr.Column(scale=2):
                output_df = gr.Dataframe(headers=["文件名", "大小(MB)", "修改时间"],
                                         datatype=["str", "number", "str"],
                                         interactive=False, label="output/", max_height=320)
            with gr.Column(scale=1):
                output_picker = gr.Dropdown(choices=[], label="选择成品")
                play_btn = gr.Button("▶️  载入试听")
                play_audio = gr.Audio(label="试听", type="filepath")
                play_file = gr.File(label="下载 MP3")
                play_status = gr.Textbox(show_label=False, interactive=False, lines=1)

        # ── 单章精修（高级） ─────────────────────────
        with gr.Accordion("🔧 单章精修：按行号区间合成 + 同步字幕（高级）", open=False):
            gr.HTML('<div class="section-label">合成范围</div>')
            with gr.Row():
                start_line = gr.Number(label="起始行（从 0 计）", value=0, precision=0,
                                       minimum=0, scale=1)
                end_line = gr.Number(label="结束行", value=500, precision=0, minimum=1, scale=1)
                section_name = gr.Textbox(label="章节名称", scale=2,
                                          placeholder="例：第一章_初遇（用于文件命名）")
            single_btn = gr.Button("🎙  开始合成", variant="primary", size="lg")
            with gr.Row(elem_classes=["ctl-row"]):
                single_stop_btn = gr.Button("⏹  停止合成", interactive=False, scale=1)
            single_status = gr.Textbox(show_label=False, interactive=False, lines=6,
                                       placeholder="合成日志会显示在这里…")
            with gr.Row():
                single_audio = gr.Audio(label="试听", type="filepath", scale=3,
                                        elem_id="single-audio")
                single_file = gr.File(label="下载 MP3", scale=1)
            gr.HTML('<div class="section-label">同步文本（随音频播放自动高亮）</div>')
            sync_display = gr.HTML(value=_SYNC_PLACEHOLDER)

        gr.HTML(f"""
        <div class="app-footer">
            ✦ &nbsp; v{APP_VERSION} · edge-tts / macOS say 双引擎 &nbsp;·&nbsp;
            仅供个人学习使用 &nbsp; ✦
        </div>
        """)

        # ── 事件绑定 ──────────────────────────────────
        ui_state = gr.State({})
        poll_timer = gr.Timer(0.8, active=False)

        # 一 · 切章
        split_btn.click(cb_split_preview,
                        inputs=[file_input, pattern_box, max_chars],
                        outputs=[chapter_df, split_status, chapter_picker, tr_picker])
        split_write_btn.click(cb_split_write,
                              inputs=[file_input, pattern_box, max_chars],
                              outputs=[chapter_df, split_status, chapter_picker, tr_picker])

        # 二 · 人物 / 大模型 / 角色库
        analyze_btn.click(cb_analyze, inputs=[file_input, use_llm_chk],
                          outputs=[char_table, analyze_status])
        llm_preset.change(cb_llm_preset, inputs=[llm_preset],
                          outputs=[llm_base, llm_model])
        llm_test_btn.click(cb_llm_test,
                           inputs=[llm_preset, llm_base, llm_model, llm_key],
                           outputs=[llm_status])
        llm_save_btn.click(cb_llm_save,
                           inputs=[llm_preset, llm_base, llm_model, llm_key, use_llm_chk],
                           outputs=[llm_status, stat_bar])
        lib_save_btn.click(cb_library_save, inputs=[char_table],
                           outputs=[library_status, library_picker, library_df])
        lib_load_btn.click(cb_library_load, inputs=[library_picker, char_table],
                           outputs=[char_table, library_status])
        lib_del_btn.click(cb_library_delete, inputs=[library_picker],
                          outputs=[library_status, library_picker, library_df])
        save_cfg_btn.click(cb_save_config, inputs=[char_table], outputs=[cfg_status])
        load_cfg_btn.click(cb_load_config, inputs=None,
                           outputs=[char_table, cfg_status])
        up_btn.click(lambda df, n: cb_move_role(df, n, -1),
                     inputs=[char_table, role_picker], outputs=[char_table, cfg_status])
        down_btn.click(lambda df, n: cb_move_role(df, n, 1),
                       inputs=[char_table, role_picker], outputs=[char_table, cfg_status])
        restyle_btn.click(cb_restyle, inputs=[char_table],
                          outputs=[char_table, cfg_status])
        char_table.change(cb_sync_role_choices, inputs=[char_table],
                          outputs=[role_picker])
        char_table.change(_role_drag_html, inputs=[char_table], outputs=[role_drag])
        drag_order.change(cb_apply_order, inputs=[drag_order, char_table],
                          outputs=[char_table, cfg_status])
        drag_order.change(lambda: "", inputs=None, outputs=[drag_order])

        # 三 · 文本转译
        tr_btn.click(cb_start_translate,
                     inputs=[tr_picker, char_table, tr_mode],
                     outputs=[tr_status, ui_state, poll_timer])
        tr_stop.click(cb_stop, inputs=None, outputs=[tr_status])

        # 四 · 剧本校对
        sp_btn.click(cb_script_preview,
                     inputs=[file_input, char_table, sp_start, sp_end],
                     outputs=[preview_state, script_panel, script_status, sp_role])
        sp_role.change(cb_script_filter, inputs=[preview_state, sp_role, sp_skip],
                       outputs=[script_panel])
        sp_skip.change(cb_script_filter, inputs=[preview_state, sp_role, sp_skip],
                       outputs=[script_panel])

        # 五 · 合成
        apply_btn.click(cb_apply_settings,
                        inputs=[engine_radio, interval_slider, workers_slider],
                        outputs=[settings_status, stat_bar])
        refresh_btn.click(cb_refresh, inputs=None,
                          outputs=[chapter_picker, chapter_df, output_df,
                                   output_picker, stat_bar, tr_picker])
        batch_btn.click(cb_start_batch,
                        inputs=[chapter_picker, char_table, force_chk, use_tr_chk,
                                engine_radio, interval_slider, workers_slider],
                        outputs=[batch_status, ui_state, poll_timer])
        pause_btn.click(cb_pause, inputs=None, outputs=[batch_status])
        stop_btn.click(cb_stop, inputs=None, outputs=[batch_status])
        skip_btn.click(cb_skip, inputs=None, outputs=[batch_status])
        preview_btn.click(cb_preview_voice,
                          inputs=[preview_text, preview_voice_name, preview_rate,
                                  preview_pitch, engine_radio, interval_slider],
                          outputs=[preview_audio, preview_status])

        # 五 · 产物
        play_btn.click(cb_pick_output, inputs=[output_picker],
                       outputs=[play_audio, play_file, play_status])
        output_df.change(cb_sync_output_choices, inputs=[output_df],
                         outputs=[output_picker])

        # 单章精修
        single_btn.click(cb_start_single,
                         inputs=[file_input, char_table, start_line, end_line, section_name,
                                 engine_radio, interval_slider, workers_slider],
                         outputs=[single_status, ui_state, poll_timer])
        single_stop_btn.click(cb_stop, inputs=None, outputs=[single_status])

        # 轮询
        poll_timer.tick(cb_poll, inputs=[ui_state],
                        outputs=[batch_log, batch_progress, batch_status, batch_audio,
                                 output_df, chapter_df, stat_bar,
                                 batch_btn, pause_btn, stop_btn, skip_btn,
                                 single_status, single_audio, single_file, sync_display,
                                 single_btn, single_stop_btn, ui_state, poll_timer,
                                 tr_panel, tr_status, tr_btn, tr_stop])

        demo.load(cb_load_saved_on_startup, inputs=None,
                  outputs=[char_table, chapter_picker, chapter_df, output_df,
                           output_picker, library_picker, library_df, stat_bar,
                           tr_picker, role_drag, role_picker])
        demo.load(fn=None, js=_SYNC_JS)
        demo.load(fn=None, js=_TR_JS)
        demo.load(fn=None, js=_DRAG_JS)

    return demo


demo = build_ui()


if __name__ == "__main__":
    studio.ensure_dirs()
    engine = studio.engine_state()[0]
    print(f"\n  有声书工坊 v{APP_VERSION} · 引擎 {engine}")
    print("  打开 http://127.0.0.1:7860\n")
    try:
        demo.launch(theme=gr.themes.Soft(
            primary_hue=gr.themes.colors.orange,
            neutral_hue=gr.themes.colors.stone,
            font=[gr.themes.GoogleFont("Noto Serif SC"), "Georgia", "serif"],
        ), css=CUSTOM_CSS)
    except ValueError as e:
        if "localhost is not accessible" in str(e):
            # gradio-client schema bug 会让健康检查失败，但 uvicorn 其实已经起来了
            import time
            print("\n  App is running at: http://127.0.0.1:7860")
            print("  Press Ctrl+C to stop\n")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            raise
