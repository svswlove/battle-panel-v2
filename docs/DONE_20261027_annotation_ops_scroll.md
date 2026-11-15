# 后台卡去掉内部滑块（标注 N20261027-012343-28）

> 主人标注：后台把内部单独的滑块去掉。滚动唯一归属方 = V2 外壳 stage，卡片不自带滚动容器。

## 1. 改动面（白名单内，无越界）

| 文件 | 性质 |
|---|---|
| `app/plugins/ops/frontend.js` | CSS 常量：`.ops-wrap` 去 `overflow:auto` / `max-height:420px`；`th` 去 `position:sticky`；`.ops-table` 加 `table-layout:fixed`，单元格加 `overflow-wrap:anywhere` |
| `app/plugins/ops/tests/test_ops_frontend.py` | 新增 6 项静态断言（滚动归属段）+ 1 项 `node --check` 语法解析守卫 |
| `app/plugins/ops/tests/js/ops_scroll.cdp.mjs` | **新增**，真实浏览器自测 25 项 × 3 视口 = 75 项 |
| `app/plugins/ops/plugin.json` | `version` 0.3.0 → **0.3.1** |
| `app/plugins/ops/tests/test_ops_plugin.py` | 仅 manifest 版本断言 0.3.0 → 0.3.1（授权内同步） |
| `docs/DONE_20261027_annotation_ops_scroll.md` | 本文件 |
| `OH-Works/文件修改日志/2026-10-27.md` | 同日 append |

**未动**：`app/core/`、`app/web/`（含 shell.css / shell.js）、其它插件、`engine/web/`。未新增依赖。

## 2. 做法

### 2.1 现场问题

改前：

```css
.ops-wrap{overflow:auto;max-height:420px;...}   /* ← 卡片内部纵向滑块 */
.ops-table th{position:sticky;top:0;...}        /* ← 依赖内部滚动的 sticky 表头 */
```

三张表各套一个 `.ops-wrap`，420px 以上被截断，表内自滚；外壳 `.stage{overflow:auto}` 反而滚不动。同一屏两个滑块，主人的标注指的正是卡片里那个。

### 2.2 改法

滚动全部交还 `.stage`，卡片只负责把表格排完：

```css
.ops-wrap{border:1px solid var(--line);border-radius:var(--radius-sm)}
.ops-table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:0.75rem}
.ops-table th,.ops-table td{padding:4px 8px;...;overflow-wrap:anywhere;word-break:break-word}
.ops-table th{background:var(--card);color:var(--dim);font-weight:500}   /* 无 sticky */
.ops-table th:first-child{width:74px}
.ops-table th:nth-child(2){width:30%}
.ops-mono{...;white-space:normal}   /* 原 nowrap 在固定布局下会溢出到相邻列 */
```

四个决策点：

1. **不用 `display:block;overflow-x:auto` 兜小屏**——那只是把纵向滑块换成横向滑块，仍是「内部单独的滑块」。改为固定布局 + 任意断行，在桌面宽度内收住。
2. **`table-layout:fixed` 必须配列宽**，否则 6 列均分，任务名挤成一坨。三张表第 1 列都是状态胶囊、第 2 列都是主体文字，按列位定宽度即可，不给每张表加传参。
3. **`.ops-mono` 的 `nowrap` 一并放开**（改 `normal`）：固定布局下时间戳/PID 不换行会溢到邻列，`tabular-nums` 保留，数字仍对齐。
4. **小屏仍靠隐藏第 4 列**（`@media(max-width:760px)`，既有规则，未改），没伪造响应式。

### 2.3 过程中修掉的一处自伤（记录）

首次改完，静态断言 27 项全过，浏览器里卡片直接报 `SyntaxError: Unexpected token '{'`——CSS 就写在模板字符串里，我在注释里写了带反引号的 `` `.stage{overflow:auto}` ``，把模板提前断开了。

正则式静态断言看不见这类错。补 `test_frontend_js_parses()`（`node --check`）后由它兜住；`node --check` 本机无 node 时 skip。

## 3. 验证

### 3.1 静态断言：`pytest app/plugins/ops/tests/test_ops_frontend.py -q` → **28 passed**

新增 7 项：

- `test_ops_wrap_is_not_a_scroll_container`：`.ops-wrap` 无 `overflow:auto/scroll`、无 `max-height`、不限高；
- `test_table_has_no_inner_horizontal_scroller`：全文件无 `overflow-x:auto/scroll`、`.ops-table` 无 `display:block`（明确否掉替代写法）；
- `test_table_fixed_layout_with_anywhere_wrap`：`.ops-table` 有 `table-layout:fixed`，单元格有 `overflow-wrap:anywhere`；
- `test_header_not_sticky`：文件内无 `position:sticky`，`.ops-table th` 无 sticky；
- `test_narrow_screen_still_hides_column`：760px 分支与 `nth-child(4)` 仍在；
- `test_frontend_js_parses`：`node --check` 真解析；
- （原有 21 项全过，无一条因本次改动放松。）

**TDD 次序**：先写断言 → 3 项红（`.ops-wrap` 溢出 / 固定布局 / sticky）→ 改实现 → 28 绿。

### 3.2 真实浏览器：`node app/plugins/ops/tests/js/ops_scroll.cdp.mjs` → **PASS 75/75**

1920×1080 / 960×900 / 720×900 三视口，走真实入口（载 `/v2/` → 点侧栏「后台」rail 按钮），非直接注入模块。每视口 25 项：

- 三张表 `.ops-wrap`：`clientHeight === scrollHeight`、`clientWidth === scrollWidth`、`overflow-y/x = visible`、`max-height = none`（15 项）；
- `.ops-card` 自身 `clientHeight === scrollHeight`；卡片内**不存在任何溢出滑块元素**（遍历 `.ops-card *`，按「声明了 auto/scroll 且确实溢出」判）；
- 滚动归属于 `.stage`：`overflow-y = auto` 且 `scrollHeight > clientHeight`（长表确实由外壳滚）；
- 表头 `position !== sticky`；`.ops-table` `table-layout = fixed`；
- 第 4 列隐藏情况与宽度匹配：≤760px 隐 1 列、1920/960 全显。

截图：`_vis/ops_scroll/ops_card_1920.png`、`ops_card_960.png`、`ops_card_720.png`。人工查看确认：1920 下表头横线贯穿整行、22 行任务完整展开无截断；720 下第 4 列（结果码）隐藏、文本按宽度断行、无横向被裁。

> 脚本首版用 `?card=ops.main` 直达，实测落到仪表盘——该参数只过滤卡片清单，初始卡仍取导航表第一张。已改为点侧栏真实入口。

### 3.3 回归

| 范围 | 结果 |
|---|---|
| `pytest app/plugins/ops/tests -q` | **125 passed, 1 failed** |
| `pytest app/tests/test_fe_shell_bridge.py -q` | **28 passed** |

那 1 项失败 = `test_ops_m1_endpoints.py::test_panic_store_mode_shape`（`source` 期望 `store`、实得 `eltdx:880005+880006+880009`），**本轮开始前就红**（首跑基线即 1 failed），属 panic 数据源状态，与本次 CSS 改动无关，按边界不修只报。

### 3.4 生效确认（重启一次）

8810 是常驻进程，manifest 在 import 期读取，改 `plugin.json` 后不重启不会生效。已 `Stop-Process` 旧 pid 51788 → `tools\ensure_8810.ps1 -Once` 拉起（健康检查通过）。复核：

- `GET /_plugins` → `ops.version = "0.3.1"`（改前 0.3.0）；
- `GET /api/health` → `ok:true, version:0.3.1`，18 插件全在；
- 重启后**重跑**浏览器自测仍 **75/75**，即 `?v=0.3.1` 新缓存键取到的是新 CSS。

## 4. 遗留

- `test_panic_store_mode_shape` 仍红（本轮前既有，非本次引入）；要修另开工单。
- 未验证：真机触屏 / 移动端宽度；`stage` 内滚动条样式（`--hide-scrollbars` 下截图看不到，未做视觉比对）。
