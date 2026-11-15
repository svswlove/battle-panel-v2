# -*- coding: utf-8 -*-
"""paper 卡片前端静态检查（只读，不跑浏览器）。

守的是 docs/task_fe_batch2b.md §2/§3/§4 与 docs/paper_contract.md 的「卡片交互契约」：
  · 零 import / 零裸 fetch / 零裸 hex / 不写全局 / 不写盘 / 不引子进程
  · 读走 ctx.api.get 的绝对 /api/…（paper 插件 mount=root，无 /api/p/paper 前缀）
  · 写全部走 ctx.api.post，且写端点只有任务书 §2 点名的五条
  · 写操作纪律：模态确认弹层（四要素 + 纸面徽标）/ Enter 不触发 / 提交中 disabled /
    失败回执原样渲染（不重试）/ 读刷新只走 GET
  · 条件单九类 + 每类提示文案 + 信号五档 + 数量快捷档 + 数量锁（对旧 kline_order.js 逐字）
  · refreshEvery 20000；unmount 释放状态
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

# 旧 kline_order.js:33-37 COND_LABEL 逐字（键顺序也要一致）
OLD_COND_LABEL = [
    ("stop_loss", "止损卖出"), ("take_profit", "止盈卖出"), ("trailing_sell", "回落卖出"),
    ("rebound_buy", "反弹买入"), ("breakout_buy", "突破买入"), ("time_order", "定时委托"),
    ("breakeven_stop", "保本止损"), ("ma5_break", "破五日线卖出"), ("signal_buy", "信号买入"),
]
# 旧 kline_order.js:752-755 SIGNALS 逐字
OLD_SIGNALS = ["BREAKOUT", "PULLBACK", "VWAP_RECLAIM", "HOOK_TURN", "WEAK_TO_STRONG"]
# 任务书 §2.2/§2.3/§2.4 点名的五个写端点
WRITE_ENDPOINTS = [
    "/api/order/place", "/api/order/cancel",
    "/api/condition/add", "/api/condition/cancel", "/api/condition/evaluate?force=1",
]
READ_ENDPOINTS = ["/api/paper_account", "/api/order/list",
                  "/api/ledger?mode=paper", "/api/paper/feed/status"]


def _src() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 主题 / 依赖纪律


def test_no_raw_hex_colors() -> None:
    """颜色一律走 themes.css token，禁裸 hex。"""
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_uses_theme_tokens() -> None:
    src = _src()
    for token in ("var(--up)", "var(--down)", "var(--warn)", "var(--danger)", "var(--dim)",
                  "var(--accent)", "var(--card2)", "var(--danger-solid)"):
        assert token in src, "缺少主题 token %s" % token


def test_style_injected_once() -> None:
    src = _src()
    assert "paper-card-css" in src
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


def test_no_bare_fetch() -> None:
    """统一走 ctx.api（shell 的 makeCtx），不裸 fetch。"""
    assert "fetch(" not in _src(), "应统一走 ctx.api.get/post，不直接 fetch"


# ---------------------------------------------------------------- 端点


def test_read_endpoints_are_absolute_root_api() -> None:
    """paper 插件 mount=root：读端点必须是绝对 /api/…，不能是插件相对路径。"""
    calls = re.findall(r"""api\.get\(\s*['"]([^'"]+)['"]""", _src())
    assert calls, "找不到任何 api.get 调用"
    for path in calls:
        assert path.startswith("/api/"), "paper 插件是 root 挂载，读端点必须绝对 /api/：%s" % path
        assert not path.startswith("http"), "不得直连旧端：%s" % path
        assert "/api/p/paper" not in path, "root 挂载插件没有 /api/p/paper 前缀：%s" % path
    assert sorted(calls) == sorted(READ_ENDPOINTS), "读端点集合与任务书 §2 不一致：%s" % calls


def test_write_endpoints_are_the_five_declared() -> None:
    """写端点只有任务书点名的五条，且全部走 ctx.api.post。"""
    calls = re.findall(r"""api\.post\(\s*['"]([^'"]+)['"]""", _src())
    assert calls, "找不到任何 api.post 调用"
    for path in calls:
        assert path.startswith("/api/"), "写端点必须用绝对 /api/ 路径：%s" % path
    assert sorted(calls) == sorted(WRITE_ENDPOINTS), "写端点集合与任务书 §2 不一致：%s" % calls


def test_ledger_mode_is_paper_only() -> None:
    """任务书 §3 禁止项：ledger mode=paper 固定，本卡不出现 mode=live。"""
    src = _src()
    assert "/api/ledger?mode=paper" in src
    assert "mode=live" not in src, "本卡只读纸面账本"


def test_no_live_trading_wording() -> None:
    """任务书 §3：不出现任何实盘下单字样。"""
    src = _src()
    for bad in ("/api/order/place_live", "live_order", "xtquant", "xttrader", "qmt_trader"):
        assert bad not in src, "出现实盘/柜台字样：%s" % bad


# ---------------------------------------------------------------- 写操作纪律（§4）


def test_modal_confirm_layer_exists() -> None:
    """§4.2：模态弹层 = 遮罩 + 只有一个确认按钮。"""
    src = _src()
    assert "pp-mask" in src, "需要遮罩层"
    assert "aria-modal" in src and "setAttribute('role', 'dialog')" in src, "弹层需 aria-modal/role=dialog"
    assert "data-modal-ok" in src, "确认按钮唯一标识 data-modal-ok"
    assert src.count("data-modal-ok") == 1, "确认按钮必须只有一个"
    assert "data-modal-cancel" in src, "需要取消按钮"


def test_modal_reviews_four_elements() -> None:
    """§4.2：复核代码 / 方向 / 数量 / 价格四要素 + 红色「纸面」徽标。"""
    src = _src()
    for label in ("['代码'", "['方向'", "['数量'", "['价格'"):
        assert label in src, "确认弹层缺少四要素之一：%s" % label
    assert '<span class="pp-badge paper">纸面</span>' in src, "弹层内需带「纸面」徽标"
    assert ".pp-badge.paper{background:var(--danger-solid)" in src, "纸面徽标必须是红色（danger token）"


def test_enter_does_not_confirm() -> None:
    """§4.2：Enter 不触发确认（必须鼠标点击）。"""
    src = _src()
    m = re.search(r"function onModalKey|const onModalKey = \(ev\) =>", src)
    assert m, "需要模态键盘拦截器"
    seg = src[m.start():m.start() + 700]
    assert "ev.key === 'Enter'" in seg and "preventDefault" in seg, "Enter 必须被吞掉"
    assert "btnOk.click" not in src, "Enter 不得程序化点击确认按钮"
    assert "document.addEventListener('keydown', onModalKey, true)" in src, "弹层开着时需全局拦截"


def test_submitting_disables_button() -> None:
    """§4.3：提交中按钮 disabled + 「提交中…」，防双击重复下单。"""
    src = _src()
    assert re.search(r"if \(S\.submitting\) return;", src), "确认按钮需 S.submitting 防重入"
    assert "S.submitting = true;" in src
    assert "btnOk.disabled = true;" in src, "提交中确认按钮必须 disabled"
    assert "提交中…" in src, "提交中需显示进度文案"


def test_failure_receipt_rendered_verbatim() -> None:
    """§4.4：失败回执原样渲染，不吞 4xx/5xx，不重试写请求。"""
    src = _src()
    assert "errText(e)" in src
    for prefix in ("'下单失败：'", "'条件单提交失败：'", "'撤单失败（'", "'撤条件单失败（'", "'评估失败：'"):
        assert prefix in src, "缺少失败回执渲染：%s" % prefix
    assert "setStatus('下单失败：' + errText(e), true)" in src
    assert "重试" not in src or "不重试" in src, "写请求不得自动重试"
    assert not re.search(r"setTimeout\([^)]*api\.post", src), "不得用定时器重试写请求"


def test_read_refresh_is_get_only() -> None:
    """§4.5：刷新拉取只用 GET，写失败不影响读。"""
    src = _src()
    m = re.search(r"const load = async \(\) => \{([\s\S]*?)\n      \};", src)
    assert m, "找不到 load()"
    body = m.group(1)
    assert "api.post" not in body, "刷新路径里不得出现写请求"
    assert body.count("api.get(") == 4, "load() 应拉 4 个读端点"


def test_write_success_reloads_from_server() -> None:
    """§2.6：写成功后立即手动重拉（以服务端为准，不乐观更新）。"""
    src = _src()
    for fn in ("orderSubmit", "condSubmit", "cancelOrder", "cancelCond", "runEvaluate"):
        m = re.search(fn + r" = async[\s\S]*?\n      \};", src)
        assert m, "找不到 %s" % fn
        seg = m.group(0)
        assert "await load()" in seg, "%s 成功后必须重拉" % fn
        assert "S.html = Object.create(null)" in seg, "%s 重拉前需清防闪缓存" % fn


def test_inline_second_click_for_cancel_and_evaluate() -> None:
    """§3 禁止项：不自动下单 —— 撤单/立即评估用行内二次点击。"""
    src = _src()
    assert "data-arm" in src, "撤单/评估需要行内二次确认标记"
    assert "确认撤单？" in src and "确认评估？" in src


# ---------------------------------------------------------------- 条件单（对旧 kline_order.js）


def test_cond_label_matches_old() -> None:
    """九类条件单标签与顺序照旧 33-37。"""
    m = re.search(r"const COND_LABEL = \{([\s\S]*?)\n\};", _src())
    assert m, "找不到 COND_LABEL"
    got = re.findall(r"(\w+):\s*'([^']+)'", m.group(1))
    assert got == OLD_COND_LABEL, "COND_LABEL 与旧端不一致：%s" % got


def test_cond_hint_present_for_nine_types() -> None:
    """每类提示文案照旧 38-48（至少点名的关键句要在）。"""
    src = _src()
    assert "现价跌到触发价 → 卖出" in src
    assert "浮盈达 1.5×ATR 后跌回成本 → 全部卖出（QMT 止损四层第①层）" in src
    assert "盘中破 MA5−0.3ATR 或 14:57 后破 MA5 → 卖出" in src
    assert "分钟 K 闭合命中信号 → 市价买入" in src
    m = re.search(r"const COND_HINT = \{([\s\S]*?)\n\};", src)
    assert m
    keys = re.findall(r"(\w+):\s*'", m.group(1))
    assert keys == [k for k, _ in OLD_COND_LABEL], "COND_HINT 键与 COND_LABEL 必须一一对应：%s" % keys


def test_signals_match_old() -> None:
    m = re.search(r"const SIGNALS = \[([\s\S]*?)\n\];", _src())
    assert m, "找不到 SIGNALS"
    got = re.findall(r"\['([A-Z_]+)'", m.group(1))
    assert got == OLD_SIGNALS, "SIGNALS 与旧端不一致：%s" % got


def test_cond_fields_per_type() -> None:
    """每类专属字段照旧 670-698：定时(at/side)、回落反弹(pct)、信号(sig)、线类(提示)、普通(触发价+ATR 缓冲)。"""
    src = _src()
    for marker in ('data-cond="at"', 'data-cond="pct"', 'data-cond="sig"',
                   'data-cond="trig"', 'data-cond="linehint"', "data-segbox", "cbuf"):
        assert marker in src, "条件单表单缺少字段：%s" % marker
    assert "payload.buffer_atr = 0.3" in src, "保本/破五日线固定 0.3×ATR（旧 1183 同口径）"
    assert "payload.valid_until" in src
    assert "payload.group = 'OCO-'" in src, "OCO 二选一 group 口径照旧 1196"


def test_signal_buy_does_not_promise_trigger() -> None:
    """§2.4：signal_buy 可选但触发依赖信号库，卡上不承诺必触发。"""
    src = _src()
    assert "本卡不承诺必触发" in src


def test_evaluate_reports_counts() -> None:
    """§2.4：立即评估如实显示 triggered/cancelled/expired 计数（quotes 空属正常）。"""
    src = _src()
    assert "评估完成：触发 ' + n('triggered')" in src
    assert "n('cancelled')" in src and "n('expired')" in src and "n('filled')" in src
    assert "无 feed 时行情为空属正常" in src


# ---------------------------------------------------------------- 下单表单


def test_qty_quick_levels_match_old() -> None:
    """§2.2：数量快捷档照旧 593-594（1/4、1/3、1/2、全部）。"""
    m = re.search(r"const QTY_QUICK = \[([\s\S]*?)\];", _src())
    assert m, "找不到 QTY_QUICK"
    got = re.findall(r"\['([\d.]+)', '([^']+)'\]", m.group(1))
    assert got == [('0.25', '1/4'), ('0.3333', '1/3'), ('0.5', '1/2'), ('1', '全部')], got


def test_qty_lock_only_for_buy() -> None:
    """§2.2：数量锁（旧 562 只对买入生效，卖出永远可改）。"""
    src = _src()
    assert "数量锁" in src
    assert "segVal('side') !== 'buy'" in src
    assert "inpQty.readOnly = locked" in src


def test_market_order_hides_price() -> None:
    """§2.2：市价单隐藏价格框。"""
    src = _src()
    assert "rowPrice.hidden = market" in src
    assert "inpPrice.hidden = market" in src


def test_place_body_matches_old() -> None:
    """下单 body 与旧 1143-1147 同形：code/side/qty/price/order_type/name/note。"""
    src = _src()
    m = re.search(r"api\.post\('/api/order/place',\s*\{([\s\S]*?)\}\)", src)
    assert m, "找不到下单 body"
    body = m.group(1)
    for key in ("code:", "side:", "qty:", "price:", "order_type:", "name:", "note:"):
        assert key in body, "下单 body 缺字段 %s" % key
    assert "note: NOTE_TAG" in src


def test_account_404_is_empty_state() -> None:
    """§2.1：无账本时 404 → 「纸面账本未生成（尚无纸面成交）」空态（与旧口径一致）。"""
    src = _src()
    assert "纸面账本未生成（尚无纸面成交）" in src
    assert "__status === 404" in src


def test_orders_and_conditions_tables() -> None:
    """§2.3：委托表（时间/代码/方向/数量/价格/状态/来源）+ 条件单表（类型/触发参数/状态/有效期）。"""
    src = _src()
    assert "const heads = ['时间', '代码 / 名称', '方向', '数量', '价格', '状态', '来源', '操作']" in src
    assert "const heads = ['类型', '代码 / 名称', '触发参数', '数量', '状态', '有效期', '操作']" in src
    assert "data-cancel-order=" in src and "data-cancel-cond=" in src


def test_frozen_qty_from_ledger() -> None:
    """§2.3：挂单冻结股数取自 /api/ledger 的 frozen_by_code。"""
    src = _src()
    assert "frozen_by_code" in src
    assert "r.frozen_qty" in src


def test_feed_status_is_expected_not_fault() -> None:
    """§2.5：行情源未启动显示「行情未连」黄色提示——预期状态，不是故障。"""
    src = _src()
    assert "行情未连" in src and "行情已连" in src
    assert "行情源未启动属预期状态（不是故障）" in src
    assert "pp-badge ' + (connected ? 'ok' : 'warn')" in src


# ---------------------------------------------------------------- 刷新 / 生命周期


def test_refresh_every_declared() -> None:
    """§2.6：refreshEvery 20000。"""
    src = _src()
    assert re.search(r"^\s*refreshEvery:\s*20000,", src, re.M)
    assert "async refresh()" in src


def test_refresh_guarded() -> None:
    """刷新需在 unmount 后 / 请求在途 / 提交中 / 弹层开着时跳过。"""
    m = re.search(r"async refresh\(\)\s*\{([\s\S]*?)\n    \},", _src())
    assert m, "找不到 refresh()"
    body = m.group(1)
    for token in ("S.alive", "S.loading", "S.submitting", "S.modal"):
        assert token in body, "refresh 缺少守卫 %s" % token


def test_no_flicker_set_html_cache() -> None:
    src = _src()
    assert re.search(r"if \(S\.html\[key\] === html\) return;", src)


def test_event_delegation_not_inline_onclick() -> None:
    """无构建无框架：按钮随数据重绘，监听挂稳定的 wrap 上（不用内联 onclick）。"""
    src = _src()
    assert "wrap.addEventListener('click'" in src
    assert "onclick=" not in src, "不得使用内联 onclick（CSP/注入面）"


def test_unmount_releases_state() -> None:
    m = re.search(r"async unmount\(\)\s*\{([\s\S]*?)\n    \},", _src())
    assert m, "找不到 unmount()"
    body = m.group(1)
    assert "S.alive = false" in body
    assert "clearTimeout(S.toastTimer)" in body
    assert "this._pp = null" in body
    assert "removeEventListener('keydown'" in body, "弹层键盘监听必须解绑"


# ---------------------------------------------------------------- 卡片声明 / manifest


def test_manifest_bumped_with_card() -> None:
    """版本号即前端缓存键；2026-10-28 补个股点击属性后再升一档。"""
    data = _manifest()
    assert data["version"] == "0.2.2"
    assert data["frontend"]["module"] == "frontend.js"
    cards = data["frontend"]["cards"]
    assert [c["id"] for c in cards] == ["paper.main"]
    assert cards[0]["slot"] == "main"
    assert cards[0]["order"] == 45
    assert cards[0]["title"] == "模拟交易"   # 2026-11-07：纸面交易 → 模拟交易


def test_manifest_backend_untouched() -> None:
    """§3 禁止项：backend.py / mount / 权限不动。"""
    data = _manifest()
    assert data["backend"]["mount"] == "root"
    assert data["backend"]["module"] == "backend"
    assert data["data"]["write"] == []
    assert data["data"]["paper"] is True


def test_frontend_card_matches_manifest() -> None:
    src = _src()
    assert "export default" in src
    assert re.search(r"id:\s*'paper\.main'", src)
    assert re.search(r"order:\s*45", src)
    assert "mount(" in src and "unmount(" in src and "refresh(" in src
