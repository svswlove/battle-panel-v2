// 对照：/legacy/（旧整页，同四份 JS 同 DOM）与插件挂载版的弹窗几何。
// 只读 GET，不改任何文件；打开/关闭用 KLineApp 自己的包装链。
// 跑法：node app/plugins/kline/tests/js/legacy_compare.cdp.mjs [--shots <dir>]

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const i = process.argv.indexOf('--shots');
const SHOT_DIR = i > 0 ? process.argv[i + 1] : path.join(HERE, '_shots_cmp');
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
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) {} }
}

const GEO = `JSON.stringify((()=>{
  const r=el=>{const b=el.getBoundingClientRect();return {l:Math.round(b.left),r:Math.round(b.right),t:Math.round(b.top),b:Math.round(b.bottom),w:Math.round(b.width),h:Math.round(b.height)};};
  const m=document.getElementById('klineModal');
  if(!m) return {missing:true};
  const box=m.querySelector('.kbox'), qp=document.getElementById('kQuotePanel'), fp=document.getElementById('kF10Panel');
  const shown=el=>getComputedStyle(el).display!=='none';
  return {vw:innerWidth,vh:innerHeight,
    box:r(box), khead:r(m.querySelector('.khead')), qp:r(qp), fp:r(fp),
    qShown:shown(qp), fShown:shown(fp),
    lcCanvas:document.querySelectorAll('#klineChart canvas').length,
    hqCanvas:document.querySelectorAll('#hq_minute canvas').length,
    title:(document.getElementById('klineTitle')||{}).textContent||'',
    status:(document.getElementById('klineStatus')||{}).textContent||''};})())`;

(async () => {
  if (!CHROME) { console.log('SKIP: 无 Chrome'); process.exit(0); }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = path.join(os.tmpdir(), 'klcmp-' + Date.now());
  const port = 9600 + Math.floor(Math.random() * 80);
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--hide-scrollbars',
    '--remote-debugging-port=' + port, '--user-data-dir=' + profile, '--window-size=1920,1080', 'about:blank'], { stdio: 'ignore' });
  let targets = null;
  for (let k = 0; k < 100; k++) { try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); } }
  if (!targets) { console.log('SKIP: CDP 未就绪'); proc.kill(); process.exit(0); }
  const cdp = new CDP(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });

  const shot = async (name) => {
    const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(SHOT_DIR, name), Buffer.from(r.data, 'base64'));
  };

  // ---- A 旧整页 /legacy/
  await cdp.send('Page.navigate', { url: BASE + '/legacy/' });
  await sleep(6000);
  await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
    if(!window.KLineApp) return 0;
    window.KLineApp.open('600036.SH','招商银行');
    for(let i=0;i<40;i++){ await w(250); if(document.querySelectorAll('#klineChart canvas').length>0) break; }
    await w(1200); return 1})()`);
  const legacy = JSON.parse(await cdp.eval(GEO));
  await shot('A_legacy_d_1920.png');

  // ---- B 插件挂载 /v2/
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  await sleep(2500);
  await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
    const mod = await import('/plugins/kline/frontend.js?v=0.7.2');
    await mod.openKlineStandalone({api:null, code:'600036.SH', name:'招商银行'});
    for(let i=0;i<40;i++){ await w(250); if(document.querySelectorAll('#klineChart canvas').length>0) break; }
    await w(1200); return 1})()`);
  const plugin = JSON.parse(await cdp.eval(GEO));
  await shot('B_plugin_d_1920.png');

  const keys = ['box', 'khead', 'qp', 'fp'];
  console.log('legacy  :', JSON.stringify(legacy));
  console.log('plugin  :', JSON.stringify(plugin));
  let bad = 0;
  for (const k of keys) {
    const a = legacy[k], b = plugin[k];
    const same = a && b && Math.abs(a.w - b.w) <= 2 && Math.abs(a.h - b.h) <= 2;
    console.log((same ? 'ok   ' : 'DIFF ') + k + '  legacy ' + JSON.stringify(a) + '  plugin ' + JSON.stringify(b));
    if (!same) bad++;
  }
  console.log((legacy.lcCanvas === plugin.lcCanvas ? 'ok   ' : 'DIFF ') + 'lcCanvas ' + legacy.lcCanvas + ' / ' + plugin.lcCanvas);
  console.log('截图: ' + SHOT_DIR);
  cdp.close(); proc.kill();
  await sleep(300);
  process.exit(bad ? 1 : 0);
})();
