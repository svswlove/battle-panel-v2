# 任务书 4.5 · `/api/review.md`（复盘 Tab 默认渲染）

> 执行者：DSHana｜主会话验收｜2026-10-23
> **无 shell，不运行 pytest/python；只写代码，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`

---

## 0. 背景

`compat_misc` 的 `/api/review.md` 目前是空骨架。旧 8800 的实现：
- 扫描 `夏天-activity/每日复盘/日复盘 YYYY-MM-DD.md`；
- `common/review_source.py` 选最新/指定日期 + 指纹缓存 + 降级；
- `md2html.py` 把 MD 渲染为带色标记的正文 HTML；
- 默认 `theme` 路径不渲染泳道/赛道图（`OH_MAINLINE_ALLOW != 1`），`charts` 为 `{swimlane:null,track:null}`。

本任务先做 **默认 v6 路径**（`/api/review.md?theme=...`），不做 `paper=1`（保留 501/404，让旧前端自动回退 v6）。

---

## 1. 交付文件

```
app/plugins/review/plugin.json
app/plugins/review/backend.py
app/plugins/review/source.py       # 从 _vendor_src/review_source.py 移植
app/plugins/review/render.py       # 从旧 server._md_to_body_html + _inline_svg 移植
app/plugins/review/md2html.py      # 从 _vendor_src/md2html.py 逐字复制
app/plugins/review/tests/test_review_source.py   # 从 _vendor_src/test_review_source.py 移植
app/plugins/review/tests/test_review_api.py      # 新增 API 测试
app/core/paths.py                  # 增加 READABLE_TEXT_DIR["daily_review"]
app/plugins/compat_misc/backend.py # 删除 /review.md 骨架路由（避免 root 冲突）
app/plugins/compat_misc/tests/test_compat_misc.py # 同步删除/调整 review.md 骨架测试
```

---

## 2. 契约

`app/core/paths.py`：
```python
READABLE_TEXT_DIR = {
  "concept_tracking": WS / "夏天-activity" / "概念追踪",
  "daily_review": WS / "夏天-activity" / "每日复盘",
}
```
`plugin.json`：
```json
"data": {
  "read": [],
  "panel": [],
  "json": [],
  "lib": [],
  "text": ["daily_review"],
  "quotes": false,
  "write": []
}
```

`source.py` 移植要求：
- `REVIEW_DIR` 不再 import `common.paths`；`pick_latest(date, review_dir)` 和 `render_cached(pick, theme, renderer)` 接受目录/路径。
- `MAINLINE_DIR` 置空或删除（v6 charts 不生成，`mainline_version()` 返回 `(0,0)` 即可）。
- 保留 `ReviewPick` / 扫描 30s TTL / LRU / degraded / `cache` 字段。
- 保留文件名严格匹配 `日复盘 YYYY-MM-DD.md`。

`render.py`：
- `render_review(pick) -> dict`：
  - 读 `pick.path` 的 UTF-8；
  - `md2html.extract_title_and_meta(md_text)`；
  - `markdown.markdown(..., extensions=["tables","fenced_code","codehilite","sane_lists"])`；
  - `md2html.colorize_td_symbols` + `md2html.apply_color_marks`；
  - 表格包 `<div class="md-table-wrap">`；
  - `_inline_svg`：把 `<img src="...svg">` 按 MD 目录解析并内联为 `<div class="rv-chart"><svg...></svg></div>`（复刻旧 `server.py:1274`，读不到原样保留）；
  - 去掉 `<script>`；
  - 返回 `{date,file,title,html,mtime_ns,charts:{"swimlane":None,"track":None}}`。
- 任何渲染异常抛 `RuntimeError`，由 `source.render_cached` 捕获降级。

`backend.py`：
```python
@router.get("/review.md")
def review_md(date: Optional[str]=None, theme: Optional[str]=None, paper: int=0):
    if paper:
        raise HTTPException(501, detail="paper=1 复盘完整管线尚未迁移，请用默认 theme 渲染")
    pick = source.pick_latest(date, ctx.text_dir("daily_review"))
    if pick is None: raise HTTPException(404, detail=...)
    res = source.render_cached(pick, theme or "", render.render_review)
    return {"date": pick.date, "file": basename(pick.path), "title": res.get("title"),
            "html": res.get("html"), "mtime_ns": pick.mtime_ns,
            "charts": res.get("charts") or {"swimlane":None,"track":None},
            "degraded": bool(res.get("degraded")), "error": res.get("error"),
            "cache": res.get("cache"), "theme": theme or ""}
```
- 返回键必须与旧端一致；`paper=1` 501 是有意让前端回退 v6。
- 插件 root 挂 `/api/review.md`；同时删除 `compat_misc` 的 `/review.md` 路由与 `build_review_md` 骨架（只删这一个）。

---

## 3. 测试

### `test_review_source.py`
从旧测试移植，`REVIEW_DIR` 用 tmp 目录、`MAINLINE_DIR` 空；保留扫描/pick/cache/degraded 断言。

### `test_review_api.py`
- `TestClient(app)` 调真实 `/api/review.md`：200，键集合 `{date,file,title,html,mtime_ns,charts,degraded,error,cache,theme}`；`html` 非空。
- `?date=1900-01-01` → 404。
- `?paper=1` → 501（旧前端会回退默认）。
- 可选：tmp 目录无 MD → 404。

### 旧端对比
主会话会手工比较 `/api/review.md` 的 `title/date/html` 与旧 8800；结构一致即可，HTML 可能因 md2html 版本细节有差异，允许记录。

---

## 4. 硬约束

- 插件禁止 `sqlite3.connect` / `from common` / `from core` / `xtquant` / `from eltdx` / 写文件。
- 不改 `concept` / `paper` / `selection` 相关文件。
- 中文注释保留。
- `md2html.py` 逐字复制，不改冻结接口。

## 5. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/plugins/review/tests -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
```
