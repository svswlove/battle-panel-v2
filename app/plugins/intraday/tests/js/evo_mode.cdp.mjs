/**
 * 盘中卡 · 模式隔离 + 六指数叠加（真实浏览器，CDP → 8810）
 *
 * 跑法：node app/plugins/intraday/tests/js/evo_mode.cdp.mjs [--shots <dir>]
 *
 * 为什么要在浏览器里再验一遍：纯函数测试能证明 HTML 里有没有 `<path>`，证明不了
 * 「点了按钮屏幕上真的换了线」——那要真实 DOM + 真实事件委托 + 真实 SVG 渲染。
 *
 * 数据：`tests/fixtures/track_filled_20261027.json`（真实生产轨迹 + 后端 `fill_idx_chg` 补出来的值，
 * 形状与重启后 `/api/sector_track.json` 一致；用 `tests/gen_track_filled.py` 可重生成）。**不重启 8810**（重启归 01），
 * 故用 `Page.addScriptToEvaluateOnNewDocument` 只对这一页劫持 `/api/sector_track.json`，
 * 其余请求全部照旧打真接口（`amv_live.json` 仍是 2026-10-23 的真实陈旧快照——
 * 正好用来验「历史预测」角标）。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../../..');       // projects/panel-v2（js/ → tests/ → intraday/ → plugins/ → app/ → panel-v2）
const SHOT_DIR = (() => {
  const i = process.argv.indexOf('--shots');
  return i > 0 ? process.argv[i + 1] : path.join(PANEL, '_vis', 'evo_mode');
})();
const FILLED = path.join(HERE, '..', 'fixtures', 'track_filled_20261027.json');
const BASE = 'http://127.0.0.1:8810';
const CARD_URL = BASE + '/v2/';
// 注：`?card=intraday::intraday.main` 当前无效（shell.js boot() 取 navItems(allCards)[0] 开卡，
// 不读被 ?card= 过滤后的 state.cards），所以这里走真实路径：进 /v2/ 后点侧栏「盘中」。
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
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
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
    return r.result ? r.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (e) { /* 已关 */ } }
}

let failed = 0; let ran = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) console.log('ok ' + ran + ' - ' + name);
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 700) : '')); }
}

async function shot(cdp, name, selector) {
  // 盘中卡很长（冰点/实时行情/…），不滚动就只能拍到页面顶部 —— 那样 6 张图长得一模一样。
  // 这里先把目标区滚进视口，再按它的 boundingBox 裁图。
  let clip = null;
  if (selector) {
    clip = await cdp.eval(`(()=>{
      const el=document.querySelector(${JSON.stringify(selector)});
      if(!el) return null;
      el.scrollIntoView({block:'center'});
      const r=el.getBoundingClientRect();
      return JSON.stringify({x:Math.max(0,r.left),y:Math.max(0,r.top),
        width:Math.min(r.width,1600),height:Math.min(r.height,1000)});
    })()`);
    clip = clip ? JSON.parse(clip) : null;
    await sleep(400);
    if (clip) {
      // scrollIntoView 后重算（元素可能已经移动）
      const again = await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});
        const r=el.getBoundingClientRect();
        return JSON.stringify({x:Math.max(0,r.left),y:Math.max(0,r.top),
          width:Math.min(r.width,1600),height:Math.min(r.height,1000)});})()`);
      clip = again ? JSON.parse(again) : clip;
    }
  }
  const params = { format: 'png' };
  if (clip && clip.width > 8 && clip.height > 8) {
    params.clip = { x: clip.x, y: clip.y, width: clip.width, height: clip.height, scale: 1 };
  }
  const r = await cdp.send('Page.captureScreenshot', params);
  const f = path.join(SHOT_DIR, name + '.png');
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

/** 日内演化区选择器（截图统一拍它） */
const EVO_SEL = '.evo-body';

// 预测浮层该不该标「历史预测」，取决于 amv_live.json 的实际交易日：
// 盘中按需刷新接通后，当日快照就是实时的；硬编码某个旧日期会让脚本在功能修好后反而失败。
const pad2 = (n) => (n < 10 ? '0' : '') + n;
function todayStr() {
  const d = new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}
function liveDay(doc) {
  const raw = String((doc && (doc.trade_date || doc.tick_date || (doc.meta || {}).trade_date)) || '').trim();
  return raw.slice(0, 10);
}

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  if (!fs.existsSync(FILLED)) { console.log('SKIP: 缺 tests/fixtures/track_filled_20261027.json（先跑 tests/gen_track_filled.py）'); process.exit(0); }
  const track = JSON.parse(fs.readFileSync(FILLED, 'utf8'));

  try { await getJson(BASE + '/api/health'); } catch (e) {
    console.log('SKIP: 8810 未在跑'); process.exit(0);
  }

  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = path.join(os.tmpdir(), 'evomode-' + Date.now());
  const port = 9600 + Math.floor(Math.random() * 90);
  const proc = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--hide-scrollbars', '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });

  let targets = null;
  for (let i = 0; i < 100; i++) { try { targets = await getJson('http://127.0.0.1:' + port + '/json/list'); break; } catch (e) { await sleep(250); } }
  if (!targets) { console.log('SKIP: cdp 起不来'); proc.kill(); process.exit(0); }

  const cdp = new CDP(targets.find((x) => x.type === 'page').webSocketDebuggerUrl);
  await cdp.open();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');

  // 只劫持这一页的 /api/sector_track.json（其它请求照旧打真接口）
  const stub = `
(() => {
  const TRACK = ${JSON.stringify(track)};
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/api/sector_track.json') >= 0) {
      return Promise.resolve(new Response(JSON.stringify(TRACK), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }));
    }
    return orig.apply(this, arguments);
  };
})();
`;
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: stub });
  await cdp.send('Page.navigate', { url: CARD_URL });
  await sleep(5000);

  // ---- 找到盘中卡的 evo 区
  const ready = await cdp.eval(`(async()=>{
    const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    const btn=[...document.querySelectorAll('#rail-tabs .rail-btn')]
      .find(b=>b.dataset.cardKey==='intraday::intraday.main');
    if(btn){ btn.click(); await w(1500); }
    for(let i=0;i<40;i++){
      const b=document.querySelector('.evo-body');
      if(b && b.querySelector('svg.st-chart')) return JSON.stringify({ok:true, panes:b.querySelectorAll('.evo-pane').length});
      await w(250);
    }
    return JSON.stringify({ok:false, rail:[...document.querySelectorAll('#rail-tabs .rail-btn')].map(x=>x.dataset.cardKey),
      html:(document.body.innerText||'').slice(0,300)});
  })()`);
  const R = JSON.parse(ready);
  check('盘中卡日内演化区已渲染', R.ok === true, R);
  if (!R.ok) { await shot(cdp, '00_not_ready'); cdp.close(); proc.kill(); process.exit(1); }

  await shot(cdp, '01_amv_default', EVO_SEL);

  // ---- ① AMV 模式：预测浮层在，且标「历史预测 2026-10-23 12:24」
  const amv = await cdp.eval(`(()=>{
    const b=document.querySelector('.evo-body');
    const fc=b.querySelector('.amv-fc-ov');
    const stale=b.querySelector('.amv-fc-ov [data-stale]');
    return JSON.stringify({
      hasFc: !!fc,
      fcText: fc ? fc.innerText.replace(/\\s+/g,' ').trim().slice(0,120) : '',
      stale: stale ? stale.innerText.trim() : '',
      title: (b.querySelector('h3.evo-h')||{}).innerText||'',
      hasInfo: !!b.querySelector('.evo-info'),
      svg: b.querySelectorAll('svg.st-chart[data-evo]').length,
      kind: (b.querySelector('svg.st-chart[data-evo]')||{}).getAttribute ? b.querySelector('svg.st-chart[data-evo]').getAttribute('data-evo') : '',
    });
  })()`);
  const A = JSON.parse(amv);
  const live = await getJson(BASE + '/api/amv_live.json').catch(() => null);
  const lday = liveDay(live);
  const predIsToday = !!lday && lday === todayStr();
  check('AMV 模式显示预测浮层', A.hasFc === true, A);
  check(predIsToday ? '预测是今日快照 → 不标历史' : '预测非今日 → 标「历史预测 ' + lday + '」',
    predIsToday ? (A.stale === '' || !/历史预测/.test(A.stale)) : new RegExp('历史预测 ' + lday).test(A.stale),
    { stale: A.stale, lday: lday, today: todayStr() });
  check('AMV 模式标题是活跃指数', /活跃指数/.test(A.title), A.title);
  check('AMV 模式左图 data-evo=amv', A.kind === 'amv', A.kind);

  // ---- ② 切到行情涨跌：预测/信息行消失、标题改行情、指数线出现
  await cdp.eval(`(()=>{const b=document.querySelector('.evo-body');
    const btn=[...b.querySelectorAll('[data-act="evo-mode"]')].find(x=>x.dataset.v==='quote');
    if(btn) btn.click(); return true;})()`);
  await sleep(600);
  const quote = await cdp.eval(`(()=>{
    const b=document.querySelector('.evo-body');
    const svg=b.querySelector('svg.st-chart[data-evo="quote"]');
    const ds=svg?[...svg.querySelectorAll('path')].map(p=>p.getAttribute('d')).filter(Boolean):[];
    return JSON.stringify({
      hasFc: !!b.querySelector('.amv-fc-ov'),
      hasInfo: !!b.querySelector('.evo-info'),
      title: (b.querySelector('h3.evo-h')||{}).innerText||'',
      kind: svg?'quote':'',
      paths: ds.length,
      labels: svg?[...svg.querySelectorAll('text.st-lab')].map(t=>t.textContent):[],
      buttons: [...b.querySelectorAll('[data-act="evo-idx"]')].map(x=>x.dataset.code),
    });
  })()`);
  const Q = JSON.parse(quote);
  check('行情模式不显示预测浮层', Q.hasFc === false, Q);
  check('行情模式不显示全A 信息行', Q.hasInfo === false, Q);
  check('行情模式标题改成「行情涨跌」', /行情涨跌/.test(Q.title), Q.title);
  check('行情模式左图 data-evo=quote', Q.kind === 'quote', Q.kind);
  check('行情模式有指数曲线（不空白）', Q.paths >= 1, Q.paths);
  check('六个指数开关都在', (Q.buttons || []).length === 6, Q.buttons);
  await shot(cdp, '02_quote_mode', EVO_SEL);

  // ---- ③ 逐一点指数：path 真的变
  const seq = [];
  for (const code of ['000001.SH', '399006.SZ', '000688.SH', '000300.SH', '000852.SH', '399303.SZ']) {
    await cdp.eval(`(()=>{const b=document.querySelector('.evo-body');
      const btn=[...b.querySelectorAll('[data-act="evo-idx"]')].find(x=>x.dataset.code==='${code}');
      if(btn) btn.click(); return true;})()`);
    await sleep(320);
    const st = await cdp.eval(`(()=>{const b=document.querySelector('.evo-body');
      const svg=b.querySelector('svg.st-chart[data-evo="quote"]');
      const ds=svg?[...svg.querySelectorAll('path')].map(p=>p.getAttribute('d')).filter(Boolean):[];
      const on=[...b.querySelectorAll('[data-act="evo-idx"].on')].map(x=>x.dataset.code);
      return JSON.stringify({paths:ds.length, on:on, sig:ds.join('|').length, hasFc:!!b.querySelector('.amv-fc-ov')});
    })()`);
    seq.push({ code: code, ...JSON.parse(st) });
  }
  const sigs = seq.map((s) => s.sig);
  check('逐个点指数都改变了 path（6 次各不相同）', new Set(sigs).size >= 5, seq.map((s) => [s.code, s.paths, s.on.length, s.sig]));
  check('点满后同时勾选 6 个指数', seq[seq.length - 1].on.length === 6, seq[seq.length - 1]);
  check('点指数过程中行情模式仍无预测浮层', seq.every((s) => s.hasFc === false), seq.map((s) => s.hasFc));
  await shot(cdp, '03_quote_all_idx', EVO_SEL);

  // ---- ④ 只勾一个：path 应只剩 1 条
  // 注意：每次点击都会 paint() 重建 innerHTML，旧节点会脱离文档——所以必须**每次现查**，
  // 不能先拿一批节点再连着点（那样除第一个外都是空点击）。
  await cdp.eval(`(async()=>{
    const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    for(let i=0;i<10;i++){
      const b=document.querySelector('.evo-body');
      const extra=[...b.querySelectorAll('[data-act="evo-idx"].on')]
        .filter(x=>x.dataset.code!=='000001.SH');
      if(!extra.length) break;
      extra[0].click(); await w(260);
    }
    return true;
  })()`);
  await sleep(500);
  const one = await cdp.eval(`(()=>{const b=document.querySelector('.evo-body');
    const svg=b.querySelector('svg.st-chart[data-evo="quote"]');
    const ds=svg?[...svg.querySelectorAll('path')].map(p=>p.getAttribute('d')).filter(Boolean):[];
    return JSON.stringify({paths:ds.length, labels:(svg?[...svg.querySelectorAll('text.st-lab')].map(t=>t.textContent):[])});
  })()`);
  const O = JSON.parse(one);
  check('只勾上证 → 图上只剩 1 条线', O.paths === 1, O);
  check('末值标签跟曲线同数', O.labels.length === 1, O.labels);
  await shot(cdp, '04_quote_one_idx', EVO_SEL);

  // ---- ⑤ 切回 AMV：预测回来（仍带历史标记），六指数叠加还在
  await cdp.eval(`(()=>{const b=document.querySelector('.evo-body');
    const btn=[...b.querySelectorAll('[data-act="evo-mode"]')].find(x=>x.dataset.v==='amv');
    if(btn) btn.click(); return true;})()`);
  await sleep(600);
  const back = await cdp.eval(`(()=>{const b=document.querySelector('.evo-body');
    const svg=b.querySelector('svg.st-chart[data-evo="amv"]');
    const ds=svg?[...svg.querySelectorAll('path')].map(p=>p.getAttribute('d')).filter(Boolean):[];
    const stale=b.querySelector('.amv-fc-ov [data-stale]');
    return JSON.stringify({
      hasFc: !!b.querySelector('.amv-fc-ov'),
      stale: stale?stale.innerText.trim():'',
      kind: svg?'amv':'',
      paths: ds.length,
      title: (b.querySelector('h3.evo-h')||{}).innerText||'',
      date: stale?stale.innerText.trim():'',
    });
  })()`);
  const B = JSON.parse(back);
  check('切回 AMV 模式预测回来', B.hasFc === true, B);
  check(predIsToday ? '切回 AMV 仍是今日实时预测' : '切回 AMV 仍标历史预测日期',
    predIsToday ? (B.stale === '' || !/历史预测/.test(B.stale)) : new RegExp('历史预测 ' + lday).test(B.stale),
    { stale: B.stale, lday: lday });
  check('切回 AMV 模式标题回活跃指数', /活跃指数/.test(B.title), B.title);
  check('AMV 模式保留六指数叠加（活跃+上证 ≥2 条）', B.paths >= 2, B.paths);
  await shot(cdp, '05_amv_back', EVO_SEL);

  const ex = cdp.events.filter((e) => e.method === 'Runtime.exceptionThrown');
  check('console 异常 0', ex.length === 0, ex.slice(0, 2).map((e) => JSON.stringify(e.params).slice(0, 200)));

  console.log('shots -> ' + SHOT_DIR);
  cdp.close();
  proc.kill();
  await sleep(300);
  console.log(failed ? ('FAIL ' + failed + '/' + ran) : ('PASS ' + ran + '/' + ran));
  process.exit(failed ? 1 : 0);
})();
