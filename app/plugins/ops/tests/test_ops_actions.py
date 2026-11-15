# -*- coding: utf-8 -*-
"""ops 固定动作通道测试（M1 A2~A4 的 core 侧）。

**铁律：一条都不真触发。** amv_refresh / sector_backfill 会写生产文件或起进程，测试里一律：

· `_load_module` 换成假模块（不 import 旧仓工具）；
· `subprocess.run` / `Popen` 换成假替身（不跑 schtasks）；
· `_list_python_procs` 换成假列表（不查 WMI）。

验的是**接线与分档**，不是旧仓算法：
  1. 未知动作名 → ValueError（HTTP 400）；
  2. amv_refresh：payload → 5 字段 / None → ok:false + reason / 异常 → 显式错误；
     并发锁非阻塞、连点 → busy（429 语义）；
  3. sector_backfill：无缺口 → ok / 有缺口 → started + 后台线程（线程也被换掉）/ busy；
  4. sector_restart：**已整体摘除**（2026-10-30 随 8002 下线与文件销毁）——不再是可调动作名；
  5. sys.path 补齐：`_ensure_legacy_path()` 幂等、含四个旧仓 import 根。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import ops_run  # noqa: E402
from app.core import paths as core_paths  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例都从干净缓存开始，并**把三条实现换成假体**（默认不真跑）。"""
    ops_run.reset_cache()
    for name in list(ops_run._ACTION_LOCKS):
        ops_run._ACTION_LOCKS[name] = threading.Lock()
    yield
    ops_run.reset_cache()


# ---------------------------------------------------------------- 名字白名单


def test_run_action_rejects_unknown_name() -> None:
    with pytest.raises(ValueError) as exc:
        ops_run.run_action("rm_rf")
    assert "未登记的 ops 动作" in str(exc.value)
    assert "amv_refresh" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_run_action_rejects_blank(bad: Any) -> None:
    with pytest.raises(ValueError):
        ops_run.run_action(bad)


def test_action_whitelist_is_exactly_these() -> None:
    """固定动作白名单：只这三个（不接任意名字）。

    2026-10-26 旧端口下线：`sector_restart` 改为退役回执；
    2026-10-30 主人拍板彻底摘除（8002 已停机、文件进回收站），名字不再登记。
    """
    assert set(ops_run.OPS_ACTIONS) == {"amv_refresh", "sector_backfill", "ops_snapshot"}
    assert set(ops_run._ACTION_IMPL) == set(ops_run.OPS_ACTIONS)


# ---------------------------------------------------------------- sys.path


def test_ensure_legacy_path_adds_four_roots() -> None:
    """旧仓工具的延迟 import 要靠这四个根（engine/executor 缺失就是 M1 实测的那个 bug）。"""
    added = ops_run._ensure_legacy_path()
    try:
        for rel in ("engine", "engine/executor", "夏天-activity/工具",
                    "envs/qmt312/Lib/site-packages"):
            assert str(core_paths.WS / rel) in sys.path, rel
        assert ops_run._ensure_legacy_path() == [], "幂等：重复调用不重复插"
    finally:
        sys.path[:] = [p for p in sys.path if p not in added]


# ---------------------------------------------------------------- amv_refresh


def _fake_amv(monkeypatch: pytest.MonkeyPatch, payload: Any = "MISSING",
              boom: Exception | None = None) -> List[str]:
    calls: List[str] = []

    def _fake_load(path: Path, name: str, required: tuple) -> Any:
        calls.append(str(path))
        if boom is not None:
            raise boom

        def run_once(publish: bool = False) -> Any:
            calls.append("run_once(publish=%s)" % publish)
            return None if payload == "MISSING" else payload

        return types.SimpleNamespace(run_once=run_once)

    monkeypatch.setattr(ops_run, "_load_module", _fake_load)
    return calls


def test_amv_refresh_maps_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_amv(monkeypatch, {
        "asof": "2026-10-23 14:00:00", "all": {"amv": 1.4e13},
        "coverage": {"pct": 0.995}, "stale": False,
        "indices": [{"code": "ALL"}, {"code": "000001.SH"}],
    })
    out = ops_run.run_action("amv_refresh")
    assert out == {"ok": True, "asof": "2026-10-23 14:00:00",
                   "all": {"amv": 1.4e13}, "coverage": {"pct": 0.995},
                   "stale": False, "indices": 2}
    assert "run_once(publish=True)" in calls, "旧端是 publish=True"


def test_amv_refresh_no_payload_reads_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_once` 返回 None（非交易/闸门未过）→ ok:false + reason + 现有值（不写盘）。"""
    _fake_amv(monkeypatch, None)
    monkeypatch.setattr(ops_run, "_read_panel_json", lambda name: {
        "asof": "2026-10-23 15:00:00", "all": {"amv": 1}, "coverage": {"pct": 1},
        "stale": True})
    out = ops_run.run_action("amv_refresh")
    assert out["ok"] is False and "无新数据" in out["reason"]
    assert out["asof"] == "2026-10-23 15:00:00" and out["stale"] is True


def test_amv_refresh_unavailable_503(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_amv(monkeypatch, boom=ops_run.OpsRunError("旧仓模块不存在：x"))
    out = ops_run.run_action("amv_refresh")
    assert out["ok"] is False and out["http"] == 503
    assert out["status"] == "unavailable"


def test_amv_refresh_other_error_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_amv(monkeypatch, boom=RuntimeError("qmt down"))
    out = ops_run.run_action("amv_refresh")
    assert out["ok"] is False and out["http"] == 500
    assert "qmt down" in out["detail"]


def test_amv_refresh_timeout_504(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 20s → 504（不杀已在跑的那次）。这里把上限改成 0.05s 模拟。"""
    monkeypatch.setattr(ops_run, "AMV_REFRESH_TIMEOUT_S", 0.05)

    def _slow_load(path: Path, name: str, required: tuple) -> Any:
        def run_once(publish: bool = False) -> Any:
            time.sleep(0.3)
            return {"asof": "x", "all": {}, "coverage": {}, "indices": []}
        return types.SimpleNamespace(run_once=run_once)

    monkeypatch.setattr(ops_run, "_load_module", _slow_load)
    out = ops_run.run_action("amv_refresh")
    assert out["ok"] is False and out["http"] == 504
    time.sleep(0.4)   # 让慢调用自己跑完（不抛即证明没被强杀）


def test_amv_refresh_busy_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """并发锁非阻塞：第二次调用立刻 busy（旧端 429），不等第一次跑完。"""
    monkeypatch.setattr(ops_run, "AMV_REFRESH_TIMEOUT_S", 1.0)
    gate = threading.Event()

    def _blocking_load(path: Path, name: str, required: tuple) -> Any:
        def run_once(publish: bool = False) -> Any:
            gate.wait(2.0)
            return {"asof": "x", "all": {}, "coverage": {}, "indices": []}
        return types.SimpleNamespace(run_once=run_once)

    monkeypatch.setattr(ops_run, "_load_module", _blocking_load)
    box: Dict[str, Any] = {}

    def _first() -> None:
        box["first"] = ops_run.run_action("amv_refresh")

    t = threading.Thread(target=_first)
    t.start()
    time.sleep(0.1)
    second = ops_run.run_action("amv_refresh")
    gate.set()
    t.join(3)
    assert second["http"] == 429 and second["status"] == "busy"
    assert box["first"]["ok"] is True


# ---------------------------------------------------------------- sector_backfill


def _fake_backfill(monkeypatch: pytest.MonkeyPatch, result: Dict[str, Any],
                   threads: List[Dict[str, Any]] | None = None) -> List[str]:
    calls: List[str] = []

    def _fake_load(path: Path, name: str, required: tuple) -> Any:
        def run(check_only: bool = False, **kw: Any) -> Dict[str, Any]:
            calls.append("check_only=%s" % check_only)
            if check_only:
                return dict(result)
            if threads is not None:
                threads.append({"apply": True})
            return {"status": "filled", "filled": 3}
        return types.SimpleNamespace(run=run)

    monkeypatch.setattr(ops_run, "_load_module", _fake_load)
    monkeypatch.setattr(ops_run, "_backfill_status_running", lambda: False)

    class _FakeThread:
        def __init__(self, target=None, name=None, daemon=None, **_kw):
            self._target = target
            self.name = name

        def start(self):
            if threads is not None:
                threads.append({"started": self.name})

        def join(self, *_a):
            return None

        def is_alive(self):
            return False

    monkeypatch.setattr(ops_run.threading, "Thread", _FakeThread)
    return calls


def test_backfill_no_gap_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_backfill(monkeypatch, {"gaps": [], "points_before": 240})
    out = ops_run.run_action("sector_backfill")
    assert out == {"ok": True, "status": "ok", "gaps": [], "points": 240}
    assert calls == ["check_only=True"], "先 check-only，无缺口就不启动"


def test_backfill_with_gaps_starts_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    threads: List[Dict[str, Any]] = []
    _fake_backfill(monkeypatch, {"gaps": [["09:31", "09:40"]], "points_before": 100}, threads)
    out = ops_run.run_action("sector_backfill")
    assert out["status"] == "started" and out["gaps"] == [["09:31", "09:40"]]
    assert threads and threads[0]["started"] == "panel-v2-sector-backfill"


def test_backfill_busy_when_status_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops_run, "_backfill_status_running", lambda: True)
    out = ops_run.run_action("sector_backfill")
    assert out["ok"] is True and out["status"] == "busy"


def test_backfill_module_missing_is_error_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(path: Path, name: str, required: tuple) -> Any:
        raise ops_run.OpsRunError("旧仓模块不存在：x")
    monkeypatch.setattr(ops_run, "_load_module", _boom)
    monkeypatch.setattr(ops_run, "_backfill_status_running", lambda: False)
    out = ops_run.run_action("sector_backfill")
    assert out["ok"] is False and out["status"] == "error"
    assert "旧仓模块不存在" in out["detail"]


def test_backfill_check_exception_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_load(path: Path, name: str, required: tuple) -> Any:
        def run(check_only: bool = False, **kw: Any) -> Dict[str, Any]:
            raise RuntimeError("xtquant missing")
        return types.SimpleNamespace(run=run)
    monkeypatch.setattr(ops_run, "_load_module", _fake_load)
    monkeypatch.setattr(ops_run, "_backfill_status_running", lambda: False)
    out = ops_run.run_action("sector_backfill")
    assert out["status"] == "error" and "xtquant missing" in out["detail"]


# ---------------------------------------------------------------- sector_restart（已摘除）

# 2026-10-30：8002 已停机且 dashboard 目录整体进回收站，`sector_restart` 不再有任何意义，
# 整条动作（含 2026-10-26 的 retired/410 桩）一并删除。下面锁的是**摘除后的不变量**：
#   ① 名字不再是合法动作（传进来必须 ValueError → 调用方 400）；
#   ② 不得有任何进程操作手段回潮（subprocess / 拉起辅助）。

def test_restart_action_is_gone() -> None:
    """动作名与实现都不存在，且调用返回 ValueError（不再是 410 桩）。"""
    assert "sector_restart" not in ops_run.OPS_ACTIONS
    assert not hasattr(ops_run, "_sector_restart")
    with pytest.raises(ValueError):
        ops_run.run_action("sector_restart")


def test_restart_touches_no_process() -> None:
    """子进程只能出现在按需脚本白名单通道；不得恢复「杀旧 + 拉起 8002」的旧链路。

    2026-11-04 主人要「选股页刷新」按钮 → core 新增 `run_script`（命令全由旧仓
    `MANUAL_SCRIPTS` 白名单决定），必须 `subprocess.Popen` 跑 python。旧断言
    「全文无 subprocess」已随能力变更过期，收窄为：带子进程的函数只能是 run_script。
    """
    import ast
    import inspect

    src = Path(inspect.getsourcefile(ops_run)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    rs = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_script")
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            if not (rs.lineno <= node.lineno <= (rs.end_lineno or rs.lineno)):
                offenders.append(node.lineno)
    assert not offenders, \
        "subprocess 只允许出现在 run_script 内（含其内部线程体），越界行：%s" % offenders


def test_restart_dead_helpers_stay_deleted() -> None:
    """已删的旧实现辅助不得回潮（只为「杀旧 + 拉起 8002」存在）。"""
    for name in ("_list_python_procs", "_qmt312_python", "RUN_DAEMONS_SCRIPT",
                 "AMV_DASHBOARD_PORT", "RUN_DAEMONS_ARG", "RESTART_PY_KEYS",
                 "RESTART_TIMEOUT_S", "PROC_QUERY_TIMEOUT_S", "TASKKILL_TIMEOUT_S"):
        assert not hasattr(ops_run, name), \
            "ops_run.%s 应已删除（8002 退役遗留）；恢复前先确认 8002 重新上线" % name


# ---------------------------------------------------------------- ops_snapshot（新增重算口）


def test_ops_snapshot_calls_legacy_build_and_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ops_snapshot` 必须调 `build(force=True, write=True)`（= 原 8800 的调用口径），不重算指标。"""
    seen: Dict[str, Any] = {}

    class _Mod:
        @staticmethod
        def build(force=False, write=True):
            seen.update({"force": force, "write": write})
            return {"meta": {"updated_at": "2026-10-26 21:30:00"},
                    "summary": {"tasks_total": 22}}

    monkeypatch.setattr(ops_run, "_load_legacy", lambda: _Mod())
    out = ops_run.run_action("ops_snapshot")

    assert seen == {"force": True, "write": True}, "重算必须 force+write，否则读到缓存/不落盘"
    assert out["ok"] is True and out["updated_at"] == "2026-10-26 21:30:00"


def test_ops_snapshot_missing_build_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧仓缺 build() → 503 如实上报，不谎报已刷新。"""
    monkeypatch.setattr(ops_run, "_load_legacy", lambda: object())
    out = ops_run.run_action("ops_snapshot")
    assert out["ok"] is False and out["http"] == 503


def test_ops_snapshot_legacy_unavailable_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧仓文件缺失/导入失败（OpsRunError）→ 503，不抛穿到 HTTP 500。"""
    def _boom():
        raise ops_run.OpsRunError("旧仓聚合器不存在")

    monkeypatch.setattr(ops_run, "_load_legacy", _boom)
    out = ops_run.run_action("ops_snapshot")
    assert out["ok"] is False and out["http"] == 503
    assert "不存在" in out["detail"]


def test_backfill_status_running_reads_state_file(monkeypatch: pytest.MonkeyPatch,
                                                 tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(core_paths, "STATE_DIR", state_dir)
    path = state_dir / "sector_track_backfill_status.json"
    assert ops_run._backfill_status_running() is False, "文件缺失 = 不忙"
    path.write_text(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "status": "running"}), encoding="utf-8")
    assert ops_run._backfill_status_running() is True
    # 30 分钟前的 running = 进程异常死亡，视为不忙（旧端同）
    path.write_text(json.dumps({"ts": "2020-01-01 00:00:00", "status": "running"}),
                    encoding="utf-8")
    assert ops_run._backfill_status_running() is False
    path.write_text(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "status": "done"}), encoding="utf-8")
    assert ops_run._backfill_status_running() is False
