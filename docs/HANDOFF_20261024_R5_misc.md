# HANDOFF · R5 遗留小项清尾（trade_mode 修复 + xdxr 指数口径 + STATUS 收口）

> 2026-10-24 夜 ｜ 01 ｜ 任务书 `docs/task_R5_misc.md`
> 结论：三项全部完成。未 commit、未重启 8810（遵任务书）。
> 全量 `python -m pytest -q` → **1152 passed / 4 skipped，exit 0**；`python tools\cutover_gate.py` → **PASS 8/8，exit 0**。

---

## 1. 结论先行

| 项 | 结论 | 关键证据 |
|---|---|---|
| A `/api/status` 的 `trade_mode` | 已修。`compat` 0.2.0 → **0.3.0**，从 `engine_state.json` 读出 `paper` | 临时实例 8812 vs 8800 **8 字段全等**（`updated_at` 秒级相同） |
| B `xdxr` 指数缺陷 | 已修（**新版修正旧端缺陷**）。`000001.SH` 返回 `[]` | 8812 `events=[]`；8800 仍 81 条（故意不一致，见 §4） |
| C `docs/STATUS.md` | 已更新为冲刺后状态（R1~R5 + 待验收 + 周一清单） | 文件已重写，含「R3 review 卡未挂」等 5 条待验收 |

**A 的一处偏离任务书**：任务书要求「若 `ctx.state_json` 没有就在 `paths.py` 加 `READABLE_STATE` + `contract.py` 加 `state_json`」。
实际 N2 已落地 `ctx.state_json(name)`（`app/core/contract.py:742`，读 `paths.WRITABLE_STATE` 同一白名单），
故 **`paths.py` / `contract.py` 零改动**，只接线。少动两个并发热点文件，直接消除与并发单的冲突面。

---

## 2. A：compat 0.3.0

### 2.1 改了什么

| 文件 | 变更 |
|---|---|
| `app/plugins/compat/plugin.json` | version `0.2.0`→`0.3.0`；`data.state: ["engine_state", "kill_switch"]`（新增） |
| `app/plugins/compat/backend.py` | 新增常量 `STATE_ENGINE` / `STATE_KILL` + `_state(ctx, name)` 读通道；`build_status` 补读 `trade_mode` / `kill_switch_active` / `kill_reason` |
| `app/plugins/compat/tests/test_compat_contract.py` | FakeCtx 加 `state_json`；新增 `ENGINE_STATE`/`KILL_SWITCH` 夹具；`/status` 用例从 3 条扩到 7 条 |

### 2.2 逐字段口径（对齐 8800 `api_status`，`engine/server.py:3199`）

| 字段 | 旧端来源 | v2 来源 |
|---|---|---|
| `code`/`label` | kill 激活→FROZEN / 已签收→RUNNING / 否则 READ_ONLY | 同 |
| `state`/`mode` | `ks.active` ? FROZEN : signed ? ARMED : READY | 同 |
| `battle_plan_signed` | `battle_plan.sign.status == "SIGNED"` | 同（唯一源不变） |
| `kill_switch_active`/`kill_reason` | `_load_kill_switch()` → `engine/state/kill_switch.json` | `ctx.state_json("kill_switch")`；读不到 → 退 `dashboard.engine.kill_switch` 快照 |
| `trade_mode` | `_load_engine_state().get("trade_mode", "live")` | `ctx.state_json("engine_state").get("trade_mode", "live")` |
| `updated_at` | `now_str()` | `_now()`（同格式 `%Y-%m-%d %H:%M:%S`） |

**`trade_mode` 不做合法性校验**：8800 就是 `st.get("trade_mode", "live")`，脏值原样带出。
若按 `executor.auto_buy.load_trade_mode()` 的「非法值→live」收敛，脏配置时两边会分叉——
本端点目标是逐字段一致，不是修脏数据。已写测试 `test_status_trade_mode_live_and_dirty_value_passthrough` 锁住。

### 2.3 一处与旧端的退化差异（如实登记）

`kill_switch.json` 读不到时：
- 旧端 `_load_kill_switch()` 返回 `{active: false}` → 报「正常」；
- v2 退 `dashboard.engine.kill_switch`（同源快照，`_refresh_dashboard` 急停后即时重建）→ 报真实状态。

信息更多、不会把已急停误报成正常。**正常档两边完全一致**，只在文件缺失这个异常档不同。
测试 `test_status_kill_falls_back_to_dashboard_snapshot` + `test_status_kill_switch_file_wins_over_dashboard_snapshot` 锁住优先级。

### 2.4 未改的相邻项（不在白名单，仅上报）

`_resolve_holdings()`（`compat/backend.py:319`）的 `trade_mode` 仍写死 `"live"`，docstring 里还写着
「engine_state.json 不在任何只读白名单」——现在这句话已过期，且 `/api/gates` 的持仓门在
`trade_mode=paper` 时仍按 `real_positions.json` 取票（旧端会改读 `paper_account.json`）。
**这是真实口径差异，但不是本单验收项**（本单验收只看 `/api/status`），未动。
若要修，`_resolve_holdings` 还需 `paper_account` 读通道（当前 `paths.py` 未登记该文件），属另一单。

---

## 3. B：xdxr 指数判断

### 3.1 改了什么

| 文件 | 变更 |
|---|---|
| `app/core/quotes.py` | 新增 `_INDEX_RULES` / `_INDEX_BARE_PREFIX` / `_is_index()`；`xdxr()` 入口先拦指数返回 `[]`；`_tdx_market` 与模块头注释同步改说明（**函数体逻辑一字未动**） |
| `app/core/tests/test_xdxr.py` | 删掉「`000001.SH` → `sz000001`」这条锁旧行为的参数；新增 3 条：指数空表且不打行情源、判据必须带后缀、无后缀 880/881 空表 |

### 3.2 判据（为什么不是只加个前缀）

```
000xxx.SH  → 指数（000001 上证 / 000300 沪深300 / 000905 中证500 …）
399xxx.SZ  → 指数（399001 深成 / 399006 创业板指 …）
880xxx / 881xxx（无后缀）→ 通达信板块指数
```

**必须带后缀判断**：`000001.SZ` 是平安银行（个股），`000001.SH` 才是指数。
只看 6 位数字前缀会把平安银行一起打成空表 —— 那是把「修一个缺陷」换成「造一个新缺陷」。
测试 `test_index_detection_requires_suffix` 专门锁这一条（`000001.SZ` 仍取 `sz000001` 且非空）。

`_tdx_market()` 本身**不改**：个股/基金/可转债的市场判定必须与旧端一致（`510300.SH`→sh、`688981.SH`→sh、
`300750.SZ`→sz 等 6 组仍逐字对齐）。指数在 `xdxr()` 入口被提前拦掉，走不到 `_tdx_market`。

### 3.3 活体验证（临时实例 8812）

| 代码 | 8812 events | 8800 events | 说明 |
|---|---|---|---|
| `000001.SH` | **0** | 81 | 修复目标：旧端串到平安银行 |
| `399001.SZ` | **0** | — | 深成指 |
| `000300.SH` | **0** | — | 沪深300 |
| `399006.SZ` | **0** | — | 创业板指 |
| `000905.SH` | **0** | — | 中证500 |
| `000001.SZ` | 81 | — | 平安银行，**未受影响** |
| `600519.SH` | 45 | — | 沪市个股 |
| `510300.SH` | 15 | — | 沪市 ETF |
| `688981.SH` | 173 | — | 科创板 |
| `300750.SZ` | 80 | — | 创业板个股 |
| `920001.BJ` | 0 | — | 北交所（本就空） |

### 3.4 两边一致性说明（**故意不一致**）

旧端同样有此缺陷，修了新版更好 → 归入 HANDOFF 的「**新版修正旧端缺陷**」类差异。
与 M1 的「同参对齐」原则冲突时，本单取「修正」而非「对齐」，理由：指数没有除权除息，
返回别人的 81 条记录是**错误数据**，不是口径选择；对齐错误数据会把缺陷永久固化。
旧端如需对齐，要单独改 `engine/server.py::_tdx_market`（不在本单白名单）。

前端影响：`kline_lc.js` 对空 events 只画空表，无报错路径。

---

## 4. C：STATUS.md

重写为冲刺后状态，新增/更新：

- 基线数字：1152 passed / 4 skipped、gate 8/8、18 插件；
- 第一波（已提交）+ 第二波 R1~R5（未提交）两张表，逐单标「完成 / 部分」+ 证据；
- 已知边界 9 条，其中 **5 条是本单现场新发现的待验收项**（见 §5）；
- 周一盘前清单 6 条，第 2 条明确写「8810 仍是旧代码，`trade_mode` 仍为 null，需重启」。

---

## 5. 本单现场发现、未处置（不在白名单）

1. **R3 review 卡未挂**：`app/plugins/review/frontend.js`（93KB）已写，但 `review/plugin.json` 没有
   `frontend.cards`，`/_plugins` 清单里 review 无卡 → 旧首页切 `review` 到 v2 会回退。
   `app/plugins/review/plugin.json` 不在 R5 白名单，未动。
2. **R2/R3 HANDOFF 缺失**：`docs/HANDOFF_20261024_R2_selection.md` / `..._R3_concept_review.md` 不存在
   （R1/R4 已有）。R2 的 `docs/selection_contract.md` 有。
3. **gate pytest 目录白名单未含新插件**：`tools/cutover_gate.py::_check_pytest` 固定列表里没有
   `app/plugins/intraday/tests`、`app/plugins/selection/tests`。gate 仍 PASS，但这两处只由全量 pytest 覆盖。
   `tools/cutover_gate.py` 不在 R5 白名单，未动。
4. **全量偶发**：本单三次全量里，第一次 1096 通过；后续一轮出现
   `selection/tests/test_selection_frontend.py::test_pool_write_uses_server_receipt_not_optimistic_update`
   失败，单跑与复跑均通过（43 passed）。并发单正在写 `selection/` 目录，属改动窗口内竞态，未定位。
5. **8810 常驻进程仍是 R5 之前的代码**：`/api/status` 现在仍返回 `trade_mode: null`。
   本单活体验收在临时实例 **8812**（`PANEL_V2_FEED=0`，仅本单用）上完成，验收后已停。

---

## 6. 验证半径说明

| 改动 | 半径 | 跑了什么 |
|---|---|---|
| `compat/backend.py` + `plugin.json` | 该插件 `/api/status` 一个端点 + manifest 校验 | `pytest app/plugins/compat/tests`（97 通过）；8812 vs 8800 逐字段 |
| `core/quotes.py::xdxr` | `xdxr` 的调用方：`ops` 插件 `/api/kline/{code}/xdxr` 一个路由 | `pytest app/core/tests/test_xdxr.py`（15 通过）；8812 上 11 只标的活体 |
| 全量 | 改动会进 `_plugins` 清单与 gate 静态扫描范围 | `pytest -q` 1152；`cutover_gate.py` 8/8 |

`paths.py` / `contract.py` **零改动**（见 §1），所以未针对状态白名单另跑契约测试；
`ctx.state_json` 的既有覆盖在 `app/plugins/ops/tests/test_ops_plugin.py` 与
`battle_plan_rw/tests/` 里，随全量一起通过。

---

## 7. 复现命令

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q                                   # 1152 passed, 4 skipped, exit 0
python tools\cutover_gate.py                          # PASS 8/8, exit 0

# A 活体（临时实例，不碰 8810）
$env:PANEL_V2_FEED='0'
python -m uvicorn app.core.main:app --host 127.0.0.1 --port 8812
# 另开：/api/status 与 127.0.0.1:8800/api/status 逐字段比
# B 活体：/api/kline/000001.SH/xdxr → {"events":[]}；/api/kline/000001.SZ/xdxr → 81 条
```

---

## 8. 遗留 / 下一步

1. **重启 8810** 后重跑 A 活体，确认生产端口 `trade_mode=paper`（本单只验到 8812）。
2. 补 R2/R3 两份 HANDOFF、给 `review/plugin.json` 加卡（§5 第 1、2 条）。
3. gate 的 pytest 目录补 `intraday` / `selection`（§5 第 3 条）。
4. `_resolve_holdings` 的 `trade_mode` 与 `paper_account` 读通道（§2.4），单开一单。
5. 旧端 `engine/server.py::_tdx_market` 是否同步修指数判断——需主人裁定（改旧端不在任何单的白名单）。
