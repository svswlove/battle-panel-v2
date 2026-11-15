/**
 * intraday 卡 · 板块轮动·当天（照旧页搬，20 个函数）
 *
 * 行为基准 = engine/web/index.html:4751-4905 + 5025-5106：
 *   stSet / _stPaint / _sectorTrackHtml / _stGridHtml / _stQuadHtml
 *   _stHitCode / _stBindQuad / _stApplyHl / _stTipEl / _stTipShow / _stTipHide / _stTipMove
 *   切片与口径（_stSnaps/_stLatest/_stVal/_stSeries/_stChgVal/_stUnit/_stLabel）→ caliber.js
 *
 * 与旧页的三处结构性差别（都是"整页 → 卡片"的必然结果，语义没动）：
 *
 * ① **一次性布尔改了位置**。旧页 `_stBindQuad` 用模块级 `let _stDelegated=false`，
 *    因为 `#panel-intraday` 每 3s 被重建，必须靠它防重复绑。卡片里宿主是 mount 时创建的
 *    新节点、`_stPaint` 只重建 `.st-body` 的 innerHTML，委托绑在**不重建的** `.it-card` 上，
 *    所以"绑一次"由「mount 一次」天然保证 —— 不再需要模块级布尔。
 *    每个实例自带 AbortController，unmount 时 abort，第二张卡挂载时绑的是它自己的节点。
 *    （旧页那种写法在卡片里会静默坏掉：第二张卡 `_stDelegated` 已是 true，事件永远不绑。）
 *
 * ② **宿主换成传入容器**。旧页 `document.getElementById('panel-intraday')` / `'stBody'` /
 *    `'stTip'` 三个 id 在卡片里都不存在，全改成 `st.el` / `closest('.st-body')` / `st.tip`。
 *
 * ③ **浮层留在容器内**。旧页 `#stTip` 挂 `document.body` + `position:fixed`；
 *    这里作为 `.it-card` 的子节点 + `position:absolute`，坐标用 `clientX - cardRect.left` 换算。
 *    滚动时容器与光标相对位置变化，所以补了一个 capture 阶段 scroll 监听按上次光标坐标重算
 *    （旧页 fixed 不存在这个问题）。
 *
 * 数据层零写 DOM：本模块只读 `st.track`（/api/sector_track.json 原样），不取数、不改数据。
 */

import { esc, num, clsUp } from './format.js';
import { snapsOf, latestOf, chgValOf, valOf, unitOf, labelOf, lineD } from './caliber.js';

const QUAD_N = 10;          // 旧页 v6-C：固定前 10 + 后 10（去 5/10/全部 控件）
const GRID_K = 12;          // 旧页：3 列 × 4 行

/** 建立本实例的板块轮动状态（不碰 DOM）；hooks.pickBoard = 点板块→跳下方研究卡（2026-10-26） */
export function createState(cardEl, bodyEl, hooks) {
  const st = {
    uid: 'i' + Math.random().toString(36).slice(2, 8),   // 每实例唯一：SVG clipPath id 不能撞
    el: cardEl,
    body: bodyEl,
    hooks: hooks || {},
    tip: null,
    track: null,
    view: 'quad',
    cal: 'rel',
    chg: 'e',
    pins: [],
    hover: null,
    mouse: { x: 0, y: 0 },
    ac: new AbortController(),
  };
  // 旧页 4755 的 URL 覆盖（验收/截图用，同 ?theme=/?fs= 惯例）
  try {
    const q = new URLSearchParams(location.search);
    const v = q.get('stview');
    if (v === 'quad' || v === 'grid') st.view = v;
    const c = q.get('stchg');
    if (c === 'w' || c === 'e') st.chg = c;
    const cal = q.get('stcal');
    if (cal === 'rel' || cal === 'abs' || cal === 'amt' || cal === 'contrib') st.cal = cal;
  } catch (e) { /* 私有模式读不到 location.search：用默认值 */ }
  return st;
}

/** 旧页 4758 stSet：切视图/涨幅口径，切视图与涨幅时清掉钉住（旧页如此） */
export function stSet(st, k, v) {
  if (k === 'view') { st.view = v; st.pins = []; st.hover = null; }
  else if (k === 'chg') { st.chg = v; st.pins = []; }
  else { st.cal = v; }
  paint(st);
}

// ---------------------------------------------------------------- 渲染入口

/** 旧页 4766 _stPaint：填内容 + 校验悬停 + 重绘浮层 */
export function paint(st) {
  const el = st.body;
  if (!el) return;
  el.innerHTML = html(st);
  if (st.hover) {
    // 重建后校验悬停是否仍成立：鼠标仍在同一板块上 → 保持高亮+提示；否则清掉（防浮层残留）
    const t = document.elementFromPoint(st.mouse.x, st.mouse.y);
    if (hitCode(t) === st.hover) showTip(st, st.hover, st.mouse.x, st.mouse.y);
    else { st.hover = null; applyHl(st); hideTip(st); }
  } else hideTip(st);
}

/** 旧页 4795 _sectorTrackHtml：骨架 + 工具条 */
function html(st) {
  const track = st.track;
  if (!track) return '<p class="muted">板块轮动加载中…（/api/sector_track.json）</p>';
  const snaps = snapsOf(track);
  if (!snaps.length) return '<p class="muted">当天暂无板块快照（盘中 60s 采样后出现；交易日外显示最近收盘）</p>';
  const calName = st.cal === 'rel' ? '超额强度（板块−沪深全A amv_pct）'
    : (st.cal === 'abs' ? '绝对 amv_pct'
      : (st.cal === 'contrib' ? '增量贡献（Δ占比，‰）' : '成交额占比'));
  const calGrp = '<span class="st-grp"><span class="st-k">涨幅</span>'
    + btn(st.chg === 'w', 'st-set', 'chg', 'w', '加权')
    + btn(st.chg === 'e', 'st-set', 'chg', 'e', '等权') + '</span>';
  const srcTxt = st.view === 'quad'
    ? '口径：x=流通市值' + (st.chg === 'w' ? '加权' : '等权') + '真实涨幅 · y=超额 ｜ 归属日 '
      + esc(track.date || '—') + ' ｜ ' + snaps.length + ' 点'
    : '榜：真实涨幅（流通市值' + (st.chg === 'w' ? '加权' : '等权') + '，剔新）· 前10%降序 / 后10%升序 ｜ 归属日 '
      + esc(track.date || '—') + ' ｜ ' + snaps.length + ' 点';
  const staleWarn = track.stale
    ? '<span class="stale-warn" title="' + esc(track.stale_reason || '') + '">⚠ 轨迹断更 '
      + esc(track.stale_reason || '') + '</span>'
    : '';
  const bar = '<div class="st-bar">' + staleWarn
    + '<span class="st-grp">' + btn(st.view === 'quad', 'st-set', 'view', 'quad', '四象')
    + btn(st.view === 'grid', 'st-set', 'view', 'grid', '网格') + '</span>'
    + calGrp + '<span class="src">' + srcTxt + '</span></div>';
  const body = st.view === 'quad'
    ? quadHtml(st)
    : gridHtml(st) + (snaps.length < 2
      ? '<div class="src">当天仅 1 点（' + esc(calName) + '）——盘中 60s 采样累积后走势更完整</div>'
      : '');
  return bar + body;
}

/** 工具条按钮：12 个内联 onclick 之一 → data-act 委托（旧页 4599/4605 两处 stSet） */
function btn(on, act, k, v, label) {
  return '<button class="' + (on ? 'on' : '') + '" data-act="' + act + '" data-k="' + esc(k)
    + '" data-v="' + esc(v) + '" aria-pressed="' + on + '">' + esc(label) + '</button>';
}

// ---------------------------------------------------------------- 网格视图（旧页 4821 _stGridHtml）

function gridHtml(st) {
  const track = st.track;
  const latest = latestOf(track);
  const snaps = snapsOf(track);
  if (!latest) return '<p class="muted">网格：暂无数据</p>';
  const wmap = (track && track.weights) || {};
  // 占比：wmap = 前一交易日 EOD 占比（基线）；占比(t) = wmap×(1+amv)/(1+all)
  const shareAt = (snap, code) => {
    const b = snap && snap.boards && snap.boards[code];
    if (!b || wmap[code] == null || b.amv_pct == null || snap.all == null) return null;
    return wmap[code] * (1 + b.amv_pct / 100) / (1 + snap.all / 100);
  };
  const rankOf = (valFn) => {
    const arr = Object.keys(latest.boards || {}).map((c) => ({ c, v: valFn(c) }))
      .filter((x) => x.v != null).sort((a, b) => b.v - a.v);
    const m = {};
    arr.forEach((x, i) => { m[x.c] = i + 1; });
    return m;
  };
  // 旧页 v6-B：榜按【涨幅】排（跟四象 x 一致）
  const all = Object.keys(latest.boards || {}).map((c) => {
    const b = latest.boards[c] || {};
    const s0 = wmap[c];
    const s1 = shareAt(latest, c);
    return {
      code: c, name: b.name || c, chg: chgValOf(latest, c, st.chg), amv: b.amv_pct,
      dw: (s0 != null && s1 != null) ? s1 - s0 : null,
      series: snaps.map((s) => chgValOf(s, c, st.chg)),
    };
  }).filter((r) => r.chg != null);
  if (!all.length) return '<p class="muted">网格：无数据（涨幅口径缺失）</p>';
  const rkNow = rankOf((c) => chgValOf(latest, c, st.chg));
  const refSnap = snaps.find((s) => (s.ts || '').slice(11, 16) >= '10:00') || snaps[0];
  const rkRef = rankOf((c) => chgValOf(refSnap, c, st.chg));
  const rows = all.map((r) => ({
    ...r, rk: rkNow[r.code],
    rkDelta: (rkRef[r.code] != null && rkNow[r.code] != null) ? (rkRef[r.code] - rkNow[r.code]) : null,
  }));
  const desc = rows.slice().sort((a, b) => b.chg - a.chg);
  const topIn = desc.slice(0, GRID_K);                  // 前 10% · 涨幅降序（最强在左上）
  const topOut = desc.slice(-GRID_K).reverse();         // 后 10% · 涨幅升序（最弱在左上）
  // 断点感知迷你走势：归一算法与旧页同（mn/mx/MA10），X=全序列索引，Y=旧归一映射
  const spark = (series) => {
    const s = series.filter((v) => v != null);
    if (s.length < 2) return '';
    const ma = s.map((v, i) => {
      const a = Math.max(0, i - 9);
      const seg = s.slice(a, i + 1);
      return seg.reduce((p, q) => p + q, 0) / seg.length;
    });
    const vals = s.concat(ma);
    const mn = Math.min.apply(null, vals);
    const mx = Math.max.apply(null, vals);
    const rg = (mx - mn) || 1;
    const n = series.length;
    const X = (i) => (n <= 1 ? 50 : (i / (n - 1) * 100));
    const Y = (v) => (19 - (v - mn) / rg * 17);
    let si = 0;
    const maFull = series.map((v) => (v == null ? null : ma[si++]));   // MA 回填到原索引（含 null → 断线）
    const d = lineD(snaps, series, X, Y);
    const dm = lineD(snaps, maFull, X, Y);
    const stroke = (s[s.length - 1] >= s[0]) ? 'var(--up)' : 'var(--down)';
    return '<svg class="st-cell-sp" viewBox="0 0 100 20" preserveAspectRatio="none">'
      + '<path d="' + dm + '" fill="none" stroke="' + stroke
      + '" stroke-width="0.9" stroke-dasharray="2 1.6" opacity="0.6"/>'
      + '<path d="' + d + '" fill="none" stroke="' + stroke + '" stroke-width="1.5"/></svg>';
  };
  const sgn = (v, d) => (v == null ? '—' : (v > 0 ? '+' : '') + num(v, d));
  const cell = (x) => '<div class="st-cell" data-act="st-goto" data-code="' + esc(x.code)
    + '" data-ctx="sector" data-name="' + esc(x.name)
    + '" title="' + esc(x.name) + ' ｜ 涨幅 ' + sgn(x.chg, 2) + '% ｜ 权重变化 ' + sgn(x.dw, 4)
    + 'pp ｜ 活跃涨跌 ' + sgn(x.amv, 2) + '% ｜ 涨幅名次 ' + (x.rk != null ? x.rk : '—')
    + (x.rkDelta ? '（当天 ' + x.rkDelta + '）' : '') + ' ｜ 点击看工作台详情">'
    + '<div class="st-cell-h"><span class="st-cell-n">' + esc(x.name) + '</span>'
    + '<span class="st-cell-rk">#' + (x.rk != null ? x.rk : '—')
    + (x.rkDelta ? '<b class="' + (x.rkDelta > 0 ? 'up' : 'down') + '">'
      + (x.rkDelta > 0 ? '▲' + x.rkDelta : '↓' + (-x.rkDelta)) + '</b>' : '') + '</span></div>'
    + spark(x.series)
    + '<div class="st-cell-f"><span class="st-cell-v ' + clsUp(x.chg) + '">' + sgn(x.chg, 2) + '%</span>'
    + '<span class="st-cell-dw">' + sgn(x.dw, 2) + 'pp</span>'
    + '<span class="st-cell-v ' + clsUp(x.amv) + '">' + sgn(x.amv, 2) + '%</span></div></div>';
  return '<div class="st-grids"><div class="st-panel"><div class="st-col-hd">前 10% 榜 · 涨幅降序（'
    + topIn.length + '）</div><div class="st-cells">' + topIn.map(cell).join('') + '</div></div>'
    + '<div class="st-panel"><div class="st-col-hd">后 10% 榜 · 涨幅升序（' + topOut.length
    + '）</div><div class="st-cells">' + topOut.map(cell).join('') + '</div></div></div>'
    + '<div class="src">工作台样式：榜按<b>真实涨幅（流通市值' + (st.chg === 'w' ? '加权' : '等权')
    + '，剔新）</b>排序 · 前 10% 降序（最强左上）/ 后 10% 升序（最弱左上）｜ 每格=板块名 · #涨幅名次'
    + '（▲N/↓N=当天名次变化）· 涨幅当天走势（虚线=MA10）· 真实涨幅 % ｜ Δ占比 pp ｜ 活跃涨跌 % ｜ 二级 '
    + all.length + ' → 各 ' + GRID_K + ' 格</div>';
}

// ---------------------------------------------------------------- 四象视图（旧页 4870 _stQuadHtml，156 行）

function quadHtml(st) {
  const track = st.track;
  const latest = latestOf(track);
  if (!latest) return '<p class="muted">四象：暂无数据</p>';
  const wmap = (track && track.weights) || {};
  const snaps = snapsOf(track);
  const all = Object.keys(latest.boards || {}).map((c) => {
    const b = latest.boards[c] || {};
    const series = snaps.map((s) => {
      const xx = chgValOf(s, c, st.chg);
      const yy = valOf(track, s, c, st.cal);
      return (xx == null || yy == null) ? null : { x: xx, y: yy };
    }).filter(Boolean);
    return {
      code: c, name: b.name || c, weight: wmap[c],
      x: chgValOf(latest, c, st.chg), y: valOf(track, latest, c, st.cal), series,
    };
  }).filter((r) => r.weight != null && r.weight > 0 && r.x != null && r.y != null);
  if (!all.length) return '<p class="muted">四象：数据不足（涨幅口径缺失 / 无快照）</p>';

  const n = QUAD_N;
  const desc = all.slice().sort((a, b) => b.y - a.y);
  const sel = [...desc.slice(0, n), ...desc.slice(-n)]
    .filter((x, i, arr) => arr.findIndex((z) => z.code === x.code) === i);
  const xs = all.map((r) => r.x);
  const ys = all.map((r) => r.y);
  let xLo = Math.min.apply(null, xs.concat([0]));
  let xHi = Math.max.apply(null, xs.concat([0]));
  const xp = (xHi - xLo) * 0.12 || 1; xLo -= xp; xHi += xp;
  let yLo = Math.min.apply(null, ys.concat([0]));
  let yHi = Math.max.apply(null, ys.concat([0]));
  const ypd = (yHi - yLo) * 0.12 || 0.5; yLo -= ypd; yHi += ypd;
  const W = 760, MT = 26, MB = 30, ML = 54, MR = 150, plotW = W - ML - MR, plotH = 380, H = MT + plotH + MB;
  const X = (v) => ML + (v - xLo) / (xHi - xLo) * plotW;
  const Y = (v) => MT + plotH * (1 - (v - yLo) / (yHi - yLo));
  const maxW = Math.max.apply(null, all.map((r) => r.weight));
  const k = 26 / Math.sqrt(maxW > 0 ? maxW : 1);
  const R = (r) => Math.max(4, Math.min(26, k * Math.sqrt(Math.max(r.weight, 0.0001))));
  const fillOf = (r) => (r.x > 0 ? 'var(--up)' : (r.x < 0 ? 'var(--down)' : 'var(--dim)'));
  const quadOf = (r) => (r.x >= 0 ? (r.y >= 0 ? '真强' : '虚涨') : (r.y >= 0 ? '吸筹' : '真弱'));
  const f2 = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + num(v, 2));
  // EMA 平滑（alpha=0.3）：削 60s 采样毛刺，保留趋势走向，尾迹更直观
  const ema = (pts2, a) => {
    const r = [pts2[0]];
    for (let i = 1; i < pts2.length; i++) r.push({ x: a * pts2[i].x + (1 - a) * r[i - 1].x, y: a * pts2[i].y + (1 - a) * r[i - 1].y });
    return r;
  };
  // Catmull-Rom 样条：折线 → 平滑曲线，消除转折点尖角
  const smoothD = (pts2) => {
    if (pts2.length < 2) return '';
    if (pts2.length === 2) return 'M' + pts2[0].x.toFixed(1) + ' ' + pts2[0].y.toFixed(1) + ' L' + pts2[1].x.toFixed(1) + ' ' + pts2[1].y.toFixed(1);
    let d = 'M' + pts2[0].x.toFixed(1) + ' ' + pts2[0].y.toFixed(1);
    for (let i = 0; i < pts2.length - 1; i++) {
      const p0 = pts2[Math.max(0, i - 1)], p1 = pts2[i], p2 = pts2[i + 1], p3 = pts2[Math.min(pts2.length - 1, i + 2)];
      const c1x = p1.x + (p2.x - p0.x) / 6, c1y = p1.y + (p2.y - p0.y) / 6;
      const c2x = p2.x - (p3.x - p1.x) / 6, c2y = p2.y - (p3.y - p1.y) / 6;
      d += ' C' + c1x.toFixed(1) + ' ' + c1y.toFixed(1) + ',' + c2x.toFixed(1) + ' ' + c2y.toFixed(1)
        + ',' + p2.x.toFixed(1) + ' ' + p2.y.toFixed(1);
    }
    return d;
  };
  const ribbon = (tp, w0, w1, nn) => {
    if (!tp || tp.length < 2) return '';
    const segs = []; let L = 0;
    for (let i = 1; i < tp.length; i++) {
      const len = Math.hypot(tp[i].x - tp[i - 1].x, tp[i].y - tp[i - 1].y);
      segs.push({ a: tp[i - 1], b: tp[i], len, s0: L }); L += len;
    }
    if (!(L > 0)) return '';
    const at = (s) => {
      let g = segs[segs.length - 1];
      for (let i = 0; i < segs.length; i++) { if (s <= segs[i].s0 + segs[i].len) { g = segs[i]; break; } }
      const fr = g.len > 0 ? Math.min(1, Math.max(0, (s - g.s0) / g.len)) : 0;
      return { x: g.a.x + (g.b.x - g.a.x) * fr, y: g.a.y + (g.b.y - g.a.y) * fr };
    };
    const N = Math.max(8, nn || 40); const P = [];
    for (let q = 0; q <= N; q++) { const pt = at(L * q / N); pt.t = q / N; P.push(pt); }
    const lf = [], rt = [];
    for (let q = 0; q <= N; q++) {
      const a = P[Math.max(0, q - 1)], b = P[Math.min(N, q + 1)];
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1; dx /= d; dy /= d;
      const h = (w0 + (w1 - w0) * P[q].t) / 2;
      lf.push({ x: P[q].x - dy * h, y: P[q].y + dx * h });
      rt.push({ x: P[q].x + dy * h, y: P[q].y - dx * h });
    }
    return smoothD(lf) + ' ' + smoothD(rt.reverse()).replace(/^M/, 'L') + ' Z';
  };
  const pts = sel.map((r) => {
    const cx = X(r.x), cy = Y(r.y), rr = R(r);
    return { r, cx, cy, rr, q: quadOf(r) };
  });
  const pinOn = (code) => st.pins.indexOf(code) >= 0;
  const LAB = Math.min(13.5, Math.max(10, plotH / Math.max(1, pts.length)));
  const labs = pts.map((p) => ({ p, y: p.cy, pin: pinOn(p.r.code) }))
    .sort((a, b) => (a.pin === b.pin ? a.y - b.y : (a.pin ? -1 : 1)));
  let cur = MT + 8;
  labs.forEach((l) => { l.y = cur; cur += LAB; });

  const o = [];
  o.push('<svg class="st-quad" viewBox="0 0 ' + W + ' ' + H
    + '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="四象气泡图 ' + sel.length + ' 个板块">');
  // clipPath id 必须每实例唯一，否则两张卡同时在页上会互相引用对方的裁剪区
  const clipId = 'stqClip-' + st.uid;
  o.push('<defs><clipPath id="' + clipId + '"><rect x="' + ML + '" y="' + MT + '" width="' + plotW
    + '" height="' + plotH + '"/></clipPath></defs>');
  for (let t = 0; t <= 5; t++) {
    const v = yLo + (yHi - yLo) * t / 5, y = Y(v);
    o.push('<line class="st-grid" x1="' + ML + '" y1="' + y.toFixed(1) + '" x2="' + (ML + plotW)
      + '" y2="' + y.toFixed(1) + '"/><text class="st-ytx" x="' + (ML - 6) + '" y="' + (y + 3).toFixed(1)
      + '" text-anchor="end">' + v.toFixed(1) + '</text>');
  }
  for (let t = 0; t <= 6; t++) {
    const v = xLo + (xHi - xLo) * t / 6, x = X(v);
    o.push('<line class="st-grid" x1="' + x.toFixed(1) + '" y1="' + MT + '" x2="' + x.toFixed(1) + '" y2="'
      + (MT + plotH) + '"/><text class="st-xtx" x="' + x.toFixed(1) + '" y="' + (MT + plotH + 15)
      + '" text-anchor="middle">' + v.toFixed(1) + '</text>');
  }
  o.push('<line class="st-base" x1="' + X(0).toFixed(1) + '" y1="' + MT + '" x2="' + X(0).toFixed(1) + '" y2="' + (MT + plotH) + '"/>');
  o.push('<line class="st-base" x1="' + ML + '" y1="' + Y(0).toFixed(1) + '" x2="' + (ML + plotW) + '" y2="' + Y(0).toFixed(1) + '"/>');
  [['真强', ML + plotW - 6, MT + 13, 'end'], ['吸筹', ML + 6, MT + 13, 'start'],
    ['真弱', ML + 6, MT + plotH - 6, 'start'], ['虚涨', ML + plotW - 6, MT + plotH - 6, 'end']]
    .forEach(([t, x, y, an]) => o.push('<text class="st-qlab" x="' + x + '" y="' + y
      + '" text-anchor="' + an + '">' + t + '</text>'));
  o.push('<g clip-path="url(#' + clipId + ')">');
  pts.forEach((p) => {
    const tp0 = p.r.series.map((v) => (v == null ? null : { x: X(v.x), y: Y(v.y) })).filter(Boolean);
    if (tp0.length < 2) return;
    const tp = ema(tp0, 0.3);   // EMA 平滑，削毛刺留趋势
    const w1 = 0.6 * p.rr, w0 = Math.max(0.7, w1 * 0.25);
    const rib = ribbon(tp, w0, w1, 40);
    const up = p.r.x > 0;
    if (rib) o.push('<path class="st-trail" data-code="' + esc(p.r.code) + '" d="' + rib
      + '" style="fill:' + (up ? 'var(--up)' : 'var(--down)') + ';opacity:0.12;pointer-events:none"/>');
    // 双层流星（照工作台 vq-tg/vq-tf）：.st-tg 外晕滞后 0.22s / .st-tf 亮芯领先；pathLength=100 归一
    const d2 = smoothD(tp);
    o.push('<path class="st-tg" data-code="' + esc(p.r.code) + '" d="' + d2
      + '" pathLength="100" style="stroke:color-mix(in srgb, ' + (up ? 'var(--up)' : 'var(--down)')
      + ' 45%, var(--card2));opacity:0"/>');
    o.push('<path class="st-tf" data-code="' + esc(p.r.code) + '" d="' + d2
      + '" pathLength="100" style="stroke:' + (up ? 'var(--up)' : 'var(--down)') + ';opacity:0"/>');
  });
  o.push('</g>');
  pts.forEach((p) => {
    const on = pinOn(p.r.code);
    const bb = latest.boards[p.r.code];
    const ab = (bb && bb.amv_pct != null) ? num(bb.amv_pct, 2) + '%（含 v9 日衰减）' : '—';
    const tt = esc(p.r.name) + ' ｜ 涨幅 ' + f2(p.r.x) + '% ｜ 超额 ' + f2(p.r.y) + 'pp ｜ 权重 '
      + num(p.r.weight, 2) + '% ｜ 绝对 amv_pct ' + ab + ' ｜ ' + p.q;
    o.push('<g class="st-bub' + (on ? ' on' : '') + '" data-code="' + esc(p.r.code)
      + '" tabindex="0" role="button" title="' + tt + '">');
    if (on) o.push('<circle cx="' + p.cx.toFixed(1) + '" cy="' + p.cy.toFixed(1) + '" r="'
      + (p.rr + 3.5).toFixed(1) + '" fill="none" style="stroke:var(--accent)" stroke-width="1.4" stroke-dasharray="3 2"/>');
    o.push('<circle cx="' + p.cx.toFixed(1) + '" cy="' + p.cy.toFixed(1) + '" r="' + p.rr.toFixed(1)
      + '" style="fill:' + fillOf(p.r) + ';stroke:' + fillOf(p.r) + '" fill-opacity="0.8" stroke-width="1.6"/>');
    o.push('</g>');
  });
  // 钉住气泡就地标签（照预览_四象标签避让.html 正本算法：按权重降序、八向逐环 1..4 贪心避让 + 引导线）
  (() => {
    const nameW = (s) => {
      let w = 0;
      for (const ch of String(s)) w += (ch.charCodeAt(0) > 0x2E80 ? 10 : 5.8);
      return w;
    };
    const hit = (a, b) => !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
    const inPlot = (r) => r.x >= ML - 1 && r.y >= MT - 1 && r.x + r.w <= ML + plotW + 1 && r.y + r.h <= MT + plotH + 1;
    const LBH = 10, boxes = [], placed = [];
    pts.forEach((p) => { boxes.push({ x: p.cx - p.rr - 1.5, y: p.cy - p.rr - 1.5, w: 2 * (p.rr + 1.5), h: 2 * (p.rr + 1.5) }); });
    const dirs = [[1, 0], [-1, 0], [0, -1], [0, 1], [1, -1], [-1, -1], [1, 1], [-1, 1]];
    pts.filter((p) => pinOn(p.r.code)).slice().sort((a, b) => b.r.weight - a.r.weight).forEach((p) => {
      const lw = nameW(p.r.name), lh = LBH;
      let chosen = null;
      for (let ring = 1; ring <= 4 && !chosen; ring++) {
        for (let d = 0; d < dirs.length && !chosen; d++) {
          const dx = dirs[d][0], dy = dirs[d][1];
          let gx, gy;
          if (dx !== 0 && dy !== 0) { gx = p.cx + (p.rr * 0.72 + 4) * ring * dx; gy = p.cy + (p.rr * 0.72 + 4) * ring * dy; }
          else { gx = p.cx + (p.rr + 6) * ring * dx; gy = p.cy + (p.rr + 6) * ring * dy; }
          let r;
          if (dx > 0 && dy === 0) r = { x: gx, y: gy - lh / 2 };
          else if (dx < 0 && dy === 0) r = { x: gx - lw, y: gy - lh / 2 };
          else if (dy < 0 && dx === 0) r = { x: gx - lw / 2, y: gy - lh };
          else if (dy > 0 && dx === 0) r = { x: gx - lw / 2, y: gy };
          else if (dx > 0 && dy < 0) r = { x: gx, y: gy - lh };
          else if (dx < 0 && dy < 0) r = { x: gx - lw, y: gy - lh };
          else if (dx > 0 && dy > 0) r = { x: gx, y: gy };
          else r = { x: gx - lw, y: gy };
          r.w = lw; r.h = lh;
          if (!inPlot(r)) continue;
          let clash = false;
          for (let b = 0; b < boxes.length; b++) { if (hit(r, boxes[b])) { clash = true; break; } }
          if (!clash) chosen = r;
        }
      }
      if (!chosen) {   // 全冲突：甩到绘图区空白处（贴边候选），画引导线连回气泡
        const cands = [[ML + plotW - lw - 2, p.cy - lh / 2], [ML + plotW - lw - 2, p.cy + p.rr + 8],
          [ML + plotW - lw - 2, p.cy - p.rr - 8 - lh], [ML + 2, p.cy - lh / 2], [ML + 2, p.cy + p.rr + 8],
          [ML + 2, p.cy - p.rr - 8 - lh], [p.cx - lw / 2, MT + 2], [p.cx - lw / 2, MT + plotH - lh - 2]];
        for (let q = 0; q < cands.length && !chosen; q++) {
          const rr = { x: cands[q][0], y: cands[q][1], w: lw, h: lh };
          if (!inPlot(rr)) continue;
          let c2 = false;
          for (let b2 = 0; b2 < boxes.length; b2++) { if (hit(rr, boxes[b2])) { c2 = true; break; } }
          if (!c2) chosen = rr;
        }
        if (!chosen) chosen = { x: ML + plotW - lw - 2, y: Math.min(MT + plotH - lh - 2, Math.max(MT + 2, p.cy - lh / 2)), w: lw, h: lh };
      }
      boxes.push({ x: chosen.x, y: chosen.y, w: chosen.w, h: chosen.h });
      placed.push({ p, r: chosen });
    });
    placed.forEach((pl) => {
      const p = pl.p, r = pl.r;
      const nx = Math.max(r.x, Math.min(p.cx, r.x + r.w));
      const ny = Math.max(r.y, Math.min(p.cy, r.y + r.h));
      const dx = nx - p.cx, dy = ny - p.cy, dist = Math.sqrt(dx * dx + dy * dy);
      if (dist > p.rr + 8) {
        const ang = Math.atan2(dy, dx) || 0;
        o.push('<line class="st-lead" x1="' + (p.cx + Math.cos(ang) * (p.rr + 2)).toFixed(1)
          + '" y1="' + (p.cy + Math.sin(ang) * (p.rr + 2)).toFixed(1) + '" x2="' + nx.toFixed(1)
          + '" y2="' + ny.toFixed(1) + '"/>');
      }
      const tx = (r.x + 1).toFixed(1), ty = (r.y + r.h / 2).toFixed(1), fill = fillOf(p.r);
      o.push('<text class="st-blab-h" x="' + tx + '" y="' + ty + '">' + esc(p.r.name) + '</text>');
      o.push('<text class="st-blab" x="' + tx + '" y="' + ty + '" style="fill:' + fill + '">' + esc(p.r.name) + '</text>');
    });
  })();
  labs.forEach((l) => {
    const lx = ML + plotW + 4;
    o.push('<g class="st-ql' + (l.pin ? ' pin' : '') + '" data-code="' + esc(l.p.r.code)
      + '" data-act="st-goto" data-goto="' + esc(l.p.r.code) + '">');
    o.push('<rect x="' + lx + '" y="' + (l.y - LAB / 2).toFixed(1) + '" width="140" height="'
      + LAB.toFixed(1) + '" fill="transparent"/>');
    o.push('<text class="st-lab" x="' + (lx + 4) + '" y="' + (l.y + 3.5).toFixed(1) + '" style="fill:'
      + fillOf(l.p.r) + '">' + esc(l.p.r.name) + ' ' + f2(l.p.r.y) + '</text>');
    o.push('</g>');
  });
  o.push('</svg>');
  const cap = (snaps.length < 2) ? ' ｜ 单点日（周末/节后）仅气泡、无尾迹' : '';
  o.push('<div class="src">x=板块真实涨幅（流通市值' + (st.chg === 'w' ? '加权' : '等权')
    + '，剔首日新股，%）· y=超额（amv_pct−沪深全A，pp）｜ 四象限：右上真强（涨+活跃强）· 左上吸筹（跌+活跃强）·'
    + ' 左下真弱（跌+活跃弱）· 右下虚涨（涨+活跃弱）｜ 气泡=权重 · 色=涨幅方向 · 尾迹=当天 (x,y) 轨迹 ｜ 显示前 '
    + QUAD_N + ' + 后 ' + QUAD_N + '（' + sel.length + '/' + all.length + '）｜ 悬停高亮看信息 · 点气泡钉住'
    + '（钉住后就地显示板块名）· 点标签跳到下方研究卡' + cap + '</div>');
  return o.join('');
}

// ---------------------------------------------------------------- 命中 / 高亮 / 浮层

/** 旧页 5026 _stHitCode：从事件节点反查板块码 */
export function hitCode(t) {
  if (!t || !t.closest) return null;
  const g = t.closest('.st-bub, .st-line, .st-line-lab');
  if (g && g.dataset.code) return g.dataset.code;
  const lab = t.closest('.st-lab');
  const q = lab && lab.closest('.st-ql');
  if (q && q.dataset.code) return q.dataset.code;
  return null;
}

/** 旧页 5064 _stApplyHl：pins ∪ hover = on，其余 dim */
export function applyHl(st) {
  const el = st.body;
  if (!el) return;
  const any = st.pins.length > 0 || !!st.hover;
  el.querySelectorAll('[data-code]').forEach((n) => {
    const k = n.getAttribute('data-code');
    const on = st.pins.indexOf(k) >= 0 || k === st.hover;
    if (n.classList.contains('st-trail')) {   // 尾迹三档（照工作台 IDLE/DIM/ON）
      n.style.opacity = on ? '0.5' : (any ? '0.04' : '0.12');
      return;
    }
    n.classList.toggle('dim', any && !on);
    n.classList.toggle('hi', on);
  });
  // 流动层只在高亮目标上显形
  el.querySelectorAll('.st-tf, .st-tg').forEach((n) => {
    const k = n.getAttribute('data-code');
    const on = st.pins.indexOf(k) >= 0 || k === st.hover;
    const glow = n.classList.contains('st-tg');
    n.style.opacity = on ? (glow ? '0.22' : '0.85') : '0';
    n.style.animation = on ? ('stFlow 1.8s linear ' + (glow ? '0.22s' : '0s') + ' infinite') : 'none';
  });
}

/** 旧页 5086 _stTipEl：浮层作为容器内子节点（旧页挂 body） */
function tipEl(st) {
  if (st.tip && st.tip.isConnected) return st.tip;
  const t = document.createElement('div');
  t.className = 'st-tip';
  t.hidden = true;
  st.el.appendChild(t);
  st.tip = t;
  return t;
}

export function hideTip(st) {
  if (st.tip) st.tip.hidden = true;
}

/** 旧页 5089 _stTipShow：内容与原页逐字一致；坐标改为容器坐标（见文件头 ③） */
export function showTip(st, code, clientX, clientY) {
  const snap = latestOf(st.track);
  if (!snap || !code) return;
  const b = (snap.boards || {})[code] || {};
  const w = ((st.track && st.track.weights) || {})[code];
  const f = (v, d) => (v == null ? '—' : (v > 0 ? '+' : '') + num(v, d));
  const t = tipEl(st);
  t.innerHTML = '<b>' + esc(b.name || code) + '</b> <span style="color:var(--dim)">' + esc(code) + '</span>'
    + '<br>真实涨幅 <b class="' + clsUp(chgValOf(snap, code, st.chg)) + '" style="font-family:var(--font-mono)">'
    + f(chgValOf(snap, code, st.chg), 2) + '%</b>'
    + '<br>超额 <b class="' + clsUp(valOf(st.track, snap, code, st.cal)) + '" style="font-family:var(--font-mono)">'
    + f(valOf(st.track, snap, code, st.cal), 2) + 'pp</b>'
    + '<br>权重 ' + (w == null ? '—' : num(w, 2) + '%') + ' ｜ 活跃涨跌 <b class="'
    + clsUp(b.amv_pct) + '" style="font-family:var(--font-mono)">' + f(b.amv_pct, 2) + '%</b>';
  t.hidden = false;
  moveTip(st, clientX, clientY);
}

/**
 * 旧页 5102 _stTipMove：光标跟随。
 * 旧页用 clientX/clientY 直接写 fixed 定位；这里换成容器内坐标，并夹在容器矩形内。
 * 传 null 时用上次记录的鼠标 client 坐标（scroll 重算走这条）。
 */
export function moveTip(st, clientX, clientY) {
  const t = tipEl(st);
  if (t.hidden) return;
  const cx = (clientX == null) ? st.mouse.x : clientX;
  const cy = (clientY == null) ? st.mouse.y : clientY;
  const cr = st.el.getBoundingClientRect();
  const pad = 12;
  let x = cx - cr.left + pad;
  let y = cy - cr.top + pad;
  const r = t.getBoundingClientRect();
  if (cx + pad + r.width > window.innerWidth - 8) x = cx - cr.left - r.width - pad;
  if (cy + pad + r.height > window.innerHeight - 8) y = cy - cr.top - r.height - pad;
  const maxX = Math.max(4, st.el.clientWidth - r.width - 4);
  const maxY = Math.max(4, st.el.clientHeight - r.height - 4);
  t.style.left = Math.max(4, Math.min(x, maxX)).toFixed(0) + 'px';
  t.style.top = Math.max(4, Math.min(y, maxY)).toFixed(0) + 'px';
}

// ---------------------------------------------------------------- 事件委托（旧页 5032 _stBindQuad）

/**
 * 旧页 `_stBindQuad` 的全部监听搬到这里，宿主从 `#panel-intraday` 换成卡片根节点。
 * 只绑一次由 mount 次数保证（见文件头 ①）；信号走 AbortController，unmount 一次全解。
 */
export function bind(st) {
  const host = st.el;
  const ac = st.ac;
  const opt = { signal: ac.signal };
  const inBody = (t) => !!(t && t.closest && t.closest('.st-body'));

  host.addEventListener('mouseover', (ev) => {
    if (!inBody(ev.target)) return;
    st.mouse = { x: ev.clientX, y: ev.clientY };
    const k = hitCode(ev.target);
    if (!k) {
      if (st.hover) { st.hover = null; applyHl(st); hideTip(st); }
      return;
    }
    if (k === st.hover) { moveTip(st, ev.clientX, ev.clientY); return; }
    st.hover = k; applyHl(st); showTip(st, k, ev.clientX, ev.clientY);
  }, opt);

  host.addEventListener('mousemove', (ev) => {
    if (!inBody(ev.target)) return;
    st.mouse = { x: ev.clientX, y: ev.clientY };
    if (st.hover) moveTip(st, ev.clientX, ev.clientY);
  }, opt);

  host.addEventListener('mouseout', (ev) => {
    if (!inBody(ev.target)) return;
    const k = hitCode(ev.target);
    if (!k) return;
    if (hitCode(ev.relatedTarget) === k) return;   // 气泡组内子元素间移动不算离开
    if (st.hover === k) { st.hover = null; applyHl(st); hideTip(st); }
  }, opt);

  host.addEventListener('mouseleave', () => {
    const t = st.tip;
    if (st.hover || (t && !t.hidden)) { st.hover = null; applyHl(st); hideTip(st); }
  }, opt);

  // 点气泡 = 钉住（可多钉、再点取消）—— 主人 2026-10-26 明确「别破坏钉住交互」，保留；
  // 点标签 / 网格格 = 跳下方研究卡 + 选中（原来是 data-open-amv 弹板块详情，主人拍「统一」）。
  host.addEventListener('click', (ev) => {
    if (!inBody(ev.target)) return;
    const go = ev.target.closest('[data-act="st-goto"]');
    if (go && go.dataset.code) {
      ev.preventDefault();
      const fn = st.hooks && st.hooks.pickBoard;
      if (typeof fn === 'function') fn(go.dataset.code);
      return;   // 不要再落到下面的钉住分支
    }
    const g = ev.target.closest('.st-bub, .st-line');
    if (!g || !g.dataset.code) return;
    const c = g.dataset.code;
    const i = st.pins.indexOf(c);
    if (i >= 0) st.pins.splice(i, 1);
    else st.pins.push(c);
    paint(st);
  }, opt);

  // 工具条（四象/网格 · 加权/等权）：旧页是内联 onclick="stSet(...)"，这里改 data-act
  host.addEventListener('click', (ev) => {
    if (!inBody(ev.target)) return;
    const t = ev.target.closest('[data-act="st-set"]');
    if (!t) return;
    stSet(st, t.dataset.k, t.dataset.v);
  }, opt);

  // 旧页 fixed 浮层不随滚动错位；改成容器内绝对定位后必须重算（见文件头 ③）
  window.addEventListener('scroll', () => {
    if (st.tip && !st.tip.hidden) moveTip(st, null, null);
  }, { passive: true, capture: true, signal: ac.signal });

  // 旧页 5041 window.blur：切走窗口时清悬停（防浮层残留）
  window.addEventListener('blur', () => {
    st.hover = null;
    hideTip(st);
    applyHl(st);
  }, { passive: true, signal: ac.signal });
}

export function unbind(st) {
  try { st.ac.abort(); } catch (e) { /* 已 abort：忽略 */ }
  st.tip = null;
  st.hover = null;
}
