# -*- coding: utf-8 -*-
"""恐慌广度装配层（`GET /api/panic`，2026-10-24 M1 A1）。

## 为什么在 core

三条理由，与 `ops_run.py` 同一先例：

1. **算法唯一源在旧仓**：250 日滚动分位 / 恐慌广度公式 / 状态标签全部在
   `engine/collectors/panic_breadth.py`（`percentile_rank` / `rolling_percentile` /
   `panic_score` / `state_of` / `score_row` / `raw_from_snapshot` / `load_store`）。
   本模块 `importlib` 动态加载它、**只调它的函数**，不抄公式——抄一份就是第二个口径源，
   下次旧仓调窗口或最小样本，两边就开始对不上（而且错的方向是「面板显示的值不是主人研究文档里的值」）。
2. **实时快照要走 core 的行情门面**：旧端 `fetch_880_snapshot()` 自己 `TdxClient()`，
   插件侧静态禁自连行情源（CONTRACT §4.1）。这里改用 `ctx.quotes`（`app/core/quotes.py`
   的进程单例连接 + 短 TTL 缓存），拿到同样的 `{code: {last, open, high, pre_close}}`。
3. **六阶段标签要读库**：旧端 `_panic_stage()` 只读 `data/track_pool.db::sentiment`。
   插件走 `ctx.db_read` 也行，但这是「旁注字段 + 60s 缓存 + 只读一次」的装配细节，
   放 core 才不会让插件为了一个标签去声明一整张库白名单。

## 与旧端的差异（逐条登记，见 docs/HANDOFF_20261024_M1_fix.md）

1. **不跑「每日追加自愈」**：旧 `api_panic` 在收盘后会起后台单飞线程跑
   `panic_breadth.append_day()` 往生产库补当日行。v2 的取值路径保持**纯只读**
   （append 要扫 kline 全表、写 data/panel 生产文件，属盘后链职责，不该由渲染端点触发）。
2. 快照、阶段标签、序列库三处缓存都保留旧端口径（2.8s / 60s / 按 mtime），
   所以同一拍内多次刷新不会重复打行情源。
3. `store_path()` 的 `DSH_PANIC_STORE` 环境覆盖仍然生效（旧仓生产者自己认这个变量，
   测试也用它做隔离）——所以序列库优先按「生产者声明的路径」读，读不到再退
   `paths.READABLE_PANEL["panic_breadth"]`。

## 第四项·量能（2026-10-29 主人加）

   量能 = 当日全天成交额 ÷ 昨日全天成交额 → 250 日滚动分位（与价三维同窗口/同 MIN_SAMPLE）。
   盘中用预估全天额、收盘后用实际全天额；只在下跌日（全A中位 < 0）计入。
   **取值与降级全在旧仓 `turnover_context` / `amount_ratio_window`**，本模块不自己读行情、
   不自己算 w(t)、不自己拉 5m K 线：它只负责把盘中预估与收盘实际这两档接上，
   并多插一个「同日刻比值」供人工校验（与预估全天比值背离 = 节奏差异，不自动调分）。
   历史行（1264 行）没有 amount 字段且不得重算 → 量能分位对它们为 None，前端遇空跳过。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import paths

# 旧仓生产者（口径唯一源）
LEGACY_PANIC = paths.WS / "engine" / "collectors" / "panic_breadth.py"
_LEGACY_NAME = "_panel_v2_legacy_panic_breadth"

# 旧端常量（`engine/server.py:1030-1031`）
PANIC_SNAP_TTL_S = 2.8          # 面板 3s 一刷 → 同一拍内只取一次数
PANIC_STAGE_TTL_S = 60.0        # sentiment.stage 缓存
SNAP_LOCK_WAIT_S = 1.5          # 另一线程正在取数时的短等上限（旧端同值）

# 快照代码：880005 涨跌家数 / 880006 停板家数 / 880009 全A 涨跌（`fetch_880_snapshot` 默认三只）
SNAP_CODES = ("880005", "880006", "880009")

_CACHE: dict[str, Any] = {}
_STORE = {"mtime": None, "doc": None}
_SNAP = {"at": 0.0, "data": None}
_STAGE = {"at": 0.0, "data": None}
_SNAP_LOCK = threading.Lock()


class PanicError(Exception):
    """旧仓口径模块不可用（文件缺失 / 导入失败）——调用方如实 503，不降级成假读数。"""


def reset_cache() -> None:
    """清掉已加载的旧仓模块与三处缓存（测试隔离用；生产不需要调）。"""
    _CACHE.pop("module", None)
    sys.modules.pop(_LEGACY_NAME, None)
    _STORE.update({"mtime": None, "doc": None})
    _SNAP.update({"at": 0.0, "data": None})
    _STAGE.update({"at": 0.0, "data": None})


def _load_legacy() -> Any:
    """加载旧仓 `panic_breadth`（按进程缓存）。

    单独一个函数是为了**可替换**：测试 monkeypatch 本函数可注入假模块，
    完全避开真文件与网络。
    """
    cached = _CACHE.get("module")
    if cached is not None:
        return cached
    path = LEGACY_PANIC
    if not Path(path).is_file():
        raise PanicError("旧仓口径模块不存在：%s（恐慌广度依赖它，未降级）" % path)
    engine_dir = str(paths.WS / "engine")
    inserted = engine_dir not in sys.path
    if inserted:
        sys.path.append(engine_dir)
    try:
        spec = importlib.util.spec_from_file_location(_LEGACY_NAME, path)
        if spec is None or spec.loader is None:
            raise PanicError("无法为 %s 建立 import spec" % path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_LEGACY_NAME] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - 导入失败一律上报
            sys.modules.pop(_LEGACY_NAME, None)
            raise PanicError("旧仓口径模块导入失败：%s: %s" % (type(exc).__name__, exc)) from exc
    finally:
        if inserted:
            # 旧仓模块 import 期自己往 sys.path 插一条，别让它永久留在最前面
            sys.path[:] = [p for p in sys.path if p != engine_dir]
    for sym in ("raw_from_snapshot", "score_row", "state_of", "WINDOW"):
        if not hasattr(module, sym):
            sys.modules.pop(_LEGACY_NAME, None)
            raise PanicError("旧仓口径模块缺少 %s：%s" % (sym, path))
    _CACHE["module"] = module
    return module


# ---------------------------------------------------------------- 序列库（只读）


def store_file() -> Path:
    """序列库路径。

    优先级：
    1. `DSH_PANIC_STORE` 环境变量（旧仓生产者与测试共用的覆盖口，两边必须看同一份）；
    2. `paths.READABLE_PANEL["panic_breadth"]`（核心登记的生产路径，生产上与旧仓
       `store_path()` 的默认值指向同一文件）。

    为什么不直接调旧仓 `store_path()`：它拼的是固定生产路径，不认核心白名单；
    沙箱/测试把白名单改到临时目录后就两边不一致（读的就不是登记的那份）。
    """
    env = os.environ.get("DSH_PANIC_STORE")
    if env:
        return Path(env)
    path = paths.panel_path("panic_breadth")
    if path is None:
        raise PanicError("panic_breadth.json 未登记进 paths.READABLE_PANEL")
    return path


def load_store_doc() -> dict[str, Any]:
    """读序列库（按 mtime 缓存：文件不变不重解析）。缺失/损坏 → `{"meta": {}, "series": []}`。

    与旧端 `load_store()` 同口径（`OSError`/`ValueError` → 空库；`series` 不是 list → 空库），
    但**自己读文件**而不调旧仓那个函数：它内部用旧仓自己的 `store_path()`，会绕过核心白名单。
    读文件不是口径（分位/评分仍全走旧仓）。
    """
    path = store_file()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"meta": {}, "series": []}
    if _STORE["mtime"] == mtime and isinstance(_STORE["doc"], dict):
        return _STORE["doc"]
    doc: dict[str, Any] = {"meta": {}, "series": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict) and isinstance(loaded.get("series"), list):
            doc = loaded
    except (OSError, ValueError):
        doc = {"meta": {}, "series": []}
    _STORE.update({"mtime": mtime, "doc": doc})
    return doc


# ---------------------------------------------------------------- 实时快照（走行情门面）


def _normalize_snap_code(raw: Any) -> str:
    """快照 code → 纯数字六位（旧端 `fetch_880_snapshot` 的 key 就是 `"880005"`）。"""
    code = str(raw or "")
    low = code.lower()
    if low[:2] in ("sh", "sz", "bj"):
        return low[2:]
    return code


def snapshot(quotes: Any) -> dict[str, Any]:
    """880005/880006/880009 eltdx 快照（TTL 2.8s + 单飞锁），不可达 → `{}`。

    旧端 `_panic_snapshot()`（`engine/server.py:1124`）的等价重写：取数从
    `fetch_880_snapshot()`（自建 TdxClient）换成 `ctx.quotes`（进程单例连接），
    字段映射与降级语义逐条照搬（拿不到就给空 dict，端点再降级到序列库，不编造）。
    """
    now = time.time()
    if now - _SNAP["at"] < PANIC_SNAP_TTL_S:
        return _SNAP["data"] or {}
    if not _SNAP_LOCK.acquire(blocking=False):
        # 另一线程正在取数：短等它一拍，避免并发拍里有一拍拿不到快照而闪回旧值
        deadline = now + SNAP_LOCK_WAIT_S
        while time.time() < deadline:
            time.sleep(0.05)
            if time.time() - _SNAP["at"] < PANIC_SNAP_TTL_S:
                return _SNAP["data"] or {}
        return _SNAP["data"] or {}
    try:
        if time.time() - _SNAP["at"] < PANIC_SNAP_TTL_S:
            return _SNAP["data"] or {}
        data = _fetch_snapshot(quotes)
        _SNAP.update({"at": time.time(), "data": data})
        return data
    finally:
        _SNAP_LOCK.release()


def _fetch_snapshot(quotes: Any) -> dict[str, Any]:
    """一次取三只快照 → `{code: {last, open, high, pre_close}}`；失败 `{}`（不重试）。

    字段来源与旧端同：`last_price` / `open_price` / `high_price` / `pre_close_price`。
    """
    out: dict[str, Any] = {}
    try:
        codes = ["sh" + c for c in SNAP_CODES]
        res = quotes.call("quotes.get_snapshots", codes)
    except Exception:  # noqa: BLE001 - 门面不可用 → 降级（端点读库）
        return {}
    if isinstance(res, (list, tuple)):
        recs = list(res)
    else:
        recs = list(getattr(res, "snapshots", None) or getattr(res, "records", None) or ())
    for s in recs:
        code = _normalize_snap_code(getattr(s, "code", "") or "")
        if not code:
            continue
        out[code] = {
            "last": getattr(s, "last_price", None),
            "open": getattr(s, "open_price", None),
            "high": getattr(s, "high_price", None),
            "pre_close": getattr(s, "pre_close_price", None),
        }
    return out


# ---------------------------------------------------------------- 量能因子装配（第四项）

VOL_FIELDS = ("amount", "amount_kind", "amount_prev", "amount_ratio",
              "amount_ratio_same_time", "amount_source")


def _legacy_fn(legacy: Any, name: str) -> Any:
    """取旧仓函数（旧仓版本偏旧时缺函数 → None，量能项缺席，不假装有）。"""
    fn = getattr(legacy, name, None)
    return fn if callable(fn) else None


def _prev_day8(legacy: Any, day8: str, series: list) -> str | None:
    """上一交易日：旧仓 `prev_trading_day`（交易日历）→ 序列末行兜底。"""
    fn = _legacy_fn(legacy, "prev_trading_day")
    fallback = str(series[-1].get("date")) if series and series[-1].get("date") else None
    if fn is None:
        return fallback
    try:
        return fn(day8, fallback) or fallback
    except Exception:  # noqa: BLE001 - 日历不可用 → 兜底（不影响价三维）
        return fallback


def with_turnover(legacy: Any, raw: dict, day8: str, kind: str,
                  series: list) -> dict:
    """把量能因子的原始值并进 raw（盘中预估 / 收盘实际，全部由旧仓 `turnover_context` 定）。

    2026-11-18（主人：量能复用现成数据、不要再拉取以免拥堵）：
      把**序列库里已经落好的每日全天额**（`panic_breadth.json` 序列的 amount 字段，
      含昨日与今日）当作 amounts 注入 `turnover_context`。这样：
        · 不再读 vipdoc `.day`（它自 2026-11-06 停更）；
        · 更不另拉 eltdx 日线（先前那版兜底已拆除）——零新增请求；
        · 盘中的「昨日全天额」直接取序列里昨天那一行（收盘链与历史回补已经算好）。
    序列没有 amount（老库行）→ 退回原逻辑（不编造）。
    """
    fn = _legacy_fn(legacy, "turnover_context")
    if fn is None:
        return raw
    amounts = {}
    for r in (series or []):
        a = r.get("amount") if isinstance(r, dict) else None
        if a is None:
            continue
        try:
            amounts[str(r.get("date"))] = float(a)
        except (TypeError, ValueError):
            continue
    prev_key = _prev_day8(legacy, day8, series)
    try:
        ctx = fn(day8, prev_key, kind, amounts=amounts or None)
    except Exception:  # noqa: BLE001 - 量能拿不到 → 该项缺席，不阻断价三维
        return raw
    if isinstance(ctx, dict):
        for key in VOL_FIELDS:
            raw[key] = ctx.get(key)
        # 来源标签如实：昨日额若取自序列库，就不是 `.day` 直读（不让人误以为是旧渠道）
        if amounts.get(prev_key) and ctx.get("amount_source"):
            raw["amount_source"] = str(ctx["amount_source"]).replace(
                "prev=tdx:880005.day", "prev=store:panic_breadth.amount")
    return raw


def amount_window(legacy: Any, series: list, day8: str) -> list | None:
    """量能分位的历史窗口（严格早于当日）；旧仓缺函数 → None（交给旧仓行内积累）。"""
    fn = _legacy_fn(legacy, "amount_ratio_window")
    if fn is None:
        return None
    cal = [str(r.get("date")) for r in series if r.get("date")]
    try:
        return list(fn(cal, end8=day8))
    except Exception:  # noqa: BLE001
        return None


def score_row(legacy: Any, series: list, raw: dict, day8: str) -> dict:
    """打分（分位/方向/分母全走旧仓）：旧仓没有第四项时退回三项，不自己算。"""
    win = amount_window(legacy, series, day8)
    try:
        return legacy.score_row(series, raw, amount_window=win)
    except TypeError:  # 旧仓版本偏旧（无 amount_window 参数）→ 三项，不假装有量能
        return legacy.score_row(series, raw)


def vol_state(row: dict) -> str:
    """量能项为何在位/缺位：in（计入）/ up_day（上涨日不计入）/ no_amount / thin_sample。"""
    if row.get("q_amount") is not None:
        return "in"
    med = row.get("median_pct")
    if row.get("amount_ratio") is not None and med is not None and med >= 0:
        return "up_day"
    if row.get("amount_ratio") is None:
        return "no_amount"
    return "thin_sample"


# ---------------------------------------------------------------- 六阶段标签（旁注）


def stage() -> dict[str, Any] | None:
    """当前六阶段标签：只读 `trace_pool.db::sentiment`（60s 缓存），不可得 → None。

    与旧端 `_panic_stage()` 逐条同口径：优先取 `ts='close'` 的最近一条，没有再取最近的任意一条。
    旁注字段，取不到就不显示——**绝不阻断主读数**。
    """
    now = time.time()
    if now - _STAGE["at"] < PANIC_STAGE_TTL_S:
        return _STAGE["data"]
    data = None
    db = paths.DATA_DIR / "track_pool.db"
    try:
        uri = "file:%s?mode=ro" % str(db).replace("\\", "/")
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            row = conn.execute("select trade_date, stage from sentiment where ts='close' "
                               "order by trade_date desc limit 1").fetchone()
            if row is None:
                row = conn.execute("select trade_date, stage from sentiment "
                                   "order by trade_date desc limit 1").fetchone()
        finally:
            conn.close()
        if row:
            data = {"date": str(row[0]), "label": row[1]}
    except Exception:  # noqa: BLE001 - 旁注字段取不到就不显示
        data = None
    _STAGE.update({"at": now, "data": data})
    return data


# ---------------------------------------------------------------- 读数归属（live/close/closed）


# ---------------------------------------------------------------- 当日恐慌分时（2026-11-18）

def _intraday_path():
    return paths.WS / "data" / "panel" / "panic_intraday.json"


def intraday_series(store_doc, day8, legacy=None, kind=None):
    """当日恐慌分时 → [{t, value, up_pct, limit_down_pct, limit_down, median_pct, state}]。

    数据源：`data/panel/panic_intraday.json`（tick_feed 每 60s 落一个原始点，见
    `engine/executor/panic_intraday.py`）；**分位与恐慌值不落盘**，在这里用日频历史现算：
    直接调旧仓 `score_row`（与日频同一函数、同 250 窗口、同 MIN_SAMPLE），不另写公式。
    缺项（该分钟没取到跌停/中位）→ 该点 value=None（前端断线，不插值、不补 0）。
    文件不存在/不是当日 → []（前端如实显示「当日无记录」）。
    """
    try:
        with open(_intraday_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001 - 未落盘/坏 JSON → 空
        return []
    if not isinstance(doc, dict) or str(doc.get("date") or "") != str(day8 or ""):
        return []
    pts = [p for p in (doc.get("points") or []) if isinstance(p, dict) and p.get("t")]
    if not pts:
        return []
    try:
        lg = legacy or _load_legacy()
    except Exception:  # noqa: BLE001 - 口径模块不可用 → 不出分时（不编造）
        return []
    # 量能维（2026-11-19 主人：「恐慌广度 30 却在黄线下面」）：
    #   日频口径的第四项是「**预估全天额** ÷ 昨日全天额」——注意是**预估**，不是当前累计额：
    #   累计额从开盘涨到收盘，直接拿它比昨日全天会把早盘看小、尾盘看大，不是同一个量。
    #   预估 = 累计 ÷ 量能进度 w(t)（近 20 日同刻累计占全天中位数），由旧仓
    #   `turnover_context(kind="live")` 吐 `forecast_full_day_amount`，我们不自己外推。
    #   分时点若不算它（先前就是），就是三维；头部算四维 → 同一分钟差 ~15 分。
    #   量能天然是日粒度输入，盘中只有当前预估这一个值 → 当日**所有**分时点共用它
    #   （与头部同一个 ratio），不逐点伪造。缺预估 → 该项缺席（保留三维，不编）。
    vol_raw = {}
    if kind in ("live", "close"):
        try:
            vol_raw = with_turnover(lg, {}, day8, kind, store_doc.get("series") or [])
        except Exception:  # noqa: BLE001 - 量能拿不到 → 该项缺席（保留三维，不编）
            vol_raw = {}
    ratio = vol_raw.get("amount_ratio")
    if ratio is None:
        # 当日已落库（收盘后）→ 用库行里已算好的 amount_ratio（同源，不再现估一遍）
        for _r in reversed(store_doc.get("series") or []):
            if str(_r.get("date") or "") == str(day8 or "") and _r.get("amount_ratio") is not None:
                ratio = _r.get("amount_ratio")
                break
    try:
        win = amount_window(lg, store_doc.get("series") or [], day8)
    except Exception:  # noqa: BLE001
        win = None
    hist = [r for r in (store_doc.get("series") or [])
            if str(r.get("date") or "") < str(day8 or "")]
    out = []
    for p in pts:
        up, down = p.get("up"), p.get("down")
        try:
            up_i, down_i = int(up), int(down)
        except (TypeError, ValueError):
            continue
        denom = up_i + down_i
        if denom <= 0:
            continue
        ld = p.get("limit_down")
        med = p.get("median_pct")
        raw = {"up": up_i, "down": down_i,
               "up_pct": round(up_i / denom * 100.0, 4),
               "limit_down": ld if isinstance(ld, (int, float)) else None,
               "limit_down_pct": (round(ld / denom * 100.0, 4)
                                  if isinstance(ld, (int, float)) else None),
               "median_pct": med if isinstance(med, (int, float)) else None,
               "limit_up": p.get("limit_up")}
        if ratio is not None:
            raw["amount_ratio"] = ratio
        if raw["limit_down_pct"] is None or raw["median_pct"] is None:
            out.append({"t": p["t"], "value": None, "state": None,
                        "up_pct": raw["up_pct"], "limit_down_pct": raw["limit_down_pct"],
                        "limit_down": raw["limit_down"], "median_pct": raw["median_pct"]})
            continue
        try:
            row = (lg.score_row(hist, raw, amount_window=win) if win
                   else lg.score_row(hist, raw))
        except Exception:  # noqa: BLE001 - 单点算不出 → 断线
            row = {}
        out.append({"t": p["t"], "value": row.get("panic"), "state": row.get("state"),
                    "up_pct": raw["up_pct"], "limit_down_pct": raw["limit_down_pct"],
                    "limit_down": raw["limit_down"], "median_pct": raw["median_pct"]})
    return out


def _is_trading_day(d: Any) -> bool:
    """交易日判定：旧仓 `common.trading_calendar.is_trading_day`；不可用 → 工作日兜底。

    兜底只影响 `live` 标记，不影响数值来源（旧端注释同口径）。
    """
    engine_dir = str(paths.WS / "engine")
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


def session_kind(now_dt: datetime) -> tuple[str, str]:
    """读数归属 → (kind, day8)：交易日连续竞价=live；交易日收盘后=close；其余=closed。

    逐条照搬旧端 `_panic_session()`（`engine/server.py:1175`）：
    周末/非交易日 closed；09:30–15:00 live；15:00 之后 close；开盘前 closed。
    """
    day8 = now_dt.strftime("%Y%m%d")
    hm = now_dt.strftime("%H:%M")
    if now_dt.weekday() >= 5:
        return "closed", day8
    if not _is_trading_day(now_dt.date()):
        return "closed", day8
    if "09:30" <= hm <= "15:00":
        return "live", day8
    if hm > "15:00":
        return "close", day8
    return "closed", day8


# ---------------------------------------------------------------- 装配（纯函数，依赖全注入）


def build_payload(store_doc: dict[str, Any], snap: dict[str, Any] | None,
                  stage_info: dict[str, Any] | None, now_dt: datetime | None = None) -> dict[str, Any]:
    """恐慌广度 payload 纯装配（旧端 `build_panic_payload`，`engine/server.py:1196` 逐键照搬）。

    返回 `{ok, asof, trade_date, mode, live, source, window, value, state, items[3], series[≤120], stage, note}`；
    无数据 → `{ok: false, error: "no_data", asof}`。

    · 当日已有落库行 → 直接用库值（盘后不刷）；
    · 盘中/收盘后 → 快照实时算分位（窗口 = 库内最近 250 行，严格早于当日的样本）；
    · 其余时段 → 库内最后一行（不拿隔夜快照冒充当日）。
    """
    legacy = _load_legacy()
    now_dt = now_dt or datetime.now()
    series = list(store_doc.get("series") or [])
    kind, day8 = session_kind(now_dt)

    row: dict[str, Any] | None = None
    source = "store"
    if series and series[-1].get("date") == day8:
        row = dict(series[-1])
        source = "store"
    else:
        raw = legacy.raw_from_snapshot(snap) if snap else None
        if raw and kind in ("live", "close"):
            raw = with_turnover(legacy, raw, day8, kind, series)
            row = dict(raw)
            row.update(score_row(legacy, series, raw, day8))
            row["date"] = day8
            row["live"] = kind == "live"
            source = "eltdx:880005+880006+880009"
        elif series:
            row = dict(series[-1])
            source = "store"

    if row is None:
        return {"ok": False, "error": "no_data", "asof": now_dt.strftime("%Y-%m-%d %H:%M:%S")}

    value = row.get("panic")
    items = [
        {"key": "limit_down", "label": "跌停占比", "unit": "%",
         "value": row.get("limit_down_pct"), "count": row.get("limit_down"),
         "percentile": row.get("q_limit_down")},
        {"key": "up_ratio", "label": "上涨占比", "unit": "%",
         "value": row.get("up_pct"), "count": row.get("up"),
         "percentile": row.get("q_up")},
        {"key": "median", "label": "全A中位涨跌", "unit": "%",
         "value": row.get("median_pct"), "count": None,
         "percentile": row.get("q_median")},
        # 第四项·量能（2026-10-29）：value = 当日全天额 ÷ 昨日全天额；kind 标预估/实际
        {"key": "amount", "label": "量能（成交额/昨日）", "unit": "×",
         "value": row.get("amount_ratio"), "count": None,
         "percentile": row.get("q_amount"),
         "kind": row.get("amount_kind"), "active": row.get("q_amount") is not None,
         "state": vol_state(row), "amount": row.get("amount"),
         "prev_amount": row.get("amount_prev"),
         "ratio_same_time": row.get("amount_ratio_same_time")},
    ]

    # series 每项 {date, value, q_limit_down_count, …}（主人 2026-10-23 约定，前端 120 日曲线用）；
    # `up`（上涨家数）仍随行下发供排查，前端不再消费。
    # 2026-10-30（标注 58 光标卡）：系列行补上四项分项的当日原值 ——
    #   跌停家数/占比、上涨家数/占比、全A中位、量能（含预估/实际）。只加键不改旧键，
    #   老消费者（只读 date/value/q_limit_down_count）不受影响。
    # 2026-10-31（标注 63）：limit_up / q_limit_up —— 涨停家数及其 250 日分位（原值，
    #   越高=涨停越多；消费端取 100− 对齐「越高越恐慌」）。展示项，不进合成分数。
    HIST_KEYS = ("q_limit_down_count", "up", "down", "up_pct", "limit_down", "limit_down_pct",
                 "median_pct", "amount_ratio", "amount_kind",
                 "limit_up", "q_limit_up")

    def _hist_row(item: dict) -> dict:
        out = {"date": item["date"], "value": item.get("panic")}
        for key in HIST_KEYS:
            out[key] = item.get(key)
        return out

    hist = [_hist_row(r) for r in series if r.get("panic") is not None]
    if not series or series[-1].get("date") != row.get("date"):
        hist.append(_hist_row(row))

    return {
        "ok": True,
        "asof": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": row.get("date"),
        "mode": kind,
        "live": bool(row.get("live", kind == "live" and source.startswith("eltdx"))),
        "source": source,
        "window": legacy.WINDOW,
        "value": value,
        "state": row.get("state") or legacy.state_of(value),
        "items": items,
        "series": hist[-120:],
        "stage": stage_info or None,
        # 量能第四项的旁注（并列输出：预估全天比值 vs 昨日同时刻比值，供人工判读）
        "volume": {"amount": row.get("amount"), "kind": row.get("amount_kind"),
                   "prev_day_amount": row.get("amount_prev"),
                   "ratio": row.get("amount_ratio"),
                   "ratio_same_time": row.get("amount_ratio_same_time"),
                   "percentile": row.get("q_amount"),
                   "state": vol_state(row), "source": row.get("amount_source")},
        # 当日恐慌分时（2026-11-18 主人：要「当日的恐慌走势图」，不是与昨日对比）：
        #   由 tick_feed 每 60s 落原始点，这里现算分位/恐慌值（与日频同函数）。无记录 → []。
        "intraday_series": intraday_series(store_doc, str(row.get("date") or day8), legacy, kind),
        "note": ("恐慌广度=价三维（跌停占比/上涨占比/全A中位）+ 量一维（当日全天额÷昨日全天额，"
                 "盘中用预估、只在下跌日计入）250 日滚动分位（越高越恐慌）反向均值；低值=恐慌；"
                 "涨停家数为展示项，不进分数"),
    }


def payload(quotes: Any, store_doc: dict[str, Any] | None = None,
            now_dt: datetime | None = None) -> dict[str, Any]:
    """端点主路径：读库 → (需要时)取快照 → 装配。旧 `api_panic` 的处理顺序照搬。

    快照只在「库末行不是今天 且 处于 live/close」时取（旧端同：省一次行情连接）。
    """
    doc = store_doc if isinstance(store_doc, dict) else load_store_doc()
    now_dt = now_dt or datetime.now()
    kind, day8 = session_kind(now_dt)
    series = list(doc.get("series") or [])
    need_snap = not (series and series[-1].get("date") == day8) and kind in ("live", "close")
    snap = snapshot(quotes) if need_snap else {}
    return build_payload(doc, snap, stage(), now_dt)
