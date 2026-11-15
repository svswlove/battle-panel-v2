// 标注清单 + 生命周期面板的浏览器验收（CDP，headless Chrome）。
//
// 铁律：**绝不写生产** `engine/state/ui_notes.json`。
// 做法：新文档脚本用 `Page.addScriptToEvaluateOnNewDocument` 在 shell.js 之前覆盖 window.fetch，
// 把 `/api/ui_notes/list` 与 `/api/ui_notes/stage` 全部接进内存桩：读只回固定假数据，
// 写只记下请求体并就地改桩内 stage（模拟后端），从不发网络。其它请求放行走真实 fetch。
// 脚本还会在结尾断言「桩收到的所有 ui_notes 请求都由桩处理」，并对比盘上 notes 计数未变。
//
// 覆盖的验收点：
//   1. rail #rail-notes 存在且在 #rail-note 之上；点它开抽屉（.np-drawer.on）
//   2. 头部统计与 chips 数字来自桩的 stats
//   3. 按 page 分组：卡名（来自壳 allCards）、整壳页、旧页「旧页已退役」置底
//   4. 组内按 created_at 倒序
//   5. chips 筛选（已修复 → 只剩 1 条）
//   6. 搜索（按 text / elem / id）
//   7. 展开：selector、四段进度条、history 时间线
//   8. 「开始处理」请求体严格等于 {id,to:'doing',expected:'open',by:'01',note,ref}
//   9. 409：桩故意回 409 → 提示「阶段已变化」且真的重拉了 list
//  10. 旧页那条不给「定位」按钮
//  11. 截图落 _vis/notes_panel/
//
// 用法：node app/tests/js/v2_notes_panel.cdp.mjs [--shots <dir>]
// 前置：8810 在跑（面板只读该进程的静态资源，接口已被桩接管）。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '..', '..', '..');
const SHOT_ARG = process.argv.indexOf('--shots');
const SHOT_DIR = SHOT_ARG > 0 ? process.argv[SHOT_ARG + 1] : path.join(PANEL, '_vis', 'notes_panel');
const NOTES_FILE = path.resolve(PANEL, '..', '..', 'engine', 'state', 'ui_notes.json');
const BASE = 'http://127.0.0.1:8810';
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- 固定假数据
// 三条覆盖三种页形态 + 五个阶段里的四个；旧页那条用来验「不给定位」。
const FIXTURE = {
  notes: [
    {
      id: 'N9001', page: 'v2:card:intraday::intraday.main', stage: 'open', stage_at: '2026-10-27 09:00:00',
      selector: '#stage > .card-host:nth-child(1) > .it-card', elem: 'DIV 分时卡', text: '分时轴刻度太密',
      owner: '', created_at: '2026-10-27 09:00:00', status: 'open', history: [],
      resolved_at: '', resolve_note: '',
    },
    {
      id: 'N9002', page: 'v2:card:intraday::intraday.main', stage: 'doing', stage_at: '2026-10-27 10:40:00',
      selector: '.it-vol', elem: 'SPAN 成交量', text: '成交量柱颜色与涨跌不一致',
      owner: '01', created_at: '2026-10-27 10:30:00', status: 'open',
      history: [{ at: '2026-10-27 10:40:00', by: '01', from: 'open', to: 'doing', note: '接了', ref: '' }],
      resolved_at: '', resolve_note: '',
    },
    {
      id: 'N9003', page: 'v2:card:ops::ops.main', stage: 'fixed', stage_at: '2026-10-26 18:30:00',
      selector: '.ops-wrap', elem: 'DIV 表格容器', text: '后台表内出现第二条滚动条',
      owner: '01', created_at: '2026-10-26 18:00:00', status: 'open',
      history: [
        { at: '2026-10-26 18:20:00', by: '01', from: 'open', to: 'doing', note: '', ref: '' },
        { at: '2026-10-26 18:30:00', by: '01', from: 'doing', to: 'fixed', note: '去掉内部滑块',
          ref: 'projects/panel-v2/docs/DONE_20261027_annotation_ops_scroll.md' },
      ],
      resolved_at: '', resolve_note: '',
    },
    {
      id: 'N9004', page: 'v2:card:layout::shell', stage: 'verified', stage_at: '2026-10-25 09:00:00',
      selector: '.main-head', elem: 'HEADER 标题栏', text: '标题栏与卡片间距过大',
      owner: '主人', created_at: '2026-10-25 08:00:00', status: 'resolved',
      history: [{ at: '2026-10-25 09:00:00', by: '01', from: 'doing', to: 'verified', note: '', ref: '' }],
      resolved_at: '2026-10-25 09:00:00', resolve_note: '',
    },
    {
      id: 'N9005', page: '#dashboard', stage: 'verified', stage_at: '2026-10-21 15:57:54',
      selector: '#panel-dashboard .kpi:nth-child(1)', elem: 'DIV 总资产', text: '总资产字号再大一号',
      owner: '', created_at: '2026-10-21 15:57:54', status: 'resolved', history: [],
      resolved_at: '2026-10-26 00:31:25', resolve_note: '',
    },
  ],
};

function statsOf(notes) {
  const byStage = { open: 0, doing: 0, fixed: 0, verified: 0, rejected: 0 };
  const byPage = {};
  for (const n of notes) {
    byStage[n.stage] = (byStage[n.stage] || 0) + 1;
    byPage[n.page] = (byPage[n.page] || 0) + 1;
  }
  return {
    total: notes.length,
    open: byStage.open + byStage.doing + byStage.fixed,
    by_stage: byStage, by_page: byPage,
  };
}

// 桩：注入到页面上下文（在 shell.js / v2_notes_panel.js 之前执行）。
// 关键点：只接管 /api/ui_notes/*，其它请求原样交给真 fetch —— 否则 /_plugins 挂了壳就起不来。
const STUB = `(() => {
  const FIXTURE = ${JSON.stringify(FIXTURE)};
  let notes = FIXTURE.notes.map((n) => JSON.parse(JSON.stringify(n)));
  const stats = () => {
    const byStage = { open: 0, doing: 0, fixed: 0, verified: 0, rejected: 0 };
    const byPage = {};
    for (const n of notes) { byStage[n.stage] += 1; byPage[n.page] = (byPage[n.page] || 0) + 1; }
    return { total: notes.length, open: byStage.open + byStage.doing + byStage.fixed,
             by_stage: byStage, by_page: byPage };
  };
  const log = { list: 0, stage: 0, bodies: [], forced409: false };
  window.__npStub = log;
  window.__npForce409 = (on) => { log.forced409 = !!on; };
  const realFetch = window.fetch.bind(window);
  const json = (body, status) => new Response(JSON.stringify(body), {
    status: status || 200, headers: { 'Content-Type': 'application/json' },
  });
  window.fetch = (input, init) => {
    const url = String(input && input.url ? input.url : input);
    if (url.indexOf('/api/ui_notes/') < 0) return realFetch(input, init);
    const method = ((init && init.method) || 'GET').toUpperCase();
    if (url.indexOf('/api/ui_notes/list') >= 0) {
      log.list += 1;
      return Promise.resolve(json({ ok: true, notes, open: stats().open, stats: stats() }));
    }
    if (url.indexOf('/api/ui_notes/stage') >= 0 && method === 'POST') {
      log.stage += 1;
      let body = {};
      try { body = JSON.parse(init.body); } catch (e) { body = {}; }
      log.bodies.push(body);
      if (log.forced409) {
        return Promise.resolve(json({ detail: '阶段已变化（stub 强制）' }, 409));
      }
      const hit = notes.find((n) => n.id === body.id);
      if (!hit) return Promise.resolve(json({ detail: 'not found' }, 404));
      if (body.expected && body.expected !== hit.stage) {
        return Promise.resolve(json({ detail: '阶段已变化' }, 409));
      }
      hit.history = hit.history.concat([{ at: '2026-10-27 12:00:00', by: body.by || '',
        from: hit.stage, to: body.to, note: body.note || '', ref: body.ref || '' }]);
      hit.stage = body.to;
      hit.owner = body.by || hit.owner;
      hit.status = (body.to === 'verified' || body.to === 'rejected') ? 'resolved' : 'open';
      return Promise.resolve(json({ ok: true, note: hit, stats: stats() }));
    }
    // 其它 ui_notes 端点（add/update/resolve）：桩里一律 404，防止任何真实写盘
    return Promise.resolve(json({ detail: 'stub: 未实现（禁止真实写盘）' }, 404));
  };
})();`;

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}

class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id);
          this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result);
        }
      };
      this.ws.onopen = res; this.ws.onerror = rej;
    });
  }
  send(method, params) {
    const id = ++this.id;
    return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method, params: params || {} })); });
  }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 500));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) {} }
}

let ran = 0;
let failed = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 600) : '')); }
}

function notesCountOnDisk() {
  try {
    const doc = JSON.parse(fs.readFileSync(NOTES_FILE, 'utf8'));
    return Array.isArray(doc.notes) ? doc.notes.length : -1;
  } catch (e) { return -1; }
}

(async () => {
  if (!CHROME) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }

  const diskBefore = notesCountOnDisk();
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = path.join(os.tmpdir(), 'npfanel-' + Date.now());
  const port = 9700 + Math.floor(Math.random() * 90);
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1680,1000', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let k = 0; k < 100; k++) {
    try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); }
  }
  if (!targets) { console.log('SKIP: CDP 未就绪'); try { proc.kill(); } catch (e) {} process.exit(0); }
  const page = targets.find((t) => t.type === 'page');
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1680, height: 1000, deviceScaleFactor: 1, mobile: false });

  // 先注入桩，再导航：必须在任何页面脚本之前
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: STUB });
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });

  // 等壳起来（rail 按钮就位）
  let ready = false;
  for (let k = 0; k < 60; k++) {
    await sleep(250);
    ready = await cdp.eval(`!!document.getElementById('rail-notes')`);
    if (ready) break;
  }
  check('壳已启动，rail 出现 #rail-notes', ready === true);
  check('桩已就位（window.__npStub 存在）', (await cdp.eval('!!window.__npStub')) === true);

  // 1. 入口顺序
  const order = await cdp.eval(`(() => {
    const a = document.getElementById('rail-notes');
    const b = document.getElementById('rail-note');
    if (!a || !b) return null;
    const pos = a.compareDocumentPosition(b);
    return { beforeNote: !!(pos & Node.DOCUMENT_POSITION_FOLLOWING),
             label: a.getAttribute('aria-label'), title: a.title };
  })()`);
  check('#rail-notes 在 #rail-note 之上（DOM 顺序）', order && order.beforeNote === true, order);
  check('入口标题为「标注清单」', order && order.label === '标注清单', order);

  // 2. 打开抽屉
  await cdp.eval(`document.getElementById('rail-notes').click()`);
  let opened = false;
  for (let k = 0; k < 40; k++) {
    await sleep(150);
    opened = await cdp.eval(`!!document.querySelector('.np-drawer.on')`);
    if (opened) break;
  }
  check('点入口开抽屉（.np-drawer.on）', opened === true);
  check('抽屉复用全局 #scrim 并显示', (await cdp.eval(`document.getElementById('scrim').classList.contains('on')`)) === true);

  // 等滑入动画结束（transform .18s）：过渡中途量 rect 会得到「还在屏外」的假失败
  await sleep(320);
  const box = await cdp.eval(`(() => {
    const d = document.querySelector('.np-drawer');
    if (!d) return null;
    const r = d.getBoundingClientRect();
    // 用 clientWidth 而非 innerWidth：innerWidth 含滚动条宽（headless 下差 19px），
    // 而 fixed right:0 对齐的是不含滚动条的视口。
    const vw = document.documentElement.clientWidth;
    return { w: Math.round(r.width), right: Math.round(vw - r.right), z: getComputedStyle(d).zIndex };
  })()`);
  check('抽屉宽 440px 且贴右边缘', box && box.w === 440 && Math.abs(box.right) <= 2, box);

  // 3. 等列表渲染（桩即时返回）
  let rendered = 0;
  for (let k = 0; k < 40; k++) {
    await sleep(150);
    rendered = await cdp.eval(`document.querySelectorAll('.np-note').length`);
    if (rendered >= 5) break;
  }
  check('列表渲染出 5 条标注', rendered === 5, rendered);
  check('list 接口被调（桩计数 > 0）', (await cdp.eval('window.__npStub.list')) > 0);

  // 4. 头部统计来自桩 stats
  const stats = await cdp.eval(`(() => {
    const out = {};
    document.querySelectorAll('[data-np-stats] .np-stat').forEach((s) => {
      out[s.className.replace('np-stat is-', '').replace('np-stat', '').trim() || 'x'] = s.textContent;
    });
    return out;
  })()`);
  check('头部统计 5 阶段 + 总数（1/1/1/2/0 + 5）',
    stats && /1待处理/.test(stats.open) && /1处理中/.test(stats.doing) && /1已修复/.test(stats.fixed)
    && /2已验证/.test(stats.verified) && /0驳回/.test(stats.rejected) && /5总数/.test(stats.total), stats);

  // 5. 分组：卡名 / 整壳 / 退役置底
  const groups = await cdp.eval(`Array.from(document.querySelectorAll('.np-group')).map((g) => ({
    label: g.querySelector('.np-group-t').textContent,
    n: Number(g.querySelector('.np-group-n').textContent),
    retired: g.classList.contains('is-retired'),
    tag: g.querySelector('.np-tag') ? g.querySelector('.np-tag').textContent : '',
  }))`);
  check('分组 4 组（盘中 / 后台 / 整壳 / 旧页）', groups.length === 4, groups);
  check('卡标题来自壳 allCards（盘中 / 后台）',
    groups[0].label === '盘中' && groups[1].label === '后台', groups.slice(0, 2));
  check('旧页组置底并标「旧页已退役」',
    groups[3].retired === true && groups[3].tag === '旧页已退役', groups[3]);

  // 6. 组内倒序（N9002 created_at 更新，排 N9001 前）
  const intradayOrder = await cdp.eval(`(() => {
    const g = Array.from(document.querySelectorAll('.np-group'))
      .find((x) => x.querySelector('.np-group-t').textContent === '盘中');
    return Array.from(g.querySelectorAll('.np-note')).map((n) => n.getAttribute('data-id'));
  })()`);
  check('组内按 created_at 倒序（N9002 在 N9001 前）',
    intradayOrder.length === 2 && intradayOrder[0] === 'N9002', intradayOrder);

  await cdp.send('Page.captureScreenshot', { format: 'png' }).then((r) => {
    fs.writeFileSync(path.join(SHOT_DIR, 'panel_list.png'), Buffer.from(r.data, 'base64'));
    console.log('shot ' + path.join(SHOT_DIR, 'panel_list.png'));
  });

  // 7. chips 筛选
  await cdp.eval(`document.querySelector('.np-chip[data-stage="fixed"]').click()`);
  await sleep(250);
  const fixedOnly = await cdp.eval(`(() => ({
    n: document.querySelectorAll('.np-note').length,
    ids: Array.from(document.querySelectorAll('.np-note')).map((x) => x.getAttribute('data-id')),
    chipOn: document.querySelector('.np-chip[data-stage="fixed"]').classList.contains('is-on'),
  }))()`);
  check('chips 筛选「已修复」只剩 1 条', fixedOnly.n === 1 && fixedOnly.ids[0] === 'N9003', fixedOnly);
  check('选中 chip 高亮', fixedOnly.chipOn === true);

  // 8. 搜索（文本 / 元素 / id）
  await cdp.eval(`document.querySelector('.np-chip[data-stage="all"]').click()`);
  const search = async (q) => {
    await cdp.eval(`(() => {
      const el = document.querySelector('.np-q');
      el.value = ${JSON.stringify(q)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await sleep(180);
    return cdp.eval(`Array.from(document.querySelectorAll('.np-note')).map((x) => x.getAttribute('data-id'))`);
  };
  const byText = await search('刻度太密');
  check('搜索命中 text（刻度太密 → N9001）', byText.length === 1 && byText[0] === 'N9001', byText);
  const byElem = await search('表格容器');
  check('搜索命中 elem（表格容器 → N9003）', byElem.length === 1 && byElem[0] === 'N9003', byElem);
  const byId = await search('N9004');
  check('搜索命中 id（N9004）', byId.length === 1 && byId[0] === 'N9004', byId);
  const byPage = await search('ops::ops.main');
  check('搜索命中 page key（ops::ops.main → N9003）', byPage.length === 1 && byPage[0] === 'N9003', byPage);
  await search('');

  // 9. 展开：selector / 四段进度条 / history
  await cdp.eval(`document.querySelector('.np-note[data-id="N9003"] .np-note-hd').click()`);
  await sleep(200);
  const expanded = await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9003"]');
    if (!row || !row.classList.contains('is-open')) return null;
    return {
      sel: row.querySelector('.np-sel').textContent,
      segs: Array.from(row.querySelectorAll('.np-seg')).map((s) => s.className.replace('np-seg ', '')),
      his: Array.from(row.querySelectorAll('.np-his-row')).map((h) => h.textContent.replace(/\\s+/g, ' ').trim()),
      ref: row.querySelector('.np-ref') ? row.querySelector('.np-ref').textContent.trim() : '',
      acts: Array.from(row.querySelectorAll('.np-act[data-to]')).map((a) => a.textContent),
    };
  })()`);
  check('展开后显示 selector', expanded && expanded.sel === '.ops-wrap', expanded && expanded.sel);
  check('四段进度条：前两段已过、第三段当前',
    expanded && expanded.segs.join(',') === 'is-done,is-done,is-current,is-todo', expanded && expanded.segs);
  check('history 两条（含 from→to 与 ref）',
    expanded && expanded.his.length === 2 && /已修复/.test(expanded.his[1])
    && /DONE_20261027_annotation_ops_scroll\.md/.test(expanded.ref), expanded);
  check('fixed 的动作 = 验收通过 / 退回处理 / 驳回',
    expanded && expanded.acts.join(',') === '验收通过,退回处理,驳回', expanded && expanded.acts);

  await cdp.send('Page.captureScreenshot', { format: 'png' }).then((r) => {
    fs.writeFileSync(path.join(SHOT_DIR, 'panel_expand.png'), Buffer.from(r.data, 'base64'));
    console.log('shot ' + path.join(SHOT_DIR, 'panel_expand.png'));
  });

  // 10. 开始处理请求体（N9001: open → doing）
  await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9001"]');
    row.querySelector('.np-note-hd').click();
    return true;
  })()`);
  await sleep(200);
  await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9001"]');
    row.querySelector('[data-np-note]').value = '今天就改';
    row.querySelector('[data-np-note]').dispatchEvent(new Event('input', { bubbles: true }));
    row.querySelector('[data-np-ref]').value = 'docs/task_x.md';
    row.querySelector('[data-np-ref]').dispatchEvent(new Event('input', { bubbles: true }));
    row.querySelector('.np-act[data-to="doing"]').click();
    return true;
  })()`);
  let bodies = [];
  for (let k = 0; k < 30; k++) {
    await sleep(150);
    bodies = await cdp.eval('window.__npStub.bodies');
    if (bodies.length >= 1) break;
  }
  check('stage 请求体严格等于 {id,to:doing,expected:open,by:01,note,ref}',
    bodies.length === 1
    && bodies[0].id === 'N9001' && bodies[0].to === 'doing' && bodies[0].expected === 'open'
    && bodies[0].by === '01' && bodies[0].note === '今天就改' && bodies[0].ref === 'docs/task_x.md',
    bodies);
  await sleep(400);
  const afterStage = await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9001"]');
    return { badge: row ? row.querySelector('.np-badge').textContent : null,
             statsDoing: document.querySelector('[data-np-stats] .np-stat.is-doing b').textContent,
             listCalls: window.__npStub.list };
  })()`);
  check('提交后该条徽标变「处理中」', afterStage.badge === '处理中', afterStage);
  check('头部统计用响应 stats 更新（处理中 2）', afterStage.statsDoing === '2', afterStage);
  check('提交成功后静默全量重拉（list 调用数增加）', afterStage.listCalls >= 2, afterStage);

  // 11. 409 分支
  const beforeList = await cdp.eval('window.__npStub.list');
  await cdp.eval(`window.__npForce409(true)`);
  await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9002"]');
    if (!row.classList.contains('is-open')) row.querySelector('.np-note-hd').click();
    return true;
  })()`);
  await sleep(200);
  await cdp.eval(`document.querySelector('.np-note[data-id="N9002"] .np-act[data-to="fixed"]').click()`);
  let toastText = '';
  for (let k = 0; k < 30; k++) {
    await sleep(150);
    toastText = await cdp.eval(`(() => { const t = document.getElementById('toast'); return t && !t.hidden ? t.textContent : ''; })()`);
    if (/阶段已变化/.test(toastText)) break;
  }
  check('409 提示「阶段已变化，已刷新」', /阶段已变化/.test(toastText), toastText);
  await sleep(300);
  const after409 = await cdp.eval(`(() => ({
    list: window.__npStub.list,
    badge: document.querySelector('.np-note[data-id="N9002"] .np-badge').textContent,
  }))()`);
  check('409 后真的重拉了 list', after409.list > beforeList, { beforeList, after: after409.list });
  check('409 未改动该条阶段（仍处理中）', after409.badge === '处理中', after409);
  await cdp.eval(`window.__npForce409(false)`);

  // 12. 旧页那条不给定位按钮
  const retiredRow = await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9005"]');
    if (!row) return null;
    const open = row.classList.contains('is-open');
    if (!open) row.querySelector('.np-note-hd').click();
    return open;
  })()`);
  await sleep(250);
  const retired = await cdp.eval(`(() => {
    const row = document.querySelector('.np-note[data-id="N9005"]');
    return {
      locate: row.querySelectorAll('.np-act[data-np-act="locate"]').length,
      nolocate: row.querySelector('.np-nolocate') ? row.querySelector('.np-nolocate').textContent : '',
    };
  })()`);
  check('旧页标注无「定位」按钮，只有不可定位说明',
    retired.locate === 0 && /不可定位/.test(retired.nolocate), retired);

  // 13. 空态（搜一个不存在的词）
  await search('这个词肯定搜不到');
  const emptyText = await cdp.eval(`(() => { const e = document.querySelector('.np-empty'); return e ? e.textContent : ''; })()`);
  check('搜索无结果显示明确空态', /没有匹配/.test(emptyText), emptyText);
  await search('');

  // 14. 生产未被写：盘上 notes 条数不变
  const diskAfter = notesCountOnDisk();
  check('生产 ui_notes.json 未被写（条数不变）', diskAfter === diskBefore, { diskBefore, diskAfter });
  check('桩之外没有真实写请求（stage 全进桩）', (await cdp.eval('window.__npStub.stage')) >= 2);

  // 15. 关抽屉
  await cdp.eval(`document.querySelector('.np-x').click()`);
  await sleep(250);
  check('✕ 关闭抽屉并收起遮罩',
    (await cdp.eval(`!document.querySelector('.np-drawer.on') && !document.getElementById('scrim').classList.contains('on')`)) === true);

  cdp.close();
  try { proc.kill(); } catch (e) {}
  console.log('\n' + (failed === 0 ? 'PASS' : 'FAIL') + ' ' + (ran - failed) + '/' + ran);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.log('ERROR ' + (e && e.stack ? e.stack : e)); process.exit(1); });
