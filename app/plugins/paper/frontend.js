// ============================================================================
// 纸面交易卡 · 前端批次 2b（docs/task_fe_batch2b.md）
// ----------------------------------------------------------------------------
// 行为基准 = 旧端 engine/web/kline_order.js（69KB 下单弹窗）+ engine/web/index.html 账本区：
//   · 550-603   orderBodyHtml      下单表单（代码/价格/数量/限价-市价/数量快捷档/数量锁）
//   · 655-722   condBodyHtml       条件单表单（COND_LABEL 九类 + 每类显隐与提示文案）
//   · 33-53     COND_LABEL / COND_HINT / STATUS_CN   逐字照搬
//   · 752-755   SIGNALS            信号买入五档
//   · 784-791   condCondCore       条件单触发参数文本
//   · 1121-1209 submit / submitCond / cancelOrder / cancelCond  写请求口径
//   · 3063-3158 index.html 账本区 KPI / 持仓表 / 成交（本卡取纸面口径）
//
// 契约（任务书 §3/§4 + docs/CONTRACT.md §5）：
//   1. 原生 ES module，零 import、零构建；只操作自己的 root。
//   2. 读走 ctx.api.get，写一律 ctx.api.post；paper 插件 mount=root，故路径全部是绝对 /api/…
//      （/api/p/paper/* 不存在，不能走插件相对路径）。
//   3. 颜色一律 themes.css 语义 token，本文件零裸 hex。
//   4. 写操作纪律（v2 壳第一张写卡）：
//      ① 所有写请求走 ctx.api.post；② 下单/加条件单必须过**模态确认弹层**（遮罩 + 只有一个确认按钮，
//         Enter 不触发，必须鼠标点击）；③ 提交中按钮 disabled + 「提交中…」，防双击重复下单；
//      ④ 失败回执**原样**渲染（不吞 4xx/5xx、不重试写请求）；⑤ 读刷新只走 GET，写失败不影响读。
//   5. 撤单 / 立即评估属「行内二次点击」：第一次点亮「确认…」，4 秒内第二次才真正 POST。
//   6. refreshEvery: 20000（账户 + 委托 20s）；写成功后立即手动重拉。
// ============================================================================

const STYLE_ID = 'paper-card-css';
const TOAST_MS = 3200;
const NOTE_TAG = 'panel';

// 条件单九类（旧 kline_order.js:33-37 逐字）
const COND_LABEL = {
  stop_loss: '止损卖出', take_profit: '止盈卖出', trailing_sell: '回落卖出',
  rebound_buy: '反弹买入', breakout_buy: '突破买入', time_order: '定时委托',
  breakeven_stop: '保本止损', ma5_break: '破五日线卖出', signal_buy: '信号买入',
};
// 每类提示文案（旧 38-48 逐字）
const COND_HINT = {
  stop_loss: '现价跌到触发价 → 卖出',
  take_profit: '现价涨到触发价 → 卖出',
  trailing_sell: '自激活后最高价回落到幅度 → 卖出',
  rebound_buy: '自激活后最低价反弹到幅度 → 买入',
  breakout_buy: '现价涨到触发价 → 买入',
  time_order: '到点按设定方向委托',
  breakeven_stop: '浮盈达 1.5×ATR 后跌回成本 → 全部卖出（QMT 止损四层第①层）',
  ma5_break: '盘中破 MA5−0.3ATR 或 14:57 后破 MA5 → 卖出',
  signal_buy: '分钟 K 闭合命中信号 → 市价买入（同票当日有手工单则自动引擎让开）',
};
// 信号买入五档（旧 752-755 逐字）
const SIGNALS = [
  ['BREAKOUT', '放量突破'], ['PULLBACK', '回踩不破'], ['VWAP_RECLAIM', '收复均价线'],
  ['HOOK_TURN', '钩头转向'], ['WEAK_TO_STRONG', '弱转强'],
];
const SIGNAL_CN = { BREAKOUT: '放量突破', PULLBACK: '回踩不破', VWAP_RECLAIM: '收复均价线',
  HOOK_TURN: '钩头转向', WEAK_TO_STRONG: '弱转强' };
// 状态中文化（旧 49-53 + core 的 COND_ACTIVE）
const STATUS_CN = { REPORTED: '挂单', PARTIAL: '部分成交', FILLED: '已成交', CANCELLED: '已撤',
  REJECTED: '已拒', ACTIVE: '监控中', TRIGGERED: '已触发', EXPIRED: '已过期', FAILED: '触发失败' };
// 数量快捷档（旧 593-594：1/4、1/3、1/2、全部）
const QTY_QUICK = [['0.25', '1/4'], ['0.3333', '1/3'], ['0.5', '1/2'], ['1', '全部']];
// 有效期下拉（旧 705-706 逐字）
const VALID_OPTS = [['0', '当日'], ['1', '1 天'], ['3', '3 天'], ['5', '5 天'],
  ['10', '10 天'], ['20', '20 天']];
const ORDER_ACTIVE = ['REPORTED', 'PARTIAL'];
const COND_TERMINAL = ['TRIGGERED', 'CANCELLED', 'EXPIRED', 'FAILED'];
// 本卡只跑纸面（任务书 §3 禁止项：ledger mode 固定 paper、不出现实盘下单字样）
const TRADE_MODE = 'paper';

const CSS = [
  '.pp-card{display:flex;flex-direction:column;gap:12px;color:var(--fg);min-width:0}',
  '.pp-top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}',
  '.pp-title{font-size:1rem;font-weight:600;color:var(--fg-strong)}',
  '.pp-badge{display:inline-block;padding:1px 9px;border-radius:var(--radius-pill);background:var(--card2);color:var(--dim);font-size:0.72rem;white-space:nowrap}',
  '.pp-badge.paper{background:var(--danger-solid);color:var(--danger-on);font-weight:600}',
  '.pp-badge.ok{background:var(--down-bg);color:var(--down-ink,var(--down))}',
  '.pp-badge.warn{background:var(--warn-bg);color:var(--warn-ink,var(--warn))}',
  '.pp-sub{color:var(--dim);font-size:0.72rem;margin-left:auto;font-variant-numeric:tabular-nums}',
  '.pp-err{padding:6px 9px;border-radius:var(--radius-sm);background:var(--danger-bg);color:var(--danger-ink,var(--danger));font-size:0.75rem;line-height:1.5;word-break:break-word}',
  '.pp-warn{padding:6px 9px;border-radius:var(--radius-sm);background:var(--warn-bg);color:var(--warn-ink,var(--warn));font-size:0.75rem;line-height:1.5}',
  '.pp-okbar{padding:6px 9px;border-radius:var(--radius-sm);background:var(--down-bg);color:var(--down-ink,var(--down));font-size:0.75rem;line-height:1.5;word-break:break-word}',
  '.pp-sec{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card);padding:8px 10px;min-width:0}',
  '.pp-sec-hd{display:flex;align-items:baseline;gap:8px;font-size:0.72rem;color:var(--dim);margin-bottom:6px;flex-wrap:wrap}',
  '.pp-sec-hd b{color:var(--fg-strong);font-size:0.78rem;font-weight:600}',
  '.pp-sec-n{margin-left:auto;font-size:0.66rem;color:var(--dim)}',
  '.pp-kv{display:flex;gap:10px;flex-wrap:wrap;font-size:0.75rem;line-height:1.7;color:var(--fg)}',
  '.pp-kpi{background:var(--card2);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:4px 10px;min-width:104px}',
  '.pp-kpi .k{color:var(--dim);font-size:0.68rem}',
  '.pp-kpi .v{font-size:0.92rem;font-weight:600;color:var(--fg-strong);font-variant-numeric:tabular-nums}',
  '.pp-tbl{width:100%;border-collapse:collapse;font-size:0.74rem;font-variant-numeric:tabular-nums}',
  '.pp-tbl th{text-align:left;font-weight:400;color:var(--dim);font-size:0.68rem;padding:3px 6px;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.pp-tbl td{padding:4px 6px;border-bottom:1px solid var(--line-soft);vertical-align:top}',
  '.pp-tbl th.n,.pp-tbl td.n{text-align:right;white-space:nowrap}',
  '.pp-card .up{color:var(--up)}',
  '.pp-card .down{color:var(--down)}',
  '.pp-code{color:var(--dim);font-size:0.66rem;margin-left:4px}',
  '.pp-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:5px 0}',
  '.pp-lab{font-size:0.72rem;color:var(--dim);flex:0 0 56px}',
  '.pp-row input[type=text],.pp-row input[type=number],.pp-row input[type=time]{background:var(--card2);color:var(--fg);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:3px 8px;font:inherit;font-size:0.8rem;outline:none;font-variant-numeric:tabular-nums}',
  '.pp-row input:focus{border-color:var(--accent)}',
  '.pp-row input.is-locked{opacity:.6}',
  '.pp-row select{width:auto;min-width:110px;padding:3px 8px;font-size:0.8rem}',
  '.pp-w{width:104px}',
  '.pp-seg{display:inline-flex;gap:4px}',
  '.pp-seg button{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--dim);border-radius:var(--radius-sm);padding:2px 10px;font-size:0.74rem;font-family:inherit;cursor:pointer}',
  '.pp-seg button.on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);font-weight:600}',
  '.pp-seg.is-dis{opacity:.45;pointer-events:none}',
  '.pp-chips{display:inline-flex;gap:4px}',
  '.pp-chips button{border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);color:var(--dim);border-radius:var(--radius-pill);padding:1px 9px;font-size:0.7rem;font-family:inherit;cursor:pointer}',
  '.pp-chips button:hover{border-color:var(--accent);color:var(--accent)}',
  '.pp-chips.is-dis{opacity:.45;pointer-events:none}',
  '.pp-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer;white-space:nowrap}',
  '.pp-btn:hover{background:var(--card2)}',
  '.pp-btn.primary{border-color:var(--accent);color:var(--accent)}',
  '.pp-btn.primary:hover{background:var(--accent-bg)}',
  '.pp-btn.danger{border-color:var(--danger);color:var(--danger-ink,var(--danger))}',
  '.pp-btn.danger:hover{background:var(--danger-bg)}',
  '.pp-btn.submit{border-color:var(--up);color:var(--up-ink,var(--up));padding:5px 16px;font-size:0.8rem;font-weight:600}',
  '.pp-btn.submit:hover{background:var(--up-bg)}',
  '.pp-btn.submit.sell{border-color:var(--down);color:var(--down-ink,var(--down))}',
  '.pp-btn.submit.sell:hover{background:var(--down-bg)}',
  '.pp-btn:disabled{opacity:.45;cursor:not-allowed}',
  '.pp-hint{font-size:0.7rem;color:var(--dim);line-height:1.55;margin:3px 0}',
  '.pp-muted{color:var(--dim);font-size:0.74rem}',
  '.pp-feed{display:flex;gap:8px;flex-wrap:wrap;align-items:baseline;font-size:0.7rem;color:var(--dim);border-top:1px solid var(--line-soft);padding-top:6px}',
  '.pp-feed-t{font-variant-numeric:tabular-nums}',
  '.pp-feed-note{color:var(--warn-ink,var(--warn))}',
  '.pp-feed-err{color:var(--danger-ink,var(--danger));word-break:break-word;flex:1 1 100%}',
  '.pp-toast{align-self:flex-start;font-size:0.72rem;padding:4px 10px;border-radius:var(--radius-sm);background:var(--accent-bg);color:var(--accent-ink,var(--accent));border:1px solid var(--accent)}',
  '.pp-src{font-size:0.66rem;color:var(--dim);line-height:1.6}',
  '.pp-mask{position:fixed;inset:0;z-index:var(--z-search,70);display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, var(--bg) 76%, transparent);backdrop-filter:blur(2px);-webkit-backdrop-filter:blur(2px)}',
  '.pp-modal{width:min(460px,92vw);max-height:86vh;overflow:auto;background:var(--card);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-lg);box-shadow:var(--shadow-pop);padding:14px 16px}',
  '.pp-modal-hd{display:flex;align-items:baseline;gap:8px;margin-bottom:10px}',
  '.pp-modal-hd b{font-size:0.92rem;color:var(--fg-strong)}',
  '.pp-modal-body .pp-tbl th{width:84px;color:var(--dim)}',
  '.pp-modal-body .pp-tbl td{color:var(--fg-strong);font-weight:600}',
  '.pp-modal-note{margin-top:8px;font-size:0.7rem;color:var(--warn-ink,var(--warn));line-height:1.55}',
  '.pp-modal-ft{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}',
].join('\n');

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------- 工具
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const isNum = (v) => v != null && v !== '' && isFinite(Number(v));
const num = (v, d) => (isNum(v) ? Number(v).toFixed(d == null ? 2 : d) : '—');
const money = (v) => (isNum(v)
  ? '¥' + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  : '—');
const toNum = (v) => (isNum(v) ? Number(v) : null);
const code6 = (v) => String(v == null ? '' : v).split('.')[0];
const cn = (map, v) => (v == null || v === '' ? '—' : (map && map[v] !== undefined ? map[v] : String(v)));
const errText = (e) => (e && e.message ? e.message : String(e));
const pad2 = (n) => (n < 10 ? '0' + n : String(n));
const hhmm = (ts) => {
  const s = String(ts == null ? '' : ts);
  return s.length >= 19 ? s.slice(11, 19) : (s || '—');
};
const day8 = (v) => {
  const s = String(v == null ? '' : v).replace(/\D/g, '');
  return s.length === 8 ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) : (s || '—');
};
const clampLot = (n) => {
  const i = Math.floor(Number(n) || 0);
  return i >= 100 ? Math.floor(i / 100) * 100 : 0;
};
// 条件单触发参数文本（旧 kline_order.js:768-791 condCondText / condCondCore）
function condTriggerText(c) {
  const t = (c && c.trigger) || {};
  const side = String((c && c.side) || '').toUpperCase();
  let s;
  if (c.type === 'time_order') s = (t.at || '—') + ' ' + (side === 'BUY' ? '买' : '卖');
  else if (c.type === 'trailing_sell' || c.type === 'rebound_buy') {
    s = (c.type === 'trailing_sell' ? '回落 ' : '反弹 ') + num(t.pct, 1) + '%';
  } else if (c.type === 'breakeven_stop') {
    s = '成本 ' + num(c.entry, 3) + ' · 缓冲 ' + num(c.buffer_atr, 2) + '×ATR';
  } else if (c.type === 'ma5_break') {
    s = '破 MA5−' + num(c.buffer_atr, 2) + '×ATR';
  } else if (c.type === 'signal_buy') {
    s = cn(SIGNAL_CN, c.signal);
  } else {
    s = (side === 'BUY' ? '≥ ' : (c.type === 'stop_loss' ? '≤ ' : '≥ ')) + num(t.price, 3);
  }
  if (c.t1_locked) s += ' · T+1待解锁';
  if (c.valid_until && c.long_term && c.days_left != null) s += ' · 剩' + c.days_left + '天';
  if (c.days_left != null && c.days_left <= 3) s += ' ⚠即将到期';
  return s;
}

// ---------------------------------------------------------------- 段落渲染（纯字符串，全部 esc 过）
const secHtml = (title, count, body) =>
  '<div class="pp-sec-hd"><b>' + esc(title) + '</b>'
  + (count == null ? '' : '<span class="pp-sec-n">' + esc(count) + '</span>')
  + '</div>' + body;

function accountHtml(S) {
  const A = S.account;
  if (!A) return '<p class="pp-muted">账户读取中…（/api/paper_account）</p>';
  if (A.__error) {
    if (A.__status === 404) {
      // 旧口径（index.html:3334）：账本文件未生成 = 尚无纸面成交，属正常空态
      return '<p class="pp-muted">纸面账本未生成（尚无纸面成交）——首次纸面成交后自动出现。</p>';
    }
    return '<p class="pp-err">账户读取失败：' + esc(A.__error) + '</p>';
  }
  const a = A.account || {};
  const rows = A.positions_list || [];
  const frozen = S.frozen || {};
  const kpis = [
    ['总资产', money(a.total_asset)],
    ['可用', money(a.available_cash)],
    ['持仓市值', money(a.market_value)],
    ['浮动盈亏', money(a.unrealized_pnl)],
    ['挂单冻结', money(a.frozen_cash)],
    ['单笔上限(10%)', money(a.single_order_cap)],
  ].map((kv) => '<div class="pp-kpi"><div class="k">' + esc(kv[0]) + '</div><div class="v">'
    + esc(kv[1]) + '</div></div>').join('');
  let html = '<div class="pp-kv">' + kpis + '</div>';
  if (!rows.length) {
    return html + '<p class="pp-muted" style="margin-top:6px">无纸面持仓（账本已生成，当前空仓）</p>';
  }
  const heads = ['代码 / 名称', '数量', '可卖', '成本', '现价', '市值', '盈亏', '冻结'];
  const body = rows.map((p) => {
    const stale = p.price_source === 'cost_fallback';
    const fz = frozen[p.ts_code];
    return '<tr>'
      + '<td data-open-kline="' + esc(p.ts_code || '') + '" data-open-name="' + esc(p.name || '') + '">'
      + esc(p.name || code6(p.ts_code)) + '<span class="pp-code">'
      + esc(code6(p.ts_code)) + '</span></td>'
      + '<td class="n">' + num(p.qty, 0) + '</td>'
      + '<td class="n">' + num(p.available_qty, 0) + '</td>'
      + '<td class="n">' + num(p.cost_price, 3) + '</td>'
      + '<td class="n"' + (stale ? ' title="无实时行情，回退成本价（不编造）"' : '') + '>'
      + num(p.last_price, 3) + (stale ? '<span class="pp-code">成本</span>' : '') + '</td>'
      + '<td class="n">' + money(p.market_value) + '</td>'
      + '<td class="n ' + (Number(p.pnl_amount) > 0 ? 'up' : (Number(p.pnl_amount) < 0 ? 'down' : ''))
      + '">' + money(p.pnl_amount) + '</td>'
      + '<td class="n">' + (fz == null ? '—' : num(fz, 0)) + '</td>'
      + '</tr>';
  }).join('');
  html += '<table class="pp-tbl" style="margin-top:6px"><thead><tr>'
    + heads.map((h, i) => '<th' + (i ? ' class="n"' : '') + '>' + esc(h) + '</th>').join('')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
  html += '<p class="pp-src" style="margin-top:4px">纸面口径：'
    + esc((a.updated_at || '—')) + ' ｜ 现价缺失项回退成本价并标「成本」（不编造）'
    + ' ｜ 冻结股数取自 /api/ledger 的 frozen_by_code</p>';
  return html;
}

function ordersHtml(S) {
  const L = S.list || {};
  if (L.__error) return '<p class="pp-err">委托读取失败：' + esc(L.__error) + '</p>';
  const orders = L.orders || [];
  if (!orders.length) return '<p class="pp-muted">当日暂无委托</p>';
  const heads = ['时间', '代码 / 名称', '方向', '数量', '价格', '状态', '来源', '操作'];
  const body = orders.slice(0, 50).map((o) => {
    const st = String(o.status || '');
    const side = String(o.side || '').toUpperCase();
    const active = ORDER_ACTIVE.indexOf(st) >= 0;
    const oid = o.paper_order_id || o.order_id || '';
    const px = o.limit_price != null ? num(o.limit_price, 3)
      : (o.filled_price != null ? num(o.filled_price, 3) : '—');
    return '<tr>'
      + '<td>' + esc(hhmm(o.created_at)) + '</td>'
      + '<td data-open-kline="' + esc(o.ts_code || '') + '" data-open-name="' + esc(o.name || '') + '">'
      + esc(o.name || code6(o.ts_code)) + '<span class="pp-code">'
      + esc(code6(o.ts_code)) + '</span></td>'
      + '<td class="' + (side === 'BUY' ? 'up' : 'down') + '">'
      + (side === 'BUY' ? '买' : '卖') + '</td>'
      + '<td class="n">' + num(o.qty, 0)
      + (o.filled_qty ? '<span class="pp-code">成' + num(o.filled_qty, 0) + '</span>' : '') + '</td>'
      + '<td class="n">' + px + '</td>'
      + '<td>' + esc(cn(STATUS_CN, st)) + '</td>'
      + '<td>' + esc(o.source || '—') + '</td>'
      + '<td>' + (active && oid
        ? '<button type="button" class="pp-btn" data-cancel-order="' + esc(oid) + '">撤单</button>'
        : '<span class="pp-muted">—</span>') + '</td>'
      + '</tr>';
  }).join('');
  return '<table class="pp-tbl"><thead><tr>'
    + heads.map((h, i) => '<th' + (i === 3 || i === 4 ? ' class="n"' : '') + '>' + esc(h) + '</th>').join('')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

function condsHtml(S) {
  const L = S.list || {};
  if (L.__error) return '<p class="pp-err">条件单读取失败：' + esc(L.__error) + '</p>';
  const all = L.conditions || [];
  if (!all.length) return '<p class="pp-muted">暂无条件单</p>';
  // 监控中（ACTIVE）排前面：旧版条件单页只列 ACTIVE，本卡把终态也留档但要让人先看到在场单
  const conds = all.slice().sort((a, b) => {
    const ra = String(a.status) === 'ACTIVE' ? 0 : 1;
    const rb = String(b.status) === 'ACTIVE' ? 0 : 1;
    if (ra !== rb) return ra - rb;
    return String(b.created_at || '').localeCompare(String(a.created_at || ''));
  });
  const heads = ['类型', '代码 / 名称', '触发参数', '数量', '状态', '有效期', '操作'];
  const body = conds.slice(0, 50).map((c) => {
    const st = String(c.status || '');
    const active = st === 'ACTIVE';
    return '<tr>'
      + '<td>' + esc(c.label || cn(COND_LABEL, c.type)) + '</td>'
      + '<td data-open-kline="' + esc(c.code || '') + '" data-open-name="' + esc(c.name || '') + '">'
      + esc(c.name || code6(c.code)) + '<span class="pp-code">'
      + esc(code6(c.code)) + '</span></td>'
      + '<td>' + esc(condTriggerText(c)) + '</td>'
      + '<td class="n">' + num(c.qty, 0) + '</td>'
      + '<td>' + esc(cn(STATUS_CN, st)) + '</td>'
      + '<td>' + esc(day8(c.valid_until)) + '</td>'
      + '<td>' + (active && c.cond_id
        ? '<button type="button" class="pp-btn" data-cancel-cond="' + esc(c.cond_id) + '">撤单</button>'
        : '<span class="pp-muted">—</span>') + '</td>'
      + '</tr>';
  }).join('');
  return '<table class="pp-tbl"><thead><tr>'
    + heads.map((h, i) => '<th' + (i === 3 ? ' class="n"' : '') + '>' + esc(h) + '</th>').join('')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

function feedHtml(F) {
  if (!F) return '<span class="pp-badge">行情诊断读取中…</span>';
  if (F.__error) return '<span class="pp-badge warn">行情诊断不可用：' + esc(F.__error) + '</span>';
  const connected = F.connected === true;
  return '<span class="pp-badge ' + (connected ? 'ok' : 'warn') + '">'
    + (connected ? '行情已连' : '行情未连') + '</span>'
    + '<span class="pp-feed-t">state=' + esc(F.state || '—')
    + ' · 订阅 ' + num(F.subscribed_count, 0) + ' 只 · tick ' + num(F.tick_count, 0)
    + (F.last_tick_at ? ' · 最后 tick ' + esc(F.last_tick_at) : '') + '</span>'
    + (connected ? ''
      : '<span class="pp-feed-note">行情源未启动属预期状态（不是故障）；无 tick 时条件单不会自动触发，'
        + '可点「立即评估」手动跑一轮。</span>')
    + (F.error ? '<span class="pp-feed-err">' + esc(F.error) + '</span>' : '');
}

// ---------------------------------------------------------------- 卡片
export default {
  cards: [{
    id: 'paper.main',
    title: '纸面交易',
    slot: 'main',
    order: 45,
    refreshEvery: 20000,

    async mount(root, ctx) {
      root.textContent = '';
      const api = ctx.api;
      const S = {
        ctx: ctx,
        root: root,
        api: api,
        alive: true,
        loading: false,
        submitting: false,
        account: null,
        list: null,
        ledger: null,
        feed: null,
        frozen: {},
        html: Object.create(null),
        toastTimer: null,
        armTimer: null,
        modal: null,
        condType: 'stop_loss',
        el: {},
      };
      this._pp = S;

      const div = (cls, text) => {
        const d = document.createElement('div');
        if (cls) d.className = cls;
        if (text != null) d.textContent = text;
        return d;
      };
      const btn = (cls, text, attrs) => {
        const b = document.createElement('button');
        b.type = 'button';
        if (cls) b.className = cls;
        b.textContent = text;
        if (attrs) Object.keys(attrs).forEach((k) => b.setAttribute(k, attrs[k]));
        return b;
      };
      const input = (type, value, cls) => {
        const i = document.createElement('input');
        i.type = type;
        if (cls) i.className = cls;
        i.value = value == null ? '' : String(value);
        return i;
      };
      const seg = (name, opts, current) => {
        const box = div('pp-seg');
        box.setAttribute('data-segbox', name);
        opts.forEach((o) => {
          // 只给容器打 data-segbox：段按钮若也带同名属性，closest('[data-segbox]') 会命中按钮自身，
          // 导致同组其它按钮不会熄灭（实测：买/卖同时高亮、市价切不掉限价）。
          box.append(btn(o[2] ? 'on' : '', o[1], { 'data-seg': o[0] }));
        });
        return box;
      };
      const segVal = (name) => {
        const box = wrap.querySelector('[data-segbox="' + name + '"]');
        if (!box) return null;
        const on = box.querySelector('button.on');
        return on ? on.getAttribute('data-seg') : null;
      };

      const wrap = div('pp-card');

      // ---- 卡头 ----
      const top = div('pp-top');
      const title = div('pp-title', '纸面交易');
      const badgePaper = div('pp-badge paper', '纸面');
      badgePaper.title = '本卡只做纸面交易：账本 mode=paper，无实盘下单通道';
      const badgeMode = div('pp-badge', 'mode ' + TRADE_MODE);
      const sub = div('pp-sub', '');
      const btnReload = btn('pp-btn', '刷新', { 'data-refresh': '1' });
      top.append(title, badgePaper, badgeMode, sub, btnReload);

      const status = div('pp-err');
      status.hidden = true;
      const toast = div('pp-toast', '');
      toast.hidden = true;

      const secAcc = div('pp-sec');
      const secForm = div('pp-sec');
      const secOrders = div('pp-sec');
      const secConds = div('pp-sec');
      const secCondForm = div('pp-sec');
      const feedBar = div('pp-feed');
      const src = div('pp-src', '数据源：/api/paper_account（账户）· /api/order/list（当日委托 + 条件单 + 指标）'
        + '· /api/ledger?mode=paper（冻结股数）· /api/paper/feed/status（行情诊断）。'
        + '写端点一律 POST：/api/order/place · /api/order/cancel · /api/condition/add'
        + ' · /api/condition/cancel · /api/condition/evaluate（force=1）。'
        + '本卡不写任何文件，账本写入全在 core 的纸面服务；实盘通道不存在。');

      wrap.append(top, status, secAcc, secForm, secOrders, secConds, secCondForm, feedBar, toast, src);
      root.append(wrap);

      S.el = { top, badgeMode, sub, status, toast, secAcc, secForm, secOrders, secConds, secCondForm,
        feedBar };

      // 防闪写入：内容没变就不碰 DOM（与 dashboard / battle_plan 卡同一策略）
      const setHtml = (el, html, key) => {
        if (!el) return;
        if (S.html[key] === html) return;
        S.html[key] = html;
        el.innerHTML = html;
      };
      const showToast = (text) => {
        if (!S.el.toast) return;
        S.el.toast.textContent = text;
        S.el.toast.hidden = false;
        if (S.toastTimer) clearTimeout(S.toastTimer);
        S.toastTimer = setTimeout(() => { if (S.el.toast) S.el.toast.hidden = true; }, TOAST_MS);
      };
      const setStatus = (text, isErr) => {
        const el = S.el.status;
        if (!el) return;
        if (!text) { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        el.className = isErr ? 'pp-err' : 'pp-okbar';
        el.textContent = text;   // 回执原样：不截断、不改写、不重试
      };

      // ------------------------------------------------------------ 下单表单（骨架只建一次）
      const fHd = div('pp-sec-hd');
      fHd.innerHTML = '<b>下单（受控确认）</b><span class="pp-sec-n">提交前必须过确认弹层 · Enter 不触发</span>';

      const rowCode = div('pp-row');
      const labCode = div('pp-lab', '代码');
      const inpCode = input('text', '', 'pp-w');
      inpCode.placeholder = '如 600519';
      const nameEcho = div('pp-muted', '');
      rowCode.append(labCode, inpCode, nameEcho);

      const rowSide = div('pp-row');
      const boxSide = seg('side', [['buy', '买入', true], ['sell', '卖出', false]], 'buy');
      rowSide.append(div('pp-lab', '方向'), boxSide);

      const rowType = div('pp-row');
      const boxOType = seg('otype', [['limit', '限价', true], ['market', '市价', false]], 'limit');
      rowType.append(div('pp-lab', '委托'), boxOType);

      const rowPrice = div('pp-row');
      const labPrice = div('pp-lab', '价格');
      const inpPrice = input('number', '', 'pp-w');
      inpPrice.step = '0.01';
      const priceUnit = div('pp-muted', '元（限价单必填）');
      rowPrice.append(labPrice, inpPrice, priceUnit);

      const rowQty = div('pp-row');
      const inpQty = input('number', '100', 'pp-w');
      inpQty.step = '100';
      const btnLock = btn('pp-btn', '数量锁：关', { 'data-lock': '1' });
      btnLock.title = '锁住数量，解锁才能改（旧版 ko_lock 同义）';
      const chips = div('pp-chips');
      chips.setAttribute('data-chips', '1');
      QTY_QUICK.forEach((q) => chips.append(btn('', q[1], { 'data-qquick': q[0] })));
      rowQty.append(div('pp-lab', '数量'), inpQty, div('pp-muted', '股'), btnLock, chips);

      const rowNote = div('pp-row');
      const inpName = input('text', '');
      inpName.placeholder = '名称备注（可选，默认自动回显）';
      rowNote.append(div('pp-lab', '名称'), inpName);

      const rowInfo = div('pp-row');
      const infoLine = div('pp-muted', '');
      rowInfo.append(infoLine);

      const rowSubmit = div('pp-row');
      const btnSubmit = btn('pp-btn submit', '确认下单', { 'data-submit-order': '1' });
      rowSubmit.append(btnSubmit);

      secForm.append(fHd, rowCode, rowSide, rowType, rowPrice, rowQty, rowNote, rowInfo, rowSubmit);

      // ------------------------------------------------------------ 条件单表单（可折叠）
      const cHd = div('pp-sec-hd');
      const cTitle = div('', '');
      cTitle.innerHTML = '<b>条件单（九类）</b>';
      const cCount = div('pp-sec-n', '');
      const btnEval = btn('pp-btn', '立即评估', { 'data-eval': '1' });
      const btnCToggle = btn('pp-btn', '展开', { 'data-cond-toggle': '1' });
      cHd.append(cTitle, cCount, btnEval, btnCToggle);
      const cBody = div('');
      cBody.hidden = true;

      const rowCType = div('pp-row');
      const selCType = document.createElement('select');
      Object.keys(COND_LABEL).forEach((k) => {
        const o = document.createElement('option');
        o.value = k;
        o.textContent = COND_LABEL[k];
        selCType.append(o);
      });
      selCType.value = 'stop_loss';
      rowCType.append(div('pp-lab', '类型'), selCType);

      const cHint = div('pp-hint', COND_HINT.stop_loss);
      const cFields = div('');          // 每类专属字段（随类型重建，输入只在类型切换时丢）
      const cQtyHint = div('pp-hint', '');

      const rowCQty = div('pp-row');
      const inpCQty = input('number', '100', 'pp-w');
      inpCQty.step = '100';
      rowCQty.append(div('pp-lab', '数量'), inpCQty, div('pp-muted', '股'));

      const rowCOType = div('pp-row');
      const boxCOType = seg('cotype', [['market', '市价', true], ['limit', '限价', false]], 'market');
      rowCOType.append(div('pp-lab', '委托'), boxCOType);

      const rowCValid = div('pp-row');
      const selCValid = document.createElement('select');
      VALID_OPTS.forEach((v) => {
        const o = document.createElement('option');
        o.value = v[0];
        o.textContent = v[1];
        selCValid.append(o);
      });
      selCValid.value = '3';
      rowCValid.append(div('pp-lab', '有效期'), selCValid);

      const rowCConf = div('pp-row');
      const boxCConf = seg('cconf', [['1', '1 次', true], ['2', '2 次', false], ['3', '3 次', false]], '1');
      const labOco = document.createElement('label');
      labOco.className = 'pp-muted';
      const inpOco = document.createElement('input');
      inpOco.type = 'checkbox';
      labOco.append(inpOco, document.createTextNode(' OCO 二选一'));
      rowCConf.append(div('pp-lab', '确认'), boxCConf, labOco);

      const rowCSubmit = div('pp-row');
      const btnCSubmit = btn('pp-btn submit', '提交条件单', { 'data-submit-cond': '1' });
      rowCSubmit.append(btnCSubmit);

      cBody.append(rowCType, cHint, cFields, rowCQty, cQtyHint, rowCOType, rowCValid, rowCConf, rowCSubmit);
      secCondForm.append(cHd, cBody);

      // 每类专属字段（旧 670-698：定时/回落反弹/信号/线类/普通）
      const buildCondFields = () => {
        const t = S.condType;
        const isTime = t === 'time_order';
        const isTrail = t === 'trailing_sell' || t === 'rebound_buy';
        const isLine = t === 'breakeven_stop' || t === 'ma5_break';
        const isSignal = t === 'signal_buy';
        cFields.innerHTML = '';
        if (isTime) {
          const r = div('pp-row');
          const inp = input('time', '14:55');
          inp.setAttribute('data-cond', 'at');
          r.append(div('pp-lab', '时间'), inp);
          const r2 = div('pp-row');
          r2.append(div('pp-lab', '方向'), seg('cside', [['buy', '买入', true], ['sell', '卖出', false]], 'buy'));
          cFields.append(r, r2);
        } else if (isTrail) {
          const r = div('pp-row');
          const inp = input('number', '1.5');
          inp.step = '0.1';
          inp.setAttribute('data-cond', 'pct');
          r.append(div('pp-lab', '幅度'), inp, div('pp-muted', '%'));
          const hint = div('pp-hint', '自条件单生效起的' + (t === 'trailing_sell' ? '最高' : '最低')
            + '价反向走这么多即触发（5s 一轮采样）');
          cFields.append(r, hint);
        } else if (isSignal) {
          const r = div('pp-row');
          const sel = document.createElement('select');
          SIGNALS.forEach((p) => {
            const o = document.createElement('option');
            o.value = p[0];
            o.textContent = p[1];
            sel.append(o);
          });
          sel.setAttribute('data-cond', 'sig');
          r.append(div('pp-lab', '信号'), sel);
          cFields.append(r, div('pp-hint',
            '分钟 K 闭合判定（服务端信号库同源），命中即按市价买入；手工单当日已有成交则自动引擎让开。'
            + '本卡不承诺必触发（触发依赖信号库可用）。'));
        } else if (isLine) {
          const hint = div('pp-hint', '');
          hint.setAttribute('data-cond', 'linehint');
          cFields.append(hint);
        } else {
          const r = div('pp-row');
          const inp = input('number', '');
          inp.step = '0.01';
          inp.setAttribute('data-cond', 'trig');
          r.append(div('pp-lab', '触发价'), inp, div('pp-muted', '元'));
          const hint = div('pp-hint', (t === 'stop_loss' ? '必须低于现价' : '必须高于现价'));
          const r2 = div('pp-row');
          r2.append(div('pp-lab', 'ATR 缓冲'),
            seg('cbuf', [['0', '无', true], ['0.3', '0.3×ATR', false]], '0'));
          const hint2 = div('pp-hint', (t === 'stop_loss'
            ? '不勾：到价就卖。勾 0.3×ATR：盘中需跌破止损线 0.3×ATR 才卖（防毛刺），14:55 后破线即卖'
            : '不勾：到价就卖。勾 0.3×ATR：需超过触发价 0.3×ATR 才卖'));
          cFields.append(r, hint, r2, hint2);
        }
      };
      buildCondFields();

      // 线类条件单提示（旧 737-750 lineCondText，指标来自 /api/order/list 的 metrics）
      const lineCondText = () => {
        const t = S.condType;
        const m = (S.list && S.list.metrics) || {};
        const code = String(inpCode.value || '').trim();
        if (!code) return '先填代码（指标 ATR14 / MA5 与持仓成本按代码取）';
        const key = Object.keys(m).find((k) => code6(k) === code6(code));
        const mm = (key ? m[key] : null) || {};
        const atr = toNum(mm.atr14);
        const ma5 = toNum(mm.ma5);
        const pos = positionsOf(code);
        const cost = pos ? toNum(pos.cost_price) : null;
        if (t === 'breakeven_stop') {
          if (!atr) return 'ATR14 取不到，暂不能设（需 14 根日线）';
          return '成本 ' + num(cost, 3) + ' · ATR14 ' + num(atr, 3)
            + ' → 浮盈达 ' + num(atr * 1.5, 3) + ' 后跌回 ' + num(cost, 3) + ' 卖出';
        }
        if (ma5 == null || !atr) return 'MA5 / ATR 取不到（需 5~14 根日线），暂不能设';
        return 'MA5 ' + num(ma5, 3) + ' · ATR14 ' + num(atr, 3)
          + ' → 盘中跌破 ' + num(ma5 - 0.3 * atr, 3) + '｜14:57 后跌破 ' + num(ma5, 3) + ' 卖出';
      };

      // 持仓查表（代码可能是 6 位或带后缀）
      const positionsOf = (raw) => {
        const pos = (S.list && S.list.account && S.list.account.positions) || {};
        const key = Object.keys(pos).find((k) => code6(k) === code6(raw));
        return key ? pos[key] : null;
      };
      const resolveName = (raw) => {
        const c = String(raw || '').trim();
        if (!c) return '';
        const p = positionsOf(c);
        if (p && p.name) return String(p.name);
        const cs = ((S.list && S.list.conditions) || []).find((x) => code6(x.code) === code6(c) && x.name);
        return cs ? String(cs.name) : '';
      };

      const syncNameEcho = () => {
        const nm = resolveName(inpCode.value);
        const c = String(inpCode.value || '').trim();
        nameEcho.textContent = !c ? '' : (nm ? nm : '（无名称记录，按代码提交）');
      };
      inpCode.addEventListener('input', syncNameEcho);

      // 价格框随委托方式显隐（旧 597-599：市价单不填价）
      const syncPriceRow = () => {
        const market = segVal('otype') === 'market';
        rowPrice.hidden = market;
        labPrice.hidden = market;
        inpPrice.hidden = market;
        priceUnit.hidden = market;
        if (market) inpPrice.value = '';
      };

      // 数量锁（旧 501-513 / 561-567：锁只对买入生效，卖出永远可改）
      let qtyLocked = false;
      const syncLock = () => {
        const buy = segVal('side') === 'buy';
        const locked = buy && qtyLocked;
        inpQty.readOnly = locked;
        if (locked) inpQty.className = 'pp-w is-locked';
        else inpQty.className = 'pp-w';
        if (btnSubmit) btnSubmit.className = 'pp-btn submit' + (buy ? '' : ' sell');
        btnLock.textContent = '数量锁：' + (locked ? '开' : '关');
        btnLock.className = 'pp-btn' + (locked ? ' danger' : '');
        chips.className = 'pp-chips' + (locked ? ' is-dis' : '');
      };

      // 快捷档（旧 1074-1096：买入按可用资金×比例÷价，卖出按可卖×比例）
      const applyQuick = (r) => {
        const buy = segVal('side') === 'buy';
        const ratio = Number(r) || 0;
        if (buy) {
          const cash = toNum(((S.list || {}).account || {}).available_cash);
          const px = toNum(inpPrice.value);
          if (cash == null || !px || px <= 0) {
            showToast('先填价格（买入快捷档按可用资金 ÷ 价格算）');
            return;
          }
          const n = clampLot(cash / px * ratio);
          inpQty.value = String(n > 0 ? n : 100);
        } else {
          const p = positionsOf(inpCode.value);
          const avail = p ? Number(p.available_qty || 0) : 0;
          if (!avail) { showToast('该代码无可用持仓'); return; }
          const n = ratio >= 1 ? avail : Math.floor(avail * ratio / 100) * 100;
          inpQty.value = String(n > 0 ? n : 100);
        }
      };

      // ------------------------------------------------------------ 渲染
      const render = () => {
        const L = S.list || {};
        S.el.badgeMode.textContent = 'mode ' + (L.mode || TRADE_MODE);
        S.el.sub.textContent = [L.date ? ('委托日 ' + L.date) : '',
          (S.ledger && S.ledger.asof) ? ('账本 ' + S.ledger.asof) : ''].filter(Boolean).join(' · ');

        const a = (S.account && S.account.account) || {};
        const posN = ((S.account && S.account.positions_list) || []).length;
        setHtml(S.el.secAcc, secHtml('账户（纸面账本）', posN ? (posN + ' 只持仓') : '空仓', accountHtml(S)), 'acc');
        setHtml(S.el.secOrders, secHtml('当日委托', ((L.orders || []).length) + ' 笔', ordersHtml(S)), 'orders');
        const condAll = L.conditions || [];
        const condActive = condAll.filter((c) => String(c.status) === 'ACTIVE').length;
        setHtml(S.el.secConds, secHtml('条件单', '监控中 ' + condActive + ' / 共 ' + condAll.length + ' 条',
          condsHtml(S)), 'conds');
        setHtml(S.el.feedBar, feedHtml(S.feed), 'feed');

        // 表单旁的读数（只改文本，不动输入框，防打字被刷新吞掉）
        const cash = a.available_cash;
        const p = positionsOf(inpCode.value);
        infoLine.textContent = '可用 ' + money(cash)
          + (isNum(a.frozen_cash) && Number(a.frozen_cash) > 0 ? '（挂单冻结 ' + money(a.frozen_cash) + '）' : '')
          + (p ? ' ｜ 该票持仓 ' + num(p.qty, 0) + ' 股 · 可卖 ' + num(p.available_qty, 0) + ' 股' : '');
        cQtyHint.textContent = (function () {
          const t = S.condType;
          const side = t === 'time_order' ? (segVal('cside') || 'buy') : null;
          const isBuy = t === 'breakout_buy' || t === 'rebound_buy' || t === 'signal_buy'
            || (t === 'time_order' && side === 'buy');
          if (isBuy) return '可用资金 ' + money(cash);
          const pp = positionsOf(inpCode.value);
          const held = pp ? Number(pp.qty || 0) : 0;
          const av = pp ? Number(pp.available_qty || 0) : 0;
          const lock = Math.max(0, held - av);
          return '持有 ' + held + ' 股 · 可卖 ' + av + ' 股'
            + (lock ? '（其中 ' + lock + ' 股今日买入，T+1 未解锁）' : '');
        })();
        cCount.textContent = (S.account && S.account.account) ? '可用 ' + money(a.available_cash) : '';
        const lineHint = cFields.querySelector('[data-cond="linehint"]');
        if (lineHint) lineHint.textContent = lineCondText();
      };

      // ------------------------------------------------------------ 取数（GET，失败只降级那一块）
      const load = async () => {
        if (!S.alive || S.loading) return;
        S.loading = true;
        try {
          const [acct, list, ledger, feed] = await Promise.all([
            api.get('/api/paper_account').catch((e) => ({ __error: errText(e), __status: e && e.status })),
            api.get('/api/order/list').catch((e) => ({ __error: errText(e) })),
            api.get('/api/ledger?mode=paper').catch((e) => ({ __error: errText(e) })),
            api.get('/api/paper/feed/status').catch((e) => ({ __error: errText(e) })),
          ]);
          if (!S.alive) return;
          S.account = acct;
          S.list = list;
          S.ledger = ledger;
          S.feed = feed;
          const frozen = {};
          ((ledger && ledger.positions) || []).forEach((r) => {
            if (r && r.code) frozen[r.code] = r.frozen_qty;
          });
          S.frozen = frozen;
          syncNameEcho();
          render();
        } finally {
          S.loading = false;
        }
      };
      S.load = load;

      // ------------------------------------------------------------ 模态确认弹层
      const closeModal = () => {
        if (S.modal) {
          document.removeEventListener('keydown', onModalKey, true);
          if (S.modal.parentNode) S.modal.parentNode.removeChild(S.modal);
          S.modal = null;
        }
      };
      // Enter 不触发确认（任务书 §4.2）：弹层开着时一律吞掉 Enter，必须鼠标点「确认」
      const onModalKey = (ev) => {
        if (!ev) return;
        if (ev.key === 'Enter') {
          ev.preventDefault();
          ev.stopPropagation();
        } else if (ev.key === 'Escape') {
          ev.preventDefault();
          closeModal();
        }
      };
      S.modalKey = onModalKey;
      const openModal = (opts) => {
        closeModal();
        const mask = div('pp-mask');
        mask.setAttribute('data-mask', '1');
        const box = div('pp-modal');
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        box.setAttribute('aria-label', opts.title);
        const hd = div('pp-modal-hd');
        hd.innerHTML = '<b>' + esc(opts.title) + '</b><span class="pp-badge paper">纸面</span>';
        const body = div('pp-modal-body');
        body.innerHTML = '<table class="pp-tbl"><tbody>' + opts.rows.map((r) => (
          '<tr><th>' + esc(r[0]) + '</th><td>' + esc(r[1]) + '</td></tr>'
        )).join('') + '</tbody></table>';
        const note = div('pp-modal-note', opts.note || '');
        const ft = div('pp-modal-ft');
        const btnCancel = btn('pp-btn', '取消', { 'data-modal-cancel': '1' });
        const btnOk = btn('pp-btn danger', opts.okText || '确认提交', { 'data-modal-ok': '1' });
        ft.append(btnCancel, btnOk);
        box.append(hd, body, note, ft);
        mask.append(box);
        root.append(mask);
        S.modal = mask;

        btnCancel.addEventListener('click', () => { closeModal(); });
        mask.addEventListener('click', (ev) => { if (ev.target === mask) closeModal(); });
        mask.addEventListener('keydown', onModalKey, true);
        document.addEventListener('keydown', onModalKey, true);
        btnOk.addEventListener('click', async () => {
          if (S.submitting) return;                    // 防双击
          S.submitting = true;
          btnOk.disabled = true;
          btnCancel.disabled = true;
          btnOk.textContent = '提交中…';
          try {
            await opts.onConfirm();
          } finally {
            S.submitting = false;
            if (btnOk.isConnected) { btnOk.disabled = false; }
            if (btnCancel.isConnected) { btnCancel.disabled = false; }
            closeModal();
          }
        });
      };

      // ------------------------------------------------------------ 写操作
      const orderSubmit = async () => {
        const side = segVal('side') === 'buy' ? 'buy' : 'sell';
        const otype = segVal('otype') === 'limit' ? 'limit' : 'market';
        const code = String(inpCode.value || '').trim();
        const qty = Math.floor(toNum(inpQty.value) || 0);
        const price = toNum(inpPrice.value);
        const name = String(inpName.value || '').trim() || resolveName(code);
        setStatus('', false);
        try {
          const res = await api.post('/api/order/place', {
            code: code, side: side, qty: qty,
            price: otype === 'limit' ? price : null,
            order_type: otype, name: name, note: NOTE_TAG,
          });
          const o = (res && res.order) || {};
          const w = ((res && res.warnings) || []).join('；');
          if (String(o.status) === 'FILLED') {
            setStatus('已成交 ' + num(o.filled_qty, 0) + ' 股 @ ' + num(o.filled_price, 3), false);
            showToast('已成交 ' + num(o.filled_qty, 0) + ' 股 @ ' + num(o.filled_price, 3));
          } else {
            setStatus('委托已受理：' + (o.paper_order_id || '—') + '（' + cn(STATUS_CN, o.status) + '）'
              + (w ? ' · ' + w : ''), false);
            showToast('委托已受理 ' + (o.paper_order_id || ''));
          }
          S.html = Object.create(null);   // 回执到达 → 以服务端为准重绘
          await load();
        } catch (e) {
          // 失败回执原样渲染（core 的 detail 带机器码，如（closed）/（no_quote））；不重试
          setStatus('下单失败：' + errText(e), true);
          showToast('下单失败');
        }
      };

      const condSubmit = async () => {
        const t = S.condType;
        const code = String(inpCode.value || '').trim();
        const qty = Math.floor(toNum(inpCQty.value) || 0);
        const name = String(inpName.value || '').trim() || resolveName(code);
        const payload = {
          code: code, type: t, qty: qty, name: name,
          order_type: segVal('cotype') || 'market',
          confirm_count: Number(segVal('cconf') || 1),
          note: NOTE_TAG,
        };
        const vd = Number(selCValid.value || 0);
        if (vd > 0) {
          const d = new Date(Date.now() + vd * 86400000);
          payload.valid_until = '' + d.getFullYear() + pad2(d.getMonth() + 1) + pad2(d.getDate());
        }
        if (t === 'time_order') {
          const atEl = cFields.querySelector('[data-cond="at"]');
          payload.at = (atEl && atEl.value) || '14:55';
          payload.side = segVal('cside') || 'buy';
        } else if (t === 'signal_buy') {
          const sigEl = cFields.querySelector('[data-cond="sig"]');
          payload.signal = (sigEl && sigEl.value) || 'BREAKOUT';
        } else if (t === 'breakeven_stop' || t === 'ma5_break') {
          payload.buffer_atr = 0.3;      // 与旧系统 stop_buffer_atr 同口径
        } else {
          if (t === 'trailing_sell' || t === 'rebound_buy') {
            const pctEl = cFields.querySelector('[data-cond="pct"]');
            payload.pct = toNum(pctEl && pctEl.value);
          }
          const bufv = segVal('cbuf');
          if (bufv && Number(bufv) > 0) payload.buffer_atr = Number(bufv);
          const trigEl = cFields.querySelector('[data-cond="trig"]');
          payload.trigger_price = toNum(trigEl && trigEl.value);
        }
        if (inpOco.checked) payload.group = 'OCO-' + code + '-' + Date.now();
        setStatus('', false);
        try {
          const res = await api.post('/api/condition/add', payload);
          const c = (res && res.condition) || {};
          setStatus('条件单已挂 ' + (c.cond_id || '') + '（' + cn(COND_LABEL, c.type)
            + ' · ' + condTriggerText(c) + '）', false);
          showToast('条件单已挂 ' + (c.cond_id || ''));
          S.html = Object.create(null);
          await load();
        } catch (e) {
          setStatus('条件单提交失败：' + errText(e), true);
          showToast('条件单提交失败');
        }
      };

      const cancelOrder = async (oid) => {
        setStatus('', false);
        try {
          const res = await api.post('/api/order/cancel', { order_id: oid });
          setStatus('已撤单 ' + ((res && res.order_id) || oid) + '（' + cn(STATUS_CN, (res || {}).status) + '）', false);
          showToast('已撤单 ' + oid);
          S.html = Object.create(null);
          await load();
        } catch (e) {
          setStatus('撤单失败（' + oid + '）：' + errText(e), true);
          showToast('撤单失败');
        }
      };

      const cancelCond = async (cid) => {
        setStatus('', false);
        try {
          const res = await api.post('/api/condition/cancel', { cond_id: cid });
          setStatus('已撤条件单 ' + ((res && res.condition && res.condition.cond_id) || cid), false);
          showToast('已撤条件单 ' + cid);
          S.html = Object.create(null);
          await load();
        } catch (e) {
          setStatus('撤条件单失败（' + cid + '）：' + errText(e), true);
          showToast('撤条件单失败');
        }
      };

      const runEvaluate = async () => {
        setStatus('', false);
        try {
          const res = await api.post('/api/condition/evaluate?force=1', {});
          const n = (k) => ((res && res[k]) || []).length;
          const qn = Object.keys((res && res.quotes) || {}).length;
          setStatus('评估完成：触发 ' + n('triggered') + ' · 撤销 ' + n('cancelled')
            + ' · 过期 ' + n('expired') + ' · 补成交 ' + n('filled')
            + ' · 行情 ' + qn + ' 只'
            + (qn === 0 ? '（无 feed 时行情为空属正常：行情源未启动）' : ''), false);
          showToast('已评估一轮');
          S.html = Object.create(null);
          await load();
        } catch (e) {
          setStatus('评估失败：' + errText(e), true);
        }
      };

      // ------------------------------------------------------------ 事件委托（按钮随数据重绘，监听挂稳定节点）
      wrap.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;

        const segBtn = t.closest('[data-seg]');
        if (segBtn) {
          const box = segBtn.closest('[data-segbox]') || segBtn.closest('.pp-seg');
          if (box) {
            Array.prototype.forEach.call(box.querySelectorAll('button'), (b) => b.classList.remove('on'));
            segBtn.classList.add('on');
            const name = box.getAttribute('data-segbox');
            if (name === 'otype') syncPriceRow();
            if (name === 'side') { syncLock(); }
            if (name === 'cside' || name === 'cotype') render();
          }
          return;
        }

        if (t.closest('[data-refresh]')) { load(); return; }

        if (t.closest('[data-cond-toggle]')) {
          const b = t.closest('[data-cond-toggle]');
          cBody.hidden = !cBody.hidden;
          b.textContent = cBody.hidden ? '展开' : '收起';
          if (!cBody.hidden) render();
          return;
        }

        if (t.closest('[data-lock]')) {
          if (segVal('side') !== 'buy') { showToast('数量锁只对买入生效（卖出永远可改）'); return; }
          qtyLocked = !qtyLocked;
          syncLock();
          showToast(qtyLocked ? '已锁定数量' : '已解锁，可修改');
          return;
        }

        const quick = t.closest('[data-qquick]');
        if (quick) { applyQuick(quick.getAttribute('data-qquick')); return; }

        // 下单：先过确认弹层（Enter 不触发）
        if (t.closest('[data-submit-order]')) {
          const side = segVal('side') === 'buy' ? 'buy' : 'sell';
          const otype = segVal('otype') === 'limit' ? 'limit' : 'market';
          const code = String(inpCode.value || '').trim();
          const qty = Math.floor(toNum(inpQty.value) || 0);
          const price = toNum(inpPrice.value);
          if (!code) { setStatus('请先填代码', true); return; }
          if (qty <= 0) { setStatus('数量要大于 0', true); return; }
          if (otype === 'limit' && !(price > 0)) { setStatus('限价单必须填写价格', true); return; }
          const nm = resolveName(code);
          openModal({
            title: '确认下单',
            rows: [
              ['代码', code + (nm ? '（' + nm + '）' : '')],
              ['方向', side === 'buy' ? '买入' : '卖出'],
              ['数量', num(qty, 0) + ' 股'],
              ['价格', otype === 'limit' ? (num(price, 3) + ' 元（限价）') : '市价（不填价格）'],
            ],
            note: '请核对四要素；本单只进纸面账本，无实盘通道。确认需鼠标点击，Enter 不触发。',
            okText: side === 'buy' ? '确认买入（纸面）' : '确认卖出（纸面）',
            onConfirm: orderSubmit,
          });
          return;
        }

        // 加条件单：同一模态弹层
        if (t.closest('[data-submit-cond]')) {
          const code = String(inpCode.value || '').trim();
          const qty = Math.floor(toNum(inpCQty.value) || 0);
          if (!code) { setStatus('请先填代码', true); return; }
          if (qty <= 0) { setStatus('数量要大于 0', true); return; }
          const t2 = S.condType;
          const trigEl = cFields.querySelector('[data-cond="trig"]');
          const pctEl = cFields.querySelector('[data-cond="pct"]');
          const atEl = cFields.querySelector('[data-cond="at"]');
          const sigEl = cFields.querySelector('[data-cond="sig"]');
          let trigText;
          if (t2 === 'time_order') trigText = ((atEl && atEl.value) || '14:55') + ' ' + ((segVal('cside') === 'sell') ? '卖出' : '买入');
          else if (t2 === 'trailing_sell' || t2 === 'rebound_buy') trigText = num(pctEl && pctEl.value, 1) + '%';
          else if (t2 === 'signal_buy') trigText = cn(SIGNAL_CN, (sigEl && sigEl.value) || 'BREAKOUT');
          else if (t2 === 'breakeven_stop' || t2 === 'ma5_break') trigText = lineCondText();
          else trigText = num(trigEl && trigEl.value, 3) + ' 元';
          const otype = segVal('cotype') || 'market';
          openModal({
            title: '确认提交条件单',
            rows: [
              ['代码', code + (resolveName(code) ? '（' + resolveName(code) + '）' : '')],
              ['类型', cn(COND_LABEL, t2)],
              ['触发参数', trigText],
              ['数量', num(qty, 0) + ' 股'],
              ['委托', otype === 'limit' ? '限价' : '市价'],
              ['有效期', (function () {
                const v = Number(selCValid.value || 0);
                if (!v) return '当日';
                return v + ' 天（到期自动过期）';
              })()],
            ],
            note: '条件单只在触发条件命中时才下单；本卡只做纸面。确认需鼠标点击，Enter 不触发。',
            okText: '确认提交（纸面）',
            onConfirm: condSubmit,
          });
          return;
        }

        // 撤单 / 立即评估：行内二次点击（第一次点亮「确认…」，4 秒内第二次才 POST）
        const arm = (b, label, action) => {
          if (b.getAttribute('data-arm') === '1') {
            b.setAttribute('data-arm', '0');
            b.textContent = label.idle;
            b.className = label.cls;
            action();
            return;
          }
          b.setAttribute('data-arm', '1');
          b.textContent = label.arm;
          b.className = 'pp-btn danger';
          if (S.armTimer) clearTimeout(S.armTimer);
          S.armTimer = setTimeout(() => {
            if (!b.isConnected) return;
            b.setAttribute('data-arm', '0');
            b.textContent = label.idle;
            b.className = label.cls;
          }, 4000);
        };

        const co = t.closest('[data-cancel-order]');
        if (co) {
          const oid = co.getAttribute('data-cancel-order');
          arm(co, { idle: '撤单', arm: '确认撤单？', cls: 'pp-btn' }, () => { cancelOrder(oid); });
          return;
        }
        const cc = t.closest('[data-cancel-cond]');
        if (cc) {
          const cid = cc.getAttribute('data-cancel-cond');
          arm(cc, { idle: '撤单', arm: '确认撤单？', cls: 'pp-btn' }, () => { cancelCond(cid); });
          return;
        }
        const ev2 = t.closest('[data-eval]');
        if (ev2) {
          arm(ev2, { idle: '立即评估', arm: '确认评估？', cls: 'pp-btn' }, () => { runEvaluate(); });
        }
      });

      // 条件单类型切换：重建专属字段（旧 665-698 的显隐逻辑）
      selCType.addEventListener('change', () => {
        S.condType = selCType.value || 'stop_loss';
        cHint.textContent = COND_HINT[S.condType] || '';
        buildCondFields();
        render();
      });

      syncPriceRow();
      syncLock();

      await load();
    },

    async unmount() {
      const S = this._pp;
      if (!S) return;
      S.alive = false;
      if (S.toastTimer) clearTimeout(S.toastTimer);
      if (S.armTimer) clearTimeout(S.armTimer);
      if (S.modal) {
        document.removeEventListener('keydown', S.modalKey, true);
        if (S.modal.parentNode) S.modal.parentNode.removeChild(S.modal);
        S.modal = null;
      }
      S.html = Object.create(null);
      this._pp = null;
    },

    // 20s 自动刷新（账户 + 委托）；页面不可见由 shell 跳过；弹层开着/提交中不刷新
    async refresh() {
      const S = this._pp;
      if (!S || !S.alive || S.loading || S.submitting) return;
      if (S.modal) return;
      await S.load();
    },
  }],
};
