// ============================================================================
// 估值卡 · 前端（2026-10-29，projects/panel-v2）
// ----------------------------------------------------------------------------
// 内容 = 现有 PEG 估值选股仪表盘（`夏天-activity/工具/peg/output/peg_dashboard.html`，
// 自包含单文件：KPI 卡 / 散点图 / 可排序表格 / 全部图表 / 新闻·公告·K线区间 / 预期修正雷达）。
//
// **本卡只做「壳」**：一个铺满主区域的 iframe 指向 core 只读挂载的 `/peg/`。
//   · 不解析产物、不重算指标、不二次加工数据 —— 产物是唯一源，面板是只读托管方；
//   · 不复制产物进 panel-v2（复制 = 双源，脚本一重跑副本就旧了）；
//   · 联动只做**背板转发**（2026-10-31）：产物 iframe 内点股票/行业时 postMessage 上来，
//     本卡校验后转成壳里已有的 `panel:open-kline` / `panel:open-card` 事件，
//     不新建弹窗、不新建端点、不代产物拉面板 API。产物自身零 fetch，单开时静默降级。
//
// 唯一本地逻辑：`/api/p/valuation/meta` 给出的 mtime/大小 → 顶栏一行「产物时间 · 大小」，
// 并把它拼成 `?v=` 做 cache-busting（脚本重跑后不用重启 8810，重载即见新页）。
//
// 契约（docs/CONTRACT.md §5）：原生 ES module、零 import、零构建、只操作自己的 root。
// ============================================================================

const STYLE_ID_CSS = 'valuation-card-css';

const CSS = [
  '.val-root{display:flex;flex-direction:column;min-width:0;min-height:0}',
  '.val-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:0 2px 8px;font-size:0.72rem;color:var(--dim);flex:0 0 auto}',
  '.val-bar b{color:var(--fg);font-weight:600}',
  '.val-warn{color:var(--warn)}',
  '.val-spacer{margin-left:auto}',
  '.val-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer}',
  '.val-btn:hover{background:var(--card2);border-color:var(--accent);color:var(--accent)}',
  '.val-refresh{font-weight:600}',
  '.val-btn[data-state="running"]{cursor:progress;border-color:var(--accent);color:var(--accent);opacity:0.9}',
  '.val-btn[data-state="failed"]{border-color:var(--danger);color:var(--danger)}',
  '.val-frame-box{flex:1 1 auto;min-height:0;display:flex;border-radius:var(--radius-md);overflow:hidden;border:var(--border-w) var(--border-style) var(--border-color);background:var(--bg)}',
  '.val-frame{flex:1 1 auto;width:100%;height:100%;border:0;display:block;background:var(--bg)}',
  '.val-msg{padding:14px 16px;font-size:0.8rem;line-height:1.6;color:var(--fg);background:var(--danger-bg);border:var(--border-w) var(--border-style) var(--danger);border-radius:var(--radius-md);white-space:pre-wrap;word-break:break-word}',
  '.val-msg code{font-family:var(--font-mono);font-size:0.75rem;color:var(--fg-strong)}',
];

function injectStyle() {
  if (document.getElementById(STYLE_ID_CSS)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID_CSS;
  s.textContent = CSS.join('\n');
  document.head.append(s);
}

function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return Math.round(n / 1024) + ' KB';
}

function fmtAge(seconds) {
  const s = Number(seconds) || 0;
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + ' 分钟前';
  if (s < 86400) return Math.round(s / 3600) + ' 小时前';
  return Math.round(s / 86400) + ' 天前';
}

// 铺满主区域：`#stage` 是 flex 子项（高度确定），但带 12/16/20 的内边距。
// 用实测 clientHeight + 负 margin 抵消内边距，比 `height:100%` 稳（不依赖百分比能否解析），
// 窗口缩放/侧栏折叠时由 ResizeObserver 重算。
function fitToStage(root, stage) {
  if (!stage) return;
  const cs = getComputedStyle(stage);
  const pt = parseFloat(cs.paddingTop) || 0;
  const pb = parseFloat(cs.paddingBottom) || 0;
  const pl = parseFloat(cs.paddingLeft) || 0;
  const pr = parseFloat(cs.paddingRight) || 0;
  root.style.margin = (-pt) + 'px ' + (-pr) + 'px ' + (-pb) + 'px ' + (-pl) + 'px';
  root.style.height = Math.max(320, stage.clientHeight) + 'px';
}

// 模块内清理句柄：shell 调 `unmount()` 时不传参（见 shell.js unmountActive），
// 所以把 observer/iframe 的收尾存在模块作用域（同一时刻只挂一张卡，不存在多实例竞争）。
let activeCleanup = null;

export default {
  cards: [{
    id: 'valuation.main',
    title: '估值',
    slot: 'main',
    order: 75,
    // 不声明 refreshEvery：产物是脚本按需生成的日频 HTML，iframe 自动重载反而打断阅读；
    // 需要新数据就重跑 refresh_dashboard.ps1，再点顶栏「重新载入」。
    async mount(el, ctx) {
      injectStyle();

      const root = document.createElement('div');
      root.className = 'val-root';

      const bar = document.createElement('div');
      bar.className = 'val-bar';
      const stamp = document.createElement('span');
      stamp.textContent = '产物时间读取中…';
      // 2026-11-04（主人：这页用得少，不进盘后链，顶部给个刷新按钮即可）：
      //   按钮 = 重新跑一次 peg_dashboard.py（约 110~140 秒，联网），跑完自动重载 iframe。
      const refreshBtn = document.createElement('button');
      refreshBtn.type = 'button';
      refreshBtn.className = 'val-btn val-refresh';
      refreshBtn.textContent = '刷新数据';
      refreshBtn.title = '重新生成估值仪表盘（约 2 分钟，会联网）';
      const hint = document.createElement('span');
      hint.className = 'val-spacer';
      hint.textContent = '只读托管 · 数据由 peg_dashboard.py 生成';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'val-btn';
      btn.textContent = '重新载入';
      // 2026-11-03（主人：估值页标注不可用）：本卡内容是 iframe，父页的标注开关管不到内层；
      //   iframe 内的 v2_note 处于 embedded 模式，按设计不造浮标（怕和父页重复），
      //   所以这里给一个卡内入口：开/关都走 iframe 自己的 __v2Note.setMode。
      const noteBtn = document.createElement('button');
      noteBtn.type = 'button';
      noteBtn.className = 'val-btn';
      noteBtn.textContent = '标注';
      noteBtn.title = '点估值页上任意元素写标注（再点一次退出）';
      bar.append(refreshBtn, stamp, hint, noteBtn, btn);

      const box = document.createElement('div');
      box.className = 'val-frame-box';

      root.append(bar, box);
      el.append(root);

      const stage = el.parentElement;
      fitToStage(root, stage);
      let ro = null;
      if (stage && typeof ResizeObserver === 'function') {
        ro = new ResizeObserver(function () { fitToStage(root, stage); });
        ro.observe(stage);
      }

      let current = null;      // 最近一次 meta
      let frame = null;        // 当前 iframe（无产物时不存在）

      // --------------------------------------------------------------
      // 产物 → 面板联动（只转发，不取数）
      // 三重校验：source 必须是本卡这张 iframe；origin 必须同源；payload 只收 code/name。
      // 通过后转成壳里已有的两个事件，不动 core、不新增端点、不新开弹窗：
      //   peg:open-stock  → panel:open-kline（shell.js bindCardJump → ctxOpenKline）
      //   peg:open-sector → panel:open-card  { pluginId:'sector', args:{industry} }
      //                     （shell.js 的卡间跳转，sector 卡自己拿 args 定位）
      // --------------------------------------------------------------
      const MSG_STOCK = 'peg:open-stock';
      const MSG_INDUSTRY = 'peg:open-sector';
      const MSG_CONTEXT = 'peg:context-menu';
      const SELF_ORIGIN = window.location.origin;

      // 右键：产物只报「点了谁 + 坐标」，这里合成到面板**现有的** ctx 菜单上
      // （shell.js 的 document contextmenu 委托 → ctxResolve → ctxOpen），不另写一套菜单。
      // 临时节点只用来承载 data-* 属性，派发完立即移除；ctxOpen 是异步取菜单项，
      // 但它在第一帧就已把 tgt 解析成普通对象，移除节点不影响。
      function openPanelContextMenu(payload, frameRect) {
        const code = String(payload.code == null ? '' : payload.code).trim();
        if (!/^\d{6}$/.test(code)) return;
        const name = String(payload.name == null ? '' : payload.name).trim().slice(0, 24);
        const px = Math.round(frameRect.left + (Number(payload.x) || 0));
        const py = Math.round(frameRect.top + (Number(payload.y) || 0));
        const proxy = document.createElement('div');
        proxy.setAttribute('data-ctx', 'stock');
        proxy.setAttribute('data-code', code);
        proxy.setAttribute('data-name', name);
        proxy.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;pointer-events:none';
        document.body.append(proxy);
        proxy.dispatchEvent(new MouseEvent('contextmenu', {
          bubbles: true, cancelable: true, clientX: px, clientY: py, button: 2, buttons: 2,
        }));
        proxy.remove();
        armMenuDismiss();
      }

      // 右键菜单的关闭：壳只监听**父页**的 click / Esc / scroll / resize；
      // 而菜单往往是在 iframe 里右键开的，在 iframe 里点空白、滚动、按 Esc 都传不到父页，
      // 表现就是「菜单打开了收不起来」。同源 iframe 可直接给它的文档挂监听，
      // 触发后合成一次父页 click，走壳自己的关闭路径（不另写关闭逻辑）。
      let menuCleanup = null;

      function closePanelMenu() {
        document.body.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
      }

      function disarmMenuDismiss() {
        if (typeof menuCleanup === 'function') menuCleanup();
        menuCleanup = null;
      }

      function armMenuDismiss() {
        disarmMenuDismiss();
        const win = frame && frame.contentWindow;
        const doc = frame && frame.contentDocument;
        if (!win || !doc) return;
        const onPointer = function () { disarmMenuDismiss(); closePanelMenu(); };
        const onKey = function (e) { if (e.key === 'Escape') { disarmMenuDismiss(); closePanelMenu(); } };
        const onScroll = function () { disarmMenuDismiss(); closePanelMenu(); };
        doc.addEventListener('pointerdown', onPointer, true);
        doc.addEventListener('mousedown', onPointer, true);
        doc.addEventListener('keydown', onKey, true);
        win.addEventListener('scroll', onScroll, true);
        menuCleanup = function () {
          doc.removeEventListener('pointerdown', onPointer, true);
          doc.removeEventListener('mousedown', onPointer, true);
          doc.removeEventListener('keydown', onKey, true);
          win.removeEventListener('scroll', onScroll, true);
        };
      }

      function onProductMessage(ev) {
        if (!frame || ev.source !== frame.contentWindow) return;
        if (ev.origin !== SELF_ORIGIN) return;
        const d = ev.data;
        if (!d || typeof d !== 'object') return;
        const p = (d.payload && typeof d.payload === 'object') ? d.payload : {};
        if (d.type === MSG_STOCK) {
          const code = String(p.code == null ? '' : p.code).trim();
          if (!/^\d{6}$/.test(code)) return;          // 产物只发 6 位裸码，其余一律丢
          const name = String(p.name == null ? '' : p.name).trim().slice(0, 24);
          document.dispatchEvent(new CustomEvent('panel:open-kline', {
            detail: { code: code, name: name }, bubbles: true, cancelable: true,
          }));
          return;
        }
        if (d.type === MSG_INDUSTRY) {
          const name = String(p.name == null ? '' : p.name).trim().slice(0, 40);
          if (!name) return;
          document.dispatchEvent(new CustomEvent('panel:open-card', {
            detail: { pluginId: 'sector', args: { industry: name } }, bubbles: true, cancelable: true,
          }));
          return;
        }
        if (d.type === MSG_CONTEXT) {
          openPanelContextMenu(p, frame.getBoundingClientRect());
        }
      }
      window.addEventListener('message', onProductMessage);

      function showMessage(text) {
        box.textContent = '';
        const msg = document.createElement('div');
        msg.className = 'val-msg';
        msg.textContent = text;
        box.append(msg);
        frame = null;
      }

      // 2026-11-03（主人：估值页标注功能不可用）：本卡内容是 iframe 里的产物 HTML，
      //   父页的 v2_note.js 只能拿到 <iframe> 整块（elementsFromPoint 不穿文档），所以这张卡不能标。
      //   同源 iframe 直接注入 /static/v2_note.js，并把 pageKey 固定成本卡卡键 →
      //   标注落到「v2:card:valuation::valuation.main」这组下，与标注看板的分组一致。
      const CARD_KEY = 'valuation::valuation.main';
      const noteCardKey = function () { return CARD_KEY; };

      function injectNote(frameEl) {
        let win = null, doc = null;
        try { win = frameEl.contentWindow; doc = frameEl.contentDocument; } catch (err) { return; }
        if (!win || !doc) return;                       // 跨域/未就绪 → 下次 load 再来
        try {
          if (win.__v2Note) { win.__v2Note.init({ cardKey: noteCardKey }); return; }
          if (doc.getElementById('v2-note-module')) return;   // 已注入、模块还在加载
          const s = doc.createElement('script');
          s.id = 'v2-note-module';
          s.type = 'module';
          s.src = '/static/v2_note.js';
          s.addEventListener('load', function () {
            try { if (win.__v2Note) win.__v2Note.init({ cardKey: noteCardKey }); } catch (err) { /* 注入成功但 init 失败：不阻断页面 */ }
          });
          (doc.head || doc.documentElement).append(s);
        } catch (err) { /* 注入失败（沙箱/未就绪）→ 这张卡暂时没有标注，不阻断页面 */ }
      }

      function mountFrame(url, version) {
        const next = document.createElement('iframe');
        next.className = 'val-frame';
        next.title = 'PEG 估值选股仪表盘';
        next.setAttribute('referrerpolicy', 'no-referrer');
        next.addEventListener('load', function () {
          injectNote(next);
          noteBtn.classList.remove('on');      // 换 iframe = 标注模式回到关（新文档里也不会自动开）
          noteBtn.textContent = '标注';
        });
        // 主题跟随面板（2026-10-31）：产物支持 ?mode=light|dark，默认 dark。
        // 面板默认 frostglass 是浅色，不传 mode 就会在浅色面板里嵌一块深色 —— 用户说的「像两个软件」。
        // 明暗不看主题名（会随主题增删失效），直接量父页 body 背景亮度。
        const mode = panelMode();
        next.dataset.mode = mode;
        next.src = url + (version ? '?v=' + version : '') + '&mode=' + mode;
        box.textContent = '';
        box.append(next);
        frame = next;
      }

      // 量父页底色定明暗：>140 视为浅色（frostglass 之类），否则深色（nightsail 之类）。
      function panelMode() {
        try {
          const bg = getComputedStyle(document.body).backgroundColor || '';
          const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(bg);
          if (!m) return 'dark';
          const lum = 0.299 * Number(m[1]) + 0.587 * Number(m[2]) + 0.114 * Number(m[3]);
          return lum > 140 ? 'light' : 'dark';
        } catch (err) { return 'dark'; }
      }

      async function load(forceReload) {
        let meta = null;
        try {
          meta = await ctx.api.get('/meta');
        } catch (err) {
          stamp.textContent = '产物信息读取失败';
          stamp.className = 'val-warn';
          showMessage('读取产物信息失败：' + (err && err.message ? err.message : err));
          return;
        }
        current = meta;
        if (!meta || !meta.available) {
          stamp.textContent = '产物不存在';
          stamp.className = 'val-warn';
          showMessage(
            '还没生成估值仪表盘产物。\n' +
            '先跑：夏天-activity\\工具\\peg\\refresh_dashboard.ps1（约 140 秒，会联网）\n' +
            '产物路径：' + ((meta && meta.dir) || '') + '\\' + ((meta && meta.file) || '')
          );
          return;
        }
        stamp.className = meta.stale ? 'val-warn' : '';
        stamp.innerHTML = '';
        const b = document.createElement('b');
        b.textContent = meta.mtime_text;
        stamp.append(
          document.createTextNode('产物 '),
          b,
          document.createTextNode(' · ' + fmtSize(meta.size) + ' · ' + fmtAge(meta.age_seconds) + (meta.stale ? '（偏旧，建议重跑）' : ''))
        );
        if (!frame || forceReload) mountFrame(meta.url, meta.mtime);
      }

      btn.addEventListener('click', function () { load(true); });

      // --------------------------------------------------------------
      // 刷新数据（2026-11-04 主人：不进盘后链，这页用得少，顶部给个按钮就够）
      //   触发：core `POST /api/shell/run-script`（名字在旧仓白名单里，插件不起进程）；
      //   等待：每 3 秒查 `/meta`，产物 mtime 变了 = 生成完成 → load(true) 重载 iframe。
      //   失败只在顶栏报错 + 按钮可重试，不动已完成画面（不调 showMessage，那会把 iframe 拆掉）。
      // --------------------------------------------------------------
      let pollTimer = null;
      let baseMtime = 0;        // 点击时的产物 mtime（变了就是新产物）
      let startedAt = 0;
      const REFRESH_TIMEOUT_S = 300;   // core 侧硬上限同值；这里只负责把按钮复位
      function stopPoll() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      }
      function setRefreshState(state, text) {
        refreshBtn.dataset.state = state;
        refreshBtn.textContent = text;
        refreshBtn.disabled = state === 'running';
        refreshBtn.classList.toggle('on', state === 'running');
      }
      async function pollRefresh() {
        const elapsed = Math.round((Date.now() - startedAt) / 1000);
        let meta = null;
        try { meta = await ctx.api.get('/meta'); } catch (err) { return; }
        if (meta && meta.available && meta.mtime && meta.mtime !== baseMtime) {
          stopPoll();
          setRefreshState('idle', '刷新数据');
          await load(true);
          return;
        }
        if (elapsed > REFRESH_TIMEOUT_S) {
          stopPoll();
          setRefreshState('failed', '刷新超时');
          stamp.className = 'val-warn';
          stamp.textContent = '刷新超时：生成超过 ' + REFRESH_TIMEOUT_S + ' 秒仍未出新产物，请看 工具/peg 下日志';
          return;
        }
        setRefreshState('running', '生成中 ' + elapsed + 's');
      }
      refreshBtn.addEventListener('click', async function () {
        if (refreshBtn.disabled) return;
        const scriptKey = (current && current.refresh_script) || 'valuation_refresh';
        baseMtime = (current && current.mtime) || 0;
        startedAt = Date.now();
        setRefreshState('running', '生成中…');
        let r = null;
        try {
          r = await ctx.api.post('/api/shell/run-script', { name: scriptKey });
        } catch (err) {
          setRefreshState('failed', '刷新失败');
          stamp.className = 'val-warn';
          stamp.textContent = '刷新请求失败：' + (err && err.message ? err.message : err);
          return;
        }
        if (!r || !r.ok) {
          if (r && r.reason === 'busy') {          // 上一次还在跑：接着等它
            stopPoll();
            pollTimer = setInterval(pollRefresh, 3000);
            return;
          }
          setRefreshState('failed', '刷新失败');
          stamp.className = 'val-warn';
          stamp.textContent = '刷新未启动：' + ((r && r.detail) || '请求失败');
          return;
        }
        stopPoll();
        pollTimer = setInterval(pollRefresh, 3000);
      });

      // 标注开关：iframe 未就绪 / 注入失败时给出可见提示，不静默失效。
      noteBtn.addEventListener('click', function () {
        const win = frame && frame.contentWindow;
        const api = win && win.__v2Note;
        if (!api) {
          noteBtn.textContent = '标注未就绪';
          setTimeout(function () { noteBtn.textContent = '标注'; }, 1500);
          return;
        }
        const next = !api.isOn();
        api.setMode(next);
        noteBtn.classList.toggle('on', next);
        noteBtn.textContent = next ? '标注中…' : '标注';
      });

      await load(false);

      // unmount 由 shell 调用（切走卡片），且**不传参**；必须断开 observer，
      // 否则被切走后 ResizeObserver 仍挂在 #stage 上，每张卡都留一个。
      // 面板切主题（data-theme 变化）→ 重挂 iframe 换 mode；不重建卡、不动顶栏。
      let themeObs = null;
      if (typeof MutationObserver === 'function') {
        themeObs = new MutationObserver(function () {
          if (!frame || !current) return;
          if (frame.dataset.mode === panelMode()) return;
          mountFrame(current.url, current.mtime);
        });
        themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      }

      activeCleanup = function () {
        disarmMenuDismiss();
        stopPoll();
        if (themeObs) { themeObs.disconnect(); themeObs = null; }
        window.removeEventListener('message', onProductMessage);
        if (ro) { ro.disconnect(); ro = null; }
        if (frame) { frame.src = 'about:blank'; frame = null; }
      };
    },

    async unmount() {
      const fn = activeCleanup;
      activeCleanup = null;
      if (typeof fn === 'function') fn();
    },
  }],
};
