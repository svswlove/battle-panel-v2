# -*- coding: utf-8 -*-
"""K 线插件 · 两类低吸条件单（回踩均线 / 关键K）的静态契约。

只读源码与枚举：不起 8810、不发请求。行为验证在
`tests/js/p2_order.test.mjs`（模型 payload）与 `tests/js/p2_dom.test.mjs`（面板 DOM 与真实点击）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLUGIN = Path(__file__).resolve().parents[1]
MODEL = PLUGIN / "frontend" / "order_model.js"
PANEL = PLUGIN / "frontend" / "order_panel.js"
ORDERS_PY = ROOT / "app" / "core" / "paper" / "orders.py"

MA = "ma_pullback_buy"
KK = "key_k_break"
NINE = ["stop_loss", "take_profit", "trailing_sell", "rebound_buy", "breakout_buy",
        "time_order", "breakeven_stop", "ma5_break", "signal_buy"]


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _branch(src: str, marker: str, end: str) -> str:
    i = src.index(marker)
    j = src.index(end, i)
    return src[i:j]


def test_two_keys_exist_on_both_sides():
    """前端 COND_TYPES 与 core COND_TYPES 用同一组 key，且都是买入方向。"""
    from app.core.paper.orders import COND_TYPES as BE
    model = _src(MODEL)
    for k, mode in ((MA, "ma_pullback"), (KK, "key_k")):
        assert "key: '%s'" % k in model, k
        assert k in BE and BE[k]["mode"] == mode
        assert BE[k]["side"] == "buy", k


def test_target_values_match_backend_allowed():
    """目标位枚举：均线 auto/yellow/white，关键K auto/upper/lower（与后端 allowed 逐字一致）。"""
    model = _src(MODEL)
    ma_block = _branch(model, "ma_pullback_buy: [", "],")
    kk_block = _branch(model, "key_k_break: [", "],")
    assert [v for v in ("'auto'", "'yellow'", "'white'") if v in ma_block] == ["'auto'", "'yellow'", "'white'"]
    assert "'upper'" not in ma_block and "'lower'" not in ma_block
    assert [v for v in ("'auto'", "'upper'", "'lower'") if v in kk_block] == ["'auto'", "'upper'", "'lower'"]
    assert "'yellow'" not in kk_block and "'white'" not in kk_block
    # 后端 add_condition 的 allowed 必须还是这两组（口径漂移时这条先断）
    orders = _src(ORDERS_PY)
    assert '("auto", "yellow", "white")' in orders
    assert '("auto", "upper", "lower")' in orders


def test_tol_range_and_default():
    """容差：小数占比 0.0001–0.05，默认 0.005（= 面板 0.5%）。"""
    model = _src(MODEL)
    assert "const TOL_MIN = 0.0001;" in model
    assert "const TOL_MAX = 0.05;" in model
    assert "const DEFAULT_TOL = 0.005;" in model
    assert "if (!tolOk(x.tol)) errors.push(" in model, "越界必须在前置校验里拦"
    orders = _src(ORDERS_PY)
    assert "tl <= 0 or tl > 0.05" in orders, "后端 0–5% 闸门仍是权威"


def test_payload_carries_target_and_tol_not_trigger_price():
    """提交体：低吸两类只带 target/tol；不许顺手写 trigger_price。"""
    model = _src(MODEL)
    blk = _branch(model, "} else if (spec.needs.indexOf('target') >= 0) {", "\n  } else {")
    assert "payload.target = t;" in blk
    assert "payload.tol = Math.round(tol * 10000) / 10000;" in blk
    assert "payload.trigger_price" not in blk, "低吸单没有固定触发价，别写 null 字段"
    panel = _src(PANEL)
    assert "target: c.target, tol: c.tol," in panel, "面板提交必须把目标位/容差传给 model"


def test_panel_renders_target_select_and_tol_percent_input():
    """面板：目标位下拉 + 容差（%），默认 0.5、范围 0–5；只在这两类出现。"""
    panel = _src(PANEL)
    assert 'id="klo-ctarget"' in panel
    assert 'id="klo-ctol"' in panel
    assert 'step="0.1" min="0" max="5"' in panel
    assert "function tolPctTxt(v)" in panel and "return '0.5';" in panel, "默认 0.5%"
    assert "F.cond.tol = v / 100;" in panel, "面板填百分数、model 收小数占比"
    assert "target: 'auto', tol: M.DEFAULT_TOL," in panel
    # 只有带 target 的类型才渲染这一组（九类表单不变）
    assert "(tgtOpts\n        ?" in panel or "(tgtOpts ?" in panel
    assert "function targetOpts(type) { return M.TARGET_OPTIONS[type] || null; }" in panel


def test_panel_top_hint_keeps_formula_wording():
    """顶部口径提示：黄线/白线公式与关键K定义，说明数据由 cond_watch 注入。"""
    panel = _src(PANEL)
    base = _branch(panel, "function baseLineHtml(type) {", "\n  }")
    assert "(MA14+MA28+MA57+MA114)/4" in base
    assert "EMA(EMA(C,10),10)" in base
    assert "放量阳线" in base and "上沿 = 收盘" in base and "下沿 = 开盘" in base
    assert "cond_watch" in base
    assert "id=\"klo-cbase\"" in panel


def test_nine_legacy_types_untouched():
    """九类的 label/hint 原文不动，且都不带 target。"""
    model = _src(MODEL)
    for key in NINE:
        assert "key: '%s'" % key in model
    for hint in ("现价跌到触发价 → 卖出", "现价涨到触发价 → 卖出", "自激活后最高价回落到幅度 → 卖出",
                 "自激活后最低价反弹到幅度 → 买入", "现价涨到触发价 → 买入",
                 "到点按设定方向委托", "浮盈达 1.5×ATR 后跌回成本 → 全部卖出",
                 "盘中破 MA5−0.3ATR 或 14:57 后破 MA5 → 卖出", "分钟 K 闭合命中信号 → 市价买入"):
        assert hint in model, hint
    # 九类的 needs 里没有 target
    for key in NINE:
        m = re.search(r"key: '%s'.*?needs: \[([^\]]*)\]" % key, model, re.S)
        assert m and "target" not in m.group(1), key
