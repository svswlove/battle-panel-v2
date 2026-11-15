# -*- coding: utf-8 -*-
"""selection 插件后端契约测试（v0.1.0，docs/task_R2_selection.md §3）。

守的是「零后端改动」这条边界：
  · backend.py 可 import、register(ctx) 返回**空** APIRouter；
  · manifest 合法、mount=root、卡片 id/slot/order 与 docs/selection_contract.md 一致；
  · 本插件不注册任何路由 —— 否则会和 compat_misc（同样 mount=root）抢同一 `/api/...`
    路径，`app/core/main.py` 的根路径冲突检查会让 core 启动直接 RuntimeError；
  · 不消费任何数据通道（data.* 全空），也不含写盘/子进程代码。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.contract import validate_manifest  # noqa: E402
from app.plugins.selection import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


class FakeCtx:
    """本插件不该碰 ctx 的任何能力：被调用即视为越界。"""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - 只在越界时触发
        raise AssertionError("selection 插件不应调用 ctx.%s" % name)


# ---------------------------------------------------------------- manifest


def test_manifest_valid() -> None:
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.id == "selection"
    assert m.version == "0.2.0"
    assert m.enabled is True


def test_manifest_mount_is_root() -> None:
    """mount=root：前端消费的是根路径 /api/selection*（compat_misc 提供），不是 /api/p/selection。"""
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.backend.mount == "root"


def test_manifest_card_contract() -> None:
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    cards = m.frontend.cards
    assert [c.id for c in cards] == ["selection.main"]
    assert cards[0].title == "选股"
    assert cards[0].slot == "main"
    assert cards[0].order == 55
    assert m.frontend.module == "frontend.js"


def test_manifest_declares_no_data_channel() -> None:
    """零后端改动 ⇒ 不声明任何数据通道（读了也没用，且声明即代表有权）。"""
    m = validate_manifest(_manifest(), PLUGIN_DIR)
    assert m.data_read == ()
    assert m.panel_read == ()
    assert m.json_read == ()
    assert m.lib_read == ()
    assert m.quotes_read is False
    assert m.feed_read is False
    assert m.paper_read is False
    assert m.data_write == ()


# ---------------------------------------------------------------- register


def test_register_returns_empty_router() -> None:
    """必须返回 APIRouter（core 硬要求），且**一条路由都不注册**。"""
    router = backend.register(FakeCtx())
    assert isinstance(router, APIRouter)
    assert router.routes == [], [getattr(r, "path", r) for r in router.routes]


def test_no_selection_route_shadowing_compat_misc() -> None:
    """选股四端点归 compat_misc；本插件若注册同名根路径，core 启动即报冲突。"""
    router = backend.register(FakeCtx())
    paths = {getattr(r, "path", "") for r in router.routes}
    for forbidden in ("/selection", "/selection_prefs", "/selection_pool",
                      "/selection_pool/add", "/selection_pool/remove",
                      "/selection_board_options"):
        assert forbidden not in paths


def test_register_does_not_touch_ctx() -> None:
    """FakeCtx 任何属性访问都会抛 AssertionError —— register 不得依赖 ctx。"""
    assert isinstance(backend.register(FakeCtx()), APIRouter)


def test_data_contract_documents_four_endpoints() -> None:
    """契约表（供后来者查证）：四个端点都在，且都写明由 compat_misc 提供。"""
    contract = backend.DATA_CONTRACT
    assert set(contract) == {"selection", "selection_prefs", "selection_pool",
                             "selection_board_options"}
    for text in contract.values():
        assert "compat_misc" in text


# ---------------------------------------------------------------- 与 compat_misc 的路由不重叠


def test_backend_module_has_no_route_decorator() -> None:
    """静态守：backend.py 里不得出现 @router.get / @router.post（空 router 的形态保证）。"""
    src = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    assert "@router.get" not in src
    assert "@router.post" not in src
    assert "APIRouter()" in src


def test_backend_has_no_io_tokens() -> None:
    """与 cutover_gate 同一份禁令的最小子集：无写盘、无网络、无子进程、无库连接。

    只扫**代码体**（`ast` 取模块 docstring 之后的源码）——文档字符串里为了说明
    “不使用 open() / subprocess” 而写的字面词不应被当成违规。
    """
    import ast

    src = (PLUGIN_DIR / "backend.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = ast.get_docstring(tree) or ""
    body = src.replace(doc, "", 1)
    for token in ("sqlite3.connect", "open(", "requests.", "urllib", "subprocess",
                  "os.remove(", "os.replace(", "json.dump("):
        assert token not in body, "backend.py 代码体出现 %s" % token


@pytest.mark.parametrize("name", ["plugin.json", "backend.py", "frontend.js"])
def test_plugin_files_exist(name: str) -> None:
    assert (PLUGIN_DIR / name).is_file(), name
