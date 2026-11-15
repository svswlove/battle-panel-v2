// 旧版 K 线弹窗「原样挂载」真实浏览器自测（Chrome CDP，直连 8810）
//
// 跑法：node app/plugins/kline/tests/js/legacy_mount.cdp.mjs [--shots <dir>]
//   · 不起服务、不发任何 POST/PUT/DELETE：只 GET 8810 与它的只读接口；
//   · 下单面板只开到「面板可见 / 首击前」，不点提交；
//   · 无 Chrome 时打印 SKIP 并以 0 退出。
//
// 断言：旧 DOM 全量关键 ID、旧四模块与四库装载顺序、初始化幂等、真实 LC/HQChart 渲染、
//       周期切换 d/t/m5、指标/工具菜单、右键下单面板、console 异常 0、两档窗口几何不重叠。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOT_DIR = (() => {
  const i = process.argv.indexOf('--shots');
  return i > 0 ? process.argv[i + 1] : path.join(HERE, '_shots');
})();
const BASE = 'http://127.0.0.1:8810';
const V = '0.7.2';
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => { let b = ''; r.on('data', (c) => b += c); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
  });
}

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
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) {} }
}

let failed = 0, ran = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 600) : '')); }
}

const IDS = ['klineModal', 'klineTitle', 'kPerGroup', 'kMinPill', 'kMinPanel', 'kTplStack', 'kIndPanel',
  'kToolToggle', 'kToolPanel', 'kToolChips', 'kBgToggle', 'kBgPanel', 'btnWatch', 'btnRefreshK',
  'klineStatus', 'klineChart', 'hq_minute', 'kTopLeft', 'kTip', 'kchipCanvas', 'kauctionCanvas',
  'kRangeBox', 'kRangeInfo', 'kQuotePanel', 'kF10Panel'];

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  // 8810 在跑吗
  try { await getJson(BASE + '/api/p/kline/health'); } catch (e) { /* health 可能 404，忽略 */ }

  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = path.join(os.tmpdir(), 'kllegacy-' + Date.now());
  const port = 9500 + Math.floor(Math.random() * 90);
  const proc = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1920,1080', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let i = 0; i < 100; i++) {
    try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); }
  }
  if (!targets) { console.log('SKIP: CDP 未就绪'); proc.kill(); process.exit(0); }
  const page = targets.find((t) => t.type === 'page');
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Network.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });

  const shot = async (name) => {
    const r = await cdp.send('Page.captureScreenshot', { format: 'png' });
    const f = path.join(SHOT_DIR, name);
    fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
    return f;
  };

  await cdp.send('Page.navigate', { url: BASE + '/v2/' });
  await sleep(2500);

  try {
    // ---------------------------------------------------------------- 1 打开（走真实入口）
    const openRes = await cdp.eval(`(async()=>{
      const w=ms=>new Promise(r=>setTimeout(r,ms));
      const mod = await import('/plugins/kline/frontend.js?v=${V}');
      window.__mod = mod;
      await mod.openKlineStandalone({ api: null, code: '600036.SH', name: '招商银行' });
      for (let i=0;i<60;i++){ await w(250); const el=document.getElementById('klineModal');
        if (el && el.style.display==='flex' && document.querySelectorAll('#klineChart canvas').length>0) break; }
      await w(1200);
      return 1; })()`);
    check('入口 openKlineStandalone 可调且返回', openRes === 1, openRes);

    const ids = await cdp.eval(`JSON.stringify(${JSON.stringify(IDS)}.filter(i=>!document.getElementById(i)))`);
    check('旧 DOM 全部关键 ID 都在', ids === '[]', JSON.parse(ids));

    const order = await cdp.eval(`JSON.stringify(Array.from(document.querySelectorAll('script[data-kline-legacy]')).map(s=>s.getAttribute('data-kline-legacy')))`);
    check('旧四库+四模块按旧序装载',
      order === JSON.stringify(['lc', 'lc-drawing', 'jquery', 'hqchart', 'kline_lc', 'kline_quote5', 'kline_f10', 'kline_order']),
      JSON.parse(order));

    const lc = await cdp.eval(`JSON.stringify({
      shown: (document.getElementById('klineModal')||{}).style.display,
      lcCanvas: document.querySelectorAll('#klineChart canvas').length,
      panes: document.querySelectorAll('#klineChart > div').length,
      bars: (window.KLineApp.__lc && window.KLineApp.__lc().bars) ? window.KLineApp.__lc().bars().length : -1,
      period: window.KLineApp.__lc().period(),
      title: document.getElementById('klineTitle').textContent,
      status: document.getElementById('klineStatus').textContent,
    })`);
    const L = JSON.parse(lc);
    check('弹窗已打开且标题带代码', L.shown === 'flex' && L.title.indexOf('600036.SH') >= 0, L);
    check('LC 主图真实渲染（canvas + 多 pane）', L.lcCanvas >= 2 && L.panes >= 2 && L.bars > 50, L);
    check('日线档 = d', L.period === 'd', L);

    // 原工具/指标控件可见
    const ctrl = await cdp.eval(`JSON.stringify({
      periodBtns: document.querySelectorAll('#klineModal .khead button[data-period]').length,
      tool: !!document.getElementById('kToolToggle'),
      tpl: !!document.getElementById('kTplStack'),
      watch: !!document.getElementById('btnWatch'),
      quote: getComputedStyle(document.getElementById('kQuotePanel')).display,
      f10: getComputedStyle(document.getElementById('kF10Panel')).display,
      kboxW: Math.round(document.getElementById('klineModal').querySelector('.kbox').getBoundingClientRect().width),
      kboxH: Math.round(document.getElementById('klineModal').querySelector('.kbox').getBoundingClientRect().height),
    })`);
    const C = JSON.parse(ctrl);
    check('原周期/工具/指标/自选控件齐全', C.periodBtns === 3 && C.tool && C.tpl && C.watch, C);
    check('盘口卡与 F10 卡由旧模块接管（display 已被改）', C.quote !== '' && C.f10 !== '', C);
    check('弹窗尺寸 88vh/≤1100px 生效', C.kboxH > 700 && C.kboxW <= 1100 && C.kboxW > 800, C);

    await shot('01_open_d_1920.png');

    // ---------------------------------------------------------------- 2 切周期 t / m5
    const t = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      window.KLineApp.setPeriod('t');
      for(let i=0;i<40;i++){ await w(250); const a=window.KLineApp.hqAlive(); if(a.hasChart&&a.canvasCount>0) break; }
      await w(800);
      const a=window.KLineApp.hqAlive();
      return JSON.stringify({period: window.KLineApp.__lc().period(), hq:a, quoteShown: getComputedStyle(document.getElementById('kQuotePanel')).display}); })()`);
    const T = JSON.parse(t);
    check('分时 t 走 HQChart（实例+canvas 都在）', T.period === 't' && T.hq.hasChart === true && T.hq.canvasCount > 0, T);
    check('分时档盘口卡显示', T.quoteShown !== 'none', T);
    await shot('02_open_t_1920.png');

    const m5 = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      window.KLineApp.setPeriod('m5');
      for(let i=0;i<40;i++){ await w(250); if(window.KLineApp.__lc().bars().length>0) break; }
      await w(600);
      return JSON.stringify({period: window.KLineApp.__lc().period(), bars: window.KLineApp.__lc().bars().length,
        lcShown: getComputedStyle(document.getElementById('klineChart')).display}); })()`);
    const M = JSON.parse(m5);
    check('m5 档回到 LC 且有数据', M.period === 'm5' && M.bars > 50, M);
    await shot('03_open_m5_1920.png');

    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));window.KLineApp.setPeriod('d');await w(1500);return 1})()`);

    // ---------------------------------------------------------------- 3 指标 / 工具菜单
    const menus = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      // 旧版把右键绑在 #kTplStack 的两个按钮上（bindCtx(b, …)），不是外层 span
      const tpl=document.querySelector('#kTplStack > button[data-tpl="0"]');
      tpl.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,button:2}));
      await w(300);
      const indOpen=document.getElementById('kIndPanel').classList.contains('open');
      const indRows=document.querySelectorAll('#kIndPanel input, #kIndPanel button').length;
      document.getElementById('kToolToggle').click(); await w(300);
      const toolOpen=document.getElementById('kToolPanel').classList.contains('open');
      const drawBtns=document.querySelectorAll('#kToolPanel .k-draw').length;
      return JSON.stringify({indOpen, indRows, toolOpen, drawBtns}); })()`);
    const MN = JSON.parse(menus);
    check('指标设置面板可开且有真控件', MN.indOpen === true && MN.indRows > 0, MN);
    check('工具面板可开且含原画线按钮', MN.toolOpen === true && MN.drawBtns >= 8, MN);
    await shot('04_menus_1920.png');
    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      document.body.click(); await w(200);
      ['kIndPanel','kToolPanel'].forEach(id=>document.getElementById(id).classList.remove('open')); return 1})()`);

    // ---------------------------------------------------------------- 4 右键下单面板（只到首击前）
    // 旧版口径（kline_order.js:1317-1324）：**日线/分钟图右键不弹菜单**（主人 2026-10-21 定，防误触）；
    //   右键下单入口只在**分时档**（#hq_minute，走 chartContext），蜡烛图的条件单走「拖成本线」。
    //   所以这里先切到 t 再右键——按旧行为验，不自己造入口。
    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      window.KLineApp.setPeriod('t');
      for(let i=0;i<40;i++){ await w(250); if(window.KLineApp.hqAlive().hasChart) break; }
      await w(900); return 1})()`);
    const ko = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const hq=document.getElementById('hq_minute');
      const cv=hq.querySelector('canvas') || hq;
      const r=cv.getBoundingClientRect();
      const x=r.left+r.width*0.6, y=r.top+r.height*0.45;
      cv.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:x,clientY:y}));
      await w(700);
      const m=document.getElementById('koMenu');
      const items=m?Array.from(m.querySelectorAll('.ko-mi')).map(e=>e.getAttribute('data-act')):[];
      return JSON.stringify({menu:!!m && !m.hidden, items:items}); })()`);
    const KO = JSON.parse(ko);
    check('右键出旧下单菜单（原菜单项）', KO.menu === true && KO.items.length >= 3, KO);

    const koPanel = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const b=document.querySelector('#koMenu .ko-mi[data-act="buy"]');
      if(!b) return JSON.stringify({ok:false,why:'no-buy-item'});
      b.click(); await w(600);
      const p=document.getElementById('koPanel');
      return JSON.stringify({ok:!!p && !p.hidden, tabs: p?Array.from(p.querySelectorAll('.ko-tab')).map(e=>e.textContent.trim()):[],
        submit: p?!!p.querySelector('.ko-submit'):false, qty: p&&p.querySelector('.ko-inp input')?p.querySelector('.ko-inp input').value:null}); })()`);
    const KP = JSON.parse(koPanel);
    check('下单面板可开（买入 Tab / 数量 / 提交按钮在位，未点提交）',
      KP.ok === true && KP.submit === true && Array.isArray(KP.tabs) && KP.tabs.length >= 3, KP);
    await shot('05_order_panel_1920.png');

    // ---------------------------------------------------------------- 5 幂等：关/开/换票
    const idem = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      const before={modals:document.querySelectorAll('#klineModal').length,
        css:document.querySelectorAll('link#kline-legacy-css').length,
        scripts:document.querySelectorAll('script[data-kline-legacy]').length};
      window.__mod.closeKline(); await w(300);
      await window.__mod.openKlineStandalone({api:null,code:'600519.SH',name:'贵州茅台'}); await w(1500);
      window.__mod.closeKline(); await w(200);
      await window.__mod.openKlineStandalone({api:null,code:'600036.SH',name:'招商银行'}); await w(1500);
      const after={modals:document.querySelectorAll('#klineModal').length,
        css:document.querySelectorAll('link#kline-legacy-css').length,
        scripts:document.querySelectorAll('script[data-kline-legacy]').length};
      return JSON.stringify({before, after, title:document.getElementById('klineTitle').textContent,
        bars:window.KLineApp.__lc().bars().length}); })()`);
    const ID = JSON.parse(idem);
    check('重复开关/换票不重复插 DOM/CSS/脚本',
      ID.before.modals === 1 && ID.after.modals === 1 && ID.before.css === 1 && ID.after.css === 1
      && ID.before.scripts === 8 && ID.after.scripts === 8, ID);
    check('换票后标题与数据跟着换', ID.title.indexOf('600036.SH') >= 0 && ID.bars > 50, ID);

    // ---------------------------------------------------------------- 5.5 点卡片外空白关闭（V2 通用口径）
    // 旧 kline_order.js:1383 明确取消了「点卡片外关闭」（怕拖拽误退），原样挂载把这行为也带回来了；
    // 主人 2026-10-27 要求补回 V2 口径（paper/frontend.js:845、sector/detail.js:326 同式）。
    // 这里用真实 CDP 鼠标事件（Input.dispatchMouseEvent，trusted），不 dispatch 假 target；
    // 每个落点先用 elementFromPoint 探明到底落在哪个元素上。
    const clickAt = async (pt) => {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pt.x, y: pt.y, button: 'none' });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pt.x, y: pt.y, button: 'left', buttons: 1, clickCount: 1 });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pt.x, y: pt.y, button: 'left', buttons: 0, clickCount: 1 });
      await sleep(320);
    };
    const shownNow = () => cdp.eval(`(document.getElementById('klineModal')||{}).style.display`);
    const reopen = async (code, name) => cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      await window.__mod.openKlineStandalone({api:null,code:'${code}',name:'${name}'});
      for(let i=0;i<40;i++){await w(200); const el=document.getElementById('klineModal');
        if(el&&el.style.display==='flex'&&document.querySelectorAll('#klineChart canvas').length>0) break;}
      await w(700); return (document.getElementById('klineModal')||{}).style.display;})()`);
    // 在 selector 框内扫格找「落在它（或其后代）上、且不是控件」的点；allowCanvas 用于图表本身
    const safePoint = (sel, allowCanvas) => cdp.eval(`JSON.stringify((()=>{
      const el=document.querySelector(${JSON.stringify(sel)}); if(!el) return {ok:false,why:'no-el'};
      const b=el.getBoundingClientRect(); if(!b.width||!b.height) return {ok:false,why:'zero-rect',display:getComputedStyle(el).display};
      const bad=${allowCanvas ? '/^(BUTTON|INPUT|SELECT|TEXTAREA|A)$/' : '/^(BUTTON|INPUT|SELECT|TEXTAREA|A|CANVAS)$/'};
      for(let fy=0.08; fy<=0.92; fy+=0.12){ for(let fx=0.08; fx<=0.92; fx+=0.12){
        const x=b.left+b.width*fx, y=b.top+b.height*fy, e=document.elementFromPoint(x,y);
        if(!e||!el.contains(e)||bad.test(e.tagName)) continue;
        return {ok:true,x:Math.round(x),y:Math.round(y),tag:e.tagName,id:e.id||'',cls:String(e.className||'').slice(0,40)}; }}
      return {ok:false,why:'no-safe-point'};})())`).then(JSON.parse);
    // 遮罩空白点：四角/边缘里第一个 elementFromPoint === #klineModal 的
    const maskPoint = () => cdp.eval(`JSON.stringify((()=>{
      const m=document.getElementById('klineModal'); const W=innerWidth,H=innerHeight;
      const cands=[[6,6],[W-6,6],[6,H-6],[W-6,H-6],[W/2,6],[6,H/2],[W-6,H/2],[W/2,H-6]];
      for(const [x,y] of cands){ const e=document.elementFromPoint(x,y);
        if(e===m) return {ok:true,x:Math.round(x),y:Math.round(y),tag:e.tagName,id:e.id}; }
      return {ok:false,why:'找不到真正落在遮罩上的点'};})())`).then(JSON.parse);

    // --- 日线档：#kF10Panel 显示、#kQuotePanel 隐藏
    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      window.KLineApp.setPeriod('d'); await w(1400); return 1})()`);
    const blank = await maskPoint();
    check('探到真正落在遮罩空白上的点（elementFromPoint === #klineModal）', blank.ok === true, blank);

    const boxPt = await safePoint('#klineModal .khead');
    const chartPt = await safePoint('#klineChart', true);
    const f10Pt = await safePoint('#kF10Panel');
    check('探到 .kbox 内（头部工具条）/图表/#kF10Panel 的可点落点',
      boxPt.ok && chartPt.ok && f10Pt.ok, { boxPt, chartPt, f10Pt });
    for (const [label, pt] of [['.kbox 内头部', boxPt], ['#klineChart', chartPt], ['#kF10Panel', f10Pt]]) {
      if (!pt.ok) { check('点 ' + label + ' 不关', false, pt); continue; }
      await clickAt(pt);
      check('点 ' + label + ' 不关', (await shownNow()) === 'flex', { pt, shown: await shownNow() });
    }

    // --- 拖拽：从图表按下、在遮罩上松开，不算「点空白」（浏览器把 click 派给共同祖先=遮罩）
    //     旧版取消外点关闭就是为了这个场景，这里守住它不被新监听误伤。
    if (chartPt.ok && blank.ok) {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: chartPt.x, y: chartPt.y, button: 'none' });
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: chartPt.x, y: chartPt.y, button: 'left', buttons: 1, clickCount: 1 });
      for (let k = 1; k <= 6; k++) {
        await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1,
          x: Math.round(chartPt.x + (blank.x - chartPt.x) * k / 6),
          y: Math.round(chartPt.y + (blank.y - chartPt.y) * k / 6) });
      }
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: blank.x, y: blank.y, button: 'left', buttons: 0, clickCount: 1 });
      await sleep(450);
      check('从图表拖到遮罩松开（非点空白）不关', (await shownNow()) === 'flex', { from: chartPt, to: blank, shown: await shownNow() });
    } else {
      check('从图表拖到遮罩松开（非点空白）不关', false, { chartPt, blank });
    }

    // --- 分时档：#kQuotePanel 显示
    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      window.KLineApp.setPeriod('t');
      for(let i=0;i<40;i++){ await w(250); if(window.KLineApp.hqAlive().hasChart) break; }
      await w(900); return 1})()`);
    const quotePt = await safePoint('#kQuotePanel');
    check('探到 #kQuotePanel（分时档）的可点落点', quotePt.ok === true, quotePt);
    if (quotePt.ok) {
      await clickAt(quotePt);
      check('点 #kQuotePanel 不关', (await shownNow()) === 'flex', { pt: quotePt, shown: await shownNow() });
    }

    // --- body 上的下单卡（koPanel 挂在 #klineModal 之外）：点它也不许关
    const koPt = await cdp.eval(`JSON.stringify((()=>{
      let p=document.getElementById('koPanel');
      if(!p || p.hidden){ try { window.KOrder.open('buy'); } catch(e){} p=document.getElementById('koPanel'); }
      if(!p || p.hidden) return {ok:false,why:'koPanel 未打开'};
      if(p.closest('#klineModal')) return {ok:false,why:'koPanel 竟在 #klineModal 内'};
      const b=p.getBoundingClientRect(), bad=/^(BUTTON|INPUT|SELECT|TEXTAREA|A|CANVAS)$/;
      for(let fy=0.1; fy<=0.9; fy+=0.16){ for(let fx=0.1; fx<=0.9; fx+=0.16){
        const x=b.left+b.width*fx, y=b.top+b.height*fy, e=document.elementFromPoint(x,y);
        if(!e||!p.contains(e)||bad.test(e.tagName)) continue;
        return {ok:true,x:Math.round(x),y:Math.round(y),tag:e.tagName,id:e.id||'',cls:String(e.className||'').slice(0,40)}; }}
      return {ok:false,why:'koPanel 内没有可点非控件落点'};})())`).then(JSON.parse);
    check('下单卡 koPanel 在 #klineModal 之外且可点', koPt.ok === true, koPt);
    if (koPt.ok) {
      await clickAt(koPt);
      check('点 body 上的 koPanel 不关', (await shownNow()) === 'flex', { pt: koPt, shown: await shownNow() });
      await cdp.eval(`(async()=>{try{window.KOrder.close();}catch(e){} return 1})()`);
      await sleep(200);
    }

    // --- 真点空白：必须关
    await shot('07_before_mask_click_1920.png');
    await clickAt(blank);
    check('点遮罩空白（左键）关闭弹窗', (await shownNow()) === 'none', { pt: blank, shown: await shownNow() });
    await shot('08_after_mask_click_1920.png');

    // --- Esc 原行为不破坏（旧 kline_lc.js 自己监听 document keydown）
    await reopen('600036.SH', '招商银行');
    check('关闭后可重开', (await shownNow()) === 'flex');
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
    await sleep(300);
    check('Esc 仍能关闭（旧行为未破坏）', (await shownNow()) === 'none', await shownNow());

    // --- 重开不重复监听：一次空白点击只能触发一次 close
    const dup = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      await window.__mod.openKlineStandalone({api:null,code:'600036.SH',name:'招商银行'});
      for(let i=0;i<40;i++){await w(200); const el=document.getElementById('klineModal');
        if(el&&el.style.display==='flex'&&document.querySelectorAll('#klineChart canvas').length>0) break;}
      await w(700);
      window.__closeCalls=0;
      const orig=window.KLineApp.close;
      window.KLineApp.close=function(){ window.__closeCalls++; return orig.apply(this, arguments); };
      window.__restoreClose=function(){ window.KLineApp.close=orig; };
      return (document.getElementById('klineModal')||{}).style.display;})()`);
    check('重复监听探针：弹窗已重开', dup === 'flex', dup);
    const blank2 = await maskPoint();
    await clickAt(blank2.ok ? blank2 : blank);
    const calls = await cdp.eval(`(function(){const n=window.__closeCalls||0; if(window.__restoreClose) window.__restoreClose(); return n;})()`);
    check('重开后一次空白点击只触发一次 close（不重复绑定）', calls === 1, { closeCalls: calls, shown: await shownNow() });

    // 恢复现场：重开并切回日线，供后续几何用例使用
    await reopen('600036.SH', '招商银行');
    await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms)); window.KLineApp.setPeriod('d'); await w(1200); return 1})()`);

    // ---------------------------------------------------------------- 6 两档窗口几何
    for (const [w, h, tag] of [[1920, 1080, '1920'], [1440, 900, '1440']]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
        window.dispatchEvent(new Event('resize'));
        if (window.KLineApp && window.KLineApp.__lc && window.KLineApp.__lc().chart) { try{ window.KLineApp.__lc().chart().applyOptions({width:0}); }catch(e){} }
        window.KLineApp.setPeriod('t'); await w(1800); return 1})()`);
      await sleep(1200);
      const g = await cdp.eval(`JSON.stringify((()=>{
        const r=el=>{const b=el.getBoundingClientRect();return {l:Math.round(b.left),r:Math.round(b.right),t:Math.round(b.top),b:Math.round(b.bottom),w:Math.round(b.width),h:Math.round(b.height)};};
        const box=r(document.querySelector('#klineModal .kbox'));
        const qp=r(document.getElementById('kQuotePanel'));
        const fp=r(document.getElementById('kF10Panel'));
        const qShown=getComputedStyle(document.getElementById('kQuotePanel')).display!=='none';
        const fShown=getComputedStyle(document.getElementById('kF10Panel')).display!=='none';
        const overlap=(a,b)=>Math.max(0,Math.min(a.r,b.r)-Math.max(a.l,b.l))*Math.max(0,Math.min(a.b,b.b)-Math.max(a.t,b.t));
        return {vw:innerWidth,vh:innerHeight,box,qp,fp,qShown,fShown,
          boxVsQp:qShown?overlap(box,qp):0, boxVsFp:fShown?overlap(box,fp):0,
          qpVsFp:(qShown&&fShown)?overlap(qp,fp):0,
          hq:window.KLineApp.hqAlive(),
          kheadH:Math.round(document.querySelector('#klineModal .khead').getBoundingClientRect().height)};})())`);
      const G = JSON.parse(g);
      check(tag + '：三栏不重叠', G.boxVsQp === 0 && G.boxVsFp === 0 && G.qpVsFp === 0, G);
      check(tag + '：弹窗不越视口、高度≈88vh',
        G.box.l >= 0 && G.box.r <= G.vw + 1 && Math.abs(G.box.h - Math.round(G.vh * 0.88)) <= 3, G.box);
      check(tag + '：HQChart 分时仍在渲染', G.hq.hasChart === true && G.hq.canvasCount > 0, G.hq);
      check(tag + '：旧头部工具条单行可见', G.kheadH > 20 && G.kheadH < 60, G.kheadH);
      await shot('06_t_' + tag + '.png');
    }

    // ---------------------------------------------------------------- 7 对照 /legacy/（同代码）
    const cmp = await cdp.eval(`(async()=>{const w=ms=>new Promise(r=>setTimeout(r,ms));
      window.__mod.closeKline(); await w(200);
      const f=document.createElement('iframe'); f.id='__cmp'; f.style.cssText='position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;border:0';
      f.src='/legacy/'; document.body.appendChild(f);
      await w(4000);
      const d=f.contentDocument;
      return JSON.stringify({ok:!!d, hasModal:!!(d&&d.getElementById('klineModal')),
        hasLC:!!(d&&d.querySelector('#klineChart canvas')),
        kboxW: d&&d.querySelector('#klineModal .kbox')?Math.round(d.querySelector('#klineModal .kbox').getBoundingClientRect().width):null}); })()`);
    const CMP = JSON.parse(cmp);
    check('对照 /legacy/ 同代码页面可读（旧弹窗+LC 画布都在）', CMP.hasModal === true, CMP);

    // ---------------------------------------------------------------- 8 console / 网络
    const ex = cdp.events.filter((e) => e.method === 'Runtime.exceptionThrown');
    const logs = cdp.events.filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error');
    const writes = cdp.events.filter((e) => e.method === 'Network.requestWillBeSent'
      && !['GET', 'HEAD', 'OPTIONS'].includes(e.params.request.method));
    const all4xx = cdp.events.filter((e) => e.method === 'Network.responseReceived'
      && e.params.response.status >= 400 && e.params.response.url.indexOf('/favicon') < 0);
    check('console 异常 0', ex.length === 0, ex.map((e) => e.params.exceptionDetails.text).slice(0, 3));
    // console error 里剔掉 /favicon.ico 404（壳 /v2/ 页面本身没带 icon，与本插件无关）
    const logsReal = logs.filter((e) => String(e.params.entry.url || '').indexOf('/favicon') < 0);
    check('console error 级日志 0（已剔除 favicon）', logsReal.length === 0, logsReal.map((e) => e.params.entry.text).slice(0, 3));
    // 写请求：旧 kline_lc.js 自己的「指标设置持久化」PUT 不属于订单链，单列，不当成违规吞掉
    const orderWrites = writes.filter((e) => /\/(order|condition|kill|sign|trade|paper)\b/i.test(e.params.request.url));
    const settingWrites = writes.filter((e) => /kline-indicator-settings/.test(e.params.request.url));
    const otherWrites = writes.filter((e) => !orderWrites.includes(e) && !settingWrites.includes(e));
    check('订单/条件单/交易写请求 0（未点提交）', orderWrites.length === 0,
      orderWrites.map((e) => e.params.request.method + ' ' + e.params.request.url).slice(0, 5));
    check('无其它写请求', otherWrites.length === 0,
      otherWrites.map((e) => e.params.request.method + ' ' + e.params.request.url).slice(0, 5));
    check('4xx/5xx 资源 0', all4xx.length === 0,
      all4xx.map((e) => e.params.response.status + ' ' + e.params.response.url).slice(0, 5));
    console.log('   注意：旧 kline_lc.js 自身发起的设置持久化 PUT ' + settingWrites.length + ' 次 → ' +
      (settingWrites[0] ? settingWrites[0].params.request.url : '—'));

    console.log('\n截图目录: ' + SHOT_DIR);
  } catch (err) {
    failed++;
    console.log('NOT OK - 用例异常终止 :: ' + (err && err.message ? err.message : err));
  } finally {
    console.log('\n' + (ran - failed) + '/' + ran + ' 通过');
    cdp.close();
    proc.kill();
    await sleep(300);
    process.exit(failed ? 1 : 0);
  }
})();
