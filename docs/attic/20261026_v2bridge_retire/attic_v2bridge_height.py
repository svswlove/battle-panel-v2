# -*- coding: utf-8 -*-
"""v2bridge 嵌 v2 时 iframe 高度口径（2026-10-26）。

主人反馈：「为什么 8810 页面底下还会有一大片位置的留白呢」（附截图，底部整条空白）。

根因（实测，非推测）：
  `v2bridge.js` 给 iframe 写死 `height: calc(100vh - 90px)`。
  那 90px 是给顶部导航让的位，但 8810 早已改成无顶栏（body 只有 12px 上下 padding）
  ⇒ 90px 里多出的 66px 全变成底部空白。
  1600×1004 视口实测：body 高 938、iframe 底边 926、下方空 78px。

修法：高度改为「填满 body 可用高度」= `calc(100dvh - 24px)`（body 上下各 12px padding），
不再猜顶栏高度；`100vh` 那条保留作老浏览器兜底。

本测试只读源码，不跑浏览器（浏览器侧口径由 `_vis/_gap_verify.js` 实测）。
"""
import re
from pathlib import Path

PANEL = Path(__file__).resolve().parents[2]
BRIDGE = PANEL / "app" / "web" / "v2bridge.js"


def _src() -> str:
    return BRIDGE.read_text(encoding="utf-8")


def test_iframe_height_not_hardcoded_90px() -> None:
    """iframe 高度不能再写死 90px：8810 没有顶栏，那 90px 全变底部空白。"""
    src = _src()
    # 从 cssText 赋值开始截（注释写在 var iframe 之前，会引用历史写法，不能一并判红）
    seg = src[src.index("iframe.style.cssText"):]
    seg = seg[: seg.index("iframe.setAttribute")]
    assert "calc(100vh - 90px)" not in seg, (
        "iframe 高度又写死 calc(100vh - 90px)：8810 无顶栏，底部会空出 66px+"
    )


def test_iframe_height_fills_body_padding() -> None:
    """高度必须按 body 实际 padding 算，不能猜一个固定数。"""
    src = _src()
    seg = src[src.index("var iframe = document.createElement"):]
    seg = seg[: seg.index("iframe.setAttribute")]
    assert "100dvh" in seg, "未用 dvh（地址栏伸缩时会溢出或留白）"
    assert "24px" in seg, "未按 body 上下 padding（各 12px）扣减"
    # 老浏览器兜底：dvh 不认时回落 vh
    assert "100vh" in seg, "缺 100vh 兜底（老浏览器高度会塌成 0）"


def test_height_declared_dvh_after_vh() -> None:
    """两条 height 的顺序必须是 vh 在前、dvh 在后——反了会让 dvh 被 vh 覆盖。"""
    src = _src()
    seg = src[src.index("var iframe = document.createElement"):]
    seg = seg[: seg.index("iframe.setAttribute")]
    i_vh = seg.find("100vh")
    i_dvh = seg.find("100dvh")
    assert i_vh != -1 and i_dvh != -1
    assert i_vh < i_dvh, "声明顺序反了：dvh 在前会被后面的 vh 覆盖，等于没改"
