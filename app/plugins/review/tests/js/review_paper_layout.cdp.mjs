// 复盘「纸面」视图 · 版面对齐实机核对（Chrome CDP，只读；改版前后都能跑）
//   node app/plugins/review/tests/js/review_paper_layout.cdp.mjs
//        [--base http://127.0.0.1:8810] [--shots <dir>] [--tag before|after]
//
// 打印（每个视口一行 JSON）：
//   mh/brief/doc 三段 rect（left/right/width）、正文首块 rect、结论卡字号、语义块计数
// 截图：<tag>_paper_1440.png / <tag>_paper_1920.png（视口整屏，不展开全文）
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, dflt) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const BASE = arg('--base', 'http://127.0.0.1:8810');
const SHOT_DIR = arg('--shots', path.join(HERE, '_shots'));
const TAG = arg('--tag', 'after');
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
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id);
          this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result);
          return;
        }
        if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
          this.consoleErrors.push((m.params.args || []).map((a) => a.value || a.description).join(' '));
        }
      };
      this.ws.onopen = res;
      this.ws.onerror = rej;
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
  close() { try { this.ws.close(); } catch (e) { /* ignore */ } }
}

async function waitFor(cdp, expr, ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await cdp.eval(expr)) return true;
    await sleep(250);
  }
  return false;
}

const MEASURE = `(() => {
  const q = (s) => document.querySelector(s);
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return { left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
             top: Math.round(r.top), fs: cs.fontSize, color: cs.color };
  };
  const paper = q('.rv-paper');
  const sheet = q('.rv-paper .sheet');
  const mh = q('.rv-paper .mh-band');
  const brief = q('.rv-paper .brief');
  const doc = q('.rv-paper .doc');
  const first = doc ? doc.firstElementChild : null;
  const p1 = doc ? doc.querySelector('p') : null;
  const tk = q('.rv-paper .takeaway');
  const kc = q('.rv-paper .keycard');
  const al = q('.rv-paper .alert');
  const major = q('.rv-paper .sec--major');
  const tbl = q('.rv-paper .table-wrapper');
  return {
    vw: window.innerWidth,
    paper: box(paper), sheet: box(sheet), mh: box(mh), brief: box(brief), doc: box(doc),
    firstBlock: first ? { cls: first.className, ...box(first) } : null,
    firstP: box(p1),
    takeaway: box(tk), keycard: box(kc), alert: box(al),
    secMajor: box(major), tableWrap: box(tbl),
    counts: {
      takeaway: document.querySelectorAll('.rv-paper .takeaway').length,
      keycard: document.querySelectorAll('.rv-paper .keycard').length,
      alert: document.querySelectorAll('.rv-paper .alert').length,
      quote: document.querySelectorAll('.rv-paper .quote').length,
    },
    docScrollW: doc ? doc.scrollWidth : null,
    docClientW: doc ? doc.clientWidth : null,
    quoteHasTarget: Array.from(document.querySelectorAll('.rv-paper .quote'))
      .some((d) => /🎯|📌|🚨/.test(d.textContent || '')),
    topline: (q('.rv-card-body .rv-topline') || {}).textContent || '',
  };
})()`;

async function main() {
  const probe = await fetch(BASE + '/').then((r) => r.status).catch(() => 0);
  if (probe !== 200) { console.log('SKIP 实例未启动 ' + BASE); return 0; }
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP 无 Chrome/Edge'); return 0; }

  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'rvp-'));
  const port = 9498;
  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
    '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1440,1000', 'about:blank',
  ], { stdio: 'ignore' });

  let cdp = null;
  try {
    let ver = null;
    for (let i = 0; i < 60 && !ver; i++) {
      try { ver = await getJson('http://127.0.0.1:' + port + '/json/version'); } catch (e) { await sleep(250); }
    }
    if (!ver) { console.log('SKIP CDP 起不来'); return 0; }
    const list = await getJson('http://127.0.0.1:' + port + '/json/list');
    const target = list.find((t) => t.type === 'page');
    cdp = new CDP(target.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');

    for (const width of [1440, 1920]) {
      await cdp.send('Emulation.setDeviceMetricsOverride',
        { width, height: 1000, deviceScaleFactor: 1, mobile: false });
      await cdp.send('Page.navigate', { url: BASE + '/?card=review::review.main' });
      if (!(await waitFor(cdp, "!!document.querySelector('[data-rvview=\"paper\"]')"))) {
        console.log('FAIL 复盘卡未就绪 ' + width);
        return 1;
      }
      await cdp.eval(`(() => {
        const b = document.querySelector('[data-rvview="paper"]');
        if (b) b.click();
        document.documentElement.style.setProperty('--probe', '1');
        return true;
      })()`);
      if (!(await waitFor(cdp, "!!document.querySelector('.rv-paper .doc')"))) {
        console.log('FAIL 纸面未渲染 ' + width);
        return 1;
      }
      await sleep(1500);
      // 滚到顶部再拍（页头 + 报头 + 结论带要同屏）
      await cdp.eval('window.scrollTo(0,0)');
      await sleep(300);
      const m = await cdp.eval(MEASURE);
      console.log('[' + width + '] ' + JSON.stringify(m));
      const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
      const p = path.join(SHOT_DIR, TAG + '_paper_' + width + '.png');
      fs.writeFileSync(p, Buffer.from(r.data, 'base64'));
      console.log('  截图: ' + p);
    }
    console.log('consoleErrors: ' + JSON.stringify(cdp.consoleErrors.slice(0, 5)));
    return 0;
  } finally {
    if (cdp) cdp.close();
    try { proc.kill(); } catch (e) { /* ignore */ }
  }
}

main().then((c) => process.exit(c)).catch((e) => { console.log('ERROR ' + (e && e.stack ? e.stack : e)); process.exit(2); });
