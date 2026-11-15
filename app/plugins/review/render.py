# -*- coding: utf-8 -*-
"""review/render.py · 复盘 MD → 面板正文 HTML（v6 默认路径，插件侧移植）

职责
    把一份被选中的复盘 Markdown 渲染成作战面板复盘 Tab 可直接 innerHTML 的正文片段：
    标题/表格/列表/引用（python-markdown）+ 表格内泳道符号着色 + {r}{y}{g}{x} 色标记
    + 表格包 `.md-table-wrap` + MD 内联 SVG 图内联（`charts/*.svg` → 真 `<svg>`）。

迁移说明（2026-10-23，任务书 docs/task_review_md_4_5.md §2）
    本文件是旧仓 `engine/server.py::_md_to_body_html`（1763 行）+ `_inline_svg`
    （1274 行）的插件侧移植，**渲染顺序与正则逐字保留**，只改依赖注入：
    · md2html 不再从 `~/.hanako/skills/report-builder/` 动态 import，
      而是加载同目录的 `md2html.py`（逐字复制件，冻结接口不变）；
    · 不 `from common`、不碰 FastAPI、不写任何文件（只用 `open(..., "r")` 读 MD/SVG）。
    · 不做图表：v6 默认路径 `charts = {swimlane: None, track: None}`
      （旧端同样默认不出图：`OH_MAINLINE_ALLOW != 1` → `_render_review_charts` 双 null）。

失败语义（与旧端刻意不同，遵循任务书 §2）
    旧端 `_md_to_body_html` 吞异常返回 `(None, None, None)`，再由路由兜底；
    本版**抛 `RuntimeError`** 交给 `source.render_cached` 统一降级
    （保留上一份成功内容 + degraded=True）——降级只在一处实现，不写两套。

上下游
    上游：`app/plugins/review/backend.py::/api/review.md` → `source.render_cached`。
    下游：旧前端 `engine/web/index.html` 复盘 Tab（`#panel-review` 容器提供样式）。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

# md2html 冻结接口的三个函数（本模块按旧顺序只调这两个；colorize_td_pct 等属纸面管线，
# 面板路径不走——旧端 `_md_to_body_html` 同样不调它，保持逐字一致）
#   extract_title_and_meta(md_text) -> (title, date_str, body_md, custom_meta)
#   colorize_td_symbols(html)       -> html
#   apply_color_marks(html)         -> html

_MD2HTML = None


def _md2html():
    """加载同目录 `md2html.py`（冻结接口副本）；失败 → RuntimeError（由 render_cached 降级）。

    为什么按文件路径加载而不是 `import md2html`：core 用
    `spec_from_file_location("_panel_v2_plugin_review_backend", ...)` 加载本插件，
    没有父包、也不保证插件目录在 `sys.path` 上；按路径加载最稳，且只在本进程加载一次。
    """
    global _MD2HTML
    if _MD2HTML is not None:
        return _MD2HTML
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "md2html.py")
    spec = importlib.util.spec_from_file_location("_panel_v2_review_md2html", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法为 md2html.py 建立 import spec: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _MD2HTML = module
    return module


def _inline_svg(body_html, md_dir, max_bytes=2_000_000):
    """把 `<img src="charts/xxx.svg">` 替换为内联 `<svg>`（旧端 server.py:1274 逐字移植）。

    根因（2026-09-11，沿用旧端说明）：复盘 MD 用 `![泳道图](charts/swimlane_{date}.svg)`
    引用相对路径 SVG（由 `夏天-activity/工具/fupan_chart.py` 产出），而 `/api/review.md`
    只做 markdown→HTML，不做 inline_svg。前端 `#panel-review` 是页面内嵌上下文，
    `charts/xxx.svg` 相对路径会解析到 `engine/web/` → 404 → 图裂。此处按 MD 所在目录解析
    src 并内联，与生成侧同口径（复用不重写）。

    - md_dir：MD 文件所在绝对目录（相对 src 以此为基准；绝对路径直接用）
    - 读不到/超 max_bytes/非 SVG → 原样保留（不编造、不阻断正文渲染）
    - 返回 (html, n)：n=**实际内联成功**的张数（repl 返回原串时 subn 仍计入替换，
      故用计数器，不数匹配数）；调用方据此留痕。
    """
    if not body_html:
        return body_html, 0

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
        if "<svg" not in svg:  # 非 SVG 内容（防误内联）→ 原样保留
            return m.group(0)
        count["n"] += 1
        # 2026-09-13：把整幅白底 rect（历史 MD 自带的旧静态图）改透明 —— 深色主题下不再刺眼。
        # 只改「整宽整高 + fill=#FFFFFF」的背景 rect（正则限首部，低风险，不误伤内容元素）。
        svg = re.sub(r'<rect\s+width="(\d+(?:\.\d+)?)"\s+height="(\d+(?:\.\d+)?)"\s+fill="#FFFFFF"\s*/>',
                     r'<rect width="\1" height="\2" fill="transparent"/>', svg, count=1)
        # .rv-chart 承载卡（前端 #panel-review 提供主题底 var(--rv-panel)）：图配色随主题变量适配。
        return f'<div class="rv-chart">{svg}</div>'

    out = re.sub(r'<img[^>]*src="([^"]+\.svg)"[^>]*>', _repl, body_html)
    return out, count["n"]


def md_to_body_html(md_text, md_dir=None):
    """MD → 正文 HTML（旧端 `_md_to_body_html` 逐字移植，异常改抛 RuntimeError）。

    返回 `(title, meta_date, body_html)`；md_dir 给定 → 额外做 SVG 内联
    （问题③：泳道图/赛道图可见，不重画）。

    meta_date 口径与旧端一致：`extract_title_and_meta` 的 date_str，取不到回退
    `custom_meta`（📅 行），再取不到为空串。
    """
    m = _md2html()
    try:
        import markdown  # noqa: PLC0415 - 懒引入：只有真正渲染时才需要（环境已装）
        title, date_str, body_md, custom_meta = m.extract_title_and_meta(md_text)
        body_html = markdown.markdown(
            body_md, extensions=["tables", "fenced_code", "codehilite", "sane_lists"],
            output_format="html5")
        body_html = m.colorize_td_symbols(body_html)   # 表格内泳道符号着色
        body_html = m.apply_color_marks(body_html)     # {r}{y}{g}{x} 色标记
        # 表格包 wrapper（与 md2html 同口径，样式由前端 #panel-review 提供）
        body_html = re.sub(r'(<table>)', r'<div class="md-table-wrap">\1', body_html)
        body_html = re.sub(r'(</table>)', r'\1</div>', body_html)
        if md_dir:  # 问题③：内联 charts/*.svg → 泳道图/赛道图随复盘更新即时可见
            body_html, _ = _inline_svg(body_html, md_dir)
            # 2026-09-13 工单 20260913-0800：此处**不做**任何审计流写入。
            # 本函数是 GET 只读渲染路径（前端复盘 Tab 每 60s 轮询），只读端点不应有写副作用。
        body_html = re.sub(r'<script.*?</script>', '', body_html, flags=re.S)  # 防注入兑底
        return title, date_str or custom_meta or "", body_html
    except Exception as exc:  # noqa: BLE001 - 统一抛 RuntimeError，由 render_cached 降级
        raise RuntimeError("复盘 MD 渲染失败: %s: %s" % (type(exc).__name__, exc)) from exc


def render_review(pick) -> dict:
    """`source.render_cached` 注入的渲染回调（旧端 `_review_renderer` 的 v6 子集）。

    返回 dict（供 review 插件 `source.py` 缓存/降级包装）：
        {date, file, title, html, mtime_ns, charts:{swimlane,track}}

    · 只读 `pick.path`（UTF-8）；读不到 / 渲染异常 → **抛 RuntimeError**，
      由 `source.render_cached` 捕获并回退上一份成功内容（不 500 拖垮面板）。
    · `charts` 恒双 null：v6 默认路径不出泳道/赛道图（旧端 `OH_MAINLINE_ALLOW != 1` 同）。
    · `title` 兜底用文件名（旧端 `title or os.path.basename(pick.path)`）。
    """
    try:
        with open(pick.path, encoding="utf-8") as f:
            md_text = f.read()
    except OSError as exc:
        raise RuntimeError("复盘 MD 读取失败: %s: %s" % (type(exc).__name__, exc)) from exc

    title, _meta_date, html = md_to_body_html(md_text, md_dir=os.path.dirname(pick.path))
    return {
        "date": pick.date,
        "file": os.path.basename(pick.path),
        "title": title or os.path.basename(pick.path),
        "html": html,
        "mtime_ns": pick.mtime_ns,
        "charts": {"swimlane": None, "track": None},
    }
