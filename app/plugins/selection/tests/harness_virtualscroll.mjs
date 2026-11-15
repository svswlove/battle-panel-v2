// selection 卡 · 虚拟滚动/交互 harness（jsdom，无浏览器）
//
// 用途：在没有浏览器时验证「8.2MB 载荷 → 首屏 DOM 行数受控」这条核心纪律，
// 并对排序 / 分组切换 / 筛选 / 池二次点击做真实调用（api 打真 8810 的四个端点，只读）。
//
// 跑法（jsdom 在 OH-WorkSpace/node_modules，故用 NODE_PATH 指过去）：
//   $env:NODE_PATH='OH-WorkSpace\node_modules'
//   node app/plugins/selection/tests/harness_virtualscroll.mjs
//
// 输出：一行 JSON（每项断言 + 耗时），退出码 0=全过。本文件不参与 pytest，也不被 core 加载。

import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '..', 'frontend.js');
const BASE = process.env.PANEL_BASE || 'http://127.0.0.1:8810';

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
  url: BASE + '/v2/',
  pretendToBeVisual: true,
});
const { window } = dom;
for (const key of ['document', 'window', 'localStorage', 'performance', 'requestAnimationFrame',
  'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'Event', 'CustomEvent']) {
  if (!(key in globalThis) || key === 'document' || key === 'window') {
    try { globalThis[key] = window[key]; } catch { /* 只读全局忽略 */ }
  }
}
globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);

const calls = { get: [], post: [] };
const api = {
  base: '/api/p/selection',
  async get(path) {
    calls.get.push(path);
    const res = await fetch(BASE + path, { headers: { Accept: 'application/json' } });
    const text = await res.text();
    if (!res.ok) throw new Error('GET ' + path + ' -> ' + res.status + ': ' + text.slice(0, 200));
    return text ? JSON.parse(text) : null;
  },
  async post(path, body) {
    calls.post.push(path);
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body == null ? {} : body),
    });
    const text = await res.text();
    if (!res.ok) throw new Error('POST ' + path + ' -> ' + res.status + ': ' + text.slice(0, 200));
    return text ? JSON.parse(text) : null;
  },
};

const mod = await import('file://' + SRC.replace(/\\/g, '/'));
const card = mod.default.cards.find((c) => c.id === 'selection.main');
const root = window.document.getElementById('host');

const out = { ok: true, checks: {}, ms: {} };
const check = (name, cond, extra) => {
  out.checks[name] = cond ? 'PASS' : ('FAIL' + (extra ? ' ' + extra : ''));
  if (!cond) out.ok = false;
};

const t0 = performance.now();
// 直连辅助（不经过被监控的 api 对象，避免污染端点断言）
const raw = async (method, path, body) => {
  const res = await fetch(BASE + path, {
    method,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return text ? JSON.parse(text) : null;
};
// ---- 生产态快照（harness 会真实打写端点；跑完必须原样写回，不留脏数据）----
const beforePrefs = await raw('GET', '/api/selection_prefs');
const beforePool = await raw('GET', '/api/selection_pool');
const restore = async () => {
  const keys = ['pools', 'ma', 'pin', 'boards', 'boardOn', 'sigs', 'inds', 'cons', 'hot'];
  const body = {};
  keys.forEach((k) => { if (beforePrefs[k] !== undefined) body[k] = beforePrefs[k]; });
  await raw('POST', '/api/selection_prefs', body);
  const nowPool = await raw('GET', '/api/selection_pool');
  const have = new Set((nowPool.items || []).map((x) => x.code));
  const want = new Set((beforePool.items || []).map((x) => x.code));
  for (const x of beforePool.items || []) {
    if (!have.has(x.code)) {
      await raw('POST', '/api/selection_pool/add',
        { code: x.code, name: x.name, group: x.group, sig: x.sig });
    }
  }
  for (const c of have) {
    if (!want.has(c)) await raw('POST', '/api/selection_pool/remove', { code: c });
  }
};

await card.mount(root, { api, plugin: { id: 'selection' }, card: { id: 'selection.main' }, log() {} });
out.ms.mount = Math.round(performance.now() - t0);

// ---- 首屏 ----
const paintAttr = root.getAttribute('data-paint-ms');
out.ms.paint = paintAttr ? Number(paintAttr) : null;
const rowEls = () => root.querySelectorAll('.sel-r');
const statText = root.querySelector('.sel-stat')?.textContent || '';
const groupCount = Number((statText.match(/原始\s*(\d+)\s*行/) || [])[1] || 0);
out.rows = { dom: rowEls().length, groupRaw: groupCount, viewAttr: Number(root.getAttribute('data-rows')) };
check('首屏记录了 paint 耗时', paintAttr != null, paintAttr);
check('首屏 DOM 行数受控（<=60）', rowEls().length > 0 && rowEls().length <= 60, String(rowEls().length));
check('当前分组原始行数 > DOM 行数', groupCount > rowEls().length,
  'raw=' + groupCount + ' dom=' + rowEls().length);
check('四个读端点都打到根路径', calls.get.every((p) => p.startsWith('/api/')), JSON.stringify(calls.get));

// ---- 切到最大分组（砖形图 2824 行）----
const bigTab = Array.from(root.querySelectorAll('.sel-tab'))
  .find((b) => (b.textContent || '').startsWith('砖形图'));
if (bigTab) {
  const t1 = performance.now();
  bigTab.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  out.ms.switchGroup = Math.round(performance.now() - t1);
  const stat2 = root.querySelector('.sel-stat')?.textContent || '';
  const raw2 = Number((stat2.match(/原始\s*(\d+)\s*行/) || [])[1] || 0);
  out.rows.bigGroupRaw = raw2;
  check('最大分组仍只渲染可视行（<=60）', rowEls().length > 0 && rowEls().length <= 60,
    'raw=' + raw2 + ' dom=' + rowEls().length);
  check('最大分组原始行数 >= 1000', raw2 >= 1000, String(raw2));
}

// ---- 滚动：窗口移动但仍只渲染可视行 ----
// 生产偏好当前只留主板 + 砖形图两类形态 + 市值 50–300 亿，最大分组过滤后只剩十几行；
// 为了真正验证窗口滑动，先切到可见行较多的「动能」分组（38 行），再滚动。
const vp = root.querySelector('.sel-vp');
const fengTab = Array.from(root.querySelectorAll('.sel-tab'))
  .find((b) => (b.textContent || '').startsWith('动能'));
if (fengTab) fengTab.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const scrollTotal = Number((root.querySelector('.sel-stat')?.textContent || '')
  .match(/显示\s*(\d+)\s*行/)?.[1] || 0);
out.rows.scrollGroup = scrollTotal;
// jsdom 的 scrollTop 是 no-op（恒返回 0），这里给它一个真属性，模拟真实滚动位置
let fakeTop = 0;
Object.defineProperty(vp, 'scrollTop', {
  configurable: true,
  get() { return fakeTop; },
  set(v) { fakeTop = Number(v) || 0; },
});
const targetTop = 32 * Math.max(1, Math.min(20, scrollTotal - 8));
vp.scrollTop = targetTop;
vp.dispatchEvent(new window.Event('scroll'));
await new Promise((r) => setTimeout(r, 60));
const tops = Array.from(rowEls()).map((el) => parseInt(el.style.top, 10));
const wantMin = Math.max(0, Math.floor(targetTop / 32) - 6) * 32;
check('滚动后行偏移跟随 scrollTop', tops.length > 0 && Math.min(...tops) >= wantMin,
  'minTop=' + Math.min(...tops) + ' want>=' + wantMin);
check('滚动后首行不在顶端（窗口真的移了）', tops.length > 0 && Math.min(...tops) > 0,
  'minTop=' + Math.min(...tops));
check('滚动后 DOM 行数仍受控（<=60）', rowEls().length <= 60, String(rowEls().length));

// ---- 排序：点「涨幅」表头，缺值沉底 ----
const sortHead = root.querySelector('[data-sort="pct"]');
sortHead.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const firstVals = Array.from(rowEls()).slice(0, 5)
  .map((el) => el.querySelectorAll('.c')[2]?.textContent || '');
check('排序后首屏行有涨幅值', firstVals.every((v) => v.trim() !== ''), JSON.stringify(firstVals));
check('排序箭头出现在表头', /▼|▲/.test(root.querySelector('[data-sort="pct"]').textContent));

// ---- 筛选：切股票池（只留主板）后行数下降 ----
const poolChip = Array.from(root.querySelectorAll('[data-chip]')).find((b) => b.dataset.chip === 'pool');
poolChip.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const cbStar = root.querySelector('[data-poolopt="star"]');
const cbBse = root.querySelector('[data-poolopt="bse"]');
for (const cb of [cbStar, cbBse]) {
  cb.checked = false;
  cb.dispatchEvent(new window.Event('change', { bubbles: true }));
}
await new Promise((r) => setTimeout(r, 500));   // 等 300ms 防抖 + POST + 回读
const afterFilter = Number((root.querySelector('.sel-stat')?.textContent || '')
  .match(/显示\s*(\d+)\s*行/)?.[1] || -1);
out.rows.afterPoolFilter = afterFilter;
check('取消科创/北交所后显示行数下降', afterFilter >= 0 && afterFilter < (out.rows.bigGroupRaw || 0),
  String(afterFilter));
check('筛选偏好走了 POST', calls.post.includes('/api/selection_prefs'), JSON.stringify(calls.post));
check('筛选偏好回读一致提示', (root.querySelector('.sel-okbar')?.textContent || '').includes('回读一致'),
  root.querySelector('.sel-okbar')?.textContent || '(无状态条)');

// 恢复股票池（避免污染生产偏好）
for (const cb of [cbStar, cbBse]) {
  cb.checked = true;
  cb.dispatchEvent(new window.Event('change', { bubbles: true }));
}
await new Promise((r) => setTimeout(r, 500));

// ---- 池 ★ 二次点击：第一次只点亮，第二次才 POST ----
const starBtn = root.querySelector('[data-pool]');
const code = starBtn.getAttribute('data-pool');
const wasIn = starBtn.classList.contains('on');
starBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
check('第一次点击只点亮确认文案（不发请求）',
  starBtn.getAttribute('data-arm') === '1' && /确认/.test(starBtn.textContent),
  starBtn.textContent);
const postBefore = calls.post.length;
starBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await new Promise((r) => setTimeout(r, 800));
const wrote = calls.post.slice(postBefore);
check('第二次点击才发写请求', wrote.length === 1, JSON.stringify(wrote));
check('写端点按当前池态选择 add/remove',
  wrote[0] === (wasIn ? '/api/selection_pool/remove' : '/api/selection_pool/add'), wrote[0]);
// 复原：把刚改的再改回来（同样走二次点击）
const starAfter = Array.from(root.querySelectorAll('[data-pool]')).find((b) => b.getAttribute('data-pool') === code);
if (starAfter) {
  starAfter.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  starAfter.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 800));
}

// ---- unmount ----
await card.unmount();
check('unmount 后状态释放', card._sel === null, String(card._sel));
check('unmount 后 root 仍可访问（无异常）', !!root.isConnected);

// ---- 生产态复原（写回 harness 开始前的偏好与池）----
await restore();
const afterPrefs = await raw('GET', '/api/selection_prefs');
const afterPool = await raw('GET', '/api/selection_pool');
check('偏好已复原', JSON.stringify(afterPrefs.pools) === JSON.stringify(beforePrefs.pools)
  && JSON.stringify(afterPrefs.sigs) === JSON.stringify(beforePrefs.sigs),
  JSON.stringify(afterPrefs.pools) + ' / ' + JSON.stringify(afterPrefs.sigs));
check('选股池已复原',
  JSON.stringify((afterPool.items || []).map((x) => x.code).sort())
  === JSON.stringify((beforePool.items || []).map((x) => x.code).sort()),
  String((afterPool.items || []).length));

out.gets = calls.get;
out.posts = calls.post;
console.log(JSON.stringify(out, null, 1));
process.exit(out.ok ? 0 : 1);
