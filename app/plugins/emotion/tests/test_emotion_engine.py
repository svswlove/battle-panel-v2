# -*- coding: utf-8 -*-
"""情绪打分 · 口径单测（纯函数，无 IO、无数据库）。

守的是「公式 + 边界」：六因子的加减分公式、clamp 边界、缺失因子不参与、
滚动窗口含当日、分位并列按半分、档位阈值。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.emotion import engine  # noqa: E402


# ---------------------------------------------------------------- 档位

@pytest.mark.parametrize("score,label", [
    (100.0, "亢奋"), (70.0, "亢奋"), (69.99, "偏热"), (55.0, "偏热"),
    (54.99, "中性"), (40.0, "中性"), (39.99, "偏冷"), (25.0, "偏冷"),
    (24.99, "冰点"), (0.0, "冰点"),
])
def test_band_boundaries(score, label):
    assert engine.band_of(score) == label


def test_band_none_passthrough():
    assert engine.band_of(None) is None


# ---------------------------------------------------------------- 滚动分位

def test_rolling_median_includes_today_and_uses_available_samples():
    vals = [10.0, 20.0, 30.0, 40.0]
    out = engine.rolling_median(vals, window=60)
    # 样本不足窗口 → 用现有全部（含当日）
    assert out == [10.0, 15.0, 20.0, 25.0]


def test_rolling_median_window_length():
    vals = [1.0, 2.0, 3.0, 100.0]
    out = engine.rolling_median(vals, window=3)
    assert out[0] == 1.0
    assert out[1] == 1.5          # [1,2]
    assert out[2] == 2.0          # [1,2,3]
    assert out[3] == 3.0          # [2,3,100] → 3.0（100 进窗，1 出窗）


def test_rolling_median_skips_none():
    assert engine.rolling_median([None, 5.0, None], window=60) == [None, 5.0, 5.0]


def test_pct_rank_ties_count_half():
    sample = [1.0, 2.0, 2.0, 3.0, 4.0]
    assert engine.pct_rank(sample, 2.0) == pytest.approx(0.4)    # 1 个小于 + 2 个并列算 1
    assert engine.pct_rank(sample, 1.0) == pytest.approx(0.1)    # 最小值：只有并列那一份
    assert engine.pct_rank(sample, 5.0) == pytest.approx(1.0)


def test_pct_rank_needs_enough_samples():
    assert engine.pct_rank([1.0, 2.0], 2.0) is None       # 样本 <5 → 不参与
    assert engine.pct_rank([1.0] * 5, None) is None


# ---------------------------------------------------------------- 六因子公式

TODAY = {"zt_total": 39, "zt_ever": 64, "dt_total": 42, "height": 4, "lb2plus": 10}


def _by_key(factors):
    return {f["key"]: f for f in factors}


def test_six_factors_today_points():
    """2026-10-29 对答案（口径 v1.1）：涨停 39 / 曾涨停 64 / 跌停 42 / 最高 4 板 / 连板 10。

    晋级率 40% 但分母只有 5 只（<10）→ 样本不足、不给分。
    """
    factors = _by_key(engine.score_factors(
        TODAY, zt_threshold=60.5, promotion_rate=0.40, lb_percentile=0.2959, promotion_base=5))
    assert len(factors) == 6
    assert factors["zt_total"]["points"] == pytest.approx(-8.6)          # (39 − 60.5) × 0.4
    assert factors["promotion"]["participated"] is False                # 样本不足
    assert factors["promotion"]["points"] == pytest.approx(0.0)
    assert "样本不足" in factors["promotion"]["note"]
    assert factors["burst"]["points"] == pytest.approx(-19.06)          # 20 − 39.06（炸板率越低越加分）
    assert factors["burst"]["raw"] == pytest.approx(round((64 - 39) / 64, 4))   # 0.3906
    assert factors["dt_vs_zt"]["points"] == pytest.approx(-10.0)         # 跌停 42 > 涨停 39
    assert factors["height"]["points"] == pytest.approx(0.0)             # 4 板在 2~5 之间
    assert factors["lb2plus"]["points"] == pytest.approx(-4.08)          # (0.2959 − 0.5) × 20
    assert sum(1 for f in factors.values() if f["participated"]) == 5


def test_promotion_small_sample_guard():
    """分母 <10 只不给分；≥ 10 只才按公式算（2026-10-29 新增）。"""
    small = _by_key(engine.score_factors(
        TODAY, zt_threshold=60.5, promotion_rate=0.40, lb_percentile=0.5, promotion_base=9))
    assert small["promotion"]["participated"] is False
    assert small["promotion"]["points"] == 0.0

    ok = _by_key(engine.score_factors(
        TODAY, zt_threshold=60.5, promotion_rate=0.40, lb_percentile=0.5, promotion_base=10))
    assert ok["promotion"]["participated"] is True
    assert ok["promotion"]["points"] == pytest.approx(15.0)              # (0.40 − 0.15) × 100 → clamp +15


def test_promotion_without_sample_still_scores():
    """库内原值没有分母样本时按原口径参与（不假装样本不足，也不掉分）。"""
    factors = _by_key(engine.score_factors(
        TODAY, zt_threshold=60.5, promotion_rate=0.05, lb_percentile=0.5, promotion_base=None))
    assert factors["promotion"]["participated"] is True
    assert factors["promotion"]["points"] == pytest.approx(-10.0)        # (0.05 − 0.15) × 100


@pytest.mark.parametrize("zt,dt,expect", [
    (39, 42, -10.0),   # 跌停 > 涨停
    (60, 40, 10.0),    # 涨停 ≥ 跌停 × 1.5
    (60, 41, 0.0),     # 60 < 61.5 → 无方向
    (60, 40.0, 10.0),  # 恰好 1.5 倍算达标
])
def test_dt_vs_zt_directions(zt, dt, expect):
    factors = _by_key(engine.score_factors(
        {"zt_total": zt, "zt_ever": zt, "dt_total": dt, "height": 4, "lb2plus": 10},
        zt_threshold=zt, promotion_rate=0.15, lb_percentile=0.5))
    assert factors["dt_vs_zt"]["points"] == pytest.approx(expect)


@pytest.mark.parametrize("height,expect", [(2, -10.0), (3, 0.0), (4, 0.0), (5, 10.0), (8, 10.0)])
def test_height_steps(height, expect):
    factors = _by_key(engine.score_factors(
        {"zt_total": 50, "zt_ever": 50, "dt_total": 1, "height": height, "lb2plus": 10},
        zt_threshold=50, promotion_rate=0.15, lb_percentile=0.5))
    assert factors["height"]["points"] == pytest.approx(expect)


def test_zt_points_clamped():
    high = _by_key(engine.score_factors(
        {"zt_total": 300, "zt_ever": 300, "dt_total": 0, "height": 6, "lb2plus": 10},
        zt_threshold=50, promotion_rate=0.15, lb_percentile=0.5))
    assert high["zt_total"]["points"] == pytest.approx(20.0)
    low = _by_key(engine.score_factors(
        {"zt_total": 0, "zt_ever": 0, "dt_total": 0, "height": 6, "lb2plus": 10},
        zt_threshold=100, promotion_rate=0.15, lb_percentile=0.5))
    assert low["zt_total"]["points"] == pytest.approx(-20.0)


def test_missing_factors_do_not_participate():
    """晋级率/曾涨停缺失 → 不参与（points=0 且 participated=False），不补假中性值。"""
    factors = _by_key(engine.score_factors(
        {"zt_total": 39, "zt_ever": None, "dt_total": 42, "height": None, "lb2plus": None},
        zt_threshold=60.5, promotion_rate=None, lb_percentile=None))
    assert factors["promotion"]["participated"] is False
    assert factors["promotion"]["points"] == 0.0
    assert factors["burst"]["participated"] is False
    assert factors["height"]["participated"] is False
    assert factors["lb2plus"]["participated"] is False
    assert factors["zt_total"]["participated"] is True


def test_burst_zero_ever_is_unavailable():
    factors = _by_key(engine.score_factors(
        {"zt_total": 0, "zt_ever": 0, "dt_total": 0, "height": 5, "lb2plus": 5},
        zt_threshold=50, promotion_rate=0.2, lb_percentile=0.5))
    assert factors["burst"]["participated"] is False


# ---------------------------------------------------------------- 序列合成

def _rows():
    return [
        {"trade_date": "20260801", "zt_total": 100, "zt_ever": 120, "dt_total": 0, "height": 6, "lb2plus": 20},
        {"trade_date": "20260804", "zt_total": 80, "zt_ever": 100, "dt_total": 1, "height": 5, "lb2plus": 18},
        {"trade_date": "20260805", "zt_total": 40, "zt_ever": 70, "dt_total": 30, "height": 3, "lb2plus": 8},
    ]


def test_series_total_is_base_plus_factors_and_clamped():
    series = engine.score_series(_rows(), {"20260805": 0.05})
    for point in series:
        parts = sum(f["points"] for f in point["factors"] if f["participated"])
        assert point["score"] == pytest.approx(max(0.0, min(100.0, 50.0 + parts)), abs=0.01)
    # 晋级率只在有值那天参与；连板分位在 3 行样本上不可用（<5 样本）
    assert [p["n_factors"] for p in series] == [4, 4, 5]


def test_series_zt_threshold_uses_rolling_window():
    series = engine.score_series(_rows(), {})
    assert series[0]["zt_threshold"] == pytest.approx(100.0)          # 样本 1 天
    assert series[1]["zt_threshold"] == pytest.approx(90.0)           # [100,80] 中位
    assert series[2]["zt_threshold"] == pytest.approx(80.0)           # [100,80,40] 中位


def test_series_accepts_dict_samples():
    """promotions 支持 {rate, base}：base 传下去做小样本保护。"""
    rows = _rows()
    series = engine.score_series(rows, {"20260805": {"rate": 0.40, "base": 4}})
    promo = next(f for f in series[-1]["factors"] if f["key"] == "promotion")
    assert promo["participated"] is False          # base=4 < 10
    series2 = engine.score_series(rows, {"20260805": {"rate": 0.40, "base": 12}})
    promo2 = next(f for f in series2[-1]["factors"] if f["key"] == "promotion")
    assert promo2["participated"] is True
