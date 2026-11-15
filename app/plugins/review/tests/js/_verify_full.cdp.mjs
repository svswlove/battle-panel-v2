// 验：图表页「完整」模式下内容是否完整（不丢信息）+ 1920 首屏
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOT_DIR = path.join(HERE, '_shots');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function getJson(url) { return new Promise((res, rej) => { http.get(url, (r) => { let b = ''; r.on('data', (c) => { b += c; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej); }); }
class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.errs = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); } if (m.method === 'Runtime.exceptionThrown') this.errs.push(1); }; this.ws.onopen = res; this.ws.onerror = rej; }); }
  send(m, p = {}) { const id = ++this.id; return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method: m, params: p })); }); }
  eval(e) { return this.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true }).then((x) => x.result && x.result.value); }
  close() { try { this.ws.close(); } catch (e) {} }
}
const waitFor = async (c, e, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await c.eval(e)) return true; await sleep(220); } return false; };

const STAT = `(() => {
  const root = document.querySelector('#panel-review');
  if (!root) return null;
  const t = (root.innerText || '').replace(/\\s+/g, ' ');
  const secs = {};
  root.querySelectorAll('.rv-sec').forEach(s => {
    const no = (s.querySelector('.rv-sec-no')||{}).textContent || '?';
    const ti = (s.querySelector('.rv-sec-t')||{}).textContent || '';
    secs[no + ti] = (s.innerText||'').replace(/\\s+/g,' ').length;
  });
  return { chars: t.length, secs, hasPositions: /持仓/.test(t), hasSix: /六格/.test(t),
           hasLedger: /台账/.test(t), hasQA: /三问/.test(t), hasJudge: /每节判断/.test(t) };
})()`;

async function main() {
  const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
  const port = 9457;
  const proc = spawn(chrome, ['--headless=new', '--remote-debugging-port=' + port, '--user-data-dir=' + path.join(os.tmpdir(), 'rvfull-' + Date.now()), '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
  let cdp = null;
  try {
    let ver = null;
    for (let i = 0; i < 60 && !ver; i++) { try { ver = await getJson('http://127.0.0.1:' + port + '/json/version'); } catch (e) { await sleep(250); } }
    const list = await getJson('http://127.0.0.1:' + port + '/json/list');
    cdp = new CDP(list.find((t) => t.type === 'page').webSocketDebuggerUrl);
    await cdp.open(); await cdp.send('Runtime.enable'); await cdp.send('Page.enable');

    for (const [w, tag] of [[1920, '1920']]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: 1000, deviceScaleFactor: 1, mobile: false });
      await cdp.send('Page.navigate', { url: 'http://127.0.0.1:8810/?card=review::review.main' });
      await waitFor(cdp, "!!document.querySelector('[data-rvview=\"chart\"]')");
      await cdp.eval("(() => { const b=document.querySelector('[data-rvview=\"chart\"]'); if(b) b.click(); return true; })()");
      await sleep(2600);
      // 简报态
      const brief = await cdp.eval(STAT);
      console.log('[' + tag + '][简报] chars=' + brief.chars + ' pos=' + brief.hasPositions + ' six=' + brief.hasSix + ' ledger=' + brief.hasLedger);
      // 切「完整」
      const okFull = await cdp.eval(`(() => {
        const bs = Array.from(document.querySelectorAll('#panel-review .rv-mode-btn, #panel-review button'));
        const b = bs.find(x => /完整/.test((x.textContent||'').trim()));
        if (b) { b.click(); return b.textContent.trim(); }
        return null;
      })()`);
      console.log('  切完整: ' + okFull);
      await sleep(2200);
      const full = await cdp.eval(STAT);
      console.log('[' + tag + '][完整] chars=' + full.chars + ' pos=' + full.hasPositions + ' six=' + full.hasSix + ' ledger=' + full.hasLedger + ' qa=' + full.hasQA + ' judge=' + full.hasJudge);
      console.log('  各节: ' + JSON.stringify(full.secs, null, 0).slice(0, 900));
      await cdp.eval('window.scrollTo(0,0)');
      await cdp.send('Page.captureScreenshot', { format: 'png' }).then((r) => {
        fs.writeFileSync(path.join(SHOT_DIR, 'chart_verify_' + tag + '_full.png'), Buffer.from(r.data, 'base64'));
        console.log('  截图: chart_verify_' + tag + '_full.png');
      });
    }
    console.log('exceptions: ' + cdp.errs.length);
    return 0;
  } finally { if (cdp) cdp.close(); try { proc.kill(); } catch (e) {} }
}
main().then((c) => process.exit(c)).catch((e) => { console.log('ERROR ' + (e && e.stack ? e.stack : e)); process.exit(2); });
