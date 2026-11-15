/**
 * intraday 卡 · 旧页视觉规则（照抄 engine/web/index.html 的 <style>，不重新设计）
 *
 * 为什么另开一个模块而不是写进 frontend.js：
 *   规则来自旧页 6 处分散的样式段（523 / 1316 / 1360-1482 / 1614-1655 / 1373），
 *   与原文件里的 `it-` 自有样式混在一起会看不出哪条是"搬来的"、哪条是新写的。
 *   这里只放搬来的部分，逐条注明旧页行号，改样式时能直接对回基准。
 *
 * 三处**有意偏离**旧页（都是"从整页搬进卡片"的必然结果，不是设计变更）：
 *   ① 所有选择器加 `.it-card` 前缀：旧页是整页独占，v2 是卡，`.src`/`.muted`/`.up`
 *      这类通用类名不加前缀会污染别的卡。
 *   ② `--mono` → `--font-mono`：旧页全文 6 处 `font-family:var(--mono)`，但
 *      themes.css **从未定义 --mono**（旧页里它一直解析为空、落到浏览器等宽兜底）。
 *      这是旧页的隐性缺陷，卡片里补一条别名让它真的用上主题等宽字体。
 *   ③ `.st-tip` 从 `position:fixed` 改 `position:absolute`（旧页挂 body 跟着鼠标跑，
 *      卡片里必须留在容器内）。副产物：滚动时卡片会移动而浮层锚在卡片坐标系，
 *      所以 JS 侧在滚动事件里重算一次坐标（见 sector.js 的 moveTip）。
 */

const OLD_RULES = `
/* ---- 旧页 1316-1317 / 523：通用文字与陈旧标记 ---- */
.src { color:var(--dim); font-size:0.6875rem; word-break:break-all; }
.muted { color:var(--dim); }
.stale-warn { display:inline-block; padding:1px 8px; border-radius:var(--radius-pill); font-size:0.75rem;
              background:var(--warn-bg); color:var(--warn-ink); border:1px solid color-mix(in srgb,var(--warn) 45%,transparent);
              margin-left:6px; white-space:nowrap; }
.st-k { color:var(--dim); font-size:0.75rem; }

/* ---- 旧页 1360-1364：板块轮动工具条 ---- */
.st-bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:8px; }
.st-grp { display:inline-flex; gap:2px; }
.st-bar button { padding:2px 10px; font-size:0.75rem; border-radius:var(--radius-sm); background:var(--card2); color:var(--dim); border:var(--border-w) var(--border-style) var(--border-color); cursor:pointer; }
.st-bar button.on { background:var(--accent); color:var(--bg); }
.st-bar .src { margin:0; }

/* ---- 旧页 1365 / 1390-1399：图与网格底 ---- */
.st-chart { display:block; width:100%; height:auto; background:var(--card2); border-radius:var(--radius-sm); }
.st-chart .st-grid { stroke:var(--line-soft); stroke-width:1; vector-effect:non-scaling-stroke; }
.st-chart .st-base { stroke:var(--dim); stroke-width:1; stroke-dasharray:4 3; vector-effect:non-scaling-stroke; }
.st-chart .st-ytx, .st-chart .st-xtx { fill:var(--dim); font-size:9px; }
.st-chart .st-lab { font-size:10px; }
.st-chart .st-line, .st-chart .st-line-lab, .st-quad .st-bub, .st-quad .st-ql { cursor:pointer; opacity:.92; transition:opacity .15s; }
.st-chart .st-line.dim, .st-chart .st-line-lab.dim, .st-quad .st-bub.dim, .st-quad .st-ql.dim { opacity:.15; }
.st-chart .st-line.hi, .st-chart .st-line-lab.hi, .st-quad .st-bub.hi, .st-quad .st-ql.hi { opacity:1; }
.st-note { color:var(--dim); font-size:0.75rem; }
.st-chart .st-hit, .st-chart .st-line-lab rect, .st-quad .st-ql rect { pointer-events:all; }

/* ---- 旧页 1401-1407 / 1453-1457：十字线与区间带 ---- */
.st-chart .evo-cross { pointer-events:none; }
.st-chart .evo-cross-bg { fill:var(--card); stroke:var(--line); stroke-width:1; vector-effect:non-scaling-stroke; opacity:.97; }
.st-chart .evo-cross-t { fill:var(--dim); font-size:9px; font-family:var(--font-mono); }
.st-chart .evo-cross-r { fill:var(--fg); font-size:9px; }
.st-chart .evo-cross-v.up { fill:var(--up); }
.st-chart .evo-cross-v.down { fill:var(--down); }
.st-chart .evo-cross-v:not(.up):not(.down) { fill:var(--dim); }
.st-chart .evo-cross-v { stroke:var(--dim); stroke-width:1; stroke-dasharray:3 3; vector-effect:non-scaling-stroke; opacity:.75; }
/* 区间带：旧页写死一个天蓝色（照源码 app.js），这里换成 --accent —— 三套主题下选区都能看见，
   且与 2026-10-25 的「颜色只走 token」门禁一致（裸 hex 会被 app/tests 拦下）。 */
.st-chart .evo-range-band { fill:var(--accent); fill-opacity:.20; stroke:var(--accent); stroke-width:1; vector-effect:non-scaling-stroke; }
.st-chart .evo-range-edge { display:none; }

/* ---- 旧页 1409-1411：板块信息浮层（fixed → absolute，见文件头 ③） ---- */
.st-tip { position:absolute; left:0; top:0; z-index:6; pointer-events:none; background:var(--card); color:var(--fg);
  border:1px solid var(--line-soft); border-radius:var(--radius-sm); padding:6px 9px;
  font-size:0.72rem; line-height:1.55; box-shadow:var(--shadow-pop); max-width:260px; }
.st-tip[hidden] { display:none; }

/* ---- 旧页 1413-1482：日内演化双栏 ---- */
.evo-split { display:flex; gap:var(--d-gap); flex-wrap:wrap; align-items:stretch; }
.evo-pane { min-width:260px; display:flex; flex-direction:column; position:relative; }
.evo-pane:first-child { flex:1.25 1 340px; }
.evo-pane:last-child { flex:1.75 1 480px; }
.evo-pane .src { margin-top:3px; }
.evo-hd { display:flex; align-items:baseline; justify-content:space-between; gap:10px; flex-wrap:wrap; margin-bottom:4px; }
.evo-h { margin:0; font-size:0.86rem; font-weight:600; }
.evo-sub { font-size:0.78rem; font-weight:400; color:var(--dim); margin-left:5px; }
.evo-ctl { display:inline-flex; align-items:center; gap:6px; flex-wrap:wrap; }
.evo-k { font-size:0.7rem; color:var(--dim); }
.evo-seg { display:inline-flex; border:var(--border-w) var(--border-style) var(--border-color);
  border-radius:var(--radius-sm); overflow:hidden; }
.evo-seg > button { padding:1px 8px; font-size:0.7rem; background:transparent; border:0; color:var(--dim); cursor:pointer; }
.evo-seg > button + button { border-left:var(--border-w) var(--border-style) var(--border-color); }
.evo-seg > button:hover { color:var(--fg); }
.evo-seg > button.on { background:var(--accent); color:var(--accent-on); }
.evo-bf { margin-left:6px; padding:1px 7px; font-size:0.68rem; border-radius:var(--radius-sm); background:transparent;
  color:var(--dim); border:var(--border-w) var(--border-style) var(--border-color); cursor:pointer; }
.evo-bf:hover { color:var(--fg); }
.evo-idx { padding:1px 7px; font-size:0.7rem; border-radius:var(--radius-sm); background:transparent;
  color:var(--dim); border:1px solid color-mix(in srgb, var(--curve) 45%, transparent); cursor:pointer; }
.evo-idx:hover { color:var(--fg); }
.evo-idx.on { color:var(--accent-on); background:var(--curve); border-color:var(--curve); }
.evo-card { position:absolute; z-index:6; min-width:132px; padding:5px 8px 6px;
  background:color-mix(in srgb, var(--card) 94%, transparent); border:var(--border-w) var(--border-style) var(--border-color);
  border-radius:var(--radius-sm); box-shadow:var(--shadow-pop); }
.evo-card-t { font-family:var(--font-mono); font-size:0.7rem; color:var(--fg); margin-bottom:3px; }
.evo-card-r { display:flex; align-items:baseline; gap:5px; font-size:0.7rem; line-height:1.5; white-space:nowrap; }
.evo-card-k { flex:0 0 auto; }
.evo-card-l { color:var(--dim); }
.evo-card-v { margin-left:auto; font-family:var(--font-mono); font-variant-numeric:tabular-nums; }
.evo-card-v.up { color:var(--up); }
.evo-card-v.down { color:var(--down); }
.evo-card-x { float:right; margin-left:8px; cursor:pointer; color:var(--dim); font-size:0.8rem; line-height:1; }
.evo-card-x:hover { color:var(--fg); }
.evo-cw { display:flex; gap:8px; align-items:stretch; max-height:300px; }
.evo-cw .st-chart { flex:1 1 auto; min-width:0; }
.evo-pane:first-child > svg.st-chart { flex:0 0 auto; height:auto; }
.evo-rank { flex:0 0 158px; display:flex; flex-direction:column; gap:1px; max-height:300px; overflow-y:auto; }
.evo-rank-row { display:flex; align-items:center; gap:5px; padding:1px 5px; border-radius:var(--radius-sm); cursor:pointer; font-size:0.72rem; line-height:1.6; }
.evo-rank-row:hover { background:var(--card2); }
.evo-rank-row.hi { background:var(--card2); }
.evo-rank-row.sel { background:var(--card2); outline:1px solid var(--accent); }
/* 2026-10-28（N20261028-140538-40）：正负对比 5+5 的分组标（正向／负向） */
.evo-rank-sep { font-size:0.62rem; line-height:1.5; color:var(--dim); padding:2px 5px 0; letter-spacing:.04em; }
.evo-rank-sep + .evo-rank-row { padding-top:0; }
.evo-hit { pointer-events:stroke; cursor:pointer; }
.st-chart .evo-line { pointer-events:none; }
.evo-rank-dot { width:7px; height:7px; border-radius:50%; flex:0 0 7px; }
.evo-rank-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.evo-rank-val { font-family:var(--font-mono); font-variant-numeric:tabular-nums; white-space:nowrap; }
.evo-lg { display:flex; flex-wrap:wrap; gap:2px 12px; margin-top:4px; }
/* 左图信息行（2026-10-26 从废弃的「板块轨迹明细」卡吸收过来）：一行小字，紧贴图下、图例上方 */
.evo-info { display:flex; flex-wrap:wrap; align-items:baseline; gap:1px 11px; margin:4px 0 0;
  font-size:0.6875rem; color:var(--dim); line-height:1.5; }
.evo-info .evo-ii { white-space:nowrap; }
.evo-info .evo-ii i { font-style:normal; opacity:.85; margin-right:3px; }
.evo-info .evo-ii b { font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-weight:600; }
.evo-lg-i { display:inline-flex; align-items:center; gap:4px; font-size:0.72rem; color:var(--dim); }
.evo-lg-d { width:8px; height:8px; border-radius:2px; flex:0 0 8px; }

/* ---- 旧页 1614-1655：网格视图 + 四象图 ---- */
.st-grids { display:grid; grid-template-columns:1fr 1fr; gap:var(--d-gap); }
.st-panel { min-width:0; }
.st-cells { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
.st-col { display:flex; flex-direction:column; gap:3px; }
.st-col-hd { color:var(--dim); font-size:0.75rem; margin-bottom:4px; }
.st-cell { position:relative; display:flex; flex-direction:column; gap:2px; cursor:pointer;
  background:var(--card2); border-radius:var(--radius-sm); padding:5px 8px; }
.st-cell:hover { background:var(--card); }
.st-cell-h { display:flex; align-items:baseline; justify-content:space-between; gap:6px; }
.st-cell-n { font-size:0.75rem; color:var(--fg); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.st-cell-rk { font-size:0.68rem; color:var(--dim); font-family:var(--mono); white-space:nowrap; }
.st-cell-rk b { margin-left:3px; }
.st-cell-rk b.up { color:var(--up); } .st-cell-rk b.down { color:var(--down); }
.st-cell-sp { display:block; width:100%; height:22px; }
.st-cell-f { display:flex; align-items:baseline; justify-content:space-between; gap:4px; }
.st-cell-dw { font-size:0.68rem; color:var(--dim); font-family:var(--mono); }
.st-cell-v { font-size:0.72rem; font-family:var(--mono); text-align:right; }
.st-cell-f .st-cell-v:first-child { text-align:left; font-weight:600; }
.st-quad { display:block; width:100%; height:auto; background:var(--card2); border-radius:var(--radius-sm); }
.st-quad .st-grid { stroke:var(--line-soft); stroke-width:1; vector-effect:non-scaling-stroke; }
.st-quad .st-base { stroke:var(--dim); stroke-width:1; stroke-dasharray:4 3; vector-effect:non-scaling-stroke; }
.st-quad .st-ytx, .st-quad .st-xtx { fill:var(--dim); font-size:9px; }
.st-quad .st-qlab { fill:var(--dim); font-size:10px; opacity:.7; }
.st-quad .st-lab { font-size:10px; }
.st-quad .st-bub { cursor:pointer; }
.st-quad .st-bub:hover circle:last-child { stroke-width:2.6; }
.st-quad .st-ql.pin .st-lab { font-weight:700; }
.st-quad .st-blab { font-size:10px; dominant-baseline:middle; pointer-events:none; }
.st-quad .st-blab-h { font-size:10px; dominant-baseline:middle; pointer-events:none; fill:var(--card2); stroke:var(--card2); stroke-width:2.6; stroke-linejoin:round; }
.st-quad .st-lead { stroke:var(--dim); stroke-width:0.9; opacity:.55; pointer-events:none; }
.st-quad .st-tg { fill:none; stroke-width:5; stroke-linecap:round; stroke-dasharray:14 86; opacity:0; pointer-events:none; }
.st-quad .st-tf { fill:none; stroke-width:2; stroke-linecap:round; stroke-dasharray:6 94; opacity:0; pointer-events:none; }
@keyframes stFlow { from { stroke-dashoffset:100; } to { stroke-dashoffset:0; } }
/* 「返回全A / 回成分股」忙碌态的小脉冲点（规则在 research.css.js 的 .rs-back.busy::before；
   @keyframes 只能放这里：research.css.js 的守门要求每行带 .it-card 前缀，@ 规则不过关） */
@keyframes rs-pulse { 0%,100% { opacity:.25; transform:scale(.75); } 50% { opacity:1; transform:scale(1); } }
@media (prefers-reduced-motion: reduce) { .st-quad .st-tf, .st-quad .st-tg { display:none; } }
`;

/** 给每条规则加 `.it-card` 前缀（去掉注释行与 @ 规则，@keyframes 原样保留） */
function scope(root) {
  return root.split('\n').map((line) => {
    const t = line.trim();
    if (!t || t.startsWith('/*') || t.startsWith('*') || t.startsWith('@') || t.startsWith('}')) return line;
    // 选择器段（到第一个 { 为止）；声明段（含 : 且无 {）不动
    const brace = t.indexOf('{');
    if (brace < 0) return line;
    const head = t.slice(0, brace).trim();
    const tail = t.slice(brace);
    if (!head || head.startsWith('--')) return line;
    const scoped = head.split(',').map((sel) => {
      const s = sel.trim();
      if (!s) return s;
      return '.it-card ' + s;
    }).join(', ');
    return '  ' + scoped + ' ' + tail;
  }).join('\n');
}

export const CARD_CSS = `
.it-card { position:relative; --mono: var(--font-mono);
  /* 六指数叠加按钮的线色由 JS 逐元素内联（--curve: 调色板某一色），
     这里给一个默认值：既是缺省兜底，也让静态门禁能看到本文件自己声明了这个变量。 */
  --curve: var(--accent); }
${scope(OLD_RULES)}
`;

export function injectCardCss(doc) {
  const ID = 'intraday-legacy-css';
  if (doc.getElementById(ID)) return;
  const s = doc.createElement('style');
  s.id = ID;
  s.textContent = CARD_CSS;
  doc.head.appendChild(s);
}
