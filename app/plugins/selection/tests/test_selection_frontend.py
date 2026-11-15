# -*- coding: utf-8 -*-
"""selection 卡片前端静态检查（只读，不跑浏览器）。

守的是 docs/selection_contract.md §3 与 docs/CONTRACT.md §5 的「卡片交互契约」：
  · 零 import / 零裸 fetch / 零裸 hex / 不写全局 / 不写盘 / 不引子进程
  · 读走 ctx.api.get、写一律 ctx.api.post；四个端点都是根路径 /api/selection*（compat_misc mount=root）
  · 8.2MB 大载荷纪律：固定行高 32px + 滚动窗口，DOM 只留可视区 ± 缓冲；窗口不变不重建 DOM
  · 写纪律：偏好 300ms 防抖 + 回读核对；池增删行内二次点击（data-arm）；失败回执原样
  · 不声明 refreshEvery；unmount 释放定时器/监听
"""
from __future__ import annotations

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

# 任务书 §1 点名的四个读端点（全部由 compat_misc 以 root 挂载提供）
# 2026-10-29 加 `/api/p/score/map`：唯一个跨插件**只读**依赖（score 插件的打分投影）。
READ_ENDPOINTS = [
    "/api/selection",
    "/api/selection_live",       # 2026-11-04 盘中刷新：轮询产物 generated_at（只读）
    "/api/selection_prefs",
    "/api/selection_pool",
    "/api/selection_pool?date=",
    "/api/selection_board_options",
    "/api/selection_etf",        # 2026-11-11 ETF 模式：etf_snapshot 只读端点（compat_misc）
    "/api/selection_quotes?codes=",  # 2026-11-12 标注 N89：盘中「涨幅」实时覆盖（eltdx 快照）
    "/api/p/score/map",
]
WRITE_ENDPOINTS = [
    "/api/selection_prefs",
    # 2026-11-13：选股池 = 自选池的 MMDD 组 → 写口改走 compat_watchlist
    #   （watchlist.json 的唯一写入者；本插件不写，避免双写锁）
    "/api/watchlist/add",
    "/api/watchlist/remove",
    # 2026-11-04 盘中刷新：触发全市场重算（core 侧的脚本白名单，插件不碰 subprocess）。
    # 本插件只发一个名字，不会把自己变成执行器；服务端白名单里没有的名字一律 400。
    "/api/shell/run-script",
]
# 旧端 SEL_POOLS 五档（legacy/index.html 6130-6136 逐字）
OLD_POOLS = [
    ("mainboard", "主板", "主"), ("chinext", "创业板", "创"), ("star", "科创板", "科"),
    ("bse", "北交所", "北"), ("st", "含ST", "ST"),
]


def _src() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 主题 / 依赖纪律


def test_no_raw_hex_colors() -> None:
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_uses_theme_tokens() -> None:
    src = _src()
    for token in ("var(--up)", "var(--down)", "var(--warn)", "var(--danger)", "var(--dim)",
                  "var(--accent)", "var(--card2)", "var(--fg-strong)", "var(--radius-pill)",
                  "var(--border-color)"):
        assert token in src, "缺少主题 token %s" % token


def test_style_injected_once() -> None:
    src = _src()
    assert "selection-card-css" in src
    assert "document.getElementById(STYLE_ID)" in src, "样式注入必须做 id 判重"
    assert not re.search(r"\.card\s*\{", src), "不得改写 shell 的 .card 规则"


def test_no_write_or_subprocess_tokens() -> None:
    src = _src()
    hits = [t for t in BANNED if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src), "frontend.js 出现 open(...,'w') 写模式"


def test_zero_imports() -> None:
    assert not re.search(r"^\s*import\s", _src(), re.M), "frontend.js 不应有任何 import"


def test_no_global_writes() -> None:
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _src())
    assert "window._selCache" not in _src(), "不得复用旧页全局缓存（跨卡共享状态）"


def test_no_bare_fetch() -> None:
    assert "fetch(" not in _src(), "应统一走 ctx.api.get/post，不直接 fetch"


def test_no_cross_plugin_dependency() -> None:
    """CONTRACT §5：卡片不得依赖其它插件（旧页的 KLineApp / watchBadges 等全局一律不碰）。

    唯一例外（2026-10-29 主人定）：个股打分列可以从 score 插件只读拉 `/api/p/score/map`。
    这个依赖单向且可缺：插件删了，404 只让那一列变「—」/「无打分数据」。
    """
    src = _src()
    for bad in ("KLineApp", "watchBadges", "watchRegimeBadge", "kNameRaw", "kNameClick",
                "/api/p/kline", "/api/p/watchlist"):
        assert bad not in src, "出现跨插件依赖：%s" % bad
    others = [p for p in re.findall(r"/api/p/[\w.\-]+/?[\w.\-]*", src) if p != "/api/p/score/map"]
    assert not others, "除打分投影外不得依赖其它插件：%s" % others


# ---------------------------------------------------------------- 端点


def test_read_endpoints_are_absolute_root_api() -> None:
    calls = re.findall(r"""api\.get\(\s*['"]([^'"]+)['"]""", _src())
    assert calls, "找不到任何 api.get 调用"
    for path in calls:
        assert path.startswith("/api/"), "root 挂载端点必须写绝对 /api/：%s" % path
        assert not path.startswith("http"), "不得直连旧端：%s" % path
        assert "/api/p/selection" not in path, "选股端点在根路径，没有 /api/p/selection 前缀：%s" % path
    assert sorted(set(calls)) == sorted(READ_ENDPOINTS), sorted(set(calls))


def test_write_endpoints_are_the_three_declared() -> None:
    calls = re.findall(r"""api\.post\(\s*['"]([^'"]+)['"]""", _src())
    assert calls, "找不到任何 api.post 调用"
    for path in calls:
        assert path.startswith("/api/"), "写端点必须用绝对 /api/ 路径：%s" % path
    assert sorted(set(calls)) == sorted(WRITE_ENDPOINTS), sorted(set(calls))


def test_pool_date_param_is_encoded() -> None:
    """切历史快照走 ?date=，且必须 encodeURIComponent（日期来自服务端快照目录）。"""
    assert "/api/selection_pool?date=' + encodeURIComponent(" in _src()


# ---------------------------------------------------------------- 大载荷渲染纪律


def test_virtual_scroll_constants() -> None:
    src = _src()
    assert re.search(r"const ROW_H = 32;", src), "固定行高常量缺失"
    assert re.search(r"const BUFFER = 6;", src), "可视区缓冲常量缺失"


def test_rows_are_absolutely_positioned_by_index() -> None:
    """虚拟滚动的判据：行按 i*ROW_H 绝对定位，容器高度 = 行数 * ROW_H。"""
    src = _src()
    assert "i * ROW_H" in src
    assert "total * ROW_H" in src
    assert "position:absolute" in src, "行必须绝对定位（否则滚不动）"


def test_visible_window_is_sliced_not_全部渲染() -> None:
    """只渲染 [first, last) 窗口；不得对全量 rows 做 map/join 后一次性 innerHTML。"""
    src = _src()
    m = re.search(r"const paintRows = \(\) => \{([\s\S]*?)\n      \};", src)
    assert m, "找不到 paintRows"
    body = m.group(1)
    assert "Math.floor(vpEl.scrollTop / ROW_H)" in body
    assert "for (let i = first; i < last; i++)" in body
    assert "S.view.map" not in body, "不得对全量视图 map（会渲染 2824 行）"
    # 窗口未变则不重建 DOM
    assert "if (S.html.range === range) return;" in body


def test_scroll_handler_is_throttled() -> None:
    src = _src()
    assert "requestAnimationFrame" in src, "滚动重绘要过 rAF，不能每个 scroll 事件都重排"
    assert "addEventListener('scroll', onScroll, { passive: true })" in src


def test_sparkline_only_for_visible_rows() -> None:
    """走势 SVG 只在行渲染函数内生成（即只为可见行），不得预计算全量。

    2026-11-11：个股行实现改名 rowHtmlStock，rowHtml 成为两模式分派入口（ETF 无走势列）。
    """
    src = _src()
    m = re.search(r"const rowHtmlStock = \(r, top\) => \{([\s\S]*?)\n      \};", src)
    assert m, "找不到 rowHtmlStock"
    assert "sparkSvg(r)" in m.group(1)
    # 分派入口不得自己再算一遍
    disp = re.search(r"const rowHtml = \(r, top\) => \{([\s\S]*?)\n      \};", src)
    assert disp, "找不到 rowHtml 分派入口"
    assert "rowHtmlStock(r, top)" in disp.group(1)
    assert "sparkSvg(" not in disp.group(1)
    assert src.count("sparkSvg(") == 2, "sparkSvg 只应有定义 + 行内调用各一处"


def test_paint_time_recorded_for_perf() -> None:
    """首屏耗时写到自己的 root 上（供浏览器实测取值，不写全局变量）。"""
    src = _src()
    assert "root.setAttribute('data-paint-ms'" in src
    assert "root.setAttribute('data-rows'" in src


def test_sort_comparator_handles_nulls() -> None:
    """旧 6922-6936：缺值沉底，且比较器必须自反（两个都缺返回 0）。"""
    src = _src()
    assert "if (na && nb) return 0;" in src
    assert "if (na) return 1;" in src
    assert "if (nb) return -1;" in src


# ---------------------------------------------------------------- 筛选口径（对旧 selPassFilter）


def test_board_prefix_rules_match_old() -> None:
    src = _src()
    m = re.search(r"function boardOf\(code6\) \{([\s\S]*?)\n\}", src)
    assert m, "找不到 boardOf"
    body = m.group(1)
    assert r"/^(60\d{4}|00\d{4})$/" in body
    assert r"/^30\d{4}$/" in body
    assert r"/^688\d{3}$/" in body
    assert r"/^92\d{4}$/" in body


def test_st_rule_matches_old() -> None:
    assert r"/^\*?ST/i" in _src()


def test_pools_match_old() -> None:
    m = re.search(r"const POOLS = \[([\s\S]*?)\n\];", _src())
    assert m, "找不到 POOLS"
    got = re.findall(r"key: '([a-z]+)', label: '([^']+)', short: '([^']+)'", m.group(1))
    assert got == OLD_POOLS, got


def test_kdj_ma_filter_all_groups_split_switches() -> None:
    """2026-11-02（标注 67/68）：KDJ 与均线拆成两个开关，且对全部策略组生效。

    历史：旧 6219-6237 把 KDJ与均线 无条件套到别的组（bug）； 2026-10-30 砖形图单开；
    2026-11-01 扩到五组白名单（B1/B2/B3/单针/动能）。主人 2026-11-02 要求：
    ① 所有策略都能**分别**开关 KDJ、均线（不再有白名单）；
    ② 白线≥黄线、收盘≥黄线 的默认系数统一 0.995。
    """
    src = _src()
    assert "if (m.kdjOn && (r.j == null || !(Number(r.j) < m.jMax))) return false;" in src
    assert "if (m.maOn) {" in src
    assert "if (w == null || y == null || !(Number(w) >= Number(y) * m.shortOverLong)) return false;" in src
    assert "if (c == null || y == null || !(Number(c) >= Number(y) * m.closeOverLong)) return false;" in src
    assert "MA_GROUPS.includes(g) && maOf(g).on" not in src, "白名单已废，所有组同构"
    assert "const MA_DEFAULTS = { kdjOn: false, maOn: false, jMax: 13, shortOverLong: 0.995, closeOverLong: 0.995 };" in src
    assert "if (g === '单针' && P.pin.on) {" in src


def test_kdj_ma_switches_render_for_every_group() -> None:
    """两个 chip（data-chip=kdj / ma）不依赖后端 group_filters，每组都出；面板各自独立。"""
    src = _src()
    assert 'data-chip="kdj"' in src and 'data-chip="ma"' in src
    assert "data-kdj-on" in src and "data-ma-on" in src
    assert "const writeMa = (patch) => {" in src
    assert "if (kind === 'kdj') {" in src


def test_board_and_hot_are_union_not_intersection() -> None:
    """旧 6240-6257：板块与热门板块取并集；板块清单为空 = 不筛。"""
    src = _src()
    assert "const boardOn = P.boardOn && selBoards.length > 0;" in src
    assert "if (!hit && hotOn) hit = hotSet().has(r.code6);" in src


def test_mcap_missing_is_filtered_out() -> None:
    """旧 6905：市值缺值被区间过滤掉（不猜）。"""
    assert "if (v == null || isNaN(v)) return false;" in _src()


def test_hot_title_matches_old() -> None:
    src = _src()
    assert "热门板块：二级行业（L2），近 " in src
    assert "满足任一即算热门 —— ① 评分 ≥ " in src


# ---------------------------------------------------------------- 结果表列集


def test_columns_match_old_nine_columns() -> None:
    """旧 6990-6996 的列集 + 2026-10-29 新增的「打分」列。

    前 11 项是旧端口径的逐字对照（不得改）；最后一项是 score 插件的只读投影列，
    插在 J 后（同步改了 .sel-grid 的轨数与 min-width）。
    """
    m = re.search(r"const COLS = \[([\s\S]*?)\n\];", _src())
    assert m, "找不到 COLS"
    labels = re.findall(r"label: '([^']*)'", m.group(1))
    assert labels == ["标的", "板块", "涨幅", "量比", "MAV涨跌", "J", "打分", "流通市值",
                      "主力净额", "走势", "距防线", ""], labels


def test_grid_tracks_match_cols() -> None:
    """列数变了必须同步改轨数，否则整表错列（虚滚动行是绝对定位的）。"""
    src = _src()
    m = re.search(r"\.sel-grid\{display:grid;grid-template-columns:([^;]+);", src)
    assert m, "找不到 .sel-grid 轨定义"
    tracks = m.group(1).split()
    mcols = re.search(r"const COLS = \[([\s\S]*?)\n\];", src)
    n_cols = len(re.findall(r"label: '[^']*'", mcols.group(1)))
    assert len(tracks) == n_cols, "轨数 %d ≠ 列数 %d" % (len(tracks), n_cols)


def test_score_column_is_readonly_projection() -> None:
    """打分列只能从 score 插件拉一次、本地查表：不得在选股插件里重算或第二套匹配。"""
    src = _src()
    assert "'/api/p/score/map'" in src, "没接 score 插件的 /map"
    assert "readScore(" in src and "attachScoreRows(" in src
    assert "state: 'none'" in src, "缺 score 插件时必须降级成 none，不得抛"
    assert "日频收盘口径，盘中不更新" in src, "列头没写数据时效"
    # 不重算：选股插件源码里不得出现六维分项字段名（那是 score 插件的事）
    for dim in ("dim_fund", "dim_trend", "dim_value", "dim_quality"):
        assert dim not in src, "选股卡出现了 %s —— 不得在本插件重算打分" % dim


def test_defense_cell_rules_match_old() -> None:
    """旧 6696-6715：全破显示负距离；否则显示线种 + 正距离；新破挂小标。"""
    src = _src()
    assert "d.status === 'broken_all'" in src
    assert "黄线新破" in src and "白线新破" in src and "N型破位" in src


def test_sparkline_three_lines_shared_scale() -> None:
    """收盘/黄线/白线共用同一归一化区间，缺值不插值；
    收盘线按**多空区间**分段染色（2026-11-03 主人：白线>黄线且收盘>黄线 = 多头/红）。"""
    src = _src()
    m = re.search(r"function sparkSvg\(r\) \{([\s\S]*?)\n\}", src)
    assert m
    body = m.group(1)
    assert "const lo = Math.min.apply(null, all), hi = Math.max.apply(null, all);" in body
    assert "poly(ws, 'spw') + poly(ys, 'spy') + segHtml" in body
    assert "(ws[i] > ys[i] && cs[i] > ys[i]) ? 'up' : 'down'" in body, "多空判据必须是白线>黄线且收盘>黄线"
    assert '"spc \' + sg.cls + \'"' in body, "收盘线必须按段着色"


def test_veto_rows_greyed() -> None:
    assert "veto_reason" in _src()
    assert re.search(r"/\^\(V1\|V2\)/", _src())


# ---------------------------------------------------------------- 写纪律


def test_prefs_save_debounced_and_readback_verified() -> None:
    """改一项 → 300ms 防抖 → POST → 回读核对（任务书 §1.2「POST 后回读一致」）。"""
    src = _src()
    assert re.search(r"const SAVE_DEBOUNCE_MS = 300;", src)
    m = re.search(r"const savePrefs = \(\) => \{([\s\S]*?)\n      \};", src)
    assert m, "找不到 savePrefs"
    body = m.group(1)
    assert "clearTimeout(S.saveTimer)" in body, "必须清旧定时器（防抖）"
    assert "api.post('/api/selection_prefs', sent)" in body
    assert "api.get('/api/selection_prefs')" in body, "写后必须回读核对"
    assert "回读一致" in body and "回读与提交不一致" in body


def test_prefs_payload_carries_all_nine_fields() -> None:
    """服务端只落显式字段；漏字段等于把已有配置覆盖成空（旧 6181-6185 全量回传）。"""
    src = _src()
    m = re.search(r"const prefsPayload = \(\) => \(\{([\s\S]*?)\}\);", src)
    assert m, "找不到 prefsPayload"
    body = m.group(1)
    for key in ("pools", "ma", "pin", "boards", "boardOn", "sigs", "inds", "cons", "hot"):
        assert key + ":" in body, "偏好提交缺字段 %s" % key


def test_pool_toggle_uses_inline_second_click() -> None:
    """任务书 §1.3 + paper 卡同款：第一次点亮「确认…」，4s 内第二次才 POST。"""
    src = _src()
    assert re.search(r"const ARM_MS = 4000;", src)
    assert "const armAction = (btnEl, idle, armText, idleCls, run) => {" in src
    assert "btnEl.getAttribute('data-arm') === '1'" in src
    assert "btnEl.setAttribute('data-arm', '1')" in src
    assert "'确认移出？' : '确认入池？'" in src
    assert "setTimeout(() => {" in src and "}, ARM_MS);" in src


def test_pool_write_uses_server_receipt_not_optimistic_update() -> None:
    """写成功后重拉池（以服务端为准），不做乐观更新。

    2026-11-13：池子改映射自选池的 MMDD 组，写口改成 compat_watchlist 的
    `/api/watchlist/add|remove`；集合仍由 loadPool() 重建，不手改。
    """
    src = _src()
    m = re.search(r"const poolToggle = async \(code, btnEl\) => \{([\s\S]*?)\n      \};", src)
    assert m, "找不到 poolToggle"
    body = m.group(1)
    assert "await api.post('/api/watchlist/remove'" in body
    assert "await api.post('/api/watchlist/add'" in body
    assert "await loadPool();" in body
    assert "S.poolSet = new Set(" not in body, "不得乐观更新（集合由 loadPool 重建）"


def test_failure_receipt_rendered_verbatim() -> None:
    src = _src()
    for prefix in ("'保存筛选偏好失败：'", "'移出选股池失败：'", "'加入选股池失败：'"):
        assert prefix in src, "缺少失败回执渲染：%s" % prefix
    assert "errText(e)" in src
    assert "setStatus('移出选股池失败：' + errText(e), 'err');" in src
    assert "setStatus('加入选股池失败：' + errText(e), 'err');" in src
    assert not re.search(r"setTimeout\([^)]*api\.post", src), "不得用定时器重试写请求"


def test_submitting_guard_prevents_double_post() -> None:
    src = _src()
    m = re.search(r"const poolToggle = async \(code, btnEl\) => \{([\s\S]*?)\n      \};", src)
    assert m
    body = m.group(1)
    assert "if (S.submitting) return;" in body
    assert "S.submitting = true;" in body
    assert "if (btnEl) btnEl.disabled = true;" in body


def test_pool_snapshot_switch_is_read_only() -> None:
    """历史快照只允许看，不允许移除（旧 6470：只有当日快照才给 ×）。"""
    src = _src()
    assert "const cur = pool.is_current !== false;" in src
    assert "(cur ? '<button type=\"button\" class=\"sel-star on\" data-pool=" in src


# ---------------------------------------------------------------- 刷新 / 生命周期


def test_no_refresh_every_declared() -> None:
    """T-1 盘后快照：不声明 refreshEvery（避免无意义重拉 8.2MB）。"""
    assert not re.search(r"^\s*refreshEvery\s*:", _src(), re.M)
    assert "async refresh()" in _src()


def test_refresh_guarded() -> None:
    m = re.search(r"async refresh\(\) \{([\s\S]*?)\n    \},", _src())
    assert m, "找不到 refresh()"
    body = m.group(1)
    for token in ("S.alive", "S.loading", "S.saving", "S.submitting"):
        assert token in body, "refresh 缺少守卫 %s" % token


def test_event_delegation_not_inline_onclick() -> None:
    src = _src()
    assert "wrap.addEventListener('click'" in src
    assert "onclick=" not in src, "不得使用内联 onclick（CSP/注入面）"
    assert "innerHTML = html" in src, "表格走 setHtml 防闪（内容不变不碰 DOM）"


def test_unmount_releases_state() -> None:
    m = re.search(r"async unmount\(\) \{([\s\S]*?)\n    \},", _src())
    assert m, "找不到 unmount()"
    body = m.group(1)
    for token in ("S.alive = false", "clearTimeout(S.toastTimer)", "clearTimeout(S.armTimer)",
                  "clearTimeout(S.saveTimer)", "removeEventListener('resize'", "this._sel = null"):
        assert token in body, "unmount 缺少释放：%s" % token


def test_load_guarded_against_reentry() -> None:
    m = re.search(r"const load = async \(\) => \{([\s\S]*?)\n      \};", _src())
    assert m, "找不到 load"
    body = m.group(1)
    assert "if (!S.alive || S.loading) return;" in body
    assert body.count("api.get(") == 5, "load() 应拉 5 个读端点（4 个自有 + score 打分投影）"
    assert "api.post" not in body, "刷新路径里不得出现写请求"


# ---------------------------------------------------------------- 卡片声明 / manifest


def test_manifest_card_matches_frontend() -> None:
    data = _manifest()
    assert data["frontend"]["module"] == "frontend.js"
    cards = data["frontend"]["cards"]
    assert [c["id"] for c in cards] == ["selection.main"]
    src = _src()
    assert re.search(r"id: 'selection\.main'", src)
    assert re.search(r"order: 55", src)
    assert "mount(" in src and "unmount(" in src and "refresh(" in src


def test_manifest_has_no_write_permission() -> None:
    data = _manifest()
    assert data["data"]["write"] == []
    assert data["backend"]["mount"] == "root"
