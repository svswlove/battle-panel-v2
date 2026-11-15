# -*- coding: utf-8 -*-
"""core 行情门面（quotes）契约测试。

不打真实网络：`_client` 用假对象替换，验证门面自己的逻辑
（归一化 / 缓存 / 断线重建一次 / 降级不抛）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import quotes as Q  # noqa: E402


# ---------------------------------------------------------------- 1. 代码归一化


@pytest.mark.parametrize("raw,want", [
    ("600519.SH", "sh600519"),
    ("sh600519", "sh600519"),
    ("600519", "sh600519"),
    ("000001.SZ", "sz000001"),
    ("300750.SZ", "sz300750"),
    ("920087.BJ", "bj920087"),
    ("bj920087", "bj920087"),
    ("000001.SH", "sh000001"),      # 带后缀按后缀走，不串到平安银行
    ("399006.SZ", "sz399006"),
    ("512480.SH", "sh512480"),
])
def test_normalize(raw: str, want: str) -> None:
    assert Q.normalize(raw) == want


@pytest.mark.parametrize("raw", ["", None, "60051", "abcdef", "600519.XX", "12345678"])
def test_normalize_rejects(raw) -> None:
    assert Q.normalize(raw) is None


def test_to_ts_code_roundtrip() -> None:
    assert Q.to_ts_code("sh600519") == "600519.SH"
    assert Q.to_ts_code("bj920087") == "920087.BJ"
    assert Q.to_ts_code("garbage") is None


# ---------------------------------------------------------------- 2. 缓存


def test_cache_ttl_and_eviction() -> None:
    c = Q._Cache(keep=2)
    c.put("a", 1)
    assert c.get("a", 10) == 1
    assert c.get("a", 0) == None  # noqa: E711 - TTL=0 视为立即过期
    c.put("b", 2)
    c.put("c", 3)  # 超上限，淘汰最旧的 a
    assert c.get("b", 10) == 2
    assert c.get("c", 10) == 3
    assert c.get("a", 10) is None


# ---------------------------------------------------------------- 3. 连接与降级


class _FakeDepth:
    records = [object()]


class _FakeTicks:
    def __init__(self, n: int) -> None:
        self.ticks = [object()] * n
        self.count = n


class _FakeBars:
    def __init__(self, n: int) -> None:
        self.bars = [object()] * n


class _FakeCli:
    """可配置失败次数的假客户端。"""

    def __init__(self, fail_depth: int = 0) -> None:
        self.fail_depth = fail_depth
        self.closed = 0
        self.calls = 0
        self.quotes = self
        self.trades = self
        self.bars = self

    def get_depth(self, glb):  # noqa: ANN001
        self.calls += 1
        if self.fail_depth > 0:
            self.fail_depth -= 1
            raise RuntimeError("socket dead")
        return _FakeDepth()

    def today(self, glb, start=0, count=1800):  # noqa: ANN001
        return _FakeTicks(count if start == 0 else 0)

    def get(self, glb, period="1m", count=241):  # noqa: ANN001
        return _FakeBars(count)

    def close(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _reset():
    Q.reset()
    yield
    Q.reset()


def test_depth_returns_record(monkeypatch) -> None:
    fake = _FakeCli()
    monkeypatch.setattr(Q, "_client", lambda: fake)
    assert Q.depth("sh600519") is not None
    # 第二次命中缓存，不再调上游
    n = fake.calls
    assert Q.depth("sh600519") is not None
    assert fake.calls == n


def test_depth_reconnects_once_then_gives_up(monkeypatch) -> None:
    """首次失败 → 丢弃连接重建一次；仍失败返回 None（不抛）。"""
    fake = _FakeCli(fail_depth=5)
    monkeypatch.setattr(Q, "_client", lambda: fake)
    assert Q.depth("sh600519") is None


def test_depth_survives_single_failure(monkeypatch) -> None:
    """失败一次后重建能拿到值。"""
    state = {"n": 0}

    class Flaky(_FakeCli):
        def get_depth(self, glb):  # noqa: ANN001
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("first fail")
            return _FakeDepth()

    monkeypatch.setattr(Q, "_client", lambda: Flaky())
    assert Q.depth("sh600519") is not None


def test_client_unavailable_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(Q, "_client", lambda: None)
    assert Q.depth("sh600519") is None
    assert Q.today_ticks("sh600519") == []
    assert Q.minute_bars("sh600519") == []
    assert Q.daily_bars("sh600519") == []


# ---------------------------------------------------------------- 4. 分钟线必须按日过滤


class _Bar:
    def __init__(self, day: str, hhmm: str) -> None:
        from datetime import datetime as _dt
        self.time = _dt.strptime(day + hhmm, "%Y%m%d%H:%M")
        self.close = 1.0


def test_minute_bars_filters_to_last_trading_day(monkeypatch) -> None:
    """eltdx 的 count 是**跨日**的最近 N 根，必须按末根日期过滤。

    2026-10-23 实测：count=241 取回「昨日 11:30 → 今日 13:00」跨两天 241 根，
    不过滤会让时间轴错位（缺 09:30–11:29、多出昨天下午）。
    """
    rows = ([_Bar("20261022", "%02d:%02d" % (h, m)) for h in (13, 14) for m in range(0, 60)]
            + [_Bar("20261023", "%02d:%02d" % (h, m)) for h in (9, 10, 11) for m in range(0, 60)])

    def fake_call(fn_name, *args, **kwargs):  # noqa: ANN001
        class R:
            bars = rows
        return R()

    monkeypatch.setattr(Q, "call", fake_call)
    out = Q.minute_bars("sh600519", 241)
    assert out, "应返回最后一日的根"
    days = {b.time.strftime("%Y%m%d") for b in out}
    assert days == {"20261023"}, f"必须只留最后一日，实际 {days}"
    times = [b.time for b in out]
    assert times == sorted(times)


def test_today_ticks_paginates_until_short_page(monkeypatch) -> None:
    """分页：满页继续，不满页停。"""
    calls = []

    def fake_call(fn_name, glb, start=0, count=1800, **kwargs):  # noqa: ANN001
        calls.append(start)
        n = 1800 if start < 3600 else 100

        class R:
            ticks = [object()] * n
        R.count = n
        return R()

    monkeypatch.setattr(Q, "call", fake_call)
    ticks = Q.today_ticks("sh600519", pages=6, page_size=1800)
    assert len(ticks) == 1800 + 1800 + 100
    assert calls == [0, 1800, 3600]
