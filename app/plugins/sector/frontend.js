/**
 * sector 卡片：板块轮动（照 8002 工作台重做）
 *
 * 主人 2026-10-25 定的形态：
 *   · 层级四档（二级/三级/概念/指数）
 *   · 概念不再分「题材/风格」（概念层当前只有 1 个板块）
 *   · 比较周期 1/3/5/10/20 天 + 日期双滑块 + 前后交易日步进
 *   · 视图只要 表格 / 网格 / 四象（折线、热力按主人要求去掉）
 *   · 一级行业目录、搜索、点行开详情卡
 *
 * 分层（改动面清晰，便于单测与定位）：
 *   frontend/compute.js  算（纯函数，与 8002 逐行对齐，已有守门测试）
 *   frontend/board.js    表与工具条
 *   frontend/views.js    网格与四象
 *   frontend/detail.js   详情卡
 *   本文件              状态、取数、编排
 *
 * 契约：只用 ctx.api.get()（shell 已加 /api/p/sector 前缀）；只操作自己的 DOM；
 *      卸载时移除监听与浮层，不留定时器。
 */

import { createCompute, LEVEL_NAME } from './frontend/compute.js';
import { el, seg, buildPanel, nextSort, renderCrumb, renderDirectory, esc, HM_CSS } from './frontend/board.js';
import { vizRows, renderGrid, renderQuad } from './frontend/views.js';
import { openDetail, mountDetail } from './frontend/detail.js';
import { injectCardCss } from './frontend/legacy.css.js';

// 壳层直调入口（2026-10-26）：嵌入旧页时 v2 是**单卡模式**，切卡会把用户正在看的卡换掉、
// 而父页 Tab 还停在原处，两边就会不同步。壳需要一份能脱离卡生命周期单独开弹窗的能力。
//
// **样式必须自己带**：板块卡的 CSS 是在 mount() 时注入的（legacy.css.js）；从壳层直调时
// 板块卡可能从未挂载过，不补这一步弹窗就是裸 DOM（实测：display:block、高度 8959px，
// 而卡内打开是 display:flex、980px）。injectCardCss 自己幂等，重复调无副作用。
export function openDetailStandalone(o) {
  injectCardCss(document);
  return openDetail(o);
}

// 视觉样式全部来自 legacy.css.js（从 8002 style.css 原样搬）。
// 本文件不再自带任何排版规则——两套混在一起就会出现「一半像新的、一半像旧的」。

const LEVEL_IDS = ['L2', 'L3', 'CONCEPT', 'INDEX'];
const LEVEL_SHORT = { L2: '二级', L3: '三级', CONCEPT: '概念', INDEX: '指数' };
const PERIODS = [1, 3, 5, 10, 20];
// 取数天数：与 8002 同口径 80 日（它 manifest.n_days=80 / show_start=20）。
// 原来取 250 日，L3 层返回 287KB、比 8002 的 92KB 大 3 倍，切层级时白花 0.6s。
// 80 日足够覆盖双滑块 + 20 天周期 + 在榜 30 日窗口。
const RECENT_DAYS = 80;

const VIEWS = [
  { v: 'table', t: '表格' },
  { v: 'grid', t: '网格' },
  { v: 'quad', t: '四象' },
];
const HIT_WIN = 30;

/**
 * 板块名归一化（仅用于「估值池行业名 → 本卡板块」的精确比对）：
 * 去空白、去尾部的罗马数字（Ⅱ/Ⅲ/IV）、全角括号转半角。
 * 不做模糊/子串匹配 —— 归一是为了让精确匹配多命中几个，不是为了猜。
 */
function normBoardName(s) {
  return String(s || '')
    .replace(/[\s\u3000]+/g, '')
    .replace(/\uff08/g, '(').replace(/\uff09/g, ')')
    .replace(/[\u2160-\u216b]+$/, '');
}

const card = {
  id: 'sector.board',
  title: '板块',
  slot: 'main',
  order: 10,
  // 板块数据是 T-1 收盘口径，盘中不会变；不声明 refreshEvery（避免无意义重算）。

  async mount(host, ctx) {
    this.host = host;
    this.ctx = ctx;
    this.destroyed = false;
    this.level = 'L2';
    this.view = 'table';
    this.sortKey = 'd';
    this.sortDir = -1;
    this.winN = 3;
    this.vizTopQ = 10;     // 四象档位：10 / 20 / 'all'（8002 state.vizTopQ，每侧条数）
    this.dayA = null;      // 基准日索引（null = 跟随 dayB - winN）
    this.dayB = null;
    this.drillL1 = null;
    this.search = '';
    this.idxSel = 'ALL';    // 指数层右栏选中项（默认全A，照 8002 state.idxSel）
    this.flowMap = {};
    this.catalog = null;
    this.rankRecent = null;
    this.C = null;
    this._seq = 0;
    injectCardCss(document);
    // 色格图的 6 档色阶是 board.js 声明的局部 token（HM_CSS），必须随卡注入，
    // 否则浮层里的 --hm-b1..b6 全部失效（空格子）。
    if (!document.getElementById('sector-hm-css')) {
      const hs = document.createElement('style');
      hs.id = 'sector-hm-css';
      hs.textContent = HM_CSS;
      document.head.appendChild(hs);
    }
    this._build();
    await this._loadAll();
    // 入参（2026-10-31）：估值页点行业 → 壳的 panel:open-card{args:{industry}} → 本卡。
    // 只做「切层级 + 滚动到该行 + 短暂描边」；找不到同名板块就只开卡不定位。
    const argInd = (ctx && ctx.card && ctx.card.args && ctx.card.args.industry) || '';
    if (argInd) await this._focusIndustry(String(argInd));
  },

  async unmount() {
    this.destroyed = true;
    this._tipHide();
    if (this._detailInst) { this._detailInst.destroy(); this._detailInst = null; }
    if (this._focusLine) { this._focusLine.remove(); this._focusLine = null; }
    if (this._tipEl && this._tipEl.parentNode) this._tipEl.remove();
    this._tipEl = null;
    this.host = null;
    this.ctx = null;
    this.C = null;
    this.catalog = null;
    this.rankRecent = null;
    this.flowMap = {};
    this._rrCache = null;
    this._flowCache = null;
  },

  async refresh() {
    await this._loadAll();
  },

  /**
   * 入参定位（2026-10-31）：把「估值池的行业名」落到本卡的一个板块上。
   *
   * 口径差异是固定的：估值池行业来自东财/申万类分类（如「航海装备Ⅱ」「炼化及贸易」），
   * 本卡目录是 AMV 板块名（如「航海装备」「油品石化贸易」）。所以先归一化再**精确**比对：
   * 去空白、去罗马数字尾缀、全角括号转半角。不做模糊匹配 —— 「航运港口」猜成「航运」或「港口」
   * 只会把用户带到错的板块，宁可不定位。
   *
   * 命中后：切到该板块所属层级 → 重取该层 → 滚动到行并短暂描边。不弹窗、不改榜单口径。
   */
  async _focusIndustry(name) {
    if (this.destroyed || !name) return;
    const want = normBoardName(name);
    const cat = this.catalog || {};
    const all = [].concat(cat.l2 || [], cat.l3 || [], cat.concept || [], cat.index || []);
    const hit = all.find((m) => m && normBoardName(m.name) === want) || null;
    if (!hit) {
      this._focusNote('未找到同名板块「' + name + '」（估值池行业名与板块目录口径不同，未定位）');
      return;
    }
    const lv = String(hit.level || '').toUpperCase();
    const needLevel = LEVEL_IDS.indexOf(lv) >= 0 && lv !== this.level;
    // L2/L3 板块带一级行业归属：把它也作为筛选条件，定位命中率明显高于只看榜单
    const nextDrill = (hit.level === 'L2' || hit.level === 'L3') ? (hit.l1 || null) : null;
    const needDrill = nextDrill !== this.drillL1;
    if (needLevel || needDrill) {
      if (needLevel) this.level = lv;
      this.drillL1 = nextDrill;
      this._paintControls();
      await this._loadAll();          // 内部会 _render()
    } else {
      this._render();
    }
    if (this.destroyed) return;
    const row = this.body ? this.body.querySelector('li.row[data-code="' + hit.code + '"]') : null;
    if (!row) {
      // 不在前/后 10% 榜单里：把目标摆到搜索框（该卡自带能力），点结果即可开详情。
      // 不新建弹窗、不伪造榜单条目。
      if (this.searchInput) {
        this.searchInput.value = hit.name;
        this._onSearch();
      }
      const lvTxt = needLevel ? ('（已切到' + (LEVEL_SHORT[lv] || lv) + '层）') : '';
      this._focusNote('板块「' + hit.name + '」不在前/后 10% 榜单内' + lvTxt + '，已在搜索框里列出，点结果可打开详情');
      return;
    }
    try { row.scrollIntoView({ block: 'center' }); } catch (err) { /* 不影响定位本身 */ }
    row.style.boxShadow = 'inset 0 0 0 2px var(--accent)';
    setTimeout(function () { try { row.style.boxShadow = ''; } catch (err) { /* 节点已卸载 */ } }, 2400);
    this._focusNote('已定位到板块「' + hit.name + '」');
  },

  /** 入参定位的结果说明：工具条下一行小字，不用 toast、不弹窗。 */
  _focusNote(text) {
    if (!this.root) return;
    if (!this._focusLine) {
      this._focusLine = el('div');
      this._focusLine.style.cssText = 'font-size:12px;color:var(--dim);padding:0 0 6px';
      const anchor = this.controls && this.controls.parentNode ? this.controls : null;
      if (anchor) anchor.parentNode.insertBefore(this._focusLine, anchor.nextSibling);
      else this.root.insertBefore(this._focusLine, this.root.firstChild);
    }
    this._focusLine.textContent = text;
  },

  // ------------------------------------------------------------ 骨架

  _build() {
    const root = el('div', { class: 'sector-card' });
    this.root = root;

    // 工具栏：结构与类名照 8002（.controls > .ctrl-row），样式表从那边原样搬来。
    this.controls = el('div', { class: 'controls' });
    const row1 = el('div', { class: 'ctrl-row' });
    this.levelSeg = el('span');
    this.searchBox = el('div', { class: 'searchbox' });
    this.searchInput = el('input', {
      type: 'search',
      placeholder: '搜索板块 / 概念…',
      'aria-label': '搜索板块或概念',
      autocomplete: 'off',
    });
    this.searchDrop = el('div', { class: 'search-drop', hidden: '' });
    this.searchInput.addEventListener('input', () => this._onSearch());
    this.searchInput.addEventListener('focus', () => this._onSearch());
    this.searchInput.addEventListener('blur', () => {
      // 延迟关闭：让下拉项的 click 先触发
      setTimeout(() => { if (this.searchDrop) this.searchDrop.hidden = true; }, 140);
    });
    this.searchBox.append(this.searchInput, this.searchDrop);

    this.periodSeg = el('span');
    this.btnPrev = el('button', { type: 'button', class: 'step', 'aria-label': '前一交易日', title: '前一交易日' }, '◀');
    this.dateAEl = el('span', { class: 'rr-date' }, '—');
    this.dateArrow = el('span', { class: 'rr-arrow' }, '→');
    this.dateBEl = el('span', { class: 'rr-date cur' }, '—');
    this.btnNext = el('button', { type: 'button', class: 'step', 'aria-label': '后一交易日', title: '后一交易日' }, '▶');
    this.btnPrev.addEventListener('click', () => this._stepDay(-1));
    this.btnNext.addEventListener('click', () => this._stepDay(1));
    this.viewSeg = el('span');

    /* 结构标签筛选（2026-10-27 主人要）——照需求：「根据徽标筛选显示的板块」。
     * 单选取一（不做多选组合：多选后「量价齐升+结构偏弱」这种互斥组合会让人误以为有含义）。
     * 选中后只影响**显示**，不改排序、不改榜单口径、不重算任何指标。
     * 标签需 stock_state（近 31 日），超出覆盖时整排禁用并标出原因。 */
    this.stFilter = 'all';
    this.stSeg = el('span', { class: 'st-seg' });
    row1.append(this.levelSeg, this.searchBox, this.periodSeg,
      this.btnPrev, this.dateAEl, this.dateArrow, this.dateBEl, this.btnNext,
      this.viewSeg, this.stSeg);

    // 区间微调（双滑块，折叠，8002 .advbox）
    this.adv = el('details', { class: 'advbox' });
    this.adv.append(el('summary', null, '区间微调（双滑块）'));
    const advRow = el('div', { class: 'adv-body' });
    this.btnFirst = el('button', { type: 'button', class: 'step', title: '最早交易日' }, '⏮');
    this.btnLast = el('button', { type: 'button', class: 'step', title: '最新交易日' }, '⏭');
    this.btnFirst.addEventListener('click', () => this._jumpEdge(false));
    this.btnLast.addEventListener('click', () => this._jumpEdge(true));
    const dual = el('div', { class: 'dual' });
    this.dualTrack = el('div', { class: 'track' });
    this.dualFill = el('div', { class: 'fill' });
    this.dualGrab = el('div', { class: 'grab', role: 'slider', tabindex: '0' });
    this.rangeA = el('input', { type: 'range', 'aria-label': '基准日（区间起点）' });
    this.rangeB = el('input', { type: 'range', 'aria-label': '当前日（区间终点）' });
    this.rangeA.addEventListener('input', () => this._onRange('A'));
    this.rangeB.addEventListener('input', () => this._onRange('B'));
    dual.append(this.dualTrack, this.dualFill, this.dualGrab, this.rangeA, this.rangeB);
    advRow.append(this.btnFirst, dual, this.btnLast);
    this.adv.append(advRow);
    this.controls.append(row1, this.adv);

    // 一级行业目录（折叠，8002 .l1box）
    this.l1Box = el('details', { class: 'l1box' });
    this.l1Summary = el('summary', null, '一级行业 · 全部');
    this.l1Box.append(this.l1Summary);
    this.l1Dir = el('div', { class: 'dir' });
    this.crumb = el('div', { class: 'crumb' });
    const l1Body = el('div', { class: 'l1-body' });
    l1Body.append(this.crumb, this.l1Dir);
    this.l1Box.append(l1Body);

    this.status = el('div', { class: 'meta' }, '加载中…');
    this.body = el('div');
    this.note = el('div', { class: 'foot' });
    this.note.innerHTML = '<b>口径</b>：板块 = 同花顺 THS 行业（二级 / 三级）+ 概念；'
      + '活跃市值涨跌幅 = 0AMV 引擎（<b>不复权</b>）环比；真实涨跌幅 = THS 板块指数环比'
      + '（<b>沪深全A 例外</b>：走通达信 <b>880008 全A等权</b>日线，与实时卡/同花顺一致）。'
      + '<b>权重</b> = 板块活跃市值 ÷ 大盘活跃市值；<b>权重变化</b> 单位 <b>pp</b>（百分点，非 %）；'
      + '<b>活跃涨跌</b> = 活跃市值区间涨跌幅（%）。'
      + '<br><b>读法</b>：<b>权重变化</b> = 板块在活跃市值总量里的<b>份额迁移</b>'
      + '（<b>相对</b>量：正 = 比大盘强，<b>不等于上涨</b>，也不等于钱流入）；'
      + '<b>活跃涨跌</b> 答「该板块自己涨跌多少」（<b>绝对</b>）。'
      + '<b>方向由同一个数决定</b>：权重变化为正 ⇔ 活跃涨跌 > 大盘活跃涨跌，'
      + '所以「权重涨 + 活跃跌」只在<b>大盘活跃市值下跌</b>时出现，不是独立结构信号。'
      + '　<b>在榜</b> = 近 30 交易日进过同侧榜（前 / 后 10%）的天数。'
      + '<br><b>驱动徽标</b>：<b>文字=驱动类型，颜色=价格方向</b>（红涨 / 绿跌）。'
      + '只比两个百分比——<b>AMV 涨幅</b>与<b>价格涨幅</b>（同窗口、同单位）：'
      + '<b>齐升</b>（AMV↑价↑）/ <b>虚涨</b>（AMV↑价↓，换手抬起来的）/ '
      + '<b>独涨</b>（AMV↓价↑，换手在退）/ <b>齐跌</b>（AMV↓价↓）。'
      + '<b>虚涨</b>用橙色强调——活跃市值在涨但价格在跌，<b>即使站上多头区间也不可信</b>。'
      + '悬停可看两个百分比和差额（差额 = 换手贡献，pp）。结构观察，不是买卖信号。';

    root.append(this.controls, this.l1Box, this.status, this.body, this.note);
    this.host.append(root);

    this._paintControls();
  },

  /** 重画工具条（层级/周期/视图高亮）。任何改 state 的路径都必须紧跟一次，
   *  否则按钮高亮会停在旧值 —— 点了生效、但按钮不动，比不生效更难判断。 */
  _paintControls() {
    // 层级
    this.levelSeg.textContent = '';
    this.levelSeg.append(seg(
      LEVEL_IDS.map((id) => ({ v: id, t: LEVEL_SHORT[id] })),
      this.level,
      (v) => { this.level = v; this.drillL1 = null; this._paintControls(); this._loadAll(); },
    ));
    // 周期
    this.periodSeg.textContent = '';
    this.periodSeg.append(seg(
      PERIODS.map((n) => ({ v: n, t: n + '天' })),
      this.winN,
      (v) => this._setWindow(v),
    ));
    // 视图
    this.viewSeg.textContent = '';
    this.viewSeg.append(seg(VIEWS, this.view, (v) => { this.view = v; this._paintControls(); this._render(); }));

    /* 结构标签筛选（单选取一）。
     * 覆盖不足（超出 stock_state 的 31 日）时整排禁用并解释原因，
     * 而不是留一排点了没反应的按钮。 */
    this._paintStFilter();

    // 一级目录显隐：行业层（申万一级）与概念层（四组）都有归属
    this.l1Box.hidden = !this._hasL1();
  },

  /** 当前层的目录键：L2→l2 / L3→l3 / CONCEPT→concept；INDEX 无一级归属。 */
  _catKey(level) {
    const lv = level || this.level;
    return lv === 'L2' ? 'l2' : lv === 'L3' ? 'l3' : lv === 'CONCEPT' ? 'concept' : null;
  },

  _hasL1() {
    return !!this._catKey();
  },

  /** 结构标签筛选栏（2026-10-27）。
   *  只在「表格」视图且非指数层可用（网格/四象不筛，避免与它们的 Top-N 截断叠成两层）。
   *  当天没算出任何标签（超出 31 日覆盖）→ 禁用 + 提示。 */
  _paintStFilter() {
    if (!this.stSeg) return;
    this.stSeg.textContent = '';
    const idxMode = this.level === 'INDEX' || this.view !== 'table';
    /* 可用性判据：不是「有没有算过分项」，而是「今天有没有可标的异常」。
     * 全都正常的一天（无巨量档）筛选栏就没意义，直接禁用并说明原因。 */
    let nTagged = 0;
    let nHit = 0;
    for (const k of Object.keys(this.flowMap || {})) {
      const st = (this.flowMap[k] || {}).st;
      if (st) {
        nTagged++;
        if (st.label) nHit++;
      }
    }
    const noData = nTagged === 0;
    const disabled = idxMode || noData;

    /* 四类短名：齐升 / 虚涨 / 独涨 / 齐跌。
     * 2026-10-27 主人定：只比 AMV 涨幅与价格涨幅两个百分比，不分项。
     * 因此不再依赖 stock_state（股本快照），**历史日期也能算**。 */
    const items = [{ v: 'all', t: '全部' }].concat(
      ['虚涨', '齐升', '齐跌', '独涨'].map((t) => ({ v: t, t })),
    );
    const box = seg(items, disabled ? 'all' : this.stFilter, (v) => {
      if (disabled) return;
      this.stFilter = v;
      this._paintControls();
      this._render();
    });
    if (disabled) {
      box.classList.add('is-off');
      box.title = idxMode
        ? '驱动徽标只在「表格」视图的板块层可用'
        : '该日缺 AMV 或价格数据';
    } else {
      box.title = '按 AMV 驱动筛选：虚涨（AMV涨但价格跌）/ 齐升 / 齐跌 / 独涨'
        + '。徽标颜色表示价格方向：红涨 / 绿跌。';
    }
    this.stSeg.append(box);
  },

  // ------------------------------------------------------------ 取数

  async _api(path) {
    return this.ctx.api.get(path);
  },

  async _loadAll() {
    const seq = ++this._seq;
    this.status.textContent = '加载中…';
    this.status.style.color = '';
    try {
      if (!this.catalog) this.catalog = await this._api('/catalog');
      // 层级数据缓存在卡内：来回切层级时不重复拉（8002 就是这样才快）。
      // 后端也有一层缓存（键带最新交易日），两层各管一段：
      //   卡内缓存管「本次会话内来回切」；后端缓存管「别的卡/刷新也命中」。
      if (!this._rrCache) this._rrCache = new Map();
      let rr = this._rrCache.get(this.level);
      if (!rr) {
        rr = await this._api('/rank_recent?level=' + encodeURIComponent(this.level) + '&days=' + RECENT_DAYS);
        if (!rr || !Array.isArray(rr.dates) || !rr.dates.length) {
          if (!this.destroyed && seq === this._seq) {
            this.status.textContent = '该层级暂无排行数据';
            this.body.textContent = '';
          }
          return;
        }
        this._rrCache.set(this.level, rr);
      }
      if (this.destroyed || seq !== this._seq) return;
      this.rankRecent = rr;
      const last = rr.dates.length - 1;
      // 换层级后日期索引可能越界（L3 与 L2 交易日相同，但防御一下）：越界就回最新日
      if (this.dayB == null || this.dayB > last) this.dayB = last;
      if (this.dayA == null || this.dayA > this.dayB) this.dayA = Math.max(0, this.dayB - this.winN);
      await this._loadFlow(seq);
      if (this.destroyed || seq !== this._seq) return;
      if (this.destroyed || seq !== this._seq) return;
      this._setStatus();
      this._syncSliders();
      this._renderDir();
      this._paintStFilter();   // flowMap 到位后重画：筛选栏依赖标签数据判断可用性
      this._render();
    } catch (err) {
      if (this.destroyed) return;
      this.status.textContent = '加载失败：' + (err && err.message ? err.message : err);
      this.status.style.color = 'var(--danger-ink)';
    }
  },

  async _loadFlow(seq) {
    const date = this.rankRecent.dates[this.dayB];
    const period = this._effectiveWin();
    const key = this.level + '|' + period + '|' + (date || '');
    if (!this._flowCache) this._flowCache = new Map();
    const hit = this._flowCache.get(key);
    if (hit) {
      this.flowMap = hit;
      return;
    }
    const q = '/board_flow?period=' + period + '&level=' + encodeURIComponent(this.level)
      + (date ? '&date=' + encodeURIComponent(date) : '');
    let d;
    try { d = await this._api(q); } catch (err) { d = null; }
    if (this.destroyed || (seq != null && seq !== this._seq)) return;
    const map = {};
    if (d && !d.error) {
      const fc = d.flow_calc || {}, fm = d.flow_main || {}, md = d.main_days || {};
      const keys = new Set([].concat(Object.keys(fc), Object.keys(d.bull || {}), Object.keys(d.real_pct || {}),
        Object.keys(d.st || {})));
      for (const k of keys) {
        map[k] = {
          net: fc[k], main: fm[k], main_days: md[k],
          real_pct: (d.real_pct || {})[k],
          amv_pct: (d.amv_pct || {})[k],
          diverge: (d.diverge || {})[k],
          bull: (d.bull || {})[k],
          st: (d.st || {})[k],
        };
      }
    }
    this.flowMap = map;
    if (map && Object.keys(map).length) {
      // 空结果不入缓存（后端刚重启或当日无数据时，不要把空结果固定下来）
      if (this._flowCache.size > 80) this._flowCache.clear();
      this._flowCache.set(key, map);
    }
  },

  _effectiveWin() {
    const n = this.dayB - this.dayA;
    return n > 0 ? n : 1;
  },

  _setStatus() {
    const n = this._effectiveWin();
    // 板块数按「候选池」口径：排掉 exclude，钻取时只数该一级行业内。
    // 不拿 catalog 猜全层数 —— 层级刚切或钻取时，猜出来的数与榜单对不上（实测：钻取电子后仍报 91）。
    const nCodes = this._poolSize();
    const unit = this.level === 'INDEX' ? ' 个指数' : ' 个板块';
    const bits = [
      (this.rankRecent.dates[this.dayB] || '—'),
      '周期 ' + n + ' 天',
      nCodes + unit,
    ];
    if (this.drillL1) bits.push('已筛 ' + this.drillL1);
    this.status.textContent = bits.join(' · ');
  },

  /** 当前候选池的板块数（与 compute 的候选池同口径）。 */
  _poolSize() {
    if (!this.rankRecent || !this.rankRecent.codes) return 0;
    const key = this.level === 'CONCEPT' ? 'concept' : this.level.toLowerCase();
    const meta = (this.catalog && this.catalog[key]) || [];
    const byCode = new Map(meta.map((m) => [m.code, m]));
    let n = 0;
    for (const c of this.rankRecent.codes) {
      const m = byCode.get(c);
      if (m && m.exclude) continue;
      if (this.drillL1 && m && m.l1 !== this.drillL1) continue;
      n++;
    }
    return n;
  },

  _syncSliders() {
    const rr = this.rankRecent;
    if (!rr) return;
    const lo = 0, hi = rr.dates.length - 1;
    this.rangeA.min = String(lo); this.rangeA.max = String(hi);
    this.rangeB.min = String(lo); this.rangeB.max = String(hi);
    this.rangeA.value = String(this.dayA);
    this.rangeB.value = String(this.dayB);
    this.dateAEl.textContent = rr.dates[this.dayA] || '—';
    this.dateBEl.textContent = rr.dates[this.dayB] || '—';
    const pct = (v) => ((v - lo) / (hi - lo)) * 100;
    const l = pct(this.dayA), r = pct(this.dayB);
    this.dualFill.style.left = l + '%';
    this.dualFill.style.width = Math.max(0, r - l) + '%';
    if (r - l < 1.2) { this.dualGrab.style.left = 'calc(' + l + '% - 6px)'; this.dualGrab.style.width = '12px'; }
    else { this.dualGrab.style.left = l + '%'; this.dualGrab.style.width = (r - l) + '%'; }
    this.dualGrab.setAttribute('aria-valuemin', String(lo));
    this.dualGrab.setAttribute('aria-valuemax', String(hi));
    this.dualGrab.setAttribute('aria-valuenow', String(this.dayA));
    this.btnPrev.disabled = this.dayB <= lo;
    this.btnNext.disabled = this.dayB >= hi;
    this.btnFirst.disabled = this.dayB === lo && this.dayA === lo;
    this.btnLast.disabled = this.dayB === hi;
  },

  /** 一级目录与面包屑（8002 renderDirectory / renderCrumb 的结构逐字）。
   *  概念层（2026-11-14）的 l1 = 四组（题材/风格/政策区域/自建），按固定组序排；
   *  行业层仍按申万一级行业名排序。 */
  _renderDir() {
    if (!this._hasL1()) { this.l1Box.hidden = true; return; }
    this.l1Box.hidden = false;
    const isConcept = this.level === 'CONCEPT';
    const GROUP_ORDER = ['题材', '风格', '政策区域', '自建'];
    const l1s = Array.from(new Set(
      ((this.catalog && this.catalog[this._catKey()]) || [])
        .filter((m) => m && m.l1 && !m.exclude)
        .map((m) => m.l1),
    )).sort((a, b) => (isConcept
      ? (GROUP_ORDER.indexOf(a) + 1 || 99) - (GROUP_ORDER.indexOf(b) + 1 || 99)
      : a.localeCompare(b, 'zh')));
    const pick = (val) => {
      this.drillL1 = val;
      this._renderDir();
      this._setStatus();
      this._render();
    };
    renderDirectory(this.l1Dir, l1s, this.drillL1, pick);
    renderCrumb(this.crumb, this.drillL1, pick);
    this.l1Summary.textContent = (isConcept ? '分组 · ' : '一级行业 · ')
      + (this.drillL1 || '全部') + '（' + l1s.length + '）';
  },

  // ------------------------------------------------------------ 交互

  _setWindow(n) {
    this.winN = n;
    this.dayA = Math.max(0, this.dayB - n);
    this._paintControls();
    this._syncSliders();
    this._recompute({ reloadFlow: true });
  },

  _onRange(which) {
    if (which === 'A') this.dayA = Math.min(Number(this.rangeA.value), this.dayB);
    else this.dayB = Math.max(Number(this.rangeB.value), this.dayA);
    this.winN = this._effectiveWin();
    this._paintControls();
    this._syncSliders();
    this._recompute({ reloadFlow: true });
  },

  _stepDay(delta) {
    if (!this.rankRecent) return;
    const hi = this.rankRecent.dates.length - 1;
    const nb = Math.min(hi, Math.max(0, this.dayB + delta));
    if (nb === this.dayB) return;
    this.dayB = nb;
    this.dayA = Math.max(0, nb - this.winN);
    this._syncSliders();
    this._recompute({ reloadFlow: true });
  },

  _jumpEdge(toLast) {
    if (!this.rankRecent) return;
    const hi = this.rankRecent.dates.length - 1;
    this.dayB = toLast ? hi : 0;
    this.dayA = Math.max(0, this.dayB - this.winN);
    this._syncSliders();
    this._recompute({ reloadFlow: true });
  },

  /** 搜索下拉（8002 `doSearch` 的 .sd-item 结构逐字：.sd-lv 层级胶囊 + .sd-nm 名称 + .sd-extra 归属）。 */
  _onSearch() {
    const kw = (this.searchInput.value || '').trim();
    if (!kw) { this.searchDrop.hidden = true; this.searchDrop.innerHTML = ''; return; }
    const cat = this.catalog || {};
    const all = [].concat(cat.l2 || [], cat.l3 || [], cat.concept || [], cat.index || []);
    const LV_TAG = { L1: '一级', L2: '二级', L3: '三级', CONCEPT: '概念', INDEX: '指数' };
    const hit = all.filter((m) => m && (String(m.name || '').indexOf(kw) >= 0 || String(m.code || '').indexOf(kw) >= 0)).slice(0, 12);
    if (!hit.length) {
      this.searchDrop.innerHTML = '<div class="sd-empty">无匹配板块</div>';
    } else {
      this.searchDrop.innerHTML = hit.map((x) => (
        '<button class="sd-item" data-lv="' + esc(x.level) + '" data-code="' + esc(x.code)
        + '" data-name="' + esc(x.name) + '">'
        + '<span class="sd-lv lv-' + esc(String(x.level).toLowerCase()) + '">'
        + (LV_TAG[x.level] || x.level) + '</span>'
        + '<span class="sd-nm">' + esc(x.name) + '</span>'
        + '<span class="sd-extra">' + (x.l1 ? esc(x.l1) : '')
        + (x.n_members != null ? ' · ' + x.n_members + '只' : '') + '</span>'
        + '</button>')).join('');
      this.searchDrop.querySelectorAll('.sd-item').forEach((b) => {
        b.addEventListener('mousedown', (ev) => ev.preventDefault());
        b.addEventListener('click', () => {
          this.searchDrop.hidden = true;
          this.searchInput.value = b.dataset.name;
          this._openDetail(b.dataset.code, b.dataset.name, b.dataset.lv);
        });
      });
    }
    this.searchDrop.hidden = false;
  },

  /** 表头排序：点同键翻转，换键回默认降序。 */
  _onSort(key) {
    const n = nextSort(this.sortKey, this.sortDir, key);
    this.sortKey = n.key;
    this.sortDir = n.dir;
    this._render();
  },

  /** 重算：flowMap 变了要重取，其余只重渲染。 */
  async _recompute(opts) {
    const needFlow = !!(opts && opts.reloadFlow);
    if (needFlow) {
      const seq = ++this._seq;
      await this._loadFlow(seq);
      if (this.destroyed || seq !== this._seq) return;
    }
    this._setStatus();
    this._render();
  },

  // ------------------------------------------------------------ 渲染

  _buildCompute() {
    this.C = createCompute({
      catalog: this.catalog,
      rankRecent: this.rankRecent,
      level: this.level,
      sortKey: this.sortKey === 'rank' || this.sortKey === 'score' || this.sortKey === 'weight'
        || this.sortKey === 'amv_pct' || this.sortKey === 'real_pct' || this.sortKey === 'main'
        ? this.sortKey : 'd',
      winN: this._effectiveWin(),
      drillL1: this.drillL1,
      flowMap: this.flowMap,
      divMap: this.flowMap,
      dayB: this.dayB,
    });
    return this.C;
  },

  _render() {
    if (this.destroyed || !this.rankRecent) return;
    const C = this._buildCompute();
    this.body.textContent = '';
    const name = LEVEL_NAME[this.level] || this.level;
    const idxMode = this.level === 'INDEX';
    const lists = C.computeBoardLists(this.dayB);

    if (this.view === 'table') {
      // 指数层（照 8002 setIdxPanels/renderIdxDetail）：左栏全列指数 + 右栏内联走势与数据
      if (idxMode) {
        this._renderIdx();
        return;
      }
      const wrap = el('div', { class: 'panels' });
      const st = { dayB: this.dayB, winN: this._effectiveWin(), drillL1: this.drillL1, sortKey: this.sortKey, sortDir: this.sortDir };
      const common = {
        C, state: st, flowMap: this.flowMap, idxMode,
        onSort: (k) => this._onSort(k),
        onOpen: (code, nm) => this._openDetail(code, nm, this.level),
        tipShow: (t, ev, moveOnly, hit) => this._tipShow(t, ev, moveOnly, hit),
        tipHide: () => this._tipHide(),
        stFilter: this.stFilter,
      };
      wrap.append(
        buildPanel(Object.assign({}, common, { side: 'in', title: name + ' · 活跃市值变化 前10%' })),
        buildPanel(Object.assign({}, common, { side: 'out', title: name + ' · 活跃市值变化 后10%' })),
      );
      this.body.append(wrap);
      return;
    }

    if (lists.warmup) {
      this.body.append(el('div', { class: 'rb-empty' }, '该日历史不足 ' + this._effectiveWin() + ' 天（窗口未填满），请往后选日期'));
      return;
    }
    // 指数层：工作台本来就是「全列指数」（不切 10%）⇒ 单栅全列（网格/四象同样全量）
    const idxRows = idxMode ? C.computeAllRows(this.dayB).rows.map((r) => Object.assign({}, r, { side: 'in' })) : null;
    // 四象按 Top N 档位截断（8002 sliceTop：N 是每侧条数，退出行不显示）
    let scope = lists;
    if (!idxMode && this.view === 'quad') {
      const k = (this.vizTopQ === 'all') ? Infinity : Math.max(1, Number(this.vizTopQ) || 10);
      scope = { topIn: lists.topIn.slice(0, k), exitsIn: [],
        topOut: lists.topOut.slice(0, k), exitsOut: [], warmup: lists.warmup };
    }
    const rows = vizRows(idxMode ? null : scope, this.flowMap, null, idxRows);
    const hostBox = el('div');
    this.body.append(hostBox);
    const common = {
      rows, C, levelName: name, idxMode, flowMap: this.flowMap,
      vizTopQ: this.vizTopQ,
      dayA: this.dayA,
      onTopQ: (n) => { this.vizTopQ = n; this._render(); },
      onRerender: () => this._render(),
      width: Math.max(560, this.body.clientWidth || this.host.clientWidth || 720),
      onOpen: (code, nm) => this._openDetail(code, nm, this.level),
      tipShow: (t, ev, moveOnly) => this._tipShow(t, ev, moveOnly),
      tipHide: () => this._tipHide(),
    };
    // 图视图（8002 结构：.vizbox > .viz-cap 说明行 + #vizStage 绘图区）。
    // 必须先挂容器再画：四象要按容器实宽算 viewBox，未挂载时 clientWidth=0，
    // 退回 fallback 后 SVG 被等比放大（实测字大 2.3 倍、气泡溢出）。
    const vizbox = el('div', { class: 'vizbox' });
    const scopeTxt = idxMode
      ? '全部指数（' + rows.length + ' 个，工作台本就是全列）'
      : (this.view === 'grid'
        ? '前10% / 后10%（含退出）'
        : (this.vizTopQ === 'all' ? '全部榜单' : '前' + this.vizTopQ + ' / 后' + this.vizTopQ) + '（不含退出）')
        + '（' + rows.filter((r) => r.side === 'in').length + ' + ' + rows.filter((r) => r.side === 'out').length + ' 个）';
    vizbox.append(el('div', { class: 'viz-cap' }, name + ' · 区间 ' + this._effectiveWin() + ' 交易日 · ' + scopeTxt
      + ' · 红涨绿跌 · ' + (this.view === 'grid' ? '点格子开详情卡' : '点气泡钉住对比（可多钉）· 点右侧板块名开详情')));
    const stage = el('div', { id: 'vizStage' });
    vizbox.append(stage);
    hostBox.append(vizbox);
    stage.append(this.view === 'grid' ? renderGrid(common) : renderQuad(common));
  },

  // ------------------------------------------------------------ 指数层（照 8002 setIdxPanels / renderIdxDetail）

  /**
   * 指数层：左栏全量指数（12 个）+ 右栏选中指数的走势与数据（内联）。
   *
   * 数据来源：compute.computeAllRows（不切 10%）→ buildPanel(rowsAll)。
   * 右栏复用 detail.js 的 mountDetail（同一个 paintCard/bindCard，禁第二套渲染）。
   * 交互（2026-11-05 主人标注 81 修正 11-03 的误解）：
   *   · 点行 = 换右栏（快速预览，右栏右上角 ↻ 重算本卡）；
   *   · 点名称 / 右键菜单 = 弹详情卡（与板块同一套 openDetail）。
   */
  _renderIdx() {
    const C = this.C || this._buildCompute();
    const all = C.computeAllRows(this.dayB);
    // 每次重画都要先清 body：点行换选中是直调本函数（不经 _render），不清就会叠出第二份 12 行
    this.body.textContent = '';
    if (this._detailInst) { this._detailInst.destroy(); this._detailInst = null; }
    if (all.warmup) {
      this.body.append(el('div', { class: 'rb-empty' }, '该日历史不足 ' + this._effectiveWin() + ' 天（窗口未填满），请往后选日期'));
      return;
    }
    const rows = all.rows;
    if (rows.length && !rows.some((r) => r.code === this.idxSel)) this.idxSel = rows[0].code;

    /* 指数层：列表全量（只有 12 个，不切 10%）+ 右栏内联走势与数据。
       2026-11-03（标注 65）曾把右栏整块删掉换成纯弹卡，2026-11-05（标注 81）判断为误解：
       右栏（含右上角 ↻ 重算）恢复，点行仍只换右栏，弹卡走名称 / 右键菜单。 */
    const wrap = el('div', { class: 'panels idx' });
    wrap.append(buildPanel({
      side: 'in', title: '各大指数 · 全部（' + rows.length + '）', C,
      state: { dayB: this.dayB, winN: this._effectiveWin(), drillL1: this.drillL1, sortKey: this.sortKey, sortDir: this.sortDir },
      flowMap: this.flowMap, idxMode: true,
      rowsAll: rows, selCode: this.idxSel,
      onSort: (k) => this._onSort(k),
      onSelect: (code, nm) => { this.idxSel = code; this._renderIdx(); },
      onOpen: (code, nm) => this._openDetail(code, nm, 'INDEX'),
      tipShow: (t, ev, moveOnly, hit) => this._tipShow(t, ev, moveOnly, hit),
      tipHide: () => this._tipHide(),
    }));
    this.body.append(wrap);

    // 右栏：详情卡挂在左栏之外的独立容器（8002 是 .panel-out 的 #listOut）
    const panelOut = el('div', { class: 'panel panel-out idx-right' });
    const hd = el('div', { class: 'panel-hd' });
    hd.append(el('span', { class: 'sw sw-in' }), el('span', null, '走势与数据'));
    panelOut.append(hd);
    const list = el('ol', { class: 'list', 'data-side': 'out' });
    const li = el('li', { class: 'idx-detail' });
    list.append(li);
    panelOut.append(list);
    wrap.append(panelOut);

    const sel = rows.find((r) => r.code === this.idxSel) || rows[0];
    if (!sel) return;
    this._detailInst = mountDetail(li, {
      api: this.ctx.api,
      code: sel.code,
      name: sel.name,
      level: 'INDEX',
      winN: this._effectiveWin(),
      date: this.rankRecent ? this.rankRecent.dates[this.dayB] : null,
      onPeriod: (n) => { this._setWindow(n); },
      onRemount: () => this._renderIdx(),
      tipShow: (t, ev, moveOnly, hit) => this._tipShow(t, ev, moveOnly, hit),
      tipHide: () => this._tipHide(),
    });
  },

  // ------------------------------------------------------------ 浮层与详情

  _tip(fresh) {
    if (!this._tipEl) {
      this._tipEl = el('div', { class: 'sector-card-tip', hidden: '' });
      document.body.append(this._tipEl);
    }
    return this._tipEl;
  },

  _tipShow(payload, ev, moveOnly, hitTip) {
    const t = this._tip();
    if (!moveOnly && payload) {
      t.classList.toggle('hit-tip', !!hitTip);   // 色格图浮层要放宽 max-width（8002 #vizTip.hit-tip）
      t.textContent = '';
      if (payload instanceof Node) t.append(payload);
      else t.innerHTML = String(payload);
      t.hidden = false;
    }
    if (t.hidden) return;
    const pad = 12;
    const r = t.getBoundingClientRect();
    let x = ev.clientX + pad;
    let y = ev.clientY + pad;
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
    t.style.left = x + 'px';
    t.style.top = y + 'px';
  },

  _tipHide() {
    if (this._tipEl) this._tipEl.hidden = true;
  },

  _openDetail(code, name, level) {
    if (!code) return;
    const self = this;
    this._tipHide();
    openDetail({
      api: this.ctx.api,
      code: code,
      name: name,
      level: level || this.level,
      winN: this._effectiveWin(),
      date: this.rankRecent ? this.rankRecent.dates[this.dayB] : null,
      onPeriod: (n) => { self._setWindow(n); },
      tipShow: (t, ev, moveOnly, hit) => self._tipShow(t, ev, moveOnly, hit),
      tipHide: () => self._tipHide(),
    });
  },
};

export default { cards: [card] };
