# 任务书 4.1.3a · core 行情 feed + 下单行情快照

> 执行者：DSHana｜主会话验收｜2026-10-23
> **你没有 shell，不要运行 pytest / python / curl。只写代码 + 自检语法，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`

---

## 0. 先读

- `docs/paper_trading_architecture.md`（§2、§4、§5）
- `app/core/quotes.py`（现有行情门面，重点 `call` / `depth` / `stock_aux` / `daily_bars`）
- `app/core/contract.py`（`PluginContext` / manifest 解析）
- `app/core/main.py`（`create_app` 末尾，当前无 lifespan）
- `run.ps1`
- `_vendor_src/quote5_reference.py`（旧 8800 的 `_order_quote` 相关纯函数与 `_build_holdings_view` 参考）

---

## 1. 目标

新增 **core 唯一的 QMT 行情订阅**，并把「下单用行情快照」做成 `quotes.order_quote()`。
**本任务不写 paper 插件、不写交易服务**（4.1.3b 做）。

### 交付文件

```
app/core/feed.py            # 新增
app/core/quotes.py          # 追加 order_quote() / order_metrics() / build_quote5_payload()
app/core/contract.py        # 扩展 manifest.data.feed + PluginContext.feed
app/core/main.py            # 启动/停止 feed（环境变量开关）
app/tests/test_feed.py      # 新增（假 xtquant，零真实 QMT）
run.ps1                     # 增加 $env:PANEL_V2_FEED = '1'
```

---

## 2. `app/core/feed.py` 设计

### 2.1 类接口

```python
class QuoteFeed:
    def __init__(self, host: str = "127.0.0.1", port: int = 58610,
                 xtdata_module=None, retry: int = 3, retry_interval_s: float = 30.0,
                 stale_s: float = 60.0, reconnect_throttle_s: float = 60.0,
                 dispatch_interval_s: float = 0.05): ...

    def start(self) -> None: ...        # 幂等；起 supervisor + consumer 两个 daemon 线程
    def stop(self) -> None: ...         # 幂等；set stop event + unsubscribe + disconnect
    def ensure(self, codes: Iterable[str]) -> None: ...  # 增量登记订阅标的
    def quote(self, code: str) -> dict | None: ...       # 最近一条（含 fresh 计算）
    def snapshot(self, codes: Iterable[str]) -> dict[str, dict]: ...
    def add_listener(self, fn: Callable[[dict[str, dict]], None]) -> None: ...
    def status(self) -> dict: ...

def get_feed() -> QuoteFeed: ...   # 进程单例
def reset_feed() -> None: ...      # 测试用
```

### 2.2 行为要求

1. **连接**：`xtdata.connect(ip=host, port=port)`。启动最多 `retry` 次，间隔 `retry_interval_s`；失败不退出进程，`status.state="ERROR"`，`status.error` 写异常文本。**启动重试耗尽后，supervisor 仍每 60 秒尝试一次重连**（8810 常驻，QMT 可能晚于 8810 启动；不能只试 3 次就永久 ERROR）。
2. **订阅**：`subscribe_whole_quote(sorted(codes), self._on_quote)`。`ensure()` 只增不减；新代码在下一次 supervisor 巡检（≤1s）时补订阅。无代码时不订阅。
3. **回调只做内存**：
   - 解析 `datas` 为 `{code: tick}`；
   - 每条 tick 转成 `{last, pre_close, bids:[{price,vol}], asks:[...], quote_time, ts, fresh}`；
   - `vol` 单位保持 QMT 原样（手），`price` round(3)；
   - 写 `_quotes[code]`（`threading.Lock`）；
   - `put_nowait` 到 `queue.Queue(maxsize=20000)`；满了丢最旧（`get_nowait` 再 put），绝不阻塞 QMT 回调。
4. **消费者线程**：每 `dispatch_interval_s` drain 队列，同 code 只留最后一条，组成 batch 调所有 listener；listener 异常吞掉并计数，不影响后续。
5. **fresh 口径**（与旧 8800 一致）：
   - `quote_time` 是 `HH:MM:SS`，`fresh = 0 <= now_sec - quote_sec <= 120`；
   - `quote()` 返回副本；若 tick age > 300s，返回 None（太旧视为不可用）。
6. **断流自愈**：supervisor 每 1s 检查；未连接时每 60s 重连一次（不区分交易时段，QMT 启动即可连）；已连接时仅在交易时段（用 `app.core.trading_calendar` 或简单工作日+09:30–11:30/13:00–15:00，若没有现成模块就内置一个 `_in_session()`）且 `now-last_tick > stale_s` → `unsubscribe_quote(seq)` → `disconnect()` → `connect()` → 全量重订阅；节流 `reconnect_throttle_s`。非交易时段不判流。
7. **status()**：
```json
{"state":"DISABLED|CONNECTING|CONNECTED|ERROR|STOPPED",
 "connected":true,"subscribed":["600519.SH"],"subscribed_count":1,
 "tick_count":123,"last_tick_at":"2026-10-23 20:00:00","last_tick_age_s":2.3,
 "reconnect_count":0,"error":null,"updated_at":"..."}
```
8. **线程安全**：`stop()` 后不得再回调 listener；`start()` 重复调用无副作用。
9. **测试注入**：`xtdata_module` 传入假模块；生产不传时延迟 `import xtquant.xtdata`。这是**唯一允许出现 `xtquant` 字符串的模块之一**（另一个是 quotes.py 的 eltdx 部分不涉及 xtquant）。

### 2.3 `_on_quote` 字段映射

QMT tick 常见字段（旧 `server.py:11157` 段）：
- `lastPrice` → last
- `lastClose` → pre_close
- `bidPrice`/`bidVol` → bids（前 5 档，price>0）
- `askPrice`/`askVol` → asks（前 5 档）
- `time`（毫秒时间戳）→ `quote_time = HH:MM:SS`
- 空/异常字段跳过，不抛

---

## 3. `app/core/quotes.py` 追加

### 3.1 `build_quote5_payload(ts_code, rec, ticks=None, aux=None, asof=None)`

从 `_vendor_src/quote5_reference.py` **逐字移植**（纯函数），放在 `quotes.py` 里。需要同时移植它依赖的 `_fmt_hhmmss` / `_quote_level` / `_elapsed_minutes` / `_finite`（可与现有 `quotes.py` 内已有函数合并，不要重复定义冲突）。

### 3.2 `order_metrics(glb: str) -> dict`

移植 `_vendor_src/quote5_reference.py` 末尾的 `compute_atr14` / `compute_ma5`，用 `daily_bars(glb, 40)`：
```python
def order_metrics(glb: str) -> dict[str, float | None]:
    """返回 {"atr14": ..., "ma5": ...}；取不到 → None。缓存 600s。"""
```
日线对象字段：`high_price` / `low_price` / `close_price`（eltdx bars）。若不是这些字段，先 `getattr(b, "high", None)` 兜底。

### 3.3 `order_quote(ts_code: str) -> dict`

对应旧 8800 `_order_quote()` 的 eltdx 半段：
1. `glb = normalize(ts_code)`；失败返回 `{}`。
2. `rec = depth(glb)`；`aux = stock_aux(glb)`；`ticks = today_ticks(glb)`（仅用于 payload 的 `ticks` 字段，可传空以减少耗时——但保持旧形状）。
3. `q = build_quote5_payload(ts_code, rec, ticks=ticks, aux=aux)`。
4. `q["fresh"] = _fresh_from_time(q["quote_time"])`（120s 口径，函数移植）。
5. `q.update(order_metrics(glb))`。
6. 返回 q（不 overlay QMT；overlay 在 feed 里做）。

### 3.4 `QuoteFeed.order_quote(ts_code)`

在 `feed.py` 里加：
```python
def order_quote(self, ts_code: str) -> dict:
    """旧 8800 `_order_quote` 同口径：eltdx 打底 + QMT 5 秒内盘口覆盖。"""
```
- 调 `app.core.quotes.order_quote(ts_code)`；
- `qm = self.quote(ts_code)`；若 `qm` 且 `now - qm["ts"] <= 5`：
  - 覆盖 `last`、`bids`、`asks`、`quote_time`；
  - 用 tick 的 `quote_time` 重算 `fresh`（120s）；
  - `src="qmt"`；
- 否则 `src="eltdx"`。
- 任何异常返回 `{}`（不抛）。

---

## 4. 契约扩展

### 4.1 manifest

新增可选字段：
```json
"data": { "feed": true }
```
- 默认 `false`；必须是布尔。
- `Manifest` dataclass 加 `feed_read: bool`；`public()` 的 `data` 里加 `"feed": ...`。
- `validate_manifest` 解析 `data_block.get("feed", False)`，类型错抛 `ContractError`。

### 4.2 PluginContext

```python
@property
def feed(self) -> Any:
    if not self.manifest.feed_read:
        raise PermissionError(f"插件 {self.id} 未在 manifest.data.feed 声明 true")
    from app.core.feed import get_feed
    return get_feed()
```

### 4.3 插件静态扫描

`FORBIDDEN_QUOTE_PATTERNS` 已经包含 `xtquant` / `xtdata`，保持不变；feed 是 core，不受插件扫描。

---

## 5. 生命周期

`app/core/main.py` 的 `create_app()` 末尾：

```python
@app.on_event("startup")
def _start_feed():
    import os
    if os.environ.get("PANEL_V2_FEED", "0") == "1":
        from app.core.feed import get_feed
        get_feed().start()

@app.on_event("shutdown")
def _stop_feed():
    from app.core.feed import get_feed, reset_feed
    get_feed().stop()
```

要求：
- 默认 **不启动**（测试环境无 QMT，绝对不能因为 import 就连接）。
- `run.ps1` 在 `Set-Location` 前加 `$env:PANEL_V2_FEED = '1'`。
- `create_app()` 可以被测试多次调用，不重复起线程（`get_feed().start()` 幂等）。

---

## 6. 测试 `app/tests/test_feed.py`

用假 `xtdata` 模块，零真实网络：

```python
class FakeXt:
    def __init__(self): self.cb=None; self.connected=False; self.seq=0
    def connect(self, ip="", port=None): self.connected=True; return object()
    def subscribe_whole_quote(self, codes, cb): self.seq+=1; self.cb=cb; self.codes=list(codes); return self.seq
    def unsubscribe_quote(self, seq): pass
    def disconnect(self): self.connected=False
```

覆盖：
1. `test_start_subscribes_and_dispatches`：`ensure(["600519.SH"])` → start → 假模块手动调 `cb({"600519.SH": {...}})` → listener 收到 batch，`quote("600519.SH")` 有 last。
2. `test_incremental_ensure`：第二次 `ensure(["000001.SZ"])` 后假模块 `codes` 包含两只（等 supervisor ≤1.5s 或提供 `feed._pump_once()` 同步方法，建议加 `_pump_once()` 供测试）。
3. `test_queue_coalesce`：同一 code 连发 3 条不同 last，消费者只派发最后一条（可调 `_drain_once()` 同步方法）。
4. `test_status_shape`：`status()` 含 §2.2 的键。
5. `test_stop_idempotent`：`stop()` 两次不抛；之后回调不再触发 listener。
6. `test_order_quote_overlay`：monkeypatch `quotes.order_quote` 返回固定 dict，feed `_quotes` 塞一条 QMT tick（ts=now），断言 `src="qmt"` 且 last 被覆盖。
7. `test_no_qmt_when_disabled`：不调 start，`status.state="DISABLED"`。

**不要**让测试等待真实 30s 重试；构造 `QuoteFeed(retry=0, retry_interval_s=0.01)`，并只测内存路径。

---

## 7. 硬约束

1. `app/core/feed.py` 是唯一可以出现 `xtquant`/`xtdata` 字符串的新文件；`app/core/quotes.py` 不出现。
2. 不写任何文件、不写数据库、不连 58610（测试用假模块）。
3. 不改 `app/core/paper/`（另一个任务在写）。
4. 不改 `app/plugins/`。
5. 不引入新第三方依赖。
6. 中文注释保留。

---

## 8. 交付格式

1. 文件清单（绝对路径）。
2. `feed.py` 的线程模型说明（3 行）。
3. 明确写：**「未运行验证：本环境无 shell，pytest 由主会话执行」**。
4. 需主会话确认的问题单独列。

---

## 9. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/tests/test_feed.py -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
```

现有 412 项不得回归。
