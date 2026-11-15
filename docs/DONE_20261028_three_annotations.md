# 2026-10-28 三条标注：标注入口 / 全局个股 K 线 / 浮层叠加

> 来源：主人在 8810 页面留的三条标注（`N20261028-100025-36`、`N20261028-100413-37`、`N20261028-100527-38`）。
> 范围：只做这三条，不动数据契约、后端与旧 8800。

## 1. 标注与结果

| 标注 | 原话 | 改法 |
|---|---|---|
| `…-36` | 标按按钮还是应该改成悬浮式可拖动的，否则想标记K线图的时候，因为遮罩的存在无法使用标记按钮 | 标注入口从 rail 按钮改为**悬浮可拖动按钮**（位置存 `localStorage: v2_note_fab_pos_v1`）；标注层 z 抬到 110/120/130（旧 K 线弹窗 z=100），按钮、高亮框、输入框都在弹窗之上 |
| `…-37` | 点击个股无法跳到K线，需要全局都有点击个股打开K线的逻辑。并且在K线图可以通过上下键翻看其他票而无需退出 | 作战表/纸面账本的个股单元格补 `data-open-kline` + `data-open-name`；壳的委托排除按钮/输入等交互控件；打开时收集同列表上下文（DOM 顺序、去重、排除 `.TI`），经 `openKlineStandalone({list})` 传给旧 `KLineApp.open(code, name, list)`，↑↓ 翻票恢复 |
| `…-38` | 点击打开板块详情后，在板块详情内点开个股K线图，板块详情卡片会消失。需要有卡片叠加逻辑，每次只关闭最上层的卡片 | 新增 `window.__v2Overlay` 浮层栈（push/pop/top）；独立打开 K 线时**不再** `closeSectorModal`；K 线与板块详情各自只在 `top()===自己` 时处理 Esc；点各自遮罩空白只关自己 |

## 2. 改动文件

| 文件 | 改动 |
|---|---|
| `app/web/shell.js` | 浮层栈 `overlayStack()`；K 线分支不再收板块详情；`klineListOf()` 列表上下文；`ctxOpenKline(code,name,list)` / `openKlineOverlay(...,list)`；委托排除交互控件；`panel:open-kline` 支持 `detail.list` |
| `app/web/v2_note.js` | 入口改悬浮按钮（`un-fab` 常驻，嵌入旧页不建）；pointer 拖动 + 位置持久化；拖动与点击区分（250ms 吞掉拖动收尾的 click） |
| `app/web/shell.css` | `--z-note:110` / `--z-note-pop:120` / `--z-note-fab:130`；`.un-fab` 可拖动样式 |
| `app/web/shell.html` | 删除 `#rail-note`（保留 `#rail-notes` 标注清单） |
| `app/plugins/kline/frontend/legacy_modal.js` | `open` 第三参传列表；`MutationObserver` 同步浮层栈；Esc 只在栈顶时关闭；版本 0.7.3 |
| `app/plugins/sector/frontend/detail.js` | 详情 open/close 时 push/pop `sector`；Esc 判栈顶 |
| `app/plugins/battle_plan/frontend.js` | 预案表/操作表/观察位/择机单/持仓的个股单元格补 `data-open-kline`；版本 0.3.1 |
| `app/plugins/paper/frontend.js` | 持仓/委托/条件单表格个股单元格补属性；版本 0.2.1 |
| 测试 | 新增 `app/tests/test_v2_overlay_stack.py`；更新 `test_v2_note.py`、`test_v2_notes_panel.py`、`test_fe_shell_bridge.py`、kline legacy 测试、battle_plan/paper 版本断言 |

## 3. 验证

实机（headless Chrome，8810 现网进程，`_vis/verify_20261028/` 截图）：**20/20**

- 页面启动、悬浮按钮存在、rail 按钮已移除、z-index 130 > 100；
- 拖动改变位置并持久化，拖动不触发模式切换；单击进入标注模式，ESC 退出；
- 板块详情打开 → 点个股 → K 线弹窗打开、**详情仍在**、标题 `1/N`；
- `ArrowDown` 切到下一只（`2/N`）；作战表点个股能开 K 线并带列表；
- 标注模式可选中 K 线弹窗内元素（`.un-box` 弹出，z 120 > 100）；
- 第一次 ESC 只关 K 线、详情仍在；第二次 ESC 关详情；
- console 异常 0（favicon 404 不算）。

pytest（壳层 + kline/sector/battle_plan/paper/watchlist/ops）：**621 passed, 2 failed**

两处红灯均为既有、与本次改动无关：

1. `test_undefined_tokens_not_used`：`sector/frontend/board.js` 写 `var(--mono, var(--font-mono))`，有 fallback，测试正则误报（该文件是周末迁移的未跟踪文件）。
2. `test_panic_store_mode_shape`：盘中实时源可用时 `/api/panic` 返回 `source=eltdx`，测试期望 `store`（环境相关）。

## 4. 补充（同日，主人反馈）：板块详情里打开的个股丢失 F10 卡片

**根因**：板块详情表格的 `stock_code` 是 6 位（`300432`），旧 F10 面板只认带后缀的 ts_code
（`kline_f10.js::isStockCode` = `/^\d{6}\.(SH|SZ|BJ)$/`），判为非个股 → 面板收起；
K 线接口本身兼容 6 位，所以图能画、F10 不弹。

**改法**：壳在唯一入口 `normalizeStockCode()` 补后缀（6/5→.SH、4/8→.BJ、92→.BJ、9→.SH、其余 .SZ），
点击打开与列表上下文用同一份代码；已带后缀的原样返回。

**验证**：板块详情点「富临精工」→ 标题 `300432.SZ`、F10 面板显示（行业/市值/PE）；
`ArrowDown` 到「模塑科技」→ `000700.SZ`、F10 内容跟着换。

### 补充 2（同日，主人反馈）：上下键翻页的股票只显示代码

**根因**：列表上下文收集时按 DOM 顺序扫 `[data-open-kline], [data-code]`；自选卡的行容器 `.wl-wc[data-code]`
在名称节点之前命中，`seen` 去重把后面的名称节点跳过 → 列表项 name 为空，标题只剩代码。

**改法**：① 重复 code 时用后面的 `data-open-name` 补名称；② 建全页 code→name 字典兜底；
③ 节点自身文本≤ 8 字且不含数字/空白时当名称（只给代码列的表格）。

**验证**：自选卡 17 只、作战表 11 只、板块详情 13 只连续 `ArrowDown`，标题均带名称。

## 补充 3（同日，主人要求）：左侧列表卡 + F10 卡可收起/展开

**列表卡**（`#kListPanel`，168px）：当前列表上下文的全部标的，标题「列表 6/277」；
当前项高亮，点击切票，↑↓ 切票时高亮与滚动跟随；无列表上下文时开关置灰。

**F10 卡**：`btnF10Toggle` 收起后宽度 0，K 线图宽度 900 → 1240；列表开 + F10 开时 900，
列表开 + F10 关时 1240（两个卡都让出的宽度全给图）。

**状态记忆**：`v2.kline.listOpen` / `v2.kline.f10Open` 存 localStorage，刷新后保持。

**实现边界**：不改旧四模块——列表数据来自 `openKline` 的 list 参数（与旧 `open(code,name,list)` 同一份），
切票高亮靠观察 `#klineTitle`，F10 显隐仍由 `kline_f10.js` 控 display，本层只加宽度 class；
新增节点包在 `<!-- v2:extra -->` 里，旧块逐字对照测试仍保留。版本 0.8.0。

**验证**：实机 9 项——列表 277 项/当前高亮、点第 5 项→5/277、`ArrowDown`→6/277 高亮跟随、
两开关切换与宽度变化、刷新后状态保持；头部无溢出。截图 `_vis/verify_20261028/panels_list.png`、
`panels_f10off.png`。

## 补充 4（同日，主人提议）：相邻票预取（0.8.1）

列表 ≥ 3 只时，切票后空闲 250ms 预取当前项**后 2 只、前 1 只**的日线与除权
（`/api/kline/{code}?period=d&limit=240&mf_lookback=20` + `/xdxr`），把服务端 60s 缓存热起来；
同一票 45s 内不重复预取。

**只预取日线与除权**：F10/盘口/筹码各自有缓存且更重，预取会跟当前票抢 eltdx 连接。

**实测**：切预取过的票 60–143ms；同一轮切未预取的冷票 493ms。列表 <3 只、无列表上下文、
弹窗已关时自动跳过。

## 5. 边界

- 未重启 8810：静态资源同盘、`Cache-Control: no-cache` 协商校验，浏览器刷新即生效；`/_plugins` 里的 manifest 版本串仍是旧值（kline 0.7.2 / sector 0.6.4 / paper 0.2.0 / battle_plan 0.3.0），重启后才会显示新版本，功能不受影响。
- 嵌入旧页（iframe）形态：K 线仍转父页 `KLineApp`，并保留收掉 v2 板块详情的旧行为（父页弹窗在 iframe 外，遮罩会盖住）；本次只改独立打开路径。
- 右键菜单开 K 线仍不传列表（右键目标可能是行/图表，列表语义不稳定）；左键点击路径全部带列表。
