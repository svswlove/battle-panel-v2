# 2026-10-30 K 线日线右键菜单 + 主图设置（已落地）

> 主人需求：① 日线右键不要分时那套买卖为主，要「加入自选」+「条件单预警」；
> ② 默认显示根数可填**任意**值，放右键「主图设置」子菜单；
> ③ 筹码分布等从「工具」搬进「主图设置」，工具只留画线。

## 0. 上一轮的关键纠正（保留记录）

先改错了层：`kline/frontend/{marks.js,overlay.js}` 那套右键菜单是**回退路径**（`frontend.js:450` 原话），
v2 弹窗实际跑的是旧模块 `kline_lc.js` + `kline_order.js`。本轮把菜单落到后者。

## 1. 改法

| 文件 | 改动 |
|---|---|
| `engine/web/kline_order.js` | `menuHtml()` 按 `st.period` 分支：分时保留「买入/卖出 + 分时标记」；**日线/周线/分钟**改为「加入/移出自选 · 条件单 ▸ · **主图设置 ▸** · 委托与条件单 · 叠加对比 · 刷新行情」。新增 `mainMenuHtml()`（60/120/240 根 + 自定义根数… + 五个开关）、`visibleBars()/setVisibleBars()`、`toolsOf()`；`onMenuAction` 接 `main/vb/vb_custom/tools_*`，悬停即开子菜单 |
| `engine/web/kline_lc.js` | `applyDefaultVisibleBars` 读 `localStorage['kline_visible_bars_v1']`，**任意 5~2000 整数**（不再限三档），缺省 60；新增 `KLineApp.toolsState()/setTools()` 供菜单读写五个开关 |
| `kline/frontend/legacy/modal.html` | 工具面板「浮层」段隐藏，只留「划线 / 测量」；旧 input 保留为隐藏节点（旧模块 `syncToolUI`/`onchange` 依赖） |

自定义根数用 `window.prompt` 输入，范围 5~2000，非法值提示后不写。

## 2. 实机验证（headless，8810）

| 项 | 结果 |
|---|---|
| 日线右键菜单项 | `加入/移出自选☆ · 条件单▸ · 主图设置▸ · 委托与条件单0 · 叠加对比▸ · 刷新行情↻`（**无买入/卖出/分时标记**） |
| 主图设置子菜单 | `60 根✓ / 120 根 / 240 根 / 自定义根数…60` + `筹码分布✓ / 复权事件✓ / 成本线✓ / 买卖点✓ / 止盈止损线✓` |
| 点「120 根」 | `localStorage['kline_visible_bars_v1'] = "120"` |
| **任意根数 137** | 重开 K 线：240 根数据、可见范围 from **103** / to 243 —— 正是 240−137，任意值生效 |

截图：`_vis/verify_20261030/kline_menu_main.png`。

## 3. 测试

`python -m pytest app/plugins/kline/tests -q`：**94 passed**
（`test_kline_legacy_mount` 的 modal.html 逐字对照新增 `<!-- v2:toolpanel -->` 段豁免；`p1a_marks.test.mjs` 新增日线菜单断言，覆盖回退路径那套。）

## 4. 边界

- 未重启 8810，静态资源协商缓存，刷新即生效。
- 分时右键分支代码保留但未单独实测（`st.period === 't'` 时仍是 买入/卖出/条件单/分时标记）。
- 回退路径（`marks.js`/`overlay.js`）的 equivalent 菜单保留未删，将来切回那套时同样可用。
