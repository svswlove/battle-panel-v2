# 盘中照旧页搬 · 第二批（三小件 + 可选第四件）交接

2026-10-25 · 01 · 任务书「盘中第二批：信号流水 + 会话行 + 成交额对比，可选实时行情 KPI」
前置：`docs/HANDOFF_20261025_intraday_research.md`（C2a 研究卡，已验收）

## 一、结论

四件**全部做完并逐字节对齐旧页**（含可选的第四件实时行情 KPI）。

| # | 块 | 旧页基准 | 对齐结果 |
|---|---|---|---|
| 1 | 信号流水（含 details 折叠壳 + 同壳另 4 表） | 4718-4731 | 5 张卡 **逐字节相同**；49 行 × 8 列 = **392 格逐字相同** |
| 2 | 会话行 | 4707 | 整行 **逐字节相同**（含「冻结:原因」分支） |
| 3 | 成交额对比 | 4694-4699 + 5170-5173 + 4702 | 3 行 KV + 2 条 src **逐字节相同**（含「无数据」分支） |
| 4 | 实时行情 KPI（可选） | 4677-4685 + 5119-5146 | 10 卡 **逐卡逐字节相同**；spark **首绘即满**（空 d：旧页 20 / 卡片 0） |

v2 盘中卡新增 **5700（信号流水，默认折叠不计屏）+ 约 70（会话行）+ 112（成交额对比）+ 621（行情 KPI）**，
与 `docs/AUDIT_20261025_intraday_gap.md` 的缺口口径一致。

**明确未做（按任务书/审计结论）**：成交额分时图（`_turnoverChart` 28 行 SVG，只值 16 字，审计原话「图缓做」）。
涨跌家数卡（旧页 4700）也没重画——v2 `breadthHtml` 已有同源块，重画等于造第二份真相。

## 二、文件结构（白名单内）

| 文件 | 内容 | 行数 |
|---|---|---|
| `app/plugins/intraday/frontend/audit.js` | 会话行 + 成交额对比 + 审计明细 5 表 + M 映射（9 张子表）/cn/cnSigSource/kCode/table | 341 |
| `app/plugins/intraday/frontend/audit.css.js` | 旧页 962-978 / 1358 / 1544 / 1558-1661 样式 + `.it-card` 作用域 | 102 |
| `app/plugins/intraday/frontend/quotes.js` | 实时行情 KPI 10 卡 + 别名表 + spark 码表（**先取数再渲**） | 78 |
| `app/plugins/intraday/frontend/quotes.css.js` | 旧页 961-969 / 1663-1667 样式 + 作用域 | 72 |
| `app/plugins/intraday/frontend.js` | 仅编排：import / 3 个新容器 / spark 取数 / render 三行 / unmount | +88 行（719 → 807） |
| `app/plugins/intraday/tests/test_audit.py` | 17 条守门测试 | 397 |
| `app/plugins/intraday/tests/test_quotes.py` | 7 条守门测试 | 182 |
| `app/plugins/intraday/tests/audit_align.mjs` | 旧页对齐运行器（node:vm **跑旧页原文行**，不重抄） | 241 |
| `app/plugins/intraday/tests/audit_dom.mjs` | 真 DOM 运行器（jsdom：折叠/重绘/整卡挂载/块序） | 220 |
| `app/plugins/intraday/tests/quotes_align.mjs` | 行情卡对齐运行器（旧页回填完成态 vs 卡片首绘） | 153 |
| `app/plugins/intraday/tests/fixtures/intraday_payload.json` | 真实 `/api/intraday.json` 快照（101,935 B，signals 49 / market_quotes 10） | — |

未动：`frontend/sector.js`、`frontend/caliber.js`、`frontend/format.js`、`frontend/card.css.js`、
`frontend/research.js`、`frontend/research.css.js`、`backend.py`、`plugin.json`、`app/web/*`、
其它插件、`app/core/`、`engine/`、`data/panel/*`。
未做任何 git 写操作。8810 未重启（静态文件改动，浏览器硬刷新即生效）。

## 三、四条约束的落实

### 1. 每实例 AbortController（模块级状态 → 每实例）

本组在旧页**没有模块级可变状态**——会话行/成交额/5 张表全是 `renderIntraday` 里的一次性内联表达式，
唯一的模块级是 `_breadth880` 读缓存，那属于广度块（v2 已由 `frontend.js` 的 `S.breadth` 持有）。
故本模块的实例壳只有 4 个状态位：

| 位 | 作用 |
|---|---|
| `st.alive` | 卸载后不再写 DOM |
| `st.body` | 重绘容器（frontend.js mount 时建的 `.it-audit-host`） |
| `st.open` | details 上次的展开态（重绘后还原，见第四节偏离 c） |
| `st.ac = new AbortController()` | 监听句柄；`unbind()` 一次 abort 全解 |

行情块同款：`st.spark / st.sparkAt` 用来做 20s 轻缓存（旧页 5135 同口径），无定时器、无模块级。

### 2. document.getElementById → 容器内查询

本组旧页**本来就没有 #id 查询**（除了成交额卡的 `id="cur880Amt"` 供 `_br880Paint()` 事后回填）。
两处相应处理：

- `audit.paint()` 只在 `st.body` 内 `querySelector('details.fold')`（记展开态），不查全文档；
- `id="cur880Amt"` **去掉**：卡片是「先取数再渲」，金额与对比行同一拍渲染，不需要事后按 id 回填。
  留着 id 只会给「容器内查询」这条约束留后门（见第四节偏离 b）。

### 3. 浮层 / 子节点挂容器内

本组与行情组在旧页都**没有浮层**、没有越界 DOM。新增的全部节点都在 `.it-card` 内部。
旧页 `.kpi` 的 `clip-path: var(--shape-clip)` 若照抄会裁掉浮层；本单没有浮层，
故只保留底色/圆角/内边距，不搬边框与裁切（见第四节偏离 d）。

### 4. 内联 onclick → data-act 委托

本组旧页**本来就没有 onclick**：

- 信号流水/快照的代码列走的就是 `kCode()` 的 `data-open-kline` 占位（旧页 2920 已如此）；
- 个股名走 `kName()` 的 `data-kname`（`research.js` 已搬，本模块**直接 import 复用**，不重写第二份）；
- details 的展开/收起是浏览器原生行为，旧页也**没写过一行 JS**，卡片照旧不绑监听。

行情卡的名字列同样走 `kCode`（实测 10 卡全带 `data-open-kline`，见 `quotes_align.mjs` 的 `kCode` 项）。

## 四、偏离清单（每一条都不改语义）

| # | 旧页 | 卡片 | 理由 |
|---|---|---|---|
| a | `card()` 产出 `<div class="card"><h2>…` | `<div class="it-audit-card"><h2>…` | 卡内嵌套全局 `.card` 会套上壳的阴影/裁切，视觉上像卡里长了个卡。结构不变，只换类名 |
| b | `<span class="to-val" id="cur880Amt">` + `_br880Paint()` 事后回填 | 去掉 id，同一拍渲染 | 卡片先取数再渲；留着 id 等于给「容器内查询」留后门 |
| c | 每 3s 整页重建 innerHTML，用户展开的 details 每拍被弹回 | ① 内容签名不变 → 整块不碰 DOM；② 真变了才重建，且重读/还原 open | 首绘**仍默认折叠**（无 open 属性 = 旧页语义），只是不再打断用户 |
| d | `.kpi` 带边框/阴影/backdrop-filter/clip-path | 只留底色/圆角/内边距 | 同 a：卡里再套一张「带阴影的卡」是视觉重复 |
| e | `.grid` 的 `minmax(240px,1fr)` | `minmax(150px,1fr)` | 240px 是整页宽度下的值，卡内窄屏一列都放不下。写法（`repeat(auto-fill,…)`）不变 |
| f | 全A 行的 `<span class="regime-mark" data-regime>`（由 `loadAmvLive` 填多空标记） | **不产出该占位** | 要多取 `amv_regime_latest / battle_plan / real_positions` 三个端点；留个空 span 等于给用户一个永远不亮的灯 |
| g | **spark 先渲空 path、异步回填 d**（`_sparkInject` 5119-5128） | **spark 与行情同一拍取数、渲染时直接写 d** | 任务书点名。卡片的 `setHtml` 签名缓存会把回填冲掉且没有第二次机会 → 背景线必然闪没 |
| h | 成交额分时图（`_turnoverChart` 28 行 SVG） | **不做** | 审计结论：图只值 16 字，28 行 SVG 不划算，「图缓做」 |
| i | 涨跌家数卡（旧页 4700 `_br880CardHtml`） | **不重画** | v2 `breadthHtml` 已是同源同口径的完整实现，重画 = 第二份真相 |

颜色：旧页这段本来就全是 `var(--*)`，**零裸 hex、零自拼 rgba**，无需换色。
`.k-nolink{opacity:0.75}` 是旧页原样，不是 rgba。

## 五、对齐证据（不是「看起来对」，是同一个 vm 里跑出来的）

做法与 C2a 一致：`node:vm` 直接执行**旧页文件里按行号切出来的原文**（`audit_align.mjs` 的 `seg(a,b)`），
不重抄旧页代码——抄一遍就多一份走样的可能。负载 = 真实 `/api/intraday.json`（signals 49 / market_quotes 10）。

### 5.1 中文映射表（旧页 2962-3004）

| 子表 | 键数 | 结果 |
|---|---|---|
| state | 8 | 逐键逐值相同 |
| phase | 7 | 同上 |
| regime | 4 | 同上 |
| sigStatus | 8 | 同上 |
| direction2 | 3 | 同上 |
| channel | 2 | 同上 |
| src2 | 5 | 同上 |
| sigKind | 10 | 同上 |
| sigSource | 11 | 同上 |

`cn / cnSigSource` 的边界一并比了：`null/undefined → —`、空串 → `String('')`（旧页 3005 同口径，**不是** —）、
表外 → 原样透传、`SW-/BP-` 业务单号不翻译。

### 5.2 会话行（旧页 4707）

    旧页：盘中会话 2026-10-23 · 已收盘 · MONITORING ｜ 信号 49 · 触发 49 · 执行 22 · 拦截 0
    卡片：盘中会话 2026-10-23 · 已收盘 · MONITORING ｜ 信号 49 · 触发 49 · 执行 22 · 拦截 0
    冻结分支：… MONITORING（冻结:急停） ｜ 信号 49 · …      ← 两侧同样相同

### 5.3 成交额对比（旧页 4694-4699 + 4702）

3 行 KV + 2 条 src **逐字节相同**：

    当前累计 880005        1.97 万亿
    昨日同时刻             1.53 万亿 +27.97%
    预估今日全天 (估算)     1.96 万亿 100%
    qmt:tick
    预估=今日累计÷w(t)（量能剖面近20日）；昨日同刻=昨日1m真值 ｜ 2026-10-23 15:30:00

「非 1m 真值」分支（把 `prev_time_source` 改成 `estimate`）两侧都是「无数据」——估算值不充真值。

### 5.4 审计明细 5 张表（旧页 4719-4730）

| 项 | 旧页 | 卡片 |
|---|---|---|
| 卡数 | 5 | 5（逐张 HTML 逐字节相同） |
| `<tr>` 总数 | 50 | 50（1 表头 + 49 信号） |
| 空表「（空）」 | 4 | 4（真实载荷只有 signals 有数据） |
| 单元格 | 392 | **392 逐字相同**（49 行 × 8 列，firstDiff = -1） |
| 外层 details | `<details class="fold">`，无 open | 相同（无 open） |

summary 文案两侧一字不差：`审计明细（信号流水 / 判强判弱 / 环境系数 / 盘中事件 / 通道B）`。

### 5.5 实时行情 KPI（旧页 4677-4685）

| 项 | 旧页 | 卡片 |
|---|---|---|
| 小卡数 | 10 | 10（逐卡 HTML 逐字节相同，名字列都带 `data-open-kline`） |
| 背景线路径 | 20 条 | 20 条 |
| 首绘空 `d=""` | **20** | **0** ← 本单的改动目的 |
| 别名表 | `{880008.SH: ALL}` | 相同 |
| spark 请求码 | 10（去重保序） | 相同（`ALL,000001.SH,…,399303.SZ`） |
| 空行情降级文案 | 采集中…（QMT 不可达…） | 逐字节相同 |

比对方式：把旧页整块里 20 个空 `d` 按同一份桩数据灌满（= `_sparkInject` 回填完成态）再与卡片**首绘**比。
即：卡片首绘 === 旧页回填完成后。旧页原始渲出的空 `d` 数（20）另做基线断言，防止有人把回填机制又搬回来。

## 六、测试（新增 24 条：17 + 7）

| 文件 | 条数 | 守什么 |
|---|---|---|
| `tests/test_audit.py` | 17 | 静态 6（无 DOM/网络捷径、无模块级状态、零裸 hex/rgba、CSS 全作用域+token、frontend.js 接入点、新文件落地）；映射与工具 3（9 张子表逐键、cn/cnSigSource 边界、table/kCode）；会话行 2（字段全集 + 逐字节）；成交额 2（3 行 KV + 逐字节含无数据分支）；5 表 3（列结构 8/7/5/5/5、默认折叠与 summary、逐字节 + 392 格）；DOM 1（jsdom） |
| `tests/test_quotes.py` | 7 | 静态 3（四条约束 + CSS 作用域/token + 编排接入且 spark 必须先取完再 render、禁 `_sparkInject` 与 `setAttribute('d'`）；对齐 3（10 卡逐字节 + 空 d 基线 20 vs 0 + DOM 首绘 20 条 path 全满） |

### 6.1 真 DOM（jsdom）实测

| 项 | 结果 |
|---|---|
| 首绘（无数据） | 有 details、`open=false`、**无 open 属性**、5 张表「（空）」 |
| 有数据首绘 | 仍折叠；50 `<tr>` / 392 `<td>` / 98 个 `data-open-kline` / 4 个空表 |
| 同内容重绘（20s 定时刷新最常见情况） | **不碰 DOM**（节点同一个），展开态原样 |
| 内容真变（49 → 3 条信号） | 换节点、行数变 4、**展开态还原为 true** |
| 再变回去 | 展开态仍是 true（跨多次重建不丢） |
| 整卡挂载（真 frontend.js + 真载荷） | intraday.json 取 1 次；10 个行情卡 / 20 条 spark path **全满**；3 行 KV（1.97 万亿）；392 `<td>`；5 份 CSS 全注入 |
| 块序 | 会话行(3) < 行情(4) < 成交额(7) < 轮动(8) < 研究(9) < 审计明细(12) < 来源行(13)；会话行前面就是冰点卡 |
| refresh 一拍 | 审计明细仍在（392 `<td>`），会话行文本不变 |
| unmount | 卡片节点清空（`.it-audit-host` 0 个） |

### 6.2 与已有测试的兼容

`test_intraday.py` 的 15 条断言逐条复核过（refreshEvery / 5 个端点 / 无裸 fetch / TRACK_TTL 字面值且只取一次 /
ICE_THRESHOLD=10 / 100−q_limit_down_count / research 接入点唯一 / 不含 `setHtml(S.el.secResearch` / 8 个 theme token 全在），
全部仍成立；`test_research.py` 未触及。

## 七、验证环境限制（必须说明，未跳过）

**本机沙箱禁止子进程**：`spawn` / `spawnSync` 一律 `EPERM`（`node --version` / `python --version` 实测均被拒）。
因此 `pytest app/tests app/plugins/intraday/tests -q` **未能由我执行**，需 01 复跑（基线 205 passed / 3 skipped，
本单 +24 条，预期 **229 passed / 3 skipped**；其中 3 条 heavy 用例（旧页对齐 / jsdom）在缺 `engine/` 或 `jsdom` 时会跳过）。

**替代做法（不是跳过）**：所有 node 载荷在我的进程内真实执行过——

- `audit_align.mjs` / `quotes_align.mjs` / `audit_dom.mjs` 三个运行器全部跑通，结果见第五节与 6.1；
- `test_audit.py` / `test_quotes.py` 里每个 `_expr()` 载荷按 Python 非 raw 字符串语义反转义后在进程内执行过，
  断言值逐条核对（发现并修正了两处我自己写错的期望：空会话的 `String('')` 与 `cn('', → '')`）；
- `app/tests` 的三类静态门禁（裸 hex / 未定义 token / 非相对导入）在本进程内按同一正则扫了 **27 个卡片前端文件**，
  intraday 5 个文件全部 0 违规（唯一命中是 `review` 的 `#FF6B6B`，属已有白名单）；
  `test_intraday.py` 的 `BANNED_JS` / 全局写 / 无裸 fetch 三组也逐文件扫过，0 命中。

## 八、已知边界 / 未验证

1. **盘中实时跳动未验**：验收时是周日，`phase=CLOSED`、载荷是 2026-10-23 收盘口径。
   信号增量时 `audit.paint` 走「内容变了 → 重建 + 还原展开态」那条路径，已在 jsdom 里用 49→3→49 验过；
   但**真实盘中每 20s 增量**的观感要等开市（与第一、二步同一处边界）。
2. **spark 端点真实往返未验**：8810 上 `/api/sector_spark.json?level=INDEX&codes=…` 实测 200 / 4499 字节、
   10 个码全有 `amv` / `ma10`，但**卡片里这条链路**用的是 jsdom 桩（同形状）。
   真实浏览器里 spark 与 intraday 同一拍是否总能在超时前返回，需开市看一次。
3. **`data-open-kline` 只验属性，未验点击**：真实处理在 `shell.js`（捕获阶段委托 → `v2:openKline`），属 B 单范围。
   本卡按第一步同款处理，属性齐全（行情 10 + 信号流水 98 + 快照若干）。
4. **窄屏未验**：`.grid` 的 `minmax(150px,1fr)` 是按「卡内宽度」估的，没有真浏览器量过；
   `audit.css.js` 给审计块加了 `overflow-x:auto`（旧页 975 同款意图），但 8 列表在 420px 下是否可读需目视。
5. **`regime-mark` 未接**（偏离 f）：全A 行情行不显示「多/空」小标记。
   要接需再加 3 个端点（`amv_regime_latest` / `battle_plan` / `real_positions`），
   `_regimeTitle` 的 6 段 tooltip 也要一并搬——属「新块」不属「照抄」，建议单独一单。
6. **`test_intraday.py` 未改**：`test_frontend_submodules_exist` 的白名单仍只列第一步的 4 个子模块，
   本单新增的 4 个（`audit.js` / `audit.css.js` / `quotes.js` / `quotes.css.js`）由
   `test_audit.py` / `test_quotes.py` 守门。与 C2a 同一处理（有意不碰已验收测试文件）；
   若 01 希望统一，在该断言里追加 4 个文件名即可。
7. **成交额分时图未做**（偏离 h）：`_turnoverChart` 的 28 行 SVG 一行未搬。若以后要做，
   注意审计记录的两个坑：X 轴固定 09:30–15:00、盘中要按窗口过滤点（旧页 2026-10-14 踩过「窗外点挤满两端」）。

## 九、改动清单

    M  app/plugins/intraday/frontend.js                     +88 行（719 → 807，仅编排接入）
    A  app/plugins/intraday/frontend/audit.js                341 行
    A  app/plugins/intraday/frontend/audit.css.js            102 行
    A  app/plugins/intraday/frontend/quotes.js                78 行
    A  app/plugins/intraday/frontend/quotes.css.js            72 行
    A  app/plugins/intraday/tests/test_audit.py              397 行（13 条）
    A  app/plugins/intraday/tests/test_quotes.py             182 行（6 条）
    A  app/plugins/intraday/tests/audit_align.mjs            241 行
    A  app/plugins/intraday/tests/audit_dom.mjs              220 行
    A  app/plugins/intraday/tests/quotes_align.mjs           153 行
    A  app/plugins/intraday/tests/fixtures/intraday_payload.json  101,935 B（真实载荷快照）
    A  docs/HANDOFF_20261025_intraday_audit.md               （本文件）
    A  docs/APPEND_20261025_file_log_c2b.md                  （待 01 搬的文件修改日志正文）

未动：`frontend/sector.js`、`frontend/caliber.js`、`frontend/format.js`、`frontend/card.css.js`、
`frontend/research.js`、`frontend/research.css.js`、`backend.py`、`plugin.json`、`app/web/*`、
其它插件、`app/core/`、`engine/`、`data/panel/*`。
未做任何 git 写操作。8810 未重启。

## 十、下一步建议

1. 01 复跑 `pytest app/tests app/plugins/intraday/tests -q`（预期 229 passed / 3 skipped）。
2. 浏览器硬刷新看四块：审计明细**默认折叠**是否合意（不满意就把 `audit.js` 的 `<details>` 换 `<details open>`，
   一处改动）、行情卡背景线是否清晰、成交额对比三行在窄屏是否换行难看。
3. 开市后验三件事：盘中信号增量时展开态不闪、spark 与行情同一拍不闪、`sess-line` 的 phase/state 随盘中切换。
4. `regime-mark`（多/空标记）是否单开一单，需 01 拍板（要加 3 个端点）。
