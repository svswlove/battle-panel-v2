# 条件单预警推送 · 可移植方案调研（只读）

- 标注：N20261028-220052-48
- 日期：2026-10-28
- 边界：只读；未改代码、未启停 QMT/8810、未发外部消息、未读凭据内容（仅确认存在）
- 结论：旧链的「推送」不是队列消费，而是 `push_event()` 内部同步直连 QQ 官方机器人；
  QMT 侧无消息推送接口可移植。建议 panel-v2 core 新建 `notify` 模块。

## 0. 先纠正三个前提

| 任务书说法 | 实际 | 证据 |
|---|---|---|
| 旧 8800 有推送队列 + 消费方轮询 | 无消费方。`push_event` 写 JSONL 后同步调 `qq_push` 直发；「Hana 轮询」是 2026-08 旧架构 | push_queue.py:5-7、:243、:296-299 |
| ops 插件有 `NOTIFY_TYPE_KEYS` | ops 里没有。只在 `battle_plan_rw:105`（写盘校验）与 `compat:1640`（常量端点） | 两文件均为只读展示 |
| QMT 有推送模块可移植 | 未找到。xtquant 的 push 全是数据推送（行情/回报） | §3 |

## 1. 旧 8800 链路

### 1.1 链路图

```
[判定] cond_watch.py（独立常驻进程，5s/轮，交易日 09:00–15:30）
  tick() :130 → is_trading_day + in_continuous_auction → 盘外跳过
  → server._order_service()  server.py:4188
  → engine._order_quote()    server.py:4279（eltdx 五档 + ATR14/MA5，fresh≤120s）
  → svc.evaluate(quotes)     cond_watch.py:192 → manual_orders.py:1003（9 类逐一比对）
      → 命中 place()         manual_orders.py:458 → PaperBroker.order() 纸面撮合
  → svc.match_pending(quotes) cond_watch.py:193

[推送] paper_broker.py:489 _alert(level,event,title,body)
  → progress.append_progress（只写日志）
  → push_queue.push_event    paper_broker.py:498
      → 去重 300s            push_queue.py:44/:150
      → 追加写 engine/state/push_queue.jsonl（审计）
      → qq_push.push_qq 同步直发，10s 超时，失败只尾注
          → POST api.sgroup.qq.com/v2/users/{openid}/messages（QQ 官方 bot C2C）

[落盘] engine/state/push_queue.jsonl（上限 500 留 200）
        engine/state/push_dedup.json（跨进程去重）
        engine/state/qq_push_health.json（通道健康）
```

### 1.2 关键事实

1. 判定进程 = `executor/cond_watch.py`，2026-10-26 从 `server.py __main__` 迁出；
   注册 `run_daemons.py:96`，`SESSION_BOUND_DAEMONS:116-119`，探活 `watchdog.py:570`。
2. 账本 `engine/state/conditional_orders.json`（manual_orders.py:54）。
   状态机 ACTIVE→TRIGGERED/CANCELLED/EXPIRED/FAILED，OCO 互斥（:1003-1193）。
3. `push_queue` 无消费者：队列文件只是审计副本，真发送在 `push_event` 里。
4. 唯一通道 = QQ 官方机器人 C2C。凭据 `~/.hanako/agents/agent-mt9jqvxf/config.yaml`
   的 `bridge.qq`（qq_push.py:35）；文件存在（1528 字节，mtime 2026-10-22 17:09），未读内容。
   无微信/钉钉/Telegram/桌面/邮件。
5. 白名单几乎全拦：`EVENT_WHITELIST` 12 类（:52-68），可勾选 9 类（:71-75）；
   `battle_plan.json:25-29` 只勾 ORDER_FILLED/STOP_TRIGGERED/TAKE_PROFIT_TRIGGERED。
   `push_queue.jsonl` 几乎每条带「[推送类型未勾选]」；`qq_push_health` 最后成功 2026-10-20 18:31:44。
6. **条件单触发不产生推送**：`evaluate` 不调 `push_event`，`EVENT_WHITELIST` 无 COND_TRIGGERED；
   STOP_TRIGGERED 的实际调用方是 paper_broker 补账检查（:1358-1398）。全队列搜「条件单」零命中。

### 1.3 顺带发现的缺陷（未修）

`cond_watch.py:162` 到期提醒 `push_event("warn","条件单即将到期", f"...")`：
① 缺 body 参数 → TypeError；② 事件名不在白名单 → ValueError；
外层 `except Exception: pass`（:169）吞掉 → 长期条件单临期提醒从未推送过。
另：`_append_failure_suffix`（push_queue.py:213）靠重写末行，多线程/多进程必错行，勿照抄。

## 2. 面板 v2 现状：零推送

| 检查项 | 结果 | 证据 |
|---|---|---|
| core 有 push/notify | 无 | `app/core/` 目录 |
| ops 插件有推送 | 无 | ops/backend.py:1-25 |
| `NOTIFY_TYPE_KEYS` | battle_plan_rw:105、compat:87-89/:1640，只读展示/校验 | — |
| 外部通道 | 无 | 全 app/ 搜 qq_push/smtp/webhook/dingtalk/telegram/wechat 零命中 |
| 条件单评估 | 8810 进程内 feed 派发线程 tick 驱动 | paper/service.py:184-217、feed.py:555 |
| 触发后推送 | 不推，`evaluate` 返回值无人消费 | paper/orders.py:873-1062 |

### 2.2 两个现成注入口

1. `alert_sink(level,event,title,body)`：签名与 `push_event` 前四参一致。
   注入 `service.py:51`，调用 `broker.py:470-484`，当前无装配方传入（paper/backend.py:231-233 只接 feed）。
2. `PaperOrderService.on_quotes(quotes)`：feed listener（service.py:184），
   `evaluate` 返回值在此丢弃 → 触发推送应加在这里，不碰状态机。

### 2.3 K 线右键菜单（v2）

- 模型 `marks.js:523 buildMenuModel`；:542「条件单」占位被 `overlay.js:1104 modelNow`
  （:1114-1118）改为可用、标「纸面」。
- 现有项：刷新行情/分时标记/手工画线/叠加对比/副图/买入/卖出/条件单/委托与条件单。
  （本轮已补「加入/移出自选」。）
- 条件类型 9 类（order_model.js:26-36 = core/paper/orders.py:110）：
  止损/止盈/回落/反弹/突破/定时/保本止损/破五日线/信号买入。
  **没有「回踩均线买入」「关键 K」**；最接近的 `ma5_break` 是卖出、`rebound_buy` 是幅度不是均线。
  → 标注说的「预置已有条件」只有一半，另两个要新增类型。

### 2.4 两本账两个评估者

| | 旧 engine | 面板 v2 |
|---|---|---|
| 账本 | `engine/state/conditional_orders.json` | `projects/panel-v2/data/panel_v2/paper/conditional_orders.json` |
| 评估 | cond_watch 独立进程 5s 轮询 | 8810 内 feed 线程 tick 驱动 |
| 推送 | 无 | 无 |

两本账互不相通；`paths.py:44-52` 明写「与旧 engine/state 完全隔离，双写必炸」。

## 3. QMT 侧：无消息推送

### 3.1 查过

- `envs/qmt312/Lib/site-packages/xtquant/`（250807.1.2，生产用）
- `C:\东北证券NET专业版\bin.x64\Lib\site-packages\xtquant\`（客户端副本）
- `C:\东北证券NET专业版\config_local\`（只有 customer.lua）
- 全盘 `*预警*`（只命中板块名「预警机」）、`xtmodel\`
- 两副本全 `.py` 搜 预警|通知|邮件|微信|短信|send_msg → 零命中

### 3.2 能力清单（全是数据推送）

| 文件:行 | 方法 | 能力 | 边界 |
|---|---|---|---|
| xttrader.py:24 | XtQuantTraderCallback | 回报回调基类 | 需连柜台 |
| :25/:31 | on_connected/on_disconnected | 连接状态 | 进程内回调 |
| :37 | on_account_status | 账户状态 | 同上 |
| :44/:51/:58/:65 | on_stock_asset/order/trade/position | 资产委托成交持仓 | 同上 |
| :72/:79 | on_order_error/on_cancel_error | 下单撤单错误 | 同上 |
| :86-128 | on_*_async_response | 异步回执 | 同上 |
| xtdata.py:1204 | subscribe_quote | 单码 K 线回调 | 仅行情 |
| :1468 | subscribe_whole_quote | 全推行情（v2 feed 在用） | 仅行情 |
| :1138 | get_full_tick | 快照 | 仅行情 |
| :1510 | run() | 维持回调线程 | 仅行情 |

### 3.3 边界

发微信不能、发邮件不能、客户端弹窗不能（QMT「条件预警」是 GUI 内建，无可调用配置/接口）。
能拿到的只有行情回调 + 交易回报回调——事件源，不是通知通道。
可移植的其实只有 `engine/common/qq_push.py`（stdlib-only，urllib 直连 QQ 官方 API）。

## 4. 方案对比

**A. 并入现有条件单，触发即推。** 改 `_on_quotes` 取 triggered；移植 qq_push；新增
`ma_pullback_buy / ma_break_price / key_k_break`；前端 3 文件 + `ConditionAddBody` 加 alert。
优：一条链一个入口。缺：动下单状态机所在文件；新类型要同改 engine 侧（双源）。

**B（推荐一期）. 独立「预警规则」+ 推送，条件单不动。** `alert_rules.json`，规则只读比对不下单，
评估复用同一 feed listener，推送走新建 core/notify，右键只写规则。
优：零侵入下单链，先验通道再决定是否自动下单。缺：多一个概念，菜单要讲清。

**C. panel 写旧 push_queue.jsonl 复用旧通道。** 否决：跨工程耦合（broker.py:49 明禁）；
白名单会拦新类型；`_append_failure_suffix` 多写方错行；与 cond_watch 故障面混。

结论：B 起步，二期并 A。C 不采用。

## 5. 接口契约

新增：`GET /api/notify/list?since&limit`；`GET /api/notify/status`（channel/last_ok_ts/
consecutive_fails/queued/enabled）；`POST /api/notify/test`（唯一真发，二次确认）；
`POST /api/alert_rule/add|cancel|list`。

复用：`POST /api/condition/add` 的 `ConditionAddBody` 加 `alert: bool = true`
（paper/backend.py:266），不改必填字段，旧前端零影响。

新事件：`COND_TRIGGERED`、`ALERT_TRIGGERED`，与旧 EVENT_WHITELIST 分开维护。

通道实现三处必须改：

1. **异步**——feed 派发线程绝不能同步 HTTP（10s 超时 = 全市场行情停摆）；入队 + 独立 sender 线程，
   满了丢最旧并计数。
2. **不抄 `_append_failure_suffix`**（末行重写），失败只写 health + 记录状态字段。
3. **凭据**不读 Hana config.yaml，放 `projects/panel-v2/data/panel_v2/notify.json`（不入 git）
   或环境变量 `PANEL_V2_QQ_*`。

## 6. 风险

- **R1 重复推送**：feed 每 50ms 派发，OCO/confirm_count/重启重放都会多推。
  对策 dedup key=(cond_id,date,trigger_price)，300s 窗口，落盘 notify_dedup.json。
- **R2 进程归属**：推送在 8810 内→面板挂了没预警；要「面板挂了也推」就得独立进程读同账本，
  会与面板重复触发。一期接受「面板在=预警在」，用 `/api/notify/status` 显形；不要两进程评同一本账。
- **R3 凭据**：现 QQ appSecret 在 Hana agent config；v2 直读=跨应用耦合，且 contract 会打码回传。
  对策私有 notify.json + status 只回「已配置」布尔。
- **R4 白名单再踩坑**：新事件不勾选又会落盘不推。对策：预警事件默认不受 notify_types 约束。
- **R5 条件类型三处双源**：engine manual_orders.py:78、v2 orders.py:110、前端 order_model.js:26，
  必须同改 + 契约测试。
- **R6 关键 K 数据依赖**：v2 core 只有 quotes，无日线结构；走 compat_kline 日线或预计算 key_levels，
  拿不到就不建单、不猜。
- **R7 本地条件单口径**：进程不在=条件单不存在（cond_watch.py:44-46）；面板要显示评估心跳。
- **R8 推送噪音**：旧链有 flood 先例（94KB）；单码单日上限 + 同类合并摘要。

## 7. 未找到 / 待确认

1. QMT 客户端「条件预警」推送配置：GUI 功能，目录内无可读配置或接口；若主人配过，
   需主人指路再判断能否反推。
2. v2 是否有 SSE/WebSocket：本次未查 `app/web/` 与 main.py 路由表；做面板内实时红点需补查。
3. 两本账是否合并：docs 无迁移计划；不合并则两处条件单互不可见——产品决策，非技术问题。

## 8. 证据索引

旧链：cond_watch.py:130,157-168,192-193；manual_orders.py:54,78,458,818,1003,1193；
paper_broker.py:489,498,1358-1398；push_queue.py:39,44,52,71,213,243；
qq_push.py:35,44,206；server.py:4188,4207,4245,4279,4414；run_daemons.py:96,116-119；
watchdog.py:570。

现状：push_queue.jsonl（搜「条件单」零命中）；qq_push_health.json（last_ok 2026-10-20 18:31:44）；
battle_plan.json:25-29。

v2：paper/service.py:51,184,208-212；paper/broker.py:180,470-484；paper/orders.py:110,873,1062；
feed.py:555；paths.py:44-52；paper/backend.py:231-233,266,352；battle_plan_rw/backend.py:105；
compat/backend.py:1640；contract.py:49,68；kline/frontend/marks.js:523,542；
overlay.js:1104-1118；order_model.js:19-36；order_panel.js:461。

QMT：xttrader.py:24-128；xtdata.py:1138,1204,1468,1510；两副本全 .py 关键词零命中。
