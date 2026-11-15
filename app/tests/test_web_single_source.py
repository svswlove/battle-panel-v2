# -*- coding: utf-8 -*-
"""前端单一来源守卫（2026-11-04）。

背景：8810 前端曾有两份副本——`engine/web/`（正本，零拷贝挂载）与
`app/web/legacy/`（sync_vendor 的 vendoring 目录）。2026-11-04 实测：
legacy 下 10 个内容页文件没有任何路由指向，改它们不生效；而 vendoring 守卫
从未进过门禁，`app/lib/VENDOR.json` 里 index.html / kline_order.js 的记录早已静默过期。

本测试守四件事：
1. 挂载源必须是 engine/web（防有人把服务源改到别处）；
2. `app/web/legacy` 只允许 shell.html（防内容页死副本回流）；
3. shell.html 的 panel-v2 补丁必须在（防补丁丢失，外壳会指回 8800）；
4. vendored shell.html 必须与「源 + 补丁重放」逐字一致、且 VENDOR.json 记录同步
   （防改了源没跑 sync —— 本次翻车的那个场景）。

跑法：pytest app/tests/test_web_single_source.py
门禁：`pytest app` 全量（cutover_gate.py 的「pytest 全套」）自动覆盖。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from starlette.routing import Mount

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import paths  # noqa: E402
from app.core.main import create_app  # noqa: E402

WS = PROJECT_ROOT.parents[1]
ENGINE_WEB = WS / "engine" / "web"
LEGACY_DIR = PROJECT_ROOT / "app" / "web" / "legacy"
MANIFEST = PROJECT_ROOT / "app" / "lib" / "VENDOR.json"
VENV_SYNC = PROJECT_ROOT / "tools" / "sync_vendor.py"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sync_vendor_module():
    """按路径加载 sync_vendor.py，复用它的 SHELL_PATCHES 作为补丁唯一源。"""
    spec = importlib.util.spec_from_file_location("_panel_v2_sync_vendor", VENV_SYNC)
    assert spec and spec.loader, f"无法加载 {VENV_SYNC}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply_patches(text: str) -> str:
    for old, new in _sync_vendor_module().SHELL_PATCHES:
        assert old in text, f"engine/web/shell.html 里找不到待替换片段（源已改结构？）：{old[:60]}"
        text = text.replace(old, new, 1)
    return text


def test_legacy_mounts_point_to_engine_web(tmp_path: Path) -> None:
    """`/legacy` 与根静态兜底都必须挂 engine/web —— 内容页正本只能有一份。"""
    app = create_app(plugins_dir=tmp_path / "empty_plugins", web_dir=paths.WEB_DIR)
    mounts = {r.name: r for r in app.routes if isinstance(r, Mount)}
    for name in ("legacy", "legacy_root"):
        assert name in mounts, f"缺少挂载 {name}（legacy_ready 判定或目录不存在？）"
        directory = getattr(mounts[name].app, "directory", None)
        assert directory, f"{name} 不是 StaticFiles 或未带 directory"
        assert Path(directory).resolve() == ENGINE_WEB.resolve(), (
            f"{name} 挂载到了 {directory}，正本应为 {ENGINE_WEB}。"
            "内容页只允许零拷贝挂载 engine/web，不要改成副本目录。"
        )


def test_legacy_dir_holds_only_patched_shell() -> None:
    """`app/web/legacy` 只允许 shell.html；内容页副本会让人改错边（改了不生效）。"""
    extra = sorted(p.name for p in LEGACY_DIR.iterdir() if p.name != "shell.html")
    assert extra == [], (
        f"app/web/legacy 下出现多余文件：{extra}。"
        "内容页正本是 engine/web（8810 零拷贝挂载），副本改了不生效；"
        "确需 vendoring 请在 tools/sync_vendor.py 的 WEB_FILES 里显式声明并说明理由。"
    )


def test_shell_patches_present() -> None:
    """外壳补丁必须在：ENGINE 同源 + PAGE_BASE 指向 /legacy/。"""
    text = (LEGACY_DIR / "shell.html").read_text(encoding="utf-8")
    assert "const ENGINE = window.location.origin;" in text, "ENGINE 同源补丁丢了"
    assert "const ENGINE = 'http://127.0.0.1:8800';" not in text, "外壳仍写死 8800"
    assert "PAGE_BASE" in text, "PAGE_BASE 补丁丢了（旧的 iframe 会指回根路径）"
    assert "f.src = PAGE_BASE + '/?shell=1&theme='" in text, "iframe 未走 PAGE_BASE"


def test_vendored_shell_equals_source_plus_patches() -> None:
    """vendored shell.html 必须与「engine/web 原版 + 补丁重放」逐字一致。"""
    source = (ENGINE_WEB / "shell.html").read_text(encoding="utf-8")
    expect = _apply_patches(source)
    got = (LEGACY_DIR / "shell.html").read_text(encoding="utf-8")
    assert got == expect, (
        "app/web/legacy/shell.html 与 engine/web/shell.html + 补丁的结果不一致。"
        "修复：python tools/sync_vendor.py（不要手改 vendored 文件）"
    )


def test_vendor_manifest_in_sync() -> None:
    """VENDOR.json 记录必须与磁盘一致 —— 源改了没跑 sync 时这条会红。"""
    mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rec = (mf.get("web") or {}).get("shell.html") or {}
    assert rec, "VENDOR.json 缺少 web.shell.html 记录"
    assert rec.get("source_sha256") == _sha256(ENGINE_WEB / "shell.html"), (
        "engine/web/shell.html 变了但没重跑 sync。修复：python tools/sync_vendor.py"
    )
    assert rec.get("local_sha256") == _sha256(LEGACY_DIR / "shell.html"), (
        "vendored shell.html 与 VENDOR.json 记录不符。修复：python tools/sync_vendor.py"
    )
    assert set(mf.get("web") or {}) == {"shell.html"}, (
        "VENDOR.json 的 web 段只应记录 shell.html（其余内容页零拷贝挂载，不 vendoring）"
    )
