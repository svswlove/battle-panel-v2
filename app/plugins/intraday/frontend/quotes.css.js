/**
 * intraday 卡 · 实时行情 KPI 样式
 * （照抄 engine/web/index.html 的 <style>，不重新设计）
 *
 * 规则来源（逐条对回旧页行号）：
 *   961       .grid（小卡自动填充网格，最小 240px）
 *   962-969   .kpi 小卡 + .kpi .k / .kpi .v
 *   1663-1667 .mq-kpi 背景迷你线（近 60 日 AMV 实线 + MA10 虚线，压在数字下面）
 *
 * 三处**有意偏离**（都是「整页 → 卡片」的必然结果）：
 *   ① 所有选择器加 .it-card 前缀（与 card.css.js / research.css.js / audit.css.js 同策略）。
 *   ② .grid 的最小列宽保持旧页原值 240px（曾为窄屏降到 150px，2026-10-25 排版对齐旧页时改回：
 *      150px 在 1476 宽下排出 9 列 153px 小卡，而旧页是 5 列 285.6px，且末行落单）。
 *   ③ 不搬 .kpi 的卡片装饰（边框 / 阴影 / backdrop-filter / clip-path）：
 *      卡内再套一层带阴影的卡，视觉上是在卡里又长了一张卡；只留底色、圆角、内边距
 *      与 .kpi .k / .kpi .v 的字号字色（旧页那张 .kpi 本来就套在 card() 里）。
 *
 * 背景线的三层叠放照旧页 1664-1665 原话：svg 绝对定位铺满，其余子元素 z-index:1 压在上面。
 * 颜色只走 themes.css token，零裸 hex、零自拼 rgba。
 */

/** 旧页 961-969 / 1663-1667 本组用到的规则（值逐字照抄，只改类名与作用域） */
const OLD_RULES = `
/* ---- 旧页 961-969：小卡网格（minmax 下限 = 旧页原值 240px） ---- */
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:var(--d-gap); }

/* ---- 旧页 962-969：小卡底 + 标题 + 主值（去卡片装饰，见文件头偏离 ③） ---- */
.kpi { background:var(--card2); border-radius:var(--radius-md); padding:9px 11px; min-width:0; }
.kpi .k { color:var(--dim); font-size:0.75rem; }
.kpi .v { font-size:1.25rem; font-weight:bold; margin-top:2px; font-variant-numeric:tabular-nums; }

/* ---- 旧页 1663-1667：实时行情行背景迷你线（近 60 日 AMV + MA10） ---- */
.mq-kpi { position:relative; overflow:hidden; }
.mq-kpi > *:not(.mq-spark) { position:relative; z-index:1; }
.mq-spark { position:absolute; inset:0; width:100%; height:100%; opacity:.30; pointer-events:none; }
.mq-spark-amv { fill:none; stroke:var(--accent); stroke-width:1.1; vector-effect:non-scaling-stroke; }
.mq-spark-ma { fill:none; stroke:var(--dim); stroke-width:0.9; stroke-dasharray:3 2; vector-effect:non-scaling-stroke; }
`;

/** 单条选择器串 → 逐个加 .it-card 前缀（与前几个 css 模块同策略） */
function scopeSel(head) {
  const h = head.trim();
  if (!h || h.startsWith('--')) return head;
  return h.split(',').map((sel) => {
    const s = sel.trim();
    if (!s) return s;
    return '.it-card ' + s;
  }).join(', ');
}

/** 给每条规则加 .it-card 前缀（跳过注释行 / 无花括号行 / @ 规则 / 右花括号） */
function scope(root) {
  return root.split('\n').map((line) => {
    const t = line.trim();
    if (!t || t.startsWith('/*') || t.startsWith('*') || t.startsWith('@') || t.startsWith('}')) return line;
    const brace = t.indexOf('{');
    if (brace < 0) return line;
    return '  ' + scopeSel(t.slice(0, brace)) + ' ' + t.slice(brace);
  }).join('\n');
}

export const QUOTES_CSS = scope(OLD_RULES);

export function injectQuotesCss(doc) {
  const ID = 'intraday-quotes-css';
  if (doc.getElementById(ID)) return;
  const s = doc.createElement('style');
  s.id = ID;
  s.textContent = QUOTES_CSS;
  doc.head.appendChild(s);
}
