// ============================================================================
// 仪表盘卡片 · 前端批次 1b（docs/task_fe_batch1b.md）
// ----------------------------------------------------------------------------
// 行为基准 = 旧 Tab engine/web/index.html 的「账本版」仪表盘（2026-10-21 起生效）：
//   · 3026-3045  dashShellHtml         壳：徽章行 / 实盘·纸面切换 / asof / meta / 双栏
//   · 3063-3088  ledgerKpisHtml        KPI 六格（金额与百分比分行；条件单到期警示）
//   · 3090-3113  ledgerPosHtml         持仓表 14 列（price_source=cost_fallback 灰显「成本」）
//   · 3115-3125  ledgerTradesHtml      今日成交（买红卖绿）
//   · 3127-3158  _bookRow / bookActiveHtml / bookHistHtml   活跃单 + 流水记录
//   · 3203-3243  renderDashboardLedger 引擎 / 执行器 / 看门狗 / 急停 / 签收 / 心跳徽章
//   · 3047-3053  _setHtml              内容缓存比对（2026-10-21 主人「屏幕一直闪烁」的修法）
//   · renderDashboard 里 renderDashboardLedger(d) 之后的死代码**不搬**（任务书 §不做）。
//
// 契约（任务书 §契约与约束）：
//   1. 只用 ctx.api.get；数据端点都是 root 挂载的绝对 /api/ 路径（shell.js 不拼插件前缀）。
//   2. 颜色一律 themes.css token（--up / --down / --warn / --danger / --dim / --accent 等），
//      本文件零裸 hex。
//   3. 骨架只建一次；之后每次刷新都先过 setHtml / setText 的字符串比对，内容没变就不写 DOM。
//   4. refreshEvery: 3000（旧版 3s）；上一轮还没回来就不叠请求。
//   5. 零写盘、零 import、不写全局；撤单按钮本批**只读**（点击提示批次 2）。
// ============================================================================

const LS_LEDGER_MODE = 'panel_ledger_mode';   // 与旧版同名 → 用户已有偏好无损
const STYLE_ID = 'dashboard-card-css';
const REFRESH_MS = 3000;
const TOAST_MS = 2600;
const DEFAULT_LEDGER_MODE = 'paper';          // 旧版兜底：trade_mode 非 live 即纸面
const EXEC_STALE_SEC = 90;                    // 执行器心跳超时阈值（与旧版同口径）

// 卡的数据源（与 backend.py::DATA_SOURCES 逐条对应，测试锁一致性）
const DATA_SOURCES = ['/api/ledger?mode=paper|live', '/api/dashboard.json', '/api/gates', '/api/status'];

// 枚举中文化（照旧 index.html 的 M 映射，只取本卡用到的子表）
const M_STATE = { INIT: '启动中', READY: '就绪', ARMED: '已签收可下单', TRIGGERED: '触发执行中',
  PAUSED: '已暂停', FROZEN: '急停冻结', ERROR: '异常', OFFLINE: '离线' };
const M_MODE = { READ_ONLY: '只读', ARMED: '已签收' };
const M_EXEC = { RUNNING: '运行中', STOPPED: '已停止', ERROR: '异常', EXITED: '已退出', ABSENT: '未运行' };
const M_WD = { OK: '正常', WARN: '告警', ERROR: '异常' };
const M_GATE = { GREEN: '绿·开', YELLOW: '黄·警', RED: '红·关', UNKNOWN: '—·无数据' };

// 持仓表 14 列（旧版 ledgerPosHtml 的表头，顺序逐字照搬）
const POS_HEADS = ['代码 / 名称', '余额', '可用', '冻结', '成本价', '现价', '参考盈亏', '盈亏%',
  '当日盈亏', '当日%', '市值', '仓位', '今买', '今卖'];

const CSS = [
  '.db-root{display:flex;flex-direction:column;gap:10px;color:var(--fg);min-width:0}',
  '.db-status{font-size:0.72rem;color:var(--dim);min-height:1em}',
  '.db-status.is-err{color:var(--danger-ink,var(--danger))}',
  '.gates-strip{display:flex;gap:8px;flex-wrap:wrap}',
  '.gates-strip .gate-lamp{flex:1 1 220px;min-width:200px;background:var(--card);border:var(--border-w) var(--border-style) var(--border-color);border-left-width:4px;border-radius:var(--radius-md);padding:7px 10px;min-width:0}',
  '.gate-lamp.g-GREEN{border-left-color:var(--down)}',
  '.gate-lamp.g-YELLOW{border-left-color:var(--warn)}',
  '.gate-lamp.g-RED{border-left-color:var(--danger)}',
  '.gate-lamp.g-UNKNOWN{border-left-color:var(--dim)}',
  '.gl-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px}',
  '.gl-name{font-weight:600;color:var(--fg-strong);font-size:0.78rem}',
  '.gl-state{font-family:var(--font-mono);font-size:0.75rem}',
  '.g-GREEN .gl-state{color:var(--down)}',
  '.g-YELLOW .gl-state{color:var(--warn)}',
  '.g-RED .gl-state{color:var(--danger)}',
  '.g-UNKNOWN .gl-state{color:var(--dim)}',
  '.gl-reason{font-size:0.7rem;color:var(--dim);margin-top:3px;line-height:1.5}',
  '.dash-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}',
  '.dash-badges{display:flex;gap:6px;flex-wrap:wrap;align-items:center;flex:1 1 auto;min-width:0}',
  '.dash-meta{font-size:0.62rem;color:var(--dim);word-break:break-all}',
  '.db-mode{display:flex;align-items:center;gap:8px;margin-left:auto}',
  '.fund-switch{display:inline-flex;gap:6px}',
  '.fund-switch button{background:var(--card2);color:var(--dim);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:2px 10px;font-size:0.75rem;font-family:inherit;cursor:pointer}',
  '.fund-switch button.on{background:var(--card3);color:var(--fg);border-color:var(--accent)}',
  '.lg-asof{font-size:0.7rem;color:var(--dim);font-variant-numeric:tabular-nums}',
  '.badge{display:inline-block;padding:1px 8px;border-radius:var(--radius-pill);background:var(--card2);color:var(--fg);margin:1px 2px;font-size:0.75rem}',
  '.badge.up{background:var(--up-bg);color:var(--up-ink)}',
  '.badge.warn{background:var(--warn-bg);color:var(--warn-ink)}',
  '.badge.danger{background:var(--danger-bg);color:var(--danger-ink)}',
  '.mode-badge{display:inline-block;padding:2px 10px;border-radius:var(--radius-pill);font-size:0.75rem;margin:1px 2px;font-weight:600;white-space:nowrap}',
  '.mode-badge-paper{background:var(--warn-solid);color:var(--warn-on)}',
  '.mode-badge-live{background:var(--down-bg);color:var(--down-ink)}',
  '.ex-badge{display:inline-block;padding:2px 10px;border-radius:var(--radius-pill);font-size:0.75rem;margin:1px 2px;cursor:default;border:1px solid transparent;user-select:none;white-space:nowrap}',
  '.ex-running{background:var(--down-bg);color:var(--down-ink);border-color:var(--down)}',
  '.ex-stopped{background:var(--line-soft);color:var(--dim);border-color:var(--line)}',
  '.ex-error{background:var(--danger-bg);color:var(--danger-ink);border-color:var(--danger)}',
  '.ex-warn{background:var(--warn-bg);color:var(--warn-ink);border-color:var(--warn)}',
  '.ex-badge[data-open-card]{cursor:pointer}',
  '.db-kv{flex:1 1 100%;display:flex;gap:6px;flex-wrap:wrap;font-size:0.7rem;color:var(--dim)}',
  '.db-kv b{color:var(--fg);font-weight:400}',
  '.dash-grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(340px,1fr);gap:12px;align-items:start}',
  '.dash-main,.dash-side{min-width:0}',
  '.dash-card{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card);padding:8px 10px;margin-bottom:10px;min-width:0}',
  '.dash-card-flat{padding:0;border:0;background:transparent;margin-bottom:6px}',
  '.dc-hd{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:0.7rem;color:var(--dim);margin-bottom:5px}',
  '.dc-n{font-size:0.62rem;color:var(--dim);opacity:.8}',
  '.dc-bd{min-width:0;max-height:330px;overflow:auto}',
  '.lg-sum{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}',
  '.kpi{background:var(--card2);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);padding:6px 9px;min-width:0}',
  '.kpi .k{color:var(--dim);font-size:0.7rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.kpi .v{font-size:0.95rem;font-weight:600;color:var(--fg-strong);font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  '.kpi .s{font-size:0.65rem;color:var(--dim);margin-top:1px;font-variant-numeric:tabular-nums}',
  '.db-root .up{color:var(--up)}',
  '.db-root .down{color:var(--down)}',
  '.db-root .muted{color:var(--dim)}',
  '.lg-warn{margin:0 0 8px;padding:5px 8px;border-radius:var(--radius-sm);background:var(--warn-bg);color:var(--warn-ink);font-size:0.7rem;line-height:1.45}',
  '.db-tbl{width:100%;border-collapse:collapse;font-size:0.75rem;font-variant-numeric:tabular-nums}',
  '.db-tbl th{text-align:left;font-weight:400;color:var(--dim);font-size:0.68rem;padding:3px 6px;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.db-tbl td{padding:3px 6px;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.db-tbl th.n,.db-tbl td.n{text-align:right}',
  '.lg-code{white-space:nowrap;color:var(--fg-strong)}',
  '.lg-code i{font-style:normal;color:var(--dim);font-size:0.66rem;margin-left:4px}',
  '.src-tag{color:var(--dim);font-size:0.62rem;margin-left:2px}',
  '.bk-flow{display:grid;grid-template-columns:1fr;font-size:0.72rem;line-height:1.55}',
  '.bk-fi{display:flex;gap:6px;align-items:baseline;white-space:nowrap;overflow:hidden;min-width:0}',
  '.bk-fi i{font-style:normal;color:var(--dim);flex:0 0 auto;font-variant-numeric:tabular-nums}',
  '.bk-fi .kd{flex:0 0 auto;color:var(--dim);opacity:.75}',
  '.bk-fi .nm{flex:0 1 auto;max-width:72px;overflow:hidden;text-overflow:ellipsis;min-width:0}',
  '.bk-fi .dr{flex:0 0 auto}',
  '.bk-fi .px,.bk-fi .st{color:var(--dim);flex:0 0 auto;font-variant-numeric:tabular-nums}',
  '.bk-mini{border:1px solid var(--line);background:transparent;color:var(--dim);border-radius:var(--radius-sm);font-size:0.62rem;padding:0 5px;height:16px;cursor:pointer;line-height:1;font-family:inherit}',
  '.bk-mini:hover{color:var(--accent);border-color:var(--accent)}',
  '.db-toast{align-self:flex-start;font-size:0.72rem;padding:4px 10px;border-radius:var(--radius-sm);background:var(--accent-bg);color:var(--accent-ink,var(--accent));border:1px solid var(--accent)}',
  '.db-src{font-size:0.66rem;color:var(--dim);line-height:1.6}',
  '@media (max-width:1250px){.lg-sum{grid-template-columns:repeat(3,minmax(0,1fr))}}',
  '@media (max-width:1100px){.dash-grid{grid-template-columns:minmax(0,1fr)}}',
  '@media (max-width:640px){.lg-sum{grid-template-columns:repeat(2,minmax(0,1fr))}}',
].join('\n');

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------- 小工具

const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

// 千分位 + 固定小数（旧版 _lgNum）：非数字一律「—」，不编造 0
const num = (v, d) => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return '—';
  const digits = d == null ? 2 : d;
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

const pct = (v) => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return '—';
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
};

// 红涨绿跌（旧版 _lgCls）：正 up / 负 down / 其余无色
const signCls = (v) => {
  const n = Number(v);
  if (v === null || v === undefined || v === '' || !isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
};

const cnOf = (map, v) => {
  if (v === null || v === undefined || v === '') return '—';
  const key = String(v);
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : key;
};

const errText = (e) => (e && e.message) || String(e);

// ---------------------------------------------------------------- 纯渲染函数（输入 JSON，输出 HTML）

function kpiCell(k, v, cls) {
  return '<div class="kpi"><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">'
    + v + '</div></div>';
}

// 金额 + 百分比分两行：单行会溢出卡片（旧版 2026-10-21 主人反馈）
function kpiCell2(k, v, pctText, cls) {
  return '<div class="kpi"><div class="k">' + esc(k) + '</div><div class="v ' + (cls || '') + '">'
    + v + '</div><div class="s ' + (cls || '') + '">' + pctText + '</div></div>';
}

function kpisHtml(d) {
  if (d === null || d === undefined) return '<p class="muted">账本加载中…（/api/ledger）</p>';
  if (d.__error) return '<p class="muted">账本加载失败：' + esc(d.__error) + '</p>';
  if (d.ok === false) {
    // paper 插件对 mode=live 明确返回 ok=false（实盘账本不假装有，任务书 §数据源）
    return '<p class="muted">账本不可用（mode=' + esc(d.mode || '—') + '）：'
      + esc(d.error || '未返回 ok') + '</p>';
  }
  const a = d.account || {};
  let warn = '';
  const ex = d.conds_expiring || [];
  if (ex.length) {
    warn = '<div class="lg-warn">⚠ 条件单即将到期 ' + ex.length + ' 条：' + ex.map((c) => (
      esc(c.name || c.code || '') + ' ' + esc(c.label || c.type || '') + '（剩 ' + esc(c.days_left) + ' 天）'
    )).join('；') + '</div>';
  }
  return warn + '<div class="lg-sum">'
    + kpiCell('总资产', num(a.total_asset))
    + kpiCell2('可用', num(a.available_cash), a.frozen_cash > 0 ? ('冻 ' + num(a.frozen_cash)) : '冻 0')
    + kpiCell('市值', num(a.market_value))
    + kpiCell2('当日盈亏', num(a.day_pnl), pct(a.day_pnl_pct), signCls(a.day_pnl))
    + kpiCell2('持仓盈亏', num(a.position_pnl), pct(a.position_pnl_pct), signCls(a.position_pnl))
    + kpiCell('现金占比', num(a.cash_ratio) + '%')
    + '</div>';
}

function posHtml(d) {
  const ps = (d && !d.__error && d.ok !== false && Array.isArray(d.positions)) ? d.positions : [];
  if (!ps.length) return '<p class="muted">无持仓</p>';
  const head = POS_HEADS.map((h, i) => '<th class="' + (i === 0 ? '' : 'n') + '">' + esc(h) + '</th>').join('');
  const rows = ps.map((p) => {
    const stale = p.price_source === 'cost_fallback';
    const cells = [
      '<td><span class="lg-code" data-ctx="stock" data-code="' + esc(p.code) + '" data-name="' + esc(p.name)
        + '" data-open-kline="' + esc(p.code) + '" data-open-name="' + esc(p.name || p.code) + '">'
        + esc(p.name || p.code) + '<i>' + esc(String(p.code || '').slice(0, 6)) + '</i></span></td>',
      '<td class="n">' + num(p.qty, 0) + '</td>',
      '<td class="n">' + num(p.available_qty, 0) + '</td>',
      '<td class="n">' + num(p.frozen_qty, 0) + '</td>',
      '<td class="n">' + num(p.cost_price, 3) + '</td>',
      '<td class="n"><span class="' + (stale ? 'muted' : '') + '" title="'
        + (stale ? '无实时行情，回退成本价' : '价源 ' + esc(p.price_source || '')) + '">' + num(p.price, 3)
        + (stale ? '<span class="src-tag">成本</span>' : '') + '</span></td>',
      '<td class="n"><span class="' + signCls(p.pnl) + '">' + num(p.pnl) + '</span></td>',
      '<td class="n"><span class="' + signCls(p.pnl_pct) + '">' + pct(p.pnl_pct) + '</span></td>',
      '<td class="n"><span class="' + signCls(p.day_pnl) + '">' + (p.day_pnl == null ? '—' : num(p.day_pnl)) + '</span></td>',
      '<td class="n"><span class="' + signCls(p.day_pnl_pct) + '">' + pct(p.day_pnl_pct) + '</span></td>',
      '<td class="n">' + num(p.market_value) + '</td>',
      '<td class="n">' + (p.weight_pct == null ? '—' : num(p.weight_pct) + '%') + '</td>',
      '<td class="n">' + num(p.bought_today, 0) + '</td>',
      '<td class="n">' + num(p.sold_today, 0) + '</td>',
    ];
    return '<tr>' + cells.join('') + '</tr>';
  });
  return '<table class="db-tbl"><thead><tr>' + head + '</tr></thead><tbody>' + rows.join('') + '</tbody></table>';
}

function tradesHtml(d) {
  const ts = (d && !d.__error && d.ok !== false && Array.isArray(d.trades)) ? d.trades : [];
  if (!ts.length) return '<p class="muted">今日无成交</p>';
  return '<div class="bk-flow">' + ts.map((t) => {
    const buy = String(t.side || '').toUpperCase() === 'BUY';
    return '<div class="bk-fi"><i>' + esc(t.time || '') + '</i>'
      + '<span class="nm">' + esc(t.name || String(t.code || '').slice(0, 6)) + '</span>'
      + '<b class="' + (buy ? 'up' : 'down') + '">' + (buy ? '买' : '卖') + '</b>'
      + '<span class="px">' + num(t.price, 3) + '×' + num(t.qty, 0) + '</span>'
      + '<span class="st">' + num(t.amount) + (t.fee == null ? '' : ' · 费' + num(t.fee)) + '</span></div>';
  }).join('') + '</div>';
}

function bookRow(o) {
  const isCond = o.kind === 'cond';
  const tag = isCond ? '条' : '委';
  const dir = isCond
    ? esc(o.label || '条件')
    : '<b class="' + (o.side === 'BUY' ? 'up' : 'down') + '">' + (o.side === 'BUY' ? '买' : '卖') + '</b>';
  const active = o.raw_status === 'ACTIVE' || o.raw_status === 'REPORTED' || o.raw_status === 'PARTIAL';
  const extra = (isCond && o.raw_status === 'ACTIVE' && o.days_left != null)
    ? '<span class="st">剩' + esc(o.days_left) + '天' + (o.expiring ? '⚠' : '') + '</span>' : '';
  // 本批只读：按钮照旧渲染，点击handler 提示「批次 2 接」（任务书 §功能 6）
  const btn = active
    ? '<button type="button" class="bk-mini" ' + (isCond
      ? 'data-ccancel="' + esc(o.id) + '"' : 'data-cancel="' + esc(o.id) + '"')
      + ' title="撤单属批次 2">撤</button>'
    : '';
  return '<div class="bk-fi"><i>' + esc(o.time || '') + '</i>'
    + '<span class="kd">' + tag + '</span>'
    + '<span class="nm"' + (o.code
      ? ' data-open-kline="' + esc(o.code) + '" data-open-name="' + esc(o.name || '') + '"' : '') + '>'
    + esc(o.name || '') + '</span>'
    + '<span class="dr">' + dir + '</span>'
    + '<span class="px">' + (o.price > 0 ? num(o.price, 3) + '×' + num(o.qty, 0) : '×' + num(o.qty, 0)) + '</span>'
    + '<span class="st">' + esc(o.status || '') + '</span>' + extra + btn + '</div>';
}

function bookOf(d) {
  if (!d || d.__error || d.ok === false) return { active: [], history: [], hist_total: 0 };
  const b = d.book || {};
  return {
    active: Array.isArray(b.active) ? b.active : [],
    history: Array.isArray(b.history) ? b.history : [],
    hist_total: b.hist_total == null ? null : b.hist_total,
    hist_max: b.hist_max,
  };
}

function bookActiveHtml(d) {
  const act = bookOf(d).active;
  return act.length
    ? '<div class="bk-flow">' + act.map(bookRow).join('') + '</div>'
    : '<p class="muted">无活跃单（挂单 + 监控中条件单）</p>';
}

function bookHistHtml(d) {
  const hist = bookOf(d).history;
  return hist.length
    ? '<div class="bk-flow">' + hist.map(bookRow).join('') + '</div>'
    : '<p class="muted">今日无流水</p>';
}

// 引擎徽章行（照旧 renderDashboardLedger 的 engineHtml）
function engineHtml(dash, status, viewMode) {
  const d = dash || {};
  const e = d.engine || {};
  const ex = d.executor || {};
  const wd = d.watchdog || {};
  const ks = e.kill_switch || {};
  const hb = e.heartbeat || {};
  const st = e.state || '—';
  const stCls = st === 'FROZEN' ? 'danger' : (st === 'ARMED' ? 'up' : 'warn');

  // 急停实时源优先 /api/status（kill_switch.json 权威），未拉到回退 dashboard.json
  const killActive = (status && typeof status.kill_switch_active === 'boolean')
    ? status.kill_switch_active
    : !!(ks && ks.active);
  const tradeMode = (status && (status.trade_mode === 'paper' || status.trade_mode === 'live'))
    ? status.trade_mode : null;
  const modeKey = tradeMode || viewMode;
  const modeSrc = tradeMode
    ? '数据源 /api/status trade_mode'
    : 'trade_mode 未上报（/api/status 返回 null）→ 按当前账本视图显示';
  const modeText = modeKey === 'paper' ? '纸面 [PAPER]' : '实盘';
  const modeBadge = '<span class="mode-badge ' + (modeKey === 'paper' ? 'mode-badge-paper' : 'mode-badge-live')
    + '" title="交易模式：' + esc(modeSrc) + '">' + esc(modeText + (tradeMode ? '' : '·视图')) + '</span>';

  let exBadge;
  if (!ex.present) {
    exBadge = '<span class="ex-badge ex-stopped" title="执行器状态文件缺失（auto_buy 未运行）">执行器 未运行</span>';
  } else {
    const stale = ex.status === 'RUNNING' && ex.heartbeat_age_sec != null && ex.heartbeat_age_sec > EXEC_STALE_SEC;
    const exCls = (stale || ex.status === 'ERROR') ? 'ex-error' : (ex.status === 'RUNNING' ? 'ex-running' : 'ex-stopped');
    const exLabel = stale ? '心跳超时' : cnOf(M_EXEC, ex.status);
    // 2026-11-07：作战表卡已删（预警页顶替），徽标不再跳转
    exBadge = '<span class="ex-badge ' + exCls + '" title="执行器状态（auto_buy）">执行器 '
      + esc(exLabel) + '</span>';
  }

  let wdBadge;
  if (wd.present) {
    const overall = wd.overall || '';
    const wdCls = overall === 'ERROR' ? 'ex-error' : (overall === 'WARN' ? 'ex-warn' : 'ex-running');
    wdBadge = '<span class="ex-badge ' + wdCls + '" title="看门狗健康：正常/告警/异常；上次检查 '
      + esc(wd.last_check || '—') + ' · 今日告警 ' + esc(wd.alerts_today == null ? 0 : wd.alerts_today)
      + '">看门狗 ' + esc(cnOf(M_WD, overall)) + '</span>';
  } else {
    wdBadge = '<span class="ex-badge ex-stopped" title="watchdog 状态文件缺失（看门狗未运行）">看门狗 状态文件缺失</span>';
  }

  const killBadge = killActive
    ? '<span class="badge danger">⛔ 已急停：' + esc(ks.reason || '') + '</span>' : '';
  // 2026-11-07：签收链已整体删除（主人拍板：只有模拟盘，签收是空壳）——这里不再展示签收态
  const kv = '<div class="db-kv">'
    + '<b>心跳</b> ' + esc(hb.last_seen || '—')
    + ' · 运行 ' + esc(hb.uptime_seconds == null ? 0 : hb.uptime_seconds) + ' 秒'
    + (hb.version ? ' · ' + esc(hb.version) : '')
    + '</div>';

  return '<span class="badge ' + stCls + '">' + esc(cnOf(M_STATE, st)) + '</span>'
    + '<span class="badge">' + esc(cnOf(M_MODE, e.mode || '')) + '</span>'
    + modeBadge + exBadge + wdBadge + killBadge + kv;
}

function metaHtml(dash) {
  const meta = dash && dash.meta;
  if (!meta) return '';
  return '生产者: ' + esc(meta.producer || '') + ' @ ' + esc(meta.generated_at || '')
    + (meta.source ? ' ｜ 来源: ' + esc(meta.source) : '');
}

function gatesHtml(g) {
  const gates = g ? [g.gate1, g.gate2, g.gate3].filter(Boolean) : [];
  if (!gates.length) {
    return '<div class="gate-lamp g-UNKNOWN"><div class="gl-head"><span class="gl-name">三道门状态灯</span>'
      + '<span class="gl-state">不可用</span></div><div class="gl-reason">/api/gates 拉取失败</div></div>';
  }
  return gates.map((x) => {
    const state = x.state || 'UNKNOWN';
    const warnItems = (x.items || []).filter((i) => i && i.state && i.state !== 'GREEN' && i.state !== 'UNKNOWN');
    const detail = warnItems.length
      ? ' ｜ ' + warnItems.map((i) => esc(i.name || i.ts_code || '—') + (i.state === 'RED' ? ' 触线' : ' 逼近')
        + '（' + num(i.price) + ' vs 线 ' + num(i.stop_price) + '）').join('；')
      : '';
    const asof = x.data_asof ? ' <span class="src-tag">数据 ' + esc(x.data_asof) + '</span>' : '';
    return '<div class="gate-lamp g-' + esc(state) + '"><div class="gl-head"><span class="gl-name">'
      + esc(x.name || '—') + asof + '</span><span class="gl-state">' + esc(M_GATE[state] || state)
      + '</span></div><div class="gl-reason">' + esc(x.reason || '—') + detail + '</div></div>';
  }).join('');
}

// ---------------------------------------------------------------- 卡片

export default {
  cards: [{
    id: 'dashboard.main',
    title: '仪表盘',
    slot: 'main',
    order: 5,
    refreshEvery: REFRESH_MS,

    async mount(root, ctx) {
      root.textContent = '';
      const api = ctx.api;
      const S = {
        ctx: ctx,
        root: root,
        api: api,
        alive: true,
        loading: false,
        autoMode: false,        // 用户没存过偏好 → 首帧跟随 /api/status 的 trade_mode
        mode: DEFAULT_LEDGER_MODE,
        ledger: null,
        dash: null,
        gates: null,
        status: null,
        html: Object.create(null),   // setHtml 缓存：key -> 上次写入的 HTML（防闪）
        text: Object.create(null),   // setText 缓存
        toastTimer: null,
        el: {},
      };
      this._db = S;

      // ---- 账本视图记忆（旧版键名 panel_ledger_mode）
      let stored = null;
      try {
        const raw = localStorage.getItem(LS_LEDGER_MODE);
        stored = (raw === 'paper' || raw === 'live') ? raw : null;
      } catch (e) { stored = null; }
      S.mode = stored || DEFAULT_LEDGER_MODE;
      S.autoMode = !stored;

      const div = (cls, text) => {
        const el = document.createElement('div');
        if (cls) el.className = cls;
        if (text != null) el.textContent = text;
        return el;
      };
      const span = (cls, text) => {
        const el = document.createElement('span');
        if (cls) el.className = cls;
        if (text != null) el.textContent = text;
        return el;
      };

      // ---- 骨架（只建一次；之后只换内容，不重建 DOM → 不闪、不丢按钮焦点）
      const wrap = div('db-root');

      const gates = div('gates-strip');
      gates.innerHTML = gatesHtml(null);

      const top = div('dash-top');
      const engine = div('dash-badges');
      const modeBox = div('db-mode');
      const sw = div('fund-switch');
      const bLive = document.createElement('button');
      bLive.type = 'button';
      bLive.className = 'fund-switch-btn';
      bLive.setAttribute('data-m', 'live');
      bLive.textContent = '实盘';
      const bPaper = document.createElement('button');
      bPaper.type = 'button';
      bPaper.className = 'fund-switch-btn';
      bPaper.setAttribute('data-m', 'paper');
      bPaper.textContent = '纸面';
      const asof = span('lg-asof', '—');
      sw.append(bLive, bPaper);
      modeBox.append(sw, asof);
      top.append(engine, modeBox);

      const meta = div('dash-meta');
      const status = div('db-status', '账本加载中…（/api/ledger）');

      const grid = div('dash-grid');
      const main = div('dash-main');
      const side = div('dash-side');

      const kpiCard = div('dash-card dash-card-flat');
      const kpis = div('lg-kpis');
      kpiCard.append(kpis);

      const posCard = div('dash-card');
      const posHd = div('dc-hd');
      posHd.append(span('', '持仓'), span('dc-n', ''));
      const pos = div('dc-bd');
      posCard.append(posHd, pos);

      const tradeCard = div('dash-card');
      const tradeHd = div('dc-hd');
      tradeHd.append(span('', '今日成交'), span('dc-n', ''));
      const trades = div('dc-bd');
      tradeCard.append(tradeHd, trades);

      main.append(kpiCard, posCard, tradeCard);

      const actCard = div('dash-card');
      const actHd = div('dc-hd');
      const actN = span('dc-n', '');
      actHd.append(span('', '活跃单'), actN);
      const active = div('dc-bd');
      actCard.append(actHd, active);

      const histCard = div('dash-card');
      const histHd = div('dc-hd');
      const histN = span('dc-n', '');
      histHd.append(span('', '流水记录'), histN);
      const hist = div('dc-bd');
      histCard.append(histHd, hist);

      side.append(actCard, histCard);
      grid.append(main, side);

      const toast = div('db-toast', '');
      toast.hidden = true;

      const src = div('db-src', '数据源：/api/ledger（账本，paper|live）· /api/dashboard.json（引擎/执行器/看门狗）'
        + ' · /api/gates（三道门）· /api/status（trade_mode / 急停）。'
        + '撤单属批次 2，本卡只读；点个股走 K 线跳转（data-open-kline，批次 1c 接）。');

      wrap.append(gates, top, meta, status, grid, toast, src);
      root.append(wrap);

      S.el = { wrap: wrap, gates: gates, engine: engine, meta: meta, status: status, kpis: kpis,
        pos: pos, trades: trades, active: active, hist: hist, actN: actN, histN: histN,
        asof: asof, bLive: bLive, bPaper: bPaper, toast: toast };

      // ---- 防闪写入：内容没变就不碰 DOM（旧版 _setHtml 的同一策略）
      const setHtml = (el, html, key) => {
        if (!el) return;
        if (S.html[key] === html) return;
        S.html[key] = html;
        el.innerHTML = html;
      };
      const setText = (el, text, key) => {
        if (!el) return;
        if (S.text[key] === text) return;
        S.text[key] = text;
        el.textContent = text;
      };

      const showToast = (text) => {
        if (!S.el.toast) return;
        S.el.toast.textContent = text;
        S.el.toast.hidden = false;
        if (S.toastTimer) clearTimeout(S.toastTimer);
        S.toastTimer = setTimeout(() => {
          if (S.el.toast) S.el.toast.hidden = true;
        }, TOAST_MS);
      };

      const setStatus = (text, isErr) => {
        setText(S.el.status, text || '', 'status');
        if (S.el.status) S.el.status.className = 'db-status' + (isErr ? ' is-err' : '');
      };

      const clearCaches = () => {
        // 切视图清缓存：避免先闪一帧旧账本数据（旧版 ledgerSwitch 同做法）
        S.html = Object.create(null);
        S.text = Object.create(null);
      };

      const renderMode = () => {
        if (S.el.bLive) S.el.bLive.className = 'fund-switch-btn' + (S.mode === 'live' ? ' on' : '');
        if (S.el.bPaper) S.el.bPaper.className = 'fund-switch-btn' + (S.mode === 'paper' ? ' on' : '');
        const lg = S.ledger;
        const asofText = (lg && !lg.__error && lg.ok !== false && lg.asof)
          ? ('更新于 ' + lg.asof) : '—';
        setText(S.el.asof, asofText, 'asof');
      };

      const render = () => {
        setHtml(S.el.gates, gatesHtml(S.gates), 'gates');
        setHtml(S.el.engine, engineHtml(S.dash, S.status, S.mode), 'engine');
        setHtml(S.el.meta, metaHtml(S.dash), 'meta');
        setHtml(S.el.kpis, kpisHtml(S.ledger), 'kpis');
        setHtml(S.el.pos, posHtml(S.ledger), 'pos');
        setHtml(S.el.trades, tradesHtml(S.ledger), 'trades');
        setHtml(S.el.active, bookActiveHtml(S.ledger), 'bkActive');
        setHtml(S.el.hist, bookHistHtml(S.ledger), 'bkHist');
        const bk = bookOf(S.ledger);
        const histTotal = bk.hist_total == null ? bk.history.length : bk.hist_total;
        setText(S.el.actN, bk.active.length ? (bk.active.length + ' 笔') : '', 'actN');
        setText(S.el.histN, bk.history.length
          ? (bk.history.length + (histTotal > bk.history.length ? '/' + histTotal : '') + ' 条') : '', 'histN');
        renderMode();
      };

      // ---- 取数（一次拉四份；单份失败只降级那一块，不拖垮整卡）
      const load = async () => {
        if (!S.alive || S.loading) return;
        S.loading = true;
        const modeAtStart = S.mode;
        try {
          const [lg, dash, gatesData, statusData] = await Promise.all([
            api.get('/api/ledger?mode=' + modeAtStart).catch((e) => ({ __error: errText(e) })),
            api.get('/api/dashboard.json').catch(() => null),
            api.get('/api/gates').catch(() => null),
            api.get('/api/status').catch(() => null),
          ]);
          if (!S.alive) return;
          // 请求在途时用户切了视图 → 这批数据是旧视图的，丢掉
          if (modeAtStart !== S.mode) return;
          S.ledger = lg;
          if (dash) S.dash = dash;
          if (gatesData) S.gates = gatesData;
          if (statusData) S.status = statusData;
          // 首帧跟随交易模式（用户没存过偏好时）
          if (S.autoMode && statusData && (statusData.trade_mode === 'live' || statusData.trade_mode === 'paper')
            && statusData.trade_mode !== S.mode) {
            S.autoMode = false;
            S.mode = statusData.trade_mode;
            clearCaches();
            S.loading = false;
            await load();
            return;
          }
          render();
          if (lg && lg.__error) setStatus('账本加载失败：' + lg.__error, true);
          else if (lg && lg.ok === false) setStatus('账本不可用：' + (lg.error || 'ok=false'), true);
          else setStatus((S.mode === 'paper' ? '纸面账本' : '实盘账本') + ' · asof ' + ((lg && lg.asof) || '—')
            + ' · 每 ' + Math.round(REFRESH_MS / 1000) + 's 刷新', false);
        } finally {
          S.loading = false;
        }
      };
      S.load = load;

      // ---- 事件：实盘 / 纸面切换（旧版 ledgerSwitch）
      const switchMode = (m) => {
        const next = m === 'live' ? 'live' : 'paper';
        if (next === S.mode) return;
        S.mode = next;
        S.autoMode = false;
        try { localStorage.setItem(LS_LEDGER_MODE, next); } catch (e) { /* 隐私模式写不进去：忽略 */ }
        clearCaches();
        S.ledger = null;
        render();
        load();
      };
      if (S.el.bLive) S.el.bLive.addEventListener('click', () => switchMode('live'));
      if (S.el.bPaper) S.el.bPaper.addEventListener('click', () => switchMode('paper'));

      // ---- 事件：撤单按钮（本批只读）
      wrap.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const btn = t.closest('[data-cancel],[data-ccancel]');
        if (btn) {
          const oid = btn.getAttribute('data-cancel');
          const cid = btn.getAttribute('data-ccancel');
          showToast('撤单属批次 2 接：' + (oid ? ('委托 ' + oid) : ('条件单 ' + cid)) + '（本卡只读）');
          return;
        }
        const jump = t.closest('[data-open-card]');
        if (jump) {
          const id = jump.getAttribute('data-open-card');
          let handled = false;
          try {
            const e = new CustomEvent('panel:open-card', { detail: { pluginId: id }, bubbles: true, cancelable: true });
            document.dispatchEvent(e);
            handled = e.defaultPrevented;
          } catch (err) { handled = false; }
          if (!handled) console.debug('[dashboard] 无监听方：panel:open-card', id);
        }
      });

      await load();
    },

    async unmount() {
      const S = this._db;
      if (!S) return;
      S.alive = false;
      if (S.toastTimer) clearTimeout(S.toastTimer);
      S.html = Object.create(null);
      S.text = Object.create(null);
      this._db = null;
    },

    // 3s 刷新：只重拉数据并比对后写内容，骨架与事件监听不动
    async refresh() {
      const S = this._db;
      if (!S || !S.alive) return;
      if (S.loading) return;      // 上一轮还没回来：不叠请求（3s tick 防堆积）
      await S.load();
    },
  }],
};
