# HANDOFF · 前端阶段：逐卡验收、主题跟随、ops 卡化

> 2026-10-25 ｜ 01 ｜ 任务书 `OH-WorkSpace/任务书/20261025_前端阶段_逐卡验收与ops卡化_01.md`
> 结论：四个段落全部做完。九个 Tab 全部走 v2 卡；`ops` 卡新增并已挂到旧页 Tab；11 张现有卡 + ops 卡颜色全部改为吃 `themes.css` token；逐卡实点发现并修掉 4 处「看着能点却点不动 / 点了没消息」的真缺陷。
>
> **测试**：本单新增的 39 例全绿；本单白名单内的域（插件前端 + 壳 + 映射）全绿；
> **全量 pytest 有 2 例红，不在本单白名单内**（`compat_misc` 的 backend 改动，见 §5），属并发工单，已记录不展开。
> 排除那两个并发目录后：**1203 passed / 4 skipped**（`--ignore=.../compat --ignore=.../compat_misc`）。

## 0. 环境与验证方式

| 项 | 值 |
|---|---|
| 8810（生产） | 重启前验一次（确认基线），重启后再验一次；重启原因：`ops/plugin.json` 新增 `frontend.cards` 只在启动时加载（卡内容 `frontend.js` 才是每请求读盘） |
| 8821（临时实例） | `PANEL_V2_DATA_DIR`/`OUT_DIR`/`PAPER_DIR` 指 `%TEMP%\fe8821`，`PANEL_V2_FEED=0`；生产 JSON 复制+DB 硬链接，写类实测全在此 |
| 浏览器探针 | headless Chrome + CDP（`%TEMP%\_fe_*.js`，未进项目目录）；取卡内状态一律 `.v2-frame iframe`（避开任务书点名的坑） |
| 全量 pytest | `envs\qmt312\Scripts\python.exe -m pytest projects\panel-v2\app -q` → 1347 passed, 2 failed, 4 skipped（两红属并发工单，见 §5）；排除并发目录后 **1203 passed / 4 skipped** |
| 本单测试 | `test_ops_frontend.py` 21 例 + `test_fe_shell_bridge.py` 18 例 → **39 passed** |
| cutover gate | `python tools\cutover_gate.py` → 8 项中 7 项 PASS，`pytest 全套` 一项 FAIL（同上两红）；结果落 `docs/cutover_gate_latest.md` |

## A 段 · 逐卡实点（12 张卡）

每张卡都过了四类：点击 / 写操作 / 刷新 / 跨卡跳转。**「点得动」的判定标准是产生了可观察的下一步**（弹窗 display 变 flex、数值变化、接口被调用），不是「元素有 cursor:pointer」。

### A.1 dashboard.main（仪表盘）
- 点击：执行器徽标 `data-open-card="battle_plan"` → **原本点了没反应**（卡片发 `CustomEvent('panel:open-card')`，shell 从未监听，只在 console 留一行「无监听方」）。已修：shell 加 `bindCardJump()` 真监听 → 实测点后主标题从「仪表盘」变「作战表」。
- 写操作：无（只读卡）。
- 刷新：`refreshEvery: 3000`，14s 内 7 次采样 `firstStillConnected=True`、节点数 90 恒定 → 骨架不重建、不闪。
- 跨卡跳转：见上，通。

### A.2 battle_plan.main（作战表）
- 点击：`✔ 全部签收` → 弹 confirm「确认全部签收今日作战表全部预案？…」→ 取消后不落盘（临时实例 `battle_plan.json` 未变）。
- 逐项签收按钮：当前数据已全部签收（`✔ 已全部签收 @2026-10-23 04:30:00`），故 `data-sign-plan` 不出现，只出「全部签收」。**属数据状态，不是缺陷**。
- 刷新：不声明 `refreshEvery`（日频），手动刷新按钮有效。
- 跨卡跳转：卡内无 `data-open-*`（作战表是名单不是个股芯片），N/A。

### A.3 intraday.main（盘中）
- 点击：板块轨迹行 `it-row` → 钉住态 `it-on` 0→1，再点回 0；文本 2084→2086→2084。通。
- 写操作：无。
- 刷新：`refreshEvery 20000`，9s 窗口节点 403 恒定、骨架存活。
- 跨卡跳转：N/A（行点击是钉住，不是开卡）。

### A.4 sector.board（板块）
- 点击：榜表行切换（3 行点击后正文 6708→7510→16959）；语义榜 `sector-rt-item` 点击切板块（+16959 字）；层级/周期两个 select 触发重载。
- 写操作：无。
- 刷新：不声明 `refreshEvery`（T-1 日线），手动/切层级重载。
- canvas：AMV 序列图原先写死 `#ececec/#8a8a8a/#d8d8d8/#e08a1e/#2a6fd6`，浅色主题下网格与坐标文字几乎不可见。改为 `palette()` 现取 token（见 B 段）。

### A.5 concept.main（概念）
- 点击：83 个 `data-open-kline` 个股芯片 + 16 个 `data-open-amv` 板块芯片；实测点个股 → 父页 `#klineModal` display `none→flex`，点板块 → `#amvModal none→flex`。
- 写操作：无。
- 刷新：不声明（日频）。
- `.TI` 指数码：单独验（见 A.12）。

### A.6 review.main（复盘）
- 点击：三视图切换 图表/纸面/文档 均有效（文档 35 行表格、10098 字）；简报/完整档位切换有效。
- **原本表格里的个股名不可点**，卡片注释却写着「个股名只带 data-kname 占位」——实际一个都没有。已补 `wireTableKNames()`（照旧页 `kTsPlayable` + `wireReviewTableKNames` 口径：带后缀可跳 / 裸 6 位仅首位 0,2,3,5,6,9；纯代码格找相邻名称格、名称+代码同格剥离）。实测文档视图产出 5 个可点名称，点「半导体ETF国联安」→ 父页 K 线弹窗标题「半导体ETF国联安 512480.SH」。
- 写操作：无（只读卡）。
- 刷新：不声明；主题切换会重取 v6（原逻辑）。

### A.7 watchlist.main（自选池）
- 点击：搜索框输入「600519」→ 下拉出「贵州茅台 600519.SH」→ 点结果 → 提示「已加入自选池：贵州茅台」，计数 28→29；`✕` → confirm → 移出，计数 29→28，提示「已移出自选池」；分组切换（自选/追踪/走坏/权重/板块）正常，空组显示「暂无卡片」。
- **原本卡内个股名点了没反应**：元素带 `data-role="kline"` 走卡内 `emit('panel:open-kline')` CustomEvent，而该事件无监听方（`console.debug` 一行）。已补 `data-open-kline` 占位（保留 `data-role` 不动卡内原有处理器）→ 实测点「德明利」→ 父页 K 线弹窗标题「德明利 001309.SZ」；板块卡片名/板块芯片补 `data-open-amv` → 点「半导体」→ `#amvModal` 打开。
- 写操作：增（真落盘 `watchlist.json`）、删（真落盘）均已验；拖拽改序/换组只验到事件接线（`dragstart/dragover/dragend` 都在），**headless 下 HTML5 拖拽无法真拖**，标未验。
- 刷新：`refreshEvery 5000`，14s 内 7 次采样：输入框 `activeElement` 保持、值 `zz` 未被冲掉、节点数 345 恒定 → 不丢焦点、不重建骨架。

### A.8 selection.main（选股）
- 点击：分组切换（B1 6 行 → B2 4 行，`sel-stat` 文案跟着变）；排序点两次 → 行序反转（正序/倒序）；筛选 chip 切换生效。
- **原本个股名不可点**（`rowHtml` 只有 `data-code`，无跳转占位）。已补 `data-open-kline` → 实测点「白云机场」→ 父页 K 线弹窗标题「白云机场 600004.SH」。
- 写操作：选股池 ★ 两段式确认（点一次变「确认入池？」armed 态，再点才提交）→ 实测「☆」→「★ on」，`selection_pool.json` 落盘（临时实例）。
- 刷新：不声明（T-1 快照）。
- 虚拟滚动：视口 220px、行容器按 `ROW_H` 撑高，滚动渲染窗口实测正常。

### A.9 amv.main / amv.hcl（活跃市值 / HCL）
- 点击：无交互元素（纯展示），N/A。
- 写操作：无。
- 刷新：不声明（日频）。
- 涨跌语义：`amv-pct` 原本「涨=绿、跌=红」写反了（CSS `.up` 给 `#55d98a`，而 A 股基线是涨红跌绿）；已改为 `.up → --up-ink`、默认 → `--down-ink`，指数表 0 值不再染跌色。
- 口径：`regime.state` 为空态时用 `--down-bg`（绿底），`多` 用 `--up-bg`（红底），与「涨红跌绿」一致。

### A.10 kline.main（K 线）
- 点击：加载按钮 / 模式切换（分时↔日线）/ 输入回车 均有效；分时视图出 241 点、五档盘口、筹码峰。
- 写操作：无（只读）。
- 刷新：`refreshEvery 20000`，分时模式下重画；9s 采样骨架存活、正文长度恒定（非交易时段数据不动，属正常）。
- **新增**：接收 `ctx.card.args.code`（搜索面板跳过来的代码），裸码不猜市场，默认仍 `600519.SH`。
- canvas：RED/GREEN/BLUE/GOLD/MUTED 五个写死常量（含 `rgba(221,51,51,.10)` 这类）全改为 `palette()` token；浅色主题下黄白线/网格/涨跌色可读（截图见 §B.3）。

### A.11 paper.main（纸面交易）
- 点击：模式切换（限价/市价、买/卖）、数量快捷键、数量锁、条件单展开 均有效。
- 写操作：限价单未填价 → 前端拦「限价单必须填写价格」（不发请求）；切市价后提交 → 弹确认弹层 → 确认 → 服务端 `409 非交易时段`（**当前是周日凌晨，非交易时段属正确行为**，回执原样显示）。下单链路本身通。
- 刷新：`refreshEvery 20000`，9s 采样骨架存活。
- 跨卡跳转：卡内无 `data-open-*`，N/A。

### A.12 ops.main（后台，本轮新增）＋ 跨卡 `.TI`
- 见 C 段。
- **`.TI` 指数码**：在 iframe 执行上下文里注入 `data-open-kline="886108.TI"` 并真实点击 → 父页 `#klineModal` `none→flex`，标题「板块指数 886108.TI」，canvas 17 个。同一路径在 concept / sector / watchlist / review 四个 Tab 各验一次，全部 `flex`。**结论：`.TI` 走 `data-open-kline` 通；不需要按码猜类型**（与 R5 HANDOFF 的判据一致）。

### A.13 跨卡桥的采样纪律（任务书点名的坑）
全部取卡内状态都用 `div.v2-frame iframe` → `contentDocument`，先确认 `iframe.contentDocument.querySelector('.card-host').length === 1` 再继续。本轮另踩了一个同族坑并记下：`postMessage` 的来源校验用 `ev.source === iframe.contentWindow`，在**父页上下文**调 `f.contentWindow.postMessage` 时 `ev.source` 是父页 → 会被正确挡掉；验证跨卡消息必须在 iframe 自己的执行上下文（CDP `contextId`）里点元素。

## B 段 · 主题跟随

### B.1 改了哪些卡

| 卡 | 改前 | 改后 |
|---|---|---|
| amv | 13 处 hex（`#3a2a2a/#ff8a8a/#55d98a/#163a2a/#3a2e16/#f0c36a/#161b26/#8b93a7/#2a3140`） | 0；全走 `--up-*`/`--down-*`/`--warn-*`/`--card2`/`--line`/`--dim` |
| kline | 28 hex + 10 rgba（含 5 个 JS 颜色常量） | 0；`palette()` 从 token 现取 |
| sector | 56 hex + 3 rgba（含 canvas 10 处、买点徽标 15 色） | 0；徽标指向既有 `--p-*`/`--mf-orange` 调色板，不新造色 |
| review | 11 hex + 2 rgba（`--judge-*` 三值与按主题名覆写） | 仅剩 5 处**属性选择器**里的旧页原始色（`span[style*="#c0392b"]` 等，匹配内联样式用，不是渲染色）+ 1 处 `color:#FF6B6B`（旧页逐字搬入、被 test 锁死，见下） |
| ops | 新增卡，0 硬编码 | 全 token |
| 其余 6 卡 | 本轮前已无 hex | 未动 |

### B.2 一个必须先说的约束
`review` 卡有一份 `test_scoped_css_rule_headers_match_legacy`：搬入的 `#panel-review` 作用域规则必须与旧页**逐字一致**（声明归一文本都不想等）。本轮一开始把 10 条规则的背景色换成 token，被这条 test 拦住（`assert not differ`）。判定：**这条 test 守的是「不复制第二份样式口径」的另一面——搬入段不得漂移**，比我做主题跟随更重要。于是回退这 10 条，只改作用域外的 `--judge-*`（那三个值不在 `#panel-review` 作用域内，不受该 test 约束）。已在 HANDOFF 与测试注释里登记为已知例外。

### B.3 实测（九主题 + 三档字号）

逐卡把 `html[data-theme]` 切七套主题 + `data-fs` 切三档，取该卡所有元素的计算色集合：

- **主题确实生效**：13 张卡的卡片底色随主题变化（例：`sector` = `rgb(43,51,66)`(夜航) → `rgb(236,236,239)`(月白) → `rgb(242,231,207)`(漫画) → `rgb(239,232,218)`(暖纸)）。
- **字号三档生效**：13 张卡 `root` 一律 `14px/16px/18px`（sm/md/lg），`body` 同步 `12.25/14/15.75`。
- **注意**：`--theme-manifest` 实际只列 7 套（月白/亚克力/夜航/漫画/暖纸/霜璃/夜璃），任务书写的「九套」与 manifest 不符——按 manifest 唯一源验证 7 套，另加 `frostglass` 用 `[data-theme="frostglass"]` 块直接验。已测 7 套全过；`sector/amv/main` 等卡在漫画主题（3px 硬描边）下边框变粗，属主题设计而非缺陷。
- **视觉核对**：`ops.main` / `kline.main` / `sector.board` / `amv.main` × 夜航/月白/暖纸/漫画 四套共 16 张截图，人工看过：浅色主题下表格文字、坐标轴、涨跌色、警戒胶囊均可读，无「白底白字」。

### B.4 新增守门（防再写死）
- `app/tests/test_fe_shell_bridge.py::test_no_raw_hex_in_any_card`：所有卡片前端禁裸 hex（属性选择器与已登记的旧页搬入例外除外）。
- `test_no_raw_rgba_in_theme_cards`：本轮整改过的四张卡禁裸 rgba。
- `test_canvas_colors_come_from_tokens`：canvas 必须经 `palette()`/`tok()` 现取。
- `test_undefined_tokens_not_used`：禁引用主题表里不存在的 token（此前 `--muted`/`--bg2`/`--panel` 这类历史误写会让整条声明静默失效）。

## C 段 · ops 卡化

### C.1 交付
- 新增 `app/plugins/ops/frontend.js`（340 行）：卡 id `ops.main`、`title 后台`、`order 70`。
- `app/plugins/ops/plugin.json` 新增 `frontend.cards` 声明。
- `app/web/v2_map.json` 的 `ops` 由 `old` 改 `v2`（九个 Tab 全 v2）。
- 新增 `app/plugins/ops/tests/test_ops_frontend.py`（21 例）。

### C.2 内容对齐旧页 `renderOps`（engine/web/index.html:8399）
| 旧页元素 | 本卡 |
|---|---|
| 头信息（更新/最近交易日/今日+时段/清单源） | ✅ 同字段同措辞 |
| 汇总三项（计划任务·常驻·数据源） | ✅ 含 失败/未跑/停用/停/陈旧 细项 |
| 计划任务表 6 列（状态/任务/最后运行/结果码/下次运行/操作） | ✅ 22 行 |
| 常驻进程表 5 列（状态/进程/PID/最后活跃/watchdog） | ✅ 16 行 |
| 数据新鲜度表 4 列（状态/数据源/最新数据日/来源） | ✅ 8 行 |
| 登记册解析失败红卡 | ✅ `meta.registry_ok === false` 时出 |
| 覆盖度告警红卡 | ✅ `tasks_unknown`/`daemons_unknown`/`daemons_unregistered` 三项（实测当前出 1 张：`PanelV2_DailyCheck`、`PanelV2_KeepAlive` 判不了状态） |
| 手动更新按钮 | ✅ 只对 `meta.manual.enabled` 白名单出（实测 2 个：`OH_BoardWeekly`、`OH_PostCloseChain`） |
| 红线标记 | ✅ 5 个红胶囊（1 任务 + 4 常驻），**只显示、不出按钮** |
| 口径脚注 | ✅ 逐句照抄 |

### C.3 写操作只在确认后发生
`手动更新` → `confirm('确认手动更新「XX」？…')` → 取消则**不发任何请求**。实测：`Network.requestWillBeSent` 里 `/api/ops/run` 请求数 **0**，按钮文案/禁用态复原正常。这是刻意的：那个端点会 `schtasks /run` 真跑生产任务，验收绝不能真触发。

### C.4 已验证
- 挂载：8810 与 8821 上 `ops.main` 都出 3 张表（22/16/8 行）、汇总三项、1 张红卡、2 个手动按钮、5 个红线胶囊、0 控制台错误。
- 旧页 Tab：`v2_map.ops="v2"` 后点「后台」→ `div.v2-frame` 1 个、`src=/v2/?card=ops.main`、旧 `panel-ops` 被 `display:none` 压住。
- 主题：七套主题下正文/胶囊/红卡颜色都随主题变（见 B.3）。

## D 段 · 收口

### D.1 「映射改回 old 能回旧界面」
在 8810 上实测三步（真实改文件、真实点、最后还原）：

1. `v2_map.ops = "old"` → 刷新 → 点「后台」：`div.v2-frame` **0** 个、`panel-ops.style.display` 为空、`innerHTML` 16937 字、正文「后台任务监控（只读）· 更新 …」。旧界面完整回来。
2. 同一次会话点「仪表盘」（仍 `v2`）：`div.v2-frame` **1** 个、`src=/v2/?card=dashboard.main`。逐 Tab 独立，互不牵连。
3. 恢复 `ops = "v2"` → 点「后台」→ 卡视图回来。

> 过程中的一个真实事故：用 PowerShell `Set-Content` 改 `v2_map.json` 时写进了 UTF-8 BOM（`git diff` 第一行显示 `﻿{`）。已用 `UTF8Encoding($false)` 重写并复核 `git diff` 只剩目标那一行。此坑记在此处：改 JSON 一律走 `write`/`edit`，不用 shell 重定向。

### D.2 `docs/STATUS.md`
已更新到当前状态（见该文件顶部「2026-10-25 前端冲刺」段）。

## 本轮修掉的 4 处真缺陷（都不是「风格问题」）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 仪表盘「执行器」徽标点了没反应 | 卡片发 `CustomEvent('panel:open-card')`，shell 从未监听（只在 console 留「无监听方」） | `shell.js` 加 `bindCardJump()` 真监听 + `preventDefault` 回执 |
| 2 | 自选池个股名点了没反应 | 只有 `data-role`（走卡内 CustomEvent，无监听方），缺跨窗口桥认的 `data-open-kline` | 补占位属性，`data-role` 保留不动原有处理器 |
| 3 | 复盘卡「个股名可点」名不副实 | 注释声称有 `data-kname` 占位，实际未实现 | 补 `wireTableKNames()`，照旧页 `kTsPlayable` 口径 |
| 4 | 选股表个股名不可点 | 行内只有 `data-code`，无跳转占位 | 补 `data-open-kline` + `.sel-nm.is-link` 视觉态 |

另修 1 处语义错误：`amv` 卡涨跌配色写反（涨给绿、跌给红），已按「涨红跌绿」基线改为 token。

## 5. 范围外发现的异常（一行记录，未展开）

`app/plugins/compat_misc/backend.py`（改动时间 02:07-02:08，**不是本单产物**）引入顶层 `import os` 并改了选股板块候选过滤，导致其自带的两条测试失败：

- `test_compat_misc.py::test_no_http_or_file_write`：测试禁后端顶层 `import os`，新改动加了。
- `test_compat_misc.py::test_selection_board_options`：断言 catalog 含 `CONCEPT` 层，新逻辑把非自建板块过滤后只剩 `L3`。

这两条属该工单的测试与实现不同步，**不在本单白名单（backend.py 禁改）**，按纪律只记录不排查、不修。它不影响本单交付：排除 `compat` / `compat_misc` 两个目录后全量 1203 passed。

另：`app/plugins/compat/backend.py` 同期被改成动态加载 `ths_board_pool.is_custom_code`，与本单无关，未触碰。

## 6. 未验证 / 已知边界

1. **拖拽**（自选池改序/换组）：headless 无法真跑 HTML5 drag，只确认事件接线存在。需真机点一次。
2. **盘中实时**：本轮验收在周日凌晨，行情是 T-1/冻结态；`refreshEvery` 各卡都验了「不闪、不丢焦点」，但**数值真跳动**要等周一盘中。
3. **纸面下单成交**：服务端 `409 非交易时段`（正确行为），下单→委托→成交→账户变动全链路未在交易时段验。
4. **红线任务真触发**：刻意未验（会跑生产任务），只验了「红线不出按钮」与「取消后不发请求」。
5. **`data-fs` 与漫画主题**：漫画主题 3px 描边在 sm 档略挤，属主题设计取舍，未改。
6. **review 的 5 处旧页原始色**：受「搬入段与旧页逐字一致」test 约束，保留原样（见 B.2）。

---

## 7. 验收补正（01 独立复验，2026-10-25 02:4x–02:5x）

本单交付后我按**用户真实入口**（从 8810 旧界面首页进入，而不是直接改 iframe 的 `data-theme`）复验，发现两处未被本单覆盖的真缺陷，已修完并回归。

### 7.1 主题在真实入口完全不跟（P1）

- **现象**：在 8810 首页用旧界面主题下拉切到暖纸 → 父页变暖纸，**卡内仍是夜航**（`body` 背景恒为 `rgb(26,32,41)`）；重载也不跟。
- **根因**：**键名两套**。旧界面（`engine/web/index.html:11358`）写 `panel_theme` / `panel_fs`；v2 壳（`app/web/shell.js`）读 `shell_theme` / `shell_fs`。同源同 localStorage 但互不相通。实测 `ls_panel=warmpaper`、`ls_shell=null`。
- **本单为何未发现**：B.3 验的是「直接改 iframe 的 `html[data-theme]`，CSS 能不能吃 token」——验的是样式层，不是用户路径。
- **修法**（两处都必要）：
  1. `shell.js` 同时读写两套键（`LS_THEMES = ['panel_theme','shell_theme']`，旧界面键优先），写入用 `lsSetAll`。
  2. `v2bridge.js` 用 `MutationObserver` 观察父页 `data-theme`/`data-fs`，变化时向自己挂的 iframe 推 `hana:theme` 消息；`shell.js` 新增 `bindThemeFromParent()` 接收。**不改旧仓 `index.html`**（沿用 v2bridge 的零改动纪律）。
- **验证**（8810 活体，headless + CDP 关缓存）：
  - 只留 `panel_theme=warmpaper` / `panel_fs=lg` 重载 → 父页 `warmpaper`、iframe `warmpaper`、`body` 背景 `rgb(245,239,228)`。
  - 运行中连切三套（月白/夜航/漫画）→ iframe 与父页逐套同值，`body` 背景分别 `rgb(245,245,247)` / `rgb(26,32,41)` / `rgb(253,246,227)`。
  - 字号三档循环 → iframe `data-fs` 与父页同步，`root` 字号 `14px / 16px / 18px`。

### 7.2 卡间跳转在真实入口仍是死的（P1）

- **现象**：仪表盘「执行器」徽标点下去仍不动，只弹一句「未装载卡片：battle_plan」。
- **根因**：`shell.js` 的 `boot()` 在 `?card=` 时把 `state.cards` 过滤成只剩 1 张；`bindCardJump` / `selectCard` / `findCardByPlugin` 都拿这份过滤后的清单查表。而 **v2bridge 嵌旧页用的正是 `/v2/?card=xxx` 单卡模式**。
- **本单为何自报成功**：A.1 验的是 `/v2/` 全壳（`state.cards` 是全部 12 张），那条路径本来就通；§A.12 的「通」也是同一路径。
- **修法**：新增 `state.allCards`（全量，不受 `?card=` 影响），`bindCardJump` / `selectCard` / `findCardByPlugin` 改查它；`state.cards` 只决定侧栏渲染什么，不决定「能挂什么」。
- **验证**：`/v2/?card=dashboard.main`（真实入口形态）派 `panel:open-card{pluginId:'battle_plan'}` → 卡内容从「情绪门…」变为「作战表 纸面 签收…」，`preventDefault=true`，无 toast；`/v2/` 全壳同参数结果一致。

### 7.3 回归

- 九个 Tab 逐个实点：全部 v2、13 张卡、零错误卡、主题继承一致（`comic`）。
- `pytest app/tests/test_fe_shell_bridge.py`：**20 passed**（原 18 + 本轮新增 4；其中 2 条旧断言随实现调整同步更新）。
- 全量 `pytest -q`：**1370 passed / 2 failed / 4 skipped**；2 红仍是 `compat_misc`（自建板块工单，不在本单范围）。

### 7.4 本轮改动的文件

```
M app/web/shell.js      两套 localStorage 键兼容 + allCards 保跳转 + 主题消息接收 + 推送函数
M app/web/v2bridge.js   MutationObserver 观察父页主题 + iframe load 后补推
M app/tests/test_fe_shell_bridge.py   +4 例（键名双读 / 消息通道两端 / allCards 查表）
```

## 改动清单

```
M app/plugins/amv/frontend.js        涨跌语义修正 + 全量 token 化（13 hex → 0）
M app/plugins/kline/frontend.js      palette()/tok() + ctx.card.args 入参（28 hex + 10 rgba → 0）
M app/plugins/sector/frontend.js     表格/canvas/徽标 token 化（56 hex + 3 rgba → 0）
M app/plugins/review/frontend.js     wireTableKNames() + --judge-* 换 token；搬入段保持逐字
M app/plugins/selection/frontend.js  行内个股名补 data-open-kline
M app/plugins/watchlist/frontend.js  个股名/板块名/板块芯片补 data-open-*
M app/plugins/ops/plugin.json        新增 frontend.cards（ops.main, order 70）
M app/web/shell.js                   ctx.card.args 一次性入参 + panel:open-card 监听 + 搜索带码开 K 线
M app/web/v2_map.json                ops: old → v2
+ app/plugins/ops/frontend.js        新卡（340 行）
+ app/plugins/ops/tests/test_ops_frontend.py   21 例
+ app/tests/test_fe_shell_bridge.py   18 例（桥/入参/占位/主题/映射）
```

> 注：`git status` 里另有 `app/plugins/compat/backend.py`、`compat_misc/backend.py` 两个改动，**不是本单产物**（自建板块码判据），未触碰。

## 回退

单文件级回退即可，互不依赖：

```
git checkout -- app/web/shell.js app/web/v2_map.json \
  app/plugins/amv/frontend.js app/plugins/kline/frontend.js app/plugins/sector/frontend.js \
  app/plugins/review/frontend.js app/plugins/selection/frontend.js app/plugins/watchlist/frontend.js \
  app/plugins/ops/plugin.json
git clean -f app/plugins/ops/frontend.js app/plugins/ops/tests/test_ops_frontend.py app/tests/test_fe_shell_bridge.py
```

- 只想退回旧「后台」Tab：`v2_map.json` 里 `ops` 改回 `"old"`，刷新即回旧界面（D.1 已实测）。
- 只想撤主题改动：上面四个 frontend.js 单独 checkout，卡功能不受影响（只回到写死颜色）。
- 撤 `panel:open-card` 监听：删 `shell.js` 的 `bindCardJump()` 定义与调用 + `selectCard` 的 args 参数（回到「跳转静默失败」）。
