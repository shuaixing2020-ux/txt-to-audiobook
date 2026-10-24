/* 有声书工坊 · 前端（原生 JS，无构建）
   桌面优先：左侧边栏 + 内容区；≤900px 自动切底部 TabBar
   状态全部走 /api/*，与 studio.py 一一对应 */

const S = {
  boot: null, voices: [], book: null, chapters: [], cast: [],
  script: null, job: null, tab: 'shelf', filter: 'all',
  analyzeMode: 'llm', trMode: 'llm', selected: new Set(),
  playing: null, timer: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    body: opts.body instanceof FormData ? opts.body
      : (opts.body ? JSON.stringify(opts.body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `请求失败（${res.status}）`);
  return data;
}

/* ── 导航 ───────────────────────────────────────────── */
const TABS = [
  ['shelf', '书架'], ['cast', '角色'], ['script', '校对'],
  ['synth', '合成'], ['works', '作品'],
];

function buildNav() {
  $('nav').innerHTML = TABS.map(([k, label]) =>
    `<button class="nav-item${k === S.tab ? ' active' : ''}" data-tab="${k}">
       ${label}<span class="dot"></span></button>`).join('');
  $('tabbar').innerHTML = TABS.map(([k, label]) =>
    `<button class="${k === S.tab ? 'on' : ''}" data-tab="${k}">${label}</button>`).join('');
  document.querySelectorAll('[data-tab]').forEach((b) =>
    b.onclick = () => setTab(b.dataset.tab));
}

function setTab(name) {
  if (name === 'settings') { openSettings(); return; }
  S.tab = name;
  document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
  $('page-' + name).classList.add('active');
  document.querySelectorAll('.nav-item').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tabbar button').forEach((b) =>
    b.classList.toggle('on', b.dataset.tab === name));
  $('main').scrollTop = 0;
  if (name === 'script') loadChapterOptions();
  if (name === 'works') { loadWorks(); loadExportPlan(); }
  if (name === 'synth') renderQueue();
  updateMini();
}

/* ── Sheet / ActionSheet / Toast ────────────────────── */
let sheetOnClose = null;

function openSheet(title, build, onClose) {
  $('sheetTitle').textContent = title;
  $('sheetBody').innerHTML = '';
  build($('sheetBody'));
  $('sheet').classList.add('on');
  $('backdrop').classList.add('on');
  sheetOnClose = onClose || null;
}
function closeSheet() {
  $('sheet').classList.remove('on');
  $('backdrop').classList.remove('on');
  if (sheetOnClose) { const f = sheetOnClose; sheetOnClose = null; f(); }
}
$('sheetClose').onclick = closeSheet;
$('backdrop').onclick = closeSheet;

function actionSheet(title, items, hint) {
  const body = document.createElement('div');
  body.className = 'actions';
  body.innerHTML = (hint ? `<div class="hint">${esc(hint)}</div>` : '') +
    items.map((it, i) =>
      `<button data-i="${i}" class="${it.destructive ? 'destructive' : ''}">${esc(it.label)}</button>`).join('') +
    `<button data-cancel class="cancel">取消</button>`;
  openSheet(title, (host) => host.appendChild(body));
  body.querySelectorAll('button[data-i]').forEach((b) => {
    b.onclick = () => { const it = items[+b.dataset.i]; closeSheet(); it.onClick && it.onClick(); };
  });
  body.querySelector('button[data-cancel]').onclick = closeSheet;
}

function toast(msg, undo) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (undo) {
    const b = document.createElement('button');
    b.textContent = '撤销';
    b.onclick = () => { t.remove(); undo(); };
    t.appendChild(b);
  }
  $('toastHost').appendChild(t);
  setTimeout(() => t.remove(), undo ? 6000 : 2600);
}

/* ── PWA / 快捷键 / 通知 ────────────────────────────── */
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

function bindShortcuts() {
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select';
    if (e.key === 'Escape') { closeSheet(); return; }
    if (typing) return;
    if (e.code === 'Space' && P.name) {          // 空格：播放 / 暂停
      e.preventDefault();
      P.audio.paused ? P.audio.play() : P.audio.pause();
      return;
    }
    if (e.key === 'ArrowLeft' && P.name) { P.audio.currentTime = Math.max(0, P.audio.currentTime - 15); }
    if (e.key === 'ArrowRight' && P.name) {
      P.audio.currentTime = Math.min(P.audio.duration || 0, P.audio.currentTime + 15);
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {   // ⌘/Ctrl+Enter：开始合成
      e.preventDefault();
      if (S.tab !== 'synth') setTab('synth');
      $('btnStart').click();
    }
  });
}

function notifyDone(text) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try { new Notification('有声书工坊', { body: text, icon: '/icon-192.png' }); } catch (e) {}
}

/* ── 启动 ───────────────────────────────────────────── */
async function boot() {
  buildNav();
  const data = await api('/api/bootstrap');
  S.boot = data;
  S.voices = (await api('/api/voices')).voices;
  S.cast = data.cast || [];
  S.chapters = data.chapters || [];
  S.selected = new Set(S.chapters.map((c) => c.file));
  applySettings(data.settings, data.llm);
  renderBooks();
  renderBook();
  renderCast();
  renderWorks(data.outputs);
  renderChapters(data.chapters);
  setTab('shelf');
  if (data.cast && data.cast.length) { /* 已有角色 */ }
  startPolling();
}

function applySettings(st, llm) {
  const label = st.engine === 'edge' ? '在线 · Edge TTS' : '离线 · macOS say';
  ['enginePill', 'enginePill2'].forEach((id) => {
    const el = $(id);
    el.textContent = label;
    el.className = 'engine-pill ' + (st.engine === 'edge' ? 'online' : 'offline');
  });
  $('synthSub').textContent = `并发 ${st.workers} 路 · 句间间隔 ${st.interval.toFixed(1)}s · 可随时暂停 / 停止`;
  S.settings = st;
  S.llm = llm;
}

/* ── 书架 ───────────────────────────────────────────── */
function renderBooks() {
  const info = S.boot?.book || { books: [], id: null, title: '' };
  const books = info.books || [];
  const cur = books.find((b) => b.id === info.id) || books[0];
  $('curBookTitle').textContent = cur ? cur.title : '还没有书';
  $('curBookMeta').textContent = cur
    ? `${cur.chapters} 章 · ${cur.outputs} 个产物${cur.author ? ' · ' + cur.author : ''}`
    : '点「新建」开始';
  $('bookSelect').innerHTML = books.map((b) =>
    `<option value="${esc(b.id)}" ${cur && b.id === cur.id ? 'selected' : ''}>${esc(b.title)}</option>`).join('');
}

$('bookSelect').onchange = async (e) => {
  try {
    await api('/api/books/select', { method: 'POST', body: { id: e.target.value } });
    await reloadData();
    toast('已切换书籍');
  } catch (err) { toast(err.message); }
};

$('btnNewBook').onclick = () => {
  openSheet('新建书籍', (host) => {
    const box = document.createElement('div');
    box.innerHTML = `
      <div class="field"><label>书名</label><input type="text" id="nbTitle" placeholder="例如：斩世刀"></div>
      <div class="field"><label>作者</label><input type="text" id="nbAuthor" placeholder="可留空"></div>
      <div class="hint">新书有独立的章节、角色表、转译稿和产物，互不干扰。</div>
      <div style="padding:12px 16px"><button class="btn primary block" id="nbOk">创建</button></div>`;
    host.appendChild(box);
    box.querySelector('#nbOk').onclick = async () => {
      const title = box.querySelector('#nbTitle').value.trim();
      if (!title) return toast('书名不能为空');
      try {
        await api('/api/books', { method: 'POST',
          body: { title, author: box.querySelector('#nbAuthor').value.trim() } });
        closeSheet(); await reloadData(); setTab('shelf'); toast(`已创建《${title}》`);
      } catch (e) { toast(e.message); }
    };
  });
};

$('btnBookMore').onclick = () => {
  const info = S.boot?.book || {};
  const cur = (info.books || []).find((b) => b.id === info.id);
  if (!cur) return toast('还没有书');
  actionSheet(cur.title, [
    { label: '重命名', onClick: () => {
      openSheet('重命名书籍', (host) => {
        const box = document.createElement('div');
        box.innerHTML = `
          <div class="field"><label>书名</label><input type="text" id="rbTitle" value="${esc(cur.title)}"></div>
          <div class="field"><label>作者</label><input type="text" id="rbAuthor" value="${esc(cur.author || '')}"></div>
          <div style="padding:12px 16px"><button class="btn primary block" id="rbOk">保存</button></div>`;
        host.appendChild(box);
        box.querySelector('#rbOk').onclick = async () => {
          await api('/api/books/rename', { method: 'POST', body: {
            id: cur.id, title: box.querySelector('#rbTitle').value.trim(),
            author: box.querySelector('#rbAuthor').value.trim() } });
          closeSheet(); await reloadData(); toast('已保存');
        };
      });
    } },
    { label: '删除（移到回收站）', destructive: true, onClick: async () => {
      try {
        await api('/api/books/' + encodeURIComponent(cur.id), { method: 'DELETE' });
        await reloadData(); toast('已移到 books/.trash/');
      } catch (e) { toast(e.message); }
    } },
  ], '删除只是移到 books/.trash/，不会真的丢');
};

async function reloadData() {
  const data = await api('/api/bootstrap');
  S.boot = data;
  S.cast = data.cast || [];
  S.chapters = data.chapters || [];
  S.selected = new Set(S.chapters.map((c) => c.file));
  applySettings(data.settings, data.llm);
  renderBooks();
  renderBook();
  renderCast();
  renderChapters(S.chapters);
  renderWorks(data.outputs);
  S.outputs = data.outputs;
  loadChapterOptions();
}

function renderBook() {
  const b = S.book;
  const n = (S.chapters || []).length;
  $('uploadCard').style.display = '';

  if (b) {
    $('bookBox').style.display = '';
    $('splitBox').style.display = '';
    $('bookCard').innerHTML = `
      <div class="row">
        <div class="grow">
          <div class="title">${esc(b.name)}</div>
          <div class="meta">${b.chars.toLocaleString()} 字 · ${(b.size / 1024).toFixed(1)} KB · 待切章</div>
        </div>
        <button class="btn small" id="btnReupload">换一本</button>
      </div>`;
    $('btnReupload').onclick = () => { S.book = null; renderBook(); };
  } else if (n) {
    $('bookBox').style.display = '';
    $('splitBox').style.display = 'none';
    $('bookCard').innerHTML = `
      <div class="row">
        <div class="grow">
          <div class="title">书架里已有 ${n} 章</div>
          <div class="meta">${(S.chapters.filter((c) => c.translated).length)} 章已转译 · 合计 ${S.chapters.reduce((a, c) => a + (c.chars || 0), 0).toLocaleString()} 字</div>
        </div>
      </div>`;
  } else {
    $('bookBox').style.display = 'none';
  }
}

async function upload(file) {
  const fd = new FormData();
  fd.append('file', file);
  $('shelfSub').textContent = '正在读取…';
  try {
    S.book = await api('/api/upload', { method: 'POST', body: fd });
    renderBook();
    $('shelfSub').textContent = `已导入 ${S.book.name}`;
    toast(`已导入「${S.book.name}」`);
    doSplit(false);
  } catch (e) {
    $('shelfSub').textContent = '导入失败';
    toast(e.message);
  }
}

$('btnPickFile').onclick = () => $('fileInput').click();
$('fileInput').onchange = (e) => e.target.files[0] && upload(e.target.files[0]);

const dz = $('dropZone');
['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => {
  e.preventDefault(); dz.style.background = 'rgba(0,122,255,.06)';
}));
['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => {
  e.preventDefault(); dz.style.background = '';
}));
dz.addEventListener('drop', (e) => {
  const f = e.dataTransfer.files[0];
  if (f) upload(f);
});

$('maxChars').oninput = (e) => { $('maxCharsVal').textContent = e.target.value; };

async function doSplit(write) {
  if (!S.book) return;
  try {
    const res = await api('/api/split', {
      method: 'POST',
      body: { path: S.book.path, pattern: $('pattern').value,
              max_chars: +$('maxChars').value, write },
    });
    if (write) {
      toast(`已写入 ${res.count} 章${res.removed.length ? `，${res.removed.length} 个旧章节已归档` : ''}`);
      const data = await api('/api/bootstrap');
      S.chapters = data.chapters;
      S.selected = new Set(S.chapters.map((c) => c.file));
      renderChapters(S.chapters);
      $('castSub').textContent = `共 ${S.chapters.length} 章`;
    } else {
      renderChapters(res.chapters.map((c, i) => ({
        file: c[3], title: c[1], chars: c[2], translated: false, idx: i + 1,
      })));
      toast(`识别到 ${res.chapters.length} 章`);
    }
    $('chapterBox').style.display = '';
  } catch (e) { toast(e.message); }
}
$('btnPreview').onclick = () => doSplit(false);
$('btnWrite').onclick = () => doSplit(true);

function renderChapters(list) {
  if (!list || !list.length) { $('chapterBox').style.display = 'none'; return; }
  $('chapterBox').style.display = '';
  const box = $('chapterList');
  box.innerHTML = list.map((c) => {
    const on = S.selected.has(c.file);
    return `<div class="row tap" data-file="${esc(c.file)}">
      <span class="chk ${on ? 'on' : ''}">✓</span>
      <div class="grow">
        <div class="title">${esc(c.title)}</div>
        <div class="meta">${(c.chars || 0).toLocaleString()} 字</div>
      </div>
      ${c.translated ? '<span class="badge">已转译</span>' : ''}
    </div>`;
  }).join('');
  box.querySelectorAll('[data-file]').forEach((row) => {
    row.onclick = () => {
      const f = row.dataset.file;
      S.selected.has(f) ? S.selected.delete(f) : S.selected.add(f);
      renderChapters(list);
      renderQueue();
    };
  });
  updateSelectSummary(list);
}

function updateSelectSummary(list) {
  const chosen = (list || S.chapters).filter((c) => S.selected.has(c.file));
  const chars = chosen.reduce((s, c) => s + (c.chars || 0), 0);
  const minutes = Math.round(chars / 620);
  $('selectSummary').textContent = chosen.length
    ? `已选 ${chosen.length} 章 · 约 ${chars.toLocaleString()} 字 · 预计 ${minutes} 分钟音频`
    : '还没选章节（合成前需要勾选）';
  $('btnNextCast').style.display = chosen.length ? '' : 'none';
}

$('btnSelectAll').onclick = () => {
  S.chapters.forEach((c) => S.selected.add(c.file));
  renderChapters(S.chapters); renderQueue();
};
$('btnSelectNone').onclick = () => {
  const all = S.chapters.every((c) => S.selected.has(c.file));
  S.chapters.forEach((c) => all ? S.selected.delete(c.file) : S.selected.add(c.file));
  renderChapters(S.chapters); renderQueue();
};
$('btnNextCast').onclick = () => setTab('cast');

/* ── 角色 ───────────────────────────────────────────── */
const AGE_OPTS = [['child', '幼'], ['teen', '少'], ['young', '青'], ['middle', '中'], ['old', '老']];
const EMO_OPTS = [['calm', '平静'], ['gentle', '温柔'], ['happy', '欢快'],
                  ['sad', '低沉'], ['angry', '愤怒'], ['serious', '严肃'], ['excited', '激动']];
const COLORS = ['#007AFF', '#5856D6', '#FF9500', '#34C759', '#FF2D55',
                '#AF52DE', '#5AC8FA', '#FFCC00', '#8E8E93'];

const ageLabel = (a) => (AGE_OPTS.find((x) => x[0] === a) || [, '青'])[1];
const emoLabel = (e) => (EMO_OPTS.find((x) => x[0] === e) || [, '平静'])[1];
const shortVoice = (v) => String(v || '').replace('zh-CN-', '').replace('Neural', '');

function renderCast() {
  const rows = S.cast || [];
  const card = $('castCard');
  if (!rows.length) {
    card.innerHTML = `<div class="empty"><div class="big">还没有角色</div>
      点「识别人物」自动生成，或从角色库载入</div>`;
    $('libCard').innerHTML = `<div class="hint">把常用的角色存进库，换书时一键载入。</div>`;
    return;
  }
  card.innerHTML = rows.map((r, i) => {
    const [name, gender, age, emo, role, voice, rate, pitch, freq, alias] = r;
    return `<div class="row tap" data-name="${esc(name)}" draggable="true">
      <span class="avatar" style="background:${COLORS[i % COLORS.length]}">${esc(String(name)[0] || '?')}</span>
      <div class="grow">
        <div class="title">${esc(name)} <span class="value" style="font-size:12px">${esc(role)}</span></div>
        <div class="meta">${esc(shortVoice(voice))} · ${esc(age)} · ${esc(emo)} · ${esc(rate)} ${esc(pitch)}${alias ? ' · 又称 ' + esc(alias) : ''}</div>
      </div>
      <span class="value">${freq || 0}</span>
      <span class="chev">›</span>
    </div>`;
  }).join('');

  card.querySelectorAll('[data-name]').forEach((row) => {
    row.onclick = () => openCastEditor(row.dataset.name);
    row.ondragstart = (e) => { e.dataTransfer.setData('text/plain', row.dataset.name); row.style.opacity = .4; };
    row.ondragend = () => { row.style.opacity = 1; };
    row.ondragover = (e) => e.preventDefault();
    row.ondrop = async (e) => {
      e.preventDefault();
      const from = e.dataTransfer.getData('text/plain');
      const to = row.dataset.name;
      if (!from || from === to) return;
      const names = S.cast.map((r) => r[0]);
      names.splice(names.indexOf(from), 1);
      names.splice(names.indexOf(to), 0, from);
      const res = await api('/api/cast/order', { method: 'POST', body: { names, rows: S.cast } });
      S.cast = res.cast; renderCast();
    };
  });

  const lib = S.boot?.library || [];
  $('libCard').innerHTML = lib.length ? lib.map((n) =>
    `<div class="row"><div class="grow"><div class="title">${esc(n)}</div></div>
      <button class="btn small" data-load="${esc(n)}">载入</button>
      <button class="btn small danger" data-del="${esc(n)}">删除</button></div>`).join('')
    : `<div class="hint">角色库是空的。点上方「存入角色库」把当前角色存下来。</div>`;

  $('libCard').querySelectorAll('[data-load]').forEach((b) => b.onclick = async () => {
    const res = await api('/api/cast/library/apply',
      { method: 'POST', body: { names: [b.dataset.load], rows: S.cast } });
    S.cast = res.cast; renderCast(); toast(`已载入「${b.dataset.load}」`);
  });
  $('libCard').querySelectorAll('[data-del]').forEach((b) => b.onclick = async () => {
    const res = await api('/api/cast/library/delete',
      { method: 'POST', body: { names: [b.dataset.del] } });
    S.boot.library = res.library; renderCast(); toast('已从角色库删除');
  });
}

function openCastEditor(name) {
  const idx = S.cast.findIndex((r) => r[0] === name);
  if (idx < 0) return;
  const row = S.cast[idx].slice();
  openSheet(name, (host) => {
    const body = document.createElement('div');
    body.innerHTML = `
      <div class="field"><label>年龄</label>
        <div class="segmented" data-age>${AGE_OPTS.map(([k, l]) =>
          `<button data-k="${k}" class="${row[2] === l ? 'on' : ''}">${l}</button>`).join('')}</div>
      </div>
      <div class="field"><label>情绪</label>
        <div class="segmented" data-emo style="flex-wrap:wrap">${EMO_OPTS.map(([k, l]) =>
          `<button data-k="${k}" class="${row[3] === l ? 'on' : ''}">${l}</button>`).join('')}</div>
      </div>
      <div class="field"><label>音色</label>
        <select data-voice>${S.voices.map((v) =>
          `<option value="${v.id}" ${v.id === row[5] ? 'selected' : ''}>${v.label}</option>`).join('')}</select>
      </div>
      <div class="field"><label>语速</label>
        <input type="range" data-rate min="-30" max="40" step="1" value="${parseInt(row[6]) || 0}">
        <span class="suffix" data-rate-v>${row[6]}</span></div>
      <div class="field"><label>音调</label>
        <input type="range" data-pitch min="-20" max="20" step="1" value="${parseInt(row[7]) || 0}">
        <span class="suffix" data-pitch-v>${row[7]}</span></div>
      <div class="field"><label>别名</label>
        <input type="text" data-alias value="${esc(row[9] || '')}" placeholder="用、分隔"></div>
      <div style="display:flex;gap:8px;padding:14px 16px 6px">
        <button class="btn" data-play>试听这句</button>
        <button class="btn" data-lib>存入角色库</button>
        <button class="btn danger" data-del>删除角色</button>
      </div>`;
    host.appendChild(body);

    body.querySelectorAll('[data-age] button').forEach((b) => b.onclick = () => {
      row[2] = b.textContent;
      body.querySelectorAll('[data-age] button').forEach((x) => x.classList.toggle('on', x === b));
    });
    body.querySelectorAll('[data-emo] button').forEach((b) => b.onclick = () => {
      row[3] = b.textContent;
      body.querySelectorAll('[data-emo] button').forEach((x) => x.classList.toggle('on', x === b));
    });
    body.querySelector('[data-voice]').onchange = (e) => { row[5] = e.target.value; };
    body.querySelector('[data-rate]').oninput = (e) => {
      row[6] = (e.target.value >= 0 ? '+' : '') + e.target.value + '%';
      body.querySelector('[data-rate-v]').textContent = row[6];
    };
    body.querySelector('[data-pitch]').oninput = (e) => {
      row[7] = (e.target.value >= 0 ? '+' : '') + e.target.value + 'Hz';
      body.querySelector('[data-pitch-v]').textContent = row[7];
    };
    body.querySelector('[data-alias]').oninput = (e) => { row[9] = e.target.value; };

    body.querySelector('[data-play]').onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = '生成中…';
      try {
        const r = await api('/api/preview', { method: 'POST',
          body: { text: '你怎么又淋成这样？外面雨这么大。',
                  voice: row[5], rate: row[6], pitch: row[7] } });
        new Audio(r.url).play();
        e.target.textContent = '再听一次';
      } catch (err) { toast(err.message); e.target.textContent = '试听这句'; }
      e.target.disabled = false;
    };
    body.querySelector('[data-lib]').onclick = async () => {
      const res = await api('/api/cast/library/save', { method: 'POST', body: { rows: [row] } });
      S.boot.library = res.library; renderCast(); toast(`已把「${row[0]}」存入角色库`);
    };
    body.querySelector('[data-del]').onclick = () => {
      S.cast.splice(idx, 1); renderCast(); closeSheet(); toast(`已删除「${name}」`);
    };
  }, async () => {
    S.cast[idx] = row;
    renderCast();
    try {                                  // 关掉面板就落盘，避免改完忘记保存
      await api('/api/cast', { method: 'PUT', body: { rows: S.cast } });
    } catch (e) { /* 静默：下次手动保存也行 */ }
  });
}

$('segMode').onclick = (e) => {
  if (e.target.tagName !== 'BUTTON') return;
  S.analyzeMode = e.target.dataset.mode;
  [...$('segMode').children].forEach((b) => b.classList.toggle('on', b === e.target));
};

$('btnAnalyze').onclick = async () => {
  const btn = $('btnAnalyze');
  btn.disabled = true; btn.textContent = '识别中…';
  try {
    const res = await api('/api/cast/analyze', {
      method: 'POST',
      body: { path: S.book ? S.book.path : '', mode: S.analyzeMode },
    });
    S.cast = res.cast;
    renderCast();
    $('castSub').textContent = res.note;
    toast(`识别到 ${res.cast.length - 1} 个角色`);
  } catch (e) { toast(e.message); }
  btn.disabled = false; btn.textContent = '识别人物';
};

$('btnRestyle').onclick = async () => {
  const res = await api('/api/cast/restyle', { method: 'POST', body: { rows: S.cast } });
  S.cast = res.cast; renderCast(); toast('已按年龄 / 情绪重算语速音调');
};

$('btnSaveCast').onclick = async () => {
  try {
    await api('/api/cast', { method: 'PUT', body: { rows: S.cast } });
    toast('已保存为默认配置');
  } catch (e) { toast(e.message); }
};

$('btnLibSave').onclick = async () => {
  const res = await api('/api/cast/library/save', { method: 'POST', body: { rows: S.cast } });
  S.boot.library = res.library; renderCast(); toast(`已存入 ${res.saved} 个角色`);
};

/* ── 校对 ───────────────────────────────────────────── */
function loadChapterOptions() {
  const sel = $('chapterSelect');
  const list = S.chapters || [];
  if (!list.length) { sel.innerHTML = '<option>书架里还没有章节</option>'; return; }
  const cur = sel.value;
  sel.innerHTML = list.map((c) =>
    `<option value="${esc(c.file)}">${esc(c.title)}${c.translated ? '（已转译）' : ''}</option>`).join('');
  if (cur && list.some((c) => c.file === cur)) sel.value = cur;
  else {
    const rich = list.find((c) => (c.chars || 0) > 200) || list[0];
    sel.value = rich.file;
  }
  loadScript();
}
$('chapterSelect').onchange = () => loadScript();

async function loadScript() {
  const chapter = $('chapterSelect').value;
  if (!chapter || !S.cast.length) {
    $('bubbleList').innerHTML = `<div class="empty"><div class="big">还没有剧本</div>
      先在「角色」页识别人物</div>`;
    return;
  }
  $('scriptStats').textContent = '加载中…';
  try {
    S.script = await api(`/api/script?chapter=${encodeURIComponent(chapter)}`);
    renderScript();
  } catch (e) {
    $('scriptStats').textContent = '—';
    $('bubbleList').innerHTML = `<div class="empty"><div class="big">读不出来</div>${esc(e.message)}</div>`;
  }
}

function renderScript() {
  const s = S.script;
  if (!s) return;
  const st = s.stats || {};
  $('scriptStats').textContent = `${st.total || 0} 句 · 朗读 ${st.read || 0} · 跳过 ${st.skipped || 0}`
    + (st.warn ? ` · 异常 ${st.warn}` : '');
  $('scriptSub').textContent = s.source === 'translated'
    ? '正在看转译稿（角色｜台词），合成会用它' : '还在看原文，建议先转译';

  const roles = (s.roles || []).map((r) => r.role);
  const rows = (s.rows || []).filter((r) => {
    if (S.filter === 'dialog') return r[1] === '对白';
    if (S.filter === 'skip') return String(r[5]).startsWith('跳过');
    if (S.filter === 'warn') return !!r[6];
    return true;
  });

  // 剧本里的角色如果不在配音表里，合成会静默回退成旁白音色 —— 这里主动提示
  const castNames = new Set(S.cast.map((r) => r[0]));
  const missing = (s.roles || []).map((r) => r.role)
    .filter((r) => r && r !== '旁白' && !castNames.has(r));
  $('missBox').innerHTML = missing.length ? `
    <div class="card" style="margin-bottom:12px">
      <div class="row">
        <div class="grow">
          <div class="title" style="color:#FF9500">${missing.length} 个角色没有配音</div>
          <div class="meta">${esc(missing.slice(0, 6).join('、'))}${missing.length > 6 ? '…' : ''} · 不补会用旁白的声音念</div>
        </div>
        <button class="btn small" id="btnMerge">补全</button>
      </div>
    </div>` : '';

  if (missing.length) {
    $('btnMerge').onclick = async () => {
      const r = await api('/api/cast/merge', { method: 'POST', body: { roles: missing } });
      S.cast = r.cast; renderCast(); renderScript();
      toast(`已补 ${r.added} 个角色，各分了独立音色`);
    };
  }

  if (!rows.length) {
    $('bubbleList').innerHTML = `<div class="empty"><div class="big">这一章还没转译</div>
      点右上「开始转译」，把原文改写成「角色｜台词」后再校对</div>`;
    return;
  }

  let right = false;
  let lastRole = null;
  $('bubbleList').innerHTML = rows.map((r) => {
    const [idx, kind, role, text, chars, status] = r;
    const skipped = String(status).startsWith('跳过');
    const isNarr = role === '旁白';
    if (isNarr) { lastRole = null; right = false; }
    else if (role !== lastRole) { right = !right; lastRole = role; }
    const color = COLORS[Math.max(0, roles.indexOf(role)) % COLORS.length];
    const side = isNarr ? 'narrator' : (right ? 'right' : '');
    const style = isNarr ? '' : `style="background:${color}"`;
    return `<div class="bubble-wrap ${side}">
      <div class="bubble ${skipped ? 'skip' : ''}" ${style} data-idx="${idx}">
        ${isNarr ? '' : `<span class="who">${esc(role)}</span>`}
        ${esc(text)}
        ${skipped ? `<span class="reason">${esc(String(status).replace('跳过（', '').replace('）', ''))}</span>` : ''}
        ${r[6] ? `<span class="reason">${esc(r[6])}</span>` : ''}
      </div></div>`;
  }).join('');

  $('bubbleList').querySelectorAll('[data-idx]').forEach((b) => {
    b.onclick = () => openBubbleActions(+b.dataset.idx);
  });
}

$('segFilter').onclick = (e) => {
  if (e.target.tagName !== 'BUTTON') return;
  S.filter = e.target.dataset.f;
  [...$('segFilter').children].forEach((b) => b.classList.toggle('on', b === e.target));
  renderScript();
};

function openBubbleActions(idx) {
  const chapter = $('chapterSelect').value;
  const row = (S.script.rows || []).find((r) => r[0] === idx);
  if (!row) return;
  const [, , role, text, , status] = row;
  const skipped = String(status).startsWith('跳过');

  const items = [{ label: '试听这一句', onClick: async () => {
    try {
      const r = await api('/api/preview', { method: 'POST',
        body: { text, voice: castVoice(role), rate: castRate(role), pitch: castPitch(role) } });
      new Audio(r.url).play();
    } catch (e) { toast(e.message); }
  } }];
  if (!skipped) {
    items.push({ label: '重做这一句', onClick: () => redoLine(idx) });
    items.push({ label: '改说话人', onClick: () => pickRole(idx) });
    items.push({ label: '设为不朗读', destructive: true, onClick: () => skipLine(idx) });
  }
  actionSheet(`第 ${idx} 句`, items,
    `${role}｜${text.slice(0, 40)}${text.length > 40 ? '…' : ''}`
    + (skipped ? '（这一句不会朗读）' : ''));
}

async function redoLine(idx) {
  const chapter = $('chapterSelect').value;
  toast('正在合成这一句…');
  try {
    const r = await api('/api/line/audio', { method: 'POST', body: { chapter, index: idx } });
    new Audio(r.url).play();
    toast(`${r.role} · ${shortVoice(r.voice)} · ${(r.ms / 1000).toFixed(1)}s`);
  } catch (e) { toast(e.message); }
}

function castVoice(role) {
  const r = S.cast.find((x) => x[0] === role);
  return r ? r[5] : 'zh-CN-XiaoxiaoNeural';
}
function castRate(role) {
  const r = S.cast.find((x) => x[0] === role);
  return r ? r[6] : '+0%';
}
function castPitch(role) {
  const r = S.cast.find((x) => x[0] === role);
  return r ? r[7] : '+0Hz';
}

function pickRole(idx) {
  const names = S.cast.map((r) => r[0]);
  openSheet('改成谁说的', (host) => {
    const box = document.createElement('div');
    box.className = 'actions';
    box.innerHTML = names.map((n) => `<button data-n="${esc(n)}">${esc(n)}</button>`).join('')
      + '<button data-cancel class="cancel">取消</button>';
    host.appendChild(box);
    box.querySelectorAll('[data-n]').forEach((b) => b.onclick = async () => {
      try {
        await api('/api/script/edit', { method: 'POST',
          body: { chapter: $('chapterSelect').value, index: idx, action: 'role', value: b.dataset.n } });
        closeSheet(); await loadScript(); toast(`第 ${idx} 句改为「${b.dataset.n}」`);
      } catch (e) { toast(e.message); }
    });
    box.querySelector('[data-cancel]').onclick = closeSheet;
  });
}

async function skipLine(idx) {
  const chapter = $('chapterSelect').value;
  try {
    const res = await api('/api/script/edit', { method: 'POST',
      body: { chapter, index: idx, action: 'skip' } });
    await loadScript();
    toast('已设为不朗读', async () => {
      await api('/api/script/edit', { method: 'POST',
        body: { chapter, index: idx, action: 'restore', value: res.removed } });
      await loadScript();
      toast('已撤销');
    });
  } catch (e) { toast(e.message); }
}

$('btnAudit').onclick = async () => {
  const chapter = $('chapterSelect').value;
  if (!chapter) return toast('先选一章');
  try {
    const r = await api('/api/audit?chapter=' + encodeURIComponent(chapter));
    if (!r.findings.length) {
      return toast(`扫了 ${r.total} 行，没发现水印 / 广告 / 乱码 / 重复`);
    }
    const stat = Object.entries(r.stats).filter(([, n]) => n)
      .map(([k, n]) => `${k} ${n}`).join(' · ');
    openSheet(`审查：${r.total} 行 · ${stat}`, (host) => {
      const box = document.createElement('div');
      box.innerHTML = r.findings.map((f) => `
        <div class="row">
          <div class="grow">
            <div class="title"><span class="badge gray">${esc(f.kind)}</span>
              ${esc(f.text)}</div>
            <div class="meta">第 ${f.index} 行 · ${esc(f.role)} · ${esc(f.reason)}</div>
          </div>
          <button class="btn small" data-skip="${f.index}">不朗读</button>
        </div>`).join('') + `
        <div style="padding:12px 16px">
          <button class="btn danger block" data-skip-all>全部设为不朗读（${r.findings.length} 行）</button>
        </div>`;
      host.appendChild(box);

      const doSkip = async (indexes) => {
        const res = await api('/api/script/skip', { method: 'POST',
          body: { chapter, indexes } });
        closeSheet();
        await loadScript();
        toast(`已跳过 ${res.removed} 行`, async () => {
          // 撤销：按原顺序插回（简化：整章重新载入会丢，这里只提示）
          toast('撤销请用编辑器的版本历史（暂未实现）');
        });
      };
      box.querySelectorAll('[data-skip]').forEach((b) =>
        b.onclick = () => doSkip([+b.dataset.skip]));
      box.querySelector('[data-skip-all]').onclick = () =>
        doSkip(r.findings.map((f) => f.index));
    });
  } catch (e) { toast(e.message); }
};

$('segTr').onclick = (e) => {
  if (e.target.tagName !== 'BUTTON') return;
  S.trMode = e.target.dataset.mode;
  [...$('segTr').children].forEach((b) => b.classList.toggle('on', b === e.target));
};

$('btnTranslate').onclick = async () => {
  const chapter = $('chapterSelect').value;
  if (!chapter) return toast('先在书架里写入章节');
  const btn = $('btnTranslate');
  btn.disabled = true; btn.textContent = '转译中…';
  try {
    await api('/api/translate', { method: 'POST', body: { chapters: [chapter], mode: S.trMode } });
    startPolling();
    toast('已开始转译，进度在底部');
  } catch (e) { toast(e.message); }
  btn.disabled = false; btn.textContent = '开始转译';
};

/* ── 合成 ───────────────────────────────────────────── */
async function loadEstimate() {
  const list = S.chapters.filter((c) => S.selected.has(c.file));
  if (!list.length || !S.cast.length) { $('estimateBox').innerHTML = ''; return; }
  try {
    const r = await api('/api/estimate', { method: 'POST',
      body: { chapters: list.map((c) => c.file), use_translated: true } });
    const min = Math.max(1, Math.round(r.est_seconds / 60));
    $('estimateBox').innerHTML = `
      <div class="section-title">本次会做什么</div>
      <div class="card">
        <div class="row">
          <div class="grow">
            <div class="title">${r.misses} 句要重合成 · ${r.cached} 句命中缓存</div>
            <div class="meta">共 ${r.sentences} 句 · 预计 ${min} 分钟（${r.engine === 'say' ? '离线' : '在线'} ${r.workers} 并发）· 只有内容或音色变了的句子才会重做</div>
          </div>
        </div>
      </div>`;
  } catch (e) { $('estimateBox').innerHTML = ''; }
}

function renderQueue() {
  const list = S.chapters.filter((c) => S.selected.has(c.file));
  const box = $('queueList');
  if (!list.length) {
    box.innerHTML = `<div class="hint">在书架里勾选章节后，回到这里开始合成。</div>`;
    return;
  }
  const job = S.job;
  const chars = list.reduce((a, c) => a + (c.chars || 0), 0);
  const audioMin = Math.round(chars / 325);                     // 实测：约 325 字/分钟
  const speed = (S.settings?.engine === 'say') ? 6 : 2.5;        // 离线 4 并发约 6× 实时
  const etaMin = Math.max(1, Math.round(audioMin / speed));
  box.innerHTML = `<div class="row" style="background:rgba(0,122,255,.04)">
      <div class="grow">
        <div class="title">已选 ${list.length} 章</div>
        <div class="meta">约 ${chars.toLocaleString()} 字 · 音频约 ${audioMin} 分钟 · 预计合成 ${etaMin} 分钟（${S.settings?.engine === 'say' ? '离线' : '在线'}）</div>
      </div>
    </div>` + list.map((c, i) => {
    let status = '等待';
    if (job) {
      if ((job.failures || []).includes(c.title)) status = '失败';
      else if (i + 1 < job.index) status = '已完成';
      else if (i + 1 === job.index) status = job.status === 'running' ? '进行中' : job.status;
    }
    return `<div class="row"><div class="grow"><div class="title">${esc(c.title)}</div>
      <div class="meta">${(c.chars || 0).toLocaleString()} 字${c.translated ? ' · 用转译稿' : ''}</div></div>
      <span class="value">${esc(status)}</span>
      ${!job && c.stale ? '<span class="badge gray">内容已变更</span>' : ''}</div>`;
  }).join('');
  loadEstimate();
}

$('btnStart').onclick = async () => {
  if (!S.selected.size) return toast('先在书架里勾选章节');
  if (!S.cast.length) { setTab('cast'); return toast('先识别人物'); }
  try {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
    await api('/api/job/batch', { method: 'POST',
      body: { chapters: [...S.selected], force: false, use_translated: true } });
    startPolling();
    toast('已开始合成');
  } catch (e) { toast(e.message); }
};

$('btnPause').onclick = async () => {
  try {
    const r = await api('/api/job/pause', { method: 'POST' });
    toast(r.msg);
  } catch (e) { toast(e.message); }
};

$('btnSkip').onclick = async () => {
  try { const r = await api('/api/job/skip', { method: 'POST' }); toast(r.msg); }
  catch (e) { toast(e.message); }
};

$('btnStop').onclick = () => {
  actionSheet('停止合成？', [
    { label: '停止', destructive: true, onClick: async () => {
      try { const r = await api('/api/job/stop', { method: 'POST' }); toast(r.msg); }
      catch (e) { toast(e.message); }
    } },
  ], '不会产出半成品；已合成的句子保留在缓存里，再点开始会续跑。');
};

function startPolling() {
  if (S.timer) return;
  schedulePoll(0);
}

function schedulePoll(delay) {
  clearTimeout(S.timer);
  S.timer = setTimeout(async () => {
    await poll();
    schedulePoll(S.job ? 700 : 2500);
  }, delay);
}

async function poll() {
  try {
    const data = await api('/api/job');
    S.job = data.job;
    renderJob();
  } catch (e) { /* 轮询失败静默 */ }
}

function renderJob() {
  const job = S.job;
  const BASE_TITLE = '有声书工坊';
  const running = job && ['running', 'paused', 'stopping'].includes(job.status);
  if (!job) {
    document.title = BASE_TITLE;
    $('pct').textContent = '0%';
    $('bar').style.width = '0%';
    $('curChapter').textContent = '等待开始';
    $('btnPause').disabled = $('btnSkip').disabled = $('btnStop').disabled = true;
    updateMini();
    return;
  }
  const pct = Math.round((job.progress || 0) * 100);
  const label = job.kind === 'translate' ? '转译' : job.kind === 'export' ? '导出' : '合成';
  document.title = ['running', 'paused', 'stopping'].includes(job.status)
    ? `[${pct}%] ${label}中 · ${BASE_TITLE}`
    : (job.status === 'done' ? `${label}完成 · ${BASE_TITLE}` : BASE_TITLE);
  $('pct').textContent = pct + '%';
  $('bar').style.width = pct + '%';
  $('curChapter').textContent = job.kind === 'translate'
    ? `转译中 · ${job.current || job.detail || ''}`
    : job.kind === 'export'
      ? (job.detail || (job.status === 'done' ? '导出完成' : '正在导出…'))
      : (job.detail || (job.status === 'done' ? '已完成' : `第 ${job.index}/${job.total} 章`));
  $('cOk').textContent = job.ok; $('cSkip').textContent = job.skip; $('cFail').textContent = job.fail;
  const sec = Math.floor(job.elapsed || 0);
  $('cTime').textContent = `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`;

  $('btnPause').disabled = !running;
  $('btnSkip').disabled = !running || job.kind !== 'batch';
  $('btnStop').disabled = !running;
  $('btnPause').textContent = job.status === 'paused' ? '继续' : '暂停';

  if (!running && job.status === 'done') {
    if (!S._doneShown) {
      S._doneShown = true;
      loadWorks(); loadChapterOptions();
      if (job.kind === 'export' && job.out_path) {
        toast(`已导出 ${job.out_path.split('/').pop()}`);
        notifyDone(`导出完成：${job.out_path.split('/').pop()}`);
        setTab('works');
      } else if (job.kind === 'batch') {
        notifyDone(`合成完成：成功 ${job.ok} / 跳过 ${job.skip} / 失败 ${job.fail}`);
      } else if (job.kind === 'translate') {
        notifyDone(`转译完成：${job.ok} 章`);
      }
    }
  }
  if (running) S._doneShown = false;
  renderQueue();
  renderFailures();
  updateMini();
}

function renderFailures() {
  const job = S.job;
  const fails = (job && job.failures) || [];
  const box = $('failBox');
  if (!fails.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="section-title">失败 ${fails.length} 章</div>
    <div class="card">${fails.map((n) => `<div class="row">
      <div class="grow"><div class="title">${esc(n)}</div>
      <div class="meta">网络超时或文本异常，可直接重试</div></div>
      </div>`).join('')}
      <div class="row"><button class="btn block" id="btnRetry">重试这 ${fails.length} 章</button></div>
    </div>`;
  $('btnRetry').onclick = async () => {
    const files = fails
      .map((n) => (S.chapters.find((c) => c.title === n) || {}).file)
      .filter(Boolean);
    if (!files.length) return toast('找不到对应章节，可能在书架里被替换了');
    try {
      await api('/api/job/batch', { method: 'POST',
        body: { chapters: files, force: true, use_translated: true } });
      toast('已重新排队');
    } catch (e) { toast(e.message); }
  };
}

function updateMini() {
  const job = S.job;
  const running = job && ['running', 'paused', 'stopping'].includes(job.status);
  const show = running && S.tab !== 'synth';
  $('miniBar').classList.toggle('on', !!show);
  if (!show) return;
  const pct = Math.round((job.progress || 0) * 100);
  $('miniFill').style.width = pct + '%';
  $('miniPct').textContent = pct + '%';
  $('miniText').textContent = job.kind === 'translate' ? '转译中'
    : job.kind === 'export' ? '导出中' : '合成中';
}
$('miniBar').onclick = () => setTab('synth');

/* ── 作品 ───────────────────────────────────────────── */
async function loadWorks(list) {
  try {
    const data = list ? { outputs: list } : await api('/api/outputs');
    S.outputs = data.outputs;
    renderWorks(data.outputs);
  } catch (e) { toast(e.message); }
}

function renderWorks(list) {
  const card = $('worksCard');
  if (!list || !list.length) {
    card.innerHTML = `<div class="empty"><div class="big">还没有成品</div>合成完成后会出现在这里</div>`;
    return;
  }
  card.innerHTML = list.map(([name, mb, mtime]) => `
    <div class="row" data-row="${esc(name)}">
      <div class="grow">
        <div class="title">${esc(name.replace(/\.(mp3|m4b)$/i, ''))}
          ${name.toLowerCase().endsWith('.m4b') ? '<span class="badge">整本</span>' : ''}</div>
        <div class="meta">${mb} MB · ${esc(mtime)}</div>
      </div>
      <button class="btn small" data-play="${esc(name)}">播放</button>
      <button class="btn small" data-more="${esc(name)}">更多</button>
    </div>`).join('');

  card.querySelectorAll('[data-play]').forEach((b) => {
    b.onclick = () => togglePlay(b.dataset.play);
  });
  card.querySelectorAll('[data-more]').forEach((b) => {
    b.onclick = () => openOutputActions(b.dataset.more);
  });
  markPlayingRow();
}

/* ── 播放器 ─────────────────────────────────────────── */
const P = { audio: new Audio(), list: [], idx: -1, name: null };

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function playerInit() {
  const a = P.audio;
  a.addEventListener('timeupdate', () => {
    $('pCur').textContent = fmtTime(a.currentTime);
    if (!S._seeking) {
      $('pSeek').value = a.duration ? Math.round((a.currentTime / a.duration) * 1000) : 0;
    }
  });
  a.addEventListener('loadedmetadata', () => {
    $('pDur').textContent = fmtTime(a.duration);
  });
  a.addEventListener('play', () => { $('pToggle').textContent = '暂停'; });
  a.addEventListener('pause', () => { $('pToggle').textContent = '播放'; });
  a.addEventListener('ended', () => { playerStep(1, true); });

  $('pToggle').onclick = () => {
    if (!P.name) return toast('先从列表里选一个');
    P.audio.paused ? P.audio.play() : P.audio.pause();
  };
  $('pBack').onclick = () => { P.audio.currentTime = Math.max(0, P.audio.currentTime - 15); };
  $('pFwd').onclick = () => { P.audio.currentTime = Math.min(P.audio.duration || 0, P.audio.currentTime + 15); };
  $('pPrev').onclick = () => playerStep(-1);
  $('pNext').onclick = () => playerStep(1);
  $('pRate').onchange = (e) => { P.audio.playbackRate = +e.target.value; };
  $('pSeek').oninput = (e) => {
    S._seeking = true;
    if (P.audio.duration) P.audio.currentTime = (e.target.value / 1000) * P.audio.duration;
  };
  $('pSeek').onchange = () => { S._seeking = false; };
}

function togglePlay(name) {
  if (P.name === name) {                       // 同一首：暂停/继续
    P.audio.paused ? P.audio.play() : P.audio.pause();
    return;
  }
  playOutput(name);
}

function playOutput(name) {
  P.list = (S.outputs || []).map((o) => o[0]);
  P.idx = P.list.indexOf(name);
  P.name = name;
  P.audio.src = '/api/audio/output/' + encodeURIComponent(name);
  P.audio.playbackRate = +$('pRate').value;
  P.audio.play().catch(() => {});
  $('pTitle').textContent = name.replace(/\.(mp3|m4b)$/i, '');
  $('player').classList.add('on');
  document.body.classList.add('has-player');
  markPlayingRow();
}

function playerStep(delta, fromEnd) {
  if (!P.list.length) return;
  const next = P.idx + delta;
  if (next < 0 || next >= P.list.length) {
    if (fromEnd) { P.audio.pause(); markPlayingRow(); }
    return toast(delta > 0 ? '已经是最后一个' : '已经是第一个');
  }
  playOutput(P.list[next]);
}

function markPlayingRow() {
  document.querySelectorAll('[data-row]').forEach((row) => {
    const on = row.dataset.row === P.name;
    row.style.background = on ? 'rgba(0,122,255,.06)' : '';
    const b = row.querySelector('[data-play]');
    if (b) b.textContent = on && !P.audio.paused ? '暂停' : '播放';
  });
}

function openOutputActions(name) {
  const isM4b = name.toLowerCase().endsWith('.m4b');
  actionSheet(name, [
    { label: P.name === name && !P.audio.paused ? '暂停播放' : '播放', onClick: () => togglePlay(name) },
    { label: '下载', onClick: () => {
      const a = document.createElement('a');
      a.href = '/api/audio/output/' + encodeURIComponent(name);
      a.download = name; a.click();
    } },
    ...(isM4b ? [] : [{ label: '重命名', onClick: () => renameOutput(name) }]),
    { label: '删除', destructive: true, onClick: () => deleteOutput(name) },
  ], isM4b ? '整本导出文件' : `${name}`);
}

function renameOutput(name) {
  openSheet('重命名', (host) => {
    const box = document.createElement('div');
    const base = name.replace(/\.(mp3|m4b)$/i, '');
    box.innerHTML = `
      <div class="field"><label>新名字</label>
        <input type="text" id="rnInput" value="${esc(base)}"></div>
      <div style="padding:12px 16px"><button class="btn primary block" id="rnOk">保存</button></div>`;
    host.appendChild(box);
    box.querySelector('#rnOk').onclick = async () => {
      try {
        const r = await api('/api/outputs/rename', { method: 'POST',
          body: { name, new_name: box.querySelector('#rnInput').value.trim() } });
        if (P.name === name) { P.name = r.name; $('pTitle').textContent = r.name.replace(/\.[^.]+$/, ''); }
        closeSheet(); await loadWorks(); toast('已重命名');
      } catch (e) { toast(e.message); }
    };
  });
}

function deleteOutput(name) {
  actionSheet('删除这个文件？', [
    { label: '删除', destructive: true, onClick: async () => {
      try {
        await api('/api/outputs/' + encodeURIComponent(name), { method: 'DELETE' });
        if (P.name === name) { P.audio.pause(); P.audio.src = ''; P.name = null;
          $('player').classList.remove('on'); document.body.classList.remove('has-player'); }
        await loadWorks(); toast('已删除');
      } catch (e) { toast(e.message); }
    } },
  ], `${name}\n删除后不可恢复（源章节还在，可以重新合成）`);
}
$('btnRefreshWorks').onclick = () => loadWorks();

async function loadExportPlan() {
  const list = S.chapters.filter((c) => S.selected.has(c.file));
  const files = (list.length ? list : S.chapters).map((c) => c.file);
  try {
    const r = await api('/api/export/plan?chapters=' + encodeURIComponent(files.join('|')));
    $('exportMeta').textContent = r.count
      ? `可导出 ${r.count} 章 · 约 ${r.minutes} 分钟（只统计已出片的）`
      : '还没有成品，先去合成';
  } catch (e) { /* 忽略 */ }
}

$('btnExport').onclick = async () => {
  const chapters = S.chapters.filter((c) => S.selected.has(c.file));
  const list = chapters.length ? chapters : S.chapters;
  try {
    await api('/api/export', { method: 'POST', body: {
      chapters: list.map((c) => c.file),
      title: $('bookTitle').value.trim() || '有声书',
      author: $('bookAuthor').value.trim(),
    } });
    startPolling();
    toast('开始导出，进度见下方');
  } catch (e) { toast(e.message); }
};

/* ── 设置 ───────────────────────────────────────────── */
function openSettings() {
  const st = S.settings || {};
  const llm = S.llm || {};
  openSheet('设置', (host) => {
    const body = document.createElement('div');
    body.innerHTML = `
      <div class="section-title" style="margin-top:4px">合成</div>
      <div class="card">
        <div class="field"><label>引擎</label>
          <div class="segmented" data-engine>
            <button data-k="edge" class="${st.engine === 'edge' ? 'on' : ''}">在线 Edge</button>
            <button data-k="say" class="${st.engine === 'say' ? 'on' : ''}">离线 say</button>
          </div>
        </div>
        <div class="field"><label>并发</label>
          <input type="range" data-workers min="1" max="12" step="1" value="${st.workers || 4}">
          <span class="suffix" data-workers-v>${st.workers || 4}</span></div>
        <div class="field"><label>句间间隔</label>
          <input type="range" data-interval min="0" max="5" step="0.1" value="${st.interval ?? 1.2}">
          <span class="suffix" data-interval-v>${(st.interval ?? 1.2).toFixed(1)}s</span></div>
      </div>

      <div class="section-title">大模型（识别人物 / 转译）</div>
      <div class="card">
        <div class="field"><label>服务商</label>
          <select data-preset>${Object.entries(llm.presets || {}).map(([k, v]) =>
            `<option value="${k}" ${llm.config?.preset === k ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select>
        </div>
        <div class="field"><label>接口地址</label>
          <input type="text" data-base value="${esc(llm.config?.base_url || '')}"></div>
        <div class="field"><label>模型</label>
          <input type="text" data-model value="${esc(llm.config?.model || '')}"></div>
        <div class="field"><label>API Key</label>
          <input type="password" data-key placeholder="${llm.has_key ? '已保存（留空不改）' : '未设置'}"></div>
        <div class="hint" data-llm-note>${esc(llm.note || '')}</div>
        <div style="display:flex;gap:8px;padding:8px 16px 14px">
          <button class="btn" data-test>测试连接</button>
          <button class="btn primary" data-save-llm>保存</button>
        </div>
      </div>

      <div class="section-title">发音词典</div>
      <div class="card">
        <div class="hint">多音字 / 专名用同音字替换，全书合成时生效（字幕仍显示原文）。例：重庆 → 崇庆。</div>
        <div id="pronList"></div>
        <div class="field"><label>原文</label><input type="text" data-pron-from placeholder="重庆"></div>
        <div class="field"><label>替换为</label><input type="text" data-pron-to placeholder="崇庆"></div>
        <div style="padding:10px 16px 14px"><button class="btn block" data-pron-add>添加</button></div>
      </div>

      <div class="section-title">诊断</div>
      <div class="card">
        <div class="row"><div class="grow"><div class="title">环境自检</div>
          <div class="meta">ffmpeg / 依赖 / 引擎 / DNS</div></div>
          <button class="btn small" data-diag>运行</button></div>
        <div id="diagList"></div>
      </div>

      <div style="padding:14px 16px">
        <button class="btn primary block" data-save-all>保存设置</button>
      </div>`;
    host.appendChild(body);

    const val = (sel) => body.querySelector(sel);
    val('[data-workers]').oninput = (e) => val('[data-workers-v]').textContent = e.target.value;
    val('[data-interval]').oninput = (e) =>
      val('[data-interval-v]').textContent = (+e.target.value).toFixed(1) + 's';
    body.querySelectorAll('[data-engine] button').forEach((b) => b.onclick = () => {
      body.querySelectorAll('[data-engine] button').forEach((x) => x.classList.toggle('on', x === b));
    });
    val('[data-preset]').onchange = (e) => {
      const p = (llm.presets || {})[e.target.value];
      if (p) { val('[data-base]').value = p.base_url; val('[data-model]').value = p.model; }
    };
    val('[data-test]').onclick = async () => {
      const btn = val('[data-test]');
      btn.disabled = true; btn.textContent = '测试中…';
      const r = await api('/api/llm/test', { method: 'POST', body: {
        preset: val('[data-preset]').value, base_url: val('[data-base]').value,
        model: val('[data-model]').value, api_key: val('[data-key]').value } });
      val('[data-llm-note]').textContent = r.ok ? `连接成功：${r.reply}` : `失败：${r.error}`;
      btn.disabled = false; btn.textContent = '测试连接';
    };
    val('[data-save-llm]').onclick = async () => {
      const r = await api('/api/llm', { method: 'POST', body: {
        preset: val('[data-preset]').value, base_url: val('[data-base]').value,
        model: val('[data-model]').value, api_key: val('[data-key]').value, enabled: true } });
      val('[data-llm-note]').textContent = r.ready ? `已保存，${r.note}` : `已保存，但 ${r.note}`;
      toast('大模型设置已保存');
    };
    // 发音词典
    let pron = {};
    const renderPron = () => {
      const rows = Object.entries(pron);
      body.querySelector('#pronList').innerHTML = rows.length ? rows.map(([k, v]) => `
        <div class="row"><div class="grow"><div class="title">${esc(k)} → ${esc(v)}</div></div>
        <button class="btn small danger" data-pron-del="${esc(k)}">删除</button></div>`).join('')
        : `<div class="hint">还没有条目</div>`;
      body.querySelectorAll('[data-pron-del]').forEach((b) => b.onclick = async () => {
        delete pron[b.dataset.pronDel];
        await api('/api/pronounce', { method: 'POST', body: { items: pron } });
        renderPron(); toast('已删除');
      });
    };
    api('/api/pronounce').then((r) => {
      pron = Object.fromEntries(r.items.map(([k, v]) => [k, v]));
      renderPron();
    });
    val('[data-pron-add]').onclick = async () => {
      const from = val('[data-pron-from]').value.trim();
      const to = val('[data-pron-to]').value.trim();
      if (!from || !to) return toast('原文和替换都要填');
      pron[from] = to;
      await api('/api/pronounce', { method: 'POST', body: { items: pron } });
      val('[data-pron-from]').value = ''; val('[data-pron-to]').value = '';
      renderPron(); toast(`已添加「${from} → ${to}」`);
    };

    val('[data-diag]').onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = '检测中…';
      const r = await api('/api/diagnose');
      body.querySelector('#diagList').innerHTML = r.items.map((it) => {
        const color = it.status === 'ok' ? '#34C759' : it.status === 'warn' ? '#FF9500' : '#FF3B30';
        const mark = it.status === 'ok' ? '正常' : it.status === 'warn' ? '注意' : '失败';
        return `<div class="row"><div class="grow"><div class="title" style="color:${color}">${esc(it.name)} · ${mark}</div>
          <div class="meta">${esc(it.detail)}</div></div></div>`;
      }).join('');
      e.target.disabled = false; e.target.textContent = '运行';
    };

    val('[data-save-all]').onclick = async () => {
      const engine = body.querySelector('[data-engine] .on').dataset.k;
      const r = await api('/api/settings', { method: 'POST', body: {
        engine, workers: +val('[data-workers]').value, interval: +val('[data-interval]').value } });
      applySettings({ engine: r.engine, interval: r.interval, workers: r.workers }, S.llm);
      toast('设置已生效'); closeSheet();
    };
  });
}
$('btnSettings').onclick = openSettings;

/* ── PWA / 快捷键 / 通知 ────────────────────────────── */
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

function bindShortcuts() {
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select';
    if (e.key === 'Escape') { closeSheet(); return; }
    if (typing) return;
    if (e.code === 'Space' && P.name) {          // 空格：播放 / 暂停
      e.preventDefault();
      P.audio.paused ? P.audio.play() : P.audio.pause();
      return;
    }
    if (e.key === 'ArrowLeft' && P.name) { P.audio.currentTime = Math.max(0, P.audio.currentTime - 15); }
    if (e.key === 'ArrowRight' && P.name) {
      P.audio.currentTime = Math.min(P.audio.duration || 0, P.audio.currentTime + 15);
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {   // ⌘/Ctrl+Enter：开始合成
      e.preventDefault();
      if (S.tab !== 'synth') setTab('synth');
      $('btnStart').click();
    }
  });
}

function notifyDone(text) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try { new Notification('有声书工坊', { body: text, icon: '/icon-192.png' }); } catch (e) {}
}

/* ── 启动 ───────────────────────────────────────────── */
playerInit();
registerSW();
bindShortcuts();

boot().catch((e) => {
  document.body.innerHTML = `<div style="padding:40px;font-family:-apple-system">
    <h2>启动失败</h2><p>${esc(e.message)}</p></div>`;
});
