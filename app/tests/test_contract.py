"""core 契约测试：健康、清单、空目录、坏 manifest、db_read 白名单/只读。

不写生产插件目录、不写 data/panel、不碰生产库（只读断言用临时 sqlite）。
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import contract, paths  # noqa: E402
from app.core.contract import (  # noqa: E402
    ContractError,
    PluginContext,
    find_forbidden_db_usage,
    find_forbidden_lib_usage,
    load_manifest,
    load_plugin,
    validate_manifest,
    version_cmp,
)
from app.core.main import create_app  # noqa: E402

MINIMAL_BACKEND = '''
from fastapi import APIRouter


def register(ctx):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"pong": True, "id": ctx.id}

    return router
'''

MINIMAL_FRONTEND = '''
export default {
  cards: [{ id: "demo.card", title: "演示", slot: "main", order: 1,
    async mount(el) { el.textContent = "demo"; },
    async unmount() {} }]
};
'''


def write_plugin(
    root: Path,
    plugin_id: str = "demo",
    *,
    manifest_overrides: dict | None = None,
    drop: tuple[str, ...] = (),
    backend: str | None = MINIMAL_BACKEND,
    frontend: str | None = MINIMAL_FRONTEND,
) -> Path:
    """在 root/<plugin_id>/ 落一个最小插件；返回目录。"""
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)

    manifest = {
        "manifest_version": 1,
        "id": plugin_id,
        "name": "演示插件",
        "version": "0.1.0",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "domain",
        "backend": {"module": "backend", "entry": "register", "timeout_ms": 5000, "permissions": []},
        "frontend": {
            "module": "frontend.js",
            "cards": [{"id": "demo.card", "title": "演示", "slot": "main", "order": 1}],
        },
        "data": {"read": [], "write": []},
        "config": {},
        "depends": [],
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    for key in drop:
        manifest.pop(key, None)

    (directory / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if backend is not None:
        (directory / "backend.py").write_text(backend, encoding="utf-8")
    if frontend is not None:
        (directory / "frontend.js").write_text(frontend, encoding="utf-8")
    return directory


@pytest.fixture()
def empty_plugins(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    return root


@pytest.fixture()
def web_dir(tmp_path: Path) -> Path:
    """最小静态壳，避免依赖真实 app/web 内容。"""
    directory = tmp_path / "web"
    directory.mkdir()
    (directory / "shell.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (directory / "shell.js").write_text("// stub", encoding="utf-8")
    (directory / "shell.css").write_text("/* stub */", encoding="utf-8")
    return directory


# ---------------------------------------------------------------- 健康/清单


def test_health_ok_with_empty_plugins(empty_plugins: Path, web_dir: Path):
    app = create_app(plugins_dir=empty_plugins, web_dir=web_dir)
    with TestClient(app) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["version"] == contract.CORE_VERSION
    assert body["plugins"] == []


def test_plugins_listing_empty(empty_plugins: Path, web_dir: Path):
    app = create_app(plugins_dir=empty_plugins, web_dir=web_dir)
    with TestClient(app) as client:
        res = client.get("/_plugins")
        health = client.get("/_plugins/health")
    assert res.status_code == 200
    assert res.json()["plugins"] == []
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["plugins"] == []


def test_root_serves_shell(empty_plugins: Path, web_dir: Path):
    app = create_app(plugins_dir=empty_plugins, web_dir=web_dir)
    with TestClient(app) as client:
        res = client.get("/")
        static = client.get("/static/shell.js")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert static.status_code == 200


def test_missing_plugins_dir_still_boots(tmp_path: Path, web_dir: Path):
    app = create_app(plugins_dir=tmp_path / "nope", web_dir=web_dir)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/_plugins").status_code == 200


# ---------------------------------------------------------------- 正常加载


def test_plugin_mounts_under_api_p(tmp_path: Path, web_dir: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    write_plugin(root)

    app = create_app(plugins_dir=root, web_dir=web_dir)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        listing = client.get("/_plugins").json()
        ping = client.get("/api/p/demo/ping")
        asset = client.get("/plugins/demo/frontend.js")
        forbidden = client.get("/plugins/demo/backend.py")

    assert health["plugins"] == ["demo"]
    assert [p["id"] for p in listing["plugins"]] == ["demo"]
    assert listing["plugins"][0]["frontend"]["cards"][0]["id"] == "demo.card"
    assert ping.status_code == 200
    assert ping.json() == {"pong": True, "id": "demo"}
    assert asset.status_code == 200
    assert "export default" in asset.text
    assert forbidden.status_code == 404


# ---------------------------------------------------------------- 坏 manifest


@pytest.mark.parametrize(
    "overrides,drop,fragment",
    [
        ({}, ("name",), "name"),
        ({"manifest_version": 2}, (), "manifest_version"),
        ({"id": "other"}, (), "目录名"),
        ({"min_core_version": "9.9.9"}, (), "min_core_version"),
        ({"data": {"read": [], "write": ["x.db"]}}, (), "data.write"),
        ({"data": {"read": ["evil.db"], "write": []}}, (), "白名单"),
        ({"data": {"read": [], "panel": ["evil_panel"], "write": []}}, (), "data.panel"),
        ({"backend": {"module": "backend", "timeout_ms": 0}}, (), "timeout_ms"),
        ({"frontend": {"module": "frontend.js", "cards": [{"id": "a.b"}]}}, (), "title"),
    ],
)
def test_bad_manifest_rejected(tmp_path: Path, overrides, drop, fragment):
    directory = write_plugin(tmp_path / "plugins", manifest_overrides=overrides, drop=drop)
    with pytest.raises(ContractError) as excinfo:
        load_manifest(directory)
    assert fragment in str(excinfo.value)


def test_bad_manifest_does_not_crash_core(tmp_path: Path, web_dir: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    write_plugin(root, "good")
    write_plugin(root, "broken", drop=("version",))

    app = create_app(plugins_dir=root, web_dir=web_dir)
    with TestClient(app) as client:
        health = client.get("/api/health")
        plugins_health = client.get("/_plugins/health").json()
        listing = client.get("/_plugins").json()
        good = client.get("/api/p/good/ping")

    assert health.status_code == 200
    assert health.json()["plugins"] == ["good"]
    assert good.status_code == 200

    by_id = {p["id"]: p for p in plugins_health["plugins"]}
    assert by_id["good"]["ok"] is True
    assert by_id["broken"]["ok"] is False
    assert "version" in by_id["broken"]["error"]
    assert plugins_health["ok"] is False
    assert [e["id"] for e in listing["errors"]] == ["broken"]


def test_register_must_return_router(tmp_path: Path):
    directory = write_plugin(tmp_path / "plugins", backend="def register(ctx):\n    return {'bad': True}\n")
    with pytest.raises(ContractError) as excinfo:
        load_plugin(directory)
    assert "APIRouter" in str(excinfo.value)


def test_backend_import_error_isolated(tmp_path: Path, web_dir: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    write_plugin(root, "boom", backend="raise RuntimeError('kaboom')\n")
    write_plugin(root, "good")

    app = create_app(plugins_dir=root, web_dir=web_dir)
    with TestClient(app) as client:
        plugins_health = client.get("/_plugins/health").json()
    by_id = {p["id"]: p for p in plugins_health["plugins"]}
    assert by_id["boom"]["ok"] is False
    assert "kaboom" in by_id["boom"]["error"]
    assert by_id["good"]["ok"] is True


def test_disabled_plugin_not_mounted(tmp_path: Path, web_dir: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    write_plugin(root, "off", manifest_overrides={"enabled": False})

    app = create_app(plugins_dir=root, web_dir=web_dir)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["plugins"] == []
        assert client.get("/api/p/off/ping").status_code == 404
        health = client.get("/_plugins/health").json()["plugins"][0]
    assert health["ok"] is True
    assert health["enabled"] is False


def test_plugin_error_wrapped(tmp_path: Path, web_dir: Path):
    backend = '''
from fastapi import APIRouter


def register(ctx):
    router = APIRouter()

    @router.get("/boom")
    def boom():
        raise ValueError("bad input")

    return router
'''
    root = tmp_path / "plugins"
    root.mkdir()
    write_plugin(root, "err", backend=backend)

    app = create_app(plugins_dir=root, web_dir=web_dir)
    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.get("/api/p/err/boom")
    assert res.status_code == 500
    assert res.json()["error"] == "plugin:err"


def test_listing_hides_sensitive_config(tmp_path: Path, web_dir: Path):
    root = tmp_path / "plugins"
    root.mkdir()
    write_plugin(
        root,
        manifest_overrides={"config": {"api_key": "s3cret", "theme": "dark", "nested": {"token": "t"}}},
    )
    app = create_app(plugins_dir=root, web_dir=web_dir)
    with TestClient(app) as client:
        text = client.get("/_plugins").text
    assert "s3cret" not in text
    assert "dark" in text


# ---------------------------------------------------------------- db_read


@pytest.fixture()
def sandbox_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把白名单库指到 tmp：tmp/amv_board.db 等，不碰生产库。"""
    data = tmp_path / "data"
    data.mkdir()
    for name in ("amv_board.db", "ths_board.db", "track_pool.db"):
        conn = sqlite3.connect(data / name)
        conn.execute("CREATE TABLE t (v INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()
    monkeypatch.setattr(
        paths,
        "READABLE_DB",
        {name: data / name for name in ("amv_board.db", "ths_board.db", "track_pool.db")},
    )
    return data


def _ctx(
    data_read: tuple[str, ...] = ("amv_board.db",),
    panel_read: tuple[str, ...] = (),
) -> PluginContext:
    manifest = validate_manifest(
        {
            "manifest_version": 1,
            "id": "demo",
            "name": "演示",
            "version": "0.1.0",
            "min_core_version": "0.1.0",
            "backend": {"module": "backend"},
            "data": {"read": list(data_read), "panel": list(panel_read), "write": []},
        },
        Path("demo"),
    )
    return PluginContext(manifest)


def test_db_read_works_and_is_readonly(sandbox_data: Path):
    conn = _ctx().db_read("amv_board.db")
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == 42
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES (1)")
    finally:
        conn.close()


def test_db_read_rejects_undeclared(sandbox_data: Path):
    with pytest.raises(PermissionError):
        _ctx(("amv_board.db",)).db_read("ths_board.db")


def test_db_read_rejects_non_whitelisted_name(sandbox_data: Path):
    ctx = _ctx()
    manifest = dataclasses.replace(ctx.manifest, data_read=("evil.db",))
    rogue = PluginContext(manifest)
    with pytest.raises(PermissionError):
        rogue.db_read("evil.db")


def test_db_read_missing_file(sandbox_data: Path):
    (sandbox_data / "track_pool.db").unlink()
    with pytest.raises(FileNotFoundError):
        _ctx(("track_pool.db",)).db_read("track_pool.db")


@pytest.fixture()
def sandbox_panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    panel = tmp_path / "panel"
    panel.mkdir()
    (panel / "amv_regime_latest.json").write_text(
        json.dumps({"date": "2026-10-21", "state": "空", "ma10": 143013.53}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "READABLE_PANEL", {"amv_regime_latest": panel / "amv_regime_latest.json"})
    return panel


@pytest.fixture()
def sandbox_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """其他生产 JSON 白名单（data.json）指向 tmp。"""
    root = tmp_path / "json"
    root.mkdir()
    target = root / "names.json"
    target.write_text(json.dumps({"names": {"600000": "浦发银行"}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(paths, "READABLE_JSON", {"stock_names": target})
    return target


def test_panel_json_works(sandbox_panel: Path):
    data = _ctx(panel_read=("amv_regime_latest",)).panel_json("amv_regime_latest")
    assert data["state"] == "空"
    assert data["ma10"] == 143013.53


def test_panel_json_rejects_undeclared(sandbox_panel: Path):
    with pytest.raises(PermissionError):
        _ctx().panel_json("amv_regime_latest")


def test_panel_json_rejects_non_whitelisted_name(sandbox_panel: Path):
    ctx = _ctx(panel_read=("amv_regime_latest",))
    manifest = dataclasses.replace(ctx.manifest, panel_read=("evil_panel",))
    with pytest.raises(PermissionError):
        PluginContext(manifest).panel_json("evil_panel")


def test_panel_json_missing_file(sandbox_panel: Path):
    (sandbox_panel / "amv_regime_latest.json").unlink()
    with pytest.raises(FileNotFoundError):
        _ctx(panel_read=("amv_regime_latest",)).panel_json("amv_regime_latest")


def test_data_dir_is_panel_v2_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "OUT_DIR", tmp_path / "out")
    ctx = _ctx()
    assert ctx.data_dir == tmp_path / "out" / "demo"
    assert ctx.data_dir.is_dir()


# ---------------------------------------------------------------- data.json（非 panel 生产 JSON）


def _ctx_json(json_read: tuple[str, ...]) -> PluginContext:
    manifest = validate_manifest(
        {
            "manifest_version": 1,
            "id": "demo",
            "name": "演示",
            "version": "0.1.0",
            "min_core_version": "0.1.0",
            "backend": {"module": "backend"},
            "data": {"read": [], "json": list(json_read), "write": []},
        },
        Path("demo"),
    )
    return PluginContext(manifest)


def test_json_read_works(sandbox_json: Path):
    data = _ctx_json(("stock_names",)).json_read("stock_names")
    assert data["names"]["600000"] == "浦发银行"


def test_json_read_rejects_undeclared(sandbox_json: Path):
    with pytest.raises(PermissionError):
        _ctx_json(()).json_read("stock_names")


def test_json_read_rejects_non_whitelisted_name(sandbox_json: Path):
    ctx = _ctx_json(("stock_names",))
    manifest = dataclasses.replace(ctx.manifest, json_read=("evil_json",))
    with pytest.raises(PermissionError):
        PluginContext(manifest).json_read("evil_json")


def test_json_read_missing_file(sandbox_json: Path):
    sandbox_json.unlink()
    with pytest.raises(FileNotFoundError):
        _ctx_json(("stock_names",)).json_read("stock_names")


# ---------------------------------------------------------------- data.lib（口径库）

PURE_LIB = '''
"""纯函数库：只算数学，不碰数据源。"""


def half(x):
    return x / 2
'''

DIRTY_LIB = '''
import sqlite3

CONN = sqlite3.connect("x.db")


def half(x):
    return x / 2
'''

# 函数体内的连库不算 import 期副作用；__main__ 自检块也不算。
SAFE_WITH_CONNECTION_IN_FUNC = '''
import sqlite3


def half(x):
    conn = sqlite3.connect("x.db")
    return x / 2


if __name__ == "__main__":
    sqlite3.connect("y.db")
'''


@pytest.fixture()
def sandbox_lib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "lib"
    root.mkdir()
    good = root / "pure_lib.py"
    good.write_text(PURE_LIB, encoding="utf-8")
    monkeypatch.setattr(
        paths, "READABLE_LIB", {"pure_lib": paths.LibSpec(path=good, exports=("half",))}
    )
    return good


def _ctx_lib(lib_read: tuple[str, ...]) -> PluginContext:
    manifest = validate_manifest(
        {
            "manifest_version": 1,
            "id": "demo",
            "name": "演示",
            "version": "0.1.0",
            "min_core_version": "0.1.0",
            "backend": {"module": "backend"},
            "data": {"read": [], "lib": list(lib_read), "write": []},
        },
        Path("demo"),
    )
    return PluginContext(manifest)


def test_lib_loads_pure_function(sandbox_lib: Path):
    mod = _ctx_lib(("pure_lib",)).lib("pure_lib")
    assert mod.half(10) == 5


def test_lib_is_cached(sandbox_lib: Path):
    ctx = _ctx_lib(("pure_lib",))
    assert ctx.lib("pure_lib") is ctx.lib("pure_lib")


def test_lib_rejects_undeclared(sandbox_lib: Path):
    with pytest.raises(PermissionError):
        _ctx_lib(()).lib("pure_lib")


def test_lib_rejects_non_whitelisted_name(sandbox_lib: Path):
    ctx = _ctx_lib(("pure_lib",))
    manifest = dataclasses.replace(ctx.manifest, lib_read=("evil_lib",))
    with pytest.raises(PermissionError):
        PluginContext(manifest).lib("evil_lib")


def test_lib_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        paths, "READABLE_LIB",
        {"gone": paths.LibSpec(path=tmp_path / "gone.py", exports=("f",))},
    )
    with pytest.raises(FileNotFoundError):
        _ctx_lib(("gone",)).lib("gone")


def test_lib_rejects_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """CONTRACT §8.1：口径库 import 期连库 → 拒绝加载（不静默降级）。"""
    dirty = tmp_path / "dirty_lib.py"
    dirty.write_text(DIRTY_LIB, encoding="utf-8")
    monkeypatch.setattr(
        paths, "READABLE_LIB", {"dirty_lib": paths.LibSpec(path=dirty, exports=("half",))}
    )
    with pytest.raises(ContractError) as excinfo:
        _ctx_lib(("dirty_lib",)).lib("dirty_lib")
    assert "sqlite3.connect" in str(excinfo.value)


HIDDEN_SYMBOL_LIB = '''
import sqlite3


def half(x):
    return x / 2


def peek_db(path):
    """自己能连库的函数：不得导出给插件（否则绕过 READABLE_DB 白名单）。"""
    return sqlite3.connect(path).execute("SELECT 1").fetchone()
'''


def test_lib_only_exposes_declared_exports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`ctx.lib()` 只能返回 exports 里声明的符号；未声明的（如自己连库的）不可见。"""
    path = tmp_path / "mixed_lib.py"
    path.write_text(HIDDEN_SYMBOL_LIB, encoding="utf-8")
    monkeypatch.setattr(
        paths, "READABLE_LIB", {"mixed_lib": paths.LibSpec(path=path, exports=("half",))}
    )
    lib = _ctx_lib(("mixed_lib",)).lib("mixed_lib")
    assert lib.half(10) == 5
    assert not hasattr(lib, "peek_db"), "能自己连库的函数不得导出"
    assert not hasattr(lib, "sqlite3")


def test_lib_rejects_missing_declared_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """声明了但模块里不存在的导出 → 拒绝加载（配置与实现不一致要暴露）。"""
    path = tmp_path / "short_lib.py"
    path.write_text("def half(x):\n    return x / 2\n", encoding="utf-8")
    monkeypatch.setattr(
        paths, "READABLE_LIB", {"short_lib": paths.LibSpec(path=path, exports=("half", "nope"))}
    )
    with pytest.raises(ContractError) as excinfo:
        _ctx_lib(("short_lib",)).lib("short_lib")
    assert "nope" in str(excinfo.value)


def test_real_amv_regime_does_not_expose_db_loaders():
    """真实口径库：`market_regime_payload` / `_load_total` 内部自己连库，必须不可见。"""
    ctx = _ctx_lib(("amv_regime",))
    lib = ctx.lib("amv_regime")
    assert hasattr(lib, "compute_regime") and hasattr(lib, "stock_state_series")
    for hidden in ("market_regime_payload", "_load_total", "_load_sector", "sqlite3"):
        assert not hasattr(lib, hidden), f"{hidden} 不得导出"


def test_lib_does_not_leak_tool_dir_into_sys_path():
    """回归（2026-10-23 审核 H4）：口径库 import 期会 `sys.path.insert(0, 它的目录)`
    且不清理。加载后必须把工具目录从 sys.path 拿掉，否则任何插件都能
    `import amv_regime` 拿到完整模块（含自己连库的函数），facade 形同虚设。
    """
    from app.core import paths as core_paths
    tools_dir = str(core_paths.READABLE_LIB["amv_contrib"].path.parent)
    ctx = _ctx_lib(("amv_contrib", "amv_regime"))
    before = list(sys.path)
    ctx.lib("amv_contrib")
    ctx.lib("amv_regime")
    assert tools_dir not in sys.path, "工具目录不得留在 sys.path"
    # 不把之前就在 sys.path 的条目误删（只清掉该库自己插的）
    assert [p for p in before if p != tools_dir] == [p for p in sys.path if p != tools_dir]


def test_plugin_cannot_import_regime_directly_after_lib_load():
    """加载口径库后，插件仍不能裸 `import amv_regime` 绕过 facade。"""
    ctx = _ctx_lib(("amv_contrib",))
    ctx.lib("amv_contrib")
    sys.modules.pop("amv_regime", None)
    # 口径库目录已被清出 sys.path → 直 import 应失败（除非环境里另有同名包，本机没有）
    with pytest.raises(ImportError):
        importlib.import_module("amv_regime")


def test_manifest_rejects_non_whitelisted_lib():
    with pytest.raises(ContractError) as excinfo:
        validate_manifest(
            {
                "manifest_version": 1,
                "id": "demo",
                "name": "演示",
                "version": "0.1.0",
                "min_core_version": "0.1.0",
                "backend": {"module": "backend"},
                "data": {"read": [], "lib": ["evil_lib"], "write": []},
            },
            Path("demo"),
        )
    assert "data.lib" in str(excinfo.value)


def test_forbidden_lib_usage_detector():
    assert find_forbidden_lib_usage("import sqlite3\nconn = sqlite3.connect('x.db')") == ["sqlite3.connect"]
    assert find_forbidden_lib_usage("def f(x):\n    return x / 2\n") == []


def test_forbidden_lib_usage_ignores_function_body_and_main_block():
    """函数体内的连库、`__main__` 自检块都不算 import 期副作用（amv_contrib 实测形态）。"""
    assert find_forbidden_lib_usage(SAFE_WITH_CONNECTION_IN_FUNC) == []


def test_lib_accepts_real_amv_contrib():
    """真实口径库 `amv_contrib.py` 必须能过扫描（它只在 __main__ 块里连库）。"""
    from app.core import paths as core_paths
    spec = core_paths.READABLE_LIB.get("amv_contrib")
    assert spec is not None and spec.path.is_file(), spec
    assert find_forbidden_lib_usage(spec.path.read_text(encoding="utf-8")) == []


def test_manifest_rejects_non_whitelisted_json():
    with pytest.raises(ContractError) as excinfo:
        validate_manifest(
            {
                "manifest_version": 1,
                "id": "demo",
                "name": "演示",
                "version": "0.1.0",
                "min_core_version": "0.1.0",
                "backend": {"module": "backend"},
                "data": {"read": [], "json": ["evil_json"], "write": []},
            },
            Path("demo"),
        )
    assert "data.json" in str(excinfo.value)


# ---------------------------------------------------------------- 静态扫描


def test_forbidden_db_usage_detector():
    assert find_forbidden_db_usage("conn = sqlite3.connect('x.db')") == ["sqlite3.connect"]
    assert find_forbidden_db_usage("import pymysql") == ["pymysql"]
    assert find_forbidden_db_usage("ctx.db_read('amv_board.db')") == []


def test_core_main_does_not_hardcode_business_tables():
    """core/main.py 只做总线：不认识业务域。"""
    text = (PROJECT_ROOT / "app" / "core" / "main.py").read_text(encoding="utf-8").lower()
    for token in ("amv", "kline", "battle_plan", "ths_board"):
        assert token not in text, f"core/main.py 出现业务词 {token}"


def test_version_cmp():
    assert version_cmp("0.1.0", "0.1.0") == 0
    assert version_cmp("0.2.0", "0.1.9") == 1
    assert version_cmp("0.1.0", "1.0.0") == -1
