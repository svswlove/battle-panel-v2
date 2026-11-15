# 盘中缺口审计（2026-10-25）

> 任务：查清旧页盘中 Tab（innerText ≈ 16702）与 v2 盘中卡（≈ 2701）之间 ~14000 字的内容构成。
> 结论先行：**缺口 ≈ 98% 来自两处——「板块与个股研究」卡和「审计明细·信号流水」**。
> 上游 HANDOFF 列的五个候选（冰点 / 广度880 / 盘中脉冲 / AMV实时 / 日内演化）**全部不是缺口**，
> 其中三个 v2 已实现且字数量级相当，盘中脉冲在旧页盘中 Tab 里根本不存在，
> 日内演化在旧页本身也只有 414 字（只占缺口 3.8%）。
>
> ⚠ 两条执行偏差（先说清，避免误读）：
> ① **无头浏览器没能跑起来**——本机沙箱禁子进程（`spawn EPERM`），改用 node:vm 跑旧页真函数 + 真数据；
> ② **探针没能落到 `Hubagent/01/_vis/`**——该目录在本会话沙箱外，写入被拒。探针现放在
> `docs/probes_20261025_intraday/`，见 §七。

---

## 〇、测量方法与可信度（先读这段，否则数字看不懂）

### 0.1 动态路线没跑成（已尝试，如实记录）

| 尝试 | 结果 |
|---|---|
| headless Chrome + CDP 取 `#panel-intraday`.innerText | **失败**：`spawn EPERM`。本机沙箱不允许子进程，chrome.exe 起来就被拦 |
| `spawnSync('cmd', …)` 同类验证 | 同样 `spawnSync cmd EPERM`，确认是执行环境限制，不是探针写错 |
| 检查既有 CDP 端口 9222/9223/9339/9341 | 全 closed，无可用在跑实例 |

**替代方案**（本审计实际用的）：在 `node:vm` 里加载**旧页自己的主 `<script>` 块**
（`engine/web/index.html` 第 2823–8990 行，6168 行 / 341151 字符），
给一个最小 DOM 桩，把 **8810 真实接口的 JSON** 灌进模块级状态变量
（`_sectorTrack` / `_amvLive` / `_panic` / `_breadth880` / `_rs`），
然后**真的调 `renderIntraday(d)`**、真的调 `_rsHtml()` / `_sectorTrackHtml()` / `_amvEvoHtml()` /
`_panicCardHtml()` / `_amvHoldHtml()`，拿到每块**真实生成的 HTML**，再按 innerText 规则折算字数。

这不是"静态估算"：跑的是旧页的原函数、喂的是生产 JSON、量的是真实产出 HTML。
唯一不是真的东西是浏览器排版（字体/折行）——而 innerText 字数不受排版影响，只受块级/单元格分隔符影响。

### 0.2 折算规则（对齐 Chrome innerText）

- 块级元素 → 换行；`<td>/<th>` → 制表符；`<tr>/<table>` → 换行；行内元素 → 直接去掉
- 实体解码；连续空白折叠；`<script>/<style>/<title>/<desc>/<defs>` 整段丢弃
- **`<details>` 收起的两种口径都算了**（见 0.3）
- SVG 里的 `<text>` 保留（innerText 会含 SVG 文本），但 `<title>` 丢弃

### 0.3 旧页总字数的两个口径

旧页盘子底下有 `<details class="fold">`（审计明细，4718 行）。它在 HTML 里**没有 `open` 属性**，
CSS 里也没有任何 JS 去展开它（`.fold` 只有 2 条样式，1660–1661 行）。实测两种口径：

| 口径 | 旧页整页 innerText | v2 整卡 innerText | 差 |
|---|---|---|---|
| **details 收起（浏览器默认真实状态）** | **13186** | **2400** | **10786** |
| details 展开（手工点开） | 18968 | 2400 | 16568 |
| 去空白字符数（收起 / 展开） | 10033 / 14829 | 2032 | 8001 / 12797 |

报告里的 **16702** 落在 13186 和 18968 之间，最接近"部分折叠块被算进去了"或"读的时候
details 处于展开态"（差值 2266，约等于信号流水表减去表头后的量级）。**口径无法完全复现，
但无论取哪一档，缺口的归属结论完全一致**——因为占比大头是同一块。

下文所有占比一律用**保守档：details 收起 = 13186**（对旧页更不利的算法），
这样得出的缺口结构不会被高估。

### 0.4 数据实态（本次测量时的真实数据，周日 T-1 收盘口径）

| 端点 | 字节 | 记录数 |
|---|---|---|
| `/api/intraday.json` | 53921 | signals 49 / market_quotes 10 / snapshots·env·events·commands_b 全 0 |
| `/api/amv_live.json` | 2548 | indices / sectors / holdings |
| `/api/panic` | 8782 | series 若干 + items 3 |
| `/api/breadth_880.json` | 237 | today / prev |
| `/api/sector_track.json` | 2505680 | snapshots 235 / boards 90 |
| `/api/amv_research.json` | 138186 | boards 90 / members 183 |

---

## 一、旧页盘中 Tab 功能块全清单

调用入口：`activate('intraday')` → `loadTab('intraday')`（8244–8265 行）→ `renderIntraday(d)`（4672–4757）。

### 1.1 渲染骨架（renderIntraday 拼卡顺序，4704–4731 行）

```
metaHtml(生产者行)
card("冰点监控",        #panicBody      )   ← _panicPaint 填
sess-line(会话行)
card("实时行情",        mqHtml          )
top-split {
  card("两市成交额",    toHtml          )   ← 含 _turnoverChart + _br880CardHtml
  card("板块轮动·当天", #stBody         )   ← _stPaint 填
}
card("日内演化 · 活跃指数与指数贡献", #amvEvoBody )  ← _evoPaint 填
card("板块与个股研究",  #rsBodyIntraday )   ← _rsPaint 填
card("持仓实时状态",    #amvHoldBody    )   ← loadAmvLive 填
<details class="fold"> 审计明细
  card("信号流水") card("判强判弱快照") card("环境系数修正")
  card("盘中事件")  card("通道B·指令单")
</details>
```

### 1.2 逐块清单（行号 = `engine/web/index.html` 现行行号）

| # | 块 | 函数组（行号范围） | 行数 | 真实 innerText（收起/展开） | 调用的 API |
|---|---|---|---|---|---|
| 1 | 页头生产者行 | `metaHtml` 2958–3004 | 47 | 53 | 无（读 d.meta） |
| 2 | **冰点监控** | `loadPanic` 4540–4570 · `_panicChart` 4571–4618 · `_panicCardHtml` 4619–4655 · `_panicPaint` 4656–4671 | 132 | **523** | `GET /api/panic` |
| 3 | 会话行 | renderIntraday 4707 内联 | 1 | ~70 | 无 |
| 4 | **实时行情**（9–10 指数 KPI + 迷你 spark） | `mqHtml` 4677–4685（内联）· `_sparkInject` 5119–5128 · `loadIndexSpark` 5129–5146 · `kCode` 2920–2927 | ~50 | **621** | `GET /api/intraday.json`(market_quotes) + `/api/sector_spark.json` |
| 5 | **两市成交额**·成交额分时 | `_hm2m` 5188 · `_turnoverChart` 5189–5216 | 29 | 分时图 16 | `/api/intraday.json`(market_turnover) |
| 6 | **两市成交额**·成交额对比 | renderIntraday 4694–4699（内联） | 6 | 112 | 同上 + `_br880AmountTxt` |
| 7 | **两市成交额**·涨跌家数/涨跌停 | `_br880CardHtml` 5147–5169 · `_br880AmountTxt` 5170–5173 · `_br880Paint` 5174–5177 · `loadBreadth880` 5178–5187 | 41 | 140（含块 5、6 共 343） | `GET /api/breadth_880.json` |
| 8 | **板块轮动 · 当天** | `_stPaint` 4766–4777 · `_sectorTrackHtml` 4795–4820 · `_stQuadHtml` 4870–5025 · `_stGridHtml` 4821–4869 · `_stBindQuad` 5032–5063 · `_stApplyHl` 5064–5085 · `_stHitCode` 5026–5031 · `_stTip*` 5086–5107 · `loadSectorTrack` 5108–5118 · `_stVal/_stSnaps/_stChgVal` 4778–4794 | **~450** | **526**（四象）/ 983（网格） | `GET /api/sector_track.json` |
| 9 | **日内演化 · 活跃指数与指数贡献** | `_evoPaint` 3690–3699 · `_amvEvoHtml` 4271–4313 · `_evoAmvChart` 4111–4146 · `_evoQuoteChart` 4183–4210 · `_evoContribChart` 4224–4251 · `_evoRankHtml` 4252–4262 · `_evoIdxLegend` 4263–4270 · `_amvForecastHtml` 4147–4173 · `_evoPlaceFc` 4174–4182 · `_evoBind` **3886–4018(133)** + 30 个交互小函数 3689–4062 · `_evoShare/_evoContrib/_lineD/_gapBreak` 4066–4082,4211–4223 | **~700**（55 函数） | **414** | `/api/sector_track.json`（同 #8）+ `/api/amv_live.json`(forecast) + `POST /api/sector_track_backfill` |
| 10 | **板块与个股研究** | `loadResearch` 4327–4368 · `rsForceRetry` 4369–4382 · `_rsTarget` 4383–4390 · `_rsPaint` 4391–4407 · `rsFmt/rsSp/rsSort/rsHead/rsRows/rsPartsHtml` 4408–4472 · `_rsHtml` 4473–4539 | **~230** | **10624** ⭐ | `GET /api/amv_research.json` |
| 11 | **持仓实时状态** | `_amvHoldHtml` 5303–5312 · `loadAmvLive` 5313–5337 | 35 | **113** | `GET /api/amv_live.json` |
| 12 | **审计明细·信号流水** | renderIntraday 4719–4721（内联 table） | 3 | **5700** ⭐ | `/api/intraday.json`(signals 49 条) |
| 13 | 审计明细·判强判弱快照 | renderIntraday 4722–4724（内联） | 3 | 10 | `/api/intraday.json`(0 条) |
| 14 | 审计明细·环境系数修正 | renderIntraday 4725–4726（内联） | 2 | 10 | 同上(0 条) |
| 15 | 审计明细·盘中事件 | renderIntraday 4727–4728（内联） | 2 | 8 | 同上(0 条) |
| 16 | 审计明细·通道B·指令单 | renderIntraday 4729–4730（内联） | 2 | 11 | 同上(0 条) |
| | **合计** | | | **13186（收起）/ 18968（展开）** | |

### 1.3 顺带纠正两个上游误解

1. **`loadIntraday` 这个函数不存在**。入口是 `loadTab('intraday')` → `renderIntraday(d)`
   （全页 grep `function loadIntraday` = 0 命中）。
2. **`watchPaintLiveSector`（5800–5827, 28 行）不是盘中 Tab 的块**，它属于**自选池**实时卡
   （`watchHydrateLiveSectors` 5828 / `watchSectorCardHtml` 5850 / `watchPaintSector` 5862）。
   v2 的「盘中脉冲」块（`pulseHtml`）是仿它做的**新块**，旧页盘中 Tab 里没有对应块。
   这一条直接推翻 HANDOFF 的候选 3。

---

## 二、v2 现状：逐块三态判定

判据（任务书要求）：函数体有无完整渲染逻辑 / 是否真调 API / DOM 是否真生成。
v2 的渲染函数我也在 `node:vm` 里真跑了一遍（拆掉 ESM 语法、喂真实 JSON），拿到真实 HTML 再折算。

| # | 旧页块 | 旧页字数 | v2 对应 | v2 字数 | 状态 |
|---|---|---|---|---|---|
| 1 | 页头生产者行 | 53 | 无（v2 用卡头徽标替代） | 63 | **完全缺失**（内容是 meta 行，v2 换成徽标，语义不等价但量级相同） |
| 2 | 冰点监控 | 523 | `panicHtml` frontend.js:197–241 + `panicChart` 150–195 | 277 | **已完整搬运** |
| 3 | 会话行 | ~70 | 无 | — | **完全缺失**（phase/state/信号计数一行） |
| 4 | 实时行情（指数 KPI） | 621 | 无（AMV 指数分项 `idxHtml` 部分替代） | 0 | **完全缺失** |
| 5 | 成交额分时图 | 16 | 无 | 0 | **完全缺失** |
| 6 | 成交额对比 3 行 | 112 | 无 | 0 | **完全缺失** |
| 7 | 涨跌家数/涨跌停 | 140 | `breadthHtml` frontend.js:299–319 | 210 | **已完整搬运** |
| 8 | 板块轮动 · 当天 | 526 | `frontend/sector.js` 全量（`paint`/`html`/`quadHtml`/`gridHtml`/tip/bind） | 574 | **已完整搬运并验收**（HANDOFF 第一步，逐值一致） |
| 9 | 日内演化 | 414 | 无 | 0 | **完全缺失**（第二步 `_evo*` 55 函数待做） |
| 10 | **板块与个股研究** | **10624** | 无 | **0** | **完全缺失** ⭐ |
| 11 | 持仓实时状态 | 113 | `amvHtml` 里的 `holdHtml` 表 frontend.js:267–278 | 436（含 AMV 全部） | **已完整搬运** |
| 12 | **审计明细·信号流水** | **5700** | 无 | **0** | **完全缺失** ⭐ |
| 13–16 | 审计明细其余 4 表 | 39 | 无 | 0 | **完全缺失**（当前数据 0 行，实际字数≈表头 39） |
| — | （v2 新增）板块轨迹摘要 | — | `trackHtml` frontend.js:357–377 | 492 | v2 自建，旧页无此块 |
| — | （v2 新增）盘中脉冲 | — | `pulseHtml` 407–419 + `pulseCard` 380–405 | 149 | v2 自建，旧页盘中无此块 |
| | **合计** | **13186** | | **2400** | |

### 2.1 五块「判定为真实现」的证据

任务书要求逐一判定「真实现 vs 占位骨架」。按三条判据实测：

| 块 | 有完整渲染逻辑 | 真调 API | DOM 真生成 | 判定 |
|---|---|---|---|---|
| 冰点 | `panicHtml` 45 行：hero 区 + 3 项分位卡（带 `--pq` 进度条）+ 图例 + `panicChart` 46 行 SVG（双线 + 冰点圆点 + 参考线 10/90）+ 页脚四段 | `/api/panic`（frontend.js:633） | 实测输出 7483 字符 HTML / 11 个 `<text>` / 2 条 `<path>` | **真实现** |
| AMV | `amvHtml` 52 行：主区（全A amv + 覆盖 + 单位校验）+ 指数分项 grid + 领先/落后双榜 + 持仓表 | `/api/amv_live.json`（632） | 实测 2961 字符 / 持仓表 5 列表头 | **真实现** |
| 广度880 | `breadthHtml` 21 行：7 行 × 3 列对比表（涨/跌/平/涨停/跌停/成交额/统计）+ 来源行 | `/api/breadth_880.json`（634） | 实测 1157 字符 / `<table>` 7 `<tr>` | **真实现** |
| 轨迹 | `trackHtml` 21 行 + `trackSpark` 22 行 SVG + `trackTable` 11 行 | `/summary`（本插件 backend，635） | 实测 6196 字符 / 2 张表 + 1 SVG | **真实现** |
| 脉冲 | `pulseHtml` 13 行 + `pulseCard` 26 行（含 160×34 迷你线） | `/api/sector_intraday.json?codes=`（590） | 实测 6300 字符 / 每码 1 卡 | **真实现** |

**没有任何一块是占位骨架。** frontend.js 里五块的引用是真的实现，不是 TODO 桩。

### 2.2 一条要澄清的账面差

v2 的 2400 字 vs 报告的 2701 字，差 301。原因是我按「旧页有对应块的」逐块重建，
没算 shell 侧注入的东西（侧栏、卡标题、状态条、`.it-warn` 等）。这不是缺陷，是测量口径差。
旧页侧同理：我算的 13186/18968 与报告的 16702 差 2266/3516，最可能是 details 折叠态与
浏览器实际 `display:none` 面板的 innerText 差异（HANDOFF 已记录旧页面板在无头浏览器里
是 `display:none`，innerText 对不可见元素返 **`''`**——那反而说明报告里的 16702 是在
`display:block` 状态下取的，与我的 details 展开档 18968 属同一类）。

---

## 三、14000 字缺口构成结论（核心）

### 3.1 按字数排序的缺口表

取保守档（旧页 details 收起 = 13186，v2 = 2400，缺口 = 10786）：

| 排名 | 缺口块 | 旧页字数 | 占缺口 | 占旧页总字 | v2 状态 |
|---|---|---|---|---|---|
| **1** | **板块与个股研究** | **10624** | **98.5%** | **80.6%** | 完全缺失 |
| **2** | **审计明细·信号流水** | **5700** | **52.8%** | **43.2%** | 完全缺失（在 details 里） |
| 3 | 实时行情（指数 KPI） | 621 | 5.8% | 4.7% | 完全缺失 |
| 4 | 日内演化 | 414 | 3.8% | 3.1% | 完全缺失 |
| 5 | 两市成交额（3 小卡合计） | 343 | 3.2% | 2.6% | 完全缺失 |
| 6 | 冰点监控 | 523 | -2.3%* | 4.0% | ✅ 已搬（v2 277，略少） |
| 7 | 板块轮动·当天 | 526 | ≈0* | 4.0% | ✅ 已搬（v2 574，略多） |
| 8 | 持仓实时状态 | 113 | ≈0* | 0.9% | ✅ 已搬（并入 AMV 436） |
| 9 | 涨跌家数 880 | 140 | ≈0* | 1.1% | ✅ 已搬（v2 210，略多） |
| 10 | 会话行 + meta 行 + 明细 4 表 | ~130 | 1.2% | 1.0% | 完全缺失 |

\* 负值/≈0 = v2 该块字数已持平或超过旧页，不构成缺口。

**注意第 1、2 名的百分比加起来 151%** —— 因为第 2 名（信号流水 5700）在 details 收起档里
**本来就不该计入**。两个口径必须分开读：

### 3.2 两个口径下的缺口构成

**口径 A：旧页 details 收起（= 浏览器默认真实显示状态），旧页 13186 / v2 2400 / 缺口 10786**

| 块 | 字数 | 占缺口 |
|---|---|---|
| 板块与个股研究 | 10624 | **98.5%** |
| 实时行情 | 621 | 5.8% |
| 日内演化 | 414 | 3.8% |
| 两市成交额 | 343 | 3.2% |
| 会话行 + meta + 明细表头 | ~130 | 1.2% |
| （冰点/轮动/广度/持仓 v2 已持平或超出） | ≈0 | ≈0% |

→ **缺口几乎 100% 是「板块与个股研究」一张卡。**

**口径 B：旧页 details 展开，旧页 18968 / v2 2400 / 缺口 16568**

| 块 | 字数 | 占缺口 |
|---|---|---|
| 板块与个股研究 | 10624 | **64.1%** |
| 审计明细·信号流水 | 5700 | **34.4%** |
| 其余（实时行情/日内演化/成交额/杂项） | ~1600 | ~9.7% |

→ **两张卡占 98.5%。**

### 3.3 「板块与个股研究」为什么这么大（拆到子块）

`_rsHtml()` 内部实测拆分（同一 `/api/amv_research.json`，boards 90 / members 183）：

| 子块 | HTML 字符 | innerText | 说明 |
|---|---|---|---|
| 工具条 `rs-bar`（层级/排序/口径行） | 594 | 83 | 3 组按钮 + 归属日 + 全A AMV |
| **左栏 板块排名表（90 行 × 6 列）** | 81829 | **3302** | `#` 名次 / 名称 / 指数贡献 / 涨跌 / AMV / 近30日AMV 缩略线 |
| 中栏 贡献分项 `rs-parts` | 1367 | 138 | 5 条横向条（涨跌/流通股/活跃度/新股/大盘基准） |
| **中栏 成分股表（183 行 × 6 列）** | 167744 | **5710** | 同 6 列结构 |
| **合计** | 251534 | **9236** | （另有 `.rs-hd` 标题行等约 100 字，折算后 10624 含表头 TAB 分隔） |

要点：**它的大不是因为「逻辑复杂」，而是因为「行数多」**。90 + 183 = 273 行 × 6 列 ≈ 1638 个单元格，
每个单元格 4–6 字，就是 8000–10000 字。**这是纯粹的数据表体量，不是功能缺失。**

同理信号流水：49 条信号 × 8 列 = 392 格 → 5700 字。数据条目决定，不是渲染复杂度。

---

## 四、每块移植工作量评级 + 依赖

评级口径：
- **小** = 有现成端点、纯表格/纯数字、无交互，≤100 行；
- **中** = 有现成端点但需要 canvas/svg 或交互，或需要新插件层；
- **大** = 端点要重写、或函数组 >300 行、或含多套手势/状态机。

| # | 块 | 字数 | 工作量 | 依赖（API / 函数组 / 图元） | 关键风险 |
|---|---|---|---|---|---|
| 1 | **板块与个股研究** | 10624 | **大** | API：`GET /api/amv_research.json`（**端点已存在且实测 200**，compat 插件 backend.py:1336 起 <br>依赖：`rsSort` 排序、`rsSp` 缩略线 SVG、`rsPartsHtml` 分项条、`_rsTarget` 双槽位逻辑（卡片里不需要）、`_rsScroll` 滚动位置保持（卡片里 `setHtml` 防闪已覆盖一半）<br>交互：层级 L2/CONCEPT/L3、排序 3 键、点行选板块、返回全A/回成分股、`data-ctx` 右键、`data-open-amv` 工作台 | **不是难，是量大**。约 230 行原函数 + 6 列 × 273 行表。要落成卡片需处理：①两栏布局在卡内宽度下的表现 ②表头点击排序要改成委托（旧页是内联 `onclick="rsSet(...)"`）③选中行状态每实例化 ④`loadResearch` 的 8s AbortController + 15s 看门狗 + 实时/收盘回落逻辑必须保留（旧页踩过「请求悬挂永久加载中」的坑） |
| 2 | **审计明细·信号流水** | 5700 | **小** | API：`/api/intraday.json` → `signals[49]`，字段 `signal_id/channel/created_at/ts_code/name/kind/source/trigger.condition/status`<br>依赖：`M.channel/M.sigKind/M.sigStatus` 中文映射表（`cnSigSource`）、`kCode`（可跳 K 线）、`table()` 工具<br>**v2 目前没有任何兼容层消费 `/api/intraday.json`** | 纯表格，无交互。唯一要注意：**v2 盘中卡不应全量渲染 49 行**——旧页是塞在 `<details>` 里的折叠明细，卡片里应做成默认折叠或限高滚动，否则会天天占 5000+ 字却没人看 |
| 3 | 实时行情（指数 KPI 带 spark） | 621 | **中** | API：`/api/intraday.json`(market_quotes 10 条) + `/api/sector_spark.json?level=INDEX&codes=`（`loadIndexSpark` 5129–5146，`_sparkInject` 回填背景线）<br>依赖：`kCode`、10 个 `.mq-kpi` 小卡 + 2 条 spark path | spark 端点 v2 已有（`app/plugins/compat_sector/backend.py:736`），实测 200；机制是「先渲空 path、异步回填 `d` 属性」，卡片里要改成先取数据再渲，否则防闪 `setHtml` 会把已回填的 path 冲掉 |
| 4 | 日内演化 | 414 | **大** | API：`/api/sector_track.json`（同 #8）+ `amv_live.forecast` + `POST /api/sector_track_backfill` / `GET ..._status`（两端点实测通）<br>函数组：55 个，`_evoBind` 单函数 133 行含 4 套手势（滚轮缩放/Shift 平移/右键拖拽量区间/Esc 清区间）<br>图元：3 张 SVG（AMV/行情/贡献），**无 canvas**<br>依赖：`_EVO_PAL` 10 色裸 hex + `_EVO_IDX_PAL` 6 色裸 hex → 会撞 `test_no_raw_hex_in_any_card` | 已在 HANDOFF 第八节列全：每实例状态的委托、容器内查询替换 `#evoCard/#evoPaneL`、`_evoBfReload` 要能清 `sector.js` 的轨迹缓存（需给 `sector.js` 开 `invalidate()` 出口）、`_evoFitH` 空函数原样保留。**字数只有 414，投入产出比最差的一块** |
| 5 | 两市成交额（3 小卡） | 343 | **中** | API：`/api/intraday.json`(market_turnover) + `/api/breadth_880.json`（已接）<br>依赖：`_turnoverChart` 28 行 SVG（X 轴固定 09:30–15:00 且要按盘中时段过滤点，旧页 2026-10-14 踩过「窗外点挤满两端」的坑）、`_br880AmountTxt` | 分时图只有 16 字！为一个 16 字的图搬 28 行 SVG 不划算；**建议只补文字部分（112 字对比行），图缓做** |
| 6 | 会话行 | ~70 | **小** | 无 API，读 `d.session`/`d.day_summary`（`phase/state/signal_count/triggered/executed/blocked`） | 一行文本。需要 `M.phase/M.state` 映射表 |
| 7–9 | 冰点 / 轮动 / 广度 / 持仓 | 已搬 | **已完成** | — | 仅冰点 v2 277 vs 旧页 523：少的是旧页的 3 项分位卡里的 `count`（家数）与部分 tooltip 文本，**属正常精简** |

### 4.1 依赖关系图（谁必须先做）

```
板块与个股研究  ── 独立（只依赖 /api/amv_research.json，端点已在）
信号流水        ── 独立（只依赖 /api/intraday.json，端点已在）
会话行          ── 独立（同 intraday.json）
实时行情 KPI    ── 依赖 /api/sector_spark.json（v2 兼容路由已在 compat_sector:736，实测 200）
两市成交额      ── 依赖 intraday.json + 已有的 breadth 端点
日内演化        ── 依赖 sector.js 的轨迹缓存要开 invalidate() 出口 ← 即：要改已验收的 sector.js
```

**只有日内演化会动到已验收代码**，其余都是纯新增。

---

## 五、结论与建议顺序

1. **14000 字缺口的真相：不是「搬漏了 5 个候选块」，而是「漏了一张 10624 字的大表 + 一张 5700 字的明细表」。**
   - 上游 HANDOFF 列的 5 个候选里，**冰点 / 广度 / AMV 三块 v2 已完整实现**（字数已持平或超出旧页），
     **盘中脉冲在旧页盘中 Tab 里根本不存在**（属自选池卡），**日内演化只有 414 字**。
   - 五个候选加起来 523+140+113(持仓)+414 = 1190 字，最多解释缺口的 **11%**。
2. **建议的搬运顺序**（按 字数/工作量 比排）：

   | 顺序 | 块 | 收益 | 成本 | 性价比 |
   |---|---|---|---|---|
   | 1 | 板块与个股研究 | 10624 字（98.5% 缺口） | 大（230 行 + 大表） | ★★★★★ 唯一真正解决缺口的一块 |
   | 2 | 信号流水（折叠） | 5700 字（仅口径 B 计入） | 小（纯表格） | ★★★★ 便宜，但要注意别让它天天占屏 |
   | 3 | 会话行 + 成交额对比文字 | 182 字 | 小（两行文本） | ★★★ |
   | 4 | 实时行情 KPI | 621 字 | 中 | ★★ |
   | 5 | 日内演化 | 414 字 | 大（55 函数 + 要改已验收 sector.js） | ★ 最后做，或明确决定不做 |

3. **对 C 单第二步派单的建议**：HANDOFF 说「如果差的 14000 字主要是 `_evo*`，那这步就是解药」——
   **这个假设已被证伪**。`_evo*` 只能补 414 字（3.1%）。如果目标是「字数对齐」，第二步应该
   改成「板块与个股研究」，而不是日内演化。

---

## 六、未验证 / 已知边界

1. **没有真实浏览器的 innerText**。本审计用 vm 跑真函数 + 真数据 + 文本折算，
   **不含浏览器排版**（字体、折行、`display:none` 继承）。字数量级可靠（与报告 16702 同量级），
   但个位数百分比会有偏差。要拿浏览器真值必须解决 `spawn EPERM`。
2. **旧页总字数 16702 无法精确复现**。我得到 13186（details 收起）/ 18968（展开）两档，
   16702 落在中间。缺口归属结论在两档下一致（板块与个股研究都是第一，
   只是「信号流水」在收起档里不计入）。
3. **盘中实时态未测**。本次测量是周日 T-1 收盘口径（2026-10-23，235 快照）。
   盘中新增采样点、`_evo` 增量重绘、`_rs` 30s 实时刷新都没实测——与 HANDOFF 第七节同一条边界。
4. **三级层级（L3）与概念视图未测**。`/api/amv_research.json` 只拉过默认 L2（boards 90 / members 183）。
   L3/概念的行数会更多，字数会更大。
5. **已补验（原文写「未验证」，现改为已验证）**：指数 spark 端点是 `/api/sector_spark.json?level=INDEX&codes=`
   （旧页 `loadIndexSpark` 5138 行），**不是** `index_spark`。v2 侧兼容路由已存在
   （`app/plugins/compat_sector/backend.py:736`），实测 `?level=INDEX&codes=ALL` → 200 / 1498 字节。
   即第 4 块的 API 依赖已满足。

---

## 七、本次产物

**⚠ 探针没能落到任务书指定的 `Hubagent/01/_vis/`。** 本会话文件沙箱只在
`projects/panel-v2` 内可写，对 `Hubagent/01/_vis/` 写入返回
`EPERM` / `sandbox: file access denied under workspace-write mode`；
申请 `danger-full-access` 升级被拒（"the user rejected escalating this operation"）。
**故探针与原始结果先落在 `projects/panel-v2/docs/probes_20261025_intraday/`，
需要人手工搬一次到 `Hubagent/01/_vis/`（或下次有写权限时搬）。**

**⚠ 探针已搬至 `Hubagent/01/_vis/`**（2026-10-25 深夜 01 手工搬运，下表路径为执行时位置，现已整体移至 _vis/，源目录已删）

| 文件（当前实际位置） | 内容 |
|---|---|
| `docs/AUDIT_20261025_intraday_gap.md` | 本文件 |
| `docs/probes_20261025_intraday/_intraday_gap_probe.js` | 测量探针（vm 加载旧页主脚本 + 真实 API + HTML→innerText 折算） |
| `docs/probes_20261025_intraday/_intraday_gap_result_legacy_blocks.json` | 旧页逐块测量原始结果（14 块） |
| `docs/probes_20261025_intraday/_intraday_gap_result_legacy_cards.json` | 旧页按 `card()` 标题切分的 12 卡结果（含 open/closed 两档） |
| `docs/probes_20261025_intraday/_intraday_gap_result_v2_blocks.json` | v2 逐块测量原始结果（8 区） |

探针跑法（沙箱内 `spawn` 被禁，需在能起 Node 的环境里跑；依赖 8810 在跑）：

```
cd OH-WorkSpace\projects\panel-v2
node docs/probes_20261025_intraday/_intraday_gap_probe.js
# 产物写到脚本同目录的 _intraday_gap_result.json
```

### 7.1 顺带验证到的 v2 API 可用性（都是 200 实测，非推断）

| 端点 | v2 是否已有 | 证据 |
|---|---|---|
| `/api/intraday.json`（信号流水数据源） | ✅ 有 | compat 通配 `/api/{name}.json` 白名单（`app/core/paths.py:93` `"intraday": PANEL_DIR / "intraday.json"`），实测 200 / 55385 字节 |
| `/api/amv_research.json`（研究卡数据源） | ✅ 有 | `app/plugins/compat/backend.py:1787`，实测 200 / 138250 字节 |
| `/api/sector_spark.json`（指数 spark） | ✅ 有 | `app/plugins/compat_sector/backend.py:736`，实测 200 / 1498 字节 |
| `/api/panic` | ✅ 有 | 已被 v2 盘中卡消费 |
| `/api/breadth_880.json` | ✅ 有 | 已被 v2 盘中卡消费 |
| `/api/amv_live.json` | ✅ 有 | `app/plugins/compat/backend.py:1688` |
| `POST /api/sector_track_backfill` | ✅ 有 | HANDOFF §八 已实测通（ops 插件） |
| `GET /api/sector_track_backfill_status` | ✅ 有 | HANDOFF §八 已实测通（compat_sector） |

**结论：四块待搬内容的 API 依赖全部已满足，没有一块是「卡在端点没做」。**
工作量全在前端渲染层。

### 7.2 文件修改日志登记未完成（需人手工补）

任务书「收尾」要求在 `OH-Works/文件修改日志/2026-10-25.md` 末尾 append 一行。
该文件在 `OH-Works/` 下、**本会话沙箱（仅 `projects/panel-v2` 可写）之外**，
写入被拒（`EPERM` / `sandbox: file access denied under workspace-write mode`），
申请 `danger-full-access` 升级被拒（"the user rejected escalating this operation"）。

**待办**：把 `docs/probes_20261025_intraday/_PENDING_文件修改日志_append.md` 的内容
手工 append 到 `OH-Works/文件修改日志/2026-10-25.md` 末尾。
