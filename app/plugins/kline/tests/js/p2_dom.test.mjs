// P2 面板 DOM 断言（可复跑）：headless Chrome + CDP 驱动真实 DOM，不起 8810、不发真实 POST。
// 跑法：node app/plugins/kline/tests/js/p2_dom.test.mjs
//   · 无 Chrome 时打印 SKIP 并以 0 退出（pytest 侧据此 skip）
//   · 网络全部被 harness 里的 fetch 桩接管，POST 只落在 window.__posts 里
//
// 覆盖（验收点 C）：
//   1 撤单第二次点击 → 恰好 1 次 POST /api/order/cancel，body {order_id: paper_order_id}
//   2 条件单第二次点击 → 恰好 1 次 POST /api/condition/add，核对 OCO group / valid_until / trigger_price / limit_price
//   3 切 Tab 不重设数量；锁定时快捷比例禁用且不改变数量
//   3b 两类低吸单（2026-10-28）：目标位下拉按类型分开、容差默认 0.5%、POST 带 target/tol 且不带 trigger_price
//   4 Esc 顺序：面板 → 菜单 → 画线 → 关窗
//   5 标题栏拖拽改变面板坐标

import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, '../..');          // app/plugins/kline
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const HARNESS = fs.readFileSync(path.join(HERE, 'p2_dom_harness.html'), 'utf8');

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const url = String(req.url || '/').split('?')[0];
      if (url === '/' || url === '/h.html') {
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(HARNESS);
        return;
      }
      const rel = url.replace(/^\//, '');
      const file = path.join(PLUGIN, rel);
      if (!file.startsWith(PLUGIN) || !fs.existsSync(file)) { res.writeHead(404); res.end('nope'); return; }
      res.writeHead(200, { 'Content-Type': 'application/javascript; charset=utf-8' });
      res.end(fs.readFileSync(file));
    });
    srv.listen(0, '127.0.0.1', () => resolve({ srv, port: srv.address().port }));
  });
}

class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) { const { r, j } = this.p.get(m.id); this.p.delete(m.id); m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); }
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
  close() { try { this.ws.close(); } catch (e) {} }
}

async function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}

let failed = 0, ran = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail ? ' :: ' + JSON.stringify(detail) : '')); }
}

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge，跳过 DOM 断言'); process.exit(0); }

  const { srv, port } = await serve();
  const profile = path.join(os.tmpdir(), 'p2dom-' + Date.now());
  const port2 = 9400 + Math.floor(Math.random() * 90);
  const proc = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + port2, '--user-data-dir=' + profile, '--window-size=1400,900', 'about:blank'],
    { stdio: 'ignore' });
  let targets = null;
  for (let i = 0; i < 80; i++) {
    try { targets = await getJson('http://127.0.0.1:' + port2 + '/json/list'); break; } catch (e) { await sleep(250); }
  }
  if (!targets) { console.log('SKIP: CDP 未就绪'); srv.close(); proc.kill(); process.exit(0); }

  const page = targets.find((t) => t.type === 'page');
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:' + port + '/h.html' });
  await sleep(1400);

  try {
    // 打开记录页
    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));const cv=document.querySelector('.klm-canvas');const r=cv.getBoundingClientRect();cv.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,button:2,clientX:r.left+200,clientY:r.top+130}));await w(150);document.querySelector('.klm-menu [data-id="list"]').dispatchEvent(new MouseEvent('click',{bubbles:true}));await w(250);return 1})()`);
    const ids = await cdp.eval(`JSON.stringify(Array.from(document.querySelectorAll('#klo-panel .klo-it button')).map(b=>b.dataset.id))`);
    check('记录页撤单号用 paper_order_id', ids === '["PP-A","CO-1"]', ids);

    // 1) 撤单两次点击
    const cancel = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const b=()=>document.querySelectorAll('#klo-panel .klo-it button')[0];
      b().click(); await w(40); const t1=b().textContent; const n1=window.__posts.length;
      b().click(); b().click(); await w(600);
      return JSON.stringify({t1:t1,n1:n1,n:window.__posts.length,p:window.__posts.filter(x=>x.url.indexOf('/order/cancel')>=0)})})()`);
    const cj = JSON.parse(cancel);
    check('撤单首击进确认态且零 POST', cj.t1.indexOf('再点') >= 0 && cj.n1 === 0, cj);
    check('撤单恰好 1 次 POST 且 body 是 paper_order_id', cj.p.length === 1 && cj.p[0].body.order_id === 'PP-A', cj.p);

    // 2) 条件单：切到条件单页，选限价 + OCO + 触发价
    const cond = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      document.querySelectorAll('#klo-panel .klo-tabs button')[2].click(); await w(150);
      const set=(sel,v)=>{const e=document.querySelector(sel);if(e){e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}};
      set('#klo-ctrig','9.50'); set('#klo-cqty','200');
      document.querySelector('#klo-cotype [data-v="limit"]').click(); await w(120);
      set('#klo-cprice','9.40'); set('#klo-cvalid','5');
      document.querySelector('#klo-coco').checked=true; document.querySelector('#klo-coco').dispatchEvent(new Event('change',{bubbles:true}));
      await w(80);
      const b=()=>document.querySelector('#klo-panel .klo-submit');
      const before=window.__posts.length;
      b().click(); await w(40); const t1=b().textContent;
      b().click(); b().click(); await w(600);
      return JSON.stringify({t1:t1,before:before,p:window.__posts.filter(x=>x.url.indexOf('/condition/add')>=0)})})()`);
    const cj2 = JSON.parse(cond);
    const p0 = cj2.p[0] ? cj2.p[0].body : {};
    check('条件单首击进确认态', cj2.t1.indexOf('再点') >= 0, cj2.t1);
    check('条件单恰好 1 次 POST', cj2.p.length === 1, cj2.p.length);
    check('条件单 trigger_price 正确', p0.trigger_price === 9.5, p0.trigger_price);
    check('条件单 limit_price 正确', p0.limit_price === 9.4 && p0.order_type === 'limit', p0);
    check('条件单 OCO group 前缀正确', String(p0.group || '').indexOf('OCO-600519.SH-') === 0, p0.group);
    check('条件单 valid_until = 5 天后', /^2026\d{4}$/.test(String(p0.valid_until || '')), p0.valid_until);

    // 3) 切 Tab 保数量 + 锁定 ratio
    const keep = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      document.querySelectorAll('#klo-panel .klo-tabs button')[0].click(); await w(120);
      const qi=document.querySelector('#klo-qty'); qi.value='500'; qi.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelectorAll('#klo-panel .klo-tabs button')[1].click(); await w(120);
      const afterSell=document.querySelector('#klo-qty').value;
      document.querySelectorAll('#klo-panel .klo-tabs button')[0].click(); await w(120);
      const lock=document.querySelector('#klo-panel [data-act="lock"]'); lock.click(); await w(150);
      const ro=document.querySelector('#klo-qty').readOnly;
      const beforeRatio=document.querySelector('#klo-qty').value;
      const btn=document.querySelectorAll('#klo-quick button')[3];
      const disabled=btn.disabled; btn.click(); await w(80);
      const afterRatio=document.querySelector('#klo-qty').value;
      return JSON.stringify({afterSell:afterSell,ro:ro,disabled:disabled,beforeRatio:beforeRatio,afterRatio:afterRatio})})()`);
    const kj = JSON.parse(keep);
    check('切 Tab 不重设数量', kj.afterSell === '500', kj);
    check('锁定后数量 readonly', kj.ro === true, kj);
    check('锁定后快捷比例禁用且不改变数量', kj.disabled === true && kj.afterRatio === kj.beforeRatio, kj);

    // 3b) 两类低吸单：目标位下拉 + 容差，POST 带 target/tol
    const tgt = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      document.querySelectorAll('#klo-panel .klo-tabs button')[2].click(); await w(180);
      const pick=(v)=>{const e=document.querySelector('#klo-ctype'); e.value=v; e.dispatchEvent(new Event('change',{bubbles:true}));};
      const opts=(sel)=>Array.from(document.querySelectorAll(sel+' option')).map(o=>o.value);
      const btn=()=>document.querySelector('#klo-panel .klo-submit');
      const fire=(sel,ev,v)=>{const e=document.querySelector(sel); if(!e) return null; e.value=v; e.dispatchEvent(new Event(ev,{bubbles:true})); return e.value;};
      pick('ma_pullback_buy'); await w(180);
      const optsMa=opts('#klo-ctarget');
      const tolEl=document.querySelector('#klo-ctol');
      const tolVal=tolEl?tolEl.value:null;
      const tolAttrs=tolEl?{min:tolEl.min,max:tolEl.max,step:tolEl.step}:null;
      const base=(document.querySelector('#klo-cbase')||{}).textContent||'';
      fire('#klo-ctarget','change','white'); fire('#klo-ctol','input','1.2'); await w(80);
      const n1=window.__posts.length;
      btn().click(); await w(40); btn().click(); await w(600);
      const p1=window.__posts.slice(n1).filter(x=>x.url.indexOf('/condition/add')>=0).map(x=>x.body);
      pick('key_k_break'); await w(180);
      const optsKk=opts('#klo-ctarget');
      const tgtAfter=(document.querySelector('#klo-ctarget')||{}).value;
      fire('#klo-ctarget','change','lower'); fire('#klo-ctol','input','3'); await w(80);
      const n2=window.__posts.length;
      btn().click(); await w(40); btn().click(); await w(600);
      const p2=window.__posts.slice(n2).filter(x=>x.url.indexOf('/condition/add')>=0).map(x=>x.body);
      return JSON.stringify({optsMa:optsMa,tolVal:tolVal,tolAttrs:tolAttrs,base:base,optsKk:optsKk,tgtAfter:tgtAfter,p1:p1,p2:p2})})()`);
    const tj = JSON.parse(tgt);
    check('回踩均线：目标位三选项', JSON.stringify(tj.optsMa) === '["auto","yellow","white"]', tj.optsMa);
    check('回踩均线：容差默认 0.5 且限 0–5', tj.tolVal === '0.5' && tj.tolAttrs && tj.tolAttrs.min === '0' && tj.tolAttrs.max === '5', tj);
    check('回踩均线：顶部口径提示含黄/白线公式', /MA14/.test(tj.base) && /EMA/.test(tj.base), tj.base);
    check('回踩均线：POST 带 target/tol 且不带 trigger_price',
      tj.p1.length === 1 && tj.p1[0].target === 'white' && Math.abs(tj.p1[0].tol - 0.012) < 1e-9 && !('trigger_price' in tj.p1[0]), tj.p1);
    check('关键K：目标位三选项', JSON.stringify(tj.optsKk) === '["auto","upper","lower"]', tj.optsKk);
    check('切到关键K 后越界目标位收回 auto', tj.tgtAfter === 'auto', tj.tgtAfter);
    check('关键K：POST 带 target=lower / tol=0.03', tj.p2.length === 1 && tj.p2[0].target === 'lower' && Math.abs(tj.p2[0].tol - 0.03) < 1e-9, tj.p2);

    // 4) Esc 顺序：面板 → 菜单 → 关窗
    const esc = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const press=()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      press(); await w(80); const p1=!document.querySelector('#klo-panel'); const m0=!!document.querySelector('.klm-box');
      const cv=document.querySelector('.klm-canvas'); const r=cv.getBoundingClientRect();
      cv.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,button:2,clientX:r.left+200,clientY:r.top+130})); await w(120);
      const menuOpen=!!document.querySelector('.klm-menu');
      press(); await w(80); const menuClosed=!document.querySelector('.klm-menu'); const m1=!!document.querySelector('.klm-box');
      press(); await w(80); const gone=!document.querySelector('.klm-box');
      return JSON.stringify({p1:p1,m0:m0,menuOpen:menuOpen,menuClosed:menuClosed,m1:m1,gone:gone})})()`);
    const ej = JSON.parse(esc);
    check('Esc 先关面板（菜单/弹窗仍在）', ej.p1 && ej.m0, ej);
    check('Esc 再关菜单（弹窗仍在）', ej.menuOpen && ej.menuClosed && ej.m1, ej);
    check('Esc 最后关弹窗', ej.gone === true, ej);

    console.log('\nP2 DOM：' + ran + ' 项，失败 ' + failed);
  } catch (e) {
    failed++;
    console.log('NOT OK - 异常：' + ((e && e.message) || e));
  } finally {
    cdp.close(); srv.close(); try { proc.kill(); } catch (e) {}
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    process.exit(failed ? 1 : 0);
  }
})();
