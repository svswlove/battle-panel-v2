/**
 * 审计明细 / 会话行 / 成交额对比 · 真 DOM 运行器（jsdom；给 tests/test_audit.py 调用）
 *
 * 守三件事，静态检查看不出来的：
 *   ① **details 默认折叠**（首绘无 open 属性 = 浏览器收起态），点 summary 后能展开；
 *   ② 展开态在重绘后**保留**（卡片 20s 一刷，旧页每 3s 整页重建会把用户展开的明细弹回去）；
 *   ③ 整卡挂载（真 frontend.js + 真载荷）：新块在 DOM 里的**相对顺序**与旧页 4704-4731 一致，
 *      信号流水 49 行 × 8 列真生成、代码列带 data-open-kline 占位、unmount 后不再响应。
 *
 * 输出：最后一行 JSON（pytest 取最后一行）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../..');
const REPO = path.resolve(PANEL, '../..');
const JSDOM_API = path.join(REPO, 'node_modules', 'jsdom', 'lib', 'api.js');

const jsdomMod = await import(new URL('file:///' + JSDOM_API.replace(/\\/g, '/')));
// jsdom 的 CJS 入口在 ESM 下会落到 default 上；两种形态都兜住（research_dom.mjs 同款处理）
const JSDOM = jsdomMod.JSDOM || (jsdomMod.default && jsdomMod.default.JSDOM);

const A = await import(new URL('../frontend/audit.js', import.meta.url).href);
const FIXTURE = path.join(HERE, 'fixtures/intraday_payload.json');
const payload = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));

function mountDom() {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="host"></div></body></html>',
    { pretendToBeVisual: true });
  return dom;
}

export async function run() {
  const out = {};

  // ---------- ① 首绘折叠 / 展开 / 重绘保留 ----------
  {
    const dom = mountDom();
    const doc = dom.window.document;
    const card = doc.createElement('div');
    card.className = 'it-card';
    const body = doc.createElement('div');
    card.append(body);
    doc.getElementById('host').append(card);

    const st = A.createState(card, body);
    A.bind(st);                       // 首绘（无数据）
    const det0 = body.querySelector('details.fold');
    out.firstPaint = {
      hasDetails: !!det0,
      open: det0 ? det0.open : null,
      hasOpenAttr: det0 ? det0.hasAttribute('open') : null,
      summary: det0 && det0.querySelector('summary') ? det0.querySelector('summary').textContent : '',
      emptyTables: body.querySelectorAll('.it-audit-card').length,
    };

    A.paint(st, payload);             // 有数据
    const det1 = body.querySelector('details.fold');
    out.withData = {
      open: det1 ? det1.open : null,
      rows: body.querySelectorAll('.it-audit-card table tr').length,   // 1 表头 + 49 行
      tds: body.querySelectorAll('.it-audit-card td').length,          // 392
      kline: body.querySelectorAll('[data-open-kline]').length,        // 49 代码 + 49 名称
      emptyBlocks: body.querySelectorAll('p.muted').length,            // 4 张空表
      tables: body.querySelectorAll('.it-audit-card').length,
    };

    // 用户点开 summary（浏览器原生行为；jsdom 里直接改 open，等价于点了那一下）
    det1.open = true;

    // ① 内容没变的一拍（20s 定时刷新最常见的情况）：整块不碰 DOM，连节点都不换
    const same1 = body.querySelector('details.fold');
    A.paint(st, payload);
    out.sameContentRepaint = {
      open: body.querySelector('details.fold').open,
      sameNode: body.querySelector('details.fold') === same1,
    };

    // ② 内容真变了（新一轮信号进来）：必须重建，但用户展开态要还原回去
    const changed = JSON.parse(JSON.stringify(payload));
    changed.signals = changed.signals.slice(0, 3);
    A.paint(st, changed);
    const det2 = body.querySelector('details.fold');
    out.afterExpandRepaint = {
      open: det2 ? det2.open : null,
      replaced: det2 !== det1,
      rows: body.querySelectorAll('.it-audit-card table tr').length,
    };

    // ③ 再变回去：展开态仍要在（跨多次重建都不丢）
    A.paint(st, payload);
    out.afterSecondRepaint = { open: body.querySelector('details.fold').open };

    A.unbind(st);
    out.unbind = { alive: st.alive };
    dom.window.close();
  }

  // ---------- ② paint 的展开态是「读 DOM 再还原」，不需要外部同步 ----------
  {
    const dom = mountDom();
    const doc = dom.window.document;
    const card = doc.createElement('div');
    card.className = 'it-card';
    const body = doc.createElement('div');
    card.append(body);
    const st = A.createState(card, body);
    A.bind(st);
    A.paint(st, payload);
    body.querySelector('details.fold').open = true;
    A.paint(st, payload);             // 不手工改 st.open，靠 paint 自己读
    out.selfHealing = { open: body.querySelector('details.fold').open };
    A.unbind(st);
    dom.window.close();
  }

  // ---------- ③ 整卡挂载（真 frontend.js + 真载荷） ----------
  {
    const dom = mountDom();
    const doc = dom.window.document;
    globalThis.document = doc;
    globalThis.window = dom.window;
    globalThis.AbortController = dom.window.AbortController || AbortController;

    const calls = [];
    const api = {
      async get(p) {
        calls.push(p);
        if (p.indexOf('/api/intraday.json') === 0) return JSON.parse(JSON.stringify(payload));
        if (p.indexOf('/api/breadth_880.json') === 0) return { today: { amount: 1972854849536, date: '2026-10-23' }, prev: {} };
        if (p.indexOf('/api/amv_live.json') === 0) return { all: { amv: 1, amv_pct: 1 }, indices: [], sectors: {}, holdings: [] };
        if (p.indexOf('/api/panic') === 0) return { ok: false, error: 'stub' };
        if (p === '/summary') return { ok: false, reason: 'stub' };
        if (p.indexOf('/api/amv_research.json') === 0) return { ok: false, detail: 'stub' };
        if (p.indexOf('/api/sector_track.json') === 0) return { snapshots: [] };
        if (p.indexOf('/api/sector_spark.json') === 0) {
          // 10 个指数码各给一条可辨识的 path（验「先取数再渲」：DOM 里必须有 d，不许空）
          const out = {};
          const raw = String(p.split('codes=')[1] || '');
          decodeURIComponent(raw).split(',').filter(Boolean).forEach((c, i) => {
            out[c] = { amv: 'M 0.0,' + i + ' L 100.0,' + (i + 1), ma10: 'M 0.0,' + (i + 2) };
          });
          return out;
        }
        if (p.indexOf('/api/sector_intraday.json') === 0) return { codes: {} };
        return {};
      },
    };

    const mod = await import(new URL('../frontend.js?v=' + Date.now(), import.meta.url).href);
    const card = mod.default.cards[0];
    const root = doc.getElementById('host');
    await card.mount(root, { api, card: { id: 'intraday.main', args: null }, plugin: { id: 'intraday' } });

    const wrap = root.querySelector('.it-card');
    const kids = Array.from(wrap.children).map((n) => n.className);
    const sessEl = wrap.querySelector('.sess-line');
    const turnover = wrap.querySelector('.it-turnover-host');
    const auditHost = wrap.querySelector('.it-audit-host');
    const det = wrap.querySelector('details.fold');
    // 块序：按「节点里有没有这个子节点」定位，不用硬编码下标（下标会随块数变化说谎）
    // 2026-10-25：顶部改成旧页 `.top-split` 两栏后，成交额与板块轮动成了同一个直系子节点的孩子，
    //   「直系下标」不再能区分它们 → 改成**文档序扁平下标**（先序遍历整个 wrap，深块白然排在后）。
    //   这样 `turnoverIdx < sectorIdx` 这类断言仍然成立，且不再受嵌套层数影响。
    const flat = [];
    (function walk(n) { Array.from(n.children).forEach((c) => { flat.push(c); walk(c); }); })(wrap);
    const idxOf = (sel) => { const el = wrap.querySelector(sel); return el ? flat.indexOf(el) : -1; };
    // 尾部来源行必须取直系子节点：块内部也有 .it-src（轨迹摘要/脉冲的副说明行），
    // 全文档 querySelector 会先命中它们。
    const srcEl = Array.from(wrap.children).find((n) => n.classList && n.classList.contains('it-src'));

    const sparkCalls = calls.filter((x) => x.indexOf('/api/sector_spark.json') === 0);
    const paths = Array.from(wrap.querySelectorAll('.mq-spark path')).map((n) => n.getAttribute('d') || '');

    out.mount = {
      fetchedIntraday: calls.filter((x) => x.indexOf('/api/intraday.json') === 0).length,
      sparkCalls: sparkCalls.length,
      sparkCodes: String((sparkCalls[0] || '').split('codes=')[1] || ''),
      quoteCards: wrap.querySelectorAll('.mq-kpi').length,
      sparkPaths: paths.length,
      sparkFilled: paths.filter((d) => d).length,
      sparkFirstAmv: paths.length ? paths[0] : '',
      childClasses: kids,
      sessText: sessEl ? sessEl.textContent : '',
      turnoverKV: turnover ? turnover.querySelectorAll('.to-kv').length : 0,
      turnoverCurAmt: turnover ? (turnover.querySelector('.to-val') || {}).textContent : '',
      auditTds: auditHost ? auditHost.querySelectorAll('td').length : 0,
      detailsOpen: det ? det.open : null,
      styles: ['intraday-card-css', 'intraday-legacy-css', 'intraday-research-css',
        'intraday-audit-css', 'intraday-quotes-css'].filter((id) => !!doc.getElementById(id)),
      // 相对顺序照旧页 4704-4731：冰点(2) → 会话行(3) → …成交额 → …轮动 → …→ 审计明细（末尾，src 之前）
      order: {
        panicBeforeSess: (wrap.children[kids.indexOf('sess-line') - 1].textContent || '').indexOf('恐慌广度') >= 0,
        quotesKpi: wrap.querySelectorAll('.it-card .mq-kpi').length,
        quotesPaths: Array.from(wrap.querySelectorAll('.mq-spark path')).filter((n) => n.getAttribute('d')).length,
        sessIdx: idxOf('.sess-line'),
        quotesIdx: idxOf('.it-quotes-host'),
        turnoverIdx: idxOf('.it-turnover-host'),
        sectorIdx: idxOf('.st-body'),
        researchIdx: idxOf('.rs-wrap-host'),
        holdIdx: idxOf('.it-hold-host'),
        auditIdx: idxOf('.it-audit-host'),
        srcIdx: srcEl ? flat.indexOf(srcEl) : -1,
      },
    };

    // 刷新一拍：会话行文本不变、审计明细仍在（details 不因 refresh 消失）
    await card.refresh();
    out.refresh = {
      details: !!wrap.querySelector('details.fold'),
      sessText: (wrap.querySelector('.sess-line') || {}).textContent || '',
      tds: wrap.querySelectorAll('.it-audit-host td').length,
    };

    await card.unmount();
    // 卡片契约（同 shell.js unmountActive L406-412）：unmount 只解绑+清状态，
    // DOM 节点由壳层移除。这里模拟壳行为，并断言真响应性（unmount 后 refresh 不再写 DOM）。
    const tdsAt = wrap.querySelectorAll('.it-audit-host td').length;
    let refreshThrew = false;
    try { await card.refresh(); } catch (e) { refreshThrew = true; }
    const tdsAfterRefresh = wrap.querySelectorAll('.it-audit-host td').length;
    wrap.remove();   // 模拟 shell.js L412 的 host.remove()
    out.unmount = {
      after: root.querySelectorAll('.it-audit-host').length,
      stillResponsive: tdsAfterRefresh !== tdsAt,
      refreshThrew,
    };
    dom.window.close();
  }

  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  console.log(JSON.stringify(await run()));
}
