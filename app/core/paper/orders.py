# -*- coding: utf-8 -*-
"""app/core/paper/orders.py · 手工下单与条件单（paper-only，4.1.2 vendoring）

【来源】旧 engine/core/manual_orders.py 的 ManualOrderService 移植（2026-10-23 任务书 4.1.2），
改名 PaperOrderService。**整类删除实盘通道**（LiveManualBroker / _place_live /
_live_account_view / live_broker() / _live 参数 / 所有 trade_mode()=="live" 分支）：
纸面通道绝不允许存在真实下单路径（硬约束第 1 条）。

【职责】把「人点一下按钮」这件事做对：校验 → 走 PaperBroker 撮合 → 回报；条件单另存一份
计划文件，由 4.1.3 的 feed 撮合循环按最新价评估触发，触发后仍回到同一条下单主链。

【边界】
- **只服务纸面**：trade_mode() 永远返回 "paper"，live_order_enabled() 永远 False。
  这两个方法保留是为了让上层面板/前端旧代码不用改（它们还要读这个口径），
  但任何"切 live"的路径在本模块已不存在。
- 不新建撮合器：成交判定、账本记账、撤单全部复用 app/core/paper/broker.PaperBroker。
- 不直连行情：报价由调用方（feed / 插件）注入 quote dict，单测可注入固定值，零网络依赖。
- 写文件只走 app/core/paper/state.py（本模块内无 open(...,"w") / os.replace）。

【口径】
- 买入必须 100 股整数倍；卖出允许零股，但不得超过「可卖数量」。
- 可卖数量 = 纸面持仓 qty − 当日买入成交量（T+1 近似；纸面账本本身不记买入日期）。
- 限价单：能立即成交就成交（对手价优先），否则留 REPORTED 由 match_pending() 按后续价格补成交。
- 市价单：按注入的最新价成交（纸面口径，无滑点模型）。
- 单笔金额超过纸面总资产 10% 只给 warning，不阻断——手工单由人决定，闸门留给自动链。
- 非交易时段：市价单拒绝（没有对手盘）；限价单可挂但不成交（行情 fresh=False 时 _resolve_fill 返回 None）。

【落盘】（全部在 paths.PAPER_STATE_DIR 下，见 state.PaperPaths）
- 委托/成交：paper_trades.json（PaperBroker 既有）
- 账户：paper_account.json（PaperBroker 既有）
- 条件单：conditional_orders.json（本模块）
- 挂单冻结：manual_orders_state.json（本模块，按上交所 3.2.10）
- 手工优先锁：manual_lock.json（本模块）
- 急停：kill_switch.json（只读判定，active=True 时一律拒单；文件缺失 = 未急停）

【条件单状态机】
  ACTIVE ──触发且下单成功──► TRIGGERED
     ├────用户撤单──────────► CANCELLED
     ├────过期（valid_until）► EXPIRED
     └────触发但下单失败────► FAILED（附 reason，可在面板看见）
  OCO：同 group 的两条，任一触发后其余 ACTIVE 腿自动 CANCELLED（reason=oco_sibling）。

【本版改造点（相对旧 manual_orders.py）】
1. 删除 LiveManualBroker 整类、_place_live、_live_account_view、live_broker()、_live 参数；
2. trade_mode() 恒 "paper"、live_order_enabled() 恒 False（不再读 engine_state 的 live 开关）；
3. 所有 _read_json / _write_json_atomic 改为 state.read_json / state.atomic_write_json；
4. 路径缺省全部来自 state.get_paths(state_dir)，删掉对旧 common.paths.STATE_DIR 的依赖；
5. now_str / to_ts_code 就地实现（原 common.tsutil 依赖切断，口径逐字照抄：
   920xxx→北交所、6/5/9→沪、0/2/3→深、4/8→北交所，非法返回 None）。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.paper import broker as broker_mod
from app.core.paper import state

__all__ = [
    "COND_ACTIVE",
    "COND_TERMINAL",
    "COND_TYPES",
    "MIN_LOT",
    "ManualOrderError",
    "PaperOrderError",
    "PaperOrderService",
    "SIGNAL_NAMES",
    "SINGLE_ORDER_CAP_RATIO",
    "manual_locked",
    "mark_manual_lock",
    "now_str",
    "to_ts_code",
]

# ------------------------------------------------------------------ 就地实现的时间/代码口径
_TS_CODE_SUFFIXES = (".SH", ".SZ", ".BJ")


def now_str() -> str:
    """统一时间口径（YYYY-MM-DD HH:MM:SS）。旧 common.tsutil.now_str 逐字照抄。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_ts_code(code) -> Optional[str]:
    """6 位数字代码 → ts_code（601985→601985.SH；300502→300502.SZ；4/8→BJ；5→SH 基金）。

    旧 common.tsutil.to_ts_code 逐字照抄（含 2026-10-07 修的 920xxx 段）：
    已是 ts_code 格式则原样返回；非法返回 None。
    """
    if not code:
        return None
    c = str(code).strip()
    if len(c) == 9 and c[:6].isdigit() and c[6:] in _TS_CODE_SUFFIXES:
        return c
    if len(c) == 6 and c.isdigit():
        # 北交所 920xxx 段（2026-10-07 修：此前 "9" 开头一律判沪市，920 被误判 .SH）
        if c[:3] == "920":
            return c + ".BJ"
        if c[0] in ("6", "5", "9"):
            return c + ".SH"
        if c[0] in ("0", "2", "3"):
            return c + ".SZ"
        if c[0] in ("4", "8"):
            return c + ".BJ"
    return None


# 条件单类型表（唯一源：类型 → 方向 / 触发语义 / 中文名）
COND_TYPES: Dict[str, Dict[str, str]] = {
    "stop_loss":     {"side": "sell", "mode": "le",      "label": "止损卖出"},
    "take_profit":   {"side": "sell", "mode": "ge",      "label": "止盈卖出"},
    "trailing_sell": {"side": "sell", "mode": "trail",   "label": "回落卖出"},
    "rebound_buy":   {"side": "buy",  "mode": "rebound", "label": "反弹买入"},
    "breakout_buy":  {"side": "buy",  "mode": "ge",      "label": "突破买入"},
    "time_order":    {"side": None,   "mode": "time",    "label": "定时委托"},
    # 2026-10-21 主人（旧系统策略迁入手工条件单）：
    "breakeven_stop": {"side": "sell", "mode": "breakeven", "label": "保本止损"},
    "ma5_break":      {"side": "sell", "mode": "ma5",       "label": "破五日线卖出"},
    "signal_buy":     {"side": "buy",  "mode": "signal",    "label": "信号买入"},
    # 2026-10-28 主人：回踩均线 / 关键K 两类低吸条件单。
    #   公式单源 = buy_module.signals.support_low_buy.select_levels（作战表关键位同一套）：
    #   黄线=(MA14+MA28+MA57+MA114)/4、白线=EMA(EMA(C,10),10)，多头排列（白>黄）才布；
    #   动能K=放量阳线（base>20 且 VOL>REF(VOL,1)×1.5），上沿=收盘/下沿=开盘。
    #   行情侧注入 yellow/white/ek_open/ek_close（cond_watch_v2 用 buy_module 正本算），
    #   本函数只做阈值比对，不重算公式。
    "ma_pullback_buy": {"side": "buy", "mode": "ma_pullback", "label": "回踩均线买入"},
    "key_k_break":     {"side": "buy", "mode": "key_k",      "label": "关键K低吸"},
}
COND_ACTIVE = "ACTIVE"
SIGNAL_NAMES = {"BREAKOUT", "PULLBACK", "VWAP_RECLAIM", "HOOK_TURN", "WEAK_TO_STRONG"}   # 信号买入可选值
COND_TERMINAL = ("TRIGGERED", "CANCELLED", "EXPIRED", "FAILED")
SINGLE_ORDER_CAP_RATIO = 0.10      # 单笔软上限（占总资产）——只提示不阻断
MIN_LOT = 100                      # A 股一手

# 手工优先锁文件（模块级常量：保持旧调用方/测试可 monkeypatch；None = 用 state 的缺省路径）
MANUAL_LOCK_FILE: Optional[str] = None


class PaperOrderError(Exception):
    """业务拒绝（HTTP 层转 4xx）。code 供前端分支：invalid / no_quote / no_cash / no_position /
    price_cage / rejected / closed / killed（旧版还有 live_locked / qmt_*，本版通道已删除）。"""

    def __init__(self, message: str, code: str = "invalid", status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


# 兼容别名：旧测试/调用方还能 import ManualOrderError
ManualOrderError = PaperOrderError


def _manual_lock_path(path: Optional[Any] = None):
    """手工优先锁落地路径：显式 path → 模块级 MANUAL_LOCK_FILE（兼容 monkeypatch）→ state 缺省。"""
    if path:
        return path
    if MANUAL_LOCK_FILE:
        return MANUAL_LOCK_FILE
    return state.get_paths().manual_lock


def manual_locked(code: str, day: Optional[str] = None, path: Optional[Any] = None) -> bool:
    """当日该票是否已有手工单（互斥用：自动引擎看到 true 就不下单）。"""
    doc = state.read_json(_manual_lock_path(path), {}) or {}
    e = doc.get(str(code)) or {}
    return str(e.get("date") or "") == (day or _today8())


def mark_manual_lock(code: str, order_id: str = "", path: Optional[Any] = None) -> None:
    """落手工锁（2026-10-21 主人：手工优先，自动引擎让开）。"""
    p = _manual_lock_path(path)
    with state.locked(p):
        doc = state.read_json(p, {}) or {}
        doc[str(code)] = {"date": _today8(), "ts": now_str(), "order_id": str(order_id or "")}
        state.atomic_write_json(p, doc, audit_event="manual_lock.mark",
                                detail="%s %s" % (code, order_id))


def _price_tick(code: str) -> float:
    """最小变动单位：ETF/LOF（5xx、1xx 场内基金）0.001，其余 0.01。"""
    c = str(code or "")[:6]
    if c.startswith(("5", "1")):
        return 0.001
    return 0.01


def _round_tick(price: float, code: str) -> float:
    t = _price_tick(code)
    return round(round(float(price) / t) * t, 4 if t < 0.01 else 3)


def _f(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _today8() -> str:
    """条件单/委托的日期口径统一 YYYYMMDD（now_str 是 YYYY-MM-DD HH:MM:SS，两者不能混比）。"""
    return time.strftime("%Y%m%d")


def _default_session_ok() -> bool:
    """保守交易时段：工作日 09:30–11:30 / 13:00–15:00。

    旧版用 common.trading_calendar（eltdx 全历史日历）。本版先内置工作日+时段，
    4.1.3 的 paper 插件装配时会注入带节假日的真实日历；取不到日历时本函数仍
    退化为周末+时段判断（宁可少放行，不放行半夜市价单）。
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm < 690) or (780 <= hm < 900)


class PaperOrderService:
    """手工委托 + 条件单（paper-only）。broker 可注入（单测用临时账本）。

    与旧 ManualOrderService 的差别（逐条见模块头「本版改造点」）：
    - 无实盘分支：trade_mode() 恒 "paper"，live_order_enabled() 恒 False；
    - 所有读写走 app/core/paper/state.py（paths.PaperPaths）；
    - 行情/信号/时段全部注入（session_ok / signal_provider / quotes），零外部依赖。
    """

    def __init__(self, broker=None, state_dir: Optional[Any] = None,
                 cond_path: Optional[Any] = None,
                 account_path: Optional[Any] = None, trades_path: Optional[Any] = None,
                 engine_state_path: Optional[Any] = None, session_ok=None,
                 kill_switch_path: Optional[Any] = None,
                 freeze_path: Optional[Any] = None):
        self._paths = state.get_paths(state_dir)
        self.state_dir = str(self._paths.root)
        if broker is None:
            broker = broker_mod.PaperBroker(state_dir=state_dir)
        self.broker = broker
        self.cond_path = str(cond_path or self._paths.conditions)
        self.account_path = str(account_path
                                or getattr(broker, "paper_account_path", None)
                                or self._paths.account)
        self.trades_path = str(trades_path
                               or getattr(broker, "paper_trades_path", None)
                               or self._paths.trades)
        self.engine_state_path = str(engine_state_path or self._paths.engine_state)
        self.kill_switch_path = str(kill_switch_path or self._paths.kill_switch)
        self.freeze_path = str(freeze_path or self._paths.freeze)
        self.manual_lock_path = str(self._paths.manual_lock)
        # 交易时段判定可注入（单测确定性）；缺省 = 工作日+连续竞价时段
        self._session_ok = session_ok or _default_session_ok
        # 信号买入判定由装配方注入（4.1.3 feed）；未注入 → 信号类条件单永不触发
        self.signal_provider = None

    # ------------------------------------------------------------------ 通用读取
    def trade_mode(self) -> str:
        """交易模式：恒为 "paper"（旧版读 engine_state 并可能返回 live —— 本版永久删除）。

        engine_state.json 仍**只读**保留（trade_mode 缺省 paper）；不存在也返回 paper，
        绝不回退 live（纸面模块不得因为文件缺失而切到真实通道）。
        """
        st = state.read_json(self.engine_state_path, {}) or {}
        mode = str(st.get("trade_mode") or "paper") if isinstance(st, dict) else "paper"
        return mode if mode == "paper" else "paper"

    def live_order_enabled(self) -> bool:
        """实盘下单开关：**恒 False**（纸面模块没有实盘下单路径，开关存在只为旧前端兼容）。"""
        return False

    def account(self) -> Dict[str, Any]:
        acct = state.read_json(self.account_path, None)
        if not isinstance(acct, dict) or "cash" not in acct:
            # 账本文件还未生成（首单前）：回落到 broker 的初始账本（含 initial_cash），
            # 不自己造一份，避免双源头。
            try:
                acct = self.broker.load_account()
            except Exception:  # noqa: BLE001
                acct = {}
        return acct or {}

    def cash(self) -> float:
        return float(self.account().get("cash") or 0.0)

    def position(self, code: str) -> Dict[str, Any]:
        return (self.account().get("positions") or {}).get(code) or {}

    def bought_today(self, code: str) -> int:
        """当日已买入成交量（T+1 近似：纸面账本不记可卖日，用当日成交反推）。"""
        day = _today8()
        total = 0
        for r in self._trades():
            if not isinstance(r, dict):
                continue
            if str(r.get("ts_code") or "") != code:
                continue
            if str(r.get("side") or "").upper() != "BUY":
                continue
            if str(r.get("status") or "") not in ("FILLED", "PARTIAL"):
                continue
            if str(r.get("created_at") or "")[:10].replace("-", "") != day:
                continue
            total += int(r.get("filled_qty") or 0)
        return total

    def available_qty(self, code: str) -> int:
        """可卖量 = 持仓 − 当日买入（T+1）− 挂单冻结。

        以当日成交推算为唯一口径（账本字段 available_qty 可能滞后或缺失；
        新买入当天无论字段如何都不可卖）。"""
        pos = self.position(code)
        qty = int((pos or {}).get("qty") or 0)
        return max(0, qty - self.bought_today(code) - self.frozen_qty(code))

    # ------------------------------------------------------------------ 挂单冻结（2026-10-21 按上交所 3.2.10）
    def _load_freeze(self) -> Dict[str, Any]:
        doc = state.read_json(self.freeze_path, {}) or {}
        frozen = doc.get("frozen") if isinstance(doc, dict) else None
        return frozen if isinstance(frozen, dict) else {}

    def _save_freeze(self, frozen: Dict[str, Any]) -> None:
        state.atomic_write_json(self.freeze_path,
                                {"version": 1, "frozen": frozen, "updated_at": now_str()},
                                audit_event="freeze.save",
                                detail="frozen=%d" % len(frozen))

    def frozen_cash(self) -> float:
        total = 0.0
        for e in self._load_freeze().values():
            if isinstance(e, dict) and e.get("kind") == "cash":
                total += float(e.get("price") or 0) * int(e.get("remaining_qty") or 0)
        return round(total, 2)

    def frozen_qty(self, code: str) -> int:
        total = 0
        for e in self._load_freeze().values():
            if isinstance(e, dict) and e.get("kind") == "qty" and e.get("code") == code:
                total += int(e.get("remaining_qty") or 0)
        return total

    def available_cash(self) -> float:
        return round(max(0.0, self.cash() - self.frozen_cash()), 2)

    def _sync_freeze(self, rec: Dict[str, Any]) -> None:
        """按委托当前状态重算冻结量（幂等）：未成交剩余继续冻，成交/撤单/废单释放。"""
        oid = str(rec.get("paper_order_id") or rec.get("order_id") or "")
        if not oid:
            return
        with state.locked(self.freeze_path):
            frozen = self._load_freeze()
            status = str(rec.get("status") or "")
            qty = int(rec.get("qty") or 0)
            filled = int(rec.get("filled_qty") or 0)
            remain = max(0, qty - filled)
            if status in ("REPORTED", "PARTIAL") and remain > 0:
                frozen[oid] = {"order_id": oid, "code": rec.get("ts_code"),
                               "kind": "cash" if str(rec.get("side")).upper() == "BUY" else "qty",
                               "price": float(rec.get("limit_price") or 0),
                               "qty": qty, "remaining_qty": remain}
            else:
                frozen.pop(oid, None)
            self._save_freeze(frozen)

    def _trades(self) -> List[Dict[str, Any]]:
        try:
            return self.broker.load_trades() or []
        except Exception:  # noqa: BLE001 - 账本读失败按空处理（校验侧另有现金/持仓兜底）
            return []

    def _maybe_expire_stale(self) -> None:
        """委托当日有效（上交所 3.3.18）：跨日后上一日的未成交单作废（隔夜单是次日新报的）。

        每个进程每天只扫一次（服务重启也安全：_expire_day 为 None 会重扫）。
        """
        today = _today8()
        if getattr(self, "_expire_day", None) == today:
            return
        self._expire_day = today
        for r in self._trades():
            if not isinstance(r, dict):
                continue
            if str(r.get("status")) not in ("REPORTED", "PARTIAL"):
                continue
            if str(r.get("source")) not in ("manual", "conditional"):
                continue
            d8 = str(r.get("created_at") or "")[:10].replace("-", "")
            if not d8 or d8 >= today:
                continue
            oid = r.get("paper_order_id")
            try:
                self.broker.cancel(oid)
                self._sync_freeze(self.broker.get_trade(oid) or r)
            except Exception:  # noqa: BLE001 - 单笔过期失败不影响其他
                continue

    def list_orders(self, day: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """当日委托（新→旧）。day=YYYYMMDD，缺省今天。"""
        self._maybe_expire_stale()
        d8 = day or _today8()
        out = []
        for r in self._trades():
            if not isinstance(r, dict):
                continue
            created = str(r.get("created_at") or "")
            if created[:10].replace("-", "") != d8:
                continue
            out.append(r)
        out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return out[:max(1, int(limit))]

    # ------------------------------------------------------------------ 下单
    def place(self, code: str, side: str, qty: int, price: Optional[float] = None,
              order_type: str = "limit", quote: Optional[Dict[str, Any]] = None,
              source: str = "manual", name: str = "", note: str = "",
              meta: Optional[Dict[str, Any]] = None,
              allow_off_hours: bool = False) -> Dict[str, Any]:
        """下手工单。返回 {order, warnings, account}。业务拒绝抛 PaperOrderError。

        **只有纸面撮合一条路**（旧版 trade_mode=live 分支已删除）。
        非交易时段：纸面同样拒——夜里下的单不可能成交，以前那样"立即成交"是假的。
        allow_off_hours 仅供条件单手动演练（force）使用。
        """
        ts_code = to_ts_code(code)
        if not ts_code:
            raise PaperOrderError(f"非法证券代码：{code!r}", "invalid")
        # 急停（安全三锁之一）：引擎急停期间一律禁止下单（手工单也不例外）
        ks = state.read_json(self.kill_switch_path, {}) or {}
        if isinstance(ks, dict) and ks.get("active"):
            raise PaperOrderError(
                f"引擎急停中（{ks.get('reason') or '急停未解除'}），禁止下单", "killed", 409)
        side_u = str(side or "").upper()
        if side_u not in ("BUY", "SELL"):
            raise PaperOrderError(f"非法方向：{side!r}（buy/sell）", "invalid")
        try:
            qty_i = int(qty)
        except (TypeError, ValueError):
            raise PaperOrderError(f"非法数量：{qty!r}", "invalid")
        if qty_i <= 0:
            raise PaperOrderError("数量必须大于 0", "invalid")
        if side_u == "BUY" and qty_i % MIN_LOT != 0:
            raise PaperOrderError(f"买入数量必须是 {MIN_LOT} 股整数倍（当前 {qty_i}）", "invalid")
        otype = str(order_type or "limit").lower()
        if otype not in ("limit", "market"):
            raise PaperOrderError(f"非法委托方式：{order_type!r}", "invalid")
        # 市价单必须有活跃行情；限价单任何时候都能挂（非交易时段挂着等，成交了才算）
        if otype == "market" and not allow_off_hours and not self._session_ok():
            raise PaperOrderError(
                "非交易时段不能下市价单（没有对手盘），请用限价挂单", "closed", 409)

        q = quote or {}
        last = _f(q.get("last"))
        if not last or last <= 0:
            raise PaperOrderError("没有可用实时价（行情不可达），已拒绝", "no_quote", 503)

        limit_up = _f(q.get("limit_up"))
        limit_down = _f(q.get("limit_down"))
        px = _f(price)
        if otype == "limit":
            if not px or px <= 0:
                raise PaperOrderError("限价单必须填写价格", "invalid")
            px = _round_tick(px, ts_code)
            if limit_up and px > limit_up + 1e-9:
                raise PaperOrderError(f"价格 {px:.2f} 高于涨停价 {limit_up:.2f}", "invalid")
            if limit_down and px < limit_down - 1e-9:
                raise PaperOrderError(f"价格 {px:.2f} 低于跌停价 {limit_down:.2f}", "invalid")
        else:
            px = _round_tick(last, ts_code)
        if otype == "limit" and q.get("fresh") is not False:
            self._check_price_cage(side_u, px, q)

        warnings: List[str] = []
        amount = round(qty_i * px, 2)
        if side_u == "BUY":
            need = round(amount + broker_mod.calc_fee(amount, "BUY"), 2)
            cash = self.available_cash()          # 已扣除其他挂单冻结（3.2.10）
            if need > cash + 1e-6:
                raise PaperOrderError(
                    f"可用资金不足：需 ¥{need:,.2f}（含费用），可用 ¥{cash:,.2f}（含挂单冻结）", "no_cash")
        else:
            avail = self.available_qty(ts_code)
            if qty_i > avail:
                raise PaperOrderError(
                    f"可卖数量不足：可卖 {avail} 股（持仓 {int(self.position(ts_code).get('qty') or 0)} 股，"
                    f"当日买入不可卖），本次 {qty_i} 股", "no_position")

        total_asset = self._total_asset()
        if total_asset > 0 and amount > total_asset * SINGLE_ORDER_CAP_RATIO + 1e-6:
            warnings.append(
                f"单笔 ¥{amount:,.0f} 超过纸面总资产 {int(SINGLE_ORDER_CAP_RATIO * 100)}%"
                f"（¥{total_asset * SINGLE_ORDER_CAP_RATIO:,.0f}），手工单已放行")

        # 纸面撮合口径（2026-10-21 主人纠正）：
        #   限价单只有在**真的出现对手盘**时才成交：买→卖一价 ≤ 限价；卖→买一价 ≥ 限价。
        #   非交易时段 / 行情不新鲜（停牌、夜间）→ 一律不成交，只挂单（可随时撤）。
        #   旧实现用 last 合成 bar 立即成交，曾导致"夜里买 100 股直接成功"，已废止。
        fill_px = self._resolve_fill(side_u, px, q, market=(otype == "market"), allow_stale=allow_off_hours)
        bar = None
        if fill_px is not None:
            # 对手档量决定能否全成（rqalpha CounterPartyOfferMatcher 同思路）：
            # 卖一挂 2 手（200 股）就只能吃 200 股，剩余继续挂着。
            vol_hand = 0
            try:
                levels = (q.get("asks") if side_u == "BUY" else q.get("bids")) or []
                vol_hand = int((levels[0] or {}).get("vol") or 0) if levels else 0
            except (TypeError, ValueError, IndexError):
                vol_hand = 0
            bar = {"ts": now_str(), "open": fill_px, "high": fill_px, "low": fill_px,
                   "close": fill_px, "volume": float(vol_hand)}
        info = dict(meta or {})
        info.setdefault("name", name)
        info.setdefault("note", note)
        rec = self.broker.order(
            ts_code, qty_i, px, side=side_u.lower(), order_type=otype, bar=bar,
            kind="BUY" if side_u == "BUY" else "SELL",
            source=source, **info)
        # PaperBroker.order() 的返回键叫 order_id（同形接口口径）；对外统一暴露 paper_order_id，
        # 与账本记录字段对齐（前端只认这一个）。
        rec.setdefault("paper_order_id", rec.get("order_id"))
        if rec.get("status") == "REJECTED":
            reason = rec.get("reject_reason") or (rec.get("record") or {}).get("reject_reason") or "未知原因"
            raise PaperOrderError(f"委托被拒：{reason}", "rejected", 409)
        # 2026-10-21 主人：手工优先 —— 每笔手工单落锁，自动引擎同一票当日不再下单
        try:
            mark_manual_lock(ts_code, rec.get("paper_order_id") or "", path=self.manual_lock_path)
        except Exception:  # noqa: BLE001 - 锁写失败不阻断下单
            pass
        # 冻结计算要用完整账本记录（order() 的返回壳只带 status/filled_*，没有 qty/price/side）
        self._sync_freeze(rec.get("record") or rec)

        if str(rec.get("status") or "") == "FILLED":
            sim = rec.get("simulation") or {}
            if sim.get("partial"):
                warnings.append(f"部分成交 {rec.get('filled_qty')} 股（当分钟流动性不足，纸面模拟）")
        else:
            warnings.append(f"已挂单：买一/卖一真正打到 {px:.2f} 才成交，期间可随时撤单")
        return {"order": rec, "warnings": warnings, "account": self._account_view()}

    def _check_price_cage(self, side_u: str, px: float, quote: Dict[str, Any]) -> None:
        """有效申报价格范围（上交所 3.3.14）：买≤基准×102% 与 基准+10 个跳动 孰高；卖对称。

        基准价：即时揭示的最低卖价（买）/最高买价（卖），无则最新价。只在行情新鲜时检查——
        隔夜挂单的基准次日才成立，用旧价当基准会误拒。
        """
        q = quote or {}
        asks = q.get("asks") or []
        bids = q.get("bids") or []
        last = _f(q.get("last"))
        tick = 0.01
        if side_u == "BUY":
            base = _f((asks[0] or {}).get("price")) if asks else None
            base = base or (_f((bids[0] or {}).get("price")) if bids else None) or last
            if not base:
                return
            hi = max(base * 1.02, base + 10 * tick)
            if px > hi + 1e-9:
                raise PaperOrderError(
                    f"超价格笼子：买入价 {px:.2f} > {hi:.2f}（基准 {base:.2f}×102% 与 +0.10 孰高），"
                    f"实盘会废单", "price_cage")
        else:
            base = _f((bids[0] or {}).get("price")) if bids else None
            base = base or (_f((asks[0] or {}).get("price")) if asks else None) or last
            if not base:
                return
            lo = min(base * 0.98, base - 10 * tick)
            if px < lo - 1e-9:
                raise PaperOrderError(
                    f"超价格笼子：卖出价 {px:.2f} < {lo:.2f}（基准 {base:.2f}×98% 与 -0.10 孰低），"
                    f"实盘会废单", "price_cage")

    def _resolve_fill(self, side_u: str, limit_px: float, quote: Dict[str, Any],
                      market: bool = False, allow_stale: bool = False) -> Optional[float]:
        """纯函数：根据盘口判能否**立即**成交，返回成交价；不能 → None（挂单等）。

        买：卖一价 ≤ 限价 → 吃卖一（价优）；无卖一 → 回退 last。
        卖：买一价 ≥ 限价 → 砸买一；无买一 → 回退 last。
        行情不新鲜（非交易时段/停牌）→ 不成交（除非 allow_stale 演练）。
        市价：有新鲜 last → 按 last 成交。
        """
        q = quote or {}
        if not allow_stale and q.get("fresh") is False:
            return None
        last = _f(q.get("last"))
        if not last or last <= 0:
            return None
        if market:
            return last
        if side_u == "BUY":
            asks = q.get("asks") or []
            best = _f((asks[0] or {}).get("price")) if asks else None
            ref = best if (best and best > 0) else last
            return min(ref, limit_px) if ref <= limit_px + 1e-9 else None
        bids = q.get("bids") or []
        best = _f((bids[0] or {}).get("price")) if bids else None
        ref = best if (best and best > 0) else last
        return max(ref, limit_px) if ref >= limit_px - 1e-9 else None

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """撤单（纸面）。已成交/已撤/已拒 → 409 业务拒绝（不静默成功）。"""
        oid = str(order_id or "").strip()
        if not oid:
            raise PaperOrderError("缺少委托号", "invalid")
        rec = self.broker.get_trade(oid)
        if rec is None:
            raise PaperOrderError(f"委托不存在：{oid}", "invalid", 404)
        if str(rec.get("status") or "") in ("FILLED", "CANCELLED", "REJECTED"):
            raise PaperOrderError(f"该委托已是终态（{rec.get('status')}），不能撤", "invalid", 409)
        ok = self.broker.cancel(oid)
        if not ok:
            raise PaperOrderError("撤单失败（可能刚成交），请刷新后重试", "invalid", 409)
        self._sync_freeze(self.broker.get_trade(oid) or {"paper_order_id": oid, "status": "CANCELLED"})
        return {"order_id": oid, "status": "CANCELLED"}

    def match_pending(self, quotes: Dict[str, Dict[str, Any]],
                      only_sources: Tuple[str, ...] = ("manual", "conditional")) -> List[Dict[str, Any]]:
        """挂单补成交：按最新价判断限价单是否轮到成交，成交走 reconcile_fill（与对账同路径）。

        2026-10-21 修（主人报 002099 夜里成交）：非交易时段一律不撮合——收盘后的"最新价"
        不是对手盘，拿它成交等于凭空成交。交易时段内仍看 quote.fresh。
        """
        if not self._session_ok():
            return []
        filled = []
        for r in self._trades():
            if not isinstance(r, dict):
                continue
            if str(r.get("status") or "") not in ("REPORTED", "PARTIAL"):
                continue
            src = str(r.get("source") or "")
            if only_sources and src not in only_sources:
                continue
            code = str(r.get("ts_code") or "")
            q = quotes.get(code) or {}
            last = _f(q.get("last"))
            lp = _f(r.get("limit_price")) or 0.0
            if not last or last <= 0 or lp <= 0:
                continue
            side = str(r.get("side") or "").upper()
            if str(r.get("order_type") or "") == "market":
                px = self._resolve_fill(side, lp, q, market=True)
            else:
                px = self._resolve_fill(side, lp, q)
            if px is None:
                continue
            try:
                res = self.broker.reconcile_fill(
                    r.get("paper_order_id"), filled_price=_round_tick(px, code),
                    matched_bar_ts=q.get("quote_time"), note="manual_match")
                if res.get("changed"):
                    self._sync_freeze(self.broker.get_trade(r.get("paper_order_id")) or r)
                    filled.append(res)
            except Exception:  # noqa: BLE001 - 单笔补成交失败不影响其他
                continue
        return filled

    # ------------------------------------------------------------------ 条件单
    def _load_cond(self) -> Dict[str, Any]:
        doc = state.read_json(self.cond_path, None)
        if not isinstance(doc, dict) or not isinstance(doc.get("orders"), list):
            doc = {"version": 1, "meta": {}, "orders": []}
        return doc

    def _save_cond(self, doc: Dict[str, Any]) -> None:
        doc.setdefault("meta", {})["updated_at"] = now_str()
        state.atomic_write_json(self.cond_path, doc, audit_event="cond.save",
                                detail="orders=%d" % len(doc.get("orders") or []))

    def list_conditions(self, code: Optional[str] = None,
                        status: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for c in self._load_cond().get("orders") or []:
            if not isinstance(c, dict):
                continue
            if code and str(c.get("code")) != to_ts_code(code):
                continue
            if status and str(c.get("status")) != status:
                continue
            # 2026-10-21：剩几天现算（不靠建单时日快照）
            try:
                _vu = str(c.get("valid_until") or "")
                if len(_vu) == 8:
                    c["days_left"] = (datetime.strptime(_vu, "%Y%m%d")
                                      - datetime.strptime(_today8(), "%Y%m%d")).days
                    if _vu != str(c.get("_days_key") or ""):
                        c["expiring"] = bool(c.get("long_term") and c["days_left"] <= 3)
            except ValueError:
                pass
            out.append(c)
        out.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
        return out

    def mark_expiring_notified(self, cond_id: str) -> None:
        """标记「已推送临期提醒」（防重复推送）。"""
        with state.locked(self.cond_path):
            doc = self._load_cond()
            for c in doc.get("orders") or []:
                if isinstance(c, dict) and c.get("cond_id") == cond_id:
                    c["expiring"] = True
                    c["expiring_notified"] = True
                    break
            self._save_cond(doc)

    def add_condition(self, code: str, ctype: str, qty: int,
                      trigger_price: Optional[float] = None,
                      pct: Optional[float] = None, amount: Optional[float] = None,
                      order_type: str = "market", limit_price: Optional[float] = None,
                      side: Optional[str] = None, at: Optional[str] = None,
                      valid_until: Optional[str] = None, group: Optional[str] = None,
                      confirm_count: int = 1,
                      buffer_atr: Optional[float] = None,
                      signal: Optional[str] = None,
                      target: Optional[str] = None, tol: Optional[float] = None,
                      quote: Optional[Dict[str, Any]] = None, name: str = "",
                      note: str = "") -> Dict[str, Any]:
        ks = state.read_json(self.kill_switch_path, {}) or {}
        if isinstance(ks, dict) and ks.get("active"):
            raise PaperOrderError(
                f"引擎急停中（{ks.get('reason') or '急停未解除'}），禁止新增条件单", "killed", 409)
        ts_code = to_ts_code(code)
        if not ts_code:
            raise PaperOrderError(f"非法证券代码：{code!r}", "invalid")
        spec = COND_TYPES.get(ctype)
        if not spec:
            raise PaperOrderError(f"未知条件单类型：{ctype!r}", "invalid")
        side_u = (side or spec["side"] or "").upper()
        if side_u not in ("BUY", "SELL"):
            raise PaperOrderError("定时委托必须指定方向（buy/sell）", "invalid")
        try:
            qty_i = int(qty)
        except (TypeError, ValueError):
            raise PaperOrderError(f"非法数量：{qty!r}", "invalid")
        if qty_i <= 0:
            raise PaperOrderError("数量必须大于 0", "invalid")
        if side_u == "BUY" and qty_i % MIN_LOT != 0:
            raise PaperOrderError(f"买入数量必须是 {MIN_LOT} 股整数倍", "invalid")
        avail = 0
        if side_u == "SELL":
            # 2026-10-21 主人：当日买入（T+1 不可卖）也允许先挂条件单，只是今天不会触发；
            #   所以这里只校验「不超持仓总量」，可卖量不足的差额在触发时再拦（不直接判死）。
            avail = self.available_qty(ts_code)
            held = int((self.position(ts_code) or {}).get("qty") or 0)
            if qty_i > held:
                raise PaperOrderError(f"数量超过持仓：持仓 {held} 股，本次 {qty_i} 股", "no_position")

        trig = {"price": None, "pct": None, "amount": None, "at": None}
        entry0 = None
        buf0 = _f(buffer_atr)
        if spec["mode"] in ("breakeven", "ma5"):
            # 无需触发价：保本线 = 持仓成本；五日线 = 当日 MA5（行情侧注入 atr14/ma5）
            if spec["mode"] == "breakeven":
                entry0 = _f((self.position(ts_code) or {}).get("cost_price"))
                if not entry0:
                    raise PaperOrderError("保本止损需要已有持仓成本", "no_position")
            buf0 = _f(buffer_atr)
            if buf0 is None:
                buf0 = 0.3          # 与旧系统 stop_buffer_atr 同口径
        elif spec["mode"] == "signal":
            # 2026-10-21 主人：信号库买入（分钟K闭合判定，装配方注入 signal_provider）
            sig0 = str(signal or "").strip().upper()
            if sig0 not in SIGNAL_NAMES:
                raise PaperOrderError(f"未知信号：{signal!r}（可选 {'/'.join(sorted(SIGNAL_NAMES))}）", "invalid")
        elif spec["mode"] in ("ma_pullback", "key_k"):
            # 2026-10-28：回踩均线 / 关键K——无需触发价，目标位与容差在评估时按行情注入的
            #   yellow/white/ek_open/ek_close 算（公式正本 buy_module，本层不重算）。
            t = str(target or "auto").strip().lower()
            allowed = (("auto", "yellow", "white") if spec["mode"] == "ma_pullback"
                       else ("auto", "upper", "lower"))
            if t not in allowed:
                raise PaperOrderError("目标位可选 %s" % "/".join(allowed), "invalid")
            trig["target"] = t
            tl = _f(tol if tol is not None else 0.005)
            if tl is None or tl <= 0 or tl > 0.05:
                raise PaperOrderError("容差需在 0–5% 之间（默认 0.5%）", "invalid")
            trig["tol"] = round(tl, 4)
        elif spec["mode"] in ("le", "ge"):
            p = _f(trigger_price)
            if not p or p <= 0:
                raise PaperOrderError("这类条件单必须填写触发价", "invalid")
            trig["price"] = _round_tick(p, ts_code)
        elif spec["mode"] in ("trail", "rebound"):
            pc = _f(pct)
            am = _f(amount)
            if (not pc or pc <= 0) and (not am or am <= 0):
                raise PaperOrderError("回落/反弹条件必须填比例（%）或金额（元）其中之一", "invalid")
            trig["pct"] = round(pc, 3) if pc and pc > 0 else None
            trig["amount"] = round(am, 3) if am and am > 0 else None
        elif spec["mode"] == "time":
            hm = str(at or "").strip()
            if not (len(hm) == 5 and hm[2] == ":" and hm.replace(":", "").isdigit()):
                raise PaperOrderError("定时委托需要 HH:MM 格式的时间", "invalid")
            trig["at"] = hm

        otype = str(order_type or "market").lower()
        if otype not in ("limit", "market"):
            raise PaperOrderError(f"非法委托方式：{order_type!r}", "invalid")
        lp = _f(limit_price)
        if lp is not None and lp <= 0:
            lp = None

        q = quote or {}
        last = _f(q.get("last"))
        # 触发价与现价的关系校验（2026-10-21，对齐老虎口径）：否则一提交就立即触发，
        # 用户会以为"挂了条件单"，实际已经变成市价单报出去了。
        if last and trig["price"] is not None:
            if spec["mode"] == "le" and trig["price"] >= last:
                raise PaperOrderError(
                    f"止损/下破触发价必须低于现价（现价 {last:.2f}，填的是 {trig['price']:.2f}）", "invalid")
            if spec["mode"] == "ge" and trig["price"] <= last:
                raise PaperOrderError(
                    f"止盈/上破触发价必须高于现价（现价 {last:.2f}，填的是 {trig['price']:.2f}）", "invalid")
        rec = {
            "cond_id": None,   # 锁内分配（防并发重号）
            "code": ts_code,
            "name": name or "",
            "type": ctype,
            "label": spec["label"],
            "side": side_u,
            "qty": qty_i,
            "trigger": trig,
            "order_type": otype,
            "limit_price": _round_tick(lp, ts_code) if lp else None,
            "valid_until": str(valid_until or _today8()),
            "group": group or None,
            "status": COND_ACTIVE,
            "t1_locked": bool(side_u == "SELL" and qty_i > avail),   # 当日买入不可卖：挂着，隔日解锁后才会触发
            "entry": entry0,          # 保本止损的锚（持仓成本；其余类型 null）
            "signal": (str(signal or "").strip().upper() if spec["mode"] == "signal" else None),
            "buffer_atr": (buf0 if spec["mode"] in ("breakeven", "ma5", "le", "ge") else None),
            "armed": False,           # 保本止损：是否已进入「浮盈≥1.5ATR」保护态
            "confirm_count": max(1, min(10, int(confirm_count or 1))),
            "hits": 0,
            "peak": last if (spec["mode"] in ("trail", "rebound") and last) else None,
            "trough": last if (spec["mode"] in ("trail", "rebound") and last) else None,
            "order_id": None,
            "reason": None,
            "long_term": False,      # 有效期≥10 天：临近到期需提醒
            "expiring_notified": False,
            "created_at": now_str(),
            "triggered_at": None,
            "note": note or "",
        }
        with state.locked(self.cond_path):
            doc = self._load_cond()
            # 有效期≥10 天 → 长期单，临期提醒（2026-10-21 主人）
            try:
                _days = (datetime.strptime(rec["valid_until"], "%Y%m%d")
                         - datetime.strptime(_today8(), "%Y%m%d")).days
            except ValueError:
                _days = 0
            rec["long_term"] = bool(_days >= 10)
            rec["days_left"] = _days
            rec["expiring"] = bool(rec["long_term"] and _days <= 3)
            # 2026-10-21 主人：止盈/止损线只能各存一条——"线类"条件单新建时自动作废旧同类型活跃单
            if ctype in ("stop_loss", "take_profit"):
                for old in doc["orders"]:
                    if (isinstance(old, dict) and old.get("code") == ts_code
                            and old.get("type") == ctype and str(old.get("status")) == COND_ACTIVE):
                        old["status"] = "CANCELLED"
                        old["reason"] = "replaced_by_new_line"
                        old["closed_at"] = now_str()
            rec["cond_id"] = self._next_cond_id(doc)
            doc["orders"].append(rec)
            self._save_cond(doc)
        return rec

    def cancel_condition(self, cond_id: str) -> Dict[str, Any]:
        cid = str(cond_id or "").strip()
        if not cid:
            raise PaperOrderError("缺少条件单号", "invalid")
        with state.locked(self.cond_path):
            doc = self._load_cond()
            hit = next((c for c in doc["orders"] if isinstance(c, dict) and c.get("cond_id") == cid), None)
            if hit is None:
                raise PaperOrderError(f"条件单不存在：{cid}", "invalid", 404)
            if str(hit.get("status")) != COND_ACTIVE:
                raise PaperOrderError(f"条件单已是终态（{hit.get('status')}），不能撤", "invalid", 409)
            hit["status"] = "CANCELLED"
            hit["reason"] = "manual_cancel"
            hit["closed_at"] = now_str()
            self._save_cond(doc)
        return hit

    def _next_cond_id(self, doc: Optional[Dict[str, Any]] = None) -> str:
        stamp = time.strftime("%Y%m%d")
        doc = doc if isinstance(doc, dict) else self._load_cond()
        seq = 0
        for c in doc.get("orders") or []:
            cid = str((c or {}).get("cond_id") or "")
            if cid.startswith("CO" + stamp):
                try:
                    seq = max(seq, int(cid[-4:]))
                except ValueError:
                    pass
        return f"CO{stamp}{seq + 1:04d}"

    def evaluate(self, quotes: Dict[str, Dict[str, Any]], now_hm: Optional[str] = None,
                 force: bool = False) -> Dict[str, Any]:
        """评估条件单。quotes：{code: {last,...}}（调用方只传活跃码）。
        返回 {triggered, cancelled, expired}。

        force=True（面板「立即评估」）绕过交易时段与行情新鲜度门槛，用于盘中之外自测。
        """
        now_hm = now_hm or time.strftime("%H:%M")
        today = _today8()
        if not force and not self._session_ok():
            # 非交易时段不评估：夜里用昨天的价触发条件单是假的
            return {"triggered": [], "cancelled": [], "expired": [], "skipped": "closed"}
        trig_out, expired_out, cancelled_out = [], [], []
        changed = False
        # 整段 read-modify-write 在同一把条件单锁内完成：add/cancel 并发时不会互相覆盖。
        # 锁内会调 place()（写 paper_trades/account，不碰条件单文件），不构成嵌套锁。
        with state.locked(self.cond_path):
            doc = self._load_cond()
            orders = doc.get("orders") or []
            for c in orders:
                if not isinstance(c, dict) or str(c.get("status")) != COND_ACTIVE:
                    continue
                if str(c.get("valid_until") or today) < today:
                    c["status"] = "EXPIRED"
                    c["reason"] = "valid_until_passed"
                    c["closed_at"] = now_str()
                    expired_out.append(c)
                    changed = True
                    continue
                spec = COND_TYPES.get(str(c.get("type")))
                if not spec:
                    continue
                # 2026-10-22 主人标注（国轩高科止损单过了 T+1 仍显示「待解锁」）：
                #   t1_locked 原本只在**创建时**算一次（add_condition() 里 qty>avail），之后从未重算 →
                #   隔日已解锁、可卖充足，卡片却永远挂在「T+1待解锁」。
                #   口径：每轮按当日实际可卖量重算（与下方触发时的 available_qty 同源），
                #   变了才置 changed，避免无意义写盘。
                # 位置要点（2026-10-22 修）：必须放在「行情过期就 continue」**之前** ——
                #   本判定只需要持仓可卖量，不依赖行情；否则收盘/停牌时永远刷不到，徒标依旧陈旧。
                if str(c.get("side") or spec["side"]).upper() == "SELL":
                    _t1 = bool(int(c.get("qty") or 0) > self.available_qty(c["code"]))
                    if bool(c.get("t1_locked")) != _t1:
                        c["t1_locked"] = _t1
                        changed = True
                    # blocked='t1'（触发时不可卖）同属该事实：解锁后一并清掉，否则卡上仍挂「今日不可卖」
                    if not _t1 and c.get("blocked") == "t1":
                        c.pop("blocked", None)
                        changed = True
                q = quotes.get(str(c.get("code"))) or {}
                last = _f(q.get("last"))
                if q.get("fresh") is False and not force:
                    continue          # 行情过期（停牌/休市/数据源断）：不拿旧价触发
                trigger = c.get("trigger") or {}
                hit = False
                if spec["mode"] == "time":
                    hit = now_hm >= str(trigger.get("at") or "99:99")
                elif last and last > 0:
                    t_price = _f(trigger.get("price"))
                    _atr = _f(q.get("atr14"))
                    _buf = (_f(c.get("buffer_atr")) or 0.0) * (_atr or 0.0)
                    if spec["mode"] == "le":
                        # ATR 缓冲（2026-10-21 主人）：盘中破幅需超过 buffer×ATR 才触发（防毛刺）；
                        # 14:55 后转尾盘验证 → 只要仍在止损线下方就卖（对应旧系统硬止损双段）。
                        if t_price is not None and t_price > 0:
                            if _buf and now_hm < "14:55":
                                hit = last <= t_price - _buf
                            else:
                                hit = last <= t_price
                    elif spec["mode"] == "ge":
                        hit = t_price is not None and t_price > 0 and last >= t_price + _buf
                    elif spec["mode"] == "breakeven":
                        # 保本止损：浮盈曾≥1.5×ATR → 跌回成本全平（旧系统止损四层第①层）
                        _entry = _f(c.get("entry"))
                        if _entry and _atr:
                            if not c.get("armed") and (last - _entry) >= 1.5 * _atr:
                                c["armed"] = True
                                changed = True
                            hit = bool(c.get("armed")) and last <= _entry
                    elif spec["mode"] == "ma5":
                        # 破五日线：盘中 < MA5 − 0.3×ATR 立即触发；14:57 后 < MA5 即触发
                        _ma5 = _f(q.get("ma5"))
                        if _ma5:
                            if now_hm >= "14:57":
                                hit = last < _ma5
                            else:
                                hit = bool(_atr) and last < _ma5 - _buf
                    elif spec["mode"] == "ma_pullback":
                        # 回踩均线：last 触及目标线价 ×(1+tol) 即触发。
                        # auto = 多头排列（白>黄）时：价在白线上方用白线，否则用黄线（同 select_levels）。
                        _w = _f(q.get("white")); _y = _f(q.get("yellow"))
                        _t = str(trigger.get("target") or "auto")
                        _tol = _f(trigger.get("tol")) or 0.005
                        line = _w if _t == "white" else (_y if _t == "yellow" else None)
                        # 容差内仍用白线（价恰好回踩到线时不要瞬间切到黄线，否则永不触发）
                        if _t == "auto" and _w and _y and _w > _y:
                            line = _w if last >= _w * (1 - _tol) else _y
                        if line and line > 0:
                            hit = last <= line * (1 + _tol)
                    elif spec["mode"] == "key_k":
                        # 关键K低吸：last 触及上沿（收盘）/下沿（开盘）× (1+tol) 触发；
                        # auto = 价在上沿上方用上沿，否则用下沿（同 support_low_buy）。
                        _up = _f(q.get("ek_close")); _dn = _f(q.get("ek_open"))
                        _t = str(trigger.get("target") or "auto")
                        _tol = _f(trigger.get("tol")) or 0.005
                        line = _up if _t == "upper" else (_dn if _t == "lower" else None)
                        if _t == "auto":
                            line = _up if (_up and last >= _up * (1 - _tol)) else (_dn or _up)
                        if line and line > 0:
                            hit = last <= line * (1 + _tol)
                    elif spec["mode"] == "signal":
                        # 信号库判定（分钟K闭合）：由装配方注入 provider；无 provider → 永不触发
                        _prov = getattr(self, "signal_provider", None)
                        hit = bool(_prov) and bool(_prov(str(c.get("code")), str(c.get("signal") or "")))
                    elif spec["mode"] == "trail":
                        peak = _f(c.get("peak")) or last
                        if last > peak:
                            peak = last
                            c["peak"] = peak
                            changed = True
                        pct = _f(trigger.get("pct"))
                        amt = _f(trigger.get("amount"))
                        if pct and pct > 0:
                            hit = last <= peak * (1 - pct / 100.0)
                        elif amt and amt > 0:
                            hit = last <= peak - amt
                    elif spec["mode"] == "rebound":
                        trough = _f(c.get("trough")) or last
                        if last < trough:
                            trough = last
                            c["trough"] = trough
                            changed = True
                        pct = _f(trigger.get("pct"))
                        amt = _f(trigger.get("amount"))
                        if pct and pct > 0:
                            hit = last >= trough * (1 + pct / 100.0)
                        elif amt and amt > 0:
                            hit = last >= trough + amt
                if not hit:
                    if int(c.get("hits") or 0):
                        c["hits"] = 0          # 未命中即清零（要求连续确认）
                        changed = True
                    continue
                want = int(c.get("confirm_count") or 1)
                if want > 1:
                    c["hits"] = int(c.get("hits") or 0) + 1
                    changed = True
                    if c["hits"] < want:
                        continue              # 防突刺：连续 N 次评估命中才算触发（5s/次）
                if spec["mode"] == "time" and not last:
                    # 定时委托也需要一个成交参考价；没有行情就下不了单，留到下轮
                    continue
                # 触发 → 下单（复用同一条 place 主链，风控/记账/幂等全部一致）
                # 2026-10-21 主人：T+1 可卖不足时「能卖多少卖多少」——按可卖量部分成交，余量今天卖不掉。
                _qty_eff = int(c.get("qty") or 0)
                if str(c.get("side") or spec["side"]).upper() == "SELL":
                    _av = self.available_qty(c["code"])
                    if _av <= 0:
                        if c.get("blocked") != "t1":
                            c["blocked"] = "t1"
                            c["hits"] = 0
                            changed = True
                        continue
                    if _av < _qty_eff:
                        _qty_eff = _av          # 部分可卖：先成交可卖的那部分
                if c.get("blocked") == "t1":
                    c.pop("blocked", None)
                    changed = True
                px = c.get("limit_price")
                otype = str(c.get("order_type") or "market")
                if otype == "limit" and not px:
                    px = last
                    otype = "market"
                try:
                    res = self.place(
                        c["code"], str(c.get("side") or spec["side"]), _qty_eff,
                        price=px, order_type=otype, quote=q, source="conditional",
                        name=str(c.get("name") or ""),
                        note=f"条件单 {c.get('cond_id')} 触发",
                        meta={"idem_key": f"COND:{c.get('cond_id')}"},
                        allow_off_hours=force)
                    c["status"] = "TRIGGERED"
                    c["triggered_qty"] = _qty_eff
                    if _qty_eff < int(c.get("qty") or 0):
                        c["reason"] = "T+1 部分可卖：按可卖量 %d 股成交（原单 %d 股）" % (_qty_eff, int(c.get("qty") or 0))
                    c["order_id"] = (res.get("order") or {}).get("paper_order_id")
                    c["triggered_at"] = now_str()
                    c["trigger_price"] = last
                    _ord = res.get("order") or {}
                    if _ord.get("filled_price"):
                        # 滑点留痕：条件单只负责触发，成交价由委托链决定
                        c["fill_price"] = _ord.get("filled_price")
                        c["slippage"] = round(float(_ord["filled_price"]) - float(last), 4)
                    trig_out.append(c)
                except PaperOrderError as e:
                    c["status"] = "FAILED"
                    c["reason"] = f"{e.code}: {e}"
                    c["closed_at"] = now_str()
                    trig_out.append(c)
                changed = True
                # OCO：同组成员互斥
                grp = c.get("group")
                if grp:
                    for sib in orders:
                        if sib is c or not isinstance(sib, dict):
                            continue
                        if sib.get("group") == grp and str(sib.get("status")) == COND_ACTIVE:
                            sib["status"] = "CANCELLED"
                            sib["reason"] = "oco_sibling"
                            sib["closed_at"] = now_str()
                            cancelled_out.append(sib)
            if changed:
                self._save_cond(doc)
        return {"triggered": trig_out, "cancelled": cancelled_out, "expired": expired_out}

    # ------------------------------------------------------------------ 内部
    def _total_asset(self) -> float:
        cash = self.cash()
        mv = 0.0
        for _code, p in (self.account().get("positions") or {}).items():
            try:
                mv += float(p.get("qty") or 0) * float(p.get("cost_price") or 0)
            except (TypeError, ValueError):
                continue
        return round(cash + mv, 2)

    def _account_view(self) -> Dict[str, Any]:
        """账户摘要（纸面口径；旧版 live 分支已删除）。

        持仓附上可卖量（T+1 近似）——前端快捷比例按这个算，不在 UI 侧重算一套口径。"""
        acct = self.account()
        total = self._total_asset()
        pos = {}
        for code, p in (acct.get("positions") or {}).items():
            row = dict(p) if isinstance(p, dict) else {}
            row["available_qty"] = self.available_qty(code)
            pos[code] = row
        return {"available_cash": self.available_cash(), "total_asset": total,
                "frozen_cash": self.frozen_cash(),
                "positions": pos,
                "updated_at": acct.get("updated_at") or now_str()}

    def account_view(self) -> Dict[str, Any]:
        """对外账户摘要（HTTP 层用；与 _account_view 同一实现，仅公开入口）。"""
        return self._account_view()

    # ------------------------------------------------------------------ feed 订阅清单
    def wanted_codes(self) -> List[str]:
        """feed 订阅清单 = 持仓 + ACTIVE 条件单 + REPORTED/PARTIAL 挂单，去重排序（4.1.3 用）。"""
        codes = set()
        for code, p in (self.account().get("positions") or {}).items():
            try:
                if int((p or {}).get("qty") or 0) > 0:
                    codes.add(str(code))
            except (TypeError, ValueError):
                continue
        for c in self._load_cond().get("orders") or []:
            if isinstance(c, dict) and str(c.get("status")) == COND_ACTIVE and c.get("code"):
                codes.add(str(c.get("code")))
        for r in self._trades():
            if isinstance(r, dict) and str(r.get("status")) in ("REPORTED", "PARTIAL") and r.get("ts_code"):
                codes.add(str(r.get("ts_code")))
        return sorted(codes)
