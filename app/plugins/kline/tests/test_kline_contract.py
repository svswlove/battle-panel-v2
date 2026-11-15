# -*- coding: utf-8 -*-
"""kline 插件契约测试：manifest / register / qfq 计算。"""
import json
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.kline import backend  # noqa: E402


def _manifest():
    with open(Path(__file__).resolve().parents[1] / "plugin.json", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_valid():
    m = validate_manifest(_manifest(), Path(__file__).resolve().parents[1])
    assert m.id == "kline"
    assert m.data_write == ()
    # v0.6.0：K 线改走 eltdx（与旧端同源），不再依赖 track_pool.db
    assert sorted(m.data_read) == ["amv_board.db"]
    assert m.lib_read == ("amv_regime", "indicators")  # v0.5.0：黄白线口径库
    # v0.4.0：分时/五档/筹码走 ctx.quotes（core 行情门面）
    assert m.quotes_read is True
    assert m.frontend.cards and m.frontend.cards[0].id == "kline.main"


def test_register_returns_router():
    class Ctx:
        def db_read(self, name):
            raise AssertionError("no db access in register")
    router = backend.register(Ctx())
    assert isinstance(router, APIRouter)
    assert {"/bars/{ts_code}", "/quote/{ts_code}", "/bg/{ts_code}",
            "/intraday/{ts_code}", "/quote5/{ts_code}",
            "/volume_profile/{ts_code}"} <= {r.path for r in router.routes}


def _make_db(path: Path) -> None:
    import sqlite3
    con = sqlite3.connect(str(path))
    con.executescript("""
    CREATE TABLE kline(ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,
                       PRIMARY KEY(ts_code,trade_date));
    CREATE TABLE factor(ts_code TEXT, trade_date TEXT, qfq_scale REAL, qfq_offset REAL,
                        PRIMARY KEY(ts_code,trade_date));
    """)
    con.execute("INSERT INTO kline VALUES('000001.SZ','20260101',10,11,9,10,1000,10000)")
    con.execute("INSERT INTO factor VALUES('000001.SZ','20260101',0.5,1.0)")
    con.commit(); con.close()


def _bars_ctx(bars=None, daily=None, amv_rows=None, tmp_path=None):
    """造一个带行情源 + 可选 stock_amv 的 Ctx（不碰 track_pool）。"""
    fake = _FakeQuotes(bars=bars or [], daily=daily or [])
    db = None
    if amv_rows is not None:
        import sqlite3
        db = tmp_path / "amv_board.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE stock_amv(date TEXT, stock TEXT, amv REAL, PRIMARY KEY(date,stock))")
        con.executemany("INSERT OR REPLACE INTO stock_amv VALUES(?,?,?)", amv_rows)
        con.commit(); con.close()

    class Ctx:
        quotes = fake

        def db_read(self, name):
            import sqlite3
            con = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True)
            con.row_factory = sqlite3.Row
            return con

        def lib(self, name):
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

    app = FastAPI()
    app.include_router(backend.register(Ctx()), prefix="/api/p/kline")
    return TestClient(app)


def _dbar(day, close, open_=None, high=None, low=None, lots=1000.0, amount=1e6):
    from datetime import datetime as _dt
    class B:
        pass
    b = B()
    b.time = _dt.strptime(day + "1500", "%Y%m%d%H%M")
    b.close = close
    b.open = open_ if open_ is not None else close
    b.high = high if high is not None else close
    b.low = low if low is not None else close
    b.volume_lots = lots
    b.amount = amount
    return b


def test_bars_pass_through_from_quotes():
    """v0.6.0：K 线走 eltdx，插件只做单位换算与字段拼装（复权由行情源做）。"""
    daily = [_dbar("20260101", 10.0), _dbar("20260102", 11.0), _dbar("20260105", 12.0)]
    c = _bars_ctx(bars=[], daily=daily)
    body = c.get("/api/p/kline/bars/000001.SZ?limit=3&adjust=qfq").json()
    assert set(body) == OLD_TOP_KEYS | {"intraday", "last_bar_date", "notice", "source"}, set(body)
    assert body["source"] == "eltdx"
    assert [b["date"] for b in body["bars"]] == ["20260101", "20260102", "20260105"]
    assert body["bars"][-1]["close"] == 12.0
    # 成交量：eltdx 给「手」，对外报「股」
    assert body["bars"][-1]["volume"] == 100000.0
    for bar in body["bars"]:
        assert set(bar) == OLD_BAR_KEYS | {"yellow", "white"}, set(bar)


def test_bars_503_when_source_unavailable():
    """行情源不可用 → 503（不静默返回空，也不回落旧库）。"""
    c = _bars_ctx(bars=[], daily=[])
    r = c.get("/api/p/kline/bars/000001.SZ?limit=3&adjust=qfq")
    assert r.status_code == 503


OLD_TOP_KEYS = {"code", "period", "adjust", "stale", "warnings", "bars"}
OLD_BAR_KEYS = {"date", "open", "high", "low", "close", "volume", "amount"}


def _make_db_multi(path: Path) -> None:
    import sqlite3
    con = sqlite3.connect(str(path))
    con.executescript("""
    CREATE TABLE kline(ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,
                       PRIMARY KEY(ts_code,trade_date));
    CREATE TABLE factor(ts_code TEXT, trade_date TEXT, qfq_scale REAL, qfq_offset REAL,
                        PRIMARY KEY(ts_code,trade_date));
    """)
    for d, px in (("20260101", 10.0), ("20260102", 11.0), ("20260105", 12.0)):
        con.execute("INSERT INTO kline VALUES('000001.SZ',?,?,?,?,?,1000,10000)", (d, px, px + 1, px - 1, px))
        con.execute("INSERT INTO factor VALUES('000001.SZ',?,1.0,0.0)", (d,))
    con.commit(); con.close()


def test_bars_boundary_fields_added():
    """v0.1 边界字段：intraday/last_bar_date/notice 必须存在且语义正确。"""
    daily = [_dbar("20260101", 10.0), _dbar("20260102", 11.0), _dbar("20260105", 12.0)]
    c = _bars_ctx(bars=[], daily=daily)
    body = c.get("/api/p/kline/bars/000001.SZ?limit=3&adjust=qfq").json()
    assert body["intraday"] is False
    assert body["last_bar_date"] == "20260105"
    assert body["last_bar_date"] == body["bars"][-1]["date"]
    assert "当日" in body["notice"]
    assert "AMV" in body["notice"]


def test_quote_shape_unchanged():
    daily = [_dbar("20260101", 10.0), _dbar("20260105", 12.0)]
    c = _bars_ctx(bars=[], daily=daily)
    body = c.get("/api/p/kline/quote/000001.SZ?adjust=qfq").json()
    assert set(body) == {"code", "adjust", "bar", "warnings"}, set(body)
    assert body["bar"]["date"] == "20260105"


# ---------------------------------------------------------------- v0.4.0 实时三项


class _FakeQuotes:
    """假行情门面：形状与 app/core/quotes.py 一致。"""

    def __init__(self, *, bars=None, ticks=None, rec=None, aux=None, ok=True,
                 days=None, daily=None):
        self._bars = bars or []
        self._ticks = ticks or []
        self._rec = rec
        self._aux = aux or {}
        self._ok = ok
        self._days = days or {}
        self._daily = daily or []

    def normalize(self, code):
        from app.core.quotes import normalize
        return normalize(code)

    def minute_bars(self, glb, count=241):
        return self._bars if self._ok else []

    def today_ticks(self, glb, pages=6, page_size=1800):
        return self._ticks if self._ok else []

    def depth(self, glb):
        return self._rec if self._ok else None

    def stock_aux(self, glb):
        return self._aux

    def daily_bars(self, glb, count=8, adjust=None):
        # 与真实门面一致：eltdx 的 count = **最近** N 根（升序返回）
        return self._daily[-count:] if count else self._daily

    def minute_bars_days(self, glb, days=5):
        return self._days

    def call(self, *a, **k):
        return None


class _T:
    def __init__(self, hhmm, close=10.0, lots=100.0, amount=1e6, open_=None):
        from datetime import datetime as _dt
        self.time = _dt.strptime("20261023" + hhmm, "%Y%m%d%H:%M")
        self.close = close
        self.open = open_ if open_ is not None else close
        self.high = close
        self.low = close
        self.volume_lots = lots
        self.amount = amount


def _D(day, close):
    from datetime import datetime as _dt
    class B:
        pass
    b = B()
    b.time = _dt.strptime(day + "1500", "%Y%m%d%H%M")
    b.close = close
    b.volume_lots = 100.0
    return b


def _client(fake):
    class Ctx:
        quotes = fake
        def db_read(self, name):
            raise AssertionError("no db")
    app = FastAPI()
    app.include_router(backend.register(Ctx()))
    return TestClient(app)


def test_intraday_slots_241_and_13_00_absent():
    slots = backend._intraday_slots()
    assert len(slots) == 241
    assert slots[0] == "09:30" and slots[-1] == "15:00"
    assert "11:30" in slots and "13:01" in slots
    assert "13:00" not in slots, "13:00 无根（旧端实测校准）"
    assert "12:00" not in slots, "午休不应出现在轴上"


def test_intraday_shape_and_avg_line():
    fake = _FakeQuotes(bars=[_T("09:31", close=10.0, lots=100.0, amount=1e6),
                             _T("09:32", close=11.0, lots=100.0, amount=1e6)])
    r = _client(fake).get("/intraday/600519.SH")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["vol_unit"] == "股"
    assert d["session"]["slot_count"] == 241
    pts = d["points"]
    assert pts[0]["t"] == "09:30", "首根必须是 09:30"
    assert pts[0]["volume"] == 0.0, "09:30 首根成交计 0（旧端同口径）"
    # 第二根：均价 = 累计额 / 累计量（volume_lots × 100 = 股）
    assert pts[1]["t"] == "09:31"
    assert pts[1]["volume"] == 10000.0
    assert abs(pts[1]["avg"] - (1e6 / 10000.0)) < 1e-6
    assert pts[1]["cum_volume"] == 10000.0


def test_intraday_degrades_without_source():
    r = _client(_FakeQuotes(ok=False)).get("/intraday/600519.SH")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["reason"]
    assert d["points"] == []


def test_intraday_rejects_bad_code():
    r = _client(_FakeQuotes()).get("/intraday/abcdef")
    assert r.status_code == 400


def test_quote5_shape():
    class Lv:
        def __init__(self, p, v):
            self.price = p
            self.volume = v

    class Rec:
        last_price = 10.5
        last_close_price = 10.0
        high_price = 10.8
        low_price = 9.9
        update_time_raw = 113002
        buy_levels = [Lv(10.49, 3), Lv(10.48, 5)]
        sell_levels = [Lv(10.51, 2), Lv(10.52, 7)]
        total_hand = 1000
        current_hand = 4
        outer_disc = 600
        inside_dish = 400

    fake = _FakeQuotes(rec=Rec(), aux={"avg5": 2000.0, "circ": 1e8,
                                       "limit_up": 11.0, "limit_down": 9.0})
    d = _client(fake).get("/quote5/600519.SH").json()
    assert d["ok"] is True
    assert d["last"] == 10.5 and d["pre_close"] == 10.0
    assert d["change"] == 0.5 and d["change_pct"] == 5.0
    assert d["quote_time"] == "11:30:02"
    assert [x["price"] for x in d["bids"]] == [10.49, 10.48]
    assert [x["price"] for x in d["asks"]] == [10.51, 10.52]
    assert d["stats"]["total_vol"] == 1000
    assert d["stats"]["turnover"] == 0.1          # 1000手*100 / 1e8 * 100%
    assert d["stats"]["vol_ratio"] is not None    # 有 avg5 就该算出来
    assert d["limit_up"] == 11.0


def test_quote5_degrades_without_depth():
    d = _client(_FakeQuotes(ok=False)).get("/quote5/600519.SH").json()
    assert d["ok"] is False
    assert d["last"] is None and d["asks"] == [] and d["bids"] == []


def test_elapsed_minutes_matches_old_endpoint():
    """量比口径：午休不计，收盘封顶 240。"""
    assert backend._elapsed_minutes("09:30") == 1
    assert backend._elapsed_minutes("11:30") == 120
    assert backend._elapsed_minutes("13:00") == 120
    assert backend._elapsed_minutes("13:30") == 150
    assert backend._elapsed_minutes("15:00") == 240
    assert backend._elapsed_minutes("09:00") == 0


def test_volume_profile_buckets_and_until():
    class Tk:
        def __init__(self, hhmm, price, vol, kind="trade"):
            self.time_label = hhmm
            self.price = price
            self.volume = vol
            self.event_kind = kind

    ticks = [Tk("09:25", 10.0, 500, kind="auction"),   # 竞价：剔除
             Tk("09:31", 10.0, 100),
             Tk("09:32", 10.0, 50),
             Tk("09:33", 10.5, 30)]
    fake = _FakeQuotes(ticks=ticks)
    d = _client(fake).get("/volume_profile/600519.SH").json()
    assert d["ok"] is True
    buckets = {b["p"]: b["v"] for b in d["buckets"]}
    assert buckets == {10.0: 150, 10.5: 30}, "竞价快照必须剔除，同价合并"
    assert d["total_v"] == 180
    assert d["max_v"] == 150
    # until 只算到 09:32
    d2 = _client(_FakeQuotes(ticks=ticks)).get("/volume_profile/600519.SH?until=09:32").json()
    assert {b["p"]: b["v"] for b in d2["buckets"]} == {10.0: 150}


def test_volume_profile_degrades():
    d = _client(_FakeQuotes(ok=False)).get("/volume_profile/600519.SH").json()
    assert d["ok"] is False and d["buckets"] == [] and d["total_v"] == 0


def test_plugin_does_not_touch_eltdx_directly():
    """CONTRACT §4.1：插件不得自己连行情源，一律走 ctx.quotes。"""
    from app.core.contract import find_forbidden_quote_usage
    src = (Path(__file__).resolve().parents[1] / "backend.py").read_text(encoding="utf-8")
    hits = find_forbidden_quote_usage(src)
    assert not hits, f"插件直接引了行情源：{hits}"

def test_intraday_multi_shape():
    """多日分时：每日独立 prev_close + points，时间轴与单日一致。"""
    fake = _FakeQuotes(days={
        "20261022": [_T("09:31", close=10.0, lots=100.0, amount=1e6),
                     _T("09:32", close=11.0, lots=100.0, amount=1e6)],
        "20261023": [_T("09:31", close=12.0, lots=50.0, amount=6e5)],
    }, daily=[
        _D("20261021", 9.0), _D("20261022", 10.5), _D("20261023", 12.5),
    ])
    d = _client(fake).get("/intraday_multi/600519.SH?days=5").json()
    assert d["ok"] is True
    assert [x["date"] for x in d["days"]] == ["20261022", "20261023"]
    assert d["days"][0]["prev_close"] == 9.0    # 10-22 的昨收 = 10-21 收盘
    assert d["days"][1]["prev_close"] == 10.5   # 10-23 的昨收 = 10-22 收盘
    # 每天首根补 09:30（开票价、成交 0）
    assert d["days"][0]["points"][0]["t"] == "09:30"
    assert d["days"][0]["points"][0]["volume"] == 0.0
    assert d["count"] == d["days"][0]["count"] + d["days"][1]["count"]
    assert d["adj"] == "front" and d["vol_unit"] == "股"


def test_intraday_multi_degrades():
    d = _client(_FakeQuotes(ok=False)).get("/intraday_multi/600519.SH").json()
    assert d["ok"] is False and d["days"] == []

def test_intraday_1301_prefers_1301_not_double_count():
    """下午首根：13:01 自身已含 13:00 的量，不能再加一遍。

    2026-10-23 盘中实测（600519.SH）：eltdx 同时给 13:00（17900 手）与 13:01，
    13:01 的 volume 已是 68600（= 17900 + 50700）。早期版本把两根相加 → 86500（翻倍）。
    """
    class B(_T):
        pass

    bars = [
        _T("09:31", close=10.0, lots=100.0, amount=1e6),
        _T("13:00", close=11.0, lots=179.0, amount=2e6),
        _T("13:01", close=11.5, lots=686.0, amount=8e6),
    ]
    d = _client(_FakeQuotes(bars=bars)).get("/intraday/600519.SH").json()
    p1301 = next(p for p in d["points"] if p["t"] == "13:01")
    assert p1301["volume"] == 68600.0, "13:01 必须用它自己的量，不能 +13:00"
    assert p1301["amount"] == 8e6
    # 不能多出 13:00 这个点
    assert all(p["t"] != "13:00" for p in d["points"])


def test_intraday_1301_falls_back_to_1300_when_missing():
    """只有 13:00（刚开盘、13:01 还没生成）时，回落到 13:00。"""
    bars = [
        _T("09:31", close=10.0, lots=100.0, amount=1e6),
        _T("13:00", close=11.0, lots=179.0, amount=2e6),
    ]
    d = _client(_FakeQuotes(bars=bars)).get("/intraday/600519.SH").json()
    p1301 = next(p for p in d["points"] if p["t"] == "13:01")
    assert p1301["volume"] == 17900.0



# ---------------------------------------------------------------- v0.7.0 逐笔压缩


def _tk(hhmm, price, vol, kind="trade", n=1):
    class T:
        pass
    t = T()
    t.time_label = hhmm
    t.price = price
    t.volume = vol
    t.event_kind = kind
    t.order_count = n
    return t


class _Rec:
    """volume_ticks 只用到 last_close_price（昨收）。"""
    last_close_price = 9.9


def test_volume_ticks_rows_shape_and_slots():
    """rows=[[minute_idx, price_milli, vol]]：剔除竞价、同 (分钟,价位) 合并、升序。"""
    ticks = [_tk("09:25", 10.0, 500, kind="auction"),   # 竞价快照：剔除
             _tk("09:30", 10.0, 100),
             _tk("09:31", 10.0, 50),
             _tk("09:31", 10.5, 30),
             _tk("13:00", 11.0, 70),                    # 13:00 归 13:01(=121)
             _tk("15:00", 12.0, 90)]
    fake = _FakeQuotes(ticks=ticks, rec=_Rec(),
                       aux={"limit_up": 11.0, "limit_down": 9.0})
    d = _client(fake).get("/volume_ticks/600519.SH").json()
    assert set(d) == {"code", "pre_close", "limit_up", "limit_down",
                      "rows", "asof", "source"}, set(d)
    assert d["code"] == "600519.SH"
    assert d["pre_close"] == 9.9
    assert d["limit_up"] == 11.0 and d["limit_down"] == 9.0
    assert d["source"] == "eltdx:ticks(0x0fc5)"
    # 09:30 -> idx 0；09:31 -> idx 1；13:01 -> idx 121；15:00 -> idx 240
    assert d["rows"] == [[0, 10000, 100], [1, 10000, 50], [1, 10500, 30],
                         [121, 11000, 70], [240, 12000, 90]], d["rows"]
    # 竞价那条不能计入
    assert sum(r[2] for r in d["rows"]) == 340


def test_volume_ticks_price_milli_and_slot_axis():
    """price_milli = round(price*1000)；minute_idx 必须落在 241 位时间轴上。"""
    slots = backend._intraday_slots()
    fake = _FakeQuotes(ticks=[_tk("10:00", 1.23456, 10)], rec=_Rec())
    d = _client(fake).get("/volume_ticks/600519.SH").json()
    i, pm, v = d["rows"][0]
    assert slots[i] == "10:00"
    assert pm == 1235, "1.23456 应四舍五入到 1235 毫价"
    assert v == 10, "volume 单位=手（eltdx 原值，不 x100）"


def test_volume_ticks_empty_state():
    """无数据（代码合法但无逐笔）→ 200 + rows=[]（旧端实测空态形状，不是 404）。"""
    d = _client(_FakeQuotes(ticks=[])).get("/volume_ticks/123456.SZ").json()
    assert d["rows"] == []
    assert d["pre_close"] is None and d["limit_up"] is None and d["limit_down"] is None
    assert d["code"] == "123456.SZ"
    assert d["source"] == "eltdx:ticks(0x0fc5)"


def test_volume_ticks_rejects_bad_code():
    r = _client(_FakeQuotes()).get("/volume_ticks/abcdef")
    assert r.status_code == 400


def test_volume_ticks_degrades_when_ticks_fail():
    """行情门面取逐笔抛错 → 空 rows，不让异常穿透成 500。"""
    class Boom(_FakeQuotes):
        def today_ticks(self, glb, pages=6, page_size=1800):
            raise RuntimeError("eltdx down")
    fake = Boom()
    fake._ok = True
    d = _client(fake).get("/volume_ticks/600519.SH").json()
    assert d["rows"] == []
