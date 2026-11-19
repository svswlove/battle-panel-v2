// ============================================================================
// 盘中卡 · v0.1.0（docs/task_R1_intraday.md）
// ----------------------------------------------------------------------------
// 行为基准 = 旧端 engine/web/index.html 的「盘中」Tab：
//   · 冰点监控（4634 _panicCardHtml / 4601 _panicChart / 4567 loadPanic）
//   · AMV 实时摘要（5286 _amvLine / 5300 _amvStamp / 5318 _amvHoldHtml / 5328 loadAmvLive）
//   · 广度 880（5162 _br880CardHtml / 5159 loadBreadth880）
//   · 板块轨迹摘要（433 _stSnaps 时段过滤 / 4906 _stVal 的 rel 口径 / 5080 四象外的榜）
//   · 盘中脉冲（6009 watchPaintLiveSector：当日 amv_pct 轨迹 + 末值）
//
// 契约（docs/CONTRACT.md §5 / docs/intraday_contract.md）：
//   1. 原生 ES module，零 import、零构建；只操作自己的 root。
//   2. 数据只走 ctx.api.get：四个既有 root 端点用绝对 `/api/...` 路径，
//      本插件自己的摘要端点用相对 `/summary`（shell 拼 /api/p/intraday）。
//   3. 颜色一律 themes.css 语义 token，本文件零裸 hex；零写盘、零子进程。
//   4. refreshEvery 20s：盘中块都是分钟级数据，页面不可见时 core 跳过 tick。
//   5. 板块轨迹**只消费服务端摘要**（/summary），不拉 2.7MB 全量
//      —— 全量渲染是旧端盘中 Tab 最重的一笔负载，本卡刻意不继承。
// ============================================================================

import { esc, num, pct, pp, clsIt as cls, code6, errText, fmtAmount, dShort } from './frontend/format.js';
import { injectCardCss } from './frontend/card.css.js';
import { injectResearchCss } from './frontend/research.css.js';
import { injectAuditCss } from './frontend/audit.css.js';
import * as sector from './frontend/sector.js?v=0.1.7';
import * as evo from './frontend/evo.js?v=0.1.7';
import * as research from './frontend/research.js';
import * as audit from './frontend/audit.js?v=0.1.7';
import { sessionText, turnoverHtml, turnoverChart } from './frontend/audit.js?v=0.1.7';
import { injectQuotesCss } from './frontend/quotes.css.js';
import { mqHtml, sparkCodes, SPARK_TTL_MS } from './frontend/quotes.js';
// AMV 预测实时源（2026-10-27）：旧设计就是按需刷新 —— `POST /api/amv_refresh` →
// core ops_run → `amv_live.run_once(publish=True)`，不新增常驻。本卡只负责「何时该 POST 一次」。
import * as amvrefresh from './frontend/amvrefresh.js';

const STYLE_ID = 'intraday-card-css';
const REFRESH_MS = 20000;
// 板块轨迹（/api/sector_track.json，当日 235 快照 × 90 板块 ≈ 2.7MB）单独给 60s 缓存：
// 采样本身就是 60s，按卡片 20s 节奏重拉纯浪费带宽与解析。旧页 loadSectorTrack 用的是 20s，
// 但那是整页 3s 重渲逼出来的；卡片没有这个约束，故按采样周期取整。
const TRACK_TTL_MS = 60000;
// 旧端 4565：状态 → 色档
const PANIC_CLS = { '极冰': 'ice', '冰点': 'cold', '正常': 'normal', '乐观': 'warm', '过热': 'hot' };
// 旧端 4566 口径（**禁改**）：两线同轴 0–100，方向统一为「低值 = 恐慌」
const ICE_THRESHOLD = 10;
// 冰点档上界（10–25 冰点、<10 极冰，与后端 state_of 同阈值）；当日视图的「距冰点」用它。
const ICE_COLD_MAX = 25;
// 冰点图视图（2026-11-18，标注 N20261118-184104-9「点击切换为当日的冰点情况」）：
//   trend = 近 60 日趋势（原样）；today = 当日快照。数据本身是日频（panic_breadth.json 一日一行），
//   当日没有分时恐慌序列落盘 —— 所以这里给的是**当日快照**（四项读数 + 分位 + 距离/最近冰点日），
//   不画假分时曲线。点击图区切换，选择记在 localStorage。
const PANIC_VIEW_KEY = 'it_panic_view';
let PANIC_VIEW = 'trend';
try { if (localStorage.getItem(PANIC_VIEW_KEY) === 'today') PANIC_VIEW = 'today'; } catch (e) { /* 无存储按趋势 */ }
// YYYYMMDD → YYYY-MM-DD（当日视图用；bindPanicCursor 里那份是函数内局部，不复用）
const fmtDate8 = (s) => { const t = String(s || ''); return t.length === 8 ? t.slice(0, 4) + '-' + t.slice(4, 6) + '-' + t.slice(6, 8) : t; };
//: 恐慌图展示的交易日窗口（2026-10-30 主人：全A 叠上后 120 根太密，改 60 根试试）。
//: 数据源仍给 120 根，只截尾巴——回看窗口只改这一个数。
const PANIC_DAYS = 60;
// 会话行未就绪文案（intraday.json 未到时用；不拿 0 冒充计数，旧页此时整块尚未渲染）
const SESS_LOADING = '盘中会话 加载中…（/api/intraday.json）';
const PULSE_TOP = 3;          // 盘中脉冲默认取领先 3 + 落后 3
const PULSE_MAX = 8;          // 钉住上限（防一次拉太多码）

// 自动取数窗口（2026-10-28 N20261027-234539-35）：
//   09:30 前（盘前）/ 16:10 后（收盘自动取数已完成）/ 周末，一律不自动取数——
//   数据已定格，20s 一拍只会反复拉 2.88MB 全量轨迹 + 重读 10MB 轨迹文件，
//   也就是主人感受到的「每次打开都要等一会儿」。首挂载仍取一次；要最新数据点右上角「刷新」。
const AUTO_FROM_MIN = 9 * 60 + 30;    // 09:30
const AUTO_TO_MIN = 16 * 60 + 10;     // 16:10（收盘取数之后）

/** 现在该不该自动取数（交易日 09:30–16:10）。now 可注入，便于单测。 */
export function inAutoRefreshWindow(now) {
  const d = (now instanceof Date) ? now : new Date();
  const wd = d.getDay();
  if (wd === 0 || wd === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return m >= AUTO_FROM_MIN && m < AUTO_TO_MIN;
}

/** 收盘定格（用于卡头标注）：16:10 之后且不是周末。 */
export function isCloseFrozen(now) {
  const d = (now instanceof Date) ? now : new Date();
  const wd = d.getDay();
  if (wd === 0 || wd === 6) return false;
  return (d.getHours() * 60 + d.getMinutes()) >= AUTO_TO_MIN;
}

const CSS = [
  '.it-card{display:flex;flex-direction:column;gap:12px;color:var(--fg);min-width:0}',
  '.it-top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}',
  '.it-title{font-size:1rem;font-weight:600;color:var(--fg-strong)}',
  '.it-badge{display:inline-block;padding:1px 9px;border-radius:var(--radius-pill);background:var(--card2);color:var(--dim);font-size:0.72rem;white-space:nowrap}',
  '.it-badge.ok{background:var(--down-bg);color:var(--down-ink,var(--down))}',
  '.it-badge.warn{background:var(--warn-bg);color:var(--warn-ink,var(--warn))}',
  '.it-badge.danger{background:var(--danger-bg);color:var(--danger-ink,var(--danger))}',
  '.it-sub{color:var(--dim);font-size:0.72rem;margin-left:auto;font-variant-numeric:tabular-nums}',
  '.it-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer;white-space:nowrap}',
  '.it-btn:hover{background:var(--card2)}',
  '.it-btn:disabled{opacity:.45;cursor:not-allowed}',
  '.it-err{padding:6px 9px;border-radius:var(--radius-sm);background:var(--danger-bg);color:var(--danger-ink,var(--danger));font-size:0.75rem;line-height:1.5;word-break:break-word}',
  '.it-warn{padding:6px 9px;border-radius:var(--radius-sm);background:var(--warn-bg);color:var(--warn-ink,var(--warn));font-size:0.75rem;line-height:1.5}',
  '.it-sec{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-lg);background:var(--card);padding:12px 14px;box-shadow:var(--shadow-card),var(--card-hairline);min-width:0}',
  '.it-sec-hd{display:flex;align-items:baseline;gap:8px;font-size:0.72rem;color:var(--dim);margin-bottom:8px;flex-wrap:wrap}',
  '.it-sec-hd b{color:var(--accent);font-size:0.9375rem;font-weight:700}',
  '.it-sec-n{display:none}',
  '.it-muted{color:var(--dim);font-size:0.74rem;line-height:1.55}',
  '.it-src{font-size:0.66rem;color:var(--dim);line-height:1.6}',
  '.it-up{color:var(--up)}', '.it-down{color:var(--down)}',
  '.it-n{font-variant-numeric:tabular-nums}',
  // 卡内分段标题（同卡两段内容之间）：持仓卡里分「AMV 摘要 / 持仓表」，轨迹卡里分「摘要 / 逐码脉冲」
  '.it-split-hd{margin:10px 0 6px;padding-top:8px;border-top:1px solid var(--line-soft);color:var(--dim);font-size:0.72rem}',
  // ---- 顶部两栏（旧页 1547-1556 `.top-split`）：左窄=两市成交额（3 小卡竖排填满）/ 右宽=板块轮动
  //      列宽逐字照旧页：minmax(190px,0.6fr) 1.7fr；1476 宽下实测 389.7 | 1104.3
  '.it-split{display:grid;grid-template-columns:minmax(190px,0.6fr) 1.7fr;gap:12px;align-items:stretch}',
  '.it-split > .it-sec{display:flex;flex-direction:column;min-width:0}',
  // ---- 冰点卡（旧页 1568-1613 `.panic-wrap`）：三段分栏 132 | 分项竖排 | 图
  //      颜色仍走 token（旧页那四个 ice/cold/warm 裸 hex 属主题化前遗留，不进卡片）
  '.it-panic{display:grid;grid-template-columns:132px minmax(230px,0.85fr) minmax(280px,1.5fr);gap:12px;align-items:stretch}',
  '.it-panic-hero{display:flex;flex-direction:column;gap:3px;justify-content:center;min-width:0}',
  '.it-panic-lab{font-size:0.72rem;color:var(--dim);letter-spacing:.04em}',
  '.it-panic-val{font-size:2.65rem;font-weight:700;line-height:1.02;font-variant-numeric:tabular-nums}',
  '.it-panic-val.ice{color:var(--danger)}',
  '.it-panic-val.cold{color:var(--warn)}',
  '.it-panic-val.normal{color:var(--fg-strong)}',
  '.it-panic-val.warm{color:var(--accent)}',
  '.it-panic-val.hot{color:var(--up)}',
  '.it-pb-state{display:inline-block;align-self:flex-start;padding:1px 9px;border-radius:var(--radius-pill);font-size:0.8125rem;font-weight:600;border:1px solid transparent}',
  '.it-pb-state.ice{color:var(--danger-ink,var(--danger));border-color:var(--danger);background:var(--danger-bg)}',
  '.it-pb-state.cold{color:var(--warn-ink,var(--warn));border-color:var(--warn);background:var(--warn-bg)}',
  '.it-pb-state.normal{color:var(--dim);border-color:var(--line);background:var(--line-soft)}',
  '.it-pb-state.warm{color:var(--accent-ink,var(--accent));border-color:var(--accent);background:var(--accent-bg)}',
  '.it-pb-state.hot{color:var(--up-ink,var(--up));border-color:var(--up);background:var(--up-bg)}',
  // ---- 冰点卡分项（旧页 1584-1598 `.panic-items` / `.panic-item`）：竖排、左侧按分位能量柱
  '.it-pitems{display:flex;flex-direction:column;justify-content:center;gap:6px;min-width:0}',
  '.it-pitem{position:relative;display:grid;grid-template-columns:minmax(66px,1fr) auto auto;gap:8px;align-items:baseline;background:var(--card2);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:5px 9px;min-width:0}',
  '.it-pitem::before{content:"";position:absolute;left:0;top:0;bottom:0;width:var(--pq,0%);background:var(--accent);opacity:.16;border-radius:inherit;pointer-events:none}',
  '.it-pitem > *{position:relative;z-index:1}',
  '.it-pitem-k{color:var(--dim);font-size:0.78rem;white-space:nowrap}',
  '.it-pitem-c{color:var(--dim);font-size:0.7rem;margin-left:4px}',
  '.it-pitem-v{font-size:1rem;font-weight:700;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}',
  '.it-pitem-q{font-size:0.72rem;color:var(--dim);text-align:right;white-space:nowrap;min-width:66px;font-variant-numeric:tabular-nums}',
  '.it-pchart-box{display:flex;flex-direction:column;justify-content:center;min-width:0;cursor:pointer}',
  // ---- 冰点卡·当日快照视图（2026-11-18 标注 N20261118-184104-9，点击图区切换）
  '.it-pday{display:flex;flex-direction:column;gap:6px;background:var(--card2);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:8px 10px;min-width:0}',
  // 当日走势空态（2026-11-18）：当日分时未落盘时如实说明，不画假曲线
  '.it-pday-empty{font-size:0.78rem;color:var(--dim);line-height:1.65}',
  '.it-pday-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}',
  '.it-pday-v{font-size:1.6rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.05}',
  '.it-pday-d{font-size:0.75rem;color:var(--dim)}',
  '.it-pday-rows{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px 12px}',
  '.it-pday-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:baseline;font-size:0.78rem;min-width:0}',
  '.it-pday-row .k{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.it-pday-row .v{font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}',
  '.it-pday-row .q{color:var(--dim);font-size:0.7rem;white-space:nowrap}',
  '.it-pday-foot{font-size:0.7rem;color:var(--dim);line-height:1.5}',
  '.it-pchart-legend{display:flex;flex-wrap:wrap;gap:10px;color:var(--dim);font-size:0.7rem;margin-bottom:2px}',
  '.it-pfoot{display:flex;gap:4px 14px;flex-wrap:wrap;font-size:0.7rem;color:var(--dim);margin-top:6px}',
  '.it-pfoot b{color:var(--fg);font-weight:600}',
  '.it-chart{width:100%;height:auto;display:block}',
  // 恐慌曲线：旧页 1608 `.panic-chart{width:100%;height:auto}`——旧页不限宽（图独占 843 宽一栏，
  // 宽屏下自然受限）；原先 v2 用 max-width:660 是因为当时分项横排把图挤窄了，三栏修正后不再需要
  '.it-pchart{width:100%;height:auto;display:block}',
  '.it-lg{display:inline-flex;align-items:center;gap:4px}',
  '.it-lg i{width:14px;height:0;border-top-width:2px;border-top-style:solid;display:inline-block}',
  '.it-lg i.lg-panic{border-top-color:var(--dim)}',
  '.it-lg i.lg-alla{border-top:0;width:10px;height:10px;border-radius:2px;opacity:.55;display:inline-block}',
  '.it-pc-wrap{position:relative}',
  '.it-pc-tip{position:absolute;top:4px;pointer-events:none;display:flex;flex-direction:column;gap:1px;padding:5px 8px;border-radius:6px;background:var(--card);border:1px solid var(--line);font-size:11px;line-height:1.45;color:var(--fg);white-space:nowrap;z-index:6;transform:translateX(-50%)}',
  '.it-pc-tip[hidden]{display:none}',
  '.it-pc-tip b{font-weight:600}',
  '.it-lg i.lg-ldc{border-top-color:var(--accent)}',
  // 2026-10-31（主人标注 63）：涨停家数分位线用 --warn，与跌停线（--accent）成对但不撞色
  '.it-lg i.lg-luc{border-top-color:var(--warn);opacity:.85}',
  '.it-lg i.lg-ice{height:6px;width:6px;border:0;border-radius:50%;background:var(--danger)}',
  // ---- 通用表（旧页 969-978：table 0.8125rem / --d-cellpad 6px 8px；2026-10-25 排版对齐时按要求补）
  '.it-tbl{width:100%;max-width:520px;border-collapse:collapse;font-size:0.8125rem;font-variant-numeric:tabular-nums}',
  '.it-tbl th{text-align:left;font-weight:400;color:var(--dim);font-size:0.8125rem;padding:6px 8px;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.it-tbl td{padding:6px 8px;border-bottom:1px solid var(--line-soft)}',
  '.it-tbl th.n,.it-tbl td.n{text-align:right;white-space:nowrap}',
  '.it-tbl-b{width:auto;min-width:240px;max-width:380px}',
  '.it-tbl-b th:first-child,.it-tbl-b td:first-child{padding-right:28px}',
  '.it-tbl-b td.n,.it-tbl-b th.n{width:88px}',
  '.it-tbl tr.it-row{cursor:pointer}',
  '.it-tbl tr.it-row:hover td{background:var(--card2)}',
  '.it-tbl tr.it-on td{background:var(--accent-bg)}',
  '.it-code{color:var(--dim);font-size:0.64rem;margin-left:4px}',
  // ---- AMV
  '.it-amv{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:10px}',
  '.it-amv-main{background:var(--card2);border-radius:var(--radius-md);padding:7px 10px;min-width:0}',
  '.it-amv-v{font-size:1.35rem;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.25}',
  '.it-amv-l{font-size:0.68rem;color:var(--dim)}',
  '.it-idx{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:4px 10px;margin-top:6px}',
  '.it-idx-i{display:flex;gap:6px;align-items:baseline;font-size:0.72rem;min-width:0}',
  '.it-idx-n{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fg)}',
  '.it-idx-v{font-variant-numeric:tabular-nums;flex:0 0 auto}',
  '.it-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,240px));gap:8px 14px;margin-top:8px;justify-content:start}',
  '.it-list{display:flex;flex-direction:column;gap:2px;font-size:0.72rem;max-width:240px}',
  '.it-li{display:flex;gap:6px;align-items:baseline}',
  '.it-li .t{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  '.it-li .v{flex:0 0 auto;font-variant-numeric:tabular-nums;margin-left:auto}',
  // ---- 轨迹 / 脉冲
  '.it-track{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:10px}',
  '.it-kv{display:flex;gap:10px;flex-wrap:wrap;font-size:0.72rem;color:var(--dim)}',
  '.it-kv b{color:var(--fg);font-weight:500;font-variant-numeric:tabular-nums}',
  '.it-pulse{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,220px));gap:6px 10px;justify-content:start}',
  '.it-pc{background:var(--card2);border-radius:var(--radius-sm);padding:5px 7px;min-width:0}',
  '.it-pc-hd{display:flex;gap:5px;align-items:baseline;font-size:0.7rem;min-width:0}',
  '.it-pc-n{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  '.it-pc-v{font-variant-numeric:tabular-nums;flex:0 0 auto}',
  '.it-pc-sub{font-size:0.62rem;color:var(--dim);font-variant-numeric:tabular-nums}',
  '@media (max-width:1100px){.it-split{grid-template-columns:minmax(0,1fr)}.it-panic{grid-template-columns:110px minmax(200px,1fr)}.it-pchart-box{grid-column:1 / -1}}',
  '@media (max-width:760px){.it-panic{grid-template-columns:minmax(0,1fr)}.it-amv{grid-template-columns:minmax(0,1fr)}.it-track{grid-template-columns:minmax(0,1fr)}}',
].join('\n');

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

// 工具（esc/num/pct/pp/cls/code6/errText/fmtAmount/dShort）统一在 frontend/format.js，
// 与旧页 2857-2943 / 4622 同口径，本文件不再各写一份。

// ---------------------------------------------------------------- 冰点卡图表（旧端 4601 _panicChart）
//
// 口径（2026-10-23 主人定，**禁改**）：两条线同轴 0–100、方向统一「低值 = 恐慌」。
// 跌停家数分位原值是「高值恐慌」，画线取 100 − q 反向；不反向的后果是双恐慌共振日
// 被画成一条在底一条在顶的假背离（实测 1238 个交易日里 28.8% 会误读）。
// 禁加均线/平滑、禁加温度或新情绪指数、禁加仓位/抄底建议。
function panicChart(p, allA) {
  const pts = (p.series || []).filter((x) => x && x.value != null).slice(-PANIC_DAYS);
  if (pts.length < 2) return '<p class="it-muted">趋势序列不足（需 ≥2 个交易日）</p>';
  const W = 600; const H = 150; const L = 40; const R = 26; const T = 10; const B = 20;  // 与 PC_GEO 同源
  const iw = W - L - R; const ih = H - T - B; const n = pts.length;
  const X = (i) => L + iw * i / (n - 1);
  const Y = (v) => T + ih * (1 - Math.max(0, Math.min(100, Number(v))) / 100);
  const LDC = (x) => (x.q_limit_down_count == null ? null : 100 - x.q_limit_down_count);
  // 2026-10-31（主人标注 63「没有涨停数据」）：涨停家数分位线与读数一并加过；
  //   2026-11-01（主人标注 66「涨停的折线不用画进来，太乱了」）：**整条折线移除**
  //   （连同只被它用到的 lineSeg），只在浮层读数里保留「涨停/跌停家数」文字对比。
  const line = (valFn) => pts.map((pt, i) => {
    const v = valFn(pt);
    return (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + (v == null ? Y(0) : Y(v)).toFixed(1);
  }).join(' ');
  const tick = (v) => '<text x="' + (L - 5) + '" y="' + (Y(v) + 3.5).toFixed(1)
    + '" text-anchor="end" font-size="9" fill="var(--dim)">' + v + '</text>';
  const gridY = (v) => '<line x1="' + L + '" y1="' + Y(v).toFixed(1) + '" x2="' + (W - R) + '" y2="'
    + Y(v).toFixed(1) + '" stroke="var(--dim)" stroke-opacity="0.16" stroke-width="1"/>';
  const ref = (v) => '<line x1="' + L + '" y1="' + Y(v).toFixed(1) + '" x2="' + (W - R) + '" y2="'
    + Y(v).toFixed(1) + '" stroke="var(--dim)" stroke-opacity="0.5" stroke-dasharray="3 3" stroke-width="1"/>'
    + '<text x="' + (W - R + 5) + '" y="' + (Y(v) + 3.5).toFixed(1) + '" font-size="9" fill="var(--dim)">' + v + '</text>';
  const last = pts[pts.length - 1];
  const lastLdc = LDC(last);
  const dateLab = '<text x="' + L + '" y="' + (H - 5) + '" font-size="9" fill="var(--dim)">'
    + esc(dShort(pts[0].date)) + '</text>'
    + '<text x="' + (W - R) + '" y="' + (H - 5) + '" text-anchor="end" font-size="9" fill="var(--dim)">'
    + esc(dShort(last.date)) + '</text>';
  const iceDots = pts.map((pt, i) => (pt.value < ICE_THRESHOLD)
    ? '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(pt.value).toFixed(1)
      + '" r="2.2" fill="var(--danger)"><title>' + esc(pt.date) + ' 恐慌广度 ' + num(pt.value, 2)
      + '（极冰）</title></circle>'
    : '').join('');
  // 2026-10-30（主人标注 58）：恐慌图背后叠全A 880008 日涨跌——浅色折线 + 线下的淡色面积。
  //   轴不同量纲：全A 涨跌幅（±M%）走单独的映射 Y2，只占图高、不碰 0–100 主轴刻度；
  //   缺失日断开不连线（不插值、不拿 0 填）。数据口 = /api/p/intraday/index-series（eltdx 日 K）。
  const amap = Object.create(null);
  for (const row of ((allA && allA.rows) || [])) {
    if (row && row.date && row.pct != null) amap[String(row.date)] = Number(row.pct);
  }
  const pctsRaw = pts.map((pt) => (amap[String(pt.date)] == null ? null : amap[String(pt.date)]));
  // 2026-11-19（主人抓的视觉 bug）：面积必须画**累计涨跌**（相对窗口首日），不能画逐日涨跌幅——
  //   11-16 −2.33%、11-17 −1.52%、11-18 −1.08%，天天在跌但跌幅在收窄，逐日线是往上爬的，
  //   看上去像在涨。累计后形状才是「这几天一共跌了多少」。缺日不进累计（不拿 0 填）。
  let cumPct = 0;
  const pcts = pctsRaw.map((p) => {
    if (p == null) return null;
    cumPct = (1 + cumPct / 100) * (1 + p / 100) * 100 - 100;
    return Math.round(cumPct * 100) / 100;
  });
  const vals = pcts.filter((v) => v != null);
  let allALayer = '';
  if (vals.length >= 2) {
    const M = Math.max(2, Math.ceil(Math.max.apply(null, vals.map((v) => Math.abs(v)))));
    const Y2 = (pct) => T + ih * (1 - (pct + M) / (2 * M));
    const idxs = [];
    pcts.forEach((v, i) => { if (v != null) idxs.push(i); });
    const i0 = idxs[0]; const i1 = idxs[idxs.length - 1];
    const baseY = T + ih;
    const area = pcts.map((pct, i) => (pct == null ? '' : (i === i0 ? 'M' : 'L') + X(i).toFixed(1) + ' ' + Y2(pct).toFixed(1))).join(' ')
      + ' L ' + X(i1).toFixed(1) + ' ' + baseY.toFixed(1) + ' L ' + X(i0).toFixed(1) + ' ' + baseY.toFixed(1) + ' Z';
    const lastPct = vals[vals.length - 1];
    const tone = lastPct >= 0 ? 'var(--up)' : 'var(--down)';
    // 2026-11-05（标注 70）：主人不要那条全A 虚线（看不清涨跌），只留线下面积；
    //   面积改成竖向渐变（顶部浓→底部透明），颜色按最后一日涨跌取 up/down，一眼能看出红绿。
    const gid = 'it-allA-grad';
    allALayer = '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0" stop-color="' + tone + '" stop-opacity="0.34"/>'
      + '<stop offset="0.55" stop-color="' + tone + '" stop-opacity="0.14"/>'
      + '<stop offset="1" stop-color="' + tone + '" stop-opacity="0.02"/>'
      + '</linearGradient></defs>'
      + '<path d="' + area + '" fill="url(#' + gid + ')" stroke="none"/>'
      + '<line x1="' + L + '" y1="' + Y2(0).toFixed(1) + '" x2="' + (W - R) + '" y2="' + Y2(0).toFixed(1)
      + '" stroke="var(--fg)" stroke-opacity="0.12" stroke-width="1" stroke-dasharray="2 4"/>';
  }
  return '<div class="it-pc-wrap"><svg class="it-pchart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="恐慌广度与跌停家数分位（已反向）近 '
    + n + ' 个交易日趋势（单轴 0–100，低值=恐慌），叠全A 880008 日涨跌幅"'
    + '>'
    + gridY(0) + gridY(50) + gridY(100) + tick(0) + tick(50) + tick(100)
    + ref(10) + ref(90) + dateLab
    + allALayer
    + '<path d="' + line(LDC) + '" fill="none" stroke="var(--accent)" stroke-opacity="0.9" stroke-width="1.2"/>'
    + '<path d="' + line((x) => x.value) + '" fill="none" stroke="var(--dim)" stroke-width="1.7"/>'
    + iceDots
    + (lastLdc != null
      ? '<circle cx="' + X(n - 1).toFixed(1) + '" cy="' + Y(lastLdc).toFixed(1)
        + '" r="2.4" fill="var(--accent)"><title>' + esc(last.date) + ' 跌停家数分位 '
        + num(last.q_limit_down_count, 1) + '（反向画为 ' + num(lastLdc, 1) + '）</title></circle>'
      : '')
    + '<circle cx="' + X(n - 1).toFixed(1) + '" cy="' + Y(last.value).toFixed(1)
    + '" r="2.6" fill="var(--dim)"><title>' + esc(last.date) + ' 恐慌广度 ' + num(last.value, 2)
    + '</title></circle>'
    // 光标虚线 + 三个点位（默认隐藏，mousemove 时显示，见 bindPanicCursor）
    + '<g class="it-pc-cur" style="display:none">'
    + '<line x1="0" y1="' + T + '" x2="0" y2="' + (T + ih) + '" stroke="var(--fg)" stroke-opacity="0.5" stroke-width="1" stroke-dasharray="3 3"/>'
    + '<circle r="3.2" fill="var(--dim)" stroke="var(--card)" stroke-width="1"/>'
    + '<circle r="2.8" fill="var(--accent)"/>'
    + '<circle r="3" fill="var(--up)"/>'
    + '</g>'
    + '</svg><div class="it-pc-tip" hidden></div></div>';
}

// 视图切换本体（2026-11-18）：只换图区那块 DOM，不重绘整卡（不依赖 mount 内部的 render）。
function togglePanicView(host, p, allA, br) {
  PANIC_VIEW = (PANIC_VIEW === 'today') ? 'trend' : 'today';
  try { localStorage.setItem(PANIC_VIEW_KEY, PANIC_VIEW); } catch (e) { /* 存不住也不影响本次切换 */ }
  try {
    const box = host && host.querySelector('.it-pchart-box');
    if (!box) return;
    const holder = document.createElement('div');
    holder.innerHTML = (p && p.ok) ? panicBoxHtml(p, allA) : '';
    const next = holder.firstElementChild;
    if (next) { box.replaceWith(next); bindPanicCursor(host, p, allA, br); }
  } catch (e) {
    // 切视图失败不拖垮卡片：原图保持不动，错误只上控制台（不静默）
    try { console.error('[intraday] panic view toggle failed', e); } catch (_) { /* 无 console */ }
  }
}

// 恐慌图光标（2026-10-30 主人：加一条光标虚线，随光标在时间轴上显示日期与涨幅）。
//   mousemove → 吸附到最近的交易日，竖虚线 + 三个点位（恐慌/跌停分位/全A）+ 左上浮层读数。
//   几何与 panicChart 一致（viewBox 600×150，L40 R26 T10 B20）；内容重绘后靠 dataset 标记重绑。
//   2026-10-30 主人收口：卡片只留三项——全A 涨幅、涨跌停家数对比、上涨下跌家数对比。
function bindPanicCursor(host, p, allA, br) {
  // 视图切换（2026-11-18）：点图区在「近 60 日趋势」↔「当日冰点情况」之间切。
  //   绑在 .it-pchart-box 上（趋势/当日两种形态都有这个壳）；新节点用 dataset 去重，旧节点被替换后自然消失。
  const pBox = host && host.querySelector('.it-pchart-box');
  if (pBox && pBox.dataset.pvBound !== '1') {
    pBox.dataset.pvBound = '1';
    pBox.addEventListener('click', function () { togglePanicView(host, p, allA, br); });
  }
  const box = host && host.querySelector('.it-pc-wrap');
  const svg = box && box.querySelector('svg.it-pchart');
  const tip = box && box.querySelector('.it-pc-tip');
  const g = svg && svg.querySelector('.it-pc-cur');
  if (!svg || !tip || !g || svg.dataset.cur === '1') return;
  const pts = ((p && p.series) || []).filter((x) => x && x.value != null).slice(-PANIC_DAYS);
  if (pts.length < 2) return;
  svg.dataset.cur = '1';
  const n = pts.length;
  const W = 600; const H = 150; const L = 40; const R = 26; const T = 10; const B = 20;
  const iw = W - L - R; const ih = H - T - B;
  const X = (i) => L + iw * i / (n - 1);
  const Y = (v) => T + ih * (1 - Math.max(0, Math.min(100, Number(v))) / 100);
  const amap = Object.create(null);
  for (const row of ((allA && allA.rows) || [])) {
    if (row && row.date) amap[String(row.date)] = row;
  }
  const pcts = pts.map((pt) => (amap[String(pt.date)] && amap[String(pt.date)].pct != null
    ? Number(amap[String(pt.date)].pct) : null)).filter((v) => v != null);
  let Y2 = null;
  if (pcts.length >= 2) {
    const M = Math.max(2, Math.ceil(Math.max.apply(null, pcts.map((v) => Math.abs(v)))));
    Y2 = (v) => T + ih * (1 - (Number(v) + M) / (2 * M));
  }
  const vline = g.querySelector('line');
  const dots = g.querySelectorAll('circle');
  const d8 = (s) => { const t = String(s || ''); return t.length === 8 ? t.slice(0, 4) + '-' + t.slice(4, 6) + '-' + t.slice(6, 8) : t; };
  const move = (ev) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const vbX = (ev.clientX - rect.left) * (W / rect.width);
    let i = Math.round((vbX - L) / iw * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const pt = pts[i];
    const row = amap[String(pt.date)] || null;
    const x = X(i);
    const xf = x.toFixed(1);
    g.style.display = '';
    vline.setAttribute('x1', xf); vline.setAttribute('x2', xf);
    if (dots[0]) { dots[0].setAttribute('cx', xf); dots[0].setAttribute('cy', Y(pt.value).toFixed(1)); }
    if (dots[1]) {
      if (pt.q_limit_down_count == null) { dots[1].style.display = 'none'; }
      else {
        dots[1].style.display = '';
        dots[1].setAttribute('cx', xf); dots[1].setAttribute('cy', Y(100 - pt.q_limit_down_count).toFixed(1));
      }
    }
    if (dots[2]) {
      if (!Y2 || !row || row.pct == null) { dots[2].style.display = 'none'; }
      else {
        dots[2].style.display = '';
        dots[2].setAttribute('cx', xf); dots[2].setAttribute('cy', Y2(row.pct).toFixed(1));
      }
    }
    const pctTxt = (row && row.pct != null)
      ? '<span>全A 880008 <b style="color:var(' + (row.pct >= 0 ? '--up' : '--down') + ')">'
        + (row.pct >= 0 ? '+' : '') + num(row.pct, 2) + '%</b></span>'
      : '<span style="opacity:.6">全A 无当日数据</span>';
    // 读数卡只留三项（主人 2026-10-30）：全A 涨幅 / 涨跌停家数对比 / 上涨下跌家数对比。
    //   涨停家数：优先序列行自带的 limit_up（2026-10-31 起历史行都有），退当日快照
    //   （/api/breadth_880.json 的 today）；两边都没有才显示 —。
    const upN = pt.up == null ? null : Number(pt.up);
    const downN = pt.down == null ? null : Number(pt.down);
    const ldN = pt.limit_down == null ? null : Number(pt.limit_down);
    const luN = pt.limit_up != null ? Number(pt.limit_up)
      : ((br && br.today && String(br.today.date) === String(pt.date) && br.today.limit_up != null)
        ? Number(br.today.limit_up) : null);
    // 跌停 / 下跌取绿（主人 2026-10-30）；无数据的「—」不染色
    const green = (v) => (v == null ? '<b>—</b>' : '<b style="color:var(--down)">' + num(v, 0) + '</b>');
    tip.innerHTML = '<b>' + esc(d8(pt.date)) + '</b>'
      + pctTxt
      + '<span>涨停 <b>' + (luN == null ? '—' : num(luN, 0)) + '</b> / 跌停 ' + green(ldN) + '</span>'
      + '<span>上涨 <b>' + (upN == null ? '—' : num(upN, 0)) + '</b> / 下跌 ' + green(downN) + '</span>';
    tip.hidden = false;
    const px = rect.width * (x / W);
    tip.style.left = Math.max(122, Math.min(rect.width - 122, px)) + 'px';
  };
  svg.addEventListener('mousemove', move);
  svg.addEventListener('mouseleave', () => { g.style.display = 'none'; tip.hidden = true; });
}

// ---------------------------------------------------------------- 冰点卡：当前视图（趋势 / 当日快照）
//
// 图区外壳统一由本函数出（legend + 图/当日卡），点击切视图（2026-11-18 主人标注 N20261118-184104-9）：
//   ① 两块内容同源（/api/panic），不新增接口、不改任何分位口径；
//   ② 选择记忆在 localStorage（it_panic_view），刷新不丢；
//   ③ 当日视图只展示**已落盘的事实**：没有分时恐慌序列就不画分时（见字段注释）。
function panicTodayChart(p) {
  const pts = ((p.intraday_series || []).filter((x) => x && x.t)).slice()
    .sort((a, b) => String(a.t).localeCompare(String(b.t)));
  if (!pts.length) {
    return '<div class="it-pday"><div class="it-pday-empty">'
      + '当日走势暂无记录：盘中每分钟落一个点（09:30–11:30 / 13:00–15:00），跨日清零。'
      + '本条自 2026-11-18 收盘后上线，今日无点，下个交易日开盘后开始累积。'
      + '</div></div>';
  }
  const valid = pts.filter((x) => x.value != null);
  const W = 600; const H = 150; const L = 34; const R = 12; const T = 10; const B = 20;
  const iw = W - L - R; const ih = H - T - B;
  const hm2m = (t) => { const a = String(t).split(':'); return (+a[0]) * 60 + (+a[1]); };
  // 2026-11-19（主人）：「不应该是把中间断开，而是把中间那一截取消掉」——
  //   X 轴按**交易分钟**排（共 240 分钟），午休 11:30–13:00 压成一点、不留白：
  //   11:30 与 13:00 落在同一 x，曲线自然接上；只有真缺档（如某分钟取数失败）才断。
  const tmin = (t) => {
    const m = hm2m(t);
    if (m <= 690) return Math.max(0, m - 570);          // 09:30–11:30 → 0..120
    if (m >= 780) return 120 + (m - 780);               // 13:00–15:00 → 120..240
    return 120;                                          // 11:30–13:00 压成一点
  };
  const X = (t) => L + iw * Math.max(0, Math.min(1, tmin(t) / 240));
  const Y = (v) => T + ih * (1 - Math.max(0, Math.min(100, Number(v))) / 100);
  const tick = (v) => '<text x="' + (L - 5) + '" y="' + (Y(v) + 3.5).toFixed(1)
    + '" text-anchor="end" font-size="9" fill="var(--dim)">' + v + '</text>';
  const gridY = (v) => '<line x1="' + L + '" y1="' + Y(v).toFixed(1) + '" x2="' + (W - R)
    + '" y2="' + Y(v).toFixed(1) + '" stroke="var(--dim)" stroke-opacity="0.16" stroke-width="1"/>';
  const ref = (v, color) => '<line x1="' + L + '" y1="' + Y(v).toFixed(1) + '" x2="' + (W - R)
    + '" y2="' + Y(v).toFixed(1) + '" stroke="' + color + '" stroke-opacity="0.55"'
    + ' stroke-dasharray="3 3" stroke-width="1"/>';
  // 折线：值非空的点才连，遇到 null 就断开（不用 0 或前值填补）
  let path = '';
  let pen = false;
  let prevMin = null;
  // 2026-11-19（主人抓的 bug）：午休 11:30–13:00 没有落点（采样闸只记连续竞价），
  //   若直接 11:30 连到 13:00，就会画出一条横跨午休的假直线（看着像恐慌值维持了 1.5 小时）。
  //   正常节奏是 1 分钟一点，相邻点间隔 > GAP_MIN 视为「中间真没数据」→ 断线重起（M），
  //   不插值、不拿前值补齐（与 null 同处理）。
  const GAP_MIN = 3;
  const dots = [];
  pts.forEach((x) => {
    if (x.value == null) { pen = false; prevMin = null; return; }
    const curMin = tmin(x.t);
    if (prevMin == null || curMin - prevMin > GAP_MIN) pen = false;
    prevMin = curMin;
    const px = X(x.t).toFixed(1); const py = Y(x.value).toFixed(1);
    path += (pen ? 'L' : 'M') + px + ' ' + py + ' ';
    pen = true;
    dots.push({ t: x.t, v: x.value, px: px, py: py });
  });
  const last = dots[dots.length - 1] || null;
  // 2026-11-19（主人）：「当日分时背后叠一条当日全A走势，作对比，不需要重新拉数据」。
  //   数据就在每个分时点自己身上——`median_pct`（全A中位涨跌 %）本来就是落点五字段之一，
  //   零新增取数、零新增接口。画法与趋势图的全A 层同语言（线下渐变面积 + 0 轴虚线，
  //   2026-11-05 标注 70 主人已明确不要那条全A 折线）。量纲不同 → 单独映射 Y2，
  //   只占图高、不碰左边 0–100 主轴刻度；缺值断开，不插值、不拿 0 填。
  const pcts = pts.map((x) => (x.median_pct == null ? null : Number(x.median_pct)));
  const pvals = pcts.filter((v) => v != null && isFinite(v));
  let allALayer = '';
  let allATxt = '—';
  if (pvals.length >= 2) {
    // 2026-11-19（主人）：「还是要叠加，把实际走势图作为背景板，但可以将它拉高，
    //   不要挤在底下一小块」。→ 纵坐标不按 ±M 居中（那种画法 0% 在正中，曲线
    //   只在中间窄带摆，面积只能铺在下半），改成**当日极值自适应铺满全高**：
    //   最深跌幅贴底、最浅贴顶（上下各留 8% 余量），面积从曲线一直铺到图底，整幅当背板。
    const pmin0 = Math.min.apply(null, pvals);
    const pmax0 = Math.max.apply(null, pvals);
    const span0 = pmax0 - pmin0;
    const pad = span0 < 0.2 ? 0.1 : span0 * 0.08;
    const lo = pmin0 - pad; const hi = pmax0 + pad;
    const Y2 = (pct) => T + ih * (1 - (pct - lo) / (hi - lo));
    const baseY = T + ih;
    // 同样按连续段画（午休断开）：跨午休连成一片会把两个时段的形态混在一起
    const segs = [];
    let run = [];
    let prevM = null;
    pcts.forEach((v, i) => {
      const m = tmin(pts[i].t);
      const gap = prevM != null && m - prevM > GAP_MIN;
      prevM = m;
      if (v == null || gap) { if (run.length) segs.push(run); run = []; }
      if (v != null) run.push(i);
    });
    if (run.length) segs.push(run);
    let area = '';
    segs.forEach((seg) => {
      if (seg.length < 2) return;
      seg.forEach((i, k) => {
        area += (k === 0 ? 'M' : 'L') + X(pts[i].t).toFixed(1) + ' ' + Y2(pcts[i]).toFixed(1) + ' ';
      });
      const s0 = seg[0]; const s1 = seg[seg.length - 1];
      area += 'L ' + X(pts[s1].t).toFixed(1) + ' ' + baseY.toFixed(1)
        + ' L ' + X(pts[s0].t).toFixed(1) + ' ' + baseY.toFixed(1) + ' Z ';
    });
    if (area) {
      area += '';
      const lastPct = pvals[pvals.length - 1];
      const tone = lastPct >= 0 ? 'var(--up)' : 'var(--down)';
      const pmin = pmin0; const pmax = pmax0;
      allATxt = (lastPct >= 0 ? '+' : '') + num(lastPct, 2) + '%'
        + '（区间 ' + num(pmin, 2) + ' ~ ' + num(pmax, 2) + '%）';
      allALayer = '<defs><linearGradient id="it-pd-alla" x1="0" y1="0" x2="0" y2="1">'
        + '<stop offset="0" stop-color="' + tone + '" stop-opacity="0.22"/>'
        + '<stop offset="0.6" stop-color="' + tone + '" stop-opacity="0.09"/>'
        + '<stop offset="1" stop-color="' + tone + '" stop-opacity="0.02"/>'
        + '</linearGradient></defs>'
        + '<path d="' + area + '" fill="url(#it-pd-alla)" stroke="none"/>'
        + (lo <= 0 && hi >= 0
          ? '<line x1="' + L + '" y1="' + Y2(0).toFixed(1) + '" x2="' + (W - R)
            + '" y2="' + Y2(0).toFixed(1) + '" stroke="var(--fg)" stroke-opacity="0.18"'
            + ' stroke-width="1" stroke-dasharray="2 4"/>'
          : '');
    }
  }
  const hi = valid.length ? valid.reduce((a, b) => (Number(b.value) > Number(a.value) ? b : a)) : null;
  const lo = valid.length ? valid.reduce((a, b) => (Number(b.value) < Number(a.value) ? b : a)) : null;
  const first = valid[0] || null;
  const xlab = '<text x="' + L + '" y="' + (H - 5)
    + '" text-anchor="start" font-size="9" fill="var(--dim)">09:30</text>'
    + '<text x="' + X('11:30').toFixed(1) + '" y="' + (H - 5)
    + '" text-anchor="middle" font-size="9" fill="var(--dim)">11:30/13:00</text>'
    + '<text x="' + (W - R) + '" y="' + (H - 5)
    + '" text-anchor="end" font-size="9" fill="var(--dim)">15:00</text>';
  const stCls = PANIC_CLS[p.state] || 'normal';
  const read = (x) => (x == null ? '—' : num(x.value, 1) + '（' + esc(String(x.t)) + '）');
  return '<div class="it-pday">'
    + '<div class="it-pday-h"><b>' + esc(fmtDate8(p.trade_date || '')) + '</b>'
    + '<span class="it-pb-state ' + stCls + '">' + esc(p.state || '—') + '</span>'
    + '<span class="it-pday-v">' + num(p.value, 1) + '</span>'
    + '<span class="it-pday-d">当日走势 ' + dots.length + ' 点</span></div>'
    + '<svg class="it-pchart it-pdchart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="当日恐慌广度走势与当日全A中位（面积）——每分钟一点，低值=恐慌">'
    + allALayer
    + gridY(0) + gridY(50) + gridY(100) + tick(0) + tick(50) + tick(100)
    + ref(ICE_THRESHOLD, 'var(--danger)') + ref(ICE_COLD_MAX, 'var(--warn)') + ref(90, 'var(--dim)')
    + xlab
    + '<path d="' + path + '" fill="none" stroke="var(--dim)" stroke-width="1.7"/>'
    + (last ? '<circle cx="' + last.px + '" cy="' + last.py + '" r="2.8" fill="var(--danger)"><title>'
      + esc(last.t) + ' 恐慌 ' + num(last.v, 1) + '</title></circle>' : '')
    + '</svg>'
    + '<div class="it-pday-foot">'
    + '开盘 ' + read(first) + ' ｜ 最高 ' + read(hi) + ' ｜ 最低 ' + read(lo)
    + ' ｜ 全A中位 ' + allATxt
    + ' ｜ 参考线：极冰 &lt;' + ICE_THRESHOLD + '、冰点 ' + ICE_COLD_MAX + '、过热 90'
    + '</div></div>';
}

function panicBoxHtml(p, allA) {
  if (PANIC_VIEW === 'today') {
    return '<div class="it-pchart-box is-today" title="点击切回近 60 个交易日的趋势图">'
      + '<div class="it-pchart-legend"><span class="it-lg"><i class="lg-panic"></i>当日恐慌走势</span>'
      + '<span>每分钟一点 · 低值=恐慌</span>'
      + '<span>极冰 &lt;' + ICE_THRESHOLD + ' · 冰点 ' + ICE_COLD_MAX + '</span>'
      + '<span>背景面积 = 全A中位涨跌（红涨绿跌）</span>'
      + '<span>点击切回趋势</span></div>'
      + panicTodayChart(p) + '</div>';
  }
  const allARow = (allA && allA.ok && allA.rows && allA.rows.length) ? allA.rows[allA.rows.length - 1] : null;
  const allAPct = (allARow && allARow.pct != null) ? Number(allARow.pct) : null;
  const nPts = Math.min((p.series || []).filter((x) => x && x.value != null).length, PANIC_DAYS);
  return '<div class="it-pchart-box" title="点击切换：当日的冰点情况">'
    + '<div class="it-pchart-legend"><span class="it-lg"><i class="lg-panic"></i>恐慌广度</span>'
    + '<span class="it-lg"><i class="lg-ldc"></i>跌停家数分位（反向）</span>'
    + (allAPct == null ? '' : '<span class="it-lg"><i class="lg-alla" style="background:var(--'
      + (allAPct >= 0 ? 'up' : 'down') + ')"></i>全A 880008 '
      + '<b style="color:var(' + (allAPct >= 0 ? '--up' : '--down') + ')">' + (allAPct >= 0 ? '+' : '') + num(allAPct, 2) + '%</b>'
      + '（面积=累计）</span>')
    + '<span class="it-lg"><i class="lg-ice"></i>冰点（&lt;' + ICE_THRESHOLD + '）</span>'
    + '<span>两线同向：低值=恐慌</span><span>参考线 10 / 90</span>'
    + '<span>近 ' + nPts + ' 个交易日</span></div>'
    + panicChart(p, allA) + '</div>';
}

function panicHtml(p, allA) {
  if (!p) return '<p class="it-muted">恐慌广度加载中…（/api/panic）</p>';
  if (!p.ok) {
    return '<p class="it-muted">恐慌广度暂不可用：' + esc(p.error || p.hint || p.detail || 'no data') + '</p>';
  }
  const st = PANIC_CLS[p.state] || 'normal';
  // 量能项（2026-10-29 第四项）：必须标清楚是盘中预估还是收盘实际，以及为何没计入
  const VOL_MARK = { forecast: '预估', actual: '实际', up_day: '上涨日不计入',
    no_amount: '无数据', thin_sample: '样本不足' };
  const VOL_KIND = { forecast: '预估', actual: '实际' };
  const items = (p.items || []).map((x) => {
    const isVol = x.key === 'amount';
    const nd = isVol ? 3 : ((x.value != null && Math.abs(x.value) < 1) ? 3 : 2);
    const valCls = x.key === 'median' ? cls(x.value) : '';
    const pq = (x.percentile == null || isNaN(x.percentile))
      ? 0 : Math.max(0, Math.min(100, Number(x.percentile)));
    const mark = isVol
      ? (x.state === 'in' ? (VOL_KIND[x.kind] || '') : (VOL_MARK[x.state] || ''))
      : '';
    const lab = isVol ? ('量能（成交额/昨日）' + (mark ? ' · ' + mark : '')) : x.label;
    const tip = isVol
      ? '量能 当前 ' + num(x.value, 3) + '×（今日成交额 ÷ 昨日全天额）；同日刻比 '
        + num(x.ratio_same_time, 3) + '×（与预估比背离=盘中节奏差异，不改分）'
        + (mark ? '；口径：' + mark : '')
        + '；2026-11-19 起量能**不参与打分**，只在冰点区做确认标签'
        + (x.confirm ? '（当前：' + x.confirm + '）' : '')
      : (x.label + ' 当前 ' + num(x.value, nd) + (x.unit || '') + '，处于过去 '
        + num(p.window, 0) + ' 个交易日分位 ' + num(x.percentile, 1));
    const volTag = isVol ? (x.confirm || '不参与打分') : ('分位 ' + num(x.percentile, 1));
    return '<div class="it-pitem" style="--pq:' + pq.toFixed(1) + '%" title="' + esc(tip) + '">'
      + '<span class="it-pitem-k">' + esc(lab)
      + (x.count != null ? '<span class="it-pitem-c">' + num(x.count, 0) + ' 家</span>' : '') + '</span>'
      + '<span class="it-pitem-v ' + valCls + '">' + num(x.value, nd) + esc(x.unit || '') + '</span>'
      + '<span class="it-pitem-q">' + esc(volTag) + '</span></div>';
  }).join('');
  const stg = (p.stage && p.stage.label) ? (esc(p.stage.label) + '（' + esc(p.stage.date || '') + '）') : '—';
  const modeTxt = p.mode === 'live' ? '（盘中）' : (p.mode === 'close' ? '（收盘）' : '（已收盘）');
  return '<div class="it-panic">'
    + '<div class="it-panic-hero">'
    + '<div class="it-panic-lab">恐慌广度</div>'
    + '<div class="it-panic-val ' + st + '">' + num(p.value, 1) + '</div>'
    + '<span class="it-pb-state ' + st + '">' + esc(p.state || '—') + '</span>'
    + '<div class="it-panic-lab">0–100 · 低值=恐慌</div>'
    + '</div>'
    + '<div class="it-pitems">' + items + '</div>'
    + panicBoxHtml(p, allA)
    + '</div>'
    + '<div class="it-pfoot">'
    + '<span>六阶段：<b>' + stg + '</b></span>'
    + '<span>数据时点：<b>' + esc(p.asof || '') + '</b>' + modeTxt + '</span>'
    + '<span>历史分位 <b>' + num(p.window, 0) + '</b> 日</span>'
    + '<span>来源：<b>' + esc(p.source || '') + '</b></span>'
    + '</div>';
}

// ---------------------------------------------------------------- AMV 实时摘要（旧端 5286-5325）
function amvHtml(lv) {
  if (!lv) return '<p class="it-muted">实时活跃市值加载中…（/api/amv_live.json）</p>';
  const all = lv.all || {};
  const cov = lv.coverage || {};
  const idx = Array.isArray(lv.indices) ? lv.indices : [];
  const sec = lv.sectors || {};
  const staleWarn = lv.stale
    ? '<div class="it-warn">⚠ 陈旧：' + esc(lv.stale_reason || '闸门未过')
      + '（保留 ' + esc(lv.stale_since || '上轮') + ' 值）</div>'
    : '';
  const idxHtml = idx.length
    ? '<div class="it-idx">' + idx.map((r) => '<div class="it-idx-i"><span class="it-idx-n" title="'
      + esc(r.code || '') + '">' + esc(r.name || r.code || '—') + '</span>'
      + '<span class="it-idx-v ' + cls(r.amv_pct) + '">' + pct(r.amv_pct) + '</span></div>').join('')
      + '</div>'
    : '<p class="it-muted">无指数分项（amv_live.indices 为空）</p>';
  const sideList = (title, rows) => '<div><div class="it-amv-l">' + esc(title) + '</div>'
    + '<div class="it-list">' + ((rows || []).length
      ? rows.map((r) => '<div class="it-li"><span class="t">' + esc(r.name || r.code || '—') + '</span>'
        + '<span class="v ' + cls(r.amv_pct) + '">' + pct(r.amv_pct) + '</span></div>').join('')
      : '<div class="it-muted">—</div>')
    + '</div></div>';
  return staleWarn
    + '<div class="it-amv">'
    + '<div class="it-amv-main">'
    + '<div class="it-amv-l">沪深全A 活跃市值</div>'
    + '<div class="it-amv-v">' + fmtAmount(all.amv) + ' <span class="' + cls(all.amv_pct)
    + '" style="font-size:1rem">' + pct(all.amv_pct) + '</span></div>'
    + '<div class="it-amv-l">' + esc(lv.tick_date || lv.trade_date || '—') + ' @ ' + esc(lv.asof || '')
    + ' ｜ 覆盖 ' + num((cov.pct || 0) * 100, 1) + '%（' + esc(cov.denom_kind || '') + '）'
    + ' ｜ 单位校验 ' + num(lv.vol_unit_check, 3) + ' ｜ 口径 v9（amv_store）</div>'
    + idxHtml
    + '</div>'
    + '<div>'
    + '<div class="it-cols">'
    + sideList('板块领先（amv_pct）', sec.leaders)
    + sideList('板块落后（amv_pct）', sec.laggards)
    + '</div></div></div>';
}

// ---------------------------------------------------------------- 持仓实时状态（旧页 5318 `_amvHoldHtml`）
//
// 旧页它是独立一张卡（排在「板块与个股研究」与审计明细之间），不是 AMV 块的一部分。
// v2 之前把这张表并进了 AMV 卡，排版上少了一个块——现照旧页拆回来（任务书拍板 L6）。
// 表头逐字照旧页：标的 / 现价 / 成本 / 盈亏 / 距止损锚；空值一律「—」，不补零。
export function holdHtml(lv) {
  const hold = (lv && Array.isArray(lv.holdings)) ? lv.holdings : [];
  if (!hold.length) return '<p class="it-muted">持仓实时状态加载中…（amv_live.json；只读展示）</p>';
  return '<table class="it-tbl"><thead><tr>'
    + ['标的', '现价', '成本', '盈亏', '距止损锚'].map((h, i) => '<th' + (i ? ' class="n"' : '') + '>'
      + esc(h) + '</th>').join('')
    + '</tr></thead><tbody>' + hold.map((h) => '<tr>'
      + '<td>' + esc(h.name || h.code || '—') + '<span class="it-code">' + esc(code6(h.code)) + '</span></td>'
      + '<td class="n">' + num(h.price, 3) + '</td>'
      + '<td class="n">' + (h.cost != null ? num(h.cost, 3) : '—') + '</td>'
      + '<td class="n ' + cls(h.pnl_pct) + '">' + (h.pnl_pct != null ? pct(h.pnl_pct) : '—') + '</td>'
      + '<td class="n ' + cls(h.dist_stop_pct) + '">' + (h.dist_stop_pct != null ? pct(h.dist_stop_pct) : '—') + '</td>'
      + '</tr>').join('') + '</tbody></table>';
}

// ---------------------------------------------------------------- 广度 880（旧端 5162 _br880CardHtml）
function breadthHtml(b) {
  if (!b) return '<p class="it-muted">涨跌家数加载中…（/api/breadth_880.json）</p>';
  const t = b.today || {};
  const p = b.prev || {};
  const cell = (x, c) => '<span class="' + (c || '') + '">' + (x == null ? '—' : esc(x)) + '</span>';
  const amtT = t.amount != null ? fmtAmount(t.amount) : null;
  const amtP = p.amount != null ? fmtAmount(p.amount) : null;
  const totP = (p.up != null && p.down != null && p.flat != null) ? (p.up + p.down + p.flat) : null;
  const row = (lab, a, bb) => '<tr><td>' + esc(lab) + '</td><td class="n">' + a + '</td><td class="n">' + bb + '</td></tr>';
  return '<table class="it-tbl it-tbl-b"><thead><tr><th>项</th><th class="n">今日</th><th class="n">昨日</th></tr></thead><tbody>'
    + row('涨', cell(t.up, 'it-up'), cell(p.up, 'it-up'))
    + row('跌', cell(t.down, 'it-down'), cell(p.down, 'it-down'))
    + row('平', cell(t.flat), cell(p.flat))
    + row('涨停', cell(t.limit_up, 'it-up'), cell(p.limit_up, 'it-up'))
    + row('跌停', cell(t.limit_down, 'it-down'), cell(p.limit_down, 'it-down'))
    + row('成交额', cell(amtT), cell(amtP))
    + row('统计', cell(t.total), cell(totP))
    + '</tbody></table>'
    + '<div class="it-src" style="margin-top:4px">880005/880006（含北交所）@ ' + esc(t.date || '—')
    + ' ｜ 平＝总数−涨−跌 · 昨日←vipdoc .day（' + esc(p.date || '—') + '）</div>';
}

// ---------------------------------------------------------------- 板块轨迹摘要（服务端已裁，见 backend.py）
function trackSpark(series) {
  const pts = (series || []).filter((x) => x && x.all != null);
  if (pts.length < 2) return '<p class="it-muted">当日全A 序列不足（需 ≥2 个采样点）</p>';
  const W = 300; const H = 60; const pad = 3;
  const vals = pts.map((x) => Number(x.all));
  let lo = Math.min(...vals); let hi = Math.max(...vals);
  if (hi <= lo) { hi = lo + 0.1; }
  const X = (i) => pad + (W - 2 * pad) * i / (pts.length - 1);
  const Y = (v) => H - pad - (v - lo) / (hi - lo) * (H - 2 * pad);
  const d = pts.map((x, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(x.all).toFixed(1)).join(' ');
  const zero = (lo < 0 && hi > 0)
    ? '<line x1="' + pad + '" y1="' + Y(0).toFixed(1) + '" x2="' + (W - pad) + '" y2="' + Y(0).toFixed(1)
      + '" stroke="var(--dim)" stroke-opacity="0.35" stroke-dasharray="3 3" stroke-width="1"/>'
    : '';
  return '<svg class="it-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img"'
    + ' aria-label="当日沪深全A 活跃市值涨幅轨迹（%），' + pts.length + ' 个采样点">'
    + zero
    + '<path d="' + d + '" fill="none" stroke="var(--accent)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>'
    + '</svg>'
    + '<div class="it-src">' + esc(pts[0].t) + ' → ' + esc(pts[pts.length - 1].t) + ' ｜ 峰值 '
    + num(hi, 2) + '% ｜ 谷值 ' + num(lo, 2) + '%</div>';
}

function trackTable(title, rows, pins) {
  if (!rows || !rows.length) return '<div><div class="it-amv-l">' + esc(title) + '</div><div class="it-muted">—</div></div>';
  return '<div><div class="it-amv-l">' + esc(title) + '</div>'
    + '<table class="it-tbl"><thead><tr><th>板块</th><th class="n">超额</th><th class="n">涨幅(等权)</th></tr></thead><tbody>'
    + rows.map((r) => '<tr class="it-row' + ((pins || []).indexOf(r.code) >= 0 ? ' it-on' : '')
      + '" data-code="' + esc(r.code) + '" data-name="' + esc(r.name) + '" title="点一行钉住/取消（钉住后在「盘中脉冲」看该板块当日轨迹）">'
      + '<td>' + esc(r.name) + '</td>'
      + '<td class="n ' + cls(r.rel) + '">' + pp(r.rel) + '</td>'
      + '<td class="n ' + cls(r.chg_e) + '">' + pct(r.chg_e) + '</td></tr>').join('')
    + '</tbody></table></div>';
}

function trackHtml(s, pins) {
  if (!s) return '<p class="it-muted">板块轨迹加载中…（/api/p/intraday/summary）</p>';
  if (!s.ok) return '<p class="it-muted">板块轨迹暂不可用：' + esc(s.reason || s.detail || 'no data') + '</p>';
  return '<div class="it-track">'
    + '<div>'
    + '<div class="it-kv"><span>日期 <b>' + esc(s.date || '—') + '</b></span>'
    + '<span>末点 <b>' + esc(String(s.last_ts || '—').slice(11, 19) || '—') + '</b></span>'
    + '<span>采样 <b>' + num(s.snaps, 0) + '</b> 点</span>'
    + '<span>板块 <b>' + num(s.boards_total, 0) + '</b> 个</span></div>'
    + '<div class="it-kv" style="margin-top:4px">'
    + '<span>全A 起点 <b class="' + cls(s.first_all) + '">' + pct(s.first_all) + '</b></span>'
    + '<span>末值 <b class="' + cls(s.last_all) + '">' + pct(s.last_all) + '</b></span>'
    + '<span>日内变化 <b class="' + cls(s.all_delta) + '">' + pp(s.all_delta) + '</b></span>'
    + '<span>跑赢 <b>' + num(s.strong, 0) + '</b> / 跑输 <b>' + num(s.weak, 0) + '</b></span></div>'
    + '<div style="margin-top:6px">' + trackSpark(s.all_series) + '</div>'
    + '</div>'
    + '<div class="it-cols" style="margin-top:0">'
    + trackTable('领先板块（超额 = amv_pct − 全A）', s.leaders, pins)
    + trackTable('落后板块（超额 = amv_pct − 全A）', s.laggards, pins)
    + '</div></div>';
}

// ---------------------------------------------------------------- 盘中脉冲（旧端 6009 watchPaintLiveSector）
function pulseCard(code, name, v, pinned) {
  const pts = (v && v.points) || [];
  const ap = pts.map((p) => p.amv_pct).filter((x) => x != null);
  const last = pts.length ? pts[pts.length - 1] : {};
  let chart = '<div class="it-pc-sub">无当日轨迹</div>';
  if (ap.length >= 2) {
    const W = 160; const H = 34; const pad = 2;
    let lo = Math.min(...ap); let hi = Math.max(...ap);
    if (hi <= lo) { hi = lo + 0.1; }
    const X = (i) => pad + (W - 2 * pad) * i / (ap.length - 1);
    const Y = (x) => H - pad - (x - lo) / (hi - lo) * (H - 2 * pad);
    const d = ap.map((x, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(x).toFixed(1)).join(' ');
    chart = '<svg class="it-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img"'
      + ' aria-label="' + esc(name) + ' 当日活跃市值涨幅轨迹">'
      + '<path d="' + d + '" fill="none" stroke="' + (ap[ap.length - 1] >= 0 ? 'var(--up)' : 'var(--down)')
      + '" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>'
      + '<div class="it-pc-sub">' + esc(pts[0].t) + '→' + esc(pts[pts.length - 1].t) + ' · '
      + ap.length + ' 点 · 区间 ' + num(lo, 2) + '~' + num(hi, 2) + '%</div>';
  }
  return '<div class="it-pc"><div class="it-pc-hd"><span class="it-pc-n" title="' + esc(code) + '">'
    + (pinned ? '★ ' : '') + esc(name) + '</span>'
    + '<span class="it-pc-v ' + cls(last.amv_pct) + '">' + pct(last.amv_pct) + '</span></div>'
    + chart
    + '<div class="it-pc-sub">涨幅 <span class="' + cls(last.change_pct) + '">' + pct(last.change_pct) + '</span></div>'
    + '</div>';
}

function pulseHtml(codes, map, pins) {
  if (!codes || !codes.length) {
    return '<p class="it-muted">盘中脉冲待数据：板块轨迹摘要就绪后自动拉取（/api/sector_intraday.json?codes=）</p>';
  }
  const items = codes.map((c) => {
    const v = map && map[c];
    const name = (v && v.name) || c;
    return pulseCard(c, name, v, (pins || []).indexOf(c) >= 0);
  }).join('');
  const missing = codes.filter((c) => !(map && map[c] && map[c].ok)).length;
  return '<div class="it-pulse">' + items + '</div>'
    + (missing ? '<div class="it-src" style="margin-top:4px">' + missing + ' 个板块无当日轨迹（如实留空，不补零）</div>' : '');
}

// ---------------------------------------------------------------- 卡片
export default {
  cards: [{
    id: 'intraday.main',
    title: '盘中',
    slot: 'main',
    order: 15,
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
        amv: null,
        panic: null,
        breadth: null,
        track: null,
        pulse: null,          // { codes: [], map: {} }
        intraday: null,       // /api/intraday.json 原样（会话行 + 成交额对比 + 审计明细 + 实时行情数据源）
        allA: null,           // /index-series：全A 880008 日涨跌（冰点卡背景层，标注 58）
        spark: null,          // /api/sector_spark.json 原样（行情卡背景线；先取数再渲，见 quotes.js 文件头）
        sparkAt: 0,           // 上次取 spark 的时间（SPARK_TTL_MS 轻缓存）
        pins: [],             // 用户钉住的板块码（点轨迹表一行）
        trackAt: 0,           // 板块轨迹上次拉取时间（60s 缓存，见 TRACK_TTL_MS）
        html: Object.create(null),
        el: {},
      };
      this._it = S;
      injectCardCss(document);
      injectResearchCss(document);
      injectAuditCss(document);
      injectQuotesCss(document);

      const div = (clsName, text) => {
        const d = document.createElement('div');
        if (clsName) d.className = clsName;
        if (text != null) d.textContent = text;
        return d;
      };

      const wrap = div('it-card');

      const top = div('it-top');
      const title = div('it-title', '盘中');
      const badgePanic = div('it-badge', '恐慌：—');
      const badgeAmv = div('it-badge', 'AMV：—');
      const sub = div('it-sub', '');
      const btnReload = document.createElement('button');
      btnReload.type = 'button';
      btnReload.className = 'it-btn';
      btnReload.textContent = '刷新';
      btnReload.addEventListener('click', () => { load(true); });
      top.append(title, badgePanic, badgeAmv, sub, btnReload);

      const status = div('it-err');
      status.hidden = true;

      const secPanic = div('it-sec');
      const secSector = div('it-sec');
      // 旧页盘中**没有**独立的 AMV 卡 / 轨迹摘要 / 盘中脉冲（逐块取证见
      // docs/HANDOFF_20261026_intraday_relayout.md）。按主人 2026-10-26 裁定归位：
      //   · AMV 与持仓同源同卡（旧页 5313 loadAmvLive 同时写 #amvHoldBody）→ 并进「持仓实时状态」
      //   · 轨迹摘要/脉冲的内容对应物在「日内演化」（左图全A 轨迹 + 右图板块曲线）→ 紧贴 evo 之后
      const secResearch = div('it-sec');
      const secQuotes = div('it-sec');     // 实时行情 KPI（旧页 4677-4685，含迷你背景线）
      const quotesBody = div('it-quotes-host');
      const secTurnover = div('it-sec');   // 两市成交额（旧页 4686-4703：3 张小卡竖排 + 汇总行）
      const turnoverBody = div('it-turnover-host');
      const secAudit = div('it-sec');      // 审计明细（旧页 4718-4731，默认折叠）
      const secHold = div('it-sec');       // 持仓实时状态 + AMV 摘要（旧页 5303-5318 的位置）
      const holdBody = div('it-hold-host');
      const secEvo = div('it-sec');        // 日内演化（旧页 3689-4313 / frontend/evo.js）
      const evoBody = div('evo-body');     // 旧页 #amvEvoBody；类名 .evo-body 是本卡的容器内查询锚点
      // 顶部两栏（旧页 1547 `.top-split`）：左窄=两市成交额 / 右宽=板块轮动·当天，两卡等高拉伸
      const split = div('it-split');

      // 板块轮动·当天（旧页 4758-5106 / frontend/sector.js）。
      // 旧页是「#stBody 内建 innerHTML + 事件委托绑 #panel-intraday」；
      // 这里把 .st-body 放进 .it-sec，事件委托绑在**不参与重绘的** .it-card 上。
      const sectorBody = div('st-body');

      const src = div('it-src', '数据源：/api/panic（冰点·恐慌广度）· /api/amv_live.json（实时活跃市值）'
        + ' · /api/breadth_880.json（涨跌家数）· /api/p/intraday/summary（板块轨迹摘要，服务端已裁剪）'
        + ' · /api/sector_intraday.json?codes=（板块当日轨迹）'
        + ' · /api/intraday.json（实时行情 KPI / 会话行 / 成交额对比 / 审计明细：信号流水·判强判弱·环境系数'
        + '·盘中事件·通道B）· /api/sector_spark.json?level=INDEX（行情卡背景迷你线，与行情同一拍取）。'
        + ' 全卡只读：不写生产文件、不下单。'
        + ' 刷新 ' + (REFRESH_MS / 1000) + 's（页面不可见时暂停）。');

      // 会话行：旧页 4707 是紧跟「冰点监控」后的一行文本（不是 card），卡片保留这个位置。
      // 类名沿用旧页 .sess-line（样式在 audit.css.js 里加了 .it-card 作用域）。
      const sessLine = div('sess-line', SESS_LOADING);

      // 块序**逐块照旧页 4704-4731**（任务书拍板：主序对齐，v2 增量块下沉到卡尾）：
      //   冰点 → 会话行 → 实时行情 → [两市成交额 ｜ 板块轮动] → 研究 → 持仓 → 审计明细(折叠)
      //   → — v2 增量块 — → AMV / 轨迹摘要 / 盘中脉冲 → 来源行
      // （旧页「日内演化」在 top-split 与「板块与个股研究」之间，第 2 步 evo.js 就位后插在那里）
      // 块序（第二版，2026-10-26 按内容归属重排）：
      //   冰点 → 会话行 → 实时行情 → [两市成交额 ｜ 板块轮动] → 日内演化 →
      //   板块轨迹明细（v2 增量，紧贴内容对应的日内演化）→ 研究 → 持仓实时状态（含 AMV 摘要）→ 审计 → 来源行
      // 块序（第三版，2026-10-26 主人看过截图后拍板）：
      //   冰点 → 会话行 → 实时行情 → [两市成交额 ｜ 板块轮动] → 日内演化（左侧信息行已吸收全A 摘要）
      //   → 研究 → 持仓实时状态（只剩持仓表）→ 审计 → 来源行
      // 删掉的：【板块轨迹明细】整卡（主人批「重复的」：领先/落后双榜 ≈ 日内演化右榜，
      //   逐码脉冲小图 ≈ 板块轮动网格视图）；「持仓实时状态」上段的 AMV 摘要（同批「重复的」）。
      //   两处代码（trackHtml / pulseHtml / amvHtml）保留不删，先只从编排里摘掉。
      split.append(secTurnover, secSector);
      wrap.append(top, status, secPanic, sessLine, secQuotes, split,
        secEvo, secResearch, secHold, secAudit, src);
      root.append(wrap);
      S.el = { top, badgePanic, badgeAmv, sub, status, secPanic, sessLine, secQuotes, quotesBody,
        split, secTurnover, turnoverBody, secSector, secEvo, evoBody,
        secResearch, secHold, holdBody, secAudit };

      // 防闪写入：内容没变就不碰 DOM（与 dashboard / battle_plan 卡同一策略）
      const setHtml = (el, html, key) => {
        if (!el) return;
        if (S.html[key] === html) return;
        S.html[key] = html;
        el.innerHTML = html;
      };
      const setText = (el, text, key) => {
        if (!el) return;
        if (S.html[key] === text) return;
        S.html[key] = text;
        el.textContent = text;
      };
      const setStatus = (text) => {
        const el = S.el.status;
        if (!el) return;
        if (!text) { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        el.textContent = text;
      };
      const secHead = (name, note) => '<div class="it-sec-hd"><b>' + esc(name) + '</b>'
        + (note ? '<span class="it-sec-n">' + esc(note) + '</span>' : '') + '</div>';

      // 板块轮动区头（旧页 4762 `card("板块轮动 · 当天", ...)`）：
      // 旧页它跟在「实时行情」下面、在「日内演化」上面；卡片保留这个相对顺序。
      secSector.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>板块轮动 · 当天</b>'
        + '<span class="it-sec-n">四象 / 网格 ｜ 旧页 4758-5106 同口径（超额 = 板块 amv_pct − 沪深全A）</span></div>');
      secSector.append(sectorBody);

      // 板块轮动本体：建实例 → 绑定（绑在 .it-card 上，只本实例一份） → 首绘
      // 2026-10-26 主人「统一」：四象的标签与网格格原来带 data-open-amv（弹板块详情卡），
      // 改成与日内演化右榜同一行为 —— 跳下方研究卡 + 选中该板块行。pickBoard 定义在下面，
      // 两个模块共用同一份（sector 先建实例，但回调是调用时才取值，不影响）。
      S.sector = sector.createState(wrap, sectorBody, { pickBoard: (code) => pickBoard(code) });
      sector.bind(S.sector);
      sector.paint(S.sector);   // 无数据时输出「加载中…」，不留空白块

      // 日内演化（旧页 3689-4313 / frontend/evo.js）。
      // 数据源与「板块轮动·当天」**同一份全量轨迹**（S.trackRaw），不新建源。
      // 回填按钮要把轨迹缓存打掉重拉——缓存实际在**本文件的 S.trackRaw / S.trackAt**（不在 sector.js，
      // docs/HANDOFF_20261025_intraday_port.md §八 那条说法已核错），所以出口开在这里：
      //   invalidateTrack() 只清缓存（纯函数、不发请求）；reloadTrack() 清完再重取。
      // sector.js 一行未动。
      const invalidateTrack = () => { S.trackRaw = null; S.trackAt = 0; };
      const reloadTrack = async () => { invalidateTrack(); await loadSector(true); };
      secEvo.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>日内演化 · 活跃指数与指数贡献</b>'
        + '<span class="it-sec-n">左：活跃指数 / 六指数叠加 ｜ 右：指数贡献演变 + 板块榜 · '
        + '旧页 3689-4313 同口径（/api/sector_track.json）</span></div>');
      secEvo.append(evoBody);
      // 2026-10-26 主人：「点击板块时，不再默认打开板块详情，而是跳到下方显示，
      // 同时板块排名内对应板块出现选中效果」/「统一：四象也这么走」——
      // 日内演化右榜、板块轮动标签 + 网格格都走这一个出口：
      //   ① research.rsSet(sel) → 左栏排名该行高亮（tr.pk.sel）+ 中栏切到该板块成分股；
      //   ② 整卡滚到「板块与个股研究」区头。
      const pickBoard = (code) => {
        if (!code) return;
        if (S.research) research.rsSet(S.research, 'sel', code);
        const sec = S.el && S.el.secResearch;
        if (sec && typeof sec.scrollIntoView === 'function') {
          sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      };
      S.evo = evo.createState(wrap, evoBody, {
        api: api,
        track: () => S.trackRaw,          // 旧页读模块级 _sectorTrack；这里读本文件状态
        reloadTrack: reloadTrack,
        // 左图信息行要的全A 摘要（起点/末值/日内变化/跑赢跑输/峰值谷值）用轨迹卡原来的取数，
        // 不新造口径 —— 就是 /api/p/intraday/summary 那一份（S.track）。
        summary: () => S.track,
        pickBoard: pickBoard,
      });
      evo.bind(S.evo);
      evo.paint(S.evo);   // 无数据时输出「盘中 60s 采样后出现」，不留空白块

      // 日内演化重绘入口（旧页由 _evoPaint 在每次整页重建时调）：
      //   · 轨迹变了（loadSector 拉到新的一份）→ 必须重绘；
      //   · 只变了 AMV/预测（amv_live 单独 20s 一拍）→ 也要重绘，否则预测浮层滞后一拍；
      //   · **拖拽/框选中不抢重绘** —— 旧页 3s 整页重建会把拖拽打断，卡片不给这个坑。
      // 用签名去重，避免同一份数据渲两次（轨迹 + 预测同时更新时很常见）。
      const paintEvo = () => {
        if (!S.evo || !S.alive) return;
        if (S.evo.drag || S.evo.rangeDrag) return;
        const sig = [S.trackAt, (S.amv && S.amv.asof) || ''].join('|');
        if (sig === S.evoSig) return;
        S.evoSig = sig;
        evo.paint(S.evo);
      };

      // 板块与个股研究（旧页 4306-4531 / frontend/research.js）。
      // 旧页把这一块挂在 #rsBodyIntraday 上、靠 _rsTarget() 双槽位选可见 body；
      // 卡片只有一个宿主，直接给它一个容器节点（约束②）。
      const researchBody = div('rs-wrap-host');
      secResearch.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>板块与个股研究</b>'
        + '<span class="it-sec-n">左栏板块排名 / 中栏成分股 · 旧页 4306-4531 同口径'
        + '（/api/amv_research.json）</span></div>');
      secResearch.append(researchBody);
      S.research = research.createState(wrap, researchBody, api);
      research.bind(S.research);
      S.research.load(true);   // 首绘「加载中…」已由 bind 的 paint 完成，这里发起首次取数

      // 成交额对比 + 审计明细（旧页 4694-4699 / 4718-4731 / frontend/audit.js）。
      // 两者的宿主容器不参与 render() 的签名缓存：审计明细重绘会重置 details 展开态与滚动，
      // 由 audit.paint() 自己管（记住展开态、内容没变则整体替换一次）。
      secQuotes.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>实时行情</b>'
        + '<span class="it-sec-n">指数 KPI + 近 60 日活跃市值背景线 · 旧页 4677-4685 同口径'
        + '（/api/intraday.json + /api/sector_spark.json）</span></div>');
      secQuotes.append(quotesBody);
      secTurnover.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>两市成交额</b></div>');
      secTurnover.append(turnoverBody);
      // 持仓实时状态（旧页 #7 位置）。2026-10-26 主人：上段 AMV 摘要与他处重复 → 删，只留持仓表。
      secHold.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>持仓实时状态</b>'
        + '<span class="it-sec-n">旧页 5303-5318 同口径（/api/amv_live.json；只读展示）</span></div>');
      // AMV 实时刷新状态行：只在失败时出现（「刷新失败 HH:MM（原因）」）。
      // 不把它写进 AMV 徽标/时间戳——值仍是上一份，标成实时就是谎报。
      const amvNote = div('it-src', '');
      amvNote.hidden = true;
      secHold.append(amvNote);
      secHold.append(holdBody);
      secAudit.insertAdjacentHTML('afterbegin',
        '<div class="it-sec-hd"><b>审计明细</b>'
        + '<span class="it-sec-n">旧页 4718-4731 同口径 · 明细默认折叠（点标题展开）；会话行在上方冰点卡后</span></div>');
      // audit.paint 只写自己的 body（区头在它上面，不参与重绘）——约束②「容器内查询」同款。
      const auditBody = div('it-audit-host');
      secAudit.append(auditBody);
      S.audit = audit.createState(wrap, auditBody);
      audit.bind(S.audit);   // 首绘 5 张表的「（空）」占位，不留空白块

      // ---- 卡头徽标（旧端 4650 hero 与 5300 stamp 的摘要口径）
      const renderHead = () => {
        const p = S.panic;
        if (p && p.ok) {
          const st = PANIC_CLS[p.state] || 'normal';
          S.el.badgePanic.className = 'it-badge ' + (st === 'ice' || st === 'cold' ? 'danger'
            : (st === 'hot' ? 'warn' : (st === 'warm' ? 'ok' : '')));
          S.el.badgePanic.textContent = '恐慌：' + num(p.value, 1) + ' ' + (p.state || '');
        } else {
          S.el.badgePanic.className = 'it-badge';
          S.el.badgePanic.textContent = '恐慌：—';
        }
        const lv = S.amv;
        if (lv && lv.all && lv.all.amv_pct != null) {
          S.el.badgeAmv.className = 'it-badge ' + (lv.stale ? 'warn' : '');
          S.el.badgeAmv.textContent = 'AMV：' + pct(lv.all.amv_pct) + (lv.stale ? ' ·陈旧' : '');
        } else {
          S.el.badgeAmv.className = 'it-badge';
          S.el.badgeAmv.textContent = 'AMV：—';
        }
        const bits = [];
        if (lv && lv.asof) bits.push('AMV ' + lv.asof);
        if (S.track && S.track.ok) bits.push('轨迹 ' + (S.track.date || '') + ' 末点 ' + (String(S.track.last_ts || '').slice(11, 16) || '—'));
        // 2026-10-28：16:10 之后不再自动刷新，卡头如实标一下，免得当成实时盘面读
        if (isCloseFrozen()) bits.push('收盘定格 · 手动刷新');
        setText(S.el.sub, bits.join(' ｜ '), 'sub');
      };

      const render = () => {
        paintEvo();   // 日内演化：轨迹/预测变了才重绘（签名去重，见 paintEvo）
        setHtml(S.el.secPanic, secHead('冰点监控 · 恐慌广度', '跌停占比 / 上涨占比 / 全A中位 三项 250 日滚动分位')
          + panicHtml(S.panic, S.allA), 'panic');
        bindPanicCursor(S.el.secPanic, S.panic, S.allA, S.breadth);   // 光标读数（内容变了重新绑，未变则跳过）
        // 持仓实时状态 = 只剩持仓表（AMV 摘要已删：与徽标/日内演化前重复）
        setHtml(S.el.holdBody, '<div class="it-split-hd">持仓（/api/amv_live.json · 只读）</div>'
          + holdHtml(S.amv), 'hold');
        // 【板块轨迹明细】整卡已摘除（2026-10-26 主人批「重复的」）：与日内演化右榜、板块轮动网格重复。
        // trackHtml / pulseHtml / S.pulse 代码保留，暂不再渲染（恢复只需把这段和 secTrace 加回去）。
        // secResearch 不在这里重绘：研究卡自带 _rsPaint 同款签名缓存（frontend/research.js），
        // 每 20s 的 render() 只清 S.html 缓存，不碰它（旧页靠 30s/120s TTL 自己管刷新）。
        // 会话行 / 成交额对比 / 审计明细同理由各自模块写自己的容器：
        //   · 会话行是纯文本，走 setText 签名缓存（旧页 4707 每次整页重建，卡片不必）；
        //   · 成交额对比随 intraday.json 走 setHtml（金额变动才重写）；
        //   · 审计明细由 audit.paint 管（details 展开态、表格大，签名缓存会是二次实现）。
        // 会话行：数据没到 → 如实说「加载中」，不用 0 冒充（旧页此时整块还没渲染，卡片先占位）
        setText(S.el.sessLine, S.intraday ? sessionText(S.intraday) : SESS_LOADING, 'sess');
        // 实时行情：spark 与行情同一拍渲染（先取数再渲），不需要旧页那套「渲空 path 再回填」
        setHtml(S.el.quotesBody, mqHtml(S.intraday && S.intraday.market_quotes, S.spark), 'quotes');
        // secTurnover 的区头在 mount 时已插在容器首位（insertAdjacentHTML），这里只重写内容节，
        // 不整段覆盖——否则每次 render 都要把同一段区头 HTML 重新解析一遍。
        if (S.el.turnoverBody) {
          // 旧页 v12 效果图：左栏 3 张小卡竖排（分时图 / 成交额对比 / 涨跌家数），卡高由 top-split 拉伸。
          // 子块先渲好再交给 audit.turnoverHtml 拼：分时图与涨跌家数不在 audit.js 里，
          // 不为了拼一个字符串把 frontend.js 的渲染函数反向 import 进子模块。
          const to = (S.intraday && S.intraday.market_turnover) || {};
          setHtml(S.el.turnoverBody, turnoverHtml(to, S.breadth, {
            chart: turnoverChart(to),
            breadth: breadthHtml(S.breadth),
          }), 'turnover');
        }
        if (S.audit) audit.paint(S.audit, S.intraday);
        renderHead();
      };

      // ---- 盘中脉冲码表：默认领先/落后各 3，叠加用户钉住的码
      const pulseCodes = () => {
        const s = S.track;
        const out = [];
        const push = (c) => { if (c && out.indexOf(c) < 0) out.push(c); };
        if (s && s.ok) {
          (s.leaders || []).slice(0, PULSE_TOP).forEach((r) => push(r.code));
          (s.laggards || []).slice(0, PULSE_TOP).forEach((r) => push(r.code));
        }
        (S.pins || []).forEach((c) => { if (out.length < PULSE_TOP * 2 + PULSE_MAX) push(c); });
        return out;
      };

      const loadPulse = async () => {
        const codes = pulseCodes();
        if (!codes.length) { S.pulse = { codes: [], map: {} }; return; }
        try {
          const d = await api.get('/api/sector_intraday.json?codes=' + encodeURIComponent(codes.join(',')));
          if (!S.alive) return;
          S.pulse = { codes: codes, map: (d && d.codes) || {} };
        } catch (e) {
          if (!S.alive) return;
          S.pulse = { codes: codes, map: {} };
          setStatus('板块脉冲读取失败：' + errText(e));
        }
      };

      // ---- 板块轮动·当天：本轮任务新增，照旧页搬（旧页 loadSectorTrack 4905 + _stPaint 4766）
      // 独立于主 load：全量轨迹 2.7MB，失败不影响其它块；60s 缓存见 TRACK_TTL_MS。
      const loadSector = async (force) => {
        if (!S.alive) return;
        if (!force && S.trackRaw && (Date.now() - S.trackAt < TRACK_TTL_MS)) {
          S.sector.track = S.trackRaw;
          sector.paint(S.sector);
          paintEvo();   // 同一份轨迹，两处共吃
          return;
        }
        try {
          const d = await api.get('/api/sector_track.json');
          if (!S.alive) return;
          S.trackRaw = d;
          S.trackAt = Date.now();
          S.sector.track = d;
          sector.paint(S.sector);
          paintEvo();
        } catch (e) {
          if (!S.alive) return;
          // 不拿旧值冒充新值：保留上次成功的 track（旧页 4905 也是 catch 里置 null 后才重绘，
          // 但卡片已经有数据时不把屏幕抹白，只在上报栏里如实标注失败）
          setStatus('板块轮动读取失败：' + errText(e));
          sector.paint(S.sector);
          paintEvo();
        }
      };

      // ---- 实时行情背景线（旧页 5129-5146 loadIndexSpark）
      // 与主 load 并行取，20s 轻缓存（背景线慢变）；失败只留空 d，行情主体照常渲染。
      const loadSpark = async (quotes) => {
        const codes = sparkCodes(quotes);
        if (!codes.length) { S.spark = null; return; }
        if (S.spark && (Date.now() - S.sparkAt < SPARK_TTL_MS)) return;   // 轻缓存
        try {
          const d = await api.get('/api/sector_spark.json?level=INDEX&codes=' + encodeURIComponent(codes.join(',')));
          if (!S.alive) return;
          S.spark = d || null;
          S.sparkAt = Date.now();
        } catch (e) {
          // 背景线是装饰层：取不到就留空，不报错到状态栏（旧页 catch 里也是 data=null 后 return）
          if (!S.alive) return;
          S.spark = null;
        }
      };

      // ---- 取数：快块先画、慢块后补（2026-10-27 N20261027-234539-35）
      //
      // 主人问「每次打开都有一定时间的延迟，是因为它要重新计算吗？」——实测不是重算，是**卡着等慢块**：
      //   /api/sector_track.json 2.88MB（浏览器下载+解析）、/summary 读 10MB 轨迹文件。
      // 旧写法把五路 + 慢块全部 await 完才调一次 render()，于是冰点卡明明 27ms 就能拿到，也要等到 700-1300ms。
      // 现在拆两段：① 四个快端点（amv/panic/breadth/intraday）→ 立即首绘；② 轨迹摘要 + 全量轨迹 + 背景线 → 到了再绘。
      const load = async (force) => {
        if (!S.alive || S.loading) return;
        S.loading = true;
        if (force) S.html = Object.create(null);
        const errs = [];
        const take = (slot, data, label) => {
          if (data && data.__error) { errs.push(label + '：' + data.__error); return; }
          S[slot] = data;
        };
        try {
          const [lv, pn, br, it, ia] = await Promise.all([
            api.get('/api/amv_live.json').catch((e) => ({ __error: errText(e) })),
            api.get('/api/panic').catch((e) => ({ __error: errText(e) })),
            api.get('/api/breadth_880.json').catch((e) => ({ __error: errText(e) })),
            // 2026-10-25 C2b：会话行 / 成交额对比 / 审计明细的数据源（旧页 loadTab('intraday') 同一份）
            api.get('/api/intraday.json').catch((e) => ({ __error: errText(e) })),
            // 2026-10-30 标注 58：全A 880008 日涨跌（冰点卡背景层）——本插件自取，缓存 120s
            api.get('/index-series?n=80').catch((e) => ({ __error: errText(e) })),
          ]);
          if (!S.alive) return;
          take('amv', lv, 'AMV');
          take('panic', pn, '恐慌广度');
          take('breadth', br, '广度880');
          take('intraday', it, '盘中流水');
          take('allA', ia, '全A涨跌');
          render();                                    // ← 首屏：冰点/行情/成交额/审计先上屏
          setStatus(errs.length ? ('部分数据源读取失败 —— ' + errs.join('；')) : '');

          // 第二段：慢块。失败只影响自己那一块，不把已经画好的首屏抹白。
          const [tr] = await Promise.all([
            api.get('/summary').catch((e) => ({ __error: errText(e) })),
            loadSpark(S.intraday && S.intraday.market_quotes),
            /* 2026-10-26：【板块轨迹明细】卡已摘除（主人批「重复的」），逐码脉冲没宿主了 →
               /api/sector_intraday.json 不再按刷新节奏拉（函数与端点留着，恢复只需把 loadPulse() 加回来）。*/
            loadSector(force),
          ]);
          if (!S.alive) return;
          take('track', tr, '板块轨迹');
          render();                                    // ← 轨迹到位：日内演化 + 板块轮动成形
          setStatus(errs.length ? ('部分数据源读取失败 —— ' + errs.join('；')) : '');
        } finally {
          S.loading = false;
        }
      };
      S.load = load;

      // ---- AMV 预测实时源（2026-10-27）：按需刷新，不新增常驻。
      //
      // 链路：本卡 → `POST /api/amv_refresh`（core ops_run 固定动作，锁 + 20s join）
      //      → `amv_live.run_once(publish=True)` → 写入 amv_live.json → 本卡重新 GET。
      // 闸门全在 frontend/amvrefresh.js（纯函数 + 可注入 api/now，单测跑 tests/amv_refresh.mjs）：
      //   · 只在**连续竞价且工昨日**刷（午休/收盘后/周末零请求）；
      //   · 同一页最多 60s 一次（不是跟 20s 节拍盲 POST）；
      //   · S.loading / S.alive / document.hidden → 不刷（防「刷新→整卡取数→再刷新」递归）；
      //   · 首轮文件非今日也触发一次（mount 后立即，不等下一个 20s）。
      // 失败只落「刷新失败 HH:MM（原因）」，**不动 S.amv**，也不把旧值标成实时。
      const visible = () => !(typeof document !== 'undefined' && document.hidden);
      const amvRefresh = amvrefresh.createRefresher({
        api: api,
        now: () => new Date(),
        state: () => ({ alive: S.alive, loading: S.loading, visible: visible(), lv: S.amv }),
        onAmv: (doc) => {
          if (!S.alive) return;
          S.amv = doc;
          S.html = Object.create(null);   // 文件换了 → 持仓表/卡头徽标/预测浮层都要重绘
          renderHead();
          setHtml(S.el.holdBody, '<div class="it-split-hd">持仓（/api/amv_live.json · 只读）</div>'
            + holdHtml(S.amv), 'hold');
          paintEvo();
        },
        onNote: (text) => {
          const el = amvNote;
          if (!el) return;
          if (!text) { el.hidden = true; el.textContent = ''; return; }
          el.hidden = false;
          el.textContent = text;
        },
      });
      // 后台 timer 只在 mount 生命周期：unmount 必须清（不然旧实例继续 POST）。
      // 刻意用独立 20s timer、不侵入 core 的 refresh()：core 的 refresh 是「取数」语义，
      // 把写操作（POST）挂进去会跟着页面不可见/整卡 loading 一起变样。
      S.amvRefresh = amvRefresh;
      S.amvTimer = setInterval(() => { amvRefresh.tick(); }, REFRESH_MS);
      // node/jsdom 跑器（tests/*.mjs）里不要因为这个 timer 挂住进程；浏览器无 unref，不影响。
      if (S.amvTimer && typeof S.amvTimer.unref === 'function') S.amvTimer.unref();

      // 事件委托：点轨迹表的板块行 → 钉住/取消（钉住后在盘中脉冲看该板块当日轨迹）
      wrap.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const row = t.closest('tr.it-row');
        if (!row) return;
        const code = row.getAttribute('data-code');
        if (!code) return;
        const i = S.pins.indexOf(code);
        if (i >= 0) S.pins.splice(i, 1);
        else {
          if (S.pins.length >= PULSE_MAX) S.pins.shift();
          S.pins.push(code);
        }
        S.html = Object.create(null);
        loadPulse().then(() => { if (S.alive) render(); });
      });

      await load(false);
      // 首轮：文件停在上一交易日（如 10-23）也要刷一次，不等下一个 20s 拍。
      amvRefresh.tick();
    },

    async unmount() {
      const S = this._it;
      if (!S) return;
      S.alive = false;
      if (S.amvTimer) { clearInterval(S.amvTimer); S.amvTimer = null; }
      S.amvRefresh = null;
      if (S.sector) sector.unbind(S.sector);
      S.sector = null;
      if (S.evo) evo.unbind(S.evo);
      S.evo = null;
      if (S.research) research.unbind(S.research);
      S.research = null;
      if (S.audit) audit.unbind(S.audit);
      S.audit = null;
      S.html = Object.create(null);
      this._it = null;
    },

    async refresh() {
      const S = this._it;
      if (!S || !S.alive || S.loading) return;
      // 2026-10-28（N20261027-234539-35）：收盘后 / 盘前 / 周末不自动取数。
      // 主人原话：「收盘期间就没必要刷新了，如果打开时间是 16:10（收盘自动取数之后）
      // 就直接取收盘后的数值进行一次最后的计算」——首挂载照旧取一次（16:10 后取到的就是收盘定格值），
      // 之后的 20s 节拍不再发请求。
      if (!inAutoRefreshWindow()) return;
      await S.load(false);
      // 研究卡自带 TTL（盘中 30s / 盘外 120s，旧页 4337），由它自己判缓存；
      // 这里只给入口，不代替它决定要不要重拉。
      if (S.research) S.research.load(false);
    },
  }],
};