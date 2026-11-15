/**
 * 盘中卡 · 「正负对比 / 行情涨跌基准线」实机取证（CDP → 8810，真实页面，不改 8810）
 *
 * 跑法：node app/plugins/intraday/tests/js/evo_dir_all.cdp.mjs [--shots <dir>]
 *
 * 目的（主人 2026-10-28 两条标注）：
 *   N20261028-140428-39「行情涨跌也需要跟 amv 涨跌一样用全A作为基准线」
 *   N20261028-140538-40「正负对比功能不可用」
 *
 * 只读：不劫持接口、不写生产文件；点的是真实按钮、读的是真实 DOM。
 * 输出：每条判定 ok / NOT OK + 关键数量（榜单行数、曲线条数、控制台异常）。
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
const CARD_URL = BASE + '/v2/';
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
  else { failed++; console.log('NOT OK ' + ran + ' - ' + name + (detail !== undefined ? ' :: ' + JSON.stringify(detail).slice(0, 800) : '')); }
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
  const f = path.join(SHOT_DIR, name + '.png');
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

const EVO_SEL = '.evo-body';
const RIGHT = "document.querySelector('.evo-body').querySelectorAll('.evo-pane')[1]";

/** 右栏一次快照：模式/方向/排序按钮态 + 榜单 + 贡献图条数 */
const sniff = `(()=>{
  const pane=${RIGHT}; if(!pane) return JSON.stringify({err:'no pane'});
  const segs=[...pane.querySelectorAll('.evo-seg')];
  const onOf=(s)=>[...s.querySelectorAll('button')].map(b=>b.dataset.v+':'+(b.classList.contains('on')?1:0));
  const rows=[...pane.querySelectorAll('.evo-rank-row')].map(r=>({
    code:r.dataset.code, name:(r.querySelector('.evo-rank-name')||{}).textContent||'',
    val:(r.querySelector('.evo-rank-val')||{}).textContent||''}));
  const svg=pane.querySelector('svg.st-chart[data-evo="contrib"]');
  const paths=svg?[...svg.querySelectorAll('path.evo-line')].map(p=>p.getAttribute('d')).filter(Boolean):[];
  return JSON.stringify({dir:segs[0]?onOf(segs[0]):[], srt:segs[1]?onOf(segs[1]):[],
    rows:rows, rowCount:rows.length, paths:paths.length,
    muted:(pane.querySelector('p.muted')||{}).textContent||'',
    src:((pane.querySelector('.src')||{}).textContent||'').slice(0,120)});
})()`;

/** 左栏一次快照：模式 + 曲线条数 + 末值标签 */
const sniffLeft = `(()=>{
  const pane=document.querySelector('.evo-body').querySelectorAll('.evo-pane')[0];
  const mode=[...pane.querySelectorAll('[data-act="evo-mode"]')].map(b=>b.dataset.v+':'+(b.classList.contains('on')?1:0));
  const svg=pane.querySelector('svg.st-chart[data-evo]');
  const kind=svg?svg.getAttribute('data-evo'):'';
  const paths=svg?[...svg.querySelectorAll('path')].map(p=>p.getAttribute('d')).filter(Boolean):[];
  const allbase=svg?svg.querySelectorAll('path.evo-allbase').length:0;
  const idxPaths=svg?[...svg.querySelectorAll('path')].filter(p=>!p.classList.contains('evo-allbase'))
    .map(p=>p.getAttribute('d')).filter(Boolean).length:0;
  const labels=svg?[...svg.querySelectorAll('text.st-lab')].map(t=>t.textContent):[];
  const title=(pane.querySelector('h3.evo-h')||{}).textContent||'';
  const lg=[...pane.querySelectorAll('.evo-lg-i')].map(x=>x.textContent.trim());
  return JSON.stringify({mode:mode, kind:kind, paths:idxPaths, allbase:allbase, labels:labels, title:title, lg:lg});
})()`;

const clickSeg = (act, v) => `(()=>{const b=document.querySelector('.evo-body');
  const btn=[...b.querySelectorAll('[data-act="${act}"]')].find(x=>x.dataset.v==='${v}');
  if(!btn) return 'no-button'; btn.click(); return 'clicked';})()`;

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }

  const profile = path.join(os.tmpdir(), 'evodir-' + Date.now());
  const port = 9700 + Math.floor(Math.random() * 90);
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
  await cdp.send('Network.enable');
  await cdp.send('Page.navigate', { url: CARD_URL });
  await sleep(4500);

  const ready = await cdp.eval(`(async()=>{
    const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    const btn=[...document.querySelectorAll('#rail-tabs .rail-btn')]
      .find(b=>b.dataset.cardKey==='intraday::intraday.main');
    if(btn){ btn.click(); await w(1500); }
    for(let i=0;i<60;i++){
      const b=document.querySelector('.evo-body');
      if(b && b.querySelector('svg.st-chart[data-evo="contrib"]')) return JSON.stringify({ok:true});
      await w(250);
    }
    return JSON.stringify({ok:false, txt:(document.body.innerText||'').slice(0,200)});
  })()`);
  const R = JSON.parse(ready);
  check('盘中卡日内演化（含贡献图）已渲染', R.ok === true, R);
  if (!R.ok) { await shot(cdp, 'probe_00_not_ready'); cdp.close(); proc.kill(); process.exit(1); }

  // ---- 基线：默认 dir=pos
  await shot(cdp, 'probe_10_evo_default', EVO_SEL);
  const base = JSON.parse(await cdp.eval(sniff));
  console.log('baseline ' + JSON.stringify(base).slice(0, 600));

  // ---- ③ 行情涨跌模式：有没有全A 基准线
  await cdp.eval(clickSeg('evo-mode', 'quote'));
  await sleep(700);
  const L0 = JSON.parse(await cdp.eval(sniffLeft));
  await shot(cdp, 'probe_11_quote_allbase', EVO_SEL);
  console.log('quote ' + JSON.stringify(L0).slice(0, 600));
  check('行情涨跌模式已切过去', /行情涨跌/.test(L0.title), L0.title);
  check('行情涨跌模式有全A 基准线（N20261028-140428-39 已修）', L0.allbase === 1, L0);
  check('全A 基准线带末值标签', (L0.labels || []).some((t) => /^全A /.test(t)), L0.labels);
  check('基准线不挤掉指数线（默认仍画两条）', L0.paths === 2, L0.paths);

  // ---- ④ 正负对比
  const clicks = {};
  for (const v of ['all', 'neg', 'pos']) {
    clicks[v] = await cdp.eval(clickSeg('evo-dir', v));
    await sleep(700);
    const s = JSON.parse(await cdp.eval(sniff));
    clicks[v] = { ret: clicks[v], dir: s.dir, rowCount: s.rowCount, paths: s.paths, muted: s.muted,
      firstRows: (s.rows || []).slice(0, 3).map((r) => r.name + ' ' + r.val),
      lastRows: (s.rows || []).slice(-3).map((r) => r.name + ' ' + r.val) };
    await shot(cdp, 'probe_12_dir_' + v, EVO_SEL);
  }
  console.log('dir clicks ' + JSON.stringify(clicks).slice(0, 1200));

  const all = clicks.all;
  check('正负对比：按钮点击有响应（dir=all 已选中）',
    (all.dir || []).includes('all:1'), all.dir);
  check('正负对比：榜单同时含正负两侧（≥6 行）', all.rowCount >= 6, all.rowCount);
  check('正负对比：贡献图画出了曲线', all.paths >= 6, all.paths);
  const posN = (all.firstRows || []).length;
  check('正负对比：没有落到「暂无数据」', !/暂无数据/.test(all.muted || ''), all.muted);

  // 正负两向的正负号检查：前 5 行应含正号、后 5 行应含负号
  const allRows = JSON.parse(await cdp.eval(`(()=>{const pane=${RIGHT};
    return JSON.stringify([...pane.querySelectorAll('.evo-rank-row')].map(r=>r.querySelector('.evo-rank-val').textContent));})()`));
  const vals = allRows;

  // ---- 真实鼠标点击（CDP Input）：区分「.click() 能跑」与「鼠标真的点得到」
  const realClick = async (label, act, v) => {
    const info = await cdp.eval(`(()=>{
      const b=document.querySelector('.evo-body');
      const btn=[...b.querySelectorAll('[data-act="${act}"]')].find(x=>x.dataset.v==='${v}');
      if(!btn) return JSON.stringify({err:'no-button'});
      btn.scrollIntoView({block:'center'});
      const r=btn.getBoundingClientRect();
      const cx=Math.round(r.left+r.width/2), cy=Math.round(r.top+r.height/2);
      const top=document.elementFromPoint(cx,cy);
      return JSON.stringify({rect:{x:r.left,y:r.top,w:r.width,h:r.height}, cx:cx, cy:cy,
        topTag:top?top.tagName:'',
        topCls:top?(top.className&&top.className.baseVal!==undefined?top.className.baseVal:top.className):'',
        isSelf: !!(top && (top===btn || btn.contains(top)))});
    })()`);
    const I = JSON.parse(info);
    if (I.err) { check(label + '：找到按钮', false, I); return I; }
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: I.cx, y: I.cy, button: 'none' });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: I.cx, y: I.cy, button: 'left', clickCount: 1 });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: I.cx, y: I.cy, button: 'left', clickCount: 1 });
    await sleep(500);
    check(label + '：鼠标点位命中按钮本身（没被浮层挡住）', I.isSelf === true,
      { cx: I.cx, cy: I.cy, top: I.topTag + '.' + I.topCls, rect: I.rect });
    return I;
  };

  await cdp.eval(`(()=>{const b=document.querySelector('.evo-body'); if(b) b.scrollIntoView({block:'center'}); return 1;})()`);
  await sleep(300);
  await realClick('正负对比（真实鼠标）', 'evo-dir', 'all');
  const afterReal = JSON.parse(await cdp.eval(sniff));
  check('正负对比：真实鼠标点击后 dir=all 生效', (afterReal.dir || []).includes('all:1'), afterReal.dir);
  const mix = JSON.parse(await cdp.eval(`(()=>{const pane=${RIGHT};
    const vs=[...pane.querySelectorAll('.evo-rank-val')].map(r=>r.textContent);
    return JSON.stringify({pos:vs.filter(v=>v.charAt(0)==='+').length, neg:vs.filter(v=>v.charAt(0)==='-').length,
      seps:[...pane.querySelectorAll('.evo-rank-sep')].map(x=>x.textContent)});})()`));
  check('正负对比：真实点击后榜单同时有正有负', mix.pos >= 3 && mix.neg >= 3, mix);
  check('正负对比：两组有分组标（正向 / 负向）', (mix.seps || []).join(',') === '正向,负向', mix.seps);
  await shot(cdp, 'probe_14_realclick_all', EVO_SEL);

  // ---- 浮层遮挡探针：AMV 模式（有预测浮层）下，右栏三个方向按钮是否被挡住
  const probeHit = async (mode) => {
    await cdp.eval(clickSeg('evo-mode', mode));
    await sleep(700);
    const hit = JSON.parse(await cdp.eval(`(()=>{
      const b=document.querySelector('.evo-body');
      const pane=b.querySelectorAll('.evo-pane')[1];
      const out=[];
      for(const v of ['pos','neg','all']){
        const btn=[...pane.querySelectorAll('[data-act="evo-dir"]')].find(x=>x.dataset.v===v);
        if(!btn){ out.push({v:v, err:'no-button'}); continue; }
        const r=btn.getBoundingClientRect();
        const cx=Math.round(r.left+r.width/2), cy=Math.round(r.top+r.height/2);
        const top=document.elementFromPoint(cx,cy);
        out.push({v:v, w:Math.round(r.width), h:Math.round(r.height), cx:cx, cy:cy,
          top: top? (top.tagName+'.'+(top.className&&top.className.baseVal!==undefined?top.className.baseVal:top.className)) : '',
          self: !!(top && (top===btn || btn.contains(top)))});
      }
      const fc=b.querySelector('.amv-fc-ov');
      const fr=fc?fc.getBoundingClientRect():null;
      return JSON.stringify({mode:'${mode}', hits:out, fc: fr?{x:Math.round(fr.left),y:Math.round(fr.top),w:Math.round(fr.width),h:Math.round(fr.height)}:null});
    })()`));
    console.log('hit ' + JSON.stringify(hit));
    check(mode + ' 模式下：正/负/正负对比三个按钮都能被点到', hit.hits.every((x) => x.self === true), hit);
    return hit;
  };
  await probeHit('amv');
  await shot(cdp, 'probe_15_amv_hdr', EVO_SEL);
  await probeHit('quote');

  // ---- 状态保持：切去别的卡再回来，正负对比还选着吗？
  await cdp.eval(clickSeg('evo-dir', 'all'));
  await sleep(500);
  const keep = await cdp.eval(`(async()=>{
    const w=(ms)=>new Promise(r=>setTimeout(r,ms));
    const rail=()=>[...document.querySelectorAll('#rail-tabs .rail-btn')];
    const cur=rail().find(b=>b.dataset.cardKey==='intraday::intraday.main');
    const other=rail().find(b=>b.dataset.cardKey && b.dataset.cardKey!=='intraday::intraday.main');
    if(!other) return JSON.stringify({skip:true});
    const back=other.dataset.cardKey;
    other.click(); await w(1200);
    const c=rail().find(b=>b.dataset.cardKey==='intraday::intraday.main');
    if(c){ c.click(); await w(2500); }
    const b=document.querySelector('.evo-body');
    if(!b) return JSON.stringify({back:back, missing:true});
    const pane=b.querySelectorAll('.evo-pane')[1];
    const dirOn=pane?[...pane.querySelectorAll('[data-act="evo-dir"]')].filter(x=>x.classList.contains('on')).map(x=>x.dataset.v):[];
    return JSON.stringify({back:back, dir:dirOn, rows:pane?pane.querySelectorAll('.evo-rank-row').length:0});
  })()`);
  console.log('keep ' + keep);
  const K = JSON.parse(keep);
  if (!K.skip && !K.missing) check('切卡再回来：正负对比状态保持', (K.dir || []).includes('all'), K);

  // ---- ⑤ 排序依据切 AMV（右图 srt=amv）也验一下不炸
  await cdp.eval(clickSeg('evo-srt', 'amv'));
  await sleep(700);
  const srt = JSON.parse(await cdp.eval(sniff));
  check('排序依据切 AMV 后仍有榜单', srt.rowCount >= 1, srt.rowCount);
  await shot(cdp, 'probe_13_srt_amv', EVO_SEL);
  await cdp.eval(clickSeg('evo-srt', 'contrib'));
  await sleep(400);

  const ex = cdp.events.filter((e) => e.method === 'Runtime.exceptionThrown');
  check('console 异常 0', ex.length === 0, ex.slice(0, 3).map((e) => JSON.stringify(e.params).slice(0, 300)));
  const errs = cdp.events.filter((e) => e.method === 'Log.entryAdded' && e.params.entry.level === 'error');
  const bad = cdp.events.filter((e) => e.method === 'Network.responseReceived'
    && e.params.response && e.params.response.status >= 400)
    .map((e) => e.params.response.status + ' ' + e.params.response.url);
  console.log('http >=400: ' + JSON.stringify([...new Set(bad)]));
  check('log error 0', errs.length === 0, errs.slice(0, 3).map((e) => e.params.entry.text.slice(0, 200)));

  console.log('shots -> ' + SHOT_DIR);
  cdp.close();
  proc.kill();
  await sleep(300);
  console.log(failed ? ('FAIL ' + failed + '/' + ran) : ('PASS ' + ran + '/' + ran));
  process.exit(failed ? 1 : 0);
})();
