# HANDOFF · R3 concept + review 概念与复盘卡（2026-10-24）

> 任务书：`docs/task_R3_concept_review.md` ｜ 执行：01 ｜ 结论：**两卡完成，零后端改动，全量与门禁 PASS**

## 1. 交付物

| 文件 | 动作 | 说明 |
|---|---|---|
| `app/plugins/concept/plugin.json` | 改 | 0.1.0 → 0.2.0；新增 `frontend` 段（卡 `concept.main`，slot=main，order=55）；`backend`/`data` 段逐字未动 |
| `app/plugins/concept/frontend.js` | 新建 | 24KB，四视图 + 卡内详情 |
| `app/plugins/concept/tests/test_concept_frontend.py` | 新建 | 35 项静态契约测试 |
| `app/plugins/review/plugin.json` | 改 | 0.1.0 → 0.2.0；新增 `frontend` 段（卡 `review.main`，order=60）；`backend`/`data` 段逐字未动 |
| `app/plugins/review/frontend.js` | 新建 | 60KB（其中 443 行 CSS 从旧页逐字搬入），三视图 + 三级降级 |
| `app/plugins/review/tests/test_review_frontend.py` | 新建 | 36 项静态契约测试 |
| `docs/concept_contract.md` | 新建 | 端点 + 卡片节 + 差异登记 |
| `docs/review_contract.md` | 新建 | 端点 + 卡片节 + 差异登记 |

> 注：任务书写的是「各追加卡片节」，但两份契约文件在仓库里**不存在**（`docs/` 下只有 `battle_plan_contract.md` / `sector_contract.md` / `paper_contract.md` 等）。按追加语义新建为完整契约文件（端点节照插件 docstring 与旧端逐字口径重写）。

## 2. concept 卡（concept.main，order=55）

行为基准 = 旧 `renderConcept`（index.html 7690 起）+ `_cpHtml/_cpCard/_cpWatchCard/_cpChainCard/_cpArchCard/_cpShowView`。

- **四视图胶囊**：活跃 21 / 观察池 15 / 链位 26 / 已归档 5（计数取 `counts[key]`，标签照旧）。
- **活跃视图**：阶段光谱（hot 2 / warm 11 / cool 8，按 `counts` 比例 flex 分条，色走 `--up-solid/--warn-solid/--accent-solid`）+ 21 张概念卡（热区左边条、阶段徽标、逻辑·持续性、板块 chips、核心标的 chips、更新日 + 下一节点）。
- **观察池**：多空光谱 + 卡片（多空徽标 / 参照板块 chips / 待验证 / AMV 权重变化 5 位）。
- **链位**：26 张三段流向卡（上游 → 本环节 → 下游），状态徽标从活跃/观察两池按名对齐（观察先入、活跃覆盖，同旧 `cpStatus`）。
- **已归档**：5 张（名 + 归档说明）。
- **详情**：点带 `data-slug` 的卡 → 卡内详情视图（← 返回列表 + 标题）；正文是服务端 `?slug=` 返回的 `html` **直接落地**（不二次解析）。实测「AI应用/数据要素」正文 13,304 字符、4 张表、59 处 `data-open-kline` 占位，横向无溢出（scrollWidth == clientWidth == 1323）。
- **个股/板块名**：只写 `data-open-kline` / `data-open-name` / `data-open-amv` 占位，本卡零点击逻辑（`KLineApp`/`postMessage` 均不出现），桥接后旧页捕获。缺码 → `cp-chip-off`（虚线 + 压暗）。
- **筛选**：只在活跃/观察池显示（同旧 `_cpShowView`），切视图清空筛选值（避免切到链位后旧关键字把卡静默藏掉）。
- **未搬**：物理AI 读数带（`/api/physical_ai.json` 当前 404，旧页同样不渲染）；跨 Tab 深链高亮/自动展开（属 R4 bridge 入参契约）。差异已登记契约 §3。

## 3. review 卡（review.main，order=60）

行为基准 = 旧 `renderReviewMd`（8014 起）→ `renderReviewMdPaper` → `renderReviewMdV6`。

- **三视图显式切换**（图表 / 纸面 / 文档），每视图保留旧端同一条**自动降级链**：
  - 图表视图：图表 → 纸面 → v6（成功判据 `ok===true && head && meta`）
  - 纸面视图：纸面 → v6（成功判据 `paper===true && !degraded && html`）
  - 文档视图：仅 v6
  - 回退时卡上留一行「已自动回退到「X」：原因」，不静默。
- **图表**：`/api/review_chart.json` → `window.RVC.render(D, mount, mode)`。组件库复用旧仓 `review_chart.js`（同源静态 `/legacy/review_chart.js`），懒加载一次。
- **档位**：简报 / 完整，localStorage 沿用旧键 `panel_rv_mode`；档位条只在图表视图显示。实测简报 5 段、完整 8 段。
- **纸面**：`.rv-paper > .sheet > masthead + main.doc + colophon`；服务端派生 CSS 注入 `<style id="review-paper-css">`（20,738 字符），卸载时移除。实测桌面底 `rgb(232,229,223)`、纸面 `rgb(250,248,244)`、版心 1209px、横向溢出 0、报头标题正常。
- **文档 v6**：`?theme=<data-theme>`；blockquote 成对转换（⚠→`.warning` 2 处、💡/📊→`.highlight` 10 处），未做全局 `</blockquote>` 替换。主题切到 moonwhite 后正文换肤成功（底 `rgb(245,245,247)` / 字 `rgb(29,29,31)`）。
- **样式来源**：旧页内联 `<style>` 中 `#panel-review` 段 + `.rv-*` 段**逐字搬入**（脚本抽取，不改写选择器），靠卡内 `wrap.id='panel-review'` 承接作用域。**不改 shell 的 host 元素 id**（宿主属性归 shell）。
- **未搬**：泳道图悬停/点击钉住（v6 默认 `charts` 双 null，无图可交互）；表格「代码+名称」转可点（依赖旧页全局 `kName/kTsPlayable`，归 R4 bridge）。差异已登记契约 §3。

## 4. 实测（浏览器：headless Chrome + CDP，真实 /v2/ 壳）

8810 进程启动早于本次改动，`/_plugins` 清单里两插件 `cards` 仍为空，故实测脚本不靠侧栏导航，而是**直接 import 插件 frontend.js + 真实 ctx 契约（ctx.api.get + 绝对 /api/）+ 真实 themes.css + 真实端点 + 真实组件库**挂到 `/v2/` 的 stage 里；只有「挂载入口」由脚本代劳。

结果：**PASS 32 / FAIL 0**，控制台零错误（`window.onerror` + `unhandledrejection` 双通道监听）。

| 组 | 项 | 结果 |
|---|---|---|
| concept | C0–C13：挂载 / 四胶囊计数 / 21 卡 / 占位属性 83+16 / 光谱三色 / 卡片样式 token 生效 / 详情 13304 字符 4 表 59 占位 / 返回 / 链位 26×3 段 / 筛选框显隐 / 卸载空转 | 14 PASS |
| review | R0–R14：挂载 / 卡内 `#panel-review` / 三视图 + 档位条 / 图表 5 段 + 8 SVG / 顶行字段 / 纸面结构 + CSS + 桌面底 + 报头 / v6 7 表 + warning 2 + highlight 10 + h2 色条 / 主题换肤 / 卸载清 CSS | 18 PASS |
| 降级（jsdom 夹具） | 图表接口 502 → 自动回退纸面（提示「图表数据不可用」）；图表 + 纸面双挂 → 回退 v6（提示两条原因） | 2 PASS |

## 5. 自测命令与结果

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q          # 1221 passed, 4 skipped   exit=0
python tools\cutover_gate.py # PASS 43.2s  exit=0
```

门禁明细：8810 health OK / 全部插件健康 OK / shadow 双跑 OK / AMV 原始 JSON 比对 OK / battle_plan 写域接管 OK / **插件无写代码 OK（含新增两个 frontend.js）** / kline 边界声明 OK / pytest 全套 OK。
新增测试：concept 35 + review 36 = 71 项，两插件目录合计 176 项全绿。

## 6. 过程中修掉的两个真实缺陷（实测才暴露）

1. **图表组件库加载无超时**：脚本既不触发 `load` 也不触发 `error` 时（网络挂起/被拦截），`loadRvc()` 的 promise 永不 settle → 卡片永久空白。已加 3s 超时 + 幂等闩锁（`settled`），超时按「组件库不可用」降级到纸面。已补测试 `test_chart_lib_load_has_timeout`。
2. **改 host 元素 id**：最初把 `root.id = 'panel-review'` 设在 shell 传入的 host 上（借用宿主元素），实测在宿主被替换/复用时拿到 null。改为卡内自建 `wrap` 承接 id，宿主属性不动。

## 7. 未验证 / 待后续

- **`/_plugins` 清单刷新**：8810 未重启，清单里两插件 `version` 仍显示 0.1.0、`cards: []`。重启后 shell 才会把两张卡渲染进侧栏（本次未重启，遵守「不重启」纪律）。文件层面已核对：`/plugins/concept/frontend.js` 200 / 27,317B，`/plugins/review/frontend.js` 200 / 60,424B，`/legacy/review_chart.js` 200 / 65,127B。
- **桥接点击**：`data-open-kline` / `data-open-amv` 的实际点击行为归 R4（v2bridge）；本卡只保证属性与旧页同名同值。
- **文件修改日志**：工作区 AGENTS.md §3 要求登记 `OH-Works/文件修改日志/{date}.md`；该路径不在本任务白名单内，且本轮 R1–R5 并发，为避免与其它子代理抢同一文件，**未写入**，建议主控合并批次后统一登记。
- 临时探针（`_tmp_r3_*`、`_tmp_rv*`、`_tmp_bisect*`、夹具与截图目录）收尾已清。
