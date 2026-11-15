# paper 卡片交互契约（paper.main · v0.2.0）

> 版本：paper 插件 0.2.0 ｜ core 0.3.1 ｜ 2026-10-24 ｜ 任务书：`docs/task_fe_batch2b.md`
> 本页是**卡片交互契约**，只写前端怎么用后端；业务口径与账本写入唯一源仍是
> `app/core/paper/`，HTTP 装配见 `app/plugins/paper/backend.py`。

## 1. 卡片声明

```js
export default { cards: [{ id: 'paper.main', title: '纸面交易', slot: 'main', order: 45,
  refreshEvery: 20000, mount, unmount, refresh }] };
```

- `order: 45`：排在作战表（40）之后、自选池（50）之前 —— 交易链自然顺序。
- `refreshEvery: 20000`：账户 + 委托 20s。shell 在页面不可见时跳过 tick；上一轮未完成不重入。
- 卡片额外守卫：`S.modal` 存在（确认弹层开着）或 `S.submitting`（提交中）时 `refresh()` 直接返回，
  避免刷新把用户正在核对的弹层/正在提交的请求打断。

## 2. 端点（读 4 + 写 5）

| 用途 | 方法 · 路径 | 说明 |
|---|---|---|
| 账户 + 持仓 | `GET /api/paper_account` | 404 = 账本未生成（尚无纸面成交），前端渲染空态，**不当错误** |
| 当日委托 + 条件单 + 指标 | `GET /api/order/list` | `orders` / `conditions` / `account` / `metrics{atr14,ma5}` |
| 冻结股数 | `GET /api/ledger?mode=paper` | 只取 `positions[].frozen_qty` 显示「冻结」列；**mode 固定 paper** |
| 行情诊断 | `GET /api/paper/feed/status` | `connected` / `subscribed_count` / `state` / `error` |
| 下单 | `POST /api/order/place` | `{code, side, qty, price, order_type, name, note}` |
| 撤单 | `POST /api/order/cancel` | `{order_id}` |
| 加条件单 | `POST /api/condition/add` | 见 §4 字段表 |
| 撤条件单 | `POST /api/condition/cancel` | `{cond_id}` |
| 立即评估 | `POST /api/condition/evaluate?force=1` | `force=1` 绕过时段/新鲜度门槛（演练用） |

**路径为什么全是绝对 `/api/…`**：paper 插件 `backend.mount = "root"`，core 把它挂在 `/api` 而不是
`/api/p/paper`。shell 的 `makeCtx` 对 `/api/` 开头的路径不拼插件前缀，所以相对路径反而会 404。
`/api/p/paper/*` **不存在**，别照其他插件的样子写相对路径。

## 3. 写操作纪律（v2 壳第一张写卡）

1. **统一入口**：所有写请求走 `ctx.api.post`（带超时与错误规整），不裸 `fetch`。
2. **模态确认弹层**（下单 / 加条件单）：
   - 遮罩 `.pp-mask`（`position:fixed;inset:0`）+ `role="dialog" aria-modal="true"`；
   - 弹层内**只有一个确认按钮**（`data-modal-ok`，全文件仅一处）+ 一个取消；
   - 复核四要素：**代码 / 方向 / 数量 / 价格**；加条件单复核**代码/类型/触发参数/数量/委托/有效期**；
   - 红色「纸面」徽标（`--danger-solid` 底 + `--danger-on` 字）；
   - **Enter 不触发确认**：弹层开着时全局捕获 `keydown`，`Enter` 一律 `preventDefault + stopPropagation`，
     必须鼠标点确认；`Escape` = 取消。
3. **防双击**：`S.submitting` 重入守卫 + 确认按钮 `disabled` + 文案变「提交中…」，`finally` 复位。
4. **失败回执原样渲染**：`catch` 里 `setStatus('下单失败：' + errText(e), true)`，行内红条
   `.pp-err` 直接显示 `ctx.api.post` 抛出的 `POST <url> -> <status>: <detail>`
   （detail 带 core 的机器码，如 `（closed）` `（no_quote）` `（invalid）`）。**不吞、不改写、不重试**。
5. **读写分离**：刷新只走 GET；写失败不阻断读（`load()` 与写请求互不依赖）。
6. **写成功后重拉**：清防闪缓存 `S.html = Object.create(null)` → `await load()`，以服务端为唯一事实源，不乐观更新。
7. **行内二次点击**（撤单 / 立即评估）：第一次点把按钮点亮成「确认撤单？」「确认评估？」（`data-arm=1`），
   4 秒内第二次点才真正 POST；超时自动熄灭。这两类不走模态弹层（任务书 §2.3 的「行内二次点击」）。

## 4. 条件单九类（对旧 `kline_order.js` 逐字）

| type | 标签 | 表单字段 |
|---|---|---|
| `stop_loss` | 止损卖出 | 触发价 + ATR 缓冲(无/0.3×ATR) |
| `take_profit` | 止盈卖出 | 触发价 + ATR 缓冲 |
| `trailing_sell` | 回落卖出 | 幅度 % |
| `rebound_buy` | 反弹买入 | 幅度 % |
| `breakout_buy` | 突破买入 | 触发价 + ATR 缓冲 |
| `time_order` | 定时委托 | 时间 HH:MM + 方向 |
| `breakeven_stop` | 保本止损 | 无（线由成本 + ATR14 决定，固定 `buffer_atr=0.3`） |
| `ma5_break` | 破五日线卖出 | 无（线由 MA5 + ATR14 决定，固定 `buffer_atr=0.3`） |
| `signal_buy` | 信号买入 | 信号五档（BREAKOUT/PULLBACK/VWAP_RECLAIM/HOOK_TURN/WEAK_TO_STRONG） |

公共字段：`qty`、`order_type`（市价/限价）、`valid_until`（当日/1/3/5/10/20 天，前端算成 YYYYMMDD）、
`confirm_count`（1/2/3 次）、OCO 勾选（`group = OCO-<code>-<ts>`）、`note: 'panel'`。

**`signal_buy` 的诚实边界**：卡上写明「本卡不承诺必触发」——触发依赖 core 的信号库
（`svc.signal_provider`），本批前端只负责把单提交上去，不在 UI 上许诺。

**`metrics` 缺失时**：保本止损/破五日线显示「ATR14 取不到，暂不能设（需 14 根日线）」，
不编造数值（旧 `lineCondText` 同口径）。

## 5. 下单表单

- 代码：手填 6 位或带后缀；名称回显来自 `/api/order/list` 的持仓/条件单记录，
  查不到显示「（无名称记录，按代码提交）」——**不编造名称**。
- 方向：买入 / 卖出（`.pp-seg`，同组互斥）。
- 委托方式：限价 / 市价；**市价时价格行整行隐藏且清空**（旧 `orderBodyHtml` 同口径）。
- 数量快捷档：1/4、1/3、1/2、全部。买入 = `clampLot(可用资金 ÷ 价格 × 比例)`；
  卖出 = `可卖 × 比例`（向下取整到 100 股）。
- 数量锁：只对**买入**生效（卖出永远可改）；锁住时数量框 `readOnly`、快捷档置灰不可点。
- 默认数量 100 股（刻意给小，避免一键打满仓位 —— 旧版注释同义）。

## 6. 账户 / 列表

- 账户：总资产 / 可用 / 持仓市值 / 浮动盈亏 / 挂单冻结 / 单笔上限(10%) 六个 KPI + 持仓表
  （代码名称 / 数量 / 可卖 / 成本 / 现价 / 市值 / 盈亏 / 冻结）。`price_source=cost_fallback` 标「成本」。
- 委托表：时间 / 代码名称 / 方向 / 数量(含已成) / 价格 / 状态 / 来源 / 操作。终态无撤单按钮。
- 条件单表：类型 / 代码名称 / 触发参数 / 数量 / 状态 / 有效期 / 操作。**ACTIVE 排前**，
  段头显示「监控中 N / 共 M 条」；`t1_locked` 标「T+1待解锁」，`days_left<=3` 标「⚠即将到期」。
- 诊断条（底部一行）：`行情已连`（绿）/ `行情未连`（黄）+ state / 订阅数 / tick 数 / 最后 tick 时间。
  **QMT 未启动是预期状态，不是故障** —— 文案里明写，且提示可点「立即评估」手动跑一轮。

## 7. 明确不做（本批边界）

- 不接实盘：无任何 QMT/柜台字样，无实盘下单通道；账本 `mode` 固定 `paper`。
- 不自动下单：下单与加条件单必须过模态确认；撤单/评估必须行内二次点击。
- 不引框架/构建：原生 ES module，零 `import`，样式自注入（`paper-card-css` 幂等）。
- 不写任何文件、不引子进程、不直连数据库（`tools/cutover_gate.py` 静态扫描同款禁令，测试里也扫一遍）。
- 不搬旧弹窗的图表联动（右键菜单、价格预览线、拖拽定位）——那是 K 线卡内交互，v2 由 kline 卡负责。
