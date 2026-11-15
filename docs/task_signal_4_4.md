# 任务书 4.4 · `signal_buy` 条件单接信号库（纸面最后功能缺口）

> 执行者：DSHana｜主会话验收｜2026-10-23
> **无 shell，不运行 pytest/python；只写代码，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`

---

## 0. 背景

`PaperOrderService.evaluate()` 的 `signal_buy` 条件单目前永远不触发，因为 `signal_provider=None`。
旧 8800 的实现：`server.py::_signal_check` 取当日分钟 K（eltdx）→ `signals/library.py` 的五个信号类判定。
本任务把信号库搬进 paper core，并接回 provider。

---

## 1. 交付文件

```
app/core/paper/signals/__init__.py        # 新增，导出 library 里的类/结构
app/core/paper/signals/library.py         # 从 _vendor_src/library.py 逐字复制（保留 docstring）
app/core/paper/signals/macd.py            # 从 _vendor_src/macd.py 逐字复制
app/core/paper/signal_provider.py         # 新增：make_signal_provider()
app/core/paper/service.py                 # 修改 build_service：装配 signal_provider
app/core/paper/tests/test_signal_provider.py  # 新增
docs/paper_trading_security_review.md     # 更新「signal_buy 未接」为已接
docs/STATUS.md                            # 更新 known gap
```

**不要改 `app/core/paper/orders.py`**（它已经按 `self.signal_provider(code, signal)` 调用）；不要改 `app/plugins/paper/`（`_wire` 已有逻辑：若 provider 为 None 才设为 None，service 装配后自然保留）。

---

## 2. `app/core/paper/signal_provider.py`

```python
def make_signal_provider(quotes_module=None) -> Callable[[str, str], bool]
```

行为（与旧 `engine/server.py::_signal_check` 同口径）：

1. `SIGNAL_CLASSES = {"BREAKOUT": library.BreakoutSignal,
                     "PULLBACK": library.PullbackSignal,
                     "VWAP_RECLAIM": library.VwapReclaimSignal,
                     "HOOK_TURN": library.HookTurnSignal,
                     "WEAK_TO_STRONG": library.WeakToStrongSignal}`
2. 参数 `signal` 大小写归一；未知 → False。
3. `quotes = quotes_module or from app.core import quotes`（懒 import，避免循环）。
4. `glb = quotes.normalize(ts_code)`；失败 → False。
5. `bars = quotes.minute_bars(glb, 241) or []`；只保留 `time` 为**今天**的 bar（按 `time.strftime("%Y%m%d") == today`）；不足 5 根 → False。
6. 转 `library.Candle`：
   - `ts = time.strftime("%Y-%m-%d %H:%M:%S")`
   - `open/high/low/close` 优先 `bar.open_price` / `bar.high_price` / `bar.low_price` / `bar.close_price`，否则 `bar.open` 等
   - `volume = bar.volume_lots`（eltdx 的 `volume_lots`，手）或 `bar.volume`；`amount = bar.amount or 0`
7. `res = cls().evaluate(candles)`；`bool(res.triggered)`。
8. 缓存：`(glb, signal)` → `(monotonic, bool)`，20 秒；与旧端 `_SIGNAL_CACHE` 同口径。
9. **任何异常 → False**（宁可不买，不瞎买）。

### `app/core/paper/service.py`

`build_service()` 末尾：
```python
from app.core.paper.signal_provider import make_signal_provider
svc = PaperOrderService(...)
svc.signal_provider = make_signal_provider()
return svc
```
注意：`make_signal_provider()` 本身不得在 import 期连接行情；它只在 provider 被调用时懒 import `quotes`。

---

## 3. `app/core/paper/signals/__init__.py`

导出：
```python
from .library import (Candle, SignalResult, BreakoutSignal, PullbackSignal,
                      VwapReclaimSignal, HookTurnSignal, MacdDivergenceSignal,
                      WeakToStrongSignal, StopSignal, StopAnchorPicker)
```
（需要确认 `library.py` 里这些类都存在；`macd.py` 由 library 内部相对导入。）

---

## 4. 测试 `app/core/paper/tests/test_signal_provider.py`

不要依赖真实 eltdx/QMT；用假 quotes 模块：

```python
class FakeBar:
    def __init__(self, dt, o,h,l,c,v): self.time=dt; self.open_price=o; ...
class FakeQuotes:
    def normalize(self,c): return 'sh600519'
    def minute_bars(self, glb, count): return [...]
```

覆盖：
1. `test_unknown_signal_false`
2. `test_insufficient_bars_false`（<5 根）
3. `test_quote_failure_false`（`minute_bars` 抛异常）
4. `test_provider_returns_bool_and_caches`：第一次调用返回 bool，第二次同一 (`code`,`signal`) 不重复调 `minute_bars`（20s 缓存）
5. `test_breakout_signal_with_synthetic_bars`：构造 25 根横盘 + 最后放量突破，断言 `BreakoutSignal().evaluate(Candle list).triggered is True`（纯库测试，不经过 provider；如果构造困难，可只断言 provider 返回 bool，不强制触发）
6. `test_signal_buy_condition_uses_provider`：用 `PaperOrderService(session_ok=lambda: True)`，注入 `svc.signal_provider=lambda code,sig: True`，加 `signal_buy` 条件单后 `evaluate(quotes)` 触发；`signal_provider=lambda...: False` 时不触发。旧 `test_orders.py` 已有类似用例，确认新 provider 接上即可。

---

## 5. 硬约束

1. `app/core/paper/` 内仍禁止 `xtquant/xtdata/xttrader/qmt_trader`。
2. 信号库复制后不得改算法；只允许改 import 路径（`library.py` 的 `.macd` 保持相对导入，包结构正确）。
3. 不写文件、不连行情（测试全 fake）。
4. 中文注释保留。
5. `docs/paper_trading_security_review.md` 的「signal_buy 未接」改为「已接，口径=signals/library.py」。

---

## 6. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/core/paper/tests/test_signal_provider.py -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
```

目标：新增测试全过，现有 604 项不回归。
