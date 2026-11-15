# -*- coding: utf-8 -*-
"""watchlist 插件契约测试。"""
import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.watchlist import backend  # noqa: E402

DATA = {
    "meta": {"updated_at": "2026-10-22 00:47:07"},
    "groups": [
        {"id": "stock_default", "name": "自选", "kind": "stock", "order": 0},
        {"id": "sector_default", "name": "板块", "kind": "sector", "order": 100},
    ],
    "items": [
        {"kind": "stock", "code": "000636.SZ", "name": "风华高科", "group": "stock_default", "order": 1},
        {"kind": "stock", "code": "603629.SH", "name": "利通电子", "group": "stock_default", "order": 0},
        {"kind": "sector", "code": "881101.TI", "name": "种植业", "group": "sector_default", "order": 0},
    ],
}


def _manifest():
    with open(Path(__file__).resolve().parents[1] / "plugin.json", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_valid():
    m = validate_manifest(_manifest(), Path(__file__).resolve().parents[1])
    assert m.id == "watchlist"
    assert m.panel_read == ("watchlist", "watchlist_quotes", "focus_levels")
    assert m.data_read == ()


QUOTES = {
    "asof": "2026-10-22 15:29:17",
    "meta": {"document_type": "watchlist_quotes", "source": "tick_feed:full-tick"},
    "quotes": {
        "603629.SH": {"price": 85.08, "pre_close": 92.03, "change_pct": -7.5519,
                     "quote_time": "2026-10-22 15:29:01"},
    },
}

LEVELS = {
    "meta": {"document_type": "focus_levels", "trade_date": "2026-10-22"},
    "quote_asof": "2026-10-22 15:29:17",
    "items": [
        {"code": "603629.SH", "name": "利通电子", "origin": ["watchlist"],
         "price": 85.08, "price_source": "watchlist_quotes", "bar_asof": "20261022",
         "key_levels": [
             {"level_type": "YELLOW", "price": 103.881, "status": "BROKEN", "effective": False},
             {"level_type": "EK_OPEN", "price": 85.83, "status": "NEAR", "effective": True},
         ]},
    ],
}


class FakeCtx:
    def __init__(self, panels=None):
        self.panels = panels if panels is not None else {
            "watchlist": DATA, "watchlist_quotes": QUOTES, "focus_levels": LEVELS,
        }

    def panel_json(self, name):
        if name not in self.panels:
            raise FileNotFoundError(name)
        return self.panels[name]


def _client():
    app = FastAPI()
    app.include_router(backend.register(FakeCtx()), prefix="/api/p/watchlist")
    return TestClient(app)


def test_summary_counts():
    body = _client().get("/api/p/watchlist/summary").json()
    assert body["counts"]["items"] == 3
    assert body["counts"]["by_kind"] == {"stock": 2, "sector": 1}
    assert body["counts"]["groups"] == 2
    assert body["read_only"] is True


def test_items_order_and_filter():
    """排序：股票在前、板块在后，各自按 order 升序（与旧 8800 实测一致）。"""
    client = _client()
    body = client.get("/api/p/watchlist/items").json()
    assert [x["code"] for x in body["items"]] == ["603629.SH", "000636.SZ", "881101.TI"]
    stocks = client.get("/api/p/watchlist/items?kind=stock").json()
    assert [x["code"] for x in stocks["items"]] == ["603629.SH", "000636.SZ"]
    one = client.get("/api/p/watchlist/items?limit=1").json()
    assert len(one["items"]) == 1


def test_groups():
    body = _client().get("/api/p/watchlist/groups").json()
    assert len(body["groups"]) == 2


def test_missing_panel_503():
    class EmptyCtx:
        def panel_json(self, name):
            raise FileNotFoundError(name)
    app = FastAPI()
    app.include_router(backend.register(EmptyCtx()), prefix="/api/p/watchlist")
    assert TestClient(app).get("/api/p/watchlist/summary").status_code == 503


def test_quotes_passthrough():
    body = _client().get("/api/p/watchlist/quotes").json()
    assert body["asof"] == "2026-10-22 15:29:17"
    assert body["n"] == 1
    assert body["quotes"]["603629.SH"]["change_pct"] == -7.5519


def test_items_with_quotes_joins_by_code():
    body = _client().get("/api/p/watchlist/items?quotes=1").json()
    by_code = {x["code"]: x for x in body["items"]}
    assert by_code["603629.SH"]["quote"]["price"] == 85.08
    # 无行情的条目（含板块）→ quote=None，不报错
    assert by_code["000636.SZ"]["quote"] is None
    assert by_code["881101.TI"]["quote"] is None
    assert body["quote_asof"] == "2026-10-22 15:29:17"


def test_items_with_quotes_survives_missing_quotes_file():
    ctx = FakeCtx({"watchlist": DATA})
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/watchlist")
    body = TestClient(app).get("/api/p/watchlist/items?quotes=1").json()
    assert body["items"] and all(x["quote"] is None for x in body["items"])
    assert body["quote_asof"] is None


def test_levels_filters_broken_by_default():
    body = _client().get("/api/p/watchlist/levels").json()
    assert body["n"] == 1
    levels = body["items"][0]["key_levels"]
    assert [lv["level_type"] for lv in levels] == ["EK_OPEN"], "默认只返 effective=true"


def test_levels_can_include_broken():
    body = _client().get("/api/p/watchlist/levels?effective_only=0").json()
    assert len(body["items"][0]["key_levels"]) == 2


def test_levels_filter_by_code():
    client = _client()
    hit = client.get("/api/p/watchlist/levels?code=603629.SH").json()
    assert hit["n"] == 1
    miss = client.get("/api/p/watchlist/levels?code=NOPE").json()
    assert miss["n"] == 0 and miss["items"] == []
