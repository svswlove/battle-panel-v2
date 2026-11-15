/**
 * 板块卡片 · 图视图（网格 / 四象）
 *
 * 来源：8002 工作台 `vizGrid`（app.js:2637，76 行）与 `vizQuad`（app.js:2783，400 行）。
 * 按主人 2026-10-25 要求照做，去掉折线（vizCompass）与热力（vizHeat）。
 *
 * **DOM 与类名逐字照抄 8002**：`.vizbox / .viz-grids / .viz-col / .viz-col-hd / .viz-grid /
 * .viz-cell / .n / .b / .bg-* / .f / svg.s`，四象用 `.vq-*` 全套（含 `.vq-trail` 尾迹、
 * `.vq-ring` 钉住、`.vq-blab` 就地标签、`.vq-lead` 引导线）。样式表就是按这些类名写的，
 * 改一个名字就等于样式全丢。
 *
 * 数据：只读 compute 实例与已取到的 flowMap，不发请求、不派生新指标。
 */

import { el, esc } from './board.js';

const VIZ_SPARK_DAYS = 60;

/* 8002 app.js:2203 的 vizCls / vizFmt（逐字）。 */
const vizCls = (v) => (v == null ? 'viz-fl' : (v > 0 ? 'viz-up' : (v < 0 ? 'viz-dn' : 'viz-fl')));
const vizFmt = (v, n) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(n == null ? 2 : n));

/**
 * 取数：严格按工作台原有的两份榜单取行（前10%榜 + 后10%榜，含各自的「退出」行），
 * 不做任何自己的筛选、排序或派生指标 —— 8002 `vizRows` app.js:2199 逐字。
 *
 * 单位：compute 的 weight/d 是 1e6 整数；/1e4 得百分数（= /1e6*100），与 8002 同式。
 */
export function vizRows(lists, flowMap, isPick, idxRows) {
  const pick = (r, side, isExit) => {
    const f = flowMap[r.code] || {};
    if (isPick && !isPick(r)) return null;
    // 与表格 buildPanel 同口径：flowMap 缺值 → null（不编造 0）
    const ap = (f.amv_pct != null) ? f.amv_pct : null;
    const mn = (f.main == null) ? null : f.main;
    return {
      j: r.j, code: r.code, name: r.name, l1: r.l1, side, isExit: !!isExit,
      weight: (r.weight == null ? 0 : r.weight) / 1e4,
      dw: (r.d == null ? 0 : r.d) / 1e4,
      pct: (ap == null ? null : ap),
      flow: (mn == null ? null : mn / 1e4),
      rank: r.rank, rankPrev: r.rankPrev,
      streak: r.streak, isNew: r.isNew, isReentry: r.isReentry,
    };
  };
  // 各大指数：工作台本来就是「全列指数」（不切 10%）——照它来，不改它的逻辑
  if (idxRows) return idxRows.map((r) => pick(r, 'all'));
  const L = lists;
  if (!L || L.warmup) return [];
  // 与 buildPanel 的入参一一对应：listIn = topIn + exitsIn，listOut = topOut + exitsOut
  return L.topIn.map((r) => pick(r, 'in')).concat(L.exitsIn.map((r) => pick(r, 'in', true)))
    .concat(L.topOut.map((r) => pick(r, 'out'))).concat(L.exitsOut.map((r) => pick(r, 'out', true)));
}

/* ---------------------------------------------------------------- 网格（8002 vizGrid 逐字） */

/** 迷你走势（近 60 日 AMV + MA10）；8002 `vizGrid` 内 spark() 逐字。 */
function sparkSvg(C, j) {
  const w = C.weight;
  const hi = C.dayB;
  const lo = Math.max(0, hi - VIZ_SPARK_DAYS + 1);
  const T = C.total || null;   // 每日全A活跃市值（与 weight 同索引；amv 换算基准）
  const vals = [], mas = [];
  let last = null;
  for (let d = lo; d <= hi; d++) {
    const row = w[d];
    const v = (row && row[j] != null && T && T[d] != null) ? (row[j] / 1e6) * T[d] : last;
    if (v == null) continue;
    last = v; vals.push(v);
    const a = Math.max(0, vals.length - 10);   // MA10（与详情卡均线同口径）
    let s2 = 0;
    for (let t = a; t < vals.length; t++) s2 += vals[t];
    mas.push(s2 / (vals.length - a));
  }
  if (vals.length < 2) return '';
  const up = vals[vals.length - 1] >= mas[mas.length - 1];
  const all = vals.concat(mas);
  const mn = Math.min.apply(null, all), mx = Math.max.apply(null, all), rg = (mx - mn) || 1;
  const X = (i) => (i / (vals.length - 1) * 100).toFixed(2);
  const Y = (v) => (28 - (v - mn) / rg * 24 - 2).toFixed(2);
  const path = (a) => a.map((v, i) => (i ? 'L' : 'M') + X(i) + ' ' + Y(v)).join(' ');
  return '<svg class="s" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true">'
    + '<path d="' + path(mas) + '" fill="none" stroke="var(--dim)" stroke-width="0.9" stroke-dasharray="2 2"/>'
    + '<path d="' + path(vals) + '" fill="none" stroke="' + (up ? 'var(--up-solid)' : 'var(--down-solid)') + '" stroke-width="1.4"/></svg>';
}

/**
 * 徽标：图形小徽标（主人 2026-10-02：不要文字，做小色块）——✦ 新进 / ↻ 重入 / ✕ 退出 /
 * ▲N·▼N 连榜 / ↑N·↓N 名次。8002 `vizGrid` 内 badge() 逐字。
 */
function badgeOf(r) {
  const t = [];
  const out = (r.side === 'out');
  const sideTxt = (out ? '后' : '前') + '10%榜';
  if (r.isExit) t.push('<i class="bg bg-ex" title="退出' + sideTxt + '">✕</i>');
  else if (r.isNew) t.push('<i class="bg bg-new" title="新进' + sideTxt + '">✦</i>');
  else if (r.isReentry) t.push('<i class="bg bg-re" title="重入' + sideTxt + '">↻</i>');
  if (r.streak != null && Math.abs(r.streak) >= 2) {
    t.push('<i class="bg bg-st" title="连续在' + sideTxt + ' ' + Math.abs(r.streak) + ' 日">'
      + (out ? '▼' : '▲') + Math.abs(r.streak) + '</i>');
  }
  if (!r.isExit && r.rank != null && r.rankPrev != null) {
    const md = r.rankPrev - r.rank;                 // 与表格 mvHtml 同义：正＝名次上升
    if (md !== 0) t.push('<i class="bg bg-rk" title="名次' + (md > 0 ? '上升' : '下降') + ' ' + Math.abs(md) + '">'
      + (md > 0 ? '↑' : '↓') + Math.abs(md) + '</i>');
  }
  return t.length ? ' <span class="b">' + t.join('') + '</span>' : '';
}

/** 悬停浮标内容（8002 `vizTipHtml` app.js:2242 逐字）。 */
function tipHtml(r) {
  const where = r.side === 'in' ? '前10%榜' : (r.side === 'out' ? '后10%榜' : '');
  const st = (where && r.streak != null && Math.abs(r.streak) >= 2)
    ? '　连续在' + where + ' ' + Math.abs(r.streak) + ' 日' : '';
  return '<b>' + esc(r.name) + '</b>' + (r.l1 && r.l1 !== r.name ? ' <span style="color:var(--dim)">' + esc(r.l1) + '</span>' : '') + '<br>'
    + '权重 <b>' + r.weight.toFixed(2) + '%</b>　权重变化 <span class="' + vizCls(r.dw) + '">' + vizFmt(r.dw) + ' pp</span><br>'
    + '活跃涨跌 <span class="' + vizCls(r.pct) + '">' + vizFmt(r.pct) + '%</span><br>'
    + '主力净流入 <span class="' + vizCls(r.flow) + '">' + vizFmt(r.flow, 1) + ' 亿</span>' + st
    + (r._ov ? '　<span style="color:var(--dim)">（端点已溢出·裁到边界）</span>' : '');
}

/** 8002 `vizBind` app.js:2252：悬停浮标 + 点击开卡（data-nocard 只给读数）。 */
function vizBind(host, rows, o) {
  const byKey = new Map(rows.map((r) => [r.side + '|' + r.code, r]));
  const open = (r) => { if (o.tipHide) o.tipHide(); o.onOpen(r.code, r.name); };
  host.querySelectorAll('[data-vkey]').forEach((n) => {
    const r = byKey.get(n.dataset.vkey);
    if (!r) return;
    n.addEventListener('mouseenter', (e) => o.tipShow(tipHtml(r), e));
    n.addEventListener('mousemove', (e) => o.tipShow(null, e, true));
    n.addEventListener('mouseleave', () => o.tipHide && o.tipHide());
    n.addEventListener('focus', () => {
      const b = n.getBoundingClientRect();
      o.tipShow(tipHtml(r), { clientX: b.left + b.width, clientY: b.top });
    });
    n.addEventListener('blur', () => o.tipHide && o.tipHide());
    if (n.hasAttribute('data-nocard')) return;
    n.addEventListener('click', () => open(r));
    n.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(r); } });
  });
}

/**
 * 网格：每格一个板块，前 10% / 后 10% 两栏（8002 `vizGrid` app.js:2637 逐字）。
 *
 * @param {object} o
 * @param {Array} o.rows        vizRows() 结果
 * @param {object} o.C          compute 实例（取迷你走势）
 * @param {string} o.levelName  层级名
 * @param {boolean} o.idxMode   指数层只出单栏「全部」
 * @param {function} o.onOpen / o.tipShow / o.tipHide
 */
export function renderGrid(o) {
  const { rows, C, levelName, idxMode } = o;
  const host = el('div');
  if (!rows.length) {
    host.append(el('div', { class: 'viz-cap' }, '该层暂无可视化数据'));
    return host;
  }
  const cell = (r) =>
    '<div class="viz-cell" tabindex="0" role="button" data-vkey="' + r.side + '|' + r.code + '" aria-label="'
    + esc(r.name) + '，权重变化 ' + vizFmt(r.dw) + ' 个百分点">'
    + '<div class="n">' + esc(r.name) + badgeOf(r) + '</div>' + sparkSvg(C, r.j)
    + '<div class="f"><span class="' + vizCls(r.dw) + '">' + vizFmt(r.dw) + 'pp</span>'
    + '<span class="' + vizCls(r.pct) + '">' + vizFmt(r.pct) + '%</span></div></div>';
  // 排序与表格保持一致：前10% 按区间 Δ 降序；后10% 按区间 Δ 升序；两栏都把退出行垫底。
  const col = (title, list, asc) => {
    const sorted = list.slice().sort((a, b) => {
      if (!!a.isExit !== !!b.isExit) return a.isExit ? 1 : -1;
      return asc ? (a.dw - b.dw) : (b.dw - a.dw);
    });
    return '<div class="viz-col"><div class="viz-col-hd">' + esc(title) + '（' + sorted.length + '）</div>'
      + '<div class="viz-grid">' + sorted.map(cell).join('') + '</div></div>';
  };
  if (idxMode) {
    host.innerHTML = '<div class="viz-grids one">' + col(levelName + ' · 全部', rows, false) + '</div>';
  } else {
    host.innerHTML = '<div class="viz-grids">'
      + col(levelName + ' · 前10%', rows.filter((r) => r.side === 'in'), false)
      + col(levelName + ' · 后10%', rows.filter((r) => r.side === 'out'), true) + '</div>';
  }
  vizBind(host, rows, o);
  return host;
}

/* ---------------------------------------------------------------- 四象（8002 vizQuad 逐字） */

/* 尾迹存在感三档（8002 常量，主人 2026-10-02 定的值）：常显 / 有高亮目标时其余 / 目标自身 1。 */
const TRAIL_IDLE = 0.26, TRAIL_DIM = 0.06;
const LAB_STEP_MAX = 13.5, LAB_STEP_MIN = 10;
const EPS = 1e-3;
const K_TRAIL = 10;   // 尾迹窗口＝近 10 交易日（2026-10-02 主人：30 日太长）

/**
 * 四象 · 气泡图（8002 `vizQuad` app.js:2783 逐字搬运，400 行）。
 *
 * x＝权重水平（当期权重 ÷ 截面中位，对数轴，1＝中位线）；y＝区间权重变化 Δ(pp，累计量，0 基线）。
 * 象限：右上领先 / 左上改善 / 左下落后 / 右下走弱。半径＝权重（面积∝权重）；
 * 颜色＝活跃涨跌（红涨绿跌）；边框＝榜单（前10%实线 / 后10%虚线）。
 *
 * @param {object} o
 * @param {Array}  o.rows      vizRows() 结果（side='all' 视为指数层）
 * @param {object} o.C         compute 实例（C.weight / C.dayB / C.dayA）
 * @param {object} o.flowMap   code -> {bull}
 * @param {string} o.levelName
 * @param {number|string} o.vizTopQ  10 | 20 | 'all'
 * @param {function} o.onTopQ / o.onOpen / o.onRerender / o.tipShow / o.tipHide
 */
export function renderQuad(o) {
  const { rows, C, flowMap, levelName } = o;
  const host = el('div');
  if (!rows.length) {
    host.append(el('div', { class: 'viz-cap' }, '该层暂无可视化数据'));
    return host;
  }
  const S = renderQuad._s || (renderQuad._s = { pins: [], hover: null });
  if (!S.pins) S.pins = [];

  const W = Math.max(720, Math.round(host.clientWidth || o.width || 760));  // 按容器实宽出图
  const MT = 30, MB = 34, ML = 56;
  const cap = Math.max(460, (window.innerHeight || 900) - 230);
  const plotH = Math.min(Math.max(460, Math.ceil(rows.length * LAB_STEP_MAX + 16)), cap);
  const step = Math.min(LAB_STEP_MAX, Math.max(LAB_STEP_MIN, (plotH - 16) / Math.max(1, rows.length)));
  const MR = 158;
  const plotW = W - ML - MR;
  const H = MT + plotH + MB;

  // --- x：权重 ÷ 截面中位（对数轴） ---
  const ws = rows.map((r) => (r.weight > 0 ? r.weight : 0)).sort((a, b) => a - b);
  let med = ws.length ? ws[Math.floor(ws.length / 2)] : 0;
  if (!(med > 0)) med = ws.length ? ws[ws.length - 1] || 1 : 1;
  const ratio = (r) => Math.max((r.weight > 0 ? r.weight : EPS * med) / med, EPS);
  const lgs = rows.map(ratio).map((v) => Math.log10(v));
  let lLo = Math.min.apply(null, lgs.concat([0])), lHi = Math.max.apply(null, lgs.concat([0]));
  const lpd = (lHi - lLo) * 0.08 || 0.3; lLo -= lpd; lHi += lpd;
  const X = (rt) => ML + (Math.log10(Math.max(rt, EPS)) - lLo) / (lHi - lLo) * plotW;

  // --- y：区间权重变化 Δ(pp，累计量) ---
  const dys = rows.map((r) => r.dw);
  let yLo = Math.min.apply(null, dys.concat([0])), yHi = Math.max.apply(null, dys.concat([0]));
  const ypd = (yHi - yLo) * 0.12 || 0.2; yLo -= ypd; yHi += ypd;
  const Y = (v) => MT + plotH * (1 - (v - yLo) / (yHi - yLo));

  // --- 半径：面积∝权重（sqrt 缩放），最大约 26px ---
  const maxW = Math.max.apply(null, rows.map((r) => r.weight).concat([med]));
  const kk = 26 / Math.sqrt(maxW > 0 ? maxW : 1);
  const R = (r) => Math.max(4, Math.min(28, kk * Math.sqrt(Math.max(r.weight, 0.0001))));

  const fillOf = (r) => (r.pct == null ? 'var(--dim)' : (r.pct > 0 ? 'var(--up-solid)' : (r.pct < 0 ? 'var(--down-solid)' : 'var(--dim)')));
  const dashOf = (r) => (r.side === 'out' ? ' stroke-dasharray="4 3"' : '');
  const quadOf = (rt, dw) => (rt >= 1 ? (dw >= 0 ? '领先' : '走弱') : (dw >= 0 ? '改善' : '落后'));
  const short = (s) => { s = String(s); return s.length > 8 ? s.slice(0, 8) + '…' : s; };
  const f2 = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2));
  const keyOf = (r) => r.side + '|' + r.code;

  // 钉住集合随榜单变化自动收敛（换层级/区间后旧键可能已不在集里）
  S.pins = S.pins.filter((k) => rows.some((r) => keyOf(r) === k));
  if (S.hover && !rows.some((r) => keyOf(r) === S.hover)) S.hover = null;

  // --- 落点（含裁边标记 _ov，供浮标提示） ---
  const pts = rows.map((r) => {
    const rt = ratio(r);
    let cx = X(rt), cy = Y(r.dw), ov = false;
    const rr = R(r);
    if (cx < ML + rr) { cx = ML + rr; ov = true; }
    if (cx > ML + plotW - rr) { cx = ML + plotW - rr; ov = true; }
    if (cy < MT + rr) { cy = MT + rr; ov = true; }
    if (cy > MT + plotH - rr) { cy = MT + plotH - rr; ov = true; }
    if (ov) r._ov = true; else delete r._ov;
    return { r, rt, cx, cy, rr, fill: fillOf(r), q: quadOf(rt, r.dw) };
  });

  // --- 历史尾迹数据（近 10 交易日）---
  // 基准日 DA：8002 读全局 state.dayA。v2 的等价物就是「当前日 − 比较周期」；
  // compute.js 不暴露 dayA（它是验收基线，不动），所以由调用方传 o.dayA，
  // 没传时回退 C.dayB − C.winN（与 frontend.js 的 _effectiveWin 同义）。
  const wMat = C.weight, DB = C.dayB;
  const DA = (o.dayA != null) ? o.dayA : Math.max(0, DB - (C.winN || 1));
  const tDays = [];
  for (let d = Math.max(0, DB - K_TRAIL + 1); d <= DB; d++) tDays.push(d);
  const wPct = (d, j) => (wMat && wMat[d] && j != null && wMat[d][j] != null) ? wMat[d][j] / 1e4 : null;
  const medDays = tDays.map((d) => {
    const arr = [];
    rows.forEach((r) => { const v = wPct(d, r.j); if (v != null && v > 0) arr.push(v); });
    if (!arr.length) return 0;
    arr.sort((a, b) => a - b);
    return arr[Math.floor(arr.length / 2)];
  });
  const ptsByKey = new Map(pts.map((p) => [keyOf(p.r), p]));

  /* 折线弧长游走器：{L: 总长, at(s): 取弧长 s 处坐标}。ribbon 在用。 */
  const arcWalk = (tp) => {
    const segs = [];
    let L = 0;
    for (let i = 1; i < tp.length; i++) {
      const len = Math.hypot(tp[i].x - tp[i - 1].x, tp[i].y - tp[i - 1].y);
      segs.push({ a: tp[i - 1], b: tp[i], len, s0: L });
      L += len;
    }
    const at = (s) => {
      let g = segs[segs.length - 1];
      for (let i = 0; i < segs.length; i++) { if (s <= segs[i].s0 + segs[i].len) { g = segs[i]; break; } }
      const f = g.len > 0 ? Math.min(1, Math.max(0, (s - g.s0) / g.len)) : 0;
      return { x: g.a.x + (g.b.x - g.a.x) * f, y: g.a.y + (g.b.y - g.a.y) * f };
    };
    return { L, at };
  };
  /* 变宽带（ribbon）：把「线径表示时间」做成一个闭合 path（沿折线细采样 → 逐点法线 → 左右偏移）。 */
  const ribbon = (tp, w0, w1, n) => {
    if (!tp || tp.length < 2) return '';
    const { L, at } = arcWalk(tp);
    if (!(L > 0)) return '';
    const N = Math.max(8, n || 40);
    const P = [];
    for (let k = 0; k <= N; k++) { const q = at(L * k / N); q.t = k / N; P.push(q); }
    const lf = [], rt = [];
    for (let k = 0; k <= N; k++) {
      const a = P[Math.max(0, k - 1)], b = P[Math.min(N, k + 1)];
      let dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1;
      dx /= d; dy /= d;
      const h = (w0 + (w1 - w0) * P[k].t) / 2;
      lf.push([P[k].x - dy * h, P[k].y + dx * h]);
      rt.push([P[k].x + dy * h, P[k].y - dx * h]);
    }
    const poly = lf.concat(rt.reverse());
    return poly.map((q, i) => (i ? 'L' : 'M') + q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join(' ') + ' Z';
  };
  const trailOf = (r) => {
    const base = wPct(DA, r.j);
    if (base == null) return [];
    const out = [];
    tDays.forEach((d, i) => {
      const v = wPct(d, r.j), md = medDays[i];
      if (v == null || !(md > 0)) return;
      out.push({ d, x: X(Math.max(v / md, EPS)), y: Y(v - base) });
    });
    // 终点强制对齐气泡：尾迹末点与气泡是同一笔数据，必须重合。
    const pp = ptsByKey.get(keyOf(r));
    if (out.length && pp && out[out.length - 1].d === DB) out[out.length - 1] = { x: pp.cx, y: pp.cy };
    return out;
  };

  // 表格口径排序：前10%在前（Δ降序）/ 后10%在后（Δ升序）/ 各组退出行垫底；指数层保持入参原序。
  const isIdx = !rows.some((r) => r.side === 'in' || r.side === 'out');
  const seq = pts.slice().sort((a, b) => {
    if (a.r.side !== b.r.side) return a.r.side === 'in' ? -1 : 1;
    const ae = a.r.isExit ? 1 : 0, be = b.r.isExit ? 1 : 0;
    if (ae !== be) return ae - be;
    return a.r.side === 'in' ? (b.r.dw - a.r.dw) : (a.r.dw - b.r.dw);
  });
  const draw = isIdx ? pts : seq;

  // --- 右侧标签（单栏）：钉住的置顶，其余按 y 顺序堆叠；超出画布的自然被裁 ---
  const pinList = S.pins || [];
  const labs = pts.map((p) => ({ p, y: p.cy, pin: pinList.indexOf(keyOf(p.r)) >= 0 }))
    .sort((a, b) => (a.pin === b.pin ? a.y - b.y : (a.pin ? -1 : 1)));
  let labCursor = MT + 6;
  labs.forEach((l) => { l.y = labCursor; labCursor += step; });
  const labShown = labs.filter((l) => l.y <= MT + plotH + 6).length;

  // 多空键集合（AMV 双轨态，取自 flowMap，不自建）
  const bullKeys = (bull) => rows.filter((r) => !!((flowMap[r.code] || {}).bull) === bull).map((r) => keyOf(r));

  // --- 象限计数 ---
  const cnt = { '领先': 0, '改善': 0, '落后': 0, '走弱': 0 };
  pts.forEach((p) => { cnt[p.q]++; });

  // --- 组装 ---
  const out = [];
  const topQ = o.vizTopQ == null ? 10 : o.vizTopQ;
  out.push('<div class="vq-bar"><span class="vq-k">气泡＝权重（越大越重）</span>'
    + '<span class="vq-k"><i class="vq-dot" style="background:var(--up-solid)"></i>涨'
    + ' <i class="vq-dot" style="background:var(--down-solid)"></i>跌'
    + ' <i class="vq-dot" style="background:var(--dim)"></i>缺值</span>'
    + '<span class="vq-k"><span class="vq-eg vq-solid"></span>前10%榜'
    + ' <span class="vq-eg vq-dash"></span>后10%榜</span>'
    + '<span class="vq-k">中位 ' + med.toFixed(2) + '%</span>'
    + '<span class="vq-k">Top N</span><span class="vz-grp" id="vqTop">'
    + [10, 20, 'all'].map((n) => '<button type="button" data-n="' + n + '"' + (topQ === n ? ' class="on"' : '') + '>'
      + (n === 'all' ? '全部' : n) + '</button>').join('')
    + '</span>'
    + '<span class="vq-k">多空</span><span class="vz-grp" id="vqBull">'
    + [['1', '多'], ['0', '空']].map(([v, t]) => {
      const ks = bullKeys(v === '1');
      const on = ks.length > 0 && ks.every((k) => S.pins.indexOf(k) >= 0);
      return '<button type="button" data-bull="' + v + '"' + (on ? ' class="on"' : '')
        + ' title="一键把「' + t + '」的气泡全部钉住 / 再点恢复（多空＝AMV 双轨态）">' + t + '</button>';
    }).join('')
    + '</span>'
    + '<span class="vq-hint">悬停看走势 · 点气泡钉住 · 点板块名开详情卡</span></div>');

  // 不输出 SVG <title>：浏览器会把它当原生悬停提示弹出（8002 2026-10-08 主人的裁定）。
  out.push('<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(levelName) + '四象限气泡图，' + rows.length + ' 个板块">');
  out.push('<defs><clipPath id="vqClip"><rect x="' + ML + '" y="' + MT + '" width="' + plotW + '" height="' + plotH + '"/></clipPath></defs>');

  // y 网格 + 刻度
  for (let t = 0; t <= 5; t++) {
    const v = yLo + (yHi - yLo) * t / 5, y = Y(v);
    out.push('<line class="vq-grid" x1="' + ML + '" y1="' + y.toFixed(1) + '" x2="' + (ML + plotW) + '" y2="' + y.toFixed(1) + '"/>');
    out.push('<text class="vq-tx" x="' + (ML - 6) + '" y="' + (y + 3.3).toFixed(1) + '" text-anchor="end">' + v.toFixed(2) + '</text>');
  }
  // x 网格（对数候选刻度）
  [0.125, 0.25, 0.5, 1, 2, 4, 8, 16].forEach((v) => {
    const lv = Math.log10(v);
    if (lv < lLo || lv > lHi) return;
    const x = X(v);
    out.push('<line class="vq-grid" x1="' + x.toFixed(1) + '" y1="' + MT + '" x2="' + x.toFixed(1) + '" y2="' + (MT + plotH) + '"/>');
    out.push('<text class="vq-tx" x="' + x.toFixed(1) + '" y="' + (MT + plotH + 15) + '" text-anchor="middle">×' + v + '</text>');
  });
  // 象限四角词（中性墨色，不用红绿防误读）
  const qLab = [['领先', ML + plotW - 8, MT + 14, 'end'], ['改善', ML + 8, MT + 14, 'start'],
    ['落后', ML + 8, MT + plotH - 8, 'start'], ['走弱', ML + plotW - 8, MT + plotH - 8, 'end']];
  qLab.forEach(([t, x, y, an]) => out.push('<text class="vq-q" x="' + x + '" y="' + y + '" text-anchor="' + an + '">' + t + ' ×' + cnt[t] + '</text>'));
  // 轴线：x＝1（中位线）、y＝0
  const x1 = X(1), y0 = Y(0);
  out.push('<line class="vq-axis" x1="' + x1.toFixed(1) + '" y1="' + MT + '" x2="' + x1.toFixed(1) + '" y2="' + (MT + plotH) + '"/>');
  out.push('<line class="vq-axis" x1="' + ML + '" y1="' + y0.toFixed(1) + '" x2="' + (ML + plotW) + '" y2="' + y0.toFixed(1) + '"/>');
  out.push('<text class="vq-axn" x="' + (ML - 6) + '" y="' + (MT - 8) + '" text-anchor="end">区间Δ pp</text>');
  out.push('<text class="vq-axn" x="' + (ML + plotW) + '" y="' + (MT + plotH + 28) + '" text-anchor="end">权重水平（÷截面中位，对数轴）→</text>');

  // ① 尾迹层（最底；悬停/钉住才显形）——近 10 交易日
  out.push('<g clip-path="url(#vqClip)">');
  draw.forEach((p) => {
    const tp = trailOf(p.r);
    if (tp.length < 2) return;
    const d = tp.map((q, i) => (i ? 'L' : 'M') + q.x.toFixed(1) + ' ' + q.y.toFixed(1)).join(' ');
    out.push('<g class="vq-trail" data-vkey="' + keyOf(p.r) + '" data-nocard style="opacity:' + TRAIL_IDLE + ';pointer-events:none">');
    const w1 = 0.6 * p.rr;                      // 头宽 = 0.3 × (2R)
    const w0 = Math.max(0.7, w1 * 0.25);        // 尾宽
    const rib = ribbon(tp, w0, w1, 40);
    if (rib) out.push('<path d="' + rib + '" fill="' + p.fill
      + '" style="fill:color-mix(in srgb, ' + p.fill + ' 55%, var(--card))" opacity="0.5"/>');
    // 双层流星：外晕 .vq-tg 在后（动画滞后 0.22s）、亮芯 .vq-tf 领先
    out.push('<path class="vq-tg" d="' + d + '" pathLength="100" style="stroke:color-mix(in srgb, ' + p.fill + ' 45%, var(--card));opacity:0"/>');
    out.push('<path class="vq-tf" d="' + d + '" pathLength="100" style="stroke:' + p.fill + ';opacity:0"/>');
    out.push('</g>');
  });
  out.push('</g>');

  // ② 气泡层（点击＝钉住 ⇒ data-nocard 让 vizBind 不绑「点击开卡」）
  draw.forEach((p) => {
    const k = keyOf(p.r);
    const dim = p.r.isExit ? ' opacity="0.45"' : '';
    out.push('<g class="vq-b" data-vkey="' + k + '" data-nocard tabindex="0" role="button"' + dim
      + ' aria-label="' + esc(p.r.name) + '，' + p.q + '，权重 ' + p.r.weight.toFixed(2) + '%，区间变化 ' + f2(p.r.dw) + ' pp，活跃涨跌 ' + f2(p.r.pct) + '%">');
    out.push('<circle class="vq-ring" cx="' + p.cx.toFixed(1) + '" cy="' + p.cy.toFixed(1) + '" r="' + (p.rr + 3.5).toFixed(1) + '" fill="none" style="stroke:var(--accent)" stroke-width="1.4" stroke-dasharray="3 2"/>');
    out.push('<circle cx="' + p.cx.toFixed(1) + '" cy="' + p.cy.toFixed(1) + '" r="' + p.rr.toFixed(1) + '" style="fill:' + p.fill + ';stroke:' + p.fill + '" fill-opacity="0.80" stroke-width="1.6"' + dashOf(p.r) + '/>');
    if (p.r.isExit) out.push('<text class="vq-x" x="' + p.cx.toFixed(1) + '" y="' + (p.cy + 3.5).toFixed(1) + '" text-anchor="middle">✕</text>');
    out.push('</g>');
  });

  // ③′ 已钉住气泡的就地板块名（8 向候选位贪心避让 + 引导线；字符估宽：汉字 10.5px、其余 6.1px）
  const blabItems = [];
  draw.forEach((p) => {
    if (pinList.indexOf(keyOf(p.r)) < 0) return;
    const tx = esc(short(p.r.name));
    let tw = 0;
    for (let i = 0; i < tx.length; i++) tw += tx.charCodeAt(i) > 0x2e80 ? 10.5 : 6.1;
    blabItems.push({ p, tx, w: tw, h: 10.5 });
  });
  const blabBoxes = pts.map((p) => ({ x: p.cx - p.rr - 1.5, y: p.cy - p.rr - 1.5, w: 2 * (p.rr + 1.5), h: 2 * (p.rr + 1.5) }));
  const blabIn = (b) => (b.x >= ML - 1 && b.y >= MT - 1 && b.x + b.w <= ML + plotW + 1 && b.y + b.h <= MT + plotH + 1);
  const blabHit = (a, b) => !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
  const blabDirs = [[1, 0], [-1, 0], [0, -1], [0, 1], [1, -1], [-1, -1], [1, 1], [-1, 1]];
  const blabPlaced = [];
  blabItems.slice().sort((a, b) => b.p.r.weight - a.p.r.weight).forEach((it) => {
    const p = it.p, lw = it.w, lh = it.h;
    let chosen = null;
    for (let ring = 1; ring <= 4 && !chosen; ring++) {
      for (let d = 0; d < blabDirs.length && !chosen; d++) {
        const dx = blabDirs[d][0], dy = blabDirs[d][1];
        const gx = (dx !== 0 && dy !== 0) ? p.cx + (p.rr * 0.72 + 4) * ring * dx : p.cx + (p.rr + 6) * ring * dx;
        const gy = (dx !== 0 && dy !== 0) ? p.cy + (p.rr * 0.72 + 4) * ring * dy : p.cy + (p.rr + 6) * ring * dy;
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
        if (!blabIn(r)) continue;
        let clash = false;
        for (let b = 0; b < blabBoxes.length; b++) { if (blabHit(r, blabBoxes[b])) { clash = true; break; } }
        if (!clash) chosen = r;
      }
    }
    if (!chosen) {   // 全冲突 ⇒ 甩到空白区，下面用引导线连回气泡
      const cands = [[ML + plotW - lw - 2, p.cy - lh / 2], [ML + plotW - lw - 2, p.cy + p.rr + 8], [ML + plotW - lw - 2, p.cy - p.rr - 8 - lh],
        [ML + 2, p.cy - lh / 2], [ML + 2, p.cy + p.rr + 8], [ML + 2, p.cy - p.rr - 8 - lh], [p.cx - lw / 2, MT + 2], [p.cx - lw / 2, MT + plotH - lh - 2]];
      for (let q = 0; q < cands.length && !chosen; q++) {
        const cb = { x: cands[q][0], y: cands[q][1], w: lw, h: lh };
        if (!blabIn(cb)) continue;
        let c2 = false;
        for (let b2 = 0; b2 < blabBoxes.length; b2++) { if (blabHit(cb, blabBoxes[b2])) { c2 = true; break; } }
        if (!c2) chosen = cb;
      }
      if (!chosen) chosen = { x: ML + plotW - lw - 2, y: Math.min(MT + plotH - lh - 2, Math.max(MT + 2, p.cy - lh / 2)), w: lw, h: lh };
    }
    blabBoxes.push({ x: chosen.x, y: chosen.y, w: chosen.w, h: chosen.h });
    blabPlaced.push({ it, r: chosen });
  });
  blabPlaced.forEach((pl) => {
    const p = pl.it.p, r = pl.r;
    const nx = Math.max(r.x, Math.min(p.cx, r.x + r.w)), ny = Math.max(r.y, Math.min(p.cy, r.y + r.h));
    const dx = nx - p.cx, dy = ny - p.cy, dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > p.rr + 8) {
      const ang = Math.atan2(dy, dx) || 0;
      out.push('<line class="vq-lead" x1="' + (p.cx + Math.cos(ang) * (p.rr + 2)).toFixed(1) + '" y1="' + (p.cy + Math.sin(ang) * (p.rr + 2)).toFixed(1)
        + '" x2="' + nx.toFixed(1) + '" y2="' + ny.toFixed(1) + '"/>');
    }
  });
  blabPlaced.forEach((pl) => {
    const lx = (pl.r.x + 1).toFixed(1), ly = (pl.r.y + pl.r.h / 2).toFixed(1);
    out.push('<text class="vq-blab-h" x="' + lx + '" y="' + ly + '">' + pl.it.tx + '</text>');
    out.push('<text class="vq-blab" x="' + lx + '" y="' + ly + '" style="fill:' + pl.it.p.fill + '">' + pl.it.tx + '</text>');
  });

  // ③ 右侧板块名（无引线；点击＝开详情卡）
  labs.forEach((l) => {
    const p = l.p, lx = ML + plotW + 2;
    out.push('<g class="vq-l" data-vkey="' + keyOf(p.r) + '" tabindex="0" role="button" aria-label="打开 ' + esc(p.r.name) + ' 详情">');
    out.push('<rect class="vq-hit" x="' + lx + '" y="' + (l.y - step / 2).toFixed(1) + '" width="150" height="' + step.toFixed(1) + '"/>');
    out.push('<text class="vq-lab" x="' + (lx + 6) + '" y="' + (l.y + 3.5).toFixed(1) + '" style="fill:' + p.fill + '">' + esc(short(p.r.name)) + ' ' + f2(p.r.dw) + '</text>');
    out.push('</g>');
  });
  out.push('</svg>');
  out.push('<div class="vq-foot">右上领先（大而走强）· 左上改善（小而走强）· 左下落后（小而走弱）· 右下走弱（大而走弱）｜ 退出行半透明 ✕ ｜ 尾迹＝近 10 交易日（线径由细到粗＝由旧到新，头宽＝气泡直径 30%）｜ 标签 ' + labShown + '/' + rows.length + '（点气泡钉住可置顶）｜ ' + rows.length + ' 个气泡</div>');
  host.innerHTML = out.join('');

  // --- 交互：悬停高亮联动；点气泡＝钉住（多钉）---
  const anyHl = () => S.pins.length > 0 || !!S.hover;
  const isOn = (k) => S.pins.indexOf(k) >= 0 || k === S.hover;
  const applyHl = () => {
    host.querySelectorAll('.vq-b, .vq-l').forEach((g) => {
      const k = g.dataset.vkey, on = isOn(k);
      g.classList.toggle('vq-dim', anyHl() && !on);
      g.classList.toggle('vq-hi', on);
      g.classList.toggle('vq-pin', S.pins.indexOf(k) >= 0);
    });
    host.querySelectorAll('.vq-trail').forEach((g) => {
      const on = isOn(g.dataset.vkey);
      g.style.opacity = on ? '1' : (anyHl() ? String(TRAIL_DIM) : String(TRAIL_IDLE));
      const tfs = g.querySelectorAll('.vq-tf, .vq-tg');
      tfs.forEach((n) => {
        const glow = n.classList.contains('vq-tg');
        n.style.opacity = on ? (glow ? '0.22' : '0.85') : '0';   // 流动层只在目标上显形
        n.style.animation = on ? ('vqFlow 1.8s linear ' + (glow ? '0.22s' : '0s') + ' infinite') : 'none';
      });
    });
  };
  host.querySelectorAll('.vq-b, .vq-l').forEach((g) => {
    const k = g.dataset.vkey;
    g.addEventListener('mouseenter', () => { S.hover = k; applyHl(); });
    g.addEventListener('mouseleave', () => { S.hover = null; applyHl(); });
    g.addEventListener('focus', () => { S.hover = k; applyHl(); });
    g.addEventListener('blur', () => { S.hover = null; applyHl(); });
  });
  host.querySelectorAll('.vq-b').forEach((g) => {
    const k = g.dataset.vkey;
    const toggle = () => {
      const i = S.pins.indexOf(k);
      if (i >= 0) S.pins.splice(i, 1); else S.pins.push(k);
      if (o.onRerender) o.onRerender();   // 重渲：钉住的标签要立刻升到最上面
    };
    g.addEventListener('click', toggle);
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  });
  applyHl();

  // Top 档
  const tb = host.querySelector('#vqTop');
  if (tb) tb.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    if (o.onTopQ) o.onTopQ(b.dataset.n === 'all' ? 'all' : Number(b.dataset.n));
  }));
  // 多空一键高亮（不互斥：每个按钮独立开关）
  const bb = host.querySelector('#vqBull');
  if (bb) bb.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
    const ks = bullKeys(b.dataset.bull === '1');
    if (!ks.length) return;
    const allIn = ks.every((k) => S.pins.indexOf(k) >= 0);
    if (allIn) S.pins = S.pins.filter((k) => ks.indexOf(k) < 0);
    else ks.forEach((k) => { if (S.pins.indexOf(k) < 0) S.pins.push(k); });
    if (o.onRerender) o.onRerender();
  }));

  vizBind(host, rows, o);   // 悬停浮标 + 右侧板块名点击开卡（气泡带 data-nocard，不绑开卡）
  return host;
}
