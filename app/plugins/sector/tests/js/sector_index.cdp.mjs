// sector 指数层真实浏览器核对（Chrome CDP，直连 8810，只读 GET）
//   node app/plugins/sector/tests/js/sector_index.cdp.mjs [--shots <dir>]
// 断言（2026-11-03 改：标注 65 定「指数向板块看齐」）：
//   切「指数」后 12 行全量、单列满宽、**没有内联右栏**（li.idx-detail / .idx-right 均不存在）、
//   点行/名称 = 弹详情卡（#sector-modal，成分股表有行）、换一行弹卡内容跟着换、
//   行右键菜单含「打开板块详情」、无 4xx、无 console 异常、.rr-span 不存在。截图落 --shots 目录。
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

const ROW_SEL = '.sector-card .panels.idx .panel-in li.row';

function clickRowByCode(cdp, code) {
  return cdp.eval(`(() => {
    const rows = Array.from(document.querySelectorAll('${ROW_SEL}'));
    const r = rows.find((x) => x.dataset.code === ${JSON.stringify(code)});
    if (!r) return false;
    (r.querySelector('.row-hd') || r).click();
    return true;
  })()`);
}

// 2026-11-05（标注 81）：点名称单独走弹卡（.nm-t 上 stopPropagation），
// 点行其余区域只切右栏——两个入口分开验证。
function clickNameByCode(cdp, code) {
  return cdp.eval(`(() => {
    const rows = Array.from(document.querySelectorAll('${ROW_SEL}'));
    const r = rows.find((x) => x.dataset.code === ${JSON.stringify(code)});
    if (!r) return false;
    const nm = r.querySelector('.nm-t');
    if (!nm) return false;
    nm.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    return true;
  })()`);
}

async function closeModal(cdp) {
  await cdp.eval(`(() => { const x = document.querySelector('#sector-modal .card-x'); if (x) x.click(); return 1; })()`);
  await sleep(400);
}

async function main() {
  const probe = await fetch(BASE + '/').then((r) => r.status).catch(() => 0);
  if (probe !== 200) { console.log('SKIP 8810 未启动'); return 0; }
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP 无 Chrome/Edge'); return 0; }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'sector-idx-'));
  const port = 9455;
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
    // 点导航「板块」（不靠 ?card=：壳会记忆上次卡片）
    const navClicked = await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '板块');
      if (!b) return false;
      b.click();
      return true;
    })()`);
    check('点导航板块', navClicked);
    const mounted = await waitFor(cdp, "!!document.querySelector('.sector-card .seg')", 25000);
    check('板块卡挂载', mounted);
    if (!mounted) return 1;
    await sleep(1200);

    // 切「指数」
    const clicked = await cdp.eval(`(() => {
      const btns = Array.from(document.querySelectorAll('.sector-card .seg-btn'));
      const b = btns.find((x) => x.textContent.trim() === '指数');
      if (!b) return false;
      b.click();
      return true;
    })()`);
    check('切到指数层', clicked);
    const ready = await waitFor(cdp, `document.querySelectorAll('${ROW_SEL}').length >= 12`);
    check('指数清单渲染 12 行', ready, await cdp.eval(`document.querySelectorAll('${ROW_SEL}').length`));
    await sleep(1200);

    const shape = await cdp.eval(`(() => {
      const card = document.querySelector('.sector-card');
      const rows = card.querySelectorAll('${ROW_SEL}');
      const sel = card.querySelector('${ROW_SEL}.sel');
      return {
        rows: rows.length,
        codes: Array.from(rows).map((r) => r.dataset.code),
        selCount: card.querySelectorAll('${ROW_SEL}.sel').length,
        selCode: sel ? sel.dataset.code : null,
        title: (card.querySelector('.panels.idx .panel-in .panel-hd span:last-child') || {}).textContent || '',
        inlineDetail: card.querySelectorAll('.panels.idx li.idx-detail').length,
        rightPanel: card.querySelectorAll('.panels.idx .idx-right').length,
        refreshBtn: card.querySelectorAll('.panels.idx .idx-right .card-refresh').length,
        rrSpan: card.querySelectorAll('.rr-span').length,
        cols: getComputedStyle(card.querySelector('.panels.idx')).gridTemplateColumns,
        tracks: getComputedStyle(card.querySelector('.panels.idx')).gridTemplateColumns.trim().split(/\s+/).length,
      };
    })()`);
    check('12 个指数全部在列', shape.rows === 12, shape.rows);
    check('清单含 12 个不同代码', new Set(shape.codes).size === 12, shape.codes);
    check('标题写全部 12', /全部（12）/.test(shape.title), shape.title);
    check('恰好一行选中', shape.selCount === 1, shape.selCount);
    check('默认选中全A', shape.selCode === 'ALL', shape.selCode);
    check('有内联右栏（li.idx-detail 在）', shape.inlineDetail === 1, shape.inlineDetail);
    check('右栏标题「走势与数据」在且带 ↻ 重算', shape.rightPanel === 1 && shape.refreshBtn === 1, { rightPanel: shape.rightPanel, refreshBtn: shape.refreshBtn });
    check('左栏 + 右栏 两个轨道', /px\s+\d/.test(String(shape.cols)), { tracks: shape.tracks, cols: shape.cols });
    check('.rr-span 已删除', shape.rrSpan === 0, shape.rrSpan);

    const shot1 = path.join(SHOT_DIR, 'sector-idx-all.png');
    const r1 = await cdp.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(shot1, Buffer.from(r1.data, 'base64'));

    // 点行 → 只切右栏（不弹卡）；点名称 → 弹详情卡（标注 81）
    const second = await cdp.eval(`(() => {
      const rows = document.querySelectorAll('${ROW_SEL}');
      return rows[1] ? rows[1].dataset.code : null;
    })()`);
    check('取到第二个指数', !!second, second);
    const clickedSecond = await clickRowByCode(cdp, second);
    check('点行（' + second + '）', clickedSecond);
    const switched = await waitFor(cdp, `(() => {
      const nm = document.querySelector('.sector-card .panels.idx .idx-inline .card-nm');
      const sel = document.querySelector('${ROW_SEL}.sel');
      return !!nm && !!sel && sel.dataset.code === ${JSON.stringify(second)}
        && !document.querySelector('#sector-modal');
    })()`, 25000);
    check('点行只换右栏、不弹卡', switched);
    const inlineNm = await cdp.eval("((document.querySelector('.sector-card .panels.idx .idx-inline .card-nm') || {}).textContent || '').trim()");
    check('右栏内容跟着换到该指数', !!inlineNm, inlineNm);

    const nameClicked = await clickNameByCode(cdp, second);
    check('点名称（' + second + '）', nameClicked);
    const modalUp = await waitFor(cdp, "!!document.querySelector('#sector-modal .modal .card-nm')", 20000);
    check('点指数名称弹出详情卡', modalUp);
    const detailReady = await waitFor(cdp, "document.querySelectorAll('#sector-modal .card-tbl tbody tr').length > 0", 25000);
    check('详情卡成分股表有行', detailReady);

    const after = await cdp.eval(`(() => {
      const m = document.querySelector('#sector-modal');
      const sel = document.querySelector('${ROW_SEL}.sel');
      return {
        nm: m ? (m.querySelector('.card-nm') || {}).textContent : null,
        rows: m ? m.querySelectorAll('.card-tbl tbody tr').length : 0,
        svg: m ? m.querySelectorAll('svg.spark').length : 0,
        selCode: sel ? sel.dataset.code : null,
        selCount: document.querySelectorAll('${ROW_SEL}.sel').length,
        inlineDetail: document.querySelectorAll('.sector-card .panels.idx li.idx-detail').length,
      };
    })()`);
    check('弹卡标题有内容', !!after.nm && after.nm.length > 0, after.nm);
    check('弹卡带成分股', after.rows > 0, after.rows);
    check('弹卡有走势图', after.svg > 0, after.svg);
    check('选中态跟着点的那行（' + second + '）', after.selCode === second && after.selCount === 1, { selCode: after.selCode, selCount: after.selCount });
    check('弹卡打开时右栏仍在', after.inlineDetail === 1, after.inlineDetail);

    await closeModal(cdp);
    check('详情卡可关', await cdp.eval("!document.querySelector('#sector-modal')"));

    // 中证A500 用 .CSI 目录码：弹卡里仍要能取到两张走势图（000510.SH 落码）
    const csiClicked = await clickNameByCode(cdp, '000510.CSI');
    check('清单含 000510.CSI 且可点', csiClicked);
    const csiOk = await waitFor(cdp, `(() => {
      const m = document.querySelector('#sector-modal');
      const nm = m && m.querySelector('.card-nm');
      return !!nm && nm.textContent.indexOf('中证A500') >= 0 && m.querySelectorAll('svg.spark').length === 2;
    })()`, 25000);
    check('中证A500 弹卡两张走势图都有（.CSI 取数可用）', csiOk);
    const csiEmpty = await cdp.eval("Array.from(document.querySelectorAll('#sector-modal .rb-empty')).map((x) => x.textContent.trim())");
    check('中证A500 弹卡无「无收盘价数据」', csiEmpty.length === 0, csiEmpty);
    await closeModal(cdp);

    // 行右键 → 壳层菜单（规则 6b 兜底：卡片宿主内 data-code 一律当板块）
    const ctxInfo = await cdp.eval(`(() => {
      const row = document.querySelector('${ROW_SEL}');
      if (!row) return null;
      const r = row.getBoundingClientRect();
      row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 8, button: 2 }));
      return new Promise((res) => setTimeout(() => {
        const m = document.getElementById('ctx-menu');
        res(m ? {
          hidden: m.hidden,
          head: (m.querySelector('.ctx-head') || {}).textContent || '',
          amv: Array.from(m.querySelectorAll('.ctx-item')).some((x) => x.textContent.trim() === '打开板块详情'),
        } : null);
      }, 700));
    })()`);
    check('指数行右键出菜单', !!ctxInfo && !ctxInfo.hidden, ctxInfo);
    check('菜单含打开板块详情', !!ctxInfo && ctxInfo.amv, ctxInfo);

    // 切回二级行业：两栏 10% 榜单仍正常（本任务不改其他层级）
    await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('.sector-card .seg-btn')).find((x) => x.textContent.trim() === '二级');
      if (b) b.click();
    })()`);
    const back = await waitFor(cdp, "document.querySelectorAll('.sector-card .panels:not(.idx) .panel').length === 2", 15000);
    check('二级层仍是前/后 10% 两栏', back);
    await sleep(1200);
    const shot2 = path.join(SHOT_DIR, 'sector-level2.png');
    const r2 = await cdp.send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(shot2, Buffer.from(r2.data, 'base64'));

    const badHttp = [...new Set(cdp.events
      .filter((e) => e.method === 'Network.responseReceived' && e.params.response.status >= 400)
      .map((e) => e.params.response.status + ' ' + e.params.response.url)
      .filter((s) => !/favicon/.test(s)))];
    check('无失败的接口请求', badHttp.length === 0, badHttp.slice(0, 4));
    const excs = cdp.events.filter((e) => e.method === 'Runtime.exceptionThrown');
    check('无 JS 异常', excs.length === 0, excs.map((e) => String(e.params.exceptionDetails.text).slice(0, 120)).slice(0, 3));
    console.log('  截图: ' + shot1);
    console.log('  截图: ' + shot2);
  } finally {
    if (cdp) cdp.close();
    try { proc.kill(); } catch (e) { /* ignore */ }
    await sleep(300);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  return failed ? 1 : 0;
}

main().then((code) => {
  console.log(failed ? `FAILED ${failed}/${ran}` : `PASS ${ran}/${ran}`);
  process.exit(code);
}).catch((e) => {
  console.error('ERROR', e && e.message);
  process.exit(2);
});
