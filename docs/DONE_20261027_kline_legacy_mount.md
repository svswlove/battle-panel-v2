# K 线弹窗：旧版完整弹窗原样挂载（2026-10-27）

> 主人裁定：**停止功能重写，把以前已经做好的旧版 K 线完整弹窗直接作为插件挂载**。
> 上一版自绘的简化弹窗（`frontend/overlay.js` + marks/indicators/subcharts/order_panel/order_model）
> **保留不动，作回退**，但不再是入口。
> 执行：子代理实现与浏览器自测；独立验收归 01。

## 1. 改动面（白名单内，无越界）

| 文件 | 性质 |
|---|---|
| `app/plugins/kline/frontend.js` | 一行换入口：`./frontend/overlay.js` → `./frontend/legacy_modal.js?v=0.7.1` |
| `app/plugins/kline/frontend/legacy_modal.js` | **新增**装载器（CSS/DOM 幂等插入 + 旧序加载 4 库 4 模块 + 对外 openKline/closeKline） |
| `app/plugins/kline/frontend/legacy/modal.html` | **新增**，自 `engine/web/index.html:2722-2822` 原样提取的 `#klineModal` 完整 DOM |
| `app/plugins/kline/frontend/legacy/modal.css` | **新增**，自同一 `<style>` 块提取的弹窗原 CSS（331 条规则） |
| `app/plugins/kline/tests/test_kline_legacy_mount.py` | **新增**静态契约 16 项 |
| `app/plugins/kline/tests/js/legacy_mount.cdp.mjs` | **新增**真实浏览器自测 32 项（Chrome CDP 直连 8810） |
| `app/plugins/kline/tests/js/legacy_compare.cdp.mjs` | **新增**对照 `/legacy/` 同代码页面的几何比对 |
| `app/plugins/kline/tests/js/legacy_entry.cdp.mjs` | **新增**真实入口（壳内点个股名）开弹窗验证 |
| `app/plugins/kline/tests/test_kline_overlay_p1a.py` | 文件面白名单 +2 处（授权 `legacy_modal.js`，否则守卫失败） |
| `app/plugins/kline/plugin.json` | `version` 0.7.0 → **0.7.1** |
| `app/plugins/kline/frontend/overlay.js`、`order_panel.js` | 仅 `?v=` 版本串跟随 bump（6 条 import 守卫同步） |

**未动**：`engine/web/` 任何文件、四个旧 JS（`kline_lc.js` / `kline_quote5.js` / `kline_f10.js` / `kline_order.js`）、
`app/core/`、paper 接口、manifest 版本以外的字段。**未发订单/条件单交易写请求；旧模块固有指标设置 PUT 保留、未拦截**（见 §4 第 2 条）。

## 2. 做法

### 2.1 DOM / CSS 提取（不是手敲）
一次性脚本从旧 `index.html` 抽出 `#klineModal` 整块（`.kbox` / `#kQuotePanel` / `#kF10Panel` 全含），
并保留旧 `onclick="KLineApp.setPeriod('d')"` 这类内联 handler——旧模块靠这些字符串绑定，改名即静默失效。
CSS 从旧 `<style>` 块按选择器筛出弹窗所需五组（K 线主域 / HQChart 画布 / 盘口 `kqp-*` / F10 `kfp-*` /
下单 `ko-*` 与画线菜单 `dd-*`），**并把旧页的裸元素规则（`button` / `select option`）限定到 `#klineModal`**，
避免作为插件资源加载时污染壳的通用控件。脚本用完即删，产物留插件。

### 2.2 装载顺序（照旧页 `index.html:9022-9029`）
```
vendor/lightweight-charts.standalone.production.js
vendor/lightweight-charts-drawing.umd.js
vendor/jquery.min.js
vendor/hqchart.umychart.min.js
kline_lc.js → kline_quote5.js → kline_f10.js → kline_order.js
```
`vendor/klinecharts.min.js` 略（现 LC 不用，旧 v9 块在旧页已置 `type="text/plain"`）。
全部**经典 script**（`async=false` 保序）：四个旧模块是 IIFE，靠 `window.KLineApp` 串链。

### 2.3 幂等与失败口径
- CSS / DOM / 每个脚本各插一次（DOM 先于 `kline_lc.js`，因为旧模块在求值时就 `if ($("klineModal")) init()`）；
- 初始化收敛到单一 Promise，并发调用共用同一次；已有 `window.KLineApp` 直接短路，不重复建实例；
- 失败给**可见红条 + console.error**，**不静默切回简化版**（主人明确要求）。

### 2.4 对外接口
`openKline({api, code, name, period})` / `closeKline` / `openKlineStandalone` 三个名字全保留。
内部按旧 `open` 的**真实签名** `open(code, name, list)` 调用（第三位是同列表上下键上下文，壳层没有，不传）；
`period` 走旧 `setPeriod`，白名单与 `kline_lc.js` 的 `PERIOD_KEYS` 一致。
`api` 保留在签名里与壳的 `makeCtx` 同形，但旧模块走同源绝对路径取数，不需要它。

### 2.5 主题
**不另造主题**：`modal.css` 里没有 `:root` / `[data-theme]` 重定义（有测试守），颜色全部走 `themes.css` 语义 token；
壳已经在 `/static/shell.html` 引了 `/legacy/themes.css`，故弹窗打开即为当前主题，无需额外适配层。
打开/关闭交给原 `KLineApp.open/close` 包装链（`kline_order.js` 的 `__orderHooked` 链式包装原样生效）。

## 3. 验证

### 3.1 静态契约 `test_kline_legacy_mount.py`（16 项，全过）
- 旧 DOM 43 个关键 ID 全在；6 处内联 handler 原样；**与旧 `index.html` 的 `#klineModal` 块逐字一致**（防手改漂移）；
- CSS 11 个规则组齐（弹窗外壳 / 图表 / HQChart / 盘口 / F10 / 下单面板 / 下单菜单 / 画线菜单 / 分钟下拉 / 测距卡 / 窄屏兜底）；
  无 `:root`、无 `[data-theme]` 重定义、无未限定裸元素规则；
- 装载顺序与旧页逐项一致；经典脚本；幂等判重与单一 Promise 在位；失败有可见出口；
- 入口指向 `legacy_modal.js?v=<manifest>`；无 iframe；资源在插件静态路由下且带版本串。

### 3.2 kline 插件 pytest：**82 passed**（含既有 66 + 新增 16）
`app/tests/test_fe_shell_bridge.py` + kline：**110 passed**（主题裸 hex / 裸 rgba / 未定义 token 三条门禁均过）。

> 以上为子代理自报。01 独立复跑结果见 §3.6（kline pytest **83 passed**）。

### 3.3 真实浏览器（Chrome CDP → 8810）：`legacy_mount.cdp.mjs` **32/32 通过**
600036.SH 实测：
- 旧 DOM 43 个关键 ID 全在；旧四库+四模块**按旧序**装载（`data-kline-legacy` 顺序断言）；
- LC 主图真渲染：`#klineChart` canvas ≥2、pane ≥2、bars >50；标题 `招商银行 600036.SH`；
- 原周期/工具/指标/自选控件齐（3 个周期按钮 + 工具 + 指标①② + ☆自选）；盘口卡与 F10 卡由旧模块接管；
- 切档 d → t → m5：`t` 走 HQChart（`hqAlive().hasChart=true`、canvas>0、盘口卡显示）；`m5` 回 LC 且有数据；
- 指标设置面板（右键指标①）可开且含真控件；工具面板可开且含 **8 个原画线按钮**；
- **右键下单**：旧版口径是「蜡烛图右键不弹菜单、只在分时档弹」(`kline_order.js:1317-1324`)，实测分时右键出原菜单（≥3 项），
  点「买入」开面板：3 个 Tab、数量框、提交按钮在位，**未点提交**；
- 重复开/关/换票：`#klineModal` 恒 1、`link#kline-legacy-css` 恒 1、`script[data-kline-legacy]` 恒 8；换票后标题与数据跟着换；
- 两档窗口（1920×1080 / 1440×900）：**三栏（.kbox / #kQuotePanel / #kF10Panel）重叠面积 0**；
  弹窗不越视口、高度 = 88vh（±3px）、旧头部工具条单行可见、HQChart 分时仍在渲染；
- console exception **0**；console error 级日志 **0**（剔除 `/v2/` 页面自身的 `/favicon.ico` 404，与本插件无关）；
- 写请求：订单/条件单/交易类 **0**，其它 **0**（此分类口径**未覆盖**指标设置 PUT）。
  旧模块固有的 `PUT /api/kline-indicator-settings` 实际发了 2 次（见 §4 第 2 条），
  故不得据此宣称「全部写请求 0」。

截图：`_vis/legacy_mount/01..06`（`01_open_d_1920` / `02_open_t_1920` / `03_open_m5_1920` /
`04_menus_1920` / `05_order_panel_1920` / `06_t_1920` / `06_t_1440`）。

### 3.4 真实入口（不是只调模块）：`legacy_entry.cdp.mjs` PASS
在 `/v2/` 壳里点侧栏「选股」→ 点个股名（`data-open-kline="601921.SH"`）→ shell 捕获阶段委托 → `ctxOpenKline` → 插件入口：
弹窗 `display=flex`、标题「浙版传媒 601921.SH」、`#klineChart` canvas 23、`window.KLineApp` 就绪、可见错误条为空、console 异常 0。
（自选池卡当前为空数据，卡内无 `[data-open-kline]`，故用选股卡走同一委托路径。）

### 3.5 对照 `/legacy/`（同四份 JS 同 DOM）：`legacy_compare.cdp.mjs` 全项一致
| 量 | /legacy/ 旧整页 | 插件挂载 |
|---|---|---|
| `.kbox` | 1100×950 @ (246,65) | 1100×950 @ (246,65) |
| `.khead` | 1070×31 @ (261,78) | 1070×31 @ (261,78) |
| `#kF10Panel` | 316×950 @ (1358,65) | 316×950 @ (1358,65) |
| `#kQuotePanel` | 隐藏（日线档） | 隐藏（日线档） |
| `#klineChart` canvas | 23 | 23 |
| 标题 / 状态行 | 招商银行 600036.SH / 前复权 ｜ 240 根 ｜ 至 20261027 | 完全相同 |

→ **几何与 DOM 与旧页逐像素一致，不需要适配层 CSS**（所以本轮没有加任何覆盖样式）。

### 3.6 01 独立验收（主会话亲跑，非子代理自报）
- **kline pytest：83 passed**（`pytest app/plugins/kline/tests -q`）。
- **真实入口 `legacy_entry.cdp.mjs` PASS**：壳内选股卡点个股名 `601921.SH`（浙版传媒）→ shell 委托 → 插件入口开弹窗，
  `#klineChart` canvas **23**、可见错误条为空、console 异常 **0**。
- **`legacy_mount.cdp.mjs` 32/32 通过**。注意：该脚本的写请求分类（订单/条件单/交易类 0、其它 0）
  **不含**旧 `kline_lc.js` 自动发的 2 次 `PUT /api/kline-indicator-settings`；独立复跑确认这两次 PUT 真实存在，
  故不得表述为「全部写请求 0」（见 §4 第 2 条）。
- **`legacy_compare.cdp.mjs` 与 `/legacy/` 同几何**：1100×950、`.khead` 1070×31、`#kF10Panel` 316×950、canvas 23，
  与 §3.5 表一致。
- **截图人工查看**：`_vis/legacy_mount/01..06` 肉眼确认为旧版完整弹窗（旧头部工具条 / 旧盘口卡 / 旧 F10 栏），
  不是简化版。

## 4. 未验证 / 已知边界

1. **写路径未实测**：下单面板只开到「面板可见 / 首击前」，**没有点过任何提交**；
   条件单、撤单、记录页的真实落库链路本轮**未跑**（旧 `kline_order.js` 的写接口在 8810 compat 已 paper 适配，
   但本轮没有验证其真实响应）。
2. **旧 `kline_lc.js` 自身会发 `PUT /api/kline-indicator-settings`**（指标面板持久化，实测 2 次）。
   这是旧模块固有行为、不在订单链上，但**它确实是写请求**——已如实记录，未拦截、未改旧文件。
3. `brick`（砖形图）、主图 MA/BOLL/缠论等旧版能力**本轮未逐一回归**：它们由旧模块原样带入，本轮只验证了
   「旧模块真在跑、主图多 pane 真渲染、工具/指标菜单可开」，没有逐指标对拍。
4. 旧页头部「＋选股池」按钮依赖 `#panel-selection.active`，v2 壳没有该节点 → 按旧逻辑自动隐藏（不是新 bug）。
5. `?v=` 版本串需随前端改动手动 bump；已打开的页面不会自动换模块（浏览器固有行为）。

## 4.1 全仓门禁现状（子代理自报，非本轮引入，01 未独立复跑）

> **口径声明**：以下两项结论与数字均为**子代理自报**，01 主会话**没有**独立全量复跑 `cutover_gate.py`，
> 不构成 01 的验收结论。本轮按指示不重跑全仓。

`python tools/cutover_gate.py` → **FAIL**，两项：
- **8810 接口结构回归 FAIL**：15 处结构变化（`market_context.overnight_news`、`scenarios`、`battle_plan.positions` 等），
  属数据形状漂移，与本次前端改动无关；
- **pytest 全套 FAIL**：`13 failed / 1548 passed / 3 skipped`，其中
  **12 条 = 交接时已存在的 intraday 行号对齐问题**（`test_audit` 6 + `test_evo` 3 + `test_quotes` 2 + `test_research` 1，
  均为「与旧页逐字一致」类断言，改的是旧页文本行号），
  另 **1 条 `ops::test_panic_store_mode_shape`**（断言 `source=='store'` 实得 `eltdx:880005+...`，
  依赖当日 panic 库末行日期，属数据状态相关；本轮未改 ops）。

kline 插件自身（子代理自报）：`pytest app/plugins/kline/tests` **82 passed**；
`app/tests/test_fe_shell_bridge.py` + kline **111 passed**（含裸 hex / 裸 rgba / 未定义 token 三条主题门禁）。
01 独立复跑 kline pytest 为 **83 passed**（见 §3.6），与自报 82 差 1，差因未追查。

## 5. 回退

把 `app/plugins/kline/frontend.js` 第 451 行附近改回
`import { openKline, closeKline } from "./frontend/overlay.js?v=0.7.2";` 即回到上一版简化弹窗（文件都还在）。
`legacy_modal.js` 与 `frontend/legacy/` 可保留（不加载即零开销）。

---

# 补丁（0.7.2）：点卡片外空白关闭

> 主人指出：**点卡片外区域退出卡片在 V2 全局通用，但 K 线弹窗没使用**。
> 根因：旧 `engine/web/kline_order.js:1383` 明确**取消**了卡片外关闭（2026-10-21 怕拖拽误退），
> 原样挂载把这行为一起带了回来。V2 口径见 `paper/frontend.js:845` 与 `sector/frontend/detail.js:326`，
> 都是 `mask.addEventListener('click', ev => { if (ev.target === mask) close(); })`。

## 6.1 改动面（只动挂载层）

| 文件 | 改动 |
|---|---|
| `app/plugins/kline/frontend/legacy_modal.js` | 新增 `bindMaskClose()`；`ensureDom()` 给自插 DOM 打 `data-kline-legacy-mount="1"`；`doInit()` 在 `ensureDom()` 之后、`legacyReady()` 短路之前调用；`V` 0.7.1→0.7.2 |
| `app/plugins/kline/plugin.json` | `version` 0.7.1 → **0.7.2** |
| `app/plugins/kline/frontend.js` | 2 条 import/注释版本串同步（`marks.js`、`legacy_modal.js`） |
| `frontend/overlay.js`、`frontend/order_panel.js` | 仅 `?v=` 版本串跟随 bump（6 条 import 守卫同步） |
| `app/plugins/kline/tests/test_kline_legacy_mount.py` | 新增 3 项静态契约（+16 → 19 项） |
| `app/plugins/kline/tests/js/legacy_mount.cdp.mjs` | 新增 5.5 段 15 项真实浏览器断言；`V` → 0.7.2 |
| `app/plugins/kline/tests/js/legacy_compare.cdp.mjs` | 入口版本串 → 0.7.2 |

**未动**：`engine/web/` 任何文件、旧四 JS（`kline_lc/quote5/f10/order.js`）、`modal.html`、`modal.css`、
`app/core/`、shell、paper 接口。

## 6.2 实现（三条约束，与任务书一致）

```js
function bindMaskClose() {
  const m = document.getElementById('klineModal');
  if (!m || m.getAttribute(MOUNT_ATTR) !== '1') return;   // 不是本插件插的 DOM：不插手
  if (m.dataset.maskClose === '1') return;                // 幂等：只绑一次
  m.dataset.maskClose = '1';
  let downOnMask = false, upOnMask = false;
  m.addEventListener('pointerdown', (ev) => { downOnMask = (ev.target === m); upOnMask = false; }, true);
  m.addEventListener('pointerup',   (ev) => { upOnMask = (ev.target === m); }, true);
  m.addEventListener('click', (ev) => {
    if (ev.target !== ev.currentTarget) return;           // 子卡片/侧栏/工具栏自然不关
    const both = downOnMask && upOnMask;
    downOnMask = false; upOnMask = false;
    if (both) closeKline();
  });
}
```

1. **用 `click`（与 V2 同事件）**：`pointerdown` 阶段旧模块要画线/起拖拽，不能在那时关窗。
2. **只判 `ev.target === ev.currentTarget`**：真正落在遮罩空白才关；`.kbox` / `#klineChart` /
   `#kQuotePanel` / `#kF10Panel` 的点击 target 是子元素，自然不关。
3. **不用 document 级外点监听**：`koPanel` / `koMenu` / `ddMenu` 都挂在 `body` 上（
   `kline_order.js:295/318/386`），document 级监听会把「点下单卡」判成外部点击而误退。
4. **不照抄「拖拽时外松关闭」**：浏览器对「图表内按下 → 遮罩上松开」也会派一个 click，
   target 恰好是共同祖先（就是遮罩）。故记 pointerdown/pointerup 两个落点，必须**按下与松开都在遮罩上**才关；
   两者都只记录，不 `preventDefault`、不 `stopPropagation`。

**双落点判断不是过度设计（已做负向验证）**：在页面上另挂一个朴素版监听（只判 `target===currentTarget`），
同一段「图表内按下 → 拖到遮罩 → 松开」的真实 CDP 拖拽触发它 **1 次**；而本实现的弹窗保持 `flex` 未误关。

## 6.3 验证（TDD：先红后绿）

**RED**（先写断言，未改实现时跑）：`legacy_mount.cdp.mjs` **45/47**，红的两条正是：
- `点遮罩空白（左键）关闭弹窗` → 实得 `shown=flex`（不关）；
- `重开后一次空白点击只触发一次 close` → `closeCalls=0`。
其余 45 条（含全部「点子卡片不关」防护项）已绿——说明断言能区分「没实现」与「实现错」。

**GREEN**（改实现后）：`legacy_mount.cdp.mjs` **47/47 通过**。新增 15 项断言逐条：

| 断言 | 方式 | 结果 |
|---|---|---|
| 探到真正落在遮罩空白上的点 | `elementFromPoint(x,y) === #klineModal`（四角/边缘八候选） | ok |
| 探到 `.khead` / `#klineChart` / `#kF10Panel` 落点 | 框内扫格 + `elementFromPoint` 回验 | ok |
| 点 `.kbox` 内头部 不关 | 真实 `Input.dispatchMouseEvent` 左键 | ok |
| 点 `#klineChart` 不关 | 同上 | ok |
| 点 `#kF10Panel` 不关（日线档） | 同上 | ok |
| 从图表拖到遮罩松开 不关 | 真实 mousePressed→6 段 mouseMoved→mouseReleased | ok |
| 点 `#kQuotePanel` 不关（分时档） | 同上 | ok |
| 下单卡 `koPanel` 在 `#klineModal` 之外且可点 | `closest('#klineModal')===null` + 扫格 | ok |
| 点 body 上的 `koPanel` 不关 | 真实左键 | ok |
| **点遮罩空白（左键）关闭** | 真实左键 → `style.display==='none'` | **ok** |
| 关闭后可重开 | — | ok |
| Esc 仍能关闭（旧行为未破坏） | 真实 `Input.dispatchKeyEvent` Escape | ok |
| 重开后一次空白点击只触发一次 close | 临时包 `KLineApp.close` 计数 | ok（`closeCalls=1`） |

**真实鼠标事件，不是 dispatch 假 target**：全部走 `Input.dispatchMouseEvent`（trusted），
且每个落点先用 `elementFromPoint` 探明到底命中哪个元素再点——避免「以为点了遮罩其实点在图表上」。

**静态契约**：`test_kline_legacy_mount.py` 新增 3 项——绑定在 `#klineModal` 而非 document、
必须 `target===currentTarget`、必须 down/up 双落点、幂等且只对自插 DOM 生效、
绑定位置在 `ensureDom()` 之后与 `legacyReady()` 短路之前。

**回归数字**（本轮实跑）：
- `pytest app/plugins/kline/tests -q` → **86 passed**（上轮 83 + 新增 3）；
- `pytest app/tests/test_fe_shell_bridge.py app/plugins/kline/tests -q` → **114 passed**（主题三条门禁均过）；
- `legacy_mount.cdp.mjs` → **47/47 通过**；
- `legacy_compare.cdp.mjs` → 与 `/legacy/` 仍逐项一致（`.kbox` 1100×950 @(246,65)、`.khead` 1070×31、
  `#kF10Panel` 316×950、`#klineChart` canvas 23=23、标题/状态行相同）；
- `legacy_entry.cdp.mjs` → PASS（壳内选股卡点 `601921.SH` → 弹窗 `flex`、canvas 23、异常 0）。

**画线工具未被误伤**（实测）：打开工具面板 → 点「线段」→ 在图上真实拖一段，
`window.__ddDebug` 记到 `{hit:'pointerdown', tool:'trend-line', spec:true, d0:true}`、
状态行变「划线已完成」、`#klineChart` canvas 仍 23、弹窗保持 `flex`。

**截图**：`_vis/legacy_mount/07_before_mask_click_1920.png`（点空白前，弹窗+盘口卡+F10 卡三栏）、
`08_after_mask_click_1920.png`（点空白后，回到仪表盘）。人工查看确认。

## 6.4 已知边界

1. **写请求口径未变**：本轮同样未点任何提交；`PUT /api/kline-indicator-settings` 仍由旧
   `kline_lc.js` 自发（本轮跑动次数 3~5 次，取决于用例里切了几次周期），非本轮引入。
2. **右键不关**：本实现只认左键 `click`（浏览器 `click` 本身就只由主键触发），
   右键菜单行为完全交给旧模块，未测「右键点遮罩」——按事件语义它不产生 `click`。
3. **触屏**：pointerdown/pointerup 用的是 pointer 事件，触屏点击语义与鼠标一致；
   但本轮只在桌面 Chrome 1920×1080 / 1440×900 两档验证，**未做移动端/触屏实测**。
4. **旧页 `/legacy/` 不受影响**：`bindMaskClose()` 只对带 `data-kline-legacy-mount="1"` 的
   `#klineModal` 生效（即本插件插的那一个），旧整页的原生弹窗仍是旧行为（外点不关）。

