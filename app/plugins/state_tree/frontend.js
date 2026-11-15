// 多空判定状态树卡片（原生 ES module，无构建）
// 样式一律走 themes.css 语义 token（唯一源 /legacy/themes.css）：不写 hex，不感知主题名。
// 四态配色：未达成=dim / 盘中达成=warn / 收盘确认=up / 否决=down / 作废=灰（line-soft）
const CSS = `
.st-card{display:flex;flex-direction:column;gap:12px}
.st-top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.st-badge{font-size:20px;font-weight:700;padding:2px 12px;border-radius:var(--radius-sm);
  background:var(--line-soft);color:var(--dim)}
.st-badge.lit{background:var(--up-bg);color:var(--up-ink)}
.st-badge.pending{background:var(--warn-bg);color:var(--warn-ink)}
.st-badge.no{background:var(--down-bg);color:var(--down-ink)}
.st-badge.void{background:var(--line-soft);color:var(--dim);text-decoration:line-through}
.st-main{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.st-pct{font-size:14px}
.st-pct.up{color:var(--up-ink)}.st-pct.down{color:var(--down-ink)}
.st-sub{color:var(--dim);font-size:12px}
.st-sec{font-size:12px;color:var(--dim);margin-top:2px}
.st-nodes{display:flex;flex-direction:column;gap:6px}
.st-row{display:grid;grid-template-columns:1fr auto auto;gap:10px;align-items:center;
  background:var(--card2);border-radius:var(--radius-sm);padding:7px 10px;font-size:13px}
.st-row .lbl{color:var(--dim)}
.st-row .val{font-variant-numeric:tabular-nums}
.st-row .st{font-size:12px;padding:1px 8px;border-radius:var(--radius-sm);
  background:var(--line-soft);color:var(--dim)}
.st-row .st.live{background:var(--warn-bg);color:var(--warn-ink)}
.st-row .st.close{background:var(--up-bg);color:var(--up-ink)}
.st-row .st.veto{background:var(--down-bg);color:var(--down-ink)}
.st-row .st.void{text-decoration:line-through}
.st-warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:var(--radius-sm);padding:7px 10px;font-size:12px}
.st-foot{color:var(--dim);font-size:11px;line-height:1.6}
.st-grid{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:8px}
.st-box{background:var(--card2);border-radius:var(--radius-sm);padding:8px 10px}
.st-box .k{color:var(--dim);font-size:11px}
.st-box .v{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:3px}
@media(max-width:720px){.st-grid{grid-template-columns:repeat(2,1fr)}}
`;
if (typeof document !== "undefined" && !document.getElementById("state-tree-card-css")) {
  const s = document.createElement("style"); s.id = "state-tree-card-css"; s.textContent = CSS;
  document.head.appendChild(s);
}

const num = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(d));
const pct = v => (v == null || !Number.isFinite(Number(v)) ? "—" : (Number(v) >= 0 ? "+" : "") + Number(v).toFixed(2) + "%");
const div = (cls, text) => { const d = document.createElement("div"); if (cls) d.className = cls; if (text != null) d.textContent = text; return d; };
const box = (k, v) => { const b = div("st-box"); b.append(div("k", k), div("v", v)); return b; };

// 节点态 → CSS 类；「作废」用灰删除线
const STATE_CLS = { "未达成": "", "缺数": "", "占位": "", "盘中达成": "live", "收盘确认": "close", "否决": "veto", "作废": "void" };
const RESULT_CLS = { "点亮": "lit", "待确认": "pending", "不点亮": "no", "作废": "void", "未启动": "" };

// `?asof=YYYY-MM-DD` 回放：同壳 `?card=` 一起用，只看历史某日的判定结果（不传 = 最新）。
function asofQS() {
  if (typeof location === "undefined") return "";
  const v = (new URLSearchParams(location.search).get("asof") || "").trim();
  return v ? `?asof=${encodeURIComponent(v)}` : "";
}

function nodeRow(label, valueText, state) {
  const row = div("st-row");
  row.append(div("lbl", label), div("val", valueText), div("st " + (STATE_CLS[state] || ""), state || "—"));
  return row;
}

export default {
  cards: [{
    id: "state_tree.main",
    title: "多空判定",
    slot: "main",
    order: 40,
    async mount(root, ctx) {
      root.textContent = "";
      const card = div("st-card");
      root.append(card);
      let data;
      try { data = await ctx.api.get("/tree" + asofQS()); }
      catch (e) { card.append(div("st-warn", `加载失败：${e.message || e}`)); return; }

      const bull = data.bull || {};
      const bear = data.bear || {};
      const top = div("st-top");
      top.append(
        div("st-badge " + (RESULT_CLS[bull.result] || ""), `多头 · ${bull.result || "—"}`),
        div("st-badge " + (RESULT_CLS[bear.result] || ""), `空头 · ${bear.result || "—"}`),
        div("st-main", `AMV ${num(data.amv)} 亿`),
        div("st-pct " + (Number(data.pct) >= 0 ? "up" : "down"), pct(data.pct)),
      );
      top.append(div("st-sub", `${data.asof || "—"} · ${data.phase === "live" ? "盘中" : "收盘"} · MA10 ${num(data.ma10)}`));
      card.append(top);

      // 读数
      const g = div("st-grid");
      g.append(
        box("AMV", `${num(data.amv)} 亿`),
        box("MA10", num(data.ma10)),
        box("AMV / 前日", pct(data.pct)),
        box("回放区间", `${(data.days || []).length} 日`),
      );
      card.append(g);

      // 多头状态树
      card.append(div("st-sec", "多头启动 —— 触发（任一）"));
      const trg = div("st-nodes");
      for (const n of (bull.trigger && bull.trigger.nodes) || []) {
        trg.append(nodeRow(`${n.label}`, pct(n.value), n.met ? (data.phase === "live" ? "盘中达成" : "收盘确认") : "未达成"));
      }
      card.append(trg);

      const pat = bull.pattern || {};
      const dl = bull.deadline || {};
      const rows2 = div("st-nodes");
      rows2.append(nodeRow("形态：下影不明显（880008）",
        pat.ratio_close == null ? "—" : `下影/收盘 ${num(pat.ratio_close, 3)}%`,
        pat.state || "缺数"));
      rows2.append(nodeRow("时限：T+1/T+2 收盘站上 MA10",
        (dl.checks || []).map(c => `${c.t} ${c.stand ? "站上" : "未站"}`).join(" · ") || (dl.reason || "—"),
        dl.state || "未达成"));
      card.append(rows2);
      if (dl.reason) card.append(div("st-sub", dl.reason));

      card.append(div("st-sub", "节点状态：收盘确认 = 该节点在收盘口径下成立；盘中达成 = 盘中成立、收盘待定；" +
        "未达成 = 不成立；否决 = 形态判据否掉本次启动；作废 = T+2 收盘仍未站上 MA10。"));

      // 空头状态树
      card.append(div("st-sec", "空头启动 —— 并列（任一触发）"));
      const bn = div("st-nodes");
      for (const n of bear.nodes || []) {
        bn.append(nodeRow(n.label, n.th == null ? "—" : `${num(n.value)} / ${num(n.th)}`, n.state || "未达成"));
      }
      card.append(bn);

      // 盘中分时（脏点清洗统计）—— 独立端点：10.5MB 轨迹读不堵首屏
      try {
        const it = await ctx.api.get(`/intraday?date=${data.intraday_date || ""}`);        const st = it.stats || {};
        if (st.n_in) {
          const env = st.envelope || {};
          card.append(div("st-sec", "今日分钟轨迹（脏点清洗）"));
          const g2 = div("st-grid");
          g2.append(box("采点", String(st.n_in)), box("保留", String(st.n_kept)),
            box("丢弃", String(st.n_dropped)), box("补点", String(st.n_filled)));
          card.append(g2);
          if (env.hi_pct != null) {
            card.append(div("st-sub", `全A 当日包络 ${pct(env.lo_pct)} ~ ${pct(env.hi_pct)}；越界点 ${(env.breaches || []).length} 个（只诊断，不丢点）`));
          }
        }
      } catch (e) {
        card.append(div("st-warn", `分时轨迹不可用：${e.message || e}`));
      }

      card.append(div("st-foot", `口径：${data.caliber || "—"}\n数据源：${(data.source && data.source.amv) || "—"} / ${(data.source && data.source.bars) || "—"} / 实时 ${(data.source && data.source.live) || "—"}`));
    },
  }],
};
