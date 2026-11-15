/**
 * 盘中卡 · 收盘定格取证（CDP → 8810，真实页面，不改 8810）
 *
 * 跑法：node app/plugins/intraday/tests/js/close_freeze.cdp.mjs [--wait 45] [--shots <dir>]
 *
 * 对应主人 2026-10-27 N20261027-234539-35：
 *   「收盘期间就没必要刷新了，如果打开时间是 16:10（收盘自动取数之后）
 *     就直接取收盘后的数值进行一次最后的计算」
 *
 * 验三件事：
 *   ① 16:10 之后打开 → 卡头标「收盘定格」，不再假装实时；
 *   ② 16:10 之后的 20s 自动刷新**不再发请求**（数 /api/panic 的请求次数）；
 *   ③ 成交额对比在收盘态只出两行（今日全天 / 昨日全天），不再出现「预估今日全天（估算）100%」。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../../..');
const SHOT_DIR = (() => {
  const i = process.argv.indexOf('--shots');
  return i > 0 ? process.argv[i + 1] : path.join(PANEL, '_vis', 'verify_20261028');
})();
const WAIT_S = (() => {
  const i = process.argv.indexOf('--wait');
  return i > 0 ? Number(process.argv[i + 1]) || 45 : 45;
})();
const BASE = 'http://127.0.0.1:8810';
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

let failed = 0; let ran = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 600) : '')); }
}

async function shot(cdp, name, selector) {
  let clip = null;
  if (selector) {
    const raw = await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});
      if(!el) return null; el.scrollIntoView({block:'center'});
      const r=el.getBoundingClientRect();
      return JSON.stringify({x:Math.max(0,r.left),y:Math.max(0,r.top),
        width:Math.min(r.width,1600),height:Math.min(r.height,1000)});})()`);
    clip = raw ? JSON.parse(raw) : null;
    await sleep(350);
    if (clip) {
      const again = await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});
        const r=el.getBoundingClientRect();
        return JSON.stringify({x:Math.max(0,r.left),y:Math.max(0,r.top),
          width:Math.min(r.width,1600),height:Math.min(r.height,1000)});})()`);
      clip = again ? JSON.parse(again) : clip;
    }
  }
  const params = { format: 'png' };
  if (clip && clip.width > 8 && clip.height > 8) params.clip = { x: clip.x, y: clip.y, width: clip.width, height: clip.height, scale: 1 };
  const r = await cdp.send('Page.captureScreenshot', params);
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const f = path.join(SHOT_DIR, name + '.png');
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

(async () => {
  if (!CHROME) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }
  const now = new Date();
  const hhmm = now.getHours() * 60 + now.getMinutes();
  if (hhmm < 16 * 60 + 10) {
    console.log('SKIP: 现在 ' + now.toTimeString().slice(0, 5) + ' 还没到 16:10，收盘定格分支不成立（换个时点再跑）');
    process.exit(0);
  }

  const profile = path.join(os.tmpdir(), 'freeze-' + Date.now());
  const port = 9800 + Math.floor(Math.random() * 90);
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
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  await sleep(4000);

  const opened = await cdp.eval(`(async()=>{
    const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    const btn=[...document.querySelectorAll('#rail-tabs .rail-btn')]
      .find(b=>b.dataset.cardKey==='intraday::intraday.main');
    if(btn){ btn.click(); await w(2200); }
    const card=document.querySelector('.it-card');
    if(!card) return JSON.stringify({ok:false});
    return JSON.stringify({ok:true, sub:(card.querySelector('.it-sub')||{}).textContent||''});
  })()`);
  const O = JSON.parse(opened);
  check('盘中卡已打开', O.ok === true, O);
  check('16:10 之后卡头标「收盘定格」', /收盘定格/.test(O.sub || ''), O.sub);

  const tv = JSON.parse(await cdp.eval(`(()=>{
    const host=document.querySelector('.it-turnover-host'); if(!host) return JSON.stringify({err:'no host'});
    const rows=[...host.querySelectorAll('.to-kv')].map(kv=>({
      lab:(kv.querySelector('.to-lab')||{}).textContent||'',
      val:(kv.querySelector('.to-val')||{}).textContent||''}));
    return JSON.stringify({rows:rows, text:host.innerText.replace(/\\s+/g,' ').slice(0,300)});
  })()`));
  console.log('turnover ' + JSON.stringify(tv).slice(0, 500));
  check('成交额对比：收盘态只两行', (tv.rows || []).length === 2, tv.rows);
  check('第一行叫「今日全天」', /今日全天/.test((tv.rows[0] || {}).lab || ''), tv.rows);
  check('不再出现「预估今日全天…（估算）」行',
    !(tv.rows || []).some((r) => /预估|估算/.test((r.lab || '') + (r.val || ''))), tv.rows);
  await shot(cdp, 'freeze_01_turnover', '.it-turnover-host');
  await shot(cdp, 'freeze_02_head', '.it-card');

  // ---- ② 数自动刷新：记 /api/panic 的请求次数，等 WAIT_S 秒
  const before = cdp.events.filter((e) => e.method === 'Network.requestWillBeSent'
    && /\/api\/panic/.test(e.params.request.url)).length;
  await sleep(WAIT_S * 1000);
  const after = cdp.events.filter((e) => e.method === 'Network.requestWillBeSent'
    && /\/api\/panic/.test(e.params.request.url)).length;
  const trackReqs = cdp.events.filter((e) => e.method === 'Network.requestWillBeSent'
    && /\/api\/sector_track\.json/.test(e.params.request.url)).length;
  console.log('panic requests before=' + before + ' after=' + after + ' (等待 ' + WAIT_S + 's) track=' + trackReqs);
  check('16:10 后 ' + WAIT_S + 's 内没有新的自动取数（/api/panic 请求数不变）', after === before,
    { before: before, after: after });

  cdp.close();
  proc.kill();
  await sleep(200);
  console.log(failed ? ('FAIL ' + failed + '/' + ran) : ('PASS ' + ran + '/' + ran));
  process.exit(failed ? 1 : 0);
})();
