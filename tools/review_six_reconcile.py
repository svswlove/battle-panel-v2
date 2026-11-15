# -*- coding: utf-8 -*-
"""复盘六格 · 对账表生成器（读已落盘产物，输出 markdown）。

职责
    把 `data/panel/review_six_cells_{YYYYMMDD}.json` 摊成一张可核对的表：
    源台账 → 温度区（每个数字带来源字段）→ 六格前 5 → 校验项 → 口径注意。
    只读产物，不重算口径（重算就会出第二份真相）。

用法
    python tools/review_six_reconcile.py --date 2026-11-02
    python tools/review_six_reconcile.py --date 2026-11-02 --out docs/review_six_reconcile_20261102.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT.parent.parent
PANEL_DIR = WS / "data" / "panel"


def _fmt(v: Any, n: int = 2, dash: str = "—") -> str:
    if v is None:
        return dash
    if isinstance(v, float):
        return f"{v:+.{n}f}" if v != 0 else f"{v:.{n}f}"
    return str(v)


def _num(v: Any, n: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{n}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _row_table(rows: List[Dict[str, Any]]) -> List[str]:
    out = ["| # | 板块 | pct % | d1 pp | d5 pp | state | 涨停 | 份额 % |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| {rank} | {name} | {pct} | {d1} | {d5} | {st} | {lu} | {sh} |".format(
            rank=r.get("rank", ""), name=r.get("name", ""),
            pct=_fmt(r.get("pct"), 2), d1=_fmt(r.get("d1"), 3), d5=_fmt(r.get("d5"), 3),
            st=r.get("state") or "—", lu=r.get("limitup", "—"),
            sh=_fmt(None if r.get("share") is None else r["share"] * 100, 2),
        ))
    return out


SOURCE_USE = {
    "sector_temp": "L2 板块池 / pct（价） / state",
    "sector_daily": "share / d1 / d5（钱）",
    "sector_ladder": "当日涨停家数（与涨停池交叉校验）",
    "limitup_pool": "全市场逐股涨停（第 6 格分子，L2 互斥归属）",
    "amv_regime": "AMV / MA10 / state / 触发类型 / 第几天",
    "position_cap": "闸门 % / 金额 / 总资产",
    "amv_breadth": "多头家数占比",
    "sentiment": "涨停跌停 / 首板+2板+ / 昨涨停溢价",
    "turnover": "两市成交额",
    "l2_attribution": "涨停股 → L2 互斥归属",
}


def build(doc: Dict[str, Any]) -> str:
    meta = doc.get("meta") or {}
    date = meta.get("target_date")
    t = doc.get("temperature") or {}
    cells = doc.get("cells") or []
    lines: List[str] = []
    lines.append(f"# 复盘六格对账表 · {date}")
    lines.append("")
    lines.append(f"> 产物：`data/panel/review_six_cells_{str(date).replace('-', '')}.json` ｜ "
                 f"生成器：`{meta.get('builder', '')}` ｜ 生成时间：{meta.get('generated_at', '')}")
    lines.append("")
    lines.append("## 1. 源台账（全部只读；任一源日期 ≠ 目标日，生成器直接报错）")
    lines.append("")
    lines.append("| 源 | 路径 | 日期 | 用途 |")
    lines.append("|---|---|---|---|")
    for key, item in (meta.get("sources") or {}).items():
        if not isinstance(item, dict):
            continue
        lines.append(f"| {key} | `{item.get('path', '')}` | {item.get('date', '—')} | {SOURCE_USE.get(key, '')} |")
    lines.append("")

    lines.append("## 2. 温度区（每个数字的来源字段）")
    lines.append("")
    amv = t.get("amv") or {}
    ma10 = t.get("ma10") or {}
    ex = t.get("excess") or {}
    sw = t.get("switch") or {}
    gate = t.get("gate") or {}
    br = t.get("breadth") or {}
    lu = t.get("limitup") or {}
    to = t.get("turnover") or {}
    pm = t.get("premium") or {}
    lines.append("| 指标 | 值 | 来源字段 |")
    lines.append("|---|---|---|")
    lines.append(f"| AMV（亿） | {_num(amv.get('value'), 2)} | amv_regime_latest.json::amv |")
    lines.append(f"| AMV 环比 % | {_fmt(t.get('amv_pct'), 2)} | amv_regime_latest.json::pct |")
    lines.append(f"| MA10（亿） | {_num(ma10.get('value'), 2)} | amv_regime_latest.json::ma10 |")
    lines.append(f"| 超出差额（亿） | {_fmt(ex.get('value'), 2)} | amv − ma10 |")
    lines.append(f"| 超出幅度 % | {_fmt(ex.get('pct'), 4)} | (amv/ma10 − 1)×100 |")
    vs = t.get("amv_vs_ma10") or {}
    lines.append(f"| AMV vs MA10 位置 | {'低于' if vs.get('position') == 'below' else '高于'} MA10 "
                 f"{abs(ex.get('value') or 0):,.2f} 亿 | amv_vs_ma10.position / diff |")
    lines.append(f"| 单轨确认 | {vs.get('label') or '已确认（AMV 在 MA10 上方）'} | amv_vs_ma10.unconfirmed |")
    lines.append(f"| state | {t.get('state', '—')} | amv_regime_latest.json::state |")
    lines.append(f"| 触发类型 | {sw.get('trigger', '—')} · {sw.get('mark', '—')} | amv_regime_latest.json::last_switch |")
    lines.append(f"| 第几天 | {t.get('day_n', '—')} | 切换日 {sw.get('date', '—')} → 目标日（交易日序列） |")
    lines.append(f"| 闸门 % | {gate.get('pct', '—')}% | position_cap_latest.json::final_pct |")
    lines.append(f"| 闸门金额（元） | {_num(gate.get('amount'), 2)} | position_cap_latest.json::cap_amount |")
    lines.append(f"| 总资产（元） | {_num(gate.get('total_asset'), 2)} | position_cap_latest.json::total_asset |")
    lines.append(f"| 多头家数占比 | {_fmt(br.get('ratio_pct'), 2)}% | amv_breadth_latest.json::market.ratio |")
    lines.append(f"| 较前日 pp | {_fmt(br.get('delta_pp'), 2)} | market.series 前一日 ratio 差 |")
    lines.append(f"| 涨停 / 跌停 | {_num(lu.get('zt'), 0)} / {_num(lu.get('dt'), 0)} | sentiment::zt_total / dt_total（纯口径 {_num(lu.get('zt_pure'), 0)} / {_num(lu.get('dt_pure'), 0)}） |")
    lines.append(f"| 两市成交额（元） | {_num(to.get('total'), 0)} | ths_index.db::index_daily（000001.SH + 399001.SZ） |")
    lines.append(f"| 较前日（元） | {_num(to.get('diff'), 0)} | 同上，prev_date={to.get('prev_date', '—')} |")
    lines.append(f"| 昨涨停今日溢价 | {pm.get('value', '—')} | sentiment::premium{'（空 → 留空）' if pm.get('value') is None else ''} |")
    lines.append("")

    lines.append("## 3. 六格（展示前 5；完整命中见产物 `cells[].matched`）")
    lines.append("")
    labels = ["① 持续吸金", "② 冷门背离", "③ 强共振", "④ 共振流出", "⑤ 杠杆背离"]
    for i, cell in enumerate(cells[:5]):
        lines.append(f"### {labels[i]}（命中 {cell.get('n_matched', 0)}）")
        lines.append("")
        lines.append(f"判据：{cell.get('criteria', '')}")
        lines.append("")
        lines.extend(_row_table(cell.get("rows") or []))
        lines.append("")

    if len(cells) > 5:
        c6 = cells[5]
        lines.append("### ⑥ 涨停集中（HHI）")
        lines.append("")
        lines.append(f"- HHI = **{c6.get('hhi')}**（近 5 日均值 {c6.get('hhi_ma5')}，窗口 {c6.get('hhi_ma5_n')} 天）")
        lines.append(f"- 分母 = 当日涨停 **{c6.get('total_limitup')}** 家（写死）；覆盖 {c6.get('coverage')}；"
                     f"未归属 {c6.get('unmapped')}")
        lines.append(f"- 首板 {c6.get('first_board_n')} 家（{_fmt((c6.get('first_board_ratio') or 0) * 100, 1)}%）、"
                     f"2 板+ {c6.get('lb2plus_n')} 家、最高 {c6.get('max_height')} 板")
        lines.append(f"- 连板分布：{c6.get('cnt_dist')}")
        lines.append("")
        lines.append("| L2 板块 | 涨停家数 | n/N | (n/N)² |")
        lines.append("|---|---|---|---|")
        for r in c6.get("rows") or []:
            lines.append(f"| {r.get('name')} | {r.get('n')} | "
                         f"{(r.get('n') or 0) / (c6.get('total_limitup') or 1):.4f} | {r.get('hhi_part')} |")
        lines.append("")

    checks = meta.get("checks") or {}
    lines.append("## 4. 校验")
    lines.append("")
    lines.append(f"- d1 = share 差 重算最大误差：{checks.get('d1_recalc_max_err')}")
    lines.append(f"- d5 = share 差 重算最大误差：{checks.get('d5_recalc_max_err')}")
    lines.append(f"- 涨停覆盖：{checks.get('limitup_coverage')}")
    lines.append(f"- ladder 涨停家数 == 涨停池 total：{checks.get('ladder_pool_total_match')}")
    thr = meta.get("thresholds") or {}
    lines.append(f"- 阈值版本：{thr.get('version')}（top_n={thr.get('top_n')}，pct_cold_max={thr.get('pct_cold_max')}，hhi_ma_n={thr.get('hhi_ma_n')}）")
    lines.append("")
    if meta.get("warnings"):
        lines.append("## 5. 数据提示")
        lines.append("")
        for w in meta["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    lines.append("## 注意")
    lines.append("")
    lines.append("- 温度区：AMV 在 MA10 下方时 state=多 属「单轨未确认」，headline 必带差额与幅度（变更单 A）。")
    lines.append("- 强共振：state=多 在前，state=空 排后并标「结构未转多」，不允许空头板块占第一（变更单 B）。")
    lines.append("- 杠杆背离：pct≥1% 且 |d1|≥0.02pp 才进格；全无合格标的显示「今日无」（变更单 C）。")
    lines.append("- 冷门背离：排除已进持续吸金的板块，同一板块不许两格同挂（变更单 D）。")
    lines.append("- 「涨停集中」按 L2 互斥归属（一股只计一次）；与按板块计数（含一票多属）的口径不同，"
                 "top3 家数会与样张略有差异，属口径差异不是算错。")
    lines.append("- pct（价）与 d1/d5（钱）是两个维度，任何一格都不互相替代。")
    lines.append("- verdicts（结论 + 做/不做/验）由夏天写入；本表只对数字，不生成判断句。")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="复盘六格对账表")
    ap.add_argument("--date", required=True)
    ap.add_argument("--panel-dir", default=str(PANEL_DIR))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    d8 = args.date.replace("-", "")
    src = Path(args.panel_dir) / f"review_six_cells_{d8}.json"
    if not src.is_file():
        raise SystemExit(f"缺产物：{src}")
    doc = json.loads(src.read_text(encoding="utf-8"))
    md = build(doc)
    out = Path(args.out) if args.out else (ROOT / "docs" / f"review_six_reconcile_{d8}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"已生成：{out}  ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
