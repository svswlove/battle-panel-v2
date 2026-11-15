# M4 切流评审（sector → amv → battle_plan → kline）

**结论**：sector / amv 满足切流条件；battle_plan 只能切"看"、写入口永久留在 8800；kline 是子集，允许不切。
**日期**：2026-10-22 ｜ **执行人**：01 ｜ **审核**：嗅嗅 ｜ **拍板**：主人
**前置铁律**：旧 8800（pid 25652）/ 8002（pid 31328）/ 8801（pid 62228）全程不停、代码一行不动。切流只改"看哪里"，不改任何旧进程与旧文件。

## 0. 切流的准确含义

| 是 | 不是 |
|---|---|
| 日常看盘入口，从 8800/8002 的对应 Tab 换到 8810 对应卡片 | 不删旧路由、不改 `engine/server.py`、不改 `engine/web/*` |
| 旧端口继续运行，作为随时可用的回退路径 | 不接 QMT、不下单、不写 `data/panel`、不写生产库 |
| 每个域独立判定、独立回退 | 不一次全切；不因为"整体差不多"就一起上 |

新版插件 `data.write` 全为空，代码里没有对 `data/panel` 或生产库的写调用（实测：`app/plugins/*/backend.py` 无 `open(...,'w')` / `json.dump`）。因此**回滚不需要回滚数据**，停 8810 即回到切流前状态。

## 1. 切流前必须满足的条件

### 1.1 全局前置（四域共用）

| # | 条件 | 现状 | 缺口 |
|---|---|---|---|
| G1 | 全套测试通过 | **151 passed**（core 52 + sector 50 + kline 10 + amv 13 + battle_plan 6 + watchlist 11 + 比较器自检 9） | 无 |
| G2 | 全部插件健康 | `/_plugins/health` → `ok=true`，当前 5 插件（sector/kline/amv/battle_plan/watchlist）全部 `ok=true` | 无 |
| G3 | 端点全通 | sector 3 个、amv 6 个、battle_plan 5 个、kline 2 个全 200；`POST /api/p/battle_plan/plan` → **405**；`level=XX` → 400；未知标的 → 404；`backend.py` / `plugin.json` 经 `/plugins/` 访问 → 404（只暴露 `frontend.js`） | 无 |
| G4 | 8810 有可靠启动入口 | 当前是手工起（父 29488 `envs\qmt312\python.exe -m uvicorn`，子 28788），**不在 `run_daemons.DAEMONS`、不在 `watchdog` 探活**（两文件对 8810 命中 0 次） | **已补**：`tools/ensure_8810.ps1` 单次/循环探活拉起，不碰旧 watchdog；见 `docs/launcher.md` |
| G5 | 工作区干净 | kline 3 个文件 + kline 文档已改并提交；shadow 工具/M4 文档同批提交 | 无（切流前再确认一次） |
| G6 | 回滚点已记录 | 记下 8810 pid 与启动命令；旧三进程 pid 已记录 | 无 |

### 1.2 分域条件

| 域 | diff 结果（证据） | shadow 观察时长 | 测试 | 回滚点 |
|---|---|---|---|---|
| **sector** | PASS。260,026 字段/元素零差异，`main_diffs=0 / missing=0 / extra=0`，1,533 次 HTTP 0 重试。tree 724 节点 × 8 字段、rank 5 period × 4 层 × 9 字段、series 全量 722 code × 313 项。唯一 12 条差异（`period=10/20`）经旧端 `/api/board_flow` 与 `/api/sector_level` 双向验证，判为旧端缺陷，`unexplained=0` | **≥1 个交易日**，且必须跨过一次 16:10 `OH_PostCloseChain`（验证新端点能读到当日新产物） | 25 项，含 catalog/search/members、`days` 裁剪、level 400、跨层 `amv_pct` 不串、连接关闭 | 8002 未改一行；停 8810 即回 8800「板块」Tab（iframe 8002） |
| **amv** | PASS。三份原始 JSON 与旧 8800 **逐对象精确相等**（`==`，无容差）：`amv_live` 15 keys / `amv_regime_latest` 11 keys / `position_cap_latest` 14 keys，全部 `equal=True` | **≥1 个交易日**，且跨过一次 16:10 主链（amv_regime / amv_breadth / position_cap 三产物重写）+ 覆盖一次盘中 live 刷新 | 5 项，含 stale 判定与缺文件 503 | 8800 AMV 卡未改；停 8810 即回 |
| **battle_plan** | PASS（只读口径）。`battle_plan.json` 19 keys、`alert_todo.json` 7 keys **精确相等**；`real_positions.json` 原始 7 keys 相等，旧端多 `positions_list` / `market_value_total`（实时估值，新版未迁移） | **≥1 个交易日**，且期间必须发生一次签收/改单，切流端刷新后与 8800 读到同一份文件 | 3 项，含"无写路由"断言 + POST 405 实测 | 8800 作战表 Tab 与全部写接口未改；停 8810 即回 |
| **kline** | 部分 PASS。7 标的中 6 个零差异（共同 bars 119/119/119/119/116/117/117）；`399001.SZ` 12 条差异，最大约 0.03，归因**源精度**（旧端 3 位 `11820.012`，`track_pool.db` 存 2 位 `11819.99`），非复权公式差异 | **≥2 个交易日**，且跨过一次 18:00 `HanaTrack_Daily18_Resume` 灌库（验证 `last_bar_date` 随灌库推进，不是卡在 T-2） | 6 项，含 qfq 公式、边界字段 `intraday/last_bar_date/notice`、旧字段不变、quote 形状 | 8800 K 线卡未改；停 8810 即回（旧端仍有当日 bar 与指标列） |

### 1.3 已知差异（切流前必须认下，不是缺陷）

| 域 | 差异 | 处置 |
|---|---|---|
| sector | `series?period=10/20` 新版返回真实值（旧端静默回落 `period=1`）→ 切流后数值**会变** | **已取证**（2026-10-23）：旧端 `?period=10` 回显 `period=1` 且响应体与 `?period=1` 逐字节相同。归因为旧端缺陷，`diff_sector.py` 自动标记 `old_side_period_collapse`；前端无依赖旧错值的判断 |
| sector | `days` 默认 60（旧 80，实测），上限 250 | 卡片显式传 `days=60`，已如此 |
| sector | `level` 非法：400（旧 200 + 空 rows）；库不可用：503（旧 500，**未实测**，来自契约文档） | 前端枚举固定，无影响 |
| sector | 旧 `/api/series_{code}.json` 静态文件形态未实现 | 切前确认无前端直连该静态路径 |
| amv | 旧 `/api/amv_refresh`（POST，触发 AMV 重算）**不迁移、不调用** | 需要刷新时回 8800 点按钮；8810 不提供该能力 |
| battle_plan | 无 `positions_list` / `market_value_total` | 持仓市值列暂缺；要市值回 8800 看 |
| battle_plan | `battle_plan.json` 的 `direction_forecast.judgment` 内容 stale（写"AMV 空档第 40 日 / MA10 139,824.51"，实际第 14 日 / 143,013.53） | 上游文档问题，插件只读不改；见 §4.5 |
| kline | 缺当日未收盘 bar、缺 `yellow/white/amv/amvMa/amvWhite/chg/amp/turnover` | 见 §2.3 |
| kline | `limit` 语义：旧端固定约 120 根不截断；新端 1–500 生效 | 前端传值需固定，不按"旧端不截断"假设 |

## 2. 分域切流顺序

顺序固定为 **sector → amv → battle_plan → kline**，一步一切、前一步观察满时长再进下一步。不并行。

### 2.1 为什么是这个顺序

1. **sector 先切**：只读、全量零差异、数据是 T-1 收盘、不参与盘中任何决策。它切下去验证的不是 sector 本身，而是"8810 作为日常入口能不能用"这件事。
2. **amv 第二**：数据精确相等，但盘中 `live` 每 60 秒变、且是 `position_cap` 的输入，时效敏感；切完要盯满一个盘中，确认 stale 判定不会误报。刷新动作仍要回 8800，需要主人习惯切换。
3. **battle_plan 第三**：看着最安全（只读影子），实际最贴交易闸门——签收状态是交易前置的唯一事实源。切了之后"看"在 8810、"写"在 8800，人的操作路径分叉，必须先把写入口明确钉在 8800 再切。
4. **kline 最后，且允许不切**：唯一"功能少于旧端"的域，切了等于接受降级。等前面三个域证明 8810 稳定，再决定要不要为一张 K 线卡承担降级。

### 2.2 battle_plan 为什么只读、也不接交易

| # | 理由 |
|---|---|
| 1 | **签收是交易前置闸门**。`battle_plan.json` 的 `sign.status` 是唯一事实源，写它等于改交易决策状态，不属于"展示迁移"范围。 |
| 2 | **急停牵动三处状态**。旧 `/api/kill` 同时改 `battle_plan` / `module_switches` / `engine_state`；新版没有这些状态机，接了就是半截实现。 |
| 3 | **单一写入口是硬约束**。旧 8800 有 41 个写端点（下单、撤单、签收、改单、手动持仓、`module_switch`、`alert_plan`、`ticket_policy`、条件单等），新版一个都没有。两端同时写同一份 panel JSON 会互相覆盖。 |
| 4 | **新版没有订单链路**。不连 QMT、没有订单状态机、没有回执与失败重试；就算接了写接口，也无法保证下单结果。 |
| 5 | **收益不抵风险**。只读影子已覆盖"看作战表/持仓/告警"的需求；接交易省下的只是"少开一个页面"，代价是双写与交易状态污染。 |

结论：battle_plan 在 8810 上**永久只有 GET**。要接交易另开版本、另做评审，不在 M4 范围。

### 2.3 kline 子集风险

| 风险 | 具体后果 | 缓解 |
|---|---|---|
| 缺当日未收盘 bar | 盘中最后一根是 T-1，拿它当"今天"会滞后一整天 | 响应固定 `intraday=false` + `notice`；卡片固定徽标「历史日线 · 不含当日/指标」，状态行显示「至 `<last_bar_date>`」 |
| 缺指标列 | `yellow/white/amv/amvMa/amvWhite/chg/amp/turnover` 未迁移；依赖指标叠加（主力动向、买卖点）的判断在 8810 复现不了 | 这类判断一律回 8800；kline 不重算 AMV（一个指标一个 owner） |
| `399001.SZ` 源精度 | 旧端 3 位、库 2 位，末位差约 0.03；整数关口/阈值比较可能翻边 | 已知差异，做阈值判断时回 8800 取数 |
| `limit` 语义不同 | 旧端固定约 120 根不截断；新端 1–500 生效。传大值会改变前端窗口长度 | 前端固定传值，不依赖"旧端不截断" |
| 覆盖只有 7 个标的 | 其余标的未验证；缺 `factor` 时按原始价返回并给 `warnings`，不是静默正确 | 切流前明确：kline 只保证已 diff 的 7 个标的 |

## 3. 旧 8800 / 8002 保留一周的回退方案

### 3.1 保留方式

| 项 | 做法 |
|---|---|
| 旧进程 | 8800 / 8002 / 8801 全部继续运行，`run_daemons` 与 `watchdog` 注册不变（watchdog 仍在探 8800 `/api/health`、8002 `/health`、8801 `/api/shell/health`） |
| 旧代码 | 一行不动：`engine/server.py`、`engine/web/*`、8002 `server.py` 全不改 |
| 旧数据 | 新版不写 `data/panel`、不写生产库，旧链产出路径不变 |
| 观察期 | 每域切流日起 **7 个自然日，含 ≥5 个交易日**；四域各自计时，不合并 |

### 3.2 回退动作（一条命令）

```powershell
# 查 8810 进程（父进程是 envs\qmt312\python.exe -m uvicorn）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*port 8810*' } | Select-Object ProcessId, CommandLine

# 回退：停 8810，旧端口立刻成为唯一入口
Stop-Process -Id <8810 的 pid>
```

回退**不需要**改配置、不需要恢复数据、不需要重启旧进程。若只是单域出问题，可改该插件 `plugin.json` 的 `"enabled": false` 后重启 8810（只停该域卡片，其余域不受影响）。

### 3.3 停止条件（任一命中即中止观察）

1. 8810 单日不可用 ≥2 次，或累计不可用 ≥30 分钟（含因无自愈而人工恢复的时间）。
2. 任一域出现与 diff 报告不符的数值差异：同参数、同日、同字段不相等。
3. 8810 返回 5xx / 504，或 `/_plugins/health` 标红。
4. 生产库或 `data/panel` 出现非预期写入（mtime 变化与 16:10 / 18:00 产出链对不上）。
5. 需要写操作时 8800 不可用（写入口断了，8810 顶不上）。
6. 发生一次误读事件：因 kline 缺当日 bar、或 battle_plan 缺市值列，导致一次错误判断或错误下单意向。

命中后动作：停 8810 → 回旧端口 → 在 `docs/` 记一条根因 → 修完重新计 7 天。命中第 2/3/4 条即使当场修好，也重新计时。

### 3.4 观察期满的判据

同时满足才算通过：7 天内 0 次停止条件命中；`/_plugins/health` 全绿；每域至少一次人工"切过去→核对→切回来"验证读数一致；期间旧端口 0 次异常重启（异常重启说明回退路径本身不可靠）。

## 4. 需要主人 / 01 确认的事项

| # | 事项 | 建议（默认执行） | 需谁拍板 |
|---|---|---|---|
| 4.1 | **交易链**：8810 是否永久只读？ | 永久只读。写端点另开版本，需独立评审（下单/撤单/签收/急停各自的状态机与回执都不在本版） | 主人 |
| 4.2 | **旧端口下线**：一周观察期满后，8800 / 8002 下线还是并行？ | 不下线。只把"看板默认入口"改到 8810，旧端口作为回退长期保留；下线另议，且必须先有 8810 自愈 | 主人 |
| 4.3 | **8810 自愈**：是否允许改 `engine/executor/run_daemons.py` + `watchdog.py`（各加一行 8810 注册/探活）？这是旧仓文件，与"旧一行不动"有张力 | 观察期内**不改**，用 `tools/ensure_8810.ps1` 独立入口拉起 8810（单次或 `-Loop`），回滚成本为零；观察期满且稳定后再决定并入自愈 | 01 定，主人知会 |
| 4.4 | **入口形态**：8810 独立页面，还是接进 8801 外壳？接外壳要改 `engine/web/shell.html` 的 `ENGINE` 常量（现写死 8800），属旧文件 | 观察期用独立页面 `http://127.0.0.1:8810`，不动 8801 | 01 定，主人知会 |
| 4.5 | **文档口径**：README / TASKS 曾写 `64 passed`，实际已 146；`battle_plan.json` 的 AMV 段内容 stale | README / TASKS / M4 基线已改为 146；作战表卡片已加只读 stale 提示，`battle_plan.json` 内容修正另开工单 | 01 |
| 4.6 | **kline 是否切**：接受"历史日线子集"作为长期形态，还是排期补当日 bar？ | 先不切，或只切给"明确只要历史日线"的场景。补当日 bar 需接独立实时源，属新版本 | 主人 |
| 4.7 | **diff 工具缺口**：`tools/` 已有 `diff_sector.py`、`diff_amv.py`；全域网关用 `shadow_check.py`（覆盖 sector/amv/battle_plan/kline，15 项） | 观察期每轮跑 `python tools\shadow_check.py --once`，报告落 `data/panel_v2/shadow/` | 01 |
| 4.8 | **sector members period 10/20 与旧端差异**（93 处，全为北交所 920xxx） | 旧端 vipdoc 路径缺陷导致 `real_pct=None`；新版按 `920` 前缀归北交所后有值。**以新版为准**，不回退；日常 shadow 只跑 period=1 | 01 |
| 4.9 | **`/api/index_closes` / `/api/hcl_idx` 不迁** | 旧端读 vipdoc 二进制给 15 年；`track_pool.db` 只 4 年且缺 `000510.CSI`，迁了是降级。这两个端点继续用旧 8002 | 01 |

## 5. 验收清单

### 5.1 切流前（每域切之前重跑一次）

- [x] `cd OH-WorkSpace\projects\panel-v2; .\tools\run_daily.ps1` → `PASS`，退出码 0（2026-10-23 03:18 实测，经计划任务包装路径 `scheduled_check.ps1` 跑通）
- [x] `python -m pytest -q` → `151 passed`（2026-10-23 03:1x 实测）
- [x] `GET http://127.0.0.1:8810/_plugins/health` → `ok=true`，全部插件 `ok=true`（2026-10-23 03:0x 实测）
- [x] `GET http://127.0.0.1:8810/_plugins` → `errors=[]`（2026-10-23 03:0x 实测）
- [x] `python tools\diff_sector.py --rank-periods 1,3,5,10,20 --rank-dates 2026-10-20,2026-10-19 --full-series` → **退出码 0，VERDICT=PASS**（2026-10-23 03:5x 实测）：tree 5,080 / rank 28,960 / series 全量 225,986 字段零差异；period 10/20 的 12 处差异经「旧端 period 塌缩」铁证归因（旧端请求 `period=10` 回显 `period=1` 且响应体与 `period=1` 逐字节相同），**unexplained=0**
- [x] `python tools\diff_amv.py` → 三对 `equal=True`，退出码 0（2026-10-23 03:1x 实测）
- [ ] `python tools\shadow_check.py --once` → **31/31 pass**，退出码 0
- [x] battle_plan：`/plan` 与 8800 `/api/battle_plan.json`、`/alerts` 与 `/api/alert_todo.json` **逐对象相等**（已纳入 shadow 25 项用例，每轮自动比对）
- [x] battle_plan：`POST /api/p/battle_plan/plan` → **405**（2026-10-23 03:1x 实测）
- [x] kline：6 个标的（000001.SH/SZ、600000.SH、300750.SZ、399006.SZ、399001.SZ）已纳入 shadow 自动比对；`512480.SH` 因 factor 表缺 2026-07-03 份额折算事件（接口已 `warnings` 明示）**暂不入日常 shadow**
- [x] kline：`/bars/000001.SH` 返回含 `intraday=false`、`last_bar_date=20261022`、`notice`（2026-10-23 03:1x 实测）
- [x] 记录 8810 pid + 启动命令 + 旧三进程 pid 到本文件（2026-10-22 23:20 实测）：8800=41472 / 8002=29336 / 8801=16088 / 8810=67108；8810 启动命令 `envs\qmt312\Scripts\python.exe -m uvicorn app.core.main:app --host 127.0.0.1 --port 8810`
- [x] kline 4 个文件及 shadow/M4 文档已 commit
- [x] 确认前端无直连 `/api/series_{code}.json` 静态文件（2026-10-22 静态扫描 `app/**/*.js|*.html`，命中 0）

### 5.2 每域切流（逐域勾，不跳步）

- [ ] sector 切：8810「板块」卡片读数与 8002 板块 Tab 同参数一致；观察满 1 个交易日（含 16:10 后核对）
- [ ] amv 切：8810「活跃市值」卡片与 8800 AMV 卡读数一致；确认刷新仍需回 8800；观察满 1 个交易日
- [ ] battle_plan 切：8810「作战表」与 8800 作战表读数一致；确认签收/改单仍在 8800 且 8810 刷新后同步；观察满 1 个交易日
- [ ] kline：决定切 / 不切（§4.6）；若切，观察满 2 个交易日（含 18:00 灌库后 `last_bar_date` 推进）
- [ ] 每域切完记一行：切流日 / 观察截止日 / 是否命中停止条件

### 5.3 观察期内每日

- [ ] `/_plugins/health` 全绿
- [ ] 8810 当日可用时长（不可用次数、累计分钟）
- [ ] 旧 8800 / 8002 当日 0 次异常重启
- [ ] 当日无停止条件命中（命中即执行 §3.2 并重新计时）

### 5.4 观察期满

- [ ] 四域全部满足 §3.4
- [x] 回退演练做过一次（2026-10-22 23:21 实测）：停 8810（pid 25396/67108）→ 探测 8800/8801/8002 全 ok → `ensure_8810.ps1 -Once` 拉起 → 0.03s 内 `/_plugins/health` ok、五插件全绿 → 再跑 `cutover_gate.py` PASS
- [ ] 输出一份观察期记录（可用时长、命中项、误读事件）附在本文件末尾
- [ ] 旧端口去留按 §4.2 结论执行

## 6. 观察记录

| 日期/时点 | shadow | cutover gate | 备注 |
|---|---|---|---|
| 2026-10-22 15:54 收盘后 | 10/10 pass | PASS（含 pytest 82） | sector/amv/battle 第 1 个交易日 |
| 2026-10-22 16:08 收盘后 | 10/10 pass | PASS（skip pytest） | kline 第 1/2 个交易日 |
| 2026-10-22 21:58 夜间 | 15/15 pass | PASS（含 pytest 94） | sector v0.3 + amv v0.2 新增 5 项用例全过 |
| 2026-10-22 23:05 夜间 | 20/20 pass | PASS（含 pytest 105） | sector v0.4（语义榜/趋势/板块TOP/资金流）+ amv v0.3（广度）+ watchlist v0.2（行情） |
| 2026-10-22 23:14 夜间 | **20/20 pass** | PASS（含 pytest 109） | 追加 watchlist `/levels`（关注池关键位）+ amv `/all_a`（全A快照） |
| 2026-10-23 02:3x 凌晨 | **31/31 pass** | PASS（含 pytest 146） | sector 0.5.x（contrib/bull）+ kline 0.3.0（四态背景带）；shadow 收紧：`missing_in_new` 判 fail、空交集判 fail、known-defect 限代码范围 |
| 2026-10-23 03:15 凌晨 | **31/31 pass** | PASS（含 pytest 151） | sector v0.6.0：`/score_trail` + members 五字段（口径源 `工具/panel_scores.py`）；shadow 新增 score_trail 用例，并撤销这五个字段的「有意不搬」声明 |
| 2026-10-23 03:18 | 31/31 pass | PASS（含 pytest 151） | 手工跑；同日第 2 次 |
| 2026-10-23 08:50（T39 自动） | — | **FAIL** | 假红：计划任务会话无 PYTHONUTF8，子进程输出按 CP936 解码，`_readerthread` 崩。已修并钉死编码 |
| 2026-10-23 10:03 | 31/31 pass | PASS | 手工复跑，确认 08:50 是编码问题不是真回归 |
| 2026-10-23 10:07 | 30/31（positions fail） | FAIL | `market_value_total` 旧 78.3/新 78.4 跳价误判。新增 `live_skew` 状态修掉 |
| 2026-10-23 11:27（T39 真触发） | **31/31 pass**（positions=live_skew） | **PASS**（15.4s，pytest 151） | 计划任务端到端跑通，`exit=0` |
| 2026-10-23 11:5x 盘中 | **31/31 pass** | **PASS**（17.8s，pytest 159） | sector 0.6.2（周期切换）+ core 0.2.1（卡片自报刷新） |
| 2026-10-23 12:38 | **35/35 pass** | **PASS**（14.5s，pytest 195） | kline v0.4.4：新增 `/intraday`、`/quote5`、`/volume_profile`、`/intraday_multi` 四端点（core 新增行情门面）；分时 120 点零差异、多日分时 1084 点零差异、筹码 954 价位零差异；五档 3 处差异归为旧端 aux 缓存 30 分钟的已知缺陷 |
| 2026-10-23 17:00（收盘后） | **33 pass + 2 old_gap** | **PASS**（pytest 197） | **kline 第 2/2 个交易日已满足**：库已推进到 20261023（1414.5 万行），`last_bar_date=20261023`，分时定格 241 点（15:00 收盘 1335.0）；黄白线在 4 个个股上窗口完全对齐、127 值逐值零差异。2 项 old_gap = 旧端 `main_flow` 全 null、新版有真值 |
| 2026-10-23 17:04 | **35/35 pass** | **PASS**（18.0s，pytest 200） | 收盘后复跑；`main_flow` 已随旧端落盘恢复，old_gap 归零 |
| 2026-10-23 19:20（T39 自动） | 待跑 | 待跑 | 自动化复跑（人工已于 17:00/17:04 验过） |

---

**证据索引**：`docs/sector_diff_report.md`、`docs/amv_diff_report.md`、`docs/battle_plan_diff_report.md`、`docs/kline_diff_report.md`；产物 `tools/sector_diff_result.json`、`tools/amv_diff_result.json`。
**本次评审未改代码、未改其他文档、未提交 git。**
