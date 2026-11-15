/**
 * 交互全集跑真 DOM（jsdom）：给 tests/test_research.py 调用，也可单独 node 跑。
 *
 * 为什么要有这一层：研究卡的交互旧页全是内联 onclick（6 处 rsSet + 2 处 onscroll），
 * 卡片里改成 data-act 委托 —— 「改完还能点」只有真在 DOM 上点一遍才算验过。
 * jsdom 在仓库根的 node_modules 里（OH-WorkSpace/node_modules/jsdom），不额外装包。
 *
 * 输出：最后一行 JSON。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../..');
const REPO = path.resolve(PANEL, '../..');
const JSDOM_API = path.join(REPO, 'node_modules', 'jsdom', 'lib', 'api.js');
const FIX_BOARD = path.join(HERE, 'fixtures/amv_research_l2_board.json');
const FIX_ALL = path.join(HERE, 'fixtures/amv_research_l2_all.json');
const FIX_L3 = path.join(HERE, 'fixtures/amv_research_l3.json');

const { JSDOM } = await import('file:///' + JSDOM_API.replace(/\\/g, '/'));
const dom = new JSDOM('<!doctype html><html><head></head><body><div id="host"></div></body></html>',
  { pretendToBeVisual: true });
const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
// 浏览器里 AbortController 与 DOM 同 realm；jsdom 自带一份，跨 realm 传 signal 会类型不符
globalThis.AbortController = window.AbortController;
globalThis.location = window.location;

const A = await import(new URL('../frontend/research.js', import.meta.url).href);

const board = JSON.parse(fs.readFileSync(FIX_BOARD, 'utf8'));
const allScope = JSON.parse(fs.readFileSync(FIX_ALL, 'utf8'));
const l3 = JSON.parse(fs.readFileSync(FIX_L3, 'utf8'));

const calls = [];
const api = {
  async get(p) {
    calls.push(p);
    // 服务端（compat build_amv_research）行为：选中码在表内就原样回，不在才回落贡献第一名。
    // 夹具是静态文件，这里按同样规则把 sel 改成请求的那个码，模拟真实往返。
    const pick = (doc) => {
      const m = /[?&]code=([^&]+)/.exec(p);
      if (!m) return doc;
      const want = decodeURIComponent(m[1]);
      return (doc.boards || []).some((b) => b.c === want) ? { ...doc, sel: want } : doc;
    };
    if (p.indexOf('scope=all') >= 0) return pick(allScope);
    if (p.indexOf('level=L3') >= 0) return pick(l3);
    return pick(board);
  },
};

const card = document.createElement('div');
card.className = 'it-card';
const body = document.createElement('div');
card.append(body);
document.getElementById('host').append(card);

const st = A.createState(card, body, api);
A.bind(st);
await A.load(st, true);

const n = (sel) => body.querySelectorAll(sel).length;
const rows = () => Array.from(body.querySelectorAll('[data-rs-col="b"] tbody tr'));
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
const btn = (txt) => Array.from(body.querySelectorAll('.rs-bar button')).find((b) => b.textContent === txt);
const topOf = (col) => rows()[0].children[col].textContent.trim();
const wait = () => new Promise((r) => setTimeout(r, 60));
const n3count = (el, sel) => el.querySelectorAll(sel).length;

const out = {};
out.initial = {
  left: n('[data-rs-col="b"] tbody tr'),
  mid: n('[data-rs-col="m"] tbody tr'),
  cols: rows()[0].children.length,
  spark: n('.rs-sp'),
  kname: n('.k-name'),
  parts: n('.rs-part'),
  selected: n('tr.sel'),
  loadingGone: !/数据加载中/.test(body.textContent),
};

// ---- 右键 / 跨 iframe 占位属性（shell 的 ctx-menu 与 v2:openAmv/openKline 桥都靠它们）
// 必须在切层级之前取：L2 是 90 板块 / 183 成分股，L3 是别的数
const ctxSnap = {
  sector: n('tr[data-ctx="sector"]'),
  stock: n('tr[data-ctx="stock"]'),
  openAmv: n('[data-open-amv]'),
  openKline: n('[data-open-kline]'),
  openName: n('[data-open-name]'),
  sectorCode: body.querySelector('tr[data-ctx="sector"]').getAttribute('data-code'),
  sectorName: body.querySelector('tr[data-ctx="sector"]').getAttribute('data-name'),
  stockCode: body.querySelector('tr[data-ctx="stock"]').getAttribute('data-code'),
  stockName: body.querySelector('tr[data-ctx="stock"]').getAttribute('data-name'),
  openAmvCode: body.querySelector('.rs-open').getAttribute('data-open-amv'),
};
out.ctx = ctxSnap;

// ---- 排序 3 键（旧页的 bug：签名不含 srt，点了不重排；卡片已修，见 HANDOFF 偏离 5）
const s = {};
s.contrib = topOf(2);
click(btn('涨跌'));
s.px_pct = topOf(3);
const on1 = Array.from(body.querySelectorAll('.rs-tbl th.on')).map((t) => t.dataset.k);
click(btn('AMV'));
s.amv_pct = topOf(4);
const on2 = Array.from(body.querySelectorAll('.rs-tbl th.on')).map((t) => t.dataset.k);
click(btn('指数贡献'));
s.apiCalls = calls.length;
s.headerOn = [on1[0], on1[1], on2[0], on2[1]];
s.restored = topOf(2) === s.contrib;
out.sort = s;

// ---- 点板块行 → 选中 + 按 code 重拉
const row = rows()[2];
const want = row.getAttribute('data-code');
click(row);
await wait();
out.select = { sel: st.sel, expect: want, url: calls[calls.length - 1], scope: st.scope };

// ---- 返回全A / 回成分股
click(body.querySelector('.rs-back'));
await wait();
out.all = {
  scope: st.scope,
  url: calls[calls.length - 1],
  midRows: n('[data-rs-col="m"] tbody tr'),
  back: body.querySelector('.rs-back').textContent.trim(),
  partsHidden: n('.rs-part') === 0,
};
click(body.querySelector('.rs-back'));
await wait();
out.backToBoard = { scope: st.scope, back: body.querySelector('.rs-back').textContent.trim() };

// ---- 层级（含旧页 CONCEPT 档按钮存在性）
const lvlBtns = Array.from(body.querySelectorAll('.rs-bar button')).slice(0, 3)
  .map((b) => b.textContent);
click(btn('同花顺三级'));
await wait();
out.level = { labels: lvlBtns, lvl: st.lvl, url: calls[calls.length - 1] };

// ---- 排序不重拉（上面的 sort.apiCalls 已比过，这里确认选板块之后也没有额外请求）
out.apiTotal = calls.length;

// ---- 滚动位置保持（旧页 4403-4406 _rsScroll）
const colB = body.querySelector('[data-rs-col="b"]');
colB.scrollTop = 123;
colB.dispatchEvent(new window.Event('scroll', { bubbles: true }));
const captured = st.scroll.b;
A.paint(st);
out.scroll = { captured, restored: body.querySelector('[data-rs-col="b"]').scrollTop };

// ---- 节点复用（旧页 4395 签名缓存：同签名不重解析）
const node1 = body.querySelector('.rs-wrap');
A.paint(st);
out.reuse = { sameNode: body.querySelector('.rs-wrap') === node1 };

// ---- 卸载：监听与看门狗一起解掉
const before = st.srt;
A.unbind(st);
rows()[0].dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
out.cleanup = { alive: st.alive, watch: st.watch, ignored: st.srt === before };

// ---- 请求在飞时点「返回全A」（2026-10-26 主人报「点没反应」）------------------------------
//
// 旧页/C2a 版本的真 bug：`rsSet` 先改 scope 再调 load，而 load 碰到 busy 就**静默 return**。
// 在飞请求回来后又按新 scope 渲染旧响应 → 标题说「全A个股」、表格还是那个板块的成分股。
// 本机直连时窗口只有 ~100ms（C2a 验收就是这样过的）；这里用一道「闸门」把响应留住，
// 稳定造出「点了没反应」的现场，再验：①点击不丢（会排队） ②最终态正确（scope=all 且表格是全A 数据）。
const gatedCalls = [];
const gate = { hold: null };
const pickSel = (doc, p) => {
  const m = /[?&]code=([^&]+)/.exec(p);
  if (!m) return doc;
  const want = decodeURIComponent(m[1]);
  return (doc.boards || []).some((b) => b.c === want) ? { ...doc, sel: want } : doc;
};
const gatedApi = {
  async get(p) {
    gatedCalls.push(p);
    if (gate.hold) await gate.hold;            // 闸门：没放行就一直挂着（模拟请求在飞）
    if (p.indexOf('scope=all') >= 0) return pickSel(allScope, p);
    if (p.indexOf('level=L3') >= 0) return pickSel(l3, p);
    return pickSel(board, p);
  },
};
const card2 = document.createElement('div');
card2.className = 'it-card';
const body2 = document.createElement('div');
card2.append(body2);
document.getElementById('host').append(card2);
const st2 = A.createState(card2, body2, gatedApi);
A.bind(st2);
await A.load(st2, true);
const n2 = (sel) => body2.querySelectorAll(sel).length;
const click2 = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
const allRows = (allScope.members || allScope.stocks || []).length;

// ① 点板块行 → 请求被闸门挂住（busy=true）
let release;
gate.hold = new Promise((r) => { release = r; });
const row2 = Array.from(body2.querySelectorAll('[data-rs-col="b"] tbody tr.pk'))[2];
click2(row2);
await wait();
const busyNow = st2.busy;
// ② 请求仍在飞时点「返回全A」——旧版本这一下会被吞掉
click2(body2.querySelector('.rs-back'));
await wait();
const rightAfterClick = {
  scope: st2.scope,
  pending: st2.pending,
  backLabel: body2.querySelector('.rs-back').textContent.trim(),
  newCalls: gatedCalls.length,
};
// ③ 放行在飞请求 → 排队的强制请求应该被补发
release();
gate.hold = null;
await wait();
await new Promise((r) => setTimeout(r, 120));
await wait();
out.raceClick = {
  busyAtClick: busyNow,
  rightAfterClick: rightAfterClick,
  finalScope: st2.scope,
  finalBack: body2.querySelector('.rs-back').textContent.trim(),
  finalMidRows: n2('[data-rs-col="m"] tbody tr'),
  expectMidRows: allRows,
  issuedScopeAll: gatedCalls.some((u) => u.indexOf('scope=all') >= 0),
  calls: gatedCalls.slice(-3),
  pendingCleared: st2.pending === false,
};
// ④ 三态循环：全A → 回成分股 → 再返回全A
click2(body2.querySelector('.rs-back'));
await wait();
const s1 = { scope: st2.scope, back: body2.querySelector('.rs-back').textContent.trim() };
click2(body2.querySelector('.rs-back'));
await wait();
const s2 = { scope: st2.scope, back: body2.querySelector('.rs-back').textContent.trim() };
out.threeState = { toBoard: s1, backToAll: s2, ok: s1.scope === 'board' && s2.scope === 'all' };
A.unbind(st2);

// ---- 忙碌态（2026-10-26 主人：「返回全A / 回成分股」点下去到数据回来有 1~1.5s 静默期，必须有反馈）
// 验证点：请求在飞 → 按钮被禁用 + aria-busy + 脉冲类 + 文案变「返回全A…」；响应回来 → 全部复原。
{
  const bCalls = [];
  const bgate = { hold: null, release: null };
  const bApi = { async get(p) { bCalls.push(p); if (bgate.hold) await bgate.hold;
    if (p.indexOf('scope=all') >= 0) return pickSel(allScope, p);
    return pickSel(board, p); } };
  const card3 = document.createElement('div');
  card3.className = 'it-card';
  const body3 = document.createElement('div');
  card3.append(body3);
  document.getElementById('host').append(card3);
  const st3 = A.createState(card3, body3, bApi);
  A.bind(st3);
  await A.load(st3, true);
  const bar3 = () => body3.querySelector('.rs-back');
  const click3 = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
  const idleText = bar3().textContent.trim();
  const idleAttrs = { disabled: bar3().disabled, aria: bar3().getAttribute('aria-busy'), cls: bar3().classList.contains('busy') };

  bgate.hold = new Promise((r) => { bgate.release = r; });
  click3(bar3());
  await wait();
  const during = {
    busy: st3.busy,
    disabled: bar3().disabled,
    aria: bar3().getAttribute('aria-busy'),
    cls: bar3().classList.contains('busy'),
    text: bar3().textContent.trim(),
    scope: st3.scope,
    calls: bCalls.length,
  };
  bgate.release();
  bgate.hold = null;
  await wait();
  await new Promise((r) => setTimeout(r, 120));
  const after = {
    busy: st3.busy,
    disabled: bar3().disabled,
    aria: bar3().getAttribute('aria-busy'),
    cls: bar3().classList.contains('busy'),
    text: bar3().textContent.trim(),
    midRows: n3count(body3, '[data-rs-col="m"] tbody tr'),
    scope: st3.scope,
  };
  out.busyState = { idleText: idleText, idleAttrs: idleAttrs, during: during, after: after };
  A.unbind(st3);
}

// ---- 看门狗按 TTL 重试（2026-10-27 标注 N20261027-101349-33「盘中研究区用昨日数据」）----------
//
// 旧实现的缺陷：看门狗第一句是 `if (st.rs) return;` —— 只要手里有**任何**旧数据（含后端实时失败
// 后回退的收盘帧），就永远不再重试，盘中会一直停在昨日数据上。这里用「截获 setInterval 回调 +
// 假时钟」直接驱动看门狗，验：TTL 未到期不打扰 / TTL 到期即使有旧 rs 也重试 / 盘外不刷 / 失败态重试。
{
  const wCalls = [];
  const wApi = { async get(p) { wCalls.push(p); return pickSel(board, p); } };
  const realSet = globalThis.setInterval;
  const realClear = globalThis.clearInterval;
  const realNow = Date.now;
  let tick = null;
  globalThis.setInterval = (fn) => { tick = fn; return 1; };
  globalThis.clearInterval = () => {};
  const card4 = document.createElement('div');
  card4.className = 'it-card';
  const body4 = document.createElement('div');
  card4.append(body4);
  document.getElementById('host').append(card4);
  const liveNow = new Date('2026-10-27T10:30:00+08:00').getTime();   // 周二盘中
  const offNow = new Date('2026-10-27T21:00:00+08:00').getTime();    // 周二盘后
  const wkend = new Date('2026-10-31T10:30:00+08:00').getTime();     // 周六盘中钟点
  Date.now = () => liveNow;
  const st4 = A.createState(card4, body4, wApi);
  A.bind(st4);
  await A.load(st4, true);
  const hasOld = !!st4.rs;
  const n0 = wCalls.length;
  tick();                                        // ① TTL 内（刚成功）→ 不重试
  const ttlFresh = wCalls.length - n0;
  st4.at = liveNow - A.RS_TTL_LIVE - 1000;       // ② TTL 到期 → 有旧 rs 也必须重试
  tick();
  await new Promise((r) => setTimeout(r, 80));
  const ttlExpired = wCalls.length - n0;
  const stillHasOld = !!st4.rs;
  // ③ 盘外（21:00）TTL 到期也不刷历史帧
  Date.now = () => offNow;
  st4.at = offNow - A.RS_TTL_LIVE - 1000;
  const n1 = wCalls.length;
  tick();
  await new Promise((r) => setTimeout(r, 60));
  const offHours = wCalls.length - n1;
  // ④ 周末钟点同样不刷
  Date.now = () => wkend;
  st4.at = wkend - A.RS_TTL_LIVE - 1000;
  const n2 = wCalls.length;
  tick();
  await new Promise((r) => setTimeout(r, 60));
  const weekend = wCalls.length - n2;
  // ⑤ 上一轮实时失败（st.err + liveErr）→ TTL 到期必须再试实时
  Date.now = () => liveNow;
  st4.err = '请求超时（8s）'; st4.liveErr = '请求超时（8s）';
  st4.at = liveNow - A.RS_TTL_LIVE - 1000;
  const n3 = wCalls.length;
  tick();
  await new Promise((r) => setTimeout(r, 80));
  const afterFail = wCalls.length - n3;
  const lastUrl = wCalls[wCalls.length - 1];
  A.unbind(st4);
  // ⑥ 纯函数口径（含「服务端自报 live_err 的收盘帧」也要重试）
  const mk = (over, at) => Object.assign({ rs: { live_mode: true }, live: 1, at: at, err: '',
    liveErr: '' }, over);
  const due = {
    noData: A.watchdogDue({ rs: null, live: 1 }, liveNow),
    fresh: A.watchdogDue(mk({}, liveNow - 1000), liveNow),
    expired: A.watchdogDue(mk({}, liveNow - A.RS_TTL_LIVE - 1), liveNow),
    offHours: A.watchdogDue(mk({}, offNow - A.RS_TTL_LIVE - 1), offNow),
    weekend: A.watchdogDue(mk({}, wkend - A.RS_TTL_LIVE - 1), wkend),
    closeMode: A.watchdogDue(mk({ live: 0 }, liveNow - A.RS_TTL_LIVE - 1), liveNow),
    closeNoErr: A.watchdogDue(mk({ rs: { live_mode: false, live_err: null } },
      liveNow - A.RS_TTL_LIVE - 1), liveNow),
    srvLiveErr: A.watchdogDue(mk({ rs: { live_mode: false, live_err: 'timeout' } },
      liveNow - A.RS_TTL_LIVE - 1), liveNow),
    localErr: A.watchdogDue(mk({ err: '接口不可达', liveErr: '接口不可达' },
      liveNow - A.RS_TTL_LIVE - 1), liveNow),
  };
  Date.now = realNow;
  globalThis.setInterval = realSet;
  globalThis.clearInterval = realClear;
  out.watchdog = { hasOld, ttlFresh, ttlExpired, stillHasOld, offHours, weekend, afterFail, lastUrl, due };
}

// ---- 降级提示可见（2026-10-27 同上）：回退收盘时不能只写「收盘」把失败藏掉 ----------------------
{
  const now = new Date('2026-10-27T10:30:00+08:00');
  const mkSt = (over) => Object.assign({ rs: null, lvl: 'L2', sel: '', srt: 'contrib',
    scope: 'board', live: 1, err: '', liveErr: '' }, over);
  const srv = mkSt({ rs: Object.assign({}, board, { live_mode: false, live_err: 'timeout' }) });
  const local = mkSt({ rs: Object.assign({}, board, { live_mode: false, live_err: null }),
    liveErr: '请求超时（8s）' });
  const okLive = mkSt({ rs: Object.assign({}, board, { live_mode: true, live_date: '2026-10-27',
    asof: '10:29:58' }) });
  const hSrv = A.html(srv, now), hLocal = A.html(local, now), hOk = A.html(okLive, now);
  out.liveErr = {
    srvHasText: /实时不可用，当前为收盘/.test(hSrv),
    srvHasReason: /timeout/.test(hSrv),
    srvAsof: /收盘 2026-10-23/.test(hSrv),
    localHasText: /实时不可用，当前为收盘/.test(hLocal),
    localHasReason: /请求超时/.test(hLocal),
    liveNoWarn: !/实时不可用/.test(hOk),
    liveAsof: /实时 10:29:58/.test(hOk),
  };
}

console.log(JSON.stringify(out));
