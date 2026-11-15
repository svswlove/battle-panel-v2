/**
 * 盘中卡 · 打开到首屏耗时实测（CDP → 8810，真实页面）
 *
 * 跑法：node app/plugins/intraday/tests/js/mount_speed.cdp.mjs [--repeat 3]
 *
 * 回答主人 2026-10-27 N20261027-234539-35 的问题：「每次打开后都会有一定时间的延迟，
 * 是因为它要重新计算吗？」——先把「延迟」量出来，再谈改法。
 *
 * 口径（都是「点侧栏『盘中』那一刻」起的毫秒）：
 *   t_card    .it-card 进 DOM（骨架可见）
 *   t_panic   冰点区出现数值（第一个真正有内容的块）
 *   t_turnover 成交额对比出现金额
 *   t_sector  板块轮动网格出现格子
 *   t_evo     日内演化左图 svg 出现
 * 同时打出五个接口的实际耗时，便于把「慢在哪」对上。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';

const BASE = 'http://127.0.0.1:8810';
const CARD_URL = BASE + '/v2/';
const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJson = (url) => new Promise((res, rej) => {
  http.get(url, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
});

class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.events = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return; }
        if (m.method) this.events.push(m);
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
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) { /* 已关 */ } }
}

/** 页面内一次性装好「时间戳打点」：用 MutationObserver 记第一次出现的时刻 */
const TAP = `(()=>{
  if (window.__tap) return 1;
  const T = {t0: performance.now(), marks: {}};
  window.__tap = T;
  const set = (k) => { if (T.marks[k] == null) T.marks[k] = Math.round(performance.now() - T.t0); };
  const seen = () => {
    if (document.querySelector('.it-card')) set('card');
    const hp = document.querySelector('.it-panic .it-panic-hero, .it-panic-hero');
    if (hp && /[0-9]/.test(hp.textContent || '')) set('panic');
    const tv = document.querySelector('.it-turnover-host .to-val');
    if (tv && /[0-9]/.test(tv.textContent || '')) set('turnover');
    if (document.querySelector('.st-body svg, .st-body canvas, .st-body .st-cell, .st-body .st-bub')) set('sector');
    if (document.querySelector('.evo-body svg.st-chart')) set('evo');
  };
  new MutationObserver(seen).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  seen();
  return 1;
})()`;

(async () => {
  if (!CHROME) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }
  const rep = (() => { const i = process.argv.indexOf('--repeat'); return i > 0 ? Number(process.argv[i + 1]) || 3 : 3; })();

  const profile = path.join(os.tmpdir(), 'mspeed-' + Date.now());
  const port = 9900 + Math.floor(Math.random() * 90);
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
  let targets = null;
  for (let i = 0; i < 100; i++) { try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); } }
  if (!targets) { console.log('SKIP: cdp 起不来'); proc.kill(); process.exit(0); }
  const cdp = new CDP(targets.find((x) => x.type === 'page').webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Network.enable');
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: TAP });
  await cdp.send('Page.navigate', { url: CARD_URL });
  await sleep(4500);

  const rows = [];
  for (let k = 0; k < rep; k++) {
    // 每次先离开盘中卡，保证是「冷挂载」（shell 会 unmount，状态与 DOM 都清掉）
    await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
      const rail=()=>[...document.querySelectorAll('#rail-tabs .rail-btn')];
      const other=rail().find(b=>b.dataset.cardKey && b.dataset.cardKey!=='intraday::intraday.main');
      if(other){ other.click(); await w(900); }
      window.__tap = null; return 1;})()`);
    await sleep(300);
    await cdp.send('Runtime.evaluate', { expression: TAP, returnByValue: true });
    await sleep(50);
    const t = await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
      window.__tap.t0 = performance.now();
      const btn=[...document.querySelectorAll('#rail-tabs .rail-btn')].find(b=>b.dataset.cardKey==='intraday::intraday.main');
      if(!btn) return JSON.stringify({err:'no rail button'});
      btn.click();
      for(let i=0;i<120;i++){ await w(50); const m=window.__tap.marks;
        if(m.card!=null&&m.panic!=null&&m.turnover!=null&&m.sector!=null&&m.evo!=null) break; }
      return JSON.stringify(window.__tap.marks);})()`);
    rows.push(JSON.parse(t));
    await sleep(600);
  }

  console.log('== 打开耗时（毫秒，自点『盘中』起）==');
  rows.forEach((r, i) => console.log('  #' + (i + 1) + ' ' + JSON.stringify(r)));

  // 接口耗时（同一时段再打一次，便于对照）
  const t0 = Date.now();
  const eps = ['/api/panic', '/api/amv_live.json', '/api/breadth_880.json', '/api/p/intraday/summary',
    '/api/intraday.json', '/api/sector_track.json'];
  const tm = {};
  for (const e of eps) {
    const s = Date.now();
    try { await getJson(BASE + e); } catch (err) { tm[e] = 'ERR'; continue; }
    tm[e] = (Date.now() - s) + 'ms';
  }
  console.log('== 接口耗时 ==  ' + JSON.stringify(tm) + '  (总 ' + (Date.now() - t0) + 'ms)');

  cdp.close();
  proc.kill();
  await sleep(200);
  process.exit(0);
})();
