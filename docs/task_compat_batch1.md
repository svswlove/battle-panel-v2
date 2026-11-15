# 任务书：panel-v2 旧端点兼容层（第 1 批：只读 JSON 与基础只读）

- 派单：01 → DSHana
- 日期：2026-10-23
- 工期：一个会话内完成；到点未完先交付已完成部分 + 回执说明

## 1. 背景（读懂再动手）

旧面板前端（`engine/web/index.html` 等，750KB，10 个 Tab）调用 **85 个 `/api/...` 路径**。
现在要做的是「**复用旧前端，把数据源换成新后端 8810**」：旧前端只改一个 `ENGINE` 常量，
其余一行不动。为此新后端要提供一批**与旧端同名、同响应格式**的端点。

**已确定的架构**：新建一个 `compat` 插件，专门承载这些旧格式端点。它不 import 其他插件，
直接读同一份数据源（`data/panel/*.json`、`data/*.db`、eltdx 行情门面 `ctx.quotes`）。
过渡期允许与现有插件功能重叠；最终形态另议，本批不做取舍。

## 2. 目标（本批只做这些）

在 `OH-WorkSpace\projects\panel-v2\app\plugins\compat\` 下新建插件，
实现 **15 个只读端点**，响应格式**必须与旧 8800 逐字段一致**（内容可因数据更新而不同）。

### 2.1 静态 JSON 转发（9 个）

路径 → 数据源（全部是现成生产文件，**只读，不新造产物**）：

| 端点 | 数据源 |
|---|---|
| `GET /api/dashboard.json` | 见 §2.3（不是单文件，要组装） |
| `GET /api/battle_plan.json` | `data/panel/battle_plan.json` |
| `GET /api/amv_live.json` | `data/panel/amv_live.json` |
| `GET /api/amv_regime_latest.json` | `data/panel/amv_regime_latest.json` |
| `GET /api/position_cap_latest.json` | `data/panel/position_cap_latest.json` |
| `GET /api/alert_todo.json` | `data/panel/alert_todo.json` |
| `GET /api/alert_plan.json` | `data/panel/alert_plan.json` |
| `GET /api/push_types.json` | `data/panel/push_types.json` |
| `GET /api/concept.json` | `data/panel/concept.json` |

这 8 个**直接透传文件内容**（不要重排字段、不要补字段），文件缺失返回 404 + `{"detail": "..."}`。

### 2.2 需要组装的只读端点（6 个）

| 端点 | 来源 | 要求 |
|---|---|---|
| `GET /api/positions.json` | 旧格式 `{meta,date,updated_at,holdings,selection,track_signals,source_audit}` | 先探旧端实际内容再决定怎么组装；`holdings` 至少有 |
| `GET /api/gates` | 旧端 `{version,updated_at,gate1,gate2,gate3}` | 读 `data/panel/gates.json`（如存在）或按旧端逻辑算 |
| `GET /api/status` | 旧端 `{code,label,state,mode,battle_plan_signed,kill_switch_active,kill_reason,trade_mode}` | 从上表几个 panel JSON 组装 |
| `GET /api/real_positions` | `data/panel/real_positions.json` + 行情补全 | 旧端带 `positions_list`（现价/PnL/权重），见 §4 |
| `GET /api/alerts` | `data/panel/alert_todo.json` 的 alerts 段 | 探旧端确认 |
| `GET /api/search/all?q=` | 全市场代码/名称/拼音搜索 | 探旧端确认返回结构；数据源用 `stock_names` JSON（`ctx.json_read("stock_names")`） |

### 2.3 `/api/dashboard.json`（首屏最关键）

旧端返回 `{meta, engine, account, positions, orders_today, environment, alerts, risk_locks}`。
实测 5.1KB。逐段对：`engine`（引擎状态/心跳）、`account`（总资产/可用/市值/当日盈亏）、
`positions`（持仓）、`orders_today`、`alerts`、`risk_locks`。
**先 `GET http://127.0.0.1:8800/api/dashboard.json` 把真实响应存下来当契约**，
再按同样结构组装（数据源优先用 `data/panel/*.json` 与 `track_pool.db` 的 account/holdings 表）。

## 3. 边界（硬约束）

**允许**
- 只在 `projects/panel-v2/app/plugins/compat/` 下新建文件
- 只读：`ctx.panel_json()` / `ctx.json_read()` / `ctx.db_read()` / `ctx.quotes`
- 可以新增 `app/core/paths.py` 的 `READABLE_PANEL` 白名单条目（**只加已有生产文件**，
  加之前先 `ls` 确认文件存在；改这个文件要在回执里单独列出）

**禁止**
- 改 `engine/`（旧仓）任何文件
- 写 `data/panel/` 或任何生产文件
- 改现有 5 个插件（sector/kline/amv/battle_plan/watchlist）
- 改 `app/core/contract.py`、`app/core/main.py`
- 新造数据产物、编造字段（缺就 `null`，不要填 0 或猜）
- 引入新依赖

## 4. 已验证的环境事实（直接用，不要重推）

- 新后端 8810 在跑：`http://127.0.0.1:8810`，健康检查 `GET /_plugins/health`
- 旧后端 8800 在跑：`http://127.0.0.1:8800`（旧端点全在这里）
- **重启 8810 的方法**（改完代码必须重启才生效）：
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*port 8810*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  schtasks /Run /TN "PanelV2_KeepAlive"
  # 等 10 秒
  ```
- 插件 manifest 必填字段参考 `app/plugins/watchlist/plugin.json`
- 插件写法参考 `app/plugins/battle_plan/backend.py`（最短，只读 JSON）
- `ctx.panel_json("名")` 需要名字同时在 `manifest.data.panel` 和 `paths.READABLE_PANEL`
- `ctx.json_read("stock_names")` 已在白名单，返回 `{"names": {代码: 名称}}`
- `ctx.db_read("track_pool.db")` 已在白名单
- **注意 8810 插件路径前缀**：插件端点在 `/api/p/<插件id>/...`，不是 `/api/...`

## 5. 自测要求（必须逐条做，结果写进回执）

1. `python -m pytest -q` → 全绿（当前基线 **196 passed**，只许多不许少）
2. 每个新端点在 8810 上 `curl` 一次，`HTTP 200`
3. **逐字段比对**：对每个端点，把 8810 与 8800 的响应 JSON 递归比对，
   允许数值差异（数据更新），**不允许键名/结构差异**。
   写个临时脚本跑，把结果贴进回执（哪些端点全等、哪些字段有差异+原因）。
4. 契约扫描：`python tools/cutover_gate.py` → 8 项 PASS（它会跑「插件无写代码」静态扫描，
   所以 backend.py 里不能出现 `open(...,'w')`/`requests.post`/`subprocess` 等）

## 6. 交付

- 代码：`app/plugins/compat/{plugin.json, backend.py, frontend.js?}`（本批无前端，frontend 可省）
- 测试：`app/plugins/compat/tests/test_compat_contract.py`
- 报告：`projects/panel-v2/docs/compat_batch1.md`（逐个端点：旧格式样本、实现方式、比对结果、未做的原因）
- 回执：**≤300 字 + 文件路径**，写清：做了几个端点、pytest 数字、比对结论、卡在哪

## 7. 说明

- 这批只做**只读**。写操作（下单/撤单/签收/急停/条件单）第 2 批再做，不要碰。
- 拿不准旧端格式时，**先 curl 旧端存样本**，不要猜。
- 端点太多做不完时：按 §2.1（静态转发，最快）→ §2.3（dashboard）→ §2.2 的顺序交付。
