# sector 插件契约（旧 8002 → 新 8810 字段映射）

**插件**：`app/plugins/sector/`（id=`sector`，v0.6.0）
**挂载前缀**：`/api/p/sector`（核心负责，插件内只写相对路径）
**数据源**：`data/amv_board.db`（AMV 日级底座）+ `data/ths_board.db`（THS 分类池成分数）+ `data/track_pool.db`（成分股名称；可选）
**访问方式**：仅 `ctx.db_read(name)`；`data.write = []`；不写 `data/panel`。
**旧实现参考**：`夏天-activity/工具/dashboard/server.py`（8002）

---

## 1. 端点清单

| # | 新端点 | 旧端点（8002） | 状态 |
|---|---|---|---|
| 1 | `GET /api/p/sector/tree` | `GET /api/sector_tree`（= `api_catalog`）、`GET /tree` | 已实现 |
| 2 | `GET /api/p/sector/rank?date=&level=&period=` | `GET /api/sector_level?level=&date=&period=` | 已实现 |
| 3 | `GET /api/p/sector/series?code=&level=&days=&period=` | `GET /api/series?code=&level=&days=&period=`（`api_series2`） | 已实现 |
| 4 | `GET /api/p/sector/catalog` | `GET /api/catalog`（`api_catalog`） | 已实现，与 `/tree` 同体 |
| 5 | `GET /api/p/sector/search?q=&limit=20` | `GET /api/search?q=&limit=` | 已实现，名称/代码子串，同名保留更细层级 |
| 6 | `GET /api/p/sector/members/{code}?date=&limit=&full=&period=` | `GET /api/members?code=&date=&period=`（`api_members2`） | **已实现 v0.2**：`full=1` 全量现算（amt/amt_avg/contrib/d_contrib/amv_pct/real_pct/diverge/main_flow/rank/**sigs**）；`full=0` 仍为 stock_top 子集 |
| 7 | `GET /api/p/sector/rank_recent?level=&days=80` | `GET /api/rank_recent?level=` | 已实现，share 矩阵 |
| 8 | `GET /api/p/sector/leaders/{code}?days=20&top_n=10` | `GET /api/leaders/{code}` | 已实现，stock_top 逐日 TOP N |
| 9 | `GET /api/p/sector/manifest` | `GET /api/manifest` | 已实现只读子集（dates/counts/coverage） |
| 10 | `GET /api/p/sector/minute_rank/minute_series/minute_multi/minute_dates` | 8002 同名端点 | **v0.3 已实现**，与旧端 `==` |
| 11 | `GET /api/p/sector/rank_top?date=&level=&n=` | `GET /api/rank`（`ep_rank`） | **v0.4 已实现**，与旧端 JSON 全等（路径避让 `/rank`） |
| 12 | `GET /api/p/sector/trend?code=&days=` | `GET /api/trend`（`ep_trend`） | **v0.4 已实现**，与旧端 JSON 全等 |
| 13 | `GET /api/p/sector/board_top?sector=&date=&top=` | `GET /api/board_top`（`ep_board_top`） | **v0.4 已实现**，与旧端 JSON 全等 |
| 14 | `GET /api/p/sector/board_flow?date=&period=&level=` | `GET /api/board_flow`（`ep_board_flow`） | **v0.5 完成**：15 组（3 period × 5 level 组合）与旧端 JSON **全等**，含 `bull` |
| 15 | `GET /api/p/sector/contrib?code=&date=&period=` | `GET /api/contrib`（`api_contrib`） | **v0.5 完成**：20 组（4 板块 × 5 period）与旧端 JSON **全等**；口径走 `ctx.lib("amv_contrib")` |
| 16 | `GET /api/p/sector/score_trail?code=&period=&days=30` | `GET /api/score_trail?code=&period=`（`api_score_trail`） | **v0.6.0 完成**：轨迹逐值全等（885311.TI 278 只 / 881101.TI 29 只，差异 0）；形状有意不同（旧端 `rows` 是 `{code: trail}`，新版是 `[{code,name,score,mkt_rank,trail}]`，shadow 走专用比较器）；口径源 `工具/panel_scores.py` |
| 17 | `GET /api/p/sector/members/{code}?full=1` 的评分字段 | 同上 `api_members2` 的 `score`/`mkt_rank`/`sec_rank`/`trail`/`sec_total` | **v0.6.0 完成**：4 板块 × 3 周期逐值比对 0 差异（885338 仅 7 只并列互换，名次位置集合一致）；3876 成分耗时 1.64s |
| — | `GET /api/p/sector/rank?level=...`（当日流入/流出榜） | `GET /api/rank`（`ep_rank`，top_in/top_out/new/exit） | **v0.4 已实现为 `/rank_top`**（路径避让 `/rank` = `sector_level`） |

`/api/series_{code}.json`（`api_series`，固定 N_STORE=80 窗口）由新端点 `/series` 覆盖其全部字段，不单独开路由。

### 1.1 `/members?full=1` 字段与单位（v0.2，与旧 8002 逐值一致）

| 新字段 | 旧字段 | 口径 / 单位 |
|---|---|---|
| `amv` | （旧端行内已剔除） | 元，原始活跃市值；保留给下游算派生字段 |
| `amt` | `amt` | `round(amv / 1e8 × 10)`，亿元×10 整数 |
| `amt_avg` | `amt_avg` | 窗口（`period+1` 个交易日）内逐日 `amv` 均值，同口径取整 |
| `contrib` | `contrib` | `round(amv[ds] / daily_total[ds] × 1e6)`，×1e6 整数（前端 /1e4 = %） |
| `d_contrib` | `d_contrib` | `contrib[ds] − contrib[base]`，单位 pp |
| `amv_pct` | `amv_pct` | `amv[ds]/amv[base]-1`，2 位 |
| `real_pct` | `real_pct` | `close[ds]/close[base]-1`，取 `track_pool.db::kline`（前复权） |
| `diverge` | `diverge` | `amv_pct − real_pct` |
| `main_flow` | `main_flow` | `Σ stock_flow.main_flow`，窗口 `[dates[1], ds]`（旧端 `dates[-2]` 降序同义），万元，1 位 |
| `sigs` | `sigs` | 买点徽标：`buy_signal.db::stock_signal` 的 `daily_sigs + weekly_sigs` 合并（日在前），空为 `[]` |
| `sigs_date` | （旧端无） | 新增：`daily_date` 与 `weekly_date` 的较大值，供前端判新鲜度 |
| `bull` | `bull` | 个股四态多空（多/偏多/空/偏空/None），口径源 `amv_regime.stock_state`（经 `ctx.lib`）；只算返回行 |
| `rank` | `rank` | 板块内按 `amv` 降序（全量，不受 `limit` 影响） |
| `n` / `n_total` | 同 | `n` = 本次返回行数；`n_total` = 全量成分数 |

**`limit` 语义**：`full=1` 时 `limit=0`（默认）= 不裁剪；`INDEX` 层同旧端默认只返前 60（全 A 2181 只，前端卡顿治理）；其他层默认全量。上限 1000。`full=0` 时 `limit=0` → 200。

**名称**：主源 `data/stock_names.json`（eltdx 全量表，主链 `stock_names` 步日更；2026-10-31 由 `_tmp/stock_names_em.json` 迁入，经 `ctx.json_read("stock_names")`）；缺失回退 `track_pool.pool` → `stock_top`。实测概念层 278 只名称零差异。

`/members?full=1` 的字段单位：`amv` 元；`contrib` 为 `amv / daily_total.total` 的 0–1 比例（旧端是 ×1e6 整数）；`main_flow` 为 `stock_flow.main_flow` 原表单位（未换算）；`real_pct` 由 `track_pool.db::kline` 的 ds/base 收盘计算，`diverge = amv_pct - real_pct`。

---

## 2. `GET /tree` 字段映射

旧 `api_catalog()` 返回 → 新返回**逐字段同名同形状**。

| 字段 | 旧语义 | 新实现 |
|---|---|---|
| `levels` | `['L1','L2','L3','CONCEPT','INDEX']` | 同，常量 |
| `l1` / `l2` / `l3` / `concept` / `index` | `sector_meta` 按 `ord` 排列的节点数组 | 同（`SELECT * FROM sector_meta WHERE level=? ORDER BY ord`） |
| 节点 `code` `name` `level` `l1` `level_num` | `sector_meta` 原值 | 同 |
| 节点 `exclude` `is_unmap` | `bool(int)` | 同 |
| 节点 `n_members` | `ths_board.db` 的 `board` + `concept` + `broad_index` 三表 `n_members` | 同（池库不可用 → `null`，不报错） |
| `tree` | L1 节点 + `children`（`l1` 名称匹配的 L2） | 同 |
| `counts` | 各层条数 | 同 |
| `note` | 固定说明串 | 同 |

**当前实库值**（2026-10-21）：`{L1:0, L2:91, L3:231, CONCEPT:390, INDEX:12}`。
`L1=0` 是实库现状（`sector_meta` 无 `L1` 行），非本插件缺陷；`tree` 因此为空数组，与旧 8002 实测响应一致。

---

## 3. `GET /rank` 字段映射

| 新字段 | 旧字段（`/api/sector_level`） | 口径 |
|---|---|---|
| `date` | `date` | 缺省 = `MAX(date) FROM daily_total` |
| `level` | `level` | 缺省 `L2`；仅接受 `L1/L2/L3/CONCEPT/INDEX`，其他 → 400 |
| `period` | `period` | 仅 `1/3/5/10/20`，其他回落 `1` |
| `n` | `n` | 过滤后行数 |
| `rows[].code` | `code` | `sector_daily.code` |
| `rows[].name` | `name` | `sector_daily.name` |
| `rows[].l1` | `l1` | `sector_meta.l1` |
| `rows[].amv` | `amv` | `int(round(amv元 / 1e8 × 10))`，单位「亿元×10」 |
| `rows[].weight` | `weight` | `int(round(share × 1e6))`，前端 `/1e4` = % |
| `rows[].rank` | `rank` | `sector_daily.rank` |
| `rows[].amv_pct` | `amv_pct` | `amv[ds]/amv[lo]-1`，保留 2 位，`lo` = `period` 个交易日前 |
| `rows[].real_pct` | `real_pct` | `close[ds]/close[lo]-1`（`board_flow.close`，自算板块指数） |
| `rows[].diverge` | `diverge` | `round(amv_pct - real_pct, 2)` |

**行过滤**：`sector_meta.exclude=1` 或 `is_unmap=1` 的板块不进入 `rows`（旧 8002 同）。实库命中 `BK9999` 一行（`exclude=1, is_unmap=1`），故 `L2` 91 条元数据 → `n=90`。

**行序**：按 `code` 升序。实库验证 `sector_daily` 的 rowid 序与 `code` 序完全一致（L2/L3/CONCEPT/INDEX 四层均一致），故与旧实现的行序等价。

---

## 4. `GET /series` 字段映射

| 新字段 | 旧字段（`/api/series` = `api_series2`） | 口径 |
|---|---|---|
| `code` `level` | 同 | `level` 缺省按 `sector_meta` 反查；`SW:` 前缀 → `L1`；查不到回落 `L2` |
| `name` | `name` | 末行 `sector_daily.name`，回退 `sector_meta.name` |
| `l1` | `l1` | `sector_meta.l1` |
| `ma_n` | `ma_n` | 常量 `10` |
| `n_members` | `n_members` | `ths_board.db` 池成分数；不可用 → `null` |
| `dates[]` | `dates` | 升序；`ORDER BY date DESC LIMIT days` 后 reverse |
| `amv[]` | `amv` | 亿元×10 整数 |
| `ma[]` | `ma` | AMV 的 `MA_N=10` 前缀累积均值（前 10 日不足按已有天数平均），再转整数 |
| `weight[]` | `weight` | ×1e6 整数 |
| `rank[]` | `rank` | 原值 |
| `real_pct` `amv_pct` `diverge` | 同 | 见 §3，作用于末行日期 |
| `flow_calc` | 同 | `Σboard_flow.flow_calc / 1e8`，保留 2 位，单位亿元 |
| `flow_main` | 同 | `Σboard_flow.flow_main`，保留 1 位，单位万元 |
| `period` `unit` | 同 | 同 |

**`days`**：默认 60，上限 250（`MAX_DAYS`），超出裁剪不报错。旧实现默认 80（`N_STORE`）；差异见 §5。
**未找到**：`{"error":"not found","code":...,"level":...}` + HTTP 200，与旧实现一致（旧实现同样用 JSON error 而非 HTTP 错误码）。

---

## 4.1 旧 8002 的 `series` period 缺陷（新版未复刻）

旧 `api_series2` 把 `period` 原样以**字符串**塞回 `ep_board_flow`：

```python
fl = ep_board_flow({'date': [rows[-1]['date']], 'period': q.get('period', ['1'])[0], ...})
```

`ep_board_flow` 内部执行 `int(q['period'][0])`，于是 `'10'` → `1`、`'20'` → `2` → 归一回 1。
**实测**（2026-10-21，`881101.TI`）：

| 请求 | 旧 8002 返回 `period` | 旧 `amv_pct` | 旧 `flow_calc` |
|---|---|---|---|
| `period=1` | 1 | -0.63 | 46.63 |
| `period=3` | 3 | -9.39 | -14.82 |
| `period=5` | 5 | -15.11 | -33.97 |
| `period=10` | **1**（应为 10） | **-0.63**（应为 -16.66） | **46.63**（应为 -56.26） |
| `period=20` | **1**（应为 20） | **-0.63**（应为 -28.87） | **46.63**（应为 41.2） |

同一库、同一日，旧 `/api/sector_level?period=10` 与 `/api/board_flow?period=10` 都返回 `amv_pct=-16.66`，
证明是 `series` 端点自己丢了参数，不是口径差异。

**新版行为**：`/series?period=10` 返回 `period=10`、`amv_pct=-16.66`，与旧 `/api/sector_level` 逐值一致。
即：**新版不复刻旧 `series` 的 period bug**。若必须与旧 8002 逐字节 diff，此项会是已知差异；
建议旧端另行修（一行 `int(...)`），不在本插件范围。

---

## 5. 与旧 8002 的差异 / 未覆盖项

**差异（有意为之）**

1. `days` 默认值 60（旧 80），上限 250（旧无上限，直接 `LIMIT ?`）。理由：v1 首块样板只服务 60 日卡片视图；上限防全表扫描。
2. `flow_calc` / `flow_main` 的窗口按旧口径 `[flow_lo, ds]`（`flow_lo` = `period` 日前那天），基准日 `lo` 只用于 `real_pct`/`amv_pct`。与旧实现逐值一致，但旧实现用 `level` 参数过滤全层，新版只算请求涉及的 code（`/rank` 传当层全量，`/series` 传单 code）。
3. 库不可用 → HTTP 503（旧实现 500 + `{"error":...}`）。契约 §6 要求核心统一包装错误，插件主动抛 `HTTPException` 更可诊断。
4. `level` 非法 → HTTP 400（旧实现静默按传入值查询，返回空 `rows`）。
5. `/series` 的 `period` 不再复刻旧端 bug（详见 §4.1）。

**未覆盖（v1 不做，需要时另开工单）**

| 旧端点 | 内容 | 原因 |
|---|---|---|
| `/api/rank`（`ep_rank`） | 当日流入/流出 TOP（`_top_k`：10% 夹 10~30）+ `new`/`exit` | **已搬（v0.4，路径 `/rank_top`）**；与旧端 JSON 全等 |
| `/api/trend`（`ep_trend`） | 板块趋势 | **已搬（v0.4，`/trend`）**；与旧端 JSON 全等 |
| `/api/board_top` | 板块内个股 TOP + 名次变化 | **已搬（v0.4，`/board_top`）**；与旧端 JSON 全等 |
| `/api/board_flow` | 板块资金/真实涨跌/多空全层批量 | **已搬（v0.5，`/board_flow`）**；含 `bull`，15 组全等 |
| `/api/members`（`api_members2`） | 成分股 + 买点徽标 | **已搬（v0.2）**：买点徽标 `sigs` 已接 `buy_signal.db`，见 §1.1 |
| `/api/catalog.json` `/api/manifest.json` | 全量清单 + 覆盖率 + `store_meta` | 前端切卡片后再补 |
| `/api/search` | 跨层关键词搜索 | 非只读样板必需 |
| `/api/contrib` | AMV 贡献分项（涨幅/换手驱动） | **已搬（v0.5，`/contrib`）**：经 `ctx.lib("amv_contrib")`（CONTRACT §8.1 口径库白名单），20 组全等 |
| `/api/score_trail` `/api/card_live` | 个股评分轨迹 / 实时口径 | 依赖 `buy_signal` + 8800 转发 |
| `/api/board_kline` | 板块 K 线 | 属 `kline` 插件域 |
| `/api/minute_*` | 分钟级 AMV | 依赖 `amv_minute.db`，未在 `data.read` 声明 |
| `/api/hcl*` `/api/index_closes` | 指数收盘/合成 | 属 `amv`/`kline` 域 |
| `/api/leaders_{code}.json` | 板块内个股榜 | 依赖 `stock_top`，留给后续 `sector.members` |
| ~~`/api/board_flow` 的 `bull`~~ | **v0.5 已搬**：经 `ctx.lib("amv_regime")`（CONTRACT §8.1），15 组全等 |

**口径库（v1.2 已定）**：`amv_regime.py` 与 `amv_contrib.py` 经 CONTRACT §8.1 口径库白名单接入，插件不重写公式也不直接 import 工具目录。`_top_k`（榜单条数）已在插件内按旧端常量与公式实现（10% 夹 10~30）。

---

## 6. 验收对照

| 契约 §9 项 | 落地 |
|---|---|
| 1 manifest 齐全、id=目录名 | `test_manifest_id_matches_directory` / `test_manifest_data_read_only` |
| 2 `register` 返回 APIRouter，路径在 `/api/p/sector` 下 | `test_register_returns_apirouter` / `test_tree_200` / `test_rank_200_*` / `test_series_200` |
| 3 无直连数据库、`data.write` 为空 | `test_no_direct_db_connect` / `test_backend_only_reads_via_ctx` / `test_manifest_data_read_only` |
| 5 `frontend.js` 卡片 id 唯一、有 mount/unmount | `test_manifest_card_ids_unique` / `test_frontend_declares_same_card` |
| 4 `/_plugins/health` 中 `ok` | `test_real_core_mounts_sector` |
| 6 改一个字段只动一个插件目录 | 本插件目录 + `docs/sector_contract.md` + `app/core/paths.py`（新增白名单项）；v0.2 因新增 `data.json` 机制动过一次 core，见 `CONTRACT.md` v1.1 |

**测试**：`envs\qmt312\Scripts\python.exe -m pytest app\plugins\sector\tests -q` → **28 passed**；
全套 → **82 passed**。全部使用 `tmp_path` 临时 sqlite fixture，
不读写生产库、不写 `data/panel`。

**端到端（真实生产库，只读）**：起 8810 同款 `create_app`（临时端口 8831），`/_plugins/health` 中
`{"id":"sector","ok":true,"load_ms":45.9}`。与 8002 逐字段 diff：

| 端点 | 比对范围 | 结果 |
|---|---|---|
| `/tree` | `counts` + `l1/l2/l3/concept/index/tree` 全字段 | **全等**（91/231/390/12） |
| `/rank` L2 | 90 行 × 9 字段 | **全等** |
| `/rank` L3 | 230 行 | **全等** |
| `/rank` CONCEPT | 390 行 | **全等** |
| `/rank` INDEX | 12 行 | **全等** |
| `/rank` L2 `period=3/5/10/20` | 90 行 | **全等** |
| `/series` | `881101.TI` `881117.TI` `884001.TI` `885311.TI` `000001.SH` `BK9999` `881273.TI`（各 `days=60`） | **diffs=0** |
| `/series` `period=3/5` | `881101.TI` | **diffs=0** |
| `/series` `period=10/20` | `881101.TI` | 差异 6 字段，**旧端 bug**，见 §4.1 |

**前端**：Node 24 + 最小 DOM stub 跑 `mount → 切层级 → 切周期 → unmount`，8 项断言全过
（rank 表/成分股表/徽标短名/成员接口 `full=1`/切层级重拉/切周期重拉/卸载清状态）；
`import()` 正常，卡片 id `sector.board`，`mount/unmount` 均为函数。

**`/members?full=1` 真实库比对（2026-10-22）**：4 个板块 × 5 个 period（1/3/5/10/20）逐行逐字段，
共 1,184 行 · 8 字段 = 9,472 值：

| 板块 | period 1/3/5 | period 10/20 |
|---|---|---|
| `881101.TI`（L2，29 只） | **0 差异** | 11/12 处差异，全为北交所 920xxx |
| `884001.TI`（L3，10 只） | **0 差异** | 7/8 处，全为 920xxx |
| `885311.TI`（概念，278 只） | **0 差异** | 24/31 处，全为 920xxx |
| `000001.SH`（INDEX，60 只） | **0 差异** | **0 差异** |

920xxx 差异的根因（2026-10-22 定位）：**vipdoc 北交所日线缺一年数据**。
实测 `vipdoc/bj/lday/bj920087.day` 共 695 根，`20250930` 之后直接跳到 `20261009`，
中间 374 个自然日（2025-10 ~ 2026-10）没有任何 bar；`bj` 目录 370 个文件里 253 个的
最后 80 根窗口不含 `20261008`。旧 8002 `_stock_closes()` 只读 vipdoc 尾部 80 根 →
`base` 日（period=10 时是 2026-10-08）取不到收盘 → `real_pct=None`、`amv_avg` 少算一天。
新版 `_kline_closes()` 读 `track_pool.db::kline`（有完整 `20261008`）→ 给出真实值。
**结论：旧端受 vipdoc 数据缺口拖累，新版为准**。vipdoc 缺口本身属数据源问题，
不在本插件范围，已记入 `STATUS.md` 已知限制。

（附带修正：`_guess_ts_code` 的 920xxx → 北交所判定也已补齐，与旧端 2026-10-07 的修法一致。）

**回归固化**：
- `test_members_full_computes_amv_contrib_rank`：`amt/contrib/d_contrib/amv_pct/real_pct/main_flow` + `sigs/sigs_date` 全字段断言。
- `test_manifest_data_read_only`：`data.read` 四项 + `data.json=["stock_names"]`。
- `test_series_amv_pct_does_not_leak_across_levels`：实库 `BK9999` 同时在 L2（80 亿）与 L3（20488 亿），
  不按 level 过滤会取错基准。首版实现漏了 `AND level=?`，被真实 diff 捕获，已修并固化为测试。
- `test_connections_are_closed`：`ctx.db_read` 每次新建连接，端点必须关闭，否则句柄泄漏。
- `test_real_core_mounts_sector`：真实 core 发现→加载→挂载→端点 200，且 `backend.py` 不被静态暴露。
- `test_frontend_api_contract`：shell 的 `ctx.api.get` 直接返回解析后的 JSON（失败抛异常），
  首版前端误当 `Response` 用了 `.status/.json()`，已修并固化为测试。
