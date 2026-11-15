# -*- coding: utf-8 -*-
"""compat_sector 插件契约测试。

覆盖 docs/CONTRACT.md §9 验收 1~4 + 本项目对兼容插件的额外约束：
  1. manifest 必填字段齐全、id 与目录名一致、`backend.mount == "root"`（旧前端要根路径）；
  2. `register(ctx)` 返回 APIRouter，9 条路径都不带 `/api` 前缀，且不带 `/api/p/<id>`；
  3. 插件代码无直连数据库、无自连行情源、无 HTTP、无子进程、`data.write` 为空；
  4. 挂到真实 core 后落在 `/api/<名>`（不是 `/api/p/compat_sector/<名>`）。

数据库全部用 tmp_path 下的临时 sqlite，不碰生产库、不写 data/panel。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
MANIFEST = PLUGIN_DIR / "plugin.json"
PLUGIN_ID = "compat_sector"

# pytest 无 __init__.py 时只把 tests/ 目录塞进 sys.path，这里补项目根
# （与 app/tests/test_contract.py、sector 的测试同写法）
PROJECT_ROOT = PLUGIN_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 旧前端请求的 9 条根路径（去掉 /api 前缀后的 router 内路径）
EXPECTED_PATHS = {
    "/sector_card.json",
    "/sector_intraday.json",
    "/sector_spark.json",
    "/sector_track.json",
    "/sector_track_backfill_status",
    "/sector/health",
    "/etf_sector_map.json",
    "/breadth_880.json",
    "/temperature_latest.json",
}


# ---------------------------------------------------------------- 模块加载

@pytest.fixture(scope="session")
def backend():
    spec = importlib.util.spec_from_file_location("compat_sector_backend_under_test", BACKEND_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    """伪造 PluginContext：db_read 返回只读连接；lib 加载真实口径库；panel_json / json_read 走注入字典。

    `cache_read` / `cache_write`（2026-11-07 盘后落盘）用**内存字典**实现，形状照 core
    `PluginContext`：读不到抛 FileNotFoundError，读回副本，写只接受 dict。
    """

    def __init__(self, dbs: Dict[str, Path] | None = None,
                 panels: Dict[str, Any] | None = None,
                 quotes: Any = None,
                 jsons: Dict[str, Any] | None = None,
                 clock: Any = None):
        self.dbs = dbs or {}
        self.panels = panels or {}
        self.jsons = jsons or {}
        self._quotes = quotes
        self._libs: Dict[str, Any] = {}
        self.clock = clock
        self.cache_docs: Dict[str, Any] = {}
        self.cache_reads: list = []
        self.cache_writes: list = []

    @property
    def trade_clock(self) -> Any:
        """未注入时钟 → 当作“非盘后”（与 core 门面缺失时的降级同向）。"""
        if self.clock is None:
            raise AttributeError("trade_clock 未注入")
        return self.clock

    def cache_read(self, name: str) -> Any:
        self.cache_reads.append(name)
        if name not in self.cache_docs:
            raise FileNotFoundError("cache not written: %s" % name)
        return json.loads(json.dumps(self.cache_docs[name]))

    def cache_write(self, name: str, payload: Dict[str, Any]) -> Path:
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是 dict")
        self.cache_docs[name] = json.loads(json.dumps(payload))
        self.cache_writes.append(name)
        return Path(name + ".json")      # 声明返回值，测试不读它

    def db_read(self, name: str) -> sqlite3.Connection:
        p = self.dbs.get(name)
        if p is None or not Path(p).exists():
            raise FileNotFoundError("db not available: %s" % name)
        conn = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def panel_json(self, name: str) -> Any:
        if name not in self.panels:
            raise FileNotFoundError("panel json not available: %s" % name)
        return self.panels[name]

    def json_read(self, name: str) -> Any:
        """白名单生产 JSON（非 data/panel）：未声明/缺文件 → FileNotFoundError（插件降级）。"""
        if name not in self.jsons:
            raise FileNotFoundError("json not available: %s" % name)
        return self.jsons[name]

    def lib(self, name: str) -> Any:
        if name in self._libs:
            return self._libs[name]
        from app.core import paths as core_paths
        from app.core.contract import find_forbidden_lib_usage
        path = core_paths.lib_path(name)
        if path is None or not path.is_file():
            raise FileNotFoundError("lib not available: %s" % name)
        assert find_forbidden_lib_usage(path.read_text(encoding="utf-8")) == [], \
            "口径库必须无 import 期副作用"
        spec = importlib.util.spec_from_file_location("_test_lib_compat_%s" % name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        parent = str(path.parent)
        inserted = parent not in sys.path
        if inserted:
            sys.path.append(parent)
        try:
            spec.loader.exec_module(mod)
        finally:
            if inserted and parent in sys.path:
                sys.path.remove(parent)
        self._libs[name] = mod
        return mod

    @property
    def quotes(self) -> Any:
        if self._quotes is None:
            raise PermissionError("quotes 未注入")
        return self._quotes


# ---------------------------------------------------------------- 临时库

DATES = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]


def _make_amv_db(path: Path, n_dates: int = 5) -> Path:
    """临时 amv_board.db：sector_daily / board_flow / daily_total 三张表。

    板块 881101.TI 的 AMV 逐日 +10%，收盘逐日 +1（便于手算 card 断言）；
    881102.TI 只有 3 日数据（<5 → card 必须跳过它，与旧端 `continue` 一致）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    dates = DATES[:n_dates]
    cur.execute("CREATE TABLE daily_total(date TEXT PRIMARY KEY, total REAL NOT NULL)")
    cur.executemany("INSERT INTO daily_total VALUES(?,?)",
                    [(d, 1e11) for d in dates])
    cur.execute("""CREATE TABLE sector_daily(
        date TEXT NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
        amv REAL NOT NULL, share REAL NOT NULL, rank INTEGER)""")
    rows = []
    for i, d in enumerate(dates):
        rows.append((d, "L2", "881101.TI", "种植业与林业", (1.0 + i * 0.1) * 1e10, 0.005, 1))
        rows.append((d, "L2", "BK9999", "未覆盖", 0.6e10, 0.002, 2))
        rows.append((d, "L3", "884001.TI", "种子生产", 0.4e10, 0.002, 1))
        if i >= n_dates - 3:      # 881102 只有 3 日
            rows.append((d, "L2", "881102.TI", "养殖业", 0.7e10, 0.003, 3))
    cur.executemany("INSERT INTO sector_daily VALUES(?,?,?,?,?,?,?)", rows)
    cur.execute("""CREATE TABLE board_flow(
        date TEXT NOT NULL, board_code TEXT NOT NULL,
        main_net REAL, main_pct REAL, close REAL, pct REAL, flow_em REAL,
        flow_em_pct REAL, flow_calc REAL, flow_main REAL,
        o REAL, h REAL, l REAL, amt REAL)""")
    bf = []
    for i, d in enumerate(dates):
        bf.append((d, "881101.TI", 1e5, 0.02, 100.0 + i, 0.01, 1e7, 0.03,
                   2e6, 5e4 * (i + 1), 99.0, 102.0, 98.0, 5e8))
    cur.executemany("INSERT INTO board_flow VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", bf)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def ctx(tmp_path: Path) -> FakeCtx:
    return FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")})


@pytest.fixture()
def client(backend, ctx: FakeCtx) -> TestClient:
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    return TestClient(app)


TRACK_DOC = {
    "meta": {"schema_version": "1.0.0", "document_type": "amv_live_sector_track",
             "generated_at": "2026-10-23 15:00:56", "producer": "engine"},
    "dates": {
        "20261022": [
            {"ts": "2026-10-22 09:30:29", "all": 0.0067,
             "boards": {"881101.TI": {"name": "种植业与林业", "amv_pct": 0.9818,
                                      "amt_share": 0.64, "change_pct": 0.2019}}},
            {"ts": "2026-10-22 09:31:29", "all": 0.0071,
             "boards": {"881101.TI": {"name": "种植业与林业", "amv_pct": 1.02,
                                      "amt_share": 0.65, "change_pct": 0.31}}},
        ],
        # 混用两种日期键：旧端 2026-10-08 修过的坑，取最新必须按去横杠比较
        "2026-10-23": [
            {"ts": "2026-10-23 09:30:29", "all": 0.001,
             "boards": {"881101.TI": {"name": "种植业与林业", "amv_pct": 0.5,
                                      "amt_share": 0.6, "change_pct": 0.1,
                                      "chg_e": 0.1, "chg_w": 0.2}}},
        ],
    },
}


@pytest.fixture()
def track_ctx(tmp_path: Path) -> FakeCtx:
    return FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")},
                   panels={"amv_live_sector_track": TRACK_DOC})


@pytest.fixture()
def track_client(backend, track_ctx: FakeCtx) -> TestClient:
    app = FastAPI()
    app.include_router(backend.register(track_ctx), prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------- 1. manifest

def test_manifest_id_matches_directory() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["id"] == PLUGIN_ID == PLUGIN_DIR.name
    assert data["manifest_version"] == 1
    for key in ("name", "version", "min_core_version", "enabled", "kind"):
        assert key in data, "manifest 缺字段: %s" % key
    assert data["backend"]["module"] == "backend"
    assert data["backend"]["entry"] == "register"


def test_manifest_mounts_at_root() -> None:
    """旧前端请求 `/api/<名>`：没有 mount=root 就永远找不到（任务书第 2 条硬要求）。"""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["backend"]["mount"] == "root"


def test_manifest_data_write_empty_and_no_undeclared_panel() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["data"]["write"] == [], "v1 插件 data.write 必须为空"
    assert data["data"]["read"] == ["amv_board.db"]
    # 2026-10-23：`amv_live_sector_track` / `etf_sector_map` 已由 01 补进
    # `paths.READABLE_PANEL` 白名单（两者都是已有生产文件），所以这里可以声明。
    assert sorted(data["data"]["panel"]) == ["amv_live_sector_track", "etf_sector_map"]
    # 声明的每个名字都必须真在全局白名单里（防“自己声明自己”绕过）
    from app.core import paths as core_paths
    for name in data["data"]["panel"]:
        assert name in core_paths.READABLE_PANEL, name


def test_manifest_declares_temperature_json() -> None:
    """N3：temperature_latest 不在 data/panel（在 夏天-activity/工具/temperature_data）
    → 走 `data.json` + `ctx.json_read`（paths.READABLE_JSON）。
    """
    from app.core import paths as core_paths

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["data"]["json"] == ["temperature_latest"]
    assert "temperature_latest" in core_paths.READABLE_JSON
    assert core_paths.READABLE_JSON["temperature_latest"].name == "temperature_latest.json"
    assert data["version"] == "0.2.1", "2026-10-27 idx_chg 读路径补值 → 0.2.0 → 0.2.1"


def test_manifest_declares_libs_and_quotes() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert sorted(data["data"]["lib"]) == ["amv_regime", "indicators"]
    assert data["data"]["quotes"] is True, "breadth_880 需要行情源门面"


# ---------------------------------------------------------------- 3. 静态扫描

def test_no_direct_db_connect() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("sqlite3.connect", "pymysql", "psycopg", "create_engine", "sqlalchemy"):
        assert token not in src, "出现禁止的直连写法: %s" % token


def test_no_self_quote_connection() -> None:
    """CONTRACT §4.1：行情源只能走 ctx.quotes，不得自己连 eltdx。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("from eltdx", "import eltdx", "TdxClient", "xtquant", "xtdata"):
        assert token not in src, "出现自连行情源写法: %s" % token


def test_no_http_and_no_subprocess() -> None:
    """取数一律走 ctx；插件不许发 HTTP、起子进程、写文件。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("urllib", "requests.", "httpx", "socket.", "subprocess",
                  "open(", "json.dump", "shutil"):
        assert token not in src, "出现禁止的 IO 写法: %s" % token


def test_reads_only_through_ctx() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("ctx.db_read", "ctx.panel_json", "ctx.lib", "ctx.quotes"):
        assert token in src, "应只经 %s 取数" % token


# ---------------------------------------------------------------- 2. 路由

def test_register_returns_apirouter(backend, ctx: FakeCtx) -> None:
    assert isinstance(backend.register(ctx), APIRouter)


def test_routes_are_root_relative(backend, ctx: FakeCtx) -> None:
    """路径里不能再写 /api（core 会拼）；也不能带 /api/p/<id>（那时旧前端找不到）。"""
    paths = {r.path for r in backend.register(ctx).routes}
    assert paths == EXPECTED_PATHS
    for p in paths:
        assert not p.startswith("/api"), "router 路径不得自带 /api 前缀: %s" % p


def test_all_endpoints_200(client: TestClient) -> None:
    """9 条全部 200 —— 包括 temperature（缺数据时给同构骨架，不再 404）。"""
    for path in sorted(EXPECTED_PATHS):
        resp = client.get("/api" + path)
        assert resp.status_code == 200, (path, resp.status_code, resp.text)


def test_temperature_fallback_is_old_shape(client: TestClient) -> None:
    """拿不到数据 → 旧端同构骨架（非 404、非空对象）。

    01 验收：原实现回 404 判未实现。改为骨架后，前端能正常渲染、结构比对能过。
    """
    body = client.get("/api/temperature_latest.json").json()
    assert isinstance(body, dict) and body, "必须是同构骨架而非 404 / 空对象"
    # 旧端 42 行样本的全部顶层键
    for key in ("date", "computed_at", "regime", "valuation_source", "dims_valid",
                "temperature_raw_sum", "dimensions", "temperature", "band",
                "cap_raw", "cap_effective", "cap_label", "note",
                "regime_note", "single_stock_cap"):
        assert key in body, "缺顶层键: %s" % key
    assert set(body["dimensions"]) == {"valuation", "volume", "trend", "sentiment"}
    assert set(body["dimensions"]["valuation"]) == {"pe", "percentile", "score"}
    assert set(body["dimensions"]["volume"]) == {"amount_now_yi", "amount_20d_yi",
                                                 "ratio", "score"}
    assert set(body["dimensions"]["trend"]) == {"close", "ma20", "ma20_prev",
                                                "dist_pct", "score"}
    assert set(body["dimensions"]["sentiment"]) == {"up", "down", "ratio", "score"}
    # 值一律 None：不编造数字
    assert body["temperature"] is None and body["band"] is None


def test_temperature_passthrough_when_available(backend, tmp_path: Path) -> None:
    """白名单已登记 + manifest 已声明 → 原样透传（走 ctx.json_read，不是 panel_json）。"""
    doc = {"date": "2026-10-23", "temperature": 65, "band": "高温",
           "dimensions": {"valuation": {"pe": 16.15, "percentile": 44.63, "score": 12}},
           "single_stock_cap": "15%"}
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")},
                  jsons={"temperature_latest": doc})
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    assert TestClient(app).get("/api/temperature_latest.json").json() == doc


# ---------------------------------------------------------------- sector_card

def test_sector_card_empty_codes_returns_empty_dict(client: TestClient) -> None:
    assert client.get("/api/sector_card.json?codes=").json() == {}


def test_sector_card_shape_and_values(client: TestClient) -> None:
    body = client.get("/api/sector_card.json?codes=881101.TI&level=L2&days=5&win=5").json()
    assert set(body) == {"881101.TI"}, "881102 只有 3 日 <5，必须跳过（旧端同）"
    item = body["881101.TI"]
    assert set(item) == {"close", "amv", "main_net", "w_delta", "amv_pct", "real_pct", "win"}
    assert set(item["close"]) == {"c", "yl", "wh"}
    assert set(item["amv"]) == {"a", "ma"}
    assert item["win"] == 5
    # AMV 1.0e10 → 1.4e10：+40%（5 日，win=5 → i0 = max(0,4-5) = 0）
    assert item["amv_pct"] == 40.0
    # 收盘 100 → 104：+4%
    assert item["real_pct"] == 4.0
    # 权重 = avf / daily_total(恒 1e11)：0.10 → 0.14，差 0.04 → 4.0 pp
    assert item["w_delta"] == 4.0
    # flow_main 2.5e5 万元 → 25.0 亿元（旧端 /1e4）
    assert item["main_net"] == 25.0
    # path 必须是非空 SVG d 串
    assert item["amv"]["a"].startswith("M ")
    assert item["close"]["c"].startswith("M ")


def test_sector_card_path_uses_index_for_x(client: TestClient) -> None:
    """旧端 x 用**下标**（None 处留空档），不能按有效点序号重排。"""
    body = client.get("/api/sector_card.json?codes=881101.TI&days=5").json()
    path = body["881101.TI"]["amv"]["a"]
    xs = [float(pair.split(",")[0]) for pair in path[2:].split(" L ")]
    assert xs == [0.0, 25.0, 50.0, 75.0, 100.0]


def test_sector_card_unknown_code_is_skipped(client: TestClient) -> None:
    assert client.get("/api/sector_card.json?codes=999999.TI").json() == {}


def test_sector_card_level_invalid_falls_back_to_l2(client: TestClient) -> None:
    ok = client.get("/api/sector_card.json?codes=881101.TI&level=NOPE&days=5").json()
    l2 = client.get("/api/sector_card.json?codes=881101.TI&level=L2&days=5").json()
    assert ok == l2


def test_sector_card_days_clamped(client: TestClient) -> None:
    """days 夹 [20,120]（旧端 max(20, min(days,120))）→ 库里只有 5 日，仍全给。"""
    body = client.get("/api/sector_card.json?codes=881101.TI&days=1").json()
    assert body["881101.TI"]["amv_pct"] == 40.0


def test_sector_card_survives_missing_db(backend, tmp_path: Path) -> None:
    """库缺失 → 空对象（旧端 `if not exists: return {}`），不 500。"""
    app = FastAPI()
    app.include_router(backend.register(FakeCtx({})), prefix="/api")
    resp = TestClient(app).get("/api/sector_card.json?codes=881101.TI")
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------- sector_spark

def test_sector_spark_shape(client: TestClient) -> None:
    body = client.get("/api/sector_spark.json?codes=881101.TI&days=5").json()
    assert set(body) == {"881101.TI"}
    assert set(body["881101.TI"]) == {"amv", "ma10"}
    assert body["881101.TI"]["amv"].startswith("M ")


def test_sector_spark_skips_short_or_gappy_series(client: TestClient) -> None:
    """旧端：len<5 或含 None → 跳过（881102 只有 3 日）。"""
    body = client.get("/api/sector_spark.json?codes=881101.TI,881102.TI&days=5").json()
    assert "881102.TI" not in body


def test_sector_spark_empty_codes(client: TestClient) -> None:
    assert client.get("/api/sector_spark.json").json() == {}


# ---------------------------------------------------------------- sector_intraday

def test_sector_intraday_points_and_meta(track_client: TestClient) -> None:
    body = track_client.get("/api/sector_intraday.json?codes=881101.TI").json()
    assert body["source"] == "amv_live_sector_track"
    # 最新日按去横杠比较 → 2026-10-23 胜出（旧端 10-08 修过的排序坑）
    assert body["date"] == "2026-10-23"
    assert body["asof"] == "2026-10-23 09:30:29"
    code = body["codes"]["881101.TI"]
    assert set(code) == {"name", "points", "count", "ok"}
    assert code["ok"] is True and code["count"] == 1
    assert code["points"][0] == {"t": "09:30", "amv_pct": 0.5, "change_pct": 0.1}
    assert code["name"] == "种植业与林业"


def test_sector_intraday_only_requested_codes(track_client: TestClient) -> None:
    body = track_client.get("/api/sector_intraday.json?codes=881101.TI,884001.TI").json()
    assert set(body["codes"]) == {"881101.TI", "884001.TI"}
    # 无轨迹的码：ok=false + count=0（不编造）
    assert body["codes"]["884001.TI"] == {"name": "884001.TI", "points": [], "count": 0, "ok": False}


def test_sector_intraday_without_panel_degrades(client: TestClient) -> None:
    """生产文件不在白名单（或缺失）→ 旧端「无数据」形状，不 500。"""
    body = client.get("/api/sector_intraday.json?codes=881101.TI").json()
    assert body == {"asof": "", "date": None, "source": "none",
                    "codes": {"881101.TI": {"name": "881101.TI", "points": [],
                                            "count": 0, "ok": False}}}


def test_sector_intraday_empty_codes(client: TestClient) -> None:
    assert client.get("/api/sector_intraday.json").json() == {
        "asof": "", "date": None, "source": "none", "codes": {}}


# ---------------------------------------------------------------- sector_track

def test_sector_track_shape(track_client: TestClient) -> None:
    body = track_client.get("/api/sector_track.json").json()
    assert body["date"] == "2026-10-23"
    assert len(body["snapshots"]) == 1
    assert body["boards"]["881101.TI"]["name"] == "种植业与林业"
    for key in ("weights", "weights_date", "stale", "stale_reason", "last_ts", "meta"):
        assert key in body, key
    assert body["meta"]["document_type"] == "amv_live_sector_track"
    assert body["last_ts"] == "2026-10-23 09:30:29"


def test_sector_track_weights_from_prev_trading_day(track_client: TestClient) -> None:
    """权重基线 = track 日**之前**最近一交易日的 L2 share（%），BK9999 不下发。"""
    body = track_client.get("/api/sector_track.json").json()
    assert body["weights_date"] == "2026-10-05"     # 临时库最新日 < 2026-10-23
    # share 0.005 → 0.5% / 0.003 → 0.3%；BK9999 必须剔除
    assert body["weights"] == {"881101.TI": 0.5, "881102.TI": 0.3}


def test_sector_track_pick_by_date(track_client: TestClient) -> None:
    body = track_client.get("/api/sector_track.json?date=20261022").json()
    assert body["date"] == "20261022"
    assert len(body["snapshots"]) == 2
    # 另一种键写法也能命中（去横杠比较）
    assert track_client.get("/api/sector_track.json?date=2026-10-22").json()["date"] == "20261022"


def test_sector_track_unknown_date_falls_back_to_latest(track_client: TestClient) -> None:
    assert track_client.get("/api/sector_track.json?date=19990101").json()["date"] == "2026-10-23"


def test_sector_track_without_panel_degrades(client: TestClient) -> None:
    """降级骨架必须与旧端正常分支**逐键一致**。

    01 验收：原实现多一个 `source` 键（旧端正常分支没有它），且 boards 为空。
    """
    body = client.get("/api/sector_track.json").json()
    assert set(body) == {"date", "snapshots", "boards", "weights", "weights_date",
                         "stale", "stale_reason", "last_ts", "meta"}
    assert "source" not in body, "旧端正常分支没有 source 键"
    assert body["snapshots"] == [] and body["boards"] == {}


def test_sector_track_shape_matches_old_contract(track_client: TestClient) -> None:
    """有数据时键集合也必须与旧端一致（不许出现 source）。"""
    body = track_client.get("/api/sector_track.json").json()
    assert set(body) == {"date", "snapshots", "boards", "weights", "weights_date",
                         "stale", "stale_reason", "last_ts", "meta"}
    # boards 下每个板块的字段名（旧端样本 amv_live_sector_track.json 实读：6 个）
    board = body["boards"]["881101.TI"]
    assert set(board) == {"name", "amv_pct", "amt_share", "change_pct", "chg_e", "chg_w"}


def test_track_stale_only_during_auction(backend) -> None:
    """只有连续竞价时段才判 stale；午休/盘后/周末冻结属正常（旧端 10-09 修过的误报）。"""
    last = "2026-10-23 10:00:00"
    # 盘中 10:30，末点 30 分钟前 → stale
    assert backend._track_stale(last, dt.datetime(2026, 10, 23, 10, 30))[0] is True
    # 午休 12:00 → 不判
    assert backend._track_stale(last, dt.datetime(2026, 10, 23, 12, 0))[0] is False
    # 盘后 16:00 → 不判
    assert backend._track_stale(last, dt.datetime(2026, 10, 23, 16, 0))[0] is False
    # 周末 → 不判
    assert backend._track_stale(last, dt.datetime(2026, 10, 24, 10, 30))[0] is False
    # 盘中但末点新鲜 → 不 stale
    assert backend._track_stale("2026-10-23 10:29:00", dt.datetime(2026, 10, 23, 10, 30))[0] is False
    # 无末点 → stale 且原因非空
    ok, reason = backend._track_stale("", dt.datetime(2026, 10, 23, 10, 30))
    assert ok is True and reason


# ---------------------------------------------------------------- backfill_status / health

def test_backfill_status_shape(client: TestClient) -> None:
    """只读端点：结构照旧，但**不读 engine/state**（红线），恒无任务在跑。"""
    body = client.get("/api/sector_track_backfill_status").json()
    assert body == {"ok": True, "running": False, "status_file": {}}


def test_sector_health_shape(client: TestClient) -> None:
    body = client.get("/api/sector/health").json()
    assert set(body) == {"alive", "port"}
    assert body["port"] == 8002
    assert body["alive"] is True            # 临时库有 sector_daily


def test_sector_health_false_without_db(backend) -> None:
    app = FastAPI()
    app.include_router(backend.register(FakeCtx({})), prefix="/api")
    body = TestClient(app).get("/api/sector/health").json()
    assert body["alive"] is False and body["port"] == 8002


# ---------------------------------------------------------------- etf map

def test_etf_map_fallback_has_all_top_keys(client: TestClient) -> None:
    """01 验收：缺 6560 键，顶层 `aliases` / `aliases_unresolved` 新版没有。

    降级骨架必须与旧端 `etf_sector_map.json` 的**全量顶层键**同构。
    """
    body = client.get("/api/etf_sector_map.json").json()
    for key in ("generated_by", "note", "targets", "etfs", "mapped",
                "unmatched", "aliases", "aliases_unresolved", "map"):
        assert key in body, "缺顶层键: %s" % key
    assert body["map"] == {}
    assert body["aliases_unresolved"] == []


def test_etf_map_passthrough(backend, tmp_path: Path) -> None:
    doc = {"generated_by": "engine/tools/build_etf_sector_map.py",
           "map": {"158005.SZ": {"name": "工程机械ETF博时", "sector": "工程机械",
                                 "sector_code": "881268.TI", "level": "L2"}}}
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")},
                  panels={"etf_sector_map": doc})
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    assert TestClient(app).get("/api/etf_sector_map.json").json() == doc


# ---------------------------------------------------------------- breadth_880

class _Snap:
    def __init__(self, last, open_, high, amount):
        self.last_price, self.open_price = last, open_
        self.high_price, self.amount = high, amount


class _SnapPage:
    def __init__(self, *snaps):
        self.snapshots = list(snaps)


class _FakeQuotes:
    """行情源门面替身：`call()` 打快照，`daily_bars()` 打日线（缺省 → 空表）。"""

    def __init__(self, mapping, bars=None):
        self._mapping = mapping
        self._bars = bars or {}

    def call(self, fn_name, *args, **kwargs):
        assert fn_name == "quotes.get_snapshots"
        code = args[0][0]
        return self._mapping.get(code)

    def daily_bars(self, glb, count=8, adjust=None):
        return list(self._bars.get(glb, ()))


class _Bar:
    def __init__(self, date8, open_, high, close, amount):
        self.time = dt.datetime.strptime(str(date8), "%Y%m%d")
        self.open, self.high, self.close, self.amount = open_, high, close, amount


def test_breadth_880_structure(client: TestClient) -> None:
    """today 8 键 / prev 7 键（旧端 prev 没有 total），不可达一律 null。"""
    body = client.get("/api/breadth_880.json").json()
    assert set(body) == {"today", "prev"}
    assert set(body["today"]) == {"date", "up", "down", "flat", "limit_up",
                                  "limit_down", "total", "amount"}
    assert set(body["prev"]) == {"date", "up", "down", "flat", "limit_up",
                                 "limit_down", "amount"}
    assert body["today"]["up"] is None and body["prev"]["up"] is None


def test_breadth_880_computes_flat_from_total(backend, tmp_path: Path) -> None:
    """平 = 总数 − 涨 − 跌；880006 给涨停/跌停。走 ctx.quotes，不自连。"""
    q = _FakeQuotes({"sh880005": _SnapPage(_Snap(3000, 1800, 4900, 8.8e11)),
                     "sh880006": _SnapPage(_Snap(45, 3, 50, 1.0))})
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")}, quotes=q)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    body = TestClient(app).get("/api/breadth_880.json").json()
    assert body["today"]["up"] == 3000
    assert body["today"]["down"] == 1800
    assert body["today"]["total"] == 4900
    assert body["today"]["flat"] == 100
    assert body["today"]["limit_up"] == 45
    assert body["today"]["limit_down"] == 3
    assert body["today"]["amount"] == 8.8e11


def test_breadth_880_degrades_when_quotes_missing(backend, tmp_path: Path) -> None:
    """行情源返回 None / 无 quotes → 全 null，不抛。"""
    q = _FakeQuotes({})
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")}, quotes=q)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    body = TestClient(app).get("/api/breadth_880.json").json()
    assert body["today"]["up"] is None and body["today"]["limit_up"] is None
    assert body["prev"] == {"date": None, "up": None, "down": None, "flat": None,
                            "limit_up": None, "limit_down": None, "amount": None}


def _bars(offsets):
    """按「距今日 N 天」造 880005/880006 日线；家数值与旧端 .day 同语义（已 /100）。"""
    today = dt.date.today()
    out = {}
    for name, rows in offsets.items():
        out[name] = [_Bar((today - dt.timedelta(days=off)).strftime("%Y%m%d"), *vals)
                     for off, vals in rows]
    return out


def test_breadth_880_prev_from_daily_bars(backend, tmp_path: Path) -> None:
    """prev ← 880005/880006 昨日日线（旧端读 vipdoc .day，新版走 ctx.quotes.daily_bars）。

    880005: up=close / down=open / total=high；880006: limit_up=close / limit_down=open；
    平 = total − up − down；当日那根不能被当成「昨日」。
    """
    q = _FakeQuotes({}, bars=_bars({
        "sh880005": [(0, (909.0, 5577.0, 4526.0, 1.97e12)),      # 当日（不算昨日）
                     (1, (4999.0, 5577.0, 535.0, 1.5418e12))],  # 昨日
        "sh880006": [(0, (7.0, 85.0, 84.0, 1.97e12)),
                     (1, (55.0, 58.0, 33.0, 1.5418e12))],
    }))
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")}, quotes=q)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    prev = TestClient(app).get("/api/breadth_880.json").json()["prev"]
    yday = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d")
    assert prev["date"] == yday
    assert prev["up"] == 535 and prev["down"] == 4999
    assert prev["flat"] == 43              # 5577 − 535 − 4999
    assert prev["limit_up"] == 33 and prev["limit_down"] == 55
    assert prev["amount"] == 1.5418e12


def test_breadth_880_prev_null_when_stale(backend, tmp_path: Path) -> None:
    """末根距今 > 30 自然日（客户端停旧）→ 不冒充昨日，prev 全 null。"""
    q = _FakeQuotes({}, bars=_bars({
        "sh880005": [(200, (100.0, 5000.0, 3000.0, 1.0))],
        "sh880006": [(200, (10.0, 60.0, 50.0, 1.0))],
    }))
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")}, quotes=q)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    prev = TestClient(app).get("/api/breadth_880.json").json()["prev"]
    assert prev["up"] is None and prev["limit_up"] is None and prev["date"] is None


def test_breadth_880_prev_skips_snapshot_trade_day(backend, tmp_path: Path) -> None:
    """非交易日窗口：末根 = 快照代表的交易日（周五），prev 必须给更早那根（周四）。

    审计 R124 P1 场景：周末远端日线已含最近交易日 bar，快照同源返回该日收盘 →
    旧实现用「自然日 today」当锚，末根 `< today` 成立被当成昨日，prev 与 today 全等。
    锚改成日线末根日期后，末根被跳过，prev 落到真实上一交易日。
    """
    q = _FakeQuotes({}, bars=_bars({
        "sh880005": [(1, (909.0, 5577.0, 4526.0, 1.97e12)),      # 周五（= 快照 today）
                     (4, (4999.0, 5577.0, 535.0, 1.5418e12))],  # 周四（真昨日）
        "sh880006": [(1, (7.0, 85.0, 84.0, 1.97e12)),
                     (4, (55.0, 58.0, 33.0, 1.5418e12))],
    }))
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")}, quotes=q)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    body = TestClient(app).get("/api/breadth_880.json").json()
    prev = body["prev"]
    thursday = (dt.date.today() - dt.timedelta(days=4)).strftime("%Y%m%d")
    assert prev["date"] == thursday, "prev 必须是快照交易日的上一交易日：%s" % prev
    assert prev["up"] == 535 and prev["down"] == 4999
    assert prev["flat"] == 43
    assert prev["limit_up"] == 33 and prev["limit_down"] == 55
    # 关键回归：prev 不得与快照 today 同值（周末塌缩的直接症状）
    assert prev["up"] != body["today"]["up"] or prev["limit_up"] != body["today"]["limit_up"]


def test_breadth_880_prev_null_when_only_snapshot_bar(backend, tmp_path: Path) -> None:
    """日线只有一根（= 快照交易日）→ 没有更早的交易日，prev 全 null（不拿 today 冒充）。"""
    q = _FakeQuotes({}, bars=_bars({
        "sh880005": [(1, (909.0, 5577.0, 4526.0, 1.97e12))],
        "sh880006": [(1, (7.0, 85.0, 84.0, 1.97e12))],
    }))
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")}, quotes=q)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    prev = TestClient(app).get("/api/breadth_880.json").json()["prev"]
    assert prev == {"date": None, "up": None, "down": None, "flat": None,
                    "limit_up": None, "limit_down": None, "amount": None}


# ------------------------------------------------ breadth_880 盘后落盘（2026-11-07）
#
# 主人：收盘后的数据已经固定，重启面板不该把两块快照 + 两条日线重新拉一遍。
# 铁律：空/降级不写｜失败不写｜数据日 ≠ 应有交易日当 miss。


class _Clock:
    """`ctx.trade_clock` 替身：盘后开关与应有数据日都由测试指定（不依赖真实钟点）。"""

    def __init__(self, postclose: bool, expected8: str = ""):
        self._pc, self._exp = postclose, expected8

    def postclose(self) -> bool:
        return self._pc

    def expected_data_date8(self) -> str:
        return self._exp


def _today8() -> str:
    return dt.date.today().strftime("%Y%m%d")


def _breadth_ctx(backend, tmp_path: Path, clock: _Clock, quotes: Any) -> tuple:
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")},
                  quotes=quotes, clock=clock)
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    return ctx, TestClient(app)


def _valid_snaps() -> "_FakeQuotes":
    return _FakeQuotes({"sh880005": _SnapPage(_Snap(3000, 1800, 4900, 8.8e11)),
                        "sh880006": _SnapPage(_Snap(45, 3, 50, 1.0))})


def test_breadth_880_postclose_writes_and_hits(backend, tmp_path: Path) -> None:
    """盘后算完落盘；下次（哪怕行情源已不可达）直接返回缓存，不重拉。"""
    today8 = _today8()
    q = _valid_snaps()
    ctx, client = _breadth_ctx(backend, tmp_path, _Clock(True, today8), q)
    first = client.get("/api/breadth_880.json").json()
    assert first["today"]["up"] == 3000

    doc = ctx.cache_docs["breadth_880"]
    assert doc["date"] == today8, "数据日必须写进 payload"
    assert doc["today"]["up"] == 3000 and doc["prev"] == first["prev"]
    assert ctx.cache_writes == ["breadth_880"]

    def _boom(*a, **k):
        raise AssertionError("命中落盘缓存时不应再拉快照")

    q.call = _boom
    assert client.get("/api/breadth_880.json").json() == first


def test_breadth_880_no_write_without_counts(backend, tmp_path: Path) -> None:
    """铁律 1/2：涨跌家数全 null（源不可达/降级）→ 不落盘。"""
    ctx, client = _breadth_ctx(backend, tmp_path, _Clock(True, _today8()), _FakeQuotes({}))
    body = client.get("/api/breadth_880.json").json()
    assert body["today"]["up"] is None
    assert ctx.cache_writes == [] and ctx.cache_docs == {}


def test_breadth_880_no_hit_when_date_differs(backend, tmp_path: Path) -> None:
    """铁律 3：缓存数据日 ≠ 应有交易日 → 当 miss（不得把昨天的家数当今天）。"""
    today8 = _today8()
    ctx, client = _breadth_ctx(backend, tmp_path, _Clock(True, today8), _valid_snaps())
    ctx.cache_docs["breadth_880"] = {
        "date": "20200101",
        "today": {"date": "20200101", "up": 9999, "down": 1, "flat": 0,
                  "limit_up": 1, "limit_down": 1, "total": 10000, "amount": 1.0},
        "prev": {"date": "20191231", "up": 1, "down": 1, "flat": 0,
                 "limit_up": 1, "limit_down": 1, "amount": 1.0},
    }
    body = client.get("/api/breadth_880.json").json()
    assert body["today"]["up"] == 3000, "数据日不符时必须现算"
    assert ctx.cache_docs["breadth_880"]["date"] == today8, "现算后应改写成本日"


def test_breadth_880_no_hit_when_counts_missing(backend, tmp_path: Path) -> None:
    """数据日对得上但关键字段空（半成品）→ 不命中，必须现算。"""
    today8 = _today8()
    ctx, client = _breadth_ctx(backend, tmp_path, _Clock(True, today8), _valid_snaps())
    ctx.cache_docs["breadth_880"] = {
        "date": today8,
        "today": {"date": today8, "up": None, "down": None, "flat": None,
                  "limit_up": None, "limit_down": None, "total": None, "amount": None},
        "prev": {"date": None, "up": None, "down": None, "flat": None,
                 "limit_up": None, "limit_down": None, "amount": None},
    }
    assert client.get("/api/breadth_880.json").json()["today"]["up"] == 3000


def test_breadth_880_no_disk_io_intraday(backend, tmp_path: Path) -> None:
    """非盘后：不读不写盘。"""
    ctx, client = _breadth_ctx(backend, tmp_path, _Clock(False, _today8()), _valid_snaps())
    assert client.get("/api/breadth_880.json").json()["today"]["up"] == 3000
    assert ctx.cache_reads == [] and ctx.cache_writes == [] and ctx.cache_docs == {}


# ---------------------------------------------------------------- 真实 core 挂载

@pytest.fixture()
def core_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """用真实 core（create_app）挂本插件，库指到临时 fixture。

    **只把本插件复制进临时 plugins 目录**：并行任务正在改 compat/ 与 compat_kline/，
    直接指向 app/plugins 会让本测试被别人的中间态带红（且 root 路径可能撞车）。
    """
    import shutil

    project_root = PLUGIN_DIR.parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app.core import paths as core_paths
    from app.core.main import create_app

    plugins = tmp_path / "plugins"
    (plugins / PLUGIN_ID).mkdir(parents=True)
    for name in ("plugin.json", "backend.py"):
        shutil.copy2(PLUGIN_DIR / name, plugins / PLUGIN_ID / name)

    data = tmp_path / "data"
    amv = _make_amv_db(data / "amv_board.db")
    monkeypatch.setattr(core_paths, "READABLE_DB", {"amv_board.db": amv})
    # 落盘缓存指到临时目录：盘后跑测试时不得读写真实 data/panel_v2/_cache
    monkeypatch.setattr(core_paths, "OUT_DIR", tmp_path / "panel_v2")
    # temperature 走 json_read：指到不存在的临时文件（白名单声明仍在，读取降级到骨架）
    monkeypatch.setattr(core_paths, "READABLE_JSON",
                        {"temperature_latest": tmp_path / "no_temperature.json"})
    web = tmp_path / "web"
    web.mkdir()
    (web / "shell.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    return create_app(plugins_dir=plugins, web_dir=web)


def test_real_core_mounts_at_api_root(core_app, monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：core 发现本插件 → mount=root → 落在 /api/<名>，不是 /api/p/<id>/<名>。"""
    # 行情源打桩：单元测试不连 eltdx（不可用一律 None 也是真实降级路径）
    from app.core import quotes as core_quotes
    monkeypatch.setattr(core_quotes, "call", lambda *a, **k: None)
    with TestClient(core_app) as client:
        health = client.get("/_plugins/health").json()
        by_id = {p["id"]: p for p in health["plugins"]}
        assert PLUGIN_ID in by_id, by_id
        assert by_id[PLUGIN_ID]["ok"] is True, by_id[PLUGIN_ID]

        card = client.get("/api/sector_card.json?codes=881101.TI&days=5")
        spark = client.get("/api/sector_spark.json?codes=881101.TI&days=5")
        status = client.get("/api/sector_track_backfill_status")
        sector_health = client.get("/api/sector/health")
        etf = client.get("/api/etf_sector_map.json")
        breadth = client.get("/api/breadth_880.json")
        track = client.get("/api/sector_track.json")
        intraday = client.get("/api/sector_intraday.json?codes=881101.TI")
        temp = client.get("/api/temperature_latest.json")
        # 不应出现在插件前缀下
        wrong = client.get("/api/p/%s/sector_card.json" % PLUGIN_ID)

    assert card.status_code == 200 and "881101.TI" in card.json()
    assert spark.status_code == 200
    assert status.status_code == 200
    assert sector_health.status_code == 200
    assert etf.status_code == 200
    assert breadth.status_code == 200
    assert track.status_code == 200
    assert intraday.status_code == 200
    # temperature 走骨架，也是 200（01 验收要求不再 404）
    assert temp.status_code == 200 and temp.json()["temperature"] is None
    assert wrong.status_code == 404, "root 插件不应同时挂在 /api/p/<id>"


def test_real_core_health_all_ok(core_app) -> None:
    with TestClient(core_app) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/_plugins/health").json()["ok"] is True


# ---------------------------------------------------------------- 结构比对工具

def _shape(value: Any) -> Any:
    """递归取「键名/类型骨架」：数值可变，键名与嵌套结构必须一致。"""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "str"


def test_shape_helper_is_recursive() -> None:
    base = {"a": 1, "b": {"c": None, "d": [{"e": "x"}]}}
    same = {"a": 999.5, "b": {"c": None, "d": [{"e": "y"}]}}
    diff = {"a": 1, "b": {"c": None, "x": [{"e": "x"}]}}
    assert _shape(base) == _shape(same)
    assert _shape(base) != _shape(diff)


def test_sector_card_matches_contract_skeleton(client: TestClient) -> None:
    """结构契约（样本缺失时的降级校验）：键名集合必须与旧端一致。

    ⚠ 真实递归比对需要旧端真响应样本 docs/sector_samples/*.json，
    本次未采集（无 shell/网络能力），见报告 §2 的「未验证」项。

    期望值用**真实 Python 值**写，靠 `_shape` 归一成 "str"/"number" 再比；
    不能直接写 "str"/"number" 字面量（那本身就是 str，比不出类型差异）。
    """
    body = client.get("/api/sector_card.json?codes=881101.TI&days=5").json()
    assert _shape(body) == _shape({
        "881101.TI": {
            "close": {"c": "", "yl": "", "wh": ""},
            "amv": {"a": "", "ma": ""},
            "main_net": 0.0, "w_delta": 0.0,
            "amv_pct": 0.0, "real_pct": 0.0, "win": 0,
        }
    })


def test_shape_distinguishes_number_from_string() -> None:
    """回归：`_shape` 必须真的区分类型，否则上面的骨架测试会恒过（假 PASS）。"""
    assert _shape({"a": 1.5}) != _shape({"a": "1.5"})
    assert _shape({"a": 1.5}) == _shape({"a": 99})
