# -*- coding: utf-8 -*-
"""dashboard 插件契约测试（薄壳后端 + manifest）。

守的是 docs/task_fe_batch1b.md §契约：
  · manifest：kind=domain、卡片 dashboard.main/order=5、data 白名单全空（不读 panel、不写）
  · backend 薄壳：只有 GET /health；无写路由；不碰 sqlite / xtquant / urllib / 写盘
  · 数据面全在前端 ctx.api（root 挂载的 /api/*），本插件不自建第二份账本口径
"""
import json
import re
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.dashboard import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
FRONTEND_JS = PLUGIN_DIR / "frontend.js"


def _manifest():
    with open(PLUGIN_DIR / "plugin.json", encoding="utf-8") as f:
        return json.load(f)


def _client():
    app = FastAPI()
    app.include_router(backend.register(None), prefix="/api/p/dashboard")
    return TestClient(app)


# ---------------------------------------------------------------- manifest


def test_manifest_valid():
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.id == "dashboard"
    assert m.kind == "domain"
    assert m.enabled is True
    assert m.backend.mount == "plugin", "本插件不挂 root：数据全在前端 ctx.api 绝对路径"


def test_manifest_card():
    cards = _manifest()["frontend"]["cards"]
    assert [c["id"] for c in cards] == ["dashboard.main"]
    assert cards[0]["title"] == "仪表盘"
    assert cards[0]["order"] == 5


def test_manifest_reads_nothing():
    """薄壳：不声明 panel/json/lib/db 读取，也没有写白名单。"""
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.panel_read == ()
    assert m.json_read == ()
    assert m.lib_read == ()
    assert m.data_read == ()
    assert m.data_write == ()
    assert m.text_read == ()


def test_backend_no_forbidden_io():
    """插件源码不得直连库/行情/写盘（CONTRACT §4）。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("sqlite3.connect", "xtquant", "xtdata", "urllib.request.urlopen",
                  "requests.", "subprocess.", "json.dump(", ".write_text(", "open("):
        assert token not in src, "backend.py 命中禁用写法：%s" % token


# ---------------------------------------------------------------- 路由


def test_register_returns_router():
    router = backend.register(None)
    assert isinstance(router, APIRouter)
    assert [r.path for r in router.routes] == ["/health"]


def test_health_shape():
    body = _client().get("/api/p/dashboard/health").json()
    assert body["ok"] is True
    assert body["plugin"] == "dashboard"
    assert body["card"] == "dashboard.main"
    assert body["read_only"] is True
    assert body["backend"] == "thin-shell"
    assert body["write_ops"] == []
    assert body["refresh_every_ms"] == 3000


def test_health_has_no_data_endpoints():
    """薄壳不该自建账本/仪表盘端点（那是 paper / compat 的 owner）。"""
    paths = [r.path for r in backend.register(None).routes]
    for banned in ("/ledger", "/dashboard.json", "/gates", "/status"):
        assert banned not in paths, "薄壳不应注册 %s" % banned


def test_no_write_route():
    """全 GET：POST /health 必须 405（与 battle_plan「永久只有 GET」同口径）。"""
    assert _client().post("/api/p/dashboard/health").status_code == 405


# ---------------------------------------------------------------- 前端一致性


def test_sources_match_frontend():
    """backend 的 DATA_SOURCES 必须与 frontend.js 的 DATA_SOURCES 逐条一致。"""
    js = FRONTEND_JS.read_text(encoding="utf-8")
    for src in backend.DATA_SOURCES:
        assert src in js, "frontend.js 未消费数据源 %s" % src


def test_frontend_declares_same_card():
    js = FRONTEND_JS.read_text(encoding="utf-8")
    assert "id: 'dashboard.main'" in js
    assert re.search(r"refreshEvery:\s*REFRESH_MS", js), "刷新周期应由 REFRESH_MS 常量驱动"
    assert re.search(r"const REFRESH_MS = 3000;", js), "任务书 §功能 8：3s tick"
