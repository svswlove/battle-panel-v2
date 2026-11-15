// v2_note.js — 面板 v2 壳的**元素级**页面标注（2026-10-26）
//
// 为什么有这个文件（旧实现的结构性缺陷 · 原文已迁入本处，见下方「旧文件头留档」）：
//   标注逻辑原先只挂在父文档里时，父文档的 elementsFromPoint 只能看到 <iframe> 整块 ——
//   跨文档事件不冒泡、跨文档也查不到内层节点，于是「一点就框住整张大卡」。
//   结论是：标注必须**在目标元素所在的文档里**跑。本文件就是 v2 壳自己的那一份：
//   在 /v2/ 文档上下文里取元素、在本上下文里生成 selector、并本页直连写盘。
//
// 【旧文件头留档 · 2026-10-30】
//   原文件 `夏天-activity/工具/dashboard/web/ui_note.js`（8002 工作台内嵌标注）随 8002 下线
//   于 2026-10-30 进回收站。它的文件头记录了本节全部设计理由，现逐字留存在此，避免理由丢失：
//
//   > 职责：在 8002 **自己的文档**里点选元素写标注；数据读写经 postMessage 交给作战面板（8800）
//   >      代理，最终落 engine/state/ui_notes.json（唯一源，仍由 8800 写，本文件不直连任何 API）。
//   >
//   > 设计初心（2026-10-21 主人报「点标记只能选一大块元素」）：
//   >   标注脚本原先只挂在 8800 父页，而板块页/板块详情卡都是 8002 的 iframe ⇒ 跨文档事件不冒泡，
//   >   父页拿到的 target 永远只是 <iframe> 整块，elementsFromPoint 也只在父文档里找，必然选不到内层。
//   >   本文件把标注逻辑搬进 iframe 文档内部：命中粒度由本文档的 elementsFromPoint 决定，
//   >   并给「上一层 / 最内层」两个按钮让主人自己定粒度（默认最内层，不再一上来就框整张卡）。
//   >
//   > 数据源与口径：无自有数据。标注存 state/ui_notes.json；字段 page 以 'sector:' 前缀标记
//   >   「本文档内标注」，父页据此跳过、不在父文档按父 selector 误配（selector 只在本文档上下文有效）。
//   >   本文件不写任何 state，只发消息。
//   >
//   > 上下游：入口 = 8800 面板 postMessage 'uiNote:setMode' / 'uiNote:list'；
//   >   出口 = 本文档 'uiNote:hello'（就绪握手）/ 'uiNote:add'（提交标注）；
//   >   回执 = 8800 'uiNote:added' {ok,error}；独立打开 8002（window.parent === window）时不激活。
//
// 与旧实现（`夏天-activity/工具/dashboard/web/ui_note.js`，已销毁）的差异（只列会影响接口/口径的）：
//   1. **写盘路径**：旧实现把 payload postMessage 给父页（8800）代理写 /api/ui_notes/add；
//      本页同源同进程，直接 fetch('/api/ui_notes/add')。少一跳，也少一个会丢消息的时序。
//   2. **入口常驻**：旧实现 window.parent === window 时直接 return（独立打开零侵入）；
//      本壳本身就是要被标注的对象，独立打开也必须能标 —— 故入口（rail 按钮）常驻。
//   3. **父页握手只保留接收侧**：仅当被旧页嵌进 iframe 时才听 uiNote:setMode / uiNote:list，
//      且提交**仍走本页直连**（任务书要求：不走父页代理）。
//   4. 草稿键带页面作用域（旧实现只有 selector，多张卡同 selector 会互相覆盖）。
//   5. 选中粒度不预设「行级」启发式（旧父页 pickTarget 有 140px/92% 宽的经验规则）；
//      这里照 ui_note.js —— 默认最内层 + 两个按钮自己定粒度。
//
// 字段口径（与 app/plugins/ops/backend.py::UiNoteBody 逐字一致，缺字段=默认空串/0）：
//   selector / text / page / elem / html / style / x / y
//   · html 由 ops 截断 800、style 截断 400（HTML_MAX/STYLE_MAX），本文件先在源头收一下，
//     避免整段 outerHTML 白白过网；
//   · page 用 v2: 前缀标记「v2 文档内标注」，旧页据此与自己的 page=hash 区分；
//   · x/y 是**视口坐标**（元素 getBoundingClientRect 左上角），与旧实现同口径。
//
// 事件纪律（与壳的其他组件共存）：
//   · 标注模式开启时，click/dblclick 在 **capture 阶段**拦下并 preventDefault +
//     stopPropagation + stopImmediatePropagation，卡片自己的点击处理器不会跟着跑；
//   · 壳层 UI（rail / drawer / 搜索面板 / 右键菜单 / 本模块自己的节点）一律**放行**，
//     否则「再点一次按钮退出模式」就点不动；
//   · 壳的右键菜单（ctx-menu）走冒泡，本模块的右键退出也走 capture —— 模式开着时右键
//     等于退出（与旧页 8800 一致），模式关着时不装任何拦截，右键菜单行为零变化。
//
// 依赖：无（不 import 任何模块、不读全局壳状态）。壳侧只在 shell.js 里 init 一次。
// 样式：全部在 /static/shell.css 的「标注模式」段（颜色只走 themes.css 语义 token）；
//       本文件只建节点、只改 left/top/width/height，不写任何色值。
// 出口：window.__v2Note（供 shell.js 用，避免动态 import 的加载时序问题）+ ES 默认导出；
//       document 上派发 v2:note-mode {on} / v2:note-saved {id,open} / v2:note-error {message}。

'use strict';

const API_ADD = '/api/ui_notes/add';
const API_UPDATE = '/api/ui_notes/update';
const API_LIST = '/api/ui_notes/list';

const PAGE_PREFIX = 'v2:';          // 与旧页 page=location.hash 区分（旧页靠前缀跳过 v2 标注）
const OWN_ATTR = 'data-un';         // 自家 UI 标记：选取时跳过、事件放行、pin 不被框住
const DRAFT_KEY = 'v2_note_draft_v1';

const MSG_SET_MODE = 'uiNote:setMode';
const MSG_LIST = 'uiNote:list';
const MSG_HELLO = 'uiNote:hello';

const HTML_MAX = 700;               // ops 侧再截到 800；这里先收口
const STYLE_MAX = 400;              // ops 侧再截到 400（同值）
const PATH_DEPTH = 6;               // selector 链最多 6 层，遇 id 提前截断（旧实现同口径）

// 壳层 UI：这些范围内不拦截事件、不高亮、不参与选取。
// 为什么按容器而不是按类名白名单：rail 的按钮是壳自己的开关，drawer/scrim 是壳的遮罩层，
// 标注模式下仍要能点（关抽屉、退模式）；框住它们既无意义又会把「点 ✕」吃掉。
const SHELL_UI = '[data-un], .rail, .drawer, .scrim, .s-panel, .ctx-menu, .toast';

const st = {
  on: false,
  inited: false,
  btn: null,
  fab: false,         // true = 用的是浮动按钮（?card= 单卡视图下 rail 被隐藏）
  fabBtn: null,       // 浮动按钮节点（懒建、复用，避免重复叠按钮）
  hud: null,
  hi: null,
  tip: null,
  box: null,
  cur: null,          // 当前目标元素（新建时随粒度按钮变化）
  deepest: null,      // 本次点击最初命中的最内层元素
  editing: null,      // 正在二次修改的标注（null = 新建）
  notes: [],          // 本页（pageKey() 相同）的未解决标注，用于画 pin
  onMode: null,       // 模式变化回调（壳侧同步按钮态/光标）
  cardKey: null,      // 壳注入：返回当前挂载卡的 key（决定 page 字段与 pin 归属）
  boundBtn: null,     // 已挂过 click 的按钮节点（init 可重复调用，按钮会被重建）
  givenBtn: null,     // 旧字段（壳曾传 rail 按钮）；2026-10-28 起不再使用，保留避免外部调用报错
  noClickUntil: 0,    // 拖动结束后短暂吞掉 click（浏览器拖完还会派一次）
};

// ---------------------------------------------------------------- 小工具

function mk(tag, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  node.setAttribute(OWN_ATTR, '1');
  return node;
}

function isOwn(node) {
  return !!(node && node.closest && node.closest('[' + OWN_ATTR + ']'));
}

function isShellUi(node) {
  return !!(node && node.closest && node.closest(SHELL_UI));
}

function styleOf(node) {
  const raw = node.getAttribute ? (node.getAttribute('class') || '') : (node.className || '');
  return String(raw).split(/\s+/).filter(Boolean).slice(0, 2).join('.');
}

// 短描述：tag#id.cls1.cls2（与旧父页 describe / ui_note.js describe 同口径，便于对照）
function describe(node) {
  if (!node) return '';
  const cls = styleOf(node);
  return node.tagName.toLowerCase() + (node.id ? '#' + node.id : '') + (cls ? '.' + cls : '');
}

// 完整路径：根 → 该元素，tag[.cls]:nth-child(n) 链，最多 PATH_DEPTH 层，遇 id 提前截断。
// **必须在 v2 文档上下文里生成**：旧页存下来的 selector 在本文档里无效（这正是本文件存在的理由）。
function path(node) {
  const parts = [];
  let n = node;
  while (n && n.nodeType === 1 && parts.length < PATH_DEPTH) {
    let s = n.tagName.toLowerCase();
    if (n.id) { s += '#' + n.id; parts.unshift(s); break; }
    const cls = styleOf(n);
    if (cls) s += '.' + cls;
    const parent = n.parentElement;
    let idx = 0;
    if (parent) {
      for (let i = 0; i < parent.children.length; i++) {
        if (parent.children[i] === n) { idx = i; break; }
      }
    }
    s += ':nth-child(' + (idx + 1) + ')';
    parts.unshift(s);
    n = parent;
  }
  return parts.join(' > ');
}

// ---------------------------------------------------------------- 稳定 selector（2026-11-06 P2）
//
// 为什么加：主人 2026-11-06 报「标注看不见」。实测盘中卡 4 条待处理标注，3 条的 selector
//   在页面上已经查不到 —— 老 path() 是「第几个孩子的第几个孩子」全链，卡片 DOM 顺序一变整条断；
//   断了以后 renderPins 直接跳过，页面上什么都不显示，只剩清单里一条死记录。
// 口径：生成时**优先唯一性**而不是结构位置 —— 从元素往上逐层加，第一次「全页唯一」就停；
//   层数到顶仍不唯一才退回老 path()（nth-child 全链）。存盘的 selector 因此通常很短
//   （如 `.it-sec-hd > span.it-sec-n`），少受中间层增删影响。
// 兼容：老标注的 selector 原样保留，读取时由 anchorCandidates() 逐段退化兜底。
function cssEsc(name) {
  return String(name || '').replace(/[^a-zA-Z0-9_-]/g, function (ch) { return '\\' + ch; });
}

function qsaCount(sel) {
  try { return document.querySelectorAll(sel).length; } catch (err) { return -1; }
}

function uniqueSel(el) {
  if (!el || el.nodeType !== 1) return '';
  if (el.id) {
    const byId = '#' + cssEsc(el.id);
    if (qsaCount(byId) === 1) return byId;
  }
  const chain = [];
  let n = el;
  for (let depth = 0; n && n.nodeType === 1 && depth < PATH_DEPTH; depth++) {
    chain.unshift(segOf(n));
    const sel = chain.join(' > ');
    if (qsaCount(sel) === 1) return sel;        // 唯一就收工：链越短越抗结构变化
    if (n.id) {
      const rest = chain.slice();
      rest[0] = '#' + cssEsc(n.id);
      const anchored = rest.join(' > ');
      if (qsaCount(anchored) === 1) return anchored;
      break;                                     // 有 id 还套不住，再往上也只是更松
    }
    n = n.parentElement;
  }
  return path(el);                               // 兜底：老的全路径（可能与旧数据同形）
}

// 单段选择器：tag.class1.class2 + nth-child(n)。
// 为什么每段仍带 nth-child：同一父下有一批同 tag 同 class 的兄弟（如仪表盘三盏 gate-lamp）
//   时，只有位置能区分它们；而：nth-of-type 在混 tag 兄弟里不按 class 计数，会错位。
// 真正抗抖的是**段数**：外层插一个 div 只影响含它的那一段，不含它的短链不变。
function segOf(n) {
  const tag = n.tagName.toLowerCase();
  const cls = styleOf(n);
  let seg = tag + (cls ? '.' + cls.split('.').map(cssEsc).join('.') : '');
  const parent = n.parentElement;
  if (parent) {
    let idx = 0;
    for (let i = 0; i < parent.children.length; i++) {
      if (parent.children[i] === n) { idx = i + 1; break; }
    }
    if (idx) seg += ':nth-child(' + idx + ')';
  }
  return seg;
}

// 读取兜底：完整 selector → 去掉 :nth-child → 逐段去掉最外层（含去 nth 版）。
// 为什么要去 nth-child：老 selector 是「第几个孩子」链，卡片内多一个节点就全断；
//   去掉位置约束后靠**唯一性校验**重新找回来（实测盘中卡 -83/-86 这类就靠这步复活）。
// **每步都要求唯一匹配**：0 个=不匹配，多个=宁可不画也不锚到错的元素上（误锚比不锚更坑）。
function stripNth(sel) {
  return String(sel || '').replace(/:nth-child\(\d+\)/g, '');
}

function anchorCandidates(sel) {
  const out = [];
  const s = String(sel || '').trim();
  if (!s) return out;
  const push = function (x) {
    const v = String(x || '').trim();
    if (v && out.indexOf(v) < 0) out.push(v);
  };
  push(s);
  push(stripNth(s));
  const parts = s.split('>').map(function (p) { return p.trim(); }).filter(Boolean);
  for (let i = 1; i < parts.length; i++) {
    const tail = parts.slice(i).join(' > ');
    push(tail);
    push(stripNth(tail));
  }
  return out;
}

function resolveTarget(note) {
  if (!note) return null;
  const cands = anchorCandidates(note.selector);
  for (let i = 0; i < cands.length; i++) {
    let hit = null;
    try { hit = document.querySelectorAll(cands[i]); } catch (err) { continue; }
    if (!hit || hit.length !== 1) continue;
    if (hit[0] && hit[0].isConnected) return hit[0];
  }
  return null;
}

// 本页标注的 page 标识：v2:card:<卡 key>（有卡）/ v2:/v2/（整壳）。
// 为什么不用 location.hash：v2 壳没有 Tab hash，「当前挂着哪张卡」只有壳自己知道 →
// 由 shell.js 经 init({cardKey}) 提供一个读取器；单卡 ?card= 视图下退回 URL 参数。
function pageKey() {
  let card = '';
  if (typeof st.cardKey === 'function') {
    try { card = String(st.cardKey() || '').trim(); } catch (err) { card = ''; }
  }
  if (!card) {
    try { card = (new URLSearchParams(location.search).get('card') || '').trim(); } catch (err) { card = ''; }
  }
  return PAGE_PREFIX + (card ? 'card:' + card : (location.pathname || '/v2/'));
}

// ---------------------------------------------------------------- 绘制层判定（2026-10-30）
//
// 为什么要有它（主人 2026-10-30 报「卡片类的东西会定位到下面的一层」）：
//   lightweight-charts 每个窗叠了 2 张同尺寸 canvas：z=1 画网格/柱/线，z=2 只画十字光标与
//   价格标签。elementsFromPoint 由内到外，最内层永远是 z=2 那张 —— 照它存 selector 等于把
//   标注锚在一块画布上（N20261030-005524-56 / -57 两条「透明度调到70%」就是这么落空的）。
// 判据为什么不用「有没有像素」：鼠标一移到图上，覆盖层立刻画出十字线+标签，实测占比
//   0.2%~0.7%（静止时为 0），空的绝对值判据当场失效 —— 必须**跟同容器的兄弟层比相对量**。
// 口径：候选链里遇到 canvas，若同父容器还有别的同尺寸 canvas、且自己内容不到最丰那一张的
//   1/4，就当覆盖层跳过、继续往下沉一层；全不满足或量不到（WebGL/tainted）就按原样返回
//   （旧行为，不让人点不到东西）。非 canvas 一律不判，行为与旧版一致。
const OVERLAY_RATIO = 0.25;   // 上层内容不到最丰一层 1/4 → 覆盖层（实测：主层 12%~23%，覆盖层 <1%）
const INK_TTL = 800;          // mousemove 每帧都会问：同一张 canvas 800ms 内只量一次
const inkCache = new WeakMap();

// 返回非透明像素占比；量不到（无 2d 上下文 / tainted / 尺寸为 0）返回 -1，调用方按「不判」处理。
function canvasInkRatio(el, force) {
  const now = Date.now();
  const hit = inkCache.get(el);
  if (!force && hit && now - hit.t < INK_TTL) return hit.v;
  let v = -1;
  try {
    const w = el.width, h = el.height;
    if (w && h) {
      const ctx = el.getContext('2d');
      if (ctx) {
        const data = ctx.getImageData(0, 0, w, h).data;
        let nz = 0;
        for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) nz++;
        v = nz / (w * h);
      }
    }
  } catch (err) { v = -1; }
  inkCache.set(el, { t: now, v: v });
  return v;
}

function canvasOverlay(el, force) {
  const parent = el.parentElement;
  if (!parent) return false;
  let sibs = null;
  try { sibs = parent.querySelectorAll('canvas'); } catch (err) { sibs = null; }
  if (!sibs || sibs.length < 2) return false;
  const mine = canvasInkRatio(el, force);
  if (!(mine >= 0)) return false;
  let best = 0;
  for (let i = 0; i < sibs.length; i++) {
    if (sibs[i] === el) continue;
    const v = canvasInkRatio(sibs[i], force);
    if (v > best) best = v;
  }
  return best > 0 && mine < best * OVERLAY_RATIO;
}

// 光标处**最内层**可标注元素（本次修复的核心：不再从外往里挑整块）
// force=true（点击时）强制重算绘制层；false（mousemove 预览）走 800ms 缓存。
function pickAt(x, y, force) {
  let list = [];
  try { list = document.elementsFromPoint(x, y) || []; } catch (err) { list = []; }
  if (!list.length) {
    let one = null;
    try { one = document.elementFromPoint(x, y); } catch (err) { one = null; }
    if (one) list = [one];
  }
  let fallback = null;
  for (let i = 0; i < list.length; i++) {
    const el = list[i];
    if (!el || el.nodeType !== 1) continue;
    if (isOwn(el) || isShellUi(el)) continue;
    if (el === document.body || el === document.documentElement) continue;
    if (!fallback) fallback = el;
    if (el.tagName === 'CANVAS' && canvasOverlay(el, force)) continue;   // 覆盖层：下沉一层
    return el;
  }
  return fallback;
}

// ---------------------------------------------------------------- 草稿（防误关丢长文，旧页同款）

function draftSave(sel, txt) {
  try {
    const key = DRAFT_KEY + '|' + pageKey() + '|' + sel;
    if (!txt) localStorage.removeItem(key);
    else localStorage.setItem(key, txt);
  } catch (err) { /* 无 localStorage（隐私模式的 iframe）不阻断 */ }
}

function draftGet(sel) {
  try { return localStorage.getItem(DRAFT_KEY + '|' + pageKey() + '|' + sel) || ''; } catch (err) { return ''; }
}

function draftClear(sel) {
  try { localStorage.removeItem(DRAFT_KEY + '|' + pageKey() + '|' + sel); } catch (err) { /* 同上 */ }
}

// ---------------------------------------------------------------- DOM 骨架

function ensure() {
  if (st.hi && st.hi.isConnected) return;
  st.hi = mk('div', 'un-hi');
  st.tip = mk('div', 'un-tip');
  st.hud = mk('div', 'un-hud');
  st.hud.innerHTML = '<b>标注模式</b> · 点任意元素写意见 · <b>ESC / 右键退出</b>';
  st.hi.hidden = true;
  st.tip.hidden = true;
  st.hud.hidden = true;
  document.body.append(st.hi, st.tip, st.hud);
}

function paintHi(el) {
  if (!st.hi) return;
  if (!el || !el.isConnected) { st.hi.hidden = true; return; }
  const r = el.getBoundingClientRect();
  if (!r.width && !r.height) { st.hi.hidden = true; return; }
  st.hi.hidden = false;
  st.hi.style.left = r.left + 'px';
  st.hi.style.top = r.top + 'px';
  st.hi.style.width = r.width + 'px';
  st.hi.style.height = r.height + 'px';
}

function paintTip(x, y, text) {
  if (!st.tip) return;
  if (!text) { st.tip.hidden = true; return; }
  st.tip.hidden = false;
  st.tip.textContent = text;
  const w = st.tip.offsetWidth || 160;
  const left = Math.min(x + 14, Math.max(8, window.innerWidth - w - 10));
  const top = Math.min(y + 16, Math.max(8, window.innerHeight - 40));
  st.tip.style.left = left + 'px';
  st.tip.style.top = top + 'px';
}

function paintButton() {
  if (!st.btn) return;
  st.btn.classList.toggle('is-on', st.on);
  st.btn.setAttribute('aria-pressed', st.on ? 'true' : 'false');
  st.btn.title = st.on ? '退出标注模式（ESC）' : '标注模式：点任意元素写意见';
}

// ---------------------------------------------------------------- 输入框

function closeBox() {
  if (st.box && st.box.parentNode) st.box.parentNode.removeChild(st.box);
  st.box = null;
  st.editing = null;
  st.cur = null;
  st.deepest = null;
  if (st.hi) st.hi.hidden = true;
}

// 编辑态没有粒度按钮：下面的 querySelector 取不到就是 null，判空后自然跳过。
function paintSel() {
  if (!st.box || !st.cur) return;
  const node = st.box.querySelector('.un-sel');
  if (node) node.textContent = describe(st.cur) + '  ·  ' + uniqueSel(st.cur);
  const up = st.box.querySelector('[data-un-act="up"]');
  if (up) {
    const p = st.cur.parentElement;
    up.disabled = !(p && p !== document.body && p !== document.documentElement && !isOwn(p));
  }
  const deep = st.box.querySelector('[data-un-act="deep"]');
  if (deep) deep.disabled = (st.cur === st.deepest);
}

function boxErr(msg) {
  if (!st.box) return;
  const node = st.box.querySelector('.un-err');
  if (!node) return;
  node.hidden = !msg;
  node.textContent = msg || '';
}

function setBusy(busy) {
  if (!st.box) return;
  const btn = st.box.querySelector('[data-un-act="save"]');
  if (!btn) return;
  btn.disabled = !!busy;
  btn.textContent = busy ? '保存中…' : '保存';
}

function placeBox(box, x, y) {
  const w = box.offsetWidth || 320;
  const h = box.offsetHeight || 200;
  box.style.left = Math.min(Math.max(8, x), Math.max(8, window.innerWidth - w - 10)) + 'px';
  box.style.top = Math.min(Math.max(8, y + 12), Math.max(8, window.innerHeight - h - 10)) + 'px';
}

function openBox(x, y, el, editing) {
  closeBox();
  ensure();
  st.cur = el;
  if (!editing) st.deepest = el;
  st.editing = editing || null;

  const box = mk('div', 'un-box');
  // 粒度切换只在「新建」时给：编辑走 /ui_notes/update，只改文字，
  // 位置/selector 变了算另一条标注（旧页 §update 的语义），给了按钮反而误导。
  box.innerHTML =
    (editing ? '<div class="un-edit"></div>' : '') +
    '<div class="un-sel"></div>' +
    (editing ? '' :
      '<div class="un-nav">' +
        '<button type="button" data-un-act="up" title="选父元素（放大粒度）">↑ 上一层</button>' +
        '<button type="button" data-un-act="deep" title="回到最初命中的最内层元素">↓ 最内层</button>' +
      '</div>') +
    '<textarea placeholder="这里要怎么改？" spellcheck="false"></textarea>' +
    '<div class="un-act">' +
      '<button type="button" data-un-act="cancel">取消</button>' +
      '<button type="button" class="un-pri" data-un-act="save">保存</button>' +
    '</div>' +
    '<div class="un-err" hidden></div>';
  document.body.append(box);
  st.box = box;

  if (editing) {
    const hint = box.querySelector('.un-edit');
    if (hint) hint.textContent = '编辑标注 #' + String(editing.id || '');
  }
  paintSel();

  const ta = box.querySelector('textarea');
  if (editing) {
    ta.value = String(editing.text || '');
  } else {
    const pre = draftGet(uniqueSel(el));
    if (pre) ta.value = pre;
  }
  placeBox(box, x, y);
  setTimeout(function () { try { ta.focus(); } catch (err) { /* 忽略 */ } }, 20);

  ta.addEventListener('input', function () {
    if (st.editing) return;                       // 编辑态不存草稿（原文已在盘上）
    draftSave(uniqueSel(st.cur), ta.value);
  });
  ta.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeBox();
    }
  });
  box.addEventListener('click', function (event) {
    const act = event.target && event.target.getAttribute ? event.target.getAttribute('data-un-act') : '';
    if (!act) return;
    event.preventDefault();
    event.stopPropagation();
    if (act === 'cancel') { closeBox(); return; }
    if (act === 'up') {
      const p = st.cur && st.cur.parentElement;
      if (p && p !== document.body && p !== document.documentElement && !isOwn(p)) {
        st.cur = p;
        paintSel();
        paintHi(st.cur);
      }
      return;
    }
    if (act === 'deep') {
      if (st.deepest && st.deepest.isConnected) { st.cur = st.deepest; paintSel(); paintHi(st.cur); }
      return;
    }
    if (act === 'save') submit();
  });
}

// ---------------------------------------------------------------- 提交（本页直连，不经父页代理）

// 与 ops 插件 UiNoteBody 的 8 个字段一一对应；顺序照模型声明，便于人工比对。
function buildBody(el, text) {
  const r = el.getBoundingClientRect();
  let cs = null;
  try { cs = getComputedStyle(el); } catch (err) { cs = null; }
  const style = cs ? [
    'font-size:' + cs.fontSize,
    'color:' + cs.color,
    'background:' + cs.backgroundColor,
    'display:' + cs.display,
    'width:' + Math.round(r.width) + 'px',
    'height:' + Math.round(r.height) + 'px',
    'padding:' + cs.padding,
    'margin:' + cs.margin,
    'gap:' + (cs.gap || '-'),
    'font-weight:' + cs.fontWeight,
  ].join(';') : '';
  return {
    selector: uniqueSel(el),
    text: text,
    page: pageKey(),
    elem: (el.tagName + ' ' + String(el.textContent || '').trim().slice(0, 30)).trim(),
    html: String(el.outerHTML || '').slice(0, HTML_MAX),
    style: style.slice(0, STYLE_MAX),
    x: Math.round(r.left),
    y: Math.round(r.top),
  };
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (!res.ok) {
    const detail = (data && data.detail) ? data.detail : ('HTTP ' + res.status);
    throw new Error(String(detail));
  }
  return data || {};
}

function emit(type, detail) {
  try { document.dispatchEvent(new CustomEvent(type, { detail: detail || {} })); } catch (err) { /* 老浏览器无 CustomEvent 构造器 */ }
}

async function submit() {
  if (!st.box || !st.cur) return;
  const ta = st.box.querySelector('textarea');
  const text = String(ta ? ta.value : '').trim();
  if (!text) { boxErr('先写点什么再保存'); return; }
  boxErr('');
  setBusy(true);
  const editing = st.editing;
  try {
    let out;
    if (editing && editing.id) {
      out = await postJson(API_UPDATE, { id: editing.id, text: text });
      emit('v2:note-saved', { id: editing.id, edit: true, open: out.open });
    } else {
      const body = buildBody(st.cur, text);
      out = await postJson(API_ADD, body);
      draftClear(body.selector);
      emit('v2:note-saved', { id: (out.note && out.note.id) || '', open: out.open });
    }
    closeBox();
    refreshNotes();
  } catch (err) {
    const message = (err && err.message) ? err.message : String(err);
    boxErr('保存失败：' + message + '（可再点保存重试）');
    setBusy(false);
    emit('v2:note-error', { message: message });
  }
}

// ---------------------------------------------------------------- pin（本页已有标注）

function clearPins() {
  const list = document.querySelectorAll('.un-pin');
  for (let i = 0; i < list.length; i++) {
    if (list[i].parentNode) list[i].parentNode.removeChild(list[i]);
  }
}

function renderPins() {
  clearPins();
  // 2026-11-04（主人：标注几乎用不了）：pin 改为**常驻**，不再要求先开标注模式。
  //   原实现 `if (!st.on) return` 的结果是：标完退出模式后点全消失，再进页面也看不到任何标注——
  //   只能写、不能回看，等于把标注做成了写一次就消失的单向通道。
  ensure();
  let seq = 0;
  let lost = 0;
  st.notes.forEach(function (note) {
    if (!note) return;
    const target = resolveTarget(note);
    if (!target) { lost += 1; return; }        // 锚点彻底失效：计入角标，本页不画鬼影
    const r = target.getBoundingClientRect();
    if (!r.width && !r.height) return;         // 隐藏元素（页签未激活等）
    // 2026-11-06（P4）：整块移出视口不画。原先只判「尺寸为 0」，滚出去以后坐标变负、
    //   被 Math.max(2, …) 夹到屏幕边缘，圆点像粘在边上一样不消失。
    if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) return;
    seq += 1;
    const pin = mk('div', 'un-pin');
    // 2026-11-06（P3）：圆点 18px、字号 10px，原先塞完整 id（实测内容 55px 宽）会漏到框外。
    //   圆点只写本页序号，完整 id 与内容进 title。
    pin.textContent = String(seq);
    pin.title = String(note.id || '') + (note.text ? ' · ' + note.text : '');
    pin.style.left = Math.max(2, r.left - 9) + 'px';
    pin.style.top = Math.max(2, r.top - 9) + 'px';
    pin.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      openBox(r.left, r.top, target, note);
    });
    document.body.append(pin);
  });
  // 2026-11-06（P2 下半）：锚不上的标注不能再「静静消失」——入口按钮上挂一个数字，
  //   告诉主人本页还有几条存在但定位失效，去标注清单里处理。0 条时角标收掉。
  setBadge(lost);
}

function setBadge(n) {
  const btn = st.fabBtn;
  if (!btn) return;
  const el = btn.querySelector('.un-fab-n');
  if (!el) return;
  el.textContent = n ? String(n) : '';
  el.hidden = !n;
}

let repaintTimer = 0;
function repaint() {
  if (repaintTimer) return;
  repaintTimer = setTimeout(function () {
    repaintTimer = 0;
    if (st.on) {
      if (st.box) { if (st.cur) paintHi(st.cur); return; }   // 输入框中：高亮锁定目标，不跟鼠标
      if (st.hi) st.hi.hidden = true;
    }
    renderPins();
  }, 100);
}

let _lastPage = '';

async function refreshNotes() {
  const mine = pageKey();
  // 2026-11-06（P1）：页面标识变了（切卡）先清掉旧圆的点 —— 等 fetch 回来再清的话，
  //   上一张卡的圆点会在新卡的屏幕上停一拍，看起来就像「串卡」。
  if (mine !== _lastPage) {
    _lastPage = mine;
    st.notes = [];
    renderPins();
  }
  try {
    const res = await fetch(API_LIST + '?status=open', { cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!res.ok) return;
    const data = await res.json();
    const rows = (data && Array.isArray(data.notes)) ? data.notes : [];
    // 只收本页（page 完全相同）的 v2 标注：selector 只在本文档上下文有效，
    // 别的卡/旧页的 selector 在这里查必然落空，画出来只会是鬼影。
    st.notes = rows.filter(function (n) {
      return n && String(n.page || '') === mine;
    });
    renderPins();
    // 切卡/首次进页时卡片 DOM 往往还没挂完：120ms 后补画一次（锚点一出现就能看见）。
    setTimeout(renderPins, 120);
  } catch (err) { /* 列表拉不到不影响标注：写盘路径与列表路径互不依赖 */ }
}

// ---------------------------------------------------------------- 模式

function setMode(next) {
  const on = !!next;
  if (st.on === on) { paintButton(); return; }
  st.on = on;
  ensure();
  document.documentElement.style.cursor = on ? 'crosshair' : '';
  st.hud.hidden = !on;
  paintButton();
  if (!on) {
    st.hi.hidden = true;
    st.tip.hidden = true;
    closeBox();
    renderPins();            // pin 常驻：退出模式后仍然看得到标过哪里
  } else {
    refreshNotes();
  }
  if (typeof st.onMode === 'function') {
    try { st.onMode(on); } catch (err) { /* 回调出错不影响模式本身 */ }
  }
  emit('v2:note-mode', { on: on });
}

function toggle() {
  setMode(!st.on);
}

// ---------------------------------------------------------------- 事件（全部 capture：抢在卡片自身处理器之前）

function bindEvents() {
  document.addEventListener('mousemove', function (event) {
    if (!st.on) return;
    if (st.box) return;                        // 输入框中：高亮锁定在当前目标，不跟鼠标跑
    const target = event.target;
    if (isShellUi(target)) { st.hi.hidden = true; st.tip.hidden = true; return; }
    const el = pickAt(event.clientX, event.clientY, false);
    if (!el) { st.hi.hidden = true; st.tip.hidden = true; return; }
    paintHi(el);
    paintTip(event.clientX, event.clientY, describe(el));
  }, true);

  document.addEventListener('click', function (event) {
    if (!st.on) return;
    const target = event.target;
    if (isShellUi(target)) return;             // 壳自己的按钮/抽屉：放行（否则退不出模式）
    // 拦截：卡片自身的 click 处理器不得跟着跑（旧页做法：capture + preventDefault）
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
    const el = pickAt(event.clientX, event.clientY, true);   // 点击：强制重算绘制层，不吃缓存
    if (!el) return;
    openBox(event.clientX, event.clientY, el, null);
  }, true);

  // 标注模式下吞掉 dblclick：卡片里「双击展开/收起」会误触发（旧页同款）
  document.addEventListener('dblclick', function (event) {
    if (!st.on) return;
    if (isShellUi(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
  }, true);

  // 右键退出（与旧页一致）：输入框内放行（保留原生复制/粘贴），框内右键不动模式。
  document.addEventListener('contextmenu', function (event) {
    if (!st.on) return;
    const target = event.target;
    if (target && target.tagName === 'TEXTAREA') return;
    if (st.box && st.box.contains && st.box.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
    setMode(false);
  }, true);

  // ESC：先收输入框，再退模式（任务书：ESC 退出模式）。
  // 必须 stopImmediatePropagation：壳自己在 document 上也有 ESC（关搜索/关抽屉），
  // 同一节点上的监听靠 stopPropagation 拦不住，会一按 ESC 连抽屉一起关。
  document.addEventListener('keydown', function (event) {
    if (!st.on) return;
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
    if (st.box) { closeBox(); return; }
    setMode(false);
  }, true);

  window.addEventListener('scroll', repaint, true);
  window.addEventListener('resize', function () {
    applyFabPos(st.fabBtn);      // 窗口变小后按钮不能跑出视口
    repaint();
  });
}

// ---------------------------------------------------------------- 父页握手（仅嵌入旧页时；提交仍走本页直连）

function inFrame() {
  return window.parent !== window;
}

// 父页 origin：iframe 内只能从 document.referrer 拿，解析失败用 '*'（不写死端口：
// 8800/8801 都可能嵌 /v2/，硬编码会静默失效 —— 与 shell.js::parentOrigin 同口径）
function parentOrigin() {
  try {
    if (document.referrer) {
      const u = new URL(document.referrer);
      if (u.origin && u.origin !== 'null') return u.origin;
    }
  } catch (err) { /* 解析失败 → '*' */ }
  return '*';
}

function hello() {
  if (!inFrame()) return;
  try { window.parent.postMessage({ type: MSG_HELLO, page: pageKey() }, parentOrigin()); } catch (err) { /* 忽略 */ }
}

function bindParentMessages() {
  if (!inFrame()) return;
  window.addEventListener('message', function (event) {
    if (event.source !== window.parent) return;   // 只认父页（同页脚本/别的 iframe 一律不接）
    const d = event.data || {};
    if (d.type === MSG_SET_MODE) { setMode(d.on); return; }
    if (d.type === MSG_LIST) {
      const mine = pageKey();
      st.notes = (Array.isArray(d.notes) ? d.notes : []).filter(function (n) {
        return n && String(n.page || '') === mine;
      });
      renderPins();
      return;
    }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', hello);
  else hello();
}

// ---------------------------------------------------------------- 对外

// 入口按钮：2026-10-28 主人定——**悬浮式可拖动**，不再用 rail 按钮。
//
// 为什么改：K 线弹窗 #klineModal 的 z-index=100（旧版 CSS），标注层原先最高 40、
// rail 按钮 z=10，弹窗一开整块被压在遮罩下——想标 K 线图时按钮点不到（标注 N20261028-100025-36）。
// 现在：入口固定在视口上（可拖到任意位置，位置记 localStorage），z 高于所有弹窗；
// 被旧页嵌进 iframe 时仍不建浮标（父页右下角已有「✎ 标注」，父页经 postMessage 驱动本文档）。
const FAB_POS_KEY = 'v2_note_fab_pos_v1';

function fabPosGet() {
  try {
    const raw = localStorage.getItem(FAB_POS_KEY);
    const o = raw ? JSON.parse(raw) : null;
    return (o && isFinite(o.x) && isFinite(o.y)) ? o : null;
  } catch (err) { return null; }
}

function fabPosSave(x, y) {
  try { localStorage.setItem(FAB_POS_KEY, JSON.stringify({ x: Math.round(x), y: Math.round(y) })); } catch (err) { /* 隐私模式：不持久化也不影响本次拖动 */ }
}

function applyFabPos(btn) {
  if (!btn) return;
  const p = fabPosGet();
  if (!p) return;
  const w = btn.offsetWidth || 64;
  const h = btn.offsetHeight || 32;
  const x = Math.min(Math.max(4, p.x), Math.max(4, window.innerWidth - w - 4));
  const y = Math.min(Math.max(4, p.y), Math.max(4, window.innerHeight - h - 4));
  btn.style.left = x + 'px';
  btn.style.top = y + 'px';
  btn.style.right = 'auto';
  btn.style.bottom = 'auto';
}

// 被旧页嵌进 iframe 时不造浮标：父页（8810 旧页）右下角已经有一个「✎ 标注」，
// 它经 pushFrames 驱动本文档内的标注（实测 8/8 全绿）。再叠一个浮标 = 同一屏两个同类按钮
// （主人 2026-10-26 明确反感重复控件）。
function embedded() {
  return window.parent !== window;
}

function ensureButton() {
  if (embedded()) {
    if (st.fabBtn) st.fabBtn.hidden = true;
    st.fab = false;
    return null;
  }
  if (st.fabBtn && st.fabBtn.isConnected) {
    st.fab = true;
    st.fabBtn.hidden = false;
    applyFabPos(st.fabBtn);
    return st.fabBtn;
  }
  const btn = mk('button', 'un-fab');
  btn.type = 'button';
  btn.setAttribute('aria-label', '标注模式');
  btn.title = '标注模式：点任意元素写意见（可拖动）';
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    + 'stroke-linecap="round" stroke-linejoin="round"><path d="M4 20l4.5-1.2L20 7.3 16.7 4 5.2 15.5z"/>'
    + '<path d="M14.5 6.2l3.3 3.3"/></svg><span class="un-fab-t">标注</span>'
    + '<span class="un-fab-n" hidden></span>';
  document.body.append(btn);
  st.fabBtn = btn;
  st.fab = true;
  applyFabPos(btn);
  // 首次 append 时 offsetWidth 可能还是 0（CSS 未生效）→ 下一帧再夹一次边界
  setTimeout(function () { applyFabPos(btn); }, 0);
  return btn;
}

// 开关绑定：按钮节点会变（嵌/不嵌切换），按节点判重，避免同一节点挂两次。
// 拖动与点击的区分：位移 ≥ 4px 算拖（不触发模式切换），否则算点击。
function bindBtn(btn) {
  if (!btn || st.boundBtn === btn) return;
  st.boundBtn = btn;
  let down = null;
  let moved = false;
  btn.addEventListener('pointerdown', function (event) {
    if (event.button != null && event.button !== 0) return;
    const r = btn.getBoundingClientRect();
    down = { x: event.clientX, y: event.clientY, left: r.left, top: r.top };
    moved = false;
    try { btn.setPointerCapture(event.pointerId); } catch (err) { /* 老浏览器无捕获 */ }
    event.preventDefault();                       // 拖动时不要选文字 / 触发原生拖拽
  });
  btn.addEventListener('pointermove', function (event) {
    if (!down) return;
    const dx = event.clientX - down.x;
    const dy = event.clientY - down.y;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    moved = true;
    btn.classList.add('is-drag');
    const w = btn.offsetWidth || 64;
    const h = btn.offsetHeight || 32;
    const x = Math.min(Math.max(4, down.left + dx), Math.max(4, window.innerWidth - w - 4));
    const y = Math.min(Math.max(4, down.top + dy), Math.max(4, window.innerHeight - h - 4));
    btn.style.left = x + 'px';
    btn.style.top = y + 'px';
    btn.style.right = 'auto';
    btn.style.bottom = 'auto';
  });
  btn.addEventListener('pointerup', function (event) {
    if (!down) return;
    const was = moved;
    down = null;
    btn.classList.remove('is-drag');
    try { btn.releasePointerCapture(event.pointerId); } catch (err) { /* 同上 */ }
    if (was) {
      fabPosSave(btn.offsetLeft, btn.offsetTop);
      st.noClickUntil = Date.now() + 250;          // 拖完浏览器还会派一次 click，吞掉（只吞这一次）
    }
  });
  btn.addEventListener('pointercancel', function () {
    down = null;
    moved = false;
    btn.classList.remove('is-drag');
  });
  btn.addEventListener('click', function (event) {
    event.preventDefault();
    event.stopPropagation();
    if (Date.now() < (st.noClickUntil || 0)) return;
    toggle();
  });
}

// 重算入口。为什么不能只在 init 里算一次：壳在 `boot()` 里才把 rail 设 display:none，
// 而 init 走的是顶层 await import，两者先后不定 —— 只算一次就会出现「init 时 rail 还在 →
// 拿 rail 按钮 → 壳随后藏 rail → 按钮跟着消失」。浮标常驻后这个问题不存在了，
// 但 syncEntry 保留：壳在 boot 末尾仍会调一次，保证按钮在 DOM 重建后仍在。
function syncEntry() {
  const next = ensureButton();
  if (next !== st.btn) st.btn = next;
  bindBtn(st.btn);
  paintButton();
  return st.btn;
}

function init(options) {
  const opts = options || {};
  if (typeof opts.onMode === 'function') st.onMode = opts.onMode;
  if (typeof opts.cardKey === 'function') st.cardKey = opts.cardKey;
  syncEntry();
  if (st.inited) { paintButton(); return api; }
  st.inited = true;
  bindEvents();
  bindParentMessages();
  paintButton();
  // pin 常驻：进页就把本页已有标注画出来（原先只在开标注模式时才拉列表）
  refreshNotes();
  return api;
}

function isOn() {
  return st.on;
}

const api = {
  init: init,
  setMode: setMode,
  toggle: toggle,
  isOn: isOn,
  pageKey: pageKey,
  refresh: refreshNotes,
  syncEntry: syncEntry,
  describe: describe,
  path: path,
};

window.__v2Note = api;
export default api;
