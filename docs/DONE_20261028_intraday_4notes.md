# DONE 2026-10-28 · 盘中页 4 条标注（-34 / -35 / -39 / -40）

插件 `app/plugins/intraday` 升 `0.1.4`。只动本插件目录（含 tests），未改 engine/web、shell.js、其它插件、公共接口路径与返回结构，未重启 8810。

分支：`v2 面板` · 完成时间 2026-10-28 23:0x

---

## 1. N20261027-122604-34「成交额预估值出现严重失真，V1 版本没有这种情况」

**结论：已修（显示层）。预估值本身的产线口径不在本插件边界内，见文末「遗留」。**

### 根因

两件事叠在一起，读起来必是「失真」：

1. **收盘后仍在出「预估」**。产线 `engine/executor/tick_feed.py:731` 的 `forecast, fmethod = forecast_full_day(total, pm, w_by_hm, None, prog)`，在 15:00 后 `pm='15:00'`、`w=1`，于是 `forecast == total_amount`，而 `estimated` 仍为 `true`。卡上就是「预估今日全天（估算）1.63 万亿 100%」——一个「预测」等于已发生的事实。
   本次实机（`/api/intraday.json` @ 2026-10-28 15:29:59）：`total_amount = forecast_full_day_amount = 1,628,487,548,928`、`progress = 1.0`、`estimated = true`。
2. **括号里的百分比不是预估用的分母**。`progress` 是**时间进度**（`intraday_collector.py` `trading_progress()`：9:30–11:30 与 13:00–15:00 共 240 分钟）；而 `estimate_method='profile'` 时预估 = 累计 ÷ **量能进度** `w(t)`（近 20 日同刻累计占全天中位数）。
   10-27 12:26 那条标注的原文数值可反推：当前累计 8774.6 亿 / 预估 1.52 万亿 / 括号 50% —— 按 50% 反推应是 1.75 万亿，差 15%，三行数字对不上。`w = 8774.6/15200 ≈ 0.577`，与 50% 不是一个东西。

旧页 4699（= V1）写的就是 `num(to.progress*100,0)`，所以这不是「V1 没有、v2 才有」的代码差异，而是**口径混用在两种实现里都成立**；这次按主人的标注把显示改对。

### 改法

`app/plugins/intraday/frontend/audit.js`：

- 新增 `isClosed(t)`（:197）：`progress >= 1` 即收盘态（只认载荷自报，不按本机时钟判，历史快照同样成立）。
- 新增 `forecastRowHtml(t)`（:213）：分母跟着「预估用的是哪一个」走——
  `estimate_method === 'profile'` 时由 `total_amount / forecast_full_day_amount` 反解 `w`，显示「量能 58%」（title 写明公式）；`linear` 或无此字段时显示「时间 50%」。反解只是公式 inversion，不重算预估、不新增口径。
- `turnoverKvHtml`（:172）：收盘态**不出预估行**，两行改叫「今日全天 / 昨日全天」，都取 880005（与「当前累计」同源，不自相矛盾）。

### 验证

- `test_audit.py::test_turnover_compare_three_rows`（重写）：盘中 profile → 3 行 + `量能 58%`；盘中 linear → `时间 50%`；收盘 → 2 行、无「预估/估算/100%」、今日全天读 880005；非 1m 真值仍「无数据」。**通过**。
- 实机（CDP，收盘态）：`_vis/verify_20261028/freeze_01_turnover.png`，两行「今日全天 1.64 万亿 / 昨日全天 1.46 万亿 +11.47%」，无「(估算)」。`close_freeze.cdp.mjs` 6/6。

---

## 2. N20261027-234539-35「每次打开都会有一定时间的延迟…收盘期间没必要刷新…16:10 之后直接取收盘值一次算完」

**结论：已修。先量化，再改。**

### 量化（改前，CDP 实测，点侧栏「盘中」那一刻起）

| 指标 | 改前 | 改后 |
|---|---|---|
| 冰点卡出现数值 | 700 / 712 / 1272 ms | 77 / 79 / 401 ms |
| 成交额对比出现金额 | 同上 | 同上 |
| 板块轮动 / 日内演化成形 | 1267 ms | 626–1170 ms |

同拍接口耗时：`/api/panic` 12–27ms、`/api/amv_live.json` 4–9ms、`/api/breadth_880.json` 38–49ms、`/api/intraday.json` 6–28ms；`/api/p/intraday/summary` 161–255ms（每次读 10MB 轨迹文件）、`/api/sector_track.json` 360–1535ms（2.88MB）。

**不是「重新计算」**：冰点接口本身 27ms 就回来了，是卡在等慢块。

### 根因

`frontend.js` 的 `load()` 把 5 路快请求 + 慢块（`loadSpark` / `loadSector`）全部 `await Promise.all` 完才 `render()` 一次 —— 首屏被 2.88MB 全量轨迹（浏览器下载+解析）和 10MB 轨迹文件读取卡住。

### 改法

`app/plugins/intraday/frontend.js`：

- `load()`（:871）拆两段：① `amv_live / panic / breadth_880 / intraday` 四路到齐就 `render()`（:893）；② `/summary` + `sector_spark` + `sector_track` 到了再 `render()` 一次。失败仍只降级自己那一块。
- 新增 `inAutoRefreshWindow(now)`（:59）/ `isCloseFrozen(now)`（:68）：**交易日 09:30–16:10 才自动取数**；`refresh()`（:995）在窗外直接返回（首挂载仍取一次，右上角「刷新」不受影响）。16:10 之后卡头（`.it-sub`）标「收盘定格 · 手动刷新」。
- 主人原话里「或者直接将它加入收盘后的自动化任务里」——那属于收盘批量任务，**不在本插件边界**，见文末「遗留」。

### 验证

- `test_intraday.py::test_close_freeze_window`（新增，node 跑 `tests/js/close_freeze_window.mjs`）：盘前 09:00 不刷、10:00 刷、15:10/16:00 刷、16:30 停、21:30 停、周六停；「收盘定格」标注只看 16:10。**通过**。
- 实机 `tests/js/close_freeze.cdp.mjs`：16:10 后 45s 内 `/api/panic` 请求数 **1 → 1**（不再自动取数），`/api/sector_track.json` 全程 1 次；卡头含「收盘定格」。**6/6 通过**。
- 实机 `tests/js/mount_speed.cdp.mjs --repeat 3`：见上表（改后数据即此脚本输出）。

---

## 3. N20261028-140428-39「行情涨跌也需要跟 AMV 涨跌一样用全A 作为基准线」

**结论：已修。**

### 根因

`app/plugins/intraday/frontend/evo.js` `quoteChart()`（:337）只画勾选的指数，`all`（全A 活跃指数，即 AMV 模式那根主线）根本没进图；Y 轴范围也不含它。

### 改法

`evo.js`：

- `quoteChart()`：把 `snap.all` 作为**基准线**画进行情图（`class="evo-allbase"`，`var(--accent)`，先画、垫在指数下面），Y 轴范围纳入全A（:380-383）；末值标签「全A +3.04%」与指数标签同列按 Y 排序避碰（:397-408）。
- 不加图例开关：基准线不随勾选变化，不占「六指数」按钮位；它自身有末值标签，看得见。
- 鼠标/区间读数：`cursorRows` / `rangeRows` 两种模式都带这一行（行情模式叫「全A（基准）」，AMV 模式仍叫「活跃指数」）（:778 `baseLabel`）。

### 验证

- `test_evo_mode.py::test_quote_mode_has_all_baseline`（新增）：勾 1 个指数 / 一个都不勾，基准线都在。
- `test_evo_mode.py` 其余 11 项仍绿（勾选数、末值标签数、缺数据话术）。
- 实机 `tests/js/evo_dir_all.cdp.mjs`：`allbase=1`、末值标签含「全A +3.04%」、指数线仍是 2 条。截图 `_vis/verify_20261028/probe_11_quote_allbase.png`。

---

## 4. N20261028-140538-40「正负对比功能不可用」

**结论：未复现「点击无效」；已修两处能解释「看起来不可用」的可复现缺陷。**

### 复现结果（先说清楚没复现到什么）

实机（CDP，真实鼠标 `Input.dispatchMouseEvent`，非 `.click()`）：

| 检查 | 结果 |
|---|---|
| 三个方向按钮是否被浮层挡住（`elementFromPoint`，AMV 有预测浮层 / 行情无浮层两种模式） | 3×2 全部命中按钮本身 |
| 点「正负对比」后 `dir=all` 是否生效 | 生效（`aria-pressed=true`） |
| 榜单是否 5+5、有正有负 | 10 行，+3.63‰…+1.09‰ / −1.20‰…−0.55‰ |
| 贡献图曲线 | 10 条 |
| console 异常 | 0 |

即：数据链路、事件委托、命中测试都正常。**「按钮点了没反应 / 报错 / 数据空」三种表现都没复现出来。**

### 修掉的两处（能解释「不可用」的观感）

1. **切卡后视角重置**：shell 切走再回来会 `unmount → mount`（实测离开后 `.evo-body` 计数 0、回来重建），`createState()` 每次从默认值开始，主人选的「正负对比 / 行情涨跌 / AMV 排序」全丢，回到正向 —— 从使用者角度看就是「我明明点了」。
   改法：`evo.js` 模块级 `VIEW`（:116）只存视角四项（dir/mode/srt/idxOn），`createState` 恢复、`setMode/setDir/setSrt/toggleIdx` 回写。
2. **5+5 没有分组标**：十个板块排成一列，和「正向」几乎看不出区别，点完像没生效。
   改法：`rankHtml`（:475）在 dir=all 时插入「正向 / 负向」两条分组标（`.evo-rank-sep`，样式在 `card.css.js`）。

### 验证

- 实机 `tests/js/evo_dir_all.cdp.mjs`：真实鼠标点击生效、分组标 `正向,负向`、切卡再回来 `dir=all` 保持。**18/19 通过**，唯一不过项是 `favicon.ico 404`（与本条无关，见「遗留」）。
- 截图：`_vis/verify_20261028/probe_14_realclick_all.png`（分组标可见）。

**若主人当时看到的是别的表现，需要一句话补充**：是「点了图表没变」还是「榜单空了/报错」。现有证据只能覆盖上面这些。

---

## 遗留 / 未修（不在本次边界，交主会话裁）

1. **预估值本身的产线口径**（判别 1 的上游）：`engine/executor/tick_feed.py:731` 用 `w_by_hm`（近 20 日量能剖面中位数）除，`engine/collectors/intraday_collector.py` 的 `forecast_full_day()`（约 :466）决定 profile/linear 回退。若主人看到的是「数字本身偏」，那要动 engine（白名单外）。本次只保证卡上三个数字**能被读者对上**。
2. **收盘后的自动化任务**（判别 2 的后半句「直接将它加入收盘后的自动化任务里」）：需在收盘链路加一步「16:10 后跑一次收盘定格取数」，属 engine/调度域。
3. **旧页对齐测试的红灯**（不是本次引入）：`test_audit.py` 5 项、`test_evo.py` 2 项、`test_quotes.py` 3 项、`test_research.py` 1 项，共 13 项，全部 `_node(*_align.mjs)`，原因是 `engine/web/index.html` 行号漂移（`cls is not defined` / `Unexpected token 'const'`）与 10-27 那批有意偏离没同步到 strip 规则。本次顺手把 `evo_align.mjs` 的旧页切片修到能跑（`SPANS` 2843–2847），其余未动。
4. **`/favicon.ico` 404**：页面每条 console 报一条，非本插件（shell 侧）。
5. **`test_fe_shell_bridge.py::test_undefined_tokens_not_used`**：`sector/frontend/board.js` 用了未定义的 `--mono`，其它插件，未动。

---

## 改动文件清单

产品代码（4）

- `app/plugins/intraday/frontend.js` — 两段式 load、自动取数窗口、收盘定格标注、plugin 版本号引用
- `app/plugins/intraday/frontend/audit.js` — 收盘两行、预估行分母
- `app/plugins/intraday/frontend/evo.js` — 行情全A 基准线、正负对比分组标、视角状态保留
- `app/plugins/intraday/frontend/card.css.js` — `.evo-rank-sep`
- `app/plugins/intraday/plugin.json` — 0.1.3 → 0.1.4

测试（7）

- `tests/test_intraday.py` — 版本断言 + `test_close_freeze_window`
- `tests/test_audit.py` — `test_turnover_compare_three_rows` 重写
- `tests/test_evo_mode.py` — 新增 `test_quote_mode_has_all_baseline`
- `tests/evo_mode.mjs` — 路径/标签计数剔除基准线，补 allbase；缺数据三例注入 `now`（原断言随机器日期漂）
- `tests/evo_align.mjs` — 旧页切片 2842→2843（`cls` 掉出切片）；比对前剔 `evo-allbase`
- `tests/js/evo_dir_all.cdp.mjs`（新）— 39/40 实机取证
- `tests/js/mount_speed.cdp.mjs`（新）— 打开耗时
- `tests/js/close_freeze.cdp.mjs`（新）— 收盘定格 + 45s 不刷新
- `tests/js/close_freeze_window.mjs`（新）— 窗口口径单测载体

---

## 复跑

```
cd projects/panel-v2
..\..\envs\qmt312\Scripts\python.exe -m pytest app/plugins/intraday/tests -q
node app/plugins/intraday/tests/js/mount_speed.cdp.mjs --repeat 3
node app/plugins/intraday/tests/js/evo_dir_all.cdp.mjs --shots _vis/verify_20261028
node app/plugins/intraday/tests/js/close_freeze.cdp.mjs --wait 45 --shots _vis/verify_20261028
```

本次结果：`109 passed, 13 failed`（13 项均为上文遗留 3 的既有红灯；改前基线是 `101 passed, 19 failed`，本次修好 6 项、新增 2 项，**0 项新红**）。
