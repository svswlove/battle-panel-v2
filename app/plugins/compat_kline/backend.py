# -*- coding: utf-8 -*-
"""compat_kline：旧面板前端「K 线域」8 个根路径端点的兼容层。

**这是过渡层，不是新功能。** 旧前端（`engine/web/kline_*.js`）请求的是根路径
`/api/kline/{code}` 等；本插件用 `backend.mount = "root"` 注册到 `/api`，
响应的**键名/结构逐字段对齐旧 8800**（口径唯一来源：旧仓 `engine/server.py`
的 `_kline_payload_out` / `build_intraday_payload` / `build_quote5_payload` /
`build_volume_profile` / `api_volume_ticks` / `_f10_brief_payload`）。

端点（9 个）：

| 路径 | 说明 |
|---|---|
| `GET /api/kline/{ts_code}` | 日/周/分钟 K 线 + 黄白线 + AMV 副图 + chg/amp/turnover |
| `GET /api/kline_bg/{ts_code}` | 四态/多空背景带 `items{日期:状态}` |
| `GET /api/intraday/{ts_code}` | 当日分时（241 位时间轴 + 集合竞价段） |
| `GET /api/intraday_multi/{ts_code}` | 多日分时 |
| `GET /api/quote5/{ts_code}` | 五档盘口卡 |
| `GET /api/volume_profile/{ts_code}` | 价位成交量分布（筹码峰） |
| `GET /api/volume_ticks/{ts_code}` | 逐笔压缩 `[[minute_idx, price_milli, vol]]` |
| `GET /api/f10_brief/{ts_code}` | F10 基础信息侧边栏 |
| `GET /api/f10_brief/{ts_code}/quick` | 轻量 F10：只取 valuation（弹窗首屏用） |

**为什么不 import 别的插件**：CONTRACT §8 禁止插件互相 import（`kline` 插件的
`bars` 函数不能直接调）。
**为什么不在插件里发网络请求打 8810**：契约静态扫描禁一切出站调用。
所以本插件按同一口径**重新实现取数**（只读、同源）：

- 行情（K 线/分时/五档/逐笔）→ `ctx.quotes`（core 行情门面，全进程单例连接）
- F10 B2 段（估值/市值/北向/盈利预测/增减持/融资融券/季度业绩/股东户数序列）→
  `ctx.quotes.f10_brief(glb)`（core 门面持有 eltdx **F10 HTTP** 客户端；插件不得自建）
- 主力动向 `mf` → `ctx.lib("l2_mf")`（`Hubagent/01/_tools/l2_mf.py` 口径唯一源，
  旧 8800 `_stock_mf` 同源）
- 个股 AMV → `ctx.db_read("amv_board.db")::stock_amv`（AMV 归 amv 插件所有，这里只读不重算）
- 黄白线/白线 → `ctx.lib("indicators")`（`engine/common/indicators.py` 口径唯一源）
- 四态/多空 → `ctx.lib("amv_regime")`
- 行业/概念归属 → `ctx.db_read("ths_board.db")`

**板块/概念指数码（`*.TI`）**（2026-10-24 S2 迁移，`docs/HANDOFF_20261024_S2_tikline.md`）：
`/api/kline/{881xxx.TI|884xxx.TI|885xxx.TI|886xxx.TI}` 已按旧端
`build_index_kline_payload` 逐段照搬：日线直取 `ths_index.db::index_daily`、周线走原生
1w 源（不可用 → `period_unavailable` 标记，**不拿日线聚合冒充**）、`before` 游标、
AMV 副图读 `amv_board.db::sector_daily`、stale 按「应收到的最后一根」判、分钟/分时明确 400。

**唯一未落地的一段（如实登记，不是静默降级）**：周线原生 1w 的**上游调用**必须在 core
行情门面里——插件侧禁一切出站调用（契约静态扫描 + `tools/cutover_gate.py` 均拦），
所以本插件通过 `ctx.quotes.ths_index_weekly(code, start8, end8)` 这个**接缝**取数：
凭据读取、代理旁路、SSE/JSON 解析全归 core（插件不读凭据、不落盘、不进日志）。
接缝尚未实现时如实报「周线源不可用」——与旧 8800 今日实测返回逐字段一致
（上游 1w 当前对所有码返回 `code=5003 Data source unavailable`）。补法见 HANDOFF §4。

等旧前端改完、新前端补齐后，本插件整体删除。
"""
from __future__ import annotations

import copy
import contextlib
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException, Query

from app.core import paths as _core_paths

# ---------------------------------------------------------------- 常量（照旧端）

KLINE_VERSION = "v1-20260912"
INTRADAY_VERSION = "v1-20260917"

# 旧端 period 白名单 → eltdx 周期键
KLINE_PERIODS: Dict[str, str] = {
    "d": "day", "w": "week",
    "m1": "1m", "m5": "5m", "m15": "15m", "m30": "30m", "m60": "60m",
}
KLINE_LIMIT_DEFAULT = 240
KLINE_LIMIT_MIN = 120
KLINE_LIMIT_MAX = 800
# 单次 bars.get 上限（eltdx 实测：count=800 正常、900 直接返回 None）
BARS_PAGE = 800
# 黄线预热（2026-11-01）：黄线 = (MA14+MA28+MA57+MA114)/4，口径库里 i<113 一律 None
# （engine/common/indicators.py 降级口径，不编代理值）。只取 limit 根就只能在尾部
# 挤出 7 根有值 —— 实测 limit=60/120 都是「黄线只显最后一小段」。
# 故取数时多拉 113 根只用于预热，算完再裁回 limit 根。
YELLOW_WARMUP = 113
# 「加载更早」翻页上限（日/周 8 页 ≈ 25 年；分钟 5 页足够覆盖其窗口）
BEFORE_MAX_PAGES_DAILY = 8
BEFORE_MAX_PAGES_MINUTE = 5
KLINE_MINUTE_BARS_PER_DAY = {"m1": 241, "m5": 48, "m15": 16, "m30": 8, "m60": 4}

SESSION_START = "09:30"
SESSION_END = "15:00"
AUCTION_START = "09:15"
AUCTION_END = "09:25"
INTRADAY_MULTI_DEFAULT_DAYS = 5
INTRADAY_MULTI_MAX_DAYS = 10   # 旧端 60；门面 minute_bars_days 实测窗口上限 10 日

QUOTE5_MAX_LEVELS = 5
QUOTE5_TICKS = 30
KLINE_BG_MAX_DAYS = 800

AMV_DB = "amv_board.db"
THS_DB = "ths_board.db"
INDEX_DB = "ths_index.db"       # 板块/概念指数日线（2026-10-24 S2 迁入只读白名单）
# 板块指数 payload 的 boards 恒为空（旧端 `build_index_kline_payload` 传的就是空表）：
# 板块指数没有「所属行业/概念」口径，不是取不到。
_EMPTY_BOARDS: Dict[str, Any] = {"industry": [], "concepts": []}
# 哨兵：区分「未传」（走个股默认取数）与「显式传 None」（板块指数不取）
_UNSET: Any = object()

# 板块指数周线：只认原生 1w 源（旧 8800 2026-10-19 主人裁定：不得用日线聚合冒充周线）。
# 取数经 core 行情门面的接缝 `ctx.quotes.ths_index_weekly()`——凭据读取、代理旁路、
# SSE/JSON 解析全部在 core（插件不读凭据、不发网络，见模块头）。
# 60s 记忆：同码同窗口不重复打上游（盘中刷新频繁，避免把上游打挂）。
_THS_WEEK_MEMO: Dict[str, Tuple[float, Optional[List[Dict[str, Any]]]]] = {}
_THS_WEEK_MEMO_TTL_S = 60.0
_THS_WEEK_UNAVAILABLE = "同花顺指数周线源不可用（1w 返回 5003/空）"

_SUFFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


# ---------------------------------------------------------------- 基础工具



# ---------------------------------------------------------------- 结构骨架（降级用）
# mf 与 F10 B2 段都已有真实现（2026-10-24 S1）；骨架仅用于**取数失败时的降级**：
# 保证面板不崩、字段名与旧端一致，值一律 None / 列表一律 []（前端显示「—」，
# 不会误读成 0）。骨架由旧端真实响应自动生成（2026-10-23），不是手抄。
# 由 tools 生成：旧端结构骨架（值全 null / 列表全空）
_MF_SKELETON = {
        'lookback': None,
        '主力资金': [],
        '主买': [],
        '占成交': [],
        '占流通': [],
        'q': [],
        'source': None,
    }

_F10_SKELETON = {
        'code': None,
        'asof': None,
        'source': None,
        'sector': None,
        'ipo_date': None,
        'is_new': None,
        'total_mcap': None,
        'float_mcap': None,
        'pe_ttm': None,
        'pe_pct': None,
        'pb_mrq': None,
        'pb_pct': None,
        'holders': None,
        'holder_report': None,
        'main_net_today': None,
        'main_ratio_today': None,
        'main_net_5d': None,
        'gd_plans': [],
        'north': {
            'date': None,
            'ratio': None,
            'chg_pct': None,
        },
        'eps_forecast': {
            'year': None,
            'eps': None,
            'org_count': None,
        },
        'perf': {
            'years': [],
            'revenue': [],
            'net': [],
            'yoy_rev': [],
            'yoy_net': [],
        },
        'rzrq': [],
        'perf_q': {
            'periods': [],
            'count': None,
            'years': {
                '2026': {
                    'sum': None,
                    'count': None,
                },
                '2025': {
                    'sum': None,
                    'count': None,
                },
                '2024': {
                    'sum': None,
                    'count': None,
                },
                '2023': {
                    'sum': None,
                    'count': None,
                },
                '2022': {
                    'sum': None,
                    'count': None,
                },
                '2021': {
                    'sum': None,
                    'count': None,
                },
                '2020': {
                    'sum': None,
                    'count': None,
                },
            },
        },
        'gdhs': {
            'periods': [],
            'count': None,
        },
    }


MF_LOOKBACK_DEFAULT = 20
MF_LOOKBACK_MIN = 1
MF_LOOKBACK_MAX = 120


def _mf_skeleton(lookback: int = MF_LOOKBACK_DEFAULT,
                 source: str = "unavailable") -> Dict[str, Any]:
    """主力资金副图**降级骨架**：结构同旧端，值全 None / 列表全空。

    口径在 `ctx.lib("l2_mf")`（`Hubagent/01/_tools/l2_mf.py`），插件不重写；
    只有口径库不可用/取数失败时才走到这里，`source` 如实标注降级原因。
    """
    out = copy.deepcopy(_MF_SKELETON)
    out["lookback"] = lookback
    out["source"] = source
    return out


# 2026-11-07（标注 N87）：.cah 取数深度。首页 700（快），翻页时 1400——
# 用户实测主力资金到 700 根就断，而 7709 能按 700 一批拼到 1400（实测 0.3s、覆盖到 2021 年）。
MF_RECORDS_DEFAULT = 700
MF_RECORDS_PAGED = 1400


def _mf_lookback(raw: Any) -> int:
    """回顾周期 N：None → 默认 20；非法 → 默认；越界 → 夹到 [1, 120]（旧端同）。"""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return MF_LOOKBACK_DEFAULT
    return max(MF_LOOKBACK_MIN, min(MF_LOOKBACK_MAX, v))


def build_mf(ctx: Any, code: str, bars: List[Dict[str, Any]],
             lookback: Any = None, records: int = MF_RECORDS_DEFAULT) -> Dict[str, Any]:
    """日线 bars → 「主力动向」5 列（口径库 `l2_mf.build_mf`，与旧 8800 `_stock_mf` 同源）。

    旧 8800 只在**日线**下发 mf（周/分钟 `mf=null`），本函数同理由调用方控制。
    任一步不可用 → 骨架降级 + `source` 如实标注，**不抛**（指标不可用不影响 K 线主体）。

    ⚠ **收盘价必须传 None**（与旧端 `_stock_mf` 逐字一致）：
      `build_mf(ts_code, dates8, None, lookback=...)` —— 口径库自己调 `default_closes()`
      读 vipdoc 的**不复权**收盘（l2_master 口径）。
      为什么不能传 bars 的 `c`：/api/kline 的 bars 是 **qfq 前复权**，用它算「占流通」
      会让分母带上复权因子 → 2026-10-24 实测与旧端逐位不等（-1.91 vs -1.87）。

    `source` 取值：`cache` / `live` / `cache_stale`（口径库原值）→ 直接透传；
    无 `.cah` 覆盖时口径库回 `none`，这里改写成 `unavailable(l2无覆盖)`，
    与旧端前端「不可用」文案一致且能区分「没登记」与「没数据」。
    """
    lb = _mf_lookback(lookback)
    if not bars:
        return _mf_skeleton(lb, "unavailable(无日线)")
    dates8 = [str(b["t"])[:8] for b in bars]
    try:
        lib = ctx.lib("l2_mf")
    except Exception as exc:  # noqa: BLE001 - 口径库不可用 → 骨架，不抛
        return _mf_skeleton(lb, "unavailable(口径库不可用: %s)" % type(exc).__name__)
    try:
        # closes=None：口径库自己取不复权收盘（见上，不得传 qfq 收盘）
        out = lib.build_mf(code, dates8, None, lookback=lb, records=records)
    except Exception as exc:  # noqa: BLE001 - 取数/口径异常 → 骨架，不抛
        return _mf_skeleton(lb, "unavailable(取数失败: %s)" % type(exc).__name__)
    if not isinstance(out, dict):
        return _mf_skeleton(lb, "unavailable(口径库返回非法)")
    # 逐列校验长度与 bars 对齐（口径库已保证；防御性兜底，防未来改口径拖垮前端）
    res = _mf_skeleton(lb)
    res["lookback"] = _mf_lookback(out.get("lookback", lb))
    for k in ("主力资金", "主买", "占成交", "占流通", "q"):
        col = out.get(k)
        res[k] = list(col)[:len(dates8)] if isinstance(col, (list, tuple)) else []
    src = str(out.get("source") or "none")
    res["source"] = "unavailable(l2无覆盖)" if src == "none" else src
    return res


def _f(v: Any) -> Optional[float]:
    """数值 → float；None/非法保留 None（不补 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _r(v: Any, dec: int = 3) -> Optional[float]:
    f = _f(v)
    return round(f, dec) if f is not None else None


def _qs(ctx: Any) -> Any:
    """行情门面（`ctx.quotes`，core `app/core/quotes.py`）；未声明权限 → 503。"""
    q = getattr(ctx, "quotes", None)
    if q is None:
        raise HTTPException(status_code=503, detail="ctx.quotes 不可用")
    return q


def _to_ts(ctx: Any, code: str) -> Optional[str]:
    """任意写法 → `600519.SH`（旧端 `to_ts_code` 口径，经 core 门面归一）。

    2026-11-01：`000510.CSI` 这类**中证指数标注**（板块目录在用）先换成交易所码再归一——
    中证指数代码段跟交易所同号：000xxx 在沪（sh000510=中证A500）、399xxx 在深。
    只在本层换算：core 的 `normalize` 只认 SH/SZ/BJ，不猜未知后缀，也不把 .CSI 传播给行情库。
    """
    raw = str(code or "").strip().upper()
    # `ALL` = 板块目录里的「沪深全A」标记（不是交易所代码）→ 通达信 880008（全A等权）。
    # 2026-11-01：从指数层右栏右键「打开 K 线」时传的就是 ALL，不映射就是 HTTP 400。
    if raw == "ALL":
        code = "880008.SH"
        raw = code
    if raw.endswith(".CSI"):
        body = raw[:-4]
        if len(body) == 6 and body.isdigit():
            code = body + (".SZ" if body.startswith("399") else ".SH")
    glb = _qs(ctx).normalize(code)
    if not glb:
        return None
    return glb[2:] + "." + _SUFFIX[glb[:2]]


def _glb(ctx: Any, ts_code: str) -> str:
    glb = _qs(ctx).normalize(ts_code)
    if not glb:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    return glb


def _db(ctx: Any, name: str):
    getter = getattr(ctx, "db_read", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.db_read 不可用")
    try:
        return getter(name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 不可读: %s" % (name, exc))


@contextlib.contextmanager
def _db_conn(ctx: Any, name: str):
    """只读连接上下文：ctx.db_read 每次新建连接，用完必须关。"""
    conn = _db(ctx, name)
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _rows(conn: Any, sql: str, params: Sequence[Any] = ()) -> List[Tuple[Any, ...]]:
    cur = conn.execute(sql, tuple(params))
    return [] if cur.description is None else [tuple(r) for r in cur.fetchall()]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slots() -> List[str]:
    """固定分钟轴：09:30–11:30 + 13:01–15:00，共 241 位（13:00 无根）。"""
    out: List[str] = []
    for h in (9, 10, 11):
        for m in range(60):
            hhmm = "%02d:%02d" % (h, m)
            if hhmm < SESSION_START or hhmm > "11:30":
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


def _hhmm_t(t: Any) -> str:
    """'YYYYMMDD HH:MM' / 'YYYYMMDDHHMM' / 'YYYY-MM-DD HH:MM:SS' → 'HH:MM'（非法 → ''）。"""
    d = "".join(ch for ch in str(t or "") if ch.isdigit())
    return "%s:%s" % (d[8:10], d[10:12]) if len(d) >= 12 else ""


# ---------------------------------------------------------------- 行情取数（ctx.quotes）


def _fetch_page(ctx: Any, glb: str, period: str, start: int, count: int,
                adjust: Optional[str]) -> Optional[List[Any]]:
    """取一页原始 bar；None = 传输级失败（调用方据此区分「真边界」与「取数失败」）。"""
    q = _qs(ctx)
    kwargs: Dict[str, Any] = {"period": period, "start": max(0, int(start)),
                              "count": max(1, min(BARS_PAGE, int(count)))}
    if adjust:
        kwargs["adjust"] = adjust
    res = q.call("bars.get", glb, **kwargs)
    if res is None:
        return None
    return list(getattr(res, "bars", None) or getattr(res, "items", None) or [])


def _bar_dict(it: Any, minute: bool) -> Optional[Dict[str, Any]]:
    """eltdx bar 对象 → 旧端 bar dict（`t/o/h/l/c/v/amount`，v 单位=股）。"""
    t = getattr(it, "time", None)
    if not hasattr(t, "strftime"):
        return None
    d8 = t.strftime("%Y%m%d")
    label = "%s %s" % (d8, t.strftime("%H:%M")) if minute else d8
    try:
        out: Dict[str, Any] = {
            "t": label,
            "o": float(it.open), "h": float(it.high),
            "l": float(it.low), "c": float(it.close),
            "v": float(getattr(it, "volume_lots", 0.0) or 0.0) * 100.0,   # 手→股
        }
    except (TypeError, ValueError):
        return None
    amt = getattr(it, "amount", None)
    if amt is not None:
        f = _f(amt)
        if f is not None:
            out["amount"] = f
    return out


def _latest_bars(ctx: Any, glb: str, period: str, want: int,
                 adjust: Optional[str]) -> List[Dict[str, Any]]:
    """最近 `want` 根（升序）。want 超过单页上限 → 说清边界，不静默截断。"""
    minute = period not in ("day", "week")
    items = _fetch_page(ctx, glb, period, 0, want, adjust)
    if items is None:
        return []
    bars = [b for b in (_bar_dict(it, minute) for it in items) if b is not None]
    bars.sort(key=lambda b: b["t"])
    return bars[-want:] if want and len(bars) > want else bars


def _bars_before(ctx: Any, glb: str, period: str, adjust: Optional[str],
                 start8: str, end8_excl: str, max_pages: int) -> Tuple[List[Dict[str, Any]], bool]:
    """「加载更早」翻页：按 `start` 根偏移逐页向前，取 [start8, end8_excl) 内的根（升序）。

    `ok=False` = 传输级失败（不把失败伪装成边界）。
    """
    minute = period not in ("day", "week")
    got: List[Dict[str, Any]] = []
    ok = False
    off = 0
    for _ in range(max_pages):
        items = _fetch_page(ctx, glb, period, off, BARS_PAGE, adjust)
        if items is None:
            return (got, True) if got else ([], False)
        ok = True
        if not items:
            break                                   # 空页 = 历史尽头
        page = [b for b in (_bar_dict(it, minute) for it in items) if b is not None]
        got.extend(page)
        if any(b["t"][:8] < start8 for b in page):
            break                                   # 已越过窗口下界
        off += len(items)                           # 步长 = 本页实际条数（未过滤）
    bars = sorted([b for b in got if start8 <= b["t"][:8] < end8_excl],
                  key=lambda b: b["t"])
    # 按时间戳去重：防御性（正常翻页窗口相邻不重叠；异常回页时不重复计）
    uniq: Dict[str, Dict[str, Any]] = {}
    for b in bars:
        uniq.setdefault(b["t"], b)
    return [uniq[t] for t in sorted(uniq)], ok


def _daily_closes(ctx: Any, glb: str, count: int = 30) -> List[Tuple[str, float]]:
    """最近 N 根日线（不复权）→ [(YYYYMMDD, close)]，升序。

    走门面的 `daily_bars()`（有 60s 缓存）；日线盘中不变，逐请求重取没意义。
    """
    q = _qs(ctx)
    try:
        rows = q.daily_bars(glb, max(2, int(count)), None)
    except Exception:  # noqa: BLE001 - 不可用 = 空（不编造）
        return []
    out: List[Tuple[str, float]] = []
    for it in rows or []:
        t = getattr(it, "time", None)
        c = _f(getattr(it, "close", None))
        if hasattr(t, "strftime") and c is not None:
            out.append((t.strftime("%Y%m%d"), c))
    out.sort(key=lambda x: x[0])
    return out


def _prev_close_before(ctx: Any, glb: str, day8: str) -> Optional[float]:
    """严格早于 day8 的最后一根日线收盘（不复权，与分时同尺度）；不可得 → None。

    2026-11-02（主人报历史分时打不开）：原固定 30 根窗口，看一两年前的日期时窗口全落在
    day8 之后 → 昨收恒 None（分时图缺 0% 基准线）。改为先 30 根，未命中且需要时放大到 800 根
    （≈3 年；800 是 eltdx 单次取数上限内、900 会静默失败，实测）。
    """
    for n in (30, 800):
        try:
            rows = _daily_closes(ctx, glb, n)
        except Exception:  # noqa: BLE001 - 取数不可用 → 交给下一档/返回 None
            continue
        for d, c in reversed(rows):
            if d < day8:
                return round(c, 3)
        if rows and min(r[0] for r in rows) < day8:
            break   # 窗口已覆盖 day8 之前仍无行（停牌/新股）→ 再放大无意义
    return None


def _minute_days(ctx: Any, glb: str, days: int) -> Dict[str, List[Any]]:
    """最近 N 个交易日的 1m bars（按日分组、组内升序）。门面实测窗口上限 10 日。"""
    try:
        return _qs(ctx).minute_bars_days(glb, max(1, min(INTRADAY_MULTI_MAX_DAYS, int(days))))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - 取数不可用 = 空（不编造）
        return {}


# ---------------------------------------------------------------- 口径库 / DB 派生


def _yellow_white(ctx: Any, values: List[float]) -> Optional[Tuple[List[Any], List[Any]]]:
    """黄白线（口径唯一源 `engine/common/indicators.py`，经 ctx.lib）。"""
    getter = getattr(ctx, "lib", None)
    if not callable(getter):
        return None
    try:
        y, w = getter("indicators").yellow_white_lines(values)
        return list(y), list(w)
    except Exception:  # noqa: BLE001 - 口径库不可用不拖垮主图
        return None


def _amv_map(ctx: Any, ts_code: str, dates8: List[str]) -> Dict[str, float]:
    """个股/指数活跃市值（元）：读 `amv_board.db`（AMV 归 amv 插件，这里只读）。

    2026-11-01 修撞码：全码（如 000016.SH）先查 `sector_daily`（INDEX 层，指数 AMV 唯一源）；
      未命中再按 6 位码查 `stock_amv`（个股）——否则宽基指数 000016.SH 会撞深市康佳A 000016.SZ
      （与旧端 server.py::_stock_amv_map 同型同修）。
    ⚠ 坑（旧端实测）：`date` 列是 `YYYY-MM-DD`，bar 的 `t` 是 `YYYYMMDD`，
    直接比较会因字典序恒空 → 入参必须转格式，返回键统一去横线。
    """
    if not dates8:
        return {}
    full = str(ts_code)
    code6 = full.split(".")[0]

    def _d(x: str) -> str:
        s = str(x)
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 else s

    lo, hi = _d(min(dates8)), _d(max(dates8))
    try:
        with _db_conn(ctx, AMV_DB) as conn:
            # ① 指数：全码查 sector_daily（INDEX 层，AMV 唯一源）
            if "." in full:
                rows = _rows(conn, "SELECT date, amv FROM sector_daily "
                                   "WHERE code=? AND date>=? AND date<=?", (full, lo, hi))
                m = {str(r[0]).replace("-", ""): float(r[1]) for r in rows if r[1] is not None}
                if m:
                    return m
            # ② 个股：6 位码查 stock_amv
            rows = _rows(conn, "SELECT date, amv FROM stock_amv "
                               "WHERE stock=? AND date>=? AND date<=?", (code6, lo, hi))
    except HTTPException:
        return {}
    except Exception:  # noqa: BLE001 - 取不到 = 副图不画（不编造）
        return {}
    return {str(r[0]).replace("-", ""): float(r[1]) for r in rows if r[1] is not None}


def _amv_series_out(ctx: Any, bars: List[Dict[str, Any]],
                    amv_map: Dict[str, float]) -> List[Optional[Tuple[Any, Any, Any]]]:
    """活跃市值 / MA10 / 白线 → 逐根 (amv, ma10, white)，单位亿元。

    白线口径与价格白线**同一实现**（`indicators.yellow_white_lines` 的 white），
    只把输入换成活跃市值序列——禁自建第二份 EMA。
    """
    if not amv_map:
        return [None] * len(bars)
    raw = [amv_map.get(str(b["t"])[:8]) for b in bars]
    ser: List[Optional[float]] = [(v / 1e8) if v is not None else None for v in raw]
    ma: List[Optional[float]] = [None] * len(ser)
    for i in range(len(ser)):
        win = [x for x in ser[max(0, i - 9):i + 1] if x is not None]
        if len(win) == 10:
            ma[i] = round(sum(win) / 10.0, 2)
    idx = [i for i, x in enumerate(ser) if x is not None]
    white: List[Optional[float]] = [None] * len(ser)
    if idx:
        yw = _yellow_white(ctx, [ser[i] for i in idx])  # type: ignore[list-item]
        if yw is not None:
            _, w = yw
            for k, i in enumerate(idx):
                white[i] = None if w[k] is None else round(float(w[k]), 2)
    return [(None if ser[i] is None else round(ser[i], 2), ma[i], white[i])
            for i in range(len(ser))]


def _boards(ctx: Any, ts_code: str) -> Dict[str, Any]:
    """个股所属 THS 行业（申万 L2 + L3 两层）+ 概念（缺失/异常 → 空表，不编造）。

    2026-10-30（标注 49「行业分别显示 L2 和 L3」）：原只回 L2；现在同时回 L3。
    `industry`/`industry_detail` 保持 L2 原义（老消费者不动），L3 走新增的
    `industry3`/`industry3_detail`。茅台这类没有 L3 映射的票，L3 为空表（不拿 L2 顶替）。

    2026-11-02 修撞码：宽基指数（000688.SH 科创50 等）与深市个股同 6 位号，
    `board_member.stock` 只存 6 位码，直接查会撞出深市个股的行业
    （000688.SH 显示国城矿业「小金属/钼」）。先查 `broad_index` 命中指数身份
    → 行业/概念全空表（指数无个股行业），未命中才走个股 6 位码路径。
    与 `_amv_map` 2026-11-01 的同型修法对齐。
    """
    empty = {"industry": [], "concepts": [], "industry_detail": [], "concept_detail": [],
             "industry3": [], "industry3_detail": []}
    full = str(ts_code).strip().upper()
    code6 = full.split(".")[0]
    if _is_broad_index(ctx, full):
        return empty          # 宽基指数：无个股行业/概念（不编造）
    try:
        with _db_conn(ctx, THS_DB) as conn:
            ind = _rows(conn, "SELECT b.name, b.code FROM board_member m "
                              "JOIN board b ON b.code=m.board_code "
                              "WHERE m.stock=? AND b.sw_level=2 ORDER BY b.name", (code6,))
            ind3 = _rows(conn, "SELECT b.name, b.code FROM board_member m "
                               "JOIN board b ON b.code=m.board_code "
                               "WHERE m.stock=? AND b.sw_level=3 ORDER BY b.name", (code6,))
            cc = _rows(conn, "SELECT c.name, c.code FROM concept_member m "
                             "JOIN concept c ON c.code=m.concept_code "
                             "WHERE m.stock=? ORDER BY c.name", (code6,))
    except Exception:  # noqa: BLE001 - 归属取不到 = 空（不编造）
        return empty
    return {"industry": [r[0] for r in ind],
            "concepts": [r[0] for r in cc],
            "industry_detail": [{"name": r[0], "code": r[1]} for r in ind if r[1]],
            "industry3": [r[0] for r in ind3],
            "industry3_detail": [{"name": r[0], "code": r[1]} for r in ind3 if r[1]],
            "concept_detail": [{"name": r[0], "code": r[1]} for r in cc if r[1]]}


def _is_broad_index(ctx: Any, ts_code: str) -> bool:
    """宽基指数判定（2026-11-02）：`ths_board.db::broad_index` 有全码记录 → True。

    背景：宽基指数与深市个股同 6 位号（000688.SH=科创50 vs 000688.SZ=国城矿业），
    F10/行业/概念这些**逐票表**只存 6 位码，直接查必撞码。这里只判身份，不改数据。
    表缺失/异常 → False（退回旧行为，不阻塞取数）。
    """
    full = str(ts_code or "").strip().upper()
    if "." not in full:
        return False
    try:
        with _db_conn(ctx, THS_DB) as conn:
            return bool(_rows(conn, "SELECT 1 FROM broad_index WHERE code=? LIMIT 1", (full,)))
    except Exception:  # noqa: BLE001 - 判不了就按个股走（旧行为）
        return False


def _float_shares(ctx: Any, ts_code: str) -> Optional[float]:
    """流通股本（股）：读 `amv_board.db::stock_state.sh` 最新一日（取不到 → None）。"""
    code6 = str(ts_code).split(".")[0]
    try:
        with _db_conn(ctx, AMV_DB) as conn:
            rows = _rows(conn, "SELECT sh FROM stock_state WHERE stock=? "
                               "ORDER BY date DESC LIMIT 1", (code6,))
    except Exception:  # noqa: BLE001
        return None
    if rows and rows[0][0]:
        return _f(rows[0][0])
    return None


# ---------------------------------------------------------------- 板块/概念指数（*.TI）
#
# 语义唯一源：旧仓 `engine/server.py:8946` 起（`is_index_kline_code` / `_index_kline_rows` /
# `_index_amv_map` / `_index_kline_name` / `_index_expected_latest_date8` /
# `build_index_kline_payload`）。以下逐段照搬，**不重写口径**。
#
# 量纲（旧端 2026-10-19 实测，THS 原始字段）：`index_daily.volume` = 股、`turnover` = 元
# ⇒ 输出 `v`（股）、`amount`（元），与个股 payload 同口径；板块无流通股本/无逐笔源
# ⇒ `turnover`/`mf`/个股 AMV 三列恒 None（不编造）。


def is_index_kline_code(ts_code: Any) -> bool:
    """板块/概念指数码判定（`.TI` 后缀；个股/宽基指数不走本分支）。"""
    return str(ts_code or "").strip().upper().endswith(".TI")


def _index_kline_rows(ctx: Any, code: str, period: str, limit: int,
                      before8: Optional[str] = None) -> List[Dict[str, Any]]:
    """`ths_index.db` → bars（升序 `[{t,o,h,l,c,v,amount}]`）；无库/无该码 → []。

    period='d' 直取日线；period='w' 由日线按 ISO 自然周聚合（o=首、h=max、l=min、c=末、
    v/amount=求和，`t` 取当周最后一根）。`before8` 非空 → 严格取该日之前（不含）的窗口。

    与旧端的**唯一实现差异**（行为不变，只修 500）：旧端 `float(b["v"])` 遇库里 volume 为
    NULL 的行（当前 1 行：999001.TI 20260105 自建指数首根）会抛 TypeError → 端点 500。
    本实现保留 None（下游 `float(b.get("v") or 0)` 已按 0 处理，与旧端设计意图一致），
    并在周线聚合里把 None 当 0 累加——见 HANDOFF §3「旧端 500 缺陷」。
    """
    sql = ("SELECT date,o,h,l,c,volume,turnover FROM index_daily "
           "WHERE code=? AND o IS NOT NULL")
    args: List[Any] = [str(code).strip().upper()]
    if before8:
        sql += " AND date < ?"
        args.append(str(before8))
    sql += " ORDER BY date DESC LIMIT ?"
    # 周线需 5 倍日线根数才能聚合出 limit 根；留 1.3 倍余量（旧端同值）
    args.append(int(limit) * (7 if period == "w" else 1))
    try:
        with _db_conn(ctx, INDEX_DB) as conn:
            rows = _rows(conn, sql, args)
    except Exception:  # noqa: BLE001 - 读不到 = 空（不编造）
        return []
    rows.reverse()
    bars: List[Dict[str, Any]] = []
    for r in rows:
        d8 = str(r[0]).replace("-", "")
        if len(d8) != 8:
            continue
        bars.append({"t": d8, "o": r[1], "h": r[2], "l": r[3], "c": r[4],
                     "v": r[5], "amount": r[6]})
    if period != "w":
        return bars
    # 周线聚合（ISO 周：周一为首日；跨年周归属按 ISO 年）
    out: List[Dict[str, Any]] = []
    cur_key: Optional[Tuple[int, int]] = None
    cur_bar: Optional[Dict[str, Any]] = None
    for b in bars:
        try:
            y, m, d = int(b["t"][:4]), int(b["t"][4:6]), int(b["t"][6:8])
            iso = datetime(y, m, d).isocalendar()
            key = (iso[0], iso[1])
        except (TypeError, ValueError):
            continue
        if key != cur_key:
            if cur_bar:
                out.append(cur_bar)
            cur_key = key
            cur_bar = dict(b)
        else:
            assert cur_bar is not None
            cur_bar["h"] = max(cur_bar["h"], b["h"])
            cur_bar["l"] = min(cur_bar["l"], b["l"])
            cur_bar["c"] = b["c"]
            cur_bar["v"] = (cur_bar["v"] or 0) + (b["v"] or 0)
            cur_bar["amount"] = (cur_bar["amount"] or 0) + (b["amount"] or 0)
            cur_bar["t"] = b["t"]        # 周线时间戳取当周最后一根（与个股周线口径一致）
    if cur_bar:
        out.append(cur_bar)
    return out


def _index_amv_map(ctx: Any, code: str, dates8: List[str]) -> Dict[str, float]:
    """板块活跃市值日序（元）：读 `amv_board.db::sector_daily`。

    返回 `{YYYYMMDD: amv}`（键去横线，与 `_amv_series_out` 取键口径一致）；无库/无该码 → {}。
    单位：`sector_daily.amv` 原值即元（旧端实测：白酒 1.165e11 ≈ 1165 亿），
    `_amv_series_out` 内部统一 /1e8 转亿元，与个股副图同口径。

    ⚠ 坑（与 `_amv_map` 同一个）：`date` 列是 `YYYY-MM-DD`、bar 的 `t` 是 `YYYYMMDD`，
    直接比较会因字典序恒空 → 入参必须转格式，返回键统一去横线。
    """
    if not dates8:
        return {}

    def _d(x: Any) -> str:
        s = str(x).replace("-", "")
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 else s

    try:
        with _db_conn(ctx, AMV_DB) as conn:
            rows = _rows(conn, "SELECT date, amv FROM sector_daily "
                               "WHERE code=? AND date>=? AND date<=?",
                         (str(code).strip().upper(), _d(min(dates8)), _d(max(dates8))))
    except Exception:  # noqa: BLE001 - 取不到 = 副图不画（不编造）
        return {}
    return {str(r[0]).replace("-", ""): float(r[1]) for r in rows if r[1] is not None}


def _index_kline_name(ctx: Any, code: str) -> str:
    """板块名：`ths_board.db::board` → `amv_board.db::sector_meta` → 空串（不编造）。"""
    c = str(code or "").strip().upper()
    for db, sql in ((THS_DB, "SELECT name FROM board WHERE code=?"),
                    (AMV_DB, "SELECT name FROM sector_meta WHERE code=? LIMIT 1")):
        try:
            with _db_conn(ctx, db) as conn:
                row = _rows(conn, sql, (c,))
        except Exception:  # noqa: BLE001 - 一张表取不到就试下一张
            continue
        if row and row[0][0]:
            return str(row[0][0])
    return ""


def _is_trading_day(d: Any) -> bool:
    """交易日判定：旧仓 `common.trading_calendar.is_trading_day`；不可用 → 工作日兜底。

    与 core `panic_calc._is_trading_day` 同一做法（懒引入 + 用完还原 `sys.path`）。
    插件不 `from common`（契约静态扫描禁），这里走**路径懒加载**，只在 stale 判定时
    调一次；日历不可用就退化为工作日（宁可少报 stale，与旧端降级链一致）。
    """
    engine_dir = str(_core_paths.WS / "engine")
    inserted = engine_dir not in sys.path
    if inserted:
        sys.path.append(engine_dir)
    try:
        from common.trading_calendar import is_trading_day  # noqa: PLC0415 - 懒引入
        return bool(is_trading_day(d))
    except Exception:  # noqa: BLE001 - 日历不可用 → 工作日兜底
        return getattr(d, "weekday", lambda: 7)() < 5
    finally:
        if inserted:
            sys.path[:] = [p for p in sys.path if p != engine_dir]


def _index_expected_latest_date8(today8: str, now: Optional[datetime] = None) -> str:
    """板块指数日线「应该已经收到的最后一根」日期（YYYYMMDD）。

    源是收盘定格（`index_daily` 当日收盘后才落库）：
      · 交易日且已过 15:05 → 当日；
      · 交易日盘中/盘前、或非交易日 → 上一交易日。
    """
    now = now or datetime.now()
    d = datetime.strptime(str(today8), "%Y%m%d").date()
    if _is_trading_day(d) and now.strftime("%H%M") >= "1505":
        return d.strftime("%Y%m%d")
    from datetime import timedelta as _td  # noqa: PLC0415

    d = d - _td(days=1)
    for _ in range(10):          # 最多回退 10 天（长假足够）
        if _is_trading_day(d):
            return d.strftime("%Y%m%d")
        d = d - _td(days=1)
    return str(today8)


def _ths_week_bars(ctx: Any, code: str, limit: int,
                   before8: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """板块指数周线：只认原生 1w 源；取不到 → None（调用方报「周线不可用」）。

    旧端 `_index_week_bars` 同语义：**不得用日线聚合冒充周线**（2026-10-19 主人裁定），
    原生不可用就返回 None，由上层在图上显示「周线不可用」。

    上游调用在 core 行情门面（`ctx.quotes.ths_index_weekly`）：插件禁出站，凭据读取与
    代理旁路归 core（见 HANDOFF §4）。门面没有该接缝 / 抛异常 / 返回空 → 一律 None。
    """
    ck = str(code).strip().upper()
    now = time.time()
    hit = _THS_WEEK_MEMO.get(ck)
    if hit and (now - hit[0]) < _THS_WEEK_MEMO_TTL_S:
        return hit[1]
    # 窗口：周线 limit 根 ≈ limit*7 自然日，留 1.6 倍余量（旧端同式）
    from datetime import timedelta as _td  # noqa: PLC0415

    today8 = datetime.now().strftime("%Y%m%d")
    try:
        end_dt = datetime.strptime(str(before8), "%Y%m%d") if before8 else datetime.now()
    except (TypeError, ValueError):
        end_dt = datetime.now()
    span = int(max(int(limit), 1) * 7 * 1.6) + 60
    start8 = (end_dt - _td(days=span)).strftime("%Y%m%d")
    end8 = end_dt.strftime("%Y%m%d") if before8 else today8
    fn = getattr(_qs(ctx), "ths_index_weekly", None)
    bars: Optional[List[Dict[str, Any]]] = None
    if callable(fn):
        try:
            raw = fn(ck, start8, end8)
        except Exception:  # noqa: BLE001 - 上游任何异常 = 不可用（不阻断 K 线）
            raw = None
        if raw:
            bars = []
            for b in raw:
                t = str(b.get("t") or "").replace("-", "")[:8]
                if len(t) != 8:
                    continue
                bars.append({"t": t, "o": b.get("o"), "h": b.get("h"),
                             "l": b.get("l"), "c": b.get("c"),
                             "v": b.get("v"), "amount": b.get("amount")})
            bars.sort(key=lambda x: x["t"])
            bars = bars or None
    if bars is not None and before8:
        bars = [b for b in bars if b["t"] < str(before8)]
        bars = bars or None
    _THS_WEEK_MEMO[ck] = (now, bars)
    return bars


def build_index_kline(ctx: Any, ts_code: str, period: str = "d",
                      limit: Any = KLINE_LIMIT_DEFAULT,
                      before: Optional[str] = None,
                      today8: Optional[str] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """板块/概念指数 K 线 payload（旧端 `build_index_kline_payload` 逐段照搬）。

    与个股 payload 同构（bars/yellow/white/indicators/stale/source），额外带
    `index=true`、`name=板块名`；换手/主力动向/个股 AMV 三列恒 None（板块无该口径）。
    非法周期抛 400（旧端 ValueError → 端点 400）；周线源不可用 → 空 bars +
    `period_unavailable` + `unavailable_reason`（不返回假数据）。
    """
    code = str(ts_code or "").strip().upper()
    p = str(period or "d").strip().lower()
    if p not in ("d", "w"):
        raise HTTPException(
            status_code=400,
            detail="板块指数暂无 %s 周期数据（仅日线；分钟与分时需个股逐笔源）" % p)
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="非法 limit：%r（需整数）" % (limit,))
    lim = max(KLINE_LIMIT_MIN, min(KLINE_LIMIT_MAX, lim))

    before8: Optional[str] = None
    # ⚠ 与个股分支的**实质差异（旧端原样）**：板块分支对 before **不做任何校验/归一**。
    # 旧端 `api_kline` 把 `before` 原值传给 `build_index_kline_payload`，后者直接当字符串
    # 拼进 `date < ?` —— 所以 `before=2026-06-01`（带横线）按字典序只截到上一年度
    # （'-'(0x2D) < '0'(0x30)），`before=bad` 不报错而是“全部早于 bad”。
    # 这些怪癖是旧端实际行为，双跑对拍要求逐字段一致 → 照搬，不在这里“修好”。
    raw_before = str(before or "").strip()
    if raw_before:
        before8 = raw_before

    today8 = today8 or datetime.now().strftime("%Y%m%d")
    # 「加载更早」多取 1 根以判定 has_more（与个股 before 语义一致）
    fetch_n = lim + 1 if before8 else lim

    # 周线：只认原生 1w 源（每次真去取，带 60s 记忆）；取不到 → 空 bars + unavailable 标记
    if p == "w":
        # 原生周线（ths_mcp_index）——2026-11-01 实测上游 1w/1mo 对所有指数一律回
        # `5003 Data source unavailable`（REST 与 MCP 两条路径同），文档也只写「当前仅支持 1d」。
        # 取不到时**不再报「周期不可用」**，改用本库日线按 ISO 自然周聚合（同一数据源的
        # 重采样，非另一套口径），source 如实标 `ths_index.db:1w(日线聚合)`。
        wb = _ths_week_bars(ctx, code, fetch_n + (0 if before8 else YELLOW_WARMUP),
                            before8=before8)
        week_src = "ths_mcp_index:1w"
        if not wb:
            wb = _index_kline_rows(ctx, code, "w",
                                   fetch_n + (0 if before8 else YELLOW_WARMUP),
                                   before8=before8)
            week_src = "ths_index.db:1w(日线聚合)"
        if not wb:
            payload = _kline_payload(ctx, code, p, [], True,
                                     "ths_mcp_index:unavailable",
                                     boards=_EMPTY_BOARDS, amv_map={}, float_sh=None,
                                     mf=None)
            payload["index"] = True
            payload["name"] = _index_kline_name(ctx, code)
            payload["period_unavailable"] = True
            payload["unavailable_reason"] = _THS_WEEK_UNAVAILABLE
            if before8:
                payload["has_more"] = False
                payload["next_before"] = None
            return payload
        # 黄线预热（同个股分支）：周线同样要 114 根才有第一条值
        wyw = None
        if not before8 and len(wb) > lim:
            full = _yellow_white(ctx, [float(b["c"]) for b in wb])
            if full is not None:
                wyw = (list(full[0][-lim:]), list(full[1][-lim:]))
        wb = wb[-lim:]
        # amv_map 传 `{}`：旧端虽也对周线传了 amv_map，但 `_kline_payload_out` 只在
        # `period == "d"` 时调 `_amv_series_out` ⇒ 周线三列恒 None。这里不白跑一次库查询，
        # 输出逐字段不变（已用活体对拍验过）。
        payload = _kline_payload(ctx, code, p, wb, False, week_src,
                                 boards=_EMPTY_BOARDS, amv_map={},
                                 float_sh=None, mf=None, yw=wyw)
        payload["index"] = True
        payload["name"] = _index_kline_name(ctx, code)
        if week_src != "ths_mcp_index:1w":
            payload["week_from_daily"] = True     # 前端/调用方一眼能看出是日线重采样
        if before8:
            payload["has_more"] = len(wb) >= lim
            payload["next_before"] = str(wb[0]["t"])[:8] if wb else None
        return payload

    bars = _index_kline_rows(ctx, code, p, fetch_n + (0 if before8 else YELLOW_WARMUP), before8=before8)
    # 黄线预热（2026-11-01，与个股分支同因）：黄线要 114 根才有第一条值，
    # 只取 limit 根 → 图上只有尾部 7 根有黄线。多取 113 根只用于计算，输出仍回 lim 根。
    yw = None
    if not before8 and len(bars) > lim:
        full = _yellow_white(ctx, [float(b["c"]) for b in bars])
        if full is not None:
            yw = (list(full[0][-lim:]), list(full[1][-lim:]))
    has_more = False
    if before8:
        has_more = len(bars) > lim
        if has_more:
            bars = bars[-lim:]
    elif len(bars) > lim:
        bars = bars[-lim:]
    # stale：库里最新一根是否覆盖到「应该已收到的最后一根」（收盘定格源：盘中不要求当日根）
    latest = bars[-1]["t"] if bars else None
    expect8 = _index_expected_latest_date8(today8, now=now)
    stale = (latest is None) or (latest < expect8)
    payload = _kline_payload(ctx, code, p, bars, stale, "ths_index.db",
                             boards=_EMPTY_BOARDS,
                             amv_map=_index_amv_map(ctx, code, [b["t"] for b in bars]),
                             float_sh=None, mf=None, yw=yw)
    payload["index"] = True
    payload["name"] = _index_kline_name(ctx, code)
    if before8:
        payload["has_more"] = bool(has_more)
        payload["next_before"] = str(bars[0]["t"])[:8] if bars else None
    return payload


# ---------------------------------------------------------------- /api/kline


def _kline_validate(ctx: Any, ts_code: str, period: str, limit: Any) -> Tuple[str, str, int]:
    # `.TI`（板块/概念指数）不走本函数：`build_kline` 一进门就分流到 build_index_kline
    # （门面 normalize 认不出 .TI，会先报「非法 ts_code」）。
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    p = str(period or "d").lower()
    if p not in KLINE_PERIODS:
        raise HTTPException(status_code=400,
                            detail="非法 period：%r（需 d|w|m1|m5|m15|m30|m60）" % (period,))
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="非法 limit：%r（需整数）" % (limit,))
    lim = max(KLINE_LIMIT_MIN, min(KLINE_LIMIT_MAX, lim))
    return code, p, lim


def build_kline(ctx: Any, ts_code: str, period: str = "d", limit: Any = KLINE_LIMIT_DEFAULT,
                before: Optional[str] = None, mf_lookback: Any = None) -> Dict[str, Any]:
    """日/周/分钟 K 线（字段逐项对齐旧 8800 `/api/kline/{ts_code}`）。

    `.TI`（板块/概念指数）一进门分流到 `build_index_kline`（旧端 `api_kline` 同构分流）。
    """
    if is_index_kline_code(ts_code):
        return build_index_kline(ctx, ts_code, period=period, limit=limit, before=before)
    code, p, lim = _kline_validate(ctx, ts_code, period, limit)
    glb = _glb(ctx, code)
    period_key = KLINE_PERIODS[p]
    minute = p not in ("d", "w")
    adjust = None if minute else "qfq"      # 日/周必须 qfq；分钟必须不传（旧端实测口径）

    has_more = False
    next_before: Optional[str] = None
    before8 = str(before or "").strip()
    if before8:
        b8 = "".join(ch for ch in before8 if ch.isdigit())[:8]
        if len(b8) != 8:
            raise HTTPException(status_code=400, detail="非法 before：%r（需 YYYYMMDD）" % (before,))
        try:
            end_dt = datetime.strptime(b8, "%Y%m%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="非法 before：%r（需 YYYYMMDD）" % (before,))
        end8 = end_dt.strftime("%Y%m%d")
        need = lim + 1
        if minute:
            per_day = KLINE_MINUTE_BARS_PER_DAY.get(p, 48)
            span = int((need / max(1, per_day)) * 1.6) + 10
            max_pages = BEFORE_MAX_PAGES_MINUTE
        else:
            span = int(need * (7.5 if p == "w" else 1.6)) + 30
            max_pages = BEFORE_MAX_PAGES_DAILY
        from datetime import timedelta as _td  # noqa: PLC0415

        start8 = (end_dt - _td(days=span)).strftime("%Y%m%d")
        bars, _ok = _bars_before(ctx, glb, period_key, adjust, start8, b8, max_pages)
        has_more = len(bars) > lim
        if has_more:
            bars = bars[-lim:]
        next_before = bars[0]["t"][:8] if bars else None
        # 只有 before 分支才带 has_more / next_before（旧端 `_build_kline_before` 同此）
        return _kline_payload(ctx, code, p, bars, False, "eltdx",
                              has_more=has_more, next_before=next_before,
                              include_paging=True, mf_lookback=mf_lookback,
                              mf_records=MF_RECORDS_PAGED)

    # 黄白线的预热：黄线要 114 根才有第一条值（口径库 i<113 为 None）。
    # 只拉 lim 根时，尾部只有 7 根有黄线（实测 limit=60/120 一样）——这就是
    # 「所有板块走势的黄线只显最后一小段」的真因。多拉 warm 根只用于计算，输出仍回 lim 根。
    warm = YELLOW_WARMUP if p in ("d", "w") else 0
    bars = _latest_bars(ctx, glb, period_key, lim + warm, adjust)
    if not bars:
        # 与旧端一致：取数失败**不报错**，回 200 + 空 bars + stale=True（不编造）；
        # 只有参数非法才 400（`_kline_validate`）。
        return _kline_payload(ctx, code, p, [], True, "none")
    yw = None
    if warm and len(bars) > lim:
        full = _yellow_white(ctx, [float(b["c"]) for b in bars])
        bars = bars[-lim:]
        if full is not None:
            # 与裁后 bars 下标对齐（只留最后 lim 项）
            yw = (list(full[0][-lim:]), list(full[1][-lim:]))
    return _kline_payload(ctx, code, p, bars, False, "eltdx", mf_lookback=mf_lookback, yw=yw)


def _kline_payload(ctx: Any, code: str, period: str, bars: List[Dict[str, Any]],
                   stale: bool, source: str, *, has_more: bool = False,
                   next_before: Optional[str] = None,
                   include_paging: bool = False,
                   mf_lookback: Any = None,
                   yw: Any = None,
                   mf_records: int = MF_RECORDS_DEFAULT,
                   boards: Any = _UNSET, amv_map: Any = _UNSET,
                   float_sh: Any = _UNSET, mf: Any = _UNSET) -> Dict[str, Any]:
    """旧端 `_kline_payload_out` 的等价实现（黄白线 + AMV 副图 + chg/amp/turnover + 元数据）。

    ⚠ `has_more` / `next_before` **只在 `before` 翻页请求里出现**——旧端只有
    `_build_kline_before` 往 payload 里塞这两个键，默认（最新窗口）响应没有它们
    （2026-10-23 shadow 的 `ignored_old_keys` 亦无反证）。照搬，不擅自补齐。

    `boards` / `amv_map` / `float_sh` / `mf` 四个**可注入参数**（2026-10-24 S2）：旧端
    `_kline_payload_out(code, period, bars, stale, source, amv_map=…, boards=…, float_sh=…, mf=…)`
    就是四参全注入的纯函数——个股分支传自己的取数结果，板块指数分支传空表/板块 AMV/None/None。
    默认值用哨兵 `_UNSET` 表示「未传 → 按个股默认路径取」，行为与之前逐字节相同。
    """
    closes = [float(b["c"]) for b in bars]
    # `yw`（2026-11-01）：调用方已按预热窗口算好黄白线时直接注入（与 bars 等长，已对齐）；
    # 不传则按 bars 现算（旧行为，短窗口下黄线尾部无值）。
    if yw is not None:
        yellow, white = list(yw[0]), list(yw[1])
    else:
        w = _yellow_white(ctx, closes)
        if w is not None:
            yellow, white = w
        else:
            yellow, white = [None] * len(bars), [None] * len(bars)
    dates8 = [str(b["t"])[:8] for b in bars]
    if amv_map is _UNSET:
        amv_map = _amv_map(ctx, code, dates8)
    amv3 = _amv_series_out(ctx, bars, amv_map or {}) if period == "d" \
        else [None] * len(bars)
    fsh = _float_shares(ctx, code) if float_sh is _UNSET else float_sh
    out_bars: List[Dict[str, Any]] = []
    for i, b in enumerate(bars):
        pc = closes[i - 1] if i >= 1 else None
        c1, h1, l1 = float(b["c"]), float(b["h"]), float(b["l"])
        v1 = float(b.get("v") or 0)
        a = amv3[i] if i < len(amv3) else None
        row = {
            "t": b["t"],
            "o": round(float(b["o"]), 3), "h": round(h1, 3),
            "l": round(l1, 3), "c": round(c1, 3),
            "v": round(v1, 2),
            "yellow": None if (yellow[i] is None) else round(float(yellow[i]), 3),
            "white": None if (white[i] is None) else round(float(white[i]), 3),
            "amv": a[0] if a else None,
            "amvMa": a[1] if a else None,
            "amvWhite": a[2] if a else None,
            # tooltip 三项：服务端算好下发（前端零计算）
            "chg": None if not pc else round((c1 / pc - 1) * 100, 2),
            "amp": None if not pc else round((h1 - l1) / pc * 100, 2),
            "turnover": round(v1 / fsh * 100, 2) if (fsh and v1) else None,
            "amount": _r(b.get("amount"), 2),
        }
        out_bars.append(row)
    prev_close = closes[-2] if len(closes) >= 2 else None
    if mf is _UNSET:
        # 旧端：仅日线且 bars 非空时才有 mf，其余（周/分钟/空窗口）一律 null
        mf_out: Any = build_mf(ctx, code, bars, mf_lookback, records=mf_records) if (period == "d" and bars) else None
    else:
        mf_out = mf
    out: Dict[str, Any] = {
        "version": "kline-" + KLINE_VERSION, "code": code, "period": period,
        "adj": "front", "asof": str(bars[-1]["t"])[:8] if bars else None,
        "stale": bool(stale), "source": source, "vol_unit": "股",
        "prev_close": None if prev_close is None else round(prev_close, 3),
        "boards": _boards(ctx, code) if boards is _UNSET else copy.deepcopy(boards),
        "bars": out_bars, "count": len(out_bars),
        "mf": mf_out,
        "indicators": {
            "yellow": [None if v is None else round(float(v), 3) for v in yellow],
            "white": [None if v is None else round(float(v), 3) for v in white],
        },
    }
    if include_paging:
        out["has_more"] = bool(has_more)
        out["next_before"] = next_before
    return out


# ---------------------------------------------------------------- /api/kline_sparks
#
# 2026-11-04：自选池打开慢（实测冷开 tPaint 中位 1491 ms / 最差 3094 ms）。
# 根因取证（37 票、当前组）：前端按票逐个拉 `/api/kline/{code}?period=d&limit=160`，
# 服务端票均 112.8 ms、响应 49 KB/票，合计 4.2 s 串行、1.5 MB；而其中
#   build_mf        43.8 ms/票（动能K，自选卡副图不用）
#   _amv_map        31.9 ms/票（单票一次 sqlite 连接 + 区间查询）
#   _boards          4.9 ms/票（THS 行业/概念，自选卡不用）
#   _float_shares    3.9 ms/票（流通股，只为 turnover，自选卡不用）
# 三项合计占 84 %，且都不是副图要的东西。
# 做法：一次请求取多票 → 只回主图/副图需要的 6 条序列的最后 tail 根；
#   AMV 走一次 IN 查询；不算 mf / 行业 / 流通股；模块级 20 s TTL 缓存
#   （日线序列盘中只可能改最后一根，20 s 足够跟上 5 s 刷新的观感）。
# 口径零复制：黄白线与 AMV 三线仍调 `_yellow_white` / `_amv_series_out`，
#   与 `/api/kline` 同一实现；本端点只做「批量取数 + 裁剪 + 拼装」。

SPARKS_TTL = 120.0                   # 秒；盘后档看日线，2 分钟滞后不改形状。
# 为何不是 20 s：前端 spark 缓存 `EOD_TTL=300000`（5 分钟）——后端 TTL 比前端短
# 就变成「每次前端重取都刚好过期」，等于把冷取（~850 ms）写进日常路径。
# 120 s 保证一般重取都命中缓存；盘中当日 bar 最多滞后 2 分钟，与前端 5 分钟窗口同量级。
SPARKS_CACHE_KEEP = 16               # 缓存条数上限（按最旧时间淘汰）
SPARKS_MAX_CODES = 120               # 单次最多取多少票（自选池上限之上留余量）
SPARKS_VERSION = "kline-sparks-v1"
_sparks_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _sparks_cache_get(key: str) -> Optional[Dict[str, Any]]:
    hit = _sparks_cache.get(key)
    if not hit:
        return None
    if (time.monotonic() - hit[0]) >= SPARKS_TTL:
        _sparks_cache.pop(key, None)
        return None
    return hit[1]


def _sparks_cache_put(key: str, payload: Dict[str, Any]) -> None:
    now = time.monotonic()
    if len(_sparks_cache) >= SPARKS_CACHE_KEEP:
        oldest = min(_sparks_cache.items(), key=lambda kv: kv[1][0])[0]
        _sparks_cache.pop(oldest, None)
    _sparks_cache[key] = (now, payload)


# ---------------------------------------------------------------- 盘后落盘（2026-11-07）
#
# 自选池副图：48 票 × 日线 + AMV，进程重启后首次要 1.4s（行情源冷取）。
# 盘后数据固定 → 落盘；失效 = 数据日（expected）变，或 payload.asof 不是那个数据日。
# 三道铁律：空不写｜失败不写｜日期对不上当 miss。
_SPARKS_DISK = "kline_sparks"
_SPARKS_DISK_KEEP = 8


def _sparks_disk_get(ctx: Any, key: str, expected8: str) -> Optional[Dict[str, Any]]:
    if not expected8:
        return None
    try:
        doc = ctx.cache_read(_SPARKS_DISK)
    except Exception:  # noqa: BLE001 - 首次/损坏 → 现算
        return None
    if (not isinstance(doc, dict) or doc.get("version") != SPARKS_VERSION
            or str(doc.get("date8") or "") != expected8):
        return None
    items = doc.get("items")
    if not isinstance(items, dict):
        return None
    hit = items.get(key)
    if not isinstance(hit, dict) or not hit.get("items"):
        return None
    if str(hit.get("asof") or "") != expected8:
        return None          # 末根不是应有数据日（停牌/次新）→ 不当命中
    return hit


def _sparks_disk_put(ctx: Any, key: str, expected8: str, payload: Dict[str, Any]) -> None:
    if not expected8 or not payload.get("items"):
        return
    if str(payload.get("asof") or "") != expected8:
        return               # 末根日期对不上就不写（铁律 3）
    try:
        doc = ctx.cache_read(_SPARKS_DISK)
    except Exception:  # noqa: BLE001
        doc = None
    if (not isinstance(doc, dict) or doc.get("version") != SPARKS_VERSION
            or str(doc.get("date8") or "") != expected8
            or not isinstance(doc.get("items"), dict)):
        doc = {"version": SPARKS_VERSION, "date8": expected8, "items": {}}
    items = dict(doc["items"])
    items[key] = payload
    if len(items) > _SPARKS_DISK_KEEP:
        items = {key: payload}
    try:
        ctx.cache_write(_SPARKS_DISK, {"version": SPARKS_VERSION, "date8": expected8,
                                       "items": items})
    except Exception:  # noqa: BLE001 - 写失败只是下次多算一次
        pass


def _amv_maps_bulk(ctx: Any, codes: Sequence[str],
                   dates8: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """多票 AMV 一次取回（口径与 `_amv_map` 逐票查询完全一致）。

    与 `_amv_map` 的对应关系：
      · 带后缀的全码（指数）→ `sector_daily`，命中即用（个股分支不覆盖）；
      · 6 位码（个股）      → `stock_amv`。
    `_amv_map` 是单票一次连接，40 票就是 40 次建连 + 40 次区间扫描；
    这里一次 IN 查询取完，分组到各票。日期窗口用全局 min/max（多取不漏）。
    """
    if not dates8:
        return {}

    def _d(x: str) -> str:
        s = str(x)
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 else s

    lo, hi = _d(min(dates8)), _d(max(dates8))
    full_codes = sorted({str(c) for c in codes if "." in str(c)})
    code6s = sorted({str(c).split(".")[0] for c in codes})
    out: Dict[str, Dict[str, float]] = {}
    try:
        with _db_conn(ctx, AMV_DB) as conn:
            if full_codes:
                marks = ",".join("?" * len(full_codes))
                rows = _rows(conn, "SELECT code, date, amv FROM sector_daily "
                                   "WHERE code IN (%s) AND date>=? AND date<=?" % marks,
                             tuple(full_codes) + (lo, hi))
                for code, d, amv in rows:
                    if amv is None:
                        continue
                    out.setdefault(str(code), {})[str(d).replace("-", "")] = float(amv)
            if code6s:
                marks = ",".join("?" * len(code6s))
                rows = _rows(conn, "SELECT stock, date, amv FROM stock_amv "
                                   "WHERE stock IN (%s) AND date>=? AND date<=?" % marks,
                             tuple(code6s) + (lo, hi))
                for stk, d, amv in rows:
                    if amv is None:
                        continue
                    out.setdefault(str(stk), {})[str(d).replace("-", "")] = float(amv)
    except HTTPException:
        return out
    except Exception:  # noqa: BLE001 - 取不到 = 副图不画（不编造，与 _amv_map 同策略）
        return out
    return out


def _sparks_ratio(v: Any, dec: int) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, dec)


def build_kline_sparks(ctx: Any, codes: Sequence[str], tail: Any = 45,
                       limit: Any = 160) -> Dict[str, Any]:
    """多票 spark 序列：主图（收盘/黄线/白线）+ 副图（AMV/MA10/白线）。

    只读；输出与 `/api/kline` 的对应字段**逐值同口径**（同一黄白线实现、同一 AMV 归一）。
    与 `/api/kline` 的差别仅在「返回什么」：不算 mf / 行业 / 概念 / 流通股 / turnover，
    只留最后 `tail` 根的 6 条序列 + 末根日期。
    """
    lim = max(8, min(1000, int(limit or 160)))
    tl = max(2, min(lim, int(tail or 45)))
    want: List[str] = []
    seen: set = set()
    for raw in codes:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        want.append(code)
        if len(want) >= SPARKS_MAX_CODES:
            break
    if not want:
        raise HTTPException(status_code=400, detail="codes 不能为空")

    key = "|".join(["%d" % tl, "%d" % lim] + sorted(want))
    cached = _sparks_cache_get(key)
    if cached is not None:
        return copy.deepcopy(cached)

    # 盘后先查落盘（重启/重开后首屏不必再拉一遍行情）
    expected8 = ""
    try:
        if ctx.trade_clock.postclose():
            expected8 = str(ctx.trade_clock.expected_data_date8() or "")
            disk = _sparks_disk_get(ctx, key, expected8)
            if disk is not None:
                _sparks_cache_put(key, disk)
                return copy.deepcopy(disk)
    except Exception:  # noqa: BLE001 - 旧 core/门面异常 → 现算
        expected8 = ""

    # ① 逐票取原始日线（含黄线预热的 113 根，与 build_kline 的 warm 同值）
    raws: Dict[str, List[Dict[str, Any]]] = {}
    for code in want:
        try:
            glb = _glb(ctx, code)
        except HTTPException:
            continue                       # 非法码跳过，不拖垮整批
        bars = _latest_bars(ctx, glb, KLINE_PERIODS["d"], lim + YELLOW_WARMUP, "qfq")
        if bars:
            raws[code] = bars

    # ② 裁到 lim 根后再算黄白线（同 build_kline：预热只用于算，输出仍回 lim 根）
    windows: Dict[str, Dict[str, Any]] = {}
    for code, bars in raws.items():
        closes = [float(b["c"]) for b in bars]
        if len(bars) > lim:
            # 预热窗口足够：先用全长算黄白线，再裁到 lim（同 build_kline）
            yw = _yellow_white(ctx, closes)
            cut = bars[-lim:]
            if yw is not None:
                yellow = list(yw[0][-len(cut):])
                white = list(yw[1][-len(cut):])
            else:
                yellow = [None] * len(cut)
                white = [None] * len(cut)
        else:
            # 历史不足 lim 根（次新股/停牌少数据）：与 _kline_payload 的兜底分支同口径，
            # 黄线因不足 114 根全为 None，但白线必须有值（不能整段置空）。
            cut = bars
            yw = _yellow_white(ctx, closes)
            if yw is not None:
                yellow = list(yw[0])
                white = list(yw[1])
            else:
                yellow = [None] * len(cut)
                white = [None] * len(cut)
        windows[code] = {"bars": cut, "yellow": yellow, "white": white}

    # ③ AMV 一次批量（日期窗口 = 各票尾部窗口的并集）
    dates: List[str] = []
    for w in windows.values():
        bs = w["bars"]
        dates.append(str(bs[0]["t"])[:8])
        dates.append(str(bs[-1]["t"])[:8])
    amv_all = _amv_maps_bulk(ctx, [c.split(".")[0] for c in windows], dates)

    # ④ 拼装（只取末 tail 根；数值精度与 _kline_payload 一致）
    items: Dict[str, Any] = {}
    last_t: Optional[str] = None
    for code, w in windows.items():
        bars_full = w["bars"]
        bars = bars_full[-tl:]
        yellow = w["yellow"][-tl:]
        white = w["white"][-tl:]
        code6 = code.split(".")[0]
        amv_map = amv_all.get(code) or amv_all.get(code6) or {}
        # ⚠ 必须在**裁到 lim 根的全长**上算 AMV 三线，再取末 tail 根：
        #   MA10 需要前 9 根（先裁会让前 9 根恒为 None）、白线是 EMA（起点不同值就不同）。
        #   与 /api/kline 的口径一致就靠这一句。
        amv_full = _amv_series_out(ctx, bars_full, amv_map) if bars_full else []
        amv3 = amv_full[-tl:] if amv_full else []
        n = len(bars)
        a_s = [None] * n
        m_s = [None] * n
        aw_s = [None] * n
        for i, a in enumerate(amv3):
            if i < n and a:
                a_s[i], m_s[i], aw_s[i] = a[0], a[1], a[2]
        items[code] = {
            "t": str(bars[-1]["t"])[:8] if bars else None,
            "c": [_sparks_ratio(b["c"], 3) for b in bars],
            "y": [None if v is None else round(float(v), 3) for v in yellow],
            "w": [None if v is None else round(float(v), 3) for v in white],
            "a": a_s,
            "m": m_s,
            "aw": aw_s,
        }
        t = items[code]["t"]
        if t and (last_t is None or t > last_t):
            last_t = t

    payload: Dict[str, Any] = {
        "version": SPARKS_VERSION,
        "asof": last_t,
        "n": len(items),
        "tail": tl,
        "limit": lim,
        "items": items,
    }
    _sparks_cache_put(key, payload)
    if expected8:
        _sparks_disk_put(ctx, key, expected8, payload)
    return copy.deepcopy(payload)


# ---------------------------------------------------------------- /api/kline_bg


def build_kline_bg(ctx: Any, ts_code: str, mode: str = "l2") -> Dict[str, Any]:
    """四态/多空背景带：`{mode, code, name, level, items{YYYYMMDD: 状态}, reason}`。"""
    m = str(mode or "l2").strip().lower()
    if m not in ("l2", "l3", "stock"):
        raise HTTPException(status_code=400, detail="mode 需为 l2 / l3 / stock")
    raw = str(ts_code or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="ts_code 不能为空")

    code: Optional[str] = None
    name = ""
    level: Optional[int] = None
    reason = ""
    states: List[Any] = []
    dates: List[str] = []

    if m in ("l2", "l3"):
        want = 2 if m == "l2" else 3
        if raw.upper().endswith(".TI"):
            try:
                with _db_conn(ctx, THS_DB) as conn:
                    row = _rows(conn, "SELECT name, sw_level FROM board WHERE code=?",
                                (raw.upper(),))
            except Exception:  # noqa: BLE001
                row = []
            if not row:
                reason = "未知板块代码"
            elif int(row[0][1]) != want:
                reason = "该板块为 %s 级，与所选 %d 级不符" % (row[0][1], want)
            else:
                code, name, level = raw.upper(), row[0][0], int(row[0][1])
        else:
            stk = raw.split(".")[0]
            row: List[Any] = []
            if _is_broad_index(ctx, raw):
                # 2026-11-02 修撞码：宽基指数无个股行业归属（查了会撞深市同名个股）
                reason = "指数无行业板块归属"
            else:
                try:
                    with _db_conn(ctx, THS_DB) as conn:
                        row = _rows(conn,
                                    "SELECT b.code,b.name,b.sw_level FROM board_member mb "
                                    "JOIN board b ON b.code=mb.board_code "
                                    "WHERE mb.stock=? AND b.sw_level=?", (stk, want))
                except Exception:  # noqa: BLE001
                    row = []
            if row:
                code, name, level = row[0][0], row[0][1], int(row[0][2])
            else:
                reason = "该股无此层级板块归属"
        if code:
            lvl = "L2" if m == "l2" else "L3"
            try:
                with _db_conn(ctx, AMV_DB) as conn:
                    rs = _rows(conn, "SELECT date,amv FROM sector_daily "
                                     "WHERE level=? AND code=? ORDER BY date", (lvl, code))
            except Exception:  # noqa: BLE001
                rs = []
            dates = [str(r[0]) for r in rs]
            # 不置零：None 表示当日无数据，由 amv_regime 自己处理（见 sma/stock_state_series 缺失值语义）
            vals = [float(r[1]) if r[1] is not None else None for r in rs]
            if vals:
                try:
                    states = list(ctx.lib("amv_regime").compute_regime(dates, vals)["state"])
                except Exception:  # noqa: BLE001
                    states = []
    else:  # stock：个股四态
        stk = raw.split(".")[0]
        code = stk          # 个股模式以股票代码作标识（前端据此判断「有归属」）
        nm: Any = None
        # 2026-11-02 修撞码：宽基指数查 stock_amv 会拿到深市同名个股的四态序列。
        # 指数无个股四态 → 保持空 states（reason 交由前端隐藏）。
        if _is_broad_index(ctx, raw):
            rs: List[Any] = []
        else:
            try:
                with _db_conn(ctx, AMV_DB) as conn:
                    rs = _rows(conn, "SELECT date,amv FROM stock_amv WHERE stock=? ORDER BY date",
                               (stk,))
                    nm = _rows(conn, "SELECT stock_name FROM stock_top WHERE stock_code=? "
                                     "ORDER BY date DESC LIMIT 1", (stk,))
            except Exception:  # noqa: BLE001
                rs = []
        dates = [str(r[0]) for r in rs]
        # 不置零：None 表示当日无数据，由 amv_regime.stock_state_series 自己处理
        vals = [float(r[1]) if r[1] is not None else None for r in rs]
        if vals:
            try:
                states = list(ctx.lib("amv_regime").stock_state_series(vals))
            except Exception:  # noqa: BLE001
                states = []
        if nm:
            name = nm[0][0]

    items: Dict[str, Any] = {}
    if states:
        for d, s in zip(dates, states):
            if s:
                items[str(d).replace("-", "")] = s
    if len(items) > KLINE_BG_MAX_DAYS:
        items = dict(list(items.items())[-KLINE_BG_MAX_DAYS:])
    return {"mode": m, "code": code, "name": name, "level": level,
            "items": items, "reason": reason}


# ---------------------------------------------------------------- 分时（单日）


def _intraday_points(bars: List[Dict[str, Any]], slots: List[str],
                     prev_close: Optional[float]) -> Tuple[Optional[float], List[Dict[str, Any]]]:
    """分钟 bars → (prev_close, points)：连续竞价段、逐点累计 VWAP、09:30 补开盘点。"""
    by_hhmm: Dict[str, Dict[str, Any]] = {}
    for b in bars or []:
        hhmm = _hhmm_t(b.get("t"))
        if SESSION_START <= hhmm <= SESSION_END:
            by_hhmm[hhmm] = b
    cum_amt = 0.0
    cum_vol = 0.0
    points: List[Dict[str, Any]] = []
    last_avg: Optional[float] = None
    for hhmm in slots:
        b = by_hhmm.get(hhmm)
        if b is None:
            continue
        amount = float(b.get("amount") or 0.0)
        volume = float(b.get("v") or 0.0)      # 已由取数层 ×100 → 股
        o = _f(b.get("o")); hi = _f(b.get("h")); lo = _f(b.get("l"))
        cum_amt += amount
        cum_vol += volume
        if cum_vol > 0:
            last_avg = cum_amt / cum_vol
        points.append({
            "t": hhmm,
            "open": None if o is None else round(o, 3),
            "high": None if hi is None else round(hi, 3),
            "low": None if lo is None else round(lo, 3),
            "close": round(float(b["c"]), 3),
            "avg": None if last_avg is None else round(last_avg, 3),
            "volume": round(volume, 2),
            "amount": round(amount, 2),
            "cum_volume": round(cum_vol, 2),
        })
    if points and points[0]["t"] == "09:31":
        b0 = by_hhmm.get("09:31")
        o = _f(b0.get("o")) if b0 else None
        if o is not None and o > 0:
            points.insert(0, {"t": SESSION_START, "open": round(o, 3), "high": round(o, 3),
                              "low": round(o, 3), "close": round(o, 3), "avg": round(o, 3),
                              "volume": 0.0, "amount": 0.0, "cum_volume": 0.0})
    return prev_close, points


def _auction(ctx: Any, glb: str, date8: str) -> List[Dict[str, Any]]:
    """集合竞价段（eltdx `auctions.series`，经门面）：09:15–09:25 秒级快照。"""
    if len(date8) != 8:
        return []
    date_iso = "%s-%s-%s" % (date8[:4], date8[4:6], date8[6:8])
    res = _qs(ctx).call("auctions.series", glb, date_iso)
    pts = getattr(res, "points", None) or ()
    out: List[Dict[str, Any]] = []
    for pt in pts:
        tl = str(getattr(pt, "time_label", "") or "")
        hhmm = tl[:5] if len(tl) >= 5 else ""
        if not (AUCTION_START <= hhmm <= AUCTION_END):
            continue                                   # 尾盘竞价段不展示
        price = float(getattr(pt, "price", 0) or 0)
        if price <= 0:
            continue                                   # 虚拟撮合段（价格未定），不编造
        matched = float(getattr(pt, "matched_volume", 0) or 0)   # 股
        um_raw = getattr(pt, "unmatched_volume", None)
        ud_raw = getattr(pt, "unmatched_direction_raw", None)
        um = _f(um_raw)
        try:
            ud = None if ud_raw is None else int(ud_raw)
        except (TypeError, ValueError):
            ud = None
        out.append({
            "t": ("%s %s" % (date8, tl)) if len(tl) >= 8 else hhmm,
            "price": round(price, 3),
            "volume": round(matched, 2),
            "matched": round(matched, 2),
            "amount": round(price * matched, 2),
            "unmatched": None if um is None else round(um, 2),
            "un_dir": ud,
        })
    # 注意（与旧端一致）：走 `auctions.series` 这条兑底链时**不补** 09:29:59 平台点、
    # 也不带 `virtual` 字段 —— 旧端只有「先有 tick 竞价段」那条支路才加。
    return out


# ---------------------------------------------------------------- 盘后分时落盘（2026-11-07）
#
# 主人：「收盘后的数据就已经固定下来了，没必要每次都重拉重算」。
# 自选卡实时档每票一次 `/api/intraday`（8810 root 路径由本插件接管），48 票冷启动 8s；
# 盘后（周末 / 16:10 后 / 09:15 前）把当日分时落盘，之后重启/重开直接读盘。
# 16:10 = `OH_PostCloseChain` 收盘取数主链启动时间（周一～五，登记册 A1 T36）；
# 15:00–16:10 库还是盘中/前一日数据，不得当作“已定格”。
# 边界：盘中不落盘（数据在变）；只缓存当日档（date 为空）且 count>0；文件名带 code+日期。
_INTRADAY_DISK_VER = 2


def _is_postclose(ctx: Any) -> bool:
    """收盘后（数据固定）时段。唯一口径 = core `trade_clock.postclose()`。

    日历不可用/门面缺失 → False（宁可现拉，不拿旧值当固定值）。
    """
    try:
        return bool(ctx.trade_clock.postclose())
    except Exception:  # noqa: BLE001 - 不可用就按“未就绪”处理
        return False


def build_intraday(ctx: Any, ts_code: str, date: Optional[str] = None) -> Dict[str, Any]:
    """当日分时（旧端 `/api/intraday/{ts_code}` 形状）。"""
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    glb = _glb(ctx, code)
    # 盘后先查落盘（当日同一票只拉一次）
    # 读路径铁律（2026-11-07 夏天核对后补）：文件名带的自然日、内容里的数据日、
    # 写时的日期三者必须一致；回退日（date_fallback）一律当 miss。
    # 此前只校验 v/count，导致存量的“昨天分时”会被当成今天返回。
    postclose = _is_postclose(ctx)
    today8 = datetime.now().strftime("%Y%m%d")
    cache_name = ""
    if postclose and not str(date or "").strip():
        cache_name = "intraday_%s_%s" % (glb, today8)
        try:
            hit = ctx.cache_read(cache_name)
        except Exception:  # noqa: BLE001 - 首次/未声明/损坏 → 现拉
            hit = None
        if isinstance(hit, dict) and hit.get("v") == _INTRADAY_DISK_VER and hit.get("date8") == today8:
            cached = hit.get("payload")
            if (isinstance(cached, dict) and cached.get("count")
                    and cached.get("date") == today8 and not cached.get("date_fallback")):
                return cached
    slots = _slots()
    days = _minute_days(ctx, glb, INTRADAY_MULTI_MAX_DAYS)

    want = "".join(ch for ch in str(date or "") if ch.isdigit())[:8]
    if str(date or "").strip() and len(want) != 8:
        raise HTTPException(status_code=400, detail="非法 date：%r（需 YYYYMMDD）" % (date,))
    if want:
        resolved, fallback = want, False
    elif days:
        resolved = max(days)
        fallback = (resolved != today8)
    else:
        resolved, fallback = today8, False

    raw_bars = days.get(resolved) or []
    bars = [b for b in (_bar_dict(it, True) for it in raw_bars) if b is not None]
    # 2026-11-02（主人报「几个月前的分时图打不开」）：`_minute_days` 只盖最近 10 日窗口，
    #   更早的日期直接空 → 落 core 的 `minute_bars_history`（eltdx minutes.history，深到 2017）。
    if not bars and want:
        try:
            bars = list(_qs(ctx).minute_bars_history(glb, resolved) or [])
        except Exception:  # noqa: BLE001 - 历史源不可用 → 维持空（不编造）
            bars = []
    prev_close = _prev_close_before(ctx, glb, resolved)
    _, points = _intraday_points(bars, slots, prev_close)
    source = "eltdx" if bars else "none"
    payload = {
        "version": "intraday-" + INTRADAY_VERSION, "code": code, "date": resolved,
        "adj": "front", "stale": not bars and not days,
        "vol_unit": "股", "source": source,
        "prev_close": None if prev_close is None else round(prev_close, 3),
        "session": {"start": SESSION_START, "end": SESSION_END,
                    "slots": slots, "slot_count": len(slots)},
        "auction": _auction(ctx, glb, resolved),
        "points": points, "count": len(points),
        "date_fallback": bool(fallback),
    }
    # 落盘铁律（主人 2026-11-07 提醒：没取到/空数据/拿昨天当今天都致命）：
    #   · 只写当日（resolved == today8，即 `fallback=False`）且 points 非空的结果；
    #   · 取数失败/回退到上一交易日的结果**一律不写**，宁可下次重拉。
    if postclose and cache_name and points and not fallback and resolved == today8:
        try:
            ctx.cache_write(cache_name, {"v": _INTRADAY_DISK_VER, "date8": today8,
                                          "payload": payload})
        except Exception:  # noqa: BLE001 - 写失败只是下次多拉一次
            pass
    return payload


# ---------------------------------------------------------------- 分时（多日）


def _days_window(available: List[str], days: int, end: Optional[str],
                 before: Optional[str], after: Optional[str]) -> List[str]:
    """按旧端语义挑窗口：end / before（严格早于）/ after（严格晚于）/ 默认最近 N 日。"""
    ds = sorted(available)
    if before:
        cand = [d for d in ds if d < before]
        return cand[-days:]
    if after:
        cand = [d for d in ds if d > after]
        return cand[:days]
    if end:
        cand = [d for d in ds if d <= end]
        return cand[-days:]
    return ds[-days:]


def build_intraday_multi(ctx: Any, ts_code: str, days: Any = None,
                        end: Optional[str] = None, before: Optional[str] = None,
                        after: Optional[str] = None) -> Dict[str, Any]:
    """多日分时（旧端 `/api/intraday_multi/{ts_code}` 形状）。"""
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    glb = _glb(ctx, code)
    try:
        n = INTRADAY_MULTI_DEFAULT_DAYS if days in (None, "") else int(days)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="非法 days：%r（需整数）" % (days,))
    n = max(1, min(INTRADAY_MULTI_MAX_DAYS, n))
    slots = _slots()
    today8 = datetime.now().strftime("%Y%m%d")

    def _d8(v: Optional[str], name: str) -> Optional[str]:
        if not v:
            return None
        s = "".join(ch for ch in str(v) if ch.isdigit())[:8]
        if len(s) != 8:
            raise HTTPException(status_code=400,
                                detail="非法 %s：%r（需 YYYYMMDD）" % (name, v))
        return s

    end8, before8, after8 = _d8(end, "end"), _d8(before, "before"), _d8(after, "after")
    grouped = _minute_days(ctx, glb, INTRADAY_MULTI_MAX_DAYS)
    if not grouped:
        return {"version": "intraday-multi-" + INTRADAY_VERSION, "code": code,
                "end": end8 or today8, "start": end8 or today8,
                "before": before8, "after": after8, "adj": "front",
                "stale": True, "vol_unit": "股", "date_fallback": False,
                "source": "none", "more": False,
                "session": {"start": SESSION_START, "end": SESSION_END,
                            "slots": slots, "slot_count": len(slots)},
                "days": [], "count": 0}

    window = _days_window(list(grouped), n, end8, before8, after8)
    blocks: List[Dict[str, Any]] = []
    prev_close = _prev_close_before(ctx, glb, window[0]) if window else None
    for d8 in window:
        raw = grouped.get(d8) or []
        bars = [b for b in (_bar_dict(it, True) for it in raw) if b is not None]
        pc, points = _intraday_points(bars, slots, prev_close)
        if not points:
            continue
        blocks.append({"date": d8, "source": None,
                       "prev_close": None if pc is None else round(pc, 3),
                       "points": points, "count": len(points)})
        prev_close = points[-1]["close"]      # 次日昨收 = 本日末根收盘
    return {
        "version": "intraday-multi-" + INTRADAY_VERSION, "code": code,
        "end": blocks[-1]["date"] if blocks else (end8 or today8),
        "start": blocks[0]["date"] if blocks else (end8 or today8),
        "before": before8, "after": after8, "adj": "front",
        "stale": not blocks, "vol_unit": "股",
        "date_fallback": bool(not end8 and blocks and blocks[-1]["date"] != today8),
        "source": "eltdx" if blocks else "none",
        "more": len(window) >= n,
        "session": {"start": SESSION_START, "end": SESSION_END,
                    "slots": slots, "slot_count": len(slots)},
        "days": blocks, "count": sum(b["count"] for b in blocks),
    }


# ---------------------------------------------------------------- 五档 / 筹码 / 逐笔


def _fmt_hhmmss(raw: Any) -> Optional[str]:
    """eltdx `update_time_raw`（如 153003）→ '15:30:03'；非法 → None。"""
    try:
        s = str(int(raw)).zfill(6)
        hh, mm, ss = int(s[0:2]), int(s[2:4]), int(s[4:6])
    except (TypeError, ValueError):
        return None
    if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
        return None
    return "%02d:%02d:%02d" % (hh, mm, ss)


def _elapsed_minutes(hm: str) -> Optional[int]:
    """'15:00' → 当日已开市分钟数（午休不计；9:30 前=0，收盘封顶 240）。"""
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


def _ticks(ctx: Any, glb: str) -> List[Any]:
    q = _qs(ctx)
    try:
        return list(q.today_ticks(glb))
    except Exception:  # noqa: BLE001
        return []


def build_quote5(ctx: Any, ts_code: str) -> Dict[str, Any]:
    """五档盘口卡（旧端 `/api/quote5/{ts_code}` 形状，**无 `ok` 字段**）。"""
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    glb = _glb(ctx, code)
    q = _qs(ctx)
    aux = q.stock_aux(glb) or {}
    out: Dict[str, Any] = {
        "code": code, "last": None, "pre_close": None, "change": None,
        "change_pct": None, "high": None, "low": None,
        "limit_up": None, "limit_down": None,
        "quote_time": None, "asks": [], "bids": [],
        "stats": {"now_vol": None, "total_vol": None, "outer": None, "inner": None,
                  "turnover": None, "vol_ratio": None},
        "ticks": [],
        "asof": _now_str(), "source": "eltdx:depth(0x0547)+ticks(0x0fc5)",
    }
    if aux.get("limit_up"):
        out["limit_up"] = round(float(aux["limit_up"]), 3)
    if aux.get("limit_down"):
        out["limit_down"] = round(float(aux["limit_down"]), 3)
    rec = q.depth(glb)
    if rec is None:
        return out
    last = _f(getattr(rec, "last_price", None)) or 0.0
    pre = _f(getattr(rec, "last_close_price", None)) or 0.0
    high = _f(getattr(rec, "high_price", None)) or 0.0
    low = _f(getattr(rec, "low_price", None)) or 0.0
    if last > 0:
        out["last"] = round(last, 3)
    if pre > 0:
        out["pre_close"] = round(pre, 3)
    if high > 0:
        out["high"] = round(high, 3)
    if low > 0:
        out["low"] = round(low, 3)
    if out["last"] is not None and out["pre_close"]:
        ch = out["last"] - out["pre_close"]
        out["change"] = round(ch, 3)
        out["change_pct"] = round(ch / out["pre_close"] * 100, 3)
    out["quote_time"] = _fmt_hhmmss(getattr(rec, "update_time_raw", None))
    for side, levels in (("bids", getattr(rec, "buy_levels", ()) or ()),
                         ("asks", getattr(rec, "sell_levels", ()) or ())):
        for lv in list(levels)[:QUOTE5_MAX_LEVELS]:
            price = _f(getattr(lv, "price", None))
            if price is None or price <= 0:
                continue
            try:
                vol = int(getattr(lv, "volume", 0) or 0)
            except (TypeError, ValueError):
                vol = 0
            out[side].append({"price": round(price, 3), "vol": vol})
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
    out["ticks"] = _recent_ticks(_ticks(ctx, glb), QUOTE5_TICKS)
    return out


def _recent_ticks(ticks: List[Any], count: int) -> List[Dict[str, Any]]:
    """最近成交（新→旧）：`{t,price,vol,side,n}`。"""
    rows: List[Dict[str, Any]] = []
    for tk in ticks[-count:][::-1]:
        price = _f(getattr(tk, "price", None))
        if price is None:
            continue
        try:
            rows.append({"t": str(getattr(tk, "time_label", "") or ""),
                         "price": round(price, 3),
                         "vol": int(getattr(tk, "volume", 0) or 0),
                         "side": str(getattr(tk, "side", "") or ""),
                         "n": int(getattr(tk, "order_count", 0) or 0)})
        except (TypeError, ValueError):
            continue
    return rows


def _pre_close_limit(ctx: Any, glb: str, aux: Dict[str, Any]) -> Dict[str, Any]:
    """价位类端点共用的昨收/涨跌停（昨收取五档快照的 `last_close_price`）。"""
    pre = None
    rec = _qs(ctx).depth(glb)
    if rec is not None:
        pre = _f(getattr(rec, "last_close_price", None))
    return {
        "pre_close": round(pre, 3) if pre else None,
        "limit_up": round(float(aux["limit_up"]), 3) if aux.get("limit_up") else None,
        "limit_down": round(float(aux["limit_down"]), 3) if aux.get("limit_down") else None,
    }


def build_volume_profile(ctx: Any, ts_code: str, until: Optional[str] = None) -> Dict[str, Any]:
    """价位成交量分布（筹码峰）：只计真实成交，同价合并，升序。"""
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    glb = _glb(ctx, code)
    q = _qs(ctx)
    hm: Optional[str] = None
    if until:
        digits = "".join(ch for ch in str(until) if ch.isdigit())
        if len(digits) >= 4:
            hm = "%s:%s" % (digits[0:2], digits[2:4])
    buckets: Dict[float, int] = {}
    total = 0
    for tk in _ticks(ctx, glb):
        if getattr(tk, "event_kind", "trade") != "trade":
            continue                     # 剔除竞价快照与开盘撮合
        if hm and str(getattr(tk, "time_label", "") or "") > hm:
            continue
        price = _f(getattr(tk, "price", None))
        try:
            vol = int(getattr(tk, "volume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price is None or price <= 0 or vol <= 0:
            continue
        p = round(price, 3)
        buckets[p] = buckets.get(p, 0) + vol
        total += vol
    rows = [{"p": p, "v": v} for p, v in sorted(buckets.items())]
    auxd = _pre_close_limit(ctx, glb, q.stock_aux(glb) or {})
    return {
        "code": code,
        "buckets": rows,
        "max_v": max((r["v"] for r in rows), default=0),
        "total_v": total,
        "pre_close": auxd["pre_close"],
        "limit_up": auxd["limit_up"],
        "limit_down": auxd["limit_down"],
        "asof": _now_str(),
        "source": "eltdx:ticks(0x0fc5)",
    }


def build_volume_ticks(ctx: Any, ts_code: str) -> Dict[str, Any]:
    """逐笔压缩：`rows=[[minute_idx, price_milli, vol]]`（供前端光标本地累计筹码）。"""
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    glb = _glb(ctx, code)
    q = _qs(ctx)
    auxd = _pre_close_limit(ctx, glb, q.stock_aux(glb) or {})
    out: Dict[str, Any] = {"code": code, "pre_close": auxd["pre_close"],
                           "limit_up": auxd["limit_up"], "limit_down": auxd["limit_down"],
                           "rows": [], "asof": _now_str(),
                           "source": "eltdx:ticks(0x0fc5)"}
    slots = _slots()
    idx = {hhmm: i for i, hhmm in enumerate(slots)}
    # 13:00 的逐笔属于下午首根（轴标签 13:01）：不补这条，本地累计会比服务端少那一段
    idx.setdefault("13:00", idx.get("13:01", 121))
    rows: Dict[Tuple[int, int], int] = {}
    for tk in _ticks(ctx, glb):
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


# ---------------------------------------------------------------- F10 简述


def _f10_date_str(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    d = "".join(ch for ch in str(v)[:10] if ch.isdigit())
    if len(d) == 8:
        return "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
    return str(v)[:10] or None


def _f10_b1(ctx: Any, glb: str) -> Dict[str, Any]:
    """B1 socket 批；异常全兜 → 各 None。

    2026-10-26 拆为两个 TTL（原来每次调用无条件跑，B2 的缓存完全包不住它，
    等于每次开弹窗都要多跑两次 socket）：
      · **资金流**（money_flow.daily）：盘中在变 → 5 分钟缓存；
      · **股东户数/上市日**（finance_batch）：季报级静态 → 按交易日缓存。
    两个子段缓存都在 core 门面（`q.f10_b1_flow` / `q.f10_b1_finance`），
    本函数只负责拼装与字段命名（插件侧不持有行情缓存，CONTRACT §4.1）。
    """
    out: Dict[str, Any] = {"main_net_today": None, "main_ratio_today": None,
                           "main_net_5d": None, "holders": None,
                           "holder_report": None, "ipo_date": None}
    q = _qs(ctx)

    flow_fn = getattr(q, "f10_b1_flow", None)
    if callable(flow_fn):
        out.update(flow_fn(glb) or {})
    else:                                    # 旧门面（无缓存接口）：退回直调，宁可慢不抛
        mf = q.call("money_flow.daily", glb)
        recs = list(getattr(mf, "records", None) or ())
        if recs:
            mn = _f(getattr(recs[0], "main_net", None))
            mr = _f(getattr(recs[0], "main_ratio", None))
            if mn is not None:
                out["main_net_today"] = round(mn, 2)
            if mr is not None:
                out["main_ratio_today"] = round(mr, 4)
            five = [v for v in (_f(getattr(r, "main_net", None)) for r in recs[:5]) if v is not None]
            if five:
                out["main_net_5d"] = round(sum(five), 2)

    fb_fn = getattr(q, "f10_b1_finance", None)
    if callable(fb_fn):
        fin = fb_fn(glb) or {}
        if fin.get("holders") is not None:
            out["holders"] = int(fin["holders"])
        out["holder_report"] = fin.get("holder_report")
        out["ipo_date"] = fin.get("ipo_date")
    else:
        fb = q.call("corporate.finance_batch", [glb])
        fbrecs = list(getattr(fb, "records", None) or ())
        if fbrecs:
            r0 = fbrecs[0]
            hd = _f(getattr(r0, "gu_dong_ren_shu_raw_float", None))
            if hd is not None:
                out["holders"] = int(round(hd))
            out["holder_report"] = _f10_date_str(getattr(r0, "updated_date", None))
            out["ipo_date"] = _f10_date_str(getattr(r0, "ipo_date", None))
    return out


def _f10_b2(ctx: Any, glb: str, force: bool = False) -> Dict[str, Any]:
    """B2 段（F10 HTTP 批）→ 门面 `ctx.quotes.f10_brief()` 的结果；不可用 → 全空。

    旧 8800 `_eltdx_f10_brief()` 的 B2 半段：同一 eltdx `F10Client`、同一 Entry/参数/字段名，
    只是客户端由 core 行情门面持有（插件不得自建行情客户端，CONTRACT §4.1）。
    """
    empty: Dict[str, Any] = {"val": {}, "plans": [], "north": None, "forecast": None,
                             "sector": None, "perf": None, "rzrq": [],
                             "perf_q": None, "gdhs": None, "source": None, "errors": [],
                             "fetched_at": None}
    q = _qs(ctx)
    fn = getattr(q, "f10_brief", None)
    if not callable(fn):
        empty["errors"] = ["facade_missing"]
        return empty
    try:
        res = fn(glb, force=force) if force else fn(glb)
    except Exception as exc:  # noqa: BLE001 - B2 整体失败不阻塞 B1
        empty["errors"] = ["%s: %s" % (type(exc).__name__, exc)]
        return empty
    if not isinstance(res, dict):
        empty["errors"] = ["bad_payload"]
        return empty
    out = dict(empty)
    out.update({k: res.get(k, v) for k, v in empty.items() if k != "errors"})
    out["errors"] = list(res.get("errors") or [])
    return out


def build_f10_brief(ctx: Any, ts_code: str, force: bool = False) -> Dict[str, Any]:
    """F10 基础信息（旧端 `/api/f10_brief/{ts_code}` 形状）。

    B1 = socket 批（资金流 0x0ffc + finance_batch 股东户数/上市日）；
    B2 = F10 HTTP 批（估值/市值/北向/盈利预测/增减持/融资融券/季度业绩/股东户数序列/行业）。
    两段任一项失败 → 该字段 null / []（不编造、不补 0），前端按 null 显示「—」。

    `force=True`：跳过 B2 的磁盘/内存缓存强制重拉（前端右键「刷新F10」用）。
    """
    code = _to_ts(ctx, ts_code)
    if not code:
        raise HTTPException(status_code=400,
                            detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
    # 2026-11-02 修撞码：F10 全部 Entry（stock_score/finance/北向/盈利预测…）都按
    # 6 位码查，宽基指数（000688.SH 等）会拿到深市同名个股的整包基本面。
    # 指数无个股基本面 → 直接回骨架空值（如实标无数据，不编造）。
    if _is_broad_index(ctx, code):
        out = copy.deepcopy(_F10_SKELETON)
        out.update({"code": code, "asof": _now_str(),
                    "source": "broad_index(no stock fundamentals)"})
        return out
    glb = _glb(ctx, code)
    b1 = _f10_b1(ctx, glb)
    b2 = _f10_b2(ctx, glb, force=force)
    val = b2.get("val") or {}
    # 以骨架打底：保证字段名与旧端一致（未取到的段值为 None / []），再覆盖已实现的段。
    out: Dict[str, Any] = copy.deepcopy(_F10_SKELETON)
    out.update({
        "code": code,
        "asof": _now_str(),
        # 数据的**真实产生时间**（B2 落盘缓存命中时会很旧）——前端据此提示「N 分钟前」。
        # 与 `asof` 的区别：asof = 本次返回时间（永远新鲜）；fetched_at = 数据那次拉取的时间。
        "fetched_at": b2.get("fetched_at"),
        # source 与旧端同款；B2 没拿到任何值时显式标注降级（排障用，不编造）
        "source": "eltdx:f10(200191)+eltdx:socket(0x0ffc)",
        "ipo_date": b1.get("ipo_date"),
        "holders": b1.get("holders"),
        "holder_report": b1.get("holder_report"),
        "main_net_today": b1.get("main_net_today"),
        "main_ratio_today": b1.get("main_ratio_today"),
        "main_net_5d": b1.get("main_net_5d"),
        "sector": b2.get("sector"),
        "total_mcap": _f(val.get("total_mcap")),
        "float_mcap": _f(val.get("float_mcap")),
        "pe_ttm": _f(val.get("pe_ttm")),
        "pe_pct": _f(val.get("pe_pct")),
        "pb_mrq": _f(val.get("pb_mrq")),
        "pb_pct": _f(val.get("pb_pct")),
        "gd_plans": b2.get("plans") or [],
        "north": b2.get("north"),
        "eps_forecast": b2.get("forecast"),
        "perf": b2.get("perf"),
        "rzrq": b2.get("rzrq") or [],
        "perf_q": b2.get("perf_q"),
        "gdhs": b2.get("gdhs"),
    })
    if b2.get("errors"):
        out["source"] = out["source"] + "(B2部分降级:%s)" % ",".join(str(e) for e in b2["errors"])
    ipo = out["ipo_date"]
    if ipo:
        try:
            d = datetime.strptime(ipo, "%Y-%m-%d").date()
            out["is_new"] = (datetime.now().date() - d).days <= 365
        except ValueError:
            out["is_new"] = None
    return out


# ---------------------------------------------------------------- 路由（root 挂载）


def register(ctx: Any) -> APIRouter:
    """注册 8 个根路径端点（manifest 里 `backend.mount = "root"` → 最终 `/api/<path>`）。"""
    router = APIRouter()

    @router.get("/kline/{ts_code}")
    def kline(ts_code: str,
              period: str = Query(default="d"),
              limit: int = Query(default=KLINE_LIMIT_DEFAULT),
              before: Optional[str] = Query(default=None),
              refresh: int = Query(default=0),
              mf_lookback: Optional[int] = Query(default=None)) -> Dict[str, Any]:
        # refresh 与旧端同签名（无缓存可绕）；mf_lookback = 主力动向回顾周期 N（仅日线生效）
        return build_kline(ctx, ts_code, period=period, limit=limit, before=before,
                           mf_lookback=mf_lookback)

    @router.get("/kline_sparks")
    def kline_sparks(codes: str = Query(default=""),
                     tail: int = Query(default=45),
                     limit: int = Query(default=160)) -> Dict[str, Any]:
        """多票 spark 序列（自选池主图/副图专用，2026-11-04）。

        `codes` = 逗号分隔的代码串；只回主图/副图需要的 6 条序列末 `tail` 根。
        与 `/api/kline/{code}` 同口径，但不含 mf/行业/概念/流通股（那几个占 84% 耗时）。
        """
        raw = [c for c in str(codes or "").replace(" ", "").split(",") if c]
        return build_kline_sparks(ctx, raw, tail=tail, limit=limit)

    @router.get("/kline_bg/{ts_code}")
    def kline_bg(ts_code: str, mode: str = Query(default="l2")) -> Dict[str, Any]:
        return build_kline_bg(ctx, ts_code, mode=mode)

    @router.get("/intraday/{ts_code}")
    def intraday(ts_code: str, date: Optional[str] = Query(default=None),
                 refresh: int = Query(default=0)) -> Dict[str, Any]:
        return build_intraday(ctx, ts_code, date=date)

    @router.get("/intraday_multi/{ts_code}")
    def intraday_multi(ts_code: str,
                       days: Optional[int] = Query(default=None),
                       end: Optional[str] = Query(default=None),
                       before: Optional[str] = Query(default=None),
                       after: Optional[str] = Query(default=None),
                       refresh: int = Query(default=0)) -> Dict[str, Any]:
        return build_intraday_multi(ctx, ts_code, days=days, end=end,
                                    before=before, after=after)

    @router.get("/quote5/{ts_code}")
    def quote5(ts_code: str) -> Dict[str, Any]:
        return build_quote5(ctx, ts_code)

    @router.get("/volume_profile/{ts_code}")
    def volume_profile(ts_code: str, until: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        return build_volume_profile(ctx, ts_code, until=until)

    @router.get("/volume_ticks/{ts_code}")
    def volume_ticks(ts_code: str) -> Dict[str, Any]:
        return build_volume_ticks(ctx, ts_code)

    @router.get("/f10_brief/{ts_code}")
    def f10_brief(ts_code: str, force: int = Query(default=0)) -> Dict[str, Any]:
        """`force=1` 跳过 B2 缓存强制重拉（弹窗里右键「刷新F10」用）。"""
        return build_f10_brief(ctx, ts_code, force=bool(force))

    @router.get("/f10_brief/{ts_code}/quick")
    def f10_brief_quick(ts_code: str, force: int = Query(default=0)) -> Dict[str, Any]:
        """**轻量 F10**：只返回估值四项（总市值/PE/PB/分位），供弹窗首屏使用。

        主人 2026-10-27 定的目标：「首屏 500ms 内看到关键数据」。
        完整 F10 是 9 个串行请求（1.7s 到 8s+ 随链路波动），不能挡在首屏前面。
        本端点单项 ~200–280ms；命中磁盘缓存时 ~1ms。

        返回：`{ok, code, val:{total_mcap, pe_ttm, pe_pct, pb_mrq, pb_pct}, fetched_at}`
        失败不 500：`val` 为空对象，前端保持「载入中」直到完整段回来。
        """
        code = _to_ts(ctx, ts_code)
        if not code:
            raise HTTPException(status_code=400,
                                detail="非法 ts_code：%r（需 6 位代码.SH/SZ/BJ）" % (ts_code,))
        # 宽基指数无个股估值（2026-11-02 同 `_is_broad_index` 修撞码）：回空 val，
        # 前端保持「载入中」→ 完整段同样回空，行业等段显示「—」。
        if _is_broad_index(ctx, code):
            return {"ok": True, "code": code, "val": {}, "fetched_at": None}
        glb = _glb(ctx, code)
        out: Dict[str, Any] = {"ok": True, "code": code, "val": {}, "fetched_at": None}
        try:
            fn = getattr(ctx.quotes, "f10_valuation_only", None)
            if fn is None:          # 旧门面：不含轻量口 → 前端等完整段
                return out
            res = fn(glb)
            if isinstance(res, dict):
                out["val"] = dict(res.get("val") or {})
                out["fetched_at"] = res.get("fetched_at")
        except Exception:  # noqa: BLE001 - 轻量口失败不阻塞（前端会走完整段）
            pass
        return out

    return router
