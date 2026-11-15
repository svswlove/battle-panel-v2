/**
 * intraday 卡 · 实时行情 KPI（照旧页搬）
 *
 * 行为基准 = engine/web/index.html：
 *   mqHtml        4677-4685（10 个 .kpi.mq-kpi 小卡 + 迷你背景线骨架）
 *   kCode         2920-2927（代码列可跳 K 线，data-open-kline 占位）
 *   _sparkInject  5119-5128 + loadIndexSpark 5129-5146（近 60 日 AMV + MA10 → path d）
 *   _amvAlias     5270-5271（880008.SH → ALL，spark 端点的码表口径）
 *   样式           1663-1667（.mq-kpi / .mq-spark / .mq-spark-amv / .mq-spark-ma）
 *
 * **本块唯一一处必须偏离旧页的地方（任务书点名）**：
 *   旧页是「先渲空 path、异步回填 d 属性」——renderIntraday 每 3s 重建 innerHTML，
 *   回填的 path 会随重建丢失，所以它还专门写了 _sparkInject 做「重建后同步回填缓存」。
 *   卡片是「先取数、再渲 DOM」（setHtml 签名缓存，内容不变不碰 DOM），若照抄空 path，
 *   回填后的 d 会被下一次 setHtml 冲掉、且没有第二次回填的机会 → 背景线必然闪没。
 *   故：spark 与行情**同一拍取数**，渲染时直接把 d 写进 path。零回填、零闪烁、零缓存态。
 *
 * 另一处（安全阀）：spark 端点失败/超时 → 整块仍照常渲染行情（价格/涨跌幅是主体），
 * 只是背景线留空 —— 与旧页「data 为 null 就 return」的结果一致，但不会拖住行情。
 *
 * 颜色只走 token；类名沿用旧页（.mq-kpi / .mq-spark_*），样式在 quotes.css.js 里加 .it-card 作用域。
 */

import { esc, num, pct, clsUp as cls } from './format.js';
import { kCode } from './audit.js';

/** 旧页 5270 AMV_CODE_ALIAS：指数行情码 → amv_live / spark 端点的码（全A = 880008.SH 全A等权） */
export const AMV_CODE_ALIAS = { '880008.SH': 'ALL' };

/** 旧页 5271 _amvAlias：取别名（没有就原样） */
export const amvAlias = (code) => AMV_CODE_ALIAS[code] || code;

/** 旧页 5146 / 5135：spark 20s 轻缓存（背景线慢变，不必每拍重拉） */
export const SPARK_TTL_MS = 20000;

/**
 * 旧页 4678-4685 单个行情小卡。
 * 与旧页的差别只有一处：\`<path d="">\` 直接写 d（见文件头），其余 DOM 结构逐字照抄。
 * 全A 那一行旧页带 \`<span class="regime-mark" data-regime>\`（由 loadAmvLive 事后填多空标记）——
 * 本卡不接那块（要多取 3 个端点），故不产出该占位：留一个空 span 等于给用户一个永远不亮的灯。
 */
export function mqCard(q, spark) {
  const idx = amvAlias(q.code);
  const d = (spark && spark[idx]) || {};
  return '<div class="kpi mq-kpi" data-idx="' + esc(idx) + '">'
    + '<svg class="mq-spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">'
    + '<path class="mq-spark-amv" d="' + esc(d.amv || '') + '"></path>'
    + '<path class="mq-spark-ma" d="' + esc(d.ma10 || '') + '"></path></svg>'
    + '<div class="k">' + kCode(q.code, q.name, q.name)
    + ' <span class="muted" style="font-size:0.6875rem">' + esc(q.code) + '</span></div>'
    + '<div class="v">' + num(q.price, 2) + '</div>'
    + '<div class="' + cls(q.change_pct) + '">' + pct(q.change_pct) + '</div>'
    + '<div class="src">' + esc(q.quote_time || '') + ' ｜ ' + esc(q.source || '') + '</div>'
    + '</div>';
}

/**
 * 旧页 4677-4685 整块：\`<div class="grid">\` + N 个小卡。
 * 无行情时给「行情源不可达（见 meta.source）」——照抄，不造占位数字。
 * （2026-11-04 文案中性化：原句带 QMT 等待型表述，QMT 已不可用但常态不应显示成故障）
 */
export function mqHtml(quotes, spark) {
  const mq = quotes || [];
  if (!mq.length) {
    return '<p class="muted">行情源不可达（见 meta.source）</p>';
  }
  return '<div class="grid">' + mq.map((q) => mqCard(q, spark)).join('') + '</div>';
}

/** 旧页 5132-5133：spark 请求的码表 = 各小卡的 data-idx（已过别名），去重保序 */
export function sparkCodes(quotes) {
  const out = [];
  (quotes || []).forEach((q) => {
    const c = amvAlias(q && q.code);
    if (c && out.indexOf(c) < 0) out.push(c);
  });
  return out;
}
