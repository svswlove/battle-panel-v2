// ============================================================================
// 概念追踪卡片 · 前端（docs/task_R3_concept_review.md，0.1.0 → 0.2.0）
// ----------------------------------------------------------------------------
// 行为基准 = 旧概念 Tab（engine/web/index.html 7690 行 renderConcept 起）：
//   · 卡头：总表更新时间 + 视图胶囊（活跃 / 观察池 / 链位 / 已归档，各带计数）+ 筛选框
//     （旧 _cpShowView：筛选框只在「活跃 / 观察池」两视图显示）
//   · 活跃视图：阶段光谱（hot/warm/cool 按 counts 比例分条）+ 概念卡墙
//     （热区左边条 / 阶段徽标 / 逻辑·持续性 / 板块 chips / 核心标的 chips / 更新日 + 下一节点）
//   · 观察池视图：多空光谱（bull/bear）+ 卡片（多空徽标 / 参照板块 chips / 待验证 / AMV 权重变化）
//   · 链位视图：上游 → 本环节 → 下游 三段流向卡（_detail 有码才可点，无码显式不可点；
//     括号内的标的按 in_group 虚线分组，kind='label' 的是类别 chip）
//   · 已归档视图：概念名 + 归档说明
//   · 点卡开概况（旧 CpDetail.open 弹层）→ 本卡改为**卡内详情视图**（面包屑 + 返回），
//     正文由服务端渲染好的 MD→HTML 直接落地，不做二次解析
//
// 端点（concept 插件 mount=root，路径与旧端同名，故用绝对 /api/ 前缀，shell 不拼插件前缀）：
//   GET /api/concept.json            → 总表索引
//   GET /api/concept.json?slug=xxx   → 单概念概况 HTML（{ok, slug, html}；缺失 404）
//
// 个股名链接：服务端 HTML 与前端 chips 一律只写 data-open-kline / data-open-name 属性占位，
//   本卡不绑 K 线逻辑——宿主页（旧页或外部壳）按该属性捕获即可。缺码 → cp-chip-off
//   （显式不可点，不伪装可点）。
//
// 契约（docs/CONTRACT.md §5）：
//   1. 原生 ES module，零 import、零构建；只操作自己的 root。
//   2. 只走 ctx.api.get；颜色一律 themes.css 语义 token，本文件零裸 hex。
//   3. 骨架只建一次，内容走字符串比对（内容没变不写 DOM，防闪）。
//   4. 不声明 refreshEvery：概念总表日频，旧端也是切 Tab 才拉；保留 refresh() 占位 + 手动刷新。
//   5. 旧「物理AI 读数带」（/api/physical_ai.json）本批不搬：该端点当前 404，旧页同样不渲染；
//      后端补上后按同一位置（卡头下）再加，不影响本卡结构。
// ============================================================================

const STYLE_ID = 'concept-card-css';

const CSS = [
  '.cp-card-root{display:flex;flex-direction:column;gap:var(--d-gap);color:var(--fg);min-width:0}',
  '.cp-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
  '.cp-tabs{display:flex;gap:8px;flex-wrap:wrap}',
  '.cp-pill{padding:3px 13px;border-radius:var(--radius-pill);border:var(--border-w) var(--border-style) var(--line);background:transparent;color:var(--dim);font-size:0.78rem;font-family:inherit;cursor:pointer}',
  '.cp-pill b{color:var(--fg-strong);font-weight:700}',
  '.cp-pill:hover{border-color:var(--accent);color:var(--fg)}',
  '.cp-pill.active{border-color:var(--accent);background:var(--accent-bg);color:var(--accent)}',
  '.cp-pill.active b{color:var(--accent)}',
  '.cp-upd{margin-left:auto;font-size:0.72rem;color:var(--dim)}',
  '.cp-search input{background:var(--card);border:var(--border-w) var(--border-style) var(--line);color:var(--fg);border-radius:var(--radius-sm);padding:5px 10px;font-size:0.78rem;min-width:170px;font-family:inherit}',
  '.cp-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer}',
  '.cp-btn:hover{background:var(--card2)}',
  '.cp-err{padding:6px 9px;border-radius:var(--radius-sm);background:var(--danger-bg);color:var(--danger-ink,var(--danger));font-size:0.75rem;line-height:1.5;word-break:break-word}',
  '.cp-empty{color:var(--dim);padding:14px}',
  '.cp-body{display:flex;flex-direction:column;gap:var(--d-gap);min-width:0}',
  '.cp-spec{background:var(--card);border:var(--border-w) var(--border-style) var(--line);border-radius:var(--radius-md);padding:10px 12px}',
  '.cp-spec-t{font-size:0.72rem;color:var(--dim);display:flex;justify-content:space-between;gap:10px;margin-bottom:8px}',
  '.cp-spec-track{display:flex;gap:5px;height:30px}',
  '.cp-seg{border-radius:5px;display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:600;white-space:nowrap;overflow:hidden;padding:0 4px}',
  '.cp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:var(--d-gap)}',
  '.cp-c{background:var(--card);border:var(--border-w) var(--border-style) var(--line);border-left:4px solid var(--line);border-radius:var(--radius-md);padding:var(--d-pad);min-width:0}',
  '.cp-c.link{cursor:pointer}',
  '.cp-c.link:hover{box-shadow:var(--shadow-card)}',
  '.cp-c.hot{border-left-color:var(--up)}',
  '.cp-c.warm{border-left-color:var(--warn)}',
  '.cp-c.cool{border-left-color:var(--accent)}',
  '.cp-c.bull{border-left-color:var(--up)}',
  '.cp-c.bear{border-left-color:var(--down)}',
  '.cp-c-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;margin-bottom:6px}',
  '.cp-c-name{font-weight:600;color:var(--fg-strong);font-size:0.92rem;line-height:1.3;word-break:break-word}',
  '.cp-badge{font-size:0.68rem;font-weight:600;padding:2px 8px;border-radius:var(--radius-pill);white-space:nowrap;flex-shrink:0;border:var(--border-w) var(--border-style);color:var(--dim)}',
  '.cp-badge.hot{color:var(--up);border-color:var(--up);background:var(--up-bg)}',
  '.cp-badge.warm{color:var(--warn);border-color:var(--warn);background:var(--warn-bg)}',
  '.cp-badge.cool{color:var(--accent);border-color:var(--accent);background:var(--accent-bg)}',
  '.cp-badge.bull{color:var(--up);border-color:var(--up);background:var(--up-bg)}',
  '.cp-badge.bear{color:var(--down);border-color:var(--down);background:var(--down-bg)}',
  '.cp-c-meta{font-size:0.72rem;color:var(--dim);margin-bottom:8px;line-height:1.5;word-break:break-word}',
  '.cp-c-meta b{color:var(--accent);font-weight:600}',
  '.cp-chips{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px}',
  '.cp-chip{font-size:0.7rem;padding:1px 7px;border-radius:4px;background:var(--card2);border:var(--border-w) var(--border-style) var(--line);color:var(--fg)}',
  '.cp-chip-link{cursor:pointer}',
  '.cp-chip-link:hover{border-color:var(--accent);color:var(--accent)}',
  '.cp-chip-off{cursor:default;opacity:.75;border-style:dashed}',
  '.cp-chip-cat{background:transparent;color:var(--dim);border-style:dotted}',
  '.cp-chip-grp{display:inline-flex;flex-wrap:wrap;gap:3px;padding:1px 4px;border-radius:4px;background:var(--bg);border:1px dashed var(--line-soft)}',
  '.cp-chip-sec{border-color:var(--accent);color:var(--accent);background:transparent}',
  '.cp-c-foot{border-top:1px dashed var(--line-soft);padding-top:6px;display:flex;justify-content:space-between;gap:8px;font-size:0.68rem;color:var(--dim)}',
  '.cp-c-foot .node{color:var(--warn);text-align:right;max-width:64%;line-height:1.4}',
  '.cp-w-q{font-size:0.79rem;line-height:1.6;color:var(--fg);border-top:1px dashed var(--line-soft);padding-top:7px;word-break:break-word}',
  '.cp-w-ql{display:inline-block;margin-right:6px;font-size:0.68rem;font-weight:600;color:var(--warn)}',
  '.cp-w-ql.dim{color:var(--dim)}',
  '.cp-w-foot{margin-top:7px;display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:0.7rem;color:var(--dim)}',
  '.cp-delta{font-variant-numeric:tabular-nums;font-weight:600}',
  '.cp-bull{color:var(--up)}',
  '.cp-bear{color:var(--down)}',
  '.cp-flow{display:flex;align-items:stretch;gap:6px;flex-wrap:wrap}',
  '.cp-flow-step{flex:1 1 150px;min-width:0;background:var(--bg);border:var(--border-w) var(--border-style) var(--line);border-radius:var(--radius-sm);padding:6px 8px}',
  '.cp-flow-step .cp-chips{margin:4px 0 0}',
  '.cp-flow-l{font-size:0.68rem;font-weight:600;color:var(--dim)}',
  '.cp-flow-step.mid .cp-flow-l{color:var(--accent)}',
  '.cp-flow-arrow{align-self:center;color:var(--dim);font-size:0.9rem}',
  '.cp-empty-chip{font-size:0.7rem;color:var(--dim)}',
  // 详情视图（旧 CpDetail 弹层的卡内替代）
  '.cp-detail-hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;padding-bottom:6px;border-bottom:1px solid var(--line-soft)}',
  '.cp-detail-t{font-weight:600;color:var(--fg-strong);font-size:0.95rem}',
  '.cp-detail-hd .cp-upd{margin-left:auto}',
  // 概况正文（服务端 MD→HTML）：类名与旧 .cp-md 一致，仅此一处样式定义
  '.cp-md{font-size:0.82rem;line-height:1.8;color:var(--fg);min-width:0}',
  '.cp-md h1,.cp-md h2,.cp-md h3,.cp-md h4{color:var(--fg-strong);margin:14px 0 6px;line-height:1.4}',
  '.cp-md h1{font-size:1.1rem}',
  '.cp-md h2{font-size:1.02rem;padding-left:9px;border-left:3px solid var(--warn)}',
  '.cp-md h3{font-size:0.92rem;color:var(--accent)}',
  '.cp-md p{margin:8px 0;color:var(--fg)}',
  '.cp-md strong,.cp-md b{color:var(--fg-strong);font-weight:600}',
  '.cp-md blockquote{margin:10px 0;padding:8px 12px;border-left:3px solid var(--accent);background:var(--accent-bg);border-radius:0 var(--radius-sm) var(--radius-sm) 0;color:var(--dim)}',
  '.cp-md ul,.cp-md ol{margin:6px 0 6px 20px}',
  '.cp-md li{margin:3px 0}',
  '.cp-md hr{border:none;border-top:1px solid var(--line-soft);margin:16px 0}',
  '.cp-md code{background:var(--line-soft);padding:1px 4px;border-radius:3px;font-family:var(--font-mono);font-size:0.78rem}',
  '.cp-md .md-table-wrap{overflow-x:auto;margin:8px 0;border:var(--border-w) var(--border-style) var(--line);border-radius:var(--radius-sm)}',
  '.cp-md table{width:100%;border-collapse:collapse;font-size:0.76rem}',
  '.cp-md th,.cp-md td{border-bottom:1px solid var(--line-soft);padding:5px 9px;text-align:left;vertical-align:top}',
  '.cp-md th{color:var(--dim);background:var(--card2);white-space:nowrap}',
  '.cp-md img{max-width:100%;height:auto}',
  // 可点个股名（服务端产出 .k-name + data-open-kline；本卡只给视觉态，不绑行为）
  '.cp-md .k-name,.cp-c .k-name{cursor:pointer}',
  '.cp-md .k-name:hover{color:var(--accent);text-decoration:underline;text-underline-offset:2px}',
  '.cp-md .k-nolink{cursor:default;opacity:.75}',
  '.cp-src{font-size:0.66rem;color:var(--dim);line-height:1.6}',
  '@media (max-width:720px){.cp-grid{grid-template-columns:1fr}}',
].join('\n');

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

// ---------------------------------------------------------------- 工具
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const errText = (e) => (e && e.message ? e.message : String(e));
// 链位名称串的分隔符（与后端 _CHAIN_SPLIT 同口径）
const CHAIN_SPLIT = /[/、]/;
const VIEWS = [['active', '活跃'], ['watch', '观察池'], ['chain', '链位'], ['archived', '已归档']];

// 链位成员 chips：_detail 有码才可点（data-open-kline 占位），无码显式不可点
// 占位语义：v2 卡在 iframe 里，K 线/AMV 跳转要等 R4 的跨窗口桥（见 HANDOFF）；卡内只给视觉态，
// title 直说「待桥接」（旧页那句「点击看 K 线」在卡内不成立，不能写）。
// kind='label'（服务端判定：解析不到代码）= 类别 chip，不写死可点态，只做视觉弱化。
function chipHtml(name, code, kind) {
  const nm = name == null ? '' : String(name);
  const cat = kind === 'label' ? ' cp-chip-cat' : '';
  if (!code) return '<span class="cp-chip cp-chip-off' + cat + '">' + esc(nm) + '</span>';
  return '<span class="cp-chip cp-chip-link" data-open-kline="' + esc(code) + '"'
    + ' data-open-name="' + esc(nm) + '" title="' + esc(code) + ' · K线跳转待桥接">' + esc(nm) + '</span>';
}

function detailItems(detail, raw) {
  const arr = Array.isArray(detail) ? detail : [];
  if (arr.length) {
    return arr.map((o) => {
      const it = o || {};
      const code = it.code || '';
      return {
        name: it.name,
        code: code,
        kind: it.kind || (code ? 'stock' : 'label'),
        in_group: it.in_group === true,
      };
    });
  }
  // 回退：服务端没给 *_detail 时按 [/、] 切（与后端 _CHAIN_SPLIT 同口径），一律不可点
  return String(raw == null ? '' : raw).split(CHAIN_SPLIT).map((s) => s.trim())
    .filter(Boolean).map((s) => ({ name: s, code: '', kind: 'label', in_group: false }));
}

// 链位 chips：括号内的 token（in_group）包一层虚线框做视觉分组，类别与标的靠 chip 自身区分
function chainChipsHtml(items) {
  let html = '';
  let grp = [];
  const flush = () => {
    if (!grp.length) return;
    html += '<span class="cp-chip-grp">' + grp.join('') + '</span>';
    grp = [];
  };
  for (const o of items) {
    const chip = chipHtml(o.name, o.code, o.kind);
    if (o.in_group) { grp.push(chip); continue; }
    flush();
    html += chip;
  }
  flush();
  return html;
}

// ---------------------------------------------------------------- 卡片渲染（纯字符串，全部 esc 过）
function activeCardHtml(a) {
  const heat = ['hot', 'warm', 'cool'].indexOf(a.heat) >= 0 ? a.heat : 'cool';
  const det = (Array.isArray(a.targets_detail) && a.targets_detail.length)
    ? a.targets_detail
    : (a.targets || []).map((t) => ({ name: t, code: '' }));
  const chips = det.map((t) => chipHtml(t && t.name, (t || {}).code)).join('');
  const secs = (Array.isArray(a.sectors_list) ? a.sectors_list : []).map((s) =>
    '<span class="cp-chip cp-chip-link cp-chip-sec" data-open-amv="' + esc(s.code) + '"'
    + ' data-open-name="' + esc(s.name) + '" title="' + esc(s.code) + ' · 对应板块（'
    + esc(s.src || '—') + '）· AMV 跳转待桥接">▤ ' + esc(s.name) + '</span>').join('');
  const slugAttr = a.slug ? ' data-slug="' + esc(a.slug) + '"' : '';
  return '<div class="cp-c ' + heat + (a.slug ? ' link' : '') + '"' + slugAttr
    + ' data-name="' + esc(a.name) + '" title="' + esc(a.slug ? '点击查看概况' : '无概况页') + '">'
    + '<div class="cp-c-head"><div class="cp-c-name">' + esc(a.name) + '</div>'
    + '<div class="cp-badge ' + heat + '">' + esc(a.stage_short || a.stage || '—') + '</div></div>'
    + '<div class="cp-c-meta"><b>' + esc(a.logic || '—') + '</b> · ' + esc(a.duration || '—') + '</div>'
    + (secs ? '<div class="cp-chips">' + secs + '</div>' : '')
    + '<div class="cp-chips">' + (chips || '<span class="cp-empty-chip">—</span>') + '</div>'
    + '<div class="cp-c-foot"><span>更新 ' + esc(a.updated || '—') + '</span>'
    + '<span class="node">' + esc(a.next_node || '') + '</span></div></div>';
}

function watchCardHtml(w) {
  const kind = w.kind === 'bull' ? 'bull' : (w.kind === 'bear' ? 'bear' : '');
  const kindCn = kind === 'bull' ? '偏多' : (kind === 'bear' ? '偏空' : '无口径');
  const secs = (Array.isArray(w.sectors_list) && w.sectors_list.length)
    ? w.sectors_list : [{ name: w.sectors || '—', code: '' }];
  // 观察池参照板块 → data-open-amv（旧 _cpWatchCard 同口径：点开 AMV 走势，不是 K 线）
  const chips = secs.map((s) => {
    const nm = s.name == null ? '' : String(s.name);
    if (!s.code) return '<span class="cp-chip cp-chip-off">' + esc(nm) + '</span>';
    return '<span class="cp-chip cp-chip-link" data-open-amv="' + esc(s.code) + '"'
      + ' data-open-name="' + esc(nm) + '" title="' + esc(s.code) + ' · AMV 跳转待桥接">' + esc(nm) + '</span>';
  }).join('');
  const dts = (Array.isArray(w.deltas) ? w.deltas : []).map((v) =>
    '<span class="cp-delta ' + (v >= 0 ? 'cp-bull' : 'cp-bear') + '">' + (v >= 0 ? '+' : '−')
    + Math.abs(v).toFixed(5) + '</span>').join('');
  return '<div class="cp-c ' + (kind || '') + '" data-name="' + esc(w.name) + '">'
    + '<div class="cp-c-head"><div class="cp-c-name">' + esc(w.name) + '</div>'
    + '<div class="cp-badge ' + kind + '">' + kindCn + '</div></div>'
    + '<div class="cp-chips">' + chips + '</div>'
    + '<div class="cp-w-q"><span class="cp-w-ql">待验证</span>' + esc(w.question || '—') + '</div>'
    + (dts ? '<div class="cp-w-foot"><span>AMV 权重变化</span>' + dts + '</div>' : '')
    + '</div>';
}

function chainCardHtml(x, st) {
  const step = (label, val, cls, det) => {
    const items = detailItems(det, val);
    const chips = items.length
      ? chainChipsHtml(items)
      : '<span class="cp-empty-chip">—</span>';
    return '<div class="cp-flow-step ' + cls + '"><div class="cp-flow-l">' + label + '</div>'
      + '<div class="cp-chips">' + chips + '</div></div>';
  };
  const s = st && st.get(String(x.name || '').trim());
  return '<div class="cp-c" data-name="' + esc(x.name) + '">'
    + '<div class="cp-c-head"><div class="cp-c-name">' + esc(x.name) + '</div>'
    + (s ? '<div class="cp-badge ' + esc(s.cls) + '" title="概念当前状态">' + esc(s.txt) + '</div>' : '')
    + '</div><div class="cp-flow">'
    + step('上游', x.up, 'up', x.up_detail) + '<div class="cp-flow-arrow">→</div>'
    + step('本环节', x.mid, 'mid', x.mid_detail) + '<div class="cp-flow-arrow">→</div>'
    + step('下游', x.down, 'down', x.down_detail)
    + '</div></div>';
}

function archCardHtml(x) {
  return '<div class="cp-c" data-name="' + esc(x.name) + '">'
    + '<div class="cp-c-head"><div class="cp-c-name">' + esc(x.name) + '</div>'
    + '<div class="cp-badge cool">已归档</div></div>'
    + '<div class="cp-w-q"><span class="cp-w-ql dim">说明</span>' + esc(x.note || '—') + '</div></div>';
}

// 链位卡右上角状态徽标（旧 7690 段 cpStatus 同口径：观察池先入、活跃覆盖）
function chainStatusMap(d) {
  const m = new Map();
  for (const w of (d.watch || [])) {
    m.set(String(w.name || '').trim(), {
      cls: w.kind === 'bull' ? 'bull' : (w.kind === 'bear' ? 'bear' : 'cool'),
      txt: '观察' + (w.kind === 'bull' ? '·偏多' : (w.kind === 'bear' ? '·偏空' : '')),
    });
  }
  for (const a of (d.active || [])) {
    m.set(String(a.name || '').trim(), { cls: a.heat || 'cool', txt: a.stage_short || a.stage || '活跃' });
  }
  return m;
}

// 光谱段：颜色名直接对应 themes.css 的 --{color}-solid / --{color}-on（旧端同口径）
function segHtml(n, color, label) {
  if (!(n > 0)) return '';
  return '<div class="cp-seg" style="flex:' + n + ';background:var(--' + color + '-solid);color:var(--'
    + color + '-on)">' + esc(label) + ' ' + n + '</div>';
}

function viewHtml(d, view) {
  if (view === 'active') {
    const c = d.counts || {};
    const cards = (d.active || []).map(activeCardHtml).join('') || '<div class="cp-empty">暂无活跃概念</div>';
    return '<div class="cp-spec"><div class="cp-spec-t"><span>阶段光谱 · ' + (c.active || 0)
      + ' 个活跃概念</span><span>左=进攻 · 右=退潮</span></div><div class="cp-spec-track">'
      + segHtml(c.hot || 0, 'up', '主升/催化中') + segHtml(c.warm || 0, 'warn', '分歧/兑现')
      + segHtml(c.cool || 0, 'accent', '降温/蓄势') + '</div></div>'
      + '<div class="cp-grid" data-grid="active">' + cards + '</div>';
  }
  if (view === 'watch') {
    const all = d.watch || [];
    const bull = all.filter((w) => w.kind === 'bull').length;
    const bear = all.filter((w) => w.kind === 'bear').length;
    const cards = all.map(watchCardHtml).join('') || '<div class="cp-empty">暂无观察方向</div>';
    return '<div class="cp-spec"><div class="cp-spec-t"><span>观察方向 · ' + all.length
      + ' 个</span><span>左=偏多 · 右=偏空（AMV 口径）</span></div><div class="cp-spec-track">'
      + segHtml(bull, 'up', '偏多') + segHtml(bear, 'down', '偏空') + '</div></div>'
      + '<div class="cp-grid" data-grid="watch">' + cards + '</div>';
  }
  if (view === 'chain') {
    const all = d.chain || [];
    const st = chainStatusMap(d);
    const cards = all.map((x) => chainCardHtml(x, st)).join('') || '<div class="cp-empty">暂无链位映射</div>';
    return '<div class="cp-spec"><div class="cp-spec-t"><span>链位映射 · ' + all.length
      + ' 条</span><span>上游 → 本环节 → 下游</span></div></div>'
      + '<div class="cp-grid" data-grid="chain">' + cards + '</div>';
  }
  const all = d.archived || [];
  const cards = all.map(archCardHtml).join('') || '<div class="cp-empty">暂无归档概念</div>';
  return '<div class="cp-grid" data-grid="archived">' + cards + '</div>';
}

function detailHtml(d, name) {
  const title = (name || d.slug || '') + ' · 概况';
  const body = (d && d.ok) ? (d.html || '') : '';
  return '<div class="cp-detail-hd"><button type="button" class="cp-btn" data-back="1">← 返回列表</button>'
    + '<span class="cp-detail-t">' + esc(title) + '</span>'
    + '<span class="cp-upd">概念概况（服务端 MD 渲染）</span></div>'
    + '<div class="cp-md">' + body + '</div>';
}

// ---------------------------------------------------------------- 卡片
export default {
  cards: [{
    id: 'concept.main',
    title: '概念追踪',
    slot: 'main',
    order: 55,

    async mount(root, ctx) {
      root.textContent = '';
      const api = ctx.api;
      const S = {
        alive: true,
        loading: false,
        view: 'active',
        data: null,
        detail: null,        // {slug, name, html}
        html: Object.create(null),
        el: {},
      };
      this._cp = S;

      const div = (cls, text) => {
        const d = document.createElement('div');
        if (cls) d.className = cls;
        if (text != null) d.textContent = text;
        return d;
      };

      const wrap = div('cp-card-root');
      const head = div('cp-head');
      const tabs = div('cp-tabs');
      const upd = div('cp-upd', '总表更新 —');
      const searchWrap = div('cp-search');
      const input = document.createElement('input');
      input.type = 'search';
      input.placeholder = '筛选概念 / 标的…';
      input.setAttribute('aria-label', '筛选概念或标的');
      searchWrap.append(input);
      const btnReload = document.createElement('button');
      btnReload.type = 'button';
      btnReload.className = 'cp-btn';
      btnReload.textContent = '刷新';
      btnReload.addEventListener('click', () => { load(); });
      head.append(tabs, upd, searchWrap, btnReload);

      const status = div('cp-err');
      status.hidden = true;
      const body = div('cp-body');
      const src = div('cp-src', '数据源：/api/concept.json（总表索引）· /api/concept.json?slug=（概念概况，'
        + '服务端 MD 渲染）。本卡只读，零写盘；个股/板块名只带 data-open-kline / data-open-amv 属性占位，'
        + '点击行为由旧页桥接接管。');

      wrap.append(head, status, body, src);
      root.append(wrap);

      S.el = { head, tabs, upd, searchWrap, input, status, body };

      const setHtml = (el, html, key) => {
        if (!el) return;
        if (S.html[key] === html) return;
        S.html[key] = html;
        el.innerHTML = html;
      };
      const setStatus = (text, isErr) => {
        const el = S.el.status;
        if (!el) return;
        if (!text) { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        el.className = isErr ? 'cp-err' : 'cp-upd';
        el.textContent = text;
      };

      // 视图胶囊（旧 cp-pill：计数 + 名称；active 态由 _cpShowView 切换）
      const renderTabs = () => {
        const c = (S.data && S.data.counts) || {};
        const html = VIEWS.map(([key, label]) =>
          '<button type="button" class="cp-pill' + (S.view === key && !S.detail ? ' active' : '')
          + '" data-cptab="' + key + '" role="tab" aria-selected="'
          + (S.view === key && !S.detail ? 'true' : 'false') + '"><b>'
          + (c[key] || 0) + '</b> ' + label + '</button>').join('');
        setHtml(S.el.tabs, html, 'tabs');
        // 搜索框只在活跃 / 观察池有意义（旧 _cpShowView 同口径）
        S.el.searchWrap.style.display = (S.view === 'active' || S.view === 'watch') && !S.detail ? '' : 'none';
      };

      const applyFilter = () => {
        const grid = S.el.body.querySelector('[data-grid]');
        if (!grid) return;
        // 筛选只对「活跃 / 观察池」生效（旧 _cpShowView：搜索框只在这两视图显示）
        const on = (S.view === 'active' || S.view === 'watch') && !S.detail;
        const q = on ? String(S.el.input.value || '').trim().toLowerCase() : '';
        for (const el of grid.children) {
          const hit = !q || String(el.textContent || '').toLowerCase().indexOf(q) >= 0;
          el.style.display = hit ? '' : 'none';
        }
      };

      const render = () => {
        renderTabs();
        if (S.detail) {
          setHtml(S.el.body, detailHtml(S.detail, S.detail.name), 'detail');
          return;
        }
        const d = S.data || {};
        setHtml(S.el.body, viewHtml(d, S.view), 'view:' + S.view);
        applyFilter();
      };

      const load = async () => {
        if (!S.alive || S.loading) return;
        S.loading = true;
        try {
          const data = await api.get('/api/concept.json');
          if (!S.alive) return;
          if (!data || data.ok === false) {
            S.data = null;
            setStatus('概念数据不可用：' + ((data && data.error) || '未知'), true);
            setHtml(S.el.body, '<div class="cp-empty">概念数据不可用</div>', 'err');
            return;
          }
          S.data = data;
          setStatus('', false);
          S.el.upd.textContent = '总表更新 ' + (data.updated || '—');
          render();
        } catch (e) {
          if (!S.alive) return;
          S.data = null;
          setStatus('概念数据加载失败：' + errText(e), true);
          setHtml(S.el.body, '<div class="cp-empty">概念数据加载失败</div>', 'err');
        } finally {
          S.loading = false;
        }
      };
      S.load = load;

      const openDetail = async (slug, name) => {
        S.detail = { slug, name, ok: false, html: '' };
        setHtml(S.el.body, '<div class="cp-empty">概况加载中…</div>', 'detail-loading');
        renderTabs();
        try {
          const d = await api.get('/api/concept.json?slug=' + encodeURIComponent(slug));
          if (!S.alive) return;
          S.detail = { slug, name, ok: !!(d && d.ok), html: (d && d.html) || '' };
          if (!(d && d.ok)) S.detail.error = (d && d.error) || '未知';
        } catch (e) {
          if (!S.alive) return;
          const st = e && e.status;
          S.detail = {
            slug, name, ok: false,
            html: '<p class="cp-empty">概况' + (st === 404 ? '不可用（该概念无 概况.md）' : '加载失败')
              + '：' + esc(errText(e)) + '</p>',
          };
        }
        if (!S.alive) return;
        S.html = Object.create(null);
        render();
      };

      const backToList = () => {
        S.detail = null;
        S.html = Object.create(null);
        render();
      };

      // 事件委托：视图切换 / 卡开详情 / 返回（卡片随数据重绘，监听挂稳定的 wrap 上）
      // 占位芯片（[data-open-kline]/[data-open-amv]）先拦下：它们由 R4 的跨窗口桥
      // 接管，卡内**不得**当详情入口。旧页靠捕获阶段 stopPropagation 保证「芯片可点、
      // 卡不误触」（index.html:10936）；iframe 内父页捕获够不到，故本卡自己先判一次。
      wrap.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        if (t.closest('[data-open-kline],[data-open-amv]')) return;   // 占位芯片：不进详情
        const back = t.closest('[data-back]');
        if (back) { backToList(); return; }
        const pill = t.closest('[data-cptab]');
        if (pill) {
          S.view = pill.getAttribute('data-cptab') || 'active';
          S.detail = null;
          // 换视图清掉筛选值：搜索框只在活跃/观察池显示，留着旧值会让别的视图卡片被静默藏掉
          S.el.input.value = '';
          S.html = Object.create(null);
          render();
          return;
        }
        const card = t.closest('.cp-c[data-slug]');
        if (card) { openDetail(card.getAttribute('data-slug'), card.getAttribute('data-name')); }
      });

      S.el.input.addEventListener('input', applyFilter);

      await load();
    },

    async unmount() {
      const S = this._cp;
      if (!S) return;
      S.alive = false;
      S.html = Object.create(null);
      this._cp = null;
    },

    // 不声明 refreshEvery（概念总表日频）；保留 refresh() 契约占位 + 卡头手动刷新
    async refresh() {
      const S = this._cp;
      if (!S || !S.alive || S.loading) return;
      await S.load();
    },
  }],
};
