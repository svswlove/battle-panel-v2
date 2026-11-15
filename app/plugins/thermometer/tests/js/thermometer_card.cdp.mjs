// 情绪温度计 · 真实浏览器核对（Chrome CDP，直连本地实例，只读 GET）
//   node app/plugins/thermometer/tests/js/thermometer_card.cdp.mjs [--base http://127.0.0.1:8810] [--shots <dir>]
// 断言：卡能挂载、八段都在、今日真实数字对得上、恐慌断点标注存在、胜率格子写「样本不足」、无 console 异常；
//       截图落 --shots（1440×900 / 1600×1000）。
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
// 默认用 10-29 收盘夹具回放 /api/panic（页面是收盘后日度工具；零点后该接口会按设计回落库内旧行，
// 直接跑"实时"就只能验证降级路径，验不了当日真实读数）。`--live` 则完全不拦截，跑真实接口。
const LIVE_ONLY = process.argv.includes('--live');
const REPLAY_FILE = LIVE_ONLY ? null : arg('--replay-panic', path.join(HERE, 'fixtures', 'panic_20261029.json'));
// 走真实路径：打开首页 → 点侧栏「情绪温度计」（顺带验证 NAV 接线）
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
  constructor(url) { this.url = url; this.id = 0; this.p = new Map(); this.consoleErrors = []; this.onPaused = null; }
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
        if (m.method === 'Fetch.requestPaused' && this.onPaused) this.onPaused(m.params);
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
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'thm-card-'));
  const port = 9483;
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
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

    // 回放夹具：只拦 /api/panic，其余接口仍走真实
    if (REPLAY_FILE) {
      if (!fs.existsSync(REPLAY_FILE)) {
        console.log('SKIP 夹具不存在 ' + REPLAY_FILE);
        return 0;
      }
      const body = fs.readFileSync(REPLAY_FILE, 'utf8');
      cdp.onPaused = async (p) => {
        const url = p.request && p.request.url ? p.request.url : '';
        if (url.indexOf('/api/panic') >= 0) {
          await cdp.send('Fetch.fulfillRequest', {
            requestId: p.requestId,
            responseCode: 200,
            responseHeaders: [
              { name: 'Content-Type', value: 'application/json; charset=utf-8' },
              { name: 'Cache-Control', value: 'no-store' },
            ],
            body: Buffer.from(body, 'utf8').toString('base64'),
          });
          return;
        }
        await cdp.send('Fetch.continueRequest', { requestId: p.requestId });
      };
      await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*api/panic*', requestStage: 'Request' }] });
      console.log('  回放模式：/api/panic ← ' + path.basename(REPLAY_FILE));
    } else {
      console.log('  实时模式：不拦截任何请求');
    }

    await cdp.send('Page.navigate', { url: CARD_URL });
    const booted = await waitFor(cdp, "document.querySelectorAll('.rail-tab,.rail-btn').length > 4", 30000);
    check('壳启动', booted);
    if (!booted) return 1;
    const navClicked = await cdp.eval(`(() => {
      const b = Array.from(document.querySelectorAll('button')).find((x) => x.textContent.trim() === '情绪温度计');
      if (!b) return false;
      b.click();
      return true;
    })()`);
    check('点侧栏「情绪温度计」', navClicked);
    const rendered = await waitFor(cdp, "!!document.querySelector('.thm-root .thm-kpi')", 30000);
    check('页面渲染', rendered);
    if (!rendered) {
      check('无错误信息', false, await cdp.eval("(document.querySelector('.thm-msg')||{}).textContent || ''"));
      return 1;
    }
    await sleep(800);

    const shape = await cdp.eval(`(() => {
      const root = document.querySelector('.thm-root');
      const txt = (sel) => { const n = root.querySelector(sel); return n ? n.textContent.trim() : null; };
      const all = (sel) => Array.from(root.querySelectorAll(sel));
      const kpis = all('.thm-k');
      const cells = all('.thm-c');
      const svg = root.querySelector('svg.thm-chart');
      const polylines = svg ? Array.from(svg.querySelectorAll('polyline')) : [];
      const texts = svg ? Array.from(svg.querySelectorAll('text')).map((t) => t.textContent) : [];
      const lines = svg ? Array.from(svg.querySelectorAll('line')) : [];
      const sigRows = Array.from(root.querySelectorAll('.thm-sig tbody tr')).map((tr) =>
        Array.from(tr.children).map((td) => td.textContent.trim()));
      return {
        kpiTitles: kpis.map((k) => k.children[0].textContent.trim()),
        kpiValues: kpis.map((k) => k.children[1].textContent.trim()),
        kpiDeltas: kpis.map((k) => k.children[2].textContent.trim()),
        band: txt('.thm-band'),
        pointer: txt('.thm-ptr-v'),
        say: txt('.thm-say'),
        cells: cells.length,
        cellTexts: cells.map((c) => c.children[0].textContent + '|' + (c.children[1] ? c.children[1].textContent : '')),
        dims: Array.from(root.querySelectorAll('.thm-dim')).map((d) => ({
          title: d.children[0] ? d.children[0].textContent.trim() : '',
          value: d.children[1] && d.children[1].children[0] ? d.children[1].children[0].textContent.trim() : '',
          band: d.children[1] && d.children[1].children[1] ? d.children[1].children[1].textContent.trim() : '',
          fill: d.children[2] && d.children[2].children[0] ? d.children[2].children[0].style.width : '',
          note: d.children[3] ? d.children[3].textContent.trim() : '',
          title3: d.children[3] ? (d.children[3].title || '') : '',
        })),
        dimsTitle: (() => { const s = Array.from(root.querySelectorAll('.thm-sec-h b')).map((b) => b.textContent); return s.indexOf('四维拆解'); })(),
        cmpRows: Array.from(root.querySelectorAll('.thm-tb tbody tr')).map((tr) => Array.from(tr.children).map((td) => td.textContent.trim())),
        polylineCount: polylines.length,
        dashedCount: polylines.filter((p) => p.getAttribute('stroke-dasharray')).length,
        polylinePoints: polylines.map((p) => p.getAttribute('points').split(' ').length),
        svgTexts: texts,
        vlines: lines.filter((l) => l.getAttribute('stroke-dasharray')).length,
        legend: txt('.thm-legend'),
        cap: txt('.thm-cap'),
        caps: all('.thm-cap').map((n) => n.textContent.trim()),
        alerts: all('.thm-a').map((a) => a.textContent.trim()),
        decide: txt('.thm-do-v'),
        decideWhy: txt('.thm-do-why'),
        rules: txt('.thm-rules'),
        sigRows: sigRows,
        foot: txt('.thm-foot'),
        bar: txt('.thm-bar'),
        scrollH: root.scrollHeight,
        clientH: document.querySelector('.stage') ? document.querySelector('.stage').clientHeight : 0,
        sections: Array.from(root.children).map((c) => (c.className || c.tagName) + ':' + Math.round(c.getBoundingClientRect().height)),
      };
    })()`);
    console.log('  页面数据: ' + JSON.stringify({
      kpi: shape.kpiValues, deltas: shape.kpiDeltas, band: shape.band, pointer: shape.pointer,
      cells: shape.cellTexts, decide: shape.decide, alerts: shape.alerts.length,
      sections: shape.sections,
      scrollH: shape.scrollH, clientH: shape.clientH,
    }, null, 1));

    // ① KPI 四格
    check('KPI 四格', shape.kpiTitles.length === 4, shape.kpiTitles);
    check('KPI 标题为我们自己的口径', ['情绪指数', '恐慌广度', '上涨 / 下跌家数', '成交额'].every((t, i) => (shape.kpiTitles[i] || '').indexOf(t) === 0), shape.kpiTitles);
    check('情绪指数 = 8.26（emotion）', /^8\.26/.test(shape.kpiValues[0] || ''), shape.kpiValues[0]);
    check('涨/跌家数 = 781 / 4728', /^781/.test(shape.kpiValues[2] || '') && /4728/.test(shape.kpiValues[2] || ''), shape.kpiValues[2]);
    check('四格都带前一日对比', shape.kpiDeltas.every((d) => /前日|接口失败|回落到/.test(d)), shape.kpiDeltas);

    if (REPLAY_FILE) {
      check('恐慌广度 = 23.1（/api/panic 回放）', /^23\.1/.test(shape.kpiValues[1] || ''), shape.kpiValues[1]);
      check('成交额 = 1.5565 万亿', /1\.5565\s*万亿/.test(shape.kpiValues[3] || ''), shape.kpiValues[3]);
      check('恐慌前日缺数据写「缺」而不是 0', /缺/.test(shape.kpiDeltas[1] || ''), shape.kpiDeltas[1]);
    } else {
      // 实时模式：要么就是当日读数 23.1，要么接口已回落旧行 → 必须显示缺 + 预警说明
      const fresh = /^23\.1/.test(shape.kpiValues[1] || '');
      check('恐慌侧要么当日值、要么标缺（不拿旧行充今日）',
        fresh || (/^缺/.test(shape.kpiValues[1] || '') && shape.alerts.some((a) => /回落到库内/.test(a))),
        { value: shape.kpiValues[1], delta: shape.kpiDeltas[1] });
    }

    // ② 温度计
    check('档位 = 冰点', shape.band === '冰点', shape.band);
    check('指针读数 = 8.26', /8\.26/.test(shape.pointer || ''), shape.pointer);
    check('状态说明含 距上一档', /距上一档/.test(shape.say || ''), (shape.say || '').slice(0, 80));

    // ③ 盘面格 10–14
    check('盘面格 10–14 个', shape.cells >= 10 && shape.cells <= 14, shape.cells);
    check('每格都有原始值 + 分位/样本说明', shape.cellTexts.every((t) => t.split('|')[1] && /分位|样本|缺/.test(t.split('|')[1])), shape.cellTexts);
    check('晋级率标样本不足', shape.cellTexts.some((t) => /晋级率/.test(t) && /样本不足/.test(t)), shape.cellTexts.find((t) => /晋级率/.test(t)));

    // ③b 四维拆解
    check('四维拆解段在盘面格之后、趋势图之前', shape.dimsTitle > 0 && shape.dims.length === 4, { idx: shape.dimsTitle, n: shape.dims.length });
    check('四维名称：广度 / 接力 / 资金 / 位置', ['广度', '接力', '资金', '位置'].every((t, i) => (shape.dims[i].title || '').indexOf(t) === 0), shape.dims.map((d) => d.title));
    check('四维分数不带百分号', shape.dims.every((d) => !/%/.test(d.value)), shape.dims.map((d) => d.value));
    check('每维都有进度条宽度', shape.dims.every((d) => /%$/.test(d.fill)), shape.dims.map((d) => d.fill));
    check('每维都有方向标签与一句说明', shape.dims.every((d) => d.band && d.note), shape.dims.map((d) => d.band + ' / ' + d.note.slice(0, 20)));
    check('四维分母写明参与项数', shape.dims.every((d) => /\d\/\d\s*项/.test(d.title)), shape.dims.map((d) => d.title));
    if (REPLAY_FILE) {
      const byKey = {};
      shape.dims.forEach((d) => { byKey[d.title.slice(0, 2)] = d; });
      check('四维：广度 = 8.1 极冷', /^8\.1/.test(byKey['广度'].value) && byKey['广度'].band === '极冷', byKey['广度']);
      check('四维：接力 = 22.4 极冷', /^22\.4/.test(byKey['接力'].value) && byKey['接力'].band === '极冷', byKey['接力']);
      check('四维：资金 = 偏冷/极冷（2/4 项参与，已无两融）', /^\d/.test(byKey['资金'].value) && /2\/4 项/.test(byKey['资金'].title), byKey['资金']);
      check('资金格带 5 日主力资金对比', /5日占成交/.test(byKey['资金'].note), byKey['资金'].note);
      check('四维：位置 = 4.2 极冷（880008 全A）', /^4\.2/.test(byKey['位置'].value) && byKey['位置'].band === '极冷' && /880008/.test(byKey['位置'].note), byKey['位置']);
      check('四维缺项不填 0', byKey['位置'].value !== '0' && byKey['资金'].value !== '0' && /4 项/.test(byKey['资金'].title), byKey['资金']);
    } else {
      check('四维至少有可见分数或标缺', shape.dims.every((d) => d.value === '缺' || /^\d/.test(d.value)), shape.dims.map((d) => d.value));
    }

    // ④ 对比表
    check('对比表 ≥8 行', shape.cmpRows.length >= 8, shape.cmpRows.length);
    check('对比表含恐慌广度一行且昨日标缺', shape.cmpRows.some((r) => r[0] === '恐慌广度' && r[2] === '缺'), shape.cmpRows.find((r) => r[0] === '恐慌广度'));

    // ⑤ 趋势图
    check('曲线分段绘制（情绪 + 恐慌至少 2 段）', shape.polylineCount >= 2, shape.polylineCount);
    check('历史恐慌段为虚线', shape.dashedCount >= 1, shape.dashedCount);
    check('断点竖线存在', shape.vlines >= 1, shape.vlines);
    check('断点文字标注', shape.svgTexts.some((t) => /口径切换 10-29/.test(t)), shape.svgTexts.slice(-6));
    check('图例写清方向（同向 / 低值=恐慌）', /同向/.test(shape.legend || '') && /低值=恐慌/.test(shape.legend || ''), shape.legend);
    check('图注写明不连线跨断点', shape.caps.some((t) => /不连线/.test(t)), shape.caps);

    // ⑥ 预警
    check('预警 ≥5 条', shape.alerts.length >= 5, shape.alerts.length);

    // ⑦ 该做什么
    if (REPLAY_FILE) {
      check('动作 = 等信号（冰点共振）', shape.decide === '等信号', shape.decide);
      check('动作带依据', /恐慌广度/.test(shape.decideWhy || ''), (shape.decideWhy || '').slice(0, 80));
    } else {
      // 实时模式：恐慌侧可能已回落旧行 → 判定退化为「不加仓」（只用情绪指数，更保守）
      check('动作只有三态之一', ['等信号', '不加仓', '做结构'].indexOf(shape.decide) >= 0, shape.decide);
      check('动作带依据', /情绪指数|恐慌广度/.test(shape.decideWhy || ''), (shape.decideWhy || '').slice(0, 80));
    }
    check('判定规则写在页面上', /不加仓/.test(shape.rules || '') && /做结构/.test(shape.rules || ''), (shape.rules || '').slice(0, 60));

    // ⑧ 信号→动作表
    check('信号表 5 行 4 列', shape.sigRows.length === 5 && shape.sigRows.every((r) => r.length === 4), shape.sigRows.map((r) => r.length));
    check('胜率/次数列写样本不足', shape.sigRows.every((r) => /样本不足（\d+ 日）/.test(r[3])), shape.sigRows.map((r) => r[3]));
    check('未编胜率/次数数字', shape.sigRows.every((r) => !/\d+%/.test(r[3])), shape.sigRows.map((r) => r[3]));
    check('跌停>涨停 今日触发', shape.sigRows.some((r) => /跌停 > 涨停/.test(r[0]) && r[1] === '触发'), shape.sigRows.map((r) => r[0] + ':' + r[1]));

    // 页脚来源
    check('页脚写清每个数字的来源接口',
      /emotion\/payload/.test(shape.foot || '') && /api\/panic/.test(shape.foot || '') && /thermometer\/meta/.test(shape.foot || ''),
      (shape.foot || '').slice(0, 120));
    check('顶栏标口径版本', /口径 v1\.1/.test(shape.bar || ''), (shape.bar || '').slice(0, 80));
    check('无 console 异常', cdp.consoleErrors.length === 0, cdp.consoleErrors.slice(0, 3));

    console.log('  整页高度 / 可视高度: ' + shape.scrollH + ' / ' + shape.clientH);

    for (const [w, h, name] of [[1440, 900, 'thermometer_1440x900.png'], [1600, 1000, 'thermometer_1600x1000.png']]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      await sleep(700);
      const fit = await cdp.eval(`(() => {
        const root = document.querySelector('.thm-root');
        const stage = document.querySelector('.stage');
        const cs = stage ? getComputedStyle(stage) : null;
        const padT = cs ? parseFloat(cs.paddingTop) || 0 : 0;
        const padB = cs ? parseFloat(cs.paddingBottom) || 0 : 0;
        return { w: window.innerWidth, h: window.innerHeight, scrollH: root.scrollHeight,
                 avail: stage ? Math.round(stage.clientHeight - padT - padB) : 0 };
      })()`);
      console.log('  ' + w + '×' + h + ' 内容高/可用高: ' + fit.scrollH + ' / ' + fit.avail +
        (fit.scrollH <= fit.avail ? ' （一屏装下）' : ' （超 ' + (fit.scrollH - fit.avail) + 'px）'));
      check(w + '×' + h + ' 一屏装下', fit.scrollH <= fit.avail, fit);
      const full = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
      const shot = path.join(SHOT_DIR, name);
      fs.writeFileSync(shot, Buffer.from(full.data, 'base64'));
      console.log('  截图: ' + shot);
    }

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
