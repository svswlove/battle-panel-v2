# -*- coding: utf-8 -*-
"""v2 页面标注（元素级）的静态检查（2026-10-26）。

守的是「v2 壳只能标整页/整块」这条主人反馈的结构性修复：
  1. 选择必须在 **v2 文档上下文**里做（`document.elementsFromPoint`），
     且默认取最内层 —— 旧实现把标注挂在父页时，父页只看得到整块 <iframe>；
  2. 粒度可控：`上一层` / `最内层` 两个按钮真的接到同一个 pick 结果上；
  3. 提交走**本页直连** `/api/ui_notes/add`，字段与 ops 插件 `UiNoteBody` 逐字对齐；
  4. `page` 带 `v2:` 前缀（旧页按 hash 存，两边不能混）；
  5. 标注模式在 **capture 阶段**拦点击并放行壳层 UI；
  6. 只用主题 token，不写裸 hex（门禁 test_no_raw_hex_in_any_card 管卡片，壳层同样不破例）。
只读源码，不跑浏览器。
"""
import re
from pathlib import Path

PANEL = Path(__file__).resolve().parents[2]
WEB = PANEL / "app" / "web"
NOTE = WEB / "v2_note.js"
SHELL_JS = WEB / "shell.js"
SHELL_CSS = WEB / "shell.css"
SHELL_HTML = WEB / "shell.html"
OPS_BACKEND = PANEL / "app" / "plugins" / "ops" / "backend.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _note() -> str:
    return _read(NOTE)


# ---------------------------------------------------------------- 1. 文件与入口


def test_note_module_exists_and_is_wired() -> None:
    """模块必须真被壳加载（否则等于没做：v2 页面原先连标注脚本都没有）。"""
    assert NOTE.is_file(), "缺 app/web/v2_note.js"
    html = _read(SHELL_HTML)
    assert "/static/v2_note.js" in html, "shell.html 未引入 v2_note.js"
    assert 'id="rail-note"' not in html, "rail 标注按钮已移除（入口改悬浮按钮，否则被 K 线弹窗压住）"
    src = _read(SHELL_JS)
    assert "bindNote()" in src, "shell.js 未调用 bindNote()"
    assert "state.note" in src, "shell.js 未保存标注模块实例"


def test_note_module_exports_for_shell() -> None:
    """两个出口都要在：动态 import 拿 default，早于 import 的时序靠 window.__v2Note。"""
    src = _note()
    assert "window.__v2Note" in src, "缺 window.__v2Note 出口（壳侧时序兜底）"
    assert re.search(r"export default api;", src), "缺默认导出"


def test_entry_is_floating_draggable_above_kline() -> None:
    """标注入口 2026-10-28 起是悬浮可拖动按钮，且层级高于 K 线弹窗（z=100）。

    主人标注 N20261028-100025-36：K 线弹窗打开后遮罩压住 rail 按钮，想标 K 线图时点不到按钮。
    守卫：① rail 不再有标注按钮；② 模块自建 un-fab 并挂 body；③ 拖动 + 位置持久化；
          ④ --z-note-fab 必须高于旧弹窗的 100。
    """
    note = _note()
    assert "function syncEntry(" in note, "缺 syncEntry（入口重算）"
    assert "syncEntry: syncEntry," in note, "syncEntry 未对外暴露给壳"
    assert "mk('button', 'un-fab')" in note, "缺悬浮入口按钮"
    assert "document.body.append(btn)" in note, "悬浮按钮未挂到 body"
    for token in ("pointerdown", "pointermove", "pointerup", "FAB_POS_KEY", "fabPosSave", "applyFabPos"):
        assert token in note, "缺拖动/位置记忆：%s" % token
    # 被旧页嵌进 iframe 时父页已有「✎ 标注」入口，再叠浮标 = 同一屏两个同类按钮
    assert "if (embedded()) {" in note, "嵌入形态未拦截浮标创建"

    css = _read(SHELL_CSS)
    m = re.search(r"--z-note-fab:\s*(\d+)", css)
    assert m and int(m.group(1)) > 100, "--z-note-fab 必须高于 #klineModal 的 z-index=100"
    assert ".un-fab.is-drag" in css, "缺拖动中的光标态"

    shell = _read(SHELL_JS)
    assert "function syncNoteEntry()" in shell, "壳侧缺 syncNoteEntry"
    assert shell.count("syncNoteEntry();") >= 2, "boot 只调了一次入口重算（覆盖不到另一种时序）"


# ---------------------------------------------------------------- 2. 元素选择


def test_pick_uses_elements_from_point_deepest_first() -> None:
    """核心修复：用 elementsFromPoint 取候选链，按返回顺序（由内到外）取第一个。"""
    src = _note()
    assert "document.elementsFromPoint(" in src, "未用 elementsFromPoint（选不到内层）"
    assert "document.elementFromPoint(" in src, "缺 elementFromPoint 兜底"
    body = src[src.index("function pickAt("):]
    body = body[: body.index("\n}")]
    assert "for (let i = 0; i < list.length; i++)" in body, "未按候选链顺序遍历"
    assert "return el;" in body, "命中即返回（默认最内层）"
    # 自家 UI / 壳层 UI / body、documentElement 必须跳过，否则会框住标注框自己
    assert "isOwn(el)" in body and "isShellUi(el)" in body, "未跳过自家/壳层节点"
    assert "document.body" in body and "document.documentElement" in body


def test_canvas_overlay_layer_is_skipped() -> None:
    """2026-10-30（主人报「卡片类的东西会定位到下面的一层」）：分层绘制的 canvas 要跳到有内容那层。

    lightweight-charts 每个窗叠 2 张同尺寸 canvas：z=1 画网格/柱/线，z=2 只画十字光标与标签。
    elementsFromPoint 最内层永远是 z=2 —— 必须按「同容器兄弟层的相对内容量」跳过它。
    绝对值判据（有没有像素）不行：鼠标一进图，覆盖层立刻画十字线，实测 0.2%~0.7%。
    """
    src = _note()
    assert "function canvasOverlay(" in src, "缺覆盖层判定"
    body = src[src.index("function canvasOverlay("):]
    body = body[: body.index("\n}")]
    assert "parent.querySelectorAll('canvas')" in body, "未与同容器兄弟 canvas 比较"
    assert "canvasInkRatio(" in body, "未量绘制内容"
    assert "OVERLAY_RATIO" in body, "缺相对阈值（绝对值判据会被十字光标骗过）"
    pick = src[src.index("function pickAt("):]
    pick = pick[: pick.index("\n}")]
    assert "canvasOverlay(el, force)" in pick, "pickAt 未接覆盖层判定"
    assert "continue" in pick, "命中覆盖层未继续往下沉"
    assert "pickAt(event.clientX, event.clientY, true)" in src, "点击时未强制重算绘制层"
    assert "return fallback;" in pick, "全无可判层时未退回原命中元素"


def test_granularity_buttons_present_and_wired() -> None:
    """上一层 / 最内层 两个粒度按钮必须既有 UI 又有处理（主人自定粒度）。"""
    src = _note()
    assert "↑ 上一层" in src and "↓ 最内层" in src, "缺粒度切换按钮"
    assert 'data-un-act="up"' in src and 'data-un-act="deep"' in src, "按钮未带动作标记"
    assert "st.cur = p;" in src, "「上一层」未改目标"
    assert "st.cur = st.deepest" in src, "「最内层」未回到最初命中的元素"
    assert "st.deepest = el;" in src, "点击时未记录最内层元素"


def test_highlight_overlay_is_fixed_and_click_through() -> None:
    """高亮框：独立 overlay 层，fixed 定位 + pointer-events:none（不吃事件）。"""
    css = _read(SHELL_CSS)
    seg = css[css.index(".un-hi{"): css.index(".un-hi[hidden]")]
    assert "position:fixed" in seg, "高亮框不是 fixed"
    assert "pointer-events:none" in seg, "高亮框会吃鼠标事件"
    assert "var(--accent)" in seg, "高亮框颜色未走 token"


def test_selector_generated_in_this_document() -> None:
    """selector 必须在 v2 文档上下文生成（旧页存下的 selector 对 v2 无效）。"""
    src = _note()
    assert "function path(node)" in src, "缺 selector 生成"
    body = src[src.index("function path(node)"):]
    body = body[: body.index("\n}")]
    assert "nth-child(" in body, "selector 缺 nth-child 链"
    assert "n.tagName.toLowerCase()" in body, "selector 未用本文档元素"
    assert "PATH_DEPTH" in body, "selector 链未限深"


# ---------------------------------------------------------------- 3. 提交接口对齐


def _ops_note_body_fields() -> list:
    """从 ops 插件源码里取 UiNoteBody 的字段名（唯一权威）。"""
    src = _read(OPS_BACKEND)
    start = src.index("class UiNoteBody(BaseModel):")
    end = src.index("class ", start + 10)
    block = src[start:end]
    return re.findall(r"^\s{4}([a-z_]+):", block, re.M)


def test_submit_fields_match_ops_ui_note_body() -> None:
    """提交体字段必须与 ops 的 UiNoteBody 一一对应（写错字段=静默丢内容）。"""
    fields = _ops_note_body_fields()
    assert fields == ["selector", "text", "page", "elem", "html", "style", "x", "y"], (
        "ops UiNoteBody 字段变了，本测试与 v2_note.js 需同步：%s" % fields
    )
    src = _note()
    body = src[src.index("function buildBody("):]
    body = body[: body.index("\n}")]
    for field in fields:
        assert field + ":" in body, "buildBody 缺字段 %s" % field


def test_submit_endpoints_and_methods() -> None:
    """写盘走本页直连（v2 与旧页同源同进程，不需要父页代理）。"""
    src = _note()
    assert "const API_ADD = '/api/ui_notes/add';" in src, "新增端点不对"
    assert "const API_UPDATE = '/api/ui_notes/update';" in src, "修改端点不对"
    assert "'method': 'POST'" in src or "method: 'POST'" in src, "提交未用 POST"
    assert "window.parent.postMessage({ type: 'uiNote:add'" not in src, (
        "不应再走旧页的父页代理写盘（任务书：提交仍走本页直连）"
    )


def test_page_field_uses_v2_prefix() -> None:
    """page 用 v2: 前缀区分旧页标注（旧页 page=location.hash）。"""
    src = _note()
    assert "const PAGE_PREFIX = 'v2:';" in src, "缺 v2: 前缀常量"
    body = src[src.index("function pageKey()"):]
    body = body[: body.index("\n}")]
    assert "PAGE_PREFIX +" in body, "page 未加前缀"
    assert "card:" in body or "cardKey" in body, "page 未区分卡（不同卡的 selector 不能混）"


# ---------------------------------------------------------------- 4. 事件纪律


def test_click_intercepted_in_capture_and_shell_ui_passes() -> None:
    """标注模式必须 capture 阶段拦截并阻止默认；壳层 UI 放行（否则退不出模式）。"""
    src = _note()
    start = src.index("document.addEventListener('click'")
    body = src[start: start + 900]
    assert "if (!st.on) return;" in body, "未按模式开关判断"
    assert "isShellUi(target)" in body, "壳层 UI 未被放行（按钮会点不动）"
    assert "event.preventDefault();" in body, "未阻止默认行为"
    assert "event.stopPropagation();" in body, "未阻止传播（卡片点击会跟着跑）"
    assert "}, true);" in body, "click 未挂 capture 阶段"
    assert "document.addEventListener('dblclick'" in src, "双击未拦（卡片会误收起）"


def test_esc_and_toggle_and_rightclick_exit() -> None:
    """三种退出：ESC、再点按钮、右键（与旧页一致）。"""
    src = _note()
    assert "event.key !== 'Escape'" in src, "缺 ESC 处理"
    assert "setMode(false)" in src, "退出未落到 setMode(false)"
    assert "document.addEventListener('contextmenu'" in src, "缺右键退出"
    assert "function toggle()" in src and "setMode(!st.on)" in src, "按钮未接开关"


def test_parent_handshake_receive_only() -> None:
    """被旧页嵌进 iframe 时只**接收** uiNote:setMode / uiNote:list，提交仍本页直连。"""
    src = _note()
    assert "MSG_SET_MODE = 'uiNote:setMode'" in src, "未接父页模式消息"
    assert "MSG_LIST = 'uiNote:list'" in src, "未接父页列表消息"
    assert "event.source !== window.parent" in src, "未校验消息来源是父页"
    assert "uiNote:hello" in src, "缺就绪握手（父页据此补发模式）"


# ---------------------------------------------------------------- 5. 主题纪律


def test_note_layer_has_no_raw_hex() -> None:
    """标注层（JS + CSS 段落）不得出现裸色值，颜色只走 themes.css token。"""
    js = _note()
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", js), "v2_note.js 出现裸 hex"
    css = _read(SHELL_CSS)
    seg = css[css.index("标注模式（2026-10-26"):]
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", seg), "标注段 CSS 出现裸 hex"


def test_note_classes_are_scoped() -> None:
    """壳层组件类名统一 un- 前缀，不进卡片作用域。"""
    src = _note()
    for cls in ("un-hi", "un-tip", "un-hud", "un-box", "un-pin", "un-fab"):
        assert cls in src, "缺类名 %s" % cls
    css = _read(SHELL_CSS)
    assert ".un-box" in css and ".rail-btn.is-on" in css


# ---------------------------------------------------------------- 6. 锚点健壮（2026-11-06 P2）


def test_selector_prefers_unique_over_nth_child() -> None:
    """存盘 selector 优先「全页唯一」而不是「第几个孩子」；不唯一才退回老路径。"""
    src = _note()
    assert "function uniqueSel(el)" in src, "缺唯一性 selector 生成"
    body = src[src.index("function uniqueSel(el)"):]
    body = body[: body.index("\n}\n")]
    assert "qsaCount(sel) === 1" in body, "未按全页唯一作收敛条件"
    assert "return path(el)" in body, "不唯一时必须退回老路径（否则无短路）"
    # 存盘用新函数，不再直接写老 path()
    build = src[src.index("function buildBody("):]
    build = build[: build.index("\n}\n")]
    assert "selector: uniqueSel(el)" in build, "buildBody 仍用老 path()"


def test_anchor_falls_back_strictly() -> None:
    """老 selector 断链时逐段退化；**多处命中不认**（误锚比不锚更坑）。"""
    src = _note()
    assert "function anchorCandidates(" in src, "缺退化候选生成"
    assert "function resolveTarget(" in src, "缺锚点解析"
    body = src[src.index("function resolveTarget("):]
    body = body[: body.index("\n}\n")]
    assert "hit.length !== 1" in body, "多处命中未拒绝（会锚到错的元素）"
    assert "isConnected" in body, "未判元素是否还在文档里"


def test_pins_render_seq_and_clip_out_of_view() -> None:
    """圆点：只写序号（不写完整 id）；整块移出视口不画（否则粘在屏幕边缘）。"""
    src = _note()
    body = src[src.index("function renderPins()"):]
    body = body[: body.index("\n}\n")]
    assert "String(seq)" in body, "圆点仍在写完整 id（18px 装不下）"
    assert "note.id != null ? note.id" not in body, "旧写法未清"
    assert "r.bottom < 0" in body and "r.top > window.innerHeight" in body, "缺视口裁剪"
    assert "resolveTarget(note)" in body, "渲染未走锚点兑底"


def test_card_switch_refreshes_notes() -> None:
    """切卡必须通知标注层重拉（否则新卡不出现、旧卡圆点残留）。"""
    src = _note()
    body = src[src.index("async function refreshNotes()"):]
    body = body[: body.index("\n}\n")]
    assert "_lastPage" in body, "缺页面切换检测"
    assert "st.notes = [];" in body, "切卡未先清旧圆点（会在新卡上停一拍）"
    shell = _read(SHELL_JS)
    sel = shell[shell.index("async function selectCard("):]
    sel = sel[: sel.index("\n}\n")]
    assert "state.note.refresh()" in sel, "selectCard 未通知标注层"
