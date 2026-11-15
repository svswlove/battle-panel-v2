# -*- coding: utf-8 -*-
"""concept 卡片前端静态检查（只读，不跑浏览器）。

守的是 docs/task_R3_concept_review.md §1/§3 与 docs/concept_contract.md §卡片：
  · 零 import、零裸 fetch、零写盘、零子进程、不写全局、零裸 hex
  · 端点只用绝对 /api/ 路径（concept 插件 mount=root，与旧端同名，不拼插件前缀）
  · 四视图胶囊与旧 renderConcept 一致（活跃/观察池/链位/已归档 + 计数）
  · 概念卡可点（data-slug）→ 详情（?slug=）；详情返回列表；筛选框只在活跃/观察池
  · 个股名只写 data-open-kline 占位、板块写 data-open-amv，无码 → cp-chip-off（不伪装可点）
  · 链位卡片三段（上游→本环节→下游）；观察池 bull/bear 光谱与徽标
  · 不声明 refreshEvery（总表日频），保留 refresh() 占位
"""
import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND_JS = PLUGIN_DIR / "frontend.js"
MANIFEST = PLUGIN_DIR / "plugin.json"

# 与 tools/cutover_gate.py::_check_no_write_code 同一份禁令（此处只守本插件前端）
BANNED = (
    ".write_text(", ".write_bytes(", ".mkdir(", ".touch(", ".unlink(", ".rmdir(", ".symlink_to(",
    "os.remove(", "os.unlink(", "os.rename(", "os.replace(", "os.makedirs(", "shutil.rmtree(",
    "shutil.move(", "shutil.copy", "json.dump(", ".to_sql(", ".to_csv(", ".to_parquet(",
    "INSERT INTO", "INSERT OR", "UPDATE ", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
    "requests.post", "requests.put", "requests.delete", "requests.patch",
    "urllib.request.urlopen", "httpx.post", "httpx.put", "subprocess.", "os.system(", "os.popen(",
    "Popen(",
)
OPEN_WRITE_RE = re.compile(r"""open\s*\([^)]{0,240}?,\s*["'](?:w|a|wb|ab|x|xb)["']""")

# 旧 renderConcept 的四个视图（顺序与计数标签）
OLD_VIEWS = [["active", "活跃"], ["watch", "观察池"], ["chain", "链位"], ["archived", "已归档"]]


def _src() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 主题 / 样式


def test_no_raw_hex_colors() -> None:
    """颜色一律走 themes.css token，禁裸 hex（光谱段也用 --{color}-solid token）。"""
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_uses_theme_tokens() -> None:
    src = _src()
    for token in ("var(--up)", "var(--down)", "var(--warn)", "var(--accent)",
                  "var(--card2)", "var(--line-soft)", "var(--radius-md)"):
        assert token in src, "缺少主题 token %s" % token


def test_style_injected_once_and_idempotent() -> None:
    src = _src()
    assert "concept-card-css" in src
    assert "document.getElementById(STYLE_ID)" in src, "样式注入必须做 id 判重"
    assert not re.search(r"\.card\s*\{", src), "不得改写 shell 的 .card 规则"


# ---------------------------------------------------------------- 写盘 / 依赖


def test_no_write_or_subprocess_tokens() -> None:
    src = _src()
    hits = [t for t in BANNED if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src), "frontend.js 出现 open(...,'w') 写模式"


def test_zero_imports() -> None:
    assert not re.search(r"^\s*import\s", _src(), re.M), "frontend.js 不应有任何 import"


def test_no_global_writes() -> None:
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _src())


def test_no_bare_fetch() -> None:
    assert "fetch(" not in _src(), "应统一走 ctx.api.get，不直接 fetch"


# ---------------------------------------------------------------- 端点


def test_endpoints_are_absolute_old_names() -> None:
    """concept 插件 mount=root：路径与旧端同名，必须用绝对 /api/ 前缀（shell 不拼前缀）。"""
    calls = re.findall(r"""api\.get\(\s*['"]([^'"]+)['"]""", _src())
    assert calls, "找不到任何 api.get 调用"
    for path in calls:
        assert path.startswith("/api/"), "root 插件读端点必须用绝对 /api/ 路径：%s" % path
        assert not path.startswith("http"), "不得直连旧端：%s" % path
    assert "/api/concept.json" in calls
    assert any("?slug=" in c for c in calls), "详情端点必须带 slug 参数"


def test_no_write_endpoints() -> None:
    assert "api.post(" not in _src(), "本卡只读，不应有写端点"


def test_detail_slug_is_encoded() -> None:
    assert "encodeURIComponent(slug)" in _src(), "slug 必须 encodeURIComponent（中文目录名）"


# ---------------------------------------------------------------- 视图（对旧 renderConcept）


def test_views_match_old_render() -> None:
    m = re.search(r"const VIEWS = \[([\s\S]*?)\];", _src())
    assert m, "找不到 VIEWS 定义"
    got = re.findall(r"\['(\w+)', '([^']+)'\]", m.group(1))
    assert [list(x) for x in got] == OLD_VIEWS, "视图顺序/名称与旧 renderConcept 不一致：%s" % got


def test_tabs_show_counts() -> None:
    src = _src()
    assert "data-cptab=" in src, "视图切换按钮需带 data-cptab"
    assert "(c[key] || 0)" in src, "胶囊需显示各视图计数（旧 cp-pill <b>n</b>）"


def test_search_only_on_active_and_watch() -> None:
    """旧 _cpShowView：搜索框只在活跃/观察池显示。"""
    src = _src()
    assert "S.view === 'active' || S.view === 'watch'" in src, "筛选框显示条件与旧版不一致"
    assert "applyFilter" in src


def test_search_value_cleared_on_view_switch() -> None:
    """换视图清筛选值：否则切到链位/归档后旧关键字会把卡片静默藏掉。"""
    src = _src()
    m = re.search(r"const pill = t\.closest\('\[data-cptab\]'\);([\s\S]*?)\n        \}", src)
    assert m, "找不到视图切换分支"
    assert "S.el.input.value = ''" in m.group(1), "视图切换必须清筛选值"


def test_active_card_opens_detail_only_with_slug() -> None:
    """旧 _cpWire：只有 dataset.slug 的卡片才绑点击（无概况页不假装可点）。"""
    src = _src()
    assert "data-slug=" in src
    assert "'.cp-c[data-slug]'" in src, "点击委托只认带 data-slug 的卡"
    assert "无概况页" in src, "无 slug 的卡片需给出提示文案"


def test_detail_has_back_button() -> None:
    src = _src()
    assert 'data-back="1"' in src
    assert "返回列表" in src
    assert "backToList" in src


# ---------------------------------------------------------------- 个股名占位（不绑 K 线）


def test_kline_links_are_placeholders_only() -> None:
    """任务书 §2：卡内只写 data-open-kline / data-open-name 占位，桥接后旧页捕获。"""
    src = _src()
    assert 'data-open-kline="' in src
    assert 'data-open-name="' in src
    for forbidden in ("KLineApp", "kNameClick", "postMessage", "parent."):
        assert forbidden not in src, "本卡不得自己实现 K 线跳转（%s）" % forbidden


def test_placeholder_click_guard_precedes_detail() -> None:
    """审计 R35 P1-2：卡内点占位芯片不得被详情委托吞掉。

    旧页靠捕获阶段 stopPropagation 保证「芯片可点、卡不误触」（index.html:10936）；
    v2 卡在 iframe 里，父页捕获够不到，所以卡自己的 bubble 委托必须先判占位属性，
    命中即 return，不得落到 `.cp-c[data-slug]` 详情分支。
    """
    src = _src()
    m = re.search(r"wrap\.addEventListener\('click', \(ev\) => \{([\s\S]*?)\n      \}\);", src)
    assert m, "找不到卡内点击委托"
    body = m.group(1)
    guard = "t.closest('[data-open-kline],[data-open-amv]')"
    assert guard in body, "点击委托缺占位芯片前置判定"
    assert body.index(guard) < body.index("t.closest('.cp-c[data-slug]')"), \
        "占位判定必须在详情分支之前"
    assert re.search(re.escape(guard) + r"\)\s*return;", body), "命中占位必须直接 return（不进详情）"


def test_placeholder_titles_say_pending_bridge() -> None:
    """占位元素不可点，title 不得再写「点击看…」（卡内桥接尚未落地）。"""
    src = _src()
    assert "待桥接" in src, "占位 title 需明说「待桥接」"
    assert "· 点击看 K 线" not in src and "· 点击看 AMV" not in src, \
        "卡内无跳转能力，不得保留旧页「· 点击看…」title 文案"


def test_missing_code_is_explicitly_unclickable() -> None:
    src = _src()
    assert "cp-chip-off" in src, "解析不到代码必须显式不可点（cp-chip-off）"
    assert "chipHtml" in src


def test_sector_chips_use_amv_attribute() -> None:
    """板块 chip 走 data-open-amv（旧 cp-chip-sec 口径），与个股 chip 区分。"""
    src = _src()
    assert 'data-open-amv="' in src
    assert "cp-chip-sec" in src
    assert "▤ " in src, "板块 chip 需带 ▤ 前缀（旧口径）"


# ---------------------------------------------------------------- 链位 / 观察池


def test_chain_card_three_steps() -> None:
    src = _src()
    for label in ("上游", "本环节", "下游"):
        assert label in src, "链位卡缺 %s 段" % label
    assert "cp-flow-arrow" in src
    assert "up_detail" in src and "mid_detail" in src and "down_detail" in src, "必须优先用服务端补好的 *_detail"
    assert "CHAIN_SPLIT" in src, "缺 _detail 时按 [/、] 回退切分（与后端同口径）"


def test_chain_status_badge_from_pools() -> None:
    """链位卡状态徽标：观察池先入、活跃覆盖（旧 cpStatus 同口径）。"""
    src = _src()
    m = re.search(r"function chainStatusMap\(d\) \{([\s\S]*?)\n\}", src)
    assert m, "找不到 chainStatusMap"
    body = m.group(1)
    assert body.index("d.watch") < body.index("d.active"), "必须先遍历 watch 再用 active 覆盖"
    assert "stage_short" in body


def test_watch_card_bull_bear() -> None:
    src = _src()
    assert "'bull'" in src and "'bear'" in src
    assert "偏多" in src and "偏空" in src
    assert "待验证" in src and "AMV 权重变化" in src
    assert "toFixed(5)" in src, "AMV 权重变化保留 5 位（旧版同口径）"


def test_spectrum_segments() -> None:
    """阶段光谱 / 多空光谱：按计数 flex 分条（旧 _cpHtml 同口径）。"""
    src = _src()
    assert "cp-spec-track" in src
    assert "'主升/催化中'" in src and "'分歧/兑现'" in src and "'降温/蓄势'" in src
    assert "segHtml(" in src
    assert "-solid" in src, "段色走 --{color}-solid（与旧端一致）"


def test_archived_card_shows_note() -> None:
    src = _src()
    assert "已归档" in src
    assert "note" in src


# ---------------------------------------------------------------- 详情渲染


def test_detail_html_is_trusted_server_render() -> None:
    """概况 HTML 由服务端 MD 渲染，前端直接落地（不二次解析、不转义）。"""
    src = _src()
    assert "detailHtml" in src
    assert "'<div class=\"cp-md\">' + body" in src or '"<div class="cp-md">" + body' in src
    assert "md-table-wrap" in src, "概况表格样式（.cp-md .md-table-wrap）需定义"


def test_detail_error_is_shown_not_blank() -> None:
    src = _src()
    assert "概况加载中…" in src
    assert "st === 404" in src, "404 需区分「该概念无概况」"
    assert "'加载失败'" in src, "非 404 需给「加载失败」态"
    assert "概况' + (st === 404" in src, "两种失败态都在详情正文里落地（不白屏）"


def test_detail_uses_k_name_style() -> None:
    """服务端 HTML 里的 .k-name 只有视觉态（cursor/hover），行为留给桥接。"""
    src = _src()
    assert ".cp-md .k-name" in src
    assert "cursor:pointer" in src


# ---------------------------------------------------------------- 刷新纪律


def test_no_refresh_every_declared() -> None:
    src = _src()
    assert not re.search(r"^\s*refreshEvery\s*:", src, re.M), "本卡不应声明 refreshEvery"
    assert "async refresh()" in src, "契约要求保留 refresh() 占位"


def test_refresh_is_guarded() -> None:
    src = _src()
    m = re.search(r"async refresh\(\)\s*\{([\s\S]*?)\n    \},", src)
    assert m, "找不到 refresh() 实现"
    body = m.group(1)
    assert "S.alive" in body and "S.loading" in body


def test_no_flicker_set_html_cache() -> None:
    src = _src()
    assert re.search(r"if \(S\.html\[key\] === html\) return;", src)


def test_unmount_releases_state() -> None:
    src = _src()
    m = re.search(r"async unmount\(\)\s*\{([\s\S]*?)\n    \},", src)
    assert m, "找不到 unmount() 实现"
    body = m.group(1)
    assert "S.alive = false" in body
    assert "this._cp = null" in body


def test_event_delegation_not_inline_onclick() -> None:
    src = _src()
    assert "wrap.addEventListener('click'" in src
    assert "onclick=" not in src, "不得使用内联 onclick"


# ---------------------------------------------------------------- 卡片声明 / manifest


def test_manifest_version_bumped() -> None:
    data = _manifest()
    assert data["version"] == "0.2.0"
    assert data["frontend"]["module"] == "frontend.js"
    cards = data["frontend"]["cards"]
    assert [c["id"] for c in cards] == ["concept.main"]
    assert cards[0]["slot"] == "main"
    assert cards[0]["title"] == "概念追踪"


def test_manifest_backend_unchanged() -> None:
    """零后端改动：mount/entry/权限/数据白名单与 0.1.0 逐字一致。"""
    data = _manifest()
    assert data["backend"]["mount"] == "root", "root 挂载才能保住 /api/concept.json 旧路径"
    assert data["backend"]["entry"] == "register"
    assert data["data"]["text"] == ["concept_tracking"]
    assert data["data"]["json"] == ["stock_names"]
    assert data["data"]["read"] == ["ths_board.db"]
    assert data["data"]["write"] == []


def test_frontend_card_matches_manifest() -> None:
    src = _src()
    assert "export default" in src
    assert re.search(r"id:\s*'concept\.main'", src)
    assert "mount(" in src and "unmount(" in src and "refresh(" in src
