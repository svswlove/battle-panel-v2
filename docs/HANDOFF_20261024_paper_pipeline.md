# 复盘纸面管线交接（2026-10-24 · 任务书 4.8）

> 前一份：`docs/HANDOFF_20261023_review.md`（4.5 复盘 Tab v6 迁移）
> 本篇：`/api/review.md?paper=1` 纸面保真完整管线迁移（替换 4.5 的 501 占位）。

---

## 1. 状态

**已完成落盘，自测未跑（本执行手无 shell / 进程 spawn 被沙箱 EPERM 拒绝）。** 见 §4 待跑清单。

- `paper=1` 已从 501 占位换成真实纸面管线：report-builder v2 完整管线
  （报头 + 速览带 + 十节节奏 + 涨跌芯片 + 暖白纸面）+ report.css 作用域派生 + 前排/退潮合并表。
- 返回键：v6 十键 ∪ `{paper, masthead, css, footer}`（14 键，`paper: true`、`theme: "paper"`）。
- 降级语义逐字保留：渲染异常由 `source.render_cached` 统一降级（保留上一份成功内容 +
  `degraded=True`，不 500）；`css is None` 且未 degraded 的「静默伪成功」防御层
  （旧端 `server.py:1907-1912`）照搬在 `backend.build_review_md_paper` 里。
- 插件契约不变：零写盘、无 sqlite3 / xtquant / common / core import、GET-only。

## 2. 文件（改动白名单内）

```
app/plugins/review/paper.py                   # 新建：纸面管线全部逻辑
app/plugins/review/backend.py                 # 改：paper=1 分支 501 → build_review_md_paper
app/plugins/review/tests/test_review_api.py   # 改：501 用例 → 真实 200 + 纸面单测 24 项
docs/HANDOFF_20261024_paper_pipeline.md       # 本篇
```

未碰：旧端 `engine/`、report-builder skill、前端、其他插件、任何生产数据。

### paper.py 结构

| 段 | 内容 | 来源 |
|---|---|---|
| 常量 | `_PAPER_SCOPE` / `_PAPER_SHIELD` | 旧 server.py 1346-1385 逐字 |
| CSS 六件套 | `_strip_css_comments` / `_iter_css_rules` / `_split_selectors` / `_scope_selectors` / `_scope_css` / `_css_escaped_scope` | 旧 1391-1538 **逐字** |
| CSS 派生 | `_paper_scope_css` + `reset_css_cache` | 旧 1544-1573，仅改路径 |
| 管线 | `_inline_svg_paper` / `_paper_pipeline` / `_extract_table_block` / `_merge_sector_front_back` | 旧 1580-1738 逐字 |
| 渲染回调 | `render_paper` | 旧 `_paper_review_renderer` 1741-1765 改名 |

**唯一两处偏离旧端**（决策 1 + 决策 2 授权）：

1. `_paper_scope_css` 的 CSS 路径：旧端 `os.path.dirname(m.__file__) + "report.css"`
   → 固定 `REPORT_CSS_PATH = ~/.hanako/skills/report-builder/report.css`。
   缓存口径（`(mtime_ns, size)`）、机械自检、垫片拼接全部不变。
2. `_review_md2html()` → `_md2html()`：复用 `render.py` 里已按文件路径加载的
   同目录冻结副本 `md2html.py`（`convert_md_to_html(md_text, "")` 传空 css，与旧端一致）。

## 3. 关键口径

- **作用域唯一源**：`#panel-review .rv-paper`；`:root` / `html` / `body` → 作用域容器自身，
  `*` → 容器后代；@media 递归作用域化，@page 与 @media print 丢弃。
- **report.css 不复制进插件**：插件目录没有也不放 `report.css`（实测唯一源 21,077 B / 18,277 字符）。
- **图表**：纸面用 MD 内联 SVG（`charts/*.svg`），保留 `figure.chart`、**不**做 v6 的白底改透明；
  面板深色图两键 `charts: {swimlane: null, track: null}`。
- **缓存维度**：`theme="paper"`，与 v6 真主题名绝不互污染；`RENDERER_VERSION = "rv3"` 未动。
- **前排/退潮合并表**：仅展示层 HTML 变换（领涨 TOP10 / 领跌 BOTTOM10 → 单表双栏），
  列数不等 / 缺表 / 行数差 > 3 / td 数与表头不齐 → 原样返回，不硬拼。

## 4. 自测（待验收人执行；本执行手进程 spawn 被沙箱 EPERM 拒绝，一条都没跑起来）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app\plugins\review\tests -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
# 重启 8810（先按命令行匹配 'port 8810' 找 PID 停掉，再 powershell -File run.ps1 或 tools\ensure_8810.ps1）
Invoke-WebRequest 'http://127.0.0.1:8810/api/review.md?paper=1'
```

新增用例 24 项（其中 1 项替换原 `test_review_md_paper_501` → 净 +23；另 3 个参数化静态扫描
各多一组 `paper` → 再 +3）。4.5 基线是 40 passed，预期本次约 66 passed（以验收人实跑为准）：

- CSS 六件套 + 垫片 9 项：`:root`/`body`/`*` 改写、@media 递归 + print/@page 丢弃、
  逗号与 `:not()` 安全、逃逸自检、注释剥离保留字符串、规则切分、垫片自检、真实 report.css 实派生；
- 派生缓存 2 项：`(mtime_ns, size)` 命中/重算、唯一源缺失 → `""`；
- 合并表 3 项：happy path（双栏 / 中缝 tb-divide / 吸收两个 caption）、5 条防御分支原样返回、
  单侧缺行补 `—`；
- 管线 5 项：masthead/doc/footer、内联 SVG 保 `figure.chart`、剥 script、`render_paper` 10 键、
  MD 读不到抛 `RuntimeError`；
- HTTP 5 项：`paper=1` → 200 + 14 键 + css 非空 + 二次 `cache=hit`、缺 css → 502、
  渲染抛错 → 502（不 500）、插件级直调 14 键、`paper=1&date=1900-01-01` → 404。

静态自检已做（无 shell 的替代验证，**非运行时结论**）：

- 六个 CSS 函数 + `_extract_table_block` / `_merge_sector_front_back` 与旧端逐字 diff 一致
  （除上表两处授权偏离）；
- 用 Node 复刻同一算法对真实 report.css 实跑：派生 18,970 字符、作用域出现 161 次、
  `_css_escaped_scope` 返回 `None`、无裸规则、无 `@page`/`print`；
- `paper.py` 无写盘 token、无 `main()`、无 `sqlite3.connect` / `xtquant` /
  `from common` / `from core`。

## 5. 剩余

1. §4 自测未跑 —— 需验收人执行并回填数字（含 8810 重启后 `/api/health` ok=true）。
2. 新旧对比（8800 vs 8810 的 `title/masthead/html/css/footer` 五字段逐字段比对）未做，同样卡在无 shell。
3. 前端未改：`app/web/legacy/index.html::renderReviewMdPaper` 早已消费
   `paper/masthead/css/footer`，本次无需改动（仍按「图表 → 纸面 → v6」降级链走）。
