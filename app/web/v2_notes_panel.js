// v2_notes_panel.js — 标注清单 + 全生命周期追踪（2026-10-27）
//
// 解决的问题：`v2_note.js` 只能在**当前页**画 pin，写完的意见散在
// engine/state/ui_notes.json 里没人看得见全貌，更没法推进（谁接了、改到哪、验收没有）。
// 本文件是标注的**看板**：按页面分组列出全部标注，四条动作推进阶段，history 可追溯。
//
// 职责边界（重要）：
//   · 写口只有后端 `POST /api/ui_notes/stage`（ops 0.3.2）。本文件**不写盘**、不算 stats，
//     头部数字一律用响应里的 `stats`（唯一权威）；断网时退化为「按本地 notes 现算」并标注。
//   · 点选/新建标注仍在 v2_note.js，本文件不改它、不接管它的按钮与 pin。
//   · 壳层组件：节点挂 body，类名 np- 前缀，`data-np` 标记（v2_note 的 pickAt 会跳过
//     `[data-un]`，np- 不是 un- 前缀，故必须另带 data-np，见 CSS 段的 scrim/层级说明）。
//
// 分组口径：note.page。`v2:card:<卡 key>` = v2 卡页（卡名向壳要，拿不到显示 key）；
// `v2:` 其它 = 整壳页；其余 = 旧页（旧页已退役，只标记、不定位、不导航）。
//
// 阶段：open → doing → fixed → verified，另有 rejected（从 doing/fixed 直接驳回）。
// 已完成 = verified/rejected；doing/fixed 仍未完成（v2_note 的 pin 继续画）——与后端
// `CLOSED_STAGES` 逐字一致，这里重写一份是为了**不 fetch 后端也能算进度条**。
//
// 并发：每个动作都带 `expected=<展开时的 stage>`；后端 409 说明别人先动过，
// 此时提示「阶段已变化，已刷新」并整表重拉（绝不拿旧 stage 硬写，那会把别人的流转覆盖掉）。
//
// 出口：`window.__v2NotesPanel` + ES 默认导出。壳侧只做接线（见 shell.js::bindNotesPanel）：
// 传进来 selectCard（跨卡定位）、cardKey（当前卡）、cardTitle（page→卡名）。

'use strict';

const API_LIST = '/api/ui_notes/list';
const API_STAGE = '/api/ui_notes/stage';
// 2026-11-06（P6）：删除。单条 delete 带 expected；批量 purge 只许 closed 阶段 + 条数校验。
const API_DELETE = '/api/ui_notes/delete';
const API_PURGE = '/api/ui_notes/purge';
const BY = '01';                       // 处理人签名：写进 history.by 与 owner
const OWN_ATTR = 'data-np';            // 自家节点标记（与 v2_note 的 data-un 并列，互不干扰）
const PAGE_PREFIX = 'v2:';
const CARD_PREFIX = 'v2:card:';
const TONE_SLOT = 600;

const STAGES = ['open', 'doing', 'fixed', 'verified', 'rejected'];
const CLOSED = ['verified', 'rejected'];
const LABELS = { open: '待处理', doing: '处理中', fixed: '已修复', verified: '已验证', rejected: '驳回' };
const PROGRESS = ['open', 'doing', 'fixed', 'verified'];

// ---------------------------------------------------------------- 纯函数（可单测，不碰 DOM）

function stageOf(note) {
  const raw = note && typeof note === 'object' ? String(note.stage || '').trim() : '';
  if (STAGES.indexOf(raw) >= 0) return raw;
  if (!note || typeof note !== 'object') return 'open';
  // 老数据（本文件上线前的 35 条）：没有 stage 字段，按 status 推导。——与后端 _stage_of 同口径
  return note.status === 'resolved' ? 'verified' : 'open';
}

function stageLabel(stage) {
  return LABELS[stage] || LABELS.open;
}

function isClosed(stage) {
  return CLOSED.indexOf(stage) >= 0;
}

// page → 卡 key（只有 `v2:card:<key>` 形态是卡页；整壳/旧页都不是）
function pageCardKey(page) {
  const s = String(page || '');
  if (s.indexOf(CARD_PREFIX) !== 0) return null;
  const key = s.slice(CARD_PREFIX.length).trim();
  return key || null;
}

// 页面描述：kind = card / shell / retired；label 是人话；locate 决定「定位」按钮能不能点。
// 为什么旧页直接判 locate=false 而不是试着查一下：旧页 DOM 在 /legacy/，selector 是在另一个
// 文档里生成的，在 v2 文档里查必然落空 —— 查一次再报「元素已不存在」会把「这页已经没了」
// 说成「元素改过了」，误导。也不导航过去：旧页已退役，导航等于把主人扔进死胡同。
function pageInfo(page, titleOf) {
  const key = pageCardKey(page);
  if (key) {
    let title = '';
    if (typeof titleOf === 'function') {
      try { title = String(titleOf(key) || '').trim(); } catch (err) { title = ''; }
    }
    return { kind: 'card', key: key, label: title || key, locate: true, page: String(page || '') };
  }
  const s = String(page || '');
  if (s.indexOf(PAGE_PREFIX) === 0) {
    return { kind: 'shell', key: null, label: '整壳页面', locate: true, page: s };
  }
  return { kind: 'retired', key: null, label: '旧页已退役', locate: false, page: s || '(未标页)' };
}

function tsOf(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(text || '').trim());
  if (!m) return null;
  return { y: +m[1], mo: +m[2], d: +m[3], h: +m[4], mi: +m[5], s: +(m[6] || 0) };
}

function sortKey(text) {
  const t = tsOf(text);
  if (!t) return '';                                  // 解析不了排最后（空串 < 任何日期串）
  return t.y + '-' + String(t.mo).padStart(2, '0') + '-' + String(t.d).padStart(2, '0')
    + ' ' + String(t.h).padStart(2, '0') + ':' + String(t.mi).padStart(2, '0')
    + ':' + String(t.s).padStart(2, '0');
}

function fmtTime(text) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  const t = tsOf(raw);
  if (!t) return raw;                                 // 手写的怪格式原样显示，不猜
  const p2 = (n) => String(n).padStart(2, '0');
  return p2(t.mo) + '-' + p2(t.d) + ' ' + p2(t.h) + ':' + p2(t.mi);
}

function refKind(ref) {
  const s = String(ref || '').trim();
  if (!s) return '';
  if (/^https?:\/\//i.test(s)) return 'url';
  // 仓库相对路径 / Windows 绝对路径：至少一段分隔符，且不含空白（有空白更像随口一句说明）
  if (/^[A-Za-z]:[\\/]\S+$/.test(s)) return 'path';
  if (/^[\w.\-\u4e00-\u9fa5]+([\\/][^\s]+)+$/.test(s)) return 'path';
  return '';
}

function fmtHistory(history) {
  const list = Array.isArray(history) ? history : [];
  const out = [];
  for (const h of list) {
    if (!h || typeof h !== 'object') continue;
    const from = String(h.from || '');
    const to = String(h.to || '');
    out.push({
      at: fmtTime(h.at),
      by: String(h.by || '').trim() || '（未记）',
      from: LABELS[from] || from || '—',
      to: LABELS[to] || to || '—',
      note: String(h.note || ''),
      ref: String(h.ref || ''),
      refKind: refKind(h.ref),
    });
  }
  return out;
}

function groupNotes(notes, titleOf) {
  const rows = (Array.isArray(notes) ? notes : []).filter((x) => x && typeof x === 'object');
  const bucket = new Map();
  for (const note of rows) {
    const page = String(note.page || '');
    if (!bucket.has(page)) bucket.set(page, []);
    bucket.get(page).push(note);
  }
  const groups = [];
  for (const [page, list] of bucket) {
    list.sort((a, b) => sortKey(b.created_at).localeCompare(sortKey(a.created_at)));
    const info = pageInfo(page, titleOf);
    groups.push(Object.assign(info, {
      page: page,
      notes: list,
      newest: sortKey(list.length ? list[0].created_at : ''),
    }));
  }
  // 活跃页（v2 卡 + 整壳）按最近活跃倒序；退役页整体沉底，内部同样按最新在前。
  groups.sort((a, b) => {
    const ar = a.kind === 'retired' ? 1 : 0;
    const br = b.kind === 'retired' ? 1 : 0;
    if (ar !== br) return ar - br;
    return b.newest.localeCompare(a.newest) || a.page.localeCompare(b.page);
  });
  return groups;
}

function visibleNotes(notes, filter) {
  const f = filter || {};
  const stage = f.stage && f.stage !== 'all' ? f.stage : '';
  const q = String(f.query || '').trim().toLowerCase();
  const titleOf = f.titleOf;
  return (Array.isArray(notes) ? notes : []).filter((note) => {
    if (!note || typeof note !== 'object') return false;
    if (stage && stageOf(note) !== stage) return false;
    if (!q) return true;
    const page = String(note.page || '');
    const hay = [
      note.text, note.elem, note.id, page,
      pageCardKey(page) || '',
      (typeof titleOf === 'function' ? safeTitle(titleOf, pageCardKey(page)) : ''),
    ].join('\u0001').toLowerCase();
    return hay.indexOf(q) >= 0;
  });
}

function safeTitle(titleOf, key) {
  if (!key || typeof titleOf !== 'function') return '';
  try { return String(titleOf(key) || ''); } catch (err) { return ''; }
}

function countStats(notes) {
  const by_stage = {};
  for (const s of STAGES) by_stage[s] = 0;
  let total = 0;
  for (const note of Array.isArray(notes) ? notes : []) {
    if (!note || typeof note !== 'object') continue;
    total += 1;
    by_stage[stageOf(note)] += 1;
  }
  return {
    total: total,
    open: STAGES.filter((s) => !isClosed(s)).reduce((sum, s) => sum + by_stage[s], 0),
    by_stage: by_stage,
  };
}

// 下一动作表：任务书逐条映射。kind 决定按钮样式（primary / danger / plain），
// 不靠「第几个按钮」猜——样式与语义分离，改顺序不会改危险色。
const ACTIONS = {
  open: [{ to: 'doing', label: '开始处理', kind: 'primary' }],
  doing: [
    { to: 'fixed', label: '已修复', kind: 'primary' },
    { to: 'rejected', label: '驳回', kind: 'danger' },
  ],
  fixed: [
    { to: 'verified', label: '验收通过', kind: 'primary' },
    { to: 'doing', label: '退回处理', kind: 'plain' },
    { to: 'rejected', label: '驳回', kind: 'danger' },
  ],
  verified: [{ to: 'open', label: '重新打开', kind: 'plain' }],
  rejected: [{ to: 'open', label: '重新打开', kind: 'plain' }],
};

function actionsFor(stage) {
  const list = ACTIONS[stage];
  return (list || ACTIONS.open).map((a) => Object.assign({}, a));
}

// 四段进度条：done（已过）/ current（当前）/ todo / rejected（末段红色分支）
// rejected 不是第五段而是末段的另一种结局：前三段都已走完（有人接过、改过），末段变红。
function progressOf(stage) {
  const cur = stage || 'open';
  const at = PROGRESS.indexOf(cur);
  return PROGRESS.map((key, i) => {
    let state = 'todo';
    if (cur === 'rejected') state = i === PROGRESS.length - 1 ? 'rejected' : 'done';
    else if (i < at) state = 'done';
    else if (i === at) state = 'current';
    return { key: key, label: state === 'rejected' ? LABELS.rejected : LABELS[key], state: state };
  });
}

function stageBody(note, to, extra) {
  if (STAGES.indexOf(String(to || '')) < 0) {
    throw new Error('非法目标阶段：' + String(to));
  }
  const e = extra || {};
  return {
    id: String((note && note.id) || ''),
    to: String(to),
    expected: stageOf(note),
    by: BY,
    note: String(e.note || '').trim(),
    ref: String(e.ref || '').trim(),
  };
}

// ---------------------------------------------------------------- 状态

const st = {
  inited: false,
  btn: null,
  drawer: null,
  scrim: null,
  listEl: null,
  statsEl: null,
  chipsEl: null,
  inputEl: null,
  notes: [],
  stats: null,          // 后端权威；null = 还没拿到
  filter: { stage: 'all', query: '' },
  open: true,           // 展开态（Set<id>）
  expanded: new Set(),
  drafts: {},           // id -> {note, ref}：重渲染不丢已填的说明
  pending: new Set(),   // 正在提交的 note id
  errs: {},             // id -> 错误文案
  loading: false,
  loadErr: '',
  seq: 0,               // 竞态序号：只让最后一次刷新落地
  opts: {},
};

function mk(tag, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  node.setAttribute(OWN_ATTR, '1');
  return node;
}

function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function toast(text) {
  const fn = st.opts.toast;
  if (typeof fn === 'function') { try { fn(text); return; } catch (err) { /* 落到 console */ } }
  console.log('[notes-panel] ' + text);
}

function cardTitle(key) {
  const fn = st.opts.cardTitle;
  if (typeof fn !== 'function') return '';
  try { return String(fn(key) || ''); } catch (err) { return ''; }
}

function titleOf(key) { return cardTitle(key); }

// ---------------------------------------------------------------- 骨架

function build() {
  if (st.drawer && st.drawer.isConnected) return;
  st.scrim = document.getElementById('scrim');

  const drawer = mk('aside', 'np-drawer');
  drawer.setAttribute('aria-label', '标注清单');
  drawer.innerHTML =
    '<div class="np-hd">'
    + '<span class="np-t">标注</span>'
    + '<span class="np-total" data-np-total></span>'
    + '<button type="button" class="np-x" data-np-act="close" aria-label="关闭">✕</button>'
    + '</div>'
    + '<div class="np-stats" data-np-stats></div>'
    + '<div class="np-tools">'
    + '<div class="np-chips" data-np-chips></div>'
    + '<input type="text" class="np-q" data-np-q placeholder="搜索 文本 / 元素 / id / 页面" '
    + 'autocomplete="off" spellcheck="false" />'
    + '</div>'
    + '<div class="np-body" data-np-list></div>'
    + '<div class="np-ft"><span>阶段 POST /ui_notes/stage · 删除 POST /ui_notes/delete</span>'
    + '<button type="button" class="np-act is-danger" data-np-act="purge">清理已完成</button></div>';
  document.body.append(drawer);
  st.drawer = drawer;
  st.listEl = drawer.querySelector('[data-np-list]');
  st.statsEl = drawer.querySelector('[data-np-stats]');
  st.chipsEl = drawer.querySelector('[data-np-chips]');
  st.inputEl = drawer.querySelector('[data-np-q]');

  drawer.addEventListener('click', onPanelClick);
  if (st.inputEl) {
    st.inputEl.addEventListener('input', function () {
      st.filter.query = st.inputEl.value;
      renderList();
    });
  }
  // 遮罩与设置抽屉共用（一个 #scrim）：设置侧只摘 .on，故面板必须自己收。
  // 用 capture：设置抽屉的 closeSettings 挂在冒泡上，谁先谁后都不影响「点遮罩就收起」。
  if (st.scrim) st.scrim.addEventListener('click', function () {
    if (isOpen()) close();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (!isOpen()) return;
    close();
  });
}

// ---------------------------------------------------------------- 渲染

// 头部统计：五个阶段 + 总数。顺序与 chips/进度条同（open→doing→fixed→verified→rejected），
// 数值只认后端 stats（applyNote 后仍用响应里的 stats），断网时才按本地 notes 现算。
function renderStats() {
  if (!st.statsEl) return;
  const s = st.stats || countStats(st.notes);
  st.statsEl.innerHTML = STAGES.map((key) => (
    '<span class="np-stat is-' + key + '"><b>' + s.by_stage[key] + '</b>' + esc(LABELS[key]) + '</span>'
  )).join('') + '<span class="np-stat is-total"><b>' + s.total + '</b>总数</span>';
  const total = st.drawer.querySelector('[data-np-total]');
  if (total) total.textContent = s.open ? ('未完成 ' + s.open + ' 条') : '全部完成';
}

function renderChips() {
  if (!st.chipsEl) return;
  const s = st.stats || countStats(st.notes);
  const items = [['all', '全部', s.total]].concat(
    STAGES.map((key) => [key, LABELS[key], s.by_stage[key]]),
  );
  st.chipsEl.innerHTML = items.map((it) => (
    '<button type="button" class="np-chip' + (st.filter.stage === it[0] ? ' is-on' : '')
    + '" data-np-act="chip" data-stage="' + it[0] + '">' + esc(it[1])
    + '<i>' + it[2] + '</i></button>'
  )).join('');
}

function noteId(note) {
  return String((note && note.id) || '');
}

function renderNote(note) {
  const id = noteId(note);
  const stage = stageOf(note);
  const expanded = st.expanded.has(id);
  const busy = st.pending.has(id);
  const err = st.errs[id] || '';
  const draft = st.drafts[id] || { note: '', ref: '' };

  const head = '<div class="np-note-hd" data-np-act="toggle" data-id="' + esc(id) + '">'
    + '<span class="np-badge is-' + stage + '">' + esc(stageLabel(stage)) + '</span>'
    + '<span class="np-text">' + esc(note.text || '(空)') + '</span>'
    + '<span class="np-meta">'
    + '<span class="np-elem">' + esc(note.elem || '') + '</span>'
    + '<span class="np-time">' + esc(fmtTime(note.created_at)) + '</span>'
    + (note.owner ? '<span class="np-owner">' + esc(note.owner) + '</span>' : '')
    + '</span></div>';

  if (!expanded) {
    return '<div class="np-note" data-id="' + esc(id) + '">' + head + '</div>';
  }

  const seg = progressOf(stage).map((p) => (
    '<span class="np-seg is-' + p.state + '"><i></i>' + esc(p.label) + '</span>'
  )).join('');

  const his = fmtHistory(note.history);
  const hisHtml = his.length
    ? his.map((h) => (
      '<div class="np-his-row">'
      + '<span class="np-his-at">' + esc(h.at) + '</span>'
      + '<span class="np-his-by">' + esc(h.by) + '</span>'
      + '<span class="np-his-flow">' + esc(h.from) + '→' + esc(h.to) + '</span>'
      + (h.note ? '<span class="np-his-note">' + esc(h.note) + '</span>' : '')
      + (h.ref && h.refKind
        ? '<button type="button" class="np-ref is-' + h.refKind + '" data-np-act="ref" data-kind="'
          + h.refKind + '" data-ref="' + esc(h.ref) + '">'
          + (h.refKind === 'url' ? '打开 ' : '复制 ') + esc(h.ref) + '</button>'
        : (h.ref ? '<span class="np-his-note">' + esc(h.ref) + '</span>' : ''))
      + '</div>'
    )).join('')
    : '<div class="np-his-empty">还没有流转记录</div>';

  const acts = actionsFor(stage).map((a) => (
    '<button type="button" class="np-act is-' + a.kind + '" data-np-act="stage" data-id="' + esc(id)
    + '" data-to="' + esc(a.to) + '"' + (busy ? ' disabled' : '') + '>' + esc(a.label) + '</button>'
  )).join('');

  const info = pageInfo(note.page, titleOf);
  const locateBtn = info.locate
    ? '<button type="button" class="np-act is-plain" data-np-act="locate" data-id="' + esc(id) + '">定位</button>'
    : '<span class="np-nolocate" title="' + esc(note.page || '') + '">旧页已退役 · 不可定位</span>';

  return '<div class="np-note is-open" data-id="' + esc(id) + '">'
    + head
    + '<div class="np-note-body">'
    + '<div class="np-kv"><span class="np-k">selector</span><code class="np-sel">'
    + esc(note.selector || '(无)') + '</code></div>'
    + '<div class="np-prog">' + seg + '</div>'
    + '<div class="np-his">' + hisHtml + '</div>'
    + '<div class="np-doc">'
    + '<input type="text" class="np-in" data-np-note="' + esc(id) + '" placeholder="说明（可空）" value="'
    + esc(draft.note) + '" />'
    + '<input type="text" class="np-in" data-np-ref="' + esc(id) + '" placeholder="文档路径 / URL（可空）" value="'
    + esc(draft.ref) + '" />'
    + '</div>'
    + '<div class="np-acts">' + acts + locateBtn
    + '<button type="button" class="np-act is-danger" data-np-act="del" data-id="' + esc(id) + '"'
    + (busy ? ' disabled' : '') + '>删除</button>'
    + '</div>'
    + (err ? '<div class="np-err">' + esc(err) + '</div>' : '')
    + '</div></div>';
}

function renderList() {
  if (!st.listEl) return;
  const rows = visibleNotes(st.notes, { stage: st.filter.stage, query: st.filter.query, titleOf: titleOf });

  if (!st.notes.length) {
    st.listEl.innerHTML = st.loadErr
      ? '<div class="np-empty is-err">读取失败：' + esc(st.loadErr) + '<br />可点右上角刷新重试。</div>'
      : (st.loading ? '<div class="np-empty">读取中…</div>' : '<div class="np-empty">还没有标注。点侧栏「标注」后点页面元素写第一条。</div>');
    return;
  }
  if (!rows.length) {
    const why = st.filter.query ? '没有匹配「' + esc(st.filter.query) + '」的标注' : '这个阶段还没有标注';
    st.listEl.innerHTML = '<div class="np-empty">' + why + '</div>';
    return;
  }

  const groups = groupNotes(rows, titleOf);
  st.listEl.innerHTML = groups.map((g) => {
    const retired = g.kind === 'retired';
    const tag = retired ? '<span class="np-tag">旧页已退役</span>' : '';
    return '<section class="np-group' + (retired ? ' is-retired' : '') + '">'
      + '<div class="np-group-hd"><span class="np-group-t">' + esc(g.label) + '</span>' + tag
      + '<span class="np-group-n">' + g.notes.length + '</span></div>'
      + (retired ? '<div class="np-group-sub">' + esc(g.page) + ' · 只读归档，不支持定位与跳转</div>' : '')
      + g.notes.map(renderNote).join('')
      + '</section>';
  }).join('');
}

function render() {
  renderStats();
  renderChips();
  renderList();
  renderFoot();
}

// 页脚「清理已完成 N」：数字只认后端 stats（与头部同源），为 0 时禁用。
function renderFoot() {
  const btn = st.drawer && st.drawer.querySelector('[data-np-act="purge"]');
  if (!btn) return;
  const by = (st.stats && st.stats.by_stage) || {};
  const n = (by.verified || 0) + (by.rejected || 0);
  btn.textContent = n ? '清理已完成 ' + n : '清理已完成';
  btn.disabled = !n;
}

// ---------------------------------------------------------------- 数据

async function fetchList() {
  const seq = ++st.seq;
  st.loading = true;
  st.loadErr = '';
  renderList();
  try {
    const res = await fetch(API_LIST, { cache: 'no-store', headers: { Accept: 'application/json' } });
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (seq !== st.seq) return;                       // 被后来的刷新取代
    if (!res.ok) throw new Error((data && data.detail) || ('HTTP ' + res.status));
    st.notes = Array.isArray(data && data.notes) ? data.notes : [];
    st.stats = (data && data.stats) || null;
    st.loadErr = '';
  } catch (err) {
    if (seq !== st.seq) return;
    st.loadErr = (err && err.message) ? err.message : String(err);
  } finally {
    if (seq === st.seq) {
      st.loading = false;
      render();
    }
  }
}

function applyNote(note) {
  if (!note || !note.id) return;
  const idx = st.notes.findIndex((n) => noteId(n) === String(note.id));
  if (idx >= 0) st.notes[idx] = note;
  else st.notes.push(note);
}

async function submitStage(noteId_, to) {
  const note = st.notes.find((n) => noteId(n) === noteId_);
  if (!note) { toast('标注已不在列表里，已刷新'); fetchList(); return; }
  const draft = st.drafts[noteId_] || { note: '', ref: '' };
  let body;
  try {
    body = stageBody(note, to, draft);
  } catch (err) {
    st.errs[noteId_] = err.message;
    renderList();
    return;
  }
  if (st.pending.has(noteId_)) return;
  st.pending.add(noteId_);
  delete st.errs[noteId_];
  renderList();
  try {
    const res = await fetch(API_STAGE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (res.status === 409) {
      // 别人先动过：不重试、不硬写。提示 + 整表重拉，让主人看到真实阶段再决定。
      toast('阶段已变化，已刷新');
      st.pending.delete(noteId_);
      await fetchList();
      return;
    }
    if (!res.ok) {
      const detail = (data && data.detail) ? data.detail : ('HTTP ' + res.status);
      throw new Error(String(detail));
    }
    if (data && data.note) applyNote(data.note);
    if (data && data.stats) st.stats = data.stats;    // 头部数字只认后端
    st.drafts[noteId_] = { note: '', ref: '' };
    st.pending.delete(noteId_);
    st.expanded.add(noteId_);                         // 刚推进的这条保持展开，能看见新 history
    render();
    toast('已更新为「' + stageLabel(to) + '」');
    fetchList();                                      // 静默全量对齐（grouping/统计口径以盘上为准）
  } catch (err) {
    st.pending.delete(noteId_);
    st.errs[noteId_] = '保存失败：' + ((err && err.message) ? err.message : String(err)) + '（可重试）';
    renderList();
    toast('保存失败：' + ((err && err.message) ? err.message : String(err)));
  }
}

// ---------------------------------------------------------------- 删除（2026-11-06 P6）

// 删除不可逆：全模块唯一用系统确认框的地方（toast 一闪就过，不适合当“确定要删”的关口）。
function confirmDelete(title, detail) {
  try {
    return window.confirm('确定' + title + '？'
      + (detail ? '\n\n' + detail : '')
      + '\n\n删掉后不可撤销（只能从文件历史里找回）。');
  } catch (err) { return false; }
}

async function submitDelete(noteId_) {
  const note = st.notes.find((n) => noteId(n) === noteId_);
  if (!note) { toast('标注已不在列表里，已刷新'); fetchList(); return; }
  const expect = stageOf(note);
  if (!confirmDelete('删除这条标注', String(note.text || '').slice(0, 80))) return;
  if (st.pending.has(noteId_)) return;
  st.pending.add(noteId_);
  delete st.errs[noteId_];
  renderList();
  try {
    const res = await fetch(API_DELETE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ id: noteId_, expected: expect }),
    });
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (res.status === 409) {
      toast('阶段已变化，已刷新');
      st.pending.delete(noteId_);
      await fetchList();
      return;
    }
    if (!res.ok) throw new Error(String((data && data.detail) ? data.detail : ('HTTP ' + res.status)));
    if (data && data.stats) st.stats = data.stats;
    st.pending.delete(noteId_);
    st.expanded.delete(noteId_);
    delete st.drafts[noteId_];
    render();
    toast('已删除 1 条标注');
    fetchList();
  } catch (err) {
    st.pending.delete(noteId_);
    st.errs[noteId_] = '删除失败：' + ((err && err.message) ? err.message : String(err)) + '（可重试）';
    renderList();
    toast(st.errs[noteId_]);
  }
}

// 批量清理：只动 closed 阶段（已验证/驳回）。未完成的标注是待办，必须逐条处理。
async function submitPurge() {
  const by = (st.stats && st.stats.by_stage) || {};
  const nv = by.verified || 0;
  const nr = by.rejected || 0;
  const total = nv + nr;
  if (!total) { toast('没有已完成的标注可清理'); return; }
  if (!window.confirm('清理已完成标注？\n\n已验证 ' + nv + ' 条、驳回 ' + nr + ' 条，共 ' + total + ' 条。'
      + '\n未处理 / 处理中 / 已修复的标注不动。\n\n清理后不可撤销。')) return;
  try {
    const plan = [['verified', nv], ['rejected', nr]];
    for (const pair of plan) {
      if (!pair[1]) continue;
      const res = await fetch(API_PURGE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ stage: pair[0], expect_count: pair[1] }),
      });
      let data = null;
      try { data = await res.json(); } catch (err) { data = null; }
      if (res.status === 409) { toast('条数已变，已刷新'); await fetchList(); return; }
      if (!res.ok) throw new Error(String((data && data.detail) ? data.detail : ('HTTP ' + res.status)));
      if (data && data.stats) st.stats = data.stats;
    }
    render();
    toast('已清理 ' + total + ' 条已完成标注');
    fetchList();
  } catch (err) {
    toast('清理失败：' + ((err && err.message) ? err.message : String(err)));
    fetchList();
  }
}

// ---------------------------------------------------------------- 定位

function hostSel(key) {
  return '.card-host[data-card-key="' + String(key).replace(/["\\]/g, '\\$&') + '"]';
}

function findTarget(key, selector) {
  let root = null;
  if (key) {
    try { root = document.querySelector(hostSel(key)); } catch (err) { root = null; }
    if (!root) return null;
  }
  const scope = root || document;
  if (!selector) return null;
  let hit = null;
  try { hit = scope.querySelector(selector); } catch (err) { hit = null; }   // 非法 selector 当失效
  if (!hit && scope !== document) {
    try { hit = document.querySelector(selector); } catch (err) { hit = null; }
  }
  return hit;
}

function flash(el) {
  if (!el || !el.classList) return;
  el.classList.remove('np-hit');
  void el.offsetWidth;                                // 强制回流：连点两次也能重播动画
  el.classList.add('np-hit');
  setTimeout(function () { el.classList.remove('np-hit'); }, 1200);
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// 跨卡定位：先切卡，再**轮询卡片 host + selector**（最多 3s）。
// 为什么必须轮询而不是切完直接查：selectCard 是异步挂载（import 插件 + mount），
// 卡片 DOM 在几十到几百毫秒后才出现；查早了只会得到「元素已不存在」这个假结论。
async function locate(note) {
  const info = pageInfo(note.page, titleOf);
  if (!info.locate) {
    toast('旧页标注不可定位（' + (info.page || '未知页') + '）：旧页已退役，只留归档');
    return;
  }
  const cur = typeof st.opts.cardKey === 'function' ? String(st.opts.cardKey() || '') : '';
  const crossCard = info.kind === 'card' && info.key !== cur;
  if (crossCard) {
    const to = st.opts.selectCard;
    if (typeof to === 'function') {
      toast('切换到「' + info.label + '」后定位…');
      try { await to(info.key); } catch (err) { toast('切卡失败：' + ((err && err.message) || err)); return; }
    } else {
      const btn = document.querySelector('.rail-btn[data-card-key="' + info.key.replace(/["\\]/g, '\\$&') + '"]');
      if (btn) btn.click(); else { toast('找不到「' + info.label + '」入口'); return; }
    }
  }
  const deadline = Date.now() + 3000;
  let hit = null;
  while (Date.now() < deadline) {
    hit = findTarget(info.kind === 'card' ? info.key : null, note.selector);
    if (hit) break;
    await sleep(120);
  }
  if (!hit) { toast('元素已不存在：' + (note.selector || '(无 selector)')); return; }
  try { hit.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' }); } catch (err) { hit.scrollIntoView(); }
  flash(hit);
  if (isOpen()) close();                              // 抽屉盖住右侧 440px，定位后收起才不会挡住目标
}

// ---------------------------------------------------------------- 事件

function onPanelClick(event) {
  const t = event.target;
  const btn = t && t.closest ? t.closest('[data-np-act]') : null;
  if (!btn) return;
  const act = btn.getAttribute('data-np-act');
  if (act === 'close') { event.preventDefault(); close(); return; }
  if (act === 'chip') {
    event.preventDefault();
    st.filter.stage = btn.getAttribute('data-stage') || 'all';
    render();
    return;
  }
  if (act === 'toggle') {
    const id = btn.getAttribute('data-id') || '';
    if (!id) return;
    if (st.expanded.has(id)) st.expanded.delete(id); else st.expanded.add(id);
    renderList();
    return;
  }
  if (act === 'stage') {
    event.preventDefault();
    submitStage(btn.getAttribute('data-id') || '', btn.getAttribute('data-to') || '');
    return;
  }
  if (act === 'del') {
    event.preventDefault();
    submitDelete(btn.getAttribute('data-id') || '');
    return;
  }
  if (act === 'purge') {
    event.preventDefault();
    submitPurge();
    return;
  }
  if (act === 'locate') {
    event.preventDefault();
    const id = btn.getAttribute('data-id') || '';
    const note = st.notes.find((n) => noteId(n) === id);
    if (!note) { toast('标注已不在列表里，已刷新'); fetchList(); return; }
    locate(note);
    return;
  }
  if (act === 'ref') {
    event.preventDefault();
    const ref = btn.getAttribute('data-ref') || '';
    const kind = btn.getAttribute('data-kind') || '';
    if (kind === 'url') {
      try { window.open(ref, '_blank', 'noopener'); } catch (err) { toast('打开失败：' + ref); }
    } else {
      copyText(ref);
    }
  }
}

// 输入框改的是草稿，不是行内 state：重渲染（筛选/刷新/推进）后值仍在。
function onPanelInput(event) {
  const t = event.target;
  if (!t) return;
  const idNote = t.getAttribute && t.getAttribute('data-np-note');
  const idRef = t.getAttribute && t.getAttribute('data-np-ref');
  const id = idNote || idRef;
  if (!id) return;
  const draft = st.drafts[id] || { note: '', ref: '' };
  if (idNote) draft.note = t.value; else draft.ref = t.value;
  st.drafts[id] = draft;
}

// 回车提交当前最可能的动作（主决策项）：列表里连点三下就能推进一条，不用先摸鼠标。
function onPanelKey(event) {
  if (event.key !== 'Enter') return;
  const t = event.target;
  const id = t && t.getAttribute ? (t.getAttribute('data-np-note') || t.getAttribute('data-np-ref')) : '';
  if (!id) return;
  event.preventDefault();
  const note = st.notes.find((n) => noteId(n) === id);
  if (!note) return;
  const first = actionsFor(stageOf(note))[0];
  if (first) submitStage(id, first.to);
}

function copyText(text) {
  const done = () => toast('已复制：' + text);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => copyFallback(text, done));
    return;
  }
  copyFallback(text, done);
}

function copyFallback(text, done) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand && document.execCommand('copy');
    ta.remove();
    if (ok) done(); else toast('复制失败：' + text);
  } catch (err) { toast('复制失败：' + text); }
}

// ---------------------------------------------------------------- 开关

function isOpen() {
  return !!(st.drawer && st.drawer.classList.contains('on'));
}

function open() {
  build();
  // 与设置抽屉/搜索面板互斥：三者共用一块遮罩与同一片右侧空间，同时开就是互相盖。
  const hook = st.opts.onOpen;
  if (typeof hook === 'function') { try { hook(); } catch (err) { /* 接线失败不阻断面板 */ } }
  st.drawer.classList.add('on');
  if (st.scrim) st.scrim.classList.add('on');
  if (st.btn) { st.btn.classList.add('is-on'); st.btn.setAttribute('aria-pressed', 'true'); }
  render();
  fetchList();                                        // 每次打开都拉一次（标注是别人也会写的数据）
}

function close() {
  if (st.drawer) st.drawer.classList.remove('on');
  if (st.scrim) st.scrim.classList.remove('on');
  if (st.btn) { st.btn.classList.remove('is-on'); st.btn.setAttribute('aria-pressed', 'false'); }
}

function toggle() {
  if (isOpen()) close(); else open();
}

// ---------------------------------------------------------------- 对外

function init(options) {
  const opts = options || {};
  st.opts = Object.assign({}, st.opts, opts);
  build();
  if (opts.button) st.btn = opts.button;
  else st.btn = document.getElementById('rail-notes');
  if (st.btn && !st.btn.dataset.npBound) {
    st.btn.dataset.npBound = '1';
    st.btn.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      toggle();
    });
  }
  if (!st.inited) {
    st.inited = true;
    st.drawer.addEventListener('input', onPanelInput);
    st.drawer.addEventListener('keydown', onPanelKey);
  }
  return api;
}

const api = {
  init: init,
  open: open,
  close: close,
  toggle: toggle,
  isOpen: isOpen,
  refresh: fetchList,
  // 纯函数（单测 + 壳侧复用）
  STAGES: STAGES,
  BY: BY,
  API_LIST: API_LIST,
  API_STAGE: API_STAGE,
  stageOf: stageOf,
  stageLabel: stageLabel,
  isClosed: isClosed,
  pageCardKey: pageCardKey,
  pageInfo: pageInfo,
  groupNotes: groupNotes,
  visibleNotes: visibleNotes,
  countStats: countStats,
  actionsFor: actionsFor,
  progressOf: progressOf,
  fmtTime: fmtTime,
  fmtHistory: fmtHistory,
  refKind: refKind,
  stageBody: stageBody,
};

// 双出口：动态 import 拿 default；模块先执行完的时序走 window 兜底。
// 判 window 是否存在：本文件被 node 单测直接 import（无 DOM），不能因顶层赋值就炸。
if (typeof window !== 'undefined') window.__v2NotesPanel = api;
export default api;
