// P2 单测：纸面下单模型（校验 / 请求体 / 记录筛选 / 提交闸门 / 端点常量）。
// 只测纯函数与注入式 fetch 桩——**绝不发真实 POST**。
import assert from 'node:assert/strict';

const M = await import(new URL('../../frontend/order_model.js', import.meta.url).href);

let n = 0;
const it = (name, fn) => { fn(); n++; console.log('ok ' + n + ' - ' + name); };

function fakeStore(seed) {
  const m = Object.assign({}, seed || {});
  return { getItem: (k) => (Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); }, _dump: () => m };
}

// ---------------------------------------------------------------- 数量/费用

it('整手取整：向下按 100 股，非法值给 0', () => {
  assert.equal(M.clampLot(199), 100);
  assert.equal(M.clampLot(100), 100);
  assert.equal(M.clampLot(99), 0);
  assert.equal(M.clampLot(-100), 0);
  assert.equal(M.clampLot(null), 0);
});

it('金额→数量：价格非法给 0，不猜', () => {
  assert.equal(M.amountToQty(10000, 10), 1000);
  assert.equal(M.amountToQty(999, 10), 0, '不足一手给 0');
  assert.equal(M.amountToQty(10000, 0), 0);
  assert.equal(M.amountToQty(0, 10), 0);
});

it('快捷比例：1/4、1/3、1/2、全部都按整手向下取整', () => {
  assert.equal(M.ratioQty(1000, 0.25), 200);
  assert.equal(M.ratioQty(1000, 0.3333), 300);
  assert.equal(M.ratioQty(1000, 0.5), 500);
  assert.equal(M.ratioQty(1000, 1), 1000);
  assert.equal(M.ratioQty(150, 1), 100, '不足一手要向下取');
});

it('费用口径与旧版一致：买万 1.1、卖万 6.1', () => {
  assert.ok(Math.abs(M.feeOf('buy', 10000) - 1.1) < 1e-9);
  assert.ok(Math.abs(M.feeOf('sell', 10000) - 6.1) < 1e-9);
  assert.equal(M.feeOf('buy', null), 0);
});

// ---------------------------------------------------------------- 记忆键

it('记忆键沿用 ko_*，坏值回退', () => {
  const s = fakeStore();
  assert.equal(M.readQtyMode(s), 'qty');
  M.saveQtyMode(s, 'amt'); assert.equal(M.readQtyMode(s), 'amt');
  assert.equal(s._dump().ko_qty_mode, 'amt');
  const bad = fakeStore({ ko_qty_mode: 'weird', ko_amount_last: 'abc', ko_qty_last: '-5', ko_lock: 'x' });
  assert.equal(M.readQtyMode(bad), 'qty');
  assert.equal(M.readAmountLast(bad), 0);
  assert.equal(M.readQtyLast(bad), 0);
  assert.equal(M.readLock(bad), false);
  M.saveAmountLast(s, 12345.6); assert.equal(M.readAmountLast(s), 12346);
  M.saveLock(s, true); assert.equal(M.readLock(s), true);
});

// ---------------------------------------------------------------- 买卖校验

it('买入：缺价/零量/非整手/超资金都要拦', () => {
  const base = { code: '600519.SH', side: 'buy', orderType: 'limit', availCash: 100000, availQty: null };
  assert.equal(M.validateOrder(Object.assign({}, base, { qty: 100 })).ok, false, '限价缺价要拦');
  assert.equal(M.validateOrder(Object.assign({}, base, { qty: 0, price: 10 })).ok, false);
  const odd = M.validateOrder(Object.assign({}, base, { qty: 150, price: 10 }));
  assert.equal(odd.ok, false); assert.ok(odd.errors.join().includes('100 股整数倍'));
  const rich = M.validateOrder(Object.assign({}, base, { qty: 20000, price: 10 }));
  assert.equal(rich.ok, false); assert.ok(rich.errors.join().includes('可用资金不足'));
  assert.equal(M.validateOrder(Object.assign({}, base, { qty: 100, price: 10 })).ok, true);
});

it('买入：可用资金未知 → 判不可用（不拿 0 冒充足额）', () => {
  const r = M.validateOrder({ code: 'X', side: 'buy', qty: 100, price: 10, orderType: 'limit', availCash: null });
  assert.equal(r.ok, false);
  assert.ok(r.errors.join().includes('可用资金未知'));
});

it('卖出：持仓未知/超可卖要拦；市价单不要价格', () => {
  const s1 = M.validateOrder({ code: 'X', side: 'sell', qty: 100, orderType: 'market', availQty: null });
  assert.equal(s1.ok, false); assert.ok(s1.errors.join().includes('持仓未知'));
  const s2 = M.validateOrder({ code: 'X', side: 'sell', qty: 300, orderType: 'market', availQty: 200 });
  assert.ok(s2.errors.join().includes('超过持仓可卖'));
  const ok = M.validateOrder({ code: 'X', side: 'sell', qty: 200, orderType: 'market', availQty: 200 });
  assert.equal(ok.ok, true);
});

// ---------------------------------------------------------------- 请求体

it('place payload 字段与 OrderPlaceBody 对齐；市价 price=null', () => {
  const a = M.buildPlacePayload({ code: '600519.SH', side: 'buy', qty: 100, price: 100.123, orderType: 'limit', name: '贵州茅台', availCash: 100000 });
  assert.equal(a.ok, true);
  assert.deepEqual(Object.keys(a.payload).sort(), ['code', 'name', 'note', 'order_type', 'price', 'qty', 'side']);
  assert.equal(a.payload.price, 100.123);
  assert.equal(a.payload.note, 'panel');
  const b = M.buildPlacePayload({ code: 'X', side: 'sell', qty: 100, price: null, orderType: 'market', availQty: 100 });
  assert.equal(b.ok, true);
  assert.equal(b.payload.price, null);
  assert.equal(b.payload.order_type, 'market');
});

it('条件单：十一类都要能出 payload；缺必填要报错', () => {
  assert.equal(M.COND_TYPES.length, 11);
  assert.equal(M.COND_TYPES.filter((t) => t.needs.indexOf('target') >= 0).length, 2,
    '2026-10-28 新增的两类低吸单靠 target 识别');
  const base = { code: '600519.SH', qty: 100, triggerPrice: 9.5, pct: 3, at: '14:55', side: 'sell', signal: 'BREAKOUT', now: new Date('2026-10-27T10:00:00') };
  M.COND_TYPES.forEach((t) => {
    const r = M.buildConditionPayload(Object.assign({ type: t.key }, base));
    assert.equal(r.ok, true, t.key + ' 应能出 payload：' + (r.errors || []).join());
    assert.equal(r.payload.type, t.key);
    assert.equal(Object.keys(r.payload).indexOf('code') >= 0, true);
  });
  const miss = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100 });
  assert.equal(miss.ok, false);
  assert.ok(miss.errors.join().includes('触发价'));
  // 回落/反弹：不要求触发价（core add_condition 与旧版 condBodyHtml 都只要幅度）
  const trail = M.buildConditionPayload({ code: 'X', type: 'trailing_sell', qty: 100, pct: 1.5 });
  assert.equal(trail.ok, true);
  assert.equal('trigger_price' in trail.payload, false, '不该写不存在的 trigger_price');
  const trailNoPct = M.buildConditionPayload({ code: 'X', type: 'trailing_sell', qty: 100 });
  assert.equal(trailNoPct.ok, false);
  assert.ok(trailNoPct.errors.join().includes('幅度'));
});

it('条件单：类型专属字段与默认值按旧版', () => {
  const now = new Date('2026-10-27T10:00:00');
  const trail = M.buildConditionPayload({ code: 'X', type: 'trailing_sell', qty: 100, triggerPrice: 10, pct: 2.5, now });
  assert.equal(trail.payload.pct, 2.5);
  assert.equal('trigger_price' in trail.payload, false, '回落型不写 trigger_price');

  const to = M.buildConditionPayload({ code: 'X', type: 'time_order', qty: 100, side: 'sell', at: '', now });
  assert.equal(to.payload.at, '14:55', '默认时间');
  assert.equal(to.payload.side, 'sell');

  const sig = M.buildConditionPayload({ code: 'X', type: 'signal_buy', qty: 100, signal: '', now });
  assert.equal(sig.payload.signal, 'BREAKOUT');

  ['breakeven_stop', 'ma5_break'].forEach((k) => {
    const r = M.buildConditionPayload({ code: 'X', type: k, qty: 100, now });
    assert.equal(r.payload.buffer_atr, 0.3, k + ' buffer_atr 应为 0.3');
  });

  const vu = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100, triggerPrice: 9, validDays: 3, now });
  assert.equal(vu.payload.valid_until, '20261030');
  const oco = M.buildConditionPayload({ code: '600519.SH', type: 'stop_loss', qty: 100, triggerPrice: 9, oco: true, now });
  assert.ok(/^OCO-600519\.SH-/.test(oco.payload.group));
  const lim = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100, triggerPrice: 9, orderType: 'limit', limitPrice: 8.88, now });
  assert.equal(lim.payload.order_type, 'limit');
  assert.equal(lim.payload.limit_price, 8.88);
});

it('低吸两类：目标位枚举按类型分开，tol 0.0001–0.05，payload 只带 target/tol', () => {
  const now = new Date('2026-10-28T10:00:00');
  // 1) 枚举：与 core add_condition 的 allowed 一致
  assert.deepEqual(M.targetValues('ma_pullback_buy'), ['auto', 'yellow', 'white']);
  assert.deepEqual(M.targetValues('key_k_break'), ['auto', 'upper', 'lower']);
  assert.deepEqual(M.targetValues('stop_loss'), [], '九类不参与 target');
  assert.equal(M.TOL_MIN, 0.0001);
  assert.equal(M.TOL_MAX, 0.05);
  assert.equal(M.DEFAULT_TOL, 0.005);

  // 2) payload：带 target/tol，且不带 trigger_price
  const ma = M.buildConditionPayload({ code: 'X', type: 'ma_pullback_buy', qty: 100, target: 'white', tol: 0.003, now });
  assert.equal(ma.ok, true);
  assert.equal(ma.payload.target, 'white');
  assert.equal(ma.payload.tol, 0.003);
  assert.equal('trigger_price' in ma.payload, false, '低吸单没有固定触发价');
  assert.equal(ma.payload.type, 'ma_pullback_buy');

  // 3) 缺省：auto + 0.5%
  const def = M.buildConditionPayload({ code: 'X', type: 'key_k_break', qty: 100, now });
  assert.equal(def.ok, true);
  assert.equal(def.payload.target, 'auto');
  assert.equal(def.payload.tol, 0.005);

  // 4) 非法目标位（黄线不属于关键K）/ 大小写归一
  const bad = M.buildConditionPayload({ code: 'X', type: 'key_k_break', qty: 100, target: 'yellow', now });
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.join().includes('目标位'));
  assert.equal(M.buildConditionPayload({ code: 'X', type: 'ma_pullback_buy', qty: 100, target: ' YELLOW ', now }).payload.target, 'yellow');

  // 5) tol 边界：0.0001/0.05 过，0 与 0.0501 不过，0.5 这种百分数写法也不过
  const tol = (v) => M.buildConditionPayload({ code: 'X', type: 'ma_pullback_buy', qty: 100, tol: v, now }).ok;
  assert.equal(tol(0.0001), true);
  assert.equal(tol(0.05), true);
  assert.equal(tol(0), false);
  assert.equal(tol(0.0501), false);
  assert.equal(tol(0.5), false, '面板传的是小数占比，不是百分数');
});

it('信号枚举与 core SIGNAL_NAMES 一致（不许自己加）', () => {
  assert.deepEqual(M.SIGNALS, ['BREAKOUT', 'PULLBACK', 'VWAP_RECLAIM', 'HOOK_TURN', 'WEAK_TO_STRONG']);
  const r = M.buildConditionPayload({ code: 'X', type: 'signal_buy', qty: 100, signal: 'VWAP_RECLAIM' });
  assert.equal(r.payload.signal, 'VWAP_RECLAIM');
});

it('触发价默认/有效期选项/ATR 选项按旧版', () => {
  assert.equal(M.defaultTrigger('stop_loss', 10), 9.8);
  assert.equal(M.defaultTrigger('take_profit', 10), 10.2);
  assert.equal(M.defaultTrigger('breakout_buy', 10), 10.1);
  assert.equal(M.defaultTrigger('trailing_sell', 10), null, '回落型没有默认触发价');
  assert.deepEqual(M.VALID_OPTIONS, [0, 1, 3, 5, 10, 20]);
  assert.equal(M.DEFAULT_VALID_DAYS, 3);
  assert.deepEqual(M.ATR_OPTIONS, [0, 0.3]);
  const noAtr = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100, triggerPrice: 9, bufferAtr: 0 });
  assert.equal('buffer_atr' in noAtr.payload, false, 'ATR=无 时不该带字段');
  const atr = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100, triggerPrice: 9, bufferAtr: 0.3 });
  assert.equal(atr.payload.buffer_atr, 0.3);
});

it('限价条件单必须填限价；不填就报错而不是静默降级', () => {
  const bad = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100, triggerPrice: 9, orderType: 'limit' });
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.join().includes('限价'));
  const good = M.buildConditionPayload({ code: 'X', type: 'stop_loss', qty: 100, triggerPrice: 9, orderType: 'limit', limitPrice: 8.8 });
  assert.equal(good.payload.limit_price, 8.8);
});

it('撤单 payload 与端点常量', () => {
  assert.deepEqual(M.buildCancelOrderPayload('P1').payload, { order_id: 'P1' });
  assert.deepEqual(M.buildCancelCondPayload('C1').payload, { cond_id: 'C1' });
  assert.equal(M.buildCancelOrderPayload('').ok, false);
  assert.deepEqual(M.ENDPOINTS, {
    place: '/api/order/place', cancelOrder: '/api/order/cancel',
    addCond: '/api/condition/add', cancelCond: '/api/condition/cancel',
  });
});

// ---------------------------------------------------------------- 记录筛选与回执

it('记录：只留可撤的（REPORTED/PARTIAL、ACTIVE），可按票过滤', () => {
  const orders = [
    { order_id: 'a', ts_code: '600519.SH', status: 'REPORTED' },
    { order_id: 'b', ts_code: '600519.SH', status: 'PARTIAL' },
    { order_id: 'c', ts_code: '600519.SH', status: 'FILLED' },
    { order_id: 'd', ts_code: '000001.SZ', status: 'REPORTED' },
  ];
  assert.deepEqual(M.liveOrders(orders).map((o) => o.order_id), ['a', 'b', 'd']);
  assert.deepEqual(M.liveOrders(orders, '600519.SH').map((o) => o.order_id), ['a', 'b']);
  const conds = [
    { cond_id: 'x', code: '600519.SH', status: 'ACTIVE' },
    { cond_id: 'y', code: '600519.SH', status: 'CANCELLED' },
    { cond_id: 'z', code: '000001.SZ', status: 'ACTIVE' },
  ];
  assert.deepEqual(M.liveConds(conds, '600519.SH').map((c) => c.cond_id), ['x']);
});

it('回执：FILLED/PARTIAL/REPORTED 分别给可读文案，其余原样', () => {
  assert.ok(M.receiptLine({ order: { status: 'FILLED', filled_qty: 100, filled_price: 10 } }).includes('已成交 100 股 @ 10.00'));
  assert.ok(M.receiptLine({ order: { status: 'PARTIAL', filled_qty: 100, filled_price: 10 } }).includes('部成'));
  assert.ok(M.receiptLine({ order: { status: 'REPORTED' } }).includes('已挂单'));
  assert.ok(M.receiptLine({ order: { status: 'REJECTED' } }).includes('REJECTED'));
});

// ---------------------------------------------------------------- 提交闸门

it('闸门：未确认不发、确认后只发一次、busy 期间再点无效', () => {
  const g = M.submitGuard();
  assert.equal(g.isArmed(), false);
  assert.equal(g.begin(), false, '未 arm 不许发');
  g.arm(3000);
  assert.equal(g.isArmed(), true);
  assert.equal(g.begin(), true, '第一次确认后放行');
  assert.equal(g.isBusy(), true);
  assert.equal(g.begin(), false, 'busy 期间第二次点击必须无效（防双击）');
  g.end();
  assert.equal(g.isBusy(), false);
  assert.equal(g.begin(), false, 'end 后仍需重新确认');
});

it('闸门：arm 超时自动解除', async () => {
  const g = M.submitGuard();
  g.arm(1000);
  await new Promise((r) => setTimeout(r, 1100));
  assert.equal(g.isArmed(), false);
});

console.log('\nP2：' + n + ' 项全过');
