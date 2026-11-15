// dump 图表页各 section 的结构与文本量
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => { let b = ''; r.on('data', (c) => { b += c; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}
class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); } }; this.ws.onopen = res; this.ws.onerror = rej; }); }
  send(method, params = {}) { const id = ++this.id; return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method, params })); }); }
  eval(expression) { return this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }).then((x) => x.result && x.result.value); }
  close() { try { this.ws.close(); } catch (e) {} }
}
const waitFor = async (cdp, expr, ms = 12000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (await cdp.eval(expr)) return true; await sleep(220); } return false; };

const DUMP = `(() => {
  const root = document.querySelector('#panel-review') || document.body;
  const out = [];
  root.querySelectorAll('.rv-sec').forEach((s, i) => {
    const t = (s.querySelector('.rv-sec-t') || {}).textContent || '';
    const no = (s.querySelector('.rv-sec-no') || {}).textContent || '';
    const sub = (s.querySelector('.rv-sec-sub') || {}).textContent || '';
    const txt = (s.innerText || '').replace(/\\s+/g, ' ').trim();
    out.push({ i, no, title: t.trim(), sub: sub.trim(), chars: txt.length,
               svg: s.querySelectorAll('svg').length,
               tables: s.querySelectorAll('table').length,
               rows: s.querySelectorAll('tr').length,
               clamp: s.querySelectorAll('.rv-clamp3, .rv-jdet, .rv-note').length,
               head: txt.slice(0, 160) });
  });
  return { sections: out, totalChars: (root.innerText||'').length };
})()`;

async function main() {
  const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP'); return 0; }
  const port = 9456;
  const profile = path.join(os.tmpdir(), 'rvdump-' + Date.now());
  const proc = spawn(chrome, ['--headless=new', '--remote-debugging-port=' + port, '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
  let cdp = null;
  try {
    let ver = null;
    for (let i = 0; i < 60 && !ver; i++) { try { ver = await getJson('http://127.0.0.1:' + port + '/json/version'); } catch (e) { await sleep(250); } }
    const list = await getJson('http://127.0.0.1:' + port + '/json/list');
    cdp = new CDP(list.find((t) => t.type === 'page').webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send('Runtime.enable'); await cdp.send('Page.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await cdp.send('Page.navigate', { url: 'http://127.0.0.1:8810/?card=review::review.main' });
    await waitFor(cdp, "!!document.querySelector('[data-rvview=\\\"chart\\\"]')");
    await cdp.eval("(() => { const b = document.querySelector('[data-rvview=\\\"chart\\\"]'); if (b) b.click(); return true; })()");
    await sleep(2500);
    const m = await cdp.eval(DUMP);
    console.log('totalChars=' + m.totalChars);
    for (const s of m.sections) {
      console.log(`[${s.no}] ${s.title} ｜sub=${s.sub} ｜chars=${s.chars} svg=${s.svg} tr=${s.rows} clamp=${s.clamp}`);
      console.log('     ' + s.head);
    }
    return 0;
  } finally { if (cdp) cdp.close(); try { proc.kill(); } catch (e) {} }
}
main().then((c) => process.exit(c)).catch((e) => { console.log('ERROR ' + (e && e.stack ? e.stack : e)); process.exit(2); });
