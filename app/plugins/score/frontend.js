// ============================================================================
// 个股打分卡 · 前端（2026-10-29，projects/panel-v2）
// ----------------------------------------------------------------------------
// 内容 = 六维加权打分引擎（夏天-activity/工具/stock_score_engine.py）当日产物：
//   一张可排序榜单（总分 / 六维分项 / 评级 / 全市场排名 / 板块内排名 / 所属板块 / 参与维度数），
//   可按板块、评级筛选，按代码/名称搜索，点代码开 K 线弹窗（走 shell 的 data-open-kline 委托）。
//
// 契约（docs/CONTRACT.md §5）：原生 ES module、零 import、零构建、只操作自己的 root。
// 数据只走 ctx.api.get：`/meta`（新鲜度 + 覆盖率）与 `/rows`（原始行）。
// 排序/筛选全在前端内存里做（5577 行，一次 3.6MB JSON）；后端不解析、不重算。
// ============================================================================

const STYLE_ID = 'score-card-css';
const ROW_H = 28;              // 固定行高（增量渲染的几何常量）
const CHUNK = 120;             // 每次追加渲染的行数
const LOAD_AHEAD = 240;        // 距底部多少像素开始追加

const COLS = [
  { key: 'rank', label: '#', cls: 'c-rank', sort: true },
  { key: 'code6', label: '代码', cls: 'c-code' },
  { key: 'name', label: '名称', cls: 'c-name', sort: true },
  { key: 'score', label: '总分', cls: 'c-score', sort: true },
  { key: 'grade', label: '级', cls: 'c-grade', sort: true },
  { key: 'dim_fund', label: '资金', cls: 'c-dim', sort: true },
  { key: 'dim_sector', label: '板块', cls: 'c-dim', sort: true },
  { key: 'dim_trend', label: '趋势', cls: 'c-dim', sort: true },
  { key: 'dim_volprice', label: '量价', cls: 'c-dim', sort: true },
  { key: 'dim_value', label: '估值', cls: 'c-dim', sort: true },
  { key: 'dim_quality', label: '质量', cls: 'c-dim', sort: true },
  { key: 'n_dims', label: '维', cls: 'c-ndim', sort: true },
  { key: 'rank_in_sector', label: '板块内', cls: 'c-rank', sort: true },
  { key: 'sector_name', label: '所属板块', cls: 'c-sector', sort: true },
];

const CSS = [
  '.sc-root{display:flex;flex-direction:column;gap:8px;min-width:0;min-height:0;overflow:hidden;color:var(--fg)}',
  '.sc-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:0.72rem;color:var(--dim);flex:0 0 auto}',
  '.sc-bar b{color:var(--fg-strong);font-weight:600}',
  '.sc-bar .sc-warn{color:var(--warn)}',
  '.sc-bar .sc-grow{margin-left:auto}',
  '.sc-bar .sc-btn{margin-left:10px}',
  '.sc-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer}',
  '.sc-btn:hover{background:var(--card2);border-color:var(--accent);color:var(--accent)}',
  '.sc-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:0 0 auto}',
  '.sc-in,.sc-sel{border:var(--border-w) var(--border-style) var(--border-color);background:var(--card);color:var(--fg);border-radius:var(--radius-sm);padding:3px 8px;font-size:0.72rem;font-family:inherit;height:24px}',
  '.sc-in{width:150px}',
  '.sc-sel{max-width:170px}',
  '.sc-in:focus,.sc-sel:focus{outline:none;border-color:var(--accent)}',
  '.sc-seg{display:inline-flex;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-sm);overflow:hidden}',
  '.sc-seg button{border:0;background:transparent;color:var(--dim);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer}',
  '.sc-seg button.on{background:var(--accent-bg);color:var(--accent)}',
  '.sc-count{color:var(--dim);font-size:0.72rem;font-variant-numeric:tabular-nums}',
  '.sc-table{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);overflow:hidden}',
  '.sc-grid{display:grid;grid-template-columns:46px 62px minmax(72px,1fr) 56px 34px 48px 48px 48px 48px 48px 48px 30px 50px minmax(78px,1fr);align-items:center;column-gap:2px}',
  '.sc-head{flex:0 0 auto;background:var(--card2);border-bottom:var(--border-w) var(--border-style) var(--border-color);font-size:0.7rem;color:var(--dim);position:sticky;top:0;z-index:2}',
  '.sc-head .sc-cell{padding:5px 4px;white-space:nowrap}',
  '.sc-head .sc-sortable{cursor:pointer;user-select:none}',
  '.sc-head .sc-sortable:hover{color:var(--fg-strong)}',
  '.sc-head .sc-sorted{color:var(--accent)}',
  '.sc-body{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden}',
  '.sc-row{height:28px;font-size:0.72rem;border-bottom:1px solid var(--line-soft);font-variant-numeric:tabular-nums}',
  '.sc-row:hover{background:var(--card2)}',
  '.sc-cell{padding:0 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  '.sc-rank{text-align:right;color:var(--dim)}',
  '.sc-code{color:var(--fg-strong);cursor:pointer;text-decoration:underline dotted}',
  '.sc-code:hover{color:var(--accent)}',
  '.sc-name{color:var(--fg)}',
  '.sc-score{text-align:right;font-weight:600;color:var(--fg-strong)}',
  '.sc-grade{text-align:center;font-weight:600}',
  '.sc-grade.g-S{color:var(--up)}',
  '.sc-grade.g-A{color:var(--accent)}',
  '.sc-grade.g-B{color:var(--fg)}',
  '.sc-grade.g-C{color:var(--dim)}',
  '.sc-grade.g-D{color:var(--down)}',
  '.sc-dim{text-align:right;color:var(--dim)}',
  '.sc-dim.hi{color:var(--up)}',
  '.sc-dim.lo{color:var(--down)}',
  '.sc-ndim{text-align:center;color:var(--dim)}',
  '.sc-ndim.short{color:var(--warn)}',
  '.sc-sector{color:var(--dim)}',
  '.sc-msg{padding:14px 16px;font-size:0.8rem;line-height:1.7;color:var(--fg);background:var(--danger-bg);border:var(--border-w) var(--border-style) var(--danger);border-radius:var(--radius-md);white-space:pre-wrap}',
  '.sc-msg code{font-family:var(--font-mono);font-size:0.75rem;color:var(--fg-strong)}',
  '.sc-empty{padding:24px 4px;color:var(--dim);font-size:0.78rem}',
];

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS.join('\n');
  document.head.append(s);
}

function num(v, digits) {
  return (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(digits === undefined ? 2 : digits);
}

function dimClass(v) {
  if (v === null || v === undefined) return 'sc-dim';
  if (v >= 70) return 'sc-dim hi';
  if (v <= 30) return 'sc-dim lo';
  return 'sc-dim';
}

// 铺满主区域：`.stage` 是 `overflow:auto` 的 flex 子项（高度确定，带 12/16/20 内边距）。
// 用实测 clientHeight + 负 margin 抵消内边距，让表格内部滚动而不是整页滚（同 valuation 卡口径）。
function fitToStage(root, stage) {
  if (!stage) return;
  const cs = getComputedStyle(stage);
  const pt = parseFloat(cs.paddingTop) || 0;
  const pb = parseFloat(cs.paddingBottom) || 0;
  const pl = parseFloat(cs.paddingLeft) || 0;
  const pr = parseFloat(cs.paddingRight) || 0;
  root.style.margin = (-pt) + 'px ' + (-pr) + 'px ' + (-pb) + 'px ' + (-pl) + 'px';
  root.style.height = Math.max(320, stage.clientHeight) + 'px';
}

// 模块内清理句柄：shell 调 unmount() 时不传参，收尾存在模块作用域（同一时刻只挂一张卡）
let activeCleanup = null;

export default {
  cards: [{
    id: 'score.main',
    title: '个股打分',
    slot: 'main',
    order: 72,
    // 不声明 refreshEvery：数据是收盘后一次的日频产物，自动重载只会打断阅读；点「重新载入」即可。
    async mount(el, ctx) {
      injectStyle();

      const root = document.createElement('div');
      root.className = 'sc-root';

      // ---------- 顶栏：数据时间 + 覆盖率 ----------
      const bar = document.createElement('div');
      bar.className = 'sc-bar';
      const stamp = document.createElement('span');
      stamp.textContent = '读取中…';
      const cov = document.createElement('span');
      cov.className = 'sc-grow';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sc-btn';
      btn.textContent = '重新载入';
      bar.append(stamp, cov, btn);

      // ---------- 工具条：搜索 / 板块 / 评级 / 排序基准 ----------
      const tools = document.createElement('div');
      tools.className = 'sc-tools';
      const search = document.createElement('input');
      search.className = 'sc-in';
      search.type = 'search';
      search.placeholder = '代码 / 名称';
      const selSector = document.createElement('select');
      selSector.className = 'sc-sel';
      const selGrade = document.createElement('select');
      selGrade.className = 'sc-sel';
      const seg = document.createElement('div');
      seg.className = 'sc-seg';
      const btnMkt = document.createElement('button');
      btnMkt.type = 'button';
      btnMkt.textContent = '全市场分';
      const btnSec = document.createElement('button');
      btnSec.type = 'button';
      btnSec.textContent = '板块内分';
      seg.append(btnMkt, btnSec);
      const count = document.createElement('span');
      count.className = 'sc-count';
      tools.append(search, selSector, selGrade, seg, count);

      // ---------- 表格 ----------
      const table = document.createElement('div');
      table.className = 'sc-table';
      const head = document.createElement('div');
      head.className = 'sc-head sc-grid';
      const body = document.createElement('div');
      body.className = 'sc-body';
      table.append(head, body);

      root.append(bar, tools, table);
      el.append(root);

      // 铺满 #stage，表格内部滚动（不依赖百分比高度能不能解析）
      const stage = el.closest('.stage') || el.parentElement;
      fitToStage(root, stage);
      let ro = null;
      if (stage && typeof ResizeObserver === 'function') {
        ro = new ResizeObserver(function () { fitToStage(root, stage); });
        ro.observe(stage);
      }

      // ---------- 状态 ----------
      let rows = [];
      let view = [];
      let sectorList = [];
      let mode = 'market';            // market | sector
      let sortKey = 'score';
      let sortDir = -1;               // -1 降序 / 1 升序
      let shown = 0;

      function headCell(col) {
        const d = document.createElement('div');
        d.className = 'sc-cell ' + col.cls + (col.sort ? ' sc-sortable' : '');
        // 排序基准切到「板块内分」时，总分列改名「内分」——否则名次按内分排、分却显全市场分，读起来自相矛盾
        d.textContent = (col.key === 'score' && mode === 'sector') ? '内分' : col.label;
        if (col.sort) {
          if (col.key === sortKey) d.classList.add('sc-sorted');
          d.addEventListener('click', function () {
            if (sortKey === col.key) sortDir = -sortDir;
            else { sortKey = col.key; sortDir = (col.key === 'rank' || col.key === 'rank_in_sector') ? 1 : -1; }
            apply();
          });
        }
        return d;
      }

      function buildHead() {
        head.textContent = '';
        for (const col of COLS) head.append(headCell(col));
      }

      function val(r, key) {
        if (key === 'rank') return mode === 'sector' ? r.rank_in_sector : r.rank;
        if (key === 'score') return mode === 'sector' ? r.score_in_sector : r.score;
        return r[key];
      }

      function apply() {
        const kw = String(search.value || '').trim().toLowerCase();
        const sc = selSector.value;
        const gr = selGrade.value;
        view = rows.filter(function (r) {
          if (sc && r.sector_code !== sc) return false;
          if (gr && r.grade !== gr) return false;
          if (kw && !(String(r.code6).includes(kw) || String(r.name || '').toLowerCase().includes(kw))) return false;
          return true;
        });
        view.sort(function (a, b) {
          const va = val(a, sortKey), vb = val(b, sortKey);
          const na = (va === null || va === undefined), nb = (vb === null || vb === undefined);
          if (na && nb) return String(a.code6).localeCompare(String(b.code6));
          if (na) return 1;
          if (nb) return -1;
          if (typeof va === 'number' && typeof vb === 'number') {
            if (va !== vb) return (va - vb) * sortDir;
            return String(a.code6).localeCompare(String(b.code6));
          }
          return String(va).localeCompare(String(vb), 'zh') * sortDir;
        });
        count.textContent = '共 ' + view.length + ' 只' + (view.length !== rows.length ? '（全 A ' + rows.length + '）' : '');
        body.scrollTop = 0;
        body.textContent = '';
        shown = 0;
        renderMore();
        buildHead();
      }

      function rowNode(r) {
        const d = document.createElement('div');
        d.className = 'sc-row sc-grid';
        const cells = [];
        const mk = function (cls, text, extra) {
          const c = document.createElement('div');
          c.className = 'sc-cell ' + cls;
          c.textContent = text;
          if (extra) extra(c);
          cells.push(c);
          return c;
        };
        mk('sc-rank', mode === 'sector' ? (r.rank_in_sector === null ? '—' : r.rank_in_sector)
          : (r.rank === null ? '—' : r.rank));
        // 代码：带 data-open-kline，shell 的 capture 委托负责开 K 线弹窗
        mk('sc-code', r.code6, function (c) {
          c.setAttribute('data-open-kline', r.code6);
          c.setAttribute('data-open-name', r.name || '');
          c.title = '开 K 线：' + r.code6 + ' ' + (r.name || '');
        });
        mk('sc-name', r.name || '—');
        mk('sc-score', num(mode === 'sector' ? r.score_in_sector : r.score), function (c) {
          c.title = '全市场分 ' + num(r.score) + ' · 板块内分 ' + num(r.score_in_sector) +
            '（板块内分 = 板块维度不参与，权重分给其余五维）';
        });
        mk('sc-grade g-' + (r.grade || 'B'), r.grade || '—');
        for (const k of ['dim_fund', 'dim_sector', 'dim_trend', 'dim_volprice', 'dim_value', 'dim_quality']) {
          mk(dimClass(r[k]), num(r[k], 1));
        }
        mk('sc-ndim' + (r.n_dims < 6 ? ' short' : ''), String(r.n_dims),
          function (c) { c.title = '参与维度数（<6 = 有维度缺项，权重已按比例重分）'; });
        mk('sc-rank', r.rank_in_sector === null ? '—' : r.rank_in_sector,
          function (c) { c.title = '板块内排名（按板块内分，板块维度不参与）'; });
        mk('sc-sector', r.sector_name || '—');
        for (const c of cells) d.append(c);
        return d;
      }

      function renderMore() {
        const end = Math.min(view.length, shown + CHUNK);
        const frag = document.createDocumentFragment();
        for (let i = shown; i < end; i++) frag.append(rowNode(view[i]));
        body.append(frag);
        shown = end;
      }

      body.addEventListener('scroll', function () {
        if (shown >= view.length) return;
        if (body.scrollTop + body.clientHeight >= body.scrollHeight - LOAD_AHEAD) renderMore();
      });

      search.addEventListener('input', function () { apply(); });
      selSector.addEventListener('change', function () { apply(); });
      selGrade.addEventListener('change', function () { apply(); });
      btnMkt.addEventListener('click', function () {
        mode = 'market'; sortKey = 'score'; sortDir = -1;
        btnMkt.classList.add('on'); btnSec.classList.remove('on');
        apply();
      });
      btnSec.addEventListener('click', function () {
        mode = 'sector'; sortKey = 'score_in_sector'; sortDir = -1;
        btnSec.classList.add('on'); btnMkt.classList.remove('on');
        apply();
      });
      btnMkt.classList.add('on');

      function fillSectors() {
        const seen = {};
        for (const r of rows) {
          if (!r.sector_code || seen[r.sector_code]) continue;
          seen[r.sector_code] = r.sector_name || r.sector_code;
        }
        sectorList = Object.keys(seen).map(function (k) { return [k, seen[k]]; })
          .sort(function (a, b) { return String(a[1]).localeCompare(String(b[1]), 'zh'); });
        selSector.textContent = '';
        const all = document.createElement('option');
        all.value = '';
        all.textContent = '全部板块（' + sectorList.length + '）';
        selSector.append(all);
        for (const [code, name] of sectorList) {
          const o = document.createElement('option');
          o.value = code;
          o.textContent = name;
          selSector.append(o);
        }
        selGrade.textContent = '';
        const allG = document.createElement('option');
        allG.value = '';
        allG.textContent = '全部评级';
        selGrade.append(allG);
        for (const g of ['S', 'A', 'B', 'C', 'D']) {
          const o = document.createElement('option');
          o.value = g;
          o.textContent = g;
          selGrade.append(o);
        }
      }

      function showMessage(text) {
        table.style.display = 'none';
        tools.style.display = 'none';
        const box = document.createElement('div');
        box.className = 'sc-msg';
        box.textContent = text;
        root.append(box);
      }

      function paintMeta(meta) {
        stamp.textContent = '';
        const b = document.createElement('b');
        b.textContent = meta.asof || '—';
        stamp.append(document.createTextNode('打分日 '), b,
          document.createTextNode(' · 生成 ' + (meta.generated_at || '—') +
            (meta.stale ? '（偏旧，建议重跑）' : '')));
        stamp.className = meta.stale ? 'sc-warn' : '';
        const c = meta.coverage || {};
        const dims = c.dims || {};
        const dimTxt = Object.keys(dims).map(function (k) {
          const cn = { fund: '资金', sector: '板块', trend: '趋势', volprice: '量价', value: '估值', quality: '质量' }[k] || k;
          return cn + dims[k];
        }).join(' · ');
        cov.textContent = '全 A ' + (c.universe || 0) + ' · 有分 ' + (c.scored || 0) +
          ' · 维度参与 ' + dimTxt +
          ' · 缺项重分 ' + ((c.reweight && c.reweight.stocks_with_missing_dim) || 0) + ' 只';
      }

      async function load() {
        let meta = null;
        try {
          meta = await ctx.api.get('/meta');
        } catch (err) {
          stamp.textContent = '读取失败';
          showMessage('读取产物信息失败：' + (err && err.message ? err.message : err));
          return;
        }
        if (!meta || !meta.available) {
          stamp.textContent = '产物不存在';
          stamp.className = 'sc-warn';
          showMessage('还没生成打分产物。\n先跑：夏天-activity\\工具\\stock_score_engine.py（约 10 秒，会联网拉估值快照）\n产物路径：' +
            ((meta && meta.dir) || '') + '\\' + ((meta && meta.file) || ''));
          return;
        }
        paintMeta(meta);
        let payload = null;
        try {
          payload = await ctx.api.get('/rows');
        } catch (err) {
          showMessage('读取榜单失败：' + (err && err.message ? err.message : err));
          return;
        }
        rows = (payload && payload.rows) || [];
        if (!rows.length) {
          showMessage('产物里没有行（引擎可能跑了 --limit 或数据源全缺）。');
          return;
        }
        fillSectors();
        apply();
      }

      btn.addEventListener('click', function () { load(); });

      buildHead();
      await load();

      activeCleanup = function () {
        if (ro) { ro.disconnect(); ro = null; }
        body.textContent = '';
        rows = [];
        view = [];
      };
    },

    async unmount() {
      const fn = activeCleanup;
      activeCleanup = null;
      if (typeof fn === 'function') fn();
    },
  }],
};
