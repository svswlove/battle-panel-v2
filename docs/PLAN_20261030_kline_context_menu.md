# 2026-10-30 K 线右键菜单：加自选 / 条件单预警（方案，待落地）

> 需求来源：标注 `N20261028-220052-48`——「给K线图也加上右键菜单功能，包括加入自选，设置条件单预警并推送
> （预置入如回踩均线，跌破均线到某个特定价位或关键K等已有的条件），QMT好像有现成的推送模块可以移植过来做成插件」

## 1. 现状（已摸清，不用新建模块）

| 能力 | 位置 | 状态 |
|---|---|---|
| 右键菜单框架 | `app/plugins/kline/frontend/overlay.js`（`.klm-menu`，header + 项 + 分隔 + 子菜单）+ `marks.js`（菜单模型纯函数，可 node 单测） | 已有，P1a 落地 |
| 条件单菜单项 | `marks.js:542` `{ id:'cond', label:'条件单（需写入通道）', disabled:true, reason:'P1c 接入条件单写入后开放' }` | 占位，未接线 |
| 条件单写入 | `order_model.js`：`addCond:'/api/condition/add'`、`cancelCond:'/api/condition/cancel'` | 通道已在 |
| 条件单字段契约 | `paper/backend.py::ConditionAddBody`：`code/type/qty/trigger_price/pct/amount/order_type/limit_price/side/at/valid_until/group/confirm_count/buffer_atr/signal`，2026-10-28 已加**回踩均线/关键K 的目标位与容差**（auto/yellow/white、auto/upper/lower） | 后端已支持主人说的预置条件 |
| 推送 | 引擎侧 `cond_watch_v2.py` + notify | 已有，K 线侧不重复做 |

结论：**不需要新插件、不需要 QMT 推送模块**——写入通道和条件单执行/推送链路都已存在，缺的只是右键菜单里的入口和参数预填。

## 2. 落地设计

菜单新增两块（插在现有「副图」之后、「划线」之前）：

**① 加入自选**
- 读当前票是否已在自选（watchlist 现有查询接口），已在 → 项变「已在自选」且 disabled；
- 未在 → 点击调 watchlist 的添加接口，成功后 toast「已加入自选」并刷新自选卡。

**② 条件单 ▸**（子菜单，预置模板）
| 模板 | 预填 | ConditionAddBody |
|---|---|---|
| 回踩 MA5 / MA10 / MA20 买入 | 目标位取当前周期对应均线值，容差按 `buffer_atr` | `type='pullback'`、`signal='ma5'/'ma10'/'ma20'`、`side='buy'` |
| 跌破 MA5 / MA10 / MA20 卖出 | 同上，方向反向 | `type='break'`、`signal='ma5'…`、`side='sell'` |
| 跌破关键 K 低点 / 突破关键 K 高点 | 取最近关键 K 的 low/high | `signal='keyk'`、目标位=关键K价 |
| 指定价位 | 弹输入框，预填右键位置价格（已有 `价格 → 主图 Y` 换算，`kline_lc.js:2758`） | `trigger_price` |

参数面板沿用 `order_panel.js` 的既有样式（数量/有效期/确认次数），不新造一套。

**③ 触发后的推送**：条件单落库后由引擎 `cond_watch_v2` 监听，触发走 notify；K 线侧只负责写入 + toast 回执，不自己轮询。

## 3. 顺序与冲突

- 先等「K 线视口默认看最新」（标注 42）改完 `kline_lc.js` 再动，避免同文件双改。
- 改动白名单：`app/plugins/kline/frontend/{marks.js, overlay.js}` + `marks.js` 的 node 单测 + 必要时 `order_panel.js`。
- 不动：`engine/web/kline_lc.js`（除非右键位置价格取不到）、paper 后端契约、cond_watch 引擎。

## 4. 验收

- `marks.js` 纯函数单测：菜单项存在性、已在自选的 disabled 态、模板 → body 字段映射。
- 实机（headless）：右键菜单 → 条件单 → 回踩 MA10 → 提交 → `/api/condition/add` 返回成功 → toast；
  自选池里该票出现；条件单列表里有这条且状态 ACTIVE。
- 回归：现有划线菜单、副图菜单不受影响。
