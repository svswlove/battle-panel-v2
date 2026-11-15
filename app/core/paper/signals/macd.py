# -*- coding: utf-8 -*-
"""engine/signals/macd.py · MACD 共振增强层（工单 A2 · 主人 2026-09-05 拍板）

量价均线选位置（VWAP_RECLAIM 等五形态），MACD 验动能——本模块是挂在 VwapReclaimSignal
上的可选增强层（params.macd_resonance，A2-1，默认开启），给量价信号加"动能真伪"过滤：
不替代量价判定、不是独立买入信号（registry 不新增信号类型，接入层另派）。

已定口径（主人拍板，不可改）：
- 动能/背离主判看 DIF 线（非 MACD 柱：柱是 DIF 一阶变化的噪声二次放大，9/4 做T实证）
- 级别分工：15 分钟做动能背景（A2-5），1 分钟做触发级禁区判定（A2-2/A2-3）——
  1 分钟 MACD 直接当买卖点用噪声大（做T实证 n=940 假信号），故 1m 层只用
  "水下禁区"这类宽域状态做否决（抗噪），不用 1m 金叉事件做买入主判；
  金叉事件仅产出 meta 强买点标注（macd_strong）
- 参数 (12,26,9) 绝不改（ZG 知识库红线，本模块常量锁定，不暴露可配入口）
- MACD 是增强确认不是主判；数据不足一律降级放行（fail-open），禁区判定 fail-closed

规则（工单 A2-1~A2-5）：
A2-1  macd_resonance 开关，默认 True，挂 VwapReclaimSignal.params
A2-2  金叉确认：最近金叉发生在 0 轴上/附近（DIF ≥ -零轴带）→ 强买点（macd_strong，
      仅标注不否决）；水下（DIF<0 且 DEA<0）状态下的金叉 = 0 轴下金叉只算反弹 → 不追
A2-3  死叉禁区（fail-closed）：DIF<0 且 DEA<0 且 DIF<DEA（死叉水下）→ 禁买。
      与 A2-2 合成"水下禁区"：DIF<0 且 DEA<0 全域禁买（金叉水下给 A2-2 理由、
      死叉水下给 A2-3 理由），等 DIF 回 0 轴上禁区才解除
A2-4  底背离增强：回踩低点不破前低（容差 0.2%）+ DIF 不创新低 → 高置信买点
      （macd_div_bonus 仅加分；不豁免水下禁区、不擅自放松拍板阈值——
      门槛放松由接入层读 flag 决定）
A2-5  动能背景：15 分钟 MACD 死叉且水下 → 当日该票不做（信号层表达为否决，
      meta.background.source/level 留痕，接入层可升级为整票当日降级）。
      15m 序列来源：调用方显式注入 bars_15m，否则由 1m bars 按 A 股时段 15 分钟桶合成；
      不足 min_bars_15m（默认 35=slow+signal）→ 降级放行并留痕

工程口径（诚实标注）：
- 1m 触发级 warmup 下限 min_bars_1m=40（对齐 MacdDivergenceSignal 的 40 根下限）；
  EMA 收敛精度随序列长度提高，40 为可用下限非最优
- 15m 合成含末桶未满 15 分钟的部分（桶内全是已闭合 1m，无未来函数）；默认当日 240 根
  1m 只合成 16 桶 < 35 → 背景层常态降级，接入层喂 ≥3 日 1m 或显式 15m 才激活
- 零轴带 zero_band_pct 默认 0.3%（×现价），仅影响强买点标注宽严
- EMA/MACD 口径与 library.macd_lines 一致（标准 α=2/(n+1)）；本模块独立实现、
  零依赖（不反向 import library，避免 import 环）
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---- MACD 参数（ZG 知识库红线：12/26/9 绝不改，主人 2026-09-05 拍板重申） ----
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# warmup 下限（工程口径）：1m 触发级对齐 MacdDivergenceSignal 的 40 根下限；
# 15m 背景级取 slow+signal=35（A 股一日 16 桶，约 2.2 日）
MIN_BARS_1M = 40
MIN_BARS_15M = 35

# 强买点金叉近端窗口（工程口径）：金叉早于该根数视为既成趋势，不再标注"强买点"
STRONG_RECENT_BARS = 60

# A2-4 底背离默认窗口与价格容差（回踩"不破前低"允许 0.2% 内的毛刺）
DIV_WINDOW = 60
DIV_PRICE_TOL = 0.002


def _ema(series: List[float], n: int) -> List[float]:
    """指数移动平均（标准 α=2/(n+1)，与 library.ema 同口径）。"""
    out: List[float] = []
    k = 2.0 / (n + 1)
    prev = None
    for v in series:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd_from_closes(closes: List[float]) -> Tuple[List[float], List[float], List[float]]:
    """收盘价序列 → (DIF, DEA, 柱)。DIF=EMA12−EMA26；DEA=EMA(DIF,9)；柱=DIF−DEA。
    参数锁定 (12,26,9)（ZG 知识库红线），本函数不提供可配入口。"""
    ef = _ema(closes, MACD_FAST)
    es = _ema(closes, MACD_SLOW)
    dif = [a - b for a, b in zip(ef, es)]
    dea = _ema(dif, MACD_SIGNAL)
    hist = [a - b for a, b in zip(dif, dea)]
    return dif, dea, hist


def macd_from_bars(bars: Sequence[Any]) -> Dict[str, List[float]]:
    """bars 序列（Candle / 鸭子类型，取 .close）→ {"dif", "dea", "hist"} 序列（工单 A2 要点）。"""
    dif, dea, hist = macd_from_closes([float(b.close) for b in bars])
    return {"dif": dif, "dea": dea, "hist": hist}


@dataclass
class ResampledBar:
    """15 分钟合成桶（resample_15m 产物；字段与 Candle 同形，鸭子兼容）。"""
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0


def resample_15m(bars: Sequence[Any]) -> List[ResampledBar]:
    """1m bars → 15m 桶序列（A2-5 背景级合成，A 股时段按 (日期, 15 分钟桶) 聚合）。

    open=桶内首根 open、high/low=极值、close=桶内末根 close、volume/amount=求和、
    ts=桶内末根 ts。末桶未满 15 分钟也计入（桶内全是已闭合 1m，无未来函数）。
    ts 解析失败的 bar 跳过（防御，不抛）。"""
    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for b in bars:
        ts = str(b.ts)
        try:
            hh = int(ts[11:13])
            mm = int(ts[14:16])
        except (ValueError, IndexError):
            continue  # ts 口径异常跳过（Candle 契约保证 'YYYY-MM-DD HH:MM:SS'，防御非契约输入）
        key = "{} {:02d}:{:02d}".format(ts[:10], hh, (mm // 15) * 15)
        vol = float(getattr(b, "volume", 0.0) or 0.0)
        amt = float(getattr(b, "amount", 0.0) or 0.0)
        if key not in buckets:
            buckets[key] = {"ts": ts, "open": float(b.open), "high": float(b.high),
                            "low": float(b.low), "close": float(b.close),
                            "volume": vol, "amount": amt}
            order.append(key)
            continue
        agg = buckets[key]
        agg["high"] = max(agg["high"], float(b.high))
        agg["low"] = min(agg["low"], float(b.low))
        agg["close"] = float(b.close)
        agg["volume"] += vol
        agg["amount"] += amt
        agg["ts"] = ts
    return [ResampledBar(**buckets[k]) for k in order]


def _cross_events(dif: List[float], dea: List[float]) -> List[Dict[str, Any]]:
    """DIF/DEA 交叉事件：golden=上穿（前 ≤0 后 >0）、dead=下穿（前 ≥0 后 <0）。
    事件含 idx/kind/dif（交叉当根 DIF 值，0 轴上/下归类用）。"""
    events: List[Dict[str, Any]] = []
    for i in range(1, len(dif)):
        prev, cur = dif[i - 1] - dea[i - 1], dif[i] - dea[i]
        if prev <= 0 < cur:
            events.append({"idx": i, "kind": "golden", "dif": dif[i]})
        elif prev >= 0 > cur:
            events.append({"idx": i, "kind": "dead", "dif": dif[i]})
    return events


def trigger_verdict(bars: Sequence[Any], min_bars: int = MIN_BARS_1M,
                    zero_band_pct: float = 0.3,
                    strong_recent_bars: int = STRONG_RECENT_BARS,
                    macd: Optional[Tuple[List[float], List[float], List[float]]] = None,
                    ) -> Dict[str, Any]:
    """1m 触发级判定（A2-2/A2-3，主人 2026-09-05 拍板）。

    水下禁区（DIF<0 且 DEA<0）全域禁买：死叉水下 → A2-3 理由；金叉水下 → A2-2 理由
    （0 轴下金叉只算反弹，管住"刚下杀完就进"）。DIF 回 0 轴上禁区解除。
    放行域内最近金叉在 0 轴上/附近（DIF ≥ -零轴带，近端 strong_recent_bars 根内）
    → strong=True（强买点标注，仅 meta）。数据不足（<min_bars）降级放行（fail-open）。

    返回 {"veto", "degraded", "reason", "strong", "meta"}。
    """
    n = len(bars)
    if n < max(int(min_bars), 2):
        return {"veto": False, "degraded": True, "reason": None, "strong": False,
                "meta": {"level": "1m", "n_bars": n, "min_bars": int(min_bars),
                         "reason": "MACD 数据不足"}}
    if macd is None:
        macd = macd_from_closes([float(b.close) for b in bars])
    dif, dea, hist = macd
    dif_now, dea_now, hist_now = dif[-1], dea[-1], hist[-1]
    underwater = dif_now < 0 and dea_now < 0
    veto, reason = False, None
    if underwater:
        veto = True
        if dif_now < dea_now:
            reason = ("A2-3 死叉水下（DIF<0 且 DEA<0 且 DIF<DEA），动能仍空头 → 禁买"
                      "（主人 2026-09-05 拍板）")
        else:
            reason = ("A2-2 0 轴下金叉只算反弹（DIF<0 且 DEA<0），刚下杀完不追 → 禁买"
                      "（主人 2026-09-05 拍板）")
    events = _cross_events(dif, dea)
    last = events[-1] if events else None
    band = abs(float(bars[-1].close)) * float(zero_band_pct) / 100.0
    strong = bool((not veto) and last and last["kind"] == "golden"
                  and last["dif"] >= -band
                  and (n - 1 - last["idx"]) <= int(strong_recent_bars))
    meta = {"level": "1m", "n_bars": n, "min_bars": int(min_bars),
            "dif": round(dif_now, 4), "dea": round(dea_now, 4), "hist": round(hist_now, 4),
            "underwater": underwater, "zero_band": round(band, 4),
            "last_cross": ({"kind": last["kind"], "ts": bars[last["idx"]].ts,
                            "dif": round(last["dif"], 4), "bars_ago": n - 1 - last["idx"]}
                           if last else None)}
    return {"veto": veto, "degraded": False, "reason": reason, "strong": strong, "meta": meta}


def divergence_bonus(bars: Sequence[Any], dif: Optional[List[float]],
                     window: int = DIV_WINDOW, price_tol: float = DIV_PRICE_TOL,
                     ) -> Dict[str, Any]:
    """A2-4 底背离增强（主人 2026-09-05 拍板）：回踩不破前低 + DIF 不创新低 → 高置信买点。

    回踩低点 = 最近 window 根内 low 的局部低点（左右邻居不低于，相邻/平台合并取更低者），
    取最近两个比较：后低 ≥ 前低×(1-容差)（回踩不破前低）且 DIF 抬高（不创新低）。
    只加分不否决：不豁免水下禁区（A2-2/A2-3），不放松拍板阈值（接入层按 flag 决定）。
    返回 {"bonus", "meta"}。"""
    n = len(bars)
    meta: Dict[str, Any] = {"window": int(window), "price_tol": float(price_tol), "lows": []}
    if dif is None or len(dif) < n or n < 5:
        meta["reason"] = "MACD 数据不足，无法判底背离"
        return {"bonus": False, "meta": meta}
    start = max(1, n - int(window))
    lows_idx = [i for i in range(start, n - 1)
                if float(bars[i].low) <= float(bars[i - 1].low)
                and float(bars[i].low) <= float(bars[i + 1].low)]
    merged: List[int] = []  # 相邻（索引差 ≤1）低点合并为同一脚，取更低者
    for i in lows_idx:
        if merged and i - merged[-1] <= 1:
            if float(bars[i].low) < float(bars[merged[-1]].low):
                merged[-1] = i
        else:
            merged.append(i)
    meta["lows"] = [{"ts": bars[i].ts, "low": round(float(bars[i].low), 3),
                     "dif": round(dif[i], 4)} for i in merged[-2:]]
    if len(merged) < 2:
        meta["reason"] = "回踩低点 {} 个 < 2，无法比较".format(len(merged))
        return {"bonus": False, "meta": meta}
    i1, i2 = merged[-2], merged[-1]
    price_hold = float(bars[i2].low) >= float(bars[i1].low) * (1 - float(price_tol))
    dif_higher = dif[i2] > dif[i1]
    meta.update({"price_hold": price_hold, "dif_higher": dif_higher,
                 "prev_low": round(float(bars[i1].low), 3),
                 "last_low": round(float(bars[i2].low), 3),
                 "prev_dif": round(dif[i1], 4), "last_dif": round(dif[i2], 4)})
    bonus = bool(price_hold and dif_higher)
    if not bonus:
        meta["reason"] = "回踩破前低，底背离不成立" if not price_hold else "DIF 创新低，底背离不成立"
    return {"bonus": bonus, "meta": meta}


def background_15m(bars_15m: Sequence[Any], min_bars: int = MIN_BARS_15M) -> Dict[str, Any]:
    """A2-5 动能背景（主人 2026-09-05 拍板）：15 分钟 MACD 死叉且水下（DIF<DEA 且
    DIF<0 且 DEA<0）→ 当日该票不做（信号层=veto，接入层可升级为整票当日降级）。

    背景层只否决"死叉水下"状态（工单原文口径）；水下金叉（15m 反弹初期）不在背景
    否决域——触发时刻由 1m 层水下禁区兜底。数据不足（<min_bars）降级放行。
    返回 {"veto", "degraded", "reason", "meta"}。"""
    n = len(bars_15m)
    meta: Dict[str, Any] = {"level": "15m", "n_bars": n, "min_bars": int(min_bars)}
    if n < max(int(min_bars), 2):
        meta["reason"] = "15m MACD 数据不足"
        return {"veto": False, "degraded": True, "reason": None, "meta": meta}
    dif, dea, _hist = macd_from_closes([float(b.close) for b in bars_15m])
    dif_now, dea_now = dif[-1], dea[-1]
    dead_underwater = dif_now < dea_now and dif_now < 0 and dea_now < 0
    meta.update({"dif": round(dif_now, 4), "dea": round(dea_now, 4),
                 "dead_underwater": dead_underwater})
    if dead_underwater:
        return {"veto": True, "degraded": False,
                "reason": ("A2-5 15m 动能背景空头（死叉水下：DIF<0 且 DEA<0 且 DIF<DEA）"
                           "→ 当日不做（主人 2026-09-05 拍板）"),
                "meta": meta}
    return {"veto": False, "degraded": False, "reason": None, "meta": meta}


def macd_resonance_check(bars: Sequence[Any], bars_15m: Optional[Sequence[Any]] = None,
                         min_bars_1m: int = MIN_BARS_1M, min_bars_15m: int = MIN_BARS_15M,
                         zero_band_pct: float = 0.3,
                         ) -> Dict[str, Any]:
    """A2 组合判定入口（VwapReclaimSignal 终闸调用；接入层可直接调用做预筛）。

    否决优先级：1m 触发级禁区（A2-2/A2-3）> 15m 背景（A2-5）；数据不足一律放行并
    在 meta.notes 留痕。bars_15m 显式注入优先，否则由 1m bars 合成（A2-5）。

    返回 {"veto", "reason", "degraded", "macd_strong", "macd_div_bonus",
          "meta": {"trigger", "background"(含 source), "divergence", "notes"}}。
    """
    notes: List[str] = []
    n = len(bars)
    macd: Optional[Tuple[List[float], List[float], List[float]]] = None
    if n >= max(int(min_bars_1m), 2):
        dif, dea, hist = macd_from_closes([float(b.close) for b in bars])
        macd = (dif, dea, hist)
    tv = trigger_verdict(bars, min_bars=min_bars_1m, zero_band_pct=zero_band_pct, macd=macd)
    if tv["degraded"]:
        notes.append("MACD 数据不足（1m {} < {} 根，放行）".format(n, int(min_bars_1m)))
    if bars_15m:
        src_bars, source = list(bars_15m), "injected"
    else:
        src_bars, source = resample_15m(bars), "synthesized_1m"
    bg = background_15m(src_bars, min_bars=min_bars_15m)
    if bg["degraded"]:
        notes.append("15m MACD 数据不足（{} < {} 桶，放行）".format(len(src_bars), int(min_bars_15m)))
    dv = divergence_bonus(bars, macd[0] if macd else None)
    return {
        "veto": bool(tv["veto"] or bg["veto"]),
        "reason": tv["reason"] or bg["reason"],
        "degraded": bool(tv["degraded"] or bg["degraded"]),
        "macd_strong": bool(tv.get("strong")),
        "macd_div_bonus": bool(dv["bonus"]),
        "meta": {"trigger": tv["meta"], "background": dict(bg["meta"], source=source),
                 "divergence": dv["meta"], "notes": notes},
    }
