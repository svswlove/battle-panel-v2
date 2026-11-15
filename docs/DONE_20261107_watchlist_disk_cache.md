# 自选池冷启动慢 · 日更富化磁盘缓存（2026-11-07）

> 主人：「自选页面首次打开速度太慢了……每次打开需要将用到的所有内容全部重新拉取计算一遍。」
> 结论：**冷启动首屏 tHead 8.07s → 1.27s，tPaint 9.08s → 2.24s；`/api/watchlist.json` 8.01s → 1.21s；口径 57 项 × 15 字段 0 差异。**

## 1. 现场（改前实测）

周六 12:32 进程重启后，用独立 8898 实例（同码、无 FEED）复现「首次打开」：

| 里程碑（浏览器 CDP，38 张卡） | 冷态（改前） | 热态（第二轮） |
|---|---|---|
| tHead（卡头出字） | **8068 ms** | 33 ms |
| tPaint（副图全部落线） | **9080 ms** | 57 ms |
| `/api/watchlist.json` | **8013 ms** | 4 ms |
| `/api/kline_sparks` | 1037 ms | 9 ms |

改前热态并不慢——问题只出在**进程重启后/15 分钟 TTL 过期后的第一个请求**。

### 冷算拆解（独立进程，48 票 + 9 板块）

| 环节 | 累计 | 说明 |
|---|---|---|
| `_sector_regime` | **5871 ms** | 每个板块读全历史 `sector_daily`（1993 起，303 万行）跑双轨状态机 |
| `_eod_quotes_for` | 1013 ms | 48 票 × `daily_bars`（行情源冷取，本轮不动） |
| `_stock_regime` | 556 ms | 48 票 × 全历史 `stock_amv` |
| `div` / `flow` / `badges` / `sec_of` | 715 ms | 各票逐项查询 |
| 合计 | **~7.1 s**（HTTP 层 8.0 s） | |

「算了但每次重启都要重算」占 85%。这些结果都来自日更库（一天只写一次），却只存在进程内存里。

## 2. 改动（4 个文件 + 1 份文档）

| 文件 | 改动 |
|---|---|
| `app/core/paths.py` | `READABLE_JSON` / `WRITABLE_MISC` 各登记 `watchlist_enrich_cache`（同一路径，读写各一条） |
| `app/plugins/compat_watchlist/plugin.json` | v0.1.1：`data.json` + `data.misc` 声明、`write:misc` 权限 |
| `app/plugins/compat_watchlist/backend.py` | 新增落盘层：`_db_stamps`（`PRAGMA database_list` + `os.stat`）、`_disk_ensure`、`_disk_put`、`_disk_flush`；8 个日更 `_cached(..., persist=True)`；`_build_watchlist` 入口校验、返回前落盘 |
| `app/plugins/compat_watchlist/tests/test_compat_watchlist.py` | FakeCtx 补 `misc_write`/缓存 `json_read`；新增 4 例（落盘、跨重启命中、库变失效、损坏降级）；manifest 断言更新 |

设计要点：

1. **失效口径 = 三个源库的 `mtime_ns + size`**（`amv_board.db` / `ths_board.db` / `buy_signal.db`）。
   任一变化 → 整份作废重算；库不变 → 重启后直接读盘。日更库盘中不写，不会把旧值当新值。
2. **只落日更类**（`DB_TTL_S`）；行情类（`eod_batch`，60s）和整包（6s）不落盘。
3. **读坏/写坏都只退化为现算**，不影响响应；写走 `ctx.misc_write`（白名单 + 原子替换 + 审计），插件不裸写文件。
4. 同一 ctx 每 30s 才重算一次库戳；`_reset_cache` 会强制下一次重检（写自选/清缓存后不会多看 30s 旧值）。

## 3. 验证（全部实测）

| 项 | 结果 |
|---|---|
| 冷启动端到端（8898，新码，磁盘缓存已存在） | tHead **1272 ms**、tPaint **2243 ms**、watchlist.json **1209 ms** |
| 重启前后首次请求 | 7829 ms（无缓存首次）→ **1234 ms**（重启命中）→ 14 ms（热） |
| 口径一致性 | 8810 现算 vs 8898 缓存命中：**57 项 × 15 字段 0 差异**，`stats` 相同 |
| 插件回归 | `pytest app/plugins/compat_watchlist/tests` → **37 passed** |
| 全量回归 | `pytest app` → 见下方执行记录 |

## 4. 回退

- 删除 `backend.py` 中 `_DISK_*` 段、`_cached` 的 `persist` 参数与 8 处 `persist=True`、`_build_watchlist` 的 ensure/flush；
- `git checkout -- app/core/paths.py app/plugins/compat_watchlist/plugin.json app/plugins/compat_watchlist/tests/test_compat_watchlist.py`；
- 缓存文件可留可删（`夏天-activity/工具/xiaoshi_cache/watchlist_enrich_cache.json`），删掉只会多算一次。

## 5. 未做（有意）

剩余首屏 2.2s 的成本是**行情源冷取**：`/api/watchlist.json` 里 48 票 × `daily_bars`（~1.0s）
与 `/api/kline_sparks` 48 票 K 线（~1.0s），两者并行。要再压必须给 `core.quotes` 加批量日线接缝
（当前是单例连接 + 全局锁），属公共接口改动，另立任务。

另：周六「板块页加载中」是 8002 工作台未启动（iframe 目标不可达），与本缓存无关。
