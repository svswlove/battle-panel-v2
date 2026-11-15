# -*- coding: utf-8 -*-
"""app/core/paper/signal_provider.py 单测（4.4）：signal_buy 条件单接信号库

【零网络】全部用**假 quotes 模块**（只有 `normalize` / `minute_bars` 两个函数）：
不连 eltdx、不连 QMT、不开 socket、不写真实账本（账本一律 tmp_path）。

覆盖（任务书 §4）：
1. `test_unknown_signal_false`：未知信号名 → False（不猜近似信号）；
2. `test_insufficient_bars_false`：当日K < 5 根 → False；
3. `test_quote_failure_false`：`minute_bars` 抛异常 → False（宁可不买）；
4. `test_provider_returns_bool_and_caches`：返回 bool；20 秒内同一 (code, signal) 不重复取K；
5. `test_breakout_signal_with_synthetic_bars`：纯库测试——横盘 21 根 + 末根放量突破 →
   `BreakoutSignal().evaluate(Candle list).triggered is True`；
6. `test_signal_buy_condition_uses_provider`：`PaperOrderService` 注入 provider=True →
   `evaluate()` 触发；provider=False → 不触发；
7. `test_build_service_wires_signal_provider`：`service.build_service()` 装配后
   `svc.signal_provider` 是可调用的（且装配期不连行情）。

另附：`test_provider_ignores_previous_days_bars`——跨日残留 bar 必须被丢掉（eltdx 的
`count` 是跨日的，不滤会把昨天的K线当今天判）。
"""
from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.paper import service as paper_service  # noqa: E402
from app.core.paper import state  # noqa: E402
from app.core.paper.broker import PaperBroker  # noqa: E402
from app.core.paper.orders import PaperOrderService  # noqa: E402
from app.core.paper.signals import library  # noqa: E402
from app.core.paper.signal_provider import make_signal_provider  # noqa: E402

CODE = "600519.SH"
GLB = "sh600519"

# 固定下单行情（与 test_orders.py 同口径：现价 10.00，涨跌停 11.00/9.00）
QUOTE = {"last": 10.00, "pre_close": 10.00, "limit_up": 11.00, "limit_down": 9.00,
         "quote_time": "10:30:00", "fresh": True}


# ---------------------------------------------------------------- 假行情
class FakeBar:
    """一条分钟K（字段名对齐 eltdx：`*_price` + `volume_lots`）。"""

    def __init__(self, dt: _dt.datetime, o: float, h: float, low: float, c: float,
                 v: float, amount: Optional[float] = None):
        self.time = dt
        self.open_price = o
        self.high_price = h
        self.low_price = low
        self.close_price = c
        self.volume_lots = v
        self.amount = amount if amount is not None else o * v * 100


class FakeBarAlt:
    """同一条K的另一种字段命名（`open`/`volume`），验证 provider 的字段兼容。"""

    def __init__(self, dt: _dt.datetime, o: float, h: float, low: float, c: float, v: float):
        self.time = dt
        self.open = o
        self.high = h
        self.low = low
        self.close = c
        self.volume = v
        self.amount = 0.0


class FakeQuotes:
    """假 quotes 模块：只实现 provider 用到的两个函数（零网络）。"""

    def __init__(self, bars: List[Any], *, normalize_to: Optional[str] = GLB,
                 raises: bool = False):
        self._bars = bars
        self._normalize_to = normalize_to
        self._raises = raises
        self.calls: List[str] = []

    def normalize(self, code: str) -> Optional[str]:
        return self._normalize_to

    def minute_bars(self, glb: str, count: int = 241) -> List[Any]:
        self.calls.append(glb)
        if self._raises:
            raise RuntimeError("行情源故障（假）")
        return list(self._bars)


# ---------------------------------------------------------------- 造 bar 工具
def _today_at(hh: int, mm: int, second: int = 0) -> _dt.datetime:
    """今天的 HH:MM（本地）；用于「当日分钟K」口径。"""
    now = _dt.datetime.now()
    return now.replace(hour=hh, minute=mm, second=second, microsecond=0)


def _yesterday_at(hh: int = 14, mm: int = 59) -> _dt.datetime:
    """昨天的 HH:MM（验证跨日残留被丢掉）。"""
    return _today_at(hh, mm) - _dt.timedelta(days=1)


def _flat_bars(n: int, *, price: float = 10.0, volume: float = 1000.0,
               start_minute: int = 0) -> List[FakeBar]:
    """n 根横盘K：开=收=price，高 = price*1.001，低 = price*0.999（开盘后第 start_minute 分起）。"""
    out = []
    for i in range(n):
        m = start_minute + i
        dt = _today_at(9 + (30 + m) // 60, (30 + m) % 60)
        out.append(FakeBar(dt, price, price * 1.001, price * 0.999, price, volume))
    return out


def _breakout_bars(n_flat: int = 21, *, price: float = 10.0, volume: float = 1000.0
                   ) -> List[FakeBar]:
    """n_flat 根横盘 + 1 根放量突破（收盘 10.5 > 前 20 根最高 10.01，量 5 倍）。"""
    bars = _flat_bars(n_flat, price=price, volume=volume)
    last_dt = bars[-1].time + _dt.timedelta(minutes=1)
    bars.append(FakeBar(last_dt, price, 10.6, price * 0.999, 10.5, volume * 5))
    return bars


def _to_candles(bars: List[FakeBar]) -> List[Any]:
    """假 bar → library.Candle（纯库测试用；provider 内部另有一套转换）。"""
    return [library.Candle(ts=b.time.strftime("%Y-%m-%d %H:%M:%S"),
                           open=b.open_price, high=b.high_price, low=b.low_price,
                           close=b.close_price, volume=b.volume_lots, amount=b.amount)
            for b in bars]


# ---------------------------------------------------------------- 1~3. 失败路径
def test_unknown_signal_false():
    """未知信号名 → False（不猜、不降级成别的信号，也不去取K）。"""
    q = FakeQuotes(_breakout_bars())
    prov = make_signal_provider(q)
    assert prov(CODE, "NOPE") is False
    assert prov(CODE, "") is False
    assert q.calls == [], "未知信号不该触发取数"


def test_signal_name_is_case_insensitive():
    """信号名大小写归一：`breakout` 与 `BREAKOUT` 同一条路径。"""
    q = FakeQuotes(_breakout_bars())
    prov = make_signal_provider(q)
    assert prov(CODE, "breakout") is True
    assert prov(CODE, "  BreakOut  ") is True


def test_insufficient_bars_false():
    """当日K不足 5 根 → False（开盘头几分钟，形态无法判定）。"""
    prov = make_signal_provider(FakeQuotes(_flat_bars(3)))
    assert prov(CODE, "BREAKOUT") is False


def test_quote_failure_false():
    """`minute_bars` 抛异常 → False（宁可不买，不瞎买）；且异常不写缓存。"""
    q = FakeQuotes([], raises=True)
    prov = make_signal_provider(q)
    assert prov(CODE, "BREAKOUT") is False
    assert prov(CODE, "BREAKOUT") is False
    assert len(q.calls) == 2, "异常路径不得写缓存（下轮评估要重试）"


def test_normalize_failure_false():
    """代码归一失败 → False（拿不到行情就不触发）。"""
    q = FakeQuotes(_breakout_bars(), normalize_to=None)
    prov = make_signal_provider(q)
    assert prov("NOTACODE", "BREAKOUT") is False
    assert q.calls == []


def test_provider_ignores_previous_days_bars():
    """跨日残留 bar 被丢掉：昨天的足量K不能让今天触发。"""
    old = _breakout_bars()
    for b in old:
        b.time = b.time - _dt.timedelta(days=1)
    prov = make_signal_provider(FakeQuotes(old))
    assert prov(CODE, "BREAKOUT") is False


# ---------------------------------------------------------------- 4. 返回值与缓存
def test_provider_returns_bool_and_caches():
    """返回 bool；20 秒缓存内同一 (code, signal) 不重复调 `minute_bars`。"""
    q = FakeQuotes(_breakout_bars())
    prov = make_signal_provider(q)
    first = prov(CODE, "BREAKOUT")
    assert isinstance(first, bool)
    assert first is True                      # 横盘 21 根 + 末根放量突破 → 触发
    assert len(q.calls) == 1
    second = prov(CODE, "BREAKOUT")           # 20 秒内命中缓存
    assert second is True
    assert len(q.calls) == 1, "缓存窗口内不应重复取分钟K"
    prov(CODE, "PULLBACK")                    # 不同信号 → 不同缓存键
    assert len(q.calls) == 2


def test_cache_expires_after_ttl(monkeypatch):
    """缓存 20 秒过期后重新取数（TTL 与旧端 `_SIGNAL_CACHE` 同口径）。"""
    q = FakeQuotes(_breakout_bars())
    prov = make_signal_provider(q)
    assert prov(CODE, "BREAKOUT") is True
    assert len(q.calls) == 1
    # 把 monotonic 往前挪 21 秒（provider 内部用 time.monotonic 计时）
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 21.0)
    assert prov(CODE, "BREAKOUT") is True
    assert len(q.calls) == 2, "超过 20 秒必须重新取数"


def test_provider_accepts_alt_field_names():
    """字段兼容：`open/high/low/close/volume` 命名的 bar 同样能判定。"""
    bars = [FakeBarAlt(b.time, b.open_price, b.high_price, b.low_price, b.close_price,
                       b.volume_lots) for b in _breakout_bars()]
    prov = make_signal_provider(FakeQuotes(bars))
    assert prov(CODE, "BREAKOUT") is True


# ---------------------------------------------------------------- 5. 纯库测试（不经 provider）
def test_breakout_signal_with_synthetic_bars():
    """横盘 21 根 + 末根放量突破：`BreakoutSignal` 纯库判定为触发（不经过 provider）。"""
    res = library.BreakoutSignal().evaluate(_to_candles(_breakout_bars()))
    assert res.triggered is True
    assert res.ok is True
    assert res.meta["signal"] == "BREAKOUT"
    assert res.meta["vol_ratio"] >= 1.5


def test_breakout_signal_no_volume_no_trigger():
    """同样上穿但缩量 → 不触发（量能门槛有效，证明库没有被复制坏）。"""
    bars = _flat_bars(21)
    dt = bars[-1].time + _dt.timedelta(minutes=1)
    bars.append(FakeBar(dt, 10.0, 10.6, 9.99, 10.5, 100.0))   # 量只有均量的 1/10
    assert library.BreakoutSignal().evaluate(_to_candles(bars)).triggered is False


# ---------------------------------------------------------------- 6. 条件单接线
@pytest.fixture()
def svc(tmp_path: Path):
    """临时账本 + 固定交易时段的服务（`signal_provider` 由各用例自行注入）。"""
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    paths = state.get_paths(paper_dir)
    broker = PaperBroker(state_dir=paper_dir, initial_cash=100000.0)
    svc = PaperOrderService(broker=broker, state_dir=paper_dir,
                            engine_state_path=paths.engine_state,
                            kill_switch_path=paths.kill_switch,
                            freeze_path=paths.freeze,
                            session_ok=lambda: True)
    return svc


def test_signal_buy_condition_uses_provider(svc: PaperOrderService):
    """provider 返回 True → 条件单触发下市价买单；返回 False → 不触发。"""
    seen: List[str] = []
    svc.signal_provider = lambda code, sig: (seen.append((code, sig)), True)[1]
    cond = svc.add_condition(CODE, "signal_buy", 100, signal="BREAKOUT", quote=QUOTE)
    assert cond["status"] == "ACTIVE"
    out = svc.evaluate({CODE: {"last": 10.0, "fresh": True}})
    assert len(out["triggered"]) == 1
    assert seen and seen[0][1] == "BREAKOUT"

    # 换成「永远不触发」的 provider：新单不再被触发
    svc.signal_provider = lambda code, sig: False
    svc.add_condition(CODE, "signal_buy", 100, signal="PULLBACK", quote=QUOTE)
    out2 = svc.evaluate({CODE: {"last": 10.0, "fresh": True}})
    assert out2["triggered"] == []


def test_signal_buy_without_provider_never_triggers(svc: PaperOrderService):
    """provider 为 None → 永不触发（不编造信号）。"""
    svc.signal_provider = None
    svc.add_condition(CODE, "signal_buy", 100, signal="BREAKOUT", quote=QUOTE)
    assert svc.evaluate({CODE: {"last": 10.0, "fresh": True}})["triggered"] == []


# ---------------------------------------------------------------- 7. 装配
def test_build_service_wires_signal_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`build_service()` 装配后 `svc.signal_provider` 是可调用的；装配期不连行情。

    只断言「接上了」，不真调它（真调会懒 import `app.core.quotes` 并尝试连 eltdx）。
    """
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(paper_dir))
    svc = paper_service.build_service(session_ok=lambda: True)
    assert svc.signal_provider is not None
    assert callable(svc.signal_provider)
    assert svc.state_dir == str(state.get_paths(paper_dir).root)
