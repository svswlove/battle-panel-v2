# 任务书 4.8 · review 插件纸面管线迁移（`/api/review.md?paper=1`）

## 目标

把旧 8800 `/api/review.md?paper=1`（report-builder v2 完整纸面管线：报头/速览带/暖白纸面/作用域 CSS）迁移到 panel-v2 的 review 插件，替换现有 501 占位。迁移后 8810 与 8800 对同一份 MD 返回结构一致的 paper 载荷。

## 已定架构决策（不许改）

1. **report.css 不复制进插件**：插件目录的 `md2html.py` 是冻结副本，但没有也不放 `report.css`。纸面 CSS 唯一源固定为 `~/.hanako/skills/report-builder/report.css`（与旧端同一文件），`paper.py` 只读 `open(..., encoding="utf-8")`，按 `(mtime_ns, size)` 缓存派生结果——与旧端 `_paper_scope_css` 同口径，仅把「从 md2html 模块同目录推导」改成固定路径。
2. **渲染逻辑独立成 `app/plugins/review/paper.py`**：CSS 作用域化五件套 + 纸面管线 + 前排/退潮合并表 + `render_paper(pick)` 渲染回调，全部移植到这一个文件。`backend.py` 只改路由分支。
3. **返回键（旧端 keys_expected.json 口径）**：v6 分支 10 键不变；paper 分支 = 那 10 键 ∪ `{paper, masthead, css, footer}`（共 14 键，`paper: true`、`theme: "paper"`）。
4. **降级语义逐字保留**：渲染异常由 `source.render_cached` 统一降级（保留上一份成功内容 + `degraded=True`，不 500）；`css is None` 且未 degraded 的「静默成功」防御层也要移植（旧端 server.py 1907-1912）。
5. 插件契约不变：零写盘、无 sqlite/xtquant/common/core import、GET-only。

## 旧端源码（逐行移植参考，只读）

`OH-WorkSpace\engine\server.py`：

| 函数/常量 | 行号 |
|---|---|
| `_PAPER_SCOPE` / `_PAPER_SHIELD` | 1346-1390 |
| `_strip_css_comments` | 1391 |
| `_iter_css_rules` | 1418 |
| `_split_selectors` | 1457 |
| `_scope_selectors` | 1466 |
| `_scope_css` | 1491 |
| `_css_escaped_scope` | 1517 |
| `_paper_scope_css` | 1544（改：CSS 路径按决策 1） |
| `_inline_svg_paper` | 1580 |
| `_paper_pipeline` | 1605 |
| `_extract_table_block` | 1650 |
| `_merge_sector_front_back` | 1664 |
| `_paper_review_renderer` | 1741（改名 `render_paper`） |
| 路由 paper 分支 | 1898-1920 |

注意：`_review_md2html()` 的等价物已存在于 `app/plugins/review/render.py::_md2html()`（按文件路径加载同目录冻结副本），paper.py 直接复用它拿 `convert_md_to_html`（冻结副本已含，md2html.py:656；它接收 css 参数但不读盘，传 `""` 与旧端一致）。

旧端测试参考（用例口径照搬）：`engine/tests/test_paper_sector_merge.py`（垫片/合并表自检）、`engine/tests/test_review_api.py` 117-225（paper=1 键集/css 非空/figure.chart 保留）。

## 改动白名单（只许碰这些）

- 新建 `app/plugins/review/paper.py`
- 修改 `app/plugins/review/backend.py`（paper=1 分支：501 → 真实实现）
- 修改 `app/plugins/review/tests/test_review_api.py`（`test_review_md_paper_501` 改真实断言；`test_backend_source_has_no_write_code` 的参数列表若需加 `paper.py` 就加；新增 paper.py 单测）
- 新建/更新 `docs/HANDOFF_20261024_paper_pipeline.md`

禁止：改旧端 engine/ 任何文件、改 report-builder skill 文件、改前端、碰其他插件、写任何生产数据、git commit（验收人负责）。

## 环境事实

- 项目根：`OH-WorkSpace\projects\panel-v2`
- Python：`OH-WorkSpace\envs\qmt312\Scripts\python.exe`
- report.css 唯一源：`<HOME>\.hanako\skills\report-builder\report.css`
- 旧端 8800 在跑（对照用，只读 GET）；新端 8810 在跑，改动后需重启（先 `Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match 'port 8810'}` 找 PID 停掉，再 `powershell -File run.ps1` 拉起或 `tools\ensure_8810.ps1`，启动后等 `/api/health` ok=true）

## 自测要求（顺序执行，全部留下输出）

1. `python -m pytest app/plugins/review/tests -q` → 全绿（含改写的 paper 用例）
2. `python -m pytest -q`（项目根，全套）→ 680+ 全绿不回退
3. 重启 8810 → `http://127.0.0.1:8810/api/health` ok=true
4. `Invoke-WebRequest 'http://127.0.0.1:8810/api/review.md?paper=1'` → 200，键含 paper/masthead/css/footer，css 非空
5. 新旧对比：同时 GET 8800 与 8810 的 `?paper=1`，`title/masthead/html/css/footer` 五字段逐字段比对，报告全等/差异点

## 交付格式

回执 ≤300 字：做了什么 / 测试数字 / 新旧对比结论 + 落盘路径。差异点如实列，不粉饰。

## 截止

软时间盒 60 分钟；到点未完 → 落盘已完成部分 + 回执「已完成 / 未完成 / 下一步」。
