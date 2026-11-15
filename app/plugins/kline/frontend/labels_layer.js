// K 线弹窗标签层（2026-11-04 · 01）
//
// 为什么单独一个文件、还做成「自注册」：
//   · 弹窗本体是**旧版原样挂载**（legacy_modal.js + 旧四模块，旧文件只读不改），
//     标签属于 v2 增强，只能挂在装载层外侧；
//   · 装载层本身常被别的任务动（版本串、列表卡、F10 卡），逻辑全塞进去会互相踩，
//     所以这里只让 legacy_modal.js 加**一行 import**，其余全在本文件自注册。
//
// 交互（主人 2026-11-04 预览确认）：
//   · 主图右键菜单多一项「标签」→ 新建 / 显示开关 / 清空本图；
//   · 标签显示在主图左下角、贴最左边，一票多条、纵向堆叠；
//   · 标签上右键：修改文字 / 样式（8 色 + 填充·描边·实心）/ 删除；双击直接改名；
//   · 按代码存 localStorage（labels.js 纯函数，key=klm_labels_v1），日线/分时共用一组。
//
// 依赖：./labels.js（存储与校验）、弹窗 DOM（#klineModal / #klineChart / #hq_minute / #koMenu）。
// 样式：自己注入 <style id="klab-css">，颜色只走 themes.css 语义 token，不写死 hex（主题切换自动跟随）。

import {
  COLORS as LAB_COLORS, SHAPES as LAB_SHAPES, SHAPE_LABEL, DEFAULT_COLOR as LAB_DEFAULT_COLOR,
  MAX_PER_CODE as LAB_MAX, TEXT_MAX as LAB_TEXT_MAX,
  readCode as readLabels, readOn as readLabelsOn, writeOn as writeLabelsOn,
  addLabel, updateLabel, removeLabel, clearCode as clearLabels,
} from './labels.js?v=0.9.67';

const CSS_ID = 'klab-css';
const STYLE_ID = 'klabStyle';

const CSS = `
#klabLayer{position:fixed;z-index:118;display:flex;flex-direction:column-reverse;align-items:flex-start;
  gap:4px;overflow:hidden;pointer-events:none;font-family:var(--font-ui,sans-serif)}
#klabLayer[hidden]{display:none}
.klab-chip{pointer-events:auto;display:inline-flex;align-items:center;gap:5px;height:20px;padding:0 8px;
  border-radius:4px;font-size:0.7rem;line-height:1;max-width:220px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;cursor:context-menu;user-select:none;box-sizing:border-box}
.klab-chip .klab-dot{width:4px;height:4px;border-radius:50%;background:currentColor;opacity:.95;flex:0 0 auto}
/* 文字色 = 标签色与正文色混合：深色主题提亮、浅色主题压暗，8 个色都读得清 */
.klab-chip.is-fill{background:color-mix(in srgb,var(--c) 14%,transparent);
  border:1px solid color-mix(in srgb,var(--c) 46%,transparent);
  color:color-mix(in srgb,var(--c) 72%,var(--fg))}
.klab-chip.is-fill:hover{background:color-mix(in srgb,var(--c) 26%,transparent);
  border-color:color-mix(in srgb,var(--c) 74%,transparent)}
.klab-chip.is-outline{background:transparent;border:1px dashed color-mix(in srgb,var(--c) 62%,transparent);
  color:color-mix(in srgb,var(--c) 72%,var(--fg))}
.klab-chip.is-outline:hover{background:color-mix(in srgb,var(--c) 16%,transparent);border-style:solid}
.klab-chip.is-solid{background:var(--c);border:1px solid var(--c);color:var(--bg);font-weight:600}
.klab-chip.is-solid .klab-dot{background:var(--bg);opacity:.5}
/* 菜单：旧菜单样式只挂在 #koMenu/#koSub 上，自建菜单要自带定位规则（其余 .ko-mi/.ko-mh/.ko-sep 通用） */
#klabMenu.ko-menu,#klabStyle.ko-menu{position:fixed;z-index:5000;min-width:198px;max-width:300px;padding:4px;
  background:var(--card2,#1a2334);border:1px solid var(--line,#26324a);border-radius:9px;
  box-shadow:0 14px 34px rgba(0,0,0,.5);font-size:0.72rem;color:var(--fg);user-select:none}
#klabMenu[hidden],#klabStyle[hidden]{display:none}
#klabStyle .klab-sws{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:4px 4px 8px}
#klabStyle .klab-sw{height:22px;border-radius:4px;border:1px solid var(--line,#26324a);cursor:pointer;padding:0}
#klabStyle .klab-sw.on{box-shadow:0 0 0 2px var(--card2,#1a2334),0 0 0 3px var(--fg)}
#klabStyle .klab-shapes{display:flex;gap:5px;margin:0 4px 4px}
#klabStyle .klab-shapes button{flex:1 1 auto;height:24px;background:transparent;border:1px solid var(--line,#26324a);
  border-radius:4px;color:var(--dim);font:inherit;font-size:0.68rem;cursor:pointer}
#klabStyle .klab-shapes button:hover{background:var(--card,#141b28);color:var(--fg)}
#klabStyle .klab-shapes button.on{border-color:var(--accent);color:var(--accent)}
/* 新建/修改浮层 */
.klab-scrim{position:fixed;inset:0;z-index:5200;background:rgba(5,8,15,.45);display:flex;
  align-items:center;justify-content:center;font-family:var(--font-ui,sans-serif)}
.klab-dlg{width:298px;background:var(--card2,#1a2334);border:1px solid var(--line,#26324a);border-radius:10px;
  box-shadow:0 20px 50px rgba(0,0,0,.5);padding:13px;color:var(--fg);font-size:0.75rem}
.klab-dlg-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:700}
.klab-dlg-h span{font-size:0.68rem;color:var(--dim);font-weight:400}
.klab-dlg-in{width:100%;height:30px;padding:0 9px;background:var(--bg,#101720);border:1px solid var(--line,#26324a);
  border-radius:5px;color:var(--fg);font:inherit;font-size:0.75rem;outline:none;box-sizing:border-box}
.klab-dlg-in:focus{border-color:var(--accent)}
.klab-dlg-row{display:flex;align-items:center;gap:8px;margin-top:11px}
.klab-dlg-row .k{font-size:0.68rem;color:var(--dim);flex:0 0 26px}
.klab-dlg-row .klab-sws{display:grid;grid-template-columns:repeat(8,1fr);gap:5px;flex:1 1 auto}
.klab-dlg-row .klab-sw{height:22px;border-radius:4px;border:1px solid var(--line,#26324a);cursor:pointer;padding:0}
.klab-dlg-row .klab-sw.on{box-shadow:0 0 0 2px var(--card2,#1a2334),0 0 0 3px var(--fg)}
.klab-dlg-row .klab-shapes{display:flex;gap:5px;flex:1 1 auto}
.klab-dlg-row .klab-shapes button{flex:1 1 auto;height:24px;background:transparent;border:1px solid var(--line,#26324a);
  border-radius:4px;color:var(--dim);font:inherit;font-size:0.68rem;cursor:pointer}
.klab-dlg-row .klab-shapes button.on{border-color:var(--accent);color:var(--accent)}
.klab-dlg-pv{display:flex;align-items:center;gap:8px;margin-top:12px;padding-top:10px;border-top:1px solid var(--line,#26324a)}
.klab-dlg-pv .k{font-size:0.68rem;color:var(--dim)}
.klab-dlg-ft{display:flex;justify-content:flex-end;gap:7px;margin-top:13px}
.klab-dlg-ft button{font:inherit;font-size:0.72rem;padding:5px 13px;border-radius:5px;cursor:pointer}
.klab-dlg-ft .c{background:transparent;border:1px solid var(--line,#26324a);color:var(--dim)}
.klab-dlg-ft .c:hover{color:var(--fg);background:var(--card,#141b28)}
.klab-dlg-ft .o{background:var(--accent);border:1px solid var(--accent);color:var(--accent-on,#04121f);font-weight:700}
.klab-dlg-tip{margin-top:9px;font-size:0.64rem;color:var(--dim);opacity:.85;line-height:1.5}
`;

let _layer = null;
let _menu = null;
let _styleEl = null;
let _dlg = null;
let _timer = 0;
let _lastList = [];
let _lastOn = true;
let _bound = false;

// ---------------------------------------------------------------- 小工具

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/** 当前票：旧弹窗把「名称 代码」写在 #klineTitle 里（切票由旧模块重写这一行）。 */
function curCode() {
  const t = document.getElementById('klineTitle');
  const m = t && /([0-9]{6}\.(?:SH|SZ|BJ|TI))/i.exec(t.textContent || '');
  return m ? m[1].toUpperCase() : '';
}

/** 当前周期档：直接读周期按钮上的 on（不信内部状态，切换时它最先变）。 */
function curPeriod() {
  const on = document.querySelector('#kPerGroup button.on');
  return on && on.dataset.period ? on.dataset.period : 'd';
}

function modalOpen() {
  const m = document.getElementById('klineModal');
  return !!m && m.style.display === 'flex';
}

function ensureCss() {
  if (document.getElementById(CSS_ID)) return;
  const s = document.createElement('style');
  s.id = CSS_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

function store() {
  return null;   // labels.js 自己判 localStorage 可用性，不可用时退内存
}

// ---------------------------------------------------------------- 标签层

function ensureLayer() {
  const box = document.querySelector('#klineModal .kbox');
  if (!box) return null;
  if (!_layer || !document.body.contains(_layer)) {
    _layer = document.createElement('div');
    _layer.id = 'klabLayer';
    _layer.className = 'klab-layer';
    _layer.hidden = true;
    // 挂 body 而不是 .kbox：弹窗是 transform 居中的，position:fixed 子元素会以弹窗为包含块，
    // 坐标就跟着偏移（实测 rect 与 style 差 30+px）。挂 body 才是真正的视口坐标。
    document.body.appendChild(_layer);
    _layer.addEventListener('contextmenu', onChipContext);
    _layer.addEventListener('dblclick', onChipDbl);
  }
  return _layer;
}

/** 主图 pane 高度（LC 多 pane；分时走 HQChart，不走这里）。拿不到返回 0 → 退回整图。 */
function mainPanePx() {
  try {
    const lc = window.KLineApp && window.KLineApp.__lc ? window.KLineApp.__lc() : null;
    const panes = lc && lc.chart ? lc.chart.panes() : null;
    if (panes && panes.length) return panes[0].getHeight();
  } catch (e) { /* 拿不到就走整图 */ }
  return 0;
}

/** 定位：主图左下角。日线贴最左（4px）；分时左侧有涨跌幅轴，整列右移到 60px。 */
function layout() {
  if (!_layer || _layer.hidden || !modalOpen()) return;
  const p = curPeriod();
  const chart = document.getElementById(p === 't' ? 'hq_minute' : 'klineChart');
  if (!chart) return;
  const r = chart.getBoundingClientRect();
  if (r.width < 60 || r.height < 60) return;
  let bottomY = r.bottom;
  if (p !== 't') {
    const h = mainPanePx();
    if (h > 40) bottomY = Math.min(r.bottom, r.top + h);
  } else {
    // 分时：HQChart 把主图 + 成交量 + MACD + KDJ 画在同一个 #hq_minute 里，没有 pane 可问。
    // 按旧页固定布局，主图（价格轴 + 均价线）约占容器高度 55%；比例变了改这一个数即可。
    bottomY = r.top + r.height * 0.55;
  }
  _layer.style.left = Math.round(r.left + (p === 't' ? 60 : 4)) + 'px';
  _layer.style.bottom = Math.round(Math.max(6, window.innerHeight - bottomY + 22)) + 'px';
  _layer.style.maxWidth = Math.max(140, Math.round(r.width - (p === 't' ? 170 : 110))) + 'px';
  _layer.style.maxHeight = Math.round(Math.max(60, bottomY - r.top - 36)) + 'px';
}

function renderLabels() {
  const layer = ensureLayer();
  if (!layer) return;
  if (!modalOpen()) { layer.hidden = true; return; }
  const code = curCode();
  if (!code) { layer.hidden = true; return; }
  _lastList = readLabels(store(), code);
  _lastOn = readLabelsOn(store());
  layer.innerHTML = '';
  layer.hidden = !_lastOn || !_lastList.length;
  if (layer.hidden) return;
  _lastList.forEach(function (lb, i) {
    const n = document.createElement('span');
    n.className = 'klab-chip is-' + lb.shape;
    n.style.setProperty('--c', lb.color);
    n.dataset.idx = String(i);
    n.title = lb.text + '（右键修改）';
    const dot = document.createElement('i');
    dot.className = 'klab-dot';
    n.appendChild(dot);
    n.appendChild(document.createTextNode(lb.text));
    layer.appendChild(n);
  });
  layout();
}

/** 合并刷新：开窗/切票/切周期/窗口缩放都走这里，一次 rAF 只画一遍。 */
function schedule() {
  if (_timer) cancelAnimationFrame(_timer);
  _timer = requestAnimationFrame(function () {
    _timer = 0;
    renderLabels();
  });
}

// ---------------------------------------------------------------- 主图右键菜单注入

/** 旧菜单（#koMenu）由 kline_order.js 每次右键重写 innerHTML；这里观察变化后补一项。 */
function injectMenu(m) {
  if (!m || m.hidden) return;
  if (!m.querySelector('[data-act="refresh"]')) return;       // 子菜单/别的浮层不插
  if (m.querySelector('[data-act="klab"]')) return;
  const refresh = m.querySelector('[data-act="refresh"]');
  const n = readLabels(store(), curCode()).length;
  const sep = document.createElement('div');
  sep.className = 'ko-sep';
  const it = document.createElement('div');
  it.className = 'ko-mi';
  it.dataset.act = 'klab';
  it.innerHTML = '<span>标签</span><i>' + (n ? String(n) : '＋') + '</i>';
  m.insertBefore(sep, refresh);
  m.insertBefore(it, refresh);
}

function watchKoMenu() {
  const hook = function (m) {
    if (!m || m.__klabHooked) return;
    m.__klabHooked = 1;
    new MutationObserver(function () { injectMenu(m); })
      .observe(m, { childList: true, attributes: true, attributeFilter: ['hidden'] });
    injectMenu(m);
  };
  const exist = document.getElementById('koMenu');
  if (exist) hook(exist);
  new MutationObserver(function () {
    const m = document.getElementById('koMenu');
    if (m) hook(m);
  }).observe(document.body, { childList: true });
}

// ---------------------------------------------------------------- 菜单（标签入口 / 标签右键 / 样式）

function ensureMenu(which) {
  const id = which === 'style' ? STYLE_ID : 'klabMenu';
  let el = document.getElementById(id);
  if (el) return el;
  el = document.createElement('div');
  el.id = id;
  el.className = 'ko-menu';
  el.hidden = true;
  document.body.appendChild(el);
  el.addEventListener('click', onMenuClick);
  return el;
}

function place(el, x, y) {
  el.style.left = '0px';
  el.style.top = '0px';
  const r = el.getBoundingClientRect();
  let left = x;
  let top = y;
  if (left + r.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - r.width - 8);
  if (top + r.height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - r.height - 8);
  el.style.left = left + 'px';
  el.style.top = top + 'px';
}

function hideMenus() {
  if (_menu) _menu.hidden = true;
  if (_styleEl) _styleEl.hidden = true;
}

function openMainMenu(x, y) {
  const m = ensureMenu('main');
  _menu = m;
  const code = curCode();
  const n = readLabels(store(), code).length;
  const on = readLabelsOn(store());
  m.innerHTML = '<div class="ko-mh">标签 · <b>' + esc(code) + '</b></div>'
    + '<div class="ko-mi" data-act="klab_new"><span>新建标签…</span><i>＋</i></div>'
    + '<div class="ko-mi" data-act="klab_show"><span>显示标签</span><i>' + (on ? '✓' : '') + '</i></div>'
    + '<div class="ko-sep"></div>'
    + '<div class="ko-mi' + (n ? '' : ' disabled') + '" data-act="klab_clear"><span>清空本图标签</span><i>'
    + (n || '') + '</i></div>';
  m.hidden = false;
  place(m, x, y);
  if (_styleEl) _styleEl.hidden = true;
}

function openChipMenu(idx, x, y) {
  const lb = _lastList[idx];
  if (!lb) return;
  const m = ensureMenu('main');
  _menu = m;
  m.innerHTML = '<div class="ko-mh">标签 · <b>' + esc(lb.text) + '</b></div>'
    + '<div class="ko-mi" data-act="klab_edit" data-idx="' + idx + '"><span>修改文字…</span><i>双击</i></div>'
    + '<div class="ko-mi" data-act="klab_style" data-idx="' + idx + '"><span>样式</span><i>▸</i></div>'
    + '<div class="ko-sep"></div>'
    + '<div class="ko-mi" data-act="klab_del" data-idx="' + idx + '" style="color:var(--danger-ink)">'
    + '<span>删除标签</span><i></i></div>';
  m.hidden = false;
  place(m, x, y);
  if (_styleEl) _styleEl.hidden = true;
}

function openStyleMenu(anchor, idx) {
  const lb = _lastList[idx];
  if (!lb) return;
  const s = ensureMenu('style');
  _styleEl = s;
  s.dataset.idx = String(idx);
  s.innerHTML = '<div class="ko-mh">颜色</div><div class="klab-sws"></div>'
    + '<div class="ko-mh">形态</div><div class="klab-shapes"></div>';
  const sws = s.querySelector('.klab-sws');
  LAB_COLORS.forEach(function (c) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'klab-sw' + (c === lb.color ? ' on' : '');
    b.style.background = c;
    b.dataset.color = c;
    sws.appendChild(b);
  });
  const shp = s.querySelector('.klab-shapes');
  LAB_SHAPES.forEach(function (k) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = (k === lb.shape ? 'on' : '');
    b.dataset.shape = k;
    b.textContent = SHAPE_LABEL[k] || k;
    shp.appendChild(b);
  });
  s.hidden = false;
  const r = anchor.getBoundingClientRect();
  place(s, r.right - 4, r.top);
}

function onMenuClick(ev) {
  // ① 样式面板（色板/形态）：它没有 data-act，先单独处理
  const styleEl = document.getElementById(STYLE_ID);
  if (styleEl && !styleEl.hidden && styleEl.contains(ev.target)) {
    ev.preventDefault();
    ev.stopPropagation();
    const target = Number(styleEl.dataset.idx);
    const sw = ev.target.closest('.klab-sw');
    if (sw) {
      updateLabel(store(), curCode(), target, { color: sw.dataset.color });
      styleEl.querySelectorAll('.klab-sw').forEach(function (x) { x.classList.toggle('on', x === sw); });
      renderLabels();
      return;
    }
    const sb = ev.target.closest('.klab-shapes button');
    if (sb) {
      updateLabel(store(), curCode(), target, { shape: sb.dataset.shape });
      styleEl.querySelectorAll('.klab-shapes button').forEach(function (x) { x.classList.toggle('on', x === sb); });
      renderLabels();
    }
    return;
  }

  const it = ev.target.closest ? ev.target.closest('#klabMenu [data-act]') : null;
  if (!it) return;
  ev.preventDefault();
  ev.stopPropagation();
  const act = it.dataset.act;
  const idx = it.dataset.idx == null ? null : Number(it.dataset.idx);

  if (act === 'klab_new') { hideMenus(); openEditor(null); return; }
  if (act === 'klab_show') {
    const on = !readLabelsOn(store());
    writeLabelsOn(store(), on);
    hideMenus();
    renderLabels();
    return;
  }
  if (act === 'klab_clear') {
    if (!readLabels(store(), curCode()).length) return;
    clearLabels(store(), curCode());
    hideMenus();
    renderLabels();
    return;
  }
  if (act === 'klab_edit') { hideMenus(); openEditor(idx); return; }
  if (act === 'klab_del') {
    removeLabel(store(), curCode(), idx);
    hideMenus();
    renderLabels();
    return;
  }
  if (act === 'klab_style') { openStyleMenu(it, idx); }
}

// ---------------------------------------------------------------- 标签上右键 / 双击

function onChipContext(ev) {
  const chip = ev.target.closest ? ev.target.closest('.klab-chip') : null;
  if (!chip) return;
  ev.preventDefault();
  ev.stopPropagation();
  hideMenus();
  openChipMenu(Number(chip.dataset.idx), ev.clientX, ev.clientY);
}

function onChipDbl(ev) {
  const chip = ev.target.closest ? ev.target.closest('.klab-chip') : null;
  if (!chip) return;
  ev.preventDefault();
  ev.stopPropagation();
  hideMenus();
  openEditor(Number(chip.dataset.idx));
}

// ---------------------------------------------------------------- 新建 / 修改浮层

function closeEditor() {
  if (_dlg) { _dlg.remove(); _dlg = null; }
}

function openEditor(idx) {
  closeEditor();
  const editing = (idx === null || idx === undefined) ? null : _lastList[idx];
  if (idx !== null && idx !== undefined && !editing) return;
  let color = editing ? editing.color : LAB_DEFAULT_COLOR;
  let shape = editing ? editing.shape : 'fill';

  const wrap = document.createElement('div');
  wrap.className = 'klab-scrim';
  wrap.innerHTML = '<div class="klab-dlg" role="dialog">'
    + '<div class="klab-dlg-h">' + (editing ? '修改标签' : '新建标签')
    + '<span>' + esc(curCode()) + ' · ' + esc(curPeriod()) + '</span></div>'
    + '<input class="klab-dlg-in" maxlength="' + LAB_TEXT_MAX + '" placeholder="写点什么，例如：竞价异动 高开3.2%">'
    + '<div class="klab-dlg-row"><span class="k">颜色</span><div class="klab-sws"></div></div>'
    + '<div class="klab-dlg-row"><span class="k">形态</span><div class="klab-shapes"></div></div>'
    + '<div class="klab-dlg-pv"><span class="k">预览</span><span class="klab-chip"></span></div>'
    + '<div class="klab-dlg-ft"><button type="button" class="c">取消</button>'
    + '<button type="button" class="o">' + (editing ? '保存' : '添加') + '</button></div>'
    + '<div class="klab-dlg-tip">Enter 保存 · Esc 取消 · 最多 ' + LAB_MAX + ' 条，按代码存（日线/分时共用）</div>'
    + '</div>';
  document.body.appendChild(wrap);
  _dlg = wrap;

  const input = wrap.querySelector('.klab-dlg-in');
  const pv = wrap.querySelector('.klab-dlg-pv .klab-chip');
  const sws = wrap.querySelector('.klab-sws');
  const shp = wrap.querySelector('.klab-shapes');
  input.value = editing ? editing.text : '';

  LAB_COLORS.forEach(function (c) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'klab-sw' + (c === color ? ' on' : '');
    b.style.background = c;
    b.dataset.color = c;
    sws.appendChild(b);
  });
  LAB_SHAPES.forEach(function (k) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = (k === shape ? 'on' : '');
    b.dataset.shape = k;
    b.textContent = SHAPE_LABEL[k] || k;
    shp.appendChild(b);
  });

  function paint() {
    pv.className = 'klab-chip is-' + shape;
    pv.style.setProperty('--c', color);
    pv.textContent = '';
    const dot = document.createElement('i');
    dot.className = 'klab-dot';
    pv.appendChild(dot);
    pv.appendChild(document.createTextNode(input.value.trim() || '标签文字'));
  }
  paint();
  input.focus();
  input.select();

  wrap.addEventListener('click', function (ev) {
    if (ev.target === wrap) { closeEditor(); return; }
    const sw = ev.target.closest('.klab-sw');
    if (sw) {
      color = sw.dataset.color;
      sws.querySelectorAll('.klab-sw').forEach(function (x) { x.classList.toggle('on', x === sw); });
      paint();
      return;
    }
    const sb = ev.target.closest('.klab-shapes button');
    if (sb) {
      shape = sb.dataset.shape;
      shp.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === sb); });
      paint();
      return;
    }
    if (ev.target.closest('.c')) { closeEditor(); return; }
    if (ev.target.closest('.o')) commit();
  });

  input.addEventListener('input', paint);
  input.addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); commit(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closeEditor(); }
  });

  function commit() {
    const res = editing
      ? updateLabel(store(), curCode(), idx, { text: input.value, color: color, shape: shape })
      : addLabel(store(), curCode(), { text: input.value, color: color, shape: shape });
    if (!res.ok) {
      // 失败就留在浮层里，用输入框下方的提示位说明原因（不关窗、不静默）
      let tip = wrap.querySelector('.klab-dlg-tip');
      if (tip) { tip.textContent = res.reason; tip.style.color = 'var(--danger-ink)'; tip.style.opacity = '1'; }
      input.focus();
      return;
    }
    closeEditor();
    renderLabels();
  }
}

// ---------------------------------------------------------------- 事件绑定

function bindDoc() {
  if (_bound) return;
  _bound = true;

  // Esc：本模块在 legacy_modal.js 顶部求值，监听**早于**装载层的 bindStack 注册，所以能先处理。
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    if (_dlg) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      closeEditor();
      return;
    }
    if ((_menu && !_menu.hidden) || (_styleEl && !_styleEl.hidden)) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      hideMenus();
    }
  }, true);

  // 点空白：关自己的菜单；「标签」入口的点击由这里接管（旧模块不认识 data-act=klab）
  document.addEventListener('click', function (ev) {
    const own = (ev.target.closest && ev.target.closest('#klabMenu, #klabStyle'));
    if (!own) hideMenus();
    const it = ev.target.closest ? ev.target.closest('#koMenu .ko-mi[data-act="klab"]') : null;
    if (!it) return;
    ev.preventDefault();
    ev.stopPropagation();
    const ko = document.getElementById('koMenu');
    const x = ko ? parseFloat(ko.style.left) || ev.clientX : ev.clientX;
    const y = ko ? parseFloat(ko.style.top) || ev.clientY : ev.clientY;
    if (ko) ko.hidden = true;
    openMainMenu(x, y);
  }, true);

  window.addEventListener('resize', schedule);

  // 切周期：按钮点击后周期重画，标签层跟着换位置
  document.addEventListener('click', function (ev) {
    if (!ev.target.closest || !ev.target.closest('#kPerGroup button')) return;
    setTimeout(schedule, 60);
    setTimeout(schedule, 500);
  }, true);
}

/** 弹窗 DOM 到位后再绑：它是 fetch 回来的，不保证在模块求值时已存在。 */
function bindModal() {
  const modal = document.getElementById('klineModal');
  if (!modal || modal.dataset.klabBound === '1') return;
  modal.dataset.klabBound = '1';
  new MutationObserver(function () { schedule(); setTimeout(schedule, 400); })
    .observe(modal, { attributes: true, attributeFilter: ['style'] });
  const title = document.getElementById('klineTitle');
  if (title) {
    new MutationObserver(function () { schedule(); setTimeout(schedule, 300); })
      .observe(title, { childList: true, characterData: true, subtree: true });
  }
}

// ---------------------------------------------------------------- 启动

function boot() {
  ensureCss();
  watchKoMenu();
  bindDoc();
  ensureLayer();
  bindModal();
  schedule();
}

// node 里（纯函数单测会顺着 import 链加载本文件）没有 DOM：所有副作用必须过这道闸，
// 否则 `new MutationObserver(...)` 在模块求值时就报 ReferenceError，把无关的单测一起拖下水。
const HAS_DOM = typeof window !== 'undefined' && typeof document !== 'undefined'
  && typeof MutationObserver === 'function';

if (HAS_DOM) {
  // 2026-11-05（标注：重启后标签要留着）：kline_lc 从服务端拿回 labels 写进 localStorage 后，
  //   需要本层重画一次才会显形。
  window.__KLINE_LABELS_RELOAD = function () { try { renderLabels(); } catch (e) { /* 未就绪忽略 */ } };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }

  if (!document.getElementById('klineModal')) {
    const mo = new MutationObserver(function () {
      if (!document.getElementById('klineModal')) return;
      mo.disconnect();
      bindModal();
      ensureLayer();
      schedule();
    });
    mo.observe(document.body, { childList: true });
  }
}

export default { render: renderLabels, layout: layout };
