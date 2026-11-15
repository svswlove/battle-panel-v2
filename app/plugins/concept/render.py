# -*- coding: utf-8 -*-
"""concept/render.py · 概念概况 MD → 正文 HTML（口径唯一源的插件侧副本）

职责
    把概念概况 Markdown 渲染成作战面板可直接插入的正文 HTML：
    标题/表格/列表/粗体（python-markdown）+ 表格包 `.md-table-wrap` + 个股名 linkify（点开 K 线）。

迁移说明（2026-10-23，任务书 docs/task_concept_4_2.md §3.3）
    本文件从旧仓同口径实现（2026-11-07 已退役删除）移植，
    渲染逻辑（正则、扩展列表、linkify 最长优先 ≥3 字）**逐字保留**，只改一处依赖注入：
    个股名 → 码不再 `from common.names import code_of`，改为 `name_to_code` callable 参数
    （生产由插件从 `ctx.json_read("stock_names")` 反查表构建）。
    插件契约禁止 `from common` / `sqlite3.connect`，故本模块**零 common 依赖**。

设计初心（为什么单独成模块）
    · **md→html 逻辑唯一源**：原实现内联在 engine/server.py::_concept_md_to_html。
      2026-10-03 新增「概念页自包含导出器」（engine/tools/export_concept_page.py）需在离线
      导出时产出**同款**概况 HTML；若两边各写一份 = 双源漂移（面板与分享版概况会分叉）。
    · 不走复盘专用管线（md2html 的泳道符号/色标记是复盘语义，概念概况用不上）；
      样式由前端 #panel-concept 提供，表格包 .md-table-wrap 与复盘同口径。

数据源与口径
    · 入参 md 文本来自同目录 `source.py::load_detail`（概念库唯一解析源）。
    · 个股名 → 可点元素（点开 K 线）依赖注入的 `name_to_code`；
      未传 / 名表不可用 / 未命中 → 原样输出（不编造）。

上下游
    上游：`app/plugins/concept/source.py`（概念 MD）。
    下游：`app/plugins/concept/backend.py` `/api/concept.json?slug=`。
    前端消费：旧 `engine/web/index.html` 概念 Tab（.cp-md 容器 + 面板级委托 [data-open-kline]）。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# 名→码解析器（注入）；缺省 None = 不做 linkify（原样输出，不猜）
NameResolver = Callable[[str], Optional[str]]


def _linkify_seg(text: str, name_to_code: NameResolver) -> str:
    """把一段**纯文本**里的已知个股名包成可点元素（最长优先、≥3 字）。

    职责：只做“名字→码”替换；不碰标签（调用方已按标签切好）。
    数据源：注入的 `name_to_code`；未命中→原样输出（不编造）。
    """
    out, i, n = [], 0, len(text)
    while i < n:
        hit = None
        for ln in range(min(8, n - i), 2, -1):      # 最长优先，≥3 字（避免把常用词误包）
            cand = text[i:i + ln]
            code = name_to_code(cand)
            if code:
                hit = (cand, code)
                break
        if hit:
            out.append('<span class="k-name" data-open-kline="%s" data-open-name="%s" title="%s · 点击看 K 线">%s</span>'
                       % (hit[1], hit[0], hit[1], hit[0]))
            i += len(hit[0])
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def linkify_stock_names(html: str, name_to_code: Optional[NameResolver] = None) -> str:
    """概念概况正文里的个股名 → 可点元素（点开 K 线）。

    职责：只处理**标签之外**的文本段，不破坏 HTML 结构与属性。
    上游：md_to_html 产出的正文 HTML；下游：前端面板级委托（[data-open-kline] → KLineApp）。
    解析器缺失/不可用 / 已 linkify 过 → 原样返回（不阻断概况）。
    """
    if not html or 'k-name' in html:
        return html
    if not callable(name_to_code):
        return html
    parts = re.split(r'(<[^>]*>)', html)          # 保序切分：标签 / 文本
    for i, seg in enumerate(parts):
        if not seg or seg.startswith('<') or not seg.strip():
            continue
        parts[i] = _linkify_seg(seg, name_to_code)
    return ''.join(parts)


def md_to_html(md_text: str, name_to_code: Optional[NameResolver] = None) -> str:
    """概念概况 MD → 正文 HTML（轻量：标题/表格/列表/粗体）。

    不走复盘专用管线（md2html 的泳道符号/色标记是复盘语义，概念概况用不上）；
    样式由前端 #panel-concept 提供，表格包 .md-table-wrap 与复盘同口径。
    """
    import markdown  # noqa: PLC0415 - 懒引入：只在真正渲染时依赖（环境已装 3.10.3）
    html = markdown.markdown(
        md_text or "",
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5")
    html = re.sub(r"(<table>)", r'<div class="md-table-wrap">\1', html)
    html = re.sub(r"(</table>)", r"\1</div>", html)
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)   # 防注入兑底
    html = linkify_stock_names(html, name_to_code)               # 个股名 → 可点（点开 K 线）
    return html
