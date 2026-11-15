// 真实入口验证：在 /v2/ 壳里点「个股名 / 图」按钮（走 shell.js ctxOpenKline），
// 确认旧弹窗真被弹出（不是只有直接调模块才能开）。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';

const BASE = 'http://127.0.0.1:8810';
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJson = (url) => new Promise((res, rej) => http.get(url, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej));
class CDP {
  constructor(u) { this.u = u; this.id = 0; this.p = new Map(); this.ev = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.u); this.ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return; } if (m.method) this.ev.push(m); }; this.ws.onopen = res; this.ws.onerror = rej; }); }
  send(method, params) { const id = ++this.id; return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method, params: params || {} })); }); }
  async eval(e) { const r = await this.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result ? r.result.value : undefined; }
  close() { try { this.ws.close(); } catch (e) { } }
}
(async () => {
  if (!CHROME) { console.log('SKIP'); process.exit(0); }
  const prof = path.join(os.tmpdir(), 'klentry-' + Date.now());
  const port = 9700 + Math.floor(Math.random() * 60);
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--hide-scrollbars',
    '--remote-debugging-port=' + port, '--user-data-dir=' + prof, '--window-size=1600,900', 'about:blank'], { stdio: 'ignore' });
  let t = null; for (let i = 0; i < 100; i++) { try { t = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); } }
  if (!t) { console.log('SKIP cdp'); proc.kill(); process.exit(0); }
  const cdp = new CDP(t.find((x) => x.type === 'page').webSocketDebuggerUrl);
  await cdp.open(); await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Log.enable');
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  await sleep(4000);
  // 壳里可点的个股名/图按钮（data-open-kline）或自选卡里的 K 线按钮
  const r = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
    // 真实路径：切到「选股」卡（卡内个股名带 data-open-kline 占位）→
    // shell 的捕获阶段委托 -> ctxOpenKline -> 插件 openKlineStandalone。
    const btns=Array.from(document.querySelectorAll('#rail-tabs .rail-btn'));
    const tab=btns.find(b=>/选股/.test(b.textContent||''));
    if(tab){ tab.click(); await w(6000); }
    const el=document.querySelector('[data-open-kline]');
    if(!el) return JSON.stringify({found:false, url:location.href,
      rail:btns.map(b=>(b.textContent||'').trim()).slice(0,12)});
    const code=el.getAttribute('data-open-kline')||'';
    const name=el.getAttribute('data-open-name')||'';
    el.click(); await w(2500);
    for(let i=0;i<48;i++){ await w(250); const m=document.getElementById('klineModal'); if(m&&m.style.display==='flex'&&document.querySelectorAll('#klineChart canvas').length>0) break; }
    const m=document.getElementById('klineModal');
    return JSON.stringify({found:true, code:code, name:name,
      shown:m?m.style.display:null,
      title:(document.getElementById('klineTitle')||{}).textContent||'',
      canvas:document.querySelectorAll('#klineChart canvas').length,
      legacy:!!window.KLineApp,
      err:(document.getElementById('kline-legacy-error')||{}).textContent||''});})()`);
  console.log(r);
  const J = JSON.parse(r);
  const ex = cdp.ev.filter((e) => e.method === 'Runtime.exceptionThrown');
  console.log('console exceptions: ' + ex.length);
  const ok = J.found && J.shown === 'flex' && J.canvas >= 2 && J.legacy === true && !J.err && ex.length === 0;
  console.log(ok ? 'PASS 真实入口（选股卡点个股名）可开旧弹窗' : 'FAIL 真实入口未开');
  cdp.close(); proc.kill(); await sleep(200);
  process.exit(ok ? 0 : 1);
})();
