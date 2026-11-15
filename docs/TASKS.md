# 任务清单（与 neo-project-manage 项目 8dbb797f 同步）

| # | 任务 | 负责人 | 状态 | 验收 |
|---|---|---|---|---|
| 1 | 总线契约与 core 骨架（8810） | 01 | **完成** | 空工程启动 8810，`/api/health` 200，`/_plugins` 可读；全套 82 tests pass |
| 2 | sector 只读插件（首块样板） | 01 + 数据员 | **完成（v0.3）** | backend/tree/rank/series + catalog/search/members(full=1,含 sigs)/rank_recent/leaders/manifest + minute_rank/minute_series/minute_multi/minute_dates；双跑 PASS（260,026 字段/元素零差异；series 全量 722 code）；members period 1/3/5 逐值相等；minute 四端点 `==` |
| 3 | kline 只读插件 | 01 | **完成（v0.1）** | `/bars` + `/quote` + canvas 卡片；只读 track_pool.db；历史日线 6/7 标的零差异；`intraday=false` 明示不含当日/指标 |
| 4 | amv 只读插件（shadow） | 01 + 数据员 | **完成（v0.2）** | `/summary` + `/live` + `/regime` + `/position_cap` + 指数/板块 + `/daily` `/history` + `/hcl`；只读 panel JSON + `hcl_latest`；三份原始 JSON 与旧 8800 精确相等；`/hcl` 与旧 8002 除 `ok` 外逐字段相等 |
| 5 | battle_plan 只读/影子（不接交易） | 01 + 嗅嗅 | **完成（v0.1）** | `/summary` + `/plan` + `/positions` + `/alerts` + `/todos`；只有 GET，POST=405；作战表/告警精确相等；实盘原始字段相等 |
| 6 | 双跑比对与切流 | 01 + 嗅嗅 | **进行中** | sector 260,026 字段零差异；amv 三份原始 JSON 精确相等；battle_plan/alert_todo 精确相等；kline 历史日线 6/7 标的零差异（399001 源精度 0.03），v0.1 明示不含当日/指标；shadow 工具首轮/15:54/16:08 均 10/10 pass；cutover gate PASS；2026-10-22 收盘观察完成（sector/amv/battle 1/1 日，kline 1/2 日），待下一交易日补 kline 第二日 |
| 7 | 项目文档与运行手册 | 01 | **完成** | README/CONTRACT/ARCHITECTURE/TASKS 可查 |
| 8 | 面板产物一致性审计 | 01 | **完成** | `tools/check_panel_consistency.py`；实际 PASS 7 / FAIL 3 / UNPARSED 1，3 项均为 battle_plan AMV 文本 stale |
| 9 | 8810 独立健康保障 | 01 | **完成** | `tools/ensure_8810.ps1` 单次/循环；不碰旧 watchdog；实测 `-Once` exit 0 |
| 10 | M4 切流门禁 | 01 | **完成** | `tools/cutover_gate.py`；首跑 PASS 5.7s，7 项全过 |
| 11 | 每日操作包装 | 01 | **完成** | `tools/run_daily.ps1`；ensure + gate 一条命令，日志落 `data/panel_v2/logs/` |
| 12 | 桌面启动器 | 01 | **完成** | `launcher.py` 健康检查/启动/开浏览器；未来 PyInstaller 封装，业务不进 exe |
| 13 | watchlist 只读插件 | 01 | **完成** | `/summary` `/items` `/groups`；只读 `data/panel/watchlist.json` |
| 14 | AMV 历史序列 | 01 | **完成** | `/daily` `/history`；只读 `amv_board.db::daily_total`，MA10 头部按实际长度 |
| 15 | sector 买点徽标 + 全量成分字段 | 01 | **完成** | `full=1` 行补 `amt/amt_avg/contrib/d_contrib/sigs/sigs_date`；period 1/3/5 与旧 8002 逐值相等；前端成分股表 |
| 16 | sector 分钟级 AMV | 01 | **完成** | `/minute_rank` `/minute_series` `/minute_multi` `/minute_dates`；6 组请求与旧 8002 `==` |
| 17 | HCL 离散度卡 | 01 | **完成** | amv `/hcl` + `amv.hcl` 卡片；只读 `hcl_latest.json`，不重算 |
| 18 | core data.json 机制 | 01 | **完成** | `ctx.json_read` + `READABLE_JSON`；CORE_VERSION 0.1.1；CONTRACT v1.1 |

| 19 | sector 语义榜/趋势/板块TOP/资金流 | 01 | **完成（v0.4）** | `/rank_top` `/trend` `/board_top` 与旧端 JSON 全等；`/board_flow` 除 `bull`（依赖工具目录，不搬）外全等；前端加语义榜双列 |
| 20 | amv 广度 + watchlist 行情 | 01 | **完成** | amv `/breadth` 透传 `amv_breadth_latest`；watchlist `/quotes` + `/items?quotes=1` 拼接 `watchlist_quotes`；条目顺序对齐旧 8800 |

| 21 | 关注池关键位 + 全A快照 | 01 | **完成** | watchlist `/levels`（focus_levels 只读，默认只返 effective）；amv `/all_a`（all_a_latest 透传）；前端自选池卡加「最近关键位/距关键位」两列 |

| 22 | kline 复权因子缺口告警 + shadow 扩到 25 项 | 01 | **完成** | 512480.SH 实测 factor 全恒等但 20260703 有 50.7% 跳变 → `/bars` 新增 warning「疑似缺复权因子」；shadow 加 5 个标的（399001.SZ 源精度容差 0.031），512480 因数据缺口暂不入 shadow |

**当前全套测试**：**151 passed**（core 52 + sector 50 + kline 10 + amv 13 + battle_plan 6 + watchlist 11 + 比较器自检 9）。
**Shadow 工具**：`tools/shadow_check.py`，**31/31 pass**；`missing_in_new` 严格判定、`empty_intersection` 判 fail、known-defect 带代码范围；详见 `docs/shadow_plan.md`。
**M4 切流**：评审见 `docs/M4_CUTOVER.md`；sector/amv 可切，battle_plan 只切看、kline 子集最后或暂不切。

## 当前里程碑

**M1（10-24）**：core 骨架 + 空插件可启动 8810；`/_plugins` 返回清单。
**M2（10-29）**：sector 只读插件字段级 diff=0。
**M3（11-15）**：kline / amv / battle_plan 只读影子全部可用。**已完成（10-22，提前）**
**M4（11-20）**：切流评审；旧端口进入回退观察期。当前只差 kline 新旧 diff 与 shadow 观察。
