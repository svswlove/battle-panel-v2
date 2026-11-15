# 2026-10-30 标注定位：分层画布跳到「有内容的那一层」

> 来源：主人反馈「标注功能定位还是不准，对于卡片类的东西会定位到下面的一层，想办法优化」。
> 前一条核查结论：N20261030-005524-56 / -57 两条「透明度调到70%」锚在 `canvas:nth-child(2)`，
> 实测非透明像素 0%，是 lightweight-charts 的十字光标覆盖层（z=2），真正画柱/线的是 z=1。

## 1. 根因

`v2_note.js::pickAt` 取 `elementsFromPoint` 最内层候选。K 线每个窗叠两张同尺寸 canvas：

| 层 | z-index | 画什么 | 实测非透明像素 |
|---|---|---|---|
| `canvas:nth-child(1)` | 1 | 网格、K线、成交额柱、指标线 | 12% ~ 39% |
| `canvas:nth-child(2)` | 2 | 十字光标、价格标签 | 静止 0%，鼠标进图 0.2% ~ 0.7% |

最内层永远是第二张 → 标注锚在看不见的覆盖层上；照它去调透明度，指标线不会变。

## 2. 判据：为什么不看「有没有像素」

第一版用绝对值阈值（非透明像素 > 0.2% 算有内容），实测失败：鼠标一移到图上，覆盖层立刻画出
十字线+标签，占比 0.7%，被判成「有内容」——锚点还是覆盖层。

改成**同容器兄弟层相对量**：

- 只在同父容器存在 ≥2 张 canvas 时才判（单画布图表零影响）；
- 本张内容 < 最丰那张的 1/4 → 判为覆盖层，`pickAt` 跳过它继续往下沉；
- 量不到（无 2d 上下文 / tainted / 尺寸 0）→ 不判，按原样返回；
- 全都不满足（比如整窗没数据的空图）→ 返回最初的命中元素，保持旧行为，不让人点不到东西。

## 3. 改动

| 文件 | 改动 |
|---|---|
| `app/web/v2_note.js` | 新增 `canvasInkRatio()`（800ms 缓存，`force` 强制重算）+ `canvasOverlay()`；`pickAt(x,y,force)` 增加覆盖层跳过；click 传 `force=true`，mousemove 预览走缓存 |
| `app/tests/test_v2_note.py` | 新增 `test_canvas_overlay_layer_is_skipped`（锁住相对阈值、跳过、fallback、点击强算） |

## 4. 验证

实机（headless Chrome，8810 现网进程，`/v2/` 壳 + 概念卡激活 + K 线弹窗）：

| 点位 | 锚到的元素 | z-index | 非透明像素 |
|---|---|---|---|
| 副图1（成交额） | `... tr(3) > td(2) > div(1) > canvas:nth-child(1)` | 1 | 22.6% |
| 副图2（主力资金） | `... tr(5) > td(2) > div(1) > canvas:nth-child(1)` | 1 | 12.3% |
| 主图 | `... tr(1) > td(2) > div(1) > canvas:nth-child(1)` | 1 | 39.2% |
| 概念卡普通 DOM（页签按钮） | `button.cp-pill.active` | — | 不受影响 |

pytest `test_v2_note.py + test_v2_notes_panel.py + test_fe_shell_bridge.py`：**63 passed, 1 failed**。
唯一红灯是既有 `test_fe_shell_bridge::test_undefined_tokens_not_used`（`sector/frontend/board.js`
写 `var(--mono, var(--font-mono))`，测试正则误报），与本次改动无关（2026-10-28 已记录）。

静态资源已在线验证：`GET /static/v2_note.js` 返回内容含 `canvasOverlay`，浏览器刷新即生效，无需重启 8810。

## 5. 边界

- **旧标注未重锚**：已存的 56/57 两条 selector 仍指向 `canvas:nth-child(2)`（空覆盖层）。改代码不改数据，
  要迁移需另改 `engine/state/ui_notes.json`（生产 state，等主人点头）。
- 只处理 canvas。SVG 分层（部分图表用透明 `<rect>` 覆盖层）未纳入 —— 现阶段只有 K 线用 canvas 叠层；
  主板/板块/自选卡里的都是普通 DOM，行为不变。
- 覆盖层判据按「内容量」而非「图层语义」：若某张图故意把主要绘制放上层、底色放底层，会被判反。
  目前仓内没有这种画法；出现时把 `OVERLAY_RATIO` 调小或改成按 z-index 判。
- mousemove 预览用 800ms 缓存，图表快速重绘时高亮框可能滞后一档；点击一律强制重算，落盘的 selector 不受影响。
