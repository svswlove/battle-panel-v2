# -*- coding: utf-8 -*-
"""ops 后台卡片前端静态检查（只读，不跑浏览器）。

守的是任务书 `docs/../../任务书/20261025_前端阶段_逐卡验收与ops卡化_01.md` C 段与
`docs/CONTRACT.md` §5：
  · 卡片 id `ops.main` / `order 70` / manifest 声明齐全
  · 无裸 hex（颜色一律 themes.css token）、零 import、不裸 fetch、不写全局
  · 只消费 ops 插件 mount=root 的既有端点（/api/ops.json、/api/ops/run），不新造路由
  · 红线任务只显示状态、不出按钮；「手动更新」只对后端 manual 白名单出
  · 三张表 + 汇总三项 + 两类红卡 + 口径脚注都在（对齐旧页 renderOps）
  · 不声明 refreshEvery（后台状态低频，按需刷新）
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


def _src() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- manifest / 卡片声明


def test_manifest_declares_ops_main_card() -> None:
    fe = _manifest().get("frontend") or {}
    assert fe.get("module") == "frontend.js"
    cards = fe.get("cards") or []
    assert len(cards) == 1, "ops 只应有一张卡：%s" % [c.get("id") for c in cards]
    card = cards[0]
    assert card["id"] == "ops.main"
    assert card["title"] == "后台"
    assert card["slot"] == "main"
    assert card["order"] == 70, "order 必须是 70（任务书 C 段）"


def test_frontend_card_id_matches_manifest() -> None:
    assert re.search(r"id:\s*'ops\.main'", _src()), "frontend.js 卡片 id 与 manifest 不一致"


def test_order_in_frontend_matches_manifest() -> None:
    assert re.search(r"order:\s*70\b", _src()), "frontend.js order 与 manifest 不一致"


# ---------------------------------------------------------------- 主题 / 颜色


def test_no_raw_hex_colors() -> None:
    """颜色一律走 themes.css token，禁裸 hex。"""
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_no_raw_rgba() -> None:
    """也不得自己拼 rgba（半透明底一律 --*-bg token）。"""
    found = re.findall(r"rgba?\([^)]*\)", _src())
    assert not found, "frontend.js 出现裸 rgba 色值：%s" % found[:8]


def test_uses_semantic_tokens() -> None:
    """成败/涨跌语义走 --up/--down/--warn/--danger 家族，骨架走 card/line/dim。"""
    src = _src()
    for token in ("var(--down-bg)", "var(--down-ink)", "var(--up-bg)", "var(--up-ink)",
                  "var(--warn-bg)", "var(--warn-ink)", "var(--danger-bg)", "var(--danger-ink)",
                  "var(--card2)", "var(--line)", "var(--dim)", "var(--font-mono)"):
        assert token in src, "缺少主题 token %s" % token


def test_style_injected_once() -> None:
    src = _src()
    assert "ops-card-css" in src
    assert "document.getElementById(STYLE_ID)" in src, "样式注入必须做 id 判重"


# ---------------------------------------------------------------- 数据源 / 依赖


def test_consumes_only_ops_endpoints() -> None:
    """只打 ops 插件自己的两个端点（mount=root，故用绝对 /api/ 前缀）。"""
    src = _src()
    assert "api.get('/api/ops.json'" in src or 'api.get("/api/ops.json"' in src
    assert "api.post('/api/ops/run'" in src or 'api.post("/api/ops/run"' in src
    # 不得出现别家插件的端点前缀（注释里提到 /api/p/ops 不算：只看真实调用）
    calls = re.findall(r"api\.(?:get|post)\(\s*['\"]([^'\"]+)", src)
    assert calls, "没找到 ctx.api 调用"
    for path in calls:
        assert not path.startswith("/api/p/"), "不得直连插件的 /api/p/<id> 路由：%s" % path
        assert path.startswith("/api/ops"), "ops 卡只应消费 ops 端点，实得：%s" % path


def test_no_bare_fetch() -> None:
    """统一走 ctx.api（shell 的 makeCtx），不裸 fetch。"""
    assert not re.search(r"(?<![\w.$])fetch\s*\(", _src()), "出现裸 fetch"


def test_no_write_or_subprocess_tokens() -> None:
    src = _src()
    hits = [t for t in BANNED if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src), "frontend.js 出现 open(...,'w') 写模式"


def test_zero_imports() -> None:
    assert not re.search(r"^\s*import\s", _src(), re.M), "frontend.js 不应有任何 import"


def test_no_global_writes() -> None:
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _src())


def test_no_cross_plugin_dependency() -> None:
    """CONTRACT §5：卡片不得依赖其它插件（旧页全局一律不碰）。"""
    src = _src()
    for bad in ("KLineApp", "AmvCard", "watchBadges", "kNameRaw", "kNameClick",
                "/api/p/kline", "/api/p/watchlist"):
        assert bad not in src, "出现跨插件依赖：%s" % bad


# ---------------------------------------------------------------- 行为对齐（旧页 renderOps）


def test_three_tables_present() -> None:
    src = _src()
    for title in ("计划任务·现役（跑没跑 / 成功没 / 最后运行）",
                  "常驻进程·现役（活 / 停 / 窗口外）",
                  "数据新鲜度（最新数据日 vs 最近交易日）"):
        assert title in src, "缺表：%s" % title


def test_retired_fold_present() -> None:
    """2026-11-09 重做：已停用/已退役项只进折叠区，不探活、不计入告警。"""
    src = _src()
    assert "已停用 / 已退役（" in src, "缺退役折叠区标题"
    assert "ops-retired" in src, "缺退役折叠区样式/容器"
    assert "只留痕，不探活、不计入告警" in src, "折叠区必须明说它不参与健康判断"


def test_active_retired_split() -> None:
    """现役/退役分组必须按后端 active 字段，不得前端再用状态关键字猜。"""
    src = _src()
    assert "x.active !== false" in src, "现役分组必须读 active 字段"
    assert "x.active === false" in src, "退役分组必须读 active 字段"


def test_summary_three_items() -> None:
    src = _src()
    for key in ("tasks_total", "tasks_fail", "tasks_running", "tasks_retired",
                "daemons_total", "daemons_down", "daemons_retired",
                "fresh_total", "fresh_stale"):
        assert key in src, "汇总缺字段：%s" % key


def test_two_red_cards_present() -> None:
    src = _src()
    assert "登记册解析失败" in src, "缺「登记册解析失败」红卡"
    assert "覆盖度告警" in src, "缺「覆盖度告警」红卡"
    for key in ("tasks_unknown", "daemons_unknown", "daemons_unregistered"):
        assert key in src, "覆盖度告警缺字段：%s" % key


def test_status_class_maps_cover_backend_values() -> None:
    """状态→胶囊配色必须覆盖后端实际返回值（否则状态会退成灰色底）。"""
    src = _src()
    for value in ("'成功'", "'失败'", "'未跑'", "'运行中'", "'停用'", "'已退役'", "'未知'",
                  "'活'", "'停'", "'窗口外'",
                  "'新鲜'", "'陈旧'", "'待更新'"):
        assert value in src, "状态映射缺值：%s" % value


def test_redline_never_clickable() -> None:
    """红线任务只显示状态：红线胶囊存在，且按钮只由 manual 白名单产生。"""
    src = _src()
    assert "严禁手动触发" in src, "缺红线提示"
    assert "ops-red" in src
    # 按钮的生成必须落在 manual 白名单分支里
    m = re.search(r"canRun\s*=\s*Object\.prototype\.hasOwnProperty\.call\(manual,\s*x\.name\)", src)
    assert m, "手动更新按钮必须由后端 manual 白名单控制（红线任务不得出按钮）"


def test_manual_run_confirms_before_post() -> None:
    """写操作前必须有确认弹层（受控写纪律）。"""
    src = _src()
    assert "confirm(" in src, "手动更新缺确认弹层"
    idx_confirm = src.index("confirm(")
    idx_post = src.index("api.post('/api/ops/run'") if "api.post('/api/ops/run'" in src else src.index('api.post("/api/ops/run"')
    assert idx_confirm < idx_post, "确认必须在提交之前"


def test_no_refresh_every_declared() -> None:
    """后台状态低频：不声明 refreshEvery，用卡头刷新按需拉。"""
    assert not re.search(r"^\s*refreshEvery\s*:", _src(), re.M)


def test_unmount_clears_state() -> None:
    src = _src()
    assert re.search(r"async unmount\(\)", src), "缺 unmount"
    assert "_dead = true" in src, "unmount 必须置失效标志，防异步回调写已卸载的 DOM"


def test_frontend_js_parses() -> None:
    """模块必须能被 JS 引擎解析。

    CSS 就写在模板字符串里，在里面写反引号（例如注释里引用 `.stage{...}`）会直接
    提前把模板断开 —— 静态断言全过、浏览器里卡片却报 SyntaxError。
    这里用 node --check 做一次真解析，不再靠正则间接推断。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过语法解析核验")
    r = subprocess.run([node, "--check", str(FRONTEND_JS)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "frontend.js 语法错误：%s" % (r.stderr or r.stdout)[:800]


def test_click_delegate_bound_once() -> None:
    """手动更新按钮走事件委托，且只在 mount 里绑一次（渲染不重建监听，防重复触发）。"""
    src = _src()
    assert src.count("addEventListener('click'") == 2, (
        "click 监听应只有 2 处（刷新按钮 + body 委托）；实得 %d" % src.count("addEventListener('click'")
    )


# ---------------------------------------------------------------- 滚动归属（标注 N20261027-012343-28）
#
# 主人要求：卡片里不许再出现「内部单独的滑块」。滚动的唯一归属方是 V2 外壳的
# `app/web/shell.css::.stage{overflow:auto}`——卡片自身只负责把内容完整排出来。
#
# 因此本段守三件事：
#   1. `.ops-wrap` 不得是滚动容器（无 overflow:auto/scroll、无 max-height）；
#   2. 表头 sticky 随滚动容器一起去掉（没有内部滚动，sticky 无意义且会叠 z-index）；
#   3. 长表靠 `table-layout:fixed` + `overflow-wrap:anywhere` 在桌面宽度内收住，
#      不靠 `display:block;overflow-x:auto` 那种「换一种内部滑块」的写法。

def _rule(selector: str) -> str:
    """取 CSS 常量里某条选择器的声明块（只取第一条完全匹配的规则）。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", _src())
    assert m, "CSS 里找不到选择器：%s" % selector
    return m.group(1)


def test_ops_wrap_is_not_a_scroll_container() -> None:
    """`.ops-wrap` 只做描边，不得自带 overflow 滚动与 max-height（否则就是内部滑块）。"""
    decl = _rule(".ops-wrap")
    assert not re.search(r"overflow(?:-[xy])?\s*:\s*(?:auto|scroll)", decl), (
        ".ops-wrap 不得声明 overflow:auto/scroll，实得：%s" % decl
    )
    assert "max-height" not in decl, ".ops-wrap 不得声明 max-height（会截断表格并造滚动），实得：%s" % decl
    assert "height" not in decl.replace("max-height", ""), ".ops-wrap 不应限定高度，实得：%s" % decl


def test_table_has_no_inner_horizontal_scroller() -> None:
    """明确否掉 `display:block;overflow-x:auto` 这条替代写法：它只是把纵向滑块换成横向滑块。"""
    src = _src()
    assert not re.search(r"overflow-x\s*:\s*(?:auto|scroll)", src), (
        "不得用 overflow-x:auto 给表格造内部横向滑块"
    )
    table = _rule(".ops-table")
    assert "display:block" not in table, ".ops-table 不得改成 display:block（会脱离表格布局并引出自滚动）"


def test_table_fixed_layout_with_anywhere_wrap() -> None:
    """桌面宽度内靠固定布局 + 任意断行收住长文本，这是「无内部 overflow」的实际手段。"""
    table = _rule(".ops-table")
    assert re.search(r"table-layout\s*:\s*fixed", table), ".ops-table 必须 table-layout:fixed，实得：%s" % table
    cell = _rule(".ops-table th,.ops-table td")
    assert re.search(r"overflow-wrap\s*:\s*anywhere", cell), (
        "单元格必须 overflow-wrap:anywhere（长串可断行，否则固定布局下仍会溢出），实得：%s" % cell
    )


def test_header_not_sticky() -> None:
    """没有内部滚动容器后，sticky 表头失去意义（且会带 z-index 叠层），一并去掉。"""
    src = _src()
    assert "position:sticky" not in src, "卡片内不得再有 position:sticky（sticky 表头随内部滚动一起去掉）"
    assert "position: sticky" not in src
    th = _rule(".ops-table th")
    assert "sticky" not in th, ".ops-table th 不得 sticky，实得：%s" % th


def test_narrow_screen_still_hides_column() -> None:
    """小屏照旧靠隐藏列保持可读，不新增横向滚动兜底。"""
    src = _src()
    assert re.search(r"@media\(max-width:760px\)", src), "缺窄屏分支"
    assert "nth-child(4)" in src, "窄屏应继续隐藏第 4 列（结果码/最新数据日）"
