# -*- coding: utf-8 -*-
"""paper 插件的**纯装配**层：账户 / 账本 / 买卖点三个视图。

【为什么单独一个文件】`backend.py` 只做 HTTP 参数校验与「调哪个 service 方法」，
真正决定「一个字段叫什么、怎么算」的代码放这里，两类东西分开：

- `backend.py` → 取数（svc.xxx()）+ 抛 HTTPException；
- `views.py`   → 纯函数：输入是**已经取好的**账本/报价/名称表，输出是对外的 JSON。

这样视图可以脱离 HTTP、脱离 service 单测（本文件不 import 任何 app.core 模块，
不读文件、不连库、不碰行情，只做算术与改名）。

【口径来源】旧 8800 `engine/server.py`：
- `_build_holdings_view`（持仓明细列规范，见 `_vendor_src/quote5_reference.py`）；
- `api_paper_account`（3821-3833）；
- `api_ledger` paper 分支（4290-4301）；
- `_conds_expiring` / `_book_extras`（判据与键名逐字，只把 `common.names` 换成传入的
  `names` 字典，`_order_service()` 换成调用方传进来的 orders/conditions 列表）；
- `build_trades_payload` 的**纸面部分**（9753-10018）。

【不在本文件】不判断交易时段、不写账本、不下单——那是 `app/core/paper/` 的事。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 常量（照旧端）

# 第二本账「流水记录」固定上限（旧端 `_book_extras` 的 HIST_MAX，防前端一次画几千行）
HIST_MAX = 50
# 活跃单最多下发 20 条（旧端同）
ACTIVE_MAX = 20

# 旧端状态中文表（逐字）
STATUS_CN: Dict[str, str] = {
    "REPORTED": "挂单", "PARTIAL": "部成", "FILLED": "已成",
    "CANCELLED": "已撤", "REJECTED": "已拒",
    "TRIGGERED": "已触发", "EXPIRED": "已过期", "FAILED": "触发失败",
}

# 单笔软上限比例（纸面总资产 × 10%；旧端 `api_paper_account` 的 single_order_cap）
SINGLE_ORDER_CAP_RATIO = 0.10

# 临期条件单阈值（旧端 `_conds_expiring`：剩 ≤3 天上仪表盘）
EXPIRING_DAYS = 3

# 买卖点 days 夹取区间（旧端 `build_trades_payload`：max(1, min(365, days))）
TRADES_DAYS_MIN, TRADES_DAYS_MAX = 1, 365
TRADES_VERSION = "v1-20260917"


# ---------------------------------------------------------------- 基础工具


def num(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """宽容转 float；非有限数（NaN/inf）或不可转 → default（不补零、不编造）。

    `default=None` 时用于「必须区分『没有值』和『值为 0』」的场合（如价格）。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) != float("inf") else default


def finite(value: Any) -> bool:
    """旧端 `_finite` 同口径：可转 float 且非 NaN/Inf。"""
    try:
        f = float(value)
        return f == f and abs(f) != float("inf")
    except (TypeError, ValueError):
        return False


def name_or_code(raw_name: Any, code: Any) -> str:
    """名称清洗（旧端 `_name_or_code`）：空/乱码（含 ? 或替换符）→ 回退代码。"""
    s = str(raw_name or "").strip()
    if (not s) or ("?" in s) or ("\ufffd" in s):
        return str(code or "")
    return s


def resolve_name(raw_name: Any, code: Any, names: Optional[Dict[str, str]]) -> str:
    """名称统一出口（旧端 `_resolve_name_or_code`）：显式名 → 名称表 → 回退代码。

    `names` 是 `stock_names`（`{6 位代码: 名称}`）。旧端还会问 `common.names`，
    本插件只能读 `ctx.json_read("stock_names")`，取不到就用代码，**不编造**。
    显式名先过 `name_or_code` 清洗：账本里的乱码（`?`/替换符）不许直接下发。
    """
    explicit = name_or_code(raw_name, "")
    if explicit:
        return explicit
    table = names or {}
    body = str(code or "").split(".")[0]
    hit = table.get(str(code or "")) or table.get(body)
    if hit:
        return str(hit)
    return str(code or "")


def resolve_name_table_first(raw_name: Any, code: Any, names: Optional[Dict[str, str]]) -> str:
    """名称解析（**名称表优先**）：旧端 `_book_extras` / `_conds_expiring` 用的是
    `names.get(code) or _name_or_code(name, code)` —— 顺序与持仓视图相反，别混用。

    账本里存的名称可能过期（改名/乱码），名称表是当日全量表，所以这两个视图先查表。
    """
    table = names or {}
    body = str(code or "").split(".")[0]
    hit = table.get(str(code or "")) or table.get(body)
    if hit:
        return str(hit)
    return name_or_code(raw_name, code)


def quote_records(raw_quotes: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """`svc.quote_map()` 的行情 → 估值视图要的形状 `{price, prev_close, source, asof}`。

    口径（任务书 §5.8）：`price = last`、`prev_close = pre_close`；
    `source` 取 quote 的 `src`（feed 标 `qmt` / `eltdx`），`asof` 取 `quote_time`。
    价格非正/缺失的票**不出现**（调用方按「拿不到价」处理，回退成本价并标 price_source）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for code, q in (raw_quotes or {}).items():
        if not isinstance(q, dict):
            continue
        price = num(q.get("last"), None)  # type: ignore[arg-type]
        if price is None or float(price) <= 0:
            continue
        prev = num(q.get("pre_close"), None)  # type: ignore[arg-type]
        out[str(code)] = {
            "price": float(price),
            "prev_close": (float(prev) if prev is not None and float(prev) > 0 else None),
            "source": str(q.get("src") or "quote"),
            "asof": q.get("quote_time"),
        }
    return out


# ---------------------------------------------------------------- 1. 持仓明细视图


def build_holdings_view(
    positions: Iterable[Tuple[str, Dict[str, Any]]],
    quotes: Optional[Dict[str, Dict[str, Any]]],
    names: Optional[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """统一持仓明细视图（旧端 `_build_holdings_view` 移植版）。

    列口径（主人 2026-09-11 定）：代码+名称｜数量｜可卖｜成本｜现价｜盈亏(额)｜盈亏%｜
    当日收益(额)｜当日盈亏比%｜市值｜仓位占比%｜建仓时间。

    数据口径：现价/当日盈亏来自 quotes（缺失 → 回退成本价并标注 `cost_fallback`，
    **不编造**）；仓位占比 = 个股市值 ÷ 持仓总市值（非总资产）。

    返回 `(rows, {"market_value": ...})`；rows 里每个字段都已定值（缺失为 None，
    前端显示 —）。
    """
    rows: List[Dict[str, Any]] = []
    mv_total = 0.0
    qmap = quotes or {}
    for code, p in positions:
        p = p if isinstance(p, dict) else {}
        qty = int(p.get("qty") or 0)
        avail = p.get("available_qty")
        avail = int(avail) if avail is not None else qty
        cost = num(p.get("cost_price"))
        q = qmap.get(code) or qmap.get(str(code).split(".")[0]) or {}
        has_live = finite(q.get("price")) and float(q.get("price")) > 0
        last = float(q["price"]) if has_live else cost
        mv = round(qty * last, 2)
        pnl_amt = round((last - cost) * qty, 2)
        pnl_pct = round((last / cost - 1) * 100, 4) if cost else 0.0
        # 当日收益：有现价且昨收可得 → (现价-昨收)×数量；否则 None（不编造）
        prev = q.get("prev_close")
        day_amt: Optional[float] = None
        day_pct: Optional[float] = None
        if has_live and finite(prev) and float(prev) > 0:
            day_amt = round((last - float(prev)) * qty, 2)
            day_pct = round((last / float(prev) - 1) * 100, 4)
        mv_total += mv
        rows.append({
            "ts_code": code,
            "name": resolve_name(p.get("name"), code, names),
            "qty": qty, "available_qty": avail,
            "cost_price": round(cost, 3), "last_price": round(last, 3),
            "price_source": (q.get("source") if has_live else "cost_fallback"),
            "quote_asof": (q.get("asof") if has_live else None),
            "pnl_amount": pnl_amt, "pnl_pct": pnl_pct,
            "day_pnl_amount": day_amt, "day_pnl_pct": day_pct,
            "market_value": mv,
            "weight_pct": None,       # 占持仓总市值比，汇总后回填
            "opened_at": (p.get("opened_at") or p.get("created_at") or p.get("open_date")),
        })
    for r in rows:                    # 回填仓位占比
        r["weight_pct"] = round(r["market_value"] / mv_total * 100, 2) if mv_total > 0 else None
    return rows, {"market_value": round(mv_total, 2)}


# ---------------------------------------------------------------- 2. /api/paper_account


def build_paper_account(
    acct: Dict[str, Any],
    positions: Iterable[Tuple[str, Dict[str, Any]]],
    quotes: Optional[Dict[str, Dict[str, Any]]],
    names: Optional[Dict[str, str]],
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """纸面独立账本视图（旧端 `api_paper_account` 逐键）。

    `out = dict(acct)`（原样带出账本顶层键）+ `account{8 键}` + `positions_list`。

    `account` 与 dashboard.json 的 account 同构（`total_asset/available_cash/market_value/
    frozen_cash/unrealized_pnl/single_order_cap/cash_reserve_pct/updated_at`），
    前端纸面视图与实盘卡片才能逐字段对齐。

    `frozen_cash` 这里恒 0.0（旧端口径）：挂单冻结在 `/api/ledger` 里体现，
    两个端点分工与旧端一致，不做「顺手改口径」。
    """
    rows, agg = build_holdings_view(positions, quotes, names)
    cash = num(acct.get("cash"))
    total = round(cash + agg["market_value"], 2)
    upnl_total = sum(r["pnl_amount"] for r in rows)
    single = round(total * SINGLE_ORDER_CAP_RATIO, 2)
    out: Dict[str, Any] = dict(acct)
    out["account"] = {
        "total_asset": total,
        "available_cash": round(cash, 2),
        "market_value": round(agg["market_value"], 2),
        "frozen_cash": 0.0,
        "unrealized_pnl": round(upnl_total, 2),
        "single_order_cap": single,
        "cash_reserve_pct": round(cash / total * 100, 1) if total else 0.0,
        "updated_at": acct.get("updated_at") or now or _now_str(),
    }
    out["positions_list"] = rows
    return out


# ---------------------------------------------------------------- 3. /api/ledger（paper）


def conds_expiring(
    conditions: Sequence[Dict[str, Any]],
    names: Optional[Dict[str, str]],
    threshold: int = EXPIRING_DAYS,
) -> List[Dict[str, Any]]:
    """即将到期的活跃条件单（旧端 `_conds_expiring` 简化版）：剩 ≤3 天就上仪表盘警示。

    `days_left` 由 core 的 `list_conditions()` 现算（不靠建单时的日快照），
    这里只做过滤与形状转换。
    """
    out: List[Dict[str, Any]] = []
    for c in conditions or []:
        if not isinstance(c, dict):
            continue
        if str(c.get("status") or "") != "ACTIVE":
            continue          # 旧端 _conds_expiring 只列 ACTIVE，已撤/已过期不警示
        d = c.get("days_left")
        if d is None:
            continue
        try:
            days = int(d)
        except (TypeError, ValueError):
            continue
        if days > threshold:
            continue
        out.append({
            "cond_id": c.get("cond_id"), "code": c.get("code"),
            "name": resolve_name_table_first(c.get("name"), c.get("code"), names),
            "type": c.get("type"), "label": c.get("label"),
            "days_left": days, "long_term": bool(c.get("long_term")),
            "trigger": (c.get("trigger") or {}).get("price"),
        })
    out.sort(key=lambda x: x["days_left"])
    return out


def book_extras(
    orders: Sequence[Dict[str, Any]],
    conditions: Sequence[Dict[str, Any]],
    names: Optional[Dict[str, str]],
    today8: str,
) -> Dict[str, Any]:
    """第二本账（旧端 `_book_extras`）：活跃单 / 流水记录。

    活跃单 = 挂着未成交的委托 + 监控中的条件单（混在一起，靠 `kind` 区分）；
    流水记录 = 已终态的委托与条件单，按时间倒序，固定上限 50 条防爆。
    """
    out: Dict[str, Any] = {"active": [], "history": [], "hist_max": HIST_MAX, "hist_total": 0}
    try:
        for o in orders or []:
            if not isinstance(o, dict):
                continue
            code = o.get("ts_code") or o.get("code") or ""
            raw = str(o.get("status") or "")
            t = str(o.get("created_at") or o.get("time") or "")
            rec = {
                "kind": "order",
                "id": o.get("paper_order_id") or o.get("order_id") or "",
                "time": (t[11:19] if len(t) >= 19 else t),
                "code": code,
                "name": resolve_name_table_first(o.get("name"), code, names),
                "side": str(o.get("side") or "").upper(),
                "price": num(o.get("limit_price") or o.get("filled_price"), None),  # type: ignore[arg-type]
                "qty": int(o.get("qty") or 0), "filled": int(o.get("filled_qty") or 0),
                "status": STATUS_CN.get(raw, raw), "raw_status": raw,
            }
            (out["active"] if raw in ("REPORTED", "PARTIAL") else out["history"]).append(rec)
        for c in conditions or []:
            if not isinstance(c, dict):
                continue
            raw = str(c.get("status") or "")
            tri = c.get("trigger") or {}
            t = str(c.get("created_at") or c.get("closed_at") or "")
            rec = {
                "kind": "cond", "id": c.get("cond_id"),
                "time": (t[11:19] if len(t) >= 19 else t),
                "code": c.get("code"),
                "name": resolve_name_table_first(c.get("name"), c.get("code"), names),
                "label": c.get("label") or c.get("type"),
                "price": num(tri.get("price") or tri.get("pct") or tri.get("at"), None),  # type: ignore[arg-type]
                "qty": int(c.get("qty") or 0),
                "status": ("监控中" + ("·T+1" if c.get("t1_locked") else "")) if raw == "ACTIVE"
                          else STATUS_CN.get(raw, raw),
                "raw_status": raw,
                "days_left": c.get("days_left"), "expiring": bool(c.get("expiring")),
            }
            if raw == "ACTIVE":
                out["active"].append(rec)
            elif str(c.get("closed_at") or c.get("created_at") or "")[:10].replace("-", "") == today8:
                out["history"].append(rec)       # 终态只看当天
        out["history"].sort(key=lambda x: x["time"], reverse=True)
        out["hist_total"] = len(out["history"])
        out["history"] = out["history"][:HIST_MAX]
        out["active"] = out["active"][:ACTIVE_MAX]
    except Exception:  # noqa: BLE001 - 账本附属信息坏了不影响主数据（旧端同）
        pass
    return out


def build_ledger(
    acct: Dict[str, Any],
    trades_all: Sequence[Dict[str, Any]],
    conditions: Sequence[Dict[str, Any]],
    orders: Sequence[Dict[str, Any]],
    quotes: Optional[Dict[str, Dict[str, Any]]],
    names: Optional[Dict[str, str]],
    frozen_cash: float = 0.0,
    frozen_by_code: Optional[Dict[str, int]] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """账本视图（旧端 `api_ledger` 的 paper 分支逐字移植）。

    - 冻结：`frozen_cash` / `frozen_by_code` 由调用方从 core 的 `svc.frozen_cash()` /
      `svc.frozen_qty(code)` 取（直接读 service 方法比再解析 freeze 文件稳）；
    - T+1：可卖 = 持仓 − 当日买入（以当日成交推算，账本字段只作交叉校验，逐字照旧）；
    - 价格：拿不到就用成本价并标 `price_source=cost_fallback`，**不编造**；
    - `account` 固定 10 个键（`LEDGER_ACCOUNT_KEYS`），顺序与旧端一致。
    """
    today8 = (now or _now_str())[:10].replace("-", "")
    qmap = quotes or {}
    frozen_map = frozen_by_code or {}

    def _nm(code: Any, fallback: Any = "") -> str:
        table = names or {}
        return (table.get(str(code or "")) or table.get(str(code or "").split(".")[0])
                or str(fallback or "") or str(code or ""))

    # 当日买/卖量（只认已成交：FILLED/PARTIAL）
    bought: Dict[str, int] = {}
    sold: Dict[str, int] = {}
    for t in trades_all or []:
        if not isinstance(t, dict):
            continue
        if str(t.get("status")) not in ("FILLED", "PARTIAL"):
            continue
        if str(t.get("created_at") or "")[:10].replace("-", "") != today8:
            continue
        q = int(t.get("filled_qty") or 0)
        code = t.get("ts_code")
        if str(t.get("side")).upper() == "BUY":
            bought[code] = bought.get(code, 0) + q
        else:
            sold[code] = sold.get(code, 0) + q

    cash = num(acct.get("cash"))
    rows: List[Dict[str, Any]] = []
    mv_total = 0.0
    pnl_total = 0.0
    day_total = 0.0
    for code, p in (acct.get("positions") or {}).items():
        p = p if isinstance(p, dict) else {}
        qty = int(p.get("qty") or 0)
        if qty <= 0:
            continue
        cost = num(p.get("cost_price"))
        q = qmap.get(code) or qmap.get(str(code).split(".")[0]) or {}
        price = num(q.get("price")) or cost
        prev = num(q.get("prev_close"))
        # T+1：可卖 = 持仓 − 当日买入；账本 today_bought 只作上限交叉校验
        avail = max(0, qty - int(bought.get(code, 0)))
        if p.get("today_bought") is not None:
            avail = min(avail, max(0, qty - int(p.get("today_bought") or 0)))
        mv = round(qty * price, 2)
        pnl = round((price - cost) * qty, 2)
        day = round((price - prev) * qty, 2) if prev > 0 else None
        mv_total += mv
        pnl_total += pnl
        day_total += day or 0.0
        rows.append({
            "code": code, "name": _nm(code, p.get("name") or ""),
            "qty": qty, "available_qty": max(0, avail),
            "frozen_qty": int(frozen_map.get(code, 0)),
            "cost_price": round(cost, 4), "price": round(price, 3),
            "price_source": q.get("source") or ("cost_fallback" if not q else "quote"),
            "pnl": pnl,
            "pnl_pct": round((price / cost - 1) * 100, 2) if cost else 0.0,
            "day_pnl": day,
            "day_pnl_pct": round((price / prev - 1) * 100, 2) if prev > 0 else None,
            "market_value": mv, "weight_pct": None,
            "bought_today": int(bought.get(code, 0)),
            "sold_today": int(sold.get(code, 0)),
            "mode": "paper",
        })
    for r in rows:
        r["weight_pct"] = round(r["market_value"] / mv_total * 100, 2) if mv_total > 0 else None

    # 当日成交（只认 FILLED/PARTIAL，按时间倒序）
    trades: List[Dict[str, Any]] = []
    for t in trades_all or []:
        if not isinstance(t, dict) or str(t.get("status")) not in ("FILLED", "PARTIAL"):
            continue
        if str(t.get("created_at") or "")[:10].replace("-", "") != today8:
            continue
        qty = int(t.get("filled_qty") or 0)
        price = num(t.get("filled_price"))
        trades.append({"time": str(t.get("created_at") or "")[11:], "code": t.get("ts_code"),
                       "name": _nm(t.get("ts_code"), t.get("name") or ""),
                       "side": t.get("side"), "price": price,
                       "qty": qty, "amount": round(price * qty, 2),
                       "fee": num(t.get("fee")), "mode": "paper"})
    trades.sort(key=lambda x: x["time"], reverse=True)

    total = round(cash + mv_total, 2)
    return {
        "ok": True, "mode": "paper", "asof": now or _now_str(),
        "account": {
            "total_asset": total, "cash": round(cash, 2),
            "available_cash": round(max(0.0, cash - num(frozen_cash)), 2),
            "frozen_cash": round(num(frozen_cash), 2),
            "market_value": round(mv_total, 2),
            "position_pnl": round(pnl_total, 2),
            "position_pnl_pct": (round(pnl_total / (total - pnl_total) * 100, 2)
                                 if total - pnl_total else 0.0),
            "day_pnl": round(day_total, 2),
            "day_pnl_pct": (round(day_total / (total - day_total) * 100, 2)
                            if total - day_total else 0.0),
            "cash_ratio": round(cash / total * 100, 2) if total else 0.0,
        },
        "positions": rows, "trades": trades,
        "conds_expiring": conds_expiring(conditions, names),
        "book": book_extras(orders, conditions, names, today8),
    }


# ---------------------------------------------------------------- 4. 买卖点（/api/trades）


def trade_hhmm(raw: Any) -> Optional[str]:
    """成交时间 → 'HH:MM'（旧端 `_trade_hhmm` 纯函数）。

    兼容三种实测格式：`HHMM`（纸面旧格式）、`YYYY-MM-DD HH:MM:SS`、
    `HH:MM:SS`（无日期）。非法/空 → None（**不猜时间**）。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return "%02d:%02d" % (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None
    d = re.sub(r"\D", "", s)
    if len(d) == 4:
        h, mi = int(d[:2]), int(d[2:])
        return "%02d:%02d" % (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None
    return None


def trade_date8(raw: Any, fallback: Optional[str] = None) -> Optional[str]:
    """成交记录 → 交易日 'YYYYMMDD'（旧端 `_trade_date8`）。

    支持 `YYYY-MM-DD HH:MM:SS` / `YYYYMMDD` / `YYYYMMDDHHMMSS`；取不到返回 fallback。
    """
    s = str(raw or "")
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    if m:
        return "%s%s%s" % (m.group(1), m.group(2), m.group(3))
    d = re.sub(r"\D", "", s)
    if len(d) >= 8:
        return d[:8]
    return fallback


def paper_date8(trade: Dict[str, Any], today8: str) -> str:
    """纸面成交日（旧端 `_paper_date8`）：matched_bar（**含日期才用**）→ closed_at → created_at。

    2026-10-23 修（主人报「K 线出现虚空 B 点」）：`matched_bar` 有两种格式，
    `'09:30:00'` / `'0932'` **只有时刻没有日期**（手工撮合那条路写的），
    旧实现把它摆第一位 → 拿不到日期就回落今天，历史买入点被画到今天的 K 线上。
    口径：matched_bar 不带日期就不参与定日期，改看 closed_at / created_at。
    全无日期才回落 today8（宁可用当日也不丢点）。
    """
    for key in ("matched_bar", "closed_at", "created_at"):
        d = trade_date8(trade.get(key))
        if d:
            return d
    return today8


def valid_cost(value: Any) -> Optional[float]:
    """成本价有效性（旧端 `_valid_cost`）：必须 > 0，否则 None（前端不画 y=0 的假成本线）。"""
    if value is None:
        return None
    f = num(value, None)  # type: ignore[arg-type]
    if f is None or float(f) <= 0:
        return None
    return value


def build_trades_payload(
    code: str,
    days: int,
    paper_trades: Sequence[Dict[str, Any]],
    today8: str,
    cost_paper: Optional[float] = None,
    cost_live: Optional[float] = None,
) -> Dict[str, Any]:
    """买卖点 payload（旧端 `build_trades_payload` 的**纸面部分**）。

    只取 `status=FILLED`（已完成）的纸面成交；points 每项
    `{mode, side, date, t, price, qty, amount, name, id}`。
    `cost` 是可选成本线数据源（前端开关控制画不画），`paper` 取纸面持仓成本、
    `live` 取实盘持仓成本（`ctx.panel_json("real_positions")`），无效即 None。
    """
    days = max(TRADES_DAYS_MIN, min(TRADES_DAYS_MAX, int(days)))
    points: List[Dict[str, Any]] = []
    for tr in paper_trades or []:
        if not isinstance(tr, dict):
            continue
        if str(tr.get("ts_code") or "").upper() != str(code or "").upper():
            continue
        if str(tr.get("status") or "").upper() != "FILLED":
            continue
        side = str(tr.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        points.append({
            "mode": "paper", "side": side,
            "date": paper_date8(tr, today8),
            "t": trade_hhmm(tr.get("matched_bar")),
            "price": round(num(tr.get("filled_price"), 0.0) or 0.0, 3),
            "qty": int(tr.get("filled_qty") or tr.get("qty") or 0),
            "amount": (round(num(tr.get("filled_amount"), 0.0) or 0.0, 2)
                       if tr.get("filled_amount") is not None else None),
            "name": str(tr.get("name") or ""),
            "id": tr.get("paper_order_id"),
        })
    lo = (datetime.strptime(today8, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
    points = [p for p in points if (p.get("date") or "") >= lo]
    points.sort(key=lambda p: ((p.get("date") or ""), (p.get("t") or "")))
    return {
        "version": "trades-%s" % TRADES_VERSION,
        "code": code, "days": days,
        "points": points, "count": len(points),
        "cost": {
            "paper": (None if cost_paper is None else round(num(cost_paper), 3)),
            "live": (None if cost_live is None else round(num(cost_live), 3)),
        },
    }


# ---------------------------------------------------------------- 内部


def _now_str() -> str:
    """统一时间口径（YYYY-MM-DD HH:MM:SS）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
