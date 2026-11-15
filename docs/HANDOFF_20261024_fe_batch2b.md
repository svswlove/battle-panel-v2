# 前端批次 2b 交接：paper 纸面交易卡（新建）

> 任务书：`docs/task_fe_batch2b.md`｜派单：01 → DSHana｜2026-10-24
> 行为基准：`engine/web/kline_order.js`（69KB 下单弹窗，逐段引用行号见下）+ `engine/web/index.html` 账本区
> 状态：**代码与静态检查已落盘**；`pytest` / `cutover_gate` / 浏览器截图**未跑** —— 本执行手沙箱禁止创建任何子进程
> （`spawn python EPERM` / `spawn cmd.exe EPERM` / `spawn node EPERM`，见 §5.1），8810 **未重启**
> （进程内 manifest 仍是 `paper 0.1.0 / cards: []`，见 §6）。

---

## 1. 文件清单（严格在白名单内）

| 文件 | 动作 | 行数 / 大小 |
|---|---|---|
| `app/plugins/paper/frontend.js` | **新建** | 1,178 行 / 58,733 B |
| `app/plugins/paper/plugin.json` | 加 `frontend` 段（cards: paper.main）+ `version` 0.1.0 → **0.2.0** | 追加 6 行 |
| `app/plugins/paper/tests/test_paper_frontend.py` | **新建**（41 条静态检查） | 414 行 / 18,319 B |
| `docs/paper_contract.md` | **新建**（卡片交互契约一页） | 107 行 / 7,696 B |
| `docs/HANDOFF_20261024_fe_batch2b.md` | **新建**（本篇） | 208 行 / 17,160 B |

**未改**（本次零触碰）：`backend.py`、`views.py`、`tests/test_paper_api.py`、`app/core/**`、
`app/web/**`（shell.js / shell.css / legacy/**）、其他插件、`engine/web/**`。
`plugin.json` 的 `backend` / `data` / `permissions` 一字未动（只有 `version` 与新增 `frontend` 段）。**未 commit**。

## 2. 逐项对任务书 §2

| # | 要求 | 落点 | 备注 |
|---|---|---|---|
| 1 | 账户区：总资产/可用/持仓市值/盈亏/持仓表 | `accountHtml()` | 六 KPI + 8 列持仓表（代码名称/数量/可卖/成本/现价/市值/盈亏/冻结）；`cost_fallback` 标「成本」 |
| 2 | 无账本 404 → 空态 | `accountHtml()` 的 `__status === 404` 分支 | 文案「纸面账本未生成（尚无纸面成交）」，与旧 3334 同义 |
| 3 | 下单表单（代码/名称回显/方向/数量/限价/市价/备注） | `mount()` 骨架 + `syncPriceRow()` | 名称回显来自 `/api/order/list` 的持仓与条件单记录，查不到显示「（无名称记录，按代码提交）」，**不编造** |
| 4 | 数量快捷档（1/4、1/3、1/2、全部） | `QTY_QUICK` + `applyQuick()` | 逐字照旧 593-594；买入按可用资金÷价×比例，卖出按可卖×比例 |
| 5 | 数量锁 | `syncLock()` | 旧 562 口径：**只对买入生效**，卖出永远可改；锁住时 `readOnly` + 快捷档置灰 |
| 6 | **确认弹层（四要素 + 纸面徽标）** | `openModal()` | 代码/方向/数量/价格四行；`.pp-badge.paper` 红底（`--danger-solid`）；遮罩 + 唯一确认按钮 |
| 7 | Enter 不触发确认 | `onModalKey` | 弹层开着时全局捕获 `keydown`，`Enter` → `preventDefault + stopPropagation`；`Escape` = 取消 |
| 8 | 提交中 disabled 防双击 | `openModal` 内确认回调 | `S.submitting` 守卫 + `btnOk.disabled` + 「提交中…」+ `finally` 复位 |
| 9 | 失败回执原样渲染、不重试 | 五个写函数 `catch` | `setStatus('下单失败：' + errText(e), true)` → 行内红条直接显示 `POST <url> -> <status>: <detail>`（含 core 机器码） |
| 10 | 提交后自动重拉账户+委托 | 五个写函数成功分支 | 清防闪缓存 → `await load()`（4 个 GET 重拉），以服务端为准，不乐观更新 |
| 11 | 委托表（时间/代码/方向/数量/价格/状态/来源） | `ordersHtml()` | 8 列，末列操作；终态无撤单按钮 |
| 12 | 条件单表（类型/触发参数/状态/有效期） | `condsHtml()` + `condTriggerText()` | 7 列；**ACTIVE 排前**，段头「监控中 N / 共 M 条」；`T+1待解锁`、`⚠即将到期` 照旧 768-783 |
| 13 | 撤单（委托 / 条件单，终态不可撤） | `cancelOrder` / `cancelCond` | **行内二次点击**（首次点亮「确认撤单？」，4s 内二次才 POST）；后端 409 原样显示 |
| 14 | 挂单冻结股数 | `load()` 读 `/api/ledger` 的 `frozen_by_code` | 从 `ledger.positions[].frozen_qty` 建映射，显示在持仓表「冻结」列 |
| 15 | 条件单九类 + 每类显隐/提示 | `COND_LABEL`/`COND_HINT`/`buildCondFields()` | 九类标签与顺序逐字照旧 33-37；提示文案照旧 38-48；每类字段照旧 670-698 |
| 16 | `signal_buy` 不承诺必触发 | `buildCondFields()` 的信号分支 | 卡上明写「本卡不承诺必触发（触发依赖信号库可用）」 |
| 17 | 立即评估 | `runEvaluate()` | `POST /api/condition/evaluate?force=1`，如实显示 triggered/cancelled/expired/filled 计数 + 行情只数；行情 0 只时注明「无 feed 时行情为空属正常」 |
| 18 | 诊断条（一行） | `feedHtml()` | `行情已连`（绿）/ `行情未连`（黄）+ state/订阅数/tick 数/最后 tick；明写「QMT 未启动属预期状态（不是故障）」 |
| 19 | `refreshEvery: 20000` | 卡片声明 | 页面不可见由 shell 跳过；另加 `S.modal`/`S.submitting` 守卫（弹层开着不刷） |

## 3. 数据源与后端边界

| 用途 | 端点 | 提供方 |
|---|---|---|
| 账户 + 持仓 | `GET /api/paper_account` | paper 插件（既有，root 挂载） |
| 当日委托 + 条件单 + 指标 | `GET /api/order/list` | paper 插件（既有） |
| 冻结股数 | `GET /api/ledger?mode=paper` | paper 插件（既有） |
| 行情诊断 | `GET /api/paper/feed/status` | paper 插件（既有） |
| 下单 / 撤单 / 加条件单 / 撤条件单 / 评估 | `POST /api/order/{place,cancel}`、`/api/condition/{add,cancel,evaluate?force=1}` | paper 插件（既有） |

**后端零改动、零新增路由**；`data.write` 仍为空；账本写入仍全在 `app/core/paper/`。
**路径必须绝对 `/api/…`**：paper 是 `mount=root` 插件，没有 `/api/p/paper` 前缀（这是本批最容易踩的坑，测试已锁死）。

## 4. 与旧版的差异（如实登记）

1. **不搬 K 线弹窗本体**：右键菜单、光标取价、价格预览虚线、面板拖拽定位属 K 线卡交互，v2 由 kline 卡负责。
   本卡是「常驻卡片形态」的下单台，不是浮层。
2. **不搬「按金额输入」模式**（旧 `qtyMode=amt`）：任务书 §2.2 只要「数量快捷档」，金额模式与其联动
   （锁金额、`clampLot(amt/px)`）未搬。要补的话在 `applyQuick()` 旁边加一个 `qtyMode` 开关即可。
3. **不搬价格快捷档（最新/买一/卖一/涨停/跌停）**：旧版依赖 `/api/quote5` 的五档快照，本卡未引第二个插件端点
   （任务书 §2.2 的功能清单里没有价格档，只有数量档）。**如需补**：加 `GET /api/quote5/{code}` 一个读端点，
   注意写端点集合测试会一起失败，需同步改 `WRITE/READ_ENDPOINTS` 常量。
4. **撤单/评估用行内二次点击，不用模态弹层**：任务书 §2.3 明确「行内二次点击」；模态弹层只给下单与加条件单。
5. **条件单表列出全部状态**（不只 ACTIVE）：旧版条件单页只列 ACTIVE，本卡保留终态留档但把 ACTIVE 排前面、
   段头给出「监控中 N / 共 M 条」。理由：纸面账本当前 21 条里 18 条 CANCELLED、3 条 TRIGGERED，只列 ACTIVE 会看到空表。
6. **无「全撤」按钮**：旧 `cancelAll` 一次串行撤多笔，属批量写操作，本批未搬（避免一次点击产生 N 个写请求）。
7. **无持仓 T+1 可卖量本地重算**：直接用 `/api/order/list` 的 `account.positions[].available_qty`（core 口径），
   前端不重算第二套。

## 5. 自测状态

### 5.1 未跑（沙箱限制，必须由 01 补跑）

本执行手沙箱对**任何**子进程返回 EPERM，实测：

```
spawnSync python -c "print(1)"    -> status=null err="spawnSync python EPERM"
spawnSync cmd /c echo hi          -> err="spawnSync cmd.exe EPERM"
spawnSync node -e "console.log(1)"-> err="spawnSync node EPERM"
```

故以下三条**未跑、无退出码**，请 01 在正常 shell 里补：

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/paper/tests app/core/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

### 5.2 已做的等价验证（不依赖子进程）

**(a) 模块可真实 import + 卡片形状**：把 `frontend.js` 以 `data:text/javascript;base64` 动态 import，
导出 `default.cards` 单卡：`id=paper.main` / `title=纸面交易` / `slot=main` / `order=45` /
`refreshEvery=20000` / `mount·unmount·refresh` 均为 function；CSS 注入 id `paper-card-css` 成功。

**(b) 41 条 pytest 断言逐条预演**（用等价 JS 正则/字符串判定，对同一份 `frontend.js` 与 `plugin.json`）：
**41/41 通过**。预演中抓到并修掉 2 处**我自己写错的断言**（不是源码问题）：
`role="dialog"` 实际是 `setAttribute('role', 'dialog')`；`QTY_QUICK` 的 `\[([\s\S]*?)\n\];` 因数组写在单行而匹配不到。

**(c) 真实 8810 数据 + DOM shim 端到端渲染**（不是 mock 数据）：`mount(root, ctx)` 走真 `fetch` 打到
`http://127.0.0.1:8810/api/{paper_account,order/list,ledger?mode=paper,paper/feed/status}`，实测渲染：

| 断言 | 实测值 |
|---|---|
| 卡片总文本 | 2,175 字符，8 个关键段全部命中（纸面交易/纸面/账户/下单/当日委托/条件单/行情未连/数据源） |
| 账户总资产 | `¥473,596.7`（与 8800 `/api/paper_account` 的 `cash` 一致） |
| 持仓 | `空仓`（`positions_list` 0 条，账本已生成 → 走「无纸面持仓」分支而非 404 空态） |
| 当日委托 | `0 笔` → 「当日暂无委托」 |
| 条件单 | `监控中 0 / 共 21 条`（18 CANCELLED + 3 TRIGGERED，与后端实况一致） |
| 诊断条 | `行情未连 state=ERROR · 订阅 0 只 · tick 0` + 预期状态说明 + 原始错误串原样透出 |

**(d) 写操作纪律闭环**（下单请求打真实 8810，**周六非交易时段 → 被 core 拒绝，正是任务书 §5.2 要验证的拒绝链路**）：

| 场景 | 断言 | 结果 |
|---|---|---|
| 点「确认下单」 | 弹层出现，**POST 次数仍为 0**（未点确认不下单） | **PASS** |
| 弹层内容 | `确认下单 纸面 代码 600519 方向 买入 数量 100 股 价格 市价（不填价格）…` 四要素齐全 + 红「纸面」徽标 | **PASS** |
| 确认按钮数量 | `[data-modal-ok]` 在弹层内 **1 个**（全文件也仅 1 处） | **PASS** |
| 按 Enter | `preventDefault` 被调用，POST 次数 **0** | **PASS** |
| 点确认（市价单） | POST `/api/order/place`，body 逐字 `{"code":"600519","side":"buy","qty":100,"price":null,"order_type":"market","name":"","note":"panel"}` | **PASS** |
| 失败回执 | 行内红条 `下单失败：POST /api/order/place -> 409: 非交易时段不能下市价单（没有对手盘），请用限价挂单（closed）`，class=`pp-err` | **PASS** |
| 弹层关闭 | 失败后弹层已移除（`S.modal=null`） | **PASS** |
| 提交中 | 确认按钮 `disabled=true`、文案「提交中…」 | **PASS** |
| 限价单路径 | 同样 4xx 原样显示（实测 `422: 价格 10.00 低于跌停价 1186.64（invalid）`） | **PASS** |
| 加条件单 | 弹层复核 6 行（代码/类型/触发参数/数量/委托/有效期）；body 逐字含 `valid_until`/`at`/`side` | **PASS** |
| 立即评估 | 首次点击只点亮「确认评估？」，POST=0；二次点击才 POST `/api/condition/evaluate?force=1` | **PASS** |
| 评估回执 | `评估完成：触发 0 · 撤销 0 · 过期 0 · 补成交 0 · 行情 0 只（无 feed 时行情为空属正常：QMT 未启动）` | **PASS** |
| 防闪 | 数据不变时连续 `refresh()` → innerHTML 写入 **0** 次 | **PASS** |
| unmount 后 | `refresh()` 请求数 **0**，`card._pp === null` | **PASS** |

**(e) 合成数据渲染**（覆盖真实数据没有的分支；14 项全 PASS）：持仓表出「贵州茅台 1000 股 / 可卖 600 / 成本标记 /
冻结 200」；委托表 2 行且**只有 REPORTED 那行有撤单按钮**；条件单 ACTIVE 排前 + `T+1待解锁` + `⚠即将到期`；
`行情已连`（connected=true）；撤单行内二次点击：首次 `确认撤单？/arm=1` POST=0 → 二次 POST `{"order_id":"PP-1"}`
→ 回执 `已撤单 PP-1（已撤）`；撤条件单失败 → 红条 `撤条件单失败（CO-1）：POST /api/condition/cancel -> 409: …`；
线类提示 `成本 10.000 · ATR14 0.500 → 浮盈达 0.750 后跌回 10.000 卖出`、
`MA5 10.200 · ATR14 0.500 → 盘中跌破 10.050｜14:57 后跌破 10.200 卖出`；信号下拉 5 档 + 「不承诺必触发」；
数量锁卖出时「关」、买入时「开」且 `readOnly=true`、快捷档 `is-dis`；卖出「全部」→ 600（=可卖）；市价切换后价格框隐藏且清空。

**(f) 修掉的两个真 bug**（本轮实测抓出来的，不是文档瑕疵）：
1. **段按钮互斥失效**：`seg()` 给每个按钮也打了 `data-segbox`，`closest('[data-segbox]')` 命中按钮自身 →
   同组按钮不熄灭（买/卖同时高亮、市价切不掉限价）。已改为只给容器打 `data-segbox`，并加 `.pp-seg` 兜底。
2. **`rowPrice.lastChild` 脆弱引用**：市价切限价时若行内节点顺序变动会 `null.hidden` 抛错。已改为显式 `priceUnit` 引用。

### 5.3 与旧 8800 账本不串的取证（截图替代证据）

| 比对 | 结果 |
|---|---|
| `8810 /api/ledger?mode=paper` vs `8800 /api/ledger?mode=paper`（去掉 `asof` 时间戳后逐字段） | **equal=True** |
| `account` 十键 | 两侧完全相同：`total_asset 473596.7 / cash 473596.7 / available_cash 473596.7 / frozen_cash 0 / market_value 0 / position_pnl 0 / cash_pct 100` |
| `positions` / `trades` / `book` | 两侧都是 0 条、`{"active":[],"history":[],"hist_max":50,"hist_total":0}` |
| `/api/paper_account` | 两侧 `cash` 均 473596.7、`realized_pnl` 均 -26403.34、`positions` 均空 |
| 探针前后账本是否被改 | **未变**（4 次拒绝探针后 `/api/ledger` 与探针前逐字段相同） |

> 说明：本批**没有成功下单**（周六非交易时段全部被拒），所以「纸面账本没被 8810 改动」是**必然结果**而非巧合；
> 上表证明的是**读口径同源**，且 8810 的写通道在拒绝路径上不落盘。

### 5.4 未做的浏览器实测（诚实标注）

- **无 CDP 端口**：9222 / 9223 / 9333 / 9515 / 4444 全部 closed（逐个探测）。
- **无 playwright / puppeteer**：`node_modules` 内两处均不存在。
- **无法自起浏览器**：`spawn EPERM`（见 5.1）。
- 结论：**8810 挂载截图（空态/有数据态）、确认弹层四要素截图、控制台 0 报错**三项**未做**。
  已用 5.2(c)(d)(e) 的 DOM shim + 真实 HTTP 渲染与真实拒绝链路替代；截图请 01 在浏览器里补。

### 5.5 cutover_gate 里可走 HTTP / 纯文件读的项（照抄 gate 判定逻辑复算）

> `docs/cutover_gate_latest.md` 现存记录是 **2026-10-24 03:07:26 PASS（385 passed, 1 skipped）**，
> 早于本次改动（frontend.js 03:50:36），**不覆盖本批**。

| gate 项 | 结果 | 证据 |
|---|---|---|
| 8810 `/api/health` | **PASS** | HTTP 200 ok=true（core 0.3.1） |
| 全部插件健康 | **PASS** | `/_plugins/health` ok=true n=14 bad=[] |
| shadow 双跑 | **未跑** | 需子进程 |
| AMV 原始 JSON 比对 | **未跑** | 需子进程 |
| battle_plan 无写接口 | **PASS** | 既有，本轮未触碰 |
| 插件无写代码 | **PASS**（等价复算） | 按 gate 同款禁令扫 `app/plugins/**/*.py,*.js`（排除 tests），新增 `frontend.js` **clean** |
| kline 边界声明 | **PASS** | 既有，本轮未触碰 |
| pytest 全套 | **未跑** | 需子进程 |

## 6. 上线前置（01 必做）

1. **重启 8810**：`/_plugins` 目前仍报 `paper 0.1.0 / cards: []`（manifest 在进程启动时缓存，`_load_all` 只跑一次）。
   不重启则 shell 里**根本看不到这张卡**。
   前端资源本身已实时从磁盘出：`GET /plugins/paper/frontend.js` 返回 58,733 B、
   与本地文件**逐字节相同**、`last-modified: Sat, 24 Oct 2026 03:50:36 GMT`、ETag 已变。
2. 跑 §5.1 三条命令并贴退出码。
3. 浏览器补：8810 纸面交易卡（空态 + 有数据态）截图、确认弹层四要素截图、控制台 0 报错。
   拒绝链路冒烟（周六即可）：填代码 600519 → 委托选「市价」→ 数量 100 → 点「确认下单」→ 弹层核对 →
   点「确认买入（纸面）」→ 应看到红条 `下单失败：POST /api/order/place -> 409: 非交易时段不能下市价单…（closed）`。

## 7. 待 01 补登（本执行手写不进去）

工作区根 `OH-Works/文件修改日志/2026-10-24.md` 在本次沙箱工作区（`projects/panel-v2`）之外，
`appendFile` 被拒（`EPERM: operation not permitted`）。请 01 补登一行：

```
## DSHana · panel-v2 批2b 前端落地（未提交）
- 新建 app/plugins/paper/frontend.js（1,178 行 / 58,733 B，paper.main 纸面交易卡）
- app/plugins/paper/plugin.json：version 0.1.0→0.2.0，新增 frontend 段（cards: paper.main, order 45）；backend/data 未动
- 新建 app/plugins/paper/tests/test_paper_frontend.py（41 条静态检查）
- 新建 docs/paper_contract.md、docs/HANDOFF_20261024_fe_batch2b.md
- 未跑 pytest/gate/浏览器（沙箱禁子进程）；8810 未重启（进程内 manifest 仍 paper 0.1.0/cards []）；未 commit
```

## 8. 遗留 / 下一批

- 价格快捷档（最新/买一/卖一/涨停/跌停）未搬 —— 需新增一个 `GET /api/quote5/{code}` 读端点，且要同步改测试里的端点常量。
- 「按金额输入」模式未搬（旧 `qtyMode=amt`）。
- 「全撤」批量撤单未搬（一次点击产生 N 个写请求，需单独设计确认）。
- 条件单的「该票监控中」侧栏（旧 `ko-sub2`）未搬 —— 已由整表 + ACTIVE 排前覆盖。
- 纸面账户的 `equity_curve`（净值曲线）未画 —— 属图表能力，建议并入 kline 或 dashboard 卡。
