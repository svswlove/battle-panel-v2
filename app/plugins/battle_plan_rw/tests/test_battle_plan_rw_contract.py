# -*- coding: utf-8 -*-
"""battle_plan_rw 契约测试：manifest / 路由 / 静态禁令。

行为测试在 `test_battle_plan_rw_write.py`（真 `PluginContext` + tmp 数据目录）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import (  # noqa: E402
    find_forbidden_db_usage,
    find_forbidden_quote_usage,
    load_plugin,
    validate_manifest,
)

PLUGIN_DIR = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN_DIR / "plugin.json"
BACKEND_PY = PLUGIN_DIR / "backend.py"

EXPECTED_ROUTES = {
    "/kill", "/toggle", "/cancel",
    "/signal_watch/toggle", "/signal_watch/cancel", "/manual_position",
    # 2026-10-24 M1 C1：模块开关批量提交（旧 server.py:7400，前端 index.html:9071）
    "/module_switches_batch",
}

MODULE_KEYS = ("entry", "exit_stop", "exit_profit", "do_t", "notify",
               "sizing", "awake", "vol_ratio_buy", "alert")

# 最小作战表/状态文件（只为验路由归属，含义与行为测试同一套字段）
PLAN = {
    "meta": {"trade_date": "2026-10-24", "generated_at": "2026-10-24 01:00:00"},
    "sign": {"status": "UNSIGNED", "signed_at": None, "signer": None, "note": "待签收"},
    "position_cap": 100000.0,
    "module_switches": {k: True for k in MODULE_KEYS},
    "buy_plan": [{"plan_id": "BP-1", "ts_code": "600000.SH", "status": "PENDING"}],
    "operations": [],
}
ENGINE_STATE = {"state": "READY", "mode": "READ_ONLY",
                "trade_mode": "paper"}
KILL_SWITCH = {"active": False, "triggered_at": None, "reason": "解除急停", "history": []}


def _manifest_data() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_valid_and_root_mount() -> None:
    m = validate_manifest(_manifest_data(), PLUGIN_DIR)
    assert m.id == "battle_plan_rw"
    assert m.backend.mount == "root", "旧前端直调根路径，必须 root 挂载"
    assert m.enabled is True


def test_manifest_whitelists() -> None:
    m = validate_manifest(_manifest_data(), PLUGIN_DIR)
    assert m.data_write == ("battle_plan",)
    assert set(m.state_write) == {"engine_state", "kill_switch"}
    # dashboard 只用于 manual_position 的总资产兜底（只读）
    assert set(m.panel_read) == {"battle_plan", "dashboard"}
    assert m.data_read == () and m.json_read == () and m.quotes_read is False


def test_routes_are_root_paths_without_api_prefix() -> None:
    loaded = load_plugin(PLUGIN_DIR)
    got = {r.path for r in loaded.router.routes}
    assert got == EXPECTED_ROUTES
    for route in loaded.router.routes:
        assert route.methods == {"POST"}, "写域只开 POST"
        assert not route.path.startswith("/api"), "前缀由 core 拼（mount=root → /api）"


def test_registered_by_core_under_api_prefix(tmp_path: Path) -> None:
    """真应用里各路由最终落在 `/api/<path>`（mount=root 的拼接由 core 负责）。"""
    import shutil

    from app.core.main import create_app

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    target = plugins / "battle_plan_rw"
    target.mkdir()
    for name in ("plugin.json", "backend.py"):
        shutil.copyfile(PLUGIN_DIR / name, target / name)
    web = tmp_path / "web"
    web.mkdir()
    app = create_app(plugins_dir=plugins, web_dir=web)
    # FastAPI 0.141 把 include_router 收成 _IncludedRouter（app.routes 不再扁平），
    # 用 openapi 看真实注册路径（含前缀）。
    registered = set(app.openapi()["paths"])
    for path in EXPECTED_ROUTES:
        assert "/api" + path in registered, path


def test_no_forbidden_db_or_quote_usage() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    assert find_forbidden_db_usage(src) == []
    assert find_forbidden_quote_usage(src) == []


def test_no_http_subprocess_or_own_file_write() -> None:
    """写盘只许走 ctx.panel_write / ctx.state_write；门禁口径与 cutover_gate 一致。"""
    src = BACKEND_PY.read_text(encoding="utf-8")
    for token in ("import requests", "import httpx", "urllib", "socket", "subprocess",
                  "os.system(", "json.dump(", ".write_text(", ".write_bytes(",
                  "os.replace(", ".mkdir("):
        assert token not in src, token
    # open() 只允许只读（state 读兜底）
    import re

    assert not re.search(r"open\s*\([^)]{0,240}?,\s*[\"'](?:w|a|wb|ab|x|xb)[\"']", src)


def test_write_channels_are_the_only_writers() -> None:
    src = BACKEND_PY.read_text(encoding="utf-8")
    # 取通道一律经 getattr(ctx, ...)：既对齐 core 的 ctx 面，也不给静态扫描留误报
    for token in ('"panel_write"', '"state_write"', '"panel_json"', '"state_json"'):
        assert token in src, token


@pytest.mark.parametrize("name", ["engine_state", "kill_switch"])
def test_state_write_requires_manifest_declaration(name: str) -> None:
    from app.core.contract import PluginContext

    m = validate_manifest(_manifest_data(), PLUGIN_DIR)
    ctx = PluginContext(m)
    assert name in m.state_write
    # 未声明的名字一律 PermissionError（这里验证插件侧只声明了这两个）
    assert set(m.state_write) == {"engine_state", "kill_switch"}
    assert ctx.id == "battle_plan_rw"
