# -*- coding: utf-8 -*-
"""review/paper.py · 复盘纸面管线（旧 8800 api_review_md(paper=1) 的插件侧移植）

职责
    把一份被选中的复盘 Markdown 走 **report-builder v2 完整管线** 渲染成纸面载荷：
    报头（masthead）/ 速览带 / 十节节奏 / 涨跌芯片 / 暖白纸面，并派生一份**作用域版**
    report.css（只在 `#panel-review .rv-paper` 内生效，不污染面板其它 Tab）。

迁移说明（任务书 docs/task_paper_4_8.md，2026-10-24）
    本文件是旧仓 engine/server.py 纸面段的插件侧移植，**逐行照抄**、只改依赖注入：
    · CSS 作用域化六件套（_strip_css_comments / _iter_css_rules / _split_selectors /
      _scope_selectors / _scope_css / _css_escaped_scope）—— 逐字保留（旧端 1391-1538）；
    · _paper_scope_css 的 CSS 路径：旧端从 md2html 模块同目录推导，本版**固定**指向
      ~/.hanako/skills/report-builder/report.css（唯一源，只读、不复制进插件）。
      缓存口径不变：按 (mtime_ns, size) 派生缓存，变更即重算；
    · md2html 不再动态 import skill 目录，改用同目录冻结副本（render.py::_md2html()
      已按文件路径加载 md2html.py，此处复用，不重复造轮子）。

降级语义（与旧端一致，只在一处实现）
    · 渲染异常（MD 读不到 / md2html 不可用 / 纸面管线未产出 masthead+doc / CSS 派生失败）
      → **抛 RuntimeError**，由 source.render_cached 统一捕获：有历史成功内容就回退并标
      degraded=True，没有就返回 html=None；绝不裸 500。
    · 「css is None 且未 degraded」的静默伪成功防御层在 backend.py 的 paper 分支里
      （旧端 server.py:1907-1912 同款），本文件只负责如实产出/缺失 css 字段。

数据源与口径
    · MD：source.pick_latest 选中的唯一事实源（与 v6 路径同一份文件）。
    · CSS：~/.hanako/skills/report-builder/report.css（唯一源，只读）。
    · 图表：纸面用 MD 内联 SVG（charts/*.svg，白底深字），保留 figure.chart 结构；
      面板深色图两键置 None（与旧端 _paper_review_renderer 同）。
    · 展示层变换：复盘 2.1「前排/退潮」两表 → 单表双栏（_merge_sector_front_back），
      只改已生成 HTML，不动 MD 内容源、不动 report.css。

契约
    零写盘、无 sqlite3 / xtquant / common / core import、GET-only（tools/cutover_gate.py
    静态扫描口径）；只 open(..., encoding="utf-8") 读 MD 与 CSS。

上下游
    上游：app/plugins/review/backend.py::/api/review.md?paper=1 → source.render_cached
          （缓存维度 theme="paper"，与 v6 主题绝不互污染）。
    下游：旧前端 engine/web/index.html 复盘 Tab（.rv-paper 容器承载 masthead + 正文 + 派生 CSS）。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys
import hashlib

# report.css 唯一源（决策 1：不复制进插件目录，与旧端读同一份文件）
REPORT_CSS_PATH = os.path.join(os.path.expanduser("~"), ".hanako", "skills",
                               "report-builder", "report.css")

_PAPER_SCOPE = "#panel-review .rv-paper"

# 面板兼容垫片（自动生成，非 report.css 副本；只在纸面作用域内生效）
#   ① 面板全局 `header{display:flex}` 会命中纸面 <header class="masthead"> → 复位块级
#   ② 面板全局 `section{display:none}`（Tab 切换机制）会命中纸面 <section class="sec"> → 复位可见
#   ③ report.css 只有 `figure.chart img{...}`，内联 SVG 后无 img → 补 figure.chart svg
#      （流式 width:100% + 可读下限 min-width:var(--chart-min)，与 img 规则同口径）
#   ④ 2026-10-10 主人反馈「元素长度参差不齐」→ 面板侧统一正文栏宽：
#      report.css 原版 正文限 --read(46em)、表格通栏、.sec--major margin:-22px → 三套右边界。
#      面板只改展示层（不动 report.css 唯一源）：定一栏宽 --rv-col，正文/表格/卡片/引用块
#      全按同宽同左边缘对齐；.sec--major 去外扩（背景带不再比内容宽）。--rv-col 可改这一处。
#      另补 前排/退潮 合并表的分隔样式（见 _merge_sector_front_back，服务端 HTML 变换）。
#   ⑤⑥⑦ 2026-11-08 主人指出「结论带被埋 + 三段宽度对不齐」后补：结论带升第一视觉层、
#      整纸定宽居中（三段同宽）、文首元信息降第三层。详见各节注释。
_PAPER_SHIELD = (
    "\n/* ===== 面板兼容垫片（自动生成，非 report.css 副本）===== */\n"
    "%(s)s header.masthead{ display:block; margin:0; }\n"
    "%(s)s section{ display:block; }\n"
    "%(s)s figure.chart svg{ display:block; width:100%%; height:auto; max-width:100%%;"
    " min-width:var(--chart-min); margin:0 auto; }\n"
    # ④ 统一栏宽：正文/表格/卡片/引用块/图表 同一 --rv-col，左右边缘对齐。
    #   尺子用 rem（=根字号，面板 data-fs 三档整体缩放）不用 em：em 按各元素自身字号换算
    #   （p 15px / blockquote 14px / .warning 13px…），同一条 max-width 得出 812~990 多种宽——
    #   正是「参差不齐」残留根因（2026-10-10 浏览器实测）。72rem≈1152px（md 档）；常规屏各元素
    #   同吃 100% 栏宽，超宽屏封顶 72rem。
    "%(s)s .doc{ --rv-col: min(100%%, 72rem); }\n"
    "%(s)s .doc p, %(s)s .doc li, %(s)s .doc dd,\n"
    "%(s)s .doc blockquote, %(s)s .doc .quote, %(s)s .doc .takeaway, %(s)s .doc .alert,\n"
    "%(s)s .doc .keycard, %(s)s .doc .warning, %(s)s .doc .highlight, %(s)s .doc .bi dd,\n"
    "%(s)s .doc h1, %(s)s .doc h2, %(s)s .doc h3, %(s)s .doc h4, %(s)s .doc hr, %(s)s .doc .sec-h,\n"
    "%(s)s .doc .table-wrapper, %(s)s .doc .tbl, %(s)s .doc figure.chart{\n"
    "  max-width: var(--rv-col); }\n"
    # .sec--major 去 margin:-22px 外扩，背景带与内容同宽；横向 padding 同步归零（与轻节同栏）
    "%(s)s .sec.sec--major{ margin: 0; padding: 26px 0 12px; }\n"
    # 前排/退潮 合并表：中缝分隔 + 双行表头（组行居中、字段行右缘对齐不变）
    "%(s)s table.rv-tb-merge{ min-width: 780px; font-size: 12.5px; }\n"
    "%(s)s table.rv-tb-merge .tb-divide{ border-left: 2px solid var(--rule-2); }\n"
    "%(s)s table.rv-tb-merge tr.tb-grp th{ text-align:center; font-size:12px; letter-spacing:.06em; }\n"
    "%(s)s table.rv-tb-merge th.tb-front{ color:#F2EFE9; background:var(--ink); }\n"
    "%(s)s table.rv-tb-merge th.tb-back{ color:#F2EFE9; background:#4A2E2C; }\n"
    "%(s)s table.rv-tb-merge td{ padding:5px 8px; line-height:1.45; }\n"
    # ⑤ 2026-11-08 主人口径：结论带（🎯/📌/🚨）是第一视觉层。report.css 里三者 15/14/14.5px
    #   与正文同号，即使分块生效也立不起来 → 垫片里抬字号（🎯≥18 / 📌≥15.5 / 🚨≥16，正文 15），
    #   左色条加粗到 5px、底色沿用原语义色（不另造色），并贴紧报头（首块上边距 18px）。
    "%(s)s .takeaway{ font-size:18px; line-height:1.76; padding:15px 20px;"
    " margin:0 0 12px; border-left-width:5px; }\n"
    "%(s)s .keycard{ font-size:15.5px; line-height:1.8; padding:13px 18px;"
    " margin:0 0 12px; border-left-width:5px; }\n"
    "%(s)s .alert{ font-size:16px; line-height:1.76; padding:13px 18px;"
    " margin:0 0 16px; border-left-width:5px; }\n"
    "%(s)s .doc > .takeaway:first-child{ margin-top:18px; }\n"
    # ⑥ 2026-11-08：三段同宽 —— 报头(.mh-band) / 速览带(.brief) / 正文(.doc) 各吃同一条纸宽。
    #   原先 .sheet 满宽、正文子元素卡在 72rem 且左对齐 → 宽屏右侧空一大截。现给整纸定宽并居中：
    #   纸宽 = 正文栏宽 72rem + 两侧页边距（--pad-x），三段内边距相同 → 左右边界像素级对齐。
    #   .bi dd 原受 --read(46em) 约束，速览带右侧也短一截，一并放开到满栏。
    "%(s)s .sheet{ max-width: calc(72rem + var(--pad-x) * 2);"
    " margin-left:auto; margin-right:auto; }\n"
    "%(s)s .brief .bi dd{ max-width: none; }\n"
    # ⑦ 文首元信息（数据时点/口径/降级项）收编进报头速览带末尾，第三层字号（≤12px 灰字）
    "%(s)s .brief .bi dt{ font-size:11px; color:var(--ink-3); }\n"
    "%(s)s .brief .bi dd{ font-size:11.8px; line-height:1.56; color:var(--ink-3); }\n"
    "%(s)s .brief-notes{ grid-column: 1 / -1; margin-top: 2px; padding-top: 8px;"
    " border-top: 1px dotted var(--rule-2); font-size: 11.5px; line-height: 1.62;"
    " color: var(--ink-3); }\n"
    "%(s)s .brief-notes p{ margin: 0; }\n"
    "%(s)s .brief-notes p + p{ margin-top: 4px; }\n"
    # 元信息块本身（md2html 定级的 .meta）也可能留在正文中段（非首屏引用）——同口径小灰字
    "%(s)s .meta{ font-size:11.8px; line-height:1.6; color:var(--ink-3); margin:10px 0; }\n"
    "%(s)s .meta p{ margin: 0; }\n"
) % {"s": _PAPER_SCOPE}


# ---------------------------------------------------------------- md2html 取用
_RENDER_MOD = None


def _render_module():
    """取兄弟模块 render（复用其 _md2html() 拿冻结副本，不重复实现加载逻辑）。

    为什么多级兜底：core 用 spec_from_file_location("_panel_v2_plugin_review_backend", ...)
    加载本插件，那种情况下本模块**没有父包**；测试里走 app.plugins.review.paper 包导入；
    backend 可能已经按路径加载过一份 render（名字 _panel_v2_review_render），优先复用，
    避免 md2html 被重复 exec 两次。
    """
    global _RENDER_MOD
    if _RENDER_MOD is not None:
        return _RENDER_MOD
    cached = sys.modules.get("_panel_v2_review_render")
    if cached is not None:
        _RENDER_MOD = cached
        return _RENDER_MOD
    if __package__:
        try:
            _RENDER_MOD = importlib.import_module(__package__ + ".render")
            return _RENDER_MOD
        except ImportError:
            pass
    try:
        _RENDER_MOD = importlib.import_module("app.plugins.review.render")
        return _RENDER_MOD
    except ImportError:
        pass
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render.py")
    spec = importlib.util.spec_from_file_location("_panel_v2_review_render", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法为 render.py 建立 import spec: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _RENDER_MOD = module
    return module


def _md2html():
    """冻结副本 md2html（convert_md_to_html 等）；加载失败 → RuntimeError（由降级兜底）。"""
    return _render_module()._md2html()


# ---------------------------------------------------------------- 一、CSS 作用域化
def _strip_css_comments(text):
    """去 CSS 注释（保留字符串字面量内的 /*）。prelude 带注释会让作用域前缀插到注释与选择器之间，
    虽是合法 CSS 但难读且易误判，故先剥。"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text[i:i + 2] == "/*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            q, j = c, i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _iter_css_rules(css):
    """遍历顶层 CSS 规则 → [(prelude, body)]（字符串/注释安全跳过）。@media 体由调用方递归。"""
    blocks, i, n, seg = [], 0, len(css), 0
    while i < n:
        c = css[i]
        if c == "/" and css[i:i + 2] == "/*":
            j = css.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            q, i = c, i + 1
            while i < n and css[i] != q:
                i += 2 if css[i] == "\\" else 1
            i += 1
            continue
        if c == "{":
            prelude = css[seg:i]
            depth, j = 1, i + 1
            while j < n and depth:
                ch = css[j]
                if ch in "\"'":
                    q, j = ch, j + 1
                    while j < n and css[j] != q:
                        j += 2 if css[j] == "\\" else 1
                    j += 1
                    continue
                if ch == "/" and css[j:j + 2] == "/*":
                    k = css.find("*/", j + 2)
                    j = n if k < 0 else k + 2
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                j += 1
            blocks.append((prelude, css[i + 1:j - 1]))
            i = seg = j
            continue
        if c == "}":
            i = seg = i + 1
            continue
        i += 1
    return blocks


def _split_selectors(sel_text):
    """按顶层逗号切分选择器列表（括号内逗号如 :not(a,b) 不切）。"""
    parts, depth, buf = [], 0, []
    for ch in sel_text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _scope_selectors(sel_text, scope):
    """选择器列表加作用域前缀。

    · :root / html / body → 作用域容器自身（变量与纸面底落在容器上，随子元素继承）
    · *                   → 作用域容器后代
    · 其它                → 逐条加 `scope ` 前缀
    逗号切分对括号（:not(...) / [attr]）免疫。
    """
    out = []
    for s in _split_selectors(sel_text):
        if s in (":root", "html", "body"):
            out.append(scope)
        elif s == "*":
            out.append(scope + " *")
        else:
            out.append(scope + " " + s)
    return ", ".join(out)


def _scope_css(css, scope):
    """把 report.css（唯一源）自动派生为作用域版 CSS（禁止手工复制第二份）。

    · 顶层规则逐条作用域化；@media(...) 递归作用域化后保留条件
    · @page / @media print 丢弃（面板内不导出 PDF）
    · 其它 at-rule（font-face/keyframes 等，本文件未使用）保守丢弃
    返回作用域后的 CSS 文本。
    """
    out = []
    for prelude, body in _iter_css_rules(css):
        p = _strip_css_comments(prelude).strip()
        if not p:
            continue
        if p.startswith("@page"):
            continue
        if p.startswith("@media"):
            if "print" in p.lower():
                continue
            out.append(p + "{" + _scope_css(body, scope) + "}")
            continue
        if p.startswith("@"):
            continue
        out.append(_scope_selectors(p, scope) + "{" + body + "}")
    return "\n".join(out)


def _css_escaped_scope(css, scope):
    """作用域自检（防御层）：顶层每条非 @ 规则的选择器都必须以 scope 开头。

    命中返回逃逸的选择器（供日志/降级），全部合规返回 None。
    @media 内的规则递归校验。用真实解析而非子串匹配（与编程规范 §4.8 同口径：
    子串判据会被注释/自身正确写法误伤）。
    """
    for prelude, body in _iter_css_rules(css):
        p = _strip_css_comments(prelude).strip()
        if not p:
            continue
        if p.startswith("@media"):
            bad = _css_escaped_scope(body, scope)
            if bad:
                return bad
            continue
        if p.startswith("@"):
            continue
        for sel in _split_selectors(p):
            if not sel.startswith(scope):
                return sel[:120]
    return None


_PAPER_CSS_CACHE = {"key": None, "css": None}


def _paper_scope_css():
    """派生（并缓存）纸面作用域 CSS。

    report.css 是唯一源（只读，固定路径 REPORT_CSS_PATH）；按 (mtime_ns, size, 垫片指纹) 缓存，
    变更即重算。垫片指纹一起进 key：2026-11-08 踩过——只改垫片、report.css 没动，缓存 key 不变
    就会一直吐旧 CSS（改了不生效，极易误判成“没改对”）。派生结果做机械自检：不得含未作用域的
    裸 :root{ / body{（否则返回 "" → 调用方抛错 → 降级回 v6）。
    返回 CSS 文本；md2html/report.css 缺失 → ""（调用方抛错降级）。
    """
    m = _md2html()
    if m is None:
        return ""
    css_path = REPORT_CSS_PATH
    try:
        st = os.stat(css_path)
    except OSError:
        return ""
    key = (st.st_mtime_ns, st.st_size,
           hashlib.md5(_PAPER_SHIELD.encode("utf-8")).hexdigest()[:12])
    if _PAPER_CSS_CACHE["key"] == key and _PAPER_CSS_CACHE["css"]:
        return _PAPER_CSS_CACHE["css"]
    try:
        with open(css_path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ""
    scoped = _scope_css(raw, _PAPER_SCOPE)
    # 机械自检（防御层）：任一条规则逃逸作用域 → 拒绝使用，走降级
    if _css_escaped_scope(scoped, _PAPER_SCOPE):
        return ""
    scoped = scoped + _PAPER_SHIELD
    _PAPER_CSS_CACHE.update(key=key, css=scoped)
    return scoped


def reset_css_cache():
    """清派生 CSS 缓存（测试用；生产不需要）。"""
    _PAPER_CSS_CACHE.update(key=None, css=None)


# ---------------------------------------------------------------- 二、纸面管线
def _inline_svg_paper(html, md_dir, max_bytes=2_000_000):
    """纸面模式内联 charts/*.svg：**保留** <figure class="chart"> 结构，直接以 <svg> 替换 <img>。

    与 v6 路径（render._inline_svg 包 div.rv-chart + 改透明底）刻意不同：纸面是白底纸面，
    图表 SVG 自带暖白底（#FAF8F4）原样保留即可读（与独立 HTML 产物同口径）。
    读不到/超限/非 SVG → 原样保留（不编造、不阻断正文）。
    """
    if not html:
        return html
    count = {"n": 0}

    def _repl(m):
        src = m.group(1)
        p = src if os.path.isabs(src) else os.path.join(md_dir, src)
        try:
            if not os.path.exists(p) or os.path.getsize(p) > max_bytes:
                return m.group(0)
            with open(p, encoding="utf-8") as f:
                svg = f.read()
        except OSError:
            return m.group(0)
        if "<svg" not in svg:
            return m.group(0)
        count["n"] += 1
        return svg

    return re.sub(r'<img[^>]*src="([^"]+\.svg)"[^>]*>', _repl, html)


def _paper_pipeline(md_text, md_dir):
    """完整管线渲染（md2html.convert_md_to_html）→ (title, masthead_html, body_html, footer)。

    抽 <header class="masthead">…</header> + <main class="doc">…</main> +
    <footer class="colophon">…</footer> 三段；正文内联 charts/*.svg（保留 figure.chart）
    后剥脚本。任一步不齐 → 抛错（调用方降级）。
    """
    m = _md2html()
    if m is None:
        raise RuntimeError("md2html 不可用")
    title, _date, full = m.convert_md_to_html(md_text, "")
    mh = re.search(r'<header class="masthead">[\s\S]*?</header>', full)
    doc = re.search(r'<main class="doc">([\s\S]*?)</main>', full)
    foot = re.search(r'<footer class="colophon">[\s\S]*?</footer>', full)
    if not mh or not doc:
        raise RuntimeError("纸面管线未产出 masthead/doc")
    body = _inline_svg_paper(doc.group(1).strip(), md_dir)
    body = re.sub(r'<script.*?</script>', '', body, flags=re.S)  # 防注入兑底
    return title, mh.group(0), body, (foot.group(0) if foot else "")


def _extract_table_block(html, start):
    """从 start 位置起，抓其后第一个 <div class="table-wrapper"><table>…</table></div>。

    返回 (thead_ths, tbody_rows, abs_start, caption_start, abs_end)；任一环节缺失 → None
    （调用方不变换）。thead_ths=[原始 th HTML…]；tbody_rows=[[原始 td HTML…]…]。
    只用于纸面展示层变换，不解析嵌套（复盘表无内嵌表/td，非贪婪匹配安全）。
    """
    tw = html.find('<div class="table-wrapper">', start)
    if tw < 0:
        return None
    end = html.find("</table></div>", tw)
    if end < 0:
        return None
    end += len("</table></div>")
    block = html[tw:end]
    # 匹配边界：<th/<tr/<td 后须紧跟空白或 >（裸 <th[\s\S] 会把 <thead> 的 th 前缀误吃，单测抓出的真 bug）
    ths = re.findall(r"<th(?=[\s>])[\s\S]*?</th>", block)
    if not ths:
        return None
    tb = re.search(r"<tbody>([\s\S]*?)</tbody>", block)
    if not tb:
        return None
    rows = []
    for tr in re.findall(r"<tr(?=[\s>])([\s\S]*?)</tr>", tb.group(1)):
        tds = re.findall(r"<td(?=[\s>])[\s\S]*?</td>", tr)
        if tds:
            rows.append(tds)
    if not rows:
        return None
    # 表前**紧邻**的标题段 <p><strong>…</strong></p>（md2html 标准结构）起点；合并时一并吸收，防与新组表头重复。
    # 只看前 160 字内且以 wrap 开头处结尾（全篇找会误捕更早无关段，把中间内容整段吞掉）；不满足则=tw。
    caption_start = tw
    pre = html[max(0, tw - 160):tw]
    mcap = re.search(r"<p[^>]*>\s*<strong>[\s\S]*?</strong></p>\s*\Z", pre)
    if mcap:
        caption_start = tw - (len(pre) - mcap.start())
    return ths, rows, tw, caption_start, end


def _merge_sector_front_back(html):
    """复盘 2.1「前排/退潮」两表（领涨 TOP10 / 领跌 BOTTOM10）→ 单表左右双栏（主人 2026-10-10）。

    两表结构完全对称（同 5 列、同表头、各 10 行）时，合并为一张表：
      · 顶行 = 组表头（前排 span5 / 退潮 span5），次行 = 原字段表头
      · 每行 = 左半（前排第 i 行）+ 右半（退潮第 i 行），中缝加 .tb-divide 分隔
    列数不等 / 任一表缺失 / 行数差过大 → 原样返回（不报错、不编造空行，宁可不合并）。
    仅展示层 HTML 变换（在 render_paper 里对已生成 body 后处理，旧端 _paper_review_renderer 同位置），
    不触碰 report.css 唯一源、不改 MD 内容源、不改数据口径。
    样式（.rv-tb-merge / .tb-divide / .tb-grp 等）见 _PAPER_SHIELD ④。
    """
    fi = html.find("领涨 TOP10")
    bi = html.find("领跌 BOTTOM10")
    if fi < 0 or bi < 0 or bi <= fi:
        return html
    front = _extract_table_block(html, fi)
    back = _extract_table_block(html, bi)
    if not front or not back:
        return html
    f_ths, f_rows, _f_start, f_cap, f_end = front
    b_ths, b_rows, _b_start, b_cap, b_end = back
    if len(f_ths) != len(b_ths) or len(f_ths) < 2:
        return html
    if any(len(r) != len(f_ths) for r in f_rows) or any(len(r) != len(b_ths) for r in b_rows):
        return html   # 行 td 数与表头不齐 → 非标准结构（可能正则误匹），不硬拼
    if abs(len(f_rows) - len(b_rows)) > 3:      # 行数差过大（一侧缺行）→ 不硬拼
        return html
    ncol = len(f_ths)

    def _divide(cell, first):
        """给中缝第一列加 tb-divide 类（保留原 class/style）。"""
        if not first:
            return cell
        if cell.startswith("<th") or cell.startswith("<td"):
            m = re.match(r"<(t[hd])([^>]*)>", cell)
            tag, attrs = m.group(1), m.group(2)
            if "tb-divide" in attrs:
                return cell
            if "class=" in attrs:
                attrs = re.sub(r"class=\"", 'class="tb-divide ', attrs, count=1)
            else:
                attrs = ' class="tb-divide"' + attrs
            return "<" + tag + attrs + ">" + cell[m.end():]
        return cell

    # 组表头行 + 字段行
    grp = ('<tr class="tb-grp">'
           '<th class="tb-front" colspan="' + str(ncol) + '">前排·领涨 TOP10（价格 pct）</th>'
           '<th class="tb-back tb-divide" colspan="' + str(ncol) + '">退潮·领跌 BOTTOM10</th></tr>')
    hdr_cells = "".join(
        [f_ths[k] for k in range(ncol)] +
        [_divide(b_ths[k], k == 0) for k in range(ncol)])
    hdr = '<tr>' + hdr_cells + '</tr>'
    nrow = max(len(f_rows), len(b_rows))
    body_rows = []
    for r in range(nrow):
        # 行宽已由上方防御保证=ncol；仅一侧缺行时补 — 占位（不编造数据）
        left = f_rows[r] if r < len(f_rows) else ["<td>—</td>"] * ncol
        right = b_rows[r] if r < len(b_rows) else ["<td>—</td>"] * ncol
        cells = "".join(left) + "".join(_divide(right[k], k == 0) for k in range(ncol))
        body_rows.append("<tr>" + cells + "</tr>")
    merged = (
        '<div class="table-wrapper"><table class="rv-tb-merge">\n'
        '<thead>\n' + grp + "\n" + hdr + "\n</thead>\n"
        '<tbody>\n' + "\n".join(body_rows) + "\n</tbody>\n"
        "</table></div>")
    # 替换：从「前表 caption」到「后表 wrapper 结束」整段替为合并表（两表连续的标准态，
    #  含两个旧 caption 一并吸收，组表头即新标题不残留）；若 caption 顺序异常（两表间夹了
    #  其它内容），从后往前分两段替，避免偏移失效。
    if f_cap <= f_end <= b_cap:
        html = html[:f_cap] + merged + html[b_end:]
    else:
        html = html[:b_cap] + "" + html[b_end:]
        html = html[:f_cap] + merged + html[f_end:]
    return html


# ---------------------------------------------------------------- 三、渲染回调
def render_paper(pick) -> dict:
    """source.render_cached 注入的渲染回调（纸面，旧端 _paper_review_renderer）。

    返回 dict（供缓存/降级包装）：
        {date, file, title, html, masthead, footer, css, paper, mtime_ns, charts}

    · 只读 pick.path（UTF-8）与 report.css 唯一源；**不写任何文件**。
    · MD 读不到 / CSS 派生失败 / 管线未产出 → **抛 RuntimeError**，由 render_cached
      捕获降级（保留上一份成功内容 + degraded=True，不 500）。
    · 纸面不出面板深色图：charts 两键置 None。
    """
    try:
        with open(pick.path, encoding="utf-8") as f:
            md_text = f.read()
    except OSError as exc:
        raise RuntimeError("复盘 MD 读取失败: %s: %s" % (type(exc).__name__, exc)) from exc
    css = _paper_scope_css()
    if not css:
        raise RuntimeError("纸面 CSS 派生失败（report.css 缺失或作用域自检未过）")
    title, masthead, html, footer = _paper_pipeline(md_text, os.path.dirname(pick.path))
    html = _merge_sector_front_back(html)   # 2026-10-10 主人指定：前排/退潮两表→单表双栏（展示层后处理）
    return {
        "date": pick.date,
        "file": os.path.basename(pick.path),
        "title": title or os.path.basename(pick.path),
        "html": html,
        "masthead": masthead,
        "footer": footer,
        "css": css,
        "paper": True,
        "mtime_ns": pick.mtime_ns,
        "charts": {"swimlane": None, "track": None},
    }
