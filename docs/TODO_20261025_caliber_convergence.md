# 口径收口清单（2026-10-25）

来源：嗅嗅审计（`AUDIT_metric_duplication_20261025.md`）+ 01 独立核实。
本文件是**待执行清单**，每条都已核实到文件:行。

---

## A. 已完成（2026-10-25 当天）

| # | 问题 | 状态 |
|---|---|---|
| A1 | `amv_regime.sma` 遇 None 抛 TypeError，导致 4 处消费端各自置 0.0 | 已改：`sma` 自己吞 None |
| A2 | `stock_state_series` EMA 种子 `e1 = e2 = vals[0]`，首日缺失污染全部历史 | 已改：种子取第一个有效值 |
| A3 | `compute_regime` 缺失日拿 None 参与比较 | 已改：缺失日两轨都不出信号 |
| A4 | 4 处置零：`compat_sector:212`、`compat_watchlist:467`、`compat_kline:1127`、`:1146` | 已改（A1 后不再需要预处理） |
| A5 | 守门测试 | 已加 6 项，含静态扫描禁止再写置零 |

**实测影响**：全市场 1073 只有缺失的票、历史累计分歧 27001 天；最新交易日两边都有值但结论相反 8 只
（000668/002667/300082/301716/603183/603759 由「多」变「空」，002388/002860 由「多」变「偏多」）。
**验证**：324 passed；无缺失的 800 只无回归；8810 上 18 插件全健康。

---

## B. 待执行（按影响面排序）

### B1 权重 Δ 量纲错 100 倍 —— 高危，硬错误

**事实**（已核实）：
- 落库 `share = sector / total`（`夏天-activity/工具/amv_store.py:369-371`），**小数**
- 落库 `d1/d5/d20` 也是小数（`delta_matrix:285-290`，不乘 100）
- `sector:792/825/861` 保持小数 ✅
- `sector:1226` `round((r["share"] - base) * 100, 4)` ❌ 变 pp
- `compat_sector:247-259` 同样 ×100 ❌

**同一接口里混用两种量纲**，前端按一种解释就会错 100 倍。

**修法**：
1. 新增 `app/lib/weight_delta.py::weight_delta_pp(share, base)` → 入参小数、出参 pp，docstring 写明
2. `sector:1226` 与 `compat_sector:259` 改调它
3. **优先用落库 `d{k}` 列**，不再现算（`d1/d5/d20` 已是权重 k 日差的正本，四处理里三处在重算已有列）
4. 删掉 `compat_sector` 的 `amv/total` 现算（省一次聚合）

**影响接口**：`/api/p/sector/rank*`、`/rank_recent`、`/minute_rank`、`/api/sector_card.json`、`/api/board_flow` 的 `weight_delta`

**命名陷阱**：`engine/executor/amv_live.py:645` 的 `amt_share` 是**成交额占比**，与 `share`（AMV 权重）不是一个东西，收口时别并。

### B2 `sector:446` 删元素导致日期前移 —— 高危

**现状**：`if r["amv"] is None: continue`，序列变短，四态与 K 线错位。
**修法**：保留位置传 `None`，窗口按日期对齐。
**注**：A1 改完后 `stock_state_series` 已能吞 None，直接改这一行即可。

### B3 `compat_sector:212` 仍在置零 —— 中危

`ma10 = sma([v if v is not None else 0.0 for v in avf], 10)`
**修法**：`ma10 = sma(avf, 10)`，整句删掉。

### B4 MA10 各变体的收敛 —— 中危，需要正本加变体

嗅嗅判定「改调 6 处、正本新增 1 个变体 + 1 个参数」。逐条：

| 处 | 判定 | 理由 |
|---|---|---|
| `lib/amv_regime.py:53 sma` | 保留（正本） | 已吞 None，唯一该存在的一份 |
| `compat_sector:212` | 改调 `sma` | 见 B3 |
| `compat_misc:666 _rolling_ma` | 语义保留、实现改调 `sma_expanding` | 「前 9 点不缺线」是老图的产品需求，合法；但它是扩张窗不是 MA10，不该借 `ma` 之名各写一份 |
| `sector:880-884` | 改调 `sma_expanding` | 与 `_rolling_ma` 同语义的第二份 |
| `amv/backend.py:60-63` | 改调 `sma_expanding` | 同上，第三份 |
| `compat_kline:552-560` | 改调 `sma`，但「缺口断线」要作为正本参数 | 现状「最近 10 个**位置**里有效值满 10」vs 正本「最近 10 个**有效值**」；有缺口时结果不同。断线更符合 K 线直觉，但那是显示策略，应由 `sma(vals, n, strict_positions=True)` 控制 |
| `sector:952-955` | **01 不同意删除**（见分歧记录） | 见下 |

**正本需新增**：
- `sma_expanding(vals, n)`：扩张窗口（前 n−1 点用已有点数平均），跳 None
- `sma(vals, n, strict_positions=False)`：加一个参数控制「缺口是否断线」

### B5 880 快照两份解析 —— 中危

| 维度 | `core/panic_calc.py:207-235` | `compat_sector:530-560` | 弥合 |
|---|---|---|---|
| 取值集 | last/open/high/pre_close（3 码一次） | last/open/high/amount（单码） | 共享函数取并集全返回 |
| 归一化 | 原始 float | `int(round())` 家数取整 | 共享层只做字段映射不取整 |
| 失败判据 | 有 code 就收 | last 或 open 非有限 → 整码弃 | 共享层不做有效性判断 |

**关键取舍**：共享「记录→字段」映射，**不共享「有效性判定」**。
理由：`panic` 的降级链是 `{}` → 回落 `panic_breadth.json` 序列库，收紧会在快照缺 `pre_close` 时误降级；
`compat_sector` 需要严判（家数取整前必须非空）。判据留在各自调用点是有意的差异。

**契约约束**：插件**不能** `from core`（§4），所以不能直接调 `panic_calc`。
唯一合法通道是 `ctx.lib`（`contract.py:916-973` + `paths.py:256-307 READABLE_LIB`）。

**落地三步**：
1. 新增 `app/lib/quote_snap.py`：`normalize_code(raw)` + `fields(rec) -> dict|None`（纯函数零 IO）
2. `paths.py:READABLE_LIB` 加 `"quote_snap"` 项；`panic_calc` 直接 import，`compat_sector` 经 `ctx.lib` + `plugin.json` 的 `data.lib` 声明
3. 两边删掉各自的 `getattr` 逐字段抄写

**更值钱的一条**：两者**各打一次 socket**。`panic_calc` 有 2.8s TTL + 单飞锁，`compat_sector._snapshot_880` **无任何缓存**。
盘中页同时请求 `/api/panic` 与 `/api/breadth_880.json`，同一组 880005/880006 取两遍。
只合并解析不合并取数，收益只有一半。

### B6 `_is_custom_board` 逐字复制 —— 中危

`compat:1090-1105` 与 `compat_misc:164-180` 逐字相同，且依赖外部工具目录。
**修法**：进 lib，删副本。

### B7 `review_chart.py` 同函数内复制 —— 低危

`731-733` 与 `752-755` 算同一组 MA，多打一次库。算一次复用。

### B8 `concept/tests` 扫描范围不全 —— 低危但该补

`test_concept_source.py:420-435` 只扫 `source.py`，`backend.py`/`render.py` 无扫描。
`compat:1090-1105`、`compat_misc:164-180` 用 `importlib` 按路径加载 `ths_board_pool.py`

### B9 `DUAL_WIN` 500 → 60（主人 2026-10-25 拍板：最多取 60 天）—— 中危，口径变更

**拍板**：窗口最多 60 天（主人原话：「最多取60天…即使只有144根K线，也不影响最新的显示」）。
**口径依据**（主人校准）：板块 AMV 多空 = MA10 单线制；「双轨」是穿越确认机制（防抖阈值），
不是两条均线；板块走势的双线是黄白线（黄线 = (MA14+MA28+MA57+MA114)/4，114 根起有值），
与 AMV 多空判定无关。
**理论差异预计≈0**：确认常态 10~20 天内完成（MA10 预热 9 天 + 同轨第二信号）；状态机无长记忆，
末值只依赖最后一次切换。两窗口末值不同 ⟺ 最后确认在 60 天前且之后零信号 —— 这种板块落到
`sector:952-955` 单轨回退，回退结果（今值 vs MA10）与延续状态天然一致。
**改法**：`sector/backend.py:39 DUAL_WIN = 500 → 60`（旧 8002 口径变更，需留痕）；
**验收**：改前对全部层板块跑末值基线，改后对拍，差异板块数应为 0 或个位且逐一列出核实。
与 C1（回退单轨去留）同批处理：窗口砍短后回退分支承接的板块变多，其存废影响变大。
（内含 `import sqlite3`），**扫描器看不见这条通道**。

---

## C. 分歧记录（01 与嗅嗅判断不同）

### C1 `sector:952-955` 回退单轨：嗅嗅建议删，01 认为应保留

**嗅嗅理由**：「正本算不出就自己再算一遍」是第二口径（`k=min(len,MA_N)` 又是一种扩张窗），
短序列该由 `compute_regime` 内部返回 None，不该在调用侧绕过。

**01 理由**：该函数 `_dual_bull` 的 docstring 明写
「窗口内无确认时回退单轨比较（不编造）」——这是**旧 8002 的既有行为**，属产品语义不是口径分歧。
删掉会让板块多空从「二态」变成「可能无值」，前端要处理新空态，是行为变更。

**待定**：需要主人判断——保留旧行为，还是接受空值。这条不能我替主人定。
**2026-10-25 更新**：已向主人解释语义（无确认/数据不足时退回「今值 vs MA10」粗判）；
与 B9（窗口 500→60）联动：窗口砍短后落回退分支的板块变多，删了它这批板块将显示无结论。建议与 B9 同批定。

### C2 距止损分级两套阈值（前端）—— 已拍板执行（2026-10-25 深夜）

**拍板**：只删档位文字标注（红·<5%/黄·<10%），距离条/止损线/距离列/整卡全保留。已执行
（engine/web/index.html 4 处：ST_CN 档位表、距离条后缀文字、loadPnl 标题规则文字、
loadNearTriggers 标题黄闪说明），静态验证通过，日志已登记。仪表盘侧完成；追踪池侧 `<1% 黄闪`
的分级在旧页其它处若仍有残留待收口时扫尾。

---

## D. 已核实但当前不会触发的问题

**`_rolling_ma` 的 TypeError 风险**：`sum(vals[lo:i+1])` 遇 None 会崩，
但实测 `sector_daily.amv` 和 `daily_total.total` **都是 0 个 NULL**（2756715 / 8229 行全满）。
所以当前不触发。B4 改调 `sma_expanding` 时顺带消除，优先级不高。

**`stock_amv.amv` 有 359011 个 NULL**（3078542 行中），这才是 A1-A4 的真实影响面。

---

## E. 执行顺序建议

1. **B2**（`sector:446` 删元素）—— 一行改动，接 A1 的尾，同批测试
2. **B3**（`compat_sector:212` 置零）—— 一行改动
3. **B1**（权重 Δ 量纲）—— 需先加 `weight_delta.py`，影响 5 个接口，改完要逐接口对拍
4. **B4**（MA10 收敛）—— 正本加 2 个变体，改调 6 处
5. **B5**（880 快照）—— 新增 lib 模块 + 白名单登记，涉及契约变更
6. **B9**（DUAL_WIN 500→60）—— 一行改动，但需全层末值基线对拍（差异应≈0）；与 C1 同批定
7. **B6 / B7 / B8** —— 低危，可批量收
8. **C1** —— 与 B9 同批（窗口砍短后回退分支承接面变大）；C2 已拍板执行完毕

每步的验收方式：改前跑一遍相关接口存基线，改后对拍逐字段相同（B1 例外，量纲修正后数值会变，要对拍的是「修正后与前端预期一致」）。
