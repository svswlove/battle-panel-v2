# -*- coding: utf-8 -*-
"""state_tree 插件契约测试。

覆盖四件事：
  1. manifest 通过 core 校验；
  2. 口径库白名单 export 齐全（`ctx.lib` 只拿到纯函数，拿不到取数入口）；
  3. 四态（未达成 / 收盘确认 / 作废 / 否决）在真实 `evaluate()` 上逐态可复现；
  4. 端点装配：**不转发 8800、不 mock** —— 实时不可用时如实落收盘口径并写明原因。
"""
import datetime as _dt
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.state_tree import backend  # noqa: E402
from app.core.contract import ContractError  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 口径库（真实加载，走同一白名单）

def _load_lib():
    spec_info = paths.lib_spec("amv_state_tree")
    assert spec_info is not None, "amv_state_tree 未登记进 READABLE_LIB"
    spec = importlib.util.spec_from_file_location("_test_amv_state_tree", spec_info.path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_amv_state_tree"] = module
    sys.path.insert(0, str(spec_info.path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(spec_info.path.parent))
    return module


LIB = _load_lib()


def _dates(n):
    base = _dt.date(2026, 8, 1)
    return [(base + _dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class _Bar:
    def __init__(self, date, o, h, lo, c):
        self.time = _dt.datetime.strptime(date, "%Y-%m-%d")
        self.open_price, self.high_price = o, h
        self.low_price, self.close_price = lo, c


class _Quotes:
    def __init__(self, bars=None):
        self._bars = bars or []

    def daily_bars(self, glb, count=8, adjust=None):
        return self._bars


class _AmvLive:
    def __init__(self, doc=None, err=None):
        self._doc, self._err = doc, err

    def snapshot(self):
        return self._doc

    def last_error(self):
        return self._err


class FakeCtx:
    def __init__(self, amv, bars=None, snap=None, snap_err=None, panel=None):
        self._amv = amv
        self._rows = [(d, float(a) * 1e8) for d, a in zip(_dates(len(amv)), amv)]
        self.quotes = _Quotes(bars)
        self.amv_live = _AmvLive(snap, snap_err)
        self._panel = panel or {}

    def db_read(self, name):
        # 每次给一条**同线程新建**的内存库（TestClient 在 worker 线程调路由）
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE daily_total (date TEXT PRIMARY KEY, total REAL)")
        conn.executemany("INSERT INTO daily_total VALUES (?, ?)", self._rows)
        conn.commit()
        return conn

    def panel_json(self, name):
        return self._panel.get(name, {})

    def lib(self, name):
        assert name == "amv_state_tree"
        import types
        spec_info = paths.lib_spec(name)
        # 用 SimpleNamespace（同 core `ctx.lib` 的做法）：函数不会变成绑定方法
        return types.SimpleNamespace(**{sym: getattr(LIB, sym) for sym in spec_info.exports})


def _client(ctx):
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api/p/state_tree")
    return TestClient(app)


# ---------------------------------------------------------------- 1. manifest

def test_manifest_valid():
    with open(PLUGIN_DIR / "plugin.json", encoding="utf-8") as f:
        raw = json.load(f)
    m = validate_manifest(raw, PLUGIN_DIR)
    assert m.id == "state_tree"
    assert m.data_read == ("amv_board.db",)
    assert m.lib_read == ("amv_state_tree",)
    assert m.quotes_read is True and m.amv_live is True
    assert m.frontend.cards[0].id == "state_tree.main"


def test_lib_exports_are_pure_only():
    """白名单里不能出现自带取数入口（build_payload / load_daily / load_bars）。"""
    exports = set(paths.lib_spec("amv_state_tree").exports)
    for banned in ("build_payload", "load_daily", "load_bars", "load_today_live"):
        assert banned not in exports, f"{banned} 是取数入口，不得暴露给插件"
    for sym in exports:
        assert hasattr(LIB, sym), f"声明的导出 {sym} 在口径库中不存在"


# ---------------------------------------------------------------- 2. 四态

def test_state_miss_idle():
    amv = [100.0] * 30
    tree = LIB.evaluate(_dates(30), amv, {})
    assert tree["days"][-1]["result"] == LIB.R_IDLE
    assert tree["days"][-1]["trigger"]["state"] == LIB.S_MISS


def test_state_close_confirmed():
    amv = [100.0] * 20 + [104.0, 106.0] + [107.0] * 5
    tree = LIB.evaluate(_dates(27), amv, {})
    day21 = tree["days"][21]
    assert day21["deadline"]["state"] == LIB.S_CLOSE
    assert day21["result"] == LIB.R_LIT
    assert "T+1" in (day21["deadline"]["checks"][0]["t"])


def test_state_live_intraday():
    """盘中标为「盘中达成」（live=True）：末根是未收盘的实时值，不得落「收盘确认」。

    本条只能单测覆盖：收盘后 `amv_live` 恒不可用，拿不到真盘中帧。
    """
    amv = [100.0] * 20 + [104.0, 105.0]
    tree = LIB.evaluate(_dates(22), amv, {}, live=True)
    day21 = tree["days"][21]
    assert day21["deadline"]["state"] == LIB.S_LIVE
    assert day21["result"] == LIB.R_LIT
    assert tree["days"][20]["trigger"]["state"] == LIB.S_CLOSE   # 触发日不是末根，不打盘中态


def test_state_void_grey():
    """触发后 T+1/T+2 收盘都没站上 MA10 → 作废（灰）。"""
    amv = [100.0] * 20 + [104.0, 70.0, 49.0] + [49.0] * 3
    tree = LIB.evaluate(_dates(26), amv, {})
    day22 = tree["days"][22]
    assert day22["deadline"]["state"] == LIB.S_VOID
    assert day22["result"] == LIB.R_VOID


def test_state_veto_pattern():
    """形态明显（长下影）→ 否决 → 不点亮。"""
    amv = [100.0] * 20 + [104.0] + [105.0] * 4
    ds = _dates(25)
    bars = {ds[20].replace("-", ""): {"open": 105.0, "high": 106.0, "low": 90.0, "close": 104.0}}
    tree = LIB.evaluate(ds, amv, bars)
    day20 = tree["days"][20]
    assert day20["pattern"]["state"] == LIB.S_VETO
    assert day20["result"] == LIB.R_NO_LIGHT


# ---------------------------------------------------------------- 3. 端点装配

def test_endpoint_closed_phase_no_mock():
    amv = [100.0] * 30
    bars = [_Bar(d, 100.0, 101.0, 99.0, 100.0) for d in _dates(30)]
    ctx = FakeCtx(amv, bars=bars, snap=None, snap_err="empty")
    r = _client(ctx).get("/api/p/state_tree/tree")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["phase"] == "close"                       # 实时不可用 → 收盘口径
    assert body["source"]["live"] == "empty"              # 原因如实回传，不伪造盘中值
    assert body["asof"] == _dates(30)[-1]
    assert body["bull"]["result"] == LIB.R_IDLE
    assert len(body["days"]) <= backend.DAYS_TAIL


def _runtime_strings(src):
    """取出**运行期**字符串常量（跳过模块/函数/类的 docstring）。"""
    import ast
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_endpoint_no_8800_no_httplib():
    """静态扫描：运行期不得出现旧端口转发 / 外部 HTTP / mock。"""
    import ast
    src = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert not ({"urllib", "requests", "http", "httpx"} & mods), f"backend.py 引入了外部 HTTP: {mods}"
    for text in _runtime_strings(src):
        for banned in (":8800", "127.0.0.1", "mock"):
            assert banned not in text, f"backend.py 运行期字符串含 {banned}: {text[:60]}"
    fe = (PLUGIN_DIR / "frontend.js").read_text(encoding="utf-8")
    assert ":8800" not in fe and "127.0.0.1" not in fe


def test_days_endpoint_tail():
    amv = [100.0] * 30
    ctx = FakeCtx(amv, bars=[])
    r = _client(ctx).get("/api/p/state_tree/days?n=5")
    assert r.status_code == 200
    assert len(r.json()["days"]) == 5
