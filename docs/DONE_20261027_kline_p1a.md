# K 线弹窗 P1a：右键菜单框架 + 手工画线 + 真尺寸重画（2026-10-27）

> 负责人：01（执行器）｜授权：write，白名单见任务书｜验证：静态 + 单测 + 一次性浏览器检查（8810 实时验收由主人做）
> 回退入口：HANA 文件历史（`OH-Works/文件恢复指引.md`），未留 `.bak`

## 1. 改动清单

| 文件 | 性质 | 内容 |
|---|---|---|
| `app/plugins/kline/frontend/overlay.js` | 修改（570 → 921 行） | 主图右键菜单、标记层 canvas、手工水平线、尺寸记忆/拖角/resize 重画、Esc 分级处理 |
| `app/plugins/kline/frontend/marks.js` | 新建（401 行） | 纯函数层：`ko_marks` 开关、`klm_size_v1` 尺寸、`klm_hlines_v1` 手工线、价格轴反算、可用性判定、菜单模型 |
| `app/plugins/kline/tests/test_kline_overlay_p1a.py` | 新建（11 项） | 静态契约（行为入口/禁用原因/不许下单接口/只留 Esc）+ 拉起 node 跑纯函数单测 |
| `app/plugins/kline/tests/js/p1a_marks.test.mjs` | 新建（20 项） | `marks.js` 纯函数断言，含 `GEOM` 与 `frontend.js` 常量的漂移守卫 |

未动：`frontend.js`、后端、paper、core、shell、公共接口、生产 state/账本、其它插件。

## 2. 做了什么

### 2.1 主图右键菜单（框架）

菜单模型在 `marks.js::buildMenuModel`，渲染在 `overlay.js::openMenu`，与旧版 `kline_order.js` 同构（header → 项 → 分隔 → 子菜单右侧展开，悬停即展开、点击也能展开）：

- 头部：`名称 代码 · 光标价`（光标价由当前周期价格轴反算；不在绘图区内则显示 `—`）
- 刷新行情（可用）
- 分时标记 ▸ 子菜单
- 手工画线 ▸ 子菜单
- 「下单与叠加 · 本阶段未接入」分组：买入 / 卖出 / 条件单 / 叠加对比 —— **四项一律 disabled，并在项内直接写出原因**（`P1b 接入下单后开放` 等），点不动、也不假装刷新

判据落点：`disabled: true` 的项必须同时有 `reason`（单测 `test_disabled_items_always_carry_reason` 与 node 用例 `菜单：下单/条件单/叠加一律 disabled 且写原因` 双向守）。

### 2.2 分时标记子菜单（逐项开关 + 全部关闭 + 持久化）

- 四项：买卖点 / 持仓成本线 / 委托线 / 条件单线，勾选态来自 `ko_marks`（**沿用旧版同一把 key**，默认全开）
- 每项右侧：有数据显 `N 条`，无数据显 `无数据`，并在项下写明原因（本阶段四项数据源都未接，原因文案为 `…数据未接入（P1b）`）
- 「全部关闭」一键四项归 false；已全关时该项自身 disabled 并说明（`四项已全关`）
- 开关点击立即落 localStorage 并原地刷新打勾 —— 点击有可见反馈，不构成假控件

### 2.3 标记线「有数据才画」

- `marks.js::lineModels` 只输出「开关开着 + 数据有效（`price > 0`）」的线/点，脏数据按没有处理
- `overlay.js::drawMarks` 画在独立的标记层 canvas（`.klm-mk`，`pointer-events:none`，不抢主图点击与右键）
  - 数据线：细虚线 + 右端标签；买卖点：圆点 + `B/S 价格`
  - 手工线：实线 + 左端标价（和数据线一眼分得开）
- P1b 接入点：`openKline()` 返回的 handle 上新增 `setMarkData({bs,cost,order,cond})` 与 `getMarks()`；
  灌数据后即时重画，`null/[]` 即「该类不可用，不画」

### 2.4 手工画线（最小水平线）

旧版 `kline_lc.js` 的绘图是 KLineChart v9 插件（350KB，依赖第三方库，无可独立复用件），但**水平线的交互语义有明文**：
「单点类工具（水平线/垂直线…）→ 按下即落库，无拖拽语义」。据此只补这一种：

- 菜单「手工画线 → 画水平线（点图落线）」进入放置态：光标十字、状态行显示 `点图放水平线 · Esc 取消`
- 图上点一次落一条（价格取该像素反算的真值），落完自动退出放置态并 toast 价格
- `klm_hlines_v1` 按代码存（每图上限 20 条，同价去重），跨弹窗记忆
- 「清除本图水平线」无手工线时 disabled 并说明

**没做**：线段/矩形/斐波那契/文字等其余工具 —— 那些离开第三方库就是凭空发明交互，本阶段不做也不摆空按钮。

### 2.5 尺寸记忆 / resize 真改容器（修原 bug）

原实现 `onResize → runChart()`：只重新取数重画，`.klm-box` 尺寸由 CSS 定死，`canvas.clientWidth` 根本没变，看起来「缩放没反应」。

改法：

- 尺寸存 `klm_size_v1`（弹窗专属 key，不覆盖 `ko_marks` 或旧版 `kline_lc_settings_v1`）
- `applySize()` 真写 `el.box.style.width` 与 `el.cv.style.height`，canvas 是 `height:100%` → `clientWidth/clientHeight` 随之改变
- 右下角加拖角（`.klm-grip`）：拖动中即时夹尺寸 + 用缓存重画，松手才落 localStorage（不逐帧写盘）
- `onResize`：先 `applySize()` 把尺寸夹回视口，再 `renderCache()` 用缓存重画 —— **不再为一次缩放重新取数**
- 尺寸下限 = CSS 实值（`MIN_W 560` / `MIN_H 260`），避免存下一个用不上的尺寸

### 2.6 快捷键

只留 Esc，且分级：画线中 → 退画线；菜单开着 → 关菜单；都不是 → 关窗。其它键本阶段不加（单测守 `test_esc_only_shortcut`）。

### 2.7 F10 右键刷新

`el.f10.oncontextmenu` 原样保留，未被主图菜单接管。

## 3. 旧版对照

| 旧版（`engine/web/kline_order.js` / `kline_lc.js`） | 本期 v2 | 差异说明 |
|---|---|---|
| `#koMenu` 菜单 + `#koSub` 子菜单，hover 展开 | `.klm-menu` / `.klm-sub` | 结构同构；类名按 v2 前缀重起，不引旧 CSS |
| `marks()` / `saveMarks()` 读写 `ko_marks`，默认全开 | `marks.js::readMarks/saveMarks` | key、字段、默认值完全一致 |
| 分时标记子菜单：买卖点/成本/委托/条件单 + 全部关闭 | 同四项 + 全部关闭 | 多做了「无数据 + 原因」一行，旧版没有（旧版数据源都在） |
| 买入/卖出/条件单/叠加对比/委托列表 | 保留位，全部 disabled + 原因 | 旧版能下单；v2 本阶段不接下单，故禁用并说明 |
| `syncChartLines()` 由 `KLineApp.setExtraLines` 画成本/委托/条件单线 | `lineModels` + 标记层 canvas | 数据形状沿用（`{price, side, qty}` / `{price,label}`），但没有数据源就一条不画 |
| 绘图工具走 KLineChart v9 插件（`kline_lc.js`） | 只做水平线（自绘 canvas） | 不搬第三方库；只搬旧版明文语义「单点落库」 |
| 尺寸由 CSS 定死（`.kbox` 96% / 88vh），无记忆 | `klm_size_v1` + 拖角 + resize 夹取 | v2 新增能力，旧版没有 |
| Esc 关面板 | Esc 分级（画线 → 菜单 → 关窗） | 多一级是因为新增了放置态，不退画线就关窗会误丢操作 |

## 4. 实测结果

### 4.1 测试

```
pytest app/plugins/kline/tests -q                → 36 passed（含既有 25 项，无回归）
pytest .../test_kline_overlay_p1a.py -q          → 11 passed
node app/plugins/kline/tests/js/p1a_marks.test.mjs → 20 项全过
```

### 4.2 一次性浏览器检查（Chromium，本地临时静态服务 + 合成数据，验完已清理）

modal 内实际执行并读回的结果：

- 主图 `contextmenu` → 菜单出现，头部 `贵州茅台 600519.SH · 1600.62`（光标反算价）
- 禁用四项文本含原因（`买入 P1b 接入下单后开放` 等），`is-disabled` 为真
- 分时标记子菜单四项 `✓ 无数据` + 原因；点 `持仓成本线` → `ko_marks = {"bs":true,"cost":false,"order":true,"cond":true}`，打勾原地消失
- 点 `全部关闭` → 四项全 false
- 手工画线 → 点图 → `klm_hlines_v1 = {"600519.SH":[1600.62]}`，放置态提示自动清空
- 拖角：`canvas.clientWidth 520 → 560`、`box.style.width 560px`、`klm_size_v1 = {"w":560,"h":220}` → **原 bug（clientWidth 不变）已消除**
- 关窗重开：尺寸从记忆恢复；标记层 canvas 与主图同尺寸（`560 × 260`）

### 4.3 过程中改掉的两个自身问题（留痕）

1. **`clampSize` 遇视口报 0 时整块返回 null**：拖角写了 `尺寸已记住` 但什么都没变。改为「视口拿不到只夹下限」，`viewport()` 再回落 `documentElement.clientWidth`。已补 node 用例。
2. **`MIN_H = 220` 小于 CSS `min-height: 260`**：存了 220 却只能显示 260（记忆与实际不符）。改成 260。
3. `lineModels` 原按「数组长度」判缺数据，`price<=0` 的脏数据会被当成有数据（菜单显 1 条却不画线）。改为按**有效线条数**判。

## 5. 未完成项 / 边界

| 项 | 状态 | 说明 |
|---|---|---|
| 买卖点 / 持仓成本线 / 委托线 / 条件单线 | **本阶段不取数** | 数据源已存在（paper 的 `/api/order/list`、`/api/trades/{ts_code}`），P2 接入；本阶段仍不取数，接口（`setMarkData`）已留 |
| 买入 / 卖出 / 条件单 / 叠加对比 | 不做（P1b） | 菜单项禁用并写原因，无写入口 |
| 其余绘图工具（线段/矩形/斐波那契/文字…） | 不做 | 依赖 KLineChart v9，无可独立复用件 |
| 键盘快捷键 | 仅 Esc | 其它键按任务书不加 |
| 8810 实时验收 | 待主人 | 本次未启动/重启任何服务；浏览器检查用临时静态服务 + 合成数据 |
| 截图留档 | 无 | 该环境浏览器截图接口返回空图（display surface 不可用），以 DOM 读回值作为行为证据 |
| 12 条 intraday 行号对齐既有失败 | 未触碰 | 与本任务无关，按要求不动 |

## 6. 口径同步点（后来者注意）

日线价格轴只有一份：`marks.js::dailyAxis`。`frontend.js::drawDaily` 直接 import 它（2026-10-27 验收返修后），
弹窗标记层与右键反算走 `scaleFor('daily')` → 同一个函数。**不许在别处再写一遍范围公式**。
分时轴仍由 `GEOM.intraday` 镜像 `drawIntraday`（本轮未授权改 drawIntraday），常量对齐由 node 用例「drawIntraday 的边距变了」看住。

---

# 验收返修（2026-10-27 第二轮，02:3x~03:0x）

> 打回意见（主人/验收）：`frontend.js::drawDaily` 的 min/max 只取 close+yellow+white，而 Y 又用于 open/high/low，
> 最高/最低超出该范围时蜡烛出界；`marks.js::scaleFor('daily')` 只镜像了同一个错轴，右键反算与手工线价位不代表真实图价。

## R1. 根因

不是两处各自写错，而是**同一份错误轴被抄了两份**：绘图里 `min/max` 只看收盘与黄白线，却拿这个 Y 去放 开/高/低；
标记层照抄这段公式，于是「图上的价」和「右键反算的价」天然错得一样多，谁也发现不了谁。

## R2. 改法（最小、不重写）

1. **唯一 helper 里算轴**：`marks.js` 新增 `dailyAxis(bars, w, h)`，取值表为 `open/high/low/close/yellow/white`，
   返回 `{lo, hi, rect, pad, yOf, priceAtY, xAt, inView}`；`scaleFor('daily')` 直接返回它（不再自算一份）。
2. **`frontend.js` 只改 drawDaily 内价格轴这一处**：新增一行 import，把原来的
   `pad / closeVals / ywVals / min / max / X / Y` 六行换成 `dailyAxis(bars, w, h)` 取 `AX`（`pad/lo/hi/xAt/yOf` 全部来自 AX）。
   其它函数、其它文件一行未动；`drawIntraday` 保持原样（未授权）。
3. 两条轴从此是**同一个函数**，不是两份常量对齐。

## R3. 先红后绿（先写复现用例）

新增 7 条 node 用例（`tests/js/p1a_marks.test.mjs` 返修 A~G），用**记录型假 canvas** 把 `drawDaily` 真实落笔的坐标读回来，和 `scaleFor` 对表：

| 用例 | 断言 | 修前 | 修后 |
|---|---|---|---|
| A 蜡烛不出界 | 画笔 y ∈ [pad, h-pad] | **红**：`top=-1028.00 < pad=28` | 绿 |
| B 两轴同源 | `scaleFor.yOf(最高)` = 图上最高落笔 y（±1e-6） | 红 | 绿 |
| C 范围覆盖 OHLC | `dailyAxis.lo/hi` = 当日最低/最高 | 红（只覆盖 close 时 lo=0/区间错） | 绿 |
| D 十字星（open==close） | 实体高 ≥1px、坐标无 NaN | 红（x 为 NaN） | 绿 |
| E 单根 | 横坐标居中、轴可反算 | 红（x 为 NaN） | 绿 |
| F 全平数据 | 无 NaN/Infinity | 红 | 绿 |
| G 同一把尺子 | 轴中点价位落绘图区竖向中点 | 绿 | 绿 |

## R4. 修的过程中连带抓到的两个真 bug（不改就会被 D/E 用例顶住）

1. `_num(null)` = 0（`Number(null) === 0`）⇒ 黄白线为 `null` 的票，0 价被算进轴里，下限被拉到 0、蜡烛挤成一团。
   **这是旧 `scaleFor` 就带的隐患**，只是此前用例都喂了非空黄白线。已改：`null/undefined/''` 一律视为「没有这个值」。
2. 共享轴的 `xAt(i, n)` 需要 `n`，而 `drawDaily` 全程只传 `i`（老实现靠闭包里的 n）⇒ 横坐标算成 NaN，蜡烛一根都画不出来。
   已改：`xAt` 的 `n` 缺省回落该轴的点数。

## R5. 实测（不是只跑测试）

`pytest app/plugins/kline/tests -q` → **36 passed**；`node tests/js/p1a_marks.test.mjs` → **27 项全过**（原 20 + 返修 7），连跑两次稳定；
两份 JS 语法检查通过。

一次性浏览器像素检查（临时静态服务 + 合成数据：40 根、收盘区间 ±4、高低 ±40，验完已停服务并删临时目录）：

- 主图画笔覆盖 `y = 28 … 232`，恰为绘图区（`h=260, pad=28`）——**蜡烛一根都没出界**；验前同一数据的最高价会落到 `y=-1028`
- 在 `y = pad` 处右键 → 菜单头部显示 `1644.00`，等于该组数据的 `max(high)=1643.998` ⇒ 反算价就是真实图价

## R6. 口径纠正（前文更正）

第一版 DONE 里写「v2 无 trades/持仓/委托/条件单只读口」是错的：**paper 已有 `/api/order/list`、`/api/trades/{ts_code}`**。
P2 将接这四类标记；本阶段（P1a）仍不取数，只是接口留好（`setMarkData()`）。以上文为准。

## R7. 白名单遵守

改动仅：`frontend/overlay.js`（本轮未再改）、`frontend/marks.js`（加 `dailyAxis` + 两处修正）、`frontend.js`（**只改 drawDaily 内价格轴 + 一行 import**）、
`tests/` 两份测试、本 DONE 文档、同日修改日志。无第二个 helper 文件、无 `.bak`、无新依赖、未启动或重启任何常驻服务。
