# -*- coding: utf-8 -*-
"""amv 插件：只读活跃市值（AMV）快照、regime 与仓位上限。

数据契约见 docs/CONTRACT.md；本文件不直连数据库/文件，只用 ctx.panel_json。
"""
from __future__ import annotations

import contextlib
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

DB = "amv_board.db"
PANEL_LIVE = "amv_live"
PANEL_REGIME = "amv_regime_latest"
PANEL_CAP = "position_cap_latest"
PANEL_BREADTH = "amv_breadth_latest"
PANEL_ALL_A = "all_a_latest"
HCL_JSON = "hcl_latest"


def _load(ctx: Any, name: str) -> Dict[str, Any]:
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
    try:
        return getter(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="%s 不存在: %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 读取失败: %s" % (name, exc))


def _db_read(ctx: Any):
    getter = getattr(ctx, "db_read", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.db_read 不可用")
    try:
        return getter(DB)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 不可读: %s" % (DB, exc))


def build_daily(ctx: Any, days: int) -> Dict[str, Any]:
    """GET /daily：daily_total 的最近 N 日 + MA10（只读，不重算 AMV）。"""
    days = max(1, min(2000, int(days or 250)))
    conn = _db_read(ctx)
    try:
        cur = conn.execute(
            "SELECT date, total FROM daily_total ORDER BY date DESC LIMIT ?", (days,)
        )
        rows = [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    rows.reverse()
    if not rows:
        raise HTTPException(status_code=404, detail="daily_total 无数据")
    totals = [float(r["total"]) if r.get("total") is not None else None for r in rows]
    ma10 = []
    for i, value in enumerate(totals):
        window = [x for x in totals[max(0, i - 9):i + 1] if x is not None]
        ma10.append(round(sum(window) / len(window), 2) if window and value is not None else None)
    return {
        "days": len(rows),
        "unit": "元",
        "dates": [r["date"] for r in rows],
        "total": totals,
        "total_yi": [round(v / 1e8, 2) if v is not None else None for v in totals],
        "ma10": ma10,
    }


def _date_of(payload: Dict[str, Any]) -> str | None:
    for key in ("trade_date", "date"):
        value = payload.get(key)
        if value:
            return str(value)
    meta = payload.get("meta")
    if isinstance(meta, dict) and meta.get("trade_date"):
        return str(meta["trade_date"])
    return None


def build_summary(ctx: Any) -> Dict[str, Any]:
    live = _load(ctx, PANEL_LIVE)
    regime = _load(ctx, PANEL_REGIME)
    cap = _load(ctx, PANEL_CAP)

    live_date = _date_of(live)
    # regime 是“上一收盘”口径，盘中 live 的 trade_date 必然比 regime 新一天；
    # 与 regime 对齐应使用 live.state_date，而不是 live.trade_date。
    state_date = str(live.get("state_date") or live_date or "") or None
    regime_date = _date_of(regime)
    cap_date = _date_of(cap)

    all_block = live.get("all") or {}
    coverage = live.get("coverage") or {}
    forecast = live.get("forecast") or {}
    breadth = live.get("breadth") or {}
    last_switch = regime.get("last_switch") or {}

    stale = {
        "live_vs_regime": bool(state_date and regime_date and state_date != regime_date),
        "cap_vs_live": bool(cap_date and live_date and cap_date != live_date),
    }

    return {
        "trade_date": live_date or regime_date,
        "asof": live.get("asof"),
        "phase": live.get("phase"),
        "amv": all_block.get("amv"),
        "amv_pct": all_block.get("amv_pct"),
        "coverage": coverage,
        "forecast": forecast,
        "breadth": breadth,
        "regime": {
            "date": regime_date,
            "state": regime.get("state"),
            "resonance": regime.get("resonance"),
            "ma10": regime.get("ma10"),
            "pct": regime.get("pct"),
            "amv": regime.get("amv"),
            "last_switch": last_switch,
            "generated_at": regime.get("generated_at"),
        },
        "position_cap": {
            "trade_date": cap_date,
            "regime": cap.get("regime"),
            "base_pct": cap.get("base_pct"),
            "final_pct": cap.get("final_pct"),
            "cap_amount": cap.get("cap_amount"),
            "total_asset": cap.get("total_asset"),
            "degraded": cap.get("degraded"),
            "warning": cap.get("warning"),
        },
        "consistency": {
            "live_date": live_date,
            "live_state_date": state_date,
            "regime_date": regime_date,
            "position_cap_date": cap_date,
            "stale": stale,
        },
    }


def _json(ctx: Any, name: str) -> Dict[str, Any]:
    getter = getattr(ctx, "json_read", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.json_read 不可用")
    try:
        data = getter(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="%s 不存在: %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 读取失败: %s" % (name, exc))
    if not isinstance(data, dict):
        raise HTTPException(status_code=503, detail="%s 顶层不是对象" % name)
    return data


def build_hcl(ctx: Any) -> Dict[str, Any]:
    """GET /hcl ← 旧 8002 `api_hcl`：只读 hcl_calc.py 的落盘结果，不重算、不写任何东西。

    口径唯一源 = `夏天-activity/工具/hcl_calc.py`（行业口径：TQ 二级行业 90 日累计收益
    横截面标准差×4）；本插件只转发。指数口径（`/api/hcl_idx`）需要读 vipdoc 二进制日线，
    不在只读白名单内，未迁移。
    """
    data = dict(_json(ctx, HCL_JSON))
    data["ok"] = True
    # 2026-11-10（主人「一并清理」）：唯一口径源 hcl_calc.py 走通达信客户端本机的 TQ 服务
    #   （127.0.0.1:17709 ·「研究二级行业 128 个」），客户端已退役 → 产物停更在旧交易日。
    #   此处按权威交易日历算期望数据日，显式标 stale：不让旧值冒充当日读数。
    #   不把 stale 当故障：换源 = 口径变更（行业集合与历史序列不可直接比），需主人拍板，
    #   未拍板前只做标注，不改算法、不编造新值。
    try:
        want = str(ctx.trade_clock.expected_data_date8() or "")
        got = str(data.get("date") or "")
        data["stale"] = bool(want and got and got < want)
        if data["stale"]:
            data["stale_expected"] = want
            data["stale_note"] = "数据源已停用（通达信客户端 TQ 接口），读数停在 %s" % got
    except Exception:  # noqa: BLE001 - 日历不可用不影响读数转发
        data["stale"] = None
    return data


def build_breadth(ctx: Any) -> Dict[str, Any]:
    """GET /breadth ← 全 A 广度（AMV≥MA10 占比）+ 分层广度序列。

    数据源 = `amv_breadth_latest.json`（生产链 16:10 落盘），只读不重算。
    """
    return _load(ctx, PANEL_BREADTH)


def build_all_a(ctx: Any) -> Dict[str, Any]:
    """GET /all_a ← 全 A 指数快照（`all_a_latest.json`，生产链落盘）。

    字段：close/prev_close/pct/high/low/up_count/down_count/spark/ma5/ma10/dist5_pct。
    amv 卡用它做头部读数；只读不重算。
    """
    return _load(ctx, PANEL_ALL_A)


def register(ctx: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/summary")
    def summary() -> Dict[str, Any]:
        return build_summary(ctx)

    @router.get("/live")
    def live() -> Dict[str, Any]:
        return _load(ctx, PANEL_LIVE)

    @router.get("/regime")
    def regime() -> Dict[str, Any]:
        return _load(ctx, PANEL_REGIME)

    @router.get("/position_cap")
    def position_cap() -> Dict[str, Any]:
        return _load(ctx, PANEL_CAP)

    @router.get("/indices")
    def indices() -> Dict[str, Any]:
        data = _load(ctx, PANEL_LIVE)
        return {"trade_date": _date_of(data), "indices": data.get("indices") or []}

    @router.get("/sectors")
    def sectors() -> Dict[str, Any]:
        data = _load(ctx, PANEL_LIVE)
        return {"trade_date": _date_of(data), "sectors": data.get("sectors") or {}}

    @router.get("/daily")
    def daily(days: int = Query(default=250)) -> Dict[str, Any]:
        return build_daily(ctx, days)

    @router.get("/history")
    def history(code: str = Query(default="ALL"), days: int = Query(default=60)) -> Dict[str, Any]:
        if code.upper() != "ALL":
            raise HTTPException(status_code=400, detail="v0.1 仅支持 code=ALL（daily_total）")
        data = build_daily(ctx, days)
        return {"code": "ALL", **data}

    @router.get("/hcl")
    def hcl() -> Dict[str, Any]:
        return build_hcl(ctx)

    @router.get("/breadth")
    def breadth() -> Dict[str, Any]:
        return build_breadth(ctx)

    @router.get("/all_a")
    def all_a() -> Dict[str, Any]:
        return build_all_a(ctx)

    return router
