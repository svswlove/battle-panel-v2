# 交接 · N1 battle_plan 原生写域（8 端点 / 7 条路径）

> 派单：01 → 本单 ｜ 2026-10-24 ｜ 并发组 N1
> 任务书：`docs/task_N1_bpwrite.md`
> **方位**：写操作原生插件化，不走转发；落盘即生效（旧执行器 5s 重读）。

---

## 1. 一句话状态

7 条根路径写端点已原生落地在**新插件 `app/plugins/battle_plan_rw/`（mount=root）**，
写通道只走 `ctx.panel_write("battle_plan")` 与 `ctx.state_write("engine_state"/"kill_switch")`；
`battle_plan` 原只读插件与 core 一行未改。插件测试 51 passed，全量 924 passed / 4 skipped。
门禁 1 项待裁决（见 §6）。

---

## 2. 方案裁决：任务书 §4 选了备选（方案 B）

| 方案 | 内容 | 结论 |
|---|---|---|
| A（任务书首选） | 改 `main.py` 挂载逻辑，新增 `backend.mount_root_extra` 让同一插件挂两前缀 | **否决**：要改 core 挂载区 + manifest 契约 + 冲突检测，收益只是省一个目录；且同插件挂两前缀会让「一个插件一个 owner、一个目录」的边界变模糊 |
| B（备选，**已执行**） | 新建 `battle_plan_rw` 兼容插件 `mount=root` 承载 `/api/sign` 等根路径，`battle_plan` 原插件不动 | **采纳**：零 core 改动，冲突检测（`main.py` 的 `claimed`）现成，`battle_plan` 卡片/只读端点零风险 |

代价与取舍：

- 多一个插件目录（14 → 15 个），但语义清晰：`battle_plan` = 读域（卡片/汇总），`battle_plan_rw` = 写域；
- `POST /api/sign` 在 core 里还留着一份转发 8800 的代理（批次 0）。**插件路由先注册 → 先匹配**，
  实测原生 handler 生效（`test_native_sign_shadows_core_proxy` 锁住：回执无 `panel_down`、无 `batch_cap`）。
  彻底删代理属 core 改动，不在本单白名单（见 §6 遗留 1）。

---

## 3. 端点清单（全部「读整份 → 改内存 → 整份写回」）

| 路径 | 旧 handler 行号 | 落盘 | 幂等/事故细节 |
|---|---|---|---|
| `POST /api/sign` | `sign_plan` 7144 | battle_plan + engine_state | 空 `buy_plan` 不降级顶层 sign；重复签收不覆盖 sign 子对象 |
| `POST /api/kill` | `kill_switch` 7646 | battle_plan + engine_state + kill_switch | on→on 不覆盖快照；on→off→on 重新快照 |
| `POST /api/toggle` | `toggle_item` 7232 | battle_plan | 不动 sign/status；只追加 status_history |
| `POST /api/cancel` | `cancel_item` 7180 | battle_plan + engine_state | operations 写 `execution_status`+`status` 两处 |
| `POST /api/signal_watch/toggle` | 7541 | battle_plan | 只改 enabled；**无 status_history**（S3 契约） |
| `POST /api/signal_watch/cancel` | 7569 | battle_plan | 只改 status；**无 status_history** |
| `POST /api/manual_position` | 7342 | battle_plan | amount = pct% × position_cap → 回退总资产 → 0；kelly 联动 |

任务书写「8 端点」，实际是 7 条路径（`signal_watch/toggle` + `/cancel` 算两条端点时共 8 个 handler 语义）；
本单按 7 条路由实现，一条不少。`/api/module_switch`、`/module_switches_batch`、`/ticket_policy*`、
`/trade_mode`、`/alert_plan/*` **不在本单**（各有 owner）。

### 照搬的三处「不得优化」

1. **`_sync_plan_sign` 空买侧不降级**（`server.py` 265 行注释）：8/28、8/31 两次事故 —— 空 buy_plan 点
   「全部签收」把已授权 sign 降成 UNSIGNED，引擎全天只读。空买侧现在：sign 一字不动、`meta.generated_at`
   也不刷，只回报「现有 sign 是否已授权」给 `_apply_engine_state`。
2. **kill on→on 不覆盖快照**（7646）：`module_switches_snapshot` 已是 dict 时沿用旧快照。否则急停第二次
   按下就把「全 false」快照进去，解除后全模块永久停机。
3. **`_apply_engine_state` 的 FROZEN 优先**（298）：kill 激活期间签收同步不得把 FROZEN 盖成 ARMED。

---

## 4. 数据与文件

| 项 | 值 |
|---|---|
| 写 battle_plan | `ctx.panel_write("battle_plan", doc)` → `data/panel/battle_plan.json`（白名单已登记，git 9ba3032 前一批） |
| 写 engine_state / kill_switch | `ctx.state_write(...)` → `engine/state/*.json` |
| 读 battle_plan / dashboard | `ctx.panel_json`（`dashboard` 只用于 manual_position 的总资产兜底） |
| 读 engine_state / kill_switch | `ctx.state_json`（N2 并发单已落地，见 `app/core/contract.py:675`）；老 core 无该方法时退 `paths.WRITABLE_STATE` 同一白名单只读兜底 |
| 备份/审计 | 全在 core 的 `panel_write`/`state_write`：`data/panel_v2/backups/<name>/<时间戳>.json` + `audit.log` |
| 锁 | 模块级 `threading.Lock`，整个「读-改-写」在锁内（旧端无锁，这里补） |

生产文件**零变更**：本轮全部测试走 tmp；唯一一次误触见 §7。

---

## 5. 测试

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/battle_plan_rw/tests -q    # 51 passed
python -m pytest -q                                      # 924 passed, 4 skipped
python tools\cutover_gate.py                             # 7/8 PASS，1 项待裁决（§6）
```

隔离方式（两层的必要性）：`PANEL_V2_WS` / `PANEL_V2_DATA_DIR` / `PANEL_V2_PAPER_DIR` 指 tmp，
**同时** monkeypatch `paths.WRITABLE_PANEL` / `READABLE_PANEL` / `WRITABLE_STATE` / `OUT_DIR` ——
`paths` 的这些字典在 import 期固化，只设环境变量对已 import 的模块不生效。

覆盖点（51 项，节选）：

- 空 `buy_plan` 全签不降级（sign 与 `meta.generated_at` 都不动，engine_state 仍 ARMED）；
- 重复签收：sign 子对象不覆盖、status_history 追加；
- kill 三态：on 快照 → on 再按沿用 → off 逐键恢复且含「原本就关着的模块」；
- kill 在作战表不可读时仍写两个状态文件、不造空作战表；
- kill 激活期间签收同步仍返回 FROZEN；
- toggle/cancel 的 422/404 一律不写盘（文本比对 + 无备份两条证据）；
- 择机单 toggle 未传 `enabled` 不写盘且回 `enabled:true`；
- manual_position 三种基准（cap / 总资产 / 0）与 0 是合法值、越界 422；
- 未知字段四级保留（顶层 / meta / 条目 / signal_watch/global_rules）；
- 静态禁令（无 DB 直连、无行情源、无 HTTP/子进程、无自写文件）。

---

## 6. 遗留（按处理人分）

| # | 项 | 归属 | 说明 |
|---|---|---|---|
| 1 | `tools/cutover_gate.py::_check_no_write` 探针与方案 B 不匹配 | 01 | 探针是 `POST /api/p/battle_plan/plan`，断言 ≠405。方案 B 下写域在**根路径**，`/api/p/battle_plan/*` 仍只有 GET → 该探针永远 FAIL。建议改为 `POST /api/sign` 带不存在的 plan_id 期望 404，或断言 `/_plugins` 含 `battle_plan_rw` 且其 `mount=root`。`tools/` 不在本单白名单，未改。 |
| 2 | `tools/cutover_gate.py::_check_pytest` 的目录列表不含 `battle_plan_rw/tests` | 01 | 门禁 pytest 只跑 416 项（不含本插件 51 项）；全量 924 项已绿。建议列表补 `app/plugins/battle_plan_rw/tests`。 |
| 3 | core `POST /api/sign` 转发代理成为死代码 | 01 | 插件路由先注册先匹配，代理不再被命中。可删（core 改动，本单不做），删时同步 `app/core/tests/test_shell_endpoints.py` 的路由断言。 |
| 4 | `batch_cap` 报告未搬 | 01 | 旧端签收时校验批次是否超单笔上限并推 QQ 告警（`_batch_cap_check_and_alert`），依赖批次上限配置与 push 通道，属另一域。回执如实在契约登记缺该键。 |
| 5 | 不重建 `dashboard.json`、不写 PROGRESS 日志 | 01 | 旧端写后 `_refresh_dashboard()` + `append_progress()`；v2 dashboard 有独立域，写域不越界；操作痕迹改记进程日志。 |
| 6 | 8810 未重启 | 01 | 新插件需重启 8810 才装载（`/api/health` 的 plugins 列表暂无 `battle_plan_rw`）。本单按要求不重启。 |
| 7 | 旧前端 `/api/module_switches_batch` 仍会 404 | 01 | 旧前端「待确认批量提交」调这个（含 manual_position.pct）。本单只做了独立 `/api/manual_position`；批量端点属模块开关域（另一 owner）。 |

---

## 7. 事故与复原（如实记录）

本轮验证时我用**默认 `create_app()`**（未指 tmp 插件/数据目录）跑了一次路由探针，
其中 `POST /api/sign {}` 命中了真实生产数据目录，产生两次写：

- `data/panel/battle_plan.json`（13:22:02）：内容与写前备份**逐字节相同**（空 buy_plan → `_sync_plan_sign`
  不动 sign、不改 generated_at），仅 mtime 变化；
- `engine/state/engine_state.json`（13:22:02）：`updated_at` 被刷新。已用写前备份
  `backups/engine_state/20261024_132202_093057.json` 原样复原，复原后哈希与备份一致
  （`3D74C35B…ADD`）。

`kill_switch.json`、`dashboard.json` 未被触及。此事与代码无关，是验证纪律问题：
**已把「任何 create_app() 探针必须显式指 tmp」写进 §5 的隔离说明；后续同类验证一律走 tmp**。
备份与审计记录保留在 `data/panel_v2/backups/`（gitignore 内）作为痕迹。

---

## 8. 回滚

删 `app/plugins/battle_plan_rw/` 整个目录 + 重启 8810 即回到改动前（根路径写端点回落 core 的 8800 代理，
`/api/sign` 仍有代理可用，其余 6 条端点回到 404/无接口）。无数据迁移、无 schema 变更。
