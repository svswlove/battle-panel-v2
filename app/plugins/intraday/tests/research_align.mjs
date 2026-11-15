/**
 * 旧页对齐运行器（给 tests/test_research.py 调用；也可单独 node 跑）
 *
 * 做什么：把旧页 engine/web/index.html 的 _rsHtml() 用 node:vm 原样跑起来，
 * 与前端模块 frontend/research.js 的 html() 在**同一份真实载荷**上逐字节比。
 *
 * 为什么单独成文件而不是塞进 pytest 的字符串：脚本要读旧页源码片段 + 两份 138KB/199KB
 * 载荷，内联进命令行会超 Windows 命令长度上限（test_caliber.py 已踩过 WinError 206）。
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
const LEGACY = path.join(REPO, 'engine', 'web', 'index.html');   // OH-WorkSpace/engine/web/index.html

const { default: _unused, ...A } = await import(
  new URL('../frontend/research.js', import.meta.url).href);

const FIX_BOARD = path.join(HERE, 'fixtures/amv_research_l2_board.json');
const FIX_ALL = path.join(HERE, 'fixtures/amv_research_l2_all.json');

// 旧页片段（按锚点定位，2026-10-30 起不写行号）：
//   · 工具函数四行 esc/num/pct/cls + kName/kTsPlayable/kNameRaw（研究表名称列用）
//   · 研究模块级状态（let _rs …）+ rsSet
//   · loadResearch … _rsHtml（研究段整块，含 rsHead/rsRows/rsPartsHtml）
//   window.__lastErr 收集器（依赖 window）夹在两者之间，不在切片范围内
const segSrc = makeSeg(LEGACY);
const RS_DECLS = /^let _rs = null/;
const RS_SET = /^function rsSet\(/;
const RS_LOAD = /^async function loadResearch\(/;
const RS_HTML = /^function _rsHtml\(/;

export function legacySource() {
  return [segSrc.each(UTIL_LINES), segSrc.upto(/^function kName\(/, /^function kNameRaw\(/),
    segSrc.upto(RS_DECLS, RS_SET), segSrc.upto(RS_LOAD, RS_HTML)].join('\n');
}

/** 在 vm 里跑旧页源码，返回 _rsHtml() 的结果（Date 固定，两边可比） */
export function legacyPaint(src, payload, lvl, srt, scope, sel, nowMs) {
  const box = {
    console: { info() {}, warn() {}, log() {} },
    setInterval: () => 0, clearInterval: () => {}, setTimeout: () => 0, clearTimeout: () => {},
    document: { hidden: false, getElementById: () => null, querySelector: () => null },
    AbortController,
  };
  vm.createContext(box);
  vm.runInContext(src + '\n' + [
    'globalThis.__RealDate = Date;',
    'globalThis.Date = class extends __RealDate {',
    '  constructor(...a){ if (!a.length) super(' + nowMs + '); else super(...a); }',
    '};',
    'globalThis.__paint = (p, lvl, srt, scope, sel) => {',
    '  _rs = p; _rsLvl = lvl; _rsSrt = srt; _rsScope = scope; _rsSel = sel; return _rsHtml();',
    '};',
  ].join('\n'), box, { filename: 'legacy-rs.js' });
  return box.__paint(payload, lvl, srt, scope, sel);
}

/**
 * 规范化掉「任务书四条约束强制造成的差异」，其余必须逐字节相同。
 * 只规范化下面 5 类；其他任何差异都会留在 equal=false 里并打印首个不同点。
 */
export function normalize(h) {
  return h
    .replace(/ onclick="rsSet\('srt','([a-z_]+)'\)"/g, ' data-act="rs-srt" data-v="$1"')
    .replace(/ onclick="rsSet\('sel','([^']*)'\)"/g, ' data-act="rs-sel" data-v="$1"')
    .replace(/ onclick="rsSet\('lvl','([A-Z0-9]+)'\)"/g, ' data-act="rs-lvl" data-v="$1"')
    .replace(/ onclick="rsSet\('scope','([a-z]+)'\)"/g, ' data-act="rs-scope" data-v="$1"')
    .replace(/ onscroll="_rsScroll\['[bm]'\]=this\.scrollTop"/g, '')
    .replace(/ onclick="kNameClick\(this,event\)"/g, '')
    .replace(/ data-open-name="[^"]*"/g, '')
    // 2026-10-26 主人要「返回全A 忙碌态」→ 按钮多了三样：data-idle / data-busy-txt / aria-busy。
    // 旧页没这些（所以才是缺陷），属于有意偏离，归一化时抹掉。
    .replace(/ data-idle="[^"]*"/g, '')
    .replace(/ data-busy-txt="[^"]*"/g, '')
    .replace(/ aria-busy="(?:true|false)"/g, '')
    .replace(/ data-kname="([^"]*)" title="/g, ' data-kname="$1" data-open-kline="$1" title="')
    .replace(/>\s+</g, '><')
    .replace(/\s+/g, ' ');
}

/**
 * 2026-11-06 主人：全A 视图要显示 AMV 贡献分项分解（读后端 `all_parts`）。
 * 旧页全A 模式整块不画，属于**有意偏离**；比对前把新渲染的这块剥掉，其余仍须逐字节相同。
 * 只在 scope=all 时调用；board 模式两边都有这块，不剥。
 */
export function stripAllParts(h) {
  const start = h.indexOf('<div class="rs-parts">');
  if (start < 0) return h;
  const f = h.indexOf('<div class="rs-parts-f">', start);
  if (f < 0) return h;
  const end = h.indexOf('</div></div>', f);
  if (end < 0) return h;
  return h.slice(0, start) + h.slice(end + '</div></div>'.length);
}

export function run() {
  const src = legacySource();
  const payload = JSON.parse(fs.readFileSync(FIX_BOARD, 'utf8'));
  const allScope = JSON.parse(fs.readFileSync(FIX_ALL, 'utf8'));
  const now = new Date(2026, 9, 26, 10, 0, 0);
  const nowMs = now.getTime();

  const cases = [
    ['L2/contrib/board', payload, 'L2', 'contrib', 'board', payload.sel],
    ['L2/px_pct/board', payload, 'L2', 'px_pct', 'board', payload.sel],
    ['L2/amv_pct/board', payload, 'L2', 'amv_pct', 'board', payload.sel],
    ['L2/contrib/all', allScope, 'L2', 'contrib', 'all', allScope.sel],
    ['L2/amv_pct/all', allScope, 'L2', 'amv_pct', 'all', allScope.sel],
  ];

  const rows = cases.map(([label, p, lvl, srt, scope, sel]) => {
    const a = normalize(legacyPaint(src, p, lvl, srt, scope, sel, nowMs));
    const bNew = A.html({ rs: p, lvl, srt, scope, sel, err: '' }, now);
    // 全A 模式的新增分项块是本次有意偏离（见 stripAllParts 注释）
    const b = normalize(scope === 'all' ? stripAllParts(bNew) : bNew);
    let at = -1;
    if (a !== b) { at = 0; while (at < a.length && a[at] === b[at]) at += 1; }
    const count = (h, re) => (h.match(re) || []).length;
    return {
      label, equal: a === b, lenA: a.length, lenB: b.length, at,
      ctxA: a.slice(Math.max(0, at - 60), at + 140),
      ctxB: b.slice(Math.max(0, at - 60), at + 140),
      boards: count(b, /data-ctx="sector"/g),
      members: count(b, /data-ctx="stock"/g),
      cols: count(b, /<td class="(?:rk|nm)"/g) ? 6 : 0,
    };
  });

  // 空态 / 失败态：旧页 _rsHtml 的两条早退分支
  const emptyLegacy = legacyPaint(src, null, 'L2', 'contrib', 'board', '', nowMs);
  const emptyNew = A.html({ rs: null, err: '', lvl: 'L2', srt: 'contrib', scope: 'board', sel: '' }, now);
  const errNew = A.html({ rs: null, err: '接口不可达', lvl: 'L2', srt: 'contrib', scope: 'board', sel: '' }, now);

  return {
    rows, emptyLegacy, emptyNew, errNew,
    counts: { boards: (payload.boards || []).length, members: (payload.members || []).length,
              allMembers: (allScope.members || []).length },
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  console.log(JSON.stringify(run()));
}
