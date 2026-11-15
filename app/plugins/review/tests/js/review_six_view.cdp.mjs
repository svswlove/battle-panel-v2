// 复盘六格（复盘卡第四视图）· 真实浏览器核对（Chrome CDP，只读）
//   node app/plugins/review/tests/js/review_six_view.cdp.mjs [--base http://127.0.0.1:8810]
//        [--sixdate 2026-11-03] [--shots <dir>]
//
// 断言（期望值从 /api/review_six.json 现取）：
//   1. 侧栏没有「复盘六格」入口（六格是复盘卡的视图，不占导航位）；
//   2. 板块页没有六格块（变更单 2026-11-03：落点收回，只留复盘视图）；
//   3. 复盘卡切到「六格」→ 6 张卡片式卡，每卡四段（标题/chips/依据/动作-待填）；
//   4. 温度区显式给 AMV 与 MA10 位置；state=多 且低于 MA10 → 「单轨未确认」；
//   5. 杠杆背离无合格标 → 「今日无」；强共振首名 state=多、空头标「未转多」。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, dflt) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const BASE = arg('--base', 'http://127.0.0.1:8810');
const SIXDATE = arg('--sixdate', '');      // 指定六格取数日（不传 = 跟复盘卡当前日）
const SHOT_DIR = arg('--shots', path.join(HERE, '_shots'));
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => {
      let b = '';
      r.on('data', (c) => { b += c; });
      r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

class CDP {
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.consoleErrors = []; }
  open() {
    return new Promise((res, rej) => {
      this.ws = new WebSocket(this.url);
      this.ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.id && this.p.has(m.id)) {
          const { r, j } = this.p.get(m.id);
          this.p.delete(m.id);
          m.error ? j(new Error(JSON.stringify(m.error))) : r(m.result);
          return;
        }
        if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
          this.consoleErrors.push((m.params.args || []).map((a) => a.value || a.description).join(' '));
        }
        if (m.method === 'Runtime.exceptionThrown') {
          this.consoleErrors.push('exception: ' + JSON.stringify(m.params.exceptionDetails).slice(0, 300));
        }
      };
      this.ws.onopen = res;
      this.ws.onerror = rej;
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
  close() { try { this.ws.close(); } catch (e) { /* ignore */ } }
}

let failed = 0;
function check(name, cond, detail) {
  if (cond) { console.log('  ok   ' + name); return; }
  failed++;
  console.log('  FAIL ' + name + (detail === undefined ? '' : ' -> ' + JSON.stringify(detail)));
}

async function waitFor(cdp, expr, ms = 25000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await cdp.eval(expr)) return true;
    await sleep(250);
  }
  return false;
}

async function shot(cdp, name, fullPage) {
  const p = path.join(SHOT_DIR, name);
  const r = await cdp.send('Page.captureScreenshot',
    fullPage ? { format: 'png', captureBeyondViewport: true } : { format: 'png' });
  fs.writeFileSync(p, Buffer.from(r.data, 'base64'));
  console.log('  截图: ' + p);
  return p;
}

const clickText = (text) => `(() => {
  const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === ${JSON.stringify(text)});
  if (!b) return false;
  b.click();
  return true;
})()`;

async function main() {
  const probe = await fetch(BASE + '/').then((r) => r.status).catch(() => 0);
  if (probe !== 200) { console.log('SKIP 实例未启动 ' + BASE); return 0; }
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP 无 Chrome/Edge'); return 0; }

  const api = await fetch(BASE + '/api/review_six.json').then((r) => r.json());
  const latestDate = (api.meta || {}).target_date;
  const sixDate = SIXDATE || latestDate;
  const sixApi = SIXDATE
    ? await fetch(BASE + '/api/review_six.json?date=' + SIXDATE).then((r) => r.json())
    : api;
  const p5 = (sixApi.cells || [])[4] || {};
  const p3 = (sixApi.cells || [])[2] || {};
  const pt = sixApi.temperature || {};
  const pvs = pt.amv_vs_ma10 || {};

  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'rsv-'));
  const port = 9497;
  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
    '--remote-debugging-port=' + port, '--user-data-dir=' + profile,
    '--window-size=1600,1000', 'about:blank',
  ], { stdio: 'ignore' });

  let cdp = null;
  try {
    let ver = null;
    for (let i = 0; i < 60 && !ver; i++) {
      try { ver = await getJson('http://127.0.0.1:' + port + '/json/version'); } catch (e) { await sleep(250); }
    }
    if (!ver) { console.log('SKIP CDP 起不来'); return 0; }
    const list = await getJson('http://127.0.0.1:' + port + '/json/list');
    const target = list.find((t) => t.type === 'page');
    cdp = new CDP(target.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });

    // ---------- 段 1：侧栏 + 板块页（无六格块）
    await cdp.send('Page.navigate', { url: BASE + '/' });
    const booted = await waitFor(cdp, "document.querySelectorAll('.rail-tab,.rail-btn').length > 4", 30000);
    check('壳启动', booted);
    if (!booted) return 1;

    const nav = await cdp.eval(`(() => {
      const labels = Array.from(document.querySelectorAll('.rail-tab,.rail-btn')).map((b) => b.textContent.trim());
      return { hasSix: labels.some((t) => t.includes('六格')), labels };
    })()`);
    check('侧栏无「复盘六格」入口', nav.hasSix === false, nav.labels.filter((t) => t.includes('复')));

    check('点侧栏「板块」', await cdp.eval(clickText('板块')));
    const sectorReady = await waitFor(cdp, "!!document.querySelector('.panels, .vizbox')", 30000);
    check('板块页渲染', sectorReady);
    await sleep(800);
    const sec = await cdp.eval(`(() => {
      const txt = document.body.textContent || '';
      return {
        sixNodes: document.querySelectorAll('.sector-six').length,
        hasMoneyIn: txt.includes('钱在进'),
        hasMoneyOut: txt.includes('钱在出'),
        hasBadge: !!Array.from(document.querySelectorAll('button')).find((b) => (b.textContent || '').includes('情绪焦点')),
      };
    })()`);
    check('板块页无六格块', sec.sixNodes === 0, sec.sixNodes);
    check('板块页无「钱在进/钱在出」', !sec.hasMoneyIn && !sec.hasMoneyOut, sec);
    check('板块页无涨停集中徽标', sec.hasBadge === false, sec.hasBadge);
    await shot(cdp, 'sector_no_six.png', false);

    // ---------- 段 2：复盘卡六格视图（可指定 sixdate）
    const url = BASE + '/?card=review::review.main' + (SIXDATE ? ('&sixdate=' + encodeURIComponent(SIXDATE)) : '');
    await cdp.send('Page.navigate', { url });
    const reviewReady = await waitFor(cdp, "!!document.querySelector('[data-rvview=\"six\"]')", 30000);
    check('复盘卡视图条含「六格」', reviewReady);
    if (!reviewReady) return 1;
    check('切到六格视图', await cdp.eval(`(() => {
      const b = document.querySelector('[data-rvview="six"]');
      if (!b) return false; b.click(); return true;
    })()`));
    const sixReady = await waitFor(cdp, "!!document.querySelector('.rv-six .rv-six-card')", 20000);
    check('六格视图渲染', sixReady);
    if (!sixReady) {
      check('页面错误信息', false, await cdp.eval("(document.querySelector('.rv-err')||{}).textContent || ''"));
      return 1;
    }
    await sleep(600);

    const six = await cdp.eval(`(() => {
      const root = document.querySelector('.rv-six');
      const cards = Array.from(root.querySelectorAll('.rv-six-card'));
      const out = cards.map((c) => ({
        key: c.getAttribute('data-key'),
        title: (c.querySelector('.rv-six-hd b') || {}).textContent || '',
        no: (c.querySelector('.rv-six-no') || {}).textContent || '',
        chips: Array.from(c.querySelectorAll('.rv-six-chip')).map((x) => x.textContent.trim()),
        basis: (c.querySelector('.rv-six-basis') || {}).textContent || '',
        act: (c.querySelector('.rv-six-act') || {}).textContent || '',
        actPending: !!(c.querySelector('.rv-six-act.pending')),
        details: !!c.querySelector('details.rv-six-d'),
      }));
      const temp = root.querySelector('.rv-six-temp');
      return {
        head: (root.querySelector('.rv-six-head') || {}).textContent || '',
        cards: out,
        temp: temp ? temp.textContent : '',
        tempCells: temp ? temp.querySelectorAll('div').length : 0,
        pill: Array.from(document.querySelectorAll('[data-rvview]')).map((b) => b.textContent.trim()),
        topline: (document.querySelector('.rv-card-body .rv-topline') || {}).textContent || '',
      };
    })()`);
    console.log('  六格视图: ' + JSON.stringify(six, null, 1));

    check('视图条 = 图表/纸面/文档/六格', six.pill.join('|') === '图表|纸面|文档|六格', six.pill);
    check('六格取数日 = ' + sixDate, String(six.topline).includes(sixDate), six.topline.slice(0, 60));
    check('6 张卡片', six.cards.length === 6, six.cards.length);
    check('编号 01-06', six.cards.map((x) => x.no).join(',') === '01,02,03,04,05,06', six.cards.map((x) => x.no));
    check('标题按规格改口', six.cards[0].title === '重点配置 · 持续吸金'
      && six.cards[1].title === '潜伏观察 · 冷门流入'
      && six.cards[2].title === '趋势确认 · 强共振'
      && six.cards[3].title === '风险最高 · 共振流出'
      && six.cards[4].title === '分歧提示 · 杠杆背离'
      && six.cards[5].title === '情绪焦点 · 涨停集中', six.cards.map((x) => x.title));
    check('每卡依据非空', six.cards.every((x) => x.basis.trim().length > 6), six.cards.map((x) => x.basis.slice(0, 20)));
    check('每卡动作标待填', six.cards.every((x) => x.actPending), six.cards.map((x) => x.act));
    check('每卡可展开明细', six.cards.every((x) => x.details), six.cards.map((x) => x.details));

    // A：温度区位置关系
    check('温度条 6 项', six.tempCells === 6, six.tempCells);
    check('温度条含 AMV 与 MA10', /AMV/.test(six.temp) && /MA10/.test(six.temp), six.temp.slice(0, 80));
    check('温度条含位置关系', /(高于|低于) MA10/.test(six.temp), six.temp.slice(0, 80));
    if (pvs.position === 'below' && pt.state === '多') {
      check('温度条含「单轨未确认」', /单轨未确认/.test(six.temp), six.temp.slice(0, 80));
      check('headline 含「单轨未确认」', /单轨未确认/.test(six.head), six.head);
      check('headline 含「低于 MA10」', /低于 MA10/.test(six.head), six.head);
    }
    check('headline 含 AMV 与 MA10', /AMV/.test(six.head) && /MA10/.test(six.head), six.head);

    // C：杠杆背离
    if (!(p5.rows || []).length) {
      check('05 卡显示「今日无」', six.cards[4].chips.join('|').includes('今日无')
        && six.cards[4].basis.includes('今日无'), six.cards[4].chips);
    } else {
      check('05 卡有合格标的', six.cards[4].chips.length > 0, six.cards[4].chips);
    }

    // B：强共振 state=多 优先
    const c3chips = p3.chips || [];
    check('强共振首名 state=多', (c3chips[0] || {}).state === '多', c3chips[0]);
    const hasShort = c3chips.some((x) => x.state && x.state !== '多');
    check('空头板块标「未转多」', !hasShort || six.cards[2].chips.join('|').includes('未转多'), six.cards[2].chips);

    await shot(cdp, 'review_six_view_' + String(sixDate).replace(/-/g, '') + '.png', true);

    check('无 console 异常', cdp.consoleErrors.length === 0, cdp.consoleErrors.slice(0, 3));
    return failed ? 1 : 0;
  } finally {
    if (cdp) cdp.close();
    try { proc.kill(); } catch (e) { /* ignore */ }
  }
}

main().then((code) => { console.log(failed ? 'FAILED ' + failed : 'ALL OK'); process.exit(code); }).catch((e) => {
  console.log('ERROR ' + (e && e.stack ? e.stack : e));
  process.exit(2);
});
