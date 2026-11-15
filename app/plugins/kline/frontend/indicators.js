// 副图指标计算（2026-10-27 · P1c，第二轮按旧版正本重写）：只做纯数学，不碰 DOM/canvas/网络。
//
// 权威口径：`engine/web/kline_lc.js` 的 Ri / Ti / Si / Mn / Qt / Dt（2026-10-27 核对原文）。
//   **本轮是等价移植，不是「业界近似」**。上一版把 KDJ 做成 9 根预热 + RSV 起手、把 RSI 做成
//   gain/(gain+loss) 的 Wilder 版 —— 与旧版不是同一口径，已按打回意见全部改掉。
//
// 旧版逐条对照（行号为 kline_lc.js）：
//   · Qt(v, prev, s, n) = prev==null ? v : (n*v + (s-n)*prev)/s      —— SMMA；n=1 即 (v+(s-1)prev)/s
//   · Dt(v, prev, n)    = prev + (2/(n+1))*(v-prev)                  —— EMA 递推
//   · Si(bars,i,n)      = (close-LLV(low,n)) / (HHV(high,n)-LLV(low,n)+1e-4) * 100，i<n-1 时 null
//   · Mn(bars,i,n)      = 同上但 HHV 用 **close**（stoch 用 close 高点）
//   · KDJ（Ri）：RSV=Si；K=((K_prev==null?50:K_prev)*2+RSV)/3；D=((D_prev==null?50:D_prev)*2+K)/3；
//     J=3K-2D。**首值就参与，无 9 根预热**（预热只由 Si 的 i>=n-1 决定）。
//     另出 stochShort=Mn(short)、stochLong=Mn(long)，与 J 同副图。
//   · MACD：ema12/ema26 首根取 close；DIF=ema12-ema26；DEA 首根=DIF，此后 Dt(DIF, prev, signal)；
//     **hist 原始值 = DIF-DEA**（旧版 buildSubs 才 ×2，本文件的 macdHist2 单独给 ×2 供柱状展示）。
//   · RSI（Ti）：gain=Qt(up,gain,s,1)、loss=Qt(|ch|,loss,s,1)；i>=s 时 = loss===0 ? 100 : gain/loss*100。
//     注意这是**比值**口径（可 >100），不是 gain/(gain+loss)。
//
// 上下游：subcharts.js（渲染）、overlay.js（装配）← 本文件；本文件不反向依赖任何 UI。
// 空值口径：数组等长 + null 占位，绝不返回 NaN（NaN 在画布上会静默断线）。

const CA = 1e-4;   // 旧版同款防除零常数

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function pick(b, a, bb) {
  if (!b) return null;
  return num(b[a] !== undefined ? b[a] : b[bb]);
}
const closes = (bars) => (bars || []).map((b) => pick(b, 'c', 'close'));
const highs = (bars) => (bars || []).map((b) => pick(b, 'h', 'high'));
const lows = (bars) => (bars || []).map((b) => pick(b, 'l', 'low'));

/** Qt：旧版 SMMA 递推；n=1 时退化为 (v+(s-1)prev)/s。 */
function qSmooth(v, prev, s, n) {
  const S = Math.max(1, s), N = (n == null ? 1 : n);
  return (prev == null) ? v : (N * v + (S - N) * prev) / S;
}
/** Dt：旧版 EMA 递推。 */
function dEma(v, prev, n) {
  const a = 2 / (Math.max(1, n) + 1);
  return prev + a * (v - prev);
}

/** Si：RSV（HHV 用 high）。i < n-1 → null。 */
function rsv(bars, n) {
  const c = closes(bars), h = highs(bars), l = lows(bars);
  const out = new Array(c.length).fill(null);
  const N = Math.max(1, Math.floor(n));
  for (let i = N - 1; i < c.length; i++) {
    let lo = Infinity, hi = -Infinity, bad = false;
    for (let a = i - N + 1; a <= i; a++) {
      if (h[a] === null || l[a] === null || c[a] === null) { bad = true; break; }
      if (l[a] < lo) lo = l[a];
      if (h[a] > hi) hi = h[a];
    }
    if (bad || !Number.isFinite(lo) || !Number.isFinite(hi)) continue;
    out[i] = (c[i] - lo) / (hi - lo + CA) * 100;
  }
  return out;
}
/** Mn：stoch（HHV 用 close）。 */
function stoch(bars, n) {
  const c = closes(bars), l = lows(bars);
  const out = new Array(c.length).fill(null);
  const N = Math.max(1, Math.floor(n));
  for (let i = N - 1; i < c.length; i++) {
    let lo = Infinity, hi = -Infinity, bad = false;
    for (let a = i - N + 1; a <= i; a++) {
      if (c[a] === null || l[a] === null) { bad = true; break; }
      if (l[a] < lo) lo = l[a];
      if (c[a] > hi) hi = c[a];
    }
    if (bad || !Number.isFinite(lo) || !Number.isFinite(hi)) continue;
    out[i] = (c[i] - lo) / (hi - lo + CA) * 100;
  }
  return out;
}

/**
 * KDJ + stochShort/Long（与旧版 Ri 同式）。
 * p: {rsv:9, short:3, long:21}（沿用旧版 SUB_DEFAULT.kdjP）
 */
function kdj(bars, p) {
  const cfg = p || {};
  const rsvN = Math.max(1, Math.floor(num(cfg.rsv) || 9));
  const shortN = Math.max(1, Math.floor(num(cfg.short) || 3));
  const longN = Math.max(1, Math.floor(num(cfg.long) || 21));
  const src = rsv(bars, rsvN);
  const L = src.length;
  const k = new Array(L).fill(null), d = new Array(L).fill(null), j = new Array(L).fill(null);
  for (let i = 0; i < L; i++) {
    const r = src[i];
    if (r === null) continue;
    const pk = i > 0 ? k[i - 1] : null;
    const pd = i > 0 ? d[i - 1] : null;
    const K = ((pk == null ? 50 : pk) * 2 + r) / 3;      // 旧版：无预热，首值即 (100+RSV)/3
    const D = ((pd == null ? 50 : pd) * 2 + K) / 3;
    k[i] = K; d[i] = D; j[i] = 3 * K - 2 * D;
  }
  return {
    rsv: src, k: k, d: d, j: j,
    stochShort: stoch(bars, shortN),
    stochLong: stoch(bars, longN),
    params: { rsv: rsvN, short: shortN, long: longN },
  };
}

/** MACD（旧版 Ri 同式）：hist 为原始 DIF−DEA；另给 hist2 = 2×（旧版 buildSubs 的柱）。 */
function macd(bars, p) {
  const cfg = p || {};
  const fast = Math.max(1, Math.floor(num(cfg.fast) || 12));
  const slow = Math.max(1, Math.floor(num(cfg.slow) || 26));
  const sig = Math.max(1, Math.floor(num(cfg.signal) || 9));
  const c = closes(bars);
  const L = c.length;
  const dif = new Array(L).fill(null), dea = new Array(L).fill(null);
  const hist = new Array(L).fill(null), hist2 = new Array(L).fill(null);
  let e12 = null, e26 = null;
  for (let i = 0; i < L; i++) {
    if (c[i] === null) { e12 = null; e26 = null; continue; }
    e12 = (i === 0 || e12 === null) ? c[i] : dEma(c[i], e12, fast);
    e26 = (i === 0 || e26 === null) ? c[i] : dEma(c[i], e26, slow);
    const m = e12 - e26;
    const prevDea = i > 0 ? dea[i - 1] : null;
    const de = (prevDea == null) ? m : dEma(m, prevDea, sig);
    dif[i] = m; dea[i] = de; hist[i] = m - de; hist2[i] = (m - de) * 2;
  }
  return { dif: dif, dea: dea, hist: hist, hist2: hist2, params: { fast: fast, slow: slow, signal: sig } };
}

/** RSI（旧版 Ti 同式）：gain/loss 比值口径，i>=period 才给值。 */
function rsi(bars, periods) {
  const ps = (periods && periods.length ? periods : [6, 12, 24])
    .map((n) => Math.max(1, Math.floor(num(n) || 1)));
  const c = closes(bars);
  const out = {};
  ps.forEach((s) => {
    const arr = new Array(c.length).fill(null);
    let gain = null, loss = null;
    for (let i = 1; i < c.length; i++) {
      if (c[i] === null || c[i - 1] === null) { gain = null; loss = null; continue; }
      const ch = c[i] - c[i - 1];
      const up = Math.max(ch, 0);
      const ab = Math.abs(ch);
      gain = qSmooth(up, gain, s, 1);
      loss = qSmooth(ab, loss, s, 1);
      if (i >= s) arr[i] = (loss === 0) ? 100 : gain / loss * 100;
    }
    out['rsi' + s] = arr;
  });
  out.periods = ps;
  return out;
}

// ================================================================ 动能K 染色算子
// 2026-11-02 新增。主人从通达信主图截取的原公式，要叠到弹窗 K 线主图上（右键「主图设置」开关）。
//
// 口径来源（两段各有正本，别在这里自创）：
//   · 内核 20 行 + V41 PRO 乘数 → 夏天-activity/工具/jrx_momentum.py::calc_jrx（已 1:1 实测 0 差）
//   · X动能 / 大绿棒 / 五信号 / 背景 → 主人提供的通达信主图原文，逐行转写
// 对拍：tests/js/p1d_dongneng.test.mjs 读 _fixtures/dongneng.json（Python 独立实现产物）逐值比。
//
// 三个口径决定（不是随手简化，理由写在注释里）：
//   1. 大哥黄/趋势白由本算子**自算**（不读 bars.yellow/white）：后端那份前 113 根为 null，
//      而通达信 MA 窗口不足用可用窗口。自算才能覆盖全序列；代价是预热段与画出的黄白线略不同值。
//   2. 换手率条件按**恒真**处理：原稿写 DYNAINFO(35)*100>1.5，但现行通达信 35=卖二价、
//      换手率是 37 → 原版这条从未真正过滤过。复刻原版实际行为，不擅自加严。
//   3. 支撑位 MINL 按原稿字面实现（X1/X2 同一条 BARSLAST，塌缩成单支撑）：原稿此处即笔误，
//      但 MINL 只喂背景色、不参与信号，改它会顺手改掉主人的原图观感，故维持原样。
//
// 纯函数：只吃 OHLCV，不碰 DOM / 网络 / 后端字段。空值口径：数值数组 null 占位，布尔数组恒 true/false。

/** 通达信 SMA(X,N,M)：Y[i] = (M*X[i] + (N-M)*Y[i-1]) / N，首值取 X 首个有效值。 */
function smaTdx(x, n, m) {
  const out = new Array(x.length).fill(null);
  let s = 0;
  while (s < x.length && (x[s] === null || !Number.isFinite(x[s]))) s++;
  if (s >= x.length) return out;
  out[s] = x[s];
  const a = m / n;
  for (let i = s + 1; i < x.length; i++) {
    const p = out[i - 1], cur = x[i];
    if (p === null || cur === null || !Number.isFinite(cur)) continue;
    out[i] = a * cur + (1 - a) * p;
  }
  return out;
}

/** EMA(X,N)（adjust=False）：首值取 X 首值，α=2/(N+1)。 */
function emaRec(x, n) {
  const out = new Array(x.length).fill(null);
  const a = 2 / (Math.max(1, n) + 1);
  let prev = null;
  for (let i = 0; i < x.length; i++) {
    const cur = x[i];
    if (cur === null || !Number.isFinite(cur)) { prev = null; continue; }
    prev = (prev === null) ? cur : prev + a * (cur - prev);
    out[i] = prev;
  }
  return out;
}

/** 滚动窗口聚合（要求窗口满 n 且全有效，对齐 pandas rolling(n) 默认 min_periods=n）。 */
function rollFull(x, n, agg) {
  const out = new Array(x.length).fill(null);
  const N = Math.max(1, Math.floor(n));
  for (let i = N - 1; i < x.length; i++) {
    const vals = [];
    let bad = false;
    for (let k = i - N + 1; k <= i; k++) {
      const v = x[k];
      if (v === null || !Number.isFinite(v)) { bad = true; break; }
      vals.push(v);
    }
    if (!bad) out[i] = agg(vals);
  }
  return out;
}

/** 滚动窗口聚合（min_periods=1、跳过 null，可选右移 shift 位 —— 对应 REF(MA(X,n),1)）。 */
function rollStep(x, n, agg, shift) {
  const out = new Array(x.length).fill(null);
  const N = Math.max(1, Math.floor(n)), sh = shift || 0;
  for (let i = 0; i < x.length; i++) {
    const e = i - sh;
    if (e < 0) continue;
    const vals = [];
    for (let k = Math.max(0, e - N + 1); k <= e; k++) {
      const v = x[k];
      if (v !== null && Number.isFinite(v)) vals.push(v);
    }
    if (vals.length) out[i] = agg(vals);
  }
  return out;
}

/** HHVBARS(X,N)：N 周期内最大值距今天数；同值取最近一次；不足 N 根用可用窗口。 */
function hhvBars(x, n) {
  const out = new Array(x.length).fill(null);
  const N = Math.max(1, Math.floor(n));
  for (let i = 0; i < x.length; i++) {
    const lo = Math.max(0, i - N + 1);
    let best = null, bestJ = -1;
    for (let k = i; k >= lo; k--) {          // 从最近往回扫：同值自然取最近
      const v = x[k];
      if (v === null || !Number.isFinite(v)) continue;
      if (best === null || v > best) { best = v; bestJ = k; }
    }
    if (bestJ >= 0) out[i] = i - bestJ;
  }
  return out;
}

const _mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
const _max = (a) => a.reduce((s, v) => (v > s ? v : s), -Infinity);

/** 从 bar 取成交量（兼容 v / vol / volume 三种命名）。 */
function volOf(b) {
  if (!b) return null;
  const v = (b.v !== undefined) ? b.v : (b.vol !== undefined ? b.vol : b.volume);
  return num(v);
}

/**
 * 动能K 全套算子。
 * 返回 { base, xd, sig }，全部与 bars 等长：
 *   base = 基础动能（新动能内核原值）        xd = X动能（动能二阶差分，洗盘反转突变算子）
 *   sig[i] = 该日 K 线染色（EK1/EK2/V2XEK/V4XEK/V8XEK 任一成立）
 */
function dongneng(bars) {
  const arr = bars || [];
  const L = arr.length;
  if (!L) return { base: [], xd: [], sig: [] };
  const o = arr.map((b) => pick(b, 'o', 'open'));
  const h = arr.map((b) => pick(b, 'h', 'high'));
  const lo = arr.map((b) => pick(b, 'l', 'low'));
  const c = arr.map((b) => pick(b, 'c', 'close'));
  const v = arr.map(volOf);
  const at = (a, i) => (i >= 0 && i < a.length ? a[i] : null);
  const fin = (x) => x !== null && Number.isFinite(x);

  // ---------- 一、动能内核（RSI3 + KDJ(9,3,3) 的 J） ----------
  const lc = c.map((x, i) => (i > 0 ? c[i - 1] : null));
  const diff = c.map((x, i) => (fin(x) && fin(lc[i]) ? x - lc[i] : null));
  const sma1 = smaTdx(diff.map((d) => (d === null ? null : Math.max(d, 0))), 3, 1);
  const sma2 = smaTdx(diff.map((d) => (d === null ? null : Math.abs(d))), 3, 1);
  const rsi3 = sma1.map((a, i) => (a === null || sma2[i] === null ? null : (sma2[i] === 0 ? 0 : a / sma2[i] * 100)));

  const llv9 = rollFull(lo, 9, (a) => Math.min.apply(null, a));
  const hhv9 = rollFull(h, 9, _max);
  const rsv = c.map((x, i) => {
    if (!fin(x) || llv9[i] === null || hhv9[i] === null) return null;
    return hhv9[i] === llv9[i] ? 50 : (x - llv9[i]) / (hhv9[i] - llv9[i]) * 100;
  });
  const kVal = smaTdx(rsv, 3, 1);
  const dVal = smaTdx(kVal, 3, 1);
  const jVal = kVal.map((k, i) => (k === null || dVal[i] === null ? null : 3 * k - 2 * dVal[i]));
  const n1 = jVal.map((x, i) => (fin(x) && fin(at(jVal, i - 1)) ? x - jVal[i - 1] : null));
  const n2 = rsi3.map((x, i) => (fin(x) && fin(at(rsi3, i - 1)) ? x - rsi3[i - 1] : null));

  // ---------- 二、V41 PRO 终极底座乘数 ----------
  const volPrev = v.map((x, i) => (i > 0 ? v[i - 1] : null));
  const volRatio = v.map((x, i) => (fin(x) && fin(volPrev[i]) ? (volPrev[i] === 0 ? 1 : x / volPrev[i]) : null));
  const isYang = arr.map((b, i) => fin(c[i]) && fin(o[i]) && fin(lc[i]) && c[i] > o[i] && c[i] > lc[i]);
  const sdenom = h.map((x, i) => (fin(x) && fin(o[i]) && fin(lc[i]) ? x - Math.min(o[i], lc[i]) : null));
  const upperShadow = sdenom.map((d, i) => {
    if (!fin(d) || !fin(h[i]) || !fin(c[i])) return null;
    return d === 0 ? 0 : (h[i] - c[i]) / d;
  });
  const volSlope = (1.2 - 1.0) / (6.0 - 2.5);
  const volMult = volRatio.map((r, i) => {
    if (!isYang[i] || !fin(r)) return 1;
    if (r >= 2.5) return r >= 6 ? 1.2 : 1.0 + volSlope * (r - 2.5);
    return 1;
  });
  const shadowCoef = upperShadow.map((u, i) => (isYang[i] && fin(u) ? (0.70 - u) * 1.3 : 1));
  const base = n1.map((a, i) => (fin(a) && fin(n2[i]) ? (a + n2[i]) / 2 * shadowCoef[i] * volMult[i] : null));

  // X动能：今日总动能 − 昨日总动能，仅真阳线且增量为正时取值
  const xDiff = n1.map((a, i) => {
    const p1 = at(n1, i - 1), p2 = at(n2, i - 1);
    if (!fin(a) || !fin(n2[i]) || !fin(p1) || !fin(p2)) return null;
    return (a + n2[i]) - (p1 + p2);
  });
  const xd = xDiff.map((d, i) => (isYang[i] && fin(d) && d > 0 ? d / 2 * shadowCoef[i] * volMult[i] : 0));

  // ---------- 三、选股信号 ----------
  const refC = (i, k) => (i - k >= 0 ? c[i - k] : null);
  const maV3 = rollStep(v, 3, _mean, 1);
  const maV10 = rollStep(v, 10, _mean, 1);
  const v2x = arr.map((b, i) => {
    const vp = volPrev[i], c0 = c[i], c1 = refC(i, 1), m3 = maV3[i];
    if (!fin(v[i]) || !fin(vp) || !fin(c0) || !fin(c1) || !fin(m3)) return false;
    if (vp === 0 || c1 === 0 || m3 === 0) return false;
    return v[i] / vp > 1.8 && c0 > c1 && (c0 - c1) / c1 * 100 > 3.5 && v[i] / m3 > 1.4;
  });
  const v4x = arr.map((b, i) => (fin(v[i]) && fin(volPrev[i]) && volPrev[i] !== 0 ? v[i] / volPrev[i] > 3.6 : false));
  const v8x = arr.map((b, i) => (fin(v[i]) && fin(volPrev[i]) && volPrev[i] !== 0 && fin(c[i]) && fin(o[i])
    ? v[i] / volPrev[i] > 8 && c[i] > o[i] : false));

  // 大绿棒：30 日内最大量日若是「大绿棒」（收不低于昨收、或不低于当日开盘）以外的阴线 → 放量不可信
  const vday = hhvBars(v, 30);
  const notGl = arr.map((b, i) => {
    const k = vday[i];
    if (!fin(k)) return null;
    const cc = refC(i, k), cc1 = refC(i, k + 1), oo = i - k >= 0 ? o[i - k] : null;
    if (!fin(cc) || !fin(cc1) || !fin(oo)) return null;
    return cc >= cc1 || cc >= oo;
  });
  const glFar = arr.map((b, i) => notGl[i] === false && fin(vday[i]) && vday[i] >= 12);
  const notGlT = arr.map((b, i) => notGl[i] === true);

  // 大哥黄 / 趋势白（短窗口径，见文件头口径决定 1）
  const ma14 = rollStep(c, 14, _mean), ma28 = rollStep(c, 28, _mean);
  const ma57 = rollStep(c, 57, _mean), ma114 = rollStep(c, 114, _mean);
  const dynYellow = c.map((x, i) => (fin(ma14[i]) && fin(ma28[i]) && fin(ma57[i]) && fin(ma114[i])
    ? (ma14[i] + ma28[i] + ma57[i] + ma114[i]) / 4 : null));
  const dynWhite = emaRec(emaRec(c, 10), 10);

  const trend1 = arr.map((b, i) => {
    const y = dynYellow[i], yp = at(dynYellow, i - 1), w = dynWhite[i], wp = at(dynWhite, i - 1);
    const c0 = c[i], c1 = at(c, i - 1);
    const upY = fin(y) && fin(yp) && y >= yp * 0.999;
    const upW = fin(w) && fin(wp) && w >= wp;
    const crossW = fin(c0) && fin(c1) && fin(w) && fin(wp) && c0 > w && c1 <= wp;
    const crossY = fin(c0) && fin(c1) && fin(y) && fin(yp) && c0 > y && c1 <= yp;
    return Boolean(upY || upW || crossW || crossY);
  });
  const trend2 = arr.map((b, i) => (fin(c[i]) && fin(dynWhite[i]) ? c[i] >= dynWhite[i] : false));

  // 换手率条件恒真（口径决定 2）
  const ek1 = arr.map((b, i) => Boolean(
    fin(v[i]) && fin(volPrev[i]) && v[i] > volPrev[i]
    && fin(maV10[i]) && v[i] > maV10[i]
    && ((fin(base[i]) && base[i] >= 40) || (fin(xd[i]) && xd[i] >= 50))
    && trend1[i] && notGlT[i]));
  const ek2 = arr.map((b, i) => Boolean(
    fin(v[i]) && fin(volPrev[i]) && v[i] > volPrev[i] * 1.5
    && fin(maV3[i]) && v[i] > maV3[i]
    && ((fin(base[i]) && base[i] >= 35) || (fin(xd[i]) && xd[i] >= 45))
    && trend2[i] && (notGlT[i] || glFar[i])));
  const ekpro = arr.map((b, i) => ek1[i] || ek2[i]);

  const hhvV40 = rollStep(v, 40, _max);
  const hhvC30 = rollStep(c, 30, _max);
  const v2xek = arr.map((b, i) => {
    if (!v2x[i]) return false;
    const hiBase = (fin(base[i]) && base[i] > 30) || (fin(xd[i]) && xd[i] > 40);
    const loBase = (fin(base[i]) && base[i] > 13) || (fin(xd[i]) && xd[i] > 18);
    const newHigh = (fin(v[i]) && fin(hhvV40[i]) && v[i] === hhvV40[i])
      || (fin(c[i]) && fin(hhvC30[i]) && c[i] === hhvC30[i]);
    return Boolean((hiBase || (loBase && newHigh)) && notGlT[i]);
  });
  const v4xek = arr.map((b, i) => Boolean(v4x[i]
    && ((fin(base[i]) && base[i] > 20) || (fin(xd[i]) && xd[i] > 20))));
  const v8xek = arr.map((b, i) => Boolean(v8x[i]
    && ((fin(base[i]) && base[i] > 10) || (fin(xd[i]) && xd[i] > 15))));
  const sig = arr.map((b, i) => ekpro[i] || v2xek[i] || v4xek[i] || v8xek[i]);

  // ---------- 四、背景（2026-11-02 主人裁定：超卖蓝底**不要**，已删） ----------
  // 原公式 DRAWGBK_DIV 那一段（J/RSI3 超卖 + 贴支撑 MINL + 45 日内有信号）连同
  //   只喂它的支撑位/BARSLAST 计算一并拿掉；本算子只输出染色信号。
  return { base: base, xd: xd, sig: sig };
}

/** 成交额（原样透传，不换单位）。 */
function amounts(bars) {
  return (bars || []).map((b) => num(b && b.amount));
}

/** 活跃市值三件（amv / amvMa / amvWhite）——P1c 首版漏了后两个，AMV 副图均线因此恒空。 */
function amv(bars) {
  return {
    amv: (bars || []).map((b) => (b ? num(b.amv) : null)),
    ma: (bars || []).map((b) => (b ? (num(b.amvMa) !== null ? num(b.amvMa) : num(b.amv_ma)) : null)),
    white: (bars || []).map((b) => (b ? (num(b.amvWhite) !== null ? num(b.amvWhite) : num(b.amv_white)) : null)),
  };
}

/**
 * 主力动向（旧版 mfByDay 口径）。
 * 对齐规则（实证 2026-10-27 真实 GET）：payload `mf.{主力资金,主买,占成交,占流通,q}` 与 `bars`
 * **等长且按下标一一对应**（`?limit=5` 也被后端回成 120 根 + 120 个 mf 值），旧版同样是 `k1[i]` 对 `bars[i]`。
 * 长度不一致时**不右对齐猜**：整块判不可用并给出原因。
 * 单位：payload 原值（主力资金/主买为万元量级），本文件不换算；展示层按旧版 ×0.7 画细柱。
 */
function mf(bars, payload) {
  const barsN = (bars || []).length;
  const m = payload && payload.mf;
  const empty = { ok: false, reason: '当前周期无主力动向数据（仅日线提供）' };
  const arr = (k) => (m && Array.isArray(m[k]) ? m[k] : null);
  const keys = ['主力资金', '主买', '占成交', '占流通', 'q'];
  const lists = {};
  for (const k of keys) {
    const a = arr(k);
    if (!a) return empty;
    if (a.length !== barsN) {
      return { ok: false, reason: '主力动向长度与 K 线不一致（' + a.length + ' vs ' + barsN + '），不做右对齐猜测' };
    }
    lists[k] = a.map(num);
  }
  return {
    ok: true,
    main: lists['主力资金'],
    buy: lists['主买'],
    zc: lists['占成交'],
    zlt: lists['占流通'],
    q: lists['q'],
    lookback: m.lookback == null ? null : Number(m.lookback),
    source: m.source || null,
  };
}

function hasValues(arr) {
  return Array.isArray(arr) && arr.some((v) => v !== null && Number.isFinite(v));
}

export {
  qSmooth, dEma, rsv, stoch,
  kdj, macd, rsi, amounts, amv, mf, hasValues, CA, dongneng,
};
export default { kdj, macd, rsi, amounts, amv, mf, hasValues, dongneng };
