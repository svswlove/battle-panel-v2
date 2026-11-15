# 前端批次 1b 交接：新建 dashboard 插件（账本版布局仪表盘卡，2026-10-24）

> 任务书：`docs/task_fe_batch1b.md`｜路线图：`docs/FRONTEND_ROADMAP.md` 批次 1.2
> 行为基准：`app/web/legacy/index.html` 账本版仪表盘（`dashShellHtml` 3026-3045、`ledgerKpisHtml` 3063-3088、
> `ledgerPosHtml` 3090-3113、`ledgerTradesHtml` 3115-3125、`_bookRow` 3127-3145、
> `bookActiveHtml`/`bookHistHtml` 3147-3158、`_setHtml` 3047-3053、`renderDashboardLedger` 3203-3243）
> 状态：**代码与静态检查已落盘；pytest / 8810 重启 / CDP 截图未跑**（本执行手沙箱禁止创建任何子进程，实测 EPERM，见 §5）

---

## 1. 文件清单

| 文件 | 动作 | 行数 |
|---|---|---|
| `app/plugins/dashboard/plugin.json` | 新 | 35 |
| `app/plugins/dashboard/backend.py` | 新（薄壳：只 GET /health） | 59 |
| `app/plugins/dashboard/frontend.js` | 新 | 686 |
| `app/plugins/dashboard/tests/test_dashboard_contract.py` | 新（9 条） | 123 |
| `app/plugins/dashboard/tests/test_dashboard_frontend.py` | 新（20 条静态检查） | 220 |
| `docs/HANDOFF_20261024_fe_batch1b.md` | 新（本篇） | — |

未改：`app/core/main.py`（插件扫描 `app/plugins/*/plugin.json` 自动发现，确认无需改）、`app/web/shell.js|shell.css|shell.html`、
`app/web/legacy/`、`engine/`、其他插件、任何依赖、`plugin.json` 之外的核心契约。

## 2. 布局与功能落点（逐条对任务书 §功能清单）

| # | 功能 | 落点 | 说明 |
|---|---|---|---|
| 1 | 引擎徽章 | `engineHtml()` | state/配色（FROZEN→danger、ARMED→up、其余 warn）、mode、纸面·实盘徽章、执行器（RUNNING/已停止/心跳超时 >90s）、看门狗（正常/告警/异常/状态文件缺失）、急停、签收、心跳。全部 token 配色 |
| 2 | 实盘/纸面切换 | `switchMode()` + `renderMode()` | localStorage 键**沿用旧版** `panel_ledger_mode`；切换清缓存后重拉 `/api/ledger?mode=` |
| 3 | KPI 六格 | `kpisHtml()` | 总资产/可用(带冻结)/市值/当日盈亏(额+%)/持仓盈亏(额+%)/现金占比；金额与百分比分行（旧版 2026-10-21 修）；千分位 2 位；红涨绿跌 |
| 4 | 持仓表 14 列 | `POS_HEADS` + `posHtml()` | 代码·名称/余额/可用/冻结/成本价/现价/参考盈亏/盈亏%/当日盈亏/当日%/市值/仓位/今买/今卖；数字列右对齐；`price_source=cost_fallback` → `.muted` + 「成本」`src-tag` |
| 5 | 今日成交 | `tradesHtml()` | 时间/名称/买(up)·卖(down)/价格×数量/金额·费用 |
| 6 | 活跃单 + 流水 | `bookRow()`/`bookActiveHtml()`/`bookHistHtml()` | 时间/委·条/名称/方向(条件单标签)/价格×数量/状态/剩余天数；**撤单按钮照旧渲染，点击弹「撤单属批次 2 接」卡内提示，不发任何写请求** |
| 7 | 三道门灯条 | `gatesHtml()` | gate1/2/3，GREEN/YELLOW/RED/UNKNOWN 四态左描边 + 状态字色；判定全在服务端，前端纯展示 |
| 8 | 刷新 / 防闪 | `setHtml()`/`setText()` + `refresh()` | `refreshEvery: 3000`；**照搬旧版 `_setHtml` 字符串比对**：内容不变不碰 DOM；骨架只在 `mount` 建一次；上一轮未回不叠请求 |
| 9 | 空态 | 各渲染函数 | 「无持仓」「今日无成交」「无活跃单（挂单 + 监控中条件单）」「今日无流水」；paper 空账本实测正常渲染 |

## 3. 数据源与后端边界

| 用途 | 端点 | 提供方 |
|---|---|---|
| 账本（主数据） | `/api/ledger?mode=paper|live` | `paper` 插件（root） |
| 引擎/执行器/看门狗 | `/api/dashboard.json` | `compat` 插件（root） |
| 三道门 | `/api/gates` | `compat` 插件（root） |
| trade_mode / 急停实时 | `/api/status` | `compat` 插件（root） |

`backend.py` 是**纯薄壳**：只注册 `GET /health`（自省：卡 id / 只读 / 数据源清单 / 刷新周期）。
`data.read|panel|json|lib|write` 全空 —— 账本口径唯一源是 `app/core/paper/` + `app/plugins/paper/views.py`，
本插件**不建第二份口径**，一行 IO 都没有。

## 4. 与旧版的行为差异（如实登记）

1. **`/api/ledger?mode=live` 在新栈不可用**：`paper` 插件对 live 明确返回 `{ok:false, mode:'live', error:'paper-only: 实盘账本请在旧 8800 查看'}`（HTTP 200）。
   → 切到「实盘」时本卡显示「账本不可用（mode=live）：paper-only…」，KPI 走同一提示，持仓/成交显示空态，**不报错、不伪造数据**。
   这与旧版切实盘能看到实盘账本不同，属数据面缺口，非卡缺陷；旧 8800 仍是实盘账本入口。
2. **`/api/status.trade_mode` 实测恒为 `null`**（`compat.build_status` 注释：`engine_state.json` 不在只读白名单，不猜）。
   → 模式徽章在 trade_mode 为 null 时按**当前账本视图**显示并加「·视图」后缀，title 写明「trade_mode 未上报」。
   首帧用户没存过 `panel_ledger_mode` 时跟随 `/api/status.trade_mode`（拿到才跟，拿不到用 paper，与旧版兜底一致）。
3. **撤单写操作本批不做**（任务书 §不做）：按钮渲染 + 点击提示，`api.post` 在 frontend.js 中**零出现**（静态测试锁定）。
4. **不做 `panel-pnl` 盈亏止损条**（旧 3313 的 `_pnlHtmlCache` 块）：任务书 §布局结构只列「三道门灯条」，
   照任务书移除，非遗漏；如需补回属批次 2。
5. **执行器/看门狗徽章不可点击跳卡**（旧版 `onclick="activate('battle_plan')"`）：跳卡要 shell 加`panel:open-card` 监听，跨批不做。
   点击时派发 `CustomEvent('panel:open-card', {pluginId})`，无监听方时 `console.debug`。
6. **个股点击只留 `data-open-kline`/`data-open-name` 属性**（任务书 §不做，批次 1c 统一接），本卡不绑点击。
7. 旧版 `metaHtml` 的「生产者/来源」行保留为 `dash-meta`（内容与旧版同一份 `dashboard.json.meta`）。

## 5. 自测状态

### 已跑（本执行手）

| 项 | 结果 |
|---|---|
| `frontend.js` 语法 + 导出形状 | **真实 `import()` 通过**（`dashboard.main` / `order=5` / `refreshEvery=3000` / mount·unmount·refresh 均为 function）。沙箱禁子进程（`node --input-type=module --check` 与 python 均 EPERM），用等价且更强的动态 import 代替 |
| 裸 hex 扫描 | **0 处** |
| 门禁禁用写法（`.write_text`/`open(`/`fetch(`/裸 import/全局赋值/api.post） | **0 命中** |
| 静态测试逐条预演（26 条断言口径） | 全部 PASS（正则、token、14 列表头逐字、防闪 setHtml 体、端点绝对路径等） |
| **真实数据行为验证**（DOM shim + 真实 8810 HTTP） | paper 空账本：KPI 六格有值（总资产 473,596.70 / 现金占比 100.00%）、持仓「无持仓」、成交「今日无成交」、活跃单/流水空态、三道门 3 盏（情绪门·持仓门·选股门）、引擎徽章（已签收可下单/已签收/纸面 [PAPER]·视图/执行器 已退出/看门狗 告警/签收✔·心跳）全部渲染 |
| 防闪实测 | 数据不变时连续 `refresh()` → **innerHTML 写入 0 次、textContent 写入 0 次** |
| 切换实测 | 切实盘 → 请求 `/api/ledger?mode=live` + localStorage 写 `panel_ledger_mode=live` → 显示 paper-only 提示；切回纸面 → 恢复 473,596.70；稳定后连续刷新仍 0 写入 |
| 只读实测 | 点击撤单按钮 → 卡内提示「撤单属批次 2 接：委托 P-123（本卡只读）」，**写请求 0 条** |
| 卸载实测 | `unmount()` 后 `refresh()` 零请求，`_db=null` |

### ⚠ 未跑（沙箱禁子进程，请派活方执行）

```powershell
cd OH-WorkSpace\projects\panel-v2
$py = OH-WorkSpace\envs\qmt312\Scripts\python.exe

# 1) 本插件测试
& $py -m pytest app/plugins/dashboard/tests -q
# 2) 全套（基线 721 passed，新增约 +29）
& $py -m pytest -q
# 3) 语法
node --input-type=module --check app/plugins/dashboard/frontend.js
# 4) 门禁（含「插件无写代码」静态扫描，会扫到新插件）
& $py tools/cutover_gate.py
```

**必须重启 8810**：本批新增 `plugin.json` + `backend.py`，插件扫描只在进程启动时跑一次（与批次 1a 只改 .js 不同）。
重启后确认：`GET /_plugins/health` 出现 `{"id":"dashboard","ok":true}`，`GET /api/p/dashboard/health` 返回 `ok:true`。

## 6. CDP 断言清单（给验收人，对应任务书 §自测 4）

打开 `http://127.0.0.1:8810/` → 点 rail「仪表盘」（order=5，排在 sector/kline 之后）后断言：

| 断言 | 期望 | 选择器 |
|---|---|---|
| KPI 六格有值 | 6 个 `.kpi`，总资产/现金占比非「—」 | `.lg-sum .kpi` |
| 空账本持仓 | 文案「无持仓」 | `.dash-main` 第 2 个 `.dash-card` |
| 空账本成交 | 文案「今日无成交」 | 第 3 个 `.dash-card` |
| 活跃单/流水空态 | 「无活跃单…」「今日无流水」 | `.dash-side .dash-card` |
| 实盘/纸面切换生效 | 点「实盘」→ 出现 paper-only 提示；再点「纸面」恢复 | `.fund-switch button[data-m]` |
| 三道门灯条 | 3 个 `.gate-lamp`，含 情绪门/持仓门/选股门 | `.gates-strip .gate-lamp` |
| 引擎徽章 | ≥5 个徽章，含「执行器」「看门狗」 | `.dash-badges .badge, .ex-badge, .mode-badge` |
| 控制台 0 错误 | — | — |
| 截图 | `docs/fe_batch1b_dashboard.png` | — |

## 7. 已知遗留

1. **pytest / 重启 / CDP 截图未跑**（沙箱禁子进程），见 §5。
2. 实盘账本在新栈不可读（`/api/ledger?mode=live` 返回 ok=false），实盘视图只有提示 —— 需批次 2 或实盘读通道补齐。
3. `/api/status.trade_mode` 恒 null，模式徽章带「·视图」后缀；等 core 白名单放开 `engine_state.json` 再回到单一数据源。
4. 撤单、条件单管理、个股开 K 线跳转、跨卡跳转统一入口：均按任务书留给批次 2 / 1c。
5. `tools/cutover_gate.py::_check_pytest` 的目录清单里还没有 `app/plugins/dashboard/tests`（门禁不跑本插件测试，
   `python -m pytest -q` 会跑到）。是否把它并入门禁属流程改动，本批未动。
