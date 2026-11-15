# -*- coding: utf-8 -*-
"""app/core/paper/service.py · 纸面服务的进程单例装配（4.1.2）

【职责】把 state / broker / orders 三个零件拼成一个可用的 PaperOrderService，
并给出 feed 需要的订阅清单入口 wanted_codes()。**只做装配，不做业务**。

【边界】
- 不启动线程、不 import feed、不连任何外部服务（4.1.3 的 app/core/feed.py 才做订阅；
  本模块只回答"该订阅哪些代码"，不自己订阅）。
- 账本目录由 paths.PAPER_STATE_DIR 决定，环境变量 PANEL_V2_PAPER_DIR 可覆盖
  （state.get_paths 每次调用都重读环境变量 → 测试在 import 后再设 env 也生效）。
- 单例只为省重复构造（每次构造都要重新扫盘定单号地板）。测试用 reset_service()
  丢弃单例，**不删任何文件**。

【为什么要有单例】PaperBroker 内部有单号分前缀计数器（_seqs / _seq_seeded，首次使用扫盘
定地板）。每请求新建一个实例 → 每次都扫盘，且两个实例并发时单号可能碰撞
（20260910-1048 事故根因）。进程内一个实例最省事也最安全。

【4.1.3b 追加】行情三件套（`quote_for` / `quote_map` / `on_quotes`）：

- 位置选择：任务书 §4 要求这三个方法挂在 **service** 上（插件只写 `svc.quote_for()`），
  但 4.1.2 交付的 `orders.PaperOrderService` 已经在验收口径内，**本任务不动 orders.py**
  （硬约束第 3 条）。因此在这里把它们作为**独立函数定义 + 挂到类上**：
  `PaperOrderService.quote_for = _quote_for` 等。效果与写在类里完全一致
  （实例方法、可 monkeypatch、测试 `PaperOrderService.quote_for` 能打到），
  改动面却只落在本文件。
- 口径：行情只读，**不写账本**；`quote_for()` 拿不到就是 `{}`，调用方按无行情处理。
- `on_quotes()` 是 feed listener：`evaluate` + `match_pending`，任何异常写 audit、
  **绝不抛回 feed 线程**（feed 的派发线程被拖死 = 全市场行情停摆）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from app.core import paths as _core_paths
from app.core.paper import state
from app.core.paper.broker import PaperBroker
from app.core.paper.orders import PaperOrderService

__all__ = ["build_service", "get_service", "reset_service", "service_status", "wanted_codes"]

_singleton: Optional[PaperOrderService] = None
_lock = threading.Lock()


def build_service(state_dir: Optional[Any] = None,
                  initial_cash: float = state.INITIAL_CASH,
                  session_ok=None,
                  close_price_provider=None,
                  name_resolver=None,
                  alert_sink=None) -> PaperOrderService:
    """按给定账本目录装配一份服务（不复用单例；测试与前台 --loop 场景直接调它）。

    - state_dir 缺省 = paths.PAPER_STATE_DIR / PANEL_V2_PAPER_DIR；
    - initial_cash 只在账本文件缺失、**自动初始化**时生效（不会覆盖已有账本）；
    - session_ok / close_price_provider / name_resolver / alert_sink 全是注入口：
      4.1.3 的 feed 会传真实实现；不传则走纸面安全缺省（见 broker / orders 注释）。
    """
    # 4.4 信号提供器（函数内 import：本模块被 app.core.paper/__init__ 早期导入，
    # 放函数里可避免任何潜在的 import 顺序问题；signal_provider 自身 import 期零副作用）
    from app.core.paper.signal_provider import make_signal_provider  # noqa: PLC0415

    paths = state.get_paths(state_dir)
    broker = PaperBroker(state_dir=paths.root,
                         initial_cash=initial_cash,
                         close_price_provider=close_price_provider,
                         name_resolver=name_resolver,
                         alert_sink=alert_sink)
    svc = PaperOrderService(broker=broker, state_dir=paths.root, session_ok=session_ok)
    # 4.4：signal_buy 条件单接信号库（BREAKOUT/PULLBACK/VWAP_RECLAIM/HOOK_TURN/WEAK_TO_STRONG）。
    # 装配在这里而非 orders.py（本包不改 orders.py）：orders 的 mode="signal" 分支按
    # `self.signal_provider(code, signal)` 调用，provider 为 None 时该类条件单永不触发。
    # provider 本身**不连行情**：真取分钟K发生在被调用时（内部懒 import app.core.quotes）。
    svc.signal_provider = make_signal_provider()
    return svc


def get_service() -> PaperOrderService:
    """进程单例；首次调用装配 PaperBroker + PaperOrderService。

    环境变量 PANEL_V2_PAPER_DIR 覆盖账本目录（测试用）。重复调用直接返回同一实例。
    """
    global _singleton
    if _singleton is None:
        with _lock:
            if _singleton is None:
                _singleton = build_service()
    return _singleton


def reset_service() -> None:
    """测试用：丢弃单例（**不删文件**，下一个 get_service() 重新按当前 env 装配）。"""
    global _singleton
    with _lock:
        _singleton = None


def service_status(svc: Optional[PaperOrderService] = None) -> Dict[str, Any]:
    """装配状态摘要（排查用；不含持仓明细，明细走 account_view()）。"""
    s = svc or get_service()
    paths = state.get_paths(s.state_dir)
    return {"mode": s.trade_mode(),                       # 恒 "paper"
            "live_order_enabled": s.live_order_enabled(), # 恒 False
            "state_dir": str(paths.root),
            "files": {
                "account": paths.account.exists(),
                "trades": paths.trades.exists(),
                "conditions": paths.conditions.exists(),
                "freeze": paths.freeze.exists(),
                "kill_switch": paths.kill_switch.exists(),
            }}


def wanted_codes(svc: Optional[PaperOrderService] = None) -> List[str]:
    """feed 订阅清单 = 持仓 + ACTIVE 条件单 + REPORTED/PARTIAL 挂单，去重排序。

    4.1.3 的 feed 每 15 秒重读一次本函数，对新增代码做增量订阅。
    只读账本，不写文件、不改状态。
    """
    return (svc or get_service()).wanted_codes()


# ---------------------------------------------------------------- 行情（4.1.3b）
#
# 下面三个函数按「模块级函数 + 末尾挂到 PaperOrderService」的方式实现，
# 原因见模块头【4.1.3b 追加】：本任务不允许改 orders.py，而任务书 §4 要求
# 插件写 `svc.quote_for(...)`。挂载后行为与写在类里完全一致（实例方法），
# 也方便测试对 `PaperOrderService.quote_for` 直接 monkeypatch。


def _quote_for(self: PaperOrderService, ts_code: str) -> Dict[str, Any]:
    """下单/估值行情：`feed.order_quote` 优先；feed 不可用 → `quotes.order_quote`；都失败 → `{}`。

    为什么要两级：feed 的 `order_quote` 本身就是「eltdx 打底 + 5 秒内 QMT 盘口覆盖」，
    正常情况下它已经包含 eltdx 兜底；只有 feed 模块整体不可用（未启动 / import 失败）
    或它返回空时，才直接走 `quotes.order_quote`（同一份 eltdx 口径）。

    **不抛、不编造**：任何异常都退化为 `{}`，调用方（下单一侧）据此拒绝下单。
    """
    code = str(ts_code or "").strip()
    if not code:
        return {}
    # 一级：feed（懒 import，避免 core 内部循环依赖与「没装 QMT 行情库就连带 paper 不可用」）
    try:
        from app.core.feed import get_feed  # noqa: PLC0415 - 懒 import

        q = get_feed().order_quote(code)
        if isinstance(q, dict) and q:
            return q
    except Exception:  # noqa: BLE001 - feed 不可用不影响下单行情降级
        pass
    # 二级：eltdx 直取（与 feed 打底同源）
    try:
        from app.core import quotes as _quotes  # noqa: PLC0415 - 懒 import

        q = _quotes.order_quote(code)
        return q if isinstance(q, dict) else {}
    except Exception:  # noqa: BLE001 - 行情源故障 → 空字典（下单一侧按 no_quote 处理）
        return {}


def _quote_map(self: PaperOrderService, codes: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """批量版 `quote_for`：`{code: quote}`，**单个失败不影响其他**（取不到的码不出现）。

    与 feed 的 `snapshot()` 同语义：不给出空壳，调用方用 `.get(code)` 判断有没有行情。
    """
    if isinstance(codes, str):
        codes = [codes]
    out: Dict[str, Dict[str, Any]] = {}
    for code in codes or ():
        key = str(code or "").strip()
        if not key:
            continue
        try:
            q = self.quote_for(key)
        except Exception:  # noqa: BLE001 - 单只失败不牵连整批
            continue
        if isinstance(q, dict) and q:
            out[key] = q
    return out


# ---------------------------------------------------------------- 独立评估进程让位（2026-10-28）
#
# 背景：条件单评估原本只活在 feed 派发线程上 ⇒ 面板一关就没人评估（调研 §6 R2）。
# `app/cond_watch_v2.py` 是接管评估的独立常驻进程，每轮写 `cond_watch_heartbeat.json`。
# 口径：**同一时刻只有一个评估者** —— 它的心跳新鲜（ok=True 且 <30s）时本进程让位；
# 心跳缺失/过期/ok=False（进程死了或优雅退出）→ 本进程兜底评估。
#
# 心跳窗口必须与 app/cond_watch_v2.py 的 HEARTBEAT_MAX_AGE_S 一致（tests 有对拍）。
_COND_WATCH_MAX_AGE_S = 30.0
# feed 每 50ms 派发一次：逐 tick 读盘既浪费，又会在对方原子替换的瞬间读到 None（误判"不在场"）。
# 缓存 3s：让位判定最多滞后 3s，远小于 30s 窗口。
_COND_WATCH_CACHE: Dict[str, Any] = {"at": 0.0, "alive": False}


def _cond_watch_heartbeat_file():
    """独立进程心跳路径（与 app/cond_watch_v2.py::HEARTBEAT_FILE 同一条）。"""
    return _core_paths.OUT_DIR / "cond_watch_heartbeat.json"


def _cond_watch_alive(now: Optional[float] = None) -> bool:
    """独立评估进程是否在场（心跳新鲜且 ok）。任何异常 → False（本进程兜底）。"""
    now = time.time() if now is None else float(now)
    if now - float(_COND_WATCH_CACHE["at"]) < 3.0:
        return bool(_COND_WATCH_CACHE["alive"])
    hb = state.read_json(_cond_watch_heartbeat_file(), None)
    if isinstance(hb, dict):
        try:
            ts = float(hb.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        alive = bool(hb.get("ok")) and 0.0 <= now - ts < _COND_WATCH_MAX_AGE_S
        _COND_WATCH_CACHE.update({"at": now, "alive": alive})
        return alive
    # 文件不存在 / 正被原子替换（Windows 短窗口）：沿用上次结论，最多沿用 MAX_AGE 秒
    if _COND_WATCH_CACHE["alive"] and now - float(_COND_WATCH_CACHE["at"]) < _COND_WATCH_MAX_AGE_S:
        return True
    _COND_WATCH_CACHE.update({"at": now, "alive": False})
    return False


def _on_quotes(self: PaperOrderService, quotes: Dict[str, Dict[str, Any]]) -> None:
    """feed listener：`evaluate`（条件单）+ `match_pending`（挂单补成交）。

    - **让位**：独立评估进程（app/cond_watch_v2.py）心跳新鲜时整段跳过，只在状态切换时
      记一条 audit（不能每 tick 写，feed 是 50ms 一次）。
    - 只处理**有 `last`** 的项：没有最新价的 tick 对撮合毫无意义（旧价/坏数据不许成交）。
    - 异常一律吞掉并写 audit：本函数跑在 feed 的派发线程上，抛出去会让整条行情
      派发链断掉（listener 异常虽然被 feed 计数，但连续失败等于纸面交易停摆）。
    - `self._session_ok()` 的门槛仍在 `evaluate` / `match_pending` 内部（非交易时段
      不撮合），这里不重复判断，避免两处口径打架。
    """
    if not isinstance(quotes, dict) or not quotes:
        return
    if _cond_watch_alive():
        if not getattr(self, "_cond_watch_yield_logged", False):
            self._cond_watch_yield_logged = True
            _audit_quiet(self, "feed.yield_to_cond_watch",
                         "cond_watch 心跳新鲜：面板让位，条件单由独立进程评估")
        return
    if getattr(self, "_cond_watch_yield_logged", False):
        self._cond_watch_yield_logged = False
        _audit_quiet(self, "feed.resume_after_cond_watch",
                     "cond_watch 心跳缺失/过期：面板恢复评估")
    usable: Dict[str, Dict[str, Any]] = {}
    for code, q in quotes.items():
        if not isinstance(q, dict):
            continue
        last = q.get("last")
        try:
            if last is None or float(last) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        usable[str(code)] = q
    if not usable:
        return
    try:
        self.evaluate(usable)
    except Exception as exc:  # noqa: BLE001 - feed 线程绝不允许被条件单评估打死
        _audit_quiet(self, "feed.evaluate_error", "%s: %s" % (type(exc).__name__, exc))
    try:
        self.match_pending(usable)
    except Exception as exc:  # noqa: BLE001 - 同上
        _audit_quiet(self, "feed.match_pending_error", "%s: %s" % (type(exc).__name__, exc))


def _audit_quiet(self: PaperOrderService, event: str, detail: str) -> None:
    """写一条账本审计（失败静默）：feed 线程里的异常留痕，不允许再抛一层。"""
    try:
        state.audit(event, detail, root=self.state_dir)
    except Exception:  # noqa: BLE001 - 留痕失败不影响任何主流程
        pass


# 挂到 PaperOrderService（模块导入即完成；写在类上等价于实例方法）
PaperOrderService.quote_for = _quote_for           # type: ignore[attr-defined]
PaperOrderService.quote_map = _quote_map           # type: ignore[attr-defined]
PaperOrderService.on_quotes = _on_quotes           # type: ignore[attr-defined]

