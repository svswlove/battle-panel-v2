// 面板 v2 壳：拉取 /_plugins，动态 import 插件 frontend.js，挂载卡片。
// 无框架、无构建。契约见 docs/CONTRACT.md §5。
//
// 批次 0（2026-10-24，docs/task_fe_batch0.md）：补齐旧壳 chrome ——
//   · 主题/字号：<html data-theme>/<html data-fs>，localStorage 键沿用旧壳（shell_theme/shell_fs），
//     主题清单读 themes.css 的 --theme-manifest（唯一源，禁在此手写主题表）；
//   · 侧栏：旧壳同款 rail（图标列 + 悬浮提示），导航单元是卡片不是固定 Tab；
//   · 搜索面板：照搬旧壳 ShellSearch 交互，fetch 打本进程 /api/shell/search。
// 行为基准 = app/web/legacy/shell.html（旧壳 Shell.applyTheme / Shell.status / ShellSearch）。

// 主题/字号 localStorage 键：**两套并存**，不是历史遗留而是必须兼容。
//   · 旧界面（`engine/web/index.html`，也就是 8810 首页）写 `panel_theme` / `panel_fs`；
//   · 本壳早期版本写 `shell_theme` / `shell_fs`。
// 同源同 localStorage，但键名不同 → 只读一套就会「用户在旧界面选的主题，v2 卡里没生效」。
// 读取顺序：旧界面的键优先（那是用户真实在用的入口），写入两套都写，保证红绿两边一致。
const LS_THEMES = ['panel_theme', 'shell_theme'];
const LS_FSS = ['panel_fs', 'shell_fs'];
const LS_RECENT = 'shell_recent_v1';
const DEFAULT_THEME = 'nightsail';
const DEFAULT_FS = 'md';
const SEARCH_LIMIT = 12;
const RECENT_MAX = 8;

const STATUS_CLASS = { RUNNING: 's-running', FROZEN: 's-frozen', READ_ONLY: 's-readonly', DOWN: 's-down' };
const STATUS_LABEL = { RUNNING: '运行中', FROZEN: '已急停', READ_ONLY: '只读', DOWN: '服务未启动' };

// rail 图标：旧壳 9 个 Tab 的 SVG 原样复用（不追求与旧壳逐字一致，但同类卡片尽量同形）；
// 映射不到的卡片按标题首字渲染（见 railIcon）。
// 侧边栏导航表 = 旧版 9 个页面（2026-10-26 修正）。
//
// 为什么不用「按 pluginId 查图标」：旧版图标是按 **Tab** 分配的，而 v2 卡是按插件拆的，
// 两者对不上就会出现「图标借位」——实测（改前）仪表盘的拱+指针图标被 amv 占用、
// 盘中的折线图标被 kline 占用，dashboard/intraday/selection/ops 四个反而没图标、
// 只能退回「仪/盘/选/后」文字。
//
// 图标 path 逐条照抄 `engine/web/index.html:8548 RAIL_ICONS`（旧版权威，不另画）。
// 顺序 = 旧版 TABS 顺序（仪表盘→后台），一屏内位置不漂。
//
// 导航表外的卡（amv / amv.hcl / kline / paper）**不进侧栏**：它们在旧版都不是独立页面
// （K 线是弹窗、amv/HCL 是仪表盘子块、纸面是口径切换）。它们仍可由
// 搜索（搜索点股票直接开 K 线卡）、卡片内跳转（panel:open-card / panel:open-kline）、
// `?card=pluginId::cardId` 打开——能到，但不占导航位。
const NAV = [
  { key: 'dashboard::dashboard.main',       label: '仪表盘',
    icon: '<path d="M4 19a8 8 0 0 1 16 0"/><path d="M12 19l4.5-5"/>' },
  { key: 'alert::alert.main',               label: '预警',
    icon: '<path d="M12 3.5a5.5 5.5 0 0 0-5.5 5.5c0 4-1.5 5.5-1.5 5.5h14s-1.5-1.5-1.5-5.5A5.5 5.5 0 0 0 12 3.5z"/><path d="M9.6 18.4a2.6 2.6 0 0 0 4.8 0"/>' },
  { key: 'intraday::intraday.main',         label: '盘中',
    icon: '<path d="M3 12h3.5l2.5-7 4 14 2.5-7H21"/>' },
  { key: 'sector::sector.board',            label: '板块',
    icon: '<rect x="3" y="3" width="7.5" height="7.5" rx="1.2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.2"/>' },
  { key: 'concept::concept.main',           label: '概念',
    icon: '<path d="M12 3a6 6 0 0 0-3.8 10.6c.6.5.9 1.2.9 1.9h5.8c0-.7.3-1.4.9-1.9A6 6 0 0 0 12 3z"/><path d="M9.5 18.5h5M10.5 21h3"/>' },
  { key: 'review::review.main',             label: '复盘',
    icon: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M8 3v15"/>' },
  { key: 'watchlist::watchlist.main',       label: '自选池',
    icon: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>' },
  // 2026-11-15（标注 N94 主人）：模拟交易不占侧栏、不做独立页面——它只在 K 线弹窗里下单用。
  // 卡与插件本身保留（`?card=paper::paper.main` 仍可打开排障），K 线下单走 paper 后端，不依赖这张卡。
  { key: 'selection::selection.main',       label: '选股',
    icon: '<path d="M4 5h16"/><path d="M6 10h12"/><path d="M8 15h8"/><path d="M10 20h4"/>' },
  // 2026-10-30 撤掉两个入口（主人定）：
  //   · 打分 —— 已融入「板块详情个股 / 选股 / 自选池」三处，不再单独占导航位；
  //   · 情绪打分 —— 已由「情绪温度计」取代。
  // 两个插件本身**保留**：打分仍被那三处只读依赖（/api/p/score/map），
  // 情绪仍被温度计依赖（emotion.payload）。撤的只是侧栏入口，卡片仍可由
  // `?card=score::score.main` / `?card=emotion::emotion.main` 直接打开，便于排障。
  //
  // 另：K 线卡（kline::kline.main）同样**不在 NAV**——完整 K 线走壳层弹窗
  // （ctxOpenKline → 插件 openKlineStandalone，旧版 KLineApp 移植）。那张简化卡
  // 只留作 `?card=kline::kline.main` 排障入口，也是「插件是否装载」的锚点；
  // 搜索选股 2026-10-31 已从「切这张卡」改为「弹窗」（之前两条入口打开的界面不一样）。
  // 2026-10-29 新增：情绪温度计 = 一屏看完「读数 → 动作」（四格 KPI / 温度计 / 盘面格 /
  // 今日 vs 昨日 / 60 日趋势含恐慌口径断点 / 预警 / 该做什么 / 信号→动作）。
  // 只消费 emotion 与 /api/panic 两个已有接口，不重算任何指标。图标取带刻度的温度计。
  { key: 'thermometer::thermometer.main',   label: '情绪温度计',
    icon: '<path d="M9.2 3.6a2.8 2.8 0 0 1 5.6 0v8.5a3.9 3.9 0 1 1-5.6 0z"/><path d="M12 8.6v6.6"/><path d="M15.2 6.4h3M15.2 9.2h2.2M15.2 12h3"/>' },
  // 2026-10-29 新增：估值 = PEG 估值选股仪表盘（外部脚本产物，core 只读直挂 /peg/）。
  // 图标取「准星」——仪表盘本体叫「低估成长股雷达」，与旧版图标表无关，故新画一个。
  { key: 'valuation::valuation.main',       label: '估值',
    icon: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.6"/><path d="M12 3.5v3.2M12 17.3v3.2M3.5 12h3.2M17.3 12h3.2"/>' },
  { key: 'ops::ops.main',                   label: '后台',
    icon: '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><circle cx="7" cy="7" r="1"/><circle cx="7" cy="17" r="1"/>' },
];
const state = {
  plugins: [],
  cards: [],            // { pluginId, pluginName, ...card } —— 参与侧栏渲染的那份
  allCards: [],         // 全量卡片：`?card=` 单卡模式下 cards 被过滤成 1 张，
                        // 但卡间跳转（dashboard 徽标 → 作战表）仍要能查到别的卡，故留这一份
  modules: new Map(),   // pluginId -> module namespace
  active: null,         // 当前挂载的卡片实例
  theme: lsGetAny(LS_THEMES, DEFAULT_THEME),
  fs: lsGetAny(LS_FSS, DEFAULT_FS),
  engineCode: null,
  statusTimer: null,
  note: null,           // 标注模块（/static/v2_note.js，见 bindNote）；就绪前为 null
  notesPanel: null,     // 标注清单面板（/static/v2_notes_panel.js，见 bindNotesPanel）
};

const el = {
  railTabs: document.getElementById('rail-tabs'),
  railSearch: document.getElementById('rail-search'),
  railNote: document.getElementById('rail-note'),   // 2026-10-28 起已移除（标注入口改悬浮按钮）
  railNotes: document.getElementById('rail-notes'),
  railGear: document.getElementById('rail-gear'),
  drawer: document.getElementById('drawer'),
  scrim: document.getElementById('scrim'),
  drawerClose: document.getElementById('drawer-close'),
  themeSel: document.getElementById('theme-sel'),
  fsSeg: document.getElementById('fs-seg'),
  stat: document.getElementById('stat'),
  statLab: document.getElementById('stat-lab'),
  statData: document.getElementById('stat-data'),
  statTs: document.getElementById('stat-ts'),
  statHint: document.getElementById('stat-hint'),
  btnKill: document.getElementById('btn-kill'),
  btnRestart: document.getElementById('btn-restart'),
  btnReload: document.getElementById('btn-reload'),
  actMsg: document.getElementById('act-msg'),
  svcMsg: document.getElementById('svc-msg'),
  stage: document.getElementById('stage'),
  empty: document.getElementById('empty'),
  banner: document.getElementById('banner'),
  title: document.getElementById('main-title'),
  sub: document.getElementById('main-sub'),
  version: document.getElementById('core-version'),
  sPanel: document.getElementById('s-panel'),
  sInput: document.getElementById('s-input'),
  sBody: document.getElementById('s-body'),
  sClose: document.getElementById('s-close'),
  toast: document.getElementById('toast'),
};

// ---------------------------------------------------------------- 工具

function lsGet(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (err) { return fallback; }
}

function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch (err) { /* 隐私模式写不进去：忽略 */ }
}

// 多键读取：按顺序取第一个非空（旧界面键优先，见文件头 LS_THEMES 注释）。
function lsGetAny(keys, fallback) {
  for (const key of keys) {
    const v = lsGet(key, null);
    if (v) return v;
  }
  return fallback;
}

// 多键写入：两套键同时写，另一侧壳读哪一套都能看到同一偏好。
function lsSetAll(keys, value) {
  for (const key of keys) lsSet(key, value);
}

function showBanner(text) {
  if (!text) {
    el.banner.hidden = true;
    el.banner.textContent = '';
    return;
  }
  el.banner.hidden = false;
  el.banner.textContent = text;
}

let toastTimer = null;
function toast(text) {
  if (!el.toast) return;
  el.toast.textContent = text;
  el.toast.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.toast.hidden = true; }, 3200);
}

function setMsg(node, text, ok) {
  if (!node) return;
  node.textContent = text || '';
  node.className = 'msg ' + (ok ? 'ok' : 'err');
}

function makeErrorCard(title, detail) {
  const box = document.createElement('div');
  box.className = 'card is-error';
  const h = document.createElement('div');
  h.className = 'card-error-title';
  h.textContent = title;
  const d = document.createElement('div');
  d.className = 'card-error-detail';
  d.textContent = String(detail && detail.stack ? detail.stack : detail);
  box.append(h, d);
  return box;
}

function pluginBase(id) {
  return '/plugins/' + encodeURIComponent(id) + '/';
}

function clearStage() {
  for (const child of Array.from(el.stage.children)) {
    if (child !== el.empty) child.remove();
  }
  el.empty.hidden = false;
  el.empty.textContent = '';
}

function makeCtx(plugin, card) {
  const base = '/api/p/' + encodeURIComponent(plugin.id);
  const args = pendingArgs;
  return {
    plugin: { id: plugin.id, name: plugin.name, version: plugin.version },
    card: { id: card.id, title: card.title, args: args },
    api: {
      base: base,
      async get(path, init) {
        const url = path.startsWith('http') || path.startsWith('/api/') ? path : base + path;
        const res = await fetch(url, Object.assign({ headers: { Accept: 'application/json' } }, init || {}));
        const text = await res.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (err) { data = text; }
        if (!res.ok) {
          const detail = data && data.detail ? data.detail : text || res.statusText;
          const e = new Error('GET ' + url + ' -> ' + res.status + ': ' + detail);
          e.status = res.status;
          e.payload = data;
          throw e;
        }
        return data;
      },
      // 批次 1a（docs/task_fe_batch1a.md §契约 1）：补写通道，与 get 同构——
      // 绝对路径（http / /api/ 开头）不拼插件前缀；body 走 JSON；非 2xx 抛错带 detail。
      // 插件自己域内的写端点仍用相对路径（/add 之类）自动拼 /api/p/<id>；
      // 兼容层（compat_watchlist）的写端点用绝对 /api/watchlist/* 直接消费。
      async post(path, body, init) {
        const url = path.startsWith('http') || path.startsWith('/api/') ? path : base + path;
        const opts = Object.assign({
          method: 'POST',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(body == null ? {} : body),
        }, init || {});
        const res = await fetch(url, opts);
        const text = await res.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (err) { data = text; }
        if (!res.ok) {
          const detail = data && data.detail ? data.detail : text || res.statusText;
          const e = new Error('POST ' + url + ' -> ' + res.status + ': ' + detail);
          e.status = res.status;
          e.payload = data;
          throw e;
        }
        return data;
      },
    },
    log: function () { console.log.apply(console, ['[' + plugin.id + '/' + card.id + ']'].concat(Array.from(arguments))); },
  };
}

// ---------------------------------------------------------------- 主题 / 字号（照搬旧壳 Shell.applyTheme）
//
// 主题清单唯一源 = themes.css 的 --theme-manifest（格式 `code|中文名|层级|描述`，条目以 ; 分隔，
// 整串是带引号的 CSS 字符串 token）。旧壳的解析契约逐字照搬：剥首尾引号 → 按 ; 切条目 → 按 | 切字段。
// 禁在 JS 里手写第二份主题表。
function readManifest() {
  try {
    let raw = getComputedStyle(document.documentElement).getPropertyValue('--theme-manifest') || '';
    raw = raw.trim().replace(/^["']|["']$/g, '').trim();
    if (!raw) return [];
    return raw.split(';').map(function (entry) {
      const parts = entry.split('|');
      const code = (parts[0] || '').trim();
      return code ? { code: code, name: (parts[1] || '').trim() || code } : null;
    }).filter(Boolean);
  } catch (err) { return []; }
}

function buildThemeSel() {
  const sel = el.themeSel;
  if (!sel) return;
  const manifest = readManifest();
  // 首帧竞态：外部样式表可能还没解析完 → --theme-manifest 读不到。此时**不能**把用户已存的
  // 主题改写成默认值（否则 shell_theme=warmpaper 的用户会静默丢偏好），只放一个占位项，
  // 等 window load 再重建（下面 load 监听里会重新调用本函数）。
  if (!manifest.length) {
    sel.textContent = '';
    const opt = document.createElement('option');
    opt.value = state.theme;
    opt.textContent = state.theme;
    sel.append(opt);
    sel.value = state.theme;
    sel.onchange = function () {
      state.theme = sel.value;
      lsSetAll(LS_THEMES, state.theme);
      applyTheme();
      pushThemeToFrames();
    };
    return;
  }
  const themes = manifest;
  sel.textContent = '';
  for (const t of themes) {
    const opt = document.createElement('option');
    opt.value = t.code;
    opt.textContent = t.name;
    sel.append(opt);
  }
  // 本地记的主题在清单里不存在（主题被删）→ 回落到第一条，避免挂一个空主题名
  if (!themes.some(function (t) { return t.code === state.theme; })) state.theme = themes[0].code;
  sel.value = state.theme;
  sel.onchange = function () {
    state.theme = sel.value;
    lsSetAll(LS_THEMES, state.theme);
    applyTheme();
    pushThemeToFrames();
  };
}

function buildFsSeg() {
  const seg = el.fsSeg;
  if (!seg) return;
  const buttons = Array.from(seg.querySelectorAll('button'));
  const mark = function () {
    for (const b of buttons) b.classList.toggle('active', b.dataset.fs === state.fs);
  };
  for (const b of buttons) {
    b.onclick = function () {
      state.fs = b.dataset.fs;
      lsSetAll(LS_FSS, state.fs);
      mark();
      applyTheme();
      pushThemeToFrames();
    };
  }
  mark();
}

function applyTheme() {
  // 与旧壳同口径：[data-theme]/[data-fs] 一设，themes.css 的语义层自动整体换肤，
  // 壳与内容卡共用同一份 token，无需在此复制色表。
  document.documentElement.setAttribute('data-theme', state.theme);
  document.documentElement.setAttribute('data-fs', state.fs);
}

// 主题/字号变化推给子 frame
//
// 场景：本壳的卡片一都在本页（无 iframe）；但 v2 壳也可能被再嵌一层（例如旧页用 iframe 嵌 /v2/），
// 且旧界面自己在抽屉里切主题时只改父页 `data-theme`，iframe 不会跟。
// 两条都要接上：
//   ① 本壳切主题 → 把自己嵌的 iframe 全推一遍（本壳目前不嵌 iframe，留作通用处理）；
//   ② 父页切主题 → 父页发 `hana:theme` 消息，本壳收到后套用（见下面 bindThemeFromParent）。
// 不用 localStorage 做同步：storage 事件不来同文档，而且用户可能同一秒切好几次。
const THEME_MSG = 'hana:theme';

function pushThemeToFrames() {
  const frames = document.querySelectorAll('iframe');
  for (const f of frames) {
    try {
      if (f.contentWindow) {
        f.contentWindow.postMessage({ type: THEME_MSG, theme: state.theme, fs: state.fs }, '*');
      }
    } catch (err) { /* 跨域 frame：推不到就算了，它自己会读 localStorage */ }
  }
}

// ---------------------------------------------------------------- 页面标注（2026-10-26）
//
// 只做三件事，选择/高亮/提交全在 /static/v2_note.js：
//   ① 入口由模块自己挂（2026-10-28 起不再用 rail 按钮：K 线弹窗 z=100 会压住 rail，
//      主人要在 K 线图上标注时点不到按钮；模块改为悬浮可拖动按钮，z 高于弹窗）；
//   ② 告诉它「当前挂着哪张卡」（page 字段 = v2:card:<卡 key>，决定标注归属与 pin 只画本卡）；
//   ③ 模式开启时收起设置抽屉/搜索面板 —— 它们盖在卡片上，开着只能选到浮层本身。
//
// 为什么行为不写在壳里：标注要拦 capture 阶段的 click/dblclick、要在高亮层上跟鼠标，
// 这些逻辑与壳的其它chrome无关，放一起只会让 shell.js 继续变胖（已 1400 行）。
// 载入方式用动态 import（与 shell.html 的 <script> 同 URL，模块表按 URL 去重 → 同一实例），
// 失败时退回 window.__v2Note（模块先执行完的时序），两条都不通就静默降级：没有标注按钮，
// 其余功能不受影响。
async function bindNote() {
  let mod = null;
  try {
    mod = await import('/static/v2_note.js');
  } catch (err) {
    console.warn('[shell] 标注模块加载失败（标注按钮不可用，其它不受影响）', err);
  }
  const note = (mod && mod.default) || window.__v2Note || null;
  if (!note || typeof note.init !== 'function') return null;
  state.note = note.init({
    cardKey: function () { return state.active ? state.active.key : ''; },
    onMode: function (on) {
      if (!on) return;
      closeSettings();
      if (el.sPanel && !el.sPanel.hidden) closeSearch();
    },
  });
  return state.note;
}

// 标注入口的重算（模块就绪前调用无效果，就绪后由 bindNote 自己算一次）。
function syncNoteEntry() {
  if (state.note && typeof state.note.syncEntry === 'function') state.note.syncEntry();
}

// ---------------------------------------------------------------- 标注清单（2026-10-27）
//
// 壳只做三件事，面板自己的渲染/请求/定位全在 /static/v2_notes_panel.js：
//   ① 把 #rail-notes 交给它当入口；
//   ② 传 selectCard（跨卡定位要真切卡，面板不能自己造一条切卡路径）；
//   ③ 传 cardKey / cardTitle（page `v2:card:<key>` → 卡名）与 onOpen（开面板时关设置抽屉）。
//
// 为什么标题从 allCards 查而不是 DOM：`?card=` 单卡模式下 state.cards 只剩一张，
// 但清单里会有别的卡的标注 —— 用 allCards 才能把「盘中」这类人话显示出来。
// 不复制 second 张数据源：allCards 就是壳的卡片全量（boot 里赋值，含 key/title）。
async function bindNotesPanel() {
  let mod = null;
  try {
    mod = await import('/static/v2_notes_panel.js');
  } catch (err) {
    console.warn('[shell] 标注清单面板加载失败（清单入口不可用，其余不受影响）', err);
  }
  const panel = (mod && mod.default) || window.__v2NotesPanel || null;
  if (!panel || typeof panel.init !== 'function') return null;
  const pool = function () { return state.allCards.length ? state.allCards : state.cards; };
  state.notesPanel = panel.init({
    button: el.railNotes,
    selectCard: selectCard,
    cardKey: function () { return state.active ? state.active.key : ''; },
    cardTitle: function (key) {
      const meta = pool().find(function (c) { return c.key === key; });
      return meta ? meta.title : '';
    },
    toast: toast,
    onOpen: function () {
      // 三个壳层浮层共用一块遮罩与同一片空间：开面板时收起设置抽屉与搜索面板。
      // 不关标注模式：那一个不是浮层（是就地选元素），关了反而丢掉正在写的那一条。
      closeSettings();
      if (el.sPanel && !el.sPanel.hidden) closeSearch();
    },
  });
  return state.notesPanel;
}

// 父页（旧界面）切主题时同步过来。父页只在切主题那一刻发一次，
// 本壳收到就直接套用，**不写 localStorage**（偏好仍由父页那次操作负责落盘）。
function bindThemeFromParent() {
  window.addEventListener('message', function (event) {
    const d = event.data || {};
    if (d.type !== THEME_MSG) return;
    if (window.parent !== window && event.source !== window.parent) return;  // 只认父页
    if (typeof d.theme === 'string' && d.theme) state.theme = d.theme;
    if (typeof d.fs === 'string' && d.fs) state.fs = d.fs;
    applyTheme();
    if (el.themeSel && state.theme) el.themeSel.value = state.theme;
  });
}

// ---------------------------------------------------------------- 侧边栏（rail）
function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function railIcon(paths) {
  // 导航表里的图标 path（逐条照旧版 RAIL_ICONS）；不引图标库、不加依赖。
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
    + 'stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>';
}

function renderNav() {
  const wrap = el.railTabs;
  if (!wrap) return;
  wrap.textContent = '';
  const pool = state.allCards.length ? state.allCards : state.cards;
  const items = navItems(pool);
  if (!items.length) {
    const tip = document.createElement('div');
    tip.className = 'sp-empty';
    tip.textContent = state.plugins.length ? '无卡片' : '无插件';
    wrap.append(tip);
    return;
  }
  for (const it of items) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'rail-btn';
    btn.dataset.cardKey = it.key;
    btn.setAttribute('aria-label', it.label);
    btn.title = it.label;
    btn.innerHTML = railIcon(it.icon) + '<span class="tip">' + escapeHtml(it.label) + '</span>';
    btn.addEventListener('click', function () { selectCard(it.key); });
    wrap.append(btn);
  }
  syncNavActive();
}

/** 导航表 ∩ 已装载卡片：保持旧版顺序，缺卡（插件禁用/未装）就跳过那一位，不留空位。 */
function navItems(pool) {
  const out = [];
  for (const nav of NAV) {
    if (!pool.some(function (c) { return c.key === nav.key; })) continue;
    out.push({ key: nav.key, label: nav.label, icon: nav.icon });
  }
  return out;
}

function syncNavActive() {
  const activeKey = state.active ? state.active.key : null;
  if (!el.railTabs) return;
  for (const btn of el.railTabs.querySelectorAll('.rail-btn')) {
    const on = btn.dataset.cardKey === activeKey;
    btn.setAttribute('aria-current', String(on));
    btn.classList.toggle('active', on);
  }
}

// ---------------------------------------------------------------- 卡片挂载
// 静态资源构建号（2026-11-12）：插件 JS 的 ?v= 只跟 manifest 版本，发版不动 manifest 时
// 浏览器会拿旧 JS（启发式缓存）。这里追加一个构建号，页面刷新即换新，不依赖用户硬刷新。
const ASSET_B = '&b=20261113i';
async function loadModule(plugin) {
  if (state.modules.has(plugin.id)) return state.modules.get(plugin.id);
  // 带 manifest 版本号做缓存键：插件升级（version 变）后浏览器必拉新 JS，
  // 不依赖用户硬刷新。同一页面内重复选中只 import 一次（state.modules 已缓存）。
  const url = pluginBase(plugin.id) + plugin.frontend.module + '?v=' + encodeURIComponent(plugin.version || '0') + ASSET_B;
  const mod = await import(/* @vite-ignore */ url);
  state.modules.set(plugin.id, mod);
  return mod;
}

function resolveCard(mod, card) {
  const def = mod && mod.default;
  const list = def && Array.isArray(def.cards) ? def.cards : [];
  return list.find(function (c) { return c && c.id === card.id; }) || null;
}

async function unmountActive() {
  if (!state.active) return;
  stopRefreshTimer();
  const current = state.active;
  state.active = null;
  try {
    if (current.card && typeof current.card.unmount === 'function') {
      await current.card.unmount();
    }
  } catch (err) {
    console.warn('[shell] unmount 失败', current.key, err);
  }
  if (current.host && current.host.parentNode) current.host.remove();
}

// ---------------------------------------------------------------- 自动刷新
//
// 卡片可选声明 `refreshEvery`（毫秒）；声明了才定期调它的 `refresh()`。
// 不统一给所有卡片定时：板块卡一次重算几千行、数据又是 T-1，每 30 秒重算纯浪费，
// 所以「要不要刷新」由卡片自己声明。页面不可见时跳过。
let refreshTimer = null;

function stopRefreshTimer() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function startRefreshTimer(card) {
  stopRefreshTimer();
  const every = Number(card && card.refreshEvery) || 0;
  if (every <= 0 || typeof card.refresh !== 'function') return;
  refreshTimer = setInterval(async function () {
    if (document.visibilityState !== 'visible') return;
    if (!state.active || state.active.card !== card) return;
    try {
      await card.refresh();
    } catch (err) {
      console.warn('[shell] refresh 失败', err);
    }
  }, every);
}

// 卡间跳转入参：卡内发起「打开另一张卡」时可带参数（如 K 线卡要看的代码）。
// 实现成一次性的待传参，**不往卡片内部写全局**：makeCtx 把它作为 ctx.card.args 交出去，
// 卡片自己在 mount 时读。没有入参时恒为 null（卡片按默认行为走）。
let pendingArgs = null;

async function selectCard(key, args) {
  // 查全量卡片（allCards）：`?card=` 单卡模式下 state.cards 只剩 1 张，但卡间跳转仍要能切到别的卡
  // （仪表盘徽标 → 作战表）。state.cards 只决定侧栏渲染什么，不决定「能挂什么」。
  const pool = state.allCards.length ? state.allCards : state.cards;
  const meta = pool.find(function (c) { return c.key === key; });
  if (!meta) return;

  pendingArgs = args || null;

  await unmountActive();
  clearStage();
  el.empty.hidden = true;

  el.title.textContent = meta.title;
  // 2026-11-13 主人：卡头双标题重复很讨厌（标题「自选池」+ 副标题「自选池 · watchlist」）——
  //   插件名与卡标题相同时副标题只留 id（如 `watchlist`），不同时才保留「插件名 · id」。
  {
    const cardTitle = String(meta.title || '').trim();
    const pname = String(meta.pluginName || '').trim();
    el.sub.textContent = (pname && pname !== cardTitle)
      ? (pname + ' · ' + String(meta.pluginId || ''))
      : String(meta.pluginId || '');
  }

  const host = document.createElement('div');
  host.className = 'card-host';
  host.dataset.cardKey = meta.key;
  el.stage.append(host);

  state.active = { key: meta.key, card: null, host: host };
  syncNavActive();

  let impl;
  try {
    const mod = await loadModule(meta.plugin);
    impl = resolveCard(mod, meta);
    if (!impl) throw new Error('frontend.js 中未找到卡片 id=' + meta.id);
    if (typeof impl.mount !== 'function') throw new Error('卡片 ' + meta.id + ' 缺少 mount(el, ctx)');
  } catch (err) {
    console.error('[shell] 加载卡片失败', meta.key, err);
    el.stage.append(makeErrorCard('卡片 ' + meta.title + ' 加载失败', err));
    state.active = { key: meta.key, card: null, host: host };
    return;
  }

  state.active.card = impl;
  try {
    await impl.mount(host, makeCtx(meta.plugin, impl));
    pendingArgs = null;            // 一次性：只交给本次挂载的卡
    startRefreshTimer(impl);
    // 2026-11-06（P1）：标注圆点按 page 归属，切卡后必须重拉本卡标注 —— 否则新卡的标注
    //   不出现、上一张卡的圆点还挂在屏幕上（实测：选股卡该有 1 个，切过去是 0；切走后旧点残留）。
    //   放在 mount 之后：圆点要靠新卡的 DOM 才能定位，挂之前拉也查不到。
    if (state.note && typeof state.note.refresh === 'function') {
      try { state.note.refresh(); } catch (noteErr) { /* 标注层出错不影响卡片 */ }
    }
  } catch (err) {
    console.error('[shell] mount 失败', meta.key, err);
    host.textContent = '';
    host.append(makeErrorCard('卡片 ' + meta.title + ' 渲染失败', err));
  }
}

// ---------------------------------------------------------------- 设置抽屉
function openSettings() {
  if (el.drawer) el.drawer.classList.add('on');
  if (el.scrim) el.scrim.classList.add('on');
  refreshStatus();
}

function closeSettings() {
  if (el.drawer) el.drawer.classList.remove('on');
  if (el.scrim) el.scrim.classList.remove('on');
}

function toggleSettings() {
  const on = el.drawer && el.drawer.classList.contains('on');
  if (on) closeSettings(); else openSettings();
}

// ---------------------------------------------------------------- 引擎状态（代理 /api/shell/status）
async function refreshStatus() {
  let data = null;
  try {
    const res = await fetch('/api/shell/status', { cache: 'no-store' });
    if (res.ok) data = await res.json();
  } catch (err) { /* 状态端点不可达 → 按 DOWN 渲染，不猜 */ }

  const eng = (data && data.engine) || { code: 'DOWN', label: '服务未启动' };
  const code = eng.code || 'DOWN';
  state.engineCode = code;
  const cls = STATUS_CLASS[code] || 's-readonly';
  if (el.railGear) el.railGear.className = 'rail-btn ' + cls;
  if (el.stat) el.stat.className = 'stat ' + cls;
  if (el.statLab) el.statLab.textContent = eng.label || STATUS_LABEL[code] || code;
  if (el.statTs) el.statTs.textContent = eng.updated_at || '';
  // 数据日（2026-11-07）：永远把「看到的是哪一天」亮出来，不只给状态词。
  // 来源 /api/shell/status.data_clock（core.trade_clock.status）；不可用就留空，不猜。
  if (el.statData) {
    const dc = (data && data.data_clock) || {};
    const d8 = String(dc.expected_data_date || '');
    if (/^\d{8}$/.test(d8)) {
      el.statData.textContent = '数据日 ' + d8.slice(4, 6) + '-' + d8.slice(6, 8);
      const degraded = dc.degraded && dc.degraded.active ? (' · 日历降级：' + (dc.degraded.reason || '')) : '';
      el.statData.title = (dc.is_trading_day ? '交易日' : '非交易日')
        + ' · ' + (dc.postclose ? '盘后数据已固定' : '盘中 / 数据未固定')
        + ' · 期望数据日 ' + d8 + degraded;
    } else {
      el.statData.textContent = '';
      el.statData.title = '';
    }
  }

  const down = code === 'DOWN';
  if (el.statHint) {
    el.statHint.textContent = down
      ? '旧 8800 引擎离线 —— v2 壳与设置仍可用，可在下方「服务」里拉起引擎。'
      : ('交易模式：' + (eng.trade_mode || '—') + (eng.kill_switch_active ? ' · 急停生效中' : ''));
  }
  if (el.btnKill) {
    // 与旧壳同口径：引擎离线时急停按钮置灰（此时打了也只会拿到 panel_down）
    el.btnKill.disabled = down;
    el.btnKill.textContent = code === 'FROZEN' ? '⛔ 解除急停' : '⛔ 急停';
  }
}

// ---------------------------------------------------------------- 操作（急停 / 重启）
//
//   2026-11-07：签收链整体删除（主人拍板：系统只有模拟盘，签收是空壳）——`doSign` 与
//   `#btn-sign` 一并移除，不再保留隐藏入口。
//   POST /api/shell/kill     → 转发到本进程 /api/kill（body 透传）
//   POST /api/shell/restart  → core 路由，重启 8810 自身（2026-10-29）：
//                                起分离 helper → 延迟 2s 杀进程 + ensure 拉起；
//                                本页收到 ok 后轮询 /api/health，恢复即 location.reload()
async function doKill() {
  const frozen = state.engineCode === 'FROZEN';
  if (!confirm(frozen ? '确认解除急停？解除后引擎恢复运行。' : '确认执行急停？冻结引擎，禁止一切下单动作。')) return;
  try {
    const res = await fetch('/api/shell/kill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: !frozen, reason: frozen ? '宿主解除急停' : '宿主急停' }),
    });
    const data = await res.json();
    setMsg(el.actMsg, data.ok ? (frozen ? '已解除急停' : '已执行急停')
      : (data.detail || data.error || '操作失败'), !!data.ok);
  } catch (err) {
    setMsg(el.actMsg, '操作失败：' + err.message, false);
  }
  refreshStatus();
}

async function doRestart() {
  if (!confirm('确认重启作战面板（8810）？约 3–6 秒页面不可用，就绪后本页会自动刷新。')) return;
  const btn = el.btnRestart;
  if (btn) btn.disabled = true;
  setMsg(el.svcMsg, '正在启动重启流程…', true);
  let helperPid = null;
  try {
    const res = await fetch('/api/shell/restart', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) {
      setMsg(el.svcMsg, '重启失败：' + (data.detail || data.error || ''), false);
      if (btn) btn.disabled = false;
      return;
    }
    helperPid = data.helper_pid;
    setMsg(el.svcMsg, '重启中（helper ' + (helperPid == null ? '—' : helperPid) + '），请稍候…', true);
  } catch (err) {
    // 请求没回到（比如进程已先被杀）：也继续走健康轮询，不当作失败
    setMsg(el.svcMsg, '重启中，等待服务恢复…', true);
  }
  // helper 先等 2s 再杀进程，所以轮到自己的时候已经断开。
  // 必须看到「一次失败/非 ok」再看到 ok 才算重启完成，否则可能在旧进程上直接 reload。
  const t0 = Date.now();
  let sawDown = false;
  const tick = async function () {
    const waited = Date.now() - t0;
    if (waited > 90000) {
      setMsg(el.svcMsg, '90 秒未恢复，请看 data/panel_v2/logs/_restart.log', false);
      if (btn) btn.disabled = false;
      return;
    }
    try {
      const r = await fetch('/api/health', { cache: 'no-store' });
      if (!r.ok) {
        sawDown = true;
      } else {
        const b = await r.json();
        if (b && b.ok === true && sawDown) { location.reload(); return; }
        if (!b || b.ok !== true) sawDown = true;
      }
    } catch (err) {
      sawDown = true;
    }
    setTimeout(tick, 700);
  };
  setTimeout(tick, 1500);
}

// ---------------------------------------------------------------- 搜索面板（照搬旧壳 ShellSearch）
//
// 交互照搬旧壳：最近搜索（localStorage shell_recent_v1，最多 8 条）→ 输入 180ms 防抖 →
// POST /api/shell/search {q, limit} → 分区渲染（股票 / ETF、板块）。
// 差异（任务书 §3 定的）：v2 的导航单元是卡片 —— 选板块 → 打开 sector 卡；
// 选个股 → 打开 kline 卡；kline 卡目前没有入参接口，故先 toast 提示，不硬做。
const searchState = { timer: null };

function recentSearches() {
  try { return JSON.parse(localStorage.getItem(LS_RECENT) || '[]') || []; } catch (err) { return []; }
}

function pushRecent(item) {
  const list = recentSearches().filter(function (x) { return x.code !== item.code; });
  list.unshift(item);
  try { localStorage.setItem(LS_RECENT, JSON.stringify(list.slice(0, RECENT_MAX))); } catch (err) { /* 忽略 */ }
}

function searchRow(item) {
  // data-ctx / data-code / data-name：搜索结果直接进壳层右键菜单（与表格行同一套 ctxResolve 规则 1）。
  // 这样不打开 K 线也能「加入自选 / 新建分组 / 复制代码」（2026-11-15 主人要求）。
  return '<div class="sp-i" data-ctx="' + searchKindOf(item) + '" data-code="' + escapeHtml(item.code) + '" data-name="' + escapeHtml(item.name)
    + '" data-kind="' + escapeHtml(item.kind || '') + '">'
    + '<span class="sp-n">' + escapeHtml(item.name) + '</span>'
    + '<span class="sp-c">' + escapeHtml(item.code) + '</span>'
    + '<span class="sp-k">' + escapeHtml(item.kind || '') + '</span></div>';
}

function renderSearchRecent() {
  if (!el.sBody) return;
  const list = recentSearches();
  el.sBody.innerHTML = list.length
    ? ('<div class="sp-sec">最近搜索</div>' + list.map(searchRow).join(''))
    : '<p class="sp-empty">输入代码、名称或拼音首字母</p>';
}

function renderSearchResult(payload) {
  if (!el.sBody) return;
  const stocks = (payload && payload.stocks) || [];
  const sectors = (payload && payload.sectors) || [];
  if (!stocks.length && !sectors.length) {
    el.sBody.innerHTML = '<p class="sp-empty">没有匹配结果</p>';
    return;
  }
  let html = '';
  if (stocks.length) html += '<div class="sp-sec">股票 / ETF</div>' + stocks.map(searchRow).join('');
  if (sectors.length) html += '<div class="sp-sec">板块</div>' + sectors.map(searchRow).join('');
  el.sBody.innerHTML = html;
}

async function runSearch(q) {
  if (!q) { renderSearchRecent(); return; }
  try {
    const res = await fetch('/api/shell/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q: q, limit: SEARCH_LIMIT }),
    });
    renderSearchResult(await res.json());
  } catch (err) {
    if (el.sBody) el.sBody.innerHTML = '<p class="sp-empty">搜索失败：' + escapeHtml(err.message) + '</p>';
  }
}

function openSearch() {
  if (!el.sPanel) return;
  el.sPanel.hidden = false;
  if (el.sInput) { el.sInput.value = ''; el.sInput.focus(); }
  renderSearchRecent();
  document.addEventListener('mousedown', onSearchOutside, true);
}

function closeSearch() {
  if (el.sPanel) el.sPanel.hidden = true;
  document.removeEventListener('mousedown', onSearchOutside, true);
}

function onSearchOutside(event) {
  if (!el.sPanel || el.sPanel.hidden) return;
  if (el.sPanel.contains(event.target)) return;
  const target = event.target;
  if (target && typeof target.closest === 'function' && target.closest('#rail-search')) return;
  closeSearch();
}

function toggleSearch() {
  if (el.sPanel && !el.sPanel.hidden) closeSearch(); else openSearch();
}

function findCardByPlugin(pluginId) {
  // 同 selectCard：搜素栏在单卡模式下也要能定位到别的卡（搜索选中 → 开 K 线）
  const pool = state.allCards.length ? state.allCards : state.cards;
  return pool.find(function (c) { return c.pluginId === pluginId; }) || null;
}

// 搜索条目的类型判定：点开 K 线/板块、右键菜单、结果行 data-ctx 三处必须同一套口径。
function searchKindOf(item) {
  const code = String((item && item.code) || '');
  const isSector = (item && item.kind === '板块') || code.slice(0, 3) === '880' || code.slice(0, 3) === '881';
  return isSector ? 'sector' : 'stock';
}

function pickSearchItem(item) {
  if (!item || !item.code) return;
  pushRecent(item);
  closeSearch();
  const isSector = searchKindOf(item) === 'sector';
  if (isSector) {
    const card = findCardByPlugin('sector');
    if (card) { selectCard(card.key); toast('已打开板块卡：' + item.name); return; }
    toast('未装载板块卡：' + item.name);
    return;
  }
  // 2026-10-31：搜索选股改为与其它入口一致——就地弹完整 K 线弹窗（旧版 KLineApp 移植）。
  // 旧写法 `selectCard(kline 卡)` 会把页面切成那张简化 K 线卡：它 2026-10-30 已从侧栏撤掉
  // （淘汰），搜索反而成了它唯一的入口，于是「搜索进的 K 线」和「点股票名进的 K 线」
  // 是两个界面（主人 2026-10-31 报的就是这个）。
  if (findCardByPlugin('kline') && ctxOpenKline(item.code, item.name || '')) {
    toast('已打开 K 线：' + (item.name || '') + '（' + item.code + '）');
    return;
  }
  toast('已选中 ' + item.name + '（' + item.code + '）');
}

function bindSearch() {
  if (el.railSearch) el.railSearch.addEventListener('click', toggleSearch);
  if (el.sClose) el.sClose.addEventListener('click', closeSearch);
  if (el.sInput) {
    el.sInput.addEventListener('input', function () {
      if (searchState.timer) clearTimeout(searchState.timer);
      const q = el.sInput.value.trim();
      searchState.timer = setTimeout(function () { runSearch(q); }, 180);
    });
  }
  if (el.sBody) {
    el.sBody.addEventListener('click', function (event) {
      const row = event.target && event.target.closest ? event.target.closest('.sp-i') : null;
      if (!row) return;
      pickSearchItem({
        code: row.getAttribute('data-code'),
        name: row.getAttribute('data-name'),
        kind: row.getAttribute('data-kind'),
      });
    });
  }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      if (el.sPanel && !el.sPanel.hidden) closeSearch();
      else closeSettings();
    }
  });
}

// ---------------------------------------------------------------- 开 K 线/板块：转给父页（R5）
//
// 卡片里的个股名/板块芯片只写 data-open-kline / data-open-amv 属性占位（与旧页同一套标记），
// 真正的打开逻辑只存在于旧页（KLineApp.open / AmvCard.open）——卡内**不复刻**，发消息给父页，
// 由父页转发到那两个入口（历史上那个转发器是旧页里的 v2bridge.js；2026-10-26 首页收口后
// 旧页只留在 /legacy/，本页独立打开时走下面第二条路径）。
//
// 父页 origin：iframe 内只能从 document.referrer 拿到父页 URL，解析失败用 '*'（不用写死端口）。
//
// 两种形态都要装委托（2026-10-25 修「板块详情页个股左键点不开 K 线」）：
//   · 被嵌入（window.parent !== window）→ postMessage 转发给宿主页（旧页或外部壳）；
//   · 独立打开（window.parent === window）→ 没有父页可转，改走**本页路径**：直接调
//     ctxOpenKline / ctxOpenSector（与右键菜单同一套实现：切 v2 K 线卡 / 板块卡详情弹窗）。
//     原来这里直接 return false，/v2/ 直开时左键委托根本不装，卡片里的 [data-open-kline]
//     与 [data-open-amv] 点了毫无反应。
const PM_KLINE = 'v2:openKline';
const PM_AMV = 'v2:openAmv';

function parentOrigin() {
  try {
    if (document.referrer) {
      const u = new URL(document.referrer);
      if (u.origin && u.origin !== 'null') return u.origin;
    }
  } catch (err) { /* 解析失败 → '*' */ }
  return '*';
}

// 板块详情弹窗的根（.sector-modal，sector/frontend/detail.js:325 挂在 document.body 上）。
// 为什么要在这里认它：这个弹窗不在 el.stage 内，板块卡 unmount 也不带走它，而它是
// position:fixed; inset:0; z-index:50 的全屏遮罩 —— 左键在弹窗里开了 K 线卡，新卡挂在
// stage 里、被遮罩整个盖住，用户看到的还是那个弹窗，等于「点了没反应」。
// 只认这一个类名（不泛指 body 下任意子节点），壳自己的 ctx-menu / toast 不受影响。
function sectorModalOf(node) {
  return node && node.closest ? node.closest('#sector-modal, .sector-modal') : null;
}

// 浮层栈（2026-10-28）：K 线弹窗与板块详情可以**叠加**，Esc / 点空白只关最上层。
//
// 为什么要有栈：两条链路各自在 document 上听 Esc（旧 K 线模块与 sector/detail.js），
// 同时开着时按一次 Esc 会两层一起关。壳只提供最小栈，不接管各家的关闭逻辑：
//   · 打开方 push(id)，关闭方 pop(id)，同一 id 只留一份（两类弹窗各自单例）；
//   · Esc 的“只关最上层”由上层自己判断 top() === 自己的 id 后 stopImmediatePropagation；
//   · 跨模块共享走 window，避免插件反向 import 壳（插件只读这一页契约）。
function overlayStack() {
  if (!window.__v2Overlay) {
    const stack = [];
    // 2026-10-28（主人标注 43）：先开的层 z-index 必须低于后开的层——
    // 旧版只靠 CSS（K 线 100、板块详情 auto），从自选池先开 K 线再右键开详情时，详情被盖住。
    // 壳不接管各层关闭逻辑，只在 push/pop 时按栈序重排 z-index。
    const Z_BASE = 100;
    const Z_STEP = 5;
    const elOf = function (id) {
      if (id === 'kline') return document.getElementById('klineModal');
      if (id === 'sector') return document.getElementById('sector-modal');
      return null;
    };
    const syncZ = function () {
      for (let i = 0; i < stack.length; i++) {
        const el = elOf(stack[i]);
        if (el) el.style.zIndex = String(Z_BASE + i * Z_STEP);
      }
    };
    window.__v2Overlay = {
      stack: stack,
      push: function (id) {
        const i = stack.indexOf(id);
        if (i >= 0) stack.splice(i, 1);
        stack.push(id);
        syncZ();
      },
      pop: function (id) {
        const i = stack.indexOf(id);
        if (i >= 0) stack.splice(i, 1);
        syncZ();
      },
      top: function () { return stack.length ? stack[stack.length - 1] : null; },
    };
  }
  return window.__v2Overlay;
}
overlayStack();

// 收掉弹窗：优先点它自己的 ✕（走插件 close()，会顺手摘掉 document 上的 keydown 监听），
// 找不到 ✕ 才直接 remove。
// 只用于「再开一层同类别的东西」与嵌入旧页的 K 线分支；独立打开 K 线时**不再收掉**
// 板块详情（2026-10-28 主人：板块详情里点个股开 K 线，详情卡片不该消失）。
function closeSectorModal(modal) {
  if (!modal || !modal.parentNode) return;
  const x = modal.querySelector ? modal.querySelector('.card-x') : null;
  if (x && typeof x.click === 'function') x.click();
  // ✕ 还没绑上处理器（innerHTML 刚换、bindCard 尚未跑）时上面那句是空转 → 直接摘节点兜底。
  // 插件 close() 是幂等的（内部 closed 标志），这里再 remove 一次不会重复触发。
  if (modal.parentNode) modal.remove();
}

// 标注模式开着时，左键一律归标注用（点元素写意见），不得同时开卡。
// 为什么必须在这里判：两者都是 document 上的 **capture** 监听，谁先注册谁先跑
// （本函数在启动序列里早于异步的 bindNote）；且 capture 监听之间 stopPropagation 拦不住
// 同一节点上的其它监听 —— 只靠 v2_note.js 单方面拦，点到 [data-open-kline] 会既开卡又弹标注框。
function noteModeOn() {
  return !!(state.note && typeof state.note.isOn === 'function' && state.note.isOn());
}

// 股票代码规范化（2026-10-28）：板块详情等卡片只给 6 位数字（`300432`），
// 而旧 K 线弹窗的 F10 面板只认带交易所后缀的 ts_code（`300432.SZ`，kline_f10.js::isStockCode），
// 结果就是「图能画、F10 卡片不弹」（主人在板块详情里点个股时发现）。
// 壳在唯一入口处补齐后缀，K 线标题、列表上下文、F10 判定三者用同一份代码。
// 已带后缀（.SH/.SZ/.BJ/.TI 等）一律原样返回，不猜。
function normalizeStockCode(code) {
  const s = String(code || '').trim();
  if (!s || s.indexOf('.') >= 0 || !/^\d{6}$/.test(s)) return s;
  const head = s.charAt(0);
  if (head === '6' || head === '5') return s + '.SH';   // 沪市主板/科创/沪基金
  if (head === '4' || head === '8') return s + '.BJ';   // 北交所
  if (/^92\d{4}$/.test(s)) return s + '.BJ';            // 北交所 920 号段
  if (head === '9') return s + '.SH';                   // 沪 B 股
  return s + '.SZ';                                     // 0/1/2/3：深市主板/创业板/深基金
}

// 打开 K 线时的同列表上下文（旧版 `KLineApp.open(code, name, list)` 的第三参）。
//
// 旧版在选股页打开 K 线后可以用 ↑↓ 翻同列表的票（kline_lc.js::navList），但 v2 壳
// 调 `open(code, name)` 只传了两参 ⇒ 弹窗里提示「当前无列表上下文」（主人 2026-10-28 标注）。
// 这里从点击节点向上找**第一个能收到 ≥2 只个股的容器**，顺序 = DOM 顺序（= 页面视觉顺序）：
//   · 先试最近的表格/卡片，不够再逐层向上，最后到整页 stage；
//   · 只收个股（.TI 板块/指数码排除，旧版也把 cur.list 置空）；
//   · 去重保序，同一只票在页面多处出现只算一次。
// 找不到就返回 null，不编造列表。
function klineListOf(node) {
  const isStock = function (c) { return !!c && !ctxIsIndexCode(c); };
  // 全页名称字典：同一只票在别的卡片里带了 data-open-name 时，列表项即使先命中
  // 无名称的行容器（如自选卡的 .wl-wc[data-code]）也能补上名称。
  const globalNames = {};
  (function () {
    const all = document.querySelectorAll('[data-open-name]');
    for (let i = 0; i < all.length; i++) {
      const el = all[i];
      const c = normalizeStockCode(el.getAttribute('data-open-kline') || el.getAttribute('data-code') || '');
      const n = el.getAttribute('data-open-name') || '';
      if (c && n && !globalNames[c]) globalNames[c] = n;
    }
  })();
  const collect = function (root) {
    if (!root || !root.querySelectorAll) return [];
    const out = [];
    const seen = {};
    const nodes = root.querySelectorAll('[data-open-kline], [data-code]');
    for (let i = 0; i < nodes.length; i++) {
      const el = nodes[i];
      const raw = String(el.getAttribute('data-open-kline') || el.getAttribute('data-code') || '').trim();
      const c = normalizeStockCode(raw);
      if (!isStock(c)) continue;
      let nm = el.getAttribute('data-open-name') || '';
      if (!nm) {
        // 有些表格只给代码列（data-code）不给名称属性；节点自身文本就是名称时直接用它。
        // 严格限制：≤ 8 字、不含数字/空白（行容器文本带代码与涨跌，不会误取）。
        const t = String(el.textContent || '').trim();
        if (t && t.length <= 8 && !/[0-9]/.test(t) && !/\s/.test(t)) nm = t;
      }
      // 同一只票可能在行容器（data-code，无名称）与名称节点（data-open-kline + name）各出现一次，
      // 行容器先命中时 name 会是空串——后面遇到带名称的节点要**补上**，否则翻页只显代码。
      if (seen[c]) {
        if (!seen[c].name && nm) seen[c].name = nm;
        continue;
      }
      seen[c] = { code: c, name: nm };
      out.push(seen[c]);
    }
    for (let i = 0; i < out.length; i++) {
      if (!out[i].name && globalNames[out[i].code]) out[i].name = globalNames[out[i].code];
    }
    return out;
  };
  let p = node && node.parentElement;
  while (p && p !== document.documentElement) {
    const list = collect(p);
    if (list.length >= 2) return list;
    p = p.parentElement;
  }
  return null;
}

function bindKlineBridge() {
  const embedded = window.parent !== window;
  const origin = embedded ? parentOrigin() : '*';
  // 挂 document 而不是 el.stage —— 板块详情弹窗挂在 document.body 上
  // （sector/frontend/detail.js:325），不在 stage 内；挂 stage 就收不到弹窗里的点击。
  // capture 阶段：与旧页面板委托同口径（index.html:10936），抢先于卡片自身点击处理器。
  document.addEventListener('click', function (event) {
    if (noteModeOn()) return;                      // 标注模式：左键归标注，不开卡
    const t = event.target;
    if (!t || !t.closest) return;
    // 交互控件（按钮/输入/链接）不归 K 线跳转管：
    // 表格行/卡片容器带上 data-open-kline 后，行内的「撤单/签收/删除」按钮仍得点得动。
    if (t.closest('button, a, input, select, textarea, label, [role="button"], [contenteditable="true"]')) return;
    const k = t.closest('[data-open-kline]');
    const a = k ? null : t.closest('[data-open-amv]');
    const node = k || a;
    if (!node) return;
    const code = (k ? node.getAttribute('data-open-kline') : node.getAttribute('data-open-amv')) || '';
    if (!code.trim()) return;                      // 无码：不动作（不伪装可点）
    const name = node.hasAttribute('data-open-name') ? (node.getAttribute('data-open-name') || '') : '';
    event.stopPropagation();                       // 只拦传播，不 preventDefault（保留文本选择/链接语义）
    // 点击若发生在板块详情弹窗里，开完卡要把它收掉（否则全屏遮罩继续盖住，等于没反应）
    const modal = sectorModalOf(node);

    if (k) {
      // K 线：嵌旧页时仍转父页 KLineApp.open（那是完整版，带 L2/筹码/分时），
      // 独立打开时切 v2 K 线卡（ctx.card.args 一次性入参）。这条链与 8002 无关，不动。
      if (embedded) {
        try {
          window.parent.postMessage({ type: PM_KLINE, code: code.trim(), name: name }, origin);
        } catch (err) {
          console.warn('[shell] 转父页开 K 线失败', code, err);
        }
        closeSectorModal(modal);   // KLineApp 弹在父页、盖在 iframe 上；v2 里的详情遮罩得收
      } else {
        // 独立打开：K 线弹窗 z=100 盖在板块详情（z=50）之上，**不收详情**——
        // 关掉 K 线后还要回到详情继续看（主人 2026-10-28 标注 N20261028-100527-38）。
        ctxOpenKline(normalizeStockCode(code.trim()), name, klineListOf(node));
      }
      return;
    }

    // 板块详情：**无论嵌不嵌都走 v2 原生**（sector/frontend/detail.js）。
    // 旧行为在嵌入时 postMessage 给父页 → AmvCard.open → iframe 嵌 8002 —— 这就是
    // 「板块 Tab 早就是 v2 卡了，8002 却关不掉」的唯一原因（2026-10-26 实测确认）。
    // 改完后 8002 不再是详情弹窗的必要条件。
    if (ctxOpenSector(code.trim(), name)) closeSectorModal(modal);
  }, true);
  return true;
}

// 旧的嵌入/独立双分支已合并：两分支只差「K 线转不转父页」一个判断，
// 拆两套的代价是同一个动作两份实现，改了一边另一边不生效（2026-10-26 就是这么踩的）。

// ---------------------------------------------------------------- 卡间跳转（卡 → 卡）
//
// 卡片里的「打开另一张卡」靠 CustomEvent 发难，shell 必须真监听——否则事件发出去无人接，
// 不会报错，只是点了不动（静默失败）。本函数就是 shell 侧的**唯一收口点**。
//
// 为什么是同一个人函数里收三件事（2026-10-26 实测发现后两个都是死的）：
//   · `panel:open-card`    —— 早已接了
//   · `panel:open-kline`   —— 自选卡点个股跳 K 线发它，**shell 从未监听过** ⇒ 点了没反应
//   · `panel:open-sector`  —— 自选卡点板块芯片发它，同样从没监听 ⇒ 点了没反应
// 旧页里这两个动作都是通的（data-open-kline / data-open-amv 委托到 KLineApp / AmvCard），
// 所以这不是新需求，是迁到 v2 后断掉的交互。卡片侧不改（发事件本来就是对的），只在壳里收口。
function bindCardJump() {
  document.addEventListener('panel:open-card', function (event) {
    const detail = (event && event.detail) || {};
    const want = detail.cardId || null;
    const pluginId = detail.pluginId || null;
    // 查全量卡片（state.allCards），不是侧栏那份：`?card=` 单卡模式下 state.cards 只剩 1 张，
    // 拿它查表会把「仪表盘徽标 → 作战表」误判成「未装载」并只弹一句提示。
    const pool = state.allCards.length ? state.allCards : state.cards;
    let meta = null;
    if (want) meta = pool.find(function (c) { return c.id === want; }) || null;
    if (!meta && pluginId) meta = pool.find(function (c) { return c.pluginId === pluginId; }) || null;
    if (!meta) {
      toast('未装载卡片：' + (want || pluginId || '未知'));
      return;
    }
    if (event.preventDefault) event.preventDefault();   // 卡片据此判断「有人接了」
    selectCard(meta.key, detail.args || null);
  });

  // 卡内请求开 K 线（自选卡 .wl-c 上的 K 线图标）→ 与右键菜单同一条实现
  document.addEventListener('panel:open-kline', function (event) {
    const d = (event && event.detail) || {};
    if (!d.code) return;
    if (event.preventDefault) event.preventDefault();
    ctxOpenKline(d.code, d.name || '', Array.isArray(d.list) ? d.list : null);
  });

  // 卡内请求开板块详情（自选卡里的板块芯片）→ 走卡内 v2 原生详情，不再绕父页 8002
  document.addEventListener('panel:open-sector', function (event) {
    const d = (event && event.detail) || {};
    if (!d.code) return;
    if (event.preventDefault) event.preventDefault();
    ctxOpenSector(d.code, d.name || '');
  });
}

// ---------------------------------------------------------------- 全局右键菜单（B 单，2026-10-25）
//
// 行为基准 = 旧页 `engine/web/index.html` 的 _ctxResolve(8298) / _ctxItemsHtml(8393) /
// _ctxOpen(8419) / _ctxClose(8359) / _bindCtxMenu(8433) 与那四个接口封装（8363-8392）。
// 卡片代码零改动：只认 DOM 属性，菜单是壳层组件（节点挂 document.body，类名 ctx- 前缀）。
//
// 与旧页的四处**必要**差异（都不是简化，逐条记在 docs/HANDOFF_20261025_ctx_menu.md）：
//   ① 规则 6 的选择器：旧页是 `#panel-watchlist .wc[data-code]` + `_watchCardKind()` 查缓存；
//      v2 自选卡的 DOM 是 `.wl-wc[data-kind][data-code]`（kind 直接写在属性上）→ 直接读属性，
//      不再需要缓存反查，语义等价。
//   ② 追加一条 v2 专用兜底（旧页没有）：`.card-host[data-card-key^="sector::"] [data-code]`。
//      v2 板块卡的行/格只带 data-code，A 单照抄 8002 后仍是这个形态；不加这条则整个板块卡右键无菜单。
//      范围死锁在板块卡的卡片宿主内，详情弹窗（挂 body）不受影响。
//   ③ 「打开板块详情 / 打开 K 线」在被嵌时走既有 v2:openAmv / v2:openKline 消息（宿主页转发到
//      AmvCard.open / KLineApp.open），与左键 data-open-* 同一条通道；独立打开时退回切 v2 卡片。
//   ④ ETF→板块映射不读 window.KLineApp.etfSectorOf（v2 壳里没有那个全局），改读同一个数据源
//      `/api/etf_sector_map.json`（KLineApp 内部就是 fetch 这个），60s 缓存。
const CTX_GROUP_TTL = 60000;
const CTX_MAP_TTL = 60000;

const ctxState = {
  groups: null,     // 全量分组（含 kind），按 CTX_GROUP_TTL 失效
  groupsAt: 0,
  etf: null,        // ETF→板块映射 map，按 CTX_MAP_TTL 失效
  etfAt: 0,
  node: null,       // 菜单节点（懒建，常驻 body）
  seq: 0,           // 打开序号：异步拉数据回来时判是否已被后来的右键/关闭取代
};

// 板块/概念指数码（THS .TI）：与旧页 _ctxIsIndexCode 同口径
function ctxIsIndexCode(code) {
  return /\.TI$/i.test(String(code || '').trim());
}

// 目标解析：优先级与旧页 _ctxResolve 逐条对齐（顺序不许改）。
// 规则 5（[onclick*=watchSectorOpen]）必须排在规则 1 之后、规则 6 之前：
//   自选池里的板块 chip 祖先 .wc/.wl-wc 是**个股/板块卡**，先认祖先就会被错判（旧页实测坐实）。
function ctxResolve(t) {
  if (!t || typeof t.closest !== 'function') return null;
  // 1) 显式 data-ctx
  const tr = t.closest('[data-ctx]');
  if (tr) {
    const k = tr.getAttribute('data-ctx');
    const c = tr.getAttribute('data-code') || '';
    if (c) return { kind: k === 'sector' ? 'sector' : 'stock', code: c, name: tr.getAttribute('data-name') || '' };
  }
  // 2) data-open-kline（值是代码；.TI 视为板块）
  const sk = t.closest('[data-open-kline]');
  if (sk) {
    const c = sk.getAttribute('data-open-kline') || '';
    if (c) return { kind: ctxIsIndexCode(c) ? 'sector' : 'stock', code: c, name: sk.getAttribute('data-open-name') || '' };
  }
  // 3) data-open-amv（强制板块）
  const sc = t.closest('[data-open-amv]');
  if (sc) {
    const c = sc.getAttribute('data-open-amv') || '';
    if (c) return { kind: 'sector', code: c, name: sc.getAttribute('data-open-name') || '' };
  }
  // 4) data-kname（值即代码，name 取 textContent）
  const kn = t.closest('[data-kname]');
  if (kn) {
    const c = kn.getAttribute('data-kname') || '';
    if (c) return { kind: ctxIsIndexCode(c) ? 'sector' : 'stock', code: c, name: (kn.textContent || '').trim() };
  }
  // 5) 板块 chip 自己的 onclick（旧页旧前端形态：onclick="watchSectorOpen('881153.TI','房地产',event)"）
  const ws1 = t.closest('[onclick*="watchSectorOpen"]');
  if (ws1) {
    const m = /watchSectorOpen\('([^']+)'\s*,\s*'([^']*)'/.exec(ws1.getAttribute('onclick') || '');
    if (m && m[1]) return { kind: 'sector', code: m[1], name: m[2] || '' };
  }
  // 6) 自选池卡片（v2：.wl-wc 自带 data-kind；旧页：#panel-watchlist .wc[data-code] 查缓存）
  const wc = t.closest('#panel-watchlist .wc[data-code], .wl-wc[data-code]');
  if (wc) {
    const c = wc.getAttribute('data-code') || '';
    if (c) {
      const dk = wc.getAttribute('data-kind') || '';
      const nmEl = wc.querySelector('.wl-nm, .wc-nm');
      const nm = nmEl ? String(nmEl.textContent || '').trim() : '';
      const kind = dk === 'sector' || dk === 'stock' ? dk : (ctxIsIndexCode(c) ? 'sector' : 'stock');
      return { kind: kind, code: c, name: nm };
    }
  }
  // 6b) v2 板块卡兜底（见文件头差异②）：卡片宿主内的 data-code 一律当板块
  const secRow = t.closest('.card-host[data-card-key^="sector::"] [data-code]');
  if (secRow) {
    const c = secRow.getAttribute('data-code') || '';
    if (c) {
      const nmEl = secRow.querySelector('.nm-t');
      const nm = nmEl ? String(nmEl.textContent || '').trim() : '';
      return { kind: 'sector', code: c, name: nm };
    }
  }
  return null;
}

function ctxEnsureNode() {
  if (ctxState.node && ctxState.node.isConnected) return ctxState.node;
  const node = document.createElement('div');
  node.id = 'ctx-menu';
  node.className = 'ctx-menu';
  node.hidden = true;
  document.body.append(node);
  // 子菜单贴右边缘翻转（旧页没有：它只在主菜单做翻转；v2 侧栏很窄，分组子菜单很容易出界）
  node.addEventListener('mouseover', function (event) {
    const sub = event.target && event.target.closest ? event.target.closest('.ctx-sub') : null;
    if (!sub) return;
    const sm = sub.querySelector('.ctx-submenu');
    if (!sm) return;
    sub.classList.remove('is-left');
    const r = sm.getBoundingClientRect();
    if (r.right > window.innerWidth - 8) sub.classList.add('is-left');
    const box = node.getBoundingClientRect();
    if (r.bottom > window.innerHeight - 8) {
      sm.style.top = Math.max(8 - box.top, (window.innerHeight - 8) - r.height - box.top) + 'px';
    } else {
      sm.style.top = '';
    }
  });
  ctxState.node = node;
  return node;
}

function ctxClose() {
  const node = ctxState.node;
  ctxState.seq += 1;              // 作废进行中的 ctxOpen（它回来时不再显示）
  if (!node) return;
  node.hidden = true;
  node.textContent = '';
  delete node.dataset.ctxKind;
  delete node.dataset.ctxCode;
  delete node.dataset.ctxName;
}

// 分组缓存 60s（旧页 _ctxGroupsAt 同口径）；只回本 kind 的组
async function ctxGroups(kind) {
  if (!ctxState.groups || Date.now() - ctxState.groupsAt > CTX_GROUP_TTL) {
    try {
      const res = await fetch('/api/watchlist/groups', { cache: 'no-store' });
      const data = await res.json();
      ctxState.groups = (data && data.groups) || [];
      ctxState.groupsAt = Date.now();
    } catch (err) {
      ctxState.groups = ctxState.groups || [];
    }
  }
  return (ctxState.groups || []).filter(function (g) { return g && g.kind === kind; });
}

async function ctxWatched(kind, code) {
  try {
    const res = await fetch('/api/watchlist/state?kind=' + encodeURIComponent(kind)
      + '&code=' + encodeURIComponent(code), { cache: 'no-store' });
    const data = await res.json();
    return !!(data && data.watched);
  } catch (err) { return false; }
}

// ETF→板块映射：与旧页 KLineApp.etfSectorOf 同源（/api/etf_sector_map.json），未就绪返回 null（不编造）
async function ctxEtfSectorOf(code) {
  const key = String(code || '').trim().toUpperCase();
  if (!key) return null;
  if (!ctxState.etf || Date.now() - ctxState.etfAt > CTX_MAP_TTL) {
    try {
      const res = await fetch('/api/etf_sector_map.json', { cache: 'no-store' });
      const data = await res.json();
      ctxState.etf = (data && data.map) || {};
      ctxState.etfAt = Date.now();
    } catch (err) {
      ctxState.etf = ctxState.etf || {};
    }
  }
  const hit = (ctxState.etf || {})[key];
  return hit && hit.sector_code ? hit : null;
}

async function ctxAddWatch(kind, code, name, group) {
  try {
    const res = await fetch('/api/watchlist/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code, name: name, kind: kind, group: group || '' }),
    });
    const data = await res.json();
    if (data && data.ok !== false) toast('已加入自选：' + (name || code));
    else toast('加入失败：' + ((data && data.detail) || '未知错误'));
  } catch (err) { toast('加入失败：' + err.message); }
}

async function ctxRemoveWatch(code, name) {
  try {
    const res = await fetch('/api/watchlist/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code }),
    });
    const data = await res.json();
    toast(data && data.ok !== false ? ('已移出自选：' + (name || code)) : '移出失败');
  } catch (err) { toast('移出失败：' + err.message); }
}

// 打开 K 线：2026-10-26 改为**壳层弹窗**（旧版行为：就地弹窗、不换页）。
//
// 改前：独立打开时 `selectCard(kline)` 把整个页面切成 K 线卡——用户当前看的 Tab 被换掉，
// 与旧版「弹一个窗、后面那页还在」不一致（主人 2026-10-26 报的就是这个）。
//
// 现在：找 kline 插件导出的 `openKlineStandalone`，直接调（弹窗自管生命周期）；
// 插件没导出或加载失败 → 退回原来的切卡路径，不仿造入口（同 ctxOpenSector 的降级口径）。
// 嵌入旧页时仍优先 postMessage 给父页（父页 KLineApp 弹窗），本页路径作兵底。
function ctxOpenKline(code, name, list) {
  code = normalizeStockCode(code);
  if (!code) return false;
  if (window.parent !== window) {
    try {
      window.parent.postMessage({ type: PM_KLINE, code: code, name: name || '' }, parentOrigin());
      return true;
    } catch (err) { /* 落到下面的本页路径 */ }
  }
  const meta = findCardByPlugin('kline');
  if (!meta) { toast('未装载 K 线卡：' + code); return false; }
  openKlineOverlay(meta, code, name, list);
  return true;
}

// 弹窗与板块详情同一模式：复用插件模块，不切卡。
// list = 打开时的同列表上下文（[{code,name}]，顺序 = 页面视觉顺序），
// 旧版 K 线弹窗的 ↑↓ 切票就是靠它（app.open 第三参）；没有就传 null，弹窗内会提示无上下文。
function openKlineOverlay(meta, code, name, list) {
  loadModule(meta.plugin).then(function (mod) {
    const fn = mod && (mod.openKlineStandalone || mod.openKline);
    if (typeof fn !== 'function') {
      // 插件没导出弹窗入口：退回切卡（行为退化但能看，不静默失败）
      console.warn('[shell] kline 插件未导出 openKlineStandalone，退回切卡');
      selectCard(meta.key, { code: code, name: name || '' });
      return;
    }
    const api = makeCtx(meta.plugin, { id: meta.id, title: meta.title }).api;
    Promise.resolve(fn({ api: api, code: code, name: name || '', period: null, list: list || null }))
      .catch(function (err) {
        console.warn('[shell] K 线弹窗打开失败', code, err);
        toast('K 线弹窗打开失败：' + (err && err.message ? err.message : err));
      });
  }).catch(function (err) {
    console.warn('[shell] kline 插件加载失败', code, err);
    toast('K 线插件加载失败：' + (err && err.message ? err.message : err));
  });
}

// 打开板块详情（2026-10-26 改优先级）：**卡内 v2 原生详情优先**，父页 AmvCard 降为兜底。
//
// 旧行为：只要嵌在旧页里就先 postMessage 给父页 → 父页 AmvCard.open → **iframe 嵌 8002**。
//   ⇒ 板块 Tab 已经是 v2 卡了，但详情弹窗还得靠 8002 活着，关不掉那个端口。
// 新行为：sector 插件的 `frontend/detail.js`（610 行，样式从 8002 style.css 原样搬的
//   legacy.css.js）已经是完整实现，直接用它；只有「本页根本没有板块卡」时才退回父页。
//   ⇒ 8002 不再是详情弹窗的必要条件。
//
// 卡未激活时的处理：旧代码只弹一句 toast（「详情弹窗只在板块卡内打开」），用户点了没反应。
// 改成「切到板块卡 → 等它挂载完 → 立即开详情」，与旧页「点哪都能弹详情」的体验对齐。
function ctxOpenSector(code, name) {
  if (!code) return false;
  const nm = name || code;

  // ① 当前就在板块卡：直接开卡内详情（沿用卡的周期/看盘日，与右键菜单同一实现）
  const active = state.active && state.active.card;
  const activePlugin = state.active && state.active.key ? state.active.key.split('::')[0] : '';
  if (activePlugin === 'sector' && active && typeof active._openDetail === 'function') {
    try { active._openDetail(code, nm, null); return true; } catch (err) { /* 退到下一条 */ }
  }

  // ② 板块卡不在前台：用**壳层弹窗**直调 sector 的同一份 detail.js，不切卡。
  //
  // 为什么不能改成「切到板块卡再开」（上一版就错在这里，实测发现的）：“
  //   · 嵌入旧页时 v2 是 `?card=xxx` 单卡模式，selectCard 会把用户当前看的卡整个换掉，
  //     而父页 Tab 仍停在原来的那个 ⇒ Tab 写着「自选」、iframe 里是「板块」，两边不同步；
  //   · 旧页的行为是「就地弹一个详情遮罩、不换页」，切卡与它不一致。
  const meta = findSectorPlugin();
  if (meta) {
    openSectorOverlay(meta, code, nm, 1);
    return true;
  }

  // ③ 本页连 sector 插件都没有（如别的宿主自带的单卡页）：退回父页 AmvCard
  if (window.parent !== window) {
    try {
      window.parent.postMessage({ type: PM_AMV, code: code, name: name || '' }, parentOrigin());
      return true;
    } catch (err) { /* 落到下面的本页路径 */ }
  }

  toast('未装载板块卡：' + nm);
  return false;
}

// 找 sector 插件的卡元数据（不依赖它当前是否挂载）
function findSectorPlugin() {
  const pool = state.allCards.length ? state.allCards : state.cards;
  return pool.find(function (c) { return c.pluginId === 'sector'; }) || null;
}

// 壳层开板块详情：复用 sector 插件的 detail.js，但不挂它的卡。
// 周期按钮要能重开（detail.js 点击后先 close 再回调 onPeriod），所以 onPeriod 反向递归。
function openSectorOverlay(meta, code, name, winN) {
  loadModule(meta.plugin).then(function (mod) {
    const fn = mod && (mod.openDetailStandalone || mod.openDetail);
    if (typeof fn !== 'function') {       // 插件没导出：如实转给父页，不伪造入口
      if (window.parent !== window) {
        try {
          window.parent.postMessage({ type: PM_AMV, code: code, name: name || '' }, parentOrigin());
        } catch (err) { /* 忽略 */ }
      } else {
        toast('板块详情不可用：' + (name || code));
      }
      return;
    }
    const api = makeCtx(meta.plugin, { id: meta.id, title: meta.title }).api;
    const open = function (wn) {
      fn({
        api: api,
        code: code,
        name: name,
        winN: wn || 1,
        date: null,
        onPeriod: function (n) { open(n); },
      });
    };
    open(winN || 1);
  }).catch(function (err) {
    console.warn('[shell] 板块详情加载失败', code, err);
    toast('板块详情加载失败：' + (name || code));
  });
}

function ctxCopy(text) {
  const s = String(text || '');
  if (!s) return;
  const done = function () { toast('已复制：' + s); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(s).then(done, function () { ctxCopyFallback(s, done); });
    return;
  }
  ctxCopyFallback(s, done);
}

// 非安全上下文 / 权限被拒时的兜底（execCommand 已废弃但仍是唯一同步通道）
function ctxCopyFallback(text, done) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand && document.execCommand('copy');
    ta.remove();
    if (ok) done(); else toast('复制失败');
  } catch (err) { toast('复制失败：' + err.message); }
}

function ctxItemsHtml(kind, code, name, groups, watched, etfSec) {
  const esc = escapeHtml;
  const g = (groups || []).map(function (x) {
    return '<div class="ctx-item" data-act="add" data-group="' + esc(x.id) + '">' + esc(x.name) + '</div>';
  }).join('');
  const addMenu = '<div class="ctx-item ctx-sub">加入自选<span class="ctx-arrow">›</span>'
    + '<div class="ctx-submenu ctx-menu">' + g
    + '<div class="ctx-sep"></div>'
    + '<div class="ctx-item" data-act="newgroup">新建分组…</div></div></div>';
  const etfItem = (etfSec && etfSec.sector_code)
    ? ('<div class="ctx-item" data-act="amv" data-code="' + esc(etfSec.sector_code)
      + '" data-name="' + esc(etfSec.sector || '') + '">打开对应板块（'
      + esc(etfSec.sector || etfSec.sector_code) + '）</div>')
    : '';
  const openItem = kind === 'sector'
    ? ('<div class="ctx-item" data-act="amv">打开板块详情</div>'
      + '<div class="ctx-item" data-act="kline">打开 K 线（日线）</div>')
    : ('<div class="ctx-item" data-act="kline">' + (etfSec ? '打开 ETF 走势' : '打开 K 线') + '</div>'
      + etfItem);
  return '<div class="ctx-head">' + esc(name && name !== code ? (name + ' · ' + code) : code) + '</div>'
    + addMenu
    + (watched ? '<div class="ctx-item" data-act="remove">移出自选</div>' : '')
    + '<div class="ctx-sep"></div>' + openItem
    + '<div class="ctx-item" data-act="copy">复制代码</div>';
}

async function ctxOpen(ev, tgt) {
  const node = ctxEnsureNode();
  if (!node || !tgt || !tgt.code) return;
  // 序号：await 期间可能又右键一次或已被关掉，只让最后一次落地（避免旧结果盖新菜单）
  const seq = ++ctxState.seq;
  const [groups, watched, etfSec] = await Promise.all([
    ctxGroups(tgt.kind),
    ctxWatched(tgt.kind, tgt.code),
    tgt.kind === 'stock' ? ctxEtfSectorOf(tgt.code) : Promise.resolve(null),
  ]);
  if (seq !== ctxState.seq) return;
  node.dataset.ctxKind = tgt.kind;
  node.dataset.ctxCode = tgt.code;
  node.dataset.ctxName = tgt.name || '';
  node.innerHTML = ctxItemsHtml(tgt.kind, tgt.code, tgt.name, groups, watched, etfSec);
  node.hidden = false;
  // 先量尺寸再定位（hidden 时量不到，故先摆 0,0 显示出来）
  node.style.left = '0px';
  node.style.top = '0px';
  const r = node.getBoundingClientRect();
  let left = ev.clientX;
  let top = ev.clientY;
  if (left + r.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - r.width - 8);
  if (top + r.height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - r.height - 8);
  node.style.left = left + 'px';
  node.style.top = top + 'px';
}

function ctxRunAction(item) {
  const node = ctxState.node;
  if (!item || !node) return;
  const act = item.getAttribute('data-act') || '';
  const kind = node.dataset.ctxKind || 'stock';
  const code = item.getAttribute('data-code') || node.dataset.ctxCode || '';
  const name = item.hasAttribute('data-name') ? (item.getAttribute('data-name') || '') : (node.dataset.ctxName || '');
  if (act === 'add') {
    const group = item.getAttribute('data-group') || '';
    ctxClose();
    ctxAddWatch(kind, code, name, group);
  } else if (act === 'remove') {
    ctxClose();
    ctxRemoveWatch(code, name);
  } else if (act === 'newgroup') {
    ctxClose();
    const label = kind === 'sector' ? '板块' : '个股';
    const nm = String(window.prompt('新建' + label + '分组名称：') || '').trim();
    if (!nm) return;
    fetch('/api/watchlist/group/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: nm, kind: kind }),
    }).then(function (res) { return res.json(); }).then(function (data) {
      ctxState.groupsAt = 0;                       // 分组变了：下次开菜单重拉
      if (data && data.ok !== false && data.group && data.group.id) {
        ctxAddWatch(kind, code, name, data.group.id);
      } else {
        toast('建组失败：' + ((data && data.detail) || '未知错误'));
      }
    }).catch(function (err) { toast('建组失败：' + err.message); });
  } else if (act === 'kline') {
    ctxClose();
    ctxOpenKline(code, name);
  } else if (act === 'amv') {
    ctxClose();
    ctxOpenSector(code, name);
  } else if (act === 'copy') {
    ctxClose();
    ctxCopy(code);
  } else {
    ctxClose();
  }
}

// 全局委托：contextmenu 用**冒泡**（旧页标注模式在 capture 阶段抢事件，capture 会打架）
function bindCtxMenu() {
  ctxEnsureNode();
  document.addEventListener('contextmenu', function (event) {
    const tgt = ctxResolve(event.target);
    if (!tgt || !tgt.code) return;                 // 解析不到：保留浏览器原生菜单
    event.preventDefault();
    ctxOpen(event, tgt);
  });
  document.addEventListener('click', function (event) {
    const node = ctxState.node;
    if (!node || node.hidden) return;
    const t = event.target;
    const item = t && t.closest ? t.closest('.ctx-item[data-act]') : null;
    if (!item) {
      if (!(t && t.closest && t.closest('#ctx-menu'))) ctxClose();
      return;
    }
    ctxRunAction(item);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') ctxClose();
  });
  window.addEventListener('scroll', function () { ctxClose(); }, true);
  window.addEventListener('resize', function () { ctxClose(); });
  window.addEventListener('blur', function () { ctxClose(); });
}

// ---------------------------------------------------------------- 启动
function flattenCards(plugins) {
  const cards = [];
  for (const plugin of plugins) {
    if (plugin.enabled === false) continue;
    const fe = plugin.frontend || {};
    for (const card of fe.cards || []) {
      cards.push({
        key: plugin.id + '::' + card.id,
        id: card.id,
        title: card.title || card.id,
        slot: card.slot || 'main',
        order: typeof card.order === 'number' ? card.order : 100,
        plugin: plugin,
        pluginId: plugin.id,
        pluginName: plugin.name || plugin.id,
      });
    }
  }
  cards.sort(function (a, b) { return (a.order - b.order) || a.key.localeCompare(b.key); });
  return cards;
}

async function boot() {
  showBanner('');
  state.modules.clear();
  state.cards = [];
  state.allCards = [];
  state.plugins = [];

  let payload;
  try {
    const res = await fetch('/_plugins', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('/_plugins -> ' + res.status);
    payload = await res.json();
  } catch (err) {
    console.error('[shell] 读取插件清单失败', err);
    showBanner('读取插件清单失败：' + err.message);
    if (el.railTabs) el.railTabs.textContent = '';
    el.empty.hidden = false;
    el.empty.textContent = '核心未就绪。';
    return;
  }

  state.plugins = Array.isArray(payload.plugins) ? payload.plugins : [];
  state.cards = flattenCards(state.plugins);
  state.allCards = state.cards.slice();   // 跳转查表用，不受下面的 `?card=` 过滤影响

  // ?card=<id>：只渲染指定卡（R4 逐 Tab 替换器用 /v2/?card=xxx 单卡嵌入旧页）。
  // 只过滤卡片清单 + 收起侧栏，挂载/ctx/错误卡走原路径，不新增渲染分支。
  const wantCard = (new URLSearchParams(location.search).get('card') || '').trim();
  if (wantCard) {
    // 2026-10-30 修：此处原为 `c.id === wantCard`，但文档写的用法是 `?card=pluginId::cardId`
    // （= card.key），两套键名对不上 → 带 `::` 写必然「卡不存在」。现两种写法都认。
    state.cards = state.cards.filter(function (c) {
      return c.key === wantCard || c.id === wantCard;
    });
    document.getElementById('rail').style.display = 'none';
    document.getElementById('main').style.left = '0';
  }
  // rail 被藏了 → 标注入口改走浮标。为什么必须在这里再叫一次：
  // bindNote() 是顶层 await import，与 boot() 谁先完成不定；若它先跑（rail 还在），
  // 模块会拿 rail 按钮，而这一行随后把 rail 藏了 —— 入口就跟着没了。
  syncNoteEntry();
  if (el.version) el.version.textContent = payload.core_version ? ('core ' + payload.core_version) : '';

  const errors = Array.isArray(payload.errors) ? payload.errors : [];
  if (errors.length) {
    showBanner('部分插件加载失败（已禁用）：\n' + errors.map(function (e) { return '· ' + e.id + ': ' + e.error; }).join('\n'));
  }

  renderNav();

  if (wantCard && !state.cards.length) {
    // 卡 id 写错/卡被摘除：留一张错误卡（.card.is-error），旧页桥据此 3s 内回退旧界面
    el.empty.hidden = true;
    el.stage.append(makeErrorCard('v2 卡不存在：' + wantCard, '请核对 card id（映射写 v2:<卡 id>）'));
    el.title.textContent = wantCard;
    el.sub.textContent = 'v2 单卡视图';
    return;
  }

  if (!state.cards.length) {
    el.empty.hidden = false;
    el.empty.textContent = state.plugins.length ? '已加载插件，但没有声明卡片。' : '暂无插件。';
    el.title.textContent = '面板';
    el.sub.textContent = '';
    return;
  }

  // 初始卡取**导航表第一张**（仪表盘），不是 cards[0]——后者的顺序由插件 order 决定，
  // 可能落在非导航卡上（如 amv 的 order 更小）。
  // 例外：显式给了 `?card=`（单卡嵌入）→ 就开那张，否则非导航卡（如 state_tree）永远进不去。
  if (wantCard && state.cards.length) {
    await selectCard(state.cards[0].key);
    syncNoteEntry();
    return;
  }
  const firstNav = navItems(state.allCards.length ? state.allCards : state.cards)[0];
  await selectCard(firstNav ? firstNav.key : state.cards[0].key);
  syncNoteEntry();          // boot 末尾再算一次：覆盖 bindNote 晚于 boot 完成的时序
}

// ---------------------------------------------------------------- 事件绑定 + 首次启动
function bindChrome() {
  if (el.railGear) el.railGear.addEventListener('click', toggleSettings);
  if (el.scrim) el.scrim.addEventListener('click', closeSettings);
  if (el.drawerClose) el.drawerClose.addEventListener('click', closeSettings);
  if (el.btnKill) el.btnKill.addEventListener('click', doKill);
  if (el.btnRestart) el.btnRestart.addEventListener('click', doRestart);
  if (el.btnReload) el.btnReload.addEventListener('click', function () {
    boot().catch(function (err) { showBanner('刷新失败：' + err.message); });
  });
}

// 先套主题再渲染（避免首帧闪白/闪黑：themes.css 的 :root 已是夜航基线，data-theme 同值也无跳变）
applyTheme();
buildThemeSel();
buildFsSeg();
bindChrome();
bindSearch();
bindKlineBridge();
bindCardJump();
bindCtxMenu();
bindThemeFromParent();
// 标注模块异步就绪：失败也不阻断启动（await 只在本行，下面的 boot 不等它）
bindNote().catch(function (err) { console.warn('[shell] 标注模块未就绪', err); });
bindNotesPanel().catch(function (err) { console.warn('[shell] 标注清单面板未就绪', err); });

// 主题清单来自 CSSOM：外部样式表可能还没解析完，等 load 后再重建一次下拉（幂等，不覆盖已选值）。
window.addEventListener('load', function () {
  const before = state.theme;
  buildThemeSel();
  if (before !== state.theme) applyTheme();
});

refreshStatus();
state.statusTimer = setInterval(refreshStatus, 5000);

boot().catch(function (err) {
  console.error('[shell] 启动失败', err);
  showBanner('启动失败：' + err.message);
});
