/**
 * 板块卡片 · 表与工具条
 *
 * **DOM 逐字照抄 8002 工作台**（`夏天-activity/工具/dashboard/web/app.js`）：
 *   · `renderList`      app.js:1003（164 行）→ buildPanel / addRow
 *   · `heatGrid`        app.js:558  → heatGrid()
 *   · `hitMapHtml`      app.js:567  → hitMapHtml()
 *   · `HM_BG` / `HM_FG` app.js:547 / 556 → 同名（色值改走 token，见下）
 *   · `mvHtml`          app.js:995  → mvHtml()
 *   · `scoreTip`        app.js:1532 → scoreTip()
 *   · `boardScoreTip`   app.js:1546 → boardScoreTip()
 *   · `renderDirectory` app.js:916  → renderDirectory()
 *   · `renderCrumb`     app.js:895  → renderCrumb()
 *
 * 为什么必须照抄：样式表（legacy.css.js）是从 8002 style.css 搬来的，类名与嵌套层级
 * 就是它的选择器契约。上一版把「在榜」格写成圆点，`.hm-grid` / `.hm-c>s` / `.hm-c>b`
 * 三组规则整批失效——同一份样式渲出另一个东西。所以本文件不自造类名、不合并层级。
 *
 * 分工：compute.js 算（纯函数，可单测）；本文件只管 DOM 与交互，不重算口径。
 */

import { LEVEL_NAME } from './compute.js';

const HIT_WIN = 30;

/* 结构徽标 —— **板块与个股共用这一套**，规格对齐已有徽标（.dk / .streak / .sg）
 *
 * 2026-10-27 主人反馈「颜色太鲜艳、抢占视觉重点」。对照已有徽标实测规格，
 * 本组此前每一条都更重：圆角 6px→2px、字号 11→10、字重 700/800→常规、
 * 还加了 box-shadow 与 outline。现在全部拉回到既有语言，只留必要区分：
 *
 *   · 颜色 = 涨跌方向：淡底（*--bg，8~16%）+ 柔和字色（*--ink）
 *   · 量驱 = 虚线边框（沿用 .dk.half / .sg-b1 的既有做法）
 *   · 混合 / 平 = 无底色、灰字
 *
 * 全部走主题变量，不写死十六进制，深浅主题自动跟随。
 */
const STG_CSS = `
/* 基础块：板块列表与成分股表通用（高度/边杠对齐已有 .sg：15px / 1px / 圆角 2px） */
.sector-card .stg, .sector-modal .stg{
  display:inline-flex;align-items:center;justify-content:center;flex:none;white-space:nowrap;
  min-width:15px;height:15px;padding:0 4px;border-radius:2px;
  font-family:var(--mono,var(--font-mono));font-size:10px;line-height:1;
  font-weight:400;background:transparent;border:1px solid transparent;
  vertical-align:middle;opacity:.92}
/* 成分股表里再收一号，避免把名称列挤窄 */
.sector-modal .card-tbl .stg{font-size:9.5px;height:14px;min-width:14px;padding:0 3px;margin-left:3px}

/* —— 方向层：以**价格方向**上色（跟上表里「真实涨跌」一致）——
 * 2026-10-27 主人：内部填充与文字同色系，影响阅读。
 * 现把底压到约一半透明度（--up-bg 本已 12%，再 color-mix 到 50% ≈ 6%），
 * 方向提示改由**边框颜色**承担（32% 不透明度，一像素，够认出方向但不脏底）。
 * 另外字色用 --up-ink / --down-ink（比 --up/--down 更沉），保证小字号下的对比度。 */
.sector-card .stg.dir-up, .sector-modal .stg.dir-up{
  background:color-mix(in srgb,var(--up-bg) 50%,transparent);color:var(--up-ink);
  border-color:color-mix(in srgb,var(--up-solid) 32%,transparent)}
.sector-card .stg.dir-dn, .sector-modal .stg.dir-dn{
  background:color-mix(in srgb,var(--down-bg) 50%,transparent);color:var(--down-ink);
  border-color:color-mix(in srgb,var(--down-solid) 32%,transparent)}

/* —— 四类微调 —— */
/* 虚涨：AMV 在涨但价格在跌 → 主人要筛的「不可信」。用 warn 系橙，一服能从满屏里挑出 */
.sector-card .stg.t-虚涨, .sector-modal .stg.t-虚涨{
  background:color-mix(in srgb,var(--warn-bg) 60%,transparent);color:var(--warn-ink);
  border-color:color-mix(in srgb,var(--warn-solid) 38%,transparent);border-style:solid}
/* 独涨：价格在涨但 AMV 在缩 → 换手在退，虚底不实。红但虚线，弱一档 */
.sector-card .stg.t-独涨, .sector-modal .stg.t-独涨{
  border-style:dashed}
/* 无数据：— ，最弱 */
.sector-card .stg.t-na, .sector-modal .stg.t-na{
  background:transparent;color:var(--dim);
  border-color:transparent;opacity:.5}

.sector-card .st-seg .seg.is-off{opacity:.45;pointer-events:none}
`;

if (typeof document !== 'undefined' && !document.getElementById('sector-stg-css')) {
  const s = document.createElement('style');
  s.id = 'sector-stg-css';
  s.textContent = STG_CSS;
  document.head.appendChild(s);
}

/** 表头列。**严格照 8002 index.html 的 col-hd**：10 个元素对 11 条 grid 轨道——
 *  没有独立的序号表头（`.ch-nm` 用 `grid-column:2/4` 跨掉序号列），
 *  早先多加一个 `.ch-rk` 占位会把后续列整体错位一轨。 */
const COLS = [
  { k: 'rank', t: '↕', cls: 'ch-mv', tip: '按名次变化排序' },
  { k: null, t: '板块', cls: 'ch-nm' },
  { k: 'score', t: '迁移强度', cls: 'ch-sc', tip: '迁移强度 = 1/3/5/10/20 日权重变化的横截面排名百分位加权（0.15/0.20/0.25/0.25/0.15）。数值越高＝份额迁移越强。前瞻检验（2026-10-26）：5/10/20 日 Q5−Q1 超额 −0.451/−0.773/−0.918 pp，方向越长越负 ⇒ 不能当买入排序，正确用法是剔末档。点此排序' },
  { k: 'weight', t: '权重', cls: 'ch-wt', tip: '权重 = 板块活跃市值 ÷ 大盘活跃市值（%）—— 该板块在全市场活跃市值里的权重；点此排序' },
  { k: 'd', t: '权重变化', cls: 'ch-dl', tip: '权重变化 = 当前日权重 − 基准日权重（单位 pp=百分点，不是 %）—— 资金在板块间的相对迁移；点此排序' },
  { k: 'amv_pct', t: '活跃涨跌', cls: 'ch-ap', tip: '活跃涨跌 = 活跃市值区间涨跌幅（%）；点此排序' },
  { k: 'real_pct', t: '真实涨跌', cls: 'ch-rl', tip: '点此按真实涨跌排序' },
  { k: 'main', t: '主力净流入', cls: 'ch-mn', tip: '主力动向（eltdx L2_AMO复刻，period累加，亿元）' },
  { k: null, t: '在榜', cls: 'th-hits', tip: '在榜 = 近 30 个交易日里，进入「当前周期 + 当前排序键」同侧榜（前10% / 后10%）的天数；悬停看逐日排名与在榜日（描边）。换周期或换排序，整条统计会按新口径重算' },
  { k: null, t: '', cls: '' },
];

export function el(tag, attrs, text) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'style') node.style.cssText = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
  }
  if (text != null) node.textContent = text;
  return node;
}

/** 8002 `esc()`（app.js:372）—— 逐字同款 5 个字符转义。 */
export function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** 分段控件（8002 `.seg > .seg-btn`，激活态是实心 accent 底）。 */
export function seg(items, cur, onPick) {
  const box = el('div', { class: 'seg', role: 'tablist' });
  for (const it of items) {
    const on = it.v === cur;
    const b = el('button', {
      type: 'button',
      class: 'seg-btn' + (on ? ' active' : ''),
      role: 'tab',
      'aria-selected': String(on),
    }, it.t);
    b.addEventListener('click', () => onPick(it.v));
    box.append(b);
  }
  return box;
}

/* ---------------------------------------------------------------- 色格图（8002 heatGrid 原样）

   2026-10-18 主人：色格图全站只留一套——「在榜 / 板块内名次 / 评分」共用本渲染器与同一套红绿色阶，
   禁再各写一份。p = 0~1 的分位（越小越红），null = 当日无值（空格）。

   色阶的 6 档阈值逐字照 8002（.10/.22/.35/.55/.75），色值改写成本文件声明的 token：
   8002 写死 rgba(139,44,31,.55) 这类在浅色主题下正确、在夜航主题下会失真
   （这正是它自己注释里提到的同类问题）；改 token 后三套主题都跟随。
   仓库守门测试也要求卡片前端颜色一律走 token，不走裸色值。 */
export const HM_CSS = `
.sector-card-tip{
  --hm-b1:color-mix(in srgb,var(--up-solid) 55%,transparent);
  --hm-b2:color-mix(in srgb,var(--warn-solid) 40%,transparent);
  --hm-b3:color-mix(in srgb,var(--warn-solid) 24%,transparent);
  --hm-b4:color-mix(in srgb,var(--fg) 14%,transparent);
  --hm-b5:color-mix(in srgb,var(--down-solid) 20%,transparent);
  --hm-b6:color-mix(in srgb,var(--down-solid) 34%,transparent);
  --hm-fg-strong:color-mix(in srgb,white 92%,var(--fg));
}
`;

export const HM_BG = (p) => {
  if (p == null || !isFinite(p)) return 'transparent';
  if (p <= 0.10) return 'var(--hm-b1)';
  if (p <= 0.22) return 'var(--hm-b2)';
  if (p <= 0.35) return 'var(--hm-b3)';
  if (p <= 0.55) return 'var(--hm-b4)';
  if (p <= 0.75) return 'var(--hm-b5)';
  return 'var(--hm-b6)';
};
export const HM_FG = (p) => ((p == null || !isFinite(p)) ? 'var(--dim)' : (p <= 0.22 ? 'var(--hm-fg-strong)' : 'var(--fg)'));

/**
 * 色格图渲染器（8002 `heatGrid` app.js:558 逐字）。
 *
 * 注意 `<i class="hm-c">` 是 i 元素，里面 `<s>` 放日期、`<b>` 放数值。
 * 上一版用 span + 圆点，`.hm-c>s` / `.hm-c>b` 全部不命中——这就是「在榜」格失效的根因。
 */
export function heatGrid(head, cells, foot) {
  const body = cells.map((c) => '<i class="hm-c' + (c.on ? ' on' : '') + '" style="background:'
    + HM_BG(c.p) + ';color:' + HM_FG(c.p) + '"><s>' + c.ds + '</s><b>' + c.txt + '</b></i>').join('');
  return '<div class="hm-hd">' + head + '</div><div class="hm-grid">' + body + '</div>'
    + '<div class="hm-ft">' + foot + '</div>';
}

/* 在榜悬浮：近 30 交易日排名矩阵（色条深浅=当日排名，越靠前越红；描边=当日在该侧榜内）。
   8002 `hitMapHtml` app.js:567 逐字；唯一差别是 sortKey / winN 从入参来（8002 读全局 state）。 */
export function hitMapHtml(h, sortKey, winN) {
  const n = h.nRank || 1;
  const cells = h.cells.map((c) => ({
    ds: c.ds, txt: c.rank ? String(c.rank) : '—', p: c.rank ? c.rank / n : null, on: c.on,
  }));
  const SORT_CN = { rank: '名次变化', weight: '权重', d: '权重变化', amv_pct: '活跃涨跌', real_pct: '真实涨跌', main: '主力净流入' };
  const sortTxt = SORT_CN[sortKey] || sortKey;
  return heatGrid('近 ' + h.total + ' 交易日 · 在榜 <b>' + h.hits + '</b> 天 · '
    + winN + '日' + sortTxt + '榜（周期改动后整条轨迹同步重算）', cells,
  '格子=当日排名（左→右、上→下由早到晚）· 红=靠前 · 描边=在该侧榜内');
}

/** 结构标签悬停提示（2026-10-27）。
 *  口径来自 amv_struct_tag.compute_row：px=涨跌贡献、act=活跃度贡献（单位‰），
 *  den=大盘基准（不参与「量价」判定，单独列出供核对）。
 *  **文案禁止暗示收益**——这套是结构观察，不是买卖信号。 */
export function stgTip(st) {
  const f = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2));
  const HINT = {
    '齐升': 'AMV 与价格同向上涨',
    '虚涨': 'AMV 在涨但价格在跌——换手抬起来的',
    '独涨': '价格在涨但 AMV 在缩——换手在退',
    '齐跌': 'AMV 与价格同向下跌',
  };
  const DIR_TXT = { up: '价格向上', dn: '价格向下', flat: '价格持平' };
  const lines = [
    '<b>' + esc(st.label || st.tag || '—') + '</b>'
      + '　<i>' + (DIR_TXT[st.dir] || '') + '</i>'
      + (HINT[st.tag] ? '　<i>' + HINT[st.tag] + '</i>' : ''),
    'AMV 涨幅 <b>' + f(st.amv_pct) + '</b>%　价格涨幅 <b>' + f(st.px_pct) + '</b>%',
    '差额（换手贡献）<b>' + f(st.gap) + '</b> pp',
  ];
  if (st.tag === '虚涨') {
    lines.push('<i>⚠ AMV 上涨但价格下跌，多头区间也不可信</i>');
  }
  lines.push('<i>只看两个百分比，不做分项拆解。结构观察，非买卖信号。</i>');
  return lines.join('<br>');
}

/** 评分轨迹色格图（8002 `scoreTip` app.js:1532 逐字）。
 *  分母固定 100 ⇒ p = 1 − 分/100（高分=红），与其他两张图同读法。 */
export function scoreTip(rows) {
  if (!rows || !rows.length) return '暂无轨迹';
  const cells = rows.map((t) => {
    const v = Number(t[1]);
    return { ds: String(t[0]).slice(5).replace('-', ''), txt: v.toFixed(0), p: 1 - v / 100 };
  });
  const vs = rows.map((t) => Number(t[1]));
  return heatGrid('近 ' + rows.length + ' 交易日 · 迁移强度（0-100）', cells,
    '格子=当日迁移强度（左→右由上到下）· 红=高迁移 · 高迁移档前瞻更差 · 最低 ' + Math.min.apply(null, vs).toFixed(1)
    + ' / 最高 ' + Math.max.apply(null, vs).toFixed(1));
}

/** 板块评分轨迹：逐日走同一个 scoreMapOf，再交给 scoreTip（8002 `boardScoreTip` 逐字）。 */
export function boardScoreTip(C, j, dayB, drillL1) {
  const rows = [];
  for (let k = 29; k >= 0; k--) {
    const di = dayB - k;
    if (di < 0) continue;
    const m = C.scoreMapOf(di, drillL1);
    if (!m || !m.has(j)) continue;
    rows.push([C.dates[di], m.get(j)]);
  }
  return scoreTip(rows);
}

/** 名次变化徽标（8002 `mvHtml` app.js:995 逐字：类名 .rank-mv、箭头 ⬆⬇、前值缺失时 NEW）。 */
export function mvHtml(cur, prev) {
  if (cur == null) return '';
  if (prev == null) return '<span class="rank-mv up">NEW</span>';
  if (prev > cur) return '<span class="rank-mv up">⬆' + (prev - cur) + '</span>';
  if (prev < cur) return '<span class="rank-mv dn">⬇' + (cur - prev) + '</span>';
  return '<span class="rank-mv flat">—</span>';
}

/** 8002 `renderList` 内的 `hitOf()`（app.js:1017-1040 逐字搬出）。 */
export function hitOf(hitDays, nRank, j, dates) {
  let hits = 0;
  const cells = [];
  for (const hd of hitDays) {
    const on = hd.set.has(j);
    if (on) hits++;
    const rk = hd.rk[j];
    cells.push({ ds: dates[hd.d] ? dates[hd.d].slice(5).replace('-', '') : String(hd.d), rank: rk, on: on });
  }
  cells.reverse();   // 展示顺序：旧→新（最新一天排在最后，2026-10-18 主人要求）
  return { hits: hits, total: hitDays.length, cells: cells, nRank: nRank };
}

/** 取近 30 日的榜位集合与排名（8002 `renderList` 顶部同款：一次取齐，避免逐行重算卡顿）。 */
export function collectHitDays(C, side, dayB, winN, drillL1) {
  const hitDays = [];
  for (let hd = dayB; hd >= Math.max(0, dayB - HIT_WIN + 1); hd--) {
    const hs = C.boardSets(hd, winN, drillL1);
    if (!hs) continue;
    hitDays.push({ d: hd, set: (side === 'in' ? hs.inSet : hs.outSet), rk: C.rankMapBy(hd, winN, drillL1) });
  }
  let nRank = 0;
  if (hitDays.length) {
    // compute.js 的 rankMapBy 返回 { j -> 名次 }（8002 返回数组，取值语义等价）。
    // 色阶分母 = 当日 rankMap 里非空项数，两种形状都要能数。
    const rk0 = hitDays[0].rk || {};
    if (Array.isArray(rk0)) {
      for (const v of rk0) if (v != null) nRank++;
    } else {
      for (const k of Object.keys(rk0)) if (rk0[k] != null) nRank++;
    }
  }
  return { hitDays, nRank };
}

/* ---------------------------------------------------------------- 榜单 */

/**
 * 建一张榜（8002 `renderList` 的 DOM 结构逐字）。
 *
 * @param {object} o
 * @param {string} o.side      in | out
 * @param {string} o.title     面板标题（8002 是 lvName + ' · 活跃市值变化 前10%'）
 * @param {object} o.C         compute 实例
 * @param {object} o.state     {dayB, winN, drillL1, sortKey, sortDir}
 * @param {object} o.flowMap   code -> {main, amv_pct, real_pct, diverge, bull, main_days}
 * @param {boolean} o.idxMode  指数层：CSS 会隐藏评分/在榜/权重/变化/活跃涨跌列（.panels.idx）
 * @param {Array} [o.rowsAll]  指数层全量行（给了就整列全显，不切 10%；compute.computeAllRows）
 * @param {string} [o.selCode] 指数层当前选中项（行加 .sel，8002 renderIdxDetail 同口径）
 * @param {function} [o.onSelect] 指数层点行 = 选右侧详情（不弹卡）
 * @param {function} o.onSort / o.onOpen / o.tipShow / o.tipHide
 */
export function buildPanel(o) {
  const { side, title, C, state, flowMap, idxMode, onSort, onOpen, tipShow, tipHide } = o;
  const rowsAll = Array.isArray(o.rowsAll) ? o.rowsAll : null;
  const selCode = o.selCode || null;
  const stFilter = o.stFilter || 'all';
  const inSide = side === 'in';
  const panel = el('div', { class: 'panel ' + (inSide ? 'panel-in' : 'panel-out') });

  const hd = el('div', { class: 'panel-hd' });
  hd.append(el('span', { class: 'sw ' + (inSide ? 'sw-in' : 'sw-out') }), el('span', null, title));
  panel.append(hd);

  // 表头：当前排序键高亮 + 方向（8002 index.html 的 col-hd：可排序列是 button，其余是 span）
  const colHd = el('div', { class: 'col-hd' });
  for (const c of COLS) {
    const sortable = !!c.k;
    if (!sortable) {
      colHd.append(el('span', { class: 'th ' + c.cls, title: c.tip || null }, c.t));
      continue;
    }
    const on = state.sortKey === c.k;
    const b = el('button', {
      type: 'button',
      class: 'th ' + c.cls + (on ? ' on' : ''),
      'data-k': c.k,
      title: c.tip || null,
    });
    b.append(document.createTextNode(c.t));
    if (on) b.append(el('i', { class: 'arr' }));
    b.addEventListener('click', () => onSort(c.k));
    colHd.append(b);
  }
  panel.append(colHd);

  const list = el('ol', { class: 'list', 'data-side': side });
  const lists = C.computeBoardLists(state.dayB);
  let rows = rowsAll || (inSide ? lists.topIn : lists.topOut);
  const exits = rowsAll ? [] : (inSide ? lists.exitsIn : lists.exitsOut);

  /* 结构标签筛选（2026-10-27）。**只过滤显示，不改榜单口径、不重算指标**：
   * 榜单（前/后 10%）仍按原口径选出，筛选只是在这个结果上再做一层。
   * 筛选值 = 徽标文案（换手驱动 / 股本驱动），比对的是 st.label；
   * 价格驱动没有 label，不会入选项。
   * 筛选后为空 → 明确告知，不拿全量顶上。 */
  const nBefore = rows.length;
  if (stFilter && stFilter !== 'all' && !idxMode) {
    rows = rows.filter((r) => {
      const st = (flowMap[r.code] || {}).st;
      return !!st && st.label === stFilter;
    });
  }

  if (!rows.length) {
    const win = state.winN + '天';
    list.append(el('li', { class: 'rb-empty' },
      (stFilter && stFilter !== 'all')
        ? '该榜单无「' + stFilter + '」结构板块（筛选前 ' + nBefore + ' 个）'
        : (lists.warmup ? '该日历史不足 ' + win + '（窗口未填满）' : '该窗口无有效板块')));
    panel.append(list);
    return panel;
  }

  // 在榜：一次取齐近 30 交易日（8002 同款；逐行重算 90 行 × 30 日会卡）
  // 指数层该列被 CSS 隐藏 ⇒ 不算（点行换选中会重画整表，30 日 × boardSets 是白花的时间）
  const { hitDays, nRank } = idxMode
    ? { hitDays: [], nRank: 0 }
    : collectHitDays(C, inSide ? 'in' : 'out', state.dayB, state.winN, state.drillL1);

  const addRow = (r, i, isExit) => {
    const li = el('li', { class: 'row' + (isExit ? ' is-exit' : '') + (selCode && r.code === selCode ? ' sel' : '') });
    li.dataset.code = r.code;
    li.dataset.j = String(r.j);
    const row = el('button', { type: 'button', class: 'row-hd', 'aria-expanded': 'false' });

    // 名次变化（8002: .mv 内是 mvHtml 的 span）
    const mvEl = el('span', { class: 'mv' });
    mvEl.innerHTML = isExit ? '' : mvHtml(r.rank, r.rankPrev);
    row.append(mvEl);

    // 序号
    row.append(el('span', { class: 'rk' }, isExit ? '退出' : String(i + 1)));

    // 名称 + 多空 + 徽标（顺序照 8002：.nm > .dk + .nm-t + .streak + .badge）
    const nm = el('span', { class: 'nm' });
    const fm0 = flowMap[r.code] || {};
    const bl0 = (fm0.bull != null) ? fm0.bull : null;
    if (bl0 != null) {
      nm.append(el('span', {
        class: 'dk ' + (bl0 ? 'bull' : 'bear'),
        title: bl0 ? '双轨确认多头（AMV 站上 MA10 / 共振或二次确认）' : '双轨确认空头（AMV 跌破 MA10 / 共振或二次确认）',
      }, bl0 ? '多' : '空'));
    }
    nm.append(el('span', { class: 'nm-t', title: r.code }, r.name || r.code));
    /* 指数层（2026-11-05 标注 81）：点行只换右栏，**点名称**弹详情卡——
       两种打开方式互不覆盖，名字额外给个提示。 */
    if (idxMode) {
      const nmT = nm.querySelector('.nm-t');
      nmT.style.cursor = 'pointer';
      nmT.title = r.code + ' · 点击开详情卡';
      nmT.addEventListener('click', (ev) => { ev.stopPropagation(); onOpen(r.code, r.name); });
    }

    /* 结构标签徽标（2026-10-27）。
     * 语义 = **AMV 由什么驱动**（价格 / 换手 / 股本），不是二维分类。
     * 表格里已有「真实涨跌」（价格轴）与「权重变化」（份额轴），
     * 被折叠的只有**换手**与**股本**两项分项 ⇒ 只标这两项。
     * 价格驱动不标（真实涨跌列已有）；换手驱动不强的不标（日均百余个，成了背景）。
     */
    const stg = (fm0.st && fm0.st.label) ? fm0.st : null;
    if (stg) {
      nm.append(el('span', {
        class: 'stg dir-' + (stg.dir || 'flat') + ' t-' + (stg.tag || ''),
        title: stgTip(stg),
      }, stg.label));
    }

    /* 徽标三态取一（8002 app.js:1075-1095 逐字）：
     *   前一日在榜（streak≥2）      → 连N（≥3 日红框）
     *   前一日不在榜且 3 日内跌出过 → 赭色「重入」
     *   前一日不在榜且 3 日外无记录 → gold「NEW」
     *   streak<=1 且非重入/非NEW（含「换边」）→ 不渲染任何徽标
     * 两个槽位都建出来（DOM 与 8002 一致），只写其中一个。 */
    const st = el('span', { class: 'streak' });
    const bd = el('span', { class: 'badge' });
    let state3 = 'none';
    if (!isExit && !r.noBadge) {
      if (r.streak >= 2) state3 = r.streak >= 3 ? 'streakHot' : 'streak';
      else if (r.isReentry) state3 = 're';
      else if (r.isNew) state3 = 'new';
      else state3 = 'none';
    }
    if (state3 === 'streak' || state3 === 'streakHot') {
      st.textContent = '连' + r.streak;
      st.classList.add('on');
      if (state3 === 'streakHot') st.classList.add('hot');
    } else if (state3 === 're') {
      bd.textContent = '重入';
      bd.classList.add('is-re');
    } else if (state3 === 'new') {
      bd.textContent = 'NEW';
      bd.classList.add('is-new');
    }
    nm.append(st, bd);
    row.append(nm);

    // 评分（8002：悬停看近 30 日评分轨迹，同一张色格图）
    const scCls = r.score == null ? ' fl' : (r.score >= 70 ? ' hot' : (r.score <= 30 ? ' cold' : ''));
    const scEl = el('span', { class: 'score' + scCls }, r.score == null ? '—' : String(r.score));
    scEl.title = '迁移强度 = 1/3/5/10/20 日权重变化的横截面排名百分位加权（0.15/0.20/0.25/0.25/0.15），数值越高＝份额迁移越强；越高≠越好，不能当买入排序，正确用法是剔末档';
    if (!idxMode && tipShow) {
      scEl.addEventListener('mouseenter', (ev) => {
        const html = boardScoreTip(C, r.j, state.dayB, state.drillL1);
        if (!html) return;
        tipShow(html, ev, false, true);
      });
      scEl.addEventListener('mousemove', (ev) => tipShow(null, ev, true));
      scEl.addEventListener('mouseleave', () => tipHide && tipHide());
    }
    row.append(scEl);

    // 权重
    row.append(el('span', { class: 'weight' }, (r.weight / 1e6 * 100).toFixed(2) + '%'));
    // 权重变化
    row.append(el('span', { class: 'delta ' + (isExit ? 'flat' : clsNum(r.d)) },
      (r.d / 1e6 * 100 >= 0 ? '+' : '') + (r.d / 1e6 * 100).toFixed(2)));
    // 活跃涨跌 / 真实涨跌 / 主力
    const fm = flowMap[r.code] || {};
    const ap = (fm.amv_pct != null) ? fm.amv_pct : null;
    row.append(el('span', { class: 'amvp ' + clsNum(ap) }, ap == null ? '—' : fmtPctNum(ap)));
    const rp = (fm.real_pct != null) ? fm.real_pct : null;
    row.append(el('span', { class: 'realp ' + clsNum(rp) }, rp == null ? '—' : fmtPctNum(rp)));
    const mn = fm.main;
    const nd = fm.main_days || 0;
    row.append(el('span', {
      class: 'mainp ' + (mn == null ? 'flat' : (mn > 0 ? 'flow-pos' : (mn < 0 ? 'flow-neg' : 'flat'))),
      title: '主力动向（L2_AMO复刻，' + state.winN + '日累加，' + nd + '日有效，亿元）',
    }, mn == null ? '—' : (mn > 0 ? '+' : '') + (mn / 10000).toFixed(2) + '亿'));

    // 在榜（8002：.hits + heatGrid 浮层；指数模式该列被 CSS 隐藏）
    const h = hitOf(hitDays, nRank, r.j, C.dates);
    const htEl = el('span', {
      class: 'hits' + (h.hits >= 15 ? ' hot' : (h.hits >= 8 ? ' warm' : '')),
    }, h.total ? (h.hits + '/' + h.total) : '—');
    if (tipShow) {
      htEl.addEventListener('mouseenter', (ev) => tipShow(hitMapHtml(h, state.sortKey, state.winN), ev, false, true));
      htEl.addEventListener('mousemove', (ev) => tipShow(null, ev, true));
      htEl.addEventListener('mouseleave', () => tipHide && tipHide());
    }
    row.append(htEl);

    row.append(el('span', { class: 'chev' }, '›'));
    row.addEventListener('click', () => {
      if (idxMode && o.onSelect) { o.onSelect(r.code, r.name); return; }
      onOpen(r.code, r.name);
    });
    li.append(row);
    li.append(el('div', { class: 'row-body' }));   // 8002 rowTpl 里也有这一层（行内展开已退役，保留结构）
    return li;
  };

  rows.forEach((r, i) => list.append(addRow(r, i, false)));
  exits.forEach((r) => list.append(addRow(r, 0, true)));
  panel.append(list);
  return panel;
}

/** 数值格式化（8002 app.js:357-361 逐字）。 */
export const fmtPct = (v) => (v == null ? '—' : (v / 1e6 * 100).toFixed(2) + '%');
export const fmtPp = (v) => (v == null ? '—' : (v / 1e6 * 100 >= 0 ? '+' : '') + (v / 1e6 * 100).toFixed(2));
export const fmtAmt = (v) => (v == null ? '—' : (v / 10).toFixed(1));
export const fmtPctNum = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%');
export const clsNum = (v) => (v == null ? 'flat' : (v > 0 ? 'pos' : (v < 0 ? 'neg' : 'flat')));

/* ---------------------------------------------------------------- 分类目录

   8002 `renderDirectory` app.js:916 的结构逐字。
   结构：.dir > button.chip（.on 为当前）。
   v2 里没有 L1 层（8002 也已废），所以目录来自 catalog 的 l1 字段去重。
   2026-11-15 主人定：不折叠、不渲染标题与面包屑，「全部」排在最后。 */
export function renderDirectory(host, l1s, drillL1, onPick) {
  // 「全部」排在最后（2026-11-15 主人定）：它是兜底项，跟在具体分类之后更顺手。
  host.innerHTML = l1s.map((n) => '<button class="chip' + (drillL1 === n ? ' on' : '') + '" data-l1="' + esc(n) + '">' + esc(n) + '</button>').join('')
    + '<button class="chip' + (drillL1 ? '' : ' on') + '" data-l1="">全部</button>';
  host.querySelectorAll('.chip').forEach((b) => {
    b.addEventListener('click', () => onPick(b.dataset.l1 || null));
  });
}

/** 表头排序用的下一状态：同键翻转方向，异键切过去并回到默认降序。 */
export function nextSort(cur, dir, key) {
  if (cur === key) return { key: key, dir: -dir };
  return { key: key, dir: -1 };
}

export { LEVEL_NAME };
