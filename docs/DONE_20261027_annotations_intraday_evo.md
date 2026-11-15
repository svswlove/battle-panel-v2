# 盘中卡 · 模式隔离 + 六指数叠加真实可用（N20261027-100741-29 / 100915-30 / 100954-31 / 101049-32）

日期：2026-10-27 ｜ 执行：01（架构 + 实现）｜ 验收：主人
范围：`app/plugins/intraday/frontend.js`、`frontend/evo.js`、`app/plugins/compat_sector/backend.py`、两边 tests、两个 `plugin.json`、本文件、当日修改日志。

## 1. 标注事实与本轮定位

| 标注 | 现象 | 根因（本轮核实） |
|---|---|---|
| -29 | 行情涨跌模式仍显示 AMV 预测浮层 | `forecastHtml(st)` 被**无条件**插在左 pane 末尾，不看 `st.mode` |
| -30 | 行情涨跌模式「暂无数据」，六指数勾了也不出线 | 生产轨迹快照只有 `ts/all/boards`，**没有 `idx_chg`**（旧契约里有）；前端 `idxVal` 取不到值 |
| -31 | `amv_live.json` 停在 2026-10-23 12:24，页面像实时 | 前端不做快照日期判定；`amv_live` 文件本身没变（生产端未产出今日快照，非本任务边界） |
| -32 | 行情模式标题还是「活跃指数」，allInfo/AMV 读数混杂 | `html()` 里标题与 `allInfoHtml` 都不看模式 |

实测证据（只读）：
- `GET /api/sector_track.json` → `snapshots[].keys == {ts, all, boards}`，`idx_chg` 0/119 点；
- 生产文件 `data/panel/amv_live_sector_track.json` → 20261027 的 114→120 点全部缺 `idx_chg`；
- `GET /api/intraday/{000001,399006,000688,000300,000852}` → 全部 `date=20261027`、`prev_close` 与 `points` 有效；
- `932000.SH`（中证2000）→ 行情源 `bars.get` 返回 0 根（无分钟线，本轮不修数据源，UI 上直标「无数据」）。

## 2. 改法

### 2.1 后端：`compat_sector` `/sector_track.json` 读路径补 `idx_chg`（0.2.0 → 0.2.1）

- 新增 `IDX_CODES`（六指数，顺序同前端 `EVO_IDX`）、`_idx_series_one()`、`idx_series_map()`、`_pct_at()`、`fill_idx_chg()`。
- 口径：`涨跌% = (分钟收盘 / 该交易日之前最后一根日线收盘 − 1) × 100`；`09:30` 点用当日第一根 `open`。
- 取数**只走 core 门面** `ctx.quotes.minute_bars / daily_bars`（不直连行情源、不发 HTTP）；每指数**只取一次**（1 次分钟 + 1 次日线），`_IDX_CACHE` 20s TTL。
- 对齐：快照 `ts` 取 `HH:MM`，取 **≤ 该分钟** 的最近有效值（快照带秒，不用「向前外推」）。
- 三条硬约束：
  1. **只认目标交易日**：`minute_bars` 返回「最近一个交易日」，日期不符 → 该指数整条不补（历史日期不会被今天曲线硬填）；
  2. **不补 0**：分钟 `close` 为 null 就跳过，绝不 `Number(null)→0`；昨收取不到时退到当日开盘价，两者都没有 → 保留缺字段；
  3. **不写生产文件**：`fill_idx_chg` 返回新 list，带 `idx_chg` 的快照原样透传，其它字段不动。
- 响应键与旧契约一致（仍是 9 键，无 `source`），`boards` 字段未污染。

### 2.2 前端：`frontend/evo.js` 模式隔离（`plugin.json` 0.1.1 → 0.1.2）

- `forecastHtml()` 首行加 `if (st.mode !== 'amv') return '';`——预测**只在 AMV 模式**出现。
- 新增 `export function forecastStale(lv, now)`：按 `trade_date / asof / meta.generated_at` 的**快照日期**与注入的 `now` 比较；非今日 → `历史预测 YYYY-MM-DD HH:MM` 角标（`data-stale="1"`），并写进 tooltip。当前时间可经 `st.now` / `st.nowFn` 注入，纯函数可测。
- `html()`：`isAmv` 分支——标题 `活跃指数 + AMV 读数`（AMV）vs `行情涨跌 + 已选指数名`（quote）；`allInfoHtml(st)` 只在 AMV 模式渲染；来源行文案 `真实存档` / `指数分钟线` 分开。
- `quoteChart()`：`quoteOn(st)` 勾选优先、一个都没勾时回退前两个（**不留白图**）；两种「无数据」话术分开——缺 `idx_chg` 字段 vs 字段在但本段无值。
- `idxLegend()`：quote 模式不再挂「活跃指数」色块；本段无值的指数按钮直标「无数据」（`932000.SH` 就是这种）；`title` 改「叠加/取消」。
- `cursorRows/rangeRows` 走 `drawnIdx(st)`，与曲线同源（quote 回退时也不出现 3 行数值对 2 条线）。
- 样式走内联（`card.css.js` 未改，保持工作区外文件不动）。
- `frontend.js` 只改 1 行：`import * as evo from './frontend/evo.js?v=0.1.2'`（浏览器强刷子模块；值与 manifest 版本同步）。

## 3. 验证

### 3.1 纯函数 / 端点（pytest）

| 命令 | 结果 |
|---|---|
| `pytest app/plugins/compat_sector/tests/test_idx_fill.py -q` | **10 passed**（新） |
| `pytest app/plugins/intraday/tests/test_evo_mode.py -q` | **11 passed**（新） |
| `pytest app/plugins/intraday/tests app/plugins/compat_sector/tests -q` | **161 passed / 12 failed**（改前基线 **139 passed / 12 failed**，失败集**同一批**：`test_audit` 6 + `test_evo` 3 + `test_quotes` 2 + `test_research` 1，全部是 legacy 对齐例，跑旧页片段时 `cls is not defined`；按任务书「不为既有 12 条 align 失败修旧页」不动） |
| `pytest app/tests/test_fe_shell_bridge.py -q` | **28 passed**（裸 hex / 主题 token 门禁未破）；连同上面一起跑 = **190 passed / 12 failed** |

新增断言（先红后绿）：
- `fill_idx_chg` 补值且原字段不动、原 list 不被改；
- 昨收缺失 → 保留缺字段（不补 0）；分钟 `close` 为 null → 回退到最近有效值而非 0；
- `09:30` / `13:00` 各自对齐（午休后首点不串到上午末点）；
- 历史日期请求 → 一根不补，**且一次行情都不打**（`fill_idx_chg(today8=...)` 可注入，非当日直接不取数）；
- 已带 `idx_chg` → 原样透传；无行情门面 → 端点仍 200 且保留缺字段；
- `idx_series_map` 二次调用**不重复打行情**（每指数 2 次调用封顶，20s TTL）；
- 端点响应键仍为旧契约 9 键。

前端纯函数（`tests/evo_mode.mjs`，真实 `frontend/evo.js`）：
- quote 模式：预测浮层 0、全A 信息行 0、标题「行情涨跌」、出 `data-evo="quote"`；
- amv 模式：预测 1、信息行 1、标题「活跃指数」；
- 预测陈旧：历史快照 → `历史预测 2026-10-23 12:24`；今日快照 → 无角标；历史预测在 quote 模式同样不显示；
- 六指数：勾 1 → 1 条 path，勾 2 → 2 条，path 内容不同；不勾 → 回退 2 条不空白；AMV 模式叠加仍在（1→2 条）；
- 缺 `idx_chg` → 明确文案、path 0 条（不补 0）。

### 3.2 真实浏览器（Chrome CDP → 8810，`tests/js/evo_mode.cdp.mjs`）：**21/21 PASS**

数据：`tests/fixtures/track_filled_20261027.json`（真实生产轨迹 + 后端 `fill_idx_chg` 的输出，形状等于重启后 `/api/sector_track.json`；由 `tests/gen_track_filled.py` 重生成），经
`Page.addScriptToEvaluateOnNewDocument` **只劫持这一页**的 `/api/sector_track.json`；其余请求照旧打真接口
（`amv_live.json` 仍是 2026-10-23 的真实陈旧快照，正好验「历史预测」）。**未重启 8810**。

实测：AMV 模式预测在且带 `历史预测 2026-10-23 12:24` → 切行情：预测 0 / 信息行 0 / 标题「行情涨跌」/ 指数线在；
六个指数逐个点，path 6 次各不相同；点满 6 个；只留上证 → 只剩 1 条线且标签 1 个；切回 AMV 预测回来、日期仍在、叠加仍在；console 异常 0。

截图：`_vis/evo_mode/01_amv_default.png`（AMV：预测+历史角标+全A 信息行+活跃指数）、
`02_quote_mode.png`（行情：上证/创业板两条、无预测无信息行）、`03_quote_all_idx.png`（六选五，中证2000 标「无数据」）、
`04_quote_one_idx.png`（只剩上证）、`05_amv_back.png`。

### 3.3 生产数据探针（`app/plugins/compat_sector/tests/probe_idx_gate.py`，只读）

```
20261026: snaps=127 filled=0 quoteCalls=0  （历史日：一次行情都不打 → 一根不补，符合「不同日不串」）
20261027: snaps=120 filled=120 quoteCalls=6 codes=000001/399006/000688/000300/000852（932000 无源）
prod file untouched: True（生产文件未被写）；响应仍 9 键
```

## 4. 需要 01 做的收尾

1. **重启 8810**（`tools/ensure_8810.ps1`）：`compat_sector` 后端改了，manifest 版本也要重读才生效。
   重启前 `/_plugins` 仍报 `compat_sector 0.2.0 / intraday 0.1.0`；重启后应变成 `0.2.1 / 0.1.2`，
   `GET /api/sector_track.json` 的 `snapshots[].idx_chg` 应有 5 个指数（无 932000）。
2. 重启后 `?v=0.1.2` 生效，浏览器会重拉 `frontend/evo.js`。

## 5. 未做 / 不在边界

- `amv_live.json` 停在 10-23 是**生产端没产出今日快照**（另单 N20261027-101349-33 已改取数范围，需重启后才产出）；本轮只让前端如实标陈旧，不碰 `engine/`。
- `932000.SH` 行情源无分钟线：UI 标「无数据」，不补、不猜替代指数。
- 既有 12 条 legacy 对齐失败：按任务书不修。
- 研究快照卡与 sector 页面（`frontend/research.js`、`app/plugins/sector/`）**一行未动**（已 `git diff` 核对）。
- 未改 `app/core/`、`engine/`、生产 state/panel、`compat_kline`；无跨插件 import；无新依赖。
