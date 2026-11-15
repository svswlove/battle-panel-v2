// K线插件卡片（原生 ES module，无构建）
//
// v0.4.0（2026-10-23）：新增「分时」视图 + 五档盘口 + 筹码峰。
// 日线视图保留原样（含 AMV 四态背景带）。
// 数据来源：/bars /bg 读库；/intraday /quote5 /volume_profile 走 core 的 eltdx 行情门面。
// 样式一律走 themes.css 语义 token（唯一源 /legacy/themes.css）：
//   · 涨红跌绿 = --up* / --down*；面板底 = --card2；描边 = --line；次级文字 = --dim；
//   · canvas 读不到 CSS 变量，统一在重画时用 palette() 从 token 现取（主题切后重画即跟随）。
const CSS = `
.kl-card{display:flex;flex-direction:column;gap:10px}
.kl-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.kl-input,.kl-btn,.kl-sel{background:var(--card2);color:var(--fg);border:1px solid var(--line);border-radius:var(--radius-sm);padding:5px 8px}
.kl-input{width:130px;font-family:var(--font-mono)}
.kl-btn,.kl-sel{cursor:pointer}
.kl-btn:hover,.kl-sel:hover{border-color:var(--accent)}
.kl-status{color:var(--dim);font-size:12px;margin-left:auto}
.kl-badge{color:var(--warn-ink);border:1px solid var(--warn);background:var(--warn-bg);border-radius:var(--radius-pill);padding:2px 8px;font-size:11px;white-space:nowrap}
.kl-badge.is-live{color:var(--down-ink);border-color:var(--down);background:var(--down-bg)}
.kl-canvas{width:100%;height:280px;background:var(--bg);border-radius:var(--radius-sm);display:block}
.kl-foot{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
.kl-panel{flex:1 1 300px;min-width:260px;max-width:520px;background:var(--card2);border-radius:var(--radius-sm);padding:8px 10px}
.kl-ptitle{color:var(--dim);font-size:11px;margin-bottom:6px}
.kl-q5{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}
.kl-q5 td{padding:2px 4px}
.kl-q5 .lbl{color:var(--dim);width:44px}
.kl-q5 .num{text-align:right;font-family:var(--font-mono);width:50%}
.kl-q5 .ask{color:var(--down-ink)}
.kl-q5 .bid{color:var(--up-ink)}
.kl-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 10px;font-size:12px;margin-top:8px}
.kl-stats .k{color:var(--dim);font-size:11px}
.kl-stats .v{font-family:var(--font-mono)}
.kl-stats .v.up{color:var(--up-ink)}
.kl-stats .v.down{color:var(--down-ink)}
.kl-empty{color:var(--dim);font-size:12px;padding:8px 0}
@media(max-width:760px){.kl-canvas{height:220px}}
`;
if (typeof document !== "undefined" && !document.getElementById("kline-card-css")) {
  const s = document.createElement("style"); s.id = "kline-card-css"; s.textContent = CSS; document.head.appendChild(s);
}

// themes.css token → 画布可用颜色。每个函数调用时现取，主题切换后下一次重画即生效。
// token 取不到时退回 `currentColor` / `transparent` / `monospace` 这类语义值，
// **不在卡内另写一份色表**（回退值里不出现任何 hex）。
function tok(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  } catch (e) { return fallback; }
}

function palette() {
  return {
    up: tok("--up-solid", "currentColor"),
    down: tok("--down-solid", "currentColor"),
    upBg: tok("--up-bg", "transparent"),
    downBg: tok("--down-bg", "transparent"),
    accent: tok("--accent", "currentColor"),
    warn: tok("--warn-solid", "currentColor"),
    dim: tok("--dim", "currentColor"),
    fg: tok("--fg", "currentColor"),
    line: tok("--line", "currentColor"),
    grid: tok("--line-soft", "transparent"),
    mono: tok("--font-mono", "monospace"),
  };
}

function setup(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 640, h = canvas.clientHeight || 280;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const c = canvas.getContext("2d");
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.scale(dpr, dpr); c.clearRect(0, 0, w, h);
  return { c, w, h };
}

// ---------------------------------------------------------------- 日线

// 2026-10-27 验收返修（只动 drawDaily 的价格轴这一处）：
//   原先轴只取 close+yellow+white，而 Y 又拿去定位 开/高/低 —— 最高/最低超出该区间时蜡烛就画出图外；
//   且弹窗的标记线与右键反算用的是另一份轴（marks.js::scaleFor），两边对不上。
//   现改为两边共用 marks.js::dailyAxis（唯一源），本文件不再自备一份。
import { dailyAxis, intradayAxis, overlayExtent } from './frontend/marks.js?v=0.9.67';

export function drawDaily(canvas, bars, states, opts) {
  const { c, w, h } = setup(canvas);
  if (!bars.length) return;
  const P = palette();
  const n = bars.length;
  // 价格轴（唯一源 marks.js::dailyAxis）：范围覆盖 开/高/低/收 + 黄白线，蜡烛不出界。
  const AX = dailyAxis(bars, w, h);
  if (!AX) return;
  const pad = AX.pad;
  const min = AX.lo, max = AX.hi;
  const X = AX.xAt;
  const Y = AX.yOf;

  // 四态背景带：红=多/偏多，绿=空/偏空
  if (states && states.length === n) {
    const step = n > 1 ? (w - pad * 2) / (n - 1) : 0;
    for (let i = 0; i < n; i++) {
      const st = states[i];
      if (!st) continue;
      const bull = st === "多" || st === "偏多";
      c.fillStyle = bull ? P.upBg : P.downBg;
      const x0 = n === 1 ? pad : X(i) - step / 2;
      c.fillRect(Math.max(pad, x0), pad, Math.max(1, step), h - pad * 2);
    }
  }

  // 动能K 染色（2026-11-02 主人裁定：只要红柱）—— 已删除原公式的超卖蓝底（DRAWGBK_DIV）。
  // dn 由调用方（overlay.js）算好传入，本文件不持有算子（算子唯一源 indicators.js::dongneng）。
  const dn = (opts && opts.dn) || null;

  // 黄白线（口径源 engine/common/indicators.py；黄线前 114 根为 null，自然断线）
  const drawLine = (key, color, width) => {
    let started = false;
    c.strokeStyle = color; c.lineWidth = width; c.beginPath();
    for (let i = 0; i < n; i++) {
      const v = bars[i][key];
      if (v == null || !Number.isFinite(Number(v))) { started = false; continue; }
      const x = X(i), y = Y(Number(v));
      if (!started) { c.moveTo(x, y); started = true; } else { c.lineTo(x, y); }
    }
    c.stroke();
  };
  drawLine("white", P.fg, 1.0);
  drawLine("yellow", P.warn, 1.2);

  // 蜡烛图（2026-10-26 补）：改前这里只画收盘折线，`open/high/low` 拿到了却没画，
  // 看上去像「走势图」而不是 K 线。口径：涨=实心 up、跌=实心 down（A 股红涨绿跌）。
  // 宽度自适应：��根太窄时退化为竖线（只画最高→最低），避免互相重叠成一片黑。
  const bw = Math.max(1, Math.min(9, (w - pad * 2) / Math.max(n, 1) * 0.62));
  bars.forEach((b, i) => {
    const o = Number(b.open), cl = Number(b.close);
    const hi = Number(b.high), lo2 = Number(b.low);
    if (![o, cl, hi, lo2].every(Number.isFinite)) return;
    const up = cl >= o;
    const col = up ? P.up : P.down;
    const x = X(i);
    // 影线
    c.strokeStyle = col; c.lineWidth = 1;
    c.beginPath(); c.moveTo(x, Y(hi)); c.lineTo(x, Y(lo2)); c.stroke();
    // 实体（开收价相等时画一条横线）
    const yO = Y(o), yC = Y(cl);
    const top = Math.min(yO, yC), hgt = Math.max(1, Math.abs(yC - yO));
    c.fillStyle = col;
    c.fillRect(x - bw / 2, top, bw, hgt);
  });

  // 收盘价细线只作辅助（蜡烛画完后叠在上面，主题换色后仍可辨）
  c.strokeStyle = P.accent; c.lineWidth = 1; c.globalAlpha = .55; c.beginPath();
  bars.forEach((b, i) => {
    const v = Number(b.close);
    if (!Number.isFinite(v)) return;
    i ? c.lineTo(X(i), Y(v)) : c.moveTo(X(i), Y(v));
  });
  c.stroke(); c.globalAlpha = 1;

  // 动能K 染色（opts.dn.sig，2026-11-02 新增）：口径同通达信
  //   STICKLINE(EKPRO OR V2XEK OR V4XEK OR V8XEK, O, C, 3, 0), COLOR3D3DFF;
  // 颜色固定亮红（通达信 COLOR3D3DFF 原值，不跟主题涨色走 —— 跟阳线同色就失去标记意义）。
  // 2026-11-09：此处的 HE 色值已在 `test_fe_shell_bridge.RGBA_ALLOWED_BY_FILE` 登记（canvas
  //   读不到 CSS 变量，且要求跨主题保持同一个标记身份）。
  // 画在最上层盖住原蜡烛与收盘细线，宽度比普通柱加宽一截，一眼能挑出信号日。
  if (dn && dn.sig && dn.sig.length === n) {
    const wMark = Math.max(bw + 2, Math.min(bw * 1.8, 12));
    bars.forEach((b, i) => {
      if (!dn.sig[i]) return;
      const o = Number(b.open), cl = Number(b.close);
      if (!Number.isFinite(o) || !Number.isFinite(cl)) return;
      const yO = Y(o), yC = Y(cl);
      const top = Math.min(yO, yC), hgt = Math.max(2, Math.abs(yC - yO));
      c.fillStyle = "rgb(255,61,61)";
      c.fillRect(X(i) - wMark / 2, top, wMark, hgt);
    });
  }

  c.fillStyle = P.dim; c.font = "11px sans-serif";
  c.fillText(max.toFixed(2), pad, 14); c.fillText(min.toFixed(2), pad, h - 6);
  c.fillText(bars[0].date, pad, h - 8);
  c.fillText(bars[bars.length - 1].date, w - 86, h - 8);
  // 图例
  c.fillStyle = P.warn; c.fillText("黄线", pad + 2, 26);
  c.fillStyle = P.fg; c.fillText("白线", pad + 34, 26);
}

// ---------------------------------------------------------------- 分时

/**
 * 分时图（P1b 起轴/叠加共用唯一源 marks.js::intradayAxis）。
 * overlay: {series:[{label, color, pct:[...]}]} —— pct 已按时间槽对到主图 slots 上，null = 空点不画。
 * 键口径：主图刻度是「涨跌幅%」，0% = 昨收 = 竖向中点；叠加线也按各自昨收换算成 %，
 * 两条通路共用同一根轴 ⇒ 不会出现「线按 %、轴按价格」的错位。
 */
export function drawIntraday(canvas, d, overlay) {
  const { c, w, h } = setup(canvas);
  const P = palette();
  const pts = d.points || [];
  if (!pts.length) {
    c.fillStyle = P.dim; c.font = "12px sans-serif";
    c.fillText("无分时数据", 12, 22);
    return;
  }
  const series = ((overlay && overlay.series) || []).filter(s => s && s.pct && s.pct.length).slice(0, 3);
  const AX = intradayAxis(d, w, h, overlayExtent(series));
  if (!AX) {
    // 没有昨收就定不出 0% 中轴：明确说，不拿收盘当基准瞎画
    c.fillStyle = P.dim; c.font = "12px sans-serif";
    c.fillText("缺昨收，无法定涨跌幅轴", 12, 22);
    return;
  }
  const pre = AX.pre;
  const slots = AX.slots;
  const padL = AX.rect.l, padR = w - AX.rect.r, padT = AX.rect.t, plotH = AX.plotH;
  const lo = AX.lo, hi = AX.hi;
  const nSlots = AX.n;
  const X = (i) => AX.xAt(i, nSlots);
  const Y = AX.yOf;

  // 网格 + 昨收虚线
  c.strokeStyle = P.grid; c.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padT + plotH * i / 4;
    c.beginPath(); c.moveTo(padL, y); c.lineTo(w - padR, y); c.stroke();
  }
  if (Number.isFinite(pre)) {
    const y = Y(pre);
    c.strokeStyle = P.dim; c.globalAlpha = .5; c.setLineDash([4, 4]);
    c.beginPath(); c.moveTo(padL, y); c.lineTo(w - padR, y); c.stroke();
    c.setLineDash([]); c.globalAlpha = 1;
    // 涨跌幅刻度（左轴）
    c.font = "10px " + P.mono; c.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = hi - (hi - lo) * i / 4;
      const pct = ((v - pre) / pre) * 100;
      c.fillStyle = pct > 0.001 ? P.up : (pct < -0.001 ? P.down : P.dim);
      c.fillText(`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`, padL - 6, padT + plotH * i / 4 + 3);
    }
    c.textAlign = "left";
  }

  // 午休分界（11:30 / 13:01 之间留一条竖线）
  const noonIdx = slots.indexOf("11:30");
  if (noonIdx >= 0) {
    c.strokeStyle = P.line; c.globalAlpha = .6;
    c.beginPath(); c.moveTo(X(noonIdx), padT); c.lineTo(X(noonIdx), padT + plotH); c.stroke();
    c.globalAlpha = 1;
  }

  // 价格线：以昨收为基准分色（涨红跌绿）
  const color = (v) => (Number.isFinite(pre) ? (v >= pre ? P.up : P.down) : P.accent);
  c.lineWidth = 1.6; c.beginPath();
  pts.forEach((p, k) => {
    const x = X(p.i != null ? p.i : k), y = Y(Number(p.close));
    k ? c.lineTo(x, y) : c.moveTo(x, y);
  });
  c.strokeStyle = color(Number(pts[pts.length - 1].close)); c.stroke();

  // 均价线（黄）：必须每个点都有有效均价才画
  // （原先 Number(null) === 0 会把缺均价的点当成 0 价，画出贴底的假线）
  const hasAvg = pts.every(p => p.avg != null && Number.isFinite(Number(p.avg)) && Number(p.avg) > 0);
  if (hasAvg) {
    c.strokeStyle = P.warn; c.lineWidth = 1.1; c.beginPath();
    pts.forEach((p, k) => {
      const x = X(p.i != null ? p.i : k), y = Y(Number(p.avg));
      k ? c.lineTo(x, y) : c.moveTo(x, y);
    });
    c.stroke();
  }

  // 时间刻度
  c.fillStyle = P.dim; c.font = "10px sans-serif";
  const marks = ["09:30", "10:30", "11:30", "14:00", "15:00"];
  marks.forEach((m) => {
    const i = slots.indexOf(m);
    if (i >= 0) c.fillText(m, Math.min(X(i) - 12, w - padR - 30), h - 5);
  });

  // 叠加线（P1b）：各自按昨收换算成 %，落在同一根轴上；null 处断线（停牌/缺槽不补值）
  if (series.length) {
    series.forEach((s) => {
      c.strokeStyle = s.color || P.accent;
      c.lineWidth = 1.3; c.globalAlpha = 0.95; c.setLineDash([]);
      c.beginPath();
      let started = false;
      s.pct.forEach((p, i) => {
        const y = (p == null) ? null : AX.yOfPct(p);
        if (y == null) { started = false; return; }
        const x = X(i);
        if (!started) { c.moveTo(x, y); started = true; } else { c.lineTo(x, y); }
      });
      c.stroke(); c.globalAlpha = 1;
    });
    // 图例：右上角一行一个（颜色 + 名称 + 末值%）
    c.font = "10px " + P.mono; c.textAlign = "right";
    series.forEach((s, k) => {
      const last = s.pct.filter(v => v != null).pop();
      const txt = (s.label || '') + (last == null ? '' : ' ' + (last >= 0 ? '+' : '') + Number(last).toFixed(2) + '%');
      c.fillStyle = s.color || P.accent;
      c.fillText(txt, w - padR - 2, padT + 11 + k * 12);
    });
    c.textAlign = "left";
  }

  // 最新价标签
  const last = pts[pts.length - 1];
  const y = Y(Number(last.close));
  c.fillStyle = color(Number(last.close));
  c.fillRect(padL, y - 1, w - padL - padR, 1);
  c.textAlign = "left"; c.font = "11px " + P.mono;
  c.fillText(String(last.close.toFixed(2)), w - padR - 44, Math.max(10, y - 4));
}

// ---------------------------------------------------------------- 筹码峰（叠在分时右侧）

export function drawChips(canvas, vp, d) {
  const { c, w, h } = setup(canvas);
  const P = palette();
  const buckets = vp.buckets || [];
  if (!buckets.length) {
    c.fillStyle = P.dim; c.font = "12px sans-serif";
    c.fillText("无筹码数据", 12, 22);
    return;
  }
  const pre = vp.pre_close || (d && d.pre_close);
  const prices = buckets.map(b => Number(b.p));
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const maxV = Math.max(...buckets.map(b => Number(b.v)), 1);
  const padL = 8, padR = 52, padT = 10, padB = 16;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const Y = (p) => padT + plotH * (1 - (p - lo) / ((hi - lo) || 1));

  for (const b of buckets) {
    const p = Number(b.p), v = Number(b.v);
    const len = Math.max(1, plotW * (v / maxV));
    const y = Y(p);
    const up = Number.isFinite(pre) ? p >= pre : true;
    c.fillStyle = up ? P.up : P.down;
    c.globalAlpha = .55;
    c.fillRect(padL, y - 1, len, 2);
    c.globalAlpha = 1;
  }
  if (Number.isFinite(pre)) {
    const y = Y(pre);
    c.strokeStyle = P.dim; c.globalAlpha = .5; c.setLineDash([4, 4]);
    c.beginPath(); c.moveTo(padL, y); c.lineTo(w - padR, y); c.stroke();
    c.setLineDash([]); c.globalAlpha = 1;
    c.fillStyle = P.dim; c.font = "10px " + P.mono;
    c.fillText("昨收", w - padR + 4, y + 3);
  }
  c.fillStyle = P.dim; c.font = "10px sans-serif";
  c.fillText(hi.toFixed(2), padL, padT + 8);
  c.fillText(lo.toFixed(2), padL, h - 5);
}

// ---------------------------------------------------------------- 盘口

export function renderQuote5(box, q) {
  const P = palette();
  box.textContent = "";
  const title = document.createElement("div");
  title.className = "kl-ptitle";
  title.textContent = q && q.ok ? `五档盘口 · ${q.quote_time || "—"}` : "五档盘口";
  box.append(title);
  if (!q || !q.ok) {
    const e = document.createElement("div"); e.className = "kl-empty";
    e.textContent = q && q.reason ? `不可用：${q.reason}` : "行情源不可用";
    box.append(e);
    return;
  }
  const table = document.createElement("table");
  table.className = "kl-q5";
  const fmtVol = (v) => (v == null ? "—" : String(v));
  const rows = [];
  // 卖5→卖1（上到下），买1→买5
  const asks = (q.asks || []).slice(0, 5);
  for (let i = asks.length - 1; i >= 0; i--) {
    rows.push({ lbl: `卖${i + 1}`, price: asks[i].price, vol: asks[i].vol, cls: "ask" });
  }
  rows.push({ sep: true });
  const bids = (q.bids || []).slice(0, 5);
  bids.forEach((b, i) => {
    rows.push({ lbl: `买${i + 1}`, price: b.price, vol: b.vol, cls: "bid" });
  });
  for (const r of rows) {
    const tr = document.createElement("tr");
    if (r.sep) {
      const td = document.createElement("td");
      td.colSpan = 3;
      td.style.borderTop = "1px solid var(--line)";
      td.style.padding = "4px 0";
      tr.append(td);
      table.append(tr);
      continue;
    }
    const t1 = document.createElement("td"); t1.className = "lbl"; t1.textContent = r.lbl;
    const t2 = document.createElement("td"); t2.className = `num ${r.cls}`;
    t2.textContent = Number(r.price).toFixed(2);
    const t3 = document.createElement("td"); t3.className = "num"; t3.textContent = fmtVol(r.vol);
    tr.append(t1, t2, t3);
    table.append(tr);
  }
  box.append(table);

  const s = q.stats || {};
  const grid = document.createElement("div");
  grid.className = "kl-stats";
  const pct = q.change_pct;
  const head = document.createElement("div");
  head.style.gridColumn = "1 / -1";
  head.style.display = "flex";
  head.style.gap = "10px";
  head.style.alignItems = "baseline";
  const price = document.createElement("span");
  price.textContent = q.last == null ? "—" : Number(q.last).toFixed(2);
  price.style.fontSize = "18px"; price.style.fontWeight = "700";
  price.style.color = pct > 0 ? P.up : (pct < 0 ? P.down : "inherit");
  const chg = document.createElement("span");
  chg.textContent = `${q.change == null ? "—" : (q.change >= 0 ? "+" : "") + Number(q.change).toFixed(2)}`
    + ` ${pct == null ? "" : (pct >= 0 ? "+" : "") + Number(pct).toFixed(2) + "%"}`;
  chg.style.color = pct > 0 ? P.up : (pct < 0 ? P.down : P.dim);
  chg.style.fontSize = "12px";
  head.append(price, chg);
  grid.append(head);
  [
    ["今开", "—"], // 旧端 quote5 无 open 字段，不编造
    ["最高", q.high == null ? "—" : Number(q.high).toFixed(2)],
    ["最低", q.low == null ? "—" : Number(q.low).toFixed(2)],
    ["涨停", q.limit_up == null ? "—" : Number(q.limit_up).toFixed(2)],
    ["跌停", q.limit_down == null ? "—" : Number(q.limit_down).toFixed(2)],
    ["昨收", q.pre_close == null ? "—" : Number(q.pre_close).toFixed(2)],
    ["总量", s.total_vol == null ? "—" : `${s.total_vol} 手`],
    ["现量", s.now_vol == null ? "—" : `${s.now_vol} 手`],
    ["换手", s.turnover == null ? "—" : `${Number(s.turnover).toFixed(2)}%`],
    ["外盘", s.outer == null ? "—" : String(s.outer)],
    ["内盘", s.inner == null ? "—" : String(s.inner)],
    ["量比", s.vol_ratio == null ? "—" : Number(s.vol_ratio).toFixed(2)],
  ].forEach(([k, v]) => {
    if (k === "今开") return; // 占位，避免误显示「—」
    const kd = document.createElement("div"); kd.className = "k"; kd.textContent = k;
    const vd = document.createElement("div"); vd.className = "v"; vd.textContent = v;
    const cell = document.createElement("div");
    cell.append(kd, vd);
    grid.append(cell);
  });
  box.append(grid);

  const ticks = q.ticks || [];
  if (ticks.length) {
    const t = document.createElement("div");
    t.className = "kl-ptitle";
    t.style.marginTop = "8px";
    t.textContent = "最近成交";
    box.append(t);
    const tt = document.createElement("table");
    tt.className = "kl-q5";
    ticks.slice(0, 8).forEach((k) => {
      const tr = document.createElement("tr");
      const a = document.createElement("td"); a.className = "lbl"; a.textContent = k.t || "—";
      const b = document.createElement("td"); b.className = "num";
      b.textContent = Number(k.price).toFixed(2);
      b.style.color = k.side === "buy" ? P.up : (k.side === "sell" ? P.down : "inherit");
      const c2 = document.createElement("td"); c2.className = "num"; c2.textContent = String(k.vol);
      tr.append(a, b, c2);
      tt.append(tr);
    });
    box.append(tt);
  }
}

// 2026-10-27 主人定：弹窗入口改回**旧版完整弹窗原样挂载**（legacy_modal.js 装旧 DOM/CSS/四个旧模块）。
// 上一版自绘的简化弹窗（frontend/overlay.js 及其 marks/indicators/subcharts/order_panel）**保留不动**，
// 作回退用：回退 = 把下一行换回 `./frontend/overlay.js?v=0.8.5`。
import { openKline, closeKline } from "./frontend/legacy_modal.js?v=0.9.67";

export default {
  cards: [{
    id: "kline.main",
    title: "K线",
    slot: "main",
    order: 20,
    // 分时视图盘中需要跟着走；日线视图不必（日频）。由 refresh 自己判断当前视图。
    refreshEvery: 20000,
    async mount(root, ctx) {
      root.textContent = "";
      const card = document.createElement("div"); card.className = "kl-card";
      root.append(card);

      const bar = document.createElement("div"); bar.className = "kl-bar";
      const input = document.createElement("input");
      input.className = "kl-input";
      // 入参：从搜索面板/其它卡跳过来时带 code（ctx.card.args，一次性），否则默认 600519.SH。
      // 只认「带后缀」的代码（600519.SH / 886108.TI），裸码不猜市场。
      const argCode = (ctx && ctx.card && ctx.card.args && ctx.card.args.code) ? String(ctx.card.args.code).trim() : "";
      input.value = /^\d{6}(\.[A-Z]{2,3})?$/.test(argCode) ? argCode : "600519.SH";
      const modeSel = document.createElement("select"); modeSel.className = "kl-sel";
      [["intraday", "分时"], ["daily", "日线"]].forEach(([v, t]) => {
        const o = document.createElement("option"); o.value = v; o.textContent = t; modeSel.append(o);
      });
      const btn = document.createElement("button"); btn.className = "kl-btn"; btn.textContent = "加载";
      const badge = document.createElement("span"); badge.className = "kl-badge";
      const status = document.createElement("span"); status.className = "kl-status";
      bar.append(input, modeSel, btn, badge, status);

      const canvas = document.createElement("canvas"); canvas.className = "kl-canvas";
      const foot = document.createElement("div"); foot.className = "kl-foot";
      const q5box = document.createElement("div"); q5box.className = "kl-panel";
      const chipBox = document.createElement("div"); chipBox.className = "kl-panel";
      chipBox.style.flex = "0 0 300px";
      chipBox.style.maxWidth = "300px";
      const chipTitle = document.createElement("div");
      chipTitle.className = "kl-ptitle"; chipTitle.textContent = "筹码分布（当日）";
      const chipCanvas = document.createElement("canvas");
      chipCanvas.style.width = "100%"; chipCanvas.style.height = "200px";
      chipCanvas.style.background = "var(--bg)"; chipCanvas.style.borderRadius = "var(--radius-sm)";
      chipCanvas.style.display = "block";
      chipBox.append(chipTitle, chipCanvas);
      foot.append(q5box, chipBox);
      card.append(bar, canvas, foot);

      let mode = "intraday";
      let lastData = null;

      const loadDaily = async (code) => {
        // 默认 120 根：黄线需要窗口内 ≥115 根才出值（口径源 B1 降级口径），
        // 只取 60 根时黄线整段 null（旧端同样）。
        const data = await ctx.api.get(`/bars/${encodeURIComponent(code)}?limit=120&adjust=qfq`);
        let states = null;
        try {
          const bg = await ctx.api.get(`/bg/${encodeURIComponent(code)}?limit=120`);
          states = bg.states || null;
        } catch (e) { states = null; }
        drawDaily(canvas, data.bars || [], states);
        const last = data.last_bar_date || (data.bars.length ? data.bars[data.bars.length - 1].date : "—");
        const bgNote = states ? ` · 背景带 ${states.filter((s) => s).length}/${states.length}` : "";
        badge.className = "kl-badge";
        badge.textContent = "历史日线 · 黄白线 · AMV四态";
        badge.title = data.notice || "";
        status.textContent = `${code} · ${data.bars.length} 根 · 至 ${last} · ${data.warnings.join("；") || "qfq"}${bgNote}`;
        foot.style.display = "none";
      };

      const isIndexLike = (code) => {
        // 指数/板块没有五档与逐笔筹码，别拿两块“不可用”去碍眼。
        // 注意 000001.SH 是上证指数、000001.SZ 是平安银行——必须看后缀。
        const u = String(code || "").toUpperCase();
        if (u.endsWith(".TI") || u.endsWith(".BK") || u.startsWith("BK")) return true;
        if (u.endsWith(".SH") && /^000\d{3}/.test(u)) return true;
        if (u.endsWith(".SZ") && /^399\d{3}/.test(u)) return true;
        if (u.endsWith(".CSI")) return true;
        return false;
      };

      const loadIntraday = async (code) => {
        const d = await ctx.api.get(`/intraday/${encodeURIComponent(code)}`);
        lastData = d;
        drawIntraday(canvas, d);
        badge.className = "kl-badge is-live";
        badge.textContent = "分时 · 实时";
        badge.title = d.notice || "";
        const pct = d.pct == null ? "" : `${d.pct >= 0 ? "+" : ""}${Number(d.pct).toFixed(2)}%`;
        status.textContent = `${code} · ${d.count} 点 · 昨收 ${d.pre_close == null ? "—" : Number(d.pre_close).toFixed(2)}`
          + ` · 现价 ${d.last == null ? "—" : Number(d.last).toFixed(2)} ${pct}`;
        // 指数/板块：只画分时，不拉五档和筹码
        if (isIndexLike(code)) {
          foot.style.display = "none";
          return;
        }
        foot.style.display = "flex";
        // 五档
        try {
          const q = await ctx.api.get(`/quote5/${encodeURIComponent(code)}`);
          renderQuote5(q5box, q);
        } catch (e) {
          renderQuote5(q5box, { ok: false, reason: e.message || String(e) });
        }
        // 筹码
        try {
          const vp = await ctx.api.get(`/volume_profile/${encodeURIComponent(code)}`);
          drawChips(chipCanvas, vp, d);
        } catch (e) {
          const { c } = setup(chipCanvas);
          const P = palette();
          c.fillStyle = P.dim; c.font = "12px sans-serif";
          c.fillText(`筹码不可用：${e.message || e}`, 8, 20);
        }
      };

      const load = async () => {
        const code = input.value.trim();
        if (!code) return;
        mode = modeSel.value;
        status.textContent = "加载中…";
        try {
          if (mode === "daily") await loadDaily(code);
          else await loadIntraday(code);
        } catch (e) {
          status.textContent = `失败：${e.message || e}`;
        }
      };

      btn.addEventListener("click", load);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
      modeSel.addEventListener("change", load);
      // 窗口尺寸变化后重画（canvas 用的是 clientWidth）
      const onResize = () => {
        if (mode === "daily") loadDaily(input.value.trim()).catch(() => {});
        else if (lastData) { drawIntraday(canvas, lastData); }
      };
      window.addEventListener("resize", onResize);

      await load();
      this._load = load;
      this._mode = () => mode;
      this._onResize = onResize;
    },
    async unmount() {
      if (this._onResize) window.removeEventListener("resize", this._onResize);
      this._load = null; this._mode = null; this._onResize = null;
    },
    async refresh() {
      // 只有分时视图需要跟；日线是日频，重拉纯浪费
      if (typeof this._mode === "function" && this._mode() === "intraday" && this._load) {
        await this._load();
      }
    }
  }]
};

// 壳层弹窗入口（2026-10-26）：shell.js 的 ctxOpenKline 直接调本函数，
// **不切卡**（旧版行为：就地弹窗、动前不动当前页）。
// 参数 { api, code, name, period }，api 由 shell 的 makeCtx 提供。
export { openKline, closeKline };
export const openKlineStandalone = openKline;
