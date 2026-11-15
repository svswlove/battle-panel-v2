# 作战面板 v2 · 前端路线图（2026-10-24 起）

> 定位：后端 32/32 端点对齐、706 测试、门禁 8 项 PASS，观察期自动运行中。
> 前端目标是「换掉对外壳与旧 index.html 的依赖」，不重写内容页业务逻辑。

## 现状盘点（2026-10-24 00:50 实测）

**三层结构并存：**

| 层 | 位置 | 状态 |
|---|---|---|
| 新六卡壳 | `app/web/`（`/`，3.4KB shell.js + 342B manifest 驱动） | 5 插件 6 卡可用；**硬编码深色、无主题系统** |
| 旧外壳 | `app/web/legacy/shell.html`（sync_vendor 托管，26KB） | 8810 下侧栏/主题/字号/搜索可用；**急停/重启 404**（`/api/shell/kill|restart|search`、`/api/sign` 未迁，实测 404） |
| 旧内容页 | `engine/web/` 挂载为 `/legacy/`（零拷贝） | 9 Tab 全渲染，依赖 32 个兼容端点；纸面/选股写等 4.5-4.8 新能力未接 |

**后端就绪但前端未消费的能力**：纸面交易 11 路由（下单/条件单/账户）、选股三写端点、review paper=1、concept、HCL 卡。

## 架构原则（不变式）

1. **数据/契约 → domain → UI**：卡片只吃 `ctx.api.get`，不 fetch 裸路径。
2. **一个 Tab 一个 owner**：内容页按 Tab 绞杀，一次一个 Tab，不并行铺开。
3. **旧 index.html 是行为基准**：每迁一个 Tab，新旧对照截图/数据双验，行为对齐才算过。
4. **无框架、无构建**：原生 ES module（现有契约 §5），不引 React/Vue/打包器。
5. **主题唯一源 themes.css**：从旧壳平移（含 `--theme-manifest` 九主题 + data-fs 三档），v2 壳与旧壳共用，禁复制色表。
6. **绞杀者迁移**：`/legacy/shell.html` 保持可用直到最后一个 Tab 迁完；URL `/` 永远是新版入口；每个 Tab 迁完旧壳里对应 Tab 仍在（可回退）。

## 批次规划

### 批次 0 · 基础设施补齐（v2 壳补课，一次到位）
**目标：v2 新壳达到旧壳的 chrome 完整度，旧壳的 4 个 404 端点补上。**

- 0.1 `themes.css` 平移进 v2 壳（`app/web/themes.css`），shell.html 挂 `data-theme`/`data-fs` + 主题切换/字号 UI + localStorage 记忆（照搬旧壳 Shell.applyTheme 逻辑）。
- 0.2 旧壳依赖端点补齐：`/api/shell/search`（POST，复用 compat `/search/all` 的 build_search）、`/api/sign`、`/api/shell/kill`、`/api/shell/restart`（后两者读旧端 server.py 实现，涉及进程管理需评估——旧端怎么做就怎么做，不改语义）。
- 0.3 v2 壳侧栏改成旧壳同款 rail（图标+悬浮提示+搜索入口），搜索面板复用 0.2 的 search 端点。
- 0.4 卡片挂载契约扩展（如需）：错误卡片样式走主题 token。

**验收**：`/` 九主题切换即时生效；旧壳 6 个端点全 200；v2 壳与旧壳同屏截图比对。

### 批次 1 · 高频核心 Tab（watchlist → dashboard）
**目标：盘中最常看的两个 Tab 在 v2 壳原生可用。**

- 1.1 **watchlist 卡升级**：现 6KB 卡是简化版；对照旧 index.html `panel-watchlist`（分组/折叠/现价 5s 刷新/搜索添加/移除/拖拽/组管理）逐功能对齐，写端点已就绪（compat_watchlist 13 路由）。
- 1.2 **dashboard 卡**：新建 dashboard 插件前端卡，对照 `panel-dashboard`（账户概览/AMV/告警/作战表摘要，3s interval → refreshEvery:3000），端点全就绪（compat 8 个）。

**验收**：新旧两 Tab 同数据截图逐块比对；自选池增删改在两边互通（同一 data/panel JSON）。

### 批次 2 · 交易链 Tab（battle_plan → paper）
- 2.1 **battle_plan 卡升级**：对照 `panel-battle_plan`（作战表+签收+仓位门），`/api/sign` 就位后接全。
- 2.2 **paper 交易卡**（新）：消费已就绪的 11 路由——下单/撤单/委托列表/条件单/纸面账户/流水。下单走受控确认（复核弹层），不自动下单。

**验收**：纸面下一笔测试单全链路（下单→委托→成交回报→账户变动）在 8810 走通，与 8800 账本不串。

### 批次 3 · 研究链 Tab（sector → concept → selection）
- 3.1 sector 卡升级（对照 `panel-sector`，60s interval）。
- 3.2 concept 卡（新，端点已全等）。
- 3.3 selection 卡（新，含 4.7 三写端点 → 选股池增删 UI）。

### 批次 4 · 低频 Tab + 收尾（intraday → review → ops）
- 4.1 intraday 卡（`panel-intraday`，盘中脉冲/板块轨迹）。
- 4.2 review 卡（v6 + paper=1 双渲染，端点 4.5/4.8 已全等）。
- 4.3 ops 卡（`panel-ops` + `ui_notes`）。
- 4.4 **切壳**：`/` 指向 v2 壳为默认；`/legacy/shell.html` 保留 30 天后下线。

## 派工节奏

- 每批一份任务书（目标/交付物/白名单/自测/交付格式），DSHana 执行、01 独立验收后 commit。
- 批内 01 可自做的 ≤2 文件小项自己做（如 0.1 themes 平移）。
- 行为基准=旧 index.html：验收时新旧同屏对照，视觉差异列表如实记录，不算失败项。
- 前端验收工具：Playwright/CDP 截图对比（engine/tests/panel/ 已有 _rs_*.js 先例可参考）。

## 风险与边界

- **不碰**：`engine/web/`（旧端）、sync_vendor 机制（除非该文件本身就是补丁载体）。
- `/api/shell/kill|restart` 涉及进程管理：照旧端语义实现（杀 8800 进程→拉起），但 v2 侧目标进程是 8810，需在任务书里明确目标差异，避免一键把旧端杀了。
- 纸面下单卡必须带确认弹层（受控写纪律），先只读账户/委托，写操作第二批再开。
