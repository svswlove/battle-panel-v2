# -*- coding: utf-8 -*-
"""纸面语义分块 v3 回归（2026-11-08 · 结论带被埋 / 三段宽度对不齐）。

背景（主人口径）
    `> 🎯 一句话 / > 📌 三件事 / > 🚨 次日方向` 是整份复盘的第一视觉层，必须各自独立成块，
    元信息（数据时点/口径/降级项）降到第三层。旧实现只看 blockquote 的**首字符**，
    而 Python-Markdown 会把「相邻引用 + 段内软换行」合并进同一个 <blockquote>/<p>，
    于是首字符为 ⏱/⚠ 的引用一降级，🎯📌🚨 全被埋进 12.8px 的 .quote（11-02~11-06 每份都中）。

本文件只管「分块与定级」与「垫片里的字号/宽度口径」；HTTP 契约仍在 test_review_api.py。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.review import paper as review_paper  # noqa: E402
from app.plugins.review import render as review_render  # noqa: E402

SCOPE = "#panel-review .rv-paper"
# 工作区根（OH-WorkSpace）：panel-v2 → projects → 工作区
WS = ROOT.parent.parent
REAL_MD = WS / "夏天-activity" / "每日复盘" / "日复盘 2026-11-06.md"


def _md():
    return review_render._md2html()


def _body(md_text):
    """只取正文段（convert_md_to_html 返回整页；报头/报尾不参与分块断言）。"""
    full = _md().convert_md_to_html(md_text, "")[2]
    m = re.search(r'<main class="doc">([\s\S]*?)</main>', full)
    assert m, "convert_md_to_html 未产出 main.doc"
    return full, m.group(1)


def _blocks(html):
    """按出现顺序取出 (class, 纯文本首段)，便于断言「块序 + 定级」。"""
    out = []
    for m in re.finditer(r'<div class="([a-z]+)">', html):
        txt = re.sub(r"<[^>]+>", "", html[m.end():m.end() + 120])
        out.append((m.group(1), txt.strip()[:26]))
    return out


# ---------------------------------------------------------------- 1. 核心用例：一个引用内多段语义标记


MULTI_MARK_MD = """# 日复盘 2026-11-06（周五 · 用例）

> 数据时点：收盘

> ⏱ 本份为周末延后执行
> ⚠ 降级项：news_snapshot 缺失

> 🎯 **一句话**：今天是外盘砸出来的算力杀估值。
> 📌 **三件事**：①钱是走的不是搬的；②退潮退到没有主线。
> 🚨 **次日方向**：周一只验费半的传导和 AMV 守不守 MA10。

## 一、板块

| a | b |
|---|---|
| 1 | 2 |
"""


def test_one_quote_with_three_marks_splits_into_three_blocks() -> None:
    """单个 <blockquote> 内的 🎯/📌/🚨 → takeaway/keycard/alert 三块（不是只认第一段）。"""
    _full, html = _body(MULTI_MARK_MD)
    blocks = [c for c, _ in _blocks(html)]
    assert blocks[:3] == ["takeaway", "keycard", "alert"], blocks[:6]
    # 三块内容各自完整（不是被截断成一片）
    assert "今天是外盘砸出来的算力杀估值" in html
    assert "钱是走的不是搬的" in html and "退潮退到没有主线" in html
    assert "周一只验费半的传导" in html
    # 三块共用同一父容器语义（正文首三个块）
    assert html.index('class="takeaway"') < html.index('class="keycard"') < html.index('class="alert"')
    assert html.index('class="alert"') < html.index("<section")


def test_lead_meta_before_conclusion_is_not_a_quote_block() -> None:
    """结论带之前的 ⏱/⚠ 注记不再占正文首屏，也不跟结论带挤同一块（降为元信息）。"""
    _full, html = _body(MULTI_MARK_MD)
    assert html.count('class="takeaway"') == 1
    # 降级项/时点注记不得留在正文第一个块里
    first = re.search(r'^\s*<div class="([a-z]+)">', html)
    assert first and first.group(1) == "takeaway"
    # .quote 块里不能再出现 🎯/📌/🚨
    for inner in re.findall(r'<div class="quote">([\s\S]*?)</div>', html):
        assert not re.search(r"[🎯📌🚨]", inner)


def test_lead_meta_hoisted_into_masthead_notes() -> None:
    """元信息被收编进报头速览带末尾（.brief-notes，第三层字号）。"""
    full = _md().convert_md_to_html(MULTI_MARK_MD, "")[2]
    assert "brief-notes" in full
    assert "降级项" in full
    # 降级项只出现一次（描在报头，不在正文重复）
    assert full.count("降级项") == 1
    # 报头顺序：速览带项在前、注记在后
    assert full.index('class="brief-items"') < full.index('class="brief-notes"')


# ---------------------------------------------------------------- 2. 不做坏：无结论带的引用保持原样


def test_quote_without_conclusion_keeps_default_classes() -> None:
    """不含 🎯📌🚨 的引用：原样留在正文，.quote / .warning / .highlight 定级不变。"""
    md = ("# 日复盘 2026-11-06（周五）\n\n> 数据时点：收盘\n\n## 一、板块\n\n"
          "> 注：这是口径脚注，不带语义标记。\n\n"
          "> ⚠ 口径备注：剥离公式见上。\n\n"
          "> 💡 判断：这句是高亮。\n")
    _full, html = _body(md)
    assert html.count('class="quote"') == 1
    assert html.count('class="warning"') == 1
    assert html.count('class="highlight"') == 1
    assert html.count('class="takeaway"') == 0        # 不凭空造结论带


def test_unbalanced_inline_tag_blocks_the_split() -> None:
    """切点处 <strong> 未闭合 → 不切（宁可不分块，也不把加粗拦腰截断）。"""
    html = ("<div class=\"quote\"><p>🎯 结论<strong>半个加粗\n📌 三件事</strong>收尾</p></div>")
    out = _md().apply_semantic_blocks(html)
    assert out.count('<div class="') == 1             # 只有原引用这一块
    assert out.count("<p>") == 1


def test_div_tags_stay_balanced() -> None:
    """分块后 div 开闭必须配平（旧 P0：凭空多出 </div> 会把 .sheet 提前关掉）。"""
    for md in (MULTI_MARK_MD, "# x\n\n> a\n\n> 🎯 b\n\n> 📌 c\n\n> 🚨 d\n"):
        _full, html = _body(md)
        assert html.count("<div") == html.count("</div>")
    # 无主闭合（旧实现的成因）也不得造出多余 </div>
    assert _md().apply_semantic_blocks("</blockquote><p>x</p>").count("</div>") == 0


# ---------------------------------------------------------------- 3. 真实复盘回归（11-06）


def test_real_1106_has_takeaway_and_no_buried_marks() -> None:
    """真文件：`class="takeaway"` ≥1，且 .quote 块内不再含 🎯（主人验收口径）。"""
    if not REAL_MD.exists():
        pytest.skip("真实复盘 MD 不可用")
    md = REAL_MD.read_text(encoding="utf-8")
    _title, _mh, body, _foot = review_paper._paper_pipeline(md, str(REAL_MD.parent))
    assert body.count('class="takeaway"') >= 1
    assert body.count('class="keycard"') >= 1
    assert body.count('class="alert"') >= 1
    for inner in re.findall(r'<div class="quote">([\s\S]*?)</div>', body):
        assert not re.search(r"[🎯📌🚨]", inner)


# ---------------------------------------------------------------- 4. 垫片口径：字号与三段同宽


def test_paper_shield_raises_conclusion_band_fonts() -> None:
    """结论带字号按口径：🎯≥18 / 📌≥15.5 / 🚨≥16（正文 15px）。"""
    css = review_paper._PAPER_SHIELD
    want = {".takeaway": 18.0, ".keycard": 15.5, ".alert": 16.0}
    for sel, size in want.items():
        m = re.search(re.escape(SCOPE) + r" " + re.escape(sel) + r"\{([^}]*)\}", css)
        assert m, sel
        fs = re.search(r"font-size:\s*([\d.]+)px", m.group(1))
        assert fs and float(fs.group(1)) >= size, (sel, m.group(1))


def test_paper_shield_sheet_is_centered_single_column() -> None:
    """三段同宽：整纸定宽 = 72rem + 两侧页边距，并左右居中（正文不再左对齐留白）。"""
    css = review_paper._PAPER_SHIELD
    m = re.search(re.escape(SCOPE) + r" \.sheet\{([^}]*)\}", css)
    assert m, "缺 .sheet 定宽规则"
    body = m.group(1)
    assert "72rem" in body and "var(--pad-x)" in body
    assert "margin-left:auto" in body and "margin-right:auto" in body
    # 速览带的 dd 不再吃 46em 行长（否则速览带右侧也短一截）
    assert re.search(re.escape(SCOPE) + r" \.brief \.bi dd\{[^}]*max-width:\s*none", css)
    # 元信息注记 ≤12px 灰字
    m2 = re.search(re.escape(SCOPE) + r" \.brief-notes\{([^}]*)\}", css)
    assert m2
    fs = re.search(r"font-size:\s*([\d.]+)px", m2.group(1))
    assert fs and float(fs.group(1)) <= 12.0


def test_paper_css_cache_key_covers_shim(monkeypatch) -> None:
    """只改垫片、report.css 不动时也必须重算（否则「改了不生效」）。"""
    review_paper.reset_css_cache()
    first = review_paper._paper_scope_css()
    assert first
    second = review_paper._paper_scope_css()
    assert second is first                                  # 命中缓存
    monkeypatch.setattr(review_paper, "_PAPER_SHIELD", review_paper._PAPER_SHIELD + "\n/* probe */\n")
    third = review_paper._paper_scope_css()
    assert "/* probe */" in third                           # 垫片变了 → 重算
