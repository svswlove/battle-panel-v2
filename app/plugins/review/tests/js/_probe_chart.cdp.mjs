// 复盘「图表」视图 · 现状盘点（只读，不改页面）
//   node app/plugins/review/tests/js/_probe_chart.cdp.mjs
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://127.0.0.1:8810';
const SHOT_DIR = path.join(HERE, '_shots');
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => {
      let b = '';
      r.on('data', (c) => { b += c; });
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.consoleErrors = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); }
        if (m.method === 'Runtime.exceptionThrown') this.consoleErrors.push(JSON.stringify(m.params).slice(0, 200));
      };
      this.ws.onopen = res; this.ws.onerror = rej;
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method, params })); });
  }
  eval(expression) {
    return this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }).then((x) => x.result && x.result.value);
  }
  close() { try { this.ws.close(); } catch (e) { /* ignore */ } }
}
const waitFor = async (cdp, expr, ms = 12000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await cdp.eval(expr)) return true; await sleep(220); }
  return false;
};

const MEASURE = `(() => {
  const q = (s) => document.querySelector(s);
  const root = q('#panel-review') || document.body;
  const tab = q('[data-rvview="chart"]');
  const sect = root.querySelector('.rv-chart-root, .rv-charts, .rv-chart') || root;
  const txt = (root.innerText || '').replace(/\\s+/g, ' ');
  const nodes = Array.from(root.querySelectorAll('[class*="rv-"]'));
  const byCls = {};
  for (const n of nodes) {
    for (const c of n.classList) { if (c.startsWith('rv-')) byCls[c] = (byCls[c] || 0) + 1; }
  }
  const canv = root.querySelectorAll('canvas').length;
  const svg = root.querySelectorAll('svg').length;
  return {
    tabFound: !!tab,
    activeTab: (q('[data-rvview].is-active, [data-rvview].active') || {}).dataset ? (q('[data-rvview].is-active, [data-rvview].active') || {}).dataset.rvview : null,
    chars: txt.length,
    canvas: canv, svg: svg,
    classes: byCls,
    textHead: txt.slice(0, 900),
    scrollH: document.documentElement.scrollHeight,
  };
})()`;

async function main() {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP 未找到 Chrome'); return 0; }
  const port = 9455;
  const profile = path.join(os.tmpdir(), 'rvchart-' + Date.now());
  const proc = spawn(chrome, ['--headless=new', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
  let cdp = null;
  try {
    let ver = null;
    for (let i = 0; i < 60 && !ver; i++) { try { ver = await getJson('http://127.0.0.1:' + port + '/json/version'); } catch (e) { await sleep(250); } }
    if (!ver) { console.log('SKIP CDP 起不来'); return 0; }
    const list = await getJson('http://127.0.0.1:' + port + '/json/list');
    const target = list.find((t) => t.type === 'page');
    cdp = new CDP(target.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await cdp.send('Page.navigate', { url: BASE + '/?card=review::review.main' });
    if (!(await waitFor(cdp, "!!document.querySelector('[data-rvview=\\\"chart\\\"]')"))) { console.log('FAIL 复盘卡未就绪'); return 1; }
    await cdp.eval("(() => { const b = document.querySelector('[data-rvview=\\\"chart\\\"]'); if (b) b.click(); return true; })()");
    await sleep(2500);
    await cdp.eval('window.scrollTo(0,0)');
    await sleep(400);
    const m = await cdp.eval(MEASURE);
    console.log(JSON.stringify(m, null, 1));
    await cdp.send('Page.captureScreenshot', { format: 'png' }).then((r) => {
      const p = path.join(SHOT_DIR, 'chart_now_1440.png');
      fs.writeFileSync(p, Buffer.from(r.data, 'base64'));
      console.log('截图: ' + p);
    });
    // 整页长图
    const mh = await cdp.eval('document.documentElement.scrollHeight');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: Math.min(6000, mh), deviceScaleFactor: 1, mobile: false });
    await sleep(800);
    await cdp.send('Page.captureScreenshot', { format: 'png' }).then((r) => {
      const p = path.join(SHOT_DIR, 'chart_now_full.png');
      fs.writeFileSync(p, Buffer.from(r.data, 'base64'));
      console.log('整页截图: ' + p + ' (h=' + mh + ')');
    });
    console.log('consoleErrors: ' + JSON.stringify(cdp.consoleErrors.slice(0, 5)));
    return 0;
  } finally {
    if (cdp) cdp.close();
    try { proc.kill(); } catch (e) { /* ignore */ }
  }
}
main().then((c) => process.exit(c)).catch((e) => { console.log('ERROR ' + (e && e.stack ? e.stack : e)); process.exit(2); });
