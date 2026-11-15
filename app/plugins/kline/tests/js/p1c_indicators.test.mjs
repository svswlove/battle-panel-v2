// P1c 单测（第二轮）：指标与旧版 kline_lc.js 的 Ri/Ti/Si/Mn/Qt/Dt **等价移植**对拍 + 副图记录型 canvas。
// 参考实现直接照抄旧版公式（测试内独立写一遍），不复用被测代码，避免「自己跟自己比」。
import assert from 'node:assert/strict';

globalThis.window = { devicePixelRatio: 1 };
const IND = await import(new URL('../../frontend/indicators.js', import.meta.url).href);
const SUB = await import(new URL('../../frontend/subcharts.js', import.meta.url).href);

let n = 0;
const it = (name, fn) => { fn(); n++; console.log('ok ' + n + ' - ' + name); };

function series(closes) {
  return closes.map((c, i) => ({ date: '2026' + String(100 + i), open: c - 1, high: c + 2, low: c - 2, close: c, amount: 1000 + i * 10, volume: 100 }));
}

// ---- 旧版原函数（照抄，作为参考口径）----
const CA = 1e-4;
const Qt = (v, prev, s, nn) => (prev == null) ? v : (nn * v + (s - nn) * prev) / s;
const Dt = (v, prev, nn) => prev + (2 / (nn + 1)) * (v - prev);
function Si(bars, i, nn) {
  if (i < nn - 1) return null;
  let lo = Infinity, hi = -Infinity;
  for (let a = i - nn + 1; a <= i; a++) { if (bars[a].low < lo) lo = bars[a].low; if (bars[a].high > hi) hi = bars[a].high; }
  if (!isFinite(lo) || !isFinite(hi)) return null;
  return (bars[i].close - lo) / (hi - lo + CA) * 100;
}
function Mn(bars, i, nn) {
  if (i < nn - 1) return null;
  let lo = Infinity, hi = -Infinity;
  for (let a = i - nn + 1; a <= i; a++) { if (bars[a].low < lo) lo = bars[a].low; if (bars[a].close > hi) hi = bars[a].close; }
  return (bars[i].close - lo) / (hi - lo + CA) * 100;
}
function refRi(bars, s, nn, rsvN, mfP, msP, msig) {
  const L = bars.length;
  const o = { k: new Array(L).fill(null), d: new Array(L).fill(null), j: new Array(L).fill(null),
    stochShort: new Array(L).fill(null), stochLong: new Array(L).fill(null),
    dif: new Array(L).fill(null), dea: new Array(L).fill(null), hist: new Array(L).fill(null) };
  const e12 = new Array(L).fill(0), e26 = new Array(L).fill(0);
  for (let a = 0; a < L; a++) {
    const i = Si(bars, a, rsvN);
    if (i != null) {
      const k = a > 0 ? o.k[a - 1] : null, d = a > 0 ? o.d[a - 1] : null;
      const K = ((k == null ? 50 : k) * 2 + i) / 3;
      const D = ((d == null ? 50 : d) * 2 + K) / 3;
      o.k[a] = K; o.d[a] = D; o.j[a] = 3 * K - 2 * D;
    }
    o.stochShort[a] = Mn(bars, a, s);
    o.stochLong[a] = Mn(bars, a, nn);
    const c = bars[a].close;
    const ema12 = a === 0 ? c : Dt(c, e12[a - 1], mfP);
    const ema26 = a === 0 ? c : Dt(c, e26[a - 1], msP);
    e12[a] = ema12; e26[a] = ema26;
    const m = ema12 - ema26;
    const dea = (a === 0 || o.dea[a - 1] == null) ? m : Dt(m, o.dea[a - 1], msig);
    o.dif[a] = m; o.dea[a] = dea; o.hist[a] = m - dea;
  }
  return o;
}
function refTi(bars, periods) {
  return periods.map((s) => {
    const out = new Array(bars.length).fill(null);
    let gain = null, loss = null;
    for (let i = 0; i < bars.length; i++) {
      if (i === 0) continue;
      const ch = bars[i].close - bars[i - 1].close;
      gain = Qt(Math.max(ch, 0), gain, s, 1);
      loss = Qt(Math.abs(ch), loss, s, 1);
      out[i] = i >= s ? (loss === 0 ? 100 : gain / loss * 100) : null;
    }
    return out;
  });
}

const BARS = series([10, 11, 12, 11, 13, 12, 14, 15, 13, 16, 17, 15, 14, 16, 18, 17, 19, 20, 18, 21,
  22, 20, 23, 24, 22, 25, 26, 24, 27, 28, 26, 29, 30, 28, 31, 32, 30, 33, 34, 32]);

// ---------------------------------------------------------------- KDJ

it('KDJ：与旧版 Ri 逐点对拍（RSV 首值、K/D 初值 50、无 9 根预热）', () => {
  const a = IND.kdj(BARS, { rsv: 9, short: 3, long: 21 });
  const b = refRi(BARS, 3, 21, 9, 12, 26, 9);
  assert.deepEqual(a.k, b.k, 'K 不一致');
  assert.deepEqual(a.d, b.d, 'D 不一致');
  assert.deepEqual(a.j, b.j, 'J 不一致');
  // 预热只由 Si 的 i>=rsv-1 决定：第 9 根（idx 8）起才算
  assert.equal(a.k[7], null);
  assert.ok(a.k[8] !== null);
  // 首值不是 RSV 本身，而是 (100+RSV)/3（K_prev 缺省 50）
  const rsv8 = (BARS[8].close - Math.min(...BARS.slice(0, 9).map(x => x.low))) /
    (Math.max(...BARS.slice(0, 9).map(x => x.high)) - Math.min(...BARS.slice(0, 9).map(x => x.low)) + CA) * 100;
  assert.ok(Math.abs(a.k[8] - (100 + rsv8) / 3) < 1e-9, '首值必须 (2*50+RSV)/3');
});

it('KDJ：stochShort/stochLong 与旧版 Mn 对拍（HHV 用 close）', () => {
  const a = IND.kdj(BARS, { rsv: 9, short: 3, long: 21 });
  const b = refRi(BARS, 3, 21, 9, 12, 26, 9);
  assert.deepEqual(a.stochShort, b.stochShort);
  assert.deepEqual(a.stochLong, b.stochLong);
});

it('KDJ：全平/单根不出 NaN', () => {
  const flat = series(new Array(12).fill(10));
  const a = IND.kdj(flat, { rsv: 9 });
  a.k.concat(a.d, a.j, a.rsv, a.stochShort).forEach(v => assert.ok(v === null || Number.isFinite(v), 'NaN'));
  const one = IND.kdj(series([10]), { rsv: 9 });
  assert.equal(one.k[0], null);
});

// ---------------------------------------------------------------- MACD

it('MACD：与旧版 Ri 对拍（首根即递推、hist 原始 = DIF−DEA）', () => {
  const a = IND.macd(BARS, { fast: 12, slow: 26, signal: 9 });
  const b = refRi(BARS, 3, 21, 9, 12, 26, 9);
  assert.deepEqual(a.dif, b.dif);
  assert.deepEqual(a.dea, b.dea);
  assert.deepEqual(a.hist, b.hist, 'hist 必须是原始 DIF−DEA（×2 只在 buildSubs）');
  assert.equal(a.dif[0], 0, '首根 DIF = 0（ema12=ema26=close）');
  a.hist.forEach((v, i) => assert.ok(Math.abs(a.hist2[i] - v * 2) < 1e-12, 'hist2 必须是 2×hist'));
});

it('MACD：全平序列 → 全 0，不出 NaN', () => {
  const a = IND.macd(series(new Array(40).fill(10)));
  a.hist.forEach(v => { assert.ok(Number.isFinite(v)); assert.ok(Math.abs(v) < 1e-12); });
});

// ---------------------------------------------------------------- RSI

it('RSI：与旧版 Ti 对拍（gain/loss 比值口径，可 >100，不预热到 i>=s）', () => {
  const a = IND.rsi(BARS, [6, 12, 24]);
  const b = refTi(BARS, [6, 12, 24]);
  [0, 1, 2].forEach((k) => {
    const key = 'rsi' + [6, 12, 24][k];
    assert.deepEqual(a[key], b[k], key + ' 不一致');
  });
  assert.equal(a.rsi6[5], null, 'i<6 不给值');
  assert.ok(a.rsi6[6] !== null, 'i>=6 起算');
});

it('RSI：单边上涨 loss=0 → 100；全平 → 也走 loss=0 分支（旧版口径）', () => {
  const up = IND.rsi(series([10, 11, 12, 13, 14, 15, 16, 17]), [6]);
  assert.equal(up.rsi6[7], 100);
  const flat = IND.rsi(series(new Array(10).fill(10)), [6]);
  assert.equal(flat.rsi6[9], 100, '旧版 loss===0 时给 100（无变化也算）');
});

// ---------------------------------------------------------------- 取数序列

it('amounts/amv：amvMa/amvWhite 必须带出来（首版漏了导致副图均线恒空）', () => {
  const bars = [
    { close: 10, amount: 100, amv: 5, amvMa: 4.5, amvWhite: 4.8 },
    { close: 11, amount: null, amv: 6, amvMa: null, amvWhite: 5 },
  ];
  const a = IND.amv(bars);
  assert.deepEqual(a.amv, [5, 6]);
  assert.deepEqual(a.ma, [4.5, null], 'amvMa 必须透传');
  assert.deepEqual(a.white, [4.8, 5], 'amvWhite 必须透传');
  assert.deepEqual(IND.amounts(bars), [100, null]);
});

// ---------------------------------------------------------------- 主力动向（mf）

function mfPayload(len, opt) {
  const o = opt || {};
  const mk = (v) => new Array(len).fill(v);
  return { lookback: len, source: 'cache', '主力资金': mk(o.main == null ? -1000 : o.main), '主买': mk(o.buy == null ? -900 : o.buy),
    '占成交': mk(-15.9), '占流通': mk(-1.0), q: mk(-2.4) };
}

it('mf：等长按下标对齐，五层字段齐全', () => {
  const bars = series(new Array(10).fill(0).map((_, i) => 10 + i));
  const r = IND.mf(bars, { mf: mfPayload(10) });
  assert.equal(r.ok, true);
  assert.equal(r.main.length, 10);
  assert.equal(r.buy[0], -900);
  assert.equal(r.zc[0], -15.9);
  assert.equal(r.q[0], -2.4);
});

it('mf：长度与 bars 不一致 → 不猜（整块不可用 + 原因）', () => {
  const bars = series(new Array(10).fill(0).map((_, i) => 10 + i));
  const r = IND.mf(bars, { mf: mfPayload(8) });
  assert.equal(r.ok, false);
  assert.ok(/长度与 K 线不一致/.test(r.reason), r.reason);
  assert.equal(IND.mf(bars, { mf: null }).ok, false);
});

it('mf：缺任一关键字段 → 不可用（不画半边）', () => {
  const bars = series(new Array(5).fill(0).map((_, i) => 10 + i));
  const p = mfPayload(5);
  delete p['占流通'];
  assert.equal(IND.mf(bars, { mf: p }).ok, false);
});

// ---------------------------------------------------------------- 副图记录型 canvas

function recCtx() {
  const ops = [];
  return {
    ops, _moves: 0, strokeStyle: '', fillStyle: '', lineWidth: 1, globalAlpha: 1, font: '', textAlign: '',
    setTransform() {}, scale() {}, clearRect() {}, beginPath() { this._moves = 0; }, closePath() {},
    setLineDash() {}, save() {}, restore() {}, stroke() { ops.push({ op: 'stroke', w: this.lineWidth }); }, fill() {},
    moveTo(x, y) { this._moves++; ops.push({ op: 'moveTo', x, y }); },
    lineTo(x, y) { ops.push({ op: 'lineTo', x, y }); },
    fillRect(x, y, w, h) { ops.push({ op: 'fillRect', x, y, w, h }); },
    fillText(t, x, y) { ops.push({ op: 'fillText', t, x, y }); },
    arc() {},
  };
}
function recCanvas(w = 520, h = 88) {
  const ctx = recCtx();
  return { clientWidth: w, clientHeight: h, width: 0, height: 0, getContext: () => ctx, _ops: ctx.ops };
}
const fakeAxis = (cnt, w = 520) => {
  const l = 56, r = w - 8;
  return { rect: { l, r, t: 10, b: 250 }, xAt: (i, N) => l + (r - l) * (N <= 1 ? 0.5 : i / (N - 1)), n: cnt };
};
const finite = (ops) => ops.every(o => (typeof o.x !== 'number' || Number.isFinite(o.x)) && (typeof o.y !== 'number' || Number.isFinite(o.y)));

it('drawSub：成交额柱 + 空数据只留原因（不画 0 柱）', () => {
  const bars = series([10, 11, 12]);
  const cv = recCanvas();
  assert.equal(SUB.drawSub(cv, 'amount', { bars, axis: fakeAxis(3), colors: {} }).ok, true);
  assert.ok(cv._ops.filter(o => o.op === 'fillRect').length >= 3);
  const cv2 = recCanvas();
  assert.equal(SUB.drawSub(cv2, 'amount', { bars: [{ close: 1 }], axis: fakeAxis(1), colors: {} }).ok, false);
  assert.equal(cv2._ops.filter(o => o.op === 'fillRect').length, 0);
  assert.ok(cv2._ops.some(o => o.op === 'fillText' && /不可用/.test(String(o.t))));
});

it('drawSub：AMV 真映射到渲染（均线/白线都落笔）', () => {
  const bars = series([10, 11, 12, 13, 14]).map((b, i) => Object.assign(b, { amv: 5 + i, amvMa: 4 + i, amvWhite: 4.5 + i }));
  const cv = recCanvas();
  const res = SUB.drawSub(cv, 'amv', { bars, axis: fakeAxis(5), colors: {}, series: { amv: IND.amv(bars) } });
  assert.equal(res.ok, true);
  assert.ok(cv._ops.some(o => o.op === 'stroke'), '至少画了线');
  assert.ok(finite(cv._ops), '坐标有 NaN');
  // AMV 全 null：不可用 + 不画线
  const cv2 = recCanvas();
  const res2 = SUB.drawSub(cv2, 'amv', { bars: series([10, 11]), axis: fakeAxis(2), colors: {}, series: { amv: IND.amv(series([10, 11])) } });
  assert.equal(res2.ok, false);
  assert.equal(cv2._ops.filter(o => o.op === 'moveTo').length, 0);
});

it('drawSub：KDJ 五线（K/D/J/stochShort/stochLong）都画，MACD 用原始 hist 画柱，RSI 落笔', () => {
  const kd = IND.kdj(BARS, { rsv: 9, short: 3, long: 21 });
  const md = IND.macd(BARS);
  const rs = IND.rsi(BARS);
  const cvK = recCanvas();
  assert.equal(SUB.drawSub(cvK, 'kdj', { bars: BARS, axis: fakeAxis(BARS.length), colors: {}, series: { kdj: kd } }).ok, true);
  const strokes = cvK._ops.filter(o => o.op === 'stroke').length;
  assert.ok(strokes >= 5, 'KDJ 应至少 5 条线，实际 ' + strokes);
  assert.ok(finite(cvK._ops));

  const cvM = recCanvas();
  assert.equal(SUB.drawSub(cvM, 'macd', { bars: BARS, axis: fakeAxis(BARS.length), colors: {}, series: { macd: md } }).ok, true);
  assert.ok(cvM._ops.some(o => o.op === 'fillRect'), 'MACD 柱没画');
  assert.ok(finite(cvM._ops));

  const cvR = recCanvas();
  assert.equal(SUB.drawSub(cvR, 'rsi', { bars: BARS, axis: fakeAxis(BARS.length), colors: {}, series: { rsi: rs } }).ok, true);
  assert.ok(cvR._ops.filter(o => o.op === 'moveTo').length > 3);
  assert.ok(finite(cvR._ops));
});

it('drawSub：mf 四层（主柱 + 主买细柱 + 占比/Q 数值）', () => {
  const bars = series(new Array(30).fill(0).map((_, i) => 10 + Math.sin(i)));
  const m = IND.mf(bars, { mf: mfPayload(30, { main: -1200, buy: -800 }) });
  assert.equal(m.ok, true);
  const cv = recCanvas();
  const res = SUB.drawSub(cv, 'mf', { bars, axis: fakeAxis(30), colors: {}, series: { mf: m } });
  assert.equal(res.ok, true);
  assert.ok(cv._ops.filter(o => o.op === 'fillRect').length >= 60, '主柱 + 细柱都要有');
  assert.ok(cv._ops.some(o => o.op === 'fillText' && /占成交/.test(String(o.t))), '占比数值没写');
});

it('drawSub：mf 不可用时给原因，不画任何柱', () => {
  const bars = series([10, 11, 12]);
  const cv = recCanvas();
  const res = SUB.drawSub(cv, 'mf', { bars, axis: fakeAxis(3), colors: {}, series: { mf: IND.mf(bars, { mf: mfPayload(2) }) } });
  assert.equal(res.ok, false);
  assert.equal(cv._ops.filter(o => o.op === 'fillRect').length, 0);
  assert.ok(cv._ops.some(o => o.op === 'fillText' && /不可用/.test(String(o.t))));
});

console.log('\nP1c：' + n + ' 项全过');

// ---------------------------------------------------------------- 竞态（P1c 返修 3）：可控 promise
const { latestGate } = await import(new URL('../../frontend/overlay.js', import.meta.url).href);

function deferred() {
  let resolve, reject;
  const p = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { p, resolve, reject };
}

it('竞态：慢 d 后快 m5 → m5 先回并生效，d 回来被丢弃', async () => {
  const gate = latestGate();
  const view = { period: null, cache: null };
  async function load(period, dPromise) {
    const token = gate.begin();
    try {
      const data = await dPromise;
      if (!gate.isLatest(token)) return 'dropped';
      view.period = period; view.cache = data;
      return 'applied';
    } catch (e) { return 'failed'; }
  }
  const dReq = deferred(), m5Req = deferred();
  const pD = load('d', dReq.p);
  const pM5 = load('m5', m5Req.p);
  m5Req.resolve({ bars: 'm5' });
  assert.equal(await pM5, 'applied');
  dReq.resolve({ bars: 'd' });
  assert.equal(await pD, 'dropped');
  assert.equal(view.period, 'm5');
  assert.deepEqual(view.cache, { bars: 'm5' });
});

it('竞态：慢 m5 后快 d → d 先回，m5 回得晚也不覆盖', async () => {
  const gate = latestGate();
  const view = { period: null, cache: null };
  async function load(period, dPromise) {
    const token = gate.begin();
    const data = await dPromise;
    if (!gate.isLatest(token)) return 'dropped';
    view.period = period; view.cache = data;
    return 'applied';
  }
  const m5Req = deferred(), dReq = deferred();
  const pM5 = load('m5', m5Req.p);
  const pD = load('d', dReq.p);
  dReq.resolve({ bars: 'd' });
  assert.equal(await pD, 'applied');
  m5Req.resolve({ bars: 'm5' });
  assert.equal(await pM5, 'dropped');
  assert.equal(view.period, 'd');
});

it('竞态：最后一次失败 → 不把旧成功留下当新结果', async () => {
  const gate = latestGate();
  const view = { period: 'm5', cache: { bars: 'm5' }, error: null };
  async function load(period, dPromise) {
    const token = gate.begin();
    try {
      const data = await dPromise;
      if (!gate.isLatest(token)) return 'dropped';
      view.period = period; view.cache = data; return 'applied';
    } catch (e) {
      if (!gate.isLatest(token)) return 'dropped';
      view.cache = null; view.error = String(e.message || e);
      return 'failed';
    }
  }
  const dReq = deferred();
  const pD = load('d', dReq.p);
  dReq.reject(new Error('HTTP 500'));
  assert.equal(await pD, 'failed');
  assert.equal(view.cache, null, '失败必须在最新请求上清掉旧 cache');
  assert.equal(view.error, 'HTTP 500');
});

it('竞态：切周期时 invalidate → 在途请求全部作废（副图/叠加同源失效）', async () => {
  const gate = latestGate();
  const t1 = gate.begin();
  gate.invalidate();
  assert.equal(gate.isLatest(t1), false);
  const t2 = gate.begin();
  assert.equal(gate.isLatest(t2), true);
});

console.log('\nP1c 竞态：' + 4 + ' 项全过');
