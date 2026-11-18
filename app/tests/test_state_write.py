# -*- coding: utf-8 -*-
"""`ctx.state_write` 契约测试（2026-11-16，补 `panel_write` 的同款门禁）。

背景：预警卡的「唤醒总开关」要写 `engine/state/wake_switch.json`。插件写 engine/state 属于
**受控写**，必须三道齐全，不能因为模块自带锁/原子/审计就绕过通道（夏天 2026-11-16 验收提的缺口）。

锁四件事：
1. 未在 `manifest.data.state` 声明 → 拒绝（即使白名单里有）；
2. 不在 `paths.WRITABLE_STATE` 全局白名单 → 加载期就拒绝（即使 manifest 声明了）；
3. 写入必须：备份旧文件 + 原子替换（不留 .tmp） + 核心审计日志；
4. `wake_switch` 这一条必须「白名单 + alert manifest」两边同时存在（防单边改动）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import ContractError, PluginContext, validate_manifest  # noqa: E402

ALERT_DIR = ROOT / "app" / "plugins" / "alert"


def _manifest(state: list[str] | None = None) -> dict:
    return {
        "manifest_version": 1,
        "id": "sprobe",
        "name": "状态写探针",
        "version": "0.0.1",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "compat",
        "backend": {"module": "backend", "entry": "register", "mount": "root"},
        "data": {"read": [], "panel": [], "state": state or [], "write": []},
    }


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch):
    """把可写白名单与输出目录都指到 tmp，避免碰真文件。"""
    target = tmp_path / "state" / "wake_switch.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"enabled": True, "updated_at": "old", "by": "old"},
                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setitem(paths.WRITABLE_STATE, "wake_switch", target)
    monkeypatch.setattr(paths, "OUT_DIR", tmp_path / "out")
    d = tmp_path / "sprobe"
    d.mkdir()
    return target, d


def test_state_write_requires_manifest_declaration(sandbox):
    target, pdir = sandbox
    ctx = PluginContext(validate_manifest(_manifest([]), pdir))
    with pytest.raises(PermissionError):
        ctx.state_write("wake_switch", {"enabled": False})


def test_state_write_rejects_unknown_name(sandbox):
    target, pdir = sandbox
    with pytest.raises(ContractError):
        validate_manifest(_manifest(["not_in_whitelist"]), pdir)


def test_state_write_is_atomic_backs_up_and_audits(sandbox):
    target, pdir = sandbox
    ctx = PluginContext(validate_manifest(_manifest(["wake_switch"]), pdir))

    ctx.state_write("wake_switch", {"enabled": False, "updated_at": "new", "by": "panel"})

    after = json.loads(target.read_text(encoding="utf-8"))
    assert after == {"enabled": False, "updated_at": "new", "by": "panel"}

    bdir = paths.OUT_DIR / "backups" / "wake_switch"
    backups = sorted(bdir.glob("*.json"))
    assert backups, "写前必须备份"
    assert json.loads(backups[0].read_text(encoding="utf-8"))["by"] == "old"

    audit = bdir / "audit.log"
    assert audit.is_file(), "核心审计必须留痕"
    line = audit.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "plugin=sprobe" in line and "file=wake_switch" in line

    assert not target.with_name(target.name + ".tmp").exists(), "不许留下 .tmp"


def test_state_write_rejects_non_dict(sandbox):
    target, pdir = sandbox
    ctx = PluginContext(validate_manifest(_manifest(["wake_switch"]), pdir))
    with pytest.raises(ValueError):
        ctx.state_write("wake_switch", ["not", "a", "dict"])


def test_wake_switch_registered_in_whitelist_and_alert_manifest():
    """双白名单必须同时存在：白名单删了 → 插件加载失败；manifest 删了 → 写被拒。"""
    assert "wake_switch" in paths.WRITABLE_STATE, "paths.WRITABLE_STATE 缺 wake_switch"
    assert paths.WRITABLE_STATE["wake_switch"].name == "wake_switch.json"
    assert paths.WRITABLE_STATE["wake_switch"].parent.name == "state"

    from app.core.contract import load_manifest

    m = load_manifest(ALERT_DIR)
    assert "wake_switch" in m.state_write, "alert manifest 必须声明 data.state=['wake_switch']"
