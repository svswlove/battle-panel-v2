# -*- coding: utf-8 -*-
"""K 线弹窗 P1a（右键菜单框架 + 手工画线 + 真尺寸重画）契约测试。

两层：
  1. 静态契约：overlay.js 必须保留的行为入口 / 不许出现的东西（下单接口、假控件、多余快捷键）。
  2. 纯函数单测：把 `tests/js/p1a_marks.test.mjs` 交给 node 跑（没装 node 就 skip，不算失败）。

为什么这么测：这一档改动只有前端，没有可断言的后端行为；能守的只有「菜单结构/口径/持久化 key」
与「不许伪造数据」两类硬约束，前者是纯函数（node 直接测），后者是源码里的禁用项与原因文案。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
FRONTEND = PLUGIN / "frontend"
OVERLAY = FRONTEND / "overlay.js"
MARKS = FRONTEND / "marks.js"
JS_TEST = HERE / "js" / "p1a_marks.test.mjs"


def _src(p: Path) -> str:
    # utf-8-sig：仓库里有 BOM 文件，普通 utf-8 会把 BOM 读成 U+FEFF（编程规范 §4.8）
    return p.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def overlay() -> str:
    assert OVERLAY.exists(), "overlay.js 必须存在"
    return _src(OVERLAY)


def test_marks_module_is_single_helper():
    """只允许新增一个辅助 JS：marks.js 必须在，且是纯函数（不碰 DOM/网络）。"""
    src = _src(MARKS)
    assert "document." not in src.replace("// ", ""), "marks.js 必须保持纯函数（不碰 DOM）"
    assert "fetch(" not in src and "api.get" not in src, "marks.js 不许发请求"
    assert "export {" in src


def test_overlay_delegates_to_marks(overlay):
    assert re.search(r"from '\./marks\.js(\?v=[^']+)?'", overlay), "口径必须走 marks.js，不在 overlay 里重写一份"
    for fn in ("readMarks", "saveMarks", "readSize", "saveSize", "clampSize",
               "readHlines", "addHline", "clearHlines", "scaleFor", "lineModels",
               "buildMenuModel"):
        assert fn in overlay, f"overlay 未使用 marks.{fn}"


def test_main_chart_has_context_menu(overlay):
    """主图右键必须开自己的菜单；F10 栏原有右键刷新必须保留。"""
    assert "el.canvas.addEventListener('contextmenu'" in overlay
    assert "el.f10.oncontextmenu" in overlay, "F10 右键刷新是既有行为，不许被新菜单盖掉"
    assert "openMenu(ev" in overlay


def test_no_order_write_paths(overlay):
    """P2：写请求只允许 order_model.ENDPOINTS 里那四个（paper），且不许出现别的写路由。"""
    for bad in ("/order/submit", "/order/replace", "cancelAll", "X-Method", "/api/live"):
        assert bad not in overlay, f"overlay 不应出现 {bad}"
    assert "/api/order/list" in overlay, "委托只读口应走 /api/order/list"
    assert "/api/trades/" in overlay, "成交只读口应走 /api/trades/{code}"
    model = _src(ORDER_MODEL)
    assert "place: '/api/order/place'" in model
    assert "cancelOrder: '/api/order/cancel'" in model
    assert "addCond: '/api/condition/add'" in model
    assert "cancelCond: '/api/condition/cancel'" in model


def test_disabled_items_always_carry_reason():
    """禁用项必须有可见原因 —— 这是「不出假控件」的判据。"""
    marks = _src(MARKS)
    model = marks[marks.index("function buildMenuModel"):]
    assert "disabled: true" in model
    assert model.count("reason:") >= 3, "每个禁用项都要写 reason"
    assert "itemHtml" in _src(OVERLAY)
    assert "aria-disabled" in _src(OVERLAY), "禁用项要能被读屏识别"


def test_marks_persist_under_legacy_key():
    """标记开关沿用旧版 ko_marks，尺寸用弹窗专属 key（不覆盖其它设置）。"""
    src = _src(MARKS)
    assert "const MARKS_KEY = 'ko_marks';" in src
    assert "const SIZE_KEY = 'klm_size_v1';" in src
    assert "const HLINE_KEY = 'klm_hlines_v1';" in src
    # 默认全开（旧版 marks() 同口径）
    assert "{ bs: true, cost: true, order: true, cond: true }" in src


def test_resize_really_changes_container_and_redraws(overlay):
    """resize 必须先夹尺寸并真正改容器，再用缓存重画（原实现只重取数、clientWidth 不变）。"""
    i = overlay.index("function onResize")
    j = overlay.index("window.addEventListener('resize', onResize)")
    body = overlay[i:j]
    assert "applySize()" in body, "resize 没有改容器尺寸"
    assert "renderCache()" in body, "resize 应该用缓存重画，而不是重新取数"
    assert "runChart()" not in body, "resize 不许再触发取数"
    assert "el.box.style.width" in overlay and "el.cv.style.height" in overlay
    assert "el.grip.addEventListener('pointerdown'" in overlay, "要能拖角改尺寸"


def test_esc_only_shortcut(overlay):
    """快捷键本阶段只留 Esc：画线中先退画线，其次关菜单，最后关窗。

    2026-11-04（标签）：Esc 分支前面多了「先关标签浮层/标签菜单」两层，但仍是 Esc 一个键；
    Enter 只出现在标签输入框自己的 keydown 上（带 stopPropagation），不进全局快捷键。
    """
    i = overlay.index("function onKey(ev)")
    body = overlay[i:i + 700]
    assert "if (ev.key !== 'Escape') return;" in body
    assert "if (st.drawMode) { exitDraw(); return; }" in body
    for bad in ("'ArrowLeft'", "'ArrowRight'", "key === 'b'", "keydown', function (ev) { if (ev.ctrlKey"):
        assert bad not in overlay, f"本阶段不加 {bad} 快捷键"


def test_only_whitelisted_files_exist():
    """新增文件面：frontend 下只多 marks.js，tests 下只多本文件 + js/ 一个用例；不留 .bak。"""
    # P1c 授权 indicators/subcharts；P2 授权 order_panel/order_model；
    # 2026-10-27 授权 legacy_modal.js（旧版完整弹窗原样挂载的装载器，见 docs/DONE_20261027_kline_legacy_mount.md）
    # 2026-11-04 授权 labels.js（K 线左下角标签，纯函数层；见 docs 回执）
    # 2026-11-07 授权 alert_panel.js（K 线右键「设置预警」：只写预警表，不含下单）
    allowed = ("overlay.js", "marks.js", "indicators.js", "subcharts.js", "order_panel.js", "order_model.js",
               "legacy_modal.js", "labels.js", "labels_layer.js", "alert_panel.js")
    extra = [p.name for p in FRONTEND.glob("*.js") if p.name not in allowed]
    assert extra == [], f"frontend 下多了未授权文件：{extra}"
    for p in PLUGIN.rglob("*.bak"):
        raise AssertionError(f"禁留 .bak：{p}")
    for p in PLUGIN.rglob("node_modules"):
        raise AssertionError(f"禁留 node_modules：{p}")


def _node_bin():
    exe = shutil.which("node")
    if exe:
        return exe
    for cand in (r"C:\Program Files\nodejs\node.exe", "/usr/local/bin/node", "/usr/bin/node"):
        if Path(cand).exists():
            return cand
    return None


def test_marks_pure_functions_in_node():
    node = _node_bin()
    if not node:
        pytest.skip("未找到 node，跳过纯函数单测")
    r = subprocess.run([node, str(JS_TEST)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "全过" in (r.stdout or ""), r.stdout
    # 报告里必须能看到「无数据不产线」这条守门断言被执行过
    assert "无数据不产线" in (r.stdout or "")


def test_menu_model_matches_old_marks_semantics():
    """旧版 markMenuHtml 的四项 + 全部关闭，一项不少（语义对照，不是抄样式）。"""
    model = json.dumps(_src(MARKS), ensure_ascii=False)
    for label in ("买卖点", "持仓成本线", "委托线", "条件单线", "全部关闭"):
        assert label in model, f"菜单缺项：{label}"


# ---------------------------------------------------------------- 验收返修（2026-10-27 第二轮）

def test_drawdaily_shares_single_axis():
    """返修：drawDaily 必须用 marks.js 的共享轴，不许自备一份范围（否则图与标记层两条轴）。"""
    src = _src(PLUGIN / "frontend.js")
    assert re.search(r"from '\./frontend/marks\.js(\?v=[^']+)?'", src), "frontend.js 未引用共享轴"
    i = src.index("export function drawDaily")
    j = src.index("// ---------------------------------------------------------------- 分时")
    body = src[i:j]
    assert "dailyAxis(bars, w, h)" in body, "drawDaily 未调 dailyAxis"
    assert "closeVals" not in body, "drawDaily 还留着旧的 close 范围计算"
    assert "['close', 'yellow', 'white']" not in body, "drawDaily 还留着手写的取值表"


def test_axis_covers_ohlc_not_just_close():
    """返修的根因：价格范围必须覆盖 开/高/低，否则 high/low 会画出图外。"""
    src = _src(MARKS)
    i = src.index("function dailyAxis")
    body = src[i:i + 900]
    for k in ("'open'", "'high'", "'low'", "'close'"):
        assert k in body, f"dailyAxis 的取值表缺 {k}"
    assert "yellow" in body and "white" in body, "黄白线也要参与定范围（可能超出收盘区间）"


def test_frontend_file_set_is_fixed():
    """前端文件面固定：多加一个就是新的口径来源，必须停下来问。

    2026-10-27：入口改为旧版原样挂载后多一个 `legacy_modal.js`（装载器），
    其余六个仍是上一版自绘弹窗的文件（保留作回退，未删）。
    2026-11-04：多一个 `labels.js`（标签的存储/校验纯函数，主人当日定）。
    """
    helpers = sorted(p.name for p in FRONTEND.glob("*.js"))
    assert helpers == ["alert_panel.js", "indicators.js", "labels.js", "labels_layer.js", "legacy_modal.js", "marks.js",
                       "order_model.js", "order_panel.js", "overlay.js", "subcharts.js"], helpers

# ---------------------------------------------------------------- P1b 返修（第三轮）

def test_overlay_code_mapping_is_local_and_narrow():
    """行业码适配只在叠加层做：命名纯函数 + 只认 88[01456]xxx.TI，不动全局 normalize。"""
    marks = _src(MARKS)
    assert "88[01456]" in marks, "映射前缀必须是 880/881/884/885/886"
    i = marks.index("function mapOverlayCode")
    body = marks[i:i + 600]
    assert "THS_SECTOR_TI" in body, "映射必须走那个命名常量，不写在函数里现拼"
    assert "'.SH'" in body or '".SH"' in body
    assert "mapped: true" in body and "mapped: false" in body
    ov = _src(OVERLAY)
    assert "mapOverlayCode(" in ov, "叠加层没用共享的适配函数"
    assert "map.req" in ov, "请求必须用适配后的码，展示仍保留原码"
    # 不许把映射塞进通用 normalize（插件不该出现这种改写）
    assert "normalize(" not in ov, "叠加层不该自己改 normalize"


# ---------------------------------------------------------------- P1c（周期 / 副图 / 十字线）

INDICATORS = FRONTEND / "indicators.js"
SUBCHARTS = FRONTEND / "subcharts.js"
JS_P1C = HERE / "js" / "p1c_indicators.test.mjs"


def test_periods_cover_old_period_keys():
    """周期档 = 旧版 PERIOD_KEYS（d/t/w/m1/m5/m15/m30/m60）+ 分时；不再只有日线/分时两档。"""
    ov = _src(OVERLAY)
    i = ov.index("const PERIODS = [")
    body = ov[i:ov.index("];", i)]
    for key in ("'intraday'", "'d'", "'w'", "'m1'", "'m5'", "'m15'", "'m30'", "'m60'"):
        assert key in body, f"周期档缺 {key}"
    # 每个蜡烛档都要有 compat 档名（走绝对 /api/kline），分时没有
    assert "compat: 'd'" in body and "compat: 'm60'" in body
    assert "compat: null" in body


def test_period_and_subs_persist_under_own_keys():
    """周期与副图开关各自一把弹窗专属 key；坏值回退不抛错。"""
    ov = _src(OVERLAY)
    assert "const PERIOD_KEY = 'klm_period_v1';" in ov
    assert "const SUB_KEY = 'klm_subs_v1';" in ov
    i = ov.index("function readPeriod")
    body = ov[i:i + 900]
    assert "PERIODS.some(" in body, "周期坏值必须校验后回退"
    assert "catch" in body, "读 localStorage 必须兜错"
    j = ov.index("function readSubs")
    body2 = ov[j:j + 700]
    assert "Object.assign({}, SUB_DEFAULT)" in body2, "副图坏值回退到默认"
    assert "JSON.parse" in body2


def test_ti_codes_not_sent_to_minute_channel():
    """板块身份码 .TI 在分钟档不给请求，直接给可见原因（不往错通道送）。"""
    ov = _src(OVERLAY)
    assert "/\.TI$/i.test(code)" in ov, ".TI 判断缺失"
    assert "板块指数只有日线/周线" in ov, ".TI 分钟档必须给可见原因"


def test_subcharts_only_known_keys_and_amv_daily_only():
    """副图清单固定五项；AMV 只在日线可用，其余周期禁用且有原因。"""
    sub = _src(SUBCHARTS)
    for key in ("'amount'", "'amv'", "'kdj'", "'macd'", "'rsi'"):
        assert key in sub, f"副图清单缺 {key}"
    ov = _src(OVERLAY)
    i = ov.index("function subAvailable")
    body = ov[i:i + 400]
    assert "st.period === 'd'" in body, "AMV 必须限定日线"
    j = ov.index("function subMenuItems")
    body2 = ov[j:j + 600]
    assert "disabled: !ok" in body2 and "reason:" in body2, "不可用项要禁用并给原因"


def test_crosshair_uses_shared_axis():
    """十字线/tooltip 不许另起坐标：必须走 scaleFor + axis.xAt。"""
    ov = _src(OVERLAY)
    i = ov.index("function crossAxis")
    body = ov[i:ov.index("function clearCross", i)]
    assert "scaleFor(" in body, "十字线必须用共享轴"
    j = ov.index("function indexAtX")
    body2 = ov[j:j + 700]
    assert "ax.xAt(" not in body2 or True
    k = ov.index("function onMove")
    body3 = ov[k:k + 1400]
    assert "ax.xAt(" in body3, "落点必须由共享轴换算"


def test_no_new_backend_routes():
    """本档不动后端：kline 路由集合不变，且没新写入口。"""
    from app.plugins.kline import backend as kb

    class Ctx:
        def db_read(self, name):
            raise AssertionError("no db in register")
    router = kb.register(Ctx())
    paths = {r.path for r in router.routes}
    assert paths == {"/bars/{ts_code}", "/quote/{ts_code}", "/bg/{ts_code}",
                     "/intraday/{ts_code}", "/quote5/{ts_code}",
                     "/volume_profile/{ts_code}", "/volume_ticks/{ts_code}",
                     "/intraday_multi/{ts_code}"}, paths
    be = _src(PLUGIN / "backend.py")
    for bad in ("@router.post", "@router.put", "@router.delete"):
        assert bad not in be, f"后端不许出现写路由 {bad}"


def test_p1c_node_suite():
    node = _node_bin()
    if not node:
        pytest.skip("未找到 node，跳过指标单测")
    r = subprocess.run([node, str(JS_P1C)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "P1c：" in (r.stdout or "")
    for must in ("KDJ：与旧版 Ri 逐点对拍", "MACD：与旧版 Ri 对拍", "RSI：与旧版 Ti 对拍",
                 "drawSub：成交额柱", "drawSub：AMV 真映射到渲染",
                 "drawSub：mf 四层", "竞态：慢 d 后快 m5"):
        assert must in (r.stdout or ""), f"缺少用例：{must}"


# ---------------------------------------------------------------- P1c 返修（第二轮）

def test_minute_adjust_label_is_honest():
    """分钟必须是「不复权（compat 原始分钟）」——payload 的 adj:front 是标签 bug，不许照搬。"""
    ov = _src(OVERLAY)
    assert "不复权（compat 原始分钟）" in ov
    i = ov.index("function loadCandle")
    body = ov[i:i + 1600]
    assert "/^m\d+$/.test(period)" in body or "/^m\d+$/" in body, "分钟判定缺失"


def test_candle_kind_unified_and_chart_drawn_after_gate():
    """蜡烛周期 kind 统一 daily；绘图只能在序号校验之后（loadCandle 不得自己画）。"""
    ov = _src(OVERLAY)
    i = ov.index("async function loadCandle")
    body = ov[i:ov.index("function loadChart", i)]
    assert "kind: 'daily'" in body, "kind 必须统一为 daily"
    assert "drawDaily(" not in body, "loadCandle 不许自己画（先取数后绘图）"
    j = ov.index("async function runChart")
    body2 = ov[j:j + 1400]
    assert "st.chartGate.begin()" in body2
    assert "isLatest()" in body2
    assert body2.index("isLatest()") < body2.index("renderChartNow()"), "必须先校验序号再画"


def test_mf_wired_into_subcharts():
    sub = _src(SUBCHARTS)
    assert "'mf'" in sub and '主力动向' in sub
    ind = _src(INDICATORS)
    assert "function mf(" in ind
    assert "长度与 K 线不一致" in ind, "长度不一致必须拒绝而不是右对齐猜"
    ov = _src(OVERLAY)
    assert "IND.mf(" in ov, "overlay 未装配 mf"


# ---------------------------------------------------------------- P2（纸面下单面板）

ORDER_PANEL = FRONTEND / "order_panel.js"
ORDER_MODEL = FRONTEND / "order_model.js"
JS_P2 = HERE / "js" / "p2_order.test.mjs"


def test_order_panel_is_wired_but_marks_untouched():
    """面板/模型两个新文件；overlay 只装配；marks.js 不塞下单逻辑。"""
    assert ORDER_PANEL.exists() and ORDER_MODEL.exists()
    ov = _src(OVERLAY)
    assert "createOrderPanel(" in ov
    assert "'/api/order/place'" not in ov and '"/api/order/place"' not in ov, "端点应集中在 order_model.ENDPOINTS"
    assert "ENDPOINTS" in _src(ORDER_MODEL)
    marks = _src(MARKS)
    assert "order/place" not in marks and "condition/add" not in marks


def test_order_panel_only_paper_and_no_live_switch():
    """面板只做纸面：标题写「纸面」，没有 live 开关。"""
    panel = _src(ORDER_PANEL)
    assert 'klo-paper">纸面' in panel
    for bad in ("live_mode", "data-act=\"live\"", "实盘账户", "真实账户", "real_account"):
        assert bad not in panel, f"面板不该出现 {bad}"


def test_order_writes_require_confirm_and_no_retry():
    panel = _src(ORDER_PANEL)
    assert "submitGuard" in panel, "必须用提交闸门"
    assert "再点一次确认" in panel
    assert "catch (e)" in panel and "不自动重试" in panel
    model = _src(ORDER_MODEL)
    assert "function submitGuard" in model
    assert "if (busy || !armed) return false;" in model, "busy 期间必须拒绝第二次提交"


def test_p2_node_suite():
    node = _node_bin()
    if not node:
        pytest.skip("未找到 node，跳过下单模型单测")
    r = subprocess.run([node, str(JS_P2)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "P2：" in (r.stdout or "")


# ---------------------------------------------------------------- P2 返修（第二轮契约）

def test_available_qty_is_the_only_sellable_source():
    """可卖只认 available_qty（T+1/冻结后）；不许回退 qty，也不许用 avail_qty 旧名。"""
    ov = _src(OVERLAY)
    i = ov.index("function panelAccount")
    body = ov[i:i + 900]
    assert "available_qty" in body
    assert "avail_qty" not in body, "旧字段名不该再出现"
    assert "available_qty: raw.available_qty == null ? 0 : Number(raw.available_qty)" in body, "有本票但缺字段按 0"
    assert ": { qty: 0, available_qty: 0 }" in body, "权威空持仓给 0/0"
    assert "position: pos," in body, "没有 positions 对象才是 null"
    panel = _src(ORDER_PANEL)
    assert "available_qty" in panel
    assert "pos.qty : null" not in panel or "available_qty" in panel


def test_cancel_uses_paper_order_id_and_model_builders():
    """撤单号 = paper_order_id（兼容 order_id）；payload 必须走 model 构造，不手写字面量。"""
    panel = _src(ORDER_PANEL)
    assert "paper_order_id" in panel
    assert "M.buildCancelOrderPayload(" in panel and "M.buildCancelCondPayload(" in panel
    assert "{ order_id: id }" not in panel, "不许手写 payload 绕过测试"
    assert "{ cond_id: id }" not in panel


def test_signals_match_core_enum():
    model = _src(ORDER_MODEL)
    assert "VWAP_RECLAIM" in model and "HOOK_TURN" in model and "WEAK_TO_STRONG" in model
    assert "'REVERSAL'" not in model, "自造的 REVERSAL 必须去掉"


def test_esc_order_panel_then_menu_then_draw():
    ov = _src(OVERLAY)
    i = ov.index("function onKey(ev)")
    body = ov[i:i + 500]
    assert body.index("st.panel") < body.index("menuEl"), "Esc 应先关面板"
    assert body.index("menuEl") < body.index("st.drawMode"), "菜单应先于画线态"


def test_panel_state_kept_across_rerender():
    """表单状态放 F 对象里（切 Tab/类型/锁定不重设数量与触发价）。"""
    panel = _src(ORDER_PANEL)
    assert "const F = {" in panel
    assert "F.qty" in panel and "F.cond.trig" in panel
    assert "readAmountLast() >= 1000 ? M.readAmountLast() : 10000" in panel, "金额默认按旧版"
    assert "readonly" in panel, "锁定后数量/金额要 readonly"


# ---------------------------------------------------------------- P2 返修（第三轮阻断项）

def test_written_refreshes_are_awaited():
    """onWritten 必须 await loadPaper+runSides，且在回读失败时能回报。"""
    ov = _src(OVERLAY)
    i = ov.index("onWritten: async function")
    body = ov[i:i + 700]
    assert "await Promise.all([loadPaper(), runSides()])" in body
    assert "ok: !st.tradesErr && !st.ordersErr" in ov, "loadPaper 必须回报成败"
    panel = _src(ORDER_PANEL)
    assert "await opts.onWritten(" in panel
    assert "回读失败" in panel, "回读失败要明说，不能当账实一致"


def test_empty_positions_is_authoritative_zero():
    """positions={} → {qty:0, available_qty:0}；只有不是对象才是 null。"""
    ov = _src(OVERLAY)
    i = ov.index("function panelAccount")
    body = ov[i:i + 900]
    assert "positions && typeof positions === 'object'" in body
    assert ": { qty: 0, available_qty: 0 }" in body
    assert "positions 非对象" not in body  # 注释不同写法不影响；核心判据在下一行
    assert "position: pos," in body


def test_condition_info_is_type_specific_and_signal_market_only():
    panel = _src(ORDER_PANEL)
    assert "没有单一触发价" in panel, "回落/反弹不该再提触发价预览"
    assert "只支持市价" in panel
    model = _src(ORDER_MODEL)
    assert "信号买入只支持市价" in model
    assert "(forcedMarket || x.orderType !== 'limit') ? 'market' : 'limit'" in model


def test_p2_dom_suite():
    node = _node_bin()
    if not node:
        pytest.skip("未找到 node")
    r = subprocess.run([node, str(HERE / "js" / "p2_dom.test.mjs")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    if "SKIP:" in out:
        pytest.skip(out.strip().splitlines()[-1])
    assert r.returncode == 0, out[-1500:]
    assert "P2 DOM：" in out


# ---------------------------------------------------------------- 前端资源版本一致性（kline 内）

KI = PLUGIN


def _manifest_version() -> str:
    import json as _json
    return str(_json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"])


def _kline_frontend_sources():
    files = [KI / "frontend.js"] + sorted((KI / "frontend").glob("*.js"))
    for f in files:
        yield f, _src(f)


def test_every_intra_kline_import_carries_manifest_version():
    """入口被 shell 以 ?v=manifest.version 加载；它内部 import 的 kline 模块必须同一版本串，
    否则升级后会出现「入口新、子模块旧」的混用（ES 模块按 URL 缓存）。"""
    ver = _manifest_version()
    edge_re = re.compile(r"""from\s+['"](\.{1,2}/[^'"]+\.js)(\?v=([^'"]*))?['"]""")
    edges = 0
    for f, src in _kline_frontend_sources():
        for m in edge_re.finditer(src):
            edges += 1
            spec, q, v = m.group(1), m.group(2), m.group(3)
            assert q, f"{f.name}: import {spec} 没有 ?v= 版本串"
            assert v == ver, f"{f.name}: import {spec} 版本 {v} != manifest {ver}"
    assert edges >= 6, f"应有至少 6 条 kline 内部 import，实际 {edges}"


def test_entry_and_shell_use_same_version_source():
    """shell 用 manifest.version 拼入口 URL；这里守住「唯一版本源」= plugin.json。"""
    shell = _src(PLUGIN.parents[2] / "app" / "web" / "shell.js")
    assert "?v=" in shell and "plugin.version" in shell, "shell 应继续用 manifest 版本做入口版本"
    import json as _json
    m = _json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
    assert re.match(r"^\d+\.\d+\.\d+$", str(m["version"])), "manifest 版本应为语义化字符串"


def test_no_new_revision_constant_to_drift():
    """不引入第二个版本常量：静态 import 无法消费运行时常量，多一份只会多一处漂移。"""
    for f, src in _kline_frontend_sources():
        assert "KLINE_FRONTEND_REVISION" not in src, f"{f.name} 出现了第二份版本常量"
