/**
 * intraday 卡 · 板块与个股研究样式（照抄 engine/web/index.html 的 <style>，不重新设计）
 *
 * 规则来源（逐条对回旧页行号，改样式时能直接查基准）：
 *   1483-1537  v14「板块与个股研究」整段（rs-split / rs-col / rs-hd / rs-ttl / rs-back /
 *              rs-scroll / rs-tbl / rs-sp / rs-parts / rs-part / rs-part-bar / rs-parts-f）
 *   1331-1335  k-name / k-nolink（成分股名可点态，旧页 kName 产出）
 *
 * 为什么另开一个模块而不是塞进 card.css.js：
 *   card.css.js 那段是第一步（板块轮动·当天）的搬运，已验收；研究卡是新一块，
 *   两块的旧页行号来源不同（1360-1482 vs 1483-1537），混在一起以后没法逐块回查。
 *
 * 两处有意偏离旧页（都是「整页 → 卡片」的必然结果）：
 *   ① 所有选择器加 .it-card 前缀：旧页整页独占，卡片里 .src / .muted / .sel 这类通用类名
 *      不加前缀会污染别的卡（与 card.css.js 同一处理）。
 *   ② 颜色只走主题 token：旧页这段本来就全是 var(--*)，无需换色，也没有裸 hex 与裸 rgba。
 *
 * 置灰态（旧页 1335 .k-nolink 的 opacity:0.75）原样照抄，不是 rgba，不受裸 rgba 门禁影响。
 */

/** 旧页 1483-1537 + 1331-1335：研究卡规则原文（逐字） */
const OLD_RULES = `
/* ---- 旧页 1485-1487：两栏（左 1fr 板块排名 / 右 1.1fr 成分股），窄屏单列 ---- */
.rs-split { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.1fr); gap:var(--d-gap); align-items:stretch; }
@media(max-width:900px){ .rs-split { grid-template-columns:minmax(0,1fr); } }
.rs-col { display:flex; flex-direction:column; min-width:0; max-height:520px; }

/* ---- 旧页 1489-1495：标题行 + 小按钮（返回全A / 回成分股） ---- */
.rs-hd { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin:5px 0 3px; flex:0 0 auto; }
.rs-ttl { font-size:0.78rem; color:var(--fg); font-weight:600; }
.rs-back { margin-left:2px; padding:1px 7px; font-size:0.68rem; line-height:1.5; border-radius:var(--radius-sm);
  background:var(--card); color:var(--dim); border:var(--border-w) var(--border-style) var(--border-color);
  cursor:pointer; white-space:nowrap; flex:0 0 auto; }
.rs-back:hover { color:var(--fg); background:var(--card2); }
/* 忙碌态（主人 2026-10-26：点「返回全A / 回成分股」到数据回来有 1~1.5s 静默期，必须有反馈）
   选禁用 + 文案「…」+ 小脉冲点：不弹框、不遮表，与「补齐缺口 → 检查中…」同一套语气 */
.rs-back.busy { cursor:progress; color:var(--accent); border-color:var(--accent); background:var(--card); }
.rs-back.busy::before { content:""; display:inline-block; width:6px; height:6px; margin-right:5px;
  border-radius:50%; background:var(--accent); vertical-align:1px; animation:rs-pulse .9s ease-in-out infinite; }
/* @keyframes rs-pulse 放在 card.css.js（同卡样式表，那份才允许 @ 规则） */
@media (prefers-reduced-motion: reduce) { .it-card .rs-back.busy::before { animation:none; opacity:1; transform:none; } }

/* ---- 旧页 1496-1513：滚动容器 + 表格（表头 sticky，数字右对齐等宽） ---- */
.rs-scroll { flex:1 1 auto; min-height:0; overflow:auto; border-radius:var(--radius-sm); }
table.rs-tbl { width:100%; border-collapse:collapse; font-size:0.72rem; }
.rs-tbl thead th { position:sticky; top:0; z-index:1; background:var(--card); color:var(--dim); font-weight:400;
  padding:2px 5px; border-bottom:1px solid var(--line-soft); white-space:nowrap; }
.rs-tbl thead th.n { text-align:right; }
.rs-tbl thead th.l { text-align:left; }
.rs-tbl thead th[data-k] { cursor:pointer; }
.rs-tbl thead th[data-k]:hover { color:var(--fg); }
.rs-tbl thead th.on { color:var(--accent); }
.rs-tbl tbody td { padding:1px 5px; border-bottom:1px solid var(--line-soft); text-align:right;
  font-family:var(--font-mono); font-variant-numeric:tabular-nums; white-space:nowrap; }
.rs-tbl tbody td.rk { color:var(--dim); }
.rs-tbl tbody td.nm { text-align:left; font-family:inherit; max-width:9.5em; overflow:hidden; text-overflow:ellipsis; }
.rs-tbl tbody td.nm .cd { color:var(--dim); font-size:0.66rem; margin-left:4px; font-family:var(--font-mono); }
.rs-tbl tbody td.sp { width:96px; padding:0 5px; }
.rs-tbl tbody tr.pk { cursor:pointer; }
.rs-tbl tbody tr.pk:hover { background:var(--card2); }
.rs-tbl tbody tr.sel { background:var(--card2); box-shadow:inset 2px 0 0 var(--accent); }

/* ---- 旧页 1514-1518：近 30 日 AMV 缩略线（上红 / 下绿 / MA10 虚线） ---- */
.rs-sp { display:block; width:88px; height:22px; overflow:visible; }
.rs-sp path { fill:none; stroke-width:1.3; vector-effect:non-scaling-stroke; }
.rs-sp .sp-up { stroke:var(--up); }
.rs-sp .sp-dn { stroke:var(--down); }
.rs-sp .sp-ma { stroke:var(--dim); stroke-dasharray:1 2; opacity:.7; }

/* ---- 旧页 1519-1537：指数贡献分项条（中栏顶部） ---- */
.rs-parts { background:var(--card2); border-radius:var(--radius-sm); padding:7px 9px; margin-bottom:6px; flex:0 0 auto; }
.rs-parts-h { display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin-bottom:5px; }
.rs-parts-t { font-size:0.8rem; font-weight:600; }
.rs-open { padding:2px 9px; font-size:0.72rem; border-radius:var(--radius-sm); background:var(--card);
  color:var(--dim); border:var(--border-w) var(--border-style) var(--border-color); cursor:pointer; white-space:nowrap; }
.rs-open:hover { color:var(--fg); }
.rs-part { display:grid; grid-template-columns:5.6em minmax(40px,1fr) 4.8em; align-items:center; gap:7px; font-size:0.7rem; }
.rs-part + .rs-part { margin-top:3px; }
.rs-part-k { color:var(--dim); white-space:nowrap; }
.rs-part-bar { position:relative; height:7px; background:var(--line-soft); border-radius:3px; overflow:hidden; }
.rs-part-bar::before { content:''; position:absolute; left:50%; top:0; bottom:0; width:1px; background:var(--line); }
.rs-part-bar i { position:absolute; top:0; bottom:0; border-radius:2px; }
.rs-part-bar i.up { background:var(--up); }
.rs-part-bar i.down { background:var(--down); }
.rs-part-v { text-align:right; font-family:var(--font-mono); font-variant-numeric:tabular-nums; }
.rs-part.zero .rs-part-v { color:var(--dim); }
.rs-parts-f { margin-top:6px; padding-top:5px; border-top:1px solid var(--line-soft); font-size:0.72rem; color:var(--dim); }
.rs-parts-f b { font-family:var(--font-mono); font-size:0.82rem; }
.rs-parts-f .src { margin-left:6px; }

/* ---- 旧页 1331-1335：个股名可点态（kName 产出 .k-name / .k-nolink） ---- */
.k-name { cursor:pointer; padding:0 1px; border-radius:2px; }
.k-name:hover { color:var(--accent); text-decoration:underline; text-underline-offset:2px; }
.k-nolink { cursor:default; opacity:0.75; }
`;

/** 单条选择器串 → 逐个加 .it-card 前缀（空串 / 变量声明行原样返回） */
function scopeSel(head) {
  const h = head.trim();
  if (!h || h.startsWith('--')) return head;
  return h.split(',').map((sel) => {
    const s = sel.trim();
    if (!s) return s;
    return '.it-card ' + s;
  }).join(', ');
}

/**
 * 给每条规则加 .it-card 前缀（与 card.css.js 的 scope 同策略：跳过注释 / 声明行）。
 *
 * 比 card.css.js 多处理一处：**单行 @media 里的内层选择器也要加前缀**。
 * 旧页 1486 是 `@media(max-width:900px){ .rs-split { ... } }` 一行写完，
 * card.css.js 那版对 @ 开头的行整行跳过，于是 .rs-split 会漏成全局选择器
 * （第一步没有单行 @media，所以那边没暴露）。这里把 @media 的内层补上。
 */
function scope(root) {
  return root.split('\n').map((line) => {
    const t = line.trim();
    if (!t || t.startsWith('/*') || t.startsWith('*') || t.startsWith('}')) return line;
    if (t.startsWith('@')) {
      // @media(cond){ 内层选择器 { 声明 } } —— 只重写内层那段，其余原样
      const m = /^(@[^{]+\{)([\s\S]*?)(\{[^{}]*\})([^{}]*)\}\s*$/.exec(t);
      if (!m) return line;
      return '  ' + m[1] + ' ' + scopeSel(m[2]) + ' ' + m[3] + m[4] + '}';
    }
    const brace = t.indexOf('{');
    if (brace < 0) return line;
    const head = t.slice(0, brace);
    const tail = t.slice(brace);
    return '  ' + scopeSel(head) + ' ' + tail;
  }).join('\n');
}

export const RESEARCH_CSS = scope(OLD_RULES);

export function injectResearchCss(doc) {
  const ID = 'intraday-research-css';
  if (doc.getElementById(ID)) return;
  const s = doc.createElement('style');
  s.id = ID;
  s.textContent = RESEARCH_CSS;
  doc.head.appendChild(s);
}
