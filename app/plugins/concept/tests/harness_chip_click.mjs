// concept 卡 · 占位芯片点击 harness（jsdom，无浏览器、无网络）
//
// 用途：验证审计 R35 P1-2 的修复——卡内点 [data-open-kline] / [data-open-amv] 芯片
//   **不得**被详情委托吞掉（不误开概念详情），而点卡片空白处仍然正常开详情。
//
// 跑法（jsdom 在 OH-WorkSpace/node_modules，故用 NODE_PATH 指过去）：
//   $env:NODE_PATH='OH-WorkSpace\node_modules'
//   node app/plugins/concept/tests/harness_chip_click.mjs
//
// 输出：一行 JSON（每项断言 + 调用计数），退出码 0=全过。不参与 pytest，不被 core 加载。

import { JSDOM } from 'jsdom';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '..', 'frontend.js');

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
  url: 'http://127.0.0.1:8810/v2/',
  pretendToBeVisual: true,
});
const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
globalThis.localStorage = window.localStorage;
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);

// 假数据：一张活跃卡（带 slug + 个股芯片 + 板块芯片）
const CONCEPT = {
  ok: true,
  updated: '2026-10-24 19:30',
  counts: { active: 1, watch: 0, chain: 0, archived: 0 },
  active: [{
    name: '物理AI',
    slug: '物理AI',
    heat: 'hot',
    stage_short: '主升',
    logic: '算力+本体',
    duration: '3日',
    updated: '2026-10-24',
    next_node: '周一看承接',
    targets: ['中大力德'],
    targets_detail: [{ name: '中大力德', code: '002896.SZ' }],
    sectors_list: [{ name: '机器人', code: '881167.TI', src: 'L2' }],
  }],
  watch: [],
  chain: [],
  archived: [],
};

const calls = { get: [], detail: 0 };
const api = {
  async get(path) {
    calls.get.push(path);
    if (path === '/api/concept.json') return JSON.parse(JSON.stringify(CONCEPT));
    if (path.indexOf('/api/concept.json?slug=') === 0) {
      calls.detail += 1;
      return { ok: true, html: '<p>概况正文</p>' };
    }
    throw new Error('unexpected GET ' + path);
  },
};

const mod = await import('file://' + SRC.replace(/\\/g, '/'));
const card = mod.default.cards.find((c) => c.id === 'concept.main');
const root = window.document.getElementById('host');

const out = { ok: true, checks: {}, calls };
const check = (name, cond, extra) => {
  out.checks[name] = cond ? 'PASS' : ('FAIL' + (extra ? ' ' + extra : ''));
  if (!cond) out.ok = false;
};
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

await card.mount(root, { api, plugin: { id: 'concept' }, card: { id: 'concept.main' }, log() {} });

const klineChip = root.querySelector('[data-open-kline]');
const amvChip = root.querySelector('[data-open-amv]');
const theCard = root.querySelector('.cp-c[data-slug]');
check('活跃卡渲染出个股芯片', !!klineChip, String(!!klineChip));
check('活跃卡渲染出板块芯片', !!amvChip, String(!!amvChip));
check('芯片有「待桥接」提示', !!klineChip && /待桥接/.test(klineChip.getAttribute('title') || ''),
  klineChip && klineChip.getAttribute('title'));
check('挂载期只打总表一次', calls.detail === 0 && calls.get.length === 1, JSON.stringify(calls));

// ---- 1. 点个股芯片：不得开详情 ----
click(klineChip);
await new Promise((r) => setTimeout(r, 20));
check('点个股芯片不发详情请求', calls.detail === 0, 'detail=' + calls.detail);
check('点个股芯片视图仍在列表', !root.querySelector('[data-back]'), 'detail view opened');
check('卡片仍在 DOM 里', !!root.querySelector('.cp-c[data-slug]'));

// ---- 2. 点板块芯片：不得开详情 ----
click(amvChip);
await new Promise((r) => setTimeout(r, 20));
check('点板块芯片不发详情请求', calls.detail === 0, 'detail=' + calls.detail);

// ---- 3. 点卡片空白处：仍然开详情（不误伤正常入口）----
click(theCard.querySelector('.cp-c-name'));
await new Promise((r) => setTimeout(r, 30));
check('点卡片正文仍开详情', calls.detail === 1, 'detail=' + calls.detail);
check('详情视图出现返回按钮', !!root.querySelector('[data-back]'));

// ---- 4. 详情里的个股名（服务端 .k-name 占位）点击也不回列表 ----
const back = root.querySelector('[data-back]');
click(back);
await new Promise((r) => setTimeout(r, 20));
check('返回列表可用', !!root.querySelector('.cp-c[data-slug]') && !root.querySelector('[data-back]'));

await card.unmount();
console.log(JSON.stringify(out));
process.exit(out.ok ? 0 : 1);
