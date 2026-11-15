/**
 * 板块卡片 · 视觉样式（移植自 8002 工作台 style.css）
 *
 * 来源：`夏天-activity/工具/dashboard/web/style.css`（1049 行，长期打磨）。
 * 主人 2026-10-25 要求「按以前的布局和排版来」——这里不重新设计，只做三件事：
 *   ① 规则原样搬（数字、间距、字号、字重、圆角、虚线、徽标形态全部照旧）；
 *   ② 选择器加 `.sector-card` 作用域，避免污染壳与其它卡（8002 是整页独占，v2 是卡）；
 *   ③ token 做一层别名（见下方 ALIAS 说明），让这套规则跟随 v2 的主题切换。
 *
 * 别名层为什么必要：8002 用 `--paper/--ink/--in/--out/--serif/--mono` 一套自有变量，
 * v2 的主题表是 `--bg/--fg/--up/--down/--font-*` 另一套。直接搬规则会因为变量不存在而全部失效。
 * 所以在本卡根节点上把 8002 的变量名映射到 v2 的主题变量——规则一个字不改，主题照常跟随。
 */

export const CARD_CSS = `
/* ============================ token 别名层 ============================
   8002 变量名 → v2 主题变量。挂在 .sector-card 上，只影响本卡。

   为什么先在 html 上做一层“桥”：--line / --line-soft / --accent / --accent-on
   在 v2 主题表里与 8002 同名。直接在 .sector-card 上写 --line: var(--line) 是
   自引用循环，CSS 规范判定为 invalid ⇒ 所有 var(--line) 失效、border-color 回退成
   currentColor（2026-10-25 实测：面板边框变成文字色，seg 高亮色全丢）。
   所以在 html 上把主题值取到不同名的 --sc-*-src，再由 .sector-card 映射过去。

   注意 --serif / --ui / --mono：8002 正文是衬线（EB Garamond），v2 主题只有 --font-ui/--font-mono。
   这里保留 8002 的字族声明，但把首个候选换成主题字体，缺字体时仍能回退到系统衬线/等宽。 */
html{
  --sc-line-src:var(--line);
  --sc-line-soft-src:var(--line-soft);
  --sc-accent-src:var(--accent);
  --sc-accent-on-src:var(--accent-on);
}
.sector-card{
  --paper: var(--bg);
  --paper-card: var(--card);
  --paper-deep: var(--card2);
  --ink: var(--fg);
  --ink-2: var(--fg-strong, var(--fg));
  --ink-3: var(--dim);
  --line: var(--sc-line-src);
  --line-soft: var(--sc-line-soft-src, var(--sc-line-src));
  --accent: var(--sc-accent-src);
  --accent-hover: var(--accent-solid, var(--sc-accent-src));
  --accent-light: var(--accent-bg);
  --accent-on: var(--sc-accent-on-src, var(--card));
  --in: var(--up-solid, var(--up));
  --out: var(--down-solid, var(--down));
  --in-bg: var(--up-bg, color-mix(in srgb, var(--up-solid) 8%, transparent));
  --out-bg: var(--down-bg, color-mix(in srgb, var(--down-solid) 8%, transparent));
  --gold: var(--warn-solid, var(--warn));
  --ochre: var(--p-amber-600, var(--warn-solid, var(--warn)));
  --radius: var(--radius-sm);
  --serif: var(--font-ui);
  --ui: var(--font-ui);
  --mono: var(--font-mono);
  --band-op: 0.12;
  --border: var(--sc-line-src);
  --text: var(--fg);
  --muted: var(--dim);
  font-family: var(--serif);
  font-size: 15px;
  line-height: 1.5;
  color: var(--ink);
}

/* ============================ 工具栏 ============================ */
.sector-card .controls{padding:6px 12px;margin-bottom:10px;background:var(--paper-card);border:0.5px solid var(--line);border-radius:var(--radius)}
.sector-card .controls .ctrl-row{display:flex;align-items:center;gap:6px;flex-wrap:nowrap}
.sector-card .ctrl-row + .ctrl-row{margin-top:6px}
.sector-card .ctrl-label{font-family:var(--ui);font-size:12px;color:var(--ink-3)}
.sector-card .ctrl-hint{font-family:var(--ui);font-size:12px;color:var(--ink-3);margin-left:auto}

.sector-card .seg{display:inline-flex;border:0.5px solid var(--line);border-radius:var(--radius);overflow:hidden;background:var(--paper-card)}
.sector-card .seg-btn{font-family:var(--ui);font-size:11px;padding:2px 6px;border:0;background:transparent;color:var(--ink-2);cursor:pointer;white-space:nowrap;line-height:1.6}
.sector-card .seg-btn + .seg-btn{border-left:0.5px solid var(--line)}
.sector-card .seg-btn:hover{background:var(--accent-light)}
.sector-card .seg-btn.active{background:var(--accent);color:var(--accent-on)}

.sector-card .step{font-family:var(--ui);font-size:11px;padding:2px 7px;border:0.5px solid var(--line);border-radius:var(--radius);background:var(--paper-card);color:var(--ink-2);cursor:pointer;line-height:1.6}
.sector-card .step:hover:not(:disabled){background:var(--accent-light);color:var(--accent-hover)}
.sector-card .step:disabled{color:var(--line);cursor:default;background:transparent}

.sector-card .rr-date{font-family:var(--mono);font-size:13px;color:var(--ink-2)}
.sector-card .rr-date.cur{color:var(--accent-hover)}
.sector-card .rr-arrow{color:var(--line)}
.sector-card .rr-hint{margin-left:auto;color:var(--ink-3);font-family:var(--ui);font-size:11px}

.sector-card .searchbox{position:relative;flex:0 1 210px;min-width:132px}
.sector-card .searchbox input{width:100%;font-family:var(--ui);font-size:12px;padding:3px 8px;border:0.5px solid var(--line);border-radius:var(--radius);background:var(--paper-card);color:var(--ink)}
.sector-card .searchbox input:focus{outline:none;border-color:var(--accent)}
.sector-card .search-drop{position:absolute;top:100%;left:0;right:0;margin-top:2px;max-height:280px;overflow:auto;background:var(--paper-card);border:0.5px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow-pop);z-index:40}
.sector-card .search-drop[hidden]{display:none}
.sector-card .sd-item{display:flex;align-items:center;gap:6px;padding:4px 8px;font-family:var(--ui);font-size:12px;cursor:pointer;border:0}
.sector-card .sd-item:hover{background:var(--accent-light)}
.sector-card .sd-nm{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
.sector-card .sd-extra{flex:none;color:var(--ink-3);font-size:11px}
.sector-card .sd-empty{padding:6px 8px;font-family:var(--ui);font-size:12px;color:var(--ink-3)}

.sector-card .advbox{margin-top:6px}
.sector-card .advbox > summary{font-family:var(--ui);font-size:11px;color:var(--ink-3);cursor:pointer;list-style:none}
.sector-card .advbox > summary::-webkit-details-marker{display:none}
.sector-card .advbox > summary::before{content:'▸';font-size:9px;color:var(--accent);margin-right:4px}
.sector-card .advbox[open] > summary::before{content:'▾'}
.sector-card .advbox > summary:hover{color:var(--ink-2)}
.sector-card .adv-body{display:flex;align-items:center;gap:8px;margin-top:4px}

.sector-card .dual{position:relative;flex:1;height:24px}
.sector-card .dual .track{position:absolute;left:0;right:0;top:10px;height:4px;background:var(--paper-deep);border-radius:2px}
.sector-card .dual .fill{position:absolute;top:10px;height:4px;background:var(--accent);opacity:.45;border-radius:2px}
.sector-card .dual .grab{position:absolute;top:6px;height:12px;cursor:grab;border-radius:6px}
.sector-card .dual .grab:active{cursor:grabbing}
.sector-card .dual input[type=range]{position:absolute;left:0;width:100%;top:2px;height:20px;margin:0;background:transparent;-webkit-appearance:none;appearance:none;pointer-events:none}
.sector-card .dual input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:var(--paper-card);border:1.5px solid var(--accent);pointer-events:auto;cursor:pointer}
.sector-card .dual input[type=range]::-moz-range-thumb{width:10px;height:10px;border-radius:50%;background:var(--paper-card);border:1.5px solid var(--accent);pointer-events:auto;cursor:pointer}

.sector-card .l1box{margin-bottom:8px}
.sector-card .l1box > summary{font-family:var(--ui);font-size:12px;color:var(--ink-3);cursor:pointer;list-style:none}
.sector-card .l1box > summary::-webkit-details-marker{display:none}
.sector-card .l1box > summary::before{content:'▸';font-size:9px;color:var(--accent);margin-right:4px}
.sector-card .l1box[open] > summary::before{content:'▾'}
.sector-card .l1box > summary:hover{color:var(--ink-2)}
.sector-card .l1-sum-t{color:var(--ink-2)}
.sector-card .l1box .l1-body{padding:2px 12px 10px}
.sector-card .l1box .crumb{margin-bottom:6px;min-height:0}
.sector-card .l1box .dir{margin-bottom:0}

.sector-card .crumb{font-family:var(--ui);font-size:12px;color:var(--ink-3);margin-bottom:8px}
.sector-card .crumb a{color:var(--accent);cursor:pointer;text-decoration:none}
.sector-card .crumb a:hover{color:var(--accent-hover);text-decoration:underline}
.sector-card .crumb .sep{margin:0 6px;color:var(--line)}
.sector-card .crumb .cur{color:var(--ink)}

.sector-card .dir{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:12px}
.sector-card .chip{font-family:var(--ui);font-size:11px;padding:1px 9px;border:0.5px solid var(--line);border-radius:999px;background:var(--paper-card);color:var(--ink-2);cursor:pointer;line-height:1.7}
.sector-card .chip:hover{background:var(--accent-light)}
.sector-card .chip.on{background:var(--accent);border-color:var(--accent);color:var(--accent-on)}

/* ============================ 榜单两栏 ============================ */
.sector-card .panels{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
.sector-card .panel{background:var(--paper-card);border:0.5px solid var(--line);border-radius:var(--radius);padding:12px 14px 6px;min-width:0}
.sector-card .panel-hd{display:flex;align-items:center;gap:6px;font-family:var(--ui);font-size:13px;color:var(--ink-2);padding:0 0 8px}
.sector-card .sw{width:10px;height:10px;border-radius:2px}
.sector-card .sw-in{background:var(--in)}
.sector-card .sw-out{background:var(--out)}

.sector-card .col-hd{display:grid;grid-template-columns:32px 26px minmax(0,1fr) 42px 50px 54px 54px 54px 62px 46px 12px;gap:7px;padding:4px 2px 6px;font-family:var(--ui);font-size:10.5px;color:var(--ink-3);border-bottom:0.5px solid var(--line)}
.sector-card .col-hd .th{display:inline-flex;align-items:center;justify-content:center;gap:2px;white-space:nowrap;background:none;border:0;font:inherit;color:inherit;padding:0;cursor:default}
.sector-card .col-hd .ch-mv{grid-column:1;justify-content:flex-start}
.sector-card .col-hd .ch-nm{grid-column:2 / 4;justify-content:flex-start}
.sector-card .col-hd .ch-sc{grid-column:4}
.sector-card .col-hd .ch-wt{grid-column:5}
.sector-card .col-hd .ch-dl{grid-column:6}
.sector-card .col-hd .ch-ap{grid-column:7}
.sector-card .col-hd .ch-rl{grid-column:8}
.sector-card .col-hd .ch-mn{grid-column:9}
.sector-card .col-hd .th-hits{grid-column:10;cursor:help}
.sector-card .col-hd button.th{cursor:pointer}
.sector-card .col-hd button.th:hover{color:var(--accent)}
.sector-card .col-hd .th.on{color:var(--accent);font-weight:600}
.sector-card .col-hd button.th.on{background:var(--accent-light);border-radius:2px}
.sector-card .col-hd .arr::before{content:'';display:inline-block;width:7px}
.sector-card .col-hd .th.on .arr::before{content:'▾';font-size:8px}
.sector-card .col-hd .ch-mv{justify-content:flex-start}

.sector-card .list{list-style:none;margin:0;padding:0}
.sector-card .row{border-bottom:0.5px solid var(--line-soft)}
.sector-card .row:last-child{border-bottom:none}
.sector-card .row-hd{display:grid;grid-template-columns:32px 26px minmax(0,1fr) 42px 50px 54px 54px 54px 62px 46px 12px;gap:7px;align-items:center;padding:7px 2px;font-family:var(--ui);font-size:13px;cursor:pointer;background:none;border:0;width:100%;text-align:left;color:var(--ink)}
.sector-card .row-body{display:none}
.sector-card .row-hd:hover{background:var(--accent-light)}

.sector-card .rk{font-family:var(--mono);font-size:12px;color:var(--ink-3);text-align:center}
.sector-card .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;gap:3px;min-width:0;text-align:left}
.sector-card .nm-t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.sector-card .weight{font-family:var(--mono);font-size:12px;color:var(--ink-3);font-variant-numeric:tabular-nums;text-align:center}
.sector-card .delta{font-family:var(--mono);font-size:13px;font-weight:500;font-variant-numeric:tabular-nums;text-align:center}
.sector-card .amvp,.sector-card .realp,.sector-card .mainp{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:12px;text-align:center}
.sector-card .score{font-family:var(--mono);font-size:12.5px;font-weight:600;text-align:center;font-variant-numeric:tabular-nums}
.sector-card .score.hot{color:var(--in)}
.sector-card .score.cold{color:var(--out)}
.sector-card .score.fl{color:var(--ink-3);font-weight:400}
.sector-card .hits{font-family:var(--mono);font-size:12px;color:var(--ink-3);text-align:center;font-variant-numeric:tabular-nums;cursor:help}
.sector-card .hits.warm{color:var(--accent)}
.sector-card .hits.hot{color:var(--in);font-weight:600}
.sector-card .mv{font-family:var(--mono);font-size:11px;text-align:left;color:var(--ink-3)}
.sector-card .chev{color:var(--line);font-size:14px;text-align:right}
.sector-card .pos{color:var(--in)}
.sector-card .neg{color:var(--out)}
.sector-card .flat{color:var(--ink-3)}
.sector-card .mainp.flow-pos{color:var(--in)}
.sector-card .mainp.flow-neg{color:var(--out)}
.sector-card .up{color:var(--in)}
.sector-card .dn{color:var(--out)}
.sector-card .rank-mv{font-family:var(--mono);font-size:11px}

.sector-card .streak{display:none;font-family:var(--mono);font-size:10px;padding:0 4px;border-radius:2px;line-height:1.5;border:0.5px solid transparent}
.sector-card .streak.on{display:inline-block;color:var(--accent-hover);background:var(--accent-light);border-color:var(--line)}
.sector-card .streak.hot{color:var(--in);background:var(--in-bg);border-color:var(--in)}
.sector-card .streak:empty,.sector-card .badge:empty{display:none}
.sector-card .badge{display:none;font-family:var(--mono);font-size:10px;padding:0 4px;border-radius:2px;line-height:1.5}
.sector-card .badge.is-new{display:inline-block;background:var(--gold);color:var(--paper-card)}
.sector-card .badge.is-re{display:inline-block;background:var(--in-bg);color:var(--ochre);border:0.5px solid var(--ochre)}
.sector-card .row.is-exit{border:1px dashed var(--line);border-radius:var(--radius);margin:4px 0}
.sector-card .row.is-exit .row-hd{opacity:0.66}
.sector-card .row.is-exit .nm{color:var(--ink-3)}

.sector-card .nm .dk{display:inline-flex;align-items:center;justify-content:center;min-width:15px;height:15px;padding:0 3px;border-radius:2px;font-family:var(--ui);font-size:10px;line-height:1;flex:none}
.sector-card .nm .dk.bull{background:var(--in-bg);color:var(--in)}
.sector-card .nm .dk.bear{background:var(--out-bg);color:var(--out)}
.sector-card .nm .dk.half{background:transparent;border:0.5px dashed currentColor}

.sector-card .rb-empty{font-family:var(--ui);font-size:12px;color:var(--ink-3);padding:8px 0}

/* ============================ 视图（网格 / 四象） ============================ */
.sector-card .viz-grids{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.sector-card .viz-grids.one{grid-template-columns:minmax(0,1fr)}
.sector-card .viz-col{min-width:0}
.sector-card .viz-col-hd{font-family:var(--ui);font-size:11px;color:var(--ink-2);padding:4px 2px 7px}
.sector-card .viz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px}
.sector-card .viz-cell{border:0.5px solid var(--line);border-radius:var(--radius);background:var(--paper-card);padding:7px 9px;cursor:pointer;min-width:0}
.sector-card .viz-cell:hover,.sector-card .viz-cell:focus{border-color:var(--accent);background:var(--paper)}
.sector-card .viz-cell .n{font-size:12.5px;font-weight:500;white-space:nowrap;overflow:hidden;display:flex;align-items:center;gap:3px}
.sector-card .viz-cell .n > span{overflow:hidden;text-overflow:ellipsis}
.sector-card .viz-cell .f{display:flex;justify-content:space-between;font-family:var(--ui);font-size:11px;margin-top:2px;font-variant-numeric:tabular-nums}
.sector-card .viz-cell svg.s{width:100%;height:36px;display:block;margin-top:2px}
.sector-card .viz-cell svg.s path{vector-effect:non-scaling-stroke}
.sector-card .viz-up{color:var(--in)}
.sector-card .viz-dn{color:var(--out)}
.sector-card .viz-fl{color:var(--ink-3)}

.sector-card .vq-bar{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin:0 0 8px;font-family:var(--ui);font-size:11px;color:var(--ink-3)}
.sector-card .vq-k{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.sector-card .vq-dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.sector-card .vq-eg{width:22px;height:0;display:inline-block;border-top:1.6px solid var(--ink-3)}
.sector-card .vq-eg.vq-dash{border-top-style:dashed}
.sector-card .vq-grid{stroke:var(--line-soft);stroke-width:1}
.sector-card .vq-axis{stroke:var(--ink-3);stroke-width:1;stroke-dasharray:4 3;opacity:0.7}
.sector-card .vq-tx{font-size:9px;fill:var(--ink-3);font-family:var(--mono);font-variant-numeric:tabular-nums}
.sector-card .vq-axn{font-size:9.5px;fill:var(--ink-3)}
.sector-card .vq-q{font-size:11px;font-weight:500;fill:var(--ink-3);font-family:var(--ui)}
.sector-card .vq-b{cursor:pointer}
.sector-card .vq-b:focus{outline:none}
.sector-card .vq-b:hover circle:not(.vq-ring){stroke-width:2.6}
.sector-card .vq-lab{font-size:10.5px;font-weight:500;font-family:var(--ui);pointer-events:none}
.sector-card .vq-l{cursor:pointer}
.sector-card .vq-l .vq-hit{fill:none;stroke:transparent;pointer-events:all}
.sector-card .vq-l:hover .vq-lab{font-weight:700}
.sector-card .vq-foot{margin-top:8px;font-family:var(--ui);font-size:11px;color:var(--ink-3);line-height:1.7}

/* ============================ 各大指数（左全列 + 右内联走势与数据）
   逐字照 8002 style.css:842-868 / 980-984（2026-09-30 主人定「各大指数」形态）：
   左清单窄（430px）且只留 序号/指数/真实涨跌/主力净流入，右栏放选中指数的走势与数据。
   早先 v2 把 .panels.idx 的样式漏了 ⇒ 12 个指数挤成一张半屏表、右栏空缺（标注 N20261027-011947-26）。 */
.sector-card .list li.sel{background:var(--accent-light)}
.sector-card .list li.sel .nm-t{font-weight:500;color:var(--accent-hover)}
.sector-card .list li.idx-detail{list-style:none;padding:4px 8px 12px;background:transparent}
.sector-card .list li.idx-detail .row-hd{cursor:default}
/* 内联详情没有「关闭」语义（✕ 无效）⇒ 隐藏，避免误认为可点（8002 同口径） */
.sector-card .list li.idx-detail .card-x{display:none}
.sector-card .panels.idx{grid-template-columns:minmax(0,520px) minmax(0,1fr)}   /* 2026-11-05（标注 81）：右栏恢复，左栏按 5 列（板块/权重/活跃/真实/主力）定 520px */
/* 2026-11-01 主人：指数只有十来个 ⇒ 去掉名次变化与序号，补上权重、活跃涨跌。
   列：板块 | 权重 | 活跃涨跌 | 真实涨跌 | 主力净流入（权重变化仍隐：已有权重水平 + 活跃涨跌两项）。 */
.sector-card .panels.idx .panel-in .col-hd{grid-template-columns:minmax(0,1fr) 52px 56px 54px 62px 12px}
.sector-card .panels.idx .panel-in .col-hd .ch-mv,
.sector-card .panels.idx .panel-in .col-hd .ch-dl{display:none}
.sector-card .panels.idx .panel-in .col-hd .ch-nm{grid-column:1}
.sector-card .panels.idx .panel-in .col-hd .ch-wt{grid-column:2}
.sector-card .panels.idx .panel-in .col-hd .ch-ap{grid-column:3}
.sector-card .panels.idx .panel-in .col-hd .ch-rl{grid-column:4}
.sector-card .panels.idx .panel-in .col-hd .ch-mn{grid-column:5}
.sector-card .panels.idx .panel-in .row-hd{grid-template-columns:minmax(0,1fr) 52px 56px 54px 62px 12px}
.sector-card .panels.idx .panel-in .row-hd .mv,
.sector-card .panels.idx .panel-in .row-hd .rk,
.sector-card .panels.idx .panel-in .row-hd .delta{display:none}
/* 指数模式少三列，在榜/评分列同样隐藏，避免 grid 多一格错位 */
.sector-card .panels.idx .panel-in .row-hd .hits,
.sector-card .panels.idx .panel-in .col-hd .th-hits,
.sector-card .panels.idx .panel-in .row-hd .score,
.sector-card .panels.idx .panel-in .col-hd .ch-sc{display:none}

/* 内联详情：把弹卡浮层还原成右栏静态块（复用 .sector-modal .card-* 整批规则，不另写一套） */
.sector-modal.idx-inline{position:static;inset:auto;display:block;padding:0;background:none;backdrop-filter:none;-webkit-backdrop-filter:none}
.sector-modal.idx-inline .modal{width:auto;max-height:none;overflow:visible;border:0;border-radius:0;padding:0;background:transparent}
/* 2026-11-01 主人：截图的图表块（走势 / 黄白线 / 板块数据 / 贡献分项）占视觉焦点，
   个股表降为右栏窄条（去掉序号/名次/评分后 7 列），看细的走右键进板块详情。
   11-01 二次（主人：「右边个股被压成两排，左边还空了很多位置」）：
   右栏 470 → 560（股价名挤到两行就是因为名栏只 ~120px）；左图改为按栏宽重画
   （detail.js fitSparks），不再固定 320px 居中留白。 */
.sector-modal.idx-inline .card-body{grid-template-columns:minmax(0,1fr) minmax(360px,560px)}
.sector-modal.idx-inline .card-right{max-height:430px}
/* 内联右栏窄（470px）：表格不再强制 560px 最小宽（那是弹窗口径），否则最右列被切进滚动区。
   7 列按 470px 自然压缩，数字列 ~60px 仍装得下 +15.87%；看细的走右键→板块详情。 */
.sector-modal.idx-inline .card-tbl{min-width:0}
/* 右栏 470px 里，创/科/北 这类“板块静态标签”会把“多/空 + 名 + 结构徽标”挤到第二行
   （实测 60 行中 18 行高 44px、其余 25px，表格参差不齐）；内联只留 多/空 + 名 + 结构徽标，
   板签留给弹窗（弹窗宽度够，不动）。 */
.sector-modal.idx-inline .card-tbl td .bd{display:none}
@media (max-width:1100px){.sector-card .panels.idx{grid-template-columns:minmax(0,1fr)}}

/* ============================ 页脚口径 ============================ */
.sector-card .foot{font-family:var(--ui);font-size:11.5px;line-height:1.8;color:var(--ink-3);border-top:0.5px solid var(--line);padding-top:10px;margin-top:12px}
.sector-card .foot b{color:var(--ink-2);font-weight:600}

/* ============================ 视图容器（8002 .vizbox） ============================ */
.sector-card .vizbox{background:var(--paper-card);border:0.5px solid var(--line);border-radius:var(--radius);padding:10px 12px 6px}
.sector-card .viz-cap{font-family:var(--ui);font-size:11px;color:var(--ink-3);margin-bottom:7px}
.sector-card .vizbox svg{width:100%;display:block}
.sector-card .viz-cell .b{font-family:var(--ui);font-size:10px;font-weight:400;color:var(--ink-3)}
.sector-card .viz-cell .b i{font-style:normal;display:inline-flex;align-items:center;justify-content:center;min-width:14px;height:14px;padding:0 3px;margin-left:4px;border-radius:3px;font-family:var(--ui);font-size:10px;line-height:1;white-space:nowrap;vertical-align:1px}
.sector-card .viz-cell .b .bg-new{background:color-mix(in srgb,var(--gold) 18%,transparent);color:var(--ochre)}
.sector-card .viz-cell .b .bg-re{background:color-mix(in srgb,var(--accent) 18%,transparent);color:var(--accent-hover)}
.sector-card .viz-cell .b .bg-ex{background:color-mix(in srgb,var(--ink-3) 20%,transparent);color:var(--ink-2)}
.sector-card .viz-cell .b .bg-st{background:color-mix(in srgb,var(--ink) 7%,transparent);color:var(--ink-2)}
.sector-card .viz-cell .b .bg-rk{background:color-mix(in srgb,var(--ink) 7%,transparent);color:var(--ink-3)}
.sector-card .vq-bar .vz-grp{display:inline-flex;border:0.5px solid var(--line);border-radius:999px;overflow:hidden}
.sector-card .vq-bar .vz-grp button{font:inherit;font-size:11px;padding:2px 10px;border:0;background:transparent;color:var(--ink-2);cursor:pointer}
.sector-card .vq-bar .vz-grp button + button{border-left:0.5px solid var(--line)}
.sector-card .vq-bar .vz-grp button.on{background:var(--accent);color:var(--accent-on)}
.sector-card .vq-hint{margin-left:auto}
.sector-card .vq-b.vq-hi circle:not(.vq-ring){stroke-width:2.8}
.sector-card .vq-b.vq-dim,.sector-card .vq-l.vq-dim{opacity:0.12}
.sector-card .vq-ring{display:none}
.sector-card .vq-b.vq-pin .vq-ring{display:inline}
.sector-card .vq-b.vq-pin circle:not(.vq-ring){stroke-width:2.6}
.sector-card .vq-l.vq-hi .vq-lab,.sector-card .vq-l.vq-pin .vq-lab{font-weight:700}
.sector-card .vq-l:focus-visible .vq-hit{stroke:var(--accent);stroke-width:1}
.sector-card .vq-tg{fill:none;stroke-width:5;stroke-linecap:round;stroke-dasharray:14 86;opacity:0;pointer-events:none}
.sector-card .vq-tf{fill:none;stroke-width:2;stroke-linecap:round;stroke-dasharray:6 94;opacity:0;pointer-events:none}
@keyframes vqFlow{from{stroke-dashoffset:100}to{stroke-dashoffset:0}}
@media (prefers-reduced-motion: reduce){.sector-card .vq-tf,.sector-card .vq-tg{display:none}}
.sector-card .vq-blab{font-size:10.5px;font-weight:600;font-family:var(--ui);dominant-baseline:middle;pointer-events:none}
.sector-card .vq-blab-h{font-size:10.5px;font-weight:600;font-family:var(--ui);dominant-baseline:middle;pointer-events:none;fill:none;stroke:var(--paper-card);stroke-width:3;stroke-linejoin:round}
.sector-card .vq-lead{stroke:var(--ink-3);stroke-width:0.9;opacity:0.6;pointer-events:none}
.sector-card .vq-x{font-size:9px;fill:var(--paper-card);pointer-events:none}

/* ============================ 搜索下拉（8002 .sd-*） ============================ */
.sector-card .sd-item{display:flex;align-items:baseline;gap:6px;width:100%;padding:5px 8px;background:transparent;border:0;border-bottom:0.5px solid var(--line-soft);font-family:var(--ui);font-size:12px;color:var(--ink);text-align:left;cursor:pointer}
.sector-card .sd-item:last-child{border-bottom:none}
.sector-card .sd-item:hover{background:var(--accent-light)}
.sector-card .sd-lv{flex:none;padding:0 5px;border-radius:2px;font-size:10.5px;background:var(--accent-light);color:var(--accent-hover)}
.sector-card .sd-lv.lv-l3{background:color-mix(in srgb,var(--ochre) 10%,transparent);color:var(--ochre)}
.sector-card .sd-lv.lv-concept{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent-hover)}

/* ============================ 浮层 ============================ */
/* 2026-10-30（主人标注 54）：悬停浮标 z-index 70 被板块详情遮罩盖住（弹窗 z 由壳的浮层栈接管：
   Z_BASE=100、Z_STEP=5，见 shell.js::overlayStack），评分轨迹 tip 看不见。
   浮标是临时层、永远应在最上 → 140，高于浮层栈（100+5n）与标注层（110/120/130）。 */
.sector-card-tip{position:fixed;z-index:140;background:var(--card);border:0.5px solid var(--line);border-radius:var(--radius-sm);box-shadow:var(--shadow-pop);padding:8px 9px;pointer-events:none;font-family:var(--font-ui);font-size:11px;line-height:1.65;color:var(--fg);max-width:280px}
.sector-card-tip[hidden]{display:none}
.sector-card-tip b{color:var(--fg);font-weight:600}
.sector-card-tip.hit-tip{max-width:none;padding:9px 10px 8px}

/* 在榜悬浮矩阵（8002 heatGrid 产物：hm-hd / hm-grid / hm-c>s+b / hm-ft）
   2026-10-25：上一版把格子写成圆点，导致这三组规则全失效。尺寸照 8002 原值。 */
.sector-card-tip .hm-hd{font-size:11px;color:var(--dim);margin-bottom:5px;white-space:nowrap}
.sector-card-tip .hm-hd b{color:var(--up-solid);font-weight:600}
.sector-card-tip .hm-grid{display:grid;grid-template-columns:repeat(10,32px);gap:3px}
.sector-card-tip .hm-c{display:flex;flex-direction:column;align-items:center;justify-content:center;height:32px;border-radius:2px;font-style:normal;line-height:1.05;border:.5px solid transparent;font-family:var(--font-mono);font-variant-numeric:tabular-nums}
.sector-card-tip .hm-c>s{text-decoration:none;font-size:8.5px;opacity:.75;font-variant-numeric:tabular-nums}
.sector-card-tip .hm-c>b{font-weight:600;font-size:11.5px;font-variant-numeric:tabular-nums}
.sector-card-tip .hm-c.on{border-color:var(--fg);border-width:.5px}
.sector-card-tip .hm-ft{margin-top:5px;font-size:10px;color:var(--dim);white-space:nowrap}

/* ============================ 板块详情卡（8002 .modal / .card-* / .ctb / .card-tbl）
   挂在 document.body 上，不在 .sector-card 内 ⇒ 用 .sector-modal 作作用域。
   类名仍逐字用 8002 的，只加一层作用域前缀（避免污染壳与其它插件）。 */
.sector-modal{position:fixed;inset:0;z-index:50;background:color-mix(in srgb,var(--fg) 35%,transparent);backdrop-filter:blur(7px) saturate(1.02);-webkit-backdrop-filter:blur(7px) saturate(1.02);display:flex;align-items:center;justify-content:center;padding:24px}
.sector-modal[hidden]{display:none}
.sector-modal .modal{width:min(1180px,100%);max-height:86vh;overflow:auto;background:var(--card);border:0.5px solid var(--line);border-radius:var(--radius-sm);padding:12px 16px 16px;font-family:var(--font-ui);font-size:13px;color:var(--fg)}
.sector-modal .card-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;padding-bottom:8px;margin-bottom:8px;border-bottom:0.5px solid var(--line)}
.sector-modal .card-nm{font-size:1.15rem;font-weight:500;color:var(--fg-strong)}
.sector-modal .card-sub,.sector-modal .card-period{font-family:var(--font-mono);font-size:11px;color:var(--dim)}
.sector-modal .pin-lv{padding:0 6px;border-radius:2px;font-family:var(--font-ui);font-size:10.5px;background:var(--accent-bg);color:var(--accent-hover)}
.sector-modal .pin-lv.lv-l3{background:color-mix(in srgb,var(--ochre) 10%,transparent);color:var(--ochre)}
.sector-modal .pin-lv.lv-concept{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent-hover)}
.sector-modal .card-periods{display:inline-flex;gap:2px;margin-left:6px}
.sector-modal .cp-btn{padding:1px 7px;font-family:var(--font-ui);font-size:10.5px;line-height:1.6;color:var(--dim);background:transparent;border:0.5px solid var(--line);border-radius:var(--radius-sm);cursor:pointer}
.sector-modal .cp-btn:hover{color:var(--accent);border-color:var(--accent)}
.sector-modal .cp-btn.on{background:var(--accent-bg);color:var(--accent);border-color:var(--accent);font-weight:600}
.sector-modal .card-hd-right{margin-left:auto;display:inline-flex;align-items:center;gap:6px}
.sector-modal .card-refresh{padding:0 7px;height:20px;line-height:19px;font-size:12px;color:var(--dim);background:transparent;border:0.5px solid var(--line);border-radius:var(--radius-sm);cursor:pointer}
.sector-modal .card-refresh:hover{color:var(--accent);border-color:var(--accent)}
.sector-modal .card-x{margin-left:8px;border:none;background:transparent;color:var(--dim);cursor:pointer;font-size:14px;line-height:1}
.sector-modal .card-x:hover{color:var(--up-solid)}
.sector-modal .card-body{display:grid;grid-template-columns:minmax(0,380px) minmax(0,1fr);gap:16px;align-items:start}
.sector-modal .card-left{display:flex;flex-direction:column;gap:12px;min-width:0}
.sector-modal .card-block{min-width:0}
.sector-modal .card-block h4{margin:0 0 6px;font-family:var(--font-ui);font-size:11px;font-weight:500;color:var(--dim)}
.sector-modal .card-block .spark{width:100%;height:96px;display:block}
.sector-modal .lg-row{display:flex;align-items:center;flex-wrap:wrap;gap:2px 6px;font-family:var(--font-ui);font-size:11px;color:var(--dim);margin-top:2px}
.sector-modal .lg{display:inline-block;width:14px;height:0;border-top-width:2px;border-top-style:solid}
.sector-modal .lg-amv{border-top-color:var(--fg)}
.sector-modal .lg-ma{border-top-color:var(--ochre);border-top-style:dashed;margin-left:6px}
.sector-modal .spark-band{opacity:0.12}
.sector-modal .pz-r path{fill:var(--up-solid);opacity:0.12}
.sector-modal .pz-g path{fill:var(--down-solid);opacity:0.12}
.sector-modal .pin-cells{display:flex;flex-wrap:wrap;gap:4px 16px;margin-top:6px}
.sector-modal .pin-cell{min-width:72px}
.sector-modal .pin-k{font-family:var(--font-ui);font-size:11px;color:var(--dim)}
.sector-modal .pin-v{font-family:var(--font-mono);font-size:13px;font-weight:500;font-variant-numeric:tabular-nums}
.sector-modal .ctb{display:flex;flex-direction:column;gap:3px;margin-top:2px}
.sector-modal .ctb-row{display:grid;grid-template-columns:5.6em minmax(0,1fr) 4.8em;align-items:center;gap:8px;font-size:11px;line-height:1.5}
.sector-modal .ctb-k{color:var(--dim);white-space:nowrap;cursor:help}
.sector-modal .ctb-bar{position:relative;height:7px;background:var(--card2);border-radius:3px}
.sector-modal .ctb-bar::before{content:'';position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.sector-modal .ctb-bar i{position:absolute;top:0;bottom:0;border-radius:2px}
.sector-modal .ctb-bar i.up{background:var(--up-solid)}
.sector-modal .ctb-bar i.down{background:var(--down-solid)}
.sector-modal .ctb-v{text-align:right;font-family:var(--font-mono)}
.sector-modal .ctb-row.zero .ctb-v{color:var(--dim)}
.sector-modal .ctb-sum{margin-top:5px;padding-top:4px;border-top:0.5px solid var(--line);font-size:11px;color:var(--dim)}
.sector-modal .ctb-sum b{font-family:var(--font-mono);font-size:12px}
.sector-modal .ctb-sub{margin-left:6px;font-size:10px}
.sector-modal .ctb-note{margin-top:2px;font-size:10.5px;color:var(--dim)}
.sector-modal .ctb-note b{color:var(--fg)}

/* 结构指标（2026-10-27 补）：广度 / top1 贡献 / 有效贡献股数 / 参与计算 */
.sector-modal .st-metrics{display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;margin-top:8px;
  padding-top:7px;border-top:0.5px dashed var(--line)}
.sector-modal .st-metrics >div{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 6px;font-size:11px}
.sector-modal .st-metrics .k{color:var(--dim);flex:none}
.sector-modal .st-metrics b{font-family:var(--font-mono);font-size:12.5px;color:var(--fg)}
.sector-modal .st-metrics .t{width:100%;font-size:10px;color:var(--dim);font-style:normal;opacity:.8}

/* Top 贡献股 */
.sector-modal .top-contrib{margin-top:9px;padding-top:7px;border-top:0.5px dashed var(--line)}
.sector-modal .top-contrib .tc-hd{font-size:11px;color:var(--dim);margin-bottom:4px}
.sector-modal .top-contrib .tc-sub{margin-left:6px;font-size:10px;opacity:.75}
.sector-modal .tp-row{display:grid;grid-template-columns:minmax(0,1fr) 5.4em 4.6em auto;
  align-items:center;gap:7px;font-size:11px;line-height:1.9}
.sector-modal .tp-nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sector-modal .tp-d{text-align:right;font-family:var(--font-mono)}
.sector-modal .tp-d i{font-size:9px;opacity:.6;margin-left:1px;font-style:normal}
.sector-modal .tp-p{text-align:right;font-family:var(--font-mono);font-size:10.5px;opacity:.85}
/* 驱动徽标样式已统一到 board.js 的 STG_CSS（.sector-modal .stg），
 * 此处不再重复定义 .drv —— 两套 CSS 正是导致风格不统一的根源。 */
.sector-modal .card-right{overflow-x:auto;max-height:460px;overflow-y:auto}
.sector-modal .card-tbl{width:100%;border-collapse:collapse;font-family:var(--font-ui);font-size:12px;min-width:560px}
.sector-modal .card-tbl th{text-align:center;font-weight:400;color:var(--dim);padding:2px 4px;border-bottom:0.5px solid var(--line);font-size:11px;position:sticky;top:0;background:var(--card);z-index:1}
.sector-modal .card-tbl td{padding:3px 4px;border-bottom:0.5px solid var(--line-soft);font-variant-numeric:tabular-nums;text-align:center}
.sector-modal .card-tbl td.num{text-align:center;font-family:var(--font-mono)}
.sector-modal .card-tbl td.sec-rank{text-align:center}
/* 2026-11-01：序号列已撤（主人去序号/名次/评分），第一列是股票 ⇒ 左对齐 + 最小宽，
   否则沿用旧的「首列 34px（序号）」会把股票名挤成竖排。 */
.sector-modal .card-tbl th:first-child,
.sector-modal .card-tbl td:first-child{text-align:left;min-width:104px}
.sector-modal .card-tbl tr:last-child td{border-bottom:none}
.sector-modal .card-tbl tr:hover{background:var(--card2)}
.sector-modal .card-tbl th.sortable{cursor:pointer;user-select:none;white-space:nowrap}
.sector-modal .card-tbl th.sortable:hover{color:var(--accent)}
.sector-modal .card-tbl th.sortable.on{color:var(--accent);font-weight:600}
.sector-modal .card-tbl .pos{color:var(--up-ink)}
.sector-modal .card-tbl .neg{color:var(--down-ink)}
.sector-modal .card-tbl .flat{color:var(--dim)}
.sector-modal .card-tbl .dk{display:inline-flex;align-items:center;justify-content:center;min-width:15px;height:15px;padding:0 3px;margin-right:4px;border-radius:2px;font-size:10px;line-height:1;vertical-align:middle}
.sector-modal .card-tbl .dk.bull{background:var(--up-bg);color:var(--up-solid)}
.sector-modal .card-tbl .dk.bear{background:var(--down-bg);color:var(--down-solid)}
.sector-modal .card-tbl .dk.bull-half{background:transparent;color:var(--up-solid);border:0.5px dashed color-mix(in srgb,var(--up-solid) 45%,transparent)}
.sector-modal .card-tbl .dk.bear-half{background:transparent;color:var(--down-solid);border:0.5px dashed color-mix(in srgb,var(--down-solid) 45%,transparent)}
.sector-modal .card-tbl .dk-empty{background:transparent;color:transparent;border:0}
.sector-modal .card-tbl .dkslot{display:inline-block;width:19px}
.sector-modal .card-tbl td .bd{display:inline-flex;align-items:center;justify-content:center;margin-left:5px;padding:0 4px;min-width:15px;height:15px;border-radius:2px;font-size:10px;line-height:1;vertical-align:middle;letter-spacing:0;border:0.5px solid var(--line);color:var(--dim);background:transparent}
.sector-modal .card-tbl .sg{display:inline-flex;align-items:center;justify-content:center;min-width:15px;height:15px;padding:0 3px;margin-left:3px;border:1px solid;border-radius:2px;font-size:10px;line-height:1;vertical-align:middle;font-weight:600}
.sector-modal .card-tbl .sg-w{background:color-mix(in srgb,var(--fg) 4%,transparent);color:var(--dim);border-color:var(--line)}
.sector-modal .card-tbl .sg-y{background:color-mix(in srgb,var(--warn-solid) 16%,transparent);color:var(--warn-ink);border-color:color-mix(in srgb,var(--warn-solid) 50%,transparent)}
.sector-modal .card-tbl .sg-q{background:color-mix(in srgb,var(--p-cyan-300) 16%,transparent);color:var(--p-cyan-300);border-color:color-mix(in srgb,var(--p-cyan-300) 55%,transparent)}
.sector-modal .card-tbl .sg-r{background:var(--up-bg);color:var(--up-ink);border-color:color-mix(in srgb,var(--up-solid) 55%,transparent)}
.sector-modal .card-tbl .sg-b2,.sector-modal .card-tbl .sg-b3,.sector-modal .card-tbl .sg-b4{background:color-mix(in srgb,var(--accent) 12%,transparent);color:var(--accent-hover);border-color:color-mix(in srgb,var(--accent) 45%,transparent)}
.sector-modal .card-tbl .sg-mo{background:color-mix(in srgb,var(--p-purple-300) 16%,transparent);color:var(--p-purple-300);border-color:color-mix(in srgb,var(--p-purple-300) 55%,transparent)}
.sector-modal .card-tbl .sg-wk{background:color-mix(in srgb,var(--ochre) 14%,transparent);color:var(--ochre);border-color:color-mix(in srgb,var(--ochre) 45%,transparent)}
.sector-modal .card-tbl .sg-b1{background:transparent;color:inherit;border:1px dashed currentColor}
.sector-modal .card-tbl .sg-zhu-w{background:transparent;color:inherit;border:1px dashed currentColor}
.sector-modal .card-tbl .sg-zhu-p{background:color-mix(in srgb,var(--p-purple-500) 16%,transparent);color:var(--p-purple-300);border-color:var(--p-purple-500)}
.sector-modal .card-tbl .sg-zhu-o{background:color-mix(in srgb,var(--mf-orange) 16%,transparent);color:var(--mf-orange);border-color:var(--mf-orange)}
.sector-modal .stk-click{cursor:pointer;border-bottom:0.5px dashed currentColor}
.sector-modal .stk-click:hover{opacity:0.75}
/* 个股打分列（2026-10-29）：分数 + 评级；颜色一律主题 token（与 score 自己的榜单同色位） */
.sector-modal .card-tbl td.ds-score{white-space:nowrap;font-family:var(--font-mono)}
.sector-modal .card-tbl td.ds-score b{font-weight:600;color:var(--fg-strong)}
.sector-modal .card-tbl td.ds-score .ds-g{margin-left:3px;font-style:normal;font-size:10px;font-weight:600}
.sector-modal .card-tbl .ds-g.g-S{color:var(--up)}
.sector-modal .card-tbl .ds-g.g-A{color:var(--accent)}
.sector-modal .card-tbl .ds-g.g-B{color:var(--fg)}
.sector-modal .card-tbl .ds-g.g-C{color:var(--dim)}
.sector-modal .card-tbl .ds-g.g-D{color:var(--down)}
.sector-modal .card-sub-score{margin-left:8px;font-size:10px;font-weight:400;color:var(--dim)}
.sector-modal .card-sub-score.is-stale{color:var(--warn-ink)}
.sector-modal .card-sub-score.is-off{color:var(--dim);opacity:.8}
.sector-modal .rb-empty{font-family:var(--font-ui);font-size:12px;color:var(--dim);padding:8px 0}
.sector-modal .loading{font-family:var(--font-ui);font-size:12px;color:var(--dim);padding:10px 2px}
.sector-modal .err{color:var(--up-solid)}
@media (max-width:900px){.sector-modal .card-body{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){.sector-modal{padding:10px}.sector-modal .modal{max-height:92vh;padding:10px 12px 12px}}

/* ============================ 窄屏兜底（8002 断点 1100 / 900 / 700） ============================ */
@media (max-width:1100px){
  .sector-card .row-hd,.sector-card .col-hd{grid-template-columns:32px 26px minmax(0,1fr) 42px 54px 54px 54px 62px 46px 12px}
  .sector-card .row-hd .weight,.sector-card .col-hd .ch-wt{display:none}
  .sector-card .col-hd .ch-dl{grid-column:5}
  .sector-card .col-hd .ch-ap{grid-column:6}
  .sector-card .col-hd .ch-rl{grid-column:7}
  .sector-card .col-hd .ch-mn{grid-column:8}
  .sector-card .col-hd .th-hits{grid-column:9}
}
@media (max-width:900px){
  .sector-card .panels{grid-template-columns:minmax(0,1fr)}
  .sector-card .controls .ctrl-row{flex-wrap:wrap;row-gap:4px}
}
@media (max-width:760px){.sector-card .viz-grids{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){
  .sector-card .row-hd,.sector-card .col-hd{grid-template-columns:30px 22px minmax(0,1fr) 54px 54px 62px 12px}
  .sector-card .hits,.sector-card .th-hits,.sector-card .score,.sector-card .ch-sc{display:none}
  .sector-card .row-hd .delta,.sector-card .col-hd .ch-dl{display:none}
  .sector-card .col-hd .ch-ap{grid-column:4}
  .sector-card .col-hd .ch-rl{grid-column:5}
  .sector-card .col-hd .ch-mn{grid-column:6}
}
`;

export function injectCardCss(doc) {
  if (doc.getElementById('sector-legacy-css')) return;
  const s = doc.createElement('style');
  s.id = 'sector-legacy-css';
  s.textContent = CARD_CSS;
  doc.head.appendChild(s);
}
