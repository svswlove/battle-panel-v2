# concept 插件契约（v0.2.0，只读 + 卡片）

**插件**：`app/plugins/concept/`（id=`concept`，v0.2.0）
**挂载**：`backend.mount = "root"` → 完整路径 `/api/concept.json`（与旧端同名；前端用绝对 `/api/` 前缀，shell 不拼插件前缀）
**安全边界**：只有 GET；不写任何文件、不连 QMT、不写 `data/panel`（`tools/cutover_gate.py` 静态扫描守着）。
**数据源**：唯一事实源 = Markdown 知识库（`夏天-activity/概念追踪/`，`ctx.text_dir("concept_tracking")`）
＋ `ths_board.db`（板块反查）＋ `stock_names`（名称→代码反查）。

## 1. 端点

| 端点 | 说明 |
|---|---|
| `GET /api/concept.json` | 总表索引：`{ok, updated, active[], watch[], chain[], archived[], counts{}, sector_map{}, unresolved_targets[], sector_unresolved[]}` |
| `GET /api/concept.json?slug=<目录名>` | 单概念概况：`{ok, slug, html}`；`html` 是服务端 MD→HTML（含个股名 linkify） |

- 总表缺失 → 仍返回 200 + `{ok:false, error}`（旧端同口径，由前端渲染缺失态）。
- 概况缺失 / 非法 slug → 404；读取异常 → 500。
- 字段口径逐字对齐旧 `engine/server.py::api_concept`（1766/2022 行）与 `_backfill_chain_codes`（1959 行）。

## 2. 卡片（v0.2.0 新增，2026-10-24 R3）

卡片 `concept.main`（slot=main / order=55），行为基准 = 旧概念 Tab `engine/web/index.html` 7690 行 `renderConcept` 起。
**后端零改动**：0.1.0 → 0.2.0 只加了 `frontend` 段与 `frontend.js`。

| 项 | 约定 |
|---|---|
| 数据源 | `GET /api/concept.json`（索引）+ `GET /api/concept.json?slug=`（概况）；slug 走 `encodeURIComponent`（中文目录名） |
| 视图 | 四视图胶囊，顺序与计数照旧：活跃 / 观察池 / 链位 / 已归档；胶囊显示 `counts[key]` |
| 活跃视图 | 阶段光谱（hot/warm/cool 按 `counts` 比例分条，色走 `--up-solid/--warn-solid/--accent-solid`）＋ 概念卡墙（热区左边条 / 阶段徽标 / 逻辑·持续性 / 板块 chips / 核心标的 chips / 更新日 + 下一节点） |
| 观察池视图 | 多空光谱（bull/bear）＋ 卡片（多空徽标 / 参照板块 chips / 待验证 / AMV 权重变化 5 位小数） |
| 链位视图 | 上游 → 本环节 → 下游 三段流向卡；状态徽标从活跃/观察两池按概念名对（观察先入、活跃覆盖） |
| 已归档视图 | 概念名 + 归档说明 |
| 筛选 | 只对活跃 / 观察池生效（搜索框也只在这两视图显示，同旧 `_cpShowView`）；切视图清空筛选值 |
| 详情 | 点带 `data-slug` 的卡 → 卡内详情视图（面包屑 + 返回列表），正文是服务端 `html` 直接落地；无 `slug` 的卡不可点（提示「无概况页」） |
| 个股名 | **只写占位属性** `data-open-kline` / `data-open-name`；板块写 `data-open-amv`。本卡不绑点击逻辑，由旧页桥接（v2bridge）捕获 |
| 缺码 | 解析不到代码 → `cp-chip-off`（虚线描边 + 压暗，显式不可点；不伪装可点） |
| 刷新 | 不声明 `refreshEvery`（总表日频）；`refresh()` 保留契约占位，卡头有「刷新」按钮 |
| 未搬 | 旧页「物理AI 自建指数读数带」（`/api/physical_ai.json`）：该端点当前 404，旧页同样不渲染。后端补上后按卡头下方同一位置加，不改本卡结构 |

## 3. 与旧端的差异（登记，不改）

| # | 差异 | 原因 |
|---|---|---|
| 1 | 概况弹层改为卡内详情视图 | v2 卡在 iframe 内，弹层需自带遮罩层；卡内视图少一层 DOM，返回路径更短。正文 HTML 与样式类名（`.cp-md`）不变 |
| 2 | 深链「板块→概念」高亮/自动展开（旧 `_cpApplyPending` / `ConceptTab.focus`）未搬 | 属于跨 Tab 深链，归 v2bridge（R4）的入参契约，不在本卡范围 |
| 3 | 物理AI 读数带未搬 | 见上表「未搬」 |
