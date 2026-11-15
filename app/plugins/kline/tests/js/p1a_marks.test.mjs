// marks.js 单测（P1a）：纯函数层，不需要 DOM。
// 由 tests/test_kline_overlay_p1a.py 用 node 拉起；也可单独跑：
//   node app/plugins/kline/tests/js/p1a_marks.test.mjs
import assert from 'node:assert/strict';
import * as fsmod from 'node:fs';

const BASE = new URL('../../frontend/marks.js', import.meta.url);
const M = await import(BASE.href);

let n = 0;
function it(name, fn) {
  fn();
  n++;
  console.log('ok ' + n + ' - ' + name);
}

function fakeStore(seed) {
  const m = Object.assign({}, seed || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    _dump: () => m,
  };
}

// ---------------------------------------------------------------- 标记开关 ko_marks

it('开关默认全开', () => {
  const s = fakeStore();
  assert.deepEqual(M.readMarks(s), { bs: true, cost: true, order: true, cond: true });
});

it('开关只读已知字段，缺项回落默认', () => {
  const s = fakeStore({ ko_marks: JSON.stringify({ cost: false }) });
  assert.deepEqual(M.readMarks(s), { bs: true, cost: false, order: true, cond: true });
});

it('坏 JSON 不让四项一起失效', () => {
  const s = fakeStore({ ko_marks: '{not json' });
  assert.deepEqual(M.readMarks(s), { bs: true, cost: true, order: true, cond: true });
});

it('保存走旧版同一把 key=ko_marks，且只存布尔', () => {
  const s = fakeStore();
  M.saveMarks(s, { bs: 0, cost: 1, order: 'x', cond: null });
  assert.equal(M.MARKS_KEY, 'ko_marks');
  assert.deepEqual(JSON.parse(s._dump().ko_marks), { bs: false, cost: true, order: true, cond: false });
});

// ---------------------------------------------------------------- 尺寸 klm_size_v1

it('尺寸：key 独立，存读往返', () => {
  const s = fakeStore();
  assert.equal(M.SIZE_KEY, 'klm_size_v1');
  assert.equal(M.readSize(s), null, '没存过要返回 null（用 CSS 默认）');
  M.saveSize(s, { w: 980.4, h: 420.6 });
  assert.deepEqual(M.readSize(s), { w: 980, h: 421 });
  assert.equal(s._dump().ko_marks, undefined, '不许覆盖 ko_marks');
});

it('尺寸夹进视口：窗口变小后不会画出屏幕', () => {
  const big = { w: 1600, h: 900 };
  const fitted = M.clampSize(big, { w: 1200, h: 800 });
  assert.ok(fitted.w <= 1200 - 40, '宽必须小于视口');
  assert.ok(fitted.h <= Math.round(800 * 0.62), '高必须受视口限制');
  const small = M.clampSize({ w: 200, h: 100 }, { w: 1200, h: 800 });
  assert.equal(small.w, M.MIN_W);
  assert.equal(small.h, M.MIN_H);
  assert.equal(M.clampSize(null, { w: 1200, h: 800 }), null);
  // 视口报 0（内嵌 webview/隐藏标签页）时不能整块失效，否则拖角与尺寸记忆一起死
  const blind = M.clampSize({ w: 900, h: 400 }, { w: 0, h: 0 });
  assert.deepEqual(blind, { w: 900, h: 400 });
  assert.deepEqual(M.clampSize({ w: 10, h: 10 }, null), { w: M.MIN_W, h: M.MIN_H });
});

// ---------------------------------------------------------------- 手工水平线

it('手工线按代码存，同价不重复，超上限丢最旧', () => {
  const s = fakeStore();
  assert.equal(M.HLINE_KEY, 'klm_hlines_v1');
  M.addHline(s, '600519.SH', 12.345);
  let arr = M.addHline(s, '600519.SH', 12.3454); // 两位小数内相同
  assert.deepEqual(arr, [12.35]);
  assert.deepEqual(M.readHlines(s, '000001.SZ'), [], '别的票不受影响');
  for (let i = 0; i < M.MAX_HLINES + 5; i++) arr = M.addHline(s, '600519.SH', 20 + i);
  assert.equal(arr.length, M.MAX_HLINES);
  assert.deepEqual(M.clearHlines(s, '600519.SH'), []);
  assert.deepEqual(M.readHlines(s, '600519.SH'), []);
});

// ---------------------------------------------------------------- 可用性 / 线模型

const EMPTY = { bs: [], cost: null, order: [], cond: [] };
const FULL = {
  bs: [{ t: '10:00', price: 10.5, side: 'BUY' }],
  cost: 9.87,
  order: [{ price: 10.1, side: 'BUY', qty: 100 }],
  cond: [{ price: 10.9, label: '止盈' }],
};

it('四项都没数据时一律 ok=false 且带原因', () => {
  const av = M.availability(EMPTY);
  ['bs', 'cost', 'order', 'cond'].forEach((k) => {
    assert.equal(av[k].ok, false, k + ' 不该声称可用');
    assert.ok(av[k].reason.length > 0, k + ' 必须给原因');
  });
});

it('有数据才 ok，条数对得上', () => {
  const av = M.availability(FULL);
  assert.deepEqual([av.bs.ok, av.bs.count, av.cost.ok, av.order.count, av.cond.ok], [true, 1, true, 1, true]);
});

it('线模型：无数据不产线，也不伪造价位', () => {
  const mm = M.lineModels(M.readMarks(null), EMPTY);
  assert.deepEqual(mm.lines, []);
  assert.deepEqual(mm.points, []);
  assert.deepEqual(mm.missing.sort(), ['bs', 'cond', 'cost', 'order']);
});

it('线模型：有数据按开关出门；关掉整类就不出', () => {
  const on = M.readMarks(null);
  assert.equal(M.lineModels(on, FULL).lines.length, 3);        // cost + order + cond
  assert.equal(M.lineModels(on, FULL).points.length, 1);       // bs 点
  const off = { bs: true, cost: false, order: false, cond: false };
  const r = M.lineModels(off, FULL);
  assert.equal(r.lines.length, 0);
  assert.equal(r.points.length, 1, 'bs 没关就还在');
  assert.ok(!r.missing.includes('cost'), '关掉的类不算「缺数据」');
});

it('线模型：价格 <= 0 的脏数据当没有，不画', () => {
  const on = M.readMarks(null);
  const r = M.lineModels(on, { bs: [], cost: 0, order: [{ price: -1 }], cond: [] });
  assert.deepEqual(r.lines, []);
  assert.deepEqual(r.missing.sort(), ['bs', 'cond', 'cost', 'order']);
});

// ---------------------------------------------------------------- 菜单模型

it('菜单：下单/条件单 disabled 且写原因；P1b 起叠加已开放（子菜单）', () => {
  const model = M.buildMenuModel({ code: '600519.SH', name: '贵州茅台', price: 10, marks: M.readMarks(null), data: EMPTY, hlines: [], period: 'intraday' });
  const byId = Object.fromEntries(model.filter((x) => x.id).map((x) => [x.id, x]));
  ['buy', 'sell', 'cond'].forEach((k) => {
    assert.equal(byId[k].disabled, true, k + ' 本阶段必须禁用');
    assert.ok(byId[k].reason && byId[k].reason.length > 0, k + ' 禁用必须给原因');
  });
  assert.ok(!byId.overlay.disabled && Array.isArray(byId.overlay.sub), '叠加对比必须可点且带子菜单');
  assert.ok(!byId.refresh.disabled, '刷新行情必须是能用的');
});

// 2026-10-30（主人）：日线右键 = 加入自选 + 设置预警 + 主图设置；分时才是买卖为主。
// 2026-11-07：预警入口从「条件单预警」改为「设置预警」（只写预警表，不再开下单面板）
it('菜单：日线有 加入自选/设置预警/主图设置，且无买卖项', () => {
  const model = M.buildMenuModel({ code: '600519.SH', name: '贵州茅台', price: 10, marks: M.readMarks(null), data: EMPTY, hlines: [], period: 'd', watched: false, tools: { chips: false, adjust: true, cost: false, bs: true, tpsl: false }, visibleBars: 120 });
  const byId = Object.fromEntries(model.filter((x) => x.id).map((x) => [x.id, x]));
  assert.ok(byId.watch && byId.watch.label === '加入自选', '日线缺「加入自选」');
  assert.ok(byId.alert && !byId.alert.disabled, '日线「设置预警」必须可用');
  assert.ok(byId.main && Array.isArray(byId.main.sub), '日线缺「主图设置」子菜单');
  assert.ok(!byId.buy && !byId.sell, '日线不应出现买入/卖出项');
  assert.ok(!byId.marks, '日线不应出现「分时标记」');
  // 主图设置：默认根数三档单选 + 五个开关
  const sub = byId.main.sub;
  assert.deepEqual(sub.filter((x) => x.id && x.id.indexOf('vb_') === 0).map((x) => x.id), ['vb_60', 'vb_120', 'vb_240']);
  assert.equal(sub.find((x) => x.id === 'vb_120').checked, true, '当前 120 根应打勾');
  ['tools_chips', 'tools_adjust', 'tools_cost', 'tools_bs', 'tools_tpsl'].forEach((k) => {
    assert.ok(sub.some((x) => x.id === k), '主图设置缺 ' + k);
  });
  assert.equal(sub.find((x) => x.id === 'tools_adjust').checked, true, '复权默认开');
  assert.equal(sub.find((x) => x.id === 'tools_chips').checked, false, '筹码默认关');
});

it('菜单：已在自选时项变「已在自选」且禁用', () => {
  const model = M.buildMenuModel({ code: 'X', price: 1, marks: M.readMarks(null), data: EMPTY, hlines: [], period: 'd', watched: true });
  const w = model.find((x) => x.id === 'watch');
  assert.equal(w.label, '已在自选');
  assert.equal(w.disabled, true);
});

it('菜单：分时标记四项在，无数据标「无数据」并给原因', () => {
  const model = M.buildMenuModel({ code: '600519.SH', price: null, marks: M.readMarks(null), data: EMPTY, hlines: [], period: 'intraday' });
  const sub = model.find((x) => x.id === 'marks').sub;
  const rows = sub.filter((x) => x.id && x.id.startsWith('mk_') && x.id !== 'mk_off');
  assert.deepEqual(rows.map((r) => r.id), ['mk_bs', 'mk_cost', 'mk_order', 'mk_cond']);
  rows.forEach((r) => {
    assert.equal(r.checked, true, '默认全开');
    assert.equal(r.tag, '无数据');
    assert.ok(r.reason.length > 0);
    assert.ok(!r.disabled, '开关本身可点（改的是持久化偏好，有打勾反馈）');
  });
  assert.ok(sub.some((x) => x.id === 'mk_off' && !x.disabled), '开着就能「全部关闭」');
});

it('菜单：全部关闭后再给「全部关闭」= 禁用并说明', () => {
  const model = M.buildMenuModel({ code: 'X', marks: { bs: false, cost: false, order: false, cond: false }, data: EMPTY, hlines: [], period: 'intraday' });
  const off = model.find((x) => x.id === 'marks').sub.find((x) => x.id === 'mk_off');
  assert.equal(off.disabled, true);
  assert.ok(off.reason.length > 0);
});

it('菜单：手工画线子项 —— 无线时「清除」禁用并说明', () => {
  const m0 = M.buildMenuModel({ code: 'X', marks: M.readMarks(null), data: EMPTY, hlines: [] });
  const d0 = m0.find((x) => x.id === 'draw').sub;
  assert.ok(d0.some((x) => x.id === 'hl_place' && !x.disabled));
  assert.equal(d0.find((x) => x.id === 'hl_clear').disabled, true);

  const m1 = M.buildMenuModel({ code: 'X', marks: M.readMarks(null), data: EMPTY, hlines: [10.5, 11] });
  const d1 = m1.find((x) => x.id === 'draw').sub;
  assert.equal(d1.find((x) => x.id === 'hl_clear').disabled, false);
  assert.equal(d1.find((x) => x.id === 'hl_clear').tag, '2 条');
});

// ---------------------------------------------------------------- 价格轴（镜像 frontend.js）

it('日线轴：yOf/priceAtY 互逆，区间取 close+yellow+white', () => {
  const sc = M.scaleFor('daily', { bars: [{ close: 10 }, { close: 20, yellow: 25, white: 8 }] }, 400, 300);
  assert.equal(sc.lo, 8);
  assert.equal(sc.hi, 25);
  const y = sc.yOf(16.5);
  assert.ok(Math.abs(sc.priceAtY(y) - 16.5) < 1e-9);
  assert.ok(sc.yOf(sc.hi) <= sc.rect.t + 1e-9, '最高价贴顶');
  assert.ok(sc.yOf(sc.lo) >= sc.rect.b - 1e-9, '最低价贴底');
});

it('分时轴：以昨收为轴心上下等距（与 drawIntraday 同口径）', () => {
  const d = { pre_close: 10, points: [{ close: 10.5, avg: 10.2 }, { close: 9.8, avg: 10.1 }] };
  const sc = M.scaleFor('intraday', { d }, 500, 300);
  const mid = (sc.lo + sc.hi) / 2;
  assert.ok(Math.abs(mid - 10) < 1e-9, '中轴必须落在昨收');
  assert.ok(Math.abs((sc.hi - 10) - (10 - sc.lo)) < 1e-9, '上下等距');
  assert.ok(Math.abs(sc.priceAtY(sc.yOf(10.2)) - 10.2) < 1e-9);
});

it('轴：没数据/没尺寸一律返回 null，不猜价位', () => {
  assert.equal(M.scaleFor('daily', { bars: [] }, 400, 300), null);
  assert.equal(M.scaleFor('intraday', { d: { points: [] } }, 400, 300), null);
  assert.equal(M.scaleFor('daily', { bars: [{ close: 10 }] }, 0, 300), null);
  assert.equal(M.scaleFor('weekly', { bars: [{ close: 10 }] }, 400, 300), null);
});

// ---------------------------------------------------------------- 口径漂移守卫

it('日线轴只有一份：drawDaily 必须调 marks.dailyAxis，不许自备范围', () => {
  const src = fsmod.readFileSync(new URL('../../frontend.js', import.meta.url), 'utf8');
  assert.ok(/from '\.\/frontend\/marks\.js(\?v=[^']+)?'/.test(src), 'drawDaily 未引用共享轴');
  assert.ok(src.includes('dailyAxis(bars, w, h)'), 'drawDaily 未调 dailyAxis');
  assert.ok(!src.includes('closeVals'), 'drawDaily 里还留着旧的 close 范围计算');
  assert.ok(!src.includes("['close', 'yellow', 'white']"), 'drawDaily 里还留着手写的取值表');
  assert.equal(M.GEOM.daily.pad, 28);
  // P1b：分时也收敛成唯一轴（drawIntraday 调 marks.intradayAxis），不再镜像两份常量
  assert.ok(src.includes('intradayAxis(d, w, h, overlayExtent(series))'), 'drawIntraday 未用共享轴');
  assert.ok(!/const padL = 56, padR = 8/.test(src), 'drawIntraday 还留着自算的边距');
  assert.deepEqual(M.GEOM.intraday, { padL: 56, padR: 8, padT: 10, padB: 18 });
});

console.log('\nP1a marks.js: ' + n + ' 项全过');

// ================================================================
// 验收返修（2026-10-27 第二轮）：日线价格轴必须覆盖 high/low，且绘图与标记层同轴。
// 做法：给 drawDaily 一个记录型假 canvas/ctx，把真实绘制出的 y 坐标读回来，
// 与 marks.js::scaleFor 的 yOf 对表 —— 这样「两条轴是否同一份」是被实测证明的，不靠读代码猜。
// ================================================================

globalThis.document = {
  documentElement: {},
  getElementById: () => ({}),          // 装作样式已注入，绕开 createElement/head 分支
  head: { appendChild() {} },
  createElement: () => ({ set textContent(v) {}, id: '' }),
};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
globalThis.window = { devicePixelRatio: 1 };   // setup() 读 DPR

const { drawDaily } = await import(new URL('../../frontend.js', import.meta.url).href);

function recCtx() {
  const ops = [];
  const ctx = {
    ops,
    setTransform() {}, scale() {}, clearRect() {}, beginPath() {}, closePath() {},
    setLineDash() {}, stroke() {}, fill() {}, save() {}, restore() {},
    moveTo(x, y) { ops.push({ op: 'moveTo', x, y }); },
    lineTo(x, y) { ops.push({ op: 'lineTo', x, y }); },
    fillRect(x, y, w, h) { ops.push({ op: 'fillRect', x, y, w, h }); },
    fillText(t, x, y) { ops.push({ op: 'fillText', t, x, y }); },
    arc(x, y, r) { ops.push({ op: 'arc', x, y, r }); },
  };
  return ctx;
}

function recCanvas(w, h) {
  const ctx = recCtx();
  return {
    clientWidth: w, clientHeight: h, width: 0, height: 0,
    getContext: () => ctx, _ops: ctx.ops, _w: w, _h: h,
  };
}

// high/low 故意超出 close(+黄白线) 区间：这是「蜡烛出界」的最小复现
const SPIKY = [
  { date: '20260105', open: 10, high: 20, low: 5, close: 12, yellow: null, white: null },
  { date: '20260106', open: 12, high: 18, low: 11, close: 13, yellow: 11.5, white: 12.2 },
  { date: '20260107', open: 13, high: 14, low: 12.5, close: 13.2, yellow: 12.1, white: 12.8 },
];

const PAD = M.GEOM.daily.pad;

it('返修A：drawDaily 的蜡烛不许超出绘图区（high/low 越界复现）', () => {
  const cv = recCanvas(600, 320);
  drawDaily(cv, SPIKY, null);
  // 只看画笔落点（线/实体），文本标签本来就画在轴上方（max/min 数字、图例），不算出界
  const ys = cv._ops.filter((o) => (o.op === 'moveTo' || o.op === 'lineTo' || o.op === 'fillRect')
    && typeof o.y === 'number').map((o) => o.y);
  assert.ok(ys.length > 0, '必须真的画了东西');
  const top = Math.min.apply(null, ys), bottom = Math.max.apply(null, ys);
  assert.ok(top >= PAD - 0.5, '最高价画出上边界：top=' + top.toFixed(2) + ' < pad=' + PAD);
  assert.ok(bottom <= cv._h - PAD + 0.5, '最低价画出下边界：bottom=' + bottom.toFixed(2));
});

it('返修B：标记层 yOf 与 drawDaily 实际落笔的极值同轴', () => {
  const cv = recCanvas(600, 320);
  drawDaily(cv, SPIKY, null);
  // 只看画笔落点（线/实体），文本标签本来就画在轴上方（max/min 数字、图例），不算出界
  const ys = cv._ops.filter((o) => (o.op === 'moveTo' || o.op === 'lineTo' || o.op === 'fillRect')
    && typeof o.y === 'number').map((o) => o.y);
  const top = Math.min.apply(null, ys), bottom = Math.max.apply(null, ys);
  const sc = M.scaleFor('daily', { bars: SPIKY }, cv._w, cv._h);
  assert.ok(Math.abs(sc.yOf(20) - top) < 1e-6, '最高价 y 与标记层不一致：draw=' + top + ' mark=' + sc.yOf(20));
  assert.ok(Math.abs(sc.yOf(5) - bottom) < 1e-6, '最低价 y 与标记层不一致：draw=' + bottom + ' mark=' + sc.yOf(5));
});

it('返修C：dailyAxis 的范围覆盖 开/高/低/收 + 黄白线', () => {
  const ax = M.dailyAxis(SPIKY, 600, 320);
  assert.equal(ax.lo, 5, '下限必须是当日最低价');
  assert.equal(ax.hi, 20, '上限必须是当日最高价');
  const only = [{ open: 8, high: 9, low: 7, close: 8.5 }];
  const ax2 = M.dailyAxis(only, 600, 320);
  assert.deepEqual([ax2.lo, ax2.hi], [7, 9], '只有 OHLC 没有黄白线时也要覆盖高低');
});

it('返修D：open==close（十字星）不崩、实体至少 1px', () => {
  const bars = [{ open: 10, high: 10.5, low: 9.5, close: 10 }, { open: 10, high: 10.2, low: 9.8, close: 10 }];
  const cv = recCanvas(400, 300);
  drawDaily(cv, bars, null);
  const bodies = cv._ops.filter((o) => o.op === 'fillRect' && o.h <= 300 && o.h > 0);
  assert.ok(bodies.length >= 2, '每根都要有实体');
  assert.ok(bodies.every((b) => b.h >= 1), '实体不许 0 高');
  // x 与 y 都要有限：共享轴曾因 xAt 缺 n 算出 NaN 横坐标（蜡烛全挤到左边画不出来）
  const bad = cv._ops.filter((o) => (typeof o.x === 'number' || typeof o.y === 'number')
    && (!Number.isFinite(o.x) || !Number.isFinite(o.y)));
  assert.equal(bad.length, 0, '不许有 NaN 坐标：' + JSON.stringify(bad.slice(0, 3)));
});

it('返修E：单根也是合法图（x 居中、坐标全有限、轴可反算）', () => {
  const bars = [{ open: 10, high: 11, low: 9, close: 10.5 }];
  const cv = recCanvas(400, 300);
  drawDaily(cv, bars, null);
  const xs = cv._ops.filter((o) => o.op === 'moveTo' || o.op === 'lineTo').map((o) => o.x);
  const center = PAD + (400 - PAD * 2) * 0.5;
  assert.ok(xs.every((x) => Number.isFinite(x)), '单根的横坐标必须是数，不是 NaN');
  assert.ok(xs.every((x) => Math.abs(x - center) < 1e-9), '单根必须画在中轴');
  const sc = M.scaleFor('daily', { bars: bars }, 400, 300);
  assert.ok(Number.isFinite(sc.yOf(11)) && Math.abs(sc.priceAtY(sc.yOf(11)) - 11) < 1e-9);
});

it('返修F：全平数据不产生 NaN/Infinity', () => {
  const bars = [{ open: 10, high: 10, low: 10, close: 10 }, { open: 10, high: 10, low: 10, close: 10 }];
  const cv = recCanvas(400, 300);
  drawDaily(cv, bars, null);
  const badY = cv._ops.filter((o) => typeof o.y === 'number' && !Number.isFinite(o.y));
  assert.equal(badY.length, 0, 'DBG ' + JSON.stringify(badY.slice(0, 3)));
  const sc = M.scaleFor('daily', { bars: bars }, 400, 300);
  assert.ok(Number.isFinite(sc.yOf(10)) && Number.isFinite(sc.priceAtY(150)));
});

it('返修G：日线轴与分时轴共用同一个 _axis 公式（同一把尺子）', () => {
  // 同一组价、同尺寸下：可视区内的中点价位应落在绘图区竖向中点
  const d = M.scaleFor('daily', { bars: [{ open: 8, high: 12, low: 8, close: 12 }] }, 400, 300);
  const mid = d.yOf((d.lo + d.hi) / 2);
  assert.ok(Math.abs(mid - (d.rect.t + d.rect.b) / 2) < 1e-9);
});

console.log('\nP1a 返修用例：' + n + ' 项全过');

// ================================================================
// P1b（2026-10-27）：分时唯一轴 + 叠加涨跌幅对齐 + 纸面只读标记。
// 先写用例（此时函数还不存在 → 全红），再实现。
// ================================================================

const SLOTS = [];
for (let h = 9, m = 30; !(h === 11 && m === 31); m++) { if (m === 60) { m = 0; h++; } SLOTS.push(String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0')); }
for (let h = 13, m = 1; !(h === 15 && m === 1); m++) { if (m === 60) { m = 0; h++; } SLOTS.push(String(h).padStart(2,'0') + ':' + String(m).padStart(2,'0')); }

function iday(points, pre, slots) {
  return { ok: true, pre_close: pre, points: points, session: { slots: slots || SLOTS } };
}
const IPTS = SLOTS.map((t, i) => ({ i, t, close: 10 + Math.sin(i / 9) * 0.2, avg: 10 + Math.sin(i / 11) * 0.1 }));

it('P1b-1 分时轴：0% 落在竖向中点（昨收为轴心）', () => {
  const ax = M.intradayAxis(iday(IPTS, 10), 500, 300);
  assert.ok(ax, '分时轴算不出来');
  const y0 = ax.yOfPct(0);
  assert.ok(Math.abs(y0 - (ax.rect.t + ax.rect.b) / 2) < 1e-9, '0% 不在中轴：' + y0);
  assert.ok(Math.abs(ax.yOf(ax.pre) - y0) < 1e-9, '昨收价与 0% 必须同一条线');
});

it('P1b-2 分时轴：涨跌幅与价格互逆，且上下等距', () => {
  const ax = M.intradayAxis(iday(IPTS, 10), 500, 300);
  const p = 2.5;
  assert.ok(Math.abs((ax.hi / ax.pre - 1) * 100 + (ax.lo / ax.pre - 1) * 100) < 1e-9, '上下不对称');
  const y = ax.yOfPct(p);
  assert.ok(Math.abs(ax.yOf(10 * (1 + p / 100)) - y) < 1e-9, 'pct 与价格通路不等价');
});

it('P1b-3 分时轴：叠加范围必须并进同一画面（否则线出界）', () => {
  const base = M.intradayAxis(iday(IPTS, 10), 500, 300);
  const wide = M.intradayAxis(iday(IPTS, 10), 500, 300, { lo: -8, hi: 6 });
  assert.ok(wide.spanPct >= 7.99, '叠加 8% 的线没并进轴：' + wide.spanPct);
  assert.ok(wide.spanPct > base.spanPct, '并进后范围应更大');
  // 对称轴取两侧绝对值的大者（8%）→ -8% 贴底、+8% 贴顶；+6% 在内侧
  assert.ok(Math.abs(wide.yOfPct(-8) - wide.rect.b) < 1.0, '叠加最低点应贴绘图区底部');
  assert.ok(Math.abs(wide.yOfPct(8) - wide.rect.t) < 1.0, '叠加最大幅度应贴绘图区顶部');
  const y6 = wide.yOfPct(6);
  assert.ok(y6 > wide.rect.t && y6 < (wide.rect.t + wide.rect.b) / 2, '+6% 应在上半区且不出界：' + y6);
});

it('P1b-4 分时轴：无昨收 → 返回 null（不假设 0 轴瞎画）', () => {
  assert.equal(M.intradayAxis(iday(IPTS, null), 500, 300), null);
  assert.equal(M.intradayAxis(iday([], 10), 500, 300), null);
  assert.equal(M.intradayAxis(iday(IPTS, 10), 0, 300), null);
});

it('P1b-5 alignPctToSlots：按时间槽对齐，缺槽留空不猜', () => {
  const j = { pre_close: 10, session: { slots: ['09:30', '09:31', '11:30'] },
    points: [{ close: 10.1 }, { close: 10.2 }, { close: 9.9 }] };
  const main = ['09:30', '09:31', '11:30', '13:01'];
  const out = M.alignPctToSlots(j, main);
  assert.equal(out.length, 4);
  assert.ok(Math.abs(out[0] - 1) < 1e-9 && Math.abs(out[1] - 2) < 1e-9);
  assert.ok(out[2] < -0.99 && out[2] > -1.01);
  assert.equal(out[3], null, '主图有而叠加没有的槽必须是空，不能补值');
});

it('P1b-6 alignPctToSlots：停牌/空点 → 空洞 + 无效点丢弃', () => {
  const j = { pre_close: 10, session: { slots: ['09:30', '09:31', '09:32'] },
    points: [{ close: 10 }, { close: 0 }, { close: 10.5 }] };
  const out = M.alignPctToSlots(j, ['09:30', '09:31', '09:32']);
  assert.equal(out[0], 0);
  assert.equal(out[1], null, '0 价是脏点，必须留空');
  assert.ok(Math.abs(out[2] - 5) < 1e-9);
});

it('P1b-7 alignPctToSlots：没有时间标签就不对齐（不许按序号硬塞）', () => {
  const j = { pre_close: 10, session: { slots: [] }, points: [{ close: 10.1 }, { close: 10.2 }] };
  assert.equal(M.alignPctToSlots(j, ['09:30', '09:31']), null);
});

it('P1b-8 overlayExtent：取全部叠加线的极值，全空返回 null', () => {
  assert.deepEqual(M.overlayExtent([{ pct: [1, -2, null] }, { pct: [3, null] }]), { lo: -2, hi: 3 });
  assert.equal(M.overlayExtent([{ pct: [null, null] }]), null);
  assert.equal(M.overlayExtent([]), null);
});

it('P1b-9 菜单：日线不给叠加入口（禁用 + 原因）', () => {
  const md = M.buildMenuModel({ code: '600519.SH', period: 'daily', marks: M.readMarks(null), data: EMPTY, hlines: [], sectorCode: '881126.TI' });
  const ov = md.find((x) => x.id === 'overlay').sub;
  ['ov_sector', 'ov_index', 'ov_custom'].forEach((id) => {
    const it0 = ov.find((x) => x.id === id);
    assert.equal(it0.disabled, true, id + ' 日线必须禁用');
    assert.ok(it0.reason && it0.reason.length > 0, id + ' 必须说明原因');
  });
});

it('P1b-10 菜单：分时可用；无行业映射时行业项禁用并说明', () => {
  const md = M.buildMenuModel({ code: '600519.SH', period: 'intraday', marks: M.readMarks(null), data: EMPTY, hlines: [], sectorCode: null });
  const ov = md.find((x) => x.id === 'overlay').sub;
  assert.ok(!ov.find((x) => x.id === 'ov_index').disabled, '大盘应可点');
  assert.ok(!ov.find((x) => x.id === 'ov_custom').disabled, '自定义应可点');
  const sec = ov.find((x) => x.id === 'ov_sector');
  assert.equal(sec.disabled, true);
  assert.ok(/映射/.test(sec.reason), '必须说无行业映射，而不是静默');
});

it('P1b-11 菜单：有叠加时「清除」可点，没有时禁用并说明', () => {
  const none = M.buildMenuModel({ code: 'X', period: 'intraday', marks: M.readMarks(null), data: EMPTY, hlines: [], overlay: null });
  assert.equal(none.find((x) => x.id === 'overlay').sub.find((x) => x.id === 'ov_clear').disabled, true);
  const one = M.buildMenuModel({ code: 'X', period: 'intraday', marks: M.readMarks(null), data: EMPTY, hlines: [], overlay: { kind: 'index', name: '上证指数' } });
  const clr = one.find((x) => x.id === 'overlay').sub.find((x) => x.id === 'ov_clear');
  assert.equal(clr.disabled, false);
  assert.ok(/上证/.test(clr.tag), '要显示当前叠加的是谁');
});

it('P1b-12 标记原因可覆盖：0 条 ≠ 请求失败 ≠ 未接入', () => {
  const av = M.availability(EMPTY, { bs: '近 120 日无成交', cost: '请求失败：HTTP 500' });
  assert.equal(av.bs.ok, false);
  assert.equal(av.bs.reason, '近 120 日无成交');
  assert.equal(av.cost.reason, '请求失败：HTTP 500');
  const md = M.buildMenuModel({ code: 'X', period: 'intraday', marks: M.readMarks(null), data: EMPTY, hlines: [],
    reasons: { order: '当前无 REPORTED/PARTIAL 委托' } });
  const rows = md.find((x) => x.id === 'marks').sub;
  assert.equal(rows.find((x) => x.id === 'mk_order').reason, '当前无 REPORTED/PARTIAL 委托');
});

it('P1b-13 纸面数据适配：买卖点/委托/条件单/成本只取 paper', () => {
  const trades = { points: [{ date: '20261021', t: '09:30', price: 46.45, side: 'BUY' }], cost: { paper: 46.4551, live: 99.9 } };
  const orders = { orders: [
    { ts_code: '002812.SZ', status: 'REPORTED', limit_price: 45.5, side: 'BUY', qty: 100 },
    { ts_code: '002812.SZ', status: 'CANCELLED', limit_price: 1, side: 'BUY', qty: 1 },
    { ts_code: '600519.SH', status: 'REPORTED', limit_price: 1600, side: 'BUY', qty: 100 },
  ], conditions: [
    { code: '002812.SZ', status: 'ACTIVE', trigger: { price: 42 }, label: '保本止损' },
    { code: '002812.SZ', status: 'CANCELLED', trigger: { price: 30 }, label: '旧' },
  ] };
  const d = M.paperMarkData('002812.SZ', trades, orders);
  assert.equal(d.bs.length, 1);
  assert.equal(d.cost, 46.4551, '只能用 paper 成本，绝不能用 live');
  assert.equal(d.order.length, 1, '只留本票 REPORTED/PARTIAL');
  assert.equal(d.order[0].price, 45.5);
  assert.equal(d.cond.length, 1, '只留本票 ACTIVE 条件单');
  const onlyLive = M.paperMarkData('X', { points: [], cost: { paper: null, live: 12 } }, { orders: [], conditions: [] });
  assert.equal(onlyLive.cost, null, 'live 成本不许当成本线');
});

it('P1b-14 纸面数据适配：接口失败 vs 空数据分开报', () => {
  const d = M.paperMarkData('X', null, null);
  assert.deepEqual([d.bs.length, d.order.length, d.cond.length, d.cost], [0, 0, 0, null]);
  assert.ok(d.failed.trades && d.failed.orders, '没给数据要标失败，不能冒充 0 条');
  const ok = M.paperMarkData('X', { points: [], cost: { paper: null } }, { orders: [], conditions: [] });
  assert.deepEqual(ok.failed, { trades: false, orders: false });
});

console.log('\nP1b 用例：' + n + ' 项全过');

// ================================================================
// P1b 返修（2026-10-27 第三轮）：行业码适配 / 槽位对齐 / drawIntraday 真装配。
// 先写用例（此时 mapOverlayCode 不存在、align 仍按 own[k] 猜）→ 先红。
// ================================================================

it('R1 行业码适配：88[01456]xxx.TI → 同 6 位 .SH，其它 .TI 一律不猜', () => {
  assert.equal(typeof M.mapOverlayCode, 'function', 'mapOverlayCode 未实现');
  const cases = [
    ['881126.TI', '881126.SH'],
    ['880008.TI', '880008.SH'],
    ['884290.TI', '884290.SH'],
    ['885000.TI', '885000.SH'],
    ['886001.TI', '886001.SH'],
    ['881126.ti', '881126.SH'],   // 大小写不敏感
  ];
  cases.forEach(([inp, req]) => {
    const r = M.mapOverlayCode(inp);
    assert.equal(r.req, req, inp + ' 请求码不对');
    assert.equal(r.mapped, true, inp + ' 应标记为已映射');
    assert.equal(r.ths, true, inp + ' 应保留 THS 身份');
    assert.equal(r.input, inp.toUpperCase(), '原始码要留着做展示/身份');
  });
  // 不属于 880/881/884/885/886 的 .TI 不许猜成 SH
  ['887126.TI', '899050.TI', '000001.TI'].forEach((inp) => {
    const r = M.mapOverlayCode(inp);
    assert.equal(r.mapped, false, inp + ' 不该被映射');
    assert.equal(r.req, inp, inp + ' 请求码必须原样');
  });
  // 非 .TI 码原样透传
  ['600519.SH', '399001.SZ', '000001.SH'].forEach((inp) => {
    const r = M.mapOverlayCode(inp);
    assert.equal(r.req, inp);
    assert.equal(r.mapped, false);
    assert.equal(r.ths, false);
  });
  assert.equal(M.mapOverlayCode(''), null);
  assert.equal(M.mapOverlayCode(null), null);
});

it('R3 align：points 带 i/t（compat 形状）时按 i 索引落槽，午后点不许错位到上午', () => {
  const main = SLOTS.slice();
  const j = { pre_close: 10, session: { slots: main },
    points: [{ i: 121, t: '13:01', close: 10.1 }, { i: 122, t: '13:02', close: 10.2 }, { i: 123, t: '13:03', close: 9.9 }] };
  const out = M.alignPctToSlots(j, main);
  assert.ok(out[121] !== null, '13:01 必须落在 121');
  assert.ok(Math.abs(out[121] - 1) < 1e-9);
  assert.equal(out[0], null, '上午不能被午后点污染');
  assert.equal(out[3], null);
  assert.equal(out.length, main.length);
});

it('R3 align：points 缺槽（跳空）→ 按各自时间落位，中间留空不补', () => {
  const main = SLOTS.slice();
  const j = { pre_close: 10, session: { slots: main },
    points: [{ i: 0, t: '09:30', close: 10.0 }, { i: 5, t: '09:35', close: 10.5 }, { i: 11, t: '09:41', close: 9.5 }] };
  const out = M.alignPctToSlots(j, main);
  assert.equal(out[0], 0);
  assert.ok(Math.abs(out[5] - 5) < 1e-9);
  assert.ok(Math.abs(out[11] + 5) < 1e-9);
  assert.equal(out[1], null);
  assert.equal(out[6], null);
  assert.equal(out.filter((v) => v !== null).length, 3, '只落 3 个点，不许被补齐');
});

it('R3 align：没有 i 也没有 t 时不猜（own 是全量槽表 ≠ 点数 → 不能拿 own[k] 硬套）', () => {
  const main = SLOTS.slice();
  const j = { pre_close: 10, session: { slots: main }, points: [{ close: 10.1 }, { close: 10.2 }] };
  assert.equal(M.alignPctToSlots(j, main), null, '无时间信息的点必须拒绝对齐');
});

it('R3 align：own 槽表与点数等长（逐点时间）时仍可按 own[k] 对齐', () => {
  const j = { pre_close: 10, session: { slots: ['09:30', '13:01'] }, points: [{ close: 10.1 }, { close: 10.2 }] };
  const out = M.alignPctToSlots(j, ['09:30', '13:01', '13:02']);
  assert.ok(Math.abs(out[0] - 1) < 1e-9);
  assert.ok(Math.abs(out[1] - 2) < 1e-9);
  assert.equal(out[2], null);
});

// ---------------------------------------------------------------- drawIntraday 真装配测试

function recCtx2() {
  const ops = [];
  const ctx = {
    ops, strokes: [], _moves: 0,
    strokeStyle: '', fillStyle: '', lineWidth: 1, globalAlpha: 1, font: '', textAlign: '',
    setTransform() {}, scale() {}, clearRect() {}, beginPath() { this._moves = 0; }, closePath() {},
    setLineDash() {}, save() {}, restore() {}, fill() {},
    moveTo(x, y) { this._moves++; ops.push({ op: 'moveTo', x, y }); },
    lineTo(x, y) { ops.push({ op: 'lineTo', x, y }); },
    fillRect(x, y, w, h) { ops.push({ op: 'fillRect', x, y, w, h }); },
    fillText(t, x, y) { ops.push({ op: 'fillText', t, x, y, h: 0 }); },
    arc(x, y, r) { ops.push({ op: 'arc', x, y, r }); },
    stroke() { this.strokes.push({ color: this.strokeStyle, width: this.lineWidth, moves: this._moves }); },
  };
  return ctx;
}
function recCanvas2(w, h) {
  const ctx = recCtx2();
  return { clientWidth: w, clientHeight: h, width: 0, height: 0, getContext: () => ctx, _ops: ctx.ops, _strokes: ctx.strokes, _w: w, _h: h };
}
const { drawIntraday } = await import(new URL('../../frontend.js', import.meta.url).href);
const finite = (ops) => ops.every((o) => (typeof o.x !== 'number' || Number.isFinite(o.x)) && (typeof o.y !== 'number' || Number.isFinite(o.y)));
function ipts(n, pre) {
  return { ok: true, pre_close: pre, session: { slots: SLOTS.slice(0, n) },
    points: SLOTS.slice(0, n).map((t, i) => ({ i, t, close: 10 + Math.sin(i / 7) * 0.3, avg: 10 + Math.sin(i / 9) * 0.15, volume: 1 })) };
}

it('R2 drawIntraday：有 avg → 主图线与均价线都真的落了笔', () => {
  const cv = recCanvas2(520, 260);
  drawIntraday(cv, ipts(241, 10), null);
  assert.ok(finite(cv._ops), '出现 NaN/Infinity 坐标');
  const widths = cv._strokes.map((s) => Number(s.width));
  assert.ok(widths.some((w) => Math.abs(w - 1.6) < 1e-9), '主价格线没画（width 1.6）');
  assert.ok(widths.some((w) => Math.abs(w - 1.1) < 1e-9), '均价线没画（width 1.1）');
  const vertices = cv._ops.filter((o) => o.op === 'moveTo' || o.op === 'lineTo').length;
  assert.ok(vertices > 241, '主图顶点数明显偏少：' + vertices);
});

it('R2 drawIntraday：avg 全 null → 不崩、不画均价线、坐标仍有限', () => {
  const d = ipts(60, 10);
  d.points.forEach((p) => { p.avg = null; });
  const cv = recCanvas2(520, 260);
  drawIntraday(cv, d, null);
  assert.ok(finite(cv._ops));
  assert.ok(!cv._strokes.some((s) => Math.abs(Number(s.width) - 1.1) < 1e-9), '没有均价就不该画均价线');
});

it('R2 drawIntraday：无昨收 → 早退并写明原因，不乱画', () => {
  const cv = recCanvas2(520, 260);
  drawIntraday(cv, ipts(30, null), null);
  assert.equal(cv._strokes.length, 0, '缺昨收时不该画任何线');
  const txt = cv._ops.filter((o) => o.op === 'fillText').map((o) => String(o.t)).join('|');
  assert.ok(txt.includes('缺昨收'), '必须写明缺昨收，而不是空白：' + txt);
});

it('R2 drawIntraday：叠加 null 断点 → 断成多段但仍落笔；主图与叠加都在', () => {
  const d = ipts(241, 10);
  const pct = new Array(241).fill(null);
  for (let i = 0; i < 60; i++) pct[i] = Math.sin(i / 8) * 1.5;
  for (let i = 61; i < 120; i++) pct[i] = Math.sin(i / 8) * 1.5;   // 60 处断点
  const cv = recCanvas2(520, 260);
  drawIntraday(cv, d, { series: [{ label: '上证指数', color: '#4da3ff', pct: pct }] });
  assert.ok(finite(cv._ops));
  const ovStroke = cv._strokes.find((s) => Math.abs(Number(s.width) - 1.3) < 1e-9);
  assert.ok(ovStroke, '叠加线没画（width 1.3）');
  assert.ok(ovStroke.moves >= 2, 'null 断点应断成两段，实际起笔 ' + ovStroke.moves + ' 次');
  assert.ok(cv._strokes.some((s) => Math.abs(Number(s.width) - 1.6) < 1e-9), '主图线不能被叠加顶掉');
  assert.ok(cv._ops.some((o) => o.op === 'fillText' && String(o.t).includes('上证指数')), '叠加图例没画');
});

it('R2 drawIntraday：slots 含 13:01 → 午休后点位落在轴上不塌陷', () => {
  const d = ipts(241, 10);
  const cv = recCanvas2(520, 260);
  drawIntraday(cv, d, null);
  const AX = M.intradayAxis(d, 520, 260, null);
  const i1301 = SLOTS.indexOf('13:01');
  assert.equal(i1301, 121);
  const x1301 = AX.xAt(i1301, AX.n);
  const xs = cv._ops.filter((o) => o.op === 'moveTo' || o.op === 'lineTo').map((o) => o.x);
  assert.ok(xs.some((x) => Math.abs(x - x1301) < 1e-9), '13:01 的横坐标与轴不一致');
  assert.ok(x1301 > AX.xAt(120, AX.n), '13:01 必须在 11:30 右侧');
});

it('R2 drawIntraday：单点 → 居中、坐标有限、不崩', () => {
  const d = { ok: true, pre_close: 10, session: { slots: ['09:30'] }, points: [{ i: 0, t: '09:30', close: 10.2, avg: 10.2 }] };
  const cv = recCanvas2(400, 300);
  drawIntraday(cv, d, null);
  assert.ok(finite(cv._ops));
  const AX = M.intradayAxis(d, 400, 300, null);
  const xs = cv._ops.filter((o) => o.op === 'moveTo' || o.op === 'lineTo').map((o) => o.x);
  // 网格/刻度也在这批坐标里，只要求「价格线那个点」落在中轴
  assert.ok(xs.some((x) => Math.abs(x - (AX.rect.l + AX.rect.r) / 2) < 1e-9), '单点应画在绘图区中轴');
});

console.log('\nP1b 返修用例：' + n + ' 项全过');
