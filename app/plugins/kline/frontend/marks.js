// K 线弹窗标记层（2026-10-27 · 01 · P1a）：右键菜单模型 + 标记线口径 + 尺寸/开关持久化。
//
// 职责：只给 `overlay.js` 提供**纯函数**——不碰 DOM、不发请求、不读全局状态。
//   · 为什么单独一个文件：菜单能不能给出「有数据 / 无数据及原因」是这一档的验收核心，
//     把它做成纯函数才能用 node 直接单测（overlay.js 本身要 DOM，跑不了）。
//   · 为什么不做成渲染函数：DOM 与 canvas 绘制留在 overlay.js，本文件只算「该画什么」。
//
// 数据源与口径（唯一源都在旧版，别在这里另立）：
//   · 标记开关 localStorage key = `ko_marks`，字段 bs/cost/order/cond 布尔，默认全开
//     （旧版 engine/web/kline_order.js::marks() / saveMarks()）。
//   · 弹窗尺寸 key = `klm_size_v1`（弹窗专属，不覆盖旧版 `kline_lc_settings_v1` 等其它设置）。
//   · 手工水平线 key = `klm_hlines_v1`，按代码存数组，每图最多 MAX_HLINES 条
//     （旧版水平线是「单点类工具：按下即落库」，见 kline_lc.js 绘图段注释；这里只保留这一种）。
//
// 上下游：overlay.js ← 本文件；本文件不反向依赖 overlay.js。
// 口径同步点（GEOM）：价格轴几何镜像 frontend.js 的 drawDaily / drawIntraday 常量，
//   改一边必须同步另一边（frontend.js 不许改，所以改这里的人要回去核那两处）。
//   tests/test_kline_overlay_p1a.py 有对应漂移守卫。

const MARKS_KEY = 'ko_marks';
const SIZE_KEY = 'klm_size_v1';
const HLINE_KEY = 'klm_hlines_v1';

const MARK_KEYS = ['bs', 'cost', 'order', 'cond'];
const MARK_LABELS = {
  bs: '买卖点',
  cost: '持仓成本线',
  order: '委托线',
  cond: '条件单线',
};
// 「没有数据」时的原因文案。本阶段（P1a）买卖点/成本/委托/条件单数据源都还没接到 v2，
// 所以默认全是这句话；P1b 接上后由 overlay 通过 setMarkData() 灌数据，原因自然消失。
const MARK_REASONS = {
  bs: '无成交记录',
  cost: '无持仓成本',
  order: '无委托',
  cond: '无条件单',
};

// frontend.js: drawDaily 用 `const pad = 28`；drawIntraday 用 padL/padR/padT/padB。
const GEOM = {
  daily: { pad: 28 },
  intraday: { padL: 56, padR: 8, padT: 10, padB: 18 },
};

const MIN_W = 560;     // 弹窗最窄（旧版 .kbox min-width 520 + 边距）
const MIN_H = 260;     // 主图最矮 = CSS .klm-cv 的 min-height（比它小就存了个用不上的尺寸）
const MAX_HLINES = 20; // 每张图最多手工线，防误点堆成墙

// ---------------------------------------------------------------- 本地存取

function _store(store) {
  if (store) return store;
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch (e) { return null; }
}

function _get(store, key) {
  const s = _store(store);
  if (!s) return null;
  try { return s.getItem(key); } catch (e) { return null; }
}

function _set(store, key, val) {
  const s = _store(store);
  if (!s) return false;
  try { s.setItem(key, val); return true; } catch (e) { return false; }
}

function _num(v) {
  // 注意 null/'' 必须先挡：Number(null) === 0、Number('') === 0，直接用会把「没有这个字段」
  // （如 yellow/white 为 null）当成 0 价参与算轴 → 轴下限被拉到 0，蜡烛挤成一团。
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// ---------------------------------------------------------------- 标记开关（ko_marks）

/** 读四项开关；默认全开，坏值逐项回落到默认，不让一条坏 JSON 把四项一起关掉。 */
function readMarks(store) {
  const out = { bs: true, cost: true, order: true, cond: true };
  const raw = _get(store, MARKS_KEY);
  if (!raw) return out;
  let s = null;
  try { s = JSON.parse(raw); } catch (e) { return out; }
  if (!s || typeof s !== 'object') return out;
  MARK_KEYS.forEach(function (k) { if (k in s) out[k] = !!s[k]; });
  return out;
}

function saveMarks(store, m) {
  const out = { bs: true, cost: true, order: true, cond: true };
  MARK_KEYS.forEach(function (k) { if (m && k in m) out[k] = !!m[k]; });
  return _set(store, MARKS_KEY, JSON.stringify(out));
}

// ---------------------------------------------------------------- 弹窗尺寸（klm_size_v1）

/** 读尺寸；没存过 / 坏值 → null（null = 用 CSS 默认，不写死像素）。 */
function readSize(store) {
  const raw = _get(store, SIZE_KEY);
  if (!raw) return null;
  let s = null;
  try { s = JSON.parse(raw); } catch (e) { return null; }
  if (!s || typeof s !== 'object') return null;
  const w = _num(s.w), h = _num(s.h);
  if (w === null || h === null || w <= 0 || h <= 0) return null;
  return { w: Math.round(w), h: Math.round(h) };
}

function saveSize(store, size) {
  if (!size) return false;
  return _set(store, SIZE_KEY, JSON.stringify({ w: Math.round(size.w), h: Math.round(size.h) }));
}

/**
 * 把尺寸夹进当前视口。
 * 为什么必须夹：尺寸是上次记的，换窗口/缩窗口后可能比屏幕还大 —— 不夹就会画出屏幕，
 * 而且 canvas clientWidth 也不会因此变小（这正是原先 resize 只重画不改尺寸的坑）。
 */
function clampSize(size, view) {
  if (!size) return null;
  const vw = _num(view && view.w) || 0;
  const vh = _num(view && view.h) || 0;
  let w = Math.max(_num(size.w) || MIN_W, MIN_W);
  let h = Math.max(_num(size.h) || MIN_H, MIN_H);
  // 视口拿不到（内嵌 webview / 隐藏标签页会报 0）时只夹下限，
  // 不能直接返回 null —— 那会让拖角与尺寸记忆集体失效（2026-10-27 实测碰到）。
  if (vw > 0) w = Math.min(w, Math.max(MIN_W, Math.round(vw - 40)));
  if (vh > 0) h = Math.min(h, Math.max(MIN_H, Math.round(vh * 0.62)));
  return { w: Math.round(w), h: Math.round(h) };
}

// ---------------------------------------------------------------- 手工水平线（klm_hlines_v1）

function readHlines(store, code) {
  const c = String(code || '');
  if (!c) return [];
  const raw = _get(store, HLINE_KEY);
  if (!raw) return [];
  let all = null;
  try { all = JSON.parse(raw); } catch (e) { return []; }
  if (!all || typeof all !== 'object') return [];
  const arr = all[c];
  if (!Array.isArray(arr)) return [];
  return arr.map(_num).filter(function (p) { return p !== null && p > 0; });
}

function saveHlines(store, code, arr) {
  const c = String(code || '');
  if (!c) return false;
  let all = {};
  const raw = _get(store, HLINE_KEY);
  if (raw) { try { const o = JSON.parse(raw); if (o && typeof o === 'object') all = o; } catch (e) {} }
  all[c] = (arr || []).slice(0, MAX_HLINES);
  return _set(store, HLINE_KEY, JSON.stringify(all));
}

/** 加一条；同价（两位小数内相同）不重复，超上限丢最旧的一条。返回新数组。 */
function addHline(store, code, price) {
  const p = _num(price);
  const cur = readHlines(store, code);
  if (p === null || p <= 0) return cur;
  const rounded = Math.round(p * 100) / 100;
  const same = cur.some(function (x) { return Math.abs(x - rounded) < 0.005; });
  if (same) return cur;
  const next = cur.concat([rounded]);
  while (next.length > MAX_HLINES) next.shift();
  saveHlines(store, code, next);
  return next;
}

function clearHlines(store, code) {
  saveHlines(store, code, []);
  return [];
}

// ---------------------------------------------------------------- 价格轴（镜像 frontend.js）

/**
 * 日线价格轴（唯一源）。图（frontend.js::drawDaily）与标记层/右键反算都用这一份。
 * 2026-10-27 验收返修：原先只取 close+yellow+white，而 Y 又拿去定位 开/高/低，
 * 最高/最低超出该区间时蜡烛就画出图外；标记层只镜像了同一个错轴 → 反算价与实际图价对不上。
 * 现口径：开/高/低/收 + 黄白线 全参与定范围。
 */
function dailyAxis(bars, w, h) {
  const arr = bars || [];
  const W = _num(w), H = _num(h);
  if (!arr.length || !(W > 0) || !(H > 0)) return null;
  const pad = GEOM.daily.pad;
  const vals = [];
  arr.forEach(function (b) {
    ['open', 'high', 'low', 'close', 'yellow', 'white'].forEach(function (k) {
      const v = _num(b && b[k]);
      if (v !== null) vals.push(v);
    });
  });
  if (!vals.length) return null;
  // xAt 的 n 可缺省：drawDaily 全程只传 i（原实现靠闭包里的 n），不补默认值就会算出 NaN 横坐标。
  const n0 = arr.length;
  const ax = _axis(vals, { l: pad, r: W - pad, t: pad, b: H - pad }, function (i, n) {
    const N = n || n0;
    return pad + (W - pad * 2) * (N === 1 ? 0.5 : i / (N - 1));
  });
  ax.pad = pad;
  return ax;
}

/**
 * 按周期算价格轴 + 反算，用于把「标记线价格」落到画布像素。
 * kind: 'daily' | 'intraday'；payload: 日线 {bars,states} / 分时 {d} 或分时对象本身。
 * 返回 null = 没有可算的数据（调用方直接不画，不猜一个价位）。
 */
function scaleFor(kind, payload, w, h, extra) {
  const W = _num(w), H = _num(h);
  if (!payload || !(W > 0) || !(H > 0)) return null;

  // 日线一律走 dailyAxis（唯一源）；这里不再自己算一遍，避免两条轴。
  if (kind === 'daily') return dailyAxis(payload.bars, W, H);

  if (kind === 'intraday') {
    return intradayAxis(payload.d || payload, W, H, extra);
  }
  return null;
}

// ---------------------------------------------------------------- 分时轴（唯一源）

/**
 * 分时价格轴（唯一源）：图（frontend.js::drawIntraday）、标记层、右键反算、叠加线全用它。
 * 口径：以昨收为轴心、上下等距 ⇒ 「0% = 竖向中点」；涨跌幅与价格是同一个线性变换，
 * 所以主图刻度（%）与叠加线（%）天然同轴，不存在「线按 pct 算、轴按价格画」的误读。
 * extra：叠加线自己的涨跌幅极值 {lo, hi}（%），必须并进同一区间，否则叠加线会画出图外。
 * 无昨收 / 无点 / 无尺子时返回 null —— 不假设 0 轴瞎画（P1b 用例 4 守）。
 */
function intradayAxis(d, w, h, extra) {
  const W = _num(w), H = _num(h);
  const pts = (d && d.points) || [];
  if (!pts.length || !(W > 0) || !(H > 0)) return null;
  const pre = _num(d.pre_close);
  if (pre === null || pre <= 0) return null;
  const G = GEOM.intraday;
  const vals = [];
  pts.forEach(function (p) {
    const c = _num(p && p.close), a = _num(p && p.avg);
    if (c !== null) vals.push(c);
    if (a !== null) vals.push(a);
  });
  if (!vals.length) return null;
  const rawLo = Math.min.apply(null, vals), rawHi = Math.max.apply(null, vals);
  let span = Math.max(Math.abs(rawHi - pre), Math.abs(pre - rawLo), pre * 0.002);
  const ex = extra || null;
  if (ex) {
    const ext = Math.max(Math.abs(_num(ex.lo) || 0), Math.abs(_num(ex.hi) || 0));
    if (ext > 0) span = Math.max(span, pre * ext / 100);
  }
  const lo = pre - span, hi = pre + span;
  const plotW = W - G.padL - G.padR;
  const own = ((d && d.session) || {}).slots || [];
  const n0 = own.length || pts.length;
  const ax = _axis([lo, hi], { l: G.padL, r: W - G.padR, t: G.padT, b: H - G.padB },
    function (i, n) {
      const N = n || n0;
      return G.padL + plotW * (N <= 1 ? 0.5 : i / (N - 1));
    });
  // 对称轴 ⇒ 上边界就是 +spanPct，下边界就是 -spanPct
  ax.pre = pre;
  ax.spanPct = (span / pre) * 100;
  ax.slots = own;
  ax.n = n0;
  ax.yOfPct = function (p) {
    const v = _num(p);
    return v === null ? null : ax.yOf(pre * (1 + v / 100));
  };
  ax.pctAtY = function (y) {
    const v = ax.priceAtY(y);
    return v === null ? null : (v / pre - 1) * 100;
  };
  return ax;
}

const THS_SECTOR_TI = /^(88[01456]\d{3})\.TI$/i;

/**
 * 行业/板块码适配（只在这一层做，**不动全局 normalize**）。
 * 权威事实：THS 板块指数的身份码是 `881126.TI`（THS DB 的日线/周线口径），
 * 但 eltdx 分时通道只认交易所前缀 `sh881126`（engine/server.py::_eltdx_history_minute_bars 注释实测
 * `sh881101` 可取；compat_kline 的 `.TI` 只用于 THS DB 日/周线，分时没有 THS 实时源）。
 * 所以：`88[01456]xxx.TI` → 同 6 位 `.SH` **只作为请求码**，`.TI` 原码保留做展示/身份。
 * 其它 `.TI` 一律不猜（不把非板块指数硬塞进 SH 通道），非 `.TI` 原样透传。
 */
function mapOverlayCode(code) {
  const raw = String(code == null ? '' : code).trim().toUpperCase();
  if (!raw) return null;
  const m = THS_SECTOR_TI.exec(raw);
  if (m) return { input: raw, req: m[1] + '.SH', mapped: true, ths: true };
  return { input: raw, req: raw, mapped: false, ths: /\.TI$/.test(raw) };
}

/** 分时点 → 涨跌幅序列（各自昨收）；缺少昨收或点 → null。 */
function pctFromIntraday(j) {
  const pre = _num(j && j.pre_close);
  const pts = (j && j.points) || [];
  if (pre === null || pre <= 0 || !pts.length) return null;
  const out = pts.map(function (p) {
    const c = _num(p && p.close);
    return c === null || c <= 0 ? null : (c / pre - 1) * 100;
  });
  return out.some(function (v) { return v !== null; }) ? out : null;
}

/**
 * 把叠加品种的涨跌幅对到主图的时间槽上。
 * 只按时间标签对齐：对不上、没时间标签、脏点一律留 null（= 不画），不按序号硬塞。
 */
function alignPctToSlots(j, slotsMain) {
  const pct = pctFromIntraday(j);
  if (!pct) return null;
  const own = ((j && j.session) || {}).slots || [];
  const main = (slotsMain && slotsMain.length) ? slotsMain : own.slice();
  if (!main.length) return null;
  const pts = j.points || [];
  // own 只有在「逐点时间表」时才等于点数；若是全量槽表，按下标取就会把午后点错配到上午
  const perPoint = own.length === pts.length;
  const out = new Array(main.length).fill(null);
  let hit = 0;
  for (let k = 0; k < pts.length; k++) {
    const p = pts[k] || {};
    let idx = null;
    // ① 点位自带 slot 索引 i（compat_kline 形状）：优先，但用 own[i] 的时间再校一次
    const pi = _num(p.i);
    if (pi !== null && pi >= 0) {
      const byOwn = (own[pi] != null) ? main.indexOf(String(own[pi])) : -1;
      if (byOwn >= 0) idx = byOwn;
      else if (own.length === main.length) idx = Math.round(pi);
    }
    // ② 时间标签 t
    if (idx === null && p.t != null) {
      const byT = main.indexOf(String(p.t));
      if (byT >= 0) idx = byT;
    }
    // ③ 只有「逐点时间表」才允许按下标取；全量槽表 + 少点的情况下绝不猜
    if (idx === null && perPoint && own[k] != null) {
      const byK = main.indexOf(String(own[k]));
      if (byK >= 0) idx = byK;
    }
    if (idx === null || idx < 0 || idx >= main.length) continue;   // 对不上就不画
    out[idx] = pct[k];
    hit++;
  }
  return hit ? out : null;
}

/** 多条叠加线的涨跌幅极值（%）；全空 → null（没东西可并进轴）。 */
function overlayExtent(series) {
  let lo = null, hi = null;
  (series || []).forEach(function (s) {
    ((s && s.pct) || []).forEach(function (p) {
      const v = _num(p);
      if (v === null) return;
      if (lo === null || v < lo) lo = v;
      if (hi === null || v > hi) hi = v;
    });
  });
  return lo === null ? null : { lo: lo, hi: hi };
}

function _axis(vals, rect, xAt) {
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const span = (hi - lo) || (Math.abs(hi) * 0.01 || 1);
  const plotH = Math.max(1, rect.b - rect.t);
  const yOf = function (p) {
    const v = _num(p);
    if (v === null) return null;
    return rect.b - plotH * ((v - lo) / span);
  };
  const priceAtY = function (y) {
    const yy = _num(y);
    if (yy === null) return null;
    return lo + span * ((rect.b - yy) / plotH);
  };
  return {
    lo: lo, hi: hi, rect: rect, plotH: plotH,
    yOf: yOf, priceAtY: priceAtY, xAt: xAt,
    /** 价位是否落在可视区（不落在区内的线不画，避免画到图外误导） */
    inView: function (p) { const v = _num(p); return v !== null && v >= lo - span && v <= hi + span; },
  };
}

// ---------------------------------------------------------------- 标记数据可用性

/**
 * 四类标记各自有没有数据。
 * data 形状（P1b 由 overlay.setMarkData 灌）：{ bs:[{t|date, price, side}], cost:number,
 *   order:[{price, side, qty}], cond:[{price, label}] }
 * 没有数据 → ok=false + reason（菜单要显示原因，绝不画假线）。
 */
function availability(data, reasons) {
  const d = data || {};
  const override = reasons || {};
  const out = {};
  const why = function (k) { return typeof override[k] === 'string' && override[k] ? override[k] : MARK_REASONS[k]; };
  const arrOf = function (v) { return Array.isArray(v) ? v.filter(function (x) { return x && _num(x.price) > 0; }) : []; };

  const bs = arrOf(d.bs);
  out.bs = bs.length ? { ok: true, count: bs.length, reason: '' } : { ok: false, count: 0, reason: why('bs') };

  const cost = _num(d.cost);
  out.cost = cost !== null && cost > 0
    ? { ok: true, count: 1, reason: '' }
    : { ok: false, count: 0, reason: why('cost') };

  const order = arrOf(d.order);
  out.order = order.length ? { ok: true, count: order.length, reason: '' } : { ok: false, count: 0, reason: why('order') };

  const cond = arrOf(d.cond);
  out.cond = cond.length ? { ok: true, count: cond.length, reason: '' } : { ok: false, count: 0, reason: why('cond') };

  return out;
}

/**
 * 该画的线/点。开关关掉的整类跳过；开关开着但没数据的进 missing（供菜单说原因）。
 * 只吐语义信息，颜色由 overlay 现取主题 token（色值不在这里硬编码）。
 */
function lineModels(marks, data) {
  const m = marks || readMarks(null);
  const d = data || {};
  const lines = [], points = [], missing = [];

  if (m.cost) {
    const p = _num(d.cost);
    if (p !== null && p > 0) lines.push({ key: 'cost', price: p, label: '成本 ' + p.toFixed(2) });
    else missing.push('cost');
  }
  if (m.order) {
    const arr = Array.isArray(d.order) ? d.order : [];
    let hit = 0;
    arr.forEach(function (o) {
      const p = _num(o && o.price);
      if (p === null || p <= 0) return;
      hit++;
      const side = o.side === 'BUY' ? '买' : (o.side === 'SELL' ? '卖' : '');
      lines.push({ key: 'order', price: p, label: (side ? side + ' ' : '') + p.toFixed(2) + (o.qty ? '×' + o.qty : '') });
    });
    // 判「缺」看**有效线数**，不看数组长度：脏数据（price<=0）不算有数据
    if (!hit) missing.push('order');
  }
  if (m.cond) {
    const arr = Array.isArray(d.cond) ? d.cond : [];
    let hit = 0;
    arr.forEach(function (o) {
      const p = _num(o && o.price);
      if (p === null || p <= 0) return;
      hit++;
      lines.push({ key: 'cond', price: p, label: (o.label || '条件') + ' ' + p.toFixed(2) });
    });
    if (!hit) missing.push('cond');
  }
  if (m.bs) {
    const arr = Array.isArray(d.bs) ? d.bs : [];
    let hit = 0;
    arr.forEach(function (p) {
      const v = _num(p && p.price);
      if (v === null || v <= 0) return;
      hit++;
      points.push({ price: v, side: p.side || '', at: p.t || p.date || null });
    });
    if (!hit) missing.push('bs');
  }
  return { lines: lines, points: points, missing: missing };
}

// ---------------------------------------------------------------- 右键菜单模型

function _markItems(marks, av) {
  const rows = MARK_KEYS.map(function (k) {
    return {
      id: 'mk_' + k,
      label: MARK_LABELS[k],
      checked: !!marks[k],
      // 有数据报条数；没数据报「无数据」，下面一行写原因 —— 开关本身仍可点（改的是持久化偏好）
      tag: av[k].ok ? av[k].count + ' 条' : '无数据',
      reason: av[k].ok ? '' : av[k].reason,
    };
  });
  const anyOn = MARK_KEYS.some(function (k) { return !!marks[k]; });
  rows.push({ kind: 'sep' });
  rows.push({ id: 'mk_off', label: '全部关闭', disabled: !anyOn, reason: anyOn ? '' : '四项已全关' });
  return rows;
}

function _drawItems(hlines) {
  const n = (hlines || []).length;
  return [
    { id: 'hl_place', label: '画水平线（点图落线）', tag: n ? '已有 ' + n : '' },
    { kind: 'sep' },
    { id: 'hl_clear', label: '清除本图水平线', tag: n ? n + ' 条' : '', disabled: !n, reason: n ? '' : '本图没有手工线' },
  ];
}

/**
 * 菜单结构（纯数据）。overlay.js 只管照着渲染。
 * P1b：叠加对比已开放（仅分时）；下单/条件单仍 disabled 带原因，不出现「点了没反应又说不出为什么」的项。
 * ctx：period / overlay（当前叠加）/ sectorCode（本票行业指数，缺则 null）/ reasons（标记原因覆盖）
 */
function _overlayItems(c) {
  const live = c.period === 'intraday';
  const without = live ? '' : '仅分时支持叠加（日线无昨收基准）';
  const active = c.overlay || null;
  return [
    { id: 'ov_sector', label: '叠加行业走势', tag: c.sectorCode || '',
      disabled: !live || !c.sectorCode,
      reason: without || (c.sectorCode ? '' : 'watchlist 无该票行业映射（不猜行业）') },
    { id: 'ov_index', label: '叠加大盘走势', tag: '000001.SH', disabled: !live, reason: without },
    { id: 'ov_custom', label: '叠加自定义品种…', disabled: !live, reason: without },
    { kind: 'sep' },
    { id: 'ov_clear', label: '清除叠加', tag: active ? (active.name || active.code || '') : '',
      disabled: !active, reason: active ? '' : '当前没有叠加' },
  ];
}

/** 主图设置（2026-10-30 主人定：筹码分布等从「工具」搬到这里，默认显示根数也放这里）。
 *  ctx.tools = { chips, adjust, cost, bs, tpsl }（来自 KLineApp 的 DEFAULTS.tools 快照）
 *  ctx.visibleBars = 当前默认显示根数（localStorage `kline_visible_bars_v1`）
 *  ctx.dongneng = 动能K染色开关（localStorage `klm_dn_v1`，2026-11-02 新增；
 *    弹窗独立存，不进 ctx.tools —— 那套是旧端共享字段） */
function _mainItems(c) {
  const t = c.tools || {};
  const vb = _num(c.visibleBars) || 60;
  return [
    { kind: 'head', label: '默认显示根数' },
    { id: 'vb_60', label: '60 根', checked: vb === 60 },
    { id: 'vb_120', label: '120 根', checked: vb === 120 },
    { id: 'vb_240', label: '240 根', checked: vb === 240 },
    { kind: 'sep' },
    { id: 'tools_chips', label: '筹码分布', checked: !!t.chips },
    { id: 'tools_adjust', label: '复权事件', checked: t.adjust !== false },
    { id: 'tools_cost', label: '成本线', checked: !!t.cost },
    { id: 'tools_bs', label: '买卖点', checked: t.bs !== false },
    { id: 'tools_tpsl', label: '止盈止损线', checked: !!t.tpsl },
    { kind: 'sep' },
    { id: 'dn', label: '动能K染色', checked: !!c.dongneng },
  ];
}

function buildMenuModel(ctx) {
  const c = ctx || {};
  const live = c.period === 'intraday';        // 分时：买卖为主（主人 2026-10-30 定）
  const marks = c.marks || readMarks(null);
  const av = availability(c.data, c.reasons);
  const price = _num(c.price);
  const title = [c.name, c.code].filter(Boolean).join(' ').trim();
  const active = c.overlay || null;
  const head = [
    { kind: 'head', label: title + ' · ' + (price === null ? '—' : price.toFixed(2)) },
    { id: 'refresh', label: '刷新行情', tag: '↻' },
    { kind: 'sep' },
  ];
  // 日线：加入自选 / 条件单预警 / 主图设置（主人 2026-10-30：「日线右键菜单，我希望设置加入自选以及条件单预警」）
  const daily = [
    { id: 'watch', label: c.watched ? '已在自选' : '加入自选', tag: c.watched ? '★' : '☆',
      disabled: !!c.watched, reason: c.watched ? '已在自选池' : '' },
    { id: 'alert', label: '设置预警…' },
    { id: 'main', label: '主图设置', sub: _mainItems(c) },
    { kind: 'sep' },
  ];
  const common = [
    { id: 'draw', label: '手工画线', sub: _drawItems(c.hlines) },
    { id: 'overlay', label: '叠加对比', tag: active ? (active.name || active.code || '') : '',
      sub: _overlayItems(c) },
  ];
  const intradayTail = [
    { kind: 'sep' },
    { kind: 'head', label: '下单 · 本阶段未接入' },
    { id: 'buy', label: '买入（需下单面板）', disabled: true, reason: 'P1c 接入下单面板后开放' },
    { id: 'sell', label: '卖出（需下单面板）', disabled: true, reason: 'P1c 接入下单面板后开放' },
    { id: 'cond', label: '条件单（需写入通道）', disabled: true, reason: 'P1c 接入条件单写入后开放' },
  ];
  if (live) {
    return head.concat(
      [{ id: 'marks', label: '分时标记', sub: _markItems(marks, av) }],
      common, intradayTail);
  }
  return head.concat(daily, common);
}

// ---------------------------------------------------------------- 纸面只读数据适配

/**
 * 把 paper 两个只读接口的原始 payload 适配成 markData（overlay.js 只负责拉，不负责拆）。
 * 口径：
 *   · 买卖点 = /api/trades/{code} 的 points（date=YYYYMMDD、t=HH:MM、price、side），全为纸面成交；
 *   · 成本只取 `cost.paper`，**绝不回退 live**（只认纸面成本线）；
 *   · 委托只留本票 + REPORTED/PARTIAL；条件单只留本票 + ACTIVE。
 * failed 标出哪一侧请求失败：0 条（真的没有）与失败（没拿到）必须分开说。
 */
function paperMarkData(code, trades, orders) {
  const c = String(code || '');
  const out = { bs: [], cost: null, order: [], cond: [], failed: { trades: false, orders: false } };
  if (!trades) out.failed.trades = true;
  else {
    const pts = Array.isArray(trades.points) ? trades.points : [];
    pts.forEach(function (p) {
      const price = _num(p && p.price);
      if (price === null || price <= 0) return;
      out.bs.push({ date: p.date || null, t: p.t || null, price: price, side: p.side || '', mode: p.mode || '' });
    });
    const cp = _num(trades.cost && trades.cost.paper);
    out.cost = (cp !== null && cp > 0) ? cp : null;
  }
  if (!orders) out.failed.orders = true;
  else {
    (Array.isArray(orders.orders) ? orders.orders : []).forEach(function (o) {
      if (!o || String(o.ts_code || '') !== c) return;
      if (o.status !== 'REPORTED' && o.status !== 'PARTIAL') return;
      const price = _num(o.limit_price);
      if (price === null || price <= 0) return;
      out.order.push({ price: price, side: o.side || '', qty: o.qty == null ? null : Number(o.qty) });
    });
    (Array.isArray(orders.conditions) ? orders.conditions : []).forEach(function (o) {
      if (!o || String(o.code || '') !== c) return;
      if (o.status !== 'ACTIVE') return;
      const price = _num(o.trigger && o.trigger.price);
      if (price === null || price <= 0) return;
      out.cond.push({ price: price, label: o.label || '条件' });
    });
  }
  return out;
}

export {
  MARKS_KEY, SIZE_KEY, HLINE_KEY, MARK_KEYS, MARK_LABELS, MARK_REASONS, GEOM,
  MIN_W, MIN_H, MAX_HLINES,
  readMarks, saveMarks,
  readSize, saveSize, clampSize,
  readHlines, saveHlines, addHline, clearHlines,
  scaleFor, availability, lineModels, buildMenuModel, dailyAxis,
  intradayAxis, pctFromIntraday, alignPctToSlots, overlayExtent, paperMarkData, mapOverlayCode,
};

export default {
  readMarks, saveMarks, readSize, saveSize, clampSize,
  readHlines, saveHlines, addHline, clearHlines,
  scaleFor, availability, lineModels, buildMenuModel, dailyAxis,
  intradayAxis, pctFromIntraday, alignPctToSlots, overlayExtent, paperMarkData, mapOverlayCode,
};
