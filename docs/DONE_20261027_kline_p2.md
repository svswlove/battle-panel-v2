# K 线弹窗 P2：纸面下单面板 / 条件单 / 记录与撤单（2026-10-27）

> 负责人：01（执行器）｜权限 write，白名单见任务书｜8810 换血与真实下单验收由主人做
> 回退：HANA 文件历史；无 `.bak`、无新依赖、未启停服务、**未新增任何后端路由**

## 0. 一句话

弹窗内可开四 Tab 纸面面板（买入/卖出/条件单/记录），右键与五档带价，买卖走两次点击确认 + 防双击，
条件单九类按字段组合提交，委托/条件单可撤；所有写请求只走既有 paper 端点，隔离桩下走通、**未对生产发过任何 POST**。

## 1. 文件面（白名单内）

| 文件 | 性质 | 内容 |
|---|---|---|
| `frontend/order_model.js` | 新建 | 纯函数：整手/比例/费用、买卖校验、place/cancel/condition 请求体、记录筛选、回执文案、提交闸门、端点常量 |
| `frontend/order_panel.js` | 新建 | 面板 DOM + 四 Tab + 交互与请求（用 `order_model`，不 import paper 插件） |
| `frontend/overlay.js` | 修改 | 装配面板、右键/五档带价、条件触发价预览线、Esc 分级、关闭销毁、写后刷新只读数据 |
| `frontend/marks.js` | **未改** | 下单逻辑没有塞进这里 |
| `frontend.js` | **未改** | — |
| `tests/js/p2_order.test.mjs` | 新建 | 16 项模型单测（含闸门与端点常量） |
| `tests/test_kline_overlay_p1a.py` | 修改 | 补 4 条 P2 守卫 + node 跑 P2 套件；文件面白名单更新 |

## 2. 面板与交互

- 四 Tab：买入 / 卖出 / 条件单 / 记录；标题带 `<纸面>` 徽标，**没有 live 开关、没有实盘入口**。
- 入口：右键「买入/卖出/条件单/委托与条件单」，带入光标价；盘口五档每行点击即带该价开买入面板（旧版 chipOn 口径）。
- 价格档位条：最新/买一/卖一/涨停/跌停（取 `/quote5`，缺档位就提示「该档位没有价格」）；步进 ±0.01。
- 输入模式：按数量 / 按金额（买入页），「锁」按 `ko_lock` 决定是否取 `ko_qty_last`；快捷键 1/4、1/3、1/2、全部按可买/可卖整手向下取整。
- 默认一手 100 股（旧版同口径），不以「全部可买」作默认，避免一键打满。
- 信息行：可买股数、金额、费用（买万 1.1 / 卖万 6.1，旧版原文口径）、占总资产或持仓比例。
- 资金/持仓拿不到时显示「不可用」并禁用提交，**不拿 0 冒充足额**。

## 3. 条件单

九类与旧版一致：`stop_loss / take_profit / trailing_sell / rebound_buy / breakout_buy / time_order / breakeven_stop / ma5_break / signal_buy`，
label/hint 原文照搬。按类型显示字段：

| 类型 | 需要 | 提交要点 |
|---|---|---|
| stop_loss / take_profit / breakout_buy | 触发价 | `trigger_price` |
| trailing_sell / rebound_buy | 触发价 + 幅度% | `trigger_price` + `pct` |
| time_order | 时间 + 方向 | `at`（默认 14:55）、`side` |
| breakeven_stop / ma5_break | — | `buffer_atr = 0.3`（与 QMT 旧系统同口径） |
| signal_buy | 信号 | `signal`（默认 BREAKOUT） |

另有：市价/限价（限价带 `limit_price`）、有效期天数（→ `valid_until` YYYYMMDD）、确认次数 1–3、OCO（`group = OCO-<code>-<ts>`）。
预览：输入触发价即在主图标记层画虚线 + 「条件预览价位」，不写入任何委托；关闭面板即清除。
提交字段严格按 paper `ConditionAddBody`，前端不重复发明业务规则（不足项只做提示性前置校验）。

## 4. 记录与撤单

- 「记录」页列当前票的可撤项：委托 `REPORTED/PARTIAL`、条件单 `ACTIVE`（`order_model.liveOrders/liveConds`）。
- 撤单也是两次点击确认（每项独立闸门，3 秒内有效），成功后只刷新只读数据与记录页。
- 回执：FILLED → `已成交 N 股 @ 价`；PARTIAL → `部成 …（可撤余量）`；REPORTED → `已挂单，等对手盘`；其它状态原样显示。
- 失败原样显示 `失败：<detail>`，**不自动重试**；成功后不重置未提交的输入（除旧版明确会清空的金额/数量记忆按规则写入）。

## 5. 关闭与事件隔离

- 面板 `contextmenu` 阻止冒泡 → 不会穿透到主图右键；点击面板外自动关闭。
- Esc 分级：面板 → 手工画线态 → 右键菜单 → 关弹窗。
- 弹窗关闭时 `panel.destroy()`，面板 DOM、document 监听、条件预览线一并清掉。
- B/S 快捷键：核旧版 `kline_order.js` / `kline_lc.js` 的 keydown 只有 Esc / Enter / ArrowUp-Down，**没有 B/S**，故未加（符合「无旧版对应则不加」）。

## 6. 测试

```
pytest app/plugins/kline/tests -q            → 54 passed
node tests/js/p2_order.test.mjs              → 16 项全过
node tests/js/p1c_indicators.test.mjs        → 20 项全过（未回归）
node tests/js/p1a_marks.test.mjs             → 52 项全过（未回归）
```

P2 覆盖：整手/金额→数量/比例取整、费用口径、`ko_*` 记忆坏值回退、买卖校验（缺价/非整手/超资金/超可卖/资金持仓未知）、
place 与 condition 请求体字段、九类条件单全过、默认值（14:55 / BREAKOUT / buffer 0.3 / valid_until）、上下文 `order_id`/`cond_id`、
记录筛选、回执文案、闸门（未确认不发、busy 期间再点无效、arm 超时）。

**浏览器桩实测**（临时静态服务 + fetch 桩，已停服务并删目录；全程没有真实 POST）：
- 右键菜单出现 `buy/sell/cond/list` 四项；点「买入」→ 面板出现、含「纸面」、四 Tab、价格预填光标价 `100.00`。
- 第一次点提交：按钮变「再点一次确认买入」、`__posts.length === 0`、提示「再点一次确认（3 秒内有效）」。
- 第二次点提交：**恰好 1 次** POST `/api/order/place`，body `{code:'600519.SH',side:'buy',qty:100,price:100,order_type:'limit',name:'贵州茅台',note:'panel'}`，回执「已成交 100 股 @ 100.10」。
- 失败路径：桩返回 500 → 显示「失败：模拟失败」，等 900ms 后 POST 计数不再增长（无自动重试）。

## 7. 未实测 / 边界（不许读成「生产下单已验证」）

1. **没有对生产发过任何 POST**：所有写路径只在 fetch 桩与纯函数层验证；真实下单、撤单、条件单落库 **未实测**，等 8810 换血后由主人验。
2. 浏览器桩里没跑到：撤单二次确认、条件单提交、OCO、有效期、Esc 分级的自动化断言（前两项有模型单测，Esc 为代码级检查）；这几条属「已实现未做 DOM 断言」。
3. 持仓来源是 `/api/order/list` 的 `account.positions`；当前生产该字段为空对象，卖出页会显示「持仓未知，暂不能卖出」——这是诚实降级，不是 bug。
4. 条件单预览只画一条触发价虚线；`pct` 型（trailing/rebound）没有画区间带，避免自造视觉口径。
5. 面板未做拖拽/记忆位置/动画（任务书明确不投入）；面板宽度/位置固定，窄窗靠中心定位夹取。
6. 「记录」只列当前票；全部委托/条件单的全票视图未做（当前接口返回全量，但面板按票过滤，与旧版一致）。

---

# 验收返修（第二轮，2026-10-27 07:5x~08:5x）

> 打回 8 项均为真实契约不匹配。逐条对账：

| # | 打回项 | 本轮处置 |
|---|---|---|
| 1 | 可卖用了 `avail_qty`/回退 `qty` | 统一 `account.positions[code].available_qty`（T+1/冻结后口径）；缺字段 → `null` → 面板显「不可用」，**绝不回退 qty** |
| 2 | 撤单用了 `order_id` | 改用 `paper_order_id`（`order_id`/`id` 仅作有值兼容）；撤单 payload 由 `M.buildCancelOrderPayload` / `buildCancelCondPayload` 构造，**不再手写字面量** |
| 3 | 条件单信号/触发/限价/有效期/ATR | 信号枚举改 core `SIGNAL_NAMES`（BREAKOUT/PULLBACK/VWAP_RECLAIM/HOOK_TURN/WEAK_TO_STRONG）；`trailing_sell`/`rebound_buy` **取消触发价**（只要 pct 或 amount），触发预览同步不画；补 `#klo-cprice` 限价输入并校验 >0；有效期改 select 当日/1/3/5/10/20（默认 3）；触发型加 ATR「无 / 0.3×ATR」；触发价默认 `.98 / 1.02 / 1.01` |
| 4 | 记录只有当前票 | 加「当前票 / 全部」切换，全部显示代码；可撤项仍只筛 REPORTED/PARTIAL、ACTIVE；空 `positions` → 持仓 0 / 可卖 0（权威空），不是「未知」 |
| 5 | 表单被重设 / 确认提示被清 / 金额默认 | 表单状态收到 `F` 对象，切 Tab/类型/锁定都不重设数量与触发价；金额默认 `lastAmount≥1000 ? last : 10000`；锁定 → 数量 readonly + 快捷比例禁用；回执 `await onWritten` 后再 render，确认提示不再被刷新清掉 |
| 6 | Esc 顺序 | 改为 面板 → 菜单 → 画线 → 关窗；面板打开时底层快捷键不触发（Esc 只作用于面板） |
| 7 | 缺标题栏拖拽 / 默认值 | 补标题栏 pointer 拖拽（不持久化、无动画）；条件单数量默认 可卖 / 持仓 / 100 |
| 8 | 写后刷新可能用旧 account | `loadPaper` 任一接口失败 → `account` 置空 + `accountErr` 记录，界面显示「可用资金未知/不可用」；不再保留旧账户冒充最新 |

## 二次复跑

```
pytest app/plugins/kline/tests -q            → 59 passed（新增 5 条契约守卫）
node tests/js/p2_order.test.mjs              → 19 项全过（新增信号枚举/默认值/限价必填/pct-only）
node tests/js/p1c_indicators.test.mjs        → 20 项全过
node tests/js/p1a_marks.test.mjs             → 52 项全过
```

## 浏览器桩实测（本轮新增）

- 记录页（`当前票`）：撤单按钮的 `data-id` 为 **`PP-A`**（paper_order_id）、条件单为 `CO-1`；同票外的 `PP-B`/`CO-2` 未列出。
- 撤单第一次点击 → 按钮变「再点确认」且 `window.__posts.length === 0`（未发请求）。
- 买入流程（上一轮已测）：两次点击 → 恰好 1 次 `POST /api/order/place`，body 正确；桩返回 500 时显示失败且无重试。

## 仍未在 DOM 层验证（不许读成已验）

1. **撤单第二次点击的 POST body** 与**条件单提交/OCO** 的 DOM 断言：本轮浏览器工具在连续 click 场景反复校验失败（工具侧问题），只验到「首击进入确认态、零 POST」与模型层 payload。
2. 表单「切 Tab 不重设数量」「锁定 readonly」「拖拽」为代码级 + 静态守卫，未做 DOM 自动断言。
3. 生产真实下单/撤单/条件单落库仍未实测（等 8810 换血）。

---

# 验收返修（第三轮阻断项，2026-10-27 09:0x~09:3x）

| 项 | 问题 | 处置 | 证据 |
|---|---|---|---|
| A | `onWritten` 是同步函数，「await」形同虚设；成功回执可能早于列表刷新 | `onWritten` 改 `async`，`await Promise.all([loadPaper(), runSides()])`；`loadPaper` 返回 `{ok, reason}`；面板 `await opts.onWritten(...)` 后用返回值决定文案：回读失败 → 「…· 回读失败：xxx（不要以列表为准）」 | pytest `test_written_refreshes_are_awaited`；DOM 桩里回执出现在列表刷新之后 |
| B | `positions={}`（权威空）仍被判「持仓未知」 | `panelAccount`：`positions` 是对象但无本票 → `{qty:0, available_qty:0}`；本票对象缺字段 → 该字段 0；只有 `positions` 非对象/请求失败 → `null` | pytest `test_empty_positions_is_authoritative_zero` + DOM harness 用真实形状 `{qty,available_qty,today_bought}` |
| C | 只有模型测试 | 新增 **`tests/js/p2_dom.test.mjs`** + `p2_dom_harness.html`：本地 http 服务 + headless Chrome CDP（抄 `_vis/_klm_verify.js` 骨架，无新依赖），15 项 DOM 断言 | 见下 |
| D | 回落/反弹仍写「触发价预览」；`signal_buy` 可选限价；买入型条件单默认数量可能 0 | `condInfoHtml` 按类型分支（触发型/幅度型/信号型/定时/成本型各有文案）；`signal_buy` 强制市价（UI 限价按钮 disabled + model 校验「信号买入只支持市价」）；条件单数量默认 可卖→持仓→100，不给 0 | pytest `test_condition_info_is_type_specific_and_signal_market_only` |

## DOM 实测（`node tests/js/p2_dom.test.mjs`，15/15 通过）

```
ok 1  记录页撤单号用 paper_order_id（["PP-A","CO-1"]，异票不列）
ok 2  撤单首击进确认态且零 POST
ok 3  撤单恰好 1 次 POST 且 body 是 paper_order_id
ok 4  条件单首击进确认态
ok 5  条件单恰好 1 次 POST
ok 6  条件单 trigger_price 正确（9.5）
ok 7  条件单 limit_price 正确（9.4 + order_type=limit）
ok 8  条件单 OCO group 前缀正确（OCO-600519.SH-…）
ok 9  条件单 valid_until = 5 天后
ok 10 切 Tab 不重设数量（500 → 500）
ok 11 锁定后数量 readonly
ok 12 锁定后快捷比例禁用且不改变数量
ok 13 Esc 先关面板（菜单/弹窗仍在）
ok 14 Esc 再关菜单（弹窗仍在）
ok 15 Esc 最后关弹窗
```

脚本自带隔离：本地 http 服务只读插件源码、`window.fetch` 全量桩化（POST 收进 `window.__posts`）、
Chrome 用独立 `--user-data-dir` 临时目录且跑完删除；没有 Chrome 时打印 `SKIP` 并退出 0（pytest 侧 skip）。

## 复跑

```
pytest app/plugins/kline/tests -q            → 63 passed（含 p2_dom 拉起）
node tests/js/p2_order.test.mjs              → 19 项全过
node tests/js/p2_dom.test.mjs                → 15 项全过
node tests/js/p1c_indicators.test.mjs        → 20 项全过
node tests/js/p1a_marks.test.mjs             → 52 项全过
```

## 仍未实测（不变）

生产环境真实下单/撤单/条件单落库；拖拽坐标变化只在代码级（DOM 脚本未断言拖拽位移，避免逐像素脆断言）。

---

# 回归修复（2026-10-27 09:3x）：面板按钮色值改语义 token

- 失败项：`app/tests/test_fe_shell_bridge.py::test_no_raw_hex_in_any_card` —— `order_panel.js` 里两个写死色值。
- 改动（只这两行，不动任何逻辑）：
  - 买入按钮 `color:#fff` → `var(--up-on)`
  - 卖出按钮 `color:#04220f` → `var(--down-on)`
- 口径：`--up-on / --down-on` 是主题里与 `--up-solid / --down-solid` 配对的对比前景（与 `review` 插件的 `.rv-badge` 同一用法），沿用「实心背景 + 对应 on 前景」的既有约定，不新增色表、不动 themes.css。
- 复验：`pytest app/tests/test_fe_shell_bridge.py -q` → 28 passed；`node --check order_panel.js` → ok；`node tests/js/p2_dom.test.mjs` → 15 项失败 0。

---

# 资源版本一致性（2026-10-27 09:4x，仅 kline）

## 问题

shell 以 `<plugin base>/frontend.js?v=<manifest.version>` 载入口，但 kline 内部 import 的子模块**没有版本串**；
ES 模块按 URL 缓存 ⇒ 升级后可能出现「入口是新的、子模块还是旧的」混用（静态子模块无 Cache-Control，
浏览器可缓存旧文件并复用，只要 URL 不变）。

## 方案（最小且可验证，没有自造第二版本源）

- **唯一版本源 = `plugin.json.version`**（当前 `0.7.0`），与 shell 拼入口用的是同一个值。
- 给 kline 内部 6 条 import 边全部加上 `?v=0.7.0`（静态说明符 + 查询串，仍是静态 import 语义，不是动态重写）：
  `frontend.js → ./frontend/marks.js`、`frontend.js → ./frontend/overlay.js`、
  `overlay.js → ../frontend.js`（与 shell 入口 URL 完全同串，因此是同一个模块实例）、
  `overlay.js → marks / indicators / subcharts / order_panel`、`order_panel.js → order_model`。
- **没有新增 `revision.js`**：静态 import 说明符不能消费运行时导出的常量，再放一个 `KLINE_FRONTEND_REVISION`
  只会变成第三处可漂移的版本源；本轮改为**用静态守卫盯住「所有边 = manifest.version」**（见下）。

## 守卫（防止以后漂回去）

- `test_every_intra_kline_import_carries_manifest_version`：扫描 kline 前端全部 `.js`，每条相对 import 必须带
  `?v=`，且值 == `plugin.json.version`（至少 6 条边）。
- `test_entry_and_shell_use_same_version_source`：shell 仍用 `plugin.version` 拼入口；manifest 版本是语义化字符串。
- `test_no_new_revision_constant_to_drift`：kline 前端任何文件出现 `KLINE_FRONTEND_REVISION` 即失败。

## 实测

```
pytest app/plugins/kline/tests -q            → 66 passed
node --check（frontend.js + frontend/*.js 共 7 个文件）→ 全部 ok
node tests/js/p2_dom.test.mjs                → 15 项失败 0（模块图带 query 仍能正常加载并交互）
```

## 诚实边界（不宣称强一致）

1. **不是强一致缓存协议**：没有给静态文件加 `Cache-Control`（那在 shell/静态服务层，属 core，不在本档白名单）。
   同一版本号内若手工覆盖文件，浏览器仍可能用旧副本；只有 `version` 变化才会换 URL。
2. 升级前已经打开的页面不会自动重载模块（浏览器固有行为）；刷新/重开即拿到同一版本的一套。
3. 版本号必须**随前端改动手动 bump** —— 守卫只能保证「manifest 与所有 import 一致」，不能替人判断何时该升。
