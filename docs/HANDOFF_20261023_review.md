# 复盘 Tab `/api/review.md` 交接（2026-10-23 夜 · 4.5）

> 前一份：`docs/HANDOFF_20261023_signal.md`
> 本篇：复盘 Tab 默认 v6 渲染迁移。

---

## 1. 状态

`/api/review.md` 已从 `compat_misc` 骨架迁到独立 `review` 插件（root `/api/review.md`）：

- 默认 `?theme=...` 路径与旧 8800 **HTML 逐字节相等**（最新 `日复盘 2026-10-22.md`，15,853 chars）。
- 返回键与旧端一致：`{date,file,title,html,mtime_ns,charts,degraded,error,cache,theme}`。
- `paper=1` 暂返回 **501**，旧前端会自动回退 v6 默认渲染（`renderReviewMdPaper` → `renderReviewMdV6`）。
- `/api/review_chart.json` 已迁：core `app/core/review_chart.py`（从旧 `review_chart_data.py` 逐字搬），与旧 8800 **全量 RV_DATA 逐字段相等**（cache 除外）。

## 2. 文件

```
app/plugins/review/plugin.json
app/plugins/review/backend.py
app/plugins/review/source.py     # review_source 移植（扫描/指纹/LRU/降级）
app/plugins/review/render.py     # _md_to_body_html + _inline_svg 移植
app/plugins/review/md2html.py    # 冻结接口副本（已去 CLI main，避免写文件）
app/core/review_chart.py         # 图表数据装配器（从旧 review_chart_data.py 逐字搬）
app/core/tests/test_review_chart.py
app/plugins/review/tests/        # 40 passed
app/core/paths.py                # READABLE_TEXT_DIR["daily_review"]
```

`compat_misc` 的 `/review.md` 路由与 `build_review_md` 已让路；`/review_chart.json` 保留。

## 3. 验证

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/plugins/review/tests -q
# 40 passed
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
# 678 passed / 4 skipped
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\cutover_gate.py
# 8 PASS
```

实测：
```text
GET /api/review.md  →  与旧 8800 html_equal=True, len=15853
GET /api/review.md?paper=1 → 501（预期，旧前端回退）
```

## 4. 剩余

1. **`/api/review_chart.json`**：已迁（core 装配器），与旧端 RV_DATA 逐字段相等。
2. **`review.md?paper=1`**：report-builder 完整管线（masthead/css）未迁，目前故意 501。
3. **复盘图表**：`review` 默认 v6 不生成泳道/赛道图（与旧端默认 `OH_MAINLINE_ALLOW != 1` 一致）。
