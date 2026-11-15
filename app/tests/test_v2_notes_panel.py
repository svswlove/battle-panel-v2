# -*- coding: utf-8 -*-
"""标注清单 + 生命周期面板的静态契约检查（2026-10-27）。

守的是「面板真的接上了、且没绕过唯一写口」这几件事（只读源码，不跑浏览器）：
  1. 入口按钮 `#rail-notes` 在 rail 里、位于 #rail-note 之上，且壳真的 init 了模块；
  2. 写口：`POST /api/ui_notes/stage`（流转，带 expected/by）+ `delete`/`purge`（删除，2026-11-06），
     不出现 add/update/resolve 直写；
  3. `expected` 取当前阶段、`by` 固定 01 —— 乐观并发是防覆盖的唯一手段，写错就静默覆盖；
  4. 409 分支必须存在且走「提示 + 重拉」，不许拿旧 stage 硬重试；
  5. 旧页（非 v2: 前缀）不定位、不导航；
  6. 样式只用 themes.css 语义 token（禁裸 hex / 裸 rgba），类名 np- 前缀；
  7. 语法：`node --check` 三个 JS 文件（含单测与 CDP 脚本）。

为什么这些要静态守：面板写的是生产 `engine/state/ui_notes.json`，一次错误的 to
就是把别人的标注推到错阶段；而 409 分支是纯异步路径，靠肉眼 review 最容易漏。
"""
import re
import subprocess
from pathlib import Path

PANEL = Path(__file__).resolve().parents[2]
WEB = PANEL / "app" / "web"
TESTS = PANEL / "app" / "tests"
PANEL_JS = WEB / "v2_notes_panel.js"
SHELL_JS = WEB / "shell.js"
SHELL_CSS = WEB / "shell.css"
SHELL_HTML = WEB / "shell.html"
UNIT = TESTS / "js" / "v2_notes_panel.test.mjs"
CDP = TESTS / "js" / "v2_notes_panel.cdp.mjs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _js() -> str:
    return _read(PANEL_JS)


def _css_segment() -> str:
    """面板样式段：从分隔注释到文件末尾（新增段落只能追加在后面，段界就是注释行）。"""
    css = _read(SHELL_CSS)
    marker = "标注清单面板（2026-10-27"
    assert marker in css, "shell.css 缺标注清单段（注释头被删？）"
    return css[css.index(marker):]


# ---------------------------------------------------------------- 1. 入口与接线


def test_panel_module_exists() -> None:
    assert PANEL_JS.is_file(), "缺 app/web/v2_notes_panel.js"
    src = _js()
    assert "export default api;" in src, "缺默认导出"
    assert "window.__v2NotesPanel" in src, "缺 window.__v2NotesPanel 出口（时序兜底）"


def test_rail_button_above_note_button() -> None:
    """标注清单入口在 rail 里；标注模式入口 2026-10-28 起改为悬浮按钮（不在 rail）。"""
    html = _read(SHELL_HTML)
    assert 'id="rail-notes"' in html, "rail 缺 #rail-notes 入口"
    assert 'id="rail-note"' not in html, "#rail-note 已移除（入口改悬浮按钮，避免被 K 线弹窗压住）"
    btns = html[html.index('id="rail-notes"') - 200: html.index('id="rail-notes"') + 600]
    assert 'aria-label="标注清单"' in btns, "按钮缺 aria-label"
    assert '标注清单' in btns, "按钮缺标题文案"


def test_shell_wires_panel_with_selectcard_and_titles() -> None:
    """壳侧只接线：传 selectCard / cardKey / cardTitle / toast / onOpen。"""
    src = _read(SHELL_JS)
    assert "async function bindNotesPanel()" in src, "壳侧缺 bindNotesPanel"
    assert "bindNotesPanel()" in src.split("function bindNotesPanel()")[1], "bindNotesPanel 定义了但没调用"
    assert "state.notesPanel" in src, "壳未保存面板实例"
    seg = src[src.index("async function bindNotesPanel()"):]
    seg = seg[: seg.index("\n}\n")]
    for hook in ("selectCard: selectCard", "cardKey:", "cardTitle:", "toast: toast", "onOpen:"):
        assert hook in seg, "接线缺 %s" % hook
    assert "button: el.railNotes" in seg, "未把 #rail-notes 交给面板"
    assert "closeSettings();" in seg, "开面板时未收起设置抽屉"
    assert "allCards" in seg, "卡标题应查全量卡片（单卡模式下 cards 只剩一张）"


def test_note_mode_behavior_unchanged() -> None:
    """壳对 v2_note 的接线（cardKey/onMode）保留；入口由模块自建（不再传 rail 按钮）。"""
    src = _read(SHELL_JS)
    seg = src[src.index("async function bindNote()"): src.index("async function bindNotesPanel()")]
    assert "button: el.railNote" not in seg, "入口已改悬浮按钮，不应再传 rail 按钮"
    assert "cardKey:" in seg
    assert "onMode:" in seg
    note = _read(WEB / "v2_note.js")
    assert "v2_notes_panel" not in note, "v2_note.js 不该被面板侵入（任务书：不改它）"


# ---------------------------------------------------------------- 2. 接口纪律


def test_only_stage_endpoint_writes() -> None:
    """写口：stage（流转）+ delete/purge（删除）；不得出现 add/update/resolve 直写。"""
    src = _js()
    assert "const API_LIST = '/api/ui_notes/list';" in src, "列表端点常量不对"
    assert "const API_STAGE = '/api/ui_notes/stage';" in src, "流转端点常量不对"
    assert "const API_DELETE = '/api/ui_notes/delete';" in src, "删除端点常量不对"
    assert "const API_PURGE = '/api/ui_notes/purge';" in src, "清理端点常量不对"
    assert "const BY = '01';" in src, "处理人签名常量不对"
    for bad in ("/api/ui_notes/add", "/api/ui_notes/update", "/api/ui_notes/resolve"):
        assert bad not in src, "面板不该直调 %s（新建/改字走 v2_note）" % bad


def test_delete_and_purge_are_guarded() -> None:
    """删除也带 expected（与 stage 同口径）；批量清理只许 closed 且带条数校验。"""
    src = _js()
    d = src[src.index("async function submitDelete("):]
    d = d[: d.index("\n}\n")]
    assert "const expect = stageOf(note);" in d, "删除未取当前阶段"
    assert "expected: expect" in d, "删除未带 expected（会用旧阶段覆盖别人）"
    assert "res.status === 409" in d, "删除未处理 409"
    assert "confirmDelete(" in d, "删除无确认（不可逆操作）"
    assert "window.confirm" in src, "确认框未落实现（confirmDelete 内部要弹）"


def test_stage_body_carries_expected_and_by() -> None:
    """每个流转都带 expected=当前 stage 与 by：缺 expected = 会用旧阶段覆盖别人的流转。"""
    src = _js()
    assert "function stageBody(" in src, "缺 stageBody"
    body = src[src.index("function stageBody("):]
    body = body[: body.index("\n}\n")]
    assert "expected: stageOf(note)" in body, "expected 未取当前阶段"
    assert "by: BY" in body, "by 未取常量"
    assert "STAGES.indexOf(String(to || '')) < 0" in body, "非法 to 未在发请求前拦下"
    # 提交处真的用了 stageBody（而不是手拼对象）
    submit = src[src.index("async function submitStage("):]
    submit = submit[: submit.index("\n// ---------------------------------------------------------------- 定位")]
    assert "stageBody(note, to, draft)" in submit, "submitStage 未用 stageBody"


def test_409_handled_by_toast_and_refetch() -> None:
    """409 = 别人先动过：提示 + 重拉，不硬重试、不静默吞。"""
    src = _js()
    submit = src[src.index("async function submitStage("):]
    submit = submit[: submit.index("\n// ---------------------------------------------------------------- 定位")]
    assert "res.status === 409" in submit, "未处理 409"
    assert "阶段已变化，已刷新" in submit, "409 缺提示文案"
    seg = submit[submit.index("res.status === 409"):]
    seg = seg[: seg.index("return;")]
    assert "fetchList()" in seg, "409 后未重拉"
    assert "toast(" in seg, "409 未提示用户"


def test_error_paths_have_messages() -> None:
    """空态/读取失败/保存失败都要有明确文案（静默失败等于没做）。"""
    src = _js()
    assert "读取失败：" in src, "读取失败无文案"
    assert "保存失败：" in src, "保存失败无文案"
    assert "还没有标注" in src, "空态无文案"
    assert "元素已不存在" in src, "定位失效无提示"
    assert "旧页标注不可定位" in src, "旧页标注未明确拒绝定位"


# ---------------------------------------------------------------- 3. 交互口径


def test_actions_cover_all_five_stages() -> None:
    """五阶段各有动作，且映射与任务书逐条一致（写错就是把标注推到错阶段）。"""
    src = _js()
    body = src[src.index("const ACTIONS = {"):]
    body = body[: body.index("\n};")]
    expect = {
        "open": ["doing"],
        "doing": ["fixed", "rejected"],
        "fixed": ["verified", "doing", "rejected"],
        "verified": ["open"],
        "rejected": ["open"],
    }
    for stage, tos in expect.items():
        line = body[body.index(stage + ": ["):]
        line = line[: line.index("]")]
        found = re.findall(r"to: '([a-z]+)'", line)
        assert found == tos, "%s 的动作应为 %s，实际 %s" % (stage, tos, found)


def test_locate_waits_for_card_host() -> None:
    """跨卡定位必须「切卡 + 轮询最多 3s」，否则切完立刻查必然报「元素不存在」。"""
    src = _js()
    seg = src[src.index("async function locate("):]
    seg = seg[: seg.index("\n}\n\n// ---------------------------------------------------------------- 事件")]
    assert "selectCard" in seg, "跨卡定位未走壳的 selectCard"
    assert "3000" in seg, "缺 3s 上限"
    assert "await sleep(" in seg, "未轮询等待卡片挂载"
    assert "scrollIntoView" in seg, "未滚动到目标"
    assert "1200" in src, "闪烁不是 1.2s"
    assert "旧页标注不可定位" in seg, "旧页未拒绝定位"


def test_retired_pages_marked_and_not_located() -> None:
    """非 v2: 前缀 = 旧页：标「旧页已退役」，locate=false。"""
    src = _js()
    body = src[src.index("function pageInfo("):]
    body = body[: body.index("\n}\n")]
    assert "'旧页已退役'" in body, "退役标记缺失"
    assert "locate: false" in body, "退役页仍可定位"
    assert "locate: true" in body


def test_grouping_and_sorting() -> None:
    """按 page 分组、组内 created_at 倒序、退役组沉底。"""
    src = _js()
    body = src[src.index("function groupNotes("):]
    body = body[: body.index("\n}\n")]
    assert "sortKey(b.created_at).localeCompare" in body, "组内未按 created_at 倒序"
    assert "kind === 'retired' ? 1 : 0" in body, "退役组未沉底"
    assert "pageInfo(page, titleOf)" in body, "组标题未走 pageInfo（卡名/退役标）"


def test_search_matches_four_fields() -> None:
    src = _js()
    body = src[src.index("function visibleNotes("):]
    body = body[: body.index("\n}\n")]
    for field in ("note.text", "note.elem", "note.id", "page"):
        assert field in body, "搜索缺字段 %s" % field
    assert ".toLowerCase()" in body, "搜索未归一大小写"


# ---------------------------------------------------------------- 4. 样式纪律


def test_panel_css_has_no_raw_colors() -> None:
    """面板样式只用 token：禁裸 hex，也禁自拼 rgba（壳层同样不破例）。"""
    seg = _css_segment()
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", seg)
    assert not hexes, "面板样式出现裸 hex：%s" % hexes[:6]
    rgbas = re.findall(r"rgba?\([^)]*\)", seg)
    assert not rgbas, "面板样式出现裸 rgba：%s" % rgbas[:6]
    assert "var(--card)" in seg and "var(--accent)" in seg, "面板样式未走语义 token"


def test_panel_tokens_are_defined() -> None:
    """引用的 token 必须在 themes.css 里有定义（拼错 token = 样式静默失效）。"""
    themes = _read(PANEL.parents[1] / "engine" / "web" / "themes.css")
    tokens = set(re.findall(r"(--[a-z0-9-]+)\s*:", themes))
    local = set(re.findall(r"(--[a-z0-9-]+)\s*:", _read(SHELL_CSS)))
    used = set(re.findall(r"var\((--[a-z0-9-]+)", _css_segment()))
    missing = sorted(t for t in used if t not in tokens and t not in local)
    assert not missing, "面板样式引用未定义 token：%s" % missing


def test_panel_classes_exist_in_css() -> None:
    """JS 建的类名必须都有样式，否则面板是一片无样式裸 DOM。"""
    src = _js()
    seg = _css_segment()
    for cls in ("np-drawer", "np-hd", "np-stats", "np-chips", "np-q", "np-body",
                "np-group", "np-note", "np-badge", "np-prog", "np-seg", "np-his",
                "np-doc", "np-acts", "np-act", "np-err", "np-empty", "np-hit"):
        assert ("'" + cls) in src or ('"' + cls) in src or (' ' + cls) in src, "JS 未用到类名 %s" % cls
        assert ("." + cls) in seg, "样式缺 .%s" % cls
    # 抽屉宽度与层级按任务书
    drawer = seg[seg.index(".np-drawer{"): seg.index(".np-drawer.on")]
    assert "width:440px" in drawer, "抽屉宽度不是 440px"
    assert "max-width:92vw" in drawer, "缺 max-width:92vw"
    assert "z-index:var(--z-drawer)" in drawer, "抽屉层级未复用设置抽屉层"


def test_drawer_uses_shared_scrim() -> None:
    """复用全局 #scrim（不新造遮罩），关闭时自己摘 .on。"""
    src = _js()
    assert "getElementById('scrim')" in src, "未复用 #scrim"
    assert "classList.remove('on')" in src, "关闭时未摘 .on"


# ---------------------------------------------------------------- 5. 语法


def _node_check(path: Path) -> None:
    proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True,
                          cwd=str(PANEL))
    assert proc.returncode == 0, "node --check 失败 %s：%s" % (path.name, proc.stderr[-800:])


def test_js_syntax_ok() -> None:
    for path in (PANEL_JS, SHELL_JS, UNIT, CDP):
        assert path.is_file(), "缺文件 %s" % path
        _node_check(path)


def test_unit_test_covers_pure_functions() -> None:
    """单测必须真的覆盖六类纯函数（否则「有测试」只是文件存在）。"""
    src = _read(UNIT)
    for fn in ("stageOf", "pageCardKey", "pageInfo", "groupNotes", "visibleNotes",
               "actionsFor", "progressOf", "fmtTime", "fmtHistory", "stageBody"):
        assert "M." + fn in src, "单测缺 %s" % fn


def test_cdp_script_stubs_api_and_never_writes_production() -> None:
    """CDP 验收必须 stub /api/ui_notes/*（绝不能真写生产 ui_notes.json）。"""
    src = _read(CDP)
    assert "addScriptToEvaluateOnNewDocument" in src, "未在页面脚本前注入 stub"
    assert "/api/ui_notes/" in src, "stub 未覆盖 ui_notes 接口"
    # 假数据必须显式声明，且 stub 要拦 POST（stage 请求只能进桩，不能落盘）
    assert "POST" in src or "method" in src, "stub 未区分方法"
    assert "expected" in src, "未校验请求体里的 expected"
    assert "_vis" in src or "notes_panel" in src, "缺截图目录"
