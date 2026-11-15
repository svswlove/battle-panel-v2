// 情绪打分卡 · 真实浏览器核对（Chrome CDP，直连本地实例，只读 GET）
//   node app/plugins/emotion/tests/js/emotion_card.cdp.mjs [--base http://127.0.0.1:8810] [--shots <dir>]
// 断言：卡能挂载、大数字/档位/六因子/曲线/stage 都在、口径版本可见、无 console 异常；截图落 --shots。
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
const SHOT_DIR = arg('--shots', path.join(HERE, '_shots'));
// 不用 `?card=`：壳的初始卡恒取导航表第一张（仪表盘），`?card=` 只过滤清单不决定首卡。
// 走真实路径：打开首页 → 点侧栏「情绪打分」（顺带验证 NAV 接线）。
const CARD_URL = BASE + '/';
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

async function main() {
  const probe = await fetch(BASE + '/').then((r) => r.status).catch(() => 0);
  if (probe !== 200) { console.log('SKIP 实例未启动 ' + BASE); return 0; }
  const chrome = CHROME_CANDIDATES.find((p) => fs.existsSync(p));
  if (!chrome) { console.log('SKIP 无 Chrome/Edge'); return 0; }
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'emo-card-'));
  const port = 9477;
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

    await cdp.send('Page.navigate', { url: CARD_URL });
    const booted = await waitFor(cdp, "document.querySelectorAll('.rail-tab,.rail-btn').length > 4", 30000);
    check('壳启动', booted);
    if (!booted) return 1;
    const navClicked = await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '情绪打分');
      if (!b) return false;
      b.click();
      return true;
    })()`);
    check('点侧栏「情绪打分」', navClicked);
    const rendered = await waitFor(cdp, "!!document.querySelector('.emo-num')", 25000);
    check('大数字渲染', rendered);
    if (!rendered) {
      check('页面错误信息', false, await cdp.eval("(document.querySelector('.emo-msg')||{}).textContent || ''"));
      return 1;
    }
    await sleep(600);

    const shape = await cdp.eval(`(() => {
      const root = document.querySelector('.emo-root');
      const num = root.querySelector('.emo-num');
      const rows = root.querySelectorAll('.emo-tr');
      const chart = root.querySelector('svg.emo-chart polyline');
      const boxes = root.querySelectorAll('.emo-dual .emo-box');
      const warn = root.querySelector('.emo-warn');
      return {
        score: (num.textContent || '').trim(),
        band: (root.querySelector('.emo-hero .emo-band') || {}).textContent,
        factorRows: rows.length,
        factorLabels: Array.from(rows).map((r) => r.children[0].textContent),
        points: Array.from(rows).map((r) => r.children[3].textContent),
        participated: Array.from(rows).map((r) => r.children[4].textContent),
        polyPoints: chart ? chart.getAttribute('points').split(' ').length : 0,
        dualBoxes: boxes.length,
        stageText: boxes[0] ? boxes[0].textContent : '',
        scoreBoxText: boxes[1] ? boxes[1].textContent : '',
        warn: warn ? warn.textContent : null,
        caliberBar: (root.querySelector('.emo-bar') || {}).textContent || '',
        navLabel: (Array.from(document.querySelectorAll('.rail-tab,.rail-btn')).map((b) => b.textContent.trim()).filter((t) => t === '情绪打分')).length,
        scrollH: root.scrollHeight,
        clientH: root.clientHeight,
      };
    })()`);
    console.log('  卡内数据: ' + JSON.stringify(shape, null, 1));

    check('顶部有分数数字', /\d/.test(shape.score), shape.score);
    check('六因子六行', shape.factorRows === 6, shape.factorRows);
    check('六因子标签齐全', ['涨停家数', '晋级率', '炸板率', '跌停 vs 涨停', '最高板', '连板数']
      .every((t) => shape.factorLabels.includes(t)), shape.factorLabels);
    check('每行都有加减分', shape.points.length === 6 && shape.points.every((p) => /[+-]/.test(p) || p === '0.00'), shape.points);
    check('参与标记存在', shape.participated.length === 6, shape.participated);
    check('曲线点数 > 50', shape.polyPoints > 50, shape.polyPoints);
    check('底部两套口径并列', shape.dualBoxes === 2 && shape.stageText.includes('规则硬判') && shape.scoreBoxText.includes('连续打分'),
      { dualBoxes: shape.dualBoxes, stage: shape.stageText.slice(0, 40) });
    check('页面标注口径版本', /口径\s*v1\.1/.test(shape.caliberBar) && shape.caliberBar.includes('2026-10-29'), shape.caliberBar.slice(0, 120));
    check('不再有反向试算提示', shape.warn === null, shape.warn);
    check('侧栏有「情绪打分」', shape.navLabel === 1, shape.navLabel);
    check('无 console 异常', cdp.consoleErrors.length === 0, cdp.consoleErrors.slice(0, 3));

    const shot = path.join(SHOT_DIR, 'emotion_card_1600x1000.png');
    const full = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    fs.writeFileSync(shot, Buffer.from(full.data, 'base64'));
    console.log('  截图: ' + shot);

    // 1440 宽再截一张，确认窄屏不塌
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await sleep(800);
    const shot2 = path.join(SHOT_DIR, 'emotion_card_1440x900.png');
    const full2 = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    fs.writeFileSync(shot2, Buffer.from(full2.data, 'base64'));
    console.log('  截图: ' + shot2);

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
