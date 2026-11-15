# -*- coding: utf-8 -*-
"""估值卡契约测试（2026-10-29，托管改到插件内后的版本）。

守的是这一轮的口径：**页面内容 = 现有 PEG 仪表盘原样，面板只做只读托管，且托管在插件内**。
  · 产物唯一源 = `夏天-activity/工具/peg/output/peg_dashboard.html`，由本插件 `/view` 原样返回；
  · core 不认识估值页：`app/core/main.py` / `app/core/paths.py` 里不得出现 peg 字样（防回潮）；
  · `/files/{name}` 只服务 `.html`，目录穿越必须 404；
  · 插件不解析产物、不重算指标、不写文件（`/meta` 只有 stat）。
不跑浏览器；用 TestClient 验真实行为。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths  # noqa: E402
from app.core.contract import load_manifest  # noqa: E402
from app.core.main import create_app  # noqa: E402
from app.plugins.valuation import backend as valuation  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND = PLUGIN_DIR / "frontend.js"
CORE_DIR = ROOT / "app" / "core"

TITLE = "<title>PEG 估值选股仪表盘 · 低估成长股雷达</title>"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fake_output(tmp_path: Path) -> Path:
    """造一份「产物目录」：一个仪表盘 + 一个别的报告 + 一个不该被服务的数据文件。"""
    peg = tmp_path / "夏天-activity" / "工具" / "peg" / "output"
    peg.mkdir(parents=True)
    (peg / "peg_dashboard.html").write_text(
        "<!DOCTYPE html><html><head>" + TITLE + "</head><body>KPI</body></html>", encoding="utf-8"
    )
    (peg / "peg_study_report.html").write_text("<html><body>study</body></html>", encoding="utf-8")
    (peg / "snapshots.csv").write_text("code,peg\n600519,0.8\n", encoding="utf-8")
    return peg


@pytest.fixture()
def fake_ws(tmp_path, monkeypatch):
    _fake_output(tmp_path)
    monkeypatch.setattr(paths, "SUMMER_TOOLS_DIR", tmp_path / "夏天-activity" / "工具")
    return tmp_path


# ---------------------------------------------------------------- manifest / 路由


def test_manifest_loads_and_declares_card():
    m = load_manifest(PLUGIN_DIR)
    assert m.id == "valuation"
    assert m.enabled is True
    assert [c.id for c in m.frontend.cards] == ["valuation.main"]
    assert m.frontend.cards[0].title == "估值"
    assert m.frontend.cards[0].slot == "main"
    # 只读域：不声明任何数据白名单，也不声明可写
    assert list(m.data_read) == []
    assert list(m.panel_read) == []
    assert list(m.json_read) == []
    assert m.quotes_read is False
    assert list(m.data_write) == []


def test_router_routes():
    router = valuation.register(ctx=None)
    assert isinstance(router, APIRouter)
    assert [r.path for r in router.routes] == ["/meta", "/view", "/files/{name:path}"]


# ---------------------------------------------------------------- 防回潮：core 不认识估值页


def test_core_has_no_peg_reference():
    """托管必须在插件内：core 一旦再出现 peg 字样，拆卸就要动 core，插件化就破了。"""
    offenders = {}
    for path in sorted(CORE_DIR.rglob("*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        if "peg" in src.lower():
            offenders[str(path.relative_to(ROOT))] = [
                i + 1 for i, line in enumerate(src.splitlines()) if "peg" in line.lower()
            ]
    assert not offenders, "core 出现 peg 引用（托管应留在 app/plugins/valuation）：%s" % offenders


def test_core_mount_route_is_gone(fake_ws):
    """`/peg` 不再由 core 挂载：请求它必须 404，而不是返回产物。"""
    app = create_app(plugins_dir=paths.PLUGINS_DIR, web_dir=paths.WEB_DIR)
    with TestClient(app) as client:
        res = client.get("/peg/peg_dashboard.html")
    assert res.status_code == 404


# ---------------------------------------------------------------- /meta


def test_meta_missing_artifact_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SUMMER_TOOLS_DIR", tmp_path / "nope")
    meta = valuation.build_meta()
    assert meta["ok"] is True
    assert meta["available"] is False
    assert "refresh_dashboard.ps1" in meta["reason"], "缺产物时必须给出刷新命令"
    assert meta["url"] == "/api/p/valuation/view"


def test_meta_reports_stat_facts_only(fake_ws):
    html = fake_ws / "夏天-activity" / "工具" / "peg" / "output" / valuation.DASHBOARD_FILE
    os.utime(html, (1_700_000_000, 1_700_000_000))

    meta = valuation.build_meta()
    assert meta["available"] is True
    assert meta["file"] == "peg_dashboard.html"
    assert meta["url"] == "/api/p/valuation/view"
    assert meta["size"] == html.stat().st_size
    # mtime 必须是整数秒：浮点会让前端每次拼出不同 ?v=，232KB 反复重下
    assert meta["mtime"] == 1_700_000_000
    assert isinstance(meta["mtime"], int)
    assert meta["mtime_text"].startswith("2023-11-1")
    assert meta["stale"] is True


# ---------------------------------------------------------------- /view（托管本体）


def test_view_serves_artifact_verbatim(fake_ws):
    app = create_app(plugins_dir=paths.PLUGINS_DIR, web_dir=paths.WEB_DIR)
    with TestClient(app) as client:
        res = client.get("/api/p/valuation/view")
        meta = client.get("/api/p/valuation/meta").json()

    html = fake_ws / "夏天-activity" / "工具" / "peg" / "output" / "peg_dashboard.html"
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "charset=utf-8" in res.headers["content-type"]
    assert res.headers["cache-control"] == "no-cache, must-revalidate"
    assert "PEG 估值选股仪表盘" in res.text
    # 面板不加工产物：返回字节数 = 磁盘字节数
    assert len(res.content) == html.stat().st_size
    assert meta["available"] is True
    assert meta["url"] == "/api/p/valuation/view"


def test_view_missing_returns_503_with_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SUMMER_TOOLS_DIR", tmp_path / "missing")
    app = create_app(plugins_dir=paths.PLUGINS_DIR, web_dir=paths.WEB_DIR)
    with TestClient(app) as client:
        res = client.get("/api/p/valuation/view")
    assert res.status_code == 503
    assert "refresh_dashboard.ps1" in res.json()["hint"]


# ---------------------------------------------------------------- /files（同目录其他产物）


def test_files_serves_sibling_html(fake_ws):
    app = create_app(plugins_dir=paths.PLUGINS_DIR, web_dir=paths.WEB_DIR)
    with TestClient(app) as client:
        res = client.get("/api/p/valuation/files/peg_study_report.html")
    assert res.status_code == 200
    assert "study" in res.text


@pytest.mark.parametrize("name", ["snapshots.csv", "nope.html"])
def test_files_rejects_non_html_and_missing(fake_ws, name):
    app = create_app(plugins_dir=paths.PLUGINS_DIR, web_dir=paths.WEB_DIR)
    with TestClient(app) as client:
        assert client.get("/api/p/valuation/files/" + name).status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "../../app/core/main.py",
        "..\\..\\app\\core\\main.py",
        "../output/peg_dashboard.html/../../../../app/core/main.py",
        str(Path(CORE_DIR) / "main.py"),   # 绝对路径
    ],
)
def test_resolve_product_blocks_traversal(name):
    assert valuation.resolve_product(name) is None


def test_traversal_request_does_not_leak_source(fake_ws):
    """编码后的 `..%2f` 也会被路由解码成路径参数，必须 404 且不返回源码。"""
    app = create_app(plugins_dir=paths.PLUGINS_DIR, web_dir=paths.WEB_DIR)
    with TestClient(app) as client:
        res = client.get("/api/p/valuation/files/%2e%2e%2f%2e%2e%2fapp%2fcore%2fmain.py")
    assert res.status_code == 404
    assert "create_app" not in res.text


# ---------------------------------------------------------------- 前端契约（静态）


def test_frontend_is_iframe_shell_without_recomputation():
    src = _read(FRONTEND)
    assert "export default" in src
    assert "valuation.main" in src
    assert "iframe" in src.lower(), "估值卡应是 iframe 壳"
    assert "ctx.api.get('/meta')" in src, "顶栏新鲜度走 /meta"
    assert "meta.url" in src, "iframe src 只能来自 /meta"
    # 只读托管：前端不得自己拼路由/产物路径，也不得解析产物
    assert "/api/p/valuation/view" not in src, "前端不认路由（url 由 /meta 给）"
    assert "DOMParser" not in src and "JSON.parse" not in src, "前端不解析产物内容"


def test_manifest_version_matches_frontend_comment():
    m = json.loads(_read(PLUGIN_DIR / "plugin.json"))
    # 0.2.0 = 托管从 core（/peg 静态挂载）搬进插件（/view）
    # 0.2.1 = 产物 → 面板联动：产物 postMessage 上来，本卡校验后转成壳里已有的事件
    # 0.2.2 = 右键菜单：产物报坐标与目标，本卡合成到壳的 ctx 菜单（不另写一套）
    # 0.2.3 = 右键菜单可收起：同源 iframe 内的 pointerdown/Esc/scroll 转发成父页 click
    # 0.2.4 = 主题跟随面板：量父页底色定明暗，iframe 拼 &mode=light|dark，切主题时重挂
    # 0.2.5 = 标注可用：同源 iframe 注入 /static/v2_note.js 并在卡顶栏给「标注」开关
    #         （iframe 内 v2_note 处于 embedded 模式，按设计不造浮标，所以入口必须在卡里）
    # 0.2.6 = 顶部「刷新数据」：POST /refresh 拉起 peg_dashboard.py，轮询状态，完成后重载 iframe
    #         （主人 2026-11-04 定：这页用得少，不进盘后链，要新数据手动点一下）
    assert m["version"] == "0.2.6"


def test_frontend_bridges_only_through_existing_channels():
    """联动守门（2026-10-31 口径）：只转发，不另开弹窗/页面/接口。

    口径由夏天转达主人定死：
      · 产物 iframe 内点股票/行业 → postMessage → 本卡转成壳里现成的事件；
      · payload 只传 code/name 或行业名；产物自身不 fetch 面板 API；
      · /meta /view /files 三端点口径不变（本测试不改它们）。
    """
    src = _read(PLUGIN_DIR / "frontend.js")
    # 两个现成通道：K 线卡（panel:open-kline）+ 板块卡（panel:open-card）
    assert "panel:open-kline" in src
    assert "panel:open-card" in src
    assert "peg:open-stock" in src and "peg:open-sector" in src
    assert "peg:context-menu" in src, "右键也要走同一条转发链"
    assert "ctxOpen(" not in src, "不另写一套菜单（菜单由壳的 ctxOpen 渲染）"
    assert "ctx-menu" not in src, "菜单由壳渲染，插件不造菜单 DOM"
    # 菜单必须可收起：iframe 内的 pointerdown / Esc / scroll 要转发成父页 click
    assert "armMenuDismiss" in src, "缺少 iframe 内的菜单关闭监听"
    assert "pointerdown" in src and "Escape" in src, "关闭要盖住点击与 Esc"
    # 主题必须跟随面板（否则浅色面板里嵌一块深色，用户说「像两个软件」）
    assert "panelMode" in src and "mode=" in src, "iframe 要带 mode 参数"
    assert "MutationObserver" in src, "切主题时要重挂 iframe"
    # 三重校验：source 指向本卡 iframe、origin 同源、股票码必须 6 位数字
    assert "ev.source !== frame.contentWindow" in src
    assert "ev.origin !== SELF_ORIGIN" in src
    assert "/^\\d{6}$/" in src
    # 不代产物取数、不自己拼产物路由
    assert "fetch(" not in src
    assert "/api/p/valuation/view" not in src


# ---------------------------------------------------------------- 顶部「刷新数据」（2026-11-04）

def test_refresh_button_goes_through_core_script_endpoint():
    """插件不自己起进程（gate 禁 subprocess），刷新走 core 的 /api/shell/run-script。"""
    src = _read(PLUGIN_DIR / "frontend.js")
    assert "刷新数据" in src
    assert "ctx.api.post('/api/shell/run-script'" in src
    be = _read(PLUGIN_DIR / "backend.py")
    assert "subprocess" not in be, "插件后端不得出现子进程调用（cutover_gate 会红）"
    assert "Popen(" not in be
    assert "requests.post" not in be


def test_refresh_waits_for_new_mtime_instead_of_self_report():
    """生成完成以产物 mtime 变化为准（不信任何「我在跑」的自述）。"""
    src = _read(PLUGIN_DIR / "frontend.js")
    assert "baseMtime" in src
    assert "meta.mtime !== baseMtime" in src
    assert "REFRESH_TIMEOUT_S" in src


def test_meta_exposes_refresh_script_key(fake_ws):
    meta = valuation.build_meta()
    assert meta["refresh_script"] == "valuation_refresh"
