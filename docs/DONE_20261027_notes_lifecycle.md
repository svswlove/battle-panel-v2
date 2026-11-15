# DONE · 标注清单 + 全生命周期追踪（2026-10-27）

> 后端契约与测试由 01 先落（ops 0.3.2，见 `HANDOFF_20261027_notes_lifecycle.md`）；本轮只做前端。
> 交付：`app/web/v2_notes_panel.js`（新）+ 壳三件套接线 + 三条测试 + 本文档。

## 1. 做了什么

| 块 | 内容 | 文件 |
|---|---|---|
| 面板本体 | 右侧抽屉 440px（max 92vw），复用 `#scrim`；头部统计、七枚筛选 chips、搜索框、按 page 分组列表、折叠/展开、四条动作、定位 | `app/web/v2_notes_panel.js`（新，783 行） |
| 入口 | rail 新增 `#rail-notes`（位于 `#rail-note` 之上，标题「标注清单」） | `app/web/shell.html`（+16 行） |
| 接线 | `bindNotesPanel()`：传 `selectCard` / `cardKey` / `cardTitle` / `toast` / `onOpen`；`onOpen` 关设置抽屉与搜索面板 | `app/web/shell.js`（+39 行，未改 `bindNote` 与 `v2_note.js`） |
| 样式 | `.np-*` 段，只用 themes.css 语义 token，`np-` 前缀，层级复用 `--z-drawer` | `app/web/shell.css`（+187 行，368→555） |
| 单测 | 19 条纯函数用例（分组/筛选/搜索/动作/进度段/page→卡/时间线/请求体） | `app/tests/js/v2_notes_panel.test.mjs`（新，230 行） |
| 静态契约 | 20 条（按钮顺序、端点、expected/by、409 分支、退役页、无裸色值、token 存在、类名齐、`node --check`） | `app/tests/test_v2_notes_panel.py`（新，276 行） |
| 浏览器验收 | 36 条，`/api/ui_notes/*` 全部 stub，含 409 分支与「生产未被写」断言 | `app/tests/js/v2_notes_panel.cdp.mjs`（新，462 行） |

## 2. 契约（前端侧口径）

### 读
`GET /api/ui_notes/list`（不带 `stage`，全量拉一次，客户端筛选）。
`stats` 恒为全量、来自后端，**头部数字只认它**；断网时才退化成本地 `countStats()` 现算。
老数据无 `stage` 字段：按 `status` 推导（`resolved`→`verified`，其余 `open`），与后端 `_stage_of` 同口径。

### 写
唯一写口 `POST /api/ui_notes/stage`，请求体恒为：

```
{ id, to, expected: <展开时的当前 stage>, by: '01', note: <操作区「说明」去空白>, ref: <操作区「文档路径/URL」去空白> }
```

不做 add/update/resolve 的直调（静态测试守住：源码里出现这三个路径即失败）。

### 动作映射
| 当前 stage | 动作（→ 目标） |
|---|---|
| `open` | 开始处理 → `doing` |
| `doing` | 已修复 → `fixed`；驳回 → `rejected` |
| `fixed` | 验收通过 → `verified`；退回处理 → `doing`；驳回 → `rejected` |
| `verified` / `rejected` | 重新打开 → `open` |

`rejected` 不是第五段，是末段的另一种结局：进度条前三段 `done`、末段红色且文案改「驳回」。

### 错误
- **409** → toast「阶段已变化，已刷新」+ `fetchList()` 整表重拉；不重试、不硬写（旧 `expected` 重发会覆盖别人的流转）。
- 非 2xx → 该条内联「保存失败：<detail>（可重试）」，按钮解禁。
- 读失败 → 列表区「读取失败：<原因>」，不清空已有数据。
- 定位失效 → 「元素已不存在：<selector>」；旧页 → 「旧页标注不可定位」且**不给定位按钮**。

### 定位
- 同卡 / 整壳页：`document.querySelector(selector)` → `scrollIntoView({block:'center'})` → `.np-hit` 闪烁 1.2s → 收起抽屉。
- 跨卡：`selectCard(key)` → 轮询 `.card-host[data-card-key=...]` + selector，上限 3s（每 120ms 一次）。
- 旧页（非 `v2:` 前缀）：只标「旧页已退役」，不导航（旧页 DOM 在 `/legacy/`，selector 在本文档必然落空，查一次再报错会把「页面没了」说成「元素改过」）。

### 分组
按 `note.page` 分组：`v2:card:<key>` 取卡名（壳的 `allCards` 查 `title`，拿不到退回 key）；`v2:` 其它 = 「整壳页面」；其余 = 「旧页已退役」。
排序：活跃组按组内最新 `created_at` 倒序，退役组恒沉底；组内 `created_at` 倒序。

## 3. 验证命令与数字

```powershell
cd projects/panel-v2
node app/tests/js/v2_notes_panel.test.mjs          # PASS 19/19
node --check app/web/v2_notes_panel.js app/web/shell.js   # 均 exit 0
node app/tests/js/v2_notes_panel.cdp.mjs           # PASS 36/36
python -m pytest app/tests/test_v2_notes_panel.py -q       # 20 passed
python -m pytest app/plugins/ops/tests app/tests/test_v2_note.py `
  app/tests/test_v2_notes_panel.py app/tests/test_fe_shell_bridge.py -q
# → 198 passed, 1 failed（test_panic_store_mode_shape，既有红灯，本轮未碰）
```

CDP 关键断言（全部实测通过）：
- 请求体严格等于 `{id:'N9001',to:'doing',expected:'open',by:'01',note:'今天就改',ref:'docs/task_x.md'}`；
- 409 后 `window.__npStub.list` 计数增加（真重拉），该条阶段未改；
- 桩外无写入：盘上 `engine/state/ui_notes.json` 条数前后一致（35）。

截图：`_vis/notes_panel/panel_list.png`、`_vis/notes_panel/panel_expand.png`。

测试自身的两处误判（已修，记在此备查）：
1. 抽屉贴边量 `window.innerWidth - rect.right` 得 -19px —— headless 下 `innerWidth` 含滚动条宽，应比 `documentElement.clientWidth`；改为后者，并等 0.18s 滑入动画结束再量（过渡中途 rect 还在屏外）。
2. 断言 `ref` 路径的正则多写了一层转义（`\\.md` 在 JS 模板串里变成字面 `\.md`），改为 `\.md`。

## 4. 未做 / 边界

- **未重启 8810**：按任务书由 01 收口统一重启；本轮浏览器验收跑在现网进程上，静态资源是同盘文件，故渲染/交互已实测，但「进程重启后首次加载」未单独验。
- 未做：批量推进（多选）、按页跳转（点组头切卡）、history 分页（35 条量级不需要）、ref 直接打开本地文件（无 file:// 权限与安全边界，只做复制/网页打开）。
- 未改：`app/plugins/ops/backend.py` 与其测试、`app/web/v2_note.js`、其它插件、`app/core/`、`engine/`。
- 无新增依赖、无 `.bak`、无临时文件残留。


## 5. 01 收口（2026-10-27 14:20~14:35）

- **后端契约由 01 落码**（`app/plugins/ops/backend.py`，ops 0.3.1→**0.3.2**）：`stage` 五段 + `history` 追加；`status` 由 stage 派生（verified/rejected→resolved），旧 `resolve` 等价 stage→verified；新增 `POST /ui_notes/stage`（`expected` 不符 → 409）；`GET /ui_notes/list` 增 `stage` 过滤与 `stats`。新增 `tests/test_ops_ui_notes_stage.py` **10 passed**；同步 `test_ops_plugin.py` 路由清单/版本/空库统计断言。
- **重启 8810**（14:20，健康检查通过）：`/_plugins` ops=0.3.2，`GET /ui_notes/list` 返回 `stats.total=35`。
- **真实页面只读验收**（无 stub，`/v2/` 实机）：**17/17** —— 按钮可用、抽屉打开、35 行全量、按页分组、退役页标记、统计 35/1/34、展开刚迁移条目的时间线（open→verified、by=01、ref 文档）、筛选 1/34、旧页显示「不可定位」且不导航、console 异常 0。截图 `_vis/notes_panel_real/01_real_panel.png`。
- **数据迁移（走接口，不手改文件）**：N20261027-011947-26 ~ 101349-33 共 8 条 → `verified`，`ref` 指向各自 DONE 文档；迁移后 `by_stage`：open 1 / verified 34。`-34`（成交额预估失真）保持待处理。
- **契约文档**：`docs/HANDOFF_20261027_notes_lifecycle.md`（字段、端点、409 语义、迁移记录）。
- 临时验收脚本用后已删（`_vis/notes_panel_real.mjs`）。

- **全量回归**：`pytest app -q` → **1602 passed / 13 failed / 3 skipped**；失败集与改动前完全一致（12 条 legacy 旧页对齐 + `test_panic_store_mode_shape`），新增 30 项测试全绿。
