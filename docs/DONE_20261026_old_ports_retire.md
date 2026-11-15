# 旧端口下线完成记录（8800 / 8001 无 / 8002 / 8801）

日期：2026-10-26
依据：`docs/HANDOFF_20261026_old_ports_retire.md`
状态：**已完成并实测**（三端口进程已停、无自愈拉起、v2 全 Tab 零旧端口请求）

---

## 1. 做了什么

| # | 改动 | 文件 | 验证 |
|---|---|---|---|
| 1 | 摘除 watchdog 对 8800/8002/8801 的 HTTP 探活与自愈链 | `engine/executor/watchdog.py` | `pytest engine/tests/test_watchdog.py test_daemon_monitoring_coverage.py` 全绿；CHECKS 快照无 server_8800/shell/8002 |
| 2 | run_daemons 撤出 server / amv_dashboard / shell 三项注册 | `engine/executor/run_daemons.py` | `pytest engine/tests/test_run_daemons.py` 全绿 |
| 3 | 修 5 个 engine 测试文件里对已删函数的引用 | `engine/tests/*` | 217 passed |
| 4 | **cutover_gate 去掉对旧端的 shadow 双跑**，改「结构基线 + 文件↔端点」守门 | `tools/cutover_gate.py`、新增 `tools/capture_baseline.py` | 35 用例 / 0 漂移 / 0 错误 |
| 5 | **补 ops_status 的生产者**：`ops_status.py` 接入 postclose_chain 末步 | `engine/tools/postclose_chain.py` | `--only opssnap --force` exit=0，5s，快照落盘 |
| 5b | **补回现场重算口**：新增固定动作 `ops_snapshot`，`/api/ops.json?refresh=1` 变真重算 | `app/core/ops_run.py`、`app/plugins/ops/backend.py`、`app/plugins/ops/frontend.js` | 实测 5.3s、updated_at 真变；重算失败回 `_refresh` 不丢旧快照 |
| 6 | 删 core 里 8800 遗留死代码（4 个符号 + 常量） | `app/core/main.py` | import ok；`test_shell_endpoints.py` 7 passed |
| 7 | `sector_restart` 改退役回执（不再拉起 8002） | `app/core/ops_run.py` | `run_action` → retired/410；ops 测试 23 passed |
| 8 | 删 v2bridge 里 `#amvFrame` 主题推送整段（`#amvFrame` 全仓从未创建） | `app/web/v2bridge.js` | `node --check` 通过 |
| 9 | **桌面启动器改开 8810**（原来开 8801，停服后双击=死页面） | `作战面板.cmd`、`拉起引擎.cmd` | GBK 写入后回读正常 |
| 10 | 停进程 | PID 37180 / 19824 / 25368 | 三端口均未监听 |

## 2. HANDOFF 未记载、现场发现的耦合

### P0：cutover_gate 依赖旧端（已修）
`shadow_check` 的 35 个用例全部以「8800 响应 == 8810 响应」为判据，旧端一停**每日自检必 FAIL**。
更关键：`amv_live.json` / `amv_regime_latest.json` / `position_cap_latest.json` 的生产者从来不是 8800
（分别是 `amv_live.py`、`amv_regime_export.py`、`position_cap_amv.py`），8800 只是**读文件再转发**。
所以「比对旧端」测到的其实是同一份文件。改法：停服前先抓结构基线（`baseline_structure.json`），
之后只比对**结构**不比对数值（时间戳与盘中实时值本身会漂）。

新增 `tools/check_amv_parity.py`：直接比「文件 == 8810 端点」，替代对旧端的依赖。

### P0：ops_status.json 的唯一生产者是 8800（已修）
v2 后台 Tab 只读 `data/panel/ops_status.json` 快照，而这份快照此前**只有** 8800 的
`GET /api/ops.json` 会触发 `ops_status.build()` 落盘。8800 一停，后台快照会永久冻结在
停服那一刻——而且**不报错**，是静默的假新鲜。
改法两半：
1. **夜间基线**：给 postclose_chain 加 `opssnap` 末步（不在 core 起第二个定时器，复用既有主链的
   超时与失败推送）。位置必须在所有取数步骤之后，否则会把刚更新的数据读成陈旧。
2. **按需重算**：新增固定动作 `ops_snapshot`（`OPS_ACTIONS` 第 4 个名字），`/api/ops.json?refresh=1`
   从「空参数兼容」改成真调 `build(force=True, write=True)`（~5s）。不补这半，白天打开后台
   Tab 看到的是上一夜的状态；而且卡片头写着「更新时间」，不查就会当成现值。
   重算失败不让旧快照消失，回 `_refresh:{ok:false,detail}`，前端提示「显示旧快照」。

### P1：桌面启动器指向 8801（已修）
`作战面板.cmd` 是主人日常入口：它 `run_daemons --only shell` 再开 Chrome 到 8801。
8801 退役后双击只能开出一个死页面。改指向 `tools/ensure_8810.ps1` + 8810。

## 3. 验收结果（均为本轮实测）

| 项 | 结果 |
|---|---|
| 三端口监听 | 8800 / 8801 / 8002 均未监听；10 分钟后仍未被拉起 |
| watchdog 日志 | 停服后全为「全部检查正常（无告警）」，无自愈记录 |
| 9 Tab 端口审计 `_vis/_portaudit_all_tabs.js` | 471 个请求全部 8810，旧端口 0，未捕获异常 0 |
| `pytest app/tests`（v2） | **190 passed / 1 skipped**（= HANDOFF 基线，未退化） |
| `pytest app\plugins\ops` | 119 passed，1 failed（见 §4 既有） |
| `pytest engine/tests`（全量） | 3133 passed / 24 failed；其中 2 条是本批造成、**已修**（`test_postclose_chain` 链序、`test_extreme_drill_d` 的 DAEMONS 注册），剩余 22 条为既有失败（见 §4） |
| `tools/cutover_gate.py` | 除 `pytest 全套` 外全 OK（含 AMV 快照 文件↔端点=OK） |
| `tools/check_amv_parity.py` | 三份快照 equal=True |
| 8810 `/api/health` | ok=True，18 个插件全部加载 |

## 4. 未完成 / 不在本次范围

本批自身的半径为绿（下表 §3 已列）。以下是**全量跑出来、但早于本批**的红灯，各自 owner 处理：

### 4.1 v2 全量 pytest：13 个红灯

1. **12 个 intraday 字节对齐测试**（`test_audit / test_evo / test_quotes / test_research`）
   根因：这些测试按**硬编码行号**从 `engine/web/index.html` 切片段（`audit_align.mjs` 的 `seg(a,b)`），
   而今天 19:46 的主题改动往旧页插了行，且偏移量在文件内**不一致**：
   ≤4700 区间约 +2 行（`const M =` 期望 2962 → 实际 2964），4719 处为 0。
   所以不能靠单一 delta 修，需要按段重新定锚（改前必须先确认每段起点的可辨文本）。
   影响：`PanelV2_DailyCheck` 的「pytest 全套」会持续红，直到这个测试修好。
2. **`test_panic_store_mode_shape`**：期望 `source=="store"`，实际 `eltdx:880005+880006+880009`
   ——行情迁 eltdx 后断言未更新。

### 4.2 engine 全量：22 条既有红灯（节选分类）

| 类别 | 例 | 判定依据 |
|---|---|---|
| 模块开关多出 `alert` 键 | `test_kill_total_stop`(2)、`test_idempotency_gate`(1) | 生产侧新增 alert 模块开关，测试的 `MODULE_KEYS` 未同步；本批未碰模块开关 |
| 主题 token 收紧 | `test_theme_tokens::test_hex_cleaned` / `test_switcher_does_not_touch_loadtab` | 今日主题改动的产物 |
| 告警排序/推送标签 | `test_task_health`、`test_panel_issues_356` | 期望 `pool-move`，实得 `chain` |
| 旧页 DOM 链路 | `test_kline_*`、`test_pool_track_html`、`test_kname_click*` | 旧页被主题改动插行后行号/选择器偏移 |
| 仓库内 tmp 写法 | `test_test_tmp_hygiene`（点名 `test_manual_orders.py`） | 既有违规，与本批无关 |
| 其它 | `test_schema::test_meta_source_present`、`test_focus_levels_builder`、`test_protect_backfill_t1`、`test_panel/test_alerts`、`test_panel/test_gates`、`test_kline_api` | 需各自 owner 核对 |

另：`test_side_effect_guard::test_full_suite_zero_side_effect` 本轮两次都在跑完后报红。它内部会
**递归再跑一遍全套**，本批期间我在它运行中重启了 8810、改过源码文件，不能排除是被并发干扰；
单独复跑结果见本轮回执，若仍红需另开工单（它检的是「全套跑完有无残留副作用」，与本批无直接关系）。

## 5. 回滚

| 改动 | 回滚动作 |
|---|---|
| watchdog 探活/自愈 | 恢复 `check_server/check_amv_dashboard/check_shell` 与 CHECKS 注册行 |
| run_daemons 三项 | 取消 `DAEMONS` 里三条注释 |
| cutover_gate | 恢复对旧端的 shadow 双跑（需旧端重新在跑） |
| ops_status 末步 | 删 `opssnap` 步骤与 Phase 4 调用 |
| 进程 | 重新拉起 `engine/server.py` / `dashboard/server.py --port 8002` / `shell_server.py --port 8801` |

## 6. 明日开盘前要确认

- `cond_watch`（条件单评估）与撑合监控已从 `server.py` 迁出为独立常驻进程，**只在交易日
  09:00–15:30 运行**（今日 19:08 已 EXITED）。开盘后若不拉起，条件单不触发且**不报错、不推送**。
  watchdog 的 `check_cond_watch` 已注册，但开盘那一刻值得看一眼。
