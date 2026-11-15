// K 线弹窗（2026-10-26 · 01）：旧版 `#klineModal` 的形态，内部用 v2 自己的图。
//
// 为什么要它：v2 原先把「点 K 线」实现成切整页卡片，与旧版「就地弹一个窗、不动当前页」不一致。
// 本模块把旧版形态搬回来，但**不搬旧版的图**——旧版是 KLineChart v9（第三方库、1500 行适配），
// v2 已有自己的 canvas 画法（frontend.js 的 drawDaily / drawIntraday / drawChips），直接复用。
//
// 布局（旧版同构）：
//   ┌─────────────── kbox（居中主窗）───────────────┐ ┌── 盘口 ──┐ ┌── F10 ──┐
//   │ 头部：代码/名称 周期档 指标  身体：走势图     │ │ 五档/统计 │ │ 估值…  │
//   │ 底部：状态行                                  │ │ 筹码峰   │ │ 序列   │
//   └──────────────────────────────────────────────┘ └──────────┘ └───────┘
//
// 关于「合并能不能省一次取数」（主人 2026-10-26 问，实测后修正过判断）：
//   · 走势图 `/intraday`（89ms）与盘口 `/quotes`（234ms 冷 / 22ms 热）、筹码 `/volume_profile`（15ms）
//     **走的是不同后端路径**，合并成一次请求省不到服务端计算，只能省 2 次 HTTP 往返（本机≈1ms）。
//   · 真正贵的只有 F10：**每个代码第一次 ~7.7-8.4s，之后 ~50ms**（内部按代码缓存）。
//     所以本模块的做法是「先出图、盘口筹码并行、F10 异步补」 —— 弹窗不等 F10，
//     打开到出图 ≈ 100ms，F10 到了再填右侧。这比任何合并都更直接地改善打开速度。
// 2026-10-27（01 · P1a）：补旧版右键菜单框架 + 手工画线 + 真尺寸记忆。
//   · 菜单结构、标记线口径、尺寸/开关持久化都在 `./marks.js`（纯函数，可 node 单测）；
//     本文件只负责 DOM、canvas 与事件。
//   · 买卖点/持仓成本线/委托线/条件单线**本阶段没有数据源**，一律「有数据才画」，
//     没有就在子菜单写原因（`marks.js::availability`）——绝不画假线；
//     下单/条件单/叠加对比整项 disabled 并带原因，不做点了没反应的假控件。
//   · 尺寸：`klm_size_v1` 存 {w,h}，窗口缩放与拖角都真正改容器尺寸后重画（原来 resize 只重取数，
//     canvas clientWidth 不动 → 看起来什么都没变）。
//
// 注意：本文件在 `frontend/` 子目录，而 `frontend.js` 在插件根目录（资源白名单只放行
// `frontend/` 与 `assets/`，所以弹窗只能放在子目录）——引用得用 `../`。
import { drawDaily, drawIntraday, drawChips } from '../frontend.js?v=0.9.67';
import {
  readMarks, saveMarks, readSize, saveSize, clampSize,
  readHlines, addHline, clearHlines,
  scaleFor, lineModels, buildMenuModel,
  alignPctToSlots, overlayExtent, paperMarkData, mapOverlayCode,
} from './marks.js?v=0.9.67';
import * as IND from './indicators.js?v=0.9.67';
import { drawSub, SUB_DEFS } from './subcharts.js?v=0.9.67';
import { createOrderPanel } from './order_panel.js?v=0.9.67';
import { openAlertPanel } from './alert_panel.js?v=0.9.67';
import {
  COLORS as LABEL_COLORS, SHAPES as LABEL_SHAPES, SHAPE_LABEL, DEFAULT_COLOR as LABEL_DEFAULT_COLOR,
  MAX_PER_CODE as LABEL_MAX, TEXT_MAX as LABEL_TEXT_MAX,
  readCode as readLabels, readOn as readLabelsOn, writeOn as writeLabelsOn,
  addLabel, updateLabel, removeLabel, clearCode as clearLabels, menuItems as labelMenuItems,
} from './labels.js?v=0.9.67';

const CSS = `
.klm-scrim{position:fixed;inset:0;background:rgba(5,8,15,.78);z-index:9000;display:flex;
  align-items:flex-start;justify-content:center;padding:3vh 12px;overflow:auto}
/* 半透明主题（frostglass/frostnight/acrylic）的 --card 带 alpha，不给毛玻璃就是「半透明 + 零模糊」，
   背后正文原封不动透上来。口径同旧页 #klineModal .kbox（2026-10-23 修正）。 */
:is([data-theme^="frost"],[data-theme="acrylic"]) .klm-box,
:is([data-theme^="frost"],[data-theme="acrylic"]) .klm-side{
  backdrop-filter:blur(18px) saturate(1.2);-webkit-backdrop-filter:blur(18px) saturate(1.2)}
.klm-wrap{display:flex;gap:10px;align-items:flex-start;max-width:100%}
.klm-box{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-lg);
  box-shadow:var(--shadow-pop);display:flex;flex-direction:column;min-width:520px;flex:0 1 auto}
.klm-hd{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:9px 12px;border-bottom:1px solid var(--line)}
.klm-title{font-weight:600;font-size:14px}
.klm-code{color:var(--dim);font-family:var(--font-mono);font-size:12px}
.klm-grp{display:inline-flex;border:1px solid var(--line);border-radius:var(--radius-sm);overflow:hidden}
.klm-grp button{background:transparent;color:var(--fg);border:0;padding:4px 9px;font:inherit;font-size:12px;cursor:pointer}
.klm-grp button:hover{background:var(--card2)}
.klm-grp button.on{background:var(--accent);color:var(--accent-on)}
.klm-x{margin-left:auto;background:transparent;border:0;color:var(--dim);font-size:16px;
  cursor:pointer;padding:2px 6px;border-radius:var(--radius-sm)}
.klm-x:hover{background:var(--card2);color:var(--fg)}
/* 主图容器与标记层：尺寸由 .klm-cv 决定（可被尺寸记忆/拖角改写），两个 canvas 都填满它。
   .klm-mk 是标记层（成本/委托/条件单/手工线），pointer-events:none，不抢主图的点击与右键。 */
.klm-cv{position:relative;width:100%;height:min(52vh,460px);min-height:260px;background:var(--bg)}
.klm-canvas{width:100%;height:100%;display:block}
.klm-mk{position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none}
.klm-grip{position:absolute;right:1px;bottom:1px;width:15px;height:15px;cursor:nwse-resize;opacity:.55;
  background:linear-gradient(135deg,transparent 46%,var(--dim) 46%,var(--dim) 54%,transparent 54%)}
.klm-grip:hover{opacity:1}
/* 右键菜单（旧版 #koMenu 同构：header + 项 + 分隔，子菜单右侧展开） */
.klm-menu{position:fixed;z-index:9200;min-width:200px;max-width:300px;padding:4px;
  background:var(--card2);border:1px solid var(--line);border-radius:var(--radius-sm);
  box-shadow:var(--shadow-pop);font-size:12px;color:var(--fg);user-select:none}
.klm-sub{z-index:9210}
.klm-mh{padding:4px 8px 6px;font-size:11px;color:var(--dim);border-bottom:1px solid var(--line);
  margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.klm-mh b{color:var(--fg)}
.klm-mi{display:flex;align-items:flex-start;gap:8px;justify-content:space-between;
  padding:5px 8px;border-radius:5px;cursor:pointer}
.klm-mi:hover{background:var(--card)}
.klm-mi.is-disabled{opacity:.45;cursor:not-allowed}
.klm-mi.is-disabled:hover{background:transparent}
.klm-mi .lb{flex:1 1 auto;white-space:nowrap}
.klm-mi .ck{color:var(--accent);font-size:11px;line-height:1.5}
.klm-mi .tg{color:var(--dim);font-size:11px;white-space:nowrap;line-height:1.5}
.klm-mi .rs{display:block;color:var(--dim);font-size:11px;line-height:1.4;white-space:normal}
.klm-sep{height:1px;margin:4px 2px;background:var(--line)}
.klm-hint{color:var(--warn-ink)}
.klm-cross{position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none}
.klm-tip{position:absolute;z-index:5;pointer-events:none;background:var(--card2);border:1px solid var(--line);
  border-radius:var(--radius-sm);padding:4px 7px;font-size:11px;line-height:1.5;box-shadow:var(--shadow-pop);
  white-space:nowrap;font-family:var(--font-mono);display:none}
.klm-subs{display:flex;flex-direction:column}
.klm-sub-cv{width:100%;height:88px;display:block;border-top:1px solid var(--line);background:var(--bg)}
.klm-ov{cursor:pointer;color:var(--dim);border-bottom:1px dotted var(--dim)}
.klm-ov:hover{color:var(--fg)}
.klm-ov.is-err{color:var(--danger-ink);border-bottom-color:var(--danger-ink)}
.klm-toast{position:fixed;left:50%;bottom:6vh;transform:translateX(-50%);z-index:9300;
  background:var(--card2);border:1px solid var(--line);border-radius:var(--radius-pill);
  padding:5px 12px;font-size:12px;box-shadow:var(--shadow-pop)}
.klm-ft{display:flex;gap:10px;align-items:center;padding:6px 12px;border-top:1px solid var(--line);
  color:var(--dim);font-size:12px;flex-wrap:wrap}
.klm-badge{color:var(--warn-ink);border:1px solid var(--warn);background:var(--warn-bg);
  border-radius:var(--radius-pill);padding:1px 7px;font-size:11px}
.klm-badge.is-live{color:var(--down-ink);border-color:var(--down);background:var(--down-bg)}
.klm-side{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-lg);
  box-shadow:var(--shadow-pop);width:250px;flex:0 0 250px;max-height:min(88vh,900px);overflow:auto;
  font-size:12px;padding:9px 11px}
.klm-side h4{margin:0 0 7px;font-size:12px;color:var(--dim);font-weight:500;
  display:flex;align-items:center;gap:6px}
.klm-side h4 .st{font-weight:400;font-size:11px;opacity:.75}
.klm-kv{display:grid;grid-template-columns:auto 1fr;gap:3px 8px;margin-bottom:9px}
.klm-kv .k{color:var(--dim)}
.klm-kv .v{text-align:right;font-family:var(--font-mono)}
.klm-kv .v.up{color:var(--up-ink)}
.klm-kv .v.down{color:var(--down-ink)}
.klm-sec{border-top:1px solid var(--line);padding-top:7px;margin-top:7px}
.klm-q5{width:100%;border-collapse:collapse;font-family:var(--font-mono)}
.klm-q5 td{padding:1px 3px}
.klm-q5 .lbl{color:var(--dim);width:30px;font-family:var(--font-ui)}
.klm-q5 .num{text-align:right}
.klm-q5 .ask{color:var(--down-ink)}
.klm-q5 .bid{color:var(--up-ink)}
.klm-note{color:var(--dim);font-size:11px;line-height:1.5}
.klm-chip{width:100%;height:130px;background:var(--bg);border-radius:var(--radius-sm);display:block}
.klm-loading{color:var(--dim);font-size:11px;opacity:.8}
/* 标签层（2026-11-04）：主图左下角纵向堆叠的备注 chip。
   贴最左边：日线左 pad=28 且价格轴在右，4px 不会压到任何刻度；
   分时左 pad=56 留着涨跌幅刻度，所以整列右移到 62px（同一层只换 left，不换 DOM）。 */
.klm-labels{position:absolute;left:4px;bottom:28px;display:flex;flex-direction:column-reverse;
  align-items:flex-start;gap:4px;max-width:calc(100% - 92px);max-height:calc(100% - 44px);
  overflow:hidden;pointer-events:none;z-index:3}
.klm-labels[data-period="intraday"]{left:62px}
.klm-label{pointer-events:auto;display:inline-flex;align-items:center;gap:5px;height:20px;padding:0 8px;
  border-radius:3px;font-size:11px;line-height:1;max-width:220px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;cursor:context-menu;user-select:none;font-family:var(--font-ui);box-sizing:border-box}
.klm-label .klm-lab-dot{width:4px;height:4px;border-radius:50%;background:currentColor;flex:0 0 auto;opacity:.95}
/* 文字色 = 标签色与正文色的混合：深色主题提亮、浅色主题压暗，8 个色在两种主题下都读得清 */
.klm-label.is-fill{background:color-mix(in srgb,var(--c) 13%,transparent);
  border:1px solid color-mix(in srgb,var(--c) 45%,transparent);
  color:color-mix(in srgb,var(--c) 72%,var(--fg))}
.klm-label.is-fill:hover{background:color-mix(in srgb,var(--c) 24%,transparent);
  border-color:color-mix(in srgb,var(--c) 72%,transparent)}
.klm-label.is-outline{background:transparent;border:1px dashed color-mix(in srgb,var(--c) 62%,transparent);
  color:color-mix(in srgb,var(--c) 72%,var(--fg))}
.klm-label.is-outline:hover{background:color-mix(in srgb,var(--c) 16%,transparent);border-style:solid}
.klm-label.is-solid{background:var(--c);border:1px solid var(--c);color:var(--bg);font-weight:600}
.klm-label.is-solid .klm-lab-dot{background:var(--bg);opacity:.5}
/* 样式面板 / 编辑浮层的色板与形态选择 */
.klm-sws{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:2px 0 8px}
.klm-sw{height:22px;border-radius:4px;border:1px solid var(--line);cursor:pointer;padding:0}
.klm-sw.on{box-shadow:0 0 0 2px var(--card2),0 0 0 3px var(--fg)}
.klm-shapes{display:flex;gap:5px}
.klm-shapes button{flex:1 1 auto;height:24px;background:transparent;border:1px solid var(--line);
  border-radius:4px;color:var(--dim);font:inherit;font-size:11px;cursor:pointer}
.klm-shapes button:hover{background:var(--card);color:var(--fg)}
.klm-shapes button.on{border-color:var(--accent);color:var(--accent)}
.klm-pal{padding:6px 8px 7px;min-width:186px}
.klm-pal h5{margin:0 0 5px;font-size:11px;color:var(--dim);font-weight:400}
.klm-dlg-scrim{position:fixed;inset:0;z-index:9300;background:rgba(5,8,15,.5);display:flex;
  align-items:center;justify-content:center}
.klm-dlg{width:296px;background:var(--card2);border:1px solid var(--line);border-radius:var(--radius-sm);
  box-shadow:var(--shadow-pop);padding:13px 13px 11px;font-size:12px;color:var(--fg)}
.klm-dlg-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;
  font-size:12.5px;font-weight:600}
.klm-dlg-h span{font-size:11px;color:var(--dim);font-weight:400}
.klm-dlg-in{width:100%;height:30px;padding:0 9px;background:var(--bg);border:1px solid var(--line);
  border-radius:5px;color:var(--fg);font:inherit;font-size:12px;outline:none;box-sizing:border-box}
.klm-dlg-in:focus{border-color:var(--accent)}
.klm-dlg-row{display:flex;align-items:center;gap:8px;margin-top:11px}
.klm-dlg-row .k{font-size:11px;color:var(--dim);flex:0 0 26px}
.klm-dlg-row .klm-sws{flex:1 1 auto;grid-template-columns:repeat(8,1fr);gap:5px;margin:0}
.klm-dlg-row .klm-shapes{flex:1 1 auto}
.klm-dlg-pv{display:flex;align-items:center;gap:8px;margin-top:12px;padding-top:10px;border-top:1px solid var(--line)}
.klm-dlg-pv .k{font-size:11px;color:var(--dim)}
.klm-dlg-ft{display:flex;justify-content:flex-end;gap:7px;margin-top:13px}
.klm-dlg-ft button{font:inherit;font-size:12px;padding:5px 13px;border-radius:5px;cursor:pointer}
.klm-dlg-ft .c{background:transparent;border:1px solid var(--line);color:var(--dim)}
.klm-dlg-ft .c:hover{color:var(--fg);background:var(--card)}
.klm-dlg-ft .o{background:var(--accent);border:1px solid var(--accent);color:var(--accent-on);font-weight:600}
.klm-dlg-tip{margin-top:9px;font-size:10.5px;color:var(--dim);opacity:.8}
.klm-err{color:var(--danger-ink);font-size:11px}
@media(max-width:1100px){.klm-wrap{flex-direction:column}.klm-side{width:auto;flex:1 1 auto}}
`;

const MODAL_ID = 'klm-root';
// 周期档（2026-10-27 P1c）：分时 + 旧版全套蜡烛周期。
//   compat = 走绝对 `/api/kline/{code}?period=...` 的档名；intraday 走插件自己的 /intraday。
//   旧版键域（kline_lc.js PERIOD_KEYS）是 d/t/w/m1/m5/m15/m30/m60；这里用 intraday 表示 t。
const PERIODS = [
  { key: 'intraday', label: '分时', compat: null },
  { key: 'd', label: '日线', compat: 'd' },
  { key: 'w', label: '周线', compat: 'w' },
  { key: 'm1', label: '1分', compat: 'm1' },
  { key: 'm5', label: '5分', compat: 'm5' },
  { key: 'm15', label: '15分', compat: 'm15' },
  { key: 'm30', label: '30分', compat: 'm30' },
  { key: 'm60', label: '60分', compat: 'm60' },
];
const SUB_KEY = 'klm_subs_v1';
const PERIOD_KEY = 'klm_period_v1';
const SUB_DEFAULT = { amount: true, amv: false, kdj: false, macd: false, rsi: false };
const CANDLE_LIMIT = 240;

let _state = null;

/**
 * 单调序号闸门（P1c 竞态）：同一次 begin 序列里，只有**最后发起**的那次能改写界面。
 * 抽成纯对象是为了能在 node 里用可控 promise 直接测三种竞态，不必起浏览器。
 */
export function latestGate() {
  let seq = 0;
  return {
    begin: function () { return ++seq; },
    isLatest: function (token) { return token === seq; },
    invalidate: function () { seq++; return seq; },
  };
}

/** 周期记忆：坏值/未知键一律回退分时（不抛错、不写脏值回去）。 */
function readPeriod(store) {
  try {
    const s = store || window.localStorage;
    const v = s.getItem(PERIOD_KEY);
    return PERIODS.some(function (p) { return p.key === v; }) ? v : null;
  } catch (e) { return null; }
}
/** 副图开关：只认已知 key 的布尔值，坏 JSON / 非对象 → 全默认。 */
function readSubs(store) {
  const out = Object.assign({}, SUB_DEFAULT);
  try {
    const s = store || window.localStorage;
    const raw = s.getItem(SUB_KEY);
    if (!raw) return out;
    const j = JSON.parse(raw);
    if (!j || typeof j !== 'object') return out;
    SUB_DEFS.forEach(function (d) { if (d.key in j) out[d.key] = !!j[d.key]; });
  } catch (e) { /* 坏值 → 默认 */ }
  return out;
}

function ensureCss() {
  if (document.getElementById('klm-css')) return;
  const s = document.createElement('style');
  s.id = 'klm-css';
  s.textContent = CSS;
  document.head.append(s);
}

function fmt(v, d) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(d === undefined ? 2 : d);
}

function pctText(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function clsOf(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
}

/** 指数/板块没有五档与逐笔筹码（同 frontend.js 判定，别拿两块「不可用」占地方）。 */
function isIndexLike(code) {
  const u = String(code || '').toUpperCase();
  if (u.endsWith('.TI') || u.endsWith('.BK') || u.startsWith('BK')) return true;
  if (u.endsWith('.SH') && /^000\d{3}/.test(u)) return true;
  if (u.endsWith('.SZ') && /^399\d{3}/.test(u)) return true;
  if (u.endsWith('.CSI')) return true;
  return false;
}

// ---------------------------------------------------------------- F10 渲染

/**
 * 轻量首屏：只渲染 valuation 四件（行业/市值/PE/PB）。
 * 主人 2026-10-27 定的目标：首屏 500ms 内看到关键数据。
 * 完整 F10 是 9 个串行请求（1.7s 到 8s+ 波动），不能挡在首屏前面。
 */
function f10QuickHtml(q) {
  const v = (q && q.val) || {};
  if (!q) return '<div class="klm-loading">估值载入中…</div>';
  if (!v.pe_ttm && !v.total_mcap) return '<div class="klm-loading">估值载入中…</div>';
  const rows = [];
  const kv = (k, val, cls) => rows.push(
    '<span class="k">' + k + '</span><span class="v' + (cls ? ' ' + cls : '') + '">' + val + '</span>');
  kv('总市值', v.total_mcap == null ? '—' : (Number(v.total_mcap) / 1e8).toFixed(0) + ' 亿');
  kv('PE(TTM)', fmt(v.pe_ttm)
    + (v.pe_pct == null ? '' : ' <span class="k">(' + fmt(v.pe_pct, 0) + '%分位)</span>'));
  kv('PB', fmt(v.pb_mrq)
    + (v.pb_pct == null ? '' : ' <span class="k">(' + fmt(v.pb_pct, 0) + '%分位)</span>'));
  return '<h4>F10 基础信息 <span class="st">' + esc(agoText(q.fetched_at)) + '</span></h4>'
    + '<div class="klm-kv">' + rows.join('') + '</div>'
    + '<div class="klm-sec klm-note">其余段载入中…（右键可刷新）</div>';
}

function f10Html(d) {
  if (!d) return '<div class="klm-loading">F10 载入中…</div>';
  if (d.ok === false) return '<div class="klm-err">F10 不可用：' + esc(d.detail || '未知原因') + '</div>';
  const rows = [];
  const kv = (k, v, cls) => rows.push(
    '<span class="k">' + k + '</span><span class="v' + (cls ? ' ' + cls : '') + '">' + v + '</span>');

  kv('行业', esc(d.sector || '—') || '—');
  kv('总市值', d.total_mcap == null ? '—' : (Number(d.total_mcap) / 1e8).toFixed(0) + ' 亿');
  kv('PE(TTM)', fmt(d.pe_ttm) + (d.pe_pct == null ? '' : ' <span class="k">(' + fmt(d.pe_pct, 0) + '%分位)</span>'));
  kv('PB', fmt(d.pb_mrq) + (d.pb_pct == null ? '' : ' <span class="k">(' + fmt(d.pb_pct, 0) + '%分位)</span>'));
  kv('上市日', esc(d.ipo_date || '—'));
  kv('股东户数', d.holders == null ? '—' : Number(d.holders).toLocaleString()
    + (d.holder_report ? ' <span class="k">' + esc(d.holder_report.slice(5)) + '</span>' : ''));
  kv('主力今日', d.main_net_today == null ? '—' : (Number(d.main_net_today) / 1e8).toFixed(2) + ' 亿',
    clsOf(d.main_net_today));
  kv('主力5日', d.main_net_5d == null ? '—' : (Number(d.main_net_5d) / 1e8).toFixed(2) + ' 亿',
    clsOf(d.main_net_5d));
  const north = d.north || null;
  if (north && north.ratio != null) {
    kv('北向持股', fmt(north.ratio) + '% <span class="k">' + esc(String(north.date || '').slice(5)) + '</span>');
  }
  const fc = d.eps_forecast || null;
  if (fc && fc.eps != null) {
    kv(fc.year + ' 预测EPS', fmt(fc.eps) + (fc.org_count ? ' <span class="k">' + fc.org_count + '家</span>' : ''));
  }

  let html = '<h4>F10 基础信息 <span class="st">' + esc(agoText(d.fetched_at)) + '</span></h4>'
    + '<div class="klm-kv">' + rows.join('') + '</div>';

  // 季度业绩（最近 4 期）。字段名是 `rq`（报告期），`net` 单位已是**亿元**
  // （实测 2026-06-30：net=172.74 → 172.74 亿），切勿再除以 1e8。
  const pq = d.perf_q;
  if (pq && Array.isArray(pq.periods) && pq.periods.length) {
    const last4 = pq.periods.slice(-4);
    const line = last4.map(function (p) {
      const net = p && p.net != null ? Number(p.net).toFixed(2) + '亿' : '—';
      const yoy = p && p.yoy_net != null ? ' (' + pctText(p.yoy_net) + ')' : '';
      return '<span class="k">' + esc(String((p && p.rq) || '').slice(2, 7)) + '</span> ' + net + yoy;
    });
    html += '<div class="klm-sec klm-note">季度净利：<br>' + line.join('<br>') + '</div>';
  }

  // 股东户数序列（最近 4 期），字段名 `rq`
  const gh = d.gdhs;
  if (gh && Array.isArray(gh.periods) && gh.periods.length) {
    const last4 = gh.periods.slice(-4);
    const line = last4.map(function (p) {
      const n = p && p.holders != null ? Number(p.holders).toLocaleString() : '—';
      const chg = p && p.chg_pct != null ? ' (' + pctText(p.chg_pct) + ')' : '';
      return '<span class="k">' + esc(String((p && p.rq) || '').slice(2, 7)) + '</span> ' + n + chg;
    });
    html += '<div class="klm-sec klm-note">股东户数：<br>' + line.join('<br>') + '</div>';
  }

  if (Array.isArray(d.errors) && d.errors.length) {
    html += '<div class="klm-sec klm-err">部分段缺失：' + esc(d.errors.join('；')) + '</div>';
  }
  return html;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

/**
 * 「数据是多久前的」：按主人要求给粗粒度（5分钟前 / 1小时前 / 1天前）。
 * 为什么不显示具体时刻：他只关心「新不新」，细到秒反而干扰读。
 * 拿不到时间（首次未取到）→ 返回空串，不编造。
 */
function agoText(ts) {
  if (!ts) return '';
  const t = Date.parse(String(ts).replace(' ', 'T'));
  if (!Number.isFinite(t)) return '';
  const mins = Math.max(0, Math.floor((Date.now() - t) / 60000));
  if (mins < 1) return '刚刚';
  if (mins < 60) return mins + ' 分钟前';
  const hours = Math.floor(mins / 60);
  if (hours < 24) return hours + ' 小时前';
  const days = Math.floor(hours / 24);
  return days + ' 天前';
}

// ---------------------------------------------------------------- 盘口渲染

function quoteHtml(q) {
  if (!q) return '<div class="klm-loading">盘口载入中…</div>';
  if (q.ok === false && !(q.asks || []).length && !(q.bids || []).length) {
    return '<h4>五档盘口</h4><div class="klm-note">不可用：' + esc(q.reason || '无盘口数据') + '</div>';
  }
  const rows = [];
  const asks = (q.asks || []).slice().reverse();
  asks.forEach(function (a, i) {
    rows.push('<tr data-price="' + (a.price == null ? '' : a.price) + '"><td class="lbl">卖' + (asks.length - i) + '</td>'
      + '<td class="num ask">' + fmt(a.price, 2) + '</td>'
      + '<td class="num">' + (a.vol == null ? '—' : Number(a.vol).toLocaleString()) + '</td></tr>');
  });
  (q.bids || []).forEach(function (b, i) {
    rows.push('<tr data-price="' + (b.price == null ? '' : b.price) + '"><td class="lbl">买' + (i + 1) + '</td>'
      + '<td class="num bid">' + fmt(b.price, 2) + '</td>'
      + '<td class="num">' + (b.vol == null ? '—' : Number(b.vol).toLocaleString()) + '</td></tr>');
  });
  const st = q.stats || {};
  return '<h4>五档盘口 <span class="st">' + esc(String(q.quote_time || '')) + '</span></h4>'
    + '<table class="klm-q5">' + rows.join('') + '</table>'
    + '<div class="klm-sec klm-kv">'
    + '<span class="k">换手</span><span class="v">' + (st.turnover == null ? '—' : fmt(st.turnover) + '%') + '</span>'
    + '<span class="k">量比</span><span class="v">' + fmt(st.vol_ratio) + '</span>'
    + '<span class="k">外盘</span><span class="v up">' + (st.outer == null ? '—' : Number(st.outer).toLocaleString()) + '</span>'
    + '<span class="k">内盘</span><span class="v down">' + (st.inner == null ? '—' : Number(st.inner).toLocaleString()) + '</span>'
    + '</div>';
}

// ---------------------------------------------------------------- 主流程

/** compat `/api/kline` 的 bar（t/o/h/l/c/v…）→ 本图通用 bar（date/open/high/low/close…）。 */
function toChartBars(list) {
  return (list || []).map(function (b) {
    const t = String((b && b.t) || '');
    return {
      date: t,                                  // 日线=YYYYMMDD，分钟=YYYYMMDD HH:MM（原样展示）
      open: Number(b && b.o), high: Number(b && b.h), low: Number(b && b.l), close: Number(b && b.c),
      volume: b && b.v, amount: b && b.amount,
      yellow: b && b.yellow, white: b && b.white,
      // AMV 三件必须一起带出来（首版只传 amv，副图均线因此恒空）
      amv: b && b.amv, amvMa: b && b.amvMa, amvWhite: b && b.amvWhite,
    };
  }).filter(function (b) { return Number.isFinite(b.close); });
}

async function loadCandle(code, period, canvas, footer) {
  const spec = PERIODS.find(function (p) { return p.key === period; });
  if (!spec || !spec.compat) throw new Error('未支持的周期：' + period);
  // 板块/指数身份码（.TI）在 compat 只有日/周（source=ths_index.db），分钟档 400：
  // 不把身份码往错通道送，直接给出可见原因。
  if (/\.TI$/i.test(code) && !(period === 'd' || period === 'w')) {
    throw new Error('板块指数只有日线/周线，无分钟数据（' + code + '）');
  }
  const url = '/api/kline/' + encodeURIComponent(code) + '?period=' + spec.compat + '&limit=' + CANDLE_LIMIT;
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const j = await r.json();
  const bars = toChartBars(j && j.bars);
  if (!bars.length) throw new Error((j && j.notice) || '该周期无 K 线数据');
  let states = null;
  if (period === 'd') {
    try {
      const bg = await fetch('/api/p/kline/bg/' + encodeURIComponent(code) + '?limit=120', { cache: 'no-store' });
      if (bg.ok) { const bj = await bg.json(); states = bj.states || null; }
    } catch (e) { states = null; }
  }
  // 注意：这里**不画**。绘制统一在 runChart 里、序号验过之后做，
  // 否则慢请求回来会拿旧数据把新周期的图盖掉（P1c 返修重点）。
  const minute = /^m\d+$/.test(period);
  return {
    kind: 'daily',            // 图形类型统一：所有蜡烛周期共用日线图与日线轴
    period: period,           // 周期本身靠这个字段区分（不靠 kind）
    bars: bars, states: states, raw: j,
    adjustLabel: minute ? '不复权（compat 原始分钟）' : (j.adj === 'front' ? '前复权' : String(j.adj || '—')),
    sourceLabel: j.source || '—',
    label: spec.label,
  };
}

async function loadChart(api, code, period, canvas, footer, overlay) {
  footer.badge.className = 'klm-badge';
  footer.badge.textContent = '载入中';
  footer.text.textContent = code;
  if (period !== 'intraday') return loadCandle(code, period, canvas, footer);
  const d = await api.get('/intraday/' + encodeURIComponent(code));
  return { kind: 'intraday', d: d };
}

/**
 * 打开 K 线弹窗。
 * @param {object} o {api, code, name, period}
 *   api 由 shell 的 makeCtx 提供（`api.get` 走 /api/p/kline，与卡内同一条通道）。
 */
export async function openKline(o) {
  ensureCss();
  const api = o.api;
  const code = String(o.code || '').trim();
  if (!api || !code) throw new Error('openKline 需要 api 与 code');

  // 单例：已有弹窗先关掉（旧版也是单窗，避免叠三层）
  closeKline();

  const root = document.createElement('div');
  root.id = MODAL_ID;
  root.className = 'klm-scrim';
  root.innerHTML = ''
    + '<div class="klm-wrap">'
    + '  <div class="klm-box">'
    + '    <div class="klm-hd">'
    + '      <span class="klm-title"></span><span class="klm-code"></span>'
    + '      <span class="klm-grp" data-role="period"></span>'
    + '      <button type="button" class="klm-x" aria-label="关闭">✕</button>'
    + '    </div>'
    + '    <div class="klm-cv"><canvas class="klm-canvas"></canvas><canvas class="klm-mk"></canvas>'
    + '      <canvas class="klm-cross"></canvas><div class="klm-labels" hidden></div><div class="klm-tip"></div>'
    + '      <span class="klm-grip" title="拖动调整大小"></span></div>'
    + '    <div class="klm-subs" hidden></div>'
    + '    <div class="klm-ft"><span class="klm-badge"></span><span class="klm-stat"></span>'
    + '      <span class="klm-ov" hidden></span><span class="klm-hint"></span></div>'
    + '  </div>'
    + '  <aside class="klm-side" data-role="quote"></aside>'
    + '  <aside class="klm-side" data-role="f10"></aside>'
    + '</div>';

  document.body.append(root);

  const el = {
    box: root.querySelector('.klm-box'),
    title: root.querySelector('.klm-title'),
    code: root.querySelector('.klm-code'),
    period: root.querySelector('[data-role="period"]'),
    canvas: root.querySelector('.klm-canvas'),
    mk: root.querySelector('.klm-mk'),
    cv: root.querySelector('.klm-cv'),
    grip: root.querySelector('.klm-grip'),
    hint: root.querySelector('.klm-hint'),
    ov: root.querySelector('.klm-ov'),
    subs: root.querySelector('.klm-subs'),
    cross: root.querySelector('.klm-cross'),
    labels: root.querySelector('.klm-labels'),
    tip: root.querySelector('.klm-tip'),
    badge: root.querySelector('.klm-badge'),
    stat: root.querySelector('.klm-stat'),
    quote: root.querySelector('[data-role="quote"]'),
    f10: root.querySelector('[data-role="f10"]'),
  };
  el.title.textContent = o.name || code;
  el.code.textContent = code;

  // ---------------------------------------------------------------- 标记层 / 尺寸 / 右键菜单
  // 口径全在 ./marks.js（纯函数）；本段只做 DOM、canvas 与事件。

  /** localStorage 不可用（隐私模式/沙箱）时退回内存：功能照用，只是不持久，不静默吞掉菜单。 */
  const memStore = (function () {
    const m = {};
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null; },
      setItem: function (k, v) { m[k] = String(v); },
    };
  })();
  function store() {
    try {
      window.localStorage.getItem('klm_probe');
      return window.localStorage;
    } catch (e) { return memStore; }
  }

  const marks = readMarks(store());        // {bs,cost,order,cond}，key=ko_marks（沿用旧版）
  const subs = readSubs(store());          // 副图开关，key=klm_subs_v1（弹窗独立）
  let labels = readLabels(store(), code);  // 本票标签，key=klm_labels_v1（2026-11-04，按代码存）
  let labelsOn = readLabelsOn(store());    // 标签显示开关，key=klm_labels_on_v1
  let hlines = readHlines(store(), code);  // 本图手工水平线，key=klm_hlines_v1
  let size = readSize(store());            // 弹窗尺寸，key=klm_size_v1；null = 用 CSS 默认
  // 标记数据：P1a 阶段四项都空（没有数据源）。P1b 用返回的 handle.setMarkData() 灌。
  const markData = { bs: [], cost: null, order: [], cond: [] };

  function applySize() {
    const s = clampSize(size, viewport());
    if (!s) { el.box.style.width = ''; el.cv.style.height = ''; return; }
    // 真改容器尺寸：canvas 是 height:100%，clientHeight/clientWidth 跟着变，重画才有意义
    el.box.style.width = s.w + 'px';
    el.cv.style.height = s.h + 'px';
  }

  /** 视口尺寸：innerWidth 在部分内嵌 webview/隐藏标签页会报 0，回落 documentElement。 */
  function viewport() {
    const de = document.documentElement || {};
    return {
      w: window.innerWidth || de.clientWidth || 0,
      h: window.innerHeight || de.clientHeight || 0,
    };
  }

  let toastEl = null, toastTimer = 0;
  function toast(msg) {
    if (toastEl) toastEl.remove();
    toastEl = document.createElement('div');
    toastEl.className = 'klm-toast';
    toastEl.textContent = msg;
    document.body.append(toastEl);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { if (toastEl) { toastEl.remove(); toastEl = null; } }, 1600);
  }

  /** 主题 token 现取（canvas 读不到 CSS 变量）；本文件不写死 hex 色值。 */
  function tok(name, fb) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fb;
    } catch (e) { return fb; }
  }
  const MARK_COLOR = { cost: '--warn-solid', order: '--accent', cond: '--dim' };

  /** 买卖点落横轴序号：分时按 t 对时间轴，日线按 date 对日期轴（P1a 无数据，仅接口就位）。 */
  function indexOfPoint(pt) {
    if (!st.cache) return null;
    if (st.cache.kind === 'intraday') {
      const slots = ((st.cache.d || {}).session || {}).slots || [];
      const i = slots.indexOf(pt.at);
      return i >= 0 ? i : null;
    }
    const bars = st.cache.bars || [];
    for (let i = 0; i < bars.length; i++) if (bars[i].date === pt.at) return i;
    return null;
  }

  // 标记层：画在主图上层（pointer-events:none，不抢点击）。只画「开关开着 + 有数据」的。
  function drawMarks() {
    const cv = el.mk;
    const w = el.canvas.clientWidth || 0, h = el.canvas.clientHeight || 0;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(1, Math.round(w * dpr));
    cv.height = Math.max(1, Math.round(h * dpr));
    const c = cv.getContext('2d');
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.scale(dpr, dpr); c.clearRect(0, 0, w, h);
    const axisKind = (st.cache && st.cache.kind === 'intraday') ? 'intraday' : 'daily';
    const sc = st.cache ? scaleFor(axisKind, st.cache, w, h, overlayExtra()) : null;
    if (!sc) return;

    const model = lineModels(marks, markData);
    model.lines.forEach(function (ln) {
      const y = sc.yOf(ln.price);
      if (y === null || y < sc.rect.t - 2 || y > sc.rect.b + 2) return;
      const col = tok(MARK_COLOR[ln.key] || '--dim', 'currentColor');
      c.strokeStyle = col; c.globalAlpha = .75; c.lineWidth = 1;
      c.setLineDash([5, 4]);
      c.beginPath(); c.moveTo(sc.rect.l, y); c.lineTo(sc.rect.r, y); c.stroke();
      c.setLineDash([]); c.globalAlpha = 1;
      c.font = '10px ' + tok('--font-mono', 'monospace');
      c.fillStyle = col; c.textAlign = 'right';
      c.fillText(ln.label, sc.rect.r - 2, Math.max(sc.rect.t + 8, y - 3));
      c.textAlign = 'left';
    });

    if (model.points.length) {
      const n = st.cache.kind === 'daily'
        ? (st.cache.bars || []).length : (((st.cache.d || {}).points || []).length);
      c.font = '10px ' + tok('--font-mono', 'monospace');
      model.points.forEach(function (pt) {
        const idx = indexOfPoint(pt);
        if (idx === null) return;
        const y = sc.yOf(pt.price);
        if (y === null) return;
        const x = sc.xAt(idx, n);
        const sell = pt.side === 'SELL';
        c.fillStyle = tok(sell ? '--down-solid' : '--up-solid', 'currentColor');
        c.beginPath(); c.arc(x, y, 3, 0, Math.PI * 2); c.fill();
        c.fillText((sell ? 'S' : 'B') + pt.price.toFixed(2), x + 4, y - 5);
      });
    }

    // 条件单触发价预览（面板输入时实时画，不写入任何委托）
    if (st.condPreview) {
      const yc = sc.yOf(st.condPreview);
      if (yc !== null && yc >= sc.rect.t - 2 && yc <= sc.rect.b + 2) {
        const col = tok('--warn-solid', 'currentColor');
        c.strokeStyle = col; c.globalAlpha = 0.9; c.lineWidth = 1;
        c.setLineDash([2, 3]);
        c.beginPath(); c.moveTo(sc.rect.l, yc); c.lineTo(sc.rect.r, yc); c.stroke();
        c.setLineDash([]); c.globalAlpha = 1;
        c.font = '10px ' + tok('--font-mono', 'monospace');
        c.fillStyle = col; c.textAlign = 'right';
        c.fillText('条件预览 ' + Number(st.condPreview).toFixed(2), sc.rect.r - 2, Math.max(sc.rect.t + 8, yc - 3));
        c.textAlign = 'left';
      }
    }

    // 手工线用实线 + 左端标价，和数据线（虚线）一眼分得开
    hlines.forEach(function (p) {
      const y = sc.yOf(p);
      if (y === null || y < sc.rect.t - 2 || y > sc.rect.b + 2) return;
      const col = tok('--accent', 'currentColor');
      c.strokeStyle = col; c.globalAlpha = .9; c.lineWidth = 1;
      c.beginPath(); c.moveTo(sc.rect.l, y); c.lineTo(sc.rect.r, y); c.stroke();
      c.globalAlpha = 1;
      c.font = '10px ' + tok('--font-mono', 'monospace');
      c.fillStyle = col; c.textAlign = 'left';
      c.fillText(p.toFixed(2), sc.rect.l + 2, Math.max(sc.rect.t + 8, y - 3));
      c.textAlign = 'left';
    });
  }

  function redrawChips() {
    if (!st.chip || st.closed) return;
    drawChips(st.chip.canvas, st.chip.vp, st.lastIntraday || null);
  }

  /** 尺寸变化走这里：拿缓存重画，不重取数据（原 resize 每次都重取却仍按旧尺寸画）。 */
  // 动能K 染色（2026-11-02）：弹窗独立 key，不往 KLineApp.tools 里塞 ——
  // 那套 tools 是旧端共享的（adjust/bs 等），加字段会连带影响旧 8800 端。
  const DN_KEY = 'klm_dn_v1';
  function readDongneng() {
    try { return localStorage.getItem(DN_KEY) === '1'; } catch (e) { return false; }
  }
  function setDongneng(on) {
    try { localStorage.setItem(DN_KEY, on ? '1' : '0'); } catch (e) { /* 隐私模式：本次不持久化 */ }
  }
  // 算子结果按当前 bars 懒算并缓存（换票/换周期只会产生新的 bars 对象，=== 就失效）
  let dnCache = null;
  function dongnengOf(bars) {
    if (!readDongneng() || !bars || !bars.length) return null;
    if (dnCache && dnCache.bars === bars) return dnCache.val;
    let val = null;
    try { val = IND.dongneng(bars); } catch (e) { val = null; }
    dnCache = { bars: bars, val: val };
    return val;
  }
  function toggleDongneng() {
    const on = !readDongneng();
    setDongneng(on);
    dnCache = null;
    renderCache();
    toast('主图设置：动能K染色 → ' + (on ? '开' : '关'));
  }

  function renderCache() {
    if (!st.cache) return;
    if (st.cache.kind === 'intraday') drawIntraday(el.canvas, st.cache.d, overlayPayload());
    else drawDaily(el.canvas, st.cache.bars, st.cache.states, { dn: dongnengOf(st.cache.bars) });
    drawMarks();
    renderSubs();
    redrawChips();
  }

  // ---------------------------------------------------------------- 叠加对比（P1b）
  // 口径：只做分时；主图与叠加各自按昨收换算成 %，共用 marks.js::intradayAxis 那根对称轴。
  // 一次只保留一条叠加（换来源就替换），清除后恢复纯主图。

  function overlayPayload() {
    if (!st.overlay) return null;
    return { series: [{ label: st.overlay.name || st.overlay.code,
                        color: st.overlay.color, pct: st.overlay.pct }] };
  }
  function overlayExtra() {
    return st.overlay ? overlayExtent([st.overlay]) : null;
  }

  /** 底部叠加状态条：正常显示「叠加：名字 ×」，失败显示「叠加失败 · 重试」并带原因。 */
  function paintOvChip() {
    if (!el.ov) return;
    if (st.overlayErr) {
      el.ov.hidden = false;
      el.ov.className = 'klm-ov is-err';
      el.ov.textContent = '叠加失败 · 重试';
      el.ov.title = st.overlayErr;
      return;
    }
    if (st.overlay) {
      el.ov.hidden = false;
      el.ov.className = 'klm-ov';
      el.ov.textContent = '叠加：' + (st.overlay.name || st.overlay.code) + ' ×';
      el.ov.title = '点一下清除叠加';
      return;
    }
    el.ov.hidden = true;
    el.ov.className = 'klm-ov';
    el.ov.textContent = '';
    el.ov.title = '';
  }

  /**
   * 取一条叠加序列并对齐到主图时间槽。
   * 竞态：每次请求带序号，晚到的响应直接丢（切周期/连续切换不写回旧数据）。
   * 失败只报错、不清主图：主图数据在 st.cache 里，独立于叠加。
   */
  async function loadOverlay(kind, codeArg, nameArg, color) {
    // 行业码适配只在这一层：THS 的 88[01456]xxx.TI 身份码 → 同 6 位 .SH 走 eltdx 分时通道；
    // 请求码与展示码分开（展示仍用 .TI 原始身份），不碰全局 normalize。
    const map = mapOverlayCode(codeArg);
    if (!map) { toast('未指定叠加代码'); return; }
    const code2 = map.input;
    if (st.period !== 'intraday' || !st.cache || st.cache.kind !== 'intraday') {
      toast('仅分时支持叠加（日线无昨收基准）');
      return;
    }
    const slots = ((st.cache.d || {}).session || {}).slots || [];
    st.ovPending = { kind: kind, code: code2, name: nameArg || code2, color: color, req: map.req };
    const seq = ++st.oseq;
    try {
      const j = await api.get('/intraday/' + encodeURIComponent(map.req));
      if (st.closed || seq !== st.oseq) return;
      if (!j || j.ok === false) throw new Error((j && j.reason) || '叠加品种无分时数据');
      const pct = alignPctToSlots(j, slots);
      if (!pct) throw new Error('无法按时间槽对齐（缺昨收或无有效点）');
      st.overlay = { kind: kind, code: code2, req: map.req, name: nameArg || code2, color: color, pct: pct };
      st.overlayErr = '';
      renderCache();
      paintOvChip();
      toast('已叠加 ' + (nameArg || code2));
    } catch (e) {
      if (st.closed || seq !== st.oseq) return;
      st.overlayErr = String((e && e.message) ? e.message : e);
      paintOvChip();
      toast('叠加失败：' + st.overlayErr);
    }
  }

  function clearOverlayUi(msg) {
    st.oseq++;
    st.overlay = null;
    st.overlayErr = '';
    st.ovPending = null;
    paintOvChip();
    renderCache();
    if (msg) toast(msg);
  }

  function retryOverlay() {
    const p = st.ovPending;
    st.overlayErr = '';
    paintOvChip();
    if (!p) { toast('没有可重试的叠加请求'); return; }
    loadOverlay(p.kind, p.req || p.code, p.name, p.color);
  }

  /** 自定义代码：沿用旧版「输入代码」方式（prompt），取消给可见反馈。 */
  function askCustomCode() {
    let q = null;
    try { q = window.prompt('输入要叠加的代码（如 600519.SH / 512480.SH / 881121.SH）', ''); }
    catch (e) { q = null; }
    const v = String(q || '').trim().toUpperCase();
    if (!v) { toast('已取消输入'); return null; }
    return v;
  }

  /** 本票行业映射：watchlist.json 里 items[].sector.l2（老格式 sector_of 也兼容）。缺就留 null，不猜。 */
  async function loadSectorMap() {
    try {
      const r = await fetch('/api/watchlist.json', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      const six = String(st.code || '').slice(0, 6);
      const so = (j && j.sector_of) || null;
      let code3 = so ? (so[six] || so[st.code] || null) : null;
      let name3 = '';
      if (!code3 && Array.isArray(j && j.items)) {
        const hit = j.items.find(function (x) { return String((x && x.code) || '').slice(0, 6) === six; });
        const l2 = hit && hit.sector && hit.sector.l2;
        if (l2 && l2.code) { code3 = l2.code; name3 = l2.name || ''; }
      }
      st.sectorCode = code3 || null;
      st.sectorName = name3;
      st.sectorErr = '';
    } catch (e) {
      st.sectorCode = null;
      st.sectorErr = String((e && e.message) ? e.message : e);
    }
  }

  // ---------------------------------------------------------------- 纸面只读标记（P1b）
  // 口径：买卖点/成本取 /api/trades（成本只认 paper），委托与条件单取 /api/order/list。
  // 「0 条」与「请求失败」分开说；失败只影响该类标记，不动主图。

  function markReasons() {
    const r = {};
    if (st.tradesState === 'fail') {
      r.bs = '成交请求失败：' + st.tradesErr + '（右键→刷新行情可重试）';
      r.cost = '纸面成本请求失败：' + st.tradesErr;
    } else {
      r.bs = markData.bs.length ? '' : '近 120 日无纸面成交';
      r.cost = markData.cost == null ? 'paper 无持仓成本' : '';
    }
    if (st.ordersState === 'fail') {
      r.order = '委托请求失败：' + st.ordersErr;
      r.cond = '条件单请求失败：' + st.ordersErr;
    } else {
      r.order = markData.order.length ? '' : '当前无 REPORTED/PARTIAL 委托';
      r.cond = markData.cond.length ? '' : '当前无 ACTIVE 条件单';
    }
    return r;
  }

  async function loadPaper() {
    const seq = ++st.pseq;
    let trades = null, orders = null;
    try {
      const r = await fetch('/api/trades/' + encodeURIComponent(st.code) + '?days=120', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      trades = await r.json();
      st.tradesErr = '';
    } catch (e) { st.tradesErr = String((e && e.message) ? e.message : e); }
    try {
      const r = await fetch('/api/order/list', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      orders = await r.json();
      st.ordersErr = '';
    } catch (e) { st.ordersErr = String((e && e.message) ? e.message : e); }
    if (st.closed || seq !== st.pseq) return { ok: false, reason: '请求已被新请求取代' };
    if (!orders) { st.account = null; st.accountErr = st.ordersErr || '委托/账户请求失败'; }
    else { st.accountErr = ''; }
    const d = paperMarkData(st.code, trades, orders);
    st.tradesState = d.failed.trades ? 'fail' : (d.bs.length ? 'ok' : 'empty');
    st.ordersState = d.failed.orders ? 'fail' : ((d.order.length || d.cond.length) ? 'ok' : 'empty');
    markData.bs = d.bs;
    markData.cost = d.cost;                       // 只取 paper，绝不回退 live
    markData.order = d.order;
    markData.cond = d.cond;
    // P2：面板与「记录」页要原始列表（只读），不是筛完的标记
    st.ordersRaw = (orders && Array.isArray(orders.orders)) ? orders.orders : [];
    st.condsRaw = (orders && Array.isArray(orders.conditions)) ? orders.conditions : [];
    if (orders) st.account = orders.account || null;
    st.cost = (trades && trades.cost) || null;
    drawMarks();
    if (st.panel) st.panel.refresh();
    if (menuEl) { closeMenu(); }                  // 菜单开着就关掉，避免显示旧状态
    return { ok: !st.tradesErr && !st.ordersErr,
             reason: st.tradesErr || st.ordersErr || '' };
  }

  // ---------------------------------------------------------------- 十字线 + tooltip（P1c）
  // 坐标只走唯一轴（dailyAxis / intradayAxis）：x 由轴的 xAt 反推索引，y 由 yOf/priceAtY 换算，
  // 不另起一套比例尺（否则十字线会与蜡烛、标记线错位）。

  function crossAxis() {
    if (!st.cache) return null;
    const kind = st.cache.kind === 'intraday' ? 'intraday' : 'daily';
    return scaleFor(kind, st.cache, el.canvas.clientWidth, el.canvas.clientHeight);
  }

  /** 屏幕 x → 数据索引；越界返回 null（不吸附到端点，避免读出不存在的那根）。 */
  function indexAtX(ax, x) {
    const n = (st.cache && st.cache.kind === 'intraday')
      ? ((st.cache.d && st.cache.d.session && st.cache.d.session.slots || []).length || (st.cache.d && st.cache.d.points || []).length)
      : ((st.cache.bars || []).length);
    if (!n) return null;
    if (x < ax.rect.l - 2 || x > ax.rect.r + 2) return null;
    const i = n === 1 ? 0 : Math.round((x - ax.rect.l) / (ax.rect.r - ax.rect.l) * (n - 1));
    return Math.max(0, Math.min(n - 1, i));
  }

  function clearCross() {
    if (!el.cross) return;
    const c = el.cross.getContext('2d');
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, el.cross.width, el.cross.height);
    el.tip.style.display = 'none';
  }

  function tipHtml(idx) {
    const isIntra = st.cache.kind === 'intraday';
    const bars = st.cache.bars || [];
    const pts = (st.cache.d && st.cache.d.points) || [];
    const slots = (st.cache.d && st.cache.d.session && st.cache.d.session.slots) || [];
    const label = isIntra ? (slots[idx] || '—') : (bars[idx] ? bars[idx].date : '—');
    const rows = [];
    if (isIntra) {
      const p = pts[idx] || {};
      const pre = st.cache.d && st.cache.d.pre_close;
      const pct = (pre > 0 && Number.isFinite(Number(p.close))) ? (p.close / pre - 1) * 100 : null;
      rows.push('价 ' + fmt(p.close) + (pct === null ? '' : '  ' + pctText(pct)));
      rows.push('均 ' + fmt(p.avg));
      rows.push('量 ' + (p.volume == null ? '—' : Number(p.volume).toLocaleString()));
      rows.push('额 ' + (p.amount == null ? '—' : Number(p.amount).toLocaleString()));
    } else {
      const b = bars[idx] || {};
      const prev = idx > 0 ? Number(bars[idx - 1].close) : NaN;
      const pct = Number.isFinite(prev) && prev > 0 ? (b.close / prev - 1) * 100 : null;
      rows.push('开 ' + fmt(b.open) + '  高 ' + fmt(b.high));
      rows.push('低 ' + fmt(b.low) + '  收 ' + fmt(b.close));
      rows.push('涨跌 ' + (pct === null ? '—' : pctText(pct)));
      rows.push('量 ' + (b.volume == null ? '—' : Number(b.volume).toLocaleString()));
      rows.push('额 ' + (b.amount == null ? '—' : Number(b.amount).toLocaleString()));
      // 当前副图值（开关开着的才算）
      if (subs.kdj && subAvailable('kdj')) {
        const k = IND.kdj(bars); rows.push('KDJ ' + fmt(k.k[idx]) + ' ' + fmt(k.d[idx]) + ' ' + fmt(k.j[idx]));
      }
      if (subs.macd && subAvailable('macd')) {
        const m = IND.macd(bars); rows.push('MACD ' + fmt(m.dif[idx]) + ' ' + fmt(m.dea[idx]) + ' ' + fmt(m.hist[idx]));
      }
      if (subs.rsi && subAvailable('rsi')) {
        const r = IND.rsi(bars); rows.push('RSI ' + (r.periods || []).map(function (n) { return fmt(r['rsi' + n][idx]); }).join(' '));
      }
      if (subs.amv && subAvailable('amv')) rows.push('AMV ' + fmt(IND.amv(bars).amv[idx]));
    }
    return '<b>' + esc(String(label)) + '</b><br>' + rows.map(function (r) { return esc(r); }).join('<br>');
  }

  function onMove(ev) {
    if (!st.cache) { clearCross(); return; }
    const ax = crossAxis();
    if (!ax) { clearCross(); return; }
    const w = el.canvas.clientWidth, h = el.canvas.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    el.cross.width = Math.round(w * dpr);
    el.cross.height = Math.round(h * dpr);
    const c = el.cross.getContext('2d');
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.scale(dpr, dpr);
    c.clearRect(0, 0, w, h);
    const idx = indexAtX(ax, ev.offsetX);
    if (idx === null) { el.tip.style.display = 'none'; return; }
    const n = (st.cache.kind === 'intraday')
      ? ((st.cache.d && st.cache.d.session && st.cache.d.session.slots || []).length || ((st.cache.d && st.cache.d.points) || []).length)
      : (st.cache.bars || []).length;
    const x = ax.xAt(idx, n);
    const y = Math.max(ax.rect.t, Math.min(ax.rect.b, ev.offsetY));
    c.strokeStyle = tok('--dim', 'currentColor');
    c.globalAlpha = 0.7; c.lineWidth = 1;
    c.beginPath(); c.moveTo(x, ax.rect.t); c.lineTo(x, ax.rect.b); c.stroke();
    c.beginPath(); c.moveTo(ax.rect.l, y); c.lineTo(ax.rect.r, y); c.stroke();
    c.globalAlpha = 1;
    el.tip.innerHTML = tipHtml(idx);
    el.tip.style.display = 'block';
    const tw = el.tip.offsetWidth || 160;
    el.tip.style.left = Math.max(4, Math.min(w - tw - 4, x + 12)) + 'px';
    el.tip.style.top = '6px';
  }

  el.canvas.addEventListener('mousemove', onMove);
  el.canvas.addEventListener('mouseleave', clearCross);

  // ---------------------------------------------------------------- 副图（P1c）
  // 只画「开关开着 + 该周期真有数据」的；数据缺失就画一行原因，不画 0 线（判据在 subcharts 内）。

  function subAvailable(key) {
    if (!st.cache || st.cache.kind !== 'daily') return false;   // 图形类型：蜡烛周期统一为 daily
    if (key === 'amv' || key === 'mf') return st.period === 'd'; // AMV / 主力动向只有日序数据
    return (st.cache.bars || []).length > 0;
  }

  function subSeriesAll() {
    const bars = (st.cache && st.cache.bars) || [];
    return {
      amount: IND.amounts(bars),
      amv: IND.amv(bars),
      mf: IND.mf(bars, st.cache.raw || null),
      kdj: IND.kdj(bars),
      macd: IND.macd(bars),
      rsi: IND.rsi(bars),
    };
  }

  function renderSubs() {
    if (!el.subs) return;
    el.subs.innerHTML = '';
    if (!st.cache || st.cache.kind !== 'daily') { el.subs.hidden = true; return; }
    const w = el.canvas.clientWidth, h = el.canvas.clientHeight;
    const axis = scaleFor('daily', st.cache, w, h);
    if (!axis) { el.subs.hidden = true; return; }
    const colors = {
      up: tok('--up-solid', 'currentColor'), down: tok('--down-solid', 'currentColor'),
      fg: tok('--fg', 'currentColor'), dim: tok('--dim', 'currentColor'),
      warn: tok('--warn-solid', 'currentColor'), grid: tok('--line-soft', 'transparent'),
      mono: tok('--font-mono', 'monospace'),
    };
    const series = subSeriesAll();
    let shown = 0;
    SUB_DEFS.forEach(function (d) {
      if (!subs[d.key] || !subAvailable(d.key)) return;
      const cv = document.createElement('canvas');
      cv.className = 'klm-sub-cv';
      cv.dataset.key = d.key;
      el.subs.append(cv);
      drawSub(cv, d.key, { bars: st.cache.bars, axis: axis, colors: colors, series: series });
      shown++;
    });
    el.subs.hidden = shown === 0;
  }

  function subMenuItems() {
    return SUB_DEFS.map(function (d) {
      const ok = subAvailable(d.key);
      return {
        id: 'sub_' + d.key, label: d.label, checked: !!subs[d.key],
        disabled: !ok,
        reason: ok ? '' : ((d.key === 'amv' || d.key === 'mf')
          ? '仅日线有' + (d.key === 'amv' ? '活跃市值' : '主力动向') + '数据'
          : '仅蜡烛周期有副图（分时无副图数据）'),
      };
    });
  }

  // ---------------------------------------------------------------- 纸面下单面板（P2）
  // 只装配：数据从只读接口来，规则在 order_model.js，DOM 在 order_panel.js。

  function panelAccount() {
    const a = st.account || {};
    // 权威空持仓：positions 是对象但没有本票 → {qty:0, available_qty:0}；
    // 只有 positions 不是对象（请求失败/结构缺字段）才给 null（未知）
    const positions = a && a.positions;
    let pos = null;
    if (positions && typeof positions === 'object') {
      const raw = positions[st.code] || positions[String(st.code).slice(0, 6)] || null;
      pos = raw
        ? { qty: raw.qty == null ? 0 : Number(raw.qty),
            available_qty: raw.available_qty == null ? 0 : Number(raw.available_qty) }
        : { qty: 0, available_qty: 0 };
    }
    return {
      availCash: a.available_cash == null ? null : Number(a.available_cash),
      totalAsset: a.total_asset == null ? null : Number(a.total_asset),
      // 可卖只认 available_qty（T+1/冻结后口径）；无持仓对象才是 null
      position: pos,
    };
  }

  function panelData() {
    const q = st.quote || {};
    const bids = q.bids || [], asks = q.asks || [];
    const acc = panelAccount();
    return {
      quote: {
        last: q.last == null ? null : Number(q.last),
        bid1: bids[0] && bids[0].price != null ? Number(bids[0].price) : null,
        ask1: asks[0] && asks[0].price != null ? Number(asks[0].price) : null,
        limit_up: q.limit_up == null ? null : Number(q.limit_up),
        limit_down: q.limit_down == null ? null : Number(q.limit_down),
      },
      account: { avail_cash: acc.availCash, total_asset: acc.totalAsset },
      position: acc.position,
      orders: st.ordersRaw || [],
      conditions: st.condsRaw || [],
      cost: { paper: st.cost && st.cost.paper != null ? Number(st.cost.paper) : null },
    };
  }

  function postJson(path, body) {
    return fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) {
          const e = new Error((j && (j.detail || j.message)) || ('HTTP ' + r.status));
          e.detail = e.message;
          throw e;
        }
        return j;
      });
    });
  }

  function ensurePanel() {
    if (st.panel) return st.panel;
    st.panel = createOrderPanel({
      code: st.code,
      get name() { return o.name || st.code; },
      getData: panelData,
      post: postJson,
      onWritten: async function () {
        // 写成功后只刷新只读数据（委托/条件单/标记/记录），保留面板里未提交的输入。
        // **真等待**：面板的回执要等回读结束再显示，否则会出现「已成交」但列表还是旧的。
        try {
          const [paper] = await Promise.all([loadPaper(), runSides()]);
          return paper || { ok: true };
        } catch (e) {
          return { ok: false, reason: String((e && e.message) || e) };
        }
      },
      onCondPreview: function (price) {
        st.condPreview = (price == null || !(price > 0)) ? null : Number(price);
        drawMarks();
      },
      onClose: function () { st.condPreview = null; drawMarks(); },
    });
    return st.panel;
  }

  function openPanelWith(tab, price) {
    closeMenu();
    ensurePanel().open(tab, price);
  }

  // ---------------------------------------------------------------- 标签（备注 chip，2026-11-04）
  // 口径在 ./labels.js（纯函数）：按代码存、8 色 3 形态、上限 8 条。本段只做 DOM 与事件。
  // 位置：主图左下角（主人 2026-11-04：「位置尽量靠到最左边」）——日线 left:4px，分时右移到 62px 避开涨跌幅刻度。
  let labelMenuEl = null, labelPalEl = null, labelDlg = null;

  function periodLabel() {
    const p = PERIODS.find(function (x) { return x.key === st.period; });
    return p ? p.label : st.period;
  }

  /** 标签层只换 left 与显隐，不重建 chip（周期切换时调用）。 */
  function layoutLabels() {
    if (!el.labels) return;
    el.labels.dataset.period = (st.cache && st.cache.kind === 'intraday') ? 'intraday' : 'daily';
    el.labels.hidden = !labelsOn || !labels.length;
  }

  function renderLabels() {
    if (!el.labels) return;
    el.labels.innerHTML = '';
    layoutLabels();
    if (!labelsOn) return;
    labels.forEach(function (lb, i) {
      const n = document.createElement('span');
      n.className = 'klm-label is-' + lb.shape;
      n.style.setProperty('--c', lb.color);
      n.dataset.idx = String(i);
      n.title = lb.text + '（右键修改）';
      const dot = document.createElement('i');
      dot.className = 'klm-lab-dot';
      n.append(dot, document.createTextNode(lb.text));
      el.labels.append(n);
    });
  }

  function setLabels(next) {
    labels = Array.isArray(next) ? next : [];
    renderLabels();
  }

  function closeLabelMenu() {
    if (labelMenuEl) { labelMenuEl.remove(); labelMenuEl = null; }
    if (labelPalEl) { labelPalEl.remove(); labelPalEl = null; }
  }
  function closeLabelEditor() {
    if (labelDlg) { labelDlg.remove(); labelDlg = null; }
  }

  function openLabelMenu(idx, x, y) {
    closeMenu(); closeLabelMenu();
    const lb = labels[idx];
    if (!lb) return;
    labelMenuEl = document.createElement('div');
    labelMenuEl.className = 'klm-menu';
    labelMenuEl.innerHTML = '<div class="klm-mh">标签 · <b>' + esc(lb.text) + '</b></div>'
      + '<div class="klm-mi" data-id="lab_edit"><span class="lb">修改文字…</span><span class="tg">双击</span></div>'
      + '<div class="klm-mi" data-id="lab_style"><span class="lb">样式</span><span class="tg">▸</span></div>'
      + '<div class="klm-sep"></div>'
      + '<div class="klm-mi" data-id="lab_del" style="color:var(--danger-ink)">'
      + '<span class="lb">删除标签</span><span class="tg"></span></div>';
    document.body.append(labelMenuEl);
    placeAt(labelMenuEl, x, y);
    labelMenuEl.addEventListener('click', function (ev) {
      const it = ev.target.closest('[data-id]');
      if (!it) return;
      ev.stopPropagation();
      const id = it.getAttribute('data-id');
      if (id === 'lab_edit') { closeLabelMenu(); openLabelEditor(idx); return; }
      if (id === 'lab_style') { openLabelPalette(it, idx); return; }
      if (id === 'lab_del') {
        const r = removeLabel(store(), code, idx);
        closeLabelMenu();
        setLabels(r.labels);
        toast(r.ok ? ('已删除标签「' + ((r.removed && r.removed.text) || '') + '」') : r.reason);
      }
    });
  }

  /** 样式面板：色板 + 三种形态。选完不关面板（能连着试），点空白处收。 */
  function openLabelPalette(anchor, idx) {
    if (labelPalEl) { labelPalEl.remove(); labelPalEl = null; }
    const lb = labels[idx];
    if (!lb) return;
    const box = document.createElement('div');
    box.className = 'klm-menu klm-pal';
    box.innerHTML = '<h5>颜色</h5><div class="klm-sws"></div><h5>形态</h5><div class="klm-shapes"></div>';
    const sws = box.querySelector('.klm-sws');
    LABEL_COLORS.forEach(function (c) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'klm-sw' + (c === lb.color ? ' on' : '');
      b.style.background = c; b.dataset.color = c;
      sws.append(b);
    });
    const shp = box.querySelector('.klm-shapes');
    LABEL_SHAPES.forEach(function (k) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = (k === lb.shape ? 'on' : ''); b.dataset.shape = k;
      b.textContent = SHAPE_LABEL[k] || k;
      shp.append(b);
    });
    document.body.append(box);
    const r = anchor.getBoundingClientRect();
    placeAt(box, r.right - 4, r.top);
    labelPalEl = box;
    box.addEventListener('click', function (ev) {
      ev.stopPropagation();
      const sw = ev.target.closest('.klm-sw');
      if (sw) {
        const rr = updateLabel(store(), code, idx, { color: sw.dataset.color });
        if (!rr.ok) { toast(rr.reason); return; }
        setLabels(rr.labels);
        sws.querySelectorAll('.klm-sw').forEach(function (x) { x.classList.toggle('on', x === sw); });
        return;
      }
      const sb = ev.target.closest('.klm-shapes button');
      if (sb) {
        const rr = updateLabel(store(), code, idx, { shape: sb.dataset.shape });
        if (!rr.ok) { toast(rr.reason); return; }
        setLabels(rr.labels);
        shp.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === sb); });
      }
    });
  }

  /** 新建/修改标签浮层：文字 + 颜色 + 形态 + 实时预览。 */
  function openLabelEditor(idx) {
    closeLabelMenu(); closeMenu(); closeLabelEditor();
    const editing = (idx === null || idx === undefined) ? null : labels[idx];
    if (idx !== null && idx !== undefined && !editing) return;
    let color = editing ? editing.color : LABEL_DEFAULT_COLOR;
    let shape = editing ? editing.shape : 'fill';

    const wrap = document.createElement('div');
    wrap.className = 'klm-dlg-scrim';
    wrap.innerHTML = '<div class="klm-dlg" role="dialog">'
      + '<div class="klm-dlg-h">' + (editing ? '修改标签' : '新建标签')
      + '<span>' + esc(code) + ' · ' + esc(periodLabel()) + '</span></div>'
      + '<input class="klm-dlg-in" maxlength="' + LABEL_TEXT_MAX + '" placeholder="写点什么，例如：竞价异动 高开3.2%">'
      + '<div class="klm-dlg-row"><span class="k">颜色</span><div class="klm-sws"></div></div>'
      + '<div class="klm-dlg-row"><span class="k">形态</span><div class="klm-shapes"></div></div>'
      + '<div class="klm-dlg-pv"><span class="k">预览</span><span class="klm-label"></span></div>'
      + '<div class="klm-dlg-ft"><button type="button" class="c">取消</button>'
      + '<button type="button" class="o">' + (editing ? '保存' : '添加') + '</button></div>'
      + '<div class="klm-dlg-tip">Enter 保存 · Esc 取消 · 最多 ' + LABEL_MAX + ' 条，按代码存（日线/分时共用）</div>'
      + '</div>';
    document.body.append(wrap);
    labelDlg = wrap;

    const input = wrap.querySelector('.klm-dlg-in');
    const pv = wrap.querySelector('.klm-dlg-pv .klm-label');
    const sws = wrap.querySelector('.klm-sws');
    const shp = wrap.querySelector('.klm-shapes');
    input.value = editing ? editing.text : '';

    LABEL_COLORS.forEach(function (c) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'klm-sw' + (c === color ? ' on' : '');
      b.style.background = c; b.dataset.color = c;
      sws.append(b);
    });
    LABEL_SHAPES.forEach(function (k) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = (k === shape ? 'on' : ''); b.dataset.shape = k;
      b.textContent = SHAPE_LABEL[k] || k;
      shp.append(b);
    });

    function paint() {
      pv.className = 'klm-label is-' + shape;
      pv.style.setProperty('--c', color);
      pv.textContent = '';
      const dot = document.createElement('i');
      dot.className = 'klm-lab-dot';
      pv.append(dot, document.createTextNode(input.value.trim() || '标签文字'));
    }
    paint();
    input.focus();
    input.select();

    wrap.addEventListener('click', function (ev) {
      if (ev.target === wrap) { closeLabelEditor(); return; }
      const sw = ev.target.closest('.klm-sw');
      if (sw) {
        color = sw.dataset.color;
        sws.querySelectorAll('.klm-sw').forEach(function (x) { x.classList.toggle('on', x === sw); });
        paint(); return;
      }
      const sb = ev.target.closest('.klm-shapes button');
      if (sb) {
        shape = sb.dataset.shape;
        shp.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === sb); });
        paint(); return;
      }
      if (ev.target.closest('.c')) { closeLabelEditor(); return; }
      if (ev.target.closest('.o')) commit();
    });
    input.addEventListener('input', paint);
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); commit(); }
      else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); closeLabelEditor(); }
    });

    function commit() {
      const res = editing
        ? updateLabel(store(), code, idx, { text: input.value, color: color, shape: shape })
        : addLabel(store(), code, { text: input.value, color: color, shape: shape });
      if (!res.ok) { toast(res.reason); input.focus(); return; }
      closeLabelEditor();
      setLabels(res.labels);
      toast(editing ? '已修改标签' : '已添加标签');
    }
  }

  // ---------------------------------------------------------------- 右键菜单
  let menuEl = null, subEl = null, subAnchor = null, subRole = '', lastMenuPrice = null;

  // 主图设置（2026-10-30 主人）：默认显示根数存 localStorage，下次打开生效；开关直接写 KLineApp.setTools。
  const VB_KEY = 'kline_visible_bars_v1';
  function readVisibleBars() {
    try { const v = Number(localStorage.getItem(VB_KEY)); return (v === 60 || v === 120 || v === 240) ? v : 60; } catch (e) { return 60; }
  }
  function setVisibleBars(n) {
    try { localStorage.setItem(VB_KEY, String(n)); } catch (e) { /* 隐私模式：本次不持久化 */ }
    toast('默认显示 ' + n + ' 根（下次打开生效）');
  }
  function toggleTool(k) {
    if (!window.KLineApp || typeof KLineApp.setTools !== 'function') return;
    const cur = (typeof KLineApp.toolsState === 'function') ? KLineApp.toolsState() : {};
    const next = Object.assign({}, cur);
    if (k === 'chips') next.chips = !cur.chips;
    else if (k === 'adjust') next.adjust = (cur.adjust === false);
    else if (k === 'cost') next.cost = !cur.cost;
    else if (k === 'bs') next.bs = (cur.bs === false);
    else if (k === 'tpsl') next.tpsl = !cur.tpsl;
    else return;
    KLineApp.setTools(next);
    toast('主图设置：' + k + ' → ' + (next[k] ? '开' : '关'));
  }

  function closeMenu() {
    if (menuEl) { menuEl.remove(); menuEl = null; }
    closeSub();
  }
  function closeSub() { if (subEl) { subEl.remove(); subEl = null; } }

  function placeAt(box, x, y) {
    box.style.left = '0px'; box.style.top = '0px';
    const r = box.getBoundingClientRect();
    let left = x, top = y;
    if (left + r.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - r.width - 8);
    if (top + r.height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - r.height - 8);
    box.style.left = left + 'px'; box.style.top = top + 'px';
  }

  function modelNow() {
    const model = buildMenuModel({
      code: st.code, name: o.name || '', price: lastMenuPrice,
      marks: marks, data: markData, hlines: hlines,
      period: st.period, overlay: st.overlay, sectorCode: st.sectorCode,
      reasons: markReasons(),
      watched: !!window.__watchSet,
      tools: (window.KLineApp && typeof KLineApp.toolsState === 'function') ? KLineApp.toolsState() : {},
      visibleBars: readVisibleBars(),
      dongneng: readDongneng(),
    });
    const at = model.findIndex(function (x) { return x.id === 'overlay'; });
    model.splice(at < 0 ? model.length : at, 0, { id: 'subs', label: '副图', sub: subMenuItems() });
    // 标签（2026-11-04）：放在「手工画线」之前——同属标注类，但又不能没入口。
    const dAt = model.findIndex(function (x) { return x.id === 'draw'; });
    model.splice(dAt < 0 ? model.length : dAt, 0, {
      id: 'labels', label: '标签', tag: labels.length ? String(labels.length) : '',
      sub: labelMenuItems(labels.length, labelsOn),
    });
    // P2：下单与条件单已接入（面板），把 marks.js 里的占位项改成可用，并补「记录」
    // 2026-10-30：日线的「条件单预警…」不受这个覆盖影响（只有分时才改成「条件单」）
    model.forEach(function (x) {
      if (x.id === 'buy') { x.label = '买入'; x.disabled = false; x.reason = ''; x.tag = '纸面'; }
      if (x.id === 'sell') { x.label = '卖出'; x.disabled = false; x.reason = ''; x.tag = '纸面'; }
      if (x.id === 'cond' && st.period === 'intraday') { x.label = '条件单'; x.disabled = false; x.reason = ''; x.tag = '纸面'; }
    });
    const cAt = model.findIndex(function (x) { return x.id === 'cond'; });
    model.splice(cAt < 0 ? model.length : cAt + 1, 0,
      { id: 'list', label: '委托与条件单', tag: String((markData.order || []).length + (markData.cond || []).length) });
    return model;
  }
  function subItemsOf(model, id) {
    const n = model.find(function (m) { return m && m.id === id; });
    return n && n.sub ? n.sub : [];
  }

  // 菜单项：文字一律 esc；disabled 项带可见原因，不做「点了没反应又不说为什么」的控件
  function itemHtml(it) {
    if (it.kind === 'sep') return '<div class="klm-sep"></div>';
    if (it.kind === 'head') return '<div class="klm-mh">' + esc(it.label) + '</div>';
    const arrow = it.sub ? '▸' : '';
    const right = [it.checked ? '✓' : '', it.tag || '', arrow].filter(Boolean).join(' ');
    const reason = it.reason ? '<span class="rs">' + esc(it.reason) + '</span>' : '';
    return '<div class="klm-mi' + (it.disabled ? ' is-disabled' : '') + '" data-id="' + esc(it.id) + '"'
      + (it.disabled ? ' aria-disabled="true"' : '') + '>'
      + '<span class="lb">' + esc(it.label) + reason + '</span>'
      + '<span class="tg">' + esc(right) + '</span></div>';
  }
  function renderList(box, items, role) {
    box.innerHTML = items.map(itemHtml).join('');
    box.dataset.role = role;
  }

  function openSub(anchor, items, role) {
    if (!subEl) {
      subEl = document.createElement('div');
      subEl.className = 'klm-menu klm-sub';
      document.body.append(subEl);
      subEl.addEventListener('click', onMenuClick);
    }
    subAnchor = anchor; subRole = role;
    renderList(subEl, items, role);
    const r = anchor.getBoundingClientRect();
    subEl.style.left = '0px'; subEl.style.top = '0px';
    const w = subEl.offsetWidth || 210;
    subEl.style.left = Math.max(8, Math.min(r.right - 4, window.innerWidth - w - 8)) + 'px';
    subEl.style.top = r.top + 'px';
    const h = subEl.offsetHeight || 180;
    if (r.top + h > window.innerHeight - 8) subEl.style.top = Math.max(8, window.innerHeight - h - 8) + 'px';
  }

  function openMenu(ev, price) {
    closeMenu();
    closeLabelMenu();
    lastMenuPrice = price;
    menuEl = document.createElement('div');
    menuEl.className = 'klm-menu';
    renderList(menuEl, modelNow(), 'main');
    document.body.append(menuEl);
    placeAt(menuEl, ev.clientX, ev.clientY);
    menuEl.addEventListener('click', onMenuClick);
    // 悬停即展开子项（旧版 2026-10-21 主人口径），点击也能展开（不依赖 hover）
    menuEl.addEventListener('mouseover', function (e) {
      const it = e.target.closest('[data-id]');
      if (!it || !menuEl || !menuEl.contains(it)) return;
      const id = it.getAttribute('data-id');
      const items = subItemsOf(modelNow(), id);
      if (items.length) openSub(it, items, id); else closeSub();
    });
  }

  function onMenuClick(e) {
    const it = e.target.closest('[data-id]');
    if (!it) return;
    e.stopPropagation();
    if (it.classList.contains('is-disabled')) return;   // 禁用项不做任何事，也不假装刷新
    const id = it.getAttribute('data-id');

    if (id === 'marks' || id === 'draw' || id === 'overlay' || id === 'subs' || id === 'labels') {
      openSub(it, subItemsOf(modelNow(), id), id);
      return;
    }
    if (id === 'lab_new') { closeMenu(); openLabelEditor(null); return; }
    if (id === 'lab_show') {
      labelsOn = !labelsOn;
      writeLabelsOn(store(), labelsOn);
      renderLabels();
      if (subEl) renderList(subEl, labelMenuItems(labels.length, labelsOn), 'labels');
      return;
    }
    if (id === 'lab_clear') {
      const r = clearLabels(store(), code);
      setLabels(r.labels);
      closeMenu();
      toast('已清空本图标签');
      return;
    }
    if (id.indexOf('sub_') === 0) {
      const k = id.slice(4);
      if (!subAvailable(k)) return;            // 禁用项：不做事（不假装切换成功）
      subs[k] = !subs[k];
      try { store().setItem(SUB_KEY, JSON.stringify(subs)); } catch (e) { /* 存不下就只影响本次 */ }
      renderSubs();
      if (subEl) renderList(subEl, subMenuItems(), 'subs');
      return;
    }
    if (id === 'refresh') {
      closeMenu();
      runChart().then(function () { loadPaper(); });
      runSides();
      if (st.ovPending) retryOverlay();
      toast('已刷新');
      return;
    }
    if (id === 'watch') { closeMenu(); try { window.KLineApp.toggleWatch(); } catch (e) { toast('加入自选失败：' + ((e && e.message) || e)); } return; }
    if (id.indexOf('vb_') === 0) { closeMenu(); setVisibleBars(Number(id.slice(3))); return; }
    if (id === 'dn') { closeMenu(); toggleDongneng(); return; }
    if (id.indexOf('tools_') === 0) { closeMenu(); toggleTool(id.slice(6)); return; }
    if (id === 'buy' || id === 'sell') { openPanelWith(id, lastMenuPrice); return; }
    if (id === 'alert') {
      // 2026-11-07：日线右键的预警入口改成「只设预警」——旧版这里开下单面板的
      // 条件单页，而预警页与条件单已彻底分家（任务书 §2.1 来源②：改完不能再下单）。
      closeMenu();
      openAlertPanel({ code: st.code, name: o.name || st.code, price: lastMenuPrice });
      return;
    }
    if (id === 'cond') { openPanelWith('cond', lastMenuPrice); return; }
    if (id === 'list') { openPanelWith('list', lastMenuPrice); return; }
    if (id === 'ov_index') {
      closeMenu();
      loadOverlay('index', '000001.SH', '上证指数', tok('--accent', 'currentColor'));
      return;
    }
    if (id === 'ov_sector') {
      closeMenu();
      if (!st.sectorCode) { toast('该票无行业映射'); return; }
      loadOverlay('sector', st.sectorCode, st.sectorName || st.sectorCode, tok('--warn-solid', 'currentColor'));
      return;
    }
    if (id === 'ov_custom') {
      closeMenu();
      const q = askCustomCode();
      if (q) loadOverlay('custom', q, q, tok('--up-solid', 'currentColor'));
      return;
    }
    if (id === 'ov_clear') { closeMenu(); clearOverlayUi('已清除叠加'); return; }
    if (id.indexOf('mk_') === 0) {
      const k = id.slice(3);
      if (k === 'off') {
        Object.keys(marks).forEach(function (x) { marks[x] = false; });
      } else if (k in marks) {
        marks[k] = !marks[k];
      }
      saveMarks(store(), marks);      // 持久化（key=ko_marks，与旧版同一把）
      drawMarks();
      if (subEl) renderList(subEl, subItemsOf(modelNow(), subRole), subRole);  // 原地刷新打勾
      return;
    }
    if (id === 'hl_place') { closeMenu(); startDraw(); return; }
    if (id === 'hl_clear') {
      hlines = clearHlines(store(), st.code);
      drawMarks(); closeMenu(); toast('已清除本图手工线');
      return;
    }
  }

  // ---------------------------------------------------------------- 手工水平线
  // 旧版水平线是「单点类工具：按下即落库」（kline_lc.js 绘图段注释），这里同样是选一次、点一下落一条。
  function startDraw() {
    st.drawMode = 'hline';
    el.canvas.style.cursor = 'crosshair';
    el.hint.textContent = '点图放水平线 · Esc 取消';
  }
  function exitDraw(msg) {
    st.drawMode = null;
    el.canvas.style.cursor = '';
    el.hint.textContent = '';
    if (msg) toast(msg);
  }

  el.canvas.addEventListener('contextmenu', function (ev) {
    ev.preventDefault();
    if (st.drawMode) exitDraw();
    let price = null;
    if (st.cache) {
      const sc = scaleFor(st.cache.kind, st.cache, el.canvas.clientWidth, el.canvas.clientHeight);
      if (sc) {
        const y = ev.offsetY;
        if (y >= sc.rect.t && y <= sc.rect.b) price = sc.priceAtY(y);
      }
    }
    openMenu(ev, price);
  });

  // 标签上的右键/双击：在标签层上收，不让事件冒到主图菜单。
  el.labels.addEventListener('contextmenu', function (ev) {
    const chip = ev.target.closest('.klm-label');
    if (!chip) return;
    ev.preventDefault(); ev.stopPropagation();
    openLabelMenu(Number(chip.dataset.idx), ev.clientX, ev.clientY);
  });
  el.labels.addEventListener('dblclick', function (ev) {
    const chip = ev.target.closest('.klm-label');
    if (!chip) return;
    ev.preventDefault(); ev.stopPropagation();
    openLabelEditor(Number(chip.dataset.idx));
  });

  el.canvas.addEventListener('click', function (ev) {
    if (st.drawMode !== 'hline' || !st.cache) return;
    const sc = scaleFor(st.cache.kind, st.cache, el.canvas.clientWidth, el.canvas.clientHeight);
    if (!sc) return;
    const y = ev.offsetY;
    if (y < sc.rect.t || y > sc.rect.b) return;   // 点在边距区不落线，避免价格跑到图外
    const p = sc.priceAtY(y);
    if (p === null || p <= 0) return;
    hlines = addHline(store(), st.code, p);
    drawMarks();
    exitDraw('已加水平线 ' + Number(p).toFixed(2));
  });

  // 拖角改尺寸：拖动中即时重画，松手才落 localStorage（不把每一帧都写盘）
  el.ov.addEventListener('click', function () {
    if (st.overlayErr) { retryOverlay(); return; }
    if (st.overlay) { clearOverlayUi('已清除叠加'); return; }
  });

  el.grip.addEventListener('pointerdown', function (ev) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const bx = el.box.getBoundingClientRect();
    const cy = el.cv.getBoundingClientRect();
    const w0 = bx.width, h0 = cy.height;
    const sx = ev.clientX, sy = ev.clientY;
    let moved = false;
    function mv(e2) {
      const dw = e2.clientX - sx, dh = e2.clientY - sy;
      if (!moved && Math.abs(dw) < 3 && Math.abs(dh) < 3) return;
      moved = true;
      size = clampSize({ w: w0 + dw, h: h0 + dh }, viewport());
      applySize();
      renderCache();
    }
    function up() {
      window.removeEventListener('pointermove', mv);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      if (moved) { saveSize(store(), size); toast('尺寸已记住'); }
    }
    window.addEventListener('pointermove', mv);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  });

  const footer = { badge: el.badge, text: el.stat };
  const savedPeriod = readPeriod();
  const initPeriod = PERIODS.some(function (p) { return p.key === savedPeriod; }) ? savedPeriod : 'intraday';
  const st = { code: code, period: o.period === 'daily' ? 'd' : initPeriod,
               closed: false, lastIntraday: null,
               cache: null, chip: null, drawMode: null,
               // P1b：叠加（同一时间只留一条，换来源即替换）与纸面只读数据的请求序号/状态
               overlay: null, overlayErr: '', ovPending: null, oseq: 0,
               sectorCode: null, sectorName: '', sectorErr: '',
               pseq: 0, tradesState: '', ordersState: '', tradesErr: '', ordersErr: '',
               // P1c 竞态：cseq=主图取数、sseq=右侧栏（盘口/筹码/F10）
               cseq: 0, sseq: 0, chartGate: latestGate(),
               // P2：纸面下单面板与它要用的原始只读数据
               panel: null, quote: null, account: null, accountErr: '', ordersRaw: [], condsRaw: [], cost: null,
               condPreview: null };
  _state = st;

  // 周期档
  PERIODS.forEach(function (p) {
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.period = p.key;
    b.textContent = p.label;
    b.className = p.key === st.period ? 'on' : '';
    b.addEventListener('click', function () {
      if (st.period === p.key) return;
      st.period = p.key;
      try { store().setItem(PERIOD_KEY, p.key); } catch (e) { /* 存不下也不影响本次 */ }
      refreshPeriodUi();
      st.chartGate.invalidate(); // 在途的主图请求作废（它回来时序号已旧）
      st.sseq++;                 // 在途的右侧栏请求作废
      st.oseq++;                 // 在途的叠加请求作废
      st.cache = null;
      if (el.subs) { el.subs.innerHTML = ''; el.subs.hidden = true; }   // 不留上一周期旧副图
      clearCross();
      // 日线不支持叠加：切过去就清掉，不留「线还在但轴已经换了」的错画面
      if (st.period !== 'intraday' && (st.overlay || st.overlayErr)) {
        clearOverlayUi();
        toast('日线不支持叠加，已清除');
      }
      runChart().then(function () {
        if (st.closed) return;
        // 切回分时时按新 slots 重算叠加，避免拿旧槽位硬对
        if (st.period === 'intraday' && st.ovPending && !st.overlay) {
          const p0 = st.ovPending;
          loadOverlay(p0.kind, p0.code, p0.name, p0.color);
        }
      });
      runSides();
    });
    el.period.append(b);
  });
  function refreshPeriodUi() {
    el.period.querySelectorAll('button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.period === st.period);
    });
    layoutLabels();
  }

  /**
   * 取数 + 绘图。
   * 竞态保护（P1c 返修）：每次请求领一个单调递增序号 `st.cseq`；回来时只有「还是最新」
   * 且弹窗未关才允许改 cache / footer / 画布。慢 d 后快 m5、慢 m5 后快 d、最后一次失败
   * 三种情形都不会把旧数据回写。
   */
  async function runChart() {
    const seq = st.chartGate.begin();
    const periodAtCall = st.period;
    const isLatest = function () { return !st.closed && st.chartGate.isLatest(seq); };
    try {
      const payload = await loadChart(api, st.code, periodAtCall, el.canvas, footer, overlayPayload());
      if (!isLatest()) return;                      // 旧请求：直接丢弃，不碰任何 UI
      st.cache = payload;
      // 筹码图的昨收线需要分时数据；日线/分钟置空（drawChips 会回退到 vp.pre_close）
      st.lastIntraday = payload.kind === 'intraday' ? payload.d : null;
      renderChartNow();                             // 图在序号校验之后才画
      drawMarks();
      renderSubs();
      paintFooter(payload);
    } catch (err) {
      if (!isLatest()) return;                      // 旧请求失败不许覆盖当前画面
      st.cache = null;
      el.badge.className = 'klm-badge';
      el.badge.textContent = '失败';
      el.stat.textContent = String(err && err.message ? err.message : err);
      drawMarks();
      renderSubs();
    }
  }

  /** 按 cache 画主图（唯一绘图入口；loadCandle / drawIntraday 都不在此之外画）。 */
  function renderChartNow() {
    if (!st.cache) return;
    if (st.cache.kind === 'intraday') drawIntraday(el.canvas, st.cache.d, overlayPayload());
    else drawDaily(el.canvas, st.cache.bars, st.cache.states, { dn: dongnengOf(st.cache.bars) });
    layoutLabels();   // 分时/日线的左下角留白不同，标签列跟着换 left
  }

  function paintFooter(payload) {
    if (!payload) return;
    if (payload.kind === 'intraday') {
      const d = payload.d || {};
      el.badge.className = 'klm-badge is-live';
      el.badge.textContent = '分时 · 实时';
      el.stat.textContent = st.code + ' · ' + (d.count || 0) + ' 点 · 昨收 '
        + (d.pre_close == null ? '—' : Number(d.pre_close).toFixed(2))
        + ' · 现价 ' + (d.last == null ? '—' : Number(d.last).toFixed(2)) + ' ' + pctText(d.pct);
      return;
    }
    const bars = payload.bars || [];
    el.badge.className = 'klm-badge';
    el.badge.textContent = (payload.label || '') + ' · ' + (payload.adjustLabel || '—')
      + ' · ' + (payload.sourceLabel || '—');
    el.stat.textContent = st.code + ' · ' + bars.length + ' 根'
      + (bars.length ? ' · 至 ' + bars[bars.length - 1].date : '');
  }

  // 右侧两块：分时才有盘口/筹码；日线只留 F10。F10 永远异步，不挡图。
  async function runSides() {
    const sseq = ++st.sseq;
    const alive = function () { return !st.closed && sseq === st.sseq; };
    const idx = isIndexLike(st.code);
    if (st.period !== 'intraday' || idx) {
      el.quote.hidden = true;
      st.chip = null;      // 切到日线/指数：旧筹码画布失效，别在尺寸变化时重画上一张的筹码
    } else {
      el.quote.hidden = false;
      el.quote.innerHTML = quoteHtml(null);
      api.get('/quote5/' + encodeURIComponent(st.code)).then(function (q) {
        if (!alive()) return;
        st.quote = q;
        el.quote.innerHTML = quoteHtml(q);
        // 五档点击带价（旧版 chipOn 口径）：点哪一档就把价带进面板
        el.quote.querySelectorAll('[data-price]').forEach(function (row) {
          row.style.cursor = 'pointer';
          row.addEventListener('click', function () {
            const pv = Number(row.dataset.price);
            if (pv > 0) openPanelWith('buy', pv);
          });
        });
        // 筹码画在盘口下面同一栏（原 frontend.js 是并列两块，弹窗里纵向排省宽度）
        const cap = document.createElement('canvas');
        cap.className = 'klm-chip';
        el.quote.append(cap);
        api.get('/volume_profile/' + encodeURIComponent(st.code)).then(function (vp) {
          if (!alive()) return;
          st.chip = { canvas: cap, vp: vp };   // 尺寸变化时用它重画，不重新取
          drawChips(cap, vp, _state.lastIntraday || null);
        }).catch(function (e) {
          if (st.closed) return;
          const n = document.createElement('div');
          n.className = 'klm-note';
          n.textContent = '筹码不可用：' + (e && e.message ? e.message : e);
          el.quote.append(n);
        });
      }).catch(function (e) {
        if (!alive()) return;
        el.quote.innerHTML = '<div class="klm-err">盘口不可用：'
          + esc(e && e.message ? e.message : e) + '</div>';
      });
    }
    el.f10.innerHTML = f10Html(null);
    // 右键「刷新 F10」：force=1 跳过 B2 缓存强制重拉（主人 2026-10-26 要求）。
    // 只在 F10 栏上绑，不干扰主图的其它右键行为。
    el.f10.oncontextmenu = function (ev) {
      ev.preventDefault();
      el.f10.dataset.full = '';     // 刷新时允许轻量首屏重新占位
      loadF10(true);
    };
    loadF10(false);
  }

  /** 取（或强制重拉）F10 并渲染。F10 挂在根路径 /api/f10_brief/*，不走 api.get。 */
  let f10Busy = false;
  function loadF10(force) {
    if (st.closed || f10Busy) return;
    f10Busy = true;
    if (force) el.f10.innerHTML = '<div class="klm-loading">刷新中…（首次冷查可能要几秒）</div>';

    // ① 首屏：只拉 valuation（单项 ~200–280ms），先把关键估值显出来。
    //    不等完整 9 段——那是 1.7s 到 8s+ 的波动区间，会直接把弹窗拖到用户以为卡死。
    const quickUrl = '/api/f10_brief/' + encodeURIComponent(st.code) + '/quick'
      + (force ? '?force=1' : '');
    let quickDone = false;
    fetch(quickUrl, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (q) {
        if (st.closed || !q) return;
        quickDone = true;
        // 只在完整段还没回来时占位，避免盖掉已出的完整数据
        if (!el.f10.dataset.full) el.f10.innerHTML = f10QuickHtml(q);
      }).catch(function () { /* 轻量口失败不报错：等完整段 */ });

    // ② 完整段：后台补齐（不阻塞首屏）。
    const url = '/api/f10_brief/' + encodeURIComponent(st.code) + (force ? '?force=1' : '');
    fetch(url, { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (st.closed) return;
        el.f10.dataset.full = '1';
        el.f10.innerHTML = f10Html(d);
      }).catch(function (e) {
        if (st.closed) return;
        // 完整段失败：若轻量首屏已出数就不顶掉（至少还有估值可看）
        if (el.f10.dataset.full || quickDone) { f10Busy = false; return; }
        el.f10.innerHTML = '<div class="klm-err">F10 不可用：'
          + esc(e && e.message ? e.message : e) + '</div>';
      }).then(function () { f10Busy = false; });
  }

  // 关闭：点遮罩空白 / ✕ / Esc
  // 快捷键本阶段只保留 Esc：画线中先退画线，菜单开着先关菜单，都不是才关窗。
  function onKey(ev) {
    if (ev.key !== 'Escape') return;
    if (labelDlg) { closeLabelEditor(); return; }                       // 0. 标签编辑浮层
    if (labelMenuEl || labelPalEl) { closeLabelMenu(); return; }        // 0.5 标签菜单/样式
    if (st.panel && st.panel.isOpen()) { st.panel.close(); return; }   // 1. 面板
    if (menuEl) { closeMenu(); return; }                               // 2. 菜单
    if (st.drawMode) { exitDraw(); return; }                           // 3. 画线态
    closeKline();
  }
  function onDocDown(ev) {
    if (labelMenuEl && labelMenuEl.contains(ev.target)) return;
    if (labelPalEl && labelPalEl.contains(ev.target)) return;
    if (labelDlg && labelDlg.contains(ev.target)) return;
    if (!menuEl) { closeLabelMenu(); return; }
    if (menuEl.contains(ev.target) || (subEl && subEl.contains(ev.target))) return;
    closeMenu();
    closeLabelMenu();
  }
  root.addEventListener('mousedown', function (ev) {
    if (ev.target === root) closeKline();
  });
  root.querySelector('.klm-x').addEventListener('click', function () { closeKline(); });
  document.addEventListener('keydown', onKey);
  document.addEventListener('mousedown', onDocDown, true);
  root._onKey = onKey;
  root._onDocDown = onDocDown;

  // 窗口尺寸变化：先把容器夹回视口内（原实现漏了这步，clientWidth 不变 → 看着没反应），
  // 再用缓存重画；不再为一次缩放重取数据。
  let resizeTimer = 0;
  function onResize() {
    if (st.closed) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (st.closed) return;
      closeMenu();
      applySize();
      if (st.cache) renderCache();
    }, 120);
  }
  window.addEventListener('resize', onResize);
  root._onResize = onResize;
  root._onPaintOv = paintOvChip;
  root._cleanup = function () {
    clearTimeout(resizeTimer);
    clearTimeout(toastTimer);
    closeMenu();
    closeLabelMenu();
    closeLabelEditor();
    if (st.panel) { st.panel.destroy(); st.panel = null; }
    if (toastEl) { toastEl.remove(); toastEl = null; }
  };

  applySize();          // 先按记忆尺寸定容器，再出图（否则第一张图按旧 CSS 高度画）
  renderLabels();       // 标签层先上（不等 K 线数据）
  // 先出图（快），再补两侧
  await runChart();
  runSides();
  paintOvChip();
  loadPaper();          // 纸面只读标记：失败只影响标记层，不动主图
  loadSectorMap();      // 行业映射：拿不到就让「行业走势」项显示无映射
  return {
    close: closeKline,
    /** P1b 接入点：灌买卖点/成本/委托/条件单数据后即时重画；null/[] 表示该类不可用，不画假线。 */
    setMarkData: function (patch) {
      Object.keys(patch || {}).forEach(function (k) { if (k in markData) markData[k] = patch[k]; });
      drawMarks();
    },
    /** 当前四项开关（只读副本，便于外部核对持久化结果）。 */
    getMarks: function () { return Object.assign({}, marks); },
  };
}

export function closeKline() {
  const root = document.getElementById(MODAL_ID);
  if (root) {
    if (root._onKey) document.removeEventListener('keydown', root._onKey);
    if (root._onDocDown) document.removeEventListener('mousedown', root._onDocDown, true);
    if (root._onResize) window.removeEventListener('resize', root._onResize);
    window.removeEventListener('resize', root._onPaintOv);
    if (root._cleanup) root._cleanup();
    root.remove();
  }
  if (_state) _state.closed = true;
  _state = null;
}

export default { openKline, closeKline };
