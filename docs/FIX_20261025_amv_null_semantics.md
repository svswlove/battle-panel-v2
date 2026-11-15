# 口径统一：个股四态缺失值语义（2026-10-25）

## 一句话

同一个指标在四个文件里被算了四遍，且缺失值处理各不相同；实测导致 **8 只股票最新交易日多空结论方向相反**。已收敛到唯一正本，并加测试守门。

## 实测证据（生产库 `data/amv_board.db`，2026-10-25）

全市场有缺失记录的股票 **1073 只**，历史累计分歧 **27001 天**。

最新交易日（末值）两边都有值但结论不同的 **8 只**：

| 股票 | 原写法（缺失置 0.0） | 新正本（缺失跳过） |
|---|---|---|
| 000668 | 多 | **空** |
| 002388 | 多 | 偏多 |
| 002667 | 偏多 | **空** |
| 002860 | 多 | 偏多 |
| 300082 | 多 | **空** |
| 301716 | 偏多 | **空** |
| 603183 | 偏多 | **空** |
| 603759 | 多 | **空** |

另有 66 只：新正本给不出结论（数据不足），旧写法硬给了一个值。新行为更保守，符合「不编造」。

无缺失的 800 只票：末值全部有结论，**无回归**。

## 根因

`app/lib/amv_regime.py::sma` 旧实现直接 `s += v`，遇 `None` 会 `TypeError`。
消费端为了不崩，只好在调它之前把 `None` 换成 `0.0`——**这就是「缺失置零」在四个文件里重复出现的根源**。

更严重的是 EMA 种子：`stock_state_series` 原第 116 行 `e1 = e2 = vals[0]`。
若 `vals[0]` 被置 0，整条 EMA 从 0 起步，要几十根才收敛，导致该股**全部历史四态被压低**，而不只是缺失那一天。

## 改了什么

### 正本 `app/lib/amv_regime.py`

1. **`sma(vals, n)`**：接受 `None`。窗口内跳过缺失，有效点数 ≥ n 才出值；缺失日本身输出 `None`。
   → 消费端不再需要、也不再允许做任何预处理。

2. **`stock_state_series(vals)`**：
   - EMA 种子取**第一个有效值**，不是 `vals[0]`
   - 缺失日 EMA 沿用前值（不推进也不置零），该日判定留空
   - MA10 窗口跳过 `None`，有效点数 ≥ 10 才出值
   - 返回长度与输入等长（不删元素，避免日期错位）

3. **`compute_regime(dates, amv)`**：缺失日两轨都不出信号（原来会拿 `None` 参与比较）。

缺失值语义写进 docstring，作为唯一口径。

### 消费端 4 处（原写法均不合口径）

| 文件 | 行 | 原写法 |
|---|---|---|
| `compat_kline/backend.py` | 1127 | `else 0.0`（板块 compute_regime） |
| `compat_kline/backend.py` | 1146 | `else 0.0`（个股 stock_state_series） |
| `compat_watchlist/backend.py` | 467 | `else 0.0`（个股四态） |
| `compat_watchlist/backend.py` | 519 | `else 0.0`（板块双轨） |

全部改为 `else None`，由正本处理。

### 另有一处不合口径但未改（属另一问题）

`sector/backend.py:446-447`：`amv is None` 时 `continue`，**删掉该元素**，导致序列变短、日期整体前移。
这条不改的原因：它走的是 `stock_state(vals)`（末值版），路径与上面 4 处不同，且删元素后日期对齐需要单独处理。已记入待办，见下。

## 守门测试

`app/lib/tests/test_amv_regime_null_semantics.py`，6 项：

1. `test_length_preserved`：缺失日必须占位，长度不变（防日期错位）
2. `test_null_day_has_no_state`：缺失当日不出判定
3. `test_seed_is_first_valid_not_zero`：EMA 种子取第一个有效值（首日缺失不污染后续）
4. `test_zero_fill_gives_different_verdict`：证明置零与跳过确实产生不同结论（规则不是形式主义）
5. `test_no_none_to_zero_on_amv`：**静态扫描**，禁止任何插件再写 `is not None else 0.0` 处理 AMV
6. `test_stock_state_series_is_single_entry`：插件内不得自建四态判定

第 5 条第一次写时路径算错（`APP` 少上溯一级），扫描范围为空，**假通过**；修正后立即抓出 4 处违规。这条教训值得记：守门测试要先证明它能抓到已知问题，否则等于没写。

## 验证

- `pytest app/lib app/plugins/sector/tests app/plugins/compat_watchlist/tests app/plugins/compat_kline/tests app/tests` → **324 passed, 1 skipped**
- 生产库实测：8 只方向翻转已按新口径输出；无缺失的 800 只无回归
- 8810 健康检查：18 个插件全 ok，改动已在运行中生效
- 接口实测：`/api/watchlist.json` 4110ms（28 只，含四态）；`/api/kline_bg/*` 125ms

## 待办（按优先级）

1. **`sector/backend.py:446` 删元素问题**：`continue` 会让日期前移，四态与 K 线错位。修法：保留位置传 `None`，交给正本。
2. **`compat_sector/backend.py:212`**：`[v if v is not None else 0.0 for v in avf]` 传给 `sma`，同一问题。
3. **MA10 六种写法收敛**（嗅嗅报的 #1）：`compat_misc:666` 扩张窗、`compat_kline:552` 严格 n、`amv/backend.py:60` 等，需逐个确认是否该改成调正本 `sma`。
4. **板块权重 Δ 两种量纲**（嗅嗅报的 #2）：小数 vs ×100，混用差 100 倍。需先确认落库 `share` 口径。
5. **880 快照解析两份**（嗅嗅报的 #4）：`core/panic_calc.py:207` 与 `compat_sector:530`，抽到 core。

## 相关文档

- `docs/AUDIT_metric_duplication_20261025.md`：嗅嗅的同指标多实现全量审计（7 组，4 组算法不一致）
- `docs/AUDIT_compat_routes_20261025.md`：数据员的 compat 路由归属盘点（待落盘）
- `docs/DESIGN_fe_shared_core_20261025.md`：前端共用核心切分方案（待落盘）
