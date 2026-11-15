# 任务书 4.1.3a-1 · quotes.order_quote（下单行情快照）

> DSHana 小任务｜cwd = panel-v2
> **无 shell，不运行 pytest/python；只写代码，交付写「未运行验证」。**

## 目标

在 `app/core/quotes.py` 追加三个函数，供后续 feed / paper 插件使用。只改这一个文件 + 新增一个测试文件。

### 1. `build_quote5_payload(ts_code, rec, ticks=None, aux=None, asof=None)`

从 `_vendor_src/quote5_reference.py` 中同名函数**逐字移植**。它依赖的 `_fmt_hhmmss`、`_quote_level`、`_elapsed_minutes`、`_finite` 也一并移植（若 `quotes.py` 已有同名函数，复用现有的，避免重复定义；`_finite` 在 quotes.py 里没有就新增）。

### 2. `order_metrics(glb: str) -> dict`

用 `daily_bars(glb, 40)` 取日线，移植 `_vendor_src/quote5_reference.py` 末尾的 `compute_atr14` / `compute_ma5`（阈值常量：ATR_PERIOD=14、MIN_BARS=15，按参考文件）。返回 `{"atr14": float|None, "ma5": float|None}`。缓存 600 秒（模块级 dict + lock）。日线对象字段优先 `high_price/low_price/close_price`，不存在则 `getattr(b,"high"/"low"/"close",None)`。

### 3. `order_quote(ts_code: str) -> dict`

对应旧 8800 `_order_quote()` 的 eltdx 半段：
1. `glb = normalize(ts_code)`；失败返回 `{}`。
2. `rec = depth(glb)`；`aux = stock_aux(glb)`；`ticks = today_ticks(glb)`（可为空）。
3. `q = build_quote5_payload(ts_code, rec, ticks=ticks, aux=aux)`。
4. `q["fresh"] = _fresh_from_time(q.get("quote_time"))`（120 秒口径，函数移植）。
5. `q.update(order_metrics(glb))`。
6. 返回 q，不 overlay QMT。

### 4. 测试 `app/tests/test_quote_order.py`

用假对象/monkeypatch，不连 eltdx：
1. `test_build_payload_shapes`：构造一个假 rec（属性 `last_price/last_close_price/high_price/low_price/update_time_raw/buy_levels/sell_levels/total_hand/current_hand/outer_disc/inside_dish`），断言 last/pre_close/quote_time/asks/bids 字段。
2. `test_order_metric_fallback`：`monkeypatch.setattr(quotes,"daily_bars",lambda *a,**k: [])`，断言 `order_metrics("sh600519") == {"atr14": None, "ma5": None}`。
3. `test_order_quote_fresh_false_on_stale_time`：monkeypatch `depth/stock_aux/today_ticks` 返回固定值，`quote_time` 设一个很旧的时间，断言 `fresh is False`，字段齐全。
4. `test_order_quote_bad_code`：`order_quote("bad") == {}`。

## 约束

- 只改 `app/core/quotes.py`，新增 `app/tests/test_quote_order.py`。
- 不出现 `xtquant/xtdata`。
- 中文注释保留。
- 交付：文件清单 + 三个函数签名 + 「未运行验证」。
