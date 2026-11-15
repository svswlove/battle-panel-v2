# -*- coding: utf-8 -*-
"""compat_misc 插件契约测试（未运行验证：本子代理无 shell，01 负责跑）。

覆盖（全部 tmp 目录 + 假 ctx，**不碰真生产文件/真库/真行情**）：

1. manifest：`backend.mount == "root"`、**无 write**、白名单条目全在册；
2. 路由：**10 条**根路径齐全、都不自带 `/api` 前缀（core 负责拼）、**一条 POST 都没有**；
3. 真实实现的端点：`selection_board_options` / `stock_amv` / `sector_amv`
   / `sector_kline`（字段与旧 8800 逐键一致，含 404/降级分支）；
4. **4.3 选股三端点（真实数据路径）**：`selection` / `selection_pool` / `selection_prefs`
   —— 在假 ctx 的 panel 目录造三份 JSON（等价于白名单已登记的生产文件），断言
   **透传 + 轻量拼装**（`pool` 实时拼、`freshness` 五键），以及文件缺失 / JSON 损坏 /
   `groups` 为空时按旧端分支降级、不抛 500；另锁住三份文件的白名单登记（§1）；
5. 骨架端点：`review_chart.json`—— **键集合与旧端一致**（键路径集合断言，值 null/空不判 fail）；
   `ops.json` / `ui_notes/list` 已移交 `ops` 插件（N2，2026-10-24），这里只锁「已让路」；
6. **移交断言（4.1.3b）**：`/order/list`、`/trades/{ts_code}`、`/condition/list`、
   `/paper_account`、`/ledger` 五条已移交 `paper` 插件——本插件再注册就是 root 路径冲突，
   所以这里锁住「路由表里没有它们」+「HTTP 404」+「骨架构造函数已删除」；
6b. **移交断言（4.5）**：`/review.md` 已移交 `review` 插件（真实渲染，不再是骨架），
   本插件锁住「路由表里没有它」+「HTTP 404（仅本插件 app）」+「`build_review_md` /
   `REVIEW_MD_KEYS` 已删除」；`/review_chart.json` 仍在本插件（未迁移）；
7. 静态禁令：无 `sqlite3.connect`、无自连行情源、无 HTTP/子进程、无自己写文件。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as core_paths  # noqa: E402
from app.core.contract import (  # noqa: E402
    find_forbidden_db_usage,
    find_forbidden_quote_usage,
    load_manifest,
)
from app.plugins.compat_misc import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"

EXPECTED_PATHS = {
    "/selection", "/selection_live", "/selection_board_options",
    "/selection_pool", "/selection_prefs", "/stock_amv/{ts_code}",
    "/sector_amv/{code}", "/sector_kline/{code}",
    "/selection_etf",       # 2026-11-11 选股页 ETF 模式（只读 etf_snapshot_latest）
    "/selection_quotes",    # 2026-11-12 标注 N89：选股表实时涨幅（core 的 eltdx 快照，不重算选股）
}
# 2026-11-04：`/selection_live` 是选股页「盘中现算」的产物端点（只读原样下发，
#   不参与 build_selection 的自动选源；源文件 selection_live.json 已在 READABLE_PANEL 登记）。

# 2026-10-24（N2）：`/ops.json` 与 `/ui_notes/list` 已移交 `app/plugins/ops`
# （真实数据：ops_status.json 进 READABLE_PANEL；ui_notes.json 进 WRITABLE_STATE +
# `ctx.state_json`）。本插件再注册就是 root 路径冲突 → 锁住「路由表里没有它们」。
MOVED_PATHS_N2 = {"/ops.json", "/ui_notes/list"}

# 4.1.3b 移交给 `app/plugins/paper` 插件的 5 条路径（本插件不得再注册）
HANDED_OVER_PATHS = {
    "/order/list", "/trades/{ts_code}", "/condition/list",
    "/paper_account", "/ledger",
}

# 4.5 移交给 `app/plugins/review` 插件的 1 条路径（本插件不得再注册）
MOVED_PATHS_45 = {"/review.md"}


# ---------------------------------------------------------------- 假行情门面


class _Bar:
    """形状与 eltdx bar 一致：time / open / high / low / close / volume_lots / amount。"""

    def __init__(self, day: str, close: float, lots: float = 1000.0, amount: float = 1e6):
        self.time = datetime.strptime(day, "%Y%m%d")
        self.open = close - 0.1
        self.high = close + 0.2
        self.low = close - 0.2
        self.close = close
        self.volume_lots = lots
        self.amount = amount


class _FakeQuotes:
    def __init__(self, daily: Optional[Dict[str, List[_Bar]]] = None, boom: bool = False):
        self._daily = daily or {}
        self._boom = boom

    def daily_bars(self, glb: str, count: int = 8, adjust: Optional[str] = None):
        if self._boom:
            raise RuntimeError("行情源不可达")
        rows = list(self._daily.get(glb, []))
        return rows[-count:] if count else rows


# ---------------------------------------------------------------- 假 ctx


class FakeCtx:
    """只实现本插件用到的 ctx 面（panel_json / db_read / lib / quotes）。"""

    id = "compat_misc"

    def __init__(self, tmp_path: Path, *, panels: Optional[Dict[str, Dict[str, Any]]] = None,
                 amv_db: Optional[Path] = None, ths_db: Optional[Path] = None,
                 sig_db: Optional[Path] = None,
                 quotes: Any = None) -> None:
        self.root = tmp_path
        self.panel_dir = tmp_path / "panel"
        self.panel_dir.mkdir(parents=True, exist_ok=True)
        self._panels = panels or {}
        self._dbs = {"amv_board.db": amv_db, "ths_board.db": ths_db,
                     "buy_signal.db": sig_db}   # 2026-11-11 ETF 买点徽标
        self._quotes = quotes if quotes is not None else _FakeQuotes()
        self.panel_calls: List[str] = []
        self.db_calls: List[str] = []

    def panel_json(self, name: str) -> Dict[str, Any]:
        self.panel_calls.append(name)
        if name in self._panels:
            return self._panels[name]
        path = self.panel_dir / ("%s.json" % name)
        if not path.is_file():
            raise PermissionError("%s 未在 manifest.data.panel 声明" % name)
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def db_read(self, name: str):
        self.db_calls.append(name)
        path = self._dbs.get(name)
        if path is None:
            raise FileNotFoundError(name)
        conn = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def panel_write(self, name: str, payload: Dict[str, Any]) -> Path:
        """测试用受控写：落 tmp panel 目录 + 记录调用；与 core panel_write 同语义。"""
        self.panel_calls.append("write:" + name)
        path = self.panel_dir / ("%s.json" % name)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._panels[name] = payload
        return path

    def lib(self, name: str):
        if name != "indicators":
            raise PermissionError(name)
        import importlib.util
        path = core_paths.lib_path(name)
        spec = importlib.util.spec_from_file_location("_t_indicators", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_t_indicators"] = mod
        spec.loader.exec_module(mod)
        return mod

    @property
    def quotes(self):
        return self._quotes


def _client(ctx: FakeCtx) -> TestClient:
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    return TestClient(app)


# ---------------------------------------------------------------- 夹具库


def _mk_amv_db(tmp_path: Path) -> Path:
    """最小 amv_board.db：sector_meta / sector_daily / stock_state（列名对齐生产 schema）。"""
    db = tmp_path / "amv_board.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE sector_meta(
        level TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
        l1 TEXT, exclude INTEGER NOT NULL DEFAULT 0, is_unmap INTEGER NOT NULL DEFAULT 0,
        level_num INTEGER, ord INTEGER, PRIMARY KEY(level, code))""")
    conn.executemany("INSERT INTO sector_meta VALUES(?,?,?,?,?,?,?,?)", [
        ("L2", "881166.TI", "军工装备", "国防军工", 0, 0, 2, 0),
        ("CONCEPT", "885311.TI", "智能电网", None, 0, 0, None, 0),
    ])
    conn.execute("""CREATE TABLE sector_daily(
        date TEXT NOT NULL, level TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
        amv REAL NOT NULL, share REAL NOT NULL, rank INTEGER, d1 REAL, d5 REAL, d20 REAL,
        PRIMARY KEY(date, level, code))""")
    for i in range(8):
        d = "2026-10-%02d" % (1 + i)
        conn.execute("INSERT INTO sector_daily VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (d, "L2", "881166.TI", "军工装备", 5e9 + i * 1e8, 0.002, 1, 0, 0, 0))
        conn.execute("INSERT INTO sector_daily VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (d, "L2", "X4001", "旧口径行业", 2e9 + i * 1e7, 0.001, 2, 0, 0, 0))
    conn.execute("""CREATE TABLE stock_state(
        stock TEXT NOT NULL, date TEXT NOT NULL, alive REAL, sh REAL,
        PRIMARY KEY(stock, date))""")
    for i in range(5):
        conn.execute("INSERT INTO stock_state VALUES(?,?,?,?)",
                     ("603629", "2026-10-%02d" % (1 + i), 1.0e7 + i, 1.2e7))
    conn.commit()
    conn.close()
    return db


def _mk_ths_db(tmp_path: Path) -> Path:
    """最小 ths_board.db：board / concept（列对齐 ths_board_pool.py SCHEMA）。"""
    db = tmp_path / "ths_board.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE board(code TEXT PRIMARY KEY, name TEXT, sw_level INTEGER,"
                 " l1 TEXT, l1_src TEXT, n_members INTEGER, updated_at TEXT)")
    conn.execute("CREATE TABLE concept(code TEXT PRIMARY KEY, name TEXT,"
                 " n_members INTEGER, updated_at TEXT)")
    conn.executemany("INSERT INTO board VALUES(?,?,?,?,?,?,?)", [
        ("881166.TI", "军工装备", 2, "国防军工", "sw", 276, "2026-10-23"),
        ("881102.TI", "养殖业", 2, "农林牧渔", "sw", 40, "2026-10-23"),
        ("884001.TI", "种子生产", 3, "农林牧渔", "sw", 12, "2026-10-23"),
    ])
    # 2026-10-25 口径：concept 表 = THS 885/886 冻结段（非自建，**不进选股板块候选**）
    #                 + 自建 999 段（进候选）。两个码必须都在，才能同时验「滤掉」与「保留」。
    conn.executemany("INSERT INTO concept VALUES(?,?,?,?)", [
        ("885311.TI", "智能电网", 80, "2026-10-23"),     # THS 概念（已下线）→ 应被滤掉
        ("999001.TI", "自建·机器人", 42, "2026-10-25"),   # 自建板块（999 段）→ 应保留
    ])
    conn.commit()
    conn.close()
    return db


def _ctx(tmp_path: Path, **kwargs) -> FakeCtx:
    panels = kwargs.pop("panels", None) or {
        "real_positions": {
            "status": "CONNECTED",
            "updated_at": "2026-10-23 19:09:45",
            "positions": [
                {"code": "600519.SH", "name": "贵州茅台", "qty": 100, "available_qty": 100,
                 "cost_price": 1500.0},
                {"code": "000001.SZ", "name": "平安银行", "qty": 100, "available_qty": 100,
                 "cost_price": 0},          # cost<=0 = 无效成本线（旧端口径）
            ],
        },
        "watchlist": {
            "meta": {"schema_version": "1.0.0"},
            "groups": [],
            "items": [
                {"kind": "sector", "code": "881166.ti", "name": "军工装备"},
                {"kind": "sector", "code": "881166.TI", "name": "军工装备"},   # 重复 → 去重
                {"kind": "sector", "code": "999999.TI", "name": ""},           # 库外 → UNKNOWN
                {"kind": "stock", "code": "603629.SH", "name": "利通电子"},   # 非板块 → 不进
            ],
        },
    }
    quotes = kwargs.pop("quotes", _FakeQuotes({
        "sh881166": [_Bar("202610%02d" % (1 + i), 100.0 + i) for i in range(30)],
    }))
    return FakeCtx(tmp_path, panels=panels,
                   amv_db=_mk_amv_db(tmp_path), ths_db=_mk_ths_db(tmp_path),
                   sig_db=kwargs.pop("sig_db", None), quotes=quotes)


# ---------------------------------------------------------------- 1. manifest


def test_manifest_root_mount_readonly() -> None:
    manifest = load_manifest(PLUGIN_DIR)
    assert manifest.id == "compat_misc"
    assert manifest.backend.mount == "root"
    assert manifest.backend.entry == "register"
    assert manifest.data_write == ("selection_filter", "selection_pool")  # 4.7 选股页写入口
    assert manifest.quotes_read is True
    assert "amv_board.db" in manifest.data_read
    assert "ths_board.db" in manifest.data_read
    for name in manifest.panel_read:
        assert name in core_paths.READABLE_PANEL, name
    for name in manifest.data_read:
        assert name in core_paths.READABLE_DB, name
    for name in manifest.lib_read:
        assert name in core_paths.READABLE_LIB, name


def test_routes_complete_and_no_post(tmp_path: Path) -> None:
    router = backend.register(_ctx(tmp_path))
    gets = {r.path for r in router.routes if "GET" in getattr(r, "methods", set())}
    assert gets == EXPECTED_PATHS
    posts = {r.path for r in router.routes
             if getattr(r, "methods", set()) - {"GET", "HEAD"}}
    assert posts == {"/selection_prefs", "/selection_pool/add", "/selection_pool/remove"}, (
        "本插件只允许选股页三个写路由（4.7）；其余仍只读")


def test_paths_have_no_api_prefix(tmp_path: Path) -> None:
    router = backend.register(_ctx(tmp_path))
    for route in router.routes:
        assert not route.path.startswith("/api"), route.path


# ---------------------------------------------------------------- 1b. 已移交 paper 插件


def test_handed_over_paths_not_registered(tmp_path: Path) -> None:
    """4.1.3b：5 条交易链路径已移交 `paper` 插件，本插件再注册就会 root 路径冲突。"""
    router = backend.register(_ctx(tmp_path))
    registered = {r.path for r in router.routes}
    assert not (registered & HANDED_OVER_PATHS), (
        "这些路径已由 app/plugins/paper 接管，本插件不得重复注册: %s"
        % sorted(registered & HANDED_OVER_PATHS))


def test_handed_over_paths_404(tmp_path: Path) -> None:
    """HTTP 层再确认一次：这五条在本插件的 app 里不存在（404），不是空骨架。"""
    client = _client(_ctx(tmp_path))
    for path in ("/api/order/list", "/api/condition/list", "/api/paper_account",
                 "/api/ledger?mode=paper", "/api/trades/600519.SH"):
        assert client.get(path).status_code == 404, path


def test_handed_over_builders_removed() -> None:
    """骨架构造函数随路由一并删除（留着就会有人再接回去，双源必然打架）。"""
    for name in ("build_order_list", "build_trades", "build_condition_list",
                 "build_paper_account", "build_ledger", "PAPER_ACCOUNT_SKELETON",
                 "LEDGER_ACCOUNT_KEYS"):
        assert not hasattr(backend, name), "已移交 paper 插件，本插件不应保留 %s" % name


# ---------------------------------------------------------------- 2. 真实实现


def test_selection_board_options(tmp_path: Path) -> None:
    # 先确认「自建判据真的加载到了」：读不到工具目录时 _is_custom_board 保守返回 True
    # （不过滤）。那样下面的 885 断言会失败——这里把原因写明，避免有人以为是数据问题
    # 就把断言删了（删掉等于放行「候选里混进已下线概念」）。
    assert backend._is_custom_board("885311.TI") is False, (
        "自建板块判据未生效（ths_board_pool.is_custom_code 未加载）→ 概念过滤形同虚设；"
        "请确认 夏天-activity/工具/ths_board_pool.py 可读")
    body = _client(_ctx(tmp_path)).get("/api/selection_board_options").json()
    assert set(body) == {"favorites", "catalog", "counts"}
    assert set(body["counts"]) == {"favorites", "catalog"}
    # favorites：去重 + 只留 sector + 库外标 UNKNOWN
    codes = [f["code"] for f in body["favorites"]]
    assert codes == ["881166.TI", "999999.TI"]
    by_code = {f["code"]: f for f in body["favorites"]}
    assert by_code["881166.TI"]["level"] == "L2"
    assert by_code["999999.TI"]["level"] == "UNKNOWN"
    assert by_code["999999.TI"]["name"] == "999999.TI"     # 名称缺失 → 回退代码
    # catalog：L2 全在前、其后再按名称混排 L3/CONCEPT（旧端 sort key 逐字：
    #   (0 if level=='L2' else 1, name)）
    levels = [c["level"] for c in body["catalog"]]
    assert levels[:2] == ["L2", "L2"]
    assert set(levels[2:]) == {"L3", "CONCEPT"}
    assert {c["name"] for c in body["catalog"][:2]} == {"军工装备", "养殖业"}
    assert body["counts"] == {"favorites": 2, "catalog": 4}

    # 2026-10-25 口径（本次守门口径，勿删）：concept 段只给**自建（999 段）**。
    # 背景：THS 概念 885/886 自 2026-10-25 起不再周更、已下线；若仍进候选，前端能把
    # 死板块加进自选、点进去是空板块。故这里同时锁两件事，缺一件都算破门：
    #   ① 非自建 THS 概念（885311.TI，库里真实存在）→ 不得进 catalog；
    #   ② 自建 999 段（999001.TI）→ 必须在 catalog，且 level=CONCEPT、名称原样。
    catalog_codes = {c["code"] for c in body["catalog"]}
    assert "885311.TI" not in catalog_codes, (
        "非自建 THS 概念（885 段，已下线）不得进选股板块候选：%s" % sorted(catalog_codes))
    assert "999001.TI" in catalog_codes, "自建概念（999 段）必须保留在候选里"
    custom = next(c for c in body["catalog"] if c["code"] == "999001.TI")
    assert custom["level"] == "CONCEPT" and custom["name"] == "自建·机器人"


def test_is_custom_board_999_segment() -> None:
    """自建板块判据：999 段为自建；885/886（THS 概念）与 881/884（行业）不是。

    判据唯一源 = `夏天-activity/工具/ths_board_pool.py::is_custom_code`（999 前缀，
    兼容裸码 999001 与带后缀 999001.TI）。本用例同时是环境门禁：工具目录读不到时
    `_is_custom_board` 会保守返回 True（不过滤），那 885 那条断言就会失败 —— 这是故意的，
    静默放行等于把「候选里混进死板块」重新放回生产。
    """
    assert backend._is_custom_board("999001") is True        # 裸码
    assert backend._is_custom_board("999001.TI") is True     # 带后缀
    assert backend._is_custom_board("999999.TI") is True
    assert backend._is_custom_board("885311.TI") is False    # THS 概念（已下线）
    assert backend._is_custom_board("886109.TI") is False    # THS 概念（已下线）
    assert backend._is_custom_board("881166.TI") is False    # THS 行业二级
    assert backend._is_custom_board("884001.TI") is False    # THS 行业三级


def test_selection_board_options_degrades_without_dbs(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)          # 无库、无 watchlist → 空清单不 500（旧端同）
    body = _client(ctx).get("/api/selection_board_options").json()
    assert body == {"favorites": [], "catalog": [],
                    "counts": {"favorites": 0, "catalog": 0}}


def test_stock_amv_ths_code(tmp_path: Path) -> None:
    body = _client(_ctx(tmp_path)).get("/api/stock_amv/603629.SH?days=3").json()
    assert set(body) == {"code", "ts_code", "dates", "alive", "sh", "note"}
    assert body["code"] == "603629"
    assert body["ts_code"] == "603629.SH"
    assert len(body["dates"]) == 3                 # 升序末 3 日
    assert body["dates"] == sorted(body["dates"])
    assert len(body["alive"]) == len(body["sh"]) == 3


def test_stock_amv_unknown_404(tmp_path: Path) -> None:
    r = _client(_ctx(tmp_path)).get("/api/stock_amv/000000.SZ")
    assert r.status_code == 404
    assert "state" in r.json()["detail"]


def test_sector_amv_board_branch(tmp_path: Path) -> None:
    body = _client(_ctx(tmp_path)).get("/api/sector_amv/881166.TI?days=5").json()
    assert set(body) == {"code", "name", "index_code", "ma_n", "level", "dates",
                         "amv", "ma", "share", "rank"}
    assert body["level"] == "L2" and body["name"] == "军工装备"
    assert body["ma_n"] == 10
    assert len(body["dates"]) == 5
    # 均线是**扩张窗口**（前 9 点也出值，旧端同）——不是 None
    assert all(v is not None for v in body["ma"])


def test_sector_amv_l2_fallback_branch(tmp_path: Path) -> None:
    """库外码走旧 L2 回落分支：无 `level` 键，name/index_code 不可得 → null。"""
    body = _client(_ctx(tmp_path)).get("/api/sector_amv/X4001?days=5").json()
    assert "level" not in body
    assert body["name"] is None and body["index_code"] is None
    assert set(body) == {"code", "name", "index_code", "ma_n", "dates",
                         "amv", "ma", "share", "rank"}


def test_sector_amv_unknown_404(tmp_path: Path) -> None:
    assert _client(_ctx(tmp_path)).get("/api/sector_amv/NOPE").status_code == 404


def test_sector_kline_shape(tmp_path: Path) -> None:
    body = _client(_ctx(tmp_path)).get("/api/sector_kline/881166.TI?limit=10").json()
    assert set(body) == {"code", "index_code", "bars", "n", "last",
                         "source", "indicators"}
    assert body["source"] == "eltdx:daily_bars"     # 如实标源，不冒充 vipdoc
    assert body["indicators"] == ["yellow", "white"]
    assert 0 < body["n"] <= 10
    bar = body["bars"][-1]
    assert set(bar) == {"t", "o", "h", "l", "c", "v", "amt", "yellow", "white"}
    assert len(bar["t"]) == 8 and bar["t"].isdigit()
    assert body["last"] == bar["t"]


def test_sector_kline_quotes_down_returns_skeleton(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, quotes=_FakeQuotes(boom=True))
    body = _client(ctx).get("/api/sector_kline/881166.TI").json()
    assert body["bars"] == [] and body["n"] == 0 and body["last"] is None
    assert set(body) == {"code", "index_code", "bars", "n", "last",
                         "source", "indicators"}


# ---------------------------------------------------------------- 3. /trades / order / condition / paper_account / ledger
# 2026-10-23（4.1.3b）：这五条端点已移交 `app/plugins/paper`，本插件的形状断言一并删除
# （对应断言已随实现迁到 `app/plugins/paper/tests/test_paper_api.py`）。
# 这里只保留一条「不许再注册」的护栏，见本文 §1b 的三个用例。


# ---------------------------------------------------------------- 4. 骨架端点键集

# ---------------------------------------------------------------- 4. 骨架端点（ops.json 已移交）
#
# N2（2026-10-24）：`/ops.json` 已移交 `app/plugins/ops`，其旧端键集断言（meta/summary/
# tasks/daemons/freshness）随之移到 `app/plugins/ops/tests`。本文件只保留「已让路」断言。


# ---------------------------------------------------------------- 4b. 选股三端点（4.3 真实数据）
#
# 口径来源：docs/task_selection_4_3.md §2。三份 JSON 都是**已有生产文件**
# （selection_latest 8.2MB 盘后快照 / selection_pool 1.8KB / selection_filter 561B），
# 这里只看「透传 + 轻量拼装」的形状，**不重算选股、不查 buy_signal.db**。


def _write_panel(ctx: "FakeCtx", name: str, obj: Any) -> Path:
    """在假 ctx 的 panel 目录里造一份 JSON（模拟已登记的生产文件）。"""
    path = ctx.panel_dir / ("%s.json" % name)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


_SEL_SNAP = {
    "meta": {"schema_version": "1.0.0", "document_type": "selection",
             "generated_at": "2026-10-23 17:04:06",
             "source": "postclose_chain:selectionsnap",
             "producer": "selection_snapshot_builder",
             "trade_date": "2026-10-23", "signal_date": "2026-10-23"},
    "date": "2026-10-23",
    "asof": {"signal_date": "2026-10-23", "quote": ["2026-10-23"]},
    "filters": {"mcap_min": 50.0, "mcap_max": 300.0},
    "filter_options": {"industries": [{"code": "881166.TI", "name": "军工装备"}]},
    "group_sigs": {"B1": ["B1_W"]},
    "group_filters": {"B1": "全部"},
    "board_map": {"603629.SH": "军工装备"},
    "hot_concepts": {"up": []},
    "stats": {"hits": 2, "groups": 1, "scored": 2},
    "warnings": [],
    "score_warn": [],
    "groups": [{"key": "B1", "label": "B1", "count": 2,
                "rows": [{"ts_code": "603629.SH", "name": "利通电子"},
                         {"ts_code": "000636.SZ", "name": "风华高科"}]}],
}

_SEL_POOL = {
    "meta": {"schema_version": "1.0.0", "document_type": "selection_pool",
             "updated_at": "2026-10-22 00:47:04", "source": "server"},
    "date": "2026-10-21",
    "items": [{"code": "603629.SH", "name": "利通电子", "group": "动能", "sig": "MOM",
               "added_at": "2026-10-22 00:47:04", "order": 0},
              {"code": "000636.SZ", "name": "风华高科", "group": "周B1", "sig": "周",
               "added_at": "2026-10-22 00:45:24", "order": 1},
              {"code": "002244.SZ", "name": "滨江集团", "group": "B1", "sig": "B1_W",
               "added_at": "2026-10-22 00:43:45", "order": 2},
              {"code": "002959.SZ", "name": "小熊电器", "group": "B2", "sig": "B2",
               "added_at": "2026-10-19 23:42:34", "order": 3}],
}

_SEL_FILTER = {
    "meta": {"schema_version": "1.0.0", "document_type": "selection_filter",
             "updated_at": "2026-10-22 00:45:46", "source": "server"},
    "pools": ["mainboard"],
    "ma": {"on": True, "jMax": 13, "shortOverLong": 0.99, "closeOverLong": 0.99},
    "pin": {"on": False, "longMin": 70, "shortMax": 35},
    "inds": [], "cons": [], "boards": [],
    "sigs": {"砖形图": ["ZHU_P", "ZHU_O"]},
    "boardOn": False,
    "hot": {"on": False},
}

SEL_POOL_KEYS = {"date", "count", "is_current", "names", "items", "slots", "keep",
                "group_id", "mmdd"}   # 2026-11-13：池子改映射自选池的 MMDD 组，多两个键
SEL_FRESHNESS_KEYS = {"state", "snapshot_date", "expected_date", "produce_by",
                      "today_pending"}
SEL_FILTER_KEYS = {"meta", "pools", "ma", "pin", "inds", "cons", "boards", "sigs",
                   "boardOn", "hot"}
# 旧端正常分支的快照键（实读 selection_latest.json 后与 keys_expected.json 对齐）
SEL_SNAP_KEYS = {"meta", "date", "asof", "filters", "filter_options", "group_sigs",
                 "group_filters", "board_map", "hot_concepts", "stats", "warnings",
                 "score_warn", "groups"}


# 2026-11-13：选股池改为映射自选池的 MMDD 组（sel_<MMDD>）——测试现场也要有 watchlist.json
_SEL_WATCHLIST = {
    "meta": {"schema_version": "1.0.0", "document_type": "watchlist",
             "updated_at": "2026-11-12 20:26:37", "source": "server"},
    "groups": [
        {"id": "stock_default", "name": "自选", "kind": "stock", "order": 0},
        {"id": "c680cc4d", "name": "选股", "kind": "stock", "order": 5},
        {"id": "sel_1112", "name": "1112", "kind": "stock", "order": 8},
        {"id": "sel_1111", "name": "1111", "kind": "stock", "order": 9},
        {"id": "sector_default", "name": "板块", "kind": "sector", "order": 100},
    ],
    "items": [
        {"kind": "stock", "code": "603279.SH", "name": "景津装备",
         "added_at": "2026-11-12 14:40:02", "order": 0, "group": "sel_1112"},
        {"kind": "stock", "code": "688303.SH", "name": "大全能源",
         "added_at": "2026-11-12 14:41:00", "order": 1, "group": "sel_1112"},
        {"kind": "stock", "code": "601919.SH", "name": "中远海控",
         "added_at": "2026-11-12 14:42:00", "order": 2, "group": "sel_1112"},
        {"kind": "stock", "code": "600000.SH", "name": "浦发银行",
         "added_at": "2026-11-11 14:00:00", "order": 0, "group": "sel_1111"},
        {"kind": "stock", "code": "000001.SZ", "name": "平安银行",
         "added_at": "2026-11-10 10:00:00", "order": 0, "group": "stock_default"},
    ],
}


def _sel_ctx(tmp_path: Path, **kwargs) -> "FakeCtx":
    """在选股四份 JSON 都在的现场建 ctx（2026-11-13 加 watchlist）。"""
    ctx = _ctx(tmp_path, **kwargs)
    _write_panel(ctx, "selection_latest", _SEL_SNAP)
    _write_panel(ctx, "selection_pool", _SEL_POOL)
    _write_panel(ctx, "selection_filter", _SEL_FILTER)
    _write_panel(ctx, "watchlist", _SEL_WATCHLIST)
    return ctx


def test_selection_whitelist_registered() -> None:
    """§1：三份文件既在 core 白名单、也在 manifest.data.panel（两边缺一个就被拒加载）。"""
    manifest = load_manifest(PLUGIN_DIR)
    for name, fname in (("selection_latest", "selection_latest.json"),
                        ("selection_pool", "selection_pool.json"),
                        ("selection_filter", "selection_filter.json")):
        assert name in core_paths.READABLE_PANEL, name
        assert name in manifest.panel_read, name
        path = core_paths.READABLE_PANEL[name]
        assert path.name == fname
        assert path.parent == core_paths.PANEL_DIR
        # 4.7：selection_latest 仍只读；selection_pool/filter 是选股页写入口
        if name == "selection_latest":
            assert name not in core_paths.WRITABLE_PANEL, name
        else:
            assert name in core_paths.WRITABLE_PANEL, name


def test_selection_real_snapshot_passthrough(tmp_path: Path) -> None:
    """`/api/selection`：快照原样透传 + 实时拼 pool + freshness（不重算 groups）。"""
    body = _client(_sel_ctx(tmp_path)).get("/api/selection").json()
    # 快照键一个不少、一个不多（旧端正常分支 + 本插件挂的 pool/freshness + 2026-11-04 的 mode）
    assert set(body) == SEL_SNAP_KEYS | {"pool", "freshness", "mode"}
    assert body["mode"] == "postclose", "tmp 里没有 selection_live → 必须走盘后分支"
    assert "error" not in body                          # 正常分支没有 error
    assert body["date"] == "2026-10-23"
    # groups/rows 原样透传（不重排、不补字段）
    assert body["groups"] == _SEL_SNAP["groups"]
    assert body["stats"] == {"hits": 2, "groups": 1, "scored": 2}
    assert body["filter_options"] == _SEL_SNAP["filter_options"]
    assert body["meta"]["producer"] == "selection_snapshot_builder"
    # pool 实时拼（来自 selection_pool.json，不是快照里带）
    assert set(body["pool"]) == SEL_POOL_KEYS
    assert body["pool"]["date"] == "2026-10-21"
    assert body["pool"]["count"] == 4
    # 池的「是不是今天的」用快照自带 signal_date（2026-10-23）比，不是自然日 → 不是当前
    assert body["pool"]["is_current"] is False
    # freshness 只四/五个键
    assert set(body["freshness"]) == SEL_FRESHNESS_KEYS
    assert body["freshness"]["snapshot_date"] == "2026-10-23"
    assert body["freshness"]["produce_by"] == "18:00"
    assert body["freshness"]["state"] in {"fresh", "stale", "unknown"}


def test_selection_does_not_mutate_snapshot(tmp_path: Path) -> None:
    """快照是共享 dict（同一 ctx 反复拿到同一个对象）：只浅拷顶层，不能把 pool/freshness 写回本体。"""
    shared = json.loads(json.dumps(_SEL_SNAP))          # 本用例私有的「共享快照」
    ctx = FakeCtx(tmp_path, panels={"selection_latest": shared,
                                    "selection_pool": _SEL_POOL,
                                    "selection_filter": _SEL_FILTER})
    body = backend.build_selection(ctx)
    assert body["pool"]["count"] == 4 and "freshness" in body
    assert "pool" not in shared and "freshness" not in shared
    assert shared == _SEL_SNAP                          # 一个键都没被改


def test_selection_pool_real(tmp_path: Path) -> None:
    """/api/selection_pool（HTTP）：items 原样、names 取前 3、单 slot、不编造历史。"""
    client = _client(_sel_ctx(tmp_path))
    body = client.get("/api/selection_pool").json()
    assert set(body) == SEL_POOL_KEYS
    assert body["date"] == "2026-10-21"
    assert body["count"] == 4
    assert body["keep"] == 5
    assert isinstance(body["is_current"], bool)         # 具体真假见下面的纯函数用例
    assert body["items"] == _SEL_POOL["items"]          # 原样、不重排
    assert body["names"] == ["利通电子", "风华高科", "滨江集团"]      # 前 3 个非空名
    # 只有一份文件 → 单 slot（不编造历史快照）
    assert body["slots"] == [{"date": "2026-10-21", "count": 4,
                             "saved_at": "2026-10-22 00:47:04"}]

    # 传别的日期（含 '1025' 短格式）→ 旧端语义：回显该日期、items 为空（不拿当前名单冒充）
    other = client.get("/api/selection_pool?date=1025").json()
    assert other["is_current"] is False
    assert other["date"] == "2026-10-25"
    assert other["count"] == 0 and other["items"] == []


def test_selection_pool_is_current_semantics(tmp_path: Path) -> None:
    """`is_current` 语义（**注入 today，不依赖跑测时钟**）。

    旧端公式 `bool(d) and d == today`，`today` = 当前信号日。插件无 `buy_signal.db`
    访问权：`/api/selection` 注入快照 signal_date，独立端点回退自然日。
    """
    ctx = _sel_ctx(tmp_path)
    # 池日期 == 注入的信号日：是当前名单
    assert backend.build_selection_pool(ctx, "", today="2026-10-21")["is_current"] is True
    # 池日期 != 信号日：不是当前（不假装）
    assert backend.build_selection_pool(ctx, "", today="2026-10-23")["is_current"] is False
    # 指定与池同一天 + 池就是当前：仍是当前
    assert backend.build_selection_pool(
        ctx, "2026-10-21", today="2026-10-21")["is_current"] is True
    # 指定别的日期（就算池是当前名单）：看的不可能是「当前」，False
    assert backend.build_selection_pool(
        ctx, "1025", today="2026-10-21")["is_current"] is False
    # 短格式也能归一命中
    assert backend.build_selection_pool(
        ctx, "20261021", today="2026-10-21")["is_current"] is True


def test_selection_pool_uses_snapshot_signal_date(tmp_path: Path) -> None:
    """`/api/selection` 拼的 pool：is_current 用**快照 signal_date** 判，不用自然日。

    快照 signal_date 改成与池同一天 → pool 就是「当前名单」；这是旧端同口径
    （不回头查 buy_signal.db，零额外 IO）。
    """
    ctx = _sel_ctx(tmp_path)
    snap = dict(_SEL_SNAP, meta=dict(_SEL_SNAP["meta"], signal_date="2026-10-21"))
    _write_panel(ctx, "selection_latest", snap)
    body = _client(ctx).get("/api/selection").json()
    assert body["pool"]["date"] == "2026-10-21"
    assert body["pool"]["is_current"] is True
    # 快照 signal_date 与池不同 → False
    _write_panel(ctx, "selection_latest", _SEL_SNAP)     # signal_date=2026-10-23
    assert _client(ctx).get("/api/selection").json()["pool"]["is_current"] is False


def test_selection_prefs_real(tmp_path: Path) -> None:
    """/api/selection_prefs：读到就原样透传（键集与旧端 selection_filter.json 一致）。"""
    body = _client(_sel_ctx(tmp_path)).get("/api/selection_prefs").json()
    assert set(body) == SEL_FILTER_KEYS
    assert body == json.loads(json.dumps(_SEL_FILTER))     # 深等值、不加不减
    assert body["ma"]["jMax"] == 13
    assert body["sigs"] == {"砖形图": ["ZHU_P", "ZHU_O"]}


def test_selection_skeleton_keys(tmp_path: Path) -> None:
    """三份文件都缺 → 旧端「缺快照分支」骨架（键齐、groups 空、error 有交代）。"""
    body = _client(_ctx(tmp_path)).get("/api/selection").json()
    assert set(body) == {"meta", "date", "error", "asof", "filters", "filter_options",
                         "group_sigs", "group_filters", "board_map", "hot_concepts",
                         "stats", "warnings", "score_warn", "freshness", "pool", "groups"}
    assert set(body["meta"]) == {"schema_version", "document_type", "generated_at",
                                 "source"}
    assert set(body["stats"]) == {"hits", "groups", "scored"}
    assert set(body["freshness"]) == SEL_FRESHNESS_KEYS
    assert body["groups"] == []
    assert body["warnings"] == ["snapshot_missing"]
    assert body["score_warn"] == ["snapshot_missing"]
    assert isinstance(body["error"], str) and body["error"]
    # 快照缺失时 pool 仍是同构骨架（不是 null），前端不必判空
    assert set(body["pool"]) == SEL_POOL_KEYS
    assert body["pool"]["items"] == [] and body["pool"]["slots"] == []


def test_selection_empty_groups_degrades(tmp_path: Path) -> None:
    """groups 为空 = 盘后计算没产出（builder 自检也这么判）→ 走缺快照分支，不谎报「今日无选股」。"""
    ctx = _sel_ctx(tmp_path)
    _write_panel(ctx, "selection_latest", dict(_SEL_SNAP, groups=[], stats={}))
    body = _client(ctx).get("/api/selection").json()
    assert body["groups"] == []
    assert body["warnings"] == ["snapshot_missing"]
    assert "快照未生成" in body["error"]


def test_selection_bad_json_degrades(tmp_path: Path) -> None:
    """三份文件都在、但 JSON 坏了 → 三个端点各自降级，都不抛 500。"""
    ctx = _sel_ctx(tmp_path)
    for name in ("selection_latest", "selection_pool", "selection_filter"):
        (ctx.panel_dir / ("%s.json" % name)).write_text("{坏掉的 JSON", encoding="utf-8")
    client = _client(ctx)
    sel = client.get("/api/selection")
    assert sel.status_code == 200
    assert sel.json()["warnings"] == ["snapshot_missing"]
    pool = client.get("/api/selection_pool").json()
    assert set(pool) == SEL_POOL_KEYS and pool["items"] == []
    assert client.get("/api/selection_prefs").json() == {}


def test_selection_pool_skeleton(tmp_path: Path) -> None:
    body = _client(_ctx(tmp_path)).get("/api/selection_pool?date=1025").json()
    assert set(body) == SEL_POOL_KEYS
    assert body["keep"] == 5
    assert body["date"].endswith("-10-25")               # '1025' → 当年补全
    assert body["items"] == [] and body["slots"] == [] and body["is_current"] is False


def test_selection_prefs_empty_dict(tmp_path: Path) -> None:
    """旧端口径：缺失/不可读 → `{}`（前端用默认），不是 404。"""
    assert _client(_ctx(tmp_path)).get("/api/selection_prefs").json() == {}


def test_selection_freshness_states() -> None:
    """`_freshness` 三态（注入 now，不依赖真日历/真时钟）。

    2026-10-23 是周五（周末见 10-24/10-25），应产时点 18:00。
    """
    def snap(date: str, signal: str = "") -> Dict[str, Any]:
        meta = {"signal_date": signal} if signal else {}
        return {"date": date, "meta": meta}

    fri_1700 = datetime(2026, 10, 23, 17, 0)
    fri_1900 = datetime(2026, 10, 23, 19, 0)

    # 当天未到点 + 快照是今天 → fresh（今天的已产，不算陈旧）
    fresh = backend._freshness(snap("2026-10-23"), fri_1700)
    assert fresh["state"] == "fresh" and fresh["today_pending"] is False
    assert fresh["expected_date"] == "2026-10-23"
    # 当天未到点 + 快照是昨天 → unknown + today_pending（不喊 stale，只提示待生成）；
    # 没有日历 → expected_date 留空（不猜「上一个应产交易日」）
    pend = backend._freshness(snap("2026-10-22"), fri_1700)
    assert pend["state"] == "unknown" and pend["today_pending"] is True
    assert pend["expected_date"] == ""
    # 已过点 + 快照还停昨天 → stale（盘后链没跑），应产日就是今天
    stale = backend._freshness(snap("2026-10-22"), fri_1900)
    assert stale["state"] == "stale" and stale["today_pending"] is False
    assert stale["expected_date"] == "2026-10-23"
    # 已过点 + 快照是今天 → fresh
    assert backend._freshness(snap("2026-10-23"), fri_1900)["state"] == "fresh"
    # meta.signal_date 兜底取快照日
    assert backend._freshness(snap("", "2026-10-23"), fri_1700)["snapshot_date"] == "2026-10-23"
    # 快照日缺失 → unknown（不编造）
    empty = backend._freshness({"date": ""}, fri_1900)
    assert empty["state"] == "unknown" and empty["snapshot_date"] == ""
    assert empty["expected_date"] == ""
    # 周末 → unknown（不猜交易日，不喊假 stale）
    for day in (24, 25):
        wk = backend._freshness(snap("2026-10-22"), datetime(2026, 10, day, 19, 0))
        assert wk["state"] == "unknown" and wk["expected_date"] == ""
    # 键集恒定（前端按固定键渲染，不因分支少键）
    for body in (fresh, pend, stale, empty):
        assert set(body) == SEL_FRESHNESS_KEYS


def test_ops_and_ui_notes_handed_over_to_ops_plugin(tmp_path: Path) -> None:
    """N2（2026-10-24）：`/ops.json` 与 `/ui_notes/list` 已移交 `app/plugins/ops`。

    两个插件都 `mount=root` 到 `/api`，本插件再注册就会 core 启动即报路径冲突，
    所以锁三件事：路由表里没有它们 / 本插件 app 里 404 / 骨架构造已删除。
    """
    ctx = _ctx(tmp_path)
    router = backend.register(ctx)
    registered = {r.path for r in router.routes}
    assert not (registered & MOVED_PATHS_N2), (
        "这些路径已由 app/plugins/ops 接管，本插件不得重复注册: %s"
        % sorted(registered & MOVED_PATHS_N2))
    client = _client(ctx)
    assert client.get("/api/ops.json").status_code == 404
    assert client.get("/api/ui_notes/list").status_code == 404
    for name in ("build_ops", "build_ui_notes", "OPS_SKELETON"):
        assert not hasattr(backend, name), "已移交 ops 插件，本插件不应保留 %s" % name


def test_review_md_handed_over_to_review_plugin(tmp_path: Path) -> None:
    """4.5：`/review.md` 已移交 `app/plugins/review`（真实渲染），本插件必须让路。

    两个插件都 `mount=root` 到 `/api`，本插件再注册就会 core 启动即报路径冲突，
    所以这里锁三件事：路由表里没有它 / 本插件 app 里 404 / 骨架构造已删除。
    """
    ctx = _ctx(tmp_path)
    router = backend.register(ctx)
    registered = {r.path for r in router.routes}
    assert not (registered & MOVED_PATHS_45), (
        "这些路径已由 app/plugins/review 接管，本插件不得重复注册: %s"
        % sorted(registered & MOVED_PATHS_45))
    assert _client(ctx).get("/api/review.md").status_code == 404
    for name in ("build_review_md", "REVIEW_MD_KEYS"):
        assert not hasattr(backend, name), "已移交 review 插件，本插件不应保留 %s" % name


def test_review_chart_handed_over_to_review_plugin(tmp_path: Path) -> None:
    """4.6：`/review_chart.json` 已移交 review 插件（core 装配器），本插件 404。"""
    ctx = _ctx(tmp_path)
    router = backend.register(ctx)
    assert "/review_chart.json" not in {r.path for r in router.routes}
    assert _client(ctx).get("/api/review_chart.json").status_code == 404


# ---------------------------------------------------------------- 5. 静态禁令


def _call_name(node: Any) -> str:
    """`ast.Call.func` → 点分名（不依赖 `ast.unparse`，兼容老解释器）。"""
    import ast
    parts: List[str] = []
    cur: Any = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _code_calls(path: Path) -> List[str]:
    """源码里**所有调用**的点分名（含函数体内的）。"""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [_call_name(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]


def test_no_direct_db_or_quote_usage() -> None:
    code = "\n".join(_code_calls(BACKEND_PY))
    assert find_forbidden_db_usage(code) == []
    assert find_forbidden_quote_usage(code) == []


def test_no_http_or_file_write() -> None:
    """AST 级扫描：本插件的**所有调用**只允许 ctx.* / 纯内置 / fastapi。"""
    import ast
    banned = {"open", "write", "writelines", "makedirs", "mkdir", "dump", "dumps",
              "connect", "urlopen", "run", "Popen", "system", "TdxClient"}
    bad = [n for n in _code_calls(BACKEND_PY) if n.rsplit(".", 1)[-1] in banned]
    assert bad == [], bad
    # 也不许 import 这些模块（只看**模块顶层** import；测试函数内的 import 不算）
    tree = ast.parse(BACKEND_PY.read_text(encoding="utf-8"))
    imported: set = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"sqlite3", "requests", "httpx", "urllib", "subprocess",
                            "socket", "json", "os", "eltdx", "xtquant"}), imported
    assert imported <= {"__future__", "contextlib", "copy", "datetime", "typing",
                        "fastapi", "pydantic"}, imported


def test_only_get_endpoints_via_http(tmp_path: Path) -> None:
    """HTTP 层再确认一遍：GET-only 路径 POST/PUT/DELETE 全部 405。

    `ops.json` / `ui_notes/list` 已移交 `ops` 插件（这里只测本插件自留的只读路径）。
    """
    client = _client(_ctx(tmp_path))
    for path in ("/api/stock_amv/600519.SH", "/api/selection", "/api/sector_amv/881166.TI"):
        for method in ("post", "put", "delete"):
            assert getattr(client, method)(path).status_code == 405, (method, path)


# ---------------------------------------------------------------- 4.7 选股写端点


def _sel_write_ctx(tmp_path: Path) -> FakeCtx:
    panels = {
        "selection_pool": {"meta": {"schema_version": "1.0.0"},
                           "date": "2026-10-21",
                           "items": [{"code": "603629.SH", "name": "利通电子",
                                      "group": "动能", "sig": "MOM",
                                      "added_at": "2026-10-22 00:47:04", "order": 0}]},
        "selection_filter": {"meta": {"schema_version": "1.0.0"}, "pools": ["mainboard"]},
    }
    return _ctx(tmp_path, panels=panels)


def test_selection_prefs_save(tmp_path: Path) -> None:
    ctx = _sel_write_ctx(tmp_path)
    body = _client(ctx).post("/api/selection_prefs",
                             json={"pools": ["chinext"], "boardOn": True}).json()
    assert body["ok"] is True and body["updated_at"]
    assert ctx._panels["selection_filter"]["pools"] == ["chinext"]
    assert ctx._panels["selection_filter"]["boardOn"] is True


def test_selection_prefs_keeps_maBy_and_ntype(tmp_path: Path) -> None:
    """标注 N82：按组记忆（maBy）与 N 型开关（ntype）必须落盘，不能被模型丢掉。"""
    ctx = _sel_write_ctx(tmp_path)
    client = _client(ctx)
    ma_by = {"B1": {"kdjOn": True, "maOn": False, "jMax": 0,
                    "shortOverLong": 0.995, "closeOverLong": 0.995}}
    ntype = {"on": True}
    client.post("/api/selection_prefs", json={"maBy": ma_by, "ntype": ntype})
    assert ctx._panels["selection_filter"]["maBy"] == ma_by
    assert ctx._panels["selection_filter"]["ntype"] == ntype
    back = client.get("/api/selection_prefs").json()
    assert back["maBy"] == ma_by and back["ntype"] == ntype


def test_selection_pool_add_remove(tmp_path: Path) -> None:
    ctx = _sel_write_ctx(tmp_path)
    client = _client(ctx)
    added = client.post("/api/selection_pool/add",
                        json={"code": "000001.SZ", "name": "平安银行",
                              "group": "B1", "sig": "B1_W"}).json()
    assert added["ok"] is True and added["action"] == "added"
    assert added["count"] == 2
    got = client.get("/api/selection_pool").json()
    assert got["count"] == 2
    assert got["items"][0]["code"] == "000001.SZ"   # 新加入排最前
    removed = client.post("/api/selection_pool/remove",
                          json={"code": "603629"}).json()
    assert removed["ok"] is True and removed["removed"] == 1
    assert removed["count"] == 1


# ---------------------------------------------------------------- 选股页 ETF 模式（2026-11-11）
# 主人：入市不满两年只能买主板，实际只能买 ETF —— 选股页加 ETF 模式。
# 契约：只读 etf_snapshot_latest.json，只换单位（元→亿、小数→百分比），不重算任何指标。


def _etf_snap(rows=None, date="20261110"):
    default_rows = [
        {"code": "sh513310", "name": "中韩半导体ETF华", "cat": "跨境", "sub": "跨境",
         "close": 3.575, "pct": 1.91, "amount": 10704912384.0, "ratio": 0.7661,
         "pct5": -2.62, "pct20": -16.98, "above_ma20": -5.17, "dist_hi60": -31.02,
         "split_suspect": False, "tail30_ratio": 0.1346, "premium": None, "share": None},
        {"code": "sz159915", "name": "创业板ETF易方达", "cat": "宽基", "sub": "宽基指数",
         "close": 2.59, "pct": 1.5, "amount": 11386148864.0, "ratio": 0.713,
         "pct5": 4.41, "pct20": -15.64, "above_ma20": -4.1, "dist_hi60": -33.4,
         "split_suspect": False, "tail30_ratio": 0.121},
        {"code": "sh588000", "name": "科创50ETF华夏", "cat": "宽基", "sub": "宽基指数",
         "close": 1.375, "pct": 0.75, "amount": None, "ratio": None,
         "split_suspect": True, "tail30_ratio": None},
    ]
    return {"date": date, "generated_at": "2026-11-10 16:57:42",
            "meta": {"generator": "etf_snapshot.py",
                     "universe": {"etf_total": 1703, "stock": 1620},
                     "warnings": []},
            "fields": ["code"],
            "rows": default_rows if rows is None else rows}


def test_selection_etf_shape_and_units(tmp_path) -> None:
    """ETF 模式：字段齐全、只换单位、成交额降序、分类统计。"""
    ctx = _ctx(tmp_path, panels={"etf_snapshot_latest": _etf_snap()})
    body = _client(ctx).get("/api/selection_etf").json()
    assert body["ok"] is True
    assert body["date"] == "20261110"
    assert body["source"] == "panel:etf_snapshot"
    assert body["stats"]["rows"] == 3
    rows = body["rows"]
    # 成交额降序（缺额沉底）
    assert [r["code6"] for r in rows] == ["159915", "513310", "588000"]
    top = rows[0]
    assert top["code"] == "159915.SZ" and top["cat"] == "宽基"
    assert abs(top["amount_yi"] - 113.86) < 0.01
    assert abs(top["tail30"] - 12.1) < 0.001
    assert abs(top["above_ma20"] - (-4.1)) < 1e-9
    assert {c["key"]: c["n"] for c in body["cats"]} == {"宽基": 2, "跨境": 1}
    # 无源/缺值字段如实透传（不多给、不补 0）
    assert rows[2]["amount_yi"] is None and rows[2]["tail30"] is None
    assert rows[2]["split_suspect"] is True


def test_selection_etf_missing_snapshot(tmp_path) -> None:
    """缺产物 → ok=False + error（不伪造空表让前端看着像「今天没 ETF」）。"""
    ctx = _ctx(tmp_path, panels={})
    body = _client(ctx).get("/api/selection_etf").json()
    assert body["ok"] is False
    assert body["rows"] == [] and body["cats"] == []
    assert "etf_snapshot" in body["error"]


def test_selection_etf_empty_rows_refused(tmp_path) -> None:
    """rows 为空 → 同样按不可用处理（不接受「扫到 0 只还叫成功」）。"""
    ctx = _ctx(tmp_path, panels={"etf_snapshot_latest": _etf_snap(rows=[])})
    body = _client(ctx).get("/api/selection_etf").json()
    assert body["ok"] is False
    assert body["error"]


def test_etf_code_normalization() -> None:
    """源码 sh510300 / 510300.SH / 510300 三种写法 → 同一对 (code6, ts_code)。"""
    for raw in ("sh510300", "510300.SH", "510300", "sz159915", "159915.SZ"):
        code6, ts = backend._etf_code(raw)
        assert len(code6) == 6 and ts.split(".")[0] == code6, raw
    assert backend._etf_code("sh510300") == ("510300", "510300.SH")
    assert backend._etf_code("sz159915") == ("159915", "159915.SZ")
    assert backend._etf_code("") == ("", "")
    assert backend._etf_code("abc") == ("", "")


# ---------------------------------------------------------------- ETF 买点徽标（2026-11-11）


def _mk_sig_db(tmp_path) -> Path:
    """最小 buy_signal.db：stock_signal（个股用）+ etf_signal（本卡用）。"""
    db = tmp_path / "buy_signal.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE stock_signal(stock TEXT PRIMARY KEY, daily_date TEXT, "
                 "daily_sigs TEXT, weekly_date TEXT, weekly_sigs TEXT)")
    conn.execute("CREATE TABLE etf_signal(etf TEXT PRIMARY KEY, daily_date TEXT, daily_sigs TEXT)")
    conn.executemany("INSERT INTO etf_signal VALUES(?,?,?)", [
        ("513310.SH", "20261111", "ZHU_W"),
        ("159915.SZ", "20261111", "B1_W,A_GOLD"),
    ])
    conn.commit()
    conn.close()
    return db


def _sel_snap_for_meta() -> Dict[str, Any]:
    """最小 selection_latest：只给 group_sigs（徽标 label/cls/desc 的唯一源）。"""
    return {"date": "2026-11-11", "groups": [],
            "group_sigs": {
                "B1": [{"code": "B1_W", "label": "B1", "cls": "w", "desc": "B1·低位整理"}],
                "单针": [{"code": "A_GOLD", "label": "针", "cls": "y", "desc": "黄金针"}],
                "砖": [{"code": "ZHU_W", "label": "砖", "cls": "zhu-w",
                        "desc": "砖形图·反转确立（个股｜砖体由绿转红且力度达标）"}],
            }}


def test_selection_etf_carries_badges(tmp_path) -> None:
    """ETF 行的买点徽标：信号来自 etf_signal，label/cls/desc 来自快照 group_sigs（不另拄一份）。"""
    ctx = _ctx(tmp_path, panels={"etf_snapshot_latest": _etf_snap(),
                                 "selection_latest": _sel_snap_for_meta()},
               sig_db=_mk_sig_db(tmp_path))
    body = _client(ctx).get("/api/selection_etf").json()
    assert body["sig_n"] == 2
    assert set(body["sig_meta"]) == {"B1_W", "A_GOLD", "ZHU_W"}
    rows = {r["code"]: r for r in body["rows"]}
    assert [b["label"] for b in rows["159915.SZ"]["badges"]] == ["B1", "针"]
    assert rows["513310.SH"]["badges"][0]["label"] == "砖"
    assert rows["513310.SH"]["badges"][0]["cls"] == "zhu-w"
    # 文案加「ETF（同个股公式）」前缀：不让人误以为是 ETF 专用指标
    assert rows["513310.SH"]["badges"][0]["desc"].startswith("ETF（同个股公式）")
    # 无信号的行不带 badges 键（payload 纪律：1,500 行不给空数组）
    assert "badges" not in rows["588000.SH"]


def test_selection_etf_without_sig_db_degrades(tmp_path) -> None:
    """买点库不可用 → 选股表照常出，只是没徽标（不报错、不挡用）。"""
    ctx = _ctx(tmp_path, panels={"etf_snapshot_latest": _etf_snap()})
    body = _client(ctx).get("/api/selection_etf").json()
    assert body["ok"] is True and len(body["rows"]) == 3
    assert body["sig_n"] == 0
    assert all("badges" not in r for r in body["rows"])


# ---------------------------------------------------------------- 盘中量比（2026-11-13）


def test_live_vol_ratio_time_corrected_and_close_parity() -> None:
    """盘中量比 = 时间校正；15:00 退化回「当日量/前20日均量」（与盘后快照同口径）。"""
    # (100 万手 / 120 分钟) ÷ (40 万手 / 240) = 5.0
    assert backend._live_vol_ratio(1_000_000, 400_000, 120) == 5.0
    # 收盘 em=240 → 退化为 100 万 / 40 万 = 2.5（盘后快照同一式子）
    assert backend._live_vol_ratio(1_000_000, 400_000, 240) == 2.5
    assert backend._live_vol_ratio(0, 400_000, 120) is None
    assert backend._live_vol_ratio(1000, 0, 120) is None
    assert backend._live_vol_ratio(1000, 400_000, 0) is None
    assert backend._live_vol_ratio(None, 400_000, 120) is None


def test_vol_avg20_excludes_today_bar(tmp_path) -> None:
    """分母 = 前 20 个交易日（不含今日）；盘中 eltdx 多出的今日 bar 不能算进分母。"""
    from datetime import timedelta

    backend._VOL20_CACHE["day"] = ""
    backend._VOL20_CACHE["map"] = {}
    today = datetime.now().strftime("%Y%m%d")
    bars = []
    for i in range(20):
        d = (datetime.now() - timedelta(days=20 - i)).strftime("%Y%m%d")
        bars.append(_Bar(d, 10.0, lots=100.0))
    bars.append(_Bar(today, 10.0, lots=9999.0))      # 今日未收盘 bar（必须剔掉）
    ctx = FakeCtx(tmp_path, quotes=_FakeQuotes(daily={"sh600000": bars}))
    avg = backend._vol_avg20(ctx, ["sh600000"])
    assert abs(avg["sh600000"] - 100.0) < 1e-9      # 若把今日算进分母会变成 ~571

    # 可用样本不足 20 日 → 不给（不编造）
    backend._VOL20_CACHE["day"] = ""
    backend._VOL20_CACHE["map"] = {}
    ctx2 = FakeCtx(tmp_path, quotes=_FakeQuotes(daily={"sh600000": bars[:-2]}))
    assert "sh600000" not in backend._vol_avg20(ctx2, ["sh600000"])
