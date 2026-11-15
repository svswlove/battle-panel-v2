/**
 * intraday 卡 · 审计明细 + 会话行 + 成交额对比（照旧页搬）
 *
 * 行为基准 = engine/web/index.html：
 *   会话行          4707（renderIntraday 内联一行）
 *   成交额对比       4694-4699 + _br880AmountTxt 5170-5173 + 4702 汇总行
 *   审计明细 details 4718-4731（summary + 5 张 card：信号流水 / 判强判弱快照 /
 *                    环境系数修正 / 盘中事件 / 通道B·指令单）
 *   映射表 M         2962-3004（只搬本组用到的 9 张子表）+ cn 3005 + cnSigSource 3007-3011
 *   工具             esc/num/pct 2842-2844 · cls 2845 · kCode 2920-2927 · table 2949-2953
 *                    fmtAmount 2928-2934
 *
 * 与旧页的结构性差别（都是「整页 → 卡片」的必然结果，语义没动，逐条对应任务书四条约束）：
 *
 * ① 模块级状态 → 每实例状态 + AbortController、② getElementById → 容器内查询：
 *    本组在旧页**没有模块级可变状态**（block 全是 renderIntraday 的一次性内联表达式，
 *    唯一的 module-level 是 _breadth880 的读缓存，那属于广度块，v2 已由 frontend.js 持有）。
 *    故本模块只有纯函数 + 一个 createState/bind/paint/unbind 壳（无 refresh：数据由 frontend.js
 *    主 load 统一取，本模块只负责把 d 画成 HTML）。
 *    details 的展开/收起走浏览器原生行为，不需要 JS 监听（约束：原生优先）。
 *
 * ③ 浮层/子节点挂容器内：本组旧页没有浮层，也没有越界查询（无 #id 依赖）。
 *
 * ④ 内联 onclick → data-act 委托：本组旧页**本来就没有 onclick**——
 *    信号流水/快照里的代码列走的就是 kCode() 的 data-open-kline 占位（旧页 2920 已如此），
 *    个股名走 kName() 的 data-kname（research.js 已搬，本模块直接复用，不重写第二份）。
 *
 * 三处**有意偏离**（都不改语义，只为「卡里不占屏 / 不撞壳样式 / 不靠 id」）：
 *   a. 旧页 card() 产出的 `<div class="card">` → 本模块用 `.it-audit-card`。
 *      卡内嵌套一个全局 .card 会套上壳的卡片阴影/裁切，视觉上像卡里长了个卡；
 *      换成本地类名后样式只在 audit.css.js 里定义（作用域 .it-card）。
 *   b. `<span class="to-val" id="cur880Amt">` 去掉 id（旧页靠 _br880Paint() 事后回填）。
 *      卡片是「先取数再渲」，金额与对比行同一拍渲染，不需要事后按 id 回填——
 *      留着 id 只会让「容器内查询」这条约束多一个后门。
 *   c. details 的展开态在重绘时保留（旧页每 3s 整体重建 innerHTML，点了就弹回）。
 *      首绘仍是**默认折叠**（无 open 属性），只是用户手动展开后不再被 20s 刷新打断。
 */

import { esc, num, pct, clsUp as cls, fmtAmount } from './format.js';
import { kName, kTsPlayable } from './research.js';

// ---------------------------------------------------------------- 中文化映射（旧页 2962-3004）
//
// 只搬本组真正用到的 9 张子表，值**逐字**照抄旧页（tests/test_audit.py 逐键比对旧页源码）。
// 不搬整张 M 是因为其余 30 余张（action/level/badge/orderStatus…）本组一个都不引用，
// 搬进来只会成为「没人用的第二份真相」。
export const M = {
  state: { INIT: '启动中', READY: '就绪', ARMED: '已签收可下单', TRIGGERED: '触发执行中', PAUSED: '已暂停', FROZEN: '急停冻结', ERROR: '异常', OFFLINE: '离线' },
  phase: { PRE_MARKET: '盘前', AUCTION: '集合竞价', OPENING: '盘中', LUNCH_FROZEN: '午休冻结', AFTERNOON: '午后', CLOSE_AUCTION: '尾盘竞价', CLOSED: '已收盘' },
  regime: { REPAIR: '修复期', ICE_POINT: '冰点期', DOWNTREND: '单边下跌', RANGE: '震荡' },
  // 本组用到的状态枚举（旧页 2989 sigStatus）
  sigStatus: { PENDING: '待触发', TRIGGERED: '已触发', EXECUTED: '已执行', BLOCKED: '被拦截', EXPIRED: '已过期', CANCELLED: '已取消', ACTIVE: '生效中', DONE: '已完成' },
  direction2: { STRONG: '判强', WEAK: '判弱', NEUTRAL: '中性' },
  channel: { A: '通道A', B: '通道B' },
  src2: { XIAOSHI_EVENTS: '小石事件（已退役）', XIAOSHI_ALERTS: '小石告警（已退役）', PATROL: '巡检', ENGINE: '引擎', QMT: 'QMT' },
  // ① 信号流水「类型」（signals[].kind，展示层中文化，2026-09-11 修）
  sigKind: {
    BUY: '买入', SELL: '卖出', STOP: '止损', ADD: '加仓', REDUCE_HALF: '减半', CLEAR: '清仓',
    T: '做T', TAKE_PROFIT: '止盈', WATCH: '观察', HOLD: '持有',
  },
  // ② 信号流水「来源」（signals[].source：引擎/执行器类枚举中文化；SW- / BP- 为业务单号原样保留）
  sigSource: {
    auto_backfill: '自动补录', exit_executor: '离场执行器', manual_protect: '手动保护单',
    auto_buy: '自动买入', executor: '执行器', engine: '引擎', qmt: 'QMT', manual: '手动',
    signal_watch: '择机监控', take_profit: '止盈模块', risk: '风控模块',
  },
};

/** 旧页 3005：枚举值 → 中文；缺值 → 破折号；表里没有 → 原样字符串（不编造翻译） */
export const cn = (map, v) => ((v === null || v === undefined) ? '—'
  : ((map && map[v] !== undefined) ? map[v] : String(v)));

/** 旧页 3007-3011：信号来源中文化。SW- / BP- 等业务单号原样保留（不编造），空值 → 破折号 */
export const cnSigSource = (v) => {
  if (v === null || v === undefined || v === '') return '—';
  const s = String(v);
  return M.sigSource[s] !== undefined ? M.sigSource[s] : s;
};

// ---------------------------------------------------------------- 行内工具（旧页 2920-2953）

/**
 * 旧页 2920-2927 kCode：可跳 K 线的**代码**列（纯代码占位，无名称）。
 * 与 kName 的区别：kName 显示名称、kCode 显示代码；两者都靠 data-open-kline 交给壳转发。
 * 不可跳的码输出 .k-nolink（显式不可点，不伪装可点 —— 旧页 2026-10-17 主人定）。
 */
export function kCode(tsCode, label, name) {
  const ts = (tsCode === null || tsCode === undefined) ? '' : String(tsCode).trim();
  if (!kTsPlayable(ts)) return '<span class="k-nolink">' + esc(label || '—') + '</span>';
  const nm = esc(name === undefined || name === null ? '' : name);
  return '<span class="k-name" data-open-kline="' + esc(ts) + '" data-open-name="' + nm + '"'
    + ' title="' + esc(ts) + '">' + esc(label || ts) + '</span>';
}

/** 旧页 2949-2953 table：空表 → 「（空）」；表头用 th；无 thead/tbody（与旧页 DOM 逐字一致） */
export function table(heads, rows) {
  if (!rows || !rows.length) return '<p class="muted">（空）</p>';
  return '<table><tr>' + heads.map((h) => '<th>' + esc(h) + '</th>').join('') + '</tr>'
    + rows.map((r) => '<tr>' + r.map((c) => '<td>' + c + '</td>').join('') + '</tr>').join('') + '</table>';
}

/** 旧页 2948 card：卡片标题 + 内容。类名换成本地 .it-audit-card（见文件头偏离 a） */
export const cardBlock = (title, html) => '<div class="it-audit-card"><h2>' + esc(title) + '</h2>' + html + '</div>';

// ---------------------------------------------------------------- 会话行（旧页 4707）

/**
 * 旧页 4707 内联一行：`盘中会话 <date> · <phase> · <state>（冻结:<reason>） ｜ 信号 n · 触发 n · 执行 n · 拦截 n`
 * 读 d.session（date / phase / state / frozen_reason）+ d.day_summary（四项计数）。
 * 计数缺省取 0（旧页 `?? 0` 同口径）；phase/state 走 M 映射，未收录原样显示。
 */
export function sessionText(d) {
  const s = (d && d.session) || {};
  return '盘中会话 ' + (s.date || '')
    + ' · ' + cn(M.phase, s.phase || '')
    + ' · ' + cn(M.state, s.state || '')
    + (s.frozen_reason ? '（冻结:' + s.frozen_reason + '）' : '')
    + ' ｜ 信号 ' + textTail(d);
}

/**
 * 会话行的 HTML 形态（旧页 4707 逐字节）。
 * 运行时卡片走 `sessionText` + `textContent`（约束②不拼 HTML），本函数留给对齐测试用：
 * 与旧页同一份数据渲出来的 div 必须一字不差（tests/audit_align.mjs）。
 */
export function sessionLine(d) {
  const s = (d && d.session) || {};
  return '<div class="sess-line">盘中会话 ' + esc(s.date || '')
    + ' · ' + esc(cn(M.phase, s.phase || ''))
    + ' · ' + esc(cn(M.state, s.state || ''))
    + (s.frozen_reason ? '（冻结:' + esc(s.frozen_reason) + '）' : '')
    + ' ｜ 信号 ' + textTail(d) + '</div>';
}

/** 会话行「信号/触发/执行/拦截」四段（sessionText 与 sessionLine 共用，防两处漂移） */
function textTail(d) {
  const sum = (d && d.day_summary) || {};
  return (sum.signal_count == null ? 0 : sum.signal_count)
    + ' · 触发 ' + (sum.triggered_count == null ? 0 : sum.triggered_count)
    + ' · 执行 ' + (sum.executed_count == null ? 0 : sum.executed_count)
    + ' · 拦截 ' + (sum.blocked_count == null ? 0 : sum.blocked_count);
}

// ---------------------------------------------------------------- 成交额对比（旧页 4694-4699）

/** 旧页 5170-5173 _br880AmountTxt：当前累计成交额读**广度 880** 的 today.amount（不是 intraday 自己的） */
export function br880AmountTxt(breadth) {
  const a = (breadth && breadth.today) ? breadth.today.amount : null;
  return a != null ? fmtAmount(a) : '<span class="muted">无数据</span>';
}

/**
 * 旧页 4694-4699「成交额对比」小卡（三行 KV：当前累计 / 昨日同时刻 / 预估今日全天）。
 *
 * **分时图已补**（旧页 5189-5216 `_turnoverChart`）：2026-10-25 主人要求「照搬 8800」，
 * 原先「一个 16 字的图不划算，图缓做」的审计建议作废（docs/AUDIT_20261025_intraday_gap.md §四 #5
 * 的性价比结论仍成立，但该轮的目标从「补字数」变成了「排版与图表反正一致」）。
 * 同理涨跌家数卡（旧页 4700）不在这里重画第二份 —— 它由 frontend.js 的 breadthHtml 渲好后传进来。
 *
 * 决策：卡片运行时走旧页三张小卡结构（`turnoverHtml(to, breadth, {chart, breadth})`）；
 * 不分时图、不传 opts 时仍输出与上一版逐字节相同的「单张小卡 + 汇总行」，
 * 因为 tests/audit_align.mjs 把 3 行 KV 与 2 条 src 锁为与旧页逐字节一致。
 *
 * `prevReal` 守卫是旧页 4691 的原话：**只有昨日 1m 真值**才显示同比，
 * 估算值不充真值（`prev_time_source === "qmt-1m"` 且金额非空）。
 */
export function turnoverCompareHtml(to, breadth) {
  return '<div class="it-to-card"><div class="it-to-h">成交额对比</div>' + turnoverKvHtml(to, breadth) + '</div>';
}

/** 三行 KV 本体（对比小卡 / 旧页 4694-4699 原样）：拆出来给「三张小卡」结构复用，行结构一字未改 */
export function turnoverKvHtml(to, breadth) {
  const t = to || {};
  const prevReal = (t.prev_time_source === 'qmt-1m') && t.prev_same_time_amount != null;
  const diff = (prevReal && t.prev_same_time_diff_pct != null)
    ? ' <span class="' + cls(t.prev_same_time_diff_pct) + '">' + pct(t.prev_same_time_diff_pct) + '</span>'
    : '';
  // 2026-10-28（N20261027-122604-34「成交额预估值出现严重失真」）：
  // 收盘后产线仍写 estimated=true + progress=1.0，预估值恒等于当日累计（1.63 万亿 == 1.63 万亿），
  // 卡上就变成「预估今日全天（估算）… 100%」——一个「预测」等于已发生的事实，读起来必是失真。
  // 收盘后不再出「预估」这一行：第一行直接叫「今日全天」，第二行叫「昨日全天」，两行都取 880005 同一源。
  const closed = isClosed(t);
  return '<div class="to-kv"><span class="to-lab">' + (closed ? '今日全天' : '当前累计')
    + ' <span class="src">880005</span></span>'
    + '<span class="to-val">' + br880AmountTxt(breadth) + '</span></div>'
    + '<div class="to-kv"><span class="to-lab">' + (closed ? '昨日全天' : '昨日同时刻')
    + '</span><span class="to-val">'
    + (prevReal ? fmtAmount(t.prev_same_time_amount) : '<span class="muted">无数据</span>') + diff + '</span></div>'
    + (closed ? '' : forecastRowHtml(t))
    + '<div class="src">' + esc(t.source || '') + '</div>';
}

/**
 * 收盘判定：`progress >= 1`（intraday_collector.trading_progress：15:00 后固定 1.0）。
 * 不按本机时钟判——历史快照（如 10-23 15:30）也是收盘态，口径只认载荷自报。
 */
export function isClosed(t) {
  const p = (t && t.progress != null) ? Number(t.progress) : null;
  return p != null && p >= 1;
}

/**
 * 「预估今日全天」行（旧页 4699 同一行；括号里的百分比换了口径）。
 *
 * 旧页（=V1）把 `progress` 当分母写进括号，可 `progress` 是**时间进度**（240 分钟走到哪）；
 * 而 profile 口径下预估用的是**量能进度** w(t)（近 20 日同刻累计占全天的中位数）。
 * 两个分母不一样时三行数字对不上：
 *   10/27 12:26 实景 = 当前累计 8774.6 亿 ／ 预估 1.52 万亿 ／ 括号 50%，
 *   读者按 50% 反推应得 1.75 万亿 → 差额 15%，怎么看都像失真。
 * 现在括号写**真正参与计算的那个分母**：profile → 由 total/forecast 反解 w；linear → 时间进度。
 * 反解只是公式 inversion（预估 = 累计 ÷ w），没有新口径、不重算预估本身。
 */
export function forecastRowHtml(t) {
  const o = t || {};
  const prog = (o.progress == null) ? null : Number(o.progress);
  const total = (o.total_amount == null) ? null : Number(o.total_amount);
  const fc = (o.forecast_full_day_amount == null) ? null : Number(o.forecast_full_day_amount);
  let tag = ' <span class="src">(估算)</span>';
  if (!o.estimated) tag = '';
  let denom = '';
  if (prog != null) {
    const w = (o.estimate_method === 'profile' && total > 0 && fc > 0) ? (total / fc) : null;
    if (w != null && w >= 0.005 && w <= 1.05) {
      denom = ' <span class="src" title="预估 = 今日累计 ÷ 量能进度；量能进度 = 近 20 日同刻累计占全天中位数">量能 '
        + num(w * 100, 0) + '%</span>';
    } else {
      denom = ' <span class="src" title="预估 = 今日累计 ÷ 时间进度">时间 '
        + num(prog * 100, 0) + '%</span>';
    }
  }
  return '<div class="to-kv"><span class="to-lab">预估今日全天' + tag + '</span><span class="to-val">'
    + fmtAmount(o.forecast_full_day_amount) + denom + '</span></div>';
}

/** 旧页 4702：成交额区的汇总来源行（口径说明 ｜ asof） */
export function turnoverFootHtml(to) {
  const t = to || {};
  // 2026-11-07（标注 N85）：只留时间戳，去掉口径说明（口径在 CONTRACT / 代码注释里）。
  return '<div class="src">' + esc(t.asof || '') + '</div>';
}

/**
 * 旧页 5189-5216 `_turnoverChart`：成交额分时（今日实线 / 昨日同时刻虚线）。
 *
 * 两条必须守住的口径（旧页踩过坑，注释原文照搬）：
 *   ① **昨日无真值就不画虚线**（v8 2026-10-08 主人定）：不拿估算值充真值；
 *   ② **X 轴固定 09:30-15:00 且按盘中时段过滤点**（2026-10-14 修「晚上实线只剩一小段」）：
 *      历史数据曾把隔夜/盘后点写进序列，窗外点全挤在两端、把盘中段挤出可视区。
 *      纯布局映射，不改数据口径。
 *
 * 与旧页的差异只有一处：旧页用 `num()`（全局）；这里从 format.js 导入同一函数（同口径）。
 */
export function turnoverChart(to) {
  const t = to || {};
  const inSession = (m) => (m >= 9 * 60 + 30 && m <= 11 * 60 + 30) || (m >= 13 * 60 && m <= 15 * 60);
  const pick = (arr) => (arr || []).filter((p) => {
    const m = hm2m(p.t);
    return m != null && inSession(m);
  });
  const st = pick(t.series_today);
  const sp = pick(t.series_prev);
  if (!st.length && !sp.length) return '<p class="muted">成交额分时：暂无数据（盘中累积后显示）</p>';
  const W = 240; const H = 56; const pad = 3; const m0 = 9 * 60 + 30; const m1 = 15 * 60;
  const X = (m) => pad + ((m - m0) / (m1 - m0)) * (W - 2 * pad);
  const allA = st.map((p) => p.a).concat(sp.map((p) => p.a));
  const maxY = Math.max(1, ...allA);
  const Y = (a) => H - pad - (a / maxY) * (H - 2 * pad);
  const path = (arr) => (arr || []).map((p, i) => {
    const m = hm2m(p.t);
    return m == null ? null : (i ? 'L' : 'M') + num(X(m), 1) + ',' + num(Y(p.a), 1);
  }).filter(Boolean).join(' ');
  const todayPath = path(st);
  const prevPath = sp.length ? path(sp) : '';
  return '<svg class="to-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">'
    + '<line x1="' + X(m0 + 120) + '" y1="' + pad + '" x2="' + X(m0 + 120) + '" y2="' + (H - pad) + '" class="to-mid"/>'
    + '<path class="to-prev" d="' + prevPath + '"/><path class="to-today" d="' + todayPath + '"/></svg>'
    + '<div class="src">纵轴峰值 ' + fmtAmount(maxY) + '（归一）'
    + (sp.length ? '' : '｜昨日分时不可得（无数据）') + '</div>';
}

/** 旧页 5188 `_hm2m`："HH:MM" → 当日分钟数；缺值/非法 → null */
function hm2m(t) {
  const p = String(t == null ? '' : t).split(':');
  const h = +p[0]; const m = +p[1];
  return (isNaN(h) || isNaN(m)) ? null : (h * 60 + m);
}

/**
 * 旧页 4694-4699 + 4702：成交额区整块。
 * - 不传 opts（单测/旧调用）→ 只出「成交额对比」小卡 + 汇总行（与上一版逐字节相同）；
 * - 传 opts.chart / opts.breadth（卡片运行时）→ 出旧页 v12 的三张小卡竖排 + 汇总行。
 * 两个子块由调用方先渲好再传进来（分时图在本模块、涨跌家数在 frontend.js），
 * 不为了拼一个字符串把 frontend.js 的渲染函数反向 import 回来。
 */
export function turnoverHtml(to, breadth, opts) {
  const o = opts || {};
  if (o.chart == null && o.breadth == null) return turnoverCompareHtml(to, breadth) + turnoverFootHtml(to);
  const cards = [];
  if (o.chart != null) {
    cards.push('<div class="it-to-card to-c-chart"><div class="it-to-h">成交额分时</div>'
      + o.chart + '</div>');
  }
  cards.push('<div class="it-to-card"><div class="it-to-h">成交额对比</div>' + turnoverKvHtml(to, breadth) + '</div>');
  if (o.breadth != null) {
    cards.push('<div class="it-to-card"><div class="it-to-h">涨跌家数 · 涨跌停（今日 ｜ 昨日）</div>'
      + o.breadth + '</div>');
  }
  return '<div class="to-cards">' + cards.join('') + '</div>' + turnoverFootHtml(to);
}

// ---------------------------------------------------------------- 审计明细（旧页 4718-4731）

/** 旧页 4719-4721：信号流水 8 列（ID/通道/时间/标的/类型/来源/触发/状态） */
export function signalRows(sig) {
  return (sig || []).map((x) => [
    esc(x.signal_id),
    cn(M.channel, x.channel),
    esc(x.created_at),
    kCode(x.ts_code, x.ts_code, x.name) + ' ' + kName(x.name, x.ts_code),
    esc(cn(M.sigKind, x.kind) + (x.batch_seq != null ? '·批次' + x.batch_seq : '')),
    esc(cnSigSource(x.source)),
    esc((x.trigger || {}).condition || ''),
    cn(M.sigStatus, x.status),
  ]);
}

/** 旧页 4722-4724：判强判弱快照 7 列 */
export function snapRows(snap) {
  return (snap || []).map((x) => [
    esc(x.snapshot_id),
    esc(x.ts),
    kCode(x.ts_code, x.ts_code, x.name) + ' ' + kName(x.name, x.ts_code),
    cn(M.direction2, x.direction),
    num(x.total_score),
    esc(x.verdict || ''),
    esc(x.made_by),
  ]);
}

/** 旧页 4725-4726：环境系数修正 5 列 */
export function envRows(env) {
  return (env || []).map((x) => [
    esc(x.ts),
    esc(x.source_slot),
    cn(M.regime, x.regime),
    num(x.env_coefficient),
    esc(x.basis || ''),
  ]);
}

/** 旧页 4727-4728：盘中事件 5 列（评分缺值 → 「—」，旧页 `x.score ?? "—"`） */
export function eventRows(ev) {
  return (ev || []).map((x) => [
    esc(x.event_id),
    esc(x.ts),
    cn(M.src2, x.source),
    esc(x.title),
    x.score == null ? '—' : x.score,
  ]);
}

/** 旧页 4729-4730：通道B·指令单 5 列 */
export function cmdRows(cmd) {
  return (cmd || []).map((x) => [
    esc(x.command_id),
    esc(x.raw_text),
    esc((x.translated || {}).condition || ''),
    cn(M.sigStatus, x.status),
    esc(x.created_at),
  ]);
}

export const SIGNAL_HEADS = ['ID', '通道', '时间', '标的', '类型', '来源', '触发', '状态'];
export const SNAP_HEADS = ['ID', '时间', '标的', '方向', '总分', '结论', '判定者'];
export const ENV_HEADS = ['时间', '来源档', '环境', '系数', '依据'];
export const EVENT_HEADS = ['ID', '时间', '来源', '标题', '评分'];
export const CMD_HEADS = ['ID', '原始指令', '翻译条件', '状态', '时间'];
export const SUMMARY_TEXT = '审计明细（信号流水 / 判强判弱 / 环境系数 / 盘中事件 / 通道B）';

/**
 * 旧页 4718-4731：审计明细折壳 + 5 张表。
 * **默认折叠**（无 open 属性）——旧页就是 `<details class="fold">`，照抄语义，不在卡里全量占屏。
 * 当前数据实态：signals 49 条、其余四段 0 条 → 四张表输出旧页同款「（空）」，表头照搬。
 */
export function auditHtml(d) {
  const sig = (d && d.signals) || [];
  const snap = (d && d.strength_snapshots) || [];
  const env = (d && d.env_adjustments) || [];
  const ev = (d && d.events) || [];
  const cmd = (d && d.commands_b) || [];
  return '<details class="fold"><summary>' + esc(SUMMARY_TEXT) + '</summary>'
    + cardBlock('信号流水', table(SIGNAL_HEADS, signalRows(sig)))
    + cardBlock('判强判弱快照', table(SNAP_HEADS, snapRows(snap)))
    + cardBlock('环境系数修正', table(ENV_HEADS, envRows(env)))
    + cardBlock('盘中事件', table(EVENT_HEADS, eventRows(ev)))
    + cardBlock('通道B·指令单', table(CMD_HEADS, cmdRows(cmd)))
    + '</details>';
}

// ---------------------------------------------------------------- 实例壳（约束①②）

/**
 * 本组没有需要自己拉的数据：intraday.json 由 frontend.js 主 load() 统一取（一块失败不拖垮整卡），
 * 这里只是一个「每实例」的持有者——与 research.js/sector.js 同款签名，便于编排与单测。
 * 状态位刻意保持最小：alive（卸载后不再写 DOM）、el（委托宿主）、body（重绘容器）、ac（监听句柄）。
 */
export function createState(cardEl, bodyEl) {
  return {
    el: cardEl || null,
    body: bodyEl || null,
    alive: true,
    open: false,            // details 上次的展开态（重绘后还原，见文件头偏离 c）
    html: null,             // 上次写进 DOM 的 HTML（签名缓存，见 paint 的防抖 ①）
    ac: new AbortController(),
  };
}

/**
 * 绑定：**没有事件监听要绑**——details 的展开/收起是浏览器原生行为（约束：原生优先，
 * 旧页同样没有为它写过一行 JS）。这里只做首绘，保证没有数据时也有「（空）」而不是空白块。
 */
export function bind(st) {
  if (!st) return;
  paint(st, null);
}

/**
 * 写内容。`d` 为 intraday.json 原样（null = 未就绪）。
 *
 * 两层防抖（这是本块与旧页最大的一处行为差异，目的是「不打断用户」）：
 *   ① 内容签名不变 → 整块不碰 DOM。旧页每 3s 重建整个 panel 的 innerHTML，
 *      49 行表的 DOM 每拍重解析，用户展开的 details 也每拍被弹回折叠。
 *      卡片 20s 一拍，签名相同就跳过 —— 展开态、滚动位置、文本选区全都不丢。
 *   ② 真变了才重建；重建前记住 details 的展开态、写完还原（首绘默认折叠，见文件头偏离 c）。
 */
export function paint(st, d) {
  const el = st && st.body;
  if (!el) return;
  const html = auditHtml(d);
  if (html === st.html) return;
  st.html = html;
  if (el.querySelector) {
    const det = el.querySelector('details.fold');
    if (det) st.open = !!det.open;
  }
  el.innerHTML = html;
  if (st.open && el.querySelector) {
    const det = el.querySelector('details.fold');
    if (det && !det.open) det.open = true;
  }
}

/** 解绑：abort 监听 + 置 alive=false（本组无定时器、无在飞请求） */
export function unbind(st) {
  if (!st) return;
  st.alive = false;
  try { st.ac.abort(); } catch (e) { /* 已 abort：忽略 */ }
}
