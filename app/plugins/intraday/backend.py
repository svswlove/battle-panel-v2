# -*- coding: utf-8 -*-
"""intraday 插件后端：板块轨迹**摘要**端点（唯一一条业务路由）+ 健康自省。

契约：docs/CONTRACT.md；本插件的数据契约：docs/intraday_contract.md。

## 为什么只有一条路由

卡片的 5 个块里，4 个直接消费既有 root 挂载端点（前端 `ctx.api.get('/api/...')`，
shell 对 `/api/` 开头的路径不拼插件前缀）：

| 块 | 端点 | 提供方 |
|---|---|---|
| 恐慌广度（冰点卡） | `GET /api/panic` | `ops` 插件（root） |
| AMV 实时摘要 | `GET /api/amv_live.json` | `compat` 插件（root） |
| 广度 880 | `GET /api/breadth_880.json` | `compat_sector` 插件（root） |
| 盘中脉冲（板块当日轨迹） | `GET /api/sector_intraday.json?codes=` | `compat_sector` 插件（root） |

只有**板块轨迹摘要**需要本插件自己出手：旧端 `/api/sector_track.json` 下发的是
`amv_live_sector_track.json` 的当日全量（235 个快照 × 90 个板块 ≈ 2.7MB，
磁盘文件 10.2MB 含两日），而卡片只需要「全A 序列 + 最新快照的领先/落后板块」。
按 20s 刷新节奏全量拉 = 每分钟 8MB 无效负载 + 21k 个 board 字典的重复解析，
故这里读同一个生产文件（`ctx.panel_json("amv_live_sector_track")`，白名单内、只读），
在服务端裁成几 KB 的摘要。

**这不是第二份口径**：`rel`（超额强度）= 板块 `amv_pct` − 同快照 `all`，
就是旧端 `_stCal='rel'` 的显示口径（index.html:4906 `_stVal`）；
排序/取头部是纯展示切片，不做任何新的指标计算、不读库、不重算 AMV。

## 与旧端的差异（如实登记）

1. 旧端 `/api/sector_track.json` 另带 `weights`（板块成交额占比基线，取 `amv_board.db`
   前一交易日 L2 share）。本摘要**不下发权重**：本插件不连库，卡片的轨迹摘要也不需要
   权重气泡；权重四象图仍在旧端 `/api/sector_track.json`（未迁移）。
2. 旧端 `_track_stale`（盘中 >3min 无新点判陈旧）是**口径判定**，不在此重算：
   摘要只如实给 `last_ts` 与快照数，新鲜度由前端按「末点时间 vs 现在」标注为提示文本。
   这样陈旧闸门仍只有一个 owner（`compat_sector`），本插件不产生第二套阈值。
3. 快照按旧端 `_stSnaps` 同口径过滤 09:30–15:00（剔盘前 08:xx 与收盘后点），
   避免纵轴被盘前极端值撑爆。

## 硬约束

不连库、不自连行情源、不发 HTTP、不写任何文件；只经 `ctx.panel_json` 读白名单生产文件，
以及 `ctx.output_read` 只读另一个插件的产物（全A 日线归 thermometer 产，本插件只消费）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

TRACK_PANEL = "amv_live_sector_track"   # paths.READABLE_PANEL 已登记（compat_sector 同源）
CARD_ID = "intraday.main"
REFRESH_EVERY_MS = 20000
# 旧端 `_stSnaps` 的盘中时段过滤（index.html:4905）：只取 09:30–15:00
SESSION_OPEN, SESSION_CLOSE = "09:30", "15:00"
TOP_DEFAULT, TOP_MAX = 6, 20

DATA_SOURCES = (
    "/api/panic",
    "/api/amv_live.json",
    "/api/breadth_880.json",
    "/api/sector_intraday.json?codes=",
    "/api/p/intraday/summary",
)

#: 全A 指数（沪深全A 等权 880008，glob 码 sh880008）——冰点卡的背景层数据口。
#: 2026-10-30 主人标注 58：全A 日涨跌要作为浅色线 + 淡色面积叠到恐慌图上。
ALL_A_GLB = "sh880008"
#: 本进程内缓存秒数（日频指标，120s 足够；刷新页面不重复打行情源）
ALL_A_TTL = 120.0
_ALL_A_CACHE: Dict[str, Any] = {"at": 0.0, "doc": None}
#: 880008 日线的**生产方**与产物名（2026-11-07 产销分离）。
#: 全A 日线归 thermometer 的盘后脚本产（tools/index_880008_daily.py：vipdoc 历史 + eltdx 尾），
#: 盘中页只是消费方：经 core 只读通道 `ctx.output_read` 取现成产物，不重算、不直连别人的目录、
#: 不每轮打 eltdx。产物结构见该脚本：`{meta:{merged_last,...}, series:[{date,close,...}]}`。
THERMO_PLUGIN = "thermometer"
THERMO_INDEX_OUTPUT = "index_880008_series"
THERMO_SOURCE = "thermometer:index_880008"
ELTDX_SOURCE = "eltdx:%s" % ALL_A_GLB


def _bar_attr(bar: Any, *names: str) -> Any:
    """日 K 记录的字段兼容取值（eltdx 不同版本字段名不一致，同 state_tree 的做法）。"""
    for name in names:
        val = getattr(bar, name, None)
        if val is not None:
            return val
    return None


def _date_key(raw: Any) -> str:
    """日 K 日期归一成 `YYYYMMDD`（eltdx 给 datetime/str，产物给 `20261106` 或 `2026-11-06`）。"""
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y%m%d")
    return str(raw or "").replace("-", "")[:8]


def _series_rows(bars: List[Any], n: int) -> List[Dict[str, Any]]:
    """`[(date, close)]`（升序）→ `[{date, close, pct}]`，取末 n 根。

    **涨跌幅口径只在这里算一次**：`pct = close / 前一日 close − 1`（%），四舍五入 2 位；
    序列首行没有前收 → `pct=None`。两个数据源共用本函数，不产生第二份口径。
    """
    rows: List[Dict[str, Any]] = []
    prev: Optional[float] = None
    for date, close in bars:
        pct = None if not prev else round((close - prev) / prev * 100.0, 2)
        rows.append({"date": date, "close": round(close, 2), "pct": pct})
        prev = close
    return rows[-n:]


def _thermometer_bars(ctx: Any, want: int) -> Optional[List[Any]]:
    """读 thermometer 现成产物 → `[(date, close)]` 升序（末 want 根）；不可用返回 None。

    只读、跨插件：`ctx.output_read` 不存在（旧 core）/ 文件不在 / 损坏 / 结构不符
    （没有 `series` 或 `bars` 列表、字段读不出）/ 全行无效 → 一律 None，由调用方回退 eltdx。
    """
    read = getattr(ctx, "output_read", None)
    if not callable(read):
        return None
    try:
        doc = read(THERMO_PLUGIN, THERMO_INDEX_OUTPUT, None)
    except Exception:  # noqa: BLE001 - 读不到就走回退，不让卡片挂
        return None
    if not isinstance(doc, dict):
        return None
    raw = doc.get("series")
    if not isinstance(raw, list):
        raw = doc.get("bars")
    if not isinstance(raw, list):
        return None
    bars: List[Any] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        date = _date_key(item.get("date") if item.get("date") is not None else item.get("time"))
        close = _num(item.get("close"))
        if not date or close is None or close <= 0.0:
            continue
        bars.append((date, close))
    if not bars:
        return None
    bars.sort(key=lambda r: r[0])
    return bars[-want:]


def _all_a_series(ctx: Any, n: int) -> Dict[str, Any]:
    """全A 880008 最近 n 个交易日 → `{ok, code, rows:[{date, close, pct}], source, reason}`。

    数据源优先级（2026-11-07）：**盘后**优先读 thermometer 的现成产物
    （`index_880008_series`，同一指标一个 owner）；读不到/结构不符/空，或**盘中**，回退
    `ctx.quotes.daily_bars`（回退路径行为不变）。

    为什么盘中不用产物：产物是收盘链 16:10 后写的，盘中最后一根是昨收；
    盘中读它会把「当日涨跌」滞后一天。宁可变慢，不能拿昨天当今天。
    两路都算同口径：`pct` = 当日收盘相对前一交易日收盘的涨跌幅（%）；首行无前收，pct 为 None。
    取数失败 / 空 → ok=false + reason，不静默造数。
    """
    now = time.time()
    cached = _ALL_A_CACHE.get("doc")
    if (cached is not None and now - float(_ALL_A_CACHE.get("at") or 0) < ALL_A_TTL
            and len(cached.get("rows") or []) >= n):
        return {**cached, "rows": (cached.get("rows") or [])[-n:]}
    # 末 n 根要算涨跌幅，多取一根当前收；产物/行情源都不额外重算
    try:
        postclose = bool(ctx.trade_clock.postclose())
    except Exception:  # noqa: BLE001 - 旧 core / 门面异常 → 按盘中处理（更保守）
        postclose = False
    pairs = _thermometer_bars(ctx, n + 1) if postclose else None
    source = THERMO_SOURCE if pairs else ""
    err = ""
    if not pairs:
        try:
            bars = ctx.quotes.daily_bars(ALL_A_GLB, max(n + 10, 80)) or []
        except Exception as exc:  # noqa: BLE001 - 取不到就标缺，不让卡片挂
            bars, err = [], "%s: %s" % (type(exc).__name__, exc)
        pairs = []
        for bar in bars:
            date = _date_key(_bar_attr(bar, "time"))
            close = _bar_attr(bar, "close_price", "close")
            if not date or close is None:
                continue
            pairs.append((date, float(close)))
        if pairs:
            source = ELTDX_SOURCE
    rows = _series_rows(pairs, n) if pairs else []
    if not rows and not err:
        err = "thermometer 产物不可用、eltdx %s 返回空" % ALL_A_GLB
    doc: Dict[str, Any] = {"ok": bool(rows), "code": ALL_A_GLB, "rows": rows,
                           "source": source, "reason": err}
    _ALL_A_CACHE.update(at=now, doc=doc)
    return doc


def _dkey(x: Any) -> str:
    """日期键归一：写入端混用 `20260930` / `2026-10-08`（`-`(0x2D) < `0`(0x30)）。"""
    return str(x).replace("-", "")


def _in_session(ts: Any) -> bool:
    """快照时间戳（`YYYY-MM-DD HH:MM:SS`）是否落在盘中 09:30–15:00。"""
    hhmm = str(ts or "")[11:16]
    return SESSION_OPEN <= hhmm <= SESSION_CLOSE


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # nan / inf 一律当缺值（旧端 `_finite` 同口径：不拿脏值画图）
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _row(code: str, board: Dict[str, Any], all_v: Optional[float]) -> Dict[str, Any]:
    amv = _num(board.get("amv_pct"))
    rel = None if (amv is None or all_v is None) else round(amv - all_v, 4)
    return {
        "code": code,
        "name": board.get("name") or code,
        "amv_pct": None if amv is None else round(amv, 4),
        "rel": rel,
        "chg_e": _num(board.get("chg_e")),
        "chg_w": _num(board.get("chg_w")),
        "amt_share": _num(board.get("amt_share")),
    }


def build_summary(doc: Any, date: Any = "", top: int = TOP_DEFAULT) -> Dict[str, Any]:
    """把 `amv_live_sector_track` 文档裁成摘要（纯函数，便于单测）。

    返回键：date / asof / last_ts / snaps / boards_total / all_series /
    first_all / last_all / all_delta / strong / weak / leaders / laggards / top。
    无数据 → `ok:false` + `reason`（不编造零值）。
    """
    top = max(1, min(int(top or TOP_DEFAULT), TOP_MAX))
    if not isinstance(doc, dict) or not isinstance(doc.get("dates"), dict):
        return {"ok": False, "reason": "板块轨迹文件缺少 dates 段"}
    keys = [k for k in doc["dates"].keys() if doc["dates"].get(k)]
    if not keys:
        return {"ok": False, "reason": "板块轨迹文件暂无任何交易日快照"}

    want = _dkey(date) if date else ""
    picked = next((k for k in keys if _dkey(k) == want), None) or max(keys, key=_dkey)
    raw = doc["dates"].get(picked) or []
    snaps = [s for s in raw if isinstance(s, dict) and _in_session(s.get("ts"))]
    if not snaps:
        return {"ok": False, "reason": "当日快照均在盘中时段之外（09:30–15:00 无点）",
                "date": picked, "snaps": 0}

    series: List[Dict[str, Any]] = []
    for s in snaps:
        series.append({"t": str(s.get("ts") or "")[11:16], "all": _num(s.get("all"))})

    latest = snaps[-1]
    all_v = _num(latest.get("all"))
    boards = latest.get("boards") or {}
    rows = [_row(c, b, all_v) for c, b in boards.items() if isinstance(b, dict)]
    rated = [r for r in rows if r["rel"] is not None]
    rated.sort(key=lambda r: r["rel"], reverse=True)
    strong = [r for r in rated if (r["rel"] or 0) > 0]
    weak = [r for r in rated if (r["rel"] or 0) < 0]

    first_all = next((p["all"] for p in series if p["all"] is not None), None)
    last_all = all_v
    delta = None if (first_all is None or last_all is None) else round(last_all - first_all, 4)
    meta = doc.get("meta") or {}
    return {
        "ok": True,
        "date": picked,
        "asof": meta.get("generated_at") or "",
        "source": meta.get("source") or "",
        "last_ts": latest.get("ts") or "",
        "snaps": len(snaps),
        "boards_total": len(rows),
        "all_series": series,
        "first_all": first_all,
        "last_all": last_all,
        "all_delta": delta,
        "strong": len(strong),
        "weak": len(weak),
        "leaders": rated[:top],
        "laggards": list(reversed(rated[-top:])),
        "top": top,
    }


def build_health() -> Dict[str, Any]:
    """GET /health：薄壳自省（无 IO、无写）。"""
    return {
        "ok": True,
        "plugin": "intraday",
        "card": CARD_ID,
        "read_only": True,
        "refresh_every_ms": REFRESH_EVERY_MS,
        "sources": list(DATA_SOURCES),
        "write_ops": [],
        "summary_panel": TRACK_PANEL,
    }


def register(ctx: Any) -> APIRouter:
    """入口：`/health` + `/summary`（板块轨迹摘要）+ `/index-series`（全A 日涨跌）。"""
    router = APIRouter()

    @router.get("/health")
    def health() -> Dict[str, Any]:
        return build_health()

    @router.get("/index-series")
    def index_series(n: int = Query(default=160, ge=20, le=400)) -> Dict[str, Any]:
        """全A 880008 日线涨跌幅（冰点卡背景层用，2026-10-30 标注 58）。

        数据口 = thermometer 的现成产物（`ctx.output_read` 只读跨插件）；产物不在/不符才回退
        `ctx.quotes.daily_bars`（core 持有 eltdx 单例，插件不自连行情源）。
        120s 进程内缓存；不在这里做全市场循环、不落盘。
        取不到 → `ok:false` + reason，前端标缺，不造数。
        """
        return _all_a_series(ctx, n)

    @router.get("/summary")
    def summary(date: str = Query(default=""), top: int = Query(default=TOP_DEFAULT)) -> Dict[str, Any]:
        """板块轨迹摘要（当日全A序列 + 最新快照领先/落后板块）。

        `date` 支持 `20261023` / `2026-10-23`；缺省取最新一日。`top` 取 1–20。
        文件缺失 → 404（与 compat 层「文件不在」同档）；文件坏 → 503（不静默回空摘要）。
        """
        if TRACK_PANEL not in getattr(ctx.manifest, "panel_read", ()):
            raise HTTPException(
                status_code=500,
                detail="%s 未在 manifest.data.panel 声明（白名单双校验的第一半）" % TRACK_PANEL,
            )
        try:
            doc = ctx.panel_json(TRACK_PANEL)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="板块轨迹生产文件不存在：%s.json" % TRACK_PANEL)
        except PermissionError as exc:
            raise HTTPException(status_code=500, detail="板块轨迹未在插件清单声明：%s" % (exc,))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=503, detail="板块轨迹文件读取失败：%s: %s"
                                % (type(exc).__name__, exc))
        return build_summary(doc, date=date, top=top)

    return router
