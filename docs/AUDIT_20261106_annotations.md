# 2026-11-06 作战面板标注功能体检（01）

> 范围：8810 面板（`projects/panel-v2`）的页面标注模块 = `app/web/v2_note.js`（页内拾取/撰写/圆点）
> + `app/web/v2_notes_panel.js`（标注清单）+ `app/plugins/ops/backend.py::ui_notes/*`（存储与阶段）。
> 方法：读码 + 对运行中的 8810（pid 84272）实机取数。未改任何代码、未动 `engine/state/ui_notes.json`。

## 0. 现状数字（`GET /api/ui_notes/list`）

| 项 | 值 |
|---|---|
| 总条数 | 87 |
| 待处理 open | 5（全部为 2026-11-06 上午 10:46–10:58 留） |
| 处理中 doing / 已修复 fixed | 0 / 0 |
| 已验证 verified | 82 |
| 驳回 rejected | 0 |

5 条待处理分布：选股卡 1 条（`N20261106-104611-82`：设置不能长久保存）、盘中卡 4 条
（`-83`/`-84`/`-85`/`-86`，均为「说明文字去掉」）。

## 1. 问题清单（按影响排序）

### P1 换卡后标注点不同步（串卡）

- 现象（实测）：`/v2/` 进页 → 挂仪表盘卡，pin=0；点侧栏「选股」→ activeCard 已变
  `selection::selection.main`，pin 仍为 0（该卡有 1 条 open 标注）；手动调
  `__v2Note.refresh()` 后才出现。
- 反向：从选股切到盘中，pin 仍是选股那条（`N20261106-104611-82`，坐标 59,167）；切回仪表盘，
  pin 仍是盘中那条（`N20261106-105649-84`，坐标 85.8,816）。点它编辑的是别的卡的标注。
- 根因：`v2_note.js::refreshNotes()` 只在 `init()` 与 `setMode(true)` 调用；
  `shell.js::selectCard()` 切卡后没有任何地方通知标注层。`pageKey()` 虽已指向新卡，
  `st.notes` 仍是旧的，`renderPins()` 只重算位置、不重拉列表。
- 改法：`selectCard` 成功后调 `state.note.refresh()`（一处调用，不动其它）。

### P2 锚点失效后标注从页面上消失

- 现象（实测，盘中卡 4 条 open）：

| 标注 | selector | 能否查到 |
|---|---|---|
| `-83` | `div.card-host:nth-child(2) > div.it-card:nth-child(1) > … > span.it-sec-n:nth-child(2)` | ✗ |
| `-84` | `div.to-cards:nth-child(1) > div.it-to-card… > div.it-to-h:nth-child(1)` | ✓ |
| `-85` | `div.src:nth-child(1)` | ✗ |
| `-86` | `div.it-card:nth-child(1) > … > div.evo-info:nth-child(3)` | ✗ |

  4 条里 3 条查不到 → 页面上只剩 1 个圆点，另外 3 条只存在于清单里。
- 根因：`path()` 用「第 n 个孩子」全链，最多 6 层、遇 id 才停。`-83` 链首是
  `card-host:nth-child(2)`（当时 stage 下还有别的子元素），DOM 顺序一变整条断；`-85` 的
  `div.src:nth-child(1)` 属于短链，即使能匹配也可能命中页面上另一个 `div.src`（误锚）。
- 改法（两步）：① 生成 selector 时优先稳定特征（`#id`、`data-*`、独有 class），减少 nth-child 依赖；
  ② 加兜底：查不到时不要静默跳过，按存的 `x/y` + `elem` 画一个「定位失效」提示点，让主人知道
  这条还在、并能从清单处理掉。

### P3 圆点上的文字溢出

- 现象（实测）：`.un-pin` 18×18px、`font-size:10px`、`overflow:visible`，`textContent` 写的是完整
  id `N20261106-105649-84`，实测 `scrollWidth=55` → 字画到圆点外面，成一条飘着的编号。
- 改法：圆点内只写序号（1/2/3…）或不写字，完整 id 放 `title`。

### P4 元素滚出视口后圆点粘在屏幕边缘

- 现象（实测）：把卡片滚 200px，pin 的 `getBoundingClientRect().top` 从 816 → 0，即被
  `Math.max(2, r.top-9)` 夹到顶部，仍可见。
- 根因：`renderPins()` 只在 `width && height` 都为 0 时才跳过，元素移出视口（负坐标）没判。
- 改法：`r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth` 时不画。

### P5 圆点层级压过抽屉

- 数据：`--z-note:110`（圆点/高亮）、`--z-drawer:31`（设置抽屉、标注清单抽屉）。
- 后果：拉开左侧 320px 设置抽屉时，落在该区域的标注圆点（如盘中卡 x=86）会盖在抽屉内容上。
- 改法：打开抽屉时隐藏 pin（或把 `.un-pin` 的层级压到 `--z-drawer` 之下）。

### P6 标注删不掉

- 后端只有 add / update / resolve / stage 四个写口，前端清单只有阶段流转 + 定位，没有删除、
  没有归档。87 条里 82 条「已验证」永久占据列表（一次全量渲染，无分页）。
- 改法：清单加「删除」（或对 verified 加归档开关），后端补 `POST /ui_notes/delete`（带 expected
  校验），顺手给列表加按状态分页/懒渲染。

## 2. 次要（记录，不列进本轮）

| 项 | 说明 |
|---|---|
| K 线 canvas 拾取开销 | `canvasInkRatio()` 对整张 canvas `getImageData` 全量扫（1920×800 ≈ 150 万像素），800ms 缓存；快速移动鼠标时可能一顿 |
| 弹窗内标注层级 | K 线弹窗 z=100 < 标注框 120，可标；但划线工具的 `.dd-menu` z=10001 会盖住标注框 |
| 触屏/移动端 | 未验证 |
| same-element 重复标注 | 同一元素可重复标多条，无去重提示 |

## 3. 复现脚本（本轮临时，收尾即删）

`Hubagent/01/_tmp/note_audit_20261106/r1_open.mjs` … `r5_anchor.mjs`（ego-browser 驱动 8810）。

## 4. 边界

- 未改代码、未重启 8810、未动标注数据（`engine/state/ui_notes.json` 87 条前后一致）。
- 本轮只出清单，改哪几条听主人定。
