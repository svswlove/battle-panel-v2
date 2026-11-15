# 当前状态（2026-10-26 凌晨 · v2 元素级标注已通 / 全量 1471 绿）

> **进行中**：旧端口下线（8800/8002/8801）→ HANDOFF_20261026_old_ports_retire.md（2026-10-26 交接，前端侧已零请求，剩自愈链拆除）

> **接续入口：先读下面 §-1（10-26 凌晨增量），再翻 §0。**
>
> 上一版（10-25 深夜）已归档为 `STATUS_20261025_morning.md`。

---

## -1. 2026-10-26 凌晨增量（v2 元素级标注）

### -1.1 需求与结果

主人反馈两件事：① 平台叠在页面上的标注只能选整页，粒度太粗；② 那个按钮还是坏的、占一整行。
定位后确认那个胶囊 + 浮条是 **Hana 内置浏览器注入的**（v2 源码、DOM 扇描、全局对象三处零命中），不属面板代码。
面板自己的元素级标注已交付：`app/web/v2_note.js`（新，27KB）+ `shell.js` 接入 + `shell.html` 引样式。

### -1.2 本轮修的真缺陷（入口时序塌陷）

`?card=` 单卡视图下**标注入口完全消失**。根因：`bindNote()` 走顶层 `await import`，完成早于 `boot()` 藏 rail
→ 模块取了 rail 按钮 → 按钮随后被藏 → 浮标兑底只在 `init` 里判过一次，永不重建。
改法：`v2_note.js` 入口拆成可重算的 `syncEntry()`，`shell.js` 在藏 rail 后 + `boot()` 末尾各调一次。

### -1.3 验收（均为实测，可重跑）

| 探针 | 覆盖 | 结果 |
|---|---|---|
| `_vis/_note_e2e_verify.js` | 单卡视图完整链路 | **14/14** |
| `_vis/_note_frame_verify.js` | 旧页嵌 v2 推模式 | **8/8** |
| `_vis/_note_frame_write_verify.js` | 跳文档写入归属 | **9/9** |
| `_vis/_note_rail_entry_verify.js` | 完整布局 rail 入口 | **10/10** |

关键证据：selector = `div.dash-main>…>div.v:nth-child(2)`，回文档查询命中 **isSameNode=true、depth=6、childCnt=0**（真到叶子）；
落盘 `page=v2:card:dashboard::dashboard.main`，未串旧页 `sector:`；pin 回绘定位正确；探针写入自行 resolve，零残留。
测试：`projects/panel-v2/app` **1472 passed / 4 skipped / 0 failed**；`test_v2_note.py` 15 passed（含新增入口时序守卫）。

### -1.4 重复控件已清（截图坐实后）

旧页嵌 v2 时右下角曾同时出现两个标注按钮：旧页「✎ 标注」(y956) + v2 浮标「标注」(y880)，
不重叠但同屏同类控件重复（主人明确反感）。已在 `ensureButton` 里加 `embedded()` 拦截：
被父页嵌进 iframe 时不造浮标（父页按钮经 `pushFrames` 已能驱动 v2 内标注，实测 8/8）。
浮标只保留给「独立打开且 rail 被隐藏」的真空白场景。截图复测：右下角只剩 1 个按钮。

### -1.5 已知边界

1. 平台注入的标注浮条（胶囊 + 图标条）无法从面板代码处理；它是 Hana 内置浏览器的 UI，如仍碍事需在客户端侧关。
2. 标注 pin 位置随滚动重算（scroll capture）；页面重载后靠 selector 重新定位，若卡片 DOM 结构变化导致 selector 失效，该 pin 不绘制（静默跳过，不画鬼影）。

---

## 0. 2026-10-25 下午增量（01 + DSHana 并行两线）

### 0.1 C 单疑点已查清，范围改定（结论反转）

**审计**（`docs/AUDIT_20261025_intraday_gap.md`，01 已抽查复核）：
- 缺口 ~98% =「板块与个股研究」10624 字（板块表 90 行 + 成分股表 183 行）+「信号流水」5700 字（49×8 列）。
- 原五候选**全部不是缺口**：冰点/广度/AMV v2 已实现；盘中脉冲属自选池卡；`_evo*` 仅 414 字（3.8%）——
  **「差 14000 字主要是 _evo*」假设证伪**。
- 详见旧版 §6 的全部待办已按此改道。

### 0.2 C2a：研究大表已搬完并验收

`frontend/research.js`（475 行）+ `research.css.js`（137）+ frontend.js 编排。与旧页 `_rsHtml()` 逐字节对齐
（250564=250564）。**01 验收**：pytest 全绿 + 浏览器探针 13/13（90/183 行、右键菜单接通、选板块/返回/排序/滚动保持）。
文档 `docs/HANDOFF_20261025_intraday_research.md`。

### 0.3 C2b：信号流水 + 会话行 + 成交额 + KPI 已搬完并验收

`frontend/{audit,audit.css,quotes,quotes.css}.js` + 24 条测试。审计明细 details 默认折叠；KPI spark 改先取数再渲
（首绘空 d 20→0）。**01 验收**：pytest 全绿 + 浏览器探针 11/11（392 td、会话行、成交额 3 KV、10 KPI、块序）。
文档 `docs/HANDOFF_20261025_intraday_audit.md`。

### 0.4 测试基线与修复

- **全量 pytest：1427 passed / 4 skipped / 0 failed**（盘中插件范围 231 passed / 1 skipped）。
- B 线修复 compat_misc 三 failed（昨天未提交的 `_is_custom_board` 业务变更没跟上测试；os→pathlib / 断言守新语义 /
  real 测试改整份透传等值）。文档 `docs/FIX_20261025_compat_misc_gate.md`。
- **01 修了 DSHana 两处测试缺陷**（沙箱跑不了 pytest 未自检到）：① css 门禁用例 import 错模块；
  ② 三个测试文件 `PANEL.parents[2]` 路径多上一级，7+2 条对齐/jsdom 守门用例被静默 skip（坑 6 复现）。

### 0.5 B 单完全收尾

三主题菜单配色探针 39/39 全绿（颜色全走 token、对比度全过 AA）。探针 `_vis/_ctx_theme_cdp.js`。

### 0.6 当前待办（按优先级）

1. **盘中实时态待开市验**（增量重绘 / spark 真实往返 / live=1）——所有盘中交付的公共边界。
2. **regime-mark（全A 多/空标记）未接**：需 3 个端点，建议单独一单。
3. **_evo* 日内演化是否搬**：审计判性价比最低（414 字 / 55 函数 / 需改已验收 sector.js），待拍板。
4. **口径收口清单 B1-B8**（`docs/TODO_20261025_caliber_convergence.md`）未动。
5. 窄屏三档断点 / L3·概念层级 / 跨浏览器未验。

### 0.7 待主人拍板（剩一项；①③已拍板）

1. ~~止损分级~~ **已拍板（2026-10-25 深夜）：只删档位文字标注**，距离条/止损线/距离列/整卡全保留。已执行（engine/web/index.html 4 处：ST_CN 档位表、距离条后缀文字、loadPnl 标题规则文字、loadNearTriggers 标题黄闪说明），静态验证通过、日志已登记。
2. ~~旧页 _rsSrt bug~~ **已拍板（2026-10-25 深夜）：不回修旧页**，随旧页退役（待明天实盘验证后退役）。**已知缺陷记录**：engine/web/index.html 4395 的 `_rsPaint` 缓存签名不含 `_rsSrt`，旧页点排序按钮只改状态不重拉不重排（实测坐实：连点三列首行与表头 class 逐字节不变）；v2 卡片侧已修（srt 计入签名）。若旧页退役前仍有人用旧页研究卡排序，该缺陷仍在。
3. **`sector:952-955` 回退单轨**：嗅嗅建议删，01 建议保留（旧 8002 既有行为）；已向主人解释语义，口径收口时定。

---

## 1. 方向修正（今天推翻了前几天的做法）

### 1.1 旧前端本来就能跑在新后端上

`http://127.0.0.1:8810/` 服务的就是 `engine/web/index.html`（旧前端正本，11555 行）。
九个 Tab **八通一空**（空的板块 Tab 是它自己 iframe 嵌 8002，与后端无关）：

| Tab | 耗时 | DOM 节点 |
|---|---|---|
| 仪表盘 | 2ms | 85 |
| 作战表 | 165ms | 410 |
| 盘中 | 210ms | 4288 |
| 板块 | — | 7（iframe，非缺陷）|
| 概念 | 160ms | 1271 |
| 复盘 | 157ms | 733 |
| 自选池 | 3449ms | 302 |
| 选股 | 525ms | 806 |
| 后台 | 164ms | 485 |

**接口覆盖 81/81**。旧前端调用的每条 `/api/` 路径 8810 都有路由；其中 2 条返 404 是业务错误
（分组/标注不存在），路由本身在。

### 1.2 我一直在做负收益的事

用「待移植 9974 行」去重写一份已经能跑的 10251 行。根因：v2 卡片前端从第一天起就是**重写**，不是搬运。

证据（旧页 1280 个 CSS 类名，各卡沿用率）：
概念 86%、复盘 90%（这两个是真搬的）；**自选池 9%、板块 12%、K 线 0%（这三个是重写的）**。

### 1.3 「照抄」的正确含义

主人 2026-10-25 指出：v2 板块卡和 8002 不一致（在榜格画成圆点 vs 日期+排名网格）。
根因不是 CSS，是 **DOM 生成代码重写了**：CSS 照搬了 8002 的 `style.css`，
但生成 DOM 的 `board.js`/`views.js`/`detail.js` 是按理解写的，类名和结构对不上 CSS。

**结论**：CSS 搬了 ≠ 视觉一致。必须连**生成 DOM 的代码**一起照抄。

---

## 2. 已完成的实质修复

### 2.1 个股四态缺失值语义统一（口径统一第一例）

**问题**：同一指标在四个文件里算了四遍，缺失值处理各不相同。

**实测后果**（生产库 `data/amv_board.db`）：
- 全市场 1073 只有缺失记录的股票，历史累计分歧 **27001 天**
- **最新交易日两边都有值但结论相反 8 只**：
  000668 / 002667 / 300082 / 301716 / 603183 / 603759 由「多」变「空」；002388 / 002860 由「多」变「偏多」

**根因**（比审计报告深一层）：`app/lib/amv_regime.py::sma` 旧实现 `s += v`，遇 `None` 抛 TypeError，
消费端为不崩只好先置 `0.0` —— 这是「缺失置零」在四处重复出现的根源。
更致命的是 EMA 种子 `e1 = e2 = vals[0]`：首日被置 0，整条 EMA 从 0 起步，该股**全部历史四态被压低**。

**改了什么**：
- `app/lib/amv_regime.py`：`sma` 接受 None（窗口跳过、有效点 ≥ n 才出值）；
  `stock_state_series` EMA 种子取第一个有效值、缺失日沿用前值且判定留空、长度不变；
  `compute_regime` 缺失日两轨不出信号。缺失值语义写入 docstring 作为唯一口径。
- 4 处消费端去置零：`compat_kline:1127`/`:1146`、`compat_watchlist:467`/`:519`
- 新增 `app/lib/tests/test_amv_regime_null_semantics.py`（6 项，含静态扫描禁止再写置零）

**验证**：324 passed；无缺失的 800 只票末值全有结论无回归；8810 上 18 插件全健康。
**文档**：`docs/FIX_20261025_amv_null_semantics.md`

### 2.2 名称表断供修复

`_tmp/stock_names_em.json` 文件不存在（由退役脚本 `build_dashboard_data.py` 每天生成，脚本已退役）。
导致板块详情卡新股只显示代码（如 688809 强一股份）。

**修复**：新增 `tools/build_stock_names.py`，走 eltdx `codes.latest_stocks()`，5580 条 / 4.3 秒。

### 2.3 追踪池彻底删除

`pool_track` Tab 已于 2026-10-15 摘除，但函数体还在（操作不存在的元素）。

**删除**：`engine/web/index.html` 11782 → 11555 行，删 227 行
- 11 个函数（`poolUrgency`/`poolOrdered`/`poolStopMap`/`poolStopCell`/`renderPoolTrack` 等）
- `refreshPoolPositions` + 缓存
- 10 个孤立声明（`poolSort`/`URGENCY`/`POOL_STOP_NEAR_PCT` 等）
- `loadTab` 的 pool_track 分支

**验证**：九 Tab 八通一空、运行时异常 0 条、`renderPoolTrack` 等在浏览器里 `undefined`、`posSort` 完好保留。

---

## 3. 三单并行交付（2026-10-25 傍晚）

### A 板块/详情 DOM 照抄 — **通过**

改 `app/plugins/sector/frontend/{board,views,detail}.js` 整文件重写为 8002 逐字照抄，
`legacy.css.js` 补规则，`frontend.js` 调编排。`compute.js` 未动。

**我独立核实**：
- 在榜格 30 个 `hm-c`、`grid-template-columns` 精确 `32px × 10`、11 个描边、**无圆点字符** ✅
- 表格 20 表头 / 25 行 / 在榜列 25 个；网格 25 个 `.viz-cell`；四象 20 气泡 + 20 标签 ✅

**顺手修的两个真缺陷**（照抄救不了）：
1. `legacy.css.js` 只搬了 8002 的 **219/487** 条选择器——表头列定位、`.vizbox`、`.vq-trail`、
   `.hm-*`、详情卡全套都缺。DOM 照抄后缺规则，样式照样失效。
2. **别名层有自引用循环**：`--line: var(--line)` / `--accent: var(--accent)` /
   `--accent-on: var(--accent-on, ...)`，CSS 判定无效 → 边框退化成文字色、强调色全丢。
   实测已修：`--line`=`#42506a` → 表头边框 `rgb(66,80,106)`；`--accent`=`#4da3ff` → 按钮背景 `rgb(77,163,255)`。

**文档**：`docs/HANDOFF_20261025_sector_dom_copy.md`

### B 全局右键菜单 — **通过**

改 `app/web/shell.js`（17 个函数 + `bindCtxMenu()`）+ `app/web/shell.css`（`.ctx-menu` 一组）。
未碰任何插件、core、engine，未重启 8810。

**六项真实鼠标实测全绿**：个股右键弹出 / 子菜单 4 分组+新建 / 复制代码进剪贴板 /
打开 K 线切卡 / 点外+Esc 关闭 / **贴边翻转**（鼠标 1441,638 → 菜单 1296,514，右 1472<1480、下 686<694）。

**两个坑记下来**：
- v2 菜单节点 id 是 `ctx-menu`（连字符），旧页是 `ctxMenu`（驼峰）。拿错表现是「菜单未实现」的误判。
- 我第一次测贴边失败，因为手动改 `style.left/top` 模拟贴边，**绕过了代码里的翻转逻辑**。
  这是今天第二次同类错误（上次是拿 `.click()` 测交互），都是绕过产品真实路径。

**文档**：`docs/HANDOFF_20261025_ctx_menu.md`

### C 盘中照旧页搬 — **第一步通过，第二步未开始**

第一步：`_st*` 20 个函数 + 切片口径共 26 个符号 → `app/plugins/intraday/frontend/{sector,caliber,format,card.css}.js`，
`frontend.js` 599 → 620 行（拆 5 模块）。

**四条约束的处理**：
1. 一次性布尔改成**每实例 `AbortController`**（旧页模块级 `let _stDelegated=false` 在卡片里会静默失效）
2. `panel-intraday`/`stBody`/`stTip` 三个 id 全改容器内查询
3. 浮层从 `body`+`fixed` 改容器子节点+`absolute`，补 capture 阶段 `scroll` 监听重算坐标
4. 4 个工具条 `onclick` 改 `data-act` 委托

**测试**：`pytest app/tests app/plugins/intraday/tests` → **194 passed / 1 skipped**

**⚠ 待查疑点**：v2 盘中内容 **2701 字**，旧页 **16702 字**，差 14000。
`_st*` 搬进去了，但旧页盘中还有一大块内容不在这个函数组里。**换会话后第一件事查这个。**

**⚠ 需报备的误操作**：C 单跑 `git stash pop` 弹出 2026-10-16 旧 stash，碰到
`engine/web/kline_lc.js`（2 处冲突标记），已 `checkout HEAD` 恢复。
**我核实过**：`index.html` 11555 行、追踪池全 0、`posSort` 在、`loadTab` 是清理后样子；
`kline_lc.js` 无冲突标记。两个文件都完好。
**根因：仓库根在 `OH-WorkSpace` 而非 `panel-v2`，`cd` 后 git 作用域错了。派活任务书必须写明这条。**

**文档**：`docs/HANDOFF_20261025_intraday_port.md`

---

## 4. 口径收口清单（未执行，按影响面排序）

见 `docs/TODO_20261025_caliber_convergence.md`。摘要：

| # | 问题 | 级别 |
|---|---|---|
| B1 | 权重 Δ 量纲错 100 倍（落库 `share` 是小数，`sector:1226` 与 `compat_sector:259` 各乘 100） | 高 |
| B2 | `sector:446` 对 None 用 `continue` 删元素 → 日期前移 | 高 |
| B3 | `compat_sector:212` 仍在置零 | 中 |
| B4 | MA10 六种写法收敛（正本需加 `sma_expanding` + `strict_positions` 参数） | 中 |
| B5 | 880 快照两份解析（共享字段映射、不共享有效性判定；且两者各打一次 socket） | 中 |
| B6 | `_is_custom_board` 逐字复制 | 中 |
| B7 | `review_chart.py` 同函数内复制 | 低 |
| B8 | `concept/tests` 扫描范围不全 | 低 |

**分歧记录**：`sector:952-955` 回退单轨——嗅嗅建议删，01 认为应保留（是旧 8002 既有行为，删了是行为变更）。

---

## 5. 前端共用层设计（未执行）

见 `docs/DESIGN_fe_shared_core_20261025.md`。

**先纠正一个错误结论**：先前报的「142 个共用核心函数 / 1969 行」**是错的**。
错因：闭包分析把模板串里的函数名当调用。旧页 `renderDashboard` 里有
`onclick="loadTab('dashboard')"`，而 `loadTab` 内部引用全部 7 个 render →
闭包退化成整页可达集。反证：三个 Tab 的「共用行数」精确等于同一个 1969。

**修正后真实数字**（`Hubagent/01/_vis/shared_core_fixed.py` 可复现）：
- **≥3 Tab 共用：2 个函数 / 14 行**
- 1 Tab 专属：137 个 / 2606 行

**结论**：旧页 Tab 之间基本独立，「逐 Tab 做成卡」结构上成立。
唯一例外是盘中（1171 行，其中 1089 行专属）——夏天判断这一层叫「盘中口径层」，不是全站共用核心。

**五模块切分**：`format.js`(16) / `symbol.js`(6) / `position.js`(12) /
`sector_amv_data.js`(33+6) / `sector_amv_view.js`(49)，约 1215 行，依赖无环。

---

## 6. 换会话后第一件事（已被 §0.6 取代，历史保留）

> 以下四条已全部处理完（见 §0）：① 差的 14000 字已查清；② C 单第二步已改道并完成（C2a+C2b）；
> ③ 全量 pytest 复跑 1427 绿；④ B 单遗留菜单配色已收尾。⑤ 止损分级待拍板（见 §0.7）。

---

## 7. 已知边界

- 盘中实时增量未验（周日数据为 T-1 收盘）
- `data-open-amv` 点击效果未验（B 单在改 shell.js 期间）
- 四象 L3/概念层未验、窄屏三档断点未验（只验 1440 宽）、跨浏览器未验
- 旧 8800/8002/8801 未动、未重启；8810 本轮重启过一次（10:44，缓存改动生效）
