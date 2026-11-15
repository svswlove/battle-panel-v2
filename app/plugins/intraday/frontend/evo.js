/**
 * intraday 卡 · 日内演化（活跃指数 × 指数贡献演变）
 * 照旧页 engine/web/index.html:3689-4313 搬（`_evo*` 共 55 个函数），逐函数对应见文末「函数对照」。
 *
 * 与旧页的**四处结构性差别**（都是「整页 → 卡片」的必然结果，语义一个没动）：
 *
 * ① **模块级状态 → 每实例状态**。旧页 17 个模块级变量（`_evoDir/_evoMode/_evoSrt/_evoSel/_evoHover/
 *    `_evoCursor/_evoIdxOn/_evoRange/_evoRangeDrag/_evoDrag/_evoRangeBase/_evoWin/_evoDelegated/
 *    `_evoMouse/_evoWheelLock/_evoBfState/_evoBfTimer`）全部下沉到 `createState()` 的返回值。
 *    旧页靠 `let _evoDelegated=false` + `if(!host||_evoDelegated) return` 防重复绑，因为
 *    `#panel-intraday` 每 3s 被整体重建、事件会跟着丢。卡片里宿主是 mount 时新建的节点、
 *    `paint()` 只重建 `.evo-body` 的 innerHTML，委托绑在**不参与重绘的** `.it-card` 上，
 *    「只绑一次」由「mount 一次」天然保证；每实例自带 AbortController，unmount 一次全解
 *    （含 window 上的 keydown 与 blur）。
 *
 * ② **`getElementById` → 容器内查询**。旧页 5 个 id 在卡片里都不存在：
 *    `#amvEvoBody`(14 处) → `st.body`；`#evoCard`(5 处) → `st.body.querySelector('.evo-card')`；
 *    `#evoPaneL`(2 处) → 左栏 `.evo-pane`；`#panel-intraday`(1 处，委托宿主) → `st.el`；
 *    `#evoBfBtn`(1 处) → 由 `_evoBfLabel()` 的状态位驱动重绘，不再按 id 改文案。
 *    **注意 `.evo-card` 必须每次现查**：它就在 `st.body` 里，paint 会把它一起换掉。
 *
 * ③ **浮层留容器内**。`.evo-card` 旧页本来就是 `position:absolute` 挂在
 *    `.evo-pane{position:relative}` 里（不是 body + fixed），所以只把 pane 换成容器内节点即可；
 *    坐标算法（`_evoPlaceCard`）逐字照搬。（盘中预测浮层 `_evoPlaceFc` 已于 2026-11-05 随预测功能一并删除。）
 *
 * ④ **内联 onclick → data-act 委托**。旧页 6 类内联调用
 *    （`evoSetMode / evoSet / evoSetSrt / evoToggleIdx / evoBackfill / evoClearRange`）
 *    改成 `data-act="evo-mode|evo-dir|evo-srt|evo-idx|evo-bf|evo-clear"`，由 `.it-card` 上的
 *    一个委托按旧页同款 `closest('[data-*]')` 分派。
 *
 * 三处**有意偏离**（不改行为，只为卡里能跑）：
 *   a. `_EVO_PAL`(10 色) / `_EVO_IDX_PAL`(6 色) 是裸 hex —— 旧页标注 `theme-lint-allow: 折线多色区分`。
 *      这里**同样登记为例外**（`app/tests/test_fe_shell_bridge.py` 的 `HEX_ALLOWED_BY_FILE`），
 *      理由与旧页一致：多色折线必须用固定色互相区分，派生自主题色无法保证 10 条线可分。
 *   b. 回填按钮走 `ctx.api.post` / `ctx.api.get`（卡片统一取数通道），旧页是裸 fetch。
 *      端点与语义不变：POST /api/sector_track_backfill、GET /api/sector_track_backfill_status。
 *   c. `_evoBfReload` 旧页调 `_stAt = 0; loadSectorTrack()` 强刷轨迹缓存；卡片里轨迹缓存是
 *      frontend.js 的 `S.trackRaw / S.trackAt`（**不在 sector.js**），故改为调 hooks.reloadTrack()，
 *      由 frontend.js 清缓存并重取，再回灌 `st.hooks.track()`。sector.js 一行未动。
 *
 * 其余**逐字照搬**、一处不改的硬骨：
 *   · `data-ml/mr/w/n/off` 五个数据属性 —— 四套手势全靠 `_evoIdxAt` 用它们把 clientX 反算成采样点索引；
 *   · `_evoFitH` 空函数（旧页 2026-10-18 修的「量高写回 → 正反馈越算越大」，调用点保留）；
 *   · 滚轮 90ms 节流（`_evoWheelLock`，「同一手势不要当多次缩放」）；
 *   · 右键松手即把 `_evoWin` 收窄到该段（照源码 app.js:366「框选即切统计区间」），并**清掉** `_evoRange`；
 *   · `_evoCardUpdate` 的三种收起条件（无光标且无区间 / 无行 / 无快照）——2026-10-18 修过「卡片挂在屏上关不掉」。
 */

import { esc, num, pct, clsUp } from './format.js';
import { snapsOf, lineD, lastOf, contribOf } from './caliber.js';

// ---------------------------------------------------------------- 调色板（旧页 4064 / 4068）

/** 旧页 4064：贡献图 10 色折线。theme-lint-allow: 折线多色区分（见文件头偏离 a） */
export const EVO_PAL = ['#CB5A48', '#2F6FEB', '#129D6B', '#B8791A', '#8B5CF6',
  '#D6336C', '#0E8F9E', '#7A8B1F', '#4C6EF5', '#C2600F'];

/** 旧页 4065：六指数（顺序即图例顺序）。2026-11-02 主人：原「中证2000 932000.SH」换成「国证2000 399303.SZ」——
 *  932000 在通达信属「中证指数」扩展市场（62），77709 标准行情没有，eltdx 取不到；国证2000 在深市段，走 7709 正常。 */
export const EVO_IDX = [['000001.SH', '上证指数'], ['399006.SZ', '创业板指'], ['000688.SH', '科创50'],
  ['000300.SH', '沪深300'], ['000852.SH', '中证1000'], ['399303.SZ', '国证2000']];

/** 旧页 4068：六指数色板 = 源码图例的 --curve 值。theme-lint-allow: 指数线区分色（偏离 a） */
export const EVO_IDX_PAL = ['#bc94e8', '#61c8c0', '#e387ac', '#74a8ed', '#a6ce72', '#e88174'];

/** 旧页 4088：左图 viewBox 高（2026-10-18 主人「拉长一点」，等比渲染字号不变形） */
export const EVO_CHART_H = 330;

// ---------------------------------------------------------------- 固定时间轴（2026-10-26 主人定）
//
// 「这两张图表的滚轮缩放功能取消。固定时间轴，盘中不再拉伸K线。
//   例如11点30K线就只能走到一半。」
//
// 旧页（含本模块之前版本）的 X 是**按采样点索引**映射的：`X(i)=ML + i*(W-ML-MR)/(n-1)`。
// 后果：盘中只有一半数据时，那半天的曲线会被拉伸到整个宽度，看上去像「一天已经走完」。
// 改成**交易日分钟轴**：09:30–11:30 + 13:00–15:00 共 240 分钟，午休压缩
// （11:30 与 13:00 共用同一个 X＝半场）。于是 11:30 恰好落在 50%，实时增长、不拉伸。
// 同时滚轮缩放与 Shift 平移一并取消（它们都是改缩放比的，与「固定」互斥）。
export const SESS_OPEN = 9 * 60 + 30;
export const SESS_NOON = 11 * 60 + 30;
export const SESS_AFTER = 13 * 60;
export const SESS_CLOSE = 15 * 60;
/** 交易分钟总数（上午 120 + 下午 120） */
export const SESS_MIN = (SESS_NOON - SESS_OPEN) + (SESS_CLOSE - SESS_AFTER);

/** "YYYY-MM-DD HH:MM:SS" → 当日分钟数；取不到返回 null */
function minOfDay(ts) {
  const s = String(ts == null ? '' : ts);
  const hh = Number(s.slice(11, 13));
  const mm = Number(s.slice(14, 16));
  if (!isFinite(hh) || !isFinite(mm) || s.length < 16) return null;
  return hh * 60 + mm;
}

/** 当日分钟 → 交易日分钟（0..240，午休压到半场）；盘前/盘后一律夹到端点 */
function tradingMin(ts) {
  const m = minOfDay(ts);
  if (m == null) return 0;
  if (m <= SESS_NOON) return Math.max(0, Math.min(120, m - SESS_OPEN));
  if (m >= SESS_AFTER) return Math.max(120, Math.min(SESS_MIN, 120 + (m - SESS_AFTER)));
  return 120;   // 午休（正常数据没有点，兜底压到半场）
}

// ---------------------------------------------------------------- 实例状态（约束①）

/**
 * 视角状态“最后选择”（模块级，随页存活；2026-10-28 N20261028-140538-40）。
 * shell 切卡会 unmount→ mount（实测：离开后 .evo-body 消失、回来重建），实例状态会归零；
 * 主人选了「正负对比 / 行情涨跌 / AMV 排序」再切回来发现回到正向，就会被读成「功能不可用」。
 * 故只把**视角四项**存到模块级，数据状态（sel/hover/cursor/区间）仍只活在本实例。
 */
const VIEW = { dir: 'pos', mode: 'amv', srt: 'contrib', idxOn: {} };

export function createState(cardEl, bodyEl, hooks) {
  return {
    el: cardEl,                       // 委托宿主 = .it-card（旧页 #panel-intraday）
    body: bodyEl,                     // 重绘容器（旧页 #amvEvoBody）
    hooks: hooks || {},               // { track(), amv(), api, reloadTrack() }
    alive: true,
    // 旧页 3680-3686 的模块级状态，逐个下沉；视角三项（方向/模式/排序/叠加）从 VIEW 恢复：
    // 切到别的卡再回来会重建实例（shell unmount → mount），不读 VIEW 就会静默掉回默认值。
    dir: VIEW.dir, mode: VIEW.mode, srt: VIEW.srt, sel: null, hover: null, cursor: null,
    idxOn: Object.assign({}, VIEW.idxOn),   // 六指数叠加开关（源码 data-index-toggle）
    range: null, rangeDrag: false, drag: null, rangeBase: null, win: null,
    mouse: { x: 0, y: 0 },
    bfState: '', bfTimer: null, bfTimers: [],
    ac: new AbortController(),
  };
}

const trackOf = (st) => ((st.hooks && st.hooks.track) ? st.hooks.track() : null) || null;
const evoSnaps = (st) => snapsOf(trackOf(st));

// ---------------------------------------------------------------- 视图窗口 / 索引映射（旧页 3712-3730）

/** 旧页 3712 `_evoView`：当前日期窗口 → 实际绘制的采样点范围（缩放/平移后只画这一段） */
function viewOf(st, snaps) {
  const n = snaps.length;
  if (!st.win || n < 3) return { snaps: snaps, off: 0, n: n };
  const from = Math.max(0, Math.min(n - 1, st.win.from));
  const to = Math.max(0, Math.min(n - 1, st.win.to));
  if (to - from < 2) return { snaps: snaps, off: 0, n: n };
  return { snaps: snaps.slice(from, to + 1), off: from, n: to - from + 1 };
}

/**
 * 屏幕 X → 采样点索引。
 * **固定时间轴（2026-10-26）**：不再用「索引→比例」，而是先把 clientX 反算成**交易日分钟**，
 * 再取时间最近的采样点——与曲线同一个映射，光标线才会压在线上。
 */
function idxAt(st, svg, clientX) {
  if (!svg) return null;
  const box = svg.getBoundingClientRect();
  if (!box.width) return null;
  const snaps = evoSnaps(st); const n = snaps.length;
  if (!(n > 0)) return null;
  const ML = +svg.dataset.ml; const MR = +svg.dataset.mr; const W = +svg.dataset.w;
  const vx = (clientX - box.left) * (W / box.width);
  const plot = W - ML - MR;
  const want = Math.max(0, Math.min(SESS_MIN, ((vx - ML) / (plot || 1)) * SESS_MIN));
  let best = 0; let bestD = Infinity;
  for (let i = 0; i < n; i++) {
    const d = Math.abs(tradingMin(snaps[i].ts) - want);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

/** 全局索引 → 图内 X 像素（与曲线同一映射：固定时间轴） */
function pxOf(st, svg, gidx) {
  const snaps = evoSnaps(st);
  const s = snaps[gidx];
  const ML = +svg.dataset.ml; const MR = +svg.dataset.mr; const W = +svg.dataset.w;
  return ML + (tradingMin(s && s.ts) / SESS_MIN) * (W - ML - MR);
}

/** 旧页 3878 `_evoToGlobal`：局部索引（图内 data-n）→ 全局索引（snapshots） */
function toGlobal(svg, localIdx) {
  const off = +((svg && svg.dataset.off) || 0);
  return (localIdx == null) ? null : localIdx + off;
}

/** 旧页 3882 `_evoToLocal`：全局索引 → 图内局部索引 */
function toLocal(svg, globalIdx) {
  const off = +((svg && svg.dataset.off) || 0);
  return (globalIdx == null) ? null : globalIdx - off;
}

const chartSvgs = (st) => (st.body ? st.body.querySelectorAll('svg.st-chart[data-evo]') : []);
const evoCardEl = (st) => (st.body ? st.body.querySelector('.evo-card') : null);
const paneLEl = (st) => (st.body ? st.body.querySelector('.evo-pane') : null);

// ---------------------------------------------------------------- 口径（旧页 4066-4100 / 4211-4223）

/** 旧页 4069 `_evoIdxVal`：快照里的指数涨跌（%） */
function idxVal(snap, code) {
  const v = (snap && snap.idx_chg) ? snap.idx_chg[code] : null;
  return (v == null || !isFinite(v)) ? null : v;
}

/**
 * 2026-10-27（N20261027-100741-29 等 4 条标注）：**模式隔离**。
 * 行情涨跌（quote）模式的左图只画指数，不得带 AMV 专属数值/全A 信息/预测浮层；
 * 只有 AMV 模式才显示活跃指数读数与全A 摘要行。
 */
export const MODE_TITLE = { quote: '行情涨跌', amv: '活跃指数' };

/** 已勾选的指数列表（顺序恒为 EVO_IDX 顺序，图例与曲线同序） */
function idxOnList(st) {
  return EVO_IDX.filter((pair) => st.idxOn[pair[0]]);
}

/**
 * 行情涨跌模式实际要画的指数：勾选优先；一个都没勾时**不画空白图**，回退前两个。
 * 返回 { list, fallback }（fallback=true 时标题会说「默认」）。
 */
function quoteOn(st) {
  const on = idxOnList(st);
  return on.length ? { list: on, fallback: false } : { list: EVO_IDX.slice(0, 2), fallback: true };
}

/** 当前模式真正画在图上的指数（光标卡 / 区间卡与曲线同源） */
function drawnIdx(st) {
  return st.mode === 'quote' ? quoteOn(st).list : idxOnList(st);
}

/** 旧页 4074 `_evoRankVal`：排序依据 amv → 板块 amv_pct；否则 → 增量贡献（‰） */
function rankVal(st, code, snap) {
  if (st.srt === 'amv') {
    const b = (snap && snap.boards) ? snap.boards[code] : null;
    const v = b ? b.amv_pct : null;
    return (v == null || !isFinite(v)) ? null : v;
  }
  return contribOf(trackOf(st), code, snap);
}

/** 旧页 4076 `_evoUnit` */
const unitOf = (st) => (st.srt === 'amv' ? '%' : '‰');

/** 旧页 4077 `_evoFmt` */
const fmtVal = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + num(v, 2));

/** 旧页 4078 `_evoPick`：TOP8 / BOTTOM8 / 正负各 5（去重） */
function pick(st, latest) {
  const arr = Object.keys((latest && latest.boards) || {})
    .map((c) => ({ c: c, v: rankVal(st, c, latest), name: ((latest.boards[c] || {}).name) || c }))
    .filter((x) => x.v != null);
  arr.sort((a, b) => b.v - a.v);
  // 2026-10-26 主人：「板块由8个增加为10个」——正好对上 EVO_PAL 的 10 色，不多不少
  const K = 10;
  if (!arr.length) return [];
  if (st.dir === 'neg') return arr.slice(-K).reverse();
  if (st.dir === 'all') {
    const out = []; const seen = {};
    arr.slice(0, 5).concat(arr.slice(-5).reverse()).forEach((x) => {
      if (!seen[x.c]) { seen[x.c] = 1; out.push(x); }
    });
    return out;
  }
  return arr.slice(0, K);
}

/** 旧页 4100 `_evoTime` */
function evoTime(snaps) {
  const a = (snaps[0] || {}).ts || '';
  const b = (snaps[snaps.length - 1] || {}).ts || '';
  return (a.slice(11, 16) || '—') + '—' + (b.slice(11, 16) || '—');
}

/**
 * 旧页 4102 `_evoXl`：X 轴时间标签。
 * **固定时间轴（2026-10-26）**：不再是「首/中/末采样点」，而是死写 09:30 / 11:30 / 15:00
 * —— 轴不动，标签也不该跟着数据飘。
 */
function evoXl(ML, W, MR, H) {
  const mid = ML + (W - ML - MR) / 2;
  return '<text class="st-xtx" x="' + ML + '" y="' + (H - 6) + '" text-anchor="start">09:30</text>'
    + '<text class="st-xtx" x="' + mid.toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle">11:30</text>'
    + '<text class="st-xtx" x="' + (W - MR) + '" y="' + (H - 6) + '" text-anchor="end">15:00</text>';
}

// ---------------------------------------------------------------- 三张图（旧页 4111-4251）

/** 旧页 4111 `_evoAmvChart`：左图模式A —— 活跃指数（+ 可选叠加六指数，同一根 % 轴） */
function amvChart(st, Hin) {
  const snaps = evoSnaps(st);
  const v = viewOf(st, snaps);
  const ss = v.snaps; const n = ss.length;
  const vals = ss.map((s) => s.all).filter((x) => x != null && isFinite(x));
  if (!vals.length) return '<p class="muted">暂无数据</p>';
  const W = 640; const H = (Hin || EVO_CHART_H); const ML = 46; const MR = 60; const MT = 12; const MB = 22;
  // 固定时间轴：X 只看时间，不看索引（盘中不拉伸）
  const plotW = W - ML - MR;
  const X = (i) => ML + (tradingMin(ss[i] && ss[i].ts) / SESS_MIN) * plotW;
  const on = EVO_IDX.filter((pair) => st.idxOn[pair[0]]);
  const over = on.map((pair) => ss.map((s) => idxVal(s, pair[0])));
  const allv = vals.concat.apply(vals, over.map((a) => a.filter((x) => x != null && isFinite(x))));
  let lo = Math.min.apply(null, allv); let hi = Math.max.apply(null, allv);
  const pd = (hi - lo) * 0.15 || 0.5; lo -= pd; hi += pd;
  const Y = (x) => MT + (H - MT - MB) * (1 - (x - lo) / (hi - lo));
  let g = '';
  for (let t = 0; t <= 4; t++) {
    const val = lo + (hi - lo) * t / 4; const y = Y(val);
    g += '<line class="st-grid" x1="' + ML + '" y1="' + y.toFixed(1) + '" x2="' + (W - MR) + '" y2="' + y.toFixed(1) + '"/>'
      + '<text class="st-ytx" x="' + (ML - 6) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">'
      + (val > 0 ? '+' : '') + val.toFixed(2) + '%</text>';
  }
  const d = lineD(ss, ss.map((s) => s.all), X, Y);
  const lastV = lastOf(ss.map((s) => s.all));
  let ov = '';
  on.forEach((pair, i) => {
    const ci = EVO_IDX.findIndex((x) => x[0] === pair[0]);
    const dd = lineD(ss, over[i], X, Y);
    if (!dd) return;
    ov += '<path d="' + dd + '" fill="none" stroke="' + EVO_IDX_PAL[ci % EVO_IDX_PAL.length]
      + '" stroke-width="1.2" opacity=".9"/>';
  });
  return '<svg class="st-chart" data-evo="amv" data-ml="' + ML + '" data-mr="' + MR + '" data-w="' + W
    + '" data-n="' + n + '" data-off="' + v.off + '" viewBox="0 0 ' + W + ' ' + H
    + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="沪深全A活跃指数（昨收=100 相对涨跌%）">'
    + g + '    <line class="st-base" x1="' + ML + '" y1="' + Y(0).toFixed(1) + '" x2="' + (W - MR)
    + '" y2="' + Y(0).toFixed(1) + '"/>\n    ' + ov
    + '\n    <path d="' + d + '" fill="none" stroke="var(--accent)" stroke-width="1.6"/>'
    + '\n    ' + (lastV == null ? '' : '<text class="st-lab" x="' + (W - MR + 6) + '" y="'
      + (Y(lastV) + 3).toFixed(1) + '" fill="var(--accent)">' + fmtVal(lastV) + '%</text>')
    + '\n    <g class="evo-range" display="none"></g>'
    + '\n    <g class="evo-cross" display="none"></g>'
    + '\n    ' + evoXl(ML, W, MR, H) + '</svg>';
}

/** 旧页 4183 `_evoQuoteChart`：左图模式B —— 只画已勾选的指数 */
function quoteChart(st, Hin) {
  const snaps = evoSnaps(st);
  const v = viewOf(st, snaps);
  const ss = v.snaps; const n = ss.length;
  if (!n) return '<p class="muted">暂无数据</p>';
  const on = quoteOn(st).list;
  const all6 = on.map((pair) => ss.map((s) => idxVal(s, pair[0])));
  if (!all6.some((a) => a.some((x) => x != null))) {
    // 2026-10-27：不再写「自 2026-10-12 起采样」那种含糊话。分两种：
    //   ① 快照里一个 idx_chg 都没有 → 这是数据缺口（后端没补上 / 行情不可用），说清楚；
    //   ② 有字段但本段全是 null → 说本段无值。两种都不补 0、不画空图。
    const anyField = ss.some((s) => s && s.idx_chg);
    const names = on.map((p) => p[1]).join(' / ');
    if (anyField) {
      return '<p class="muted">' + esc(names) + ' 本段无值（快照有 idx_chg 字段，但这些指数缺值）</p>';
    }
    // 历史交易日的轨迹本来就不会被补（后端只给当日补，不拿今天曲线贴历史）——话要说准。
    if (isHistoricalDay(st)) {
      return '<p class="muted">历史交易日无指数分钟数据（只在当日轨迹上补 idx_chg）</p>';
    }
    return '<p class="muted">暂无指数涨跌数据（轨迹快照缺 idx_chg，且当日指数分钟线不可用）</p>';
  }
  const W = 640; const H = (Hin || EVO_CHART_H); const ML = 46; const MR = 78; const MT = 12; const MB = 22;
  const plotW = W - ML - MR;
  const X = (i) => ML + (tradingMin(ss[i] && ss[i].ts) / SESS_MIN) * plotW;
  // 2026-10-28（N20261028-140428-39「行情涨跌也需要跟 amv 涨跌一样用全A作为基准线」）：
  // 把全A（= AMV 模式那根主线，快照的 all）同时画进行情图当基准，指数跑输/跑赢一眼可见。
  // 只是**多画一条已存在的序列**：不新增指标、不重算任何值；Y 轴范围把全A 一起算进去。
  const allSer = ss.map((s) => ((s && s.all != null && isFinite(s.all)) ? s.all : null));
  const allv = [];
  all6.forEach((a) => a.forEach((x) => { if (x != null && isFinite(x)) allv.push(x); }));
  allSer.forEach((x) => { if (x != null) allv.push(x); });
  allv.push(0);
  let lo = Math.min.apply(null, allv); let hi = Math.max.apply(null, allv);
  const pd = (hi - lo) * 0.12 || 0.3; lo -= pd; hi += pd;
  const Y = (x) => MT + (H - MT - MB) * (1 - (x - lo) / (hi - lo));
  let g = '';
  for (let t = 0; t <= 4; t++) {
    const val = lo + (hi - lo) * t / 4; const y = Y(val);
    g += '<line class="st-grid" x1="' + ML + '" y1="' + y.toFixed(1) + '" x2="' + (W - MR) + '" y2="' + y.toFixed(1) + '"/>'
      + '<text class="st-ytx" x="' + (ML - 6) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">'
      + (val > 0 ? '+' : '') + val.toFixed(2) + '%</text>';
  }
  // 全A 基准线先画（垫在指数线下面），类名 evo-allbase 供测试「基准线存在」与剔除基准后比旧页
  const dAll = lineD(ss, allSer, X, Y);
  let lines = dAll
    ? '<path class="evo-allbase" d="' + dAll + '" fill="none" stroke="var(--accent)" stroke-width="1.6"'
      + ' opacity=".95"/>'
    : '';
  on.forEach((pair, i) => {
    const ci = EVO_IDX.findIndex((x) => x[0] === pair[0]);
    const d = lineD(ss, all6[i], X, Y);
    if (!d) return;
    lines += '<path d="' + d + '" fill="none" stroke="' + EVO_IDX_PAL[ci % EVO_IDX_PAL.length]
      + '" stroke-width="1.3"/>';
  });
  // 末值标签：全A 与指数同列（按 Y 排序避碰），全A 用主色，一抬眼就知道哪根是基准
  const vAll = lastOf(allSer);
  const lyAll = [];
  if (vAll != null) lyAll.push({ name: '全A', k: 'ALL', v: vAll, col: 'var(--accent)' });
  on.forEach((pair, i) => {
    const ci = EVO_IDX.findIndex((x) => x[0] === pair[0]);
    lyAll.push({ name: pair[1], k: pair[0], v: lastOf(all6[i]), col: EVO_IDX_PAL[ci % EVO_IDX_PAL.length] });
  });
  const ly = lyAll.filter((L) => L.v != null).map((L) => Object.assign({}, L, { y: Y(L.v) }))
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ly.length; i++) if (ly[i].y - ly[i - 1].y < 12) ly[i].y = ly[i - 1].y + 12;
  ly.forEach((L) => {
    lines += '<text class="st-lab" x="' + (W - MR + 6) + '" y="' + (L.y + 3).toFixed(1)
      + '" fill="' + L.col + '">' + esc(L.name) + ' ' + fmtVal(L.v) + '%</text>';
  });
  return '<svg class="st-chart" data-evo="quote" data-ml="' + ML + '" data-mr="' + MR + '" data-w="' + W
    + '" data-n="' + n + '" data-off="' + v.off + '" viewBox="0 0 ' + W + ' ' + H
    + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="指数行情涨跌对照">' + g
    + '\n    <line class="st-base" x1="' + ML + '" y1="' + Y(0).toFixed(1) + '" x2="' + (W - MR)
    + '" y2="' + Y(0).toFixed(1) + '"/>'
    + '\n    ' + lines + '<g class="evo-range" display="none"></g><g class="evo-cross" display="none"></g>'
    + evoXl(ML, W, MR, H) + '</svg>';
}

/** 旧页 4224 `_evoContribChart`：右图 —— 按排序取 TOP/BOTTOM 画多色折线 + 透明命中层 */
function contribChart(st) {
  const all = evoSnaps(st);
  const v = viewOf(st, all);
  const ss = v.snaps; const n = ss.length;
  if (!n) return '<p class="muted">暂无数据</p>';
  const sel = pick(st, all[all.length - 1]);
  if (!sel.length) return '<p class="muted">暂无数据（缺昨收权重）</p>';
  // 右图 viewBox 与容器同比例（旧页 2026-10-18）：容器 ≈ 654×300，取 650×300，xMidYMid meet 时几乎无留白
  const W = 650; const H = 300; const ML = 52; const MR = 24; const MT = 12; const MB = 22;
  const plotW = W - ML - MR;
  const X = (i) => ML + (tradingMin(ss[i] && ss[i].ts) / SESS_MIN) * plotW;
  const series = sel.map((x) => ss.map((s) => rankVal(st, x.c, s)));
  const allv = [];
  series.forEach((s) => s.forEach((x) => { if (x != null && isFinite(x)) allv.push(x); }));
  allv.push(0);
  let lo = Math.min.apply(null, allv); let hi = Math.max.apply(null, allv);
  const pd = (hi - lo) * 0.12 || 0.05; lo -= pd; hi += pd;
  const Y = (x) => MT + (H - MT - MB) * (1 - (x - lo) / (hi - lo));
  let g = '';
  for (let t = 0; t <= 4; t++) {
    const val = lo + (hi - lo) * t / 4; const y = Y(val);
    g += '<line class="st-grid" x1="' + ML + '" y1="' + y.toFixed(1) + '" x2="' + (W - MR) + '" y2="' + y.toFixed(1) + '"/>'
      + '<text class="st-ytx" x="' + (ML - 6) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">'
      + val.toFixed(2) + '</text>';
  }
  let lines = '';
  sel.forEach((x, i) => {
    const col = EVO_PAL[i % EVO_PAL.length];
    const on = (st.sel === x.c);
    const dim = ((st.sel != null) && !on);
    const d = lineD(ss, series[i], X, Y);
    if (!d) return;
    lines += '<path class="evo-hit" data-code="' + esc(x.c) + '" d="' + d + '" fill="none" stroke="transparent" stroke-width="9"/>';
    lines += '<path class="evo-line" data-code="' + esc(x.c) + '" d="' + d + '" fill="none" stroke="' + col
      + '" stroke-width="' + (on ? 2.6 : 1.3) + '" opacity="' + (dim ? 0.22 : 1) + '"/>';
  });
  return '<svg class="st-chart" data-evo="contrib" data-ml="' + ML + '" data-mr="' + MR + '" data-w="' + W
    + '" data-n="' + n + '" data-off="' + v.off + '" viewBox="0 0 ' + W + ' ' + H
    + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="指数贡献演变">' + g
    + '\n    <line class="st-base" x1="' + ML + '" y1="' + Y(0).toFixed(1) + '" x2="' + (W - MR)
    + '" y2="' + Y(0).toFixed(1) + '"/>'
    + '\n    ' + lines + '<g class="evo-range" display="none"></g><g class="evo-cross" display="none"></g>'
    + evoXl(ML, W, MR, H) + '</svg>';
}

/** 旧页 4252 `_evoRankHtml`：右侧板块榜（与曲线同色同序）
 * 2026-10-26：去掉 `data-open-amv`（点行不再弹板块详情），改成 `data-act="evo-goto"`，
 * 点击行为交由 hooks.pickBoard 接管（跳到下方研究卡并选中该板块）。
 * 2026-10-28（N20261028-140538-40「正负对比功能不可用」）：5+5 两组中间加一条分组标，
 * 不然十个名字排成一列、与「正向」几乎看不出区别，点完像没反应。
 */
function rankHtml(st, sel) {
  if (!sel.length) return '';
  const split = (st.dir === 'all') && sel.length > 5;
  return '<div class="evo-rank">' + sel.map((x, i) => {
    const col = EVO_PAL[i % EVO_PAL.length];
    const on = (st.sel === x.c);
    const sep = (split && i === 0) ? '<div class="evo-rank-sep">正向</div>'
      : ((split && i === 5) ? '<div class="evo-rank-sep">负向</div>' : '');
    return sep + '<div class="evo-rank-row' + (on ? ' sel' : '') + '" data-code="' + esc(x.c)
      + '" data-ctx="sector" data-name="' + esc(x.name) + '"'
      + ' data-act="evo-goto" data-goto="' + esc(x.c)
      + '" title="' + esc(x.name) + ' · 悬停高亮曲线 · 点击跳到下方「板块与个股研究」· 右键出板块菜单">'
      + '<span class="evo-rank-dot" style="background:' + col + '"></span>'
      + '<span class="evo-rank-name">' + esc(x.name) + '</span>'
      + '<span class="evo-rank-val ' + clsUp(x.v) + '">' + fmtVal(x.v) + unitOf(st) + '</span></div>';
  }).join('') + '</div>';
}

/** 旧页 4263 `_evoIdxLegend`：左图下方图例 + 六指数叠加开关 */
function idxLegend(st) {
  const isQuote = st.mode === 'quote';
  const fb = isQuote && !idxOnList(st).length;
  // 哪个指数本段一个值都没有（例：指数代码在行情源无分钟线）——按钮上直接标「无数据」，
  // 不让人以为「点了没反应」。
  const snaps = evoSnaps(st);
  const empty = {};
  EVO_IDX.forEach((pair) => {
    empty[pair[0]] = !snaps.some((s) => idxVal(s, pair[0]) != null);
  });
  return '<div class="evo-lg">'
    + (isQuote ? '' : '<span class="evo-lg-i"><span class="evo-lg-d" style="background:var(--accent)"></span>活跃指数</span>')
    + EVO_IDX.map((pair, i) => {
      const on = !!st.idxOn[pair[0]];
      const nd = !!empty[pair[0]];
      return '<button class="evo-idx' + (on ? ' on' : '') + (nd ? ' nodata' : '')
        + '" style="--curve:' + EVO_IDX_PAL[i % EVO_IDX_PAL.length]
        + (nd ? ';opacity:.55;border-style:dashed' : '')
        + '" data-act="evo-idx" data-code="' + esc(pair[0]) + '" aria-pressed="' + on
        + '" title="' + (nd ? '本段无该指数分钟数据（行情源未提供）' : '单击' + (on ? '取消叠加' : '叠加到左图')) + '">'
        + esc(pair[1]) + (nd ? '<i style="font-style:normal;font-size:.62rem;margin-left:3px">无数据</i>' : '') + '</button>';
    }).join('')
    + (fb ? '<span class="evo-lg-tip muted">未勾选：默认画前两个，点上方按钮自选</span>' : '')
    + '</div>';
}

/** 实例可注入的「现在」（st.now / st.nowFn），缺省取真实时间 */
function nowOf(st) {
  if (st && typeof st.nowFn === 'function') return st.nowFn();
  if (st && st.now instanceof Date) return st.now;
  return new Date();
}

/** 轨迹日期是否为「今天以前」（历史交易日）：决定无数据话术，也提醒不要拿今天曲线硬填 */
function isHistoricalDay(st) {
  const d = (String((trackOf(st) || {}).date || '')).replace(/-/g, '').slice(0, 8);
  if (d.length !== 8) return false;
  const n = nowOf(st);
  const today = String(n.getFullYear())
    + String(n.getMonth() + 1).padStart(2, '0') + String(n.getDate()).padStart(2, '0');
  return d < today;
}


/** 旧页 4021 `_evoBfLabel`：回填按钮文案（状态位驱动，不再按 id 改 textContent） */
function bfLabel(st) {
  if (st.bfState === 'checking') return '检查中…';
  if (st.bfState === 'running') return '补录中…';
  if (st.bfState === 'ok') return '已是最新';
  if (st.bfState === 'err') return '检查失败';
  if (st.bfState.indexOf('done:') === 0) return '已补 ' + st.bfState.slice(5) + ' 点';
  return '补齐缺口';
}

/**
 * 2026-10-26 主人：「这些数据可以吸收叠加到活跃市值的图上」
 *   ① 全A 起点 −1.42% 末值 +5.08% 日内变化 +6.49pp 跑赢 27 / 跑输 63
 *   ② 09:30 → 15:00 ｜ 峰值 5.14% ｜ 谷值 −2.00%
 * 这两行原本在【板块轨迹明细】卡里（顶部全A 行 + spark 下方说明行）。那张卡整卡被删
 * （内容与日内演化右榜、板块轮动网格重复），两行数据搬到这里做左图的信息行。
 * 取数就用轨迹卡原来那一份（hooks.summary = /api/p/intraday/summary），没新增口径：
 *   first_all / last_all / all_delta / strong / weak 直接取字段；时段与峰谷由 all_series 算（与删之前
 *   trackSpark 同一算法：filter all!=null 后取 min/max 与首末 t，不补零）。
 */
function allInfoHtml(st) {
  const s = (st.hooks && st.hooks.summary) ? st.hooks.summary() : null;
  if (!s || !s.ok) return '';   // 没数据就不占位（不拿 0 冒充）
  const pts = (s.all_series || []).filter((x) => x && x.all != null);
  const vals = pts.map((x) => Number(x.all));
  const hi = vals.length ? Math.max.apply(null, vals) : null;
  const lo = vals.length ? Math.min.apply(null, vals) : null;
  const t0 = pts.length ? String(pts[0].t || '') : '';
  const t1 = pts.length ? String(pts[pts.length - 1].t || '') : '';
  const pp = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + num(v, 2) + 'pp');
  const kv = (k, v, cl) => '<span class="evo-ii"><i>' + k + '</i><b'
    + (cl ? ' class="' + cl + '"' : '') + '>' + v + '</b></span>';
  return '<div class="evo-info">'
    + kv('全A 起点', pct(s.first_all), clsUp(s.first_all))
    + kv('末值', pct(s.last_all), clsUp(s.last_all))
    + kv('日内变化', pp(s.all_delta), clsUp(s.all_delta))
    + kv('跑赢/跑输', num(s.strong, 0) + ' / ' + num(s.weak, 0), '')
    + kv('时段', esc(t0) + ' → ' + esc(t1), '')
    + kv('峰值', hi == null ? '—' : num(hi, 2) + '%', '')
    + kv('谷值', lo == null ? '—' : num(lo, 2) + '%', '')
    + '</div>';
}

// ---------------------------------------------------------------- 整块（旧页 4271 `_amvEvoHtml`）

export function html(st) {
  const track = trackOf(st);
  const snaps = evoSnaps(st);
  const staleWarn = (track && track.stale)
    ? '<div class="stale-warn">⚠ 日内演化轨迹断更：' + esc(track.stale_reason || '') + '（下图冻结在末点，非实时）</div>'
    : '';
  if (!snaps.length) return staleWarn + '<p class="muted">盘中 60s 采样后出现</p>';
  const last = snaps[snaps.length - 1];
  const amv = last.all;
  const amvTxt = amv == null ? '—' : '<b class="' + clsUp(amv) + '">' + pct(amv) + '</b>';
  const sel = pick(st, last);

  const bfBtn = '<button class="evo-bf" data-act="evo-bf" title="检查轨迹缺口；缺则用 1m K线重放补录'
    + '（后台约 6-8 分钟）">' + bfLabel(st) + '</button>';
  const seg = (act, items, cur) => '<span class="evo-seg">' + items.map((it) =>
    '<button class="' + (cur === it[0] ? 'on' : '') + '" data-act="' + act + '" data-v="' + it[0]
    + '" aria-pressed="' + (cur === it[0]) + '">' + it[1] + '</button>').join('') + '</span>';

  const modeSeg = seg('evo-mode', [['quote', '行情涨跌'], ['amv', 'AMV涨跌']], st.mode);
  const dirSeg = seg('evo-dir', [['pos', '正向'], ['neg', '负向'], ['all', '正负对比']], st.dir);
  const srtSeg = seg('evo-srt', [['contrib', '指数贡献'], ['amv', 'AMV涨跌']], st.srt);
  // 2026-10-27（N20261027-100741-29 等 4 条标注）：**模式隔离**——
  //   行情涨跌模式：标题「行情涨跌」，不显示 AMV 读数、不显示全A 信息行、不显示预测浮层；
  //   AMV 模式：标题「活跃指数」+ AMV 读数 + 全A 信息行 + 预测浮层。
  const isAmv = st.mode === 'amv';
  const idxOn = idxOnList(st);
  const hdTitle = isAmv
    ? '<h3 class="evo-h">' + MODE_TITLE.amv + ' <span class="evo-sub">' + amvTxt + '</span></h3>'
    : '<h3 class="evo-h">' + MODE_TITLE.quote + ' <span class="evo-sub">'
      + (idxOn.length ? idxOn.map((p) => p[1]).join(' / ') : '六指数待选') + '</span></h3>';

  return staleWarn
    + '\n    <div class="evo-split">'
    + '\n      <div class="evo-pane">'
    + '\n        <div class="evo-hd">'
    + '\n          ' + hdTitle
    + '\n          <span class="evo-ctl"><span class="evo-k">叠加模式</span>' + modeSeg + '</span>'
    + '\n        </div>'
    + '\n        ' + (isAmv ? amvChart(st) : quoteChart(st))
    + (isAmv ? '\n        ' + allInfoHtml(st) : '')
    + '\n        ' + idxLegend(st)
    + '\n        <div class="evo-card" hidden></div>'
    + '\n        <div class="src">' + (isAmv ? '真实存档 ' : '指数分钟线 ')
    + esc(evoTime(snaps)) + ' ｜ ' + snaps.length + ' 点 @ '
    + esc((last.ts || '').slice(11, 16)) + ' ' + bfBtn + '</div>'
    + '\n      </div>'
    + '\n      <div class="evo-pane">'
    + '\n        <div class="evo-hd">'
    + '\n          <h3 class="evo-h">指数贡献演变</h3>'
    + '\n          <span class="evo-ctl">' + dirSeg + '<span class="evo-k">排序依据</span>' + srtSeg + '</span>'
    + '\n        </div>'
    + '\n        <div class="evo-cw">' + contribChart(st) + rankHtml(st, sel) + '</div>'
    + '\n        <div class="src">基准 ' + esc((track && track.weights_date) || '—') + ' ｜ 单位 '
    + unitOf(st) + ' ｜ 光标移动时榜单切到该时点</div>'
    + '\n      </div>'
    + '\n    </div>';
}

// ---------------------------------------------------------------- 高亮 / 十字线 / 区间（旧页 3733-3876）

/** 旧页 3733 `_evoApplyHl`：sel ∪ hover = 亮，其余 dim */
function applyHl(st) {
  if (!st.body) return;
  const any = (st.sel != null) || (st.hover != null);
  st.body.querySelectorAll('.evo-line').forEach((p) => {
    const k = p.dataset.code;
    const on = (st.sel === k) || (st.hover === k);
    p.style.opacity = on ? '1' : (any ? '0.22' : '1');
    p.setAttribute('stroke-width', (st.sel === k) ? '2.6' : (on ? '1.9' : '1.3'));
  });
  st.body.querySelectorAll('.evo-rank-row').forEach((r) => {
    const k = r.dataset.code;
    const on = (st.sel === k) || (st.hover === k);
    r.classList.toggle('sel', st.sel === k);
    r.classList.toggle('hi', on && st.sel !== k);
    r.style.opacity = (any && !on) ? '0.45' : '1';
  });
}

/** 旧页 3741 `_evoDrawCross`：两图同索引各画一条竖线 */
function drawCross(st, svg, gidx) {
  const g = svg.querySelector('.evo-cross');
  if (!g) return;
  const idx = toLocal(svg, gidx);
  if (idx == null || idx < 0 || idx > (+svg.dataset.n - 1)) {
    g.setAttribute('display', 'none'); g.innerHTML = ''; return;
  }
  const N = +svg.dataset.n;
  if (!(N > 0)) return;
  const px = pxOf(st, svg, gidx);
  const H = svg.viewBox.baseVal.height;
  g.innerHTML = '<line x1="' + px.toFixed(1) + '" y1="0" x2="' + px.toFixed(1) + '" y2="' + H + '" class="evo-cross-v"/>';
  g.setAttribute('display', 'inline');
}

/** 旧页 3753 `_evoDrawRange`：两图同步画区间带 + 两端竖线 */
function drawRange(st, svg, ga, gb) {
  const g = svg.querySelector('.evo-range');
  if (!g) return;
  const N = +svg.dataset.n; const ML = +svg.dataset.ml; const MR = +svg.dataset.mr; const W = +svg.dataset.w;
  const a = toLocal(svg, ga); const b = toLocal(svg, gb);
  const a2 = Math.max(0, Math.min(N - 1, a == null ? 0 : a));
  const b2 = Math.max(0, Math.min(N - 1, b == null ? a2 : b));
  if (ga == null || !(N > 0) || (a < 0 && b < 0) || (a > N - 1 && b > N - 1)) {
    g.setAttribute('display', 'none'); g.innerHTML = ''; return;
  }
  const X = (i) => pxOf(st, svg, toGlobal(svg, i));
  const H = svg.viewBox.baseVal.height;
  const x1 = Math.min(X(a2), X(b2)); const x2 = Math.max(X(a2), X(b2));
  g.innerHTML = '<rect class="evo-range-band" x="' + x1.toFixed(1) + '" y="0" width="'
    + Math.max(1, x2 - x1).toFixed(1) + '" height="' + H + '"/>'
    + '<line class="evo-range-edge" x1="' + x1.toFixed(1) + '" y1="0" x2="' + x1.toFixed(1) + '" y2="' + H + '"/>'
    + '<line class="evo-range-edge" x1="' + x2.toFixed(1) + '" y1="0" x2="' + x2.toFixed(1) + '" y2="' + H + '"/>';
  g.setAttribute('display', 'inline');
}

/** 旧页 3776 `_evoApplyRange`：把当前区间套到所有图上 */
function applyRange(st) {
  if (!st.body) return;
  const r = st.range;
  chartSvgs(st).forEach((svg) => {
    if (!r) drawRange(st, svg, null, null);
    else drawRange(st, svg, r.a, r.b);
  });
}

/** 旧页 3783 `_evoCursorRows`：光标卡的行（活跃指数 + 已叠加指数） */
function cursorRows(st, snap) {
  // 2026-10-28：全A 在两种模式下都是主色主线（AMV = 活跃指数，行情 = 全A 基准），光标卡都该有它
  const rows = [{ k: baseLabel(st), c: 'var(--accent)', v: (snap.all == null ? null : snap.all) }];
  drawnIdx(st).forEach((pair) => {
    const i = EVO_IDX.findIndex((x) => x[0] === pair[0]);
    rows.push({ k: pair[1], c: EVO_IDX_PAL[i % EVO_IDX_PAL.length], v: idxVal(snap, pair[0]) });
  });
  return rows;
}

/** 左图主色线的叫法：AMV 模式下它就是活跃指数（全A 活跃市值），行情模式下当基准用 */
function baseLabel(st) {
  return st.mode === 'amv' ? '活跃指数' : '全A（基准）';
}

/** 旧页 3856 `_evoRangeRows`：区间涨幅 = 末值 − 首值（相对昨收的 %，相减即区间涨跌） */
function rangeRows(st, s0, s1) {
  const rows = [];
  if (s0.all != null && s1.all != null) {
    rows.push({ k: baseLabel(st), c: 'var(--accent)', v: s1.all - s0.all });
  }
  drawnIdx(st).forEach((pair) => {
    const i = EVO_IDX.findIndex((x) => x[0] === pair[0]);
    const v0 = idxVal(s0, pair[0]); const v1 = idxVal(s1, pair[0]);
    rows.push({ k: pair[1], c: EVO_IDX_PAL[i % EVO_IDX_PAL.length], v: (v0 == null || v1 == null) ? null : v1 - v0 });
  });
  return rows;
}

/** 旧页 3808 `_evoPlaceCard`：卡片夹在 pane 内（旧页 2026-10-18 修过「鼠标在 pane 上方时 y 成负数→卡片飞出去」） */
function placeCard(card, pane, ev) {
  const pr = pane.getBoundingClientRect();
  const cw = card.offsetWidth || 150; const ch = card.offsetHeight || 120;
  let x = (ev ? ev.clientX : pr.left + 40) - pr.left + 14;
  let y = (ev ? ev.clientY : pr.top + 20) - pr.top + 12;
  x = Math.max(4, Math.min(x, Math.max(4, pr.width - cw - 4)));
  y = Math.max(4, Math.min(y, Math.max(4, pr.height - ch - 4)));
  card.style.left = x.toFixed(0) + 'px';
  card.style.top = y.toFixed(0) + 'px';
}

/** 旧页 3802 `_evoCardUpdate`：光标卡 / 区间卡的三种收起条件与两种位置（左上角固定 vs 跟鼠标） */
function cardUpdate(st, idx, ev) {
  const card = evoCardEl(st);
  const pane = paneLEl(st);
  if (!card || !pane) return;
  const snaps = evoSnaps(st);
  if (!snaps.length) { card.hidden = true; return; }
  const inRange = !!(st.range && st.range.a != null && st.range.b != null);
  if (idx == null && !inRange) { card.hidden = true; card.innerHTML = ''; return; }
  if (inRange) {
    const a = Math.min(st.range.a, st.range.b); const b = Math.max(st.range.a, st.range.b);
    const s0 = snaps[a] || {}; const s1 = snaps[b] || {};
    const rows = rangeRows(st, s0, s1);
    card.innerHTML = '<div class="evo-card-t">' + esc(String(s0.ts || '').slice(11, 16)) + ' — '
      + esc(String(s1.ts || '').slice(11, 16))
      + '<span class="evo-card-x" data-act="evo-clear" title="恢复全区间">×</span></div>'
      + rows.map((r) => '<div class="evo-card-r"><span class="evo-card-k" style="color:' + r.c + '">'
        + esc(r.k) + '：</span><span class="evo-card-l">区间涨幅</span>'
        + '<span class="evo-card-v ' + clsUp(r.v) + '">'
        + (r.v == null ? '—' : (r.v > 0 ? '+' : '') + num(r.v, 2) + '%') + '</span></div>').join('');
    card.hidden = false;
    // 区间卡片固定在左上角，不跟鼠标（区间是「已选定的一段」，跟鼠标会误以为还在拖）
    card.style.left = '8px';
    card.style.top = '8px';
    return;
  }
  if (idx == null || !snaps.length) { card.hidden = true; return; }
  const s = snaps[idx] || {};
  const rows = cursorRows(st, s);
  if (!rows.length) { card.hidden = true; return; }
  card.innerHTML = '<div class="evo-card-t">' + (esc(String(s.ts || '').slice(11, 19) || '—')) + '</div>'
    + rows.map((r) => '<div class="evo-card-r"><span class="evo-card-k" style="color:' + r.c + '">'
      + esc(r.k) + '：</span><span class="evo-card-l">区间涨跌</span>'
      + '<span class="evo-card-v ' + clsUp(r.v) + '">'
      + (r.v == null ? '—' : (r.v > 0 ? '+' : '') + num(r.v, 2) + '%') + '</span></div>').join('');
  card.hidden = false;
  placeCard(card, pane, ev);
}

/** 旧页 3838 `_evoRankSync`：右榜数值跟着光标时点走（区间模式下改为区间涨幅） */
function rankSync(st, idx) {
  if (!st.body) return;
  const snaps = evoSnaps(st);
  if (!snaps.length) return;
  const inRange = !!(st.range && st.range.a != null && st.range.b != null);
  const s = (idx == null) ? snaps[snaps.length - 1] : (snaps[idx] || {});
  const a = inRange ? Math.min(st.range.a, st.range.b) : null;
  const s0 = inRange ? (snaps[a] || {}) : null;
  st.body.querySelectorAll('.evo-rank-row').forEach((r) => {
    const el = r.querySelector('.evo-rank-val');
    let v;
    if (inRange) {
      const v0 = rankVal(st, r.dataset.code, s0);
      const v1 = rankVal(st, r.dataset.code, s);
      v = (v0 == null || v1 == null) ? null : v1 - v0;
    } else v = rankVal(st, r.dataset.code, s);
    if (el) { el.textContent = (v == null ? '—' : fmtVal(v) + unitOf(st)); el.className = 'evo-rank-val ' + clsUp(v); }
  });
}

/** 旧页 3870 `_evoApplyCursor`：两图画线 + 光标卡 + 右榜跟随 */
function applyCursor(st, idx, ev) {
  st.cursor = idx;
  if (!st.body) return;
  chartSvgs(st).forEach((svg) => drawCross(st, svg, idx));
  cardUpdate(st, idx, ev);
  rankSync(st, idx);
}


// ---------------------------------------------------------------- 重绘（旧页 3690 `_evoPaint`）

/** 旧页 3690：清光标 → 重填 → 校验悬停 → 重套高亮 → 对齐预测浮层 */
export function paint(st) {
  if (!st || !st.body) return;
  st.cursor = null;
  st.body.innerHTML = html(st);
  if (st.hover) {
    const t = (st.el && st.el.ownerDocument) ? st.el.ownerDocument.elementFromPoint(st.mouse.x, st.mouse.y) : null;
    if (hitCode(st, t) !== st.hover) st.hover = null;
  }
  applyHl(st);
  fitH();          // 旧页 4090 空函数，调用点不删
}

/** 旧页 4090 `_evoFitH`：**空函数，原样保留**（旧页 2026-10-18 修过「量高写回→正反馈越算越大」，调用点保留） */
function fitH() { /* 故意为空：调用点在 paint 里，别删 */ }

/** 旧页 3701 `_evoHitCode`：从事件节点反查板块码（限定在 .evo-body 内） */
function hitCode(st, t) {
  if (!t || !t.closest) return null;
  if (!st.body || !st.body.contains(t)) return null;
  const n = t.closest('[data-code]');
  return (n && n.dataset.code) ? n.dataset.code : null;
}

/** 旧页 3695 `_evoRe` */
export const repaint = paint;

// ---------------------------------------------------------------- 回填（旧页 4021-4060）

function bfMark(st, s, ms) {
  st.bfState = s;
  paint(st);
  if (ms) {
    const id = setTimeout(() => {
      if (st.bfState === s) { st.bfState = ''; paint(st); }
    }, ms);
    st.bfTimers.push(id);
  }
}

async function reloadTrack(st) {
  if (st.hooks && st.hooks.reloadTrack) await st.hooks.reloadTrack();
  paint(st);
}

export async function backfill(st) {
  if (st.bfState === 'checking' || st.bfState === 'running') return;
  bfMark(st, 'checking');
  let r = null;
  try { r = await st.hooks.api.post('/api/sector_track_backfill'); } catch (e) { r = null; }
  if (!r) { bfMark(st, 'err', 4000); return; }
  if (r.status === 'ok') { bfMark(st, 'ok', 2500); await reloadTrack(st); return; }
  if (r.status === 'started' || r.status === 'busy') {
    st.bfState = 'running';
    paint(st);
    bfPoll(st);
    return;
  }
  bfMark(st, 'err', 4000);
}

function bfPoll(st) {
  if (st.bfTimer) clearInterval(st.bfTimer);
  st.bfTimer = setInterval(async () => {
    let s = null;
    try { s = await st.hooks.api.get('/api/sector_track_backfill_status'); } catch (e) { return; }
    const sf = (s && s.status_file) || {};
    if (sf.status === 'done') {
      clearInterval(st.bfTimer); st.bfTimer = null;
      bfMark(st, 'done:' + (sf.filled || 0), 4000);
      await reloadTrack(st);
    } else if (sf.status === 'error') {
      clearInterval(st.bfTimer); st.bfTimer = null;
      bfMark(st, 'err', 6000);
    }
  }, 6000);
}

// ---------------------------------------------------------------- 切视图 / 钉住（旧页 3696-3710）

export function setMode(st, m) { VIEW.mode = m; st.mode = m; st.hover = null; paint(st); }
export function setDir(st, d) { VIEW.dir = d; st.dir = d; st.sel = null; st.hover = null; paint(st); }
export function setSrt(st, s) { VIEW.srt = s; st.srt = s; st.sel = null; st.hover = null; paint(st); }
export function toggleIdx(st, c) {
  st.idxOn[c] = !st.idxOn[c];
  VIEW.idxOn = Object.assign({}, st.idxOn);
  paint(st);
}
export function togglePin(st, code) {
  if (!code) return;
  st.sel = (st.sel === code) ? null : code;
  paint(st);
}

/**
 * 2026-10-26 主人：「点击板块时，不再默认打开板块详情，而是跳到下方显示，
 * 同时板块排名内对应板块出现选中效果」（箭头指向右侧板块榜 → 研究卡左栏排名）
 *
 * 于是：右榜行点击 → ① 本卡里把这板块钉住高亮；② 交给前端 hooks.pickBoard → 研究卡选中它 + 滚到那儿。
 * 不再带 data-open-amv，宿主不会弹详情卡。
 */
export function gotoBoard(st, code) {
  if (!code) return;
  if (st.sel !== code) { st.sel = code; paint(st); }
  const fn = st.hooks && st.hooks.pickBoard;
  if (typeof fn === 'function') fn(code);
}

/** 旧页 3814 `evoClearRange`：清区间 + 收起卡片（2026-10-18 修「卡片一直跟着鼠标不消失」） */
export function clearRange(st) {
  st.range = null; st.rangeDrag = false; st.rangeBase = null;
  const card = evoCardEl(st);
  if (card) { card.hidden = true; card.innerHTML = ''; }
  st.cursor = null;
  applyRange(st);
  paint(st);
}

// ---------------------------------------------------------------- 事件委托（旧页 3886 `_evoBind`）

export function bind(st) {
  const host = st.el;
  const ac = st.ac;
  const opt = { signal: ac.signal };
  const inEvo = (t) => !!(t && st.body && st.body.contains(t));

  // ---- 光标：任一张图上移动 → 取该图采样点索引 → 两图一起画线
  host.addEventListener('mousemove', (ev) => {
    if (!inEvo(ev.target)) return;
    st.mouse = { x: ev.clientX, y: ev.clientY };
    const svg = ev.target.closest && ev.target.closest('svg.st-chart[data-evo]');
    if (!svg) {
      if (st.cursor != null) applyCursor(st, null, ev);
      return;
    }
    const box = svg.getBoundingClientRect();
    if (!box.width) return;
    const ML = +svg.dataset.ml; const MR = +svg.dataset.mr; const W = +svg.dataset.w;
    const vx = (ev.clientX - box.left) * (W / box.width);
    const plot = W - ML - MR;
    if (vx < ML - 2 || vx > ML + plot + 2) {
      if (st.cursor != null) applyCursor(st, null, ev);
      return;
    }
    // 固定时间轴：不再自己算索引，统一走 idxAt（时间反算），与曲线映射同源
    const idx = idxAt(st, svg, ev.clientX);
    if (idx !== st.cursor) applyCursor(st, idx, ev);
    else cardUpdate(st, idx, ev);   // 索引没变也要跟手（卡片位置）
  }, opt);

  host.addEventListener('mouseover', (ev) => {
    if (!inEvo(ev.target)) return;
    const k = hitCode(st, ev.target);
    if (k === st.hover) return;
    st.hover = k || null;
    applyHl(st);
  }, opt);

  host.addEventListener('mouseout', (ev) => {
    if (!inEvo(ev.target)) return;
    const k = hitCode(st, ev.target);
    if (!k) return;
    if (hitCode(st, ev.relatedTarget) === k) return;
    if (st.hover === k) { st.hover = null; applyHl(st); }
  }, opt);

  host.addEventListener('mouseleave', () => {
    if (st.hover) { st.hover = null; applyHl(st); }
    if (st.cursor != null) applyCursor(st, null, null);
    const card = evoCardEl(st);
    if (card && !(st.range && st.range.a != null && st.range.b != null)) { card.hidden = true; card.innerHTML = ''; }
  }, opt);

  window.addEventListener('blur', () => {
    if (st.hover) { st.hover = null; applyHl(st); }
  }, { passive: true, signal: ac.signal });

  // ---- 点击：钉住曲线（旧页 `.evo-hit`）+ 工具条/图例/回填/清区间（内联 onclick 改 data-act）
  host.addEventListener('click', (ev) => {
    if (!inEvo(ev.target)) return;
    const act = ev.target.closest && ev.target.closest('[data-act]');
    if (act) {
      const a = act.dataset.act;
      if (a === 'evo-mode') { setMode(st, act.dataset.v); return; }
      if (a === 'evo-dir') { setDir(st, act.dataset.v); return; }
      if (a === 'evo-srt') { setSrt(st, act.dataset.v); return; }
      if (a === 'evo-idx') { toggleIdx(st, act.dataset.code); return; }
      if (a === 'evo-bf') { backfill(st); return; }
      if (a === 'evo-clear') { clearRange(st); return; }
      // 2026-10-26 主人：「点击板块时，不再默认打开板块详情，而是跳到下方显示，
      // 同时板块排名内对应板块出现选中效果」
      if (a === 'evo-goto') { gotoBoard(st, act.dataset.goto || act.dataset.code); return; }
    }
    const hit = ev.target.closest && ev.target.closest('.evo-hit');
    if (hit && hit.dataset.code) togglePin(st, hit.dataset.code);
  }, opt);

  // ---- 右键拖拽量区间（2026-10-18 主人令：右键可以看区间涨幅）
  host.addEventListener('contextmenu', (ev) => {
    if (!inEvo(ev.target)) return;
    const svg = ev.target.closest && ev.target.closest('svg.st-chart[data-evo]');
    if (svg) ev.preventDefault();
  }, opt);

  host.addEventListener('mousedown', (ev) => {
    if (!inEvo(ev.target)) return;
    if (ev.button === 2) {                      // 右键：开始量区间
      const svg = ev.target.closest && ev.target.closest('svg.st-chart[data-evo]');
      if (!svg) return;
      ev.preventDefault();
      const i = idxAt(st, svg, ev.clientX);
      if (i == null) return;
      st.rangeDrag = true; st.range = { a: i, b: i };
      applyRange(st); cardUpdate(st, null, ev);
      return;
    }
    // 2026-10-26：Shift 平移已取消（固定时间轴，不允许改缩放比），旧页那个 data-n 混用的 bug 一并没了
  }, opt);

  host.addEventListener('mouseup', (ev) => {
    if (ev.button === 2 && st.rangeDrag) {
      st.rangeDrag = false;
      if (st.range && st.range.a != null && st.range.b != null) {
        const snaps = evoSnaps(st);
        const a = Math.min(st.range.a, st.range.b); const b = Math.max(st.range.a, st.range.b);
        if (b - a < 1) { st.range = null; applyRange(st); paint(st); return; }   // 单击（未拉开）→ 不算区间
        // 2026-10-26 主人「固定时间轴」：松手**不再把窗口收窄**（收窄 = 又拉伸了一回，
        // 与「固定」互斥）。区间改为纯量具：带子 + 区间卡留在屏上，Esc / × 清。
      }
      return;
    }
    if (st.drag) st.drag = null;
  }, opt);

  // ---- 拖拽中：右键拉区间
  host.addEventListener('mousemove', (ev) => {
    if (!inEvo(ev.target) && !st.rangeDrag) return;
    if (st.rangeDrag && st.range) {
      const svg = (ev.target.closest && ev.target.closest('svg.st-chart[data-evo]'))
        || (st.body && st.body.querySelector('svg.st-chart[data-evo]'));
      if (!svg) return;
      const i = idxAt(st, svg, ev.clientX);
      if (i == null) return;
      st.range.b = i; applyRange(st); cardUpdate(st, null, ev);
    }
  }, opt);

  // ---- 滚轮缩放：2026-10-26 主人「这两张图表的滚轮缩放功能取消。固定时间轴，盘中不再拉伸K线」
  // 监听器不绑了（旧页的 90ms 节流 wheelLock 也一并停用）。轴固定，就不存在缩放比可改。

  // ---- Esc 清除区间
  window.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && st.range) clearRange(st);
  }, opt);
}

export function unbind(st) {
  if (!st) return;
  st.alive = false;
  if (st.bfTimer) { clearInterval(st.bfTimer); st.bfTimer = null; }
  (st.bfTimers || []).forEach((id) => clearTimeout(id));
  st.bfTimers = [];
  try { st.ac.abort(); } catch (e) { /* 已 abort：忽略 */ }
}

/**
 * 函数对照（旧页 → 本模块）
 *
 * 3690 _evoPaint        → paint           3695 _evoRe           → repaint
 * 3696 evoSet           → setDir          3697 evoSetMode       → setMode
 * 3698 evoSetSrt        → setSrt          3699 evoToggleIdx     → toggleIdx
 * 3700 _evoTogglePin    → togglePin       3701 _evoHitCode      → hitCode
 * 3704 _evoIdxAt        → idxAt           3712 _evoView         → viewOf
 * 3733 _evoApplyHl      → applyHl         3741 _evoDrawCross    → drawCross
 * 3753 _evoDrawRange    → drawRange       3776 _evoApplyRange   → applyRange
 * 3783 _evoCursorRows   → cursorRows      3788 _evoCardUpdate   → cardUpdate
 * 3802 _evoPlaceCard    → placeCard       3814 evoClearRange    → clearRange
 * 3838 _evoRankSync     → rankSync        3856 _evoRangeRows    → rangeRows
 * 3870 _evoApplyCursor  → applyCursor     3878 _evoToGlobal     → toGlobal
 * 3882 _evoToLocal      → toLocal         3886 _evoBind         → bind
 * 4021 _evoBfLabel      → bfLabel         4032 _evoBfMark       → bfMark
 * 4038 evoBackfill      → backfill        4057 _evoBfPoll       → bfPoll
 * 4060 _evoBfReload     → reloadTrack     4084 _evoFitH         → fitH（空函数）
 * 4085 _evoSnaps        → evoSnaps        4086 _evoTime         → evoTime
 * 4102 _evoXl           → evoXl           4104 _EVO_PAL         → EVO_PAL
 * 4065 _EVO_IDX         → EVO_IDX         4068 _EVO_IDX_PAL     → EVO_IDX_PAL
 * 4071 _evoLastOf       → caliber.lastOf  4072 _evoIdxVal       → idxVal
 * 4074 _evoRankVal      → rankVal         4076 _evoUnit         → unitOf
 * 4077 _evoFmt          → fmtVal          4078 _evoPick         → pick
 * 4111 _evoAmvChart     → amvChart        4183 _evoQuoteChart   → quoteChart
 * 4224 _evoContribChart → contribChart    4252 _evoRankHtml     → rankHtml
 * 4263 _evoIdxLegend    → idxLegend       4271 _amvEvoHtml      → html
 * 4211 _evoShare        → caliber.shareOf 4218 _evoContrib      → caliber.contribOf
 * 4066 _gapBreak        → caliber.gapBreak 4073 _lineD          → caliber.lineD
 */
