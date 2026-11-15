# 旧前端 85 个 API 路径 · 迁移清单

> 来源：扫 `engine/web/{shell.html,index.html,kline_lc.js,kline_f10.js,kline_quote5.js,kline_order.js,review_chart.js}`
> 得到 **85 个**被引用的 `/api/...` 路径（去重后）。这张表是迁移工作量的唯一依据。

## 关键事实（2026-10-23 实测）

- 旧前端**不含任何硬编码 `127.0.0.1:8800`**，所有 API 走相对路径 → 同源托管即可复用
- `shell.html` 只有一处 `const ENGINE = 'http://127.0.0.1:8800'`，改成 `window.location.origin` 即可
- `shell.html` 用 `<iframe>` 加载 `index.html`，通过 postMessage 通信
- 因此：**把前端文件复制到 8810 静态目录 + 改一个常量**，旧界面就能跑在新后端上

## 分类

### A. 静态 JSON 转发（9）

| 路径 | 数据源 |
|---|---|
| `GET /api/dashboard.json` | 组装（§C） |
| `GET /api/battle_plan.json` | `data/panel/battle_plan.json` |
| `GET /api/amv_live.json` | `data/panel/amv_live.json` |
| `GET /api/amv_regime_latest.json` | `data/panel/amv_regime_latest.json` |
| `GET /api/position_cap_latest.json` | `data/panel/position_cap_latest.json` |
| `GET /api/alert_todo.json` | `data/panel/alert_todo.json` |
| `GET /api/alert_plan.json` | `data/panel/alert_plan.json` |
| `GET /api/push_types.json` | `data/panel/push_types.json` |
| `GET /api/concept.json` | `data/panel/concept.json` |

### B. 行情 / K线（已有新端点，需格式转换）（8）

| 路径 | 对应新端点 |
|---|---|
| `GET /api/kline/{ts_code}` | `/api/p/kline/bars/{ts_code}`（字段名不同：`t/o/h/l/c/v` vs `date/open/...`）|
| `GET /api/kline_bg/{ts_code}` | `/api/p/kline/bg/{ts_code}`（结构不同：`items{}` vs `dates[]+states[]`）|
| `GET /api/intraday/{ts_code}` | `/api/p/kline/intraday/{ts_code}`（几乎同构）|
| `GET /api/intraday_multi/{ts_code}` | `/api/p/kline/intraday_multi/{ts_code}`（几乎同构）|
| `GET /api/quote5/{ts_code}` | `/api/p/kline/quote5/{ts_code}`（几乎同构）|
| `GET /api/volume_profile/{ts_code}` | `/api/p/kline/volume_profile/{ts_code}`（同构）|
| `GET /api/volume_ticks/{ts_code}` | **无**，需实现（逐笔压缩 `[[minute_idx, price_milli, vol]]`）|
| `GET /api/f10_brief/{ts_code}` | **无**，需实现（eltdx 资金流 + F10 HTTP）|

### C. 组装型只读（12）

| 路径 | 说明 |
|---|---|
| `GET /api/dashboard.json` | `{meta,engine,account,positions,orders_today,environment,alerts,risk_locks}` |
| `GET /api/positions.json` | `{meta,date,updated_at,holdings,selection,track_signals,source_audit}` |
| `GET /api/real_positions` | 含 `positions_list`（现价/PnL/权重，走 eltdx 快照）|
| `GET /api/paper_account` | 纸面账户 |
| `GET /api/ledger` | 台账 |
| `GET /api/gates` | `{version,updated_at,gate1,gate2,gate3}` |
| `GET /api/status` | `{code,label,state,mode,battle_plan_signed,kill_switch_active,kill_reason,trade_mode}` |
| `GET /api/alerts` | 告警列表 |
| `GET /api/search/all?q=` | 全市场搜索 |
| `GET /api/review.md` | 复盘 markdown |
| `GET /api/review_chart.json` | 复盘图数据 |
| `GET /api/selection` + `_board_options` + `_pool` + `_prefs` | 选股页四端点 |

### D. 板块（4）

| 路径 | 说明 |
|---|---|
| `GET /api/sector_card.json` / `sector_intraday.json` / `sector_spark.json` / `sector_track.json` | 板块相关 JSON |
| `GET /api/sector/health` | 板块服务健康 |
| `GET /api/etf_sector_map.json` | ETF 板块映射 |
| `GET /api/breadth_880.json` / `temperature_latest.json` / `amv_research.json` | AMV 扩展 |

### E. 自选池（9，含写）

| 路径 | 类型 |
|---|---|
| `GET /api/watchlist.json` | 读 |
| `GET /api/watchlist/groups` | 读 |
| `GET /api/watchlist/state` | 读 |
| `GET /api/watchlist/search` | 读 |
| `POST /api/watchlist/add` / `remove` / `move` / `reorder` | **写** |
| `POST /api/watchlist/group/{add,remove,rename,reorder}` | **写** |

### F. 交易链（写，第 3 批）

| 路径 | 说明 |
|---|---|
| `POST /api/order/place` / `cancel` | 下单/撤单 |
| `GET /api/order/list` | 委托列表 |
| `POST /api/condition/{add,cancel}` | 条件单 |
| `GET /api/condition/list` / `evaluate` | 条件单查询 |
| `POST /api/sign` | 作战表签收 |
| `POST /api/kill` | 急停 |
| `POST /api/toggle` | 模块开关 |
| `POST /api/module_switches_batch` | 批量开关 |
| `POST /api/cancel` | 撤单（别名）|
| `GET /api/trades/{ts_code}` | 买卖点标记（只读）|
| `POST /api/alert_plan/{toggle,delete}` | 预警增删 |
| `POST /api/signal_watch/{toggle,cancel}` | 择机单 |
| `POST /api/ui_notes/{add,resolve,update}` | UI 备注 |
| `GET /api/ui_notes/list` | |
| `POST /api/selection_pool/{add,remove}` | 选股池写 |
| `POST /api/amv_refresh` | AMV 重算 |
| `GET /api/ops.json` / `POST /api/ops/run` | 运维 |
| `POST /api/sector/restart` | 板块服务重启 |
| `POST /api/sector_track_backfill` + `_status` | 板块轨迹回填 |
| `GET /api/stock_amv/{ts_code}` / `sector_amv/{code}` / `sector_kline/{code}` | 板块个股 AMV |

## 分批计划

| 批次 | 范围 | 状态 |
|---|---|---|
| **第 1 批** | A 全部（9）+ C 的 dashboard/positions/gates/status/alerts/search + real_positions | **已派 DSHana**（taskId `app:dshana:77a1c3bb`）|
| **第 2 批** | B 全部（8，含新实现 volume_ticks / f10_brief）+ C 剩余（paper_account/ledger/review/selection） | 待派 |
| **第 3 批** | D 板块（7） | 待派 |
| **第 4 批** | E 自选池（9，含写） | 待派 |
| **第 5 批** | F 交易链（约 27，写操作，需逐个评审） | 待派 |

## 静态托管（我可以并行做，不依赖上面批次）

1. 复制 `engine/web/{shell.html,index.html,kline_*.js,review_chart.js,themes.css}` → `panel-v2/app/web/legacy/`
2. `shell.html` 的 `const ENGINE = 'http://127.0.0.1:8800'` → `const ENGINE = window.location.origin`
3. core 加静态路由：`GET /legacy/*` → `app/web/legacy/`
4. 入口：`http://127.0.0.1:8810/legacy/shell.html`
5. 回退：继续用 `http://127.0.0.1:8800`（原样未动）
