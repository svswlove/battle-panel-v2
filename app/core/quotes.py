"""行情源门面（v1，2026-10-23 新增）。

**为什么要有这一层**：分时图、五档盘口、逐笔筹码三项都靠 eltdx 的按需 socket 取数，
没有常驻进程。如果每个插件各自 `TdxClient()`，就会出现 N 个连接、N 份重连逻辑、
N 套降级分支——行情源一变要改 N 处。这一层只做三件事：

1. **单例连接**：全进程一个 `TdxClient`，懒建、断线重建一次、失败返回 None（调用方降级）。
2. **短 TTL 缓存**：同一标的同一取数在 `ttl` 秒内复用，避免一张卡刷新把上游打爆。
3. **口径归一**：`sh600519` / `600519.SH` / `600519` 三种写法统一成 eltdx 的 `sh600519`。

**这不是"新版接 QMT"**：契约 §8 禁的是写 `data/panel`、下单、以及接 QMT 的写链路。
eltdx 是**只读行情源**，与 `ctx.db_read` 同级；旧 8800 的分时/五档/筹码也全部走它
（`server.py` 的 `_eltdx_quote5_bundle` / `_eltdx_volume_profile`），同源才谈得上双跑比对。

**边界**：插件只能通过 `ctx.quotes` 用，不得自己 `from eltdx import TdxClient`。
core 在加载插件时静态扫描这一条（见 `contract.py` 的 FORBIDDEN_IMPORTS）。

**下单行情（4.1.3a-1）**：`order_quote()` / `order_metrics()` 是旧 8800
`server.py::_order_quote()` 的 eltdx 半段——盘口卡同形 payload + `fresh`（120s）
+ ATR14/MA5。**不接 QMT**：QMT 5 秒内盘口覆盖在 `app/core/feed.py` 里做，
本模块不设 `src`、不碰 tick，保证"eltdx 打底"这层口径能被单测钉死。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

# eltdx 所在目录：envs/qmt312（旧 server.py 的 QMT312_DIR 同源）
from .paths import WS

logger = logging.getLogger(__name__)

ELTDX_DIR = WS / "envs" / "qmt312"

# 默认缓存时长（秒）。分时/五档盘中变化快，取 3s：一张卡 20s 刷一次也不会重复取数，
# 但手动连点也不会打穿上游。
DEFAULT_TTL = 3.0
# 逐笔筹码要翻 6 页，代价高，缓存长一点（旧端 _Q5_TICK_TTL = 20s，同口径）
TICKS_TTL = 20.0
# 单次 bars.get 的根数上限：2026-10-23 实测 800 根 OK、900 根返回 None。
# 多日分时必须分页拼，不能一次要 1200+ 根。
BARS_PAGE = 720

_LOCK = threading.Lock()
_CLIENT: Any = None


def normalize(code: str) -> str | None:
    """`600519.SH` / `sh600519` / `600519` → `sh600519`；无法识别返回 None。

    交易所前缀规则（与旧 server.py 一致）：
      .SH / 6xxxxx / 5xxxxx / 9xxxxx  → sh
      .SZ / 0xxxxx / 3xxxxx / 1xxxxx  → sz
      .BJ / 4xxxxx / 8xxxxx / 920xxx  → bj
    指数（000001.SH）带后缀时按后缀走，不带后缀按 6 位首位判断（与旧端 `to_ts_code` 同风险，
    但行情端点一律要求带后缀或前缀，避免 000001 串到平安银行）。
    """
    raw = str(code or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if low[:2] in ("sh", "sz", "bj") and len(low) >= 8:
        return low
    body, _, suffix = raw.partition(".")
    body = body.strip()
    if len(body) != 6 or not body.isdigit():
        return None
    sfx = suffix.upper()
    # 带了后缀就必须是已知交易所；拼错（.XX）拒绝，不猜
    if suffix and sfx not in ("SH", "SZ", "BJ"):
        return None
    if sfx == "SH":
        return "sh" + body
    if sfx == "SZ":
        return "sz" + body
    if sfx == "BJ":
        return "bj" + body
    head = body[0]
    if head in "65 9".replace(" ", ""):
        return "sh" + body
    if head in "013":
        return "sz" + body
    if head in "48" or body.startswith("920"):
        return "bj" + body
    return None


def to_ts_code(code: str) -> str | None:
    """反向：`sh600519` → `600519.SH`。"""
    g = normalize(code)
    if not g:
        return None
    prefix, body = g[:2], g[2:]
    return body + "." + {"sh": "SH", "sz": "SZ", "bj": "BJ"}[prefix]


def _client() -> Any:
    """懒建单例客户端。不可用返回 None（调用方降级，不抛）。"""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    try:
        if str(ELTDX_DIR) not in sys.path:
            sys.path.insert(0, str(ELTDX_DIR))
        from eltdx import TdxClient  # noqa: PLC0415 - 懒引入：不可用即降级
        _CLIENT = TdxClient(timeout=6, probe_hosts=False)
    except Exception:  # noqa: BLE001 - 环境缺 eltdx：返回 None 由调用方降级
        _CLIENT = None
    return _CLIENT


def _drop_client() -> None:
    global _CLIENT
    try:
        if _CLIENT is not None:
            _CLIENT.close()
    except Exception:  # noqa: BLE001
        pass
    _CLIENT = None


def call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    """在单例客户端上调用 `fn_name`，断线重建一次；仍失败返回 None。

    `fn_name` 形如 `"quotes.get_depth"`（点号取属性）。
    加锁保证同一时刻只有一个线程在动 socket——eltdx 的连接不是线程安全的。
    """
    with _LOCK:
        for attempt in (0, 1):
            cli = _client()
            if cli is None:
                return None
            try:
                target = cli
                for part in fn_name.split("."):
                    target = getattr(target, part)
                return target(*args, **kwargs)
            except Exception:  # noqa: BLE001 - 首次失败重建一次
                if attempt == 1:
                    return None
                _drop_client()
        return None


class _Cache:
    """极简 TTL 缓存：键 → (时间戳, 值)。按标的数量上限淘汰最旧的。"""

    def __init__(self, keep: int = 16) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._keep = keep
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float) -> Any:
        with self._lock:
            hit = self._d.get(key)
            if hit and (time.monotonic() - hit[0]) < ttl:
                return hit[1]
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._d[key] = (time.monotonic(), value)
            if len(self._d) > self._keep:
                oldest = min(self._d, key=lambda k: self._d[k][0])
                self._d.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


# 缓存容量按「同时在屏的标的数」定（2026-10-29 自选页打开慢的根因之一）：
#   原 keep=16 —— 自选池 31 票 + K 线弹窗 24 条同时取日线时，键数 55+ 远超 16，
#   互相淘汰导致**每次请求 100% 未命中**：30 票 EOD 行情串行 31×17ms=0.53s，
#   而且面板每 5s 轮询都重付一次。日线/盘口每票几十根 bar，放 256 个键约 20–70MB。
#   ticks（当日逐笔）单票几千条，保持 16。
_depth_cache = _Cache(keep=128)
_ticks_cache = _Cache()
_bars_cache = _Cache(keep=256)
_aux_cache = _Cache(keep=128)


def depth(glb: str) -> Any:
    """五档快照记录（eltdx `quotes.get_depth` 的 records[0]）；不可用返回 None。"""
    hit = _depth_cache.get(glb, DEFAULT_TTL)
    if hit is not None:
        return hit
    res = call("quotes.get_depth", [glb])
    recs = getattr(res, "records", None) or ()
    rec = recs[0] if recs else None
    if rec is not None:
        _depth_cache.put(glb, rec)
    return rec


def today_ticks(glb: str, pages: int = 6, page_size: int = 1800) -> list[Any]:
    """当日逐笔（**按时间升序**，最后一条是最新成交）。缓存 TICKS_TTL 秒。

    ⚠ 分页顺序坑（2026-10-23 实测）：`trades.today(start=N)` 的 `start` 是**从最新往前**的
    偏移 —— `start=0` 是最新一段（15:29→13:24）、`start=1800` 是次新（13:24→10:21）、
    `start=3600` 是最早（09:25→10:21）。所以**按 page 0→N 顺序 extend 会得到倒序**，
    `[-30:]` 取到的反而是开盘附近（实测拿到 10:21 而不是 15:29）。
    正确做法：先把各页收起来，再**从最后一页往回收**，拼成升序。

    另：`start` 超出当日总量时 eltdx 会抛 `ProtocolError: record count ... exceeds payload
    capacity`，这里当“取完了”处理，不报错。
    不可用返回空列表。
    """
    hit = _ticks_cache.get(glb, TICKS_TTL)
    if hit is not None:
        return hit
    pages_data: list[list[Any]] = []
    for page in range(pages):
        try:
            part = call("trades.today", glb, start=page * page_size, count=page_size)
        except Exception:  # noqa: BLE001 - 越界/传输错误 = 取完了
            break
        if part is None:
            break
        chunk = list(getattr(part, "ticks", ()) or ())
        n = int(getattr(part, "count", 0) or 0)
        if chunk:
            pages_data.append(chunk)
        if n < page_size:
            break
    out: list[Any] = []
    for chunk in reversed(pages_data):
        out.extend(chunk)
    if out:
        _ticks_cache.put(glb, out)
    return out


def minute_bars(glb: str, count: int = 241) -> list[Any]:
    """**最近一个交易日**的 1 分钟线（已按日期过滤，升序）。缓存 DEFAULT_TTL 秒。

    ⚠ 这里必须过滤日期：eltdx 的 `count` 是**跨日**的“最近 N 根”，不是“当日全量”。
    2026-10-23 实测 `count=241` 取回的是「昨日 11:30 → 今日 13:00」跨两天 241 根，
    直接当当日分时用会得到错位的时间轴（缺 09:30–11:29，多出昨天下午）。
    所以：先多取（当日最多 241 根，取 2 倍保证覆盖完整一日），再按**末根所在日期**过滤。

    不可用返回空列表。
    """
    key = f"{glb}:{count}"
    hit = _bars_cache.get(key, DEFAULT_TTL)
    if hit is not None:
        return hit
    res = call("bars.get", glb, period="1m", count=max(count, 241) * 2)
    rows = list(getattr(res, "bars", ()) or ()) if res is not None else []
    if not rows:
        return []
    # 末根所在日期 = 目标交易日
    last_t = getattr(rows[-1], "time", None)
    last_day = last_t.strftime("%Y%m%d") if hasattr(last_t, "strftime") else ""
    if last_day:
        rows = [b for b in rows
                if hasattr(getattr(b, "time", None), "strftime")
                and getattr(b, "time").strftime("%Y%m%d") == last_day]
    rows.sort(key=lambda b: getattr(b, "time", None))
    if rows:
        _bars_cache.put(key, rows)
    return rows


def daily_bars(glb: str, count: int = 8, adjust: str | None = None) -> list[Any]:
    """最近 N 根日线（升序）。`adjust="qfq"` 直接要前复权。

    缓存 DEFAULT_TTL 秒（日线盘中不变，但收盘后会变，所以也给个短 TTL）。
    """
    key = f"{glb}:d:{count}:{adjust or 'raw'}"
    hit = _bars_cache.get(key, 60.0)
    if hit is not None:
        return hit
    kwargs: dict[str, Any] = {"period": "day", "count": count}
    if adjust:
        kwargs["adjust"] = adjust
    res = call("bars.get", glb, **kwargs)
    rows = list(getattr(res, "bars", ()) or ()) if res is not None else []
    if rows:
        _bars_cache.put(key, rows)
    return rows


def index_daily_bars(ts_code: str, count: int = 40) -> list[dict[str, Any]]:
    """指数日线（eltdx，不复权）→ [{date8, o, h, l, c, amount}] 升序；不可用 → []。

    路由口径（主人 2026-11-01 定）：**指数走 eltdx（通达信），同花顺只留给 .TI 板块**。
    复盘图指数卡/量能卡、选股打分基准等指数日线消费方一律走本函数，不再读
    ths_index.db（那是同花顺口径，且生产侧缺口自算兜底行混在真实行里）。

    代码换算复用 `normalize()`（000016.SH→sh000016、399001.SZ→sz399001、
    880008.SH→sh880008，与 fupan_all_a 同一套前缀口径）。
    缓存 60s：复盘装配每次 build 会逐码取，短缓存避免同分钟重复打 eltdx。
    """
    glb = normalize(ts_code)
    if not glb or count <= 0:
        return []
    key = f"{glb}:idxd:{count}"
    hit = _bars_cache.get(key, 60.0)
    if hit is not None:
        return hit
    res = call("bars.get", glb, period="day", count=int(count))
    rows = list(getattr(res, "bars", ()) or ()) if res is not None else []
    out: list[dict[str, Any]] = []
    for b in rows:
        t = getattr(b, "time", None)
        if not hasattr(t, "strftime"):
            continue
        try:
            out.append({
                "date8": t.strftime("%Y%m%d"),
                "o": float(b.open), "h": float(b.high),
                "l": float(b.low), "c": float(b.close),
                "amount": float(getattr(b, "amount", 0.0) or 0.0) or None,
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["date8"])
    _bars_cache.put(key, out)
    return out


def stock_aux(glb: str) -> dict[str, Any]:
    """盘口辅助数据：`avg5`（过去5日日均量·手）/ `circ`（流通股本·股）/ 涨跌停价。

    口径唯一源：旧 8800 `_quote5_aux()`。任一项失败 → 该字段 None（不补零）。
    缓存 60s（这些数据盘中不变）。
    """
    key = f"{glb}:aux"
    hit = _aux_cache.get(key, 60.0)
    if hit is not None:
        return hit
    out: dict[str, Any] = {"avg5": None, "circ": None,
                           "limit_up": None, "limit_down": None}
    try:
        rows = daily_bars(glb, 8)
        today = None
        for b in reversed(rows):
            t = getattr(b, "time", None)
            if hasattr(t, "strftime"):
                today = t.strftime("%Y%m%d")
                break
        vols = []
        for b in rows:
            t = getattr(b, "time", None)
            d = t.strftime("%Y%m%d") if hasattr(t, "strftime") else ""
            v = getattr(b, "volume_lots", None)
            if v and today and d < today:
                vols.append(float(v))
        if len(vols) >= 5:
            out["avg5"] = sum(vols[-5:]) / 5.0
    except Exception:  # noqa: BLE001
        pass
    try:
        prof = call("helpers.stock_profile_table", [glb])
        rows = getattr(prof, "rows", None) or ()
        if rows:
            out["circ"] = getattr(rows[0], "circulating_shares", None)
    except Exception:  # noqa: BLE001
        pass
    try:
        day = None
        rows = daily_bars(glb, 2)
        if rows:
            t = getattr(rows[-1], "time", None)
            day = t.strftime("%Y%m%d") if hasattr(t, "strftime") else None
        lim = call("helpers.daily_price_limits", [glb], trade_date=day) if day else None
        rows2 = getattr(lim, "rows", None) or ()
        if rows2:
            out["limit_up"] = getattr(rows2[0], "limit_up_price", None)
            out["limit_down"] = getattr(rows2[0], "limit_down_price", None)
    except Exception:  # noqa: BLE001
        pass
    _aux_cache.put(key, out)
    return out


def minute_bars_history(glb: str, date8: str) -> list[dict[str, Any]]:
    """**指定交易日**的历史分时（eltdx `minutes.history`，协议 0x0FB4）→ [{t,o,h,l,c,v,amount,pc}]。

    为什么要单独一个函数（2026-11-02）：`minute_bars()` 只给「最近一个交易日」，
    `minute_bars_days()` 只盖最近 10 日窗口 —— 再往前（几百天前）就全空。
    历史日走本函数：实测 301550.SZ 2026-09-01 可取 240 点（09:31~15:00）。

    字段口径（与 minute_bars 对齐）：
      · 无 amount 字段 → amount = 价格 × 量 推算（与当日分时同链的近似，实测差 0.04%）；
      · volume 单位=手 → ×100 转股（与 1m bars 同口径）；
      · 只有连续竞价段（无 09:25 竞价根，竞价另走 auctions）。
    非交易日 / 超回溯边界 → []（不编造）。
    """
    d8 = "".join(ch for ch in str(date8 or "") if ch.isdigit())[:8]
    if len(d8) != 8:
        return []
    key = f"{glb}:hist:{d8}"
    hit = _bars_cache.get(key, 120.0)
    if hit is not None:
        return hit
    try:
        iso = f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
        res = call("minutes.history", glb, iso)
    except Exception:  # noqa: BLE001 - 不可用 → 空（调用方降级）
        return []
    out: list[dict[str, Any]] = []
    for pt in (getattr(res, "points", None) or []):
        tl = str(getattr(pt, "time_label", "") or "")[:5]
        if len(tl) != 5 or tl[2] != ":":
            continue
        try:
            price = float(getattr(pt, "price", 0) or 0)
            vol_lots = float(getattr(pt, "volume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        v_shares = vol_lots * 100.0
        out.append({"t": f"{d8} {tl}", "o": price, "h": price, "l": price, "c": price,
                    "v": round(v_shares, 2), "amount": round(price * v_shares, 2)})
    try:
        pc = getattr(res, "prev_close", None)
        pc = float(pc) if pc else None
    except (TypeError, ValueError):
        pc = None
    if pc is not None and pc > 0:
        for b in out:
            b["pc"] = pc
    if out:
        _bars_cache.put(key, out)
    return out


def minute_bars_days(glb: str, days: int = 5) -> dict[str, list[Any]]:
    """最近 N 个交易日的分时，按 `YYYYMMDD` 分组（组内升序）。

    走 `bars.get(period="1m")` + **start 偏移翻页**（见 `_bars_page`）。
    不要用 `minutes.history`：那个接口无 amount、无 09:30、无 open，
    均价线只能价×量推，与当日分时（走 1m bars，有真 amount）口径不一致。

    不可用返回空 dict。
    """
    days = max(1, min(10, int(days)))
    key = f"{glb}:md:{days}"
    hit = _bars_cache.get(key, 60.0)
    if hit is not None:
        return hit
    # 一个交易日最多 241 根；多取 20% 防止半天/停牌导致不够
    rows = _bars_page(glb, "1m", int(days * 241 * 1.2))
    out: dict[str, list[Any]] = {}
    for b in rows:
        t = getattr(b, "time", None)
        if not hasattr(t, "strftime"):
            continue
        out.setdefault(t.strftime("%Y%m%d"), []).append(b)
    for d in out:
        out[d].sort(key=lambda b: getattr(b, "time", None))
    keep = sorted(out)[-days:]
    out = {d: out[d] for d in keep}
    if out:
        _bars_cache.put(key, out)
    return out


def _bars_page(glb: str, period: str, count: int) -> list[Any]:
    """一次取 count 根 K 线，**按 start 偏移翻页**。

    2026-10-23 实测：
    - `count<=800` 正常；`count>=900` 直接返回 None（无异常，静默失败）。
    - `bars.get` 有 `start` 参数（偏移）：`start=0/241/482` 分别返回相邻三段。
      **翻页必须靠 start**；只调 count 不动 start，每次都是同一段（早期版本踩过）。
    """
    out: list[Any] = []
    offset = 0
    remaining = count
    while remaining > 0:
        take = min(BARS_PAGE, remaining)
        res = call("bars.get", glb, period=period, start=offset, count=take)
        rows = list(getattr(res, "bars", ()) or ()) if res is not None else []
        if not rows:
            break
        out.extend(rows)
        offset += take
        remaining -= take
        if len(rows) < take:
            break
    return out


# ============================================================================
# 复权事件（除权除息，2026-10-24 M1 B2 新增）
#
# 旧 8800 `GET /api/kline/{ts_code}/xdxr`（`engine/server.py:672`）走 **pytdx**
# `TdxHq_API.get_xdxr_info(market, code)`，逐字段拼成
# `{date, category, type, fenhong, songzhuangu, peigu, peigujia}`（行号 690-704）。
#
# 这里改走 eltdx 的 `corporate.capital_changes`（同一个通达信协议、同一批记录），
# 理由与 `ctx.quotes` 全层一致：插件不得自连行情源，core 只保留一个连接与一套降级。
# 字段映射**实测逐票对齐**（2026-10-24，000001.SZ/600519.SH/300750.SZ/688981.SH/
# 601318.SH/002594.SZ/000858.SZ 七只票 0 差异）：
#   category==1（除权除息）：fenhong=c1、songzhuangu=c3、peigu=c4、peigujia=c2
#                           （pytdx 的同名字段与 eltdx 的 c1..c4 顺序不同，别按序号抄）
#   其余 category：四项一律 None（旧端 pytdx 同样给 None，不是缺数据）
# 与旧端的差异（如实登记，见 docs/HANDOFF_20261024_M1_fix.md）：
#   · 旧端缓存落盘 `夏天-activity/工具/xiaoshi_cache/xdxr/{code}_{YYYYMMDD}.json`；
#     v2 不写该目录（插件写通道受控、cwd 只读），改**进程内内存缓存**，跨自然日自动失效。
#   · 北交所（920xxx 等）：旧端 `_tdx_market()` 返回 None → 空列表；eltdx 有数据 → 正常返回。
#     这是能力增加，不是口径漂移。
#   · 指数码（`000001.SH` 上证 / `399001.SZ` 深成等）：**新版修正旧端缺陷**（2026-10-24 R5）——
#     旧端把 `000001.SH` 判成深市、返回平安银行（sz000001）的 81 条除权记录；
#     v2 对指数返回空数组。指数本就没有除权除息，这是修正不是漂移，
#     故两边**故意不一致**（旧端如需对齐要单独改 `engine/server.py`）。
# ============================================================================

# 事件分类中文（逐字照搬 `engine/server.py:629 _XDXR_CAT`）
XDXR_CATEGORY = {
    1: "除权除息", 2: "送配股上市", 3: "非流通股上市", 4: "未知", 5: "股本变化",
    6: "增发新股", 7: "股份回购", 8: "增发新股上市", 9: "转配股上市",
    10: "可转债上市", 11: "扩缩股", 12: "非流通股缩股", 13: "送认购权证", 14: "送认沽权证",
}
# 只有「除权除息」这一类带分红送配数字（旧端 pytdx 同口径）
_XDXR_DIVIDEND_CATEGORY = 1

_xdxr_cache = _Cache(keep=64)


def _tdx_market(ts_code: str) -> int | None:
    """ts_code → 市场（1=沪, 0=深）；旧端不支持的段返回 None。

    **逐字照搬** `engine/server.py:636 _tdx_market`：只看代码数字前缀，**不看后缀**。
    所以 `000001.SH`（上证指数）会被判成 market=0（深市）——旧端就是这样，
    实际拿的是 sz000001（平安银行）的除权除息记录。

    2026-10-24 R5 修正：这条历史口径本身**不改**（个股/基金/可转债的市场判定必须与旧端一致），
    指数串票的问题在 `xdxr()` 入口用 `_is_index()` 提前拦掉（指数无除权除息）。
    这样 `000001.SZ`（平安银行）不受影响，`000001.SH`（上证指数）不再拿到别人的记录。
    """
    c = str(ts_code or "").split(".")[0]
    # 北交所 920xxx：pytdx 该路径不支持（旧注：此前 “9” 开头误判沪市）
    if c.startswith("920"):
        return None
    if c.startswith(("60", "68", "9", "5")):
        return 1
    if c.startswith(("00", "30", "12", "15", "16", "18", "20", "39")):
        return 0
    return None


# 指数码识别（2026-10-24 R5 修 M1 遗留）：
#   · `.SH` + `000xxx`：上证/中证系列指数（000001 上证指数、000300 沪深300…）
#   · `.SZ` + `399xxx`：深证系列指数（399001 深成指、399006 创业板指…）
#   · 无后缀的 `880xxx` / `881xxx`：通达信板块指数（旧端判不出市场 → 本就返回空表，这里一并锁住）
# **必须带后缀判断**：`000001.SZ` 是平安银行（个股），只有 `000001.SH` 才是指数。
_INDEX_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SH", ("000",)),
    ("SZ", ("399",)),
)
_INDEX_BARE_PREFIX: tuple[str, ...] = ("880", "881")


def _is_index(ts_code: str) -> bool:
    """是否指数码（指数无除权除息 → `xdxr()` 直接返回空表）。

    判据带后缀：只看数字前缀会把 `000001.SZ`（平安银行）也误判成指数。
    不带后缀的 `000001` 不算指数——旧端会当深市个股取平安银行记录，保持原行为。
    """
    raw = str(ts_code or "").strip()
    body, _, suffix = raw.partition(".")
    body, sfx = body.strip(), suffix.strip().upper()
    if len(body) != 6 or not body.isdigit():
        return False
    for market, prefixes in _INDEX_RULES:
        if sfx == market:
            return any(body.startswith(p) for p in prefixes)
    if not sfx:
        return body.startswith(_INDEX_BARE_PREFIX)
    return False


def _xdxr_event(rec: Any) -> dict[str, Any] | None:
    """eltdx CapitalChangeRecord → 旧端事件 dict；日期非法 → None（旧端同样跳过）。"""
    try:
        y, m, d = int(getattr(rec, "date_raw")) // 10000, \
            int(getattr(rec, "date_raw")) // 100 % 100, int(getattr(rec, "date_raw")) % 100
    except (TypeError, ValueError):
        date = getattr(rec, "date", None)
        if not hasattr(date, "strftime"):
            return None
        y, m, d = date.year, date.month, date.day
    if not (1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    cat = getattr(rec, "category_raw", None)
    out: dict[str, Any] = {
        "date": "%04d%02d%02d" % (y, m, d),
        "category": cat,
        "type": XDXR_CATEGORY.get(cat, str(cat)),
        "fenhong": None, "songzhuangu": None, "peigu": None, "peigujia": None,
    }
    if cat == _XDXR_DIVIDEND_CATEGORY:
        out.update({
            "fenhong": getattr(rec, "c1_float", None),
            "songzhuangu": getattr(rec, "c3_float", None),
            "peigu": getattr(rec, "c4_float", None),
            "peigujia": getattr(rec, "c2_float", None),
        })
    return out


def xdxr(ts_code: str) -> list[dict[str, Any]]:
    """除权除息事件列表（按日期升序，字段与旧 8800 同名端点逐键一致）。

    指数（`000001.SH` / `399001.SZ` 等）**直接返回 `[]`**：指数没有除权除息，
    而旧端 `_tdx_market` 只看数字前缀，会把 `000001.SH` 判成深市去取 `sz000001`
    （平安银行）的记录——2026-10-24 R5 修掉这条（见 `_is_index`）。
    个股/基金/可转债仍走 `_tdx_market()`（逐字照搬旧端，含“只看数字前缀”这一历史口径）；
    数据源 eltdx `corporate.capital_changes`（同一份通达信协议记录，字段映射见模块注释）。
    不可用 / 市场不支持 → `[]`（端点回空 events，**不编造**）。
    缓存按 (标的, 自然日)：旧端也是「当日有效」，第二天要重新取（除权公告会更新）。
    """
    if _is_index(ts_code):
        return []
    market = _tdx_market(ts_code)
    raw = str(ts_code or "").split(".")[0]
    if market is None or not raw:
        return []
    glb = ("sh" if market == 1 else "sz") + raw
    key = "%s:%s" % (glb, time.strftime("%Y%m%d"))
    hit = _xdxr_cache.get(key, 12 * 3600.0)
    if hit is not None:
        return list(hit)
    block = call("corporate.capital_changes", glb)
    recs = list(getattr(block, "records", None) or ()) if block is not None else []
    out: list[dict[str, Any]] = []
    for rec in recs:
        ev = _xdxr_event(rec)
        if ev is not None:
            out.append(ev)
    out.sort(key=lambda e: e["date"])
    if out:
        _xdxr_cache.put(key, out)
    return out


def reset() -> None:
    """测试用：清缓存并丢弃连接。"""
    _depth_cache.clear()
    _ticks_cache.clear()
    _bars_cache.clear()
    _aux_cache.clear()
    _order_metrics_cache.clear()
    _xdxr_cache.clear()
    _f10_cache.clear()
    _f10_state["fail_streak"] = 0
    _f10_state["skip_until"] = 0.0
    with _LOCK:
        _drop_client()
    with _F10_LOCK:
        _F10_CLIENT["cli"] = None


# ============================================================================
# F10 HTTP 客户端（7615 TQLEX）—— B2 段（估值/市值/北向/盈利预测/增减持/融资融券/季度业绩）
#
# 旧 8800 `server.py::_eltdx_f10_brief()` 的 B2 半段，逐项移植（同一 eltdx `F10Client`、
# 同一 Entry/参数/字段名）。为什么放在门面：B2 走的是 **HTTP** 而不是 7709 socket，
# 但同样属于「行情源访问」——插件不得自己 `from eltdx import F10Client`（CONTRACT §4.1
# 静态扫描会拦），所以与 depth/ticks 一样由 core 统一持有、统一缓存、统一降级。
#
# 口径约束（照搬旧端，禁改）：
#   · PE/PB/市值唯一来源 = valuation 200191，数据在 `result_sets[1]`（`response.rows` 只有 table0）；
#     字段名反直觉：AVGMVM=**流通**市值、ALIQMV=**总**市值；**禁止**用 EPS 现算冒充 TTM。
#   · 任一项失败 → 该项 None/[]，不补 0、不编造（前端按 None 显示「—」）。
#   · 每次调用失败重试 1 次（同旧端 `_f10_http_call`：总预算 ~5s，禁止第三次）。
#   · TTL 60s：估值/股东/预测这类静态数据日内不变，60s 足够挡住一张卡反复刷新。
# ============================================================================

# F10 单次 HTTP 超时（旧端 _F10_HTTP_TIMEOUT 同值）。
# ⚠ 语义注意：eltdx 的 `_connect_ipv4_first` 会**遍历全部解析地址**，每个地址各消耗一次本超时，
# 所以实际最坏耗时 = timeout × 地址数（域名解析出 IPv6+IPv4 时为 2 倍）。见本文件 F10_ENDPOINTS。
F10_HTTP_TIMEOUT = 2.5

# ---------------------------------------------------------------- F10 服务端选优（2026-10-27）
#
# 为什么需要：域名 `static.tdx.com.cn` 解析出 IPv6（本机不可达）+ IPv4，eltdx 会**逐个尝试**，
# 每个地址吃满一次 timeout → 最坏 16s（实测 16047ms）。直连 IPv4 字面量后只解析出一条记录，
# 该损耗消失。
#
# 为什么带 Host 头：直连 IP 会丢掉 vhost 路由，服务端按 Host 分流 TQLEX 入口。
#
# 候选清单（(base_url, host_header, 说明)）：
#   · static.tdx 的 IPv4 —— 现用，9 段全通（实测合计 1704ms）
#   · hot.icfqs 的 IPv4 —— 更快（718ms）但 9 段里 7 段 503，只服务涨停梯队，
#     **不作为主用**，仅留档说明为什么不用它
F10_ENDPOINTS = [
    ("http://49.232.135.168:7615/TQLEX", "static.tdx.com.cn", "static.tdx IPv4 直连"),
    ("http://static.tdx.com.cn:7615/TQLEX", "static.tdx.com.cn", "static.tdx 域名（兜底）"),
]

# 为何不计入 hot.icfqs（实测记录，勿再重复试错）：
#   它响应更快（8 段合计 718ms vs static 的 1704ms），但 9 段里 **7 段 503**
#   （gdyj/zlcc/ybpj/ggzp/cwfx… 全缺），只服务涨停梯队那一个 Entry。
#   按「能取到值」而非「能连上」判定时它会被自然排除；此处不单列为候选以免误选。
# 探测间隔（秒）。F10 是日级数据，链路质量不会分秒级变化；15 分钟足够。
F10_PROBE_TTL = 900.0
# 探测结果落盘：进程重启后 TTL 内直接复用，否则每次重启都要重探（约 500ms 开销，
# 会把冷查首屏从 250ms 抬到 890ms，直接冲破主人定的 500ms 目标）。
F10_ENDPOINT_STATE = WS / "data" / "cache" / "f10_endpoint.json"
# 单次探测超时（要比业务超时更短，探测本身不该拖慢任何请求）
F10_PROBE_TIMEOUT = 2.0
# 选优偏好阈值（ms）：
#   实测 IP 直连与域名差距在 40–60ms（同一量级、方向不稳定），但 IP 直连消除了
#   「IPv6 不可达时逐个地址等满 timeout」这个 16 秒级风险。所以 **差距不超过本阈值时
#   优先 IP 直连**（确定性优先）；超过才真选更快的。
F10_PROBE_PREFER_MS = 200.0
# B2 整批总预算（秒）：9 个 Entry 串行，每项最多 2 次尝试。超预算后剩余项直接留 None，
# 不把插件拖到超时（compat_kline 的 backend.timeout_ms = 20000）。
F10_BUDGET = 12.0
# B2 段内存 TTL。2026-10-26 起 B2 主要靠**落盘缓存 + 交易日失效**（见 F10_B2_CACHE_DIR），
# 这个内存 TTL 只是同一请求内的二次挡板，给它一个远大于一天的值即可，不再承担跨天判断。
F10_TTL = 86400.0
# B1 段（资金流：main_net_today / main_ratio_today / main_net_5d）内存 TTL。
# 资金流是**盘中会变**的，不能跟 B2 一样按天缓存；5 分钟粒度足够
# （主人 2026-10-26：「5 分钟内 F10 根本不会变化」——对 B2 成立，对资金流按 5min 取保守值）。
F10_B1_TTL = 300.0
# B2 落盘缓存目录（按交易日失效；删目录 = 强制全量刷新）
F10_B2_CACHE_DIR = None   # 由下方 _f10_b2_dir() 按 paths.WS 惰性解析，避免 import 期依赖
# 熔断：连续整批失败达到此次数后，F10_SKIP_TTL 秒内不再尝试（避免每次刷新都白等超时）。
F10_FAIL_STREAK = 2
F10_SKIP_TTL = 120.0

_f10_cache = _Cache(keep=64)        # B2 内存层（交易日级别的落盘缓存之上再加一层）
_f10_b1_cache = _Cache(keep=256)    # B1 资金流（5min TTL，不落盘）
_f10_state: dict[str, Any] = {"fail_streak": 0, "skip_until": 0.0}


def _f10_b2_dir() -> Optional[str]:
    """B2 落盘缓存目录。

    `F10_B2_CACHE_DIR` 为 None → 按 paths 默认值解析（并缓存到模块变量）；
    非 None → **直接用**（测试/排障可 monkeypatch 指向临时目录）；
    空串 → 禁用落盘（调用方退回纯内存）。
    """
    global F10_B2_CACHE_DIR
    if F10_B2_CACHE_DIR is not None:
        return F10_B2_CACHE_DIR or None
    try:
        d = os.path.join(str(WS), "data", "cache", "f10_b2")
        os.makedirs(d, exist_ok=True)
        F10_B2_CACHE_DIR = d
        return d
    except Exception:  # noqa: BLE001 - 目录建不出来就别用落盘
        F10_B2_CACHE_DIR = ""
        return None


def _today_str() -> str:
    """缓存归属交易日。

    ⚠ 不用 `now().strftime('%Y%m%d')`：那会让**每天 0 点整**所有 F10 同时失效——
    而面板是常驻后台、凌晨也开着，突然全体重拉（实测踩到：23:57 写的缓存，
    00:02 就被判过期）。

    改为「**凌晨 8 点前算前一天**」：F10 是日级基本面数据，08:00 前不存在「今天的
    数据已经更新」这回事；跨过 08:00 才真正换交易日。开盘（09:30）前仍有 1.5 小时余量。
    """
    now = datetime.now()
    if now.hour < 8:
        now = now - timedelta(days=1)
    return now.strftime("%Y%m%d")


def _f10_b2_clear(glb: str) -> None:
    """删掉单个代码的 B2 落盘缓存（测试隔离 / 排障时强制重拉）。"""
    d = _f10_b2_dir()
    if not d:
        return
    try:
        p = os.path.join(d, "%s.json" % glb)
        if os.path.exists(p):
            os.remove(p)
    except Exception:  # noqa: BLE001 - 删不掉不影响主流程
        pass


def _f10_b2_load(glb: str) -> Optional[dict]:
    """读 B2 落盘缓存；只有**同一交易日**才算命中。损坏/缺日 → None。"""
    d = _f10_b2_dir()
    if not d:
        return None
    p = os.path.join(d, "%s.json" % glb)
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001 - 缺失/损坏都当没有
        return None
    if not isinstance(doc, dict):
        return None
    if str(doc.get("trade_date") or "") != _today_str():
        return None                      # 跨交易日 → 失效，重新拉
    payload = doc.get("payload")
    if not isinstance(payload, dict):
        return None
    # 兼容：该字段是 2026-10-27 才加的，旧缓存文件里没有。
    # 落盘时 `saved_at` 就是那次的取数时间，直接用它补——不把旧缓存当无效（否则升级即全量重拉）。
    if not payload.get("fetched_at"):
        payload = dict(payload)
        payload["fetched_at"] = doc.get("saved_at")
    return payload


def _f10_b2_save(glb: str, payload: dict) -> None:
    d = _f10_b2_dir()
    if not d:
        return
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s.json" % glb)
    tmp = p + ".tmp"
    try:
        doc = {"glb": glb, "trade_date": _today_str(),
               "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "payload": payload}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 - 落盘失败只影响下次冷查速度
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass
# ⚠ 必须是 RLock（可重入），不能是 Lock。
#
# 这把锁同时保护两件事：
#   ① `_f10_no_proxy()` 的环境变量修改（需在**整个 F10 调用期间**持有，防并发还原）
#   ② `_f10_client()` / `_f10_endpoint()` 的单例与 endpoint
# 而调用链是嵌套的：`_f10_no_proxy()`（拿锁）→ `_f10_call()` → `_f10_client()`（再拿锁）。
# 用普通 `threading.Lock` 就会自锁死——2026-10-27 踩过：所有 F10 调用 100% 超时，
# 查了半天以为是网络间歇超时，实际是本文件自己死锁。
_F10_LOCK = threading.RLock()
_F10_CLIENT: dict[str, Any] = {"cli": None}

# 需要绕开系统代理的 F10 网关主机（见 `_f10_no_proxy`）
_F10_HOSTS = ("static.tdx.com.cn", "hot.icfqs.com")

# ----------------------------------------------------------------------------
# THS 指数原生周线源（S2 接缝）
#
# 旧 8800 `engine/server.py::_ths_index_bars_mcp` 同源同参数；插件侧禁一切出站调用
# （`docs/CONTRACT.md` §8），所以上游 HTTP 落在这里，插件通过
# `ctx.quotes.ths_index_weekly(code, start8, end8)` 拿已解析的 bars。
# 凭据从用户配置读，**不落盘、不进日志、不进异常文案**。
# ----------------------------------------------------------------------------
THS_INDEX_MCP_URL = "https://fuyao.aicubes.cn/mcp/a-share-index"
THS_INDEX_MCP_HOST = "fuyao.aicubes.cn"
THS_CFG_FILE = WS / "夏天-activity" / "工具" / "hithink_config.json"


def _f10_float(v: Any) -> float | None:
    """F10 数值（字符串/数字）→ float；空/非法 → None（不补 0）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if _finite(f) else None


def _f10_date(v: Any) -> str | None:
    """'20261019' / '2026-10-19' / date 对象 → 'YYYY-MM-DD'；空/非法 → None。"""
    if v is None or v == "":
        return None
    s = str(v)[:10]
    d = "".join(ch for ch in s if ch.isdigit())
    if len(d) >= 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return s or None


def _f10_year(v: Any) -> int | None:
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    return int(d[:4]) if len(d) >= 4 else None


@contextlib.contextmanager
def _no_proxy_hosts(hosts: Any):
    """调用期间让 `hosts` 里的主机**不走系统代理**（通用实现，F10 与 THS 共用）。

    背景（2026-10-24 S1 实测，这就是「F10 经常不可用」的真因）：本机用户环境里有
    `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897`（Clash 类代理）。8800 是计划任务/常驻
    进程、环境里**没有**这两个变量，所以它调 F10 一直正常；8810 从带代理环境的会话拉起，
    urllib 每次请求都先连 127.0.0.1:7897——代理一关就是 `WinError 10061 积极拒绝`，
    B2 段全部 15 个字段变 null。THS 指数网关（fuyao.aicubes.cn）同一个坑。

    做法：调用期间把这些主机追加进 `no_proxy/NO_PROXY`（`urllib.request.getproxies()` 每次
    调用都重读环境变量，追加即生效），退出时还原。加锁保护，不改全局 socket、不改 opener。

    ⚠ Windows 的 `os.environ` **大小写不敏感**（写 `no_proxy` 即写 `NO_PROXY`，两个名字
    指向同一个值），所以这里不需要（也不应该）分别处理两个名字：读一个即可，写一个即可。
    """
    with _F10_LOCK:
        # 大小写敏感平台（Linux/macOS）两个名字可能不同，取并集；写回时两个都写。
        saved = {k: os.environ.get(k) for k in ("no_proxy", "NO_PROXY")}
        cur = (saved.get("no_proxy") or saved.get("NO_PROXY") or "")
        parts = [p.strip() for p in cur.split(",") if p.strip()]
        low = {p.lower() for p in parts}
        added = [h for h in hosts if str(h).lower() not in low]
        if added:
            merged = ",".join(parts + added)
            os.environ["no_proxy"] = merged
            os.environ["NO_PROXY"] = merged
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


@contextlib.contextmanager
def _f10_no_proxy():
    """F10 调用期间让 7615 网关**不走系统代理**（= `_no_proxy_hosts(_F10_HOSTS)` 薄包装）。

    行为与泛化前完全一致；语义说明见 `_no_proxy_hosts`。
    """
    with _no_proxy_hosts(_F10_HOSTS):
        yield


# 当前选中的 endpoint（探测结果）：{"base_url":..., "host":..., "label":..., "probed_at":...}
_F10_ENDPOINT: dict[str, Any] = {}


def _f10_build_client(base_url: str, host: str) -> Any:
    """按 endpoint 建一个 F10Client；失败 → None。"""
    try:
        if str(ELTDX_DIR) not in sys.path:
            sys.path.insert(0, str(ELTDX_DIR))
        from eltdx import F10Client  # noqa: PLC0415 - 懒引入：不可用即降级

        kwargs: dict[str, Any] = {"timeout": F10_HTTP_TIMEOUT}
        if host:
            # 直连 IP 时必须带 Host，否则服务端按 vhost 分流找不到 TQLEX
            kwargs["headers"] = {"Host": host}
        return F10Client(base_url=base_url, **kwargs)
    except Exception:  # noqa: BLE001 - 环境缺 eltdx：降级
        return None


def _f10_probe_endpoints() -> dict[str, Any]:
    """逐个候选发一次轻量 valuation，按「能取到值」判可用，再按下列规则选一：

    1. 清单**靠前**的候选只要能取到值，且不比最快者慢超过 `F10_PROBE_PREFER_MS`，就选它
       ——清单顺序已按「确定性优先」排好（IP 直连在域名之前）；
    2. 否则选最快的；
    3. 全失败 → 用清单最后一条（域名兜底），行为回到改造前，不会更差。

    判据是「能取到值」而不是「能连上」：hot.icfqs 那台连得上但 7/9 段 503，
    单看连通性会误判成最优。
    """
    results: list[tuple[int, str, str, float, str]] = []   # (序号, url, host, ms, label)
    tried: list[str] = []
    for idx, (base_url, host, label) in enumerate(F10_ENDPOINTS):
        cli = _f10_build_client(base_url, host)
        if cli is None:
            tried.append("%s: client_unavailable" % label)
            continue
        t0 = time.monotonic()
        try:
            resp = cli.valuation("sh600519", req_id="200191")
            rows = list(getattr(resp, "rows", None) or ())
            ms = (time.monotonic() - t0) * 1000
            if not rows:
                tried.append("%s: empty" % label)
                continue
            tried.append("%s: %.0fms" % (label, ms))
            results.append((idx, base_url, host, ms, label))
        except Exception as exc:  # noqa: BLE001 - 单点失败不影响其它候选
            tried.append("%s: %s" % (label, type(exc).__name__))
            continue

    if not results:
        base_url, host, label = F10_ENDPOINTS[-1]
        best: dict[str, Any] = {"base_url": base_url, "host": host, "label": label,
                                "probe_ms": None, "probed_at": time.monotonic(),
                                "tried": tried, "degraded": True}
    else:
        fastest = min(results, key=lambda r: r[3])
        # 清单靠前者只要「不比最快慢太多」就用它（确定性优先）
        pick = fastest
        for row in results:
            if row[3] <= fastest[3] + F10_PROBE_PREFER_MS:
                pick = row
                break
        best = {"base_url": pick[1], "host": pick[2], "label": pick[4],
                "probe_ms": round(pick[3]), "probed_at": time.monotonic(),
                "tried": tried,
                "fastest": fastest[4], "fastest_ms": round(fastest[3])}
    _F10_ENDPOINT.clear()
    _F10_ENDPOINT.update(best)
    _f10_endpoint_save(best)
    logger.warning("F10 endpoint 选优 → %s（%sms）；候选：%s",
                   best["label"], best.get("probe_ms"), " | ".join(tried))
    return best


def _f10_endpoint_load() -> dict[str, Any] | None:
    """从磁盘读上次的探测结果；TTL 内有效才返回。

    为什么要落盘：探测一次约 500ms（2 个候选 × ~250ms）。不落盘的话每个进程重启
    都要重探，首次打开弹窗就会从 250ms 抬到 890ms——直接冲破 500ms 目标。
    """
    try:
        raw = json.loads(F10_ENDPOINT_STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 文件不存在/格式坏都当作未缓存
        return None
    if not isinstance(raw, dict) or not raw.get("base_url"):
        return None
    age = time.time() - float(raw.get("probed_at_epoch") or 0)
    if age < 0 or age >= F10_PROBE_TTL:
        return None
    raw["probed_at"] = time.monotonic() - age   # 转回本进程的 monotonic 基准
    return raw


def _f10_endpoint_save(ep: dict[str, Any]) -> None:
    """探测结果落盘（失败不影响功能，只是下次要重探）。"""
    try:
        F10_ENDPOINT_STATE.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in ep.items() if k != "probed_at"}
        payload["probed_at_epoch"] = time.time()
        F10_ENDPOINT_STATE.write_text(json.dumps(payload, ensure_ascii=False),
                                      encoding="utf-8")
    except Exception:  # noqa: BLE001 - 落盘失败无妨
        logger.debug("F10 endpoint 落盘失败", exc_info=True)


def _f10_endpoint() -> dict[str, Any]:
    """取当前 endpoint；过期或从未探测 → 先探一次。"""
    cur = _F10_ENDPOINT
    if cur and (time.monotonic() - float(cur.get("probed_at") or 0)) < F10_PROBE_TTL:
        return cur
    with _F10_LOCK:
        cur = _F10_ENDPOINT
        if cur and (time.monotonic() - float(cur.get("probed_at") or 0)) < F10_PROBE_TTL:
            return cur
        disk = _f10_endpoint_load()
        if disk is not None:
            _F10_ENDPOINT.clear()
            _F10_ENDPOINT.update(disk)
            return disk
        return _f10_probe_endpoints()


def _f10_client() -> Any:
    """懒建单例 F10 HTTP 客户端（不可用 → None，调用方降级）。

    2026-10-27：改为按探测选中的 endpoint 建（IPv4 直连 + Host 头），
    避免 eltdx 逐个尝试地址、IPv6 不可达时白等一整轮 timeout。
    endpoint 变了（重新探测换了服务器）就重建客户端。
    """
    ep = _f10_endpoint()
    cli = _F10_CLIENT.get("cli")
    if cli is not None and _F10_CLIENT.get("base_url") == ep.get("base_url"):
        return cli
    with _F10_LOCK:
        # ⚠ 此处**不得**再调 _f10_endpoint()：它内部也拿 _F10_LOCK，
        # 而 _F10_LOCK 是 threading.Lock（不可重入）→ 直接死锁。
        # 2026-10-27 踩过：quick 端点 100% 超时、_f10_after 卡死二分多钟都源于此。
        # 已持锁时只做双检与重建；探测由 _f10_probe_endpoints() 完成（它不拿锁）。
        ep = _F10_ENDPOINT
        if not ep or (time.monotonic() - float(ep.get("probed_at") or 0)) >= F10_PROBE_TTL:
            ep = _f10_probe_endpoints()
        cli = _F10_CLIENT.get("cli")
        if cli is not None and _F10_CLIENT.get("base_url") == ep.get("base_url"):
            return cli
        cli = _f10_build_client(ep.get("base_url") or "", ep.get("host") or "")
        _F10_CLIENT["cli"] = cli
        _F10_CLIENT["base_url"] = ep.get("base_url")
        return cli


def _f10_call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    """调 F10 的一个方法/Entry，失败重试 1 次（同旧端 `_f10_http_call`）。仍失败抛异常。

    `fn_name` 形如 `"valuation"` / `"call"`（点号取属性，与 `call()` 同约定）。
    """
    cli = _f10_client()
    if cli is None:
        raise RuntimeError("F10Client 不可用")
    last: Exception | None = None
    for _ in (0, 1):
        try:
            target = cli
            for part in fn_name.split("."):
                target = getattr(target, part)
            return target(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - ProtocolError/TransportError/ValueError 全兜
            last = exc
    raise last if last is not None else RuntimeError("f10 call failed")


def _f10_rows(resp: Any) -> list[dict[str, Any]]:
    """F10Response → 首表行列表（无表/空表 → []）。"""
    return list(getattr(resp, "rows", None) or ())


def _f10_tables(resp: Any) -> list[Any]:
    return list(getattr(resp, "result_sets", None) or ())


# ---- 各 Entry 的解析（逐字移植旧 8800 server.py 同名函数）


def _f10_parse_valuation(resp: Any) -> dict[str, Any]:
    """估值 200191 → 6 字段；数据在 `result_sets[1]`。"""
    out: dict[str, Any] = {"pe_ttm": None, "pe_pct": None, "pb_mrq": None,
                           "pb_pct": None, "total_mcap": None, "float_mcap": None}
    tables = _f10_tables(resp)
    if len(tables) < 2 or not getattr(tables[1], "rows", None):
        return out
    row = tables[1].rows[0]
    out["pe_ttm"] = _f10_float(row.get("PETTM"))
    out["pe_pct"] = _f10_float(row.get("PEBFW"))
    out["pb_mrq"] = _f10_float(row.get("PBMRQ"))
    out["pb_pct"] = _f10_float(row.get("PBBFW"))
    out["float_mcap"] = _f10_float(row.get("AVGMVM"))    # 反直觉：AVGMVM = 流通市值
    out["total_mcap"] = _f10_float(row.get("ALIQMV"))    # 反直觉：ALIQMV = 总市值
    return out


def _f10_parse_plans(resp: Any, keep: int = 2) -> list[dict[str, Any]]:
    """增减持计划（N001 公告日 / N002 方向 / N003 股东 / N004 身份 / N011 状态）；新→旧取 2 条。"""
    out: list[dict[str, Any]] = []
    for row in _f10_rows(resp):
        d = _f10_date(row.get("N001"))
        if not d:
            continue
        out.append({"date": d,
                    "dir": str(row.get("N002") or "") or None,
                    "who": str(row.get("N003") or "") or None,
                    "role": str(row.get("N004") or "") or None,
                    "status": str(row.get("N011") or "") or None})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:keep]


def _f10_parse_north(resp: Any) -> dict[str, Any] | None:
    """北向季度持股（N001 日期 / N002 比例% / N005 较上期变化%）；0 行 → None。"""
    rows = _f10_rows(resp)
    if not rows:
        return None
    r0 = rows[0]
    return {"date": _f10_date(r0.get("N001")),
            "ratio": _f10_float(r0.get("N002")),
            "chg_pct": _f10_float(r0.get("N005"))}


def _f10_parse_forecast(resp: Any) -> dict[str, Any] | None:
    """盈利预测：result_sets[1] 未来三年 EPS（T036/37/38，取第一个非空），result_sets[4] 机构数 t023。"""
    tables = _f10_tables(resp)
    eps = None
    org = None
    year = None
    if len(tables) > 1 and tables[1].rows:
        row = tables[1].rows[0]
        for col in ("T036", "T037", "T038"):
            v = _f10_float(row.get(col))
            if v is not None:
                eps = v
                break
    if len(tables) > 4 and tables[4].rows:
        org = _f10_float(tables[4].rows[0].get("t023"))
        year = _f10_year(tables[4].rows[0].get("rq"))
    if eps is None and org is None:
        return None
    return {"year": year, "eps": eps,
            "org_count": int(org) if org is not None else None}


def _f10_parse_sector(resp: Any) -> str | None:
    """行业：stock_score N012（无该列 → None，不硬编码）。"""
    rows = _f10_rows(resp)
    if rows and rows[0].get("N012"):
        return str(rows[0]["N012"])
    return None


def _f10_parse_rzrq(resp: Any, keep: int = 8) -> list[dict[str, Any]]:
    """融资融券（stock_score section="rzrq"，新→旧）：N001 日期 / N002 融资净买入 / N003 融资余额 /
    N004 融资余量 / N005 融券余额。0 行（非两融标的）→ []。"""
    out: list[dict[str, Any]] = []
    for row in _f10_rows(resp)[:keep]:
        d8 = "".join(ch for ch in str(row.get("N001") or "") if ch.isdigit())[:8]
        if len(d8) != 8:
            continue
        out.append({"date": f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}",
                    "rz_net_buy": _f10_float(row.get("N002")),
                    "rz_balance": _f10_float(row.get("N003")),
                    "rz_vol": _f10_float(row.get("N004")),
                    "rq_balance": _f10_float(row.get("N005"))})
    return out


def _f10_parse_perf(resp: Any) -> dict[str, Any] | None:
    """业绩历史（profit_forecast 5 表）：t2 历史实际 + t1 未来预测 + t0 起始年。

    t2：T002 年份 / T012 营业收入（万元，缺则 T003 营业总收入）/ T018 归母净利润（万元）；
    t1：T021/T022/T023 营收预测、T033/T034/T035 净利预测（行序 = t0.nyear 起 +0/+1/+2）。
    输出单位：亿元（万元/1e4）。同比只对有前值的算（None 传染 → null）。
    """
    tables = _f10_tables(resp)
    out: dict[str, list[Any]] = {"years": [], "revenue": [], "net": [],
                                 "yoy_rev": [], "yoy_net": []}
    hist: list[dict[str, Any]] = []
    if len(tables) > 2:
        for row in tables[2].rows:
            y = _f10_year(row.get("T002"))
            if y is None:
                continue
            rev = _f10_float(row.get("T012"))
            if rev is None:
                rev = _f10_float(row.get("T003"))
            hist.append({"year": y, "revenue": rev, "net": _f10_float(row.get("T018"))})
    hist.sort(key=lambda x: x["year"])
    for h in hist:
        out["years"].append(h["year"])
        out["revenue"].append(round(h["revenue"] / 1e4, 4) if h["revenue"] is not None else None)
        out["net"].append(round(h["net"] / 1e4, 4) if h["net"] is not None else None)
    if len(tables) > 1 and tables[0].rows and tables[1].rows:
        base_year = _f10_year(tables[0].rows[0].get("nyear"))
        row = tables[1].rows[0]
        if base_year is not None:
            for off, (rev_k, net_k) in enumerate((("T021", "T033"), ("T022", "T034"),
                                                 ("T023", "T035"))):
                y = base_year + off
                if y in out["years"]:
                    continue      # 历史已有同年实际值：不覆盖（宁少显示不错位）
                rev = _f10_float(row.get(rev_k))
                net = _f10_float(row.get(net_k))
                out["years"].append(y)
                out["revenue"].append(round(rev / 1e4, 4) if rev is not None else None)
                out["net"].append(round(net / 1e4, 4) if net is not None else None)
    order = sorted(range(len(out["years"])), key=lambda i: out["years"][i])
    for k in ("years", "revenue", "net"):
        out[k] = [out[k][i] for i in order]
    yoy_r: list[Any] = []
    yoy_n: list[Any] = []
    for i in range(len(out["years"])):
        if i == 0 or out["revenue"][i - 1] in (None, 0) or out["revenue"][i] is None:
            yoy_r.append(None)
        else:
            yoy_r.append(round((out["revenue"][i] / out["revenue"][i - 1] - 1) * 100, 2))
        if i == 0 or out["net"][i - 1] in (None, 0) or out["net"][i] is None:
            yoy_n.append(None)
        else:
            yoy_n.append(round((out["net"][i] / out["net"][i - 1] - 1) * 100, 2))
    out["yoy_rev"], out["yoy_net"] = yoy_r, yoy_n
    if not any(v is not None for v in out["net"] + out["revenue"]):
        return None
    return out


def _f10_parse_perf_q(resp: Any, keep: int = 12, agg_keep: int = 24) -> dict[str, Any] | None:
    """季度单季业绩（`CWServ.tdxf10_gg_cwfx` params=[code6,"zyzb",""] 的 t0 表，已是单季口径）：
    T002 报告期 / jyr 单季净利（元→亿）/ jlrtbzzl 净利同季同比% / yysrtb 营收同季同比% /
    mgsy 单季 EPS / xsmll 毛利率%。

    `periods` 严格 12 季（前端契约）；`years` 用最近 24 季按年聚合 `{年: {sum, count}}`
    （sum 单位亿元，只计有值季，缺值不补 0）。
    """
    tables = _f10_tables(resp)
    if not tables or not getattr(tables[0], "rows", None):
        return None
    out: list[dict[str, Any]] = []
    for row in tables[0].rows:
        rq = _f10_date(row.get("T002"))
        if not rq:
            continue
        d = "".join(ch for ch in rq if ch.isdigit())
        if len(d) != 8:
            continue
        year, month = int(d[:4]), int(d[4:6])
        net = _f10_float(row.get("jyr"))
        out.append({"rq": rq, "year": year, "q": (month + 2) // 3,
                    "net": round(net / 1e8, 4) if net is not None else None,
                    "yoy_net": _f10_float(row.get("jlrtbzzl")),
                    "rev_yoy": _f10_float(row.get("yysrtb")),
                    "eps": _f10_float(row.get("mgsy")),
                    "gross": _f10_float(row.get("xsmll"))})
    if not out:
        return None
    out.sort(key=lambda x: x["rq"], reverse=True)      # 新→旧
    years: dict[str, dict[str, Any]] = {}
    for item in out[:agg_keep]:
        if item["net"] is None:
            continue
        slot = years.get(str(item["year"]))
        if slot is None:
            years[str(item["year"])] = {"sum": round(float(item["net"]), 2), "count": 1}
        else:
            slot["sum"] = round(slot["sum"] + float(item["net"]), 2)
            slot["count"] += 1
    out = out[:keep]
    if not any(x["net"] is not None for x in out):
        return None
    return {"periods": out, "count": len(out), "years": years}


def _f10_parse_gdhs(resp: Any, keep: int = 12) -> dict[str, Any] | None:
    """股东户数历史（`CWServ.tdxf10_gg_gdyj` params=[code6,"gdrs","","","1","1","40"]）：
    T002 报告期 / T003 户数 / T004 户均持股 / T005 户数环比% / T006 户均环比% / T007 期末价。"""
    out: list[dict[str, Any]] = []
    for row in _f10_rows(resp):
        rq = _f10_date(row.get("T002"))
        if not rq or len("".join(ch for ch in rq if ch.isdigit())) != 8:
            continue
        holders = _f10_float(row.get("T003"))
        if holders is None or holders <= 0:
            continue
        out.append({"rq": rq, "holders": int(round(holders)),
                    "avg_hold": _f10_float(row.get("T004")),
                    "chg_pct": _f10_float(row.get("T005")),
                    "avg_chg_pct": _f10_float(row.get("T006")),
                    "close": _f10_float(row.get("T007"))})
    if not out:
        return None
    out.sort(key=lambda x: x["rq"], reverse=True)
    out = out[:keep]
    return {"periods": out, "count": len(out)}


def f10_valuation_only(glb: str) -> dict[str, Any]:
    """**轻量 F10**：只拉 valuation（PE/PB/市值），其余段不碰。

    为什么单独开一个（主人 2026-10-27 「首屏 500ms 内」）：
    完整 F10 是 9 个串行请求（合计 1.7–8s，随链路波动）；而 valuation 单项只需 ~200–280ms。
    弹窗首屏只用这个把「关键估值」先显出来，其余 8 段再异步补。

    缓存：与 B2 同一份磁盘缓存（命中则 1ms）；本函数不写缓存，只读。
    """
    key = str(glb or "")
    if not key:
        return {}
    disk = _f10_b2_load(key)
    if disk is not None:
        return {"val": disk.get("val") or {}, "fetched_at": disk.get("fetched_at")}
    val = _step_valuation(key)
    return {"val": val or {}, "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _step_valuation(key: str) -> dict[str, Any]:
    """只跑 valuation 一步（不写缓存、不动熔断计数）。"""
    out: dict[str, Any] = {}
    with _f10_no_proxy():
        try:
            val = _f10_parse_valuation(_f10_call("valuation", key, req_id="200191"))
            if val and any(v is not None for v in val.values()):
                out = val
        except Exception:  # noqa: BLE001 - 轻量口失败就当无值，不抛
            out = {}
    return out


def f10_brief(glb: str, force: bool = False) -> dict[str, Any]:
    """F10 **B2 段**（HTTP 批）一次取全，逐项独立降级。

    `force=True`：跳过磁盘/内存缓存强制重拉（前端右键菜单「刷新F10」用）。

    返回：::

        {"val": {pe_ttm,pe_pct,pb_mrq,pb_pct,total_mcap,float_mcap},
         "plans": [...], "north": {...}|None, "forecast": {...}|None,
         "sector": str|None, "perf": {...}|None, "rzrq": [...],
         "perf_q": {...}|None, "gdhs": {...}|None,
         "source": "eltdx:f10(200191)", "errors": ["valuation", ...]}

    边界：
      · 任一项失败 → 该项空值 + 记进 `errors`，**不抛**；
      · 总预算 `F10_BUDGET` 秒，超预算的剩余项直接跳过（记 `budget_exhausted`）；
      · 整批全空且连续失败达 `F10_FAIL_STREAK` 次 → 熔断 `F10_SKIP_TTL` 秒（防每次刷新白等超时）；
      · 结果缓存 `F10_TTL` 秒（键 = glb）。
    """
    key = str(glb or "")
    empty = {"val": {}, "plans": [], "north": None, "forecast": None, "sector": None,
             "perf": None, "rzrq": [], "perf_q": None, "gdhs": None,
             "source": "eltdx:f10(200191)", "errors": [], "fetched_at": None}
    if not key:
        empty["errors"] = ["bad_code"]
        return empty

    # ── 2026-10-26 落盘缓存（主人选型）──
    # B2 是基本面数据，同一交易日内直接读盘（≈5ms），跨日自动失效。
    # 为什么不在内存 TTL 上拉长：进程重启/晚上关面板都会清空内存，而冷查要 7–12s；
    # 只有落盘才能做到「每个代码一天只冷一次」。
    disk = None if force else _f10_b2_load(key)
    if disk is not None:
        out_d = dict(empty)
        out_d.update(disk)
        out_d["errors"] = list(disk.get("errors") or [])
        # 数据真实产生时间（不是本次返回时间）：前端据此提示「N 分钟前」
        out_d["fetched_at"] = disk.get("fetched_at")
        _f10_cache.put(key, dict(out_d))     # 顺手回过内存层（同一进程后续更快）
        return out_d

    hit = None if force else _f10_cache.get(key, F10_TTL)
    if hit is not None:
        return dict(hit)
    if time.monotonic() < float(_f10_state.get("skip_until") or 0.0):
        out = dict(empty)
        out["errors"] = ["circuit_open"]
        return out

    code6 = key[-6:] if key[:2] in ("sh", "sz", "bj") else key[:6]
    out = dict(empty)
    errors: list[str] = []
    deadline = time.monotonic() + F10_BUDGET

    def _step(name: str, fn: Any) -> Any:
        """跑一项：超预算 → 记 budget 并返回 None；异常 → 记 name 并返回 None。"""
        if time.monotonic() > deadline:
            if "budget_exhausted" not in errors:
                errors.append("budget_exhausted")
            return None
        try:
            return fn()
        except Exception:  # noqa: BLE001 - 单项失败不影响其它项
            errors.append(name)
            return None

    with _f10_no_proxy():
        val = _step("valuation", lambda: _f10_parse_valuation(
            _f10_call("valuation", key, req_id="200191")))
        # 全 None 的估值 = 没取到（回 {}，让下游按「无值」处理，而不是一堆 null 键）
        if val and any(v is not None for v in val.values()):
            out["val"] = val
        plans = _step("plans", lambda: _f10_parse_plans(
            _f10_call("shareholder_change_plans", key)))
        if plans:
            out["plans"] = plans
        north = _step("north", lambda: _f10_parse_north(
            _f10_call("northbound_holding", key, section="bszj")))
        if north:
            out["north"] = north
        fc = _step("forecast", lambda: _f10_call("profit_forecast", key))
        if fc is not None:
            out["forecast"] = _f10_parse_forecast(fc)
            # 业绩历史与 forecast 同入口（5 表）：旧端为「502 重试互不牵连」另调一次，照搬
            out["perf"] = _step("perf", lambda: _f10_parse_perf(
                _f10_call("profit_forecast", key)))
        rzrq = _step("rzrq", lambda: _f10_parse_rzrq(
            _f10_call("stock_score", key, section="rzrq")))
        if rzrq:
            out["rzrq"] = rzrq
        out["perf_q"] = _step("perf_q", lambda: _f10_parse_perf_q(
            _f10_call("call", "CWServ.tdxf10_gg_cwfx", params=[code6, "zyzb", ""])))
        out["gdhs"] = _step("gdhs", lambda: _f10_parse_gdhs(
            _f10_call("call", "CWServ.tdxf10_gg_gdyj",
                      params=[code6, "gdrs", "", "", "1", "1", "40"])))
        out["sector"] = _step("sector", lambda: _f10_parse_sector(
            _f10_call("stock_score", key)))

    out["errors"] = errors
    out["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 熔断计数：整批一个值都没拿到 = 一次硬失败
    got_any = bool(out["val"] or out["plans"] or out["north"] or out["forecast"]
                   or out["sector"] or out["perf"] or out["rzrq"]
                   or out["perf_q"] or out["gdhs"])
    if got_any:
        _f10_state["fail_streak"] = 0
        _f10_cache.put(key, dict(out))
        # 落盘（整个 B2 段都是日级数据；写不进去只影响下次冷查速度）
        _f10_b2_save(key, dict(out))
    else:
        _f10_state["fail_streak"] = int(_f10_state.get("fail_streak") or 0) + 1
        if _f10_state["fail_streak"] >= F10_FAIL_STREAK:
            _f10_state["skip_until"] = time.monotonic() + F10_SKIP_TTL
    return out


# ----------------------------------------------------------------------------
# F10 B1 段（socket：资金流 + 股东户数/上市日）—— 2026-10-26 加两层缓存
#
# 为什么单独开函数：B1 原来写在插件 `compat_kline/backend.py::_f10_b1()` 里，
# **没有任何缓存**，每次开弹窗都要跑两次 socket（money_flow + finance_batch）。
# B2 落到交易日缓存后，B1 就是剩下的那两次固定开销。
#
# 两个子段变化频率差很远，所以分两个 TTL，而不是一起按天：
#   · 资金流 main_net_today / main_ratio_today / main_net_5d → **盘中在变**，5 分钟；
#   · 股东户数 / 上市日 → 季报级静态，按交易日（进程内，成本低不落盘）。
# 缓存放 core 而不是插件：插件不得持有行情缓存（CONTRACT §4.1）。
# ----------------------------------------------------------------------------

_B1_FLOW_CACHE = _Cache(keep=256)
_B1_FIN_CACHE = _Cache(keep=256)


def f10_b1_flow(glb: str) -> dict[str, Any]:
    """资金流三件（5min TTL）。不可用 → 空 dict（调用方保持 None，不编造）。"""
    key = str(glb or "")
    if not key:
        return {}
    hit = _B1_FLOW_CACHE.get(key, F10_B1_TTL)
    if hit is not None:
        return dict(hit)
    res = call("money_flow.daily", key)
    recs = list(getattr(res, "records", None) or ())
    if not recs:
        return {}
    out: dict[str, Any] = {}
    r0 = recs[0]
    mn = getattr(r0, "main_net", None)
    mr = getattr(r0, "main_ratio", None)
    if mn is not None:
        out["main_net_today"] = round(float(mn), 2)
    if mr is not None:
        out["main_ratio_today"] = round(float(mr), 4)
    five = [float(getattr(r, "main_net")) for r in recs[:5]
            if getattr(r, "main_net", None) is not None]
    if five:
        out["main_net_5d"] = round(sum(five), 2)
    if out:
        _B1_FLOW_CACHE.put(key, dict(out))
    return out


def f10_b1_finance(glb: str) -> dict[str, Any]:
    """股东户数 / 户数报告期 / 上市日（交易日 TTL，进程内）。"""
    key = str(glb or "")
    if not key:
        return {}
    day_key = "%s@%s" % (key, _today_str())
    hit = _B1_FIN_CACHE.get(day_key, 86400.0)
    if hit is not None:
        return dict(hit)
    res = call("corporate.finance_batch", [key])
    recs = list(getattr(res, "records", None) or ())
    if not recs:
        return {}
    r0 = recs[0]
    out: dict[str, Any] = {}
    hd = getattr(r0, "gu_dong_ren_shu_raw_float", None)
    if hd is not None:
        out["holders"] = int(round(float(hd)))
    upd = getattr(r0, "updated_date", None)
    if upd is not None:
        out["holder_report"] = str(upd)[:10]
    ipo = getattr(r0, "ipo_date", None)
    if ipo is not None:
        out["ipo_date"] = str(ipo)[:10]
    if out:
        _B1_FIN_CACHE.put(day_key, dict(out))
    return out


# ============================================================================
# 下单行情快照（4.1.3a-1）
#
# 供 feed / paper 插件使用：`order_quote()` 是旧 8800 `server.py::_order_quote()`
# 的 **eltdx 半段**（QMT 覆盖在 feed 层做，core 不接 QMT）。
# `build_quote5_payload()` 与 `_fmt_hhmmss` / `_quote_level` / `_elapsed_minutes` /
# `compute_atr14` / `compute_ma5` 全部逐字移植自 `_vendor_src/quote5_reference.py`
# （旧 8800 `_eltdx_quote5_bundle` + `collectors/atr_preheat.py`），口径不得改：
# 一改就和旧端双跑对不上。
# ============================================================================

# 五档档数上限（旧端 QUOTE5_MAX_LEVELS）
QUOTE5_MAX_LEVELS = 5
# ATR14 周期与最少日线根数（旧端 atr_preheat.py：凑不齐 14 个完整 TR 就不算）
ATR_PERIOD = 14
MIN_BARS = 15
# 下单指标缓存 600s（旧端 _order_metrics 同口径：盘中日线不变）
METRICS_TTL = 600.0
# 日线取 40 根（旧端 count=40），算的时候只用末 30 根
METRICS_BARS = 40
METRICS_USE = 30

# 下单指标缓存（模块级 TTL 缓存 + 线程锁，见 _Cache）。keep 给大一点：
# 条件单/挂单一轮评估要批量过一遍代码，留太少会自己把自己挤掉。
_order_metrics_cache = _Cache(keep=512)


def _now_str() -> str:
    """统一时间口径（`YYYY-MM-DD HH:MM:SS`，本地时区）。

    与 `app.core.paper.state.now_str()` 同格式，但不 import 它：
    core 行情门面不该反过来依赖纸面账本（那层随时可能改路径与锁）。
    """
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _finite(v: Any) -> bool:
    """有限数判定（NaN / inf / 不可转 float → False）。"""
    try:
        f = float(v)
        return f == f and abs(f) != float("inf")
    except (TypeError, ValueError):
        return False


def _fmt_hhmmss(v: Any) -> str | None:
    """eltdx update_time_raw（如 153003）→ '15:30:03'；非法 → None。"""
    try:
        s = str(int(v)).zfill(6)
        hh, mm, ss = int(s[0:2]), int(s[2:4]), int(s[4:6])
        if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
            return None
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    except (TypeError, ValueError):
        return None


def _quote_level(level: Any) -> dict[str, Any] | None:
    """eltdx QuoteLevel → {price, vol(手)}；价格非正/字段缺失 → None（该档留空）。"""
    try:
        price = float(getattr(level, "price", 0) or 0)
        vol = int(getattr(level, "volume", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not _finite(price) or price <= 0:
        return None
    return {"price": round(price, 3), "vol": vol}


def elapsed_minutes(hm: str) -> int | None:
    """'15:00' → 当日已开市分钟数（午休不计；9:30 前=0，收盘封顶 240）。

    量比口径：量比 = (当日累计量 / 已开市分钟) / (过去5日日均量 / 240)。
    2026-11-13：公开给插件用（`ctx.quotes.elapsed_minutes`，选股页盘中量比），
    旧名 `_elapsed_minutes` 保留为别名，不另写第二份实现。
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


_elapsed_minutes = elapsed_minutes      # 兼容旧调用名（本文件 quote5 等）


def build_quote5_payload(ts_code: str, rec: Any, ticks: Any = None,
                         aux: dict[str, Any] | None = None,
                         asof: str | None = None) -> dict[str, Any]:
    """盘口卡 payload（纯函数，供 /api/quote5 与单测；逐字移植自旧 8800）。

    rec = eltdx QuoteRefreshRecord（0x0547）或 None；ticks = 最近成交（新→旧）；
    aux = {avg5(过去5日日均量·手), circ(流通股本·股), limit_up, limit_down}。
    形状：{code,last,pre_close,change,change_pct,high,low,limit_up,limit_down,
           quote_time,asks[],bids[],stats{now_vol,total_vol,outer,inner,turnover,vol_ratio},
           ticks[{t,price,vol,side,n}],asof,source}
    口径：asks=卖1..卖5（升序）、bids=买1..买5（降序）、vol/量单位=手。
    """
    code = str(ts_code or "")
    aux = aux or {}
    out: dict[str, Any] = {
        "code": code, "last": None, "pre_close": None, "change": None,
        "change_pct": None, "high": None, "low": None,
        "limit_up": None, "limit_down": None,
        "quote_time": None, "asks": [], "bids": [],
        "stats": {"now_vol": None, "total_vol": None, "outer": None, "inner": None,
                  "turnover": None, "vol_ratio": None},
        "ticks": list(ticks or []),
        "asof": asof or _now_str(),
        "source": "eltdx:depth(0x0547)+ticks(0x0fc5)",
    }
    if aux.get("limit_up"):
        out["limit_up"] = round(float(aux["limit_up"]), 3)
    if aux.get("limit_down"):
        out["limit_down"] = round(float(aux["limit_down"]), 3)
    if rec is None:
        return out
    try:
        last = float(getattr(rec, "last_price", 0) or 0)
        pre = float(getattr(rec, "last_close_price", 0) or 0)
        high = float(getattr(rec, "high_price", 0) or 0)
        low = float(getattr(rec, "low_price", 0) or 0)
    except (TypeError, ValueError):
        return out
    if _finite(last) and last > 0:
        out["last"] = round(last, 3)
    if _finite(pre) and pre > 0:
        out["pre_close"] = round(pre, 3)
    if _finite(high) and high > 0:
        out["high"] = round(high, 3)
    if _finite(low) and low > 0:
        out["low"] = round(low, 3)
    if out["last"] is not None and out["pre_close"]:
        ch = out["last"] - out["pre_close"]
        out["change"] = round(ch, 3)
        out["change_pct"] = round(ch / out["pre_close"] * 100, 3)
    out["quote_time"] = _fmt_hhmmss(getattr(rec, "update_time_raw", None))
    for side, levels in (("bids", getattr(rec, "buy_levels", ()) or ()),
                         ("asks", getattr(rec, "sell_levels", ()) or ())):
        for lv in list(levels)[:QUOTE5_MAX_LEVELS]:
            row = _quote_level(lv)
            if row:
                out[side].append(row)
    # 现量 / 总量 / 外盘 / 内盘（手）
    try:
        total = int(getattr(rec, "total_hand", 0) or 0)
        now_vol = int(getattr(rec, "current_hand", 0) or 0)
        outer = int(getattr(rec, "outer_disc", 0) or 0)
        inner = int(getattr(rec, "inside_dish", 0) or 0)
    except (TypeError, ValueError):
        total = now_vol = outer = inner = 0
    st = out["stats"]
    st["total_vol"] = total or None
    st["now_vol"] = now_vol if now_vol else None
    st["outer"] = outer or None
    st["inner"] = inner or None
    circ = aux.get("circ")
    if circ and total:
        # 换手率 = 成交量(手)*100 / 流通股本 * 100%
        st["turnover"] = round(total * 10000.0 / float(circ), 2)
    avg5 = aux.get("avg5")
    em = _elapsed_minutes(out["quote_time"] or "")
    if avg5 and float(avg5) > 0 and total and em:
        st["vol_ratio"] = round((total / float(em)) / (float(avg5) / 240.0), 3)
    return out


def _fresh_from_time(qt: str | None, max_lag: int = 120) -> bool:
    """quote_time（HH:MM:SS）是否在当前时刻前 max_lag 秒内。

    逐字移植旧 8800 `server.py::_fresh_from_time`：条件单触发门槛看这个；
    手工单不看（纸面演练允许收盘后按最新价模拟）。
    注意跨零点：`nsec - qsec` 为负即判 False（与旧端一致，不猜隔夜）。
    """
    try:
        s = str(qt or "")
        if len(s) < 5:
            return False
        h, m = int(s[0:2]), int(s[3:5])
        sec = int(s[6:8]) if len(s) >= 8 else 0
        qsec = h * 3600 + m * 60 + sec
        n = datetime.now()
        nsec = n.hour * 3600 + n.minute * 60 + n.second
        return 0 <= nsec - qsec <= max_lag
    except (TypeError, ValueError):
        return False


def _bar_triplet(b: Any) -> dict[str, Any]:
    """日线对象 → {"high","low","close"}。

    eltdx `bars.get(period="day")` 的字段是 `high_price/low_price/close_price`；
    若上游换了形状（旧端 / 测试假对象）则回退 `high/low/close`，再回退 dict 取值。
    取不到的给 None —— 由 compute_* 判 None，**不补 0**（脏数据比缺失更危险）。
    """
    out: dict[str, Any] = {}
    for key, primary in (("high", "high_price"), ("low", "low_price"),
                         ("close", "close_price")):
        v = getattr(b, primary, None)
        if v is None:
            v = getattr(b, key, None)
        if v is None and isinstance(b, dict):
            v = b.get(primary, b.get(key))
        out[key] = v
    return out


def compute_atr14(bars: list[dict[str, Any]]) -> float | None:
    """ATR14 纯函数（可单测）。bars：按时间升序的日线 [{high, low, close}, ...]。

    返回 round(atr14, 4)；不足 MIN_BARS 根（凑不齐 14 个完整 TR）→ None。
    TR_i = max(high_i-low_i, |high_i-close_{i-1}|, |low_i-close_{i-1}|)，
    首根 TR_0=high_0-low_0。
    """
    if not bars or len(bars) < MIN_BARS:
        return None
    trs: list[float] = []
    prev_close: float | None = None
    for b in bars:
        try:
            h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        except (KeyError, TypeError, ValueError):
            return None
        if h <= 0 or l <= 0 or c <= 0 or h < l:
            # 非法行情（停牌填充/脏数据）→ 整票放弃（回退兼容口径比脏 ATR 安全）
            return None
        if prev_close is None:
            trs.append(h - l)
        else:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = c
    window = trs[-ATR_PERIOD:]
    return round(sum(window) / ATR_PERIOD, 4)


def compute_ma5(bars: list[dict[str, Any]]) -> float | None:
    """MA5 纯函数：5 个交易日收盘价简单均值。

    bars：按时间升序的日线 [{high, low, close}, ...]。返回 round(ma5, 3)；
    不足 5 根或末 5 根含非法价（≤0）→ None（保留建仓静态快照，不覆盖）。
    """
    if not bars or len(bars) < 5:
        return None
    closes: list[float] = []
    for b in bars[-5:]:
        try:
            c = float(b["close"])
        except (KeyError, TypeError, ValueError):
            return None
        if c <= 0:
            return None
        closes.append(c)
    return round(sum(closes) / len(closes), 3)


def order_metrics(glb: str) -> dict[str, float | None]:
    """日线 ATR14 / MA5（保本止损、破五日线条件单用）→ {"atr14","ma5"}。

    取不到就留 None（条件单评估时自己跳过），不编造、不补零。
    缓存 METRICS_TTL 秒：日线盘中不变，收盘后自然过期（与旧端 600s 同口径）。
    """
    hit = _order_metrics_cache.get(glb, METRICS_TTL)
    if hit is not None:
        return dict(hit)
    out: dict[str, float | None] = {"atr14": None, "ma5": None}
    try:
        # 前复权：旧 8800 `_order_metrics()` 走的是 `_eltdx_qfq_bars`（前复权日线）。
        # 用不复权序列算 ATR/MA5，遇到除权日会得到一根假长 TR、MA5 也会断层，
        # 保本止损的价位就跟着错——指标必须在与下单展示价同一口径上算。
        rows = daily_bars(glb, METRICS_BARS, adjust="qfq") or []
        bars = [_bar_triplet(b) for b in rows][-METRICS_USE:]
        out["atr14"] = compute_atr14(bars)
        out["ma5"] = compute_ma5(bars)
    except Exception:  # noqa: BLE001 - 指标缺失不阻断下单
        pass
    _order_metrics_cache.put(glb, dict(out))
    return dict(out)      # 返回副本：调用方改动不回写缓存


def order_quote(ts_code: str) -> dict[str, Any]:
    """下单用行情快照：与 /api/quote5 同源同形，另算 `fresh` + ATR14/MA5。

    旧 8800 `_order_quote()` 的 **eltdx 半段**：这里不 overlay QMT 盘口，
    覆盖由 feed 层（`QuoteFeed.order_quote`）按「5 秒内 tick」叠加，core 不碰 QMT。
    代码无法识别 → `{}`（调用方按无行情处理，不抛）。
    """
    glb = normalize(ts_code)
    if not glb:
        return {}
    rec = depth(glb)
    aux = stock_aux(glb)
    ticks = today_ticks(glb)          # 可为空：先按空跑通交易链路
    q = build_quote5_payload(ts_code, rec, ticks=ticks, aux=aux)
    q["fresh"] = _fresh_from_time(q.get("quote_time"))
    q.update(order_metrics(glb))      # atr14 / ma5（保本止损·破五日线）
    return q


# ============================================================================
# THS 指数原生周线（S2 接缝，2026-10-24）
#
# 供 `compat_kline` 插件的 `*.TI` 周线用：插件禁出站（CONTRACT §8），所以 HTTP、
# 凭据读取与代理旁路全部留在 core。口径唯一源：旧 8800
# `engine/server.py::_ths_index_bars_mcp`（同 URL、同参数、同字段名）。
#
# **任何失败一律返回 None**（不抛、不编造）：上层据此标「周线源不可用」，
# 不用日线聚合冒充（2026-10-19 主人裁定）。
# ============================================================================

# 单次请求超时（旧端同量级）
THS_HTTP_TIMEOUT = 20.0


def _ths_api_key() -> str | None:
    """THS API Key（读用户配置文件）→ str | None。

    **凭据纪律**：不落盘、不进日志、不进异常文案；文件缺失/损坏 → None（调用方降级）。
    """
    import json  # noqa: PLC0415 - 仅本段用

    try:
        with open(THS_CFG_FILE, encoding="utf-8") as f:
            c = json.load(f)
        k = (c.get("headers") or {}).get("X-api-key") or c.get("api_key")
        return str(k) if k else None
    except Exception:  # noqa: BLE001 - 凭据缺失 → 调用方降级
        return None


def _ths_parse_body(raw: str) -> dict[str, Any] | None:
    """MCP 响应体 → dict。兼容两种形态：纯 JSON / SSE（`data: {...}` 行）。

    先按 JSON 解析（最稳），失败再按 SSE 逐行找 `data:`——不靠 `"\\ndata:" in raw`
    这种子串猜测（JSON 里的字符串也可能含该子串）。
    """
    import json  # noqa: PLC0415 - 仅本段用

    txt = (raw or "").strip()
    if not txt:
        return None
    try:
        got = json.loads(txt)
        return got if isinstance(got, dict) else None
    except ValueError:
        pass
    for line in txt.splitlines():
        s = line.strip()
        if not s.startswith("data:"):
            continue
        try:
            got = json.loads(s[5:].strip())
        except ValueError:
            continue
        if isinstance(got, dict):
            return got
    return None


def ths_index_weekly(code: str, start8: Any, end8: Any = None) -> list[dict[str, Any]] | None:
    """THS 指数**原生周线**（interval=1w）；升序 bars 或 None。

    参数：`code` = 指数代码（`881121.TI`，大小写不敏感）；`start8` / `end8` = `YYYYMMDD`，
    `end8` 省略 → 今天（含当日）。
    返回：``[{"t":"YYYYMMDD","o","h","l","c","v","amount"}, ...]`` 按 `t` 升序；
    凭据缺失 / HTTP 异常 / 上游 `code != 0`（如 5003）/ 空 items → **None**。

    ⚠ 上游 2026-10-24 对 `interval=1w` 返回 `{"code":5003,"message":"Data source
    unavailable"}`，故线上当前仍拿 None——这是**如实的不可用**，不是本函数的缺陷，
    也不要在 core 里造替代数据（不用日线聚合冒充周线）。
    代理旁路：本机 Clash 代理会让该域名 WinError 10061，与 F10 同机制（`_no_proxy_hosts`）。
    """
    key = _ths_api_key()
    if not key:
        return None
    import json  # noqa: PLC0415 - 仅本段用
    import urllib.request  # noqa: PLC0415 - 仅本段用

    try:
        start_ms = int(datetime.strptime(str(start8), "%Y%m%d").timestamp() * 1000)
        end_s = str(end8) if end8 else datetime.now().strftime("%Y%m%d")
        end_ms = int(datetime.strptime(end_s, "%Y%m%d").timestamp() * 1000) + 86400_000
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_a_share_index_prices_historical",
                           "arguments": {"thscode": str(code).strip().upper(),
                                         "interval": "1w",
                                         "start": start_ms, "end": end_ms}}}
        req = urllib.request.Request(
            THS_INDEX_MCP_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream",
                     "X-api-key": key})
        with _no_proxy_hosts((THS_INDEX_MCP_HOST,)):
            with urllib.request.urlopen(req, timeout=THS_HTTP_TIMEOUT) as resp:
                raw = resp.read().decode()
        payload = _ths_parse_body(raw)
        content = ((payload or {}).get("result") or {}).get("content") or []
        txt = content[0].get("text") if content else None
        if not txt:
            return None
        d = json.loads(txt)
        if not isinstance(d, dict) or d.get("code") != 0:
            return None                      # 5003/1002 等 → 视为不可用
        items = (d.get("data") or {}).get("item") or []
        out: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            ms = it.get("date_ms")
            if ms is None:
                continue
            try:
                t = datetime.fromtimestamp(float(ms) / 1000).strftime("%Y%m%d")
            except (TypeError, ValueError, OSError):
                continue
            out.append({"t": t, "o": it.get("open_price"), "h": it.get("high_price"),
                        "l": it.get("low_price"), "c": it.get("close_price"),
                        "v": it.get("volume"), "amount": it.get("turnover")})
        out.sort(key=lambda b: b["t"])
        return out or None
    except Exception:  # noqa: BLE001 - 任何异常 = 不可用（不阻断 K 线）
        return None
