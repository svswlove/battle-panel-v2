/**
 * 板块卡片 · 详情卡（点板块弹出）
 *
 * **DOM 与类名逐字照抄 8002** `cardHtml`（app.js:1309，205 行）与 `bindCard`（app.js:1517 起）：
 *   `.card-hd > .card-nm + .pin-lv + .card-sub + .card-periods(.cp-btn) + .card-hd-right(.card-refresh/.card-x)`
 *   `.card-body > .card-left(.card-block × 4) + .card-right.card-block(.card-tbl)`
 *   左侧四块：板块走势（活跃市值+MA10 带子）/ 板块指数收盘+黄白线 / 板块数据(.pin-cells) / 贡献分项(.ctb)
 *   右侧：成分股表（.card-tbl，列头可排序，.dk 四态、.bd 板块标识、.sg 买点徽标、.sec-rank 名次）
 *
 * 契约：
 *   · 只调 v2 已有端点：/series /members/{code}?full=1 /contrib /score_trail
 *     + 兼容层 /api/kline/{code}（板块指数日线，取 c/yellow/white 画黄白线）
 *   · 8002 的「实时口径」没搬：主人 2026-10-22 明确回退，本卡纯收盘口径。
 *   · 颜色全走主题 token（不写新 hex）。
 */

import { el, esc, heatGrid } from './board.js';

/* 8002 `SIG_META`（app.js:1262）逐字；配色在 legacy.css.js 的 .sg-* 里（走 token）。 */
const SIG_META = {
  B1_W: { t: 'B1', c: 'b1', d: 'B1·低位整理（ENV_OK + J<16，尚未出现阳线转强确认）' },
  B1_Y: { t: 'B1', c: 'y', d: 'B1·阳线转强（J<16 + RSI6 上行 + 收阳）' },
  B1_Q: { t: 'B1', c: 'q', d: 'B1·缩量蓄势（窄幅基底、无阴量派发，ACCUM_BASE）' },
  TOP_RES: { t: 'B1', c: 'r', d: 'B1·砖首转强（砖型图首根走强 + J 低位）' },
  B2: { t: 'B2', c: 'b2', d: 'B2 放量突破' },
  B3: { t: 'B3', c: 'b3', d: 'B3' },
  B4: { t: 'B4', c: 'b4', d: 'B4' },
  A_GOLD: { t: '针', c: 'y', d: '黄金针（仅日线）' },
  B_WHITE: { t: '针', c: 'w', d: '白金针' },
  MOM: { t: '动', c: 'mo', d: '动能选股' },
  WEEK_B1: { t: '周', c: 'wk', d: '周线B1（每周五更新）' },
  '周': { t: '周', c: 'wk', d: '周线B1（每周五更新）' },
  ZHU_P: { t: '砖', c: 'zhu-p', d: '砖形图·终极起爆（个股）' },
  ZHU_O: { t: '砖', c: 'zhu-o', d: '砖形图·单日反包起爆（个股）' },
  ZHU_W: { t: '砖', c: 'zhu-w', d: '砖形图·反转确立（个股）' },
};

const LV_TAG = { L1: '一级', L2: '二级', L3: '三级', CONCEPT: '概念', INDEX: '指数' };
const SCALE_A = 10;   // 亿元：×10 整数（与后端 _amv_int 同口径）

const fmtAmt = (v) => (v == null ? '—' : (v / SCALE_A).toFixed(1));
const fmtPp = (v) => (v == null ? '—' : (v / 1e6 * 100 >= 0 ? '+' : '') + (v / 1e6 * 100).toFixed(2));
const fmtPctNum = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%');
const clsNum = (v) => (v == null ? 'flat' : (v > 0 ? 'pos' : (v < 0 ? 'neg' : 'flat')));

/* 8002 `CARD_COLS`（app.js:1178）逐字改：9 列 → 7 列（2026-11-01 主人：
 * 「右边不显示那么多个股信息，把序号、名次和评分去掉」）。
 * 去掉的列：序号（seq）/ 评分（score）/ 名次（sec_rank）—— 都是排序辅助，
 * 内联右栏要让位给左侧图表；个股看细的走右键进板块详情。
 * 保留：股票 / 打分（六维，来自 score 插件）/ 活跃市值(均) / 权重变化 / 活跃涨跌 / 真实涨跌 / 主力净流入。
 * 默认仍按 d_contrib 降序。 */
const CARD_COLS = [
  { k: 'name', t: '股票', sortable: true, dir: 1, tip: '点此排序：有买入信号的个股排前面（再按活跃市值名次）' },
  // 2026-10-29：接个股打分（口径唯一源 = score 插件的 /api/p/score/map，本卡不重算）
  { k: 'dscore', t: '打分', sortable: true, dir: 1,
    tip: '个股打分（六维加权 0-100）+ 评级 S/A/B/C/D；日频收盘口径，盘中不更新' },
  { k: 'amt_avg', t: '活跃市值(均)', sortable: true, dir: 1,
    tip: '区间内每日「活跃流通股本 × 收盘」的均值，避免单日噪声误判规模',
    fmt: (v) => (v == null ? '—' : fmtAmt(v) + '亿') },
  { k: 'd_contrib', t: '权重变化', sortable: true, dir: 1, fmt: (v) => fmtPp(v), cls: clsNum },
  { k: 'amv_pct', t: '活跃涨跌', sortable: true, dir: 1, fmt: (v) => fmtPctNum(v), cls: clsNum },
  { k: 'real_pct', t: '真实涨跌', sortable: true, dir: 1, fmt: (v) => fmtPctNum(v), cls: clsNum },
  { k: 'main_flow', t: '主力净流入', sortable: true, dir: 1, cls: clsNum,
    fmt: (v) => (v == null ? '—' : (v > 0 ? '+' : '') + (v / 10000).toFixed(2) + '亿') },
];

/* ---------------------------------------------------------------- 个股打分（只读依赖 score 插件）
 *
 * 口径唯一源 = score 插件的 `/api/p/score/map`（`{code6: [score, grade, rank]}`）。
 * 本文件只做「拉一次 + 按 6 位代码查表」，不重算、不实现第二套匹配逻辑。
 * score 插件被删掉 → 该端点 404 → 这里降级成「无打分数据」：表格照常渲染、单元格「—」，
 * 不抛错、不弹窗（拆卸演练要求）。
 */
const SCORE = { state: 'idle', map: {}, asof: null, stale: false, reason: '', promise: null };

function ensureScore(api) {
  if (SCORE.promise) return SCORE.promise;
  SCORE.promise = api.get('/api/p/score/map').then((j) => {
    if (!j || !j.available || !j.map) {
      SCORE.state = 'none';
      SCORE.map = {};
      SCORE.reason = (j && j.reason) || '打分数据不可用';
      return SCORE;
    }
    SCORE.state = 'ok';
    SCORE.map = j.map;
    SCORE.asof = j.asof || null;
    SCORE.stale = !!j.stale;
    return SCORE;
  }).catch((err) => {
    SCORE.state = 'none';
    SCORE.map = {};
    SCORE.reason = (err && err.status === 404)
      ? '未装载 score 插件（/api/p/score/map 404）'
      : '打分读取失败：' + ((err && err.message) || err);
    return SCORE;
  });
  return SCORE.promise;
}

const code6Of = (code) => String(code == null ? '' : code).split('.')[0];

/** 把打分挂到成分股行上（`_ds` 分数 / `_dg` 评级），供表格与排序共用一份值。 */
function attachScore(rows) {
  for (const r of (rows || [])) {
    const hit = SCORE.map[code6Of(r.stock_code)];
    r._ds = (hit && hit[0] != null) ? hit[0] : null;      // 不在打分池 → null → 显示「—」，不写 0
    r._dg = (hit && hit[1]) || null;
  }
}

/** 数据时效文案（列头/单元格悬浮提示 + 表头角标共用一份，避免两处口径不一致）。 */
function scoreCaliberTip() {
  if (SCORE.state !== 'ok') return '无打分数据' + (SCORE.reason ? '：' + SCORE.reason : '');
  return '打分 ' + (SCORE.asof || '—') + ' 收盘口径（日频，盘中不更新）' + (SCORE.stale ? '，产物偏旧建议重跑' : '');
}

function scoreNoteHtml() {
  if (SCORE.state === 'ok') {
    return '<span class="card-sub-score' + (SCORE.stale ? ' is-stale' : '') + '" title="'
      + esc(scoreCaliberTip()) + '">打分 ' + esc(SCORE.asof || '—') + ' 收盘口径'
      + (SCORE.stale ? '（偏旧）' : '') + '</span>';
  }
  if (SCORE.state === 'none') {
    return '<span class="card-sub-score is-off" title="' + esc(scoreCaliberTip()) + '">无打分数据</span>';
  }
  return '';
}

/* ---------------------------------------------------------------- 走势图内层（8002 逐字） */

/** 活跃市值 + MA10，带子表示多空（8002 `sparkInner` app.js:1899 逐字）。
 *
 * 2026-11-01（主人：宽栅里图只占 320px，两边空着）：除了 H=96 固定外，宽度改为入参。
 * 它只是绘图局部坐标，不影响线条/文字比例；viewBox 跟着同步（`0 0 W 96`）。
 * 不传时仍为旧的 320（弹窗外的旧调用不受影响）。
 */
function sparkInner(vals, mas, W0) {
  const clean = vals.filter((v) => v != null);
  if (!clean.length) return null;
  const W = Math.max(120, Math.round(W0 || 320)), H = 96, padL = 6, padR = 6, padT = 12, padB = 14;
  const lo = Math.min(...clean, ...mas.filter((v) => v != null));
  const hi = Math.max(...clean, ...mas.filter((v) => v != null));
  const span = hi - lo || 1;
  const n = vals.length;
  const x = (i) => padL + (W - padL - padR) * (n <= 1 ? 0 : i / (n - 1));
  const y = (v) => H - padB - (H - padT - padB) * ((v - lo) / span);
  const pathOf = (arr) => {
    let p = '', on = false;
    arr.forEach((v, i) => {
      if (v == null) { on = false; return; }
      p += (on ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      on = true;
    });
    return p;
  };
  const pMa = pathOf(mas);
  // 「V3 单色线 + 多空带」：只填现线与 MA10 之间那块，交叉处线性插值收腰（无硬接缝）
  const nodes = [];
  for (let i = 0; i < n; i++) {
    const v = vals[i], m = mas[i];
    nodes.push((v == null || m == null) ? null : [x(i), y(v), y(m), v >= m]);
  }
  const blocks = [];
  let cur = [];
  nodes.forEach((nd) => {
    if (nd == null) { if (cur.length) blocks.push(cur); cur = []; } else { cur.push(nd); }
  });
  if (cur.length) blocks.push(cur);
  const crossPt = (a, b) => {
    const d0 = a[1] - a[2], d1 = b[1] - b[2];
    const t = d0 / ((d0 - d1) || 1e-9);
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  };
  let pBand = '';
  blocks.forEach((blk) => {
    let i = 0;
    while (i < blk.length) {
      const s = blk[i][3];
      let j = i;
      while (j + 1 < blk.length && blk[j + 1][3] === s) j += 1;
      const poly = [];
      if (i > 0) poly.push(crossPt(blk[i - 1], blk[i]));
      for (let k = i; k <= j; k++) poly.push(blk[k]);
      if (j + 1 < blk.length) poly.push(crossPt(blk[j], blk[j + 1]));
      if (poly.length >= 2) {
        const up = poly.map((p) => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('L');
        const dn = poly.slice().reverse().map((p) => p[0].toFixed(1) + ' ' + p[2].toFixed(1)).join('L');
        pBand += '<path class="spark-band" d="M' + up + 'L' + dn + 'Z" fill="' + (s ? 'var(--up-solid)' : 'var(--down-solid)') + '"/>';
      }
      i = j + 1;
    }
  });
  const pLine = pathOf(vals);
  const lastV = vals[n - 1];
  const lastM = mas[n - 1];
  const lastUp = (lastV != null && lastM != null) ? (lastV >= lastM) : null;
  const bias = (lastV == null || lastM == null || !lastM) ? null : (lastV / lastM - 1) * 100;
  const biasTxt = bias == null ? '' : (bias >= 0 ? '多头 +' : '空头 ') + bias.toFixed(1) + '%';
  return pBand
    + '<path d="' + pLine + '" fill="none" stroke="var(--fg-strong)" stroke-width="1.4" stroke-linejoin="round"/>'
    + (pMa ? '<path d="' + pMa + '" fill="none" stroke="var(--warn-solid)" stroke-width="1.1" stroke-dasharray="3 2"/>' : '')
    + (lastV == null ? '' : '<circle cx="' + x(n - 1).toFixed(1) + '" cy="' + y(lastV).toFixed(1)
      + '" r="2.6" fill="' + (lastUp === false ? 'var(--down-solid)' : 'var(--up-solid)') + '"/>')
    + '<text x="' + padL + '" y="' + (padT - 3) + '" font-size="10" fill="var(--dim)" font-family="var(--font-mono)">' + hi.toFixed(0) + ' 亿</text>'
    + '<text x="' + padL + '" y="' + (H - 3) + '" font-size="10" fill="var(--dim)" font-family="var(--font-mono)">' + lo.toFixed(0) + ' 亿</text>'
    + (biasTxt ? '<text x="' + (W - padR) + '" y="' + (padT - 3) + '" text-anchor="end" font-size="10" font-family="var(--font-mono)" fill="' + (bias >= 0 ? 'var(--up-solid)' : 'var(--down-solid)') + '">' + biasTxt + '</text>' : '');
}

/** 板块指数收盘 + 黄白线，红/绿带表示站上/跌下两线（8002 `pxZoneInner` app.js:1829 逐字）。
 * 宽度口径同 `sparkInner`（2026-11-01：宽度改为入参，viewBox 跟着走）。 */
function pxZoneInner(close, yellow, white, W0) {
  const W = Math.max(120, Math.round(W0 || 320)), H = 96, padL = 6, padR = 6, padT = 12, padB = 14;
  const all = [].concat(close, yellow, white).filter((v) => v != null && isFinite(v));
  if (all.length < 6) return null;
  const lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
  const span = (hi - lo) || 1;
  const n = close.length;
  const x = (i) => padL + (W - padL - padR) * (n <= 1 ? 0 : i / (n - 1));
  const y = (v) => H - padB - (H - padT - padB) * ((v - lo) / span);
  const at = (arr, i) => (arr && arr[i] != null && isFinite(arr[i])) ? arr[i] : null;
  const up = [], dn = [];
  for (let i = 0; i < n; i++) {
    const a = at(yellow, i), b = at(white, i);
    if (a == null || b == null) { up.push(null); dn.push(null); continue; }
    up.push(Math.max(a, b));
    dn.push(Math.min(a, b));
  }
  function zone(ref, mode) {
    let out = '';
    const ok = (k) => at(close, k) != null && ref[k] != null
      && (mode > 0 ? close[k] > ref[k] : close[k] < ref[k]);
    const crossP = (k0, k1) => {
      const d0 = close[k0] - ref[k0], d1 = close[k1] - ref[k1];
      const t = d0 / ((d0 - d1) || 1e-9);
      return [x(k0) + (x(k1) - x(k0)) * t, y(close[k0] + (close[k1] - close[k0]) * t)];
    };
    for (let i = 0; i < n;) {
      if (!ok(i)) { i++; continue; }
      let j = i;
      while (j + 1 < n && ok(j + 1)) j++;
      const A = [], B = [];
      if (i > 0 && at(close, i - 1) != null && ref[i - 1] != null) {
        const p = crossP(i - 1, i); A.push(p); B.push(p);
      }
      for (let k = i; k <= j; k++) { A.push([x(k), y(close[k])]); B.push([x(k), y(ref[k])]); }
      if (j + 1 < n && at(close, j + 1) != null && ref[j + 1] != null) {
        const p = crossP(j, j + 1); A.push(p); B.push(p);
      }
      if (A.length >= 2) {
        const pa = A.map((q) => q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join('L');
        const pb = B.slice().reverse().map((q) => q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join('L');
        out += '<path d="M' + pa + 'L' + pb + 'Z"/>';
      }
      i = j + 1;
    }
    return out;
  }
  const line = (arr, color, w) => {
    let p = '', on = false;
    for (let i = 0; i < n; i++) {
      const v = arr[i];
      if (v == null || !isFinite(v)) { on = false; continue; }
      p += (on ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      on = true;
    }
    return p ? '<path d="' + p + '" fill="none" stroke="' + color + '" stroke-width="' + w + '"/>' : '';
  };
  const lab = (t, ty) => '<text x="' + padL + '" y="' + ty + '" font-size="10" fill="var(--dim)"'
    + ' font-family="var(--font-mono)">' + t + '</text>';
  return '<g class="pz-r">' + zone(up, 1) + '</g><g class="pz-g">' + zone(dn, -1) + '</g>'
    + line(close, 'var(--fg)', 1.5) + line(yellow, 'var(--warn-solid)', 1.1) + line(white, 'var(--dim)', 1.1)
    + lab(hi.toFixed(2), padT - 3) + lab(lo.toFixed(2), H - 3);
}

/** 板块内名次悬停（8002 `secRankTip` app.js:1517 逐字，色格图共用 heatGrid）。 */
function secRankTip(cell) {
  const tra = (cell.getAttribute('data-trail') || '').split(',').filter(Boolean);
  const total = Number(cell.getAttribute('data-total') || 0) || 1;
  const period = Number(cell.getAttribute('data-period') || 1);
  if (!tra.length) return '暂无轨迹';
  const cells = tra.slice().reverse().map((t) => {
    const [ds, rk] = t.split('#');
    return { ds: ds, txt: rk, p: Number(rk) / Math.max(total, 1) };
  });
  return heatGrid('近 ' + tra.length + ' 交易日 · 板块内名次（' + period + '日权重变化）', cells,
    '格子=当日板块内排名（左→右由上到下）· 红=靠前 · 共 ' + total + ' 只');
}

/* ---------------------------------------------------------------- 小部件（8002 逐字） */

function boardTag(code) {
  const c = String(code == null ? '' : code);
  if (/^(688|689)/.test(c)) return '<span class="bd" title="科创板 ' + c + '（非主板）">科</span>';
  if (/^(300|301)/.test(c)) return '<span class="bd" title="创业板 ' + c + '（非主板）">创</span>';
  if (/^(43|83|87|88|92)/.test(c)) return '<span class="bd" title="北交所 ' + c + '（非主板）">北</span>';
  return '';
}

/** 结构徽标（2026-10-27 统一）：**与板块列表同一套**，不再单独实现。
 * 数据源：后端的 `st` 字段（逐股跑 amv_contrib.parts → 同一个 classify）。
 * 样式来自 board.js 的 STG_CSS（.sector-modal .stg），此处只管拼 DOM。
 */
function stgBadge(st) {
  if (!st || !st.label) return '';
  return '<span class="stg dir-' + (st.dir || 'flat') + ' t-' + (st.tag || '') + '" title="'
    + esc(stgTipText(st)) + '">' + esc(st.label) + '</span>';
}

function stgTipText(st) {
  const f = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2));
  const HINT = {
    '齐升': 'AMV 与价格同向上涨',
    '虚涨': 'AMV 在涨但价格在跌——换手抬起来的',
    '独涨': '价格在涨但 AMV 在缩——换手在退',
    '齐跌': 'AMV 与价格同向下跌',
  };
  return (st.label || '') + (HINT[st.tag] ? '：' + HINT[st.tag] : '')
    + '（AMV ' + f(st.amv_pct) + '%　价格 ' + f(st.px_pct) + '%　差额 ' + f(st.gap) + 'pp）';
}

function sigBadges(sigs) {
  if (!sigs || !sigs.length) return '';
  return sigs.map((s) => {
    const m = SIG_META[s];
    if (!m) return '';
    return '<span class="sg sg-' + m.c + '" title="' + esc(m.d) + '">' + m.t + '</span>';
  }).join('');
}

/** 排序取值：打分挂在 `_ds` 上（attachScore），列 key 叫 'dscore'。
 * 2026-10-30（主人标注 55「打分排序是乱的」）：原实现直接 a[col.k]，dscore 取到 undefined
 * → 两侧都是缺值 → 比较恒 0，点表头等于没排。 */
function sortValue(row, k) {
  if (k === 'dscore') return row._ds == null ? null : row._ds;
  return row[k];
}

/** 个股行排序（8002 `cardSortRows` app.js:1287 逐字）。 */
function cardSortRows(rows, sortKey, dir) {
  const col = CARD_COLS.find((c) => c.k === sortKey) || CARD_COLS.find((c) => c.k === 'd_contrib') || CARD_COLS[0];
  return rows.slice().sort((a, b) => {
    if (col.k === 'name') {
      const sa = (a.sigs && a.sigs.length) ? 1 : 0;
      const sb = (b.sigs && b.sigs.length) ? 1 : 0;
      if (sa !== sb) return dir * (sb - sa);
      return (a.rank || 0) - (b.rank || 0);
    }
    const va = sortValue(a, col.k), vb = sortValue(b, col.k);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;          // 缺值恒排最后（不参与方向）
    if (vb == null) return -1;
    return dir * (vb - va);            // dir=1 → 值大在前
  });
}

/** 打分单元格：分数 1 位小数 + 评级色字；不在打分池 → 「—」。 */
function dsCell(r) {
  if (r._ds == null) {
    return '<td class="num ds-score" title="不在打分池（score 产物里没有这只票）">—</td>';
  }
  const g = r._dg;
  const tip = '个股打分 ' + Number(r._ds).toFixed(1) + (g ? ' · 评级 ' + g : '') + ' · ' + scoreCaliberTip();
  return '<td class="num ds-score" title="' + esc(tip) + '"><b>' + Number(r._ds).toFixed(1) + '</b>'
    + (g ? '<i class="ds-g g-' + esc(g) + '">' + esc(g) + '</i>' : '') + '</td>';
}

/* ---------------------------------------------------------------- 表格渲染（cardHtml 右侧那一半） */

function buildThead(state) {
  return CARD_COLS.map((c) => {
    const lab = (c.k === 'name') ? '<span class="dkslot"></span>' + c.t : c.t;
    if (!c.sortable) return '<th>' + lab + '</th>';
    const on = state.sortKey === c.k;
    return '<th class="sortable' + (on ? ' on' : '') + '" data-k="' + c.k
      + '" role="button" tabindex="0" title="' + esc(c.tip || ('点此按「' + c.t + '」排序')) + '">' + lab
      + (on ? (state.dir === 1 ? ' ▾' : ' ▴') : '') + '</th>';
  }).join('');
}

function buildTbody(rows, state, winN, memErr) {
  const sorted = cardSortRows(rows, state.sortKey, state.dir);
  if (!sorted.length) {
    return '<tr><td colspan="' + CARD_COLS.length + '" class="rb-empty">'
      + (memErr ? '成分股加载失败：' + esc(memErr) : '无成分股明细') + '</td></tr>';
  }
  return sorted.map((r, ri) => '<tr>' + CARD_COLS.map((c) => {
    if (c.k === 'seq') return '<td class="seq">' + (ri + 1) + '</td>';
    if (c.k === 'name') {
      const dkT = '双线制四态：金叉(MA10>白线) 价格在两线之上=多｜夹两线中间=偏多｜跌破两线=空；'
        + '死叉(白线>MA10) 价格在两线之下=空｜夹两线中间=偏空｜站上两线=多。白线=活跃市值 EMA(EMA(amv,10),10)';
      const dk = r.bull === '多' ? '<span class="dk bull" title="' + dkT + '">多</span>'
        : r.bull === '偏多' ? '<span class="dk bull-half" title="' + dkT + '">多</span>'
          : r.bull === '空' ? '<span class="dk bear" title="' + dkT + '">空</span>'
            : r.bull === '偏空' ? '<span class="dk bear-half" title="' + dkT + '">空</span>'
              : '<span class="dk dk-empty"></span>';   /* 空槽也占位 ⇒ 各行列名对齐 */
      const nm = '<span class="stk-click" data-open-kline="' + esc(r.stock_code)
        + '" data-open-name="' + esc(r.stock_name || '') + '">' + esc(r.stock_name || r.stock_code) + '</span>';
      return '<td title="' + esc(r.stock_code) + '">' + dk + nm + stgBadge(r.st) + boardTag(r.stock_code) + sigBadges(r.sigs) + '</td>';
    }
    const v = r[c.k];
    if (c.k === 'dscore') return dsCell(r);
    if (c.k === 'score') {
      return '<td class="num score-cell" data-code="' + esc(r.stock_code) + '"'
        + ' title="悬停看近 30 日评分轨迹">' + c.fmt(v, r) + '</td>';
    }
    if (c.k === 'sec_rank') {
      const tra = (r.trail || []).map((x) => String(x[0]).slice(5).replace('-', '') + '#' + x[1]).join(',');
      return '<td class="num sec-rank" data-trail="' + esc(tra) + '" data-total="' + (r.sec_total || 0)
        + '" data-period="' + winN + '">' + c.fmt(v, r) + '</td>';
    }
    return '<td class="num ' + (c.cls ? c.cls(v) : '') + '">' + c.fmt(v, r) + '</td>';
  }).join('') + '</tr>').join('');
}

/* ---------------------------------------------------------------- 详情卡 */

/**
 * 打开详情卡。
 *
 * @param {object} o
 * @param {object} o.api       ctx.api（get 已带 /api/p/sector 前缀；/api/ 开头走绝对路径）
 * @param {string} o.code / o.name / o.level
 * @param {number} o.winN      周期（1/3/5/10/20）
 * @param {string} o.date      当前看盘日（YYYY-MM-DD），只作标注
 * @param {function} o.onPeriod (n) => void   切周期（等于板块页顶部周期按钮）
 * @param {function} o.tipShow / o.tipHide    悬停浮标（复用板块页那一个，禁两套）
 */
export function openDetail(o) {
  const { code, name, winN, date } = o;
  const prev = document.getElementById('sector-modal');
  if (prev) prev.remove();

  const mask = el('div', { class: 'sector-modal', id: 'sector-modal' });
  const box = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': (name || code) + ' 板块详情' });
  mask.append(box);
  document.body.append(mask);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    mask.remove();
    if (ro) ro.disconnect();
    document.removeEventListener('keydown', escHandler);
    const ov = window.__v2Overlay;
    if (ov && ov.pop) ov.pop('sector');
  };
  const escHandler = (ev) => {
    if (ev.key !== 'Escape') return;
    // 浮层栈（2026-10-28）：K 线弹窗可能叠在本详情之上，Esc 只关最上层。
    // 上面的 K 线自己会 stopImmediatePropagation，这里再判一次 top，防注册顺序变化。
    const ov = window.__v2Overlay;
    if (ov && ov.top && ov.top() !== 'sector') return;
    close();
  };
  document.addEventListener('keydown', escHandler);
  mask.addEventListener('click', (ev) => { if (ev.target === mask) close(); });
  const ov = window.__v2Overlay;
  if (ov && ov.push) ov.push('sector');

  box.innerHTML = '<div class="card-hd"><span class="card-nm">' + esc(name || code)
    + '</span><button class="card-x" aria-label="关闭">✕</button></div><div class="loading">加载中…</div>';
  box.querySelector('.card-x').addEventListener('click', close);

  // 弹窗宽度变化（窗口缩放）→ 两张走势图按新宽度重画（paintCard 里落的 __fitSparks）
  let ro = null;
  if (typeof ResizeObserver !== 'undefined') {
    let raf = 0;
    ro = new ResizeObserver(() => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; if (box.__fitSparks) box.__fitSparks(); });
    });
    ro.observe(box);
  }

  // 2026-10-30（主人标注 53「板块详情内切换显示周期时，就只切换详情内的周期」）：
  //   原来点周期 = close() + onPeriod(n) → 板块页全局周期跟着变、整页重算，关掉详情后回不去。
  //   现在详情自己持一份 winN：点周期只重拉本卡的 /series /members /contrib 再重画，不关窗、不动全局。
  const st = { winN: winN };
  const oi = Object.assign({}, o, { winN: winN });
  const reloadIn = (n) => {
    if (!n || n === st.winN) return;
    st.winN = n; oi.winN = n;
    delete box.dataset.bound;      // innerHTML 重建 → 卡头按钮是新节点，必须允许重绑
    box.innerHTML = '<div class="loading">加载中…</div>';
    fetchDetail(o.api, oi).then(([sr2, mem2, ct2, kl2]) => {
      if (closed || !mask.isConnected) return;
      attachScore((mem2 && mem2.rows) || []);
      paintCard(box, oi, sr2, mem2, ct2, kl2, { sortKey: 'd_contrib', dir: 1 }, close);
    });
  };
  oi.onPeriodIn = reloadIn;

  fetchDetail(o.api, oi).then(([sr, mem, ct, kl]) => {
    if (closed || !mask.isConnected) return;
    attachScore((mem && mem.rows) || []);
    paintCard(box, oi, sr, mem, ct, kl, { sortKey: 'd_contrib', dir: 1 }, close);
  });

  return { close };
}

/** 四个只读端点一次取齐（弹卡与内联详情共用，禁两套取数）。 */
function fetchDetail(api, o) {
  const { code, winN, date } = o;
  const q = (p) => api.get(p).catch((err) => ({ __err: String((err && err.message) || err) }));
  // 全A 没有交易所指数码，全站全A 一律用通达信 **880008（全A等权）**：
  //   实时卡指数别名 AMV_CODE_ALIAS「880008.SH → ALL」、复盘指数卡 INDEX_CARDS、
  //   state_tree 形态 K 线（sh880008）、盘中卡恐慌图叠的全A 日涨跌。
  // 2026-11-01 主人：「全A 跟实时页面用一样的口径，通达信 8800XX」——所以收盘价图也走 880008，
  // 不再直接短路。注意它只供「收盘价 + 黄白线」这一张图，本卡的活跃市值/占比重口径不变。
  const klCode = code === 'ALL' ? '880008.SH' : code;
  const kl = q('/api/kline/' + encodeURIComponent(klCode) + '?limit=60');
  return Promise.all([
    q('/series?code=' + encodeURIComponent(code) + '&days=60&period=' + winN),
    q('/members/' + encodeURIComponent(code) + '?full=1&period=' + winN),
    q('/contrib?code=' + encodeURIComponent(code) + '&period=' + winN + (date ? '&date=' + encodeURIComponent(date) : '')),
    kl,
    ensureScore(api),          // 永不会 reject：score 插件缺失也只降级成「无打分数据」
  ]);
}

/**
 * 内联详情（照 8002 `renderIdxDetail`：指数层右栏就地把详情画进 `.list li.idx-detail`）。
 *
 * 复用同一个 `paintCard` / `bindCard`（禁两套渲染）：宿主 `<li class="idx-detail">` 里放一个
 * `.sector-modal.idx-inline > .modal`，CSS 把浮层样式改回静态块（见 legacy.css.js 的 .idx-inline），
 * 于是 `.sector-modal .card-*` 那整批规则原样生效，不需要再写一份卡内样式。
 *
 * @param {HTMLElement} host  <li class="idx-detail">
 * @param {object} o          同 openDetail（api/code/name/level/winN/date/onPeriod/tipShow/tipHide/onRemount）
 */
export function mountDetail(host, o) {
  if (!host) return { destroy() {} };
  host.textContent = '';
  const mask = el('div', { class: 'sector-modal idx-inline' });
  const box = el('div', { class: 'modal', role: 'region', 'aria-label': (o.name || o.code) + ' 指数详情' });
  mask.append(box);
  host.append(mask);
  box.innerHTML = '<div class="loading">加载中…</div>';

  let dead = false;
  const noop = () => {};
  // 栏宽变化（右栏宽度、窗口缩放）→ 两张走势图按新宽度重画；不重画表格（500 行重建很贵）
  let ro = null;
  if (typeof ResizeObserver !== 'undefined') {
    let raf = 0;
    ro = new ResizeObserver(() => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; if (box.__fitSparks) box.__fitSparks(); });
    });
    ro.observe(box);
  }
  fetchDetail(o.api, o).then(([sr, mem, ct, kl]) => {
    if (dead || !box.isConnected) return;
    attachScore((mem && mem.rows) || []);
    paintCard(box, o, sr, mem, ct, kl, { sortKey: 'd_contrib', dir: 1 }, noop);
    // 内联场景「↻ 重算」= 原地重挂（弹卡版是关掉再开一张）；✕ 由 CSS 隐藏。
    const rf = box.querySelector('.card-refresh');
    if (rf && o.onRemount) {
      const clone = rf.cloneNode(true);
      rf.replaceWith(clone);
      clone.addEventListener('click', (ev) => { ev.stopPropagation(); o.onRemount(); });
    }
  });

  return { destroy() { dead = true; if (ro) ro.disconnect(); mask.remove(); } };
}

/** 画整张卡（8002 `cardHtml` 的 DOM 结构逐字）。 */
function paintCard(box, o, sr, mem, ct, kl, state, close) {
  const { code, name, level, winN, date } = o;
  const srok = sr && !sr.__err && !sr.error;

  // 走势：8002 只画 want = dates.slice(show_start, dayB+1)；这里取最近 60 日全段
  const sdates = (srok && sr.dates) ? sr.dates : [];
  const amv = sdates.map((d, i) => (sr.amv && sr.amv[i] != null ? sr.amv[i] / SCALE_A : null));
  const ma = sdates.map((d, i) => (sr.ma && sr.ma[i] != null ? sr.ma[i] / SCALE_A : null));
  const inner = (srok && amv.length) ? sparkInner(amv, ma) : null;

  // 板块指数收盘 + 黄白线（8002 走 /api/board_kline；v2 用兼容层 /api/kline，字段在 bar 上）
  const kb = (kl && !kl.__err && Array.isArray(kl.bars)) ? kl.bars : [];
  const kpos = new Map(kb.map((b) => [b.t, b]));
  const kd = (d) => String(d).replace(/-/g, '');
  const pick = (f) => sdates.map((d) => {
    const b = kpos.get(kd(d));
    return (b && b[f] != null) ? b[f] : null;
  });
  const pxInner = sdates.length ? pxZoneInner(pick('c'), pick('yellow'), pick('white')) : null;

  // 板块数据四格（8002：活跃市值(均) 取最近 winN+1 日均值）
  const nAmv = Math.min((srok ? (sr.amv || []).length : 0), winN + 1);
  const lastAmv = nAmv > 0
    ? ((sr.amv.slice(-nAmv).reduce((a, b) => a + b, 0) / nAmv) / SCALE_A).toFixed(1) + '亿'
    : '—';
  const cell = (label, val, cls2) => '<div class="pin-cell"><div class="pin-k">' + label
    + '</div><div class="pin-v ' + (cls2 || '') + '">' + val + '</div></div>';

  // 贡献分项（8002 ctInner 逐字）
  const F_CT = (v) => (v == null || isNaN(v)) ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(3) + '‰';
  const ctInner = (() => {
    if (!ct || ct.__err || !ct.ok) {
      return '<div class="rb-empty" style="font-size:11px">' + esc((ct && (ct.reason || ct.__err)) || '无分项数据')
        + '<br>（分项需 stock_state 近 31 日快照，仅支持最近约 30 个交易日）</div>';
    }
    const p = ct.parts || {};
    const items = [
      ['涨跌贡献', p.px, '价格变动：a1·(p1−p0)，按活跃市值加权（p=不复权价）。这一项大 = 涨幅驱动。\n'
        + '注意：与上方「真实涨跌」（板块指数口径，偏等权）不同源；大小票分化时会背离'],
      ['流通股贡献', p.sh, '股本变动（解禁/送转）：p0·a0·(sh1/sh0−1)'],
      ['活跃度贡献', p.act, '换手抬活：p0·a0·(a1/a0 − sh1/sh0)。这一项大 = 换手驱动'],
      ['新股上市', p.new, '当日新入池/首日票的整额 AMV'],
    ];
    if (Math.abs(p.susp || 0) >= 0.01) items.push(['停牌/缺数', p.susp, '当日无成交（v9 语义 contrib=0）']);
    items.push(['大盘基准', p.den, 'Δ占比 换分母的效应：−板块昨收AMV × 全A涨幅。窗口越长此项越大']);
    const mx = Math.max.apply(null, items.map((x) => Math.abs(x[1] || 0)).concat([0.01]));
    const body = items.map((it) => {
      const val = (it[1] == null ? 0 : it[1]);
      const w = Math.min(50, Math.abs(val) / mx * 50);
      const left = val >= 0 ? 50 : 50 - w;
      const dir = val > 0 ? 'up' : (val < 0 ? 'down' : '');
      return '<div class="ctb-row' + (Math.abs(val) < 0.0005 ? ' zero' : '') + '">'
        + '<span class="ctb-k" title="' + esc(it[2] || '') + '">' + it[0] + '</span>'
        + '<span class="ctb-bar"><i class="' + dir + '" style="left:' + left.toFixed(1)
        + '%;width: ' + w.toFixed(1) + '%"></i></span>'
        + '<span class="ctb-v ' + clsNum(val) + '">' + F_CT(val) + '</span></div>';
    }).join('');
    return '<div class="ctb">' + body
      + '<div class="ctb-sum">合计 <b class="' + clsNum(p.total) + '">' + F_CT(p.total) + '</b>'
      + '<span class="ctb-sub">' + esc(String(ct.d0 || '').slice(5)) + '→' + esc(String(ct.d1 || '').slice(5))
      + ' · ' + ct.n + ' 只' + (ct.n_bj ? '（剔北交 ' + ct.n_bj + '）' : '') + '</span></div>'
      + '<div class="ctb-note">AMV 涨幅 <b>' + fr2(ct.amv_pct) + '</b>　价格涨幅 <b>'
      + fr2(ct.px_pct) + '</b>　差额 <b>' + (ct.cmp && ct.cmp.gap != null
        ? ((ct.cmp.gap > 0 ? '+' : '') + Number(ct.cmp.gap).toFixed(2) + 'pp') : '—')
      + '</b>' + (ct.cmp ? stgBadge(ct.cmp) : '') + '</div>'
      + '</div>'
      + structBlock(ct)
      + topBlock(ct);
  })();

  function fr2(v) {
    return v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%';
  }

  /* 结构指标（2026-10-27 补，对齐参考站点「资金结构」）：
   * 价格区间 / 广度 / top1 贡献 / 有效贡献股数。全部由后端按同期窗口算好。 */
  function structBlock(ct) {
    const s = (ct && ct.struct) || {};
    if (!s || s.n_calc == null) return '';
    const fp = (v) => (v == null ? '—' : Number(v).toFixed(1) + '%');
    return '<div class="st-metrics">'
      + '<div><span class="k">广度</span><b>' + fp(s.breadth) + '</b>'
      + '<i class="t">上涨成分股占比</i></div>'
      + '<div><span class="k">top1 贡献</span><b>' + fp(s.top1) + '</b>'
      + '<i class="t">最大单只占正向合计</i></div>'
      + '<div><span class="k">有效贡献股</span><b>' + (s.eff_n == null ? '—' : s.eff_n + ' 只') + '</b>'
      + '<i class="t">1/Σw²，实际出力只数</i></div>'
      + '<div><span class="k">参与计算</span><b>' + s.n_calc + ' 只</b>'
      + '<i class="t">两端都有 AMV 的成分</i></div>'
      + '</div>';
  }

  /* Top 贡献股（2026-10-27 补）：谁在拉这个板块，以及每只是哪类驱动。
   * driver 是逐股近似口径（AMV涨幅 vs 价格涨幅的差），不是板块四档徽标。 */
  function topBlock(ct) {
    const t = (ct && Array.isArray(ct.top)) ? ct.top : [];
    if (!t.length) return '';
    const fp = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(3));
    const fr = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%');
    const rows = t.map((r) => {
      /* Top 贡献股也用同一套徽标（标签文字 + 方向色 + 形状强弱） */
      const stLike = r.driver ? { tag: r.driver, label: r.driver, dir: r.dir || 'flat' } : null;
      return '<div class="tp-row">'
        + '<span class="tp-nm" title="' + esc(r.code) + '">' + esc(r.name) + '</span>'
        + '<span class="tp-d ' + clsNum(r.delta_pp) + '">' + fp(r.delta_pp) + '<i>pp</i></span>'
        + '<span class="tp-p ' + clsNum(r.px_pct) + '">' + fr(r.px_pct) + '</span>'
        + stgBadge(stLike) + '</div>';
    }).join('');
    return '<div class="top-contrib"><div class="tc-hd">Top 贡献股'
      + '<span class="tc-sub">Δpp · 区间价格 · 驱动</span></div>' + rows + '</div>';
  }

  // 成分股
  const rows0 = (mem && !mem.__err && Array.isArray(mem.rows)) ? mem.rows.slice() : [];
  const memTotal = (mem && mem.n_total != null) ? mem.n_total : rows0.length;
  const memErr = (mem && mem.__err) || null;

  const lvCls = 'lv-' + String(level || '').toLowerCase();
  box.innerHTML = '<div class="card-hd">'
    + '<span class="card-nm">' + esc(name || code) + '</span>'
    + '<span class="pin-lv ' + esc(lvCls) + '">' + (LV_TAG[level] || level || '') + '</span>'
    + '<span class="card-sub">' + (date ? '<b>' + esc(date) + '</b> · ' : '')
    + esc(code) + (srok && sr.n_members != null ? ' · ' + sr.n_members + '只' : '') + '</span>'
    + '<span class="card-periods" role="group" aria-label="周期（与板块页周期选择一致）">'
    + [1, 3, 5, 10, 20].map((n) => '<button class="cp-btn' + (winN === n ? ' on' : '') + '" data-wn="' + n
      + '" aria-pressed="' + (winN === n ? 'true' : 'false')
      + '" title="切 ' + n + ' 日周期（等于板块页顶部周期按钮，整列表同步）">' + n + '日</button>').join('')
    + '</span>'
    + '<span class="card-hd-right">'
    + '<button class="card-refresh" title="重算本卡：重新拉一次数据（数据出现整列「—」时点它）">↻</button>'
    + '<button class="card-x" aria-label="关闭">✕</button></span></div>'
    + '<div class="card-body">'
    + '<div class="card-left">'
    + '<div class="card-block">'
    + '<h4>板块走势（活跃市值 + MA' + (sr.ma_n || 10) + '）</h4>'
    + (inner ? '<svg class="spark spark-amv" viewBox="0 0 320 96">' + inner + '</svg>' : '<div class="rb-empty">无走势数据</div>')
    + '<div class="lg-row"><span class="lg lg-amv"></span>活跃市值<span class="lg lg-ma"></span>MA'
    + (sr.ma_n || 10) + ' · 带子<b style="color:var(--up-solid)">红上=多头</b>/<b style="color:var(--down-solid)">绿下=空头</b></div>'
    + '</div>'
    + '<div class="card-block">'
    + '<h4>板块指数收盘 + 黄白线' + (code === 'ALL' ? '（全A等权 · 通达信 880008）' : '') + '</h4>'
    + (pxInner ? '<svg class="spark spark-px" viewBox="0 0 320 96">' + pxInner + '</svg>' : '<div class="rb-empty">无收盘价数据</div>')
    + '<div class="lg-row"><span class="lg lg-close"></span>收盘<span class="lg lg-yellow"></span>黄线<span class="lg lg-white"></span>白线'
    + '<span>｜带子：<b style="color:var(--up-solid)">红=站上两线（多）</b>·<b style="color:var(--down-solid)">绿=跌下两线（空）</b></span></div>'
    + '</div>'
    + '<div class="card-block">'
    + '<h4>板块数据（' + winN + ' 日）</h4>'
    + '<div class="pin-cells">'
    + cell('活跃市值(均)', lastAmv)
    + cell('区间涨跌(活跃)', srok && sr.amv_pct != null ? fmtPctNum(sr.amv_pct) : '—', clsNum(srok ? sr.amv_pct : null))
    + cell('真实涨跌', srok && sr.real_pct != null ? fmtPctNum(sr.real_pct) : '—', clsNum(srok ? sr.real_pct : null))
    + cell('主力净流入', srok && sr.flow_main != null ? (sr.flow_main > 0 ? '+' : '') + (sr.flow_main / 10000).toFixed(2) + '亿' : '—',
      clsNum(srok ? sr.flow_main : null))
    + '</div></div>'
    + '<div class="card-block">'
    + '<h4>活跃市值贡献分项（' + winN + ' 日 · ‰）</h4>' + ctInner
    + '</div></div>'
    + '<div class="card-right card-block">'
    + '<h4>成分股（全 ' + memTotal + ' 只 · 按活跃市值排名，点列头排序）' + scoreNoteHtml() + '</h4>'
    + '<table class="rb-tbl card-tbl"><thead><tr>' + buildThead(state) + '</tr></thead><tbody>'
    + buildTbody(rows0, state, winN, memErr) + '</tbody></table>'
    + '</div></div>';

  // 2026-11-01（主人：宽栏里图只占中间 320px，两边空着）：SVG 按实际栏宽重画。
  // viewBox 跟着像素走（320×96 → W×96），线条/字号保持 1:1 —— 不用 preserveAspectRatio=none
  // 拉伸（会把字撑宽），也不靠等比放大（会把 10px 标签放到 17px）。
  // 栏宽变了就重画（ResizeObserver 在挂载处绑定，调的是本函数）。
  const fitSparks = () => {
    const fit = (sel, build) => {
      const s = box.querySelector(sel);
      if (!s) return;
      const w = Math.round(s.getBoundingClientRect().width || 0);
      const W = Math.max(320, w || 0);
      if (String(s.dataset.w) === String(W)) return;
      s.dataset.w = String(W);
      s.setAttribute('viewBox', '0 0 ' + W + ' 96');
      s.innerHTML = build(W) || '';
    };
    fit('svg.spark-amv', (W) => sparkInner(amv, ma, W));
    fit('svg.spark-px', (W) => pxZoneInner(pick('c'), pick('yellow'), pick('white'), W));
  };
  fitSparks();
  box.__fitSparks = fitSparks;

  bindCard(box, o, rows0, state, close, api_placeholder(o), memErr);
}

/* api 从 o 取（paintCard 的签名保持 8002 形态，不额外传第 8 个参数） */
function api_placeholder(o) { return o.api; }

/**
 * 卡片事件绑定（8002 `bindCard`）：卡头按钮只绑一次，表头与悬停列每次重画后重绑。
 */
function bindCard(box, o, rows, state, close, api, memErr) {
  const { winN, tipShow, tipHide, onPeriod } = o;

  if (box.dataset.bound !== '1') {
    box.dataset.bound = '1';

    const x = box.querySelector('.card-x');
    if (x) x.addEventListener('click', close);

    // 右上角「↻ 重算」：重新拉一次（等价于重开本卡）
    const rf = box.querySelector('.card-refresh');
    if (rf) rf.addEventListener('click', (ev) => {
      ev.stopPropagation();
      close();
      openDetail(Object.assign({}, o));
    });

    // 标题栏内直接切周期（2026-10-30 标注 53）：详情自己有周期时只重算详情；
    //   没有（内联指数详情等旧入口）才退回「关窗 + 改全局周期」。
    box.querySelectorAll('.cp-btn').forEach((b) => {
      b.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const n = Number(b.dataset.wn);
        if (o.onPeriodIn) { o.onPeriodIn(n); return; }
        close();
        if (onPeriod) onPeriod(n);
      });
    });
  }

  bindTable(box, o, rows, state, close, api, memErr);
}

/** 表头排序 + 两个悬停列（sec-rank / score-cell）；每次重画 tbody/thead 后必须重绑。 */
function bindTable(box, o, rows, state, close, api, memErr) {
  const { winN, tipShow, tipHide } = o;

  // 板块内名次悬停：近 30 日名次色格图（与「在榜 / 评分」共用 heatGrid）
  box.querySelectorAll('.sec-rank').forEach((td) => {
    td.addEventListener('mouseenter', (ev) => {
      const html = secRankTip(td);
      if (!html || !tipShow) return;
      tipShow(html, ev, false, true);
    });
    td.addEventListener('mousemove', (ev) => tipShow && tipShow(null, ev, true));
    td.addEventListener('mouseleave', () => tipHide && tipHide());
  });

  // 评分列悬停：轨迹懒取（首次约 2 秒），拿到前先显示「计算中」
  const scells = Array.from(box.querySelectorAll('.score-cell'));
  if (scells.length && tipShow) {
    const dsel = o.date || '';
    let cache = null;
    let inflight = null;
    const ensure = () => {
      if (cache) return Promise.resolve(cache);
      if (!inflight) {
        inflight = api.get('/score_trail?code=' + encodeURIComponent(o.code)
          + '&period=' + winN + (dsel ? '&date=' + encodeURIComponent(dsel) : ''))
          .then((j) => {
            const map = {};
            for (const r of ((j && j.rows) || [])) map[r.code] = r.trail || [];
            cache = map;
            return map;
          })
          .catch(() => { cache = {}; return {}; });
      }
      return inflight;
    };
    ensure();                                   // 后台预取
    let hoverCode = null;
    scells.forEach((td) => {
      td.addEventListener('mouseenter', (ev) => {
        hoverCode = td.dataset.code;
        if (cache && Object.keys(cache).length) {
          tipShow(scoreTipCache(cache, td.dataset.code), ev, false, true);
          return;
        }
        tipShow('<div class="hm-hd">评分轨迹计算中…（首次约 2 秒）</div>', ev, false, true);
        ensure().then((map) => {
          if (hoverCode !== td.dataset.code) return;
          tipShow(scoreTipCache(map || {}, td.dataset.code), ev, false, true);
        });
      });
      td.addEventListener('mousemove', (ev) => tipShow(null, ev, true));
      td.addEventListener('mouseleave', () => { hoverCode = null; tipHide && tipHide(); });
    });
  }

  // 列头排序（与 8002 同一套：同列再点翻转，异列取该列默认方向）
  box.querySelectorAll('.card-tbl th.sortable').forEach((h) => {
    h.addEventListener('click', () => {
      const k = h.dataset.k;
      if (state.sortKey === k) state.dir = -state.dir;
      else {
        state.sortKey = k;
        state.dir = (CARD_COLS.find((c) => c.k === k) || {}).dir || 1;
      }
      repaint(box, o, rows, state, close, api, memErr);
    });
  });
}

/** 评分轨迹 → 色格图（8002 `scoreTip`，从 board.js 借 heatGrid）。 */
function scoreTipCache(map, code) {
  const rows = map[code];
  if (!rows || !rows.length) return '暂无轨迹';
  const cells = rows.map((t) => {
    const v = Number(t[1]);
    return { ds: String(t[0]).slice(5).replace('-', ''), txt: v.toFixed(0), p: 1 - v / 100 };
  });
  const vs = rows.map((t) => Number(t[1]));
  return heatGrid('近 ' + rows.length + ' 交易日 · 评分（0-100）', cells,
    '格子=当日评分（左→右由上到下）· 红=高分 · 最低 ' + Math.min.apply(null, vs).toFixed(1)
    + ' / 最高 ' + Math.max.apply(null, vs).toFixed(1));
}

/** 只重画表格（排序切换），不重新取数。 */
function repaint(box, o, rows, state, close, api, memErr) {
  const { winN } = o;
  const tbody = box.querySelector('.card-tbl tbody');
  const thead = box.querySelector('.card-tbl thead tr');
  if (!tbody || !thead) return;
  thead.innerHTML = buildThead(state);
  tbody.innerHTML = buildTbody(rows, state, winN, memErr);
  // 重绑（表头 + 两个悬停列），换表后旧监听随节点一起丢弃
  bindCard(box, o, rows, state, close, api, memErr);
}
