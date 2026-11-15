# -*- coding: utf-8 -*-
"""K 线弹窗「旧版原样挂载」静态契约（2026-10-27）。

守的是这一轮的口径：**入口 = 8800 旧版完整弹窗原样**，不是重画一版简化弹窗。
  · `frontend/legacy/modal.html` 必须与 `engine/web/index.html` 的 `#klineModal` 块逐字一致；
  · `frontend/legacy/modal.css` 必须带齐弹窗所需的五组规则（K 线 / HQChart 画布 / 盘口 kqp /
    F10 kfp / 下单 ko / 画线菜单 dd），且裸元素规则已限定到 `#klineModal` 作用域；
  · `frontend/legacy_modal.js` 的装载顺序 = 旧页 index.html:9022-9029 的顺序；
  · 入口 `frontend.js` 指向装载器，版本串与 manifest 一致；不许 iframe 嵌旧整页。
只读源码，不跑浏览器。
"""
import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
FRONTEND = PLUGIN / "frontend"
LEGACY = FRONTEND / "legacy"
LEGACY_JS = FRONTEND / "legacy_modal.js"
ENTRY = PLUGIN / "frontend.js"
# 旧引擎前端仍在工作区根的 engine/web 下（与本仓库并列，不在本包内）。
# 2026-11-15：改为按本文件位置推导，不再写死本机绝对路径。
PROJECT_ROOT = PLUGIN.parents[2]          # app/plugins/kline → panel-v2
WS_ROOT = PROJECT_ROOT.parents[1]         # panel-v2 → projects → 工作区根
OLD_INDEX = WS_ROOT / "engine" / "web" / "index.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _manifest_version() -> str:
    return str(json.loads(_read(PLUGIN / "plugin.json"))["version"])


# ---------------------------------------------------------------- DOM

# 旧四个模块按这些 ID 取节点，少一个就是静默失效（不是「看起来还行」）
REQUIRED_IDS = [
    "klineModal", "klineTitle", "kMinWrap", "kPerGroup", "kMinPill", "kMinMain", "kMinPanel",
    "kTplWrap", "kTplStack", "kIndPanel", "kIndMain", "kIndSub",
    "kToolWrap", "kToolToggle", "kToolPanel", "kToolChips", "kToolAdjust", "kToolCost",
    "kToolTPSL", "kToolTPSLOp", "kToolBS", "kDrawClear",
    "kBgWrap", "kBgToggle", "kBgPanel", "kBgDesc", "btnWatch", "btnSelPool", "btnRefreshK",
    "klineStatus", "klineChart", "hq_minute", "kTopLeft", "kTip", "kAdjInfo", "kXLabel",
    "kchipCanvas", "kauctionCanvas", "kRangeBox", "kRangeInfo", "kQuotePanel", "kF10Panel",
]


def test_modal_html_exists_and_is_extracted_not_handwritten():
    p = LEGACY / "modal.html"
    assert p.exists(), "缺 frontend/legacy/modal.html（旧弹窗 DOM）"
    src = _read(p)
    assert src.lstrip().startswith("<!--"), "modal.html 应带「原样提取」来源注释"
    assert "engine/web/index.html" in src, "modal.html 未标明提取来源"


def test_modal_html_has_all_required_ids():
    src = _read(LEGACY / "modal.html")
    missing = [i for i in REQUIRED_IDS if f'id="{i}"' not in src]
    assert not missing, "旧弹窗 DOM 缺关键 ID：%s" % missing


def test_modal_html_keeps_old_inline_handlers():
    """旧 DOM 的 onclick 直调 KLineApp.*，必须原样保留（改名等于把按钮变成死的）。"""
    src = _read(LEGACY / "modal.html")
    for call in ("KLineApp.setPeriod('d')", "KLineApp.setPeriod('t')", "KLineApp.setPeriod('w')",
                 "KLineApp.toggleWatch()", "KLineApp.toggleSelPool()", "KLineApp.refreshData()"):
        assert call in src, "旧弹窗按钮缺原样 handler：%s" % call


def test_modal_html_matches_old_index_block():
    """逐字对照：旧 index 的 #klineModal 块必须与插件里的 DOM 一致（防手改漂移）。"""
    if not OLD_INDEX.exists():
        import pytest
        pytest.skip("旧仓 index.html 不在本机，跳过逐字对照")
    lines = _read(OLD_INDEX).split("\n")
    start = next(i for i, t in enumerate(lines, 1) if t.strip() == '<div id="klineModal">')
    depth, end = 0, None
    for i in range(start, len(lines) + 1):
        depth += lines[i - 1].count("<div") - lines[i - 1].count("</div>")
        if i > start and depth == 0 and "</div>" in lines[i - 1]:
            end = i
            break
    assert end, "旧 index 的 #klineModal 未闭合"
    old = "\n".join(lines[start - 1:end]).strip()
    new = _read(LEGACY / "modal.html")
    body = new[new.index("<div id=\"klineModal\">"):].strip()
    # 2026-10-28：插件新增的节点（左侧列表卡、列表/F10 开关）包在 `<!-- v2:extra -->` 里，
    # 比对前剥掉；旧块其余部分仍逐字对照，防手改漂移。
    body = re.sub(r"\n?\s*<!-- v2:extra -->.*?<!-- /v2:extra -->", "", body, flags=re.S)
    # 2026-10-30（主人：筹码/复权/成本线/买卖点移入右键「主图设置」，工具面板只留画线）：
    # 工具面板「浮层」段与旧块必然不同 —— 比对前把这段换回旧块原文，其余仍逐字对照。
    old_tool = re.search(r'\s*<div class="k-sec">浮层</div>.*?(?=<div class="k-sec">划线 / 测量</div>)', old, re.S)
    if old_tool:
        body = re.sub(r"<!-- v2:toolpanel -->.*?<!-- /v2:toolpanel -->",
                      lambda m: old_tool.group(0).strip(), body, flags=re.S)
    assert body == old, "modal.html 与旧 index 的 #klineModal 不一致（被手改过）"


# ---------------------------------------------------------------- CSS

CSS_GROUPS = {
    "弹窗外壳": r"#klineModal \{",
    "图表容器": r"#klineChart \{",
    "HQChart 分时": r"#hq_minute \{",
    "盘口卡": r"\.kqp-lv \{",
    "F10 卡": r"\.kfp-grid \{",
    "下单面板": r"\.ko-submit \{",
    "下单菜单": r"#koMenu\.ko-menu",
    "画线菜单": r"\.dd-menu \{",
    "分钟下拉": r"#kMinPanel \.k-mini",
    "测距卡": r"#kRangeInfo \{",
    "窄屏兜底": r"@media \(max-width:900px\)",
}


def test_modal_css_covers_every_panel_not_just_toolbar():
    """只拿头部工具栏样式是这一轮最容易犯的错：图表/盘口/订单/弹层/响应式一条都不能漏。"""
    src = _read(LEGACY / "modal.css")
    missing = [k for k, pat in CSS_GROUPS.items() if not re.search(pat, src)]
    assert not missing, "modal.css 缺这些规则组：%s" % missing


def test_modal_css_does_not_repaint_theme():
    """不另造主题：不得重定义 :root / [data-theme] 的色值（色只从 themes.css 来）。"""
    src = _read(LEGACY / "modal.css")
    assert not re.search(r"^\s*:root\s*\{", src, re.M), "modal.css 重定义了 :root"
    assert not re.search(r"^\s*\[data-theme", src, re.M), "modal.css 重定义了主题层"


def test_modal_css_bare_element_rules_are_scoped():
    """作为插件资源加载，裸 `button {}` 会污染壳的通用控件 —— 必须限定到 #klineModal。"""
    src = _read(LEGACY / "modal.css")
    bare = re.findall(r"^(button|input|select|label|table)\b[^{]*\{", src, re.M)
    assert not bare, "modal.css 仍有未限定的裸元素规则：%s" % bare
    assert "#klineModal button {" in src, "旧页 button 基础样式没有搬进来（弹窗按钮会掉样式）"


# ---------------------------------------------------------------- 装载器

# 旧页 index.html:9022-9029 的真实顺序（vendor/klinecharts 略：现 LC 不用，v9 旧块已置 text/plain）
EXPECT_ORDER = [
    "/vendor/lightweight-charts.standalone.production.js",
    "/vendor/lightweight-charts-drawing.umd.js",
    "/vendor/jquery.min.js",
    "/vendor/hqchart.umychart.min.js",
    "/kline_lc.js",
    "/kline_quote5.js",
    "/kline_f10.js",
    "/kline_order.js",
]


def test_loader_script_order_matches_old_index():
    src = _read(LEGACY_JS)
    # 2026-10-28：旧模块 URL 带 V 缓存串；2026-11-12 再追加构建号 B → 只取引号里的 base。
    found = [u for u in (u.split('?')[0] for u in re.findall(r"\['[a-z0-9_-]+',\s*'([^']+)'", src)) if u.startswith('/')]
    assert found == EXPECT_ORDER, "装载顺序与旧页不一致：%s" % found


def test_loader_is_classic_script_not_module():
    """四个旧模块是 IIFE，靠 window.KLineApp 串链：必须经典 script（不能 type=module）。"""
    src = _read(LEGACY_JS)
    assert "s.async = false" in src, "经典脚本要显式 async=false 保序"
    assert not re.search(r"\.type\s*=\s*['\"]module", src), "旧模块不能用 module 装"
    assert 'type="module"' not in src, "旧模块不能用 module 装"
    assert "import(" not in src and "await import" not in src, "旧模块不能用动态 import 装"


def test_loader_is_idempotent_by_construction():
    """CSS / DOM / 每个脚本各插一次；初始化走单一 Promise，并发调用共用同一次。"""
    src = _read(LEGACY_JS)
    assert "if (document.getElementById(CSS_ID)) return;" in src, "CSS 未做幂等判重"
    assert "if (document.getElementById('klineModal')) return;" in src, "DOM 未做幂等判重"
    assert 'data-kline-legacy' in src and "dataset.state" in src, "脚本未做幂等/状态判重"
    assert "if (!_initPromise)" in src, "初始化未收敛到单一 Promise"
    assert "if (legacyReady()) return;" in src, "已有 library 时未短路（会重复建实例）"


def test_loader_fails_loudly_not_silently():
    src = _read(LEGACY_JS)
    assert "function showError(" in src, "缺可见错误出口"
    assert "console.error" in src, "失败必须打 console.error"
    assert "overlay.js" not in src, "装载器不得回落到简化版（主人明确不要静默切换）"


def test_loader_exports_public_entry():
    src = _read(LEGACY_JS)
    for name in ("export async function openKline", "export function closeKline",
                 "export const openKlineStandalone"):
        assert name in src, "缺公开导出：%s" % name
    assert "app.open(code, opt.name || '', curList)" in src, "未按旧 open(code, name, list) 真实签名调用（list 缺失则 ↑↓ 切票不可用）"
    assert "app.setPeriod(p)" in src, "period 未走旧 setPeriod"


def test_loader_period_whitelist_matches_old_module():
    src = _read(LEGACY_JS)
    assert "PERIOD_KEYS = ['d', 't', 'w', 'm1', 'm5', 'm15', 'm30', 'm60']" in src, \
        "周期白名单须与 kline_lc.js 的 PERIOD_KEYS 一致"


# ---------------------------------------------------------------- 点卡片外空白关闭（0.7.2）

def test_mask_click_close_binds_on_modal_not_document():
    """V2 全局口径：点遮罩空白关弹窗。必须绑在 #klineModal 上，不能挂 document 级外点监听。

    旧 `kline_order.js:1383` 明确取消了外点关闭（拖拽误退），原样挂载把这行为带回来了。
    这里守住补回的方式：不用 document 级监听——`koPanel`/`koMenu`/`ddMenu` 挂在 body 上，
    document 级监听会把「点下单卡」当成外部点击而误退。
    """
    src = _read(LEGACY_JS)
    assert "function bindMaskClose()" in src, "缺遮罩关闭绑定函数"
    i = src.index("function bindMaskClose()")
    body = src[i:src.index("\n}\n", i) + 3]
    assert "document.getElementById('klineModal')" in body, "必须绑定 #klineModal 本身"
    assert "m.addEventListener('click'" in body, "必须用 click（与 V2 paper/sector 同事件）"
    assert "document.addEventListener" not in body, "不得用 document 级外点监听（body 浮层会误退）"
    assert "ev.target !== ev.currentTarget" in body, "必须判 target===currentTarget（真正遮罩空白）"


def test_mask_click_close_requires_both_down_and_up_on_mask():
    """不照抄「拖拽时外松关闭」：图表内按下 → 遮罩上松开，浏览器也会派一个 click，
    target 是共同祖先（遮罩）。所以必须按下与松开都在遮罩上才关。"""
    src = _read(LEGACY_JS)
    i = src.index("function bindMaskClose()")
    body = src[i:src.index("\n}\n", i) + 3]
    assert "pointerdown" in body and "pointerup" in body, "需记录 pointerdown/pointerup 两个落点"
    assert "downOnMask" in body and "upOnMask" in body, "两个落点都要记"
    assert "if (both) closeKline();" in body, "两个落点都在遮罩上才 closeKline()"
    for bad in ("preventDefault", "stopPropagation"):
        assert bad not in body, f"遮罩绑定层不得 {bad}（会打乱旧模块画线/拖拽）"


def test_mask_click_close_is_idempotent_and_only_on_own_dom():
    """重开不重复监听；且只给本插件插的 DOM 打标，不插手旧页原生弹窗。"""
    src = _read(LEGACY_JS)
    assert "MOUNT_ATTR" in src, "需给自插 DOM 打标以区分旧页原生弹窗"
    assert "m.dataset.maskClose === '1'" in src, "缺幂等判重（重开会重复绑）"
    assert "m.dataset.maskClose = '1'" in src, "缺幂等标记写入"
    assert "m.getAttribute(MOUNT_ATTR) !== '1'" in src, "必须只对自己插的 DOM 绑定"
    # 绑定必须发生在 DOM 到位之后、且与旧模块是否已就绪无关（已有 library 也要能关）
    init = src[src.index("async function doInit()"):]
    assert init.index("await ensureDom()") < init.index("bindMaskClose()"), \
        "bindMaskClose 必须在 ensureDom 之后"
    assert init.index("bindMaskClose()") < init.index("if (legacyReady()) return;"), \
        "bindMaskClose 必须在 legacyReady 短路之前（旧库已在也要能点空白关）"


def test_entry_points_at_legacy_loader_with_manifest_version():
    src = _read(ENTRY)
    ver = _manifest_version()
    m = re.search(r"""from\s+["']\./frontend/legacy_modal\.js\?v=([^"']+)["']""", src)
    assert m, "frontend.js 入口未指向 legacy_modal.js"
    assert m.group(1) == ver, "入口版本串 %s != manifest %s" % (m.group(1), ver)
    assert "openKlineStandalone = openKline" in src, "公开导出名被改掉了"


def test_loader_version_constant_matches_manifest():
    """装载器里的 V 是静态资源版本串的第二份，必须与 manifest 同步（防漂移）。"""
    src = _read(LEGACY_JS)
    m = re.search(r"const V = '([0-9.]+)'", src)
    assert m, "legacy_modal.js 缺版本常量 V"
    assert m.group(1) == _manifest_version(), \
        "V=%s 与 manifest %s 不一致" % (m.group(1), _manifest_version())


def test_no_iframe_of_old_page():
    """用户要的是旧弹窗原样作为插件，不是 iframe 嵌旧整页。"""
    for p in (LEGACY_JS, LEGACY / "modal.html"):
        src = _read(p)
        assert "<iframe" not in src and "createElement('iframe')" not in src, \
            "%s 里出现了 iframe（禁用整页嵌入）" % p.name


def test_supertrend_single_line_and_hollow_candles():
    """2026-10-28 主人标注 45/46：超级趋势一条线逐点换色、阳线空心；2026-11-02 成交额副图换公式版。

    旧页与 v2 共用 engine/web/kline_lc.js，这里直接读旧文件守行为。
    """
    src = _read(OLD_INDEX.parent / "kline_lc.js")
    assert "function supertrendSegs(stv)" in src, "超级趋势缺少分段函数（同一时间必须只有一条线）"
    assert "function supertrendSeriesList(stv, kd, cRise, cFall" in src, "超级趋势缺少分段 series 列表"
    assert "supertrendSeriesList(stv, kd, cRise, cFall, " in src, "主图未按段拆 series"
    assert "if (k > 0 && r.dir[k - 1] === -1) dn[k] = r.lower[k];" not in src, \
        "翻转点不得补点（会重叠成两条线；主人要求断开）"
    assert 'upColor: "rgba(0,0,0,0)"' in src and "borderUpColor: p.up" in src, "阳线未空心"
    # 2026-11-02 主人：成交额副图改照通达信「成交量分析副图」公式——柱体全部交给图元画，
    #   hist 只撑纵轴（颜色全透明）；原「阳线空心/阴线实心」的 hist 双色写法已废。
    assert 'color: "rgba(0,0,0,0)" }; }) });' in src, "成交额 hist 未改为纯透明（撑轴用）"
    assert "function drawAmountBars(target, S)" in src, "成交额柱图元（公式版）缺失"
    assert "function amtLimitPct(code)" in src, "涨跌停比例判定缺失"
    assert "var SUB_BAR_ALPHA = 0.7;" in src, "柱体不透明度常量缺失"
    assert "hexToRgba(v >= 0 ? pal.up : pal.down, SUB_BAR_ALPHA)" in src, \
        "主力资金柱未跟成交额柱同口径"
    assert "bars: function () {" in src, "固定范围量分布未接 bars（volume 缺失会画空框）"


def test_legacy_assets_are_plugin_resources():
    """产物落在插件目录内、由 /plugins/kline/frontend/legacy/ 提供（不改 engine/web）。"""
    for name in ("modal.html", "modal.css"):
        assert (LEGACY / name).is_file(), "缺 %s" % name
    src = _read(LEGACY_JS)
    assert "/plugins/kline/frontend/legacy" in src, "资源 URL 不在插件静态路由下"
    assert "?v=${V}" in src, "资源 URL 缺版本串（缓存换不掉）"


def test_range_volume_profile_uses_minute_bars():
    """2026-11-11 主人：区间筹码的成交量改用 1 分钟线分档（日线摊平太粗）。

    守四件事：
      1. 前端把 minute bars 接到 DrawingManager 的 volumeProfileBars（不是改全局 bars，
         否则持仓/形态等其它工具会跟着改用分钟根）；
      2. 按日取 `/api/intraday/{code}?date=`——该端点近期走 1m bars、更早自动落
         `minutes.history`（深到 2016），这是主人说的「两个数据源」；
      3. 分钟量合计 < 日线量 90% 时回退日线根；≥90% 时按日线总量归一（集合竞价约 7% 不该让整日退回日线）；
      4. 库里真有这个覆盖口——否则前端传了也白传（库内部只读 series bars）。
    """
    src = _read(OLD_INDEX.parent / "kline_lc.js")
    assert "volumeProfileBars: function () { return _vpBars(); }" in src, \
        "区间筹码未接 minutes 覆盖口"
    assert '"/api/intraday/"' in src and "?date=" in src, \
        "区间筹码未按日走 /api/intraday（近期 1m + 历史 minutes.history 两源）"
    assert "sumV < kv * 0.90" in src, "缺「分钟量对不上日线量就回退」的守卫"
    assert "vf = kv / sumV" in src, "缺「分钟量按日线总量归一」的缩放"
    assert "VP_MIN_POINTS" in src, "缺少点数下限（开盘半小时内不替换）"
    assert "VP_MIN_LADDER" not in src, "旧的 5m/15m 降级阶梯应已删除（历史源能直接给 1m）"
    vendor = _read(OLD_INDEX.parent / "vendor" / "lightweight-charts-drawing.umd.js")
    assert "this.coords.vpBars=" in vendor, "绘图库未挂 volumeProfileBars 覆盖口"
    assert "n.vpBars?n.vpBars():n.bars()" in vendor, "区间筹码算量未走覆盖口"
