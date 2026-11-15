// selection 卡 · 真实浏览器首屏性能实测（headless Chrome + CDP，无第三方依赖）
//
// 为什么用 CDP 而不是 Puppeteer：本机没装 puppeteer，但 Node 22+ 自带全局 WebSocket，
// 直接说 DevTools 协议即可拿到真实的导航时序与 paint 标记，不需要装包。
//
// 跑法：
//   node app/plugins/selection/tests/perf_headless.mjs
// 输出：一行 JSON（首屏各阶段耗时 / DOM 行数 / 内存量级），退出码 0=达标（首屏 < 2000ms）。
//
// 纪律：只读页面，不点写操作（写操作另由 harness_virtualscroll.mjs 覆盖并复原生产态）。

import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.env.PANEL_BASE || 'http://127.0.0.1:8810';
const CARD = 'selection.main';
const PORT = Number(process.env.CDP_PORT || 9333);
const BUDGET_MS = Number(process.env.BUDGET_MS || 2000);

const CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
];
const exe = CANDIDATES.find((p) => existsSync(p));
if (!exe) {
  console.log(JSON.stringify({ ok: false, error: '未找到 Chrome/Edge', candidates: CANDIDATES }));
  process.exit(2);
}

const profile = mkdtempSync(join(tmpdir(), 'selperf-'));
const args = [
  '--headless=new',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  '--disable-background-networking',
  `--user-data-dir=${profile}`,
  `--remote-debugging-port=${PORT}`,
  '--window-size=1440,900',
  'about:blank',
];
const child = spawn(exe, args, { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForEndpoint() {
  for (let i = 0; i < 100; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (res.ok) return await res.json();
    } catch { /* 还没起来 */ }
    await sleep(120);
  }
  throw new Error('CDP 端点 10s 未就绪');
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let seq = 0;
  const pending = new Map();
  const events = [];
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', () => res());
    ws.addEventListener('error', (e) => rej(new Error('ws error ' + (e && e.message))));
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
      return;
    }
    events.push(msg);
  });
  const send = async (method, params) => {
    await ready;
    const id = ++seq;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  };
  return { send, events, close: () => ws.close(), ready };
}

const out = { ok: false, browser: exe.split('/').pop(), url: `${BASE}/v2/?card=${CARD}` };
try {
  await waitForEndpoint();
  const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
  const c = cdp(target.webSocketDebuggerUrl);
  await c.ready;
  await c.send('Page.enable');
  await c.send('Runtime.enable');
  await c.send('Network.enable');
  await c.send('Network.setCacheDisabled', { cacheDisabled: true });
  await c.send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

  // 网络层计量：/api/selection 的传输字节与耗时（证明 8.2MB 真的过网了）
  const net = { bytes: 0, ms: null };
  const t0 = Date.now();
  const nav = await c.send('Page.navigate', { url: out.url });
  out.navError = nav.errorText || null;

  // 轮询等 data-paint-ms 出现（卡片自报的首屏耗时）
  let paint = null;
  let rows = null;
  let domRows = null;
  for (let i = 0; i < 200; i++) {
    const r = await c.send('Runtime.evaluate', {
      expression: `(() => {
        const host = document.querySelector('.card-host');
        if (!host) return null;
        const v = host.getAttribute('data-paint-ms');
        return v ? { paint: Number(v), rows: Number(host.getAttribute('data-rows')),
                      domRows: host.querySelectorAll('.sel-r').length,
                      err: !!document.querySelector('.card.is-error') } : null;
      })()`,
      returnByValue: true,
    });
    const v = r.result && r.result.value;
    if (v) { paint = v.paint; rows = v.rows; domRows = v.domRows; out.cardError = v.err; break; }
    await sleep(50);
  }
  out.navToPaintMs = Date.now() - t0;
  out.paintMs = paint;
  out.cardRows = rows;

  // 传输计量（Network.responseReceived + loadingFinished 后读 encodedDataLength）
  // 必须精确比 pathname：/api/selection 是 /api/selection_prefs 等路径的前缀
  const selEvents = c.events.filter((e) => {
    if (e.method !== 'Network.responseReceived') return false;
    const u = String(((e.params || {}).response || {}).url || '');
    try { return new URL(u).pathname === '/api/selection'; } catch { return false; }
  });
  if (selEvents.length) {
    const reqId = selEvents[0].params.requestId;
    const fin = c.events.find((e) => e.method === 'Network.loadingFinished'
      && e.params.requestId === reqId);
    if (fin) net.bytes = fin.params.encodedDataLength || 0;
    const resp = selEvents[0].params.response;
    if (resp && resp.timing) net.ttfbMs = Math.round(resp.timing.receiveHeadersEnd || 0);
    if (resp && resp.headers && resp.headers['content-length']) {
      net.contentLength = Number(resp.headers['content-length']);
    }
  }

  // 真实视口几何（jsdom 量不到，这里才有）
  const geo = await c.send('Runtime.evaluate', {
    expression: `(() => {
      const vp = document.querySelector('.sel-vp');
      const host = document.querySelector('.card-host');
      return { vpH: vp ? vp.clientHeight : 0,
               vpScrollH: vp ? vp.scrollHeight : 0,
               domRows: host ? host.querySelectorAll('.sel-r').length : 0,
               stat: (document.querySelector('.sel-stat') || {}).textContent || '',
               heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null };
    })()`,
    returnByValue: true,
  });
  const g = geo.result.value || {};
  out.vp = { clientHeight: g.vpH, scrollHeight: g.vpScrollH, domRows: g.domRows, heapMB: g.heap };
  out.stat = g.stat;

  // 切到最大分组（砖形图 2824 行）再量一次切换耗时
  const sw = await c.send('Runtime.evaluate', {
    expression: `(async () => {
      const t = Array.from(document.querySelectorAll('.sel-tab')).find(b => (b.textContent||'').startsWith('砖形图'));
      if (!t) return null;
      const s = performance.now();
      t.click();
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const host = document.querySelector('.card-host');
      return { ms: Math.round(performance.now() - s), domRows: host.querySelectorAll('.sel-r').length,
               stat: (document.querySelector('.sel-stat') || {}).textContent || '' };
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  out.switchBigGroup = sw.result.value;

  // 切到「动能」分组（当前生产偏好下可见行最多的组之一，> 视口高），再滚动 20 帧
  const sc = await c.send('Runtime.evaluate', {
    expression: `(async () => {
      const tab = Array.from(document.querySelectorAll('.sel-tab')).find(b => (b.textContent||'').startsWith('动能'));
      if (tab) tab.click();
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const vp = document.querySelector('.sel-vp');
      if (!vp) return null;
      const host = document.querySelector('.card-host');
      const before = host.querySelectorAll('.sel-r').length;
      const s = performance.now();
      for (let i = 1; i <= 20; i++) {
        vp.scrollTop = i * 40;
        await new Promise(r => requestAnimationFrame(r));
      }
      await new Promise(r => requestAnimationFrame(r));
      const tops = Array.from(host.querySelectorAll('.sel-r')).map(e => parseInt(e.style.top, 10));
      return { ms: Math.round(performance.now() - s), domRowsBefore: before,
               domRows: host.querySelectorAll('.sel-r').length,
               minTop: tops.length ? Math.min.apply(null, tops) : null,
               scrollTop: vp.scrollTop, scrollHeight: vp.scrollHeight, clientHeight: vp.clientHeight,
               stat: (document.querySelector('.sel-stat') || {}).textContent || '' };
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  out.scroll = sc.result.value;

  out.checks = {
    '首屏 paint 标记存在': paint != null,
    '首屏 < 2000ms': paint != null && paint < BUDGET_MS,
    'DOM 行数受控（<=60）': domRows != null && domRows > 0 && domRows <= 60,
    '最大分组 2824 行仍只渲染可视行': out.switchBigGroup ? out.switchBigGroup.domRows <= 60 : false,
    '切最大分组 < 500ms': out.switchBigGroup ? out.switchBigGroup.ms < 500 : false,
    '滚动 20 帧后 DOM 行数受控': out.scroll ? out.scroll.domRows <= 60 : false,
    '滚动确实位移了（minTop>0）': out.scroll ? (out.scroll.minTop || 0) > 0 : false,
    '无错误卡': !out.cardError,
    '8.2MB 载荷真实过网': net.bytes > 8_000_000,
  };
  out.net = net;

  // 可选：截图存盘（SHOT=<绝对路径>），用于人工目视校验布局（默认不写盘）
  if (process.env.SHOT) {
    const shot = await c.send('Page.captureScreenshot', { format: 'png' });
    const { writeFileSync } = await import('node:fs');
    writeFileSync(process.env.SHOT, Buffer.from(shot.data, 'base64'));
    out.shot = process.env.SHOT;
  }

  out.ok = Object.values(out.checks).every(Boolean);
  c.close();
} catch (err) {
  out.error = String(err && err.stack ? err.stack : err);
} finally {
  try { child.kill(); } catch { /* 已退出 */ }
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* 占用中忽略 */ }
}

console.log(JSON.stringify(out, null, 1));
process.exit(out.ok ? 0 : 1);
