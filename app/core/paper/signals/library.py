# -*- coding: utf-8 -*-
"""signals/library.py · 信号模板库（M5 执行引擎 dry-run）

每个信号 = 独立类（判定函数 + 参数），输入分钟K（list[Candle] 或 pandas DataFrame），
输出 SignalResult(triggered, meta) —— triggered 为触发布尔，meta 为判定详情（§16.2 判断留痕素材）。

信号族（规格映射）：
- BreakoutSignal        放量突破（§11.1 突破颈线 / §13.2 启动突破期候选）
- PullbackSignal        回踩不破（§11.4 突破→回踩→不破三段确认；第二笔加仓标准形态）
- VwapReclaimSignal     收复均价线（§11.3 判强因子1：放量上穿均价线，二次上穿更佳）
- MacdDivergenceSignal  底背离待机（§11.1：两次底背离才稳妥；底背离≠买入，只进 STANDBY）
- WeakToStrongSignal    弱转强（第五模式：昨日弱势 → 竞价高开超预期 → 首根放量 → 站稳均价线）
- StopSignal            止损触发（止损锚点经 StopAnchorPicker 择优，细则模板 §三）
- signals/macd.py       MACD 共振增强层（工单 A2，主人 2026-09-05 拍板：水下禁区 fail-closed、
                        底背离加分、15m 动能背景；挂 VwapReclaimSignal 终闸）

工程口径说明（诚实标注）：
- 黄线精确口径 = (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4（细则 §5.1）；
- 黄柱 = (ΔKDJ.J + ΔRSI3)/2 × 影线系数（缺源码口径，影线系数暂按 1.0 简化，meta 标注 simplified）；
- 动能K止损候选 = 最近两根动能K最低价 ×0.999（细则 §5.3）。
全部指标仅依赖 OHLCV，无外部数据源（细则 §5.4）。
"""
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# A2 MACD 共振增强层（主人 2026-09-05 拍板，工单 A2）：macd.py 零依赖不反向 import 本模块，
# 双路径导入仅为顶层直接运行调试兜底（与 adapters.py 同模式）
try:  # 引擎内导入（正式路径，signals 包上下文）
    from .macd import macd_resonance_check
except ImportError:  # pragma: no cover - 顶层直接运行调试兜底
    from signals.macd import macd_resonance_check

# ---------------- 基础结构 ----------------

@dataclass
class Candle:
    """单根K线。ts 为 'YYYY-MM-DD HH:MM:SS'（本地）；open/high/low/close 元；volume 股/手一致口径由调用方保证。"""
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0


@dataclass
class SignalResult:
    """信号判定结果。triggered=True 表示该信号在最后一根K线触发。"""
    triggered: bool
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self):
        return self.triggered


class Signal:
    """信号基类：name + params + evaluate(candles)。子类实现 _judge。"""

    name = "BASE"
    params: Dict[str, Any] = {}

    def __init__(self, **params):
        merged = dict(self.params)
        merged.update({k: v for k, v in params.items() if v is not None})
        self.params = merged

    def evaluate(self, candles) -> SignalResult:
        """输入分钟K（list[Candle] / DataFrame），输出触发布尔 + meta。"""
        bars = candles if isinstance(candles, list) else candles_from_dataframe(candles)
        if len(bars) < 2:
            return SignalResult(False, {"reason": "K线不足"})
        return self._judge(bars)

    def _judge(self, bars: List[Candle]) -> SignalResult:  # pragma: no cover - 基类占位
        raise NotImplementedError


def candles_from_dataframe(df) -> List[Candle]:
    """pandas DataFrame（qmt_cache parquet / 列式 dict）→ list[Candle]。
    兼容：索引为 'YYYYMMDDHHMMSS' 字符串 / DatetimeIndex / RangeIndex+time(epoch毫秒) 列。"""
    rows: List[Candle] = []
    for i in range(len(df)):
        row = df.iloc[i]
        ts = _bar_ts(df, i, row)
        rows.append(Candle(
            ts=ts,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
            amount=float(row.get("amount") or 0.0),
        ))
    return rows


def _bar_ts(df, i, row) -> str:
    idx = df.index[i]
    if hasattr(idx, "strftime"):
        return idx.strftime("%Y-%m-%d %H:%M:%S")
    s = str(idx)
    if s.isdigit() and len(s) == 14:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
    if s.isdigit() and len(s) == 13:  # epoch 毫秒
        import datetime
        return datetime.datetime.fromtimestamp(int(s) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    if "time" in df.columns and i < len(df):
        t = df["time"].iloc[i]
        try:
            import datetime
            return datetime.datetime.fromtimestamp(float(t) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    return s


# ---------------- 指标工具（纯 OHLCV，细则 §5.4） ----------------

def ma(series: List[float], n: int) -> List[float]:
    """简单移动平均，前 n-1 项为 NaN（None）。"""
    out: List[Optional[float]] = []
    s = 0.0
    for i, v in enumerate(series):
        s += v
        if i >= n:
            s -= series[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(series: List[float], n: int) -> List[float]:
    """指数移动平均（标准 α=2/(n+1)）。"""
    out: List[float] = []
    k = 2.0 / (n + 1)
    prev = None
    for v in series:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd_lines(closes: List[float], fast=12, slow=26, signal=9) -> Tuple[List[float], List[float], List[float]]:
    """MACD：(DIF, DEA, 柱)。DIF=EMA(fast)−EMA(slow)；DEA=EMA(DIF, signal)；柱=DIF−DEA（东财口径金叉=柱由负转正）。"""
    ef = ema(closes, fast)
    es = ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, signal)
    hist = [a - b for a, b in zip(dif, dea)]
    return dif, dea, hist


def _wilder_rsi(closes: List[float], n=3) -> List[float]:
    """RSI(n)，Wilder 平滑（SMA 通式）。细则 §5.4：RSI3日用 SMA 通式实现。"""
    out: List[float] = []
    gains, losses = [], []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    for i in range(len(closes)):
        if i == 0:
            out.append(50.0)
            continue
        window = gains[max(0, i - n):i]
        wl = losses[max(0, i - n):i]
        if not window:
            out.append(50.0)
            continue
        ag = sum(window) / len(window)
        al = sum(wl) / len(wl)
        out.append(100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al))
    return out


def kdj_lines(closes: List[float], highs: List[float], lows: List[float], n=9) -> Tuple[List[float], List[float], List[float]]:
    """KDJ（默认 9,3,3）。RSV=(C−LLV(L,n))/(HHV(H,n)−LLV(L,n))×100；K=SMA(RSV,3,1)；D=SMA(K,3,1)；J=3K−2D。"""
    k, d, j = [], [], []
    pk = pd_ = 50.0
    for i in range(len(closes)):
        if i >= n - 1:
            hh = max(highs[i - n + 1:i + 1])
            ll = min(lows[i - n + 1:i + 1])
            rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0
        else:
            rsv = 50.0
        pk = (2.0 * pk + rsv) / 3.0
        pd_ = (2.0 * pd_ + pk) / 3.0
        k.append(pk)
        d.append(pd_)
        j.append(3.0 * pk - 2.0 * pd_)
    return k, d, j


def huangxian(closes: List[float]) -> List[float]:
    """大哥黄 = (MA(C,14)+MA(C,28)+MA(C,57)+MA(C,114))/4（细则 §5.1 精确口径）。前 113 项为 None。"""
    m14, m28, m57, m114 = ma(closes, 14), ma(closes, 28), ma(closes, 57), ma(closes, 114)
    out: List[Optional[float]] = []
    for a, b, c, d in zip(m14, m28, m57, m114):
        if None in (a, b, c, d):
            out.append(None)
        else:
            out.append((a + b + c + d) / 4.0)
    return out


def _huangzhu(closes, highs, lows, vol_ratio=1.0) -> List[float]:
    """黄柱工程近似 = (ΔKDJ.J + ΔRSI3)/2 × 影线系数 × 倍量系数加成。
    影线系数缺源码口径暂按 1.0（meta 标注 simplified）；倍量系数加成=min(vol_ratio, 2.0)。"""
    _, _, j = kdj_lines(closes, highs, lows)
    r = _wilder_rsi(closes)
    out: List[float] = []
    for i in range(len(closes)):
        dj = (j[i] - j[i - 1]) if i > 0 else 0.0
        dr = (r[i] - r[i - 1]) if i > 0 else 0.0
        out.append((dj + dr) / 2.0 * min(max(vol_ratio, 0.5), 2.0))
    return out


def vwap_series(candles: List[Candle]) -> List[float]:
    """当日累计 VWAP = Σ(典型价×量)/Σ量（典型价取 close 作近似；分时均价线口径）。"""
    out: List[float] = []
    cum_pv = 0.0
    cum_v = 0.0
    for c in candles:
        cum_pv += c.close * c.volume
        cum_v += c.volume
        out.append(cum_pv / cum_v if cum_v > 0 else c.close)
    return out


# ---------------- 信号实现 ----------------

class BreakoutSignal(Signal):
    """BREAKOUT · 放量突破：最后一根收盘价上穿前 N 根最高价，且成交量 ≥ 均量 × volume_ratio。

    参数：
    - lookback       前高回看窗口（默认 20 根分钟K）
    - volume_ratio   放量倍数（默认 1.5，细则：成交量 ≥ 前 5 日平均量 1.5 倍）
    - strict_close   收盘价上穿（默认 True；False 时 high 触及即算）
    """

    name = "BREAKOUT"
    params = {"lookback": 20, "volume_ratio": 1.5, "strict_close": True}

    def _judge(self, bars: List[Candle]) -> SignalResult:
        lb = int(self.params["lookback"])
        ratio = float(self.params["volume_ratio"])
        strict = bool(self.params["strict_close"])
        if len(bars) < lb + 2:
            return SignalResult(False, {"reason": f"K线不足（需 ≥{lb + 2}）"})
        last = bars[-1]
        window = bars[-lb - 1:-1]
        ref_high = max(b.high for b in window)
        avg_vol = sum(b.volume for b in window) / len(window)
        vol_ratio = (last.volume / avg_vol) if avg_vol > 0 else 0.0
        broke = last.close > ref_high if strict else last.high >= ref_high
        if not broke:
            return SignalResult(False, {"ref_high": ref_high, "close": last.close,
                                        "vol_ratio": round(vol_ratio, 2), "reason": "未上穿前高"})
        if vol_ratio < ratio:
            return SignalResult(False, {"ref_high": ref_high, "close": last.close,
                                        "vol_ratio": round(vol_ratio, 2),
                                        "reason": f"量能不足（{vol_ratio:.2f} < {ratio}）"})
        return SignalResult(True, {
            "signal": self.name, "ts": last.ts, "close": last.close,
            "ref_high": round(ref_high, 3), "break_pct": round((last.close / ref_high - 1) * 100, 3),
            "vol_ratio": round(vol_ratio, 2), "volume": last.volume,
            "reason": f"放量突破前{lb}根高点 {ref_high:.3f}（量比 {vol_ratio:.2f}）",
        })


class PullbackSignal(Signal):
    """PULLBACK · 回踩不破（§11.4 三段确认）：先有突破锚点，随后回踩进伏击区且不破下沿。

    实现：找最近一根「收盘站上前 N 根最高」的突破K（锚点K），结构锚=锚点K之前 N 根的
    最高价（即被突破的颈线/前高）。此后 max_touch_bars 根内回踩触伏击区（默认取结构锚±
    pullback_tolerance 自动生成，也可显式传 zone_low/zone_high），且收盘不破下沿 → 触发。

    参数：
    - anchor_lookback  突破锚点回看（默认 20 根）
    - zone_low / zone_high  伏击区下沿/上沿（§10.1 显式伏击区；缺省自动取结构锚附近）
    - pullback_tolerance  自动伏击区宽度（默认 1%）
    - max_touch_bars   回踩后多少根内确认（默认 10）
    - close_below_fail 收盘跌破下沿即失败（默认 True，假突破第二笔永不触发 §11.4）
    - anchor          回踩锚点口径：structure=突破颈线/前高（默认，向后兼容）；
                      vwap=分时均价线（黄线锚变体，回踩对象=当日 VWAP，zone 随 K 动态取回踩K当时的 VWAP±tolerance）
    - confirm_bars   触及后企稳确认（工单 20260909-1404，主人 2026-09-09 裁决）：触及后需连续 N 根
                     已闭合K收盘 ≥ 伏击区下沿才触发（默认 2，触及≠确认）；0=显式豁免旧行为（触及即触发）
    - anchor_max_age_bars 锚点时效：突破锚K距末根超此根数不触发（默认 60，防隔时锚变盘后开火）；0=不限
    - max_touch_bars 兼作触及时效：触及K距末根 ≤ max_touch_bars 才参与判定（防旧触及开火）
    """

    name = "PULLBACK"
    params = {"anchor_lookback": 20, "zone_low": None, "zone_high": None,
              "pullback_tolerance": 0.01, "max_touch_bars": 10, "close_below_fail": True,
              "anchor": "structure",
              # v3 企稳判定扩展（全部可配，默认关闭向后兼容）
              "break_allowance": 0.0, "recover_bars": 3, "stabilize_bars": 4,
              "touch_count_min": 1, "volume_decay_check": False,
              "macd_div_confirm": False, "anchor_price": None,
              # 企稳确认（工单 20260909-1404，主人 2026-09-09 裁决「一切都要等确认」）：
              # confirm_bars      触及后需连续 N 根已闭合K收盘 ≥ 伏击区下沿才触发（默认 2）；
              #                   0 = 显式豁免「触及即触发」旧行为（白名单项，仅限口头单明确要求时）
              # anchor_max_age_bars 锚点时效：突破锚K距末根超过该根数 → 结构锚过旧不触发
              #                   （防隔时锚在变盘后开火，吉比特 9/8 实证）；0 = 不限
              # 触及时效（复用 max_touch_bars）：触及K距末根 ≤ max_touch_bars 才参与判定，
              #                   防落表后首次评估扫到数小时前旧触及开火（9/8 触及K=10:06 距评估 186 根实证）
              "confirm_bars": 2, "anchor_max_age_bars": 60}

    # 触及容差：回踩 low 略穿下沿（0.3% 以内）仍视为有效触及
    TOUCH_TOL = 0.003
    # 回踩段量窗口：触及K及其前 N-1 根（量能递减比较口径）
    FOOT_VOL_WINDOW = 3

    def _judge(self, bars: List[Candle]) -> SignalResult:
        lb = int(self.params["anchor_lookback"])
        if len(bars) < lb + 3:
            return SignalResult(False, {"reason": "K线不足"})
        # 找最近一根突破K：收盘 > 其前 lb 根最高（两代判定共用）
        anchor_idx = None
        for i in range(len(bars) - 1, lb - 1, -1):
            if bars[i].close > max(b.high for b in bars[i - lb:i]):
                anchor_idx = i
                break
        if anchor_idx is None:
            return SignalResult(False, {"reason": "回看窗口内无突破锚点（三段确认缺第一段）"})
        # 锚点时效（工单 20260909-1404，主人裁决「锚点时效约束」）：锚K距今超窗不触发，
        # 防隔时锚在变盘后开火（吉比特 9/8：锚 10:05 距评估 13:12 共 186 根仍开火实证）
        max_age = int(self.params.get("anchor_max_age_bars") or 0)
        if max_age > 0 and (len(bars) - 1 - anchor_idx) > max_age:
            return SignalResult(False, {
                "reason": f"结构锚过旧（锚 {bars[anchor_idx].ts} 距末根 {len(bars) - 1 - anchor_idx} 根"
                          f" > {max_age} 根，变盘后隔时锚不触发）",
                "anchor_ts": bars[anchor_idx].ts, "anchor_age_bars": len(bars) - 1 - anchor_idx})
        if not self._v3_enabled():
            return self._judge_legacy(bars, anchor_idx)
        return self._judge_v3(bars, anchor_idx)

    def _v3_enabled(self) -> bool:
        """v3 启用判据：任一语义开关非默认即启用。recover_bars/stabilize_bars 是流程参数，不单独启用。"""
        return (float(self.params.get("break_allowance", 0.0)) > 0
                or int(self.params.get("touch_count_min", 1)) > 1
                or bool(self.params.get("volume_decay_check", False))
                or bool(self.params.get("macd_div_confirm", False)))

    def _judge_legacy(self, bars: List[Candle], anchor_idx: int) -> SignalResult:
        """v2 判定 + 企稳确认（工单 20260909-1404，主人 2026-09-09 裁决）：
        ①触及时效——触及K距末根 ≤ max_touch_bars 才参与判定（防落表首评扫到数小时前旧触及开火）
        ②触及后需连续 confirm_bars 根已闭合K收盘站稳伏击区下沿才触发（触及≠确认）；
          确认数据不足 → fail-closed 本轮不触发，等下轮评估；确认段内收盘破下沿 → 假突破 fail
        confirm_bars=0 时回退「触及即触发」旧行为（白名单显式豁免项）。"""
        lb = int(self.params["anchor_lookback"])
        max_touch = int(self.params["max_touch_bars"])
        confirm_bars = int(self.params.get("confirm_bars") or 0)
        anchor_mode = self.params.get("anchor", "structure")
        tol = float(self.params.get("pullback_tolerance", 0.01))
        vwap_vals = vwap_series(bars) if anchor_mode == "vwap" else None
        structure_anchor = max(b.high for b in bars[anchor_idx - lb:anchor_idx])
        zone_low = self.params.get("zone_low")
        zone_high = self.params.get("zone_high")
        if anchor_mode != "vwap" and (zone_low is None or zone_high is None):
            zone_low = structure_anchor * (1 - tol)
            zone_high = structure_anchor * (1 + tol)
        last_idx = len(bars) - 1
        # 触及窗口 = 锚后窗口 ∩ 近端时效窗（触及K距末根 ≤ max_touch_bars）
        scan_start = max(anchor_idx + 1, last_idx + 1 - max_touch)
        for j in range(scan_start, min(len(bars), anchor_idx + 1 + max_touch)):
            b = bars[j]
            if anchor_mode == "vwap":
                v = vwap_vals[j]
                zl, zh = v * (1 - tol), v * (1 + tol)
            else:
                zl, zh = zone_low, zone_high
            touched = b.low <= zh and b.low >= zl
            broke = b.close < zl if self.params["close_below_fail"] else b.low < zl
            if touched and not broke:
                if confirm_bars <= 0:  # 显式豁免：触及即触发（旧行为）
                    return self._legacy_hit(b, zl, zh, structure_anchor,
                                            bars[anchor_idx].ts, anchor_mode, confirm_bars, [])
                if j + confirm_bars > last_idx:
                    # 确认数据不足：fail-closed 不触发（下轮评估K更多再判，主人「等确认」）
                    return SignalResult(False, {
                        "reason": f"{b.ts} 触及伏击区但确认数据不足"
                                  f"（需触及后 {confirm_bars} 根收盘站稳下沿，现有 {last_idx - j} 根）",
                        "touch_ts": b.ts, "touch_low": round(b.low, 3),
                        "zone": [round(zl, 3), round(zh, 3)],
                        "confirm_bars": confirm_bars, "confirm_have": last_idx - j,
                        "pending_confirm": True})
                confirm = bars[j + 1:j + 1 + confirm_bars]
                broken = next((k for k, c in enumerate(confirm, start=j + 1)
                               if c.close < zl), None)
                if broken is not None:
                    return SignalResult(False, {
                        "reason": f"{bars[broken].ts} 触及后确认段收盘破下沿"
                                  f"（{bars[broken].close:.3f} < {zl:.3f}，假突破，第二笔永不触发 §11.4）"})
                return self._legacy_hit(b, zl, zh, structure_anchor, bars[anchor_idx].ts,
                                        anchor_mode, confirm_bars,
                                        [(c.ts, c.close) for c in confirm])
            if broke:
                return SignalResult(False, {"reason": f"{b.ts} 已破伏击区下沿（假突破，第二笔永不触发 §11.4）"})
        return SignalResult(False, {"reason": "回踩窗口内未触伏击区或已破位"})

    def _legacy_hit(self, b, zl, zh, structure_anchor, anchor_ts, anchor_mode,
                    confirm_bars, confirm_closes):
        """legacy 触发结果组装（触及K + 企稳确认明细留痕）。"""
        conf_txt = (f"，触及后 {confirm_bars} 根收盘站稳下沿（企稳确认 ✓）" if confirm_bars
                    else "（触及即触发·显式豁免确认）")
        return SignalResult(True, {
            "signal": self.name, "ts": b.ts, "close": b.close,
            "zone": [round(zl, 3), round(zh, 3)],
            "touch_low": round(b.low, 3),
            "structure_anchor": round(structure_anchor, 3),
            "anchor_ts": anchor_ts, "anchor_mode": anchor_mode,
            "confirm_bars": confirm_bars,
            "confirm_closes": [(ts, round(c, 3)) for ts, c in confirm_closes],
            "reason": f"突破({anchor_ts} 收盘站上前高)后回踩 {b.low:.3f} "
                      f"于 [{zl:.3f},{zh:.3f}] 不破（锚={"分时均价线" if anchor_mode == "vwap" else "突破颈线"}）"
                      f"→ 三段确认{conf_txt}",
        })

    def _judge_v3(self, bars: List[Candle], anchor_idx: int) -> SignalResult:
        """v3 企稳判定：四段式（触及容差0.3%→容忍浅破→收回→企稳）确认，touch_count_min≥2 时 W底反复确认。

        锚点基准：显式 anchor_price > structure/vwap 自动锚。
        失败升级：二脚破一脚低点 → fail reason 明确「支撑证伪」。
        """
        lb = int(self.params["anchor_lookback"])
        max_touch = int(self.params["max_touch_bars"])
        tol = float(self.params.get("pullback_tolerance", 0.01))
        anchor_mode = self.params.get("anchor", "structure")
        vwap_vals = vwap_series(bars) if anchor_mode == "vwap" else None
        structure_anchor = max(b.high for b in bars[anchor_idx - lb:anchor_idx])
        zone_low = self.params.get("zone_low")
        zone_high = self.params.get("zone_high")
        if anchor_mode != "vwap" and (zone_low is None or zone_high is None):
            zone_low = structure_anchor * (1 - tol)
            zone_high = structure_anchor * (1 + tol)
        anchor_price = self.params.get("anchor_price")
        allowance = float(self.params.get("break_allowance", 0.0))
        recover_bars = int(self.params.get("recover_bars", 3))
        stabilize_bars = int(self.params.get("stabilize_bars", 4))
        volume_decay = bool(self.params.get("volume_decay_check", False))
        macd_div = bool(self.params.get("macd_div_confirm", False))
        min_touch = int(self.params.get("touch_count_min", 1))
        # volume_decay/macd_div 语义上要求二次回踩，未显式开 touch_count_min≥2 时提升
        effective_min = max(min_touch, 2 if (volume_decay or macd_div) else 1)

        closes = [b.close for b in bars]
        dif = dea = hist = None
        if macd_div:
            dif, dea, hist = macd_lines(closes)

        touches = []  # 已确认回踩脚：[{idx, ts, low, close, vol, confirm_end}]
        # 第一脚触及时效（工单 20260909-1404）：触及K距末根 ≤ max_touch，防旧触及开火（与 legacy 同口径）
        i = max(anchor_idx + 1, len(bars) - max_touch)
        while i < len(bars) and len(touches) < effective_min:
            foot, fail_reason = self._scan_one_foot(
                bars, i, max_touch, anchor_mode, vwap_vals, zone_low, zone_high,
                tol, structure_anchor, anchor_price, allowance, recover_bars, stabilize_bars)
            if foot is None:
                reason = fail_reason or "窗口内无有效回踩"
                return SignalResult(False, {
                    "reason": f"回踩确认 {len(touches)}/{effective_min} 次，{reason}"})
            touches.append(foot)
            if len(touches) >= 2:
                first, second = touches[-2], touches[-1]
                if second["low"] < first["low"]:
                    return SignalResult(False, {
                        "reason": f"二脚({second['ts']} 低点 {second['low']:.3f})破一脚"
                                  f"({first['ts']} 低点 {first['low']:.3f})，支撑证伪",
                        "first_low": round(first["low"], 3), "second_low": round(second["low"], 3),
                    })
                if volume_decay and not (second["vol"] < first["vol"]):
                    return SignalResult(False, {
                        "reason": f"第二脚回踩段量 {second['vol']:.0f} ≥ 第一脚 {first['vol']:.0f}，抛压未递减",
                        "first_vol": round(first["vol"], 1), "second_vol": round(second["vol"], 1),
                    })
                if macd_div and not (dif[second["idx"]] > dif[first["idx"]]
                                     or (hist[second["idx"]] > hist[first["idx"]] and hist[first["idx"]] < 0)):
                    return SignalResult(False, {
                        "reason": "二次回踩 MACD 底背离未确认（DIF/绿柱未抬高）",
                        "first_dif": round(dif[first["idx"]], 4), "second_dif": round(dif[second["idx"]], 4),
                    })
            i = foot["confirm_end"] + 1

        last_foot = touches[-1]
        last = bars[last_foot["confirm_end"]]
        zone_info = ([round(zone_low, 3), round(zone_high, 3)]
                     if anchor_mode != "vwap" else {"vwap_dynamic": True})
        return SignalResult(True, {
            "signal": self.name, "ts": last.ts, "close": last.close,
            "zone": zone_info,
            "structure_anchor": round(structure_anchor, 3),
            "anchor_ts": bars[anchor_idx].ts, "anchor_mode": anchor_mode,
            "v3": True, "touch_count": len(touches), "touch_count_min": effective_min,
            "touches": [{"ts": t["ts"], "low": round(t["low"], 3), "close": round(t["close"], 3),
                          "vol": round(t["vol"], 1)} for t in touches],
            "break_allowance": allowance, "recover_bars": recover_bars,
            "stabilize_bars": stabilize_bars,
            "volume_decay_check": volume_decay, "macd_div_confirm": macd_div,
            "reason": f"回踩×{len(touches)} 企稳确认（{'W底' if effective_min >= 2 else '单脚'}"
                      f"{'，量能递减✓' if volume_decay else ''}{'，MACD底背离✓' if macd_div else ''}）→ 触发",
        })

    def _scan_one_foot(self, bars, start, max_touch, anchor_mode, vwap_vals,
                       zone_low, zone_high, tol, structure_anchor, anchor_price,
                       allowance, recover_bars, stabilize_bars):
        """单脚四段式：触及(容差0.3%) → 容忍浅破 → 收回 → 企稳。

        返回 (foot, None) 成功；(None, fail_reason) 失败。
        foot = {idx, ts, low, close, vol, confirm_end}
        """
        scope_end = min(len(bars), start + max_touch)
        for j in range(start, scope_end):
            b = bars[j]
            zl, zh, anchor = self._zone_at(j, anchor_mode, vwap_vals, zone_low, zone_high,
                                           tol, structure_anchor, anchor_price)
            if b.low > zh:
                continue  # 未触及伏击区
            touch_ok = b.low >= zl * (1 - self.TOUCH_TOL)
            close_ok = b.close >= anchor * (1 - allowance)
            if not (touch_ok or close_ok):
                return None, f"{b.ts} 收盘深破锚区（{b.close:.3f} < 容忍线 " \
                             f"{anchor * (1 - allowance):.3f}，假突破 §11.4）"
            if b.close < zl and not close_ok:
                return None, f"{b.ts} 收盘深破锚区（{b.close:.3f} < 容忍线 " \
                             f"{anchor * (1 - allowance):.3f}，假突破 §11.4）"
            if b.close < zl:
                # 收盘浅破：recover_bars 内收回锚区上方，再企稳
                recover_idx = self._find_recover(bars, j + 1, recover_bars, zl,
                                                 anchor_mode, vwap_vals, tol)
                if recover_idx is None:
                    return None, f"{b.ts} 浅破后 {recover_bars} 根内未收回锚区上方（支撑未修复）"
                if not self._check_stabilize(bars, recover_idx, stabilize_bars):
                    return None, f"{b.ts} 收回后 {stabilize_bars} 根内未企稳（创新低）"
                foot = {"idx": j, "ts": b.ts, "low": b.low, "close": b.close,
                        "vol": self._foot_vol(bars, j),
                        "confirm_end": recover_idx + stabilize_bars}
            else:
                # 收盘未破：四段式段2/段3 通过，直接企稳确认（收回=触及K本身）
                if not self._check_stabilize(bars, j, stabilize_bars):
                    return None, f"{b.ts} 触及未破但 {stabilize_bars} 根内未企稳（创新低）"
                foot = {"idx": j, "ts": b.ts, "low": b.low, "close": b.close,
                        "vol": self._foot_vol(bars, j),
                        "confirm_end": j + stabilize_bars}
            return foot, None
        return None, "窗口内未触及伏击区"

    def _zone_at(self, idx, anchor_mode, vwap_vals, zone_low, zone_high,
                 tol, structure_anchor, anchor_price):
        """返回 (zl, zh, anchor)。anchor 为浅破容忍基准：显式 anchor_price > 自动锚。"""
        if anchor_mode == "vwap":
            v = vwap_vals[idx]
            return v * (1 - tol), v * (1 + tol), v
        anchor = anchor_price if anchor_price is not None else structure_anchor
        return zone_low, zone_high, anchor

    def _find_recover(self, bars, start, recover_bars, zl_ref, anchor_mode,
                      vwap_vals, tol):
        """浅破后 recover_bars 根内，收盘收回锚区上方（vwap 模式收回目标随 K 动态取当时 VWAP 下沿）。"""
        for k in range(start, min(len(bars), start + recover_bars)):
            zl = vwap_vals[k] * (1 - tol) if anchor_mode == "vwap" else zl_ref
            if bars[k].close >= zl:
                return k
        return None

    def _check_stabilize(self, bars, base_idx, stabilize_bars):
        """企稳确认：base_idx 之后连续 stabilize_bars 根 low ≥ 基准（= bars[base_idx].low，即收回后低点）。"""
        if len(bars) < base_idx + 1 + stabilize_bars:
            return False
        base = bars[base_idx].low
        return all(b.low >= base for b in bars[base_idx + 1:base_idx + 1 + stabilize_bars])

    def _foot_vol(self, bars, idx):
        """回踩段量 = 触及K及其前 FOOT_VOL_WINDOW-1 根的成交量均值（工程口径，量能递减比较用）。"""
        s = max(0, idx - self.FOOT_VOL_WINDOW + 1)
        seg = bars[s:idx + 1]
        return sum(b.volume for b in seg) / len(seg)


class VwapReclaimSignal(Signal):
    """VWAP_RECLAIM · 收复均价线（§11.3 判强因子1）。

    五条质量规则（主人 2026-09-05 拍板，工单 A1，起因 09-04 天通/三安两笔缩量首穿误买）：
    A1-1 放量门槛：volume_ratio 默认 1.5（>1 时生效），站上当根量 ≥ 前 N 根均量×ratio 才算有效上穿；
         10 点前（early_penalty）放量要求 ×1.5（即 2.25），时间从末根 ts 解析
    A1-2 开盘价收复：require_open_reclaim=True 时要求 close > 今日开盘价（当日第一根 bar 的 open；
         多日序列被 lookback 截断、取不到当日首根时降级放行并记录 reason）
    A1-3 首穿不买：cross_count ≥ min_cross_count（默认 2）才触发，且站上均价线需连续
         hold_bars（默认 3）根闭合确认
    A1-4 见 A1-1 内 early_penalty
    A1-5 低点抬高确认：low_higher_count 默认 2——站上均价线后的回落低点序列最近 2 个依次抬高
         （防假收复）

    MACD 共振增强层（主人 2026-09-05 拍板，工单 A2，rules A2-1~A2-5，实现见 signals/macd.py）：
    量价均线选位置、MACD 验动能——挂在全部量价检查之后作终闸（增强确认非主判）：
    A2-1 macd_resonance 开关（默认 True）
    A2-2/A2-3 水下禁区（1m，fail-closed）：DIF<0 且 DEA<0 → 禁买（死叉水下=A2-3、
         0 轴下金叉只算反弹=A2-2），任何量价信号不触发
    A2-4 底背离增强（回踩不破前低 + DIF 不创新低）→ meta.macd_div_bonus 加分标注
         （不豁免禁区、不放松 A1 拍板阈值，门槛放松由接入层按 flag 决定）
    A2-5 动能背景（15m）：15 分钟 MACD 死叉且水下 → 否决（当日不做，接入层可升级整票降级）；
         15m 序列显式传 macd_bars_15m，否则由 1m bars 合成；数据不足降级放行（fail-open）
    否决时 meta.macd 留痕判定详情；放行时 meta.macd_strong（0 轴上/附近金叉强买点）、
    macd_div_bonus、macd（判定详情）留痕。

    volume_ratio<=1 时跳过放量检查（显式传 1.0 可关量检，向后兼容测试/回放）。"""

    name = "VWAP_RECLAIM"
    params = {"hold_bars": 3, "volume_ratio": 1.5, "lookback": 240,
              "low_higher_count": 2, "require_open_reclaim": True,
              "min_cross_count": 2, "early_penalty": True,
              # A2 MACD 共振增强层（主人 2026-09-05 拍板，工单 A2；MACD 参数 (12,26,9)
              # 在 signals/macd.py 常量锁定，绝不可配——ZG 知识库红线）
              "macd_resonance": True, "macd_bars_15m": None,
              "macd_min_bars": 40, "macd_min_bars_15m": 35, "macd_zero_band_pct": 0.3}

    def _judge(self, bars: List[Candle]) -> SignalResult:
        hold = int(self.params["hold_bars"])
        ratio = float(self.params["volume_ratio"])
        lb = int(self.params["lookback"])
        low_higher = int(self.params.get("low_higher_count", 2))
        min_cross = int(self.params.get("min_cross_count", 2))
        need_open = bool(self.params.get("require_open_reclaim", True))
        early = bool(self.params.get("early_penalty", True))
        seg = bars[-lb:]
        vwap = vwap_series(seg)
        if len(seg) < 2:
            return SignalResult(False, {"reason": "K线不足"})
        crosses = 0
        for i in range(1, len(seg)):
            if seg[i - 1].close <= vwap[i - 1] and seg[i].close > vwap[i]:
                crosses += 1
        avg_vol = sum(b.volume for b in seg[:-1]) / max(len(seg) - 1, 1)
        last = seg[-1]
        above = all(seg[-(k + 1)].close > vwap[-(k + 1)] for k in range(min(hold, len(seg))))
        vol_ratio = (last.volume / avg_vol) if avg_vol > 0 else 0.0
        if not above:
            return SignalResult(False, {"vwap": round(vwap[-1], 3), "close": last.close,
                                        "cross_count": crosses, "reason": "未站上均价线"})
        # A1-3 首穿不买：下杀后第一次上穿不买，二次上穿才确认（主人 2026-09-05 拍板）
        if crosses < min_cross:
            return SignalResult(False, {"vwap": round(vwap[-1], 3), "close": last.close,
                                        "cross_count": crosses, "min_cross_count": min_cross,
                                        "reason": f"上穿{crosses}次 < {min_cross}次（首穿不买）"})
        # A1-1+A1-4 放量门槛（修复原 `ratio>1 恒不触发` 死代码）：ratio>1 时生效；
        # 10 点前放量要求 ×1.5（early_penalty，时间从末根 ts 解析）（主人 2026-09-05 拍板）
        eff_ratio = ratio
        if early:
            try:
                hh, mm = last.ts.split(" ")[1].split(":")[:2]
                if int(hh) * 60 + int(mm) < 10 * 60:
                    eff_ratio = ratio * 1.5
            except (IndexError, ValueError):
                pass  # ts 解析失败不加倍（保守放行到量检本身）
        if ratio > 1 and vol_ratio < eff_ratio:
            return SignalResult(False, {"vwap": round(vwap[-1], 3), "close": last.close,
                                        "vol_ratio": round(vol_ratio, 2),
                                        "need_ratio": round(eff_ratio, 2),
                                        "reason": "上穿无量"})
        # A1-2 开盘价收复：close > 今日开盘价（当日首根 bar 的 open）；取不到当日首根（多日
        # 序列被 lookback 截断）时降级放行并记录（主人 2026-09-05 拍板）
        open_reclaim_ok = True
        if need_open:
            today = last.ts[:10]
            day_bars = [b for b in seg if b.ts[:10] == today]
            if day_bars:
                day_open = day_bars[0].open
                open_reclaim_ok = last.close > day_open
                if not open_reclaim_ok:
                    return SignalResult(False, {"vwap": round(vwap[-1], 3), "close": last.close,
                                                "day_open": day_open,
                                                "reason": f"未收复开盘价 {day_open:.3f}"})
            else:
                open_reclaim_ok = None  # 降级放行：seg 内无当日 bar（异常数据，记录不拦截）
        # A1-5 低点抬高确认：站上均价线后的回落低点（close 局部低点）序列最近 low_higher 个
        # 依次抬高，防站上即回落假收复（主人 2026-09-05 拍板，默认 2）
        low_higher_ok = True
        low_seq: List[float] = []
        if low_higher > 0:
            for i in range(2, len(seg)):
                if seg[i - 1].close > vwap[i - 1] and seg[i].close > vwap[i] \
                        and seg[i].close <= seg[i - 1].close:
                    low_seq.append(seg[i].close)
            if len(low_seq) < low_higher:
                return SignalResult(False, {"vwap": round(vwap[-1], 3), "close": last.close,
                                            "low_seq": [round(x, 3) for x in low_seq],
                                            "reason": f"站上后回落低点 {len(low_seq)} 个 < {low_higher} 个"})
            last_n = low_seq[-low_higher:]
            if not all(last_n[k] > last_n[k - 1] for k in range(1, len(last_n))):
                return SignalResult(False, {"vwap": round(vwap[-1], 3), "close": last.close,
                                            "low_seq": [round(x, 3) for x in last_n],
                                            "reason": "回落低点未依次抬高（假收复）"})
            low_higher_ok = True
        # ---- A2 MACD 共振增强层终闸（主人 2026-09-05 拍板，工单 A2）----
        # 量价均线选位置、MACD 验动能：挂在全部量价检查之后（量价过才轮到动能否决）。
        # 禁区否决 fail-closed（A2-2 水下金叉不追 / A2-3 死叉水下禁买 / A2-5 15m 死叉水下
        # 当日不做）；数据不足降级放行 fail-open（meta.macd.notes 留痕）；A2-4 底背离只加分。
        macd_meta: Any = None
        macd_strong = macd_div_bonus = macd_degraded = False
        macd_reason = ""
        if bool(self.params.get("macd_resonance", True)):
            chk = macd_resonance_check(
                bars,
                bars_15m=self.params.get("macd_bars_15m"),
                min_bars_1m=int(self.params.get("macd_min_bars", 40)),
                min_bars_15m=int(self.params.get("macd_min_bars_15m", 35)),
                zero_band_pct=float(self.params.get("macd_zero_band_pct", 0.3)))
            macd_meta = chk["meta"]
            macd_strong = bool(chk["macd_strong"])
            macd_div_bonus = bool(chk["macd_div_bonus"])
            macd_degraded = bool(chk["degraded"])
            if chk["veto"]:
                return SignalResult(False, {
                    "signal": self.name, "ts": last.ts, "close": last.close,
                    "vwap": round(vwap[-1], 3), "cross_count": crosses,
                    "vol_ratio": round(vol_ratio, 2),
                    "macd": chk["meta"], "macd_veto": True,
                    "reason": chk["reason"]})
            tags = []
            if macd_strong:
                tags.append("0轴上金叉✓")
            if macd_div_bonus:
                tags.append("底背离✓")
            if macd_degraded:
                tags.append("数据不足放行")
            if tags:
                macd_reason = "，MACD：" + "+".join(tags)
        return SignalResult(True, {
            "signal": self.name, "ts": last.ts, "close": last.close,
            "vwap": round(vwap[-1], 3), "cross_count": crosses,
            "vol_ratio": round(vol_ratio, 2), "need_ratio": round(eff_ratio, 2),
            "hold_bars": hold, "open_reclaim_ok": open_reclaim_ok,
            "second_cross": crosses >= 2,
            "low_higher_ok": low_higher_ok, "low_seq": [round(x, 3) for x in low_seq[-3:]],
            "macd": macd_meta, "macd_strong": macd_strong,
            "macd_div_bonus": macd_div_bonus, "macd_degraded": macd_degraded,
            "reason": f"收复均价线 {vwap[-1]:.3f}（上穿{crosses}次，量比 {vol_ratio:.2f}"
                      + (", 低点抬高✓" if low_higher_ok and low_higher > 0 else "")
                      + (", 收复开盘价✓" if open_reclaim_ok else "") + macd_reason + "）",
        })


class HookTurnSignal(Signal):
    """HOOK_TURN · 急速勾头（§七）：急跌后 V 型勾头 + 分时量配合。

    判定（全部参数可配）：
    1. 急跌：低点前 drop_lookback 根内从高点回落跌幅 ≥ drop_threshold_pct%
    2. V 型：低点后连续 recover_bars 根收盘逐根回升，且当前收盘收复跌幅的 recover_ratio 比例
    3. 量配合：回升段均量 ≥ 下跌段均量 × vol_ratio

    参数：
    - drop_lookback      急跌回看窗口（默认 5 根）
    - drop_threshold_pct 急跌跌幅阈值 %（默认 2.0）
    - recover_bars       V 型确认连续回升根数（默认 3）
    - recover_ratio      收复跌幅比例（默认 0.5，收复一半即确认）
    - vol_ratio          回升段量 ≥ 下跌段均量 × 阈值（默认 1.2）
    - low_lookback       低点搜索窗口（默认 30 根，取窗口内 low 最低为 V 型谷底）
    """

    name = "HOOK_TURN"
    params = {"drop_lookback": 5, "drop_threshold_pct": 2.0, "recover_bars": 3,
              "recover_ratio": 0.5, "vol_ratio": 1.2, "low_lookback": 30}

    def _judge(self, bars: List[Candle]) -> SignalResult:
        dl = int(self.params["drop_lookback"])
        thr = float(self.params["drop_threshold_pct"]) / 100.0
        rb = int(self.params["recover_bars"])
        rec = float(self.params["recover_ratio"])
        vr = float(self.params["vol_ratio"])
        ll = int(self.params["low_lookback"])
        seg = bars[-ll:]
        if len(seg) < dl + rb + 2:
            return SignalResult(False, {"reason": "K线不足"})
        # 1. 找最近低点（low 最低，且非当前根——需已出现回升）
        low_idx = min(range(len(seg) - 1), key=lambda i: seg[i].low)
        low_b = seg[low_idx]
        drop_start = max(0, low_idx - dl)
        hi_before = max(b.high for b in seg[drop_start:low_idx + 1])
        if hi_before <= 0:
            return SignalResult(False, {"reason": "价格异常（前高非正）"})
        drop_pct = (hi_before - low_b.low) / hi_before
        if drop_pct < thr:
            return SignalResult(False, {"drop_pct": round(drop_pct * 100, 3),
                                        "reason": f"跌幅 {drop_pct * 100:.2f}% < {thr * 100:.1f}%（非急跌）"})
        # 2. V 型回升：低点后逐根收盘递增（连续），且当前收复跌幅比例
        rise = seg[low_idx + 1:]
        if len(rise) < rb:
            return SignalResult(False, {"reason": "回升段不足"})
        consec = all(rise[k].close > rise[k - 1].close for k in range(1, len(rise)))
        if not consec:
            return SignalResult(False, {"reason": "回升非连续（V 型不成立）"})
        target = low_b.low * (1 + drop_pct * rec)
        if rise[-1].close < target:
            return SignalResult(False, {"close": rise[-1].close, "target": round(target, 3),
                                        "reason": f"未收复跌幅比例（close {rise[-1].close:.3f} < {target:.3f}）"})
        # 3. 量配合：回升段均量 ≥ 下跌段均量 × vol_ratio
        down_avg = sum(b.volume for b in seg[drop_start:low_idx + 1]) / max(low_idx - drop_start + 1, 1)
        up_avg = sum(b.volume for b in rise[:rb]) / min(len(rise), rb)
        if down_avg > 0 and up_avg < down_avg * vr:
            return SignalResult(False, {"up_vol": round(up_avg, 1), "down_vol": round(down_avg, 1),
                                        "vol_ratio": round(up_avg / down_avg, 2) if down_avg else None,
                                        "reason": f"回升量能不足（{up_avg / down_avg:.2f} < {vr}）"})
        return SignalResult(True, {
            "signal": self.name, "ts": rise[-1].ts, "close": rise[-1].close,
            "low_ts": low_b.ts, "low": round(low_b.low, 3),
            "drop_pct": round(drop_pct * 100, 3),
            "recover_pct": round((rise[-1].close / low_b.low - 1) * 100, 3),
            "up_vol": round(up_avg, 1), "down_vol": round(down_avg, 1),
            "vol_ratio": round(up_avg / down_avg, 2) if down_avg else None,
            "reason": f"急跌 {drop_pct * 100:.2f}% 后 V 型勾头（收复 {(rise[-1].close / low_b.low - 1) * 100:.2f}%），"
                      f"回升量 {up_avg:.0f} ≥ 下跌均量 {down_avg:.0f} × {vr}",
        })


class MacdDivergenceSignal(Signal):
    """MACD_DIVERGENCE · 底背离待机（§11.1）：股价创新低 + DIF/绿柱不创新低。
    经典判法：连续两个回落低点（价格新低、DIF 抬高）计一次底背离；
    达到 min_divergences 次才触发（主人定：两次才稳妥）。底背离≠买入，只进 STANDBY。"""

    name = "MACD_DIVERGENCE"
    params = {"fast": 12, "slow": 26, "signal": 9, "min_divergences": 2, "lookback": 240}

    def _judge(self, bars: List[Candle]) -> SignalResult:
        lb = int(self.params["lookback"])
        seg = bars[-lb:]
        if len(seg) < 40:
            return SignalResult(False, {"reason": "K线不足（底背离至少需 40 根）"})
        closes = [b.close for b in seg]
        dif, dea, hist = macd_lines(closes, int(self.params["fast"]),
                                    int(self.params["slow"]), int(self.params["signal"]))
        # 找回落低点：close 局部最小（左右各 1 根更低/相等区间内最小）
        lows_idx = [i for i in range(2, len(seg) - 1)
                    if seg[i].close <= seg[i - 1].close and seg[i].close <= seg[i + 1].close]
        lows_idx = [i for i in lows_idx if seg[i].close <= min(seg[i - 2:i + 3], key=lambda b: b.close).close + 1e-9]
        events = []
        for a, b in zip(lows_idx, lows_idx[1:]):
            if seg[b].close < seg[a].close and dif[b] > dif[a]:
                events.append({"low_idx": b, "price": round(seg[b].close, 3),
                               "prev_low_price": round(seg[a].close, 3),
                               "dif": round(dif[b], 4), "prev_dif": round(dif[a], 4)})
        last_low = seg[lows_idx[-1]] if lows_idx else None
        if len(events) >= int(self.params["min_divergences"]):
            return SignalResult(True, {
                "signal": self.name, "ts": seg[-1].ts, "close": seg[-1].close,
                "divergences": events, "count": len(events),
                "standby": True,  # §11.1：只进待机，不单独触发买入
                "reason": f"底背离×{len(events)}（价格新低 + DIF 不创新低）→ 待机 STANDBY，需金叉/放量反包确认",
            })
        return SignalResult(False, {
            "divergences": events, "count": len(events),
            "last_low": round(last_low.close, 3) if last_low else None,
            "reason": f"底背离 {len(events)} 次 < {int(self.params['min_divergences'])} 次，未达待机标准",
        })


class WeakToStrongSignal(Signal):
    """WEAK_TO_STRONG · 弱转强（第五模式）：昨日弱势 → 竞价高开超预期 → 首根放量 → 持续站上均价线。

    判定（输入 candles 第一根 = 开盘后首根 K；昨收 params.prev_close 由上层从日线显式传入）：
    1. 竞价超预期：gap = 首根 open / prev_close − 1 ∈ [gap_min, gap_max]
    2. 首根放量：首根量比 ≥ vol_ratio_min（量比 = 首根量 / 后续K均量，缺历史分钟量能时的工程口径）
    3. 站稳：随后 confirm_bars 根收盘持续 > VWAP（库内 vwap_series），且盘中不跌破开盘价（low ≥ 首根 open）
    可选前置 weak_prev（默认 True）：昨日收阴（close<open，需 params.prev_open）或昨日跌幅≤0
    （需 params.prev_prev_close）；缺数据时跳过不阻塞，meta.weak_check 标注 skipped。

    参数：gap_min/gap_max/vol_ratio_min/confirm_bars/weak_prev/prev_close 为契约键；
    prev_open/prev_prev_close 为 weak_prev 判定的可选增强键（上层有日线昨开/前收时传入）。
    """

    name = "WEAK_TO_STRONG"
    params = {"gap_min": 0.02, "gap_max": 0.05, "vol_ratio_min": 1.5,
              "confirm_bars": 3, "weak_prev": True, "prev_close": None,
              "prev_open": None, "prev_prev_close": None}

    def _judge(self, bars: List[Candle]) -> SignalResult:
        prev_close = self.params.get("prev_close")
        gap_min = float(self.params["gap_min"])
        gap_max = float(self.params["gap_max"])
        vol_min = float(self.params["vol_ratio_min"])
        confirm = int(self.params["confirm_bars"])
        if prev_close is None or prev_close <= 0:
            return SignalResult(False, {"reason": "缺少有效 prev_close（上层须从日线传入昨收）"})
        if len(bars) < 1 + confirm:
            return SignalResult(False, {"reason": f"K线不足（需 ≥{1 + confirm} 根：首根 + {confirm} 根确认）"})
        first = bars[0]
        # 1. 竞价高开 gap（昨收为基准）
        gap = first.open / prev_close - 1.0
        if gap < gap_min:
            return SignalResult(False, {"gap": round(gap * 100, 3),
                                        "reason": f"高开 {gap * 100:.2f}% < {gap_min * 100:.1f}%（竞价未超预期）"})
        if gap > gap_max:
            return SignalResult(False, {"gap": round(gap * 100, 3),
                                        "reason": f"高开 {gap * 100:.2f}% > {gap_max * 100:.1f}%（高开过度，非弱转强形态）"})
        # 2. 首根放量：量比 = 首根量 / 首根之后 K 均量（工程口径，缺历史分钟量能基准）
        tail = bars[1:]
        base_vol = sum(b.volume for b in tail) / len(tail)
        vol_ratio = (first.volume / base_vol) if base_vol > 0 else 0.0
        if vol_ratio < vol_min:
            return SignalResult(False, {"gap": round(gap * 100, 3), "vol_ratio": round(vol_ratio, 2),
                                        "reason": f"首根量比 {vol_ratio:.2f} < {vol_min}（未放量）"})
        # 3. 持续站上均价线且不回落跌破开盘价（confirm_bars 根）
        vwap = vwap_series(bars)
        open0 = first.open
        for k in range(1, confirm + 1):
            b = bars[k]
            if b.close <= vwap[k]:
                return SignalResult(False, {"gap": round(gap * 100, 3), "vol_ratio": round(vol_ratio, 2),
                                            "k": k, "close": b.close, "vwap": round(vwap[k], 3),
                                            "reason": f"第{k + 1}根收盘 {b.close:.3f} 未站上均价线 {vwap[k]:.3f}"})
            if b.low < open0:
                return SignalResult(False, {"gap": round(gap * 100, 3), "vol_ratio": round(vol_ratio, 2),
                                            "k": k, "low": b.low, "open0": open0,
                                            "reason": f"第{k + 1}根盘中跌破开盘价（{b.low:.3f} < {open0:.3f}）"})
        # 4. 昨日弱势前置（可选，默认开）：收阴或昨日跌幅≤0；缺数据跳过不阻塞
        weak_prev = bool(self.params.get("weak_prev", True))
        weak_check = "skipped"
        if weak_prev:
            weak_check = self._prev_weak(prev_close, self.params.get("prev_open"),
                                         self.params.get("prev_prev_close"))
            if weak_check == "not_weak":
                return SignalResult(False, {"reason": "昨日非弱势（前置不满足，非弱转强场景）"})
        last = bars[confirm]
        return SignalResult(True, {
            "signal": self.name, "ts": last.ts, "close": last.close,
            "gap": round(gap * 100, 3), "prev_close": prev_close, "open0": round(open0, 3),
            "vol_ratio": round(vol_ratio, 2), "base_vol": round(base_vol, 1),
            "vwap": round(vwap[confirm], 3), "confirm_bars": confirm,
            "weak_prev": weak_prev, "weak_check": weak_check,
            "reason": f"昨日弱势→高开 {gap * 100:.2f}%→首根量比 {vol_ratio:.2f}"
                      f"→{confirm} 根站上均价线不破开盘价 → 弱转强",
        })

    def _prev_weak(self, prev_close, prev_open, prev_prev_close):
        """昨日弱势 = 昨日收阴（close<open）或昨日跌幅≤0（close≤前收）。缺数据返回 skipped 不阻塞。"""
        if prev_open is not None and prev_open > 0 and prev_close < prev_open:
            return "weak_ying"
        if prev_prev_close is not None and prev_prev_close > 0 and prev_close <= prev_prev_close:
            return "weak_die"
        if prev_open is not None or prev_prev_close is not None:
            return "not_weak"
        return "skipped"


class StopSignal(Signal):
    """STOP · 止损触发（细则模板：止损锚经 StopAnchorPicker 择优）。

    参数：
    - stop_price   止损价（锚点，如 黄线/动能K低点×0.999/N型低点/B2实体一半…）
    - basis        锚点依据一句话
    - mode         close=收盘跌破触发（默认，黄线口径 §1.1 以收盘为准）；intraday=盘中触及触发（加速下杀场景）
    """

    name = "STOP"
    params = {"stop_price": None, "basis": "", "mode": "close"}

    def _judge(self, bars: List[Candle]) -> SignalResult:
        stop = self.params.get("stop_price")
        if stop is None or stop <= 0:
            return SignalResult(False, {"reason": "缺少有效 stop_price"})
        last = bars[-1]
        mode = self.params.get("mode", "close")
        if mode == "intraday":
            hit = last.low <= stop
        else:
            hit = last.close < stop
        if not hit:
            return SignalResult(False, {"stop_price": stop, "close": last.close, "mode": mode,
                                        "distance_pct": round((last.close / stop - 1) * 100, 3),
                                        "reason": "未触止损"})
        return SignalResult(True, {
            "signal": self.name, "ts": last.ts, "close": last.close, "low": last.low,
            "stop_price": stop, "mode": mode, "basis": self.params.get("basis", ""),
            "distance_pct": round((last.close / stop - 1) * 100, 3),
            "reason": f"{mode}触发止损 {stop:.3f}（basis={self.params.get('basis', '')}）",
        })


# ---------------- 止损锚择优（细则 §三） ----------------

def _safe(x):
    try:
        x = float(x)
        return x == x and abs(x) != float("inf")
    except (TypeError, ValueError):
        return False


def select_stop_anchor(candidates, buy_price, target_price,
                       min_dist=0.01, max_dist=0.08):
    """细则 §2.1/§3.1：候选池择优——止损距离 1%~8% 双向剔除，取盈亏比最高者（并列取距离最近）。

    candidates: [{"name": "黄线", "price": 8.5}, ...]（无效候选传 price=None 或 NaN）
    返回 {ok, anchor, stop_price, distance_pct, rr, basis}；全部剔除 → ok=False。
    """
    if not _safe(buy_price) or not _safe(target_price) or buy_price <= 0:
        return {"ok": False, "reason": "买点/目标价无效"}
    if target_price <= buy_price:
        return {"ok": False, "reason": "目标价未高于买点（目标=前N型高点须在买点上方）"}
    best = None
    for c in candidates or []:
        p = c.get("price")
        if not _safe(p) or p <= 0:
            continue
        dist = (buy_price - p) / buy_price
        if dist < min_dist - 1e-9:
            continue  # 噪音内剔除（§2.1）
        if dist > max_dist:
            continue  # 距离过远剔除（§2.1）
        rr = (target_price - buy_price) / (buy_price - p)
        key = (rr, -dist)  # 盈亏比最大；并列取距离最近（dist 大者止损更近）
        if best is None or key > best[0]:
            best = (key, c["name"], p, dist, rr)
    if best is None:
        return {"ok": False, "reason": f"候选全部剔除（止损距离须在 {min_dist:.0%}~{max_dist:.0%} 之间）"}
    _, name, p, dist, rr = best
    return {
        "ok": True, "anchor": name, "stop_price": round(p, 3),
        "distance_pct": round(dist * 100, 3), "rr": round(rr, 3),
        "basis": f"{name}，止损 {dist * 100:.2f}%，盈亏比 {rr:.2f}",
    }


def b2_stop_candidates(attack_candle) -> List[Dict[str, Any]]:
    """B2 止损候选：阳线实体一半 或 阳线低点（细则 §七 B2）。attack_candle 为买点对应的攻击阳线。"""
    half = (attack_candle.open + attack_candle.close) / 2.0
    return [{"name": "B2阳线实体一半", "price": half},
            {"name": "B2阳线低点", "price": attack_candle.low}]


def b3_stop_candidates(attack_candle, self_low) -> List[Dict[str, Any]]:
    """B3 止损候选：自身最低点 或 阳线实体一半（细则 §七 B3）。"""
    half = (attack_candle.open + attack_candle.close) / 2.0
    return [{"name": "B3自身最低点", "price": self_low},
            {"name": "B3阳线实体一半", "price": half}]


def ek_stop_candidates(bars: List[Candle], ek_hits: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """动能K止损候选：最近两根动能K各自最低价 ×0.999（细则 §5.3）。
    ek_hits 未提供时用简化 EK1 判据（量>昨量 且 >10日均量 且 阳线）自动找。"""
    if ek_hits is None:
        ek_hits = simplified_ek_hits(bars)
    cands = []
    for idx in ek_hits[-2:]:
        b = bars[idx]
        cands.append({"name": f"动能K低点({b.ts[11:16]})×0.999", "price": round(b.low * 0.999, 3)})
    return cands


def simplified_ek_hits(bars: List[Candle], vol_mult=1.0, vol_ma=10) -> List[int]:
    """简化 EK1：量 > 昨量 且 > 前 vol_ma 日均量 且 阳线（close≥open）。返回命中索引列表。"""
    hits = []
    vols = [b.volume for b in bars]
    for i in range(max(2, vol_ma), len(bars)):
        prev_avg = sum(vols[i - vol_ma:i - 1]) / (vol_ma - 1)
        if prev_avg > 0 and vols[i] > vols[i - 1] * vol_mult and vols[i] > prev_avg and bars[i].close >= bars[i].open:
            hits.append(i)
    return hits


def previous_n_high(candles: List[Candle], lookback=60) -> Optional[float]:
    """目标 = 前一个 N 型高点（§2.2 保守口径）工程近似：回看窗口内最高价。
    精确前N型高点（第一涨顶部）由选股侧给出时，直接以 target_price 参数传入 select_stop_anchor。"""
    if len(candles) < 2:
        return None
    return max(b.high for b in candles[-lookback - 1:-1])


class StopAnchorPicker:
    """止损锚择优器：B1（黄线/动能K低点×2/60分钟动能K/N型低点）/ B2 / B3 共用同一择优函数。

    用法：
        picker = StopAnchorPicker(buy_price=10.0, target_price=11.5)
        picker.add_candidate("黄线", 9.4).add_candidate("N型低点", 9.6)
        r = picker.pick()
    """

    def __init__(self, buy_price, target_price, min_dist=0.01, max_dist=0.08):
        self.buy_price = buy_price
        self.target_price = target_price
        self.min_dist = min_dist
        self.max_dist = max_dist
        self._candidates: List[Dict[str, Any]] = []

    def add_candidate(self, name, price):
        self._candidates.append({"name": name, "price": price})
        return self

    def pick(self):
        return select_stop_anchor(self._candidates, self.buy_price, self.target_price,
                                  self.min_dist, self.max_dist)
