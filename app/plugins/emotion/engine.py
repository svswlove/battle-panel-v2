# -*- coding: utf-8 -*-
"""emotion 插件 · 情绪连续打分口径（纯函数，零 IO、零第三方依赖）。

## 定位

`track_pool.db::sentiment.stage` 是规则 if-else 硬判（冰点/启动/发酵/高潮/分歧/退潮），
只有离散档位、没有强弱幅度。本模块把六个横截面因子合成一个 0–100 连续分，
与 `stage` **并列**展示、互不覆盖：两者背离时才看得见分歧。

## 公式（照《情绪因子总表_20260822》§一B，扩展自 5 因子；基准 50 分，clamp [0,100]）

| # | 因子 | 原始值 | 阈值 | 加减分 |
|---|---|---|---|---|
| 1 | 涨停家数 | `zt_total` | 滚动 60 日 q50 | `clamp((zt − 阈值) × 0.4, −20, +20)` |
| 2 | 晋级率 | `promotion_2b`（比例，非百分数） | 15%；分母样本 <10 只不给分 | `clamp((率 − 0.15) × 100, −15, +15)` |
| 3 | 炸板率 | `(zt_ever − zt_total) / zt_ever` | 20% | `clamp(20 − 率 × 100, −20, +20)`（炸板率越低越加分） |
| 4 | 跌停 vs 涨停 | `dt_total` 对 `zt_total` | — | 跌停 > 涨停 −10；涨停 ≥ 跌停×1.5 +10；其余 0 |
| 5 | 最高板 | `height` | — | ≤2 板 −10；≥5 板 +10；其余 0 |
| 6 | 连板数 | `lb2plus` | 滚动 60 日分位 | `clamp((分位 − 0.5) × 20, −10, +10)` |

**阈值为什么用滚动分位而不是固定 68/100**：68 是 2026-08 的市场状态，2026-10-29 涨停只有 39 家，
固定阈值会连续几个月判冰点、失去区分度。滚动窗口跟市场自身的中枢走（样本不足 60 日用现有全部）。

**窗口含当日**（`rolling_median` 取「最近 60 个收盘样本含今天」）：这是 `rolling(60).median()` 的
标准语义，且样本 ≤60 日时窗口即全样本，不含当日会让前 59 天恒等失真。

## 因子 6（连板数）为什么用分位、不用绝对阈值

任务书没给参考值（本地无总表），要求自定并写明理由。选分位有三条依据：

1. **连板家数没有绝对中枢**：它同时受全市场涨停家数（39 家 vs 141 家）和连板生态（高度断层）影响，
   20 家在亢奋期偏低、在冰点期极高，任何固定数都会随季节漂移——与固定阈值 68 的失败原因同源。
2. **与因子 5 分工**：最高板是阶跃信号（离散高度），连板数用连续分位补幅度，两者不重复。
3. **线性映射而非阶跃**：`(分位−0.5)×20` 在分位 0/1 处正好 ±10、在中位处 0，连续无跳变；
   阶跃版（如 ≥0.8 才 +10）会让总分在阈值附近抖动 10 分，掩盖真实变化。

分位口径：`(窗口内严格小于今日的样本数 + 0.5 × 等于今日的样本数) / 样本数`，并列按半分计，
样本 <5 个不参与（返回 None）。

## 因子 3 的方向（2026-10-29 主人修正）

原任务书写的 `炸板率 × 100 − 20` 是从总表转写时抄错了符号：炸板率越高加分越多，与「炸板多 =
封板失败多 = 情绪弱」相反（2026-10-29 炸板率 39.06% 会给 +19.06，把退潮日推到「偏热」）。
主人 2026-10-29 确认为抄写错误，正式口径改为 **`20 − 炸板率 × 100`**（炸板率越低分越高），
今日 39.06% → −19.06。方向收在 `BURST_SIGN` 一个常量上，不再提供反向试算。

## 因子 2 的小样本保护（2026-10-29 新增）

晋级率的分母是「昨日 N 板家数」，样本很少时一个 2/5 = 40% 就能拿满分（+15 封顶），
统计上撑不住。因此：**分母样本 < 10 只 → 该项不给分（`participated=False`、`points=0`，
标注「样本不足」）**，而不是按 40% 折算。库内原值（旧妙想 MCP 口径）没有分母样本数，
此时优先用本地涨停池同日算出的分母；两处都没有样本数才按原口径参与（并在 note 里说明）。
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: 基准分
BASE_SCORE = 50.0
#: 滚动窗口长度（交易日）
WINDOW = 60
#: 晋级率因子的中性基准（比例口径：0.15 = 15%）
PROMOTION_BASE = 0.15
#: 晋级率因子的最小分母样本（昨日 N 板家数 < 10 只 → 不给分，标注「样本不足」）
#:
#: 2026-10-29 主人定案：**保持 10 只，不放宽**。本地「昨日 2 板家数」常态只有 3–17 只，
#: 58 天里只有 8 天够门槛，这不是缺陷而是事实——小样本的晋级率没有统计意义，宁可缺项也不要
#: 拿 2/5 = 40% 去凑满分。缺项记 0 分（`participated=False`），既不加也不减，不压低总分。
MIN_PROMOTION_SAMPLE = 10
#: 炸板率因子的中性基准（比例口径：0.20 = 20%）
BURST_BASE = 0.20
#: 因子 3 方向：−1 = `20 − 炸板率×100`（炸板率越低越加分，2026-10-29 主人修正后的正式口径）
#: +1 是任务书原式（`炸板率×100 − 20`），已确认是总表转写时抄错符号，不再使用
BURST_SIGN = -1

#: 口径版本与修订记录（页面必须显示，避免「同一张图两种算法」）
CALIBER_VERSION = "v1.1"
CALIBER_REVISED = "2026-10-29"
CALIBER_REVISION_NOTE = "修正炸板率符号为 20 − 炸板率×100；晋级率加小样本保护（分母 <10 只不给分）"

#: 档位标签（照总表）：(下界, 标签)，从高到低匹配
BANDS = ((70.0, "亢奋"), (55.0, "偏热"), (40.0, "中性"), (25.0, "偏冷"), (0.0, "冰点"))

#: 六个因子的键与中文名（顺序 = 页面展示顺序）
FACTOR_KEYS = ("zt_total", "promotion", "burst", "dt_vs_zt", "height", "lb2plus")
FACTOR_LABELS = {
    "zt_total": "涨停家数",
    "promotion": "晋级率",
    "burst": "炸板率",
    "dt_vs_zt": "跌停 vs 涨停",
    "height": "最高板",
    "lb2plus": "连板数",
}


def band_of(score: Optional[float]) -> Optional[str]:
    """分数 → 档位标签。None 原样返回（数据缺失不猜档位）。"""
    if score is None:
        return None
    for lower, label in BANDS:
        if score >= lower:
            return label
    return BANDS[-1][1]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _f(value: Any) -> Optional[float]:
    """数值化：None/空串/NaN → None（sentiment 表大量字段是 NULL，不当 0 用）。"""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


def rolling_median(values: Sequence[Optional[float]], window: int = WINDOW) -> List[Optional[float]]:
    """逐点滚动中位数（窗口含当日，样本不足 window 用现有全部）。

    返回列表与输入等长；某点窗口内一个有效值都没有 → None。
    """
    out: List[Optional[float]] = []
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - window + 1): i + 1] if v is not None]
        out.append(statistics.median(chunk) if chunk else None)
    return out


def pct_rank(window_values: Iterable[Optional[float]], value: Optional[float]) -> Optional[float]:
    """value 在窗口内的分位（0–1，并列按半分）。样本 <5 或 value 缺失 → None。"""
    if value is None:
        return None
    sample = [v for v in window_values if v is not None]
    if len(sample) < 5:
        return None
    below = sum(1 for v in sample if v < value)
    equal = sum(1 for v in sample if v == value)
    return (below + 0.5 * equal) / len(sample)


def rolling_percentile(values: Sequence[Optional[float]], window: int = WINDOW) -> List[Optional[float]]:
    """逐点分位（窗口含当日），供历史序列与当日共用同一口径。"""
    out: List[Optional[float]] = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1): i + 1]
        out.append(pct_rank(chunk, values[i]))
    return out


def _factor(key: str, raw: Any, threshold: Any, points: float, participated: bool,
            note: str = "") -> Dict[str, Any]:
    return {
        "key": key,
        "label": FACTOR_LABELS[key],
        "raw": raw,                 # 原始值（数值或 None）
        "threshold": threshold,     # 阈值 / 基准（展示用；无阈值因子为 None）
        "points": round(points, 2),
        "participated": bool(participated),
        "note": note,
    }


def score_factors(
    row: Dict[str, Any],
    *,
    zt_threshold: Optional[float],
    promotion_rate: Optional[float],
    lb_percentile: Optional[float],
    promotion_base: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """六因子逐项打分。row 用 sentiment 表字段名（zt_total/zt_ever/dt_total/height/lb2plus）。

    任一因子缺原始值 → `participated=False`、`points=0`（不参与合成，也不补 0 值假装中性）。
    `promotion_base` = 晋级率分母样本（昨日 N 板家数）；< MIN_PROMOTION_SAMPLE 时不给分。
    """
    zt = _f(row.get("zt_total"))
    ever = _f(row.get("zt_ever"))
    dt = _f(row.get("dt_total"))
    height = _f(row.get("height"))
    lb2 = _f(row.get("lb2plus"))
    rate = _f(promotion_rate)
    out: List[Dict[str, Any]] = []

    # 1 涨停家数：与滚动中枢比高低
    if zt is None or zt_threshold is None:
        out.append(_factor("zt_total", zt, zt_threshold, 0.0, False, "缺 zt_total 或阈值样本"))
    else:
        pts = _clamp((zt - zt_threshold) * 0.4, -20.0, 20.0)
        out.append(_factor("zt_total", zt, round(zt_threshold, 2), pts, True,
                           "滚动 60 日 q50 为中枢，每高 1 家 +0.4 分"))

    # 2 晋级率：比例口径（0.15 = 15%），算不出就不参与；分母样本 <10 只不给分
    base_n = None if promotion_base is None else int(promotion_base)
    if rate is None:
        out.append(_factor("promotion", None, PROMOTION_BASE, 0.0, False, "该因子不可用（无晋级率样本）"))
    elif base_n is not None and base_n < MIN_PROMOTION_SAMPLE:
        out.append(_factor("promotion", round(rate, 4), PROMOTION_BASE, 0.0, False,
                           "样本不足：昨日 2 板仅 %d 只（<%d），不给分" % (base_n, MIN_PROMOTION_SAMPLE)))
    else:
        pts = _clamp((rate - PROMOTION_BASE) * 100.0, -15.0, 15.0)
        note = "2 板晋级率（昨日 2 板 → 今日 3 板）"
        if base_n is None:
            note += "·库内原值，无样本数"
        else:
            note += "·分母 %d 只" % base_n
        out.append(_factor("promotion", round(rate, 4), PROMOTION_BASE, pts, True, note))

    # 3 炸板率：(曾涨停 − 涨停) / 曾涨停
    if ever is None or zt is None or ever <= 0:
        out.append(_factor("burst", None, BURST_BASE, 0.0, False, "缺 zt_ever 或曾涨停为 0"))
    else:
        burst = (ever - zt) / ever
        pts = _clamp(BURST_SIGN * (burst * 100.0 - 20.0), -20.0, 20.0)
        out.append(_factor("burst", round(burst, 4), BURST_BASE, pts, True,
                           "曾涨停 %g 家、涨停 %g 家" % (ever, zt)))

    # 4 跌停 vs 涨停：方向性二元信号
    if dt is None or zt is None:
        out.append(_factor("dt_vs_zt", None, None, 0.0, False, "缺 dt_total 或 zt_total"))
    else:
        if dt > zt:
            pts, note = -10.0, "跌停 %g > 涨停 %g" % (dt, zt)
        elif zt >= dt * 1.5:
            pts, note = 10.0, "涨停 %g ≥ 跌停 %g × 1.5" % (zt, dt)
        else:
            pts, note = 0.0, "跌停 %g 对涨停 %g，无方向" % (dt, zt)
        out.append(_factor("dt_vs_zt", {"dt": dt, "zt": zt}, None, pts, True, note))

    # 5 最高板：阶跃
    if height is None:
        out.append(_factor("height", None, None, 0.0, False, "缺 height"))
    else:
        pts = -10.0 if height <= 2 else (10.0 if height >= 5 else 0.0)
        out.append(_factor("height", height, None, pts, True, "≤2 板 −10；≥5 板 +10"))

    # 6 连板数：滚动窗口分位线性映射（理由见模块头 §因子 6）
    if lb2 is None or lb_percentile is None:
        out.append(_factor("lb2plus", lb2, lb_percentile, 0.0, False, "缺 lb2plus 或分位样本 <5"))
    else:
        pts = _clamp((lb_percentile - 0.5) * 20.0, -10.0, 10.0)
        out.append(_factor("lb2plus", lb2, round(lb_percentile, 4), pts, True,
                           "滚动 60 日分位 %.0f%%（横向比，无绝对阈值）" % (lb_percentile * 100)))
    return out


def score_series(rows: Sequence[Dict[str, Any]], promotions: Dict[str, Any]) -> List[Dict[str, Any]]:
    """整段序列打分。rows 必须按 trade_date 升序（滚动窗口依赖顺序）。

    promotions: {trade_date(YYYYMMDD): 2 板晋级率样本}。值可以是：
      · float —— 只有晋级率比例（没有分母样本数，不做小样本保护）；
      · dict —— `{"rate": float|None, "base": int|None}`，`base` = 昨日 2 板家数。
    两种形式都接受，由 source.py 合并「库内原值 + 梯队推算」后传入。
    """
    zt_series = [_f(r.get("zt_total")) for r in rows]
    lb_series = [_f(r.get("lb2plus")) for r in rows]
    thresholds = rolling_median(zt_series)
    lb_pcts = rolling_percentile(lb_series)

    out: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        date = str(row.get("trade_date") or "")
        sample = promotions.get(date)
        if isinstance(sample, dict):
            rate = _f(sample.get("rate"))
            base_n = sample.get("base")
        else:
            rate, base_n = _f(sample), None
        factors = score_factors(
            row,
            zt_threshold=thresholds[i],
            promotion_rate=rate,
            lb_percentile=lb_pcts[i],
            promotion_base=base_n,
        )
        total = BASE_SCORE + sum(f["points"] for f in factors if f["participated"])
        total = _clamp(total, 0.0, 100.0)
        out.append({
            "date": date,
            "score": round(total, 2),
            "band": band_of(total),
            "n_factors": sum(1 for f in factors if f["participated"]),
            "factors": factors,
            "zt_threshold": None if thresholds[i] is None else round(thresholds[i], 2),
            "sample_n": len([v for v in zt_series[max(0, i - WINDOW + 1): i + 1] if v is not None]),
        })
    return out
