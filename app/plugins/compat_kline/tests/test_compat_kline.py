# -*- coding: utf-8 -*-
"""compat_kline 插件契约测试：manifest(root 挂载) / 路由 / 8 端点字段形状 / 静态禁令。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import (  # noqa: E402
    find_forbidden_db_usage,
    find_forbidden_quote_usage,
    validate_manifest,
)
from app.plugins.compat_kline import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]

# 旧 8800 逐端点的顶层键（契约样本，见 docs/compat_kline.md §2）
# ⚠ has_more / next_before **只出现在 before 翻页响应里**（旧端 _build_kline_before 才追加）。
OLD_KLINE_TOP = {
    "version", "code", "period", "adj", "asof", "stale", "source", "vol_unit",
    "prev_close", "boards", "bars", "count", "mf", "indicators",
}
OLD_KLINE_TOP_PAGED = OLD_KLINE_TOP | {"has_more", "next_before"}
OLD_KLINE_BAR = {
    "t", "o", "h", "l", "c", "v", "yellow", "white", "amv", "amvMa", "amvWhite",
    "chg", "amp", "turnover", "amount",
}
OLD_INTRADAY_TOP = {
    "version", "code", "date", "adj", "stale", "vol_unit", "source", "prev_close",
    "session", "auction", "points", "count", "date_fallback",
}
OLD_INTRADAY_POINT = {"t", "open", "high", "low", "close", "avg", "volume", "amount", "cum_volume"}
OLD_MULTI_TOP = {
    "version", "code", "end", "start", "before", "after", "adj", "stale", "vol_unit",
    "date_fallback", "source", "more", "session", "days", "count",
}
OLD_QUOTE5_TOP = {
    "code", "last", "pre_close", "change", "change_pct", "high", "low",
    "limit_up", "limit_down", "quote_time", "asks", "bids", "stats", "ticks",
    "asof", "source",
}
OLD_VP_TOP = {
    "code", "buckets", "max_v", "total_v", "pre_close", "limit_up", "limit_down",
    "asof", "source",
}
OLD_VT_TOP = {"code", "pre_close", "limit_up", "limit_down", "rows", "asof", "source"}
OLD_F10_TOP = {
    "code", "asof", "source", "sector", "ipo_date", "is_new", "total_mcap",
    "float_mcap", "pe_ttm", "pe_pct", "pb_mrq", "pb_pct", "holders",
    "holder_report", "main_net_today", "main_ratio_today", "main_net_5d",
    "gd_plans", "north", "eps_forecast", "perf", "rzrq", "perf_q", "gdhs",
    # 2026-10-27 新增：数据的**真实产生时间**（B2 落盘缓存命中时会比 asof 旧很多）。
    # 前端据此显示「N 分钟前」；主人口径："可以接受旧数据，但必须标出多久前"。
    # 与旧端形状的差异仅此一字段（只增不改，旧消费方可忽略）。
    "fetched_at",
}
OLD_BG_TOP = {"mode", "code", "name", "level", "items", "reason"}


# ---------------------------------------------------------------- 假行情门面 / ctx


class _Bar:
    def __init__(self, ts: str, o: float, h: float, l: float, c: float,
                 lots: float = 100.0, amount: float = 1e6):
        self.time = datetime.strptime(ts, "%Y%m%d%H%M")
        self.open, self.high, self.low, self.close = o, h, l, c
        self.volume_lots = lots
        self.amount = amount


class _Tick:
    def __init__(self, hhmm: str, price: float, vol: int, kind: str = "trade"):
        self.time_label = hhmm
        self.price = price
        self.volume = vol
        self.side = "B"
        self.order_count = 1
        self.event_kind = kind


class _Lv:
    def __init__(self, price: float, vol: int):
        self.price = price
        self.volume = vol


class _Rec:
    last_price = 10.5
    last_close_price = 10.0
    high_price = 10.8
    low_price = 9.9
    update_time_raw = 113002
    buy_levels = [_Lv(10.49, 3), _Lv(10.48, 5)]
    sell_levels = [_Lv(10.51, 2), _Lv(10.52, 7)]
    total_hand = 1000
    current_hand = 4
    outer_disc = 600
    inside_dish = 400


class _FakeQuotes:
    """形状与 `app/core/quotes.py` 一致（含 call，供 K 线/竞价/资金流取数）。"""

    def __init__(self, *, bars=None, daily=None, ticks=None, rec=None, aux=None,
                 days=None, day_pages=None, calls=None):
        self._bars = bars or []
        self._daily = daily or []
        self._ticks = ticks or []
        self._rec = rec
        self._aux = aux or {}
        self._days = days or {}
        self._day_pages = day_pages or {}
        self._calls = calls if calls is not None else []

    def normalize(self, code):
        from app.core.quotes import normalize
        return normalize(code)

    def call(self, fn_name, *args, **kwargs):
        self._calls.append((fn_name, args, kwargs))
        if fn_name == "bars.get":
            period = kwargs.get("period")
            if period in ("day", "week"):
                return _Page(self._daily)
            return _Page(self._bars)
        return None

    def minute_bars(self, glb, count=241):
        return self._bars

    def minute_bars_days(self, glb, days=5):
        return self._days

    def today_ticks(self, glb, pages=6, page_size=1800):
        return self._ticks

    def depth(self, glb):
        return self._rec

    def stock_aux(self, glb):
        return self._aux

    def daily_bars(self, glb, count=8, adjust=None):
        return self._daily[-count:]


class _Page:
    def __init__(self, bars):
        self.bars = list(bars)


class _StubMf:
    """`ctx.lib("l2_mf")` 替身：结构同真库（build_mf + 常量），**零网络**。

    真库 `build_mf` 会自己连 7709 取 `.cah`（口径库允许自连），测试一律用本替身。
    `rows` 为预置的逐日结果（按调用时 dates8 的索引映射）；`source` 控制降级分支。
    """

    DEFAULT_LOOKBACK = 20
    LOOKBACK_MIN = 1
    LOOKBACK_MAX = 120

    def __init__(self, source: str = "live", main: float = 12.5):
        self.source = source
        self.main = main
        self.calls = []

    def build_mf(self, ts_code, dates8, closes=None, lookback=None, records=None):
        self.calls.append({"ts_code": ts_code, "dates8": list(dates8),
                           "closes": closes, "lookback": lookback, "records": records})
        n = len(dates8)
        if self.source == "none":
            return {"lookback": lookback or 20,
                    "主力资金": [None] * n, "主买": [None] * n,
                    "占成交": [None] * n, "占流通": [None] * n, "q": [None] * n,
                    "source": "none"}
        return {"lookback": lookback or 20,
                "主力资金": [self.main] * n, "主买": [self.main / 2] * n,
                "占成交": [1.5] * n, "占流通": [0.2] * n, "q": [0.8] * n,
                "source": self.source}


def _mk_ctx(tmp_path: Optional[Path] = None, *, quotes=None,
            amv_rows=None, state_rows=None, board=True, libs=None,
            index_rows=None, sector_rows=None, index_meta_rows=None,
            board_name="白酒", sector_name=None, index_code="881121.TI"):
    """造一个 Ctx：quotes + 可选 amv_board.db / ths_board.db / ths_index.db + 口径库替身。

    `libs`：{库名: 替身对象 / 工厂 callable / Exception}。给了就用替身（**零网络**）；
    未给的名字走真实口径库。**`l2_mf` 默认一律替身**：真实库会连 7709 取 `.cah`，
    测试不得触网（任务书硬约束）。需要真实口径的用例显式传 `libs={"l2_mf": ...}`。

    `index_rows`：`[(date8, o, h, l, c, volume, turnover), ...]` → tmp `ths_index.db::index_daily`
    （`code` 列由 `index_code` 统一填）；
    `sector_rows`：`[(date_iso, level, code, amv), ...]` → 同一 tmp `amv_board.db::sector_daily`；
    `index_meta_rows`：`[(key, value), ...]` → `ths_index.db::index_meta`（只为对齐真实表结构）。
    三者任一非 None 时才建对应的库（不建 = 模拟「库缺失」，用于空库降级用例）。
    """
    if libs is None:
        libs = {"l2_mf": _StubMf()}
    import sqlite3

    db = None
    if amv_rows is not None or state_rows is not None or sector_rows is not None:
        assert tmp_path is not None
        db = tmp_path / "amv_board.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE stock_amv(date TEXT, stock TEXT, amv REAL, "
                    "PRIMARY KEY(date,stock))")
        con.execute("CREATE TABLE stock_state(date TEXT, stock TEXT, sh REAL, "
                    "PRIMARY KEY(date,stock))")
        con.execute("CREATE TABLE stock_top(date TEXT, stock_code TEXT, stock_name TEXT)")
        con.execute("CREATE TABLE sector_daily(date TEXT, level TEXT, code TEXT, amv REAL)")
        con.execute("CREATE TABLE sector_meta(level TEXT, code TEXT, name TEXT, "
                    "parent TEXT, a INTEGER, b INTEGER, sw_level INTEGER, ord INTEGER)")
        if amv_rows:
            con.executemany("INSERT OR REPLACE INTO stock_amv VALUES(?,?,?)", amv_rows)
        if state_rows:
            con.executemany("INSERT OR REPLACE INTO stock_state VALUES(?,?,?)", state_rows)
        if sector_rows:
            con.executemany("INSERT OR REPLACE INTO sector_daily VALUES(?,?,?,?)", sector_rows)
        if sector_name:
            con.execute("INSERT INTO sector_meta VALUES('L2','881121.TI',?,'半导体',0,0,2,0)",
                        (sector_name,))
        con.commit()
        con.close()

    idx = None
    if index_rows is not None:
        assert tmp_path is not None
        idx = tmp_path / "ths_index.db"
        con = sqlite3.connect(str(idx))
        con.execute("CREATE TABLE index_daily(date TEXT, code TEXT, o REAL, h REAL, l REAL, "
                    "c REAL, volume REAL, turnover REAL, source TEXT, filled_at TEXT, "
                    "PRIMARY KEY(date,code))")
        con.execute("CREATE TABLE index_meta(key TEXT PRIMARY KEY, value TEXT)")
        if index_rows:
            con.executemany(
                "INSERT OR REPLACE INTO index_daily VALUES(?,?,?,?,?,?,?,?,'test',NULL)",
                [(r[0], index_code) + tuple(r[1:]) for r in index_rows])
        if index_meta_rows:
            con.executemany("INSERT OR REPLACE INTO index_meta VALUES(?,?)", index_meta_rows)
        con.commit()
        con.close()

    tdb = None
    if board and tmp_path is not None:
        tdb = tmp_path / "ths_board.db"
        con = sqlite3.connect(str(tdb))
        con.execute("CREATE TABLE board(code TEXT, name TEXT, sw_level INTEGER)")
        con.execute("CREATE TABLE board_member(stock TEXT, board_code TEXT)")
        con.execute("CREATE TABLE concept(code TEXT, name TEXT)")
        con.execute("CREATE TABLE concept_member(stock TEXT, concept_code TEXT)")
        con.execute("CREATE TABLE broad_index(code TEXT, name TEXT, n_members INTEGER, "
                    "updated_at TEXT)")
        con.execute("INSERT INTO board VALUES('881101.TI',?,2)", (board_name,))
        con.execute("INSERT INTO board VALUES('881121.TI',?,2)", (board_name,))
        con.execute("INSERT INTO board_member VALUES('600519','881101.TI')")
        con.execute("INSERT INTO board_member VALUES('000688','881101.TI')")
        con.execute("INSERT INTO board_member VALUES('000852','881121.TI')")
        # 宽基指数目录：与深市个股同 6 位号的撞码回归样本（2026-11-02）
        con.execute("INSERT INTO broad_index VALUES('000688.SH','科创50',50,'2026-11-01')")
        con.execute("INSERT INTO broad_index VALUES('000905.SH','中证500',500,'2026-11-01')")
        con.commit()
        con.close()

    class Ctx:
        pass

    ctx = Ctx()
    ctx.quotes = quotes if quotes is not None else _FakeQuotes()

    def db_read(name):
        target = {"amv_board.db": db, "ths_board.db": tdb,
                  "ths_index.db": idx}.get(name)
        if target is None:
            raise FileNotFoundError(name)
        con = sqlite3.connect("file:%s?mode=ro" % str(target).replace("\\", "/"), uri=True)
        con.row_factory = sqlite3.Row
        return con

    ctx.db_read = db_read

    def lib(name):
        if libs is not None and name in libs:
            stub = libs[name]
            if isinstance(stub, Exception):
                raise stub
            if callable(stub) and not hasattr(stub, "build_mf"):
                return stub()
            return stub
        import importlib.util
        from app.core import paths as core_paths
        path = core_paths.lib_path(name)
        spec = importlib.util.spec_from_file_location("_t_" + name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_t_" + name] = mod
        parent = str(path.parent)
        inserted = parent not in sys.path
        if inserted:
            sys.path.append(parent)
        try:
            spec.loader.exec_module(mod)
        finally:
            if inserted:
                sys.path.remove(parent)
        return mod

    ctx.lib = lib
    return ctx


def _client(ctx, prefix="/api"):
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix=prefix)
    return TestClient(app)


def _dbars(n: int = 5, start: str = "20261001") -> List[_Bar]:
    out = []
    for i in range(n):
        day = "202610%02d" % (1 + i)
        c = 10.0 + i
        out.append(_Bar(day + "1500", c - 0.2, c + 0.3, c - 0.4, c, lots=100.0, amount=1e6))
    return out


def _mbars() -> List[_Bar]:
    return [_Bar("202610230931", 10.0, 10.1, 9.9, 10.0, lots=100.0, amount=1e6),
            _Bar("202610230932", 10.05, 10.2, 10.0, 10.1, lots=200.0, amount=2e6)]


# ---------------------------------------------------------------- manifest / 路由


def test_manifest_mounts_at_root():
    raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    m = validate_manifest(raw, PLUGIN_DIR)
    assert m.id == "compat_kline"
    assert m.backend.mount == "root", "旧前端请求 /api/<名>，必须挂到 /api"
    assert m.data_write == ()
    assert m.quotes_read is True
    assert sorted(m.data_read) == ["amv_board.db", "ths_board.db", "ths_index.db"]
    assert m.lib_read == ("amv_regime", "indicators", "l2_mf")


def test_register_exposes_nine_root_paths():
    ctx = _mk_ctx()
    router = backend.register(ctx)
    assert isinstance(router, APIRouter)
    paths = {r.path for r in router.routes}
    assert paths == {
        "/kline/{ts_code}", "/kline_bg/{ts_code}", "/intraday/{ts_code}",
        "/intraday_multi/{ts_code}", "/quote5/{ts_code}", "/volume_profile/{ts_code}",
        "/volume_ticks/{ts_code}", "/f10_brief/{ts_code}",
        # 2026-10-27 新增：轻量 F10（只取 valuation），供弹窗首屏 500ms 内出值
        "/f10_brief/{ts_code}/quick",
        # 2026-11-04 新增：多票 spark 批量（自选池主图/副图专用）
        "/kline_sparks",
    }, paths
    # 不写 /api 前缀（core 负责拼 /api + path）
    assert all(not p.startswith("/api") for p in paths)


def test_plugin_has_no_http_no_forbidden_imports():
    src = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    assert not find_forbidden_db_usage(src), "禁止直连数据库"
    assert not find_forbidden_quote_usage(src), "禁止自己连行情源"
    for token in ("import requests", "import httpx", "requests.", "httpx.",
                  "urllib", "socket.", "subprocess"):
        assert token not in src, token
    for other in ("app.plugins.kline", "app.plugins.sector", "app.plugins.amv"):
        assert other not in src, "禁止插件互相 import：%s" % other


# ---------------------------------------------------------------- /api/kline


def test_kline_csi_suffix_maps_to_exchange_code(tmp_path):
    """2026-11-01：板块目录里的 `000510.CSI`（中证指数标注）要能取到日线。

    现场：指数层右栏中证 A500 一直显示「无收盘价数据」——`000510.CSI` 直接打到
    /api/kline 被 core 的 normalize 当未知后缀拒（400），前端 catch 成空 bars。
    换算口径：中证指数代码段与交易所同号（000xxx → sh，399xxx → sz）；
    实测 eltdx `sh000510` = 中证A500（前收 4884.90）。
    """
    quotes = _FakeQuotes(daily=_dbars(5))
    ctx = _mk_ctx(tmp_path, quotes=quotes)
    c = _client(ctx)
    body = c.get("/api/kline/000510.CSI?period=d&limit=5").json()
    assert body["code"] == "000510.SH", body["code"]
    assert len(body["bars"]) == 5
    # 399xxx 走深市（将来目录出现 399xxx.CSI 时不能一概当沪市）
    body2 = c.get("/api/kline/399975.CSI?period=d&limit=5").json()
    assert body2["code"] == "399975.SZ", body2["code"]


def test_kline_shape_matches_old_8800(tmp_path):
    quotes = _FakeQuotes(daily=_dbars(5))
    ctx = _mk_ctx(tmp_path, quotes=quotes,
                  amv_rows=[("2026-10-%02d" % (1 + i), "600519", 1e10 + i)
                            for i in range(5)])
    body = _client(ctx).get("/api/kline/600519.SH?period=d&limit=5").json()
    assert set(body) == OLD_KLINE_TOP, set(body) ^ OLD_KLINE_TOP
    for bar in body["bars"]:
        assert set(bar) == OLD_KLINE_BAR, set(bar) ^ OLD_KLINE_BAR
    assert body["vol_unit"] == "股"
    assert body["adj"] == "front"
    assert body["version"].startswith("kline-")
    assert body["code"] == "600519.SH" and body["period"] == "d"
    assert body["count"] == len(body["bars"]) == 5
    assert body["bars"][0]["t"] == "20261001"
    # 成交量：eltdx 手 → 股
    assert body["bars"][-1]["v"] == 10000.0
    # 首根无前收 → chg/amp 为 null（不编造）
    assert body["bars"][0]["chg"] is None and body["bars"][0]["amp"] is None
    assert body["bars"][1]["chg"] == 10.0            # 11 / 10 - 1
    # 黄白线与 AMV 三线都要在（AMV 有数据才有值）
    assert body["bars"][-1]["white"] is not None
    assert body["bars"][-1]["amv"] is not None
    # 换手率需要流通股本（本轮无 stock_state → null）
    assert body["bars"][-1]["turnover"] is None
    # mf（2026-10-24 S1）：日线已接真口径库（`ctx.lib("l2_mf")`）。
    # 本用例注入替身（零网络）→ 各列与 bars 逐位对齐、source 透传。
    assert isinstance(body["mf"], dict)
    assert body["mf"]["lookback"] == 20
    assert len(body["mf"]["主力资金"]) == len(body["bars"]) == 5
    assert body["mf"]["主力资金"][0] == 12.5
    assert body["mf"]["source"] == "live"
    assert set(body["mf"]) == {"lookback", "主力资金", "主买", "占成交", "占流通", "q", "source"}
    assert set(body["indicators"]) == {"yellow", "white"}
    # 板块归属走 ths_board.db
    assert body["boards"]["industry"] == ["白酒"]


def test_kline_broad_index_no_stock_industry(tmp_path):
    """2026-11-02 撞码回归：宽基指数（000688.SH 科创50）不得拿到深市同名个股的行业。

    ths_board.db::board_member 只存 6 位码，000688 在表里是国城矿业（小金属/钼）；
    _boards 必须先查 broad_index 判指数身份 → 行业/概念全空。
    """
    quotes = _FakeQuotes(daily=_dbars(3))
    ctx = _mk_ctx(tmp_path, quotes=quotes)
    body = _client(ctx).get("/api/kline/000688.SH?period=d&limit=3").json()
    assert body["boards"]["industry"] == []
    assert body["boards"]["industry3"] == []
    assert body["boards"]["concepts"] == []
    # 个股路径不受影响：600519 照常回行业
    body2 = _client(ctx).get("/api/kline/600519.SH?period=d&limit=3").json()
    assert body2["boards"]["industry"] == ["白酒"]


def test_f10_brief_broad_index_returns_skeleton(tmp_path):
    """2026-11-02 撞码回归：F10 侧栏对宽基指数不得回同名个股的基本面。

    000905.SH（中证500）曾拿到 000905.SZ 厦门港务的「港口航运/港口」行业与整包估值。
    F10 全部 Entry 按 6 位码查，无法逐段去市场缀 → 指数直接回骨架空值。
    """
    quotes = _FakeQuotes(daily=_dbars(3))
    ctx = _mk_ctx(tmp_path, quotes=quotes)
    body = _client(ctx).get("/api/f10_brief/000905.SH").json()
    assert body["sector"] is None
    assert body["total_mcap"] is None and body["pe_ttm"] is None
    assert body["code"] == "000905.SH"
    assert "broad_index" in (body["source"] or "")
    # quick 端点同样回空 val
    quick = _client(ctx).get("/api/f10_brief/000905.SH/quick").json()
    assert quick["val"] == {} and quick["code"] == "000905.SH"
    # 个股路径不受影响（走门面缺失降级，但不得被指数拦截）
    body2 = _client(ctx).get("/api/f10_brief/600519.SH").json()
    assert body2["code"] == "600519.SH"
    assert "broad_index" not in (body2["source"] or "")


def test_kline_bg_broad_index_no_sector_regime(tmp_path):
    """2026-11-02 撞码回归：多空背景带 l2/l3/stock 三档对宽基指数均不撞个股。"""
    quotes = _FakeQuotes(daily=_dbars(3))
    ctx = _mk_ctx(tmp_path, quotes=quotes)
    for mode in ("l2", "l3", "stock"):
        body = _client(ctx).get("/api/kline_bg/000688.SH?mode=%s" % mode).json()
        assert body["items"] == {}, "mode=%s 不得画出深市同名个股的背景带" % mode


def test_kline_first_bar_prev_close_and_turnover(tmp_path):
    quotes = _FakeQuotes(daily=_dbars(3))
    ctx = _mk_ctx(tmp_path, quotes=quotes,
                  amv_rows=[], state_rows=[("2026-10-23", "600519", 1e6)])
    # 有 stock_state.sh → 换手率算得出来（10000 股 / 1e6 = 1%）
    body = _client(ctx).get("/api/kline/600519.SH?period=d&limit=3").json()
    assert body["bars"][-1]["turnover"] == 1.0


def test_kline_degrades_to_empty_bars_when_source_unavailable():
    """取数不可用 → 200 + 空 bars + stale=True（旧端同语义：不报 5xx、不编造）。"""
    ctx = _mk_ctx(quotes=_FakeQuotes(daily=[]))
    r = _client(ctx).get("/api/kline/600519.SH?period=d&limit=5")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == OLD_KLINE_TOP
    assert body["bars"] == [] and body["count"] == 0
    assert body["stale"] is True and body["source"] == "none"
    assert body["asof"] is None and body["prev_close"] is None
    # 旧端：无 bars 时 mf 也是 null（不是空骨架）
    assert body["mf"] is None
    assert "has_more" not in body, "默认窗口不带分页键（旧端同）"


def test_kline_rejects_bad_code_and_period():
    ctx = _mk_ctx(quotes=_FakeQuotes(daily=_dbars(3)))
    c = _client(ctx)
    assert c.get("/api/kline/abcdef").status_code == 400
    assert c.get("/api/kline/600519.SH?period=x1").status_code == 400


def test_kline_index_rejects_minute_periods(tmp_path):
    """板块指数无分钟/分时源 → 400 且文案点明原因（不静默空图）。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), index_rows=[])
    c = _client(ctx)
    for p in ("m1", "m5", "m15", "m30", "m60", "x1"):
        r = c.get("/api/kline/881121.TI?period=%s" % p)
        assert r.status_code == 400, p
        assert "仅日线" in r.json()["detail"], r.json()


def test_kline_index_before_not_validated_like_old(tmp_path):
    """板块分支的 `before` **不校验也不归一**（旧端实际行为，照搬不「修」）。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), index_rows=[])
    c = _client(ctx)
    assert c.get("/api/kline/881121.TI?before=2026-1").status_code == 200
    assert c.get("/api/kline/881121.TI?before=20261332").status_code == 200


def test_kline_limit_clamped_to_old_range():
    ctx = _mk_ctx(quotes=_FakeQuotes(daily=_dbars(5)))
    c = _client(ctx)
    assert c.get("/api/kline/600519.SH?limit=1").json()["count"] == 5  # 5 根全给（<120 下限）
    assert backend.KLINE_LIMIT_MIN == 120 and backend.KLINE_LIMIT_MAX == 800


def test_kline_before_pagination():
    quotes = _FakeQuotes(daily=_dbars(5))
    ctx = _mk_ctx(quotes=quotes)
    body = _client(ctx).get("/api/kline/600519.SH?period=d&limit=120&before=20261004").json()
    assert set(body) == OLD_KLINE_TOP_PAGED
    assert [b["t"] for b in body["bars"]] == ["20261001", "20261002", "20261003"]
    assert body["has_more"] is False
    assert body["next_before"] == "20261001"


def test_mf_records_deeper_on_paging(tmp_path):
    """标注 N87：首页 .cah 取 700，翻页（before）取 1400——否则主力资金到 700 根就断。"""
    stub = _StubMf()
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(daily=_dbars(5)), libs={"l2_mf": stub})
    c = _client(ctx)
    c.get("/api/kline/600519.SH?period=d&limit=3")
    assert stub.calls[-1]["records"] == backend.MF_RECORDS_DEFAULT == 700
    c.get("/api/kline/600519.SH?period=d&limit=3&before=20261004")
    assert stub.calls[-1]["records"] == backend.MF_RECORDS_PAGED == 1400


# ---------------------------------------------------------------- /api/kline_bg


def test_kline_bg_stock_mode_items_mapping(tmp_path):
    vals = []
    for i in range(80):
        d = "2026%02d%02d" % (3 + i // 28, i % 28 + 1)   # 唯一日期（主键不冲突）
        vals.append((d, "600519", 1e10 + i * 1e8))
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), amv_rows=vals)
    body = _client(ctx).get("/api/kline_bg/600519.SH?mode=stock").json()
    assert set(body) == OLD_BG_TOP
    assert body["mode"] == "stock" and body["code"] == "600519"
    assert isinstance(body["items"], dict) and body["items"]
    # 日期键必须是无横线的 YYYYMMDD
    assert all(len(k) == 8 and k.isdigit() for k in body["items"])
    # 状态只能是四态或 None（None 不进 items）
    assert all(v in ("多", "偏多", "空", "偏空") for v in body["items"].values())


def test_kline_bg_l2_lookup(tmp_path):
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), amv_rows=[], state_rows=[])
    body = _client(ctx).get("/api/kline_bg/600519.SH?mode=l2").json()
    assert body["code"] == "881101.TI" and body["name"] == "白酒" and body["level"] == 2
    assert body["reason"] == "" and body["items"] == {}   # sector_daily 无数据 → 空（不编造）


def test_kline_bg_rejects_bad_mode():
    ctx = _mk_ctx(quotes=_FakeQuotes())
    assert _client(ctx).get("/api/kline_bg/600519.SH?mode=xx").status_code == 400


# ---------------------------------------------------------------- 分时


def test_intraday_shape_and_points():
    quotes = _FakeQuotes(days={"20261023": _mbars()}, daily=_dbars(5))
    ctx = _mk_ctx(quotes=quotes)
    body = _client(ctx).get("/api/intraday/600519.SH").json()
    assert set(body) == OLD_INTRADAY_TOP, set(body) ^ OLD_INTRADAY_TOP
    assert body["session"]["slot_count"] == 241
    assert body["vol_unit"] == "股" and body["adj"] == "front"
    assert body["code"] == "600519.SH"
    for p in body["points"]:
        assert set(p) == OLD_INTRADAY_POINT
    # 09:31 起 → 补 09:30 开盘点（成交量 0）
    assert body["points"][0]["t"] == "09:30" and body["points"][0]["volume"] == 0.0
    assert body["points"][1]["t"] == "09:31"
    assert body["points"][1]["volume"] == 10000.0
    assert body["count"] == len(body["points"])
    assert isinstance(body["auction"], list)
    # 昨收取严格早于 20261023 的最后一根日线
    assert body["prev_close"] == 14.0

def test_intraday_degrades_without_source():
    ctx = _mk_ctx(quotes=_FakeQuotes(days={}, daily=[]))
    body = _client(ctx).get("/api/intraday/600519.SH").json()
    assert body["points"] == [] and body["count"] == 0
    assert body["source"] == "none" and body["stale"] is True


def test_intraday_multi_shape():
    quotes = _FakeQuotes(days={"20261022": _mbars(), "20261023": _mbars()},
                         daily=_dbars(5))
    ctx = _mk_ctx(quotes=quotes)
    body = _client(ctx).get("/api/intraday_multi/600519.SH?days=5").json()
    assert set(body) == OLD_MULTI_TOP, set(body) ^ OLD_MULTI_TOP
    assert [d["date"] for d in body["days"]] == ["20261022", "20261023"]
    for d in body["days"]:
        assert set(d) == {"date", "source", "prev_close", "points", "count"}
    # 20261022 的昨收 = 20261005 收盘（日线最后一根在它之前）
    assert body["days"][0]["prev_close"] == 14.0
    # 链推：次日昨收 = 本日末根收盘
    assert body["days"][1]["prev_close"] == 10.1
    assert body["count"] == sum(d["count"] for d in body["days"])
    assert body["vol_unit"] == "股" and body["adj"] == "front"


def test_intraday_multi_degrades():
    ctx = _mk_ctx(quotes=_FakeQuotes(days={}))
    body = _client(ctx).get("/api/intraday_multi/600519.SH").json()
    assert body["days"] == [] and body["count"] == 0 and body["stale"] is True


# ---------------------------------------------------------------- 五档 / 筹码 / 逐笔


def test_quote5_shape_without_ok_key():
    quotes = _FakeQuotes(rec=_Rec(), aux={"avg5": 2000.0, "circ": 1e8,
                                         "limit_up": 11.0, "limit_down": 9.0},
                         ticks=[_Tick("11:30:02", 10.5, 3)])
    ctx = _mk_ctx(quotes=quotes)
    body = _client(ctx).get("/api/quote5/600519.SH").json()
    assert set(body) == OLD_QUOTE5_TOP, set(body) ^ OLD_QUOTE5_TOP
    assert "ok" not in body, "旧端无 ok 字段"
    assert body["last"] == 10.5 and body["pre_close"] == 10.0
    assert body["change"] == 0.5 and body["change_pct"] == 5.0
    assert body["quote_time"] == "11:30:02"
    assert [x["price"] for x in body["bids"]] == [10.49, 10.48]
    assert [x["price"] for x in body["asks"]] == [10.51, 10.52]
    assert set(body["stats"]) == {"now_vol", "total_vol", "outer", "inner",
                                  "turnover", "vol_ratio"}
    assert body["stats"]["turnover"] == 0.1        # 1000 手 ×100 / 1e8 × 100%
    assert body["stats"]["vol_ratio"] is not None
    assert set(body["ticks"][0]) == {"t", "price", "vol", "side", "n"}


def test_quote5_degrades_without_depth():
    ctx = _mk_ctx(quotes=_FakeQuotes(rec=None, aux={}))
    body = _client(ctx).get("/api/quote5/600519.SH").json()
    assert body["last"] is None and body["asks"] == [] and body["bids"] == []


def test_elapsed_minutes_matches_old():
    assert backend._elapsed_minutes("09:30") == 1
    assert backend._elapsed_minutes("11:30") == 120
    assert backend._elapsed_minutes("13:00") == 120
    assert backend._elapsed_minutes("15:00") == 240


def test_volume_profile_and_until():
    ticks = [_Tick("09:25", 10.0, 500, kind="auction"),   # 竞价剔除
             _Tick("09:31", 10.0, 100), _Tick("09:32", 10.0, 50),
             _Tick("09:33", 10.5, 30)]
    ctx = _mk_ctx(quotes=_FakeQuotes(ticks=ticks, rec=_Rec(),
                                     aux={"limit_up": 11.0, "limit_down": 9.0}))
    c = _client(ctx)
    body = c.get("/api/volume_profile/600519.SH").json()
    assert set(body) == OLD_VP_TOP, set(body) ^ OLD_VP_TOP
    assert {b["p"]: b["v"] for b in body["buckets"]} == {10.0: 150, 10.5: 30}
    assert body["total_v"] == 180 and body["max_v"] == 150
    assert body["pre_close"] == 10.0 and body["limit_up"] == 11.0
    d2 = c.get("/api/volume_profile/600519.SH?until=09:32").json()
    assert {b["p"]: b["v"] for b in d2["buckets"]} == {10.0: 150}


def test_volume_profile_empty_when_no_ticks():
    ctx = _mk_ctx(quotes=_FakeQuotes(ticks=[], rec=None, aux={}))
    body = _client(ctx).get("/api/volume_profile/600519.SH").json()
    assert body["buckets"] == [] and body["total_v"] == 0 and body["max_v"] == 0


def test_volume_ticks_rows_and_axis():
    ticks = [_Tick("09:25", 10.0, 500, kind="auction"),
             _Tick("09:31", 10.0, 100), _Tick("09:31", 10.0, 50),
             _Tick("13:00", 10.5, 70), _Tick("15:00", 10.6, 20)]
    ctx = _mk_ctx(quotes=_FakeQuotes(ticks=ticks, rec=_Rec(),
                                     aux={"limit_up": 11.0, "limit_down": 9.0}))
    body = _client(ctx).get("/api/volume_ticks/600519.SH").json()
    assert set(body) == OLD_VT_TOP, set(body) ^ OLD_VT_TOP
    rows = body["rows"]
    assert all(isinstance(r, list) and len(r) == 3 for r in rows)
    # 09:31 → idx 1；同 (分钟, 价位) 合并求和
    assert [1, 10000, 150] in rows
    # 13:00 归到 13:01（idx 121），15:00 → 240
    assert [121, 10500, 70] in rows
    assert [240, 10600, 20] in rows
    assert body["pre_close"] == 10.0 and body["limit_up"] == 11.0
    # 竞价（event_kind != trade）必须被剔除
    assert all(r[2] != 500 for r in rows)


# ---------------------------------------------------------------- F10


def test_f10_brief_skeleton_keys():
    ctx = _mk_ctx(quotes=_FakeQuotes())
    body = _client(ctx).get("/api/f10_brief/600519.SH").json()
    assert set(body) == OLD_F10_TOP, set(body) ^ OLD_F10_TOP
    assert body["code"] == "600519.SH"
    # B2 段未实现 → null / []（不编造）
    assert body["pe_ttm"] is None and body["total_mcap"] is None
    assert body["gd_plans"] == [] and body["rzrq"] == []
    # B1 段不可用 → 各 null（不是缺键）
    assert body["main_net_today"] is None and body["holders"] is None


def test_f10_brief_b1_from_socket():
    class _MF:
        class _R:
            main_net = 1234.5
            main_ratio = 0.0321
        records = [_R()]

    class _FB:
        class _R:
            gu_dong_ren_shu_raw_float = 296404.0
            updated_date = "20260930"
            ipo_date = "20010827"
        records = [_R()]

    class _Q(_FakeQuotes):
        def call(self, fn_name, *args, **kwargs):
            if fn_name == "money_flow.daily":
                return _MF()
            if fn_name == "corporate.finance_batch":
                return _FB()
            return super().call(fn_name, *args, **kwargs)

    ctx = _mk_ctx(quotes=_Q())
    body = _client(ctx).get("/api/f10_brief/600519.SH").json()
    assert body["main_net_today"] == 1234.5
    assert body["main_ratio_today"] == 0.0321
    assert body["main_net_5d"] == 1234.5
    assert body["holders"] == 296404
    assert body["holder_report"] == "2026-09-30"
    assert body["ipo_date"] == "2001-08-27"
    assert body["is_new"] is False


# ---------------------------------------------------------------- mf（主力动向）


def test_mf_only_for_daily_and_aligned(tmp_path):
    """mf 仅日线有值；周线/分钟为 null（旧端同）；数组与 bars 逐位对齐。"""
    stub = _StubMf()
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(daily=_dbars(4), bars=_mbars()),
                  libs={"l2_mf": stub})
    c = _client(ctx)
    daily = c.get("/api/kline/600519.SH?period=d&limit=4").json()
    assert isinstance(daily["mf"], dict)
    assert len(daily["mf"]["主力资金"]) == len(daily["bars"])
    assert daily["mf"]["主力资金"] == [12.5] * len(daily["bars"])
    # 周线：mf 必须是 null（公式只建议日线，旧端周线不下发）
    week = c.get("/api/kline/600519.SH?period=w&limit=4").json()
    assert week["mf"] is None
    minute = c.get("/api/kline/600519.SH?period=m5&limit=4").json()
    assert minute["mf"] is None
    # 传参校验：口径库拿到的是 ts_code + **closes=None**（不复权收盘由口径库自取，
    # 不得传 bars 的 qfq 收盘，否则「占流通」与旧端逐位不等）
    assert stub.calls[0]["ts_code"] == "600519.SH"
    assert stub.calls[0]["closes"] is None


def test_mf_lookback_passthrough_and_clamp(tmp_path):
    """mf_lookback 透传到口径库；越界夹到 [1,120]；非法值回默认 20。"""
    stub = _StubMf()
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(daily=_dbars(3)), libs={"l2_mf": stub})
    c = _client(ctx)
    assert c.get("/api/kline/600519.SH?limit=3&mf_lookback=5").json()["mf"]["lookback"] == 5
    assert c.get("/api/kline/600519.SH?limit=3&mf_lookback=9999").json()["mf"]["lookback"] == 120
    assert c.get("/api/kline/600519.SH?limit=3&mf_lookback=0").json()["mf"]["lookback"] == 1
    assert c.get("/api/kline/600519.SH?limit=3").json()["mf"]["lookback"] == 20
    assert stub.calls[0]["lookback"] == 5


def test_mf_lib_unavailable_degrades_without_raising(tmp_path):
    """口径库不可用/取数抛异常 → 骨架降级 + source 如实标注，**不抛**（K 线主体不受影响）。"""
    (tmp_path / "a").mkdir()
    ctx = _mk_ctx(tmp_path / "a", quotes=_FakeQuotes(daily=_dbars(3)),
                  libs={"l2_mf": RuntimeError("no lib")})
    r = _client(ctx).get("/api/kline/600519.SH?limit=3")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3, "mf 失败不得拖垮 K 线主体"
    mf = body["mf"]
    assert set(mf) == {"lookback", "主力资金", "主买", "占成交", "占流通", "q", "source"}
    assert mf["主力资金"] == [] and mf["q"] == []
    assert mf["source"].startswith("unavailable"), mf["source"]
    assert "口径库不可用" in mf["source"]

    class _Boom:
        LOOKBACK_MIN = 1

        def build_mf(self, *a, **k):
            raise RuntimeError("7709 down")

    (tmp_path / "b").mkdir()
    ctx = _mk_ctx(tmp_path / "b", quotes=_FakeQuotes(daily=_dbars(3)),
                  libs={"l2_mf": _Boom()})
    mf = _client(ctx).get("/api/kline/600519.SH?limit=3").json()["mf"]
    assert mf["主力资金"] == [] and "取数失败" in mf["source"]


def test_mf_no_coverage_source_marks_unavailable(tmp_path):
    """口径库回 source=none（无 .cah 覆盖）→ 改写成「unavailable(l2无覆盖)」（与旧端文案一致）。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(daily=_dbars(3)),
                  libs={"l2_mf": _StubMf(source="none")})
    mf = _client(ctx).get("/api/kline/600519.SH?limit=3").json()["mf"]
    assert mf["source"] == "unavailable(l2无覆盖)"
    assert mf["主力资金"] == [None, None, None]


# ---------------------------------------------------------------- F10 B2 段


class _QWithF10(_FakeQuotes):
    """带门面 `f10_brief()` 的假行情：返回预置 B2 payload（或抛异常）。"""

    def __init__(self, payload=None, boom=None, **kw):
        super().__init__(**kw)
        self._payload = payload
        self._boom = boom
        self.f10_calls = []
        self.f10_quick_calls = []

    def f10_brief(self, glb):
        self.f10_calls.append(glb)
        if self._boom is not None:
            raise self._boom
        return self._payload or {}

    def f10_valuation_only(self, glb):
        """轻量口：与生产同形，只给 val + fetched_at（不跑其它 8 段）。"""
        self.f10_quick_calls.append(glb)
        if self._boom is not None:
            raise self._boom
        return {"val": dict((self._payload or {}).get("val") or {}),
                "fetched_at": "2026-10-27 01:00:00"}


_B2_PAYLOAD = {
    "val": {"pe_ttm": 20.49, "pe_pct": 19.06, "pb_mrq": 6.64, "pb_pct": 9.65,
            "total_mcap": 1668858904576.0, "float_mcap": 1668858904576.0},
    "plans": [{"date": "2025-12-30", "dir": "拟增持", "who": "某集团",
               "role": "控股股东", "status": "完成"}],
    "north": {"date": "2026-09-30", "ratio": 4.42, "chg_pct": 2.8896},
    "forecast": {"year": 2026, "eps": 67.191, "org_count": 39},
    "sector": "白酒",
    "perf": {"years": [2025], "revenue": [1148.0895], "net": [823.2007],
             "yoy_rev": [None], "yoy_net": [None]},
    "rzrq": [{"date": "2026-10-23", "rz_net_buy": -2.5e8, "rz_balance": 1.61e10,
              "rz_vol": -900.0, "rq_balance": 2.78e8}],
    "perf_q": {"periods": [{"rq": "2026-06-30", "year": 2026, "q": 2,
                           "net": 172.7437, "yoy_net": -6.9, "rev_yoy": -5.23,
                           "eps": 13.8186, "gross": 89.26}],
               "count": 1, "years": {"2026": {"sum": 445.17, "count": 2}}},
    "gdhs": {"periods": [{"rq": "2026-06-30", "holders": 296404,
                          "avg_hold": 4217.4923, "chg_pct": 21.8972,
                          "avg_chg_pct": -18.107, "close": 1185.49}], "count": 1},
    "source": "eltdx:f10(200191)", "errors": [],
}


def test_f10_b2_fills_all_fields():
    """B2 段接线：15 个差异字段从门面取到值（逐字段与旧端同形）。"""
    q = _QWithF10(payload=_B2_PAYLOAD)
    body = _client(_mk_ctx(quotes=q)).get("/api/f10_brief/600519.SH").json()
    assert set(body) == OLD_F10_TOP, set(body) ^ OLD_F10_TOP
    assert q.f10_calls == ["sh600519"], "门面必须收到 eltdx 写法"
    assert body["source"] == "eltdx:f10(200191)+eltdx:socket(0x0ffc)"
    assert body["sector"] == "白酒"
    assert body["pe_ttm"] == 20.49 and body["pe_pct"] == 19.06
    assert body["pb_mrq"] == 6.64 and body["pb_pct"] == 9.65
    assert body["total_mcap"] == 1668858904576.0
    assert body["float_mcap"] == 1668858904576.0
    assert body["gd_plans"][0]["dir"] == "拟增持"
    assert body["north"]["ratio"] == 4.42
    assert body["eps_forecast"]["eps"] == 67.191
    assert body["perf"]["net"] == [823.2007]
    assert body["rzrq"][0]["rz_net_buy"] == -2.5e8
    assert body["perf_q"]["periods"][0]["net"] == 172.7437
    assert body["perf_q"]["years"]["2026"]["sum"] == 445.17
    assert body["gdhs"]["periods"][0]["holders"] == 296404


def test_f10_b2_degrades_when_facade_fails():
    """门面抛异常 → B2 各字段 null/[]、source 标注降级，B1 照常（不抛、不 500）。"""
    q = _QWithF10(boom=RuntimeError("http down"))
    r = _client(_mk_ctx(quotes=q)).get("/api/f10_brief/600519.SH")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == OLD_F10_TOP
    assert body["pe_ttm"] is None and body["total_mcap"] is None
    assert body["sector"] is None and body["gd_plans"] == [] and body["rzrq"] == []
    assert body["north"] is None and body["eps_forecast"] is None
    assert body["perf"] is None and body["perf_q"] is None and body["gdhs"] is None
    assert "B2部分降级" in body["source"]


def test_f10_b2_missing_facade_is_not_fatal():
    """旧门面（无 f10_brief 方法）→ B2 全空但 200（向后兼容，不回 500）。"""
    body = _client(_mk_ctx(quotes=_FakeQuotes())).get("/api/f10_brief/600519.SH").json()
    assert body["pe_ttm"] is None and body["rzrq"] == []
    assert "facade_missing" in body["source"]


# ---------------------------------------------------------------- 轻量 F10（/quick，首屏用）


def test_f10_quick_returns_only_valuation():
    """轻量口只返回估值四项 + 时间戳，**不得**触发完整 9 段（那是首屏 500ms 的全部意义）。"""
    q = _QWithF10(payload=_B2_PAYLOAD)
    body = _client(_mk_ctx(quotes=q)).get("/api/f10_brief/600519.SH/quick").json()
    assert body["ok"] is True and body["code"] == "600519.SH"
    assert body["val"]["pe_ttm"] == 20.49
    assert body["val"]["total_mcap"] == 1668858904576.0
    assert body["fetched_at"] == "2026-10-27 01:00:00"
    # 轻量口被调用一次；完整 f10_brief **不得**被调用（否则退化回 9 段）
    assert q.f10_quick_calls == ["sh600519"]
    assert q.f10_calls == []


def test_f10_quick_degrades_without_lightweight_hook():
    """旧门面无 `f10_valuation_only` → 200 + 空 val（前端继续等完整段，不报错）。"""
    body = _client(_mk_ctx(quotes=_FakeQuotes())).get("/api/f10_brief/600519.SH/quick").json()
    assert body["ok"] is True and body["val"] == {}


def test_f10_quick_rejects_bad_code():
    r = _client(_mk_ctx()).get("/api/f10_brief/NOTACODE/quick")
    assert r.status_code == 400


# ---------------------------------------------------------------- 归一 / 工具


def test_bad_code_returns_400_on_all_endpoints():
    ctx = _mk_ctx(quotes=_FakeQuotes())
    c = _client(ctx)
    for p in ("/api/kline/zzz", "/api/intraday/zzz", "/api/intraday_multi/zzz",
              "/api/quote5/zzz", "/api/volume_profile/zzz", "/api/volume_ticks/zzz",
              "/api/f10_brief/zzz"):
        assert c.get(p).status_code == 400, p


def test_kline_bg_does_not_reject_unknown_code(tmp_path):
    """旧端 `/api/kline_bg` 不校验 ts_code（找不到归属就给空 items + reason），照搬。"""
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), amv_rows=[], state_rows=[])
    body = _client(ctx).get("/api/kline_bg/zzz?mode=stock").json()
    assert body["code"] == "zzz" and body["items"] == {}
    body = _client(ctx).get("/api/kline_bg/000000.SH?mode=l2").json()
    assert body["code"] is None and body["reason"] == "该股无此层级板块归属"


def test_code_normalization_fills_suffix():
    ctx = _mk_ctx(quotes=_FakeQuotes(rec=None, aux={}))
    body = _client(ctx).get("/api/quote5/600519").json()
    assert body["code"] == "600519.SH"
    body = _client(ctx).get("/api/quote5/000001").json()
    assert body["code"] == "000001.SZ"


def test_slots_241_without_1300():
    slots = backend._slots()
    assert len(slots) == 241
    assert slots[0] == "09:30" and slots[-1] == "15:00"
    assert "11:30" in slots and "13:01" in slots
    assert "13:00" not in slots and "12:00" not in slots


def test_amv_series_ma10_and_white(tmp_path):
    """AMV 副图三线：MA10 需满 10 个有效值，白线口径同 indicators。"""
    bars = [{"t": "202610%02d" % (1 + i), "c": 10.0 + i, "o": 10.0, "h": 11.0,
             "l": 9.0, "v": 100.0, "amount": 1e6} for i in range(12)]
    ctx = _mk_ctx(tmp_path, quotes=_FakeQuotes(), state_rows=[],
                  amv_rows=[("2026-10-%02d" % (1 + i), "600519", 1e10 + i * 1e8)
                            for i in range(12)])
    amv = backend._amv_map(ctx, "600519.SH", [b["t"] for b in bars])
    assert len(amv) == 12
    series = backend._amv_series_out(ctx, bars, amv)
    assert series[8][1] is None        # 不足 10 个有效值 → MA10 为 None
    assert series[9][1] is not None
    assert series[9][0] == round((1e10 + 9e8) / 1e8, 2)   # 元 → 亿元
    assert series[9][2] is not None    # 白线


def test_amv_map_index_code_no_collision(tmp_path):
    """宽基指数撞码（2026-11-01 主人截图：上证50 副图显示康佳A 的活跃市值）。

    000016.SH 全码先查 sector_daily（INDEX 层），不得跌进 stock_amv 撞康佳A 000016.SZ。
    """
    bars = [{"t": "202610%02d" % (1 + i), "c": 2700.0, "o": 2700.0,
             "h": 2710.0, "l": 2690.0, "v": 1e6, "amount": 1e9} for i in range(5)]
    ctx = _mk_ctx(
        tmp_path, quotes=_FakeQuotes(), state_rows=[],
        amv_rows=[("2026-10-0%d" % (1 + i), "000016", 5e8 + i) for i in range(5)],  # 康佳A
        sector_rows=[("2026-10-0%d" % (1 + i), "INDEX", "000016.SH", 1.38e12 + i)
                     for i in range(5)],                                               # 上证50
    )
    m = backend._amv_map(ctx, "000016.SH", [b["t"] for b in bars])
    assert sorted(m.values())[0] == 1.38e12, "指数全码必须命中 sector_daily，且不被个股值污染"
    # 个股不受影响：000016.SZ 仍查 stock_amv
    ms = backend._amv_map(ctx, "000016.SZ", [b["t"] for b in bars])
    assert sorted(ms.values())[0] == 5e8

def _dbars_span(n: int) -> List[_Bar]:
    """跨月的连续日线（_dbars 只能造 10 月内 28 根，不够黄线预热用）。"""
    from datetime import date, timedelta
    d0 = date(2025, 9, 1)
    out = []
    for i in range(n):
        d = d0 + timedelta(days=i)
        c = 10.0 + i * 0.1
        out.append(_Bar(d.strftime("%Y%m%d") + "1500", c - 0.2, c + 0.3, c - 0.4, c, lots=100.0, amount=1e6))
    return out


def test_kline_yellow_has_warmup(tmp_path):
    """黄线 = (MA14+MA28+MA57+MA114)/4 ⇒ 前 113 根恒 None（口径库降级，不编代理值）。

    2026-11-01（主人：所有板块走势的黄线只显最后一小段）：只拉 limit 根时，尾部只剩 7 根
    有黄线（实测 /api/kline/000016.SH?limit=120 → yellow 非空 7/120）。
    修法：多拉 113 根只用于预热指标，输出仍回 limit 根 —— 返回窗口首根就该有黄线。
    """
    quotes = _FakeQuotes(daily=_dbars_span(260))
    ctx = _mk_ctx(tmp_path, quotes=quotes)
    body = _client(ctx).get("/api/kline/600519.SH?period=d&limit=120").json()
    assert len(body["bars"]) == 120, len(body["bars"])
    assert all(b["yellow"] is not None for b in body["bars"]), "黄线预热后窗口内每根都应有值"
    assert all(b["white"] is not None for b in body["bars"])
    # 是「多取预热」而不是「把 limit 改大」：最后一次取数要的根数 ≥ limit + 113
    calls = [c for c in quotes._calls if c[0] == "bars.get"]
    assert calls, "未取数"
    assert calls[-1][2].get("count") >= 120 + 113, calls[-1][2]

def test_kline_all_marker_maps_to_880008(tmp_path):
    """`ALL`（板块目录的沪深全A 标记）→ 880008.SH。

    2026-11-01：指数层右栏右键「打开 K 线」传的是 ALL，不映射就是 HTTP 400
    （实测弹窗报「加载失败：HTTP 400」）。全站全A = 通达信 880008（等权），同 sector 口径。
    """
    quotes = _FakeQuotes(daily=_dbars(5))
    ctx = _mk_ctx(tmp_path, quotes=quotes)
    body = _client(ctx).get("/api/kline/ALL?period=d&limit=120").json()
    assert body["code"] == "880008.SH", body["code"]
    assert len(body["bars"]) == 5


def test_intraday_disk_cache_guards() -> None:
    """落盘守门（主人 2026-11-07）：回退到上一交易日 / 空数据的结果不得写缓存。

    分时取不到当日数据时 `build_intraday` 会回退到最近交易日（`date_fallback`）；
    这种结果如果按“今天”落盘，就是把昨天当今天。这条用例盯死落盘条件三件套：
    当日（fallback=False）、points 非空、resolved == today8。
    """
    src = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    assert "points and not fallback and resolved == today8" in src
    assert "_INTRADAY_DISK_VER" in src
