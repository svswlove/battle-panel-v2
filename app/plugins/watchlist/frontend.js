// ============================================================================
// 自选池卡片 · 前端批次 1a（docs/task_fe_batch1a.md）
// ----------------------------------------------------------------------------
// 行为基准 = 旧 Tab `engine/web/index.html` 的 #panel-watchlist：
//   · 5550-5668  徽标 / 所属板块 / 移除 / 分组条（_watchGroupBar）/ 搜索下拉
//   · 5688-5731  卡片网格 + 迷你走势（sparkPath / watchSeries / watchPaintCard）
//   · 5732-5803  实时档当日分时（走势线 + 均价线 + 量柱）
//   · 5853-5891  板块卡（走势 + AMV + 涨幅 / 主力 / 权重）
//   · 5916-5976  拖拽（组内改序 → /reorder；拖到同类型分组标签 → /move）
//   · 5977-6024  renderWatchlist（拖动中 / 搜索中不重建）
//
// 契约（任务书 §契约与约束）：
//   · 只用 ctx.api.get / ctx.api.post；写操作走 compat_watchlist 的 /api/watchlist/* 绝对路径
//     （shell.js 的 makeCtx 对 /api/ 开头的路径不拼插件前缀；批次 1a 已补 api.post）。
//   · 颜色一律 themes.css token（--up / --down / --warn / --dim / --accent 等），本文件无裸 hex。
//   · 零写盘、零 import、不写全局变量；卡片只操作自己的 el。
//   · refreshEvery: 5000；「搜索框聚焦或非空」「拖拽中」「分组名编辑中」三种情况跳过刷新。
//
// 与旧 Tab 的差异（如实登记，见 docs/HANDOFF_20261024_fe_batch1a.md）：
//   1. 搜索命中点击 = 直接 /watchlist/add（任务书 §功能 7 定的）；旧版是开详情再点「☆ 自选」。
//   2. 移除不弹 confirm（任务书 §功能 8 定的口径；旧版实测是有 confirm 的）。
//   3. 分组改名改成组内联 input（旧版用 prompt）：内联才有真实的「编辑中」状态供 refresh 判断；
//      新建分组仍用 prompt（无现成输入点）。分组长按 600ms 也走删除（旧版只有右键）。
// ============================================================================

const LS_MODE = 'wl_mode_v1';          // 盘后 / 实时档记忆（任务书 §功能 2）
const STYLE_ID = 'watchlist-card-css';

const EOD_TTL = 300000;                // 日线序列缓存 5 分钟（旧版一次拉取后永久缓存）
const LIVE_TTL = 15000;                // 当日分时缓存 15 秒（5s 轮询不重复打同一只票）
const SECTOR_TTL = 300000;             // 板块走势缓存 5 分钟（同旧版 _WSPARK）

// ---- 跨挂载缓存（2026-10-28 主人标注 47「自选池打开速度太慢」）
// shell 切 Tab 时 unmount 会删掉宿主节点，插件里的 S 随之销毁 —— 原来每次切回都重新拉
// /api/watchlist.json（实测约 520ms）+ 24 条 /api/kline（约 700ms）。把取数结果放模块作用域，
// 重开时先按缓存画一帧，再照常刷新（stale-while-revalidate），不改任何数据口径。
const MOD = {
  payload: { at: 0, data: null },      // /api/watchlist.json（含行情/多空/徽标）
  levels: { at: 0, data: null },       // 本插件 /levels（关键位）
  eod: new Map(), live: new Map(), secEod: new Map(), secLive: new Map(),
};
const PAYLOAD_CACHE_MS = 15000;        // 重开时可直接画的窗口；超过就等新数据，不显示旧价
const LEVELS_CACHE_MS = 60000;
const LONGPRESS_MS = 600;              // 分组标签长按 = 删除（旧版是右键）

const DOT_CLS = { '多': 'dot-up', '偏多': 'dot-lean', '偏空': 'dot-cool', '空': 'dot-down' };

// 关键位类型中文名（2026-11-06 主人：自选池不要显英文）。
// 文案与作战表 battle_plan 的同名词典一致；两插件不互相 import（插件边界），
// 若 source 新增类型，这里缺的仍回退原值，不把信息吞掉。
const LV_CN = { YELLOW: '黄线', WHITE: '白线', EK_OPEN: '动能K下沿', EK_CLOSE: '动能K上沿',
  N_LOW: 'N型低点', HWM: '保本高水位' };

const CSS = `
.wl-root{display:flex;flex-direction:column;gap:10px;color:var(--fg)}
.wl-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.wl-title{margin:0;font-size:0.98rem;font-weight:600;color:var(--fg-strong)}
.wl-meta{color:var(--dim);font-size:0.7rem}
.wl-gap{flex:1 1 auto}
.wl-search{position:relative;flex:0 0 auto}
.wl-search input{width:210px;padding:4px 12px;border-radius:var(--radius-pill);border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);color:var(--fg);font:inherit;font-size:0.75rem}
.wl-search input:focus{outline:none;border-color:var(--accent)}
.wl-drop{position:absolute;right:0;top:calc(100% + 4px);z-index:6;width:320px;max-height:320px;overflow:auto;background:var(--card);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);box-shadow:var(--shadow-pop);padding:4px}
.wl-sr{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:var(--radius-sm);cursor:pointer;font-size:0.78rem;color:var(--fg)}
.wl-sr:hover{background:var(--card2)}
.wl-sr-nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wl-sr-cd{color:var(--dim);font-size:0.7rem;font-family:var(--font-mono)}
.wl-sr-tag{font-size:0.62rem;padding:0 5px;border-radius:var(--radius-sm);border:var(--border-w) var(--border-style) var(--border-color);color:var(--dim)}
.wl-sr-tag.is-sec{color:var(--accent);border-color:var(--accent)}
.wl-sr-tag.is-etf{color:var(--warn-ink,var(--warn));border-color:var(--warn)}
.wl-sr-empty{padding:8px;color:var(--dim);font-size:0.75rem;text-align:center}
.wl-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.wl-groups{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
/* 2026-11-13 主人：搜索框位置换成 MMDD 日期分组条（横向一排，多了横向滑，不撑高头部） */
.wl-mmdd{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;max-width:min(560px, 50vw)}
.wl-mmdd::-webkit-scrollbar{display:none}
.wl-mmdd .wl-group{flex:none;border-color:var(--accent-weak,var(--border-color))}
.wl-mmdd-empty{font-size:0.7rem;color:var(--dim)}
.wl-group{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:var(--radius-pill);border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);color:var(--dim);font-size:0.72rem;cursor:pointer;user-select:none;-webkit-user-select:none}
.wl-group.is-on{background:var(--accent);color:var(--accent-on);border-color:var(--accent);font-weight:600}
.wl-group.is-drop{border-color:var(--accent);box-shadow:inset 0 0 0 2px var(--accent)}
.wl-group.is-drag{opacity:.45}
.wl-group .wl-gn{font-size:0.62rem;opacity:.7}
.wl-group .wl-gx{opacity:.6;font-size:0.7rem}
.wl-group .wl-gx:hover{opacity:1;color:var(--up)}
.wl-gadd{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:var(--radius-pill);border:1px dashed var(--border-color);background:transparent;color:var(--dim);font-size:0.85rem;line-height:1;cursor:pointer;font-family:inherit}
.wl-gadd:hover{border-color:var(--accent);color:var(--accent)}
.wl-gsep{width:1px;height:16px;background:var(--line);margin:0 2px}
.wl-ginput{border:var(--border-w) var(--border-style) var(--accent);background:var(--card);color:var(--fg);font:inherit;font-size:0.72rem;border-radius:var(--radius-pill);padding:1px 8px;outline:none;min-width:72px}
.wl-mode{display:inline-flex;align-items:center;gap:6px}
.wl-mode-t{font-size:0.72rem;color:var(--dim)}
.wl-mode-t.is-on{color:var(--fg-strong);font-weight:600}
.wl-sw{position:relative;width:38px;height:20px;border-radius:var(--radius-pill);border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);cursor:pointer;padding:0}
.wl-sw.is-live{background:var(--accent);border-color:var(--accent)}
.wl-sw i{position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:var(--dim);transition:transform .15s,background .15s}
.wl-sw.is-live i{transform:translateX(18px);background:var(--accent-on)}
.wl-msg{font-size:0.75rem;color:var(--dim);min-height:14px}
.wl-msg.is-err{color:var(--up)}
.wl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(246px,1fr));gap:14px}
.wl-grid.is-sec{grid-template-columns:repeat(auto-fill,minmax(292px,1fr))}
.wl-wc{background:var(--card2);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);padding:11px 12px;min-height:204px;display:flex;flex-direction:column;cursor:grab;overflow:hidden}
.wl-wc.is-sec{min-height:240px}
.wl-wc.is-drag{opacity:.45}
.wl-wc-hd{display:flex;align-items:baseline;gap:6px}
.wl-cd{font-size:0.72rem;color:var(--dim);font-family:var(--font-mono)}
.wl-nm{font-size:1rem;font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fg-strong)}
.wl-nm.is-link,.wl-cd.is-link{cursor:pointer}
.wl-nm.is-link:hover,.wl-cd.is-link:hover{color:var(--accent);text-decoration:underline dotted;text-underline-offset:2px}
.wl-wc.is-sec .wl-nm{flex:0 1 auto}
.wl-bd{font-size:0.62rem;padding:1px 6px;border-radius:5px;background:var(--accent);color:var(--accent-on);font-weight:700}
.wl-x{border:0;background:transparent;color:var(--dim);cursor:pointer;font-size:0.74rem;padding:0 2px;font-family:inherit}
.wl-x:hover{color:var(--up)}
.wl-wc.is-sec .wl-x{margin-left:auto}
.wl-px{display:flex;align-items:baseline;justify-content:space-between;margin:3px 0 4px}
.wl-price{font-size:1.5rem;font-weight:700;color:var(--fg-strong);font-variant-numeric:tabular-nums}
.wl-chg{font-size:0.95rem;font-weight:600;font-variant-numeric:tabular-nums}
.wl-chg.up{color:var(--up)}
.wl-chg.down{color:var(--down)}
.wl-main{width:100%;height:70px;display:block}
.wl-sub{width:100%;height:28px;display:block;margin-top:4px}
.wl-wc.is-sec .wl-main{flex:2 1 48px;min-height:48px;height:auto}
.wl-wc.is-sec .wl-sub{flex:1 1 24px;min-height:24px;height:auto}
.wl-wc.is-sec .wl-main path,.wl-wc.is-sec .wl-sub path{vector-effect:non-scaling-stroke}
.wl-main .wl-close{stroke:var(--accent);stroke-width:1.5;fill:none}
.wl-main .wl-yl{stroke:var(--warn);stroke-width:1.1;fill:none}
.wl-main .wl-wh{stroke:var(--dim);stroke-width:0.8;fill:none;stroke-dasharray:2 1;opacity:0.45}
.wl-sub .wl-amv{stroke:var(--up);stroke-width:1.2;fill:none}
.wl-sub .wl-amv-ma{stroke:var(--warn);stroke-width:0.9;fill:none;stroke-dasharray:2 1}
.wl-sub .wl-amv-wh{stroke:var(--fg);stroke-width:0.8;fill:none;opacity:0.7}
.wl-sub .wl-amv2{stroke:var(--accent);stroke-width:1.2;fill:none}
.wl-sub .wl-ma10{stroke:var(--dim);stroke-width:0.8;fill:none;stroke-dasharray:2 1;opacity:0.5}
.wl-main .wl-live-px{stroke:var(--accent);stroke-width:1.5;fill:none}
.wl-main .wl-live-avg{stroke:var(--warn);stroke-width:1.1;fill:none}
.wl-sub .wl-live-vol{fill:var(--dim);opacity:.45}
.wl-wc .wl-empty{fill:var(--dim);font-size:9px}
.wl-chips{display:flex;gap:5px;margin-top:auto;align-items:center;flex-wrap:nowrap;overflow:hidden;min-height:19px}
.wl-chip,.wl-divtag,.wl-flow{font-size:0.62rem;padding:0 7px;border-radius:var(--radius-pill);border:var(--border-w) var(--border-style) var(--border-color);color:var(--dim);white-space:nowrap;display:inline-flex;align-items:center;gap:3px;flex:0 0 auto;height:18px;line-height:18px;font-variant-numeric:tabular-nums}
.wl-chip .wl-cnm{max-width:5.4em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wl-chip.is-bull{color:var(--up);border-color:var(--up);background:var(--up-bg)}
.wl-chip.is-bear{color:var(--down);border-color:var(--down);background:var(--down-bg)}
.wl-chip.is-link{cursor:pointer}
.wl-chip.is-link:hover{border-color:var(--accent);color:var(--accent)}
i.wl-dot{width:5px;height:5px;border-radius:50%;display:inline-block;flex:0 0 auto;background:var(--dim)}
i.wl-dot.dot-up{background:var(--up)}
i.wl-dot.dot-down{background:var(--down)}
i.wl-dot.dot-lean{background:var(--warn)}
i.wl-dot.dot-cool{background:var(--temp-cool)}
i.wl-dot.dot-flat{background:var(--dim)}
.wl-divtag{color:var(--up);border-color:var(--up);background:var(--up-bg)}
.wl-divtag.is-down{color:var(--down);border-color:var(--down);background:var(--down-bg)}
.wl-flow.up{color:var(--up);border-color:var(--up);background:var(--up-bg)}
.wl-flow.down{color:var(--down);border-color:var(--down);background:var(--down-bg)}
.wl-flow.flat{color:var(--dim)}
.wl-kv2{font-size:0.62rem;color:var(--dim);margin:2px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wl-kv2 b{font-weight:600;font-variant-numeric:tabular-nums;color:var(--fg)}
.wl-kv2 b.up{color:var(--up)}
.wl-kv2 b.down{color:var(--down)}
.wl-kv2 .wl-sep{margin:0 6px;opacity:.45}
.wl-lvrow{display:flex;gap:4px;align-items:baseline;font-size:0.62rem;color:var(--dim);margin-top:3px;white-space:nowrap;overflow:hidden}
.wl-lvrow b{font-weight:600;font-variant-numeric:tabular-nums;color:var(--fg)}
.wl-lvrow b.is-near{color:var(--warn)}
.wl-lvrow b.up{color:var(--up)}
.wl-lvrow b.down{color:var(--down)}
.wl-empty-hint{color:var(--dim);font-size:0.78rem;line-height:1.7;padding:14px 2px}
.wl-src{color:var(--dim);font-size:0.6875rem;line-height:1.55}
@media(max-width:760px){.wl-grid,.wl-grid.is-sec{grid-template-columns:repeat(2,minmax(0,1fr))}}
`;

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------- 小工具（照旧 Tab 的 esc/num/pct/cls）

const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));
const num = (v, d) => {
  const n = Number(v);
  if (v == null || v === '' || !isFinite(n)) return '—';
  return n.toFixed(d == null ? 2 : d);
};
const pct = (v) => {
  const n = Number(v);
  if (v == null || v === '' || !isFinite(n)) return '—';
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
};
const signCls = (v) => {
  const n = Number(v);
  if (v == null || !isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
};
const code6 = (code) => String(code == null ? '' : code).split('.')[0];
const errText = (e) => (e && e.message) || String(e);

// ---------------------------------------------------------------- SVG 路径（逐字照搬旧 Tab）

function sparkPath(arr, lo, hi, h) {
  const n = arr.length;
  const rg = (hi - lo) || 1;
  let d = '';
  let first = true;
  for (let i = 0; i < n; i += 1) {
    const v = arr[i];
    if (v == null) continue;
    const x = (n > 1 ? (i / (n - 1)) * 100 : 0);
    const y = (1 - (v - lo) / rg) * h;
    d += (first ? 'M' : ' L') + x.toFixed(2) + ' ' + y.toFixed(2);
    first = false;
  }
  return d;
}

function extentOf(groups, padRatio, minPad) {
  const all = [];
  for (const arr of groups) {
    for (const v of arr) {
      if (v == null) continue;
      const n = Number(v);
      if (isFinite(n)) all.push(n);
    }
  }
  if (!all.length) return null;
  let lo = all[0];
  let hi = all[0];
  for (const v of all) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const pad = (hi - lo) * padRatio || minPad;
  return { lo: lo - pad, hi: hi + pad };
}

function intradayPath(points, key, lo, hi, h) {
  const n = points.length;
  const rg = (hi - lo) || 1;
  let d = '';
  let first = true;
  for (let i = 0; i < n; i += 1) {
    const v = points[i][key];
    if (v == null) continue;
    const x = (n > 1 ? (i / (n - 1)) * 100 : 0);
    const y = (1 - (v - lo) / rg) * h;
    d += (first ? 'M' : ' L') + x.toFixed(2) + ' ' + y.toFixed(2);
    first = false;
  }
  return d;
}

function readMode() {
  try {
    return localStorage.getItem(LS_MODE) === 'live' ? 'live' : 'eod';
  } catch (e) {
    return 'eod';
  }
}

function writeMode(mode) {
  try { localStorage.setItem(LS_MODE, mode); } catch (e) { /* 隐私模式写不进去：忽略 */ }
}

// ---------------------------------------------------------------- 卡片

export default {
  cards: [{
    id: 'watchlist.main',
    title: '自选池',
    slot: 'main',
    order: 50,
    // 旧 Tab 是 5s 轮询；跳过条件见 refresh()（搜索中 / 拖拽中 / 分组改名中）。
    refreshEvery: 5000,

    async mount(root, ctx) {
      root.textContent = '';

      const S = {
        ctx: ctx,
        root: root,
        mode: readMode(),          // 'eod' 盘后 | 'live' 实时
        group: '',                 // 当前分组 id（首帧由 loadAll 兜底）
        data: null,                // /api/watchlist.json 原样
        levels: {},                // code -> 最近关键位（本插件 /levels，保留既有增值）
        // 下面四张表走模块作用域（MOD）：切 Tab 回来时命中缓存，不重新拉取
        eod: MOD.eod,              // code -> 日线序列路径
        live: MOD.live,            // code -> 当日分时点
        secEod: MOD.secEod,        // 'L:<level>:<codes>' -> sector_card map
        secLive: MOD.secLive,      // 'S:<codes>' / code -> 当日轨迹
        els: new Map(),            // code -> .wl-wc 元素
        drag: null,                // {code, kind, moved}
        editing: false,            // 分组名编辑中
        searchTimer: null,
        searchSeq: 0,
        liveSeq: 0,
        eodSeq: 0,                                                   
        alive: true,
        onDocDown: null,
        el: {},
      };
      this._wl = S;
      const api = ctx.api;

      const div = (cls, text) => {
        const d = document.createElement('div');
        if (cls) d.className = cls;
        if (text != null) d.textContent = text;
        return d;
      };
      // 相对时间（2026-10-28 主人标注 44）：刷新时间不标到秒，只给「刚刚 / N分钟前 / N小时前 / N天前」。
      // 输入不是可解析时间时原样返回（不编造）。
      const relTime = (s) => {
        const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(String(s || ''));
        if (!m) return s || '—';
        const hasTime = m[4] != null;
        const y = +m[1], mo = +m[2] - 1, da = +m[3];
        const now = new Date();
        const dayDiff = Math.floor((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
          - new Date(y, mo, da).getTime()) / 86400000);
        if (!hasTime) {                 // 盘后档的 quote_asof 只有日期
          if (dayDiff <= 0) return '今天';
          if (dayDiff === 1) return '昨天';
          if (dayDiff < 30) return dayDiff + '天前';
          return s.slice(0, 10);
        }
        const t = new Date(y, mo, da, +m[4], +m[5], +(m[6] || 0)).getTime();
        const d = Date.now() - t;
        if (!isFinite(d) || d < 0) return s;
        const min = Math.floor(d / 60000);
        if (min < 1) return '刚刚';
        if (min < 60) return min + '分钟前';
        const hr = Math.floor(min / 60);
        if (hr < 24) return hr + '小时前';
        const day = Math.floor(hr / 24);
        if (day < 30) return day + '天前';
        return s.slice(0, 10);
      };

      const showMsg = (text, isErr) => {
        if (!S.el.msg) return;
        S.el.msg.textContent = text || '';
        S.el.msg.classList.toggle('is-err', !!isErr);
      };
      // 跨卡跳转：派发 CustomEvent，无监听方时只 debug（任务书 §不做）
      const emit = (name, detail) => {
        let handled = false;
        try {
          const ev = new CustomEvent(name, { detail: detail, bubbles: true, cancelable: true });
          document.dispatchEvent(ev);
          handled = ev.defaultPrevented;
        } catch (e) {
          handled = false;
        }
        if (!handled) console.debug('[watchlist] 无监听方：', name, detail);
      };

      // ---- 骨架（只建一次；之后 render 只换内容，不重建骨架 → 不丢焦点）
      const wrap = div('wl-root');
      const head = div('wl-head');
      const title = document.createElement('h2');
      title.className = 'wl-title';
      title.textContent = '自选池';
      const meta = div('wl-meta', '');
      const search = div('wl-search');
      const input = document.createElement('input');
      input.type = 'text';
      input.placeholder = '搜股票 / 板块 / 代码…';
      input.autocomplete = 'off';
      input.spellcheck = false;
      input.setAttribute('aria-label', '搜索后加入自选池');
      const drop = div('wl-drop');
      drop.hidden = true;
      search.append(input, drop);
      // 2026-11-13 主人：搜索框去掉，这个位置改放 MMDD 日期分组（当日/近日选股）。
      //   搜索 DOM 仍建但**不挂到页面**：它牵涉下拉/防抖/点外部关闭与 refresh 的「搜索中不重绘」，
      //   整块拆会把那条判断一起带走；先停用，代码留待清理。
      const mmdd = div('wl-groups wl-mmdd');
      const modeBox = div('wl-mode');
      // 2026-11-13 主人：排版更简洁 —— 卡内不再重复「自选池」标题（壳已经有一份），
      //   盘后/实时开关与日期分组同排右侧，分组条独占一行。
      head.append(meta, div('wl-gap'), modeBox, mmdd);

      const bar = div('wl-bar');
      const groups = div('wl-groups');
      bar.append(groups);

      const msg = div('wl-msg', '');
      const body = div('wl-body');
      const src = div('wl-src', '拖卡片改组内顺序，拖到分组标签换组；卡片右上 ✕ 移出。'
        + '数据共用 0AMV 工作台（个股多空=AMV 双线四态；板块多空=AMV 双轨；徽标=buy_signal.db）。');
      wrap.append(head, bar, msg, body, src);
      root.append(wrap);
      S.el = { wrap: wrap, head: head, title: title, meta: meta, input: input, drop: drop,
        mmdd: mmdd,
        bar: bar, groups: groups, mode: modeBox, msg: msg, body: body, src: src };

      // ---- 分组工具
      const groupsOf = () => (S.data && S.data.groups) || [];
      const groupItems = () => ((S.data && S.data.items) || []).filter((x) => x.group === S.group);
      const findGroup = (gid) => groupsOf().find((g) => g.id === gid) || null;

      // ---- 关键位索引（本插件既有增值，任务书 §不做 要求保留）
      const buildLevelIndex = (payload) => {
        const out = {};
        for (const row of (payload && payload.items) || []) {
          const lvs = (row.key_levels || []).filter((k) => k && k.distance_pct != null && isFinite(Number(k.distance_pct)));
          if (!lvs.length) continue;
          const eff = lvs.filter((k) => k.effective !== false);
          const pool = eff.length ? eff : lvs;      // 全部已破时退回最近的已破档（不显示空白）
          let near = pool[0];
          for (const k of pool) {
            if (Math.abs(Number(k.distance_pct)) <= Math.abs(Number(near.distance_pct))) near = k;
          }
          if (row.code) out[row.code] = near;
          const bare = code6(row.code);
          if (bare && !out[bare]) out[bare] = near;
        }
        return out;
      };

      const levelHtml = (code) => {
        const lv = S.levels[code] || S.levels[code6(code)];
        if (!lv) return '<div class="wl-lvrow" title="最近关键位（focus_levels，只读）">关键位 <b>—</b></div>';
        const d = Number(lv.distance_pct);
        const mark = lv.effective === false ? '（已破）' : '';
        const c = Math.abs(d) <= 1 ? 'is-near' : (d > 0 ? 'up' : 'down');
        return '<div class="wl-lvrow" title="最近关键位（focus_levels，只读；已破档也显示）">关键位 <b class="'
          + c + '">' + esc(LV_CN[lv.level_type] || lv.level_type || '—') + ' ' + num(lv.price, 3) + mark + '</b> '
          + (d >= 0 ? '+' : '') + d.toFixed(2) + '%</div>';
      };

      // ---- 个股卡片（照旧 Tab watchCardHtml 的信息密度）
      const stockCardHtml = (it) => {
        const q = it.quote || {};
        const sec = it.sector || {};
        const l2 = sec.l2 || {};
        const rg = it.regime || '';
        const rcls = (rg === '多' || rg === '偏多') ? ' is-bull' : ((rg === '空' || rg === '偏空') ? ' is-bear' : '');
        const dot = DOT_CLS[rg] || 'dot-flat';
        const badges = (it.badges || []).slice(0, 2).map((b) => (
          '<span class="wl-bd" title="' + esc((b && b.desc) || '') + '">' + esc(b && b.label) + '</span>'
        )).join('');
        const dv = (it.div == null || !isFinite(Number(it.div))) ? null : Number(it.div);
        const dtag = dv == null ? '' : (
          '<span class="wl-divtag' + (dv > 0 ? '' : ' is-down') + '" title="5日背离：'
          + (dv > 0 ? '资金增配（活跃市值涨 &gt; 价格涨）' : '价格虚涨（价格涨 &gt; 活跃市值涨）')
          + '">' + (dv > 0 ? '+' : '') + dv.toFixed(1) + 'pp</span>'
        );
        const l2rg = l2.regime || '';
        const sdot = DOT_CLS[l2rg] || 'dot-flat';
        const scls = (l2rg === '多' || l2rg === '偏多') ? ' is-bull' : ((l2rg === '空' || l2rg === '偏空') ? ' is-bear' : '');
        const secchip = l2.name ? (
          '<span class="wl-chip is-link' + scls + '" data-role="sector" data-open-amv="' + esc(l2.code)
          + '" data-open-name="' + esc(l2.name) + '" data-code="' + esc(l2.code)
          + '" data-name="' + esc(l2.name) + '" title="点开板块详情：' + esc(l2.name)
          + '（板块多空 ' + esc(l2rg || '—') + '）"><span class="wl-cnm">' + esc(l2.name)
          + '</span><i class="wl-dot ' + sdot + '"></i></span>'
        ) : '';
        const fl = (it.flow == null || !isFinite(Number(it.flow))) ? null : Number(it.flow);
        const ftag = fl == null ? '' : (
          '<span class="wl-flow ' + (signCls(fl) || 'flat') + '" title="主力净流入（东财口径）">'
          + (fl > 0 ? '+' : '') + fl.toFixed(1) + '亿</span>'
        );
        const qsrc = (S.mode === 'live' && it.quote_source === 'live') ? '实时（tick_feed）' : '收盘';
        return '<div class="wl-wc" draggable="true" data-code="' + esc(it.code) + '" data-kind="stock"'
          + ' data-group="' + esc(it.group || '') + '" title="拖拽可改顺序 / 拖到其它同类型分组换组">'
          + '<div class="wl-wc-hd">'
          // 跳转占位：data-open-kline 由 shell 的捕获阶段委托接管（R5 跨窗口桥）；
          // data-role 保留给卡内自己的处理器（旧口径），两者并存、互不替代。
          + '<span class="wl-cd is-link" data-role="kline" data-open-kline="' + esc(it.code)
          + '" data-open-name="' + esc(it.name || '') + '" data-code="' + esc(it.code)
          + '" data-name="' + esc(it.name) + '" title="点开 K 线：' + esc(it.code) + '">' + esc(code6(it.code)) + '</span>'
          + '<span class="wl-nm is-link" data-role="kline" data-open-kline="' + esc(it.code)
          + '" data-open-name="' + esc(it.name || '') + '" data-code="' + esc(it.code)
          + '" data-name="' + esc(it.name) + '" title="点名称看 K 线">' + esc(it.name || code6(it.code)) + '</span>'
          + badges
          + '<button type="button" class="wl-x" data-role="remove" data-code="' + esc(it.code) + '" title="移出自选池">✕</button>'
          + '</div>'
          + '<div class="wl-px"><span class="wl-price" title="' + qsrc + '">' + num(q.price, 2) + '</span>'
          + '<span class="wl-chg ' + signCls(q.change_pct) + '">' + pct(q.change_pct) + '</span></div>'
          + '<svg class="wl-main" viewBox="0 0 100 52" preserveAspectRatio="none" aria-hidden="true"></svg>'
          + '<svg class="wl-sub" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true"></svg>'
          + levelHtml(it.code)
          + '<div class="wl-chips"><span class="wl-chip' + rcls + '"><i class="wl-dot ' + dot + '"></i>'
          + esc(rg || '—') + '</span>' + secchip + dtag + ftag + '</div>'
          + '</div>';
      };

      // ---- 板块卡片（照旧 Tab watchSectorCardHtml）
      const sectorCardHtml = (it) => {
        const rg = it.regime || '';
        const rcls = (rg === '多') ? ' is-bull' : (rg === '空' ? ' is-bear' : '');
        const dot = DOT_CLS[rg] || 'dot-flat';
        return '<div class="wl-wc is-sec" draggable="true" data-code="' + esc(it.code) + '" data-kind="sector"'
          + ' data-level="' + esc(it.level || 'L2') + '" data-group="' + esc(it.group || '')
          + '" title="拖拽可改顺序 / 拖到其它同类型分组换组 · 点名称开板块详情">'
          + '<div class="wl-wc-hd">'
          + '<span class="wl-nm is-link" data-role="sector" data-open-amv="' + esc(it.code)
          + '" data-open-name="' + esc(it.name || '') + '" data-code="' + esc(it.code)
          + '" data-name="' + esc(it.name) + '" title="点开板块详情">' + esc(it.name || it.code) + '</span>'
          + '<span class="wl-chip' + rcls + '" title="板块多空（AMV 双轨）"><i class="wl-dot ' + dot + '"></i>'
          + esc(rg || '—') + '</span>'
          + (it.level ? '<span class="wl-chip">' + esc(it.level) + '</span>' : '')
          + '<button type="button" class="wl-x" data-role="remove" data-code="' + esc(it.code) + '" title="移出自选池">✕</button>'
          + '</div>'
          + '<svg class="wl-main" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"></svg>'
          + '<div class="wl-kv2" data-kv="0">涨幅 <b class="' + signCls(it.pct) + '">' + pct(it.pct) + '</b></div>'
          + '<svg class="wl-sub" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true"></svg>'
          + '<div class="wl-kv2" data-kv="1">AMV <b>—</b></div>'
          + '</div>';
      };

      // MMDD 日期分组：name 是 4 位数字的个股组（1112 / 1111 …）
      const isMmddGroup = (g) => !!g && g.kind === 'stock' && /^\d{4}$/.test(String(g.name || '').trim());
      // 头部日期分组条（搜索框原位置）：最新的在最左，点击切组（事件委托走 wrap，不用另绑）
      const renderMmdd = () => {
        if (!S.el.mmdd) return;
        const gs = groupsOf().filter(isMmddGroup)
          .sort((a, b) => String(b.name).localeCompare(String(a.name)));
        S.el.mmdd.innerHTML = gs.length
          ? gs.map((g) => '<span class="wl-group' + (g.id === S.group ? ' is-on' : '')
              + '" data-gid="' + esc(g.id) + '" data-gkind="stock" data-gname="' + esc(g.name) + '"'
              + ' title="' + esc(String(g.name) + ' 当日选股 · ' + (Number(g.count) || 0) + ' 只') + '">'
              + esc(g.name) + '<span class="wl-gn">' + (Number(g.count) || 0) + '</span></span>').join('')
          : '<span class="wl-mmdd-empty">还没有日期分组</span>';
      };
      // 点击切组：MMDD 条在 head 里，不在下面那条分组条的 click 委托范围内，单独绑
      mmdd.addEventListener('click', (ev) => {
        const pill = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (!pill || !pill.dataset.gid) return;
        if (pill.dataset.gid === S.group) return;
        S.group = pill.dataset.gid;
        render();
      });

      // ---- 分组条（个股组 + 一个「＋」+ 分隔 + 板块组；默认组无改名/删除入口）
      const groupBarHtml = () => {
        const mk = (g) => {
          const isDefault = (g.id === 'stock_default' || g.id === 'sector_default');
          const tip = isDefault
            ? '默认分组（不可改名/删除）· 拖卡片到此处换组'
            : '双击改名 · 右键或长按删除 · 拖卡片到此处换组 · 拖动标签可调顺序';
          return '<span class="wl-group' + (g.id === S.group ? ' is-on' : '') + '" data-gid="' + esc(g.id)
            + '" draggable="true" data-gkind="' + esc(g.kind) + '" data-gname="' + esc(g.name) + '"'
            + ' data-gdefault="' + (isDefault ? '1' : '0') + '" title="' + esc(tip) + '">'
            + esc(g.name) + '<span class="wl-gn">' + (Number(g.count) || 0) + '</span>'
            + (isDefault ? '' : '<span class="wl-gx" data-act="gdel" title="删除分组">✕</span>')
            + '</span>';
        };
        // MMDD 日期分组单独摆在头部（搜索框原位置），不在下面这条里重复出现
        const stockGs = groupsOf().filter((g) => g.kind === 'stock' && !isMmddGroup(g));
        const sectorGs = groupsOf().filter((g) => g.kind === 'sector');
        return stockGs.map(mk).join('')
          + '<button type="button" class="wl-gadd" data-act="gadd" title="新建个股分组">＋</button>'
          + '<span class="wl-gsep"></span>'
          + sectorGs.map(mk).join('');
      };

      // ---- 盘后 / 实时开关
      const renderMode = () => {
        const box = S.el.mode;
        box.textContent = '';
        const left = document.createElement('span');
        left.className = 'wl-mode-t' + (S.mode === 'eod' ? ' is-on' : '');
        left.textContent = '盘后';
        const sw = document.createElement('button');
        sw.type = 'button';
        sw.className = 'wl-sw' + (S.mode === 'live' ? ' is-live' : '');
        sw.title = '盘后=收盘数据 · 实时=当日分时';
        sw.setAttribute('aria-label', '盘后/实时切换');
        sw.setAttribute('aria-pressed', String(S.mode === 'live'));
        sw.append(document.createElement('i'));
        sw.addEventListener('click', () => { switchMode(S.mode === 'live' ? 'eod' : 'live'); });
        const right = document.createElement('span');
        right.className = 'wl-mode-t' + (S.mode === 'live' ? ' is-on' : '');
        right.textContent = '实时';
        box.append(left, sw, right);
      };

      // ---- 渲染（骨架不动，只换内容）
      // 盘后档数据没变化就不重建卡片：5s 轮询每次都全量重写 innerHTML 会白闪 + 白重绘。
      // 实时档不跳（当日分时点位每秒在变，必须重画）；日线缓存过期也不跳（否则图会停在旧数据上）。
      let lastSig = '';
      const eodStale = () => {
        for (const it of groupItems()) {
          if (it.kind === 'stock' && !fresh(S.eod.get(it.code), EOD_TTL)) return true;
        }
        return false;
      };
      const dataSig = (d) => {
        const items = (d && d.items) || [];
        let s = String((d && d.quote_asof) || '') + '#' + S.mode + '#' + S.group + '#' + items.length;
        for (const it of items) {
          const q = it.quote || {};
          s += '|' + it.code + ',' + it.group + ',' + it.order + ',' + q.price + ',' + q.change_pct
            + ',' + it.regime + ',' + it.level + ',' + it.pct + ',' + it.div + ',' + it.flow
            + ',' + ((it.badges || []).map((b) => (b && b.label) || '').join('+'));
        }
        return s;
      };

      const render = () => {
        const d = S.data || {};
        const items = d.items || [];
        const st = d.stats || {};
        const total = st.total == null ? items.length : st.total;
        S.el.meta.textContent = total + ' 只 · 股 ' + (st.stocks == null ? 0 : st.stocks)
          + ' / 板 ' + (st.sectors == null ? 0 : st.sectors)
          + ' · 行情 ' + relTime(d.quote_asof);
        S.el.groups.innerHTML = groupBarHtml();
        renderMmdd();
        renderMode();
        const sig = dataSig(d) + '#' + (MOD.levels.at || 0);
        if (S.mode === 'eod' && sig === lastSig && !eodStale()) return;   // 无变化 → 保留现有 DOM
        lastSig = sig;
        renderBody();
        hydrate();
      };

      const renderBody = () => {
        const items = groupItems();
        const cur = findGroup(S.group) || { kind: 'stock' };
        S.els = new Map();
        S.el.body.textContent = '';
        if (!items.length) {
          const gname = (findGroup(S.group) || {}).name || '该分组';
          S.el.body.append(div('wl-empty-hint', '「' + gname
            + '」暂无卡片。用顶部搜索框加入，或把其它分组的卡片拖到上方同类型分组标签。'));
          return;
        }
        const grid = document.createElement('div');
        grid.className = 'wl-grid' + (cur.kind === 'sector' ? ' is-sec' : '');
        grid.innerHTML = items.map((it) => (it.kind === 'sector' ? sectorCardHtml(it) : stockCardHtml(it))).join('');
        S.el.body.append(grid);
        for (const cardEl of grid.querySelectorAll('.wl-wc')) S.els.set(cardEl.dataset.code, cardEl);
      };

      // ---- 绘制
      const paintStockEod = (code) => {
        const el = S.els.get(code);
        const s = S.eod.get(code);
        if (!el || !s || !s.main) return;
        const main = el.querySelector('.wl-main');
        const sub = el.querySelector('.wl-sub');
        if (main) {
          main.innerHTML = '<path class="wl-close" d="' + s.main.c + '"/><path class="wl-yl" d="' + s.main.y
            + '"/><path class="wl-wh" d="' + s.main.w + '"/>';
        }
        if (sub) {
          // 副图 = 活跃市值(红) + MA10(黄虚线) + 白线(白)，三条同量纲（亿），同轴直接叠
          sub.innerHTML = '<path class="wl-amv" d="' + s.sub.a + '"/><path class="wl-amv-ma" d="'
            + s.sub.m + '"/><path class="wl-amv-wh" d="' + s.sub.w + '"/>';
        }
      };

      const paintStockLive = (code) => {
        const el = S.els.get(code);
        if (!el) return;
        const main = el.querySelector('.wl-main');
        const sub = el.querySelector('.wl-sub');
        const rec = S.live.get(code);
        if (!rec || !rec.ok || !rec.points || !rec.points.length) {
          if (main) main.innerHTML = '<text x="50" y="30" text-anchor="middle" class="wl-empty">—</text>';
          if (sub) sub.innerHTML = '';
          return;
        }
        const pts = rec.points;
        const cl = pts.map((p) => p.close).filter((v) => v != null);
        const av = pts.map((p) => p.avg).filter((v) => v != null);
        const ext = extentOf([cl, av], 0.08, 0.01);
        if (main) {
          main.innerHTML = ext
            ? '<path class="wl-live-px" d="' + intradayPath(pts, 'close', ext.lo, ext.hi, 52) + '"/>'
              + '<path class="wl-live-avg" d="' + intradayPath(pts, 'avg', ext.lo, ext.hi, 52) + '"/>'
            : '<text x="50" y="30" text-anchor="middle" class="wl-empty">—</text>';
        }
        // 副图：当日量柱（归一）
        const vols = pts.map((p) => Number(p.volume) || 0);
        let vmax = 1;
        for (const v of vols) if (v > vmax) vmax = v;
        const n = pts.length;
        const bw = n > 1 ? 100 / n : 100;
        let bars = '';
        for (let i = 0; i < n; i += 1) {
          const vh = (vols[i] / vmax) * 20;
          bars += '<rect x="' + ((i / n) * 100).toFixed(2) + '" y="' + (20 - vh).toFixed(2)
            + '" width="' + Math.max(0.3, bw * 0.7).toFixed(2) + '" height="' + vh.toFixed(2)
            + '" class="wl-live-vol"/>';
        }
        if (sub) sub.innerHTML = bars;
      };

      const paintSectorEod = (code) => {
        const el = S.els.get(code);
        if (!el) return;
        const lvl = el.dataset.level || 'L2';
        const bucket = S.secEod.get('L:' + lvl + ':' + sectorCodesOf(lvl).slice().sort().join(','));
        const s = bucket && bucket.map && bucket.map[code];
        if (!s) return;
        const main = el.querySelector('.wl-main');
        const sub = el.querySelector('.wl-sub');
        const cl = s.close || {};
        const am = s.amv || {};
        if (main) {
          main.innerHTML = '<path class="wl-close" d="' + (cl.c || '') + '"/><path class="wl-yl" d="'
            + (cl.yl || '') + '"/><path class="wl-wh" d="' + (cl.wh || '') + '"/>';
        }
        if (sub) sub.innerHTML = '<path class="wl-amv2" d="' + (am.a || '') + '"/><path class="wl-ma10" d="'
          + (am.ma || '') + '"/>';
        const kvs = el.querySelectorAll('.wl-kv2');
        const f = (v, u) => (v == null || !isFinite(Number(v)) ? '—' : ((Number(v) > 0 ? '+' : '') + Number(v).toFixed(2) + u));
        if (kvs[0]) {
          kvs[0].innerHTML = '涨幅 <b class="' + signCls(s.real_pct) + '">' + f(s.real_pct, '%') + '</b>'
            + '<span class="wl-sep">·</span>主力 <b class="' + signCls(s.main_net) + '">' + f(s.main_net, '亿') + '</b>';
        }
        if (kvs[1]) {
          kvs[1].innerHTML = 'AMV <b class="' + signCls(s.amv_pct) + '">' + f(s.amv_pct, '%') + '</b>'
            + '<span class="wl-sep">·</span>权重 <b class="' + signCls(s.w_delta) + '">' + f(s.w_delta, 'pp') + '</b>';
        }
      };

      const paintSectorLive = (code) => {
        const el = S.els.get(code);
        if (!el) return;
        const main = el.querySelector('.wl-main');
        const sub = el.querySelector('.wl-sub');
        const kvs = el.querySelectorAll('.wl-kv2');
        const rec = S.secLive.get(code);
        const f = (v, u) => (v == null || !isFinite(Number(v)) ? '—' : ((Number(v) > 0 ? '+' : '') + Number(v).toFixed(2) + u));
        if (!rec || !rec.ok || !rec.points || !rec.points.length) {
          if (main) main.innerHTML = '<text x="50" y="24" text-anchor="middle" class="wl-empty">—（无当日轨迹）</text>';
          if (sub) sub.innerHTML = '';
          if (kvs[0]) kvs[0].innerHTML = '涨幅 <b>—</b>';
          if (kvs[1]) kvs[1].innerHTML = 'AMV <b>—</b>';
          return;
        }
        const pts = rec.points;
        const ap = pts.map((p) => p.amv_pct).filter((v) => v != null);
        // 累计均值（均价线语义，同旧 Tab）
        let acc = 0;
        let cnt = 0;
        const avg = [];
        for (const p of pts) {
          if (p.amv_pct != null) {
            acc += Number(p.amv_pct);
            cnt += 1;
          }
          avg.push(cnt ? acc / cnt : null);
        }
        const ext = extentOf([ap, avg.filter((v) => v != null)], 0.08, 0.05);
        if (main) {
          main.innerHTML = ext
            ? '<path class="wl-live-px" d="' + intradayPath(pts, 'amv_pct', ext.lo, ext.hi, 40) + '"/>'
              + '<path class="wl-live-avg" d="'
              + intradayPath(avg.map((v) => ({ amv_pct: v })), 'amv_pct', ext.lo, ext.hi, 40) + '"/>'
            : '<text x="50" y="24" text-anchor="middle" class="wl-empty">—（无当日轨迹）</text>';
        }
        if (sub) sub.innerHTML = '';
        const last = pts[pts.length - 1] || {};
        if (kvs[0]) kvs[0].innerHTML = '涨幅 <b class="' + signCls(last.change_pct) + '">' + f(last.change_pct, '%') + '</b>';
        if (kvs[1]) kvs[1].innerHTML = 'AMV <b class="' + signCls(last.amv_pct) + '">' + f(last.amv_pct, '%') + '</b>';
      };

      const sectorCodesOf = (level) => groupItems()
        .filter((x) => x.kind === 'sector' && (x.level || 'L2') === level)
        .map((x) => x.code);

      const paintCached = () => {
        for (const it of groupItems()) {
          if (it.kind === 'sector') {
            if (S.mode === 'live') paintSectorLive(it.code);
            else paintSectorEod(it.code);
          } else if (S.mode === 'live') {
            paintStockLive(it.code);
          } else {
            paintStockEod(it.code);
          }
        }
      };
      // ---- 取数
      const fresh = (rec, ttl) => !!rec && (Date.now() - rec.at) < ttl;

      // 日线序列（主图=收盘/黄线/白线，副图=活跃市值 + MA10 + 白线）
      // 副图口径（2026-10-28 主人标注 41）：三条线同源同量纲，全部来自 /api/kline 的
      //   amv      = 当日活跃市值（亿）
      //   amvMa    = 其 10 日均（MA10，副图黄虚线）
      //   amvWhite = 其白线（副图白线；与价格白线同一实现 `indicators.yellow_white_lines`，只是输入换成 AMV 序列）
      // 三者都是「亿」，所以同一个纵轴直接叠，不做归一、也不与收盘价混画（改前副图是「收盘归一+AMV 归一」，
      // 两条不同量纲的序列各自归一到 100 再叠，只能看形状、不能看绝对量）。
      // limit=160：2026-10-30（主人标注 52「卡片的黄线计算不全，只能计算到最后面一小段」）。
      //   黄线 = (MA14+MA28+MA57+MA114)/4，前 113 根必然为空（口径库 `indicators.yellow_white_lines`）；
      //   原来只拉 60 根 → 画出来的 45 根里只有末尾几根有黄线。
      //   160 = 114 根预热 + 45 根显示（留 1 根余量），比 260 根少 100 根、单请求便宜约 20ms。
      const loadStockEod = async (code) => {
        const rec = { at: Date.now(), empty: true };
        try {
          const k = await api.get('/api/kline/' + encodeURIComponent(code) + '?period=d&limit=160');
          if (k && Array.isArray(k.bars) && k.bars.length >= 8) {
            const bars = k.bars.slice(-45);
            const C = bars.map((b) => b.c);
            const YL = bars.map((b) => b.yellow);
            const WH = bars.map((b) => b.white);
            const AMV = bars.map((b) => b.amv);
            const AMA = bars.map((b) => b.amvMa);
            const AWH = bars.map((b) => b.amvWhite);
            const mainExt = extentOf([C, YL, WH], 0.08, 1);
            const subExt = extentOf([AMV, AMA, AWH], 0.08, 0.5);
            if (mainExt && subExt) {
              rec.at = Date.now();
              rec.empty = false;
              rec.date = bars[bars.length - 1].t;
              rec.main = {
                c: sparkPath(C, mainExt.lo, mainExt.hi, 52),
                y: sparkPath(YL, mainExt.lo, mainExt.hi, 52),
                w: sparkPath(WH, mainExt.lo, mainExt.hi, 52),
              };
              rec.sub = {
                a: sparkPath(AMV, subExt.lo, subExt.hi, 20),
                m: sparkPath(AMA, subExt.lo, subExt.hi, 20),
                w: sparkPath(AWH, subExt.lo, subExt.hi, 20),
              };
            }
          }
        } catch (e) {
          rec.empty = true;      // 取不到 → 留空图，不编造（旧卡同口径）
        }
        S.eod.set(code, rec);
        return rec;
      };

      // 批量 spark（2026-11-04）：一次请求取整组票的主图/副图序列。
      // 为什么：逐票拉 /api/kline 时服务端票均 112.8 ms（其中动能K 43.8、AMV 单票查询 31.9、
      // 行业 4.9、流通股 3.9 —— 自选卡一个都不用），37 票合计 4.2 s 串行 / 1.5 MB 载荷。
      // /api/kline_sparks 同口径（黄白线、AMV 三线逐值相等，见 compat_kline 探针），
      // 只回末 45 根的 6 条序列：37 票 ≈ 64 KB、冷取 ~0.9 s、20 s 内热取 ~5 ms。
      const buildEodRec = (s) => {
        const rec = { at: Date.now(), empty: true };
        if (!s || !Array.isArray(s.c) || s.c.length < 8) return rec;
        const C = s.c;
        const YL = s.y || [];
        const WH = s.w || [];
        const AMV = s.a || [];
        const AMA = s.m || [];
        const AWH = s.aw || [];
        const mainExt = extentOf([C, YL, WH], 0.08, 1);
        const subExt = extentOf([AMV, AMA, AWH], 0.08, 0.5);
        if (!mainExt || !subExt) return rec;
        rec.at = Date.now();
        rec.empty = false;
        rec.date = s.t;
        rec.main = {
          c: sparkPath(C, mainExt.lo, mainExt.hi, 52),
          y: sparkPath(YL, mainExt.lo, mainExt.hi, 52),
          w: sparkPath(WH, mainExt.lo, mainExt.hi, 52),
        };
        rec.sub = {
          a: sparkPath(AMV, subExt.lo, subExt.hi, 20),
          m: sparkPath(AMA, subExt.lo, subExt.hi, 20),
          w: sparkPath(AWH, subExt.lo, subExt.hi, 20),
        };
        return rec;
      };

      // 批量取一组票的 spark；失败/空码返回 null（调用方退回逐票老路径，不退化）
      const loadStockSparks = async (codes) => {
        const uniq = [...new Set(codes.filter(Boolean))];
        if (!uniq.length) return new Map();
        try {
          const r = await api.get('/api/kline_sparks?tail=45&limit=160&codes='
            + encodeURIComponent(uniq.join(',')));
          const items = (r && r.items) || null;
          if (!items) return null;
          const out = new Map();
          for (const code of uniq) out.set(code, buildEodRec(items[code]));
          return out;
        } catch (e) {
          return null;
        }
      };

      // 当日分时（走势线 + 均价线 + 量柱）
      const loadStockLive = async (code) => {
        let rec = { at: Date.now(), ok: false };
        try {
          const k = await api.get('/api/intraday/' + encodeURIComponent(code));
          if (k && Array.isArray(k.points) && k.points.length) {
            rec = { at: rec.at, ok: true, points: k.points, date: k.date, prev_close: k.prev_close };
          }
        } catch (e) {
          rec = { at: rec.at, ok: false };
        }
        S.live.set(code, rec);
        return rec;
      };

      // 板块日线走势（按 level 批量拉，同旧 Tab watchHydrateSectors）
      const loadSectorEod = async (level, codes) => {
        const key = 'L:' + level + ':' + codes.slice().sort().join(',');
        let map = {};
        try {
          map = (await api.get('/api/sector_card.json?level=' + encodeURIComponent(level)
            + '&days=60&win=5&codes=' + encodeURIComponent(codes.join(',')))) || {};
        } catch (e) {
          map = {};
        }
        S.secEod.set(key, { at: Date.now(), map: map });
        return map;
      };

      // 板块当日轨迹（一次多码，同旧 Tab watchHydrateLiveSectors）
      const loadSectorLive = async (codes) => {
        const key = 'S:' + codes.slice().sort().join(',');
        let map = {};
        try {
          const d = await api.get('/api/sector_intraday.json?codes=' + encodeURIComponent(codes.join(',')));
          map = (d && d.codes) || {};
        } catch (e) {
          map = {};
        }
        for (const code of codes) {
          const v = map[code];
          S.secLive.set(code, (v && Array.isArray(v.points) && v.points.length)
            ? { at: Date.now(), ok: true, points: v.points }
            : { at: Date.now(), ok: false });
        }
        S.secLive.set(key, { at: Date.now(), map: map });
        return map;
      };

      // 实时档并发上限 4（同旧 Tab _watchLiveFetch）+ 序号：切档/切组后旧回调作废
      const hydrateLiveStocks = (stocks) => {
        const seq = ++S.liveSeq;
        const codes = stocks.map((x) => x.code);
        let i = 0;
        let running = 0;
        const next = () => {
          if (!S.alive || seq !== S.liveSeq) return;
          if (i >= codes.length && running === 0) return;
          while (running < 4 && i < codes.length) {
            const code = codes[i];
            i += 1;
            running += 1;
            loadStockLive(code).then(() => {
              running -= 1;
              if (S.alive && seq === S.liveSeq) paintStockLive(code);
              next();
            }).catch(() => {
              running -= 1;
              next();
            });
          }
        };
        next();
      };

      // 盘后档：**一次批量取**整组票的 spark（2026-11-04）。
      // 失败或返回空 → 退回逐票老路径（限并发 4），保证不因新端点故障而整卡空图。
      // 序号 eodSeq：切档/切组后旧回调作废。
      const hydrateEodOneByOne = (todo, seq) => {
        let i = 0;
        let running = 0;
        const next = () => {
          if (!S.alive || seq !== S.eodSeq) return;
          if (i >= todo.length && running === 0) return;
          while (running < 4 && i < todo.length) {
            const code = todo[i].code;
            i += 1;
            running += 1;
            loadStockEod(code).then(() => {
              running -= 1;
              if (S.alive && seq === S.eodSeq) paintStockEod(code);
              next();
            }).catch(() => {
              running -= 1;
              next();
            });
          }
        };
        next();
      };

      const hydrateEodStocks = (stocks) => {
        const seq = ++S.eodSeq;
        // 命中缓存的立即画，不等取数
        for (const it of stocks) {
          if (fresh(S.eod.get(it.code), EOD_TTL)) paintStockEod(it.code);
        }
        const todo = stocks.filter((it) => !fresh(S.eod.get(it.code), EOD_TTL));
        if (!todo.length) return;
        loadStockSparks(todo.map((it) => it.code)).then((recs) => {
          if (!S.alive || seq !== S.eodSeq) return;
          if (!recs) {
            hydrateEodOneByOne(todo, seq);
            return;
          }
          for (const it of todo) {
            const rec = recs.get(it.code) || { at: Date.now(), empty: true };
            S.eod.set(it.code, rec);
            paintStockEod(it.code);
          }
        }).catch(() => {
          if (S.alive && seq === S.eodSeq) hydrateEodOneByOne(todo, seq);
        });
      };

      const hydrate = () => {
        if (!S.alive) return;
        paintCached();          // 先画缓存命中的，剩下的异步补
        const items = groupItems();
        if (!items.length) return;
        const stocks = items.filter((x) => x.kind === 'stock');
        const sectors = items.filter((x) => x.kind === 'sector');
        if (S.mode === 'live') {
          if (stocks.length) hydrateLiveStocks(stocks);
          if (sectors.length) {
            const codes = sectors.map((x) => x.code);
            loadSectorLive(codes).then(() => {
              if (!S.alive) return;
              for (const code of codes) paintSectorLive(code);
            }).catch(() => { /* 轨迹不可用 → 卡片保持「无当日轨迹」 */ });
          }
          return;
        }
        // 盘后档：原来 for 循环直接发全部个股请求，没有并发上限 —— 自选里十几只票就会
        // 同时打十几条 /api/kline，浏览器每条并发连接各占一份，实测单条从 70ms 拖到 350~620ms。
        // 实时档（hydrateLiveStocks）本来就限 4，这里补上同一个上限。
        hydrateEodStocks(stocks);
        if (!sectors.length) return;
        const byLevel = new Map();
        for (const it of sectors) {
          const lvl = it.level || 'L2';
          if (!byLevel.has(lvl)) byLevel.set(lvl, []);
          byLevel.get(lvl).push(it.code);
        }
        byLevel.forEach((codes, lvl) => {
          const cached = S.secEod.get('L:' + lvl + ':' + codes.slice().sort().join(','));
          if (fresh(cached, SECTOR_TTL)) {
            for (const c of codes) paintSectorEod(c);
            return;
          }
          loadSectorEod(lvl, codes).then(() => {
            if (!S.alive) return;
            for (const c of codes) paintSectorEod(c);
          }).catch(() => {});
        });
      };

      // ---- 主加载（mount 与 5s 刷新共用）
      const applyData = (data) => {
        S.data = data || {};
        const gs = groupsOf();
        if (!gs.some((g) => g.id === S.group)) {
          // 当前组不存在 → 回退同类型默认组（同旧 Tab renderWatchlist）
          const fallback = String(S.group || '').indexOf('sector') === 0 ? 'sector_default' : 'stock_default';
          S.group = gs.some((g) => g.id === fallback) ? fallback : ((gs[0] && gs[0].id) || 'stock_default');
        }
        render();
      };

      const fetchLevels = async () => {
        try {
          return buildLevelIndex(await api.get('/levels?effective_only=0'));
        } catch (e) {
          return null;          // 关键位缺失不影响主表（旧卡同口径）
        }
      };

      const loadAll = async () => {
        if (!S.alive) return;
        // 条目与关键位并行取（原来串行 await，白等一个 RTT）；关键位 60s 内复用模块缓存
        const lc = MOD.levels;
        const levelsP = (lc.data && (Date.now() - lc.at) < LEVELS_CACHE_MS)
          ? Promise.resolve(lc.data)
          : fetchLevels().then((v) => {
            if (v) MOD.levels = { at: Date.now(), data: v };
            return v;
          });
        let d = null;
        try {
          const r = await Promise.all([
            api.get('/api/watchlist.json' + (S.mode === 'live' ? '?mode=live' : '')),
            levelsP,
          ]);
          d = r[0];
          if (r[1]) S.levels = r[1];
        } catch (e) {
          showMsg('加载失败：' + errText(e), true);
          return;
        }
        if (!S.alive) return;
        MOD.payload = { at: Date.now(), data: d || {} };
        applyData(d);
        if (!S.el.msg.classList.contains('is-err')) showMsg('', false);
      };
      S.reload = loadAll;
      S.showMsg = showMsg;

      // ---- 写操作（全部走 compat_watchlist 的 /api/watchlist/* 绝对路径）
      const addItem = async (kind, code, name) => {
        closeDrop();
        S.el.input.value = '';
        try {
          await api.post('/api/watchlist/add', {
            kind: kind === 'sector' ? 'sector' : 'stock', code: code, name: name || '',
          });
          const cur = findGroup(S.group);
          const wantSector = kind === 'sector';
          if (cur && (cur.kind === 'sector') !== wantSector) {
            // 新条目落同类型默认组；当前组类型不同 → 跳到对应默认组，否则用户看不到反馈
            S.group = wantSector ? 'sector_default' : 'stock_default';
          }
          await loadAll();
          showMsg('已加入自选池：' + (name || code), false);
        } catch (e) {
          showMsg('加入失败：' + errText(e), true);
        }
      };

      const removeItem = async (code) => {
        // 2026-10-24 验收修：旧版 watchRemove 有 confirm（legacy:5568），任务书误写「无确认」；
        // 行为基准=旧版，补回防误删。
        if (!confirm('从自选池移出 ' + code + '？')) return;
        try {
          await api.post('/api/watchlist/remove', { code: code });
          S.eod.delete(code);
          S.live.delete(code);
          await loadAll();
          showMsg('已移出自选池：' + code, false);
        } catch (e) {
          showMsg('移除失败：' + errText(e), true);
        }
      };

      const moveCard = async (code, gid) => {
        try {
          await api.post('/api/watchlist/move', { code: code, group: gid });
          S.group = gid;
          await loadAll();
        } catch (e) {
          showMsg('移动失败：' + errText(e), true);
          await loadAll();
        }
      };

      const reorderGroup = async (codes, gid) => {
        if (!codes.length) return;
        try {
          await api.post('/api/watchlist/reorder', { codes: codes, group: gid });
        } catch (e) {
          showMsg('排序失败：' + errText(e), true);
        }
        await loadAll();
      };

      const addGroup = async () => {
        let raw = '';
        try {
          raw = window.prompt('新建个股分组名称：', '') || '';
        } catch (e) {
          raw = '';
        }
        const name = String(raw).trim();
        if (!name) return;
        try {
          const j = await api.post('/api/watchlist/group/add', { name: name, kind: 'stock' });
          const gid = j && j.group && j.group.id;
          if (gid) S.group = gid;
          await loadAll();
          showMsg('已新建分组：' + name, false);
        } catch (e) {
          showMsg('新建分组失败：' + errText(e), true);
        }
      };

      const deleteGroup = async (gid) => {
        const g = findGroup(gid);
        if (!g) return;
        if (gid === 'stock_default' || gid === 'sector_default') return;
        if (!window.confirm('删除分组「' + g.name + '」？组内 ' + (Number(g.count) || 0)
          + ' 张卡片将移入同类型默认分组。')) return;
        try {
          await api.post('/api/watchlist/group/remove', { id: gid });
          if (S.group === gid) S.group = (g.kind === 'sector' ? 'sector_default' : 'stock_default');
          await loadAll();
          showMsg('已删除分组：' + g.name, false);
        } catch (e) {
          showMsg('删除分组失败：' + errText(e), true);
        }
      };

      const renameGroup = async (gid, name) => {
        const g = findGroup(gid);
        if (!g || !name || name === g.name) return;
        try {
          await api.post('/api/watchlist/group/rename', { id: gid, name: name });
          await loadAll();
          showMsg('已改名为：' + name, false);
        } catch (e) {
          showMsg('改名失败：' + errText(e), true);
        }
      };

      // ---- 分组名内联编辑（双击触发；编辑期间 refresh 跳过，防 5s 轮询打断）
      const renderGroupsOnly = () => { S.el.groups.innerHTML = groupBarHtml(); renderMmdd(); };
      const startRename = (pill) => {
        if (S.editing) return;
        const gid = pill.dataset.gid;
        const oldName = pill.dataset.gname || '';
        S.editing = true;
        pill.textContent = '';
        const box = document.createElement('input');
        box.className = 'wl-ginput';
        box.type = 'text';
        box.value = oldName;
        box.setAttribute('aria-label', '分组新名称');
        pill.append(box);
        box.focus();
        box.select();
        let done = false;
        const finish = (commit) => {
          if (done) return;
          done = true;
          S.editing = false;
          const next = String(box.value || '').trim();
          renderGroupsOnly();
          if (commit && next && next !== oldName) renameGroup(gid, next);
        };
        box.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter') {
            ev.preventDefault();
            finish(true);
          } else if (ev.key === 'Escape') {
            ev.preventDefault();
            finish(false);
          }
        });
        box.addEventListener('blur', () => finish(true));
        box.addEventListener('mousedown', (ev) => ev.stopPropagation());
        box.addEventListener('click', (ev) => ev.stopPropagation());
        box.addEventListener('dblclick', (ev) => ev.stopPropagation());
      };

      // ---- 盘后 / 实时切换
      const switchMode = (mode) => {
        const next = mode === 'live' ? 'live' : 'eod';
        if (next === S.mode) return;
        S.mode = next;
        writeMode(next);
        if (next === 'live') {
          S.live.clear();
          S.secLive.clear();
        } else {
          S.eod.clear();
          S.secEod.clear();
        }
        loadAll();
      };

      // ---- 搜索（防抖 200ms → POST /api/watchlist/search → 下拉；点结果 → add）
      const closeDrop = () => {
        S.el.drop.hidden = true;
        S.el.drop.textContent = '';
      };

      const renderDrop = (payload) => {
        const stocks = (payload && payload.stocks) || [];
        const sectors = (payload && payload.sectors) || [];
        const rows = [];
        for (const x of stocks) {
          // type 由后端给（stock / etf）：不按号段猜，前端只负责显示标签
          const isEtf = x.type === 'etf';
          rows.push('<div class="wl-sr" data-kind="stock" data-code="' + esc(x.code) + '" data-name="'
            + esc(x.name) + '"><span class="wl-sr-nm">' + esc(x.name) + '</span><span class="wl-sr-cd">'
            + esc(x.code) + '</span><span class="wl-sr-tag' + (isEtf ? ' is-etf' : '') + '">'
            + (isEtf ? 'ETF' : '股') + '</span></div>');
        }
        for (const x of sectors) {
          rows.push('<div class="wl-sr" data-kind="sector" data-code="' + esc(x.code) + '" data-name="'
            + esc(x.name) + '"><span class="wl-sr-nm">' + esc(x.name) + '</span><span class="wl-sr-cd">'
            + esc(x.code) + '</span><span class="wl-sr-tag is-sec">板块</span></div>');
        }
        S.el.drop.innerHTML = rows.length ? rows.join('') : '<div class="wl-sr-empty">无匹配</div>';
        S.el.drop.hidden = false;
      };

      const runSearch = async (q) => {
        const seq = ++S.searchSeq;
        try {
          const d = await api.post('/api/watchlist/search', { q: q });
          if (!S.alive || seq !== S.searchSeq) return;
          renderDrop(d);
        } catch (e) {
          if (!S.alive || seq !== S.searchSeq) return;
          S.el.drop.innerHTML = '<div class="wl-sr-empty">搜索失败：' + esc(errText(e)) + '</div>';
          S.el.drop.hidden = false;
        }
      };

      // ---- 事件 · 搜索
      input.addEventListener('input', () => {
        const q = String(input.value || '').trim();
        clearTimeout(S.searchTimer);
        if (!q) {
          closeDrop();
          return;
        }
        S.searchTimer = setTimeout(() => { runSearch(q); }, 200);
      });
      input.addEventListener('focus', () => {
        const q = String(input.value || '').trim();
        if (q) runSearch(q);
      });
      drop.addEventListener('click', (ev) => {
        const row = ev.target && ev.target.closest ? ev.target.closest('.wl-sr') : null;
        if (!row) return;
        addItem(row.dataset.kind, row.dataset.code, row.dataset.name);
      });
      const onDocDown = (ev) => {
        if (!S.el.drop || S.el.drop.hidden) return;
        if (search.contains(ev.target)) return;
        closeDrop();
      };
      S.onDocDown = onDocDown;
      document.addEventListener('mousedown', onDocDown, true);

      // ---- 事件 · 分组条（点击切换 / ✕ 删除 / ＋ 新建；右键或长按删除；双击改名）
      let pressTimer = null;
      const cancelPress = () => {
        if (pressTimer) {
          clearTimeout(pressTimer);
          pressTimer = null;
        }
      };
      groups.addEventListener('pointerdown', (ev) => {
        const pill = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (!pill || pill.dataset.gdefault === '1' || S.editing) return;
        cancelPress();
        pressTimer = setTimeout(() => {
          pressTimer = null;
          deleteGroup(pill.dataset.gid);
        }, LONGPRESS_MS);
      });
      groups.addEventListener('pointerup', cancelPress);
      groups.addEventListener('pointerleave', cancelPress);
      groups.addEventListener('pointercancel', cancelPress);
      groups.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        if (t.closest('[data-act="gadd"]')) {
          ev.stopPropagation();
          addGroup();
          return;
        }
        const del = t.closest('[data-act="gdel"]');
        if (del) {
          ev.stopPropagation();
          const pill = del.closest('.wl-group');
          if (pill) deleteGroup(pill.dataset.gid);
          return;
        }
        const pill = t.closest('.wl-group');
        if (!pill || S.editing) return;
        if (pill.dataset.gid === S.group) return;
        S.group = pill.dataset.gid;
        render();
      });
      groups.addEventListener('dblclick', (ev) => {
        const pill = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (!pill || pill.dataset.gdefault === '1') return;
        ev.preventDefault();
        cancelPress();
        startRename(pill);
      });
      groups.addEventListener('contextmenu', (ev) => {
        const pill = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (!pill || pill.dataset.gdefault === '1') return;
        ev.preventDefault();
        cancelPress();
        deleteGroup(pill.dataset.gid);
      });

      // ---- 事件 · 卡片（✕ 移除 / 名称派发跨卡事件）
      body.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const rm = t.closest('[data-role="remove"]');
        if (rm) {
          ev.stopPropagation();
          removeItem(rm.dataset.code);
          return;
        }
        const kl = t.closest('[data-role="kline"]');
        if (kl) {
          ev.stopPropagation();
          emit('panel:open-kline', { code: kl.dataset.code, name: kl.dataset.name || '' });
          return;
        }
        const sc = t.closest('[data-role="sector"]');
        if (sc) {
          ev.stopPropagation();
          emit('panel:open-sector', { code: sc.dataset.code, name: sc.dataset.name || '' });
        }
      });

      // ---- 事件 · 拖拽（组内改序 → /reorder；拖到同类型分组标签 → /move）
      const clearDropMarks = () => {
        for (const pill of S.el.groups.querySelectorAll('.wl-group.is-drop')) pill.classList.remove('is-drop');
      };

      // 分组标签拖完 → 把当前 DOM 顺序写成 order（只动 order，不动其它字段与条目）
      const saveGroupOrder = async () => {
        const box = S.el.groups;
        if (!box) return;
        const ids = [];
        for (const el of box.querySelectorAll('.wl-group')) {
          const gid = el.getAttribute('data-gid');
          if (gid && ids.indexOf(gid) < 0) ids.push(gid);
        }
        if (!ids.length) return;
        try {
          await api.post('/api/watchlist/group/reorder', { ids: ids });
          showMsg('分组顺序已保存');
        } catch (e) {
          showMsg('分组顺序保存失败：' + errText(e), true);
        }
        S.data = null;
        await loadAll();
      };

      wrap.addEventListener('dragstart', (ev) => {
        // 2026-11-13 主人：分组标签也要能拖（原来只按生成时间排 order，无法手工调）
        const grpEl = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (grpEl && grpEl.getAttribute('data-gid')) {
          S.gdrag = { id: grpEl.getAttribute('data-gid'),
                      kind: grpEl.getAttribute('data-gkind'), el: grpEl };
          grpEl.classList.add('is-drag');
          try {
            ev.dataTransfer.setData('text/plain', grpEl.getAttribute('data-gid'));
            ev.dataTransfer.effectAllowed = 'move';
          } catch (e) { /* 老浏览器忽略 */ }
          return;
        }
        const cardEl = ev.target && ev.target.closest ? ev.target.closest('.wl-wc') : null;
        if (!cardEl) return;
        S.drag = { code: cardEl.dataset.code, kind: cardEl.dataset.kind, moved: false };
        cardEl.classList.add('is-drag');
        try {
          ev.dataTransfer.setData('text/plain', cardEl.dataset.code);
          ev.dataTransfer.effectAllowed = 'move';
        } catch (e) { /* 老浏览器忽略 */ }
      });

      wrap.addEventListener('dragover', (ev) => {
        // 分组标签拖动中：同 kind 内换位（不跨个股/板块）
        if (S.gdrag) {
          const pill0 = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
          if (!pill0 || pill0 === S.gdrag.el) return;
          if (pill0.getAttribute('data-gkind') !== S.gdrag.kind) return;
          ev.preventDefault();
          const r0 = pill0.getBoundingClientRect();
          const after0 = (ev.clientX - r0.left) > r0.width / 2;
          pill0.parentNode.insertBefore(S.gdrag.el, after0 ? pill0.nextSibling : pill0);
          return;
        }
        if (!S.drag) return;
        const t = ev.target;
        if (!t || !t.closest) return;
        const pill = t.closest('.wl-group');
        if (pill) {
          // 跨类型不给落点（个股组↔板块组）：不 preventDefault，浏览器显示不可放
          if (pill.dataset.gkind === S.drag.kind) {
            ev.preventDefault();
            pill.classList.add('is-drop');
          }
          return;
        }
        const cardEl = t.closest('.wl-wc');
        if (!cardEl || cardEl.dataset.code === S.drag.code) return;
        if (cardEl.dataset.group !== S.group) return;   // 异组卡片不在此重排（换组走分组标签）
        ev.preventDefault();
        const src = S.els.get(S.drag.code);
        if (!src || !cardEl.parentNode) return;
        const r = cardEl.getBoundingClientRect();
        const after = (ev.clientX - r.left) > r.width / 2;
        cardEl.parentNode.insertBefore(src, after ? cardEl.nextSibling : cardEl);
        S.drag.moved = true;
      });

      wrap.addEventListener('dragleave', (ev) => {
        const pill = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (pill) pill.classList.remove('is-drop');
      });

      wrap.addEventListener('drop', (ev) => {
        if (!S.drag) return;
        const pill = ev.target && ev.target.closest ? ev.target.closest('.wl-group') : null;
        if (pill && pill.dataset.gkind === S.drag.kind) {
          ev.preventDefault();
          const code = S.drag.code;
          const gid = pill.dataset.gid;
          S.drag = null;
          clearDropMarks();
          moveCard(code, gid);        // 换组
          return;
        }
        ev.preventDefault();
      });

      wrap.addEventListener('dragend', (ev) => {
        if (S.gdrag) {
          const g = S.gdrag;
          S.gdrag = null;
          if (g.el) g.el.classList.remove('is-drag');
          saveGroupOrder();
          return;
        }
        const cardEl = ev.target && ev.target.closest ? ev.target.closest('.wl-wc') : null;
        if (cardEl) cardEl.classList.remove('is-drag');
        clearDropMarks();
        const drag = S.drag;
        S.drag = null;
        if (!drag || !drag.moved) return;
        const codes = [];
        for (const card of S.el.body.querySelectorAll('.wl-wc')) codes.push(card.dataset.code);
        reorderGroup(codes, S.group);   // 组内改序：只提交本组码（带 group）
      });

      // 切 Tab 回来：模块缓存里还有上一帧的条目与日线，先画出来（不等接口），再走正常刷新。
      // 缓存超过 PAYLOAD_CACHE_MS 就不用（宁可等新数据，也不把旧价当成现价）。
      const pc = MOD.payload;
      if (pc.data && (Date.now() - pc.at) < PAYLOAD_CACHE_MS) {
        const lc = MOD.levels;
        if (lc.data && (Date.now() - lc.at) < LEVELS_CACHE_MS) S.levels = lc.data;
        applyData(pc.data);
      }
      await loadAll();
    },

    async unmount() {
      const S = this._wl;
      if (!S) return;
      S.alive = false;
      clearTimeout(S.searchTimer);
      if (S.onDocDown) document.removeEventListener('mousedown', S.onDocDown, true);
      S.els.clear();
      this._wl = null;
    },

    // 5s 刷新：三种情况跳过（任务书 §契约 6；同旧 Tab renderWatchlist 的规矩）
    async refresh() {
      const S = this._wl;
      if (!S || !S.alive) return;
      if (S.drag) return;                     // ① 拖拽进行中（防重建打断拖拽）
      if (S.editing) return;                  // ② 分组名编辑中（防丢输入）
      const input = S.el.input;
      if (input && (document.activeElement === input || String(input.value || '').trim())) return;  // ③ 搜索中
      await S.reload();
    },
  }],
};
