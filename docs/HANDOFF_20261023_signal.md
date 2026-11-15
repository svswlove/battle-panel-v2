# signal_buy 信号库接入交接（2026-10-23）

> 前一份：`docs/HANDOFF_20261023_concept_selection.md`（概念 + 选股）
> 本篇：4.4 `signal_buy` 条件单接信号库。

---

## 1. 状态

`signal_buy` 条件单已可用：

- `app/core/paper/signals/library.py` 与 `macd.py`：从 `engine/signals/` **逐字 vendoring**（SHA256 与旧文件一致）。
- `app/core/paper/signal_provider.py`：`make_signal_provider()` 取 `ctx.quotes.minute_bars` 当日分钟K → `Candle[]` → 五个信号类 `evaluate()`；20 秒缓存；任何异常 → False。
- `app/core/paper/service.py::build_service` 已装配 `svc.signal_provider`。
- 测试：`app/core/paper/tests/test_signal_provider.py` 14 passed；全套 **620 passed / 4 skipped**。

## 2. 口径

- 信号类：`BREAKOUT` / `PULLBACK` / `VWAP_RECLAIM` / `HOOK_TURN` / `WEAK_TO_STRONG`（与 `orders.SIGNAL_NAMES` 一一对应）。
- 取数：`quotes.minute_bars(glb, 241)`，只保留 `time` 为今天的 bar；<5 根 → False。
- `WEAK_TO_STRONG` 需要 `prev_close` 参数，当前 provider 未注入（旧 `_signal_check` 也没显式传，旧库缺省会 skip weak 前置），保持旧口径。
- 缓存 `(glb, signal)` 20s，与旧 `_SIGNAL_CACHE` 同口径；异常不写缓存。

## 3. 验证命令

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/core/paper/tests/test_signal_provider.py -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
# 620 passed / 4 skipped
```

## 4. 剩余

- `/api/review.md`、`/api/review_chart.json` 仍是 `compat_misc` 骨架（复盘 Markdown + panel JSON 白名单）。
- selection 写操作（`POST /api/selection_prefs`、选股池增删）仍只在旧 8800。
- 纸面 QMT 第二订阅者周一 09:15 活体验证（见 `docs/HANDOFF_20261023_paper.md`）。
