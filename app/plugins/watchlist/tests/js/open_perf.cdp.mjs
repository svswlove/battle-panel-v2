/**
 * 自选池卡 · 打开/重开耗时测量（真实浏览器，CDP → 8810）
 *
 * 跑法：
 *   node app/plugins/watchlist/tests/js/open_perf.cdp.mjs --runs 3 --tag after
 *   node app/plugins/watchlist/tests/js/open_perf.cdp.mjs --runs 3 --tag before \
 *        --before _vis/verify_20261028/frontend_before.js        # 用改前模块跑同一套量法
 *
 * 量什么（对应标注 N20261028-215730-47「自选池打开速度太慢」）：
 *   tHead   点侧栏「自选池」→ 卡头出字（.wl-meta 有内容）
 *   tCard   第一张 .wl-wc 出现
 *   tPaint  当前组所有个股卡副图落线（最后一根线画完）
 *   重开     首屏之后切到别的卡再切回自选 → 同样三个点（这条最能反映「切 Tab 太慢」）
 * 同时记录本轮 /api/ 请求的 (url, 次数, 总耗时, 最大耗时)，用于判断瓶颈在接口还是绘制。
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
const TAG = argOf('--tag', 'run');
const RUNS = Number(argOf('--runs', '3'));
const BEFORE = argOf('--before', '');
const BASE = argOf('--base', 'http://127.0.0.1:8810');
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
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.handlers = new Map(); this.events = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id); this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return;
        }
        if (m.method) {
          if (m.method.indexOf('Network.') === 0) this.events.push(m);
          if (this.handlers.has(m.method)) for (const fn of this.handlers.get(m.method)) fn(m.params).catch(() => {});
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

async function shot(cdp, name, selector) {
  const clipRaw = await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)return null;
    const r=el.getBoundingClientRect();
    return JSON.stringify({x:Math.max(0,r.left),y:Math.max(0,r.top),
      width:Math.min(r.width,1600),height:Math.min(r.height,1200)});})()`);
  const clip = clipRaw ? JSON.parse(clipRaw) : null;
  const params = { format: 'png' };
  if (clip && clip.width > 8 && clip.height > 8) params.clip = { ...clip, scale: 1 };
  const r = await cdp.send('Page.captureScreenshot', params);
  const f = path.join(SHOT_DIR, name + '.png');
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

/** 页面里注入的量测函数：点 tab → 轮询三个里程碑 */
const PROBE = (key) => `(async()=>{
  const w=(ms)=>new Promise(r=>setTimeout(r,ms));
  const now=()=>performance.now();
  const btn=document.querySelector('#rail-tabs .rail-btn[data-card-key="${key}"]');
  if(!btn) return JSON.stringify({err:'no rail btn'});
  const t0=now();
  btn.click();
  const poll=(fn,limit)=>{return new Promise(res=>{const s=now();
    const tick=()=>{ if(fn()){res(Math.round(now()-t0));return;} if(now()-s>limit){res(-1);return;} setTimeout(tick,16); };
    tick();});};
  const metaEl=()=>{const m=document.querySelector('.wl-meta'); return !!(m && (m.textContent||'').trim().length>0);};
  const firstCard=()=>!!document.querySelector('.wl-wc');
  const stockPainted=()=>{const cs=[...document.querySelectorAll('.wl-wc[data-kind="stock"]')];
    if(!cs.length) return false;
    return cs.every(c=>{const s=c.querySelector('.wl-sub'); return s && s.querySelectorAll('path').length>0;});};
  const out={};
  out.tHead=await poll(metaEl,20000);
  out.tCard=await poll(firstCard,20000);
  out.tPaint=await poll(stockPainted,30000);
  const root=document.querySelector('.wl-root')||document.body;
  out.cards=document.querySelectorAll('.wl-wc').length;
  out.svgs=root.querySelectorAll('svg').length;
  out.paths=root.querySelectorAll('svg path').length;
  out.domNodes=root.querySelectorAll('*').length;
  out.metaText=(document.querySelector('.wl-meta')||{}).textContent||'';
  return JSON.stringify(out);
})()`;

function netSummary(events) {
  const reqs = new Map();
  for (const e of events) {
    if (e.method === 'Network.requestWillBeSent') {
      const u = e.params.request.url;
      if (u.indexOf('/api/') >= 0) reqs.set(e.params.requestId, { url: u.replace(BASE, ''), t0: e.params.timestamp });
    } else if (e.method === 'Network.loadingFinished' && reqs.has(e.params.requestId)) {
      const r = reqs.get(e.params.requestId);
      r.ms = Math.round((e.params.timestamp - r.t0) * 1000);
      r.bytes = e.params.encodedDataLength;
    }
  }
  const by = {};
  for (const r of reqs.values()) {
    if (r.ms == null) continue;
    const k = r.url.split('?')[0].replace(/\/\d{6}(\.(SH|SZ|BJ))?$/, '/{code}');
    const b = by[k] || (by[k] = { n: 0, ms: 0, max: 0, bytes: 0 });
    b.n += 1; b.ms += r.ms; b.max = Math.max(b.max, r.ms); b.bytes += r.bytes || 0;
  }
  return by;
}

async function bootToWatchlist(cdp) {
  // 进 /v2/，等壳就绪（不点自选卡）
  await cdp.eval(`(async()=>{const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<120;i++){if(document.querySelector('#rail-tabs .rail-btn[data-card-key="${CARD_KEY}"]')) return true; await w(100);} return false;})()`);
  await sleep(800);
}

async function otherTabKey(cdp) {
  const raw = await cdp.eval(`JSON.stringify([...document.querySelectorAll('#rail-tabs .rail-btn')]
    .map(b=>b.dataset.cardKey).filter(k=>k && k!=='${CARD_KEY}'))`);
  const keys = JSON.parse(raw || '[]');
  return keys[0] || '';
}

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }
  fs.mkdirSync(SHOT_DIR, { recursive: true });

  const profile = path.join(os.tmpdir(), 'wlperf-' + Date.now());
  const port = 9700 + Math.floor(Math.random() * 90);
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
  await cdp.send('Network.enable');

  // 可选：把 watchlist 模块换成「改前」文件，量同一套指标
  let intercept = !!BEFORE;
  if (BEFORE) {
    const beforeSrc = fs.readFileSync(path.resolve(PANEL, BEFORE), 'utf8');
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
  }

  const first = [];
  const reopen = [];
  const ALT = !!BEFORE && process.argv.indexOf('--alternate') > 0;
  for (let i = 1; i <= RUNS; i += 1) {
    // --alternate：奇偶轮轮流跑「改前/改后」（同一浏览器会话，抵消服务端负载漂移）
    if (ALT) intercept = (i % 2 === 1);
    const variant = ALT ? (intercept ? 'before' : 'after') : TAG;
    cdp.events.length = 0;
    await cdp.send('Page.navigate', { url: BASE + '/v2/' });
    await bootToWatchlist(cdp);
    const r = JSON.parse(await cdp.eval(PROBE(CARD_KEY)));
    r.variant = variant;
    first.push(r);
    console.log(variant + ' first#' + i + ' ' + JSON.stringify(r) + ' api=' + JSON.stringify(netSummary(cdp.events)));

    // 重开：切到别的卡，再切回自选（这条最能反映「切 Tab 太慢」）
    const other = await otherTabKey(cdp);
    if (other) {
      await cdp.eval(`(document.querySelector('#rail-tabs .rail-btn[data-card-key="${other}"]')||{click(){}}).click()`);
      await sleep(1200);
      cdp.events.length = 0;
      const r2 = JSON.parse(await cdp.eval(PROBE(CARD_KEY)));
      // 重开之后还得能自动刷新：数 6.5s 内的接口请求（5s 轮询应命中 ≥1 次 watchlist.json）
      cdp.events.length = 0;
      await sleep(Number(argOf('--watch', '6500')));
      const net2 = netSummary(cdp.events);
      r2.refreshWl = (net2['/api/watchlist.json'] || { n: 0 }).n;
      r2.refreshKline = (net2['/api/kline/{code}'] || { n: 0 }).n;
      r2.variant = variant;
      reopen.push(r2);
      console.log(variant + ' reopen#' + i + ' ' + JSON.stringify(r2));
    }
    if (i === RUNS) await shot(cdp, 'watchlist_open_' + TAG, '.wl-root');
  }

  const med = (arr) => { const s = arr.filter((v) => v >= 0).sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : -1; };
  const group = (rows, v) => rows.filter((r) => r.variant === v);
  const variants = [...new Set(first.map((r) => r.variant))];
  const stats = {};
  for (const v of variants) {
    const f = group(first, v);
    const o = group(reopen, v);
    stats[v] = {
      n: f.length,
      firstHead: f.map((r) => r.tHead), firstPaint: f.map((r) => r.tPaint),
      medFirstHead: med(f.map((r) => r.tHead)), medFirstPaint: med(f.map((r) => r.tPaint)),
      reopenHead: o.map((r) => r.tHead), reopenPaint: o.map((r) => r.tPaint),
      medReopenHead: med(o.map((r) => r.tHead)), medReopenPaint: med(o.map((r) => r.tPaint)),
      refreshWl: o.map((r) => r.refreshWl), refreshKline: o.map((r) => r.refreshKline),
    };
  }
  console.log('SUMMARY ' + JSON.stringify({
    tag: TAG, runs: RUNS, alternate: ALT, variants: stats,
    lastCards: first[first.length - 1].cards, lastPaths: first[first.length - 1].paths,
    lastDom: first[first.length - 1].domNodes, meta: first[first.length - 1].metaText,
  }));
  cdp.close();
  proc.kill();
  await sleep(300);
  process.exit(0);
})();
