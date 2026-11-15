# -*- coding: utf-8 -*-
"""浮层叠加 + 全局个股跳 K 线（2026-10-28 三条标注的契约守门）。

来源（主人标注）：
  · N20261028-100413-37：作战表点个股打不开 K 线；K 线里要能 ↑↓ 翻同列表的票。
  · N20261028-100527-38：板块详情里点个股开 K 线后，详情卡片不该消失；每次只关最上层。

这一档只有前端，没有可断言的后端行为；能守的是**结构契约**：
  1. 壳有一个跨模块浮层栈（window.__v2Overlay，push/pop/top）；
  2. 独立打开 K 线时不再 closeSectorModal（嵌入旧页仍收，因为父页弹窗盖住 iframe）；
  3. Esc 只在自己是栈顶时处理（K 线与板块详情各自判 top）；
  4. 打开 K 线时把同列表上下文传进旧弹窗（app.open 第三参），否则 ↑↓ 不可用；
  5. 作战表/纸面账本的个股单元格带 data-open-kline 占位（壳的全局委托靠它）。
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
WEB = APP / "web"
SHELL_JS = WEB / "shell.js"
SHELL_CSS = WEB / "shell.css"
KLINE_LEGACY = APP / "plugins" / "kline" / "frontend" / "legacy_modal.js"
SECTOR_DETAIL = APP / "plugins" / "sector" / "frontend" / "detail.js"
PAPER = APP / "plugins" / "paper" / "frontend.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def _fn_body(src: str, head: str) -> str:
    """取一个函数体（从 head 到下一个行首 }）。"""
    i = src.index(head)
    j = src.index("\n}\n", i)
    return src[i:j + 3]


def test_shell_exposes_overlay_stack() -> None:
    src = _read(SHELL_JS)
    assert "window.__v2Overlay" in src, "壳未暴露浮层栈"
    body = _fn_body(src, "function overlayStack()")
    for token in ("push:", "pop:", "top:"):
        assert token in body, "浮层栈缺 %s" % token
    assert "overlayStack();" in src, "启动时未初始化浮层栈"


def test_kline_does_not_close_sector_modal_when_standalone() -> None:
    """独立打开 K 线时保留板块详情；嵌入旧页仍收（父页弹窗在 iframe 外）。"""
    src = _read(SHELL_JS)
    i = src.index("if (k) {")
    j = src.index("return;", i)
    body = src[i:j]
    assert "ctxOpenKline(normalizeStockCode(code.trim()), name, klineListOf(node));" in body, "独立分支未传列表上下文"
    # 独立分支（else 之后）不得出现 closeSectorModal
    tail = body[body.index("} else {"):]
    assert "closeSectorModal" not in tail, "独立打开 K 线仍会收掉板块详情（N20261028-100527-38 回归）"


def test_kline_list_context_collects_stocks_only() -> None:
    src = _read(SHELL_JS)
    body = _fn_body(src, "function klineListOf(node)")
    assert "ctxIsIndexCode" in body, "列表未排除板块/指数码（.TI）"
    assert "querySelectorAll('[data-open-kline], [data-code]')" in body, "列表收集口径不对"
    assert "list.length >= 2" in body, "不足两只时不应伪造列表"
    assert "return null" in body, "无上下文必须返回 null"


def test_kline_esc_only_when_top_of_stack() -> None:
    src = _read(KLINE_LEGACY)
    body = _fn_body(src, "function bindStack()")
    assert "MutationObserver" in body, "弹窗显隐未同步进栈（谁关的都要同步）"
    assert "ov.top() !== 'kline'" in body, "Esc 未判栈顶"
    assert "stopImmediatePropagation" in body, "Esc 未拦住下层的 document 监听"
    assert "app.open(code, opt.name || '', curList)" in src, "旧 open 第三参（列表）未传"


def test_sector_detail_pushes_and_pops_stack() -> None:
    src = _read(SECTOR_DETAIL)
    assert "ov.push('sector')" in src, "板块详情未入栈"
    assert "ov.pop('sector')" in src, "板块详情关闭未出栈"
    assert "ov.top() !== 'sector'" in src, "Esc 未判栈顶"


def test_paper_stock_cells_are_clickable() -> None:
    # 2026-11-07：作战表插件已整体删除（预警页顶替），本断言只留模拟交易（原纸面）账本
    for path, label in ((PAPER, "模拟交易账本"),):
        src = _read(path)
        assert "data-open-kline=" in src, "%s 个股单元格缺 data-open-kline" % label
        assert "data-open-name=" in src, "%s 缺 data-open-name（列表上下文要用）" % label


def test_interactive_controls_not_hijacked() -> None:
    """行/卡片带 data-open-kline 后，行内按钮（撤单/签收/删除）仍得点得动。"""
    src = _read(SHELL_JS)
    body = _fn_body(src, "function bindKlineBridge()")
    assert "button, a, input, select, textarea, label" in body, "未排除交互控件"


def test_kline_list_keeps_names_when_row_container_hits_first() -> None:
    """自选卡的行容器带 data-code 无名称、名称节点在后面：先命中行时也要补上名称，
    否则 ↑↓ 翻页只显示代码（主人 2026-10-28 反馈）。"""
    src = _read(SHELL_JS)
    body = _fn_body(src, "function klineListOf(node)")
    assert "if (!seen[c].name && nm) seen[c].name = nm;" in body, "重复 code 时未补名称"
    assert "globalNames" in body, "缺全页名称字典兜底"
    assert "t.length <= 8" in body, "缺节点文本兜底（只给代码列时取名称）"


def test_stock_code_normalized_before_open() -> None:
    """6 位代码要补交易所后缀：旧 F10 面板只认 ts_code（300432.SZ），
    否则「图能画、F10 卡片不弹」（主人 2026-10-28 在板块详情里发现）。"""
    src = _read(SHELL_JS)
    body = _fn_body(src, "function normalizeStockCode(code)")
    for suffix in ("'.SH'", "'.SZ'", "'.BJ'"):
        assert suffix in body, "代码规范化缺 %s" % suffix
    assert "ctxOpenKline(normalizeStockCode(code.trim()), name, klineListOf(node))" in src, \
        "点击路径未先规范化代码"
    assert "const c = normalizeStockCode(raw);" in _fn_body(src, "function klineListOf(node)"), \
        "列表上下文未规范化代码（↑↓ 翻票后 F10 会再丢）"


def test_overlay_stack_orders_z_index() -> None:
    """后开的层必须盖住先开的层（主人标注 43：自选池先开 K 线再开板块详情，详情被挡）。"""
    body = _fn_body(_read(SHELL_JS), "function overlayStack()")
    assert "syncZ" in body and "Z_BASE" in body and "Z_STEP" in body, "浮层栈未按栈序重排 z-index"
    assert "el.style.zIndex" in body, "未写入元素 z-index"


def test_kline_list_panel_and_f10_toggle() -> None:
    """左侧列表卡（可收起/展开）+ F10 卡开关（主人 2026-10-28）。

    列表卡数据只来自 openKline 的 list 参数；切票高亮靠观察 #klineTitle，
    F10 显隐仍由 kline_f10.js 控 display，本层只加宽度 class。
    """
    legacy_dir = APP / "plugins" / "kline" / "frontend" / "legacy"
    html = _read(legacy_dir / "modal.html")
    assert 'id="kListPanel"' in html and 'class="k-list-panel"' in html, "缺左侧列表卡容器"
    assert 'id="btnListToggle"' in html and 'id="btnF10Toggle"' in html, "缺列表/F10 开关"
    js = _read(KLINE_LEGACY)
    for fn in ("function renderKList()", "function applyListState()", "function applyF10State()"):
        assert fn in js, "缺 %s" % fn
    assert "v2.kline.listOpen" in js and "v2.kline.f10Open" in js, "显隐状态未持久化"
    assert "MutationObserver" in _fn_body(js, "function bindPanels()"), "切票未同步高亮"
    assert "curList = Array.isArray(opt.list)" in js, "openKline 未保存列表上下文"
    assert "function prefetchAround()" in js and "PREFETCH_TTL" in js, "缺相邻票预取"
    assert "schedulePrefetch()" in _fn_body(js, "function syncKListActive(scroll)"), \
        "切票后未触发相邻票预取"
    css = _read(legacy_dir / "modal.css")
    assert ".k-list-panel {" in css and ".k-list-panel.open" in css, "列表卡样式缺失"
    assert ".kfp-collapsed" in css, "F10 收起样式缺失"
    assert "#klineModal.with-list .kbox" in css and "#klineModal.no-f10 .kbox" in css, \
        "卡片开关未联动 K 线宽度"


def test_note_layer_above_legacy_kline_modal() -> None:
    """标注层必须高于旧 K 线弹窗（z-index:100），否则标不到弹窗内元素。"""
    css = _read(SHELL_CSS)
    for var in ("--z-note", "--z-note-pop", "--z-note-fab"):
        m = re.search(re.escape(var) + r":\s*(\d+)", css)
        assert m and int(m.group(1)) > 100, "%s 必须 > 100（旧弹窗层级）" % var
