# battle_plan 插件契约（v0.3.0，只读影子 + 签收代理）

> **2026-10-24 更新（N1）**：签收/急停/启停/取消/择机单/手动仓位已改为**原生写域插件**
> `battle_plan_rw`（`mount=root`，写 `battle_plan.json` + `engine/state`），不再转发 8800。
> 写域完整契约见本文末「battle_plan 写域契约」一节；上半部分只讲只读影子插件 `battle_plan`。

**插件**：`app/plugins/battle_plan/`（id=`battle_plan`，v0.3.0）
**挂载前缀**：`/api/p/battle_plan`
**安全边界**：插件路由只有 GET；不接 QMT、不写 state/账本、不下单；任何 POST/PUT/PATCH/DELETE 返回 405。
签收是 **core 挂载的代理**（`POST /api/sign` → 旧 8800），不是本插件路由，见下文「卡片与签收交互」。
**数据源**：只读 `data/panel/` 的 `battle_plan.json` / `real_positions.json` / `alert_todo.json`。

| 端点 | 说明 |
|---|---|
| `GET /summary` | 作战表日期/签收/仓位上限/账户/持仓/告警/待办汇总；含 `narrative_warning`（仅当检测到已知旧 AMV 文本） |
| `GET /plan` | 原始 `battle_plan.json` |
| `GET /positions` | 原始 `real_positions.json` |
| `GET /alerts` | 原始 `alert_todo.json` |
| `GET /todos` | 仅 OPEN 待办 |

## 与旧 8800 的差异

- `battle_plan.json`、`alert_todo.json` 与旧 `/api/battle_plan.json`、`/api/alert_todo.json` **逐对象相等**。
- `real_positions.json` 原始 7 个顶层键与文件一致；旧 `/api/real_positions` 额外附加的 `positions_list` / `market_value_total` 依赖实时行情快照，v0.1 **未迁移**（避免在作战表插件里复制行情/估值逻辑）。后续如需要，由独立持仓估值插件提供，不在本插件重算。

## 卡片与签收交互（v0.3.0，2026-10-24 批2a）

卡片 `battle_plan.main`（slot=main / order=40）从"基础只读表"升级为对照旧 `renderBattlePlan` 的全功能对等卡。
**后端零改动**：插件仍只有 GET，签收走 core 代理。

| 项 | 约定 |
|---|---|
| 数据源 | `GET /summary`（汇总）+ `GET /plan`（原始 `battle_plan.json`）；两份各拉一次，单份失败只降级该块 |
| 段落顺序 | meta（签收进度/引擎状态/仓位口径/总仓上限）→ buy_plan（逐项签收）→ operations → watch_levels → risk_warnings → pending_todos → check_log → signal_watch（在场表 + 终态计数）→ module_switches（只读）→ 持仓与账户快照 |
| 逐项签收 | `POST /api/sign` body `{signer:'主人', plan_id:<plan_id>}`（与旧端 `signItem` 同形）；**由 `battle_plan_rw` 原生实现**（2026-10-24 起不转发 8800） |
| 一键全部签收 | `POST /api/sign` body `{signer:'主人', all:true}`；先 `confirm` 二次确认；`allSigned` 时按钮 disabled |
| 回执处理 | **以服务端回执为准**：成功后清防闪缓存并重拉 `/summary`+`/plan`，不做乐观更新 |
| 失败呈现 | 回执判定用 `res.ok === true`（写通道不可达时插件返 5xx；旧代理时代曾返 `{ok:false,error:'panel_down'}`），只看状态码会误判 |
| 重复提交 | 提交期间 `signing` 置位，按钮 disabled，不叠请求 |
| 模块开关 | 只读徽标展示；缺键视为启用（同旧版 `sw[k] !== false`）。编辑/批量提交不在 `battle_plan_rw` 域 |
| stale 提示 | `summary.narrative_warning`（旧 AMV 文字）保留在卡片顶部 |
| 刷新 | 不声明 `refreshEvery`（签收后手动刷新；卡头有「刷新」按钮） |

签收写入发生在 `battle_plan_rw`（`ctx.panel_write` → `data/panel/battle_plan.json`），只读插件 `battle_plan` 仍不写任何文件。

## 不覆盖

下单、改单/启停/取消、模块开关编辑、QMT 连接、持仓写入、告警确认/关闭、纸面账本。旧 8800 写接口全部保留；
本插件只接签收这一条**代理**写路径（`POST /api/sign` 由 core 挂载，不是插件路由）。

---

# battle_plan 写域契约（v0.1.0，原生写，2026-10-24 周末冲刺 N1）

**插件**：`app/plugins/battle_plan_rw/`（id=`battle_plan_rw`，v0.1.0，`backend.mount = "root"`）
**挂载**：`/api/<path>`（旧前端直调的根路径），与只读插件 `battle_plan`（`/api/p/battle_plan/*`）并存。
**写通道**：`ctx.panel_write("battle_plan")` + `ctx.state_write("engine_state" | "kill_switch")`；
插件不做任何文件 IO 写（`tools/cutover_gate.py` 静态扫描仍然有效）。
**读通道**：`ctx.panel_json("battle_plan" | "dashboard")` + `ctx.state_json("engine_state" | "kill_switch")`。
**语义唯一源**：旧仓 `engine/server.py` 的同名 handler（行号见下表，只读参考）。

## 端点（7 条路径 / 8 个 handler 语义）

全部「读整份 battle_plan → 改内存 → 整份写回」，未知字段（顶层/`meta`/条目）原样保留；
整个读改写在模块级 `threading.Lock` 内（旧端无锁，这里补）。

| 路径 | 旧 handler | 落盘 | 请求体 | 响应 |
|---|---|---|---|---|
| `POST /api/sign` | 7144 | battle_plan + engine_state | `{signer,note,plan_id,all}` | `{ok,sign,engine_state,signed_items,total_items}` |
| `POST /api/kill` | 7646 | battle_plan + engine_state + kill_switch | `{active,reason}` | `{ok,kill_switch,engine_state,module_switches_snapshot}` |
| `POST /api/toggle` | 7232 | battle_plan | `{item_type,plan_id,ts_code,enabled,signer}` | `{ok,item_type,item,enabled,status}` |
| `POST /api/cancel` | 7180 | battle_plan + engine_state | `{item_type,plan_id,ts_code,reason,signer}` | `{ok,item_type,item,status,engine_state}` |
| `POST /api/signal_watch/toggle` | 7541 | battle_plan（`enabled` 未传则不写） | `{watch_id,enabled}` | `{ok,watch_id,enabled,status}` |
| `POST /api/signal_watch/cancel` | 7569 | battle_plan | `{watch_id,reason}` | `{ok,watch_id,status}` |
| `POST /api/manual_position` | 7342 | battle_plan | `{pct}`（∈[0,100]，否则 422） | `{ok,manual_position}` |

## 必须照搬的幂等/事故细节

1. **空 `buy_plan` 点「全部签收」不降级顶层 sign**（`server.py` 265 行注释）：8/28、8/31 两次事故。
   空买侧 `sign` 一字不动、`meta.generated_at` 也不刷，只回报「现有 sign 是否已授权」。
2. **kill on→on 不覆盖快照**（7646）：`engine_state.module_switches_snapshot` 已是 dict 时沿用旧快照；
   on→off→on 才重新快照（off 已恢复 + 清快照）。
3. **kill 激活期间 `_apply_engine_state` 仍给 FROZEN**（298）：签收同步不得盖掉急停。
4. **`_apply_engine_state` 的 `signed_at` 用 `setdefault`**：键存在（含 null）不覆盖 —— 旧端行为，如实照搬。
5. **toggle 不动 sign/status**，只改 `enabled` + 追加 `status_history`；
   **择机单 toggle/cancel 不写 `status_history`**（S3 契约没这个字段，不破坏 schema）。
6. **重复签收已签条目**：`sign` 子对象不覆盖（保留首次 signed_at/signer/note），`status_history` 每次追加。
7. **cancel 的 operations 写两处**：`execution_status` + `status` 都置 `CANCELLED`。
8. **manual_position 的基准顺序**：`battle_plan.position_cap`（≥0）→ `dashboard.account.total_asset`（>0）→ 0.0；
   0 是合法值（AMV 极端档禁开新仓），不回退总资产。落盘同时把 `buy_plan[].kelly.size_amount`
   联协为 `amount × 50%`（`amount>0` 才联动）。

## 与旧端的差异（逐条登记，不编造）

| # | 差异 | 原因 |
|---|---|---|
| 1 | `/api/sign` 响应**缺 `batch_cap` 键** | `_batch_cap_check_and_alert` 要读批次/单笔上限配置并推 QQ 事件，属另一域，本批不搬 |
| 2 | 写后不重建 `dashboard.json` | 旧端 `_refresh_dashboard()` 走 `dashboard_builder`（IO+库）；v2 dashboard 有独立域，写域不越界 |
| 3 | 不写 `PROGRESS` 日志 | 旧端 `append_progress()` 写引擎进程日志；改记 `ctx.logger.info`（同内容，不新增写目标） |
| 4 | 作战表缺失 → 404 `battle_plan 无数据` | 旧端 `_battle_plan()` 会回退 schema 示例并**把示例写回生产文件**；v2 不拿示例冒充真数据 |
| 5 | kill 在作战表不可读时仍写两个状态文件 | 急停不能被读盘问题挡住；不拿空文档覆盖作战表，`history.snapshot` 如实写「作战表不可读：…」 |
| 6 | `POST /api/sign` 的 core 转发代理成为死代码 | 插件路由先注册先匹配；删代理属 core 改动，未在本批做 |

## 不覆盖（各有 owner）

`/api/module_switch`、`/api/module_switches_batch`（模块开关域）、`/api/ticket_policy*`（工单策略）、
`/api/trade_mode`（交易模式）、`/api/alert_plan/*`（长期预警）、`/api/ops/run`（运维触发）、
`/api/ui_notes/*`（UI 备注）。

## 回滚

删 `app/plugins/battle_plan_rw/` + 重启 8810。无数据迁移、无 schema 变更。

