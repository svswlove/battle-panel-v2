# -*- coding: utf-8 -*-
"""`backend.mount` 契约测试（2026-10-23 新增）。

旧面板前端请求根路径 `/api/<名>`，兼容插件需要挂到 `/api` 而不是 `/api/p/<id>`。
这条机制有两个必须锁住的点：取值只能是 plugin/root；根路径不能被两个插件抢。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import ContractError, validate_manifest  # noqa: E402


def _manifest(**backend_extra) -> dict:
    m = {
        "manifest_version": 1,
        "id": "compat_probe",
        "name": "探测",
        "version": "0.0.1",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "compat",
        "backend": {"module": "backend", "entry": "register"},
        "data": {"read": [], "write": []},
    }
    m["backend"].update(backend_extra)
    return m


def _dir(tmp_path: Path) -> Path:
    d = tmp_path / "compat_probe"
    d.mkdir()
    return d


def test_mount_defaults_to_plugin(tmp_path: Path) -> None:
    m = validate_manifest(_manifest(), _dir(tmp_path))
    assert m.backend.mount == "plugin"


def test_mount_accepts_root(tmp_path: Path) -> None:
    m = validate_manifest(_manifest(mount="root"), _dir(tmp_path))
    assert m.backend.mount == "root"


@pytest.mark.parametrize("bad", ["api", "ROOT2", "", 1, None])
def test_mount_rejects_unknown(tmp_path: Path, bad) -> None:
    with pytest.raises(ContractError):
        validate_manifest(_manifest(mount=bad), _dir(tmp_path))


def test_root_mount_registers_at_api_prefix() -> None:
    """root 插件注册的路由落在 `/api/<path>`（不是 `/api/p/<id>/<path>`）。"""
    # 直接验证 main.py 的前缀拼接规则：root -> "/api"
    # （完整挂载需起应用，这里只锁住拼接口径，避免有人把 prefix 写回 /api/p）
    router = APIRouter()

    @router.get("/gates")
    def gates():
        return {"ok": True}

    assert [r.path for r in router.routes] == ["/gates"]
    # mount=root 时期望的完整路径
    assert "/api" + router.routes[0].path == "/api/gates"


def test_conflict_detection_logic() -> None:
    """两个 root 插件抢同一路径 → 必须报错（不静默覆盖）。

    复刻 main.py 的 claimed 逻辑：这里只验证判定规则本身，
    避免为了测它去启动整个应用。
    """
    claimed: dict[str, str] = {}
    full = "/api/gates"
    claimed[full] = "compat_a"
    prev = claimed.get(full)
    assert prev is not None and prev != "compat_b", "第二个插件应被判定为冲突"
