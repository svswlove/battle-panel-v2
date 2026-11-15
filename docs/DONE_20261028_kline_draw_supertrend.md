# K 线：固定范围量分布 + 超级趋势单线（2026-10-28）

## 1. 固定范围量分布测量后不显示筹码（主人报）

**现象**：工具面板点「固定范围量分布」，在图上拖出框后只有一个空矩形，没有筹码柱。

**根因**：库 `fixed-range-volume-profile` 的分档函数读 `bar.volume`；
`DrawingManager` 默认 `bars()` = `candle series.data()`，而 LC 的 `data()` 不保留 `volume`
（蜡烛数据只有 OHLC）→ 每档成交量全 0 → 画出空框。

**改法**（`engine/web/kline_lc.js`）：
1. 创建 `DrawingManager` 时传自定义 `bars()`，从 `lastKd`（含 volume）映射成库要的
   `{time, open, high, low, close, volume}`；只在库重算 vp 时调用。
2. `sCandle.setData` 顺带带上 `volume`（LC 忽略未知字段，不影响 K 线）。

**验证**：实机拖框后 `ddState` 显示 `bars=240 / barsVol=62418352`，截图
`_vis/verify_20261028/draw_vp.png`：框内出红绿筹码柱 + POC/VAH/VAL 线。

## 2. 超级趋势：翻转处断开（主人 2026-10-28 定稿）

**演进**：先拆两条 series（翻转点补点）→ 重叠成两条；再改一条 series 逐点换色 → 翻转处有竖向连线。
主人看截图后定稿：**不需要强行连接，红绿交接允许断开**。

**最终实现**（`engine/web/kline_lc.js`，v0.8.5）：
- 根因：两条 series 靠 null 空点（whitespace）断线**无效**——实测空点仍把前后点连成一条长线，
  于是多头 series 跨过空头段、空头 series 跨过多头段，视觉就是两条并行（240 根就复现）。
- 改法：`supertrendSegs` 按 `dir` 切段，`supertrendSeriesList` **每段一个独立 series**（段内才有值），
  涨段绿 / 跌段红；段与段是不同 series，物理上不可能连成一条。
- 代价：series 数量随窗口内段数变化，翻页 preserve 校验不等时走全拆建（可接受）。

**验证**：002931.SZ 日线 240 根，同一时间只有一条阶梯线，翻转处断开。
截图 `_vis/verify_20261028/st_final_002931.png`；执行版本 `kline_lc.js?v=0.8.5`。

## 3. 顺带修的两条标注

- **43**（自选池右键板块详情被 K 线挡住）：`shell.js::overlayStack` 在 push/pop 时按栈序
  重排 z-index（100 起、每层 +5），后开的层盖住先开的层；K 线与板块详情两个方向都成立。
- **44**（刷新时间不要标到秒）：自选池卡头 `行情 <时间>` 改相对时间
  （刚刚 / N分钟前 / N小时前 / 天前 / 今天 / 昨天）。

## 4. 剩余标注（未做，下一批）

| id | 页面 | 内容 |
|---|---|---|
| N20261028-141425-41 | 自选池 | 副图改为个股 AMV + 10日线/白线 |
| N20261028-213150-45 | K 线 | 成交额柱体颜色调暗 |
| N20261028-213210-46 | K 线 | 阳线使用空心 K 线 |
| N20261028-202536-42 | 选股 | K 线默认看最新（本轮复测未复现，需用户给具体票/操作） |
| N20261028-140428-39 | 盘中 | 行情涨跌用全 A 作基准线 |
| N20261028-140538-40 | 盘中 | 正负对比功能不可用 |
| N20261027-122604-34 | 盘中 | 成交额预估值失真 |
| N20261027-234539-35 | 盘中 | 打开延迟 / 收盘后不必刷新 |

## 4. 后续批次（2026-10-28 晚）

- **45 成交额柱与 K 线一致**：阳线柱透明填充 + `drawHollow` 描边（`pal.up`），阴线柱实心 `hexToRgba(pal.down, 0.55)`；
  跳空不再单独标记（主图已有跳空信息）。截图 `_vis/verify_20261028/amount_hollow.png`。
- **46 阳线空心**：`candleOptions` 改 `upColor: rgba(0,0,0,0)` + `borderVisible: true` + `borderUpColor: p.up`；阴线保持实心。
  截图 `_vis/verify_20261028/hollow_candle.png`。
- **48a K 线右键菜单恢复**：`kline_order.js::chartContextCandle` 不再直接 return，恢复 `chartContext(ev)`；
  价格反解优先 `lcPriceAtY`（蜡烛）、回退 `hqPriceAtY`（分时）；菜单新增「加入/移出自选」。
  实测菜单项：加入/移出自选 ｜ 买入 ｜ 卖出 ｜ 条件单 ｜ 委托与条件单 ｜ 叠加对比 ｜ 分时标记 ｜ 刷新行情。
- **48b 条件单预警推送**：已派子代理只读调研（旧 push_queue 链路 + QMT 可用模块），产出
  `docs/RESEARCH_20261028_cond_push.md`，待回。
- **42（K 线默认看最新）**：本轮从选股页复测，打开即显示最新（截图 `sel_kline.png`），未复现；
  需主人给具体票/操作路径。

## 4.5 回踩均线 / 关键K 条件单（后端，2026-10-28）

**公式来源**（与作战表关键位同一套 `buy_module.signals.support_low_buy.select_levels`）：
- 黄线 = (MA14+MA28+MA57+MA114)/4；白线 = EMA(EMA(C,10),10)；多头排列（白>黄）才布；
- 动能K = 放量阳线（base>20 且 VOL>REF(VOL,1)×1.5），上沿=收盘、下沿=开盘；
  取最近 20 个交易日内最多 3 根、未破的。

**实现**：
- `orders.py` 新增 `ma_pullback_buy`（mode=ma_pullback）/ `key_k_break`（mode=key_k），
  参数 `target`（auto/yellow/white 或 auto/upper/lower）+ `tol`（0–5%，默认 0.5%）；
  评估只比阈值，行情字段 `yellow/white/ek_open/ek_close` 由 `cond_watch_v2.enrich_levels`
  调 buy_module 正本注入（不重写公式）。
- `paper/backend.py` 的 ConditionAddBody 加 `target`/`tol`；前端表单由子代理做
  （order_model.js / order_panel.js）。
- 测试：paper/orders 新增 3 项（两类触发 + 校验），paper+notify+cond_watch 共 192 passed。

## 5. 外派中的标注（子代理）

- 盘中页 4 条：34 / 35 / 39 / 40。
- 自选池 2 条：41（副图改 AMV+10日/白线）、47（打开速度）。

## 6. 验证与影响面

- pytest：`app/plugins/kline/tests`、`app/tests/test_v2_overlay_stack.py`、
  `app/plugins/watchlist/tests` 共 122 通过。
- 旧四模块 URL 加 `?v=V` 缓存串（`legacy_modal.js::MODULES`），避免浏览器继续用旧 `kline_lc.js`；
  装载顺序测试已同步剥离版本串。
- `kline_lc.js` 是旧页与 v2 共用文件，本次改动对旧页同样生效（volume 字段、单线、bars 注入）。
