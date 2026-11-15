# -*- coding: utf-8 -*-
"""`data.ops_run` / `ctx.ops_run` 契约测试（2026-10-24 N2 追补）。

三件事必须锁住：
1. manifest 字段解析：默认 False、显式 true 生效、非布尔 → ContractError、public() 透出；
2. 白名单闸：未声明调 `ctx.ops_run` → PermissionError，且**不碰底层**；
3. 门面行为：透传 name、旧仓缺失/导入失败显式报错、超时不杀在跑的调用。

铁律：**任何用例都不真跑 schtasks** —— 一律 monkeypatch `_load_legacy` 或改
`LEGACY_OPS_STATUS` 指向 tmp 假文件。
"""
from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import ops_run as core_ops_run  # noqa: E402
from app.core.contract import (  # noqa: E402
    ContractError,
    PluginContext,
    validate_manifest,
)


def _manifest(ops_run_flag: object = "absent") -> dict:
    m = {
        "manifest_version": 1,
        "id": "opsprobe",
        "name": "触发探针",
        "version": "0.0.1",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "domain",
        "backend": {"module": "backend", "entry": "register", "mount": "root"},
        "data": {"read": [], "panel": [], "write": []},
    }
    if ops_run_flag != "absent":
        m["data"]["ops_run"] = ops_run_flag
    return m


@pytest.fixture()
def pdir(tmp_path: Path) -> Path:
    d = tmp_path / "opsprobe"
    d.mkdir()
    return d


# ---------------------------------------------------------------- manifest 解析


def test_ops_run_defaults_false(pdir: Path) -> None:
    m = validate_manifest(_manifest(), pdir)
    assert m.ops_run is False
    assert m.public()["data"]["ops_run"] is False


def test_ops_run_true(pdir: Path) -> None:
    m = validate_manifest(_manifest(True), pdir)
    assert m.ops_run is True
    assert m.public()["data"]["ops_run"] is True


@pytest.mark.parametrize("bad", [1, 0, "true", "1", None, [], {}])
def test_ops_run_must_be_bool(pdir: Path, bad) -> None:
    """非布尔 → ContractError（不静默当 False/True）。对齐 data.feed / data.paper 口径。"""
    with pytest.raises(ContractError):
        validate_manifest(_manifest(bad), pdir)


# ---------------------------------------------------------------- 白名单闸


def test_ops_run_requires_declaration(pdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _boom(*_a, **_kw):
        raise AssertionError("未声明就不许加载旧仓")
    monkeypatch.setattr(core_ops_run, "_load_legacy", _boom)
    ctx = PluginContext(validate_manifest(_manifest(), pdir))
    with pytest.raises(PermissionError):
        ctx.ops_run("OH_Fupan")
    assert calls == []


def test_ops_run_declared_but_legacy_missing(pdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """声明了 true 但旧仓文件不存在 → RuntimeError（不降级成 ok:false）。"""
    monkeypatch.setattr(core_ops_run, "LEGACY_OPS_STATUS", Path("Z:/nope/ops_status.py"))
    core_ops_run.reset_cache()
    ctx = PluginContext(validate_manifest(_manifest(True), pdir))
    with pytest.raises(RuntimeError) as exc:
        ctx.ops_run("OH_Fupan")
    assert "旧仓聚合器不存在" in str(exc.value)


# ---------------------------------------------------------------- 门面行为


def test_run_passes_name_and_returns_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    payload = {"ok": True, "reason": "ok", "task": "OH_Fupan",
               "ts": "2026-10-24 13:30:00", "message": "已触发"}

    def run_manual(name: str) -> dict:
        seen.append(name)
        return payload
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: types.SimpleNamespace(run_manual=run_manual))
    out = core_ops_run.run("  OH_Fupan  ")
    assert seen == ["OH_Fupan"], "两端空白要 strip"
    assert out == payload, "原样透传，不补键不改写"


def test_run_wraps_exception_as_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧仓内部抛异常 → 折成 `{ok:false, reason:'error'}`（HTTP 层仍能 200 如实回报）。"""
    def run_manual(_name: str) -> dict:
        raise PermissionError("filelock 被占")
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: types.SimpleNamespace(run_manual=run_manual))
    out = core_ops_run.run("OH_Fupan")
    assert out["ok"] is False and out["reason"] == "error"
    assert "PermissionError" in out["message"] and "filelock" in out["message"]


def test_run_bad_return_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: types.SimpleNamespace(run_manual=lambda n: ["nope"]))
    out = core_ops_run.run("OH_Fupan")
    assert out["ok"] is False and out["reason"] == "bad_response"


def test_run_blank_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: types.SimpleNamespace(run_manual=lambda n: {}))
    for bad in ("", "   ", None, 123):
        with pytest.raises(ValueError):
            core_ops_run.run(bad)  # type: ignore[arg-type]


def test_run_timeout_does_not_kill_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时返回 dict；在跑的那次调用不被强杀（跑完自己写结果，不抛）。"""
    done: list[str] = []

    def run_manual(name: str) -> dict:
        time.sleep(0.25)
        done.append(name)
        return {"ok": True, "task": name}
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: types.SimpleNamespace(run_manual=run_manual))
    out = core_ops_run.run("OH_Fupan", timeout_s=0.05)
    assert out["ok"] is False and out["reason"] == "timeout"
    assert done == [], "主线程已返回"
    time.sleep(0.4)
    assert done == ["OH_Fupan"], "后台那次必须自己跑完（未被杀）"


# ---------------------------------------------------------------- 旧仓定位与加载


def test_legacy_path_points_at_engine_ops_status() -> None:
    assert core_ops_run.LEGACY_OPS_STATUS.name == "ops_status.py"
    assert core_ops_run.LEGACY_OPS_STATUS.parent.name == "ops"
    assert core_ops_run.LEGACY_OPS_STATUS.parent.parent.name == "engine"


def test_load_legacy_real_module_exposes_run_manual() -> None:
    """真旧仓存在时：能加载且 `run_manual` 可调（**只加载，不调用**，不会起进程）。"""
    if not core_ops_run.LEGACY_OPS_STATUS.is_file():
        pytest.skip("旧仓不在本机：%s" % core_ops_run.LEGACY_OPS_STATUS)
    core_ops_run.reset_cache()
    module = core_ops_run._load_legacy()
    assert callable(module.run_manual)
    # 再取一次走缓存（同一个对象）
    assert core_ops_run._load_legacy() is module


def test_load_legacy_import_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "ops_status.py"
    bad.write_text("def run_manual(\n", encoding="utf-8")     # 语法错
    monkeypatch.setattr(core_ops_run, "LEGACY_OPS_STATUS", bad)
    core_ops_run.reset_cache()
    with pytest.raises(core_ops_run.OpsRunError) as exc:
        core_ops_run._load_legacy()
    assert "导入失败" in str(exc.value)


def test_load_legacy_missing_run_manual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = tmp_path / "ops_status.py"
    good.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(core_ops_run, "LEGACY_OPS_STATUS", good)
    core_ops_run.reset_cache()
    with pytest.raises(core_ops_run.OpsRunError) as exc:
        core_ops_run._load_legacy()
    assert "缺少 run_manual" in str(exc.value)


def test_engine_dir_not_left_on_sys_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """调用结束后不把旧仓 engine 目录永久留在 sys.path（同 ctx.lib 口径）。"""
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: types.SimpleNamespace(run_manual=lambda n: {"ok": True}))
    engine_dir = str(core_ops_run.paths.WS / "engine")
    before = list(sys.path)
    core_ops_run.run("OH_Fupan")
    after = list(sys.path)
    assert after == before, "sys.path 必须原样恢复"
    if engine_dir not in before:
        assert engine_dir not in after
