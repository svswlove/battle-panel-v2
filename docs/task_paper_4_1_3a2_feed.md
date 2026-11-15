# 任务书 4.1.3a-2 · core QuoteFeed（QMT 订阅）

> DSHana 小任务｜cwd = panel-v2
> **无 shell，不运行 pytest/python；只写代码，交付写「未运行验证」。**
> 前置：`quotes.order_quote()` 已由 4.1.3a-1 完成。若暂无，按 `from app.core import quotes` 调用即可，缺函数用 `getattr` 兜底。

## 目标

新增 `app/core/feed.py` 与 `app/tests/test_feed.py`。只改这两个文件（外加 `app/core/contract.py` 的 feed 字段与 `PluginContext.feed`，以及 `app/core/main.py` 的 startup/shutdown、`run.ps1` 环境变量）。

### `app/core/feed.py`

完整接口与行为见 `docs/task_paper_4_1_3a_feed.md` §2（**这份是权威细则，逐条照做**）。要点再列：

```python
class QuoteFeed:
    def __init__(self, host="127.0.0.1", port=58610, xtdata_module=None,
                 retry=3, retry_interval_s=30.0, stale_s=60.0,
                 reconnect_throttle_s=60.0, dispatch_interval_s=0.05): ...
    def start(self): ...
    def stop(self): ...
    def ensure(self, codes): ...
    def quote(self, code): ...
    def snapshot(self, codes): ...
    def add_listener(self, fn): ...
    def status(self): ...
    def order_quote(self, ts_code): ...   # eltdx 打底 + QMT 5 秒内覆盖

def get_feed(): ...
def reset_feed(): ...
```

必须包含：
- 回调只写内存 + `queue.Queue(maxsize=20000)`，消费者合并同 code 最后一条后派发 listener；
- 启动 3 次×30s 退避，失败不退出进程，status ERROR；**之后 supervisor 每 60s 继续重连一次（8810 常驻，QMT 可能晚启动）**；
- 增量订阅 `ensure()`，supervisor ≤1s 补订阅；
- 盘中 60s 无 tick 重连（unsubscribe → disconnect → connect → 全量重订阅），非交易时段不判流；
- `order_quote()` 调 `app.core.quotes.order_quote`，QMT tick 5 秒内覆盖 last/bids/asks/quote_time，`src` 标 `qmt`/`eltdx`；
- 测试注入 `xtdata_module`；生产延迟 `import xtquant.xtdata`（这是 core 唯一允许出现 xtquant 的文件）；
- 交易时段判断：内部 `_in_session()` 用工作日 09:30–11:30 / 13:00–15:00（不引入新模块）。

### `app/core/contract.py`

按 4.1.3a 任务书 §4：`data.feed` 布尔、`Manifest.feed_read`、`public()` 增加 `"feed"`、`PluginContext.feed` 属性。

### `app/core/main.py`

按 4.1.3a 任务书 §5：`startup` 里 `PANEL_V2_FEED=1` 才 `get_feed().start()`；`shutdown` 调 stop；默认关闭。

### `run.ps1`

`Set-Location` 前加 `$env:PANEL_V2_FEED = '1'`。

### `app/tests/test_feed.py`

用假 `xtdata` 模块（不要真连接）：
1. `start` 订阅 + 假回调派发 + `quote()` 有值；
2. `ensure` 增量订阅；
3. 队列合并同 code 只派发最后一条（提供 `_drain_once()` 同步方法）；
4. `status()` 键齐全；
5. `stop()` 幂等且不再回调；
6. `order_quote` overlay（monkeypatch `quotes.order_quote` 返回固定值，feed 内存塞 tick）；
7. 未 start 时 `state="DISABLED"`。

## 约束

- 不真连 58610；测试全部假模块。
- 不写文件、不写数据库。
- 不改 `app/core/paper/`、`app/plugins/`、`_vendor_src/`。
- 中文注释保留。
- 交付：文件清单 + 线程模型 3 行 + 「未运行验证」。
