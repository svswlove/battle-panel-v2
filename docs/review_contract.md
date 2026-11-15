# review 插件契约（v0.2.0，只读 + 卡片）

**插件**：`app/plugins/review/`（id=`review`，v0.2.0）
**挂载**：`backend.mount = "root"` → 完整路径 `/api/review.md`、`/api/review_chart.json`（与旧端同名；前端用绝对 `/api/` 前缀）
**安全边界**：只有 GET；不写任何文件、不连 QMT、不写 `data/panel`（`tools/cutover_gate.py` 静态扫描守着）。
**数据源**：`夏天-activity/每日复盘/`（`ctx.text_dir("daily_review")`）+ 图表装配器在 core（`app/core/review_chart.py`）。

## 1. 端点

| 端点 | 说明 |
|---|---|
| `GET /api/review.md?theme=&date=` | v6 正文：`{date, file, title, html, mtime_ns, charts{swimlane,track}, degraded, error, cache, theme}`（10 键） |
| `GET /api/review.md?paper=1` | 纸面保真：上述 10 键 ∪ `{paper, masthead, css, footer}`（14 键，`paper:true`、`theme:"paper"`） |
| `GET /api/review_chart.json?date=` | 图表模板 RV_DATA（`ok/meta/head/levels/index/amv/breadth/temp/turnover/sector/ladder/anomaly/sentiment/news/positions/gate/tomorrow/scenarios/gaps/md_quotes/file/mtime_ns/cache`） |

- 无 MD / 指定日期不存在 → 404；渲染降级 → `degraded:true` + 保留上一份成功内容；纸面「有文件但渲染不出」→ 502。
- 返回键与旧端逐字一致（`docs/misc_samples/keys_expected.json`）。

## 2. 卡片（v0.2.0 新增，2026-10-24 R3）

卡片 `review.main`（slot=main / order=60），行为基准 = 旧复盘 Tab `engine/web/index.html` 8014 行 `renderReviewMd` 起。
**后端零改动**：0.1.0 → 0.2.0 只加了 `frontend` 段与 `frontend.js`。

| 项 | 约定 |
|---|---|
| 三视图 | 图表 / 纸面 / 文档（胶囊切换）；每视图保留旧端同一条自动降级链 |
| 降级链 | 图表视图：图表 → 纸面 → v6；纸面视图：纸面 → v6；文档视图：仅 v6。回退时卡上一行提示「已自动回退到「X」：原因」 |
| 图表视图 | `GET /api/review_chart.json`，成功判据 = `ok===true && head && meta`（旧 7822 同口径）；渲染走 `window.RVC.render(D, mount, mode)` |
| 组件库 | 复用旧仓 `review_chart.js`（同源静态 `/legacy/review_chart.js`），懒加载一次；**3s 超时兜底**（脚本既不 load 也不 error 时按不可用处理，避免卡片永久空白） |
| 档位 | 简报 / 完整，localStorage 键沿用旧页 `panel_rv_mode`（缺省 `brief`）；档位条只在图表视图显示 |
| 纸面视图 | `GET /api/review.md?paper=1`，成功判据 = `paper===true && !degraded && html`（旧 7868 同口径）；结构 `.rv-paper > .sheet > masthead + main.doc + footer` |
| 纸面 CSS | 服务端派生 CSS 只作用于 `#panel-review .rv-paper`，故卡把 **root.id 设为 `panel-review`**（与旧页容器同名）；CSS 注入 `<style id="review-paper-css">`，卸载时移除 |
| 纸面配色 | 固定暖白纸（report-builder 口径），不随面板主题变（旧端同设计）；主题切换不重渲染纸面 |
| 文档视图 | `GET /api/review.md?theme=<data-theme>`；blockquote 成对转换 ⚠→`.warning`、💡/📊→`.highlight`（逐字搬旧 7897，禁止全局替换闭合标签）；`degraded` 时正文上方给警示块 |
| 主题联动 | MutationObserver 监听 `data-theme`，仅在文档视图清签名重取（旧端同口径） |
| 顶行 | 复盘文件 / 归属交易日 / 最后更新时间（MD mtime，`fmtMtime` 逐字搬入）/ 渲染方式 / 缓存；图表视图的归属交易日取 `meta.trade_date` |
| 样式来源 | 旧页内联 `<style>` 中 `#panel-review` 段 + `.rv-*` 段**逐字搬入**本卡 `CSS` 数组（不改写选择器，靠 root id 复用；含旧页 `--judge-*` 暖橙三值） |
| 刷新 | 不声明 `refreshEvery`（复盘 MD 日频）；`refresh()` 保留契约占位，卡头有「刷新」按钮 |

## 3. 与旧端的差异（登记，不改）

| # | 差异 | 原因 |
|---|---|---|
| 1 | 三视图改成**显式**切换（旧端是自动降级、无视图条） | 主人要能指定看哪一版；自动降级链原样保留在每条视图内 |
| 2 | 泳道图悬停高亮/点击钉住（旧 `wireReviewSwimlane`）未搬 | v6 默认 `charts` 双 null（`OH_MAINLINE_ALLOW != 1`），无图可交互；有图时属新增交互，另开工单 |
| 3 | 表格内「代码列+名称列」股票名转可点（旧 `wireReviewTableKNames`）未搬 | 该函数依赖旧页全局 `kName/kTsPlayable`；本卡只保留服务端已给的 `k-name`/`data-kname` 占位，点击归 v2bridge（R4） |
