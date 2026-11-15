// ============================================================================
// 复盘卡片 · 前端（docs/task_R3_concept_review.md，0.1.0 → 0.2.0）
// ----------------------------------------------------------------------------
// 行为基准 = 旧复盘 Tab（engine/web/index.html 8014 行 renderReviewMd 起）：
//   旧端降级链「图表模板 → 纸面 → v6 深色」是**自动**回退；本卡把它做成**显式三视图**
//   （图表 / 纸面 / 文档），每条视图仍保留同一条自动降级链，任一级取数失败即往下走一级：
//     · 图表：GET /api/review_chart.json → window.RVC.render(D, mount, mode)
//       （组件库 = 旧仓 review_chart.js，同源静态 /legacy/review_chart.js，懒加载一次）
//       ok!==true / 缺 head+meta / 组件库不可用 / 渲染抛错 → 回退纸面
//     · 纸面：GET /api/review.md?paper=1 → paper:true 且未降级且有正文才算成功
//       （服务端派生 CSS 只作用于 `#panel-review .rv-paper`，故本卡把 root.id 设为
//        panel-review，与旧页容器同名；CSS 由本卡注入，卸载时移除）
//       → 失败回退 v6
//     · 文档 v6：GET /api/review.md?theme=<当前主题> → 正文 HTML + 图表插槽
//       （旧 7897 的 blockquote 成对转换逐字搬入：⚠→warning，💡/📊→highlight）
//   档位（简报 / 完整）沿用旧键 localStorage panel_rv_mode；缺省 brief。
//
// 主题：v6 与图表模板跟 document.documentElement 的 data-theme 走（themes.css 语义 token），
//   主题变化时清签名重取一次（旧端 MutationObserver 同口径）；纸面配色按旧端设计**固定**
//   （report-builder 暖白纸，不随主题变），故主题切换不动纸面视图。
//
// 端点（review 插件 mount=root，路径与旧端同名，故用绝对 /api/ 前缀）：
//   GET /api/review.md?theme=&date=     → v6 10 键
//   GET /api/review.md?paper=1          → 14 键（含 paper/masthead/css/footer）
//   GET /api/review_chart.json          → 图表模板 RV_DATA
//
// 契约（docs/CONTRACT.md §5）：
//   1. 原生 ES module，零 import、零构建；只操作自己的 root。
//   2. 只走 ctx.api.get；样式表从旧页逐字搬入（含旧页既有 --judge-* 暖橙三值），
//      本卡自有骨架一律 themes.css 语义 token。
//   3. 骨架只建一次，内容走字符串比对（内容没变不写 DOM，防闪）。
//   4. 不声明 refreshEvery（复盘 MD 日频）；保留 refresh() 占位 + 卡头手动刷新。
//   5. 个股名只带 data-kname / data-open-kline 占位，点击行为由旧页桥接接管。
// ============================================================================

const STYLE_ID = 'review-card-css';
const PAPER_CSS_ID = 'review-paper-css';
const LS_MODE = 'panel_rv_mode';        // 与旧页同键：简报 / 完整
const RVC_SRC = '/legacy/review_chart.js';
// 2026-11-15（标注 N93 主人）：删「图表」视图——有效信息太少。默认落在「纸面」。
//   chart 的渲染函数与组件库懒加载代码保留（无入口不执行），仅从视图表与默认值里拿掉。
const VIEWS = [['paper', '纸面'], ['doc', '文档'], ['six', '六格']];

// ---------------------------------------------------------------- 表格个股名后处理
// 口径逐条照旧页 `kTsPlayable` + `wireReviewTableKNames`（engine/web/index.html）：
//   · 带市场后缀（.SH/.SZ/.BJ）→ 可跳；裸 6 位码仅当首位 ∈ {0,2,3,5,6,9}；
//   · 情形 A：本格是纯代码、相邻格是名称（含中文）→ 名称格变可点；
//   · 情形 B：名称与代码同格（`药明康德 603259`）→ 同格剥离代码。
// 差异：旧页挂 onclick 直调 KLineApp；本卡在 iframe 里，改成 data-open-kline 占位，
//   由 shell 的捕获阶段委托发 postMessage 给父页（R5 桥），**不在卡内复刻打开逻辑**。
function kTsPlayable(ts) {
  const s = (ts === null || ts === undefined) ? '' : String(ts).trim();
  if (!s) return false;
  if (/^\d{6}\.(SH|SZ|BJ)$/.test(s)) return true;
  return /^[023569]\d{5}$/.test(s);
}

function wireTableKNames(scope) {
  if (!scope || !scope.querySelectorAll) return 0;
  const hasCn = (s) => /[\u4e00-\u9fa5]/.test(s);
  let n = 0;
  scope.querySelectorAll('table tr').forEach((tr) => {
    const tds = Array.from(tr.children).filter((td) => td.tagName === 'TD');
    if (!tds.length) return;
    const codeAt = {};
    tds.forEach((td, i) => {
      if (td.classList.contains('k-name')) return;
      const t = (td.textContent || '').trim();
      if (kTsPlayable(t)) codeAt[i] = t;
    });
    tds.forEach((td, i) => {
      if (td.classList.contains('k-name')) return;         // 幂等：已转过
      if (td.querySelector('.k-name')) return;
      const raw = (td.textContent || '').trim();
      if (!raw) return;
      if (codeAt[i]) {
        // 情形 A：纯代码格 → 找相邻含中文的名称格
        const nb = [i + 1, i - 1].map((j) => tds[j]).filter(Boolean);
        const nmTd = nb.find((x) => !x.classList.contains('k-name')
          && !x.querySelector('.k-name') && hasCn((x.textContent || '').trim()));
        if (nmTd) {
          const nmTxt = (nmTd.textContent || '').trim();
          nmTd.innerHTML = '';
          nmTd.append(makeKName(nmTxt, codeAt[i]));
          n += 1;
        }
        return;
      }
      // 情形 B：名称 + 代码同格
      const m = raw.match(/(?:^|\s)(\d{6}(?:\.[A-Z]{2})?)(?:\s|$)/);
      if (!m) return;
      const code = m[1];
      if (!kTsPlayable(code)) return;
      const name = raw.replace(m[0], ' ').trim();
      if (!name) return;
      td.innerHTML = '';
      td.append(makeKName(name, code));
      n += 1;
    });
  });
  return n;
}

function makeKName(name, code) {
  const span = document.createElement('span');
  span.className = 'k-name';
  span.setAttribute('data-open-kline', code);
  span.setAttribute('data-open-name', name);
  span.setAttribute('title', code);
  span.textContent = name;
  return span;
}

// ---------------------------------------------------------------- 样式
// CSS 段来源：旧页 engine/web/index.html 内联 <style> 的复盘相关规则**逐字搬入**
//   A 段 = 选择器含 `#panel-review` 的规则 + `tr.todo-done`（v6 正文 / 顶行 / 变量块）
//   B 段 = 选择器含 `.rv-` 的规则（图表模板 rv-* 类）
// 为什么不改写选择器：旧页 CSS 全挂在 #panel-review 下，本卡把 root.id 设成同名容器即可
// 原样复用（不复制第二份样式口径、不逐条改写引入偏差）。
const CSS = [
  // ===== A 段：旧页 #panel-review 作用域（v6 正文 / 顶行 / 变量块）逐字搬入 =====
  "#panel-review { --rv-bg:var(--bg); --rv-panel:var(--card); --rv-panel2:var(--card2); --rv-line:var(--line);",
  "    --rv-ink:var(--fg); --rv-ink2:var(--dim); --rv-ink3:var(--dim);",
  "    --rv-up:var(--up); --rv-down:var(--down); --rv-amber:var(--warn); --rv-blue:var(--accent); --rv-purple:var(--purple-300); --rv-cyan:var(--cyan-300);",
  "    --rv-mono:var(--font-mono);",
  "    ",
  "    --rv-pad-x:calc(var(--d-font) * 1.6); --rv-read-w:calc(var(--d-font) * 60);",
  "    background:transparent; color:var(--rv-ink); font-size:var(--d-font); line-height:var(--d-rowh);",
  "    padding:0; }",
  "#panel-review .up { color:var(--rv-up); }",
  "#panel-review .down { color:var(--rv-down); }",
  "#panel-review .warn { color:var(--rv-amber); }",
  "#panel-review .muted { color:var(--rv-ink3); }",
  "#panel-review .src { color:var(--rv-ink3); font-size:0.6875rem; }",
  "#panel-review .rv-md { font-size:.86rem; line-height:1.8; color:var(--rv-ink);",
  "    padding:0 var(--rv-pad-x);",
  "    max-width:calc(var(--rv-read-w) + 2 * var(--rv-pad-x)); margin:var(--d-gap) auto; }",
  "@media (max-width:760px){",
  "  #panel-review .rv-md { padding-left:var(--d-gap); padding-right:var(--d-gap); }",
  "}",
  "#panel-review .rv-topline { color:var(--rv-ink3); font-size:0.6875rem; font-family:var(--rv-mono);",
  "    letter-spacing:.04em; padding:0 var(--rv-pad-x);",
  "    max-width:calc(var(--rv-read-w) + 2 * var(--rv-pad-x)); margin:var(--d-gap) auto 0;",
  "    overflow-wrap:anywhere; }",
  "@media (max-width:760px){",
  "  #panel-review .rv-topline { padding-left:var(--d-gap); padding-right:var(--d-gap); }",
  "}",
  "#panel-review .rv-md h1, #panel-review .rv-md h2, #panel-review .rv-md h3, #panel-review .rv-md h4 {",
  "    font-weight:600; letter-spacing:.04em; margin:24px 0 10px; color:var(--rv-ink); line-height:1.5;",
  "  }",
  "#panel-review .rv-md h2 { font-size:1.1rem; padding-left:10px; border-left:4px solid var(--rv-amber); color:var(--rv-ink); }",
  "#panel-review .rv-md h3 { font-size:.98rem; color:var(--rv-blue); }",
  "#panel-review .rv-md h4 { font-size:.88rem; color:var(--rv-ink); }",
  "#panel-review .rv-md p { margin:8px 0; color:var(--rv-ink2); }",
  "#panel-review .rv-md p b, #panel-review .rv-md li b, #panel-review .rv-md td b { color:var(--rv-ink); font-weight:600; }",
  "#panel-review .rv-md strong { color:var(--rv-ink); font-weight:600; }",
  "#panel-review .rv-md span[style*=\"#c0392b\"] { color:#FF6B6B !important; }",
  "#panel-review .rv-md span[style*=\"#d9a62e\"] { color:var(--rv-amber) !important; }",
  "#panel-review .rv-md span[style*=\"#4A6B4A\"] { color:var(--down) !important; }",
  "#panel-review .rv-md span[style*=\"#888\"] { color:var(--rv-ink3) !important; }",
  "#panel-review .rv-md span[style*=\"#bbb\"] { color:var(--rv-ink3) !important; }",
  "#panel-review .rv-md blockquote:not(.warning):not(.highlight) {",
  "    border-left:3px solid var(--rv-blue); background:rgba(77,163,255,.07); color:var(--rv-ink2);",
  "  }",
  "#panel-review .rv-md ul, #panel-review .rv-md ol { margin:8px 0 8px 22px; color:var(--rv-ink2); }",
  "#panel-review .rv-md li { margin:4px 0; }",
  "#panel-review .rv-md hr { border:none; border-top:1px solid var(--line-soft); margin:22px 0; }",
  "#panel-review .rv-md code {",
  "    font-family:var(--rv-mono); font-size:.78rem; background:rgba(255,255,255,.06);",
  "    padding:1px 6px; border-radius:var(--radius-sm); color:var(--rv-blue);",
  "  }",
  "#panel-review .rv-md pre { background:var(--line-soft); border:var(--border-w) var(--border-style) var(--rv-line); border-radius:var(--radius-sm); padding:var(--d-pad); overflow-x:auto; }",
  "#panel-review .rv-md pre code { background:none; padding:0; color:var(--rv-ink2); }",
  "#panel-review .rv-md img { max-width:100%; height:auto; display:block; margin:12px 0; border-radius:var(--radius-sm); }",
  "#panel-review .rv-md .rv-chart { background:var(--rv-panel); border:var(--border-w) var(--border-style) var(--rv-line); border-radius:var(--radius-md);",
  "    padding:var(--d-pad); margin:16px 0; overflow-x:auto; }",
  "#panel-review .rv-md .rv-chart svg { display:block; margin:0 auto; max-width:100%; height:auto; min-width:52em; }",
  "#panel-review .rv-md .md-table-wrap { overflow-x:auto; margin:14px 0 18px; border:var(--border-w) var(--border-style) var(--rv-line); border-radius:var(--radius-sm); }",
  "#panel-review .rv-md table { width:100%; border-collapse:collapse; font-size:.8rem; min-width:520px; }",
  "#panel-review .rv-md th {",
  "    background:rgba(255,255,255,.05); color:var(--rv-amber); font-weight:600; text-align:left;",
  "    padding:var(--d-cellpad); border-bottom:1px solid var(--line-soft); white-space:nowrap;",
  "    font-family:var(--rv-mono); font-size:.72rem; letter-spacing:.05em;",
  "  }",
  "#panel-review .rv-md td { padding:var(--d-cellpad); border-bottom:1px solid var(--line-soft); vertical-align:top; color:var(--rv-ink2); line-height:1.7; }",
  "#panel-review .rv-md tr:last-child td { border-bottom:none; }",
  "#panel-review .rv-md blockquote {",
  "    margin:12px 0; padding:10px 14px; border-left:3px solid var(--rv-amber);",
  "    background:rgba(255,200,87,.08); border-radius:0 var(--radius-sm) var(--radius-sm) 0; color:var(--rv-ink2); font-size:.82rem;",
  "  }",
  "#panel-review .rv-md .warning { background:rgba(255,91,91,.1); border-left:4px solid var(--rv-up); color:var(--rv-ink2); padding:12px 16px; margin:14px 0; border-radius:0 var(--radius-sm) var(--radius-sm) 0; }",
  "#panel-review .rv-md .highlight { background:rgba(255,200,87,.1); border-left:4px solid var(--rv-amber); color:var(--rv-ink2); padding:12px 16px; margin:14px 0; border-radius:0 var(--radius-sm) var(--radius-sm) 0; }",
  "tr.todo-done td { opacity:.45; text-decoration:line-through; }",
  "#panel-review .rv-root section.rv-sec { display:block; }",
  // ===== B 段：旧页 rv-* 图表模板类逐字搬入 =====
  ".rv-root { max-width:1560px; margin:0 auto; }",
  ".rv-sec { margin-bottom:calc(var(--d-gap) * 1.6); }",
  ".rv-sec-hd { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;",
  "  margin:0 0 var(--d-gap); padding-bottom:6px; border-bottom:var(--border-w) var(--border-style) var(--border-color); }",
  ".rv-sec-no { font-family:var(--font-mono); font-size:0.75rem; font-weight:700; letter-spacing:.06em;",
  "  color:var(--accent-on); background:var(--accent-solid); padding:1px 8px; border-radius:var(--radius-pill); }",
  ".rv-sec-t { font-size:1.0625rem; font-weight:700; color:var(--fg-strong); }",
  ".rv-sec-sub { font-size:0.75rem; color:var(--dim); }",
  ".rv-cols { display:grid; gap:var(--d-gap); align-items:start; }",
  ".rv-cols-2 { grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }",
  ".rv-cols-3 { grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }",
  ".rv-cols-4 { grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); }",
  ".rv-cols-wide { grid-template-columns:minmax(280px,.9fr) minmax(420px,1.6fr); }",
  "@media (max-width:900px){ .rv-cols-wide { grid-template-columns:1fr; } }",
  ".rv-fold { margin-top:var(--d-gap); background:transparent;",
  "  border:1px solid var(--line-soft); border-radius:var(--radius-md);",
  "  box-shadow:none; overflow:hidden; }",
  ".rv-fold > summary { cursor:pointer; padding:var(--d-pad-sm); font-size:0.75rem; font-weight:600; color:var(--dim);",
  "  list-style:none; display:flex; align-items:center; gap:6px; }",
  ".rv-fold > summary::-webkit-details-marker { display:none; }",
  ".rv-fold > summary::before { content:'▸'; color:var(--accent); display:inline-block; transition:transform .15s; }",
  ".rv-fold[open] > summary::before { transform:rotate(90deg); }",
  ".rv-fold > summary:hover { color:var(--fg); background:var(--line-soft); }",
  ".rv-fold-b { padding:0 var(--d-pad-sm) var(--d-pad-sm); border-top:1px solid var(--line-soft); }",
  ".rv-fold-b .card { margin-top:var(--d-gap); }",
  ".rv-sen-hd { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:var(--d-gap); }",
  ".rv-stage-lg { font-size:1rem; padding:4px 16px; }",
  ".rv-temp-read { font-family:var(--font-num); font-size:1.5rem; font-weight:700; color:var(--fg-strong); }",
  ".rv-quali { margin-top:var(--d-gap); border-left:3px solid var(--warn-solid); padding:6px var(--d-pad-sm);",
  "  background:var(--card2); border-radius:0 var(--radius-md) var(--radius-md) 0; font-size:0.75rem; line-height:1.6; color:var(--fg); }",
  ".rv-quali .who { display:block; font-size:0.6875rem; color:var(--dim); margin-bottom:2px; }",
  ".rv-idx-4 { grid-template-columns:repeat(auto-fit,minmax(172px,1fr)); }",
  ".rv-idx-4 .rv-idx-v { font-size:1rem; }",
  ".rv-badge { display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:var(--radius-pill);",
  "  font-size:0.75rem; font-weight:700; white-space:nowrap; border:1px solid transparent; }",
  ".rv-badge.up,.rv-badge.pos { background:var(--up-bg); color:var(--up-ink); }",
  ".rv-badge.down,.rv-badge.neg { background:var(--down-bg); color:var(--down-ink); }",
  ".rv-badge.warn { background:var(--warn-bg); color:var(--warn-ink); }",
  ".rv-badge.danger { background:var(--danger-bg); color:var(--danger-ink); }",
  ".rv-badge.neutral,.rv-badge.none { background:var(--line-soft); color:var(--dim); }",
  ".rv-badge.accent { background:var(--accent-bg); color:var(--accent-ink); }",
  ".rv-badge.solid-warn { background:var(--warn-solid); color:var(--warn-on); }",
  ".rv-badge.solid-up { background:var(--up-solid); color:var(--up-on); }",
  ".rv-badge.solid-down { background:var(--down-solid); color:var(--down-on); }",
  ".rv-tag { display:inline-block; padding:0 6px; border-radius:var(--radius-sm); font-size:0.6875rem;",
  "  background:var(--line-soft); color:var(--dim); white-space:nowrap; }",
  ".rv-kpi { background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-radius:var(--radius-md); padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline);",
  "  display:flex; flex-direction:column; gap:3px; min-width:0; }",
  ".rv-kpi > .k { color:var(--dim); font-size:0.75rem; }",
  ".rv-kpi > .v { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:1rem; font-weight:700;",
  "  color:var(--fg); line-height:1.25; }",
  ".rv-kpi > .v small { font-size:0.6875rem; font-weight:400; color:var(--dim); margin-left:3px; }",
  ".rv-kpi > .d { font-size:0.75rem; font-family:var(--font-num); }",
  ".rv-kpi.acc { border-left:3px solid var(--accent-solid); }",
  ".rv-kpi.up-b { border-left:3px solid var(--up-solid); }",
  ".rv-kpi.down-b { border-left:3px solid var(--down-solid); }",
  ".rv-kpi.warn-b { border-left:3px solid var(--warn-solid); }",
  ".rv-kpi.danger-b { border-left:3px solid var(--danger-solid); }",
  ".rv-kpi .up { color:var(--up); }",
  ".rv-kpi .down { color:var(--down); }",
  ".rv-kv { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }",
  ".rv-kv > .kl { color:var(--dim); font-size:0.75rem; white-space:nowrap; }",
  ".rv-kv > .vv { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:0.875rem; font-weight:700;",
  "  text-align:right; white-space:nowrap; }",
  ".rv-idx-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:var(--d-gap); }",
  ".rv-idx { position:relative; overflow:hidden; background:var(--card);",
  "  border:var(--border-w) var(--border-style) var(--border-color); border-radius:var(--radius-md);",
  "  padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline); min-width:0; }",
  ".rv-idx > .rv-spark { position:absolute; inset:auto 0 0 0; width:100%; height:34%; opacity:.34; pointer-events:none; }",
  ".rv-idx > *:not(.rv-spark) { position:relative; z-index:1; }",
  ".rv-idx-n { display:flex; align-items:baseline; justify-content:space-between; gap:6px; }",
  ".rv-idx-n b { font-size:0.875rem; color:var(--fg-strong); }",
  ".rv-idx-n span { font-size:0.6875rem; color:var(--dim); font-family:var(--font-mono); }",
  ".rv-idx-v { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:1rem; font-weight:700;",
  "  color:var(--fg); line-height:1.3; }",
  ".rv-idx-p { font-family:var(--font-num); font-size:0.8125rem; font-weight:700; }",
  ".rv-idx-hl { font-size:0.6875rem; color:var(--dim); font-family:var(--font-num); font-variant-numeric:tabular-nums; }",
  ".rv-ma { display:flex; align-items:center; gap:6px; margin-top:2px; }",
  ".rv-ma-t { flex:1; position:relative; height:8px; border-radius:var(--radius-pill); background:var(--card2); overflow:hidden; }",
  ".rv-ma-t::after { content:''; position:absolute; left:50%; top:-1px; bottom:-1px; width:1px; background:var(--dim); opacity:.65; }",
  ".rv-ma-f { position:absolute; top:0; bottom:0; border-radius:var(--radius-pill); }",
  ".rv-ma-f.below { right:50%; background:var(--down-solid); }",
  ".rv-ma-f.above { left:50%; background:var(--up-solid); }",
  ".rv-ma-v { font-size:0.6875rem; font-family:var(--font-num); font-variant-numeric:tabular-nums;",
  "  color:var(--dim); min-width:46px; text-align:right; }",
  ".rv-spark-line { fill:none; stroke:var(--accent); stroke-width:1.2; vector-effect:non-scaling-stroke; }",
  ".rv-spark-fill { fill:var(--accent); opacity:.10; }",
  ".rv-bar-row { display:grid; grid-template-columns:minmax(52px,auto) minmax(0,1fr) 66px; gap:8px; align-items:center; }",
  ".rv-bar-lab { font-size:0.75rem; color:var(--dim); white-space:nowrap; }",
  ".rv-bar-track { position:relative; display:flex; height:16px; border-radius:var(--radius-sm); overflow:hidden; background:var(--card2); }",
  ".rv-bar-seg { height:100%; min-width:2px; transition:width .2s; }",
  ".rv-bar-seg.up { background:var(--up-solid); }",
  ".rv-bar-seg.down { background:var(--down-solid); }",
  ".rv-bar-seg.flat { background:var(--dim); opacity:.55; }",
  ".rv-bar-val { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:0.8125rem;",
  "  text-align:right; white-space:nowrap; color:var(--fg); }",
  ".rv-bar-val small { color:var(--dim); font-size:0.6875rem; }",
  ".rv-duo { display:flex; flex-direction:column; gap:6px; }",
  ".rv-duo-hd { display:flex; justify-content:space-between; align-items:baseline; font-size:0.6875rem; color:var(--dim); }",
  ".rv-pct-line { display:flex; align-items:center; gap:8px; }",
  ".rv-pct-line .who { font-size:0.75rem; color:var(--dim); width:38px; flex:0 0 auto; }",
  ".rv-pct-line .bar { flex:1; height:14px; border-radius:var(--radius-sm); background:var(--card2); position:relative; overflow:hidden; }",
  ".rv-pct-line .bar i { position:absolute; left:0; top:0; bottom:0; background:var(--up-solid); display:block; }",
  ".rv-pct-line .bar em { position:absolute; right:0; top:0; bottom:0; background:var(--down-solid); display:block; }",
  ".rv-pct-line .num { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:0.8125rem; width:112px; text-align:right; white-space:nowrap; }",
  ".rv-pct-line .num i { font-style:normal; color:var(--dim); font-size:0.6875rem; margin-left:5px; }",
  ".rv-temp { display:flex; flex-direction:column; gap:4px; }",
  ".rv-temp-track { position:relative; height:14px; border-radius:var(--radius-pill);",
  "  background:linear-gradient(90deg,var(--temp-cool),var(--temp-mid),var(--temp-warm),var(--temp-hot),var(--temp-very-hot)); }",
  ".rv-temp-mark { position:absolute; top:-4px; width:3px; height:22px; background:var(--fg-strong);",
  "  border-radius:var(--radius-pill); box-shadow:0 0 4px rgba(0,0,0,.5); }",
  ".rv-temp-prev { position:absolute; top:2px; width:2px; height:10px; background:var(--card); opacity:.85; }",
  ".rv-temp-scale { display:flex; justify-content:space-between; color:var(--dim); font-size:0.625rem; }",
  ".rv-dims { display:flex; height:10px; border-radius:var(--radius-sm); overflow:hidden; background:var(--card2); gap:1px; }",
  ".rv-dims i { display:block; height:100%; }",
  ".rv-dims-legend { display:flex; flex-wrap:wrap; gap:4px 12px; margin-top:2px; }",
  ".rv-dims-legend span { font-size:0.6875rem; color:var(--dim); white-space:nowrap; }",
  ".rv-dims-legend i { width:9px; height:9px; border-radius:2px; display:inline-block; margin-right:4px; vertical-align:-1px; }",
  ".rv-ref { color:var(--dim); opacity:.75; margin-left:4px; }",
  ".rv-dims-legend b { font-family:var(--font-num); color:var(--fg); }",
  ".rv-diverge { display:flex; flex-direction:column; gap:3px; }",
  ".rv-dv-row { display:grid; grid-template-columns:96px minmax(0,1fr) 58px; gap:8px; align-items:center; }",
  ".rv-dv-n { font-size:0.75rem; color:var(--fg); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }",
  ".rv-dv-t { position:relative; height:14px; background:var(--card2); border-radius:var(--radius-sm); overflow:hidden; }",
  ".rv-dv-t::before { content:''; position:absolute; left:50%; top:0; bottom:0; width:1px; background:var(--dim); opacity:.5; }",
  ".rv-dv-t i { position:absolute; top:2px; bottom:2px; border-radius:2px; }",
  ".rv-dv-t i.p { left:50%; background:var(--up-solid); }",
  ".rv-dv-t i.n { right:50%; background:var(--down-solid); }",
  ".rv-dv-v { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:0.75rem; text-align:right; }",
  ".rv-dv-chip { display:inline-block; padding:0 5px; border-radius:var(--radius-sm); font-size:0.625rem;",
  "  background:var(--line-soft); color:var(--dim); margin-left:4px; vertical-align:1px; }",
  ".rv-dv-chip.m { background:var(--up-bg); color:var(--up-ink); }",
  ".rv-dv-chip.k { background:var(--down-bg); color:var(--down-ink); }",
  ".rv-lad-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:var(--d-gap); }",
  ".rv-lad { background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-radius:var(--radius-md); padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline); min-width:0; }",
  ".rv-lad-hd { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:2px; }",
  ".rv-lad-hd b { font-size:0.875rem; color:var(--fg-strong); }",
  ".rv-lad-cap { font-size:0.6875rem; color:var(--dim); }",
  ".rv-steps { display:flex; align-items:flex-end; gap:3px; height:64px; margin-top:4px; }",
  ".rv-step { flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; gap:2px; min-width:0; }",
  ".rv-step .col { width:100%; border-radius:2px 2px 0 0; background:var(--dim); min-height:2px; }",
  ".rv-step .col.lead { background:var(--up-solid); }",
  ".rv-step .col.hollow { background:transparent; border:1px dashed var(--danger); min-height:14px; }",
  ".rv-step .n { font-family:var(--font-num); font-size:0.625rem; color:var(--dim); }",
  ".rv-step .k { font-family:var(--font-mono); font-size:0.5625rem; color:var(--dim); }",
  ".rv-gap-note { margin-top:4px; font-size:0.6875rem; color:var(--danger-ink); }",
  ".rv-trans { display:flex; flex-direction:column; gap:5px; }",
  ".rv-tr-row { display:grid; grid-template-columns:104px minmax(0,1fr) 76px; gap:8px; align-items:center; }",
  ".rv-tr-n { font-size:0.75rem; color:var(--fg); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }",
  ".rv-tr-t { position:relative; height:16px; background:var(--card2); border-radius:var(--radius-sm); overflow:hidden; }",
  ".rv-tr-t i { position:absolute; left:0; top:0; bottom:0; background:var(--accent-solid); opacity:.9; }",
  ".rv-tr-t i.weak { background:var(--warn-solid); }",
  ".rv-tr-t i.bad { background:var(--danger-solid); }",
  ".rv-tr-t u { position:absolute; top:0; bottom:0; width:1px; background:var(--fg-strong); opacity:.5; text-decoration:none; }",
  ".rv-tr-v { font-family:var(--font-num); font-variant-numeric:tabular-nums; font-size:0.75rem; text-align:right; color:var(--fg); }",
  ".rv-stage-strip { display:flex; gap:2px; align-items:flex-end; }",
  ".rv-stage-cell { flex:1; display:flex; flex-direction:column; gap:2px; min-width:0; }",
  ".rv-stage-cell .zc { background:var(--up-solid); border-radius:2px 2px 0 0; min-height:2px; }",
  ".rv-stage-cell .dc { background:var(--down-solid); border-radius:0 0 2px 2px; min-height:2px; opacity:.75; }",
  ".rv-stage-cell .dot { height:6px; border-radius:2px; background:var(--dim); }",
  ".rv-stage-cell .dt { font-family:var(--font-mono); font-size:0.5rem; color:var(--dim); text-align:center; white-space:nowrap; overflow:hidden; }",
  ".rv-stage-cell.today .dot { outline:2px solid var(--fg-strong); outline-offset:1px; }",
  ".rv-stage-cell .lb { font-size:0.5rem; text-align:center; font-weight:700; line-height:1.1; }",
  ".rv-s-fer .dot { background:var(--up-solid); }",
  ".rv-s-hot .dot { background:var(--warn-solid); }",
  ".rv-s-mid .dot { background:var(--accent-solid); }",
  ".rv-s-cool .dot { background:var(--down-solid); }",
  ".rv-s-fer .lb, .rv-s-hot .lb { color:var(--up-ink); }",
  ".rv-s-mid .lb { color:var(--accent-ink); }",
  ".rv-s-cool .lb { color:var(--down-ink); }",
  ".rv-unseal { display:flex; flex-direction:column; gap:3px; }",
  ".rv-unseal li { list-style:none; display:flex; align-items:baseline; gap:6px; font-size:0.75rem; color:var(--fg); }",
  ".rv-unseal li .ic { flex:0 0 auto; font-weight:700; }",
  ".rv-unseal li.no .ic { color:var(--down-ink); }",
  ".rv-unseal li.yes .ic { color:var(--danger-ink); }",
  ".rv-unseal li .dt { color:var(--dim); margin-left:auto; font-family:var(--font-num); font-size:0.6875rem; }",
  ".rv-news { display:flex; flex-direction:column; gap:6px; }",
  ".rv-news-item { display:grid; grid-template-columns:4px minmax(0,1fr); gap:10px;",
  "  background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-radius:var(--radius-md); padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline); }",
  ".rv-news-item > .sp { border-radius:var(--radius-pill); background:var(--dim); }",
  ".rv-news-item.pos > .sp { background:var(--up-solid); }",
  ".rv-news-item.neg > .sp { background:var(--down-solid); }",
  ".rv-news-item.warn > .sp { background:var(--warn-solid); }",
  ".rv-news-item.neutral > .sp { background:var(--line); }",
  ".rv-news-item.mixed > .sp { background:linear-gradient(180deg,var(--up-solid) 0 50%,var(--down-solid) 50% 100%); }",
  ".rv-news-b { min-width:0; }",
  ".rv-news-hd { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }",
  ".rv-news-t { font-size:0.8125rem; font-weight:700; color:var(--fg-strong); }",
  ".rv-news-v { margin-left:auto; font-size:0.6875rem; font-weight:700; }",
  ".rv-news-m { font-size:0.75rem; color:var(--dim); line-height:1.6; margin-top:2px; }",
  ".rv-news-a { font-size:0.75rem; color:var(--fg); line-height:1.6; margin-top:4px; padding-top:4px; border-top:1px solid var(--line-soft); }",
  ".rv-pos-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(268px,1fr)); gap:var(--d-gap); }",
  ".rv-pos { background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-radius:var(--radius-md); padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline);",
  "  border-left:4px solid var(--dim); display:flex; flex-direction:column; gap:3px; min-width:0; }",
  ".rv-pos.danger { border-left-color:var(--danger-solid); }",
  ".rv-pos.warn { border-left-color:var(--warn-solid); }",
  ".rv-pos.neutral { border-left-color:var(--line); }",
  ".rv-pos.ok { border-left-color:var(--down-solid); }",
  ".rv-pos-hd { display:flex; align-items:baseline; gap:6px; }",
  ".rv-pos-hd b { font-size:0.9375rem; color:var(--fg-strong); }",
  ".rv-pos-hd span { font-size:0.6875rem; color:var(--dim); font-family:var(--font-mono); }",
  ".rv-pos-st { font-size:0.75rem; font-weight:700; margin-left:auto; }",
  ".rv-pos-nums { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:2px 8px; }",
  ".rv-pos-nums div { display:flex; justify-content:space-between; gap:4px; font-size:0.6875rem; color:var(--dim); }",
  ".rv-pos-nums b { font-family:var(--font-num); font-variant-numeric:tabular-nums; color:var(--fg); font-weight:700; }",
  ".rv-anchor { position:relative; height:20px; margin-top:4px; }",
  ".rv-anchor-track { position:absolute; left:0; right:0; top:8px; height:5px; border-radius:var(--radius-pill); background:var(--card2); }",
  ".rv-anchor-fill { position:absolute; left:0; top:8px; height:5px; border-radius:var(--radius-pill); background:var(--down-solid); }",
  ".rv-anchor-fill.over { background:var(--up-solid); }",
  ".rv-anchor-anc { position:absolute; top:2px; height:17px; width:2px; background:var(--fg-strong); }",
  ".rv-anchor-anc::after { content:attr(data-l); position:absolute; left:50%; transform:translateX(-50%); top:-11px;",
  "  font-size:0.5625rem; color:var(--dim); white-space:nowrap; font-family:var(--font-mono); }",
  ".rv-anchor-now { position:absolute; top:4px; width:11px; height:11px; border-radius:50%;",
  "  background:var(--card); border:2px solid var(--danger-solid); transform:translateX(-5.5px); }",
  ".rv-anchor-note { font-size:0.6875rem; color:var(--dim); font-family:var(--font-num); }",
  ".rv-pos-todo { font-size:0.6875rem; color:var(--warn-ink); }",
  ".rv-acct { display:flex; flex-wrap:wrap; gap:6px 18px; align-items:center; padding:var(--d-pad-sm);",
  "  background:var(--card2); border-radius:var(--radius-md); font-size:0.75rem; color:var(--dim); }",
  ".rv-acct b { font-family:var(--font-num); font-variant-numeric:tabular-nums; color:var(--fg-strong); font-size:0.875rem; }",
  ".rv-gates { display:flex; flex-wrap:wrap; gap:var(--d-gap); }",
  ".rv-gate { flex:1; min-width:180px; background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-left:5px solid var(--dim); border-radius:var(--radius-md); padding:var(--d-pad-sm);",
  "  box-shadow:var(--shadow-card),var(--card-hairline); }",
  ".rv-gate.mid { border-left-color:var(--warn-solid); }",
  ".rv-gate.hi { border-left-color:var(--danger-solid); }",
  ".rv-gate.lo { border-left-color:var(--down-solid); }",
  ".rv-gate b { font-size:0.8125rem; color:var(--fg-strong); }",
  ".rv-gate .lamp { font-size:0.6875rem; font-weight:700; }",
  ".rv-gate p { font-size:0.75rem; color:var(--dim); margin-top:2px; line-height:1.5; }",
  ".rv-sc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:var(--d-gap); }",
  ".rv-sc { background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-radius:var(--radius-md); box-shadow:var(--shadow-card),var(--card-hairline); overflow:hidden; min-width:0; }",
  ".rv-sc-hd { display:flex; align-items:center; gap:6px; padding:4px var(--d-pad-sm); background:var(--card2);",
  "  border-bottom:1px solid var(--line-soft); }",
  ".rv-sc-hd .id { font-family:var(--font-mono); font-size:0.6875rem; color:var(--dim); }",
  ".rv-sc-hd b { font-size:0.8125rem; color:var(--fg-strong); }",
  ".rv-sc-body { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); }",
  ".rv-sc-col { padding:6px var(--d-pad-sm); min-width:0; }",
  ".rv-sc-col + .rv-sc-col { border-left:1px solid var(--line-soft); }",
  ".rv-sc-col .h { font-size:0.625rem; color:var(--dim); letter-spacing:.05em; }",
  ".rv-sc-col .t { font-size:0.75rem; line-height:1.55; color:var(--fg); margin-top:2px; }",
  ".rv-sc-col.act .t { color:var(--accent-ink); }",
  ".rv-sc-col.act.bad .t { color:var(--danger-ink); }",
  ".rv-sc-col.act.ok .t { color:var(--up-ink); }",
  ".rv-verdict { display:flex; flex-direction:column; gap:5px; }",
  ".rv-vd { background:var(--card); border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-radius:var(--radius-md); padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline); }",
  ".rv-vd b { display:block; font-size:0.8125rem; color:var(--accent-ink); }",
  ".rv-vd p { font-size:0.75rem; color:var(--fg); line-height:1.6; margin-top:2px; }",
  ".rv-quote { border-left:3px solid var(--warn-solid); padding:6px var(--d-pad-sm); background:var(--card2);",
  "  border-radius:0 var(--radius-md) var(--radius-md) 0; font-size:0.8125rem; line-height:1.7; color:var(--fg); }",
  ".rv-quote .who { font-size:0.6875rem; color:var(--dim); display:block; margin-bottom:2px; }",
  // 旧页暖橙三值（--judge-*）：旧页原本写死 hex + 按主题名覆写，本卡改为指向 warn 语义 token，
  // 浅色主题的深色值由 themes.css 自带，不再按主题名分支。
  // 注意：`#panel-review` 作用域内的规则被 test_scoped_css_rule_headers_match_legacy 锁为
  // 「与旧页逐字一致」，不能改；本组变量不在该作用域内，可安全换成 token。
  ":root{ --judge-bar:var(--warn-solid); --judge-chip:var(--warn-solid); --judge-chip-ink:var(--warn-on); }",
  ".rv-judge, .rv-hero { transition:background .15s, color .15s, border-color .15s; }",
  ".rv-judge { margin:0 0 var(--d-gap); background:var(--card);",
  "  border:var(--border-w) var(--border-style) var(--border-color);",
  "  border-left:4px solid var(--judge-bar);",
  "  border-radius:0 var(--radius-md) var(--radius-md) 0;",
  "  padding:var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline); }",
  ".rv-judge-cap { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; margin-bottom:5px; }",
  ".rv-judge-tag { font-size:0.75rem; font-weight:700; color:var(--judge-chip-ink); background:var(--judge-chip);",
  "  padding:1px 8px; border-radius:var(--radius-pill); white-space:nowrap; }",
  ".rv-judge-cap .who { font-size:0.6875rem; color:var(--dim); }",
  ".rv-judge .t { font-size:0.875rem; line-height:1.8; color:var(--fg-strong); }",
  ".rv-hero { display:flex; flex-direction:column; gap:10px; margin-bottom:var(--d-gap); background:var(--card);",
  "  border:var(--border-w) var(--border-style) var(--border-color); border-left:4px solid var(--judge-bar);",
  "  border-radius:0 var(--radius-lg) var(--radius-lg) 0; padding:var(--d-pad);",
  "  box-shadow:var(--shadow-card),var(--card-hairline); }",
  ".rv-hero-date { display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:0.75rem;",
  "  color:var(--dim); margin-bottom:2px; }",
  ".rv-hero-date b { font-size:1rem; color:var(--fg-strong); font-family:var(--font-num); }",
  ".rv-hero-title { font-size:0.875rem; font-weight:700; color:var(--fg-strong); line-height:var(--d-rowh); }",
  ".rv-hero-next { margin:0; padding:8px var(--d-pad-sm); border-radius:var(--radius-md);",
  "  background:rgba(224,164,88,.10); border-left:3px solid var(--judge-bar);",
  "  color:var(--fg-strong); font-size:0.9375rem; font-weight:700; line-height:1.75; }",
  ".rv-hero-next .rv-tag { background:transparent; border:1px solid var(--judge-bar); color:var(--judge-bar);",
  "  font-weight:700; margin-right:6px; }",
  ".rv-hero-one { font-size:0.875rem; line-height:1.75; color:var(--fg-strong); }",
  ".rv-hero-one .rv-tag { margin-right:6px; background:rgba(224,164,88,.10); color:var(--dim);",
  "  border:1px solid var(--line-soft); }",
  ".rv-three { border-top:1px solid var(--line-soft); padding-top:8px; }",
  ".rv-three .rv-lad-cap { font-size:0.75rem; font-weight:700; color:var(--dim); }",
  ".rv-three ol { margin:4px 0 0; padding:0 0 0 18px; display:flex; flex-direction:column; gap:4px; }",
  ".rv-three li { font-size:0.8125rem; line-height:1.65; color:var(--fg-strong); }",
  ".rv-three li::marker { color:var(--dim); font-weight:700; }",
  ".rv-note { font-size:0.75rem; color:var(--dim); line-height:1.6; }",
  ".rv-note b { color:var(--fg); }",
  ".rv-gaps { display:grid; gap:5px; }",
  ".rv-gap { display:flex; gap:8px; align-items:baseline; font-size:0.75rem; padding:4px var(--d-pad-sm);",
  "  background:var(--warn-bg); border-radius:var(--radius-sm); color:var(--warn-ink); }",
  ".rv-gap b { flex:0 0 auto; }",
  ".rv-gap span { color:var(--fg); }",
  ".rv-miss { display:inline-block; padding:0 6px; border-radius:var(--radius-sm); border:1px dashed var(--danger);",
  "  color:var(--danger-ink); font-size:0.6875rem; font-family:var(--font-mono); white-space:nowrap; }",
  ".rv-legend { display:flex; flex-wrap:wrap; gap:4px 12px; margin-top:4px; }",
  ".rv-legend span { display:inline-flex; align-items:center; gap:4px; font-size:0.6875rem; color:var(--dim); }",
  ".rv-legend i { width:9px; height:9px; border-radius:2px; display:inline-block; }",
  ".rv-prio { display:flex; flex-direction:column; gap:5px; counter-reset:pr; }",
  ".rv-prio li { list-style:none; display:grid; grid-template-columns:22px minmax(0,1fr); gap:8px; align-items:start;",
  "  background:var(--card); border:var(--border-w) var(--border-style) var(--border-color); border-radius:var(--radius-md);",
  "  padding:5px var(--d-pad-sm); box-shadow:var(--shadow-card),var(--card-hairline); }",
  ".rv-prio .no { font-family:var(--font-mono); font-size:0.8125rem; font-weight:700; color:var(--accent); }",
  ".rv-prio .bd b { font-size:0.8125rem; color:var(--fg-strong); display:block; }",
  ".rv-prio .bd p { font-size:0.75rem; color:var(--dim); line-height:1.6; margin-top:1px; }",
  ".rv-prio li.danger { border-left:3px solid var(--danger-solid); }",
  ".rv-prio li.warn { border-left:3px solid var(--warn-solid); }",
  ".rv-prio li.pos { border-left:3px solid var(--up-solid); }",
  ".rv-mode-btn { padding:2px 10px; font-size:0.72rem; border-radius:var(--radius-sm); cursor:pointer;",
  "  background:var(--card2); color:var(--fg); border:var(--border-w) var(--border-style) var(--border-color);",
  "  font-family:inherit; }",
  ".rv-mode-btn.on { background:var(--accent-solid); color:var(--accent-on); border-color:var(--accent-solid); }",
  ".rv-mode-btn + .rv-mode-btn { margin-left:4px; }",
  "/* 通用小类垫片（旧页全局有、v2 壳没有）：图表模板与 v6 正文都用得上 */",
  "#panel-review .k-name{cursor:pointer}",
  "#panel-review .k-name:hover{color:var(--accent);text-decoration:underline;text-underline-offset:2px}",
  "#panel-review .k-nolink{cursor:default;opacity:.75}",
  // ---- 本卡自有骨架（旧页没有的：视图切换条 / 卡片容器） ----
  '.rv-card-root{display:flex;flex-direction:column;gap:var(--d-gap);min-width:0}',
  '.rv-card-hd{display:flex;align-items:center;gap:8px;flex-wrap:wrap}',
  '.rv-vtabs{display:flex;gap:8px;flex-wrap:wrap;align-items:center}',
  '.rv-vpill{padding:3px 13px;border-radius:var(--radius-pill);border:var(--border-w) var(--border-style) var(--line);background:transparent;color:var(--dim);font-size:0.78rem;font-family:inherit;cursor:pointer}',
  '.rv-vpill:hover{border-color:var(--accent);color:var(--fg)}',
  '.rv-vpill.active{border-color:var(--accent);background:var(--accent-bg);color:var(--accent)}',
  '.rv-card-hd .rv-mode-btn{margin-left:0}',
  '.rv-sp-gap{margin-left:auto}',
  '.rv-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer}',
  '.rv-btn:hover{background:var(--card2)}',
  '.rv-err{padding:6px 9px;border-radius:var(--radius-sm);background:var(--danger-bg);color:var(--danger-ink,var(--danger));font-size:0.75rem;line-height:1.5;word-break:break-word}',
  '.rv-note-line{padding:6px 9px;border-radius:var(--radius-sm);background:var(--warn-bg);color:var(--warn-ink,var(--warn));font-size:0.75rem;line-height:1.5}',
  '.rv-card-body{min-width:0}',
  '.rv-src-foot{font-size:0.66rem;color:var(--dim);line-height:1.6}',
  // ---- 第四视图「六格」（卡片式，规格 v2：两列卡片 + 语义色左边条，全用主题 token） ----
  '.rv-six{display:flex;flex-direction:column;gap:10px}',
  '.rv-six-head{font-size:0.875rem;line-height:1.7;color:var(--fg-strong);padding:8px var(--d-pad-sm);background:var(--card);border-left:3px solid var(--accent-solid);border-radius:0 var(--radius-md) var(--radius-md) 0}',
  '.rv-six-head b{color:var(--fg-strong)}',
  '.rv-six-temp{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}',
  '.rv-six-temp div{background:var(--card2);border:var(--border-w) var(--border-style) var(--line-soft);border-radius:var(--radius-sm);padding:4px 7px;min-width:0}',
  '.rv-six-temp span{display:block;font-size:0.625rem;color:var(--dim);letter-spacing:.04em}',
  '.rv-six-temp b{display:block;font-size:0.75rem;color:var(--fg-strong);font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
  '.rv-six-temp b.rv-six-up{color:var(--up-ink)}',
  '.rv-six-temp b.rv-six-down{color:var(--down-ink)}',
  '.rv-six-temp b.rv-six-warn{color:var(--warn-ink)}',
  '@media (max-width:900px){.rv-six-temp{grid-template-columns:repeat(3,minmax(0,1fr))}}',
  '.rv-six-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}',
  '.rv-six-card{position:relative;background:var(--card);border:var(--border-w) var(--border-style) var(--border-color);border-left:3px solid var(--accent-solid);border-radius:var(--radius-md);padding:10px var(--d-pad-sm) 9px;box-shadow:var(--shadow-card),var(--card-hairline);display:flex;flex-direction:column;gap:6px;min-width:0}',
  '.rv-six-card.c1{border-left-color:var(--up-solid)}',
  '.rv-six-card.c2{border-left-color:var(--accent-solid)}',
  '.rv-six-card.c3{border-left-color:var(--up-solid)}',
  '.rv-six-card.c4{border-left-color:var(--down-solid)}',
  '.rv-six-card.c5{border-left-color:var(--warn-solid)}',
  '.rv-six-card.c6{border-left-color:var(--warn-solid)}',
  '.rv-six-card.focus{outline:2px solid var(--accent);outline-offset:1px}',
  '.rv-six-hd{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}',
  '.rv-six-no{font-family:var(--font-mono);font-size:0.6875rem;color:var(--dim);letter-spacing:.06em}',
  '.rv-six-hd b{font-size:0.8125rem;color:var(--fg-strong)}',
  '.rv-six-tag{margin-left:auto;font-size:0.6875rem;color:var(--dim)}',
  '.rv-six-chips{display:flex;flex-wrap:wrap;gap:4px 6px}',
  '.rv-six-chip{font-size:0.72rem;padding:0 7px;border-radius:var(--radius-pill);background:var(--card2);color:var(--fg);border:var(--border-w) var(--border-style) var(--line-soft);white-space:nowrap}',
  '.rv-six-chip em{font-style:normal;color:var(--dim);margin-left:1px}',
  /* 2026-11-05（标注 78）：可点的板块 chip / 明细行——hover 提亮 + 手型 */
  '.rv-six-chip.rv-link{cursor:pointer}',
  '.rv-six-chip.rv-link:hover{background:var(--accent-bg);border-color:var(--accent)}',
  '.rv-six-tbl td.nm[data-code]{cursor:pointer}',
  '.rv-six-tbl td.nm[data-code]:hover{color:var(--accent)}',
  '.rv-six-basis{font-size:0.72rem;line-height:1.6;color:var(--dim);font-variant-numeric:tabular-nums}',
  '.rv-six-basis b{color:var(--fg)}',
  '.rv-six-act{font-size:0.72rem;line-height:1.6;color:var(--fg);border-top:1px solid var(--line-soft);padding-top:5px}',
  '.rv-six-act .lb{font-size:0.625rem;font-weight:700;color:var(--warn-ink);margin-right:4px}',
  '.rv-six-act.pending{color:var(--dim);font-style:italic}',
  '.rv-six-d{margin-top:2px}',
  '.rv-six-d summary{cursor:pointer;font-size:0.66rem;color:var(--accent);list-style:none}',
  '.rv-six-d summary::-webkit-details-marker{display:none}',
  '.rv-six-d summary::before{content:"\\25B8 "}',
  '.rv-six-d[open] summary::before{content:"\\25BE "}',
  '.rv-six-tbl{width:100%;border-collapse:collapse;font-size:0.7rem;margin-top:3px}',
  '.rv-six-tbl th{font-weight:500;color:var(--dim);font-size:0.625rem;text-align:right;padding:3px 5px;border-bottom:1px solid var(--line-soft);white-space:nowrap}',
  '.rv-six-tbl th:first-child,.rv-six-tbl td:first-child{text-align:left}',
  '.rv-six-tbl td{padding:3px 5px;border-bottom:1px dashed var(--line-soft);text-align:right;font-variant-numeric:tabular-nums}',
  '.rv-six-tbl td.nm{color:var(--fg-strong);font-weight:600}',
  '.rv-six-up{color:var(--up-ink)}',
  '.rv-six-down{color:var(--down-ink)}',
  '.rv-six-plain{color:var(--fg)}',
  '@media (max-width:720px){.rv-six-grid{grid-template-columns:1fr}}',
].join('\n');

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

// ---------------------------------------------------------------- 工具
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const errText = (e) => (e && e.message ? e.message : String(e));

// 复盘 MD 文件 mtime（ns）→ 'YYYY-MM-DD HH:MM'（旧 fmtMtime 逐字搬入：时区用浏览器本地，
// 缺值/非法 → null，调用方显示「不可用」，不编造）
function fmtMtime(ns) {
  if (ns === null || ns === undefined || ns === '') return null;
  const t = Number(ns);
  if (!isFinite(t) || t <= 0) return null;
  const d = new Date(t / 1e6);
  if (isNaN(d.getTime())) return null;
  const p = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
    + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

function lsGetMode() {
  try { return localStorage.getItem(LS_MODE) === 'full' ? 'full' : 'brief'; } catch (e) { return 'brief'; }
}

function lsSetMode(m) {
  try { localStorage.setItem(LS_MODE, m); } catch (e) { /* 存储不可用不阻断 */ }
}

function curTheme() {
  try { return document.documentElement.getAttribute('data-theme') || 'nightsail'; } catch (e) { return 'nightsail'; }
}

// 旧 7897：blockquote 语义与 md2html 同口径（⚠=warning，💡/📊=highlight）。
// 成对替换——只有「开标签被转换」的引用块才换对应的闭合标签，普通 blockquote 原样保留
// （旧端曾因全局 replace(/<\/blockquote>/) 产生孤立 </div>，把后续正文挤出容器）。
function convertBlockquotes(html) {
  return String(html || '').replace(/<blockquote>\s*<p>(⚠|💡|📊)([\s\S]*?)<\/blockquote>/g,
    (m, mark, rest) => {
      const cls = mark === '⚠' ? 'warning' : 'highlight';
      return '<div class="' + cls + '"><p>' + mark + rest + '</div>';
    });
}

function chartsHtml(charts) {
  const ch = charts || {};
  let out = '';
  if (ch.swimlane) out += '<div class="rv-chart">' + ch.swimlane + '</div>';
  if (ch.track) out += '<div class="rv-chart">' + ch.track + '</div>';
  return out;
}

// 图表组件库（旧仓 review_chart.js，同源静态资源）：懒加载一次，失败 → 返回 null（不抛）。
// 超时兜底（R3 实测踩到）：脚本既不触发 load 也不触发 error 时（网络挂起 / 被拦截），
// 不能无限等——3s 后当作不可用，让降级链继续走纸面，卡片不留空白。
const RVC_TIMEOUT_MS = 3000;
let rvcPromise = null;
function loadRvc() {
  if (typeof window !== 'undefined' && window.RVC) return Promise.resolve(window.RVC);
  if (rvcPromise) return rvcPromise;
  rvcPromise = new Promise((resolve) => {
    if (typeof document === 'undefined') { resolve(null); return; }
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      resolve((typeof window !== 'undefined' && window.RVC) ? window.RVC : null);
    };
    const timer = setTimeout(done, RVC_TIMEOUT_MS);
    const finish = () => { clearTimeout(timer); done(); };
    const exist = document.getElementById('review-chart-lib');
    if (exist) {
      exist.addEventListener('load', finish);
      exist.addEventListener('error', finish);
      return;
    }
    const s = document.createElement('script');
    s.id = 'review-chart-lib';
    s.src = RVC_SRC;
    s.async = true;
    s.addEventListener('load', finish);
    s.addEventListener('error', finish);
    document.head.appendChild(s);
  });
  return rvcPromise;
}

// ---------------------------------------------------------------- 卡片
export default {
  cards: [{
    id: 'review.main',
    title: '每日复盘',
    slot: 'main',
    order: 60,

    async mount(root, ctx) {
      root.textContent = '';
      const api = ctx.api;
      // 卡间跳转（板块页顶部 → 复盘六格明细）：args.view 决定初始视图
      const argView = (ctx && ctx.card && ctx.card.args && ctx.card.args.view) || '';
      const argCell = (ctx && ctx.card && ctx.card.args && ctx.card.args.cell) || '';
      // 可选日期覆盖（args.date 或 URL ?sixdate=）：仅影响六格取数，便于回看/验收；默认跟复盘日
      let urlSixDate = '';
      try { urlSixDate = new URLSearchParams(location.search).get('sixdate') || ''; } catch (e) { /* ignore */ }
      const argDate = (ctx && ctx.card && ctx.card.args && ctx.card.args.date) || urlSixDate || '';
      const initView = ['paper', 'doc', 'six'].indexOf(argView) >= 0 ? argView : 'paper';
      const S = {
        alive: true,
        loading: false,
        view: initView,           // chart | paper | doc | six
        sixFocus: argCell,       // 跳转定位（板块页点哪格，就在六格视图里描边哪张卡）
        sixDate: argDate,        // 六格取数日期覆盖（空 = 跟复盘卡当前归属日）
        mode: lsGetMode(),       // brief | full（仅图表视图）
        data: null,
        html: Object.create(null),
        rvc: null,
        el: {},
        themeObs: null,
      };
      this._rv = S;

      const div = (cls, text) => {
        const d = document.createElement('div');
        if (cls) d.className = cls;
        if (text != null) d.textContent = text;
        return d;
      };

      const wrap = div('rv-card-root');
      // 纸面 CSS 与旧页样式都挂在 #panel-review 下：卡内再套一层同名容器承接作用域，
      // 旧页样式原样生效，且**不改 shell 的 host 元素**（宿主属性归 shell）。
      wrap.id = 'panel-review';
      const head = div('rv-card-hd');
      const vtabs = div('rv-vtabs');
      const modeBar = div('rv-vtabs');
      const gap = div('rv-sp-gap');
      const btnReload = document.createElement('button');
      btnReload.type = 'button';
      btnReload.className = 'rv-btn';
      btnReload.textContent = '刷新';
      btnReload.addEventListener('click', () => { load(true); });
      head.append(vtabs, modeBar, gap, btnReload);

      const status = div('rv-err');
      status.hidden = true;
      const note = div('rv-note-line');
      note.hidden = true;
      const body = div('rv-card-body');
      const foot = div('rv-src-foot', '数据源：/api/review.md?paper=1（纸面）· '
        + '/api/review.md?theme=（文档 v6）· /api/review_six.json（六格）。'
        + '本卡只读，零写盘；个股名只带 data-kname 占位，点击由旧页桥接接管。');

      wrap.append(head, status, note, body, foot);
      root.append(wrap);
      S.el = { head, vtabs, modeBar, status, note, body };

      const setHtml = (el, html, key) => {
        if (!el) return;
        if (S.html[key] === html) return;
        S.html[key] = html;
        el.innerHTML = html;
        // 表格里的个股名/代码 → data-open-kline 占位（旧页 wireReviewTableKNames 同口径）。
        // 放这里而不是每个 render 末尾：三条视图（图表/纸面/文档）的表格都经此写入，一处覆盖。
        try { wireTableKNames(el); } catch (e) { /* 后处理失败不阻断正文 */ }
      };
      const setStatus = (text) => {
        const el = S.el.status;
        if (!el) return;
        if (!text) { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        el.textContent = text;
      };
      const setNote = (text) => {
        const el = S.el.note;
        if (!el) return;
        if (!text) { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        el.textContent = text;
      };

      const renderHead = () => {
        setHtml(S.el.vtabs, VIEWS.map(([k, label]) =>
          '<button type="button" class="rv-vpill' + (S.view === k ? ' active' : '') + '" data-rvview="' + k
          + '" role="tab" aria-selected="' + (S.view === k ? 'true' : 'false') + '">' + label + '</button>').join(''),
        'vtabs');
        // 档位切换只对图表视图有意义（旧 rvModeBar 同口径）
        if (S.view !== 'chart') {
          setHtml(S.el.modeBar, '', 'modebar');
          S.el.modeBar.style.display = 'none';
          return;
        }
        S.el.modeBar.style.display = '';
        setHtml(S.el.modeBar, '<span class="src">视图</span>'
          + '<button type="button" class="rv-mode-btn' + (S.mode === 'brief' ? ' on' : '')
          + '" data-rvmode="brief">简报</button>'
          + '<button type="button" class="rv-mode-btn' + (S.mode === 'full' ? ' on' : '')
          + '" data-rvmode="full">完整</button>'
          + '<span class="src">简报=默认 6 块，其余收进「全窗明细」折叠；完整=全展开</span>', 'modebar');
      };

      // 顶行（旧 rv-topline 同构）：复盘文件 / 归属交易日 / 最后更新时间 / 渲染方式 / 缓存
      // 图表模板的归属交易日在 meta.trade_date（旧 7840 同口径），另两路用 d.date
      const topline = (d, kind) => {
        const upd = fmtMtime(d.mtime_ns);
        const date = d.date || ((d.meta || {}).trade_date) || '—';
        return '<div class="src rv-topline">复盘文件: ' + esc(d.file || '') + ' ｜ 归属交易日: '
          + esc(date) + ' ｜ 最后更新时间: ' + (upd ? esc(upd) : '不可用')
          + ' ｜ 渲染: ' + kind + ' ｜ 缓存: ' + esc(d.cache || '—') + '</div>';
      };

      // ---- 视图 1：图表模板 ----
      const renderChart = async () => {
        let d = null;
        try {
          const j = await api.get('/api/review_chart.json');
          if (j && j.ok === true && j.head && j.meta) d = j;
        } catch (e) { /* 装配失败 → 下走纸面 */ }
        if (!d) return { ok: false, why: '图表数据不可用' };
        const rvc = await loadRvc();
        if (!rvc) return { ok: false, why: '图表组件库未加载' };
        S.rvc = rvc;
        S.data = d;
        const key = [d.file || '', d.mtime_ns || '', (d.meta && d.meta.trade_date) || '',
          (d.meta && (d.meta.miss || []).length) || 0, S.mode].join('|');
        if (S.html['chart-key'] === key && body.querySelector('.rv-root')) return { ok: true };
        S.html['chart-key'] = key;
        const miss = (d.meta && d.meta.miss) || [];
        const missTip = miss.length
          ? '<span class="warn" title="' + esc(miss.join('；')) + '">⚠ ' + miss.length
            + ' 段数据源未对齐（悬停看明细）</span>'
          : '';
        setHtml(S.el.body, topline(d, '图表模板') + missTip + '<div id="rvChartMount"></div>', 'chart');
        const mount = body.querySelector('#rvChartMount');
        if (!mount) return { ok: false, why: '图表挂载点缺失' };
        try {
          rvc.render(d, mount, S.mode);
        } catch (e) {
          return { ok: false, why: '图表渲染失败：' + errText(e) };
        }
        setNote('');
        return { ok: true };
      };

      // ---- 视图 2：纸面（report-builder 暖白纸，配色固定不随主题） ----
      const injectPaperCss = (css) => {
        if (!css) return;
        let el = document.getElementById(PAPER_CSS_ID);
        if (!el) {
          el = document.createElement('style');
          el.id = PAPER_CSS_ID;
          document.head.appendChild(el);
        }
        if (el.textContent !== css) el.textContent = css;
      };

      const renderPaper = async () => {
        let p = null;
        try {
          const j = await api.get('/api/review.md?paper=1');
          if (j && j.paper === true && !j.degraded && j.html) p = j;
        } catch (e) { /* 纸面不可用 → 下走 v6 */ }
        if (!p) return { ok: false, why: '纸面版不可用' };
        S.data = p;
        injectPaperCss(p.css || '');
        const key = [p.file || '', p.mtime_ns || '', (p.html || '').length, (p.css || '').length, 'paper'].join('|');
        if (S.html['paper-key'] === key && body.querySelector('.rv-paper')) return { ok: true };
        S.html['paper-key'] = key;
        setHtml(S.el.body, topline(p, '纸面')
          + '<div class="rv-paper"><div class="sheet">' + (p.masthead || '')
          + '<main class="doc">' + (p.html || '') + '</main>' + (p.footer || '') + '</div></div>', 'paper');
        setNote('');
        return { ok: true };
      };

      // ---- 视图 3：文档 v6（深色正文，跟主题） ----
      const renderDoc = async () => {
        const theme = curTheme();
        let d = null;
        try {
          d = await api.get('/api/review.md?theme=' + encodeURIComponent(theme));
        } catch (e) {
          const st = e && e.status;
          const payload = (e && e.payload) || {};
          const detail = payload.detail || errText(e);
          setHtml(S.el.body, '<div class="card"><h2>复盘 MD 缺失</h2><p class="muted">'
            + esc(detail) + '</p><div class="src">复盘管线每晚产出 日复盘 {date}.md'
            + '（夏天-activity/每日复盘/）；面板按需发现最新交易日。'
            + (st ? '（HTTP ' + st + '）' : '') + '</div></div>', 'doc-miss');
          return { ok: true };
        }
        if (!d) return { ok: false, why: '文档数据为空' };
        S.data = d;
        const html = convertBlockquotes(d.html || '');
        const key = [d.file || '', d.mtime_ns || '', html.length, theme].join('|');
        if (S.html['doc-key'] === key && body.querySelector('.rv-md')) return { ok: true };
        S.html['doc-key'] = key;
        const degradedHtml = d.degraded
          ? '<div class="warning"><p>⚠ 复盘渲染降级：' + esc(d.error || '渲染失败')
            + '（显示上一份成功内容）</p></div>'
          : '';
        setHtml(S.el.body, topline(d, '文档 v6') + degradedHtml
          + '<div class="rv-md">' + html + chartsHtml(d.charts) + '</div>', 'doc');
        setNote('');
        return { ok: true };
      };

      // ---- 视图 4：六格（卡片式，规格 v2） ----
      // 数据只读 GET /api/review_six.json（与板块页顶部同一份 JSON）；本视图不重算任何口径。
      const sgn = (v, n) => (v == null ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(n));
      const fixed = (v, n) => (v == null ? '—' : Number(v).toFixed(n));
      const clsOf = (v) => (v == null ? 'rv-six-plain' : v > 0 ? 'rv-six-up' : v < 0 ? 'rv-six-down' : 'rv-six-plain');
      const CHIP_KEY = { persistent_inflow: 'd5', cold_divergence: 'd5', strong_resonance: 'limitup',
        resonance_outflow: 'd1', leverage_divergence: 'pct', limitup_concentration: 'n' };

      // 旧产物（11-02 生成于版式 v2 之前）无 chips/basis：用已有字段现拼展示文本，不自算口径。
      const fallbackChip = (r, key) => ({
        name: r.name,
        metric: key === 'limitup' ? '涨停 ' + r.limitup + ' 家'
          : key === 'd1' ? 'd1 ' + sgn(r.d1, 3)
            : key === 'pct' ? sgn(r.pct, 2) + '%'
              : key === 'n' ? r.n + ' 家' : 'd5 ' + sgn(r.d5, 3),
      });

      /* 2026-11-05（标注 78 主人：复盘页板块点不动）：chips 本身只有 name/metric，
         **code 在 cell.rows / cell.matched 里**（如 881279.TI）。这里按名称建索引，
         给 chip 补 data-code + data-open-amv ⇒ 左键走壳的板块详情、右键出板块菜单。 */
      const codeIndex = (rows) => {
        const m = {};
        (rows || []).forEach((r) => { if (r && r.name && r.code) m[String(r.name)] = r.code; });
        return m;
      };
      const linkAttrs = (code, name) => (code
        ? ' data-code="' + esc(code) + '" data-open-amv="' + esc(code)
          + '" data-open-name="' + esc(name) + '" data-name="' + esc(name) + '"'
        : '');

      const sixTbl = (rows) => {
        if (!rows || !rows.length) return '<div class="rv-miss">无明细</div>';
        return '<table class="rv-six-tbl"><thead><tr><th>板块</th><th>价%</th><th>d1 pp</th>'
          + '<th>d5 pp</th><th>状态</th><th>涨停</th><th>份额%</th></tr></thead><tbody>'
          + rows.map((r) => '<tr><td class="nm"' + linkAttrs(r.code, r.name) + '>' + esc(r.name) + '</td>'
            + '<td class="' + clsOf(r.pct) + '">' + sgn(r.pct, 2) + '</td>'
            + '<td class="' + clsOf(r.d1) + '">' + sgn(r.d1, 3) + '</td>'
            + '<td class="' + clsOf(r.d5) + '">' + sgn(r.d5, 3) + '</td>'
            + '<td>' + esc(r.state == null ? '—' : r.state) + '</td>'
            + '<td>' + (r.limitup == null ? '—' : r.limitup) + '</td>'
            + '<td>' + fixed(r.share == null ? null : r.share * 100, 2) + '</td></tr>').join('')
          + '</tbody></table>';
      };

      const sixHhiTbl = (rows, total) => {
        if (!rows || !rows.length) return '<div class="rv-miss">无明细</div>';
        return '<table class="rv-six-tbl"><thead><tr><th>L2 板块</th><th>涨停家数</th><th>n/N</th>'
          + '<th>(n/N)²</th></tr></thead><tbody>'
          + rows.map((r) => '<tr><td class="nm">' + esc(r.name) + '</td><td>' + r.n + '</td>'
            + '<td>' + fixed(r.n / (total || 1), 4) + '</td><td>' + fixed(r.hhi_part, 6) + '</td></tr>').join('')
          + '</tbody></table>';
      };

      const sixDetail = (cell) => {
        if (cell.key === 'limitup_concentration') {
          return '<details class="rv-six-d"><summary>各 L2 涨停家数与 HHI 分量</summary>'
            + sixHhiTbl(cell.rows || [], cell.total_limitup) + '</details>';
        }
        const matched = cell.matched || cell.rows || [];
        return '<details class="rv-six-d"><summary>底层明细（命中 ' + matched.length + '）</summary>'
          + sixTbl(matched) + '</details>';
      };

      const sixCard = (cell, i) => {
        const rows = cell.rows || [];
        const key = CHIP_KEY[cell.key] || 'd5';
        const chips = (cell.chips && cell.chips.length) ? cell.chips
          : rows.slice(0, 5).map((r) => fallbackChip(r, key));
        const codeOf = codeIndex(rows.concat(cell.matched || []));
        const act = cell.action && String(cell.action).trim();
        const count = cell.n_matched != null ? cell.n_matched : rows.length;
        return '<article class="rv-six-card c' + (i + 1) + '" data-key="' + esc(cell.key) + '">'
          + '<div class="rv-six-hd"><span class="rv-six-no">' + esc(cell.no || ('0' + (i + 1)).slice(-2))
          + '</span><b>' + esc(cell.title || cell.short || cell.key) + '</b>'
          + '<span class="rv-six-tag">' + (cell.key === 'limitup_concentration'
            ? ('覆盖 ' + esc(cell.coverage || '—'))
            : ('命中 ' + count + ' 个')) + '</span></div>'
          + (chips.length
            ? '<div class="rv-six-chips">' + chips.map((c) => {
              const cd = codeOf[String(c.name)] || null;
              return '<span class="rv-six-chip' + (cd ? ' rv-link' : '') + '"' + linkAttrs(cd, c.name) + ''
                + (cd ? ' title="左键开板块详情 · 右键出板块菜单"' : '') + '>' + esc(c.name)
                + '<em>（' + esc(c.metric) + '）</em>'
                + (cell.key === 'strong_resonance' && c.state && c.state !== '多'
                  ? '<i class="rv-six-flag">未转多</i>' : '') + '</span>';
            }).join('') + '</div>'
            : '<div class="rv-six-chips"><span class="rv-six-chip">'
              + esc(cell.empty_note || '无命中') + '</span></div>')
          + '<div class="rv-six-basis">' + esc(cell.basis || cell.criteria || '') + '</div>'
          + (act ? '<div class="rv-six-act"><span class="lb">动作</span>' + esc(act) + '</div>'
            : '<div class="rv-six-act pending"><span class="lb">动作</span>待填（夏天写）</div>')
          + sixDetail(cell)
          + '</article>';
      };

      const fallbackHeadline = (d) => {
        const t = d.temperature || {};
        const gate = t.gate || {};
        const br = t.breadth || {};
        const lu = t.limitup || {};
        const vs = t.amv_vs_ma10 || {};
        const ex = t.excess || {};
        const amv = (t.amv || {}).value;
        const ma10 = (t.ma10 || {}).value;
        // A：旧产物没有 headline 时也不许只写「AMV 多」
        let head = '今日 AMV ' + (amv == null ? '—' : Math.round(amv).toLocaleString('zh-CN')) + ' 亿';
        if (ex.value != null) {
          head += '，' + (vs.position === 'below' ? '低于' : '高于') + ' MA10 '
            + Math.abs(Math.round(ex.value)).toLocaleString('zh-CN') + ' 亿（' + sgn(ex.pct, 2) + '%）';
        }
        const stateTxt = 'state=' + esc(t.state == null ? '—' : t.state)
          + (vs.unconfirmed ? '·单轨未确认' : (t.resonance ? '·共振' : ''));
        const parts = [head + '，' + stateTxt];
        if (gate.pct != null) parts.push('闸门 ' + gate.pct + '%');
        const inner = [];
        if (br.ratio_pct != null) inner.push('多头家数 ' + br.ratio_pct + '%');
        if (lu.zt != null) inner.push('涨停 ' + lu.zt + ' 家');
        return parts.join('、') + (inner.length ? '（' + inner.join('、') + '）' : '');
      };

      // 温度区（A：显式给 AMV / MA10 / 差额 / state / 闸门 / 多头 / 涨停；state=多 但低于 MA10 → 单轨未确认）
      const sixTempBar = (d) => {
        const t = d.temperature || {};
        const ex = t.excess || {};
        const vs = t.amv_vs_ma10 || {};
        const gate = t.gate || {};
        const br = t.breadth || {};
        const lu = t.limitup || {};
        const sw = t.switch || {};
        const amv = (t.amv || {}).value;
        const ma10 = (t.ma10 || {}).value;
        const cells = [
          ['AMV', amv == null ? '—' : Math.round(amv).toLocaleString('zh-CN') + ' 亿', null],
          ['MA10', ma10 == null ? '—' : Math.round(ma10).toLocaleString('zh-CN') + ' 亿', null],
          [(vs.position === 'below' ? '低于 MA10' : '高于 MA10'),
            ex.value == null ? '—' : Math.abs(ex.value).toFixed(2) + ' 亿（' + sgn(ex.pct, 2) + '%）',
            ex.value == null ? null : (ex.value < 0 ? 'down' : 'up')],
          ['state', (t.state == null ? '—' : t.state) + (vs.unconfirmed ? ' · 单轨未确认' : '')
            + (sw.date ? ' · 第 ' + (t.day_n == null ? '—' : t.day_n) + ' 天' : ''),
            vs.unconfirmed ? 'warn' : null],
          ['闸门', (gate.pct == null ? '—' : gate.pct + '%')
            + (gate.amount == null ? '' : ' · ' + (gate.amount / 1e4).toFixed(2) + ' 万'), null],
          ['多头 / 涨停', (br.ratio_pct == null ? '—' : br.ratio_pct + '%') + ' / '
            + (lu.zt == null ? '—' : lu.zt) + ' 家', null],
        ];
        return '<div class="rv-six-temp">' + cells.map(([k, v, cls]) =>
          '<div><span>' + esc(k) + '</span><b' + (cls ? ' class="rv-six-' + cls + '"' : '') + '>'
          + esc(v) + '</b></div>').join('') + '</div>';
      };

      const renderSix = async () => {
        // 「日期回看沿用复盘卡现有逻辑」：默认跟复盘卡归属日；可用 args.date / ?sixdate= 覆盖（回看/验收）。
        const want = S.sixDate || (S.data && (S.data.date || (S.data.meta || {}).trade_date)) || null;
        let d = null;
        let usedWant = false;
        try {
          d = await api.get('/api/review_six.json' + (want ? '?date=' + encodeURIComponent(want) : ''));
          usedWant = !!want;
        } catch (e) {
          if (want) {
            try { d = await api.get('/api/review_six.json'); } catch (e2) { d = null; }
          }
        }
        if (!d) return { ok: false, why: '六格产物不可用（先跑 tools/review_six_build.py）' };
        const meta = d.meta || {};
        const key = [meta.target_date, (d.cells || []).length, meta.generated_at || ''].join('|');
        if (S.html['six-key'] === key && body.querySelector('.rv-six')) return { ok: true };
        S.html['six-key'] = key;
        const srcNames = Object.keys(meta.sources || {}).join(' / ');
        const top = '<div class="src rv-topline">六格产物: ' + esc(meta.target_date || '—')
          + ' ｜ 生成: ' + esc(meta.generated_at || '—')
          + ' ｜ 口径: d1/d5 = 份额Δ（pp）· pct = 板块指数涨跌（%）'
          + ' ｜ 源: ' + esc(srcNames || '—') + '</div>';
        setHtml(S.el.body, top + '<div class="rv-six">'
          + '<div class="rv-six-head"><b>' + esc(d.headline || fallbackHeadline(d)) + '</b></div>'
          + sixTempBar(d)
          + '<div class="rv-six-grid">' + (d.cells || []).map(sixCard).join('') + '</div>'
          + '<div class="rv-note">数据源：data/panel/review_six_cells_' + esc(String(meta.target_date || '').replace(/-/g, ''))
          + '.json（生成器 tools/review_six_build.py）。每格「依据」由数据层生成，动作字段由夏天写入，未写时显示「待填」。</div>'
          + '</div>', 'six');
        const drift = usedWant && want && meta.target_date && meta.target_date !== want;
        setNote(drift ? ('六格产物日期 ' + meta.target_date + ' 与复盘日 ' + want + ' 不一致（已显示产物日期）') : '');
        // 从板块页点格跳过来：定位到对应卡片（一次性）
        if (S.sixFocus) {
          const focusKey = String(S.sixFocus).replace(/[^A-Za-z0-9_]/g, '');
          const target = focusKey ? body.querySelector('.rv-six-card[data-key="' + focusKey + '"]') : null;
          if (target) {
            try { target.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ }
            target.classList.add('focus');
            setTimeout(() => { try { target.classList.remove('focus'); } catch (e) { /* ignore */ } }, 1800);
          }
          S.sixFocus = '';
        }
        return { ok: true };
      };

      const RENDER = { chart: renderChart, paper: renderPaper, doc: renderDoc, six: renderSix };
      const CHAIN = { chart: ['chart', 'paper', 'doc'], paper: ['paper', 'doc'], doc: ['doc'], six: ['six'] };

      const load = async (force) => {
        if (!S.alive) return;
        // 加载中再次切视图（如刚点复盘就点六格）：记住最后一次请求，本轮结束后补跑
        if (S.loading) { S.pending = { force: !!force }; return; }
        S.loading = true;
        if (force) S.html = Object.create(null);
        renderHead();
        setStatus('');
        try {
          const chain = CHAIN[S.view] || CHAIN.doc;
          const fails = [];
          for (const view of chain) {
            if (!S.alive) return;
            const res = await RENDER[view]();
            if (!S.alive) return;
            if (res && res.ok) {
              if (view !== S.view) {
                const label = (VIEWS.find((v) => v[0] === view) || [view, view])[1];
                setNote('已自动回退到「' + label + '」：' + (fails.join('；') || '上一级不可用'));
              }
              return;
            }
            fails.push((res && res.why) || '不可用');
          }
          setStatus('复盘渲染失败：' + (fails.join('；') || '全部视图不可用'));
          setHtml(S.el.body, '<div class="rv-err">复盘渲染失败：' + esc(fails.join('；')) + '</div>', 'allfail');
        } finally {
          S.loading = false;
          if (S.pending) {
            const p = S.pending;
            S.pending = null;
            load(p.force);
          }
        }
      };
      S.load = load;

      // 事件委托：视图切换 / 档位切换（按钮随数据重绘，监听挂稳定的 wrap 上）
      wrap.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const v = t.closest('[data-rvview]');
        if (v) {
          const next = v.getAttribute('data-rvview');
          if (next === S.view) return;
          S.view = next;
          S.html = Object.create(null);
          body.textContent = '';
          load(true);
          return;
        }
        const m = t.closest('[data-rvmode]');
        if (m) {
          const next = m.getAttribute('data-rvmode') === 'full' ? 'full' : 'brief';
          if (next === S.mode) return;
          S.mode = next;
          lsSetMode(next);
          S.html = Object.create(null);
          load(true);
        }
      });

      // 主题变化 → 重取 v6（图表走 CSS 变量瞬时适配，纸面固定配色不重渲染）；旧端 MutationObserver 同口径
      if (typeof MutationObserver !== 'undefined' && document.documentElement) {
        try {
          S.themeObs = new MutationObserver(() => {
            if (!S.alive || S.view !== 'doc') return;
            S.html = Object.create(null);
            load(true);
          });
          S.themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        } catch (e) { /* 监听失败不阻断卡片 */ }
      }

      await load(false);
    },

    async unmount() {
      const S = this._rv;
      if (!S) return;
      S.alive = false;
      if (S.themeObs) { try { S.themeObs.disconnect(); } catch (e) { /* ignore */ } }
      S.html = Object.create(null);
      const paper = document.getElementById(PAPER_CSS_ID);
      if (paper && paper.parentNode) paper.parentNode.removeChild(paper);
      this._rv = null;
    },

    // 不声明 refreshEvery（复盘 MD 日频）；保留 refresh() 契约占位 + 卡头手动刷新
    async refresh() {
      const S = this._rv;
      if (!S || !S.alive || S.loading) return;
      await S.load(false);
    },
  }],
};
