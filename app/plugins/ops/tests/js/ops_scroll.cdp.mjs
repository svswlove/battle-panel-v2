// 后台卡「去掉内部滑块」实测（标注 N20261027-012343-28）。
//
// 守的只有一件事：卡片里不再有任何自带滚动条的容器。
//   · 每个 .ops-wrap：clientHeight === scrollHeight、clientWidth === scrollWidth
//   · 每个 .ops-wrap 的 computed overflow-y / overflow-x 必须是 visible
//   · 滚动必须落在 V2 外壳的 .stage 上（stage.scrollHeight > stage.clientHeight 时能滚）
//   · 表头不得 sticky（没有内部滚动，sticky 无意义）
//   · 960 / 1920 两种宽度都过一遍，各存一张截图
//
// 用法：node app/plugins/ops/tests/js/ops_scroll.cdp.mjs [--shots <dir>]
// 前置：8810 在跑（tools/ensure_8810.ps1）。只读 GET，不打任何写端点。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const i = process.argv.indexOf('--shots');
const SHOT_DIR = i > 0 ? process.argv[i + 1] : path.join(HERE, '..', '..', '..', '..', '..', '_vis', 'ops_scroll');
const BASE = 'http://127.0.0.1:8810';
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
      this.ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); } };
      this.ws.onopen = res; this.ws.onerror = rej;
    });
  }
  send(method, params) {
    const id = ++this.id;
    return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method, params: params || {} })); });
  }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) {} }
}

// 观测量：卡片内每个可能的滚动容器逐个体检，外加 stage 的归属判断。
// 注意选择器前缀：selCard = 后台卡的卡片宿主（.card-host[data-card-key^="ops::"]）。
const PROBE = `JSON.stringify((()=>{
  const host=document.querySelector('.card-host[data-card-key^="ops::"]');
  const card=host?host.querySelector('.ops-card'):document.querySelector('.ops-card');
  const wraps=Array.from(document.querySelectorAll('.ops-wrap'));
  const one=(el)=>{
    const cs=getComputedStyle(el);
    return {
      cls:el.className,
      ch:el.clientHeight, sh:el.scrollHeight,
      cw:el.clientWidth, sw:el.scrollWidth,
      oy:cs.overflowY, ox:cs.overflowX,
      mh:cs.maxHeight,
      vScroll: el.scrollHeight - el.clientHeight,
      hScroll: el.scrollWidth - el.clientWidth,
    };
  };
  const stage=document.getElementById('stage');
  const th=document.querySelector('.ops-table th');
  return {
    card: !!card,
    activeKey: host?host.getAttribute('data-card-key'):null,
    rows: document.querySelectorAll('.ops-table tbody tr').length,
    tables: document.querySelectorAll('.ops-table').length,
    wraps: wraps.map(one),
    stage: stage ? { ch:stage.clientHeight, sh:stage.scrollHeight, oy:getComputedStyle(stage).overflowY,
                     canScroll: stage.scrollHeight > stage.clientHeight } : null,
    thPos: th ? getComputedStyle(th).position : null,
    tableLayout: (()=>{const t=document.querySelector('.ops-table'); return t?getComputedStyle(t).tableLayout:null;})(),
    cardScroll: card ? { ch:card.clientHeight, sh:card.scrollHeight } : null,
    innerScrollers: Array.from(document.querySelectorAll('.ops-card *')).filter(el=>{
      const cs=getComputedStyle(el);
      const scrollable=/(auto|scroll)/.test(cs.overflowY)||/(auto|scroll)/.test(cs.overflowX);
      if(!scrollable) return false;
      return el.scrollHeight>el.clientHeight+1 || el.scrollWidth>el.clientWidth+1;
    }).map(el=>({cls:el.className||el.tagName, dh:el.scrollHeight-el.clientHeight, dw:el.scrollWidth-el.clientWidth})),
  };})())`;

let ran = 0;
let failed = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 700) : '')); }
}

const VIEWS = [{ w: 1920, h: 1080, tag: '1920' }, { w: 960, h: 900, tag: '960' }, { w: 720, h: 900, tag: '720' }];

(async () => {
  if (!CHROME) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }

  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = path.join(os.tmpdir(), 'opsscroll-' + Date.now());
  const port = 9600 + Math.floor(Math.random() * 90);
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1920,1080', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let k = 0; k < 100; k++) {
    try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); }
  }
  if (!targets) { console.log('SKIP: CDP 未就绪'); proc.kill(); process.exit(0); }
  const page = targets.find((t) => t.type === 'page');
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  for (const v of VIEWS) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: v.w, height: v.h, deviceScaleFactor: 1, mobile: false });
    // 走真实入口：载入 /v2/，等侧栏出现，点「后台」那个 rail 按钮。
    // （`?card=` 只过滤卡片清单，初始卡仍取导航表第一张仪表盘，所以不能拿来直达后台卡。）
    await cdp.send('Page.navigate', { url: BASE + '/v2/' });
    let clicked = null;
    for (let k = 0; k < 60; k++) {
      await sleep(250);
      clicked = await cdp.eval(`(()=>{const b=document.querySelector('#rail-tabs .rail-btn[data-card-key="ops::ops.main"]');
        if(!b) return null; b.click(); return b.getAttribute('data-card-key');})()`);
      if (clicked) break;
    }
    check('[' + v.tag + '] 侧栏找到并点击后台卡入口', clicked === 'ops::ops.main', clicked);

    // 等卡片把三张表渲染出来（数据来自 /api/ops.json，本机 ~1s）
    let ok = 0;
    for (let k = 0; k < 60; k++) {
      await sleep(250);
      try { ok = await cdp.eval(`document.querySelectorAll('.ops-table tbody tr').length`); } catch (e) { ok = 0; }
      if (ok > 0) break;
    }
    await sleep(400);
    const g = JSON.parse(await cdp.eval(PROBE));
    const T = '[' + v.tag + '] ';

    check(T + '后台卡已渲染（三张表 + 行数 > 0）',
      g.card && g.activeKey === 'ops::ops.main' && g.tables === 3 && g.rows > 0,
      { card: g.card, key: g.activeKey, tables: g.tables, rows: g.rows });
    check(T + '.ops-wrap 数量 = 3', g.wraps.length === 3, g.wraps.length);

    for (let n = 0; n < g.wraps.length; n++) {
      const w = g.wraps[n];
      check(T + ('第 ' + (n + 1) + ' 张表 .ops-wrap 无纵向溢出（clientHeight=scrollHeight）'),
        w.vScroll === 0, w);
      check(T + ('第 ' + (n + 1) + ' 张表 .ops-wrap 无横向溢出（clientWidth=scrollWidth）'),
        w.hScroll === 0, w);
      check(T + ('第 ' + (n + 1) + ' 张表 .ops-wrap overflow-y=visible'),
        w.oy === 'visible', w.oy);
      check(T + ('第 ' + (n + 1) + ' 张表 .ops-wrap overflow-x=visible'),
        w.ox === 'visible', w.ox);
      check(T + ('第 ' + (n + 1) + ' 张表 .ops-wrap 无 max-height'),
        !w.mh || w.mh === 'none', w.mh);
    }

    check(T + '卡片自身无内部滚动（.ops-card clientHeight=scrollHeight）',
      g.cardScroll && g.cardScroll.ch === g.cardScroll.sh, g.cardScroll);
    check(T + '卡片内不存在任何溢出滑块元素', g.innerScrollers.length === 0, g.innerScrollers);
    check(T + '滚动归属于 .stage（overflow-y=auto）',
      g.stage && g.stage.oy === 'auto', g.stage);
    check(T + 'stage 内容高于视口时可滚（长表交给外壳）',
      g.stage && g.stage.canScroll === true, g.stage);
    check(T + '表头不 sticky（无内部滚动后无意义）',
      g.thPos !== 'sticky', g.thPos);
    check(T + '表格固定布局（桌面宽度内不撑破）', g.tableLayout === 'fixed', g.tableLayout);

    const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
    const f = path.join(SHOT_DIR, 'ops_card_' + v.tag + '.png');
    fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
    console.log('shot ' + f);

    // 窄屏分支（CSS @media(max-width:760px)）只在 720 生效；960 用来证明不代表窄屏。
    // 只取第一张表的首行：三张表各算一遍会把三组列数加在一起。
    const cols = JSON.parse(await cdp.eval(`JSON.stringify((()=>{
      const tds=Array.from(document.querySelectorAll('.ops-table'))[0].querySelectorAll('tbody tr:first-child td');
      return {cols:tds.length, hidden:Array.from(tds).filter(td=>getComputedStyle(td).display==='none').length};})())`));
    const expectHidden = v.w <= 760 ? 1 : 0;
    check(T + '第 4 列隐藏情况与宽度匹配（≤60px 隐、否则全显）',
      cols.cols >= 4 && cols.hidden === expectHidden, Object.assign({ expectHidden }, cols));
  }

  cdp.close();
  try { proc.kill(); } catch (e) {}
  console.log('\n' + (failed === 0 ? 'PASS' : 'FAIL') + ' ' + (ran - failed) + '/' + ran);
  process.exit(failed === 0 ? 0 : 1);
})().catch((e) => { console.log('ERROR ' + (e && e.stack ? e.stack : e)); process.exit(1); });
