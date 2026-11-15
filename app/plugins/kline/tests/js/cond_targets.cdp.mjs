/**
 * 两类低吸条件单（回踩均线 / 关键K）实机验证：真实 8810 + headless Chrome/CDP。
 *
 * 跑法：node app/plugins/kline/tests/js/cond_targets.cdp.mjs [--shots <dir>]
 * 做的事：在 /v2/ 页面里挂 overlay（openKline）→ 画布右键 → 条件单 → 选两类，各截一张图。
 * 不提交：面板只验「表单出现 + 下拉可切换」；POST 由 p2_dom.test.mjs 的桩验，实机不落单。
 *
 * 为什么是 import overlay.js 而不是点卡片里那个 K 线按钮：
 *   线上 K 线卡片当前挂的是 legacy 弹窗（frontend.js:452，2026-10-27 主人定），
 *   新的 overlay/order_panel 那条路只能直接装配——本脚本不改任何入口文件，
 *   只把 8810 正在提供的同一份 overlay.js 装到页面上；行情/持仓/条件单仍全部打真实接口。
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
const BASE = 'http://127.0.0.1:8810';
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJson = (url) => new Promise((res, rej) => http.get(url, (r) => {
  let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
}).on('error', rej));

class CDP {
  constructor(u) { this.u = u; this.id = 0; this.p = new Map(); this.ev = []; }
  open() { return new Promise((res, rej) => {
    this.ws = new WebSocket(this.u);
    this.ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return; }
      if (m.method) this.ev.push(m);
    };
    this.ws.onopen = res; this.ws.onerror = rej;
  }); }
  send(method, params) { const id = ++this.id; return new Promise((r, j) => { this.p.set(id, { r, j }); this.ws.send(JSON.stringify({ id, method, params: params || {} })); }); }
  async eval(e) {
    const r = await this.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) { /* 已关 */ } }
}

let failed = 0, ran = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 700) : '')); }
}

async function shot(cdp, name, selector, pad) {
  const p = pad == null ? 10 : pad;
  const box = await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(selector)}); if(!el) return null;
    const r=el.getBoundingClientRect();
    return JSON.stringify({x:r.left,y:r.top,width:r.width,height:r.height});})()`);
  if (!box) throw new Error('截图目标不存在：' + selector);
  const b = JSON.parse(box);
  const r = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    clip: { x: Math.max(0, b.x - p), y: Math.max(0, b.y - p), width: Math.min(b.width + p * 2, 1600), height: Math.min(b.height + p * 2, 1200), scale: 1 },
  });
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const f = path.join(SHOT_DIR, name + '.png');
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

(async () => {
  if (!CHROME) { console.log('SKIP 没有本地 Chrome/Edge'); process.exit(0); }
  const prof = path.join(os.tmpdir(), 'klcond-' + Date.now());
  const port = 9600 + Math.floor(Math.random() * 90);
  const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + prof,
    '--window-size=1600,900', 'about:blank'], { stdio: 'ignore' });
  let t = null;
  for (let i = 0; i < 100; i++) { try { t = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); } }
  if (!t) { console.log('SKIP CDP 未就绪'); proc.kill(); process.exit(0); }
  const cdp = new CDP(t.find((x) => x.type === 'page').webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  await sleep(3000);

  try {
    // 1) 装配线上同一份 overlay（K 线画布 + 右键菜单 + 下单面板）
    const mount = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const api={ get: async (p) => { const r=await fetch('/api/p/kline'+p,{cache:'no-store'});
        if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); } };
      try {
        const m=await import('/plugins/kline/frontend/overlay.js?v=0.8.1');
        window.__klo=m.openKline({api:api, code:'600519.SH', name:'贵州茅台', period:'daily'});
      } catch(e) { return JSON.stringify({ok:false,err:String(e&&e.message||e)}); }
      for(let i=0;i<80&&!document.querySelector('.klm-canvas');i++) await w(250);
      await w(600);
      return JSON.stringify({ok:true,canvas:!!document.querySelector('.klm-canvas'),
        bars:((document.querySelector('.klm-canvas')||{}).width)||0})})()`);
    const mj = JSON.parse(mount);
    check('8810 页面里 overlay 挂载成功且画出画布', mj.ok && mj.canvas === true, mj);
    if (!mj.ok || !mj.canvas) throw new Error('overlay 未挂载：' + JSON.stringify(mj));

    // 2) 画布右键 → 条件单（真实菜单委托，不直接调 order_panel）
    const menu = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const cv=document.querySelector('.klm-canvas');
      const r=cv.getBoundingClientRect();
      cv.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,
        clientX:r.left+Math.round(r.width*0.55),clientY:r.top+Math.round(r.height*0.45)}));
      await w(250);
      const items=Array.from(document.querySelectorAll('.klm-menu [data-id]')).map(e=>e.getAttribute('data-id'));
      const cond=document.querySelector('.klm-menu [data-id="cond"]');
      if(cond) cond.dispatchEvent(new MouseEvent('click',{bubbles:true}));
      await w(500);
      return JSON.stringify({items:items,panel:!!document.getElementById('klo-panel')})})()`);
    const mj2 = JSON.parse(menu);
    check('右键菜单含「条件单」且点开面板', (mj2.items || []).indexOf('cond') >= 0 && mj2.panel === true, mj2);
    if (!mj2.panel) throw new Error('条件单面板未打开：' + JSON.stringify(mj2));

    // 3) 类型下拉：11 项、两类都在
    const types = await cdp.eval(`(()=>{const s=document.querySelector('#klo-ctype'); if(!s) return JSON.stringify({none:true});
      return JSON.stringify({n:s.options.length, opts:Array.from(s.options).map(o=>o.value),
        labels:Array.from(s.options).map(o=>o.textContent)})})()`);
    const tj0 = JSON.parse(types);
    check('类型下拉含 11 项（九类 + 两类）', tj0.n === 11, tj0.n);
    check('两类 key 在列表里', (tj0.opts || []).indexOf('ma_pullback_buy') >= 0
      && (tj0.opts || []).indexOf('key_k_break') >= 0, tj0.opts);

    // 4) 回踩均线买入
    const ma = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const s=document.querySelector('#klo-ctype'); s.value='ma_pullback_buy'; s.dispatchEvent(new Event('change',{bubbles:true}));
      await w(320);
      const tg=document.querySelector('#klo-ctarget'); const tol=document.querySelector('#klo-ctol');
      return JSON.stringify({opts:tg?Array.from(tg.options).map(o=>o.value):null,
        labels:tg?Array.from(tg.options).map(o=>o.textContent):null,
        tol:tol?tol.value:null, min:tol?tol.min:null, max:tol?tol.max:null,
        base:(document.querySelector('#klo-cbase')||{}).textContent||'',
        trig:!!document.querySelector('#klo-ctrig'),
        info:(document.querySelector('#klo-cinfo')||{}).textContent||''})})()`);
    const maj = JSON.parse(ma);
    check('回踩均线：目标位 auto/yellow/white', JSON.stringify(maj.opts) === '["auto","yellow","white"]', maj.opts);
    check('回踩均线：容差默认 0.5、限 0–5', maj.tol === '0.5' && maj.min === '0' && maj.max === '5', maj);
    check('回踩均线：顶部口径提示含黄/白线公式', /MA14/.test(maj.base) && /EMA/.test(maj.base), maj.base);
    check('回踩均线：不是触发价型（无触发价输入）', maj.trig === false, maj.trig);
    const f1 = await shot(cdp, 'cond_ma_pullback', '#klo-panel');
    console.log('shot ' + f1);

    // 5) 关键K低吸
    const kk = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const s=document.querySelector('#klo-ctype'); s.value='key_k_break'; s.dispatchEvent(new Event('change',{bubbles:true}));
      await w(320);
      const tg=document.querySelector('#klo-ctarget');
      const first=tg?tg.value:null;
      if(tg){ tg.value='lower'; tg.dispatchEvent(new Event('change',{bubbles:true})); }
      await w(200);
      const tol=document.querySelector('#klo-ctol');
      if(tol){ tol.value='2.5'; tol.dispatchEvent(new Event('input',{bubbles:true})); }
      await w(200);
      return JSON.stringify({opts:tg?Array.from(tg.options).map(o=>o.value):null,
        first:first, now:tg?tg.value:null,
        base:(document.querySelector('#klo-cbase')||{}).textContent||'',
        tol:(document.querySelector('#klo-ctol')||{}).value,
        info:(document.querySelector('#klo-cinfo')||{}).textContent||''})})()`);
    const kkj = JSON.parse(kk);
    check('关键K：目标位 auto/upper/lower', JSON.stringify(kkj.opts) === '["auto","upper","lower"]', kkj.opts);
    check('关键K：切类后目标位回落 auto，且能切到 lower', kkj.first === 'auto' && kkj.now === 'lower', kkj);
    check('关键K：顶部口径提示（放量阳线/上沿收盘）', /放量阳线/.test(kkj.base) && /上沿/.test(kkj.base), kkj.base);
    check('关键K：容差可改到 2.5%', kkj.tol === '2.5', kkj.tol);
    const f2 = await shot(cdp, 'cond_key_k', '#klo-panel');
    console.log('shot ' + f2);

    const ex = cdp.ev.filter((e) => e.method === 'Runtime.exceptionThrown');
    check('页面无 JS 异常', ex.length === 0, ex.map((e) => (e.params && e.params.exceptionDetails && e.params.exceptionDetails.text) || 'x'));

    console.log('\n条件单两类实机：' + ran + ' 项，失败 ' + failed);
  } catch (e) {
    failed++;
    console.log('NOT OK - 异常：' + ((e && e.message) || e));
  } finally {
    cdp.close();
    try { proc.kill(); } catch (e) { /* 已退 */ }
    try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) { /* 占用 */ }
    process.exit(failed ? 1 : 0);
  }
})();
