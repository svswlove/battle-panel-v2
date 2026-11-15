/**
 * intraday 卡 · 审计明细 / 会话行 / 成交额对比样式
 * （照抄 engine/web/index.html 的 <style>，不重新设计）
 *
 * 规则来源（逐条对回旧页行号，改样式时能直接查基准）：
 *   962-969    .kpi 小卡（成交额对比卡的外壳） + .kpi .k 标题
 *   1358       .sess-line 会话行
 *   1540-1556  .to-cards / .top-split 三小卡竖排等高拉伸（2026-10-25 排版对齐旧页时补）
 *   1544       .to-cards .kpi .src（成交额区来源行小字）
 *   1558-1560  .to-kv / .to-lab / .to-val（三行 KV，左标签右数值）
 *   1660-1661  .fold / .fold > summary（审计明细折叠壳）
 *   1671-1674  .to-chart / .to-today / .to-prev / .to-mid（成交额分时线，本次同补）
 *   969-978    旧页全局 table / th / td / .up / .down（本组只搬需要的那几条）
 *
 * 为什么另开一个模块而不塞进 card.css.js：
 *   card.css.js 是第一步（板块轮动·当天）的搬运，已验收；本组样式来源是另一批行号
 *   （962-978 / 1358 / 1544 / 1558-1661），混在一起以后没法逐块回查。
 *
 * 三处**有意偏离**（都是「整页 → 卡片」的必然结果，不是设计变更）：
 *   ① 所有选择器加 .it-card 前缀（与 card.css.js / research.css.js 同策略）：
 *      旧页整页独占，.up / .down / .src / .muted / .fold 这类通用类名不加前缀会污染别的卡。
 *   ② 不搬旧页 .kpi 的卡片装饰（边框 / 阴影 / backdrop-filter / clip-path）：
 *      卡内再套一层「带阴影的卡」视觉上是在卡里又长了一张卡。这里只留底色、圆角与内边距，
 *      边框与阴影交给外层 .it-sec。（旧页那张 .kpi 本来就是套在 card() 里的）
 *   ③ .fold 的 margin-top 改 0：卡片用 flex gap 分隔块，旧页 .fold{margin-top:var(--d-gap)}
 *      会变成双倍间距。padding / 字号 / 颜色全部照旧。
 *
 * 类名分工（旧页没有这两级区分，是卡片拆分带来的）：
 *   .it-audit-card  审计明细里 5 张表的容器（= 旧页 card() 产出的 <div class="card"><h2>…）
 *   .it-to-card     成交额对比小卡（= 旧页 <div class="kpi"><div class="k">成交额对比</div>…）
 * 两者外观不同（前者是表容器，后者是小卡），故不合并成一个类。
 *
 * 颜色只走 themes.css token，零裸 hex、零自拼 rgba。
 */

/** 旧页 962-978 / 1358 / 1544 / 1558-1661 本组用到的规则（值逐字照抄，只改类名/作用域） */
const OLD_RULES = `
/* ---- 旧页 1660-1661：审计明细折叠壳 ---- */
.fold { margin-top:0; }
.fold > summary { cursor:pointer; color:var(--dim); font-size:0.8125rem; padding:6px 0; }
.fold > summary:hover { color:var(--fg); }

/* ---- 旧页 1661 之后：折壳里的 5 张表 = 旧页 card() 的块级间隔 ---- */
.fold .it-audit-card { margin-top:8px; }
.fold .it-audit-card h2 { font-size:0.82rem; color:var(--accent); font-weight:600; margin-bottom:5px; }

/* ---- 旧页 969-978：表（旧页是全局 th,td，这里只作用于审计块；窄屏横向滚动 = 旧页 975 的意图） ---- */
.it-audit-card { min-width:0; overflow-x:auto; }
.it-audit-card table { width:100%; border-collapse:collapse; font-size:0.75rem; font-variant-numeric:tabular-nums; }
.it-audit-card th { color:var(--dim); font-weight:normal; white-space:nowrap; border-bottom:1px solid var(--line-soft); padding:3px 6px; text-align:left; vertical-align:top; }
.it-audit-card td { border-bottom:1px solid var(--line-soft); padding:3px 6px; text-align:left; vertical-align:top; }
.it-audit-card .up { color:var(--up); }
.it-audit-card .down { color:var(--down); }
.it-audit-card .muted { color:var(--dim); }

/* ---- 旧页 962-969 + 1544：成交额对比小卡（去卡片装饰，见文件头偏离 ②） ---- */
.it-to-card { background:var(--card2); border-radius:var(--radius-md); padding:10px 12px; min-width:0; }
.it-to-card .it-to-h { color:var(--dim); font-size:0.75rem; }
.it-to-card .src { font-size:0.6875rem; line-height:1.4; }

/* ---- 旧页 1540-1556：成交额左栏 3 张小卡竖排、等高拉伸（v12 2026-10-08 效果图） ---- */
.to-cards { display:flex; flex-direction:column; gap:12px; }
/* 旧页 1552：.top-split .to-cards{flex:1} —— 左栏填满整卡高，3 张卡再等分（不填就只占内容高、下方留大片空白）。
   注意：本段在模板字符串里，注释里不能出现反引号（会把字符串提前截断）。 */
.it-turnover-host { display:flex; flex-direction:column; flex:1 1 auto; min-width:0; }
.it-turnover-host > .to-cards { flex:1 1 auto; }
.to-cards > .it-to-card { flex:1; min-width:0; }
.to-cards > .to-c-chart { display:flex; flex-direction:column; }
/* 图表卡：分时图随卡片增高填满，不再固定 48px 留白 */
.to-cards .to-chart { flex:1; height:auto; min-height:48px; }

/* ---- 旧页 1671-1674：成交额分时线（今日实线 / 昨日同时刻虚线 / 午间分隔线） ---- */
.to-chart { display:block; width:100%; height:56px; background:var(--card2); border-radius:var(--radius-sm); }
.to-chart .to-today { fill:none; stroke:var(--accent); stroke-width:1.4; vector-effect:non-scaling-stroke; }
.to-chart .to-prev { fill:none; stroke:var(--dim); stroke-width:1.2; stroke-dasharray:4 3; vector-effect:non-scaling-stroke; }
.to-chart .to-mid { stroke:var(--line-soft); stroke-width:1; vector-effect:non-scaling-stroke; }

/* ---- 旧页 1558-1560：三行 KV（左标签 / 右数值 / 等宽数字，值右对齐） ---- */
.to-kv { display:flex; align-items:baseline; justify-content:space-between; gap:8px; margin-top:3px; }
.to-kv .to-lab { color:var(--dim); font-size:0.75rem; white-space:nowrap; }
.to-kv .to-val { font-weight:bold; font-size:1rem; text-align:right; font-family:var(--font-mono); font-variant-numeric:tabular-nums; }

/* ---- 旧页 1358：会话行（一行文本，紧跟冰点卡） ---- */
.sess-line { color:var(--dim); font-size:0.75rem; margin:0; padding:2px 0; }
`;

/** 单条选择器串 → 逐个加 .it-card 前缀（与 card.css.js / research.css.js 同策略） */
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
    const head = t.slice(0, brace);
    const tail = t.slice(brace);
    return '  ' + scopeSel(head) + ' ' + tail;
  }).join('\n');
}

export const AUDIT_CSS = scope(OLD_RULES);

export function injectAuditCss(doc) {
  const ID = 'intraday-audit-css';
  if (doc.getElementById(ID)) return;
  const s = doc.createElement('style');
  s.id = ID;
  s.textContent = AUDIT_CSS;
  doc.head.appendChild(s);
}
