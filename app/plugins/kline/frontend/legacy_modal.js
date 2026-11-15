// K 线弹窗 · 旧版原样挂载（2026-10-27）
//
// 目标：把 8800 时代已做好的 K 线完整弹窗**原样**作为插件入口，不重写功能。
//   · DOM：`frontend/legacy/modal.html`（自 engine/web/index.html 的 #klineModal 原样提取）
//   · CSS：`frontend/legacy/modal.css`（自同一 <style> 块提取弹窗所需规则，裸元素规则已限定作用域）
//   · 脚本：沿用 8810 根静态直接提供的旧四模块与四个库（只读 GET，不改旧文件）
//
// 装载顺序照旧页 index.html:9022-9029：vendor LC → LC drawing → jquery → HQChart → kline_lc
//   → quote5 → f10 → order。经典 script（非 ESM）：四个旧模块都是 IIFE，靠 window.KLineApp 串链。
//   vendor/klinecharts.min.js 略（现 LC 不用）。
//
// 幂等：CSS/DOM/每个脚本各插一次；初始化用单一 Promise，并发调用共用同一次。
// 失败：显式可见错误（红条 + console.error），**不静默退回简化版**。
//
// 版本串必须与 plugin.json 一致（tests/test_kline_overlay_p1a.py 有守卫）。

// 标签层（2026-11-04 · 01）：自注册副作用模块——本文件只加这一行，逻辑全在 labels_layer.js
// （不改旧四模块；标签按代码存 localStorage，读写都走 labels.js）
import './labels_layer.js?v=0.9.67';

const V = '0.9.67';
const ASSET = `/plugins/kline/frontend/legacy`;
const B = '&b=20261113i';

const CSS_ID = 'kline-legacy-css';
const CSS_HREF = `${ASSET}/modal.css?v=${V}${B}`;
const DOM_URL = `${ASSET}/modal.html?v=${V}${B}`;
const ERR_ID = 'kline-legacy-error';
const MOUNT_ATTR = 'data-kline-legacy-mount';   // 只给自己插进来的 DOM 打标（旧页原生弹窗不插手）

// 旧页装载顺序（vendor klinecharts 略；它只服务已置为 text/plain 的 v9 旧块）
// 静态资源构建号（2026-11-12）：旧模块与 vendor 库的 ?v= 只跟插件 manifest 版本，
// 不改 manifest 时浏览器会拿旧 JS。追加构建号，页面刷新即换新。
const LIBS = [
  ['lc', '/vendor/lightweight-charts.standalone.production.js?v=' + V + B],
  ['lc-drawing', '/vendor/lightweight-charts-drawing.umd.js?v=' + V + B],
  ['jquery', '/vendor/jquery.min.js?v=' + V + B],
  ['hqchart', '/vendor/hqchart.umychart.min.js?v=' + V + B],
];
const MODULES = [
  // 2026-10-28：带上 V 缓存串——旧模块改了样式/取数后，浏览器不能继续用缓存。
  ['kline_lc', '/kline_lc.js?v=' + V + B],
  ['kline_quote5', '/kline_quote5.js?v=' + V + B],
  ['kline_f10', '/kline_f10.js?v=' + V + B],
  ['kline_order', '/kline_order.js?v=' + V + B],
];
const PERIOD_KEYS = ['d', 't', 'w', 'm1', 'm5', 'm15', 'm30', 'm60'];

let _initPromise = null;

// ---------------------------------------------------------------- 可见错误

function showError(msg) {
  try { console.error('[kline-legacy] ' + msg); } catch (e) { /* 无 console 也不致命 */ }
  let el = document.getElementById(ERR_ID);
  if (!el) {
    el = document.createElement('div');
    el.id = ERR_ID;
    // 只做「看得见的失败」：固定底部一条，样式内联（本组件不依赖插件 CSS 是否已插成功）
    el.setAttribute('role', 'alert');
    // 颜色只走 themes.css 语义 token（裸 hex / rgba 会被 app/tests 的主题守卫拦下）
    el.style.cssText = 'position:fixed;left:12px;right:12px;bottom:12px;z-index:2147483000;'
      + 'padding:10px 12px;border-radius:8px;font-size:13px;line-height:1.5;'
      + 'background:var(--danger-bg);color:var(--danger-ink);'
      + 'border:1px solid var(--danger);box-shadow:var(--shadow-pop);'
      + 'white-space:pre-wrap;';
    document.body.appendChild(el);
  }
  el.textContent = 'K 线弹窗（旧版）装载失败：' + msg;
  return el;
}

function clearError() {
  const el = document.getElementById(ERR_ID);
  if (el && el.parentNode) el.parentNode.removeChild(el);
}

// ---------------------------------------------------------------- CSS / DOM

function ensureCss() {
  if (document.getElementById(CSS_ID)) return;
  const link = document.createElement('link');
  link.id = CSS_ID;
  link.rel = 'stylesheet';
  link.href = CSS_HREF;
  document.head.appendChild(link);
}

async function ensureDom() {
  if (document.getElementById('klineModal')) return;
  const res = await fetch(DOM_URL, { cache: 'no-store' });
  if (!res.ok) throw new Error(`modal.html ${res.status}`);
  const html = await res.text();
  // 再判一次：fetch 期间可能有并发调用已插好
  if (document.getElementById('klineModal')) return;
  const holder = document.createElement('div');
  holder.innerHTML = html;
  const modal = holder.querySelector('#klineModal');
  if (!modal) throw new Error('modal.html 里没有 #klineModal');
  modal.setAttribute(MOUNT_ATTR, '1');
  document.body.appendChild(modal);
}

// ---------------------------------------------------------------- 点卡片外空白关闭
//
// 旧页 `kline_order.js:1383` 明确**取消**了「点卡片外关闭」（2026-10-21 主人：拖拽时误退），
// 原样挂载把这行为也带了回来。V2 口径是全局通用的：`paper/frontend.js:845` 与
// `sector/detail.js:326` 都是 `mask.addEventListener('click', ev => { if (ev.target === mask) close(); })`。
// 本装载层补回这条，**不改旧四 JS**。
//
// 三条约束：
//   ① 只认 `ev.target === ev.currentTarget`（真正落在遮罩空白上）；子卡片/侧栏/工具栏的点击
//      自然不满足，无需逐个排除；
//   ② 不用 document 级外点监听：下单卡 `koPanel`/`koMenu`/`ddMenu` 挂在 body 上，document 级
//      监听会把「点下单卡」判成外部点击而误退；
//   ③ 不照抄「拖拽时外松关闭」：浏览器对「图表内按下 → 遮罩上松开」也会派一个 click，
//      target 恰好是共同祖先（就是遮罩）。故另记 pointerdown/pointerup 两个落点，
//      必须**按下与松开都在遮罩上**才关；两者都只记录，不 preventDefault、不 stopPropagation。
function bindMaskClose() {
  const m = document.getElementById('klineModal');
  if (!m || m.getAttribute(MOUNT_ATTR) !== '1') return;   // 不是本插件插的 DOM：不插手
  if (m.dataset.maskClose === '1') return;                // 幂等：只绑一次
  m.dataset.maskClose = '1';
  let downOnMask = false;
  let upOnMask = false;
  m.addEventListener('pointerdown', (ev) => { downOnMask = (ev.target === m); upOnMask = false; }, true);
  m.addEventListener('pointerup', (ev) => { upOnMask = (ev.target === m); }, true);
  m.addEventListener('click', (ev) => {
    // 与 V2 同事件（click）：pointerdown 阶段旧模块要画线/起拖拽，不能在那时关窗
    if (ev.target !== ev.currentTarget) return;
    const both = downOnMask && upOnMask;
    downOnMask = false; upOnMask = false;
    if (both) closeKline();
  });
}

// ---------------------------------------------------------------- 浮层栈（2026-10-28）
//
// K 线弹窗与板块详情可以叠加（板块详情里点个股 → K 线盖在上面，详情不关）。
// 旧模块与 detail.js 各自在 document 上听 Esc，同时开着会两层一起关。
// 壳提供 window.__v2Overlay（shell.js::overlayStack）；本文件只做两件事：
//   ① 把弹窗显隐同步进栈（MutationObserver 看 style，不管谁关的都能同步）；
//   ② Esc 只在**自己是栈顶**时处理，并 stopImmediatePropagation 拦住下层的 document 监听。
function syncStack() {
  const m = document.getElementById('klineModal');
  const ov = window.__v2Overlay;
  if (!m || !ov || !ov.push) return;
  if (m.style.display === 'flex') ov.push('kline');
  else ov.pop('kline');
}

function bindStack() {
  const m = document.getElementById('klineModal');
  if (!m || m.getAttribute(MOUNT_ATTR) !== '1') return;
  if (m.dataset.stackBound === '1') return;
  m.dataset.stackBound = '1';
  syncStack();
  const mo = new MutationObserver(syncStack);
  mo.observe(m, { attributes: true, attributeFilter: ['style'] });
  // Esc：只在 K 线是栈顶时关它（capture 阶段抢在旧模块/详情面板的 document 冒泡监听前）。
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape') return;
    const ov = window.__v2Overlay;
    if (!ov || !ov.top || ov.top() !== 'kline') return;   // 不是最上层：交给上层
    const el = document.getElementById('klineModal');
    if (!el || el.style.display !== 'flex') return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    closeKline();
  }, true);
}

// ---------------------------------------------------------------- 列表卡 / F10 卡（2026-10-28）
//
// 主人要求：左边一个列表卡片（当前列表上下文全部标的，可收起/展开），右边 F10 卡也能收起/展开。
// 约束：不改旧四模块——列表数据来自 openKline 的 list 参数（与旧 open(code,name,list) 同一份），
//   切票高亮靠观察 #klineTitle 文本（旧 navList 会重写它），F10 显隐仍由 kline_f10.js 控 display，
//   本层只加宽度 class，不与它抢状态。

const LIST_KEY = 'v2.kline.listOpen';
const F10_KEY = 'v2.kline.f10Open';
let curList = null;

function lsBool(key, dflt) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? dflt : v === '1';
  } catch (e) { return dflt; }
}

function lsSet(key, on) {
  try { localStorage.setItem(key, on ? '1' : '0'); } catch (e) { /* 隐私模式不致命 */ }
}

function listWanted() { return lsBool(LIST_KEY, true); }
function f10Wanted() { return lsBool(F10_KEY, true); }

function applyListState() {
  const m = document.getElementById('klineModal');
  const p = document.getElementById('kListPanel');
  const btn = document.getElementById('btnListToggle');
  if (!m || !p || !btn) return;
  const has = !!(curList && curList.length > 1);
  const open = has && listWanted();
  p.classList.toggle('open', open);
  m.classList.toggle('with-list', open);
  p.setAttribute('aria-hidden', open ? 'false' : 'true');
  btn.classList.toggle('on', open);
  btn.disabled = !has;
  btn.title = has ? '列表卡片：开/关（共 ' + curList.length + ' 只，可上下键切票）' : '当前没有列表上下文';
}

function applyF10State() {
  const m = document.getElementById('klineModal');
  const p = document.getElementById('kF10Panel');
  const btn = document.getElementById('btnF10Toggle');
  if (!m || !p || !btn) return;
  const open = f10Wanted();
  p.classList.toggle('kfp-collapsed', !open);
  m.classList.toggle('no-f10', !open);
  btn.classList.toggle('on', open);
}

function curCodeFromTitle() {
  const t = document.getElementById('klineTitle');
  const m = t && /([0-9]{6}\.(?:SH|SZ|BJ|TI))/i.exec(t.textContent || '');
  return m ? m[1].toUpperCase() : '';
}

function syncKListActive(scroll) {
  const body = document.querySelector('#kListPanel .klp-body');
  if (!body) return;
  const code = curCodeFromTitle();
  const items = body.querySelectorAll('.klp-item');
  let idx = -1;
  for (let i = 0; i < items.length; i++) {
    const on = items[i].getAttribute('data-code') === code;
    items[i].classList.toggle('on', on);
    if (on) idx = i;
    if (on && scroll) { try { items[i].scrollIntoView({ block: 'nearest' }); } catch (e) { /* 忽略 */ } }
  }
  const cnt = document.querySelector('#kListPanel .klp-count');
  if (cnt) cnt.textContent = items.length ? (idx >= 0 ? (idx + 1) : '-') + '/' + items.length : '';
  schedulePrefetch();
}

// ---------------------------------------------------------------- 相邻票预取（2026-10-28）
// 列表很长时切票要等 /api/kline 冷取数（实测沪市冷票 0.4–0.9s）。
// 空闲时预取当前项后 2 只、前 1 只的日线与除权，把服务端 60s 缓存热起来；
// 不预取 F10/盘口/筹码——它们各自有缓存且更重，预取会跟当前票抢 eltdx 连接。
const PREFETCH_TTL = 45000;
const _prefetched = {};
let _pfTimer = 0;

function prefetchAround() {
  if (!curList || curList.length < 3) return;
  const cur = curCodeFromTitle();
  let idx = -1;
  for (let i = 0; i < curList.length; i++) {
    if (curList[i] && curList[i].code === cur) { idx = i; break; }
  }
  if (idx < 0) return;
  // 主人 2026-10-28 定：只预取下一只（多预取收益小，还会跟当前票抢连接）。
  const next = curList[idx + 1];
  const picks = (next && next.code) ? [next.code] : [];
  picks.forEach(function (code, k) {
    if (Date.now() - (_prefetched[code] || 0) < PREFETCH_TTL) return;
    _prefetched[code] = Date.now();
    setTimeout(function () {
      const m = document.getElementById('klineModal');
      if (!m || m.style.display !== 'flex') return;
      try {
        fetch('/api/kline/' + encodeURIComponent(code) + '?period=d&limit=240&mf_lookback=20', { cache: 'no-store' })
          .catch(function () { /* 预取失败不影响主流程 */ });
        fetch('/api/kline/' + encodeURIComponent(code) + '/xdxr', { cache: 'no-store' })
          .catch(function () { /* 同上 */ });
      } catch (e) { /* 同上 */ }
    }, 300 + k * 150);
  });
}

function schedulePrefetch() {
  if (_pfTimer) clearTimeout(_pfTimer);
  _pfTimer = setTimeout(function () { _pfTimer = 0; prefetchAround(); }, 250);
}

// 列表右侧涨跌幅（2026-11-05 标注 80：主人要求「不显示代码，代码列改显示涨跌幅」）。
//   数据源就用自选池卡那份 `/api/watchlist.json`（items[].quote.change_pct），不新建接口；
//   弹窗开时异步拉一次后重绘，取不到的票留空、不拿 0 冒充。
let klpPct = null;
let klpPctAt = 0;
const KLP_TTL = 60000;   // 涨跌幅 60s 过期：同一弹窗反复开关不重复拉，盘中也不至于拿旧值
function ensureKlpPct() {
  if (klpPct && (Date.now() - klpPctAt) < KLP_TTL) return Promise.resolve(klpPct);
  klpPct = {};
  klpPctAt = Date.now();
  return fetch('/api/watchlist.json', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      (d && d.items || []).forEach(function (it) {
        const q = (it && it.quote) || {};
        if (it && it.code && q.change_pct != null && isFinite(Number(q.change_pct))) {
          klpPct[it.code] = Number(q.change_pct);
        }
      });
      return klpPct;
    })
    .catch(function () { return klpPct; });
}

function renderKList() {
  const body = document.querySelector('#kListPanel .klp-body');
  const cnt = document.querySelector('#kListPanel .klp-count');
  if (!body) return;
  body.textContent = '';
  if (cnt) cnt.textContent = (curList && curList.length > 1) ? curList.length + ' 只' : '';  if (curList && curList.length > 1) {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < curList.length; i++) {
      const it = curList[i] || {};
      const b = document.createElement('button');
      b.className = 'klp-item';
      b.setAttribute('data-code', it.code);
      b.title = (it.name || '') + ' ' + (it.code || '');
      const nm = document.createElement('span');
      nm.className = 'klp-nm';
      nm.textContent = it.name || it.code || '';
      const cd = document.createElement('span');
      // 代码不在此列展示，只留涨跌幅（代码在 title 与 data-code 里）
      const v = (klpPct && it.code in klpPct) ? klpPct[it.code] : null;
      cd.textContent = (v == null) ? '' : ((v > 0 ? '+' : '') + v.toFixed(2) + '%');
      cd.className = 'klp-cd' + (v == null ? '' : (v > 0 ? ' up' : (v < 0 ? ' down' : ' flat')));
      b.appendChild(nm);
      b.appendChild(cd);
      b.addEventListener('click', function () {
        const app = window.KLineApp;
        if (!app || typeof app.open !== 'function') return;
        app.open(it.code, it.name || '', curList);
      });
      frag.appendChild(b);
    }
    body.appendChild(frag);
  }
  syncKListActive(true);
  applyListState();
}

function bindPanels() {
  const m = document.getElementById('klineModal');
  if (!m || m.getAttribute(MOUNT_ATTR) !== '1') return;
  if (m.dataset.panels === '1') return;
  m.dataset.panels = '1';
  const lb = document.getElementById('btnListToggle');
  const fb = document.getElementById('btnF10Toggle');
  if (lb) lb.addEventListener('click', function () {
    if (!curList || curList.length < 2) return;
    lsSet(LIST_KEY, !listWanted());
    applyListState();
  });
  if (fb) fb.addEventListener('click', function () {
    lsSet(F10_KEY, !f10Wanted());
    applyF10State();
  });
  const title = document.getElementById('klineTitle');
  if (title) {
    new MutationObserver(function () { syncKListActive(true); }).observe(title, {
      childList: true, characterData: true, subtree: true,
    });
  }
  applyListState();
  applyF10State();
}

// ---------------------------------------------------------------- 脚本

function loadScript(key, src) {
  const sel = `script[data-kline-legacy="${key}"]`;
  const exist = document.querySelector(sel);
  if (exist) {
    // 已在装载或已装载：已完成的直接过，进行中的等它
    if (exist.dataset.state === 'done') return Promise.resolve();
    if (exist.dataset.state === 'error') return Promise.reject(new Error(`${src} 之前装载失败`));
    return new Promise((resolve, reject) => {
      exist.addEventListener('load', () => resolve(), { once: true });
      exist.addEventListener('error', () => reject(new Error(`${src} 装载失败`)), { once: true });
    });
  }
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.setAttribute('data-kline-legacy', key);
    s.dataset.state = 'loading';
    s.async = false;            // 经典脚本按插入顺序执行
    s.src = src;
    s.onload = () => { s.dataset.state = 'done'; resolve(); };
    s.onerror = () => { s.dataset.state = 'error'; reject(new Error(`${src} 装载失败`)); };
    document.head.appendChild(s);
  });
}

// ---------------------------------------------------------------- 初始化

function legacyReady() {
  return !!(window.KLineApp && typeof window.KLineApp.open === 'function');
}

async function doInit() {
  ensureCss();
  // DOM 必须先于 kline_lc.js 存在：旧模块在脚本求值时就做 `if ($("klineModal")) init()`
  await ensureDom();
  bindMaskClose();                           // 幂等；与旧模块初始化无关，DOM 到位即可绑
  bindStack();                               // 同上：显隐同步进浮层栈 + Esc 只关栈顶
  bindPanels();                              // 列表卡 / F10 卡开关（同上，只绑一次）
  if (legacyReady()) return;                 // 已有 library（例如嵌在旧页里）：不重复创建
  for (const [k, src] of LIBS) await loadScript(k, src);
  for (const [k, src] of MODULES) await loadScript(k, src);
  if (!legacyReady()) throw new Error('旧模块已加载但 window.KLineApp 未就绪');
}

function ensureInit() {
  if (!_initPromise) {
    _initPromise = doInit().then(() => { clearError(); return true; }, (err) => {
      // 失败后允许下次重试（比如临时网络抖动），但本次必须让用户看见
      _initPromise = null;
      showError(err && err.message ? err.message : String(err));
      throw err;
    });
  }
  return _initPromise;
}

// ---------------------------------------------------------------- 对外入口

/**
 * 打开旧版 K 线弹窗。
 * @param {object} o {api, code, name, period}
 *   api 保留在签名里（与壳的 makeCtx 同形），旧模块走同源绝对路径取数，不需要它。
 * @returns {Promise<boolean>}
 */
export async function openKline(o) {
  const opt = o || {};
  const code = String(opt.code || '').trim();
  if (!code) throw new Error('openKline 需要 code');
  await ensureInit();
  const app = window.KLineApp;
  // 旧 open 的真实签名：open(code, name, list) —— 第三位是「同列表上下键切票」的上下文。
  // 2026-10-28：壳现在把点击位置所在的列表传进来（shell.js::klineListOf），
  // 旧弹窗的 ↑↓ 切票因此可用；没有上下文时传 null（旧代码对空 list 已判空）。
  // 同一份 list 也喂给左侧列表卡（renderKList），两处永远一致。
  curList = Array.isArray(opt.list) && opt.list.length > 1 ? opt.list : null;
  app.open(code, opt.name || '', curList);
  // 2026-10-30（标注 60）：弹窗刚显示时 chart 宽度还是 0，视口设置被 LC 丢掉 →
  //   首屏左对齐到最旧一根。尺寸稳定后再设两次（120ms 首调，420ms 补抛）。
  [120, 420].forEach((ms) => setTimeout(() => {
    try {
      if (window.KLineApp && typeof window.KLineApp.resetView === 'function') window.KLineApp.resetView();
    } catch (e) { /* 视口重设失败不该阻断开窗 */ }
  }, ms));
  renderKList();
  ensureKlpPct().then(function () { renderKList(); });   // 涨跌取到后重绘一次（不阻塞开窗）
  const p = String(opt.period || '');
  if (p && PERIOD_KEYS.indexOf(p) >= 0 && p !== 'd') {
    // open 内部已把周期重置为 d；要别的档就再切一次（setPeriod 在 cur.code 已设后才生效）
    app.setPeriod(p);
  }
  return true;
}

/** 关闭弹窗。未初始化 / 未打开时静默返回，不抛。 */
export function closeKline() {
  try {
    if (legacyReady()) window.KLineApp.close();
  } catch (e) { /* 关闭失败不该冒泡到壳 */ }
}

export const openKlineStandalone = openKline;
export default { openKline, closeKline, openKlineStandalone };
