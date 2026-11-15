# -*- coding: utf-8 -*-
"""review 插件 HTTP 契约测试（4.5 建，4.8 补纸面 paper=1 真实断言）。

覆盖（对齐任务书 docs/task_review_md_4_5.md §3）：

1. `test_manifest_root_mount_readonly`：`backend.mount == "root"`、`data.text == ["daily_review"]`、
   `data.write` 为空、`daily_review` 确实登记在 `paths.READABLE_TEXT_DIR`；
2. `test_routes_only_get_review_md`：路由表只有 `/review.md` 且只有 GET、无 `/api` 前缀
   （core 负责拼）；
3. `test_review_md_real`：真实 `/api/review.md` → 200，键集合与旧端**逐字一致**，
   `html` 非空、`title` 非空、`charts` 双 null、`degraded=False`；第二次 → `cache="hit"`；
4. `test_review_md_date_404`：`?date=1900-01-01` → 404（精确日不回溯）；
5. `test_review_md_paper_real`：`?paper=1` → **200**（纸面完整管线，任务书 4.8），
   键集 = v6 十键 ∪ {paper, masthead, css, footer}，css 非空、paper=true、theme="paper"；
6. `test_review_md_empty_dir_404`：白名单指到空 tmp 目录 → 404（不编造内容）；
7. `test_render_review_inline_svg_and_strip_script`：`render.render_review` 把 MD 里的
   `charts/x.svg` 内联成 `<div class="rv-chart"><svg…>`，并剥掉 `<script>`；
8. `test_no_forbidden_imports`：review 插件源码 AST 级 import 扫描（禁 sqlite3 / common /
   core / xtquant / eltdx）；9. `test_backend_source_has_no_write_code`：backend/render/source/paper
   四份源码**无写盘动作**（`md2html.py` 是逐字冻结副本，CLI `main()` 才写文件，单独说明）。
10. 纸面单测（任务书 4.8）：CSS 作用域化六件套 + 垫片 + 前排/退潮合并表 + 纸面管线
   （`paper.py`），口径照搬旧端 `engine/tests/test_paper_sector_merge.py` 与
   `test_review_api.py` 的 PaperScopeCssTest。

> 真实数据断言只读 `夏天-activity/每日复盘/`（生产只读，不写）；临时用例全部落 `tmp_path`。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import paths as core_paths  # noqa: E402
from app.core.contract import (  # noqa: E402
    find_forbidden_db_usage,
    find_forbidden_quote_usage,
    load_manifest,
)
from app.core.main import app  # noqa: E402
from app.plugins.review import backend  # noqa: E402
from app.plugins.review import paper as review_paper  # noqa: E402
from app.plugins.review import render as review_render  # noqa: E402
from app.plugins.review import source as review_source  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
RENDER_PY = PLUGIN_DIR / "render.py"
SOURCE_PY = PLUGIN_DIR / "source.py"
PAPER_PY = PLUGIN_DIR / "paper.py"

# 旧端 `api_review_md` 返回键（docs/misc_samples/keys_expected.json 的 /api/review.md）
EXPECTED_KEYS = {"date", "file", "title", "html", "mtime_ns", "charts",
                 "degraded", "error", "cache", "theme"}
# 纸面分支（paper=1）= 上面 10 键 ∪ 这 4 键（旧端 server.py:1898 口径）
EXPECTED_PAPER_KEYS = EXPECTED_KEYS | {"paper", "masthead", "css", "footer"}

# report.css 唯一源（与旧端同一份文件；不复制进插件）
REPORT_CSS = Path.home() / ".hanako" / "skills" / "report-builder" / "report.css"


@pytest.fixture(autouse=True)
def _reset_review_caches():
    """每个用例前后清缓存：扫描 TTL / LRU / 纸面 CSS 派生都是模块级状态，别互相污染。"""
    review_source.reset_caches()
    review_paper.reset_css_cache()
    yield
    review_source.reset_caches()
    review_paper.reset_css_cache()


# ---------------------------------------------------------------- 1. manifest


def test_manifest_root_mount_readonly() -> None:
    manifest = load_manifest(PLUGIN_DIR)
    assert manifest.id == "review"
    assert manifest.backend.mount == "root"
    assert manifest.backend.entry == "register"
    assert manifest.data_write == ()                     # 全只读，一条写都没有
    assert manifest.text_read == ("daily_review",)       # 只读文本目录声明
    assert manifest.quotes_read is False                 # 不碰行情源
    for name in manifest.text_read:
        assert name in core_paths.READABLE_TEXT_DIR, name


def test_daily_review_dir_registered() -> None:
    """4.5 白名单登记：`daily_review` → 夏天-activity/每日复盘（路径推导自 WS，不写死盘符）。"""
    d = core_paths.text_dir("daily_review")
    assert d is not None
    assert d == core_paths.WS / "夏天-activity" / "每日复盘"


# ---------------------------------------------------------------- 2. 路由


class _FakeCtx:
    """只实现本插件用到的 ctx 面（text_dir）；目录可直接指定。"""

    id = "review"

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def text_dir(self, name: str) -> Path:
        if name != "daily_review":
            raise PermissionError("未声明 %s" % name)
        return self.directory


def test_routes_only_get_review_md(tmp_path: Path) -> None:
    router = backend.register(_FakeCtx(tmp_path))
    paths_ = [r.path for r in router.routes]
    assert set(paths_) == {"/review.md", "/review_chart.json", "/review_six.json"}
    assert all(not p.startswith("/api") for p in paths_), "前缀由 core 拼（mount=root → /api）"
    for route in router.routes:
        assert getattr(route, "methods", set()) <= {"GET", "HEAD"}, "本插件只读"


# ---------------------------------------------------------------- 3~6. 真实端点


def test_review_md_real() -> None:
    """真实复盘 MD → 200，键集合与旧端逐字一致，html 非空。"""
    client = TestClient(app)
    res = client.get("/api/review.md")
    assert res.status_code == 200, res.text
    body: Dict[str, Any] = res.json()
    assert set(body) == EXPECTED_KEYS
    assert body["date"] and len(body["date"]) == 10          # YYYY-MM-DD
    assert body["file"] == "日复盘 %s.md" % body["date"]
    assert body["title"]
    assert isinstance(body["html"], str) and body["html"].strip()
    assert body["mtime_ns"] > 0
    assert body["charts"] == {"swimlane": None, "track": None}   # v6 默认不出图
    assert body["degraded"] is False and body["error"] is None
    assert body["theme"] == ""
    assert body["cache"] in ("hit", "miss")
    # 第二次同一指纹 → 命中缓存（前端 60s 轮询稳态零渲染）
    again = client.get("/api/review.md").json()
    assert again["cache"] == "hit"
    assert again["html"] == body["html"]


def test_review_md_theme_echo_and_cache_split() -> None:
    """`?theme=` 只回显 + 参与缓存键（不改变 v6 正文，charts 仍双 null）。"""
    client = TestClient(app)
    base = client.get("/api/review.md")
    if base.status_code != 200:
        pytest.skip("本机没有可用复盘 MD")
    themed = client.get("/api/review.md", params={"theme": "nightsail"})
    assert themed.status_code == 200
    body = themed.json()
    assert body["theme"] == "nightsail"
    assert body["charts"] == {"swimlane": None, "track": None}
    assert set(body) == EXPECTED_KEYS


def test_review_md_date_404() -> None:
    res = TestClient(app).get("/api/review.md", params={"date": "1900-01-01"})
    assert res.status_code == 404
    assert "复盘 MD 缺失" in res.json()["detail"]


def test_review_md_paper_real() -> None:
    """`paper=1` → 200 真实纸面载荷（任务书 4.8：纸面完整管线已迁移）。

    键集 = v6 十键 ∪ {paper, masthead, css, footer}（共 14 键）；`css` 非空（report.css
    作用域派生成功才算真成功），`paper=true`、`theme="paper"`、charts 双 null。
    """
    if not REPORT_CSS.exists():
        pytest.skip("report.css 唯一源不可用（%s）" % REPORT_CSS)
    client = TestClient(app)
    res = client.get("/api/review.md", params={"paper": 1})
    assert res.status_code == 200, res.text
    body: Dict[str, Any] = res.json()
    assert set(body) == EXPECTED_PAPER_KEYS
    assert body["paper"] is True
    assert body["theme"] == "paper"                    # 与 v6 主题维度隔离
    assert body["date"] and len(body["date"]) == 10
    assert body["file"] == "日复盘 %s.md" % body["date"]
    assert body["title"]
    assert isinstance(body["html"], str) and body["html"].strip()
    assert isinstance(body["masthead"], str) and "masthead" in body["masthead"]
    assert isinstance(body["footer"], str) and "colophon" in body["footer"]
    assert isinstance(body["css"], str) and body["css"].strip()
    assert "#panel-review .rv-paper" in body["css"]     # 作用域派生生效
    assert body["charts"] == {"swimlane": None, "track": None}
    assert body["degraded"] is False and body["error"] is None
    assert body["cache"] in ("hit", "miss")
    # 第二次同一指纹 → 命中缓存（paper 维度独立，不与 v6 抢条目）
    again = client.get("/api/review.md", params={"paper": 1}).json()
    assert again["cache"] == "hit"
    assert again["css"] == body["css"] and again["html"] == body["html"]


def _patch_paper_renderer(monkeypatch, fn) -> None:
    """把纸面渲染回调替换掉，打在**所有已加载的 paper 模块副本**上。

    为什么要遍历 sys.modules：core 用 spec_from_file_location 加载 backend，
    走不到包导入时会用 `_panel_v2_review_paper` 这类按路径加载的名字再加载一份 paper，
    两份模块对象的 `render_paper` 是各自独立的属性，只打一份可能不生效。
    """
    targets = {id(review_paper): review_paper}
    for name, mod in list(sys.modules.items()):
        if mod is None or not (name.endswith("_paper") or name.endswith(".paper")):
            continue
        if getattr(mod, "render_paper", None) is not None:
            targets[id(mod)] = mod
    for mod in targets.values():
        monkeypatch.setattr(mod, "render_paper", fn)


def test_review_md_paper_missing_css_is_failure(monkeypatch) -> None:
    """防御层（旧端 server.py:1907-1912）：渲染器返回缺 css 的「静默伪成功」→ 按失败处理（502）。"""
    def _fake(pick):
        return {"date": pick.date, "file": "f.md", "title": "t", "html": "<p>x</p>",
                "masthead": "<header></header>", "mtime_ns": pick.mtime_ns,
                "charts": {"swimlane": None, "track": None}}   # 无 css / 无 paper

    _patch_paper_renderer(monkeypatch, _fake)
    review_source.reset_caches()
    res = TestClient(app).get("/api/review.md", params={"paper": 1})
    assert res.status_code == 502
    assert "css" in res.json()["detail"]


def test_review_md_paper_render_error_not_500(monkeypatch) -> None:
    """纸面渲染抛错 → 走同一 degraded 机制：无历史成功内容 → 502（不裸 500、不 404）。"""
    def _boom(pick):
        raise RuntimeError("paper boom")

    _patch_paper_renderer(monkeypatch, _boom)
    review_source.reset_caches()
    res = TestClient(app).get("/api/review.md", params={"paper": 1})
    assert res.status_code == 502
    assert "paper boom" in res.json()["detail"]


def test_build_review_md_paper_from_tmp_md(tmp_path: Path) -> None:
    """插件级直调：tmp 目录的一份 MD 走纸面管线 → 14 键、masthead/css/html 非空。"""
    if not REPORT_CSS.exists():
        pytest.skip("report.css 唯一源不可用")
    md = ("# 日复盘 2026-10-23（周五 · 测试）\n\n> 数据时点：收盘\n\n"
          "## 一、板块\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    (tmp_path / "日复盘 2026-10-23.md").write_text(md, encoding="utf-8")
    body = backend.build_review_md_paper(_FakeCtx(tmp_path), None)
    assert set(body) == EXPECTED_PAPER_KEYS
    assert body["date"] == "2026-10-23"
    assert body["paper"] is True and body["theme"] == "paper"
    assert body["degraded"] is False
    assert "mh-band" in body["masthead"]          # 报头速览带
    assert "table-wrapper" in body["html"]        # 完整管线（表格包 wrapper）
    assert "colophon" in body["footer"]
    assert "#panel-review .rv-paper" in body["css"]


def test_build_review_md_paper_date_404() -> None:
    """纸面分支同样：指定日期不存在 → 404（精确日不回溯）。"""
    res = TestClient(app).get("/api/review.md", params={"paper": 1, "date": "1900-01-01"})
    assert res.status_code == 404
    assert "复盘 MD 缺失" in res.json()["detail"]


def test_review_md_empty_dir_404(tmp_path: Path, monkeypatch) -> None:
    """白名单指到空目录 → 404（不是空 html，也不编造复盘）。"""
    empty = tmp_path / "daily_review_empty"
    empty.mkdir()
    monkeypatch.setitem(core_paths.READABLE_TEXT_DIR, "daily_review", empty)
    review_source.reset_caches()
    res = TestClient(app).get("/api/review.md")
    assert res.status_code == 404


def test_build_review_md_from_tmp_md(tmp_path: Path) -> None:
    """插件级直调（不走 HTTP 白名单）：tmp 目录里的一份 MD 能正常渲染，图表双 null。"""
    md = "# 日复盘 2026-10-23（周五 · 测试）\n\n正文 **加粗**。\n"
    (tmp_path / "日复盘 2026-10-23.md").write_text(md, encoding="utf-8")
    body = backend.build_review_md(_FakeCtx(tmp_path), None, None)
    assert set(body) == EXPECTED_KEYS
    assert body["date"] == "2026-10-23"
    assert body["file"] == "日复盘 2026-10-23.md"
    assert "日复盘 2026-10-23" in body["title"]
    assert "<strong>加粗</strong>" in body["html"]
    assert body["charts"] == {"swimlane": None, "track": None}
    assert body["degraded"] is False and body["error"] is None


# ---------------------------------------------------------------- 7. 渲染细节


def test_render_review_inline_svg_and_strip_script(tmp_path: Path) -> None:
    """`_inline_svg` 复刻旧端：`charts/*.svg` 内联成 `<div class="rv-chart"><svg>`；
    `<script>` 一律剥除；读不到的图**原样保留**（不编造）。"""
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "swimlane.svg").write_text(
        '<svg width="10" height="10"><rect width="10" height="10" fill="#FFFFFF"/>'
        '<circle r="1"/></svg>', encoding="utf-8")
    md = (
        "# 日复盘 2026-10-23（周五）\n\n"
        "![泳道图](charts/swimlane.svg)\n\n"
        "![缺失图](charts/nope.svg)\n\n"
        "<script>alert(1)</script>\n"
    )
    p = tmp_path / "日复盘 2026-10-23.md"
    p.write_text(md, encoding="utf-8")

    pick = review_source.ReviewPick(path=str(p), date="2026-10-23",
                                    mtime_ns=p.stat().st_mtime_ns,
                                    size=p.stat().st_size)
    out = review_render.render_review(pick)

    assert out["date"] == "2026-10-23" and out["file"] == p.name
    assert out["charts"] == {"swimlane": None, "track": None}
    assert out["mtime_ns"] == pick.mtime_ns
    html = out["html"]
    assert '<div class="rv-chart"><svg' in html            # 内联成功
    assert 'fill="transparent"' in html                    # 白底 rect → 透明（深色主题可读）
    assert 'src="charts/nope.svg"' in html                 # 读不到的图原样保留，不编造
    assert "<script" not in html and "alert(1)" not in html


def test_render_review_raises_on_missing_file(tmp_path: Path) -> None:
    """MD 读不到 → RuntimeError（由 render_cached 捕获降级，不穿透成 500 之外的异常）。"""
    pick = review_source.ReviewPick(path=str(tmp_path / "nope.md"), date="2026-10-23",
                                    mtime_ns=1, size=1)
    with pytest.raises(RuntimeError):
        review_render.render_review(pick)


# ---------------------------------------------------------------- 8~9. 静态禁令


def _top_level_imports(path: Path) -> set:
    """模块顶层（含 try/except 块内）import 的顶层包名集合。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


@pytest.mark.parametrize("path", [BACKEND_PY, RENDER_PY, SOURCE_PY, PAPER_PY],
                         ids=["backend", "render", "source", "paper"])
def test_no_forbidden_imports(path: Path) -> None:
    """AST 取真实 import：禁 sqlite3 / pymysql / common / core / xtquant / eltdx。"""
    mods = _top_level_imports(path)
    for banned in ("sqlite3", "pymysql", "psycopg", "common", "core",
                   "xtquant", "xtdata", "eltdx"):
        assert banned not in mods, "%s 不得 import %s（实际: %s）" % (path.name, banned, sorted(mods))


def _code_calls(path: Path) -> str:
    """源码里所有调用的点分名（拼成一段文本，交给 contract 的静态扫描器）。"""
    def dotted(node: Any) -> str:
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return "\n".join(dotted(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call))


@pytest.mark.parametrize("path", [BACKEND_PY, RENDER_PY, SOURCE_PY, PAPER_PY],
                         ids=["backend", "render", "source", "paper"])
def test_no_direct_db_or_quote_usage(path: Path) -> None:
    calls = _code_calls(path)
    assert find_forbidden_db_usage(calls) == []
    assert find_forbidden_quote_usage(calls) == []


# 写盘动作黑名单：与 tools/cutover_gate.py 的口径同源（open 写模式按上下文判）
_WRITE_TOKENS = (".write_text(", ".write_bytes(", ".mkdir(", ".touch(", ".unlink(",
                 ".rmdir(", "os.remove(", "os.unlink(", "os.rename(", "os.replace(",
                 "os.makedirs(", "shutil.", "json.dump(", ".to_sql(", ".to_csv(",
                 "subprocess.", "os.system(", "Popen(")


@pytest.mark.parametrize("path", [BACKEND_PY, RENDER_PY, SOURCE_PY, PAPER_PY],
                         ids=["backend", "render", "source", "paper"])
def test_backend_source_has_no_write_code(path: Path) -> None:
    """插件代码零写盘（`md2html.py` 例外，见下条用例的说明）。"""
    text = path.read_text(encoding="utf-8")
    hits = [tok for tok in _WRITE_TOKENS if tok in text]
    assert hits == [], "%s 出现写盘动作: %s" % (path.name, hits)
    # open 只允许读模式（"r" / 无 mode / encoding=...）
    import re
    assert not re.search(r"open\s*\([^)]{0,240}?,\s*[\"'](?:w|a|wb|ab|x|xb)[\"']", text), path.name


def test_md2html_frozen_interfaces_no_cli() -> None:
    """`md2html.py` 保留冻结接口；CLI `main()` 已剥离（插件不得有写盘代码）。"""
    md2html_py = PLUGIN_DIR / "md2html.py"
    text = md2html_py.read_text(encoding="utf-8")
    for frozen in ("def extract_title_and_meta(", "def colorize_td_symbols(",
                   "def apply_color_marks("):
        assert frozen in text, frozen
    # 插件侧不 import 也不调用 main（写盘代码只在 CLI 分支里）
    for path in (BACKEND_PY, RENDER_PY, SOURCE_PY, PAPER_PY):
        assert "main()" not in path.read_text(encoding="utf-8"), path.name


# ---------------------------------------------------------------- 10. 复盘图表档（4.6）
# 2026-11-05 契约扩展（任务书：图表视图首屏要有「今日三问 / 假设台账 / 六格摘要」）：
#   新增 questions / ledger / six 三个顶层键；gaps_note = 无缺口时的显式说明（gaps 仍为数组）。
REVIEW_CHART_KEYS = {
    "ok", "meta", "head", "levels", "index", "amv", "breadth", "temp",
    "turnover", "sector", "ladder", "anomaly", "sentiment", "news",
    "positions", "gate", "tomorrow", "scenarios", "gaps", "gaps_note", "md_quotes",
    "questions", "ledger", "six",
    "file", "mtime_ns", "cache",
}


def test_review_chart_real() -> None:
    """真实 `/api/review_chart.json` → 200，键集合与旧端一致，ok=True。"""
    body = TestClient(app).get("/api/review_chart.json").json()
    assert set(body) == REVIEW_CHART_KEYS
    assert body.get("ok") is True
    assert body.get("meta", {}).get("trade_date")
    assert body.get("ladder") is not None


def test_review_chart_date_404() -> None:
    assert TestClient(app).get("/api/review_chart.json?date=1900-01-01").status_code == 404


# ---------------------------------------------------------------- 11. 纸面单测（4.8）
# 口径照搬旧端 engine/tests/test_paper_sector_merge.py + test_review_api.py::PaperScopeCssTest；
# 全部只调纯函数（作用域化 / 合并表 / 管线），不读写生产目录、不启服务。

SCOPE = "#panel-review .rv-paper"


def _tbl(rows: int, cols: int, head=None) -> str:
    """构造与复盘 2.1 同构的 table-wrapper HTML 段。"""
    head = head or ["<th>h%d</th>" % i for i in range(cols)]
    trs = "\n".join(
        "<tr>" + "".join('<td class="lbl">r%dc%d</td>' % (r, c) for c in range(cols)) + "</tr>"
        for r in range(rows))
    return ('<div class="table-wrapper"><table>\n<thead>\n<tr>'
            + "".join(head) + "</tr>\n</thead>\n<tbody>\n" + trs + "\n</tbody>\n</table></div>")


def _doc(front_rows=10, back_rows=10, front_cols=5, back_cols=5, with_captions=True) -> str:
    cap1 = "<p><strong>领涨 TOP10（价格 pct）</strong></p>\n" if with_captions else ""
    cap2 = "<p><strong>领跌 BOTTOM10</strong></p>\n" if with_captions else ""
    return ("<h3>2.1 前排 / 退潮（L2 行业）</h3>\n"
            + cap1 + _tbl(front_rows, front_cols) + "\n"
            + cap2 + _tbl(back_rows, back_cols) + "\n"
            + '<p><strong>AMV 双轨 state = 多 的 L2</strong>：电池、小家电。</p>\n'
            + '<div class="quote"><p>结构性特征。</p></div>')


# ---- 11.1 CSS 作用域化六件套


def test_scope_css_rewrites_root_body_star() -> None:
    """`:root`/`body`/`*` 必须被重写成作用域形式，不残留裸选择器。"""
    raw = ":root{--x:1}\nbody{color:red}\n*{margin:0}\n.temp{color:blue}\n"
    out = review_paper._scope_css(raw, SCOPE)
    assert SCOPE + "{--x:1}" in out
    assert SCOPE + "{color:red}" in out
    assert SCOPE + " *{margin:0}" in out
    assert SCOPE + " .temp{color:blue}" in out
    assert review_paper._css_escaped_scope(out, SCOPE) is None


def test_scope_css_handles_media_and_drops_print() -> None:
    """@media(max-width) 内规则递归作用域化；@media print / @page 丢弃。"""
    raw = ("@media (max-width:760px){.brief{grid-template-columns:1fr}}\n"
           "@media print{.sheet{max-width:none}}\n@page{size:A4}\n")
    out = review_paper._scope_css(raw, SCOPE)
    assert "@media (max-width:760px){" in out
    assert SCOPE + " .brief" in out
    assert "print" not in out
    assert "@page" not in out
    assert review_paper._css_escaped_scope(out, SCOPE) is None


def test_scope_selectors_comma_and_paren_safe() -> None:
    """逗号多选择器逐条作用域；`:not(...)` 内逗号不误切。"""
    assert review_paper._scope_selectors(".a, .b:not(.c), .d", "#S") == \
        "#S .a, #S .b:not(.c), #S .d"


def test_css_escaped_scope_detects_bare_rule() -> None:
    """逃逸自检：裸 `body{` 必须被判为逃逸（防御层不被绕过）。"""
    assert review_paper._css_escaped_scope("body{color:red}", "#S") is not None


def test_strip_css_comments_keeps_string_literals() -> None:
    """注释被剥，但字符串字面量里的 /* 必须保留（旧端口径）。"""
    css = '/* 说明 */ .a{ content: "/* keep */"; }\n.b{x:1}'
    out = review_paper._strip_css_comments(css)
    assert "说明" not in out
    assert '"/* keep */"' in out
    assert ".b{x:1}" in out


def test_iter_css_rules_nested_and_string_safe() -> None:
    """顶层规则切分：@media 体整段交出、字符串内的花括号不参与配平。"""
    css = '@media (max-width:9px){.a{x:1}}\n.b{ content: "}"; }\n'
    rules = review_paper._iter_css_rules(css)
    assert [p.strip() for p, _ in rules] == ["@media (max-width:9px)", ".b"]
    assert rules[1][1].strip() == 'content: "}";'


def test_split_selectors_paren_safe() -> None:
    assert review_paper._split_selectors(".a, .b:not(.c,.d), .e") == \
        [".a", ".b:not(.c,.d)", ".e"]


def test_paper_shield_scoped_and_unified_width() -> None:
    """垫片：统一栏宽规则存在，且本身不逃逸作用域（自检口径与 _paper_scope_css 一致）。"""
    css = review_paper._PAPER_SHIELD
    assert "--rv-col" in css
    assert ".sec.sec--major" in css
    assert "rv-tb-merge" in css
    assert "tb-divide" in css
    assert review_paper._css_escaped_scope(css, SCOPE) is None


def test_paper_scope_css_real_source_is_scoped() -> None:
    """对 report.css 唯一源实派生：解析式自检全部规则带作用域，且确实做过作用域化。"""
    if not REPORT_CSS.exists():
        pytest.skip("report.css 不可用（环境缺唯一源）")
    review_paper.reset_css_cache()
    css = review_paper._paper_scope_css()
    assert css, "report.css 存在时派生结果不得为空"
    assert review_paper._css_escaped_scope(css, SCOPE) is None
    assert css.count(SCOPE) > 0
    assert "--rv-col" in css                       # 垫片已拼接
    assert Path(review_paper.REPORT_CSS_PATH) == REPORT_CSS   # 固定指向唯一源，不复制进插件


def test_paper_scope_css_cache_by_mtime_size(tmp_path: Path, monkeypatch) -> None:
    """派生缓存口径：(mtime_ns, size) 未变 → 复用；变更 → 重算。"""
    css_file = tmp_path / "report.css"
    css_file.write_text("body{color:red}\n", encoding="utf-8")
    monkeypatch.setattr(review_paper, "REPORT_CSS_PATH", str(css_file))
    review_paper.reset_css_cache()
    first = review_paper._paper_scope_css()
    assert SCOPE + "{color:red}" in first
    second = review_paper._paper_scope_css()
    assert second is first                          # 命中缓存（同一对象）
    css_file.write_text("body{color:red}\n.tmp{margin:0}\n", encoding="utf-8")
    third = review_paper._paper_scope_css()
    assert SCOPE + " .tmp{margin:0}" in third       # size 变化 → 重算


def test_paper_scope_css_missing_source_returns_empty(tmp_path: Path, monkeypatch) -> None:
    """唯一源缺失 → ""（调用方据此抛错降级，不静默给半成品纸面）。"""
    monkeypatch.setattr(review_paper, "REPORT_CSS_PATH", str(tmp_path / "nope.css"))
    review_paper.reset_css_cache()
    assert review_paper._paper_scope_css() == ""


# ---- 11.2 前排/退潮合并表


def test_merge_sector_front_back_happy() -> None:
    out = review_paper._merge_sector_front_back(_doc())
    assert 'class="rv-tb-merge"' in out
    assert out.count('<div class="table-wrapper">') == 1     # 两表被替换成一张
    assert out.count("<table>") == 0                         # 原裸 table 全没了
    assert '<tr class="tb-grp">' in out
    assert "前排·领涨 TOP10" in out and "退潮·领跌 BOTTOM10" in out
    assert 'colspan="5"' in out
    # 双栏体：10 行 × (左 5 + 右 5) = 每行 10 td
    import re
    body = re.search(r"<tbody>([\s\S]*?)</tbody>", out).group(1)
    trs = re.findall(r"<tr>([\s\S]*?)</tr>", body)
    assert len(trs) == 10
    for tr in trs:
        assert len(re.findall(r"<td", tr)) == 10
    # 中缝：右半第一列带 tb-divide，左半第一列不带
    tds = re.findall(r"<td[^>]*>", trs[0])
    assert "tb-divide" in tds[5]
    assert "tb-divide" not in tds[0]
    assert "tb-divide" not in tds[9]
    # 两个旧 caption 被吸收，不残留；其它内容原样
    assert "<p><strong>领涨 TOP10（价格 pct）</strong></p>" not in out
    assert "<p><strong>领跌 BOTTOM10</strong></p>" not in out
    assert "AMV 双轨 state = 多 的 L2" in out
    assert '<div class="quote"><p>结构性特征。</p></div>' in out


def test_merge_sector_front_back_passthrough_guards() -> None:
    """防御分支原样返回：无锚文本 / 缺后表 / 列数不等 / 行数差过大 / td 数与表头不齐。"""
    html = "<p>无关内容</p>" + _tbl(3, 5)
    assert review_paper._merge_sector_front_back(html) == html

    no_back = ("<p><strong>领涨 TOP10（价格 pct）</strong></p>\n" + _tbl(10, 5)
               + "\n<p><strong>领跌 BOTTOM10</strong></p>\n")
    assert review_paper._merge_sector_front_back(no_back) == no_back

    col_mismatch = _doc(front_cols=5, back_cols=4)
    assert review_paper._merge_sector_front_back(col_mismatch) == col_mismatch

    row_gap = _doc(front_rows=10, back_rows=3)      # 差 7 > 3
    assert review_paper._merge_sector_front_back(row_gap) == row_gap

    uneven = ('<div class="table-wrapper"><table>\n<thead>\n<tr>'
              + "".join("<th>h%d</th>" % i for i in range(5)) + "</tr>\n</thead>\n<tbody>\n"
              + "<tr>" + "".join("<td>c%d</td>" % i for i in range(4)) + "</tr>\n"
              + "<tr>" + "".join("<td>c%d</td>" % i for i in range(5)) + "</tr>\n"
              + "</tbody>\n</table></div>")
    uneven_html = ("<p><strong>领涨 TOP10（价格 pct）</strong></p>\n" + uneven
                   + "\n<p><strong>领跌 BOTTOM10</strong></p>\n" + uneven)
    assert review_paper._merge_sector_front_back(uneven_html) == uneven_html


def test_merge_sector_front_back_small_gap_fills_placeholder() -> None:
    """单侧缺行（差 ≤3）→ 合并 + `<td>—</td>` 占位（不编造数据、不错位）。"""
    out = review_paper._merge_sector_front_back(_doc(front_rows=10, back_rows=8))
    assert 'class="rv-tb-merge"' in out
    assert "<td>—</td>" in out


# ---- 11.3 纸面管线 / 渲染回调


def test_paper_pipeline_masthead_doc_footer(tmp_path: Path) -> None:
    """完整管线：masthead 含 mh-band、正文含 section（十节节奏）、footer 为 colophon。"""
    md = ("# 日复盘 2026-09-16（周三 · 测试）\n\n> 数据时点：x\n\n"
          "## 一、板块\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    title, masthead, body, footer = review_paper._paper_pipeline(md, str(tmp_path))
    assert title
    assert "mh-band" in masthead
    assert "sec" in body
    assert "<main" not in body and "colophon" in footer


def test_paper_pipeline_inline_svg_keeps_figure_chart(tmp_path: Path) -> None:
    """纸面内联 SVG 必须保留 figure.chart 结构（§7.4），且不做 v6 的透明底改写。"""
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "swimlane.svg").write_text(
        '<svg width="10" height="10"><rect width="10" height="10" fill="#FFFFFF"/>'
        '<circle r="1"/></svg>', encoding="utf-8")
    md = ("# 日复盘 2026-09-16（周三）\n\n> 数据时点：x\n\n"
          "## 一、板块\n\n![泳道图](charts/swimlane.svg)\n\n![缺失图](charts/nope.svg)\n")
    _title, _mh, body, _foot = review_paper._paper_pipeline(md, str(tmp_path))
    assert '<figure class="chart">' in body
    assert "<svg" in body                               # 内联成功（figure.chart 内直接是 svg）
    assert 'src="charts/swimlane.svg"' not in body      # 已替换成 <svg>，不再留 img
    assert 'fill="#FFFFFF"' in body                     # 纸面保留原暖白底（v6 才改透明）
    assert 'src="charts/nope.svg"' in body              # 读不到的图原样保留，不编造


def test_paper_pipeline_strips_script(tmp_path: Path) -> None:
    md = ("# 日复盘 2026-09-16（周三）\n\n> 数据时点：x\n\n"
          "## 一、板块\n\n<script>alert(1)</script>\n")
    _title, _mh, body, _foot = review_paper._paper_pipeline(md, str(tmp_path))
    assert "<script" not in body and "alert(1)" not in body


def test_render_paper_payload_keys(tmp_path: Path) -> None:
    """render_paper 载荷键与旧端 _paper_review_renderer 一致（10 键），paper=True。"""
    if not REPORT_CSS.exists():
        pytest.skip("report.css 唯一源不可用")
    p = tmp_path / "日复盘 2026-10-23.md"
    p.write_text("# 日复盘 2026-10-23（周五）\n\n> 数据时点：收盘\n\n## 一、板块\n\n正文。\n",
                 encoding="utf-8")
    pick = review_source.ReviewPick(path=str(p), date="2026-10-23",
                                    mtime_ns=p.stat().st_mtime_ns, size=p.stat().st_size)
    out = review_paper.render_paper(pick)
    assert set(out) == {"date", "file", "title", "html", "masthead", "footer",
                        "css", "paper", "mtime_ns", "charts"}
    assert out["paper"] is True
    assert out["date"] == "2026-10-23" and out["file"] == p.name
    assert out["charts"] == {"swimlane": None, "track": None}
    assert out["mtime_ns"] == pick.mtime_ns
    assert out["css"].strip() and SCOPE in out["css"]


def test_render_paper_raises_on_missing_file(tmp_path: Path) -> None:
    """MD 读不到 → RuntimeError（由 render_cached 捕获降级，不穿透成裸异常）。"""
    pick = review_source.ReviewPick(path=str(tmp_path / "nope.md"), date="2026-10-23",
                                    mtime_ns=1, size=1)
    with pytest.raises(RuntimeError):
        review_paper.render_paper(pick)
