# 自选池打开慢 · 批量 spark 端点（2026-11-04）

> 主人：「作战面板的自选池打开速度真的好慢，已经优化了好几次了，但还是慢，要等好久」
> 结论：**前端 tPaint 中位 1491 ms → 81 ms，请求 37 条 → 1 条，载荷 1.5 MB → 64 KB。**
> 口径与 `/api/kline` 逐值相等（45 根 × 6 序列 × 37 票，0 处差异）。

## 1. 现场（改前实测，不是猜）

### 浏览器端到端（`open_perf.cdp.mjs`，8810，3 轮）

| 里程碑 | 改前 |
|---|---|
| tHead（卡头出字） | 16–63 ms |
| tCard（第一张卡） | 16–63 ms |
| **tPaint（当前组 37 张卡副图落线）** | **3094 / 1491 / 1283 ms，中位 1491** |
| 切走再切回 | 17–24 ms |
| 请求 | `/api/kline/{code}` **37 条**，合计 1.55 MB |

### 服务端拆解（37 票，串行 4174 ms、票均 112.8 ms、载荷 1506 KB）

| 环节 | 累计 | 票均 | 说明 |
|---|---|---|---|
| `build_mf`（动能K） | 1622 ms | 43.8 | **自选卡副图不用** |
| `_amv_map`（个股 AMV） | 1178 ms | 31.9 | 单票一次 sqlite 连接 + 区间查询 |
| `_latest_bars`（日线） | 771 ms | 20.8 | 真取数，core 门面全局限流，无法并行 |
| `_boards`（行业/概念） | 181 ms | 4.9 | 自选卡不用 |
| `_float_shares`（流通股） | 143 ms | 3.9 | 只为 turnover，自选卡不用 |
| `_amv_series_out` / `_yellow_white` | 62 ms | 1.3 | 纯计算 |

合计「不用但每票都算」的部分占 **84 %**。

### 载荷构成（单票 49.1 KB）

| 段 | 占比 | 自选卡用 |
|---|---|---|
| `bars` 160 行 × 15 字段 | 76 % | 只用末 45 行的 7 个字段 |
| `mf` | 13 % | ❌ |
| `indicators`（与 bars 内字段重复） | 5 % | ❌ |
| `boards` | 5.5 % | ❌ |

## 2. 改动（2 个生产文件 + 2 个测试 + 1 份文档）

| 文件 | 改动 |
|---|---|
| `app/plugins/compat_kline/backend.py` | 新增 `GET /api/kline_sparks?codes=&tail=&limit=`；`_amv_maps_bulk()` 一次 IN 查询取多票 AMV；模块级 120 s TTL 缓存 |
| `app/plugins/watchlist/frontend.js` | 盘后档 `hydrateEodStocks` 改走批量端点；批量失败退回逐票（`hydrateEodOneByOne`，不退化） |
| `app/plugins/compat_kline/tests/test_kline_sparks.py`（新） | 8 例：口径逐值相等 / 短历史白线 / 瘦身 / 多票 / 缓存 / 400 / 坏码 / AMV 缺失不编造 |
| `app/plugins/watchlist/tests/test_watchlist_frontend.py` | 新增 `test_open_uses_batch_sparks`（批量路径 + 兜底都在） |
| `docs/compat_kline.md` | 端点表加第 9 条，写明为什么存在 |

**口径零复制**：黄白线仍 `_yellow_white`（→ `ctx.lib("indicators")`），AMV 三线仍
`_amv_series_out`，只是把「每票一次查询」换成「一次查询取多票」。
一个指标一个 owner 不变：AMV 仍来自 `amv_board.db`（归 amv 插件），kline 只读。

### 两个关键坑（写在这里防下次踩）

1. **AMV 三线必须在裁到 `limit` 的全长上算，再取末 `tail` 根**。先裁再算会让 `amvMa`
   前 9 根恒为 null、`amvWhite`（EMA）整段偏移 —— 实测 76 处不一致。
2. **历史不足 `limit` 根（次新股）时黄白线不能整段置空**。`/api/kline` 走的是
   `_kline_payload` 的兜底分支（现算），照抄才一致 —— 否则 `301513.SZ` 白线差 2 处。

## 3. 验证（全部实测）

| 项 | 结果 |
|---|---|
| 口径一致性 | 37 票 × 45 根 × 6 序列 = **9990 个数值，0 处差异**（与 `/api/kline` 逐值比） |
| 变异验证 | 把「先裁再算」改回去 → `test_sparks_matches_kline_series` 变红；复原变绿 |
| 端点耗时（37 票） | 冷 **870 ms** / 热 **5–29 ms** / 64 KB |
| 浏览器端到端（8898 同码） | tPaint **81 / 44 / 845 ms，中位 81**；请求 1 条 `/api/kline_sparks`（66 KB） |
| 切走再切回 | 21 / 17 / 24 ms（原 17–24 ms，未退化） |
| 插件回归 | `pytest app/plugins/compat_kline/tests app/plugins/watchlist/tests` → **115 passed** |
| 门禁 | `cutover_gate.py`：我的两个文件不在任何 FAIL 项里（三项 FAIL 均为既有：battle_plan 结构漂移、emotion 写代码、kline/sector 前端 hex） |

## 4. 边界与回退

- 不改 `/api/kline` 的既有行为（一条代码路径都没动，只新增函数与路由）。
- 前端批量失败自动退回逐票老路径，最坏情况 = 改前行为。
- 回退：删 `app/plugins/compat_kline/backend.py` 里 `# /api/kline_sparks` 整段 +
  `register()` 里的 `kline_sparks` 路由；`git checkout -- app/plugins/watchlist/frontend.js`。
- `_sparks_cache` 是进程内内存，进程重启即清空，不落盘。

## 5. 未做（有意）

- **冷取 870 ms 没再往下压**：剩余成本是 37 次 `ctx.quotes.call("bars.get")`，
  core 门面是「单例连接 + 全局锁」（`quotes.py::_LOCK`），插件内并行化无效。
  要再压必须给门面加 bars 缓存或批量接缝 —— 属公共接口改动，另立。
- TTL 120 s 的依据：前端 `EOD_TTL = 300000`（5 分钟）。后端 TTL 若比前端短，
  就变成「每次前端重取都刚好过期」，把冷取写进日常路径。
