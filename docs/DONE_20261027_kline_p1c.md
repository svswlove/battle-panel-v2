# K 线弹窗 P1c：周期补齐 + 副图 + 十字线（2026-10-27）

> 负责人：01（执行器）｜权限 write，白名单见任务书｜8810 换血由主人统一做
> 回退：HANA 文件历史；无 `.bak`、无新依赖、未启停任何常驻服务、未改后端

## 0. 结论先说

- 周期从「分时/日线」扩到旧版全套 8 档，数据走已部署的 compat 绝对口 `/api/kline/{code}?period=...`。
- 副图做了 5 项：成交额、活跃市值（仅日线）、KDJ、MACD、RSI；**主力动向、砖形图未做**（原因见矩阵）。
- 十字线 + tooltip 已上，坐标走既有唯一轴（`dailyAxis` / `intradayAxis`）。
- 新增两个文件：`frontend/indicators.js`（纯计算）、`frontend/subcharts.js`（canvas 渲染）；`marks.js` 未再加算法/绘图。

## 1. 旧版功能矩阵（**不能读成「已全对齐」**）

| 旧版能力 | 状态 | 依据 / 缺口 |
|---|---|---|
| 日线 d | ✅ 已实现 | compat `/api/kline?period=d`，实测 `adj=front`、含 yellow/white/amv |
| 周线 w | ✅ 已实现 | 同上，`amv` 全 null（日序数据不聚合，与旧版一致） |
| 1/5/15/30/60 分钟 | ✅ 已实现 | compat `m1/m5/m15/m30/m60`，实测各 200、241 根可取 |
| 分时 t | ✅ 保留 | 仍走插件 `/intraday/{code}` |
| 周期持久化 | ✅ 已实现 | `klm_period_v1`；未知键/坏值回退分时 |
| 成交额副图 | ✅ 已实现 | `bars[].amount` 原样透传（不换单位） |
| 活跃市值副图 | ✅ 仅日线 | `bars[].amv/amvMa/amvWhite`；非日线禁用并写明「仅日线有活跃市值」 |
| KDJ | ✅ 已实现 | RSV 9 / K,D 平滑 3（`SMA(X,3,1)`），与独立参考实现对拍 |
| MACD | ✅ 已实现 | 12/26/9，柱=2×(DIF−DEA) |
| RSI | ✅ 已实现 | 6/12/24，Wilder 平滑 |
| 十字线 + tooltip | ✅ 已实现 | 时间/开高低收/涨跌/量额 + 当前副图值；移出即清 |
| **主力动向 mf** | ❌ 未做 | 数据**存在**（`/api/kline` 顶层 `mf.{主力资金,主买,占比,Q}`，按 lookback 天给数组），但要与 bars 逐日对齐、且旧版口径含「主买细柱 + 双占比 + Q」四层；本档时间不够，未接，也不画假柱 |
| **砖形图 brick** | ❌ 未做 | 旧版是库内置 Renko 类指标，本地无现成算法与对拍样本；自己发明一套等于造口径，按「不伪造」不做 |
| 主图指标（MA/BOLL/超级趋势/缠论） | ❌ 未做 | 本档任务范围是副图 + 周期；主图指标属下一档 |
| 副图窗高拖拽 / 双模板 / 排序 | ❌ 不做 | 任务书明确排除 |
| 多日分时（intraday_multi）叠加 | ❌ 未做 | 任务书未要求，且叠加只做当日 |

## 2. 周期（A）

| 项 | 实现 |
|---|---|
| 档位 | `intraday / d / w / m1 / m5 / m15 / m30 / m60`（键名沿用旧版 PERIOD_KEYS，t→intraday） |
| 取数 | 绝对 `GET /api/kline/{code}?period=<d|w|m1…>&limit=240`（compat 已部署口，**未新增后端路由**） |
| 字段 | 实测：`t/o/h/l/c/v/amount/yellow/white/amv/amvMa/amvWhite/turnover/amp/chg` + 顶层 `adj/source/prev_close/mf/indicators` |
| 复权 | **实测后端对所有周期都返回 `adj: "front"`**，参数 `adj=raw` 被忽略（已用 `?adj=raw` 试过，仍 front）。故界面按 payload 原样显示「前复权」，**不自行宣称「分钟不复权」**；这是与任务书预设不同的事实，记在此处 |
| 日线背景 | 日线仍取 `/api/p/kline/bg/{code}` 拿 AMV 四态带；其它周期不请求 |
| 指数 `.TI` | `.TI` 码只有日/周（compat `source=ths_index.db`）；分钟档**直接给可见原因**「板块指数只有日线/周线，无分钟数据」，不把身份码送去 eltdx 通道 |
| 坏值 | `readPeriod()` 校验 key 在 PERIODS 内，否则回退分时；localStorage 异常有兜错 |

## 3. 副图（B）

- 容器：`.klm-subs` 在主图下方，一图一 canvas（88px 高），**横轴复用主图唯一轴**（`axis.xAt(i,n)`），纵轴各自 min/max（副图固有的第二纵坐标，不是第二套时间轴）。
- 开关：右键 → 副图 ▸ 五项勾选；key `klm_subs_v1`，只认已知键的布尔值，坏 JSON 回退默认（默认只开成交额）。
- 不可用项：`disabled` + 原因行（AMV 非日线 / 分时无副图数据）。
- 空数据：`subcharts.js` 内先判「序列有没有有效值」，没有就写一行「不可用：…」，**不画 0 线**。
- 指标口径见 `indicators.js` 头部注释：KDJ `SMA(X,N,1)`、MACD 2 倍柱、RSI Wilder。

## 4. 十字线 + tooltip（C）

- 独立 `.klm-cross` 图层 + `.klm-tip`，`pointer-events:none`，不抢右键/点击。
- 索引由共享轴反推：`indexAtX()` 用 `axis.rect` 线性反算，落点用 `axis.xAt(idx,n)`；y 夹在 `rect.t..rect.b`。
- 内容：日线/分钟显示 时间 + 开高低收 + 涨跌% + 量 + 额 + 开启中的副图值；分时显示 价/均/量/额。
- 移出画布即清空（`mouseleave`）。

## 5. 测试与实测

```
pytest app/plugins/kline/tests -q                  → 47 passed
node tests/js/p1c_indicators.test.mjs              → 14 项全过
node tests/js/p1a_marks.test.mjs                   → 52 项全过（P1a/P1b 未回归）
```

- 指标对拍：KDJ/MACD/RSI 各自与**独立参考实现**（测试内重写一遍）逐点对拍，覆盖首值、预热 null、全平（中性 50 / 收敛 0）、样本不足全 null。
- 渲染：`drawSub` 用记录型 canvas 断言「成交额每根都有柱」「MACD 有柱」「KDJ/RSI 有折线」「全 null 时零柱 + 一行原因」。
- 静态守卫：周期档齐全、两把 localStorage key、`.TI` 分钟档给原因、副图清单固定、AMV 限日线、十字线走 `scaleFor`、后端路由集合未变。
- 一次性浏览器检查（临时静态服务 + 桩数据，验完已停服务、删目录）：
  - 日线：`日线 · 前复权 · eltdx`、120 根；副图默认 `[amount]`，勾 KDJ 后 `[amount, kdj]`；
  - 十字线：tooltip 显示 `2026060 开 116.03 高 122.03 低 114.03 收 118.03 涨跌 -0.41% 量 1,000,060 额 106,000,000 KDJ 57.58 65.00 42.72`，移出后 `display:none`；
  - 切 5 分：`5分 · 前复权 · eltdx`，副图保留；`klm_period_v1=m5`、`klm_subs_v1={"amount":true,...,"kdj":true,...}` 已落盘。

## 6. 未验证 / 已知限制

1. **主力动向、砖形图未做**（见矩阵）：不做不给假图，菜单里也没有入口。
2. 真实 8810 上的观感（8 个周期按钮在窄窗是否挤、副图窗高是否够看）未在真机验；等换血后看。
3. 分钟档前复权是**后端事实**（`adj=front`），若主人要「分钟不复权」，得后端开口径，不在本档白名单。
4. 十字线只做主图，副图内没有十字线（旧版副图也不联动）；tooltip 不跟随鼠标纵向位置，固定贴顶部，避免遮挡 K 线。
5. 周期切换有 `runChart` 的既有竞态保护（旧响应靠 `st.closed` 拦），本轮**没有**为周期切换加序号：快速连点可能有一次旧周期覆盖新周期；已记录，未修（不属本次打回项，且需重做 runChart 的请求编排）。
6. `.TI` 分钟档只有「日/周可用、分钟给原因」；未做把板块指数映射到 `.SH` 再取分钟（行业叠加那套映射只用于叠加，不用于主图，避免把主图身份码改掉）。

## 7. 文件面

| 文件 | 变更 |
|---|---|
| `frontend/indicators.js` | 新建（纯计算：sma/ema/smooth/kdj/macd/rsi/amounts/amv） |
| `frontend/subcharts.js` | 新建（canvas 渲染 + SUB_DEFS 清单） |
| `frontend/overlay.js` | 周期档/取数分支、副图容器与菜单、十字线 tooltip、两把 localStorage key |
| `frontend/marks.js` | 未改（本档没往这里加算法或绘图） |
| `frontend.js` | 未改（`drawDaily` 已能吃 candles；日线轴仍是唯一源） |
| `tests/js/p1c_indicators.test.mjs` | 新建 |
| `tests/test_kline_overlay_p1a.py` | 追加 P1c 守卫；文件面白名单更新为 4 个 JS |

---

# 验收返修（第二轮，2026-10-27 05:3x~06:2x）

> 打回 5 项：① 指标不是旧版公式 ② toChartBars 漏 amvMa/amvWhite + kind 不一致 ③ 无请求序号、先画后取数
> ④ mf 不许留缺口 ⑤ 分钟复权文案误导。砖形图按指示保留缺口。

## F1. 指标改为旧版等价移植（`kline_lc.js` 465–515 为权威）

| 指标 | 旧版公式（原文） | 上一版（错） | 本轮（已改） |
|---|---|---|---|
| RSV/Si | `(close-LLV(low,n))/(HHV(high,n)-LLV(low,n)+1e-4)*100`，i<n-1 为 null | 自写近似 | 照抄，含 1e-4 常数 |
| KDJ | K=((K_prev==null?50:K_prev)*2+RSV)/3；D 同理；J=3K−2D；**无 9 根预热** | 9 根预热 + K 从 RSV 起手（首值≈RSV） | 照抄，首值 =(100+RSV)/3 |
| stoch | Mn：HHV 用 **close** | 未实现 | 已出 stochShort/stochLong，与 J 同副图 |
| MACD | 首根 EMA=close 起递推；hist **原始 = DIF−DEA**（buildSubs 才 ×2） | 26 根预热、柱×2 | 照抄；另给 `hist2` 供需要时用 |
| RSI | `gain/loss*100`（Qt 平滑，可 >100），i>=period 才给值 | gain/(gain+loss) 的 Wilder 版 | 照抄比值口径 |

测试改为**照抄旧版函数**（测试内独立实现 Si/Mn/Qt/Dt 与 Ri/Ti 循环）逐点对拍，含首值、预热边界、
其余全平、单根；不再用「自定近似」当基准。

## F2. toChartBars 与图形 kind

- 补 `amvMa` / `amvWhite`（首版只传 `amv`，AMV 副图均线必然为空）。
- 图形 `kind` 统一为 `daily`（`period` 字段单独区分 d/w/m1…），`scaleFor`、`indexOfPoint`、
  副图判据全部按 `kind==='daily'` 走；不再出现「candle 走不到日线轴」的断点。
- 浏览器实测：AMV 副图 canvas 有 3476 像素落笔（三线都在），mf 副图 20515 像素（主柱+细柱）。

## F3. 请求序号 + 先取数后绘图

- 新增导出 `latestGate()`（单调序号闸门），`runChart()` 每次领号；`loadCandle`/分时分支**不再自己画**，
  统一在 `runChart` 里 `isLatest()` 通过后 `renderChartNow() → drawMarks() → renderSubs() → paintFooter()`。
- 切周期时 `chartGate.invalidate()`、`sseq++`、`oseq++`，并立刻清空副图容器（不留上一周期旧副图）。
- `runSides` 用 `sseq` 保护（盘口/筹码晚到不覆盖新周期）；F10 接口未动。
- 4 条可控 promise 用例：慢 d 后快 m5、慢 m5 后快 d、最后一次失败、invalidate 全作废。
- 浏览器实测：先点 d（桩延迟 400ms）再点 m5（20ms），最终徽标 `5分 · 不复权（compat 原始分钟） · eltdx`，
  副图自动降为 `[amount]`，无 d 的旧数据回写。

## F4. 主力动向 mf（不再算缺口）

- 真实 GET `/api/kline/600519.SH?period=d&limit=240&mf_lookback=120`：`mf` 含 `主力资金/主买/占成交/占流通/q`
  **各 240 个值，与 bars 等长**；`limit=5` 时后端仍回 120 根与 120 个值 ⇒ 旧版 `k1[i]`↔`bars[i]` 的下标对齐成立。
- 实现：`indicators.mf()` 校验「数组齐全 + 长度等于 bars」，否则整块不可用并给原因（**不做右对齐猜测**）。
- 副图四层：主力资金柱（红/绿）+ 主买×0.7 细柱 + 占成交/占流通/Q 数值（写入标题行）。
- 仅日线可用；分钟档菜单项 disabled 并写「仅日线有主力动向数据」（浏览器实测确认）。

## F5. 复权文案

- 源码口径：compat `build_kline` 对分钟 `adjust=None`，payload 的 `adj:'front'` 是**标签 bug**（不改后端）。
- UI 按取数路径纠正：分钟档显示 **「不复权（compat 原始分钟）」**，日/周显示 payload 的「前复权」。
- 浏览器实测徽标：`5分 · 不复权（compat 原始分钟） · eltdx`。

## F6. 复跑与本轮缺口

```
pytest app/plugins/kline/tests -q                  → 47 passed（基线保持）
node tests/js/p1c_indicators.test.mjs              → 20 项全过（16 指标/渲染 + 4 竞态）
node tests/js/p1a_marks.test.mjs                   → 52 项全过（未回归）
```

- **砖形图仍缺**：按指示保留缺口；旧版 `Ni()` 有公式（Qt 4/6/6 平滑 + 阈值），但无对拍样本，
  自造样本等于自定口径，故不做、也不给入口。
- 主图 MA/BOLL 不在本档。
- 十字线 tooltip 在 d/w/m* 都走同一套（未单独复测分钟档 tooltip）。
