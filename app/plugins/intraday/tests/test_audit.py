# -*- coding: utf-8 -*-
"""审计明细 / 会话行 / 成交额对比（frontend/audit.js）守门测试。

这一块照旧页 engine/web/index.html 搬的（会话行 4707 / 成交额对比 4694-4699 + 5170-5173 /
审计明细 4718-4731 / 映射表 2962-3011）。搬错不会报错，只会显示错字或显示错数，所以测四件事：

  ① 中文映射表**逐子表逐键逐值**对旧页（tests/audit_align.mjs 在 node:vm 里跑旧页真源码比对，
     不是比我自己抄的字符串）；
  ② 5 张表的**列结构与行数**（signals 49 条 × 8 列 = 392 格逐字对旧页 table() 输出）；
  ③ 会话行字段（date / phase / state / frozen_reason / 四项计数）；
  ④ details **默认折叠**（首绘无 open 属性）且在重绘后保留用户展开态（jsdom 真 DOM）。

做法与 test_research.py 一致：Node 加载真实模块 + node:vm 跑旧页源码。
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
AUDIT = FE / "audit.js"
AUDIT_CSS = FE / "audit.css.js"
FRONTEND = PLUGIN / "frontend.js"
# PANEL=projects/panel-v2 → parents[0]=projects → parents[1]=OH-WorkSpace（仓库根）
LEGACY = PANEL.parents[1] / "engine" / "web" / "index.html"
HAS_LEGACY = LEGACY.is_file()
JSDOM = PANEL.parents[1] / "node_modules" / "jsdom" / "lib" / "api.js"
HAS_JSDOM = JSDOM.is_file()
FIXTURE = TESTS / "fixtures" / "intraday_payload.json"


def _node(script_path: Path, timeout: int = 240) -> dict:
    """跑 tests/*.mjs 运行器，取最后一行 JSON。"""
    proc = subprocess.run(["node", str(script_path)], capture_output=True, text=True,
                          timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])


def _expr(body: str, timeout: int = 120) -> dict:
    """用 --input-type=module -e 跑小表达式（不落盘）。"""
    head = "const A = await import('file:///%s');\n" % AUDIT.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head + body],
                          capture_output=True, text=True, timeout=timeout, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1])


# ---------------------------------------------------------------- 静态约束（四条约束 + 门禁）


def test_audit_has_no_dom_or_network_shortcuts() -> None:
    """约束②③：不用 getElementById、不挂 body、不 fixed；取数走 frontend.js 注入的 api（本模块不取数）。"""
    src = AUDIT.read_text(encoding="utf-8")
    for banned in ("fetch(", "localStorage", "document.body", "position:fixed", "position: fixed",
                   "document.querySelector(", "api.get("):
        assert banned not in src, "audit.js 出现 %s" % banned
    # 只允许在自己的 body 上做容器内查询（约束②正例）
    assert "el.querySelector" in src, "应在传入的 body 内查询，而不是全文档查询"


def test_audit_has_no_module_level_state() -> None:
    """约束①：不许模块级可变状态（旧页本组本来就是一次性内联表达式，没有模块级 let 可搬）。"""
    src = AUDIT.read_text(encoding="utf-8")
    bad = re.findall(r"^let\s+_?[A-Za-z_$]", src, re.M)
    assert not bad, "audit.js 出现模块级 let（会跨实例串味）：%s" % bad
    assert "new AbortController()" in src, "每实例必须自带 AbortController"


def test_audit_writes_no_raw_hex_or_rgba() -> None:
    for name in ("audit.js", "audit.css.js"):
        src = (FE / name).read_text(encoding="utf-8")
        assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", src), "%s 出现裸 hex" % name
        assert not re.search(r"rgba?\(", src), "%s 出现自拼 rgba" % name


def test_audit_css_scoped_and_tokens_exist() -> None:
    """颜色只走 token（门禁 test_no_raw_hex_in_any_card 同款）+ 选择器全部加 .it-card。"""
    head = "const A = await import('file:///%s');\n" % AUDIT_CSS.as_posix()
    proc = subprocess.run(["node", "--input-type=module", "-e", head +
                           "console.log(JSON.stringify({ css: A.AUDIT_CSS }));"],
                          capture_output=True, text=True, timeout=120, cwd=str(PANEL))
    if proc.returncode != 0:
        pytest.fail("node 执行失败：\n%s\n%s" % (proc.stdout[-3000:], proc.stderr[-3000:]))
    lines = [x for x in proc.stdout.strip().splitlines() if x.strip()]
    out = json.loads(lines[-1])
    css = out["css"]
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
    missing = sorted(t for t in used if t not in tokens)
    assert not missing, "引用了 themes.css 未定义的 token：%s" % missing


def test_frontend_js_wires_audit_section() -> None:
    """编排接入：import / 建实例 / 绑 / 卸载各一处，且 intraday.json 只取一次。"""
    src = FRONTEND.read_text(encoding="utf-8")
    assert "import * as audit from './frontend/audit.js" in src
    assert "injectAuditCss(document)" in src
    assert src.count("audit.createState(") == 1
    assert src.count("audit.bind(") == 1
    assert src.count("audit.unbind(") == 1
    assert "audit.paint(S.audit" in src
    # 2026-10-25 排版对齐旧页：会话行已照旧页回到冰点卡后（旧页 4707），
    # 本块只剩审计明细，标题随之从「盘中会话 · 审计明细」改为「审计明细」。
    assert "审计明细" in src, "卡片区块标题"
    assert src.count("'/api/intraday.json'") == 1, "intraday.json 只允许一处取数"
    for token in ("sessionText(", "turnoverHtml("):
        assert token in src, "frontend.js 未接 %s" % token


def test_frontend_submodules_new_files_exist() -> None:
    """本单新增的两个子模块必须真落地（防「import 写了、文件没落」的静默 404）。"""
    for name in ("audit.js", "audit.css.js"):
        p = FE / name
        assert p.is_file(), "缺子模块 %s" % name
        assert p.stat().st_size > 200, "子模块 %s 内容为空壳" % name


# ---------------------------------------------------------------- 中文映射 / 工具函数（对旧页逐值）


def test_mapping_tables_match_legacy_verbatim() -> None:
    """M 的 9 张子表（state/phase/regime/sigStatus/direction2/channel/src2/sigKind/sigSource）
    逐键逐值对旧页 2962-3004 真源码。翻错一个字都会在这里挂。"""
    out = _node(TESTS / "audit_align.mjs")
    assert out["m"], "映射表为空"
    for row in out["m"]:
        assert row["exists"], "旧页没有这张子表：%s" % row["key"]
        assert row["ok"], "映射表与旧页不一致：%s missing=%s extra=%s diff=%s" % (
            row["key"], row["missing"], row["extra"], row["diff"])
    keys = {r["key"] for r in out["m"]}
    assert keys == {"state", "phase", "regime", "sigStatus", "direction2",
                    "channel", "src2", "sigKind", "sigSource"}


def test_cn_and_source_boundaries() -> None:
    """cn：缺值 → —，表外原样透传；cnSigSource：空 → —，SW-/BP- 业务单号不翻译。"""
    out = _node(TESTS / "audit_align.mjs")
    for row in out["cn"]:
        assert row["legacy"] == row["cur"], "cn(%s, %r) 旧页 %r / 卡片 %r" % (
            row["k"], row["v"], row["legacy"], row["cur"])
    for row in out["cnSigSource"]:
        assert row["legacy"] == row["cur"], "cnSigSource(%r) 旧页 %r / 卡片 %r" % (
            row["v"], row["legacy"], row["cur"])
    # 具体值再钉一遍（防止两边同时错成一样）
    # 空串落 String('')（旧页 3005 同口径：只有 null/undefined 才给 —），不是「—」
    assert _expr("console.log(JSON.stringify({ n: A.cn(A.M.channel, null),"
                 " e: A.cn(A.M.channel, ''), u: A.cn(A.M.channel, 'Z'),"
                 " h: A.cn(A.M.channel, 'A'), s: A.cnSigSource('SW-1'),"
                 " s2: A.cnSigSource(''), m: A.cnSigSource('manual') }));") == {
        "n": "—", "e": "", "u": "Z", "h": "通道A", "s": "SW-1", "s2": "—", "m": "手动"}


def test_table_and_kcode_match_legacy() -> None:
    """空表 → 「（空）」；表头 th、无 thead/tbody；kCode 可跳带 data-open-kline、不可跳显式 .k-nolink。"""
    out = _node(TESTS / "audit_align.mjs")
    assert out["table"]["emptyLegacy"] == out["table"]["emptyCur"] == '<p class="muted">（空）</p>'
    assert out["table"]["rowsLegacy"] == out["table"]["rowsCur"]
    for row in out["kCode"]:
        assert row["legacy"] == row["cur"], "kCode(%r) 旧页 %r / 卡片 %r" % (
            row["c"], row["legacy"], row["cur"])


# ---------------------------------------------------------------- 会话行


def test_session_line_fields() -> None:
    """会话行字段：date / phase / state / frozen_reason / 四项计数，缺省计数取 0（旧页 ?? 0）。"""
    out = _expr("""
const full = { session: { date:'2026-10-23', phase:'CLOSED', state:'FROZEN', frozen_reason:'急停' },
  day_summary: { signal_count:49, triggered_count:49, executed_count:22, blocked_count:3 } };
const bare = { session: { date:'', phase:'OPENING', state:'READY' }, day_summary: {} };
const none = {};
console.log(JSON.stringify({
  full: A.sessionText(full),
  frozen: A.sessionLine(full),
  bare: A.sessionText(bare),
  none: A.sessionText(none),
  hasRule: A.sessionLine(full).indexOf('（冻结:急停）') > 0,
}));
""")
    assert out["full"] == "盘中会话 2026-10-23 · 已收盘 · 急停冻结（冻结:急停） ｜ 信号 49 · 触发 49 · 执行 22 · 拦截 3"
    assert out["bare"] == "盘中会话  · 盘中 · 就绪 ｜ 信号 0 · 触发 0 · 执行 0 · 拦截 0"
    # 空数据：phase/state 是空串（旧页 cn(M.phase, s.phase||"") 同样落 String("") 而非「—」），
    # 四项计数一律 0 —— 与旧页逐字一致，不在这里「修」成更好看的样子
    assert out["none"] == "盘中会话  ·  ·  ｜ 信号 0 · 触发 0 · 执行 0 · 拦截 0", "空数据不得造数"
    assert 'class="sess-line"' in out["frozen"] and out["hasRule"]


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_session_line_byte_identical_to_legacy() -> None:
    """会话行整行文本与旧页 4707 逐字节相同（含真实载荷 + 冻结原因分支）。"""
    out = _node(TESTS / "audit_align.mjs")
    s = out["session"]
    assert s["legacyText"] == s["curText"], "旧页 %r / 卡片 %r" % (s["legacyText"], s["curText"])
    assert s["legacyHtml"] == s["curHtml"], "HTML 形态也必须一字不差"
    assert out["sessionFrozen"]["legacy"] == out["sessionFrozen"]["cur"], "冻结原因分支"
    assert "（冻结:急停）" in out["sessionFrozen"]["cur"]


# ---------------------------------------------------------------- 成交额对比


def test_turnover_compare_three_rows() -> None:
    """成交额对比：盘中 3 行（当前累计 / 昨日同时刻 / 预估全天）+ 汇总行。

    2026-10-28（N20261027-122604-34）：括号里的百分比换成「真正参与计算的分母」——
    profile 口径下预估 = 累计 ÷ **量能进度** w(t)，旧页写的是**时间进度**，两个分母不一样时
    三行数字对不上（10/27 12:26：8774.6 亿 / 预估 1.52 万亿 / 括号 50%，按 50% 反推应是 1.75 万亿）。
    """
    out = _expr("""
const base = { prev_time_source:'qmt-1m', prev_same_time_amount: 1528708179180,
  prev_same_time_diff_pct: 27.968, forecast_full_day_amount: 1956257843600,
  progress: 0.5, estimated: true, source: 'qmt:tick',
  estimate_note: '预估=今日累计÷w(t)', asof: '2026-10-23 11:30:00' };
// 盘中 profile：累计 1.13 万亿 → 预估 1.96 万亿 ⇒ 量能进度 ≈ 58%（不是 50%）
const prof = Object.assign({}, base, { total_amount: 1128708179180, estimate_method: 'profile' });
// 盘中 linear：预估 = 累计 ÷ 时间进度，括号就该写时间
const lin = Object.assign({}, base, { total_amount: 978127921800, estimate_method: 'linear' });
// 收盘：progress=1（旧形态 = 「预估今日全天（估算）100%」把已发生的事实当预测）
const closed = Object.assign({}, base, { progress: 1, total_amount: 1956257843600, estimate_method: 'profile' });
const br = { today: { amount: 1972854849536 } };
const est = Object.assign({}, prof, { prev_time_source: 'estimate' });
const noBr = null;
console.log(JSON.stringify({
  profile: A.turnoverCompareHtml(prof, br),
  linear: A.turnoverCompareHtml(lin, br),
  closed: A.turnoverCompareHtml(closed, br),
  estimate: A.turnoverCompareHtml(est, br),
  noBreadth: A.br880AmountTxt(noBr),
  foot: A.turnoverFootHtml(prof),
  full: A.turnoverHtml(prof, br),
}));
""")
    prof = out["profile"]
    assert prof.count('class="to-kv"') == 3, "盘中应三行 KV"
    assert "1.97 万亿" in prof, "当前累计读广度 880 的 today.amount"
    assert "1.53 万亿" in prof and "+27.97%" in prof
    assert '<span class="src">(估算)</span>' in prof and "1.96 万亿" in prof
    # 分母必须写量能进度（1.13/1.96 ≈ 58%），不再写时间进度 50%
    assert "量能 58%" in prof, prof
    assert "时间 50%" not in prof
    assert "成交额对比" in prof

    lin = out["linear"]
    assert lin.count('class="to-kv"') == 3
    assert "时间 50%" in lin, lin
    assert "量能" not in lin

    # 收盘：不再出「预估今日全天（估算）」——直接给今日全天，且不出假的 100%
    cl = out["closed"]
    assert cl.count('class="to-kv"') == 2, "收盘只留两行（今日全天 / 昨日全天）：%s" % cl
    assert "今日全天" in cl and "昨日全天" in cl
    assert "预估今日全天" not in cl and "(估算)" not in cl and "100%" not in cl
    assert "1.97 万亿" in cl, "收盘时今日全天仍读 880005（与当前累计同源，不自相矛盾）"

    # 非 1m 真值 → 必须显「无数据」，估算值不充真值（旧页 4691 原话）
    assert "无数据" in out["estimate"] and "1.53 万亿" not in out["estimate"]
    assert out["noBreadth"] == '<span class="muted">无数据</span>'
    assert out["foot"] == '<div class="src">2026-10-23 11:30:00</div>'
    assert out["full"].endswith(out["foot"]), "整块 = 小卡 + 汇总行"


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_turnover_compare_byte_identical_to_legacy() -> None:
    """盘中态：前两行 KV + 2 条 src 与旧页逐字节一致；第三行只比标签与金额。

    两处规格偏离（有意改的，不是回归）：
      ① 收盘态卡片 3 行收成 2 行（今日全天 / 昨日全天），旧页恒 3 行；
      ② 第三行括号里的分母旧页写时间进度（50%），卡片写真正参与计算的量能进度
         （profile 口径下由 total/forecast 反解 w）——两个分母不一致时三行数字对不上（audit.js 注释）。
    所以逐字节只覆盖前两行；第三行比标签与数值，不比分母文案。
    收盘形态由 test_turnover_compare_three_rows 守。
    """
    out = _node(TESTS / "audit_align.mjs")
    t = out["turnoverOpen"]
    assert t["legacyKV"][:2] == t["curKV"][:2], "旧页 %s\n卡片 %s" % (t["legacyKV"][:2], t["curKV"][:2])
    assert len(t["curSrc"]) == len(t["legacySrc"]) == 2
    assert len(t["curKV"]) == 3
    # 2026-11-07（标注 N85）：卡片 foot 只留时间戳，口径说明已去掉 → 与旧页**有意偏离**，
    # 只比时间戳，不再逐字节比整行。
    for legacy, cur in zip(t["legacySrc"], t["curSrc"]):
        stamp = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", legacy)
        if stamp:
            assert stamp.group(0) in cur, "旧页 %s\n卡片 %s" % (legacy, cur)
            assert "预估" not in cur, "口径说明应已去掉：%s" % cur
        else:
            assert legacy == cur, "旧页 %s\n卡片 %s" % (legacy, cur)
    assert "预估今日全天" in t["curKV"][2] and "1.96 万亿" in t["curKV"][2], t["curKV"][2]
    f = out["turnoverFake"]
    assert f["legacyKV"][:2] == f["curKV"][:2], "非 1m 真值的「无数据」分支必须与旧页一致"


# ---------------------------------------------------------------- 审计明细 5 张表


def test_audit_column_structure() -> None:
    """5 张表的表头与列数（旧页 4719-4730 原样）：8 / 7 / 5 / 5 / 5。"""
    out = _expr("""
const heads = { sig: A.SIGNAL_HEADS, snap: A.SNAP_HEADS, env: A.ENV_HEADS,
  ev: A.EVENT_HEADS, cmd: A.CMD_HEADS };
const html = A.auditHtml({ signals: [
  { signal_id:'S1', channel:'A', created_at:'2026-10-23 09:30:06', ts_code:'002733.SZ',
    name:'雄韬股份', kind:'BUY', source:'manual', trigger:{ condition:'条件X' }, status:'TRIGGERED' } ],
  strength_snapshots: [{ snapshot_id:'N1', ts:'t', ts_code:'600519.SH', name:'贵州茅台',
    direction:'STRONG', total_score:88.5, verdict:'持有', made_by:'ENGINE' }],
  env_adjustments: [{ ts:'t', source_slot:'A', regime:'RANGE', env_coefficient:0.8, basis:'b' }],
  events: [{ event_id:'E1', ts:'t', source:'PATROL', title:'标题' }],
  commands_b: [{ command_id:'C1', raw_text:'原始', translated:{ condition:'c' }, status:'PENDING', created_at:'t' }] });
console.log(JSON.stringify({
  heads,
  ths: (html.match(/<th>([^<]*)<\\/th>/g) || []).map(s => s.replace(/<\\/?th>/g, '')),
  tables: (html.match(/<table>/g) || []).length,
  rowTds: (html.match(/<tr>(?!<th>)/g) || []).length,
  eventScore: (html.match(/<td>—<\\/td><\\/tr>/) || []).length,
}));
""")
    assert out["heads"]["sig"] == ["ID", "通道", "时间", "标的", "类型", "来源", "触发", "状态"]
    assert out["heads"]["snap"] == ["ID", "时间", "标的", "方向", "总分", "结论", "判定者"]
    assert out["heads"]["env"] == ["时间", "来源档", "环境", "系数", "依据"]
    assert out["heads"]["ev"] == ["ID", "时间", "来源", "标题", "评分"]
    assert out["heads"]["cmd"] == ["ID", "原始指令", "翻译条件", "状态", "时间"]
    assert out["ths"] == (out["heads"]["sig"] + out["heads"]["snap"] + out["heads"]["env"]
                          + out["heads"]["ev"] + out["heads"]["cmd"]), "表头逐字且顺序一致"
    assert out["tables"] == 5, "5 张表"
    assert out["eventScore"] == 1, "评分缺值 → —（旧页 x.score ?? \"—\"）"


def test_audit_details_default_collapsed() -> None:
    """details 默认折叠：产出里无 open 属性；summary 文案与旧页 4718 一字不差。"""
    out = _expr("""
const html = A.auditHtml({ signals: [], strength_snapshots: [], env_adjustments: [],
  events: [], commands_b: [] });
console.log(JSON.stringify({
  starts: html.slice(0, 60),
  hasOpen: /<details[^>]*\\sopen/.test(html),
  hasFold: html.indexOf('<details class="fold">') === 0,
  summary: A.SUMMARY_TEXT,
  emptyCount: (html.match(/（空）/g) || []).length,
  cards: (html.match(/class="it-audit-card"/g) || []).length,
}));
""")
    assert out["hasFold"], "外层必须是 <details class=\"fold\">（旧页 4718）"
    assert not out["hasOpen"], "首绘必须折叠（不许带 open 属性）"
    assert out["summary"] == "审计明细（信号流水 / 判强判弱 / 环境系数 / 盘中事件 / 通道B）"
    assert out["emptyCount"] == 5 and out["cards"] == 5, "无数据时 5 张表都输出「（空）」，不留空白"


@pytest.mark.skipif(not HAS_LEGACY, reason="缺旧页 engine/web/index.html（只读参考）")
def test_audit_five_tables_byte_identical_to_legacy() -> None:
    """5 张表与旧页 4719-4730 逐字节相同；信号流水 49 行 × 8 列 = 392 格逐字相同。"""
    out = _node(TESTS / "audit_align.mjs")
    a = out["audit"]
    assert a["curCards"] and len(a["curCards"]) == 5, "必须有 5 张卡"
    assert a["cardsEqual"], "与旧页不一致（首张差异见下）\n旧页 %s\n卡片 %s" % (
        a["legacyCards"][0][:600], a["curCards"][0][:600])
    assert a["legacyTr"] == a["curTr"], "<tr> 数：旧页 %s / 卡片 %s" % (a["legacyTr"], a["curTr"])
    assert a["legacyEmpty"] == a["curEmpty"] == 4, "真实载荷只有 signals 有数据，其余 4 段为空"
    assert a["realSignals"] == 49 and a["realOther"] == 0, "载荷实态：signals 49 / 其余 0"
    c = out["cells"]
    assert c["n"] == 392, "49 行 × 8 列 = 392 格，实测 %s" % c["n"]
    assert c["equal"], "单元格不一致，首个不同格 #%s：旧页 %r / 卡片 %r" % (
        c["firstDiff"],
        c["legacy"][c["firstDiff"]] if c["firstDiff"] >= 0 else "",
        c["cur"][c["firstDiff"]] if c["firstDiff"] >= 0 else "")


# ---------------------------------------------------------------- 真 DOM（jsdom）


@pytest.mark.skipif(not HAS_JSDOM, reason="缺 jsdom（OH-WorkSpace/node_modules/jsdom）")
def test_details_collapse_and_repaint_in_real_dom() -> None:
    """details 首绘折叠 → 点开 → 20s 重绘后仍展开；整卡挂载后块序与旧页相对位置一致。"""
    out = _node(TESTS / "audit_dom.mjs")
    fp = out["firstPaint"]
    assert fp["hasDetails"] is True, "首绘就要有 details（无数据也不留空白块）"
    assert fp["open"] is False and fp["hasOpenAttr"] is False, "首绘必须默认折叠"
    assert fp["summary"].startswith("审计明细（信号流水")
    assert fp["emptyTables"] == 5

    wd = out["withData"]
    assert wd["open"] is False, "有数据后仍默认折叠（不在卡里全量占屏）"
    assert wd["rows"] == 50, "1 表头 + 49 行信号"
    assert wd["tds"] == 392, "49 行 × 8 列"
    assert wd["kline"] == 98, "49 代码 + 49 名称，全部带 data-open-kline"
    assert wd["emptyBlocks"] == 4 and wd["tables"] == 5

    # 内容没变的一拍（20s 定时刷新最常见情况）：整块不碰 DOM → 展开态与节点都原样留着
    same = out["sameContentRepaint"]
    assert same["open"] is True and same["sameNode"] is True, "同内容重绘必须跳过 DOM 写入"
    # 内容真变了：必须重建，但用户展开态要还原
    rep = out["afterExpandRepaint"]
    assert rep["open"] is True, "用户展开后真重建也不得弹回折叠"
    assert rep["replaced"] is True, "内容变了必须换节点（不能拿旧内容糊弄）"
    assert rep["rows"] == 4, "3 条信号 + 1 行表头"
    assert out["afterSecondRepaint"]["open"] is True, "跨多次重建展开态都不丢"
    assert out["selfHealing"]["open"] is True, "paint 自己读 DOM 的 open 再还原（不靠外部同步）"
    assert out["unbind"]["alive"] is False

    m = out["mount"]
    assert m["fetchedIntraday"] == 1, "intraday.json 只取一次"
    assert m["sessText"] == ("盘中会话 2026-10-23 · 已收盘 · MONITORING"
                             " ｜ 信号 49 · 触发 49 · 执行 22 · 拦截 0")
    assert m["turnoverKV"] == 2 and m["turnoverCurAmt"] == "1.97 万亿"
    assert m["auditTds"] == 392
    assert m["detailsOpen"] is False
    assert m["styles"] == ["intraday-card-css", "intraday-legacy-css", "intraday-research-css",
                          "intraday-audit-css", "intraday-quotes-css"], "五份 CSS 都注入"
    o = m["order"]
    assert o["panicBeforeSess"], "会话行紧跟冰点卡（旧页 4706 → 4707）"
    assert o["quotesKpi"] == 10 and o["quotesPaths"] == 20, "实时行情 10 卡 / 20 条背景线（详见 test_quotes.py）"
    assert (o["sessIdx"] < o["quotesIdx"] < o["turnoverIdx"] < o["sectorIdx"]
            < o["researchIdx"] < o["auditIdx"] < o["srcIdx"]), (
        "块序必须照旧页 4704-4731：会话行 → 实时行情 → 成交额 → 轮动 → 研究 → 审计明细（末尾）"
        " → 来源行；实测 %s" % o)

    r = out["refresh"]
    assert r["details"] is True and r["tds"] == 392, "refresh 后审计明细仍在"
    assert r["sessText"] == m["sessText"], "会话行文本不变"
    u = out["unmount"]
    assert u["after"] == 0, "unmount+壳移除（shell.js L412 host.remove()）后卡片节点清空"
    assert not u["stillResponsive"], "unmount 后 refresh 不再写 DOM（事件与状态已清）"
    assert not u["refreshThrew"], "unmount 后 refresh 安全返回不抛异常（S 为 null 应早退）"
