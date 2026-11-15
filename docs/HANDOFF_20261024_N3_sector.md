# HANDOFF · 周末冲刺 N3 板块只读域（compat_sector 0.1.1 → 0.2.0）

- 日期：2026-10-24
- 任务书：`docs/task_N3_sector.md`
- 执行：01 → DSHana（N3/4 并发组）
- 结论速览：**9 条根路径已全部可用并与旧 8800 结构一致（值 8/9 完全一致，1 条数据更鲜）**；
  三个带参端点（`/stock_amv`、`/sector_amv`、`/sector_kline`）已由 `compat_misc` 在**同一 root 前缀**注册，
  本插件再注册会让 8810 起不来（`main.py:322-337` 根路径冲突保护），本单**未重复实现**，
  并查出一处真实数据源缺陷（§4.2，需 01 裁定）。本批净新增：`temperature_latest` 走真实生产文件、
  `breadth_880.prev` 走真实昨日日线。

---

## 1. 逐端点登记（旧 handler 行号 + 数据源 + 新实现落点）

行号一律指 `OH-WorkSpace\engine\server.py`（旧 8800，只读未改）。
新落点一律指 `app/plugins/compat_sector/backend.py`（本插件 `backend.mount=root`，router 路径不带 `/api`）。

| # | 路径 | 旧 handler | 旧数据源（源码级） | 新落点 | 对齐 |
|---|---|---|---|---|---|
| 1 | `GET /api/sector_card.json` | `api_sector_card` :2321-2427 | `amv_board.db`：`sector_daily(amv)` + `board_flow(close,flow_main)` + `daily_total(total)`；MA10=`amv_regime.sma`；黄白线=`common/indicators.yellow_white_lines` | `build_sector_card` :154 | 结构=旧，值=旧（多板块同参实测） |
| 2 | `GET /api/sector_intraday.json` | `api_sector_intraday` :7063-7105 | `data/panel/amv_live_sector_track.json`（`dates[日]→boards[码]`） | `build_sector_intraday` :323 | 结构=旧，值=旧 |
| 3 | `GET /api/sector_spark.json` | `api_sector_spark` :2429-2483 | `amv_board.db::sector_daily` + `amv_regime.sma`，SVG viewBox 100×24 | `build_sector_spark` :270 | 结构=旧，值=旧 |
| 4 | `GET /api/sector_track.json` | `api_sector_track` :3069-3115；`_track_stale` :3040 | `amv_live_sector_track.json` + `amv_board.db::sector_daily(share)` 权重基线 | `build_sector_track` :433 | 结构=旧，值=旧 |
| 5 | `GET /api/etf_sector_map.json` | `api_etf_sector_map` :3116-3130 | `data/panel/etf_sector_map.json`（原样透传） | `build_etf_sector_map` :516 | 结构=旧，值=旧（123,784 B 全等） |
| 6 | `GET /api/sector/health` | `api_sector_health` :2130-2136 | 旧=HTTP 探活 8002 `/health`；新版禁 HTTP | `build_health` :479 | 结构=旧（`{alive,port}`）；`alive` 语义改为「板块日频数据可用」（见 §5.3） |
| 7 | `GET /api/breadth_880.json` | `api_breadth_880` :979-987；`_eltdx_880_snapshot` :871、`_read_880_day` :898、`_breadth_880_prev` :935 | today←880005/880006 快照；prev←vipdoc `sh880005/880006.day`（`TDX_SH_LDAY` :105，二进制） | `build_breadth_880` / `_snapshot_880` / `_day_880` / `_prev_880` | 结构=旧；today=旧；**prev 本批由 null 变真实值**（源更鲜，见 §5.2） |
| 8 | `GET /api/temperature_latest.json` | `api_temperature` :858-866；`TEMPERATURE_FILE` :93 | `夏天-activity/工具/temperature_data/temperature_latest.json`（不在 data/panel） | `build_temperature` :690 + `paths.READABLE_JSON["temperature_latest"]` | 结构=旧，**值本批由 null 骨架变真实值** |
| 9 | `GET /api/sector_track_backfill_status` | `api_sector_track_backfill_status` :2295-2298；`_bf_status` :2246、`_bf_running` :2249 | `engine/state/sector_track_backfill_status.json`（+30min 超时保护） | `build_backfill_status` :468 | 结构=旧，值=旧（该状态文件当前不存在，旧端同为空对象） |
| 10 | `GET /api/stock_amv/{ts_code}` | `api_stock_amv` :8767-8774 → `tdx_sector_api.stock_amv` | `amv_tdx.db::stock_state`（已退役）→ 现 `amv_board.db::stock_state` | **已在 `app/plugins/compat_misc/backend.py:634`** | 结构=旧，值=旧（同参逐值一致） |
| 11 | `GET /api/sector_amv/{code}` | `api_sector_amv` :8755-8762 → `tdx_sector_api.sector_amv` | `amv_board.db::sector_meta`(层级反查) + `sector_daily` | **已在 `compat_misc/backend.py:681`** | 结构=旧，值=旧（`.TI` 与 6 位码同参逐值一致） |
| 12 | `GET /api/sector_kline/{code}` | `api_sector_kline` :8743-8750 → `tdx_sector_api.sector_kline` | `.TI`→`ths_index.db::index_daily`；`BK/ALL`→`amv_board.db::board_flow` 自算；6 位→vipdoc `.day` | **已在 `compat_misc/backend.py:745`** | 结构=`BK` 码缺 `bars[].*`；值不同（源不同，见 §4.2） |

**端口归属**：这 12 条**全部**只在 8800 注册。旧 8002（`夏天-activity\工具\dashboard\server.py`）的 `handle()`
路由表里一条都没有（其 `/health` 自述 endpoints 亦无），任务书表里写的「8002」为误记——
2026-10-23 的 `docs/compat_sector.md` §0 已核实过一次，本批复核结论不变。

---

## 2. 本批实质改动（3 处 + 版本/声明）

1. `app/core/paths.py` → `READABLE_JSON` 补 **1 个名字**（最小 diff，未动其他行）：
   `"temperature_latest": SUMMER_TOOLS_DIR / "temperature_data" / "temperature_latest.json"`。
   用 `READABLE_JSON` 而不是 `READABLE_PANEL`：旧端源文件不在 `data/panel`（`server.py:93`），
   `READABLE_JSON` 才是「非 data/panel 的既有生产 JSON」的登记位（同 `hcl_latest`）。
   与并发 N2 的 `READABLE_PANEL["ops"]` **不同字典、不同行**，无冲突面。
2. `build_temperature`：`ctx.panel_json` → `ctx.json_read("temperature_latest")`。文件真缺失仍给旧端同构空骨架（不 404）。
3. `build_breadth_880` 新增 `_day_880` / `_prev_880`：`prev` 由「恒 null」改为真实昨日条。
   旧端读 vipdoc `sh880005/880006.day` 二进制（32B/条，价格×100），插件不得直读二进制、
   vipdoc 不在任何白名单 → 改走 `ctx.quotes.daily_bars("sh880005"/"sh880006")`（同一份通达信日线，已 /100），
   算术逐条照旧：`up=close`、`down=open`、`total=high`、`flat=total−up−down`、880006 `limit_up=close`/`limit_down=open`、
   末根距今 >`BREADTH_880_STALE_DAYS=30` 视为停旧不取。**prev 仍无 `total` 键**（旧端就没有）。
4. `plugin.json`：`version 0.1.1 → 0.2.0`；`data.json: ["temperature_latest"]`；`data.panel` / `data.read` / `data.write` 未变。

未改：`backend.py` 的路由集合（仍是 9 条）、`contract.py`、`main.py`、其他插件、`engine/`、旧 8002/8800 一行未动。

---

## 3. 验证（证据，全部本机实测）

### 3.1 契约/单元
- `python -m pytest app/plugins/compat_sector/tests -q` → **50 passed**（原 47 + 新增 3：temperature 走 json_read、
  breadth prev 真实值、breadth prev 停旧不取）。
- `python -m pytest -q` → **918 passed, 4 skipped**（跑于并发单收口后的一次复跑；中途曾因 `ops/`、`battle_plan_rw/` 在飞而各红 1 项，均非本插件）。
- `python tools/cutover_gate.py` → `FAIL battle_plan 写域接管`（`POST /api/p/battle_plan/plan → 405`），其余 7 项全 OK
  （health / 全部插件健康 / shadow 双跑 / AMV 原始 JSON 比对 / 插件无写代码 clean(30 files) / kline 边界 / pytest 全套）。
  8810 **未重启**，`battle_plan_rw` 为并发 N1 的新建未跟踪目录，405 与本插件无交集，未修、不越界。
  compat_sector 自身在「无写代码」扫描里 0 命中（单测 `test_no_http_and_no_subprocess` 也守着）。

### 3.2 与旧端对齐（真实数据，非 mock）
8810 按纪律**未重启**，仍跑 0.1.1 旧码；为拿到「新码 + 真生产文件」的实际响应，
用**真实** `PluginContext`（真 `paths` 白名单、真 `data/panel`、真 `amv_board.db`、真行情门面）在进程内调用
9 个构造函数，与 8800 同参响应逐键比对：

| 端点 | 结构 | 值 |
|---|---|---|
| `sector_card.json`（codes=881101.TI, days=60, win=5） | 一致 | 完全一致 |
| `sector_spark.json` | 一致 | 完全一致 |
| `sector_intraday.json` | 一致 | 完全一致 |
| `sector_track.json` | 一致 | 完全一致 |
| `sector_track_backfill_status` | 一致 | 完全一致 |
| `sector/health` | 一致 | 完全一致 |
| `etf_sector_map.json` | 一致 | 完全一致 |
| `temperature_latest.json` | 一致 | **完全一致**（本批修复点） |
| `breadth_880.json` | 一致 | today 全等；prev 值不同（见 §5.2） |

`python tools/compare_legacy.py --batch 3`（打到**在跑的** 8810=0.1.1 与 8800）→ **9/9 ok，结构不一致 0**；
`breadth_880` 值差 7（prev null，重启后消失）、`temperature_latest` 值差 30（null 骨架，重启后消失）。

三个带参端点（由 compat_misc 提供，实测 8810 vs 8800）：`stock_amv/600519.SH` 结构+值全等、
`sector_amv/881101.TI` 结构+值全等；`sector_kline` 见 §4.2。

---

## 4. 上报（需 01 裁定，本单未越界处理）

### 4.1 三个带参端点归属与 N3 任务书不一致
任务书把它们列给 compat_sector，实际它们已在 `app/plugins/compat_misc`（0.1.0）root 注册：
`/stock_amv/{ts_code}`、`/sector_amv/{code}`、`/sector_kline/{code}`。
`app/core/main.py:322-337` 对同路径第二次注册**直接 raise**（「根路径冲突」）→ 在 compat_sector 再注册会让 8810 起不来。
本单处置：不重复实现，逐条实测其对齐状态（§3.2）。
若要按域收敛到 compat_sector，需一次显式搬迁（改 compat_misc 删除 3 条 + compat_sector 新增 3 条 + 各自 manifest），
属跨插件改动，**请 01 单开工单**，不要在两条并发单里对撞。

### 4.2 `sector_kline` 存在**数据正确性**问题（不是结构问题）
compat_misc 的 `build_sector_kline` 因 `ths_index.db` 未登记，回退 `ctx.quotes.daily_bars`：
- `GET /api/sector_kline/881101.TI` → 旧端 `source=ths_index.db（真实指数）`、收盘 ~4342（THS 881101 点位）；
  新版 `source=eltdx:daily_bars`、收盘 **430.73**。这不是「值差」，是**取到了另一个标的**：
  881xxx 在 THS 与通达信两侧数字空间碰撞（`tdx_sector_api.py` 头注释已记载「881121：TDX=水产饲料 / THS=半导体」）。
- `GET /api/sector_kline/BK0475` → 旧端 `bars` 有 240 根（`amv_board.db::board_flow` 自算），新版 `bars=[]`（骨架），结构比对因此缺 `bars[].*`。

修法只有两条，均超出本单白名单：
① `paths.READABLE_DB` 增 `"ths_index.db"`（1 行，文件在 `data/ths_index.db`，已在 `review_chart` 直读）+ compat_misc 改回 `index_daily` 取数；
② `BK/ALL` 分支改走 `ctx.db_read("amv_board.db")::board_flow`（该库已在白名单，**不需要**新白名单，仅改 compat_misc 代码）。
建议 02 优先做 ②（零白名单成本、修掉空图），① 另行评估。

### 4.3 `sector_track_backfill_status` 仍不读状态文件
真实源是 `engine/state/sector_track_backfill_status.json`（旧 `server.py:2244-2257`）。该文件当前**不存在**，
旧端此刻同样返回 `{ok:true, running:false, status_file:{}}`，所以实测完全一致。
但一旦真有回填在跑，新版会恒报 `running:false`。读 `engine/state` 的通道正由并发单 N2 在 `contract.py` 定
（`ctx.state_json`），本单不动 `contract.py`、也不另开 `READABLE_JSON` 次通道（同一含义只认一个权威源）。
→ 待 N2 落地后由 01 指派一次小改（`build_backfill_status` 改走 `ctx.state_json`）。

---

## 5. 口径差异（已知、显式登记，非缺陷）

1. **`sector_card` 多板块请求的第 2 个及以后板块 MA10 起点不同**：旧端在 `for` 循环内就地 `dates = dates[-days:]`，
   第二个板块只剩 days 日算均线（前 9 点 None）；新版统一「先按满窗算、再切 days」。
   单板块请求逐值一致（§3.2 实测）；多板块时第 2+ 板块 `amv.ma` 点数多于旧端。
2. **`breadth_880.prev` 数据更鲜**：旧端读 vipdoc `lday/*.day`（本机 TDX 客户端未更新到 10-23，末根=20261022），
   新版走 eltdx 日线（末根=20261023）。结构一致，同一数据源不同通道的新鲜度差；行情服务停机时两边都会 null。
3. **`sector/health` 的 `alive` 语义变更**：旧端是「HTTP 探活 8002」（插件禁发 HTTP），
   新版改为「`amv_board.db::sector_daily` 有行」。`{alive, port:8002}` 键与值型不变，前端沿用不报错。
4. `sector_card` / `sector_spark` / `sector_track` 的 SVG 画布常量、x 用下标（None 留空档）、
   `/1e4` 万元→亿元、权重基线取「track 日之前最近交易日」等，均逐字照旧端，未「优化」。

---

## 6. 改动文件（白名单内，共 5 个）

```
app/plugins/compat_sector/backend.py             # temperature 走 json_read；breadth prev 真实化；注释/文档校正
app/plugins/compat_sector/plugin.json            # 0.1.1 → 0.2.0；data.json: ["temperature_latest"]
app/plugins/compat_sector/tests/test_compat_sector.py  # FakeCtx 增 json_read；+3 测试；实core 测试隔离 temperature 白名单
app/core/paths.py                                # 仅 READABLE_JSON 补 "temperature_latest"（1 条目）
docs/HANDOFF_20261024_N3_sector.md               # 本文件
```

未写任何生产文件（临时探针脚本建在 `%TEMP%`，用完即删）；未 commit；未重启 8810。

## 7. 待 01 执行 / 后续

1. **重启 8810**（并发组收口时一次性）→ `temperature_latest` 出真实温度、`breadth_880.prev` 出昨日列；
   届时 `compare_legacy --batch 3` 的 7+30 条值差应归零。
2. `§4.1` 三个带参端点归属裁定；`§4.2` `sector_kline` 数据源修正（建议先做 `board_flow` 分支）；`§4.3` 待 N2 的 `ctx.state_json`。
3. 排他提示：本单期间 `app/plugins/ops/`、`app/plugins/battle_plan_rw/`、`app/core/contract.py`、`app/plugins/compat_misc/`
   正被并发单修改，复跑时全量 pytest / cutover_gate 的红项一概来自这些在飞目录，与本插件无关（证据见 §3.1）。
