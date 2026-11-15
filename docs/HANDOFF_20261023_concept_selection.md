# 概念 Tab + 选股三端点交接（2026-10-23 夜 · 4.2 / 4.3）

> 前一份：`docs/HANDOFF_20261023_paper.md`（纸面交易真迁移）
> 本篇只讲 4.2 `/api/concept.json` 与 4.3 selection 三端点。

---

## 1. 一句话状态

**旧端点对齐已 32/32。** `compare_legacy.py` 全部 `ok`，`/api/concept.json` 与旧 8800 **完整 JSON 逐字段全等**；`/api/selection`（8.2MB）/`/api/selection_pool`/`/api/selection_prefs` 也与旧端 **全量 JSON 全等**。

当前：**604 passed / 4 skipped**，`cutover_gate.py` **8 项 PASS**。

---

## 2. 4.2 `/api/concept.json`

| 项 | 内容 |
|---|---|
| 插件 | `app/plugins/concept/`（root 挂 `/api/concept.json`），version 0.1.0 |
| 解析 | `source.py` 从 `engine/common/concept_source.py` 移植；`load_index(concept_dir, sector_names, name_to_code)` / `load_detail(slug, concept_dir)` |
| 渲染 | `render.py` 从 `engine/common/concept_render.py` 移植；`md_to_html(md, name_to_code)` |
| 契约 | `app/core/paths.py::READABLE_TEXT_DIR` + `PluginContext.text_dir(name)`；`manifest.data.text` |
| 数据源 | `夏天-activity/概念追踪/概念与催化总表.md` + `{slug}/概况.md` |
| 验证 | 索引与旧端全等（21 活跃 / 15 观察 / 26 链位 / 5 归档）；26 个详情页 HTML 抽查，5 个仅个股名 linkify 差异（正文同构） |
| 测试 | `app/plugins/concept/tests` 39 passed |

注意：`concept` 插件通过 `ctx.db_read("ths_board.db")` 构建板块名表，通过 `ctx.json_read("stock_names")` 反查个股代码；插件内无 sqlite/无写文件。
`backend.py` 里 `_NAME_CODE_ALIASES` 是已知缺口补丁（宇树科技/新能源车/创业板指/创业板/数据要素），东财名表缺失时才用，后续若更新名表可删。

## 3. 4.3 selection 三端点

| 端点 | 源 | 结果 |
|---|---|---|
| `/api/selection` | `data/panel/selection_latest.json`（8.2MB 盘后快照） | 与旧 8800 **完整 JSON 全等**（含 8.2MB groups/rows） |
| `/api/selection_pool` | `data/panel/selection_pool.json` | 全等（date/count/is_current/names/items/slots/keep） |
| `/api/selection_prefs` | `data/panel/selection_filter.json` | 全等（meta/pools/ma/pin/inds/cons/boards/sigs/boardOn/hot） |

白名单：`paths.READABLE_PANEL` 新增 `selection_latest` / `selection_pool` / `selection_filter`；`compat_misc/plugin.json` 同步声明。
`freshness` 是简化版：只按工作日 + 18:00 粗判，不识别法定节假日；周末不喊 stale。要精确需另登记 `trading_calendar` 口径库。

## 4. 验证命令

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
# 604 passed / 4 skipped
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\compare_legacy.py
# 32/32 ok
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\cutover_gate.py
# 8 PASS
```

## 5. 还差什么

1. **`signal_buy` 条件单**：`signals/library.py` 60KB 未接，`signal_provider=None`；其余 8 类条件单可用。
2. **复盘两个端点**：`compat_misc` 的 `/api/review.md`、`/api/review_chart.json` 仍是骨架（需要 Markdown 复盘目录 + 多份 panel JSON 白名单）。
3. **concept 详情 linkify**：5 个概念页有个股/指数名 linkify 差异（正文 HTML 一致，仅 `<span class="k-name">` 包裹范围不同）；不影响概念 Tab。
4. **旧 8800 写入口**：selection 的 `POST /api/selection_prefs`、选股池增删仍只在旧 8800；新版只读三端点。若要在 8810 保存偏好，需另做写通道（不在 4.2/4.3 范围）。
