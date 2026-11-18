# -*- coding: utf-8 -*-
"""compat_watchlist 插件契约测试。

覆盖（全部用 tmp 目录，**不碰真生产文件**）：

1. manifest：`backend.mount == "root"`、`data.write == ["watchlist"]`、白名单全在册；
2. 路由：12 条根路径，且都不自带 `/api` 前缀（core 负责拼）；
3. 4 个读端点：字段形状与旧 8800 一致（`_watchlist_payload` / `groups` / `state` / `search`）；
4. 5 类写操作：落盘内容正确、**备份存在**、**审计有记录**（假 ctx 逐条复刻 core `panel_write`）；
5. **未知字段原样保留**：写前写后键集合一致（顶层 / meta / 条目 / 分组四级）；
6. 静态禁令：无直连库、无自连行情源、无 HTTP/子进程、无自己写文件。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
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
from app.core import paths as core_paths  # noqa: E402
from app.plugins.compat_watchlist import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN_DIR / "plugin.json"
BACKEND_PY = PLUGIN_DIR / "backend.py"

# 旧 8800 `/api/watchlist.json` 顶层键（`_watchlist_payload` 逐字）
OLD_WL_TOP = {"meta", "updated_at", "quote_asof", "mode", "groups", "stats", "items"}
OLD_WL_META = {"schema_version", "document_type", "generated_at", "source"}
OLD_WL_GROUP = {"id", "name", "kind", "order", "count"}
OLD_WL_STOCK = {"kind", "code", "name", "added_at", "order", "group",
                "quote", "quote_source", "regime", "sector", "div", "flow", "badges"}
OLD_WL_SECTOR = {"kind", "code", "name", "added_at", "order", "group",
                 "level", "regime", "pct", "quote_date"}
OLD_WL_STATS = {"total", "stocks", "sectors", "bull"}

EXPECTED_PATHS = {
    "/watchlist.json", "/watchlist/groups", "/watchlist/state", "/watchlist/search",
    "/watchlist/add", "/watchlist/remove", "/watchlist/move", "/watchlist/reorder",
    "/watchlist/group/add", "/watchlist/group/remove",
    "/watchlist/group/rename", "/watchlist/group/reorder",
}


# ---------------------------------------------------------------- 假行情门面


class _Bar:
    def __init__(self, day: str, close: float):
        self.time = datetime.strptime(day, "%Y%m%d")
        self.close = close


class _FakeQuotes:
    """形状与 `app/core/quotes.py` 一致（normalize + daily_bars）。"""

    def __init__(self, daily: Optional[Dict[str, List[_Bar]]] = None, boom: bool = False):
        self._daily = daily or {}
        self._boom = boom

    def normalize(self, code: str) -> Optional[str]:
        from app.core.quotes import normalize
        return normalize(code)

    def daily_bars(self, glb: str, count: int = 8, adjust: Optional[str] = None):
        if self._boom:
            raise RuntimeError("行情源不可达")
        return list(self._daily.get(glb, []))[-count:]


# ---------------------------------------------------------------- 假 ctx（复刻 core panel_write 语义）


class FakeCtx:
    """只实现本插件声明用到的 ctx 面。

    `panel_write` **逐条复刻 core `PluginContext.panel_write`** 的四步：
    备份到 `backups/<name>/<时间戳>.json` → 写 `.tmp` → `os.replace` → 追加 `audit.log`。
    （core 的实现在真生产路径上，测试不能跑；这里验的是插件侧调用契约。）
    """

    id = "compat_watchlist"

    def __init__(self, tmp_path: Path, *, doc: Optional[Dict[str, Any]] = None,
                 quotes: Any = None, quotes_doc: Optional[Dict[str, Any]] = None,
                 names: Optional[Dict[str, str]] = None, etfs: Optional[Dict[str, str]] = None,
                 amv_db: Optional[Path] = None,
                 ths_db: Optional[Path] = None, sig_db: Optional[Path] = None,
                 allow_write: bool = True, corrupt: bool = False) -> None:
        self.root = tmp_path
        self.panel_dir = tmp_path / "panel"
        self.panel_dir.mkdir(parents=True, exist_ok=True)
        self.wl_path = self.panel_dir / "watchlist.json"
        self.backup_dir = tmp_path / "backups" / "watchlist"
        self.panel_write_calls: List[Dict[str, Any]] = []
        # 2026-11-07 富化磁盘缓存：同 REAL 路径形状（xiaoshi_cache/），但不碰真文件
        self.enrich_cache_path = tmp_path / "xiaoshi_cache" / "watchlist_enrich_cache.json"
        self.misc_writes: List[Dict[str, Any]] = []
        self._quotes = quotes if quotes is not None else _FakeQuotes()
        self._dbs = {"amv_board.db": amv_db, "ths_board.db": ths_db, "buy_signal.db": sig_db}
        self._names = names or {}
        self._etfs = etfs or {}
        self._allow_write = allow_write
        if corrupt:
            self.wl_path.write_text("{not json", encoding="utf-8")
        elif doc is not None:
            self.wl_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        if quotes_doc is not None:
            (self.panel_dir / "watchlist_quotes.json").write_text(
                json.dumps(quotes_doc, ensure_ascii=False), encoding="utf-8")

    # -- 读 --
    def panel_json(self, name: str) -> Dict[str, Any]:
        path = {"watchlist": self.wl_path,
                "watchlist_quotes": self.panel_dir / "watchlist_quotes.json"}.get(name)
        if path is None:
            raise PermissionError("%s 不在只读 panel 白名单内" % name)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("panel JSON 顶层必须是对象")
        return data

    def json_read(self, name: str) -> Any:
        if name == "stock_names":
            return {"date": "2026-09-20", "names": dict(self._names), "etfs": dict(self._etfs)}
        if name == "watchlist_enrich_cache":
            if not self.enrich_cache_path.is_file():
                raise FileNotFoundError(str(self.enrich_cache_path))
            return json.loads(self.enrich_cache_path.read_text(encoding="utf-8"))
        raise PermissionError(name)

    def db_read(self, name: str):
        path = self._dbs.get(name)
        if path is None:
            raise FileNotFoundError(name)
        conn = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def lib(self, name: str):
        if name != "amv_regime":
            raise PermissionError(name)
        import importlib.util
        path = core_paths.lib_path(name)
        spec = importlib.util.spec_from_file_location("_t_amv_regime", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_t_amv_regime"] = mod
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

    @property
    def quotes(self):
        return self._quotes

    # -- 写（复刻 core 四步）--
    def misc_write(self, name: str, payload: Dict[str, Any]) -> Path:
        """复刻 core `misc_write`（原子替换；备份/审计对缓存无意义，测试只看落盘内容）。"""
        if name != "watchlist_enrich_cache":
            raise PermissionError(name)
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是 dict")
        self.enrich_cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.enrich_cache_path.with_name(self.enrich_cache_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.enrich_cache_path)
        self.misc_writes.append(payload)
        return self.enrich_cache_path

    def panel_write(self, name: str, payload: Dict[str, Any]) -> Path:
        if not self._allow_write:
            raise PermissionError("插件未在 manifest.data.write 声明 %s" % name)
        if name != "watchlist":
            raise PermissionError("%s 不在可写白名单内" % name)
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是 dict")
        self.panel_write_calls.append(payload)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if self.wl_path.is_file():
            shutil.copyfile(self.wl_path, self.backup_dir / ("%s.json" % stamp))
        tmp = self.wl_path.with_name(self.wl_path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
        tmp.replace(self.wl_path)
        with (self.backup_dir / "audit.log").open("a", encoding="utf-8") as fh:
            fh.write("%s\tplugin=%s\tfile=%s\tbytes=%d\n"
                     % (stamp, self.id, name, self.wl_path.stat().st_size))
        return self.wl_path

    # -- 断言辅助 --
    def doc(self) -> Dict[str, Any]:
        return json.loads(self.wl_path.read_text(encoding="utf-8"))

    def backups(self) -> List[Path]:
        return sorted(self.backup_dir.glob("*.json")) if self.backup_dir.is_dir() else []

    def audit_lines(self) -> List[str]:
        log = self.backup_dir / "audit.log"
        if not log.is_file():
            return []
        return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _client(ctx: FakeCtx) -> TestClient:
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------- 样本文档


def _base_doc() -> Dict[str, Any]:
    """贴近真实 `data/panel/watchlist.json`，并**故意塞进未知字段**（四级各一处）。"""
    return {
        "meta": {
            "schema_version": "1.0.0",
            "document_type": "watchlist",
            "updated_at": "2026-10-22 00:47:07",
            "source": "server",
            "written_by": "some_other_module",       # ← 未知 meta 键（别的模块写的）
        },
        "groups": [
            {"id": "stock_default", "name": "自选", "kind": "stock", "order": 0},
            {"id": "3169210f", "name": "追踪", "kind": "stock", "order": 1,
             "color": "#ff0"},                        # ← 未知分组键
            {"id": "sector_default", "name": "板块", "kind": "sector", "order": 100},
        ],
        "items": [
            {"kind": "stock", "code": "603629.SH", "name": "利通电子",
             "added_at": "2026-10-22 00:47:07", "order": 0, "group": "stock_default",
             "note": "旧字段"},                        # ← 未知条目键
            {"kind": "stock", "code": "300750.SZ", "name": "宁德时代",
             "added_at": "2026-10-21 15:46:26", "order": 1, "group": "3169210f"},
            {"kind": "sector", "code": "881166.TI", "name": "军工装备",
             "added_at": "2026-10-18 13:28:56", "order": 2, "group": "sector_default"},
        ],
        "schema_extra": {"keep": True},                # ← 未知顶层键
    }


def _mk_amv_db(tmp_path: Path) -> Path:
    db = tmp_path / "amv_board.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE stock_amv(date TEXT, stock TEXT, amv REAL)")
    conn.execute("CREATE TABLE stock_state(date TEXT, stock TEXT, alive REAL)")
    conn.execute("CREATE TABLE sector_daily(date TEXT, level TEXT, code TEXT, amv REAL)")
    conn.execute("CREATE TABLE sector_meta(level TEXT, code TEXT, name TEXT, ord INTEGER)")
    conn.execute("CREATE TABLE board_flow(date TEXT, board_code TEXT, pct REAL)")
    conn.execute("CREATE TABLE stock_flow(date TEXT, stock_code TEXT, main_flow REAL)")
    for i in range(8):
        d = "2026-10-%02d" % (1 + i)
        conn.execute("INSERT INTO stock_amv VALUES(?,?,?)", (d, "603629", 1e10 + i * 1e8))
        conn.execute("INSERT INTO stock_state VALUES(?,?,?)", (d, "603629", 1e8))
        conn.execute("INSERT INTO sector_daily VALUES(?,?,?,?)", (d, "L2", "881166.TI", 5e9 + i * 1e8))
    conn.execute("INSERT INTO sector_meta VALUES('L2','881166.TI','军工装备',1)")
    conn.execute("INSERT INTO board_flow VALUES('2026-10-08','881166.TI',0.0123)")
    conn.execute("INSERT INTO stock_flow VALUES('2026-10-08','603629',12345.0)")
    conn.commit()
    conn.close()
    return db


def _mk_ths_db(tmp_path: Path) -> Path:
    db = tmp_path / "ths_board.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE board(code TEXT, name TEXT, sw_level INTEGER)")
    conn.execute("CREATE TABLE board_member(stock TEXT, board_code TEXT)")
    conn.execute("INSERT INTO board VALUES('881166.TI','军工装备',2)")
    conn.execute("INSERT INTO board_member VALUES('603629','881166.TI')")
    conn.commit()
    conn.close()
    return db


def _mk_sig_db(tmp_path: Path) -> Path:
    db = tmp_path / "buy_signal.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE stock_signal(stock TEXT, daily_sigs TEXT, weekly_sigs TEXT)")
    conn.execute("INSERT INTO stock_signal VALUES('603629.SH','B1_W','周')")
    # 2026-11-11 ETF 买点（run_etf.py 产）：与个股分表存，徽标查不到个股时回落这张
    conn.execute("CREATE TABLE etf_signal(etf TEXT, daily_date TEXT, daily_sigs TEXT)")
    conn.execute("INSERT INTO etf_signal VALUES('510300.SH','20261111','ZHU_W,B1_Y')")
    conn.commit()
    conn.close()
    return db


def _ctx(tmp_path: Path, **kwargs) -> FakeCtx:
    quotes = kwargs.pop("quotes", _FakeQuotes({
        "sh603629": [_Bar("20261007", 10.0), _Bar("20261008", 11.0)],
    }))
    return FakeCtx(tmp_path, quotes=quotes,
                   amv_db=_mk_amv_db(tmp_path),
                   ths_db=_mk_ths_db(tmp_path),
                   sig_db=_mk_sig_db(tmp_path),
                   names=kwargs.pop("names", {"603629": "利通电子", "300750": "宁德时代",
                                               "000001": "平安银行", "881166": "军工装备"}),
                   **kwargs)


@pytest.fixture(autouse=True)
def _clear_cache():
    backend._reset_cache()
    backend._reset_disk_state()
    yield
    backend._reset_cache()
    backend._reset_disk_state()


# ---------------------------------------------------------------- 1. manifest


def test_manifest_mounts_at_root_and_declares_write() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    m = validate_manifest(raw, PLUGIN_DIR)
    assert m.id == "compat_watchlist"
    assert m.backend.mount == "root", "旧前端请求 /api/watchlist*，必须挂到 /api"
    assert m.data_write == ("watchlist",), "写白名单必须正好是 watchlist"
    assert "watchlist" in core_paths.WRITABLE_PANEL
    assert sorted(m.data_read) == ["amv_board.db", "buy_signal.db", "ths_board.db"]
    assert sorted(m.panel_read) == ["watchlist", "watchlist_quotes"]
    assert m.json_read == ("stock_names", "watchlist_enrich_cache")
    assert m.lib_read == ("amv_regime",)
    assert m.quotes_read is True


def test_manifest_json_data_block_shape() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    blk = data["data"]
    # 读白名单：watchlist（条目）+ watchlist_quotes（行情拼接）；写白名单只有 watchlist。
    assert sorted(blk["panel"]) == ["watchlist", "watchlist_quotes"]
    assert blk["write"] == ["watchlist"]
    assert blk["read"] == ["amv_board.db", "ths_board.db", "buy_signal.db"]
    assert sorted(blk["json"]) == ["stock_names", "watchlist_enrich_cache"]
    assert blk["misc"] == ["watchlist_enrich_cache"]


# ---------------------------------------------------------------- 2. 路由


def test_register_exposes_twelve_root_paths(tmp_path) -> None:
    router = backend.register(_ctx(tmp_path))
    assert isinstance(router, APIRouter)
    assert {r.path for r in router.routes} == EXPECTED_PATHS
    for route in router.routes:
        assert not route.path.startswith("/api"), "router 路径不得自带 /api 前缀"


def test_write_routes_are_post_only(tmp_path) -> None:
    router = backend.register(_ctx(tmp_path))
    methods = {(r.path, m) for r in router.routes for m in getattr(r, "methods", set())}
    for path in ("/watchlist/add", "/watchlist/remove", "/watchlist/move",
                 "/watchlist/reorder", "/watchlist/group/add", "/watchlist/group/remove",
                 "/watchlist/group/rename", "/watchlist/group/reorder"):
        assert (path, "POST") in methods, path
        assert (path, "GET") not in methods, "写端点不得暴露 GET：%s" % path
    # search 两个方法都接（旧前端 POST，任务书表里写 GET）
    assert ("/watchlist/search", "GET") in methods
    assert ("/watchlist/search", "POST") in methods


# ---------------------------------------------------------------- 3. 读端点形状


def test_watchlist_json_shape_matches_old_8800(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    body = _client(ctx).get("/api/watchlist.json").json()
    assert set(body) == OLD_WL_TOP, set(body) ^ OLD_WL_TOP
    assert set(body["meta"]) >= OLD_WL_META
    assert set(body["stats"]) == OLD_WL_STATS
    assert body["mode"] == "eod"
    assert body["stats"]["total"] == 3 and body["stats"]["stocks"] == 2
    assert body["stats"]["sectors"] == 1
    for g in body["groups"]:
        assert OLD_WL_GROUP <= set(g), set(g) ^ OLD_WL_GROUP
    stocks = [x for x in body["items"] if x["kind"] == "stock"]
    sectors = [x for x in body["items"] if x["kind"] == "sector"]
    for row in stocks:
        assert set(row) == OLD_WL_STOCK, set(row) ^ OLD_WL_STOCK
    for row in sectors:
        assert set(row) == OLD_WL_SECTOR, set(row) ^ OLD_WL_SECTOR
    # EOD 行情来自 daily_bars 末两根
    hit = next(x for x in stocks if x["code"] == "603629.SH")
    assert hit["quote"]["price"] == 11.0
    assert hit["quote"]["change_pct"] == 10.0
    assert hit["quote_source"] == "eod"
    assert hit["quote"]["asof"] == "2026-10-08"
    assert hit["badges"] and hit["badges"][0]["label"] == "B1"
    assert hit["sector"]["l2"]["code"] == "881166.TI"
    assert hit["flow"] == 1.23                       # 万元 → 亿元
    # 无行情票 → quote=None（不补 0）
    miss = next(x for x in stocks if x["code"] == "300750.SZ")
    assert miss["quote"] is None


def test_watchlist_json_live_mode_marks_source(tmp_path) -> None:
    quotes_doc = {"asof": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "quotes": {"603629.SH": {"price": 12.5, "change_pct": 2.1}}}
    ctx = _ctx(tmp_path, doc=_base_doc(), quotes_doc=quotes_doc)
    body = _client(ctx).get("/api/watchlist.json?mode=live").json()
    assert body["mode"] == "live"
    hit = next(x for x in body["items"] if x.get("code") == "603629.SH")
    assert hit["quote_source"] == "live"
    assert hit["quote"] == {"price": 12.5, "change_pct": 2.1,
                            "source": "tick_feed", "asof": quotes_doc["asof"]}


def test_watchlist_json_missing_file_degrades_to_empty(tmp_path) -> None:
    """旧端 `_read_json` 容错：文件缺失 → 空池 200，不 500。"""
    ctx = _ctx(tmp_path)                             # 不写 watchlist.json
    body = _client(ctx).get("/api/watchlist.json").json()
    assert body["items"] == []
    assert [g["id"] for g in body["groups"]] == ["stock_default", "sector_default"]
    assert body["stats"]["total"] == 0


def test_watchlist_json_corrupt_file_degrades_to_empty(tmp_path) -> None:
    ctx = _ctx(tmp_path, corrupt=True)
    body = _client(ctx).get("/api/watchlist.json").json()
    assert body["items"] == []


def test_groups_shape(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    body = _client(ctx).get("/api/watchlist/groups").json()
    assert body["ok"] is True
    assert [g["id"] for g in body["groups"]] == ["stock_default", "3169210f", "sector_default"]
    assert OLD_WL_GROUP - {"count"} <= set(body["groups"][0])


def test_state_normalizes_code_like_add(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    # 6 位码 → ts_code 命中
    body = client.get("/api/watchlist/state?kind=stock&code=603629").json()
    assert body["watched"] is True
    assert body["code"] == "603629.SH"
    assert set(body) == {"ok", "kind", "code", "watched", "ts"}
    # 未命中 → false（不编造）
    assert client.get("/api/watchlist/state?kind=stock&code=600000").json()["watched"] is False
    # 板块：大写原样
    sec = client.get("/api/watchlist/state?kind=sector&code=881166.ti").json()
    assert sec["watched"] is True and sec["code"] == "881166.TI"
    # 空码 → false
    assert client.get("/api/watchlist/state?code=").json()["watched"] is False


def test_search_get_and_post_same_shape(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    for body in (client.get("/api/watchlist/search?q=603629").json(),
                 client.post("/api/watchlist/search", json={"q": "603629"}).json()):
        assert set(body) == {"q", "stocks", "sectors"}
        assert body["q"] == "603629"
        assert body["stocks"] and body["stocks"][0]["code"] == "603629.SH"
        assert set(body["stocks"][0]) == {"code", "name", "type"}
        assert body["stocks"][0]["type"] == "stock"
    hit = client.post("/api/watchlist/search", json={"q": "军工"}).json()
    assert hit["sectors"] == [{"code": "881166.TI", "name": "军工装备", "level": "L2"}]
    empty = client.post("/api/watchlist/search", json={"q": ""}).json()
    assert empty == {"q": "", "stocks": [], "sectors": []}


def test_search_excludes_etf_and_index_segments(tmp_path) -> None:
    """`names` 段仍是纯个股名单：即使里面有 ETF/指数码，也不得被当成个股搜出来。

    2026-11-11 起 ETF 走单独的 `etfs` 段（见下一条测试），本条锁的是「两段不混」。
    """
    ctx = _ctx(tmp_path, doc=_base_doc())
    ctx._names = {"510300": "沪深300ETF", "000001": "平安银行", "603629": "利通电子"}
    body = _client(ctx).get("/api/watchlist/search?q=0").json()
    codes = [x["code"] for x in body["stocks"]]
    assert "510300.SH" not in codes, "ETF 不得从个股名单里冒充个股"
    assert "000001.SZ" in codes


def test_search_includes_etf_from_etf_segment(tmp_path) -> None:
    """ETF 段（2026-11-11）：搜代码/名称都能命中，且 type=etf；货币/债券 ETF 也可搜。"""
    ctx = _ctx(tmp_path, doc=_base_doc())
    ctx._etfs = {"510300": "沪深300ETF华泰柏", "159915": "创业板ETF易方达",
                 "511010": "国债ETF国泰", "500001": "老封闭基金（非 ETF）",
                 "519999": "场内开放基金（非 ETF）", "160123": "LOF（非 ETF）"}
    client = _client(ctx)
    by_name = client.post("/api/watchlist/search", json={"q": "沪深300"}).json()
    etf_hits = [x for x in by_name["stocks"] if x["code"] == "510300.SH"]
    assert etf_hits and etf_hits[0]["type"] == "etf"
    by_code = client.post("/api/watchlist/search", json={"q": "159915"}).json()
    assert [x["code"] for x in by_code["stocks"]] == ["159915.SZ"]
    assert by_code["stocks"][0]["type"] == "etf"
    # 511（货币/国债 ETF 段）算 ETF
    assert [x["code"] for x in client.post("/api/watchlist/search", json={"q": "511010"}).json()["stocks"]] == ["511010.SH"]
    # 500（老封闭基金）/519（场内开放基金）/16x（LOF）不在 ETF 白名单
    for bad in ("500001", "519999", "160123"):
        got = [x["code"] for x in client.post("/api/watchlist/search", json={"q": bad}).json()["stocks"]]
        assert bad + "." not in " ".join(got), "%s 不该被当 ETF 搜出" % bad


def test_add_accepts_sh_and_sz_etf(tmp_path) -> None:
    """ETF 加自选：沪 5xxxxx 与深 **159xxx** 都要归一成 ts_code（旧 `_to_ts_code` 对 159 返回 None）。"""
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    for raw, want in (("510300", "510300.SH"), ("159915", "159915.SZ"),
                      ("159915.SZ", "159915.SZ"), ("588000", "588000.SH")):
        body = client.post("/api/watchlist/add", json={"kind": "stock", "code": raw,
                                                         "name": "ETF"}).json()
        assert body["ok"] is True
        codes = [x["code"] for x in ctx.doc()["items"]]
        assert want in codes, "%s 应归一为 %s，实际 %s" % (raw, want, codes)
    # 归类仍走 stock 分组（kind=stock）：ETF 不是 sector，不新造 kind
    st = client.get("/api/watchlist/state?code=159915").json()
    assert st == {"ok": True, "kind": "stock", "code": "159915.SZ", "watched": True, "ts": st["ts"]}


# ---------------------------------------------------------------- 4/5. 写操作 + 未知字段保留


def _assert_unknown_kept(ctx: FakeCtx) -> None:
    """写后键集合必须与写前样本一致（四级：顶层 / meta / 条目 / 分组）。"""
    before, after = _base_doc(), ctx.doc()
    assert set(after) >= set(before), "顶层未知键被丢：%s" % (set(before) - set(after))
    assert after["schema_extra"] == {"keep": True}
    assert set(after["meta"]) >= set(before["meta"]), "meta 未知键被丢"
    assert after["meta"]["written_by"] == "some_other_module"
    kept_item = next(x for x in after["items"] if x["code"] == "603629.SH")
    assert kept_item["note"] == "旧字段", "条目未知键被丢"
    kept_group = next(g for g in after["groups"] if g["id"] == "3169210f")
    assert kept_group["color"] == "#ff0", "分组未知键被丢"


def test_add_writes_full_doc_backup_and_audit(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "600000", "name": "浦发银行", "kind": "stock"}).json()
    assert body == {"ok": True, "action": "added", "kind": "stock",
                    "code": "600000.SH", "group": "stock_default", "total": 4,
                    # 2026-11-13：「加入选股」要用到组名与清理回执，add 回执多带三个键
                    "group_name": "自选", "created_group": "", "pruned": []}
    doc = ctx.doc()
    new = next(x for x in doc["items"] if x["code"] == "600000.SH")
    assert new["name"] == "浦发银行" and new["group"] == "stock_default"
    assert new["order"] == 0, "新条目排最前（order 取最小−1 后被重编号为 0）"
    assert [x["order"] for x in doc["items"]] == list(range(4)), "落盘按 order 重编号"
    # 写通道三件套
    assert len(ctx.panel_write_calls) == 1, "一次请求只整份写一次"
    assert ctx.backups(), "写前必须留备份"
    assert ctx.audit_lines() and "plugin=compat_watchlist" in ctx.audit_lines()[-1]
    assert json.loads(ctx.backups()[-1].read_text(encoding="utf-8")) == _base_doc()
    _assert_unknown_kept(ctx)


def test_add_is_idempotent_upsert(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    first = client.post("/api/watchlist/add", json={"code": "603629.SH", "name": "利通电子"}).json()
    assert first["action"] == "exists" and first["total"] == 3
    after = client.post("/api/watchlist/add",
                        json={"code": "603629", "name": "利通电子2"}).json()
    assert after["action"] == "exists"
    doc = ctx.doc()
    hits = [x for x in doc["items"] if x["code"] == "603629.SH"]
    assert len(hits) == 1 and hits[0]["name"] == "利通电子2"


def test_add_rejects_bad_code_and_unknown_group_without_writing(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    before = ctx.wl_path.read_text(encoding="utf-8")
    assert client.post("/api/watchlist/add", json={"code": "abc"}).status_code == 422
    assert client.post("/api/watchlist/add",
                       json={"code": "600000", "group": "nope"}).status_code == 422
    assert client.post("/api/watchlist/add",
                       json={"code": "881166.TI", "kind": "sector",
                             "group": "stock_default"}).status_code == 422
    assert ctx.panel_write_calls == [], "校验不过不许写"
    assert ctx.wl_path.read_text(encoding="utf-8") == before


def test_add_sector_keeps_code_uppercased(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "881101.ti", "kind": "sector"}).json()
    assert body["code"] == "881101.TI" and body["group"] == "sector_default"
    assert body["kind"] == "sector"


def test_remove_is_idempotent(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    body = client.post("/api/watchlist/remove", json={"code": "300750"}).json()
    assert body == {"ok": True, "removed": 1, "total": 2}
    again = client.post("/api/watchlist/remove", json={"code": "300750"}).json()
    assert again == {"ok": True, "removed": 0, "total": 2}
    assert ctx.backups() and ctx.audit_lines()
    _assert_unknown_kept(ctx)


def test_move_requires_matching_kind_and_group(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    ok = client.post("/api/watchlist/move",
                     json={"code": "603629.SH", "group": "3169210f"}).json()
    assert ok == {"ok": True, "code": "603629.SH", "from": "stock_default", "group": "3169210f"}
    hit = next(x for x in ctx.doc()["items"] if x["code"] == "603629.SH")
    assert hit["group"] == "3169210f"
    # 跨类型 422、未知组 422、不存在 404，且都不写
    calls = len(ctx.panel_write_calls)
    assert client.post("/api/watchlist/move",
                       json={"code": "603629.SH", "group": "sector_default"}).status_code == 422
    assert client.post("/api/watchlist/move",
                       json={"code": "603629.SH", "group": "nope"}).status_code == 422
    assert client.post("/api/watchlist/move",
                       json={"code": "600000.SH", "group": "3169210f"}).status_code == 404
    assert len(ctx.panel_write_calls) == calls
    _assert_unknown_kept(ctx)


def test_reorder_global_and_scoped(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    body = client.post("/api/watchlist/reorder",
                       json={"codes": ["603629.SH", "300750.SZ"], "group": "stock_default"}).json()
    assert body == {"ok": True, "total": 3}
    doc = ctx.doc()
    assert [x["order"] for x in doc["items"]] == list(range(3))
    # 只要求「列出的 codes 都在、且都在未列出的之前」；同 order 的内部先后不保证
    seq_codes = ["603629.SH", "300750.SZ"]
    codes_now = [x["code"] for x in doc["items"]]
    assert set(seq_codes) <= set(codes_now), "重排后不能丢条目"
    first_unlisted = min((i for i, c in enumerate(codes_now) if c not in seq_codes), default=len(codes_now))
    assert all(codes_now.index(c) < first_unlisted for c in seq_codes), "列出的条目应排在未列出的之前"
    # 未列出的条目保持相对位置排在已列出的之后
    client.post("/api/watchlist/reorder", json={"codes": []})
    doc = ctx.doc()
    assert [x["order"] for x in doc["items"]] == list(range(3))
    _assert_unknown_kept(ctx)


def test_group_add_rename_remove(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    added = client.post("/api/watchlist/group/add",
                        json={"name": "观察", "kind": "stock"}).json()
    assert added["ok"] is True and added["action"] == "added"
    gid = added["group"]["id"]
    assert len(gid) == 8 and added["group"]["kind"] == "stock"
    # 幂等：同类型同名 → exists
    again = client.post("/api/watchlist/group/add",
                        json={"name": "观察", "kind": "stock"}).json()
    assert again["action"] == "exists" and again["group"]["id"] == gid
    # 改名
    ren = client.post("/api/watchlist/group/rename",
                      json={"id": gid, "name": "观察2"}).json()
    assert ren["group"]["name"] == "观察2" and ren["group"]["id"] == gid
    # 默认组拒绝改名/删除
    assert client.post("/api/watchlist/group/rename",
                       json={"id": "stock_default", "name": "x"}).status_code == 422
    assert client.post("/api/watchlist/group/remove",
                       json={"id": "sector_default"}).status_code == 422
    # 删自定义组：组内条目移入同类型默认组
    client.post("/api/watchlist/move", json={"code": "300750.SZ", "group": gid})
    removed = client.post("/api/watchlist/group/remove", json={"id": gid}).json()
    assert removed == {"ok": True, "moved": 1, "target": "stock_default"}
    doc = ctx.doc()
    assert gid not in [g["id"] for g in doc["groups"]]
    assert next(x for x in doc["items"] if x["code"] == "300750.SZ")["group"] == "stock_default"
    assert ctx.audit_lines(), "每次写都要留审计"
    _assert_unknown_kept(ctx)


def test_group_add_requires_kind_and_name(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    assert client.post("/api/watchlist/group/add", json={"name": "x"}).status_code == 422
    assert client.post("/api/watchlist/group/add",
                       json={"kind": "stock"}).status_code == 422
    assert ctx.panel_write_calls == []


def test_group_reorder_writes_order(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    body = _client(ctx).post("/api/watchlist/group/reorder",
                             json={"ids": ["sector_default", "stock_default", "3169210f"]}).json()
    assert body["ok"] is True
    order = {g["id"]: g["order"] for g in body["groups"]}
    assert order["sector_default"] == 0 and order["stock_default"] == 1
    assert ctx.audit_lines()
    _assert_unknown_kept(ctx)


def test_write_refused_when_manifest_lacks_write(tmp_path) -> None:
    """core 的 PermissionError 必须转成 500，且**文件不被改**。"""
    ctx = _ctx(tmp_path, doc=_base_doc(), allow_write=False)
    before = ctx.wl_path.read_text(encoding="utf-8")
    resp = _client(ctx).post("/api/watchlist/add", json={"code": "600000"})
    assert resp.status_code == 500
    assert "data.write" in resp.json()["detail"]
    assert ctx.wl_path.read_text(encoding="utf-8") == before


def test_write_refused_when_doc_unreadable(tmp_path) -> None:
    """读失败就不写（防拿空文档覆盖生产文件）。"""
    ctx = _ctx(tmp_path, corrupt=True)
    resp = _client(ctx).post("/api/watchlist/add", json={"code": "600000"})
    assert resp.status_code == 503
    assert "拒绝写入" in resp.json()["detail"]
    assert ctx.panel_write_calls == []


def test_atomic_write_leaves_no_tmp_file(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    _client(ctx).post("/api/watchlist/add", json={"code": "600000"})
    assert not (ctx.panel_dir / "watchlist.json.tmp").exists()


# ---------------------------------------------------------------- 6. 静态禁令


def test_no_direct_db_connect() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert find_forbidden_db_usage(src) == [], "禁止直连数据库"


def test_no_self_quote_connection() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert find_forbidden_quote_usage(src) == [], "禁止自己连行情源"


def _code_only(path: Path) -> str:
    """只留代码行：去掉注释与文档字符串，避免“注释里提到 open(”被当成真代码。

    2026-10-23：原实现直接对全文做子串匹配，结果本文件的**说明性注释**里写了
    `open()` / `os.replace` 就误报（这两个词恰好是在解释“为什么不用它们”）。
    改成用 AST 取所有 Name/Attribute/Call，拼成名字序列再判。
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.append(fn.id + "()")
            elif isinstance(fn, ast.Attribute):
                names.append(fn.attr + "()")
    return "\n".join(names)


def test_no_http_subprocess_or_own_file_io() -> None:
    """写文件必须走 ctx.panel_write；插件自己不许开文件句柄/发 HTTP/起子进程。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("import requests", "import httpx", "urllib", "socket", "subprocess"):
        assert token not in src, token
    # 只看真代码（AST 名字序列），不看注释/文档字符串
    code = _code_only(BACKEND_PY)
    for name in ("open()", "dump()", "replace()", "write_text()", "write_bytes()",
                 "rmtree()", "mkdir()"):
        assert name not in code, "出现禁止的自写文件写法: %s" % name


def test_reads_and_writes_only_through_ctx() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    # `ctx.lib` 可能被封在 `_lib()` 辅助里（本插件就是这么写的），所以两种形态都算。
    for token in ("ctx.panel_json", "ctx.panel_write", "ctx.db_read", "ctx.quotes"):
        assert token in src, "应只经 %s 取数/落盘" % token
    assert "ctx.lib" in src or "getter(\"amv_regime\")" in src, "口径库应经 ctx.lib 取"


def test_no_plugin_cross_import() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    for other in ("app.plugins.watchlist", "app.plugins.compat", "app.plugins.sector",
                  "app.plugins.kline", "app.plugins.amv", "app.plugins.battle_plan"):
        assert other not in src, "禁止插件互相 import：%s" % other


# ------------------------------------------------ 8. 买点徽标缓存新鲜度（2026-10-30 补）
#
# 背景：旧仓 `engine/tests/test_sig_cache_freshness.py` 守的是同一个事故——
#   buy_signal.db 更新后徽标不变（301550 显示 B2/动/砖，库里却是空的），
#   根因是缓存键不含库版本签名。那条测试随 8002 工作台退役（2026-10-30），
#   v2 侧没有等价覆盖 → 本组接住。
#
# 口径差异（已知、可接受，但不许丢）：
#   旧端按 buy_signal.db（含 -wal）mtime 签名失效；插件拿不到数据文件路径，
#   改用 TTL 分层失效 ⇒ 库更新后徽标**最长滞后 DB_TTL_S**。


def _badges_of(body: dict, code: str) -> list:
    row = next(x for x in body["items"] if x.get("code") == code)
    return row["badges"]


def test_badges_ttl_layers_are_layered() -> None:
    """缓存分层常量口径：整包 < 行情 < 日更库，且日更库不得超 15 分钟。"""
    assert backend.PAYLOAD_TTL_S < backend.CACHE_TTL_S <= backend.DB_TTL_S
    assert backend.DB_TTL_S <= 900.0, "日更库类 TTL 超过 15 分钟 = 徽标可落后一整天"


def test_badges_come_from_db_and_refresh_after_ttl(tmp_path) -> None:
    """徽标值只来自 buy_signal.db；缓存失效后必须重查库（不得返回旧值）。"""
    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    first = _badges_of(client.get("/api/watchlist.json").json(), "603629.SH")
    assert first and first[0]["label"] == "B1"

    # 库变了：清空该票信号
    conn = sqlite3.connect(str(ctx._dbs["buy_signal.db"]))
    conn.execute("DELETE FROM stock_signal")
    conn.commit()
    conn.close()

    # 未到 TTL：仍返回旧徽标（TTL 语义；这段滞后窗口是已知差异，不是 bug）
    assert _badges_of(client.get("/api/watchlist.json").json(), "603629.SH") == first

    # 到 TTL（等价于 _cached 里 monotonic 过期后重算）：必须重新查库 → 徽标消失
    backend._reset_cache()
    assert _badges_of(client.get("/api/watchlist.json").json(), "603629.SH") == []


def test_badges_key_is_per_stock(tmp_path) -> None:
    """缓存键含 ts_code：一只票的信号不能串到另一只（旧端事故的另一半）。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert '"badges:" + ts_code' in src, "徽标缓存键必须含 ts_code"


def test_badges_fall_back_to_etf_signal(tmp_path) -> None:
    """ETF 买点徽标（2026-11-11）：ETF 不在 stock_signal，回落查 etf_signal。

    守两件事：① ETF 能拿到徽标（不是空）；② 个股仍优先走 stock_signal，不被 ETF 表抢。
    """
    doc = _base_doc()
    doc["items"].append({"kind": "stock", "code": "510300.SH", "name": "沪深300ETF",
                         "added_at": "2026-11-11 10:00:00", "order": 99,
                         "group": "stock_default"})
    ctx = _ctx(tmp_path, doc=doc)
    body = _client(ctx).get("/api/watchlist.json").json()
    etf = _badges_of(body, "510300.SH")
    assert [b["label"] for b in etf] == ["砖", "B1"]
    # 个股路径不变：603629 仍从 stock_signal 拿到 B1 + 周
    stock = _badges_of(body, "603629.SH")
    assert [b["label"] for b in stock] == ["B1", "周"]


# ---------------------------------------------------------------- 7. 日更富化磁盘缓存（2026-11-07）
#
# 背景：8810 重启后首屏自选卡要 8s（板块/个股 regime 跑全历史状态机）。
# 落盘层按三个源库的 mtime+size 失效；进程重启后首次读盘即命中。


def test_enrich_cache_is_written_after_first_build(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    _client(ctx).get("/api/watchlist.json").json()
    assert ctx.enrich_cache_path.is_file(), "首次构建后应落盘日更富化缓存"
    doc = json.loads(ctx.enrich_cache_path.read_text(encoding="utf-8"))
    assert doc["version"] == backend._DISK_VERSION
    assert sorted(doc["stamps"]) == ["amv_board.db", "buy_signal.db", "ths_board.db"]
    assert doc["items"]["regime:603629"] in ("多", "偏多", "空", "偏空", None)
    assert any(k.startswith("sec_regime:") for k in doc["items"])
    assert not any(k.startswith("eod_batch:") for k in doc["items"]), "行情类不落盘"


def test_enrich_cache_survives_process_restart(tmp_path, monkeypatch) -> None:
    """模拟重启：内存全丢、磁盘文件还在 → 不得重跑口径库。"""
    ctx = _ctx(tmp_path, doc=_base_doc())
    first = _client(ctx).get("/api/watchlist.json").json()
    assert ctx.enrich_cache_path.is_file()

    backend._reset_cache()
    backend._reset_disk_state()          # 只清内存镜像，文件保留

    def boom(_ctx):
        raise AssertionError("命中磁盘缓存时不应重新加载 amv_regime")

    monkeypatch.setattr(backend, "_amv_lib", boom)
    second = _client(ctx).get("/api/watchlist.json").json()
    assert second["items"] == first["items"]


def test_enrich_cache_invalidates_when_source_db_changes(tmp_path) -> None:
    """源库 mtime/size 变了 → 整份作废，必须重查（不得把旧徽标当新值）。"""
    import time as _time

    ctx = _ctx(tmp_path, doc=_base_doc())
    client = _client(ctx)
    first = _badges_of(client.get("/api/watchlist.json").json(), "603629.SH")
    assert first and first[0]["label"] == "B1"

    conn = sqlite3.connect(str(ctx._dbs["buy_signal.db"]))
    conn.execute("DELETE FROM stock_signal")
    conn.commit()
    conn.close()
    _time.sleep(0.05)                    # 保证 mtime 可比（Windows 粗粒度场合）

    backend._reset_cache()
    backend._reset_disk_state()
    assert _badges_of(client.get("/api/watchlist.json").json(), "603629.SH") == []


def test_corrupt_enrich_cache_falls_back_and_rewrites(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc())
    ctx.enrich_cache_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.enrich_cache_path.write_text("{not json", encoding="utf-8")

    body = _client(ctx).get("/api/watchlist.json").json()
    assert body["items"], "缓存损坏不得影响响应"
    doc = json.loads(ctx.enrich_cache_path.read_text(encoding="utf-8"))
    assert doc["version"] == backend._DISK_VERSION, "损坏后应重写为合法缓存"


# ---------------------------------------------------------------- 加入选股（MMDD 日期分组，2026-11-13）


def _mmdd_doc(names: List[str]) -> Dict[str, Any]:
    """带指定日期分组的现场（names 如 ['1111','1110']），每组各一只票。"""
    groups = [{"id": "stock_default", "name": "自选", "kind": "stock", "order": 0}]
    items = []
    for i, nm in enumerate(names):
        groups.append({"id": "sel_" + nm, "name": nm, "kind": "stock", "order": 8 + i})
        items.append({"kind": "stock", "code": "60000%d.SH" % i, "name": "样本%d" % i,
                      "added_at": "2026-11-11 10:00:00", "order": 0, "group": "sel_" + nm})
    groups.append({"id": "sector_default", "name": "板块", "kind": "sector", "order": 100})
    return {"meta": {"schema_version": "1.0.0", "document_type": "watchlist",
                     "updated_at": "2026-11-11 10:00:00", "source": "server"},
            "groups": groups, "items": items}


def _set_now(monkeypatch, dt: datetime) -> None:
    monkeypatch.setattr(backend, "_now_dt", lambda: dt)


def _set_trading(ctx, on: bool) -> None:
    import types
    ctx.trade_clock = types.SimpleNamespace(is_trading_day=lambda d=None: on)


def _read_wl(tmp_path: Path) -> Dict[str, Any]:
    return json.loads((tmp_path / "panel" / "watchlist.json").read_text(encoding="utf-8"))


def test_add_auto_mmdd_after_10_creates_today_group(tmp_path, monkeypatch) -> None:
    """交易日 10:00 后加入选股 → 建当日组；2+1=3 个正好，不清理。"""
    ctx = _ctx(tmp_path, doc=_mmdd_doc(["1111", "1110"]))
    _set_now(monkeypatch, datetime(2026, 11, 12, 14, 30))
    _set_trading(ctx, True)
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "603279", "name": "景津装备",
                                   "kind": "stock", "autoMmdd": True}).json()
    assert body["group"] == "sel_1112"
    assert body["created_group"] == "1112"
    assert body["pruned"] == []
    doc = _read_wl(tmp_path)
    assert {g["name"] for g in doc["groups"] if str(g["id"]).startswith("sel_")} == {"1110", "1111", "1112"}


def test_add_auto_mmdd_prunes_oldest_over_three(tmp_path, monkeypatch) -> None:
    """已有 3 个日期组 + 新建今日组 = 4 个 → 删最旧那个，组内票一并删。"""
    ctx = _ctx(tmp_path, doc=_mmdd_doc(["1111", "1110", "1109"]))
    _set_now(monkeypatch, datetime(2026, 11, 12, 14, 30))
    _set_trading(ctx, True)
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "603279", "name": "景津装备",
                                   "kind": "stock", "autoMmdd": True}).json()
    assert body["group"] == "sel_1112"
    assert body["pruned"] == ["1109"]
    doc = _read_wl(tmp_path)
    assert {g["name"] for g in doc["groups"] if str(g["id"]).startswith("sel_")} == {"1110", "1111", "1112"}
    assert all(it["group"] != "sel_1109" for it in doc["items"])


def test_add_auto_mmdd_before_10_goes_to_latest(tmp_path, monkeypatch) -> None:
    """交易日 10:00 前 → 落到最近一个日期组，不新建、不清理。"""
    ctx = _ctx(tmp_path, doc=_mmdd_doc(["1111", "1110"]))
    _set_now(monkeypatch, datetime(2026, 11, 12, 9, 30))
    _set_trading(ctx, True)
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "603279", "name": "景津装备",
                                   "kind": "stock", "autoMmdd": True}).json()
    assert body["group"] == "sel_1111"
    assert body["created_group"] == ""
    assert body["pruned"] == []


def test_add_auto_mmdd_holiday_goes_to_latest(tmp_path, monkeypatch) -> None:
    """非交易日（日历判否）→ 落到最近一个日期组，绝不新建。"""
    ctx = _ctx(tmp_path, doc=_mmdd_doc(["1111", "1110"]))
    _set_now(monkeypatch, datetime(2026, 11, 14, 14, 0))
    _set_trading(ctx, False)
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "603279", "name": "景津装备",
                                   "kind": "stock", "autoMmdd": True}).json()
    assert body["group"] == "sel_1111"
    assert body["created_group"] == ""


def test_add_auto_mmdd_no_group_at_all_creates_today(tmp_path, monkeypatch) -> None:
    """一个日期组都没有（含 10 点前/非交易日）→ 建当日组，否则票无处可去。"""
    ctx = _ctx(tmp_path, doc=_base_doc())
    _set_now(monkeypatch, datetime(2026, 11, 12, 9, 30))
    _set_trading(ctx, True)
    body = _client(ctx).post("/api/watchlist/add",
                             json={"code": "603279", "name": "景津装备",
                                   "kind": "stock", "autoMmdd": True}).json()
    assert body["group"] == "sel_1112"
    assert body["created_group"] == "1112"


# ---------------------------------------------------------------- 8. 名称占位兜底（2026-11-18）
#
# 背景（主人报）：601985 中国核电明明在池里，自选卡与 K 线标题都显示“????”——
#   泡盘的 name 就是占位符（加入那一刻取名失败/传了占位）。口径：新增时用名表纠正后落盘；
#   存量脏名只在展示层纠正（读取不写盘），避免读端点变成隐式写入方。

def test_add_heals_placeholder_name_from_name_table(tmp_path) -> None:
    ctx = _ctx(tmp_path, doc=_base_doc(), names={"601985": "中国核电"})
    body = _client(ctx).post("/api/watchlist/add",
                             json={"kind": "stock", "code": "601985", "name": "????"}).json()
    assert body["ok"] is True and body["code"] == "601985.SH"
    saved = json.loads(ctx.wl_path.read_text(encoding="utf-8"))
    hit = next(x for x in saved["items"] if x["code"] == "601985.SH")
    assert hit["name"] == "中国核电"


def test_add_keeps_real_name_untouched(tmp_path) -> None:
    """真名一律不覆盖（名表只兜底占位符）。"""
    ctx = _ctx(tmp_path, doc=_base_doc(), names={"601985": "名表里的名字"})
    _client(ctx).post("/api/watchlist/add",
                      json={"kind": "stock", "code": "601985", "name": "中国核电"}).json()
    saved = json.loads(ctx.wl_path.read_text(encoding="utf-8"))
    hit = next(x for x in saved["items"] if x["code"] == "601985.SH")
    assert hit["name"] == "中国核电"


def test_watchlist_json_heals_legacy_placeholder_without_rewrite(tmp_path) -> None:
    doc = _base_doc()
    doc["items"].append({"kind": "stock", "code": "601985.SH", "name": "????",
                         "added_at": "2026-11-11 10:44:54", "order": 3, "group": "stock_default"})
    ctx = _ctx(tmp_path, doc=doc, names={"601985": "中国核电"})
    body = _client(ctx).get("/api/watchlist.json").json()
    hit = next(x for x in body["items"] if x.get("code") == "601985.SH")
    assert hit["name"] == "中国核电"
    saved = json.loads(ctx.wl_path.read_text(encoding="utf-8"))
    raw = next(x for x in saved["items"] if x["code"] == "601985.SH")
    assert raw["name"] == "????"
