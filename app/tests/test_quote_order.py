# -*- coding: utf-8 -*-
"""下单行情快照（quotes.order_quote / order_metrics / build_quote5_payload）契约测试。

不打真实网络：全部用假对象 + monkeypatch，不连 eltdx。
口径与旧 8800 `server.py::_order_quote()` 的 eltdx 半段一致（任务书 4.1.3a-1）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import quotes as Q  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    # 清 depth/ticks/bars/aux/指标缓存，避免用例互相串味
    Q.reset()
    yield
    Q.reset()


# ---------------------------------------------------------------- 假对象


class _Lv:
    """eltdx QuoteLevel 的最小形状。"""

    def __init__(self, price: float, volume: int) -> None:
        self.price = price
        self.volume = volume


class _Rec:
    """eltdx QuoteRefreshRecord（0x0547）的最小形状。"""

    def __init__(self, update_time_raw=145959) -> None:
        self.last_price = 1688.0
        self.last_close_price = 1670.0
        self.high_price = 1699.5
        self.low_price = 1666.0
        self.update_time_raw = update_time_raw
        # 卖1..卖5（升序）；买1..买5（降序）；最后一档价格 0 → 该档留空
        self.sell_levels = [_Lv(1688.5 + i, 100 + i) for i in range(4)] + [_Lv(0.0, 9)]
        self.buy_levels = [_Lv(1688.0 - i, 200 + i) for i in range(4)] + [_Lv(0.0, 9)]
        self.total_hand = 12345
        self.current_hand = 12
        self.outer_disc = 6000
        self.inside_dish = 6345


def _stale_hhmmss() -> int:
    """把 quote_time 钉到 1 小时前（HHMMSS 整数）。

    1 小时 > 120s 阈值，必为 stale；跨零点回绕时 `nsec - qsec` 变负数，
    旧端口径同样判 False，所以任意时刻运行都稳定。
    """
    t = datetime.now() - timedelta(hours=1)
    return t.hour * 10000 + t.minute * 100 + t.second


# ---------------------------------------------------------------- 1. payload 形状


def test_build_payload_shapes() -> None:
    """五档/现价/昨收/时间 与旧端同形；非法档位留空、不编造。"""
    rec = _Rec()
    q = Q.build_quote5_payload("600519.SH", rec, ticks=None, aux=None)

    assert q["code"] == "600519.SH"
    assert q["last"] == 1688.0
    assert q["pre_close"] == 1670.0
    assert q["change"] == 18.0
    assert q["change_pct"] == round(18.0 / 1670.0 * 100, 3)
    assert q["high"] == 1699.5
    assert q["low"] == 1666.0
    assert q["quote_time"] == "14:59:59"

    # 卖5 价格 0 → 丢弃，只留 4 档；买同理
    assert len(q["asks"]) == 4
    assert len(q["bids"]) == 4
    assert q["asks"][0] == {"price": 1688.5, "vol": 100}
    assert q["bids"][0] == {"price": 1688.0, "vol": 200}
    # asks 升序、bids 降序（源顺序即如此，这里确认没被重排）
    assert [a["price"] for a in q["asks"]] == sorted(a["price"] for a in q["asks"])
    assert [b["price"] for b in q["bids"]] == sorted(
        (b["price"] for b in q["bids"]), reverse=True)

    assert q["stats"]["total_vol"] == 12345
    assert q["stats"]["now_vol"] == 12
    assert q["stats"]["outer"] == 6000
    assert q["stats"]["inner"] == 6345
    # 无 aux → 换手率/量比留 None（不补零）
    assert q["stats"]["turnover"] is None
    assert q["stats"]["vol_ratio"] is None
    assert q["limit_up"] is None and q["limit_down"] is None
    assert q["ticks"] == []
    assert q["source"].startswith("eltdx:")


def test_build_payload_none_rec_keeps_shape() -> None:
    """rec 为 None（eltdx 不可用）仍返回完整骨架，不抛。"""
    q = Q.build_quote5_payload("600519.SH", None, aux={"limit_up": 11.0})
    assert q["last"] is None and q["pre_close"] is None
    assert q["asks"] == [] and q["bids"] == []
    assert q["limit_up"] == 11.0


# ---------------------------------------------------------------- 2. 指标降级


def test_order_metric_fallback(monkeypatch) -> None:
    """日线取不到 → 两个指标都 None（条件单评估时自己跳过，不编造）。"""
    monkeypatch.setattr(Q, "daily_bars", lambda *a, **k: [])
    assert Q.order_metrics("sh600519") == {"atr14": None, "ma5": None}


# ---------------------------------------------------------------- 3. 快照骨架 + fresh


def test_order_quote_fresh_false_on_stale_time(monkeypatch) -> None:
    """quote_time 落后 1 小时 → fresh False；字段齐全。"""
    monkeypatch.setattr(Q, "depth", lambda glb: _Rec(update_time_raw=_stale_hhmmss()))
    monkeypatch.setattr(Q, "stock_aux", lambda glb: {"avg5": 1000.0, "circ": 1.0e9,
                                                     "limit_up": 1837.0,
                                                     "limit_down": 1503.0})
    monkeypatch.setattr(Q, "today_ticks", lambda glb, **k: [])
    monkeypatch.setattr(Q, "daily_bars", lambda *a, **k: [])

    q = Q.order_quote("600519.SH")

    assert q["fresh"] is False
    assert q["last"] == 1688.0
    assert q["pre_close"] == 1670.0
    assert q["quote_time"] is not None
    assert q["limit_up"] == 1837.0 and q["limit_down"] == 1503.0
    assert q["stats"]["turnover"] is not None      # circ + total 齐了
    # vol_ratio 取决于 quote_time 落在开市第几分钟（非交易时段 _elapsed_minutes=0 → 留 None），
    # 这里只断言键存在、类型合法，不断言具体值（避免依赖跑测时的钟点）
    assert "vol_ratio" in q["stats"]
    # 指标走 daily_bars 空 → None；键必须在（调用方无脑取）
    assert q["atr14"] is None and q["ma5"] is None
    # 不 overlay QMT：core 不设 src
    assert "src" not in q


def test_order_quote_fresh_true_on_recent_time(monkeypatch) -> None:
    """刚成交的时间戳 → fresh True（120s 口径）。"""
    now = datetime.now()
    if now.hour == 23 and now.minute == 59:
        # 23:59:xx 打到 00:00:xx 会让 `nsec - qsec` 变负（旧端口径判 False），
        # 这是口径本身的跨零点行为，不是被测逻辑的问题——跳过这一分钟
        pytest.skip("跨零点窗口，fresh 口径不可判定")
    raw = now.hour * 10000 + now.minute * 100 + now.second
    monkeypatch.setattr(Q, "depth", lambda glb: _Rec(update_time_raw=raw))
    monkeypatch.setattr(Q, "stock_aux", lambda glb: {})
    monkeypatch.setattr(Q, "today_ticks", lambda glb, **k: [])
    monkeypatch.setattr(Q, "daily_bars", lambda *a, **k: [])

    assert Q.order_quote("600519.SH")["fresh"] is True


def test_order_quote_depth_unavailable(monkeypatch) -> None:
    """eltdx 不可用（rec None）→ 返回骨架、fresh False，不抛。"""
    monkeypatch.setattr(Q, "depth", lambda glb: None)
    monkeypatch.setattr(Q, "stock_aux", lambda glb: {})
    monkeypatch.setattr(Q, "today_ticks", lambda glb, **k: [])
    monkeypatch.setattr(Q, "daily_bars", lambda *a, **k: [])

    q = Q.order_quote("600519.SH")
    assert q["last"] is None
    assert q["fresh"] is False


# ---------------------------------------------------------------- 4. 非法代码


def test_order_quote_bad_code() -> None:
    """代码无法归一 → {}（调用方按无行情处理）。"""
    assert Q.order_quote("bad") == {}
    assert Q.order_quote("") == {}
    assert Q.order_quote("600519.XX") == {}


# ---------------------------------------------------------------- 5. 指标口径


def _flat_bars(n: int = 20) -> list[dict[str, float]]:
    """等差日线：high=10+i / low=9+i / close=9.5+i。

    TR_0 = 1.0，其后每根 TR = max(1.0, |Δhigh|, |Δlow| vs 前收) = 1.5，
    所以 14 根窗口（末 14 根不含首根）的 ATR14 = 1.5。
    """
    return [{"high": 10.0 + i, "low": 9.0 + i, "close": 9.5 + i} for i in range(n)]


def test_compute_atr14_and_ma5_pure() -> None:
    """纯函数口径：不足根数 → None；正常 20 根 → 有值。"""
    flat = _flat_bars()
    assert Q.compute_atr14(flat) == 1.5
    assert Q.compute_ma5(flat) == round(sum(b["close"] for b in flat[-5:]) / 5, 3)
    # 不足根数：ATR 要 15 根（凑 14 个完整 TR）、MA5 要 5 根
    assert Q.compute_atr14(flat[:14]) is None
    assert Q.compute_ma5(flat[:4]) is None
    # 非法价（≤0）→ 整票放弃（脏 ATR 比缺失更危险）
    bad = [dict(b) for b in flat]
    bad[0]["low"] = 0.0
    assert Q.compute_atr14(bad) is None


def test_order_metrics_uses_bar_fields(monkeypatch) -> None:
    """日线对象字段：优先 high_price/low_price/close_price，缺了才回退 high/low。"""

    class _Bar:
        """同时带两套字段，短名故意填假值——取错就会被断言抓到。"""

        def __init__(self, i: int) -> None:
            self.high_price = 10.0 + i
            self.low_price = 9.0 + i
            self.close_price = 9.5 + i
            self.high = -1.0
            self.low = -1.0
            self.close = -1.0

    class _ShortBar:
        """只有短名（上游形状变了时的兜底）。"""

        def __init__(self, i: int) -> None:
            self.high = 10.0 + i
            self.low = 9.0 + i
            self.close = 9.5 + i

    seen = {}

    def fake_bars(glb, count=40, adjust=None):  # noqa: ANN001
        seen["count"] = count
        return [_Bar(i) for i in range(20)]

    monkeypatch.setattr(Q, "daily_bars", fake_bars)
    out = Q.order_metrics("sh600519")
    assert seen["count"] == 40          # 任务书：daily_bars(glb, 40)
    assert out["atr14"] == 1.5
    assert out["ma5"] == round(sum(9.5 + i for i in range(15, 20)) / 5, 3)

    # 只给短名的对象也能算（getattr(b,"high") 兜底）
    monkeypatch.setattr(Q, "daily_bars", lambda *a, **k: [_ShortBar(i) for i in range(20)])
    Q.reset()
    out2 = Q.order_metrics("sh600519")
    assert out2["atr14"] == 1.5
