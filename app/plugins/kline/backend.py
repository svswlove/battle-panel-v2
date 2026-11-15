# -*- coding: utf-8 -*-
"""kline 插件：日线 K 线 + 分时 + 五档 + 筹码 + 逐笔压缩 + 复权事件 + 指标设置。

数据源（v0.6.0，2026-10-23 改）：
- K 线/分时/五档/筹码 → **eltdx**（经 `ctx.quotes` 行情门面，与旧 8800 同源）
- 个股四态背景带 → `amv_board.db::stock_amv`（经 `ctx.db_read` 只读）

**不再依赖 `track_pool.db`**：它是旧系统的核心生产库（track_db/track_data/
qmt_close_collector 在用），但新版不该读它——旧端 `/api/kline` 自己也是走 eltdx，
而 eltdx 的 `adjust="qfq"` 实测与旧端逐值零差异。

v0.8.0 **未发布**（2026-10-24 M1 曾计划把两个根路径端点放本插件，最终改落 `ops`）：
- `GET /xdxr/{ts_code}`（除权除息，旧 8800 `/api/kline/{ts_code}/xdxr`）
- `GET/PUT /kline-indicator-settings`（指标设置持久化）

为什么不在这里：这两个端点都是**根路径**（`/api/kline-indicator-settings`、
`/api/kline/{code}/xdxr`），而本插件是 `mount=plugin`（`/api/p/kline/*`）；
core 当前「一插件一路由表、挂一个前缀」，不支持同插件挂两前缀（见
`HANDOFF_20261024_N1_bpwrite.md §2` 与 `HANDOFF_20261024_M1_fix.md §1.1`）。
两者的**语义全部在 core**：`app/core/quotes.py::xdxr()`（eltdx `corporate.capital_changes`
+ 旧端市场判定照搬）与 `app/core/contract.py::misc_write()`；`ops` 只持 HTTP 壳。
若后续允许改 `main.py` 挂载区，把三条路由搬回本插件即可（本文件零业务依赖）。
"""
from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

AMV_DB = "amv_board.db"
MAX_LIMIT = 500
REGIME_LIB = "amv_regime"
# v0.1 有意边界：只回已完成日线，不含当日未收盘 bar 与 yellow/white/AMV 指标列。
# v0.4.0（2026-10-23）新增当日实时三项：`/intraday` 分时 · `/quote5` 五档 · 后续 `/volume_profile` 筹码。
# v0.7.0（2026-10-24）新增 `/volume_ticks` 逐笔压缩：旧 8800 `api_volume_ticks` 的只读等价。
# 它们走 `ctx.quotes`（core 的 eltdx 只读行情门面），不改日线口径。
NOTICE = "仅历史已完成日线；不含当日未收盘 bar 与 yellow/white/AMV 指标"


def _fetch(conn: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, params)
    if cur.description is None:
        return []
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _conn(ctx: Any, name: str = AMV_DB):
    getter = getattr(ctx, "db_read", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.db_read 不可用")
    try:
        return getter(name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 不可读: %s" % (name, exc))


@contextlib.contextmanager
def _db_conn(ctx: Any, name: str = AMV_DB):
    """只读连接上下文：ctx.db_read 每次新建连接，用完必须关，否则句柄泄漏。"""
    conn = _conn(ctx, name)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _decimals(code: str) -> int:
    """ETF/场内基金与深证指数 3 位，其余 2 位（与 track_data 口径一致）。"""
    if str(code).startswith("399"):
        return 3
    return 3 if str(code)[:3] in ("510", "511", "512", "513", "515", "516", "518", "159", "501", "502") else 2


def _adj(v: Optional[float], scale: Optional[float], offset: Optional[float], dec: int) -> Optional[float]:
    if v is None:
        return None
    if scale is None:
        return round(float(v), dec)
    return round(float(v) * float(scale) + float(offset or 0.0), dec)


def _yellow_white(ctx: Any, closes: List[float]):
    """黄白线：调口径库 `indicators`（engine/common/indicators.py）。

    口径库不可用（未声明/加载失败）→ 返回 None，上屏显示 null，不编造。
    """
    getter = getattr(ctx, "lib", None)
    if not callable(getter):
        return None
    try:
        lib = getter("indicators")
        return lib.yellow_white_lines(closes)
    except Exception:  # noqa: BLE001 - 口径库不可用不影响 K 线主体
        return None


def build_bars(ctx: Any, ts_code: str, limit: int, adjust: str) -> Dict[str, Any]:
    """日线 K 线。**主源 = eltdx（与旧端同源）**，track_pool 仅作降级备选。

    2026-10-23 改：`track_pool.db` 是旧系统的核心生产库（`track_db`/`track_data`/
    `qmt_close_collector` 都在用），但**新版不该依赖它**：
    - 旧端 `/api/kline` 自己就是走 eltdx（`build_kline_payload`）
    - eltdx `adjust="qfq"` 实测 120 根与旧端**零差异**
    - 读 track_pool 要 JOIN 2829 万行 factor，且因子缺事件时静默算错（512480.SH）
    """
    if adjust not in ("qfq", "raw"):
        raise HTTPException(status_code=400, detail="adjust 只支持 qfq/raw")
    limit = max(1, min(MAX_LIMIT, int(limit)))

    q = getattr(ctx, "quotes", None)
    src = "eltdx"
    warnings: List[str] = []
    bars: List[Dict[str, Any]] = []
    if q is not None:
        glb = q.normalize(ts_code)
        if glb:
            rows = q.daily_bars(glb, limit, adjust=None if adjust == "raw" else "qfq")
            dec = _decimals(ts_code)
            for b in rows:
                t = getattr(b, "time", None)
                bars.append({
                    "date": t.strftime("%Y%m%d") if hasattr(t, "strftime") else "",
                    "open": _r(getattr(b, "open", None), dec),
                    "high": _r(getattr(b, "high", None), dec),
                    "low": _r(getattr(b, "low", None), dec),
                    "close": _r(getattr(b, "close", None), dec),
                    "volume": _f(getattr(b, "volume_lots", None)) * 100.0
                              if getattr(b, "volume_lots", None) is not None else None,
                    "amount": _f(getattr(b, "amount", None)),
                })
    if not bars:
        raise HTTPException(status_code=503, detail="行情源不可用，无法取 K 线: %s" % ts_code)
    if len(bars) > limit:
        bars = bars[-limit:]

    # 黄白线（v0.5.0）：口径唯一源 `engine/common/indicators.py`。
    # 旧端 `_kline_payload_out` 是拿 payload 里的 c（前复权）算，这里同口径。
    yellow_white = _yellow_white(ctx, [b["close"] for b in bars])
    if yellow_white is not None:
        yellow, white = yellow_white
        for i, b in enumerate(bars):
            b["yellow"] = None if yellow[i] is None else round(float(yellow[i]), 3)
            b["white"] = round(float(white[i]), 3)
    else:
        for b in bars:
            b["yellow"] = None
            b["white"] = None
    return _finish_bars(ctx, ts_code, bars, warnings, source=src, adjust=adjust)


def _r(v: Any, dec: int) -> Optional[float]:
    f = _f(v)
    return round(f, dec) if f is not None else None


def _finish_bars(ctx: Any, ts_code: str, bars: List[Dict[str, Any]],
                 warnings: List[str], *, source: str, adjust: str = "qfq") -> Dict[str, Any]:
    return {"code": ts_code, "period": "d", "adjust": adjust,
            "stale": False, "warnings": warnings, "bars": bars,
            "intraday": False, "source": source,
            "last_bar_date": bars[-1]["date"] if bars else None,
            "notice": NOTICE}


def build_bg(ctx: Any, ts_code: str, limit: int) -> Dict[str, Any]:
    """GET /bg/{ts_code} ← 个股四态背景带（旧 8800 `/api/kline_bg` 的只读等价）。

    口径唯一源 = `amv_regime.stock_state_series`（经 `ctx.lib`，CONTRACT §8.1）——
    它是 `stock_state` 的逐日滚动展开，判定只在正本定义。
    取数：`stock_amv` 近 N 日 AMV（= alive × 不复权收盘，与板块成分表同口径）。
    返回的 `states` 与 `dates` 等长；前 11 根预热期为 `None`。
    """
    lib = ctx.lib(REGIME_LIB)
    limit = max(12, min(MAX_LIMIT, int(limit)))
    # 预热：四态判定需 12 根（MA10 + EMA 双重预热），但 EMA 递推起点影响要更长才衰减干净。
    # 实测 12 根不够（600000.SH 20260724 差 1 天），60 根后与旧端零差异。
    # ⚠ 预热根数不能被 MAX_LIMIT 吃掉：limit=500 时若 fetch_n 仍封顶 500，就变成零预热，
    # 窗口头 11 根又全 None（2026-10-23 审核实测 covered=489）。所以 fetch 走独立上限。
    preheat = 60
    fetch_n = limit + preheat
    # 2026-10-23 改：收盘价改取 eltdx（与旧端同源），不再读 `track_pool.db::kline`。
    # 注意必须**不复权**：`stock_amv` 的 amv = alive × 不复权收盘，同口径才对得上。
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    glb = q.normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400, detail="非法 ts_code: %r" % (ts_code,))
    raw = q.daily_bars(glb, fetch_n, adjust=None)
    rows = []
    for b in raw:
        t = getattr(b, "time", None)
        c = _f(getattr(b, "close", None))
        if hasattr(t, "strftime") and c is not None:
            rows.append({"trade_date": t.strftime("%Y%m%d"), "close": c})
    if not rows:
        raise HTTPException(status_code=404, detail="no kline for %s" % ts_code)
    rows = rows[-fetch_n:]
    dates_all = [r["trade_date"] for r in rows]
    amv: Dict[str, float] = {}
    try:
        with _db_conn(ctx, AMV_DB) as conn:
            amv = {r["date"].replace("-", ""): r["amv"] for r in _fetch(
                conn, "SELECT date, amv FROM stock_amv WHERE stock=? AND date>=? AND date<=?",
                (str(ts_code).split(".")[0],
                 "%s-%s-%s" % (dates_all[0][:4], dates_all[0][4:6], dates_all[0][6:]),
                 "%s-%s-%s" % (dates_all[-1][:4], dates_all[-1][4:6], dates_all[-1][6:])))}
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - stock_amv 不可用 = 无背景带，不拖垮 K 线主图
        amv = {}
    vals_all = [amv.get(d) for d in dates_all]
    idx = [i for i, v in enumerate(vals_all) if v is not None]
    states_all: List[Optional[str]] = [None] * len(dates_all)
    if idx:
        series = lib.stock_state_series([float(vals_all[i]) for i in idx])
        for pos, i in enumerate(idx):
            states_all[i] = series[pos]
    # 只返回最后 limit 根（前面多取的仅用于预热）
    dates = dates_all[-limit:]
    states = states_all[-limit:]
    covered = sum(1 for s in states if s is not None)
    return {"code": ts_code, "n": len(dates), "dates": dates, "states": states,
            "covered": covered,
            "notice": "四态由 AMV 双线制判定（amv_regime.stock_state_series）；已多取 12 根预热"}


# ---------------------------------------------------------------- 盘后分时落盘（2026-11-07）
#
# 主人：「收盘后的数据就已经固定下来了，没必要每次都重拉重算」。
# 自选卡实时档每票一次 `/api/intraday`，48 票冷启动 8s（eltdx 单连接串行，插件内并行无效）。
# 盘后（周末 / 16:10 后 / 09:15 前）把当日分时落盘，之后重启/重开直接读盘（<0.5s）。
# 16:10 = `OH_PostCloseChain` 收盘取数主链启动时间（周一～五，登记册 A1 T36）。
# 边界：盘中不落盘（数据在变）；只缓存 ok=true；文件名带 code+日期，次日自然失效。
_INTRADAY_CACHE_VER = 1


def _is_postclose(ctx: Any) -> bool:
    """收盘后（数据固定）时段。唯一口径 = core `trade_clock.postclose()`（与 compat 同源）。

    日历不可用/门面缺失 → False（宁可现拉，不拿旧值当固定值）。
    """
    try:
        return bool(ctx.trade_clock.postclose())
    except Exception:  # noqa: BLE001 - 不可用就按“未就绪”处理
        return False


def build_intraday(ctx: Any, ts_code: str) -> Dict[str, Any]:
    """当日分时（v0.4.0 新增）。

    口径逐项对齐旧 8800 `/api/intraday/{ts_code}`（已实测比对）：
    - `points[]` 字段名与含义一致：`t / close / avg / volume / amount / cum_volume`
    - **09:30 首根** = 开盘价，`volume/amount/cum_volume = 0`（旧端同样处理，不是缺数据）
    - `avg` = 累计成交额 / 累计成交量（均价线）；无成交量时回落到 close
    - 时间轴 `session.slots` 固定 241 位（09:30–11:30 + 13:01–15:00，**13:00 无根**）
    - `vol_unit = "股"`；`prev_close` 取今日之前最后一根日线收盘（不复权）

    不可用 → `ok=false` + `reason`，**不编造数据**。
    """
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    glb = q.normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400, detail="非法 ts_code: %r" % (ts_code,))
    # 盘后先查落盘（当日同一票只拉一次）
    # 读路径铁律（2026-11-07）：文件名自然日 = 内容数据日 = 当天，不一致当 miss。
    postclose = _is_postclose(ctx)
    today8 = datetime.now().strftime("%Y%m%d")
    cache_name = ""
    if postclose:
        cache_name = "intraday_%s_%s" % (glb, today8)
        try:
            hit = ctx.cache_read(cache_name)
        except Exception:  # noqa: BLE001 - 首次/未声明/损坏 → 现拉
            hit = None
        if isinstance(hit, dict) and hit.get("v") == _INTRADAY_CACHE_VER and hit.get("date8") == today8:
            cached = hit.get("payload")
            if isinstance(cached, dict) and cached.get("ok") and cached.get("date") == today8:
                return cached
    slots = _intraday_slots()
    bars = q.minute_bars(glb, 241)
    if not bars:
        return {"code": ts_code, "ok": False, "reason": "eltdx 分钟线不可用",
                "session": {"start": "09:30", "end": "15:00", "slots": slots,
                            "slot_count": len(slots)},
                "points": [], "count": 0, "source": "eltdx:1m"}

    idx = {hhmm: i for i, hhmm in enumerate(slots)}
    # 13:00 的根属于下午首根（轴标签 13:01）；旧端同样处理
    idx.setdefault("13:00", idx.get("13:01", 121))

    points: List[Dict[str, Any]] = []
    cum_vol = 0.0
    cum_amt = 0.0
    seen: set[int] = set()
    # 下午首根：先把「13:00 / 13:01」归一成一根，再进主循环。
    # 2026-10-23 实测（600519.SH）：eltdx 同时给 13:00（17900 手）与 13:01，
    # 而 13:01 自身的 volume 已包含 13:00 那段（68600 = 17900 + 50700）。
    # → 有 13:01 就丢掉 13:00（否则翻倍）；只有 13:00 时把它当 13:01 用。
    def _hhmm(x: Any) -> str:
        t = getattr(x, "time", None)
        return t.strftime("%H:%M") if hasattr(t, "strftime") else ""

    bars_seq = list(bars)
    has_1301 = any(_hhmm(b) == "13:01" for b in bars_seq)
    norm: list[Any] = []
    for b in bars_seq:
        hh = _hhmm(b)
        if hh == "13:00":
            if not has_1301:
                norm.append(b)   # 没有 13:01 → 当作下午首根
            continue
        norm.append(b)

    for b in norm:
        hhmm = _hhmm(b)
        i = idx.get(hhmm) or (idx.get("13:01") if hhmm == "13:00" else None)
        if i is None or i in seen:
            continue
        close = _f(getattr(b, "close", None))
        if close is None:
            continue
        seen.add(i)
        vol_lots = _f(getattr(b, "volume_lots", None))
        vol = (vol_lots * 100.0) if vol_lots is not None else 0.0
        amt = _f(getattr(b, "amount", None)) or 0.0
        if i == 0:
            # 09:30 首根：只取开盘价，成交计 0（旧端同口径）
            o = _f(getattr(b, "open", None))
            points.append({"t": slots[0], "close": o if o is not None else close,
                           "avg": o if o is not None else close,
                           "volume": 0.0, "amount": 0.0, "cum_volume": 0.0})
            continue
        cum_vol += vol
        cum_amt += amt
        points.append({
            "t": slots[i],
            "close": round(close, 3),
            "avg": round(cum_amt / cum_vol, 3) if cum_vol > 0 else round(close, 3),
            "volume": round(vol, 1),
            "amount": round(amt, 1),
            "cum_volume": round(cum_vol, 1),
        })

    # 旧端首根固定是 09:30：若分钟线里没有 09:30 这根（eltdx 首根常为 09:31），
    # 用当日开盘价补上，成交计 0 —— 不补会让时间轴从 09:31 起，图上少一格。
    if points and points[0]["t"] != slots[0]:
        first_bar = bars[0] if bars else None
        o = _f(getattr(first_bar, "open", None)) if first_bar is not None else None
        if o is not None:
            points.insert(0, {"t": slots[0], "close": o, "avg": o,
                              "volume": 0.0, "amount": 0.0, "cum_volume": 0.0})

    pre_close = _intraday_prev_close(ctx, glb)
    last = points[-1]["close"] if points else None
    payload = {
        "code": ts_code,
        "ok": True,
        "date": _bar_day(bars[-1]),
        "adj": "raw",
        "vol_unit": "股",
        "session": {"start": "09:30", "end": "15:00", "slots": slots,
                    "slot_count": len(slots)},
        "points": points,
        "count": len(points),
        "pre_close": pre_close,
        "last": last,
        "pct": (round((last / pre_close - 1) * 100, 4)
                if (last is not None and pre_close) else None),
        "source": "eltdx:1m",
        "notice": "分时价未复权；时间轴固定 241 位（13:00 无根）；09:30 首根为开盘价、成交计 0",
    }
    # 落盘铁律（主人 2026-11-07 提醒）：只写“末根日期 = 今天”且 points 非空的结果；
    # 回退到上一交易日、空数据、取数失败一律不写（宁可下次重拉）。
    if (postclose and cache_name and points
            and payload.get("date") == datetime.now().strftime("%Y%m%d")):
        try:
            ctx.cache_write(cache_name, {"v": _INTRADAY_CACHE_VER, "date8": today8,
                                          "payload": payload})
        except Exception:  # noqa: BLE001 - 写失败只是下次多拉一次
            pass
    return payload


def _bar_day(bar: Any) -> Optional[str]:
    t = getattr(bar, "time", None)
    return t.strftime("%Y%m%d") if hasattr(t, "strftime") else None


def _f(v: Any) -> Optional[float]:
    """数值转 float（None 保留 None，不补 0）。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _intraday_slots() -> List[str]:
    """固定分钟轴 ['09:30'...'11:30', '13:01'...'15:00']，共 241 位。

    口径唯一源：旧 8800 `_intraday_slots()`（已实测校准 241 根；13:00 无根）。
    """
    out: List[str] = []
    for h in (9, 10, 11):
        for m in range(60):
            hhmm = "%02d:%02d" % (h, m)
            if hhmm < "09:30" or hhmm > "11:30":
                continue
            out.append(hhmm)
    out.append("13:01")
    for h in (13, 14):
        for m in range(60):
            hhmm = "%02d:%02d" % (h, m)
            if hhmm <= "13:01":
                continue
            out.append(hhmm)
    out.append("15:00")
    return out


def _intraday_prev_close(ctx: Any, glb: str) -> Optional[float]:
    """分时昨收：取今日之前最后一根日线收盘（不复权，与分时同尺度）。

    口径唯一源：旧 8800 `_intraday_prev_close()`（同取 eltdx 原始价、不传 adjust）。
    走门面的 `daily_bars()`（有缓存），不直接 `call()`。不可得 → None（不编造）。
    """
    q = getattr(ctx, "quotes", None)
    if q is None:
        return None
    try:
        rows = q.daily_bars(glb, 8)
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    # 末根可能是当日（盘中就有当日 bar），倒着找第一根日期更早的
    last_day = None
    last_t = getattr(rows[-1], "time", None)
    if hasattr(last_t, "strftime"):
        last_day = last_t.strftime("%Y%m%d")
    for b in reversed(rows):
        t = getattr(b, "time", None)
        d = t.strftime("%Y%m%d") if hasattr(t, "strftime") else ""
        if last_day and d and d < last_day:
            c = getattr(b, "close", None)
            return round(float(c), 3) if c is not None else None
    # 全是同一天（盘前）：直接取末根收盘
    c = getattr(rows[-1], "close", None)
    return round(float(c), 3) if c is not None else None


def build_quote5(ctx: Any, ts_code: str) -> Dict[str, Any]:
    """五档盘口（v0.4.0 新增）。口径逐项对齐旧 8800 `/api/quote5/{ts_code}`。

    形状：`{code,last,pre_close,change,change_pct,high,low,limit_up,limit_down,
              quote_time,asks[],bids[],stats{},ticks[],asof,source}`
    - `asks` = 卖1..卖5（升序）、`bids` = 买1..买5（降序），`vol` 单位 = 手
    - `stats.turnover` = 总量(手)×100 / 流通股本 × 100%
    - `stats.vol_ratio` = (当日累计量 / 已开市分钟) / (过去5日日均量 / 240)
    - `ticks` 新→旧，最多 30 条

    不可用 → 各字段 null + `ok=false`，**不补零、不编造**。
    """
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    glb = q.normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400, detail="非法 ts_code: %r" % (ts_code,))

    rec = q.depth(glb)
    aux = q.stock_aux(glb)
    out: Dict[str, Any] = {
        "code": ts_code, "ok": rec is not None,
        "last": None, "pre_close": None, "change": None, "change_pct": None,
        "high": None, "low": None, "limit_up": None, "limit_down": None,
        "quote_time": None, "asks": [], "bids": [],
        "stats": {"now_vol": None, "total_vol": None, "outer": None, "inner": None,
                  "turnover": None, "vol_ratio": None},
        "ticks": [],
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "eltdx:depth(0x0547)+ticks(0x0fc5)",
    }
    if aux.get("limit_up"):
        out["limit_up"] = round(float(aux["limit_up"]), 3)
    if aux.get("limit_down"):
        out["limit_down"] = round(float(aux["limit_down"]), 3)
    if rec is None:
        return out

    last = _f(getattr(rec, "last_price", None))
    pre = _f(getattr(rec, "last_close_price", None))
    high = _f(getattr(rec, "high_price", None))
    low = _f(getattr(rec, "low_price", None))
    if last and last > 0:
        out["last"] = round(last, 3)
    if pre and pre > 0:
        out["pre_close"] = round(pre, 3)
    if high and high > 0:
        out["high"] = round(high, 3)
    if low and low > 0:
        out["low"] = round(low, 3)
    if out["last"] is not None and out["pre_close"]:
        ch = out["last"] - out["pre_close"]
        out["change"] = round(ch, 3)
        out["change_pct"] = round(ch / out["pre_close"] * 100, 3)
    out["quote_time"] = _fmt_hhmmss(getattr(rec, "update_time_raw", None))

    for side, levels in (("bids", getattr(rec, "buy_levels", ()) or ()),
                         ("asks", getattr(rec, "sell_levels", ()) or ())):
        for lv in list(levels)[:5]:
            price = _f(getattr(lv, "price", None))
            if price is None or price <= 0:
                continue
            out[side].append({"price": round(price, 3),
                              "vol": int(getattr(lv, "volume", 0) or 0)})

    try:
        total = int(getattr(rec, "total_hand", 0) or 0)
        now_vol = int(getattr(rec, "current_hand", 0) or 0)
        outer = int(getattr(rec, "outer_disc", 0) or 0)
        inner = int(getattr(rec, "inside_dish", 0) or 0)
    except (TypeError, ValueError):
        total = now_vol = outer = inner = 0
    st = out["stats"]
    st["total_vol"] = total or None
    st["now_vol"] = now_vol or None
    st["outer"] = outer or None
    st["inner"] = inner or None
    circ = aux.get("circ")
    if circ and total:
        st["turnover"] = round(total * 10000.0 / float(circ), 2)
    avg5 = aux.get("avg5")
    em = _elapsed_minutes(out["quote_time"] or "")
    if avg5 and float(avg5) > 0 and total and em:
        st["vol_ratio"] = round((total / float(em)) / (float(avg5) / 240.0), 3)

    out["ticks"] = _recent_ticks(q, glb, 30)
    return out


def _recent_ticks(q: Any, glb: str, count: int) -> List[Dict[str, Any]]:
    """最近成交（新→旧）。口径同旧端 `_quote5_ticks`。"""
    rows: List[Dict[str, Any]] = []
    for tk in q.today_ticks(glb)[-count:][::-1]:
        price = _f(getattr(tk, "price", None))
        if price is None:
            continue
        rows.append({
            "t": str(getattr(tk, "time_label", "") or ""),
            "price": round(price, 3),
            "vol": int(getattr(tk, "volume", 0) or 0),
            "side": str(getattr(tk, "side", "") or ""),
            "n": int(getattr(tk, "order_count", 0) or 0),
        })
    return rows


def _fmt_hhmmss(raw: Any) -> Optional[str]:
    """eltdx 的 update_time_raw → 'HH:MM:SS'。不可解析 → None。"""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    hh, rem = divmod(v, 10000)
    mm, ss = divmod(rem, 100)
    if hh > 23 or mm > 59 or ss > 59:
        return None
    return "%02d:%02d:%02d" % (hh, mm, ss)


def _elapsed_minutes(hm: str) -> Optional[int]:
    """'15:00' → 当日已开市分钟数（午休不计；9:30 前=0，收盘封顶 240）。

    量比口径：量比 = (当日累计量 / 已开市分钟) / (过去5日日均量 / 240)。
    口径唯一源：旧 8800 `_elapsed_minutes()`。
    """
    try:
        hh, mm = int(hm[0:2]), int(hm[3:5])
    except (TypeError, ValueError, IndexError):
        return None
    t = hh * 60 + mm
    if t < 9 * 60 + 30:
        return 0
    m = min(t - (9 * 60 + 30), 120)
    if t >= 13 * 60:
        m += min(t - 13 * 60, 120)
    return max(1, m)


def build_volume_profile(ctx: Any, ts_code: str, until: Optional[str] = None) -> Dict[str, Any]:
    """价位成交量分布（筹码峰，v0.4.0 新增）。口径逐项对齐旧 8800 `/api/volume_profile/{ts_code}`。

    - `buckets[{p,v}]`：按成交价聚合，升序；`v` 单位 = 手
    - 只计**真实成交**：剔除竞价快照与开盘撮合（`event_kind != "trade"`）
    - `until=HH:MM`：只算到该时刻（供光标悬停），非法则忽略
    - `max_v` / `total_v` 供前端归一化画峰

    不可用 → `ok=false` + 空 buckets，**不编造**。
    """
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    glb = q.normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400, detail="非法 ts_code: %r" % (ts_code,))
    hm = None
    if until:
        digits = "".join(ch for ch in str(until) if ch.isdigit())
        if len(digits) >= 4:
            hm = "%s:%s" % (digits[0:2], digits[2:4])

    ticks = q.today_ticks(glb)
    buckets: Dict[float, int] = {}
    total = 0
    for tk in ticks:
        if getattr(tk, "event_kind", "trade") != "trade":
            continue
        if hm and str(getattr(tk, "time_label", "") or "") > hm:
            continue
        price = _f(getattr(tk, "price", None))
        vol = int(getattr(tk, "volume", 0) or 0)
        if price is None or price <= 0 or vol <= 0:
            continue
        p = round(price, 3)
        buckets[p] = buckets.get(p, 0) + vol
        total += vol

    rows = [{"p": p, "v": v} for p, v in sorted(buckets.items())]
    max_v = max((r["v"] for r in rows), default=0)
    aux = q.stock_aux(glb)
    pre_close = _intraday_prev_close(ctx, glb)
    return {
        "code": ts_code,
        "ok": bool(rows),
        "buckets": rows,
        "max_v": max_v,
        "total_v": total,
        "pre_close": pre_close,
        "limit_up": round(float(aux["limit_up"]), 3) if aux.get("limit_up") else None,
        "limit_down": round(float(aux["limit_down"]), 3) if aux.get("limit_down") else None,
        "until": hm,
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "eltdx:ticks(0x0fc5)",
    }


def build_volume_ticks(ctx: Any, ts_code: str) -> Dict[str, Any]:
    """逐笔压缩（v0.7.0 新增）：`rows=[[minute_idx, price_milli, vol], ...]`。

    口径逐项对齐旧 8800 `api_volume_ticks`（已实测同参对照）：
    - `minute_idx` 对齐 `_intraday_slots()`（09:30=0 … 15:00=240）；**13:00 的逐笔归 13:01**
      （不补这条，前端本地累计会比服务端少 13:00 那一段）
    - `price_milli = round(price × 1000)`；`vol` 单位 = **手**（eltdx ticks 原值，不×100）
    - 只计真实成交：剔除竞价快照/开盘撮合（`event_kind != "trade"`）
    - 同 (分钟, 价位) 合并相加，输出按 (分钟, 价位) 升序
    - `pre_close` 取五档快照 `last_close_price`；`limit_up/limit_down` 取 `stock_aux`

    空态形状（旧端实测）：`pre_close/limit_up/limit_down` 可能为 null、`rows=[]`，HTTP 200。
    非法代码 → 400。`code` 返回**归一后的 ts_code**（旧端 `to_ts_code` 同口径）。
    """
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    glb = q.normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400, detail="非法 ts_code：%r" % (ts_code,))
    code = _glb_to_ts(glb)

    aux = {}
    try:
        aux = q.stock_aux(glb) or {}
    except Exception:  # noqa: BLE001 - 涨跌停不可得 → null，不编造
        aux = {}
    pre_close = None
    try:
        rec = q.depth(glb)
        if rec is not None:
            pre = _f(getattr(rec, "last_close_price", None))
            pre_close = round(pre, 3) if pre else None
    except Exception:  # noqa: BLE001
        pre_close = None

    out: Dict[str, Any] = {
        "code": code,
        "pre_close": pre_close,
        "limit_up": round(float(aux["limit_up"]), 3) if aux.get("limit_up") else None,
        "limit_down": round(float(aux["limit_down"]), 3) if aux.get("limit_down") else None,
        "rows": [],
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "eltdx:ticks(0x0fc5)",
    }

    slots = _intraday_slots()
    idx = {hhmm: i for i, hhmm in enumerate(slots)}
    idx.setdefault("13:00", idx.get("13:01", 121))
    rows: Dict[tuple, int] = {}
    try:
        ticks = q.today_ticks(glb)
    except Exception:  # noqa: BLE001 - 行情源不可用 → 空 rows（同旧端 bundle=None 分支）
        ticks = []
    for tk in ticks:
        if getattr(tk, "event_kind", "trade") != "trade":
            continue
        i = idx.get(str(getattr(tk, "time_label", "") or ""))
        if i is None:
            continue
        price = _f(getattr(tk, "price", None))
        try:
            v = int(getattr(tk, "volume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price is None or price <= 0 or v <= 0:
            continue
        pm = int(round(price * 1000))
        if pm <= 0:
            continue
        rows[(i, pm)] = rows.get((i, pm), 0) + v
    out["rows"] = [[i, pm, v] for (i, pm), v in sorted(rows.items())]
    return out


def _glb_to_ts(glb: str) -> str:
    """`sz000001` → `000001.SZ`（旧端 `to_ts_code` 同口径；本端点回 code 用归一值）。"""
    return glb[2:] + "." + {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(glb[:2], "SZ")


def build_intraday_multi(ctx: Any, ts_code: str, days: int = 5) -> Dict[str, Any]:
    """多日分时（v0.4.1 新增）。口径对齐旧 8800 `/api/intraday_multi/{ts_code}`。

    形状：`{code,start,end,days:[{date,prev_close,points,count}],session,count,vol_unit}`
    - 每日 `points` 与 `/intraday` 同口径（`t/close/avg/volume/amount/cum_volume`）
    - `prev_close` 取该日之前最后一根日线收盘；不可得 → None
    - `adj="front"`（与旧端一致：分时统一用不复权原始价，不另做复权）
    """
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    glb = q.normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400, detail="非法 ts_code: %r" % (ts_code,))
    days = max(1, min(10, int(days)))
    slots = _intraday_slots()
    grouped = q.minute_bars_days(glb, days)
    if not grouped:
        return {"code": ts_code, "ok": False, "reason": "eltdx 分钟线不可用",
                "session": {"start": "09:30", "end": "15:00", "slots": slots,
                            "slot_count": len(slots)},
                "days": [], "count": 0, "vol_unit": "股", "adj": "front",
                "source": "eltdx:1m"}

    idx = {hhmm: i for i, hhmm in enumerate(slots)}
    idx.setdefault("13:00", idx.get("13:01", 121))

    # 日线收盘表：用于每日 prev_close（一次取够，避免逐日请求）
    daily = q.daily_bars(glb, days + 12)
    closes: Dict[str, float] = {}
    for b in daily:
        t = getattr(b, "time", None)
        c = _f(getattr(b, "close", None))
        if hasattr(t, "strftime") and c is not None:
            closes[t.strftime("%Y%m%d")] = c
    ordered_days = sorted(closes)

    def _prev_close(day: str) -> Optional[float]:
        prev = [d for d in ordered_days if d < day]
        return round(closes[prev[-1]], 3) if prev else None

    out_days: List[Dict[str, Any]] = []
    total = 0
    for day in sorted(grouped):
        bars = grouped[day]
        pts: List[Dict[str, Any]] = []
        cum_vol = 0.0
        cum_amt = 0.0
        seen: set[int] = set()
        # 下午首根归一（同 build_intraday）：有 13:01 就丢 13:00，否则把它当 13:01。
        def _hhmm2(x: Any) -> str:
            t = getattr(x, "time", None)
            return t.strftime("%H:%M") if hasattr(t, "strftime") else ""

        has_1301 = any(_hhmm2(b) == "13:01" for b in bars)
        norm: list[Any] = []
        for b in bars:
            hh = _hhmm2(b)
            if hh == "13:00":
                if not has_1301:
                    norm.append(b)
                continue
            norm.append(b)
        for b in norm:
            hhmm = _hhmm2(b)
            i = idx.get(hhmm) or (idx.get("13:01") if hhmm == "13:00" else None)
            if i is None or i in seen:
                continue
            close = _f(getattr(b, "close", None))
            if close is None:
                continue
            seen.add(i)
            vol_lots = _f(getattr(b, "volume_lots", None))
            vol = (vol_lots * 100.0) if vol_lots is not None else 0.0
            amt = _f(getattr(b, "amount", None)) or 0.0
            if i == 0:
                o = _f(getattr(b, "open", None))
                pts.append({"t": slots[0], "close": o if o is not None else close,
                            "avg": o if o is not None else close,
                            "volume": 0.0, "amount": 0.0, "cum_volume": 0.0})
                continue
            cum_vol += vol
            cum_amt += amt
            pts.append({"t": slots[i], "close": round(close, 3),
                        "avg": round(cum_amt / cum_vol, 3) if cum_vol > 0 else round(close, 3),
                        "volume": round(vol, 1), "amount": round(amt, 1),
                        "cum_volume": round(cum_vol, 1)})
        if pts and pts[0]["t"] != slots[0]:
            o = _f(getattr(bars[0], "open", None)) if bars else None
            if o is not None:
                pts.insert(0, {"t": slots[0], "close": o, "avg": o,
                               "volume": 0.0, "amount": 0.0, "cum_volume": 0.0})
        total += len(pts)
        # prev_close：优先用接口自带的（minutes.history 有），否则回日线推算
        pc = getattr(bars[0], "prev_close", None) if bars else None
        pc = _f(pc) if pc is not None else _prev_close(day)
        out_days.append({"date": day, "prev_close": round(pc, 3) if pc else None,
                         "points": pts, "count": len(pts)})

    return {
        "code": ts_code, "ok": True,
        "start": out_days[0]["date"] if out_days else None,
        "end": out_days[-1]["date"] if out_days else None,
        "days": out_days,
        "count": total,
        "session": {"start": "09:30", "end": "15:00", "slots": slots,
                    "slot_count": len(slots)},
        "vol_unit": "股",
        "adj": "front",
        "source": "eltdx:1m",
        "notice": "多日分时统一用不复权原始价；每日时间轴固定 241 位",
    }


def register(ctx: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/bars/{ts_code}")
    def bars(ts_code: str,
             limit: int = Query(default=120),
             adjust: str = Query(default="qfq")) -> Dict[str, Any]:
        return build_bars(ctx, ts_code, limit, adjust)

    @router.get("/quote/{ts_code}")
    def quote(ts_code: str, adjust: str = Query(default="qfq")) -> Dict[str, Any]:
        data = build_bars(ctx, ts_code, 1, adjust)
        return {"code": ts_code, "adjust": adjust, "bar": data["bars"][-1],
                "warnings": data["warnings"]}

    @router.get("/bg/{ts_code}")
    def bg(ts_code: str, limit: int = Query(default=120)) -> Dict[str, Any]:
        return build_bg(ctx, ts_code, limit)

    @router.get("/intraday/{ts_code}")
    def intraday(ts_code: str) -> Dict[str, Any]:
        return build_intraday(ctx, ts_code)

    @router.get("/intraday_multi/{ts_code}")
    def intraday_multi(ts_code: str, days: int = Query(default=5)) -> Dict[str, Any]:
        return build_intraday_multi(ctx, ts_code, days)

    @router.get("/quote5/{ts_code}")
    def quote5(ts_code: str) -> Dict[str, Any]:
        return build_quote5(ctx, ts_code)

    @router.get("/volume_profile/{ts_code}")
    def volume_profile(ts_code: str, until: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        return build_volume_profile(ctx, ts_code, until)

    @router.get("/volume_ticks/{ts_code}")
    def volume_ticks(ts_code: str) -> Dict[str, Any]:
        return build_volume_ticks(ctx, ts_code)

    return router
