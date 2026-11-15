# -*- coding: utf-8 -*-
"""review 卡片前端静态检查（只读，不跑浏览器）。

守的是 docs/task_R3_concept_review.md §1/§3 与 docs/review_contract.md §卡片：
  · 零 import、零裸 fetch、零写盘、零子进程、不写全局
  · 端点只用绝对 /api/ 路径（review 插件 mount=root，与旧端同名）
  · 三视图（图表 / 纸面 / 文档）+ 每视图保留旧端自动降级链（图表→纸面→v6）
  · 纸面：root.id = panel-review（服务端派生 CSS 的作用域），css 注入 + 卸载移除
  · v6：blockquote 成对转换（⚠→warning，💡/📊→highlight），不做全局 </blockquote> 替换
  · 档位沿用 localStorage panel_rv_mode；简报/完整两档
  · 不声明 refreshEvery（复盘 MD 日频），保留 refresh() 占位
"""
import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND_JS = PLUGIN_DIR / "frontend.js"
MANIFEST = PLUGIN_DIR / "plugin.json"
# 旧页（复盘 Tab 行为与样式的唯一基准）：engine/web 零拷贝挂载于 8810 的 /legacy/
LEGACY_HTML = PLUGIN_DIR.parents[4] / "engine" / "web" / "index.html"

BANNED = (
    ".write_text(", ".write_bytes(", ".mkdir(", ".touch(", ".unlink(", ".rmdir(", ".symlink_to(",
    "os.remove(", "os.unlink(", "os.rename(", "os.replace(", "os.makedirs(", "shutil.rmtree(",
    "shutil.move(", "shutil.copy", "json.dump(", ".to_sql(", ".to_csv(", ".to_parquet(",
    "INSERT INTO", "INSERT OR", "UPDATE ", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
    "requests.post", "requests.put", "requests.delete", "requests.patch",
    "urllib.request.urlopen", "httpx.post", "httpx.put", "subprocess.", "os.system(", "os.popen(",
    "Popen(",
)
OPEN_WRITE_RE = re.compile(r"""open\s*\([^)]{0,240}?,\s*["'](?:w|a|wb|ab|x|xb)["']""")

OLD_VIEWS = [["chart", "图表"], ["paper", "纸面"], ["doc", "文档"]]
# 2026-11-15（标注 N93 主人）：「图表」视图有效信息太少，已从 UI 删掉。
#   渲染函数与降级链代码保留（无入口不执行），只校当前上屏的三个视图。
CUR_VIEWS = [["paper", "纸面"], ["doc", "文档"], ["six", "六格"]]


def _src() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _css_lines() -> list:
    """取 CSS 数组里的字符串行（供样式口径断言）。"""
    src = _src()
    start = src.index("const CSS = [")
    end = src.index("].join('\\n');", start)
    out = []
    for line in src[start:end].split("\n"):
        s = line.strip()
        if s.startswith('"') and s.endswith('",'):
            out.append(json.loads(s[:-1]))
        elif s.startswith("'") and s.endswith("',"):
            out.append(s[1:-2])
    return out


def _strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _parse_css_rules(css: str) -> list:
    """→ [(ctx, selector, decls)]；ctx = 外层 @media 头（无则空串），嵌套按一层算。"""
    out = []
    stack = []
    buf = ""
    for ch in css:
        if ch == "{":
            stack.append(buf.strip())
            buf = ""
        elif ch == "}":
            body = buf.strip()
            buf = ""
            if stack:
                head = stack.pop()
                if not head.startswith("@"):
                    out.append((" ".join(stack), head, body))
        else:
            buf += ch
    return out


def _norm(s: str) -> str:
    return " ".join(s.split())


def _scoped(selector: str) -> bool:
    """旧页里属于复盘作用域的选择器（与搬入时的抽取口径一致）。"""
    parts = [_norm(p) for p in selector.split(",")]
    return any(p.startswith("#panel-review") or p.startswith(".rv-") or " .rv-" in p
               or p.startswith("tr.todo-done") for p in parts)


# ---------------------------------------------------------------- 依赖 / 写盘


def test_zero_imports() -> None:
    assert not re.search(r"^\s*import\s", _src(), re.M), "frontend.js 不应有任何 import"


def test_no_write_or_subprocess_tokens() -> None:
    src = _src()
    hits = [t for t in BANNED if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src)


def test_no_global_writes() -> None:
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _src())


def test_no_bare_fetch() -> None:
    assert "fetch(" not in _src(), "应统一走 ctx.api.get，不直接 fetch"


def test_only_get_requests() -> None:
    src = _src()
    calls = re.findall(r"""api\.(get|post)\(\s*['"]([^'"]+)['"]""", src)
    assert calls, "找不到任何 api 调用"
    for verb, path in calls:
        assert verb == "get", "本卡只读：%s %s" % (verb, path)
        assert path.startswith("/api/"), "root 插件端点必须用绝对 /api/ 路径：%s" % path


# ---------------------------------------------------------------- 三视图 + 降级链


def test_views_match_task() -> None:
    m = re.search(r"const VIEWS = \[([\s\S]*?)\];", _src())
    assert m, "找不到 VIEWS 定义"
    got = re.findall(r"\['(\w+)', '([^']+)'\]", m.group(1))
    # 2026-11-03：在三视图后追加第四视图「六格」（规格：六格卡片式版式_20261103）
    # 2026-11-15：按标注 N93 删「图表」——现为纸面 / 文档 / 六格。
    assert [list(x) for x in got] == CUR_VIEWS, "视图与任务书不一致：%s" % got


def test_degrade_chain_matches_old() -> None:
    """旧端降级链：图表 → 纸面 → v6；文档视图只走 v6。"""
    src = _src()
    m = re.search(r"const CHAIN = \{([\s\S]*?)\};", src)
    assert m, "找不到降级链定义"
    body = m.group(1)
    assert re.search(r"chart:\s*\['chart', 'paper', 'doc'\]", body), "图表视图降级链不完整"
    assert re.search(r"paper:\s*\['paper', 'doc'\]", body), "纸面视图降级链不完整"
    assert re.search(r"doc:\s*\['doc'\]", body), "文档视图只走 v6"


def test_degrade_is_visible_not_silent() -> None:
    src = _src()
    assert "已自动回退到「" in src, "回退需在卡上留一行提示（不静默）"
    assert "复盘渲染失败" in src, "全链失败需显式错误行"


def test_chart_requires_ok_head_meta() -> None:
    """旧 7822：成功 = ok:true + head + meta，任一不满足即回退。"""
    src = _src()
    assert "j.ok === true && j.head && j.meta" in src


def test_chart_lib_is_legacy_static() -> None:
    src = _src()
    assert "RVC_SRC = '/legacy/review_chart.js'" in src, "组件库复用旧仓 review_chart.js（同源静态）"
    assert "document.createElement('script')" in src
    assert "window.RVC" in src
    assert "rvc.render(d, mount, S.mode)" in src, "渲染入口必须是 RVC.render(D, mount, mode)"


def test_chart_lib_load_has_timeout() -> None:
    """R3 实测：脚本既不 load 也不 error 时（挂起/被拦），必须超时降级，否则卡片永久空白。"""
    src = _src()
    assert "RVC_TIMEOUT_MS" in src
    assert "setTimeout(done, RVC_TIMEOUT_MS)" in src
    m = re.search(r"function loadRvc\(\) \{([\s\S]*?)\n\}", src)
    assert m
    body = m.group(1)
    assert "settled" in body, "需幂等闩锁，防 load/error/超时三重 settle"
    assert "clearTimeout(timer)" in body


def test_paper_requires_paper_true_and_not_degraded() -> None:
    """旧 7868：纸面成功 = paper:true + 未降级 + 有正文。"""
    src = _src()
    assert "j.paper === true && !j.degraded && j.html" in src


def test_paper_root_id_is_panel_review() -> None:
    """服务端派生 CSS 只作用于 `#panel-review .rv-paper`：卡内套一层同名容器承接作用域
    （不改 shell 的 host 元素 —— 宿主属性归 shell）。"""
    src = _src()
    assert "wrap.id = 'panel-review'" in src
    assert "root.id = 'panel-review'" not in src, "不得改 shell 的 host 元素 id"


def test_paper_css_injected_and_removed() -> None:
    src = _src()
    assert "review-paper-css" in src
    assert "injectPaperCss" in src
    assert "removeChild(paper)" in src, "卸载时须移除注入的纸面 CSS"


def test_paper_structure_matches_old() -> None:
    """旧 7886：.rv-paper > .sheet > masthead + main.doc + footer。"""
    src = _src()
    assert 'class="rv-paper"' in src
    assert 'class="sheet"' in src
    assert '<main class="doc">' in src
    assert "p.masthead" in src and "p.footer" in src


def test_v6_blockquote_pairing() -> None:
    """旧 7897：⚠→warning、💡/📊→highlight；成对替换，不做全局闭合标签替换。"""
    src = _src()
    m = re.search(r"function convertBlockquotes\(html\) \{([\s\S]*?)\n\}", src)
    assert m, "找不到 convertBlockquotes"
    body = m.group(1)
    assert "⚠|💡|📊" in body, "三种标记都要认"
    assert "mark === '⚠' ? 'warning' : 'highlight'" in body
    # 只允许一处 replace（整块成对匹配）；禁止单独对闭合标签做全局替换
    assert body.count(".replace(") == 1, "不得多段替换（旧端孤立 </div> 就是这么来的）"
    assert "([\\s\\S]*?)<\\/blockquote>" in body, "必须非贪婪整块匹配"


def test_v6_theme_param_and_observer() -> None:
    src = _src()
    assert "api.get('/api/review.md?theme='" in src
    assert "MutationObserver" in src, "主题变化需重取 v6（旧端同口径）"
    assert "attributeFilter: ['data-theme']" in src


def test_v6_degraded_banner() -> None:
    src = _src()
    assert "复盘渲染降级" in src, "degraded 需在正文上方给警示块"
    assert "上一份成功内容" in src


def test_v6_missing_md_card() -> None:
    """旧 7904：MD 缺失时给「复盘 MD 缺失」卡 + 管线说明，不白屏。"""
    src = _src()
    assert "复盘 MD 缺失" in src
    assert "夏天-activity/每日复盘/" in src


def test_topline_fields_match_old() -> None:
    """旧 7938 顶行：复盘文件 / 归属交易日 / 最后更新时间 / 渲染 / 缓存。"""
    src = _src()
    for field in ("复盘文件: ", "归属交易日: ", "最后更新时间: ", "渲染: ", "缓存: "):
        assert field in src, "顶行缺字段 %s" % field
    assert "fmtMtime(d.mtime_ns)" in src or "fmtMtime(" in src


def test_fmt_mtime_matches_old() -> None:
    src = _src()
    m = re.search(r"function fmtMtime\(ns\) \{([\s\S]*?)\n\}", src)
    assert m, "找不到 fmtMtime"
    body = m.group(1)
    assert "t / 1e6" in body, "ns → ms 换算"
    assert "padStart(2, '0')" in body
    assert "return null" in body, "非法/缺值必须返回 null（调用方显示不可用）"


# ---------------------------------------------------------------- 档位


def test_mode_uses_old_localstorage_key() -> None:
    src = _src()
    assert "LS_MODE = 'panel_rv_mode'" in src, "档位键与旧页同名，用户偏好无损"
    assert "data-rvmode=" in src
    assert "简报" in src and "完整" in src
    assert "全窗明细" in src, "档位说明文案照旧"


def test_mode_bar_only_in_chart_view() -> None:
    src = _src()
    m = re.search(r"const renderHead = \(\) => \{([\s\S]*?)\n      \};", src)
    assert m, "找不到 renderHead"
    body = m.group(1)
    assert "S.view !== 'chart'" in body, "档位切换只对图表视图显示"


# ---------------------------------------------------------------- 样式口径


def test_scoped_css_mirrors_legacy() -> None:
    """搬入的旧页 CSS 必须保留 #panel-review 作用域（不得改成全局选择器污染其它卡）。"""
    lines = _css_lines()
    text = "\n".join(lines)
    assert "#panel-review .rv-md" in text, "v6 正文样式缺失"
    assert "#panel-review {" in text, "复盘变量块缺失"
    assert ".rv-sec" in text and ".rv-sec-hd" in text, "图表模板段落样式缺失"
    assert "#panel-review .rv-root section.rv-sec { display:block; }" in text, \
        "面板兼容垫片（section 复位可见）必须保留"
    for line in lines:
        s = line.strip()
        if not s or s.startswith("/*") or "{" not in s:
            continue
        sel = s.split("{")[0].strip()
        for part in sel.split(","):
            p = " ".join(part.split())
            if not p:
                continue
            assert p.startswith((".rv-", "#panel-review", ":root", "html[data-theme", "tr.todo-done",
                                 "@media")), "非作用域选择器会污染其它卡：%s" % p[:80]


def test_scoped_css_rule_headers_match_legacy() -> None:
    """搬入的旧页复盘段规则头集合必须与旧页**等值**（防再漂移，审计 R35 P1-1）。

    旧页 `engine/web/index.html` 第一个内联 <style> 里，作用域命中 `#panel-review` /
    `.rv-*` / `tr.todo-done` 的规则（含 @media 内的嵌套规则，按「外层条件 + 选择器」
    归一）：
      · 每一条在卡内都要有同名规则头（含 3 条窄屏 @media，曾经整段丢失）；
      · 卡内不得出现重复规则头（曾经 A/B 两段把 v6 正文块整段注入了两遍）。
    只比规则头与声明归一文本，不比注释与空白。
    """
    legacy = LEGACY_HTML.read_text(encoding="utf-8")
    block = re.search(r"<style[^>]*>(.*?)</style>", legacy, re.S)
    assert block, "旧页内联 <style> 找不到"
    legacy_rules = _parse_css_rules(_strip_css_comments(block.group(1)))
    want = {}
    for ctx, sel, body in legacy_rules:
        if not (_scoped(sel)):
            continue
        want[(_norm(ctx), _norm(sel))] = _norm(body)
    assert len(want) >= 300, "旧页复盘段规则数异常（口径基准变了？）：%d" % len(want)

    card_rules = _parse_css_rules(_strip_css_comments("\n".join(_css_lines())))
    got = {}
    for ctx, sel, body in card_rules:
        got.setdefault((_norm(ctx), _norm(sel)), []).append(_norm(body))

    dups = sorted(k for k, v in got.items() if len(v) > 1)
    assert not dups, "卡内规则头重复注入（%d 条）：%s" % (len(dups), dups[:6])

    missing = sorted(k for k in want if k not in got)
    assert not missing, "旧页规则未搬入卡内（%d 条）：%s" % (len(missing), missing[:6])

    differ = sorted(k for k in want if got.get(k) and got[k][0] != want[k])
    assert not differ, "规则声明与旧页不一致（%d 条）：%s" % (len(differ), differ[:6])

    # 三条窄屏 @media 显式锁死（本次缺陷的直接症状）
    for key in (("@media (max-width:760px)", "#panel-review .rv-md"),
                ("@media (max-width:760px)", "#panel-review .rv-topline"),
                ("@media (max-width:900px)", ".rv-cols-wide")):
        assert key in got, "窄屏规则丢失：%s" % (key,)


def test_own_spacer_not_colliding_with_legacy_gap() -> None:
    """卡自有卡头 spacer 不得用旧页图表模板的 `.rv-gap`（审计 R35 P2-1 撞车埋雷）。

    旧页 `.rv-gap` 是告警条（flex 布局），卡头 spacer 用 `margin-left:auto`；同名会让
    spacer 元素同时命中两条规则，旧页改样式即静默串。自有类统一用 `.rv-sp-gap`。
    """
    lines = _css_lines()
    text = "\n".join(lines)
    assert ".rv-sp-gap{margin-left:auto}" in text, "卡头 spacer 应用自有类 .rv-sp-gap"
    assert ".rv-gap{margin-left:auto}" not in text, "不得再给旧页 .rv-gap 叠 spacer 规则"
    src = _src()
    assert "div('rv-sp-gap')" in src, "卡头 spacer 元素类名未同步"
    assert "div('rv-gap')" not in src


def test_own_skeleton_uses_tokens() -> None:
    text = "\n".join(_css_lines())
    for token in ("var(--accent)", "var(--card2)", "var(--dim)", "var(--radius-pill)"):
        assert token in text, "自有骨架缺主题 token %s" % token


def test_style_injected_once() -> None:
    src = _src()
    assert "review-card-css" in src
    assert "document.getElementById(STYLE_ID)" in src


# ---------------------------------------------------------------- 刷新纪律 / 生命周期


def test_no_refresh_every_declared() -> None:
    src = _src()
    assert not re.search(r"^\s*refreshEvery\s*:", src, re.M)
    assert "async refresh()" in src


def test_refresh_is_guarded() -> None:
    src = _src()
    m = re.search(r"async refresh\(\)\s*\{([\s\S]*?)\n    \},", src)
    assert m
    body = m.group(1)
    assert "S.alive" in body and "S.loading" in body


def test_no_flicker_set_html_cache() -> None:
    assert re.search(r"if \(S\.html\[key\] === html\) return;", _src())


def test_unmount_cleans_up() -> None:
    src = _src()
    m = re.search(r"async unmount\(\)\s*\{([\s\S]*?)\n    \},", src)
    assert m
    body = m.group(1)
    assert "S.alive = false" in body
    assert "themeObs.disconnect()" in body, "卸载须断开主题观察"
    assert "this._rv = null" in body


def test_event_delegation_not_inline_onclick() -> None:
    src = _src()
    assert "wrap.addEventListener('click'" in src
    assert "onclick=" not in src


# ---------------------------------------------------------------- manifest


def test_manifest_version_bumped() -> None:
    data = _manifest()
    # 0.2.0 → 0.3.x：第四视图「六格」（2026-11-03）
    assert data["version"].startswith("0.3.")
    assert data["frontend"]["module"] == "frontend.js"
    cards = data["frontend"]["cards"]
    assert [c["id"] for c in cards] == ["review.main"]
    assert cards[0]["slot"] == "main"
    assert cards[0]["title"] == "每日复盘"


def test_manifest_backend_unchanged() -> None:
    data = _manifest()
    assert data["backend"]["mount"] == "root", "root 挂载才能保住 /api/review.md 旧路径"
    assert data["backend"]["entry"] == "register"
    assert data["data"]["text"] == ["daily_review"]
    assert data["data"]["write"] == []


def test_frontend_card_matches_manifest() -> None:
    src = _src()
    assert "export default" in src
    assert re.search(r"id:\s*'review\.main'", src)
    assert "mount(" in src and "unmount(" in src and "refresh(" in src
