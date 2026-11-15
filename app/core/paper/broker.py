# -*- coding: utf-8 -*-
"""app/core/paper/broker.py · 纸面下单路由（paper-only，4.1.2 vendoring）

【来源】旧 engine/core/paper_broker.py 的 PaperBroker 逐字移植（2026-10-23 任务书 4.1.2）。
口径（撮合规则 / 费用 / 幂等 / 补账）一行不改，只做「切断旧依赖」的改造，见文末
「本版改造点」清单。

硬约束：**只服务纸面**。本模块不 import 任何真实行情/交易库，不存在任何真实下单调用路径
——任何 order 调用只产生纸面记录（账本落盘见 app/core/paper/state.py）。

撮合规则（用当分钟 OHLC 判断成交可行性）：
- 限价买：分钟 low ≤ 限价 → 成交；成交价 = open（若 open ≤ 限价，跳空低开按开盘价）否则 = 限价
- 限价卖：分钟 high ≥ 限价 → 成交；成交价 = open（若 open ≥ 限价）否则 = 限价
- 市价（§14.4 对手方最优）：按分钟 open 成交
- 部分成交模拟：partial_ratio_override 设定确定性比例（回放测试用）；
  否则按当分钟成交量折算流动性（量 < 订单股数 → 部分成交），量足够 → 全额成交
- 撤单：cancel(order_id, retry_market=True) 模拟市价重试（§3 撤单失败市价重试1次）

落盘（本版唯一落盘 = 纸面账本，走 state.atomic_write_json）：
- paper_trades.json —— 滚动交易记录（§6 凯利统计来源 / §8 对账输入）
- paper_account.json —— 独立账本（每笔成交原子更新）
（旧版还双写 data/panel/intraday.json signals 段；4.1.2 起**删除**，信号流水归 4.1.3 的
 core feed + paper 插件，纸面模块不再碰生产 panel JSON。）

T6 信号流水溯源（2026-08-27）：record 带 batch_seq（分批建仓第几批，来自
battle_plan.batch.slices[].seq，auto_buy._execute_trigger 下发）；name 缺失时
由注入的 name_resolver 回填（本版不读作战表，缺省返回空、落盘侧兜底 ts_code）。

Q1 共享层对接（2026-09-12）：本类与订单管理链组成订单管理——超时撤单/重挂/OCO 互锁策略
在共享层（纸面/实盘共用一套状态机），本类只承担 paper 通道（order/cancel/query_align/
get_trade 同形接口）；cancel 对 FILLED 单返回 False（已成交不可撤，防御层防撤单覆盖错账）。

P4 幂等终闸（2026-09-19）：record 新增 idem_key（通道/批次粒度判重键，由调用方按来源生成：
量比=VOL_RATIO:{ts_code}、择机=WATCH:{watch_id}:{seq}、预案=PLAN:{plan_id}:{seq}、
combo=COMBO:{ts_code}:{batch}、尾盘=EOD:{ts_code}；禁止价格判重，低吸/量比分批同票多笔合法）。
order() 在账本统一锁内查「同 idem_key 当日已成交（FILLED/PARTIAL）→ 新单 REJECTED
不入撮合不入账」——两实例同 tick 并发下单在锁内串行化，必有一方命中
（堵 2026-09-18 603009 量比双买形态，当日两笔均即时 FILLED 不在 P4③ 活跃单口径内的逃逸缺口）。
仅当日（跨日自动放行，量比 bought 跨日重置语义一致）；判重键不落日期，按 created_at 日期比对。

本版改造点（相对旧 engine/core/paper_broker.py，逐条对应任务书 §3.2）：
1. 路径全部来自 state.get_paths(state_dir)（PAPER_STATE_DIR），删掉对旧 common.paths 的
   PAPER_TRADES_DEFAULT / PAPER_ACCOUNT_DEFAULT / INTRADAY_DEFAULT 依赖；
2. 删除 write_intraday_signal / _write_intraday_signal_locked / _load_intraday / _day_summary
   （不再双写 data/panel/intraday.json），这些方法不导出；
3. 删除 close_prices_from_db（track_data 取价）→ close_price_provider 缺省 = 空字典价源；
4. 删除 name_from_battle_plan（读作战表 + common.names）→ name_resolver 注入，缺省返回空串；
5. _alert 改为 self.alert_sink(level, event, title, body)，缺省只写 audit，
   不再 import common.progress / push_queue；
6. 所有写文件走 state.atomic_write_json / state.locked（本模块内无 open(...,"w")）；
7. 删除 1041 行之后的 _real_positions_map / _paper_positions_map / protect_scope_positions /
   protection_coverage / unprotected_holdings / protect_backfill_hook（自动引擎专用，本版不需要）。
"""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.paper import state

__all__ = [
    "ACTIVE_ORDER_STATUSES",
    "COMMISSION_RATE",
    "DEFAULT_ORDER_PREFIX",
    "INITIAL_CASH",
    "KIND_ORDER_PREFIX",
    "MIN_COMMISSION",
    "PaperBroker",
    "STAMP_RATE",
    "TRANSFER_RATE",
    "calc_fee",
    "is_non_equity_collateral",
]

# 纸面初始资金（元；2026-08-24 主人定 50 万）
INITIAL_CASH = state.INITIAL_CASH

# 交易费用（2026-10-21 主人定：佣金万 1、免 5）
#   佣金：成交额 × 0.01%（万 1；同日曾短暂改 1% 方便口算，已按主人口径回调）
#   印花税：卖出 × 0.05%（万 5，2023-08-28 后单边）
#   过户费：沪深双向 × 0.001%（万 0.1）
#   合计：买万 1.1，卖万 6.1
COMMISSION_RATE = 0.0001
MIN_COMMISSION = 0.0
STAMP_RATE = 0.0005
TRANSFER_RATE = 0.00001

# 账本锁等待预算（秒）：超时 fail-open + 告警留痕，绝不死等死锁
LEDGER_LOCK_TIMEOUT_S = 10.0


def calc_fee(amount: float, side: str) -> float:
    """单笔交易费用（元，四舍五入到分）。amount=成交金额；side=BUY/SELL（大小写均可）。"""
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    if amt <= 0:
        return 0.0
    fee = amt * COMMISSION_RATE + amt * TRANSFER_RATE
    if str(side).upper() == "SELL":
        fee += amt * STAMP_RATE
    if fee < MIN_COMMISSION:
        fee = MIN_COMMISSION
    return round(fee, 2)


# ---- P4 撮合层加固（2026-08-28 工单 v2-P4）----
ACTIVE_ORDER_STATUSES = ("REPORTED", "PARTIAL")       # 活跃单（未终态）：重复单检测口径
KIND_ORDER_PREFIX = {"STOP": "PS", "TAKE_PROFIT": "PT"}  # 单号分命名空间（①）：其余 kind → PP
DEFAULT_ORDER_PREFIX = "PP"
_PROTECTED_SOURCES = ("manual_protect", "auto_backfill")  # 保护单来源标记（②）
# 存心无锚（主人裁定不管的票，非缺陷）：保护覆盖巡检单列，防后续班次误报为缺口。
# 主人 2026-09-02 裁定：通信ETF 159583 静态持有，破 1.583 仅记录不催。
# 本版没有保护覆盖巡检（随 protect_* 系列一并删除），此处保留裁定原文备查，
# 将来做持仓守护（4.1.5+）时直接沿用，不许重新发明口径。
INTENTIONAL_NO_ANCHOR: Dict[str, str] = {
    "159583.SZ": "主人 2026-09-02 裁定：静默持有，不管；破 1.583 仅记录不催（存心无锚，非缺口）",
}
# 非权益抵押位（债券通用质押式回购的标准券抵押位等）：**无价格风险、本不该要求止损锚**，
# 属**工具属性分类**（不是「存心不管」的裁定，也不是缺口）。工单 20260922-1815 实证：
# 2026-09-22 15:06 逆回购 204001.SH SELL 1580@1.43 成交后，柜台侧生成 888880.SH「新标准券」
# 抵押位（cost_price=100 面值、非权益）——被保护覆盖误计 missing_anchor；trade_mode 切 live 后
# 会让 watchdog protection_cover 开盘即 STALE 刷伪告警。分类时单列排除类，防误报缺口。
# 代码口径：上交所新标准券 888880.SH、深交所债券回购标准券 131990.SZ（原国债/企业债标准券合并）；
# 另防御 沪市 204xxx / 深市 1318xx 通用质押式回购品种代码族（持仓表中通常不出现，出现同样无锚需求）。
NON_EQUITY_COLLATERAL_CODES = ("888880.SH", "131990.SZ")
NON_EQUITY_COLLATERAL_PREFIXES = ("204", "1318")


def is_non_equity_collateral(code: str) -> bool:
    """非权益抵押位判据（唯一源）：逆回购/标准券类工具，无价格风险、不需止损锚。
    保护覆盖分类、缺锚闸、补账钩子共用本函数，禁止各消费端自建名单（口径唯一源原则）。"""
    c = str(code or "").upper()
    if not c:
        return False
    if c in NON_EQUITY_COLLATERAL_CODES:
        return True
    return c.split(".", 1)[0].startswith(NON_EQUITY_COLLATERAL_PREFIXES)


@contextmanager
def _noop_context():
    """锁不可用时的直通上下文（P2-3 fail-open 兜底，不阻断写入）。"""
    yield


class PaperBroker:
    """纸面券商。路径/时钟可注入（测试用临时文件与假时钟）。

    独立账本（第5件，2026-08-24 主人需求）：每笔成交（FILLED/PARTIAL）后原子更新
    paper_account.json —— {initial_cash, cash, positions, realized_pnl, equity_curve, stats}。
    持仓成本=移动加权；卖出按成本价结算 realized_pnl；equity_curve 按日记录 total
    （cash + Σ(qty>0 × 当日收盘价)，收盘价缺失的票按成本价并在条目 missing 标注 ——
    2026-10-22 主人定口径，见 _marked_equity）；stats.win_rate 从已平仓记录统计（前端胜率展示）。

    close_price_provider：估值价源注入口（codes, day → {code: price}）。缺省 = 空字典
    （全部按成本价降级并标 missing）；生产由 4.1.3 的 core 服务注入统一出口取价。
    测试注入确定性价源，零生产 IO。

    name_resolver：标的名称解析注入口（code → name）。缺省 = 空串（不编造；
    落盘侧兜底用 ts_code，前端只显示代码）。旧版直接读作战表 + 本地库，本版改为注入。

    alert_sink：撮合层告警出口（level, event, title, body）。缺省只写 audit.log；
    旧版走 common.progress + push_queue，本版全部断开（纸面模块不得反向依赖旧工程）。
    """

    def __init__(self, state_dir: Optional[Any] = None,
                 paper_trades_path: Optional[Any] = None,
                 paper_account_path: Optional[Any] = None,
                 initial_cash: float = INITIAL_CASH,
                 clock: Optional[Callable[[], float]] = None,
                 partial_ratio_override: Optional[float] = None,
                 seq_start: int = 1,
                 close_price_provider: Optional[Callable[[List[str], str], Dict[str, float]]] = None,
                 name_resolver: Optional[Callable[[str], str]] = None,
                 alert_sink: Optional[Callable[[str, str, str, str], None]] = None):
        # 路径口径：state_dir 决定账本目录（PAPER_STATE_DIR 或 PANEL_V2_PAPER_DIR），
        # 显式单文件路径覆盖对应那一项（更具体，单测直接指文件时用）。
        self._paths = state.get_paths(state_dir)
        self.state_dir = str(self._paths.root)
        if paper_trades_path:
            self.paper_trades_path = str(paper_trades_path)
            # 账本缺省跟随 trades 同目录（测试直接指 tmp 文件时账本自动隔离，零改调用方）
            self.paper_account_path = str(
                paper_account_path
                or os.path.join(os.path.dirname(self.paper_trades_path), state.ACCOUNT_NAME))
        else:
            self.paper_trades_path = str(self._paths.trades)
            self.paper_account_path = str(paper_account_path or self._paths.account)
        self.initial_cash = float(initial_cash)
        self.clock = clock or time.time
        self.partial_ratio_override = partial_ratio_override  # 0<r<1 强制部分成交比例（测试用）
        # 估值价源（缺省 = 空字典价源：全部按成本价降级，不读取任何外部库/文件）
        self.close_price_provider = close_price_provider or (lambda codes, day: {})
        # 名称解析（缺省 = 空串，不编造）
        self.name_resolver = name_resolver or (lambda code: "")
        # 告警出口（缺省 = 只落 audit.log）
        self.alert_sink = alert_sink
        self._seq = seq_start  # 兼容旧字段：PP 前缀起始序号（seq_start 语义不变）
        self._seqs: Dict[str, int] = {}          # P4①：分前缀计数器（惰性初始化，含磁盘地板）
        self._seq_seeded: Dict[str, bool] = {}   # P4①：前缀是否已扫盘定地板（旧号永不复用）

    # ---------------- 下单（同形接口 order(code, volume, price, side, order_type)） ----------------
    def order(self, code: str, volume: int, price: float, side: str = "buy",
              order_type: str = "limit", bar: Optional[Dict[str, Any]] = None,
              **meta) -> Dict[str, Any]:
        """纸面下单。返回 {"order_id", "status", "filled_qty", "filled_price", "simulation", "record"}。
        meta 可带：signal_id / name / kind / channel / source / trigger_time / trigger_type / condition /
                  decision_reason / t1_alert / note / is_stop（P4②：保护单标记）。绝不连柜台。
        P4③ 重复单检测：同 ts_code+同 side+同价活跃单已存在 → 新单 REJECTED（挂起）+ 告警 + 留痕。
        20260910-1048：单号分配 + 重复检测 + 入账三步收进同一把账本锁内原子完成——此前单号
        惰性扫盘定地板与重复检测都在锁外，多进程并发首单各自扫到同一地板 → 同号碰撞 →
        _upsert_trade 按 paper_order_id 互覆（两笔批次记账共用一笔实单，春风动力 seq2
        无单记账实证），且后单同价重复检测被竞态跳过。"""
        kind = str(meta.get("kind") or "BUY")
        side_u = side.upper()
        raw_bs = meta.get("batch_seq")
        record = {
            "paper_order_id": None,  # 锁内分配（见上）
            "signal_id": meta.get("signal_id"),
            "ts_code": code,
            "name": meta.get("name", ""),
            "side": side_u,
            "order_type": order_type,
            "limit_price": round(float(price), 3),
            "qty": int(volume),
            "filled_qty": 0,
            "filled_price": None,
            "status": "REPORTED",
            "kind": meta.get("kind", "BUY"),
            "channel": meta.get("channel", "A"),
            "source": meta.get("source", ""),
            "is_stop": bool(meta.get("is_stop")),  # P4②：保护单标记（止盈/止损/人工保护）
            "pre_hung": bool(meta.get("pre_hung")),  # L1 2026-09-03：真实预挂阶段标记（缺省 False=即时撮合）
            "batch_seq": (int(raw_bs) if raw_bs is not None else None),  # T6：分批建仓批次序号
            "idem_key": meta.get("idem_key"),  # P4 幂等终闸判重键（2026-09-19；通道/批次粒度，None=不判重）
            "trigger_time": meta.get("trigger_time"),
            "matched_bar": None,
            "simulation": None,
            "reject_reason": None,
            "pnl": None,
            "closed_at": None,
            "partial_ratio": meta.get("partial_ratio"),  # 单笔部分成交比例覆盖（测试/回放用）
            "created_at": self._now_str(),
        }
        with self._ledger_lock(self.paper_trades_path):
            order_id = self._next_order_id(kind)  # 惰性扫盘定地板（锁内，跨进程互斥）
            record["paper_order_id"] = order_id
            # T6：name 缺失 → name_resolver 回填（不编造，解析失败保持空、落盘侧兜底 ts_code）
            if not record["name"]:
                try:
                    record["name"] = str(self.name_resolver(code) or "")
                except Exception:  # noqa: BLE001 - 名称解析失败不阻断下单
                    record["name"] = ""
            # T+1 只在手工下单层强制（orders 读账本 available_qty）；此处只记 today_bought，
            # 不在撮合层硬拒——自动链/回放/单测的纸面卖出依赖既有语义，改这里会把 OCO/止损链全打崩。
            sim = self._simulate(record, bar)
            record["simulation"] = sim
            if sim["fill"]:
                record["status"] = "PARTIAL" if sim["partial"] else "FILLED"
                record["filled_qty"] = sim["fill_qty"]
                record["filled_price"] = round(sim["fill_price"], 3)
                record["matched_bar"] = (bar or {}).get("ts")
            else:
                record["status"] = "REPORTED"  # 挂单未成交（等状态机/后续分钟）
            # P4③ 重复单检测（锁内读）：同票同向同价活跃单已存在 → 后入挂起（REJECTED+留痕），不接撮合不入账
            dup = self._find_duplicate_active(record)
            if dup is not None:
                record["status"] = "REJECTED"
                record["reject_reason"] = f"duplicate_active_order: {dup.get('paper_order_id')}（同票同向同价活跃单已存在，后入挂起）"
                self._upsert_trade_locked(record)
                self._alert("warn", "ORDER_REJECTED", f"重复单挂起：{record['ts_code']}",
                            f"新单 {order_id} 与活跃单 {dup.get('paper_order_id')} 同票同向同价"
                            f"（{record['ts_code']} {side_u} @{record['limit_price']}），已挂起不入撮合")
                return {"order_id": order_id, "status": record["status"],
                        "filled_qty": 0, "filled_price": None,
                        "simulation": {"fill": False, "partial": False, "fill_qty": 0,
                                       "fill_price": None, "rule": "duplicate_active", "fake_fill": False},
                        "record": record, "rejected": True}
            # P4 幂等终闸（2026-09-19）：同票同通道（idem_key）当日已成交 → 后单 REJECTED，
            # 不入撮合不入账。在统一账本锁内原子判——两实例同 tick 并发下单在此串行化，
            # 后到者必命中先到者的 FILLED 记录（调用方前置预查为快检，本处为权威终闸）。
            if record.get("idem_key"):
                dup_fill = self._find_duplicate_filled_today(record)
                if dup_fill is not None:
                    record["status"] = "REJECTED"
                    record["reject_reason"] = (
                        f"duplicate_filled_today: {dup_fill.get('paper_order_id')}"
                        f"（同票同通道当日已成交，幂等终闸）")
                    self._upsert_trade_locked(record)
                    self._alert("warn", "ORDER_REJECTED", f"幂等终闸拒单：{record['ts_code']}",
                                f"新单 {order_id} 与当日成交 {dup_fill.get('paper_order_id')} 同票同通道"
                                f"（idem_key={record['idem_key']}），已拒绝不入撮合不入账（P4 幂等终闸）")
                    return {"order_id": order_id, "status": record["status"],
                            "filled_qty": 0, "filled_price": None,
                            "simulation": {"fill": False, "partial": False, "fill_qty": 0,
                                           "fill_price": None, "rule": "duplicate_filled_today",
                                           "fake_fill": False},
                            "record": record, "rejected": True}
            if not self._upsert_trade_locked(record):
                # P4② 保护单防覆盖：写入被拒（旧保护单原样保留），本单不入账
                record["status"] = "REJECTED"
                record["reject_reason"] = "protected_order_overwrite_denied"
                return {"order_id": order_id, "status": record["status"],
                        "filled_qty": 0, "filled_price": None,
                        "simulation": {"fill": False, "partial": False, "fill_qty": 0,
                                       "fill_price": None, "rule": "protected_overwrite_denied", "fake_fill": False},
                        "record": record, "rejected": True}
            self._bookkeeping_locked(record, bar)  # 第5件：成交记账（FILLED/PARTIAL 才动账；已在统一锁内）
        # 旧版此处调 write_intraday_signal 双写 data/panel/intraday.json；
        # 4.1.2 起**删除**（纸面模块不写生产 panel，信号流水归 4.1.3 的 core feed + paper 插件）。
        return {"order_id": order_id, "status": record["status"],
                "filled_qty": record["filled_qty"], "filled_price": record["filled_price"],
                "simulation": sim, "record": record}

    # ---------------- 撮合模拟（当分钟 OHLC） ----------------
    def _simulate(self, record: Dict[str, Any], bar: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if bar is None:
            return {"fill": False, "partial": False, "fill_qty": 0, "fill_price": None,
                    "rule": "no_bar", "fake_fill": False}
        qty = record["qty"]
        price = record["limit_price"]
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        side, otype = record["side"], record["order_type"]

        if otype == "market":
            fillable = True
            fill_price = o
            rule = "market_open"
        elif side == "BUY":
            fillable = l <= price
            fill_price = o if o <= price else price
            rule = "buy_low_le_price"
        else:
            fillable = h >= price
            fill_price = o if o >= price else price
            rule = "sell_high_ge_price"

        if not fillable:
            return {"fill": False, "partial": False, "fill_qty": 0, "fill_price": None,
                    "rule": rule, "fake_fill": False,
                    "reason": f"当分钟{'low' if side == 'BUY' else 'high'}未触及限价"}
        # 部分成交模拟（优先级：单笔 partial_ratio > 构造参数 partial_ratio_override）
        ratio = record.get("partial_ratio")
        if ratio is None:
            ratio = self.partial_ratio_override
        if ratio is not None and 0 < ratio < 1:
            fill_qty = max(100, int(qty * ratio))
            partial = fill_qty < qty
        else:
            liquidity = float(bar.get("volume") or 0.0) * 100.0  # 分钟量（手→股近似）
            if liquidity >= qty:
                fill_qty, partial = qty, False
            elif liquidity > 0:
                fill_qty, partial = int(liquidity), True
            else:
                fill_qty, partial = qty, False  # 无量视为全额（分钟量缺失的容错）
        return {"fill": True, "partial": partial, "fill_qty": int(fill_qty),
                "fill_price": round(fill_price, 3), "rule": rule, "fake_fill": False}

    # ---------------- 撤单（§3：失败→市价重试1次；retry_market 由状态机驱动） ----------------
    def cancel(self, order_id: str, order: Optional[Dict[str, Any]] = None,
               retry_market: bool = False, bar: Optional[Dict[str, Any]] = None) -> bool:
        """撤单模拟。retry_market=True 时模拟市价单成交（对手方最优，§14.4）。
        返回 True=撤单成功；False=失败（触发状态机市价重试/告警路径）。"""
        rec = self.get_trade(order_id)
        if rec is None:
            return False
        if rec.get("status") == "FILLED":
            # Q1 2026-09-12 防御层：已成交单不可撤（撤单把 FILLED 覆写成 CANCELLED 属错账
            # ——超时状态机/OCO 互锁与查成交推进并发时序下防双写）。返回 False=撤单失败口径，
            # 调用方（order_manager._cancel_leg 等）按失败走市价重试/告警路径。
            return False
        if retry_market:
            # 市价重试：以当分钟开盘价成交剩余量（纸面），模拟成功
            if bar is not None:
                rec["filled_qty"] = rec["qty"]
                rec["filled_price"] = round(float(bar["open"]), 3)
                rec["simulation"] = {"fill": True, "partial": False, "fill_qty": rec["qty"],
                                     "fill_price": rec["filled_price"], "rule": "market_retry",
                                     "fake_fill": False}
            rec["status"] = "CANCELLED"  # 市价重试后按已了结处理（纸面口径：撤单成功）
            rec["closed_at"] = self._now_str()
            self._upsert_trade(rec)
            self._bookkeeping(rec, bar)  # 第5件：市价重试成交同样记账
            return True
        rec["status"] = "CANCELLED"
        rec["closed_at"] = self._now_str()
        self._upsert_trade(rec)
        return True

    # ---------------- 超时对齐（§4：以柜台查询结果为准） ----------------
    def query_align(self, order_id: str) -> Dict[str, Any]:
        """纸面对齐：以本地纸面记录为"柜台口径"返回。"""
        rec = self.get_trade(order_id)
        if rec is None:
            return {"status": "REPORTED", "filled_qty": 0, "filled_price": None,
                    "reject_reason": "查无此单"}
        return {"status": rec["status"], "filled_qty": rec["filled_qty"],
                "filled_price": rec["filled_price"],
                "reject_reason": rec.get("reject_reason")}

    # ---------------- 记录读写 ----------------
    def _next_order_id(self, kind: str = "BUY") -> str:
        """P4① 单号分命名空间 + 旧号永不复用：STOP→PS / TAKE_PROFIT→PT / 其余→PP。
        各前缀独立序号，首次使用时扫盘定地板（现存同前缀最大序号，重启/多实例不重号——
        事故根因：止盈/止损单 seq 重置后与旧单同号，被 _upsert_trade 按 paper_order_id 覆盖）。"""
        now = time.localtime(self.clock())
        stamp = time.strftime("%Y%m%d", now)
        prefix = KIND_ORDER_PREFIX.get(kind, DEFAULT_ORDER_PREFIX)
        if prefix not in self._seq_seeded:
            self._seqs[prefix] = max(self._max_seq_on_disk(prefix),
                                     self._seq if prefix == DEFAULT_ORDER_PREFIX else 0)
            self._seq_seeded[prefix] = True
        self._seqs[prefix] += 1
        self._seq = self._seqs[prefix] if prefix == DEFAULT_ORDER_PREFIX else self._seq
        return f"{prefix}{stamp}-{self._seqs[prefix]:04d}"

    def _max_seq_on_disk(self, prefix: str) -> int:
        """现存 trades 中同前缀单号的最大序号（无 → 0）。保证旧号永不复用。"""
        pat = re.compile(rf"^{prefix}\d{{8}}-(\d+)$")
        best = 0
        for t in self.load_trades():
            m = pat.match(str(t.get("paper_order_id") or ""))
            if m:
                best = max(best, int(m.group(1)))
        return best

    def _find_duplicate_active(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """P4③：同 ts_code+同 side+同价（精确相等）活跃单（未终态）→ 返回旧单，无 → None。"""
        for t in self.load_trades():
            if t.get("paper_order_id") == record.get("paper_order_id"):
                continue
            if t.get("status") not in ACTIVE_ORDER_STATUSES:
                continue
            if (t.get("ts_code") == record.get("ts_code")
                    and t.get("side") == record.get("side")
                    and t.get("limit_price") is not None
                    and float(t.get("limit_price")) == float(record.get("limit_price"))):
                return t
        return None

    def _find_duplicate_filled_today(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """P4 幂等终闸（2026-09-19）：同 idem_key 当日已成交（FILLED/PARTIAL）→ 返回旧单，无 → None。
        判重键=通道/批次粒度 idem_key（禁止价格判重——低吸/量比分批同票多笔合法，价格判重会误伤）；
        仅比对 created_at 为当日（跨日自动放行，与 vol_ratio bought 跨日重置语义一致）。
        调用点在 order() 统一账本锁内（锁内读，与 _find_duplicate_active 同纪律）。"""
        today = self._now_str()[:10]
        for t in self.load_trades():
            if t.get("paper_order_id") == record.get("paper_order_id"):
                continue
            if t.get("idem_key") != record.get("idem_key"):
                continue
            if t.get("status") not in ("FILLED", "PARTIAL"):
                continue
            if str(t.get("created_at") or "")[:10] != today:
                continue
            return t
        return None

    @staticmethod
    def _is_protected(trade: Dict[str, Any]) -> bool:
        """P4②：保护单判定（is_stop 标记 或 人工/补账保护来源）。"""
        return bool(trade.get("is_stop")) or str(trade.get("source") or "") in _PROTECTED_SOURCES

    def _alert(self, level: str, event: str, title: str, body: str) -> None:
        """撮合层告警（推送降级：缺省只落 audit.log，不抛不阻断）。

        旧版走 common.progress.append_progress + common.push_queue.push_event；本版
        alert_sink 由装配方注入（service.py 缺省只 audit），纸面模块不反向依赖旧工程。
        """
        try:
            state.audit("alert.%s.%s" % (str(level or "info"), str(event or "")),
                        "%s · %s" % (title, body),
                        root=os.path.dirname(self.paper_account_path) or None)
        except Exception:  # noqa: BLE001 - 留痕失败不影响主流程
            pass
        if self.alert_sink is not None:
            try:
                self.alert_sink(level, event, title, body)
            except Exception:  # noqa: BLE001 - 推送失败不阻断（同 progress 降级精神）
                pass

    def _now_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.clock()))

    def load_trades(self) -> List[Dict[str, Any]]:
        """读成交流水（宽容：文件缺失/损坏 → 空列表）。"""
        doc = state.read_json(self.paper_trades_path, None)
        if not isinstance(doc, dict):
            return []
        trades = doc.get("trades") or []
        return trades if isinstance(trades, list) else []

    def get_trade(self, order_id: str) -> Optional[Dict[str, Any]]:
        for t in self.load_trades():
            if t.get("paper_order_id") == order_id:
                return t
        return None

    @contextmanager
    def _ledger_lock(self, target_path: Optional[str] = None):
        """账本 RMW 跨进程排他锁上下文（P2-3，嗅嗅审查 2026-09-01）。
        多进程写方（下单/对账/日终重估/补账钩子）并发 read-modify-write
        同一账本 JSON，交错会丢更新错账。锁载体 = <账本锚点>.lock（vendored filelock）。
        ★ 统一锁锚点 = paper_account_path（不随被写文件变化）：upsert(paper_trades) 与
        bookkeeping/mtm(paper_account) 必须共用同一把锁，否则 trades/account 双锁跨文件
        竞态在 Windows 上仍会丢更新/PermissionError。锁超时 fail-open + 告警留痕。
        """
        anchor = str(self.paper_account_path or target_path or self.paper_trades_path)
        entered = False
        try:
            with state.locked(anchor, timeout_s=LEDGER_LOCK_TIMEOUT_S):
                entered = True
                yield
        except state.FileLockTimeout:
            if entered:      # 临界区内抛的 FileLockTimeout 不是本层的（原样上抛）
                raise
            # F2（嗅嗅审查 2026-09-02）：锁超时 fail-open 不得静默——补留痕（锚点+等待时长），
            # 与 atomic_write_json「绝不静默吞错账」精神对齐（_alert 走 audit 通道）。
            self._alert("warn", "LEDGER_LOCK_TIMEOUT",
                        f"账本锁超时 fail-open：anchor={os.path.basename(anchor)} "
                        f"wait={LEDGER_LOCK_TIMEOUT_S:.0f}s",
                        "降级直通，并发写方可能丢更新（下轮重试）")
            yield

    def _upsert_trade(self, record: Dict[str, Any]) -> bool:
        """按 paper_order_id upsert。P4②：目标为保护单时任何改写业务字段的覆盖 → 拒绝
        （拒绝 + 告警 + 原单留痕，返回 False）；同单状态推进（业务字段一致）放行。
        P2-3：整段 RMW 加跨进程锁，防多写方并发丢更新。"""
        with self._ledger_lock(self.paper_trades_path):
            return self._upsert_trade_locked(record)

    def _upsert_trade_locked(self, record: Dict[str, Any]) -> bool:
        """（锁内实现，P2-3 拆出保持缩进可读）落盘走 state.atomic_write_json。"""
        trades = self.load_trades()
        replaced = False
        for i, t in enumerate(trades):
            if t.get("paper_order_id") == record["paper_order_id"]:
                if self._is_protected(t):
                    mutated = (t.get("side") != record.get("side")
                               or float(t.get("limit_price") or 0) != float(record.get("limit_price") or 0)
                               or int(t.get("qty") or 0) != int(record.get("qty") or 0)
                               or t.get("kind") != record.get("kind"))
                    if mutated:
                        self._alert("error", "ORDER_REJECTED",
                                    f"保护单覆盖拒绝：{t.get('paper_order_id')}（{t.get('ts_code')}）",
                                    f"保护单（kind={t.get('kind')} source={t.get('source') or '-'} "
                                    f"{t.get('side')} @{t.get('limit_price')} ×{t.get('qty')}）拒绝被改写为 "
                                    f"（kind={record.get('kind')} {record.get('side')} "
                                    f"@{record.get('limit_price')} ×{record.get('qty')}），原单保留")
                        return False
                trades[i] = record
                replaced = True
                break
        if not replaced:
            trades.append(record)
        doc = {"schema_version": "1.0.0", "updated_at": self._now_str(), "trades": trades}
        state.atomic_write_json(self.paper_trades_path, doc,
                                audit_event="trades.upsert",
                                detail="%s %s %s" % (record["paper_order_id"], record.get("status"),
                                                     record.get("ts_code")))
        return True

    # ---------------- 独立账本（第5件：paper_account.json，每笔成交原子更新） ----------------
    def _roll_day(self, acct: Dict[str, Any]) -> bool:
        """T+1 日切：跨日后昨日买入转为可卖（available_qty=qty）。返回是否发生日切。

        账本字段：positions[code] = {qty, cost_price, today_bought, available_qty}，
        trade_date = 最近一次结算日（YYYY-MM-DD）。旧账本无这些字段→ today_bought=0，全部可卖。
        """
        today = self._now_str()[:10]
        if acct.get("trade_date") == today:
            return False
        for p in (acct.get("positions") or {}).values():
            if not isinstance(p, dict):
                continue
            p["today_bought"] = 0
            p["available_qty"] = int(p.get("qty") or 0)
        acct["trade_date"] = today
        return True

    def roll_day(self, date: Optional[str] = None) -> None:
        """手动把「当日买入」结算为可卖（日终/测试用；date 参数保留兼容，忽略）。"""
        with self._ledger_lock(self.paper_account_path):
            acct = self._load_account()
            for p in (acct.get("positions") or {}).values():
                if isinstance(p, dict):
                    p["today_bought"] = 0
                    p["available_qty"] = int(p.get("qty") or 0)
            acct["trade_date"] = self._now_str()[:10]
            self._save_account(acct)

    def _load_account(self) -> Dict[str, Any]:
        doc = state.read_json(self.paper_account_path, None)
        acct = doc if isinstance(doc, dict) and "cash" in doc else None
        if acct is None:
            # 账本缺失/损坏：自动初始化（绝不报错/拒单，主人需求：首单不因账本缺失失败）
            acct = {"initial_cash": self.initial_cash, "cash": self.initial_cash,
                    "positions": {}, "realized_pnl": 0.0, "equity_curve": [
                        {"date": self._now_str()[:10], "total": self.initial_cash}],
                    "stats": {"win_rate": 0.0, "closed_trades": 0, "wins": 0},
                    "updated_at": self._now_str()}
        self._roll_day(acct)      # T+1：跨日自动解锁昨日买入
        return acct

    def _save_account(self, acct: Dict[str, Any]) -> None:
        acct["stats"] = self._win_stats()  # 胜率/平仓数（从纸面成交记录统计）
        state.atomic_write_json(self.paper_account_path, acct,
                                audit_event="account.save",
                                detail="cash=%.2f positions=%d"
                                       % (float(acct.get("cash") or 0),
                                          len(acct.get("positions") or {})))

    def _bookkeeping(self, record: Dict[str, Any], bar: Optional[Dict[str, Any]] = None) -> None:
        """成交记账：FILLED/PARTIAL 才动账（现金/持仓/已实现盈亏/权益曲线）。
        买入=现金扣减+持仓（移动加权成本）；卖出=现金增加+按成本结算 realized_pnl。
        equity_curve 按日记录 total = cash + Σ(qty>0 × 当日收盘价)（2026-10-22 主人定口径，
        见 _marked_equity）；收盘价缺失的票按成本价并在条目 missing 标注。
        P2-3：整段 RMW（_load_account→改→_save_account）加跨进程锁，防多写方并发丢更新。"""
        with self._ledger_lock(self.paper_account_path):
            return self._bookkeeping_locked(record, bar)

    @staticmethod
    def _same_day_marks(acct: Dict[str, Any], day: str) -> Optional[Dict[str, float]]:
        """当日已有 close 口径条目带出的已确认价（供同日后续写入复用）；无/非 close → None。"""
        for c in reversed(acct.get("equity_curve") or []):
            if not isinstance(c, dict) or c.get("date") != day:
                continue
            marks = c.get("marks")
            if c.get("mtm_source") == "close" and isinstance(marks, dict) and marks:
                return marks
            return None
        return None

    def _marked_equity(self, acct: Dict[str, Any], day: str,
                       closes: Optional[Dict[str, float]] = None,
                       prev_marks: Optional[Dict[str, float]] = None) -> Tuple[float, Dict[str, Any]]:
        """账本权益 = cash + Σ(qty>0 × 当日收盘价) —— **口径唯一实现**（成交记账与日终重估共用）。

        2026-10-22 修（主人定）：旧口径用持仓**成本价**当市值，数学上恒等于
        initial_cash + realized_pnl ⇒ 持仓期净值成直线、与真实总资产偏差 = 浮亏合计
        （10-21 实证：474,361.40 vs 真实 474,015.61，虚高 345.79）。

        - closes=None：自动取价（close_price_provider；本版缺省 = 空字典价源，即全量成本降级）
        - closes={}  ：显式"无任何收盘价"（全部按成本价降级，供日终重估显式注入口）
        - prev_marks：当日已有 close 口径条目带出的已确认价（成交记账传入）——
          盘后成交时当日 kline 可能还未入库，若整批降级成本价，会把 15:05 已重估好的
          当日条目打回假线（10-21 旧记录缺 mtm 三字段即此形态）。故先用当日已确认价补缺口。
        - 仍缺价的票按成本价，并标 mtm_source=cost_fallback + missing 列票码
          （字段约定沿用 2026-08-27 起的 equity_curve 条目）
        - 条目附带 marks（本次实际采用的价），供同日后续写入复用
        - 空仓：total = cash，不取价（不发无意义的库查）

        返回 (total, meta)；meta 同时用作 equity_curve 条目附加字段。
        """
        cash = float(acct.get("cash") or 0)
        held = {str(c): p for c, p in (acct.get("positions") or {}).items()
                if isinstance(p, dict) and int(p.get("qty") or 0) > 0}
        if not held:
            return round(cash, 2), {"mtm": True, "mtm_source": "close",
                                    "missing": [], "marks": {}}
        if closes is None:
            provider = self.close_price_provider or (lambda codes, day_: {})
            try:
                raw = provider(list(held.keys()), day) or {}
                raw = dict(raw) if hasattr(raw, "items") else {}
            except Exception:  # noqa: BLE001 - 取价失败降级成本口径（记账绝不断）
                raw = {}
            closes = {}
            for k, v in raw.items():
                try:
                    if float(v) > 0:
                        closes[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
        missing: List[str] = []
        marks: Dict[str, float] = {}
        mv = 0.0
        for code, pos in held.items():
            px = closes.get(code)
            if px is None and prev_marks:
                try:
                    cand = float(prev_marks.get(code) or 0)
                except (TypeError, ValueError):
                    cand = 0.0
                if cand > 0:
                    px = cand  # 当日已确认价补齐（盘后成交不再打回假线）
            if px is None:
                px = float(pos.get("cost_price") or 0)  # 停牌/数据缺失 → 成本口径
                missing.append(code)
            else:
                marks[code] = float(px)
            mv += int(pos.get("qty") or 0) * float(px)
        missing.sort()
        meta: Dict[str, Any] = {"mtm": True,
                                "mtm_source": "close" if not missing else "cost_fallback",
                                "missing": missing, "marks": marks}
        if missing:
            meta["mtm_note"] = "收盘价缺失持仓按成本价口径估值（停牌/数据缺失）"
        return round(cash + mv, 2), meta

    def _bookkeeping_locked(self, record: Dict[str, Any], bar: Optional[Dict[str, Any]] = None) -> None:
        """（锁内实现，P2-3 拆出）"""
        if record.get("status") not in ("FILLED", "PARTIAL"):
            return
        qty = int(record.get("filled_qty") or 0)
        price = float(record.get("filled_price") or 0)
        if qty <= 0 or price <= 0:
            return
        acct = self._load_account()
        acct.setdefault("positions", {})
        code = record["ts_code"]
        if record["side"] == "BUY":
            amt = round(qty * price, 2)
            fee = calc_fee(amt, "BUY")
            record["fee"] = fee
            acct["cash"] = round(float(acct.get("cash", self.initial_cash)) - amt - fee, 2)
            pos = acct["positions"].setdefault(code, {"qty": 0, "cost_price": 0.0})
            new_qty = int(pos.get("qty", 0)) + qty
            if new_qty:
                # 成本价含买入费用（真实持仓成本口径）
                pos["cost_price"] = round(
                    (float(pos.get("cost_price", 0)) * int(pos.get("qty", 0)) + amt + fee) / new_qty, 4)
            pos["qty"] = new_qty
            pos["today_bought"] = int(pos.get("today_bought") or 0) + qty   # T+1
            pos["available_qty"] = max(0, new_qty - int(pos["today_bought"]))
        else:  # SELL（含止损卖单/做T减仓）
            pos = acct["positions"].get(code)
            pos_qty = int(pos.get("qty", 0)) if pos else 0
            _today_bought = int((pos or {}).get("today_bought") or 0)
            sell_qty = min(qty, pos_qty)  # P2-2：不超卖——无持仓/持仓不足时钳到持仓量，禁扣负
            # 注：T+1 不在撮合层拦（见 order() 注释），记 today_bought 供手工模块/面板判断
            if sell_qty > 0:
                amt = round(sell_qty * price, 2)
                fee = calc_fee(amt, "SELL")
                record["fee"] = fee
                acct["cash"] = round(float(acct.get("cash", self.initial_cash)) + amt - fee, 2)
                pnl = round((price - float(pos.get("cost_price", 0))) * sell_qty - fee, 2)
                acct["realized_pnl"] = round(float(acct.get("realized_pnl", 0.0)) + pnl, 2)
                pos["qty"] = pos_qty - sell_qty
                pos["available_qty"] = max(0, pos["qty"] - _today_bought)
                if pos["qty"] <= 0:
                    acct["positions"].pop(code, None)
                if record.get("pnl") is None:
                    record["pnl"] = pnl  # 回写成交记录（胜率统计源）
                    self._upsert_trade_locked(record)  # P2-3：已在统一锁内，走无锁内部实现防嵌套锁
        total, meta = self._marked_equity(
            acct, self._now_str()[:10], prev_marks=self._same_day_marks(acct, self._now_str()[:10]))
        acct["equity_curve"] = self._append_curve(
            acct.get("equity_curve") or [], self._now_str()[:10], total, meta=meta)
        acct["updated_at"] = self._now_str()
        self._save_account(acct)

    # ---------------- 对账补记（P9，2026-08-27 工单） ----------------
    def reconcile_fill(self, order_id: str, filled_qty: Optional[int] = None,
                       filled_price: Optional[float] = None,
                       matched_bar_ts: Optional[str] = None,
                       note: str = "") -> Dict[str, Any]:
        """对账补记/盘中执行：REPORTED（或 PARTIAL 补尾）挂单经确认实际可成交后转 FILLED 并入账。
        走 _bookkeeping 同一原子写路径（现金/持仓/权益曲线/胜率统计全套），不裸改 JSON。
        幂等：终态（FILLED/CANCELLED/REJECTED）拒绝重复补；PARTIAL 默认补剩余量，
        显式 filled_qty 超剩余量时钳到剩余量（不超卖）。返回 {changed, ...}。
        调用方：对账链路（历史分钟 bar 校验）+ 盘中保护单强制成交（2026-09-01 工单 P0-A）。"""
        rec = self.get_trade(order_id)
        if rec is None:
            raise ValueError(f"纸面单不存在：{order_id}")
        if rec.get("status") not in ("REPORTED", "PARTIAL"):
            return {"changed": False, "status": rec.get("status"), "reason": "终态，不重复补账"}
        remaining = int(rec.get("qty") or 0) - int(rec.get("filled_qty") or 0)
        qty = int(remaining) if filled_qty is None else min(int(filled_qty), remaining)
        # P2-2（嗅嗅审查 2026-09-01）：SELL 补账不超卖——可卖量受账本持仓钳制
        # （裸调用/对账链路只钳订单剩余量，不钳持仓 → 记录与实际成交失真）。
        # 防御层：_bookkeeping_locked SELL 同样 min(qty, pos_qty)。
        # 边界：仅 held>0 时钳制（真实持仓超卖拦截）；held=0（回放样本历史事件序
        # sell 先于 buy 到达，账本暂无持仓属正常态）放行入账，防误拒后续同价重挂单。
        if rec.get("side") == "SELL":
            held = int((self._load_account().get("positions") or {}).get(rec["ts_code"], {}).get("qty") or 0)
            if held > 0:
                qty = min(qty, held)
        price = float(filled_price or rec.get("limit_price") or 0)
        if qty <= 0 or price <= 0:
            raise ValueError(f"非法补账参数：qty={qty} price={price}")
        # 伪 bar 仅用于 _bookkeeping 曲线日期口径（成交价/量以参数为准，不入撮合判定链）
        pseudo_bar = {"ts": matched_bar_ts, "open": price, "high": price,
                      "low": price, "close": price, "volume": 1.0}
        # P2-2（嗅嗅审查 2026-09-01）：filled_qty 改累加——PARTIAL 补尾 50+150=200，
        # 不再覆盖写（原值 150≠实际累计 200，污染 win_stats 溯源）
        rec["filled_qty"] = int(rec.get("filled_qty") or 0) + qty
        rec["filled_price"] = round(price, 3)
        rec["matched_bar"] = matched_bar_ts
        rec["simulation"] = {"fill": True, "partial": False, "fill_qty": qty,
                             "fill_price": round(price, 3), "rule": "reconcile_fill",
                             "fake_fill": False, "note": note[:200]}
        rec["status"] = "FILLED"
        self._upsert_trade(rec)
        # 动账用本次增量 qty（不是累加 filled_qty，防 PARTIAL 补尾重复动账）
        book_rec = dict(rec)
        book_rec["filled_qty"] = qty
        self._bookkeeping(book_rec, pseudo_bar)   # 第5件：现金扣减+持仓+曲线（与正常成交同一路径）
        return {"changed": True, "order_id": order_id, "status": "FILLED",
                "filled_qty": rec["filled_qty"], "filled_price": round(price, 3)}

    @staticmethod
    def _append_curve(curve: List[Dict[str, Any]], date: str, total: float,
                      meta: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """按日权益曲线：同日追加覆盖（取最新），跨日追加。meta 可选标注（如 mtm 来源）。"""
        out = [c for c in curve if c.get("date") != date]
        entry = {"date": date, "total": total}
        if meta:
            entry.update(meta)
        out.append(entry)
        return sorted(out, key=lambda c: c["date"])[-120:]  # 保留最近 120 日

    def _win_stats(self) -> Dict[str, Any]:
        """胜率统计（已平仓卖单 pnl>0 计胜）：供面板纸面视图。"""
        sells = [t for t in self.load_trades()
                 if t.get("side") == "SELL" and t.get("pnl") is not None]
        wins = sum(1 for t in sells if float(t.get("pnl") or 0) > 0)
        return {"win_rate": round(wins / len(sells) * 100, 1) if sells else 0.0,
                "closed_trades": len(sells), "wins": wins}

    def load_account(self) -> Dict[str, Any]:
        """对外读账本（前端 /api/paper_account 数据源）。"""
        return self._load_account()

    # ---------------- 日终 mark-to-market（T2，2026-08-27 工单） ----------------
    def mark_to_market(self, close_prices: Optional[Dict[str, float]] = None,
                       date: Optional[str] = None) -> Dict[str, Any]:
        """日终重估：按当日收盘价重估持仓市值，覆盖更新 equity_curve 当日值。

        2026-10-22 起：本方法与成交记账共用 _marked_equity（同一口径实现）——此前成交记账
        用成本价另算一份，同日覆盖会把盘后重估条目打回成本口径（10-21 记录缺 mtm 字段即此形态）。

        - total = cash + Σ(qty × 收盘价)；收盘价缺失的持仓（停牌/数据缺失）
          保留成本价口径估值并在条目标注 missing
        - 幂等：total 全量重算（非增量叠加）+ _append_curve 同日覆盖，
          重复执行结果一致，绝不重复加
        - close_prices: {ts_code: 当日收盘价}；None → 走 close_price_provider 自动取价；
          传 {} → 显式全量降级成本口径（单测/降级路径用）

        返回重估摘要 {date, total, mtm_source, missing}。"""
        with self._ledger_lock(self.paper_account_path):
            return self._mark_to_market_locked(close_prices, date)

    def _mark_to_market_locked(self, close_prices, date):
        """（锁内实现，P2-3 拆出）口径与成交记账共用 _marked_equity（杜绝两条路径分叉）。"""
        acct = self._load_account()
        day = date or self._now_str()[:10]
        if close_prices is None:
            total, meta = self._marked_equity(acct, day, prev_marks=self._same_day_marks(acct, day))
        else:
            total, meta = self._marked_equity(acct, day, closes=close_prices)
        acct["equity_curve"] = self._append_curve(
            acct.get("equity_curve") or [], day, total, meta=meta)
        acct["updated_at"] = self._now_str()
        self._save_account(acct)
        return {"date": day, "total": total,
                "mtm_source": meta["mtm_source"], "missing": meta["missing"]}

    # ---------------- 便捷：保护单（§3 止损单统一挂买一价；P4② 扩止盈/保护语义） ----------------
    def stop_sell_order(self, code: str, volume: int, buy1_price: float,
                        **meta) -> Dict[str, Any]:
        """止损单：卖出方向，价格 = 买一价（§3）。仍走纸面路由。
        P4②：缺省打保护单标记（kind=STOP / is_stop=True），单号 PS 前缀，不可被 upsert 改写。"""
        meta.setdefault("kind", "STOP")
        meta.setdefault("is_stop", True)
        return self.order(code, volume, buy1_price, side="sell", order_type="limit", **meta)

    def take_profit_sell_order(self, code: str, volume: int, tp_price: float,
                               **meta) -> Dict[str, Any]:
        """止盈单（P4① 新增便捷）：卖出方向限价单，kind=TAKE_PROFIT，单号 PT 前缀。
        与止损同属保护单（is_stop=True）：不可被 upsert 改写，重复同价同向挂起。"""
        meta.setdefault("kind", "TAKE_PROFIT")
        meta.setdefault("is_stop", True)
        return self.order(code, volume, tp_price, side="sell", order_type="limit", **meta)
