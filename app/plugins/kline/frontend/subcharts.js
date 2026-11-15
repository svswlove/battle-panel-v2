// 副图渲染（2026-10-27 · P1c）：canvas 画成交量/成交额、活跃市值、KDJ、MACD、RSI。
//
// 职责：只画，不算。指标序列由 indicators.js 给，横轴由主图唯一轴（dailyAxis / intradayAxis）给。
//   **横轴不另起一套**：本文件所有 x 都调 axis.xAt(i, n)，与主图蜡烛逐根对齐；
//   自己只决定「纵轴」——每个副图按自己的 min/max 缩放，这是副图固有的第二坐标，不是第二套时间轴。
//
// 空数据口径：序列全 null / 长度不匹配 / canvas 尺寸为 0 → 画一行「不可用：原因」，不画 0 线、不画假柱。
// 上下游：overlay.js（装配）← 本文件 ← indicators.js；本文件不读网络、不读 localStorage。

const PAD_T = 4, PAD_B = 4, PAD_L = 56, PAD_R = 8;

function setup(canvas) {
  const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
  const w = canvas.clientWidth || 640;
  const h = canvas.clientHeight || 90;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const c = canvas.getContext('2d');
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.scale(dpr, dpr);
  c.clearRect(0, 0, w, h);
  return { c: c, w: w, h: h };
}

function niceRange(values) {
  let lo = null, hi = null;
  values.forEach(function (v) {
    if (v === null || !Number.isFinite(v)) return;
    if (lo === null || v < lo) lo = v;
    if (hi === null || v > hi) hi = v;
  });
  if (lo === null) return null;
  if (lo === hi) { lo -= 1; hi += 1; }         // 全平：给 2 个单位，避免除零
  return { lo: lo, hi: hi };
}

function frame(c, w, h, colors) {
  c.strokeStyle = colors.grid || colors.dim;
  c.globalAlpha = 0.35;
  c.lineWidth = 1;
  c.beginPath();
  c.moveTo(PAD_L, h - PAD_B + 0.5);
  c.lineTo(w - PAD_R, h - PAD_B + 0.5);
  c.stroke();
  c.globalAlpha = 1;
}

function title(c, text, colors) {
  c.font = '10px ' + (colors.mono || 'monospace');
  c.fillStyle = colors.dim;
  c.textAlign = 'left';
  c.fillText(text, 6, 11);
}

function notice(c, text, colors, w, h) {
  c.font = '11px ' + (colors.mono || 'monospace');
  c.fillStyle = colors.dim;
  c.textAlign = 'left';
  c.fillText(text, 6, Math.max(14, h / 2));
}

function polyline(c, values, axis, yOf, color, width) {
  c.strokeStyle = color;
  c.lineWidth = width || 1;
  c.beginPath();
  let started = false;
  const n = values.length;
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v === null || !Number.isFinite(v)) { started = false; continue; }
    const x = axis.xAt(i, n), y = yOf(v);
    if (y === null) { started = false; continue; }
    if (!started) { c.moveTo(x, y); started = true; } else { c.lineTo(x, y); }
  }
  c.stroke();
}

function columnSeries(c, values, axis, yOf0, yOf, colors, w) {
  const n = values.length;
  const bw = Math.max(1, Math.min(9, ((axis.rect.r - axis.rect.l) / Math.max(n, 1)) * 0.62));
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (v === null || !Number.isFinite(v)) continue;
    const x = axis.xAt(i, n);
    const y0 = yOf0(), y1 = yOf(v);
    if (y1 === null || y0 === null) continue;
    c.fillStyle = v >= 0 ? colors.up : colors.down;
    c.globalAlpha = 0.75;
    c.fillRect(Math.min(x - bw / 2, axis.rect.r), Math.min(y0, y1), bw, Math.max(1, Math.abs(y1 - y0)));
    c.globalAlpha = 1;
  }
}

/**
 * 画一个副图。
 * @param {HTMLCanvasElement} canvas
 * @param {string} key amount|amv|kdj|macd|rsi
 * @param {object} o {bars, axis, colors, series（已算好的指标，可省）}
 * @returns {object} {ok:boolean, reason?:string}
 */
export function drawSub(canvas, key, o) {
  const opts = o || {};
  const colors = opts.colors || {};
  const axis = opts.axis;
  const bars = opts.bars || [];
  const { c, w, h } = setup(canvas);
  const innerW = w - PAD_L - PAD_R;

  if (!axis || !bars.length) {
    notice(c, '不可用：没有 K 线数据', colors, w, h);
    return { ok: false, reason: '没有 K 线数据' };
  }
  if (innerW <= 4) {
    notice(c, '不可用：宽度不足', colors, w, h);
    return { ok: false, reason: '宽度不足' };
  }

  const top = PAD_T + 12;
  const bottom = h - PAD_B;
  const plotH = Math.max(1, bottom - top);
  const mkY = function (range) {
    return function (v) {
      return bottom - plotH * ((v - range.lo) / (range.hi - range.lo));
    };
  };

  if (key === 'amount') {
    const vals = opts.series && opts.series.amount ? opts.series.amount : bars.map(function (b) { return (b.amount === null || b.amount === undefined) ? null : Number(b.amount); });
    if (!vals.some(function (v) { return v !== null && Number.isFinite(v); })) {
      notice(c, '不可用：无成交额字段', colors, w, h);
      return { ok: false, reason: '无成交额字段' };
    }
    const r = niceRange(vals.concat([0]));
    const yOf = mkY(r);
    frame(c, w, h, colors);
    columnSeries(c, vals, axis, function () { return bottom; }, yOf, colors, w);
    // 右轴上下限（成交额口径原样透传，不在这里换单位）
    c.font = '9px ' + (colors.mono || 'monospace');
    c.fillStyle = colors.dim;
    c.textAlign = 'right';
    c.fillText(String(Math.round(r.hi)), w - PAD_R, top + 7);
    title(c, '成交额', colors);
    return { ok: true };
  }

  if (key === 'mf') {
    const d = opts.series && opts.series.mf;
    if (!d || !d.ok) {
      notice(c, '不可用：' + ((d && d.reason) || '无主力动向数据'), colors, w, h);
      return { ok: false, reason: (d && d.reason) || '无主力动向数据' };
    }
    // 旧版四层：主力资金柱（红/绿）＋主买细柱（×0.7）＋占成交/占流通（数值入图例）＋Q
    const all = d.main.concat(d.buy.map(function (v) { return v === null ? null : v * 0.7; }), [0]);
    const r = niceRange(all);
    const yOf = mkY(r);
    const y0 = yOf(0);
    frame(c, w, h, colors);
    columnSeries(c, d.main, axis, function () { return y0; }, yOf, colors, w);
    const n2 = d.buy.length;
    const bw = Math.max(1, Math.min(4, ((axis.rect.r - axis.rect.l) / Math.max(n2, 1)) * 0.3));
    for (let i = 0; i < n2; i++) {
      const v = d.buy[i];
      if (v === null || !Number.isFinite(v)) continue;
      const x = axis.xAt(i, n2), y = yOf(v * 0.7);
      if (y === null) continue;
      c.fillStyle = colors.warn; c.globalAlpha = 0.9;
      c.fillRect(x - bw / 2, Math.min(y0, y), bw, Math.max(1, Math.abs(y - y0)));
      c.globalAlpha = 1;
    }
    const i2 = lastIdx(d.main);
    title(c, '主力动向  占成交 ' + fmt(d.zc[i2]) + '%  占流通 ' + fmt(d.zlt[i2]) + '%  Q ' + fmt(d.q[i2]), colors);
    return { ok: true };
  }

  if (key === 'amv') {
    // 两种入参都认：series.amv = {amv,ma,white}（overlay 用法）或直接是数组（单测/简用）
    const raw = opts.series && opts.series.amv ? opts.series.amv : null;
    const s = (raw && !Array.isArray(raw)) ? raw : null;
    const main = s ? (s.amv || []) : (Array.isArray(raw) ? raw : bars.map(function (b) { return b.amv === undefined ? null : b.amv; }));
    if (!main.some(function (v) { return v !== null && Number.isFinite(v); })) {
      notice(c, '不可用：该周期无活跃市值（仅日线有）', colors, w, h);
      return { ok: false, reason: '该周期无活跃市值' };
    }
    const all = main.concat(s ? (s.ma || []) : [], s ? (s.white || []) : []);
    const r = niceRange(all);
    const yOf = mkY(r);
    frame(c, w, h, colors);
    if (s && s.ma) polyline(c, s.ma, axis, yOf, colors.warn, 1);
    if (s && s.white) polyline(c, s.white, axis, yOf, colors.fg, 0.8);
    polyline(c, main, axis, yOf, colors.up, 1.4);
    title(c, '活跃市值', colors);
    return { ok: true };
  }

  if (key === 'kdj') {
    const k = opts.series && opts.series.kdj;
    if (!k || !k.k || !k.k.some(function (v) { return v !== null; })) {
      notice(c, '不可用：K 线不足，KDJ 无法起算', colors, w, h);
      return { ok: false, reason: 'K 线不足' };
    }
    // 旧版同副图：K/D/J + stochShort + stochLong；J 与 stoch 都可能越出 0–100，故用动态范围
    const all = k.k.concat(k.d, k.j, k.stochShort || [], k.stochLong || []);
    const r = niceRange(all.concat([0, 100]));
    const yOf = mkY(r);
    frame(c, w, h, colors);
    c.strokeStyle = colors.dim; c.globalAlpha = 0.25;
    [20, 80].forEach(function (lv) {
      if (lv < r.lo || lv > r.hi) return;
      const y = yOf(lv);
      c.beginPath(); c.moveTo(axis.rect.l, y); c.lineTo(axis.rect.r, y); c.stroke();
    });
    c.globalAlpha = 1;
    polyline(c, k.k, axis, yOf, colors.fg, 1);
    polyline(c, k.d, axis, yOf, colors.warn, 1);
    polyline(c, k.j, axis, yOf, colors.up, 1);
    if (k.stochShort) polyline(c, k.stochShort, axis, yOf, colors.accent || colors.dim, 0.8);
    if (k.stochLong) polyline(c, k.stochLong, axis, yOf, colors.down, 0.8);
    title(c, 'KDJ ' + fmt(lastOf(k.k)) + ' / ' + fmt(lastOf(k.d)) + ' / ' + fmt(lastOf(k.j))
      + '  stoch ' + fmt(lastOf(k.stochShort)) + ' / ' + fmt(lastOf(k.stochLong)), colors);
    return { ok: true };
  }

  if (key === 'macd') {
    const m = opts.series && opts.series.macd;
    if (!m || !m.dif || !m.dif.some(function (v) { return v !== null; })) {
      notice(c, '不可用：K 线不足，MACD 无法起算', colors, w, h);
      return { ok: false, reason: 'K 线不足' };
    }
    const r = niceRange(m.dif.concat(m.dea, m.hist, [0]));
    const yOf = mkY(r);
    const y0 = yOf(0);
    frame(c, w, h, colors);
    // 旧版 hist 原始 = DIF−DEA；柱子直接用原始值（与旧版 pane 的取值一致，不再二次放大）
    columnSeries(c, m.hist, axis, function () { return y0; }, yOf, colors, w);
    polyline(c, m.dif, axis, yOf, colors.fg, 1);
    polyline(c, m.dea, axis, yOf, colors.warn, 1);
    title(c, 'MACD ' + fmt(lastOf(m.dif)) + ' / ' + fmt(lastOf(m.dea)) + ' / ' + fmt(lastOf(m.hist)), colors);
    return { ok: true };
  }

  if (key === 'rsi') {
    const r0 = opts.series && opts.series.rsi;
    const keys = r0 && r0.periods ? r0.periods.map(function (n) { return 'rsi' + n; }) : ['rsi6', 'rsi12', 'rsi24'];
    const any = keys.some(function (kk) { return r0 && r0[kk] && r0[kk].some(function (v) { return v !== null; }); });
    if (!any) {
      notice(c, '不可用：K 线不足，RSI 无法起算', colors, w, h);
      return { ok: false, reason: 'K 线不足' };
    }
    const flatv = [];
    keys.forEach(function (kk) { flatv.push.apply(flatv, r0[kk] || []); });
    const r = niceRange(flatv);
    const yOf = mkY(r);
    frame(c, w, h, colors);
    c.strokeStyle = colors.dim; c.globalAlpha = 0.25;
    [30, 70].forEach(function (lv) {
      if (lv < r.lo || lv > r.hi) return;
      const y = yOf(lv);
      c.beginPath(); c.moveTo(axis.rect.l, y); c.lineTo(axis.rect.r, y); c.stroke();
    });
    c.globalAlpha = 1;
    const cols = [colors.fg, colors.warn, colors.up];
    keys.forEach(function (kk, idx) { polyline(c, r0[kk], axis, yOf, cols[idx % cols.length], 1); });
    title(c, 'RSI ' + keys.map(function (kk) { return fmt(lastOf(r0[kk])); }).join(' / '), colors);
    return { ok: true };
  }

  notice(c, '未实现的副图：' + key, colors, w, h);
  return { ok: false, reason: '未实现' };
}

function lastIdx(arr) {
  if (!Array.isArray(arr)) return 0;
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && Number.isFinite(arr[i])) return i;
  return 0;
}
function lastOf(arr) {
  if (!Array.isArray(arr)) return null;
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && Number.isFinite(arr[i])) return arr[i];
  return null;
}
function fmt(v) {
  return (v === null || !Number.isFinite(v)) ? '—' : Number(v).toFixed(2);
}

/** 副图清单（顺序 = 展示顺序）。数据源情况在 overlay 里判定，这里只画。 */
const SUB_DEFS = [
  { key: 'amount', label: '成交额' },
  { key: 'mf', label: '主力动向（仅日线）' },
  { key: 'amv', label: '活跃市值（仅日线）' },
  { key: 'kdj', label: 'KDJ' },
  { key: 'macd', label: 'MACD' },
  { key: 'rsi', label: 'RSI' },
];

export { SUB_DEFS };
export default { drawSub, SUB_DEFS };
