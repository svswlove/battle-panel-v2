# -*- coding: utf-8 -*-
"""外壳端点测试：8800 退役后的语义（2026-10-26 重写）。

改前这份测的是「把写请求转发给 8800 / 用 run_daemons 拉起 8800」。8800 停服后：
  · `/api/shell/kill`    → 转发目标改为**本进程** `/api/kill`（rw 插件原生实现）；
  · `/api/shell/restart` → 2026-10-29 主人裁定：改为**重启 8810 自身**（分离 helper）；
  · `/api/sign`          → 一直由 rw 插件原生提供，core 侧无路由。
故旧的三条红线（不杀 8810 / already up / run_daemons 兜底）已无对象，改为锁新语义：

  1. restart 必须起 `tools/restart_8810.ps1`（分离进程 + 固定脚本路径），且**不得**再拉 8800；
  2. kill 必须发到本进程 /api/kill，body 原样透传；
  3. 已删的 8800 遗留符号**不得回潮**（死代码守卫）。

全部 monkeypatch，测试零副作用（不真跑 PowerShell/taskkill/子进程）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import main as core_main  # noqa: E402


# ---------------------------------------------------------------- 固件

class _FakeSubprocess:
    """subprocess 模块的最小替身：**替换 main.py 里的引用**，不污染全局 subprocess。"""

    DEVNULL = -3

    def __init__(self, run_impl=None, popen_impl=None):
        self.run = run_impl
        self.Popen = popen_impl


def _client() -> TestClient:
    """只挂 core 的 kill 路由，不加载插件（create_app 会扫插件目录）。"""
    app = FastAPI()

    @app.post("/api/shell/kill")
    async def _kill(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        target = str(request.base_url).rstrip("/") + "/api/kill"
        return JSONResponse(core_main._http_json(target, method="POST", body=body,
                                                 timeout=core_main.WRITE_PROXY_TIMEOUT_S))

    return TestClient(app)


# ---------------------------------------------------------------- restart：重启 8810 自身（2026-10-29）

class _FakeProc:
    pid = 4321


def test_restart_spawns_detached_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """restart = 起分离的 restart_8810.ps1，先回 {ok:true}（不在请求线程里杀自己）。"""
    calls: list[dict] = []

    def fake_popen(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "kwargs": kwargs})
        return _FakeProc()

    monkeypatch.setattr(core_main, "subprocess", _FakeSubprocess(popen_impl=fake_popen))
    res = _real_client().post("/api/shell/restart")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True, body
    assert body["engine"] == "panel_v2"
    assert body["helper_pid"] == 4321
    assert len(calls) == 1, calls

    cmd = calls[0]["cmd"]
    flat = " ".join(str(x) for x in cmd)
    assert "restart_8810.ps1" in flat, cmd
    assert "-File" in cmd
    assert "-NoProfile" in cmd
    kwargs = calls[0]["kwargs"]
    flags = kwargs.get("creationflags", 0)
    assert flags & 0x08000000, "必须 CREATE_NO_WINDOW（无控制台窗口）"
    assert flags & 0x00000200, "必须 CREATE_NEW_PROCESS_GROUP（不接父控制台事件）"
    assert not (flags & 0x00000008), "DETACHED_PROCESS 下 powershell.exe 会立即退出（实测）"
    assert kwargs.get("cwd"), "helper 必须指定 cwd（否则 ensure_8810.ps1 找不到项目根）"
    # 只起 helper，不在请求线程里直接杀进程 / 不碰 8800
    assert "server.py" not in flat
    assert "run_daemons" not in flat
    assert "8800" not in flat


def test_restart_reports_missing_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """脚本丢了就如实报错（503，ok=False），不谎报重启成功。"""
    client = _real_client()
    monkeypatch.setattr(core_main.paths, "PANEL_V2_DIR", tmp_path)
    res = client.post("/api/shell/restart")
    assert res.status_code == 503
    body = res.json()
    assert body["ok"] is False
    assert "重启脚本缺失" in body["detail"]


def test_restart_reports_spawn_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Popen 抛异常 → 503 + ok=False，detail 带异常类型。"""
    def boom(*a, **kw):
        raise OSError("denied")

    monkeypatch.setattr(core_main, "subprocess", _FakeSubprocess(popen_impl=boom))
    res = _real_client().post("/api/shell/restart")
    assert res.status_code == 503
    body = res.json()
    assert body["ok"] is False
    assert "OSError" in body["detail"]


# ---------------------------------------------------------------- kill：转发到本进程

def test_kill_proxies_to_self_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """急停转发到**本进程** /api/kill（不再是 8800），body 原样透传。"""
    seen: dict[str, object] = {}

    def fake_http(url, method="GET", body=None, timeout=0.8):
        # 写代理必须用 WRITE_PROXY_TIMEOUT_S（8s），不是探活级 0.8s
        assert timeout == core_main.WRITE_PROXY_TIMEOUT_S, (
            "写代理超时口径变了：期望 %s，实际 %s" % (core_main.WRITE_PROXY_TIMEOUT_S, timeout))
        seen.update({"url": url, "method": method, "body": body})
        return {"ok": True, "kill_switch": {"active": True}}

    monkeypatch.setattr(core_main, "_http_json", fake_http)
    res = _client().post("/api/shell/kill", json={"active": True, "reason": "宿主急停"})

    assert res.status_code == 200
    assert seen["url"].endswith("/api/kill")
    assert "8800" not in str(seen["url"]), "转发目标不该再是 8800"
    assert seen["method"] == "POST"
    assert seen["body"] == {"active": True, "reason": "宿主急停"}
    assert res.json() == {"ok": True, "kill_switch": {"active": True}}


def test_kill_empty_body_defaults_to_obj(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 body / 非 JSON → 按空对象转发，不 500。"""
    seen: dict[str, object] = {}
    monkeypatch.setattr(core_main, "_http_json",
                        lambda url, method="GET", body=None, timeout=0.8:
                        seen.update({"body": body}) or {"ok": True})
    res = _client().post("/api/shell/kill")
    assert res.status_code == 200
    assert seen["body"] == {}


# ---------------------------------------------------------------- 死代码守卫

def test_8800_legacy_symbols_stay_deleted() -> None:
    """已删的 8800 遗留符号不得回潮（2026-10-26 旧端口下线）。

    保留原因：这些函数只在「转发/拉起 8800」时有意义。回潮意味着有人又把旧链路接回来，
    而 8800 已停服 —— 那会是「看着有代码、运行时永远失败」的静默缺陷。
    """
    for name in ("_proxy_engine_write", "build_engine_restart", "_list_python_procs",
                 "ENGINE_BASE", "ENGINE_SERVER_SCRIPT", "RUN_DAEMONS",
                 "PANEL_DOWN_DETAIL", "RESTART_TIMEOUT_S"):
        assert not hasattr(core_main, name), (
            "core.main.%s 应已删除（8800 退役遗留）；若确需恢复，先确认 8800 重新上线" % name)


def test_kept_symbols_still_present() -> None:
    """仍需要的符号不能被误删：_http_json（kill 转发用）、WRITE_PROXY_TIMEOUT_S。"""
    assert hasattr(core_main, "_http_json")
    assert core_main.WRITE_PROXY_TIMEOUT_S >= 1.0


# ---------------------------------------------------------------- 真路由挂载（不伪造路由）

def _real_client() -> TestClient:
    """用 create_app 起真应用（不扫真实插件目录），验证路由确实注册在 /api 上。"""
    import atexit
    import shutil
    import tempfile

    from app.core.main import create_app

    plugins = Path(tempfile.mkdtemp(prefix="panel_v2_empty_plugins_"))
    web = Path(tempfile.mkdtemp(prefix="panel_v2_empty_web_"))
    # 每次调用建两个临时目录，必须登记回收。不回收会在 %TEMP% 每轮留两个目录，
    # 攒到几千个会拖慢 User Profile Service 登录通知（2026-09-15、11-17 两次实测）。
    atexit.register(shutil.rmtree, plugins, True)
    atexit.register(shutil.rmtree, web, True)
    return TestClient(create_app(plugins_dir=plugins, web_dir=web))


def test_routes_mounted_on_real_app() -> None:
    """`/api/shell/kill`、`/api/shell/restart` 挂在真应用上；`/api/sign` 不在 core 路由表。

    2026-10-24：`/api/sign` 已由 battle_plan_rw 插件原生接管（core 代理删除），
    空插件目录下不该有该路由（防死代码回潮）；插件侧由 battle_plan_rw 自己的测试锁。
    """
    app = _real_client().app
    paths = {(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", ()) or ())))
             for r in app.routes}
    assert ("/api/shell/kill", ("POST",)) in paths
    assert ("/api/sign", ("POST",)) not in paths
    assert ("/api/shell/restart", ("POST",)) in paths
