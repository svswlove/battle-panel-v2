# compat_misc 插件（其余只读端点）· 逐端点报告

- 派单：01 → DSHana ｜ 日期：2026-10-23 ｜ 任务书：`docs/task_compat_misc.md`
- 代码：`app/plugins/compat_misc/{plugin.json,backend.py}`、`app/plugins/compat_misc/tests/test_compat_misc.py`
- 键集基线：`docs/misc_samples/keys_expected.json`（+ `README.md` 说明为何没有 curl 样本）
- **状态：代码写完了，一行都没跑过（本子代理无 shell）。标「未运行验证」，等 01 验收。**

## 0. 先说三件必须知道的事

### 0.1 没有 curl 样本（任务书 §2.1 没做到）

任务书要求「先 `curl http://127.0.0.1:8800/api/...` 存样本当契约，再按同样字段拼」。
**我没有 shell，curl 一次都没跑。** 替代做法是**双源对齐**：

| 证据级 | 用在哪 | 可靠性 |
|---|---|---|
| **旧仓源码逐行** | 装配型端点（ops / order_list / trades / ledger / paper_account / selection / review_chart / **3 个 AMV 端点**） | 高：旧端 `return` 字面量逐键抄，含分支 |
| **磁盘实读**（2026-10-23） | 补「旧端从别的模块带出的键」（`paper_account` 账本顶层、`selection_filter` 偏好键、`selection_latest` 快照键） | 高：那就是当日真实产物 |
| **无法验证** | `review.md` 的 `charts`/`paper` 分支、`ops.json` 的 `tasks[]`/`daemons[]` 元素键 | 中：从 `return` 字面量抄，但没有响应样本交叉印证 |

**01 要做的一步**：跑 `docs/misc_samples/README.md` 里那段 PowerShell，把 20 个真响应落成
`docs/misc_samples/*.json`，再 `python tools\compare_legacy.py --only ...` 比对。
`keys_expected.json` 就是拿来跟真样本对账的（差在哪 = 我抄错在哪）。

### 0.2 16 个端点里只有 3 个能真取到数

任务书 §2.4 允许「缺数据源就返回同构空骨架」。按白名单实况（**未改 `paths.py`**）：

- ✅ **真实现 3 个**：`/selection_board_options`（`ths_board.db` + `panel/watchlist.json`）、
  `/stock_amv/{ts_code}`（`amv_board.db::stock_state`）、`/sector_amv/{code}`
  （`amv_board.db::sector_meta/sector_daily`）。
- 🟡 **半实现 2 个**：`/trades`（`points` 空，但 `cost.live` 真读 `real_positions.json`）、
  `/sector_kline`（改走 `ctx.quotes` 日线，见 §2.13）。
- ⬜ **骨架 11 个**：数据源不在任何白名单（详见 §5 清单）。

### 0.3 一条 POST 都没有

`register()` 只 `@router.get`，测试里两条独立断言把这件事钉住：
路由方法集 ⊆ {GET, HEAD}（`test_routes_complete_and_no_post`），以及 HTTP 层
`post/put/delete` 全 405（`test_only_get_endpoints_via_http`）。

---

## 1. 端点总表

| # | 路径 | 优先级 | 实现度 | 数据源（应为→实际） |
|---|---|---|---|---|
| 1 | `GET /api/ops.json` | 1 | 骨架 | `data/panel/ops_status.json` → **未登记 READABLE_PANEL** |
| 2 | `GET /api/order/list` | 1 | 骨架 | `engine/state/manual_orders_state.json` → **state 红线** |
| 3 | `GET /api/trades/{ts_code}` | 1 | 半 | `engine/state/paper_trades.json` + `panel/real_trades_{today,history}.json` → 均不可读；**成本线读 `real_positions.json`（可读）** |
| 4 | `GET /api/condition/list` | 2 | 骨架 | `engine/state/conditional_orders.json` → 红线 |
| 5 | `GET /api/paper_account` | 2 | 骨架 | `engine/state/paper_account.json` → 红线 |
| 6 | `GET /api/ledger` | 2 | 骨架 | 同上 + `real_trades_today.json` → 红线/未登记 |
| 7 | `GET /api/selection` | 2 | 骨架 | `data/panel/selection_latest.json` → 未登记 |
| 8 | `GET /api/stock_amv/{ts_code}` | 2 | **真** | `amv_board.db::stock_state` ✅ |
| 9 | `GET /api/sector_amv/{code}` | 2 | **真** | `amv_board.db::sector_meta/sector_daily` ✅ |
| 10 | `GET /api/sector_kline/{code}` | 2 | 半 | `ths_index.db` / vipdoc `.day` → 都不在册；改 `ctx.quotes` ✅ |
| 11 | `GET /api/selection_board_options` | 3 | **真** | `ths_board.db` + `panel/watchlist.json` ✅ |
| 12 | `GET /api/selection_pool` | 3 | 骨架 | `data/panel/selection_pool.json` → 未登记 |
| 13 | `GET /api/selection_prefs` | 3 | 骨架 | `data/panel/selection_filter.json` → 未登记 |
| 14 | `GET /api/ui_notes/list` | 3 | 骨架 | `engine/state/ui_notes.json` → 红线 |
| 15 | `GET /api/review.md` | 3 | 骨架 | `夏天-activity/每日复盘/*.md` → MD 目录不在册；且无 markdown 依赖 |
| 16 | `GET /api/review_chart.json` | 3 | 骨架 | MD + 11 个源（4 个不可读）+ `track_pool.db`/`ths_index.db` 未登记 |

---

## 2. 逐端点明细

### 2.1 `/api/ops.json`（🔴 骨架）

- **旧端**：`engine/server.py::api_ops` → `ops.ops_status.build(force=refresh)`
  （`engine/ops/ops_status.py::_aggregate`）；15s TTL 缓存，`?refresh=1` 绕缓存。
- **旧端键**（源码 return 字面量，用 `data/panel/ops_status.json` 实读交叉核对）：
  `{meta{updated_at,today,trade_date,is_trading_day,in_session,registry_ok,registry_error,
  task_query_error,proc_query_error,source,counts{tasks,daemons},
  coverage{tasks_unknown,daemons_unknown,daemons_unregistered},manual{enabled,note}},
  summary{10 键}, tasks[{12 键}], daemons[{12 键}], freshness[{key,label,latest,source,state}]}`
- **数据源**：`data/panel/ops_status.json`（**磁盘上确实存在**，2026-10-23 17:33 写）。
- **为什么是骨架**：① 该名不在 `paths.READABLE_PANEL`；② 更根本的是旧端这个端点**每次请求都跑
  PowerShell/WMI 子进程并回写生产 JSON** —— 新契约禁子进程、禁写生产文件，所以即使补了白名单
  也只能读**快照**（语义变化：不再实时探测，改为读最后一次聚合的落盘结果）。这条要 01 拍板。
- **实现**：先 `_panel(ctx,"ops_status")` 试读（manifest 也没声明，恒 PermissionError→None），
  拿不到就返回 `OPS_SKELETON` 深拷贝，`meta.source` 写明原因、`meta.registry_ok=false`
  （**不谎报「一切正常」**）、`summary` 全 0、三个数组空。
- **前端影响**：后台 Tab 会显示 0 个任务/0 个常驻。比「全部显示正常」安全。

### 2.2 `/api/order/list`（🔴 骨架）

- **旧端**：`api_order_list` → `ManualOrderService.list_orders/list_conditions/account_view`
  → `{ok, mode, date, orders[], conditions[], account{}, metrics{}}`，`mode=live` 另加 `qmt_reports`。
- **`account` 键**（`core/manual_orders._account_view`）：`{available_cash,total_asset,
  frozen_cash,positions,updated_at}`。
- **数据源**：`engine/state/manual_orders_state.json`（`{"version":1,"frozen":{},"updated_at":…}`）
  与 `paper_trades.json` —— **全在 `engine/state/`，红线，不申请**。
  另外 `metrics` 需 ATR14/MA5（`_order_metrics` 走 eltdx 行情 + `collectors.atr_preheat` 口径库，
  该库未登记）。
- **实现**：`ok=false`、`mode=null`（旧端读 `engine_state.trade_mode`，不可得**不猜**）、
  `date` 缺省今日 8 位、`orders/conditions=[]`、`metrics={}`、`account` 五键全 null/{}。
- **前端影响**：K 线弹窗的「委托与条件单」计数会是 0；盘中 Tab 的委托表空。**不会崩**。

### 2.3 `/api/trades/{ts_code}`（🟡 半实现）

- **旧端**：`api_trades` → `build_trades_payload`（纯函数，`engine/server.py:9825`）。
- **旧端键**：`{version:"trades-v1-20260917", code, days, points[{mode,side,date,t,price,qty,
  amount,name,id}], count, cost{paper,live}}`；参数非法 → 400；`days` 夹到 [1,365]。
- **数据源**：纸面 `engine/state/paper_trades.json`（红线）、实盘 `panel/real_trades_today.json`
  + `real_trades_history.json`（未登记）。→ `points=[]`、`count=0`。
- **能给的给真的**：`cost.live` 读 `panel/real_positions.json`（**在白名单**），
  有效成本（>0，旧端口径 `_valid_cost`）就下发；`cost.paper=null`。
- **实现**：`_to_ts_code()` 归一（`600519` / `sh600519` / `600519.SH` 都收），非法 400；
  `days` 非整数 400、超界夹取（测试覆盖 9999→365、0→1）。
- **前端影响**：K 线图上**没有 B/S 点**（数据确实读不到），但**成本线照画**。

### 2.4 `/api/condition/list`（🔴 骨架）

- **旧端**：`{ok:true, conditions:[…]}`，元素来自 `conditional_orders.json` 的 `orders[]`
  （`list_conditions` 会就地补 `days_left`/`expiring`）。
- **实现**：`{ok:false, conditions:[]}`。**故意 `ok=false`**：旧端 `ok=true + 空数组` 语义是
  「引擎在跑、今天没条件单」，这里是「读不到」，不能混。前端 `kline_order.js` 按
  `j.ok` 判断是否渲染，会走「不可用」分支而不是「无单」分支。

### 2.5 `/api/paper_account`（🔴 骨架）

- **旧端**：`api_paper_account`：`out = dict(acct) + account{8 键} + positions_list[]`
  （`_build_holdings_view`，15 列）；文件不存在 → 404。
- **旧端账本顶层键**（实读 `engine/state/paper_account.json`）：`initial_cash, cash,
  positions, realized_pnl, equity_curve, stats{win_rate,closed_trades,wins},
  updated_at, trade_date`。
- **实现**：`PAPER_ACCOUNT_SKELETON` 深拷贝。**不返回 404**：旧端 404 的语义是「尚无纸面成交」，
  这里是「文件在、新版无权读」，给骨架让前端按「数据暂不可得」渲染（显示成本更低、且不会
  误导成「没有成交记录」）。
- **风险**：`paper_broker` 若给账本顶层加键，骨架会缺 → 见 §5 建议（补白名单后透传即可，无需改码）。

### 2.6 `/api/ledger`（🔴 骨架）

- **旧端**：`api_ledger(mode)`，`mode∉{paper,live}` → 400。
  `{ok, mode, asof, account{10 键}, positions[{17 键}], trades[{9 键}], conds_expiring[],
  book{active,history,hist_max:50,hist_total}}`。
- **数据源**：paper → `paper_account.json` + `paper_trades.json` + `manual_orders_state.json`
  （冻结量）；live → `dashboard.json` + `real_positions.json` + `real_trades_today.json`。
- **实现**：两模式都返回同构骨架（`account` 10 键全 null、两数组空、`book` 空壳保持
  `hist_max=50`）。非法 mode → 400（与旧端一致）。
- **前端影响**：仪表盘 KPI 区（`index.html:3168`）拿到 null → 显示「—」，不会显示 0。

### 2.7 `/api/selection`（🔴 骨架）

- **旧端**：`_selection_snapshot_payload()` 读 `selection_latest.json`，**请求里零计算**；
  快照缺失/坏 → 返回**自己的缺快照分支**（键与正常分支一致，`groups=[]` + `error` + `warnings`）。
- **实现**：直接照抄那个**缺快照分支**的键（源码 6419-6429 逐字），`error` 改成
  「快照不可读：… 未在白名单」，`warnings/score_warn=["snapshot_missing"]`，
  `freshness.state="unknown"`，`pool` 用 `/selection_pool` 骨架。
- **为什么不给空 groups + ok**：前端会渲「今日无选股」，那是错的信息。`error` 字段非空时
  前端显示「快照未生成」。

### 2.8 `/api/stock_amv/{ts_code}`（✅ 真实现）

- **旧端**：`tdx_sector_api.stock_amv` → `SELECT date,alive,sh FROM stock_state
  WHERE stock=? ORDER BY date DESC LIMIT ?`（近 31 日），返回
  `{code, ts_code, dates[], alive[], sh[], note}`；无快照 → 404「无该股 state 快照（state 仅保留近 31 日）」。
- **数据源**：旧端读 `data/amv_tdx.db`（已退役）；新源 `data/amv_board.db` —— **同一张
  `stock_state(stock,date,alive,sh)` 表、同一列名**，`amv_board.db` **已在白名单**。
  → SQL 与旧端逐字相同，只是连接走 `ctx.db_read`。
- **实现**：`_digits()` 取 6 位码；无行 → 404 同一句 detail；`days` 夹 [1,500]（旧端不夹，
  但 `stock_state` 只有 31 日，超了也是全给）。
- **测试**：`test_stock_amv_ths_code`（6 键、升序、days 生效）、`test_stock_amv_unknown_404`。

### 2.9 `/api/sector_amv/{code}`（✅ 真实现）

- **旧端路由 3 段**（`tdx_sector_api.sector_amv`，2026-10-02 改）：
  ① `BK` 前缀 → `_board_amv`；② `sector_meta` 反查到层级（L2/L3/CONCEPT/INDEX）→ `_board_amv`；
  ③ 否则回落旧 L2 分支。
- **两种返回键**：
  - board 分支：`{code, name, index_code, ma_n, level, dates[], amv[], ma[], share[], rank[]}`
  - L2 回落：`{code, name, index_code, ma_n, dates[], amv[], ma[], share[], rank[]}`（**无 `level`**）
- **均线口径（易错点，照抄了）**：`ma[i] = sum(amv[max(0,i-9):i+1]) / (i+1-lo)` —— **扩张窗口**，
  前 9 个点用已有点数平均，**不是 None**。改成滚动窗口会让老图前 9 点缺线。
- **差异**：L2 回落分支的 `name`/`index_code` 来自 `tdx_industry.load_index_table()`（通达信表，
  不在白名单）→ 这两键为 **null（键在、值缺）**。THS 码走 board 分支，名称/层级都是真的。
- **测试**：board 分支（10 键 + `ma` 全非 None）、L2 分支（9 键、无 `level`、name=null）、未知码 404。

### 2.10 `/api/sector_kline/{code}`（🟡 半实现）

- **旧端 3 路**：`.TI` → `ths_index.db::index_daily`（真实指数）；`BK`/`ALL` → 自算
  （`amv_board.db::board_flow` 的 o/h/l/close/amt）；6 位数字 → 通达信 vipdoc `.day` 二进制。
  三者都返回 `{code, index_code, bars[{t,o,h,l,c,v,amt,yellow,white}], n, last, source,
  indicators:["yellow","white"]}`，黄线多取 114 根预热再截尾。
- **为什么改道**：`ths_index.db` 不在 `READABLE_DB`；vipdoc `.day` 是**二进制且插件禁 `open()`**；
  `board_flow` 无 `o/h/l`（生产实测该表由 `board_metrics_update` 补列，但 `amv_board.db` 里
  这三列可空）。→ 统一走 `ctx.quotes.daily_bars("sh"+6 位, limit+114)`（**只读行情门面**）。
- **保持**：键集与旧端正常分支**逐键相同**；每根 bar 键相同；`v` 由 `volume_lots×100` 换成「股」
  （旧端 `.day` 的 v 单位就是股；`compat_kline` 同处理）；黄白线走 `ctx.lib("indicators")`（口径唯一源）。
- **如实标源**：`source = "eltdx:daily_bars"`，**不写 `vipdoc:sh881xxx.day`**（不冒充）。
- **门面不可用**：返回 `bars=[]/n=0/last=null` 的骨架，**不 404**（旧端 404 表示「无此板块」，
  这里的语义是「取数通道不可用」，两者不能混）。
- **已知不足**：`BKxxxx` / `ALL` 段没有对应行情码 → 直接返回骨架（旧端能从 `board_flow` 自算）。
  要补齐必须登记 `amv_board.db` 之外的 `ths_index.db` 或允许读 vipdoc。

### 2.11 `/api/selection_board_options`（✅ 真实现）

- **旧端**：`api_selection_board_options`（6733-6734）：
  `favorites` = `watchlist.json` 里 `kind='sector'` 的条目（去重、保持原序、名称缺失回
  `ths_board.db` 补）；`catalog` = `board(sw_level=2→L2, =3→L3)` + `concept(CONCEPT)`；
  排序 = L2 优先、组内按名称；`level` 取值 L2/L3/CONCEPT/**UNKNOWN**（库外 → UNKNOWN）。
- **实现**：照抄，`_panel(ctx,"watchlist")` + `ctx.db_read("ths_board.db")`，两库都在白名单。
  任一不可用 → 各自空表（旧端也是 catch 后返回空清单，不 500）。
- **测试**：去重（`881166.ti` + `881166.TI` 只留一个，码统一大写）、非 sector 剔除、
  库外 UNKNOWN + 名称回退代码、catalog 排序（L2,L2,L3,CONCEPT）、两库都无 → 空清单不 500。

### 2.12 `/api/selection_pool`（🔴 骨架）

- **旧端**：`_selection_pool_payload(date)` → `{date, count, is_current, names[≤3], items[],
  slots[{date,count,saved_at}], keep:5}`；`date` 支持 `2026-10-25` / `20261025` / `1025`（当年补全）。
- **实现**：`_dash_date()` 复刻归一（`1025`→`YYYY-10-25`，测试覆盖），其余空/5。
  `is_current=false`（旧端只有「今天」才 true；这里没数据，false 是保守正确）。

### 2.13 `/api/selection_prefs`（🔴 骨架）

- **旧端口径**（`api_selection_prefs`，6660-6661）：`_read_json(selection_filter.json)`，
  **不可读 → `{}`**（文档明写「缺失/损坏 → {}（前端用默认）」）。
- **实现**：`return {}` —— 这就是旧端那条分支，不是偷懒。前端 `index.html:6201` 拿到 `{}` 后
  全部走默认值，`selApplyPrefs({})` 不会崩。
- **注**：`selection_filter.json` 的真实键集（`meta,pools,ma,pin,inds,cons,boards,sigs,
  boardOn,hot`）已记进 `keys_expected.json`，补白名单后应改为透传。

### 2.14 `/api/ui_notes/list`（🔴 骨架）

- **旧端**：`{ok:true, notes:[…], open:N}`；`notes[]` 14 个键（id/selector/elem/html/style/
  text/page/x/y/status/created_at/resolve_note/resolved_at/edited_at）。
- **实现**：`{ok:true, notes:[], open:0}`。`ok=true` 与旧端一致（旧端 `_ui_notes_doc` 读不到时
  也当空文档），前端标注面板显示 0 条，属如实降级。

### 2.15 `/api/review.md`（🔴 骨架）

- **旧端**：`review_source.pick_latest` 扫 `夏天-activity/每日复盘/日复盘 YYYY-MM-DD.md`
  → markdown 渲染（tables/fenced_code/codehilite/sane_lists）+ 泳道/赛道 SVG + 指纹缓存；
  返回 `{date,file,title,html,mtime_ns,cache,degraded,error,theme,charts{swimlane,track}}`；
  `?paper=1` 额外 `paper/masthead/css/footer`。
- **为什么做不到**：① MD 目录不是 JSON 也不是库，**不在任何白名单**，插件也不能 `open()`；
  ② 渲染依赖 `markdown`（第三方）与 `common/review_md2html`（未登记口径库），契约禁新依赖。
- **实现**：同构骨架，`degraded=true` + `error` 写明「需 01 决策：登记 MD 目录 + md2html 口径库，
  或由盘后链产出 review 快照 JSON」，`charts` 双 null，`theme` 回显入参。
  前端三条降级链（图表→纸面→v6）会看到 `degraded` 而显示「渲染降级」，
  **不会误报「复盘文件未找到」**（那是 404 分支）。

### 2.16 `/api/review_chart.json`（🔴 骨架）

- **旧端**：`review_chart_data.build_cached` 从 MD + 11 个源实时装配，22 个顶层键。
- **11 个源里本插件只读得到 4 个**（dashboard/real_positions/watchlist/watchlist_quotes），
  其余（`amv_regime_latest`/`amv_breadth_latest`/`sector_temp_latest`/`battle_plan`/
  `sector_ladder_{date}`/`sector_anomaly_{date}`/`position_cap_latest`/`all_a_latest`/
  `track_pool.db`/`ths_index.db`/temperature）**要么未登记、要么不在册**，且 MD 本身读不到
  → 一段都装不出来。
- **实现**：22 键 + 每段内部键全给（`meta/head/index/amv/breadth/temp/turnover/sector/
  ladder/anomaly/sentiment/news/positions/gate/tomorrow/scenarios/gaps/md_quotes`），
  `ok=false`、各段 `miss` 交代原因、`meta.freshness=[]`。
- **已知结构差**：旧端 `levels` 有上证数据时是 `{sse:{close,ma5,ma10,ma20,res,sup}}`，
  骨架固定 `null` → `compare_legacy` 会报 `levels.sse.*` missing。**预期内**，比对时放 `allow_missing`。

---

## 3. 安全边界自查

| 约束 | 落实 |
|---|---|
| 全部只读、无 POST | 路由只有 `@router.get`；两条测试钉死（路由方法集 + HTTP 405） |
| 不写任何文件 | 无 `open` / `json.dump` / `os.makedirs`（测试用 tokenize 去注释后静态扫描） |
| 不直连库 | 无 `sqlite3.connect`（只 `ctx.db_read`）；测试同扫 |
| 不自己连行情源 | 无 `eltdx` / `TdxClient` / `xtquant`（测试同扫） |
| 不发 HTTP / 不起子进程 | 无 `requests`/`httpx`/`urllib`/`subprocess` |
| 不改 core / 其他插件 / engine | 只新增 `app/plugins/compat_misc/**` 与 `docs/misc_samples/**`、`docs/compat_misc.md` |
| 不猜 paper/live、不填 0 | `mode=null`、账户数值全 null、`registry_ok=false`、`ok=false` 三类「不可读」信号 |

`manifest.data` 最终声明（**都是已在册的名**）：

```
read:  amv_board.db, ths_board.db
panel: real_positions, watchlist
json:  []
lib:   indicators
quotes: true
write: []
```

> 注：`ops_status` / `paper_account` / `selection_latest` 等**都没有**写进 manifest —— 它们
> 不在全局白名单，写进去会被 `validate_manifest` 直接拒载。`build_*` 里对它们的读取全部包在
> `try/except → None` 里，**补白名单 + 补 manifest 声明后自动生效，不用改代码**。

---

## 4. 测试覆盖（未运行验证）

`app/plugins/compat_misc/tests/test_compat_misc.py`，28 条：

- manifest（mount=root / 无 write / 白名单在册）2 条
- 路由（16 路径齐 / 无 POST / 无 `/api` 前缀）3 条
- 真实现（board_options 2 条、stock_amv 2 条、sector_amv 3 条、sector_kline 2 条）9 条
- trades（形状+成本线 / 无效成本 / 400 / days 夹取）4 条
- 骨架键集（ops/order_list/condition_list/paper_account/ledger×2/selection/selection_pool/
  selection_prefs/ui_notes/review_md/review_chart）10 条
- 静态禁令 3 条

全部用 tmp 目录 + 假 ctx（`FakeCtx` 只实现 `panel_json/db_read/lib/quotes`），
**零生产 IO、零真库、零真行情**。

跑法：

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/compat_misc/tests -q
```

---

## 5. 需要 01 补的白名单（**我没动 `paths.py`**）

按「补了就能真出数」的收益排序：

### A. `paths.READABLE_PANEL`（4 个，改了 compat_misc 立刻变真数据，**无需改码**）

| 名 | 路径 | 解锁 |
|---|---|---|
| `ops_status` | `data/panel/ops_status.json` | `/ops.json` 全量（含 tasks/daemons/freshness）；**语义仍是快照**，不实时探测 |
| `selection_latest` | `data/panel/selection_latest.json` | `/selection` 主体（1.4MB 盘后快照）；`freshness` 对账仍需 `common.trading_calendar`（见 B） |
| `selection_pool` | `data/panel/selection_pool.json` | `/selection_pool` + `/selection` 的 `pool` 段 |
| `selection_filter` | `data/panel/selection_filter.json` | `/selection_prefs` 从 `{}` 变真实偏好 |

### B. `paths.READABLE_LIB`（1 个）

| 名 | 路径 | 解锁 |
|---|---|---|
| `trading_calendar` | `engine/common/trading_calendar.py`（导出 `is_trading_day` / `in_trading_session` / `get_trading_dates`） | `/selection` 的 `freshness` 陈旧判定（`_selection_snapshot_freshness`）；`/ops.json` 若要做实时聚合也依赖它 |

### C. `paths.READABLE_DB`（1 个）

| 名 | 路径 | 解锁 |
|---|---|---|
| `ths_index.db` | `data/ths_index.db` | `/sector_kline` 换回**真实 THS 板块指数**（现在是 eltdx 日线代理，`source` 不同）；`/review_chart.json` 的 `index` 段 |

### D. 需要**架构决策**、不是补白名单能解决的（3 条）

1. **`engine/state/*`（交易链红线）** —— `/order/list`、`/condition/list`、
   `/paper_account`、`/ledger`(paper)、`/trades`(paper 点)、`/ui_notes/list` 六个端点全卡在这。
   建议：**不要为兼容层开 state 白名单**，而是让旧引擎写一份 `data/panel/*_readonly_snapshot.json`
   （它已经在写 `real_positions` / `real_trades_today` 这类面板快照，同一条路），新端点只读快照。
   这样红线不动、语义还更清楚（快照 vs 实时）。
2. **`/ops.json` 的本质冲突** —— 旧端是「请求时跑 schtasks/WMI」。新后端不可能同语义。
   建议定为「只读快照 + 外部调度器定期跑 `ops_status.py`」（`docs/AGENTS` 明令不新建常驻服务，
   所以挂到现有盘后链/KeepAlive 里）。
3. **复盘两兄弟（`/review.md`、`/review_chart.json`）** —— 源是 Markdown 知识库 + markdown 渲染。
   建议：盘后链增加一步产出 `data/panel/review_chart_{date}.json`（等于把
   `review_chart_data.build_cached` 的结果落盘），新端点直接透传；`/review.md` 则需要把
   MD 正文渲染成 HTML 一起落盘（或登记 `engine/common/review_md2html.py` 为口径库 + 允许
   `markdown` 这一依赖）。**这两条不做，复盘 Tab 在新后端只能显示「降级」占位。**

---

## 6. 与旧端的已知差异汇总（不编造，逐条登记）

| # | 端点 | 差异 | 原因/性质 |
|---|---|---|---|
| 1 | `/ops.json` | 返回骨架（0 任务/0 常驻），`registry_ok=false` | 数据源未登记 + 旧端语义含子进程探测 |
| 2 | `/order/list` | `mode=null`、`ok=false` | state 红线，不猜 paper/live |
| 3 | `/trades` | `points=[]`；`cost.paper=null`；`cost.live` 真 | 三份成交源不可读；成本线可读 |
| 4 | `/condition/list` | `ok=false`（旧端 `ok=true`+空） | 区分「读不到」与「没有单」 |
| 5 | `/paper_account` | 骨架而非 404 | 旧端 404 = 文件不存在，这里 = 无权读，语义不同 |
| 6 | `/ledger` | 两模式骨架 | 同 5 |
| 7 | `/selection` | 走旧端「缺快照分支」+ 改 `error` 文案 | 该分支本就是旧端自带的，键集与正常分支一致 |
| 8 | `/selection_board_options` | **无差异**（真实现） | — |
| 9 | `/selection_pool` | 全空骨架 | 未登记 |
| 10 | `/selection_prefs` | `{}`（= 旧端不可读分支） | 与旧端该分支**完全一致** |
| 11 | `/stock_amv` | 源库从 `amv_tdx.db` 换 `amv_board.db` | 同表同列（`stock_state`），值域相同；旧库已退役 |
| 12 | `/sector_amv` | L2 回落分支 `name/index_code=null` | `tdx_industry` 表不在白名单 |
| 13 | `/sector_kline` | `source=eltdx:daily_bars`（非 vipdoc）；`BK*/ALL` 段空骨架 | vipdoc 二进制禁读、`ths_index.db` 未登记 |
| 14 | `/ui_notes/list` | `notes=[]`、`open=0` | state 红线 |
| 15 | `/review.md` | `degraded=true` + `html=null` | MD 目录不在册 + 无 markdown 依赖 |
| 16 | `/review_chart.json` | `ok=false` + 全段骨架；`levels=null` | 11 源只 4 源可读，MD 不可读 |

---

## 7. 交付清单

| 文件 | 说明 |
|---|---|
| `app/plugins/compat_misc/plugin.json` | manifest（mount=root、无 write、5 类只读声明） |
| `app/plugins/compat_misc/backend.py` | 16 个 GET 端点 + 全部 `build_*` 纯函数（可单测） |
| `app/plugins/compat_misc/tests/test_compat_misc.py` | 28 条契约测试 |
| `docs/compat_misc.md` | 本文件（逐端点：旧端依据 / 数据源 / 实现方式 / 缺什么白名单） |
| `docs/misc_samples/keys_expected.json` | 逐端点键集基线（含每项 `src` 出处），供 01 拿真样本对账 |
| `docs/misc_samples/README.md` | 「为什么没有 curl 样本」+ 01 要跑的那段 PowerShell |
