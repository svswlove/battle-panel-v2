# -*- coding: utf-8 -*-
"""卡内跨卡事件 ↔ 壳监听 的对齐检查（2026-10-26）。

**为什么有这个文件**：迁到 v2 后有两处交互静默失效，都不报错、只是点了不动：

  · `panel:open-kline`  —— 自选卡点个股跳 K 线，卡内 `emit()` 发了事件，shell 从未监听；
  · `panel:open-sector` —— 自选卡点板块芯片，同上，而且它还是 8002 端口关不掉的最后一条路
                           （旧路径：事件 → 父页 → `AmvCard.open` → iframe 嵌 8002）。

这两条在旧页里都是通的（`data-open-kline` / `data-open-amv` 委托到 KLineApp / AmvCard），
所以不是新需求，是迁移断掉的交互。根因是**「发」和「收」两份代码各写各的，没有一致性约束**。

本文件守两条不变式：
  1. 卡内发出的每个 `panel:*` 事件，壳里必须有对应监听（否则就是静默失败）；
  2. 板块详情优先用卡内 v2 原生实现，父页 `AmvCard` 只做「本页无板块卡」时的兜底
     —— 顺序反了 8002 就关不掉。

只读源码，不跑浏览器。
"""
import re
from pathlib import Path

PANEL = Path(__file__).resolve().parents[2]
WEB = PANEL / "app" / "web"
PLUGINS = PANEL / "app" / "plugins"

# 卡侧前端全集：frontend.js + 其子模块（板块卡已拆成 frontend/*.js）
CARD_FILES = sorted(list(PLUGINS.glob("*/frontend.js")) + list(PLUGINS.glob("*/frontend/*.js")))

SHELL = WEB / "shell.js"

# 这些事件是「壳发给卡」的方向，不该在卡侧被 emit，也不归本检查管
_NOT_EMITTED_BY_CARDS = {"open-card-ack"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _emit_pattern(prefix: str) -> re.Pattern:
    # 兼容 emit('panel:x' / new CustomEvent('panel:x' 两种写法，单双引号都认
    return re.compile(
        r"""(?:emit\s*\(\s*|new\s+CustomEvent\s*\(\s*)['"]("""
        + re.escape(prefix)
        + r"""[\w-]+)['"]"""
    )


def _emitted_by_cards() -> dict:
    """{事件名: [发出它的文件相对路径, ...]}"""
    found = {}
    pat = _emit_pattern("panel:")
    for f in CARD_FILES:
        for name in pat.findall(_read(f)):
            if name in _NOT_EMITTED_BY_CARDS:
                continue
            found.setdefault(name, []).append(str(f.relative_to(PANEL)))
    return found


def _listened_by_shell() -> set:
    src = _read(SHELL)
    return set(re.findall(r"""addEventListener\s*\(\s*['"](panel:[\w-]+)['"]""", src))


def test_every_card_event_has_a_shell_listener():
    """卡内发出的每个 panel:* 事件，壳都必须真监听。

    失败含义：卡片里有个可点入口发事件出去，没人接 ⇒ 用户点了没反应，且控制台只留
    一行 debug（生产不显示），排查成本极高。修法是在壳里补监听，不要删卡的 emit。
    """
    emitted = _emitted_by_cards()
    listened = _listened_by_shell()
    missing = {name: files for name, files in emitted.items() if name not in listened}
    assert not missing, (
        "以下事件卡内在发、壳没监听（点了会静默无反应）：\n"
        + "\n".join("  %s ← %s" % (n, ", ".join(fs)) for n, fs in sorted(missing.items()))
    )


def test_known_card_events_are_the_two_real_ones():
    """反向校验：确实扫到了这两个事件，防止正则写坏导致上一条空过。

    只断言存在性、不锁死全集——将来新增合法事件不该让本文件变红。
    """
    emitted = _emitted_by_cards()
    for name in ("panel:open-kline", "panel:open-sector"):
        assert name in emitted, "没扫到 %s，检查 _emit_pattern 是否还能匹配卡侧写法" % name


def _fn_body(src: str, name: str) -> str:
    """取一个顶层 function 的函数体（到下一个顶层 function 为止）。"""
    m = re.search(r"^function %s\s*\(" % re.escape(name), src, re.M)
    assert m, "shell.js 里找不到 function %s" % name
    rest = src[m.end():]
    nxt = re.search(r"^function \w+\s*\(", rest, re.M)
    return rest[: nxt.start() if nxt else len(rest)]


def test_open_sector_prefers_in_card_detail_over_parent_amvcard():
    """板块详情：卡内 v2 原生详情在前，父页 AmvCard 在后。

    这是 8002 能否下线的判定条件。旧实现把 `window.parent !== window` 的判断放在最前面，
    一嵌旧页就直接 postMessage 给父页，父页再 iframe 8002 —— 板块 Tab 早就是 v2 卡了，
    详情却仍被 8002 绑住。
    """
    body = _fn_body(_read(SHELL), "ctxOpenSector")
    i_detail = body.find("_openDetail")
    i_parent = body.find("window.parent")
    assert i_detail != -1, "ctxOpenSector 里没有卡内 _openDetail 调用"
    assert i_parent != -1, "ctxOpenSector 里没有父页 AmvCard 兜底"
    assert i_detail < i_parent, (
        "ctxOpenSector 里父页 postMessage（第 %d 字符）排在卡内 _openDetail（第 %d 字符）之前"
        " —— 又会绕过 v2 原生详情去 iframe 8002" % (i_parent, i_detail)
    )


def test_open_sector_does_not_switch_card():
    """板块详情不能用「切卡」实现。

    2026-10-26 实测踩过的坑：改成「先 selectCard('sector') 再开详情」后，嵌入旧页的
    单卡模式下 selectCard 会把用户当前看的卡整个换掉，而父页 Tab 还停在原处
    ⇒ Tab 写着「自选」、iframe 里是「板块」，两边不同步；旧页的行为是就地弹遮罩、不换页。
    正确做法是壳层直调 sector 的 detail.js（openDetailStandalone），不碰卡生命周期。
    """
    body = _fn_body(_read(SHELL), "ctxOpenSector")
    assert "selectCard(" not in body, (
        "ctxOpenSector 里又出现了切卡。嵌入单卡模式下它会换掉用户正在看的卡，"
        "而父页 Tab 不会跟着变——两边不同步（2026-10-26 实测复现）"
    )
    assert "openSectorOverlay(" in body, "没走壳层弹窗，板块详情会退回父页/8002"
    assert "_openDetail" in body, "当前就在板块卡时应优先用卡内详情（沿用卡的周期/看盘日）"


def test_sector_plugin_exports_standalone_detail():
    """sector 前端必须导出 openDetailStandalone，壳层弹窗靠它。"""
    src = _read(PLUGINS / "sector" / "frontend.js")
    assert "openDetailStandalone" in src, (
        "sector/frontend.js 没导出 openDetailStandalone —— 壳层无法脱离卡开板块详情"
    )


def test_kline_bridge_has_one_delegation():
    """开卡委托只能有一套。

    旧实现按「嵌入 / 独立」拆了两套监听：同一动作两份实现，改一边另一边不生效
    （2026-10-26 板块详情就是这么“改了没反应”的）。现在合并为一套，
    两条分支只差「K 线转不转父页」这个判断。
    """
    body = _fn_body(_read(SHELL), "bindKlineBridge")
    assert body.count("addEventListener('click'") == 1, (
        "bindKlineBridge 里装了 %d 个 click 委托，应该只有 1 个"
        % body.count("addEventListener('click'")
    )
    # 板块详情在嵌入模式下也不能转父页（否则又是 8002）
    i_amv_post = body.find("PM_AMV")
    assert i_amv_post == -1 or body.find("ctxOpenSector") < i_amv_post, (
        "bindKlineBridge 仍在嵌入时用 PM_AMV 转父页——板块详情又回到 8002 了"
    )
    assert "ctxOpenSector(" in body, "板块点击没接 ctxOpenSector"


def test_sector_card_exposes_open_detail():
    """板块卡必须暴露 `_openDetail(code, name, level)`，这是壳调它的契约。"""
    src = _read(PLUGINS / "sector" / "frontend.js")
    assert re.search(r"_openDetail\s*\(\s*code\s*,\s*name\s*,\s*level\s*\)", src), (
        "板块卡 _openDetail 签名变了，shell.js 的调用点会静默失效"
    )
    assert "openDetail(" in src, "板块卡没调 frontend/detail.js 的 openDetail"
    detail = PLUGINS / "sector" / "frontend" / "detail.js"
    assert detail.exists(), "sector/frontend/detail.js 不存在 —— 板块详情不是 v2 原生实现"
