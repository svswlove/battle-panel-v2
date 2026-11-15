# -*- coding: utf-8 -*-
"""common/indicators.py · 黄白线唯一实现（口径唯一源，2026-09-12 K1a 作战面板看盘能力）

【口径（唯一源，主人定）】
  白线 = EMA(EMA(C, 10), 10)
  黄线 = (MA14 + MA28 + MA57 + MA114) / 4

【EMA 约定】种子=序列首值，α=2/(n+1) —— 与 engine/signals/library.ema、
  夏天-activity/工具/four_dim.ema 同口径（存量三处已实证一致，本模块为新增消费端的唯一入口）。

【降级口径（four_dim B1 同源）】黄线在 K 线不足 114 根的位置输出 None（不编代理值）；
  白线自第 1 根起恒有值（EMA 种子=首值）。

【落点说明（施工图 §四 要求写明）】
  - 落 engine/common/：K1a 新端点（server.py /api/kline）与后续 K1b 前端渲染数据装配
    均在 engine 侧，common 是 engine 既有共享层（paths/tsutil 等先例），零新增依赖、
    纯函数可单测；不落 夏天-activity/工具/（那是脚本侧，engine 导入跨层反向依赖）。
  - 存量散落 ≥5 处实现（xuan_gu_cache.py / tier_generator.py / four_dim.py 等）
    本包不收敛（施工图明示另行评估，避免回归面过大）；**新消费端一律 import 本模块，
    禁止自建第二份**（口径卡铁律：口径唯一源）。

纯函数、零 IO、零第三方依赖（标准库实现，供端点服务端算好下发，前端零计算守铁律）。
"""
from typing import List, Optional, Sequence, Tuple

# 黄线四线周期（唯一源，勿在他处复写）
YELLOW_PERIODS = (14, 28, 57, 114)
WHITE_PERIOD = 10  # 白线双重 EMA 周期


def ema(series: Sequence[float], n: int) -> List[float]:
    """指数移动平均（标准 α=2/(n+1)，种子=首值，与 signals/library.ema 同口径）。"""
    if n <= 0:
        raise ValueError(f"EMA 周期须为正整数，收到 n={n!r}")
    out: List[float] = []
    k = 2.0 / (n + 1)
    prev: Optional[float] = None
    for v in series:
        prev = float(v) if prev is None else float(v) * k + prev * (1 - k)
        out.append(prev)
    return out


def ma_series(series: Sequence[float], n: int) -> List[Optional[float]]:
    """简单移动平均序列：前 n-1 项为 None（窗口不足不编造），其后为滚动均值。"""
    if n <= 0:
        raise ValueError(f"MA 周期须为正整数，收到 n={n!r}")
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(series):
        s += float(v)
        if i >= n:
            s -= float(series[i - n])
        out.append(s / n if i >= n - 1 else None)
    return out


def yellow_white_lines(closes: Sequence[float]) -> Tuple[List[Optional[float]], List[float]]:
    """收盘价序列 → (黄线序列, 白线序列)，与输入等长、按下标对齐。

    - 白线[i] = EMA(EMA(C,10),10)[i]（自 i=0 起恒有值）；
    - 黄线[i] = (MA14+MA28+MA57+MA114)/4，任一 MA 窗口不足（i<113）→ None（B1 降级口径）；
    - 空/None 输入 → ([], [])（调用方按缺数据降级，不抛异常）。"""
    if not closes:
        return [], []
    vals = [float(c) for c in closes]
    white_inner = ema(vals, WHITE_PERIOD)
    white = ema(white_inner, WHITE_PERIOD)
    ma_lines = [ma_series(vals, n) for n in YELLOW_PERIODS]
    yellow: List[Optional[float]] = []
    for i in range(len(vals)):
        parts = [line[i] for line in ma_lines]
        if any(p is None for p in parts):
            yellow.append(None)
        else:
            yellow.append(sum(parts) / len(parts))
    return yellow, white
