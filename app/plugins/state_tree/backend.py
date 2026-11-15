# -*- coding: utf-8 -*-
"""state_tree 插件：AMV 多空判定状态树（2026-10-30 由旧 8002 `/api/state_tree` 迁入 8810）。

## 口径

**判定逻辑一个字都没搬**：全部在口径唯一源 `夏天-activity/工具/amv_state_tree.py`，
经 `ctx.lib("amv_state_tree")` 取纯函数（`paths.READABLE_LIB` 白名单，只导出纯函数与常量）。
本文件只做「取数 + 装配」，不含任何阈值、任何公式。

## 数据链（三条，全部走 8810 自己的门面）

| 维度 | 来源 | 说明 |
|---|---|---|
| AMV 日线 | `ctx.db_read("amv_board.db")::daily_total` | 元 → 亿元，与 `amv_regime._load_total()` 同一个 SELECT、同一个换算 |
| 形态 K 线 | `ctx.quotes.daily_bars("sh880008", 90)` | eltdx 单例连接在 core，插件不自己连 |
| 盘中实时 | `ctx.amv_live.snapshot()` | 旧仓 `executor/amv_live.py::stocks_snapshot` 同进程调用 |
| 今日分钟轨迹 | `ctx.panel_json("amv_live_sector_track")` | tick_feed 盘中写；全A 分时序列 |

**不转发任何旧端口、不做 mock**：旧 8002 版第三路是 urllib 打 `127.0.0.1:8800/api/amv_research.json`，
8800 退役后那条路恒失败，验收靠 `mock_8800_live.py` 撑——本插件把那一段换成 core 门面。

## 盘中口径与旧版的一处刻意差异

旧版 `build_payload()` 拿 `live_pct` 直接**覆写 `amv[-1]`**（最后一个已收盘交易日）。
若 `daily_total` 当日尚无今日行，这会把一个历史收盘值改成盘中值 —— 历史被污染。
本插件改为：**在序列尾部追加一根「今日虚拟日」**（`amv[-1] × (1 + live_pct/100)`），`live=True`，
不改任何已收盘值。判定规则不变，`days` 末尾多出的那一天就是当日盘中态。

⚠ 该分支在盘后无法实测（收盘后 `amv_live` 恒不可用），当前只有单测覆盖，`docs` 里标未验证。
"""
from __future__ import annotations

import datetime as _dt
import threading
import time
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException, Query

DB = "amv_board.db"
BARS_GLB = "sh880008"
BARS_COUNT = 90
BARS_TTL = 120.0          # 同旧版：盘中要跟最新一根，但不必每请求都连 eltdx
TRACK_TTL = 30.0          # 10.5MB 文件，不每请求重读
DAYS_TAIL = 60            # 逐日回放只回传最近 60 日（同旧版）

_BARS: Dict[str, Any] = {"ts": 0.0, "bars": None, "err": ""}
_TRACK: Dict[str, Any] = {"ts": 0.0, "doc": None}
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 取数

def _lib(ctx: Any) -> Any:
    try:
        return ctx.lib("amv_state_tree")
    except FileNotFoundError as exc:
        # 2026-11-15：口径库文件缺失 = 依赖不可用，回 503（与 db 缺失同语义），503 页提示缺数据
        raise HTTPException(status_code=503, detail="口径库不可用: %s" % exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="口径库不可用: %s" % exc)


def _load_daily(ctx: Any, asof: str | None) -> Tuple[List[str], List[float]]:
    """`daily_total` → (dates, amv 亿元)。

    口径唯一源的取数段（`amv_regime._load_total`）就是这一句 SELECT + `/1e8`；
    这里照抄是为了走 `ctx.db_read` 的只读白名单，不另开连接。
    """
    conn = ctx.db_read(DB)
    try:
        rows = conn.execute("SELECT date, total FROM daily_total ORDER BY date").fetchall()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    dates = [str(r[0]) for r in rows]
    if any(r[1] is None for r in rows):
        raise HTTPException(status_code=503, detail="daily_total 存在空值行，拒用")
    amv = [float(r[1]) / 1e8 for r in rows]
    if asof:
        keep = [i for i, d in enumerate(dates) if d <= asof]
        dates = [dates[i] for i in keep]
        amv = [amv[i] for i in keep]
    if not dates:
        raise HTTPException(status_code=503, detail="daily_total 无数据")
    return dates, amv


def _bar_attr(bar: Any, *names: str) -> Any:
    for name in names:
        v = getattr(bar, name, None)
        if v is not None:
            return v
        if isinstance(bar, dict) and bar.get(name) is not None:
            return bar[name]
    return None


def _load_bars(ctx: Any) -> Tuple[Dict[str, Dict[str, float]], str]:
    """880008 日 K → {YYYYMMDD: {open,high,low,close}}，缓存 120s。

    取数失败返回 ({}, 原因)，**不静默造数**：形态节点落「缺数」，其余节点照常判定。
    """
    now = time.time()
    with _LOCK:
        if _BARS["bars"] is not None and (now - float(_BARS["ts"])) < BARS_TTL:
            return _BARS["bars"], _BARS["err"]
    try:
        rows = ctx.quotes.daily_bars(BARS_GLB, BARS_COUNT) or []
    except Exception as exc:  # noqa: BLE001
        rows, err = [], "%s: %s" % (type(exc).__name__, exc)
    else:
        err = ""
    out: Dict[str, Dict[str, float]] = {}
    for b in rows:
        t = _bar_attr(b, "time")
        key = t.strftime("%Y%m%d") if hasattr(t, "strftime") else str(t or "").replace("-", "")[:8]
        o = _bar_attr(b, "open_price", "open")
        h = _bar_attr(b, "high_price", "high")
        lo = _bar_attr(b, "low_price", "low")
        c = _bar_attr(b, "close_price", "close")
        if not key or None in (o, h, lo, c):
            continue
        out[key] = {"open": round(float(o), 2), "high": round(float(h), 2),
                    "low": round(float(lo), 2), "close": round(float(c), 2)}
    if not out and not err:
        err = "eltdx sh880008 返回空"
    with _LOCK:
        _BARS.update(ts=time.time(), bars=out, err=err)
    return out, err


def _load_track(ctx: Any) -> Dict[str, Any]:
    now = time.time()
    with _LOCK:
        if _TRACK["doc"] is not None and (now - float(_TRACK["ts"])) < TRACK_TTL:
            return _TRACK["doc"]
    try:
        doc = ctx.panel_json("amv_live_sector_track") or {}
    except FileNotFoundError:
        doc = {}
    except Exception:  # noqa: BLE001 - 轨迹缺失只影响分时条，不影响判定
        doc = {}
    with _LOCK:
        _TRACK.update(ts=time.time(), doc=doc)
    return doc


def _live_pct(ctx: Any) -> Tuple[float | None, str | None, str | None]:
    """盘中实时「全A活跃涨跌%」→ (pct, 快照日期, 失败原因)。

    数据源 = `ctx.amv_live`（core 门面 → 旧仓 `amv_live.py::stocks_snapshot`）。
    取不到 / 形状不对 / 日期不可用 → (None, None, 原因)，调用方落收盘口径，**不编造盘中值**。
    """
    try:
        snap = ctx.amv_live.snapshot()
    except Exception as exc:  # noqa: BLE001
        return None, None, "%s: %s" % (type(exc).__name__, exc)
    if not snap:
        try:
            err = ctx.amv_live.last_error()
        except Exception:  # noqa: BLE001
            err = None
        return None, None, err or "实时快照不可用"
    date = str(snap.get("date") or "")
    all_amv = snap.get("all_amv")
    base = snap.get("base_all")
    if not date or not isinstance(all_amv, (int, float)) or not isinstance(base, (int, float)) or not base:
        return None, None, "快照缺 date/all_amv/base_all"
    return (float(all_amv) / float(base) - 1.0) * 100.0, date, None


def _intraday(ctx: Any, lib: Any, date8: str) -> Dict[str, Any]:
    """今日分钟 AMV 序列 + 脏点清洗统计（口径在口径库，本函数只取数与调用）。"""
    doc = _load_track(ctx)
    raw: List[Any] = []
    for k, snaps in (doc.get("dates") or {}).items():
        if str(k).replace("-", "") == date8:
            raw = list(snaps or [])
            break
    if not raw:
        return {"date": date8, "points": [], "stats": {"n_in": 0, "n_kept": 0,
                                                       "n_dropped": 0, "n_filled": 0,
                                                       "dropped": [], "note": "无当日轨迹"}}
    points, stats = lib.clean_minute_points(raw, date8=date8)
    return {"date": date8, "points": points, "stats": stats}


def _envelope(points: List[Dict[str, Any]], bars: Dict[str, Dict[str, float]],
              today8: str) -> Dict[str, Any]:
    """诊断：分钟 `all` 是否越出「全A 当日振幅包络 ±1.0pp」。**只诊断、不丢点**。

    旧版同款（口径未定，不自行改判定规则）：把越界点如实列出来，交主人裁定。
    """
    out: Dict[str, Any] = {"hi_pct": None, "lo_pct": None, "breaches": []}
    keys = sorted(bars or {})
    if today8 not in keys or keys.index(today8) == 0:
        return out
    k = keys.index(today8)
    prev_c = bars[keys[k - 1]].get("close")
    bar = bars[today8]
    if not prev_c:
        return out
    hi = (bar["high"] / prev_c - 1.0) * 100.0
    lo = (bar["low"] / prev_c - 1.0) * 100.0
    out["hi_pct"] = round(hi, 2)
    out["lo_pct"] = round(lo, 2)
    out["breaches"] = [p["ts"] for p in points if p["all"] > hi + 1.0 or p["all"] < lo - 1.0]
    return out


# ---------------------------------------------------------------- 装配

def build_state_tree(ctx: Any, asof: str | None = None) -> Dict[str, Any]:
    lib = _lib(ctx)
    dates, amv = _load_daily(ctx, asof)
    bars, bars_err = _load_bars(ctx)

    live_pct, live_date, live_err = _live_pct(ctx)
    live_used = False
    phase = "close"
    today = _dt.date.today().strftime("%Y-%m-%d")
    # 只在快照确实是「今天」的盘中值时追加虚拟日；否则落收盘口径，不覆写历史。
    if live_pct is not None and live_date == today and dates[-1] != today and amv:
        dates = dates + [today]
        amv = amv + [amv[-1] * (1.0 + float(live_pct) / 100.0)]
        live_used = True
        phase = "live"

    tree = lib.evaluate(dates, amv, bars, live=live_used)
    last = tree["days"][-1]
    today8 = str(dates[-1]).replace("-", "")

    return {
        "ok": True,
        "asof": dates[-1],
        "phase": phase,
        "amv": round(amv[-1], 2),
        "ma10": last["ma10"],
        "pct": last["pct"],
        "bull": tree["bull"],
        "bear": tree["bear"],
        "days": tree["days"][-DAYS_TAIL:],
        "intraday_date": today8,
        "caliber": ("多头触发 单日+4%/两日+6%/三日+6.5% ∧ 形态(880008 下影不明显) ∧ "
                    "T+1/T+2 收盘站上 MA10；空头 单日≤−2.3% / AMV<MA10 / MACD死叉 / W3(待定)"),
        "source": {
            "amv": "amv_board.db::daily_total（元→亿元，同 amv_regime._load_total）",
            "bars": "eltdx:sh880008 日K（ctx.quotes）" if not bars_err else ("缺数：" + bars_err),
            "live": ("ctx.amv_live 实时快照" if live_used
                     else (live_err or "收盘口径")),
            "intraday": "data/panel/amv_live_sector_track.json（ctx.panel_json，见 GET /intraday）",
            "caliber_lib": "夏天-activity/工具/amv_state_tree.py（ctx.lib）",
        },
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_intraday(ctx: Any, date8: str | None = None) -> Dict[str, Any]:
    """今日分钟轨迹 + 脏点清洗统计（独立端点：10.5MB 轨迹读与 120s 清洗不应堵首屏）。"""
    lib = _lib(ctx)
    if not date8:
        dates, _amv = _load_daily(ctx, None)
        date8 = str(dates[-1]).replace("-", "")
    out = _intraday(ctx, lib, date8)
    bars, _err = _load_bars(ctx)
    out["stats"]["envelope"] = _envelope(out["points"], bars, date8)
    return out


def register(ctx: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/tree")
    def tree(asof: str = Query(default="")) -> Dict[str, Any]:
        return build_state_tree(ctx, asof or None)

    @router.get("/intraday")
    def intraday(date: str = Query(default="")) -> Dict[str, Any]:
        return build_intraday(ctx, date.replace("-", "") or None)

    @router.get("/days")
    def days(asof: str = Query(default=""), n: int = Query(default=60)) -> Dict[str, Any]:
        payload = build_state_tree(ctx, asof or None)
        n = max(1, min(DAYS_TAIL, int(n or DAYS_TAIL)))
        return {"ok": True, "asof": payload["asof"], "days": payload["days"][-n:]}

    return router
