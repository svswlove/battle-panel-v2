# -*- coding: utf-8 -*-
"""app/core/paper/broker.py 单测（4.1.2 移植）：纸面撮合 / 部分成交 / 撤单 / 记账 / 估值口径

【来源】旧 engine/tests/test_paper_broker.py（2026-10-23 任务书 4.1.2 移植）。
断言原样保留（费用、部分成交、T+1、补账、估值口径）；改动只有三处：
  1. 路径全部走 state_dir=tmp_path（临时目录），**绝不写真实 data/panel_v2/paper**；
  2. import 由 core.paper_broker → app.core.paper.broker；
  3. intraday.json 双写用例改成 pytest.mark.skip（4.1.2 明确删除该双写，见 broker.py 头）。
另外补 3 个新用例：账本目录隔离 / 包内无真实通道关键字 / reset_to_initial。

【测试隔离铁律】本文件不使用 open(...,"w")，读写账本一律走 app.core.paper.state，
这样连测试代码都不可能出现"绕过 state 写账本"的坏样板（硬约束第 3 条）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as paths_mod  # noqa: E402
from app.core.paper import state  # noqa: E402
from app.core.paper.broker import PaperBroker, calc_fee  # noqa: E402

# 真实行情库 / 真实交易库 / 柜台客户端四类关键字（**分段拼接**：本文件自身也在
# app/core/paper/ 下，必须能过"包内零关键字"那条全目录扫描，所以不能出现完整字面量）
FORBIDDEN_TOKENS = ["xt" + "quant", "xt" + "trader", "xt" + "data", "qmt" + "_trader"]

# 分钟 bar：开盘 10.0 / 最高 10.2 / 最低 9.9（限价买 10.05 会以开盘价 10.0 成交）
BAR = {"ts": "2026-08-24 09:45:00", "open": 10.0, "high": 10.2, "low": 9.9,
       "close": 10.1, "volume": 100000, "pre_close": 10.0}


def _ts(text: str) -> float:
    """'YYYY-MM-DD HH:MM:SS' → epoch（假时钟用，避免依赖真实当前时间）。"""
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


def _scan_package_for_live_tokens():
    """扫 app/core/paper 下**全部 .py**（含本测试文件自己），返回命中项 [文件 :: 关键字]。

    关键字表见 FORBIDDEN_TOKENS（由字符串分段拼接而成，故本文件自身不含完整字面量，
    否则它会被自己的扫描命中）。
    """
    pkg = Path(__file__).resolve().parents[1]        # app/core/paper
    offenders = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore").lower()
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f"{py.relative_to(pkg)} :: {token}")
    return offenders


class FakeClock:
    """确定性时钟（旧测试同名工具，逐字保留语义）。"""

    def __init__(self, t: float = 1784597400.0):  # 2026-07-21 09:30（本地）
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class BrokerTestBase:
    """公共夹具：临时账本目录 + 假时钟 + 只走 state 的读写工具。"""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.state_dir = tmp_path / "paper"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.paths = state.get_paths(self.state_dir)
        self.trades_path = str(self.paths.trades)
        self.acct_path = str(self.paths.account)
        self.clock = FakeClock()
        self.broker = PaperBroker(state_dir=self.state_dir, clock=self.clock)

    def _acct(self):
        """读账本（宽容读，与生产同一条 API）。"""
        doc = state.read_json(self.acct_path, {})
        return doc if isinstance(doc, dict) else {}

    def _trades(self):
        doc = state.read_json(self.trades_path, {})
        return (doc or {}).get("trades") or []


# ---------------------------------------------------------------- 独立账本（第5件）
class TestPaperAccount(BrokerTestBase):
    """独立账本（第5件，2026-08-24 主人需求）：每笔成交原子更新 paper_account.json。
    覆盖：买入扣现金/持仓加权成本 / 部分成交按实成交量 / 卖出结算 realized_pnl / 胜率统计 /
    账本路径缺省跟随账本目录（测试隔离）。"""

    def test_buy_fill_updates_account(self):
        self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR)
        acct = self._acct()
        assert acct["initial_cash"] == 500000.0          # 主人定初始资金 50 万
        fee = calc_fee(10000.0, "BUY")
        assert round(acct["cash"], 2) == round(500000.0 - 10000.0 - fee, 2)
        pos = acct["positions"]["601985.SH"]
        assert pos["qty"] == 1000
        assert round(pos["cost_price"], 4) == round((10000.0 + fee) / 1000, 4)
        assert acct["realized_pnl"] == 0.0
        assert acct["equity_curve"]                          # 权益曲线已记录
        assert round(acct["equity_curve"][-1]["total"], 2) == 500000.0

    def test_partial_fill_books_actual_qty(self):
        broker = PaperBroker(state_dir=self.state_dir, clock=self.clock,
                             partial_ratio_override=0.5)
        r = broker.order("601985.SH", 2000, 10.05, side="buy", bar=BAR)
        assert r["status"] == "PARTIAL"
        acct = self._acct()
        exp = 500000.0 - 1000 * 10.0 - calc_fee(1000 * 10.0, "BUY")
        assert round(acct["cash"], 2) == round(exp, 2)         # 按实成交量 1000 记账（含费用）
        assert acct["positions"]["601985.SH"]["qty"] == 1000

    def test_sell_realizes_pnl_and_win_rate(self):
        self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR)   # 成本 10.0（开盘 10.0≤限价→按开盘）
        self.broker.roll_day()                                            # T+1：次日才可卖
        bar2 = {"ts": "2026-08-25 10:00:00", "open": 10.5, "high": 10.6,
                "low": 10.4, "close": 10.5, "volume": 100000}
        self.broker.order("601985.SH", 1000, 10.5, side="sell", bar=bar2)  # 涨 5%，扣双边费用后仍盈利
        acct = self._acct()
        assert acct["positions"].get("601985.SH") is None                   # 清仓
        buy_fee = calc_fee(10000.0, "BUY")
        sell_fee = calc_fee(10500.0, "SELL")
        exp_pnl = round((10.5 - round((10000.0 + buy_fee) / 1000, 4)) * 1000 - sell_fee, 2)
        assert round(acct["realized_pnl"], 2) == round(exp_pnl, 2)           # 含双边费用
        assert round(acct["cash"], 2) == round(
            500000.0 - 10000.0 - buy_fee + 10500.0 - sell_fee, 2)
        assert acct["stats"]["closed_trades"] == 1                           # 已平仓 1 笔
        assert acct["stats"]["wins"] == 1                                    # 盈利卖出
        assert acct["stats"]["win_rate"] == 100.0

    def test_default_account_path_follows_trades_dir(self):
        """不显式传 paper_account_path → 账本与 trades 同目录（4.1.2 口径：state_dir 决定）。"""
        assert self.broker.paper_account_path == self.acct_path
        # 显式给 trades 单文件路径时，账本自动跟随同目录（旧行为保留）
        broker2 = PaperBroker(
            paper_trades_path=str(self.state_dir / "sub" / "paper_trades.json"),
            clock=self.clock)
        assert broker2.paper_account_path == str(self.state_dir / "sub" / "paper_account.json")

    def test_unfilled_does_not_touch_account(self):
        self.broker.order("601985.SH", 1000, 9.5, side="buy", bar=BAR)  # 未成交（low 9.9 > 9.5）
        assert not Path(self.acct_path).exists()                        # 未成交不建账本

    def test_missing_account_auto_init_and_first_order_ok(self):
        """账本缺失自动初始化（主人追加需求）：无 paper_account.json → 首单正常成交，
        账本自动创建（初始资金 50 万），不报错不拒单。"""
        assert not Path(self.acct_path).exists()          # 新目录：天然缺失
        r = self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR)
        assert r["status"] == "FILLED"                    # 首单不因账本缺失失败
        acct = self._acct()
        assert acct["initial_cash"] == 500000.0
        assert round(acct["cash"], 2) == round(
            500000.0 - 10000.0 - calc_fee(10000.0, "BUY"), 2)
        assert round(acct["equity_curve"][-1]["total"], 2) == 500000.0

    def test_initial_cash_default_500k(self):
        assert self.broker.initial_cash == 500000.0       # 初始资金默认 50 万（主人定）

    def test_fee_model_w0_8_no_min(self):
        """2026-10-21 主人定：佣金万 1、免 5；另印花税卖 0.05%、双向过户费 0.001%。"""
        assert round(calc_fee(10000.0, "BUY"), 2) == 1.10    # 万 1 + 万 0.1
        assert round(calc_fee(10000.0, "SELL"), 2) == 6.10   # +印花税 0.05%
        assert round(calc_fee(1000.0, "BUY"), 2) == 0.11
        assert calc_fee(0, "BUY") == 0.0


# ---------------------------------------------------------------- 撮合
class TestMatching(BrokerTestBase):
    def test_limit_buy_fill(self):
        r = self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR,
                              signal_id="SIG-001", kind="BUY")
        assert r["status"] == "FILLED"
        # 开盘 10.0 ≤ 限价 10.05 → 按开盘价成交
        assert r["filled_price"] == 10.0
        assert r["filled_qty"] == 1000
        assert r["simulation"]["rule"] == "buy_low_le_price"

    def test_limit_buy_unfilled(self):
        r = self.broker.order("601985.SH", 1000, 9.5, side="buy", bar=BAR)
        assert r["status"] == "REPORTED"
        assert r["filled_qty"] == 0

    def test_limit_sell_fill_at_limit(self):
        # 先建仓并过日切（T+1），再验限价卖单成交价
        self.broker.order("601985.SH", 1000, 9.9, side="buy", bar=BAR)
        self.broker.roll_day()
        r = self.broker.order("601985.SH", 1000, 10.1, side="sell", bar=BAR)
        assert r["status"] == "FILLED"
        # 开盘 10.0 < 限价 10.1 → 按限价成交
        assert r["filled_price"] == 10.1

    def test_market_fill_at_open(self):
        r = self.broker.order("601985.SH", 1000, 0, side="buy", order_type="market", bar=BAR)
        assert r["status"] == "FILLED"
        assert r["filled_price"] == 10.0


class TestPartialFill(BrokerTestBase):
    def test_partial_ratio_override(self):
        broker = PaperBroker(state_dir=self.state_dir, clock=self.clock,
                             partial_ratio_override=0.5)
        r = broker.order("601985.SH", 2000, 10.05, side="buy", bar=BAR)
        assert r["status"] == "PARTIAL"
        assert r["filled_qty"] == 1000


# ---------------------------------------------------------------- 撤单 / 对齐
class TestCancelAndAlign(BrokerTestBase):
    def test_cancel_updates_record(self):
        # Q1 2026-09-12：cancel 对 FILLED 单返回 False（已成交不可撤，防覆盖错账），
        # 本用例回归本意=撤 REPORTED 挂单（去掉 bar 使单据保持挂单态）
        r = self.broker.order("601985.SH", 1000, 10.05, side="buy")
        oid = r["order_id"]
        assert r["status"] == "REPORTED"
        assert self.broker.cancel(oid) is True
        rec = self.broker.get_trade(oid)
        assert rec["status"] == "CANCELLED"
        assert rec["closed_at"] is not None

    def test_cancel_filled_returns_false(self):
        """Q1 防御层：已成交单不可撤（返回 False，调用方走市价重试/告警路径）。"""
        r = self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR)
        assert r["status"] == "FILLED"
        assert self.broker.cancel(r["order_id"]) is False
        assert self.broker.get_trade(r["order_id"])["status"] == "FILLED"

    def test_cancel_market_retry(self):
        # Q1 2026-09-12：同上，市价重试路径也撤 REPORTED 挂单（retry_market 按当分钟 open 成交）
        r = self.broker.order("601985.SH", 1000, 10.05, side="buy")
        oid = r["order_id"]
        assert r["status"] == "REPORTED"
        assert self.broker.cancel(oid, retry_market=True, bar=BAR) is True
        rec = self.broker.get_trade(oid)
        assert rec["status"] == "CANCELLED"
        assert rec["simulation"]["rule"] == "market_retry"

    def test_query_align(self):
        r = self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR)
        q = self.broker.query_align(r["order_id"])
        assert q["status"] == "FILLED"
        assert q["filled_qty"] == 1000


# ---------------------------------------------------------------- 落盘
class TestPersistence(BrokerTestBase):
    def test_paper_trades_json(self):
        self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR,
                          signal_id="SIG-001", name="中国核电", kind="BUY",
                          channel="A", source="BP-01", trigger_time="2026-08-24 09:45:02")
        doc = state.read_json(self.trades_path, {})
        assert len(doc["trades"]) == 1
        t = doc["trades"][0]
        assert t["signal_id"] == "SIG-001"
        assert t["limit_price"] == 10.05
        assert t["status"] == "FILLED"

    @pytest.mark.skip(reason="paper-only migration: intraday 双写已移除")
    def test_intraday_json_schema_valid(self):
        """旧用例：下单后双写 data/panel/intraday.json 并通过 intraday schema 校验。

        4.1.2 明确删除 write_intraday_signal / _write_intraday_signal_locked /
        _load_intraday / _day_summary —— 纸面模块不再写生产 panel JSON
        （信号流水归 4.1.3 的 core feed + 4.1.4 的 paper 插件）。保留空壳用例以便追溯。
        """
        raise AssertionError("intraday 双写已移除，本用例不应执行")

    def test_stop_sell_order_buy1_price(self):
        r = self.broker.stop_sell_order("601985.SH", 2000, buy1_price=8.77,
                                        signal_id="SIG-STOP-1", kind="STOP",
                                        trigger_time="2026-08-24 10:00:00")
        assert r["record"]["side"] == "SELL"
        assert r["record"]["limit_price"] == 8.77
        assert r["record"]["kind"] == "STOP"

    def test_take_profit_sell_order_kind(self):
        """P4①：止盈单 kind=TAKE_PROFIT，单号 PT 前缀，同属保护单。"""
        r = self.broker.take_profit_sell_order("601985.SH", 100, 12.34)
        assert r["record"]["kind"] == "TAKE_PROFIT"
        assert r["record"]["is_stop"] is True
        assert str(r["order_id"]).startswith("PT")


# ---------------------------------------------------------------- 对账补记（P9）
class TestReconcileFill(BrokerTestBase):
    """P9 对账补记（2026-08-27 对账实锤的防再犯层）：REPORTED 挂单经人工/脚本
    确认可成交后 → FILLED 入账（走 _bookkeeping 同一原子写路径），幂等不重复补。"""

    def _reported_order(self):
        """造一笔 no_bar REPORTED 挂单（当日真实场景：bar 提供器无输入）。"""
        r = self.broker.order("002428.SZ", 100, 94.55, side="buy", bar=None,
                              signal_id=None, kind="BUY", channel="A",
                              source="BP-20260827-01")
        oid = r["order_id"]
        rec = self.broker.get_trade(oid)
        assert rec["status"] == "REPORTED"     # 前置：no_bar 确实挂账
        return oid

    def test_reconcile_reported_to_filled_and_booked(self):
        oid = self._reported_order()
        res = self.broker.reconcile_fill(oid, filled_qty=100, filled_price=94.55,
                                         matched_bar_ts="0932", note="tick 回放双验证")
        assert res["changed"] is True
        rec = self.broker.get_trade(oid)
        assert rec["status"] == "FILLED"
        assert rec["filled_qty"] == 100
        assert round(rec["filled_price"], 2) == 94.55
        assert rec["simulation"]["rule"] == "reconcile_fill"
        # 账本入账：cash 扣减（含买入费用）+ positions 增加 + updated_at 刷新（与正常成交同路径）
        acct = self._acct()
        fee = calc_fee(100 * 94.55, "BUY")
        assert round(acct["cash"], 2) == round(500000 - 100 * 94.55 - fee, 2)
        pos = acct["positions"]["002428.SZ"]
        assert pos["qty"] == 100
        assert round(pos["cost_price"], 4) == round((100 * 94.55 + fee) / 100, 4)
        assert acct["updated_at"] != ""

    def test_reconcile_idempotent(self):
        oid = self._reported_order()
        r1 = self.broker.reconcile_fill(oid, filled_qty=100, filled_price=94.55)
        assert r1["changed"] is True
        r2 = self.broker.reconcile_fill(oid)     # 二次补：拒绝
        assert r2["changed"] is False
        assert "不重复补账" in r2["reason"]
        acct = self._acct()
        assert acct["positions"]["002428.SZ"]["qty"] == 100   # 账不被双扣

    def test_reconcile_missing_order_raises(self):
        with pytest.raises(ValueError):
            self.broker.reconcile_fill("PP-NOTEXIST")

    def test_reconcile_default_uses_limit_price(self):
        # filled_qty/filled_price 缺省 → 用挂单 qty 与 limit_price（回放确认口径）
        oid = self._reported_order()
        res = self.broker.reconcile_fill(oid, matched_bar_ts="0934")
        assert res["changed"] is True
        rec = self.broker.get_trade(oid)
        assert rec["filled_qty"] == 100
        assert round(rec["filled_price"], 2) == 94.55

    def test_reconcile_partial_tail_cumulative(self):
        """P2-2（嗅嗅审查 2026-09-01）：PARTIAL 补尾 filled_qty 改累加——
        已成交 50 再补尾 50 → 记录 filled_qty=100（原覆盖写只留 50，污染 win_stats 溯源）。"""
        # 真实 partial 成交 50（bar volume=0.5 股 → liquidity=50 < qty=100）
        pbar = {"ts": "2026-08-24 09:30:00", "open": 94.55, "high": 94.7,
                "low": 94.5, "close": 94.6, "volume": 0.5}
        r = self.broker.order("002428.SZ", 100, 94.55, side="buy", bar=pbar)
        assert r["status"] == "PARTIAL"
        assert r["filled_qty"] == 50
        oid = r["order_id"]
        # 补尾剩余 50
        res = self.broker.reconcile_fill(oid, filled_qty=50, filled_price=94.55,
                                         matched_bar_ts="0935")
        assert res["changed"] is True
        rec = self.broker.get_trade(oid)
        assert rec["filled_qty"] == 100   # 累加：50+50=100（非覆盖写）
        assert rec["status"] == "FILLED"
        # 动账=前 50 + 本次 50 均入账：持仓 100、cash 扣 100×94.55 + 两笔费用
        acct = self._acct()
        assert acct["positions"]["002428.SZ"]["qty"] == 100
        fees = calc_fee(50 * 94.55, "BUY") * 2
        assert abs(acct["cash"] - (500000 - 100 * 94.55 - fees)) <= 0.02

    def test_reconcile_sell_clamped_to_position(self):
        """P2-2：裸 reconcile_fill SELL 超卖钳制——订单剩余量大于账本持仓时
        按持仓卖（qty=min(qty,pos_qty)），禁扣负 pop 全额入现金。"""
        # 先建仓 50：买入 50@10.0（非补账，直接成交）
        buy_bar = {"ts": "2026-08-24 09:30:00", "open": 10.0, "high": 10.2,
                   "low": 9.8, "close": 10.0, "volume": 10000}
        b = self.broker.order("002428.SZ", 50, 10.0, side="buy", bar=buy_bar)
        assert b["status"] == "FILLED"
        self.broker.roll_day()   # T+1：次日才可卖
        # 挂 SELL 100@10.5（无 bar → REPORTED）
        s = self.broker.order("002428.SZ", 100, 10.5, side="sell", bar=None)
        oid = s["order_id"]
        # 裸补账（filled_qty 缺省=剩余 100）→ 应钳到持仓 50，不超卖
        res = self.broker.reconcile_fill(oid, filled_price=10.5, matched_bar_ts="0935")
        assert res["changed"] is True
        assert res["filled_qty"] == 50      # 钳到持仓量
        rec = self.broker.get_trade(oid)
        assert rec["filled_qty"] == 50
        acct = self._acct()
        assert acct["positions"].get("002428.SZ") is None   # 持仓清空（卖 50=全部）
        fees = calc_fee(50 * 10.0, "BUY") + calc_fee(50 * 10.5, "SELL")
        assert round(acct["cash"], 2) == round(500000 - 50 * 10.0 + 50 * 10.5 - fees, 2)


# ---------------------------------------------------------------- 并发（跨进程锁）
def _wk_buy(state_dir: str, costs):
    """子进程：连续买入（每个进程独立 PaperBroker，写同一账本）。"""
    b = PaperBroker(state_dir=state_dir,
                    clock=lambda: _ts("2026-09-01 10:00:00"))
    bar = {"ts": "2026-09-01 10:00:00", "open": 10.0, "high": 10.1,
           "low": 9.9, "close": 10.0, "volume": 50000.0}
    for price in costs:
        b.order("601985.SH", 100, price, side="buy", bar=bar)


def _wk_mtm(state_dir: str):
    """子进程：日终重估（验证 mtm 与成交并发不丢更新）。"""
    b = PaperBroker(state_dir=state_dir,
                    clock=lambda: _ts("2026-09-01 15:00:00"))
    b.mark_to_market({"601985.SH": 11.0}, date="2026-09-01")


class TestLedgerConcurrency(BrokerTestBase):
    """P2-3（嗅嗅审查 2026-09-01）：账本 RMW 跨进程锁——多写方并发不丢更新。
    用两个进程各自独立 PaperBroker 写同一账本文件：进程A连续买入、进程B连续买入。
    若无锁，读-改-写交错会让其中一方覆盖对方现金变动 → 最终现金≠预期。
    有锁则最终现金 = 初始 - 买入总额（含并发安全）。"""

    def test_concurrent_buy_sell_no_lost_update(self):
        import multiprocessing as mp
        # 成交价=bar open 10.0（o<=price 按开盘价成交），每笔 100 股 → 扣 1000 现金
        cost_series = [10.0 + 0.01 * i for i in range(20)]
        p1 = mp.Process(target=_wk_buy, args=(str(self.state_dir), cost_series[:10]))
        p2 = mp.Process(target=_wk_buy, args=(str(self.state_dir), cost_series[10:]))
        p1.start(); p2.start(); p1.join(); p2.join()
        assert p1.exitcode == 0
        assert p2.exitcode == 0
        acct = self._acct()
        # 20 笔买入 × 100 股 × 成交价 10.0 = 扣 20000 + 每笔费用
        fees = sum(calc_fee(1000.0, "BUY") for _ in range(20))
        assert abs(acct["cash"] - (500000.0 - 20000.0 - fees)) <= 0.05
        assert acct["positions"]["601985.SH"]["qty"] == 2000

    def test_mtm_concurrent_preserves_account(self):
        """mark_to_market 与成交并发：锁内 RMW，重估不吞成交现金变动。"""
        import multiprocessing as mp
        self.broker.order("601985.SH", 500, 10.05, side="buy", bar=BAR)
        p = mp.Process(target=_wk_mtm, args=(str(self.state_dir),))
        p.start(); p.join()
        assert p.exitcode == 0
        acct = self._acct()
        assert round(acct["cash"], 2) == round(
            500000.0 - 500 * 10.0 - calc_fee(5000.0, "BUY"), 2)   # 现金不被 mtm 污染
        assert acct["positions"]["601985.SH"]["qty"] == 500


# ---------------------------------------------------------------- 日终重估（T2）
class TestMarkToMarket(BrokerTestBase):
    """T2 日终 mark-to-market（2026-08-27 工单）：按当日收盘价重估 equity_curve 当日值。
    幂等（全量重算+同日覆盖）；收盘价缺失保留成本口径并标注。
    验收场景：模拟今日（08-27）——002428 两笔买入 100@94.55 + 100@94.79，
    cash=481,066，收盘 101.36 → 当日 total 应为 501,338（非成本口径 500,000）。"""

    def _seed_today(self):
        """模拟今日成交：两笔 002428 买入（P9 补账口径）。"""
        self.broker.reconcile_fill(
            self.broker.order("002428.SZ", 100, 94.55, side="buy", bar=None)["order_id"],
            filled_qty=100, filled_price=94.55, matched_bar_ts="0932")
        self.broker.reconcile_fill(
            self.broker.order("002428.SZ", 100, 94.79, side="buy", bar=None)["order_id"],
            filled_qty=100, filled_price=94.79, matched_bar_ts="0934")

    def test_mtm_today_scenario(self):
        """验收主用例：两笔买入（含费用）→ 按收盘 101.36 重估。"""
        self._seed_today()
        acct = self._acct()
        fees = calc_fee(9455.0, "BUY") + calc_fee(9479.0, "BUY")
        cash = 500000.0 - 9455.0 - 9479.0 - fees
        assert round(acct["cash"], 2) == round(cash, 2)
        assert round(acct["equity_curve"][-1]["total"], 2) == 500000.0  # 成本口径（含费用后仍平）
        s = self.broker.mark_to_market({"002428.SZ": 101.36}, date="2026-08-27")
        assert round(s["total"], 2) == round(200 * 101.36 + cash, 2)
        assert s["mtm_source"] == "close"
        assert s["missing"] == []
        curve = {c["date"]: c for c in self._acct()["equity_curve"]}
        assert round(curve["2026-08-27"]["total"], 2) == round(200 * 101.36 + cash, 2)
        assert curve["2026-08-27"].get("mtm")

    def test_mtm_idempotent(self):
        """幂等：重复执行同日 total 不叠加、结果一致。"""
        self._seed_today()
        s1 = self.broker.mark_to_market({"002428.SZ": 101.36}, date="2026-08-27")
        s2 = self.broker.mark_to_market({"002428.SZ": 101.36}, date="2026-08-27")
        s3 = self.broker.mark_to_market({"002428.SZ": 101.36}, date="2026-08-27")
        assert round(s1["total"], 6) == round(s2["total"], 6)
        assert round(s2["total"], 6) == round(s3["total"], 6)
        entries = [c for c in self._acct()["equity_curve"] if c["date"] == "2026-08-27"]
        assert len(entries) == 1     # 同日仅一条，不重复追加

    def test_mtm_missing_close_keeps_cost_and_marks(self):
        """收盘价缺失（停牌/数据缺失）→ 保留成本口径并在条目标注 missing。"""
        self._seed_today()
        s = self.broker.mark_to_market({}, date="2026-08-27")   # 全量缺失
        assert round(s["total"], 2) == 500000.0                 # 成本口径不变
        assert s["mtm_source"] == "cost_fallback"
        assert s["missing"] == ["002428.SZ"]
        entry = [c for c in self._acct()["equity_curve"] if c["date"] == "2026-08-27"][0]
        assert entry["mtm_source"] == "cost_fallback"
        assert "002428.SZ" in entry["missing"]

    def test_mtm_partial_missing(self):
        """部分缺失：有收盘价的按收盘价，无的按成本价，missing 列表准确。"""
        self._seed_today()
        self.broker.reconcile_fill(
            self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=None)["order_id"],
            filled_qty=1000, filled_price=10.0, matched_bar_ts="0935")
        # 002428 有收盘价 101.36；601985 无（停牌模拟）→ 成本价（含买入费用）
        s = self.broker.mark_to_market({"002428.SZ": 101.36}, date="2026-08-27")
        acct = self._acct()
        cost601 = float(acct["positions"]["601985.SH"]["cost_price"])
        assert round(s["total"], 2) == round(acct["cash"] + 200 * 101.36 + 1000 * cost601, 2)
        assert s["missing"] == ["601985.SH"]
        assert s["mtm_source"] == "cost_fallback"


# ---------------------------------------------------------------- 权益口径（2026-10-22 主人定）
class TestEquityCaliber(BrokerTestBase):
    """equity_curve.total 口径（2026-10-22 主人定，工单：修 paper_account 口径）。

    旧口径 = cash + Σ(qty × **成本价**)，数学上恒等于 initial_cash + realized_pnl
    ⇒ 持仓期净值是假直线，与真实总资产偏差 = 浮亏合计（10-21 实证虚高 345.79）。
    新口径 = cash + Σ(qty × **当日收盘价**)，缺价票按成本价 + missing 标注。

    验收场景（工单给定，用真实数字钉死）：
      cash=463,915.61 / 002733.SZ 200@15.71 / 002812.SZ 100@43.69 / 002074.SZ 100@25.89
      → 持仓市值 10,100.00 → total = 474,015.61（误差 0）。
    """

    HOLDINGS = {"002733.SZ": (200, 15.71, 16.05),
                "002812.SZ": (100, 43.69, 46.4551),
                "002074.SZ": (100, 25.89, 25.9028)}

    def _seed_ledger(self, cash=463915.61):
        """直接铺一份账本（不经过成交路径，专测估值口径）。走 state 原子写，不裸 open。"""
        doc = {"initial_cash": 500000.0, "cash": cash,
               "positions": {c: {"qty": q, "cost_price": cp, "today_bought": 0,
                                 "available_qty": q}
                             for c, (q, _px, cp) in self.HOLDINGS.items()},
               "realized_pnl": -25638.64, "equity_curve": [],
               "stats": {"win_rate": 0.0, "closed_trades": 0, "wins": 0},
               "updated_at": "2026-10-21 15:00:00", "trade_date": "2026-10-21"}
        state.atomic_write_json(self.acct_path, doc, audit_event="test.seed_ledger")

    def _broker_with_closes(self, closes):
        """注入确定性价源（零生产 IO）。"""
        self.broker.close_price_provider = lambda codes, day: {
            c: closes[c] for c in codes if c in closes}
        return self.broker

    def test_1021_acceptance_total_is_474015_61(self):
        """验收主用例：10-21 现金 463,915.61 + 持仓市值 10,100.00 = 474,015.61，误差 0。"""
        self._seed_ledger()
        closes = {c: px for c, (_q, px, _cp) in self.HOLDINGS.items()}
        broker = self._broker_with_closes(closes)
        s = broker.mark_to_market(date="2026-10-21")
        assert round(s["total"], 2) == 474015.61
        assert s["mtm_source"] == "close"
        assert s["missing"] == []
        entry = [c for c in self._acct()["equity_curve"] if c["date"] == "2026-10-21"][0]
        assert round(entry["total"], 2) == 474015.61
        assert entry.get("mtm")               # 重估字段齐（10-21 旧记录缺这三个）
        assert entry.get("mtm_source") == "close"
        assert entry.get("missing") == []

    def test_bookkeeping_uses_close_not_cost(self):
        """成交记账写曲线时同口径：买入后立刻按当日收盘估值（不再走成本价恒等式）。"""
        bar = {"ts": "2026-10-21 10:00:00", "open": 15.71, "high": 15.80,
               "low": 15.60, "close": 15.71, "volume": 100000}
        broker = self._broker_with_closes({"002733.SZ": 15.71})
        broker.clock = lambda: _ts("2026-10-21 10:00:00")
        broker.order("002733.SZ", 200, 15.70, side="buy", bar=bar)
        acct = self._acct()
        # 限价 15.70 低于开盘 15.71 → 按限价成交（撮合规则：open > 限价时取限价）
        amt = round(200 * 15.70, 2)
        fee = calc_fee(amt, "BUY")
        exp_cash = round(500000.0 - amt - fee, 2)
        assert round(acct["cash"], 2) == exp_cash
        entry = acct["equity_curve"][-1]
        # 估值用当日收盘 15.71（不是成交价/成本价）
        assert round(entry["total"], 2) == round(exp_cash + 200 * 15.71, 2)
        assert round(entry["total"], 2) != 500000.0   # 不再是恒等式
        assert entry.get("mtm_source") == "close"

    def test_empty_positions_total_equals_cash(self):
        """空仓：total = cash（不取价、不发无谓库查）。"""
        self._seed_ledger(cash=474361.4)
        doc = self._acct()
        doc["positions"] = {}
        state.atomic_write_json(self.acct_path, doc, audit_event="test.empty_positions")
        calls = []
        self.broker.close_price_provider = lambda codes, day: calls.append(codes) or {}
        s = self.broker.mark_to_market(date="2026-10-21")
        assert round(s["total"], 2) == 474361.4
        assert calls == []                 # 空仓不取价

    def test_missing_close_falls_back_to_cost_and_marks(self):
        """缺价票按成本价，标 cost_fallback + missing 列票码（沿用 08-27 字段约定）。"""
        self._seed_ledger()
        broker = self._broker_with_closes({"002733.SZ": 15.71})   # 另两只缺价
        s = broker.mark_to_market(date="2026-10-21")
        exp_mv = 200 * 15.71 + 100 * 46.4551 + 100 * 25.9028
        assert round(s["total"], 2) == round(463915.61 + exp_mv, 2)
        assert s["mtm_source"] == "cost_fallback"
        assert s["missing"] == ["002074.SZ", "002812.SZ"]

    def test_provider_exception_degrades_to_cost(self):
        """价源异常（库/网络/依赖问题）不得打断记账：全量成本口径降级并标注。"""
        self._seed_ledger()

        def _boom(codes, day):
            raise RuntimeError("db gone")

        self.broker.close_price_provider = _boom
        s = self.broker.mark_to_market(date="2026-10-21")
        exp_mv = sum(q * cp for q, _px, cp in self.HOLDINGS.values())
        assert round(s["total"], 2) == round(463915.61 + exp_mv, 2)
        assert s["mtm_source"] == "cost_fallback"
        assert len(s["missing"]) == 3

    def test_bookkeeping_after_eod_mtm_keeps_close_caliber(self):
        """盘后重估之后再发生成交：同日覆盖不得把已重估的收盘口径打回成本价
        （10-21 记录缺 mtm 字段即此形态：成交记账在重估之后写了一条无 meta 的条目）。"""
        self._seed_ledger()
        closes = {c: px for c, (_q, px, _cp) in self.HOLDINGS.items()}
        broker = self._broker_with_closes(closes)
        s1 = broker.mark_to_market(date="2026-10-21")
        assert round(s1["total"], 2) == 474015.61
        # 同日晚间卖出一笔（走成交记账，覆盖同日曲线）
        broker.clock = lambda: _ts("2026-10-21 20:00:00")
        bar = {"ts": "2026-10-21 14:57:00", "open": 43.69, "high": 43.80,
               "low": 43.60, "close": 43.69, "volume": 100000}
        broker.order("002812.SZ", 100, 43.69, side="sell", bar=bar, kind="SELL")
        entry = [c for c in self._acct()["equity_curve"] if c["date"] == "2026-10-21"][0]
        assert entry.get("mtm_source") == "close"   # 仍是收盘口径，非成本
        assert entry.get("mtm")

    def test_eod_booking_when_day_bar_missing_reuses_confirmed_marks(self):
        """盘后成交时当日 kline 未入库（价源归空）：用当日已确认价补缺口，不整批降回成本价。

        复现 10-21 实际链路：15:07 重估（价源正常）→ 夜间成交记账（价源拿不到当日价）。
        若不复用 marks，第二条会把 total 打回 cash+成本市值 —— 正是旧记录那条无 mtm 字段的假线。
        """
        self._seed_ledger()
        closes = {c: px for c, (_q, px, _cp) in self.HOLDINGS.items()}
        broker = self._broker_with_closes(closes)
        s1 = broker.mark_to_market(date="2026-10-21")
        assert round(s1["total"], 2) == 474015.61
        entry1 = [c for c in self._acct()["equity_curve"] if c["date"] == "2026-10-21"][0]
        assert entry1.get("marks")            # 已确认价已落条目
        # 价源"失明"（当日 bar 未入库）
        self.broker.close_price_provider = lambda codes, day: {}
        self.broker.clock = lambda: _ts("2026-10-21 23:30:00")
        bar = {"ts": "2026-10-21 14:57:00", "open": 25.89, "high": 25.90,
               "low": 25.80, "close": 25.89, "volume": 100000}
        self.broker.order("002074.SZ", 100, 25.89, side="sell", bar=bar, kind="SELL")
        acct = self._acct()
        entry2 = [c for c in acct["equity_curve"] if c["date"] == "2026-10-21"][0]
        assert entry2.get("mtm_source") == "close"
        assert entry2.get("missing") == []
        # 剩 2 只持仓（002733/002812）按已确认收盘价估值
        assert round(entry2["total"], 2) == round(
            acct["cash"] + 200 * 15.71 + 100 * 43.69, 2)


# ---------------------------------------------------------------- 锁超时 fail-open
class TestLedgerLockTimeout(BrokerTestBase):
    """F2（嗅嗅 2026-09-02）：_ledger_lock 锁超时 fail-open 必须留痕（不再静默直通）。"""

    def test_timeout_alerts_and_fails_open(self):
        from unittest import mock

        calls = []

        def _spy_alert(level, event, title, body):
            calls.append((level, event, title, body))

        with mock.patch.object(state, "locked",
                              side_effect=state.FileLockTimeout("test timeout")):
            with mock.patch.object(self.broker, "_alert", side_effect=_spy_alert):
                with self.broker._ledger_lock(self.trades_path):
                    pass      # fail-open：上下文可用（不抛）
        assert len(calls) == 1
        level, event, title, body = calls[0]
        assert event == "LEDGER_LOCK_TIMEOUT"
        assert "paper_account.json" in title     # 锚点 + 等待时长留痕
        assert "fail-open" in title


# ---------------------------------------------------------------- 4.1.2 新增用例
class TestVendorMigrationGuards(BrokerTestBase):
    """4.1.2 新增：账本目录隔离 / 包内无真实通道关键字 / 一键回初始。"""

    def test_state_dir_isolated(self):
        """默认账本路径必须落在 paths.PAPER_STATE_DIR 下，绝不出现 engine/state。"""
        default_paths = state.get_paths()          # 不传 root = 生产缺省
        assert default_paths.root == Path(paths_mod.PAPER_STATE_DIR)
        assert default_paths.account == Path(paths_mod.PAPER_STATE_DIR) / "paper_account.json"
        assert default_paths.trades == Path(paths_mod.PAPER_STATE_DIR) / "paper_trades.json"
        for p in (default_paths.root, default_paths.account, default_paths.trades,
                  default_paths.conditions, default_paths.freeze,
                  default_paths.manual_lock, default_paths.engine_state,
                  default_paths.kill_switch, default_paths.audit, default_paths.backups):
            text = str(p).replace("\\", "/").lower()
            assert "engine/state" not in text, f"账本路径落进了旧 state 目录：{p}"
            assert "data/panel/" not in text, f"账本路径落进了生产 panel 目录：{p}"
        # 显式 state_dir 优先，且不影响全局缺省
        custom = state.get_paths(self.state_dir)
        assert custom.root == Path(self.state_dir)
        assert state.get_paths().root == Path(paths_mod.PAPER_STATE_DIR)

    def test_env_override(self, tmp_path, monkeypatch):
        """PANEL_V2_PAPER_DIR 覆盖（每次 get_paths 重读，import 之后设置也生效）。"""
        env_dir = tmp_path / "env_paper"
        monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(env_dir))
        assert state.get_paths().root == Path(env_dir)
        monkeypatch.delenv("PANEL_V2_PAPER_DIR")
        assert state.get_paths().root == Path(paths_mod.PAPER_STATE_DIR)

    def test_package_has_no_live_channel_tokens(self):
        """纸面包内任何 .py 都不得出现真实行情/交易库关键字（硬约束第 1 条）。

        扫描范围 = app/core/paper 下**全部 .py**（含本测试文件自己）。
        """
        offenders = _scan_package_for_live_tokens()
        assert offenders == [], "纸面通道出现真实交易/行情库关键字：" + "; ".join(offenders)


    def test_reset_to_initial(self):
        """写几笔后 reset_to_initial → 回到 50 万空仓，旧账本进 backups/reset_*。"""
        self.broker.order("601985.SH", 1000, 10.05, side="buy", bar=BAR)
        assert self._acct()["positions"], "前置：应已建仓"
        res = state.reset_to_initial(self.state_dir, initial_cash=500000.0)
        acct = self._acct()
        assert acct["cash"] == 500000.0
        assert acct["positions"] == {}
        assert acct["initial_cash"] == 500000.0
        assert acct["realized_pnl"] == 0.0
        assert self._trades() == []
        # 旧账本已归档
        assert res["archived"], "reset 必须归档旧账本"
        backup_dir = Path(res["backup_dir"])
        assert backup_dir.exists()
        assert backup_dir.parent == Path(self.paths.backups)
        assert backup_dir.name.startswith("reset_")
        assert "paper_account.json" in res["archived"]
        assert (backup_dir / "paper_account.json").exists()
        # 归档副本里还是旧内容（50 万 - 买入额 - 费用）
        old = state.read_json(str(backup_dir / "paper_account.json"), {})
        assert old["positions"].get("601985.SH", {}).get("qty") == 1000


# ---------------------------------------------------------------- 任务书 §4 指定的用例名
# test_no_<真实行情库>_import：用字符串拼接后动态挂进模块命名空间，保证本测试文件自身
# 不含那四类关键字字面量（否则会被自己的全目录扫描命中）。pytest 按模块命名空间收集，
# 动态挂上的 test_ 名字同样能被 -k 过滤与全量运行选中。
def _no_live_channel_import_case() -> None:
    """模块级用例（无 self / fixture 依赖）：纸面包内四类关键字零命中。"""
    offenders = _scan_package_for_live_tokens()
    assert offenders == [], "纸面通道出现真实交易/行情库关键字：" + "; ".join(offenders)


globals()["test_no_" + "xt" + "quant" + "_import"] = _no_live_channel_import_case
