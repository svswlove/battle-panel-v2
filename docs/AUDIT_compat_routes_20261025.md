# compat 路由归属盘点（2026-10-25）

盘点人：数据员｜委托：01｜01 已逐条实测核实关键字段集。

## 总览

compat 层共 57 条路由，其中 **51 条是 domain 插件没有的**。

| 类 | 条数 | 含义 | 处置 |
|---|---|---|---|
| **A** | 10 | 同一指标的第二个实现 | 应删 compat，留 domain |
| **B** | 35 | 只存在于 compat 的独有指标 | 迁到某插件或新建 |
| **C** | 6 | 纯旧壳胶水 | 保留在 compat |

**但 A 类不等于可以直接删**，见第 4 节。

---

## 1. A 类 · 10 条（应删除 compat 实现）

| # | 路由（方法） | 返回语义 | compat 位置 | domain 等价 | 分歧 |
|---|---|---|---|---|---|
| 1 | `GET /battle_plan.json` | 作战表原样透传 | `compat:1684` | `/p/battle_plan/plan` | — |
| 2 | `GET /amv_live.json` | AMV 实时快照透传 | `compat:1688` | `/p/amv/live` | — |
| 3 | `GET /amv_regime_latest.json` | regime 透传 | `compat:1692` | `/p/amv/regime` | — |
| 4 | `GET /position_cap_latest.json` | 仓位上限透传 | `compat:1696` | `/p/amv/position_cap` | — |
| 5 | `GET /alert_todo.json` | 告警/待办透传 | `compat:1700` | `/p/battle_plan/alerts` | — |
| 6 | `GET /real_positions` | 实盘持仓 + `positions_list`（实时估值） | `compat:1763`（build:899） | `/p/battle_plan/positions`（**缺 positions_list**） | ① |
| 7 | `GET /kline/{ts_code}` | 日/周/分钟 K 线 + amv 三列 + mf + 黄白线 | `compat_kline:1733`（build:937） | `/p/kline/bars/{ts}`（**字段名+字段集均不同**） | ② |
| 8 | `GET /kline_bg/{ts_code}` | 四态背景带 `items{YYYYMMDD:态}` | `compat_kline:1744`（build:1073） | `/p/kline/bg/{ts}`（`dates[]+states[]`） | ③ |
| 9 | `GET /watchlist.json` | 富化自选池（quote/regime/sector/div/flow/badges） | `compat_watchlist:1130`（build:724） | `/p/watchlist/items?quotes=1`（**仅 quote**） | ⑤＋静默降级 |
| 10 | `GET /watchlist/groups` | 分组 `{ok,groups[]}` | `compat_watchlist:1134`（build:814） | `/p/watchlist/groups`（缺 `ok`） | 小 |

**可立即删的 5 条**（字段逐字相同、有通配路由兜底、零风险）：#1 #2 #3 #4 #5。
另 `GET /positions.json` 同理可删。

---

## 2. C 类 · 6 条（保留在 compat）

| # | 路由 | 语义 | 位置 |
|---|---|---|---|
| 1 | `GET /shell/status` | 外壳探活 `{panel_up,engine,ts}` | `compat:1722` |
| 2 | `GET /shell/version` | 版本戳 `{v}`，供 iframe 重载判定 | `compat:1728` |
| 3 | `POST /shell/search` | 外壳侧栏搜索 | `compat:1738` |
| 4 | `GET /gates` | 三道门服务端评估 | `compat:1755`（build:641） |
| 5 | `GET /status` | 引擎徽章（signed/kill/trade_mode） | `compat:1759`（build:1590） |
| 6 | `GET /sector/health` | 板块数据源可读性 | `compat_sector:753`（build:479） |

注：`/gates` 是**计算型聚合，不是纯胶水**，但它是旧壳专用，无 domain 对应。

---

## 3. B 类 · 35 条（独有指标，需迁）

按建议归属分组：

| 归属 | 条数 | 路由 |
|---|---|---|
| **watchlist** | 12 | `/search/all`、`/watchlist/state`、`/watchlist/search`(GET·POST)、`/watchlist/add`、`/remove`、`/move`、`/reorder`、`/group/add`、`/group/rename`、`/group/remove`、`/group/reorder` |
| **selection** | 6 | `/selection`、`/selection_board_options`、`/selection_pool`、`/selection_prefs`(GET·POST)、`/selection_pool/add`、`/selection_pool/remove` |
| **sector** | 6 | `/sector_amv/{code}`、`/sector_card.json`、`/sector_spark.json`、`/sector_intraday.json`、`/sector_track.json`、`/etf_sector_map.json` |
| **kline** | 2 | `/f10_brief/{ts}`、`/sector_kline/{code}` |
| **amv** | 4 | `/amv_research.json`、`/amv_research`、`/stock_amv/{ts}`、`/breadth_880.json` |
| **ops** | 3 | `/alert_plan.json`、`/push_types.json`、`/sector_track_backfill_status` |
| **dashboard** | 1 | `/dashboard.json`（需改 mount=root） |
| **新建 positions** | 1 | `/positions.json` |
| **新建 temperature** | 1 | `/temperature_latest.json` |

**其中 10 条是写操作**（`watchlist/add|remove|move|reorder|group/*`、`selection_pool/add|remove`、`selection_prefs` 写），
迁移时需先在 `plugin.json` 的 `data.write` 里授权。

---

## 4. 三个必须先解决的字段级分歧（01 已实测确认）

### 分歧②：`/kline` 两套字段集完全不同 —— 硬阻断

实测（`002531.SZ`，2026-10-25）：

| | compat `/api/kline` | domain `/p/kline/bars` |
|---|---|---|
| 顶层键 | `adj, asof, bars, boards, code, count, indicators, mf, period, prev_close, source, stale, version, vol_unit` | `adjust, bars, code, intraday, last_bar_date, notice, period, source, stale, warnings` |
| bar 键（15 vs 9） | `t, o, h, l, c, v, amount, amp, amv, amvMa, amvWhite, turnover, chg, yellow, white` | `date, open, high, low, close, volume, amount, yellow, white` |

旧前端读的是 `t/o/h/l/c/v` 那一套（`kline_lc.js:100-105`），还依赖 `p.mf`（`:418`）、`p.stale`、`p.asof`、`p.indicators`、
背景带 `bgData.items`（`:2881`）。

**结论：domain 那套不能直接替换。** 要么写适配层（`date→t`、`open→o`…并补齐 `amv/mf`），
要么让 kline 插件直接提供旧格式。**改旧前端不是选项**（11468 行 + `kline_lc.js` 4669 行）。

### 分歧⑤：`/watchlist.json` 富化字段全缺 —— 静默降级

实测：

| | compat `/api/watchlist.json` | domain `/p/watchlist/items?quotes=1` |
|---|---|---|
| 顶层 | `groups, items, meta, mode, quote_asof, stats, updated_at` | `group, items, kind, n, quote_asof` |
| item 键 | `added_at, badges, code, div, flow, group, kind, name, order, quote, quote_source, regime, sector` | `added_at, code, group, kind, name, order, quote` |

缺的 6 个字段全是**渲染依赖**，旧前端 7 处用它（`index.html:5893-5910`）：
`quote.price/change_pct`（价格涨跌）、`regime`（四态红绿圆点）、`badges[].label`（买点徽标）、
`div`（5 日背离）、`sector.l2.name/regime`（板块 chip）、`flow`（主力净流入）、`quote_source`（实时/收盘标题）。

**删字面路由后不报错，而是价格显示 `—`、圆点变灰、chip 消失。** 这是唯一会静默坏掉的地方。

**更麻烦的一层**：即使将来把字段补齐到 watchlist 插件，`regime` 的来源
（正本 `amv_regime.stock_state_series`）也必须一并过去，否则会**复刻今天刚修掉的 0.0 缺陷**——
而且是修好又搬回去，更难发现。

**固化为硬前置**：`/watchlist.json` 必须是最后一批迁，验收按「字段集 + regime 数值逐票比对」双条件，不能只比 schema。

### 分歧①：`/real_positions` 缺 `positions_list`

旧前端硬依赖（`index.html:3399-3409`、`:3447-3449`）：
`account.total_asset`、`manual_positions`、`positions_list`、`status`、`last_synced_at`、`market_value_total`。

domain `/p/battle_plan/positions` 只缺 `positions_list`，**但那正是唯一带实时估值的一份**。
删了 compat，持仓卡与仓位三层同时失效。

---

## 5. `/alerts` 名同义不同（数据员查证，01 已核实）

- compat `/api/alerts`：三源合一计算体，返回 `a.pnl.items[]`、`a.holdings`、`a.near_triggers.items[]`
- domain `/p/battle_plan/alerts`：`alert_todo.json` 透传，`alerts[]/todos[]/updated_at`，**字段零重合**

**旧前端只消费 compat 那个**（`index.html:5184`）。
`/p/battle_plan/alerts`、`/todos`、`/positions` 全仓 grep 只在
`battle_plan/tests/test_battle_plan_contract.py:84-118` 里被调用，**任何前端都不请求**。

**结论**：compat 的 `/alerts` 必须留；battle_plan 那条同名路由是**语义占位**，
建议迁移时删掉或改名 `/alert_todo`，避免以后有人按名字取错实现。

---

## 6. 迁移顺序

1. **摘 5 条纯透传**（#1-#5）—— 零风险，通配路由兜底
2. **selection 6 条** —— 插件本来是 root 空壳，代码形态现成
3. **watchlist 写侧 10 条** —— 需先给 `data.write` 授权
4. **sector / amv / ops** + 新建 `positions` 和 `temperature`
5. **最后才碰 kline 族** —— 需先定适配层方案（分歧②）
6. **`/watchlist.json` 绝对最后** —— 双条件验收（分歧⑤）

---

## 7. 风险清单

- **R1** 通配路由暴露面超过旧端白名单：`selection_latest`（8.2MB）、`selection_filter`（可写偏好）都有只读入口
- **R2** `/real_positions` 缺 `positions_list`，删了 compat 就失效（分歧①）
- **R3** `sector_kline` 与 `compat_kline` 的 `.TI` 指数分支是同一指标的两份实现，迁移前要先定唯一源
- **R4** `/kline` 字段集完全不同，需适配层（分歧②）
- **R5** `/watchlist.json` 删了会静默降级，且 regime 数值会回退到旧口径（分歧⑤）
- **R6** `/alerts` 名同义不同，battle_plan 那条是语义占位，别按名字取
