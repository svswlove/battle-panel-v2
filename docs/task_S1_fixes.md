# 任务书 · 周末批次 S1 三问题修复（板块卡个股点击 / 主力动向 mf / F10 B2 段）

> 派单：01 → 子代理 ｜ 2026-10-24 深夜 ｜ 用户实测反馈三问题，根因已全部定位

## 三个问题与修法

### 1. 板块详情卡点个股无反应

**根因（已实锤）**：板块详情卡是 8002 工作台的 iframe（跳源），个股点击 `window.parent.postMessage({...}, PANEL_ORIGIN)`，而 `PANEL_ORIGIN = 'http://127.0.0.1:8800'` 写死在 `夏天-activity\工具\dashboard\web\app.js`。8810 打开面板时父页是 8810 源，消息发到 8800 的窗口（不存在/不是父页）→ postMessage 静默丢弃。父页（engine/web/index.html:7562）的监听器本身工作正常（只认 ev.origin===8002 ✓）。

**修法**：8002 前端 app.js 的 `PANEL_ORIGIN` 改为动态：iframe 内 `document.referrer` 的 origin（拿不到退回 8800 保底）。这是 8002 工作台自己的文件（不属于旧引擎 server，可改；改前先确认 app.js 是 8002 独占资源）。注意 `IN_IFRAME` 判断已有。改完在 8810 上实测：板块卡内点个股 → 父页 KLineApp.open 弹 K 线。
**注意**：这个文件是夏天的工作台，改动要最小（只动 PANEL_ORIGIN 一处逻辑），改后 8800 场景也要回归（iframe 模式下 referrer=8800/8810 动态正确；独立打开模式无 referrer 走 IN_IFRAME=false 分支不受影响）。

### 2. K 线主力动向指标「无 L2 数据」

**根因（已实锤）**：compat_kline 的 `mf` 字段是骨架（`_mf_skeleton`，source="unavailable(口径库未登记)"）。口径唯一源在 `OH-WorkSpace\Hubagent\01\_tools\l2_mf.py`（01 自己的 L2 工具，含 7709 通道取数），迁移时未登记口径库白名单。

**修法**：
1. core paths.py `READABLE_LIB` 登记 `l2_mf`（exports 先读 l2_mf.py 源码确定：它可能既有纯计算又有自连接取数——只导出纯计算函数或收连接的函数；若有自取数函数（如内部连 7709），评估：口径库允许它自连吗？先例：amv_contrib 的 board_parts 收连接。**如果 l2_mf 的取数函数自带连接且复杂，允许导出取数函数本身**（它是数据客户端不是口径重写），AST 扫描 import 期无副作用即可）
2. compat_kline 的 kline 端点：mf 段从骨架改为真实现（调 ctx.lib("l2_mf") 的函数，取数失败回退骨架+如实 source 标注，不抛）
3. 测试：mock lib 返回样本数据 → mf 字段非空；lib 不可用 → 骨架降级不炸

### 3. F10 经常不可用

**根因（已实锤）**：compat_kline 的 f10_brief B1 段已实现（socket 批 0x0ffc），**B2 段（估值/市值/北向/盈利预测/增减持/融资融券/季度业绩 15 字段）未实现**——依赖 eltdx F10 HTTP 客户端（旧端 source 显示 `eltdx:f10(200191)`），门面未暴露。

**修法**：
1. core quotes.py（eltdx 门面）补 F10 HTTP 客户端方法（读旧 8800 server.py 的 f10 实现怎么调 eltdx：函数名/参数/响应结构，照搬为门面方法 `f10_brief(glb)`，TTL 缓存 60s 档）
2. compat_kline 的 `build_f10_brief` B2 段接线：15 个字段从门面取，异常兜底 null（与 B1 同模式）
3. 活体验收：8810 `/api/f10_brief/600519.SH` 与 8800 同参 15 个差异字段全部有值且逐值相等（source 字段变为 `eltdx:f10+socket` 同款）
4. 「经常不可用」的间歇性可能来自上游 HTTP 抖动——门面层加重试一次（同连接），失败如实标注

## 改动白名单

```
夏天-activity\工具\dashboard\web\app.js          # 仅 PANEL_ORIGIN 动态化（问题1）
app/core/paths.py                                  # l2_mf 登记（问题2）
app/plugins/compat_kline/backend.py + tests/       # mf 真实现 + f10 B2 段（问题2/3）
app/core/quotes.py + tests                         # F10 门面方法（问题3）
docs/HANDOFF_20261024_S1_fixes.md
```

## 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q
python tools\cutover_gate.py
# 活体（临时端口实例）：
# f10: 8810 vs 8800 15 差异字段逐值相等
# mf: /api/kline/600519.SH?period=d&limit=5 的 mf 段 source 非 unavailable、主力资金数组非空
# 板块卡：ego/CDP 实测 8810 打开板块卡点个股弹 K 线（PANEL_ORIGIN 动态化后）
```

## 交付

回执 ≤300 字；明细落 HANDOFF；不 commit 不重启 8810。
