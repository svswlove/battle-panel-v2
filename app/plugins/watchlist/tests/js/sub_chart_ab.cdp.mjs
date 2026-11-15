/**
 * 自选池个股卡 · 副图 A/B 截图（真实浏览器，CDP → 8810）
 *
 * 跑法：node app/plugins/watchlist/tests/js/sub_chart_ab.cdp.mjs \
 *         --before _vis/verify_20261028/frontend_before.js --shots _vis/verify_20261028
 *
 * 做什么：
 *   ① 用 Fetch 域把 /plugins/watchlist/frontend.js 这一条请求换成「改前」文件 → 截图（同票）
 *   ② 关掉拦截、重新加载 → 截「改后」同一张卡
 * 同一只票、同一批 bars、同一个 1600×1000 视口，差别只有副图那两条线。
 * 只读：不改 state、不写后端；产物只有截图。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../../..');
const argOf = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const SHOT_DIR = path.resolve(PANEL, argOf('--shots', '_vis/verify_20261028'));
const BEFORE = path.resolve(PANEL, argOf('--before', '_vis/verify_20261028/frontend_before.js'));
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
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.handlers = new Map(); }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id); this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return;
        }
        if (m.method && this.handlers.has(m.method)) {
          for (const fn of this.handlers.get(m.method)) fn(m.params).catch(() => {});
        }
      };
      this.ws.onopen = res; this.ws.onerror = rej;
    });
  }
  on(method, fn) { if (!this.handlers.has(method)) this.handlers.set(method, []); this.handlers.get(method).push(fn); }
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

async function shotCard(cdp, name, code) {
  const clip = await cdp.eval(`(()=>{const el=document.querySelector('.wl-wc[data-code="${code}"]');
    if(!el) return null; el.scrollIntoView({block:'center'});
    const r=el.getBoundingClientRect();
    return JSON.stringify({x:Math.max(0,r.left-6),y:Math.max(0,r.top-6),
      width:Math.min(r.width+12,1600),height:Math.min(r.height+12,1000)});})()`);
  const c = clip ? JSON.parse(clip) : null;
  await sleep(300);
  const params = { format: 'png' };
  if (c) params.clip = { ...c, scale: 2 };
  const r = await cdp.send('Page.captureScreenshot', params);
  const f = path.join(SHOT_DIR, name + '.png');
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

/** 打开自选卡并等到指定票的副图落线；返回副图 path 的 class 列表 */
async function openAndInspect(cdp, code) {
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<80;i++){const b=document.querySelector('#rail-tabs .rail-btn[data-card-key="${CARD_KEY}"]');
      if(b){b.click();return true;} await w(100);} return false;})()`);
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<200;i++){const s=document.querySelector('.wl-wc[data-code="${code}"] .wl-sub');
      if(s && s.querySelectorAll('path').length) return true; await w(100);} return false;})()`);
  await sleep(500);
  const info = await cdp.eval(`(()=>{const el=document.querySelector('.wl-wc[data-code="${code}"]');
    if(!el) return JSON.stringify({ok:false});
    const sub=el.querySelector('.wl-sub');
    const paths=[...sub.querySelectorAll('path')].map(p=>({cls:p.getAttribute('class'),dlen:(p.getAttribute('d')||'').length,
      head:(p.getAttribute('d')||'').slice(0,40)}));
    const main=el.querySelector('.wl-main');
    return JSON.stringify({ok:true, sub:paths, mainPaths:main.querySelectorAll('path').length,
      price:(el.querySelector('.wl-price')||{}).textContent||'', chg:(el.querySelector('.wl-chg')||{}).textContent||''});})()`);
  return JSON.parse(info);
}

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  if (!fs.existsSync(BEFORE)) { console.log('SKIP: 缺改前文件 ' + BEFORE); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const beforeSrc = fs.readFileSync(BEFORE, 'utf8');

  // 同一只票：取自选池当前组第一只
  const d = await getJson(BASE + '/api/watchlist.json');
  const stock = (d.items || []).find((x) => x.kind === 'stock' && x.group === 'stock_default');
  const code = stock.code;
  console.log('stock=' + code + ' ' + stock.name);

  const profile = path.join(os.tmpdir(), 'wlab-' + Date.now());
  const port = 9800 + Math.floor(Math.random() * 90);
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

  // ---- ① 改前（拦截 watchlist 模块，喂改前文件）
  let intercept = true;
  cdp.on('Fetch.requestPaused', async (p) => {
    if (!intercept) { await cdp.send('Fetch.continueRequest', { requestId: p.requestId }); return; }
    await cdp.send('Fetch.fulfillRequest', {
      requestId: p.requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: 'text/javascript; charset=utf-8' },
        { name: 'Cache-Control', value: 'no-store' }],
      body: Buffer.from(beforeSrc, 'utf8').toString('base64'),
    });
  });
  await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*/plugins/watchlist/frontend.js*', requestStage: 'Request' }] });
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  const before = await openAndInspect(cdp, code);
  const f1 = await shotCard(cdp, 'sub_before_' + code.split('.')[0], code);
  console.log('BEFORE ' + JSON.stringify(before));

  // ---- ② 改后（放行真实模块）
  intercept = false;
  await cdp.send('Fetch.disable');
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  const after = await openAndInspect(cdp, code);
  const f2 = await shotCard(cdp, 'sub_after_' + code.split('.')[0], code);
  console.log('AFTER ' + JSON.stringify(after));
  console.log('shots -> ' + f1 + ' | ' + f2);

  const okBefore = before.ok && (before.sub || []).some((p) => p.cls === 'wl-pc');
  const okAfter = after.ok && (after.sub || []).length === 3
    && ['wl-amv', 'wl-amv-ma', 'wl-amv-wh'].every((c) => (after.sub || []).some((p) => p.cls === c));
  console.log(okBefore && okAfter ? 'PASS' : 'FAIL');
  cdp.close();
  proc.kill();
  await sleep(300);
  process.exit(okBefore && okAfter ? 0 : 1);
})();
