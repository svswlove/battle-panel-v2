// 恐慌广度图 · 涨停家数（2026-11-03 改）核对（Chrome CDP，直连 8810，只读 GET）
//   node app/plugins/intraday/tests/js/panic_limit_up.cdp.mjs [--shots <dir>]
// 背景：10-31 标注 63「没有涨停数据」→ 加过涨停分位折线；
//   11-01 标注 66「涨停的折线不用画进来，太乱了」→ **折线整条移除**，只保留浮层读数里的涨停家数。
// 断言：图例/aria-label 都不再提涨停线、SVG 没有 --warn 折线与涨停圆点、跌停/恐慌线仍在、
//       光标浮层仍能读到涨停家数（非「—」）、光标签只有 3 个点、无 console 异常。
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
const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => { let b = ''; r.on('data', (c) => { b += c; }); r.on('end', () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); }).on('error', rej);
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
  close() { try { this.ws.close(); } catch (e) { /* ignore */ } }
}

let failed = 0, ran = 0;
function check(name, cond, detail) {
  ran++;
  if (cond) { console.log('  ok   ' + name); return; }
  failed++;
  console.log('  FAIL ' + name + (detail === undefined ? '' : ' -> ' + JSON.stringify(detail)));
}

async function waitFor(cdp, expr, ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await cdp.eval(expr)) return true;
    await sleep(250);
  }
  return false;
}

async function main() {
  const probe = await fetch(BASE + '/').then((r) => r.status).catch(() => 0);
  if (probe !== 200) { console.log('SKIP 8810 未启动'); return 0; }
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP 无 Chrome/Edge'); return 0; }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'panic-lu-'));
  const port = 9466;
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
    await cdp.send('Network.enable');
    await cdp.send('Log.enable');

    await cdp.send('Page.navigate', { url: BASE + '/' });
    const booted = await waitFor(cdp, "document.querySelectorAll('.rail-btn,.rail-tab').length > 4", 25000);
    check('壳启动', booted);
    const navClicked = await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '盘中');
      if (!b) return false;
      b.click();
      return true;
    })()`);
    check('点导航盘中', navClicked);
    const mounted = await waitFor(cdp, "!!document.querySelector('svg.it-pchart')", 30000);
    check('恐慌图挂载', mounted);
    if (!mounted) return 1;
    await sleep(1500);

    const shape = await cdp.eval(`(() => {
      const svg = document.querySelector('svg.it-pchart');
      const legend = document.querySelector('.it-pchart-legend');
      const paths = Array.from(svg.querySelectorAll('path'));
      const circles = Array.from(svg.querySelectorAll('circle'));
      return {
        aria: svg.getAttribute('aria-label') || '',
        legend: legend ? legend.textContent : '',
        strokes: paths.map((p) => p.getAttribute('stroke')).filter(Boolean),
        warnPath: paths.filter((p) => p.getAttribute('stroke') === 'var(--warn)').length,
        panicPath: paths.filter((p) => p.getAttribute('stroke') === 'var(--dim)').length,
        ldcPath: paths.filter((p) => p.getAttribute('stroke') === 'var(--accent)').length,
        warnDot: circles.filter((c) => c.getAttribute('fill') === 'var(--warn)').length,
        pathCount: paths.length,
      };
    })()`);
    check('图例不再有涨停家数分位', !/涨停家数分位/.test(shape.legend), shape.legend.slice(0, 160));
    check('图例写「两线同向」', /两线同向/.test(shape.legend), shape.legend.slice(0, 160));
    check('aria-label 不再提涨停线', !/涨停/.test(shape.aria), shape.aria);
    check('没有涨停折线（--warn）', shape.warnPath === 0, shape.strokes);
    check('跌停线（--accent）仍在', shape.ldcPath >= 1, shape.strokes);
    check('恐慌主线（--dim）仍在', shape.panicPath >= 1, shape.strokes);
    check('没有涨停端点圆点', shape.warnDot === 0, shape.warnDot);
    check('path 总数 ≥4（全A 面积/线 + 两条线）', shape.pathCount >= 4, shape.pathCount);

    // 光标：移到图中间，浮层应读出一行涨停家数（历史日也应有值，不再是「—」）
    const tip = await cdp.eval(`(async () => {
      const svg = document.querySelector('svg.it-pchart');
      const box = svg.closest('.it-pc-wrap');
      const r = svg.getBoundingClientRect();
      const x = r.left + r.width * 0.5;
      const y = r.top + r.height * 0.5;
      svg.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y, bubbles: true }));
      await new Promise((res) => setTimeout(res, 150));
      const t = box.querySelector('.it-pc-tip');
      const cur = svg.querySelector('.it-pc-cur');
      return {
        hidden: !t || t.hidden,
        text: t ? t.textContent : '',
        dots: cur ? Array.from(cur.querySelectorAll('circle')).map((c) => c.style.display === 'none' ? 'off' : 'on') : [],
      };
    })()`);
    check('光标浮层显示', !tip.hidden, tip);
    check('浮层仍保留涨停家数文字', /涨停/.test(tip.text), tip.text);
    check('浮层涨停不是「—」', !/涨停\s*—/.test(tip.text), tip.text);
    check('光标签只有 3 个点（恐慌/跌停/全A）', tip.dots.length === 3, tip.dots);

    const shot = path.join(SHOT_DIR, 'panic_limit_up.png');
    const img = await cdp.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(shot, Buffer.from(img.data, 'base64'));
    console.log('  shot ' + shot);

    // 只查 JS 异常 + 非 favicon 的网络 error：壳没挂 favicon，404 是噪声不是回归
    const errs = cdp.events.filter((e) => e.method === 'Runtime.exceptionThrown'
      || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error'
        && !/favicon\.ico/.test(e.params.entry.url || '')));
    check('无 console 异常', errs.length === 0, errs.slice(0, 2).map((e) => JSON.stringify(e).slice(0, 200)));
  } finally {
    if (cdp) cdp.close();
    try { proc.kill(); } catch (e) { /* ignore */ }
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  console.log(`\n${ran - failed}/${ran} 通过`);
  return failed ? 1 : 0;
}

main().then((c) => process.exit(c)).catch((e) => { console.error(e); process.exit(1); });
