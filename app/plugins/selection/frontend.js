// ============================================================================
// 选股卡 · 前端（v0.1.0，docs/task_R2_selection.md）
// ----------------------------------------------------------------------------
// 行为基准 = 旧端 engine/web/index.html 的 renderSelection 区（8810 零拷贝挂载于 /legacy/）：
//   · 6142-6200  selFApply / selFInit / selFSave / selPrefsLoad   筛选偏好读写口径
//   · 6213-6258  selPassFilter   股票池 / ST / KDJ与均线 / 单针 / 板块 / 热门（并集）判定
//   · 6219-6237  selBoardOf      板块归属前缀规则（60/00、30、688、92）
//   · 6267-6277  selHotTitle     热门板块口径文案（L2 + 近 30 交易日评分/在榜天数）
//   · 6676-6770  selVal / selHitBadges / selSectorCell / selDefenseCell / selNumCell
//                / selRowHtml    结果表 9 列 + 行尾 ★ 池按钮
//   · 6894-7020  renderSelection 表头 / 排序 / 空态原因 / 快照告警 / 脚注
//   · 6430-6492  selPoolRender   选股池面板（收盘日快照切换 + 行尾 ×）
//
// 契约（docs/selection_contract.md §3 + docs/CONTRACT.md §5）：
//   1. 原生 ES module，零 import、零构建；只操作自己的 root，不写全局变量。
//   2. 读走 ctx.api.get、写一律 ctx.api.post；四个端点都是 **根路径** /api/selection*
//      （由 compat_misc 以 mount=root 提供），所以路径写绝对形式，不拼插件前缀。
//   3. 颜色一律 themes.css 语义 token，本文件零裸 hex。
//   4. 大载荷纪律：/api/selection 是 8.2MB、2968 行的盘后快照。固定行高 32px + 滚动窗口
//      渲染（DOM 只留可视区 ±6 行），排序/过滤在内存数组上做，走势 SVG 只为可见行生成。
//      首屏完成后在 root 上写 data-paint-ms（供性能实测取值，不写全局）。
//   5. 写操作：筛选偏好 300ms 防抖 → POST → 回读核对；池增删用**行内二次点击**
//      （第一次点亮「确认…」，4s 内第二次才 POST，同 paper 卡 data-arm 模式）；
//      写成功后以服务端回执为准重绘，失败回执原样渲染，不重试。
//   6. 不声明 refreshEvery：数据是 T-1 盘后快照，只有「刷新」按钮与写后重拉。
// ============================================================================

const STYLE_ID = 'selection-card-css';
const ROW_H = 32;              // 固定行高：虚拟滚动的唯一几何常量
const BUFFER = 6;              // 可视区上下各多渲染的行数
const TOAST_MS = 3200;
const ARM_MS = 4000;           // 行内二次点击的有效窗口（同 paper 卡）
const SAVE_DEBOUNCE_MS = 300;
const LS_MCAP = 'sel_mcap_v2';
// 选股页模式（2026-11-11）：'stock' 个股 / 'etf' ETF。持久化，下次打开保持上次模式。
const LS_MODE = 'sel_mode_v1';
// 盘中现算（2026-11-04）：轮询产物落盘 + 硬超时（全市场重算实测 2~3 分钟，10 分钟是网）
const LIVE_POLL_MS = 5000;
const LIVE_TIMEOUT_MS = 10 * 60 * 1000;
// 盘中现算的刷新范围（2026-11-12 主人：不必全刷，右键菜单勾选要刷的部分）。
//   只列真会出票的 7 组；池内买点/五日线/共振 是空占位组（后端 SELECTION_GROUPS 同口径），
//   没有信号可算，列出来只会让人以为“能刷”。
const LIVE_GROUPS = ['B1', 'B2', 'B3', '单针', '动能', '砖形图', '周B1'];
const LS_LIVE = 'sel_live_scope_v1';
/** 读上次的刷新范围（localStorage 不可用/坏值 → 全选）。 */
const readLiveScope = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_LIVE) || 'null');
    if (Array.isArray(raw)) {
      const keep = raw.filter((x) => LIVE_GROUPS.indexOf(x) >= 0);
      if (keep.length) return keep;
    }
  } catch (e) { /* 无 localStorage → 用默认 */ }
  return LIVE_GROUPS.slice();
};

// 股票池五档（旧 6130-6136 SEL_POOLS 逐字）
const POOLS = [
  { key: 'mainboard', label: '主板', short: '主', hint: '沪深主板（60/00 开头）' },
  { key: 'chinext', label: '创业板', short: '创', hint: '深市创业板（30 开头）' },
  { key: 'star', label: '科创板', short: '科', hint: '沪市科创板（688 开头）' },
  { key: 'bse', label: '北交所', short: '北', hint: '北交所（92 开头）' },
  { key: 'st', label: '含ST', short: 'ST', hint: '必须同时选板块；不选则剔除 ST/*ST' },
];
// 板块多空徽标配色（旧 5544 WATCH_REGIME_CLS）
const REGIME_CLS = { 多: 'up', 偏多: 'warn', 空: 'down', 偏空: 'cool' };
// 表头列（旧 6990-6996：标的 / 板块 / 涨幅 / 量比 / MAV涨跌 / J / 流通市值 / 主力净额 / 走势 / 距防线 + 操作）
// 2026-10-29 插入「打分」列：是 score 插件 /api/p/score/map 的只读投影，本插件不重算。
const COLS = [
  { key: '', label: '标的', cls: 'c-nm' },
  { key: '', label: '板块', cls: 'c-sec' },
  { key: 'pct', label: '涨幅' },
  { key: 'vol_ratio', label: '量比' },
  { key: 'mav_pct', label: 'MAV涨跌' },
  { key: 'j', label: 'J' },
  { key: 'dscore', label: '打分', cls: 'c-ds',
    tip: '个股打分（六维加权 0-100）+ 评级 S/A/B/C/D · 日频收盘口径，盘中不更新' },
  { key: 'mcap_yi', label: '流通市值' },
  { key: 'flow_yi', label: '主力净额' },
  { key: '', label: '走势', cls: 'c-sp' },
  { key: 'defense_pct', label: '距防线', cls: 'c-df' },
  { key: '', label: '' },
];

// ETF 模式表头（2026-11-11 主人：主板买不了、实际只能买 ETF，选股页要能切到 ETF）。
// 列与个股完全不同（无市值/无概念/无买点）：分类 + 多周期涨幅 + 距均线 + 流动性 + 量比 + 尾盘占比。
// 全部字段都是 `etf_snapshot` 快照的直接映射（后端 build_selection_etf 只换单位），前端零重算。
const COLS_ETF = [
  { key: '', label: '标的', cls: 'c-nm' },
  { key: '', label: '分类', cls: 'c-cat' },
  { key: 'pct', label: '涨幅' },
  { key: 'pct5', label: '5日' },
  { key: 'pct20', label: '20日' },
  { key: 'above_ma20', label: '距MA20', cls: 'c-ma' },
  { key: 'amount_yi', label: '成交额(亿)', cls: 'c-amt' },
  { key: 'ratio', label: '量比' },
  { key: 'tail30', label: '尾盘占比' },
  { key: '', label: '' },
];
// ETF 分类默认档（后端 cats 是动态的；这里只给「全部」的固定键）
const ETF_ALL = '全部';

/** 读上次的模式（localStorage 不可用/坏值 → 个股，不影响主路径）。 */
function readMode() {
  try {
    return localStorage.getItem(LS_MODE) === 'etf' ? 'etf' : 'stock';
  } catch (e) {
    return 'stock';
  }
}

const CSS = [
  '.sel-card{display:flex;flex-direction:column;gap:10px;color:var(--fg);min-width:0}',
  '.sel-top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}',
  '.sel-title{font-size:1rem;font-weight:600;color:var(--fg-strong)}',
  '.sel-badge{display:inline-block;padding:1px 9px;border-radius:var(--radius-pill);background:var(--card2);color:var(--dim);font-size:0.72rem;white-space:nowrap}',
  '.sel-badge.ok{background:var(--down-bg);color:var(--down-ink,var(--down))}',
  '.sel-badge.warn{background:var(--warn-bg);color:var(--warn-ink,var(--warn))}',
  '.sel-badge.bad{background:var(--danger-bg);color:var(--danger-ink,var(--danger))}',
  '.sel-sub{color:var(--dim);font-size:0.72rem;font-variant-numeric:tabular-nums}',
  '.sel-grow{flex:1 1 auto}',
  '.sel-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer;white-space:nowrap}',
  '.sel-btn:hover{background:var(--card2)}',
  '.sel-btn.primary{border-color:var(--accent);color:var(--accent)}',
  '.sel-btn.primary:hover{background:var(--accent-bg)}',
  '.sel-btn.danger{border-color:var(--danger);color:var(--danger-ink,var(--danger))}',
  '.sel-btn.danger:hover{background:var(--danger-bg)}',
  '.sel-btn:disabled{opacity:.45;cursor:not-allowed}',
  '.sel-btn.is-live{border-color:var(--accent);color:var(--accent);animation:selPulse 1.2s ease-in-out infinite}',
  '@keyframes selPulse{0%,100%{opacity:1}50%{opacity:.45}}',
  '.sel-live{color:var(--accent);font-weight:600}',
  '.sel-menu{position:fixed;z-index:70;background:var(--card);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);box-shadow:0 8px 24px rgba(0,0,0,.35);padding:6px;min-width:176px;font-size:0.74rem}',
  '.sel-menu-h{padding:4px 8px 6px;color:var(--dim);font-size:0.68rem}',
  '.sel-menu-i{display:flex;align-items:center;gap:7px;padding:5px 8px;border-radius:6px;cursor:pointer;color:var(--fg)}',
  '.sel-menu-i:hover{background:var(--card2)}',
  '.sel-menu-i input{accent-color:var(--accent);margin:0}',
  '.sel-menu-f{display:flex;gap:6px;margin-top:6px;padding-top:6px;border-top:1px solid var(--line-soft)}',
  '.sel-menu-f .sel-btn{flex:1;text-align:center}',
  '.sel-err{padding:6px 9px;border-radius:var(--radius-sm);background:var(--danger-bg);color:var(--danger-ink,var(--danger));font-size:0.75rem;line-height:1.55;word-break:break-word}',
  '.sel-warn{padding:6px 9px;border-radius:var(--radius-sm);background:var(--warn-bg);color:var(--warn-ink,var(--warn));font-size:0.75rem;line-height:1.55}',
  '.sel-okbar{padding:6px 9px;border-radius:var(--radius-sm);background:var(--down-bg);color:var(--down-ink,var(--down));font-size:0.75rem;line-height:1.55;word-break:break-word}',
  '.sel-tabs{display:flex;gap:2px;flex-wrap:wrap;border-bottom:1px solid var(--line-soft)}',
  '.sel-tab{border:0;background:transparent;color:var(--dim);font-family:inherit;font-size:0.78rem;padding:4px 9px;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap}',
  '.sel-tab:hover{color:var(--fg);background:var(--card2)}',
  '.sel-tab.on{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}',
  '.sel-tab .n{font-size:0.66rem;margin-left:3px;opacity:.75}',
  '.sel-tab.part{border-bottom-style:dashed}',
  '.sel-fbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center}',
  '.sel-chip{display:inline-flex;align-items:center;gap:5px;border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);color:var(--dim);border-radius:var(--radius-pill);padding:2px 9px;font-family:inherit;font-size:0.7rem;cursor:pointer;white-space:nowrap}',
  '.sel-chip:hover{border-color:var(--accent);color:var(--accent)}',
  '.sel-chip.on{border-color:var(--accent);background:var(--accent-bg);color:var(--accent);font-weight:600}',
  '.sel-chip b{color:var(--fg);font-weight:600}',
  '.sel-chip .u{opacity:.7}',
  '.sel-inp{width:58px;background:var(--card);color:var(--fg);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:1px 5px;font-family:inherit;font-size:0.7rem;outline:none;font-variant-numeric:tabular-nums}',
  '.sel-inp:focus{border-color:var(--accent)}',
  '.sel-mini{border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);color:var(--dim);border-radius:var(--radius-sm);padding:0 7px;font-family:inherit;font-size:0.68rem;cursor:pointer}',
  '.sel-mini:hover{border-color:var(--accent);color:var(--accent)}',
  '.sel-panel{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card);padding:8px 10px;font-size:0.74rem;line-height:1.6}',
  '.sel-panel-hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:6px}',
  '.sel-panel-hd b{color:var(--fg-strong);font-size:0.78rem;font-weight:600}',
  '.sel-panel-hd .n{margin-left:auto;font-size:0.66rem;color:var(--dim)}',
  '.sel-hint{font-size:0.68rem;color:var(--dim);line-height:1.6;margin:4px 0}',
  '.sel-opts{display:flex;gap:12px;flex-wrap:wrap;margin:5px 0}',
  '.sel-opt{display:inline-flex;align-items:center;gap:5px;cursor:pointer;white-space:nowrap}',
  '.sel-opt input{accent-color:var(--accent)}',
  '.sel-frow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:5px 0}',
  '.sel-frow label{color:var(--dim);font-size:0.7rem;flex:0 0 auto}',
  '.sel-frow input[type=number]{width:78px;background:var(--card2);color:var(--fg);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:2px 7px;font-family:inherit;font-size:0.74rem;outline:none;font-variant-numeric:tabular-nums}',
  '.sel-frow input[type=text]{flex:1 1 180px;min-width:140px;background:var(--card2);color:var(--fg);border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);padding:2px 7px;font-family:inherit;font-size:0.74rem;outline:none}',
  '.sel-blist{display:flex;gap:6px;flex-wrap:wrap;max-height:150px;overflow:auto;margin-top:4px}',
  '.sel-brow{display:inline-flex;align-items:center;gap:4px;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-pill);padding:1px 8px;font-size:0.7rem}',
  '.sel-brow .x{border:0;background:transparent;color:var(--dim);cursor:pointer;font-family:inherit;font-size:0.72rem;padding:0 1px}',
  '.sel-brow .x:hover{color:var(--up)}',
  '.sel-brow .c{color:var(--dim);font-size:0.64rem}',
  '.sel-slots{display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin:4px 0 6px}',
  '.sel-slot{border:var(--border-w) var(--border-style) var(--border-color);background:var(--card2);color:var(--dim);border-radius:var(--radius-pill);padding:1px 8px;font-family:inherit;font-size:0.68rem;cursor:pointer;font-variant-numeric:tabular-nums}',
  '.sel-slot.on{border-color:var(--accent);color:var(--accent);background:var(--accent-bg);font-weight:600}',
  '.sel-slot i{margin-left:3px;opacity:.7;font-style:normal}',
  '.sel-plist{display:flex;flex-direction:column;gap:2px;max-height:230px;overflow:auto}',
  '.sel-pitem{display:grid;grid-template-columns:minmax(90px,1.4fr) 72px 78px 108px 30px;align-items:center;gap:6px;font-size:0.74rem;padding:2px 4px;border-bottom:1px solid var(--line-soft)}',
  '.sel-pitem .nm{color:var(--fg-strong)}',
  '.sel-pitem .mut{color:var(--dim);font-size:0.68rem;font-variant-numeric:tabular-nums}',
  '.sel-stat{font-size:0.7rem;color:var(--dim);font-variant-numeric:tabular-nums}',
  '.sel-stat b{color:var(--fg-strong);font-weight:600}',
  '.sel-tablewrap{overflow-x:auto;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card)}',
  // 11 条轨 = COLS 的 11 列（名/板块/涨幅/量比/MAV/J/市值/主力/走势/距防线/星标）。
  // 名称与板块列给上限（长名省略，不无限吃掉宽度），星标列 1fr 吸剩余宽度 → 星标贴右。
  // 实测 1440 视口下原名称列 450px、数字列只剩 46-72px，改后数值列宽度固定可读。
  '.sel-grid{display:grid;grid-template-columns:minmax(150px,320px) minmax(104px,220px) 70px 62px 84px 52px 64px 80px 78px 104px 152px 1fr;align-items:center;min-width:1004px}',
  // ETF 模式（2026-11-11）：10 列，列宽与 COLS_ETF 一一对应（改列必同步改这里）
  '.sel-grid.is-etf{grid-template-columns:minmax(150px,300px) minmax(96px,170px) 64px 64px 66px 78px 86px 62px 74px 1fr;min-width:860px}',
  '.sel-modewrap{display:inline-flex;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-pill);overflow:hidden}',
  '.sel-modebtn{border:0;background:transparent;color:var(--dim);font-family:inherit;font-size:0.72rem;padding:2px 10px;cursor:pointer;white-space:nowrap}',
  '.sel-modebtn:hover{background:var(--card2);color:var(--fg)}',
  '.sel-modebtn.on{background:var(--accent-bg);color:var(--accent);font-weight:600}',
  // 打分列（2026-10-29）：分数 + 评级，颜色一律主题 token
  '.sel-head .h.c-ds{text-align:right}',
  '.sel-r .c.c-ds{font-variant-numeric:tabular-nums}',
  '.sel-card .c-ds b{font-weight:600;color:var(--fg-strong)}',
  '.sel-card .c-ds .sel-dsg{margin-left:3px;font-style:normal;font-size:0.62rem;font-weight:600}',
  '.sel-card .sel-dsg.g-S{color:var(--up)}',
  '.sel-card .sel-dsg.g-A{color:var(--accent)}',
  '.sel-card .sel-dsg.g-B{color:var(--fg)}',
  '.sel-card .sel-dsg.g-C{color:var(--dim)}',
  '.sel-card .sel-dsg.g-D{color:var(--down)}',
  '.sel-card .sel-off{color:var(--warn-ink,var(--warn))}',
'.sel-nm.is-link{cursor:pointer}',
'.sel-nm.is-link:hover{color:var(--accent);text-decoration:underline;text-underline-offset:2px}',
  '.sel-head{background:var(--card2);border-bottom:1px solid var(--border-color)}',
  '.sel-head .h{font-size:0.68rem;color:var(--dim);font-weight:400;padding:5px 6px;white-space:nowrap}',
  '.sel-head .h.num{text-align:right}',
  // 「距防线」单元格内容是「线种 + 百分比 + 新破小标」左起的行内流，表头不能右对齐（否则对不齐）
  '.sel-head .h.c-df{text-align:left}',
  '.sel-head .h.sortable{cursor:pointer}',
  '.sel-head .h.sortable:hover{color:var(--accent)}',
  '.sel-head .h.sorted{color:var(--accent);font-weight:600}',
  '.sel-vp{overflow-y:auto;overflow-x:hidden;max-height:58vh;min-height:220px;position:relative}',
  '.sel-rows{position:relative}',
  '.sel-r{position:absolute;left:0;right:0;height:32px;border-bottom:1px solid var(--line-soft);font-size:0.74rem;font-variant-numeric:tabular-nums}',
  '.sel-r:hover{background:var(--card2)}',
  '.sel-r.veto{opacity:.55}',
  '.sel-r .c{padding:0 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  '.sel-r .c.num{text-align:right}',
  '.sel-card .up{color:var(--up)}',
  '.sel-card .down{color:var(--down)}',
  '.sel-card .mut{color:var(--dim)}',
  '.sel-sg{display:inline-flex;align-items:center;justify-content:center;min-width:15px;height:15px;padding:0 3px;border-radius:3px;border:1px solid currentColor;font-size:0.6rem;line-height:1;margin-left:3px;vertical-align:middle}',
  '.sel-sg.y{color:var(--warn-ink,var(--warn));background:var(--warn-bg)}',
  '.sel-sg.w{color:var(--dim);background:var(--card2)}',
  '.sel-sg.q{color:var(--accent);background:var(--accent-bg)}',
  '.sel-sg.r{color:var(--up);background:var(--up-bg)}',
  '.sel-sg.mo{color:var(--accent);background:var(--accent-bg)}',
  '.sel-sg.wk{color:var(--dim);background:var(--card2)}',
  '.sel-sg.p{color:var(--down);background:var(--down-bg)}',
  '.sel-dk{display:inline-block;padding:0 4px;border-radius:3px;border:1px solid currentColor;font-size:0.6rem;line-height:1.4;margin-left:3px}',
  '.sel-dk.up{color:var(--up);background:var(--up-bg)}',
  '.sel-dk.down{color:var(--down);background:var(--down-bg)}',
  '.sel-dk.warn{color:var(--warn);background:var(--warn-bg)}',
  '.sel-dk.cool{color:var(--dim);background:var(--card2)}',
  '.sel-line{display:inline-block;padding:0 4px;border-radius:3px;border:var(--border-w) var(--border-style) var(--border-color);font-size:0.62rem;color:var(--dim)}',
  '.sel-spk{display:block;width:96px;height:24px}',
  '.sel-spk.up{color:var(--up)}',
  '.sel-spk.down{color:var(--down)}',
  '.sel-spk polyline{fill:none;stroke-linejoin:round;stroke-linecap:round}',
  '.sel-spk .spc{stroke:currentColor;stroke-width:1.3}',
  // 2026-11-03（主人）：走势按**多空区间**逐段染色 —— 白线在黄线上且收盘在黄线上=多头（红），否则空头（绿），
  //   黄/白线缺值（次新或窗口不够）→ 中性灰，不冒充多空。
  '.sel-spk .spc.up{stroke:var(--up)}',
  '.sel-spk .spc.down{stroke:var(--down)}',
  '.sel-spk .spc.na{stroke:var(--dim)}',
  '.sel-spk circle.up{fill:var(--up)}',
  '.sel-spk circle.down{fill:var(--down)}',
  '.sel-spk circle.na{fill:var(--dim)}',
  '.sel-spk .spy{stroke:var(--warn);stroke-width:1;opacity:.9}',
  '.sel-spk .spw{stroke:var(--fg);stroke-width:1;opacity:.5}',
  '.sel-spk circle{fill:currentColor}',
  '.sel-star{border:0;background:transparent;color:var(--dim);cursor:pointer;font-family:inherit;font-size:0.86rem;line-height:1;padding:0 4px}',
  '.sel-r .c.star{display:flex;justify-content:flex-end;padding-right:8px}',
  '.sel-star:hover{color:var(--accent)}',
  '.sel-star.on{color:var(--accent)}',
  '.sel-star.armed{color:var(--warn-ink,var(--warn));font-size:0.66rem;font-weight:600}',
  '.sel-lg{display:flex;gap:12px;flex-wrap:wrap;font-size:0.68rem;color:var(--dim);align-items:center}',
  '.sel-lg i{display:inline-block;width:12px;height:2px;margin-right:4px;vertical-align:middle}',
  '.sel-lg .l1{background:var(--fg)}',
  '.sel-lg .l2{background:var(--warn)}',
  '.sel-lg .l3{background:var(--fg);opacity:.5}',
  '.sel-src{font-size:0.66rem;color:var(--dim);line-height:1.65}',
  '.sel-toast{align-self:flex-start;font-size:0.72rem;padding:4px 10px;border-radius:var(--radius-sm);background:var(--accent-bg);color:var(--accent-ink,var(--accent));border:1px solid var(--accent)}',
  '@media (max-width:720px){.sel-vp{max-height:48vh}}',
].join('\n');

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------- 工具（口径照旧端）
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const isNum = (v) => v != null && v !== '' && isFinite(Number(v));
const num = (v, d) => (isNum(v) ? Number(v).toFixed(d == null ? 2 : d) : '—');
const pct = (v) => (isNum(v) ? (Number(v) > 0 ? '+' : '') + Number(v).toFixed(2) + '%' : '—');
const clsOf = (v) => (Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : ''));
const errText = (e) => (e && e.message ? e.message : String(e));
const now = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now());
const hhmmss = () => {
  const d = new Date();
  const p = (n) => (n < 10 ? '0' + n : String(n));
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
};

// 板块归属（旧 6203-6209 selBoardOf，前缀规则与投资科学一致）
function boardOf(code6) {
  const c = String(code6 || '');
  if (/^(60\d{4}|00\d{4})$/.test(c)) return 'mainboard';
  if (/^30\d{4}$/.test(c)) return 'chinext';
  if (/^688\d{3}$/.test(c)) return 'star';
  if (/^92\d{4}$/.test(c)) return 'bse';
  return null;
}
// ST 判定（旧 6211 selIsST）
function isST(name) {
  return /^\*?ST/i.test(String(name || '').replace(/\s+/g, ''));
}
// 排序取值：距防线按带符号距离（旧 6676-6681 selVal）；打分读只读投影挂的 _ds
function sortVal(r, key, S) {
  if (key === 'defense_pct') return r.defense ? r.defense.distance_pct : null;
  if (key === 'dscore') return r._ds;
  // 涨幅/量比两列盘中按实时值排序，与屏幕上看到的数字同源（2026-11-13）
  if (key === 'pct' && S && S.livePct && S.livePct[r.code]) return S.livePct[r.code].pct;
  if (key === 'vol_ratio' && S && S.livePct && S.livePct[r.code]
      && S.livePct[r.code].vr != null) return S.livePct[r.code].vr;
  return r[key];
}

/** 个股打分文案（悬浮提示用；数据时效写死在这里，避免「昨天的分当实时」）。 */
function dsTip(S, code6) {
  const sc = S.score || {};
  if (sc.state !== 'ok') return '无打分数据' + (sc.reason ? '：' + sc.reason : '');
  const hit = (sc.map || {})[code6];
  const head = '打分 ' + (sc.asof || '—') + ' 收盘口径（日频，盘中不更新）';
  if (!hit) return '不在打分池 · ' + head;
  return '个股打分 ' + Number(hit[0]).toFixed(1) + (hit[1] ? ' · 评级 ' + hit[1] : '') + ' · ' + head;
}

/** 打分单元格：分数 1 位小数 + 评级色字；不在打分池 → 「—」（不写 0）。 */
function dsCell(r) {
  if (r._ds == null) return '—';
  return '<b>' + Number(r._ds).toFixed(1) + '</b>'
    + (r._dg ? '<i class="sel-dsg g-' + esc(r._dg) + '">' + esc(r._dg) + '</i>' : '');
}

/** score 插件的 `/map` 响应 → 本卡内部状态（永不抛：404/缺产物都归到 state:'none'）。 */
function readScore(j) {
  if (j && !j.__error && j.available && j.map) {
    return { state: 'ok', map: j.map, asof: j.asof || null, stale: !!j.stale, reason: '' };
  }
  const err = j && j.__error;
  const reason = err
    ? (j.__status === 404 ? '未装载 score 插件（/api/p/score/map 404）' : String(err))
    : ((j && j.reason) || '打分数据不可用');
  return { state: 'none', map: {}, asof: null, stale: false, reason };
}

/** 把打分挂到快照行上（`_ds` 分数 / `_dg` 评级）：排序与渲染共用一份值，不重算。 */
function attachScoreRows(d, sc) {
  for (const g of ((d && d.groups) || [])) {
    for (const r of (g.rows || [])) {
      const code6 = r.code6 || String(r.code || '').split('.')[0];
      const hit = (sc && sc.state === 'ok') ? sc.map[code6] : null;
      r._ds = (hit && hit[0] != null) ? hit[0] : null;
      r._dg = (hit && hit[1]) || null;
    }
  }
}
// 命中徽标：同 label 去重，本组战法优先（旧 6682-6690 selHitBadges）
function hitBadges(r) {
  const seen = [];
  const list = [];
  const push = (b) => {
    if (b && b.label && seen.indexOf(b.label) < 0) { seen.push(b.label); list.push(b); }
  };
  push(r.badge);
  (r.badges || []).forEach(push);
  if (!list.length) return '<span class="mut">—</span>';
  return list.map((b) => '<span class="sel-sg ' + esc(b.cls) + '" title="' + esc(b.desc || '')
    + '">' + esc(b.label) + '</span>').join('');
}
// 板块 + 多空徽标（旧 6691-6695 selSectorCell）
function sectorCell(sec) {
  if (!sec || !sec.name) return '<span class="mut">—</span>';
  const c = REGIME_CLS[sec.regime] || 'cool';
  return '<span title="' + esc(sec.code || '') + '">' + esc(sec.name) + '</span>'
    + (sec.regime ? ' <span class="sel-dk ' + c + '" title="板块多空（0AMV 工作台双轨口径）">'
      + esc(sec.regime) + '</span>' : '');
}
// 距防线（旧 6696-6715 selDefenseCell → 2026-11-01 决策表口径：rule 为选线依据，N型已破/rr 标记始终展示）
function defenseCell(r) {
  const d = r.defense;
  if (!d) return '<span class="mut">—</span>';
  const t = d.distance_pct;
  const lb = d.label || d.kind || '—';
  const be = d.broken_event || {};
  const evTags = (be.yellow ? '<span class="sel-dk down">黄线新破</span>' : '')
    + (be.ntype ? '<span class="sel-dk down">N型破位</span>' : '')
    + (!be.yellow && be.white ? '<span class="sel-dk down">白线新破</span>' : '');
  // 2026-11-01 主人定：「N型已破」标记**始终展示**；rr 三标记只标不剔
  const nb = d.ntype_broken ? '<span class="sel-dk down" title="收盘 < 最近已确认缠论笔低点（N 型结构已破，趋势大概率走坏）">N型已破</span>' : '';
  const marks = (d.rr_pass === false ? '<span class="sel-dk down" title="盈亏比低于门槛（B1 2.0 / B2 1.5，待主人确认），只作下限筛标记不剔除">rr未达</span>' : '')
    + (d.stop_invalid ? '<span class="sel-dk down" title="止损距入场过近（<1%，待主人确认），rr 无意义">止损过近</span>' : '')
    + (d.target_missing ? '<span class="mut" title="无前 N 型高点（止盈不可算）">无止盈</span>' : '');
  if (d.status === 'broken_all' || (t != null && Number(t) < 0)) {
    return '<span class="down" title="价在所有候选线下方，数字=距最近一条线的负距离">全破 '
      + pct(t) + '</span>' + nb + evTags;
  }
  const lines = (d.lines || []).map((x) => x.label + ' ' + pct(x.distance_pct)).join(' / ');
  return '<span class="sel-line">' + esc(lb) + '</span> <span class="up" title="'
    + esc(d.rule || '') + '（防线决策表 2026-11-01）'
    + (lines ? '；全部线：' + esc(lines) : '') + '">' + pct(t) + '</span>' + nb + evTags + marks;
}
function numCell(v, nd, colored) {
  if (v == null || v === '' || isNaN(v)) return '<span class="mut">—</span>';
  const s = (colored && Number(v) > 0 ? '+' : '') + Number(v).toFixed(nd);
  return '<span class="' + (colored ? clsOf(v) : '') + '">' + s + '</span>';
}
// 走势（旧 6054-6084 selSpark）：收盘+黄线+白线共用同一归一化区间，缺值不插值不补 0
function sparkSvg(r) {
  const s = r && r.spark;
  if (!s) return '<span class="mut">—</span>';
  const w = 92, h = 24, pad = 2;
  const cs = s.close || [], ys = s.yellow || [], ws = s.white || [];
  const ok = (v) => v != null && isFinite(v);
  const n = Math.max(cs.length, ys.length, ws.length);
  const all = [];
  for (let i = 0; i < n; i++) [cs[i], ys[i], ws[i]].forEach((v) => { if (ok(v)) all.push(v); });
  if (n < 2 || all.length < 2) return '<span class="mut">—</span>';
  const lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);
  const span = hi > lo ? hi - lo : 1e-9;
  const X = (i) => pad + (w - 2 * pad) * (i / (n - 1));
  const Y = (v) => pad + (h - 2 * pad) * (1 - (v - lo) / span);
  const poly = (arr, klass) => {
    const pts = [];
    for (let i = 0; i < arr.length && i < n; i++) {
      if (ok(arr[i])) pts.push(X(i).toFixed(1) + ',' + Y(arr[i]).toFixed(1));
    }
    return pts.length >= 2 ? '<polyline class="' + klass + '" points="' + pts.join(' ') + '"/>' : '';
  };
  const cl = [];
  for (let i = 0; i < cs.length && i < n; i++) if (ok(cs[i])) cl.push([i, cs[i]]);
  if (cl.length < 2) return '<span class="mut">—</span>';
  const trend = cl[cl.length - 1][1] >= cl[0][1] ? 'up' : 'down';
  const last = cl[cl.length - 1];
  // 2026-11-03（主人）：**多头区间画红、空头区间画绿** —— 判据：白线在黄线之上 ∧ 收盘在黄线之上。
  //   逐日判、相邻同色合并成段（段首接上一点，线不断）；黄/白线缺值 → 中性灰。
  const stateOf = (i) => {
    if (!ok(cs[i]) || !ok(ys[i]) || !ok(ws[i])) return 'na';
    return (ws[i] > ys[i] && cs[i] > ys[i]) ? 'up' : 'down';
  };
  const segs = [];
  for (let i = 0; i < n; i++) {
    if (!ok(cs[i])) continue;
    const cls = stateOf(i);
    const cur = segs[segs.length - 1];
    const pt = [X(i), Y(cs[i])];
    if (cur && cur.cls === cls) cur.pts.push(pt);
    else {
      const prev = (i > 0 && ok(cs[i - 1])) ? [X(i - 1), Y(cs[i - 1])] : null;
      segs.push({ cls, pts: prev ? [prev, pt] : [pt] });
    }
  }
  const segHtml = segs.map((sg) => (sg.pts.length >= 2
    ? '<polyline class="spc ' + sg.cls + '" points="' + sg.pts.map((q) => q[0].toFixed(1) + ',' + q[1].toFixed(1)).join(' ') + '"/>'
    : '')).join('');
  const segPct = cl[0][1] ? ((cl[cl.length - 1][1] / cl[0][1] - 1) * 100) : 0;
  const lastCls = stateOf(cl[cl.length - 1][0]);
  const spkTitle = '近30交易日走势：收盘 ' + (segPct >= 0 ? '+' : '') + segPct.toFixed(1) + '%'
    + '；红=多头（白线在黄线之上 且 收盘在黄线之上），绿=空头，灰=黄/白线缺值；橙=黄线，浅灰=白线';
  return '<svg class="sel-spk ' + trend + '" viewBox="0 0 ' + w + ' ' + h
    + '" preserveAspectRatio="none" role="img" aria-label="近30交易日走势">'
    + '<title>' + esc(spkTitle) + '</title>'
    + poly(ws, 'spw') + poly(ys, 'spy') + segHtml
    + '<circle class="' + lastCls + '" cx="' + X(last[0]).toFixed(1) + '" cy="' + Y(last[1]).toFixed(1) + '" r="1.8"/></svg>';
}
// 热门板块口径文案（旧 6267-6277 selHotTitle 逐字）
function hotTitle(d) {
  const h = (d && d.hot_concepts) || {};
  const rule = h.rule || {};
  const n = (h.boards || []).length;
  return '热门板块：二级行业（L2），近 ' + (h.window_days || 30) + ' 个交易日按「活跃市值 3 日涨幅」排名，'
    + '满足任一即算热门 —— ① 评分 ≥ ' + (rule.score_min != null ? rule.score_min : 66)
    + ' 分（每日分位得分均值，0-100）② 在榜 ≥ ' + (rule.days_min != null ? rule.days_min : 10)
    + ' 天（当日进前 ' + (rule.top_pct || 20) + '%）｜截至 ' + (h.asof || '—') + '，共 ' + n + ' 个板块';
}

// ---------------------------------------------------------------- 卡片
export default {
  cards: [{
    id: 'selection.main',
    title: '选股',
    slot: 'main',
    order: 55,

    async mount(root, ctx) {
      root.textContent = '';
      const t0 = now();
      const api = ctx.api;
      const S = {
        ctx, root, api,
        alive: true,
        loading: false,
        mode: readMode(),       // 'stock' | 'etf'（默认上次选择）
  liveScope: readLiveScope(),  // 盘中现算要刷新的组（右键菜单改，2026-11-12）
  liveMenu: null,
  liveOutside: null,
        etf: null,              // ETF 模式数据（懒加载：只切过去才拉）
        etfLoading: false,
        etfCat: ETF_ALL,        // ETF 分类筛选（宽基/行业主题/跨境）
        etfMin: 0.5,            // ETF 成交额下限（亿）：0.09 亿的殭尸 ETF 默认不刷屏
        etfSigOnly: '',        // '' | 'all' | 'b1'（2026-11-11 主人：要的就是买点信号）
        saving: false,
        submitting: false,
        data: null,
        prefs: null,
        pool: null,
        opts: null,
        group: null,
        view: [],          // 过滤+排序后的行（内存数组，虚拟滚动的数据源）
        // 2026-11-13（盘中涨幅在两日之间来回跳的根因修复）：实时涨幅进内存模型，
        // 渲染与排序都优先读它。旧实现只把实时值写进 DOM 单元格，虚拟滚动一重绘
        // （滚动/排序/切组/resize）就拿回盘后快照里的昨日涨幅，于是两值来回跳。
        livePct: Object.create(null),   // code → {pct,last,pre,asof}
        liveAt: '',
        poolSet: new Set(),
        hotSet: null,
        sort: { key: null, dir: 1 },
        score: { state: 'idle', map: {}, asof: null, stale: false, reason: '' },
        mcap: { min: '', max: '' },
        panel: '',
        scrollTop: 0,
        html: Object.create(null),
        toastTimer: null,
        armTimer: null,
        saveTimer: null,
        raf: 0,
        el: {},
      };
      this._sel = S;

      const div = (cls, text) => {
        const d = document.createElement('div');
        if (cls) d.className = cls;
        if (text != null) d.textContent = text;
        return d;
      };
      const btn = (cls, text, attrs) => {
        const b = document.createElement('button');
        b.type = 'button';
        if (cls) b.className = cls;
        b.textContent = text;
        if (attrs) Object.keys(attrs).forEach((k) => b.setAttribute(k, attrs[k]));
        return b;
      };

      // ---- 卡头 ----
      const wrap = div('sel-card');
      const top = div('sel-top');
      const title = div('sel-title', '选股');
      // 模式切换（2026-11-11 主人：主板买不了，实际只能买 ETF）——两套表和两套数据源，
      //   切换只换视图，互不影响：个股路径代码一行不动，ETF 走独立分支。
      const modeWrap = div('sel-modewrap');
      const btnModeStock = btn('sel-modebtn', '个股', { 'data-mode': 'stock' });
      const btnModeEtf = btn('sel-modebtn', 'ETF', { 'data-mode': 'etf' });
      modeWrap.append(btnModeStock, btnModeEtf);
      const badgeDate = div('sel-badge', '—');
      const badgeFresh = div('sel-badge', '快照 —');
      const badgeSrc = div('sel-badge', '—');
      const sub = div('sel-sub', '');
      const grow = div('sel-grow');
      const btnRefresh = btn('sel-btn', '刷新', { 'data-act': 'refresh' });
      // 盘中现算（2026-11-04 主人：尾盘选股）——触发全市场重算，不是重新拉一次盘后快照。
      // 两者分开：日常「刷新」秒回；「盘中现算」要 2~3 分钟，必须用户明确点。
      const btnLive = btn('sel-btn', '盘中现算', { 'data-act': 'live' });
      btnLive.title = '按当前行情重算全市场选股（含今日实时 bar），约 2~3 分钟；'
        + '产物不覆盖盘后快照，收盘后页面自动切回盘后定稿';
      const btnPool = btn('sel-chip', '★ 选股池', { 'data-chip': 'poolview' });
      const poolCount = document.createElement('b');
      poolCount.textContent = '0';
      btnPool.append(poolCount);
      top.append(title, modeWrap, badgeDate, badgeFresh, badgeSrc, grow, sub, btnPool, btnLive, btnRefresh);

      const status = div('sel-err');
      status.hidden = true;
      const note = div('sel-warn');
      note.hidden = true;
      const toast = div('sel-toast', '');
      toast.hidden = true;

      const tabs = div('sel-tabs');
      const fbar = div('sel-fbar');
      const panels = div('sel-panels');
      const stat = div('sel-stat', '');
      const head = div('sel-grid sel-head');
      const rows = div('sel-rows');
      const vp = div('sel-vp');
      vp.append(rows);
      const tableWrap = div('sel-tablewrap');
      tableWrap.append(head, vp);
      const legend = div('sel-lg');
      legend.innerHTML = '<span><i class="l1"></i>收盘</span><span><i class="l2"></i>黄线</span>'
        + '<span><i class="l3"></i>白线</span>'
        + '<span style="margin-left:auto">近 30 交易日 · 三条线共用同一量纲 · 缺值留空不插值</span>';
      const src = div('sel-src',
        '数据源：GET /api/selection（8.2MB 盘后快照，只读透传；本卡按固定行高 32px 窗口化渲染，DOM 只留可视行）'
        + ' · GET|POST /api/selection_prefs（筛选偏好）· GET /api/selection_pool（选股池 = 自选池的日期分组，只读）'
        + ' · POST /api/watchlist/add|remove（加入/移出，写自选池）'
        + ' · GET /api/selection_board_options（板块候选）。四个端点全部由 compat_misc 以 root 挂载提供，本插件零后端改动、零写盘。'
        + '口径：信号=盘后链 selectionsnap · 价=EOD 收盘（不取实时）· 距防线=价下方最近未破线（全破显负）· 多空与净额=amv_board.db'
        + ' · 归属=ths_board.db 二级。前端只做筛选与排序，不重算任何指标。');

      wrap.append(top, status, note, tabs, fbar, panels, stat, tableWrap, legend, toast, src);
      root.append(wrap);
      S.el = { top, title, modeWrap, btnModeStock, btnModeEtf,
        badgeDate, badgeFresh, badgeSrc, sub, btnPool, poolCount, btnLive,
        status, note, toast, tabs, fbar, panels, stat, head, rows, vp, tableWrap, legend, src };
      // 个股/ETF 两套底部说明文案（2026-11-11）：切模式时换回对应说明，不两混
      const SRC_STOCK_TEXT = src.textContent;
      const SRC_ETF_TEXT = '数据源：GET /api/selection_etf（只读透传 data/panel/etf_snapshot_latest.json，'
        + '盘后链 etf_snapshot 步产物）· 本卡只做筛选与排序，不重算任何指标'
        + ' · 分类口径=engine/tools/etf_taxonomy.py（单一源）'
        + ' · 量比=当日成交额/20日均额 · 距MA20/距60日高/尾盘30分钟占比均为源字段';
      const setSrc = (mode) => { src.textContent = (mode === 'etf') ? SRC_ETF_TEXT : SRC_STOCK_TEXT; };

      const showToast = (text) => {
        const el = S.el.toast;
        if (!el) return;
        el.textContent = text;
        el.hidden = false;
        if (S.toastTimer) clearTimeout(S.toastTimer);
        S.toastTimer = setTimeout(() => { if (S.el.toast) S.el.toast.hidden = true; }, TOAST_MS);
      };
      // 状态条：成功走 okbar、失败走 err（回执原样，不截断不改写）
      const setStatus = (text, kind) => {
        const el = S.el.status;
        if (!el) return;
        if (!text) { el.hidden = true; el.textContent = ''; return; }
        el.hidden = false;
        el.className = kind === 'err' ? 'sel-err' : 'sel-okbar';
        el.textContent = text;
      };

      // ------------------------------------------------------------ 偏好状态（旧 6138-6168 selFApply）
      // 2026-11-02（标注 67/68）：KDJ 与均线**拆成两个独立开关**，并且对**全部策略组**开放
      //   （原 MA_GROUPS 白名单只放 B1/B2/B3/单针/动能，其它组点了没反应）。
      //   默认系数按主人定：白线≥黄线×0.995、收盘≥黄线×0.995（比旧 0.99/0.97 严）。
      const MA_DEFAULTS = { kdjOn: false, maOn: false, jMax: 13, shortOverLong: 0.995, closeOverLong: 0.995 };
      // 2026-11-03（主人）：周线选股的 J 上限默认 **0** —— 「周线 KDJ 至少要小于 0 才有意义」。
      //   日线组仍用 13；周B1 组如果存的还是旧默认 13，自动升为 0。
      const maDefaultJ = (g) => (g === '周B1' ? 0 : 13);
      const maUpgradeJ = (v, g) => (g === '周B1' && Math.abs(Number(v) - 13) < 1e-9 ? 0 : Number(v));
      const MA_GROUPS = ['B1', 'B2', 'B3', '单针', '动能'];   // 仅作旧配置迁移时的已知组清单
      const defaultPrefs = () => ({
        pools: POOLS.map((p) => p.key),
        ma: { on: false, kdjOn: false, maOn: false, jMax: 13, shortOverLong: 0.995, closeOverLong: 0.995 },
        maBy: {},                              // 2026-11-01：按组记忆 {组key:{kdjOn,maOn,jMax,shortOverLong,closeOverLong}}
        ntype: { on: false },                  // 2026-11-01：N 型结构开关（默认关，开=剔已破；标记始终展示）
        pin: { on: false, longMin: 70, shortMax: 35 },
        boards: [], boardOn: false, sigs: {}, inds: [], cons: [], hot: { on: false },
        // 2026-11-15（标注 N82）：流通市值原来只存 localStorage（换设备/清缓存就丢）→ 同时落服务端。
        mcap: null,
      });
      const P = defaultPrefs();
      // 旧默认值 → 新默认值：只升「还停留在旧默认」的，不覆盖主人手改过的值
      const maUpgrade = (v, oldDefault) => (Math.abs(Number(v) - oldDefault) < 1e-9 ? 0.995 : Number(v));
      const maNorm = (m, legacyMerge, g) => {
        const out = Object.assign({}, MA_DEFAULTS, { jMax: maDefaultJ(g) });
        if (!m || typeof m !== 'object') return out;
        if (legacyMerge && typeof m.on === 'boolean') { out.kdjOn = m.on; out.maOn = m.on; }   // 旧合并开关
        if (typeof m.kdjOn === 'boolean') out.kdjOn = m.kdjOn;
        if (typeof m.maOn === 'boolean') out.maOn = m.maOn;
        if (isNum(m.jMax)) out.jMax = maUpgradeJ(m.jMax, g);
        if (isNum(m.shortOverLong)) out.shortOverLong = maUpgrade(m.shortOverLong, 0.99);
        if (isNum(m.closeOverLong)) out.closeOverLong = maUpgrade(m.closeOverLong, 0.97);
        return out;
      };
      // 读口：按组取参数；B1 在没存过 maBy['B1'] 时才回落旧存储 P.ma
      const maOf = (g) => maNorm((P.maBy || {})[g] || (g === 'B1' ? P.ma : null), true, g);
      const applyPrefs = (s) => {
        if (!s || typeof s !== 'object') return;
        if (Array.isArray(s.pools)) {
          const ok = s.pools.filter((k) => POOLS.some((p) => p.key === k));
          P.pools = ok;
        }
        if (s.ma && typeof s.ma === 'object') {
          P.ma = maNorm(s.ma, true, 'B1');
          P.ma.zhuOn = !!s.ma.zhuOn;   // 旧字段：砖形图独立开关（下面迁移到 maBy['砖形图']）
        }
        // 按组记忆：新格式 {kdjOn,maOn,...}；旧格式 {on,...} 迁移为两开关同开
        if (s.maBy && typeof s.maBy === 'object') {
          const out = {};
          Object.keys(s.maBy).forEach((k) => {
            const m = s.maBy[k];
            if (m && typeof m === 'object') out[k] = maNorm(m, true, k);
          });
          P.maBy = out;
        }
        // 旧砖形图开关 → 砖形图组（两组条件同源，迁移后跟其它组同构）
        if (s.ma && typeof s.ma === 'object' && s.ma.zhuOn && !P.maBy['砖形图']) {
          P.maBy['砖形图'] = Object.assign({}, maNorm(s.ma, false, '砖形图'), { kdjOn: true, maOn: true });
        }
        if (s.ma && typeof s.ma === 'object' && !P.maBy['B1']) P.maBy['B1'] = maNorm(s.ma, true, 'B1');   // 旧配置迁入 B1
        if (s.ntype && typeof s.ntype === 'object') P.ntype = { on: !!s.ntype.on };
        if (Array.isArray(s.inds)) P.inds = s.inds.filter((x) => typeof x === 'string');
        if (Array.isArray(s.cons)) P.cons = s.cons.filter((x) => typeof x === 'string');
        if (s.pin && typeof s.pin === 'object') {
          P.pin = {
            on: !!s.pin.on,
            longMin: isNum(s.pin.longMin) ? Number(s.pin.longMin) : 70,
            shortMax: isNum(s.pin.shortMax) ? Number(s.pin.shortMax) : 35,
          };
        }
        if (s.hot && typeof s.hot === 'object') P.hot = { on: !!s.hot.on };
        // 流通市值：{min,max}，允许空串（= 不限）；缺字段不覆盖。
        if (s.mcap && typeof s.mcap === 'object') {
          const num = (v) => (v === '' || v == null || !isNum(v) ? '' : Number(v));
          P.mcap = { min: num(s.mcap.min), max: num(s.mcap.max) };
        }
        if (Array.isArray(s.boards)) {
          P.boards = s.boards.filter((x) => x && (x.code || x.name)).map((x) => ({
            code: String(x.code || ''), name: String(x.name || x.code || ''), on: x.on !== false,
          }));
        }
        if (s.boardOn !== undefined) P.boardOn = !!s.boardOn;
        if (s.sigs && typeof s.sigs === 'object' && !Array.isArray(s.sigs)) {
          const out = {};
          Object.keys(s.sigs).forEach((k) => {
            const v = s.sigs[k];
            if (Array.isArray(v)) out[k] = v.filter((x) => typeof x === 'string');
          });
          P.sigs = out;
        }
      };
      const prefsPayload = () => ({
        pools: P.pools, ma: P.ma, pin: P.pin, boards: P.boards, boardOn: !!P.boardOn,
        sigs: P.sigs || {}, inds: P.inds, cons: P.cons, hot: P.hot,
        maBy: P.maBy || {}, ntype: P.ntype || { on: false },
        mcap: P.mcap || null,
        liveGroups: (S.liveScope || []).slice(),
      });
      // 回读核对用的比较键（服务端只落显式字段，故只比这 9 个）
      const PREF_KEYS = ['pools', 'ma', 'pin', 'boards', 'boardOn', 'sigs', 'inds', 'cons', 'hot',
        'maBy', 'ntype', 'liveGroups', 'mcap'];
      const pickPrefs = (s) => {
        const out = {};
        PREF_KEYS.forEach((k) => { out[k] = s && s[k] !== undefined ? s[k] : null; });
        return out;
      };

      // ------------------------------------------------------------ 过滤（旧 6213-6258 selPassFilter）
      const sigOn = (key, sig) => {
        const arr = P.sigs && P.sigs[key];
        return !Array.isArray(arr) || arr.indexOf(sig) >= 0;
      };
      const hotSet = () => {
        if (S.hotSet) return S.hotSet;
        const list = ((S.data && S.data.hot_concepts) || {}).stocks || [];
        S.hotSet = new Set(list);
        return S.hotSet;
      };
      const passFilter = (r) => {
        const pools = new Set(P.pools);
        const bd = boardOf(r.code6);
        if (!bd || !pools.has(bd)) return false;
        if (!pools.has('st') && isST(r.name)) return false;
        const g = S.group || '';
        // 2026-11-02（标注 67/68）：KDJ 与均线**拆成两个开关**，所有策略组同构（不再有白名单）。
        //   KDJ：J < 上限；均线：白线≥黄线×系数 且 收盘≥黄线×系数。缺值判不通过（不插值）。
        if (g) {
          const m = maOf(g);
          if (m.kdjOn || m.maOn) {
            const sp = r.spark || {};
            const lastOf = (k) => { const a = sp[k] || []; return a.length ? a[a.length - 1] : null; };
            if (m.kdjOn && (r.j == null || !(Number(r.j) < m.jMax))) return false;
            if (m.maOn) {
              const c = lastOf('close'), w = lastOf('white'), y = lastOf('yellow');
              if (w == null || y == null || !(Number(w) >= Number(y) * m.shortOverLong)) return false;
              if (c == null || y == null || !(Number(c) >= Number(y) * m.closeOverLong)) return false;
            }
          }
        }
        // N 型结构开关（2026-11-01 主人定）：默认关；开时剔除「收盘 < 最近已确认缠论笔低点」；关时不过滤但标记始终展示
        if (P.ntype && P.ntype.on && r.defense && r.defense.ntype_broken) return false;
        // 单针长短期（只在本战法生效）
        if (g === '单针' && P.pin.on) {
          const p = r.pin;
          if (!p || p.long == null || p.short == null) return false;
          if (!(Number(p.long) >= P.pin.longMin)) return false;
          if (!(Number(p.short) <= P.pin.shortMax)) return false;
        }
        const bm = ((S.data || {}).board_map || {})[r.code6] || {};
        const selBoards = (P.boards || []).filter((x) => x && x.on !== false && (x.code || x.name));
        const boardOn = P.boardOn && selBoards.length > 0;
        const hotOn = !!(P.hot && P.hot.on);
        if (boardOn || hotOn) {
          let hit = false;
          if (boardOn) {
            const codes = new Set([].concat(bm.ind_codes || [], bm.ind3_codes || [], bm.con_codes || []));
            const names = new Set([].concat(bm.ind || [], bm.ind3 || [], bm.con || []));
            hit = selBoards.some((x) => (x.code ? codes.has(x.code) : names.has(x.name)));
          }
          if (!hit && hotOn) hit = hotSet().has(r.code6);
          if (!hit) return false;
        }
        return true;
      };
      const mcapOk = (r) => {
        const lo = S.mcap.min === '' ? null : Number(S.mcap.min);
        const hi = S.mcap.max === '' ? null : Number(S.mcap.max);
        if (lo === null && hi === null) return true;
        const v = r.mcap_yi;
        if (v == null || isNaN(v)) return false;      // 市值缺 → 被区间过滤掉（不猜）
        return (lo === null || Number(v) >= lo) && (hi === null || Number(v) <= hi);
      };

      // ------------------------------------------------------------ 计算视图（过滤 + 排序）
      const groupOf = (key) => {
        const gs = (S.data && S.data.groups) || [];
        return gs.find((x) => x.key === key) || null;
      };
      const computeView = () => {
        const g = groupOf(S.group) || { key: S.group, rows: [] };
        let out = (g.rows || []).filter((r) => sigOn(g.key, r.sig) && passFilter(r) && mcapOk(r));
        const k = S.sort.key;
        if (k) {
          const dir = S.sort.dir;
          out = out.slice().sort((a, b) => {
            const va = sortVal(a, k, S), vb = sortVal(b, k, S);
            const na = va == null || va === '' || isNaN(va);
            const nb = vb == null || vb === '' || isNaN(vb);
            if (na && nb) return 0;                 // 比较器必须自反，否则 null 会被排散
            if (na) return 1;                       // 缺值沉底
            if (nb) return -1;
            return (va < vb ? 1 : (va > vb ? -1 : 0)) * dir;
          });
        }
        S.view = out;
      };

      // ------------------------------------------------------------ 虚拟滚动渲染
      // ---- ETF 行（2026-11-11）：列顺序与 COLS_ETF 一一对应；字段全是后端换好单位的源值
      const rowHtmlEtf = (r, top) => {
        const lq = S.livePct[r.code];
        const t30 = (r.tail30 == null) ? '<span class="mut">—</span>'
          : '<span class="' + (Number(r.tail30) >= 25 ? 'up' : '') + '">'
            + Number(r.tail30).toFixed(1) + '%</span>';
        const split = r.split_suspect
          ? '<span class="sel-dk warn" title="疑是份额折算（窗口内单日跳空>50%），价格序列不可比">折算</span>'
          : '';
        // 买点徽标（2026-11-11）：后端从 buy_signal.db::etf_signal 取信号、用快照 group_sigs 的
        // label/cls/desc 渲染；本页不重算信号，只显示。
        const bd = (r.badges || []).map((b) => (
          '<span class="sel-sg ' + esc(b.cls || '') + '" title="' + esc(b.desc || '')
          + '">' + esc(b.label || '') + '</span>'
        )).join('');
        return '<div style="top:' + top + 'px" class="sel-grid sel-r is-etf" data-code="' + esc(r.code) + '">'
          + '<div class="c c-nm" title="' + esc(r.code) + '"><span class="sel-nm is-link" data-open-kline="'
          + esc(r.code) + '" data-open-name="' + esc(r.name || r.code) + '">' + esc(r.name || r.code)
          + '</span>' + bd + split + '</div>'
          + '<div class="c c-cat" title="' + esc(r.sub || '') + '">' + esc(r.cat || '—') + '</div>'
          + '<div class="c num" data-k="pct"'
          + (lq ? ' title="实时 ' + esc(Number(lq.last).toFixed(2) + '（昨收 '
            + Number(lq.pre).toFixed(2) + '）· ' + (lq.asof || '')) + '"' : '')
          + '>' + numCell(lq ? lq.pct : r.pct, 2, true) + '</div>'
          + '<div class="c num">' + numCell(r.pct5, 2, true) + '</div>'
          + '<div class="c num">' + numCell(r.pct20, 2, true) + '</div>'
          + '<div class="c num">' + numCell(r.above_ma20, 2, true) + '</div>'
          + '<div class="c num c-amt">' + numCell(r.amount_yi, 2, false) + '</div>'
          + '<div class="c num">' + numCell(r.ratio, 2, false) + '</div>'
          + '<div class="c num">' + t30 + '</div>'
          + '<div class="c"></div>'
          + '</div>';
      };
      const rowHtml = (r, top) => {
        // 2026-11-11：ETF 行单独一套列（列顺序与 COLS_ETF 一一对应），个股行逻辑不动
        if (S.mode === 'etf') return rowHtmlEtf(r, top);
        return rowHtmlStock(r, top);
      };
      const rowHtmlStock = (r, top) => {
        const inPool = S.poolSet.has(r.code);
        // 盘中实时涨幅（2026-11-13）：渲染直接吃模型里的实时值，任何重绘都不再退回昨日涨幅
        const lq = S.livePct[r.code];
        // 2026-11-03（主人问「为什么这几行变暗」）：veto 行只降透明度、界面上没任何说明。
        //   这里把否决原因接进行/名称的 title —— 悬停就能看到「V1 出货痕迹（日期…）」。
        const vetoList = ((r.score || {}).veto_reason || []).filter((x) => /^(V1|V2)/.test(String(x)));
        const vetoed = vetoList.length > 0;
        const vetoTip = vetoed ? ('一票否决：' + vetoList.join('；')) : '';
        // 个股名带 data-open-kline 占位（shell 捕获阶段委托 → 父页 K 线弹窗）；
        // 行本身只用来定位，不抢点击：名称节点可点，其余单元格保持纯展示。
        return '<div style="top:' + top + 'px" class="sel-grid sel-r' + (vetoed ? ' veto' : '')
          + '" data-code="' + esc(r.code) + '"'
          + (vetoTip ? ' title="' + esc(vetoTip) + '"' : '')
          + '>'
          + '<div class="c c-nm" title="' + esc([r.code, vetoTip].filter(Boolean).join(' · ')) + '"><span class="sel-nm is-link" data-open-kline="'
          + esc(r.code) + '" data-open-name="' + esc(r.name || r.code) + '">' + esc(r.name || r.code)
          + '</span>' + hitBadges(r) + '</div>'
          + '<div class="c c-sec">' + sectorCell(r.sector) + '</div>'
          + '<div class="c num" data-k="pct"'
          + (lq ? ' title="实时 ' + esc(Number(lq.last).toFixed(2) + '（昨收 '
            + Number(lq.pre).toFixed(2) + '）· ' + (lq.asof || '')) + '"' : '')
          + '>' + numCell(lq ? lq.pct : r.pct, 2, true) + '</div>'
          + '<div class="c num" data-k="vr"'
          + (lq && lq.vr != null ? ' title="盘中实时量比（累计 '
            + esc(Number(lq.vol || 0).toFixed(0)) + ' 手 / 前20日均量）· ' + esc(lq.asof || '') + '"' : '')
          + '>' + numCell(lq && lq.vr != null ? lq.vr : r.vol_ratio, 2, false) + '</div>'
          + '<div class="c num">' + numCell(r.mav_pct, 2, true) + '</div>'
          + '<div class="c num">' + numCell(r.j, 1, false) + '</div>'
          + '<div class="c num c-ds" title="' + esc(dsTip(S, r.code6 || String(r.code || '').split('.')[0]))
          + '">' + dsCell(r) + '</div>'
          + '<div class="c num" title="' + (r.mcap_src === 'snapshot' ? '快照口径（非 PIT）' : '')
          + '">' + numCell(r.mcap_yi, 1, false) + '</div>'
          + '<div class="c num">' + numCell(r.flow_yi, 2, true) + '</div>'
          + '<div class="c c-sp">' + sparkSvg(r) + '</div>'
          + '<div class="c c-df">' + defenseCell(r) + '</div>'
          + '<div class="c star"><button type="button" class="sel-star' + (inPool ? ' on' : '')
          + '" data-pool="' + esc(r.code) + '" title="' + (inPool ? '移出选股池' : '加入选股池')
          + '">' + (inPool ? '★' : '☆') + '</button></div>'
          + '</div>';
      };
      const paintRows = () => {
        const vpEl = S.el.vp;
        if (!vpEl) return;
        const total = S.view.length;
        const totalH = (total * ROW_H) + 'px';
        if (S.html.rowsH !== totalH) {
          S.html.rowsH = totalH;
          S.el.rows.style.height = totalH;
        }
        if (!total) { S.el.rows.innerHTML = ''; S.html.range = ''; return; }
        const viewH = vpEl.clientHeight || 320;
        const first = Math.max(0, Math.floor(vpEl.scrollTop / ROW_H) - BUFFER);
        const last = Math.min(total, Math.ceil((vpEl.scrollTop + viewH) / ROW_H) + BUFFER);
        // 窗口没变就不重建 DOM（滚动时绝大多数帧都会命中这里）
        const range = first + ':' + last + ':' + total;
        if (S.html.range === range) return;
        S.html.range = range;
        const parts = [];
        for (let i = first; i < last; i++) {
          // 行绝对定位到 i*ROW_H：行数变化时只需改容器高度，不必重排整表
          parts.push(rowHtml(S.view[i], i * ROW_H));
        }
        S.el.rows.innerHTML = parts.join('');
      };
      const onScroll = () => {
        // 滚动落定后补实时值（220ms 防抖）：虚拟滚动重绘出来的新行不能停在昨日涨幅上
        if (S.alive && S.kickLive) S.kickLive();
        if (S.raf) return;
        const raf = (typeof requestAnimationFrame === 'function')
          ? requestAnimationFrame : ((fn) => setTimeout(fn, 16));
        S.raf = raf(() => { S.raf = 0; paintRows(); });
      };
      vp.addEventListener('scroll', onScroll, { passive: true });

      // ------------------------------------------------------------ 表头
      const paintHead = (cols) => {
        // 2026-11-11：支持传列集（ETF 模式用 COLS_ETF），不传仍是个股 COLS
        const html = (cols || COLS).map((c) => {
          if (!c.key) return '<div class="h ' + (c.cls || '') + '">' + esc(c.label) + '</div>';
          const act = S.sort.key === c.key;
          const arrow = act ? (S.sort.dir === 1 ? ' ▼' : ' ▲') : ' ⇅';
          const isNumCol = c.key !== 'defense_pct';
          return '<div class="h sortable' + (act ? ' sorted' : '') + (isNumCol ? ' num' : '')
            + (c.cls ? ' ' + c.cls : '') + '" data-sort="' + c.key + '" title="'
            + esc(c.tip || '点击排序') + '">'
            + esc(c.label) + arrow + '</div>';
        }).join('');
        const headCls = 'sel-grid sel-head' + (S.mode === 'etf' ? ' is-etf' : '');
        if (S.html.head !== html || S.html.headCls !== headCls) {
          S.html.head = html;
          S.html.headCls = headCls;
          S.el.head.className = headCls;
          S.el.head.innerHTML = html;
        }
      };

      // ------------------------------------------------------------ 状态区（快照告警 / 空态原因）
      const activeFilters = () => {
        const out = [];
        const allPools = (P.pools || []).length === POOLS.length;
        if (!allPools) out.push('股票池：' + (P.pools.length ? P.pools.map((k) => (POOLS.find((p) => p.key === k) || {}).short || k).join('') : '无'));
        if (S.mcap.min !== '' || S.mcap.max !== '') out.push('流通市值 ' + S.mcap.min + '–' + S.mcap.max + ' 亿');
        const mAct = maOf(S.group);
        if (mAct.kdjOn) out.push('KDJ 过滤');
        if (mAct.maOn) out.push('均线过滤');
        if (P.ntype && P.ntype.on) out.push('N型结构（剔已破）');
        if (S.group === '单针' && P.pin.on) out.push('单针长短期过滤');
        const on = (P.boards || []).filter((x) => x.on !== false);
        if (P.boardOn && on.length) out.push('板块筛选（' + on.length + ' 项）');
        if (P.hot && P.hot.on) out.push('热门板块' + (P.boardOn && on.length ? '（与板块取并集）' : ''));
        const g = S.group || '';
        const selArr = P.sigs && P.sigs[g];
        const allSigs = ((S.data || {}).group_sigs || {})[g] || [];
        if (Array.isArray(selArr) && allSigs.length > 1 && selArr.length < allSigs.length) {
          out.push(g + ' 类型 ' + selArr.length + '/' + allSigs.length);
        }
        return out;
      };
      const paintStat = () => {
        const g = groupOf(S.group) || { rows: [], count: 0 };
        const raw = (g.rows || []).length;
        const shown = S.view.length;
        const act = activeFilters();
        let s = '分组 <b>' + esc(g.name || g.key || '—') + '</b> · 原始 <b>' + raw + '</b> 行 · 显示 <b>'
          + shown + '</b> 行';
        if (shown !== raw) s += ' · 过滤掉 ' + (raw - shown);
        if (act.length) s += ' · 生效条件：' + esc(act.join(' ｜ '));
        // 数据时效：打分是日频收盘产物，盘中看到的是上一交易日的分（写死在列头悬停 + 这一行）
        const sc = S.score || {};
        if (sc.state === 'ok') {
          s += ' · 打分 ' + esc(sc.asof || '—') + ' 收盘口径' + (sc.stale ? '（偏旧）' : '');
        } else if (sc.state === 'none') {
          s += ' · <b class="sel-off" title="' + esc(sc.reason || '') + '">无打分数据</b>';
        }
        if (S.liveAt && isTradingNow()) {
          s += ' · <b class="sel-live">涨幅实时 ' + esc(S.liveAt) + '</b>';
        }
        if (S.html.stat !== s) { S.html.stat = s; S.el.stat.innerHTML = s; }
      };
      const emptyHtml = () => {
        const g = groupOf(S.group) || { rows: [], count: 0 };
        const raw = (g.rows || []).length;
        if (!raw) return '<div class="sel-hint">该战法今日无命中（占位分组，数据源见 docs/selection_contract.md §2）。</div>';
        const act = activeFilters();
        const head = '当前筛选条件下无标的（本组共 ' + raw + ' 只）。';
        const why = act.length
          ? head + '生效条件：' + act.join(' ｜ ') + '。'
          : head + '没有检测到生效的筛选条件 —— 可能是这批票缺关键字段（市值/归属/信号），请上报。';
        return '<div class="sel-hint">' + esc(why) + '</div>';
      };

      // ------------------------------------------------------------ 快照告警（旧 6980-7010）
      const paintSnapshot = () => {
        const d = S.data || {};
        const fr = d.freshness || {};
        const el = S.el.status;
        if (d.__error) {
          el.hidden = false;
          el.className = 'sel-err';
          el.textContent = '选股快照加载失败：' + d.__error;
          return;
        }
        if (d.error) {
          el.hidden = false;
          el.className = 'sel-err';
          el.textContent = '⚠ 选股快照未生成：' + d.error;
          return;
        }
        if (fr.state === 'stale') {
          el.hidden = false;
          el.className = 'sel-err';
          el.textContent = '⚠ 选股快照已过期：当前快照 ' + (fr.snapshot_date || '—')
            + '，应产日 ' + (fr.expected_date || '—') + '（' + (fr.produce_by || '18:00')
            + ' 前应有新版）。下表是旧交易日的选股，不是今天的。';
          return;
        }
        // 盘中预估（2026-11-04）：产物当日有效。收盘后盘后链产快照后，/api/selection 自动切回盘后版
        if ((d.mode || fr.mode) === 'intraday') {
          setStatus('', 'ok');
          const meta = d.meta || {};
          const extra = (meta.notes || []).join('；');
          S.el.note.hidden = false;
          S.el.note.textContent = '盘中预估（' + (meta.generated_at || fr.generated_at || '—')
            + '）：信号与 9 列均按「历史收盘 + 今日实时 bar」重算；量能=至今累计（非全天），'
            + '14:45 后最接近收盘口径。收盘后盘后链产出快照，本页自动切回盘后定稿。'
            + (extra ? (' ' + extra) : '');
          return;
        }
        setStatus('', 'ok');
        const warns = [];
        if (fr.state === 'unknown') warns.push('⚠ 快照新鲜度无法判定（交易日历不可用或快照日缺失）——下表可能不是最新。');
        if (fr.today_pending) warns.push('今日快照待生成（' + (fr.produce_by || '18:00') + ' 后由盘后链产出）。');
        if ((d.warnings || []).length) warns.push('⚠ 打分告警：' + (d.warnings || []).join('；'));
        if ((d.score_warn || []).length && !(d.warnings || []).length) {
          warns.push('⚠ 打分告警：' + (d.score_warn || []).join('；'));
        }
        if (warns.length) {
          S.el.note.hidden = false;
          S.el.note.textContent = warns.join(' ');
        } else {
          S.el.note.hidden = true;
          S.el.note.textContent = '';
        }
      };

      // ------------------------------------------------------------ 分组选项卡（旧 6950-6968）
      const paintTabs = () => {
        const d = S.data || {};
        const gs = d.groups || [];
        const html = gs.map((x) => {
          const sigs = ((d.group_sigs || {})[x.key]) || [];
          const sel = P.sigs && P.sigs[x.key];
          const part = Array.isArray(sel) && sigs.length > 1 && sel.length < sigs.length;
          const tip = sigs.length > 1
            ? '点击切换分组；筛「类型」可只显示部分形态'
            : '点击切换分组';
          return '<button type="button" class="sel-tab' + (x.key === S.group ? ' on' : '')
            + (part ? ' part' : '') + '" data-g="' + esc(x.key) + '" title="' + esc(tip) + '">'
            + esc(x.name || x.key) + '<span class="n">' + (x.count != null ? x.count : (x.rows || []).length)
            + '</span>' + (part ? '<span class="n">' + sel.length + '/' + sigs.length + '</span>' : '')
            + '</button>';
        }).join('');
        if (S.html.tabs !== html) { S.html.tabs = html; S.el.tabs.innerHTML = html; }
        if (!gs.length) S.el.tabs.innerHTML = '<span class="sel-hint">快照无分组。</span>';
      };

      // ------------------------------------------------------------ 筛选条
      const poolSummary = () => {
        const on = POOLS.filter((p) => (P.pools || []).indexOf(p.key) >= 0);
        if (on.length === POOLS.length) return '全';
        if (!on.length) return '无';
        return on.map((p) => p.short).join('');
      };
      const paintFilterBar = () => {
        const d = S.data || {};
        const allPools = (P.pools || []).length === POOLS.length;
        const boardsOn = (P.boards || []).filter((x) => x.on !== false).length;
        const sigs = ((d.group_sigs || {})[S.group]) || [];
        const sigSel = P.sigs && P.sigs[S.group];
        const sigPart = Array.isArray(sigSel) && sigs.length > 1 && sigSel.length < sigs.length;
        const gf = ((d.group_filters || {})[S.group]) || [];
        const parts = [];
        parts.push('<button type="button" class="sel-chip' + (allPools ? '' : ' on')
          + '" data-chip="pool" title="股票池：不选任一板块＝不显示任何票；含ST 未选则剔除 ST/*ST">股票池<b>'
          + esc(poolSummary()) + '</b></button>');
        parts.push('<span class="sel-chip' + ((S.mcap.min !== '' || S.mcap.max !== '') ? ' on' : '')
          + '" title="按流通市值剔庄股 / 大票（前端过滤当前列表，亿）">⚙ 流通市值'
          + '<input class="sel-inp" data-mcap="min" value="' + esc(S.mcap.min) + '" aria-label="流通市值下限">'
          + '<span class="u">–</span>'
          + '<input class="sel-inp" data-mcap="max" value="' + esc(S.mcap.max) + '" aria-label="流通市值上限">'
          + '<span class="u">亿</span></span>');
        parts.push('<button type="button" class="sel-chip' + (P.boardOn ? ' on' : '')
          + '" data-chip="board" title="板块筛选（行业+概念合并，二级/三级/概念取并集，任一命中即可）">板块<b>'
          + (P.boardOn ? (boardsOn + ' / ' + (P.boards || []).length) : '关') + '</b></button>');
        parts.push('<button type="button" class="sel-chip' + (P.hot && P.hot.on ? ' on' : '')
          + '" data-chip="hot" title="' + esc(hotTitle(d)) + '">热门板块<b>'
          + (P.hot && P.hot.on ? (((d.hot_concepts || {}).boards || []).length + ' 个') : '关')
          + '</b></button>');
        // N 型结构开关（2026-11-01 主人定）：默认关；开=剔已破；关时只展示标记
        parts.push('<button type="button" class="sel-chip' + (P.ntype && P.ntype.on ? ' on' : '')
          + '" data-chip="ntype" title="N 型结构开关（2026-11-01 主人定）：开 = 剔除「收盘 < 最近已确认缠论笔低点」的标的；关 = 不过滤，但「N型已破」标记始终展示。默认关。">N型结构<b>'
          + (P.ntype && P.ntype.on ? '剔已破' : '关') + '</b></button>');
        if (sigs.length > 1) {
          parts.push('<button type="button" class="sel-chip' + (sigPart ? ' on' : '')
            + '" data-chip="sig" title="只显示本组的部分形态/类型（不选＝全开）">类型<b>'
            + (sigPart ? (sigSel.length + '/' + sigs.length) : '全') + '</b></button>');
        }
        // 2026-11-02（标注 67/68）：KDJ 与均线各一个开关，**所有策略组都出**（不再跟后端 group_filters 走）；
        //   原合并 chip 的 J 上限会把整组滤成 0 行，主人没法单独关掉 KDJ —— 拆开后可以只开均线。
        const mNow = maOf(S.group);
        parts.push('<button type="button" class="sel-chip' + (mNow.kdjOn ? ' on' : '')
          + '" data-chip="kdj" title="KDJ 过滤：J < 上限（本组独立记忆，默认关）">KDJ<b>'
          + (mNow.kdjOn ? '开' : '关') + '</b></button>');
        parts.push('<button type="button" class="sel-chip' + (mNow.maOn ? ' on' : '')
          + '" data-chip="ma" title="均线过滤：白线≥黄线×系数 且 收盘≥黄线×系数（本组独立记忆，默认关）">均线<b>'
          + (mNow.maOn ? '开' : '关') + '</b></button>');
        gf.forEach((f) => {
          if (f.type === 'pin_rsv') {
            parts.push('<button type="button" class="sel-chip' + (P.pin.on ? ' on' : '')
              + '" data-chip="pin" title="长 RSV≥下限 · 短 RSV≤上限">长短期<b>'
              + (P.pin.on ? '开' : '关') + '</b></button>');
          }
        });
        const html = parts.join('');
        if (S.html.fbar !== html) { S.html.fbar = html; S.el.fbar.innerHTML = html; }
      };

      // ------------------------------------------------------------ 内联面板（替代旧端 body 浮层）
      const boardMatches = (raw) => {
        const cat = ((S.opts && S.opts.catalog) || []);
        const up = String(raw).toUpperCase();
        const hit = cat.find((x) => String(x.code).toUpperCase() === up)
          || cat.find((x) => x.name === raw)
          || cat.find((x) => String(x.name).indexOf(raw) >= 0);
        return hit || null;
      };
      const panelHtml = () => {
        const kind = S.panel;
        if (!kind) return '';
        const d = S.data || {};
        if (kind === 'pool') {
          const onSet = new Set(P.pools || []);
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>股票池</b>'
            + '<span class="n">不选任一板块＝不显示任何票</span>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-opts">' + POOLS.map((p) => '<label class="sel-opt" title="' + esc(p.hint) + '">'
              + '<input type="checkbox" data-poolopt="' + p.key + '"' + (onSet.has(p.key) ? ' checked' : '')
              + '><span>' + esc(p.label) + '</span></label>').join('') + '</div>'
            + '<div class="sel-hint">含 ST 必须同时选板块；未勾「含ST」时剔除 ST/*ST。'
            + '板块归属按代码前缀判定：主板 60/00、创业板 30、科创板 688、北交所 92。</div></div>';
        }
        if (kind === 'board') {
          const arr = P.boards || [];
          const list = arr.length
            ? arr.map((x, i) => '<span class="sel-brow"><label class="sel-opt">'
              + '<input type="checkbox" data-boardon="' + i + '"' + (x.on !== false ? ' checked' : '')
              + '><span>' + esc(x.name || x.code) + '</span></label>'
              + '<span class="c">' + (x.code ? esc(x.code) : '按名称') + '</span>'
              + '<button type="button" class="x" data-boarddel="' + i + '" title="从清单删除">×</button></span>').join('')
            : '<span class="sel-hint">清单为空 —— 点「同步自选池板块」或在上方输入框添加。</span>';
          const favN = ((S.opts && S.opts.favorites) || []).length;
          const catN = ((S.opts && S.opts.catalog) || []).length;
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>板块筛选设置</b>'
            + '<span class="n">自选池收藏板块 ' + favN + ' 项 · 目录 ' + catN + ' 项</span>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-opts"><label class="sel-opt"><input type="checkbox" data-boardenable="1"'
            + (P.boardOn ? ' checked' : '') + '><span>启用板块筛选</span></label></div>'
            + '<div class="sel-hint">命中口径：按代码精确匹配个股的<b>二级行业 ∪ 三级行业 ∪ 概念</b>（取并集，任一命中即可）；'
            + '只填名称的按名称匹配。清空清单 = 不筛。与「热门板块」同时开启时两者也取并集。</div>'
            + '<div class="sel-frow"><input type="text" data-boardadd-inp placeholder="添加板块：名称或代码，如 881153.TI / 房地产">'
            + '<button type="button" class="sel-mini" data-act="board-add">添加</button>'
            + '<button type="button" class="sel-mini" data-act="board-sync">同步自选池板块</button></div>'
            + '<div class="sel-blist">' + list + '</div></div>';
        }
        if (kind === 'sig') {
          const sigs = ((d.group_sigs || {})[S.group]) || [];
          const cur = P.sigs && P.sigs[S.group];
          const on = (code) => !Array.isArray(cur) || cur.indexOf(code) >= 0;
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>显示哪些 · '
            + esc((groupOf(S.group) || {}).name || S.group) + '</b>'
            + '<span class="n">不勾＝一种都不显示（如实空，不偷偷回退全开）</span>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-opts">' + sigs.map((x) => '<label class="sel-opt" title="' + esc(x.desc || x.code) + '">'
              + '<input type="checkbox" data-sigopt="' + esc(x.code) + '"' + (on(x.code) ? ' checked' : '')
              + '><span>' + esc(x.label) + ' · ' + esc(x.desc || x.code) + '</span></label>').join('') + '</div>'
            + '<div class="sel-frow"><button type="button" class="sel-mini" data-act="sig-all">全选</button>'
            + '<button type="button" class="sel-mini" data-act="sig-none">清空</button></div></div>';
        }
        if (kind === 'kdj') {
          const m = maOf(S.group);
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>KDJ 过滤 · '
            + esc(S.group || '') + '</b>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-opts"><label class="sel-opt"><input type="checkbox" data-kdj-on="1"'
            + (m.kdjOn ? ' checked' : '') + '><span>启用 KDJ 过滤（J < 上限）</span></label></div>'
            + '<div class="sel-frow"><label>J 上限</label><input type="number" step="0.5" data-kdj="jMax" value="'
            + esc(m.jMax) + '"></div>'
            + '<div class="sel-hint">本组独立记忆（' + esc(S.group || '') + '）。缺 J 值即判不通过，不插值、不补 0。'
            + (S.group === '周B1' ? '周线组默认 <b>0</b>（周线 KDJ 小于 0 才有意义）。' : '') + '</div></div>';
        }
        if (kind === 'ma') {
          const m = maOf(S.group);
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>均线过滤 · '
            + esc(S.group || '') + '</b>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-opts"><label class="sel-opt"><input type="checkbox" data-ma-on="1"'
            + (m.maOn ? ' checked' : '') + '><span>启用均线过滤（白线≥黄线、收盘≥黄线）</span></label></div>'
            + '<div class="sel-frow"><label>白线 ≥ 黄线 ×</label><input type="number" step="0.001" data-ma="shortOverLong" value="'
            + esc(m.shortOverLong) + '"><label>收盘 ≥ 黄线 ×</label><input type="number" step="0.001" data-ma="closeOverLong" value="'
            + esc(m.closeOverLong) + '"></div>'
            + '<div class="sel-hint">本组独立记忆（' + esc(S.group || '') + '）。白线＝短期均线 · 黄线＝长期均线（口径与走势列、距防线同源）。'
            + '默认 0.995（允许 0.5% 容差）；任一值缺失即判不通过，不插值、不补 0。</div></div>';
        }
        if (kind === 'pin') {
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>单针长短期过滤</b>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-opts"><label class="sel-opt"><input type="checkbox" data-pin-on="1"'
            + (P.pin.on ? ' checked' : '') + '><span>启用此过滤</span></label></div>'
            + '<div class="sel-frow"><label>长 RSV ≥</label><input type="number" step="1" data-pin="longMin" value="'
            + esc(P.pin.longMin) + '"><label>短 RSV ≤</label><input type="number" step="1" data-pin="shortMax" value="'
            + esc(P.pin.shortMax) + '"></div>'
            + '<div class="sel-hint">口径 = needle_daily_builder.needle_signals；缺值判不通过。</div></div>';
        }
        if (kind === 'poolview') {
          const pool = S.pool || {};
          const items = (pool.items || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
          const slots = pool.slots || [];
          const cur = pool.is_current !== false;
          const bar = slots.length
            ? '<span class="sel-hint" style="margin:0">近 ' + slots.length + ' 个收盘日：</span>'
              + slots.map((s) => '<button type="button" class="sel-slot' + (s.date === pool.date ? ' on' : '')
                + '" data-slot="' + esc(s.date) + '" title="' + esc(s.date) + ' · ' + s.count + ' 只">'
                + esc(String(s.date || '').slice(5)) + '<i>' + s.count + '</i></button>').join('')
            : '<span class="sel-hint" style="margin:0">还没有快照（每天收盘后自动固化当日名单）。</span>';
          const list = items.length
            ? items.map((x) => '<div class="sel-pitem"><span class="nm">' + esc(x.name || x.code) + '</span>'
              + '<span class="mut">' + esc(String(x.code || '').split('.')[0]) + '</span>'
              + '<span class="mut">' + esc(x.group || '—') + '</span>'
              + '<span class="mut">' + esc(String(x.added_at || '').slice(5, 16)) + '</span>'
              + (cur ? '<button type="button" class="sel-star on" data-pool="' + esc(x.code)
                + '" title="从选股池移出（需二次点击）">×</button>' : '<span class="mut">—</span>')
              + '</div>').join('')
            : '<span class="sel-hint">该收盘日没有名单。</span>';
          return '<div class="sel-panel"><div class="sel-panel-hd"><b>选股池 · '
            + esc(pool.date || (S.data || {}).date || '—') + '（' + items.length + ' 只）</b>'
            + '<span class="n">最多保留 ' + (pool.keep || 5) + ' 个收盘日快照</span>'
            + '<button type="button" class="sel-mini" data-act="panel-close">关闭</button></div>'
            + '<div class="sel-slots">' + bar + '</div>'
            + '<div class="sel-hint">' + esc(pool.is_current === false
              ? '这是「' + (pool.date || '—') + '」的历史名单（非今日）；当日名单保留不清零。'
              : '这是当日精筛名单；结果表行尾 ☆ 可随时加票。')
            + '</div><div class="sel-plist">' + list + '</div></div>';
        }
        return '';
      };
      // ------------------------------------------------------------ ETF 模式（2026-11-11）
      // 设计：ETF 是**另一套表**，只与个股共用虚拟滚动容器与滚动节流，其余全独立：
      //   个股路径的 filter/tabs/stat/快照告警/行渲染一行不动 —— 靠 S.mode 在入口处分派。
      //   数据源 = GET /api/selection_etf（compat_misc，只读 etf_snapshot_latest）。
      const isEtf = () => S.mode === 'etf';
      const etfRowsAll = () => ((S.etf && S.etf.rows) || []);

      const computeEtfView = () => {
        let out = etfRowsAll();
        if (S.etfCat && S.etfCat !== ETF_ALL) {
          out = out.filter((r) => String(r.cat || '') === S.etfCat);
        }
        const min = Number(S.etfMin);
        if (isFinite(min) && min > 0) {
          out = out.filter((r) => r.amount_yi != null && Number(r.amount_yi) >= min);
        }
        if (S.etfSigOnly === 'b1') {
          // B1 四形态（B1_W/B1_Y/B1_Q/TOP_RES）：label 都是「B1」，砖首转强的 desc 带「砖首」
          out = out.filter((r) => (r.badges || []).some(
            (b) => String(b.label || '').indexOf('B1') >= 0 || String(b.desc || '').indexOf('砖首') >= 0));
        } else if (S.etfSigOnly === 'all') {
          out = out.filter((r) => (r.badges || []).length > 0);
        }
        const k = S.sort.key;
        if (k) {
          const dir = S.sort.dir;
          out = out.slice().sort((a, b) => {
            const va = sortVal(a, k, S), vb = sortVal(b, k, S);
            const na = va == null || va === '' || isNaN(va);
            const nb = vb == null || vb === '' || isNaN(vb);
            if (na && nb) return 0;
            if (na) return 1;
            if (nb) return -1;
            return (va < vb ? 1 : (va > vb ? -1 : 0)) * dir;
          });
        }
        S.view = out;
      };

      const paintEtfTabs = () => {
        const d = S.etf || {};
        const cats = d.cats || [];
        const cur = S.etfCat || ETF_ALL;
        const keys = [ETF_ALL].concat(cats.map((x) => x.key));
        const nOf = (k) => (k === ETF_ALL
          ? ((d.stats || {}).rows || 0)
          : (((cats.find((x) => x.key === k) || {}).n) || 0));
        const html = keys.map((k) => '<button type="button" class="sel-tab'
          + (k === cur ? ' on' : '') + '" data-etfcat="' + esc(k) + '">' + esc(k)
          + '<span class="n">' + nOf(k) + '</span></button>').join('');
        if (S.html.etfTabs !== html) {
          S.html.etfTabs = html;
          S.el.tabs.innerHTML = html;
        }
      };

      const paintEtfFilterBar = () => {
        const d = S.etf || {};
        const all = etfRowsAll();
        const sigN = all.filter((r) => (r.badges || []).length > 0).length;
        const b1N = all.filter((r) => (r.badges || []).some(
          (b) => String(b.label || '').indexOf('B1') >= 0 || String(b.desc || '').indexOf('砖首') >= 0)).length;
        const html = '<span class="sel-sub">成交额下限</span>'
          + '<input class="sel-inp" type="number" step="0.5" min="0" data-etfmin value="'
          + esc(String(S.etfMin)) + '" title="只显示当日成交额 ≥ 该值的 ETF（亿）">'
          + '<span class="sel-sub">亿</span>'
          + '<button type="button" class="sel-chip' + (S.etfSigOnly === 'all' ? ' on' : '')
          + '" data-etfsig="all" title="只看当日命中买点信号的 ETF（B1/B2/B3/B4/单针/砖形图，口径同个股 core.py）">'
          + '只看买点 <b>' + sigN + '</b></button>'
          + '<button type="button" class="sel-chip' + (S.etfSigOnly === 'b1' ? ' on' : '')
          + '" data-etfsig="b1" title="只看 B1 低位买点（B1 白/黄/青 + 砖首转强）—— ETF 上这类信号很稀缺">'
          + '只看 B1 <b>' + b1N + '</b></button>'
          + '<span class="sel-grow"></span>'
          + '<span class="sel-sub">显示 <b>' + S.view.length + '</b> 只 · 快照 '
          + esc(d.date || '—') + ' · ' + esc(d.generated_at || '—') + '</span>';
        if (S.html.etfFbar !== html) {
          S.html.etfFbar = html;
          S.el.fbar.innerHTML = html;
        }
      };

      const paintEtfStat = () => {
        const d = S.etf || {};
        const all = etfRowsAll().length;
        const shown = S.view.length;
        let s = 'ETF 快照 <b>' + esc(d.date || '—') + '</b> · 全量 <b>' + all + '</b> 只 · 显示 <b>'
          + shown + '</b> 只';
        if (shown !== all) s += ' · 过滤掉 ' + (all - shown);
        s += ' · 分类 ' + esc(S.etfCat || ETF_ALL) + ' · 默认按成交额降序（列头可点排序）';
        if (S.html.stat !== s) { S.html.stat = s; S.el.stat.innerHTML = s; }
      };

      const emptyEtfHtml = () => {
        const all = etfRowsAll().length;
        if (!all) return '<div class="sel-hint">ETF 快照未加载（点一下顶部的「ETF」重试）。</div>';
        return '<div class="sel-hint">当前筛选下无 ETF（全量 ' + all + ' 只，分类 '
          + esc(S.etfCat || ETF_ALL) + '，成交额下限 ' + esc(String(S.etfMin)) + ' 亿）。</div>';
      };

      const paintEtfSnapshot = () => {
        const d = S.etf;
        if (!d) return;                       // 加载中：「加载中」文案由 loadEtf 先写进行容器
        if (!d.ok) {
          setStatus('⚠ ' + (d.error || 'ETF 快照不可用'), 'err');
          return;
        }
        // 快照日期 ≠ 今天 → 如实标注（盘后产物，今天收盘后才会有今天的）
        const dt = new Date();
        const ymd = dt.getFullYear() * 10000 + (dt.getMonth() + 1) * 100 + dt.getDate();
        const dnum = Number(String(d.date || '').replace(/-/g, ''));
        setStatus('', 'ok');
        if (dnum && dnum < ymd) {
          S.el.note.hidden = false;
          S.el.note.textContent = 'ETF 快照为 ' + (d.date || '—') + ' 收盘口径（盘后链 etf_snapshot 每日 16:50 '
            + '前后覆盖）；今日收盘后会自动变成今天的。表中不含盘中实时值。';
        } else {
          S.el.note.hidden = true;
        }
      };

      const repaintEtf = () => {
        computeEtfView();
        paintEtfStat();
        paintEtfFilterBar();
        S.html.range = null;            // 排序/筛选变了：行内容与顺序都变，必须重绘可见窗口
        paintRows();
        if (S.kickLive) S.kickLive();   // ETF 涨幅同样按实时值补齐（2026-11-13）
        if (!S.view.length) {
          const html = emptyEtfHtml();
          if (S.html.empty !== html) { S.html.empty = html; S.el.rows.innerHTML = html; }
          S.html.rowsH = 'auto';
          S.el.rows.style.height = 'auto';
        }
      };

      const renderAllEtf = () => {
        paintTop();
        paintEtfTabs();
        computeEtfView();
        paintEtfFilterBar();
        paintEtfSnapshot();
        paintHead(COLS_ETF);
        paintEtfStat();
        S.html.range = null;
        paintRows();
        if (!S.view.length) {
          const html = emptyEtfHtml();
          if (S.html.empty !== html) { S.html.empty = html; S.el.rows.innerHTML = html; }
          S.html.rowsH = 'auto';
          S.el.rows.style.height = 'auto';
        }
        if (S.kickLive) S.kickLive();   // ETF 切模式/重载后补实时涨幅（2026-11-13）
      };

      const loadEtf = async (force) => {
        if (!S.alive || S.etfLoading) return;
        if (S.etf && !force) return;
        S.etfLoading = true;
        S.html = Object.create(null);
        S.el.fbar.textContent = 'ETF 数据加载中…';
        S.el.rows.innerHTML = '<div class="sel-hint">ETF 快照加载中（1,500+ 只）…</div>';
        S.html.rowsH = 'auto';
        S.el.rows.style.height = 'auto';
        try {
          const d = await api.get('/api/selection_etf').catch((e) => ({ __error: errText(e) }));
          if (!S.alive) return;
          S.etf = (d && !d.__error) ? d
            : { ok: false, rows: [], cats: [], error: (d || {}).__error || '空响应' };
          S.html = Object.create(null);
          S.el.vp.scrollTop = 0;
          if (isEtf()) renderAllEtf();
        } finally {
          S.etfLoading = false;
        }
      };

      const switchMode = (mode) => {
        const next = (mode === 'etf') ? 'etf' : 'stock';
        if (next === S.mode) return;
        S.mode = next;
        try { localStorage.setItem(LS_MODE, next); } catch (e) { /* 无 localStorage 就只本会话生效 */ }
        S.html = Object.create(null);
        S.sort = { key: null, dir: 1 };
        S.panel = '';
        S.view = [];
        S.el.panels.textContent = '';      // 个体面板（筛选/板块/池）在 ETF 模式下不适用，清 DOM 免残留
        S.el.vp.scrollTop = 0;
        S.el.note.hidden = true;
        setStatus('', 'ok');
        if (next === 'etf') {
          if (!S.etf) loadEtf();
          else renderAllEtf();
        } else if (S.data) {
          renderAll();
        } else {
          load();
        }
      };

      const paintPanels = () => {
        const html = panelHtml();
        if (S.html.panels !== html) {
          S.html.panels = html;
          S.el.panels.innerHTML = html;
        }
        S.el.panels.hidden = !html;
      };

      // ------------------------------------------------------------ 总渲染
      const paintTop = () => {
        // 模式按钮状态（2026-11-11）：两套表共用卡头，只换高亮与徽标文案
        if (S.el.btnModeStock) S.el.btnModeStock.className = 'sel-modebtn' + (isEtf() ? '' : ' on');
        if (S.el.btnModeEtf) S.el.btnModeEtf.className = 'sel-modebtn' + (isEtf() ? ' on' : '');
        if (isEtf()) {
          const e = S.etf || {};
          // 新鲜度：快照日 < 今天 → 标「旧」（warn 色），与个股模式的 stale 语义一致
          const dt = new Date();
          const ymd = dt.getFullYear() * 10000 + (dt.getMonth() + 1) * 100 + dt.getDate();
          const dnum = Number(String(e.date || '').replace(/-/g, ''));
          const stale = !!(dnum && dnum < ymd);
          // 用 style.display 而不是 hidden：.sel-lg/.sel-chip 自带 display，
          // HTML hidden 属性会被 CSS 覆盖（2026-11-11 实测：图例/池按钮在 ETF 模式仍显示）
          S.el.legend.style.display = 'none';
          S.el.btnPool.style.display = 'none';
          S.el.btnLive.style.display = 'none';   // 「盘中现算」只算个股，ETF 模式不适用
          setSrc('etf');
          S.el.badgeDate.textContent = 'ETF ' + (e.date || '—');
          S.el.badgeFresh.textContent = e.ok ? ('快照 ' + (stale ? '旧' : 'ok')) : '不可用';
          S.el.badgeFresh.className = 'sel-badge ' + (!e.ok ? 'bad' : (stale ? 'warn' : 'ok'));
          S.el.badgeFresh.title = 'ETF 快照（盘后链 etf_snapshot 步产物，每日 16:50 前后覆盖）';
          S.el.badgeSrc.textContent = 'etf_snapshot';
          S.el.badgeSrc.title = '生成 ' + (e.generated_at || '—') + ' · 只读透传，前端只做筛选排序';
          const st = e.stats || {};
          S.el.sub.textContent = '股票型 ETF 全量 ' + (st.rows != null ? st.rows : '—')
            + ' 只 · 分类 ' + ((e.cats || []).length || '—') + ' 类'
            + ' · 数据日 ' + (e.date || '—');
          // ETF 无选股池语义：池按钮在 ETF 模式下隐藏，免误点把 ETF 塞进个股池
          S.el.btnPool.hidden = true;
          return;
        }
        S.el.btnPool.hidden = false;
        S.el.legend.style.display = '';
        S.el.btnPool.style.display = '';
        S.el.btnLive.style.display = '';
        S.el.legend.hidden = false;
        setSrc('stock');
        const d = S.data || {};
        const fr = d.freshness || {};
        const isLive = (d.mode || fr.mode) === 'intraday';
        S.el.badgeDate.textContent = d.date ? ('当日 ' + d.date) : '快照 —';
        const st = fr.state || 'unknown';
        S.el.badgeFresh.textContent = isLive ? '盘中预估' : ('快照 ' + st);
        S.el.badgeFresh.className = 'sel-badge '
          + (isLive ? 'warn' : (st === 'fresh' ? 'ok' : (st === 'stale' ? 'bad' : 'warn')));
        S.el.badgeFresh.title = isLive
          ? ('盘中刷新产物：生成 ' + (((d.meta || {}).generated_at) || '—')
             + '（收盘后由盘后链 selectionsnap 定稿）')
          : ('快照日 ' + (fr.snapshot_date || '—') + ' · 应产日 '
             + (fr.expected_date || '—') + ' · 应产时点 ' + (fr.produce_by || '18:00'));
        const meta = d.meta || {};
        S.el.badgeSrc.textContent = String(meta.source || '—');
        S.el.badgeSrc.title = '生成 ' + (meta.generated_at || '—') + ' · 产物 ' + (meta.producer || '—');
        const asof = d.asof || {};
        S.el.sub.textContent = '信号 ' + (asof.signal_date || '—')
          + ' · 行情 ' + (((asof.quote || [])[0]) || '—')
          + ' · 命中 ' + (((d.stats || {}).hits) != null ? d.stats.hits : '—')
          + ' · 打分 ' + (((d.stats || {}).scored) != null ? d.stats.scored : '—');
        const n = ((S.pool && S.pool.count) != null) ? S.pool.count : ((d.pool || {}).count || 0);
        S.el.poolCount.textContent = String(n);
        S.el.btnPool.className = 'sel-chip' + (n ? ' on' : '') + (S.panel === 'poolview' ? ' on' : '');
      };
      const renderAll = () => {
        // 2026-11-11：ETF 模式走独立渲染链（个股链一行不动）
        if (isEtf()) { renderAllEtf(); return; }
        paintTop();
        paintTabs();
        paintFilterBar();
        paintPanels();
        paintSnapshot();
        paintHead();
        computeView();
        paintStat();
        paintRows();
        if (!S.view.length) {
          // 空态插在行容器里（不另建 DOM 分支：虚拟容器高度为 0，直接把提示放进去）
          const html = emptyHtml();
          if (S.html.empty !== html) { S.html.empty = html; S.el.rows.innerHTML = html; }
          S.html.rowsH = 'auto';
          S.el.rows.style.height = 'auto';
        }
        if (S.kickLive) S.kickLive();   // 切组/重载后补实时涨幅（2026-11-13）
      };
      const repaint = () => {
        paintFilterBar();
        paintTabs();
        computeView();
        paintStat();
        S.html.range = null;          // 排序/过滤变了：行内容与顺序都变，必须重绘可见窗口
        paintRows();
        if (S.kickLive) S.kickLive();  // 排序/筛选后立即按实时值补齐（2026-11-13）
        if (!S.view.length) {
          const html = emptyHtml();
          if (S.html.empty !== html) { S.html.empty = html; S.el.rows.innerHTML = html; }
          S.html.rowsH = 'auto';
          S.el.rows.style.height = 'auto';
        }
      };

      // ------------------------------------------------------------ 取数
      const loadPool = async () => {
        const p = await api.get('/api/selection_pool').catch((e) => ({ __error: errText(e) }));
        if (p && !p.__error) {
          S.pool = p;
          S.poolSet = new Set(((p.items || []).map((x) => x.code)));
        }
        return p;
      };
      const load = async () => {
        if (!S.alive || S.loading) return;
        S.loading = true;
        try {
          const [sel, prefs, pool, opts, sc] = await Promise.all([
            api.get('/api/selection').catch((e) => ({ __error: errText(e) })),
            api.get('/api/selection_prefs').catch((e) => ({ __error: errText(e) })),
            api.get('/api/selection_pool').catch((e) => ({ __error: errText(e) })),
            api.get('/api/selection_board_options').catch((e) => ({ __error: errText(e) })),
            // 打分是**可缺依赖**：404（score 插件被拆）或产物未生成均降级成「无打分数据」
            api.get('/api/p/score/map').catch((e) => ({ __error: errText(e), __status: e && e.status })),
          ]);
          if (!S.alive) return;
          S.score = readScore(sc);
          S.data = sel && !sel.__error ? sel : { __error: (sel || {}).__error || '空响应', groups: [] };
          attachScoreRows(S.data, S.score);
          S.prefs = prefs && !prefs.__error ? prefs : null;
          applyPrefs(S.prefs);
          S.pool = pool && !pool.__error ? pool : { items: [], count: 0 };
          S.poolSet = new Set(((S.pool.items || []).map((x) => x.code)));
          S.opts = opts && !opts.__error ? opts : { favorites: [], catalog: [] };
          S.hotSet = null;
          S.html = Object.create(null);
          const gs = (S.data.groups || []);
          if (!gs.some((g) => g.key === S.group)) S.group = (gs[0] || {}).key || 'B1';
          // 市值默认值取快照 filters（旧 6085-6102 selMcapInit：localStorage 优先）
          // 2026-11-15（标注 N82）：优先服务端 prefs.mcap（跨设备/清缓存不丢），其次 localStorage，最后快照默认。
          const f = (S.data || {}).filters || {};
          S.mcap = {
            min: f.mcap_min == null ? '' : f.mcap_min,
            max: f.mcap_max == null ? '' : f.mcap_max,
          };
          if (P.mcap && (isNum(P.mcap.min) || isNum(P.mcap.max))) {
            S.mcap = { min: isNum(P.mcap.min) ? Number(P.mcap.min) : '', max: isNum(P.mcap.max) ? Number(P.mcap.max) : '' };
          } else {
            try {
              const saved = JSON.parse(localStorage.getItem(LS_MCAP) || 'null');
              if (saved && isNum(saved.min) && isNum(saved.max)) {
                S.mcap = { min: Number(saved.min), max: Number(saved.max) };
              }
            } catch (e) { /* 无 localStorage → 用服务端默认 */ }
          }
          S.el.vp.scrollTop = 0;
          // ETF 模式首屏（2026-11-11）：个股快照仍拉（供切回零等待），ETF 数据懒加载
          if (isEtf() && !S.etf) loadEtf();
          renderAll();
          const dt = Math.round(now() - t0);
          S.el.sub.title = '首屏渲染 ' + dt + ' ms · 原始行 ' + ((S.data.stats || {}).hits || 0)
            + ' · 分组 ' + ((S.data.stats || {}).groups || 0);
          root.setAttribute('data-paint-ms', String(dt));
          root.setAttribute('data-rows', String((S.view || []).length));
        } finally {
          S.loading = false;
        }
      };
      S.load = load;

      // ------------------------------------------------------------ 盘中现算（2026-11-04 主人：尾盘选股）
      // 流程：POST /api/shell/run-script {name} → core 起后台 python（selection_live_refresh.py）
      //       → 轮询 /api/selection_live 的 meta.generated_at 变化 → load()（/api/selection 内部自动选源）。
      // 不在插件里碰 subprocess（静态扫描禁令）；也不自己算——只触发 + 等产物。
      // ------------------------------------------------------------ 刷新范围菜单（2026-11-12 主人）
      // 右键「盘中现算」→ 勾选要刷新的部分：未勾选的组不重算、结果里沿用盘后定稿。
      // 为什么不用左键弹菜单：左键仍是“按上次勾选的范围直接刷”，少一步。
      const closeLiveMenu = () => {
        if (S.liveMenu && S.liveMenu.isConnected) S.liveMenu.remove();
        S.liveMenu = null;
        if (S.liveOutside) {
          document.removeEventListener('mousedown', S.liveOutside, true);
          S.liveOutside = null;
        }
      };
      const openLiveMenu = (x, y) => {
        closeLiveMenu();
        const m = div('sel-menu');
        m.innerHTML = '<div class="sel-menu-h">盘中现算 · 勾选要刷新的部分</div>'
          + LIVE_GROUPS.map((g) => '<label class="sel-menu-i"><input type="checkbox" data-g="'
            + esc(g) + '"' + (S.liveScope.indexOf(g) >= 0 ? ' checked' : '') + '>' + esc(g) + '</label>').join('')
          + '<div class="sel-menu-f">'
          + '<button type="button" class="sel-btn" data-mact="cancel">取消</button>'
          + '<button type="button" class="sel-btn primary" data-mact="go">开始现算</button></div>';
        m.style.left = Math.max(8, Math.min(x, window.innerWidth - 200)) + 'px';
        m.style.top = Math.max(8, Math.min(y, window.innerHeight - 320)) + 'px';
        document.body.append(m);
        S.liveMenu = m;
        m.addEventListener('change', (ev) => {
          const g = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-g') : '';
          if (!g) return;
          const i = S.liveScope.indexOf(g);
          if (ev.target.checked && i < 0) S.liveScope.push(g);
          if (!ev.target.checked && i >= 0) S.liveScope.splice(i, 1);
        });
        m.addEventListener('click', (ev) => {
          const act = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-mact') : '';
          if (act === 'cancel') { closeLiveMenu(); return; }
          if (act !== 'go') return;
          if (!S.liveScope.length) { showToast('至少勾选一个部分'); return; }
          try { localStorage.setItem(LS_LIVE, JSON.stringify(S.liveScope)); } catch (e) { /* 忽略 */ }
          // 同时落服务端：现算脚本按它决定只算哪几组（复用同一份筛选偏好，不新开文件）
          api.post('/api/selection_prefs', prefsPayload()).catch(() => { /* 落不上就按上次服务端值跑 */ });
          closeLiveMenu();
          if (typeof confirm === 'function'
              && !confirm('只刷新：' + S.liveScope.join('/') + '？\n约 2~3 分钟。')) return;
          liveRefresh(true);
        });
        const outside = (ev) => {
          if (S.liveMenu && S.liveMenu.contains(ev.target)) return;
          closeLiveMenu();
        };
        S.liveOutside = outside;
        setTimeout(() => document.addEventListener('mousedown', outside, true), 0);
      };
      S.openLiveMenu = openLiveMenu;

      const liveRefresh = async (skipConfirm) => {
        if (S.liveBusy) return;
        const scope = (S.liveScope && S.liveScope.length) ? S.liveScope : LIVE_GROUPS.slice();
        const scopeText = scope.length === LIVE_GROUPS.length
          ? ('全部 ' + scope.length + ' 组') : scope.join('/');
        if (!skipConfirm && typeof confirm === 'function'
            && !confirm('按当前行情重算选股（' + scopeText + '）？\n约 2~3 分钟（全市场取数 + 信号 + 9 列富化），期间页面可继续用。\n'
              + '右键「盘中现算」可以改要刷新的部分。')) return;
        S.liveBusy = true;
        const btnEl = S.el.btnLive;
        const t0 = now();
        let pollTimer = null;
        const setBtn = () => {
          if (!btnEl) return;
          btnEl.textContent = '现算中 ' + Math.round((now() - t0) / 1000) + 's';
        };
        const disarm = () => {
          if (btnEl) { btnEl.disabled = false; btnEl.classList.remove('is-live'); btnEl.textContent = '盘中现算'; }
        };
        if (btnEl) { btnEl.disabled = true; btnEl.classList.add('is-live'); }
        setBtn();
        const spin = setInterval(setBtn, 1000);
        const before = String(((S.data || {}).meta || {}).generated_at || '');
        const fail = (msg) => {
          clearInterval(spin);
          if (pollTimer) clearInterval(pollTimer);
          S.liveBusy = false;
          disarm();
          setStatus(msg, 'err');
        };
        let trigger = null;
        try {
          trigger = await api.post('/api/shell/run-script', { name: 'selection_live_refresh' });
        } catch (e) {
          trigger = { __error: errText(e) };
        }
        if (trigger && trigger.__error) { fail('触发盘中现算失败：' + trigger.__error); return; }
        if (trigger && trigger.ok === false && trigger.reason !== 'busy') {
          fail('触发失败：' + (trigger.detail || JSON.stringify(trigger)));
          return;
        }
        setStatus('盘中现算已启动（' + (trigger && trigger.reason === 'busy' ? '接续上一次在跑的' : '后台计算中')
          + '，范围 ' + scopeText + '），预计 2~3 分钟；算完本页自动刷新。', 'ok');
        const deadline = now() + LIVE_TIMEOUT_MS;
        pollTimer = setInterval(async () => {
          if (!S.alive) { clearInterval(pollTimer); return; }
          if (now() > deadline) {
            fail('盘中现算超时（>10 分钟未见新产物）——查「运行」页 ops_script.log');
            return;
          }
          let j = null;
          try { j = await api.get('/api/selection_live'); } catch (e) { j = null; }
          const gen = String(((j || {}).meta || {}).generated_at || '');
          if (!gen || gen === before) return;
          clearInterval(spin);
          clearInterval(pollTimer);
          S.liveBusy = false;
          disarm();
          setStatus('盘中结果已就绪（' + gen + '），正在重载…', 'ok');
          await load();
        }, LIVE_POLL_MS);
      };
      S.liveRefresh = liveRefresh;

      // ------------------------------------------------------------ 盘中实时涨幅（2026-11-12 标注 N89）
      // 主人：「涨幅之类的是实时数据，不需要额外获取数据，因为咱们的盘中页面本来就要获取快照」。
      // 所以这里**只覆盖「涨幅」这一列**：名单/信号/打分/防线一律不动，也不新增取数通道
      // （数据源 = /api/selection_quotes，后端走 core 的 eltdx 单例快照）。
      const LIVE_QUOTE_MS = 10000;
      const isTradingNow = () => {
        const d = new Date();
        const wd = d.getDay();
        if (wd === 0 || wd === 6) return false;
        const hm = d.getHours() * 100 + d.getMinutes();
        return (hm >= 925 && hm <= 1135) || (hm >= 1255 && hm <= 1505);
      };
      const liveQuotes = async () => {
        if (!S.alive || !isTradingNow() || !S.el.rows) return;
        const nodes = S.el.rows.querySelectorAll('.sel-r[data-code]');
        if (!nodes.length) return;
        const codes = [];
        for (let i = 0; i < nodes.length; i++) codes.push(nodes[i].getAttribute('data-code'));
        let j = null;
        try {
          j = await api.get('/api/selection_quotes?codes=' + encodeURIComponent(codes.join(',')));
        } catch (e) { return; }
        const q = (j && j.quotes) || {};
        const asof = (j && j.asof) || '';
        let hit = 0;
        for (let i = 0; i < nodes.length; i++) {
          const code = nodes[i].getAttribute('data-code');
          const row = q[code];
          if (!row) continue;
          const pct = Number(row.pct), last = Number(row.last), pre = Number(row.pre);
          if (!isFinite(pct) || !isFinite(last) || !isFinite(pre)) continue;
          const vr = (row.vr == null) ? null : Number(row.vr);
          const vol = Number(row.vol || 0);
          // 先写模型再生效 DOM：后续任何重绘（滚动/排序/切组/resize）都读这个值，
          // 不会再退回盘后快照里的昨日涨幅与昨日量比（2026-11-13）。
          S.livePct[code] = { pct, last, pre, vr: isFinite(vr) ? vr : null, vol, asof };
          const cell = nodes[i].querySelector('[data-k="pct"]');
          if (cell) {
            cell.innerHTML = numCell(pct, 2, true);
            cell.title = '实时 ' + last.toFixed(2) + '（昨收 ' + pre.toFixed(2) + '）· ' + asof;
          }
          const vc = nodes[i].querySelector('[data-k="vr"]');
          if (vc && vr != null && isFinite(vr)) {
            vc.innerHTML = numCell(vr, 2, false);
            vc.title = '盘中实时量比（累计 ' + vol.toFixed(0) + ' 手 / 前20日均量）· ' + asof;
          }
          hit += 1;
        }
        if (hit) { S.liveAt = asof; S.html.stat = ''; paintStat(); }
      };
      S.liveQuotes = liveQuotes;
      // 切组/排序/筛选/滚动落定后补一次：新进视口的票立即变实时，不等 10 秒轮询
      let liveKick = 0;
      let liveLast = 0;
      S.kickLive = () => {
        if (!S.alive || !isTradingNow()) return;
        if (liveKick) clearTimeout(liveKick);
        liveKick = setTimeout(() => {
          liveKick = 0;
          const t = Date.now();
          if (t - liveLast < 2000) return;    // 2s 内不重复拉同一批
          liveLast = t;
          liveQuotes();
        }, 220);
      };
      // 右键「盘中现算」→ 范围菜单（左键保持“按上次范围直接刷”）
      if (S.el.btnLive) {
        S.el.btnLive.addEventListener('contextmenu', (ev) => {
          ev.preventDefault();
          if (S.liveBusy) return;
          openLiveMenu(ev.clientX, ev.clientY);
        });
      }

      // ------------------------------------------------------------ 筛选偏好写（旧 6174-6190 selFSave，改：只以服务端为准）
      const savePrefs = () => {
        if (S.saveTimer) clearTimeout(S.saveTimer);
        S.saveTimer = setTimeout(async () => {
          if (!S.alive) return;
          const sent = prefsPayload();
          S.saving = true;
          try {
            await api.post('/api/selection_prefs', sent);
            // 回读核对：只比这 9 个字段，不一致就如实报（不静默）
            const back = await api.get('/api/selection_prefs');
            const a = JSON.stringify(pickPrefs(sent));
            const b = JSON.stringify(pickPrefs(back));
            if (a === b) setStatus('筛选偏好已保存并回读一致（' + hhmmss() + '）', 'ok');
            else setStatus('筛选偏好已保存，但回读与提交不一致 —— 服务端：' + b, 'err');
            showToast('筛选偏好已保存');
          } catch (e) {
            setStatus('保存筛选偏好失败：' + errText(e), 'err');
            showToast('保存筛选偏好失败');
          } finally {
            S.saving = false;
          }
        }, SAVE_DEBOUNCE_MS);
      };

      // ------------------------------------------------------------ 选股池写（行内二次点击，同 paper 卡）
      const armAction = (btnEl, idle, armText, idleCls, run) => {
        if (btnEl.getAttribute('data-arm') === '1') {
          btnEl.setAttribute('data-arm', '0');
          btnEl.className = idleCls;
          btnEl.textContent = idle;
          run();
          return;
        }
        btnEl.setAttribute('data-arm', '1');
        btnEl.className = 'sel-star armed';
        btnEl.textContent = armText;
        if (S.armTimer) clearTimeout(S.armTimer);
        S.armTimer = setTimeout(() => {
          if (!btnEl.isConnected) return;
          btnEl.setAttribute('data-arm', '0');
          btnEl.className = idleCls;
          btnEl.textContent = idle;
        }, ARM_MS);
      };
      const poolToggle = async (code, btnEl) => {
        if (S.submitting) return;
        const inPool = S.poolSet.has(code);
        const g = groupOf(S.group) || {};
        let row = null;
        ((S.data || {}).groups || []).forEach((gg) => {
          (gg.rows || []).forEach((r) => { if (!row && r.code === code) row = r; });
        });
        // 两个写端点都是字面量（静态扫描能直接查出端点集合，不靠拼字符串）
        const body = inPool ? { code } : {
          code,
          name: row ? row.name : '',
          group: row ? row.group : (g.key || ''),
          sig: row ? row.sig : '',
        };
        // 2026-11-13 主人：选股池 = 自选池的日期分组（MMDD）→ 写口改走 compat_watchlist，
        //   它是 watchlist.json 的**唯一写入者**（本插件不写，避免双写锁）。
        S.submitting = true;
        if (btnEl) btnEl.disabled = true;
        try {
          if (inPool) {
            await api.post('/api/watchlist/remove', { code: code });
          } else {
            const gid = (S.pool && S.pool.group_id) || '';
            await api.post('/api/watchlist/add', {
              kind: 'stock', code: code,
              name: row ? row.name : '', group: gid,
            });
          }
          // 以服务端为准：重拉池（条目/数量/分组回执全以 watchlist 现态为准）
          await loadPool();
          S.html = Object.create(null);
          paintTop();
          paintPanels();
          repaint();
          setStatus((inPool ? '已移出选股池 ' : '已加入选股池 ') + code + '（当前 '
            + (S.pool.count || 0) + ' 只）', 'ok');
          showToast((inPool ? '已移出 ' : '已加入 ') + code);
        } catch (e) {
          if (inPool) setStatus('移出选股池失败：' + errText(e), 'err');
          else setStatus('加入选股池失败：' + errText(e), 'err');
          showToast(inPool ? '移出失败' : '加入失败');
        } finally {
          S.submitting = false;
          if (btnEl && btnEl.isConnected) btnEl.disabled = false;
        }
      };

      // ------------------------------------------------------------ 事件委托（按钮随数据重绘，监听挂稳定节点）
      wrap.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;

        // 模式切换（2026-11-11）：个股 / ETF
        const md = t.closest('[data-mode]');
        if (md) {
          ev.stopPropagation();
          switchMode(md.getAttribute('data-mode'));
          return;
        }
        // ETF 分类切换
        const ec = t.closest('[data-etfcat]');
        if (ec) {
          const k = ec.getAttribute('data-etfcat');
          if (k && k !== S.etfCat) {
            S.etfCat = k;
            S.el.vp.scrollTop = 0;
            repaintEtf();
          }
          return;
        }
        // ETF 「只看买点 / 只看 B1」切换（两个 chip，再点同一个 = 取消）
        const sg = t.closest('[data-etfsig]');
        if (sg) {
          const k = sg.getAttribute('data-etfsig') || 'all';
          S.etfSigOnly = (S.etfSigOnly === k) ? '' : k;
          S.el.vp.scrollTop = 0;
          repaintEtf();
          return;
        }

        // 排序
        const sh = t.closest('[data-sort]');
        if (sh) {
          const key = sh.getAttribute('data-sort');
          S.sort = (S.sort.key === key) ? { key, dir: -S.sort.dir } : { key, dir: 1 };
          S.el.vp.scrollTop = 0;
          if (isEtf()) { paintHead(COLS_ETF); repaintEtf(); }
          else { paintHead(); repaint(); }
          return;
        }
        // 分组切换
        const gt = t.closest('[data-g]');
        if (gt) {
          const key = gt.getAttribute('data-g');
          if (key && key !== S.group) {
            S.group = key;
            S.panel = '';
            S.el.vp.scrollTop = 0;
            S.html = Object.create(null);
            renderAll();
          }
          return;
        }
        // 池 ★（行内二次点击）
        const pb = t.closest('[data-pool]');
        if (pb) {
          ev.stopPropagation();
          const code = pb.getAttribute('data-pool');
          const inPool = S.poolSet.has(code);
          const isPanel = !!pb.closest('.sel-pitem');
          const idle = isPanel ? '×' : (inPool ? '★' : '☆');
          const idleCls = 'sel-star' + (inPool ? ' on' : '');
          armAction(pb, idle, inPool ? '确认移出？' : '确认入池？', idleCls, () => { poolToggle(code, pb); });
          return;
        }
        // 筛选 chip
        const chip = t.closest('[data-chip]');
        if (chip) {
          const kind = chip.getAttribute('data-chip');
          if (kind === 'hot') {
            P.hot = { on: !(P.hot && P.hot.on) };
            savePrefs();
            paintFilterBar();
            repaint();
            return;
          }
          if (kind === 'ntype') {
            P.ntype = { on: !(P.ntype && P.ntype.on) };
            savePrefs();
            paintFilterBar();
            repaint();
            return;
          }
          // 2026-11-08 主人：KDJ / 均线这两个是「常用开关」，左键直接切，不再弹面板；
          //   参数（J 上限 / 白黄系数）走右键 —— 见下方 contextmenu。
          if (kind === 'kdj' || kind === 'ma') {
            const f = kind === 'kdj' ? 'kdjOn' : 'maOn';
            const cur2 = Object.assign({}, maOf(S.group));
            cur2[f] = !cur2[f];
            P.maBy = P.maBy || {};
            P.maBy[S.group] = cur2;
            if (S.group === 'B1') P.ma = Object.assign({}, P.ma || {}, cur2);
            savePrefs();
            S.el.vp.scrollTop = 0;
            paintFilterBar();
            repaint();
            return;
          }
          S.panel = (S.panel === kind) ? '' : kind;
          S.html.panels = null;
          paintPanels();
          paintTop();
          return;
        }
        // 面板内按钮
        const act = t.closest('[data-act]');
        if (act) {
          const a = act.getAttribute('data-act');
          if (a === 'refresh') { load(); return; }
          if (a === 'live') { liveRefresh(); return; }
          if (a === 'panel-close') {
            S.panel = '';
            paintPanels();
            paintTop();
            return;
          }
          if (a === 'mcap-apply') {
            applyMcap();
            return;
          }
          if (a === 'board-sync') {
            const favs = ((S.opts && S.opts.favorites) || []);
            if (!favs.length) { showToast('自选池里还没有收藏板块（自选池 → 板块组）'); return; }
            const have = new Set((P.boards || []).map((x) => x.code).filter(Boolean));
            let added = 0;
            favs.forEach((f) => {
              if (!f.code || have.has(f.code)) return;
              P.boards.push({ code: f.code, name: f.name || f.code, on: true });
              have.add(f.code);
              added++;
            });
            if (added) P.boardOn = true;
            savePrefs();
            S.html = Object.create(null);
            paintFilterBar();
            paintPanels();
            repaint();
            showToast(added ? ('已同步 ' + added + ' 个板块') : '清单已是最新');
            return;
          }
          if (a === 'board-add') {
            const inp = S.el.panels.querySelector('[data-boardadd-inp]');
            const raw = ((inp && inp.value) || '').trim();
            if (!raw) return;
            const hit = boardMatches(raw);
            const item = hit ? { code: hit.code, name: hit.name, on: true }
              : { code: '', name: raw, on: true };
            const dup = (P.boards || []).some((x) => (item.code && x.code === item.code)
              || (!item.code && x.name === item.name));
            if (!dup) P.boards.push(item);
            if (inp) inp.value = '';
            savePrefs();
            S.html = Object.create(null);
            paintFilterBar();
            paintPanels();
            repaint();
            if (!hit) showToast('「' + raw + '」不在二级行业/概念目录里，已按名称加入');
            return;
          }
          if (a === 'sig-all' || a === 'sig-none') {
            const sigs = (((S.data || {}).group_sigs || {})[S.group]) || [];
            if (a === 'sig-all') delete P.sigs[S.group];
            else P.sigs[S.group] = [];
            savePrefs();
            S.html = Object.create(null);
            paintFilterBar();
            paintTabs();
            paintPanels();
            repaint();
            return;
          }
        }
        // 收盘日快照切换
        const slot = t.closest('[data-slot]');
        if (slot) {
          const date = slot.getAttribute('data-slot');
          api.get('/api/selection_pool?date=' + encodeURIComponent(date))
            .then((j) => {
              if (!S.alive || !j) return;
              S.pool = j;
              S.poolSet = new Set(((j.items || []).map((x) => x.code)));
              S.html = Object.create(null);
              paintTop();
              paintPanels();
              repaint();
            })
            .catch((e) => setStatus('该日快照加载失败：' + errText(e), 'err'));
          return;
        }
      });

      // 面板内勾选 / 数字框（change 委托）
      const applyMcap = () => {
        const lo = S.el.fbar.querySelector('[data-mcap="min"]');
        const hi = S.el.fbar.querySelector('[data-mcap="max"]');
        const loV = lo ? parseFloat(lo.value) : NaN;
        const hiV = hi ? parseFloat(hi.value) : NaN;
        S.mcap = { min: isFinite(loV) ? loV : '', max: isFinite(hiV) ? hiV : '' };
        try { localStorage.setItem(LS_MCAP, JSON.stringify(S.mcap)); } catch (e) { /* 忽略 */ }
        // 2026-11-15（标注 N82）：同时落服务端偏好（localStorage 换设备/清缓存就丢）
        P.mcap = { min: S.mcap.min, max: S.mcap.max };
        savePrefs();
        S.el.vp.scrollTop = 0;
        paintFilterBar();
        repaint();
      };
      S.applyMcap = applyMcap;

      S.el.panels.addEventListener('change', (ev) => {
        const t = ev.target;
        if (!t || !t.getAttribute) return;
        const poolOpt = t.getAttribute('data-poolopt');
        if (poolOpt) {
          const set = new Set(P.pools || []);
          if (t.checked) set.add(poolOpt); else set.delete(poolOpt);
          P.pools = POOLS.map((p) => p.key).filter((k) => set.has(k));
          savePrefs();
          S.el.vp.scrollTop = 0;
          paintFilterBar();
          repaint();
          return;
        }
        if (t.getAttribute('data-boardenable')) {
          P.boardOn = !!t.checked;
          savePrefs();
          S.el.vp.scrollTop = 0;
          paintFilterBar();
          repaint();
          return;
        }
        const bOn = t.getAttribute('data-boardon');
        if (bOn != null) {
          const i = Number(bOn);
          if (P.boards[i]) P.boards[i].on = !!t.checked;
          savePrefs();
          paintFilterBar();
          repaint();
          return;
        }
        const sigOpt = t.getAttribute('data-sigopt');
        if (sigOpt) {
          const sigs = (((S.data || {}).group_sigs || {})[S.group]) || [];
          const cur = Array.isArray(P.sigs[S.group]) ? P.sigs[S.group].slice()
            : sigs.map((x) => x.code);
          const idx = cur.indexOf(sigOpt);
          if (t.checked && idx < 0) cur.push(sigOpt);
          if (!t.checked && idx >= 0) cur.splice(idx, 1);
          if (cur.length === sigs.length) delete P.sigs[S.group];
          else P.sigs[S.group] = sigs.map((x) => x.code).filter((c) => cur.indexOf(c) >= 0);
          savePrefs();
          S.el.vp.scrollTop = 0;
          paintFilterBar();
          paintTabs();
          repaint();
          return;
        }
        // 2026-11-02（标注 67/68）：KDJ / 均线各自开关与参数，统一写回本组
        const writeMa = (patch) => {
          const cur = Object.assign({}, maOf(S.group), patch);
          P.maBy = P.maBy || {};
          P.maBy[S.group] = cur;
          if (S.group === 'B1') P.ma = Object.assign({}, P.ma || {}, cur);
          savePrefs();
        };
        if (t.getAttribute('data-kdj-on')) {
          writeMa({ kdjOn: !!t.checked });
          S.el.vp.scrollTop = 0;
          paintFilterBar();
          repaint();
          return;
        }
        if (t.getAttribute('data-ma-on')) {
          writeMa({ maOn: !!t.checked });
          S.el.vp.scrollTop = 0;
          paintFilterBar();
          repaint();
          return;
        }
        const kdjKey = t.getAttribute('data-kdj');
        if (kdjKey) {
          const v = parseFloat(t.value);
          if (isFinite(v)) writeMa({ [kdjKey]: v });
          repaint();
          return;
        }
        const maKey = t.getAttribute('data-ma');
        if (maKey) {
          const v = parseFloat(t.value);
          if (isFinite(v)) writeMa({ [maKey]: v });
          repaint();
          return;
        }
        if (t.getAttribute('data-pin-on')) {
          P.pin.on = !!t.checked;
          savePrefs();
          S.el.vp.scrollTop = 0;
          paintFilterBar();
          repaint();
          return;
        }
        const pinKey = t.getAttribute('data-pin');
        if (pinKey) {
          const v = parseFloat(t.value);
          P.pin[pinKey] = isFinite(v) ? v : P.pin[pinKey];
          savePrefs();
          repaint();
        }
      });

      // 2026-11-08 主人：KDJ / 均线的**参数设置**走右键（左键已改为直接开关）。
      //   其余 chip 右键不动 —— 不抢浏览器原生菜单。
      wrap.addEventListener('change', (ev) => {
        // ETF 成交额下限（2026-11-11）：change 而非 input —— 输入中间值不反复重排全表
        const inp = ev.target;
        if (inp && inp.getAttribute && inp.getAttribute('data-etfmin') != null) {
          const v = parseFloat(inp.value);
          S.etfMin = (isFinite(v) && v >= 0) ? v : 0;
          S.el.vp.scrollTop = 0;
          repaintEtf();
        }
      });

      wrap.addEventListener('contextmenu', (ev) => {
        const t = ev.target;
        const chip = t && t.closest ? t.closest('[data-chip]') : null;
        if (!chip) return;
        const kind = chip.getAttribute('data-chip');
        if (kind !== 'kdj' && kind !== 'ma') return;
        ev.preventDefault();
        S.panel = kind;
        S.html.panels = null;
        paintPanels();
        paintTop();
      });

      // 板块清单删除（按钮，二次点击防误删）
      S.el.panels.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const del = t.closest('[data-boarddel]');
        if (!del) return;
        armAction(del, '×', '确认删？', 'x', () => {
          const i = Number(del.getAttribute('data-boarddel'));
          if (!isNaN(i)) P.boards.splice(i, 1);
          savePrefs();
          S.html = Object.create(null);
          paintFilterBar();
          paintPanels();
          repaint();
        });
      });

      // 市值输入框回车即应用（旧 6094 同款手感）
      // 2026-11-15（标注 N82）：补 change（失焦/数字框上下箭头）也应用——以前只有回车生效，
      //   用户输完直接点别处/刷新，值根本没进内存也没存，表现就是「设置没保存」。
      S.el.fbar.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' && ev.target && ev.target.getAttribute
          && ev.target.getAttribute('data-mcap')) {
          ev.preventDefault();
          applyMcap();
        }
      });
      S.el.fbar.addEventListener('change', (ev) => {
        const t = ev.target;
        if (t && t.getAttribute && t.getAttribute('data-mcap')) applyMcap();
      });

      // 视口尺寸变化（字号档 / 窗口）→ 重算可见窗口
      S.onResize = () => { if (S.alive) paintRows(); };
      if (typeof window !== 'undefined' && window.addEventListener) {
        window.addEventListener('resize', S.onResize);
      }

      await load();
      // 盘中实时涨幅（标注 N89）：拉一次，之后每 LIVE_QUOTE_MS 刷一次
      if (S.liveTimer) clearInterval(S.liveTimer);
      S.liveTimer = setInterval(() => { if (S.alive) liveQuotes(); }, LIVE_QUOTE_MS);
      if (S.alive) liveQuotes();
    },

    async unmount() {
      const S = this._sel;
      if (!S) return;
      S.alive = false;
      if (S.liveTimer) { clearInterval(S.liveTimer); S.liveTimer = null; }
      if (S.toastTimer) clearTimeout(S.toastTimer);
      if (S.armTimer) clearTimeout(S.armTimer);
      if (S.saveTimer) clearTimeout(S.saveTimer);
      if (S.raf) {
        const cancel = (typeof cancelAnimationFrame === 'function') ? cancelAnimationFrame : clearTimeout;
        cancel(S.raf);
        S.raf = 0;
      }
      if (S.onResize && typeof window !== 'undefined' && window.removeEventListener) {
        window.removeEventListener('resize', S.onResize);
      }
      S.html = Object.create(null);
      S.view = [];
      S.data = null;
      this._sel = null;
    },

    // 不声明 refreshEvery（T-1 盘后快照）；方法保留作契约占位，供手动/外部触发
    async refresh() {
      const S = this._sel;
      if (!S || !S.alive || S.loading || S.saving || S.submitting) return;
      await S.load();
    },
  }],
};
