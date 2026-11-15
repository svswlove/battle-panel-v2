// 标注清单 / 生命周期面板的纯函数单测（2026-10-27）。
//
// 只测 `app/web/v2_notes_panel.js` 里**不碰 DOM、不发请求**的那一层：
//   分组（page 分组 + 退役页置后 + 组内 created_at 倒序）、阶段筛选、搜索、
//   下一动作映射、进度段、page→卡 key 解析、时间/时间线格式化、ref 类型判定。
//
// 为什么这些必须单测而不是只看截图：它们决定「点哪个按钮会发什么 to」，
// 一旦映射错，409 会静默把标注推到错误阶段（写的是生产 ui_notes.json）。
//
// 用法：node app/tests/js/v2_notes_panel.test.mjs

import assert from 'node:assert/strict';

const M = (await import(new URL('../../web/v2_notes_panel.js', import.meta.url).href)).default;

let n = 0;
const it = (name, fn) => { fn(); n++; console.log('ok ' + n + ' - ' + name); };

// 固定假数据：两条 v2 卡、一条整壳、两条旧页（退役）
const NOTES = [
  { id: 'N1', page: 'v2:card:intraday::intraday.main', stage: 'open', text: '分时轴太密',
    elem: 'DIV 时间轴', selector: '#axis > .tick:nth-child(3)', owner: '', created_at: '2026-10-27 09:00:00',
    history: [] },
  { id: 'N2', page: 'v2:card:intraday::intraday.main', stage: 'doing', text: '成交量柱颜色',
    elem: 'SPAN 成交量', selector: '.vol', owner: '01', created_at: '2026-10-27 10:30:00',
    history: [{ at: '2026-10-27 10:40:00', by: '01', from: 'open', to: 'doing', note: '接了', ref: '' }] },
  { id: 'N3', page: 'v2:card:ops::ops.main', stage: 'fixed', text: '表格滚动条',
    elem: 'DIV 表格', selector: '.ops-wrap', owner: '01', created_at: '2026-10-26 18:00:00',
    history: [], ref: undefined },
  { id: 'N4', page: 'v2:/v2/', stage: 'verified', text: '整壳字体', elem: 'BODY 面板',
    selector: 'body', owner: '主人', created_at: '2026-10-25 08:00:00', history: [] },
  { id: 'N5', page: '#dashboard', status: 'resolved', text: '旧页 KPI 字号', elem: 'DIV 总资产',
    selector: '#panel-dashboard .kpi:nth-child(1)', owner: '', created_at: '2026-10-21 15:57:54', history: [] },
  { id: 'N6', page: 'sector:panel', status: 'open', text: '旧板块面板', elem: 'DIV 板块',
    selector: '.sp', owner: '', created_at: '2026-10-20 12:00:00', history: [] },
];

const TITLES = {
  'intraday::intraday.main': '盘中',
  'ops::ops.main': '后台',
};
const titleOf = (key) => TITLES[key] || '';

// ---------------------------------------------------------------- 阶段口径

it('stageOf：老数据按 status 推导（resolved→verified，其余 open）', () => {
  assert.equal(M.stageOf({ stage: 'doing' }), 'doing');
  assert.equal(M.stageOf({ status: 'resolved' }), 'verified');
  assert.equal(M.stageOf({ status: 'open' }), 'open');
  assert.equal(M.stageOf({ stage: 'weird', status: 'open' }), 'open', '非法 stage 不认');
  assert.equal(M.stageOf(null), 'open');
});

it('STAGES 五项与中文标签，已完成 = verified/rejected', () => {
  assert.deepEqual(M.STAGES, ['open', 'doing', 'fixed', 'verified', 'rejected']);
  assert.deepEqual(M.STAGES.map(M.stageLabel), ['待处理', '处理中', '已修复', '已验证', '驳回']);
  assert.equal(M.isClosed('verified'), true);
  assert.equal(M.isClosed('rejected'), true);
  assert.equal(M.isClosed('fixed'), false, 'fixed 仍未完成（pin 继续画）');
  assert.equal(M.isClosed('doing'), false);
});

// ---------------------------------------------------------------- page → 卡

it('pageCardKey：只有 v2:card: 前缀解析成卡 key', () => {
  assert.equal(M.pageCardKey('v2:card:intraday::intraday.main'), 'intraday::intraday.main');
  assert.equal(M.pageCardKey('v2:card:'), null, '空 key 不算卡');
  assert.equal(M.pageCardKey('v2:/v2/'), null);
  assert.equal(M.pageCardKey('#dashboard'), null);
});

it('pageInfo：卡页取卡标题，取不到退回 key；整壳页与退役页分开', () => {
  const a = M.pageInfo('v2:card:intraday::intraday.main', titleOf);
  assert.equal(a.kind, 'card');
  assert.equal(a.key, 'intraday::intraday.main');
  assert.equal(a.label, '盘中');
  const b = M.pageInfo('v2:card:ghost::x', titleOf);
  assert.equal(b.label, 'ghost::x', '卡标题取不到时显示 key');
  const c = M.pageInfo('v2:/v2/', titleOf);
  assert.equal(c.kind, 'shell');
  assert.equal(c.locate, true, '整壳页仍可定位（同一文档）');
  const d = M.pageInfo('#dashboard', titleOf);
  assert.equal(d.kind, 'retired');
  assert.equal(d.label, '旧页已退役');
  assert.equal(d.locate, false, '旧页不导航');
});

// ---------------------------------------------------------------- 分组

it('分组：v2 卡组按最近时间倒序，退役页恒置后，组内 created_at 倒序', () => {
  const g = M.groupNotes(NOTES, titleOf);
  assert.deepEqual(g.map((x) => x.page), [
    'v2:card:intraday::intraday.main',
    'v2:card:ops::ops.main',
    'v2:/v2/',
    '#dashboard',
    'sector:panel',
  ]);
  const intraday = g[0];
  assert.deepEqual(intraday.notes.map((x) => x.id), ['N2', 'N1'], '组内倒序');
  assert.equal(intraday.label, '盘中');
  assert.equal(g[3].kind, 'retired');
  assert.equal(g[4].kind, 'retired');
});

it('分组：非对象/空数组不炸', () => {
  assert.deepEqual(M.groupNotes([], titleOf), []);
  assert.deepEqual(M.groupNotes(null, titleOf), []);
  const g = M.groupNotes([null, 3, NOTES[0]], titleOf);
  assert.equal(g.length, 1);
});

// ---------------------------------------------------------------- 筛选 + 搜索

it('筛选：按阶段取子集，all 不筛', () => {
  assert.equal(M.visibleNotes(NOTES, { stage: 'all' }).length, 6);
  assert.deepEqual(M.visibleNotes(NOTES, { stage: 'open' }).map((x) => x.id), ['N1', 'N6']);
  assert.deepEqual(M.visibleNotes(NOTES, { stage: 'verified' }).map((x) => x.id), ['N4', 'N5']);
  assert.equal(M.visibleNotes(NOTES, { stage: 'doing' }).length, 1);
});

it('搜索：匹配 text / elem / id / page，大小写不敏感，可与阶段叠加', () => {
  assert.deepEqual(M.visibleNotes(NOTES, { query: '轴太密' }).map((x) => x.id), ['N1']);
  assert.deepEqual(M.visibleNotes(NOTES, { query: 'div 时间轴' }).map((x) => x.id), ['N1']);
  assert.deepEqual(M.visibleNotes(NOTES, { query: 'n3' }).map((x) => x.id), ['N3']);
  assert.deepEqual(M.visibleNotes(NOTES, { query: 'ops::ops.main' }).map((x) => x.id), ['N3']);
  assert.equal(M.visibleNotes(NOTES, { query: '不存在的东西' }).length, 0);
  assert.equal(M.visibleNotes(NOTES, { query: '  ' }).length, 6, '空白等于不筛');
  assert.deepEqual(
    M.visibleNotes(NOTES, { query: '面板', stage: 'verified' }).map((x) => x.id),
    ['N4'],
  );
});

it('搜索也认卡标题（page 是 key，标题是人话）', () => {
  assert.deepEqual(M.visibleNotes(NOTES, { query: '盘中', titleOf }).map((x) => x.id), ['N1', 'N2']);
});

it('统计：五阶段计数 + 总数由 notes 现算，与后端 stats 同形', () => {
  const s = M.countStats(NOTES);
  assert.equal(s.total, 6);
  assert.deepEqual(s.by_stage, { open: 2, doing: 1, fixed: 1, verified: 2, rejected: 0 });
  assert.equal(s.open, 4);
});

// ---------------------------------------------------------------- 动作映射

it('下一动作：open→开始处理；doing→已修复/驳回；fixed→验收/退回/驳回；已完成→重新打开', () => {
  const act = (s) => M.actionsFor(s).map((a) => a.to + ':' + a.label);
  assert.deepEqual(act('open'), ['doing:开始处理']);
  assert.deepEqual(act('doing'), ['fixed:已修复', 'rejected:驳回']);
  assert.deepEqual(act('fixed'), ['verified:验收通过', 'doing:退回处理', 'rejected:驳回']);
  assert.deepEqual(act('verified'), ['open:重新打开']);
  assert.deepEqual(act('rejected'), ['open:重新打开']);
  // 只有主决策项带 primary，驳回带 danger —— 样式靠这个分类，不靠按钮顺序猜
  assert.equal(M.actionsFor('fixed')[0].kind, 'primary');
  assert.equal(M.actionsFor('fixed')[2].kind, 'danger');
});

it('动作映射覆盖全部枚举（漏一个就是「这条标注无法推进」）', () => {
  for (const s of M.STAGES) {
    const list = M.actionsFor(s);
    assert.ok(list.length > 0, s + ' 没有可用动作');
    for (const a of list) assert.ok(M.STAGES.includes(a.to), s + ' 的动作指向非法阶段 ' + a.to);
  }
});

// ---------------------------------------------------------------- 进度段

it('进度段：四段 open→doing→fixed→verified，rejected 走末段红色分支', () => {
  const st = (s) => M.progressOf(s).map((x) => x.key + '=' + x.state);
  assert.deepEqual(st('open'), ['open=current', 'doing=todo', 'fixed=todo', 'verified=todo']);
  assert.deepEqual(st('doing'), ['open=done', 'doing=current', 'fixed=todo', 'verified=todo']);
  assert.deepEqual(st('fixed'), ['open=done', 'doing=done', 'fixed=current', 'verified=todo']);
  assert.deepEqual(st('verified'), ['open=done', 'doing=done', 'fixed=done', 'verified=current']);
  assert.deepEqual(st('rejected'), ['open=done', 'doing=done', 'fixed=done', 'verified=rejected']);
  assert.equal(M.progressOf('rejected')[3].label, '驳回');
  assert.equal(M.progressOf(undefined).length, 4);
});

// ---------------------------------------------------------------- 时间 / 时间线

it('fmtTime：短格式 MM-DD hh:mm，解析不了原样返回', () => {
  assert.equal(M.fmtTime('2026-10-27 09:05:33'), '10-27 09:05');
  assert.equal(M.fmtTime('2026-10-27T09:05:33'), '10-27 09:05');
  assert.equal(M.fmtTime(''), '');
  assert.equal(M.fmtTime('随手写的'), '随手写的');
});

it('fmtHistory：at/by/from→to/note/ref 全部成形，ref 判类型', () => {
  const rows = M.fmtHistory([
    { at: '2026-10-27 10:40:00', by: '01', from: 'open', to: 'doing', note: '接了',
      ref: 'projects/panel-v2/docs/DONE_x.md' },
    { at: '2026-10-26 11:00:00', by: '', from: 'doing', to: 'verified', note: '', ref: '' },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].at, '10-27 10:40');
  assert.equal(rows[0].by, '01');
  assert.deepEqual([rows[0].from, rows[0].to], ['待处理', '处理中']);
  assert.equal(rows[0].note, '接了');
  assert.equal(rows[0].refKind, 'path');
  assert.equal(rows[1].by, '（未记）');
  assert.equal(M.fmtHistory([]).length, 0);
  assert.equal(M.fmtHistory(null).length, 0);
  assert.equal(M.fmtHistory([null, 'x']).length, 0, '脏数据不渲染');
});

it('refKind：http(s) 是 url，仓库相对路径是 path，其余不认', () => {
  assert.equal(M.refKind('https://example.com/a?b=1'), 'url');
  assert.equal(M.refKind('HTTP://x/y'), 'url');
  assert.equal(M.refKind('projects/panel-v2/docs/DONE_20261027_notes_lifecycle.md'), 'path');
  assert.equal(M.refKind('app/web/shell.css'), 'path');
  assert.equal(M.refKind('C:\\Users\\65442\\x.md'), 'path');
  assert.equal(M.refKind('暂时没有'), '');
  assert.equal(M.refKind(''), '');
});

// ---------------------------------------------------------------- 请求体

it('stageBody：带 expected=当前阶段、by=01，note/ref 去空白', () => {
  const b = M.stageBody({ id: 'N2', stage: 'doing' }, 'fixed', { note: ' 改完了 ', ref: ' docs/a.md ' });
  assert.deepEqual(b, { id: 'N2', to: 'fixed', expected: 'doing', by: '01', note: '改完了', ref: 'docs/a.md' });
  const c = M.stageBody({ id: 'N5', status: 'resolved' }, 'open', {});
  assert.equal(c.expected, 'verified', '老数据（无 stage 字段）的 expected 按推导值取');
  assert.equal(c.note, '');
  assert.equal(c.ref, '');
});

it('stageBody：非法 to 直接抛（不发请求）', () => {
  assert.throws(() => M.stageBody({ id: 'N1', stage: 'open' }, 'done', {}), /非法/);
});

it('端点常量与壳侧接线路径（静态契约，避免改一半）', () => {
  assert.equal(M.API_LIST, '/api/ui_notes/list');
  assert.equal(M.API_STAGE, '/api/ui_notes/stage');
  assert.equal(M.BY, '01');
});

console.log('\nPASS ' + n + '/' + n);
