# -*- coding: utf-8 -*-
"""板块 Tab 的 8002 依赖收口（2026-10-26）。

**要解决什么**：全 Tab 实测 9 个 Tab 里 8 个只走 8810，只有板块 Tab 带 9 条 8002 请求
（`8002/index.html?embed=panel` + style.css + app.js）。板块 Tab 早就命中 `v2:sector.board`，
但旧面板仍会在旧 `activate()` 里渲染一次 —— 因为 v2bridge 的包装是「先跑旧 activate，再盖 v2 卡」，
而 v2bridge 注入在 `</body>` 前、要等 300ms 轮询 + fetch 映射，**晚于旧页 boot**。

修法分两层，缺一层都不够：
  1. 服务端在 `<head>` 同步注入 `window.__V2_OWNED`（v2bridge 来不及，只能服务端给）；
  2. 旧页 `renderSector` 见到归属就让位；v2bridge 切卡后再兜底拆掉残留 iframe。

本文件守这四件事。只读源码 + 纯函数，不跑服务器。
"""
import json
from pathlib import Path

PANEL = Path(__file__).resolve().parents[2]
APP = PANEL / "app"
WS = PANEL.parent.parent          # OH-WorkSpace


def _main_py() -> str:
    return (APP / "core" / "main.py").read_text(encoding="utf-8")


def _legacy_index() -> str:
    return (WS / "engine" / "web" / "index.html").read_text(encoding="utf-8")


def _bridge() -> str:
    return (APP / "web" / "v2bridge.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. 服务端归属清单


def test_owned_tabs_reads_v2_map(tmp_path):
    """映射值为 v2 / v2:<card> 的 Tab 都算 v2 接管；old 与说明字段不算。"""
    import sys

    sys.path.insert(0, str(APP))
    from core.main import _v2_owned_tabs  # noqa: PLC0415

    m = tmp_path / "v2_map.json"
    m.write_text(json.dumps({
        "sector": "v2:sector.board",
        "dashboard": "v2",
        "concept": "old",
        "_doc": "说明字段",
    }), encoding="utf-8")

    owned = _v2_owned_tabs(tmp_path)
    assert owned.get("sector") is True
    assert owned.get("dashboard") is True
    assert "concept" not in owned, "old 不该算 v2 接管"
    assert "_doc" not in owned, "说明字段不该进清单"


def test_owned_tabs_fails_closed(tmp_path):
    """映射缺失/坏掉 → 空清单（全按 old 走），不能崩。"""
    import sys

    sys.path.insert(0, str(APP))
    from core.main import _v2_owned_tabs  # noqa: PLC0415

    assert _v2_owned_tabs(tmp_path) == {}          # 文件不存在
    (tmp_path / "v2_map.json").write_text("{坏 json", encoding="utf-8")
    assert _v2_owned_tabs(tmp_path) == {}          # 坏 json


def test_index_html_injects_owned_map_in_head():
    """注入点必须在 </head>：放到 </body> 就跟 v2bridge 一样晚，拦不住 boot。"""
    src = _main_py()
    assert "_v2_owned_tabs(" in src, "首页没有注入归属清单"
    i_head = src.find('html.replace("</head>"')
    i_body = src.find('html.replace("</body>"')
    assert i_head != -1, "没有往 </head> 注入 __V2_OWNED"
    assert i_body != -1 and i_head < i_body, "注入顺序不对（head 注入必须在 body 注入之前）"
    assert "window.__V2_OWNED" in src


# ---------------------------------------------------------------- 2. 旧页让位


def test_legacy_render_sector_yields_to_v2():
    """旧页 renderSector 必须在拉 8002 之前判断归属并让位。"""
    src = _legacy_index()
    i = src.find("async function renderSector()")
    assert i != -1, "engine/web/index.html 里找不到 renderSector"
    body = src[i:i + 1600]
    i_guard = body.find("__V2_OWNED")
    # 找**真实调用**而不是注释里的网址（上一版就栽在注释上）
    i_probe = body.find("fetch('/api/sector/health'")
    assert i_guard != -1, "renderSector 没有读 __V2_OWNED"
    assert i_probe != -1, "renderSector 的探活调用不见了（检查是否改错文件）"
    assert i_guard < i_probe, "归属判断必须在探活/建 iframe 之前，否则请求已经发出去了"
    # 让位后必须打 ready 标记，否则旧页下次 activate 还会再走一遍
    assert "dataset.ready = '1'" in body


# ---------------------------------------------------------------- 3. 桥兜底


def test_bridge_drops_legacy_sector_iframe():
    """首屏直接开 #sector 时旧页 boot 早于桥安装，仍会建出 iframe → 切卡后必须兜底拆掉。"""
    src = _bridge()
    assert "function dropLegacyPanel(" in src, "缺旧面板拆除"
    assert "sectorFrame" in src, "拆除逻辑没对上 iframe 的 id"
    i = src.find("window.activate = function")
    body = src[i:i + 900]
    assert "dropLegacyPanel(key)" in body, "activate 命中 v2 后没有拆旧面板"
    assert "window.__v2OwnsTab" in src, "桥没有导出归属查询（旧页面板早期判断用不到它）"


# ---------------------------------------------------------------- 4. 旧页副本身份


def test_served_file_is_engine_web_index():
    """首页服务的是 `engine/web/index.html`，不是 `app/web/legacy/index.html`。

    两者内容相近，改错文件会「看起来改了、实际没生效」（2026-10-26 就是这么绕了一圈）。
    """
    assert "legacy_web" in _main_py(), "首页来源不再是 engine/web"
    served = _legacy_index()
    assert "async function renderSector()" in served
    assert "__V2_OWNED" in served, "改的若是 legacy 副本，这条是绿的但线上仍会拉 8002"
