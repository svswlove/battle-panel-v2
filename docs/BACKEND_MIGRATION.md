# 后端迁移矩阵（先搬后端，前端后接）

**原则**：按域搬到 `/api/p/<id>`；旧路由保留；不复制 11k 行 `server.py`；一个指标一个 owner；写接口/交易链不搬；前端只是消费者。

## 1. 已搬（只读）

| 域 | 新端点 | 旧端点 | 状态 |
|---|---|---|---|
| sector | `/tree`、`/rank`、`/series` | 8002 `/api/sector_tree`、`/api/sector_level`、`/api/series` | 全量 diff PASS |
| sector | `/catalog`、`/search` | 8002 `/api/catalog`、`/api/search` | 与旧端精确相等 |
| sector | `/members/{code}?full=1` | 8002 `/api/members`（`api_members2`） | **v0.2 完成**：amt/amt_avg/contrib/d_contrib/amv_pct/real_pct/diverge/main_flow/rank/**sigs**；period 1/3/5 逐值相等，10/20 差异全归因旧端 920xxx 缺陷 |
| sector | `/rank_recent`、`/leaders/{code}`、`/manifest` | 8002 同名端点 | 已实现只读矩阵/TOP/清单 |
| sector | `/minute_rank`、`/minute_series`、`/minute_multi`、`/minute_dates` | 8002 同名端点 | **v0.3 完成**：6 组请求与旧端 `==` 精确相等（含 period=1/5、指定日期） |
| sector | `/rank_top`、`/trend`、`/board_top` | 8002 `/api/rank`、`/api/trend`、`/api/board_top` | **v0.4 完成**：三端点与旧端 JSON 全等（含 n=1/3、CONCEPT 层、days=120） |
| sector | `/board_flow` | 8002 `/api/board_flow` | **v0.4 完成**：flow/main_days/amv_pct/real_pct/diverge 全等；`bull` 不搬（依赖工具目录） |
| amv | `/live`、`/regime`、`/position_cap`、`/daily`、`/history` | 8800 `/api/amv_live.json` 等 | 三份原始 JSON 精确相等；历史序列只读 `daily_total` |
| amv | `/hcl` | 8002 `/api/hcl` | **v0.2 完成**：只读 `hcl_calc.py` 落盘，除 `ok` 外逐字段 `==` |
| amv | `/breadth`、`/all_a` | —— | **v0.3/v0.4 完成**：全 A 广度 `amv_breadth_latest.json`、全 A 快照 `all_a_latest.json`，均只读透传 |
| watchlist | `/quotes`、`/items?quotes=1`、`/levels` | —— | **v0.2/v0.3 完成**：行情拼接 `watchlist_quotes.json`；关键位只读 `focus_levels.json`（默认只返 effective=true）；条目顺序对齐旧 8800（股票前、板块后） |
| watchlist | `/summary`、`/items`、`/groups` | 8800 `/api/watchlist.json` | 只读 panel JSON，未做行情富化 |
| battle_plan | `/plan`、`/alerts`、`/positions` | 8800 `/api/battle_plan.json`、`/api/alert_todo.json`、`/api/real_positions` | 原始字段相等；`market_value_total` 已补（纯求和）；`positions_list` 需实时行情，不搬 |
| kline | `/bars`、`/quote` | 8800 `/api/kline`、`/api/quote5`（部分） | 历史日线子集，`intraday=false` |

## 2. 下一批建议（按风险/收益）

1. **sector contrib**：`/api/contrib`（涨幅/换手驱动分项）；旧端 import `工具/amv_contrib.py`，需先定「插件能否复用工具目录」的契约条款。
2. **sector score_trail**：`/api/score_trail`（个股评分轨迹）；口径在旧 `server.py` 内，需要先确认评分已稳定不再变。
3. **板块 K 线**：`/api/board_kline` 属 kline 域，依赖 `tdx_sector_api`（自算板块指数，vipdoc 二进制）。
4. **指数收盘 / hcl_idx**：见 §2.1，需新增「二进制目录白名单」类别，不在 v1 范围。
5. **battle_plan `positions_list`**：旧端现场算实时报价/PnL；新版无行情源，只给 `market_value_total`（纯求和），`positions_list` 继续回旧端。

### 2.1 明确不搬（附原因）

| 旧端点 | 原因 |
|---|---|
| `/api/index_closes` | 旧端直读 `C:\new_tdx_zyb\zd_zyb\vipdoc\{sh,sz}\lday\*.day` 二进制；`track_pool.db::kline` 只有 4 年（20210802 起）且缺 `000510.CSI`，而旧端给 15 年（20100428 起）。迁过来是**降级**（少 11 年 + 3 处 0.01 分位差），不切 |
| `/api/hcl_idx` | 指数口径 HCL，同上依赖 vipdoc；行业口径（`/api/hcl`）已迁 |
| `/api/card_live` | 8002 反向代理 8800 实时口径；新版本永久只读收盘，不接实时 |

## 3. 不搬

- 8800 全部写接口：下单、撤单、签收、改单、手动持仓、`module_switch`、`alert_plan`、`ticket_policy`、条件单。
- `amv_refresh`（触发 AMV 重算）。
- QMT/交易链、账本、急停状态机。
- 8002 的写操作/缓存刷新（如有）。

## 4. 验收

每个后端域：旧/新同请求字段级 diff=0（或明确的“部分”字段清单）；测试用临时 sqlite，不碰生产库；旧端口一行不动；新端点只读。
