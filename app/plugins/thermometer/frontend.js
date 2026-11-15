// ============================================================================
// 情绪温度计 · 前端（2026-10-29，projects/panel-v2）
// ----------------------------------------------------------------------------
// 一屏看完：从读数到动作。八段固定顺序：
//   ① 四格 KPI（每格带前一日对比）  ② 温度计刻度条  ③ 今日盘面指标格（原始值 + 分位）
//   ④ 今日 vs 昨日对比表            ⑤ 近 60 交易日趋势图（三线，含恐慌口径断点）
//   ⑥ 预警区                        ⑦ 现在该做什么  ⑧ 信号→动作表
//
// **本卡不产数**：两个读数各有 owner，本卡只取、只画。
//   · 情绪指数 / 六因子 / 盘面原始值 / stage → GET /api/p/emotion/payload（emotion 插件，口径 v1.1）
//   · 恐慌广度 / 四项 / 成交额 / 量能        → GET /api/panic（core panic_calc → 旧仓 panic_breadth）
//   · 恐慌口径断点 / 序列库行数              → GET /api/p/thermometer/meta（本插件的 backend）
// 前端不重算情绪分、不重算恐慌广度、不改任何一侧口径；缺数据一律显示「缺」，不填 0。
//
// 两处口径判断（页面上都要写明，避免「同一张图两种算法」）：
//   1. **恐慌广度方向**：旧仓 `panic_score()` 是「低值 = 恐慌」（10 极冰 / 90 过热），
//      与情绪指数**同向**（越高越热），不是反向对冲。图例逐条写清，不让人读反。
//   2. **恐慌口径断点**：2026-10-29 起四项含量能，此前 1264 行是三项。图上竖线标注，
//      断点两侧分段（历史虚线 / 今日实心点），**不连线跨断点比较**。
//
// 分位口径（页面格子里的小字）：情绪侧 = 60 日窗口含当日、（小于当日 + 0.5×等于）/ 样本数，
// 样本 <5 显示「缺」——与 `emotion/engine.pct_rank` 同式（本卡只是在展示层排序，
// 不参与任何打分）；恐慌侧直接用 /api/panic 给的分位（250 日窗口）。两者分母不同，格子内标注。
//
// 契约（docs/CONTRACT.md §5）：原生 ES module、零 import、零构建、只操作自己的 root。
// 日频页面，不声明 refreshEvery；顶栏「刷新」按钮手动重取。
// ============================================================================

const STYLE_ID = 'thm-card-css';

// 五档（与 emotion/engine.BANDS 同值，仅用于刻度条分段与配色，不参与打分）
const BANDS = [[70, '亢奋', 'hot'], [55, '偏热', 'warm'], [40, '中性', 'mid'], [25, '偏冷', 'cold'], [0, '冰点', 'ice']];
const BAND_TEXT = { 亢奋: '亢奋', 偏热: '偏热', 中性: '中性', 偏冷: '偏冷', 冰点: '冰点' };

// 分母：情绪侧 60 日滚动 / 恐慌侧 250 日滚动（各自 owner 的既有口径，页面不改）
const EMOTION_WINDOW = 60;
const PANIC_WINDOW = 250;
const PCT_MIN_SAMPLE = 5;

// 断点常量（与 backend.CALIBER_BREAK 同值；/meta 取不到时兜底，页面标注来源）
const BREAK_FALLBACK = '20261029';

const CSS = [
  '.thm-root{display:flex;flex-direction:column;gap:3px;min-width:0;color:var(--fg);font-size:0.74rem}',
  '.thm-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:0.7rem;color:var(--dim);min-height:22px}',
  '.thm-bar b{color:var(--fg-strong);font-weight:600}',
  '.thm-grow{margin-left:auto}',
  '.thm-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.7rem;font-family:inherit;cursor:pointer}',
  '.thm-btn:hover{background:var(--card2);border-color:var(--accent);color:var(--accent)}',
  '.thm-warn-c{color:var(--warn)}',
  '.thm-sec{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card);min-width:0}',
  '.thm-sec-h{display:flex;align-items:center;gap:8px;padding:4px 9px;border-bottom:1px solid var(--line-soft);font-size:0.66rem;color:var(--dim)}',
  '.thm-sec-h b{color:var(--fg-strong);font-weight:600;font-size:0.72rem}',
  '.thm-sec-h .thm-hint{margin-left:auto;font-variant-numeric:tabular-nums}',
  // ① KPI 四格
  '.thm-kpi{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}',
  '.thm-k{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card);padding:3px 9px 4px;min-width:0}',
  '.thm-k-t{font-size:0.66rem;color:var(--dim);display:flex;align-items:center;gap:6px}',
  '.thm-k-t .thm-src{margin-left:auto;font-size:0.6rem;color:var(--dim);opacity:0.75}',
  '.thm-k-v{display:flex;align-items:baseline;gap:6px;margin-top:1px}',
  '.thm-k-v b{font-size:1.32rem;line-height:1.15;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-0.5px}',
  '.thm-k-v span{font-size:0.68rem;color:var(--dim)}',
  '.thm-k-d{font-size:0.66rem;color:var(--dim);margin-top:2px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.thm-k-d b{font-weight:600}',
  '.thm-up{color:var(--up)}',
  '.thm-down{color:var(--down)}',
  // ② 温度计
  '.thm-thermo{padding:4px 10px 2px}',
  '.thm-scale{position:relative;height:14px;border-radius:999px;overflow:visible;border:var(--border-w) var(--border-style) var(--border-color);background:linear-gradient(90deg,rgba(70,140,200,0.55) 0%,rgba(70,140,200,0.30) 22%,rgba(120,120,120,0.22) 42%,rgba(220,150,60,0.32) 58%,rgba(220,80,80,0.55) 100%)}',
  '.thm-tick{position:absolute;top:0;bottom:0;width:1px;background:var(--bg);opacity:0.55}',
  '.thm-ptr{position:absolute;top:-5px;bottom:-5px;width:2px;background:var(--fg-strong);box-shadow:0 0 0 1px var(--bg)}',
  '.thm-ptr::after{content:"";position:absolute;left:-4px;top:-1px;width:10px;height:10px;border-radius:50%;background:var(--fg-strong)}',
  '.thm-ptr-v{position:absolute;top:-18px;transform:translateX(-50%);font-size:0.68rem;font-weight:700;font-variant-numeric:tabular-nums;color:var(--fg-strong);white-space:nowrap}',
  '.thm-lab{display:flex;justify-content:space-between;font-size:0.62rem;color:var(--dim);margin-top:2px;font-variant-numeric:tabular-nums}',
  '.thm-thermo-foot{display:flex;align-items:baseline;gap:10px;margin-top:2px;flex-wrap:nowrap;overflow:hidden}',
  '.thm-band{flex:0 0 auto;font-size:0.8rem;font-weight:700;padding:0 8px;border-radius:999px;border:var(--border-w) var(--border-style) currentColor}',
  '.thm-band.hot{color:var(--up)}',
  '.thm-band.warm{color:var(--warn)}',
  '.thm-band.mid{color:var(--fg)}',
  '.thm-band.cold{color:var(--dim)}',
  '.thm-band.ice{color:var(--accent)}',
  '.thm-say{font-size:0.7rem;color:var(--fg);min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.thm-say b{color:var(--fg-strong)}',
  // ③ 盘面格
  '.thm-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:4px;padding:4px 9px 5px}',
  '.thm-c{border:var(--border-w) var(--border-style) var(--line-soft);border-radius:var(--radius-sm);padding:2px 6px;min-width:0;background:var(--card2)}',
  '.thm-c-t{font-size:0.6rem;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.thm-c-row{display:flex;align-items:baseline;gap:5px;min-width:0}',
  '.thm-c-v{font-size:0.86rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.15;white-space:nowrap}',
  '.thm-c-v small{font-size:0.62rem;font-weight:400;color:var(--dim);margin-left:2px}',
  '.thm-c-p{font-size:0.6rem;color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:auto}',
  '.thm-c-p .thm-nodata{color:var(--warn)}',
  // ④ 对比表
  '.thm-tb{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}',
  '.thm-tb th{font-size:0.6rem;color:var(--dim);font-weight:400;text-align:right;padding:1px 7px;border-bottom:1px solid var(--line-soft)}',
  '.thm-tb th:first-child{text-align:left}',
  '.thm-tb td{font-size:0.63rem;line-height:1.05;padding:0 7px;text-align:right;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.thm-tb td:first-child{text-align:left;color:var(--fg-strong)}',
  '.thm-tb tr:last-child td{border-bottom:0}',
  '.thm-cmpgrid{display:none}',
  // 四维拆解（广度 / 接力 / 资金 / 位置）
  '.thm-dims{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px;padding:2px 10px 2px}',
  '.thm-dim{border:var(--border-w) var(--border-style) var(--line-soft);border-radius:var(--radius-sm);padding:3px 8px 3px;background:var(--card2);min-width:0}',
  '.thm-dim-h{display:flex;align-items:baseline;gap:5px;font-size:0.64rem;color:var(--dim)}',
  '.thm-dim-h b{color:var(--fg-strong);font-weight:600;font-size:0.72rem}',
  '.thm-dim-h span{margin-left:auto;font-variant-numeric:tabular-nums}',
  '.thm-dim-v{display:flex;align-items:baseline;gap:6px;margin-top:1px}',
  '.thm-dim-v b{font-size:1.05rem;line-height:1.05;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-0.5px}',
  '.thm-dim-band{font-size:0.64rem;font-weight:600;padding:0 6px;border-radius:999px;border:var(--border-w) var(--border-style) currentColor}',
  '.thm-dim-band.cold{color:var(--accent)}',
  '.thm-dim-band.cool{color:var(--dim)}',
  '.thm-dim-band.mid{color:var(--fg)}',
  '.thm-dim-band.warm{color:var(--warn)}',
  '.thm-dim-band.hot{color:var(--up)}',
  '.thm-dim-bar{position:relative;height:4px;margin-top:3px;border-radius:999px;background:var(--line-soft);overflow:hidden}',
  '.thm-dim-fill{position:absolute;left:0;top:0;bottom:0;border-radius:999px}',
  '.thm-dim-n{font-size:0.58rem;color:var(--dim);margin-top:1px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  // 四维子项条：子项原始值 + 分位并列（资金四项方向相反时一眼看出分歧）
  '.thm-dim-items{display:grid;grid-template-columns:1fr 1fr;gap:0 7px;margin-top:1px;font-size:0.55rem;line-height:1.25;color:var(--dim)}',
  '.thm-dim-it{display:flex;gap:3px;align-items:baseline;white-space:nowrap;overflow:hidden;min-width:0}',
  '.thm-dim-it s{text-decoration:none;overflow:hidden;text-overflow:ellipsis;color:var(--dim)}',
  '.thm-dim-it i{font-style:normal;font-weight:700;color:var(--fg);font-variant-numeric:tabular-nums}',
  '.thm-dim-it em{font-style:normal;font-variant-numeric:tabular-nums;opacity:0.72}',
  '.thm-dim-it.miss i{color:var(--dim);font-weight:400}',
  '.thm-miss{color:var(--warn)}',
  // ⑤ 图
  '.thm-chart-box{padding:4px 10px 6px}',
  '.thm-chart{width:100%;display:block}',
  '.thm-legend{display:flex;flex-wrap:nowrap;overflow:hidden;gap:0 12px;padding:1px 10px 3px;font-size:0.62rem;color:var(--dim);white-space:nowrap}',
  '.thm-legend i{display:inline-block;width:14px;height:0;border-top-width:2px;border-top-style:solid;margin-right:4px;vertical-align:middle}',
  '.thm-legend .thm-lg-b{display:inline-block;width:8px;height:8px;border-radius:1px;margin-right:4px;vertical-align:middle}',
  '.thm-legend b{color:var(--fg);font-weight:600}',
  '.thm-cap{font-size:0.62rem;color:var(--dim);padding:0 10px 4px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  // ⑥ 预警
  '.thm-alerts{display:flex;flex-direction:column;gap:0;padding:3px 10px 4px}',
  '.thm-a{display:flex;gap:5px;font-size:0.62rem;line-height:1.32;align-items:flex-start}',
  '.thm-a em{flex:0 0 auto;font-style:normal;font-size:0.58rem;padding:0 4px;border-radius:3px;border:var(--border-w) var(--border-style) currentColor;line-height:1.45;margin-top:1px}',
  '.thm-a.alert em{color:var(--danger)}',
  '.thm-a.warn em{color:var(--warn)}',
  '.thm-a.info em{color:var(--dim)}',
  '.thm-a span{min-width:0;color:var(--fg)}',
  '.thm-a span b{color:var(--fg-strong);font-weight:600}',
  // ⑦ 决定
  '.thm-do{display:flex;gap:10px;padding:7px 10px 4px;align-items:center;flex-wrap:wrap}',
  '.thm-do-v{font-size:1.24rem;font-weight:800;letter-spacing:1px;padding:2px 11px;border-radius:var(--radius-sm);border:var(--border-w) var(--border-style) currentColor}',
  '.thm-do-v.hold{color:var(--danger)}',
  '.thm-do-v.struct{color:var(--accent)}',
  '.thm-do-v.wait{color:var(--warn)}',
  '.thm-do-why{flex:1 1 200px;min-width:0;font-size:0.7rem;color:var(--dim);line-height:1.6}',
  '.thm-do-why b{color:var(--fg-strong)}',
  '.thm-rules{font-size:0.6rem;color:var(--dim);padding:0 9px 5px;line-height:1.4}',
  // ⑧ 信号表
  '.thm-sig{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}',
  '.thm-sig th{font-size:0.6rem;color:var(--dim);font-weight:400;padding:2px 6px;text-align:left;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.thm-sig td{font-size:0.64rem;line-height:1.35;padding:1px 6px;border-bottom:1px solid var(--line-soft);vertical-align:top}',
  '.thm-sig tr:last-child td{border-bottom:0}',
  '.thm-sig .thm-trig{color:var(--danger);font-weight:600}',
  '.thm-sig .thm-notrig{color:var(--dim)}',
  '.thm-sig .thm-nosample{color:var(--warn)}',
  '.thm-two{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:6px}',
  '.thm-three{display:grid;grid-template-columns:minmax(0,1.12fr) minmax(0,0.86fr) minmax(0,1.32fr);gap:6px;align-items:start}',
  '.thm-msg{padding:12px 14px;font-size:0.78rem;line-height:1.7;color:var(--fg);background:var(--danger-bg);border:var(--border-w) var(--border-style) var(--danger);border-radius:var(--radius-md);white-space:pre-wrap;word-break:break-word}',
  '.thm-msg code{font-family:var(--font-mono);font-size:0.72rem;color:var(--fg-strong)}',
  '.thm-foot{font-size:0.6rem;color:var(--dim);line-height:1.5;padding:0 2px 2px}',
  '.thm-foot b{color:var(--fg-strong);font-weight:600}',
  '.thm-foot code{font-family:var(--font-mono);font-size:0.62rem}',
];

// ---------------------------------------------------------------- 纯函数（导出供 node 单测）
export function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 分位：窗口含当日，窗口 = 最近 window 个样本；样本 < minSample → null（页面显示「缺」）。
 *  与 emotion/engine.pct_rank 同式：(严格小于 + 0.5×等于) / 样本数。 */
export function pctRank(values, value, window, minSample) {
  const v = num(value);
  if (v === null) return null;
  const w = window || EMOTION_WINDOW;
  const minN = minSample || PCT_MIN_SAMPLE;
  const tail = (values || []).slice(-w).map(num).filter((x) => x !== null);
  if (tail.length < minN) return null;
  let below = 0;
  let equal = 0;
  for (const x of tail) {
    if (x < v) below += 1;
    else if (x === v) equal += 1;
  }
  return (below + 0.5 * equal) / tail.length;
}

/** 炸板率 = (曾涨停 − 涨停) / 曾涨停；缺值或曾涨停 0 → null。 */
export function burstRate(row) {
  if (!row) return null;
  const zt = num(row.zt_total);
  const ever = num(row.zt_ever);
  if (zt === null || ever === null || ever <= 0) return null;
  return (ever - zt) / ever;
}

/** 按缺失日期与断点日切段：返回若干连续点数组，段与段之间不连线。 */
export function splitSegments(points, breakDate) {
  const out = [];
  let cur = [];
  for (const p of points || []) {
    const v = num(p.value);
    const crossed = breakDate && p.date === breakDate && cur.length > 0;
    if (v === null || crossed) {
      if (cur.length) out.push(cur);
      cur = [];
      if (v === null) continue;
    }
    cur.push({ date: p.date, value: v });
  }
  if (cur.length) out.push(cur);
  return out;
}

/** 三态动作：只按今日两个读数判，规则原文写在页面上（展示用，未经回测）。
 *  三种动作 = 「不加仓 / 做结构 / 等信号」；分歧日按较弱的一侧执行。 */
export function decide(score, panic) {
  const s = num(score);
  const p = num(panic);
  if (s === null) {
    return { key: 'wait', label: '缺读数', why: '情绪指数缺失，不出判定' };
  }
  if (s < 25) {
    if (p !== null && p < 25) {
      return {
        key: 'wait', label: '等信号',
        why: '冰点共振：情绪指数 ' + s.toFixed(2) + ' 与恐慌广度 ' + p.toFixed(1) + ' 同日都 <25，等止跌与放量确认，不接飞刀',
      };
    }
    return { key: 'hold', label: '不加仓', why: '情绪指数 ' + s.toFixed(2) + ' < 25（冰点档），等止跌信号再谈仓位' };
  }
  if (p !== null && p >= 70) {
    return { key: 'hold', label: '不加仓', why: '恐慌广度 ' + p.toFixed(1) + ' ≥ 70（乐观/过热档），高位不接力' };
  }
  if (s < 55) {
    return { key: 'struct', label: '做结构', why: '情绪指数 ' + s.toFixed(2) + ' 在中性偏冷区间，只做有承接的结构，不做扩散' };
  }
  return { key: 'struct', label: '做结构', why: '情绪指数 ' + s.toFixed(2) + ' ≥ 55（偏热档），持股为主、不追高' };
}

export function bandOf(score) {
  const s = num(score);
  if (s === null) return null;
  for (const edge of BANDS) {
    if (s >= edge[0]) return { label: edge[1], cls: edge[2], lower: edge[0] };
  }
  const last = BANDS[BANDS.length - 1];
  return { label: last[1], cls: last[2], lower: last[0] };
}

// ---------------------------------------------------------------- 展示格式化
function fmt(v, digits) {
  const n = num(v);
  if (n === null) return '缺';
  return n.toFixed(digits === undefined ? 2 : digits);
}

function fmtPct(ratio, digits) {
  const n = num(ratio);
  if (n === null) return '缺';
  return (n * 100).toFixed(digits === undefined ? 1 : digits) + '%';
}

function fmtAmt(v) {
  const n = num(v);
  if (n === null) return '缺';
  if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(4) + ' 万亿';
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + ' 亿';
  return n.toFixed(0);
}

function fmtDelta(cur, prev, digits, suffix) {
  const a = num(cur);
  const b = num(prev);
  if (a === null || b === null) return null;
  const d = a - b;
  const sign = d > 0 ? '+' : '';
  return sign + d.toFixed(digits === undefined ? 2 : digits) + (suffix || '');
}

function md(date8) {
  const s = String(date8 || '');
  return s.length === 8 ? s.slice(4, 6) + '-' + s.slice(6, 8) : s;
}

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS.join('\n');
  document.head.append(s);
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function sec(title, hint) {
  const box = el('div', 'thm-sec');
  const head = el('div', 'thm-sec-h');
  head.append(el('b', null, title));
  if (hint) head.append(el('span', 'thm-hint', hint));
  box.append(head);
  return box;
}

const NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  for (const k in attrs) {
    if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, String(attrs[k]));
  }
  return node;
}

// ---------------------------------------------------------------- 卡片
export default {
  cards: [{
    id: 'thermometer.main',
    title: '情绪温度计',
    slot: 'main',
    order: 74,
    async mount(host, ctx) {
      injectStyle();
      const root = el('div', 'thm-root');
      host.append(root);

      // 顶栏
      const bar = el('div', 'thm-bar');
      const stamp = el('span', null, '读数读取中…');
      const hint = el('span', null, '只读接口 · 不重算分数');
      const grow = el('span', 'thm-grow');
      const btn = el('button', 'thm-btn', '刷新');
      btn.type = 'button';
      bar.append(stamp, hint, grow, btn);
      root.append(bar);

      let cleanup = null;

      function showMessage(text) {
        const msg = el('div', 'thm-msg');
        msg.textContent = text;
        root.textContent = '';
        root.append(bar, msg);
      }

      async function load() {
        let emotion = null;
        let panic = null;
        let meta = null;
        let emotionErr = null;
        let panicErr = null;
        try {
          emotion = await ctx.api.get('/api/p/emotion/payload?history_days=' + EMOTION_WINDOW);
        } catch (err) {
          emotionErr = err;
        }
        try {
          panic = await ctx.api.get('/api/panic');
        } catch (err) {
          panicErr = err;
        }
        try {
          meta = await ctx.api.get('/meta');
        } catch (err) {
          meta = null;   // 口径元信息拿不到不阻断页面：断点用常量兜底，行数显示「缺」
        }

        // 四维拆解：上游事实（晋级率、全A中位/量能的分位与原始值）由本层透传给插件后端，
        // 四维打分只在后端算一处（见 dims.py）。上游缺哪个就不传，后端会把该项标「缺」。
        // **恐慌侧只在「/api/panic 的 trade_date 等于情绪侧数据日」时透传**：非交易时段
        // （如零点后至开盘前）该接口会按设计「回落到库内最后一行」并标 mode=closed，
        // 拿它当今日读数就是拿隔夜旧行冒充——一律不传，后端标缺。
        let dims = null;
        let dimsErr = null;
        try {
          const params = new URLSearchParams();
          const set = (k, v) => { const n = num(v); if (n !== null) params.set(k, String(n)); };
          const todayDate = emotion && emotion.today ? String(emotion.today.date || '') : '';
          const panicFresh = !!(panic && todayDate && String(panic.trade_date || '') === todayDate);
          if (emotion && emotion.today) {
            set('promo_rate', emotion.today.promotion_2b);
            set('promo_base', emotion.today.promotion_base);
          }
          if (panic && panicFresh) {
            const items = {};
            for (const it of (panic.items || [])) items[it.key] = it;
            if (items.median) { set('panic_median', items.median.value); set('panic_median_q', items.median.percentile); }
            if (items.amount) { set('panic_amount', items.amount.value); set('panic_amount_q', items.amount.percentile); }
          }
          const qs = params.toString();
          dims = await ctx.api.get('/dims' + (qs ? '?' + qs : ''));
        } catch (err) {
          dimsErr = err;   // 四维拿不到不影响上方读数：该段显示「缺」+ 原因
        }

        const today = (emotion && emotion.today) || null;
        if (!today) {
          showMessage(
            '情绪指数取不到，本页不出读数（缺数据不填 0）。\n' +
            '接口：GET /api/p/emotion/payload?history_days=60\n' +
            '原因：' + (emotionErr ? String(emotionErr.message || emotionErr) : '返回里没有 today') + '\n' +
            '补数据：检查 data/track_pool.db::sentiment 是否有 ts=\'close\' 行。'
          );
          return;
        }
        const hist = (emotion.history || []).slice(-EMOTION_WINDOW);
        const pdates = {};
        for (const p of ((panic && panic.series) || [])) pdates[p.date] = p;
        // 只有在「接口数据日 = 情绪侧数据日」时，才把它的当天点并进曲线（否则该接口
        // 已按设计回落到库内旧行，再并进来就是把隔夜旧值画成今日点）
        if (panic && today && String(panic.trade_date || '') === String(today.date || '') && num(panic.value) !== null) {
          pdates[panic.trade_date] = { date: panic.trade_date, value: panic.value };
        }

        const prev = hist.length > 1 ? hist[hist.length - 2] : null;
        render({ emotion, panic, meta, today, hist, prev, pdates, panicErr, dims, dimsErr });
      }

      function render(d) {
        const { emotion, panic, meta, today, hist, prev, pdates, panicErr, dims, dimsErr } = d;
        root.textContent = '';
        root.append(bar);

        const breakDate = (meta && meta.break_from) || BREAK_FALLBACK;
        const breakLabel = (meta && meta.break_label) || '此前三项口径 / 此后四项含量能';
        const asof = today.date || (emotion.asof || '');
        const caliber = '口径 v' + String(emotion.caliber_version || '?').replace(/^v/, '') + ' · ' + (emotion.caliber_revised || '');

        stamp.textContent = '';
        const b1 = el('b', null, asof ? asof.slice(0, 4) + '-' + md(asof) : '—');
        stamp.append(b1, document.createTextNode(' 收盘 · ' + caliber + ' · ' + (today.band || '—') + ' · 规则 stage ' + (today.stage || '—')));
        hint.textContent = '情绪 ← /api/p/emotion/payload · 恐慌 ← /api/panic';

        // ------------------------------------------------------------ ① KPI 四格
        const kpi = el('div', 'thm-kpi');
        const score = num(today.score);
        // 恐慌侧新鲜度：`/api/panic` 的 trade_date 必须等于情绪侧数据日。不相等 = 该接口
        // 在非交易时段按设计「回落到库内最后一行」（mode=closed/source=store），不能用它当今日读数。
        const asofDate = String(today.date || '');
        const panicFresh = !!(panic && asofDate && String(panic.trade_date || '') === asofDate);
        const panicStale = !!(panic && !panicFresh);
        const panicVal = panicFresh ? num(panic.value) : null;
        const panicPrev = (prev && pdates[prev.date] ? num(pdates[prev.date].value) : null);
        const amt = panicFresh && panic.volume ? num(panic.volume.amount) : null;
        const amtPrev = panicFresh && panic.volume ? num(panic.volume.prev_day_amount) : null;

        function kpiCell(title, src, valueHtml, deltaHtml) {
          const box = el('div', 'thm-k');
          const t = el('div', 'thm-k-t');
          t.append(el('span', null, title), el('span', 'thm-src', src));
          const v = el('div', 'thm-k-v');
          v.append(valueHtml);
          const dd = el('div', 'thm-k-d');
          if (deltaHtml) dd.append(deltaHtml);
          box.append(t, v, dd);
          return box;
        }

        const bandInfo = bandOf(score);
        const v1 = el('b', null, fmt(score, 2));
        const v1s = el('span', null, '/100' + (bandInfo ? ' · ' + bandInfo.label : ''));
        const d1 = el('span');
        const dScore = fmtDelta(score, prev && prev.score, 2);
        if (dScore === null) {
          d1.append(document.createTextNode('前日：缺'));
        } else {
          const cls = num(score) >= num(prev.score) ? 'thm-up' : 'thm-down';
          const s = el('b', cls, dScore);
          d1.append(document.createTextNode('前日 ' + fmt(prev.score, 2) + ' · '), s, document.createTextNode(' 对比'));
        }
        kpi.append(kpiCell('情绪指数', 'emotion 六因子', (() => { const w = el('span'); w.append(v1, v1s); return w; })(), d1));

        const v2 = el('b', null, fmt(panicVal, 1));
        const v2s = el('span', null, '/100 · ' + (panicFresh ? ((panic && panic.state) || '缺') : '缺'));
        const d2 = el('span');
        if (panicErr) {
          d2.append(document.createTextNode('接口失败：' + String(panicErr.message || panicErr).slice(0, 40)));
        } else if (panicStale) {
          d2.append(
            document.createTextNode('接口回落到库内 '),
            el('b', 'thm-miss', md(panic.trade_date) + ' 行'),
            document.createTextNode('（' + (panic.mode || 'closed') + ' · ' + fmt(num(panic.value), 1) + '），非 ' + md(asofDate) + ' 读数 → 显示'),
            el('b', 'thm-miss', '缺')
          );
        } else if (panicPrev === null) {
          const lastDate = Object.keys(pdates).filter((k) => k < prev.date).sort().pop();
          d2.append(
            document.createTextNode('前日：'),
            el('span', 'thm-miss', '缺'),
            document.createTextNode('（' + md(prev.date) + ' 未落库' + (lastDate ? '，最近 ' + md(lastDate) + ' = ' + fmt(pdates[lastDate].value, 1) : '') + '）')
          );
        } else {
          const dd = fmtDelta(panicVal, panicPrev, 1);
          const cls = panicVal >= panicPrev ? 'thm-up' : 'thm-down';
          d2.append(document.createTextNode('前日 ' + fmt(panicPrev, 1) + ' · '), el('b', cls, dd), document.createTextNode(' 对比'));
        }
        kpi.append(kpiCell('恐慌广度', 'core /api/panic', (() => { const w = el('span'); w.append(v2, v2s); return w; })(), d2));

        const up = num(today.up_cnt);
        const down = num(today.down_cnt);
        const v3 = el('b', null, up === null ? '缺' : String(Math.round(up)));
        const v3s = el('span', null, ' / ' + (down === null ? '缺' : String(Math.round(down))));
        const d3 = el('span');
        const prevUp = prev ? num(prev.up_cnt) : null;
        const prevDown = prev ? num(prev.down_cnt) : null;
        if (prevUp === null || prevDown === null) {
          d3.append(document.createTextNode('前日：缺'));
        } else {
          d3.append(
            document.createTextNode('前日 ' + Math.round(prevUp) + ' / ' + Math.round(prevDown) + ' · 上涨 '),
            el('b', prevUp !== null && up !== null && up < prevUp ? 'thm-down' : 'thm-up', (up !== null ? (up - prevUp >= 0 ? '+' : '') + Math.round(up - prevUp) : '缺')),
            document.createTextNode(' 家')
          );
        }
        kpi.append(kpiCell('上涨 / 下跌家数', 'emotion ← sentiment', (() => { const w = el('span'); w.append(v3, v3s); return w; })(), d3));

        const v4 = el('b', null, fmtAmt(amt));
        const v4s = el('span', null, panic && panic.volume && panic.volume.kind === 'actual' ? '收盘实际' : (panic && panic.volume && panic.volume.kind ? '盘中预估' : ''));
        const d4 = el('span');
        if (panicStale) {
          d4.append(document.createTextNode('接口回落到库内旧行，成交额显示'), el('b', 'thm-miss', '缺'));
        } else if (amt === null || amtPrev === null) {
          d4.append(document.createTextNode('前日成交额：缺'));
        } else {
          const r = amt / amtPrev - 1;
          d4.append(
            document.createTextNode('前日 ' + fmtAmt(amtPrev) + ' · '),
            el('b', r >= 0 ? 'thm-up' : 'thm-down', (r >= 0 ? '+' : '') + (r * 100).toFixed(1) + '%'),
            document.createTextNode('（量能比 ' + fmt(panic.volume.ratio, 3) + '× · 分位 ' + fmt(panic.volume.percentile, 1) + '%）')
          );
        }
        kpi.append(kpiCell('成交额', '/api/panic volume', (() => { const w = el('span'); w.append(v4, v4s); return w; })(), d4));
        root.append(kpi);

        // ------------------------------------------------------------ ② 温度计
        const thermo = sec('情绪温度计', '0–100 · 刻度＝档位边界（数字即阀值）');
        const tbody = el('div', 'thm-thermo');
        const scaleWrap = el('div');
        scaleWrap.style.cssText = 'position:relative;padding-top:20px';
        const scale = el('div', 'thm-scale');
        BANDS.forEach((edge) => {
          if (edge[0] <= 0) return;
          const tick = el('div', 'thm-tick');
          tick.style.left = edge[0] + '%';
          scale.append(tick);
        });
        if (score !== null) {
          const ptr = el('div', 'thm-ptr');
          ptr.style.left = 'calc(' + Math.max(0, Math.min(100, score)) + '% - 1px)';
          const pv = el('div', 'thm-ptr-v');
          pv.textContent = fmt(score, 2);
          pv.style.left = Math.max(0, Math.min(100, score)) + '%';
          ptr.append(pv);
          scale.append(ptr);
        }
        scaleWrap.append(scale);
        const lab = el('div', 'thm-lab');
        lab.append(el('span', null, '0 冰点'), el('span', null, '25 偏冷'), el('span', null, '40 中性'), el('span', null, '55 偏热'), el('span', null, '70 亢奋'), el('span', null, '100'));
        scaleWrap.append(lab);
        tbody.append(scaleWrap);

        const foot = el('div', 'thm-thermo-foot');
        const band = el('div', 'thm-band ' + (bandInfo ? bandInfo.cls : 'mid'), bandInfo ? bandInfo.label : '缺');
        const nextEdge = bandInfo ? BANDS.filter((x) => x[0] > score).map((x) => x[0]).sort((a, b) => a - b)[0] : null;
        const say = el('div', 'thm-say');
        if (score === null) {
          say.append(el('b', null, '缺'), document.createTextNode('：情绪指数不可用'));
        } else {
          const gap = nextEdge === undefined || nextEdge === null ? null : nextEdge - score;
          say.append(
            el('b', null, fmt(score, 2)),
            document.createTextNode(' / 100 落'),
            el('b', null, bandInfo.label),
            document.createTextNode('档'),
            document.createTextNode(gap === null ? '（已在最高档）' : '，距上一档（' + nextEdge + '）差 ' + gap.toFixed(2) + ' 分'),
            document.createTextNode('；同日规则 stage = ' + (today.stage || '缺') +
              (today.stage_note ? '（' + String(today.stage_note).slice(0, 48) + (String(today.stage_note).length > 48 ? '…' : '') + '）' : ''))
          );
        }
        foot.append(band, say);
        tbody.append(foot);
        thermo.append(tbody);
        root.append(thermo);

        // ------------------------------------------------------------ ③ 盘面格
        const ztSeries = hist.map((r) => r.zt_total);
        const dtSeries = hist.map((r) => r.dt_total);
        const burstSeries = hist.map((r) => burstRate(r));
        const hSeries = hist.map((r) => r.height);
        const lbSeries = hist.map((r) => r.lb2plus);
        const resealSeries = hist.map((r) => r.reseal_rate);
        const upSeries = hist.map((r) => r.up_cnt);
        const scoreSeries = hist.map((r) => r.score);
        const histN = hist.length;

        const panicItems = {};
        if (panicFresh) {
          for (const it of ((panic && panic.items) || [])) panicItems[it.key] = it;
        }

        const gridBox = sec('今日盘面指标', '情绪侧分位：' + histN + ' 日窗口含当日 · 恐慌侧分位：250 日窗口（各自 owner 口径，不混用）');
        const grid = el('div', 'thm-grid');
        const promoBase = num(today.promotion_base);
        const promoRate = num(today.promotion_2b);
        const promoMin = num(emotion.min_promotion_sample) || 10;

        function numText(v, digits, suffix) {
          const n = num(v);
          return n === null ? '缺' : n.toFixed(digits) + (suffix || '');
        }

        function panicPct(key) {
          const it = panicItems[key];
          const v = it ? num(it.percentile) : null;
          return v === null ? null : v / 100;
        }

        const cells = [
          { t: '情绪指数分位', v: fmt(score, 2), u: '分', p: pctRank(scoreSeries, score), note: '越高越热' },
          { t: '涨停家数', v: fmt(today.zt_total, 0), u: '家', p: pctRank(ztSeries, today.zt_total) },
          { t: '跌停家数', v: fmt(today.dt_total, 0), u: '家', p: pctRank(dtSeries, today.dt_total) },
          { t: '炸板率', v: fmtPct(burstRate(today)), u: '', p: pctRank(burstSeries, burstRate(today)) },
          { t: '最高板', v: fmt(today.height, 0), u: '板', p: pctRank(hSeries, today.height) },
          { t: '连板数（2 板+）', v: fmt(today.lb2plus, 0), u: '家', p: pctRank(lbSeries, today.lb2plus) },
          { t: '封板率 reseal_rate', v: fmt(today.reseal_rate, 1) + '%', u: '', p: pctRank(resealSeries, today.reseal_rate) },
          {
            t: '晋级率（2→3 板）',
            v: promoRate === null ? '缺' : fmtPct(promoRate),
            u: '',
            p: null,
            note: promoBase === null ? '无分母样本' : (promoBase < promoMin ? '样本不足：分母 ' + promoBase + ' 只 < ' + promoMin : '分母 ' + promoBase + ' 只'),
            warn: promoBase !== null && promoBase < promoMin,
          },
          // 恐慌侧分位由 /api/panic 直接给出（百分数单位，如 85.2）；这里统一折成 0–1 再展示
          { t: '跌停占比', v: numText(panicItems.limit_down ? panicItems.limit_down.value : null, 2, '%'), u: '', p: panicPct('limit_down'), base: PANIC_WINDOW },
          { t: '上涨占比', v: numText(panicItems.up_ratio ? panicItems.up_ratio.value : null, 2, '%'), u: '', p: panicPct('up_ratio'), base: PANIC_WINDOW },
          { t: '全A中位涨跌', v: numText(panicItems.median ? panicItems.median.value : null, 2, '%'), u: '', p: panicPct('median'), base: PANIC_WINDOW },
          { t: '量能（成交额/昨日）', v: numText(panicItems.amount ? panicItems.amount.value : null, 3, '×'), u: '', p: panicPct('amount'), base: PANIC_WINDOW },
        ];
        for (const c of cells) {
          const cell = el('div', 'thm-c');
          cell.append(el('div', 'thm-c-t', c.t));
          const vv = el('div', 'thm-c-v');
          vv.append(document.createTextNode(c.v));
          if (c.u) vv.append(el('small', null, c.u));
          const pp = el('div', 'thm-c-p');
          if (c.p !== null && c.p !== undefined) {
            pp.append(document.createTextNode('分位 ' + (c.p * 100).toFixed(0) + '% · ' + (c.base || histN) + ' 日'), document.createTextNode(c.note ? ' · ' + c.note : ''));
          } else if (c.warn) {
            pp.append(el('span', 'thm-nodata', c.note));
          } else {
            pp.append(el('span', 'thm-nodata', c.note ? c.note + ' · 分位缺' : '分位缺（样本 <' + PCT_MIN_SAMPLE + '）'));
          }
          const row = el('div', 'thm-c-row');
          row.append(vv, pp);
          cell.append(row);
          grid.append(cell);
        }
        gridBox.append(grid);
        root.append(gridBox);

        // ------------------------------------------------------------ ③b 四维拆解（广度 / 接力 / 资金 / 位置）
        // 分数只在后端算（dims.py）：子项分位 → 方向统一为越高越热 → 维内等权、缺失跳过。
        // 四维之间不加权合成；情绪总分（上方 KPI）仍由 emotion 提供、本段不碰。
        const DIM_BAND_CLS = { 极冷: 'cold', 偏冷: 'cool', 中性: 'mid', 偏热: 'warm', 过热: 'hot' };
        const DIM_BAND_FILL = { cold: 'var(--accent)', cool: 'var(--dim)', mid: 'var(--fg)', warm: 'var(--warn)', hot: 'var(--up)' };
        const DIM_SAY = {
          breadth: { 极冷: '上涨面几乎消失', 偏冷: '赚钱面偏窄', 中性: '涨跌基本对半', 偏热: '赚钱面铺开', 过热: '普涨' },
          relay: { 极冷: '接力断了，没人愿意接', 偏冷: '连板生态偏弱', 中性: '接力一般', 偏热: '接力顺畅', 过热: '高度扩张' },
          capital: { 极冷: '主力资金净流出、缩量', 偏冷: '资金偏弱', 中性: '资金中性', 偏热: '资金有增量', 过热: '资金涌入' },
          position: { 极冷: '位置很低', 偏冷: '位置偏低', 中性: '中位', 偏热: '位置偏高', 过热: '高位' },
        };
        const DIM_HINT = {
          breadth: '上涨家数占比 / 涨停 / 跌停 / 全A中位',
          relay: '最高板 / 连板数 / 晋级率 / 炸板率',
          capital: '量能 / 主力资金(880008) / 龙虎榜 / 北向',
          position: '距 MA20 / MA60 / 250 日高（880008 全A）',
        };
        // 子项条用的短标签 + 小数位（原始值在后端已换算成展示单位）
        const DIM_ITEM_SHORT = {
          up_ratio: '上涨占比', zt_total: '涨停', dt_total: '跌停', median_pct: '全A中位',
          height: '最高板', lb2plus: '连板', promotion: '晋级率', burst: '炸板率',
          amount: '量能', main_ratio: '主力资金', dragon: '龙虎榜', northbound: '北向',
          dev20: 'MA20偏离', dev60: 'MA60偏离', gap250: '距250日高',
        };
        const DIM_ITEM_DIGITS = { zt_total: 0, dt_total: 0, height: 0, lb2plus: 0 };
        function itemValueText(it) {
          const raw = it.raw;
          if (raw === null || raw === undefined) return '缺';
          const dg = DIM_ITEM_DIGITS[it.key];
          return fmt(raw, dg === undefined ? 2 : dg) + (it.unit || '');
        }

        const dimsBox = sec('四维拆解', '各维独立 0–100、不加权合成；越高越热（分数不是百分比）');
        const dimsRow = el('div', 'thm-dims');
        if (!dims || !dims.dims || !dims.dims.length) {
          const msg = el('div', 'thm-dim');
          msg.style.gridColumn = '1 / -1';
          msg.append(el('div', 'thm-dim-h', '四维拆解取不到'));
          msg.append(el('div', 'thm-dim-n', dimsErr ? String(dimsErr.message || dimsErr).slice(0, 120)
            : ((dims && dims.reason) || '后端 /dims 无数据')));
          dimsRow.append(msg);
        } else {
          for (const dim of dims.dims) {
            const box = el('div', 'thm-dim');
            const head = el('div', 'thm-dim-h');
            head.append(el('b', null, dim.label), el('span', null, dim.n_used + '/' + dim.n_total + ' 项'));
            box.append(head);

            const vrow = el('div', 'thm-dim-v');
            if (dim.score === null || dim.score === undefined) {
              vrow.append(el('b', null, '缺'));
              const chip = el('div', 'thm-dim-band mid', '无读数');
              vrow.append(chip);
            } else {
              vrow.append(el('b', null, dim.score.toFixed(1)));
              const cls = DIM_BAND_CLS[dim.band] || 'mid';
              vrow.append(el('div', 'thm-dim-band ' + cls, dim.band));
            }
            box.append(vrow);

            const bar = el('div', 'thm-dim-bar');
            const fill = el('div', 'thm-dim-fill');
            const pctFill = dim.score === null || dim.score === undefined ? 0 : Math.max(0, Math.min(100, dim.score));
            fill.style.width = pctFill + '%';
            fill.style.background = dim.score === null ? 'transparent' : (DIM_BAND_FILL[DIM_BAND_CLS[dim.band]] || 'var(--fg)');
            bar.append(fill);
            box.append(bar);

            const line = el('div', 'thm-dim-n');
            if (dim.score === null || dim.score === undefined) {
              const why = dim.note || (dim.missing && dim.missing[0] && dim.missing[0].why) || '无子项参与';
              line.textContent = /^缺/.test(why) ? why : '缺：' + why;
              line.title = (dim.missing && dim.missing[0] && dim.missing[0].why) || why;
            } else {
              const say = (DIM_SAY[dim.key] || {})[dim.band] || '';
              // 资金格：5 日对比直接顶掉「一句说明」（读数差距才是重点，不平白占高度）
              const extra = dim.flow_compare ? dim.flow_compare
                : ((DIM_HINT[dim.key] || '')
                  + (dim.n_used < dim.n_total ? ' · ' + dim.missing.map((m) => m.label + '缺').join('/') : ''));
              line.textContent = dim.flow_compare ? extra : (say + ' · ' + extra);
              line.title = dim.note || '';
            }
            box.append(line);

            // 子项条：每项原始值 + 分位（缺项也列出来标「缺」，不让它凭空消失）
            const its = Array.isArray(dim.items) ? dim.items : [];
            if (its.length) {
              const strip = el('div', 'thm-dim-items');
              its.forEach((it) => {
                const one = el('div', 'thm-dim-it' + (it.used ? '' : ' miss'));
                one.append(el('s', null, DIM_ITEM_SHORT[it.key] || it.label));
                one.append(el('i', null, itemValueText(it)));
                // 分位槽：四维共用一个样式——有分位就显示百分数，没有就标「无分位」
                //（资金维度的 250 日历史分位从后端 percentile 来，2026-11-06 起不再只有单点读数）
                if (it.percentile !== null && it.percentile !== undefined) {
                  // 序列陈旧时后端给 stale=true：分位照常显示，但标「参考」且不计入该维分数
                  one.append(el('em', null, Math.round(it.percentile * 100) + '%' + (it.stale ? '·参考' : '')));
                } else {
                  one.append(el('em', null, '无分位'));
                }
                one.title = [it.label, itemValueText(it),
                  it.main_net_yi !== null && it.main_net_yi !== undefined ? ('净额 ' + fmt(it.main_net_yi, 2) + ' 亿') : '',
                  it.percentile === null || it.percentile === undefined ? '' : ('分位 ' + (it.percentile * 100).toFixed(1) + '%'),
                  it.base ? (it.base + ' 日基') : '', it.asof ? ('数据日 ' + it.asof) : '',
                  it.t_plus_1 ? 'T+1' : '', it.why || ''].filter(Boolean).join(' · ');
                strip.append(one);
              });
              box.append(strip);
            }
            dimsRow.append(box);
          }
        }
        dimsBox.append(dimsRow);
        root.append(dimsBox);

        // ------------------------------------------------------------ ④ 今日 vs 昨日（单表，9 行：只放 KPI / 盘面格不重复的项）
        const cmp = sec('今日 vs 昨日', prev ? '昨日 = ' + (prev.date.slice(0, 4) + '-' + md(prev.date)) + '（上涨/下跌家数、晋级率见 KPI 与盘面格）' : '无前一日');
        const tb = el('table', 'thm-tb');
        const thead = el('thead');
        const htr = el('tr');
        ['指标', '今日', '昨日', '变化'].forEach((t) => htr.append(el('th', null, t)));
        thead.append(htr);
        tb.append(thead);
        const tbBody = el('tbody');
        tb.append(tbBody);

        function row(label, cur, prevV, digits, suffix) {
          const a = num(cur);
          const b = num(prevV);
          const tr = el('tr');
          tr.append(el('td', null, label));
          tr.append(el('td', null, a === null ? '缺' : fmt(a, digits) + (suffix || '')));
          tr.append(el('td', null, b === null ? '缺' : fmt(b, digits) + (suffix || '')));
          const dd = fmtDelta(a, b, digits, suffix || '');
          const td = el('td');
          if (dd === null) {
            td.append(el('span', 'thm-miss', '—'));
          } else {
            td.textContent = dd;
            td.className = a >= b ? 'thm-up' : 'thm-down';
          }
          tr.append(td);
          tbBody.append(tr);
          return tr;
        }

        const panicPrevCell = prev && pdates[prev.date] ? num(pdates[prev.date].value) : null;
        row('情绪指数', score, prev ? prev.score : null, 2);
        row('恐慌广度', panicVal, panicPrevCell, 1);
        row('涨停家数', num(today.zt_total), prev ? num(prev.zt_total) : null, 0, ' 家');
        row('跌停家数', num(today.dt_total), prev ? num(prev.dt_total) : null, 0, ' 家');
        row('炸板率', burstRate(today) === null ? null : burstRate(today) * 100,
          prev && burstRate(prev) !== null ? burstRate(prev) * 100 : null, 2, '%');
        row('最高板', num(today.height), prev ? num(prev.height) : null, 0, ' 板');
        row('连板数（2 板+）', num(today.lb2plus), prev ? num(prev.lb2plus) : null, 0, ' 家');
        row('成交额（万亿）', amt === null ? null : amt / 1e12, amtPrev === null ? null : amtPrev / 1e12, 4, ' 万亿');
        cmp.append(tb);

        const dec = decide(score, panicVal);
        const doBox = sec('现在该做什么', '判定规则见下（展示用，未经回测）');
        const doRow = el('div', 'thm-do');
        const doV = el('div', 'thm-do-v ' + dec.key, dec.label);
        const doWhy = el('div', 'thm-do-why');
        doWhy.append(el('b', null, '依据：'), document.createTextNode(dec.why + '。'));
        if (score !== null && panicVal !== null) {
          const both = (score < 25 && panicVal < 25) ? '两个口径同日都落冰点区，方向一致（不是一冷一热的分歧）。'
            : (score >= 55 && panicVal >= 55 ? '两个口径同日都偏热，方向一致。' : '两个口径不同档，属分歧日，按较弱的那一侧执行。');
          doWhy.append(document.createTextNode(' ' + both));
        }
        if (panicErr) doWhy.append(document.createTextNode('（恐慌广度接口失败，判定只用了情绪指数）'));
        doRow.append(doV, doWhy);
        doBox.append(doRow);
        const rules = el('div', 'thm-rules');
        rules.textContent = '判定（写死在页面）：① 情绪<25 且 恐慌<25 → 等信号；② 情绪<25 → 不加仓；'
          + '③ 恐慌≥70 → 不加仓；④ 25≤情绪<55 → 做结构；⑤ 情绪≥55 → 做结构（持股为主）。'
          + '只有三种动作：不加仓 / 做结构 / 等信号。';
        doBox.append(rules);

        // ------------------------------------------------------------ ⑤ 趋势图 + ⑥ 预警（并排）
        const two2 = el('div', 'thm-two');
        const chartBox = sec('近 ' + histN + ' 个交易日', '情绪指数 / 恐慌广度 / 上涨家数（柱）');
        const chartHost = el('div', 'thm-chart-box');
        chartBox.append(chartHost);
        const legend = el('div', 'thm-legend');
        legend.append(
          (() => { const s = el('span'); const i = el('i'); i.style.borderTopColor = 'var(--accent)'; s.append(i, el('b', null, '情绪指数'), document.createTextNode(' 左轴 0–100 越高越热')); return s; })(),
          (() => { const s = el('span'); const i = el('i'); i.style.borderTopColor = 'var(--warn)'; i.style.borderTopStyle = 'dashed'; s.append(i, el('b', null, '恐慌广度'), document.createTextNode(' 左轴 0–100 同向：越高越热、低值=恐慌')); return s; })(),
          (() => { const s = el('span'); const i = el('span', 'thm-lg-b'); i.style.background = 'var(--dim)'; i.style.opacity = '0.5'; s.append(i, el('b', null, '上涨家数'), document.createTextNode(' 右轴 0–' + (upSeries.length ? Math.max.apply(null, upSeries.map((x) => num(x) || 0)) : 0) + ' 家')); return s; })()
        );
        chartBox.append(legend);
        const cap = el('div', 'thm-cap');
        chartBox.append(cap);
        two2.append(chartBox);

        const alerts = sec('预警区', '按今日读数逐条判');
        const alertList = el('div', 'thm-alerts');
        function alert(kind, tag, text) {
          const row = el('div', 'thm-a ' + kind);
          row.append(el('em', null, tag), el('span', null, text));
          alertList.append(row);
        }
        if (score !== null && score < 25) {
          alert('alert', '档位', '情绪 ' + fmt(score, 2) + ' 冰点档：58 日分位 ' +
            (pctRank(scoreSeries, score) === null ? '缺' : (pctRank(scoreSeries, score) * 100).toFixed(0) + '%') +
            '，距上一档差 ' + (nextEdge === null || nextEdge === undefined ? '—' : (nextEdge - score).toFixed(2)) + ' 分');
        }
        const dtv = num(today.dt_total);
        const ztv = num(today.zt_total);
        const br = burstRate(today);
        if ((dtv !== null && ztv !== null && dtv > ztv) || (br !== null && br > 0.2)) {
          const parts = [];
          if (dtv !== null && ztv !== null && dtv > ztv) parts.push('跌停 ' + dtv + ' > 涨停 ' + ztv + '（因子4 −10）');
          if (br !== null && br > 0.2) parts.push('炸板率 ' + (br * 100).toFixed(2) + '%（>20%，因子3 ' + (20 - br * 100).toFixed(2) + '）');
          alert('warn', '因子', parts.join('；'));
        }
        if (promoBase !== null && promoBase < promoMin) {
          alert('info', '样本', '晋级率 ' + (promoRate === null ? '缺' : fmtPct(promoRate)) + ' 分母仅 ' + promoBase + ' 只（<' + promoMin + '）→ 不给分');
        }
        alert('alert', '口径', '恐慌 ' + md(breakDate) + ' 起四项（含量能），左段三项值与今日不可直接比');
        if (meta && meta.store && meta.store.last && prev && meta.store.last < prev.date) {
          const gapDays = hist.filter((r) => r.date > meta.store.last && r.date <= prev.date).length;
          alert('alert', '数据', '恐慌序列库最新 ' + md(meta.store.last) + '，' + gapDays + ' 个交易日未落库 → 昨日恐慌标缺（不补值）');
        }
        if (panicStale) {
          alert('alert', '数据', '/api/panic 现回落到库内 ' + md(panic.trade_date) + ' 旧行（' + (panic.mode || 'closed') +
            '）→ 恐慌/成交额/四项格/四维里的量能均标缺，不拿旧行充今日');
        }
        // 两融已移出资金维度（2026-10-30：逐票数据、无市场级接口，300 只聚合 2.6 分钟不抵成本）
        if (meta && !meta.ok) {
          alert('warn', '数据', '口径元信息不可读：断点用常量 ' + breakDate + '，行数标缺');
        }
        alert('info', '核对', '上涨家数两处：emotion ' + (up === null ? '缺' : Math.round(up)) + ' / panic ' +
          (panicItems.up_ratio && num(panicItems.up_ratio.count) !== null ? Math.round(num(panicItems.up_ratio.count)) : '缺') +
          (up !== null && panicItems.up_ratio && num(panicItems.up_ratio.count) === up ? '（一致）' : (panicFresh ? '（不一致，需查源）' : '')));
        alerts.append(alertList);
        two2.append(alerts);
        root.append(two2);

        // ------------------------------------------------------------ ⑧ 信号 → 动作
        const sigBox = sec('信号 → 动作', '胜率 / 次数写「样本不足（' + histN + ' 日）」，不编数字');
        const sig = el('table', 'thm-sig');
        const sHead = el('thead');
        const sHtr = el('tr');
        ['触发条件', '今日', '该做什么', '历史胜率 / 次数（58 日）'].forEach((t) => sHtr.append(el('th', null, t)));
        sHead.append(sHtr);
        sig.append(sHead);
        const sBody = el('tbody');
        const conds = [
          { c: '情绪 <15 且 恐慌 <15', hit: score !== null && panicVal !== null && score < 15 && panicVal < 15, a: '等信号（不出手）' },
          { c: '跌停 > 涨停', hit: dtv !== null && ztv !== null && dtv > ztv, a: '不加仓' },
          { c: '炸板率 >35%', hit: br !== null && br > 0.35, a: '不追高，等封板率回升' },
          { c: '恐慌 <10 且 量能分位 >60', hit: panicVal !== null && panicVal < 10 && num(panicItems.amount && panicItems.amount.percentile) !== null && num(panicItems.amount.percentile) > 60, a: '等反转确认' },
          { c: '情绪 ≥70 且 恐慌 ≥70', hit: score !== null && panicVal !== null && score >= 70 && panicVal >= 70, a: '过热，减仓 / 不接力' },
        ];
        for (const item of conds) {
          const tr = el('tr');
          tr.append(el('td', null, item.c));
          const tdHit = el('td', item.hit ? 'thm-trig' : 'thm-notrig', item.hit ? '触发' : '未触发');
          tr.append(tdHit);
          tr.append(el('td', null, item.a));
          tr.append(el('td', 'thm-nosample', '样本不足（' + histN + ' 日）'));
          sBody.append(tr);
        }
        sig.append(sBody);
        sigBox.append(sig);

        // ④ / ⑦ / ⑧ 三列并排：对比表 | 该做什么 | 信号→动作（横向铺开，省纵向空间）
        const three = el('div', 'thm-three');
        three.append(cmp, doBox, sigBox);
        root.append(three);

        // ------------------------------------------------------------ 页脚：数字来源
        const footBox = el('div', 'thm-foot');
        footBox.append(
          el('b', null, '数字来源：'),
          el('code', null, 'emotion/payload'),
          document.createTextNode(' 情绪+盘面 · '),
          el('code', null, 'api/panic'),
          document.createTextNode(' 恐慌/成交额/量能 · '),
          el('code', null, 'thermometer/meta'),
          document.createTextNode(' 断点与 ' + (meta && meta.ok ? meta.store.rows + ' 行' : '缺')),
          document.createTextNode(' · '),
          el('code', null, 'thermometer/dims'),
          document.createTextNode(' 四维（sentiment + 880008 全A + L2 主力资金 250 日序列 + 龙虎榜，后端算）')
        );
        root.append(footBox);

        // ------------------------------------------------------------ 画图
        const calm = () => { if (cleanup) { cleanup(); cleanup = null; } };
        calm();
        cleanup = drawChart(chartHost, {
          hist, pdates, breakDate, breakLabel, cap,
        });
      }

      // 图：情绪线（实线）+ 恐慌线（断点为界：历史虚线 / 今日实心点）+ 上涨家数柱（右轴）
      function drawChart(host, opt) {
        host.textContent = '';
        const { hist, pdates, breakDate, breakLabel, cap } = opt;
        if (!hist || !hist.length) return null;

        const width = Math.max(360, host.clientWidth || 640);
        const height = 50;
        const padL = 26;
        const padR = 30;
        const padT = 8;
        const padB = 30;
        const plotW = width - padL - padR;
        const plotH = height - padT - padB;
        const n = hist.length;
        const xAt = (i) => padL + (n <= 1 ? plotW / 2 : (plotW * i) / (n - 1));
        const yAt = (v) => padT + plotH * (1 - Math.max(0, Math.min(100, v)) / 100);
        const upMax = Math.max(1, Math.max.apply(null, hist.map((r) => num(r.up_cnt) || 0)));
        const yUp = (v) => padT + plotH * (1 - Math.max(0, Math.min(upMax, v)) / upMax);

        const svg = svgEl('svg', { class: 'thm-chart', viewBox: '0 0 ' + width + ' ' + height, height: height, preserveAspectRatio: 'none' });

        // 网格 0/25/40/55/70/100
        [0, 25, 40, 55, 70, 100].forEach((v) => {
          svg.append(svgEl('line', {
            x1: padL, x2: padL + plotW, y1: yAt(v), y2: yAt(v),
            stroke: 'var(--line-soft)', 'stroke-width': v === 50 ? 1 : 0.6,
          }));
          const t = svgEl('text', { x: padL - 5, y: yAt(v) + 3, 'text-anchor': 'end', 'font-size': 9, fill: 'var(--dim)' });
          t.textContent = String(v);
          svg.append(t);
        });
        const tR = svgEl('text', { x: padL + plotW + 4, y: yAt(100) + 3, 'text-anchor': 'start', 'font-size': 9, fill: 'var(--dim)' });
        tR.textContent = '情绪/恐慌';
        svg.append(tR);
        const tR2 = svgEl('text', { x: padL + plotW + 4, y: yAt(0) + 3, 'text-anchor': 'start', 'font-size': 9, fill: 'var(--dim)' });
        tR2.textContent = '0';
        svg.append(tR2);

        // 上涨家数柱（右轴刻度在外侧）
        const barW = Math.max(1.6, Math.min(7, plotW / Math.max(1, n) * 0.42));
        hist.forEach((r, i) => {
          const v = num(r.up_cnt);
          if (v === null) return;
          const y = yUp(v);
          svg.append(svgEl('rect', {
            x: xAt(i) - barW / 2, y: y, width: barW, height: Math.max(0.6, padT + plotH - y),
            fill: 'var(--dim)', 'fill-opacity': 0.28,
          }));
        });

        // 情绪线
        const scorePts = hist.map((r, i) => ({ date: r.date, value: num(r.score), x: xAt(i) }));
        const scoreSegs = splitSegments(scorePts, null);
        for (const seg of scoreSegs) {
          if (seg.length === 1) continue;
          const poly = svgEl('polyline', {
            points: seg.map((p) => {
              const i = scorePts.findIndex((q) => q.date === p.date);
              return xAt(i) + ',' + yAt(p.value);
            }).join(' '),
            fill: 'none', stroke: 'var(--accent)', 'stroke-width': 1.6, 'stroke-linejoin': 'round',
          });
          svg.append(poly);
        }
        scorePts.forEach((p, i) => {
          if (p.value === null) return;
          svg.append(svgEl('circle', {
            cx: xAt(i), cy: yAt(p.value), r: i === n - 1 ? 3.2 : 1.5,
            fill: i === n - 1 ? 'var(--accent)' : 'var(--bg)', stroke: 'var(--accent)', 'stroke-width': 1,
          }));
        });

        // 恐慌线：按断点切段（历史段虚线，断点日之后实线）；缺失日断开，不跨断点连线
        const panicPts = hist.map((r, i) => {
          const row = pdates[r.date];
          return { date: r.date, value: row ? num(row.value) : null, x: xAt(i) };
        });
        const panicSegs = splitSegments(panicPts, breakDate);
        for (const seg of panicSegs) {
          if (seg.length < 2) continue;
          const dashed = seg[seg.length - 1].date < breakDate;
          svg.append(svgEl('polyline', {
            points: seg.map((p) => {
              const i = panicPts.findIndex((q) => q.date === p.date);
              return xAt(i) + ',' + yAt(p.value);
            }).join(' '),
            fill: 'none', stroke: 'var(--warn)', 'stroke-width': 1.4,
            'stroke-dasharray': dashed ? '3 2.5' : '',
            'stroke-linejoin': 'round',
          }));
        }
        panicPts.forEach((p, i) => {
          if (p.value === null) return;
          const isBreak = p.date === breakDate;
          svg.append(svgEl('circle', {
            cx: xAt(i), cy: yAt(p.value), r: isBreak ? 3.4 : 1.6,
            fill: isBreak ? 'var(--warn)' : 'var(--bg)', stroke: 'var(--warn)', 'stroke-width': 1,
          }));
        });

        // 断点竖线 + 文字标注
        const bi = hist.findIndex((r) => r.date === breakDate);
        const bx = bi >= 0 ? xAt(bi) : padL + plotW;
        svg.append(svgEl('line', {
          x1: bx, x2: bx, y1: padT, y2: padT + plotH,
          stroke: 'var(--danger)', 'stroke-width': 1, 'stroke-dasharray': '4 3',
        }));
        const bt = svgEl('text', {
          x: bx - 5, y: padT + 9, 'text-anchor': 'end', 'font-size': 9, fill: 'var(--danger)',
        });
        bt.textContent = '口径切换 ' + md(breakDate);
        svg.append(bt);

        // x 轴
        [0, Math.floor((n - 1) / 2), n - 1].forEach((i, k) => {
          if (i < 0 || !hist[i]) return;
          const t = svgEl('text', {
            x: xAt(i), y: height - 16, 'text-anchor': k === 0 ? 'start' : (k === 2 ? 'end' : 'middle'),
            'font-size': 9, fill: 'var(--dim)',
          });
          t.textContent = md(hist[i].date);
          svg.append(t);
        });

        host.append(svg);

        // 悬停读数
        const readout = el('div', 'thm-cap');
        const lastP = hist[n - 1];
        const lastPanic = pdates[lastP.date] ? num(pdates[lastP.date].value) : null;
        const text = (i) => {
          const r = hist[i];
          const pv = pdates[r.date] ? num(pdates[r.date].value) : null;
          return r.date.slice(0, 4) + '-' + md(r.date) + ' · 情绪 ' + fmt(r.score, 2) +
            ' · 恐慌 ' + (pv === null ? '缺' : pv.toFixed(1)) +
            ' · 上涨 ' + (num(r.up_cnt) === null ? '缺' : Math.round(num(r.up_cnt))) + ' 家' +
            ' · 涨停 ' + (num(r.zt_total) === null ? '缺' : Math.round(num(r.zt_total))) +
            ' · stage ' + (r.stage || '缺');
        };
        readout.textContent = '悬停查看任意一日：' + text(n - 1);
        host.append(readout);
        if (cap) cap.textContent = '断点 ' + breakDate.slice(0, 4) + '-' + md(breakDate) + '（' + breakLabel + '）：今日恐慌 ' +
          (lastPanic === null ? '缺' : lastPanic.toFixed(1)) + ' 为四项口径实心点，历史为三项虚线，两点不连线、不直接比较';

        function onMove(ev) {
          const rect = svg.getBoundingClientRect();
          const x = ev.clientX - rect.left;
          const ratio = (x - padL) / Math.max(1, plotW);
          const i = Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))));
          readout.textContent = '悬停查看任意一日：' + text(i);
        }
        svg.addEventListener('mousemove', onMove);
        return function () { svg.removeEventListener('mousemove', onMove); };
      }

      btn.addEventListener('click', function () { load(); });
      await load();
    },

    async unmount() {
      // 图表只挂了 mousemove 监听，节点随卡片移除即失效；无定时器需要清。
    },
  }],
};
