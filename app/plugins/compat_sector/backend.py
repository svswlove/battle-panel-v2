"""compat_sector 插件后端：旧前端「板块」域 9 个根路径端点（只读，0.2.0）。

契约：docs/CONTRACT.md；旧端实现与逐端点比对：docs/compat_sector.md；
本批（N3）逐端点「旧 handler 行号 + 数据源 + 新落点」：docs/HANDOFF_20261024_N3_sector.md。

**端口归属（源码级核实，2026-10-23）**：这 9 个路径**全部**注册在旧 `engine/server.py`（8800），
`夏天-activity\\工具\\dashboard/server.py`（8002）的 `handle()` 里一个都没有。
任务书表里写「8002?」是待确认项；实测归属与依据见 docs/compat_sector.md §0。

**三个带参端点不归本插件（2026-10-24）**：`/stock_amv/{ts_code}`、`/sector_amv/{code}`、
`/sector_kline/{code}` 已由 `app/plugins/compat_misc` 在**同一 root 前缀**真实实现
（旧 8800 `api_stock_amv` / `api_sector_amv` / `api_sector_kline` → `tdx_sector_api`）。
`app/core/main.py:322-337` 的根路径冲突保护会在启动时直接报错 ⇒ 本插件再注册一遍会让 8810 起不来，
故不重复实现（实测 stock_amv / sector_amv 与旧端逐值一致；sector_kline 的差异与归属建议见 HANDOFF §4）。

路径不带 `/api` 前缀：本插件 manifest 里 `backend.mount = "root"`，core 统一挂到 `/api`。

数据只经 `ctx.db_read` / `ctx.panel_json` / `ctx.json_read` / `ctx.lib` / `ctx.quotes` 取得；
本文件不直连数据库、不发 HTTP、不写任何文件。
"""

from __future__ import annotations

import datetime as _dt
import contextlib
import copy
import math
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, HTTPException, Query

AMV_DB = "amv_board.db"
SMA_LIB = "amv_regime"       # MA10 口径唯一源（旧 server.py `_amv_sma_fn` 同源）
INDICATOR_LIB = "indicators"  # 黄白线口径唯一源（旧 server.py `common.indicators` 同源）
TRACK_PANEL = "amv_live_sector_track"  # 板块轨迹生产文件（paths.READABLE_PANEL 已登记）
ETF_PANEL = "etf_sector_map"           # ETF→板块映射（paths.READABLE_PANEL 已登记）
TEMPERATURE_JSON = "temperature_latest"  # 市场温度 J0（paths.READABLE_JSON；文件不在 data/panel）

LEVELS = ("L1", "L2", "L3", "CONCEPT", "INDEX")

# 旧 server.py 的两组 SVG 画布常量（逐个照抄，别改成"差不多"的尺寸）
CARD_TOP_W, CARD_TOP_H = 100.0, 40.0     # 上图：收盘 + 黄白线
CARD_BOT_W, CARD_BOT_H = 100.0, 20.0     # 下图：活跃市值 + MA10
SPARK_W, SPARK_H = 100.0, 24.0           # 背景迷你线

# 连续竞价时段（旧端走 common.trading_calendar.in_continuous_auction；
# 该模块未登记为口径库，这里按同一时段常量判定，节假日日历年差异见报告 §6）
AUCTION = ((_dt.time(9, 30), _dt.time(11, 30)), (_dt.time(13, 0), _dt.time(15, 0)))
STALE_SECONDS = 180
# 880005/880006 日线「末根距今 ≤ N 自然日」才算新鲜（旧 server.py:107 BREADTH_880_STALE_DAYS）
BREADTH_880_STALE_DAYS = 30


# ---------------------------------------------------------------- 基础工具

def _norm_level(value: Any, default: str = "L2") -> str:
    lv = str(value or "").upper()
    return lv if lv in LEVELS else default


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def _split_codes(codes: Any) -> List[str]:
    return [c.strip() for c in str(codes or "").split(",") if c.strip()]


def _finite(value: Any) -> bool:
    """旧 server.py `_finite` 同口径：None / nan / inf 一律 False。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


@contextlib.contextmanager
def _conn_opt(ctx: Any) -> Iterator[Optional[Any]]:
    """取只读连接；库不可用 → 交出 None（调用方按旧端「文件缺失返回 {}」降级）。"""
    conn = None
    try:
        conn = ctx.db_read(AMV_DB)
    except FileNotFoundError:
        conn = None
    except Exception as exc:  # noqa: BLE001 - 权限/白名单问题要暴露，不静默
        raise HTTPException(status_code=503, detail="%s 不可读: %s" % (AMV_DB, exc))
    try:
        yield conn
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _rows(conn: Any, sql: str, params: tuple = ()) -> List[tuple]:
    return list(conn.execute(sql, params))


def _panel_opt(ctx: Any, name: str) -> Optional[Dict[str, Any]]:
    """读白名单 panel JSON；未声明 / 不在白名单 / 文件缺失 → None（降级，不抛）。

    `amv_live_sector_track` 与 `etf_sector_map` 已在 `paths.READABLE_PANEL` 登记
    （2026-10-23 compat 第 3 批），故 `sector_intraday` / `sector_track` / `etf_sector_map`
    三条现在拿的是**真实生产文件**；只有文件真缺失时才落到「数据缺失」分支。
    """
    try:
        data = ctx.panel_json(name)
    except Exception:  # noqa: BLE001 - PermissionError / FileNotFoundError 统一降级
        return None
    return data if isinstance(data, dict) else None


def _sma(ctx: Any):
    """MA10 口径唯一源：`amv_regime.sma`（旧 server.py `_amv_sma_fn` 同一模块）。"""
    return ctx.lib(SMA_LIB).sma


def _yellow_white(ctx: Any):
    """黄白线口径唯一源：`indicators.yellow_white_lines`（旧 server.py `common.indicators`）。"""
    try:
        return ctx.lib(INDICATOR_LIB).yellow_white_lines
    except Exception:  # noqa: BLE001 - 口径库不可用 → 旧端也是"黄白线留空"
        return None


def _mk(vals: List[Optional[float]], w: float, h: float, lo: float, hi: float) -> str:
    """归一化 SVG path（旧 server.py `mk` 逐字照抄）。

    x 用**下标**而非有效点序号（None 处留空档），所以不能改成 enumerate(valid)。
    """
    pts: List[str] = []
    n = len(vals)
    rng = (hi - lo) or 1.0
    for i, v in enumerate(vals):
        if v is None:
            continue
        x = round(i / (n - 1) * w, 2) if n > 1 else 0.0
        y = round(h - (v - lo) / rng * h, 2)
        pts.append(f"{x},{y}")
    return "M " + " L ".join(pts) if pts else ""


def _py(v: float, lo: float, hi: float, h: float) -> float:
    return round(h - (v - lo) / ((hi - lo) or 1.0) * h, 2)


# ---------------------------------------------------------------- /sector_card.json

def build_sector_card(ctx: Any, codes: Any, level: Any, days: Any, win: Any) -> Dict[str, Any]:
    """自选板块卡片（旧 8800 `api_sector_card`）。

    每板块 → 上图「收盘 + 黄白线」/ 下图「活跃市值 + MA10」SVG path
    + 主力净流入（亿）/ 权重变化（pp）/ AMV 涨幅% / 真实涨幅%。
    源 = amv_board.db（sector_daily amv/share + board_flow close/flow_main + daily_total 权重分母）。
    """
    want = _split_codes(codes)
    if not want:
        return {}
    days = _clamp_int(days if days not in (None, "") else 60, 60, 20, 120)
    win = _clamp_int(win if win not in (None, "") else 5, 5, 1, 20)
    lvl = _norm_level(level, "L2")

    with _conn_opt(ctx) as conn:
        if conn is None:
            return {}
        try:
            dates = sorted(r[0] for r in _rows(
                conn,
                "SELECT DISTINCT date FROM sector_daily WHERE level=? ORDER BY date DESC LIMIT ?",
                (lvl, days + 160)))
            if not dates:
                return {}
            d0 = dates[0]
            amv: Dict[str, Dict[str, Any]] = {c: {} for c in want}
            bf: Dict[str, Dict[str, Any]] = {c: {} for c in want}
            for d, c, v in _rows(conn,
                                 "SELECT date,code,amv FROM sector_daily WHERE level=? AND date>=?",
                                 (lvl, d0)):
                if c in amv:
                    amv[c][d] = v
            for d, board, cl, fl in _rows(
                    conn, "SELECT date,board_code,close,flow_main FROM board_flow WHERE date>=?", (d0,)):
                if board in bf:
                    bf[board][d] = (cl, fl)
            tot = {d: v for d, v in _rows(
                conn, "SELECT date,total FROM daily_total WHERE date>=?", (d0,))}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 旧端同样是 500 + 明确 detail
            raise HTTPException(status_code=500, detail=f"板块卡片数据失败：{type(exc).__name__}: {exc}")

    sma = _sma(ctx)
    ywl = _yellow_white(ctx)
    # 显示窗口：所有板块统一「先按满窗算 MA10/黄白线，再切到 days」。
    # ⚠ 与旧端有意不同：旧端在 for 循环**内部**就地 `dates = dates[-days:]`，
    #    于是第 2 个及以后的板块只用 days 日算 MA10（前 9 点为 None）。
    #    这里统一成「先算后切」（= 旧端第 1 个板块、也是其注释所写本意），
    #    多板块请求时第 2+ 板块的 amv.ma 点数会与旧端不同（值差异，非结构差异）。
    #    详见 docs/compat_sector.md §5 第 6 条。
    shown = dates[-days:]
    out: Dict[str, Any] = {}
    for c in want:
        av = [amv[c].get(d) for d in dates]
        if len([v for v in av if v is not None]) < 5:
            continue
        avf = [float(v) if v is not None else None for v in av]
        ma10 = sma([v if v is not None else 0.0 for v in avf], 10)
        cl = [(bf[c].get(d) or (None, None))[0] for d in dates]
        clv = [float(v) if v is not None else None for v in cl]
        yl: Optional[List[Optional[float]]] = None
        wh: Optional[List[float]] = None
        if ywl is not None and len([v for v in clv if v is not None]) >= 20:
            try:
                _y, _w = ywl([v if v is not None else float("nan") for v in clv])
                yl, wh = list(_y), list(_w)
            except Exception:  # noqa: BLE001 - 旧端同：黄白线算不出就留空，不阻断
                yl = wh = None
        avf, ma10, clv = avf[-days:], ma10[-days:], clv[-days:]
        if yl:
            yl = yl[-days:]
        if wh:
            wh = wh[-days:]

        aval = [v for v in avf if v is not None]
        if not aval:
            continue
        alo, ahi = min(aval), max(aval)
        if ahi <= alo:
            ahi = alo + 1
        cval = [v for v in (clv or []) if v is not None]
        top: Dict[str, Any] = {}
        if cval:
            pool = cval + [v for v in (yl or []) if v is not None] + [v for v in (wh or []) if v is not None]
            clo, chi = min(pool), max(pool)
            if chi <= clo:
                chi = clo + 1
            top = {"c": _mk(clv, CARD_TOP_W, CARD_TOP_H, clo, chi),
                   "yl": _mk(yl or [], CARD_TOP_W, CARD_TOP_H, clo, chi) if yl else "",
                   "wh": _mk(wh or [], CARD_TOP_W, CARD_TOP_H, clo, chi) if wh else ""}
        last = len(shown) - 1
        i0 = max(0, last - win)
        fl = (bf[c].get(shown[last]) or (None, None))[1] if shown else None
        t_now = tot.get(shown[last], 0) if shown else 0
        t_old = tot.get(shown[i0], 0) if shown else 0
        w_now = (avf[last] / t_now) if (avf[last] and t_now) else None
        w_old = (avf[i0] / t_old) if (avf[i0] and t_old) else None
        amv_pct = ((avf[last] / avf[i0] - 1) * 100) if (avf[last] and avf[i0]) else None
        rp = ((clv[last] / clv[i0] - 1) * 100) if (clv[last] and clv[i0]) else None
        out[c] = {
            "close": top,
            "amv": {"a": _mk(avf, CARD_BOT_W, CARD_BOT_H, alo, ahi),
                    "ma": _mk(ma10, CARD_BOT_W, CARD_BOT_H, alo, ahi)},
            # flow_main 单位=万元 → 亿元（旧端 /1e4）
            "main_net": round(fl / 1e4, 2) if fl is not None else None,
            "w_delta": round((w_now - w_old) * 100, 2) if (w_now is not None and w_old is not None) else None,
            "amv_pct": round(amv_pct, 2) if amv_pct is not None else None,
            "real_pct": round(rp, 2) if rp is not None else None,
            "win": win,
        }
    return out


# ---------------------------------------------------------------- /sector_spark.json

def build_sector_spark(ctx: Any, codes: Any, days: Any, level: Any) -> Dict[str, Any]:
    """背景迷你线：近 N 日活跃市值 + MA10 → {code: {amv, ma10}}（SVG path d，100x24）。"""
    want = _split_codes(codes)
    if not want:
        return {}
    days = _clamp_int(days if days not in (None, "") else 60, 60, 10, 120)
    lvl = _norm_level(level, "L2")

    with _conn_opt(ctx) as conn:
        if conn is None:
            return {}
        try:
            dates = sorted(r[0] for r in _rows(
                conn,
                "SELECT DISTINCT date FROM sector_daily WHERE level=? ORDER BY date DESC LIMIT ?",
                (lvl, days)))
            data: Dict[str, Dict[str, Any]] = {c: {} for c in want}
            if dates:
                for d, c, amv in _rows(
                        conn, "SELECT date,code,amv FROM sector_daily WHERE level=? AND date>=?",
                        (lvl, dates[0])):
                    if c in data:
                        data[c][d] = amv
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"板块背景线失败：{type(exc).__name__}: {exc}")

    sma = _sma(ctx)
    out: Dict[str, Any] = {}
    n = len(dates)
    if n < 2:
        return out
    for c in want:
        vals = [data[c].get(d) for d in dates]
        if len(vals) < 5 or any(v is None for v in vals):
            continue
        nums = [float(v) for v in vals]
        ma = sma(nums, 10)
        allv = nums + [v for v in ma if v is not None]
        lo, hi = min(allv), max(allv)
        if hi <= lo:
            continue
        px = lambda i: round(i / (n - 1) * SPARK_W, 2)  # noqa: E731
        out[c] = {
            "amv": "M " + " L ".join(f"{px(i)},{_py(v, lo, hi, SPARK_H)}"
                                     for i, v in enumerate(nums)),
            "ma10": "M " + " L ".join(f"{px(i)},{_py(v, lo, hi, SPARK_H)}"
                                      for i, v in enumerate(ma) if v is not None),
        }
    return out


# ---------------------------------------------------------------- /sector_intraday.json

def build_sector_intraday(ctx: Any, codes: Any) -> Dict[str, Any]:
    """板块当日轨迹（旧 8800 `api_sector_intraday`）：只返回请求的码。

    返回 {asof, date, source, codes:{<code>:{name, points:[{t,amv_pct,change_pct}], count, ok}}}；
    无轨迹 → ok:false + count:0（前端显示 —，不编造）。
    """
    want = _split_codes(codes)
    if not want:
        return {"asof": "", "date": None, "source": "none", "codes": {}}
    empty = {"asof": "", "date": None, "source": "none",
             "codes": {c: {"name": c, "points": [], "count": 0, "ok": False} for c in want}}
    data = _panel_opt(ctx, TRACK_PANEL)
    if data is None or not isinstance(data.get("dates"), dict):
        return empty
    keys = list(data["dates"].keys())
    if not keys:
        return empty
    d = max(keys, key=_dkey)
    snaps = data["dates"].get(d) or []
    asof = str((snaps[-1] or {}).get("ts") or "") if snaps else ""
    out: Dict[str, Any] = {}
    for c in want:
        pts: List[Dict[str, Any]] = []
        name = c
        for s in snaps:
            b = (s.get("boards") or {}).get(c)
            if not isinstance(b, dict):
                continue
            name = b.get("name") or name
            ap, cp = b.get("amv_pct"), b.get("change_pct")
            if ap is None and cp is None:
                continue
            pts.append({"t": str(s.get("ts") or "")[11:16], "amv_pct": ap, "change_pct": cp})
        out[c] = {"name": name, "points": pts, "count": len(pts), "ok": bool(pts)}
    return {"asof": asof, "date": d, "source": "amv_live_sector_track", "codes": out}


# ---------------------------------------------------------------- /sector_track.json

def _dkey(x: Any) -> str:
    """日期键归一（旧端同日修：写入端混用 20260930 / 2026-10-08，'-'(0x2D)<'0'(0x30)）。"""
    return str(x).replace("-", "")


def _in_continuous_auction(now: Optional[_dt.datetime] = None) -> bool:
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return any(a <= t <= b for a, b in AUCTION)


def _track_stale(last_ts: Any, now: Optional[_dt.datetime] = None) -> tuple:
    """轨迹新鲜度闸门（旧 8800 `_track_stale`）：仅连续竞价时段判，午休/盘后冻结属正常。"""
    now = now or _dt.datetime.now()
    if not _in_continuous_auction(now):
        return False, ""
    if not last_ts:
        return True, "轨迹无数据"
    try:
        t = _dt.datetime.strptime(str(last_ts)[:19], "%Y-%m-%d %H:%M:%S")
        age = (now - t).total_seconds()
        if age > STALE_SECONDS:
            return True, f"末点 {last_ts} 距今 {int(age / 60)}min（盘中 >3min 无新点）"
        return False, ""
    except Exception:  # noqa: BLE001 - 判定失败不阻断展示（旧端同）
        return False, ""


def _track_weights(conn: Any, dash: Any) -> tuple:
    """权重基线：track 日**之前最近一交易日**的 L2 share（%）；BK9999 不下发。"""
    weights: Dict[str, float] = {}
    try:
        rows = _rows(conn, "SELECT MAX(date) FROM sector_daily WHERE level='L2' AND date<?", (dash,))
        wdate = rows[0][0] if rows else None
        if wdate:
            for code, share in _rows(
                    conn,
                    "SELECT code,share FROM sector_daily WHERE level='L2' AND date=? AND code<>'BK9999'",
                    (wdate,)):
                if share is not None:
                    weights[code] = round(float(share) * 100, 4)
    except Exception:  # noqa: BLE001 - 权重缺失不阻断（前端降级不画四象）
        return {}, None
    return weights, wdate


# ---------------------------------------------------------------- idx_chg 读路径补值（2026-10-27）

# 六指数（顺序与前端 EVO_IDX 一致；只读展示用，不做任何指标计算）
# 2026-11-02 主人：末位原「中证2000 932000.SH」换成「国证2000 399303.SZ」——
#   932000 在通达信属「中证指数」扩展市场（市场号 62，走 7727），7709 标准行情无此码；
#   国证2000 在深市指数段（399xxx），走 7709 正常可取。
IDX_CODES = ("000001.SH", "399006.SZ", "000688.SH", "000300.SH", "000852.SH", "399303.SZ")
IDX_CACHE_TTL = 20.0     # 秒：一张卡 20s 刷一次，同一交易分钟内不重复打行情
_IDX_LOCK = threading.Lock()
_IDX_CACHE: Dict[str, Any] = {}      # {date8: (ts, {code: [(hhmm, pct)]})}


def _idx_series_one(ctx: Any, code: str, date8: str) -> List[tuple]:
    """单指数 → [(HH:MM, 涨跌%)]（升序）；不可用/日期不符 → []。

    口径：涨跌% = (分钟收盘 / 该交易日之前的最后一根日线收盘 − 1) × 100。
    取数一律走 core 行情门面（`ctx.quotes.minute_bars` / `daily_bars`），不自连行情源。
    **只认目标交易日**：`minute_bars` 返回「最近一个交易日」，日期不符就不拿它硬填。
    """
    try:
        q = ctx.quotes
    except Exception:  # noqa: BLE001 - 未注入行情门面 → 无数据（前端降级）
        return []
    try:
        glb = q.normalize(code)
        if not glb:
            return []
        bars = list(q.minute_bars(glb, 241) or [])
    except Exception:  # noqa: BLE001 - 取数不可用一律空，不编造
        return []
    if not bars:
        return []
    want = "%s-%s-%s" % (date8[:4], date8[4:6], date8[6:])
    pts: List[tuple] = []
    first_open: Optional[float] = None
    for b in bars:
        t = getattr(b, "time", None)
        if not hasattr(t, "strftime"):
            continue
        if t.strftime("%Y-%m-%d") != want:
            return []          # 跨日：宁可不补，也不把今天曲线贴到历史快照上
        c = getattr(b, "close", None)
        try:
            c = float(c) if c is not None else None
        except (TypeError, ValueError):
            c = None
        if first_open is None:
            try:
                first_open = float(getattr(b, "open", None))
            except (TypeError, ValueError):
                first_open = None
        if c is not None:
            pts.append((t.strftime("%H:%M"), c))
    if not pts:
        return []
    pre = None
    try:
        for d in reversed(list(q.daily_bars(glb, 8) or [])):
            dt = getattr(d, "time", None)
            if hasattr(dt, "strftime") and dt.strftime("%Y-%m-%d") < want:
                pre = float(getattr(d, "close", None))
                break
    except Exception:  # noqa: BLE001
        pre = None
    if pre is None or pre <= 0:
        # 没有独立昨收就缺数据；当日开盘不是昨收，拿它代替会伪造涨跌口径。
        return []
    out: List[tuple] = []
    if first_open:                       # 09:30 那个快照点用当日开盘价
        out.append(("09:30", round((first_open / pre - 1) * 100, 3)))
    for hhmm, c in pts:
        out.append((hhmm, round((c / pre - 1) * 100, 3)))
    return out


def idx_series_map(ctx: Any, date8: str) -> Dict[str, List[tuple]]:
    """六指数当日分钟涨跌（带 20s 缓存）。每指数**只取一次**，不按快照重复取。"""
    now = time.time()
    with _IDX_LOCK:
        hit = _IDX_CACHE.get(date8)
        if hit and now - hit[0] < IDX_CACHE_TTL:
            return hit[1]
    out: Dict[str, List[tuple]] = {}
    for code in IDX_CODES:
        s = _idx_series_one(ctx, code, date8)
        if s:
            out[code] = s
    with _IDX_LOCK:
        _IDX_CACHE.clear()
        _IDX_CACHE[date8] = (now, out)
    return out


def _pct_at(series: List[tuple], hhmm: str) -> Optional[float]:
    """取 ≤ hhmm 的最近一个分钟值（快照 ts 带秒，用「不晚于」对齐；不向前外推）。"""
    v = None
    for t, x in series:
        if t > hhmm:
            break
        v = x
    return v


def fill_idx_chg(ctx: Any, snaps: Any, date8: str,
                 today8: Optional[str] = None) -> List[Dict[str, Any]]:
    """给缺 `idx_chg` 的快照按 ts 分钟补值；已有字段、其它字段原样保留（纯函数式拷贝）。

    无法对齐的日期/指数：保留缺字段，**不补 0、不拿别的日期硬填**。
    返回值恒为新列表，不改调用方传入的 dict（生产轨迹文件只读）。

    `today8` 可注入（默认服务端今天）：**只有当日轨迹才去拉分钟线**——翻历史日期时
    不白白打 6 次行情（那 6 次也只会返回今天的曲线，本来就对不上）。
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(snaps, list):
        return out
    today = today8 or _dt.date.today().strftime("%Y%m%d")
    need = [s for s in snaps if isinstance(s, dict) and not isinstance(s.get("idx_chg"), dict)]
    series: Dict[str, List[tuple]] = (
        idx_series_map(ctx, date8) if (need and len(date8) == 8 and date8 == today) else {})
    for s in snaps:
        if not isinstance(s, dict):
            continue
        cur = s.get("idx_chg")
        if isinstance(cur, dict) and cur:
            out.append(s)                     # 旧文件自带 → 原样透传
            continue
        if not series:
            out.append(s)
            continue
        hhmm = str(s.get("ts") or "")[11:16]
        vals: Dict[str, float] = {}
        for code, arr in series.items():
            v = _pct_at(arr, hhmm) if hhmm else None
            if v is not None:
                vals[code] = v
        if not vals:
            out.append(s)
            continue
        s2 = dict(s)
        s2["idx_chg"] = vals
        out.append(s2)
    return out


def _track_skeleton(ctx: Any, date: Any = "") -> Dict[str, Any]:
    """降级骨架：**键集合与旧端正常分支逐键一致**（旧端返回 9 个键，无 `source`）。

    旧端正常分支（engine/server.py:2838-2842）的键：
        date, snapshots, boards, weights, weights_date,
        stale, stale_reason, last_ts, meta
    `weights` 仍从 amv_board.db 取（该库在白名单内），故降级时这部分数据不缺。
    """
    weights: Dict[str, float] = {}
    wdate = None
    d = str(date or "") or None
    dk = _dkey(d) if d else ""
    dash = f"{dk[:4]}-{dk[4:6]}-{dk[6:]}" if (len(dk) == 8 and dk.isdigit()) else d
    if dash:
        with _conn_opt(ctx) as conn:
            if conn is not None:
                weights, wdate = _track_weights(conn, dash)
    return {"date": d, "snapshots": [], "boards": {},
            "weights": weights, "weights_date": wdate,
            "stale": False, "stale_reason": "",
            "last_ts": None, "meta": {}}


def build_sector_track(ctx: Any, date: Any) -> Dict[str, Any]:
    """板块轮动当天轨迹（旧 8800 `api_sector_track`）：快照序列 + 最新全板块 + 权重基线。

    ⚠ 返回键必须与旧端正常分支**逐键一致**（旧端没有 `source` 键，降级分支早期多写了它）。

    2026-10-27（N20261027-100741-29 等 4 条标注）：生产轨迹文件里的快照**只写了 ts/all/boards**，
    没有 `idx_chg`（旧契约里有这个字段），于是前端「行情涨跌」模式六个指数全空。这里在**读路径**
    按 ts 分钟补 `idx_chg`：不写回生产文件，只补缺失字段，原 snap 其余字段原样保留。
    """
    data = _panel_opt(ctx, TRACK_PANEL)
    if data is None or not isinstance(data.get("dates"), dict):
        return _track_skeleton(ctx, date)
    keys = list(data["dates"].keys())
    if not keys:
        return _track_skeleton(ctx, date)
    want = _dkey(date) if date else ""
    d = next((k for k in keys if _dkey(k) == want), None) or max(keys, key=_dkey)
    snaps = data["dates"].get(d) or []
    # 只补缺失的 idx_chg（纯读，不改原文件）；历史日期不拉分钟线
    snaps = fill_idx_chg(ctx, snaps, _dkey(d))
    boards = (snaps[-1].get("boards") if snaps else {}) or {}

    weights: Dict[str, float] = {}
    wdate = None
    dk = _dkey(d)
    dash = f"{dk[:4]}-{dk[4:6]}-{dk[6:]}" if (len(dk) == 8 and dk.isdigit()) else str(d)
    with _conn_opt(ctx) as conn:
        if conn is not None:
            weights, wdate = _track_weights(conn, dash)

    last_ts = (snaps[-1].get("ts") if snaps else "")
    stale, stale_reason = _track_stale(last_ts)
    return {"date": d, "snapshots": snaps, "boards": boards,
            "weights": weights, "weights_date": wdate,
            "stale": stale, "stale_reason": stale_reason,
            "last_ts": last_ts or None,
            "meta": data.get("meta") or {}}


# ---------------------------------------------------------------- /sector_track_backfill_status

def build_backfill_status(ctx: Any) -> Dict[str, Any]:
    """板块轨迹回填状态（旧 8800 `api_sector_track_backfill_status`，只读）。

    旧端读 `engine/state/sector_track_backfill_status.json`——**state 是红线**，
    新后端不读不写；这里恒返回「无任务在跑」的等价形状（前端轮询看到 status 缺失即不动作）。
    """
    return {"ok": True, "running": False, "status_file": {}}


# ---------------------------------------------------------------- /sector/health

def build_health(ctx: Any) -> Dict[str, Any]:
    """板块域健康（旧 8800 `/api/sector/health` 结构：{alive, port}）。

    ⚠ 语义变更（见 docs/compat_sector.md §5）：旧端是 **HTTP 探活 8002**，新后端禁发 HTTP，
    改为「板块域数据源是否可用」——`amv_board.db` 能查出板块日频即为 alive。
    """
    alive = False
    try:
        with _conn_opt(ctx) as conn:
            if conn is not None:
                alive = bool(_rows(conn, "SELECT 1 FROM sector_daily LIMIT 1"))
    except HTTPException:
        raise        # 白名单/权限配置错误要暴露，不能伪装成「服务不可用」
    except Exception:  # noqa: BLE001 - 其余探活失败 = not alive，不抛
        alive = False
    return {"alive": alive, "port": 8002}


# ---------------------------------------------------------------- /etf_sector_map.json

# 旧端 `data/panel/etf_sector_map.json` 的**全量顶层键**（实读样本，2026-10-23）。
# 降级骨架必须逐键同构：`compare_legacy` 按「键路径集合」判结构，少一个键就 FAIL。
# 注意旧端「文件缺失」只回 `{"map": {}}`，但那是旧端自己也没数据时的兜底；
# 新后端的降级发生在**旧端有数据、新版读不到**的场景，故按全量键给骨架，不照抄 `{"map":{}}`。
ETF_SKELETON = {
    "generated_by": None,
    "note": None,
    "targets": None,
    "etfs": None,
    "mapped": None,
    "unmatched": None,
    "aliases": None,
    "aliases_unresolved": [],
    "map": {},
}


def build_etf_sector_map(ctx: Any) -> Dict[str, Any]:
    """ETF→板块映射（旧 8800 `api_etf_sector_map`）：原样透传。

    拿不到（白名单未登记 / 文件缺失）→ 返回**旧端全量顶层键**的空骨架，
    其中 `aliases`=`aliases_unresolved` 也在（01 验收指出原实现缺这两个键）。
    """
    data = _panel_opt(ctx, ETF_PANEL)
    if data is None:
        return copy.deepcopy(ETF_SKELETON)   # 深拷贝：map/aliases_unresolved 是可变对象
    return data


# ---------------------------------------------------------------- /breadth_880.json

# 盘后落盘（2026-11-07）：涨跌家数每次现取两块快照 + 两条日线，重启后首屏要重拉。
# 收盘后数据固定 → 落盘；失效 = 数据日 ≠ 当前应有交易日（`trade_clock.expected_data_date8`）。
# 三道铁律：空/降级不写｜失败不写｜数据日不符当 miss。路径归 core，本文件不碰文件系统。
BREADTH_DISK_NAME = "breadth_880"


def _is_postclose(ctx: Any) -> bool:
    """收盘后（数据固定）时段。唯一口径 = core `trade_clock.postclose()`。

    门面缺失/抛错 → False：宁可现拉，不拿旧值当固定值。
    """
    try:
        return bool(ctx.trade_clock.postclose())
    except Exception:  # noqa: BLE001 - 未注入/日历故障一律按“未就绪”
        return False


def _expected_date8(ctx: Any) -> str:
    """当前「应该看到」的数据日（YYYYMMDD）；门面不可用 → 空串（不读不写盘）。"""
    try:
        return str(ctx.trade_clock.expected_data_date8() or "")
    except Exception:  # noqa: BLE001
        return ""


def _breadth_ready(today: Any) -> bool:
    """涨跌家数关键字段齐且非空才算有数据（空/降级不得落盘，也不得命中）。

    total 一起要求：flat = total − up − down，缺 total 的 flat 也是推导值。
    """
    if not isinstance(today, dict):
        return False
    return all(_finite(today.get(k)) for k in
               ("up", "down", "total", "limit_up", "limit_down"))


def _breadth_disk_get(ctx: Any, date8: str) -> Optional[Dict[str, Any]]:
    """盘后读盘：数据日必须等于应有交易日，payload 关键字段必须齐。不符 → None（现算）。"""
    if not date8:
        return None
    try:
        doc = ctx.cache_read(BREADTH_DISK_NAME)
    except Exception:  # noqa: BLE001 - 首次/未声明/损坏 → 现算
        return None
    if not isinstance(doc, dict) or str(doc.get("date") or "") != date8:
        return None
    today, prev = doc.get("today"), doc.get("prev")
    if not _breadth_ready(today) or not isinstance(prev, dict):
        return None
    return {"today": copy.deepcopy(today), "prev": copy.deepcopy(prev)}


def _breadth_disk_put(ctx: Any, date8: str, body: Dict[str, Any]) -> None:
    """盘后写盘：只在数据日 == 应有交易日、且涨跌家数有效时写（空/降级不写）。"""
    if not date8 or not _breadth_ready(body.get("today")):
        return
    try:
        ctx.cache_write(BREADTH_DISK_NAME, {"date": date8,
                                           "today": body["today"], "prev": body["prev"]})
    except Exception:  # noqa: BLE001 - 写失败只是下次多拉一次，不影响响应
        pass


def _snapshot_880(ctx: Any, code: str) -> Optional[tuple]:
    """880005/880006 快照 → (last, open, high, amount) 或 None（不可达/字段缺失）。

    旧 server.py `_eltdx_880_snapshot` 同口径，但连接改走 `ctx.quotes` 门面（禁自连行情源）。
    880005：last=涨, open=跌, high=总数, amount=成交额（元）；880006：last=涨停, open=跌停。
    """
    try:
        res = ctx.quotes.call("quotes.get_snapshots", ["sh" + code])
        # `quotes.get_snapshots` 返回快照列表（旧端同：`snaps[0]`）；
        # 兼容未来返回分页对象（有 .records / .snapshots）的形态。
        if isinstance(res, (list, tuple)):
            recs = list(res)
        else:
            recs = list(getattr(res, "snapshots", None) or getattr(res, "records", None) or ())
        snap = recs[0] if recs else None
        if snap is None:
            return None
        last = getattr(snap, "last_price", None)
        open_ = getattr(snap, "open_price", None)
        high = getattr(snap, "high_price", None)
        amount = getattr(snap, "amount", None)
        if not _finite(last) or not _finite(open_):
            return None
        return (int(round(float(last))), int(round(float(open_))),
                int(round(float(high))) if _finite(high) else None,
                float(amount) if _finite(amount) else None)
    except Exception:  # noqa: BLE001 - 行情源不可用一律降级为 None
        return None


def _count_880(value: Any) -> Optional[int]:
    """家数值归一为 int（行情门面给 float；旧端从 .day 整数 //100 得 int）。"""
    return int(round(float(value))) if _finite(value) else None


def _day_880(ctx: Any, code: str, count: int = 6) -> List[tuple]:
    """880005/880006 日线 → [(YYYYMMDD:int, open, high, close, amount)]（升序）。

    旧端 `_read_880_day`（`engine/server.py:899-919`）直读 vipdoc `sh{code}.day` 二进制
    （32B/条，`struct.unpack('<IIIIIfII')`）。插件不得直读二进制文件、vipdoc 不在任何白名单，
    改走 `ctx.quotes.daily_bars`（`app/core/quotes.py:262`，同一份通达信日线，价格已 /100）。
    因此本函数得到的 o/h/c **不再** //100（旧端 `.day` 里存的是 ×100 的整数）。
    不可用 → []（调用方按「昨日不可得」降级，不编造）。
    """
    try:
        bars = ctx.quotes.daily_bars("sh" + code, count=count)
    except Exception:  # noqa: BLE001 - 行情源不可用 / 未注入 → 空表
        return []
    out: List[tuple] = []
    for bar in bars or []:
        t = getattr(bar, "time", None)
        if not hasattr(t, "strftime"):
            continue
        try:
            d = int(t.strftime("%Y%m%d"))
        except (TypeError, ValueError):
            continue
        out.append((d, getattr(bar, "open", None), getattr(bar, "high", None),
                    getattr(bar, "close", None), getattr(bar, "amount", None)))
    out.sort(key=lambda r: r[0])
    return out


def _prev_880(ctx: Any, today8: str) -> Dict[str, Any]:
    """昨日条（旧 8800 `_breadth_880_prev`，`engine/server.py:935-977` 逐条照搬语义）。

    旧端：880005 的 `.day` 里 up=close、down=open、total=high（×100）；880006 里
    limit_up=close、limit_down=open；末根距今 > `BREADTH_880_STALE_DAYS` 视为客户端停旧 → 不取。
    改源后数值不再 //100（见 `_day_880`）。结构与旧端逐键一致：**prev 无 `total` 键**。

    **锚 = 快照交易日，不是自然日（2026-10-24 修 R124 P1）**：非交易日（周末/节假日）里
    880005/880006 快照返回的是最近一个交易日的收盘值，远端日线末根也正是那一根。旧实现拿
    「自然日 today」当锚 → 末根（= 快照当日）满足 `< today`，被当成昨日，prev 与 today 全等。
    改法：锚取「日线末根日期」（= 快照所代表的交易日，且不超过自然日），再取严格早于它的一根；
    只有一根（或没有更早的）→ prev 全 null，宁可缺也不冒充。
    """
    prev: Dict[str, Any] = {"date": None, "up": None, "down": None, "flat": None,
                            "limit_up": None, "limit_down": None, "amount": None}
    try:
        t_int = int(today8)
        today = _dt.datetime.strptime(today8, "%Y%m%d")
    except (TypeError, ValueError):
        return prev

    rows5 = _day_880(ctx, "880005")
    rows6 = _day_880(ctx, "880006")
    anchor = 0
    for rows in (rows5, rows6):
        if rows:
            anchor = max(anchor, min(int(rows[-1][0]), t_int))
    if not anchor:                     # 两源都没有日线 → 昨日不可得
        return prev

    def _pick(rows: List[tuple]) -> Optional[tuple]:
        if not rows:
            return None
        try:
            gap = (today - _dt.datetime.strptime(str(rows[-1][0]), "%Y%m%d")).days
        except ValueError:
            return None
        if gap > BREADTH_880_STALE_DAYS:   # 防旧文件冒充昨日
            return None
        for row in reversed(rows):
            if row[0] < anchor:            # 严格早于快照交易日（末根 = 快照当日，必须跳过）
                return row
        return None

    r5 = _pick(rows5)
    r6 = _pick(rows6)
    if r5 is not None:
        d, open_, high, close, amount = r5
        up, down, total = _count_880(close), _count_880(open_), _count_880(high)
        prev.update({"date": str(d), "up": up, "down": down,
                     "amount": float(amount) if _finite(amount) else None})
        if total is not None and up is not None and down is not None and total >= up + down:
            prev["flat"] = total - up - down
    if r6 is not None:
        prev["limit_up"], prev["limit_down"] = _count_880(r6[3]), _count_880(r6[1])
        if prev["date"] is None:
            prev["date"] = str(r6[0])
    return prev


def build_breadth_880(ctx: Any) -> Dict[str, Any]:
    """同源 880005/880006 涨跌家数（旧 8800 `api_breadth_880`，`engine/server.py:979-987`）。

    today ← 行情源快照（涨/跌/涨停/跌停/总数/成交额，平＝总数−涨−跌）；
    prev  ← 880005/880006 昨日日线（旧端读 vipdoc `.day`，新版走 `ctx.quotes.daily_bars`，
            同一份数据源、不同通道）。两源都不可达 → 对应字段 null（不编造）。

    盘后（`postclose`）先查落盘缓存，命中直接返回（重启后首屏不再拉两块快照 + 两条日线）；
    算完只在「涨跌家数有效 + 数据日 == 应有交易日」时落盘（铁律：空/降级不写）。
    """
    postclose = _is_postclose(ctx)
    expected8 = _expected_date8(ctx) if postclose else ""
    if postclose:
        hit = _breadth_disk_get(ctx, expected8)
        if hit is not None:
            return hit
    # 数据日 = 快照所代表的交易日（880005 日线末根），**不是自然日**。
    # 周末/节假日读到的就是最近交易日收盘；标成自然日就是「昨天冒充今天」（主人 2026-11-07 提醒）。
    snap8 = ""
    try:
        rows5_all = _day_880(ctx, "880005")
        if rows5_all:
            snap8 = str(rows5_all[-1][0])
    except Exception:  # noqa: BLE001 - 取不到日线 → 退回自然日（与旧行为一致）
        snap8 = ""
    today8 = snap8 or _dt.datetime.now().strftime("%Y%m%d")
    today: Dict[str, Any] = {"date": today8, "up": None, "down": None, "flat": None,
                             "limit_up": None, "limit_down": None, "total": None,
                             "amount": None}
    s5 = _snapshot_880(ctx, "880005")
    if s5 is not None:
        up, down, total, amount = s5
        today["up"], today["down"], today["amount"] = up, down, amount
        if total is not None and total >= up + down:
            today["total"] = total
            today["flat"] = total - up - down
    s6 = _snapshot_880(ctx, "880006")
    if s6 is not None:
        today["limit_up"], today["limit_down"] = s6[0], s6[1]
    body = {"today": today, "prev": _prev_880(ctx, today8)}
    if postclose:
        # 数据日 = today.date（已经是快照代表的交易日），与应有交易日一致才落盘
        data8 = str(today.get("date") or expected8)
        if data8 == expected8:
            _breadth_disk_put(ctx, data8, body)
    return body


# ---------------------------------------------------------------- /temperature_latest.json

# 旧端 `temperature_latest.json` 的**全量顶层键**（实读样本 2026-10-23，42 行）。
# 降级骨架逐键同构（`dimensions` 四维各带自己的子键），值一律 None —— 不编造数字。
TEMPERATURE_SKELETON = {
    "date": None,
    "computed_at": None,
    "regime": None,
    "valuation_source": None,
    "dims_valid": None,
    "temperature_raw_sum": None,
    "dimensions": {
        "valuation": {"pe": None, "percentile": None, "score": None},
        "volume": {"amount_now_yi": None, "amount_20d_yi": None, "ratio": None, "score": None},
        "trend": {"close": None, "ma20": None, "ma20_prev": None, "dist_pct": None, "score": None},
        "sentiment": {"up": None, "down": None, "ratio": None, "score": None},
    },
    "temperature": None,
    "band": None,
    "cap_raw": None,
    "cap_effective": None,
    "cap_label": None,
    "note": None,
    "regime_note": None,
    "single_stock_cap": None,
}


def build_temperature(ctx: Any) -> Any:
    """市场温度（旧 8800 `api_temperature`，`engine/server.py:858-865`）：原样透传。

    旧端源 `TEMPERATURE_FILE`（`engine/server.py:93`）= `夏天-activity/工具/temperature_data/
    temperature_latest.json`，**不在 data/panel** → 登记在 `paths.READABLE_JSON`（名
    `temperature_latest`），走 `ctx.json_read`（不是 `panel_json`）。

    文件真缺失 → 返回**旧端同构空骨架**（不是 404）：旧端 404 只会在文件不存在时发生，
    而前端把 404 当「整卡报错」、把 null 字段当「数据不可用」（显 —）。骨架方案见
    docs/compat_sector.md §5 与 HANDOFF §5 的口径说明。
    """
    try:
        data = ctx.json_read(TEMPERATURE_JSON)
    except Exception:  # noqa: BLE001 - 未声明 / 未登记 / 文件缺失 → 骨架降级，不 500
        data = None
    if not isinstance(data, dict):
        return copy.deepcopy(TEMPERATURE_SKELETON)
    return data


# ---------------------------------------------------------------- 路由

def register(ctx: Any) -> APIRouter:
    """插件入口：返回 APIRouter（manifest 里 mount=root ⇒ core 挂到 /api）。"""
    router = APIRouter()

    @router.get("/sector_card.json")
    def sector_card(codes: str = Query(default=""), level: str = Query(default="L2"),
                    days: str = Query(default="60"), win: str = Query(default="5")) -> Dict[str, Any]:
        return build_sector_card(ctx, codes, level, days, win)

    @router.get("/sector_spark.json")
    def sector_spark(codes: str = Query(default=""), days: str = Query(default="60"),
                     level: str = Query(default="L2")) -> Dict[str, Any]:
        return build_sector_spark(ctx, codes, days, level)

    @router.get("/sector_intraday.json")
    def sector_intraday(codes: str = Query(default="")) -> Dict[str, Any]:
        return build_sector_intraday(ctx, codes)

    @router.get("/sector_track.json")
    def sector_track(date: str = Query(default="")) -> Dict[str, Any]:
        return build_sector_track(ctx, date)

    @router.get("/sector_track_backfill_status")
    def sector_track_backfill_status() -> Dict[str, Any]:
        return build_backfill_status(ctx)

    @router.get("/sector/health")
    def sector_health() -> Dict[str, Any]:
        return build_health(ctx)

    @router.get("/etf_sector_map.json")
    def etf_sector_map() -> Dict[str, Any]:
        return build_etf_sector_map(ctx)

    @router.get("/breadth_880.json")
    def breadth_880() -> Dict[str, Any]:
        return build_breadth_880(ctx)

    @router.get("/temperature_latest.json")
    def temperature_latest() -> Any:
        return build_temperature(ctx)

    return router
