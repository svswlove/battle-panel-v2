# 任务书 · 批2b paper 交易卡（新）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 项目：作战面板 v2 · 插件化重构
> 前置事实已由 01 验证：paper 插件后端 11 条路由全就绪（openapi 实查）；plugin.json `cards: []`（前端零代码）；`ctx.api.post` 可用；git 干净（HEAD 8f8e70e）。

## 1. 目标

给 paper 插件补第一张前端卡 `paper.main`：纸面交易台（下单/撤单/条件单/账户/委托流水），行为基准=旧 kline_order.js 下单弹窗（69KB）+ 旧 index.html 的账本区。这是 v2 壳里第一张**写操作卡**，纪律要求见 §4。

## 2. 功能清单（对照旧版逐项）

### 2.1 账户区（顶部）
- `/api/paper_account`：总资产/可用/持仓市值/盈亏/持仓表（代码/名称/数量/成本/现价/市值/盈亏/可卖）
- 无账本时 404 → 显示"纸面账本未生成（尚无纸面成交）"空态（与旧口径一致）

### 2.2 下单区（受控确认）
- 表单：代码（带名称回显，`stock_names` 可从 `/api/order/list` 顺带拿不到就省略名称）/ 方向 buy-sell / 数量 / 限价单价格（市价单隐藏价格框）/ 名称备注
- 数量快捷档（旧版有：全仓/半仓/1/4 等，读旧 kline_order.js 对齐）
- **提交前确认弹层**：复核代码/方向/数量/价格四要素 + "纸面"红色徽标，点确认才 POST `/api/order/place`；回执失败原样显示（detail 带 core 的 code，行内红条）
- 提交后自动重拉账户+委托

### 2.3 委托与条件单列表（中部）
- `/api/order/list`：当日委托表（时间/代码/方向/数量/价格/状态/来源）+ 条件单表（类型/触发参数/状态/有效期）
- 撤单按钮：委托 → `/api/order/cancel` {order_id}；条件单 → `/api/condition/cancel` {cond_id}；终态不可撤（后端 409，前端照实显示）
- 挂单冻结股数可显示（`/api/ledger` 里有 frozen_by_code）

### 2.4 条件单表单（可折叠区，9 类）
- 类型下拉（旧 COND_LABEL 九类：止损卖出/止盈卖出/回落卖出/反弹买入/突破买入/定时委托/保本止损/破五日线卖出/信号买入）
- 各类型参数：trigger_price/pct/amount/limit_price/valid_until/at/group/confirm_count/buffer_atr——**照旧 kline_order.js 的每类显隐与提示文案搬**，不要自己发明交互
- signal_buy 类型可选但触发依赖信号库——后端已接 provider，前端正常提交即可，卡上不承诺"必触发"
- "立即评估"按钮：POST `/api/condition/evaluate`（盘中无 feed 时回执 quotes 为空属正常，如实显示 triggered/cancelled/expired 计数）

### 2.5 诊断条（底部，一行）
- `/api/paper/feed/status`：feed 状态（connected/subscribed 数），QMT 未启动显示"行情未连"黄色提示——**这是预期状态，不是故障**

### 2.6 刷新
- 卡声明 `refreshEvery: 20000`（账户+委托 20s；下单/撤单/加条件单成功后立即手动重拉）
- 页面不可见跳过（core 已有机制，声明即可）

## 3. 改动白名单（只许碰这些）

```
app/plugins/paper/frontend.js               # 新建
app/plugins/paper/plugin.json               # cards 加 paper.main；version 0.1.0 → 0.2.0
app/plugins/paper/tests/test_paper_api.py   # 可加前端约定相关断言（如确认弹层存在性走不了 pytest，则不加）
docs/paper_contract.md                      # 新建（卡片交互契约一页）
docs/HANDOFF_20261024_fe_batch2b.md         # 交接文档（新建）
```

禁止项：
- 不改 paper/backend.py、views.py、任何 core 文件、其他插件、engine/web/
- 不接实盘：不出现任何 QMT 实盘下单字样；ledger mode=paper 固定
- 不自动下单：任何 POST 都必须经确认弹层或行内二次点击
- 不引框架/构建

## 4. 写操作纪律（v2 壳第一张写卡，全项目在看）

1. 所有写请求走 `ctx.api.post`（统一入口带超时与错误规整）
2. 确认弹层是**模态**的：遮罩 + 只有一个确认按钮；Enter 不触发确认（必须鼠标点击）
3. 提交中按钮 disabled + 转圈，防双击重复下单
4. 失败回执**原样**渲染（不吞 4xx/5xx，不重试写请求）
5. 刷新拉取用 GET，不因写失败影响读

## 5. 自测要求（全部要跑，贴退出码）

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/paper/tests app/core/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

浏览器实测（CDP/playwright）：
1. `http://127.0.0.1:8810/` 挂载 paper 卡，截图（空态与有数据态各一张）
2. **纸面测试单全链路**：周六非交易时段 → 下市价单会被 `_session_ok` 拒（4xx，detail 带时段提示）——这**正好验证拒绝链路**，截图错误回执；再 POST `/api/condition/evaluate?force=1` 验证手动评估链路回执渲染
3. 确认弹层四要素复核截图一张
4. 控制台 0 报错

## 6. 交付格式

回执 ≤300 字：做了什么 / 文件 / pytest 数字 / gate / 全链路验证结论（含"与 8800 账本不串"的取证方式——比对 `/api/ledger` 与 8800 账本文件或旧端点响应）。截图与明细落 `docs/HANDOFF_20261024_fe_batch2b.md`。**不要自行 commit**。
