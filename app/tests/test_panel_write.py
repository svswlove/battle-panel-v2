# -*- coding: utf-8 -*-
"""`ctx.panel_write` 契约测试（2026-10-23 新增）。

把「插件零写入」改成「受控写」后，必须锁住三件事：
1. 未在 manifest.data.write 声明 → 拒绝
2. 不在 paths.WRITABLE_PANEL 全局白名单 → 拒绝（即使 manifest 声明了）
3. 写入必须：备份旧文件 + 原子替换 + 审计日志
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


def _manifest(write: list[str] | None = None) -> dict:
    return {
        "manifest_version": 1,
        "id": "wprobe",
        "name": "写探针",
        "version": "0.0.1",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "compat",
        "backend": {"module": "backend", "entry": "register", "mount": "root"},
        "data": {"read": [], "panel": [], "write": write or []},
    }


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch):
    """把可写白名单与输出目录都指到 tmp，避免碰真文件。"""
    target = tmp_path / "panel" / "watchlist.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"items": [], "v": 1}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setitem(paths.WRITABLE_PANEL, "watchlist", target)
    monkeypatch.setattr(paths, "OUT_DIR", tmp_path / "out")
    d = tmp_path / "wprobe"
    d.mkdir()
    return target, d


def test_write_requires_manifest_declaration(sandbox):
    target, pdir = sandbox
    m = validate_manifest(_manifest([]), pdir)
    ctx = PluginContext(m)
    with pytest.raises(PermissionError):
        ctx.panel_write("watchlist", {"x": 1})


def test_write_rejects_unknown_name(sandbox):
    target, pdir = sandbox
    # manifest 声明了一个白名单里没有的名字 → 加载期就应被拒
    with pytest.raises(ContractError):
        validate_manifest(_manifest(["not_in_whitelist"]), pdir)


def test_write_is_atomic_and_backs_up(sandbox):
    target, pdir = sandbox
    m = validate_manifest(_manifest(["watchlist"]), pdir)
    ctx = PluginContext(m)

    before = json.loads(target.read_text(encoding="utf-8"))
    assert before["v"] == 1

    ctx.panel_write("watchlist", {"items": [{"code": "600519.SH"}], "v": 2})

    after = json.loads(target.read_text(encoding="utf-8"))
    assert after["v"] == 2 and after["items"][0]["code"] == "600519.SH"

    # 备份目录里应有一份旧内容
    bdir = paths.OUT_DIR / "backups" / "watchlist"
    backups = sorted(bdir.glob("*.json"))
    assert backups, "写前必须备份"
    assert json.loads(backups[0].read_text(encoding="utf-8"))["v"] == 1

    # 审计日志
    audit = bdir / "audit.log"
    assert audit.is_file()
    line = audit.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "plugin=wprobe" in line and "file=watchlist" in line

    # 不留下 .tmp
    assert not target.with_name(target.name + ".tmp").exists()


def test_write_rejects_non_dict(sandbox):
    target, pdir = sandbox
    m = validate_manifest(_manifest(["watchlist"]), pdir)
    ctx = PluginContext(m)
    with pytest.raises(ValueError):
        ctx.panel_write("watchlist", ["not", "a", "dict"])
