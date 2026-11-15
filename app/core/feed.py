"""core 唯一的 QMT 行情订阅（4.1.3a-2 新增）。

**为什么要有这一层**：分时/五档/逐笔走 eltdx 按需 socket（`app.core.quotes`），
但「下单用行情快照」要拿到秒级的盘口更新，只能让 QMT 整推（`subscribe_whole_quote`）。
如果每个插件各自 `import xtquant`，就是 **N 个连接、N 套重连、N 份线程**——行情源一变改 N 处。
所以：全进程一个 `QuoteFeed` 单例，插件只能通过 `ctx.feed` 用（`manifest.data.feed: true`）。

**本文件是 core 里唯一允许出现 `xtquant` / `xtdata` 的文件**（契约 §4.1 的
`find_forbidden_quote_usage()` 静态扫描拦插件，feed 属 core 不受限）。
生产路径**延迟 import**；测试通过 `xtdata_module=` 注入假模块，**绝不真连 58610**。

**线程模型（3 个）**：
1. QMT 回调线程（`xtdata` 自己的线程）→ `_on_quote()`：只解析 + 写内存 + 入队；
   **绝不阻塞、绝不调 listener**（QMT 回调被卡住会拖死整个行情端）。
2. supervisor 线程（daemon）→ `_supervise_loop()`：连接/重连、`ensure()` 的新代码补订阅、
   交易时段 60s 无 tick 判流重连。**启动重试耗尽后仍每 60s 继续试**（QMT 可能晚于 8810 启动）。
3. consumer 线程（daemon）→ `_consume_loop()`：按 `dispatch_interval_s` drain 队列，
   同 code 只留最后一条，组成 batch 派发给 listener；listener 异常吞掉并计数。

**边界**：不写文件、不写库、不下单。行情只读，`order_quote()` 只做
「eltdx 打底 + 5 秒内 QMT 盘口覆盖」，`src` 标 `qmt` / `eltdx`。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, time as _dtime
from typing import Any, Callable, Iterable

from app.core import quotes

logger = logging.getLogger("panel_v2.core.feed")

# ---------------------------------------------------------------- 常量（口径）

# 回调队列上限：QMT 整推一秒可能上百条，消费者慢一拍也不该丢行情；
# 但**必须**有上限——否则行情端抖动 + 消费者卡死会把内存吃光。
QUEUE_MAXSIZE = 20000
# 队列满时丢弃计数上限（仅用于 status 观测，不做限流）
TICK_MAX_AGE_S = 300.0        # `quote()` 超过 5 分钟视为不可用（返回 None）
OVERLAY_WINDOW_S = 5.0        # `order_quote()` 只吃 5 秒内的 QMT tick
FRESH_WINDOW_S = 120          # fresh 口径与旧 8800 一致：0 <= now_sec - quote_sec <= 120
# supervisor 巡检间隔：保证 `ensure()` 的新代码 ≤1s 内补订阅
SUPERVISOR_INTERVAL_S = 1.0
# 「订阅清单」重读间隔（4.1.3b）：`set_interest_provider(fn)` 注入的 fn 每 15 秒取一次，
# 持仓/条件单/挂单一变就能在 15s 内被 `ensure()` 增量补订阅。
INTEREST_INTERVAL_S = 15.0
# 五档档数（与 quotes.QUOTE5_MAX_LEVELS 同口径，QMT 也只推 5 档）
MAX_LEVELS = 5


# ---------------------------------------------------------------- 交易时段


def _in_session(now: datetime | None = None) -> bool:
    """是否在连续竞价时段（工作日 09:30–11:30 / 13:00–15:00）。

    刻意不引入交易历模块：这里只用来**判断要不要做断流自愈**，
    节假日多判几次重连无害（重连本身有 `reconnect_throttle_s` 节流）；
    反过来漏判就会在盘中把断流当正常，那才是真问题。
    """
    n = now or datetime.now()
    if n.weekday() >= 5:          # 5=周六 6=周日
        return False
    t = n.time()
    return (_dtime(9, 30) <= t <= _dtime(11, 30)) or (_dtime(13, 0) <= t <= _dtime(15, 0))


# ---------------------------------------------------------------- 小工具


def _num(v: Any) -> float | None:
    """转 float；非有限数（NaN/inf）或不可转 → None（不补零、不编造）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) == float("inf"):   # NaN / inf
        return None
    return f


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fmt_tick_time(v: Any) -> str | None:
    """QMT `time`（**毫秒**时间戳）→ `HH:MM:SS`；非法 → None。

    兼容两种上游：毫秒（>1e11，QMT 原生）与秒（旧端个别接口）。
    """
    ts = _num(v)
    if ts is None or ts <= 0:
        return None
    if ts > 1e11:                 # 毫秒
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def _fresh_from_time(quote_time: Any, window_s: int = FRESH_WINDOW_S) -> bool:
    """`quote_time`（HH:MM:SS）是否在当前时刻前 window_s 秒内。

    优先复用 `quotes._fresh_from_time`（旧 8800 逐字移植，跨零点判 False 的口径一致）；
    拿不到（被改名/缺失）时走本文件的等价实现，**不因缺函数而判 True**。
    """
    fn = getattr(quotes, "_fresh_from_time", None)
    if callable(fn):
        try:
            return bool(fn(quote_time))
        except Exception:  # noqa: BLE001 - 兜底走下面的等价实现
            pass
    try:
        s = str(quote_time or "")
        if len(s) < 5:
            return False
        h, m = int(s[0:2]), int(s[3:5])
        sec = int(s[6:8]) if len(s) >= 8 else 0
        qsec = h * 3600 + m * 60 + sec
        n = datetime.now()
        nsec = n.hour * 3600 + n.minute * 60 + n.second
        return 0 <= nsec - qsec <= window_s
    except (TypeError, ValueError):
        return False


def _levels(prices: Any, vols: Any) -> list[dict[str, Any]]:
    """`bidPrice`/`bidVol`（或 ask 同理）→ `[{price, vol}]`，只留 price>0 的前 5 档。

    `vol` 保持 QMT 原样（单位：手），`price` round(3)；
    价格非正 / 字段缺失一律跳过该档——**留空比填 0 安全**（0 会被当成有效档位）。
    """
    out: list[dict[str, Any]] = []
    plist = list(prices) if isinstance(prices, (list, tuple)) else []
    vlist = list(vols) if isinstance(vols, (list, tuple)) else []
    for i, raw in enumerate(plist[:MAX_LEVELS]):
        price = _num(raw)
        if price is None or price <= 0:
            continue
        vol = _int(vlist[i]) if i < len(vlist) else None
        out.append({"price": round(price, 3), "vol": vol})
    return out


# ---------------------------------------------------------------- QuoteFeed


class QuoteFeed:
    """QMT 整推行情：单例订阅 + 内存快照 + 队列派发。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 58610,
        xtdata_module: Any = None,
        retry: int = 3,
        retry_interval_s: float = 30.0,
        stale_s: float = 60.0,
        reconnect_throttle_s: float = 60.0,
        dispatch_interval_s: float = 0.05,
    ) -> None:
        self._host = host
        self._port = int(port)
        # 测试注入的假模块；None 时生产路径延迟 import xtquant.xtdata
        self._xt_module = xtdata_module
        self._retry = max(1, int(retry))       # 启动阶段最多尝试次数（0 也至少试 1 次）
        self._retry_interval_s = float(retry_interval_s)
        self._stale_s = float(stale_s)
        self._reconnect_throttle_s = float(reconnect_throttle_s)
        self._dispatch_interval_s = float(dispatch_interval_s)
        # supervisor 巡检间隔（任务书要求 ≤1s 补订阅；测试可调小加速）
        self._patrol_s = SUPERVISOR_INTERVAL_S

        self._lock = threading.Lock()          # 保护 _quotes / _codes / _subscribed / 计数
        self._listener_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)

        self._quotes: dict[str, dict[str, Any]] = {}   # code → tick（内存快照，唯一权威）
        self._codes: set[str] = set()                  # 已登记（ensure）的标的
        self._subscribed: set[str] = set()             # 已成功订阅的标的
        self._listeners: list[Callable[[dict[str, dict]], None]] = []

        self._state = "DISABLED"      # DISABLED|CONNECTING|CONNECTED|ERROR|STOPPED
        self._connected = False
        self._seq = 0                 # 最近一次 subscribe_whole_quote 返回的订阅号（兼容/展示）
        self._seqs: list[int] = []    # 全部活跃订阅号（增量订阅会有多个，退订必须逐个退）
        self._error: str | None = None
        self._ever_connected = False
        self._reconnects = 0
        self._tick_count = 0
        self._dropped = 0
        self._listener_errors = 0
        self._last_tick_mono: float | None = None
        self._last_tick_at: str | None = None
        self._last_connect_mono = 0.0
        self._last_stale_mono = 0.0
        # 4.1.3b：订阅清单提供方（`fn() -> Iterable[str]`）与上次取清单的时刻。
        # supervisor 每 INTEREST_INTERVAL_S 秒取一次，取到就 `ensure()`（**只增不减**）。
        self._interest_fn: Callable[[], Iterable[str]] | None = None
        self._last_interest_mono = 0.0
        self._interest_errors = 0

        self._started = False
        self._stop_evt: threading.Event | None = None
        self._threads: list[threading.Thread] = []
        # 2026-10-26：行情轮询线程（替代原 QMT 推送订阅），只用启一次
        self._poll_started = False

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """启动 supervisor + consumer 两个 daemon 线程；**幂等**，可被 create_app 多次调用。

        连接本身在 supervisor 里做，所以 start() 立即返回：
        即使 QMT 没起（启动重试 3×30s），8810 也不会被阻塞。
        """
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop_evt = threading.Event()
            self._state = "CONNECTING"
            self._error = None
        evt = self._stop_evt
        sup = threading.Thread(target=self._supervise_loop, args=(evt,),
                               name="panel-v2-feed-supervisor", daemon=True)
        con = threading.Thread(target=self._consume_loop, args=(evt,),
                               name="panel-v2-feed-dispatch", daemon=True)
        self._threads = [sup, con]
        sup.start()
        con.start()
        logger.info("QuoteFeed 启动（QMT %s:%s）", self._host, self._port)

    def stop(self) -> None:
        """停止：set stop event → unsubscribe → disconnect → 等线程退出。**幂等**。

        停机路径**不允许抛**：shutdown 时抛异常会把整个进程退出码搞脏。
        停止后 QMT 回调即使还在路上也不会再进 listener（`_on_quote` / `_drain_once` 双保险）。
        """
        with self._lock:
            if not self._started:
                return                  # 从未启动 → 保持 DISABLED，幂等
            self._started = False
            evt = self._stop_evt
        if evt is not None:
            evt.set()
        self._unsubscribe_quiet()
        self._disconnect_quiet()
        with self._lock:
            self._connected = False
            self._subscribed = set()
            self._seq = 0
            self._seqs = []
        # 等两个线程退出：supervisor 卡在 1s wait 上，会被 stop event 立刻唤醒
        for th in self._threads:
            if th is threading.current_thread():
                continue
            try:
                th.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
        self._threads = []
        with self._lock:
            self._state = "STOPPED"
        logger.info("QuoteFeed 已停止")

    # ------------------------------------------------------------ 订阅登记

    def ensure(self, codes: Iterable[str]) -> None:
        """增量登记订阅标的（**只增不减**）；新代码由 supervisor ≤1s 内补订阅。

        入参统一成 QMT 口径 `600519.SH`（`sh600519` 也能接受，这里归一）；
        无法归一的原样登记——订阅失败会在 status.error 里暴露，**不静默丢**。
        """
        if isinstance(codes, str):
            codes = [codes]
        add: list[str] = []
        with self._lock:
            for raw in codes or ():
                key = str(raw or "").strip()
                if not key:
                    continue
                ts = quotes.to_ts_code(key) or key
                if ts not in self._codes:
                    self._codes.add(ts)
                    add.append(ts)
        if add:
            logger.debug("QuoteFeed 登记待订阅: %s", sorted(add))

    def add_listener(self, fn: Callable[[dict[str, dict]], None]) -> None:
        """注册派发回调 `fn(batch)`，batch = {code: tick}（同 code 已是最后一条）。"""
        if not callable(fn):
            return
        with self._listener_lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def set_interest_provider(self, fn: Callable[[], Iterable[str]] | None) -> None:
        """注入「该订阅哪些代码」的提供方（4.1.3b）：`fn() -> Iterable[str]`。

        supervisor 每 `INTEREST_INTERVAL_S`（15s）调一次，把返回值交给 `ensure()`
        （**只增不减**，见 `ensure` 注释）。接上之后，持仓/条件单/挂单发生变化，
        新增标的会在 15 秒内被增量订阅，不需要重启进程。

        **异常一律吞掉**：订阅清单来自纸面账本，账本读失败/业务异常都不允许把
        supervisor 线程打死——行情订阅停了比少订阅几只票严重得多（错误只计数，
        在 `status()['interest_errors']` 里可见）。`fn=None` 表示撤掉提供方。
        """
        with self._lock:
            self._interest_fn = fn if callable(fn) else None
            # 立刻允许本轮取一次清单（否则注入后要先等满 15s 才开始订阅）
            self._last_interest_mono = 0.0

    def _refresh_interest(self) -> None:
        """取一次订阅清单并 `ensure()`；provider 异常只计数，绝不抛回 supervisor。"""
        with self._lock:
            fn = self._interest_fn
        if fn is None:
            return
        try:
            codes = fn()
        except Exception:  # noqa: BLE001 - 订阅清单故障不得影响行情订阅
            with self._lock:
                self._interest_errors += 1
            logger.warning("QuoteFeed interest provider 异常（已计数，不中断）", exc_info=True)
            return
        if isinstance(codes, str):      # 容错：provider 只返回一个码
            codes = [codes]
        try:
            self.ensure(codes or ())
        except Exception:  # noqa: BLE001 - ensure 内部只操作内存，理论上不抛
            with self._lock:
                self._interest_errors += 1
            logger.warning("QuoteFeed interest ensure 异常（已计数，不中断）", exc_info=True)

    # ------------------------------------------------------------ 读快照

    def _keys(self, code: str) -> list[str]:
        """一条代码的可能键：原样 + `600519.SH` + 小写前缀形式（订阅/查询混用都能命中）。"""
        raw = str(code or "").strip()
        if not raw:
            return []
        out = [raw]
        ts = quotes.to_ts_code(raw)
        for cand in (ts, raw.upper(), raw.lower()):
            if cand and cand not in out:
                out.append(cand)
        return out

    def quote(self, code: str) -> dict | None:
        """最近一条 QMT tick（**副本**）+ 现算 `fresh` / `age_s`；无或太旧 → None。

        太旧（> 300s）视为不可用：宁可让调用方降级到 eltdx，也不要把收盘前的报价
        当实时价去算委托价。
        """
        tick = None
        with self._lock:
            for key in self._keys(code):
                hit = self._quotes.get(key)
                if hit is not None:
                    tick = hit
                    break
        if tick is None:
            return None
        age = time.time() - float(tick.get("ts") or 0.0)
        if age > TICK_MAX_AGE_S:
            return None
        out = dict(tick)
        out["bids"] = [dict(x) for x in tick.get("bids") or []]
        out["asks"] = [dict(x) for x in tick.get("asks") or []]
        out["age_s"] = round(age, 3)
        out["fresh"] = _fresh_from_time(out.get("quote_time"))
        return out

    def snapshot(self, codes: Iterable[str]) -> dict[str, dict]:
        """批量快照：{输入代码: quote}；取不到的代码**不出现**（不补空壳）。"""
        if isinstance(codes, str):
            codes = [codes]
        out: dict[str, dict] = {}
        for code in codes or ():
            q = self.quote(code)
            if q is not None:
                out[str(code)] = q
        return out

    # ------------------------------------------------------------ 下单行情

    def order_quote(self, ts_code: str) -> dict[str, Any]:
        """旧 8800 `_order_quote` 同口径：eltdx 打底 + **QMT 5 秒内**盘口覆盖。

        覆盖范围严格按任务书 §3.4：`last` / `bids` / `asks` / `quote_time`，
        并用 tick 的 `quote_time` 重算 `fresh`（120s）；`src` 标 `qmt` / `eltdx`。
        `change` / `change_pct` / `stats` 仍按 eltdx 骨架口径（本任务书未要求覆盖）。
        任何异常 → `{}`（调用方按无行情处理，**不抛**）。
        """
        try:
            base = quotes.order_quote(ts_code)
        except Exception as exc:  # noqa: BLE001 - 下单链路不允许因行情抛
            logger.warning("order_quote 取 eltdx 底稿失败: %s: %s", type(exc).__name__, exc)
            return {}
        if not isinstance(base, dict) or not base:
            return {}          # 代码无法归一 / eltdx 全空：沿用 quotes 的 {} 语义
        qm = self.quote(ts_code)
        if qm is not None and (time.time() - float(qm.get("ts") or 0.0)) <= OVERLAY_WINDOW_S:
            if qm.get("last") is not None:
                base["last"] = qm["last"]
            if qm.get("bids"):
                base["bids"] = [dict(x) for x in qm["bids"]]
            if qm.get("asks"):
                base["asks"] = [dict(x) for x in qm["asks"]]
            if qm.get("quote_time"):
                base["quote_time"] = qm["quote_time"]
            base["fresh"] = _fresh_from_time(qm.get("quote_time"))
            base["src"] = "qmt"
            base["qmt_age_s"] = round(time.time() - float(qm.get("ts") or 0.0), 3)
        else:
            base["src"] = "eltdx"
        return base

    # ------------------------------------------------------------ 状态

    def status(self) -> dict[str, Any]:
        """对外状态（健康检查 / `/_health` 用）；键与任务书 §2.2-7 对齐。"""
        now = time.monotonic()
        with self._lock:
            state = self._state
            connected = bool(self._connected)
            subs = sorted(self._subscribed)
            codes = sorted(self._codes)
            tick_count = self._tick_count
            last_mono = self._last_tick_mono
            last_str = self._last_tick_at
            recon = self._reconnects
            err = self._error
            listener_errors = self._listener_errors
            dropped = self._dropped
            interest_errors = self._interest_errors
        age = None if last_mono is None else round(max(0.0, now - last_mono), 1)
        return {
            "state": state,
            "connected": connected,
            "subscribed": subs,
            "subscribed_count": len(subs),
            "codes": codes,
            "codes_count": len(codes),
            "tick_count": tick_count,
            "last_tick_at": last_str,
            "last_tick_age_s": age,
            "reconnect_count": recon,
            "error": err,
            "queue_len": self._queue.qsize(),
            "dropped": dropped,
            "listener_errors": listener_errors,
            # 4.1.3b：订阅清单提供方是否接上 / 故障计数（不接 = 只依赖 ensure() 显式登记）
            "interest_provider": self._interest_fn is not None,
            "interest_errors": interest_errors,
            "host": f"{self._host}:{self._port}",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ------------------------------------------------------------ QMT 回调

    def _on_quote(self, datas: Any) -> None:
        """QMT 整推回调（**跑在 xtdata 自己的线程**）：只解析 + 写内存 + 入队。

        三条铁律：不调 listener、不取锁等慢操作以外的东西、不抛。
        """
        evt = self._stop_evt
        if evt is not None and evt.is_set():
            return
        if not self._connected:
            return                       # stop() / 重连中：旧回调一律丢弃
        if not isinstance(datas, dict):
            return
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        for raw_code, payload in datas.items():
            code = str(raw_code or "").strip()
            if not code:
                continue
            tick = self._parse_tick(code, payload, now)
            if tick is None:
                continue
            with self._lock:
                self._quotes[code] = tick
                self._tick_count += 1
                self._last_tick_mono = time.monotonic()
                self._last_tick_at = stamp
            self._push(tick)

    def _parse_tick(self, code: str, payload: Any, now: float) -> dict[str, Any] | None:
        """单条 tick → `{code,last,pre_close,bids,asks,quote_time,ts,fresh}`；坏数据 → None。

        QMT 的 `datas` 形如 `{code: [tick, ...]}`（一批多帧），也兼容单帧 dict
        （测试/老版本接口）。
        """
        row: Any = payload
        if isinstance(payload, (list, tuple)):
            if not payload:
                return None
            row = payload[-1]            # 末帧最新
        if row is None:
            return None
        if not isinstance(row, dict):
            # 上游换成对象：按已知字段做一次浅转换，取不到的留 None
            row = {k: getattr(row, k, None) for k in
                   ("lastPrice", "lastClose", "bidPrice", "bidVol",
                    "askPrice", "askVol", "time")}
        quote_time = _fmt_tick_time(row.get("time"))
        last = _num(row.get("lastPrice"))
        pre_close = _num(row.get("lastClose"))
        # 旧 8800 cond-push 同口径：last/pre_close 缺一或 <=0 的 tick 直接丢弃，
        # 否则 order_quote 会把 eltdx 的好价覆盖成 0。
        if not last or last <= 0 or not pre_close or pre_close <= 0:
            return None
        return {
            "code": code,
            "last": last,
            "pre_close": pre_close,
            "bids": _levels(row.get("bidPrice"), row.get("bidVol")),
            "asks": _levels(row.get("askPrice"), row.get("askVol")),
            "quote_time": quote_time,
            "ts": now,
            "fresh": _fresh_from_time(quote_time),
        }

    def _push(self, tick: dict[str, Any]) -> None:
        """入队；**满了丢最旧**（get 一条再 put），绝不阻塞 QMT 回调。"""
        try:
            self._queue.put_nowait(tick)
            return
        except queue.Full:
            pass
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            self._dropped += 1
        try:
            self._queue.put_nowait(tick)
        except queue.Full:
            pass

    # ------------------------------------------------------------ 消费者

    def _consume_loop(self, evt: threading.Event) -> None:
        while not evt.is_set():
            try:
                self._drain_once()
            except Exception:  # noqa: BLE001 - 消费者不许死
                logger.exception("QuoteFeed 派发异常")
            evt.wait(self._dispatch_interval_s)

    def _drain_once(self) -> int:
        """drain 一次队列并派发（**同步方法，测试用**）；返回派发的标的数。

        同 code 只留**最后一条**：盘口一秒变几十次，插件只关心最终状态，
        逐条派发会让 listener 被无意义地打爆。
        """
        evt = self._stop_evt
        if evt is not None and evt.is_set():
            return 0                     # stop() 后不得再回调 listener
        batch: dict[str, dict[str, Any]] = {}
        while True:
            try:
                tick = self._queue.get_nowait()
            except queue.Empty:
                break
            batch[str(tick.get("code") or "")] = tick
        batch.pop("", None)
        if not batch:
            return 0
        with self._listener_lock:
            listeners = list(self._listeners)
        for fn in listeners:
            if evt is not None and evt.is_set():
                break
            try:
                fn(batch)
            except Exception:  # noqa: BLE001 - 单个 listener 故障不影响其他
                with self._lock:
                    self._listener_errors += 1
                logger.warning("QuoteFeed listener 异常（已计数，不中断）", exc_info=True)
        return len(batch)

    # ------------------------------------------------------------ supervisor

    def _supervise_loop(self, evt: threading.Event) -> None:
        """连接（带启动退避）→ 每 `_patrol_s` 巡检一次：补订阅 / 重连 / 断流自愈。"""
        self._connect_with_retry(evt)
        while not evt.is_set():
            try:
                self._pump_once()
            except Exception:  # noqa: BLE001 - supervisor 不许死
                logger.exception("QuoteFeed supervisor 异常")
            evt.wait(self._patrol_s)

    def _pump_once(self) -> bool:
        """巡检一次（**同步方法，测试用**）；返回是否处于已连接状态。

        做四件事：
        0. 每 `INTEREST_INTERVAL_S` 秒重读一次订阅清单（`set_interest_provider`），
           把新增标的登记为待订阅——**放在连接判断之前**：QMT 没起时也要把清单收进
           `_codes`，否则连上的那一刻只能订阅上一次的旧清单；
        1. 未连接 → 到了节流窗口就再试一次（启动重试耗尽后**永不再放弃**，
           因为 QMT 常常晚于 8810 启动，任务书 §2.2-1）；
        2. 已连接 → `ensure()` 新登记的代码补订阅；
        3. 已连接 + 交易时段 + 超过 `stale_s` 没 tick → unsubscribe/disconnect/connect/全量重订阅。
        """
        now = time.monotonic()
        # 0. 订阅清单定时重读（paper 服务的 wanted_codes()：持仓 + ACTIVE 条件单 + 挂单）
        if self._interest_fn is not None and (now - self._last_interest_mono) >= INTEREST_INTERVAL_S:
            self._last_interest_mono = now
            self._refresh_interest()
        with self._lock:
            connected = self._connected
            codes = set(self._codes)
            subscribed = set(self._subscribed)
            last_tick = self._last_tick_mono
        if not connected:
            if now - self._last_connect_mono >= self._reconnect_throttle_s:
                self._connect_once()
            return self._connected
        # 2. 增量补订阅
        if codes and codes != subscribed:
            self._subscribe_all()
        # 3. 断流自愈（只看交易时段；非交易时段没有 tick 是正常的）
        if _in_session():
            ref = last_tick if last_tick is not None else self._last_connect_mono
            if ref and (now - ref) > self._stale_s and (now - self._last_stale_mono) >= self._reconnect_throttle_s:
                self._last_stale_mono = now
                logger.warning("QuoteFeed 交易时段 %.0fs 无 tick，重连", now - ref)
                self._hard_reconnect()
        return self._connected

    # ------------------------------------------------------------ 连接/订阅

    def _xt(self) -> Any:
        """取行情模块。

        2026-10-26 MIGRATED：QMT `xtdata` → eltdx adapter（同名同签名）。
        保留本方法与返回对象形状：测试通过 `self._xt_module` 注入假模块，契约不变。
        延迟返回（import 期绝不连网）的语义也保留：adapter 首次调用才建连。
        """
        if self._xt_module is not None:
            return self._xt_module
        try:
            import eltdx_adapter as _adapter  # noqa: PLC0415 - 生产路径延迟引入
        except Exception as exc:  # noqa: BLE001 - 环境缺依赖：记错误、不抛
            self._set_error(f"行情 adapter 不可用: {type(exc).__name__}: {exc}")
            return None
        self._xt_module = _adapter
        return _adapter

    def _set_error(self, text: str) -> None:
        with self._lock:
            self._error = text
            self._state = "ERROR"

    def _connect_with_retry(self, evt: threading.Event) -> bool:
        """启动阶段：最多 `retry` 次、间隔 `retry_interval_s` 的退避连接。

        全部失败**不退出进程**，state=ERROR，交给 supervisor 继续每 60s 重试。
        启动中收到 stop → 立刻返回。
        """
        for attempt in range(1, self._retry + 1):
            if evt.is_set():
                return False
            if self._connect_once():
                return True
            if attempt < self._retry:
                evt.wait(self._retry_interval_s)
        logger.warning("QuoteFeed 启动连接失败（%d 次），转入每 %.0fs 重连",
                       self._retry, self._reconnect_throttle_s)
        return False

    def _connect_once(self) -> bool:
        """单次探活；成功即登记已入码表并（首次）启行情轮询线程。失败 → ERROR（不抛）。"""
        with self._lock:
            self._state = "CONNECTING"
        self._last_connect_mono = time.monotonic()
        # 2026-10-26：QMT `connect()` → eltdx 真实探活。
        # **必须走 `self._xt()`**：探活/取数/断开三处都从注入模块取，测试才能在
        # 不改代码的前提下把行情源整个替掉。上一版直接 `from eltdx_adapter import qmt_port_open`，
        # 接缝被绕过了 —— 6 条 feed 单测因为“探活看不到假模块”而红。
        xt = self._xt()
        if xt is None:
            return False                       # `_xt()` 已写 ERROR
        try:
            probe = getattr(xt, "qmt_port_open", None)
            if callable(probe):
                ok = probe()
            else:
                ok = xt.connect()              # 旧形状（只有 connect）
            if not ok:
                raise RuntimeError("行情源不可达")
        except Exception as exc:  # noqa: BLE001 - 行情源没起是常态，记错误继续等
            self._set_error(f"探活失败: {type(exc).__name__}: {exc}")
            return False
        with self._lock:
            self._connected = True
            self._error = None
            self._state = "CONNECTED"
            if self._ever_connected:
                self._reconnects += 1
            self._ever_connected = True
            need_start = not self._poll_started
            self._poll_started = True
        logger.info("QuoteFeed 行情源就绪（eltdx）")
        self._subscribe_all()
        if need_start:
            threading.Thread(target=self._poll_loop, daemon=True,
                             name="quote-feed-poll").start()
        return True

    def _subscribe_all(self) -> bool:
        """登记待轮询代码（MIGRATED 2026-10-26）。

        原实现调 xtdata `subscribe_whole_quote`（推送式）；QMT 关闭后改为**只登记码表**，
        由 `_poll_loop` 统一拉取后派给 `_on_quote`（派发/撮合/条件单逻辑一行未改）。

        保留原设计的关键约束（仍适用）：只处理「新增」代码，不重复登记 ——
        原注释里的教训是「重复订阅会导致同一 tick 被派发 N 次 → 撮合重复评估」；
        轮询下同样成立（重复派发会重复评估），所以 `codes - subscribed` 保留。
        """
        with self._lock:
            codes = sorted(self._codes)
            already = set(self._subscribed)
        new_codes = [c for c in codes if c not in already]
        if not new_codes:
            return True                 # 无新增不订阅（任务书 §2.2-2）
        with self._lock:
            self._subscribed |= set(new_codes)
            self._error = None
            if self._connected:
                self._state = "CONNECTED"
        logger.info("QuoteFeed 登记 %d 只（累计 %d）", len(new_codes), len(self._subscribed))
        return True

    def _poll_once(self) -> bool:
        """拉一轮行情并派发（**同步方法，测试用**）。返回本轮是否有数据。

        与 `_poll_loop` 拆开是为了可测：线程里的循环只能靠 sleep 节奏碰运气，
        而派发/合并/撑合语义需要确定性驱动（同 `_pump_once` 的做法）。
        """
        with self._lock:
            codes = sorted(self._subscribed)
        if not codes:
            return False
        xt = self._xt()
        if xt is None:
            return False
        datas = xt.get_full_tick(codes)
        if not datas:
            return False
        self._on_quote(datas)
        return True

    def _poll_loop(self) -> None:
        """行情轮询线程（MIGRATED 2026-10-26）：1s 一轮，代替推送订阅。"""
        while True:
            evt = self._stop_evt
            if evt is not None and evt.is_set():
                break
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001 - 单轮失败不杀线程
                self._set_error(f"行情轮询: {type(exc).__name__}: {exc}")
            if evt is not None:
                evt.wait(1.0)
            else:
                time.sleep(1.0)
            if evt is not None and evt.is_set():
                break

    def _unsubscribe_quiet(self) -> None:
        """停机清理（MIGRATED 2026-10-26）：轮询无订阅句柄，只需清空登记。"""
        with self._lock:
            self._seqs = []
            self._seq = 0

    def _disconnect_quiet(self) -> None:
        """断开当前行情连接（MIGRATED 2026-10-26：走 `self._xt()` 接缝）。"""
        xt = self._xt()
        fn = getattr(xt, "disconnect", None) if xt is not None else None
        if not callable(fn):
            return
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass

    def _hard_reconnect(self) -> bool:
        """断流自愈（MIGRATED 2026-10-26）：清登记 → 断连接 → 重建 → 拉一轮验证。"""
        self._unsubscribe_quiet()
        self._disconnect_quiet()
        with self._lock:
            self._connected = False
            self._subscribed = set()
            self._seq = 0
            self._seqs = []
        return self._connect_once()


# ---------------------------------------------------------------- 单例


_FEED_LOCK = threading.Lock()
_FEED: QuoteFeed | None = None


def get_feed() -> QuoteFeed:
    """进程单例（`create_app` / `ctx.feed` / 编辑器都用同一个）。"""
    global _FEED
    with _FEED_LOCK:
        if _FEED is None:
            _FEED = QuoteFeed()
        return _FEED


def reset_feed() -> None:
    """测试用：停掉并丢弃单例（下一次 `get_feed()` 重新建）。"""
    global _FEED
    with _FEED_LOCK:
        feed = _FEED
        _FEED = None
    if feed is not None:
        try:
            feed.stop()
        except Exception:  # noqa: BLE001 - 清理路径不抛
            pass
