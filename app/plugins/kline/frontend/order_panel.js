// 纸面下单面板（2026-10-27 · P2，第二轮按契约返修重写）：弹窗内浮动面板，四 Tab。
//
// 职责：面板 DOM、表单状态、交互与请求发送。规则/请求体全在 order_model.js（纯函数、可单测）。
// 本轮修的真实契约（不是优化）：
//   · 可卖 = account.positions[code].available_qty（T+1/冻结后口径），**绝不回退 qty**；
//   · 撤单委托号 = `paper_order_id`（旧字段 order_id 有值时兼容），且必须走 model 的 payload 构造；
//   · 条件单信号枚举 = core SIGNAL_NAMES；回落/反弹只需幅度（无触发价）；限价必须填价；
//   · 有效期下拉 当日/1/3/5/10/20（默认 3 天），触发型有 ATR「无/0.3」；
//   · 表单状态跨 Tab/类型/锁定保留，不重设成默认 100；金额默认 lastAmount≥1000 否则 10000；
//   · 记录支持「当前票 / 全部」切换；空 positions → 持仓 0（权威空），不是「未知」。
//
// 写请求（paper 既有端点，本档未加后端）：
//   POST /api/order/place · /api/order/cancel · /api/condition/add · /api/condition/cancel
// 两次点击确认 + busy 锁；失败原样显示，不自动重试。

import * as M from './order_model.js?v=0.9.67';

const PANEL_ID = 'klo-panel';
const TABS = [
  { key: 'buy', label: '买入' },
  { key: 'sell', label: '卖出' },
  { key: 'cond', label: '条件单' },
  { key: 'list', label: '记录' },
];

const CSS = `
#${PANEL_ID}{position:fixed;z-index:9400;width:312px;background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius-lg);box-shadow:var(--shadow-pop);font-size:12px;color:var(--fg);
  padding:8px 9px 7px;user-select:none}
#${PANEL_ID} .klo-hd{display:flex;align-items:center;gap:6px;margin-bottom:6px;cursor:move}
#${PANEL_ID} .klo-title{font-weight:600}
#${PANEL_ID} .klo-code{color:var(--dim);font-family:var(--font-mono);font-size:11px}
#${PANEL_ID} .klo-paper{color:var(--warn-ink);border:1px solid var(--warn);background:var(--warn-bg);
  border-radius:var(--radius-pill);padding:0 6px;font-size:10px}
#${PANEL_ID} .klo-x{margin-left:auto;background:transparent;border:0;color:var(--dim);cursor:pointer;font-size:14px}
#${PANEL_ID} .klo-tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);margin-bottom:7px}
#${PANEL_ID} .klo-tabs button{flex:1 1 0;background:transparent;border:0;color:var(--dim);padding:5px 0;cursor:pointer;font:inherit}
#${PANEL_ID} .klo-tabs button.on{color:var(--fg);border-bottom:2px solid var(--accent)}
#${PANEL_ID} .klo-row{display:flex;align-items:center;gap:6px;margin-bottom:5px}
#${PANEL_ID} .klo-lb{color:var(--dim);min-width:36px}
#${PANEL_ID} input,#${PANEL_ID} select,#${PANEL_ID} .klo-inp{background:var(--card2);color:var(--fg);
  border:1px solid var(--line);border-radius:var(--radius-sm);padding:3px 6px;font:inherit;font-family:var(--font-mono)}
#${PANEL_ID} input[type=number]{width:100%}
#${PANEL_ID} input[readonly]{opacity:.7}
#${PANEL_ID} .klo-grow{flex:1 1 auto;min-width:0}
#${PANEL_ID} .klo-seg{display:inline-flex;border:1px solid var(--line);border-radius:var(--radius-sm);overflow:hidden}
#${PANEL_ID} .klo-seg button{background:transparent;border:0;color:var(--fg);padding:3px 7px;cursor:pointer;font:inherit}
#${PANEL_ID} .klo-seg button.on{background:var(--accent);color:var(--accent-on)}
#${PANEL_ID} .klo-chips{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:5px}
#${PANEL_ID} .klo-chips button{background:var(--card2);border:1px solid var(--line);color:var(--dim);
  border-radius:var(--radius-sm);padding:2px 6px;cursor:pointer;font:inherit;font-size:11px}
#${PANEL_ID} .klo-chips button.on{border-color:var(--accent);color:var(--accent)}
#${PANEL_ID} .klo-chips button:disabled{opacity:.4;cursor:default}
#${PANEL_ID} .klo-info{color:var(--dim);font-size:11px;line-height:1.5;margin:4px 0 6px}
#${PANEL_ID} .klo-info b{color:var(--fg);font-family:var(--font-mono)}
#${PANEL_ID} .klo-submit{width:100%;height:30px;border:0;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-weight:700}
#${PANEL_ID} .klo-submit.buy{background:var(--up-solid);color:var(--up-on)}
#${PANEL_ID} .klo-submit.sell{background:var(--down-solid);color:var(--down-on)}
#${PANEL_ID} .klo-submit.cond{background:var(--accent);color:var(--accent-on)}
#${PANEL_ID} .klo-submit.armed{outline:2px solid var(--warn-solid);outline-offset:1px}
#${PANEL_ID} .klo-submit:disabled{opacity:.55;cursor:default}
#${PANEL_ID} .klo-hint{color:var(--dim);font-size:11px;line-height:1.5;margin:4px 0 6px}
#${PANEL_ID} .klo-list{max-height:200px;overflow:auto;border-top:1px solid var(--line);margin-top:6px}
#${PANEL_ID} .klo-it{display:flex;gap:6px;align-items:center;padding:4px 2px;border-bottom:1px solid var(--line)}
#${PANEL_ID} .klo-it .m{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#${PANEL_ID} .klo-it .st{color:var(--dim);font-size:10px}
#${PANEL_ID} .klo-it button{background:transparent;border:1px solid var(--line);color:var(--dim);
  border-radius:var(--radius-sm);padding:1px 5px;cursor:pointer;font:inherit;font-size:10px}
#${PANEL_ID} .klo-it button.armed{border-color:var(--danger-ink);color:var(--danger-ink)}
#${PANEL_ID} .klo-msg{font-size:11px;line-height:1.5;margin-top:5px;color:var(--dim);word-break:break-all}
#${PANEL_ID} .klo-msg.err{color:var(--danger-ink)}
#${PANEL_ID} .klo-msg.ok{color:var(--down-ink)}
`;

let cssDone = false;
function ensureCss() {
  if (cssDone || typeof document === 'undefined') return;
  const s = document.createElement('style');
  s.id = PANEL_ID + '-css';
  s.textContent = CSS;
  document.head.append(s);
  cssDone = true;
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(v) { const n = Number(v); return Number.isFinite(n) ? n.toFixed(2) : '—'; }
function priceTxt(v) { const n = Number(v); return Number.isFinite(n) && n > 0 ? n.toFixed(2) : ''; }

/**
 * @param {object} o {code, name, getData, post, onWritten, onCondPreview, onClose}
 */
export function createOrderPanel(o) {
  ensureCss();
  const opts = o || {};
  const code = String(opts.code || '');
  const name = String(opts.name || '');
  const guard = M.submitGuard();
  const cancelGuards = {};
  let root = null;
  let busy = false;
  let msg = { text: '', kind: '' };
  let drag = null;
  let onDocumentDown = null;

  // 表单状态：跨 Tab/类型/锁定全部保留（返修 5）
  const F = {
    tab: 'buy',
    price: null, pricePick: 'last', otype: 'limit',
    amountMode: M.readQtyMode(), locked: M.readLock(),
    qty: null,           // 买入/卖出：数量（股）；金额模式下是元
    cond: {
      type: 'stop_loss', trig: null, trigEdited: false, pct: 1.5, at: '14:55', side: 'buy',
      signal: 'BREAKOUT', atr: 0, valid: M.DEFAULT_VALID_DAYS, qty: null,
      otype: 'market', limit: null, conf: 1, oco: false,
      // 2026-10-28：回踩均线 / 关键K 的目标位与容差（tol 是小数占比，0.005 = 0.5%）
      target: 'auto', tol: M.DEFAULT_TOL,
    },
    scope: 'code',       // 记录：当前票 / 全部
  };
  let inited = false;

  function data() { return (opts.getData && opts.getData()) || {}; }
  function post(path, body) {
    if (opts.post) return opts.post(path, body);
    return fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then((r) => r.json().then((j) => {
      if (!r.ok) { const e = new Error((j && (j.detail || j.message)) || ('HTTP ' + r.status)); e.detail = e.message; throw e; }
      return j;
    }));
  }

  /** 首屏默认值：只算一次（除非改票后显式 reset）。 */
  function initDefaults() {
    const d = data();
    const q = d.quote || {};
    if (F.price == null && Number(q.last) > 0) F.price = Number(q.last);
    if (F.qty == null) {
      F.qty = (F.amountMode && F.tab === 'buy')
        ? (M.readAmountLast() >= 1000 ? M.readAmountLast() : 10000)
        : M.LOT;
      if (F.locked && F.tab === 'buy') {
        F.qty = F.amountMode ? (M.readAmountLast() || 10000) : (M.readQtyLast() || M.LOT);
      }
    }
    const c = F.cond;
    if (!c.trigEdited) c.trig = M.defaultTrigger(c.type, Number(q.last));
    if (c.qty == null) c.qty = defaultCondQty();
    inited = true;
  }

  /** 条件单数量默认：可卖 / 持仓 / 100（旧版同口径）。 */
  function defaultCondQty() {
    const d = data();
    const pos = d.position;
    const avail = pos && pos.available_qty != null ? Number(pos.available_qty) : null;
    const qty = pos && pos.qty != null ? Number(pos.qty) : null;
    return (avail && avail > 0) ? M.clampLot(avail) : ((qty && qty > 0) ? M.clampLot(qty) : M.LOT);
  }

  // ---------------------------------------------------------------- 买卖表单

  function orderFormHtml(side) {
    const d = data();
    const q = d.quote || {};
    const pos = d.position;
    const isBuy = side === 'buy';
    const availCash = d.account && d.account.avail_cash != null ? Number(d.account.avail_cash) : null;
    // 可卖只认 available_qty（T+1/冻结后的口径）；有持仓对象但缺该字段 → 未知（不拿 qty 顶）
    const availQty = pos ? (pos.available_qty == null ? null : Number(pos.available_qty)) : null;
    const useAmt = isBuy && F.amountMode === 'amt';
    const disabled = isBuy ? (availCash === null) : (availQty === null);
    const reasons = [];
    if (isBuy && availCash === null) reasons.push('可用资金未知，暂不能买入');
    if (!isBuy && availQty === null) reasons.push(pos ? '持仓缺少可卖字段，暂不能卖出' : '持仓未知，暂不能卖出');
    return ''
      + '<div class="klo-row"><span class="klo-lb">可用</span><b>' + (availCash === null ? '不可用' : '¥' + money(availCash)) + '</b>'
      + (isBuy ? '' : '<span class="klo-lb" style="margin-left:auto">可卖</span><b>' + (availQty === null ? '不可用' : availQty) + '</b>')
      + (isBuy ? '' : '<span class="klo-lb">持仓</span><b>' + (pos && pos.qty != null ? pos.qty : '—') + '</b>')
      + '</div>'
      + '<div class="klo-chips">'
      + chip('last', '最新', q.last) + chip('bid1', '买一', q.bid1) + chip('ask1', '卖一', q.ask1)
      + chip('limit_up', '涨停', q.limit_up) + chip('limit_down', '跌停', q.limit_down)
      + '</div>'
      + '<div class="klo-row"><span class="klo-lb">价格</span><span class="klo-grow"><input id="klo-price" type="number" step="0.01" value="'
      + priceTxt(F.price) + '"></span>'
      + '<button class="klo-step" data-act="step" data-d="-0.01">−</button><button class="klo-step" data-act="step" data-d="0.01">+</button></div>'
      + (isBuy ? '<div class="klo-row"><span class="klo-lb">输入</span><span class="klo-seg" id="klo-qmode">'
        + '<button data-act="qmode" data-v="qty" class="' + (useAmt ? '' : 'on') + '">按数量</button>'
        + '<button data-act="qmode" data-v="amt" class="' + (useAmt ? 'on' : '') + '">按金额</button></span>'
        + '<button class="klo-lock' + (F.locked ? ' on' : '') + '" data-act="lock" title="' + (F.locked ? '已锁：取上次记忆值' : '未锁：可自由填') + '">'
        + (F.locked ? '🔒' : '🔓') + '</button></div>' : '')
      + '<div class="klo-row"><span class="klo-lb">' + (useAmt ? '金额' : '数量') + '</span>'
      + '<span class="klo-grow"><input id="klo-qty" type="number" step="' + (useAmt ? 500 : 100) + '" value="' + (F.qty == null ? '' : F.qty) + '"'
      + ((isBuy && F.locked) ? ' readonly' : '') + '></span></div>'
      + '<div class="klo-chips" id="klo-quick">'
      + [['0.25', '1/4'], ['0.3333', '1/3'], ['0.5', '1/2'], ['1', '全部']]
        .map(([r, lb]) => '<button data-act="ratio" data-r="' + r + '"' + ((isBuy && F.locked) ? ' disabled' : '') + '>' + lb + '</button>').join('')
      + '</div>'
      + '<div class="klo-row"><span class="klo-lb">委托</span><span class="klo-seg" id="klo-otype">'
      + '<button data-act="otype" data-v="limit" class="' + (F.otype === 'limit' ? 'on' : '') + '">限价</button>'
      + '<button data-act="otype" data-v="market" class="' + (F.otype === 'market' ? 'on' : '') + '">市价</button></span></div>'
      + '<div class="klo-info" id="klo-info">' + infoHtml(side) + '</div>'
      + (reasons.length ? '<div class="klo-msg err">' + esc(reasons.join('；')) + '</div>' : '')
      + '<button class="klo-submit ' + side + (guard.isArmed() ? ' armed' : '') + '" data-act="submit" data-side="' + side + '"'
      + (disabled ? ' disabled' : '') + '>'
      + (guard.isArmed() ? '再点一次确认' + (isBuy ? '买入' : '卖出') : (isBuy ? '确认买入' : '确认卖出')) + '</button>'
      + msgHtml();
  }

  function chip(k, label, v) {
    const n = Number(v);
    return '<button data-act="fill" data-f="' + k + '"' + (F.pricePick === k ? ' class="on"' : '') + '>'
      + label + (Number.isFinite(n) && n > 0 ? ' ' + n.toFixed(2) : '') + '</button>';
  }

  function infoHtml(side) {
    const d = data();
    const isBuy = side === 'buy';
    const useAmt = isBuy && F.amountMode === 'amt';
    const price = Number(F.price) || 0;
    const qty = useAmt ? M.amountToQty(F.qty, price) : Math.floor(Number(F.qty) || 0);
    const amt = price * qty;
    const fee = M.feeOf(side, amt);
    const pos = d.position;
    const availCash = d.account && d.account.avail_cash != null ? Number(d.account.avail_cash) : null;
    const total = d.account && d.account.total_asset != null ? Number(d.account.total_asset) : null;
    if (isBuy) {
      const maxQty = (availCash !== null && price > 0) ? M.clampLot(availCash / price) : null;
      const pct = (total && total > 0) ? (amt / total * 100).toFixed(1) + '%' : '—';
      return '可买 <b>' + (maxQty === null ? '不可用' : maxQty) + '</b> 股 · 金额 <b>¥' + money(amt)
        + '</b> · 费≈¥' + fee.toFixed(2) + ' · 占总资产 ' + pct;
    }
    return '持仓 <b>' + (pos && pos.qty != null ? pos.qty : '—') + '</b> 股 · 金额 <b>¥' + money(amt) + '</b> · 费≈¥' + fee.toFixed(2);
  }

  // ---------------------------------------------------------------- 条件单

  function condSpec() { return M.COND_TYPES.find((t) => t.key === F.cond.type) || M.COND_TYPES[0]; }

  /** 两类低吸单的目标位选项（其余类型 null，不影响九类原有表单）。 */
  function targetOpts(type) { return M.TARGET_OPTIONS[type] || null; }
  /** 当前 target 不在本类型可选值里（如黄线 → 关键K）→ 回落 auto，不把非法值送到后端。 */
  function curTarget(c) {
    const vals = M.targetValues(c.type);
    return vals.indexOf(String(c.target || '')) >= 0 ? String(c.target) : 'auto';
  }
  /** 小数占比 → 百分比文本（0.005 → "0.5"）。 */
  function tolPctTxt(v) {
    if (v === null || v === undefined || v === '') return '0.5';
    const n = Number(v);
    if (!Number.isFinite(n)) return '0.5';
    return String(Math.round(n * 10000) / 100);
  }
  /** 面板顶部口径提示（只给这两类看，九类不出现）。 */
  function baseLineHtml(type) {
    if (type === 'ma_pullback_buy') {
      return '黄线 = (MA14+MA28+MA57+MA114)/4 · 白线 = EMA(EMA(C,10),10)'
        + '<br>目标线由 cond_watch 盘中注入行情，本页不下发线值。';
    }
    return '关键K = 放量阳线（上沿 = 收盘 / 下沿 = 开盘）'
      + '<br>目标位由 cond_watch 盘中注入行情，本页不下发线值。';
  }

  function condFormHtml() {
    const c = F.cond;
    const spec = condSpec();
    const needs = spec.needs;
    const trigType = needs.indexOf('trigger') >= 0;
    const tgtOpts = targetOpts(c.type);
    return ''
      + (tgtOpts ? '<div class="klo-info" id="klo-cbase">' + baseLineHtml(c.type) + '</div>' : '')
      + '<div class="klo-row"><span class="klo-lb">类型</span><span class="klo-grow"><select id="klo-ctype">'
      + M.COND_TYPES.map((t) => '<option value="' + t.key + '"' + (t.key === c.type ? ' selected' : '') + '>' + t.label + '</option>').join('')
      + '</select></span></div>'
      + '<div class="klo-hint">' + esc(spec.hint) + '</div>'
      + (tgtOpts
        ? '<div class="klo-row"><span class="klo-lb">目标位</span><span class="klo-grow"><select id="klo-ctarget">'
          + tgtOpts.map((o) => '<option value="' + o.value + '"' + (o.value === curTarget(c) ? ' selected' : '') + '>'
            + esc(o.label) + '</option>').join('')
          + '</select></span></div>'
          + '<div class="klo-row"><span class="klo-lb">容差%</span><span class="klo-grow"><input id="klo-ctol" type="number"'
          + ' step="0.1" min="0" max="5" value="' + tolPctTxt(c.tol) + '"></span></div>'
          + '<div class="klo-hint">现价跌到目标线 ×(1+容差) 以内即买入；容差 0–5%，默认 0.5%。</div>'
        : '')
      + (trigType
        ? '<div class="klo-row"><span class="klo-lb">触发价</span><span class="klo-grow"><input id="klo-ctrig" type="number" step="0.01" value="'
          + priceTxt(c.trig) + '"></span></div>'
          + '<div class="klo-row"><span class="klo-lb">ATR</span><span class="klo-seg" id="klo-catr">'
          + M.ATR_OPTIONS.map((v) => '<button data-act="catr" data-v="' + v + '" class="' + (Number(c.atr) === v ? 'on' : '') + '">'
            + (v === 0 ? '无' : v + '×ATR') + '</button>').join('') + '</span></div>'
        : '')
      + (needs.indexOf('pct') >= 0
        ? '<div class="klo-row"><span class="klo-lb">幅度%</span><span class="klo-grow"><input id="klo-cpct" type="number" step="0.1" value="' + c.pct + '"></span></div>'
          + '<div class="klo-hint">自条件单生效起，反向走这么多即触发（5s 一轮采样）</div>' : '')
      + (needs.indexOf('at') >= 0
        ? '<div class="klo-row"><span class="klo-lb">时间</span><span class="klo-grow"><input id="klo-cat" type="time" value="' + esc(c.at) + '"></span>'
          + '<span class="klo-seg" id="klo-cside"><button data-act="cside" data-v="buy" class="' + (c.side === 'buy' ? 'on' : '') + '">买</button>'
          + '<button data-act="cside" data-v="sell" class="' + (c.side === 'sell' ? 'on' : '') + '">卖</button></span></div>' : '')
      + (needs.indexOf('signal') >= 0
        ? '<div class="klo-row"><span class="klo-lb">信号</span><span class="klo-grow"><select id="klo-csig">'
          + M.SIGNALS.map((s) => '<option value="' + s + '"' + (s === c.signal ? ' selected' : '') + '>' + s + '</option>').join('')
          + '</select></span></div>' : '')
      + (needs.indexOf('buffer') >= 0 ? '<div class="klo-hint">buffer_atr 固定 0.3（与 QMT 旧系统同口径）</div>' : '')
      + '<div class="klo-row"><span class="klo-lb">数量</span><span class="klo-grow"><input id="klo-cqty" type="number" step="100" value="' + (c.qty == null ? '' : c.qty) + '"></span></div>'
      + '<div class="klo-row"><span class="klo-lb">委托</span><span class="klo-seg" id="klo-cotype">'
      + '<button data-act="cotype" data-v="market" class="' + ((c.otype === 'market' || c.type === 'signal_buy') ? 'on' : '') + '">市价</button>'
      + '<button data-act="cotype" data-v="limit" class="' + ((c.otype === 'limit' && c.type !== 'signal_buy') ? 'on' : '')
      + '"' + (c.type === 'signal_buy' ? ' disabled title="信号买入只支持市价"' : '') + '>限价</button></span>'
      + (c.type === 'signal_buy' ? '<span class="klo-hint">信号买入只支持市价</span>' : '')
      + (c.otype === 'limit'
        ? '<span class="klo-grow"><input id="klo-cprice" type="number" step="0.01" value="' + priceTxt(c.limit) + '" placeholder="限价"></span>'
        : '')
      + '</div>'
      + '<div class="klo-row"><span class="klo-lb">有效期</span><select id="klo-cvalid">'
      + M.VALID_OPTIONS.map((v) => '<option value="' + v + '"' + (Number(c.valid) === v ? ' selected' : '') + '>'
        + (v === 0 ? '当日' : v + ' 天') + '</option>').join('')
      + '</select><span class="klo-lb" style="margin-left:auto">确认</span><select id="klo-cconf">'
      + [1, 2, 3].map((v) => '<option value="' + v + '"' + (Number(c.conf) === v ? ' selected' : '') + '>' + v + ' 次</option>').join('')
      + '</select><label class="klo-lb"><input id="klo-coco" type="checkbox"' + (c.oco ? ' checked' : '') + '> OCO</label></div>'
      + '<div class="klo-info" id="klo-cinfo">' + condInfoHtml() + '</div>'
      + '<button class="klo-submit cond' + (guard.isArmed() ? ' armed' : '') + '" data-act="submit-cond">'
      + (guard.isArmed() ? '再点一次确认条件单' : '确认创建条件单') + '</button>'
      + msgHtml();
  }

  function condInfoHtml() {
    const d = data();
    const pos = d.position;
    const cost = d.cost && d.cost.paper != null ? Number(d.cost.paper) : null;
    const spec = condSpec();
    const needs = spec.needs;
    const trig = Number(F.cond.trig) || 0;
    const posTxt = pos && pos.qty != null ? ' · 持仓 ' + pos.qty : '';
    // 按类型给文案：只有「触发价型」才有触发价预览
    let line = '';
    if (needs.indexOf('trigger') >= 0) {
      const tip = (cost && trig) ? ((trig >= cost ? '触发价高于成本 ' : '触发价低于成本 ') + money(Math.abs(trig - cost))) : '成本不可用';
      line = '纸面成本 <b>' + (cost === null ? '不可用' : money(cost)) + '</b> · ' + esc(tip) + posTxt
        + '<br>触发价预览画在主图上（虚线），不写任何真实委托。';
    } else if (needs.indexOf('pct') >= 0) {
      line = '纸面成本 <b>' + (cost === null ? '不可用' : money(cost)) + '</b>' + posTxt
        + '<br>按幅度触发：自生效起跟踪' + (F.cond.type === 'trailing_sell' ? '最高' : '最低')
        + '价，反向走 ' + F.cond.pct + '% 即触发。没有单一触发价，所以主图不画预览线。';
    } else if (F.cond.type === 'signal_buy') {
      line = '分钟 K 闭合命中信号即市价买入（服务端信号库同源）；<b>只支持市价</b>，无触发价预览。';
    } else if (needs.indexOf('at') >= 0) {
      line = '到点按设定方向委托（时间 ' + esc(F.cond.at || '—') + '）；不挂在价格上，主图无预览线。';
    } else if (needs.indexOf('target') >= 0) {
      const opts = targetOpts(F.cond.type) || [];
      const hit = opts.find((o) => o.value === curTarget(F.cond));
      line = '目标位 <b>' + esc(hit ? hit.label : curTarget(F.cond)) + '</b> · 容差 <b>'
        + tolPctTxt(F.cond.tol) + '%</b>' + posTxt
        + '<br>现价触及目标线（含容差）即买入；线由 cond_watch 盘中计算，无固定触发价可预览。';
    } else {
      line = '保本止损/破五日线由成本与 MA5 决定，不填触发价；纸面成本 <b>'
        + (cost === null ? '不可用' : money(cost)) + '</b>' + posTxt + '。';
    }
    return line;
  }

  // ---------------------------------------------------------------- 记录

  function cancelId(o) {
    // paper 委托号是 paper_order_id（旧 order_id 有值时兼容）
    return String(o.paper_order_id || o.order_id || o.id || '');
  }

  function listHtml() {
    const d = data();
    const scopeAll = F.scope === 'all';
    const orders = M.liveOrders(d.orders || [], scopeAll ? null : code);
    const conds = M.liveConds(d.conditions || [], scopeAll ? null : code);
    const pos = d.position;
    const availQty = pos && pos.available_qty != null ? Number(pos.available_qty) : null;
    return ''
      + '<div class="klo-row"><span class="klo-lb">范围</span><span class="klo-seg" id="klo-scope">'
      + '<button data-act="scope" data-v="code" class="' + (scopeAll ? '' : 'on') + '">当前票</button>'
      + '<button data-act="scope" data-v="all" class="' + (scopeAll ? 'on' : '') + '">全部</button></span>'
      + '<span class="klo-lb" style="margin-left:auto">持仓</span><b>' + (pos && pos.qty != null ? pos.qty : '0')
      + '</b><span class="klo-lb">可卖</span><b>' + (availQty === null ? '—' : availQty) + '</b></div>'
      + '<div class="klo-list">'
      + (orders.length ? orders.map((o) => '<div class="klo-it"><span class="m">'
        + (scopeAll ? esc(String(o.ts_code || o.code || '')) + ' ' : '')
        + esc(o.side === 'SELL' ? '卖' : '买') + ' ' + esc(o.qty) + ' @ '
        + esc(o.limit_price == null ? '市价' : Number(o.limit_price).toFixed(2))
        + ' <span class="st">' + esc(o.status) + '</span></span>'
        + '<button data-act="cancel-order" data-id="' + esc(cancelId(o)) + '">撤单</button></div>').join('')
        : '<div class="klo-hint">无 REPORTED/PARTIAL 委托</div>')
      + (conds.length ? conds.map((c) => '<div class="klo-it"><span class="m">'
        + (scopeAll ? esc(String(c.code || '')) + ' ' : '')
        + esc(c.label || c.type) + ' ' + esc(c.qty) + ' 股 <span class="st">' + esc(c.status) + '</span></span>'
        + '<button data-act="cancel-cond" data-id="' + esc(String(c.cond_id || '')) + '">撤销</button></div>').join('')
        : '<div class="klo-hint">无 ACTIVE 条件单</div>')
      + '</div>' + msgHtml();
  }

  function msgHtml() { return msg.text ? '<div class="klo-msg ' + msg.kind + '">' + esc(msg.text) + '</div>' : ''; }

  // ---------------------------------------------------------------- 渲染 / 事件

  function render() {
    if (!root) return;
    initDefaults();
    const body = F.tab === 'buy' ? orderFormHtml('buy')
      : F.tab === 'sell' ? orderFormHtml('sell')
      : F.tab === 'cond' ? condFormHtml() : listHtml();
    root.innerHTML = ''
      + '<div class="klo-hd"><span class="klo-title">' + esc(name || code) + '</span>'
      + '<span class="klo-code">' + esc(code) + '</span><span class="klo-paper">纸面</span>'
      + '<button class="klo-x" data-act="close" title="关闭">✕</button></div>'
      + '<div class="klo-tabs">' + TABS.map((t) => '<button data-act="tab" data-t="' + t.key + '"'
        + (F.tab === t.key ? ' class="on"' : '') + '>' + t.label + '</button>').join('') + '</div>'
      + body;
    bind();
  }

  function bind() {
    const on = (sel, ev, fn) => { const el = root.querySelector(sel); if (el) el.addEventListener(ev, fn); };
    on('#klo-price', 'input', (e) => { F.price = Number(e.target.value) || 0; F.pricePick = ''; refreshInfo(); });
    on('#klo-qty', 'input', (e) => { F.qty = Number(e.target.value) || 0; refreshInfo(); });
    on('#klo-ctype', 'change', (e) => {
      F.cond.type = e.target.value;
      F.cond.trigEdited = false;
      F.cond.trig = M.defaultTrigger(F.cond.type, Number((data().quote || {}).last));
      // 目标位不同类各自一套（黄/白 ↔ 上/下沿），切类时把不合法值收回 auto
      F.cond.target = curTarget(F.cond);
      render(); previewCond();
    });
    on('#klo-ctarget', 'change', (e) => { F.cond.target = e.target.value; refreshCondInfo(); });
    on('#klo-ctol', 'input', (e) => {
      const v = Number(e.target.value) || 0;          // 空/非法 → 0，提交时由 model 拦（不静默改成 0.5%）
      F.cond.tol = v / 100;
      refreshCondInfo();
    });
    on('#klo-ctrig', 'input', (e) => { F.cond.trig = Number(e.target.value) || 0; F.cond.trigEdited = true; refreshCondInfo(); previewCond(); });
    on('#klo-cpct', 'input', (e) => { F.cond.pct = Number(e.target.value) || 0; });
    on('#klo-cat', 'input', (e) => { F.cond.at = e.target.value; });
    on('#klo-csig', 'change', (e) => { F.cond.signal = e.target.value; });
    on('#klo-cqty', 'input', (e) => { F.cond.qty = Number(e.target.value) || 0; });
    on('#klo-cprice', 'input', (e) => { F.cond.limit = Number(e.target.value) || 0; });
    on('#klo-cvalid', 'change', (e) => { F.cond.valid = Number(e.target.value); });
    on('#klo-cconf', 'change', (e) => { F.cond.conf = Number(e.target.value); });
    on('#klo-coco', 'change', (e) => { F.cond.oco = !!e.target.checked; });
  }

  function refreshInfo() { const i = root.querySelector('#klo-info'); if (i) i.innerHTML = infoHtml(F.tab === 'sell' ? 'sell' : 'buy'); }
  function refreshCondInfo() { const i = root.querySelector('#klo-cinfo'); if (i) i.innerHTML = condInfoHtml(); }

  function previewCond() {
    if (!opts.onCondPreview) return;
    const trigType = condSpec().needs.indexOf('trigger') >= 0;
    const v = Number(F.cond.trig);
    opts.onCondPreview(F.tab === 'cond' && trigType && v > 0 ? v : null);
  }

  function setMsg(text, kind) { msg = { text: text || '', kind: kind || '' }; }

  function onFill(kind) {
    const q = data().quote || {};
    const map = { last: q.last, bid1: q.bid1, ask1: q.ask1, limit_up: q.limit_up, limit_down: q.limit_down };
    const v = Number(map[kind]);
    if (!Number.isFinite(v) || v <= 0) { setMsg('该档位没有价格（行情不可用）', 'err'); render(); return; }
    F.pricePick = kind;
    F.price = v;
    render();
  }

  async function submitOrder(side) {
    if (!guard.isArmed()) { guard.arm(); setMsg('再点一次确认（3 秒内有效）', ''); render(); return; }
    if (!guard.begin()) return;
    busy = true;
    const d = data();
    const pos = d.position;
    const isBuy = side === 'buy';
    const useAmt = isBuy && F.amountMode === 'amt';
    const qty = useAmt ? M.amountToQty(F.qty, F.price) : Math.floor(Number(F.qty) || 0);
    const built = M.buildPlacePayload({
      code: code, side: side, qty: qty, price: F.price, orderType: F.otype, name: name,
      availCash: d.account && d.account.avail_cash != null ? Number(d.account.avail_cash) : null,
      availQty: pos && pos.available_qty != null ? Number(pos.available_qty) : null,
    });
    if (!built.ok) { guard.end(); busy = false; setMsg(built.errors.join('；'), 'err'); render(); return; }
    try {
      const r = await post(M.ENDPOINTS.place, built.payload);
      if (isBuy && useAmt) M.saveAmountLast(null, F.qty);
      if (isBuy && !F.locked) M.saveQtyLast(null, qty);
      const back = opts.onWritten ? await opts.onWritten('order', r) : { ok: true };
      const base = M.receiptLine(r) + ((r && r.warnings && r.warnings.length) ? ' · ' + r.warnings.join('；') : '');
      if (back && back.ok === false) setMsg(base + ' · 回读失败：' + (back.reason || '读取失败') + '（不要以列表为准）', 'err');
      else setMsg(base, 'ok');
    } catch (e) {
      setMsg('失败：' + ((e && (e.detail || e.message)) || '未知错误'), 'err');
    } finally {
      guard.end(); busy = false; render();
    }
  }

  async function submitCond() {
    if (!guard.isArmed()) { guard.arm(); setMsg('再点一次确认（3 秒内有效）', ''); render(); return; }
    if (!guard.begin()) return;
    busy = true;
    const c = F.cond;
    const built = M.buildConditionPayload({
      code: code, type: c.type, qty: c.qty, triggerPrice: c.trig, pct: c.pct,
      at: c.at, side: c.side, signal: c.signal, validDays: c.valid, orderType: c.otype,
      limitPrice: c.limit, confirmCount: c.conf, bufferAtr: c.atr, oco: c.oco, name: name,
      target: c.target, tol: c.tol,
    });
    if (!built.ok) { guard.end(); busy = false; setMsg(built.errors.join('；'), 'err'); render(); return; }
    try {
      const r = await post(M.ENDPOINTS.addCond, built.payload);
      const back = opts.onWritten ? await opts.onWritten('cond', r) : { ok: true };
      const head = '条件单已创建：' + ((r && (r.cond_id || (r.condition && r.condition.cond_id))) || '已提交');
      if (back && back.ok === false) setMsg(head + ' · 回读失败：' + (back.reason || '读取失败'), 'err');
      else setMsg(head, 'ok');
      if (opts.onCondPreview) opts.onCondPreview(null);
    } catch (e) {
      setMsg('失败：' + ((e && (e.detail || e.message)) || '未知错误'), 'err');
    } finally {
      guard.end(); busy = false; render();
    }
  }

  async function doCancel(kind, id, btn) {
    if (!id) { setMsg('该记录缺少委托号/条件单号，不能撤', 'err'); render(); return; }
    const g = cancelGuards[kind + id] || (cancelGuards[kind + id] = M.submitGuard());
    if (!g.isArmed()) { g.arm(); btn.classList.add('armed'); btn.textContent = '再点确认'; return; }
    if (!g.begin()) return;
    btn.disabled = true;
    const built = kind === 'order' ? M.buildCancelOrderPayload(id) : M.buildCancelCondPayload(id);
    if (!built.ok) { g.end(); setMsg(built.errors.join('；'), 'err'); render(); return; }
    try {
      const r = await post(kind === 'order' ? M.ENDPOINTS.cancelOrder : M.ENDPOINTS.cancelCond, built.payload);
      if (opts.onWritten) await opts.onWritten(kind === 'order' ? 'cancel-order' : 'cancel-cond', r);
      setMsg((kind === 'order' ? '已撤单 ' : '已撤销条件单 ') + id, 'ok');
    } catch (e) {
      setMsg('撤单失败：' + ((e && (e.detail || e.message)) || '未知错误'), 'err');   // 不自动重试
    } finally {
      g.end(); delete cancelGuards[kind + id]; render();
    }
  }

  function onClick(ev) {
    const btn = ev.target.closest('[data-act]');
    if (!btn) return;
    ev.stopPropagation();
    const act = btn.dataset.act;
    if (act === 'close') { api.close(); return; }
    if (act === 'tab') {
      F.tab = btn.dataset.t; guard.disarm(); setMsg(''); render();
      if (F.tab === 'cond') previewCond(); else if (opts.onCondPreview) opts.onCondPreview(null);
      return;
    }
    if (act === 'fill') { onFill(btn.dataset.f); return; }
    if (act === 'step') {
      F.price = Math.max(0, (Number(F.price) || 0) + Number(btn.dataset.d));
      F.pricePick = ''; render(); return;
    }
    if (act === 'qmode') { F.amountMode = btn.dataset.v === 'amt' ? 'amt' : 'qty'; M.saveQtyMode(null, F.amountMode); render(); return; }
    if (act === 'lock') {
      F.locked = !F.locked; M.saveLock(null, F.locked);
      if (F.locked) F.qty = F.amountMode ? (M.readAmountLast() || 10000) : (M.readQtyLast() || F.qty || M.LOT);
      render(); return;
    }
    if (act === 'otype') { F.otype = btn.dataset.v === 'market' ? 'market' : 'limit'; render(); return; }
    if (act === 'ratio') {
      const d = data();
      const isBuy = F.tab === 'buy';
      const base = isBuy
        ? (d.account && d.account.avail_cash != null && F.price > 0 ? M.clampLot(Number(d.account.avail_cash) / F.price) : 0)
        : (d.position && d.position.available_qty != null ? M.clampLot(Number(d.position.available_qty)) : 0);
      const q = M.ratioQty(base, Number(btn.dataset.r));
      F.qty = (isBuy && F.amountMode === 'amt') ? Math.round(q * (F.price || 0)) : q;
      render(); return;
    }
    if (act === 'cotype') { F.cond.otype = btn.dataset.v === 'limit' ? 'limit' : 'market'; render(); return; }
    if (act === 'catr') { F.cond.atr = Number(btn.dataset.v) || 0; render(); return; }
    if (act === 'cside') { F.cond.side = btn.dataset.v === 'sell' ? 'sell' : 'buy'; render(); return; }
    if (act === 'scope') { F.scope = btn.dataset.v === 'all' ? 'all' : 'code'; render(); return; }
    if (act === 'submit') { submitOrder(btn.dataset.side === 'sell' ? 'sell' : 'buy'); return; }
    if (act === 'submit-cond') { submitCond(); return; }
    if (act === 'cancel-order') { doCancel('order', btn.dataset.id, btn); return; }
    if (act === 'cancel-cond') { doCancel('cond', btn.dataset.id, btn); return; }
  }

  /** 标题栏拖拽（旧版 ensurePanel 同口径；不做位置持久化、不做动画）。 */
  function onDragStart(ev) {
    if (ev.button !== 0 || ev.target.closest('button')) return;
    const box = root.getBoundingClientRect();
    const dx = ev.clientX - box.left, dy = ev.clientY - box.top;
    drag = { dx: dx, dy: dy, moved: false };
    ev.preventDefault();
    const mv = (e2) => {
      if (!drag) return;
      drag.moved = true;
      const x = Math.min(Math.max(0, e2.clientX - drag.dx), Math.max(0, window.innerWidth - box.width));
      const y = Math.min(Math.max(0, e2.clientY - drag.dy), Math.max(0, window.innerHeight - 40));
      root.style.left = x + 'px';
      root.style.top = y + 'px';
    };
    const up = () => {
      window.removeEventListener('pointermove', mv);
      window.removeEventListener('pointerup', up);
      drag = null;
    };
    window.addEventListener('pointermove', mv);
    window.addEventListener('pointerup', up);
  }

  const api = {
    open(nextTab, price, ctype) {
      F.tab = TABS.some((t) => t.key === nextTab) ? nextTab : 'buy';
      if (Number(price) > 0) { F.price = Number(price); F.pricePick = ''; }
      if (ctype && M.COND_KEYS.indexOf(ctype) >= 0) { F.cond.type = ctype; F.cond.trigEdited = false; F.cond.trig = M.defaultTrigger(ctype, Number((data().quote || {}).last)); }
      guard.disarm(); setMsg('');
      if (!root) {
        root = document.createElement('div');
        root.id = PANEL_ID;
        document.body.append(root);
        root.addEventListener('click', onClick);
        root.addEventListener('contextmenu', (ev) => { ev.stopPropagation(); });
        root.__dragBound = false;
        onDocumentDown = (ev) => { if (root && !root.contains(ev.target)) api.close(); };
        document.addEventListener('mousedown', onDocumentDown, true);
      }
      root.style.display = '';
      root.style.left = Math.max(8, Math.min(window.innerWidth / 2 + 40, window.innerWidth - 330)) + 'px';
      root.style.top = Math.max(60, window.innerHeight / 2 - 170) + 'px';
      render();
      const hd = root.querySelector('.klo-hd');
      if (hd && !root.__dragBound) { hd.addEventListener('pointerdown', onDragStart); root.__dragBound = true; }
      if (F.tab === 'cond') previewCond();
      return api;
    },
    /** 写后刷新：只重画，不动表单状态（数量/触发价/确认提示都保留）。 */
    refresh() { if (root) render(); },
    close() {
      guard.disarm();
      if (opts.onCondPreview) opts.onCondPreview(null);
      if (root && root.parentNode) root.parentNode.removeChild(root);
      root = null;
      if (onDocumentDown) document.removeEventListener('mousedown', onDocumentDown, true);
      onDocumentDown = null;
      if (opts.onClose) opts.onClose();
    },
    destroy() { api.close(); },
    isOpen: () => !!root,
    activeTab: () => F.tab,
    el: () => root,
    state: () => F,
  };
  return api;
}

export default { createOrderPanel, PANEL_ID, TABS };
