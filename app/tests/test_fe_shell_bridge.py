# -*- coding: utf-8 -*-
"""壳与卡间桥的静态检查（2026-10-25 前端冲刺）。

守的是本轮修掉的三处「看着能点却点不动」以及主题跟随纪律：
  1. `ctx.card.args` 一次性入参：搜索选中个股 → K 线卡按该代码打开；
  2. `panel:open-card` 有真监听（原来 shell 从未监听，卡片跳转静默失败）；
  3. 卡片里可点个股/板块必须写 `data-open-kline` / `data-open-amv` 占位（跨 iframe 桥靠它）；
  4. 卡片前端不得再写死颜色（唯一源 `/legacy/themes.css`）。
只读源码，不跑浏览器。
"""
import json
import re
from pathlib import Path

PANEL = Path(__file__).resolve().parents[2]
WEB = PANEL / "app" / "web"
PLUGINS = PANEL / "app" / "plugins"

FRONTENDS = sorted(PLUGINS.glob("*/frontend.js"))


def frontend_files() -> list:
    """卡片前端全集：`frontend.js` + 它的子模块 `frontend/*.js`。

    为什么必须包含子模块：2026-10-25 板块卡拆成了 `frontend/compute|board|views|detail.js`，
    只扫 `*/frontend.js` 会让这四份文件完全绕过主题/占位/颜色三类静态检查。
    """
    files = list(FRONTENDS)
    for p in PLUGINS.glob("*/frontend/*.js"):
        files.append(p)
    return sorted(files)


FRONTEND_FILES = frontend_files()
# 主题唯一色表：8810 零拷贝挂载 engine/web（/legacy/themes.css 与根 /themes.css 同源）。
# 2026-11-04 前曾读 app/web/legacy 的副本，那份副本无路由指向、已删。
THEMES_CSS = PANEL.parents[1] / "engine" / "web" / "themes.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _shell() -> str:
    return _read(WEB / "shell.js")


# ---------------------------------------------------------------- 卡间入参 / 跳转


def test_select_card_accepts_args() -> None:
    src = _shell()
    assert re.search(r"async function selectCard\(key,\s*args\)", src), "selectCard 必须接受一次性入参"
    assert "pendingArgs" in src, "缺一次性入参载体"
    assert "args: args" in src, "ctx.card.args 未透出"


def test_pending_args_is_one_shot() -> None:
    """入参只交给本次挂载的卡：挂载后立刻清空，避免下次切卡串味。"""
    src = _shell()
    mount_at = src.index("await impl.mount(")
    clear_at = src.rindex("pendingArgs = null;")     # rindex：取清空那一处，不是声明处
    assert clear_at > mount_at, "入参必须在 mount 之后清空"
    assert clear_at - mount_at < 200, "清空必须紧跟 mount（不能拖到下次切卡才清）"


def test_search_opens_kline_with_code() -> None:
    """搜索选中个股必须把 code 交给 K 线。

    2026-10-31 口径变更：搜索选股不再「切到 K 线卡」（那张简化卡已从侧栏撤掉），
    改为就地弹完整 K 线弹窗（`ctxOpenKline(code, name)`），与点股票名进 K 线一致。
    两条路径都算合格：关键是 **code 真的传下去了**，不是只弹一句提示。
    """
    src = _shell()
    by_dialog = re.search(r"ctxOpenKline\(item\.code", src)
    by_card = re.search(r"selectCard\(klineCard\.key,\s*\{\s*code:", src)
    assert by_dialog or by_card, (
        "搜索选中个股必须把 code 交给 K 线（ctxOpenKline 弹窗或 selectCard 切卡）"
    )
    assert "K线入参下批接" not in src, "占位提示应已删除"


def test_open_card_event_has_listener() -> None:
    """`panel:open-card` 必须有真监听，否则卡片跳转只在 console 留一行。"""
    src = _shell()
    assert "panel:open-card" in src, "shell 未监听 panel:open-card"
    assert "bindCardJump()" in src, "监听未安装"
    # 命中后必须 preventDefault，卡片据此判断「有人接了」。
    # 定位锁 addEventListener 那一行，不用裸字符串 index——注释里也会提到事件名，
    # 裸 index 会拿到注释、把守卫变成假红（2026-10-26 踩过）。
    anchor = "addEventListener('panel:open-card'"
    assert anchor in src, "panel:open-card 的监听注册方式变了，守卫需同步"
    idx = src.index(anchor)
    tail = src[idx:idx + 1500]
    assert "preventDefault" in tail, "命中后必须 preventDefault（卡片据此判 handled）"


def test_card_jump_resolves_card_id_and_plugin() -> None:
    src = _shell()
    start = src.index("function bindCardJump()")
    tail = src[start:start + 1400]
    assert "detail.cardId" in tail, "未按 cardId 解析"
    assert "pluginId" in tail, "未按 pluginId 兜底解析"
    assert "selectCard(meta.key" in tail, "命中后未真正切卡"


# ---------------------------------------------------------------- 跨卡跳转占位属性


def test_kline_card_uses_args_code() -> None:
    src = _read(PLUGINS / "kline" / "frontend.js")
    assert "ctx.card.args" in src or "ctx.card" in src, "K 线卡未读入参"
    assert re.search(r"ctx\.card\.args\.code", src), "K 线卡未使用 args.code"


def test_clickable_stock_placeholders() -> None:
    """写了个股名可点样的卡，必须同时带 data-open-kline（跨 iframe 桥的唯一判据）。"""
    expect = {
        "dashboard": "data-open-kline",
        "concept": "data-open-kline",
        "review": "data-open-kline",
        "selection": "data-open-kline",
        "watchlist": "data-open-kline",
    }
    for plugin, attr in expect.items():
        src = _read(PLUGINS / plugin / "frontend.js")
        assert attr in src, "%s 卡缺 %s 占位（跨 iframe 开 K 线会静默失败）" % (plugin, attr)


def test_clickable_sector_placeholders() -> None:
    for plugin in ("concept", "watchlist"):
        src = _read(PLUGINS / plugin / "frontend.js")
        assert "data-open-amv" in src, "%s 卡缺 data-open-amv 占位" % plugin


def test_watchlist_keeps_own_role_attrs() -> None:
    """补占位属性不得替掉卡内既有 data-role（卡自己的处理器还要用）。"""
    src = _read(PLUGINS / "watchlist" / "frontend.js")
    assert 'data-role="kline"' in src
    assert 'data-role="sector"' in src


def test_review_wires_table_knames() -> None:
    """复盘卡注释一直声称「个股名带 data-kname 占位」，本轮补上真实后处理。"""
    src = _read(PLUGINS / "review" / "frontend.js")
    assert "wireTableKNames" in src, "缺表格个股名后处理"
    assert "kTsPlayable" in src, "缺代码可跳判定（旧页同口径）"
    assert "data-open-kline" in src
    # 幂等 + 不嵌套
    assert "td.querySelector('.k-name')" in src


# ---------------------------------------------------------------- 主题跟随


def _theme_tokens() -> set:
    """主题 token 全集 = themes.css（唯一色表）+ shell.css 的 :root 布局常量。"""
    css = _read(THEMES_CSS) + "\n" + _read(WEB / "shell.css")
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", css))


# 旧页样式逐字搬入段的已知例外：
#   `span[style*="#c0392b"]` 这类是**属性选择器**（匹配内联样式的写法），不是色值；
#   `color:#FF6B6B` 是旧页原样搬入的强调色，被 test_scoped_css_rule_headers_match_legacy
#   锁为「与旧页逐字一致」，改 token 会让那条测试失败，所以在此显式登记。
HEX_ALLOWED = {("review", "#FF6B6B")}

# 文件级例外（2026-10-25 盘中卡照旧页搬「日内演化」）：
#   `intraday/frontend/evo.js` 里的 `EVO_PAL`(10 色) / `EVO_IDX_PAL`(6 色) 是旧页 4064-4068 的
#   折线调色板，旧页原文就标着 `theme-lint-allow: 折线多色区分`。
#   为什么不换成主题派生色：图上同时最多 10 条曲线（贡献图 TOP8）+ 6 条指数线，
#   必须靠固定色互相区分；从三套主题的 token 里派不出 16 个两两可分且稳定的颜色，
#   而“同一条线在不同主题下变色”会让对照旧页时的颜色身份也丢。
#   作用域按文件锁死（不是按目录），不会把例外泄漏给其它 frontend/*.js。
HEX_ALLOWED_BY_FILE = {
    "intraday/frontend/evo.js": {
        "#CB5A48", "#2F6FEB", "#129D6B", "#B8791A", "#8B5CF6",
        "#D6336C", "#0E8F9E", "#7A8B1F", "#4C6EF5", "#C2600F",
        "#bc94e8", "#61c8c0", "#e387ac", "#74a8ed", "#a6ce72", "#e88174",
    },
    # 2026-11-09：K 线标签调色板（8 色）——同一只票可挂多个标签，必须靠固定色互相区分；
    # 从三套主题 token 里派不出 8 个两两可分且稳定的颜色，且「同一标签在不同主题下变色」
    # 会丢掉颜色身份（与 evo.js 同理）。作用域按文件锁死。
    "kline/frontend/labels.js": {
        "#ff5b5b", "#ff9f45", "#ffc857", "#35c97f",
        "#35d6c9", "#4da3ff", "#a88bff", "#aebbcd",
    },
}
# rgba 例外（2026-11-09）：K 线「动能 K」信号柱染色 = 通达信 COLOR3D3DFF 原值，
# canvas 读不到 CSS 变量；且不能跟阳线同色（否则失去标记意义）——固定色是设计，不是硬编码遗漏。
RGBA_ALLOWED_BY_FILE = {
    "kline/frontend.js": {"rgb(255,61,61)"},
}
ATTR_SELECTOR_HEX = re.compile(r'style\*=\\?"#[0-9a-fA-F]{3,8}')
# 2026-11-09：`var(--token, #hex)` 是「token 优先、缺省兜底」，与 `test_undefined_tokens_not_used`
# 已有的口径一致（那里已明确 `var(--x, 兜底)` 合法）——兜底不是硬编码颜色。
# 扫描前先把这类兜底剥离，只留真正的裸 hex。
VAR_HEX_FALLBACK = re.compile(r"var\(\s*--[\w-]+\s*,\s*#[0-9a-fA-F]{3,8}\s*\)")

def test_no_raw_hex_in_any_card() -> None:
    """所有卡片前端禁裸 hex：颜色只能来自 themes.css token（文件级例外见 HEX_ALLOWED_BY_FILE）。"""
    bad = {}
    for path in FRONTEND_FILES:
        src = _read(path)
        rel = path.relative_to(PLUGINS).as_posix()
        allowed = HEX_ALLOWED_BY_FILE.get(rel, set())
        # 去掉属性选择器里的 hex（那是匹配字符串，不是颜色声明）
        src = ATTR_SELECTOR_HEX.sub("style*=\"COLOR", src)
        # 再剔掉 `var(--token, #hex)` 的兜底值（合法写法，见 VAR_HEX_FALLBACK 注释）
        src = VAR_HEX_FALLBACK.sub("var(--token)", src)
        found = [h for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", src)
                 if (path.parent.name, h) not in HEX_ALLOWED and h not in allowed]
        if found:
            bad[rel] = found[:6]
    assert not bad, "卡片出现裸 hex 色值：%s" % bad


def test_evo_palette_exception_is_scoped_and_used() -> None:
    """日内演化调色板例外不能是“空头支票”：登记了就必须真在用，而且只在登记文件里。

    反向守两件事：① 登记的颜色在 evo.js 里确实存在（否则例外变成永久白名单，以后谁写裸 hex 都过）；
    ② evo.js 里出现的裸 hex 必须全在登记集内（不靠 `found[:6]` 截断掩盖）。
    """
    rel = "intraday/frontend/evo.js"
    src = _read(PLUGINS / rel)
    found = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", src))
    allowed = HEX_ALLOWED_BY_FILE[rel]
    assert found, "evo.js 里没找到调色板：例外登记成了空头承诺"
    assert found <= allowed, "evo.js 出现未登记的裸 hex：%s" % sorted(found - allowed)
    # 其它文件不得沾这份例外
    for path in FRONTEND_FILES:
        if path.relative_to(PLUGINS).as_posix() == rel:
            continue
        hits = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", _read(path))) & allowed
        assert not hits, "%s 沾上了 evo 调色板例外：%s" % (path.name, sorted(hits))


def test_no_raw_rgba_in_theme_cards() -> None:
    """本轮整改过的四张卡（amv/kline/sector/ops）不再自拼 rgba。"""
    bad = {}
    for name in ("amv", "kline", "sector", "ops"):
        for path in FRONTEND_FILES:
            if path.parent.name != name:
                continue
            rel = path.relative_to(PLUGINS).as_posix()
            allowed = RGBA_ALLOWED_BY_FILE.get(rel, set())
            found = [m for m in re.findall(r"rgba?\([^)]*\)", _read(path)) if m not in allowed]
            if found:
                bad[rel] = found[:6]
    assert not bad, "卡片出现裸 rgba 色值：%s" % bad


def test_canvas_colors_come_from_tokens() -> None:
    """用 canvas 画图的卡：canvas 读不到 CSS 变量，必须经 palette()/tok() 从 token 现取。

    2026-10-25 板块卡重做后不再用 canvas（走势改 SVG，SVG style 能直解 var()），
    所以 sector 不再需要 palette：这里只对仍在用 canvas 的卡断言。
    """
    for name in ("kline",):
        src = _read(PLUGINS / name / "frontend.js")
        assert "getContext('2d')" in src or 'getContext("2d")' in src, "%s 卡已不用 canvas" % name
        assert "function palette(" in src, "%s 卡缺 palette()" % name
        # getComputedStyle 现取 token
        assert "getComputedStyle(document.documentElement).getPropertyValue" in src


def test_undefined_tokens_not_used() -> None:
    """不得引用 themes.css / shell.css 里不存在的 token（`--muted` / `--bg2` / `--panel` 这类历史误写）。

    2026-10-25 改：不再用白名单遮盖，改为**自动识别文件内自己定义的变量**（`--x: ...` 声明）。
    原先给板块卡加了一串白名单，这等于对 `legacy.css.js` 整个文件失守——别名层里写错一个
    映射目标（比如 `--radius: var(--radius-sm)` 而主题表没有 `--radius-sm`）就拓不出来。
    现在：文件内声明过的算局部变量；引用了但**既不在主题表、也没在本文件声明**的，照样报错。

    2026-10-30 改：`var(--x, 兜底值)` 自带 fallback，是合法且安全的写法（如
    `var(--mono, var(--font-mono))`），不再判为引用未定义 token；只校 `var(--x)` 这种无兜底的引用。
    """
    tokens = _theme_tokens()
    # 卡内自定义（非主题）变量白名单：由 JS 运行时注入、无法从源码静态声明的
    local_ok = {"--pq", "--judge-bar", "--judge-chip", "--judge-chip-ink",
                # 2026-11-09：K 线标签/覆盖层每个元素的颜色由 JS 运行时写进 style（--c），
                # 静态源码里只能看到引用——与 --pq 同类。
                "--c",
                "--rv-bg", "--rv-panel", "--rv-panel2", "--rv-line", "--rv-ink", "--rv-ink2",
                "--rv-ink3", "--rv-up", "--rv-down", "--rv-amber", "--rv-blue", "--rv-purple",
                "--rv-cyan", "--rv-mono", "--rv-pad-x", "--rv-read-w"}
    bad = {}
    for path in FRONTEND_FILES:
        src = _read(path)
        # 只校「无兜底」的引用：var(--x) 必须能解析；var(--x, 兜底) 有 fallback，合规
        used = set(t for t, fallback in re.findall(r"var\((--[a-z0-9-]+)(\s*,)?", src) if not fallback)
        # 本文件内声明过的变量（`--x:` 形式，包括 JS 字符串里的 CSS 规则）
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", src))
        missing = sorted(t for t in used
                         if t not in tokens and t not in local_ok and t not in declared)
        if missing:
            bad[str(path.relative_to(PLUGINS))] = missing
    assert not bad, "卡片引用了主题表未定义的 token：%s" % bad


def test_shell_css_tokens_exist() -> None:
    """shell.css 的 var() 除自身布局常量外，必须在 themes.css 里有定义。"""
    tokens = set(re.findall(r"(--[a-z0-9-]+)\s*:", _read(THEMES_CSS)))
    local = {"--rail", "--z-rail", "--z-scrim", "--z-drawer", "--z-search", "--z-toast",
             "--z-note", "--z-note-pop", "--z-note-fab"}
    used = set(re.findall(r"var\((--[a-z0-9-]+)", _read(WEB / "shell.css")))
    missing = sorted(t for t in used if t not in tokens and t not in local)
    assert not missing, "shell.css 引用未定义 token：%s" % missing


# ---------------------------------------------------------------- 首页收口（2026-10-26）

# 改前这一节测的是 `v2_map.json`：九个 Tab 全映射到 v2 卡，由 `v2bridge.js` 在旧页里
# 逐个换成 iframe 嵌的 v2 卡。2026-10-26 旧端口下线后主人拍板收口：
#   `/` 直接开 v2 壳（`app/web/shell.html`），旧页整体移到 `/legacy/` 作回滚，
#   桥脚本与映射表一并下线（归档在 `docs/attic/20261026_v2bridge_retire/`）。
# 于是下面改为**反向守卫**：这些东西不许回潮，且 v2 必须自带全部 Tab 对应的卡。


def test_home_is_v2_shell_not_legacy() -> None:
    """首页 `/` 必须返回 v2 壳，且不得再往页面里注入桥脚本或归属清单。"""
    from fastapi.testclient import TestClient

    from app.core.main import create_app

    app = create_app(plugins_dir=PLUGINS, web_dir=WEB)
    res = TestClient(app).get("/")
    assert res.status_code == 200
    html = res.text
    assert "shell.js" in html and "rail-tabs" in html, "首页不是 v2 壳"
    assert "v2bridge" not in html, "首页仍在注入 v2 桥脚本（已下线）"
    assert "__V2_OWNED" not in html, "首页仍在注入 __V2_OWNED（已下线）"
    assert "统一作战面板" not in html, "首页还在服务旧页（旧页应只在 /legacy/）"


def test_legacy_page_still_served_for_rollback() -> None:
    """旧页必须仍在 `/legacy/` 可访问（回滚入口，不能因为收口就丢掉）。"""
    from fastapi.testclient import TestClient

    from app.core.main import create_app

    app = create_app(plugins_dir=PLUGINS, web_dir=WEB)
    res = TestClient(app).get("/legacy/")
    assert res.status_code == 200
    assert "统一作战面板" in res.text, "旧页在 /legacy/ 下取不到了，回滚路径已断"


def test_v2bridge_files_stay_retired() -> None:
    """桥脚本与映射表不得回到静态区（回潮 = 又有人在旧页里套 iframe 了）。"""
    assert not (WEB / "v2bridge.js").exists(), "v2bridge.js 已被下线，不该回到 app/web"
    assert not (WEB / "v2_map.json").exists(), "v2_map.json 已被下线，不该回到 app/web"


def test_v2_cards_cover_all_nine_legacy_tabs() -> None:
    """旧页九个 Tab 在 v2 里都必须有对应卡（收口的前提；少一个就不该切首页）。

    2026-11-07：`battle_plan`（作战表）已整体删除，导航位由 `alert`（预警）顶替
    （主人拍板：只有模拟盘，签收是空壳）。此断言也跟着换成新口径，不再要求旧卡存在。
    """
    tabs = ["dashboard", "alert", "intraday", "sector", "concept",
            "review", "watchlist", "selection", "ops"]
    for tab in tabs:
        # 卡 id 约定 `<tab>.main`，板块是例外（sector.board）
        candidates = [tab + ".main", tab + ".board"]
        assert any(_card_exists(c) for c in candidates), (
            "旧页 Tab %s 在 v2 里没有对应卡：查过 %s" % (tab, candidates))


def _card_exists(card_id: str) -> bool:
    for manifest in PLUGINS.glob("*/plugin.json"):
        data = json.loads(_read(manifest))
        for card in ((data.get("frontend") or {}).get("cards") or []):
            if card.get("id") == card_id:
                return True
    return False


# ---------------------------------------------------------------- 2026-10-25 验收发现的真缺陷回归
#
# 两处都不在原单的静态检查范围内，是「从 8810 旧界面首页进入」这条用户真实链路才暴露的：
#   ① 主题键名两套（旧页 panel_theme / v2 壳 shell_theme）→ 卡里主题永不跟随；
#   ② `?card=` 单卡模式把 state.cards 过滤成 1 张 → 卡间跳转查表失败（而 v2bridge 嵌旧页用的正是单卡模式）。
# 这些断言守的是「读哪份清单、读哪套键」，防止以后又把 allCards 与 cards 揉回一个变量。


def test_theme_reads_both_legacy_and_shell_keys() -> None:
    src = _shell()
    assert "panel_theme" in src, "必须兼容旧界面的 panel_theme 键"
    assert "panel_fs" in src, "必须兼容旧界面的 panel_fs 键"
    assert "shell_theme" in src and "shell_fs" in src, "自身的 shell_theme/shell_fs 键不能丢"
    # 两套键必须都写：只写一套会让另一侧静默丢偏好
    assert "lsSetAll(LS_THEMES" in src, "主题键必须两套一起写"
    assert "lsSetAll(LS_FSS" in src, "字号键必须两套一起写"


def test_theme_message_channel_shell_side() -> None:
    """v2 壳仍要能收父页主题消息（2026-10-26 收口后父页只剩历史入口）。

    改前这条是「两端都有」：发送端=旧页里的 v2bridge，接收端=v2 壳。
    首页切成 v2 壳后发送端已随桥下线，但 v2 壳仍可能被嵌（旧书签、外部工具用 ?card=），
    故保留接收端校验，避免以后有人把它当成死代码删掉。
    """
    shell = _shell()
    assert "hana:theme" in shell, "v2 壳未接收主题消息"
    assert "bindThemeFromParent()" in shell, "v2 壳未安装主题消息监听"


# ---------------------------------------------------------------- 2026-10-25 独立形态左键开 K 线（修「板块详情页个股左键点不开」）
#
# 现场：`bindKlineBridge()` 第一行是 `if (window.parent === window) return false;`，
# /v2/ 独立打开时左键委托**根本不装** → 板块详情弹窗（挂 document.body，不在 el.stage 内）里
# 个股名的 `.stk-click[data-open-kline]`（sector/frontend/detail.js:286）点了毫无反应；
# 而右键菜单 ctxOpenKline / ctxOpenSector 的本页路径是好的（B 单验收「打开 K 线切卡」通过）。
# 修法：独立形态不再短路，改挂 **document 捕获阶段** click 委托，直接调那两个函数。
# 下面守的是「不得退回短路」+「本页路径真的接上了」。


def _js_function_body(src: str, header: str) -> str:
    """按大括号配对截出某个顶层函数体（bindKlineBridge 内无花括号字符串，配对安全）。"""
    start = src.index(header)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("函数体未闭合：%s" % header)


def test_kline_bridge_does_not_short_circuit_in_standalone() -> None:
    """独立形态不得直接 return false：那等于 /v2/ 直开时左键委托不存在。"""
    body = _js_function_body(_shell(), "function bindKlineBridge()")
    assert not re.search(r"window\.parent\s*===\s*window\)\s*return\s+false", body), (
        "bindKlineBridge 独立形态不得短路 return false（左键 data-open-* 会静默失效）"
    )


def test_kline_bridge_uses_document_capture() -> None:
    """必须挂 document 级 click 捕获委托（两种形态共用一套，2026-10-26 合并）。

    必须是 document 而不是 el.stage：板块详情弹窗挂在 document.body 上（detail.js:325），
    stage 覆盖不到它 —— 这正是「详情页里个股点不开」的另一半原因。
    不再按形态拆两套：拆两套的代价是同一动作两份实现，改一边另一边不生效。
    """
    body = _js_function_body(_shell(), "function bindKlineBridge()")
    assert "document.addEventListener('click'" in body, "缺 document 级 click 委托"
    assert body.count("addEventListener('click'") == 1, "只应有一套 click 委托"
    assert "}, true);" in body, "委托未开 capture 阶段（会被卡片自身点击抢先）"
    for kw in ("ctxOpenKline(", "ctxOpenSector(", "data-open-name", "stopPropagation"):
        assert kw in body, "缺 %s" % kw


def test_kline_bridge_embedded_forwards_kline_but_not_sector() -> None:
    """嵌入旧页时：K 线仍转父页 KLineApp；板块详情**不转**（转了就是 8002）。

    旧实现不分动作，命中任意 data-open-* 就 postMessage ⇒ 板块详情被父页 AmvCard 接走，
    而 AmvCard 就是 iframe 嵌 8002 —— 这是 8002 关不掉的唯一原因。
    """
    body = _js_function_body(_shell(), "function bindKlineBridge()")
    assert "parentOrigin()" in body, "父页 origin 解析丢了"
    assert "PM_KLINE" in body, "嵌父页时 K 线必须还转父页 KLineApp"
    assert "window.parent.postMessage(" in body, "K 线的父页转发被删了"
    # PM_AMV 只能出现在 ctxOpenSector 的兜底分支头（“本页连 sector 插件都没有”），
    # 不得出现在 bindKlineBridge 里（那就是又绕回 8002）
    assert "PM_AMV" not in body, (
        "bindKlineBridge 里还在向父页发 PM_AMV —— 板块详情又回到 AmvCard/8002 了"
    )


def test_click_inside_sector_modal_closes_it() -> None:
    """从板块详情弹窗里开 K 线后，必须把弹窗收掉。

    弹窗（`.sector-modal`，detail.js:325）挂在 document.body 上，是 position:fixed + inset:0 +
    z-index:50 的全屏遮罩，且板块卡 unmount 不带走它 —— 只修「能开卡」而不收弹窗，用户看到的
    仍是那个遮罩盖着新卡，体感还是「点了没反应」。
    """
    src = _shell()
    assert "function sectorModalOf(" in src, "缺弹窗识别（.sector-modal）"
    assert "function closeSectorModal(" in src, "缺弹窗收口"
    assert re.search(r"closest\('#sector-modal, \.sector-modal'\)", src), (
        "弹窗选择器必须对上 detail.js 实际挂的 id/class（#sector-modal / .sector-modal）"
    )
    body = _js_function_body(src, "function bindKlineBridge()")
    assert "sectorModalOf(node)" in body, "点击未识别弹窗上下文"
    assert "closeSectorModal(modal)" in body, "开卡后未收弹窗"
    assert body.count("closeSectorModal(modal)") >= 2, (
        "K 线与板块两条分支都要收弹窗"
    )


def test_ctx_open_functions_exist() -> None:
    """独立形态直接复用这两个函数：它们必须真的是 shell.js 里的函数声明（不是未来占位）。"""
    src = _shell()
    for fn in ("function ctxOpenKline(code, name, list)", "function ctxOpenSector(code, name)"):
        assert fn in src, "缺 %s" % fn
    # 本页路径的实质动作：切 K 线卡 / 板块卡详情弹窗
    assert re.search(r"findCardByPlugin\('kline'\)", src), "ctxOpenKline 缺切 K 线卡的本页路径"
    assert "panel:open-sector" in src, "ctxOpenSector 缺板块卡详情弹窗入口"


def test_card_jump_uses_full_card_list() -> None:
    """卡间跳转必须查全量卡片（allCards），否则 `?card=` 单卡模式下表现为「未装载」。"""
    src = _shell()
    assert "state.allCards" in src, "缺全量卡片清单 state.allCards"
    # bindCardJump 与 selectCard 都必须用全量清单查表
    for fn in ("function bindCardJump()", "async function selectCard(key, args)"):
        start = src.index(fn)
        tail = src[start:start + 900]
        assert "allCards" in tail, "%s 未用全量卡片清单查表" % fn.split("(")[0]
