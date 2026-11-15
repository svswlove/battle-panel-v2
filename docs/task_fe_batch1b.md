# 任务书 · 前端批次 1b：dashboard 卡（新建，账本版布局）

> 路线图 `docs/FRONTEND_ROADMAP.md` 批次 1。行为基准 = `app/web/legacy/index.html`：
> `dashShellHtml()` 3026-3045、`renderDashboardLedger()` 3203-3243、`ledgerKpisHtml()` 3072-3096、
> `ledgerPosHtml()` 3098-3128、`ledgerTradesHtml()` 3130-3141、`_bookRow()` 3143-3160。
> 旧版 2026-10-21 起生效的是「账本版」布局（`renderDashboard` 里 `renderDashboardLedger(d); return;`
> 之后是死代码，**不用搬**）。

## 目标

新建 `dashboard` 插件（manifest + backend 薄壳 + frontend.js + tests），在 v2 壳里提供「仪表盘」卡，与旧账本版布局功能对等。

## 布局结构（照旧 dashShellHtml）

```
[引擎徽章行] [实盘|纸面 切换] [asof]
[meta 行]
┌ 主列 ────────────────────────┬ 侧列 ──────────┐
│ KPI 六格（总资产/可用/市值/   │ 活跃单          │
│  当日盈亏/持仓盈亏/现金占比）  │ 流水记录        │
│ 持仓表（14 列）               │                │
│ 今日成交                      │                │
└──────────────────────────────┴────────────────┘
```

## 数据源（已实测就绪，2026-10-24 02:2x）

| 用途 | 端点 | 实测 |
|---|---|---|
| 账本（主数据） | `/api/ledger?mode=paper|live` | keys: account/asof/book/conds_expiring/mode/ok/positions/trades；paper 当前 0 持仓 0 成交（**空账本必须正常渲染，不能报错**） |
| 引擎/执行器/看门狗徽章 | `/api/dashboard.json` | keys: account/alerts/engine/environment/executor/meta/orders_today/positions/risk_locks/watchdog |
| 三道门 | `/api/gates` | keys: gate1/gate2/gate3/updated_at/version |
| 实盘/纸面切换 | `/api/status` 的 trade_mode（或 dashboard.json 里的 mode） | 旧版 `_tradeMode` 来自 `/api/status` 3s 轮询 |

> 注意：`/api/ledger` 与 `/api/dashboard.json` 由 `compat` 插件提供（root 挂载）；`/api/gates` 也是。dashboard 插件自己的 backend 只做薄壳（健康/聚合转发），前端用 `ctx.api.get('/api/ledger?...')` 绝对路径消费（shell.js 已支持）。

## 功能清单

| # | 功能 | 旧实现要点 | 要求 |
|---|---|---|---|
| 1 | 引擎徽章 | state（ARMED/FROZEN 配色）、mode、纸面/实盘徽章、执行器徽章（RUNNING/未运行/心跳超时）、看门狗徽章（正常/告警/异常/状态文件缺失）、急停徽章、签收状态、心跳 | 同款全部徽章；颜色走 token（`--up`/`--danger`/`--warn`/`--dim`） |
| 2 | 实盘/纸面切换 | `_fundView` / `_ledgerMode()`，localStorage 记忆；切换后重拉 ledger | 同款；切到空纸面账本要显示「无持仓 / 今日无成交」，不报错 |
| 3 | KPI 六格 | 总资产、可用（带冻结）、市值、当日盈亏（金额+%）、持仓盈亏（金额+%）、现金占比 | 同款六格；红涨绿跌；数字千分位 2 位小数 |
| 4 | 持仓表 | 14 列：代码/名称、余额、可用、冻结、成本价、现价、参考盈亏、盈亏%、当日盈亏、当日%、市值、仓位、今买、今卖；`price_source=cost_fallback` 标「成本」灰显 | 同款 14 列；列宽紧凑、数字右对齐 |
| 5 | 今日成交 | 时间/名称/买卖/价格×数量/金额·费用；买红卖绿 | 同款 |
| 6 | 活跃单 + 流水 | `book` 里的委托/条件单：时间/类型(委/条)/名称/方向/价格×数量/状态/剩余天数/撤单按钮；流水记录 | **本批只读**：撤单按钮渲染但点击弹「批次 2 接」提示（写操作留批次 2） |
| 7 | 三道门状态灯 | `/api/gates` gate1/2/3 + 状态色 | 同款灯条 |
| 8 | 刷新 | 旧版 3s 轮询 | `refreshEvery: 3000`；但**账本与持仓变化慢，KPI/持仓 3s 重建会闪**——旧版 2026-10-21 专门修过「屏幕一直闪烁」（见 renderDashboardLedger 注释）。**必须照搬「首次搭壳、之后只改内容不重建 DOM」策略**：`_setHtml` 比对缓存后再写 |
| 9 | 空态 | 无持仓/无成交/无活跃单 | 各自 muted 文案，不塌 |

## 不做（本批排除）

- 撤单/下单（批次 2）
- 条件单管理（批次 2）
- 个股点击开 K 线（跨卡跳转，批次 1c 统一做；本批用 `data-open-kline` 属性预留）
- 旧版死代码（`renderDashboard` 里 `return` 之后的全部逻辑）

## 契约与约束

1. 新建插件四件套：`app/plugins/dashboard/{plugin.json, backend.py, frontend.js, tests/}`；manifest `kind=domain`、`frontend.cards=[{id:"dashboard.main", title:"仪表盘", order:5}]`。
2. backend 薄壳：只做健康检查与必要的聚合（如需）；**不写盘、不直连 sqlite/xtquant**；消费 `/api/ledger` 等走前端 ctx.api。
3. 颜色走 themes.css token，**禁裸 hex**（旧版大量 `#d33`/`#178a4c` 必须换 `--up`/`--down`）。
4. 刷新纪律：3s tick，但 `_setHtml` 缓存比对 + 持仓表按行 diff 或整体比对后再写，避免闪烁。
5. 无框架无构建，原生 ES module。

## 改动白名单

- 新建：`app/plugins/dashboard/**`（plugin.json / backend.py / frontend.js / tests/）
- 改：`app/core/main.py`？**不改**——插件扫描自动发现新插件（先读 core 的插件发现逻辑确认目录约定）
- 新建：`docs/HANDOFF_20261024_fe_batch1b.md`

禁止：改 `app/web/legacy/`、`engine/`、其他插件、引依赖、git commit。

## 环境事实

- Python：`OH-WorkSpace\envs\qmt312\Scripts\python.exe`
- 8810 在跑；新插件需重启 8810 才被发现
- 插件目录约定：`app/plugins/<id>/`，manifest 见 `docs/CONTRACT.md` §2
- 参考现有插件：`app/plugins/amv/`（有 frontend.js + backend.py + tests）
- 旧版样式参考：`app/web/legacy/index.html` 的 `#panel-dashboard` 相关 CSS（2403-2436 行）

## 自测要求

1. `python -m pytest app/plugins/dashboard/tests -q` → 全绿
2. `python -m pytest -q` → ≥721 passed
3. 重启 8810 → `/_plugins/health` 里出现 `dashboard` 且 ok
4. CDP 实测：打开 `http://127.0.0.1:8810/` → 点 rail「仪表盘」→ 断言：KPI 六格有值、持仓表渲染（paper 空账本显示「无持仓」）、实盘/纸面切换生效、三道门灯条渲染、控制台 0 错误；截图 `docs/fe_batch1b_dashboard.png`
5. **文件完整性**：每个文件回读验证；js `node --input-type=module --check`；回执报行数

## 交付格式

回执 ≤300 字：做了什么 / 测试数字 / CDP 断言 / 文件行数 / 遗留 + 路径。
