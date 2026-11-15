# -*- coding: utf-8 -*-
"""app/core/paper/orders.py 单测（4.1.2 移植）：手工单 / 条件单 / OCO / 冻结 / 急停 / T+1

【来源】旧 engine/tests/test_manual_orders.py（2026-10-23 任务书 4.1.2 移植）。
断言原样保留；改动只有三处：
  1. 路径全部走 state_dir=tmp_path（临时目录），**绝不写真实 data/panel_v2/paper**；
  2. import 由 core.manual_orders → app.core.paper.orders（PaperOrderService），
     ManualOrderError 走兼容别名（旧测试/调用方仍能 import）；
  3. 只测实盘通道的 3 个用例改成 pytest.mark.skip（实盘通道整类删除，见 orders.py 头）。

覆盖：模式闸门 / 数量与资金与持仓校验 / 涨跌停 / 限价即时成交与挂单补成交 / 撤单 /
条件单 9 类触发 / OCO 互斥 / 行情新鲜度 / 有效期过期 / T+1 可卖近似 / 冻结 / 价格笼子。
全部在临时目录跑，不碰生产 state；本文件也不裸 open(...,"w")（走 state 读写）。
"""
from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paper import state  # noqa: E402
from app.core.paper.broker import PaperBroker, calc_fee  # noqa: E402
from app.core.paper.orders import (  # noqa: E402
    COND_TYPES,
    MIN_LOT,
    ManualOrderError,
    PaperOrderError,
    PaperOrderService,
    manual_locked,
    mark_manual_lock,
)

# 固定行情：现价 10.00，涨跌停 11.00/9.00（单测确定性，零网络）
QUOTE = {"last": 10.00, "pre_close": 10.00, "limit_up": 11.00, "limit_down": 9.00,
         "quote_time": "10:30:00", "fresh": True}


class OrdersTestBase:
    """公共夹具：临时账本目录 + 注入 session_ok=True（真实时段另有专测）。"""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tmp = tmp_path / "paper"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.paths = state.get_paths(self.tmp)
        self.broker = PaperBroker(state_dir=self.tmp, initial_cash=100000.0)
        self.state = self.paths.engine_state
        self._write_state("paper")
        self.svc = PaperOrderService(
            broker=self.broker,
            state_dir=self.tmp,
            engine_state_path=self.paths.engine_state,
            kill_switch_path=self.paths.kill_switch,
            freeze_path=self.paths.freeze,
            session_ok=lambda: True)      # 单测固定为"交易时段"（真实时段另有专测）

    def _write_state(self, mode, **extra):
        """写 engine_state.json（走 state 原子写，不裸 open）。"""
        doc = {"trade_mode": mode}
        doc.update(extra)
        state.atomic_write_json(self.state, doc, audit_event="test.engine_state")

    def _new_svc(self, *, cond_name: str, session_ok=lambda: True):
        """换一份条件单文件的服务（同一账本，用例之间条件单互不干扰）。"""
        return PaperOrderService(
            broker=self.broker,
            state_dir=self.tmp,
            cond_path=self.tmp / cond_name,
            engine_state_path=self.paths.engine_state,
            kill_switch_path=self.paths.kill_switch,
            freeze_path=self.paths.freeze,
            session_ok=session_ok)

    # ---------------- 工具 ----------------
    def _backdate_trades(self):
        """把纸面成交的 created_at 改到昨天，绕过 T+1 近似（仅测试用，走 state 原子写）。"""
        doc = state.read_json(self.paths.trades, {})
        rows = doc if isinstance(doc, list) else (doc or {}).get("trades") or []
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        for r in rows:
            if isinstance(r, dict) and r.get("created_at"):
                r["created_at"] = yesterday + str(r["created_at"])[10:]
        state.atomic_write_json(self.paths.trades, doc, audit_event="test.backdate_trades")
        # 同步把账本的"当日买入"结算掉（否则 T+1 仍会拦住卖出）
        try:
            self.broker.roll_day()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 校验矩阵
class TestValidation(OrdersTestBase):
    def test_buy_qty_must_be_lot(self):
        with pytest.raises(ManualOrderError):
            self.svc.place("000001.SZ", "buy", 150, 10.0, quote=QUOTE)

    def test_buy_insufficient_cash(self):
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "buy", 20000, 10.0, quote=QUOTE)
        assert cm.value.code == "no_cash"

    def test_price_above_limit_up(self):
        with pytest.raises(ManualOrderError):
            self.svc.place("000001.SZ", "buy", 100, 11.5, quote=QUOTE)

    def test_sell_without_position(self):
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "sell", 100, 10.0, quote=QUOTE)
        assert cm.value.code == "no_position"

    def test_no_quote(self):
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "buy", 100, 10.0, quote={})
        assert cm.value.code == "no_quote"

    def test_kill_switch_blocks_orders_and_conditions(self):
        state.atomic_write_json(self.paths.kill_switch,
                                {"active": True, "reason": "测试急停"},
                                audit_event="test.kill_switch")
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "buy", 100, 10.0, quote=QUOTE)
        assert cm.value.code == "killed"
        with pytest.raises(ManualOrderError) as cm2:
            self.svc.add_condition("000001.SZ", "breakout_buy", 100, trigger_price=10.5, quote=QUOTE)
        assert cm2.value.code == "killed"

    def test_kill_switch_missing_means_not_killed(self):
        """kill_switch.json 不存在 = 未急停（4.1.2 显式口径）。"""
        assert not Path(self.paths.kill_switch).exists()
        res = self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        assert res["order"]["status"] == "FILLED"


# ---------------------------------------------------------------- 成交与挂单
class TestPlaceAndMatch(OrdersTestBase):
    def test_limit_buy_fills_and_books(self):
        res = self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        order = res["order"]
        assert order["status"] == "FILLED"
        assert order["filled_qty"] == 1000
        assert round(order["filled_price"], 3) == 10.0
        acct = self.svc.account()
        assert round(float(acct["cash"]), 2) == round(90000.0 - calc_fee(10000.0, "BUY"), 2)
        assert int(acct["positions"]["000001.SZ"]["qty"]) == 1000

    def test_limit_buy_below_market_rests_then_matches(self):
        res = self.svc.place("000001.SZ", "buy", 100, 9.50, quote=QUOTE)
        assert res["order"]["status"] == "REPORTED"
        # 价格没到 → 不成交
        self.svc.match_pending({"000001.SZ": {"last": 9.80}})
        oid = res["order"]["paper_order_id"]
        assert self.broker.get_trade(oid)["status"] == "REPORTED"
        # 价格下穿 → 补成交
        filled = self.svc.match_pending({"000001.SZ": {"last": 9.45}})
        assert len(filled) == 1
        rec = self.broker.get_trade(oid)
        assert rec["status"] == "FILLED"
        # 价格跌穿限价时按更优价成交（纸面口径：min(最新价, 限价)）
        assert round(rec["filled_price"], 3) == 9.45

    def test_cancel_pending_and_reject_filled_cancel(self):
        res = self.svc.place("000001.SZ", "buy", 100, 9.50, quote=QUOTE)
        oid = res["order"]["paper_order_id"]
        self.svc.cancel_order(oid)
        assert self.broker.get_trade(oid)["status"] == "CANCELLED"
        with pytest.raises(ManualOrderError):
            self.svc.cancel_order(oid)

    def test_t1_available_qty(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        assert self.svc.available_qty("000001.SZ") == 0
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "sell", 1000, 10.2, quote=QUOTE)
        assert cm.value.code == "no_position"

    def test_t1_blocks_same_day_sell(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "sell", 1000, 10.2, quote=QUOTE)
        assert cm.value.code == "no_position"
        self._backdate_trades()          # 模拟隔日：成交日变为昨天 → T+1 解锁
        assert self.svc.available_qty("000001.SZ") == 1000
        res = self.svc.place("000001.SZ", "sell", 1000, 9.90, quote=QUOTE)   # ≤现价，可成交
        assert res["order"]["status"] == "FILLED"

    def test_soft_cap_warning_only(self):
        res = self.svc.place("000001.SZ", "buy", 2000, 10.0, quote=QUOTE)
        assert res["order"]["status"] == "FILLED"
        assert any("10%" in w for w in res["warnings"])


# ---------------------------------------------------------------- 冻结 / 价格笼子 / 逐档部分成交
class TestFreezeAndCage(OrdersTestBase):
    def test_resting_buy_freezes_cash_and_cancel_releases(self):
        before = self.svc.available_cash()
        res = self.svc.place("000001.SZ", "buy", 1000, 9.00, quote=QUOTE)
        assert res["order"]["status"] == "REPORTED"
        assert round(self.svc.available_cash(), 2) == round(before - 9000.0, 2)
        assert round(self.svc.frozen_cash(), 2) == 9000.0
        self.svc.cancel_order(res["order"]["paper_order_id"])
        assert round(self.svc.available_cash(), 2) == round(before, 2)
        assert self.svc.frozen_cash() == 0.0

    def test_price_cage_rejects_far_limit(self):
        q = dict(QUOTE)
        q["asks"] = [{"price": 10.00, "vol": 10}]
        q["bids"] = [{"price": 9.99, "vol": 10}]
        with pytest.raises(ManualOrderError) as cm:
            self.svc.place("000001.SZ", "buy", 100, 10.50, quote=q)   # 10.50 > 10.20
        assert cm.value.code == "price_cage"
        with pytest.raises(ManualOrderError) as cm2:
            self.svc.place("000001.SZ", "sell", 100, 9.50, quote=q)   # 9.50 < 9.79
        assert cm2.value.code == "price_cage"

    def test_partial_fill_by_level_volume(self):
        q = dict(QUOTE)
        q["asks"] = [{"price": 10.00, "vol": 2}]      # 卖一只有 2 手 = 200 股
        q["bids"] = [{"price": 10.00, "vol": 5}]
        res = self.svc.place("000001.SZ", "buy", 500, 10.00, quote=q)
        assert res["order"]["status"] == "PARTIAL"
        assert res["order"]["filled_qty"] == 200
        assert round(self.svc.frozen_cash(), 2) == round(300 * 10.0, 2)

    def test_day_rollover_expires_resting_orders(self):
        res = self.svc.place("000001.SZ", "buy", 100, 9.00, quote=QUOTE)
        oid = res["order"]["paper_order_id"]
        assert res["order"]["status"] == "REPORTED"
        assert round(self.svc.frozen_cash(), 2) == 900.0
        self._backdate_trades()          # 模拟隔日：委托创建日变成昨天
        self.svc.list_orders()           # 触发日切清理
        assert self.broker.get_trade(oid)["status"] == "CANCELLED"
        assert self.svc.frozen_cash() == 0.0


# ---------------------------------------------------------------- 条件单
class TestConditions(OrdersTestBase):
    def test_stop_loss_triggers_sell(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        # 绕过 T+1：把当日买入记录改成昨天，模拟隔日持仓
        self._backdate_trades()
        assert self.svc.available_qty("000001.SZ") == 1000
        c = self.svc.add_condition("000001.SZ", "stop_loss", 1000, trigger_price=9.60,
                                   order_type="market", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 9.70}})
        assert out["triggered"] == []
        out = self.svc.evaluate({"000001.SZ": {"last": 9.55}})
        assert len(out["triggered"]) == 1
        assert out["triggered"][0]["status"] == "TRIGGERED"
        assert out["triggered"][0]["order_id"]
        assert self.svc.list_conditions(status="TRIGGERED")[0]["cond_id"] == c["cond_id"]

    def test_cond_sell_on_t1_allowed_but_not_triggered(self):
        """2026-10-21 主人：当日买入也能先挂止损，今天不触发（可卖 0），隔日解锁后自动触发。"""
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        assert self.svc.available_qty("000001.SZ") == 0     # T+1
        c = self.svc.add_condition("000001.SZ", "stop_loss", 1000, trigger_price=9.60,
                                   order_type="market", quote=QUOTE)
        assert c["status"] == "ACTIVE"
        assert c["t1_locked"]
        out = self.svc.evaluate({"000001.SZ": {"last": 9.50}})
        assert out["triggered"] == []
        assert self.svc.list_conditions(status="ACTIVE")[0]["blocked"] == "t1"
        self._backdate_trades()                                      # 隔日：T+1 解锁
        out2 = self.svc.evaluate({"000001.SZ": {"last": 9.50}})
        assert len(out2["triggered"]) == 1

    def test_cond_partial_t1_sells_available(self):
        """300 股中 200 是当日买入：止损 300 触发时按可卖 100 成交（2026-10-21 主人：能卖多少卖多少）。"""
        self.svc.place("000001.SZ", "buy", 100, 10.0, quote=QUOTE)
        self._backdate_trades()                                   # 100 股隔日，可卖
        self.svc.place("000001.SZ", "buy", 200, 10.0, quote=QUOTE)  # 200 股当日，未解锁
        assert self.svc.available_qty("000001.SZ") == 100
        c = self.svc.add_condition("000001.SZ", "stop_loss", 300, trigger_price=9.60,
                                   order_type="market", quote=QUOTE)
        assert c["t1_locked"]
        out = self.svc.evaluate({"000001.SZ": {"last": 9.50}})
        assert len(out["triggered"]) == 1
        t = out["triggered"][0]
        assert t["status"] == "TRIGGERED"
        assert t["triggered_qty"] == 100
        od = [o for o in self.svc.list_orders() if o["paper_order_id"] == t["order_id"]][0]
        assert int(od["qty"]) == 100

    def test_breakeven_stop_arms_then_triggers(self):
        """保本止损（旧系统止损四层第①层）：浮盈达 1.5×ATR 后跌回成本 → 全平。"""
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        c = self.svc.add_condition("000001.SZ", "breakeven_stop", 1000,
                                   quote={**QUOTE, "atr14": 0.2})
        assert c["status"] == "ACTIVE"
        assert c["armed"] is False
        # 浮盈 0.40 ≥ 1.5×0.2=0.30 → armed（不触发）
        out = self.svc.evaluate({"000001.SZ": {"last": 10.40, "atr14": 0.2, "fresh": True}},
                                now_hm="10:30")
        assert out["triggered"] == []
        # 跌回成本 → 触发
        out2 = self.svc.evaluate({"000001.SZ": {"last": 9.98, "atr14": 0.2, "fresh": True}},
                                 now_hm="10:35")
        assert len(out2["triggered"]) == 1

    def test_ma5_break_intraday_buffer_and_close(self):
        """破五日线：盘中需跌破 MA5−0.3ATR；14:57 后跌破 MA5 即卖。"""
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        self.svc.add_condition("000001.SZ", "ma5_break", 1000,
                               quote={**QUOTE, "ma5": 10.5, "atr14": 0.2})
        q = {"last": 10.46, "ma5": 10.5, "atr14": 0.2, "fresh": True}   # 10.5−0.06=10.44
        assert self.svc.evaluate({"000001.SZ": q}, now_hm="10:30")["triggered"] == []
        q2 = {"last": 10.42, "ma5": 10.5, "atr14": 0.2, "fresh": True}
        assert len(self.svc.evaluate({"000001.SZ": q2}, now_hm="10:30")["triggered"]) == 1

    def test_ma5_break_close_channel(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        self.svc.add_condition("000001.SZ", "ma5_break", 1000,
                               quote={**QUOTE, "ma5": 10.5, "atr14": 0.2})
        q = {"last": 10.46, "ma5": 10.5, "atr14": 0.2, "fresh": True}
        assert len(self.svc.evaluate({"000001.SZ": q}, now_hm="14:58")["triggered"]) == 1

    def test_ma_pullback_buy_auto_line(self):
        """回踩均线（2026-10-28）：多头排列白>黄，价在白线上方 → 目标白线；触及 0.5% 内触发。"""
        self.svc.add_condition("000001.SZ", "ma_pullback_buy", 100,
                               target="auto", quote={**QUOTE, "white": 10.2, "yellow": 9.8})
        q = {"last": 10.30, "white": 10.2, "yellow": 9.8, "fresh": True}
        assert self.svc.evaluate({"000001.SZ": q}, now_hm="10:30")["triggered"] == []
        q2 = {"last": 10.20, "white": 10.2, "yellow": 9.8, "fresh": True}
        assert len(self.svc.evaluate({"000001.SZ": q2}, now_hm="10:30")["triggered"]) == 1

    def test_key_k_break_auto_level(self):
        """关键K（2026-10-28）：价在上沿上方 → 目标上沿（收盘）；跌到上沿 0.5% 内触发。"""
        self.svc.add_condition("000001.SZ", "key_k_break", 100,
                               target="auto", quote={**QUOTE, "ek_open": 9.9, "ek_close": 10.1})
        q = {"last": 10.20, "ek_open": 9.9, "ek_close": 10.1, "fresh": True}
        assert self.svc.evaluate({"000001.SZ": q}, now_hm="10:30")["triggered"] == []
        q2 = {"last": 10.05, "ek_open": 9.9, "ek_close": 10.1, "fresh": True}
        assert len(self.svc.evaluate({"000001.SZ": q2}, now_hm="10:30")["triggered"]) == 1

    def test_new_cond_target_validation(self):
        """目标位/容差校验：非法 target、tol 越界 → 拒绝。"""
        with pytest.raises(PaperOrderError):
            self.svc.add_condition("000001.SZ", "ma_pullback_buy", 100, target="ma5", quote=QUOTE)
        with pytest.raises(PaperOrderError):
            self.svc.add_condition("000001.SZ", "key_k_break", 100, target="auto", tol=0.2, quote=QUOTE)

    def test_stop_loss_atr_buffer(self):
        """ATR 缓冲（2026-10-21）：止损需破 0.3×ATR 才触发，带内算毛刺。"""
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        self.svc.add_condition("000001.SZ", "stop_loss", 1000, trigger_price=9.80,
                               buffer_atr=0.3, quote={**QUOTE, "atr14": 0.20})
        # 9.75 > 9.80−0.06 → 不触发
        assert self.svc.evaluate({"000001.SZ": {"last": 9.75, "atr14": 0.20, "fresh": True}},
                                 now_hm="10:30")["triggered"] == []
        # 9.73 ≤ 9.74 → 触发
        assert len(self.svc.evaluate({"000001.SZ": {"last": 9.73, "atr14": 0.20, "fresh": True}},
                                     now_hm="10:30")["triggered"]) == 1

    def test_stop_loss_atr_buffer_tail_verify(self):
        """勾 ATR 后：盘中缓冲带内不卖；14:55 后仍在止损线下方 → 卖（尾盘验证）。"""
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        self.svc.add_condition("000001.SZ", "stop_loss", 1000, trigger_price=9.80,
                               buffer_atr=0.3, quote={**QUOTE, "atr14": 0.20})
        # 盘中 9.78（在 9.74~9.80 缓冲带内）→ 不卖
        assert self.svc.evaluate({"000001.SZ": {"last": 9.78, "atr14": 0.20, "fresh": True}},
                                 now_hm="13:30")["triggered"] == []
        # 14:56 仍破 9.80 → 卖
        assert len(self.svc.evaluate({"000001.SZ": {"last": 9.78, "atr14": 0.20, "fresh": True}},
                                     now_hm="14:56")["triggered"]) == 1

    def test_same_type_line_replaces_old(self):
        """止盈/止损线只能各一条：新建同类型自动作废旧单。"""
        self.svc.place("000001.SZ", "buy", 100, 10.0, quote=QUOTE)
        a = self.svc.add_condition("000001.SZ", "stop_loss", 100, trigger_price=9.6, quote=QUOTE)
        self.svc.add_condition("000001.SZ", "stop_loss", 100, trigger_price=9.4, quote=QUOTE)
        actives = [c for c in self.svc.list_conditions(status="ACTIVE") if c["type"] == "stop_loss"]
        assert len(actives) == 1
        old = [c for c in self.svc.list_conditions() if c["cond_id"] == a["cond_id"]][0]
        assert old["status"] == "CANCELLED"
        assert old["reason"] == "replaced_by_new_line"

    def test_long_term_flag(self):
        """有效期≥10 天 → long_term=True（临期提醒的前提）。"""
        self.svc.place("000001.SZ", "buy", 100, 10.0, quote=QUOTE)
        d20 = (_dt.date.today() + _dt.timedelta(days=20)).strftime("%Y%m%d")
        c = self.svc.add_condition("000001.SZ", "stop_loss", 100, trigger_price=9.6,
                                   valid_until=d20, quote=QUOTE)
        assert c["long_term"]
        assert c["days_left"] >= 19

    def test_signal_buy_uses_provider(self):
        """信号买入：命中由装配方注入的 provider 决定（分钟K口径），命中即下市价买单。"""
        seen = []
        self.svc.signal_provider = lambda code, name: (seen.append(name), True)[1]
        c = self.svc.add_condition("000001.SZ", "signal_buy", 100, signal="BREAKOUT", quote=QUOTE)
        assert c["status"] == "ACTIVE"
        out = self.svc.evaluate({"000001.SZ": {"last": 10.0, "fresh": True}})
        assert len(out["triggered"]) == 1
        assert seen[0] == "BREAKOUT"

    def test_signal_buy_without_provider_never_triggers(self):
        """未注入 provider → 信号类条件单永不触发（不编造信号）。"""
        self.svc.signal_provider = None
        self.svc.add_condition("000001.SZ", "signal_buy", 100, signal="BREAKOUT", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.0, "fresh": True}})
        assert out["triggered"] == []

    def test_signal_buy_rejects_unknown_name(self):
        with pytest.raises(Exception) as cm:
            self.svc.add_condition("000001.SZ", "signal_buy", 100, signal="NOPE", quote=QUOTE)
        assert "未知信号" in str(cm.value)

    def test_take_profit_triggers(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        self.svc.add_condition("000001.SZ", "take_profit", 1000, trigger_price=10.80,
                               order_type="market", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.85}})
        assert len(out["triggered"]) == 1

    def test_trailing_sell_uses_peak(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        cond = self.svc.add_condition("000001.SZ", "trailing_sell", 1000, pct=2.0,
                                      order_type="market", quote={"last": 10.0})
        self.svc.evaluate({"000001.SZ": {"last": 10.50}})   # 峰值抬到 10.50
        mid = self.svc.list_conditions()[0]
        assert round(mid["peak"], 3) == 10.50
        out = self.svc.evaluate({"000001.SZ": {"last": 10.28}})  # 10.50*0.98=10.29
        assert len(out["triggered"]) == 1
        assert cond["cond_id"] == out["triggered"][0]["cond_id"]

    def test_rebound_buy_uses_trough(self):
        cond = self.svc.add_condition("000001.SZ", "rebound_buy", 500, pct=1.0,
                                      order_type="market", quote={"last": 10.0})
        self.svc.evaluate({"000001.SZ": {"last": 9.80}})
        assert round(self.svc.list_conditions()[0]["trough"], 3) == 9.80
        out = self.svc.evaluate({"000001.SZ": {"last": 9.90}})
        assert len(out["triggered"]) == 1
        assert cond["cond_id"] == out["triggered"][0]["cond_id"]

    def test_breakout_buy(self):
        self.svc.add_condition("000001.SZ", "breakout_buy", 300, trigger_price=10.50,
                               order_type="limit", limit_price=10.55, quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.45}})
        assert out["triggered"] == []
        out = self.svc.evaluate({"000001.SZ": {"last": 10.56}})
        assert len(out["triggered"]) == 1
        order = self.broker.get_trade(out["triggered"][0]["order_id"])
        assert order["side"] == "BUY"
        assert round(order["limit_price"], 3) == 10.55

    def test_time_order(self):
        self.svc.add_condition("000001.SZ", "time_order", 200, at="14:55", side="buy",
                               order_type="market", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.0}}, now_hm="14:50")
        assert out["triggered"] == []
        out = self.svc.evaluate({"000001.SZ": {"last": 10.0}}, now_hm="14:56")
        assert len(out["triggered"]) == 1

    def test_oco_sibling_cancelled(self):
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        self._backdate_trades()
        g = "GRP1"
        self.svc.add_condition("000001.SZ", "stop_loss", 1000, trigger_price=9.5,
                               order_type="market", group=g, quote=QUOTE)
        self.svc.add_condition("000001.SZ", "take_profit", 1000, trigger_price=10.5,
                               order_type="market", group=g, quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 9.4}})
        assert len(out["triggered"]) == 1
        assert len(out["cancelled"]) == 1
        assert out["cancelled"][0]["reason"] == "oco_sibling"
        assert self.svc.list_conditions(status="CANCELLED")[0]["type"] == "take_profit"

    def test_stale_quote_skipped_unless_force(self):
        self.svc.add_condition("000001.SZ", "breakout_buy", 100, trigger_price=10.5,
                               order_type="market", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.9, "fresh": False}})
        assert out["triggered"] == []
        out = self.svc.evaluate({"000001.SZ": {"last": 10.9, "fresh": False}}, force=True)
        assert len(out["triggered"]) == 1

    def test_expired_condition(self):
        self.svc.add_condition("000001.SZ", "breakout_buy", 100, trigger_price=10.5,
                               order_type="market", valid_until="20200101", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.9}})
        assert out["triggered"] == []
        assert len(out["expired"]) == 1
        assert self.svc.list_conditions()[0]["status"] == "EXPIRED"

    def test_condition_failure_marks_failed(self):
        # 现金不足：触发后下单失败 → FAILED，不静默丢单
        self.svc.add_condition("000001.SZ", "breakout_buy", 100000, trigger_price=10.5,
                               order_type="market", quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.9}})
        assert out["triggered"][0]["status"] == "FAILED"
        assert "no_cash" in out["triggered"][0]["reason"]

    def test_cancel_condition(self):
        c = self.svc.add_condition("000001.SZ", "breakout_buy", 100, trigger_price=10.5,
                                   order_type="market", quote=QUOTE)
        self.svc.cancel_condition(c["cond_id"])
        assert self.svc.list_conditions()[0]["status"] == "CANCELLED"
        with pytest.raises(ManualOrderError):
            self.svc.cancel_condition(c["cond_id"])

    def test_trigger_price_side_must_be_valid(self):
        # 止损价必须在现价下方，否则一提交就触发（对齐老虎的价格校验）
        with pytest.raises(ManualOrderError):
            self.svc.add_condition("000001.SZ", "stop_loss", 100, trigger_price=10.20, quote=QUOTE)
        with pytest.raises(ManualOrderError):
            self.svc.add_condition("000001.SZ", "take_profit", 100, trigger_price=9.80, quote=QUOTE)

    def test_confirm_count_needs_consecutive_hits(self):
        self.svc.add_condition("000001.SZ", "breakout_buy", 100, trigger_price=10.50,
                               order_type="market", confirm_count=2, quote=QUOTE)
        out = self.svc.evaluate({"000001.SZ": {"last": 10.60}})
        assert out["triggered"] == []
        out = self.svc.evaluate({"000001.SZ": {"last": 10.60}})
        assert len(out["triggered"]) == 1

    def test_error_alias_compat(self):
        """兼容别名：PaperOrderError 就是 ManualOrderError（旧测试/调用方还能 import）。"""
        from app.core.paper import orders as orders_mod
        assert orders_mod.ManualOrderError is orders_mod.PaperOrderError

    def test_all_cond_types_known(self):
        """条件单类型全在（口径唯一源 COND_TYPES）。2026-10-28 起 11 类（新增回踩均线/关键K）。"""
        assert set(COND_TYPES) == {"stop_loss", "take_profit", "trailing_sell", "rebound_buy",
                                   "breakout_buy", "time_order", "breakeven_stop",
                                   "ma5_break", "signal_buy", "ma_pullback_buy", "key_k_break"}
        assert MIN_LOT == 100


# ---------------------------------------------------------------- 时段闸门
class TestSessionGates(OrdersTestBase):
    def test_closed_session_limit_rests_market_rejected(self):
        svc = self._new_svc(cond_name="closed_limit.json", session_ok=lambda: False)
        # 非交易时段：限价单可以挂，但不成交（等开盘后真有对手盘才算）
        res = svc.place("000001.SZ", "buy", 100, 9.0, order_type="limit",
                        quote={"last": 10.0, "fresh": False, "limit_up": 11.0, "limit_down": 9.0})
        assert res["order"]["status"] == "REPORTED"
        assert res["order"]["filled_qty"] == 0
        # 市价单没对手盘 → 直接拒
        with pytest.raises(ManualOrderError) as cm:
            svc.place("000001.SZ", "buy", 100, None, order_type="market",
                      quote={"last": 10.0, "fresh": False})
        assert cm.value.code == "closed"

    def test_closed_session_skips_evaluate(self):
        svc = self._new_svc(cond_name="closed_eval.json", session_ok=lambda: False)
        svc.add_condition("000001.SZ", "breakout_buy", 100, trigger_price=10.5,
                          order_type="market", quote=QUOTE)
        out = svc.evaluate({"000001.SZ": {"last": 10.9}})
        assert out["triggered"] == []
        assert out.get("skipped") == "closed"

    def test_match_pending_skipped_when_closed(self):
        """2026-10-21 修：非交易时段一律不补成交（收盘后的"最新价"不是对手盘）。"""
        svc = self._new_svc(cond_name="closed_match.json", session_ok=lambda: False)
        svc.place("000001.SZ", "buy", 100, 9.50, order_type="limit",
                  quote={"last": 10.0, "fresh": True, "limit_up": 11.0, "limit_down": 9.0})
        assert svc.match_pending({"000001.SZ": {"last": 9.40}}) == []


# ---------------------------------------------------------------- 实盘通道（整类删除 → skip）
class TestLiveChannelRemoved(OrdersTestBase):
    """旧版实盘用例。4.1.2 整类删除实盘通道（LiveManualBroker / _place_live /
    _live_account_view / live_broker() / live_order_enabled 分支），因此这些用例
    只剩「纸面口径不可被 live 触发」这一条可测事实，其余原样跳过。"""

    def test_trade_mode_always_paper(self):
        """trade_mode() 恒 "paper"：engine_state 写什么都不改变口径。"""
        self._write_state("live", live_order_enabled=True)
        assert self.svc.trade_mode() == "paper"
        assert self.svc.live_order_enabled() is False
        # engine_state.json 不存在也返回 paper，绝不回退 live
        svc_no_state = self._new_svc(cond_name="no_state.json")
        svc_no_state.engine_state_path = str(self.tmp / "not_exists_engine_state.json")
        assert svc_no_state.trade_mode() == "paper"
        assert svc_no_state.live_order_enabled() is False

    def test_live_mode_state_does_not_block_paper_order(self):
        """旧版 trade_mode=live 且未开开关 → 一律拒（live_locked）。
        本版没有 live 通道了，同一路径应照常按纸面成交（拒绝分支已删除）。"""
        self._write_state("live")            # 无 live_order_enabled
        res = self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        assert res["order"]["status"] == "FILLED"
        assert res["order"].get("live") is None      # 纸面记录不带 live 标记

    @pytest.mark.skip(reason="paper-only migration: 实盘通道（LiveManualBroker/_place_live）已整类删除")
    def test_live_locked_without_switch(self):
        raise AssertionError("实盘通道已删除，本用例不应执行")

    @pytest.mark.skip(reason="paper-only migration: 实盘通道（LiveManualBroker/_place_live）已整类删除")
    def test_live_mode_routes_to_qmt(self):
        raise AssertionError("实盘通道已删除，本用例不应执行")


# ---------------------------------------------------------------- 手工优先锁
class TestManualLock:
    """手工优先锁（2026-10-21 主人）：手工下单后自动引擎同票当日让开。"""

    def test_mark_and_check(self, tmp_path):
        lock = tmp_path / "manual_lock.json"
        assert manual_locked("000001.SZ", path=lock) is False
        mark_manual_lock("000001.SZ", "PP1", path=lock)
        assert manual_locked("000001.SZ", path=lock) is True
        assert manual_locked("000002.SZ", path=lock) is False
        assert manual_locked("000001.SZ", day="20200101", path=lock) is False

    def test_place_writes_manual_lock(self, tmp_path):
        """下单成功后自动落手工锁（同一票当日自动引擎让开）。"""
        paths = state.get_paths(tmp_path / "p2")
        broker = PaperBroker(state_dir=tmp_path / "p2", initial_cash=100000.0)
        svc = PaperOrderService(broker=broker, state_dir=tmp_path / "p2",
                                session_ok=lambda: True)
        svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)
        assert manual_locked("000001.SZ", path=paths.manual_lock) is True


# ---------------------------------------------------------------- 4.1.2 新增：wanted_codes
class TestWantedCodes(OrdersTestBase):
    def test_wanted_codes_union(self):
        """feed 订阅清单 = 持仓 + ACTIVE 条件单 + REPORTED/PARTIAL 挂单，去重排序。"""
        self.svc.place("000001.SZ", "buy", 1000, 10.0, quote=QUOTE)      # 持仓
        self.svc.place("000002.SZ", "buy", 100, 9.50, quote=QUOTE)       # REPORTED 挂单
        self._backdate_trades()
        self.svc.add_condition("000004.SZ", "rebound_buy", 100, pct=1.0,
                               order_type="market", quote={"last": 10.0})   # ACTIVE 条件单
        codes = self.svc.wanted_codes()
        assert codes == sorted(set(codes))              # 去重且排序
        assert "000001.SZ" in codes                     # 持仓
        assert "000004.SZ" in codes                     # ACTIVE 条件单
        assert self.svc.wanted_codes() == codes         # 幂等（只读不改状态）
