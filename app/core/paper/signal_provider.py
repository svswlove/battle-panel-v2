# -*- coding: utf-8 -*-
"""app/core/paper/signal_provider.py · `signal_buy` 条件单的信号判定提供器（任务书 4.4）

【职责】把 `signals/library.py` 的五类信号接到 `PaperOrderService.signal_provider` 上。
`orders.py` 已按 `self.signal_provider(code, signal)` 调用（`spec["mode"] == "signal"`），
本模块只负责「取当日分钟K → 转 Candle → 交给信号类判定 → 返回 bool」。

【与旧端同口径】旧 8800 `engine/core/order_runtime.py::_signal_check`（2026-10-30 前在 `engine/server.py`）：eltdx 取当日分钟K →
`signals/library.py` 的信号类 `evaluate()` → `triggered`。本模块逐条对齐：

1. 信号名大小写归一；未知信号 → False（不猜、不降级成别的信号）；
2. `quotes.normalize(ts_code)` 归一失败 → False；
3. `quotes.minute_bars(glb, 241)`；**只保留 `time` 为今天的 bar**（eltdx 的 `count` 是跨日的，
   不滤会把昨天的K线当今天判）；不足 5 根 → False；
4. 转 `library.Candle`：`ts` 取 `time.strftime`，OHLC 优先 `*_price`（eltdx 字段名），
   否则退 `open/high/low/close`；`volume` 取 `volume_lots`（手，eltdx 口径）否则 `volume`；
   `amount` 缺省 0；
5. 结果缓存 `(glb, signal) → (monotonic, bool)`，TTL 20 秒（与旧端 `_SIGNAL_CACHE` 同口径：
   feed 每几秒评估一次，同一根分钟K不必反复算）；
6. **任何异常 → False**（宁可不买，不瞎买：行情断、字段缺失、库内部异常都不许变成「买」）。

【边界】
- 本模块 import 期**不连行情**：`app.core.quotes` 只在 provider **首次被调用**时懒 import
  （`quotes` 自己也是懒建 eltdx 单例），所以 `make_signal_provider()` 本身零副作用。
- 不写文件、不写账本；纯计算 + 只读行情。
- 纸面包硬约束：本文件不出现任何真实行情/交易库关键字
  （`app/core/paper/tests/test_broker.py` 会对 `app/core/paper/` 全目录静态扫描）。

【缓存位置】缓存挂在**每个 provider 实例的闭包**里（不是模块级全局）：生产只有一个 provider
（service 装配一次），语义与旧端全局缓存等价；测试注入多个 provider 时互不串味。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.paper.signals import library

__all__ = ["SIGNAL_CLASSES", "CACHE_TTL", "MIN_BARS", "BARS_COUNT", "make_signal_provider"]

# 信号名 → 信号类（与 orders.SIGNAL_NAMES 一一对应：BREAKOUT / PULLBACK / VWAP_RECLAIM /
# HOOK_TURN / WEAK_TO_STRONG）。MACD_DIVERGENCE（§11.1 只进待机，不是买入信号）与
# STOP（止损另有专用条件单类型）不注册——建单侧 orders.add_condition 已把它们拦掉。
SIGNAL_CLASSES: Dict[str, Any] = {
    "BREAKOUT": library.BreakoutSignal,
    "PULLBACK": library.PullbackSignal,
    "VWAP_RECLAIM": library.VwapReclaimSignal,
    "HOOK_TURN": library.HookTurnSignal,
    "WEAK_TO_STRONG": library.WeakToStrongSignal,
}

# 缓存 TTL（秒）：与旧端 `_SIGNAL_CACHE` 同口径（20 秒）
CACHE_TTL = 20.0

# 当日分钟K最少根数：不足 5 根任何形态都判不了（开盘头几分钟 / 停牌 / 取数失败）
MIN_BARS = 5

# 分钟K取数根数（A 股一日 241 根：09:30–11:30 + 13:00–15:00）
BARS_COUNT = 241


def _to_candle(bar: Any) -> library.Candle:
    """一根分钟K → `library.Candle`（字段缺失抛异常，由 provider 统一兜成 False）。

    OHLC 兼容两套命名：eltdx 用 `open_price` / `high_price` / `low_price` / `close_price`，
    回放/假数据用 `open` / `high` / `low` / `close`。成交量优先 `volume_lots`（手），
    否则 `volume`（股/手口径由数据源保证一致）。
    """
    t = getattr(bar, "time", None)
    ts = t.strftime("%Y-%m-%d %H:%M:%S")

    def _pick(*names: str) -> Optional[float]:
        for name in names:
            v = getattr(bar, name, None)
            if v is not None:
                return float(v)
        return None

    o = _pick("open_price", "open")
    h = _pick("high_price", "high")
    lo = _pick("low_price", "low")
    c = _pick("close_price", "close")
    if o is None or h is None or lo is None or c is None:
        raise ValueError("分钟K缺 OHLC 字段")
    return library.Candle(ts=ts, open=o, high=h, low=lo, close=c,
                          volume=_pick("volume_lots", "volume") or 0.0,
                          amount=_pick("amount") or 0.0)


def _today_candles(quotes: Any, glb: str) -> List[library.Candle]:
    """取当日分钟K并转 Candle：跨日残留 bar 丢掉，没有时间戳的丢掉。"""
    bars = quotes.minute_bars(glb, BARS_COUNT) or []
    today = time.strftime("%Y%m%d")
    out: List[library.Candle] = []
    for bar in bars:
        t = getattr(bar, "time", None)
        if t is None or not hasattr(t, "strftime"):
            continue                      # 无时间戳 → 无法确认是「今天」，丢掉
        if t.strftime("%Y%m%d") != today:
            continue                      # 昨天/更早的残留（eltdx count 跨日）
        out.append(_to_candle(bar))
    return out


def make_signal_provider(quotes_module: Any = None) -> Callable[[str, str], bool]:
    """造一个 `signal_provider(code, signal) -> bool`（喂 `PaperOrderService.signal_provider`）。

    - `quotes_module` 不传 = 运行时懒 import `app.core.quotes`（生产路径）；
      测试传假模块（有 `normalize` / `minute_bars` 两个函数即可），**零网络**。
    - 返回的闭包**永不抛**：任何异常 → False（宁可不买）。
    - 缓存：同一 provider 内 `(glb, signal)` 20 秒。异常路径**不写缓存**——
      行情断了不该被记成「20 秒内一定没信号」，等下一轮评估重试。
    """
    cache: Dict[Tuple[str, str], Tuple[float, bool]] = {}

    def _quotes() -> Any:
        """取行情模块：注入优先，否则懒 import（import 期绝不连行情）。"""
        if quotes_module is not None:
            return quotes_module
        from app.core import quotes as _q  # noqa: PLC0415 - 懒 import：避免 import 期连行情与循环依赖

        return _q

    def provider(ts_code: str, signal: str) -> bool:
        """判定 `ts_code` 的 `signal`（BREAKOUT/PULLBACK/...）此刻是否触发。永不抛。"""
        try:
            name = str(signal or "").strip().upper()
            cls = SIGNAL_CLASSES.get(name)
            if cls is None:
                return False              # 未知信号：不触发（也不猜近似信号）
            q = _quotes()
            glb = q.normalize(str(ts_code or ""))
            if not glb:
                return False              # 代码归一失败：拿不到行情，不触发
            key = (glb, name)
            now = time.monotonic()
            hit = cache.get(key)
            if hit is not None and (now - hit[0]) < CACHE_TTL:
                return hit[1]             # 20 秒内同一 (代码, 信号) 直接复用（与旧端同口径）
            candles = _today_candles(q, glb)
            if len(candles) < MIN_BARS:
                result = False            # K 线不足：不判、不触发
            else:
                result = bool(getattr(cls().evaluate(candles), "triggered", False))
            cache[key] = (now, result)
            return result
        except Exception:  # noqa: BLE001 - 信号判定任何异常一律「不买」
            return False

    return provider
