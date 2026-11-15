/**
 * 旧页对齐运行器（给 tests/test_audit.py 调用；也可单独 node 跑）
 *
 * 做什么：把旧页 engine/web/index.html 的**原始行**用 node:vm 原样跑起来，与
 * frontend/audit.js 的输出在同一份真实载荷上逐字节比。
 *
 * 关键设计：**不重抄旧页代码**。会话行、成交额对比、审计明细 5 表（均按可辨锚点定位，见下方 legacy_anchor）
 * 都是按锚点切出来、原样塞进 vm 执行的——抄一遍就多一份走样的可能。
 * 2026-10-30：原先按硬编码行号切片，旧页整体下移 2 行后切在表达式中间（node SyntaxError，
 * pytest 在比对之前就判红）；现在行号一律不写，改由 legacy_anchor.mjs 按锚点定位。
 *
 * 比什么（每项的旧页取数锚点写在 legacyBox 里，不写行号）：
 *   ① M 映射表：本组搬的 8 张子表，逐子表逐键逐值
 *   ② cn / cnSigSource：含「表里没有 → 原样」「空 → —」两条边界
 *   ③ table / kCode
 *   ④ 会话行（`<div class="sess-line">` 整行）：逐字节
 *   ⑤ 成交额对比（prevReal 声明 + toHtml 模板串 + _br880AmountTxt）：3 行 KV + 2 条 src 逐字
 *   ⑥ 审计明细 5 表（`<details class="fold">` 内的 card(...) 链）：5 张卡的 HTML 逐字节 + 信号流水 392 个单元格逐字
 *
 * 明确不比（本单不做，已在 HANDOFF 说明）：成交额分时图（旧页 5189-5216，16 字）与
 * 涨跌家数卡（旧页 4700，v2 breadthHtml 已有）——两者在 vm 里桩成空串。
 *
 * 输出：最后一行 JSON（pytest 取最后一行）。
 */
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeSeg, UTIL_LINES } from './legacy_anchor.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// HERE = panel-v2/app/plugins/intraday/tests → 上溯 4 层是 projects/panel-v2，再上 2 层是仓库根 OH-WorkSpace
const PANEL = path.resolve(HERE, '../../../..');
const REPO = path.resolve(PANEL, '../..');
const LEGACY = path.join(REPO, 'engine', 'web', 'index.html');
const FIXTURE = path.join(HERE, 'fixtures/intraday_payload.json');

const A = await import(new URL('../frontend/audit.js', import.meta.url).href);

/** 旧页源码切片：按锚点定位，返回整段原文（不写行号） */
const seg = makeSeg(LEGACY);

/** 去掉表达式结尾的续行加号（切出来的片段末尾是 " +"，接下一行的） */
const dropPlus = (s) => s.replace(/\+\s*$/, '');

/**
 * 在 vm 里跑旧页真身。
 * 只给最小桩：console / 两个本单不做的块（分时图、家数卡）。
 */
export function legacyBox(payload, breadth) {
  const src = [
    seg.each(UTIL_LINES),                                    // esc / num / pct / cls 四行
    seg.upto(/^function kName\(/, /^function kNameRaw\(/),   // kName / kTsPlayable / kNameRaw
    seg.upto(/^function kCode\(/, /^const fmtAmount = /),    // kCode / fmtAmount
    seg.upto(/^const card = /, /^const table = /),           // card / table
    seg.upto(/^const M = \{/, /^const cnSigSource = /),      // M / cn / cnSigSource
    seg.block(/^function _br880AmountTxt\(/),
  ].join('\n');

  const box = { console: { info() {}, warn() {}, log() {} }, _breadth880: breadth || null };
  vm.createContext(box);

  // 会话行：原文就是一个模板串表达式，去掉结尾续行加号即可求值
  const sessExpr = dropPlus(seg.line(/<div class="sess-line">/));
  const sessFn = 'globalThis.__sess = function(d){ var s = d.session||{}, sum = d.day_summary||{};'
    + '\n return ' + sessExpr + '; };';
  // 成交额对比：prevReal 声明 + toHtml 模板串原文
  const toFn = 'globalThis.__to = function(d){ var to = d.market_turnover||{};'
    + '\n' + seg.line(/const prevReal = /)
    + '\n' + seg.stmt(/const toHtml = `/)
    + '\n return toHtml; };';
  // 审计明细 5 表：载荷声明 + <details class="fold"> 内的 card(...) 链
  const auditDecl = seg.span(/const s = d\.session/, /cmd = d\.commands_b/);
  const auditExpr = dropPlus(seg.slice(
    seg.index(/<details class="fold">/) + 1, seg.index(/<\/details>/) - 1));
  const auditFn = 'globalThis.__audit = function(d){'
    + '\n' + auditDecl + '\n return ' + auditExpr + '; };';

  // 一次性跑完：vm 的顶层 const 不会挂到全局对象上（research_align.mjs 同款处理），
  // 故在**同一个脚本里**显式导出，供宿主取用。
  vm.runInContext(src + '\n' + [
    'function _br880CardHtml(){ return "<!--br880-card-skipped-->"; }',
    'function _turnoverChart(){ return "<!--turnover-chart-skipped-->"; }',
    sessFn,
    toFn,
    auditFn,
    'globalThis.__M = M;',
    'globalThis.__cn = cn;',
    'globalThis.__cnSigSource = cnSigSource;',
    'globalThis.__table = table;',
    'globalThis.__card = card;',
    'globalThis.__kCode = kCode;',
    'globalThis.__fmtAmount = fmtAmount;',
  ].join('\n'), box, { filename: 'legacy-audit.js' });
  return box;
}

/** 归一化：只抹掉任务书四条约束 / 本单三处偏离强制造成的差异，其余必须逐字节相同 */
export function normalize(h) {
  return h
    // 偏离 a：旧页 <div class="card"><h2> → 卡片 .it-audit-card（同结构，只换类名）
    .replace(/<div class="card"><h2>/g, '<div class="it-audit-card"><h2>')
    // 偏离 b：旧页 to-val 的 id="cur880Amt"（事后按 id 回填）→ 卡片同拍渲染，不需要 id
    .replace(/ id="cur880Amt"/g, '')
    // 偏离 c：kNameRaw 的内联 onclick → data-open-kline 占位（旧页 2880 与卡片同一意图）
    .replace(/ onclick="kNameClick\(this,event\)"/g, '')
    .replace(/ data-open-name="[^"]*"/g, '')
    .replace(/ data-kname="([^"]*)" title="/g, ' data-kname="$1" data-open-kline="$1" title="')
    .replace(/>\s+</g, '><')
    .replace(/\s+/g, ' ')
    .trim();
}

/** 逐块抽取（不做整串结构比对，避免分时图/家数卡被删导致的错位） */
function blocks(h, re) {
  return (h.match(re) || []).map(normalize);
}

export function run() {
  const payload = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
  // 「当前累计」读的是广度 880 的 today.amount（旧页 5171），与 intraday.total_amount 是两个口径。
  // 用固定桩值，两边同源即可比。
  const breadth = { today: { amount: 1972854849536 }, prev: {} };

  const box = legacyBox(payload, breadth);
  const out = {};

  // ① M：本组搬的 8 张子表逐键比（旧页 2962-3004 里还有 30+ 张本组不用，不搬也不比）
  out.m = Object.keys(A.M).map((key) => {
    const old = box.__M[key];
    const cur = A.M[key];
    const oldKeys = old ? Object.keys(old) : [];
    const curKeys = cur ? Object.keys(cur) : [];
    const missing = oldKeys.filter((k) => curKeys.indexOf(k) < 0);
    const extra = curKeys.filter((k) => oldKeys.indexOf(k) < 0);
    const diff = oldKeys.filter((k) => curKeys.indexOf(k) >= 0 && cur[k] !== old[k])
      .map((k) => [k, old[k], cur[k]]);
    return { key, exists: !!old, n: oldKeys.length,
      ok: !!old && !missing.length && !extra.length && !diff.length, missing, extra, diff };
  });

  // ② cn / cnSigSource 边界
  out.cn = [['phase', 'CLOSED'], ['phase', 'NOPE_X'], ['state', 'FROZEN'], ['sigKind', 'BUY'],
    ['channel', 'A'], ['sigKind', ''], ['channel', null], ['sigStatus', 'BLOCKED'],
    ['src2', 'QMT'], ['direction2', 'STRONG']]
    .map(([k, v]) => ({ k, v, legacy: box.__cn(box.__M[k], v), cur: A.cn(A.M[k], v) }));
  out.cnSigSource = ['manual', 'conditional', 'SW-20261023-001', 'BP-9', '', null, 'risk', 'qmt']
    .map((v) => ({ v, legacy: box.__cnSigSource(v), cur: A.cnSigSource(v) }));

  // ③ table / kCode
  out.table = {
    emptyLegacy: box.__table(['A', 'B'], []),
    emptyCur: A.table(['A', 'B'], []),
    rowsLegacy: box.__table(['A', 'B'], [['1', '2'], ['3', '4']]),
    rowsCur: A.table(['A', 'B'], [['1', '2'], ['3', '4']]),
  };
  out.kCode = ['002733.SZ', '600519', '100001', '', null, '830799.BJ']
    .map((c) => ({ c, legacy: box.__kCode(c, c, '某票'), cur: A.kCode(c, c, '某票') }));

  // ④ 会话行：`<div class="sess-line">` 整行就是一段文本，卡片运行时用 textContent 写同一段
  const legacySess = box.__sess(payload);
  out.session = {
    legacyHtml: normalize(legacySess),
    curHtml: normalize(A.sessionLine(payload)),
    legacyText: normalize(legacySess.replace(/^<div class="sess-line">/, '').replace(/<\/div>$/, '')),
    curText: A.sessionText(payload),
  };
  // 冻结原因分支（真实载荷 frozen_reason=null，单独造一条）
  const frozen = Object.assign({}, payload, { session: Object.assign({}, payload.session, { frozen_reason: '急停' }) });
  out.sessionFrozen = {
    legacy: normalize(box.__sess(frozen).replace(/^<div class="sess-line">/, '').replace(/<\/div>$/, '')),
    cur: A.sessionText(frozen),
  };

  // ⑤ 成交额对比：3 行 KV + 2 条 src 逐字（分时图卡 / 家数卡本单不做，不参与比对）
  const mt = payload.market_turnover || {};
  const legacyTo = box.__to(payload);
  const curTo = A.turnoverHtml(mt, breadth);
  out.turnover = {
    legacyKV: blocks(legacyTo, /<div class="to-kv">[\s\S]*?<\/div>/g),
    curKV: blocks(curTo, /<div class="to-kv">[\s\S]*?<\/div>/g),
    legacySrc: blocks(legacyTo, /<div class="src">[\s\S]*?<\/div>/g),
    curSrc: blocks(curTo, /<div class="src">[\s\S]*?<\/div>/g),
    legacyTitle: (legacyTo.match(/<div class="k">成交额对比<\/div>/) || []).length,
    curTitle: (curTo.match(/<div class="k">成交额对比<\/div>/) || []).length,
  };
  // 「昨日同时刻」非真值分支（prev_time_source !== qmt-1m → 必须显「无数据」，不拿估算充真）
  const mtFake = Object.assign({}, mt, { progress: 0.5, prev_time_source: 'estimate', prev_same_time_diff_pct: 9.9 });
  const legacyFake = box.__to(Object.assign({}, payload, { market_turnover: mtFake }));
  const curFake = A.turnoverHtml(mtFake, breadth);
  out.turnoverFake = {
    legacyKV: blocks(legacyFake, /<div class="to-kv">[\s\S]*?<\/div>/g),
    curKV: blocks(curFake, /<div class="to-kv">[\s\S]*?<\/div>/g),
  };
  // 盘中态（progress<1）：旧页与卡片都走 3 行分支，可逐字节比。
  // 收盘态（progress>=1）卡片 2026-10-28 有意改为 2 行（今日全天/昨日全天），
  // 旧页恒为 3 行——那是规格偏离，不拿逐字节比对守（由 test_turnover_compare_three_rows 守）。
  const mtOpen = Object.assign({}, mt, { progress: 0.5 });
  const legacyOpen = box.__to(Object.assign({}, payload, { market_turnover: mtOpen }));
  const curOpen = A.turnoverHtml(mtOpen, breadth);
  out.turnoverOpen = {
    legacyKV: blocks(legacyOpen, /<div class="to-kv">[\s\S]*?<\/div>/g),
    curKV: blocks(curOpen, /<div class="to-kv">[\s\S]*?<\/div>/g),
    legacySrc: blocks(legacyOpen, /<div class="src">[\s\S]*?<\/div>/g),
    curSrc: blocks(curOpen, /<div class="src">[\s\S]*?<\/div>/g),
  };

  // ⑥ 审计明细 5 表
  const legacyAudit = box.__audit(payload);
  const curAudit = A.auditHtml(payload);
  // 两侧的卡容器类名不同（旧页 .card / 卡片 .it-audit-card，见 audit.js 文件头偏离 a），
  // 切分时认两种，再统一归一成旧页写法比对。
  const splitCards = (h) => h.split(/<div class="(?:card|it-audit-card)"><h2>/).slice(1)
    .map((x) => normalize('<div class="card"><h2>' + x));
  const curInner = curAudit
    .replace(/^<details[^>]*>/, '')
    .replace(/<summary>[\s\S]*?<\/summary>/, '')
    .replace(/<\/details>$/, '');
  out.audit = {
    legacyCards: splitCards(legacyAudit),
    curCards: splitCards(curInner),
    legacyTr: (legacyAudit.match(/<tr>/g) || []).length,
    curTr: (curInner.match(/<tr>/g) || []).length,
    legacyEmpty: (legacyAudit.match(/（空）/g) || []).length,
    curEmpty: (curInner.match(/（空）/g) || []).length,
    detailsOpen: /<details[^>]*\sopen/.test(curAudit),
    summary: (curAudit.match(/<summary>([\s\S]*?)<\/summary>/) || [])[1] || '',
    realSignals: (payload.signals || []).length,
    realOther: (payload.strength_snapshots || []).length + (payload.env_adjustments || []).length
      + (payload.events || []).length + (payload.commands_b || []).length,
  };
  out.audit.cardsEqual = out.audit.legacyCards.length === out.audit.curCards.length
    && out.audit.legacyCards.every((v, i) => v === out.audit.curCards[i]);

  // 信号流水 49 行 × 8 列 = 392 格逐字比（整串比会被空白差异淹没，格子比能指到具体哪一格错）
  const cellText = (h) => (h.match(/<td>[\s\S]*?<\/td>/g) || []).map((x) => normalize(x));
  out.cells = {
    legacy: cellText(out.audit.legacyCards[0] || ''),
    cur: cellText(out.audit.curCards[0] || ''),
  };
  out.cells.n = out.cells.cur.length;
  out.cells.equal = out.cells.legacy.length === out.cells.cur.length
    && out.cells.legacy.every((v, i) => v === out.cells.cur[i]);
  // 首个不同的格子（对齐失败时 pytest 打印它，不用人肉 diff 400 格）
  out.cells.firstDiff = -1;
  for (let i = 0; i < out.cells.legacy.length; i += 1) {
    if (out.cells.legacy[i] !== out.cells.cur[i]) { out.cells.firstDiff = i; break; }
  }

  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  console.log(JSON.stringify(run()));
}
