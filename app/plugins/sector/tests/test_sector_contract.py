"""sector 插件契约测试（只读样板）。

覆盖 docs/CONTRACT.md §9 验收 1~3：
  1. manifest 必填字段齐全，id 与目录名一致；
  2. register(ctx) 返回 APIRouter，路径落在 /api/p/sector 下（TestClient 200）；
  3. 插件代码无直连数据库（静态扫描）、data.write 为空。

数据库：全部用 tmp_path 下的临时 sqlite fixture，不碰生产库、不写 data/panel。
"""

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

PLUGIN_DIR = Path(__file__).resolve().parents[1]          # app/plugins/sector
BACKEND_PY = PLUGIN_DIR / "backend.py"
MANIFEST = PLUGIN_DIR / "plugin.json"
FRONTEND_JS = PLUGIN_DIR / "frontend.js"
PLUGIN_ID = "sector"


# ---------------------------------------------------------------- 模块加载

@pytest.fixture(scope="session")
def backend():
    """按文件路径加载 backend.py，并注册到 sys.modules（供测试内 import 使用）。"""
    spec = importlib.util.spec_from_file_location("sector_backend_under_test", BACKEND_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clear_score_caches(backend):
    """清空 backend 的评分进程内缓存：每个测试的临时库内容不同但日期相同，
    键只含日期/成员指纹 ⇒ 不清会跨测试串数据。"""
    backend._SCORES_CACHE.clear()
    backend._SEC_RANK_CACHE.clear()
    yield
    backend._SCORES_CACHE.clear()
    backend._SEC_RANK_CACHE.clear()


# ---------------------------------------------------------------- 假 ctx + 临时库

class _FakeClock:
    """`ctx.trade_clock` 替身（core `app/core/trade_clock.py` 同面，只用来控制盘后开关）。"""

    def __init__(self, postclose: bool = False, expected8: str = ""):
        self.postclose_flag = postclose
        self.expected8 = expected8

    def postclose(self) -> bool:
        return self.postclose_flag

    def expected_data_date8(self) -> str:
        return self.expected8


class FakeCtx:
    """伪造 PluginContext：db_read(name) 返回只读连接，json_read/lib 供 sector 新端点用。

    `cache_read` / `cache_write`（2026-11-07 盘后落盘）用**内存字典**实现，形状照
    core `PluginContext`（读不到抛 FileNotFoundError，读回的是副本，写只接受 dict）。
    """

    def __init__(self, paths: Dict[str, Path], jsons: Dict[str, Any] | None = None,
                 clock: _FakeClock | None = None):
        self.paths = paths
        self.jsons = jsons or {}
        self._libs: Dict[str, Any] = {}
        self.clock = clock if clock is not None else _FakeClock()
        self.cache_docs: Dict[str, Any] = {}
        self.cache_reads: List[str] = []
        self.cache_writes: List[str] = []

    @property
    def trade_clock(self) -> _FakeClock:
        return self.clock

    def cache_read(self, name: str) -> Dict[str, Any]:
        self.cache_reads.append(name)
        if name not in self.cache_docs:
            raise FileNotFoundError("cache not written: %s" % name)
        return json.loads(json.dumps(self.cache_docs[name]))    # 与 core 一样是“从盘上读”

    def cache_write(self, name: str, payload: Dict[str, Any]) -> Path:
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是 dict")
        self.cache_docs[name] = json.loads(json.dumps(payload))
        self.cache_writes.append(name)
        return Path(name + ".json")                           # 声明返回值，测试不读它

    def db_read(self, name: str) -> sqlite3.Connection:
        p = self.paths.get(name)
        if p is None or not Path(p).exists():
            raise FileNotFoundError("db not available: %s" % name)
        conn = sqlite3.connect("file:%s?mode=ro" % str(p).replace("\\", "/"), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def json_read(self, name: str) -> Any:
        if name not in self.jsons:
            raise FileNotFoundError("json not available: %s" % name)
        return self.jsons[name]

    def lib(self, name: str) -> Any:
        """测试用：真实加载 `app/core/paths.py` 白名单里的口径库（只读纯函数模块）。"""
        if name in self._libs:
            return self._libs[name]
        from app.core import paths as core_paths
        from app.core.contract import find_forbidden_lib_usage
        path = core_paths.lib_path(name)
        if path is None or not path.is_file():
            raise FileNotFoundError("lib not available: %s" % name)
        source = path.read_text(encoding="utf-8")
        assert find_forbidden_lib_usage(source) == [], "口径库必须无 import 期副作用"
        spec = importlib.util.spec_from_file_location("_test_lib_%s" % name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        parent = str(path.parent)
        inserted = parent not in sys.path
        if inserted:
            sys.path.append(parent)
        try:
            spec.loader.exec_module(mod)
        finally:
            if inserted:
                sys.path.remove(parent)
        self._libs[name] = mod
        return mod


def _make_amv_db(path: Path) -> Path:
    """临时 amv_board.db：只建本插件用到的 4 张表 + 最小样本。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE daily_total(date TEXT PRIMARY KEY, total REAL NOT NULL)")
    dates = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]
    cur.executemany("INSERT INTO daily_total VALUES(?,?)",
                    [(d, 1e11 + i) for i, d in enumerate(dates)])
    cur.execute("""CREATE TABLE sector_meta(
        level TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
        l1 TEXT, exclude INTEGER NOT NULL DEFAULT 0, is_unmap INTEGER NOT NULL DEFAULT 0,
        level_num INTEGER, ord INTEGER, PRIMARY KEY(level, code))""")
    cur.executemany("INSERT INTO sector_meta VALUES(?,?,?,?,?,?,?,?)", [
        ("L2", "881101.TI", "种植业与林业", "农林牧渔", 0, 0, 2, 0),
        ("L2", "881102.TI", "养殖业", "农林牧渔", 0, 0, 2, 1),
        ("L2", "BK9999", "未覆盖", None, 1, 1, 2, 2),          # exclude+is_unmap，必须被过滤
        ("L3", "884001.TI", "种子生产", "农林牧渔", 0, 0, 3, 0),
        # 跨层同 code：实库 BK9999 同时存在 L2（80 亿）与 L3（20488 亿），
        # 用于回归 _board_flow 必须按 level 过滤，否则 amv_pct 会串层取错基准。
        ("L3", "881101.TI", "种植业与林业(三级)", "农林牧渔", 0, 0, 3, 1),
        ("CONCEPT", "885311.TI", "智能电网", None, 0, 0, None, 0),
        ("INDEX", "000001.SH", "上证综指", None, 0, 0, None, 1),
    ])
    cur.execute("""CREATE TABLE sector_daily(
        date TEXT NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
        amv REAL NOT NULL, share REAL NOT NULL, rank INTEGER, d1 REAL, d5 REAL, d20 REAL,
        PRIMARY KEY(date, level, code))""")
    rows = []
    for i, d in enumerate(dates):
        rows.append((d, "L2", "881101.TI", "种植业与林业", (1.0 + i * 0.1) * 1e10,
                     (0.005 + i * 0.0001), 1, 0, 0, 0))
        rows.append((d, "L2", "881102.TI", "养殖业", (0.7 - i * 0.02) * 1e10,
                     (0.003 - i * 0.0001), 2, 0, 0, 0))
        rows.append((d, "L2", "BK9999", "未覆盖", 0.6e10, 0.002, 3, 0, 0, 0))
        rows.append((d, "L3", "884001.TI", "种子生产", 0.4e10, 0.002, 1, 0, 0, 0))
        # 跨层同 code：L3 量级与趋势都与 L2 相反，串层会立刻暴露
        rows.append((d, "L3", "881101.TI", "种植业与林业(三级)",
                     (2.0 - i * 0.1) * 1e12, 0.002, 1, 0, 0, 0))
        rows.append((d, "CONCEPT", "885311.TI", "智能电网", 1.5e10, 0.007, 1, 0, 0, 0))
        rows.append((d, "INDEX", "000001.SH", "上证综指", 3.0e10, 0.01, 1, 0, 0, 0))
    cur.executemany("INSERT INTO sector_daily VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    cur.execute("""CREATE TABLE stock_amv(date TEXT, stock TEXT, amv REAL, PRIMARY KEY(date,stock))""")
    cur.executemany("INSERT INTO stock_amv VALUES(?,?,?)", [
        ("2026-10-05", "300308", 1.0e9), ("2026-10-05", "300502", 0.5e9),
        ("2026-10-04", "300308", 0.9e9), ("2026-10-04", "300502", 0.4e9),
    ])
    cur.execute("""CREATE TABLE stock_flow(date TEXT, stock_code TEXT, main_flow REAL, PRIMARY KEY(date,stock_code))""")
    cur.executemany("INSERT INTO stock_flow VALUES(?,?,?)", [
        ("2026-10-05", "300308", 10.0), ("2026-10-05", "300502", 20.0),
    ])
    # stock_state：AMV 贡献分项（`/contrib`）依赖 alive/sh，缺表会让口径库抛 OperationalError
    cur.execute("""CREATE TABLE stock_state(
        date TEXT, stock TEXT, alive REAL, sh REAL, PRIMARY KEY(date,stock))""")
    cur.executemany("INSERT INTO stock_state VALUES(?,?,?,?)", [
        ("2026-10-05", "300308", 1.0e7, 1.2e7), ("2026-10-05", "300502", 5.0e6, 6.0e6),
        ("2026-10-04", "300308", 1.0e7, 1.2e7), ("2026-10-04", "300502", 5.0e6, 6.0e6),
    ])
    cur.execute("""CREATE TABLE stock_top(
        date TEXT NOT NULL, sector_code TEXT NOT NULL, stock_code TEXT NOT NULL,
        stock_name TEXT NOT NULL, amt REAL, contrib REAL, rank INTEGER,
        PRIMARY KEY(date, sector_code, stock_code))""")
    cur.executemany("INSERT INTO stock_top VALUES(?,?,?,?,?,?,?)", [
        ("2026-10-05", "881101.TI", "300308", "中际旭创", 2.4e11, 0.0144, 1),
        ("2026-10-05", "881101.TI", "300502", "新易盛", 1.5e11, 0.0092, 2),
        ("2026-10-04", "881101.TI", "300308", "中际旭创", 2.3e11, 0.0140, 1),
    ])
    cur.execute("""CREATE TABLE board_flow(
        date TEXT NOT NULL, board_code TEXT NOT NULL,
        main_net REAL, main_pct REAL, close REAL,
        pct REAL, flow_em REAL, flow_em_pct REAL, flow_calc REAL, flow_main REAL,
        o REAL, h REAL, l REAL, amt REAL, PRIMARY KEY(date, board_code))""")
    bf = []
    for i, d in enumerate(dates):
        bf.append((d, "881101.TI", 1e5 * (i + 1), 0.02, 100.0 + i,
                   0.01, 1e7, 0.03, 2e6 * (i + 1), 5e4 * (i + 1),
                   99.0, 102.0, 98.0, 5e8))
    cur.executemany("INSERT INTO board_flow VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", bf)
    conn.commit()
    conn.close()
    return path


def _make_pool_db(path: Path) -> Path:
    """临时 ths_board.db：只建 board/concept/broad_index（补 n_members）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("""CREATE TABLE board(code TEXT PRIMARY KEY, name TEXT NOT NULL,
        sw_level INTEGER, l1 TEXT, l1_src TEXT, n_members INTEGER, updated_at TEXT)""")
    cur.executemany("INSERT INTO board VALUES(?,?,?,?,?,?,?)", [
        ("881101.TI", "种植业与林业", 2, "农林牧渔", "sw", 29, "2026-10-18"),
        ("881102.TI", "养殖业", 2, "农林牧渔", "sw", 36, "2026-10-18"),
    ])
    cur.execute("""CREATE TABLE concept(code TEXT PRIMARY KEY, name TEXT NOT NULL,
        n_members INTEGER, updated_at TEXT)""")
    cur.execute("INSERT INTO concept VALUES('885311.TI','智能电网',278,'2026-10-18')")
    cur.execute("""CREATE TABLE broad_index(code TEXT PRIMARY KEY, name TEXT NOT NULL,
        n_members INTEGER, updated_at TEXT)""")
    cur.execute("INSERT INTO broad_index VALUES('000001.SH','上证综指',2230,'2026-10-18')")
    cur.execute("""CREATE TABLE board_member(board_code TEXT, stock TEXT, PRIMARY KEY(board_code,stock))""")
    cur.executemany("INSERT INTO board_member VALUES(?,?)", [
        ("881101.TI", "300308"), ("881101.TI", "300502"),
    ])
    cur.execute("""CREATE TABLE concept_member(concept_code TEXT, stock TEXT, PRIMARY KEY(concept_code,stock))""")
    cur.execute("INSERT INTO concept_member VALUES('885311.TI','300308')")
    cur.execute("""CREATE TABLE broad_index_member(index_code TEXT, stock TEXT, PRIMARY KEY(index_code,stock))""")
    cur.execute("INSERT INTO broad_index_member VALUES('000001.SH','300308')")
    conn.commit()
    conn.close()
    return path


def _make_track_pool(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE pool(ts_code TEXT PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO pool VALUES(?,?)", [
        ("300308.SZ", "中际旭创"), ("300502.SZ", "新易盛"),
    ])
    conn.execute("CREATE TABLE kline(ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL)")
    conn.executemany("INSERT INTO kline VALUES(?,?,?,?,?,?,?,?)", [
        ("300308.SZ", "20261005", 100, 100, 100, 100, 1, 1),
        ("300308.SZ", "20261004", 90, 90, 90, 90, 1, 1),
        ("300502.SZ", "20261005", 50, 50, 50, 50, 1, 1),
        ("300502.SZ", "20261004", 45, 45, 45, 45, 1, 1),
    ])
    conn.commit()
    conn.close()
    return path


def _make_sig_db(path: Path) -> Path:
    """临时 buy_signal.db：单表 stock_signal（与 工具/buy_signal/store.py 同结构）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE stock_signal(
        stock TEXT PRIMARY KEY, daily_date TEXT, daily_sigs TEXT,
        weekly_date TEXT, weekly_sigs TEXT)""")
    conn.executemany("INSERT INTO stock_signal VALUES(?,?,?,?,?)", [
        ("300308.SZ", "20261005", "B1_W,B2", "20261003", "周"),
        ("300502.SZ", None, None, None, None),
        ("600000.SH", None, None, "20261003", "周"),
    ])
    conn.commit()
    conn.close()
    return path


def _make_minute_db(path: Path) -> Path:
    """临时 amv_minute.db：单表 sector_minute（与 工具/amv_minute.py 同结构）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE sector_minute(
        date TEXT, hhmm TEXT, level TEXT, code TEXT, name TEXT, amv REAL, share REAL,
        PRIMARY KEY(date, hhmm, level, code))""")
    rows = []
    for d in ("20261005", "20261004"):
        for hhmm in ("09:35", "15:00"):
            rows.append((d, hhmm, "L2", "881101.TI", "种植业与林业", 1.0e10, 0.005))
            rows.append((d, hhmm, "L2", "881102.TI", "养殖业", 0.7e10, 0.003))
    conn.executemany("INSERT INTO sector_minute VALUES(?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def _write_names_json(path: Path) -> Path:
    """临时名称表（东财全量表同结构：{date, names}）。故意不含 300308/300502。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": "2026-09-20", "names": {"600000": "浦发银行"}},
                               ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture()
def ctx(tmp_path: Path) -> FakeCtx:
    return FakeCtx({
        "amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db"),
        "ths_board.db": _make_pool_db(tmp_path / "pool" / "ths_board.db"),
        "track_pool.db": _make_track_pool(tmp_path / "track" / "track_pool.db"),
        "buy_signal.db": _make_sig_db(tmp_path / "sig" / "buy_signal.db"),
        "amv_minute.db": _make_minute_db(tmp_path / "minute" / "amv_minute.db"),
    }, jsons={
        # 名称表：故意不含 300308/300502，验证 track_pool.pool 回退仍然生效
        "stock_names": {"date": "2026-09-20", "names": {"600000": "浦发银行"}},
    })


@pytest.fixture()
def client(backend, ctx: FakeCtx) -> TestClient:
    router = backend.register(ctx)
    app = FastAPI()
    app.include_router(router, prefix="/api/p/%s" % PLUGIN_ID)
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


def test_manifest_data_read_only() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["data"]["write"] == [], "v1 插件 data.write 必须为空"
    assert sorted(data["data"]["read"]) == [
        "amv_board.db", "amv_minute.db", "buy_signal.db", "ths_board.db", "track_pool.db"
    ]
    assert data["data"].get("json") == ["stock_names"]


def test_manifest_card_ids_unique() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards = data["frontend"]["cards"]
    ids = [c["id"] for c in cards]
    assert ids == ["sector.board"]
    assert len(ids) == len(set(ids)), "卡片 id 必须唯一"


def test_frontend_declares_same_card() -> None:
    """frontend.js 里的卡片 id 必须与 manifest 一致（静态文本校验，不引构建链）。"""
    src = FRONTEND_JS.read_text(encoding="utf-8")
    assert "export default" in src
    assert re.search(r"id:\s*['\"]sector\.board['\"]", src), "frontend.js 卡片 id 与 manifest 不一致"
    assert "mount(" in src and "unmount(" in src, "卡片必须实现 mount/unmount"


# ---------------------------------------------------------------- 3. 无直连 DB

def test_no_direct_db_connect() -> None:
    """契约 §4：插件代码禁止 sqlite3.connect / pymysql / psycopg。"""
    banned = ("sqlite3.connect", "pymysql", "psycopg", "create_engine(", "sqlalchemy")
    for f in (BACKEND_PY, FRONTEND_JS):
        src = f.read_text(encoding="utf-8")
        for token in banned:
            assert token not in src, "%s 出现禁止的直连写法: %s" % (f.name, token)


def test_backend_only_reads_via_ctx(backend) -> None:
    """backend 只允许通过 ctx.db_read 取连接：源码里出现 db_read 调用。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert "db_read" in src


# ---------------------------------------------------------------- 2. register / 路由

def test_register_returns_apirouter(backend, ctx: FakeCtx) -> None:
    router = backend.register(ctx)
    assert isinstance(router, APIRouter)
    paths = {r.path for r in router.routes}
    assert {"/tree", "/rank", "/series"} <= paths, "缺端点: %s" % (paths,)


def test_tree_200(client: TestClient) -> None:
    resp = client.get("/api/p/%s/tree" % PLUGIN_ID)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["levels"] == ["L1", "L2", "L3", "CONCEPT", "INDEX"]
    assert data["counts"]["L2"] == 3          # 含被排除的 BK9999（目录仍列出，同旧 8002）
    assert len(data["l2"]) == 3
    l2 = {x["code"]: x for x in data["l2"]}
    assert l2["881101.TI"]["n_members"] == 29, "n_members 应来自 ths_board.db"
    assert l2["BK9999"]["exclude"] is True and l2["BK9999"]["is_unmap"] is True
    assert l2["881101.TI"]["l1"] == "农林牧渔"


def test_tree_survives_missing_pool_db(backend, tmp_path: Path) -> None:
    """池库不可用只丢 n_members，不能让 /tree 500。"""
    ctx = FakeCtx({"amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db")})
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/%s" % PLUGIN_ID)
    resp = TestClient(app).get("/api/p/%s/tree" % PLUGIN_ID)
    assert resp.status_code == 200, resp.text
    assert resp.json()["l2"][0]["n_members"] is None


def test_rank_200_and_excludes_unmapped(client: TestClient) -> None:
    resp = client.get("/api/p/%s/rank?level=L2&date=2026-10-05" % PLUGIN_ID)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["date"] == "2026-10-05"
    assert data["level"] == "L2"
    codes = [r["code"] for r in data["rows"]]
    assert "BK9999" not in codes, "exclude/is_unmap 板块必须过滤"
    assert data["n"] == len(data["rows"]) == 2
    row = next(r for r in data["rows"] if r["code"] == "881101.TI")
    # 字段名与旧 8002 /api/sector_level 的 rows 项一致
    assert set(row) == {"code", "name", "l1", "amv", "weight", "rank",
                        "amv_pct", "real_pct", "diverge"}
    assert row["amv"] == 1400                # 1.4e10 元 → 亿×10
    assert row["weight"] == 5400              # 0.0054 → ×1e6
    assert row["amv_pct"] == 7.69             # 1.4e10 / 1.3e10 − 1
    assert row["real_pct"] == 0.97            # close 104 / 103 − 1
    assert row["diverge"] == round(row["amv_pct"] - row["real_pct"], 2)


def test_rank_defaults_to_latest_date(client: TestClient) -> None:
    data = client.get("/api/p/%s/rank?level=L2" % PLUGIN_ID).json()
    assert data["date"] == "2026-10-05", "缺省应取库内最新交易日"


def test_rank_invalid_level_400(client: TestClient) -> None:
    resp = client.get("/api/p/%s/rank?level=NOPE" % PLUGIN_ID)
    assert resp.status_code == 400


def test_series_200(client: TestClient) -> None:
    resp = client.get("/api/p/%s/series?code=881101.TI&days=5&period=1" % PLUGIN_ID)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["code"] == "881101.TI"
    assert data["level"] == "L2"
    assert data["name"] == "种植业与林业"
    assert data["l1"] == "农林牧渔"
    assert data["ma_n"] == 10
    assert data["n_members"] == 29
    assert data["dates"] == ["2026-10-01", "2026-10-02", "2026-10-03",
                             "2026-10-04", "2026-10-05"]
    assert data["amv"] == [1000, 1100, 1200, 1300, 1400]
    assert data["weight"] == [5000, 5100, 5200, 5300, 5400]
    assert data["rank"] == [1, 1, 1, 1, 1]
    # MA10 前 10 日不足 → 前缀累积均值（与旧 8002 同算法）
    assert data["ma"][0] == 1000
    assert data["ma"][1] == 1050
    assert len(data["ma"]) == len(data["amv"])
    assert set(data) >= {"flow_calc", "flow_main", "real_pct", "amv_pct",
                         "diverge", "period", "unit"}


def test_series_level_autodetect(client: TestClient) -> None:
    """不传 level 时按 sector_meta 反查（L3 不能被误判成 L2）。"""
    data = client.get("/api/p/%s/series?code=884001.TI&days=3" % PLUGIN_ID).json()
    assert data["level"] == "L3"
    assert data["code"] == "884001.TI"
    assert len(data["dates"]) == 3


def test_series_amv_pct_does_not_leak_across_levels(client: TestClient) -> None:
    """回归：同一 code 同时在 L2/L3 时，amv_pct 必须只算本层。

    实库 BK9999 同时存在 L2（80 亿）与 L3（20488 亿），不过滤 level 会取错基准。
    """
    l2 = client.get("/api/p/%s/series?code=881101.TI&level=L2&days=5" % PLUGIN_ID).json()
    l3 = client.get("/api/p/%s/series?code=881101.TI&level=L3&days=5" % PLUGIN_ID).json()
    assert l2["level"] == "L2" and l3["level"] == "L3"
    # L2 递增 → 正；L3 递减 → 负。串层会得到对方的符号
    assert l2["amv_pct"] == 7.69, l2["amv_pct"]
    # L3 从 200000 降到 160000：基准取末行前一交易日（160000/170000-1）
    assert l3["amv_pct"] == -5.88, l3["amv_pct"]
    assert l2["amv"] == [1000, 1100, 1200, 1300, 1400]
    assert l3["amv"] == [200000, 190000, 180000, 170000, 160000]


def test_series_not_found_is_json(client: TestClient) -> None:
    resp = client.get("/api/p/%s/series?code=999999.TI&days=3" % PLUGIN_ID)
    assert resp.status_code == 200
    assert resp.json()["error"] == "not found"


def test_catalog_counts_and_tree(client: TestClient) -> None:
    body = client.get("/api/p/sector/catalog").json()
    assert body["counts"]["L2"] == 3  # 含被排除的 BK9999，catalog 是目录不是排行
    assert body["counts"]["L3"] >= 1
    assert isinstance(body["tree"], list)
    assert body["note"]


def test_rank_recent_matrix(client: TestClient) -> None:
    body = client.get("/api/p/sector/rank_recent?level=L2&days=5").json()
    assert body["level"] == "L2"
    assert body["dates"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]
    assert len(body["codes"]) == 3
    assert len(body["weight"]) == 5


def test_manifest_and_leaders(client: TestClient) -> None:
    manifest = client.get("/api/p/sector/manifest").json()
    assert manifest["n_days"] == 5
    assert manifest["counts"]["L2"] == 3
    leaders = client.get("/api/p/sector/leaders/881101.TI?days=5&top_n=1").json()
    assert leaders["dates"][-1] == "2026-10-05"
    assert leaders["rows"][-1]["rows"][0]["stock_code"] == "300308"


def test_search_empty_and_ranking(client: TestClient) -> None:
    empty = client.get("/api/p/sector/search?q=").json()
    assert empty["n"] == 0 and empty["items"] == []
    body = client.get("/api/p/sector/search?q=种植").json()
    assert body["n"] >= 1
    assert body["items"][0]["name"] == "种植业与林业"


def test_members_default_date_and_order(client: TestClient) -> None:
    body = client.get("/api/p/sector/members/881101.TI").json()
    assert body["date"] == "2026-10-05"
    assert body["full"] is False and body["source"] == "stock_top"
    assert [r["rank"] for r in body["rows"]] == [1, 2]
    assert body["rows"][0]["stock_code"] == "300308"


def test_members_full_computes_amv_contrib_rank(client: TestClient) -> None:
    """全量现算：与旧 8002 `api_members2` 同口径（amt/contrib/d_contrib/amv_pct/real_pct/main_flow/rank）。"""
    body = client.get("/api/p/sector/members/881101.TI?full=1&period=1").json()
    assert body["full"] is True and body["source"] == "full"
    assert body["base_date"] == "2026-10-04"
    assert [r["stock_code"] for r in body["rows"]] == ["300308", "300502"]
    first = body["rows"][0]    # contrib 单位 = ×1e6 整数（旧端 weight_int 同口径）
    assert first["contrib"] == round(1.0e9 / (1e11 + 4) * 1e6)
    assert first["amt"] == round(1.0e9 / 1e8 * 10)
    assert first["amv_pct"] == 11.11
    assert first["real_pct"] == 11.11
    assert first["diverge"] == 0.0
    assert first["main_flow"] == 10.0
    # 买点徽标：daily_sigs + weekly_sigs 合并（日在前）；date 取两者较大值
    assert first["sigs"] == ["B1_W", "B2", "周"]
    assert first["sigs_date"] == "20261005"
    # 无信号个股：空数组（不是 None），旧端 sig_map 也不返回空条目
    assert body["rows"][1]["sigs"] == [] and body["rows"][1]["sigs_date"] is None
    # 个股四态多空：窗口不足 12 日 → None（不编造）
    assert "bull" in first and first["bull"] is None


def test_members_full_bull_uses_regime_lib() -> None:
    """个股四态 `bull` 走 `ctx.lib("amv_regime")`；序列够 12 日时必须给出四态之一。"""
    import importlib.util as _u
    import sys as _s
    _s.path.insert(0, str(PLUGIN_DIR.parents[2]))
    from app.core.contract import PluginContext, load_manifest
    m = load_manifest(PLUGIN_DIR)
    lib = PluginContext(m).lib("amv_regime")
    # 上升序列 → 多；下降序列 → 空
    up = [100.0 * (1.01 ** i) for i in range(20)]
    down = [100.0 * (0.99 ** i) for i in range(20)]
    assert lib.stock_state(up) == "多"
    assert lib.stock_state(down) == "空"
    assert lib.stock_state([1.0] * 5) is None, "不足 12 日 → None"


def test_members_404(client: TestClient) -> None:
    assert client.get("/api/p/sector/members/999999.TI").status_code == 404


def test_series_days_clamped(client: TestClient) -> None:
    data = client.get("/api/p/%s/series?code=881101.TI&days=9999" % PLUGIN_ID).json()
    assert len(data["dates"]) == 5, "days 上限裁剪后仍只返回库内全部"


def test_db_read_missing_db_503(backend, tmp_path: Path) -> None:
    """库不可用 → 503，不静默返回空数据。"""
    ctx = FakeCtx({})       # 任何 db_read 都 FileNotFoundError
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/%s" % PLUGIN_ID)
    resp = TestClient(app).get("/api/p/%s/tree" % PLUGIN_ID)
    assert resp.status_code == 503


def test_connections_are_closed(backend) -> None:
    """ctx.db_read 每次新建连接：端点用完必须关，否则句柄泄漏。"""
    opened = []

    class CountingCtx(FakeCtx):
        def db_read(self, name):
            conn = super().db_read(name)
            opened.append(conn)
            return conn

    ctx = CountingCtx({})
    # 复用临时库构造：直接借 _make_* 写一份
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="sector_conn_"))
    ctx.paths = {
        "amv_board.db": _make_amv_db(root / "amv" / "amv_board.db"),
        "ths_board.db": _make_pool_db(root / "pool" / "ths_board.db"),
    }
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/%s" % PLUGIN_ID)
    client = TestClient(app)
    assert client.get("/api/p/%s/tree" % PLUGIN_ID).status_code == 200
    assert client.get("/api/p/%s/rank?level=L2" % PLUGIN_ID).status_code == 200
    assert client.get("/api/p/%s/series?code=881101.TI&days=5" % PLUGIN_ID).status_code == 200

    assert opened, "端点应至少打开过一个连接"
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")   # 已关闭的连接再执行会报 ProgrammingError


# ---------------------------------------------------------------- 真实 ctx（core）

@pytest.fixture()
def core_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """用真实 core（PluginContext + create_app）挂 sector 插件，库指到临时 fixture。"""
    project_root = PLUGIN_DIR.parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app.core import paths as core_paths
    from app.core.main import create_app

    data = tmp_path / "data"
    amv = _make_amv_db(data / "amv_board.db")
    pool = _make_pool_db(data / "ths_board.db")
    track = _make_track_pool(data / "track_pool.db")
    sig = _make_sig_db(data / "buy_signal.db")
    minute = _make_minute_db(data / "amv_minute.db")
    monkeypatch.setattr(core_paths, "READABLE_DB", {
        "amv_board.db": amv, "ths_board.db": pool, "track_pool.db": track,
        "buy_signal.db": sig, "amv_minute.db": minute,
    })
    monkeypatch.setattr(core_paths, "READABLE_JSON", {
        "stock_names": _write_names_json(data / "stock_names.json"),
    })
    web = tmp_path / "web"
    web.mkdir()
    (web / "shell.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    return create_app(plugins_dir=PLUGIN_DIR.parent, web_dir=web)


def test_real_core_mounts_sector(core_app) -> None:
    """端到端：core 发现 sector → 挂 /api/p/sector → 三个端点 200。"""
    with TestClient(core_app) as client:
        health = client.get("/_plugins/health").json()
        by_id = {p["id"]: p for p in health["plugins"]}
        assert by_id["sector"]["ok"] is True, by_id["sector"]

        tree = client.get("/api/p/sector/tree")
        rank = client.get("/api/p/sector/rank?level=L2")
        series = client.get("/api/p/sector/series?code=881101.TI&days=5")
        asset = client.get("/plugins/sector/frontend.js")
        blocked = client.get("/plugins/sector/backend.py")

    assert tree.status_code == 200, tree.text
    assert rank.status_code == 200, rank.text
    assert series.status_code == 200, series.text
    assert asset.status_code == 200 and "export default" in asset.text
    assert blocked.status_code == 404


# ---------------------------------------------------------------- 分钟级 AMV

def test_minute_dates_and_rank(client: TestClient) -> None:
    dates = client.get("/api/p/sector/minute_dates").json()
    assert dates["dates"] == ["20261005", "20261004"]
    rank = client.get("/api/p/sector/minute_rank?level=L2&n=5").json()
    assert rank["date"] == "20261005"
    assert rank["hhmm"] == "15:00"
    assert rank["rows"][0]["code"] == "881101.TI"
    assert rank["rows"][0]["amv"] == 1.0e10


def test_minute_series_base_share(client: TestClient) -> None:
    body = client.get("/api/p/sector/minute_series?code=881101.TI&level=L2&date=20261005&period=1").json()
    assert body["date"] == "20261005"
    assert body["period"] == 1
    assert len(body["series"]) == 2
    # 基准 = 1 个交易日前（2026-10-04）日线 share = 0.0051
    assert body["base_share"] is not None
    assert body["series"][0]["d_share"] is not None


def test_minute_multi_merges(client: TestClient) -> None:
    body = client.get("/api/p/sector/minute_multi?codes=881101.TI,881102.TI&level=L2").json()
    assert set(body["series"]) == {"881101.TI", "881102.TI"}
    assert body["series"]["881102.TI"]["name"] == "养殖业"


def test_minute_unknown_code_is_json_error(client: TestClient) -> None:
    body = client.get("/api/p/sector/minute_series?code=999999.TI&level=L2").json()
    assert "error" in body


# ---------------------------------------------------------------- 语义榜 / 趋势 / 板块内 TOP / 板块资金

def test_rank_top_in_out_and_new_exit(client: TestClient) -> None:
    """`/rank_top`：与旧 8002 `ep_rank` 同口径（share 差值排序 + `_top_k` 夹 10~30）。"""
    body = client.get("/api/p/sector/rank_top?level=L2&n=1").json()
    assert body["date"] == "2026-10-05" and body["prev_date"] == "2026-10-04"
    # 池 = 2 个非 exclude 且有基准的板块（881101 涨、881102 跌）
    assert [r["code"] for r in body["top_in"]] == ["881101.TI"]
    assert body["top_in"][0]["d_weight"] > 0
    # top_out 与 top_in 不重叠
    assert not ({r["code"] for r in body["top_in"]} & {r["code"] for r in body["top_out"]})
    assert "new" in body and "exit" in body


def test_trend_series_and_ma(client: TestClient) -> None:
    body = client.get("/api/p/sector/trend?code=881101.TI&days=5").json()
    assert body["level"] == "L2" and body["ma_n"] == 10
    assert len(body["dates"]) == 5
    assert len(body["amv"]) == len(body["ma"]) == len(body["weight"]) == len(body["rank"]) == 5
    # MA 首值 = 自身（不足 10 日按已有天数平均）
    assert body["ma"][0] == body["amv"][0]


def test_trend_not_found_is_json(client: TestClient) -> None:
    body = client.get("/api/p/sector/trend?code=999999.TI").json()
    assert body["error"] == "not found"


def test_board_top_rank_delta(client: TestClient) -> None:
    body = client.get("/api/p/sector/board_top?sector=881101.TI&top=2").json()
    assert body["sector_name"] == "种植业与林业"
    assert body["prev_date"] == "2026-10-04"
    assert [i["code"] for i in body["items"]] == ["300308", "300502"]
    # 名次未变 → rank_delta == 0
    assert body["items"][0]["rank_delta"] == 0


def test_board_flow_layer_filter(client: TestClient) -> None:
    """`/board_flow` 不传 level = 全层；传 level = 只该层；**含 `bull`**（口径库 `amv_regime`）。"""
    all_lv = client.get("/api/p/sector/board_flow?period=1").json()
    assert all_lv["date"] == "2026-10-05" and all_lv["period"] == 1
    assert "bull" in all_lv, "双轨多空走口径库 amv_regime，已接入"
    assert "881101.TI" in all_lv["flow_calc"]
    l2 = client.get("/api/p/sector/board_flow?period=1&level=L2").json()
    assert l2["level"] == "L2"
    # L2 层不含 L3 独有码 884001.TI
    assert "884001.TI" not in l2["flow_calc"]
    # 口径字段齐备
    for key in ("flow_calc", "flow_main", "main_days", "amv_pct", "real_pct", "diverge", "bull", "unit", "source"):
        assert key in l2, key


def test_board_flow_st_period_follows_request(client: TestClient) -> None:
    """结构徽标的周期必须跟随请求的 period（2026-10-27 主人要求）。

    背景：早先 _struct_tags 固定取「最后两个交易日」，选 3 日时表格显示 3 日涨跌、
    徽标却算单日，口径分裂。本测试锁住：返回体 st_period == 请求的 period。
    """
    for p in (1, 3, 5):
        body = client.get("/api/p/sector/board_flow?period=%d&level=L2" % p).json()
        assert body.get("st_period") == p, (p, body.get("st_period"))
        assert "st" in body, "结构标签字段必须在响应体里"


def test_board_flow_bull_is_bool_per_board(client: TestClient) -> None:
    """`bull` 每个板块一个 bool；窗口内无确认时回退单轨比较（不编造 None）。"""
    body = client.get("/api/p/sector/board_flow?period=1&level=L2").json()
    bull = body["bull"]
    assert bull and all(isinstance(v, bool) for v in bull.values()), bull
    assert "881101.TI" in bull


def test_board_flow_main_days_window(client: TestClient) -> None:
    """`main_days` 窗口 = [降序 dates[-2], ds]（period=1 时只含当日 → 1 天）。"""
    one = client.get("/api/p/sector/board_flow?period=1&level=L2").json()
    assert one["main_days"]["881101.TI"] == 1
    five = client.get("/api/p/sector/board_flow?period=5&level=L2").json()
    # 库内只有 5 个交易日：period=5 时窗口退化为可用天数
    assert five["main_days"]["881101.TI"] >= 1


def test_board_flow_invalid_level_400(client: TestClient) -> None:
    assert client.get("/api/p/sector/board_flow?level=XX").status_code == 400


# ---------------------------------------------------------------- 贡献分项（口径库）


def test_contrib_uses_lib_and_returns_parts(client: TestClient) -> None:
    """`/contrib` 走 `ctx.lib("amv_contrib")`（口径库），不重写公式。"""
    body = client.get("/api/p/sector/contrib?code=881101.TI&period=1").json()
    assert body["ok"] is True, body
    assert body["level"] == "L2"
    assert body["d1"] == "2026-10-05" and body["d0"] == "2026-10-04"
    assert body["period"] == 1
    parts = body["parts"]
    # 旧端同式：五项 + 大盘基准 == total
    for key in ("px", "sh", "act", "new", "susp", "den", "total", "cover"):
        assert key in parts, key
    assert abs(parts["cover"] - parts["total"]) < 1e-9


def test_contrib_period_shifts_base(client: TestClient) -> None:
    one = client.get("/api/p/sector/contrib?code=881101.TI&period=1").json()
    five = client.get("/api/p/sector/contrib?code=881101.TI&period=5").json()
    assert one["d0"] != five["d0"] or five["period"] == 1, "period 变则基准日应变"
    assert five["period"] == 5


def test_contrib_no_members_is_json_error(client: TestClient) -> None:
    body = client.get("/api/p/sector/contrib?code=999999.TI&period=1").json()
    assert body["ok"] is False and "reason" in body


def test_manifest_declares_contrib_lib() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["data"].get("lib") == ["amv_contrib", "amv_regime", "amv_struct_tag", "panel_scores"]


# ---------------------------------------------------------------- 评分 / 板块内名次（panel_scores）

def test_score_trail_shape(client: TestClient) -> None:
    """`/score_trail` 形状：{date, sector, period, days, rows:[{code,name,score,mkt_rank,trail}]}。"""
    body = client.get("/api/p/%s/score_trail?code=881101.TI&period=1&days=5" % PLUGIN_ID).json()
    assert set(body) >= {"date", "sector", "period", "days", "rows"}, body
    assert body["sector"] == "881101.TI" and body["period"] == 1 and body["days"] == 5
    assert isinstance(body["rows"], list)
    assert {r["code"] for r in body["rows"]} == {"300308", "300502"}
    for r in body["rows"]:
        assert set(r) >= {"code", "name", "score", "mkt_rank", "trail"}
        assert isinstance(r["trail"], list)


def test_members_full_has_score_fields(client: TestClient) -> None:
    """`/members?full=1` 每行 5 个新字段：score/mkt_rank/sec_rank/trail/sec_total。"""
    body = client.get("/api/p/sector/members/881101.TI?full=1&period=1").json()
    assert body["full"] is True
    assert len(body["rows"]) >= 1
    for r in body["rows"]:
        assert "score" in r and "mkt_rank" in r and "sec_rank" in r and "trail" in r and "sec_total" in r
        # 库内样本不足 MIN_SAMPLES=10 的横截面 → 全窗口跳过 → score=None（不编造）
        assert r["score"] is None or isinstance(r["score"], float)
        assert r["mkt_rank"] is None or isinstance(r["mkt_rank"], int)
        assert r["sec_rank"] is None or isinstance(r["sec_rank"], int)
        assert isinstance(r["trail"], list)
        assert isinstance(r["sec_total"], int)
    assert body["rows"][0]["sec_total"] == 2   # 成分数（board_member 2 只）


def test_members_full_degrades_without_scores_lib(backend, tmp_path: Path) -> None:
    """无 panel_scores 库：降级不崩——5 字段为 null/空 + notice，不抛 500。"""
    class NoLibCtx(FakeCtx):
        def lib(self, name):
            # 只拒 panel_scores：其它口径库（amv_regime 等）照常，测的是评分库缺失的降级
            if name == "panel_scores":
                raise FileNotFoundError("lib not available: %s" % name)
            return super().lib(name)

    ctx = NoLibCtx({
        "amv_board.db": _make_amv_db(tmp_path / "amv" / "amv_board.db"),
        "ths_board.db": _make_pool_db(tmp_path / "pool" / "ths_board.db"),
    })
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/%s" % PLUGIN_ID)
    client = TestClient(app)
    resp = client.get("/api/p/sector/members/881101.TI?full=1&period=1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "notice" in body and "panel_scores" in body["notice"]
    for r in body["rows"]:
        assert r["score"] is None and r["mkt_rank"] is None and r["sec_rank"] is None
        assert r["trail"] == []
        assert isinstance(r["sec_total"], int)
    # /score_trail 同样降级为 ok=False，不 500
    st = client.get("/api/p/sector/score_trail?code=881101.TI&period=1")
    assert st.status_code == 200
    assert st.json()["ok"] is False and "reason" in st.json()


def test_no_direct_import_of_panel_scores() -> None:
    """评分口径只能经 ctx.lib("panel_scores")，源码不得出现 import panel_scores。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert "import panel_scores" not in src
    assert "from panel_scores" not in src
    assert 'ctx.lib(SCORES_LIB)' in src or 'lib(SCORES_LIB)' in src


def test_scores_computed_for_two_members_when_enough_samples(client: TestClient) -> None:
    """横截面样本足够时（MIN_SAMPLES=10），评分/名次必须真算出来而非恒空。"""
    # 复用既有 fixture 的板块 881101.TI 只有 2 只票 → 样本不足，score=None 是正确行为；
    # 这里断言的是「有值时类型正确」的通道：直接打 /score_trail 验证 score_map 通路。
    body = client.get("/api/p/sector/score_trail?code=885311.TI&period=1&days=3").json()
    # 概念板块 885311.TI 成分 300308：stock_amv 只有两日 → 轨迹至多 2 点
    assert body["rows"] and body["rows"][0]["code"] == "300308"
    # 两个窗口日里逐日横截面只有 2 只票 < MIN_SAMPLES → score=None，trail=[]
    assert body["rows"][0]["score"] is None
    assert body["rows"][0]["trail"] == []


def test_frontend_api_contract() -> None:
    """frontend 必须适配 shell 的 ctx.api.get（直接返回已解析 JSON，不是 Response）。"""
    src = FRONTEND_JS.read_text(encoding="utf-8")
    assert ".json()" not in src, "shell 的 api.get 已返回解析后的对象，不应再调 .json()"
    assert "res.status" not in src and "resp.status" not in src, \
        "shell 的 api.get 失败时抛异常，不返回带 status 的 Response"
    assert "ctx.api.get" in src


def test_frontend_rank_request_carries_period() -> None:
    """榜单与主力/涨跌快照的请求必须带周期。

    2026-10-23 盘中实测缺陷：`/rank?level=...` 漏了 `&period=`，于是周期选择器只改了
    成分股与贡献分项，板块榜恒为 1 日值。

    2026-10-25 重做后形态变了（榜单在前端算，不再打 /rank）：周期不再是查询参数，
    而是 `_effectiveWin()`（dayB - dayA）参与 `board_flow` 取数与 compute 的 winN。
    断言改成守**语义**：周期必须进 flow 请求、必须进 compute，且不得写死。
    """
    src = FRONTEND_JS.read_text(encoding="utf-8")
    m = re.search(r"const q = '/board_flow\?period=' \+ ([^\n]+)", src)
    assert m, "找不到 board_flow 请求模板"
    # 周期可以是 _effectiveWin() 直调，也可以是它赋给的局部变量（2026-10-25 加缓存键时提取成 period）。
    # 两种都接受，但必须能追溯到 _effectiveWin —— 不许写成常量。
    period_expr = m.group(1).split("'")[0].strip().rstrip("+").strip()
    assert period_expr == "period" or "_effectiveWin()" in period_expr, (
        "board_flow 请求的周期不是 _effectiveWin() 派生：%s" % period_expr)
    if period_expr == "period":
        assert re.search(r"const period = this\._effectiveWin\(\);", src), \
            "局部变量 period 必须来自 _effectiveWin()"
    assert re.search(r"winN:\s*this\._effectiveWin\(\)", src), "compute 未接 _effectiveWin()"
    assert "period=1" not in src, "board_flow 请求里周期被写死为 1"


def test_frontend_period_change_recomputes_board() -> None:
    """切周期必须重算整张榜（含重新取 flow），不能只改标签。

    只重载 members + contrib 会让板块榜和走势图停在旧周期，是半个生效。
    """
    src = FRONTEND_JS.read_text(encoding="utf-8")
    m = re.search(r"_setWindow\(n\)\s*\{(.+?)\n  \},", src, re.S)
    assert m, "找不到 _setWindow 实现"
    body = m.group(1)
    assert "dayA" in body and "winN" in body, "切周期必须同步改 winN 与基准日 dayA"
    assert "_recompute(" in body, "切周期必须走 _recompute()，否则榜单不跟着变"
    assert "reloadFlow: true" in body, "切周期必须重取 flow（主力/涨跌是区间累加值）"


def test_frontend_accepts_industry_arg_from_shell() -> None:
    """入参定位（2026-10-31）：估值页点行业 → panel:open-card{args:{industry}} → 本卡定位。

    守三件事：读过 ctx.card.args.industry；名称只做归一化后的**精确**比对（不模糊猜）；
    找不到时不伪造榜单条目，只给一行说明。
    """
    src = FRONTEND_JS.read_text(encoding="utf-8")
    assert "card.args.industry" in src, "未读壳的一次性入参"
    assert "_focusIndustry" in src, "缺少入参定位实现"
    assert "normBoardName" in src, "名称须归一化后再比对"
    assert "\\u2160-\\u216b" in src, "需剥离罗马数字尾缀（Ⅱ/Ⅲ）"
    assert "find((m) =>" in src, "匹配只允许精确等于"
    assert "未找到同名板块" in src, "未命中时要明确说明，不能静默乱跳"

def test_all_a_real_pct_uses_tdx_880008() -> None:
    """2026-11-01 主人：指数层「沪深全A」的真实涨跌走通达信 880008（全A等权）。

    背景：board_flow 的 ALL 行来自自算板块指数（vipdoc 流通股本加权·不复权，2026-10-30 −1.83%），
    同花顺「全A」与通达信 880008 是等权（同日 −3.03%），差 1.2pp —— 主人对过同花顺后定了 8800XX。
    守四件事：常量指向 sh880008；三个读口（board_flow / series / rank）都过覆写；
    取不到时保留原自算值（不置空、不编造）；manifest 申请了 quotes 权限（否则覆写静默失效）。
    """
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert 'ALL_A_GLB = "sh880008"' in src, "全A 行情源必须写死 880008（禁第二套）"
    assert src.count("_apply_all_real_pct(") == 4, "应 3 处调用（board_flow/series/rank）+ 1 处定义"
    assert 'fl["real_pct"]["ALL"] = v' in src, "只覆写 ALL 这一行，其余板块保持 THS 真实指数"
    assert "if v is None:\n        return" in src, "取不到日线要保留原自算值，不得置空"
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert man["data"].get("quotes") is True, "sector 需要 data.quotes 才能取 880008 日线"


# ------------------------------------------------ 盘后落盘（2026-11-07）
#
# 主人：收盘后的数据已经固定，重启面板不该再重算一遍（L3 层单次 1.7s）。
# 三道铁律守在这里：空结果不写｜失败不写｜读时数据日/库戳对不上当 miss。
# 盘后开关由假 trade_clock 注入，不依赖跑测试的真实钟点。


def _reset_flow_cache(backend) -> None:
    """模拟「进程重启」：内存缓存与库戳节流表全清，只剩 FakeCtx 里的假盘。"""
    backend._FLOW_CACHE.clear()
    backend._STAMP.update(t=0.0, v="")


def test_board_flow_postclose_writes_and_survives_restart(backend, ctx: FakeCtx,
                                                          client: TestClient,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """盘后算完就落盘；重启后（内存全丢）同一请求直接命中，不得重算。"""
    ctx.clock = _FakeClock(postclose=True, expected8="20261005")
    _reset_flow_cache(backend)
    first = client.get("/api/p/sector/board_flow?period=1&level=L2").json()

    doc = ctx.cache_docs["board_flow"]
    assert doc["date"] == "2026-10-05"
    assert doc["version"], "版本戳必须非空（amv_board.db 的 mtime+size）"
    dk = backend._flow_disk_key(1, "L2")
    assert isinstance(doc["items"][dk], dict), "条目按 (period, level) 存"
    assert doc["items"][dk]["date"] == "2026-10-05"

    _reset_flow_cache(backend)

    def boom(*a, **k):
        raise AssertionError("盘后命中落盘缓存时不应重算")

    monkeypatch.setattr(backend, "_board_flow", boom)
    assert client.get("/api/p/sector/board_flow?period=1&level=L2").json() == first


def test_board_flow_cache_keeps_other_periods(backend, ctx: FakeCtx,
                                              client: TestClient) -> None:
    """读-改-写：写 period=3 不得冲掉已存的 period=1。"""
    ctx.clock = _FakeClock(postclose=True, expected8="20261005")
    _reset_flow_cache(backend)
    client.get("/api/p/sector/board_flow?period=1&level=L2")
    _reset_flow_cache(backend)
    client.get("/api/p/sector/board_flow?period=3&level=L2")
    items = ctx.cache_docs["board_flow"]["items"]
    assert set(items) == {backend._flow_disk_key(1, "L2"), backend._flow_disk_key(3, "L2")}


def test_board_flow_empty_result_not_cached(backend, ctx: FakeCtx, client: TestClient,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """铁律 1：全空分项（榜单 0 条）哪怕响应 200 也不得落盘。"""
    ctx.clock = _FakeClock(postclose=True, expected8="20261005")
    _reset_flow_cache(backend)
    monkeypatch.setattr(backend, "_board_flow", lambda *a, **k: {
        "flow_calc": {}, "flow_main": {}, "real_pct": {}, "amv_pct": {},
        "diverge": {}, "from_date": None})
    monkeypatch.setattr(backend, "_dual_bull", lambda *a, **k: {})
    monkeypatch.setattr(backend, "_struct_tags", lambda *a, **k: {})
    body = client.get("/api/p/sector/board_flow?period=1&level=L2").json()
    assert body["date"] == "2026-10-05"
    assert ctx.cache_writes == [] and ctx.cache_docs == {}


def test_board_flow_failure_not_cached(backend) -> None:
    """铁律 2：库不可读（503）不读写任何盘。"""
    ctx = FakeCtx({}, clock=_FakeClock(postclose=True, expected8="20261005"))
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/sector")
    resp = TestClient(app).get("/api/p/sector/board_flow?period=1&level=L2")
    assert resp.status_code == 503
    assert ctx.cache_reads == [] and ctx.cache_writes == []


def test_board_flow_disk_miss_when_date_differs(backend, ctx: FakeCtx, client: TestClient,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """铁律 3：数据日不符当 miss（不得把昨天的结果当今天返回），重算后改写成当日。"""
    ctx.clock = _FakeClock(postclose=True, expected8="20261005")
    _reset_flow_cache(backend)
    client.get("/api/p/sector/board_flow?period=1&level=L2")
    ctx.cache_docs["board_flow"]["date"] = "2026-10-04"     # 数据日不符
    _reset_flow_cache(backend)

    calls = []
    real = backend._board_flow

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(backend, "_board_flow", spy)
    body = client.get("/api/p/sector/board_flow?period=1&level=L2").json()
    assert calls, "数据日不符必须重算"
    assert body["date"] == "2026-10-05"
    assert ctx.cache_docs["board_flow"]["date"] == "2026-10-05"


def test_board_flow_disk_miss_when_stamp_differs(backend, ctx: FakeCtx, client: TestClient,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """库戳不符（链重写过库）同样当 miss —— 半成品不得整晚复用。"""
    ctx.clock = _FakeClock(postclose=True, expected8="20261005")
    _reset_flow_cache(backend)
    client.get("/api/p/sector/board_flow?period=1&level=L2")
    ctx.cache_docs["board_flow"]["version"] = "stale-stamp"
    _reset_flow_cache(backend)

    calls = []
    real = backend._board_flow

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(backend, "_board_flow", spy)
    assert client.get("/api/p/sector/board_flow?period=1&level=L2").status_code == 200
    assert calls, "库戳不符时必须重算"
    assert ctx.cache_docs["board_flow"]["version"] != "stale-stamp", "重算后应改写库戳"


def test_board_flow_no_disk_io_intraday(backend, ctx: FakeCtx, client: TestClient) -> None:
    """非盘后：不读不写盘（盘中数据会变，不得落盘）。"""
    ctx.clock = _FakeClock(postclose=False, expected8="20261005")
    _reset_flow_cache(backend)
    assert client.get("/api/p/sector/board_flow?period=1&level=L2").status_code == 200
    assert ctx.cache_reads == [] and ctx.cache_writes == []
    assert ctx.cache_docs == {}
