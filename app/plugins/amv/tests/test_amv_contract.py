# -*- coding: utf-8 -*-
"""amv 插件契约测试：manifest / register / summary 组合。"""
import json
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.amv import backend  # noqa: E402

LIVE = {
    "trade_date": "2026-10-22",
    "asof": "2026-10-22 14:05:41",
    "phase": "AFTERNOON",
    "all": {"amv": 13574255839122.31, "amv_pct": -1.6126},
    "coverage": {"pct": 0.9973, "ok": True},
    "forecast": {"pred": -2.3573, "p10": -2.8699, "p90": -1.8639},
    "breadth": {"advance": 941, "decline": 4196, "flat": 89},
    "indices": [{"code": "000001.SH", "name": "上证指数", "amv": 6504615538462.05, "amv_pct": -1.6133}],
    "sectors": {"leaders": [], "laggards": []},
}
REGIME = {
    "date": "2026-10-22",
    "state": "空",
    "resonance": False,
    "ma10": 143013.53,
    "pct": -0.58,
    "amv": 137967.48,
    "last_switch": {"date": "2026-09-24", "dir": "空"},
    "generated_at": "2026-10-22 11:00:29",
}
CAP = {
    "trade_date": "2026-10-22",
    "regime": "空",
    "base_pct": 30,
    "final_pct": 30,
    "cap_amount": 142308.42,
    "total_asset": 474361.4,
    "degraded": None,
    "warning": None,
}


def _manifest():
    with open(Path(__file__).resolve().parents[1] / "plugin.json", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_valid():
    m = validate_manifest(_manifest(), Path(__file__).resolve().parents[1])
    assert m.id == "amv"
    assert m.panel_read == ("amv_live", "amv_regime_latest", "position_cap_latest", "amv_breadth_latest", "all_a_latest")
    assert m.data_read == ("amv_board.db",)
    assert m.frontend.cards[0].id == "amv.main"
    assert [c.id for c in m.frontend.cards] == ["amv.main", "amv.hcl"]

class FakeCtx:
    def __init__(self, panels=None, db_path=None, jsons=None):
        self.db_path = db_path
        self.panels = panels or {
            "amv_live": LIVE,
            "amv_regime_latest": REGIME,
            "position_cap_latest": CAP,
        }
        self.jsons = jsons or {}

    def panel_json(self, name):
        if name not in self.panels:
            raise FileNotFoundError(name)
        return self.panels[name]

    def json_read(self, name):
        if name not in self.jsons:
            raise FileNotFoundError(name)
        return self.jsons[name]

    def db_read(self, name):
        if name != "amv_board.db" or not self.db_path:
            raise FileNotFoundError(name)
        conn = sqlite3.connect("file:%s?mode=ro" % str(self.db_path).replace("\\", "/"), uri=True)
        conn.row_factory = sqlite3.Row
        return conn


def _client(ctx):
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/amv")
    return TestClient(app)


def test_register_returns_router():
    router = backend.register(FakeCtx())
    assert {"/summary", "/live", "/regime", "/position_cap", "/indices", "/sectors", "/daily", "/history", "/hcl", "/breadth", "/all_a"} <= {
        r.path for r in router.routes
    }


HCL = {
    "date": "20261022",
    "hcl_raw": 52.47,
    "hcl_smooth": 50.79,
    "pct_5y": 60.3,
    "chg_1d": 2.35,
    "chg_5d": 1.13,
    "peak_all": {"date": "20150602", "value": 161.39},
    "history_len": 3743,
    "series": [["20261021", 50.12], ["20261022", 52.47]],
    "series_smooth": [["20261022", 50.79]],
    "series_all": [["20261021", 50.12], ["20261022", 52.47]],
    "series_all_smooth": [["20261022", 50.79]],
}


def test_hcl_passthrough_adds_ok():
    """`/hcl` 只读 hcl_calc.py 落盘结果，除 `ok=true` 外逐字段不变。"""
    body = _client(FakeCtx(jsons={"hcl_latest": HCL})).get("/api/p/amv/hcl").json()
    assert body["ok"] is True
    for key, value in HCL.items():
        assert body[key] == value, key


def test_hcl_missing_file_returns_503():
    r = _client(FakeCtx()).get("/api/p/amv/hcl")
    assert r.status_code == 503
    assert "hcl_latest" in r.json()["detail"]


BREADTH = {
    "date": "2026-10-22",
    "generated_at": "2026-10-22 16:25:45",
    "ma_n": 10,
    "market": {"date": "2026-10-22", "multi": 1115, "valid": 5196, "ratio": 0.214588},
    "sector_level": {"L2": {"multi": 12, "valid": 90, "ratio": 0.1333, "n_boards": 90}},
}


def test_breadth_passthrough():
    """`/breadth` 只读 amv_breadth_latest.json，逐字段不变。"""
    ctx = FakeCtx({
        "amv_live": LIVE, "amv_regime_latest": REGIME,
        "position_cap_latest": CAP, "amv_breadth_latest": BREADTH,
    })
    body = _client(ctx).get("/api/p/amv/breadth").json()
    assert body == BREADTH


def test_breadth_missing_returns_503():
    r = _client(FakeCtx()).get("/api/p/amv/breadth")
    assert r.status_code == 503
    assert "amv_breadth_latest" in r.json()["detail"]


ALL_A = {
    "code": "880008", "name": "全A", "date": "20261022", "close": 1323.78,
    "prev_close": 1360.21, "pct": -2.68, "high": 1362.17, "low": 1323.78,
    "up_count": 535, "down_count": 4999, "spark": [["20261020", 1350.0]],
    "ma5": 1348.94, "ma10": 1370.89, "dist5_pct": -1.87,
}


def test_all_a_passthrough():
    ctx = FakeCtx({
        "amv_live": LIVE, "amv_regime_latest": REGIME,
        "position_cap_latest": CAP, "all_a_latest": ALL_A,
    })
    body = _client(ctx).get("/api/p/amv/all_a").json()
    assert body == ALL_A


def test_manifest_declares_hcl_json():
    m = validate_manifest(_manifest(), Path(__file__).resolve().parents[1])
    assert m.json_read == ("hcl_latest",)


def test_summary_combines_panels():
    r = _client(FakeCtx()).get("/api/p/amv/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trade_date"] == "2026-10-22"
    assert body["amv"] == LIVE["all"]["amv"]
    assert body["amv_pct"] == -1.6126
    assert body["regime"]["state"] == "空"
    assert body["regime"]["ma10"] == 143013.53
    assert body["position_cap"]["final_pct"] == 30
    assert body["consistency"]["stale"] == {"live_vs_regime": False, "cap_vs_live": False}


def test_summary_flags_stale_dates():
    ctx = FakeCtx({
        "amv_live": dict(LIVE, trade_date="2026-10-22"),
        "amv_regime_latest": dict(REGIME, date="2026-10-21"),
        "position_cap_latest": dict(CAP, trade_date="2026-10-20"),
    })
    body = _client(ctx).get("/api/p/amv/summary").json()
    assert body["consistency"]["stale"] == {"live_vs_regime": True, "cap_vs_live": True}


def test_missing_panel_returns_503():
    ctx = FakeCtx({"amv_live": LIVE, "amv_regime_latest": REGIME})
    r = _client(ctx).get("/api/p/amv/summary")
    assert r.status_code == 503
    assert "position_cap_latest" in r.json()["detail"]


def _daily_db(tmp_path: Path) -> Path:
    path = tmp_path / "amv_board.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE daily_total(date TEXT PRIMARY KEY, total REAL)")
    for i in range(12):
        conn.execute("INSERT INTO daily_total VALUES(?,?)", ("2026-10-%02d" % (i + 1), 100e8 + i * 1e8))
    conn.commit(); conn.close()
    return path


def test_daily_ma10_and_clamp(tmp_path: Path):
    ctx = FakeCtx(db_path=_daily_db(tmp_path))
    body = _client(ctx).get("/api/p/amv/daily?days=5").json()
    assert body["days"] == 5
    assert len(body["dates"]) == 5
    # 窗口内第 1 根 MA10 只按自身，最后 1 根按 5 根均值
    assert body["ma10"][0] == 107e8
    assert body["ma10"][-1] == 109e8
    assert body["total_yi"][0] == 107.0


def test_history_all_and_reject_other(tmp_path: Path):
    ctx = FakeCtx(db_path=_daily_db(tmp_path))
    client = _client(ctx)
    assert client.get("/api/p/amv/history?code=ALL&days=3").status_code == 200
    assert client.get("/api/p/amv/history?code=881101.TI").status_code == 400
