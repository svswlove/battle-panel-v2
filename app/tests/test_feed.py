# -*- coding: utf-8 -*-
"""core QMT 行情订阅（app/core/feed.py）契约测试。

**零真实 QMT**：全部用假 `xtdata` 模块（`xtdata_module=` 注入），不连 58610、不写文件。
任务书 4.1.3a-2 §6 的 7 条全覆盖，另加几条边界（坏数据 / 队列满 / 未声明 manifest）。
"""
from __future__ import annotations

import queue as _queue
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import feed as F  # noqa: E402
from app.core import quotes as Q  # noqa: E402
from app.core.contract import ContractError, PluginContext, validate_manifest  # noqa: E402


# ---------------------------------------------------------------- 假 QMT


class FakeXt:
    """假行情模块（2026-10-26 MIGRATED：推送式 → 轮询式）。

    形状按 `eltdx_adapter` 的实际接口：`qmt_port_open()` 探活 + `get_full_tick(codes)` 批量拉取。
    测试用 `push()` 放进下一轮要返回的 tick，再 `feed._poll_once()` 确定性驱动。
    仍然零联网、零文件写入。
    """

    def __init__(self, fail_connect: int = 0) -> None:
        self.pending: dict = {}          # 下一轮 get_full_tick 的返回体
        self.polls = 0                   # 拉取轮数（替代旧 `seq`）
        self.connected = False
        self.codes: list[str] = []       # 历次请求过的代码并集
        self.calls: list[list[str]] = []
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.fail_connect = fail_connect       # 前 N 次探活失败（模拟行情源没起）

    def connect(self, ip: str = "", port=None):  # noqa: ANN001, ANN201
        self.connect_calls += 1
        if self.fail_connect > 0:
            self.fail_connect -= 1
            raise RuntimeError("行情源未就绪")
        self.connected = True
        return object()

    def qmt_port_open(self) -> bool:
        """feed 的探活入口（adapter 同名同签名）；走 connect 以复用失败注入。"""
        try:
            return self.connect() is not None
        except RuntimeError:
            return False

    def get_full_tick(self, codes):  # noqa: ANN001, ANN201
        self.polls += 1
        self.calls.append(list(codes))
        self.codes = list(dict.fromkeys(self.codes + list(codes)))
        return dict(self.pending)

    def push(self, batch: dict) -> None:
        """准备下一轮要返回的 tick（等价于旧版真回调 `cb(...)`）。"""
        self.pending = dict(batch)

    @property
    def seq(self) -> int:
        """兼容旧断言的别名：轮询架构下“轮数”即“序号”。"""
        return self.polls

    def disconnect(self):  # noqa: ANN201
        self.disconnect_calls += 1
        self.connected = False


def _now_ms() -> int:
    """QMT `time` 是毫秒时间戳。"""
    return int(time.time() * 1000)


def _tick(last: float = 1688.0, pre: float = 1670.0, ms: int | None = None) -> dict:
    """一条最小 QMT tick（字段名与 xtdata 一致）。"""
    return {
        "lastPrice": last,
        "lastClose": pre,
        "bidPrice": [1687.9, 1687.8, 1687.7, 1687.6, 1687.5, 1687.4],
        "bidVol": [100, 200, 300, 400, 500, 600],
        "askPrice": [1688.1, 1688.2, 1688.3, 1688.4, 1688.5, 1688.6],
        "askVol": [110, 210, 310, 410, 510, 610],
        "time": _now_ms() if ms is None else ms,
    }


# ---------------------------------------------------------------- 夹具


@pytest.fixture(autouse=True)
def _reset_feed():
    """每个用例前后都丢掉单例 + 清 quotes 缓存，避免串味。"""
    F.reset_feed()
    Q.reset()
    yield
    F.reset_feed()
    Q.reset()


def wait_until(fn, timeout: float = 3.0, interval: float = 0.01) -> bool:
    """等到 fn() 为真；超时返回 False（**不 sleep 死等**，测试要快）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


def make_feed(xt, **kwargs) -> F.QuoteFeed:
    """构造 feed；默认把重试窗口压到极短，测试绝不真的等 30s。"""
    params = {
        "xtdata_module": xt,
        "retry": 1,
        "retry_interval_s": 0.01,
        "stale_s": 60.0,
        "reconnect_throttle_s": 60.0,
        "dispatch_interval_s": 0.01,
    }
    params.update(kwargs)
    return F.QuoteFeed(**params)


def make_threadless_feed(xt, **kwargs) -> F.QuoteFeed:
    """**不起线程**的 feed：内部状态手工摆成「已连接」，只测内存路径。

    这样 `_on_quote` / `_drain_once` / `_pump_once` 都是同步可断言的，
    不会出现「消费者线程先把队列吃掉了」这种 flaky。
    巡检时间戳也一起摆好，避免一进来就判断流重连。
    """
    feed = make_feed(xt, **kwargs)
    feed._stop_evt = threading.Event()
    feed._connected = True
    # 模拟「本就连着且订阅过」：重连计数与退订才走真实分支
    feed._ever_connected = True
    feed._started = True
    feed._state = "CONNECTED"
    feed._seq = 1
    now = time.monotonic()
    feed._last_connect_mono = now
    feed._last_tick_mono = now
    feed._last_stale_mono = now
    return feed


# ---------------------------------------------------------------- 1. start 订阅 + 派发


def test_start_subscribes_and_dispatches() -> None:
    """ensure → start → 假回调 → listener 收到 batch → quote() 有 last。"""
    xt = FakeXt()
    feed = make_feed(xt)
    feed.ensure(["600519.SH"])
    got: list[dict] = []
    feed.add_listener(lambda batch: got.append(batch))

    feed.start()
    # supervisor 线程立刻探活（假模块瞬时返回），等它把代码登记进轮询表
    assert wait_until(lambda: feed.status()["subscribed_count"] >= 1), "supervisor 未在 3s 内完成登记"

    xt.push({"600519.SH": _tick(last=1688.0)})
    assert feed._poll_once() is True
    assert wait_until(lambda: bool(got)), "listener 未收到派发"
    batch = got[-1]
    assert "600519.SH" in batch
    assert batch["600519.SH"]["last"] == 1688.0
    assert xt.calls[-1] == ["600519.SH"]        # 真的是拿这只码去拉的

    q = feed.quote("600519.SH")
    assert q is not None
    assert q["last"] == 1688.0
    assert q["pre_close"] == 1670.0
    assert len(q["bids"]) == 5          # 只留前 5 档
    assert len(q["asks"]) == 5
    assert q["bids"][0] == {"price": 1687.9, "vol": 100}
    assert q["asks"][0] == {"price": 1688.1, "vol": 110}
    assert q["quote_time"] is not None
    assert q["fresh"] is True           # 刚推的 tick 必然新鲜

    feed.stop()


# ---------------------------------------------------------------- 2. 增量订阅


def test_incremental_ensure() -> None:
    """ensure 只增不减；新代码由下一次巡检（≤1s）登记进轮询表。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)

    feed.ensure(["600519.SH"])
    assert feed._pump_once() is True
    assert feed.status()["subscribed"] == ["600519.SH"]

    feed.ensure(["000001.SZ"])
    feed._pump_once()                     # 等价于 supervisor 的 1s 巡检
    assert sorted(feed.status()["subscribed"]) == ["000001.SZ", "600519.SH"]   # 两只都在，老的没被砍

    # 重复 ensure 同一只：不新增登记
    n = feed.status()["subscribed_count"]
    feed.ensure(["600519.SH"])
    feed._pump_once()
    assert feed.status()["subscribed_count"] == n

    assert feed.status()["subscribed_count"] == 2


# ---------------------------------------------------------------- 3. 队列合并


def test_queue_coalesce() -> None:
    """同 code 连发 3 条，消费者只派发**最后一条**。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    batches: list[dict] = []
    feed.add_listener(lambda batch: batches.append(batch))

    for last in (1681.0, 1685.0, 1689.0):
        feed._on_quote({"600519.SH": _tick(last=last)})

    assert feed._drain_once() == 1        # 只派发 1 个标的
    assert len(batches) == 1
    assert batches[0]["600519.SH"]["last"] == 1689.0      # 最后一条
    # 内存快照也是最后一条
    assert feed.quote("600519.SH")["last"] == 1689.0
    # 队列已排空
    assert feed.status()["queue_len"] == 0


def test_drain_does_not_dispatch_when_stopped() -> None:
    """stop 后再 drain → 0（停机后绝不再回调 listener）。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    batches: list[dict] = []
    feed.add_listener(lambda batch: batches.append(batch))
    feed._on_quote({"600519.SH": _tick()})
    feed._stop_evt.set()                  # 模拟 stop 的停机信号
    assert feed._drain_once() == 0
    assert batches == []


# ---------------------------------------------------------------- 4. status 形状


def test_status_shape() -> None:
    """status() 含任务书 §2.2-7 要求的全部键（缺一个监控就瞎一眼）。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    feed._on_quote({"600519.SH": _tick()})
    st = feed.status()

    for key in ("state", "connected", "subscribed", "subscribed_count",
                "tick_count", "last_tick_at", "last_tick_age_s",
                "reconnect_count", "error", "updated_at"):
        assert key in st, f"status 缺键: {key}"

    assert st["state"] == "CONNECTED"
    assert st["connected"] is True
    assert st["subscribed_count"] == len(st["subscribed"])
    assert st["tick_count"] == 1
    assert st["last_tick_at"] is not None
    assert isinstance(st["last_tick_age_s"], float)
    assert st["reconnect_count"] == 0
    assert st["error"] is None
    # 观测性附加键（队列/丢弃/listener 故障）
    for key in ("queue_len", "dropped", "listener_errors", "codes", "host"):
        assert key in st


def test_listener_exception_is_counted() -> None:
    """单个 listener 抛异常 → 吞掉 + 计数，其他 listener 照常收到。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    ok: list[dict] = []

    def boom(batch):  # noqa: ANN001
        raise RuntimeError("listener 坏了")

    feed.add_listener(boom)
    feed.add_listener(lambda batch: ok.append(batch))
    feed._on_quote({"600519.SH": _tick()})
    feed._drain_once()

    assert len(ok) == 1
    assert feed.status()["listener_errors"] == 1


# ---------------------------------------------------------------- 5. stop 幂等


def test_stop_idempotent() -> None:
    """stop() 两次不抛；之后回调不再触发 listener。"""
    xt = FakeXt()
    feed = make_feed(xt)
    feed.ensure(["600519.SH"])
    got: list[dict] = []
    feed.add_listener(lambda batch: got.append(batch))

    feed.start()
    assert wait_until(lambda: feed.status()["subscribed_count"] >= 1)
    xt.push({"600519.SH": _tick()})
    assert feed._poll_once() is True
    assert wait_until(lambda: bool(got))

    feed.stop()
    feed.stop()                         # 第二次：幂等，不抛
    assert feed.status()["state"] == "STOPPED"

    before = len(got)
    xt.push({"600519.SH": _tick(last=1999.0)})     # 停机后再拉：必须被丢弃
    assert feed._poll_once() is False               # 停机已清登记 → 一轮不拉
    feed._drain_once()
    time.sleep(0.05)
    assert len(got) == before


def test_stop_without_start_is_noop() -> None:
    """没 start 就 stop：不抛，也不把状态改成 STOPPED（仍 DISABLED）。"""
    xt = FakeXt()
    feed = make_feed(xt)
    feed.stop()
    feed.stop()
    assert feed.status()["state"] == "DISABLED"
    assert xt.connect_calls == 0


# ---------------------------------------------------------------- 6. order_quote overlay


def test_order_quote_overlay(monkeypatch) -> None:
    """eltdx 打底 + 5 秒内 QMT tick 覆盖 last/bids/asks/quote_time，src=qmt。"""
    fixed = {
        "code": "600519.SH", "last": 1600.0, "pre_close": 1590.0,
        "bids": [{"price": 1599.9, "vol": 1}], "asks": [{"price": 1600.1, "vol": 1}],
        "quote_time": "14:59:59", "fresh": False, "atr14": 12.5, "ma5": 1580.0,
    }
    monkeypatch.setattr(Q, "order_quote", lambda ts_code: dict(fixed))

    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    feed._on_quote({"600519.SH": _tick(last=1688.0, pre=1670.0)})   # ts=now

    q = feed.order_quote("600519.SH")
    assert q["src"] == "qmt"
    assert q["last"] == 1688.0                     # 被 QMT 覆盖
    assert q["bids"][0]["price"] == 1687.9
    assert q["asks"][0]["price"] == 1688.1
    # quote_time 用 tick 的（与内存快照一致），不是 eltdx 的 14:59:59
    assert q["quote_time"] == feed.quote("600519.SH")["quote_time"]
    assert q["fresh"] is True                      # 用 tick 的 quote_time 重算
    assert q["atr14"] == 12.5 and q["ma5"] == 1580.0   # 其他字段保留 eltdx 口径
    assert "qmt_age_s" in q


def test_order_quote_falls_back_to_eltdx(monkeypatch) -> None:
    """tick 过期（>5s）→ 不覆盖，src=eltdx。"""
    fixed = {"code": "600519.SH", "last": 1600.0, "bids": [], "asks": [],
             "quote_time": "14:59:59", "fresh": False}
    monkeypatch.setattr(Q, "order_quote", lambda ts_code: dict(fixed))

    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    stale = _tick(last=1688.0)
    stale["time"] = _now_ms() - 30_000             # 30 秒前的 tick（quote_time 会旧）
    feed._on_quote({"600519.SH": stale})
    # 把「收到时刻」也拨到 10 秒前，稳定越过 5 秒覆盖窗口
    with feed._lock:
        feed._quotes["600519.SH"]["ts"] = time.time() - 10.0

    q = feed.order_quote("600519.SH")
    assert q["src"] == "eltdx"
    assert q["last"] == 1600.0                     # 没被覆盖


def test_order_quote_empty_on_bad_code(monkeypatch) -> None:
    """quotes.order_quote 返回 {} → feed 也返回 {}（不造空壳）。"""
    monkeypatch.setattr(Q, "order_quote", lambda ts_code: {})
    feed = make_threadless_feed(FakeXt())
    assert feed.order_quote("bad") == {}


def test_order_quote_swallows_exception(monkeypatch) -> None:
    """eltdx 抛异常 → 返回 {}，不把异常抛进下单链路。"""
    def boom(ts_code):  # noqa: ANN001
        raise RuntimeError("socket dead")

    monkeypatch.setattr(Q, "order_quote", boom)
    feed = make_threadless_feed(FakeXt())
    assert feed.order_quote("600519.SH") == {}


# ---------------------------------------------------------------- 7. 未 start


def test_no_qmt_when_disabled() -> None:
    """不调 start → state=DISABLED，且一次 connect 都不发（不能因为 import 就连接）。"""
    xt = FakeXt()
    feed = make_feed(xt)
    feed.ensure(["600519.SH"])
    st = feed.status()
    assert st["state"] == "DISABLED"
    assert st["connected"] is False
    assert st["tick_count"] == 0
    assert xt.connect_calls == 0
    assert feed.quote("600519.SH") is None


def test_get_feed_singleton_and_reset() -> None:
    """get_feed() 是进程单例；reset_feed() 换新对象。"""
    a = F.get_feed()
    b = F.get_feed()
    assert a is b
    F.reset_feed()
    c = F.get_feed()
    assert c is not a


# ---------------------------------------------------------------- 8. 启动失败不退出


def test_connect_failure_keeps_retrying() -> None:
    """启动重试耗尽 → state=ERROR 但不崩；之后 `_pump_once` 仍会再试（QMT 晚启动）。"""
    xt = FakeXt(fail_connect=1)
    feed = make_feed(xt, retry=1, reconnect_throttle_s=0.0)

    assert feed._connect_with_retry(threading.Event()) is False
    assert feed.status()["state"] == "ERROR"
    assert feed.status()["error"] is not None

    # 8810 常驻：下一次巡检（生产是每 60s）继续重连，QMT 起来后就能连上
    feed._last_connect_mono = 0.0
    assert feed._pump_once() is True
    assert feed.status()["state"] == "CONNECTED"
    assert xt.connect_calls == 2


def test_no_connect_without_codes() -> None:
    """无代码不订阅：连上了也不调 subscribe（QMT 对空列表行为不确定，别试）。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    assert feed._pump_once() is True
    assert xt.seq == 0
    assert feed.status()["subscribed_count"] == 0


def test_stale_stream_triggers_reconnect() -> None:
    """已连接 + 超过 stale_s 没 tick → 断开/重连 + 全量重登记。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt, stale_s=1.0, reconnect_throttle_s=0.0)
    feed.ensure(["600519.SH"])
    feed._pump_once()
    assert feed.status()["subscribed"] == ["600519.SH"]

    # 假装 5 秒没有 tick；把「现在」钉在交易时段内是环境相关的，
    # 所以只在该环境下断言行为（非交易时段本来就不判流，这是设计）。
    if not F._in_session():
        pytest.skip("非交易时段：按任务书 §2.2-6，不判流")
    connects_before = xt.connect_calls
    feed._last_tick_mono = time.monotonic() - 5.0
    feed._last_stale_mono = 0.0
    feed._pump_once()

    assert xt.disconnect_calls >= 1
    assert xt.connect_calls > connects_before      # 重连了一次
    assert feed.status()["reconnect_count"] >= 1
    assert feed.status()["subscribed"] == ["600519.SH"]   # 重连后登记表仍完整


# ---------------------------------------------------------------- 9. 坏数据 / 队列


def test_bad_ticks_are_skipped() -> None:
    """空/非 dict/坏价位不抛，坏档位直接丢（不补 0）。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    feed._on_quote({})                       # 空 batch
    feed._on_quote({"600519.SH": []})        # 空帧
    feed._on_quote({"600519.SH": {"lastPrice": None, "bidPrice": [0.0, -1.0],
                                  "bidVol": [1, 2], "askPrice": None,
                                  "askVol": None, "time": None}})
    st = feed.status()
    assert st["tick_count"] == 0             # last/pre_close 无效的 tick 不落内存（旧 cond-push 同口径）
    assert feed.quote("600519.SH") is None
    feed._on_quote({"600519.SH": _tick(last=1688.0)})
    assert feed.status()["tick_count"] == 1
    q = feed.quote("600519.SH")
    assert q["last"] == 1688.0
    feed._on_quote("not a dict")             # 极端脏数据：不抛


def test_stale_tick_returns_none() -> None:
    """内存里超过 300s 的 tick → quote() 返回 None（宁可降级，也不给旧价）。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    feed._on_quote({"600519.SH": _tick()})
    assert feed.quote("600519.SH") is not None
    with feed._lock:
        feed._quotes["600519.SH"]["ts"] = time.time() - (F.TICK_MAX_AGE_S + 1)
    assert feed.quote("600519.SH") is None


def test_snapshot_skips_missing() -> None:
    """snapshot 只回有 tick 的代码（不补空壳）。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    feed._on_quote({"600519.SH": _tick()})
    snap = feed.snapshot(["600519.SH", "000001.SZ"])
    assert list(snap) == ["600519.SH"]


def test_queue_full_drops_oldest() -> None:
    """队列满 → 丢最旧、保留最新的，且不阻塞回调。"""
    xt = FakeXt()
    feed = make_threadless_feed(xt)
    feed.ensure(["600519.SH"])
    feed._queue = _queue.Queue(maxsize=2)
    for last in (1.0, 2.0, 3.0):
        feed._on_quote({"600519.SH": _tick(last=last)})

    assert feed._queue.qsize() == 2
    assert feed.status()["dropped"] == 1
    feed._drain_once()
    assert feed.quote("600519.SH")["last"] == 3.0      # 最新的那条没丢


# ---------------------------------------------------------------- 10. 契约扩展


def _manifest_data(feed_flag) -> dict:  # noqa: ANN001
    data = {
        "manifest_version": 1,
        "id": "demo",
        "name": "演示插件",
        "version": "0.1.0",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "domain",
        "backend": {"module": "backend", "entry": "register", "timeout_ms": 5000,
                    "permissions": []},
        "frontend": {"module": "frontend.js", "cards": []},
        "data": {"read": [], "write": []},
        "config": {},
        "depends": [],
    }
    if feed_flag is not None:
        data["data"]["feed"] = feed_flag
    return data


def test_manifest_feed_default_false(tmp_path: Path) -> None:
    """data.feed 默认 False；public() 里带上 feed 键。"""
    m = validate_manifest(_manifest_data(None), tmp_path / "demo")
    assert m.feed_read is False
    assert m.public()["data"]["feed"] is False


def test_manifest_feed_true(tmp_path: Path) -> None:
    """显式 true → feed_read True + public 透出。"""
    m = validate_manifest(_manifest_data(True), tmp_path / "demo")
    assert m.feed_read is True
    assert m.public()["data"]["feed"] is True


def test_manifest_feed_must_be_bool(tmp_path: Path) -> None:
    """data.feed 非布尔 → ContractError（不静默当成 False）。"""
    with pytest.raises(ContractError) as excinfo:
        validate_manifest(_manifest_data("yes"), tmp_path / "demo")
    assert "data.feed" in str(excinfo.value)


def test_ctx_feed_permission(tmp_path: Path) -> None:
    """未声明 data.feed → ctx.feed 抛 PermissionError；声明了 → 拿到单例。"""
    denied = PluginContext(validate_manifest(_manifest_data(None), tmp_path / "demo"))
    with pytest.raises(PermissionError):
        _ = denied.feed

    root = tmp_path / "demo"
    allowed = PluginContext(validate_manifest(_manifest_data(True), root))
    assert allowed.feed is F.get_feed()


def test_in_session_calendar() -> None:
    """交易时段判断：工作日 09:30–11:30 / 13:00–15:00，其余为 False。"""
    Mon = datetime(2026, 10, 26, 10, 0)         # 周一 10:00
    assert F._in_session(Mon) is True
    assert F._in_session(datetime(2026, 10, 26, 12, 0)) is False   # 午休
    assert F._in_session(datetime(2026, 10, 26, 14, 30)) is True
    assert F._in_session(datetime(2026, 10, 26, 15, 30)) is False  # 收盘后
    assert F._in_session(datetime(2026, 10, 26, 9, 0)) is False    # 开盘前
    assert F._in_session(datetime(2026, 10, 31, 10, 0)) is False   # 周六
    # fresh 口径（120s）：刚过去的时间为 True，1 小时前为 False
    now = datetime.now()
    assert F._fresh_from_time(now.strftime("%H:%M:%S")) is True
    old = (now - timedelta(hours=1)).strftime("%H:%M:%S")
    assert F._fresh_from_time(old) is False


def test_no_files_written(monkeypatch) -> None:
    """保险丝：feed 模块没有可写文件/连库的入口（静态断言，防止后人加回来）。

    2026-10-26 修正：原来直接查子串 `"open("`，迁到 eltdx 后 `qmt_port_open()` 里也含 `open(`
    —— 探活不是写文件，却被判成违规（假阳性）。改成只认**真正的文件打开**：
    `open(` 前面不能是标识符字符（`_qmt_port_open` / `Path.open` 都不算）。
    """
    import inspect

    src = inspect.getsource(F)
    assert not re.search(r"(?<![\w.])open\s*\(", src), "feed.py 不应出现真正的文件 open()"
    for banned in ("sqlite3", "write_text", "os.replace"):
        assert banned not in src, f"feed.py 不应出现 {banned}"
