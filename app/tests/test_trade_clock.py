# -*- coding: utf-8 -*-
"""交易日时钟门面测试（2026-11-07）。

重点：周一~周五/周末、盘中/盘后、日历不可用兜底——这三类决定“该不该落盘/该显示哪一天”。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import trade_clock as tc  # noqa: E402


class _FakeCal:
    def __init__(self, days):
        self.days = {dt.date.fromisoformat(d) if isinstance(d, str) else d for d in days}

    def is_trading_day(self, d):
        return d in self.days

    def get_trading_dates(self, start, end):
        s = dt.date.fromisoformat(str(start)[:10]) if "-" in str(start) else dt.datetime.strptime(str(start), "%Y%m%d").date()
        e = dt.date.fromisoformat(str(end)[:10]) if "-" in str(end) else dt.datetime.strptime(str(end), "%Y%m%d").date()
        return [d.isoformat() for d in sorted(self.days) if s <= d <= e]


@pytest.fixture
def fake_cal(monkeypatch):
    # 2026-11-05(四)、11-06(五) 是交易日；11-07/08 周末
    days = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    monkeypatch.setattr(tc, "_CAL", {"tried": True, "mod": _FakeCal(days), "error": None})
    monkeypatch.setattr(tc, "_DEGRADED", {"active": False, "reason": None})
    return _FakeCal(days)


def test_weekend_is_not_trading_day(fake_cal):
    assert tc.is_trading_day("2026-11-07") is False
    assert tc.is_trading_day("2026-11-08") is False


def test_intraday_expects_previous_trade_date(fake_cal):
    now = dt.datetime(2026, 11, 6, 10, 30)
    assert tc.expected_data_date8(now) == "20261105"
    assert tc.postclose(now) is False


def test_postclose_before_1640_is_not_ready(fake_cal):
    now = dt.datetime(2026, 11, 6, 16, 30)
    assert tc.postclose(now) is False, "16:40 前链可能还在写库"
    assert tc.expected_data_date8(now) == "20261106", "收盘后数据日就是当天"


def test_postclose_after_1640_is_ready(fake_cal):
    now = dt.datetime(2026, 11, 6, 16, 45)
    assert tc.postclose(now) is True


def test_weekend_expects_latest_trade_date(fake_cal):
    now = dt.datetime(2026, 11, 7, 10, 0)
    assert tc.expected_data_date8(now) == "20261106"
    assert tc.postclose(now) is True, "非交易日数据固定"


def test_calendar_unavailable_falls_back_to_weekday(monkeypatch):
    monkeypatch.setattr(tc, "_CAL", {"tried": True, "mod": None, "error": "boom"})
    monkeypatch.setattr(tc, "_DEGRADED", {"active": False, "reason": None})
    assert tc.is_trading_day("2026-11-06") is True, "日历坏了也按工作日兜底"
    assert tc.is_trading_day("2026-11-07") is False, "周末不需要日历"
    assert tc.degraded()["active"] is True
    assert "boom" in (tc.degraded()["reason"] or "")


def test_prev_trading_date_skips_weekend(fake_cal):
    assert tc.prev_trading_date("2026-11-09").isoformat() == "2026-11-06"
    assert tc.latest_trading_date("2026-11-07").isoformat() == "2026-11-06"


def test_status_payload_shape(fake_cal):
    s = tc.status(dt.datetime(2026, 11, 6, 17, 0))
    assert set(s) >= {"now", "today", "is_trading_day", "expected_data_date", "postclose", "degraded"}
    assert s["today"] == "20261106"
    assert s["expected_data_date"] == "20261106"
    assert s["postclose"] is True
