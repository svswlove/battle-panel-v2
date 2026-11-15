// ============================================================================
// 情绪打分卡 · 前端（2026-10-29，projects/panel-v2）
// ----------------------------------------------------------------------------
// 内容 = 六因子合成的一个 0–100 连续情绪分（基准 50，clamp [0,100]）：
//   ① 顶部大数字 + 档位标签（亢奋/偏热/中性/偏冷/冰点）
//   ② 六因子拆解（原始值 / 阈值 / 加减分 / 是否参与）
//   ③ 近 60 日分数曲线（背景按档位分段着色，50 分基准线）
//   ④ 底部并列显示 `stage`（规则硬判）+ `stage_note`
//
// **两套东西并列、不合并**：`stage` 是 track_pool.db 里 if-else 判出来的离散档
// （冰点/启动/发酵/高潮/分歧/退潮），本卡的分是连续值。两者背离时正好能看出来——
// 这是本卡的用途，不是缺陷，所以任何一侧都不许覆盖另一侧。
//
// 契约（docs/CONTRACT.md §5）：原生 ES module、零 import、零构建、只操作自己的 root。
// 数据只走 ctx.api.get('/payload')：后端现场算（58 行收盘数据 + 本地涨停池，毫秒级），
// 前端不重算任何因子、不改任何分数。
// ============================================================================

const STYLE_ID = 'emo-card-css';

const BAND_CLASS = { 亢奋: 'b-hot', 偏热: 'b-warm', 中性: 'b-mid', 偏冷: 'b-cold', 冰点: 'b-ice' };
// 曲线背景的分段着色（与档位一一对应，用主题变量而不是硬编码色值）
const BAND_FILL = {
  亢奋: 'rgba(220,80,80,0.10)',
  偏热: 'rgba(220,150,60,0.08)',
  中性: 'rgba(120,120,120,0.05)',
  偏冷: 'rgba(70,140,200,0.08)',
  冰点: 'rgba(70,140,200,0.14)',
};
// 档位分段（下界），与后端 engine.BANDS 同值；只用于画背景条，不参与打分
const BAND_EDGES = [[70, '亢奋'], [55, '偏热'], [40, '中性'], [25, '偏冷'], [0, '冰点']];

const CSS = [
  '.emo-root{display:flex;flex-direction:column;gap:10px;min-width:0;min-height:0;overflow:auto;color:var(--fg);padding:2px}',
  '.emo-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:0.72rem;color:var(--dim);flex:0 0 auto}',
  '.emo-bar b{color:var(--fg-strong);font-weight:600}',
  '.emo-bar .emo-grow{margin-left:auto}',
  '.emo-btn{border:var(--border-w) var(--border-style) var(--border-color);background:transparent;color:var(--fg);border-radius:var(--radius-sm);padding:3px 10px;font-size:0.72rem;font-family:inherit;cursor:pointer}',
  '.emo-btn:hover{background:var(--card2);border-color:var(--accent);color:var(--accent)}',
  // 顶部：大数字 + 档位
  '.emo-hero{display:flex;align-items:flex-end;gap:18px;flex:0 0 auto;padding:10px 14px;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);background:var(--card)}',
  '.emo-num{font-size:3.6rem;line-height:1;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-1px}',
  '.emo-num small{font-size:0.9rem;font-weight:400;color:var(--dim);margin-left:6px;letter-spacing:0}',
  '.emo-hero-side{display:flex;flex-direction:column;align-items:flex-start;gap:6px;padding-bottom:4px}',
  '.emo-band{display:inline-flex;align-items:center;gap:6px;font-size:0.95rem;font-weight:600;padding:2px 10px;border-radius:999px;border:var(--border-w) var(--border-style) currentColor}',
  '.emo-band.b-hot{color:var(--up)}',
  '.emo-band.b-warm{color:var(--warn)}',
  '.emo-band.b-mid{color:var(--fg)}',
  '.emo-band.b-cold{color:var(--dim)}',
  '.emo-band.b-ice{color:var(--accent)}',
  '.emo-hero-meta{font-size:0.72rem;color:var(--dim);font-variant-numeric:tabular-nums}',
  '.emo-rule{margin-left:auto;text-align:right;font-size:0.72rem;color:var(--dim);line-height:1.7;padding-bottom:4px}',
  '.emo-rule b{color:var(--fg-strong);font-weight:600}',
  // 六因子表
  '.emo-sec{flex:0 0 auto;border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);overflow:hidden}',
  '.emo-sec-h{display:flex;align-items:center;gap:8px;padding:6px 12px;background:var(--card2);border-bottom:var(--border-w) var(--border-style) var(--border-color);font-size:0.72rem;color:var(--dim)}',
  '.emo-sec-h b{color:var(--fg-strong);font-weight:600}',
  '.emo-tb{display:grid;grid-template-columns:minmax(88px,1fr) 132px 120px 84px 58px minmax(120px,1.5fr);align-items:center;column-gap:6px}',
  '.emo-th{font-size:0.7rem;color:var(--dim);padding:4px 12px;border-bottom:1px solid var(--line-soft)}',
  '.emo-tr{font-size:0.74rem;padding:5px 12px;border-bottom:1px solid var(--line-soft);font-variant-numeric:tabular-nums}',
  '.emo-tr:last-child{border-bottom:0}',
  '.emo-tr.off{opacity:0.45}',
  '.emo-fl{color:var(--fg-strong)}',
  '.emo-raw{color:var(--fg)}',
  '.emo-thr{color:var(--dim)}',
  '.emo-pt{text-align:right;font-weight:600}',
  '.emo-pt.pos{color:var(--up)}',
  '.emo-pt.neg{color:var(--down)}',
  '.emo-pt.zero{color:var(--dim)}',
  '.emo-partic{text-align:center;font-size:0.68rem;border-radius:3px;padding:1px 0}',
  '.emo-partic.on{color:var(--fg);background:var(--card2)}',
  '.emo-partic.off{color:var(--dim);border:1px dashed var(--line-soft)}',
  '.emo-note{color:var(--dim);font-size:0.7rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
  // 曲线
  '.emo-chart-box{flex:0 0 auto;position:relative;padding:6px 12px 2px}',
  '.emo-chart{width:100%;display:block}',
  '.emo-cap{font-size:0.7rem;color:var(--dim);padding:2px 12px 6px;font-variant-numeric:tabular-nums;min-height:1.2em}',
  '.emo-cap b{color:var(--fg-strong);font-weight:600}',
  // 底部：两套口径并列
  '.emo-dual{display:grid;grid-template-columns:1fr 1fr;gap:10px;flex:0 0 auto}',
  '.emo-box{border:var(--border-w) var(--border-style) var(--border-color);border-radius:var(--radius-md);padding:8px 12px;background:var(--card)}',
  '.emo-box .emo-k{font-size:0.7rem;color:var(--dim);margin-bottom:4px}',
  '.emo-box .emo-v{font-size:1.05rem;font-weight:600;color:var(--fg-strong)}',
  '.emo-box .emo-s{font-size:0.72rem;color:var(--dim);margin-top:4px;line-height:1.6;word-break:break-word}',
  '.emo-msg{padding:14px 16px;font-size:0.8rem;line-height:1.7;color:var(--fg);background:var(--danger-bg);border:var(--border-w) var(--border-style) var(--danger);border-radius:var(--radius-md);white-space:pre-wrap}',
  '.emo-msg code{font-family:var(--font-mono);font-size:0.75rem;color:var(--fg-strong)}',
  '.emo-foot{font-size:0.68rem;color:var(--dim);line-height:1.6;padding:0 2px 4px}',
];

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS.join('\n');
  document.head.append(s);
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function fmt(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits === undefined ? 2 : digits);
}

function pct(v, digits) {
  if (v === null || v === undefined) return '—';
  return (Number(v) * 100).toFixed(digits === undefined ? 1 : digits) + '%';
}

function bandClass(band) {
  return BAND_CLASS[band] || 'b-mid';
}

// 因子原始值的展示（不同因子量纲不同，统一在这里转成人话）
function rawText(f) {
  // 原始值有就显示（即使该因子因样本不足不计分）：把 40.0% 藏成「—」会让读者以为是缺数据
  if (f.raw === null || f.raw === undefined) return '—';
  switch (f.key) {
    case 'zt_total': return fmt(f.raw, 0) + ' 家';
    case 'promotion': return pct(f.raw, 1);
    case 'burst': return pct(f.raw, 1);
    case 'dt_vs_zt': return '跌停 ' + fmt(f.raw && f.raw.dt, 0) + ' / 涨停 ' + fmt(f.raw && f.raw.zt, 0);
    case 'height': return fmt(f.raw, 0) + ' 板';
    case 'lb2plus': return fmt(f.raw, 0) + ' 家';
    default: return String(f.raw);
  }
}

function thrText(f) {
  if (f.threshold === null || f.threshold === undefined) return '—';
  switch (f.key) {
    case 'zt_total': return fmt(f.threshold, 1) + ' 家';
    case 'promotion': return pct(f.threshold, 1);
    case 'burst': return pct(f.threshold, 1);
    case 'lb2plus': return '分位 ' + pct(f.threshold, 0);
    default: return fmt(f.threshold, 2);
  }
}

// 晋级率在底部摘要里的一句话：要能看出「有值但样本不足不计分」与「根本没值」的区别
function promoText(today) {
  const f = (today.factors || []).find(function (x) { return x.key === 'promotion'; });
  const hasValue = today.promotion_2b !== null && today.promotion_2b !== undefined;
  if (!hasValue) return '不可用';
  if (f && !f.participated) return pct(today.promotion_2b, 1) + '（样本不足，不计分）';
  const base = today.promotion_base;
  return pct(today.promotion_2b, 1) + (base ? '（分母 ' + base + ' 只）' : '');
}

// 铺满主区域：`.stage` 是 overflow:auto 的 flex 子项，带 12/16/20 内边距。
// 内容比一屏高时本卡自己滚（root overflow:auto），所以只对齐宽度、不强设高度。
function fitWidth(root, stage) {
  if (!stage) return;
  const cs = getComputedStyle(stage);
  const pl = parseFloat(cs.paddingLeft) || 0;
  const pr = parseFloat(cs.paddingRight) || 0;
  root.style.marginLeft = (-pl) + 'px';
  root.style.marginRight = (-pr) + 'px';
}

// 模块内清理句柄（shell 调 unmount() 不传参，同一时刻只挂一张卡）
let activeCleanup = null;

export default {
  cards: [{
    id: 'emotion.main',
    title: '情绪打分',
    slot: 'main',
    order: 73,
    // 不声明 refreshEvery：源是日频收盘数据，盘中不会有新值；需要刷新就点「重算」。
    async mount(rootEl, ctx) {
      injectStyle();

      const root = el('div', 'emo-root');
      rootEl.append(root);

      const bar = el('div', 'emo-bar');
      const stamp = el('span', null, '情绪分读取中…');
      const grow = el('span', 'emo-grow');
      const hint = el('span', null, '六因子合成 · 基准 50 · 与 stage 并列不合并');
      const btn = el('button', 'emo-btn', '重算');
      btn.type = 'button';
      bar.append(stamp, hint, grow, btn);
      root.append(bar);
      let payload = null;
      let chartBox = null;
      let observer = null;

      function showMessage(text) {
        root.textContent = '';
        root.append(bar);
        const msg = el('div', 'emo-msg');
        msg.textContent = text;
        root.append(msg);
      }

      // ---------------------------------------------------------------- 曲线
      function drawChart(host, hist) {
        host.textContent = '';
        if (!hist || !hist.length) return;

        const width = Math.max(320, host.clientWidth || 720);
        const height = 190;
        const padL = 30, padR = 14, padT = 10, padB = 20;
        const plotW = width - padL - padR;
        const plotH = height - padT - padB;
        const n = hist.length;
        const xAt = (i) => padL + (n === 1 ? plotW / 2 : (plotW * i) / (n - 1));
        const yAt = (v) => padT + plotH * (1 - Math.max(0, Math.min(100, v)) / 100);

        const NS = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(NS, 'svg');
        svg.setAttribute('class', 'emo-chart');
        svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
        svg.setAttribute('height', String(height));
        svg.setAttribute('preserveAspectRatio', 'none');

        // 档位背景条（从上到下：亢奋 → 冰点）
        for (let i = 0; i < BAND_EDGES.length; i++) {
          const lower = BAND_EDGES[i][0];
          const upper = i === 0 ? 100 : BAND_EDGES[i - 1][0];
          const rect = document.createElementNS(NS, 'rect');
          rect.setAttribute('x', String(padL));
          rect.setAttribute('y', String(yAt(upper)));
          rect.setAttribute('width', String(plotW));
          rect.setAttribute('height', String(Math.max(1, yAt(lower) - yAt(upper))));
          rect.setAttribute('fill', BAND_FILL[BAND_EDGES[i][1]] || 'transparent');
          svg.append(rect);
        }

        // 刻度（0/25/40/55/70/100）
        [0, 25, 40, 55, 70, 100].forEach(function (v) {
          const line = document.createElementNS(NS, 'line');
          line.setAttribute('x1', String(padL));
          line.setAttribute('x2', String(padL + plotW));
          line.setAttribute('y1', String(yAt(v)));
          line.setAttribute('y2', String(yAt(v)));
          line.setAttribute('stroke', 'var(--line-soft)');
          line.setAttribute('stroke-width', v === 50 ? '1' : '0.6');
          if (v === 50) line.setAttribute('stroke-dasharray', '4 3');
          svg.append(line);
          const t = document.createElementNS(NS, 'text');
          t.setAttribute('x', String(padL - 6));
          t.setAttribute('y', String(yAt(v) + 3));
          t.setAttribute('text-anchor', 'end');
          t.setAttribute('font-size', '9');
          t.setAttribute('fill', 'var(--dim)');
          t.textContent = String(v);
          svg.append(t);
        });

        // 折线
        const pts = hist.map((p, i) => xAt(i) + ',' + yAt(p.score));
        const poly = document.createElementNS(NS, 'polyline');
        poly.setAttribute('points', pts.join(' '));
        poly.setAttribute('fill', 'none');
        poly.setAttribute('stroke', 'var(--accent)');
        poly.setAttribute('stroke-width', '1.6');
        poly.setAttribute('stroke-linejoin', 'round');
        svg.append(poly);

        // 数据点（参与因子不足 6 个的旧点画空心，提示该日晋级率不可用）
        hist.forEach(function (p, i) {
          const c = document.createElementNS(NS, 'circle');
          c.setAttribute('cx', String(xAt(i)));
          c.setAttribute('cy', String(yAt(p.score)));
          c.setAttribute('r', i === n - 1 ? '3.4' : '1.9');
          c.setAttribute('fill', i === n - 1 ? 'var(--accent)' : 'var(--bg)');
          c.setAttribute('stroke', 'var(--accent)');
          c.setAttribute('stroke-width', '1.1');
          if (p.n_factors < 6) c.setAttribute('fill-opacity', '0.25');
          svg.append(c);
        });

        // x 轴：首、中、尾三个日期（MM-DD）
        [0, Math.floor((n - 1) / 2), n - 1].forEach(function (i, k) {
          if (i < 0 || !hist[i]) return;
          const t = document.createElementNS(NS, 'text');
          t.setAttribute('x', String(xAt(i)));
          t.setAttribute('y', String(height - 5));
          t.setAttribute('text-anchor', k === 0 ? 'start' : (k === 2 ? 'end' : 'middle'));
          t.setAttribute('font-size', '9');
          t.setAttribute('fill', 'var(--dim)');
          t.textContent = String(hist[i].date || '').slice(4).replace(/^(\d\d)(\d\d)$/, '$1-$2');
          svg.append(t);
        });

        host.append(svg);

        // 悬停读数：透明覆盖层把鼠标 x 映射到最近的交易日
        const cap = el('div', 'emo-cap');
        cap.textContent = '悬停查看任意一日：' + (hist[n - 1].date || '') + ' · ' +
          fmt(hist[n - 1].score, 2) + ' 分 · ' + (hist[n - 1].band || '—') +
          (hist[n - 1].stage ? ' · 规则 ' + hist[n - 1].stage : '');
        host.append(cap);
        svg.addEventListener('mousemove', function (ev) {
          const box = svg.getBoundingClientRect();
          if (!box.width) return;
          // 屏幕坐标 → viewBox 坐标 → 交易日下标（先缩放到用户单位，再扣掉左右留白）
          const xUser = (ev.clientX - box.left) * (width / box.width);
          const rel = (xUser - padL) / plotW;
          const idx = Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1))));
          const p = hist[idx];
          if (!p) return;
          cap.textContent = '';
          const b = el('b', null, String(p.date || ''));
          cap.append(b, document.createTextNode(
            ' · ' + fmt(p.score, 2) + ' 分 · ' + (p.band || '—') +
            ' · 涨停 ' + fmt(p.zt_total, 0) + ' / 跌停 ' + fmt(p.dt_total, 0) +
            ' / 最高 ' + fmt(p.height, 0) + ' 板 · 规则 ' + (p.stage || '—') +
            ' · 参与 ' + (p.n_factors || 0) + '/6'));
        });
      }

      // ---------------------------------------------------------------- 整页
      function render(data) {
        payload = data;
        root.textContent = '';
        root.append(bar);

        const today = data.today || {};
        const hist = data.history || [];
        // 口径版本必须在页面上：口径一改（如 2026-10-29 修正炸板率符号），旧截图/旧记录才追得回来
        hint.textContent = '六因子合成 · 基准 ' + fmt(data.base_score, 0) +
          ' · 口径 ' + (data.caliber_version || '—') +
          '（' + (data.caliber_revised || '') + ' 修订）· 与 stage 并列不合并';

        // 顶部：大数字 + 档位 + 规则判定摘要（并列，不合并）
        const hero = el('div', 'emo-hero');
        const num = el('div', 'emo-num');
        num.textContent = fmt(today.score, 1);
        num.append(el('small', null, '/100'));
        const side = el('div', 'emo-hero-side');
        const band = el('span', 'emo-band ' + bandClass(today.band), today.band || '—');
        const meta = el('div', 'emo-hero-meta');
        meta.textContent = '基准 ' + fmt(data.base_score, 0) + ' + 六因子合成 · 参与 ' +
          (today.n_factors || 0) + '/6 · 数据日 ' + (data.asof || '—');
        side.append(band, meta);
        const rule = el('div', 'emo-rule');
        rule.append(
          document.createTextNode('规则判定 stage '),
          el('b', null, today.stage || '—'),
          document.createElement('br'),
          document.createTextNode('（硬判，与打分并列）')
        );
        hero.append(num, side, rule);
        root.append(hero);

        // 六因子拆解
        const sec = el('div', 'emo-sec');
        const secH = el('div', 'emo-sec-h');
        secH.append(el('b', null, '六因子拆解'), document.createTextNode('· 阈值：涨停家数/连板数用滚动 ' + (data.window || 60) + ' 日分位'));
        sec.append(secH);

        const head = el('div', 'emo-tb emo-th');
        ['因子', '原始值', '阈值 / 基准', '加减分', '参与', '说明'].forEach(function (t) {
          head.append(el('div', null, t));
        });
        sec.append(head);

        (today.factors || []).forEach(function (f) {
          const row = el('div', 'emo-tb emo-tr' + (f.participated ? '' : ' off'));
          row.append(el('div', 'emo-fl', f.label));
          row.append(el('div', 'emo-raw', rawText(f)));
          row.append(el('div', 'emo-thr', thrText(f)));
          const pts = Number(f.points) || 0;
          const cls = pts > 0 ? 'pos' : (pts < 0 ? 'neg' : 'zero');
          row.append(el('div', 'emo-pt ' + cls, (pts > 0 ? '+' : '') + fmt(pts, 2)));
          row.append(el('div', 'emo-partic ' + (f.participated ? 'on' : 'off'), f.participated ? '是' : '否'));
          const note = el('div', 'emo-note', f.note || '');
          note.title = f.note || '';
          row.append(note);
          sec.append(row);
        });
        root.append(sec);

        // 近 60 日曲线
        const chartSec = el('div', 'emo-sec');
        const chartH = el('div', 'emo-sec-h');
        chartH.append(
          el('b', null, '近 ' + hist.length + ' 日情绪分'),
          document.createTextNode('· 虚线 = 基准 50 · 背景按档位分段 · 空心点 = 该日晋级率不可用')
        );
        chartSec.append(chartH);
        chartBox = el('div', 'emo-chart-box');
        chartSec.append(chartBox);
        root.append(chartSec);
        drawChart(chartBox, hist);

        // 底部：两套口径并列
        const dual = el('div', 'emo-dual');
        const boxA = el('div', 'emo-box');
        boxA.append(el('div', 'emo-k', '规则硬判（track_pool.db::sentiment.stage）'));
        boxA.append(el('div', 'emo-v', today.stage || '—'));
        boxA.append(el('div', 'emo-s', today.stage_note || '（无 stage_note）'));
        const boxB = el('div', 'emo-box');
        boxB.append(el('div', 'emo-k', '连续打分（本卡六因子合成）'));
        const bv = el('div', 'emo-v');
        bv.append(document.createTextNode(fmt(today.score, 2) + ' 分 '));
        bv.append(el('span', 'emo-band ' + bandClass(today.band), today.band || '—'));
        boxB.append(bv);
        boxB.append(el('div', 'emo-s',
          '涨停 ' + fmt(today.zt_total, 0) + ' / 曾涨停 ' + fmt(today.zt_ever, 0) +
          ' / 跌停 ' + fmt(today.dt_total, 0) + ' · 最高 ' + fmt(today.height, 0) + ' 板 · 连板 ' + fmt(today.lb2plus, 0) +
          ' · 2 板晋级 ' + promoText(today)));
        dual.append(boxA, boxB);
        root.append(dual);

        // 页脚：口径与边界
        const promo = data.promotion || {};
        const foot = el('div', 'emo-foot');
        foot.textContent = '口径 ' + (data.caliber_version || '—') + ' · ' + (data.caliber_revised || '') +
          ' 修订：' + (data.caliber_revision_note || '') + ' · 数据源 ' + (data.caliber && data.caliber.source ? data.caliber.source : '') +
          ' · 晋级率' + (promo.factor_available
            ? '：' + (promo.first_date || '') + '–' + (promo.last_date || '') + ' 共 ' + (promo.n_dates || 0) +
              ' 日可用（库内原值优先，缺失用本地涨停池复算 ' + (promo.caliber || '') + '）'
            : '：不可用（无涨停池样本，该因子不参与）') +
          ' · 溢价 premium 不可用：' + (promo.premium_reason || '本地无源') +
          ' · 只读：不改 track_pool.db、不写 data/panel。';
        root.append(foot);

        // 窗口尺寸变化重画曲线（SVG 用实测像素，不做百分比拉伸）
        if (observer) observer.disconnect();
        if (chartBox && typeof ResizeObserver === 'function') {
          let lastW = chartBox.clientWidth;
          observer = new ResizeObserver(function () {
            if (!chartBox) return;
            const w = chartBox.clientWidth;
            if (Math.abs(w - lastW) < 8) return;
            lastW = w;
            drawChart(chartBox, hist);
          });
          observer.observe(chartBox);
        }
      }

      async function load() {
        stamp.className = '';
        stamp.textContent = '情绪分读取中…';
        let data = null;
        try {
          data = await ctx.api.get('/payload?history_days=60');
        } catch (err) {
          stamp.textContent = '读取失败';
          showMessage('读取情绪分失败：' + (err && err.message ? err.message : err));
          return;
        }
        if (!data || !data.today) {
          stamp.textContent = '无数据';
          showMessage('后端没有返回今日情绪分（sentiment 表可能没有 ts=close 的收盘行）。');
          return;
        }
        stamp.textContent = '';
        stamp.append(
          document.createTextNode('数据日 '),
          el('b', null, data.asof || '—'),
          document.createTextNode(' · 收盘口径 ' + (data.n_trade_dates || 0) + ' 个交易日')
        );
        render(data);
      }

      btn.addEventListener('click', function () { load(); });

      const stage = rootEl.parentElement;
      fitWidth(root, stage);
      await load();

      activeCleanup = function () {
        if (observer) { observer.disconnect(); observer = null; }
        chartBox = null;
        payload = null;
      };
    },

    async unmount() {
      const fn = activeCleanup;
      activeCleanup = null;
      if (typeof fn === 'function') fn();
    },
  }],
};
