// sector 指数层 + 区间提示 单测（2026-10-27）
//   node app/plugins/sector/tests/js/sector_index.test.mjs
// 覆盖标注 N20261027-011947-26 / N20261027-012116-27：
//   1 compute.computeAllRows 全量返回（不受 10% 切片限制）
//   2 buildPanel(rowsAll) 渲染 12 行且选中行带 .sel
//   3 frontend.js 指数分支走全量，点行 = 切右栏、点名称/右键 = 弹卡（2026-11-05 标注 81 恢复右栏）
//   4 .rr-span 已删（元素 / 写入 / CSS）
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, '../..');
const ROOT = path.resolve(PLUGIN, '../../../..');           // OH-WorkSpace
const require = createRequire(path.join(ROOT, 'package.json'));
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.HTMLElement = dom.window.HTMLElement;

const C = await import(new URL('../../frontend/compute.js', import.meta.url).href);
const B = await import(new URL('../../frontend/board.js', import.meta.url).href);

const CODES = ['000001.SH', '000016.SH', '000300.SH', '000510.CSI', '000688.SH', '000852.SH',
  '000905.SH', '399001.SZ', '399006.SZ', '399303.SZ', '880823.SH', 'ALL'];
const NAMES = ['上证综指', '上证50', '沪深300', '中证A500', '科创50', '中证1000',
  '中证500', '深证成指', '创业板指', '国证2000', '微盘股', '沪深全A'];
const DAYS = 80;

function fakeData() {
  const catalog = {
    index: CODES.map((code, i) => ({ code, name: NAMES[i], level: 'INDEX', l1: null, exclude: false })),
  };
  const dates = [];
  for (let i = 0; i < DAYS; i++) {
    const d = new Date(Date.UTC(2026, 6, 1) + i * 86400000);
    dates.push(d.toISOString().slice(0, 10));
  }
  const codes = CODES.slice();
  const weight = [];
  const total = [];
  for (let i = 0; i < DAYS; i++) {
    total.push(1e12);
    weight.push(codes.map((c, j) => 1e6 + ((i * 7 + j * 13) % 97) * 1000));
  }
  return { catalog, rankRecent: { dates, total, codes, weight } };
}

// ---------------------------------------------------------------- 1 全量行
{
  const { catalog, rankRecent } = fakeData();
  const inst = C.createCompute({
    catalog, rankRecent, level: 'INDEX', sortKey: 'd', winN: 3,
    drillL1: null, flowMap: {}, divMap: {}, dayB: DAYS - 1,
  });
  assert.equal(typeof inst.computeAllRows, 'function', 'compute 必须导出 computeAllRows（指数层全量）');
  const all = inst.computeAllRows(DAYS - 1);
  assert.equal(all.warmup, false);
  assert.equal(all.rows.length, 12, '指数层必须全量 12 个，不能被 10% 切片切成 6 个');
  for (const r of all.rows) {
    assert.ok(r.code && r.name, '每行都要有 code/name');
    assert.equal(r.noBadge, true, '指数层不出连N/NEW 徽标（8002 idxPoolRows 同口径）');
    assert.ok('rank' in r && 'rankPrev' in r && 'score' in r, '每行补齐 rank/rankPrev/score');
  }
  // 与 10% 切片对照：computeBoardLists 仍然只给 10%
  const lists = inst.computeBoardLists(DAYS - 1);
  assert.ok(lists.topIn.length + lists.exitsIn.length < 12, '其他口径仍是 10% 切片，本用例只改指数层');
  // 全量按当前排序键降序（d 大在前），但全A 固定首位（8002 同款）
  assert.equal(all.rows[0].code, 'ALL', '全A 固定首位');
  const rest = all.rows.slice(1);
  for (let i = 1; i < rest.length; i++) {
    assert.ok(rest[i - 1].d >= rest[i].d, '除全A 外按当前排序键降序');
  }
}

// ---------------------------------------------------------------- 2 渲染 12 行 + 选中
{
  const { catalog, rankRecent } = fakeData();
  const inst = C.createCompute({
    catalog, rankRecent, level: 'INDEX', sortKey: 'd', winN: 3,
    drillL1: null, flowMap: {}, divMap: {}, dayB: DAYS - 1,
  });
  const all = inst.computeAllRows(DAYS - 1);
  const panel = B.buildPanel({
    side: 'in', title: '各大指数 · 全部（12）', C: inst,
    state: { dayB: DAYS - 1, winN: 3, drillL1: null, sortKey: 'd', sortDir: -1 },
    flowMap: {}, idxMode: true,
    rowsAll: all.rows, selCode: 'ALL',
    onSort: () => {}, onOpen: () => {}, onSelect: () => {},
    tipShow: () => {}, tipHide: () => {},
  });
  document.body.innerHTML = '';
  document.body.append(panel);
  const rows = panel.querySelectorAll('li.row');
  assert.equal(rows.length, 12, '指数清单必须渲染 12 行');
  assert.equal(panel.querySelectorAll('li.row.sel').length, 1, '当前选中指数恰好 1 行 .sel');
  assert.equal(panel.querySelector('li.row.sel').dataset.code, 'ALL', '默认选中全A');
  assert.ok(!panel.querySelector('.col-hd .ch-wt') || panel.querySelector('.col-hd .ch-wt').textContent === '权重',
    '列头结构保持 8002 口径（隐藏交给 CSS）');
}

// ---------------------------------------------------------------- 3/4 源码契约
{
  const frontend = fs.readFileSync(path.join(PLUGIN, 'frontend.js'), 'utf8');
  const css = fs.readFileSync(path.join(PLUGIN, 'frontend/legacy.css.js'), 'utf8');
  const detail = fs.readFileSync(path.join(PLUGIN, 'frontend/detail.js'), 'utf8');

  assert.ok(frontend.includes('computeAllRows'), 'frontend 指数分支必须用全量行');
  // 2026-11-05（标注 81）：11-03 删内联右栏被判定为误改——右栏（含右上角 ↻ 重算）恢复；
  //   点行换右栏，点名称/右键菜单弹卡。
  assert.ok(frontend.includes('li.idx-detail') || frontend.includes("class: 'idx-detail'"),
    '指数右栏内联详情必须存在（li.idx-detail）');
  assert.ok(frontend.includes('mountDetail'), '右栏走 mountDetail 复用同一套卡片渲染');
  assert.ok(/onSelect:\s*\(code, nm\)\s*=>\s*\{\s*this\.idxSel = code;\s*this\._renderIdx\(\);\s*\}/.test(frontend),
    '指数行点击 = 只切右栏（不再顺带弹卡）');
  assert.ok(/onOpen:\s*\(code, nm\)\s*=>\s*this\._openDetail\(code, nm, 'INDEX'\)/.test(frontend),
    '指数行仍保留弹详情卡入口（名称/右键）');
  assert.ok(!frontend.includes('lists.topIn.length + lists.exitsIn.length'),
    '标题不能再用 10% 切片计数（6 个）冒充全量');
  assert.ok(css.includes('.panels.idx'), '需要 .panels.idx 样式');
  assert.ok(/\.panels\.idx\{grid-template-columns:minmax\(0,520px\) minmax\(0,1fr\)\}/.test(css),
    '左栏 520px + 右栏 1fr（右栏恢复后不能还是单列满宽）');

  // 标注 27：.rr-span 元素/写入/CSS 全删
  assert.ok(!frontend.includes('rr-span'), 'frontend.js 不得再创建 .rr-span');
  assert.ok(!frontend.includes('区间 \' + span + \' 交易日'), '不再写「区间 N 交易日」');
  assert.ok(!css.includes('rr-span'), 'legacy.css.js 不得再留 .rr-span 规则');

  // 2026-11-01 主人：右栏去掉序号/评分/名次，只留 7 列；内联右栏右键出板块菜单
  assert.ok(!/k: 'seq'/.test(detail) && !/k: 'score'/.test(detail) && !/k: 'sec_rank'/.test(detail),
    'CARD_COLS 不得再有 seq / score / sec_rank 列');
}
console.log('sector index/layout: all assertions passed');
