# 当前状态（2026-10-25 凌晨 · 前端冲刺：逐卡验收 / 主题跟随 / ops 卡化）

> 基线：本单的 39 例新测试全绿；**全量 pytest 1347 passed / 2 failed / 4 skipped**，两红均在 `compat_misc`（并发工单的后端改动，不在本单白名单，见下）。
> `tools/cutover_gate.py` 8 项中 7 PASS、`pytest 全套` 因同两红 FAIL（`docs/cutover_gate_latest.md`）。
> **本轮**：九个 Tab 全部走 v2 卡（`v2_map.json` 无 `old`）、新增 `ops.main` 卡、12 张卡颜色全部吃 `themes.css` token，
> 逐卡实点修掉 4 处「看着能点却点不动」的真缺陷。详见 `docs/HANDOFF_20261025_fe_sprint.md`。
> 旧 8800/8002/8801 未动、未重启；8810 本轮重启过一次（让 `ops/plugin.json` 的新 `frontend.cards` 生效）。

## 前端冲刺（2026-10-25 凌晨）

| 段 | 内容 | 状态 | 证据 |
|---|---|---|---|
| A 逐卡实点 | 12 张卡 × 点击 / 写操作 / 刷新 / 跨卡跳转四类；修 4 处真缺陷（仪表盘徽标跳转无监听、自选池个股名不走桥、复盘表格个股名未实现、选股行缺跳转占位）；另修 amv 涨跌配色写反 | **完成** | headless CDP 实测；`docs/HANDOFF_20261025_fe_sprint.md` §A |
| B 主题跟随 | amv 13 hex / kline 28 hex+10 rgba / sector 56 hex+3 rgba / review 11 hex+2 rgba → 全部改 token；canvas 走 `palette()` 现取；七套主题 × 三档字号逐卡验；新增 4 道静态守门 | **完成**，有已知例外 | `docs/HANDOFF_20261025_fe_sprint.md` §B；`app/tests/test_fe_shell_bridge.py` |
| C ops 卡化 | 新增 `app/plugins/ops/frontend.js`（`ops.main` / order 70 / 340 行）+ manifest 声明 + 21 例测试；三张表 22/16/8 行、汇总三项、两类红卡、红线只显示、手动更新过确认 | **完成** | `v2_map.ops = v2`；8810/8821 双实例实测；`app/plugins/ops/tests/test_ops_frontend.py` |
| D 收口 | 「映射改回 old 能回旧界面」实测（`ops` 改 `old` → iframe 0 个、旧 panel 16937 字）；本文件更新 | **完成** | `docs/HANDOFF_20261025_fe_sprint.md` §D |

### 本轮未收的 2 例红（并发工单，非本单）

`compat_misc/tests/test_compat_misc.py::test_no_http_or_file_write`（后端顶层新增了 `import os`）与 `::test_selection_board_options`（catalog 不再含 `CONCEPT` 层）。两者源于 `app/plugins/compat_misc/backend.py` 02:07 的并发改动，**该文件不在本单白名单**，只记录不修。

### 本轮新增的已知例外（不是缺陷）

- **review 卡 `#panel-review` 作用域内的 10 条规则保持旧页原始色值**：`test_scoped_css_rule_headers_match_legacy` 锁死「搬入段与旧页逐字一致」，改 token 会破该 test。只把作用域外的 `--judge-*` 三值换成 `var(--warn-*)`。
- **`--theme-manifest` 实际只有 7 套主题**（月白/亚克力/夜航/漫画/暖纸/霜璃/夜璃），不是「九套」；按 manifest 唯一源验了 7 套，另按 `[data-theme="frostglass"]` 块直接验。


## 方向（主人 2026-10-24 裁定）

1. **全部插件化、不走转发**：中枢（core）+ 可插拔插件；装卸一个功能只动一个文件夹。
2. **不是搬家，要有优化**：每次写自动备份+原子+审计（旧系统没有）；全部可单测；改一个域不用读全系统。
3. 验收流程：R1~R5 完成后由嗅嗅做对抗性审查+活体探测，01 只收分级报告做最终抽查与提交。

## 第一波（commit 9ba3032 → f0bf025，已提交）

| 域 | 内容 | 证据 |
|---|---|---|
| core 状态写机制 | `WRITABLE_STATE`（engine_state/kill_switch/ui_notes）+ `ctx.state_write` + `ctx.state_json` + manifest `data.state`；契约 `docs/CONTRACT_STATE.md` | 白名单拦截探针；977 tests |
| battle_plan_rw 0.2.0 | 原生写域 7 端点：sign/kill/toggle/cancel/signal_watch×2/manual_position；受控写 battle_plan.json + engine_state + kill_switch；两处历史事故语义照搬 | 51 tests；幂等签收/急停/解除端到端与旧端一致；生产零净变更 |
| ops 0.3.0 | ui_notes 四件套、ops.json 读、trade_mode 读写、alert_plan toggle/delete、ops/run、panic、amv_refresh、sector 三动作、kline 周边两端点 | 与 8800 逐字段对齐；trade_mode 幂等写 changed=false |
| compat_sector 0.2.0 | 板块域 10 读端点 | 9/9 与旧端结构一致；temperature 逐值一致 |
| compat 0.2.0 / kline 0.7.0 | `{name}.json` 通配、amv_research 全量实现、volume_ticks | amv_research 5 组参数与 8800 零差异 |
| 前端 | v2 插件卡经 v2bridge iframe 嵌入旧首页（commit 30d4f5f） | 旧页 Tab 出现、6 卡渲染、控制台零错误 |

## 第二波 R1~R5（**未 commit**，工作区改动）

| 单 | 内容 | 状态 | 证据 |
|---|---|---|---|
| R1 intraday | 新插件 0.1.0（manifest+backend+frontend 34KB+tests 25 条）：冰点监控/AMV 实时/宽度 880/板块轨迹摘要/盘中脉冲；另加 `/summary` 端点（轨迹 2.67MB → 8.2KB） | **完成**，HANDOFF 已落 | 25 tests；CDP 实测五块全有数；`docs/HANDOFF_20261024_R1_intraday.md` |
| R2 selection | 新插件 0.1.0：`selection.main` 卡（前端消费 compat_misc 四个既有根端点，backend 空 router 占位避免 root 路径冲突） | **完成**，HANDOFF 已落 | 卡可渲染、`/api/selection` 200；`docs/selection_contract.md`、`docs/HANDOFF_20261024_R2_selection.md` |
| R3 concept+review | concept 0.1.0→0.2.0 补 `concept.main` 卡；review 补 `frontend.js` + `frontend.cards`（`review.main`） | **完成**（审计 Rfix 后闭环），HANDOFF 已落 | `/_plugins` 清单两卡均注册；`docs/HANDOFF_20261024_R3_concept_review.md`、`docs/HANDOFF_20261024_Rfix_audit.md` |
| R4 bridge | `v2_map.json` 新增（出厂全 old）+ `v2bridge.js` 重写（逐 Tab 替换 + 三条回退判据）+ `shell.js` 支持 `?card=` | **完成**，HANDOFF 已落 | CDP 九 Tab 默认不变、切 v2 生效、三条回退 4.2~4.5s；`docs/HANDOFF_20261024_R4_bridge.md` |
| R5 misc | compat 0.2.0→0.3.0（`/api/status` trade_mode 修复）+ quotes.xdxr 指数判断 + 本文件 | **完成** | `docs/HANDOFF_20261024_R5_misc.md` |
| Rfix 审计修复 | 嗅嗅两批审计的 3×P1 + 2×P2：`breadth_880.prev` 锚改快照交易日、review 卡 3 条 @media 补回 + 重复注入去重 + `.rv-gap` 改名 `.rv-sp-gap`、concept 卡占位芯片不再吞详情 | **完成**，HANDOFF 已落 | `docs/HANDOFF_20261024_Rfix_audit.md` |

## 与旧系统的差异（优化点）

1. 每次写生产文件自动备份到 `data/panel_v2/backups/<name>/` + audit.log 审计（旧端无备份无审计）。
2. 写域保留旧端两处事故修复（空签收不降级、kill on→on 不覆盖快照），并有测试锁死。
3. 改任何域只读一个插件文件夹（manifest 边界 + tests 自包含）；旧 600KB server.py 退出写路径。
4. `/api/status` 的 `trade_mode` 已从 null 修成真值（与 8800 同参逐字段一致）。
5. `xdxr` 对指数码返回空数组（**新版修正旧端缺陷**：旧端 `000001.SH` 会返回平安银行的 81 条除权记录）。

## 已知边界 / 待验收

- **8810 已重启**（2026-10-24 23:5x，提交 `17dece4`/`16a33d8`/`b299516`/`db73eca`）：18 插件、`trade_mode` 正常、`breadth_880.prev` 已修。**且已改为由计划任务 `PanelV2_KeepAlive` 拉起**（进程树归 Task Scheduler，不随 HANA 宿主退出）。
- **review / concept 卡已挂且已接旧页**：2026-10-25 起 `v2_map.json` 九个 Tab 全 `v2`，不再有 `old`。回退方式：改对应 Tab 的值回 `"old"` 即回旧界面（`ops` 已实测）。
- **跨 iframe 桥**：v2 卡内 `data-open-kline` / `data-open-amv` 经 postMessage 交父页，转旧页 `KLineApp.open` / `AmvCard.open`（`app/web/v2bridge.js` + `shell.js`）。2026-10-25 补了三张卡缺的占位属性（watchlist / selection / review）与 `.TI` 指数码实点（4 个 Tab 全通）。已知边界：shell 侧只发 `code`+`name`，不带 `list`，所以卡内点击没有「上/下一只」的 `1/N`。
- **门禁 pytest 已改为扫整个 `app/`**（`b299516`）：此前手工枚举目录，漏了 compat_kline(68)/selection(81)/intraday/core_paper(111) 共 260+ 例，门禁却一直打印「全套 OK」。现覆盖随目录结构走，1328 例。
- **`amv_research live=1` 已接**（`17dece4`）：core `app/core/amvlive.py` 门面 + 契约 `data.amv_live`，`live=1` 真接 `stocks_snapshot()`。**未验证**：盘中真实快照路径（QMT 未开），周一盘中验。
- **`sector_kline` 不是缺陷（2026-10-24 实测定案）**：8810 走 `eltdx:daily_bars`、8800 走 `vipdoc:sh881xxx.day`，两者在 36 个共同日期上 OHLC 与量额**零差异**；差别只是 8810 数据更新（已到 20261023，8800 只到 20261022）。不改。
- **`/api/gates` 持仓门 trade_mode 已修**（`db73eca`）：原先 `mode` 写死 `live`，paper 模式下持仓来源/stale 判据与 8800 三处不一致；修后 gate2 六项逐字段对齐。
- volume_ticks 与旧端差 1–2 笔：eltdx 分页边界抖动，非逻辑差异。
- 全量首轮偶发：selection / compat 各出现过一次单测失败，单跑与复跑均通过，属并发改动窗口，未定位（现全量 1328 绿）。
- 前端 v2 原生卡替换 **已完成**（2026-10-25）：九个 Tab 全 `v2`，`ops` 补上唯一缺卡。
- **未验证（2026-10-25 验收窗口所限）**：① 自选池拖拽（headless 无法真跑 HTML5 drag，只验事件接线）；② 盘中数值真跳动（验收在周日凌晨，行情 T-1）；③ 纸面下单→成交→账户全链路（服务端 `409 非交易时段` 属正确行为）；④ 红线任务真触发（刻意不验，会跑生产任务）。

## 周一盘前清单

1. ~~commit 第二波改动~~ → **已完成**（`17dece4`/`16a33d8`/`b299516`/`db73eca`，工作区干净）。
2. ~~重启 8810~~ → **已完成**（18 插件，且已改由计划任务拉起）。
3. **入口切 8810**（旧界面零功能损失），按 `docs/M4_CUTOVER.md` 切流；切流前跑一遍 `python tools\cutover_gate.py`。**← 唯一剩下的正事**
4. 嗅嗅两批审计报告（`docs/audit_20261024_R124.md`、`docs/audit_20261024_R35.md`）的 P1 已全部修完、P2 修了 2 项；剩余 P2（R2/R3 自报计数对账、降级链夹具入库、并发自测打生产端点）按小单处理。
5. 小单队列现状：`amv_research` live 口径 ✅ 已接（盘中待验）、`sector_kline` ✅ 实测非缺陷、gate pytest 目录 ✅ 已改扫全 app、跨 iframe postMessage 桥 ✅ 已做（R5）。**队列清空。**
