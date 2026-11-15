# 盘中卡 · 2026-10-26 第三轮：删两块重复内容 + 全A 摘要并入左图 + 四象统一

> 上一轮：`HANDOFF_20261026_intraday_axis_rank_pick.md`（固定时间轴 / 10 行榜 / 点榜跳研究 / 忙碌态）。
> 本轮白名单不变：只动 `app/plugins/intraday/frontend/` + `tests/`。

## 一、删【板块轨迹明细】整卡（主人批「重复的」）

理由（主人给的，成立）：
- 领先/落后双榜 ≈ 日内演化右榜（同源同口径，都是 `sector_track` 的板块贡献）；
- 下方 6 张逐码脉冲小图 ≈ 板块轮动网格视图（每格当日走势）。

落地（`frontend.js`）：
- 摘掉 `secTrace` / `traceBody` 节点、`wrap.append` 里的位置、`S.el` 两项、`render()` 里的 `setHtml(traceBody…)`；
- `loadPulse()` 不再按 20s 节奏调用（逐码脉冲没宿主了，`/api/sector_intraday.json` 不再空跑）；
- **代码保留可回退**：`trackHtml()` / `pulseHtml()` / `pulseCard()` / `trackSpark()` / `amvHtml()` / `loadPulse()` / `pulseCodes()` 全部还在文件里，`tr.it-row` 的钉住委托也留着（现在是死路径）。
- 块序变为：冰点 → 会话行 → 实时行情 → [两市成交额 ｜ 板块轮动] → 日内演化 → 板块与个股研究 → 持仓实时状态 → 审计明细 → 来源行。

## 二、删「持仓实时状态」的 AMV 摘要段

- `holdBody` 现在只写：`<div class="it-split-hd">持仓（/api/amv_live.json · 只读）</div>` + `holdHtml(S.amv)`。
- 区头 note 从句尾去掉「· 上段 AMV 摘要为 v2 增量（旧页该卡仅持仓表）」。
- 实测：持仓区 `.it-amv` / `.it-idx` / `.it-list` 全为 0，只剩 1 张表。

## 三、全A 摘要吸收进「日内演化」左图（主人原话：「这些数据可以吸收叠加到活跃市值的图上」）

两组数据 → 左图（活跃指数图）下方、六指数图例上方的一行信息：

| 项 | 取值 | 来源 |
|---|---|---|
| 全A 起点 | `summary.first_all` | `/api/p/intraday/summary`（原轨迹卡同一份取数） |
| 末值 | `summary.last_all` | 同上 |
| 日内变化 | `summary.all_delta`（**直取，不自己相减**） | 同上 |
| 跑赢 / 跑输 | `summary.strong` / `summary.weak` | 同上 |
| 时段 | `all_series` 首末 `t` | 同上 |
| 峰值 / 谷值 | `all_series` 过滤 null 后 max / min（与删之前 `trackSpark` 同算法） | 同上 |

- 实拍 2026-10-23 存档：`全A 起点 -1.42% ｜ 末值 +5.08% ｜ 日内变化 +6.49pp ｜ 跑赢/跑输 27 / 63 ｜ 时段 09:30 → 15:00 ｜ 峰值 5.14% ｜ 谷值 -2.00%`，与接口真值（-1.4171 / 5.0754 / 6.4925 / 27 / 63 / 5.1375 / -1.9969）逐项一致，也与主人截图那两行数字一致。
- 样式：`.evo-info`（`card.css.js`）—— 0.6875rem、`--dim` 色、等宽数字、`flex-wrap` 自适应；实测高 36px（两行内），不压图。涨跌色走 `clsUp()`（红涨绿跌，东财口径）。

## 四、统一：四象点板块也跳研究卡

- `frontend/sector.js`：
  - `.st-ql`（右侧标签组）与 `.st-cell`（网格格）：去掉 `data-open-amv` / `data-open-name`，改 `data-act="st-goto"` + `data-code`；
  - `createState(cardEl, bodyEl, hooks)` 新增 hooks；`click` 委托里 `st-goto` 分支**先于**钉住分支并 `return`；
  - 说明行「点标签看工作台详情」→「点标签跳到下方研究卡」。
- `frontend.js`：抽出共用 `const pickBoard = (code) => { rsSet(sel) + 滚研究区头 }`，同时传给 `sector.createState` 与 `evo.createState`。两个模块同一行为，不各写一份。
- **钉住交互未动**：气泡 `.st-bub` / 尾迹 `.st-line` 点击仍然只钉住（可多钉、再点取消），hover 浮层 / 引导线 / 就地标签全部原样。实测：点气泡后 `.st-bub.on` 出现该板块，且研究卡选中行不变（不误跳）。

## 五、验证

| 项 | 结果 |
|---|---|
| `pytest app/plugins/intraday/tests app/tests -q` | **258 passed / 1 skipped**（本轮前 255/1，净 +3） |
| 真浏览器探针 `Hubagent/01/_vis/_c2e_verify.js` | **19 / 19 PASS**（真实鼠标） |
| 区块标题列表 | 冰点 / 实时行情 / 两市成交额 / 板块轮动 / 日内演化 / 研究 / 持仓 / 审计 —— 无「板块轨迹明细」 |
| 轨迹卡残留 | `.it-trace-host` `.it-track` `.it-pulse` = 0 |
| 持仓卡 | `.it-amv`=0 `.it-idx`=0 `.it-list`=0，表格 1 张 |
| 信息行 | 7 项与 `/api/p/intraday/summary` 逐项对上，位于 svg 下、`.evo-lg` 上方，高 36px |
| 四象 | 零 `data-open-amv`；标签 20 个 / 网格格 24 个全带 `st-goto`；点标签 → 研究卡选中 `881277.TI`、区头 top=132、页签 13→13（不弹详情）；点网格格 → 选中 `881171.TI` |
| 钉住 | 点气泡 → `.st-bub.on` 含该码，研究卡选中不变 |

新增测试 3 条：
- `test_intraday.py::test_trace_card_and_amv_summary_removed`（编排里零 `secTrace`/零 `amvHtml(S.amv)`/块序，且函数本体仍在）
- `test_intraday.py::test_sector_click_goes_to_research`（sector 零 `data-open-amv`、两处 `st-goto`、`st-goto` 先于钉住、共用 pickBoard）
- `test_evo.py::test_evo_all_info_row_absorbed_from_trace_card`（7 项数字 + 不占位 + 位置在图例上方）

## 六、遗留 / 待主人定

1. **四象气泡单击仍是「钉住」**（不跳研究卡）。理由：主人明确「别破坏原有钉住交互」，且气泡本来就不弹详情（弹详情的是标签和网格格）。若要让气泡也跳，最省事的做法是「单击钉住不变，双击跳研究卡」，或把钉住挪到 Shift+点击 —— 一句话就改。
2. 信息行在 605px 左栏里会折成两行（36px）。若主人希望严格一行，可减「时段」或把「跑赢/跑输」并到「日内变化」后面。
3. 仍没验：盘中实时增量（边跑边来新点）、窄屏 <1100px 排布。
4. `_relayout_verify.js`（第二轮探针）里针对轨迹卡 / AMV 摘要的断言已随卡删失效，文件头已标注以 `_c2e_verify.js` 为准。
