/**
 * 自选池卡 · 实时档回归（真实浏览器，CDP → 8810）
 *
 * 跑法：node app/plugins/watchlist/tests/js/live_mode.cdp.mjs
 *
 * 为什么单独验：改副图（标注 41）和改缓存/重绘（标注 47）都动了 paint/hydrate 的路径，
 * 盘后档验过不代表实时档没坏 —— 实时档副图是量柱（rect），而且 render 在实时档永不跳过重绘。
 * 只读：不改 state、不写后端。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../../..');
const BASE = 'http://127.0.0.1:8810';
const CARD_KEY = 'watchlist::watchlist.main';
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJson = (url) => new Promise((res, rej) => {
  http.get(url, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
});

class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.exceptions = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id); this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return;
        }
        if (m.method === 'Runtime.exceptionThrown') this.exceptions.push(JSON.stringify(m.params).slice(0, 300));
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
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) { /* 已关 */ } }
}

let failed = 0;
const check = (name, ok, detail) => {
  if (ok) console.log('ok - ' + name);
  else { failed += 1; console.log('NOT OK - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 400) : '')); }
};

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }

  const profile = path.join(os.tmpdir(), 'wllive-' + Date.now());
  const port = 9900 + Math.floor(Math.random() * 90);
  const proc = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
  let targets = null;
  for (let i = 0; i < 100; i++) { try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); } }
  if (!targets) { console.log('SKIP: cdp 起不来'); proc.kill(); process.exit(0); }

  const cdp = new CDP(targets.find((x) => x.type === 'page').webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<120;i++){const b=document.querySelector('#rail-tabs .rail-btn[data-card-key="${CARD_KEY}"]');
      if(b){b.click();return true;} await w(100);} return false;})()`);
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<200;i++){const s=document.querySelector('.wl-wc[data-kind="stock"] .wl-sub');
      if(s && s.querySelectorAll('path').length) return true; await w(100);} return false;})()`);

  const eod = JSON.parse(await cdp.eval(`(()=>{const c=document.querySelector('.wl-wc[data-kind="stock"]');
    const s=c.querySelector('.wl-sub');
    return JSON.stringify({paths:[...s.querySelectorAll('path')].map(p=>p.getAttribute('class')),mode:(document.querySelector('.wl-meta')||{}).textContent||''});})()`));
  check('盘后档副图是 AMV 三条线', eod.paths.length === 3 && eod.paths.indexOf('wl-amv-ma') >= 0, eod);

  // 切实时档
  await cdp.eval(`(()=>{const b=document.querySelector('.wl-sw'); if(b) b.click(); return true;})()`);
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<200;i++){const s=document.querySelector('.wl-wc[data-kind="stock"] .wl-sub');
      if(s && s.querySelectorAll('rect').length) return true; await w(100);} return false;})()`);
  const live = JSON.parse(await cdp.eval(`(()=>{const c=document.querySelector('.wl-wc[data-kind="stock"]');
    const s=c.querySelector('.wl-sub');
    return JSON.stringify({rects:s.querySelectorAll('rect').length, paths:s.querySelectorAll('path').length,
      mainPaths:c.querySelectorAll('.wl-main path').length,
      mode:(document.querySelector('.wl-meta')||{}).textContent||''});})()`));
  check('实时档副图是量柱', live.rects > 0 && live.paths === 0, live);
  check('实时档主图有分时/均价两条线', live.mainPaths === 2, live);
  check('卡头显示「实时档」', /实时档/.test(live.mode), live.mode);

  // 实时档下等一轮 5s 刷新，确认还在重绘（不因缓存跳过而冻住）
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    window.__t0=performance.now(); await w(5600);
    const s=document.querySelector('.wl-wc[data-kind="stock"] .wl-sub');
    return JSON.stringify({rects:s.querySelectorAll('rect').length});})()`);
  const after = JSON.parse(await cdp.eval(`(()=>{const s=document.querySelector('.wl-wc[data-kind="stock"] .wl-sub');
    return JSON.stringify({rects:s.querySelectorAll('rect').length});})()`));
  check('实时档 5s 后量柱仍在（未冻住）', after.rects > 0, after);

  check('无 JS 异常', cdp.exceptions.length === 0, cdp.exceptions.slice(0, 2));
  cdp.close();
  proc.kill();
  await sleep(200);
  console.log(failed ? 'FAIL ' + failed : 'PASS');
  process.exit(failed ? 1 : 0);
})();
