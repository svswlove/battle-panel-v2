# HANDOFF · R1 intraday 盘中卡（新插件 intraday v0.1.0）

日期：2026-10-24 ｜ 任务书：`docs/task_R1_intraday.md` ｜ 契约：`docs/intraday_contract.md`
白名单内改动，未 commit、未重启 8810。

## 1. 结论

新建 `app/plugins/intraday/`（manifest + backend + frontend + tests），复刻旧盘中 Tab 五块：
冰点监控（恐慌广度）／AMV 实时摘要／广度 880／板块轨迹摘要／盘中脉冲。
**零后端改动**（core 与其它插件一字节未动），只新增本插件自己的 `/summary` 摘要端点。

验收：`python -m pytest -q` → **1152 passed, 4 skipped，exit 0**（本插件 25 项；数字含并发批次同时新增的测试，首轮跑时是 1096）；
`python tools\cutover_gate.py` → **PASS，exit 0**（7 项全 OK，n=16 插件）。
CDP 实测 `/v2/` 卡渲染无错误、五块全有数（详见 §5）。

## 2. 文件清单

| 文件 | 说明 |
|---|---|
| `app/plugins/intraday/plugin.json` | v0.1.0，kind=domain，mount=**plugin**（不是 root），卡 `intraday.main` order=15 |
| `app/plugins/intraday/backend.py` | `/health` + `/summary`（板块轨迹摘要，纯函数 `build_summary` 可单测） |
| `app/plugins/intraday/frontend.js` | 卡实现，34KB，零 import / 零裸 fetch / 零裸 hex |
| `app/plugins/intraday/tests/test_intraday.py` | 25 项：manifest、摘要语义、错误分档、静态禁令 |
| `docs/intraday_contract.md` | 数据契约 + 与旧端差异 |
| `docs/intraday_card_20261024.png` | 卡片实测截图（上半） |
| `docs/intraday_card_lower_20261024.png` | 卡片实测截图（轨迹 + 脉冲） |

## 3. 关键设计决策（含假设）

**为什么 mount=plugin 而不是 root**：`/api/panic`（ops）、`/api/amv_live.json`（compat）、
`/api/breadth_880.json` / `/api/sector_intraday.json`（compat_sector）都已被 root 插件持有。
本插件再挂 root 会触发 `app/core/main.py` 的根路径冲突保护，8810 直接起不来。
故本插件挂 `/api/p/intraday`，四个既有端点用绝对 `/api/...` 路径消费
（shell.js `makeCtx.api.get` 对 `/api/` 开头不拼插件前缀，dashboard 卡同一先例）。

**为什么加一个 `/summary` 端点**（任务书说「能不加后端就不加」）：
`/api/sector_track.json` 下发的是当日全量——实测 **2.67MB / 235 快照 × 90 板块**。
卡片 20s 刷一次 = 每分钟约 8MB 无效负载 + 每次两万多个 board 字典重复解析。
摘要端点读**同一个生产文件**（`ctx.panel_json("amv_live_sector_track")`，已在
`paths.READABLE_PANEL` 白名单，无需改 core），裁到 **8.2KB**，实测耗时 159ms。
判据：这不是新口径，`rel`（= `amv_pct` − 同快照 `all`）就是旧端 `_stVal` 的 `cal='rel'` 分支
（index.html:4906），时段过滤是旧端 `_stSnaps`（4905）。若坚持纯前端，这块要么全量拉、
要么不展示——前者与「只取摘要层别全量渲染」的任务书原话冲突。

**快照口径**：只取 `ts` 的 `HH:MM` ∈ `09:30–15:00`（剔盘前 08:xx 与收盘后点，旧端同）。
`nan/inf` 当缺值，不参与排序、不写序列；`first_all` 取首个有效点，任一端空则 `all_delta=null`（不补零）。

**前端骨架**：照 battle_plan 卡——`setHtml` 内容比对防闪、themes.css token、事件委托
（点轨迹表一行钉住/取消，挂在稳定 wrap 上）、`refreshEvery: 20000`。
四路 `Promise.all` 各自 `.catch` 降级，单块失败只上顶部一行错误，不拖垮整卡。

## 4. 与旧端的差异（如实登记，不静默）

1. **不下发权重**：旧 `/api/sector_track.json` 带 `weights`（`amv_board.db` 前一交易日 L2 成交额占比）。
   本插件不连库，摘要无权重；权重四象图仍在旧端，本卡未复刻。
2. **不重算陈旧闸门**：旧 `_track_stale`（盘中 >3min 无新点）口径 owner 是 `compat_sector`，
   本卡只展示 `last_ts` 与采样点数，不产生第二套阈值。
3. **不继承 3s 重绘**：旧端每 3s 重建整个 `#panel-intraday`；本卡按任务书 20s 增量刷新。
4. **盘中脉冲码表**：默认领先/落后各 3（6 张卡），点轨迹表一行可钉，钉住上限 8、总码数上限 20。
   旧端自选板块的实时卡属 `watchlist` 卡域，本卡不复刻。
5. **旧端 `breadth_880.json` 的 prev 段与 8800 不一致**（既有问题，不在本单范围）：
   8800 给 `prev={date:20261022, up:535, down:4999…}`，8819（compat_sector）给
   `prev={date:20261023, up:4526…}`——即 8819 的 prev 等于 today。**上报，未修**（`compat_sector` 不在白名单）。

## 5. 实测证据（CDP，headless Chrome 155 / 1440×1200）

临时实例 `127.0.0.1:8819`（不碰 8810），`/v2/` 点「盘中」卡：

```
key=intraday::intraday.main  hasCard=true
secs=[冰点监控 · 恐慌广度, 实时活跃市值（AMV）, 广度 880 · 涨跌家数, 板块轨迹摘要, 盘中脉冲]
svg=8  tr.it-row=12  .it-pc=6  errBox=null  shellBanner=""
errors=[]（除 favicon 404）  warnLogs=[]
```

数值抽样：

- 冰点：`恐慌广度 90.6 过热`，三项 `跌停占比 0.129%/分位21.0 · 上涨占比 83.28%/分位96.0 · 全A中位 1.55%/分位96.8`，
  序列 120 日、六阶段「高潮（20261023）」、`mode=closed`。
- AMV：`沪深全A 13.20 万亿 −1.23%`，9 个指数分项、板块领先/落后各 5、持仓 1 条（512480）。
- 轨迹摘要：`date=20261023 · 235 点 · 90 板块 · 全A −1.42%→+5.08%（+6.49pp）· 跑赢 27/跑输 63`。
- 脉冲：点「电机」行后 `pinned=[881277.TI]`，6 张卡全部有轨迹（`09:30→15:00 · 235 点`）。

**同源一致性**（8800 旧端 vs 8819 新端逐值比对）：

| 端点 | 结果 |
|---|---|
| `/api/amv_live.json` | `all` / `indices` / `sectors` **完全相等** |
| `/api/panic` | `value/state/trade_date/mode` 相等，`items` 相等，`series` 均 120 条 |
| `/api/sector_intraday.json` | 两码请求**完全相等** |
| 轨迹摘要 vs 8800 `/api/sector_track.json` 手算 | 领先 top6 / 落后 top6 / `first_all` / `last_all` / 板块数 90 **逐值相等** |
| `/api/breadth_880.json` | today 相等；**prev 不等**（见 §4 第 5 条，既有问题） |

## 6. 验证半径说明

改动半径 = 新插件目录 + 两份文档。跑的是全量 `pytest -q`（1152 项）+ `cutover_gate.py`（7 项），
超出半径（未动 core、未动其它插件），理由是新增插件会进 `_plugins` 清单与 gate 的
「插件无写代码」静态扫描范围，必须全量确认无冲突。gate 的 pytest 白名单里**未含**
`app/plugins/intraday/tests`——那是 `tools/cutover_gate.py` 的固定列表，不在本单白名单内，
故单插件测试靠 `python -m pytest -q` 全量覆盖（已含）。**建议后续把新插件加进 gate 列表**
（该文件属 core/工具域，留给 owner）。

## 7. 遗留 / 下一步建议

1. `tools/cutover_gate.py::_check_pytest` 的目录列表未含 `app/plugins/intraday/tests`（见 §6）。
2. `compat_sector` 的 `/api/breadth_880.json` prev 段等于 today（§4 第 5 条），需另开工单。
3. 权重四象图（旧 `/api/sector_track.json` 的 `weights`）未迁移，若盘中 Tab 要整块替换需单独评估。
4. 本卡未接 v2bridge 的旧首页 Tab 位（`app/web/v2bridge.js` 不在白名单），旧首页仍走旧盘中 Tab。
