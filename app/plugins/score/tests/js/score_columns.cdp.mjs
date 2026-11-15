/**
 * 打分接入三个页面 · 真实浏览器验收（CDP → 8810）
 *
 * 跑法（先确保 8810 在跑）：
 *   node app/plugins/score/tests/js/score_columns.cdp.mjs --variant normal
 *   node app/plugins/score/tests/js/score_columns.cdp.mjs --variant stale      # 产物偏旧
 *   node app/plugins/score/tests/js/score_columns.cdp.mjs --variant partial    # 部分票不在打分池
 *   node app/plugins/score/tests/js/score_columns.cdp.mjs --variant noplugin   # 模拟 score 插件不可用（404）
 *
 * 量什么：
 *   · 三个页面的打分列是否渲染（选择器见各段）与样例文案
 *   · 布局有没有被挤坏：表格 scrollWidth vs clientWidth、列宽、卡片高度
 *   · 降级形态：缺数据时必须出现「—」/「无打分数据」，且页面无 JS 异常
 *
 * stale / partial / noplugin 三种形态用 CDP Fetch 改写 `/api/p/score/map` 的响应得到
 * （真实产物每天只有一份，产不出这两种极端值）；noplugin 只改响应，真实删插件的演练在
 * 交付记录里单独做（删目录 + 重启 + 同一脚本 --variant normal）。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../../..');
const argOf = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const VARIANT = argOf('--variant', 'normal');
const SHOT_DIR = path.resolve(PANEL, argOf('--shots', '_vis/score_integration'));
const BASE = 'http://127.0.0.1:8810';
const MAP_URL = BASE + '/api/p/score/map';
const KEYS = { selection: 'selection::selection.main', watchlist: 'watchlist::watchlist.main', sector: 'sector::sector.board' };
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
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.handlers = new Map(); this.errors = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id); this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result); return;
        }
        if (m.method === 'Runtime.exceptionThrown') {
          const d = m.params.exceptionDetails || {};
          this.errors.push((d.text || '') + ' ' + ((d.exception && d.exception.description) || ''));
        }
        if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
          this.errors.push('console.error: ' + (m.params.args || []).map((a) => a.value || a.description || '').join(' '));
        }
        if (m.method && this.handlers.has(m.method)) for (const fn of this.handlers.get(m.method)) fn(m.params).catch(() => {});
      };
      this.ws.onopen = res; this.ws.onerror = rej;
    });
  }
  on(method, fn) { if (!this.handlers.has(method)) this.handlers.set(method, []); this.handlers.get(method).push(fn); }
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

async function waitFor(cdp, expr, ms = 15000) {
  const t0 = Date.now();
  for (;;) {
    if (await cdp.eval(expr)) return true;
    if (Date.now() - t0 > ms) return false;
    await sleep(120);
  }
}

async function shot(cdp, name, selector) {
  const raw = selector ? await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)return null;
    const r=el.getBoundingClientRect();
    return JSON.stringify({x:Math.max(0,r.left),y:Math.max(0,r.top),
      width:Math.min(r.width,1600),height:Math.min(r.height,1400)});})()`) : null;
  const clip = raw ? JSON.parse(raw) : null;
  const params = { format: 'png' };
  if (clip && clip.width > 8 && clip.height > 8) params.clip = { ...clip, scale: 1 };
  const r = await cdp.send('Page.captureScreenshot', params);
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const f = path.join(SHOT_DIR, VARIANT + '-' + name + '.png');
  fs.writeFileSync(f, Buffer.from(r.data, 'base64'));
  return f;
}

/** 造变异响应（stale / partial / noplugin）：只改 map 响应，不碰仓库与产物。 */
async function installVariant(cdp) {
  if (VARIANT === 'normal') return;
  let body = null;
  let code = 200;
  if (VARIANT === 'noplugin') {
    code = 404;
    body = JSON.stringify({ detail: 'Not Found' });
  } else {
    const j = await getJson(MAP_URL);
    if (VARIANT === 'stale') {
      j.asof = '2026-10-20';
      j.generated_at = '2026-10-20 22:30:00';
      j.stale = true;
    } else if (VARIANT === 'partial') {
      // 删掉 300 个代码 = 模拟「这批票不在打分池」
      const keys = Object.keys(j.map);
      for (let i = 0; i < 300 && i < keys.length; i += 1) delete j.map[keys[i]];
      j.n = Object.keys(j.map).length;
    }
    body = JSON.stringify(j);
  }
  cdp.on('Fetch.requestPaused', async (p) => {
    if (p.request.url.indexOf('/api/p/score/map') < 0) {
      await cdp.send('Fetch.continueRequest', { requestId: p.requestId });
      return;
    }
    await cdp.send('Fetch.fulfillRequest', {
      requestId: p.requestId, responseCode: code,
      responseHeaders: [
        { name: 'Content-Type', value: 'application/json; charset=utf-8' },
        { name: 'Cache-Control', value: 'no-store' },
      ],
      body: Buffer.from(body, 'utf8').toString('base64'),
    });
  });
  await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/p/score/map*', requestStage: 'Request' }] });
}

async function rail(cdp, key, probe, ms = 25000) {
  const ok = await cdp.eval(`(()=>{const b=document.querySelector('#rail-tabs .rail-btn[data-card-key="${key}"]');
    if(!b) return false; b.click(); return true;})()`);
  if (!ok) throw new Error('没有侧栏按钮：' + key);
  if (!(await waitFor(cdp, probe, ms))) throw new Error('卡未就绪：' + key);
}

const checks = [];
function check(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail === undefined ? '' : '  → ' + JSON.stringify(detail)));
}

(async () => {
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP: 没有本地 Chrome/Edge'); process.exit(0); }
  try { await getJson(BASE + '/api/health'); } catch (e) { console.log('SKIP: 8810 未在跑'); process.exit(0); }

  const profile = path.join(os.tmpdir(), 'scorecols-' + Date.now());
  const port = 9800 + Math.floor(Math.random() * 90);
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
  await cdp.send('Network.enable');
  await installVariant(cdp);

  const out = {};
  try {
    await cdp.send('Page.navigate', { url: BASE + '/v2/' });
    await waitFor(cdp, "document.querySelectorAll('#rail-tabs .rail-btn').length > 4", 25000);

    // ---------------------------------------------------------------- 1. 选股
    await rail(cdp, KEYS.selection, "document.querySelectorAll('.sel-r').length > 0");
    await sleep(600);
    out.selection = JSON.parse(await cdp.eval(`(()=>{
      const wrap=document.querySelector('.sel-tablewrap');
      const head=[...document.querySelectorAll('.sel-head .h')].map(h=>h.textContent.trim());
      const dsIdx=head.findIndex(t=>t.indexOf('打分')===0);
      const rows=[...document.querySelectorAll('.sel-r')];
      const cells=rows.map(r=>{const c=r.children[dsIdx];return c?c.textContent.trim():null;});
      const r0=rows[0];
      const widths=r0?[...r0.children].map(c=>Math.round(c.getBoundingClientRect().width)):[];
      const stat=document.querySelector('.sel-stat');
      return JSON.stringify({head, dsIdx, sample:cells.slice(0,5), cells:n_cells(rows),
        scrollW:wrap?wrap.scrollWidth:0, clientW:wrap?wrap.clientWidth:0,
        widths, stat:stat?stat.textContent:'',
        trackCount:getComputedStyle(document.querySelector('.sel-head')).gridTemplateColumns.split(' ').length});
      function n_cells(rs){return rs.filter(r=>r.children[dsIdx]&&r.children[dsIdx].textContent.trim()).length;}
    })()`));
    await shot(cdp, 'selection', '.sel-card');
    check('选股：打分列存在', out.selection.dsIdx > 0, out.selection.head.join('|'));
    check('选股：无横向溢出', out.selection.scrollW <= out.selection.clientW + 1, out.selection);
    check('选股：栏目轨数与列数一致', out.selection.trackCount === out.selection.head.length,
      { tracks: out.selection.trackCount, cols: out.selection.head.length });
    check('选股：状态行写明打分口径/无数据', /打分 \d{4}-\d{2}-\d{2}|无打分数据/.test(out.selection.stat), out.selection.stat);
    check('选股：样例单元格', out.selection.sample.some((t) => t && t !== ''), out.selection.sample);

    // ---------------------------------------------------------------- 2. 自选池
    await rail(cdp, KEYS.watchlist, "document.querySelectorAll('.wl-wc').length > 0");
    await sleep(1200);
    out.watchlist = JSON.parse(await cdp.eval(`(()=>{
      const cards=[...document.querySelectorAll('.wl-wc[data-kind="stock"]')];
      const metas=[...document.querySelectorAll('.wl-meta')].map(m=>m.textContent);
      const lines=cards.map(c=>{const s=c.querySelector('.wl-score');return s?s.textContent.trim():null;});
      const heights=cards.map(c=>Math.round(c.getBoundingClientRect().height));
      const overflow=cards.filter(c=>c.scrollHeight>c.clientHeight+1).length;
      return JSON.stringify({n:cards.length, lines, heights:[...new Set(heights)],
        clipped:overflow, meta:metas.join(' ')});
    })()`));
    await shot(cdp, 'watchlist', '.wl-grid');
    check('自选池：每张个股卡都有打分位', out.watchlist.n > 0 && out.watchlist.lines.every((x) => x), out.watchlist.lines.slice(0, 6));
    check('自选池：卡片未被裁切', out.watchlist.clipped === 0, { clipped: out.watchlist.clipped, heights: out.watchlist.heights });
    check('自选池：页眉写明打分口径/无数据', /打分 \d{4}-\d{2}-\d{2}|无打分数据/.test(out.watchlist.meta), out.watchlist.meta);

    // ---------------------------------------------------------------- 3. 板块详情个股列表
    await rail(cdp, KEYS.sector, "document.querySelectorAll('.sector-card .panel-in li.row').length > 3", 30000);
    await sleep(500);
    const opened = await cdp.eval(`(()=>{const li=document.querySelector('.sector-card .panel-in li.row');
      if(!li) return false;
      const row=li.querySelector('.row-hd, .row') || li; row.click(); return true;})()`);
    check('板块：能打开详情', opened);
    await waitFor(cdp, "document.querySelectorAll('#sector-modal .card-tbl tbody tr').length > 3", 25000);
    await sleep(700);
    out.sector = JSON.parse(await cdp.eval(`(()=>{
      const box=document.querySelector('#sector-modal .modal');
      const cr=document.querySelector('#sector-modal .card-right');
      const heads=[...document.querySelectorAll('#sector-modal .card-tbl thead th')].map(h=>h.textContent.trim());
      const idx=heads.findIndex(t=>t.indexOf('打分')===0);
      const rows=[...document.querySelectorAll('#sector-modal .card-tbl tbody tr')];
      const cells=rows.map(r=>{const c=r.children[idx];return c?c.textContent.trim():null;});
      const h4=document.querySelector('#sector-modal .card-right h4');
      const tbl=document.querySelector('#sector-modal .card-tbl');
      const modalW=box?Math.round(box.getBoundingClientRect().width):0;
      const tblW=tbl?Math.round(tbl.getBoundingClientRect().width):0;
      return JSON.stringify({heads, idx, sample:cells.slice(0,6), n:rows.length,
        note:h4?h4.textContent:'', rightScroll:cr?cr.scrollWidth:0, rightClient:cr?cr.clientWidth:0,
        modalW, tblW});
    })()`));
    await shot(cdp, 'sector-detail', '#sector-modal .modal');
    check('板块：打分列存在', out.sector.idx > 0, out.sector.heads.join('|'));
    check('板块：表头写明打分日与口径', /打分 \d{4}-\d{2}-\d{2} 收盘口径|无打分数据/.test(out.sector.note), out.sector.note);
    check('板块：表格未被挤变形（无横向溢出）', out.sector.rightScroll <= out.sector.rightClient + 1, out.sector);
    check('板块：样例单元格', out.sector.sample.some((t) => t && t !== '—'), out.sector.sample);

    check('全程无 JS 异常', cdp.errors.length === 0, cdp.errors.slice(0, 5));
  } catch (err) {
    check('脚本执行', false, String(err && err.message ? err.message : err));
  } finally {
    cdp.close();
    proc.kill();
  }

  const bad = checks.filter((c) => !c.ok);
  console.log('\n[summary] variant=' + VARIANT + ' pass=' + (checks.length - bad.length) + '/' + checks.length
    + ' shots=' + SHOT_DIR);
  console.log(JSON.stringify(out, null, 1).slice(0, 4000));
  process.exit(bad.length ? 1 : 0);
})();
