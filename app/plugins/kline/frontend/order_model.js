// 纸面下单模型（2026-10-27 · P2）：纯函数 + 请求体构造，不碰 DOM、不发请求。
//
// 口径来源（均为旧版原文，不在本文件重造规则）：
//   · 面板交互与校验：`engine/web/kline_order.js` orderBodyHtml / submit / submitCond（405–1200）
//   · 费用：buy = 金额×0.00011（佣金万1 + 过户费万0.1），sell = 金额×0.00061（再加印花税万5）
//   · 数量记忆键：`ko_qty_mode` / `ko_amount_last` / `ko_lock` / `ko_qty_last`
//   · 交易主体只有 paper：本文件不带任何 live 开关
//
// 边界：业务校验的**权威**在 paper backend（ConditionAddBody / OrderPlaceBody）；
// 这里只做旧版同款的前置校验（不足一手、超可卖、超可用），防止把明显非法请求发出去。
// 后端若另加校验，前端不复制、也不猜。

const LOT = 100;
const TICK = 0.01;
const FEE_RATE = { buy: 0.00011, sell: 0.00061 };
const MEM = { qtyMode: 'ko_qty_mode', amountLast: 'ko_amount_last', lock: 'ko_lock', qtyLast: 'ko_qty_last' };
/** 写端点（根路径，paper 后端既有；本档未新增任何后端路由）。 */
const ENDPOINTS = {
  place: '/api/order/place',
  cancelOrder: '/api/order/cancel',
  addCond: '/api/condition/add',
  cancelCond: '/api/condition/cancel',
};

/** 旧版九类条件单：label/hint 原文照搬，needs 说明该类型要填哪些字段。 */
const COND_TYPES = [
  { key: 'stop_loss', label: '止损卖出', hint: '现价跌到触发价 → 卖出', needs: ['trigger'] },
  { key: 'take_profit', label: '止盈卖出', hint: '现价涨到触发价 → 卖出', needs: ['trigger'] },
  { key: 'trailing_sell', label: '回落卖出', hint: '自激活后最高价回落到幅度 → 卖出', needs: ['pct'] },
  { key: 'rebound_buy', label: '反弹买入', hint: '自激活后最低价反弹到幅度 → 买入', needs: ['pct'] },
  { key: 'breakout_buy', label: '突破买入', hint: '现价涨到触发价 → 买入', needs: ['trigger'] },
  { key: 'time_order', label: '定时委托', hint: '到点按设定方向委托', needs: ['at', 'side'] },
  { key: 'breakeven_stop', label: '保本止损', hint: '浮盈达 1.5×ATR 后跌回成本 → 全部卖出', needs: ['buffer'] },
  { key: 'ma5_break', label: '破五日线卖出', hint: '盘中破 MA5−0.3ATR 或 14:57 后破 MA5 → 卖出', needs: ['buffer'] },
  { key: 'signal_buy', label: '信号买入', hint: '分钟 K 闭合命中信号 → 市价买入', needs: ['signal'] },
  // 2026-10-28 主人：回踩均线 / 关键K 两类低吸条件单（公式正本 buy_module，后端已支持 target/tol）
  { key: 'ma_pullback_buy', label: '回踩均线买入', hint: '现价触及黄线/白线（多头排列）附近 → 买入', needs: ['target'] },
  { key: 'key_k_break', label: '关键K低吸', hint: '现价触及动能K上沿/下沿 → 买入', needs: ['target'] },
];
const COND_KEYS = COND_TYPES.map((t) => t.key);
/**
 * 2026-10-28：两类低吸条件单的目标位。取值集合与 core `add_condition` 的 allowed 一一对应
 *   （ma_pullback：auto/yellow/white；key_k：auto/upper/lower），多一个少一个后端都会拒。
 * 这里只管「有哪些选项」，线的算法在行情侧（buy_module 正本），本文件不重算。
 */
const TARGET_OPTIONS = {
  ma_pullback_buy: [
    { value: 'auto', label: '自动（多头排列白>黄时按价取线）' },
    { value: 'yellow', label: '黄线' },
    { value: 'white', label: '白线' },
  ],
  key_k_break: [
    { value: 'auto', label: '自动（价在上沿上方用上沿，否则下沿）' },
    { value: 'upper', label: '上沿（收盘）' },
    { value: 'lower', label: '下沿（开盘）' },
  ],
};
/** 容差口径同后端：小数占比 0.0001–0.05（0.01%–5%），默认 0.005（0.5%）。 */
const TOL_MIN = 0.0001;
const TOL_MAX = 0.05;
const DEFAULT_TOL = 0.005;
/** 目标位的可选值（未知类型给 []，不猜）。 */
function targetValues(type) {
  const opts = TARGET_OPTIONS[String(type || '')];
  return opts ? opts.map((o) => o.value) : [];
}
/** 目标位归一：留空按 auto（与后端 `str(target or "auto")` 同口径）。 */
function normTarget(v) {
  const s = String(v == null ? '' : v).trim().toLowerCase();
  return s || 'auto';
}
/** 容差可否接受：缺省=默认值合法，超界/非数字一律拒（后端同样的 0–5% 闸门）。 */
function tolOk(v) {
  if (v === null || v === undefined || v === '') return true;
  const n = num(v);
  return n !== null && n >= TOL_MIN && n <= TOL_MAX;
}
const DEFAULT_VALID_DAYS = 3;                        // 旧版 select 默认 3 天
const VALID_OPTIONS = [0, 1, 3, 5, 10, 20];          // 当日/1/3/5/10/20
const ATR_OPTIONS = [0, 0.3];                        // 无 / 0.3×ATR
/** 触发价默认（旧版 condBodyHtml）：止损 ×0.98、止盈 ×1.02、突破 ×1.01。 */
function defaultTrigger(type, last) {
  const p = num(last);
  if (!(p > 0)) return null;
  if (type === 'stop_loss') return Math.round(p * 0.98 * 100) / 100;
  if (type === 'take_profit') return Math.round(p * 1.02 * 100) / 100;
  if (type === 'breakout_buy') return Math.round(p * 1.01 * 100) / 100;
  return null;
}

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
/** 按 100 股整手向下取整；负数/非法一律 0。 */
function clampLot(qty) {
  const v = Math.floor(num(qty) || 0);
  return v <= 0 ? 0 : Math.floor(v / LOT) * LOT;
}
function amountToQty(amount, price) {
  const a = num(amount), p = num(price);
  if (!(a > 0) || !(p > 0)) return 0;
  return clampLot(a / p);
}
function feeOf(side, amount) {
  const a = num(amount) || 0;
  return a * (side === 'sell' ? FEE_RATE.sell : FEE_RATE.buy);
}
/** 快捷比例：1/4、1/3、1/2、全部（按可买/可卖取整手）。 */
function ratioQty(base, ratio) {
  const b = clampLot(base);
  const r = num(ratio);
  if (!(b > 0) || !(r > 0)) return 0;
  return clampLot(Math.floor(b * r));
}

// ---------------------------------------------------------------- 记忆（ko_*）

function store(s) { return s || (typeof localStorage !== 'undefined' ? localStorage : null); }
function readKv(s, key, fb) {
  try { const v = store(s).getItem(key); return v == null ? fb : v; } catch (e) { return fb; }
}
function writeKv(s, key, val) {
  try { store(s).setItem(key, String(val)); return true; } catch (e) { return false; }
}
/** 数量/金额输入模式：坏值回落 'qty'（旧版同口径）。 */
function readQtyMode(s) { const v = readKv(s, MEM.qtyMode, 'qty'); return v === 'amt' ? 'amt' : 'qty'; }
function saveQtyMode(s, m) { return writeKv(s, MEM.qtyMode, m === 'amt' ? 'amt' : 'qty'); }
function readAmountLast(s) { const v = num(readKv(s, MEM.amountLast, 0)); return v && v > 0 ? Math.round(v) : 0; }
function saveAmountLast(s, v) { const n = num(v); return n && n > 0 ? writeKv(s, MEM.amountLast, Math.round(n)) : false; }
function readLock(s) { return readKv(s, MEM.lock, '0') === '1'; }
function saveLock(s, on) { return writeKv(s, MEM.lock, on ? '1' : '0'); }
function readQtyLast(s) { const v = num(readKv(s, MEM.qtyLast, 0)); return v && v > 0 ? Math.floor(v) : 0; }
function saveQtyLast(s, v) { const n = num(v); return n && n > 0 ? writeKv(s, MEM.qtyLast, Math.floor(n)) : false; }

// ---------------------------------------------------------------- 买卖校验与请求体

/**
 * 下单前置校验（旧版 submit 同款规则）。
 * 资金/持仓为空（null）时**判不可用**，不拿 0 当「余额为零」——旧版也是「没有行情就不给下单」。
 */
function validateOrder(o) {
  const x = o || {};
  const side = x.side === 'sell' ? 'sell' : 'buy';
  const qty = Math.floor(num(x.qty) || 0);
  const price = num(x.price);
  const orderType = x.orderType === 'market' ? 'market' : 'limit';
  const errors = [];
  if (!x.code) errors.push('没有选中股票');
  if (orderType === 'limit' && !(price > 0)) errors.push('限价单要填价格');
  if (qty <= 0) errors.push('数量要大于 0');
  if (side === 'buy' && qty % LOT) errors.push('买入必须是 100 股整数倍');
  const availQty = num(x.availQty);
  const availCash = num(x.availCash);
  if (side === 'sell') {
    if (availQty === null) errors.push('持仓未知，暂不能卖出');
    else if (qty > availQty) errors.push('超过持仓可卖数量');
  } else {
    if (availCash === null) errors.push('可用资金未知，暂不能买入');
    else if (orderType === 'limit' && price > 0 && price * qty > availCash + 0.001) errors.push('可用资金不足');
  }
  return { ok: errors.length === 0, errors: errors, side: side, qty: qty, price: price, orderType: orderType };
}

/** POST /api/order/place 的 body（字段与 OrderPlaceBody 一一对应）。 */
function buildPlacePayload(o) {
  const x = o || {};
  const v = validateOrder(x);
  if (!v.ok) return { ok: false, errors: v.errors };
  return {
    ok: true,
    payload: {
      code: String(x.code),
      side: v.side,
      qty: v.qty,
      price: v.orderType === 'limit' ? v.price : null,
      order_type: v.orderType,
      name: x.name || '',
      note: 'panel',
    },
  };
}

/** 有效期：天数 → YYYYMMDD（旧版用 Date.now()+n 天）。 */
function validUntilFromDays(days, now) {
  const d = num(days);
  if (!(d > 0)) return null;
  const base = now ? new Date(now) : new Date();
  const t = new Date(base.getTime() + Math.round(d) * 86400000);
  return '' + t.getFullYear() + String(t.getMonth() + 1).padStart(2, '0') + String(t.getDate()).padStart(2, '0');
}

// 与 core `manual_orders.SIGNAL_NAMES` 完全一致（唯一枚举，不自己加）
const SIGNALS = ['BREAKOUT', 'PULLBACK', 'VWAP_RECLAIM', 'HOOK_TURN', 'WEAK_TO_STRONG'];

/**
 * POST /api/condition/add 的 body（字段与 ConditionAddBody 一一对应）。
 * 字段组合严格按旧版 submitCond：time_order→at/side；signal_buy→signal；
 * breakeven_stop/ma5_break→buffer_atr=0.3；其余→trigger_price（trailing/rebound 另加 pct）。
 */
function buildConditionPayload(o) {
  const x = o || {};
  const type = String(x.type || '');
  const spec = COND_TYPES.find((t) => t.key === type);
  const errors = [];
  if (!x.code) errors.push('没有选中股票');
  if (!spec) errors.push('未知条件单类型：' + type);
  const qty = Math.floor(num(x.qty) || 0);
  if (qty <= 0) errors.push('数量要大于 0');
  if (spec) {
    if (spec.needs.indexOf('trigger') >= 0 && !(num(x.triggerPrice) > 0)) errors.push('要填触发价');
    if (spec.needs.indexOf('pct') >= 0 && !(num(x.pct) > 0) && !(num(x.amount) > 0)) {
      errors.push('要填幅度%（或金额）');
    }
    if (spec.needs.indexOf('target') >= 0) {
      const allowed = targetValues(type);
      if (allowed.indexOf(normTarget(x.target)) < 0) errors.push('目标位可选 ' + allowed.join('/'));
      if (!tolOk(x.tol)) errors.push('容差需在 0.01%–5% 之间（默认 0.5%）');
    }
    // at/signal 与旧版一致：留空就用默认值（14:55 / BREAKOUT），不拦提交
  }
  if (errors.length) return { ok: false, errors: errors };

  // 信号买入走服务端信号库，core 只支持市价 → 强制 market（不给无效路径）
  const forcedMarket = (type === 'signal_buy');
  if (forcedMarket && x.orderType === 'limit') errors.push('信号买入只支持市价');
  if (errors.length) return { ok: false, errors: errors };
  const payload = {
    code: String(x.code),
    type: type,
    qty: qty,
    order_type: (forcedMarket || x.orderType !== 'limit') ? 'market' : 'limit',
    confirm_count: Math.max(1, Math.floor(num(x.confirmCount) || 1)),
    name: x.name || '',
    note: 'panel',
  };
  const vu = validUntilFromDays(x.validDays, x.now);
  if (vu) payload.valid_until = vu;
  if (x.orderType === 'limit') {
    if (!(num(x.limitPrice) > 0)) return { ok: false, errors: ['限价条件单要填限价'] };
    payload.limit_price = num(x.limitPrice);
  }

  if (type === 'time_order') {
    payload.at = x.at || '14:55';
    payload.side = x.side === 'sell' ? 'sell' : 'buy';
  } else if (type === 'signal_buy') {
    payload.signal = x.signal || 'BREAKOUT';
  } else if (type === 'breakeven_stop' || type === 'ma5_break') {
    payload.buffer_atr = 0.3;                       // 与 QMT 旧系统 stop_buffer_atr 同口径
  } else if (type === 'trailing_sell' || type === 'rebound_buy') {
    // 回落/反弹：只需幅度（或金额），**不写不存在的 trigger_price**
    if (num(x.pct) > 0) payload.pct = num(x.pct);
    else if (num(x.amount) > 0) payload.amount = num(x.amount);
  } else if (spec.needs.indexOf('target') >= 0) {
    // 回踩均线 / 关键K：只带目标位与容差（后端 trigger.target / trigger.tol），**不写 trigger_price**
    const t = normTarget(x.target);
    if (targetValues(type).indexOf(t) < 0) return { ok: false, errors: ['目标位可选 ' + targetValues(type).join('/')] };
    const tl = num(x.tol);
    const tol = (tl === null ? DEFAULT_TOL : tl);
    if (!(tol >= TOL_MIN && tol <= TOL_MAX)) return { ok: false, errors: ['容差需在 0.01%–5% 之间（默认 0.5%）'] };
    payload.target = t;
    payload.tol = Math.round(tol * 10000) / 10000;
  } else {
    payload.trigger_price = num(x.triggerPrice);
    const buf = num(x.bufferAtr);
    if (buf > 0) payload.buffer_atr = buf;            // 0/缺省 = 不带（UI 的「无」）
  }
  if (x.oco) payload.group = 'OCO-' + x.code + '-' + Date.now();
  return { ok: true, payload: payload };
}

function buildCancelOrderPayload(orderId) {
  const id = String(orderId || '');
  return id ? { ok: true, payload: { order_id: id } } : { ok: false, errors: ['缺少委托号'] };
}
function buildCancelCondPayload(condId) {
  const id = String(condId || '');
  return id ? { ok: true, payload: { cond_id: id } } : { ok: false, errors: ['缺少条件单号'] };
}

// ---------------------------------------------------------------- 记录筛选

const ORDER_LIVE = ['REPORTED', 'PARTIAL'];
const COND_LIVE = ['ACTIVE'];

function liveOrders(list, code) {
  return (list || []).filter((o) => o && ORDER_LIVE.indexOf(String(o.status)) >= 0
    && (!code || String(o.ts_code || o.code) === String(code)));
}
function liveConds(list, code) {
  return (list || []).filter((c) => c && COND_LIVE.indexOf(String(c.status)) >= 0
    && (!code || String(c.code) === String(code)));
}
/** 回执一行：FILLED 显示成交价量，REPORTED/PARTIAL 显示挂单/部成，其余原样给状态。 */
function receiptLine(r) {
  const o = (r && r.order) || {};
  const status = String(o.status || '');
  if (status === 'FILLED') return '已成交 ' + (o.filled_qty == null ? '' : o.filled_qty) + ' 股 @ ' + (o.filled_price == null ? '—' : Number(o.filled_price).toFixed(2));
  if (status === 'PARTIAL') return '部成 ' + (o.filled_qty == null ? '' : o.filled_qty) + ' 股 @ ' + (o.filled_price == null ? '—' : Number(o.filled_price).toFixed(2)) + '（可撤余量）';
  if (status === 'REPORTED') return '已挂单，等对手盘（可随时撤）';
  if (!status) return '已提交';
  return '状态：' + status;
}

// ---------------------------------------------------------------- 提交闸门（防双击 / 二次确认）

/**
 * 两次点击两件事：第一次 arm（进入「再点一次确认」），第二次才允许发请求；
 * 请求进行中 busy=true，任何点击都不再发第二个请求（不自动重试）。
 */
function submitGuard() {
  let armed = false, busy = false, timer = null;
  return {
    isArmed: () => armed,
    isBusy: () => busy,
    arm(ttlMs) {
      armed = true;
      if (timer) clearTimeout(timer);
      const ttl = Math.max(1000, num(ttlMs) || 3000);
      timer = setTimeout(() => { armed = false; }, ttl);
      return true;
    },
    disarm() { armed = false; if (timer) { clearTimeout(timer); timer = null; } },
    /** 只有在 armed 且非 busy 时才放行；放行同时进入 busy。 */
    begin() {
      if (busy || !armed) return false;
      busy = true; armed = false;
      if (timer) { clearTimeout(timer); timer = null; }
      return true;
    },
    end() { busy = false; },
  };
}

export {
  LOT, TICK, FEE_RATE, MEM, ENDPOINTS, COND_TYPES, COND_KEYS, SIGNALS,
  TARGET_OPTIONS, TOL_MIN, TOL_MAX, DEFAULT_TOL, targetValues, normTarget, tolOk,
  DEFAULT_VALID_DAYS, VALID_OPTIONS, ATR_OPTIONS, defaultTrigger,
  clampLot, amountToQty, ratioQty, feeOf,
  readQtyMode, saveQtyMode, readAmountLast, saveAmountLast, readLock, saveLock, readQtyLast, saveQtyLast,
  validateOrder, buildPlacePayload, buildConditionPayload, validUntilFromDays,
  buildCancelOrderPayload, buildCancelCondPayload,
  liveOrders, liveConds, receiptLine, submitGuard,
};

export default {
  clampLot, amountToQty, ratioQty, feeOf, validateOrder, buildPlacePayload,
  buildConditionPayload, liveOrders, liveConds, receiptLine, submitGuard, COND_TYPES,
  TARGET_OPTIONS, TOL_MIN, TOL_MAX, DEFAULT_TOL, targetValues, tolOk,
};
