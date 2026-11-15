// 活跃市值卡片（原生 ES module，无构建）
// 样式一律走 themes.css 语义 token（唯一源 /legacy/themes.css）：
//   · 涨红跌绿 = --up / --down 家族，不写 hex；
//   · 面板底/描边 = --card2 / --line；次级文字 = --dim；
//   · 九套主题 + 三档字号由 html[data-theme]/[data-fs] 驱动，本卡不感知主题名。
const CSS = `
.amv-card{display:flex;flex-direction:column;gap:12px}
.amv-top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.amv-state{font-size:22px;font-weight:700;padding:2px 12px;border-radius:var(--radius-sm);background:var(--down-bg);color:var(--down-ink)}
.amv-state.long{background:var(--up-bg);color:var(--up-ink)}
.amv-state.flat{background:var(--line-soft);color:var(--dim)}
.amv-state.warn{background:var(--warn-bg);color:var(--warn-ink)}
.amv-main{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
.amv-pct{font-size:14px;color:var(--down-ink)}
.amv-pct.up{color:var(--up-ink)}
.amv-sub{color:var(--dim);font-size:12px}
.amv-grid{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:8px}
.amv-box{background:var(--card2);border-radius:var(--radius-sm);padding:9px 10px}
.amv-box .k{color:var(--dim);font-size:11px}
.amv-box .v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:3px}
.amv-warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:var(--radius-sm);padding:7px 10px;font-size:12px}
.amv-table{width:100%;border-collapse:collapse;font-size:12px}
.amv-table th,.amv-table td{padding:4px 6px;border-bottom:1px solid var(--line);text-align:right}
.amv-table th:first-child,.amv-table td:first-child{text-align:left}
.amv-table th{color:var(--dim);font-weight:500}
.amv-table td.up{color:var(--up-ink)}
.amv-table td.down{color:var(--down-ink)}
@media(max-width:720px){.amv-grid{grid-template-columns:repeat(2,1fr)}}
`;
if (typeof document !== "undefined" && !document.getElementById("amv-card-css")) {
  const s = document.createElement("style"); s.id = "amv-card-css"; s.textContent = CSS; document.head.appendChild(s);
}

const fmtYi = v => (v == null || !Number.isFinite(Number(v)) ? "—" : (Number(v) / 1e8).toFixed(2) + " 亿");
// regime 产物里的 ma10 / amv 已经是**亿元**（旧 8800 面板同口径），不能再除 1e8。
const fmtYiRaw = v => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(2) + " 亿");
const fmtPct = v => (v == null || !Number.isFinite(Number(v)) ? "—" : (Number(v) >= 0 ? "+" : "") + Number(v).toFixed(2) + "%");
const fmtNum = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(d));
const div = (cls, text) => { const d = document.createElement("div"); if (cls) d.className = cls; if (text != null) d.textContent = text; return d; };
const box = (k, v, cls) => { const b = div("amv-box"); b.append(div("k", k), div("v" + (cls ? " " + cls : ""), v)); return b; };

export default {
  cards: [{
    id: "amv.main",
    title: "活跃市值",
    slot: "main",
    order: 30,
    async mount(root, ctx) {
      root.textContent = "";
      const card = div("amv-card");
      root.append(card);
      let data;
      try { data = await ctx.api.get("/summary"); }
      catch (e) { card.append(div("amv-warn", `加载失败：${e.message || e}`)); return; }

      const top = div("amv-top");
      const state = div("amv-state" + (data.regime.state === "多" ? " long" : ""), data.regime.state || "—");
      top.append(state, div("amv-main", fmtYi(data.amv)), div("amv-pct" + (Number(data.amv_pct) >= 0 ? " up" : ""), fmtPct(data.amv_pct)));
      top.append(div("amv-sub", `${data.trade_date || "—"} · ${data.phase || ""} · ${data.asof || ""} · regime ${data.regime.date || "—"}`));
      card.append(top);

      const grid = div("amv-grid");
      grid.append(
        box("MA10（昨收）", fmtYiRaw(data.regime.ma10)),
        box("AMV / MA10（昨收）", fmtPct(data.regime.pct)),
        box("仓位上限", data.position_cap.final_pct == null ? "—" : `${data.position_cap.final_pct}%`),
        box("覆盖", data.coverage && data.coverage.pct != null ? `${(Number(data.coverage.pct) * 100).toFixed(2)}%` : "—"),
      );
      card.append(grid);

      if (data.consistency && (data.consistency.stale.live_vs_regime || data.consistency.stale.cap_vs_live)) {
        card.append(div("amv-warn", `口径日期不一致：live状态 ${data.consistency.live_state_date || data.consistency.live_date} / regime ${data.consistency.regime_date} / cap ${data.consistency.position_cap_date}`));
      }
      if (data.position_cap.warning) card.append(div("amv-warn", data.position_cap.warning));

      const breadth = data.breadth || {};
      const forecast = data.forecast || {};
      const grid2 = div("amv-grid");
      grid2.append(
        box("涨 / 跌", `${breadth.advance ?? "—"} / ${breadth.decline ?? "—"}`),
        box("预测", forecast.pred == null ? "—" : fmtPct(forecast.pred)),
        box("预测区间", forecast.p10 == null ? "—" : `${fmtPct(forecast.p10)} ~ ${fmtPct(forecast.p90)}`),
        box("最近切换", data.regime.last_switch && data.regime.last_switch.date ? `${data.regime.last_switch.date} ${data.regime.last_switch.dir || ""}` : "—"),
      );
      card.append(grid2);

      // 全A 指数快照（all_a_latest.json，只读）
      try {
        const aa = await ctx.api.get("/all_a");
        const g3 = div("amv-grid");
        g3.append(
          box("全A 收盘", aa.close == null ? "—" : Number(aa.close).toFixed(2)),
          box("全A 涨跌", aa.pct == null ? "—" : fmtPct(aa.pct)),
          box("距 MA5", aa.dist5_pct == null ? "—" : fmtPct(aa.dist5_pct)),
          box("涨 / 跌家数", `${aa.up_count ?? "—"} / ${aa.down_count ?? "—"}`),
        );
        card.append(g3);
      } catch (e) { card.append(div("amv-sub", `全A 快照加载失败：${e.message || e}`)); }

      try {
        const idx = await ctx.api.get("/indices");
        const table = document.createElement("table"); table.className = "amv-table";
        const thead = document.createElement("thead");
        thead.innerHTML = "<tr><th>指数</th><th>AMV</th><th>日变化</th></tr>";
        const tbody = document.createElement("tbody");
        (idx.indices || []).slice(0, 6).forEach(r => {
          const tr = document.createElement("tr");
          [r.name || r.code, fmtYi(r.amv), fmtPct(r.amv_pct)].forEach((v, i) => {
            const td = document.createElement("td"); td.textContent = v;
            // 涨红跌绿：正负各给一类，零值不着色（不把 0 染成跌色）
            if (i === 2 && r.amv_pct != null) td.className = Number(r.amv_pct) > 0 ? "up" : (Number(r.amv_pct) < 0 ? "down" : "");
            tr.append(td);
          });
          tbody.append(tr);
        });
        table.append(thead, tbody); card.append(table);
      } catch (e) { card.append(div("amv-sub", `指数表加载失败：${e.message || e}`)); }
    },
    async unmount() {},
    async refresh() {}
  }, {
    id: "amv.hcl",
    title: "HCL 离散度",
    slot: "main",
    order: 35,
    async mount(root, ctx) {
      root.textContent = "";
      const card = div("amv-card");
      root.append(card);
      let data;
      try { data = await ctx.api.get("/hcl"); }
      catch (e) { card.append(div("amv-warn", `HCL 读取失败：${e.message || e}`)); return; }
      if (!data || data.ok === false) {
        card.append(div("amv-warn", `HCL 不可用：${(data && (data.error || data.detail)) || "未知"}`));
        return;
      }

      const band = (v) => (v == null ? "—" : (v > 70 ? "分化" : (v >= 30 ? "正常" : "混沌")));
      const bandCls = (v) => (v != null && v > 70 ? "amv-state warn" : "amv-state flat");
      const top = div("amv-top");
      top.append(div(bandCls(data.hcl_raw), band(data.hcl_raw)),
                 div("amv-main", data.hcl_raw == null ? "—" : Number(data.hcl_raw).toFixed(2)),
                 div("amv-pct" + (Number(data.chg_1d) >= 0 ? " up" : ""),
                     data.chg_1d == null ? "—" : (Number(data.chg_1d) >= 0 ? "+" : "") + Number(data.chg_1d).toFixed(2)));
      const d8 = String(data.date || "");
      const pretty = d8.length === 8 ? `${d8.slice(0, 4)}-${d8.slice(4, 6)}-${d8.slice(6)}` : (d8 || "—");
      top.append(div("amv-sub", `${pretty} · 3日平滑 ${data.hcl_smooth ?? "—"} · 行业口径`));
      card.append(top);
      // 2026-11-10：数据源（通达信客户端 TQ 接口）已停用，产物停更 → 明说停留日期，
      // 不让旧值当新信号。换源属口径变更，未拍板前只标注、不重算。
      if (data.stale) {
        card.append(div("amv-warn", `读数停在 ${pretty}（${data.stale_note || "数据源已停用"}）—— 不是当日值`));
      }

      const grid = div("amv-grid");
      grid.append(
        box("5日变化", data.chg_5d == null ? "—" : (Number(data.chg_5d) >= 0 ? "+" : "") + Number(data.chg_5d).toFixed(2)),
        box("窗口内分位", data.pct_5y == null ? "—" : `${data.pct_5y}%`),
        box("历史峰值", data.peak_all && data.peak_all.value != null ? `${data.peak_all.value}` : "—"),
        box("峰值日期", data.peak_all && data.peak_all.date ? data.peak_all.date : "—"),
      );
      card.append(grid);
      card.append(div("amv-sub", "口径：同花顺申万2021 二级行业（90 个，与板块页面同源）90 日累计收益横截面标准差×4（hcl_calc_ths.py 落盘，本卡只读不重算）；区间 30–70 正常、>70 分化；历史仅约 4 年，分位/峰值为窗口内值"));
    },
    async unmount() {},
    async refresh() {}
  }]
};
