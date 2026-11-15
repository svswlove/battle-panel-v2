# -*- coding: utf-8 -*-
"""实时行情 KPI（frontend/quotes.js）守门测试 —— 本单可选的第四件。

旧页是「先渲空 path、异步回填 d 属性」，靠 _sparkInject（5119-5128）在每次整页重建后
把缓存的背景线写回去。卡片是「先取数、再渲 DOM + 内容签名缓存」，照抄空 path 会让回填
被下一次 setHtml 冲掉且没有第二次机会（任务书点名的坑）。所以本单测的核心是两件事：

  ① 渲染结果与旧页**回填完成后**的 DOM 逐字节一致（cards 10 / paths 20 全填）；
  ② 卡片**首绘就没有空 d**（rawEmpty=20 是旧页，curEmpty=0 是卡片）——
     这一条用 quotes_align.mjs + audit_dom.mjs 的 jsdom 断言，静态检查看不出来。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[4]      # projects/panel-v2
PLUGIN = PANEL / "app" / "plugins" / "intraday"
FE = PLUGIN / "frontend"
TESTS = PLUGIN / "tests"
QUOTES = FE / "quotes.js"
QUOTES_CSS = FE / "quotes.css.js"
FRONTEND = PLUGIN / "frontend.js"
LEGACY = PANEL.parents[1] / "engine" / "web" / "index.html"
HAS_LEGACY = LEGACY.is_file()
JSDOM = PANEL.parents[1] / "node_modules" / "jsdom" / "lib" / "api.js"
HAS_JSDOM = JSDOM.is_file()


def _node(script_path: Path, timeout: int = 240) -> dict:
    proc = subprocess.run(["node", str(script_path)], capture_output=True, text=True,
                          timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])


def _expr(body: str, timeout: int = 120) -> dict:
    head = "const A = await import('file:///%s');\n" % QUOTES.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head + body],
                          capture_output=True, text=True, timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])


# ---------------------------------------------------------------- 静态约束


def test_quotes_static_rules() -> None:
    """四条约束 + 颜色门禁：不取数（数据由 frontend.js 注入）、零裸 hex、CSS 全作用域。"""
    src = QUOTES.read_text(encoding="utf-8")
    for banned in ("fetch(", "localStorage", "document.", "api.get(", "position:fixed"):
        assert banned not in src, "quotes.js 出现 %s" % banned
    assert not re.findall(r"^let\s+_?[A-Za-z_$]", src, re.M), "不许模块级可变状态"
    for name in ("quotes.js", "quotes.css.js"):
        body = (FE / name).read_text(encoding="utf-8")
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", body), "%s 出现裸 hex" % name
        assert not re.search(r"rgba?\(", body), "%s 出现自拼 rgba" % name


def test_quotes_css_scoped_and_tokens_exist() -> None:
    head = "const A = await import('file:///%s');\n" % QUOTES_CSS.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head +
                           "console.log(JSON.stringify({ css: A.QUOTES_CSS }));"],
                          capture_output=True, text=True, timeout=120, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    css = json.loads([x for x in proc.stdout.strip().splitlines() if x.strip()][-1])["css"]
    for line in css.splitlines():
        s = line.strip()
        if not s or s.startswith("/*") or s.startswith("*"):
            continue
        if s.startswith("@media"):
            assert ".it-card ." in s, "单行 @media 内层没加作用域：%s" % s
            continue
        if "{" not in s:
            continue
        for sel in s.split("{", 1)[0].split(","):
            sel = sel.strip()
            if sel:
                assert sel.startswith(".it-card "), "未加作用域的选择器：%s" % sel
    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    tokens = set(re.findall(r"(--[a-z0-9-]+)\s*:",
                            (LEGACY.parent / "themes.css").read_text(encoding="utf-8")))
    assert not sorted(t for t in used if t not in tokens), "引用了未定义 token"


def test_frontend_js_wires_quotes_section() -> None:
    """编排：inject / 区头 / 渲染各一处；spark 与行情同一拍（不许出现异步回填）。"""
    src = FRONTEND.read_text(encoding="utf-8")
    assert "import { mqHtml, sparkCodes, SPARK_TTL_MS } from './frontend/quotes.js';" in src
    assert "injectQuotesCss(document)" in src
    assert "mqHtml(S.intraday && S.intraday.market_quotes, S.spark)" in src
    assert "const loadSpark = async (quotes) => {" in src
    assert src.count("'/api/sector_spark.json?level=INDEX&codes='") == 1, "spark 只允许一处取数"
    # 先取数再渲：loadSpark 必须在 render() 之前 await 完（同一拍）
    # 2026-10-28（-35 首屏提速）：spark 与轨道文件同一拍 Promise.all，不再单独打头
    spark_at = src.index("loadSpark(S.intraday")
    block_at = src.rindex("await Promise.all([", 0, spark_at)
    block_end = src.index("]);", spark_at)
    render_at = src.index("render();", block_end)
    assert block_at < spark_at < block_end < render_at, "spark 必须先取完再 render"
    # 不许照抄旧页的异步回填（_sparkInject 那套在卡里会把已填的 path 冲掉）
    assert "_sparkInject" not in src and "setAttribute('d'" not in src, "卡片里不许出现异步回填 path"


# ---------------------------------------------------------------- 与旧页逐卡对齐


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_mq_cards_byte_identical_to_legacy() -> None:
    """10 张行情小卡逐字节对旧页 4678-4685（旧页那一份先按 _sparkInject 灌满 d 再比）。"""
    out = _node(TESTS / "quotes_align.mjs")
    assert out["meta"]["quotes"] == 10, "真实载荷 10 条 market_quotes"
    assert out["meta"]["cardsEqual"], "逐卡不一致：%s" % [
        c for c in out["cards"] if not c["equal"]]
    assert out["aliasTable"]["legacy"] == out["aliasTable"]["cur"] == {"880008.SH": "ALL"}
    for row in out["alias"]:
        assert row["legacy"] == row["cur"], "别名 %r 旧页 %r / 卡片 %r" % (
            row["c"], row["legacy"], row["cur"])
    assert out["codes"]["legacy"] == out["codes"]["cur"], "spark 请求码表（旧页 data-idx 去重）不一致"
    assert out["empty"]["legacy"] == out["empty"]["cur"], "空行情降级文案必须一字不差"


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_spark_is_prefetched_not_backfilled() -> None:
    """核心断言：旧页整块有 20 个空 d（等回填），卡片 0 个（首绘就满）。"""
    out = _node(TESTS / "quotes_align.mjs")
    assert out["rawHasEmptyPath"] == 20, "旧页 10 卡 × 2 条 path，渲出来全是空 d"
    assert out["curHasEmptyPath"] == 0, "卡片不许出现空 d（否则就是退回了旧页的回填机制）"
    assert out["noSpark"]["cards"] == 10, "spark 缺失时行情主体照常渲（背景线只是装饰层）"
    assert out["noSpark"]["emptyPaths"] == 20, "spark 缺失时才允许空 d"
    for row in out["cards"]:
        assert row["equal"], "卡片 %s 与旧页不一致" % row["code"]


def test_mq_html_shape() -> None:
    """10 卡 / 20 条 path / 每卡 4 段（k / v / 涨跌 / src）/ 名字列带 data-open-kline。"""
    out = _expr("""
const q = { code:'880008.SH', name:'全A', price:1348.11, change_pct:1.8379,
  quote_time:'2026-10-23 15:29:43', source:'eltdx:snap' };
const up = A.mqCard(q, { ALL: { amv:'M 0 0 L 1 1', ma10:'M 0 2 L 1 3' } });
const flat = A.mqCard(Object.assign({}, q, { change_pct: -0.5 }), null);
const zero = A.mqCard(Object.assign({}, q, { change_pct: 0 }), null);
const html = A.mqHtml([q], null);
console.log(JSON.stringify({
  up, flat,
  zeroCls: (zero.match(/<div class="">/) || []).length,
  upPaths: (up.match(/<path/g) || []).length,
  upFilled: (up.match(/d="M 0 0 L 1 1"/) || []).length,
  emptyCard: A.mqHtml([], null),
  codes: A.sparkCodes([{code:'880008.SH'},{code:'000001.SH'},{code:'880008.SH'},{}]),
  ttl: A.SPARK_TTL_MS,
  alias: A.AMV_CODE_ALIAS,
}));
""")
    assert out["upPaths"] == 2, "每条卡两条背景线"
    assert out["upFilled"] == 1, "spark 必须写进 d（不是空 path）"
    assert 'class="up"' in out["up"] and 'class="down"' in out["flat"]
    assert out["zeroCls"] == 1, "0 涨幅不染色（旧页 cls 同口径）"
    assert out["emptyCard"] == '<p class="muted">行情源不可达（见 meta.source）</p>'
    assert out["codes"] == ["ALL", "000001.SH"], "别名 + 去重保序"
    assert out["ttl"] == 20000, "旧页 5135 的 20s 轻缓存"
    assert out["alias"] == {"880008.SH": "ALL"}


@pytest.mark.skipif(not HAS_JSDOM, reason="缺 jsdom（OH-WorkSpace/node_modules/jsdom）")
def test_spark_paths_present_on_first_paint() -> None:
    """真 DOM：整卡挂载后 20 条 path 全是满的（首绘即满，不存在「先空后填」窗口）。"""
    out = _node(TESTS / "audit_dom.mjs")
    m = out["mount"]
    assert m["sparkCalls"] == 1, "spark 只取一次"
    assert m["quoteCards"] == 10, "10 个行情小卡"
    assert m["sparkPaths"] == 20, "20 条背景线"
    assert m["sparkFilled"] == 20, "首绘就全满（这一条就是本单要的「先取数再渲」）"
    assert m["sparkFirstAmv"].startswith("M "), "d 属性必须是真的 path"
    assert "intraday-quotes-css" in m["styles"], "行情样式已注入"
