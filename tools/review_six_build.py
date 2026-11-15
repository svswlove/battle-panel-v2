# -*- coding: utf-8 -*-
"""复盘六格 · 数据产物生成器（2026-11-03，任务书=夏天「把复盘六格样张做成 8810 真实页面」）。

职责
    读现有产物，算「复盘六格」所需的全部数字，落两份 JSON：
        data/panel/review_six_cells_{YYYYMMDD}.json   当日真值（历史留档）
        data/panel/review_six_cells_latest.json       最近一次生成（同内容，页面默认读它）
    本脚本只产数据，不做任何判断句 —— 每格「结论 + 做/不做/验」由夏天写入 verdicts 字段。

设计初心
    1. 页面（8810 插件 review_six）只展示、不计算：所有口径在这里一次算完并留下 source 台账，
       避免前端各自重算份额 Δ / 涨停归属，出现第二份口径。
    2. 源日期必须等于目标日：任何一份源停在上一交易日就整体报错退出（不许拿旧值顶替）。
    3. verdicts 是「人写的字段」：重跑同日时保留已填内容，不被覆盖；也可用 --verdicts 外部文件注入。

数据源与口径（全部只读，不新增外部数据源）
    · 板块池      data/panel/sector_temp_latest.json                level=L2 的 90 个板块（与 sector_temp 一致）
    · 份额 Δ      data/amv_board.db::sector_daily                   share / d1 / d5；d1=今日−昨日、d5=今日−5 交易日前（pp）
    · 涨停池      data/panel/sector_ladder_{date}.json              当日涨停家数（limitup_total）+ 前排板块明细
                  夏天-activity/工具/xiaoshi_cache/hithink_{date}_limitup.json
                                                                  全市场逐股涨停池（ladder 的原始源，131 家全名单）
    · L2 归属     data/ths_board.db::board(sw_level=2)+board_member 一股只归一个 L2（实测 0 只多属）
    · AMV 温度    data/panel/amv_regime_latest.json                 amv / ma10 / state / 切换点（触发类型、第几天）
    · 仓位闸门    data/panel/position_cap_latest.json               final_pct / cap_amount / total_asset
    · 多头家数    data/panel/amv_breadth_latest.json                market.ratio（+前一日差值）
    · 涨停跌停    data/track_pool.db::sentiment                      zt_total/dt_total（附 zt_pure/dt_pure）
    · 两市成交额  data/ths_index.db::index_daily                    000001.SH + 399001.SZ 的 turnover（元）
    · 昨涨停溢价  data/track_pool.db::sentiment.premium             当前为空 → 输出 null（不编）

上下游
    上游：盘后链产物（本脚本不触发任何生产工序，只读）。
    下游：app/core/review_six.py（读取器）→ app/plugins/review_six（8810 卡片）。

用法
    python tools/review_six_build.py                        # 目标日 = sector_temp_latest.date
    python tools/review_six_build.py --date 2026-11-02      # 显式指定交易日（YYYY-MM-DD 或 YYYYMMDD）
    python tools/review_six_build.py --verdicts path.json   # 注入人工判断（每格 conclusion/do/no/check）
    python tools/review_six_build.py --dry-run              # 只算不落盘
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent          # panel-v2
WS = ROOT.parent.parent                                 # OH-WorkSpace
PANEL_DIR = WS / "data" / "panel"
DB_DIR = WS / "data"
POOL_DIR = WS / "夏天-activity" / "工具" / "xiaoshi_cache"
THS_DB = DB_DIR / "ths_board.db"
AMV_DB = DB_DIR / "amv_board.db"
TRACK_DB = DB_DIR / "track_pool.db"
INDEX_DB = DB_DIR / "ths_index.db"

SCHEMA_VERSION = "1.0.0"

# 六格判据（第一版阈值 = 初值，跑出来再校；改这里要同步 docs/review_six_contract.md）
TOP_N = 5
CRITERIA = {
    "persistent_inflow": "d1>0 且 d5>0 且 state=多；按 d5 降序前 5",
    "cold_divergence": "d5>0 且 d1>0 且 pct<1% 且 当日涨停家数=0，排除已进持续吸金；按 d5 降序前 5",
    "strong_resonance": "d1>0 且 pct>0 且 涨停家数>0；state=多 优先，再按 d1 降序前 5",
    "resonance_outflow": "d1<0 且 pct<0；按 d1 升序（最负在前）前 5",
    "leverage_divergence": "pct≥1% 且 d1<0 且 |d1|≥0.02pp；按 pct 降序前 5；无合格标的显示「今日无」",
}
PCT_COLD_MAX = 1.0      # 冷门背离的「价没动」上限（%）
PCT_LEV_MIN = 1.0       # 杠杆背离下限：pct ≥ 1%（低于则属噪音，不进格）
D1_LEV_MIN = 0.02       # 杠杆背离下限：|d1| ≥ 0.02pp
HHI_MA_N = 5            # HHI 近 5 日均值

# verdicts 的槽位（夏天按格填；本脚本只留位、不生成判断）
VERDICT_SLOTS = ("temperature", "cell_1", "cell_2", "cell_3", "cell_4", "cell_5", "cell_6")


# ---------------------------------------------------------------- 基础 IO
def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm_date(raw: Optional[str]) -> str:
    """'2026-11-02' / '20261102' → '2026-11-02'；空 → 空。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})$", s)
    if not m:
        raise SystemExit(f"日期格式不认识：{raw!r}（要 YYYY-MM-DD 或 YYYYMMDD）")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _date8(date: str) -> str:
    return date.replace("-", "")


def _ro_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _round(v: Optional[float], n: int) -> Optional[float]:
    return None if v is None else round(float(v), n)


# ---------------------------------------------------------------- 源装载 + 日期校验
def _require(cond: bool, msg: str) -> None:
    """源日期 ≠ 目标日等硬约束：直接退出，不降级、不带病落盘。"""
    if not cond:
        raise SystemExit("[review_six] " + msg)


def load_sector_temp(date: str) -> Dict[str, Any]:
    path = PANEL_DIR / "sector_temp_latest.json"
    _require(path.is_file(), f"缺源：{path}")
    doc = _read_json(path)
    _require(doc.get("date") == date,
             f"sector_temp_latest.json 日期={doc.get('date')} ≠ 目标日 {date}（拒绝用旧值顶替）")
    l2 = [b for b in doc.get("boards") or [] if b.get("level") == "L2"]
    _require(len(l2) > 0, "sector_temp_latest.json 没有 L2 板块")
    for b in l2:
        _require(b.get("date") == date,
                 f"sector_temp 板块 {b.get('name')} 日期={b.get('date')} ≠ 目标日 {date}")
    return doc


def load_sector_daily(date: str) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """返回 ({code: row}, 全部交易日升序)。row 含 share/d1/d5/amv/name。"""
    _require(AMV_DB.is_file(), f"缺源：{AMV_DB}")
    con = _ro_connect(AMV_DB)
    try:
        dates = [r[0] for r in con.execute("select distinct date from sector_daily order by date")]
        _require(date in dates, f"sector_daily 没有 {date} 的行（源日期未到位）")
        rows = [r for r in con.execute(
            "select code,name,share,d1,d5,amv from sector_daily where date=? and level='L2'", (date,))]
        _require(len(rows) > 0, f"sector_daily 在 {date} 没有 L2 行")
        return ({r["code"]: dict(r) for r in rows}, dates)
    finally:
        con.close()


def load_ladder(date: str) -> Dict[str, Any]:
    path = PANEL_DIR / f"sector_ladder_{_date8(date)}.json"
    _require(path.is_file(), f"缺源：{path}（当日涨停梯度未生成）")
    doc = _read_json(path)
    _require(_norm_date(doc.get("trade_date")) == date,
             f"{path.name} trade_date={doc.get('trade_date')} ≠ 目标日 {date}")
    return doc


def load_limitup_pool(date: str) -> Dict[str, Any]:
    """全市场逐股涨停池（ladder 的原始源）。缺文件即报错：第 6 格没有它就不可算。"""
    path = POOL_DIR / f"hithink_{_date8(date)}_limitup.json"
    _require(path.is_file(), f"缺源：{path}（涨停池，第 6 格的分母与逐股名单）")
    doc = _read_json(path)
    data = ((doc.get("data") or {}).get("data") or {})
    items = data.get("item") or []
    total = int((data.get("pagination") or {}).get("total") or len(items))
    _require(len(items) == total, f"{path.name} 池内 {len(items)} 家 ≠ total {total}")
    _require(_date8(date) == str(doc.get("date") or ""),
             f"{path.name} date={doc.get('date')} ≠ 目标日 {date}")
    return {"path": path, "items": items, "total": total}


def load_regime(date: str) -> Dict[str, Any]:
    path = PANEL_DIR / "amv_regime_latest.json"
    _require(path.is_file(), f"缺源：{path}")
    doc = _read_json(path)
    _require(_norm_date(doc.get("date")) == date,
             f"amv_regime_latest.json date={doc.get('date')} ≠ 目标日 {date}")
    return doc


def load_position_cap(date: str) -> Dict[str, Any]:
    path = PANEL_DIR / "position_cap_latest.json"
    _require(path.is_file(), f"缺源：{path}")
    doc = _read_json(path)
    _require(_norm_date(doc.get("trade_date")) == date,
             f"position_cap_latest.json trade_date={doc.get('trade_date')} ≠ 目标日 {date}")
    return doc


def load_breadth(date: str) -> Dict[str, Any]:
    path = PANEL_DIR / "amv_breadth_latest.json"
    _require(path.is_file(), f"缺源：{path}")
    doc = _read_json(path)
    _require(_norm_date(doc.get("date")) == date,
             f"amv_breadth_latest.json date={doc.get('date')} ≠ 目标日 {date}")
    return doc


def load_sentiment(date: str) -> Dict[str, Any]:
    _require(TRACK_DB.is_file(), f"缺源：{TRACK_DB}")
    con = _ro_connect(TRACK_DB)
    try:
        row = con.execute(
            "select * from sentiment where trade_date=? and ts='close'",
            (_date8(date),)).fetchone()
    finally:
        con.close()
    _require(row is not None, f"track_pool.db::sentiment 没有 {_date8(date)} close 行")
    return dict(row)


def load_turnover(date: str) -> Dict[str, Any]:
    """两市成交额 = 沪 000001.SH + 深 399001.SZ 的 index_daily.turnover（元）。缺该日 → 返回 miss。"""
    if not INDEX_DB.is_file():
        return {"miss": f"缺源：{INDEX_DB}"}
    d8 = _date8(date)
    # date<=目标日的最近两条（当日 + 上一交易日）
    con = _ro_connect(INDEX_DB)
    try:
        per_code: Dict[str, List[sqlite3.Row]] = {}
        for code in ("000001.SH", "399001.SZ"):
            per_code[code] = list(con.execute(
                "select date,turnover from index_daily where code=? and date<=? "
                "order by date desc limit 2", (code, d8)))
    finally:
        con.close()
    for code, lst in per_code.items():
        if not lst or lst[0]["date"] != d8:
            return {"miss": f"ths_index.db 缺 {code} 的 {d8} 成交额"}
        if lst[0]["turnover"] is None:
            return {"miss": f"ths_index.db {code} {d8} 成交额为空"}
    total = float(per_code["000001.SH"][0]["turnover"]) + float(per_code["399001.SZ"][0]["turnover"])
    prev = None
    if per_code["000001.SH"][1]["date"] == per_code["399001.SZ"][1]["date"]:
        prev = float(per_code["000001.SH"][1]["turnover"]) + float(per_code["399001.SZ"][1]["turnover"])
    return {"total": total, "prev": prev, "prev_date": (per_code["000001.SH"][1]["date"]
                                                       if len(per_code["000001.SH"]) > 1 else None)}


def load_l2_map() -> Dict[str, str]:
    """股票 → L2 板块码（互斥）。实测 board_member 中一股最多落一个 sw_level=2 板块。"""
    _require(THS_DB.is_file(), f"缺源：{THS_DB}")
    con = _ro_connect(THS_DB)
    try:
        l2 = {r["code"]: r["name"] for r in con.execute(
            "select code,name from board where sw_level=2")}
        out: Dict[str, str] = {}
        for r in con.execute("select board_code,stock from board_member"):
            code = r["board_code"]
            if code in l2:
                stock = r["stock"]
                prev = out.get(stock)
                _require(prev is None or prev == code,
                         f"股票 {stock} 同时属于多个 L2（{prev}/{code}）：互斥归属假设被打破，停止")
                out[stock] = code
        return out
    finally:
        con.close()


# ---------------------------------------------------------------- 温度区
def build_temperature(date: str, dates: List[str], regime: Dict[str, Any],
                      cap: Dict[str, Any], breadth: Dict[str, Any],
                      senti: Dict[str, Any], turnover: Dict[str, Any]) -> Dict[str, Any]:
    amv = float(regime.get("amv") or 0.0)
    ma10 = regime.get("ma10")
    excess = None if ma10 is None else round(amv - float(ma10), 2)
    excess_pct = None if not ma10 else round((amv / float(ma10) - 1) * 100, 4)
    state = regime.get("state")
    # A（2026-11-03）：AMV 与 MA10 的位置关系必须显式输出；state=多 但 AMV 在 MA10 下方 = 单轨未确认
    unconfirmed = bool(state == "多" and excess is not None and excess < 0)
    amv_vs_ma10 = {
        "position": "below" if (excess is not None and excess < 0) else "above",
        "diff": excess,
        "pct": excess_pct,
        "unconfirmed": unconfirmed,
        "label": "单轨未确认" if unconfirmed else None,
    }

    # 第几天：切换日算第 1 天（交易日口径，来自 sector_daily 的交易日序列）
    sw = regime.get("last_switch") or {}
    sw_date = _norm_date(sw.get("date"))
    _require(sw_date in dates, f"amv_regime.last_switch.date={sw.get('date')} 不在交易日序列里")
    day_n = dates.index(date) - dates.index(sw_date) + 1
    _require(day_n >= 1, f"切换日 {sw_date} 晚于目标日 {date}")
    # 上一次切换的方向（给顶部总述判「空转多 / 多转空」）
    prev_dir = None
    for s in reversed((regime.get("recent_switches") or [])[:-1]):
        prev_dir = s.get("dir")
        break

    mk = (breadth.get("market") or {})
    series = mk.get("series") or []
    prev_ratio = None
    for p in reversed(series):
        if _norm_date(p.get("date")) < date:
            prev_ratio = p.get("ratio")
            break
    ratio = mk.get("ratio")
    delta_pp = None if (ratio is None or prev_ratio is None) else round((float(ratio) - float(prev_ratio)) * 100, 2)

    return {
        "amv": {"value": round(amv, 2), "unit": "亿"},
        "amv_pct": _round(regime.get("pct"), 2),
        "ma10": {"value": _round(ma10, 2), "unit": "亿"},
        "excess": {"value": excess, "unit": "亿", "pct": excess_pct,
                   "formula": "AMV − MA10（亿）；pct = (AMV/MA10 − 1)×100"},
        "state": regime.get("state"),
        "amv_vs_ma10": amv_vs_ma10,
        "resonance": bool(regime.get("resonance")),
        "switch": {"date": sw_date, "mark": sw.get("mark"), "trigger": sw.get("trigger"),
                   "dir": sw.get("dir"), "prev_dir": prev_dir,
                   "delay_any": sw.get("delay_any"),
                   "delay_track": sw.get("delay_track")},
        "day_n": day_n,
        "gate": {"pct": cap.get("final_pct"), "base_pct": cap.get("base_pct"),
                 "amount": _round(cap.get("cap_amount"), 2),
                 "total_asset": _round(cap.get("total_asset"), 2),
                 "factors": cap.get("factors") or [],
                 "note": cap.get("note")},
        "breadth": {"ratio_pct": None if ratio is None else round(float(ratio) * 100, 2),
                    "prev_ratio_pct": None if prev_ratio is None else round(float(prev_ratio) * 100, 2),
                    "delta_pp": delta_pp, "multi": mk.get("multi"), "valid": mk.get("valid")},
        "limitup": {"zt": senti.get("zt_total"), "dt": senti.get("dt_total"),
                    "zt_pure": senti.get("zt_pure"), "dt_pure": senti.get("dt_pure"),
                    "stage": senti.get("stage"), "stage_note": senti.get("stage_note")},
        "turnover": ({"miss": turnover.get("miss")} if turnover.get("miss") else {
            "total": turnover.get("total"), "prev": turnover.get("prev"),
            "prev_date": turnover.get("prev_date"),
            "diff": (None if turnover.get("prev") is None
                     else round(turnover["total"] - turnover["prev"], 0)),
            "unit": "元",
        }),
        "premium": ({"value": None, "note": "track_pool.db::sentiment.premium 为空，无源，留空"}
                    if senti.get("premium") is None else {"value": senti.get("premium"),
                                                          "unit": "%"}),
    }


# ---------------------------------------------------------------- 六格
def _resid_pct(board: Dict[str, Any], sd: Dict[str, Any],
               amv_pct: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """§8.6 份额迁移的价格剥离（只算数，不参与任何判据/排序）。

    · 份额变化率（%）= d1(pp) / share（share 为小数份额，两者相除即百分数）
      —— 即 Δln(share)·100 的一阶近似
    · 价格贡献（%）= Δln(ΣAMV) − pct（Δln(ΣAMV) 取 amv_regime_latest.pct）
    · 换手残差（%）= 份额变化率 + 价格贡献 = 份额变化率 − pct + Δln(ΣAMV)
    缺 pct / share / d1 / amv_pct 任一项 → (None, None)，不补零、不猜。
    与正本 2026-11-06 实测对过：光伏 +4.75 / 电力 +2.36 / 银行 −0.42 / 通信 −2.22。
    """
    pct = board.get("pct")
    d1 = sd.get("d1")
    share = sd.get("share")
    if pct is None or d1 is None or amv_pct is None or not share:
        return None, None
    share_chg = d1 * 100.0 / share          # d1 是小数份额变化 → ×100 得 pp，再除 share 得 %
    price = amv_pct - float(pct)
    return round(share_chg + price, 2), round(price, 2)


def _row(code: str, board: Dict[str, Any], sd: Dict[str, Any], limitup_n: int,
         rank: Optional[int] = None, amv_pct: Optional[float] = None) -> Dict[str, Any]:
    """一行板块口径：价(pct) 与 钱(d1/d5) 并列，不互相替代。

    2026-11-08：新增 resid_pct / price_contrib_pct（§8.6 价格剥离）——只添两列，
    不改任何判据、排序、命中数。
    """
    resid, price_contrib = _resid_pct(board, sd, amv_pct)
    return {
        "rank": rank,
        "code": code,
        "name": board.get("name"),
        "level": "L2",
        "pct": _round(board.get("pct"), 2),
        "d1": _round(sd.get("d1") * 100 if sd.get("d1") is not None else None, 3),
        "d5": _round(sd.get("d5") * 100 if sd.get("d5") is not None else None, 3),
        "state": board.get("state"),
        "limitup": limitup_n,
        "share": _round(sd.get("share"), 6),
        "amv": _round(sd.get("amv"), 0),
        "resid_pct": resid,
        "price_contrib_pct": price_contrib,
    }


# 六格卡片式版式（规格正本：OH-Works/临时文件/六格卡片式版式规格_20261103.md）
# 标题 = 编号 + 定性词 · 格子名；chips = 板块名 + 该格最关键的一个数；依据 = 数值化陈述（不写形容词）；
# 动作由夏天写，生成器只留字段（action=null / action_status=pending）。
CARD_META = {
    "persistent_inflow": {"no": "01", "title": "重点配置 · 持续吸金", "short": "持续吸金", "chip": "d5"},
    "cold_divergence": {"no": "02", "title": "潜伏观察 · 冷门流入", "short": "冷门流入", "chip": "d5"},
    "strong_resonance": {"no": "03", "title": "趋势确认 · 强共振", "short": "强共振", "chip": "limitup"},
    "resonance_outflow": {"no": "04", "title": "风险最高 · 共振流出", "short": "共振流出", "chip": "d1"},
    "leverage_divergence": {"no": "05", "title": "分歧提示 · 杠杆背离", "short": "杠杆背离", "chip": "pct"},
    "limitup_concentration": {"no": "06", "title": "情绪焦点 · 涨停集中", "short": "涨停集中", "chip": "n"},
}


def _sp(v: Optional[float], n: int) -> str:
    """带符号数字（数据层用 ASCII +/-，便于复制与检索）。"""
    return "—" if v is None else f"{v:+.{n}f}"


def _chip_metric(r: Dict[str, Any], key: str) -> Optional[str]:
    """chips 括号里的那个数：每格只带最关键的一个（规格第三节）。"""
    if key == "limitup":
        return None if r.get("limitup") is None else f"涨停 {r['limitup']} 家"
    if key == "d5":
        return None if r.get("d5") is None else f"d5 {_sp(r['d5'], 3)}"
    if key == "d1":
        return None if r.get("d1") is None else f"d1 {_sp(r['d1'], 3)}"
    if key == "pct":
        return None if r.get("pct") is None else f"{_sp(r['pct'], 2)}%"
    if key == "n":
        return None if r.get("n") is None else f"{r['n']} 家"
    return None


def _chips(cell: Dict[str, Any], key: str, limit: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in (cell.get("rows") or [])[:limit]:
        metric = _chip_metric(r, key)
        if metric is None:
            continue
        out.append({"name": r.get("name"), "metric": metric,
                    "metric_key": key, "value": r.get(key), "state": r.get("state")})
    return out


def _basis(cell: Dict[str, Any], key: str, streak: Optional[Dict[str, int]]) -> str:
    """依据一行：只写真实数值与计数，不写形容词、不下结论。"""
    rows = cell.get("rows") or []
    n = cell.get("n_matched") or 0
    if not rows:
        if key == "leverage_divergence":
            return "今日无（pct≥1% 且 d1<0 且 |d1|≥0.02pp 无合格标的）"
        return f"无命中（判据：{cell.get('criteria', '')}）"
    t = rows[0]
    if key == "persistent_inflow":
        s = (streak or {}).get(t.get("code"))
        tail = f"、连续 {s} 天 d1/d5 双正" if s else ""
        return (f"{t.get('name')} d1 {_sp(t.get('d1'), 3)}、d5 {_sp(t.get('d5'), 3)}{tail}，"
                f"state=多；命中 {n} 个板块")
    if key == "cold_divergence":
        return (f"{t.get('name')} d5 {_sp(t.get('d5'), 3)}、pct {_sp(t.get('pct'), 2)}%"
                f"（<1%）、当日涨停 {t.get('limitup')} 家；命中 {n} 个")
    if key == "strong_resonance":
        note = "，结构未转多" if t.get("state") != "多" else ""
        return (f"{t.get('name')} pct {_sp(t.get('pct'), 2)}%、d1 {_sp(t.get('d1'), 3)}、"
                f"涨停 {t.get('limitup')} 家（三项同时成立）{note}；命中 {n} 个")
    if key == "resonance_outflow":
        return (f"{t.get('name')} d1 {_sp(t.get('d1'), 3)}、pct {_sp(t.get('pct'), 2)}%"
                f"（同负，按 d1 最负在前）；命中 {n} 个")
    if key == "leverage_divergence":
        return (f"{t.get('name')} pct {_sp(t.get('pct'), 2)}%、d1 {_sp(t.get('d1'), 3)}"
                f"（价涨钱走）；命中 {n} 个")
    return ""


def build_streak_map(date: str, dates: List[str], sd_map: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """每个 L2 截至当日、d1>0 且 d5>0 的连续交易日数（最多回溯 30 天）。"""
    out: Dict[str, int] = {}
    idx = dates.index(date)
    window = dates[max(0, idx - 29): idx + 1]
    con = _ro_connect(AMV_DB)
    try:
        for code in sd_map:
            n = 0
            for d in reversed(window):
                row = con.execute(
                    "select d1,d5 from sector_daily where date=? and level='L2' and code=?",
                    (d, code)).fetchone()
                if row is None or row["d1"] is None or row["d5"] is None:
                    break
                if row["d1"] > 0 and row["d5"] > 0:
                    n += 1
                else:
                    break
            out[code] = n
    finally:
        con.close()
    return out


def build_headline(date: str, temperature: Dict[str, Any], regime: Dict[str, Any]) -> str:
    """顶部总述：状态机输出 + 口径数字（不写行情判断、不写形容词）。"""
    t = temperature or {}
    gate = t.get("gate") or {}
    br = t.get("breadth") or {}
    lu = t.get("limitup") or {}
    sw = t.get("switch") or {}
    state = t.get("state") or "—"
    flip = ""
    if sw.get("date") == date:
        prev = sw.get("prev_dir")
        if not prev:
            for s in reversed((regime.get("recent_switches") or [])[:-1]):
                prev = s.get("dir")
                break
        if prev and sw.get("dir") and prev != sw.get("dir"):
            flip = f"（{prev}转{sw.get('dir')}）"
    # A：headline 不许只写「AMV 多」——必须带 AMV 与 MA10 的位置关系
    amv = (t.get("amv") or {}).get("value")
    ex = t.get("excess") or {}
    if amv is not None and ex.get("value") is not None:
        pos = "高于" if ex["value"] >= 0 else "低于"
        head = (f"今日 AMV {int(amv):,} 亿，{pos} MA10 {abs(round(ex['value'])):,} 亿"
                f"（{_sp(ex.get('pct'), 2)}%）")
    else:
        head = f"今日 AMV {amv if amv is not None else '—'} 亿"
    state_txt = f"state={state}"
    if (t.get("amv_vs_ma10") or {}).get("unconfirmed"):
        state_txt += "·单轨未确认"
    parts = [head + f"，{state_txt}{flip}"]
    if gate.get("pct") is not None:
        parts.append(f"闸门 {gate['pct']}%")
    inner = []
    if br.get("ratio_pct") is not None:
        inner.append(f"多头家数 {br['ratio_pct']}%")
    if lu.get("zt") is not None:
        inner.append(f"涨停 {int(lu['zt'])} 家")
    return "、".join(parts) + ("（" + "、".join(inner) + "）" if inner else "")


def build_cells(boards_l2: List[Dict[str, Any]], sd_map: Dict[str, Dict[str, Any]],
                limitup_by_l2: Counter, pool_items: List[Dict[str, Any]],
                pool_total: int, l2_names: Dict[str, str],
                streak_map: Optional[Dict[str, int]] = None,
                amv_pct: Optional[float] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    board_by_code = {b.get("code"): b for b in boards_l2}
    for b in boards_l2:
        code = b.get("code")
        sd = sd_map.get(code)
        _require(sd is not None, f"sector_daily 缺 L2 板块 {code}（{b.get('name')}）的份额行")
        rows.append(_row(code, b, sd, int(limitup_by_l2.get(code, 0)), amv_pct=amv_pct))

    def _ok(v: Any) -> bool:
        return v is not None and isinstance(v, (int, float))

    def pick(pred, sort_key, reverse: bool, key: str, title: str, criteria: str) -> Dict[str, Any]:
        matched = [r for r in rows if pred(r)]
        matched.sort(key=sort_key, reverse=reverse)
        top = []
        for i, r in enumerate(matched[:TOP_N], 1):
            rr = dict(r)
            rr["rank"] = i
            top.append(rr)
        return {"key": key, "title": title, "criteria": criteria,
                "n_matched": len(matched), "rows": top,
                "matched": matched}
    c1 = pick(lambda r: _ok(r["d1"]) and _ok(r["d5"]) and r["d1"] > 0 and r["d5"] > 0 and r["state"] == "多",
              lambda r: r["d5"], True, "persistent_inflow", "持续吸金", CRITERIA["persistent_inflow"])
    # D：冷门背离排除已进持续吸金的板块（同一板块不许两格都挂）
    c1_codes = {r["code"] for r in c1["matched"]}
    c2 = pick(lambda r: _ok(r["d1"]) and _ok(r["d5"]) and _ok(r["pct"]) and r["d5"] > 0 and r["d1"] > 0
              and r["pct"] < PCT_COLD_MAX and r["limitup"] == 0 and r["code"] not in c1_codes,
              lambda r: r["d5"], True, "cold_divergence", "冷门背离", CRITERIA["cold_divergence"])
    # B：强共振 state=多 在前，state=空 排后面（不允许空头板块占第一）
    c3 = pick(lambda r: _ok(r["d1"]) and _ok(r["pct"]) and r["d1"] > 0 and r["pct"] > 0 and r["limitup"] > 0,
              lambda r: (0 if r["state"] == "多" else 1, -(r["d1"] or 0)),
              False, "strong_resonance", "强共振", CRITERIA["strong_resonance"])
    for r in c3["rows"]:
        r["struct_note"] = None if r.get("state") == "多" else "结构未转多"
    c4 = pick(lambda r: _ok(r["d1"]) and _ok(r["pct"]) and r["d1"] < 0 and r["pct"] < 0,
              lambda r: r["d1"], False, "resonance_outflow", "共振流出", CRITERIA["resonance_outflow"])
    # C：杠杆背离加下限（pct<1% 或 |d1|<0.02pp 属噪音，不进格）
    c5 = pick(lambda r: _ok(r["d1"]) and _ok(r["pct"]) and r["pct"] >= PCT_LEV_MIN
              and r["d1"] < 0 and abs(r["d1"]) >= D1_LEV_MIN,
              lambda r: r["pct"], True, "leverage_divergence", "杠杆背离", CRITERIA["leverage_divergence"])

    # ---- 第 6 格：涨停集中（HHI）
    cnt_dist = Counter(int(i.get("continue_day_cnt") or 1) for i in pool_items)
    first_board = cnt_dist.get(1, 0)
    lb2plus = sum(v for k, v in cnt_dist.items() if k >= 2)
    max_height = max(cnt_dist) if cnt_dist else None
    hhi = sum((n / pool_total) ** 2 for n in limitup_by_l2.values()) if pool_total else None
    attributed = sum(limitup_by_l2.values())
    top3 = [{"code": c, "name": l2_names.get(c, c), "n": n}
            for c, n in limitup_by_l2.most_common(3)]
    top3_sum = sum(x["n"] for x in top3)
    l2_rows = []
    for c, n in limitup_by_l2.most_common():
        resid, price_contrib = _resid_pct(board_by_code.get(c) or {}, sd_map.get(c) or {}, amv_pct)
        l2_rows.append({"code": c, "name": l2_names.get(c, c), "n": n,
                        "hhi_part": round((n / pool_total) ** 2, 6),
                        "resid_pct": resid, "price_contrib_pct": price_contrib})
    c6 = {
        "key": "limitup_concentration", "title": "涨停集中",
        "criteria": "HHI = Σ(n_i / 当日涨停家数)²；n_i 按 L2 互斥归属；分母写死=当日涨停家数",
        "hhi": _round(hhi, 4),
        "hhi_formula": "Σ(该 L2 涨停家数 / 当日涨停家数)²",
        "total_limitup": pool_total,
        "attributed": attributed,
        "unmapped": pool_total - attributed,
        "coverage": f"{attributed}/{pool_total}",
        "top3": top3,
        "top3_sum": top3_sum,
        "top3_share": _round(top3_sum / pool_total, 4) if pool_total else None,
        "first_board_ratio": _round(first_board / pool_total, 4) if pool_total else None,
        "first_board_n": first_board,
        "lb2plus_n": lb2plus,
        "max_height": max_height,
        "cnt_dist": {str(k): v for k, v in sorted(cnt_dist.items())},
        "rows": l2_rows,
    }
    cells = [c1, c2, c3, c4, c5, c6]
    # 卡片式版式字段（规格 v2）：标题 / chips / 依据 / 动作留位
    for cell in cells:
        meta = CARD_META.get(cell["key"], {})
        cell["no"] = meta.get("no")
        cell["title"] = meta.get("title") or cell.get("title")
        cell["short"] = meta.get("short") or cell.get("title")
        cell["chips"] = _chips(cell, meta.get("chip") or "d5")
        if cell["key"] == "limitup_concentration":
            top3_sum = sum(x.get("n") or 0 for x in (cell.get("top3") or []))
            share3 = (top3_sum / pool_total * 100) if pool_total else None
            cell["basis"] = (f"涨停 {pool_total} 家，前三 {top3_sum} 家"
                             f"（{share3:.1f}%），HHI {cell.get('hhi')}"
                             f"（近 5 日均值 {cell.get('hhi_ma5')}）" if share3 is not None else
                             f"涨停 {pool_total} 家，HHI {cell.get('hhi')}")
        else:
            cell["basis"] = _basis(cell, cell["key"], streak_map)
        cell["action"] = None
        cell["action_status"] = "pending"
        cell["empty_note"] = "今日无" if cell["key"] == "leverage_divergence" else "无命中"
    return cells


def relayout_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """给旧版产物补「卡片式版式」字段（no/title/chips/basis/action）。**不重算任何指标**。

    为什么需要：11-02 产物生成于版式 v2 之前，而源产物已被 11-03 覆盖（源日期校验禁止重生成）。
    只补展示字段（全部从已落盘的数值字段取），保证旧日期也能按同一套卡片版式渲染。
    """
    cells = doc.get("cells") or []
    for cell in cells:
        key = cell.get("key")
        meta = CARD_META.get(key, {})
        cell["no"] = meta.get("no") or cell.get("no")
        cell["title"] = meta.get("title") or cell.get("title")
        cell["short"] = meta.get("short") or cell.get("short") or cell.get("title")
        if not cell.get("chips"):
            cell["chips"] = _chips(cell, meta.get("chip") or "d5")
        if not cell.get("basis"):
            if key == "limitup_concentration":
                total = cell.get("total_limitup") or 0
                top3 = cell.get("top3") or []
                s = sum(int(x.get("n") or 0) for x in top3)
                cell["top3_sum"] = s
                cell["top3_share"] = round(s / total, 4) if total else None
                cell["basis"] = (f"涨停 {total} 家，前三 {s} 家"
                                 + (f"（{s / total * 100:.1f}%）" if total else "")
                                 + f"，HHI {cell.get('hhi')}（近 5 日均值 {cell.get('hhi_ma5')}，"
                                 f"窗口 {cell.get('hhi_ma5_n')} 天）")
            else:
                cell["basis"] = _basis(cell, key, None)
        if "action" not in cell:
            cell["action"] = None
        cell["action_status"] = "written" if cell.get("action") else "pending"
    # headline 是自动字段：relayout 一律重算（旧版产物当时没有 prev_dir）
    temp = doc.get("temperature") or {}
    sw = temp.get("switch") or {}
    if sw.get("date"):
        if not sw.get("prev_dir"):
            try:
                reg = _read_json(PANEL_DIR / "amv_regime_latest.json")
                sws = reg.get("recent_switches") or []
                for i, s in enumerate(sws):
                    if _norm_date(s.get("date")) == sw.get("date") and i > 0:
                        sw["prev_dir"] = sws[i - 1].get("dir")
                        break
            except (OSError, ValueError):
                pass
        doc["headline"] = build_headline(doc.get("meta", {}).get("target_date") or "",
                                         temp, {})
    return doc


def build_hhi_series(date: str, dates: List[str], l2_map: Dict[str, str],
                     l2_names: Dict[str, str]) -> Dict[str, Any]:
    """近 5 个交易日（含目标日）的 HHI。缺涨停池的日期跳过并记录，不补值。"""
    idx = dates.index(date)
    window = dates[max(0, idx - HHI_MA_N + 1): idx + 1]
    out: List[Dict[str, Any]] = []
    missing: List[str] = []
    for d in window:
        path = POOL_DIR / f"hithink_{_date8(d)}_limitup.json"
        if not path.is_file():
            missing.append(d)
            continue
        try:
            doc = _read_json(path)
            data = ((doc.get("data") or {}).get("data") or {})
            items = data.get("item") or []
            total = int((data.get("pagination") or {}).get("total") or len(items))
        except (OSError, ValueError):
            missing.append(d)
            continue
        if not items or not total:
            missing.append(d)
            continue
        cnt = Counter(l2_map.get(i.get("ticker")) for i in items if l2_map.get(i.get("ticker")))
        hhi = sum((n / total) ** 2 for n in cnt.values())
        out.append({"date": d, "hhi": round(hhi, 4), "total": total,
                    "attributed": sum(cnt.values())})
    vals = [x["hhi"] for x in out]
    return {"ma5": round(sum(vals) / len(vals), 4) if vals else None,
            "n": len(vals), "series": out, "missing_dates": missing,
            "note": "近 %d 个交易日（含目标日）HHI 均值；缺池日期跳过，不补值" % HHI_MA_N}


# ---------------------------------------------------------------- 落盘
def _empty_verdicts() -> Dict[str, Any]:
    return {k: {"conclusion": None, "do": None, "no": None, "check": None} for k in VERDICT_SLOTS}


def _merge_verdicts(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """只覆盖显式给出的非空字段；结构外的键忽略。"""
    for slot in VERDICT_SLOTS:
        src = (extra or {}).get(slot)
        if not isinstance(src, dict):
            continue
        dst = base.setdefault(slot, {})
        for key in ("conclusion", "do", "no", "check"):
            if src.get(key) not in (None, ""):
                dst[key] = src[key]
    return base


def write_outputs(out: Dict[str, Any], date: str, out_dir: Path, dry_run: bool) -> Tuple[Path, Path]:
    dated = out_dir / f"review_six_cells_{_date8(date)}.json"
    latest = out_dir / "review_six_cells_latest.json"
    if dry_run:
        return dated, latest
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, ensure_ascii=False, indent=1)
    for target in (dated, latest):
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return dated, latest


def main(argv=None) -> int:
    global PANEL_DIR  # noqa: PLW0603 - 测试/换机重定向用；默认即生产路径
    ap = argparse.ArgumentParser(description="复盘六格数据产物生成器")
    ap.add_argument("--date", default=None, help="目标交易日 YYYY-MM-DD / YYYYMMDD（默认取 sector_temp 的 date）")
    ap.add_argument("--panel-dir", default=str(PANEL_DIR))
    ap.add_argument("--out-dir", default=str(PANEL_DIR))
    ap.add_argument("--verdicts", default=None, help="外部 verdicts JSON（只覆盖非空字段）")
    ap.add_argument("--actions", default=None, help="外部动作 JSON（{格key: 动作}，只覆盖非空）")
    ap.add_argument("--relayout", default=None,
                    help="只给指定日期的已落盘产物补卡片版式字段（不重算指标，不读源）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.panel_dir != str(PANEL_DIR):
        PANEL_DIR = Path(args.panel_dir)
    out_dir = Path(args.out_dir)

    # --relayout：只补版式字段（旧产物兼容），不重新计算任何口径
    if args.relayout:
        rd = _norm_date(args.relayout)
        path = out_dir / f"review_six_cells_{_date8(rd)}.json"
        _require(path.is_file(), f"缺产物：{path}")
        doc = relayout_doc(_read_json(path))
        if not args.dry_run:
            payload = json.dumps(doc, ensure_ascii=False, indent=1)
            # 只回写当日文件；_latest 必须保持最新交易日，不能被旧日期覆盖
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload + "\n", encoding="utf-8")
            os.replace(tmp, path)
        print(f"[review_six] relayout {rd} → {path}")
        return 0

    if args.date:
        date = _norm_date(args.date)
    else:
        p = PANEL_DIR / "sector_temp_latest.json"
        _require(p.is_file(), f"缺源：{p}")
        date = _norm_date(_read_json(p).get("date"))
    _require(bool(date), "无法确定目标日")
    st = load_sector_temp(date)

    sd_map, dates = load_sector_daily(date)
    ladder = load_ladder(date)
    pool = load_limitup_pool(date)
    regime = load_regime(date)
    cap = load_position_cap(date)
    breadth = load_breadth(date)
    senti = load_sentiment(date)
    turnover = load_turnover(date)
    l2_map = load_l2_map()
    boards_l2 = [b for b in st["boards"] if b.get("level") == "L2"]
    l2_names = {b["code"]: b["name"] for b in boards_l2}

    # 份额 Δ 自校验：sector_daily.d1/d5 必须与 share 差一致（防旧值/口径漂移）
    prev_d = dates[dates.index(date) - 1] if dates.index(date) >= 1 else None
    d5_d = dates[dates.index(date) - 5] if dates.index(date) >= 5 else None
    con = _ro_connect(AMV_DB)
    try:
        err_d1 = err_d5 = 0.0
        for code, row in sd_map.items():
            if prev_d:
                prev = con.execute("select share from sector_daily where date=? and level='L2' and code=?",
                                   (prev_d, code)).fetchone()
                if prev is not None and row["d1"] is not None:
                    err_d1 = max(err_d1, abs((row["share"] - prev[0]) - row["d1"]))
            if d5_d:
                prev5 = con.execute("select share from sector_daily where date=? and level='L2' and code=?",
                                    (d5_d, code)).fetchone()
                if prev5 is not None and row["d5"] is not None:
                    err_d5 = max(err_d5, abs((row["share"] - prev5[0]) - row["d5"]))
    finally:
        con.close()
    _require(err_d1 < 1e-9 and err_d5 < 1e-9,
             f"sector_daily 的 d1/d5 与 share 差重算不一致（d1 err={err_d1:.3e}, d5 err={err_d5:.3e}）")

    # 涨停股 → L2 归属
    streak_map = build_streak_map(date, dates, sd_map)
    limitup_by_l2: Counter = Counter()
    unmapped: List[str] = []
    for it in pool["items"]:
        code = l2_map.get(it.get("ticker"))
        if code:
            limitup_by_l2[code] += 1
        else:
            unmapped.append(it.get("ticker"))
    # 分母写死 = 当日涨停家数；ladder 的 total 与池必须一致（两源交叉校验）
    _require(int(ladder.get("limitup_total") or 0) == pool["total"],
             f"ladder limitup_total={ladder.get('limitup_total')} ≠ 涨停池 total={pool['total']}")

    cells = build_cells(boards_l2, sd_map, limitup_by_l2, pool["items"], pool["total"], l2_names,
                        streak_map=streak_map, amv_pct=regime.get("pct"))
    hhi_series = build_hhi_series(date, dates, l2_map, l2_names)
    cells[5]["hhi_ma5"] = hhi_series["ma5"]
    cells[5]["hhi_ma5_series"] = hhi_series["series"]
    cells[5]["hhi_ma5_n"] = hhi_series["n"]
    cells[5]["hhi_ma5_missing"] = hhi_series["missing_dates"]
    # 06 的依据依赖 hhi_ma5（build_cells 时还没算出来）→ 这里重算一次
    if pool["total"]:
        top3_sum = sum(int(x.get("n") or 0) for x in (cells[5].get("top3") or []))
        cells[5]["basis"] = (f"涨停 {pool['total']} 家，前三 {top3_sum} 家"
                             f"（{top3_sum / pool['total'] * 100:.1f}%），HHI {cells[5].get('hhi')}"
                             f"（近 5 日均值 {cells[5].get('hhi_ma5')}，窗口 {cells[5].get('hhi_ma5_n')} 天）")
    cells[5]["hhi_ma5_note"] = hhi_series["note"]
    cells[5]["unmapped_tickers"] = unmapped

    # 校验：sentiment 的 2板+/高度 与涨停池逐股统计一致（不一致只警告，不拦）
    warnings: List[str] = []
    if senti.get("lb2plus") is not None and int(senti["lb2plus"]) != cells[5]["lb2plus_n"]:
        warnings.append(f"sentiment.lb2plus={senti['lb2plus']} ≠ 涨停池统计 {cells[5]['lb2plus_n']}")
    if senti.get("height") is not None and int(senti["height"]) != (cells[5]["max_height"] or 0):
        warnings.append(f"sentiment.height={senti['height']} ≠ 涨停池统计 {cells[5]['max_height']}")
    if unmapped:
        warnings.append(f"涨停股 {len(unmapped)} 家未归入任何 L2：{unmapped[:10]}（HHI 分子不含）")

    temperature = build_temperature(date, dates, regime, cap, breadth, senti, turnover)
    if turnover.get("miss"):
        warnings.append(turnover["miss"])
    if senti.get("premium") is None:
        warnings.append("昨涨停今日溢价：sentiment.premium 为空，无源留空")

    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "review_six_cells",
        "meta": {
            "target_date": date,
            "trade_date": date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "builder": "tools/review_six_build.py",
            "sources": {
                "sector_temp": {"path": "data/panel/sector_temp_latest.json", "date": st.get("date")},
                "sector_daily": {"path": "data/amv_board.db::sector_daily", "date": date,
                                 "prev_date": prev_d, "d5_date": d5_d},
                "sector_ladder": {"path": f"data/panel/sector_ladder_{_date8(date)}.json",
                                  "date": _norm_date(ladder.get("trade_date"))},
                "limitup_pool": {"path": str(pool["path"].relative_to(WS)).replace("\\", "/"),
                                 "date": _norm_date(str(pool["path"].stem.split("_")[1])),
                                 "total": pool["total"]},
                "amv_regime": {"path": "data/panel/amv_regime_latest.json", "date": _norm_date(regime.get("date"))},
                "position_cap": {"path": "data/panel/position_cap_latest.json", "date": _norm_date(cap.get("trade_date"))},
                "amv_breadth": {"path": "data/panel/amv_breadth_latest.json", "date": _norm_date(breadth.get("date"))},
                "sentiment": {"path": "data/track_pool.db::sentiment", "date": senti.get("trade_date")},
                "turnover": {"path": "data/ths_index.db::index_daily", "date": _date8(date)},
                "l2_attribution": {"path": "data/ths_board.db::board+board_member",
                                   "caliber": "sw_level=2，一股只归一个 L2（实测 0 只多属）"},
            },
            "caliber": {
                "share": "份额 = 板块AMV / 大盘AMV；L2 为互斥完备划分（Σshare=1）",
                "d1": "今日份额 − 上一交易日份额（pp，×100）",
                "d5": "今日份额 − 5 个交易日前份额（pp，×100）",
                "pct": "THS 板块指数当日涨跌幅（%），价维度，源 board_flow.pct",
                "price_money": "pct（价）与 d1/d5（钱）是两个维度，禁止互替",
                "resid": "resid_pct = 份额变化率 + 价格贡献（§8.6）：份额变化率=d1/share；"
                         "价格贡献=Δln(ΣAMV)−pct（Δln(ΣAMV)=amv_regime_latest.pct）；"
                         "一阶近似、未回测标定，只用于同日横截面排序，仍不是「钱」",
                "limitup_attribution": "涨停股按 ths_board L2 互斥归属，一股只计一次",
                "hhi": "Σ(n_i / 当日涨停家数)²；分母写死 = 当日涨停家数",
                "hhi_ma5": hhi_series["note"],
                "turnover": "两市成交额 = 000001.SH + 399001.SZ 指数日线 turnover（元）",
            },
            "checks": {
                "d1_recalc_max_err": err_d1,
                "d5_recalc_max_err": err_d5,
                "limitup_coverage": f"{sum(limitup_by_l2.values())}/{pool['total']}",
                "ladder_pool_total_match": True,
            },
            "thresholds": {"top_n": TOP_N, "pct_cold_max": PCT_COLD_MAX,
                           "hhi_ma_n": HHI_MA_N, "version": "初值 v1（跑出来再校）"},
            "warnings": warnings,
        },
        "temperature": temperature,
        "headline": build_headline(date, temperature, regime),
        "cells": cells,
        "verdicts": _empty_verdicts(),
    }

    # verdicts：保留同日已填内容 + 叠加外部文件（人写字段，重跑不丢）
    dated_path = out_dir / f"review_six_cells_{_date8(date)}.json"
    if dated_path.is_file():
        try:
            old = _read_json(dated_path)
            if isinstance(old.get("verdicts"), dict):
                _merge_verdicts(out["verdicts"], old["verdicts"])
            # 动作是人写的字段：同日重跑保留，按 key 对齐（不按位置）
            old_actions = {c.get("key"): c.get("action") for c in (old.get("cells") or [])
                           if isinstance(c, dict)}
            for c in out["cells"]:
                if old_actions.get(c.get("key")):
                    c["action"] = old_actions[c["key"]]
                    c["action_status"] = "written"
        except (OSError, ValueError):
            pass
    if args.actions:
        acts = _read_json(Path(args.actions))
        for c in out["cells"]:
            v = (acts or {}).get(c.get("key"))
            if v not in (None, ""):
                c["action"] = v
                c["action_status"] = "written"
    if args.verdicts:
        _merge_verdicts(out["verdicts"], _read_json(Path(args.verdicts)))

    dated, latest = write_outputs(out, date, out_dir, args.dry_run)

    print(f"[review_six] {date}  格1={len(cells[0]['matched'])} 格2={len(cells[1]['matched'])} "
          f"格3={len(cells[2]['matched'])} 格4={len(cells[3]['matched'])} 格5={len(cells[4]['matched'])} "
          f"HHI={cells[5]['hhi']} (ma5={cells[5]['hhi_ma5']})")
    for w in warnings:
        print("  ⚠", w)
    print(("  [dry-run] 未落盘: " if args.dry_run else "  已落盘: ") + f"{dated}\n{' ' * 10}{latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
