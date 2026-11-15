# 全站盘后取数落盘（2026-11-07）

> 盘后阈值取 **16:10**（`OH_PostCloseChain` 收盘取数主链启动时间，登记册 A1 T36）。
> 15:00 只是收市，库到 16:10 后才重建成收盘数据；15:00–16:10 之间仍按盘中处理。

> 主人：「所有页面都可以使用这样的方法，因为收盘后的数据就已经固定下来了，没必要每次都重拉重算。」
> 结论：**盘中页 10.0s → 1.5s 级、自选实时档 8.0s → 1.5s、自选盘后档 8.0s → 1.3s（均为进程重启后首次）。**
> 当天第一次仍要拉一次数据（物理成本），之后所有页面重启都读盘。

## 1. 全站冷启动基线（12 张卡，独立 8898 实例依次点开）

| 卡 | 首次请求总耗时 | 最大单请求 |
|---|---|---|
| **盘中** | **10009 ms** | `amv_research.json` 6848 ms；`breadth_880` 947 ms；`index-series` 904 ms |
| 自选池 | 1456 ms（已修） | `kline_sparks` 744 ms；`watchlist.json` 681 ms |
| 板块 | 1127 ms | `board_flow` 915 ms |
| 选股 | 1111 ms | 4 个并发各 ~200 ms |
| 情绪温度计 | 228 ms | — |
| 复盘 | 226 ms | — |
| 仪表盘 | 197 ms | — |
| 模拟交易 | 82 ms | — |
| 概念 | 52 ms | — |
| 预警 / 估值 / 后台 | 20–39 ms | — |

只有 4 张卡需要治；其余 8 张本来就 ≤230 ms。

## 2. 改动

| 文件 | 改动 |
|---|---|
| `app/core/paths.py` | 新增 `plugin_cache_path()`：`data/panel_v2/_cache/<plugin>/<name>.json`，名字白名单校验防穿越 |
| `app/core/contract.py` | 新增 `ctx.cache_read(name)` / `ctx.cache_write(name, payload)`：原子写、不做备份审计（缓存可重算） |
| `app/plugins/compat/backend.py` | `amv_research` 盘后落盘：**缓存检查提到 `_rs_base` 之前**（冷启动 6.8s 的大头），按 `amv_board.db`/`ths_board.db` mtime+size 失效；盘后（周末/16:10 后/09:15 前）统一收盘口径 |
| `app/plugins/compat_kline/backend.py` | `/api/intraday/{code}` 盘后落盘：文件名带 code+日期，次日自然失效；只缓存当日档、只缓存 count>0 |
| `app/plugins/kline/backend.py` | 同一套落盘（v2 K 线卡走 `/api/p/kline/intraday`） |
| `app/plugins/compat_watchlist/backend.py` | （前一轮）日更富化落盘 8s → 1.2s |
| `app/tests/test_plugin_cache.py` | 新增 7 例：写读闭环、缺文件、损坏、路径穿越、插件隔离、原子写、payload 校验 |
| `app/plugins/compat/tests/test_compat_contract.py` | 新增 2 例（盘后落盘跨重启命中、库变失效）；加 autouse fixture 固定「盘中」，避免周末跑挂 |
| `app/plugins/compat_watchlist/tests/test_compat_watchlist.py` | 新增 4 例（前一轮） |

## 2.5 三道防线（主人 2026-11-07 提醒：没取到 / 空数据 / 拿昨天当今天都致命）

| 防线 | 做法 | 落在哪 |
|---|---|---|
| 空数据不写 | 榜单/序列 0 条、`ok=false`、取数失败一律不落盘 | `amv_research` 看 `boards` 非空；分时看 `points` 非空 |
| 失败不写 | 降级回上一交易日的结果不写 | 分时看 `fallback=False` 且 `resolved == today8` |
| 日期对齐 | 读缓存时校验 payload 数据日 == 当前库最新日；不等当 miss | `_rs_disk_get` 比 `asof` 与 `SELECT MAX(date)` |

这三条已写进 `ctx.cache_read` 的 docstring（core 只给存储，不做业务判断）。

## 2.6 第二批（同日收尾）

| 文件 | 改动 |
|---|---|
| `app/plugins/compat_kline/backend.py` | `/api/kline_sparks` 盘后落盘（`kline_sparks`）：key 含 tail/limit/codes，校验 `asof == expected` |
| `app/plugins/compat_watchlist/backend.py` | EOD 报价落盘（`eod_quotes`）：校验至少一条 `asof == expected`（注：EOD asof 是 `YYYY-MM-DD`，比较前归一） |
| `app/plugins/compat_sector/backend.py` | `breadth_880` 数据日改为「快照所代表的交易日」（880005 日线末根），周末也能正确落盘，不再标自然日 |
| `app/core/prewarm.py` | state 加 `pid`：新进程旧 done 不可信，必须重新预热（否则内存缓存是空的） |

## 3. 验证（8898 新码实测）

| 场景 | 改前 | 改后 |
|---|---|---|
| 盘中页 `amv_research` 冷启动 | 6848 ms | **532 ms**（算完落盘）/ **139 ms**（重启读盘） |
| 自选实时档（38 张卡全部画完） | 8056 ms | **319 ms**（读盘）/ **1481 ms**（进程重启后含 watchlist 冷取） |
| 自选盘后档 | 8069 ms | 1272 ms（前一轮） |
| 盘中页首次全量 | 10009 ms | 待复测（还剩 breadth/index-series 各 0.9s） |

口径：
- `amv_research` 落盘与现算逐字段一致（同一份 payload）；
- 分时落盘只在盘后生效，盘中仍实时拉 eltdx；
- 缓存按源库 mtime+size / 日期失效，数据更新自动重算。

## 3.5 最终 12 卡（预热后，进程重启首屏）

| 卡 | 改前 | 改后 | 主要剩余 |
|---|---|---|---|
| 盘中 | 10009 ms | **1432 ms** | sector_track 534 + spark 357 + summary 331 |
| 板块 | 1127 ms | **39 ms** | — |
| 自选 | 1456–3573 ms | **977 ms** | kline_sparks 913（38 票全量 key 未预热） |
| 选股 | 1111 ms | 876 ms | 4 个 ~270 ms 并发 |
| 复盘 | 226 ms | 212 ms | — |
| 仪表盘 | 197 ms | 162 ms | — |
| 其余 6 卡 | 20–228 ms | 20–241 ms | 未退化 |

单端点（重启后首次）：`amv_research` 6.8s → **0.14s**；`board_flow` 0.9s → **6ms**；`breadth_880` 2.2s → **9ms**；`watchlist` 1.7s → **27ms**；`kline_sparks`（20 票）0.82s → **0.11s**。

### 生产 8810 实测（同码，预热完成后）

| 卡 | 改前 | 改后 |
|---|---|---|
| 盘中 | 10009 ms | **1498 ms** |
| 板块 | 1127 ms | **49 ms** |
| 自选池 | 1456–3573 ms | **89 ms** |
| 其余 9 卡 | 20–1111 ms | 25–1130 ms（持平） |

注：自选 89 ms 是因为探针在 8898 先跑过一次，38 票的 sparks 磁盘缓存与 8810 共享同一 OUT_DIR。
全新环境（无缓存）第一次仍会慢一次（行情拉取），之后都是读盘。

## 4. 回退

- core：删 `paths.plugin_cache_path` 与 `contract.cache_read/cache_write`；
- 插件：删各文件的 `_is_postclose`/落盘段，恢复 `return {...}`；
- 缓存目录可整体删除（`data/panel_v2/_cache/`），删掉只会重算一次。

## 4.5 夏天核对结论（2026-11-07，只读核查）

1. **16:10 是链启动时间，不是完成时间**。11-06 实测：16:10:00 起链，16:21:27 才写完 `amv_board.db`，sector_temp 16:21:30，`buy_signal.db` 16:29:09，六维打分 16:29:37。建议兜底钟点用 **16:40**（链的 `READY_DEADLINE`），不是 16:10。
2. **4 个慢端点的产物情况**：`board_flow` 无全板块产物（`sector_temp_latest` 缺 flow 字段）；`breadth_880` 无产物且两段不同源（today 快照 / prev 日线）；`index-series` **有等价产物**（`panel_v2/thermometer/index_880008_series.json`，3656 根，`merged_last=20261106`）但端点没用；`amv_research` 无产物，现在的磁盘缓存就是事实产物。
3. **复盘不在 16:10 链上**：`review_six_cells_latest.json` 停在 11-05 22:16，11-06 没产——不能列进“盘后直读”。
4. **边界**：盘后直读窗口 = 产物 `date == 最近交易日` 起，到次日 **09:15** 止；09:15–09:30 只切实时类视图（竞价/分时/五档/逐笔），盘后类指标继续读昨日产物并标“基准=上一交易日收盘”。

### 据此修掉的三个风险

| 风险 | 处理 |
|---|---|
| 分时缓存读路径只看 `v/count`，存量 38 个 `intraday_*_20261107.json` 里 `date=20261106`、`fallback=true` → 今天打开会命中返回“昨天的分时” | 读路径补三道校验（`date8` 元数据、`payload.date`、`not date_fallback`）；存量 38 个已清；实测返回 `date=20261106 fallback=true` 且不再写盘 |
| `board_flow` / `rank_recent` / `struct_tag` 的进程内缓存键只有 `(ds, period, level)`，16:11–16:21 链重写库时可能把半成品缓存整晚 | 键里加库戳（`amv_board.db` mtime+size，1s 节流），库一变键就变 |
| 时段判断写了三遍、只看周末不判节假日 | **未收敛**（下一批）：统一走 `common.trading_calendar`，core 加门面，兼底 16:40 |

## 5. 未做（下一批）

| 端点 | 冷启动 | 备注 |
|---|---|---|
| `sector/board_flow` | 915 ms | 板块卡资金流列 |
| `breadth_880` | 947 ms | 盘中页涨跌家数 |
| `intraday/index-series` | 904 ms | 盘中页全A 日涨跌 |
| `selection` 四件套 | ~200 ms × 4 | 分散，可一处并发合并 |

实时档「当天第一次」仍需 ~7s：48 票分时是 eltdx 单连接串行，插件内并行会被 `core.quotes` 全局锁挡住。要根治得给门面加批量/多连接，属公共接口改动，另立任务。
