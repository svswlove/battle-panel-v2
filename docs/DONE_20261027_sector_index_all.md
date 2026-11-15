# 板块卡指数层全量 12 + 右栏走势与数据；区间提示删除（2026-10-27）

> 标注：`N20261027-011947-26`（`.panels.idx`）、`N20261027-012116-27`（`.rr-span`）
> 负责人：01｜授权：write，白名单 = `app/plugins/sector/**` + 本 DONE + 同日修改日志
> 回退入口：HANA 文件历史（`OH-Works/文件恢复指引.md`），未留 `.bak`

## 1. 改动清单

| 文件 | 性质 | 内容 |
|---|---|---|
| `app/plugins/sector/frontend/compute.js` | 修改（+34 行） | 新增 `computeAllRows(dayIndex)`：指数层**全量行**，不切 10%，全A 固定首位 |
| `app/plugins/sector/frontend/board.js` | 修改 | `buildPanel` 接受 `rowsAll` / `selCode` / `onSelect`；指数层不再算「在榜」 |
| `app/plugins/sector/frontend/detail.js` | 修改 | 抽 `fetchDetail()` 供弹卡与内联共用；新增 `mountDetail(host, o)`；全A 不再发必失败的 `/api/kline/ALL` |
| `app/plugins/sector/frontend.js` | 修改 | 新增 `_renderIdx()`（左全列 + 右内联）；`idxSel` 状态；删 `.rr-span` 元素与写入 |
| `app/plugins/sector/frontend/legacy.css.js` | 修改 | 补 `.panels.idx` 整套（照 8002 style.css:842-868 / 980-984）+ `.idx-inline` 静态化；删 `.rr-span` 规则 |
| `app/plugins/sector/plugin.json` | 修改 | 版本 0.6.2 → 0.6.3（壳用 `?v=<version>` 做前端缓存键） |
| `app/plugins/sector/tests/js/sector_index.test.mjs` | 新建 | jsdom + 源码契约 14 项 |
| `app/plugins/sector/tests/js/sector_index.cdp.mjs` | 新建 | 真实 Chrome CDP，直连 8810，只读 GET，27 项 |
| `docs/DONE_20261027_sector_index_all.md` | 新建 | 本文 |

### 生效状态（改完立刻核过）

- 前端文件**已生效**：`GET /plugins/sector/frontend.js` 已含 `_renderIdx`、已无 `rr-span`，
  ETag 随内容变（`de06…`）；服务端每次从磁盘读，不缓存。
- `/_plugins` 仍报 `sector.version=0.6.2`：manifest 在进程 import 期读，**要下次重启 8810 才更新**。
  版本号只做前端缓存键（`?v=<version>`），不影响功能；本次未重启常驻服务。

未动：`backend.py`（零改动，全用现有只读端点）、`app/core/`、`app/web/`、其它插件、`engine/`、旧 8002。

## 2. 标注 26：12 个指数只显示 6 个 + 右侧没有走势与详情

### 2.1 根因（不是标题写错数字）

`computeBoardLists` 按 `topCount(n)=clamp(round(n*10%),10,30)` 切前/后 10%：
指数层 12 个 → `topCount(12)=6` → `topIn` 6 条、`topOut` 6 条被丢弃，
而标题把 `topIn.length + exitsIn.length`（=6）当总数写成「全部」。左栏只有 6 行，
右栏是空 panel（`.panel-out` 从没渲染内容），所以既看不到 12 个，也看不到走势。

### 2.2 做法（照 8002，不发明）

旧版 8002 的指数模式是两条现成路径：`idxPoolRows()`（全量、全A 固定首位）+ `setIdxPanels(true)`
（`.panels` 加 `.idx`，隐藏 `#listOut` 的列头）+ `renderIdxDetail()`（把详情**内联**画进
`#listOut > li.idx-detail`，`bindCard` 同一套绑定）。v2 逐条对位：

1. `computeAllRows` = `idxPoolRows` 的 v2 版（同一 `poolAt`/`scoreMapOf`/`rankMapBy`，只是不切片）。
2. `buildPanel(rowsAll)` 直接铺全量行；`selCode` 给当前选中行加 `.sel`。
3. 点行 = `onSelect(code)` → 换右栏（不弹卡），与 8002 `renderList` 里 `isIdxMode()` 分支一致。
4. 右栏用 `mountDetail()` 复用同一个 `paintCard` / `bindCard`：宿主 `<li class="idx-detail">`
   里放 `.sector-modal.idx-inline > .modal`，CSS 把浮层还原成静态块，`.sector-modal .card-*`
   整批规则原样生效（禁第二套卡内样式）。
5. 数据全走现有只读端点：`/series`（活跃市值 + MA10 走势图）、`/members/{code}?full=1`
   （成分股表）、`/contrib`（贡献分项）、`/api/kline/{code}`（收盘 + 黄白线）。
   **零后端改动**。

### 2.3 与旧版布局对齐（截图核对）

- `.panels.idx` = `430px | 1fr`；左栏只留 序号 / 指数 / 真实涨跌 / 主力净流入（隐藏评分、权重、
  权重变化、活跃涨跌、在榜），与 `style.css:856-868`、`980-984` 逐条一致。
- 内联详情 `.card-body` = `288px | 1fr`、`.card-right{max-height:520px}`（`style.css:852-853`）。
- 全A 没有板块指数日线（兼容层要求 `6位代码.SH/SZ/BJ`，`ALL` 必 400）→ 右栏显示
  「无收盘价数据」，其余 11 个指数两张图都有。这是旧版同口径（旧版也发同一请求、也空）。
  本轮顺手短路掉这个必失败请求（`fetchDetail` 里 `code === 'ALL'` 直接返回错误占位）。

## 3. 标注 27：`.rr-span`（区间 N 交易日）

按钮已表明状态（`1天/3天/5天/10天/20天` + 起止日期），该标签重复且占位。
删除三处：`frontend.js` 的 `this.spanEl` 元素创建与 `_syncSliders()` 里的文本写入、
`legacy.css.js` 的 `.rr-span` 规则。**范围提示保留**：`dateA → dateB` 与 `区间微调（双滑块）`
都在，`_syncSliders` 其余逻辑（双滑块 fill/grab、按钮 disabled）一字未动。

## 4. 验证
### 4.1 TDD（先红后绿）

`tests/js/sector_index.test.mjs` 先写后改：改前红在
`compute 必须导出 computeAllRows`（函数不存在）；实现后 14 项全绿。

```
node app/plugins/sector/tests/js/sector_index.test.mjs   → all assertions passed（exit 0）
pytest app/plugins/sector/tests -q                        → 70 passed（基线 70，无回归）
```

### 4.2 真实浏览器（Chrome CDP，直连 8810，只读）

```
node app/plugins/sector/tests/js/sector_index.cdp.mjs    → 27/27 项通过
```

走真实入口（`/` → 点导航「板块」→ 点「指数」），断言：

- 左栏 12 行、12 个不同代码、标题「各大指数 · 全部（12）」、恰好 1 行 `.sel`、默认全A
- 右栏 `.idx-detail` 内有详情：走势图真有笔迹、成分股表有行、4 个数据格
- 两栏 `430px + 1fr`
- 点第 2 行（`000300.SH`）→ 选中跟着换、右栏标题变「沪深300」、不叠行（仍 12 行）、
  两张走势图都在、不弹卡（`#sector-modal` = 0）
- 切回「二级」→ 仍是前/后 10% 两栏（其他层级零影响）
- 无失败请求、无 JS 异常
- `.rr-span` = 0

截图：`app/plugins/sector/tests/js/_shots/sector-idx-all.png`（全A）、
`sector-idx-second.png`（沪深300）。人工查看：左栏 12 行全列、右栏上图下表、与旧版左右布局一致。

### 4.3 过程中自己抓到并修掉的两个问题

1. `_renderIdx` 直调（点行换选中不经 `_render`）没清 `this.body` → 每次点击叠一份 12 行
   （实测 24 行）。改为函数开头 `body.textContent=''` + 先 `destroy()` 旧详情实例；
   CDP 加「换选中后不叠行（仍 12 行）」守门。
2. 指数层每行都算「在榜」（30 日 × `boardSets`），而该列在 `.panels.idx` 里被 CSS 隐藏 →
   点行换选中整表重画时白花时间。`idxMode` 时跳过（`hitDays: []`），行文本不变（列本就不可见）。

## 5. 未完成项 / 边界

| 项 | 状态 | 说明 |
|---|---|---|
| 全A 的板块指数日线 | 仍缺 | 兼容层只认 `6位代码.SH/SZ/BJ`；旧版同缺。要补需动 `compat_kline`，超出本任务边界 |
| 网格 / 四象视图的指数层 | 已全量 | `idxRows` 改走 `computeAllRows`（12 格 / 12 气泡，CDP 实测） |
| 右栏「↻ 重算」 | 原地重挂 | 弹卡版仍是关掉再开；内联版没有关闭语义，故走 `onRemount` |
| 手机 / 触屏 | 未验证 | 只做 `@media (max-width:1100px)` 单栏兜底（照旧版断点） |
| 其它层级 10% 口径 | 一字未改 | CDP 断言「二级层仍是前/后 10% 两栏」 |

## 6. 口径同步点（后来者注意）

指数层有两个入口，别再各自写一份：

- 表格：`frontend.js::_renderIdx()`（左 `buildPanel(rowsAll)` + 右 `mountDetail`）
- 网格/四象：`_render()` 里 `idxRows = C.computeAllRows(dayB).rows`

「全量」的唯一实现是 `compute.js::computeAllRows`；「10%」的唯一实现是 `computeBoardLists`。
详情卡的唯一渲染是 `detail.js::paintCard`，弹卡与内联都只调它。
