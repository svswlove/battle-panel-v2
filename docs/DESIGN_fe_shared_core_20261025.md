# 前端共用层设计（2026-10-25）

## 0. 先纠正一个错误结论

01 先前报的「142 个共用核心函数 / 1969 行，仪表盘/作战表/自选池各自 1969 行共用」**是错的**。

**错因**：闭包分析把模板串里的函数名当成了真实调用。旧页大量用模板串生成
`onclick="_fundView='live';loadTab('dashboard')"`（`legacy/index.html:3330`），
而 `loadTab` 内部有 `fns={dashboard:renderDashboard, ..., selection:renderSelection}`
（`legacy/index.html:8173-8176`），于是任何一个 Tab 的闭包都会把全部 7 个 render 拉进来。
**反证**：仪表盘、作战表、自选池的「共用行数」精确等于同一个 1969 —— 那是同一全集算了三遍。

**修正方法**：剔除引号/模板串内的函数名，并把 `loadTab`/`activate` 这类分发器列入黑名单。

**修正后真实数字**（`Hubagent/01/_vis/shared_core_fixed.py` 可复现）：

| Tab | 闭包函数 | 闭包行数 | ≥3Tab 共用 | 1Tab 专属 |
|---|---|---|---|---|
| 仪表盘 | 23 | 516 | 14 | 490 |
| 作战表 | 17 | 332 | 14 | 306 |
| 盘中 | 84 | 1171 | 14 | 1089 |
| 板块 | 3 | 42 | 0 | 42 |
| 概念 | 22 | 282 | 0 | 214 |
| 自选池 | 12 | 219 | 0 | 219 |
| 选股 | 10 | 189 | 0 | 189 |
| 后台 | 2 | 57 | 0 | 57 |

- 被引用函数合计 **154 个 / 2700 行**
- **≥3 Tab 共用：2 个函数 / 14 行**
- 2 Tab 共用：15 个 / 80 行
- **1 Tab 专属：137 个 / 2606 行**

## 1. 结论

**旧页的 Tab 之间基本是独立的。** 「逐 Tab 做成插件卡」在代码结构上成立，
不存在「共用核心会被复制四份」的问题（那是 01 的错误判断）。

但**盘中是例外**：84 个函数 / 1171 行，其中 `_st*`（板块轮动，20 个）、
`_evo*`（日内演化，55 个）、`_amv*`（活跃市值，11 个）是一整套口径层。
夏天判断：**这 86 个的正确名字是「盘中口径层」，不是「全站共用核心」。**

## 2. 五个模块的切分（按口径分，不按行数分）

| 模块 | 职责 | 约行数 | 用它的 Tab |
|---|---|---|---|
| `format.js` | 16 个格式化工具（数字/百分比/亿元/日期/涨跌色） | 180 | 9/9 全部 |
| `symbol.js` | 6 个标的标识（代码归一/市场判定/带后缀/板块层级） | 75 | 7 |
| `position.js` | 持仓盈亏/距止损分级/三层展示 | 150 | 3 |
| `sector_amv_data.js` | 板块与 AMV 取数 + store + onChange | 210 | 盘中 |
| `sector_amv_view.js` | 板块与 AMV 渲染（网格/四象/演化图） | 600 | 盘中 |

合计约 **1215 行**。

**依赖方向（无环）**：
```
format ← symbol ← position
format ← sector_amv_data ← sector_amv_view
```

**现有代码有 3 个环**，成因函数是 `loadSectorTrack`、`loadAmvLive`、`_evoBfReload`——
数据层里直接调 `_stPaint()` / `_evoPaint()`，回头又读数据层状态。
**拆法**：数据层只提供 `load()` + `store` + `onChange`；绘制由视图层或卡片的 `refresh()` 发起。

## 3. 迁移顺序

1. **`format.js` 先抽**：9/9 Tab 引用、纯函数、零状态零 DOM。
   验收标准：同一份输入 JSON，新旧实现输出**逐字节相同**。
2. **`symbol.js`**：7 个 Tab 引用，同样纯函数，同上验收。
3. **`sector_amv_data.js` + `sector_amv_view.js`**：盘中专用，先解 3 个环再搬。
4. **`position.js`**：最后，因为它涉及下面第 5 节的口径冲突，需先定阈值。

## 4. 三类踩坑风险（都在 142 项里，逐条明细见附录）

| 风险 | 数量 | 说明 |
|---|---|---|
| 依赖旧页全局可变状态 | 72 | 日内演化 28、板块轮动 15、活跃市值 11 最多 |
| 依赖 DOM id 直取（`getElementById`） | 34 | 抽取后必须改成作用域内查询（`el.querySelector`） |
| 内联 `onclick="fn()"` | 12 | 抽取后必须改成 `addEventListener` |

**最阴的一条**：`_evoDelegated` / `_stDelegated` 是一次性布尔，用于「事件只绑一次」。
若沿用模块级变量，**第二张卡挂载时事件永远绑不上，且不报错**。必须改成每实例状态。

## 5. 必须主人拍板的两处口径冲突（不是代码重复，是两个值）

| 口径 | 三份实现 | 冲突 |
|---|---|---|
| Δ占比‰ | `_evoContrib` / `_stGridHtml` 内联 `shareAt` / `_stVal` 的 contrib 分支 | 三处各算一遍 |
| **距止损分级** | 仪表盘 `<10% 黄 / <5% 红`；追踪池 `<1% 黄`；持仓实时态不分级 | **同一指标两套阈值** |
| 持仓盈亏% | `ledgerPosHtml` / `loadPnl` / `posThreeLayerHtml` | 三处各算一遍 |

第二行是**口径冲突**：同一指标两套阈值，抽进 `position.js` 前必须二选一，
或写成两个具名常量，**不能留两个魔数**。

## 6. 另两个需要拍板的问题

1. **旧页何时停改**：`/` 仍是 `engine/web/index.html`。v2 卡与旧页并存期间，
   共用模块只能保证「v2 内部一份」，旧页那份仍在动。
2. **追踪池那 12 个函数**（`pool*` 家族）：`pool_track` Tab 已于 2026-10-15 摘除，
   这 12 个是死代码，直接归档还是留着？

## 7. 对「数据源插件化」的意义

这份设计解决的是**前端口径**，与后端数据源是两件事：

- **后端数据源**：`core/quotes.py` + `core/feed.py` 是唯一取数门面，18 个插件共用；
  实测只有 1 处越界（`compat_kline/backend.py:742` 的 `common.trading_calendar`）。
- **后端指标口径**：真正的问题在 `docs/AUDIT_metric_duplication_20261025.md`
  （7 组多实现，4 组算法不一致）。
- **前端口径**：本文档。共用层抽完后，同一指标在前端也只有一份格式化/判定逻辑。

三者合起来，才是「同一口径、同一数据源」的完整形态。

## 附录：分析脚本

- `Hubagent/01/_vis/shared_core.py`：第一版（有缺陷，保留作为反例）
- `Hubagent/01/_vis/shared_core_fixed.py`：修正版（剔除字符串引用 + 分发器黑名单）
- `Hubagent/01/_vis/shared_core_fixed.json`：修正版明细（含每个函数被几个 Tab 引用）

---

# 第二部分：逐函数清单（夏天提供，01 已抽查行号）

共 **116 个符号**（109 个活函数 + 2 个死代码并入 + 1 个新增 + 4 个常量簇）。

## 2.1 `fe/format.js` — 16 个符号

| # | 名字 | 行 | 形态 | 备注 |
|---|---|---|---|---|
| 1 | `$` | 2783 | const 箭头 | `document.querySelector` 别名；唯一非格式化项 |
| 2 | `esc` | 2784 | const 箭头 | HTML 转义（含单引号），与 `esc2`(2522) 不是同一个，**不要合并** |
| 3 | `num` | 2785 | const 箭头 | `null/undefined/NaN → "—"` |
| 4 | `pct` | 2786 | const 箭头 | 正数加 `+`，固定 2 位 |
| 5 | `cls` | 2787 | const 箭头 | `>0 up / <0 down / 其余空串` |
| 6 | `code6` | 2788 | const 箭头 | `ts_code.split(".")[0]` |
| 7 | `fmtAmount` | 2870 | const 箭头 | 万亿/亿/元三档 |
| 8 | `fmtMtime` | 2881 | const 箭头 | ns → `YYYY-MM-DD HH:MM`，非法返回 `null` |
| 9 | `card` | 2890 | const 箭头 | 卡片骨架 |
| 10 | `table` | 2891 | const 箭头 | 空表返回 `（空）` |
| 11 | `badges` | 2896 | const 箭头 | 依赖 `M.badge` |
| 12 | `metaHtml` | 2900 | const 箭头 | 生产者/来源行 |
| 13 | `M` | 2904 | const 对象 | 30 组枚举→中文，**必须整体搬** |
| 14 | `cn` | 2947 | const 箭头 | `cn(M.xxx, v)` |
| 15 | `cnSigSource` | 2949 | const 箭头 | 信号来源专用，`SW-*/BP-*` 原样保留 |
| 16 | `_alertSrcCn` | 7975 | const 箭头 | 告警来源专用（4 个枚举） |

**边界判定**：`_alertSrcCn` 与 `cnSigSource` 都放这里——它们是「枚举→中文」这条口径的两个方言，
留在同一模块才能保证新增枚举时两处都被看见。

**验收**：拿 `dashboard.json` / `intraday.json` / `watchlist.json` 各一份真实快照，
旧 `pct`/`num`/`fmtAmount`/`fmtMtime` 与新模块对同一输入数组输出**逐字节相同**。

## 2.2 `fe/symbol.js` — 6 个

| # | 名字 | 行 | 作用 |
|---|---|---|---|
| 1 | `kTsPlayable` | 2808 | 判定是否可跳 K 线：带 `.SH/.SZ/.BJ` 后缀可跳；裸 6 位仅 `[023569]` 开头 |
| 2 | `kName` | 2798 | 名称 → 可点 HTML（名称先 `esc`） |
| 3 | `kNameRaw` | 2818 | 内核：显示文本已是 HTML，只转义代码 |
| 4 | `kNameClick` | 2828 | 点击处理器，`stopPropagation` + `KLineApp.open` |
| 5 | `kListFrom` | 2836 | 向上找首个含 ≥2 个 `.k-name` 的容器，供上下键切票 |
| 6 | `kCode` | 2862 | 纯代码可点（走 `data-open-kline` 面板级委托） |

**唯一改动点**：`kNameClick` 里的 `KLineApp.open(ts, name, kListFrom(el))`（2833）
改成 `host.open(...)`，host 由 `bindKLineHost()` 注入。其余五行原样搬。

**边界判定**：`kTsPlayable` 是唯一判定源，`wireReviewTableKNames`(7019) 复用它，
不许在复盘模块里再写一遍正则——那是 2026-10-17 已踩过的坑。

**验收**：`kTsPlayable` 对七个边界逐个对拍：
`"000001.SZ"`→true、`"600000"`→true、`"159583"`→**false**（ETF，1 开头）、
`"430047"`→**false**（北交所 4 开头）、`"888880"`→**false**、`""`→false、`null`→false。
这七个是 2026-09-13 实测出来的，改错任何一个都会出现「看着能点、点了报非法 ts_code」。

## 2.3 `fe/position.js` — 12 个

| # | 名字 | 行 | 归属 | 消费者 |
|---|---|---|---|---|
| 1 | `_lgNum` | 3055 | 账本数字（千分位） | 仪表盘 |
| 2 | `_lgCls` | 3060 | 涨跌色 | 仪表盘 |
| 3 | `_lgPct` | 3061 | 百分比 | 仪表盘 |
| 4 | `ledgerKpisHtml` | 3063 | 账户 KPI 组（含条件单到期告警） | 仪表盘 |
| 5 | `ledgerPosHtml` | 3090 | 券商账本持仓表（14 列） | 仪表盘 |
| 6 | `_bookRow` | 3127 | 委托/条件单行 | 仪表盘 |
| 7 | `bookActiveHtml` | 3147 | 活跃单 | 仪表盘 |
| 8 | `posThreeLayerHtml` | 3440 | 三层仓位（纸面/实盘/手动） | 作战表 |
| 9 | `_amvHoldHtml` | 5097 | 持仓实时状态表 | 盘中 |
| 10 | `poolStopMap` | 5437 | 止损锚取数（武装 STOP_LOSS 最高一条） | **死代码，口径保留** |
| 11 | `poolStopCell` | 5450 | 止损距离渲染 | **死代码，口径保留** |
| 12 | `stopDistanceGrade` | — | **新增**：唯一分级函数，把三处阈值收成一处 | 全部 |

**边界判定**：只统一「格式 + 分级」，不统一数值来源。
`ledgerPosHtml` 吃券商账本、`loadPnl` 吃 positions.json+实时价，
两个来源并存是业务事实，前端不该假装它们是同一个数。

## 2.4 `fe/sector_amv_data.js` — 33 个 + 6 个常量

**sector_track 切片与口径（7）**：`_stSnaps`(4576)、`_stLatest`(4577)、`_stVal`(4578)、
`_stSeries`(4586)、`_stChgVal`(4572)、`_stUnit`(4587)、`_stLabel`(4588)

**演化口径与几何（13）**：`_evoSnaps`(4006)、`_evoTime`(4007)、`_gapBreak`(4008)、
`_lineD`(4015)、`_evoXl`(4025)、`_evoLastOf`(4031)、`_evoIdxVal`(4032)、`_evoRankVal`(4033)、
`_evoUnit`(4037)、`_evoFmt`(4038)、`_evoPick`(4039)、`_evoView`(3664)、`_evoShare`(4149)

**指数贡献（1，核心）**：`_evoContrib`(4156) = `(share−base)×10` 即 Δ占比‰。
**唯一实现**，`_stVal` 的 contrib 分支与 `_stGridHtml` 的 `shareAt` 必须改调它。

**sector_track 取数（1）**：`loadSectorTrack`(4902)

**amv_live 取数与时效（12）**：`loadAmvLive`(5107)、`_amvAlias`(5065)、`_amvIndexOf`(5066)、
`_amvLine`(5071)、`_amvStamp`(5076)、`_regimeMark`(5084)、`_regimeTitle`(5091)、
`_inTradingSession`(5019)、`_amvStale`(5029)、`_amvAutoRefresh`(5043)、`refreshAmv`(5050)

**常量（6）**：`_EVO_PAL`(4027)、`_EVO_IDX`(4028)、`_EVO_IDX_PAL`(4030)、
`_FC_STAGE_MAE`(4088)、`AMV_STALE_MIN`(5028)、`AMV_CODE_ALIAS`(5064)

**边界判定**：`_amvLine`/`_regimeMark`/`_amvStamp` 三个返回 HTML，严格说不属数据层。
留在数据层的理由：它们编码的是「哪个码对应哪个值、什么算陈旧」这个口径，
拆去视图层会让口径跨两个文件。代价是数据层要 import `format.js` 的 `esc/cls/pct`——单向依赖，不构成环。

## 2.5 `fe/sector_amv_view.js` — 49 个

**板块轮动·当天（12）**：`stSet`(4552)、`_stPaint`(4560)、`_sectorTrackHtml`(4589)、
`_stGridHtml`(4615)、`_stQuadHtml`(4664，156 行最长)、`_stHitCode`(4820)、`_stBindQuad`(4826)、
`_stApplyHl`(4858)、`_stTipEl`(4880)、`_stTipShow`(4883)、`_stTipHide`(4882)、`_stTipMove`(4896)

**日内演化·状态与绘制（22）**：`_evoRe`(3631)、`_evoPaint`(3632)、`evoSet`(3642)、
`evoSetMode`(3643)、`evoSetSrt`(3644)、`evoToggleIdx`(3645)、`_evoTogglePin`(3646)、
`evoClearRange`(3790)、`_evoHitCode`(3647)、`_evoIdxAt`(3653)、`_evoToGlobal`(3820)、
`_evoToLocal`(3824)、`_evoApplyHl`(3672)、`_evoDrawCross`(3683)、`_evoDrawRange`(3695)、
`_evoApplyRange`(3713)、`_evoCursorRows`(3720)、`_evoCardUpdate`(3731)、`_evoRangeRows`(3768)、
`_evoPlaceCard`(3779)、`_evoRankSync`(3797)、`_evoApplyCursor`(3812)、`_evoBind`(3828，约 130 行最复杂)

**演化·补录与浮层（6）**：`_evoBfLabel`(3961)、`_evoBfMark`(3969)、`evoBackfill`(3973)、
`_evoBfPoll`(3984)、`_evoBfReload`(4000)、`_evoFitH`(4005，**空函数，必须原样保留**，调用点不删)

**演化·图表与骨架（8）**：`_evoAmvChart`(4053)、`_evoQuoteChart`(4121)、`_evoContribChart`(4162)、
`_amvForecastHtml`(4089)、`_evoPlaceFc`(4112)、`_evoRankHtml`(4190)、`_evoIdxLegend`(4201)、
`_amvEvoHtml`(4209)

---

# 第三部分：三类风险的逐条明细

## 3.1 DOM id 直取 — 34 处 = 31 处 `getElementById` + 3 处越界挂载

### A. `fe/sector_amv_view.js`（17 处）

| # | 行 | 函数 | 取的 id | 改成 |
|---|---|---|---|---|
| 1 | 3633 | `_evoPaint` | `amvEvoBody` | `this.box` |
| 2 | 3673 | `_evoApplyHl` | `amvEvoBody` | `this.box` |
| 3 | 3714 | `_evoApplyRange` | `amvEvoBody` | `this.box` |
| 4 | 3732 | `_evoCardUpdate` | `evoCard` | `box.querySelector('.evo-card')` |
| 5 | 3733 | `_evoCardUpdate` | `evoPaneL` | `box.querySelector('.evo-pane-l')` |
| 6 | 3792 | `evoClearRange` | `evoCard` | 同上 |
| 7 | 3798 | `_evoRankSync` | `amvEvoBody` | `this.box` |
| 8 | 3814 | `_evoApplyCursor` | `amvEvoBody` | `this.box` |
| 9 | 3829 | `_evoBind` | `panel-intraday` | **传入的 el** |
| 10 | 3857 | `_evoBind` | `evoCard` | `box.querySelector('.evo-card')` |
| 11 | 4113 | `_evoPlaceFc` | `evoPaneL` | `box.querySelector('.evo-pane-l')` |
| 12 | 4561 | `_stPaint` | `stBody` | `this.box` |
| 13 | 4827 | `_stBindQuad` | `panel-intraday` | **传入的 el** |
| 14 | 4843 | `_stBindQuad` | `stTip` | 容器内子节点 |
| 15 | 4859 | `_stApplyHl` | `stBody` | `this.box` |
| 16 | 4880 | `_stTipEl` | `stTip` | 容器内子节点 |
| 17 | 4882 | `_stTipHide` | `stTip` | 容器内子节点 |

**第 9、13 两处最要紧**：`#panel-intraday` 是旧页的 section 容器，抽成卡片后**它不存在**，
`_evoBind`/`_stBindQuad` 会静默 `return`，事件永远绑不上。

### B. `fe/sector_amv_data.js`（6 处，全在数据层写 DOM —— 这是环的一部分）

| # | 行 | 函数 | 取的 id |
|---|---|---|---|
| 18 | 5053 | `refreshAmv` | `amvRefreshBtn` |
| 19 | 5059 | `refreshAmv` | `amvStamp` |
| 20 | 5060 | `refreshAmv` | `amvStamp` |
| 21 | 5061 | `refreshAmv` | `amvStamp` |
| 22 | 5124 | `loadAmvLive` | `amvHoldBody` |
| 23 | 5125 | `loadAmvLive` | `amvStamp` |

这 6 处必须**从数据层删掉**，改成数据层只更新 `store`，由视图层在 `refresh()` 后重绘。
删不掉，环就拆不干净。

### C. 随 Tab 迁移时一并改（8 处）

| # | 行 | 函数 | 取的 id |
|---|---|---|---|
| 24 | 4969 | `_br880Paint` | `br880Body` |
| 25 | 4970 | `_br880Paint` | `cur880Amt` |
| 26 | 7165 | `_rvInjectPaperCss` | `rvPaperCss` |
| 27 | 7798 | `_rvChartSetMode` | `rvChartMount` |
| 28 | 7808 | `_rvChartWireToggle` | `rvModeBar` |
| 29 | 7020 | `wireReviewTableKNames` | `panel-review` |
| 30 | 7077 | `wireReviewSwimlane` | `panel-review` |
| 31 | 7081 | `wireReviewSwimlane` | `rvFloat` |

### D. 越界挂载（3 处，必须改成容器内子节点）

| # | 行 | 函数 | 越界目标 |
|---|---|---|---|
| 32 | 4881 | `_stTipEl` | `document.body.appendChild` |
| 33 | 7091 | `wireReviewSwimlane` | `document.body.appendChild` |
| 34 | 7166 | `_rvInjectPaperCss` | `document.head.appendChild` |

第 32 处最危险：`#stTip` 挂在 body 上、`position:fixed` 跟鼠标。
改成容器内绝对定位后，**滚动时坐标要重算**（旧页没这个问题是因为它 fixed）。
第 34 处可以把 `<style>` 塞进卡片根节点，但同一张卡开两次会重复注入，需要 `data-sig` 去重。

**另计 8 处 `$`（querySelector）直取**，性质相同但不进 34：
`renderConcept`(7490)、`renderReviewMd`(7826/7847)、`renderReviewMdPaper`(7863)、
`renderReviewMdV6`(7898)、`_cpWire`(7668)、`_cpShowView`(7715)、`_cpApplyPending`(7730)、`renderSector`(7225)。

## 3.2 内联 onclick — 12 个函数 / 14 处

| # | 函数 | 行 | 生成位置 | 迁移后 |
|---|---|---|---|---|
| 1 | `kNameClick` | 2823 | `kNameRaw` | `data-act="kline"` |
| 2 | `evoClearRange` | 3746 | `_evoCardUpdate` | `data-act="evo-clear-range"` |
| 3 | `evoToggleIdx` | 4206 | `_evoIdxLegend` | `data-act="evo-idx" data-arg="${c}"` |
| 4 | `evoBackfill` | 4216 | `_amvEvoHtml` | `data-act="evo-backfill"` |
| 5 | `evoSetMode` | 4218 | `_amvEvoHtml` | `data-act="evo-mode"` |
| 6 | `evoSet` | 4219 | `_amvEvoHtml` | `data-act="evo-dir"` |
| 7 | `evoSetSrt` | 4220 | `_amvEvoHtml` | `data-act="evo-srt"` |
| 8 | `stSet` | 4599, 4605 | `_sectorTrackHtml` | `data-act="st-set"`（2 处） |
| 9 | `refreshAmv` | 5012 | `_amvBar` | `data-act="amv-refresh"` |
| 10 | `sortPool` | 5500, 5505, 5509 | `renderPoolTrack` | **死代码，随归档删**（3 处） |
| 11 | `poolToggleDetail` | 5517 | `renderPoolTrack` | **死代码，随归档删** |
| 12 | `_rvChartSetMode` | 7843, 7844 | `renderReviewMd` | `data-act="rv-mode"`（2 处） |

**活代码只有 10 个**（第 10、11 项属追踪池死代码）。

**另计 4 处内联 onclick 调用的是 Tab 专属函数**，同一批迁移时要一起处理：
`ledgerSwitch`(3031/3032)、`rsSet`(4365/4372/4439/4442/4453/4454 共 6 处)、
`rsForceRetry`(8196)、`activate`(3265/3271/8459)。

**改法模板**（三处已有现成委托可抄）：`_evoBind`(3828)、`_stBindQuad`(4826)、`_cpWire`(7667)
全是 `addEventListener` + `closest('[data-*]')`。只需把宿主从 `#panel-intraday` / `$('#panel-concept')`
换成传入容器，事件体照搬。

```js
// 卡片 mount 内，一次委托覆盖全部按钮
el.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-act]');
  if (!t) return;
  const { act, arg } = t.dataset;
  const handlers = {
    'evo-dir':   () => { store.evoDir = arg; paintEvo(box); },
    'evo-mode':  () => { store.evoMode = arg; paintEvo(box); },
    'evo-srt':   () => { store.evoSrt = arg; paintEvo(box); },
    'evo-idx':   () => { store.evoIdxOn[arg] = !store.evoIdxOn[arg]; paintEvo(box); },
    'st-set':    () => { store.stSet(t.dataset.k, arg); paintQuad(box); },
    'amv-refresh': () => refreshAmv(store),
  };
  if (handlers[act]) handlers[act]();
});
```

## 3.3 最阴的一条：一次性布尔

`_evoDelegated` / `_stDelegated` 用于「事件只绑一次」。若沿用模块级变量，
**第二张卡挂载时事件永远绑不上，且不报错**。必须改成每实例状态。

## 3.4 拆环必须付的代价

3.1 的 B 组（6 处数据层写 DOM）删掉后，`refreshAmv` 的失败提示
（`amvStamp.textContent = '刷新失败：...'`）就没有落点了。
要么数据层返回 `{ok, reason}` 由视图层显示，要么加一个 `onStatus` 回调。
这是拆环必须付的代价，不是可以省略的细节。

---

# 第四部分：核验记录

01 抽查 24 个行号，全部准确（`esc`2784 / `num`2785 / `pct`2786 / `cls`2787 / `code6`2788 /
`fmtAmount`2870 / `fmtMtime`2881 / `M`2904 / `kTsPlayable`2808 / `kName`2798 /
`kNameClick`2828 / `kCode`2862 / `_lgNum`3055 / `posThreeLayerHtml`3440 /
`poolStopMap`5437 / `poolStopCell`5450 / `_evoContrib`4156 / `loadSectorTrack`4902 /
`loadAmvLive`5107 / `refreshAmv`5050 / `_stQuadHtml`4664 / `_evoBind`3828 /
`_evoFitH`4005 / `_stVal`4578）。
