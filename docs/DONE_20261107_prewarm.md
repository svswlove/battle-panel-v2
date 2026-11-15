# 盘后预热 · 读看板判就绪（2026-11-07）

> 主人：「收盘链不用时间卡得特别紧，通过主执行程序加钩子；数据获取之后就开始并行做数据处理。
> 如果全部完成，就闭环，不需要执行后续的定时任务。」
> 结论：**不新增完成标记、不改链**——复用链末 `opssnap` 已经产出的后台看板（`ops_status.json`）当"数据就绪"信号，
> 面板侧起一个后台线程，就绪后并行把首屏端点跑一遍（实测 10 项 4.4 秒），用户打开直接命中。

## 1. 为什么不用时间点

- `OH_PostCloseChain` 16:10 只是**启动**；11-06 实测 16:21:27 才写完 `amv_board.db`，16:29 才跑完打分。
- 固定钟点要么早（读到半成品）、要么晚（白白多等）。
- 链末的 `opssnap` 已经把"哪些数据新鲜"聚合进 `data/panel/ops_status.json`（后台 Tab 只读这份）。
  它就是现成的闭环信号，复用它即可，不另造实体。

## 2. 判定与执行

| 环节 | 规则 |
|---|---|
| 就绪 | 看板 `meta.trade_date` == `trade_clock.expected_data_date8()`，且清单 `ready_sources` 全部「新鲜」 |
| 未就绪 | 不预热（不拿昨天当今天） |
| 清单 | `projects/panel-v2/prewarm_plan.json`（随代码走；core 不硬编码业务路径） |
| 执行 | `httpx.ASGITransport` 直接打 app（不开端口），并发 2，单任务超时 180s |
| 失败 | 只记录，不影响其他；同一数据日最多重试 3 次（5 分钟退避） |
| 状态 | `data/panel_v2/prewarm_state.json`（数据日、已完成、失败原因） |
| 关闭 | `PANEL_V2_PREWARM=0`（测试 conftest 已设） |

预热清单（首屏慢点）：板块研究 L2/L3/CONCEPT、板块资金流 L2/L3、板块排名 L2/L3、自选池、涨跌家数、
全A 日线。**不预热**：`/api/selection`（8.2MB）、自选 38 票分时（当天第一次 7s，之后落盘）。

## 3. 与链的边界（必须守）

- 看板只用于「下游知不知道数据好了」。**绝不参与链自己判断"要不要跑"**——
  2026-10-16 曾因链自写状态、写盘静默失败，一天跑了 47 次。
- 不改 `postclose_chain.py`；链的失败隔离、错过补跑保持原样。
- "全部完成就不跑后续定时任务"：链后当前没有独立后续任务（登记册里的 18:30 监管代码已不存在），
  暂无可跳过的对象；以后真有时，按看板 `trade_date` 判断即可。

## 3.5 数据日标签（2026-11-07 补齐）

- 后端：`/api/shell/status` 返回 `data_clock`（`trade_clock.status()`：交易日/期望数据日/盘后/降级）。
- 前端：顶栏状态行新增 `#stat-data`，显示「数据日 11-06」，悬停提示「非交易日 · 盘后数据已固定 · 期望数据日 20261106」。
- 不可用/旧 core → 留空，不猜（不显示假日期）。
- 实测（CDP）：`{"text":"数据日 11-06","title":"非交易日 · 盘后数据已固定 · 期望数据日 20261106"}`。

## 3.6 预热清单支持动态代码（2026-11-07 补齐）

自选副图（`kline_sparks`）的代码集是活的，不能写死在清单里。清单条目支持：

```json
{ "path": "/api/kline_sparks?tail=45&limit=160&codes={codes}",
  "codes_from": { "panel": "watchlist", "items": "items", "key": "code",
                  "where": { "kind": "stock" }, "max": 60 } }
```

core 只做「读 panel JSON → 按清单里的字段/过滤条件取代码」的通用遍历，不含任何业务常量；
取不到就跳过该任务。清单现为 **11 项**。

实测：预热 `done=11 / failed=0`（17:00:55），`kline_sparks.json` 194KB；自选卡端到端
`tPaint 95 ms`（改前中位 1491 ms，冷启动 9080 ms），`watchlist.json` 12 ms、`kline_sparks` 21 ms。

## 4. 验证

| 项 | 结果 |
|---|---|
| 手动 `tick` | 10/10 成功，4.4 秒；`prewarm_state.json` 落盘 |
| startup 线程 | 删除 state 后 75 秒内自动重建（说明 60s 周期在跑） |
| 单测 | `test_prewarm.py` 9 例 + `test_trade_clock.py` 8 例 |
| 冷启动对比 | 预热后端到端由 12 卡探针复测（见回执） |

## 5. 回退

- `PANEL_V2_PREWARM=0` 即停（无需改代码）；
- 删 `app/core/prewarm.py` + `main.py` 两处 startup/shutdown 调用 + `prewarm_plan.json`；
- `prewarm_state.json` 可删，下次重新生成。

## 6. 未做（下一批）

- 页面统一亮"数据日"（`trade_clock.status()` 已提供字段，前端未接）。
- 链内失败步骤的"下个执行点重试"（当前靠计划任务错过补跑；要做得先定重试窗口，不为它提前加机制）。
- 复盘不在 16:10 链上，不列入盘后直读。
